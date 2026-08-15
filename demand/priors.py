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

import hashlib
import json
import subprocess
import sys
from pathlib import Path

GEO_PATH = Path("web/data/network.geojson")
STRUCTURAL_REFERENCE_DATE = "2025-09-16"


def direction_priors_are_current(data: dict, date: str, *,
                                 model_path: Path = Path(
                                     "data/dirsplit/model.pkl")) -> bool:
    """Whether a learned opposite-direction prior matches the live model."""
    if (data.get("schema_version") != "dirsplit_prior_v2"
            or data.get("date") != date or not model_path.is_file()):
        return False
    return data.get("model_sha256") == hashlib.sha256(
        model_path.read_bytes()).hexdigest()

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


def _assignment_prior_cache_contract(**config) -> tuple[int, str]:
    """Load the producer's cache contract without duplicating it here."""
    # Import lazily: assignment_priors imports the candidate/end-point stack,
    # while this module is imported by the demand orchestrator at startup.
    from assignment_priors import (ASSIGNMENT_PRIOR_SCHEMA_VERSION,
                                   assignment_prior_input_key)
    return (ASSIGNMENT_PRIOR_SCHEMA_VERSION,
            assignment_prior_input_key(**config))


def ensure_assignment_priors(
    *, gravity_km: float | None = None,
    through_fraction: float | None = None,
    cross_fraction: float | None = None,
    gravity_alpha: float | None = None,
    seed: int | None = None,
) -> dict:
    """Weak gravity-assignment prior for every otherwise-unconstrained edge
    (assignment_priors.py) — replaces the PFE's implicit 'pull to zero'
    (parsimony term) with 'pull toward the gravity-implied realistic
    level' everywhere a real measurement, bound, direction prior or
    corridor coupling doesn't already apply."""
    # The field is a structural approximation, but its OD class mix and
    # gravity kernel must still be the SAME ones used to generate the
    # candidate route population for this build.  Omit None values so direct
    # callers retain assignment_priors.py's own documented defaults.
    config = {
        name: value for name, value in {
            "gravity_km": gravity_km,
            "through_fraction": through_fraction,
            "cross_fraction": cross_fraction,
            "gravity_alpha": gravity_alpha,
            "seed": seed,
        }.items() if value is not None
    }
    path = Path("sumo/assignment_priors.json")
    try:
        expected_schema, expected_key = _assignment_prior_cache_contract(**config)
    except (ImportError, OSError, ValueError) as exc:
        # A structurally unverified upper-bound field is worse than no weak
        # field at all: it also affects candidate gate draws. Fail closed.
        print(f"WARNING assignment-prior contract unavailable: {exc}")
        return {"weight": 0.0, "flows": {}}

    existing = None
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            print("Assignment-prior artifact is unreadable; rebuilding …")
        else:
            if (existing.get("schema_version") == expected_schema
                    and existing.get("input_key") == expected_key):
                return existing
            print("Assignment-prior inputs changed or artifact is legacy; rebuilding …")

    if existing is None or (existing.get("schema_version") != expected_schema
                            or existing.get("input_key") != expected_key):
        print("Computing assignment priors (assignment_priors.py) …")
        command = [sys.executable, "assignment_priors.py"]
        cli_flags = {
            "gravity_km": "--gravity-km",
            "through_fraction": "--through-fraction",
            "cross_fraction": "--cross-fraction",
            "gravity_alpha": "--gravity-alpha",
            "seed": "--seed",
        }
        for name, flag in cli_flags.items():
            if name in config:
                command += [flag, str(config[name])]
        res = subprocess.run(command,
                             capture_output=True, text=True, timeout=1200)
        if res.returncode != 0:
            print(res.stderr[-800:])
            return {"weight": 0.0, "flows": {}}
    try:
        with open(path) as f:
            rebuilt = json.load(f)
    except (OSError, json.JSONDecodeError):
        print("Assignment-prior rebuild did not publish a readable artifact")
        return {"weight": 0.0, "flows": {}}
    if (rebuilt.get("schema_version") != expected_schema
            or rebuilt.get("input_key") != expected_key):
        print("Assignment-prior rebuild published an unexpected input identity; ignoring it")
        return {"weight": 0.0, "flows": {}}
    return rebuilt


def ensure_priors(date: str) -> dict:
    """Level-3 learned priors for unmeasured opposite directions."""
    path = Path("sumo/prior_flows.json")
    if path.exists():
        with open(path) as f:
            d = json.load(f)
        if direction_priors_are_current(d, date):
            return d
    print("Computing level-3 priors (prior_flows) …")
    res = subprocess.run([sys.executable, "prior_flows.py", "--date", date],
                         capture_output=True, text=True, timeout=1200)
    if res.returncode != 0:
        print(res.stderr[-1000:])
        print("  (no priors available — continuing without level 3)")
        return {"edges": {}}
    with open(path) as f:
        rebuilt = json.load(f)
    if not direction_priors_are_current(rebuilt, date):
        print("  rebuilt direction priors do not match the live model; ignoring")
        return {"edges": {}}
    return rebuilt


def build_interval_constraints(
    n_intervals: int,
    qi_start: int,
    bounds_data: dict,
    priors_data: dict,
    corridor_priors: dict,
    assignment_data: dict,
    prior_key: str = "prior",
    opposite_bounds: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build the exact per-quarter PFE constraint hierarchy used in production.

    Level-2 structural bounds always take precedence over the broad
    assignment ceilings; level-3 learned/corridor priors take precedence over
    assignment ceilings too.  Keeping this construction here gives the
    demand builder and the bit-identity verifier one implementation, so a
    verification run cannot silently solve a different mathematical problem.
    """
    bounds_per_q: list[dict] = []
    priors_per_q: list[dict] = []
    hard_bounds_per_q: list[dict] = []
    assignment_weight = assignment_data.get("weight", 0.0)
    assignment_flows = assignment_data.get("flows", {})

    for i in range(n_intervals):
        hard_bounds = {}
        for edge_id, series in bounds_data.get("bounds", {}).items():
            # Bounds are structural reference-day relationships; repeat their
            # time-of-day slots for every target day in a multi-day build.
            slot_i = i % 96
            if slot_i < len(series) and series[slot_i]:
                hard_bounds[edge_id] = (series[slot_i][0], series[slot_i][1])

        # The unmeasured carriageway at each single-direction station. It is a
        # level-2 hard bound like any other structural relationship, so the
        # solution MUST land inside it -- the estimate is fulfilled, not
        # merely suggested. It is applied before the broad assignment
        # ceilings below, which explicitly never overwrite a hard bound.
        if opposite_bounds and i < len(opposite_bounds):
            for edge_id, span in opposite_bounds[i].items():
                hard_bounds.setdefault(edge_id, span)

        bounds = dict(hard_bounds)
        priors = {}
        slot = (qi_start + i) % 96
        for edge_id, data in priors_data.get("edges", {}).items():
            value = data[prior_key][slot]
            if value is None:
                continue
            low = data["prior_low"][slot] or 0.0
            high = data["prior_high"][slot] or value
            priors[edge_id] = (float(value), 1.0 / max(1.0, high - low))

        for edge_id, data in corridor_priors.items():
            qi = qi_start + i
            if qi >= len(data["prior"]) or data["prior"][qi] is None:
                continue
            band = data["band"][qi] or 8.0
            priors[edge_id] = (float(data["prior"][qi]),
                               1.0 / max(1.0, band))

        if assignment_weight > 0:
            for edge_id, series in assignment_flows.items():
                # Assignment is a wide plausibility ceiling only.  It must
                # never replace a hard structural bound or a stronger prior.
                if edge_id in bounds or edge_id in priors or slot >= len(series):
                    continue
                value = series[slot]
                if value is not None:
                    bounds[edge_id] = (0.0, max(5.0, 5.0 * value))

        bounds_per_q.append(bounds)
        priors_per_q.append(priors)
        hard_bounds_per_q.append(hard_bounds)

    return bounds_per_q, priors_per_q, hard_bounds_per_q


def structural_bounds_and_priors(begin: str, end: str) -> tuple[dict, dict]:
    """Load date-invariant structural inputs, never target-date inputs."""
    return (ensure_bounds(STRUCTURAL_REFERENCE_DATE, begin, end),
            ensure_priors(STRUCTURAL_REFERENCE_DATE))


def opposite_direction_bounds(
    flows: dict[str, list],
    n_intervals: int,
    qi_start: int,
    registry_path: Path | None = None,
    split_key: str = "edge_shares",
    anchor_day: str | None = None,
) -> list[dict[str, tuple[float, float]]]:
    """Level-2 bounds for the UNMEASURED carriageway at each station.

    Five of the six stations measure one direction only. Until 2026-08-06 the
    opposite carriageway carried no constraint at all: the PFE filled it from
    structure with nothing to answer to, which is a silent assumption dressed
    as a result. The direction model now predicts both sides, so the opposite
    flow follows from the measured one:

        v_opposite = v_measured * (1 - s) / s

    where ``s`` is the measured direction's predicted share. The q10 and q90
    shares give the interval; ``lo`` and ``hi`` are ordered afterwards because
    the mapping is DECREASING in ``s`` -- a larger measured share means less
    traffic left for the other side, so the q90 share yields the LOW bound.

    A bound, never a target. CLAUDE.md forbids presenting this direction as
    known, and it is not: the solution must land inside a declared interval,
    which is exactly what a level-2 bound expresses. The interval is wide --
    a measured 50 admits roughly 33 to 72 the other way -- and that width is
    the honest content, not a defect to tune away.
    """
    from demand.intake import load_direction_split

    registry_path = registry_path or Path("data_in/sensors.json")
    if not registry_path.is_file():
        return [{} for _ in range(n_intervals)]
    registry = json.loads(registry_path.read_text())
    rows = registry if isinstance(registry, list) else registry.get(
        "sensors", registry)

    split_kwargs = {"anchor_day": anchor_day} if anchor_day is not None else {}
    shares = {key: load_direction_split(key, **split_kwargs) for key in
              (split_key, "edge_shares_q10", "edge_shares_q90")}
    pairs = []
    for row in rows:
        spec = row.get("opposite_direction") or {}
        opposite = spec.get("edge_id")
        measured = [e for e in row.get("approved_edge_ids", [])]
        if not opposite or len(measured) != 1:
            continue                      # two-way stations need no inference
        pairs.append((measured[0], opposite))

    out: list[dict[str, tuple[float, float]]] = []
    for i in range(n_intervals):
        qi, slot = qi_start + i, (qi_start + i) % 96
        bounds: dict[str, tuple[float, float]] = {}
        for measured, opposite in pairs:
            series = flows.get(measured) or []
            v = series[qi] if qi < len(series) else None
            if v is None:
                continue                  # missing measurement constrains nothing
            candidates = []
            for key in ("edge_shares_q10", "edge_shares_q90"):
                s = (shares[key].get(measured) or [None] * 96)[slot]
                if s and 0.0 < s < 1.0:
                    candidates.append(v * (1.0 - s) / s)
            if len(candidates) == 2:
                # A CEILING ONLY. The lower bound was removed 2026-08-06 after
                # measuring that it bound at the FLOOR in 40.1% of edge-quarters
                # -- the solver wanted less traffic on the opposite carriageway
                # than the model demanded, so the constraint was manufacturing
                # vehicles no measurement asked for. That is precisely what the
                # baseline rule forbids ("only what is measured is simulated").
                #
                # The cause is structural, not numerical: the candidate pool is
                # sensor-anchored, so the unmeasured carriageway carries 3x to
                # 10x fewer routes than the measured one (283 against 2,779 at
                # station 133). With so few routes to spread over, meeting a
                # floor means inflating the ones that exist rather than using
                # appropriate ones. The right fix is to GENERATE routes that
                # serve that direction -- column generation -- not to coerce
                # the ones already there.
                #
                # A ceiling is safe in a way a floor is not: it can only
                # prevent implausible excess, never create traffic.
                bounds[opposite] = (0.0, max(candidates))
        out.append(bounds)
    return out
