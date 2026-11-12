"""Validated constraint repository.

Stores each discovered constraint with the conditions it was discovered
under (domain / infrastructure scale), the experiments that support it,
its formal-check results and its measured accuracy, so a later scale
change can decide which constraints to re-check (Milestone 5).

Layout:
  constraint_repository/constraints.json          list of constraint entries
  constraint_repository/evidence/<id>.csv         experiment rows backing it
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Dict, List, Optional

import pandas as pd

from or_engine.rules import Rule

HERE = os.path.dirname(os.path.abspath(__file__))


class ConstraintRepository:
    def __init__(self, root: str = HERE):
        self.root = root
        self.path = os.path.join(root, "constraints.json")
        self.evidence_dir = os.path.join(root, "evidence")
        os.makedirs(self.evidence_dir, exist_ok=True)

    def load(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return json.load(f)

    def save_all(self, entries: List[dict]) -> None:
        with open(self.path, "w") as f:
            json.dump(entries, f, indent=2, default=float)

    def add(self, cid: str, rule: Rule, *, slo: Dict, domain: Dict, scale: Dict,
            evidence_rows: List[dict], status: str, verification: List[dict],
            metrics: Optional[Dict] = None, refinement: Optional[Dict] = None,
            notes: str = "") -> dict:
        ev_file = os.path.join(self.evidence_dir, f"{cid}.csv")
        pd.DataFrame(evidence_rows).to_csv(ev_file, index=False)
        entry = {
            "id": cid,
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "status": status,                 # candidate | validated | invalidated
            "target": rule.target,
            "slo": slo,
            "rule": rule.to_dict(),
            "text": rule.text(),
            "domain": domain,                 # bounds of the experimental search space
            "scale": scale,                   # infrastructure conditions it was learned at
            "evidence": {"file": os.path.relpath(ev_file, self.root),
                         "n_experiments": len(evidence_rows)},
            "verification": verification,
            "metrics": metrics or {},
            "refinement": refinement or {},
            "notes": notes,
        }
        entries = [e for e in self.load() if e["id"] != cid] + [entry]
        self.save_all(entries)
        return entry

    def get_rule(self, cid: str) -> Rule:
        for e in self.load():
            if e["id"] == cid:
                return Rule.from_dict(e["rule"])
        raise KeyError(cid)
