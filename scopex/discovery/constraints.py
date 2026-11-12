"""Interpretable constraint learning for operational boundaries.

Extracts symbolic rules of the form "conditions => SLO violation" using:
1. Decision Trees: Conjunctive axis-aligned bounding rules.
2. Sparse Regression: L1-penalized linear inequalities selecting minimal feature subsets.

Rules are converted into:
- Structured :class:`Constraint` objects with metadata (variables, inequality,
  confidence, support, source experiments).
- Z3-readable expressions and SMT-LIB2 format for automated verification.
- Evaluated and ranked on held-out test data by precision, recall, and F1.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import z3
from sklearn.linear_model import Lasso
from sklearn.tree import DecisionTreeClassifier, _tree

logger = logging.getLogger(__name__)

FEATURE_COLUMNS: list[str] = [
    "target_rps",
    "checkout_replicas",
    "db_pool_size",
    "cache_enabled",
    "consumers",
]


@dataclass
class Constraint:
    """Structured representation of an operational constraint.

    Represents a rule of the form:
        conditions => SLO violation

    Attributes:
        variables: List of feature names appearing in the condition.
        inequality: Human-readable inequality string representing the condition.
        confidence: Purity / precision on the training dataset (in [0, 1]).
        support: Fraction of training samples satisfying the condition.
        support_count: Number of training samples satisfying the condition.
        source_experiments: List of experiment or run IDs from training data.
        z3_expr: Z3-readable Python expression string (e.g. ``"And(target_rps > 487.5, cache_enabled <= 0.0)"``).
        method: Method used to learn the rule (``"decision_tree"`` or ``"sparse_regression"``).
        evaluator: Optional callable ``df -> np.ndarray`` for fast vectorized row matching.
        heldout_precision: Precision on the held-out test set (populated after ranking).
        heldout_recall: Recall on the held-out test set.
        heldout_f1: F1 score on the held-out test set.
        heldout_support: Number of held-out test samples satisfying the condition.
    """

    variables: list[str]
    inequality: str
    confidence: float
    support: float
    support_count: int
    source_experiments: list[str]
    z3_expr: str
    method: str
    evaluator: Callable[[pd.DataFrame], np.ndarray] | None = field(
        default=None, repr=False, compare=False
    )
    heldout_precision: float | None = None
    heldout_recall: float | None = None
    heldout_f1: float | None = None
    heldout_support: int | None = None

    def matches(self, df: pd.DataFrame) -> np.ndarray:
        """Return a boolean mask of rows in *df* satisfying this constraint.

        Args:
            df: Input DataFrame containing the required feature columns.

        Returns:
            Boolean numpy array of shape ``(len(df),)``.
        """
        if self.evaluator is not None:
            return np.asarray(self.evaluator(df), dtype=bool)

        # Fallback evaluator using Z3 environment or simple pandas eval
        expr_str = self.inequality.replace("&&", "and").replace("||", "or")
        try:
            return df.eval(expr_str).to_numpy(dtype=bool)
        except Exception as exc:
            logger.warning("df.eval failed for '%s': %s", expr_str, exc)
            return np.zeros(len(df), dtype=bool)

    def to_z3(self) -> z3.BoolRef:
        """Parse this constraint into a live Z3 AST expression.

        Returns:
            :class:`z3.BoolRef` representing the condition formula.
        """
        var_objs = {v: z3.Real(v) for v in self.variables}
        scope = {
            "And": z3.And,
            "Or": z3.Or,
            "Not": z3.Not,
            "z3": z3,
            **var_objs,
        }
        return eval(self.z3_expr, {"__builtins__": {}}, scope)

    def to_smt2(self) -> str:
        """Return the SMT-LIB2 format s-expression for this constraint."""
        return self.to_z3().sexpr()

    def human_readable(self) -> str:
        """Return a clean human-readable representation."""
        return f"IF {self.inequality} THEN SLO_VIOLATION"

    def to_dict(self) -> dict[str, Any]:
        """Convert constraint to serializable dictionary."""
        return {
            "variables": self.variables,
            "inequality": self.inequality,
            "human_readable": self.human_readable(),
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "support": round(self.support, 4),
            "support_count": self.support_count,
            "source_experiments_count": len(self.source_experiments),
            "source_experiments": self.source_experiments[:10],  # truncate list for brevity
            "z3_expr": self.z3_expr,
            "smt2": self.to_smt2(),
            "heldout_precision": round(self.heldout_precision, 4)
            if self.heldout_precision is not None
            else None,
            "heldout_recall": round(self.heldout_recall, 4)
            if self.heldout_recall is not None
            else None,
            "heldout_f1": round(self.heldout_f1, 4)
            if self.heldout_f1 is not None
            else None,
            "heldout_support": self.heldout_support,
        }


# ---------------------------------------------------------------------------
# Decision Tree Rule Extraction
# ---------------------------------------------------------------------------


def learn_decision_tree_constraints(
    df_train: pd.DataFrame,
    features: list[str] | None = None,
    target_col: str = "p95_latency_ms",
    threshold: float = 500.0,
    max_depths: list[int] | None = None,
    min_samples_leaf: int = 5,
    min_confidence: float = 0.65,
) -> list[Constraint]:
    """Extract conjunctive constraint rules from Decision Trees.

    Args:
        df_train: Training DataFrame.
        features: Feature columns to inspect (default: standard 5 features).
        target_col: Performance metric column.
        threshold: Performance threshold defining an SLO violation (> threshold).
        max_depths: Decision tree depths to search (default: [2, 3, 4]).
        min_samples_leaf: Minimum samples in a leaf to form a rule.
        min_confidence: Minimum training precision (purity) for violation leaf.

    Returns:
        List of :class:`Constraint` objects extracted from decision tree leaves.
    """
    if features is None:
        features = [f for f in FEATURE_COLUMNS if f in df_train.columns]
    if max_depths is None:
        max_depths = [2, 3, 4]

    X = df_train[features].values
    y = (df_train[target_col] > threshold).astype(int).values
    n_total = len(df_train)

    run_ids = (
        df_train["run_id"].tolist()
        if "run_id" in df_train.columns
        else [f"run_{i}" for i in range(n_total)]
    )

    constraints: list[Constraint] = []
    seen_inequalities: set[str] = set()

    for depth in max_depths:
        dt = DecisionTreeClassifier(
            max_depth=depth, min_samples_leaf=min_samples_leaf, random_state=42
        )
        dt.fit(X, y)
        tree = dt.tree_

        def _traverse(t_obj: Any, node: int, path: list[tuple[str, str, float]]) -> None:
            if t_obj.children_left[node] == _tree.TREE_LEAF:
                # Leaf node: check class distribution
                val = t_obj.value[node][0]
                prob_violation = float(val[1] / val.sum())
                n_samples = int(t_obj.n_node_samples[node])

                if prob_violation >= min_confidence and n_samples >= min_samples_leaf:
                    # Construct condition strings
                    vars_in_path = list(dict.fromkeys(p[0] for p in path))
                    cond_strs = [f"{f} {op} {val:.2f}" for f, op, val in path]
                    inequality_str = " and ".join(cond_strs)

                    if inequality_str in seen_inequalities:
                        return
                    seen_inequalities.add(inequality_str)

                    # Z3 expression string
                    if len(cond_strs) == 1:
                        z3_str = cond_strs[0]
                    else:
                        z3_str = f"And({', '.join(cond_strs)})"

                    # Vectorized evaluator
                    current_path = list(path)

                    def make_eval(p_list):
                        return lambda d: np.logical_and.reduce(
                            [
                                (d[f] <= v if op == "<=" else d[f] > v)
                                for f, op, v in p_list
                            ]
                        )

                    eval_fn = make_eval(current_path)

                    # Find training source experiments matching leaf
                    train_mask = eval_fn(df_train)
                    source_exps = [
                        run_ids[i] for i, m in enumerate(train_mask) if m
                    ]

                    constraints.append(
                        Constraint(
                            variables=vars_in_path,
                            inequality=inequality_str,
                            confidence=prob_violation,
                            support=float(n_samples / n_total),
                            support_count=n_samples,
                            source_experiments=source_exps,
                            z3_expr=z3_str,
                            method="decision_tree",
                            evaluator=eval_fn,
                        )
                    )
                return

            f_name = features[t_obj.feature[node]]
            th = float(t_obj.threshold[node])
            _traverse(
                t_obj, t_obj.children_left[node], path + [(f_name, "<=", round(th, 2))]
            )
            _traverse(
                t_obj, t_obj.children_right[node], path + [(f_name, ">", round(th, 2))]
            )

        _traverse(tree, 0, [])

    logger.info("Extracted %d decision tree constraints", len(constraints))
    return constraints


# ---------------------------------------------------------------------------
# Sparse Regression Rule Extraction
# ---------------------------------------------------------------------------


def learn_sparse_regression_constraints(
    df_train: pd.DataFrame,
    features: list[str] | None = None,
    target_col: str = "p95_latency_ms",
    threshold: float = 500.0,
    alphas: list[float] | None = None,
    min_confidence: float = 0.55,
) -> list[Constraint]:
    """Extract linear inequality constraints using L1-penalized sparse regression (Lasso).

    Fits Lasso models across multiple regularization strengths :math:`\\alpha`. Features
    with non-zero coefficients define an active hyperplane:
        .. math::
            \\sum_{i} w_i x_i + b \\ge 0.5 \\implies \\text{SLO violation}

    Args:
        df_train: Training DataFrame.
        features: Feature columns to inspect.
        target_col: Performance metric column.
        threshold: Performance threshold defining an SLO violation (> threshold).
        alphas: Regularization strengths to explore sparsity levels.
        min_confidence: Minimum training precision to accept the rule.

    Returns:
        List of :class:`Constraint` objects extracted from sparse regression hyperplanes.
    """
    if features is None:
        features = [f for f in FEATURE_COLUMNS if f in df_train.columns]
    if alphas is None:
        alphas = [0.01, 0.03, 0.06, 0.10, 0.15]

    X = df_train[features].values
    y = (df_train[target_col] > threshold).astype(float).values
    n_total = len(df_train)

    run_ids = (
        df_train["run_id"].tolist()
        if "run_id" in df_train.columns
        else [f"run_{i}" for i in range(n_total)]
    )

    constraints: list[Constraint] = []
    seen_inequalities: set[str] = set()

    for alpha in alphas:
        lasso = Lasso(alpha=alpha, random_state=42)
        lasso.fit(X, y)

        coef = lasso.coef_
        intercept = float(lasso.intercept_)

        # Identify active non-zero features
        active_indices = [i for i, c in enumerate(coef) if abs(c) > 1e-5]
        if not active_indices:
            continue

        active_vars = [features[i] for i in active_indices]
        active_coefs = [float(coef[i]) for i in active_indices]

        # Decision threshold is 0.5 (probability of violation >= 50%)
        # sum(w_i * x_i) + b >= 0.5  ==>  sum(w_i * x_i) >= 0.5 - b
        rhs = 0.5 - intercept

        terms = []
        z3_terms = []
        for v, c in zip(active_vars, active_coefs, strict=True):
            terms.append(f"{c:+.5f} * {v}")
            z3_terms.append(f"({c:+.5f} * {v})")

        lhs_str = " + ".join(terms).lstrip("+ ").replace("+ -", "- ")
        inequality_str = f"{lhs_str} >= {rhs:.4f}"

        if inequality_str in seen_inequalities:
            continue
        seen_inequalities.add(inequality_str)

        z3_lhs = " + ".join(z3_terms).lstrip("+ ")
        z3_str = f"{z3_lhs} >= {rhs:.4f}"

        # Vectorized evaluator
        def make_eval(vars_list, coefs_list, rhs_val):
            return lambda d: (
                np.dot(d[vars_list].to_numpy(dtype=float), coefs_list)
                >= rhs_val - 1e-9
            )

        eval_fn = make_eval(active_vars, active_coefs, rhs)
        train_mask = eval_fn(df_train)
        support_count = int(np.sum(train_mask))
        if support_count == 0:
            continue

        confidence = float(np.mean(y[train_mask] == 1.0))
        if confidence < min_confidence:
            continue

        source_exps = [run_ids[i] for i, m in enumerate(train_mask) if m]

        constraints.append(
            Constraint(
                variables=active_vars,
                inequality=inequality_str,
                confidence=confidence,
                support=float(support_count / n_total),
                support_count=support_count,
                source_experiments=source_exps,
                z3_expr=z3_str,
                method="sparse_regression",
                evaluator=eval_fn,
            )
        )

    logger.info("Extracted %d sparse regression constraints", len(constraints))
    return constraints


# ---------------------------------------------------------------------------
# Combined Learning & Held-Out Ranking
# ---------------------------------------------------------------------------


def learn_constraints(
    df_train: pd.DataFrame,
    features: list[str] | None = None,
    target_col: str = "p95_latency_ms",
    threshold: float = 500.0,
) -> list[Constraint]:
    """Learn constraints using both Decision Trees and Sparse Regression.

    Args:
        df_train: Training DataFrame.
        features: Feature columns to inspect.
        target_col: Performance metric column.
        threshold: Performance threshold defining an SLO violation.

    Returns:
        List of all candidate :class:`Constraint` objects extracted from train data.
    """
    dt_rules = learn_decision_tree_constraints(
        df_train=df_train,
        features=features,
        target_col=target_col,
        threshold=threshold,
    )
    sr_rules = learn_sparse_regression_constraints(
        df_train=df_train,
        features=features,
        target_col=target_col,
        threshold=threshold,
    )
    return dt_rules + sr_rules


def evaluate_and_rank_constraints(
    constraints: list[Constraint],
    df_heldout: pd.DataFrame,
    target_col: str = "p95_latency_ms",
    threshold: float = 500.0,
) -> list[Constraint]:
    """Evaluate candidate constraints on held-out test data and rank by precision and recall.

    For each rule:
    - Matches rule against held-out configurations.
    - Computes True Positives, False Positives, False Negatives.
    - Calculates Precision, Recall, and F1 on the held-out set.
    - Ranks rules: primary by held-out precision (descending), secondary by recall (descending).

    Args:
        constraints: List of candidate :class:`Constraint` objects.
        df_heldout: Isolated test DataFrame (never seen during training).
        target_col: Performance metric column.
        threshold: Performance threshold defining an SLO violation.

    Returns:
        New sorted list of :class:`Constraint` objects with held-out metrics populated.
    """
    y_true = (df_heldout[target_col] > threshold).astype(int).to_numpy()
    n_heldout = len(df_heldout)

    evaluated: list[Constraint] = []
    for c in constraints:
        mask = c.matches(df_heldout)
        heldout_support = int(np.sum(mask))

        tp = int(np.sum(mask & (y_true == 1)))
        fp = int(np.sum(mask & (y_true == 0)))
        fn = int(np.sum((~mask) & (y_true == 1)))

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        c_eval = Constraint(
            variables=c.variables,
            inequality=c.inequality,
            confidence=c.confidence,
            support=c.support,
            support_count=c.support_count,
            source_experiments=c.source_experiments,
            z3_expr=c.z3_expr,
            method=c.method,
            evaluator=c.evaluator,
            heldout_precision=prec,
            heldout_recall=rec,
            heldout_f1=f1,
            heldout_support=heldout_support,
        )
        evaluated.append(c_eval)

    # Rank by precision (descending), then recall (descending), then F1 (descending)
    ranked = sorted(
        evaluated,
        key=lambda x: (
            x.heldout_precision or 0.0,
            x.heldout_recall or 0.0,
            x.heldout_f1 or 0.0,
        ),
        reverse=True,
    )
    logger.info(
        "Ranked %d constraints on held-out set (n=%d)", len(ranked), n_heldout
    )
    return ranked


# ---------------------------------------------------------------------------
# Formatting & Presentation
# ---------------------------------------------------------------------------


def format_constraints_report(
    constraints: list[Constraint],
    top_n: int = 10,
    title: str = "TOP OPERATIONAL CONSTRAINTS (SLO VIOLATIONS)",
) -> str:
    """Format ranked constraints into a human-readable summary report.

    Args:
        constraints: List of ranked :class:`Constraint` objects.
        top_n: Maximum number of rules to display.
        title: Report header title.

    Returns:
        Formatted multi-line report string.
    """
    lines: list[str] = []
    lines.append("=" * 86)
    lines.append(f"{title}")
    lines.append("Rules of the form: IF <condition> THEN SLO_VIOLATION")
    lines.append("Ranked by held-out precision and recall on unseen test configurations")
    lines.append("-" * 86)

    fmt_header = f"{'Rank':<5} {'Method':<12} {'Held-Out P':<11} {'Held-Out R':<11} {'F1':<7} {'Train Conf':<11} {'Support':<8}"
    lines.append(fmt_header)
    lines.append("-" * 86)

    for idx, c in enumerate(constraints[:top_n], start=1):
        prec_str = (
            f"{c.heldout_precision * 100:.1f}%"
            if c.heldout_precision is not None
            else "N/A"
        )
        rec_str = (
            f"{c.heldout_recall * 100:.1f}%"
            if c.heldout_recall is not None
            else "N/A"
        )
        f1_str = f"{c.heldout_f1:.2f}" if c.heldout_f1 is not None else "N/A"
        conf_str = f"{c.confidence * 100:.1f}%"
        supp_str = (
            f"{c.heldout_support}" if c.heldout_support is not None else "N/A"
        )
        method_short = (
            "DecTree" if c.method == "decision_tree" else "SparseReg"
        )

        lines.append(
            f"#{idx:<4} {method_short:<12} {prec_str:<11} {rec_str:<11} {f1_str:<7} {conf_str:<11} {supp_str:<8}"
        )
        lines.append(f"      Rule:    {c.human_readable()}")
        lines.append(f"      Z3 Expr: {c.z3_expr}")
        lines.append(f"      SMT2:    {c.to_smt2()}")
        lines.append(
            f"      Vars:    {c.variables}  |  Source runs: {len(c.source_experiments)}"
        )
        lines.append("")

    lines.append("=" * 86)
    return "\n".join(lines)
