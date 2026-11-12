"""Prediction and constraint-classification metrics."""
from __future__ import annotations

from typing import Dict

import numpy as np


def rmse(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mae(y_true, y_pred) -> float:
    return float(np.mean(np.abs(np.asarray(y_true, float) - np.asarray(y_pred, float))))


def regression_report(y_true, y_pred) -> Dict[str, float]:
    yt, yp = np.asarray(y_true, float), np.asarray(y_pred, float)
    lt, lp = np.log(np.maximum(yt, 1)), np.log(np.maximum(yp, 1))
    return {"rmse_ms": rmse(yt, yp), "mae_ms": mae(yt, yp),
            "rmse_log": rmse(lt, lp), "mae_log": mae(lt, lp),
            "median_abs_pct_err": float(np.median(np.abs(yp - yt) / np.maximum(yt, 1)) * 100)}


def classification_report(violates_true, violates_pred) -> Dict[str, float]:
    """'Positive' = SLO violation (the constraint we want to discover)."""
    t = np.asarray(violates_true, bool)
    p = np.asarray(violates_pred, bool)
    tp = int((t & p).sum()); fp = int((~t & p).sum())
    fn = int((t & ~p).sum()); tn = int((~t & ~p).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"accuracy": (tp + tn) / max(len(t), 1), "precision": prec, "recall": rec,
            "f1": f1, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
