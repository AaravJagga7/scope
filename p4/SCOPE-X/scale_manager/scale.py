"""Scale Manager: change Checkout capacity programmatically and record it.

What it does for each change
  1. read the current replica count
  2. refuse targets outside the allowed range (1-6, same as the OR engine)
  3. kubectl scale
  4. wait until the change has *settled*: controller saw it, exactly N pods
     ready/available/updated, and no terminating pods left (so scale-down
     timings include draining)
  5. append one row to datasets/infra/scale_events.csv
     (time, from, to, seconds-to-ready, success, pod names, note)

    python -m scale_manager.scale --replicas 4 --note "first 2->4 test"
    python -m scale_manager.scale --sequence 1 2 4 2 --hold 10
    python -m scale_manager.scale --show-log
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from .config import DEFAULT_SETTINGS, ScaleSettings
from .status import (DeploymentStatus, KubectlError, Runner, deployment_status, make_runner,
                     pod_statuses)

LOG_COLUMNS = ["event_id", "timestamp_utc", "service", "deployment", "namespace", "action",
               "from_replicas", "to_replicas", "ready_s", "success", "ready_pods",
               "terminating_left", "error", "note", "detail"]


@dataclass
class ScaleEvent:
    service: str
    deployment: str
    namespace: str
    action: str                 # "scale" | "scale_noop" | "resources"
    from_replicas: int
    to_replicas: int
    ready_s: Optional[float]    # seconds from request until settled (None if not waited)
    success: bool
    ready_pods: List[str] = field(default_factory=list)
    terminating_left: int = 0
    error: str = ""
    note: str = ""
    detail: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp_utc: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc)
                               .isoformat(timespec="seconds"))


class ScaleManager:
    def __init__(self, settings: ScaleSettings = DEFAULT_SETTINGS, run: Optional[Runner] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.s = settings
        self.run = run or make_runner(settings)
        self.clock, self.sleep = clock, sleep

    # ------------------------------------------------------------- reading
    def status(self, service: str = "checkout") -> DeploymentStatus:
        return deployment_status(self.s.services[service].deployment, self.run)

    def current_replicas(self, service: str = "checkout") -> int:
        return self.status(service).desired

    # ------------------------------------------------------------- waiting
    def wait_until_settled(self, service: str, target: int) -> tuple:
        """Poll until exactly ``target`` pods are ready and none are terminating.

        Returns (settled: bool, ready_pod_names, terminating_count, last_status).
        """
        t = self.s.services[service]
        deadline = self.clock() + self.s.ready_timeout_s
        while True:
            d = deployment_status(t.deployment, self.run)
            pods = pod_statuses(t.label_selector, self.run)
            live = [p for p in pods if not p.terminating]
            ready = [p.name for p in live if p.ready]
            terminating = sum(p.terminating for p in pods)
            if d.settled and d.desired == target and len(ready) == target == len(live) \
                    and terminating == 0:
                return True, ready, 0, d
            if self.clock() >= deadline:
                return False, ready, terminating, d
            self.sleep(self.s.poll_interval_s)

    # ------------------------------------------------------------- changing
    def scale(self, replicas: int, service: str = "checkout", wait: bool = True,
              note: str = "") -> ScaleEvent:
        t = self.s.services[service]
        if not t.min_replicas <= replicas <= t.max_replicas:
            raise ValueError(f"{service}: replicas must be in [{t.min_replicas}, {t.max_replicas}], "
                             f"got {replicas}")
        before = self.current_replicas(service)
        action = "scale_noop" if before == replicas else "scale"
        t0 = self.clock()
        ev = ScaleEvent(service, t.deployment, self.s.namespace, action, before, replicas,
                        None, False, note=note)
        try:
            if action == "scale":
                self.run(["scale", "deployment", t.deployment, f"--replicas={replicas}"])
            if wait:
                ok, ready, term, d = self.wait_until_settled(service, replicas)
                ev.ready_s = round(self.clock() - t0, 3)
                ev.success, ev.ready_pods, ev.terminating_left = ok, ready, term
                if not ok:
                    ev.error = (f"not settled after {self.s.ready_timeout_s:.0f}s: desired={d.desired} "
                                f"ready={d.ready} available={d.available} terminating={term}")
            else:
                ev.success = True
        except KubectlError as e:
            ev.error = str(e)
        self.record(ev)
        return ev

    def set_resources(self, service: str, cpu_request: str = "", memory_request: str = "",
                      cpu_limit: str = "", memory_limit: str = "", wait: bool = True,
                      note: str = "") -> ScaleEvent:
        """Change per-pod resources (the A in S = (r_c, r_s, A)).

        This triggers a rolling restart of every pod, so it is a much bigger
        disturbance than scaling - never do it inside a measurement window.
        """
        t = self.s.services[service]
        args = ["set", "resources", "deployment", t.deployment]
        req = ",".join(x for x in (f"cpu={cpu_request}" if cpu_request else "",
                                   f"memory={memory_request}" if memory_request else "") if x)
        lim = ",".join(x for x in (f"cpu={cpu_limit}" if cpu_limit else "",
                                   f"memory={memory_limit}" if memory_limit else "") if x)
        if not req and not lim:
            raise ValueError("give at least one request or limit")
        if req:
            args.append(f"--requests={req}")
        if lim:
            args.append(f"--limits={lim}")
        n = self.current_replicas(service)
        t0 = self.clock()
        ev = ScaleEvent(service, t.deployment, self.s.namespace, "resources", n, n, None, False,
                        note=note, detail=f"requests={req} limits={lim}")
        try:
            self.run(args)
            if wait:
                ok, ready, term, _ = self.wait_until_settled(service, n)
                ev.ready_s = round(self.clock() - t0, 3)
                ev.success, ev.ready_pods, ev.terminating_left = ok, ready, term
                if not ok:
                    ev.error = "rollout not settled before timeout"
            else:
                ev.success = True
        except KubectlError as e:
            ev.error = str(e)
        self.record(ev)
        return ev

    def apply_scale_config(self, scale_config: Dict, note: str = "") -> List[ScaleEvent]:
        """Apply S, e.g. {"checkout_replicas": 4, "checkout_cpu": "500m",
        "checkout_memory": "512Mi"}. Keys are ``<service>_replicas``,
        ``<service>_cpu`` and ``<service>_memory`` (requests). Resources are
        applied before replicas so new pods start with the new allocation."""
        # validate everything before touching the cluster
        unknown = {k for k in scale_config
                   if k.rsplit("_", 1)[0] not in self.s.services
                   or k.rsplit("_", 1)[-1] not in ("replicas", "cpu", "memory")}
        if unknown:
            raise KeyError(f"unknown keys {sorted(unknown)}: use <service>_replicas/_cpu/_memory "
                           f"for a service listed in scale_manager/config.py")
        events = []
        for svc in self.s.services:
            cpu, mem = scale_config.get(f"{svc}_cpu", ""), scale_config.get(f"{svc}_memory", "")
            if cpu or mem:
                events.append(self.set_resources(svc, cpu_request=cpu, memory_request=mem, note=note))
            if f"{svc}_replicas" in scale_config:
                events.append(self.scale(int(scale_config[f"{svc}_replicas"]), svc, note=note))
        return events

    # -------------------------------------------------------------- logging
    def record(self, ev: ScaleEvent) -> None:
        os.makedirs(os.path.dirname(self.s.log_path), exist_ok=True)
        new = not os.path.exists(self.s.log_path) or os.path.getsize(self.s.log_path) == 0
        row = asdict(ev)
        row["ready_pods"] = " ".join(ev.ready_pods)
        with open(self.s.log_path, "a", newline="") as f:      # append only, never truncates
            w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in LOG_COLUMNS})


def _print(ev: ScaleEvent) -> None:
    status = "OK" if ev.success else "FAILED"
    t = "-" if ev.ready_s is None else f"{ev.ready_s:.1f}s"
    print(f"[{status}] {ev.service}: {ev.from_replicas} -> {ev.to_replicas} ({ev.action}), "
          f"settled in {t}; ready pods: {len(ev.ready_pods)}")
    if ev.error:
        print(f"   error: {ev.error}")


def _cli():
    ap = argparse.ArgumentParser(description="SCOPE-X Scale Manager")
    ap.add_argument("--service", default="checkout")
    ap.add_argument("--replicas", type=int)
    ap.add_argument("--sequence", type=int, nargs="+", help="e.g. 1 2 4 2")
    ap.add_argument("--hold", type=float, default=0, help="seconds to stay at each step of --sequence")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--show-log", action="store_true")
    a = ap.parse_args()
    sm = ScaleManager()
    if a.show_log:
        if os.path.exists(sm.s.log_path):
            with open(sm.s.log_path) as f:
                print(f.read())
        else:
            print(f"no log yet at {sm.s.log_path}")
        return
    steps = a.sequence or ([a.replicas] if a.replicas is not None else [])
    if not steps:
        ap.error("give --replicas N, --sequence ..., or --show-log")
    t = sm.s.services[a.service]
    bad = [n for n in steps if not t.min_replicas <= n <= t.max_replicas]
    if bad:
        ap.error(f"replicas must be in [{t.min_replicas}, {t.max_replicas}]; got {bad}")
    print(f"current {a.service} replicas: {sm.current_replicas(a.service)}")
    for i, n in enumerate(steps):
        ev = sm.scale(n, a.service, wait=not a.no_wait, note=a.note)
        _print(ev)
        if not ev.success:
            raise SystemExit(1)
        if a.hold and i < len(steps) - 1:
            time.sleep(a.hold)
    print(f"log: {sm.s.log_path}")


if __name__ == "__main__":
    try:
        _cli()
    except KubectlError as e:
        raise SystemExit(f"kubectl error: {e}\nIs Minikube running? Try: minikube status")
