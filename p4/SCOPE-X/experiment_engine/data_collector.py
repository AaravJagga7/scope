"""Collects experiment rows into a CSV dataset (one row per experiment)."""
from __future__ import annotations

import csv
import json
import os
from typing import Dict, Iterable, List

import pandas as pd

INPUT_COLS = ["traffic_rate", "replicas", "admission_rate", "queue_capacity", "bucket_capacity"]
OUTPUT_COLS = ["L95_ms", "L99_ms", "L50_ms", "throughput_rps", "error_rate", "rejection_rate",
               "Qw_mean_ms", "Qw95_ms", "Qw_max_ms", "queue_max", "cpu_util", "worker_util", "db_util",
               "db_write_mean_ms"]
META_COLS = ["experiment_id", "strategy", "seed", "backend", "arrivals", "admitted",
             "rejected", "expired", "aborted", "abort_reason", "slo_ok", "sim_time_s", "extra"]
ALL_COLS = META_COLS[:3] + INPUT_COLS + OUTPUT_COLS + META_COLS[3:]


class DataCollector:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, row: Dict) -> None:
        new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        row = dict(row)
        if isinstance(row.get("extra"), dict):
            row["extra"] = json.dumps(row["extra"])
        with open(self.path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ALL_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def extend(self, rows: Iterable[Dict]) -> None:
        for r in rows:
            self.append(r)

    def load(self) -> pd.DataFrame:
        return pd.read_csv(self.path) if os.path.exists(self.path) else pd.DataFrame(columns=ALL_COLS)

    def reset(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
