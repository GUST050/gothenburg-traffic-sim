"""Bounds/priors intake for calibration — split from
build_sumo_demand.py 2026-07-14 (IMPROVEMENT_PLAN.md H1).

Owns the ensure_* functions that (re)build level-2 observability bounds,
corridor priors, the gravity-assignment prior, and learned per-edge
priors — each cached against the current graph fingerprint and rebuilt by
shelling out to its own tool. Structural inputs always come from
STRUCTURAL_REFERENCE_DATE (see build_sumo_demand's module docstring).
Patch subprocess/GEO_PATH HERE.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GEO_PATH = Path("web/data/network.geojson")
STRUCTURAL_REFERENCE_DATE = "2025-09-16"

def ensure_bounds(date: str, begin: str, end: str) -> dict:
    """Level-2 interval bounds for this window — computed on demand."""
    path = Path("web/data/observability_bounds.json")
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        with open(GEO_PATH) as f:
            n_now = len(json.load(f)["features"])
        # date/window AND graph fingerprint must match — stale bounds from a
        # different network silently poison the calibration as infeasibility
        if ((d["date"], d["begin"], d["end"]) == (date, begin, end)
                and d.get("graph_edges") == n_now):
            return d
    print("Computing level-2 bounds (observability LP) …")
    from observability import compute_bounds_cli
    compute_bounds_cli(date, begin, end)
    with open(path) as f:
        return json.load(f)


def ensure_observability() -> dict:
    """Fresh Agent-B products (derived flows, corridor priors) for THIS graph."""
    path = Path("web/data/observability.json")
    with open(GEO_PATH) as f:
        n_now = len(json.load(f)["features"])
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        if d.get("graph_edges") == n_now:
            return d
    print("Running observability (Agent B) …")
    res = subprocess.run([sys.executable, "observability.py"],
                         capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        print(res.stderr[-800:])
        return {"corridor_priors": {}, "derived_flows": {}}
    with open(path) as f:
        return json.load(f)


def ensure_assignment_priors() -> dict:
    """Weak gravity-assignment prior for every otherwise-unconstrained edge
    (assignment_priors.py) — replaces the PFE's implicit 'pull to zero'
    (parsimony term) with 'pull toward the gravity-implied realistic
    level' everywhere a real measurement, bound, direction prior or
    corridor coupling doesn't already apply."""
    path = Path("sumo/assignment_priors.json")
    if not path.exists():
        print("Computing assignment priors (assignment_priors.py) …")
        res = subprocess.run([sys.executable, "assignment_priors.py"],
                             capture_output=True, text=True, timeout=1200)
        if res.returncode != 0:
            print(res.stderr[-800:])
            return {"weight": 0.0, "flows": {}}
    with open(path) as f:
        return json.load(f)


def ensure_priors(date: str) -> dict:
    """Level-3 learned priors for unmeasured opposite directions."""
    path = Path("sumo/prior_flows.json")
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        if d.get("date") == date:
            return d
    print("Computing level-3 priors (prior_flows) …")
    res = subprocess.run([sys.executable, "prior_flows.py", "--date", date],
                         capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        print(res.stderr[-1000:])
        print("  (no priors available — continuing without level 3)")
        return {"edges": {}}
    with open(path) as f:
        return json.load(f)


def structural_bounds_and_priors(begin: str, end: str) -> tuple[dict, dict]:
    """Load date-invariant structural inputs, never target-date inputs."""
    return (ensure_bounds(STRUCTURAL_REFERENCE_DATE, begin, end),
            ensure_priors(STRUCTURAL_REFERENCE_DATE))
