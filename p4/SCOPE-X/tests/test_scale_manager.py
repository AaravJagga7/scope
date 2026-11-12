"""Scale Manager tests against a fake kubectl (no cluster needed).

FakeCluster imitates the parts of Kubernetes the Scale Manager reads: new
pods become Ready a few polls after a scale-up, removed pods stay
Terminating for a few polls after a scale-down.
"""
import csv
import json
import os

import pytest
import yaml

from scale_manager.config import ScaleSettings
from scale_manager.scale import ScaleManager
from scale_manager.status import KubectlError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeCluster:
    def __init__(self, replicas=1, start_polls=2, stop_polls=2, never_ready=False):
        self.spec = replicas
        self.generation = 1
        self.observed = 1
        self.pods = [{"name": f"checkout-{i}", "age": 99, "dying": None} for i in range(replicas)]
        self.next_id = replicas
        self.start_polls, self.stop_polls = start_polls, stop_polls
        self.never_ready = never_ready
        self.calls = []

    def tick(self):
        for p in self.pods:
            p["age"] += 1
            if p["dying"] is not None:
                p["dying"] += 1
        self.pods = [p for p in self.pods if p["dying"] is None or p["dying"] < self.stop_polls]

    def ready(self, p):
        return p["dying"] is None and p["age"] >= self.start_polls and not self.never_ready

    def __call__(self, args):
        self.calls.append(args)
        i = args.index("-n") + 2 if "-n" in args else 0
        cmd = args[i:] if "-n" in args else args
        if cmd[0] == "scale":
            n = int(cmd[-1].split("=")[1])
            live = [p for p in self.pods if p["dying"] is None]
            for _ in range(n - len(live)):
                self.pods.append({"name": f"checkout-{self.next_id}", "age": 0, "dying": None})
                self.next_id += 1
            for p in live[n:] if n < len(live) else []:
                p["dying"] = 0
            self.spec, self.generation = n, self.generation + 1
            return "scaled"
        if cmd[:2] == ["set", "resources"]:
            self.generation += 1
            return "updated"
        if cmd[:2] == ["get", "deployment"]:
            self.tick()
            self.observed = self.generation
            live = [p for p in self.pods if p["dying"] is None]
            r = sum(self.ready(p) for p in live)
            return json.dumps({"metadata": {"generation": self.generation},
                               "spec": {"replicas": self.spec},
                               "status": {"observedGeneration": self.observed, "replicas": len(live),
                                          "readyReplicas": r, "availableReplicas": r,
                                          "updatedReplicas": len(live)}})
        if cmd[:2] == ["get", "pods"]:
            items = []
            for p in self.pods:
                meta = {"name": p["name"]}
                if p["dying"] is not None:
                    meta["deletionTimestamp"] = "now"
                items.append({"metadata": meta, "spec": {"nodeName": "minikube"},
                              "status": {"phase": "Running", "conditions": [
                                  {"type": "Ready", "status": "True" if self.ready(p) else "False"}],
                                  "containerStatuses": [{"restartCount": 0}]}})
            return json.dumps({"items": items})
        raise KubectlError(f"fake kubectl: unsupported {cmd}")


def manager(tmp_path, cluster, timeout=30):
    s = ScaleSettings(log_path=str(tmp_path / "infra" / "scale_events.csv"),
                      ready_timeout_s=timeout, poll_interval_s=1)
    t = {"now": 0.0}
    return ScaleManager(s, run=cluster, clock=lambda: t["now"],
                        sleep=lambda d: t.__setitem__("now", t["now"] + d))


def read_log(sm):
    with open(sm.s.log_path) as f:
        return list(csv.DictReader(f))


def test_scale_up_waits_for_ready_and_logs(tmp_path):
    c = FakeCluster(replicas=2, start_polls=3)
    sm = manager(tmp_path, c)
    ev = sm.scale(4, note="2->4")
    assert ev.success and ev.from_replicas == 2 and ev.to_replicas == 4
    assert len(ev.ready_pods) == 4 and ev.ready_s >= 2      # had to wait for new pods
    row = read_log(sm)[0]
    assert row["from_replicas"] == "2" and row["to_replicas"] == "4" and row["success"] == "True"
    assert row["note"] == "2->4"


def test_scale_down_waits_for_terminating_pods(tmp_path):
    c = FakeCluster(replicas=4, stop_polls=3)
    sm = manager(tmp_path, c)
    ev = sm.scale(2)
    assert ev.success and ev.terminating_left == 0 and len(ev.ready_pods) == 2
    assert ev.ready_s >= 2


def test_noop_scale_is_recorded(tmp_path):
    sm = manager(tmp_path, FakeCluster(replicas=2))
    ev = sm.scale(2)
    assert ev.action == "scale_noop" and ev.success
    assert not any(a[a.index("-n") + 2 if "-n" in a else 0] == "scale" for a in sm.run.calls)


def test_bounds_refused_before_touching_cluster(tmp_path):
    c = FakeCluster(replicas=2)
    sm = manager(tmp_path, c)
    with pytest.raises(ValueError):
        sm.scale(7)
    with pytest.raises(ValueError):
        sm.scale(0)
    assert c.calls == [] and not os.path.exists(sm.s.log_path)


def test_timeout_is_reported_not_hidden(tmp_path):
    sm = manager(tmp_path, FakeCluster(replicas=2, never_ready=True), timeout=5)
    ev = sm.scale(3)
    assert not ev.success and "not settled" in ev.error
    assert read_log(sm)[0]["success"] == "False"


def test_log_is_append_only(tmp_path):
    sm = manager(tmp_path, FakeCluster(replicas=1, start_polls=1))
    for n in (2, 4, 2):
        sm.scale(n)
    rows = read_log(sm)
    assert [(r["from_replicas"], r["to_replicas"]) for r in rows] == [("1", "2"), ("2", "4"), ("4", "2")]
    assert len({r["event_id"] for r in rows}) == 3


def test_apply_scale_config(tmp_path):
    c = FakeCluster(replicas=2, start_polls=1)
    sm = manager(tmp_path, c)
    evs = sm.apply_scale_config({"checkout_replicas": 4, "checkout_cpu": "500m",
                                 "checkout_memory": "512Mi"})
    assert [e.action for e in evs] == ["resources", "scale"]
    assert any(a[:2] == ["set", "resources"] and "--requests=cpu=500m,memory=512Mi" in a
               for a in [x[x.index("-n") + 2:] if "-n" in x else x for x in c.calls])
    with pytest.raises(KeyError):
        sm.apply_scale_config({"inventory_replicas": 2})     # not configured yet


def test_manifests_are_consistent():
    with open(os.path.join(ROOT, "kubernetes", "checkout-deployment.yaml")) as f:
        dep = yaml.safe_load(f)
    with open(os.path.join(ROOT, "kubernetes", "checkout-service.yaml")) as f:
        svc = yaml.safe_load(f)
    sel = dep["spec"]["selector"]["matchLabels"]
    assert sel == {"app": "checkout"}
    assert all(dep["spec"]["template"]["metadata"]["labels"][k] == v for k, v in sel.items())
    assert svc["spec"]["selector"] == sel
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert "readinessProbe" in c and "requests" in c["resources"]
