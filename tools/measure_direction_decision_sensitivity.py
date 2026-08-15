"""Fas 0B — does the direction axis change a closure decision? (Gate S)

The question this answers is narrow on purpose. Before any scenario, monthly,
warm-state, API or UI work is justified, the plan requires evidence that
plausible direction variation changes something a user would act on. If it
does not, the whole branch closes and 50/50 plus sensor 107's local anchor is
a complete, successful outcome.

Design, all of it frozen before execution by the preregistration:

* The existing q10/q50/q90 route artifacts are used as NAMED STRESS CASES.
  They are not probability statements — their nominal coverage has never been
  validated — so this measures a spread, not a distribution.
* The SAME seed list runs for every stress case, and the same
  ``(stress case, seed)`` pair runs the baseline and the candidate. That is
  what separates the direction effect from simulator noise: without matched
  seeds the two are confounded, which is precisely the defect in today's
  default where each q case gets exactly one seed.
* Materiality is decided by comparing the BETWEEN-CASE spread against the
  WITHIN-CASE seed spread. A direction difference that is smaller than the
  noise the simulator produces anyway is not a decision difference.

This tool deliberately does not touch `ScenarioSpec`, the monthly path, the
warm-state cache, the API or the UI. It calls the existing runner and reduces
its output. Its artifacts carry ``release_evidence: false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def content_digest(payload: Any) -> str:
    """Stable SHA-256 over a JSON-serialisable payload.

    Local on purpose: this tool must stay runnable from a checkout that has
    nothing but the existing pipeline, so it does not reach for a shared
    helper that a later, conditional phase might introduce.
    """
    if isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SELECTION_RULE = "dirsplit_sensitivity_selection_v1"
PROTOCOL = "dirsplit_direction_decision_sensitivity_v1"

REGISTRATION_PATH = Path(
    "validation/dirsplit_direction_sensitivity_registration_v1.json")
OUTCOME_PATH = Path(
    "validation/dirsplit_direction_sensitivity_outcome_v1.json")

#: Route artifacts written by build_sumo_demand, in a fixed order.
STRESS_CASES: tuple[tuple[str, str], ...] = (
    ("q50", "calibrated.rou.xml"),
    ("q10", "calibrated_v1.rou.xml"),
    ("q90", "calibrated_v2.rou.xml"),
)

#: Case name -> the variant token run_scenario's ScenarioSpec understands.
#: run_scenario.variant_path resolves q50/q10/q90 to the route-file index, so
#: pinning the token is what actually selects the demand arm.
VARIANT_BY_CASE: Mapping[str, str] = {name: name for name, _f in STRESS_CASES}


# ──────────────────────────────────────────────────────────────────────────
# frozen materiality thresholds
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MaterialityThresholds:
    """What counts as a decision difference. Frozen before execution.

    ``spread_ratio`` is the load-bearing one: the between-case range must
    exceed the mean within-case seed range by this factor before the
    direction axis is credited with the difference. A ratio of 1.0 would
    credit direction with ordinary simulator noise.
    """

    spread_ratio: float = 2.0
    relative_objective: float = 0.10
    min_rank_correlation: float = 0.90
    require_identical_viable_set: bool = True
    require_identical_winner: bool = True


@dataclass(frozen=True)
class Registration:
    protocol: str
    selection_rule: str
    date: str
    window_begin: str
    window_end: str
    demand_window_begin: str
    seeds: tuple[int, ...]
    stress_cases: tuple[str, ...]
    candidate_edges: tuple[str, ...]
    candidate_selection: Mapping[str, Any]
    timeout_s: int
    objective: str
    comparison_fields: tuple[str, ...]
    thresholds: MaterialityThresholds
    source_digests: Mapping[str, str]
    release_evidence: bool = False
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["seeds"] = list(self.seeds)
        payload["stress_cases"] = list(self.stress_cases)
        payload["candidate_edges"] = list(self.candidate_edges)
        payload["comparison_fields"] = list(self.comparison_fields)
        payload["thresholds"] = asdict(self.thresholds)
        return payload

    @property
    def content_key(self) -> str:
        return content_digest(self.to_json())

    def closure_window_s(self, duration_s: int) -> tuple[int, int]:
        """The registered closure window, as offsets into the demand window.

        The demand build defines the simulated day; the registered
        window_begin/window_end name when the closure is ACTIVE inside it. An
        earlier version passed neither, so every candidate was closed for the
        whole run regardless of what the registration said.
        """
        begin = _seconds_of_day(self.window_begin)
        end = _seconds_of_day(self.window_end)
        epoch_offset = _seconds_of_day(self.demand_window_begin)
        begin_s = max(0, begin - epoch_offset)
        end_s = min(int(duration_s), end - epoch_offset)
        if end_s <= begin_s:
            raise ValueError(
                f"registered closure window {self.window_begin}-"
                f"{self.window_end} does not overlap the {duration_s}s demand "
                f"window starting {self.demand_window_begin}")
        return begin_s, end_s


def _seconds_of_day(value: str) -> int:
    hours, _, minutes = str(value).partition(":")
    return int(hours) * 3600 + int(minutes or 0) * 60


# ──────────────────────────────────────────────────────────────────────────
# outcome-blind candidate selection
# ──────────────────────────────────────────────────────────────────────────
def route_edge_exposure(route_path: Path) -> dict[str, int]:
    """Vehicles whose route traverses each edge, from a SUMO route file.

    Demand exposure is an INPUT-side signal: it is computable before any
    simulation runs, so selecting on it cannot see an outcome. The same
    pattern is already precedented in this repository's `demand_exposure_v1`
    held-out selection rule.
    """
    import xml.etree.ElementTree as ET

    counts: dict[str, int] = {}
    for _event, element in ET.iterparse(route_path, events=("end",)):
        if element.tag != "route":
            continue
        for edge in (element.get("edges") or "").split():
            counts[edge] = counts.get(edge, 0) + 1
        element.clear()
    return counts


def survives_own_closure(edge_id: str, net_path: Path) -> bool:
    """Can traffic still reach this edge's successors once it is closed?

    A candidate that severs its own downstream pocket is a topology failure
    rather than a direction-sensitivity observation, so it is filtered out
    before the experiment rather than becoming a confound inside it.
    """
    try:
        import sumolib
    except ImportError:
        return True                      # cannot probe; do not silently drop
    net = sumolib.net.readNet(str(net_path))
    if not net.hasEdge(edge_id):
        return False
    edge = net.getEdge(edge_id)
    successors = {e.getID() for e in edge.getOutgoing()}
    if not successors:
        return False
    frontier = [e.getID() for e in edge.getIncoming()]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current in seen or current == edge_id:
            continue
        seen.add(current)
        if current in successors:
            return True
        if not net.hasEdge(current):
            continue
        frontier.extend(e.getID() for e in net.getEdge(current).getOutgoing()
                        if e.getID() not in seen)
    return False


def select_candidates(sumo_dir: Path, sensor_edges: Iterable[str],
                      count: int) -> tuple[list[str], dict[str, Any]]:
    """Deterministic, outcome-blind candidate selection.

    Rule ``dirsplit_sensitivity_selection_v1``:
      1. exclude the measured sensor edges (closing a constraint edge is a
         different experiment);
      2. require strictly positive exposure in EVERY stress case, so no arm
         of the comparison is degenerate;
      3. require the edge to survive its own closure;
      4. rank by q50 exposure descending, tie-break by edge id ascending;
      5. take the first ``count``.
    """
    exposures: dict[str, dict[str, int]] = {}
    for name, filename in STRESS_CASES:
        path = sumo_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"stress case {name} route file is missing: {path}")
        exposures[name] = route_edge_exposure(path)

    excluded = set(sensor_edges)
    common = set(exposures["q50"])
    for name in exposures:
        common &= {edge for edge, n in exposures[name].items() if n > 0}
    pool = sorted(common - excluded,
                  key=lambda edge: (-exposures["q50"][edge], edge))

    net_path = sumo_dir / "net.net.xml"
    chosen: list[str] = []
    probed = 0
    for edge in pool:
        if len(chosen) >= count:
            break
        probed += 1
        if survives_own_closure(edge, net_path):
            chosen.append(edge)

    return chosen, {
        "rule": SELECTION_RULE,
        "pool_size": len(pool),
        "probed": probed,
        "excluded_sensor_edges": sorted(excluded),
        "exposure_q50": {edge: exposures["q50"][edge] for edge in chosen},
        "exposure_q10": {edge: exposures["q10"].get(edge, 0) for edge in chosen},
        "exposure_q90": {edge: exposures["q90"].get(edge, 0) for edge in chosen},
    }


# ──────────────────────────────────────────────────────────────────────────
# running the matrix
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Observation:
    """One (stress case, seed, candidate) result, with its decision fields.

    ``added_vehicle_hours`` is the product's own quantity and is ALREADY a
    paired within-run difference: run_scenario computes the cheapest legal
    path with and without the closure over the same calibrated routes. The
    matched-seed design therefore holds by construction for the objective,
    and running the identical seed list across the stress cases is what
    additionally removes simulator noise from the BETWEEN-CASE comparison.
    """

    stress_case: str
    seed: int
    candidate: str
    added_vehicle_hours: float | None
    vehicles_affected: int | None
    vehicles_considered: int | None
    vehicles_no_detour: int | None
    health_flags: tuple[str, ...]
    teleports: int | None
    collisions: int | None
    viable: bool
    hard_failure: bool
    failure_reason: str | None
    runtime_s: float


def _demand_identity(sumo_dir: Path) -> tuple[dict, str, int]:
    """(meta, demand_build_id, duration_s) for the loaded demand build."""
    import pandas as pd

    meta_path = sumo_dir / "demand_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"{meta_path} is missing; build demand before running Gate S")
    meta = json.loads(meta_path.read_text())

    sys.path.insert(0, str(ROOT))
    import run_scenario as rs

    demand_id = str(meta.get("build_id") or rs.demand_signature(meta))
    duration_s = int(meta["n_intervals"]) * int(meta["interval_minutes"]) * 60 \
        if "interval_minutes" in meta else int(meta["n_intervals"]) * 900
    return meta, demand_id, duration_s


def _spec_payload(*, name: str, meta: dict, demand_id: str, network_id: str,
                  duration_s: int, seed: int, variant: str,
                  closures: Sequence[str], closure_begin_s: int,
                  closure_end_s: int) -> dict[str, Any]:
    """A ScenarioSpec pinning exactly ONE seed to ONE demand variant.

    This is the only supported way to control which route file and which seed
    a run uses: run_scenario has no CLI flag for either, and its ``--seeds``
    argument is a COUNT rather than a value. Building the spec here is what
    makes the q10/q50/q90 arms genuinely different runs instead of three
    identical ones.

    A single-seed spec is legal: run_scenario only enforces three-variant
    coverage when the spec carries three or more seeds.
    """
    import pandas as pd

    start = pd.Timestamp(meta["epoch_sim"])
    end = start + pd.Timedelta(seconds=duration_s)
    closure_start = start + pd.Timedelta(seconds=closure_begin_s)
    closure_end = start + pd.Timedelta(seconds=closure_end_s)
    return {
        "schema_version": 1,
        "scenario_id": name,
        "demand_build_id": demand_id,
        "network_build_id": network_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "closures": [
            {
                "edge_id": edge,
                "start_time": closure_start.isoformat(),
                "end_time": closure_end.isoformat(),
                "closure_type": "full",
            }
            for edge in closures
        ],
        "simulation_mode": "meso",
        "seed_set": [seed],
        "demand_variant_mapping": [[seed, variant]],
    }


def run_matrix(registration: Registration, sumo_dir: Path,
               out_dir: Path) -> list[Observation]:
    """Run every (stress case, seed, candidate) as its own pinned run."""
    sys.path.insert(0, str(ROOT))
    from traffic_sim.core.contracts import SCHEMA_VERSION  # noqa: F401
    import run_scenario as rs

    meta, demand_id, duration_s = _demand_identity(sumo_dir)
    network_id = rs.sha256_file(sumo_dir / "net.net.xml")
    if network_id is None:
        raise FileNotFoundError(f"{sumo_dir / 'net.net.xml'} is missing")

    begin_s, end_s = registration.closure_window_s(duration_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    observations: list[Observation] = []

    for case_name in registration.stress_cases:
        variant = VARIANT_BY_CASE[case_name]
        for seed in registration.seeds:
            for candidate in registration.candidate_edges:
                tag = f"{case_name}_{seed}_{candidate}"
                spec = _spec_payload(
                    name=tag, meta=meta, demand_id=demand_id,
                    network_id=network_id, duration_s=duration_s, seed=seed,
                    variant=variant, closures=[candidate],
                    closure_begin_s=begin_s, closure_end_s=end_s)
                observations.append(_run_one(
                    spec, registration, out_dir, tag,
                    stress_case=case_name, seed=seed, candidate=candidate))
    return observations


def _run_one(spec: Mapping[str, Any], registration: Registration,
             out_dir: Path, tag: str, *, stress_case: str, seed: int,
             candidate: str) -> Observation:
    """One SUMO run driven by a written ScenarioSpec."""
    run_dir = out_dir / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")

    command = [
        sys.executable, str(ROOT / "run_scenario.py"),
        "--scenario-spec", str(spec_path),
        "--no-trajectories",
        "--out-dir", str(run_dir),
        "--name", tag,
    ]

    def failed(reason: str, runtime: float) -> Observation:
        return Observation(
            stress_case=stress_case, seed=seed, candidate=candidate,
            added_vehicle_hours=None, vehicles_affected=None,
            vehicles_considered=None, vehicles_no_detour=None,
            health_flags=(), teleports=None, collisions=None, viable=False,
            hard_failure=True, failure_reason=reason, runtime_s=runtime)

    started = time.time()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=registration.timeout_s)
    except subprocess.TimeoutExpired:
        return failed(f"timeout after {registration.timeout_s}s",
                      time.time() - started)
    runtime = time.time() - started
    if completed.returncode != 0:
        return failed(f"exit {completed.returncode}: "
                      f"{completed.stderr.strip()[-300:]}", runtime)

    scenario_path = run_dir / f"{tag}.json"
    if not scenario_path.exists():
        found = sorted(p for p in run_dir.glob("*.json")
                       if p.name not in ("spec.json", "index.json"))
        if not found:
            return failed("no scenario artifact produced", runtime)
        scenario_path = found[0]

    payload = json.loads(scenario_path.read_text())
    disruption = payload.get("disruption") or {}
    if not disruption:
        return failed("scenario carries no disruption block", runtime)

    health = payload.get("seed_health") or []
    flags = tuple(str(f) for f in (payload.get("seed_health_flags") or []))
    teleports = sum(int(h.get("teleports") or 0) for h in health) or 0
    collisions = sum(int(h.get("collisions") or 0) for h in health) or 0

    no_detour = disruption.get("vehicles_no_detour")
    # Viability is a HARD gate: a candidate that strands vehicles with no
    # legal detour, or that trips a health flag, is not a usable closure and
    # must not be averaged into a decision as if it were.
    viable = (not flags and int(no_detour or 0) == 0)

    return Observation(
        stress_case=stress_case, seed=seed, candidate=candidate,
        added_vehicle_hours=_as_float(disruption.get("added_vehicle_hours")),
        vehicles_affected=_as_int(disruption.get("vehicles_affected")),
        vehicles_considered=_as_int(disruption.get("vehicles_considered")),
        vehicles_no_detour=_as_int(no_detour),
        health_flags=flags, teleports=teleports, collisions=collisions,
        viable=viable, hard_failure=False, failure_reason=None,
        runtime_s=runtime)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────
# deciding Gate S
# ──────────────────────────────────────────────────────────────────────────
def decide_gate_s(observations: Sequence[Observation],
                  registration: Registration) -> dict[str, Any]:
    """Compare the PREREGISTERED decision fields across the stress cases.

    The registration names hard failures, the viable set, the ranking and the
    winner, so all four are compared here — an earlier version reduced only
    the mean of a single objective, which could not answer the plan's
    question at all.

    Fail-closed: an incomplete matrix, a timeout or any run that produced no
    usable objective yields ``INCONCLUSIVE``, never ``NO``. "We could not
    measure it" and "we measured it and it does not matter" are different
    answers and must not collapse into each other.
    """
    expected = (len(registration.stress_cases) * len(registration.seeds)
                * len(registration.candidate_edges))
    if len(observations) != expected:
        return _inconclusive(
            f"incomplete matrix: {len(observations)} of {expected} "
            "observations", observations, registration)

    failures = [o for o in observations if o.hard_failure]
    if failures:
        return _inconclusive(
            f"{len(failures)} observation(s) failed to produce a result",
            observations, registration,
            failure_reasons=sorted({o.failure_reason for o in failures
                                    if o.failure_reason}))

    missing = [o for o in observations if o.added_vehicle_hours is None]
    if missing:
        return _inconclusive(
            f"{len(missing)} observation(s) carry no {registration.objective}",
            observations, registration)

    material: list[str] = []

    # ── hard-failure and viability fields, per case ──────────────────────
    viable_by_case: dict[str, set[str]] = {}
    no_detour_by_case: dict[str, dict[str, int]] = {}
    health_by_case: dict[str, list[str]] = {}
    for obs in observations:
        if obs.viable:
            viable_by_case.setdefault(obs.stress_case, set()).add(obs.candidate)
        else:
            viable_by_case.setdefault(obs.stress_case, set())
        bucket = no_detour_by_case.setdefault(obs.stress_case, {})
        bucket[obs.candidate] = max(bucket.get(obs.candidate, 0),
                                    int(obs.vehicles_no_detour or 0))
        health_by_case.setdefault(obs.stress_case, []).extend(obs.health_flags)

    viable_sets = {case: tuple(sorted(edges))
                   for case, edges in viable_by_case.items()}
    viable_identical = len(set(viable_sets.values())) == 1
    if not viable_identical:
        material.append(f"viable set differs between stress cases: {viable_sets}")

    no_detour_identical = True
    for candidate in registration.candidate_edges:
        values = {case: no_detour_by_case[case].get(candidate, 0)
                  for case in registration.stress_cases}
        if len(set(values.values())) > 1:
            no_detour_identical = False
            material.append(
                f"{candidate}: vehicles_no_detour differs between stress "
                f"cases: {values}")

    # ── objective spread, direction versus seed noise ────────────────────
    per_candidate: dict[str, Any] = {}
    for candidate in registration.candidate_edges:
        by_case: dict[str, list[float]] = {}
        for obs in observations:
            if obs.candidate == candidate:
                by_case.setdefault(obs.stress_case, []).append(
                    float(obs.added_vehicle_hours))
        case_means = {case: statistics.fmean(v) for case, v in by_case.items()}
        within = [max(v) - min(v) for v in by_case.values() if len(v) > 1]
        within_mean = statistics.fmean(within) if within else 0.0
        between = (max(case_means.values()) - min(case_means.values())
                   if len(case_means) > 1 else 0.0)
        if within_mean > 0:
            ratio: float | None = between / within_mean
        else:
            ratio = None if between > 0 else 0.0
        reference = abs(statistics.fmean(case_means.values())) or 1.0
        relative = between / reference
        exceeds = ((ratio is None and between > 0)
                   or (ratio is not None
                       and ratio >= registration.thresholds.spread_ratio)) \
            and relative >= registration.thresholds.relative_objective

        per_candidate[candidate] = {
            "case_means": {k: round(v, 6) for k, v in case_means.items()},
            "between_case_range": round(between, 6),
            "mean_within_case_seed_range": round(within_mean, 6),
            "spread_ratio": (None if ratio is None else round(ratio, 3)),
            "relative_between_case": round(relative, 4),
            "material": bool(exceeds),
        }
        if exceeds:
            material.append(
                f"{candidate}: between-case range {between:.4g} "
                f"{registration.objective} is "
                + (f"{ratio:.1f}x the seed range" if ratio is not None
                   else "nonzero while the seed range is zero")
                + f" and {relative:.1%} of the mean")

    # ── ranking and winner, over VIABLE candidates only ──────────────────
    ranking_by_case: dict[str, list[str]] = {}
    for case in registration.stress_cases:
        usable = [c for c in registration.candidate_edges
                  if c in viable_by_case.get(case, set())]
        ranking_by_case[case] = sorted(
            usable, key=lambda c: per_candidate[c]["case_means"].get(case, 0.0))
    rankings_identical = len({tuple(v) for v in ranking_by_case.values()}) == 1
    if not rankings_identical:
        material.append(
            f"candidate ranking differs between stress cases: {ranking_by_case}")

    winners = {case: (order[0] if order else None)
               for case, order in ranking_by_case.items()}
    winner_identical = len(set(winners.values())) == 1
    if not winner_identical:
        material.append(f"winner differs between stress cases: {winners}")

    return {
        "protocol": PROTOCOL,
        "gate_s": "YES" if material else "NO",
        "registration_key": registration.content_key,
        "release_evidence": False,
        "objective": registration.objective,
        "reasons": material,
        "per_candidate": per_candidate,
        "viable_set_by_case": {k: list(v) for k, v in viable_sets.items()},
        "viable_set_identical": viable_identical,
        "vehicles_no_detour_by_case": no_detour_by_case,
        "vehicles_no_detour_identical": no_detour_identical,
        "health_flags_by_case": {k: sorted(set(v))
                                 for k, v in health_by_case.items()},
        "ranking_by_case": ranking_by_case,
        "winner_by_case": winners,
        "rankings_identical": rankings_identical,
        "winner_identical": winner_identical,
        "n_observations": len(observations),
        "n_hard_failures": len(failures),
        "total_runtime_s": round(sum(o.runtime_s for o in observations), 1),
        "observations": [asdict(o) for o in observations],
    }


def _inconclusive(reason: str, observations: Sequence[Observation],
                  registration: Registration,
                  failure_reasons: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "gate_s": "INCONCLUSIVE",
        "registration_key": registration.content_key,
        "release_evidence": False,
        "reasons": [reason],
        "failure_reasons": list(failure_reasons),
        "n_observations": len(observations),
        "note": ("INCONCLUSIVE is not NO. It forbids product integration and "
                 "requires repairing measurability, then rerunning a fresh "
                 "frozen version without selecting cases from this outcome."),
        "observations": [asdict(o) for o in observations],
    }


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def build_registration(sumo_dir: Path, *, date: str, begin: str, end: str,
                       seeds: Sequence[int], count: int, timeout_s: int,
                       demand_window_begin: str = "06:00") -> Registration:
    from traffic_sim.intake.sensors import load_registry

    registry = load_registry(Path("data_in/sensors.json"))
    sensor_edges = {edge for record in registry.records.values()
                    for edge in record.approved_edge_ids}
    candidates, selection = select_candidates(sumo_dir, sensor_edges, count)

    digests = {}
    for name, filename in STRESS_CASES:
        path = sumo_dir / filename
        if path.exists():
            digests[filename] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))
    for extra in ("net.net.xml", "direction_split.json"):
        path = sumo_dir / extra
        if path.exists():
            digests[extra] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))

    return Registration(
        protocol=PROTOCOL,
        selection_rule=SELECTION_RULE,
        date=date, window_begin=begin, window_end=end,
        demand_window_begin=demand_window_begin,
        seeds=tuple(seeds),
        stress_cases=tuple(name for name, _f in STRESS_CASES),
        candidate_edges=tuple(candidates),
        candidate_selection=selection,
        timeout_s=timeout_s,
        objective="added_vehicle_hours",
        comparison_fields=("hard_failure", "seed_health_flags",
                           "vehicles_no_detour", "viable_set",
                           "candidate_ranking", "winner",
                           "added_vehicle_hours"),
        thresholds=MaterialityThresholds(),
        source_digests=digests,
        note=("q10/q50/q90 are NAMED STRESS CASES with unvalidated nominal "
              "coverage, not probability statements. This measures spread, "
              "not a distribution. Each run is pinned by its own "
              "ScenarioSpec to exactly one seed and one demand variant, "
              "because run_scenario has no CLI flag for either and its "
              "--seeds argument is a count. added_vehicle_hours is already a "
              "paired within-run difference against the same routes without "
              "the closure."),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumo-dir", type=Path, default=Path("sumo"))
    parser.add_argument("--date", default="2025-09-16")
    parser.add_argument("--begin", default="06:00")
    parser.add_argument("--end", default="10:00")
    parser.add_argument("--seeds", type=int, nargs="+",
                        default=[1000, 1001, 1002, 1003])
    parser.add_argument("--candidates", type=int, default=4)
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--demand-window-begin", default="06:00",
                        help="start of the DEMAND window the build covers; "
                             "the registered closure window is expressed as "
                             "an offset into it")
    parser.add_argument("--out-dir", type=Path,
                        default=Path("runs/dirsplit_sensitivity"))
    parser.add_argument("--registration", type=Path, default=REGISTRATION_PATH)
    parser.add_argument("--outcome", type=Path, default=OUTCOME_PATH)
    parser.add_argument("--freeze-only", action="store_true",
                        help="write the registration and stop, so selection "
                             "is provably frozen before execution")
    args = parser.parse_args(argv)

    registration = build_registration(
        args.sumo_dir, date=args.date, begin=args.begin, end=args.end,
        seeds=args.seeds, count=args.candidates, timeout_s=args.timeout_s,
        demand_window_begin=args.demand_window_begin)

    payload = registration.to_json()
    payload["content_key"] = registration.content_key
    if args.registration.exists():
        existing = json.loads(args.registration.read_text())
        if existing.get("content_key") != registration.content_key:
            raise SystemExit(
                f"{args.registration} already exists with a DIFFERENT key. "
                "A registration is frozen evidence: publish a new version "
                "rather than editing a recorded one.")
        print(f"registration already frozen: {registration.content_key}")
    else:
        args.registration.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(args.registration,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(handle, "w") as file:
            json.dump(payload, file, indent=1, sort_keys=True)
            file.write("\n")
        print(f"froze registration {registration.content_key}")

    print(f"  candidates: {list(registration.candidate_edges)}")
    print(f"  seeds     : {list(registration.seeds)}")
    print(f"  cases     : {list(registration.stress_cases)}")
    if args.freeze_only:
        return 0

    observations = run_matrix(registration, args.sumo_dir, args.out_dir)
    outcome = decide_gate_s(observations, registration)

    args.outcome.parent.mkdir(parents=True, exist_ok=True)
    if args.outcome.exists():
        raise SystemExit(f"{args.outcome} already exists; outcomes are "
                         "append-only")
    handle = os.open(args.outcome, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(handle, "w") as file:
        json.dump(outcome, file, indent=1, sort_keys=True)
        file.write("\n")

    print(f"\nGate S = {outcome['gate_s']}")
    for reason in outcome.get("reasons", []):
        print(f"  {reason}")
    print(f"Wrote {args.outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
