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
  ``(stress case, seed)`` pair runs the baseline and the candidate. Each pair
  is bound through a per-run ``ScenarioSpec`` with a ONE-SEED ``seed_set`` and
  an explicit ``demand_variant_mapping``, which is the existing contract's own
  way of naming (demand case, seed) orthogonally. Nothing about
  ``ScenarioSpec``, monthly, warm-state, the API or the UI is changed.
* The decision is read from the REAL closure policy — the same
  ``closure_ranking.worst_variant_cost``/``rank_closures`` the product uses —
  so Gate S answers the plan's question ("does the viable set, the ranking or
  the winner change?") rather than a proxy of it.

REPAIRED 2026-08-14 after review. The first cut of this tool was
non-functional as evidence in four separate ways, all of which would have
produced a confident but empty answer:

1. ``_run_one`` built its command without the route file and without the
   seed, so every q case and every seed ran the SAME simulation. It compared
   a thing with itself. The ``DIRSPLIT_SENSITIVITY_SEED`` environment
   variable it set is read by nothing.
2. It read ``disruption["total_time_loss_s"]``, a field the product does not
   produce. Every observation would have been ``None`` and Gate S would have
   returned INCONCLUSIVE for a reason that had nothing to do with direction.
3. The registered 06:00-10:00 window was recorded but never applied; the
   closure ran for the whole demand window.
4. The reducer checked only process failures and the mean of one objective,
   not the preregistered decision fields (SUMO health, closure integrity,
   no-detour, viable set, ranking, winner).

One structural fact drives the repaired materiality rule, and it is recorded
here because it is easy to get wrong: the deployed ranking key comes from
``run_scenario.closure_disruption``, which reads the calibrated route file and
free-flow costs. It is DEMAND-SIDE and seed-independent BY CONSTRUCTION. So
the within-case seed range on that key is structurally zero, and a
"between-case spread beats seed noise" ratio on it would be a tautology. The
tool therefore:

* VERIFIES the invariant per case rather than assuming it (a case whose
  ranking key moves across seeds is a finding, not noise to average);
* uses the seed axis for what it actually governs — simulator health,
  closure integrity and inserted-vehicle counts — and requires the seeds to
  have demonstrably differed before any answer is published.

This tool deliberately does not touch `ScenarioSpec`, the monthly path, the
warm-state cache, the API or the UI. It calls the existing runner and reduces
its output with the existing policy. Its artifacts carry
``release_evidence: false``.
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
from dataclasses import asdict, dataclass
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
PROTOCOL = "dirsplit_direction_decision_sensitivity_v2"

REGISTRATION_PATH = Path(
    "validation/dirsplit_direction_sensitivity_registration_v2.json")
OUTCOME_PATH = Path(
    "validation/dirsplit_direction_sensitivity_outcome_v2.json")

#: Route artifacts written by build_sumo_demand, in a fixed order, each with
#: the ScenarioSpec demand-variant name that selects it. The name is what
#: binds a stress case to a route file inside run_scenario, so the two can
#: never drift apart the way a bare filename could.
STRESS_CASES: tuple[tuple[str, str, str], ...] = (
    ("q50", "calibrated.rou.xml", "q50"),
    ("q10", "calibrated_v1.rou.xml", "q10"),
    ("q90", "calibrated_v2.rou.xml", "q90"),
)

#: The fields the deployed closure policy ranks on. Read straight from the
#: product's own ``disruption`` record — see closure_ranking.REQUIRED_FIELDS.
POLICY_FIELDS = ("added_vehicle_hours", "added_metres_total",
                 "vehicles_affected", "vehicles_no_detour")


# ──────────────────────────────────────────────────────────────────────────
# frozen materiality thresholds
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MaterialityThresholds:
    """What counts as a decision difference. Frozen before execution.

    ``relative_objective`` is the load-bearing one for the deployed ranking
    key, because that key is seed-deterministic (see the module docstring):
    there is no simulator noise to clear, so the question is whether the
    between-case difference is large relative to the decision itself.

    ``spread_ratio`` still governs the SIMULATOR-side quantity, where seed
    noise is real: a between-case difference in inserted vehicles smaller
    than the seed-to-seed range is not a direction effect.
    """

    spread_ratio: float = 2.0
    relative_objective: float = 0.10
    require_identical_viable_set: bool = True
    require_identical_ranking: bool = True
    require_identical_winner: bool = True
    require_seed_axis_to_vary: bool = True
    require_clean_closure_integrity: bool = True
    require_healthy_seeds: bool = True


@dataclass(frozen=True)
class Registration:
    protocol: str
    selection_rule: str
    date: str
    window_begin: str
    window_end: str
    closure_begin: str
    closure_end: str
    seeds: tuple[int, ...]
    stress_cases: tuple[str, ...]
    candidate_edges: tuple[str, ...]
    candidate_selection: Mapping[str, Any]
    timeout_s: int
    objective: str
    comparison_fields: tuple[str, ...]
    thresholds: MaterialityThresholds
    source_digests: Mapping[str, str]
    demand_build_id: str = ""
    network_build_id: str = ""
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
    for name, filename, _variant in STRESS_CASES:
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
    """One (stress case, seed, candidate) run, with everything the gate reads.

    ``policy`` carries the deployed ranking fields verbatim, so the reducer
    can hand them straight to ``closure_ranking`` rather than re-deriving a
    private objective that the product would never use.
    """

    stress_case: str
    seed: int
    candidate: str
    policy: dict[str, float] | None
    closure_integrity: str | None
    seed_health_flags: tuple[str, ...]
    vehicles_inserted: int | None
    hard_failure: bool
    failure_reason: str | None
    runtime_s: float

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["seed_health_flags"] = list(self.seed_health_flags)
        return payload


def demand_identity(sumo_dir: Path) -> tuple[str, str]:
    """(demand_build_id, network_build_id) exactly as run_scenario computes.

    Reusing run_scenario's own helpers rather than re-implementing them is
    what keeps a spec this tool writes acceptable to the runner: a private
    copy of the signature would drift the first time the demand contract
    changed.
    """
    import run_scenario as rs

    with open(sumo_dir / "demand_meta.json") as handle:
        meta = json.load(handle)
    network_id = rs.sha256_file(sumo_dir / "net.net.xml")
    if network_id is None:
        raise FileNotFoundError(f"network artifact missing: {sumo_dir}/net.net.xml")
    return str(meta.get("build_id") or rs.demand_signature(meta)), network_id


def demand_window(sumo_dir: Path) -> tuple[str, str]:
    """(start_time, end_time) of the loaded demand build, ISO, as the spec
    validator requires them: exactly epoch_sim and epoch_sim + duration."""
    import pandas as pd

    with open(sumo_dir / "demand_meta.json") as handle:
        meta = json.load(handle)
    start = pd.Timestamp(meta["epoch_sim"])
    end = start + pd.Timedelta(seconds=int(meta["n_intervals"]) * 900)
    return start.isoformat(), end.isoformat()


def closure_window(sumo_dir: Path, begin: str, end: str) -> tuple[str, str]:
    """Resolve the registered HH:MM closure window onto the demand calendar.

    The registered window is a real constraint, not a label: a closure that
    silently ran for the whole demand day would measure a different scenario
    from the one that was frozen. Clamped into the demand window so the spec
    stays valid; the clamp is visible in the registration because both the
    requested and the resolved window are recorded.
    """
    import pandas as pd

    start_iso, end_iso = demand_window(sumo_dir)
    start, finish = pd.Timestamp(start_iso), pd.Timestamp(end_iso)
    day = start.normalize()
    lo = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {begin}")
    hi = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {end}")
    lo = max(lo, start)
    hi = min(hi, finish)
    if hi <= lo:                      # window lies outside this demand build
        lo, hi = start, finish
    return lo.isoformat(), hi.isoformat()


def build_spec_payload(registration: Registration, *, case_variant: str,
                       seed: int, closures: Sequence[str],
                       scenario_id: str, start_time: str,
                       end_time: str) -> dict[str, Any]:
    """One ScenarioSpec binding exactly one demand case to exactly one seed.

    ``seed_set`` has a single member, so ``run_scenario``'s three-variant
    coverage requirement (which applies from three seeds up) does not fire —
    that requirement exists so a PUBLISHED release spans the direction
    interval, which is the opposite of what this diagnostic wants: here each
    arm must be a single, named case.
    """
    return {
        "scenario_id": scenario_id,
        "demand_build_id": registration.demand_build_id,
        "network_build_id": registration.network_build_id,
        "start_time": start_time,
        "end_time": end_time,
        "closures": [
            {"edge_id": edge, "start_time": registration.closure_begin,
             "end_time": registration.closure_end, "closure_type": "full"}
            for edge in closures
        ],
        "simulation_mode": "meso",
        "seed_set": [seed],
        "demand_variant_mapping": {str(seed): case_variant},
    }


def run_matrix(registration: Registration, sumo_dir: Path,
               out_dir: Path) -> list[Observation]:
    """Run every (stress case, seed, candidate) plus its matched baseline."""
    observations: list[Observation] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    start_time, end_time = demand_window(sumo_dir)

    for case_name, _filename, variant in STRESS_CASES:
        if case_name not in registration.stress_cases:
            continue
        for seed in registration.seeds:
            for candidate in registration.candidate_edges:
                tag = f"cand_{case_name}_{seed}_{candidate}"
                spec_payload = build_spec_payload(
                    registration, case_variant=variant, seed=seed,
                    closures=[candidate], scenario_id=tag,
                    start_time=start_time, end_time=end_time)
                result = _run_one(spec_payload, registration, out_dir, tag)
                observations.append(Observation(
                    stress_case=case_name, seed=seed, candidate=candidate,
                    policy=result["policy"],
                    closure_integrity=result["closure_integrity"],
                    seed_health_flags=tuple(result["seed_health_flags"]),
                    vehicles_inserted=result["vehicles_inserted"],
                    hard_failure=result["hard_failure"],
                    failure_reason=result["reason"],
                    runtime_s=result["runtime_s"],
                ))
    return observations


def _run_one(spec_payload: Mapping[str, Any], registration: Registration,
             out_dir: Path, tag: str) -> dict[str, Any]:
    """One SUMO run through the existing runner, with a fixed timeout.

    The (demand case, seed) pair travels in a ScenarioSpec file rather than
    in ad-hoc flags. That is what makes the arms genuinely different runs:
    ``run_scenario`` reads ``seed_set`` and ``demand_variant_mapping`` from
    the spec and resolves the variant to its route file itself.
    """
    run_dir = out_dir / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.json"
    spec_path.write_text(json.dumps(spec_payload, indent=1, sort_keys=True))

    command = [
        sys.executable, str(ROOT / "run_scenario.py"),
        "--scenario-spec", str(spec_path),
        "--no-trajectories",
        "--out-dir", str(run_dir),
    ]

    started = time.time()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=registration.timeout_s,
        )
    except subprocess.TimeoutExpired:
        return _failed(f"timeout after {registration.timeout_s}s",
                       time.time() - started)
    runtime = time.time() - started
    if completed.returncode != 0:
        return _failed(f"exit {completed.returncode}: "
                       f"{completed.stderr.strip()[-300:]}", runtime)

    scenario = run_dir / f"{tag}.json"
    if not scenario.exists():
        found = sorted(p for p in run_dir.glob("*.json")
                       if p.name not in {"spec.json", "index.json"})
        scenario = found[0] if found else None
    if scenario is None:
        return _failed("no scenario artifact produced", runtime)

    payload = json.loads(scenario.read_text())
    disruption = payload.get("disruption") or {}
    missing = [name for name in POLICY_FIELDS if name not in disruption]
    if missing:
        return _failed(
            f"scenario artifact carries no {', '.join(missing)} in disruption",
            runtime)

    health = [record for record in (payload.get("seed_health") or [])
              if isinstance(record, dict)]
    inserted = None
    if health:
        values = [record.get("inserted") for record in health
                  if record.get("inserted") is not None]
        inserted = int(sum(values)) if values else None

    return {
        "policy": {name: float(disruption[name]) for name in POLICY_FIELDS},
        "closure_integrity": (payload.get("scenario") or {}).get(
            "closure_integrity"),
        "seed_health_flags": list(payload.get("seed_health_flags") or []),
        "vehicles_inserted": inserted,
        "hard_failure": False,
        "reason": None,
        "runtime_s": runtime,
    }


def _failed(reason: str, runtime: float) -> dict[str, Any]:
    return {"policy": None, "closure_integrity": None,
            "seed_health_flags": [], "vehicles_inserted": None,
            "hard_failure": True, "reason": reason, "runtime_s": runtime}


# ──────────────────────────────────────────────────────────────────────────
# deciding Gate S
# ──────────────────────────────────────────────────────────────────────────
def policy_decision_for_case(observations: Sequence[Observation],
                             case: str) -> dict[str, Any]:
    """Run the REAL closure policy over one stress case's candidates.

    Uses ``closure_ranking`` unchanged: the same worst-across-variants
    reduction, the same no-detour disqualifier, the same lexicographic sort.
    Anything else would answer a question the product never asks.
    """
    from traffic_sim.simulation.closure_ranking import (rank_closures,
                                                        worst_variant_cost)

    by_candidate: dict[str, list[dict[str, float]]] = {}
    for obs in observations:
        if obs.stress_case == case and obs.policy is not None:
            by_candidate.setdefault(obs.candidate, []).append(obs.policy)

    costs = [worst_variant_cost(candidate, records)
             for candidate, records in sorted(by_candidate.items())]
    viable, refused = rank_closures(costs)
    ranking = [cost.candidate_id for cost in viable]
    return {
        "viable_set": sorted(cost.candidate_id for cost in viable),
        "ranking": ranking,
        "winner": ranking[0] if ranking else None,
        "disqualified": sorted(cost.candidate_id for cost in refused),
        "added_vehicle_hours": {cost.candidate_id: cost.added_vehicle_hours
                                for cost in viable + refused},
    }


def seed_invariance(observations: Sequence[Observation]) -> dict[str, Any]:
    """Check, don't assume, that the ranking key is seed-independent.

    ``closure_disruption`` reads the calibrated route file and free-flow
    costs, so for a fixed (case, candidate) the policy fields must be
    identical at every seed. Verifying it is what makes the between-case
    comparison meaningful: if the key DID move across seeds, the deployed
    ranking would carry Monte Carlo noise it does not declare, and that is a
    finding rather than a nuisance to average away.
    """
    grouped: dict[tuple[str, str], list[dict[str, float]]] = {}
    for obs in observations:
        if obs.policy is not None:
            grouped.setdefault((obs.stress_case, obs.candidate),
                               []).append(obs.policy)
    violations = []
    for (case, candidate), records in sorted(grouped.items()):
        for name in POLICY_FIELDS:
            values = {round(float(record[name]), 6) for record in records}
            if len(values) > 1:
                violations.append(
                    f"{case}/{candidate}: {name} varies across seeds "
                    f"{sorted(values)}")
    return {
        "ranking_key_is_seed_deterministic": not violations,
        "violations": violations,
        "basis": ("closure_disruption is demand-side and congestion-"
                  "independent, so this is an invariant of the deployed "
                  "policy, verified here rather than assumed"),
    }


def seed_axis_varied(observations: Sequence[Observation]) -> dict[str, Any]:
    """Did the seeds actually produce different simulations?

    This is the guard against the exact defect this tool was repaired for: a
    matrix that runs the same simulation under different labels answers
    nothing. Inserted-vehicle counts are simulator-side and seed-sensitive,
    so a matrix where they never move means the seed axis was inert.
    """
    per_case: dict[str, set[int]] = {}
    for obs in observations:
        if obs.vehicles_inserted is not None:
            per_case.setdefault(obs.stress_case, set()).add(
                int(obs.vehicles_inserted))
    measurable = {case: sorted(values) for case, values in per_case.items()}
    varied = any(len(values) > 1 for values in measurable.values())
    return {
        "varied": bool(varied),
        "distinct_inserted_by_case": measurable,
        "note": ("a matrix whose seeds produce identical simulator output "
                 "has no seed axis, whatever its labels say"),
    }


def decide_gate_s(observations: Sequence[Observation],
                  registration: Registration) -> dict[str, Any]:
    """Decide Gate S on the preregistered decision fields.

    Fail-closed: missing observations, timeouts, unclean closure integrity,
    unhealthy seeds or an inert seed axis produce ``INCONCLUSIVE``, never
    ``NO``. "We could not measure it" and "we measured it and it does not
    matter" are different answers and must not be collapsed.
    """
    expected = (len(registration.stress_cases) * len(registration.seeds)
                * len(registration.candidate_edges))
    if len(observations) != expected:
        return _inconclusive(
            f"incomplete matrix: {len(observations)} of {expected} "
            "observations", observations, registration)

    failures = [o for o in observations if o.hard_failure]
    usable = [o for o in observations
              if not o.hard_failure and o.policy is not None]
    if len(usable) < expected:
        return _inconclusive(
            f"{expected - len(usable)} observation(s) failed or produced no "
            "policy record", observations, registration,
            failure_reasons=sorted({o.failure_reason for o in failures
                                    if o.failure_reason}))

    thresholds = registration.thresholds

    if thresholds.require_clean_closure_integrity:
        unclean = sorted({f"{o.stress_case}/{o.seed}/{o.candidate}="
                          f"{o.closure_integrity}" for o in usable
                          if o.closure_integrity != "verified_clean"})
        if unclean:
            return _inconclusive(
                "closure integrity is not verified_clean in "
                f"{len(unclean)} run(s): {unclean[:5]}",
                observations, registration)

    if thresholds.require_healthy_seeds:
        unhealthy = sorted({f"{o.stress_case}/{o.seed}/{o.candidate}: "
                            f"{'; '.join(o.seed_health_flags)}"
                            for o in usable if o.seed_health_flags})
        if unhealthy:
            return _inconclusive(
                f"SUMO health flags raised in {len(unhealthy)} run(s): "
                f"{unhealthy[:5]}", observations, registration)

    axis = seed_axis_varied(usable)
    if thresholds.require_seed_axis_to_vary and not axis["varied"]:
        return _inconclusive(
            "the seed axis did not vary: every seed produced identical "
            "simulator output, so no matched-seed comparison was actually "
            "performed", observations, registration, seed_axis=axis)

    invariance = seed_invariance(usable)
    material_reasons: list[str] = []
    if not invariance["ranking_key_is_seed_deterministic"]:
        material_reasons.append(
            "the deployed ranking key is NOT seed-deterministic: "
            + "; ".join(invariance["violations"][:3]))

    # ── the real policy, applied once per stress case ────────────────────
    decision_by_case = {case: policy_decision_for_case(usable, case)
                        for case in registration.stress_cases}

    viable_sets = {tuple(d["viable_set"]) for d in decision_by_case.values()}
    if thresholds.require_identical_viable_set and len(viable_sets) > 1:
        material_reasons.append(
            "the viable set differs between stress cases: "
            + json.dumps({c: d["viable_set"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    rankings = {tuple(d["ranking"]) for d in decision_by_case.values()}
    rankings_identical = len(rankings) == 1
    if thresholds.require_identical_ranking and not rankings_identical:
        material_reasons.append(
            "the candidate ranking differs between stress cases: "
            + json.dumps({c: d["ranking"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    winners = {case: d["winner"] for case, d in decision_by_case.items()}
    winner_identical = len(set(winners.values())) == 1
    if thresholds.require_identical_winner and not winner_identical:
        material_reasons.append(
            f"the winner differs between stress cases: {winners}")

    disqualified = {tuple(d["disqualified"]) for d in decision_by_case.values()}
    if len(disqualified) > 1:
        material_reasons.append(
            "no-detour disqualification differs between stress cases: "
            + json.dumps({c: d["disqualified"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    # ── per-candidate spread on the deployed ranking key ─────────────────
    per_candidate: dict[str, Any] = {}
    for candidate in registration.candidate_edges:
        case_values = {
            case: decision_by_case[case]["added_vehicle_hours"].get(candidate)
            for case in registration.stress_cases
        }
        present = [value for value in case_values.values() if value is not None]
        if not present:
            continue
        between = max(present) - min(present)
        reference = abs(statistics.fmean(present)) or 1.0
        relative = between / reference
        exceeds = relative >= thresholds.relative_objective
        per_candidate[candidate] = {
            "added_vehicle_hours_by_case": {
                case: (None if value is None else round(value, 4))
                for case, value in case_values.items()},
            "between_case_range": round(between, 4),
            "relative_between_case": round(relative, 4),
            "seed_range_within_case": 0.0,
            "material": bool(exceeds),
        }
        if exceeds:
            material_reasons.append(
                f"{candidate}: added_vehicle_hours spans {between:.4f} h "
                f"across stress cases, {relative:.1%} of its own mean")

    gate = "YES" if material_reasons else "NO"
    return {
        "protocol": PROTOCOL,
        "gate_s": gate,
        "registration_key": registration.content_key,
        "release_evidence": False,
        "reasons": material_reasons,
        "decision_by_case": decision_by_case,
        "per_candidate": per_candidate,
        "rankings_identical": rankings_identical,
        "winner_identical": winner_identical,
        "winner_by_case": winners,
        "seed_axis": axis,
        "seed_invariance": invariance,
        "policy": "traffic_sim.simulation.closure_ranking (deployed, unchanged)",
        "n_observations": len(observations),
        "n_usable": len(usable),
        "n_hard_failures": len(failures),
        "total_runtime_s": round(sum(o.runtime_s for o in observations), 1),
        "observations": [o.to_json() for o in observations],
    }


def _inconclusive(reason: str, observations: Sequence[Observation],
                  registration: Registration,
                  failure_reasons: Sequence[str] = (),
                  seed_axis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = {
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
        "observations": [o.to_json() for o in observations],
    }
    if seed_axis is not None:
        payload["seed_axis"] = dict(seed_axis)
    return payload


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def build_registration(sumo_dir: Path, *, date: str, begin: str, end: str,
                       seeds: Sequence[int], count: int,
                       timeout_s: int) -> Registration:
    from traffic_sim.intake.sensors import load_registry

    registry = load_registry(Path("data_in/sensors.json"))
    sensor_edges = {edge for record in registry.records.values()
                    for edge in record.approved_edge_ids}
    candidates, selection = select_candidates(sumo_dir, sensor_edges, count)
    demand_id, network_id = demand_identity(sumo_dir)
    closure_begin, closure_end = closure_window(sumo_dir, begin, end)

    digests = {}
    for _name, filename, _variant in STRESS_CASES:
        path = sumo_dir / filename
        if path.exists():
            digests[filename] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))
    for extra in ("net.net.xml", "direction_split.json", "demand_meta.json"):
        path = sumo_dir / extra
        if path.exists():
            digests[extra] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))

    return Registration(
        protocol=PROTOCOL,
        selection_rule=SELECTION_RULE,
        date=date, window_begin=begin, window_end=end,
        closure_begin=closure_begin, closure_end=closure_end,
        seeds=tuple(seeds),
        stress_cases=tuple(name for name, _f, _v in STRESS_CASES),
        candidate_edges=tuple(candidates),
        candidate_selection=selection,
        timeout_s=timeout_s,
        objective="added_vehicle_hours (deployed closure_ranking key)",
        comparison_fields=("hard_failure", "closure_integrity",
                           "seed_health_flags", "vehicles_no_detour",
                           "viable_set", "candidate_ranking", "winner",
                           "added_vehicle_hours"),
        thresholds=MaterialityThresholds(),
        source_digests=digests,
        demand_build_id=demand_id,
        network_build_id=network_id,
        note=("q10/q50/q90 are NAMED STRESS CASES with unvalidated nominal "
              "coverage, not probability statements. This measures spread, "
              "not a distribution. Each (case, seed) pair is bound through "
              "its own one-seed ScenarioSpec, and the decision is read from "
              "the deployed closure_ranking policy. The ranking key is "
              "demand-side and therefore seed-deterministic by construction; "
              "that invariant is verified per run, and the seed axis is used "
              "for simulator health, closure integrity and the inertness "
              "check."),
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
        seeds=args.seeds, count=args.candidates, timeout_s=args.timeout_s)

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
    print(f"  closure   : {registration.closure_begin} → "
          f"{registration.closure_end}")
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
