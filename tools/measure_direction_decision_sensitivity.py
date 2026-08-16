"""Fas 0B of the direction plan: does direction variation change a closure
DECISION, once the simulator's own randomness is held apart from it?

Today the two axes are entangled. ``run_scenario.seed_variant_plan`` maps seed
1000 to q50, 1001 to q10 and 1002 to q90, so a three-seed study varies demand
case and random seed together and cannot say which moved the answer; the
published ``disruption`` block then takes a FIELD-WISE worst across variants,
a vector that occurred in no single world.  This tool measures the remaining
confounding directly, on a frozen matrix, before any product code is changed:

    for every demand stress case  x  every seed  x  {baseline, candidate}

with the SAME seed list under every stress case and the SAME (case, seed) pair
for a candidate and its baseline — common random numbers, as FHWA's
alternatives-analysis guidance and the traffic-simulation CRN literature both
recommend.

What it is NOT
--------------
This is a bounded diagnostic.  It writes an append-only registration/outcome
pair marked ``release_evidence: false``, changes no ScenarioSpec, monthly,
warm-state, API or UI contract, and cannot promote anything.  Gate S has three
honest outcomes and ``INCONCLUSIVE`` is one of them.

Usage
-----
    python3 -m tools.measure_direction_decision_sensitivity preregister
    python3 -m tools.measure_direction_decision_sensitivity run
    python3 -m tools.measure_direction_decision_sensitivity classify \\
        --outcome validation/direction_decision_sensitivity_outcome_v1.json

``preregister`` freezes candidates, seeds, stress cases, window, tolerances and
the decision rule BEFORE any SUMO run, and refuses to overwrite an existing
registration with different content.  ``run`` executes that frozen matrix and
appends one outcome record.  ``classify`` re-derives the verdict from a stored
outcome without touching the simulator.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REGISTRATION_PATH = ROOT / "validation" / \
    "direction_decision_sensitivity_registration_v1.json"
OUTCOME_PATH = ROOT / "validation" / \
    "direction_decision_sensitivity_outcome_v1.json"

SCHEMA_VERSION = "direction_decision_sensitivity_v1"
GATE_NO, GATE_YES, GATE_INCONCLUSIVE = "NO", "YES", "INCONCLUSIVE"

#: Stress cases are the EXISTING q artifacts, used under their honest name.
#: They carry no probability weight; see the plan's point 7.
STRESS_CASES = ("q50", "q10", "q90")


# ── the frozen matrix ────────────────────────────────────────────────────────

def default_registration(created: str) -> dict:
    """The preregistered study.  Every field here is chosen outcome-blind.

    Candidates are the six measured sensor edges' own streets, picked from the
    validated sensor registry rather than from any earlier closure result:
    they are the streets that certainly carry calibrated baseline traffic
    under the sensor-crossing baseline rule, so a closure there has something
    to divert.  One of them (Skanegatan northbound) is already known to sever
    a small pocket, which keeps the hard-failure axis represented.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "study_id": "direction_decision_sensitivity_v1",
        "question": ("Does plausible direction-split variation change the "
                     "closure decision — viable set, shortlist, winner — when "
                     "the SUMO seed is held matched across stress cases?"),
        "created": created,
        "release_evidence": False,
        "demand": {
            "date": "2025-09-16",
            "source": "historical",
            "window_start": "07:00",
            "window_end": "09:00",
            "window_rationale": ("AM peak: the transfer model claims its "
                                 "largest directional asymmetry there, so it "
                                 "is where direction variation has the best "
                                 "chance of mattering."),
        },
        "stress_cases": list(STRESS_CASES),
        "stress_case_semantics": ("Named stress cases from the existing "
                                  "q10/q50/q90 demand artifacts. No "
                                  "probability weight is asserted or implied."),
        "seeds": [1000, 1001, 1002, 1003],
        "seed_rationale": ("Four repetitions per condition, following FHWA's "
                           "Traffic Analysis Toolbox guidance; the same list "
                           "runs under every stress case."),
        "candidates": [
            {"candidate_id": "skanegatan_n_107",
             "edges": ["60786979_3575001205_0"], "street": "Skanegatan"},
            {"candidate_id": "skanegatan_s_107",
             "edges": ["1455801464_18241874_0"], "street": "Skanegatan"},
            {"candidate_id": "valhallagatan_v_1074",
             "edges": ["60790252_60790253_0"], "street": "Valhallagatan"},
            {"candidate_id": "gibraltargatan_so_134",
             "edges": ["26355153_96523321_0"], "street": "Gibraltargatan"},
        ],
        "comparison": {
            "primary_cost": "delta_time_loss_s",
            "primary_cost_basis": ("candidate minus baseline total time loss, "
                                   "matched per (stress case, seed)"),
            "decision_fields": ["viable_set", "shortlist", "winner"],
            "shortlist_size": 2,
        },
        "tolerances": {
            "winner_margin_pct": 5.0,
            "material_rank_correlation": 0.9,
            "material_regret_pct": 10.0,
            "min_pairs_per_cell": 4,
        },
        "budget": {"timeout_s": 7200, "simulation_mode": "meso"},
        "decision_rule": {
            "NO": ("hard failures, viable set, shortlist and winner (or tie) "
                   "are identical across all stress cases within tolerance"),
            "YES": "at least one preregistered decision field differs materially",
            "INCONCLUSIVE": ("timeout, failed run health, or fewer matched "
                             "pairs than min_pairs_per_cell"),
        },
        "forbidden": ["ScenarioSpec change", "monthly integration",
                      "warm-state integration", "API change", "UI change",
                      "release promotion"],
    }


REQUIRED_REGISTRATION_FIELDS = (
    "schema_version", "study_id", "created", "release_evidence", "demand",
    "stress_cases", "seeds", "candidates", "comparison", "tolerances",
    "budget", "decision_rule",
)


def validate_registration(payload: Mapping[str, Any]) -> None:
    """Fail closed on a registration that could not decide anything."""
    missing = [key for key in REQUIRED_REGISTRATION_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"registration is missing field(s): {missing}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported registration schema_version {payload['schema_version']!r}")
    if payload.get("release_evidence") is not False:
        raise ValueError("this study is diagnostic; release_evidence must be false")
    cases = list(payload["stress_cases"])
    if not cases or len(set(cases)) != len(cases):
        raise ValueError("stress_cases must be a non-empty unique list")
    unknown = sorted(set(cases) - set(STRESS_CASES))
    if unknown:
        raise ValueError(f"unknown stress case(s): {unknown}")
    seeds = list(payload["seeds"])
    if len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must contain at least two unique values")
    if any(isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
           for seed in seeds):
        raise ValueError("seeds must be non-negative integers")
    candidates = list(payload["candidates"])
    if not candidates:
        raise ValueError("at least one closure candidate is required")
    ids = [str(candidate["candidate_id"]) for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate_id values must be unique")
    for candidate in candidates:
        if not candidate.get("edges"):
            raise ValueError(
                f"candidate {candidate.get('candidate_id')!r} closes no edge")
    if int(payload["comparison"].get("shortlist_size", 0)) < 1:
        raise ValueError("shortlist_size must be at least one")
    if int(payload["tolerances"].get("min_pairs_per_cell", 0)) < 1:
        raise ValueError("min_pairs_per_cell must be at least one")


@dataclass(frozen=True)
class Cell:
    """One matched measurement cell: a candidate under one stress case."""

    candidate_id: str
    stress_case: str
    seeds: tuple[int, ...]


def enumerate_cells(registration: Mapping[str, Any]) -> list[Cell]:
    """The complete cross product; the baseline appears under every case."""
    seeds = tuple(int(seed) for seed in registration["seeds"])
    candidates = ["baseline"] + [str(candidate["candidate_id"])
                                 for candidate in registration["candidates"]]
    return [Cell(candidate_id=candidate, stress_case=case, seeds=seeds)
            for case in registration["stress_cases"]
            for candidate in candidates]


# ── decision extraction (pure; no SUMO needed) ───────────────────────────────

def cell_cost(paired: Mapping[str, Any]) -> float:
    """The cell's primary cost: mean matched candidate-minus-baseline delay."""
    return float(paired["mean_time_loss_delta_s"])


def rank_within_case(costs: Mapping[str, float],
                     disqualified: Mapping[str, Sequence[str]],
                     *, shortlist_size: int, winner_margin_pct: float) -> dict:
    """One stress case's decision: viable set, ranking, shortlist, winner.

    A disqualified candidate is never ranked against a viable one — the same
    rule ``traffic_sim/simulation/closure_ranking.rank_closures`` applies, kept
    here because this study ranks SUMO deltas rather than the deterministic
    route-based disruption records that module consumes.
    """
    viable = sorted((cid for cid in costs if not disqualified.get(cid)),
                    key=lambda cid: (costs[cid], cid))
    refused = sorted(cid for cid in costs if disqualified.get(cid))
    groups = tie_groups(viable, costs, winner_margin_pct)
    winner: str | None = viable[0] if viable else None
    tied = list(groups[0]) if groups else []
    return {
        "viable_set": viable,
        "disqualified": refused,
        "disqualification_reasons": {cid: list(reasons)
                                     for cid, reasons in sorted(disqualified.items())
                                     if reasons},
        "ranking": viable,
        # Candidates the study cannot practically separate share one rank, so a
        # coin-flip reordering inside the tolerance is not read as a decision
        # change later.
        "rank_groups": [list(group) for group in groups],
        "shortlist": viable[:shortlist_size],
        "winner": winner,
        "tied_with_winner": tied,
        "costs": {cid: costs[cid] for cid in sorted(costs)},
    }


def tie_groups(order: Sequence[str], costs: Mapping[str, float],
               margin_pct: float) -> list[list[str]]:
    """Group a ranking into bands the study cannot practically separate."""
    groups: list[list[str]] = []
    for cid in order:
        if groups:
            leader = costs[groups[-1][0]]
            scale = abs(leader) if abs(leader) > 0 else 1.0
            if abs(costs[cid] - leader) <= scale * margin_pct / 100.0:
                groups[-1].append(cid)
                continue
        groups.append([cid])
    return groups


def rank_index(decision: Mapping[str, Any]) -> dict[str, int]:
    """{candidate: tie-band rank} for rank-correlation between stress cases."""
    groups = decision.get("rank_groups")
    if groups is None:
        return {cid: index for index, cid in enumerate(decision["ranking"])}
    return {cid: index for index, group in enumerate(groups) for cid in group}


def spearman_from_ranks(rank_a: Mapping[str, int],
                        rank_b: Mapping[str, int]) -> float | None:
    """Rank correlation between two rank maps over the same candidates."""
    shared = sorted(set(rank_a) & set(rank_b))
    n = len(shared)
    if n < 2:
        return None
    d_squared = sum((rank_a[cid] - rank_b[cid]) ** 2 for cid in shared)
    return 1 - (6 * d_squared) / (n * (n ** 2 - 1))


def spearman(order_a: Sequence[str], order_b: Sequence[str]) -> float | None:
    """Rank correlation between two strict orderings of the same candidates."""
    return spearman_from_ranks(
        {cid: index for index, cid in enumerate(order_a)},
        {cid: index for index, cid in enumerate(order_b)})


def max_regret(decisions: Mapping[str, Mapping[str, Any]]) -> dict:
    """Worst cost a stress case's winner suffers in any other stress case.

    Regret is measured on COMPLETE cells, never spliced field-wise: each entry
    is one candidate's cost inside one world that actually ran.
    """
    result: dict[str, Any] = {"per_case": {}, "max_regret_s": 0.0,
                              "max_regret_pct": 0.0}
    best_anywhere: dict[str, float] = {}
    for case, decision in decisions.items():
        for cid, cost in decision["costs"].items():
            best_anywhere.setdefault(case, float("inf"))
            if not decision["disqualified"] or cid not in decision["disqualified"]:
                best_anywhere[case] = min(best_anywhere[case], float(cost))
    for case, decision in decisions.items():
        winner = decision["winner"]
        if winner is None:
            continue
        worst = 0.0
        worst_pct = 0.0
        for other_case, other in decisions.items():
            if other_case == case or winner not in other["costs"]:
                continue
            best = best_anywhere.get(other_case)
            if best is None or best == float("inf"):
                continue
            regret = float(other["costs"][winner]) - best
            scale = abs(best) if abs(best) > 0 else 1.0
            worst = max(worst, regret)
            worst_pct = max(worst_pct, 100.0 * regret / scale)
        result["per_case"][case] = {"winner": winner,
                                    "max_regret_s": worst,
                                    "max_regret_pct": worst_pct}
        result["max_regret_s"] = max(result["max_regret_s"], worst)
        result["max_regret_pct"] = max(result["max_regret_pct"], worst_pct)
    return result


def classify_gate_s(decisions: Mapping[str, Mapping[str, Any]],
                    tolerances: Mapping[str, Any],
                    *, health: Mapping[str, Any] | None = None) -> dict:
    """The preregistered Gate S rule, applied deterministically.

    ``NO`` closes scenario and product integration; ``YES`` is the only result
    that may later open Gren B/D; ``INCONCLUSIVE`` repairs measurability and
    changes nothing.  A missing or unhealthy measurement can never read as
    ``NO`` — absence of evidence is not evidence of irrelevance.
    """
    health = dict(health or {})
    if health.get("status") and health["status"] != "ok":
        return {"verdict": GATE_INCONCLUSIVE,
                "reasons": [f"run health: {health.get('detail') or health['status']}"],
                "changed_fields": [], "health": health}
    if len(decisions) < 2:
        return {"verdict": GATE_INCONCLUSIVE,
                "reasons": ["fewer than two stress cases produced a decision"],
                "changed_fields": [], "health": health}

    cases = sorted(decisions)
    changed: list[str] = []
    reasons: list[str] = []
    reference = decisions[cases[0]]

    for case in cases[1:]:
        other = decisions[case]
        if set(other["viable_set"]) != set(reference["viable_set"]):
            changed.append("viable_set")
            reasons.append(
                f"viable set differs between {cases[0]} and {case}: "
                f"{sorted(reference['viable_set'])} vs {sorted(other['viable_set'])}")
        if set(other["disqualified"]) != set(reference["disqualified"]):
            changed.append("hard_failure")
            reasons.append(
                f"hard failures differ between {cases[0]} and {case}: "
                f"{sorted(reference['disqualified'])} vs {sorted(other['disqualified'])}")
        if set(other["shortlist"]) != set(reference["shortlist"]):
            changed.append("shortlist")
            reasons.append(
                f"shortlist differs between {cases[0]} and {case}: "
                f"{reference['shortlist']} vs {other['shortlist']}")
        # A changed winner only counts when the two candidates are not tied
        # in BOTH worlds within the preregistered practical tolerance.
        if other["winner"] != reference["winner"]:
            mutually_tied = (
                other["winner"] in reference["tied_with_winner"]
                and reference["winner"] in other["tied_with_winner"])
            if not mutually_tied:
                changed.append("winner")
                reasons.append(
                    f"winner differs between {cases[0]} and {case}: "
                    f"{reference['winner']} vs {other['winner']} "
                    f"(outside the {tolerances['winner_margin_pct']}% tie band)")

    correlations = {}
    for index, case in enumerate(cases):
        for other_case in cases[index + 1:]:
            rho = spearman_from_ranks(rank_index(decisions[case]),
                                      rank_index(decisions[other_case]))
            correlations[f"{case}|{other_case}"] = rho
            if rho is not None and rho < float(tolerances["material_rank_correlation"]):
                changed.append("rank_correlation")
                reasons.append(
                    f"rank correlation {case} vs {other_case} is {rho:.2f}, "
                    f"below {tolerances['material_rank_correlation']}")

    regret = max_regret(decisions)
    if regret["max_regret_pct"] > float(tolerances["material_regret_pct"]):
        changed.append("max_regret")
        reasons.append(
            f"maximum regret {regret['max_regret_pct']:.1f}% exceeds "
            f"{tolerances['material_regret_pct']}%")

    verdict = GATE_YES if changed else GATE_NO
    if verdict == GATE_NO:
        reasons.append(
            "every preregistered decision field is identical across stress "
            "cases with matched seeds")
    return {"verdict": verdict,
            "changed_fields": sorted(set(changed)),
            "reasons": reasons,
            "rank_correlation": correlations,
            "regret": regret,
            "health": health}


# ── append-only evidence ─────────────────────────────────────────────────────

def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def write_registration(path: Path, payload: Mapping[str, Any]) -> str:
    """Write the frozen registration, refusing a silent redefinition."""
    validate_registration(payload)
    if path.exists():
        existing = json.loads(path.read_text())
        if _canonical(existing) != _canonical(payload):
            raise ValueError(
                f"{path} already holds a different registration; a preregistered "
                f"study may not be redefined — write a new versioned file instead")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return "written"


def append_outcome(path: Path, record: Mapping[str, Any]) -> int:
    """Append one outcome record; earlier records are never rewritten."""
    if record.get("release_evidence") is not False:
        raise ValueError("a diagnostic outcome must carry release_evidence: false")
    payload: dict[str, Any]
    if path.exists():
        payload = json.loads(path.read_text())
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path} has an incompatible schema_version")
    else:
        payload = {"schema_version": SCHEMA_VERSION, "release_evidence": False,
                   "records": []}
    payload["records"].append(dict(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    return len(payload["records"])


def environment_record() -> dict:
    """Platform/toolchain provenance for the outcome record."""
    record = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        import run_scenario as rs           # noqa: PLC0415 — optional at import
        from traffic_sim.core.fingerprint import sumo_version
        record["sumo_version"] = sumo_version(rs.sumo_home())
    except Exception as error:              # noqa: BLE001 — provenance only
        record["sumo_version"] = None
        record["sumo_detail"] = f"{type(error).__name__}: {error}"
    return record


# ── the measured run ─────────────────────────────────────────────────────────

class EnvironmentUnavailable(RuntimeError):
    """The frozen matrix cannot be executed here (no SUMO, no demand build)."""


def _load_runtime():
    """Import the existing runners lazily so the pure logic stays testable."""
    try:
        import run_scenario as rs
        from signal_lab import window_offsets_s
        from signal_optimize import paired_comparison, run_condition
        from traffic_sim.simulation import metrics as cm
    except Exception as error:              # noqa: BLE001 — environment probe
        raise EnvironmentUnavailable(
            f"simulation runtime unavailable: {type(error).__name__}: {error}"
        ) from error
    return rs, window_offsets_s, run_condition, paired_comparison, cm


def measure(registration: Mapping[str, Any], *,
            runner: Callable[..., Any] | None = None) -> dict:
    """Execute the frozen matrix and return the outcome record.

    ``runner`` exists so the study's control flow — matched pairs, per-case
    decisions, Gate S — is testable without a simulator.  It receives
    ``(candidate_id, edges, stress_case, seeds)`` and returns
    ``(runs, disqualification_reasons)``.
    """
    validate_registration(registration)
    started = time.perf_counter()
    timeout_s = float(registration["budget"]["timeout_s"])
    tolerances = registration["tolerances"]
    seeds = tuple(int(seed) for seed in registration["seeds"])
    by_id = {str(candidate["candidate_id"]): list(candidate["edges"])
             for candidate in registration["candidates"]}

    if runner is None:
        runner = _sumo_runner(registration)

    cells: dict[tuple[str, str], Any] = {}
    reasons: dict[tuple[str, str], list[str]] = {}
    health: dict[str, Any] = {"status": "ok"}
    for cell in enumerate_cells(registration):
        if time.perf_counter() - started > timeout_s:
            health = {"status": "timeout",
                      "detail": f"budget of {timeout_s:.0f}s exhausted at "
                                f"{cell.candidate_id}/{cell.stress_case}"}
            break
        edges = [] if cell.candidate_id == "baseline" else by_id[cell.candidate_id]
        try:
            runs, why = runner(candidate_id=cell.candidate_id, edges=edges,
                               stress_case=cell.stress_case, seeds=seeds)
        except EnvironmentUnavailable:
            raise
        except Exception as error:          # noqa: BLE001 — one cell's failure
            health = {"status": "run_failed",
                      "detail": f"{cell.candidate_id}/{cell.stress_case}: "
                                f"{type(error).__name__}: {error}"}
            break
        cells[(cell.candidate_id, cell.stress_case)] = runs
        reasons[(cell.candidate_id, cell.stress_case)] = list(why)

    decisions: dict[str, Any] = {}
    paired_records: dict[str, Any] = {}
    if health["status"] == "ok":
        from signal_optimize import paired_comparison
        min_pairs = int(tolerances["min_pairs_per_cell"])
        for case in registration["stress_cases"]:
            baseline_runs = cells.get(("baseline", case))
            if baseline_runs is None:
                health = {"status": "incomplete",
                          "detail": f"no baseline runs for stress case {case}"}
                break
            costs: dict[str, float] = {}
            disqualified: dict[str, list[str]] = {}
            case_pairs: dict[str, Any] = {}
            for candidate_id in by_id:
                candidate_runs = cells.get((candidate_id, case))
                if candidate_runs is None:
                    health = {"status": "incomplete",
                              "detail": f"missing cell {candidate_id}/{case}"}
                    break
                paired = paired_comparison(baseline_runs, candidate_runs)
                if paired["n_pairs"] < min_pairs:
                    health = {"status": "insufficient_pairs",
                              "detail": f"{candidate_id}/{case} has "
                                        f"{paired['n_pairs']} matched pairs, "
                                        f"below {min_pairs}"}
                    break
                case_pairs[candidate_id] = paired
                costs[candidate_id] = cell_cost(paired)
                disqualified[candidate_id] = reasons[(candidate_id, case)]
            if health["status"] != "ok":
                break
            paired_records[case] = case_pairs
            decisions[case] = rank_within_case(
                costs, disqualified,
                shortlist_size=int(registration["comparison"]["shortlist_size"]),
                winner_margin_pct=float(tolerances["winner_margin_pct"]))

    gate = classify_gate_s(decisions, tolerances, health=health)
    return {
        "study_id": registration["study_id"],
        "schema_version": SCHEMA_VERSION,
        "release_evidence": False,
        "registration_digest": _digest(registration),
        "environment": environment_record(),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "simulation_units": len(cells) * len(seeds),
        "seeds": list(seeds),
        "stress_cases": list(registration["stress_cases"]),
        "paired_comparisons": paired_records,
        "decisions": decisions,
        "gate_s": gate,
    }


def _digest(payload: Mapping[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]


def _sumo_runner(registration: Mapping[str, Any]) -> Callable[..., Any]:
    """Bind the frozen study to the project's existing closure runner."""
    rs, window_offsets_s, run_condition, _paired, cm = _load_runtime()

    meta_path = rs.SUMO_DIR / "demand_meta.json"
    if not meta_path.is_file():
        raise EnvironmentUnavailable(
            f"no calibrated demand build at {meta_path}; run `make demand` first")
    meta = json.loads(meta_path.read_text())
    demand = registration["demand"]
    if meta.get("date") != demand["date"] or meta.get("source") != demand["source"]:
        raise EnvironmentUnavailable(
            f"loaded demand is {meta.get('date')}/{meta.get('source')}, the study "
            f"is frozen on {demand['date']}/{demand['source']}")
    variants = rs.demand_variants(meta)
    if len(variants) < len(registration["stress_cases"]):
        raise EnvironmentUnavailable(
            f"the build has {len(variants)} demand variant(s); the study needs "
            f"{len(registration['stress_cases'])}")
    begin_s, end_s = window_offsets_s(meta["epoch_sim"], demand["window_start"],
                                      demand["window_end"])
    home = rs.sumo_home()
    freeflow = rs.edge_freeflow_times()
    work_root = rs.SUMO_DIR / "direction_sensitivity"
    work_root.mkdir(parents=True, exist_ok=True)

    def runner(*, candidate_id: str, edges: Sequence[str], stress_case: str,
               seeds: Sequence[int]):
        route_path = rs.variant_path(variants, stress_case)
        add_paths: list[Path] = []
        run_route = route_path
        if edges:
            closures = [{"edge_id": edge, "begin_s": begin_s, "end_s": end_s}
                        for edge in edges]
            closure_add = work_root / f"closure_{candidate_id}_{stress_case}.add.xml"
            rs.write_closure_additional(
                closure_add, closures, rs.edges_near(list(edges), rs.REROUTER_RADIUS_M))
            add_paths = [closure_add]
            # The same stranded-vehicle truncation the deployed closure path
            # uses: a driver whose destination is cut off still drives most of
            # the trip, so deleting the vehicle would erase its real load on
            # every other edge.
            run_route = work_root / f"{route_path.stem}_{candidate_id}.rou.xml"
            rs.truncate_stranded_vehicles(
                route_path, list(edges), run_route,
                rs.build_edge_graph(set(edges)), closures=closures,
                edge_travel_s=freeflow)
        seed_plan = [(int(seed), run_route) for seed in seeds]
        _aggregate, runs = run_condition(
            net_path=rs.NET_PATH, add_paths=add_paths, variants=[run_route],
            seeds=len(seed_plan), begin_s=begin_s, end_s=end_s, home=home,
            micro=(registration["budget"]["simulation_mode"] == "micro"),
            seed_plan=seed_plan, closed_edges=list(edges) or None,
            run_label=f"dirsens_{candidate_id}_{stress_case}")
        # Every run keeps the stress case in its pair key, so a candidate can
        # only ever be compared with the baseline of the SAME case and seed.
        runs = [type(run)(seed=run.seed, demand_variant=stress_case,
                          metrics=run.metrics) for run in runs]
        why: list[str] = []
        if edges:
            for run in runs:
                why.extend(cm.disqualification_reasons(run.metrics))
        return runs, sorted(set(why))

    return runner


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_gate(outcome: Mapping[str, Any]) -> None:
    gate = outcome["gate_s"]
    print(f"\nGate S: {gate['verdict']}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    if gate["verdict"] == GATE_NO:
        print("  => direction is not decision-relevant here; scenario and "
              "product integration stay closed (plan Exit A/C path).")
    elif gate["verdict"] == GATE_YES:
        print("  => direction changes the decision; Gren B/D MAY be opened "
              "once Gate M is decided. No product code is authorised yet.")
    else:
        print("  => nothing is decided; repair measurability and rerun the "
              "frozen study without choosing cases from the outcome.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("preregister", help="freeze the study")
    register.add_argument("--out", type=Path, default=REGISTRATION_PATH)
    register.add_argument("--created", default="2026-08-16",
                          help="Freeze date recorded in the registration.")

    run = sub.add_parser("run", help="execute the frozen matrix")
    run.add_argument("--registration", type=Path, default=REGISTRATION_PATH)
    run.add_argument("--outcome", type=Path, default=OUTCOME_PATH)

    classify = sub.add_parser("classify", help="re-derive the verdict")
    classify.add_argument("--outcome", type=Path, default=OUTCOME_PATH)
    classify.add_argument("--registration", type=Path, default=REGISTRATION_PATH)

    args = parser.parse_args(argv)

    if args.command == "preregister":
        payload = default_registration(args.created)
        status = write_registration(args.out, payload)
        print(f"{status}: {args.out}")
        print(f"  {len(payload['candidates'])} candidates x "
              f"{len(payload['stress_cases'])} stress cases x "
              f"{len(payload['seeds'])} seeds "
              f"= {(len(payload['candidates']) + 1) * len(payload['stress_cases']) * len(payload['seeds'])} "
              f"simulation units (baseline included)")
        return 0

    if args.command == "run":
        registration = json.loads(Path(args.registration).read_text())
        try:
            outcome = measure(registration)
        except EnvironmentUnavailable as error:
            outcome = {
                "study_id": registration.get("study_id"),
                "schema_version": SCHEMA_VERSION,
                "release_evidence": False,
                "registration_digest": _digest(registration),
                "environment": environment_record(),
                "decisions": {},
                "gate_s": {"verdict": GATE_INCONCLUSIVE,
                           "changed_fields": [],
                           "reasons": [f"environment unavailable: {error}"],
                           "health": {"status": "environment_unavailable",
                                      "detail": str(error)}},
            }
        index = append_outcome(Path(args.outcome), outcome)
        print(f"appended record {index} to {args.outcome}")
        _print_gate(outcome)
        return 0

    payload = json.loads(Path(args.outcome).read_text())
    registration = json.loads(Path(args.registration).read_text())
    record = payload["records"][-1]
    gate = classify_gate_s(record.get("decisions", {}),
                           registration["tolerances"],
                           health=record["gate_s"].get("health"))
    _print_gate({"gate_s": gate})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
