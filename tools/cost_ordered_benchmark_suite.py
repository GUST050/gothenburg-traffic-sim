"""Pre-register and run an outcome-blind multi-case cost-order benchmark.

The original benchmark intentionally chose one structural case. Its first real
run was useful but non-discriminating: every candidate had to be verified. A
second single-case registration would merely repeat that risk. This harness
freezes a small suite across distinct dates and directed edges before looking
at any new outcome, then applies the unchanged per-case equivalence gates and
an aggregate positive-saving gate.

It does not activate policy v3. Even a passing suite still requires the frozen
untouched held-out campaign and the existing release gates.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.cost_ordered_benchmark as base  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
REGISTRATION_SCHEMA = "cost_ordered_benchmark_suite_registration_v1"
OUTCOME_SCHEMA = "cost_ordered_benchmark_suite_outcome_v1"
DEFAULT_REGISTRATION = (
    ROOT / "validation" / "cost_ordered_benchmark_registration_v5.json")
DEFAULT_OUTCOME = (
    ROOT / "validation" / "cost_ordered_benchmark_outcome_v5.json")

SUITE_CASE_COUNT = 4
MINIMUM_DISTINCT_DATES = 2
MINIMUM_DISTINCT_EDGES = 4
MINIMUM_CASES_WITH_TWO_HEALTH_VIABLE_CANDIDATES = 2
MINIMUM_TOTAL_SUMO_SAVING = 1


def _edge(item: Mapping[str, Any]) -> str:
    return str(item["spec"]["directed_edges"][0])


def _dates(item: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in item["work_dates"])


def select_suite_cases(
    selection: Mapping[str, Any],
    *,
    count: int = SUITE_CASE_COUNT,
) -> list[dict[str, Any]]:
    """Choose distinct-edge cases round-robin across dates, outcome-blind.

    Candidate count is the primary discriminating signal. Date and edge
    diversity are then maximised without consulting a cost, health result,
    winner, timeout or prior benchmark outcome.
    """
    eligible = [
        dict(item) for item in selection["evaluated"]
        if item.get("structurally_eligible")
    ]
    eligible.sort(key=lambda item: (
        -int(item["candidate_count"]),
        int(item["unique_daily_unit_count"]),
        _dates(item),
        _edge(item),
        str(item["search_id"]),
    ))
    available_dates = sorted({date for item in eligible for date in _dates(item)})
    chosen: list[dict[str, Any]] = []
    chosen_keys: set[str] = set()
    used_edges: set[str] = set()

    while len(chosen) < count:
        progressed = False
        for work_date in available_dates:
            candidate = next((
                item for item in eligible
                if work_date in _dates(item)
                and _edge(item) not in used_edges
                and str(item["search_content_key"]) not in chosen_keys
            ), None)
            if candidate is None:
                continue
            chosen.append(candidate)
            chosen_keys.add(str(candidate["search_content_key"]))
            used_edges.add(_edge(candidate))
            progressed = True
            if len(chosen) == count:
                break
        if not progressed:
            break
    return chosen


def _case_record(index: int, item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": f"suite-v1-{index + 1}-{item['search_content_key'][:12]}",
        **{key: copy.deepcopy(item[key]) for key in (
            "search_id", "search_content_key", "spec", "candidate_count",
            "unique_daily_unit_count", "work_dates",
            "work_dates_with_calibrated_archive",
        )},
    }


def build_registration(
    runs_root: Path = base.DEFAULT_RUNS_ROOT,
    *,
    data_root: Path = ROOT,
    outcome_path: Path = DEFAULT_OUTCOME,
) -> dict[str, Any]:
    """Freeze the suite and every input before any suite outcome exists."""
    # Reuse the mature source/runtime/network/policy seal. This first call is
    # itself outcome-blind; its single selected case is replaced below.
    record = base.build_registration(
        runs_root,
        from_archives=True,
        data_root=data_root,
        outcome_path=outcome_path,
    )
    selection = base.select_case(Path(runs_root), from_archives=True)
    chosen = select_suite_cases(selection)
    distinct_dates = sorted({date for item in chosen for date in _dates(item)})
    distinct_edges = sorted({_edge(item) for item in chosen})
    if (len(chosen) < SUITE_CASE_COUNT
            or len(distinct_dates) < MINIMUM_DISTINCT_DATES
            or len(distinct_edges) < MINIMUM_DISTINCT_EDGES):
        raise ValueError(
            "archive library cannot form the frozen four-case suite with "
            "at least two dates and four distinct directed edges")

    archives: dict[str, Any] = {}
    for item in chosen:
        archives.update(copy.deepcopy(item.get("resolved_archives") or {}))

    record.update({
        "schema": REGISTRATION_SCHEMA,
        "kind": "cost_ordered_benchmark_suite_registration",
        "hypothesis": (
            "Across four outcome-blind structural cases, cost-first execution "
            "preserves every exhaustive decision and failure classification, "
            "while saving at least one SUMO candidate verification in total."),
        "outcome_record": base._relative(Path(outcome_path).resolve()),
        "selected_cases": [
            _case_record(index, item) for index, item in enumerate(chosen)
        ],
        "archives": archives,
        "suite_thresholds": {
            "case_count": SUITE_CASE_COUNT,
            "minimum_distinct_dates": MINIMUM_DISTINCT_DATES,
            "minimum_distinct_edges": MINIMUM_DISTINCT_EDGES,
            "minimum_cases_with_two_health_viable_candidates": (
                MINIMUM_CASES_WITH_TWO_HEALTH_VIABLE_CANDIDATES),
            "minimum_total_sumo_verifications_saved": (
                MINIMUM_TOTAL_SUMO_SAVING),
            "per_case_equivalence": copy.deepcopy(base.GATE_THRESHOLDS),
        },
        "output_roots": {
            "workspace_namespace": "closure-search-benchmark-suite-v1",
            "daily_cost_cache": (
                "runs/closure-search-benchmark-suite-v1-daily-costs"),
        },
        "claim_boundary": {
            "activates_policy_v3": False,
            "opens_global_best": False,
            "permits_ui_claim": False,
            "reason": "a preregistration freezes a question; it measures nothing",
        },
    })
    record.pop("selected_case", None)
    record.pop("blocked_by", None)
    record["selection"] = {
        "rule": (
            "highest candidate count, then fewest units; round-robin across "
            "archive dates while requiring a new directed edge; no outcome, "
            "cost, health result, timeout or winner is consulted"),
        "case_source": "discovered_from_archive_metadata",
        "evaluated_case_count": len(selection["evaluated"]),
        "structurally_eligible_case_count": sum(
            bool(item.get("structurally_eligible"))
            for item in selection["evaluated"]),
        "archives_available": selection["archives_available"],
        "selected_case_count": len(chosen),
        "distinct_dates": distinct_dates,
        "distinct_edges": distinct_edges,
        "reads_outcomes": False,
    }
    record["sources"] = dict(record["sources"])
    record["sources"]["tools/cost_ordered_benchmark_suite.py"] = sha256_file(
        Path(__file__))
    record["content_key"] = base._content_key({
        key: value for key, value in record.items()
        if key not in {"content_key", "registered_at"}
    })
    return record


def verify_bindings(registration: Mapping[str, Any], runs_root: Path) -> list[str]:
    drift = base.verify_bindings(registration, runs_root)
    cases = list(registration.get("selected_cases") or [])
    if len(cases) != SUITE_CASE_COUNT:
        drift.append("the suite no longer contains exactly four selected cases")
    keys = [str(item.get("search_content_key")) for item in cases]
    if len(set(keys)) != len(keys):
        drift.append("the suite contains duplicate search content keys")
    return drift


def _pilot_health_viable_count(arm: Mapping[str, Any]) -> int:
    pilot = (arm.get("result") or {}).get("pilot_selection") or {}
    return sum(
        bool(item.get("complete"))
        and bool(item.get("eligible"))
        and not item.get("hard_failures")
        for item in pilot.get("candidates") or []
    )


def _case_semantics_pass(gates: Mapping[str, Any]) -> bool:
    checks = dict(gates.get("checks") or {})
    checks.pop("sumo_verifications_saved", None)
    return bool(checks) and all(checks.values())


def suite_gate_results(
    case_results: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    total_saved = sum(
        int((item.get("comparison") or {}).get(
            "sumo_verifications_saved", 0))
        for item in case_results
    )
    health_cases = sum(
        int(item.get("pilot_health_viable_candidate_count", 0)) >= 2
        for item in case_results
    )
    checks = {
        "case_count": len(case_results) == int(thresholds["case_count"]),
        "all_case_equivalence_gates": bool(case_results) and all(
            _case_semantics_pass(item.get("gates") or {})
            for item in case_results),
        "minimum_cases_with_two_health_viable_candidates": (
            health_cases >= int(thresholds[
                "minimum_cases_with_two_health_viable_candidates"])),
        "positive_total_sumo_saving": (
            total_saved >= int(thresholds[
                "minimum_total_sumo_verifications_saved"])),
        "no_execution_errors": all(
            "execution_error" not in (item.get("comparison") or {})
            for item in case_results),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": copy.deepcopy(dict(thresholds)),
        "measured": {
            "case_count": len(case_results),
            "cases_with_two_health_viable_candidates": health_cases,
            "total_sumo_verifications_saved": total_saved,
        },
    }


def run_suite(
    registration: Mapping[str, Any],
    *,
    runs_root: Path,
    data_root: Path,
    release_root: Path,
    workspace_root: Path,
    fault_injection: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    namespace = str(registration["output_roots"]["workspace_namespace"])
    cache = str(registration["output_roots"]["daily_cost_cache"])
    for index, case in enumerate(registration["selected_cases"]):
        case_id = str(case["case_id"])
        child = copy.deepcopy(dict(registration))
        child["schema"] = base.REGISTRATION_SCHEMA
        child["selected_case"] = copy.deepcopy(case)
        child["output_roots"] = {
            "exhaustive": f"{case_id}-exhaustive",
            "cost_ordered": f"{case_id}-cost-ordered",
            # Case-specific, not the suite's shared `cache`: the real
            # per-daily-unit SUMO evidence cache
            # (`_isolated_daily_results_cache_root`) is derived from this
            # path's PARENT and is only cloned once, the first time a given
            # destination is seen. A cache path shared across every case in
            # the suite made every case after the first silently resume
            # whatever case 1 already computed there — a real cross-case
            # evidence leak, not merely a missed optimisation, because it
            # corrupts exactly the attempt-count/wall-time numbers this
            # benchmark exists to measure.
            "daily_cost_cache": f"{cache}-{case_id}",
        }
        try:
            executed = base.run_benchmark(
                child,
                runs_root=Path(runs_root),
                data_root=Path(data_root),
                release_root=Path(release_root),
                workspace_root=Path(workspace_root) / namespace,
                fault_injection=fault_injection,
                # Alternate which arm starts first across cases so a
                # systematic host-load difference between "runs first" and
                # "runs second" cannot land entirely on one arm across the
                # whole suite — the isolation-hardening requirement this
                # runner was missing (each individual case already isolates
                # the two arms into separate processes; this is the
                # complementary cross-case control).
                counterbalance=bool(index % 2),
            )
            comparison = dict(executed["comparison"])
            health_count = _pilot_health_viable_count(
                executed["arms"]["exhaustive"])
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            comparison = {"execution_error": {
                "type": type(error).__name__, "message": str(error)}}
            health_count = 0
        results.append({
            "case_id": case_id,
            "search_id": case["search_id"],
            "search_content_key": case["search_content_key"],
            "work_dates": copy.deepcopy(case["work_dates"]),
            "directed_edges": copy.deepcopy(case["spec"]["directed_edges"]),
            "candidate_count": case["candidate_count"],
            "pilot_health_viable_candidate_count": health_count,
            "comparison": comparison,
            "gates": base._gate_results(comparison),
        })
    return results


def build_outcome(
    registration: Mapping[str, Any],
    case_results: Sequence[Mapping[str, Any]],
    *,
    registration_path: Path,
) -> dict[str, Any]:
    gates = suite_gate_results(case_results, registration["suite_thresholds"])
    record: dict[str, Any] = {
        "schema": OUTCOME_SCHEMA,
        "kind": "cost_ordered_benchmark_suite_outcome",
        "evidence_class": "preregistered_benchmark",
        "release_evidence": False,
        "measured_at": time.strftime("%Y-%m-%d"),
        "status": "measured" if gates["checks"]["no_execution_errors"] else (
            "measured_with_execution_failures"),
        "registration": {
            "path": base._relative(Path(registration_path).resolve()),
            "schema": registration.get("schema"),
            "content_key": registration.get("content_key"),
        },
        "case_results": list(copy.deepcopy(case_results)),
        "gates": gates,
        "claim_boundary": {
            "activates_policy_v3": False,
            "opens_global_best": False,
            "permits_ui_claim": False,
            "reason": (
                "policy v3 additionally requires the untouched held-out "
                "campaign and every existing release gate"),
        },
    }
    record["content_key"] = base._content_key({
        key: value for key, value in record.items()
        if key not in {"content_key", "measured_at"}
    })
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preregister", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--runs-root", type=Path, default=base.DEFAULT_RUNS_ROOT)
    parser.add_argument("--data-root", type=Path, default=ROOT)
    parser.add_argument("--registration", type=Path,
                        default=DEFAULT_REGISTRATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument("--release-root", type=Path,
                        default=base.DEFAULT_RELEASE_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--no-fault-injection", action="store_true")
    args = parser.parse_args(argv)

    if args.preregister:
        record = build_registration(
            args.runs_root, data_root=args.data_root, outcome_path=args.out)
        base._write(args.registration, record, overwrite=False)
        print(
            f"wrote {args.registration} "
            f"(cases={len(record['selected_cases'])}, "
            f"dates={len(record['selection']['distinct_dates'])})")
        return 0

    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    if registration.get("schema") != REGISTRATION_SCHEMA:
        raise SystemExit(f"unsupported suite registration schema: "
                         f"{registration.get('schema')!r}")
    declared = registration.get("outcome_record")
    if declared != base._relative(args.out.resolve()):
        raise SystemExit(
            f"registration names {declared}, not {base._relative(args.out.resolve())}")
    drift = verify_bindings(registration, args.runs_root)
    if drift:
        raise SystemExit("registration binding drift:\n  - " + "\n  - ".join(drift))
    cases = run_suite(
        registration,
        runs_root=args.runs_root,
        data_root=args.data_root,
        release_root=args.release_root,
        workspace_root=args.workspace_root,
        fault_injection=not args.no_fault_injection,
    )
    outcome = build_outcome(
        registration, cases, registration_path=args.registration)
    base._write(args.out, outcome, overwrite=False)
    print(
        f"wrote {args.out} (status={outcome['status']}, "
        f"gates_passed={outcome['gates']['passed']}, "
        f"saved={outcome['gates']['measured']['total_sumo_verifications_saved']})")
    return 0 if outcome["gates"]["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
