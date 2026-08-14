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
    stress_case: str
    seed: int
    candidate: str
    added_time_loss_s: float | None
    hard_failure: bool
    failure_reason: str | None
    runtime_s: float


def run_matrix(registration: Registration, sumo_dir: Path,
               out_dir: Path) -> list[Observation]:
    """Run every (stress case, seed, candidate) plus its matched baseline."""
    observations: list[Observation] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for case_name, filename in STRESS_CASES:
        if case_name not in registration.stress_cases:
            continue
        route_path = sumo_dir / filename
        for seed in registration.seeds:
            baseline = _run_one(route_path, [], seed, registration, out_dir,
                                f"base_{case_name}_{seed}")
            for candidate in registration.candidate_edges:
                result = _run_one(route_path, [candidate], seed, registration,
                                  out_dir, f"cand_{case_name}_{seed}_"
                                           f"{candidate}")
                added = None
                if (baseline["time_loss"] is not None
                        and result["time_loss"] is not None):
                    added = result["time_loss"] - baseline["time_loss"]
                observations.append(Observation(
                    stress_case=case_name, seed=seed, candidate=candidate,
                    added_time_loss_s=added,
                    hard_failure=result["hard_failure"],
                    failure_reason=result["reason"],
                    runtime_s=result["runtime_s"],
                ))
    return observations


def _run_one(route_path: Path, closures: Sequence[str], seed: int,
             registration: Registration, out_dir: Path,
             tag: str) -> dict[str, Any]:
    """One SUMO run through the existing runner, with a fixed timeout."""
    command = [
        sys.executable, str(ROOT / "run_scenario.py"),
        "--seeds", "1",
        "--no-trajectories",
        "--out-dir", str(out_dir / tag),
        "--name", tag,
    ]
    if closures:
        command += ["--close", *closures]

    started = time.time()
    env = dict(os.environ)
    env.setdefault("DIRSPLIT_SENSITIVITY_SEED", str(seed))
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=registration.timeout_s, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"time_loss": None, "hard_failure": True,
                "reason": f"timeout after {registration.timeout_s}s",
                "runtime_s": time.time() - started}
    runtime = time.time() - started
    if completed.returncode != 0:
        return {"time_loss": None, "hard_failure": True,
                "reason": f"exit {completed.returncode}: "
                          f"{completed.stderr.strip()[-300:]}",
                "runtime_s": runtime}

    scenario = out_dir / tag / f"{tag}.json"
    if not scenario.exists():
        candidates = sorted((out_dir / tag).glob("*.json"))
        scenario = candidates[0] if candidates else None
    if scenario is None:
        return {"time_loss": None, "hard_failure": True,
                "reason": "no scenario artifact produced",
                "runtime_s": runtime}
    payload = json.loads(scenario.read_text())
    disruption = payload.get("disruption") or {}
    return {
        "time_loss": disruption.get("total_time_loss_s"),
        "hard_failure": False,
        "reason": None,
        "runtime_s": runtime,
    }


# ──────────────────────────────────────────────────────────────────────────
# deciding Gate S
# ──────────────────────────────────────────────────────────────────────────
def decide_gate_s(observations: Sequence[Observation],
                  registration: Registration) -> dict[str, Any]:
    """Compare between-case spread against within-case seed spread.

    Fail-closed: missing observations, timeouts or a matrix that is not
    complete produce ``INCONCLUSIVE``, never ``NO``. "We could not measure
    it" and "we measured it and it does not matter" are different answers and
    must not be collapsed.
    """
    expected = (len(registration.stress_cases) * len(registration.seeds)
                * len(registration.candidate_edges))
    if len(observations) != expected:
        return _inconclusive(
            f"incomplete matrix: {len(observations)} of {expected} "
            "observations", observations, registration)

    failures = [o for o in observations if o.hard_failure]
    usable = [o for o in observations if not o.hard_failure
              and o.added_time_loss_s is not None]
    if len(usable) < expected:
        return _inconclusive(
            f"{expected - len(usable)} observation(s) failed or produced no "
            "objective", observations, registration,
            failure_reasons=sorted({o.failure_reason for o in failures
                                    if o.failure_reason}))

    per_candidate: dict[str, Any] = {}
    material_reasons: list[str] = []

    for candidate in registration.candidate_edges:
        by_case: dict[str, list[float]] = {}
        for obs in usable:
            if obs.candidate == candidate:
                by_case.setdefault(obs.stress_case, []).append(
                    float(obs.added_time_loss_s))
        case_means = {case: statistics.fmean(values)
                      for case, values in by_case.items()}
        within_ranges = [max(values) - min(values)
                         for values in by_case.values() if len(values) > 1]
        within = statistics.fmean(within_ranges) if within_ranges else 0.0
        between = (max(case_means.values()) - min(case_means.values())
                   if len(case_means) > 1 else 0.0)
        ratio = (between / within) if within > 0 else float("inf") \
            if between > 0 else 0.0
        reference = abs(statistics.fmean(case_means.values())) or 1.0
        relative = between / reference

        exceeds = (ratio >= registration.thresholds.spread_ratio
                   and relative >= registration.thresholds.relative_objective)
        per_candidate[candidate] = {
            "case_means": {k: round(v, 3) for k, v in case_means.items()},
            "between_case_range": round(between, 3),
            "mean_within_case_seed_range": round(within, 3),
            "spread_ratio": (None if ratio == float("inf") else round(ratio, 3)),
            "relative_between_case": round(relative, 4),
            "material": bool(exceeds),
        }
        if exceeds:
            material_reasons.append(
                f"{candidate}: between-case range {between:.1f}s is "
                f"{ratio:.1f}x the seed range and {relative:.1%} of the mean")

    ranking_by_case = {}
    for case in registration.stress_cases:
        means = {c: per_candidate[c]["case_means"].get(case)
                 for c in registration.candidate_edges}
        ranking_by_case[case] = [c for c, _v in
                                 sorted(means.items(), key=lambda kv: kv[1])]
    rankings_identical = len({tuple(v) for v in ranking_by_case.values()}) == 1
    if not rankings_identical:
        material_reasons.append(
            f"candidate ranking differs between stress cases: "
            f"{ranking_by_case}")

    winners = {case: order[0] for case, order in ranking_by_case.items()}
    winner_identical = len(set(winners.values())) == 1
    if not winner_identical:
        material_reasons.append(f"winner differs between stress cases: {winners}")

    gate = "YES" if material_reasons else "NO"
    return {
        "protocol": PROTOCOL,
        "gate_s": gate,
        "registration_key": registration.content_key,
        "release_evidence": False,
        "reasons": material_reasons,
        "per_candidate": per_candidate,
        "ranking_by_case": ranking_by_case,
        "winner_by_case": winners,
        "rankings_identical": rankings_identical,
        "winner_identical": winner_identical,
        "n_observations": len(observations),
        "n_usable": len(usable),
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
                       seeds: Sequence[int], count: int,
                       timeout_s: int) -> Registration:
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
        seeds=tuple(seeds),
        stress_cases=tuple(name for name, _f in STRESS_CASES),
        candidate_edges=tuple(candidates),
        candidate_selection=selection,
        timeout_s=timeout_s,
        objective="added_total_time_loss_s",
        comparison_fields=("hard_failure", "viable_set", "candidate_ranking",
                           "winner", "added_total_time_loss_s"),
        thresholds=MaterialityThresholds(),
        source_digests=digests,
        note=("q10/q50/q90 are NAMED STRESS CASES with unvalidated nominal "
              "coverage, not probability statements. This measures spread, "
              "not a distribution. Matched seeds separate the direction "
              "effect from simulator noise."),
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
