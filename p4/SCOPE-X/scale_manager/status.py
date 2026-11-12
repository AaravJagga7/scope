"""Read Kubernetes state through kubectl.

All cluster access goes through one function, ``run_kubectl``, so tests can
replace it with a fake and so every call uses the same namespace/context.

    python -m scale_manager.status            # Checkout deployment + pods
    python -m scale_manager.status --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

from .config import DEFAULT_SETTINGS, ScaleSettings

Runner = Callable[[List[str]], str]


class KubectlError(RuntimeError):
    pass


def make_runner(settings: ScaleSettings = DEFAULT_SETTINGS) -> Runner:
    """Return a function that runs ``kubectl <args>`` and returns stdout."""
    def run(args: List[str]) -> str:
        cmd = [settings.kubectl]
        if settings.context:
            cmd += ["--context", settings.context]
        cmd += ["-n", settings.namespace] + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise KubectlError(f"'{settings.kubectl}' not found on PATH")
        except subprocess.TimeoutExpired:
            raise KubectlError(f"timed out: {' '.join(cmd)}")
        if p.returncode != 0:
            raise KubectlError(f"{' '.join(cmd)} failed:\n{p.stderr.strip()}")
        return p.stdout
    return run


@dataclass
class DeploymentStatus:
    name: str
    desired: int            # spec.replicas - what we asked for
    current: int            # status.replicas - pods the controller manages
    ready: int              # passing readiness probe
    available: int          # ready for at least minReadySeconds
    updated: int            # running the latest pod template
    generation: int
    observed_generation: int

    @property
    def settled(self) -> bool:
        """The controller has seen the latest spec and every requested pod is ready."""
        return (self.observed_generation >= self.generation
                and self.current == self.desired == self.ready == self.available == self.updated)


@dataclass
class PodStatus:
    name: str
    phase: str
    ready: bool
    terminating: bool
    node: str
    restarts: int


def deployment_status(name: str, run: Runner) -> DeploymentStatus:
    d = json.loads(run(["get", "deployment", name, "-o", "json"]))
    spec, st, meta = d.get("spec", {}), d.get("status", {}), d.get("metadata", {})
    return DeploymentStatus(
        name=name,
        desired=int(spec.get("replicas", 0)),
        current=int(st.get("replicas", 0)),
        ready=int(st.get("readyReplicas", 0)),
        available=int(st.get("availableReplicas", 0)),
        updated=int(st.get("updatedReplicas", 0)),
        generation=int(meta.get("generation", 0)),
        observed_generation=int(st.get("observedGeneration", 0)),
    )


def pod_statuses(label_selector: str, run: Runner) -> List[PodStatus]:
    items = json.loads(run(["get", "pods", "-l", label_selector, "-o", "json"])).get("items", [])
    pods = []
    for p in items:
        conds = {c["type"]: c["status"] for c in p.get("status", {}).get("conditions", [])}
        cs = p.get("status", {}).get("containerStatuses", []) or []
        pods.append(PodStatus(
            name=p["metadata"]["name"],
            phase=p.get("status", {}).get("phase", "Unknown"),
            ready=conds.get("Ready") == "True",
            terminating="deletionTimestamp" in p["metadata"],
            node=p.get("spec", {}).get("nodeName", ""),
            restarts=sum(int(c.get("restartCount", 0)) for c in cs),
        ))
    return sorted(pods, key=lambda x: x.name)


def _cli():
    ap = argparse.ArgumentParser(description="Show Checkout deployment and pod status")
    ap.add_argument("--service", default="checkout")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    t = DEFAULT_SETTINGS.services[a.service]
    run = make_runner()
    d = deployment_status(t.deployment, run)
    pods = pod_statuses(t.label_selector, run)
    if a.json:
        print(json.dumps({"deployment": asdict(d), "settled": d.settled,
                          "pods": [asdict(p) for p in pods]}, indent=2))
        return
    print(f"{d.name}: desired={d.desired} current={d.current} ready={d.ready} "
          f"available={d.available} updated={d.updated} settled={d.settled}")
    for p in pods:
        flag = " (terminating)" if p.terminating else ""
        print(f"  {p.name:40s} {p.phase:9s} ready={str(p.ready):5s} restarts={p.restarts}{flag}")


if __name__ == "__main__":
    try:
        _cli()
    except KubectlError as e:
        raise SystemExit(f"kubectl error: {e}\nIs Minikube running? Try: minikube status")
