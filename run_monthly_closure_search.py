#!/usr/bin/env python3
"""Run or resume a robust recurring closure search with archived SUMO demand."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import (
    load_closure_search_spec,
)
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.workspace import WorkspaceLock
from traffic_sim.simulation.monthly_search import (
    MonthlySearchPolicy,
    run_monthly_search,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
)
from traffic_sim.simulation.independent_daily import (
    INDEPENDENT_DAILY_ENVELOPE_POLICY,
    IndependentDailyRunner,
    IsolatedDailySumoRunner,
)
from traffic_sim.simulation.closure_preflight import (
    ClosureSearchPreflight,
    UnsupportedPreflightSpec,
    preflight,
)
# The seed-worker budget is a JSON reader, and it is consulted before any
# simulation exists. Importing it from `monthly_sumo` pulled in run_scenario
# (pandas) and suggest_closure_time (SciPy) — about 110 MiB — on every run,
# including one the exact preflight then refuses.
from traffic_sim.simulation.seed_worker_budget import (
    SEED_WORKER_BENCHMARK_RECORD,
    approved_seed_workers,
)
from traffic_sim.simulation.search_workspace import DEFAULT_ROOT


def _simulation_backends():
    """Import the SUMO-side stack, lazily.

    `monthly_sumo` and `monthly_demand` reach run_scenario and
    suggest_closure_time, whose module-scope numpy/pandas/SciPy imports cost
    ~110 MiB. Nothing in argument validation, the exact preflight or the
    streaming enumeration needs any of it, so a search that is refused before
    it starts must not pay for it. Imported here, in one place, so the cost is
    incurred exactly once and exactly where a simulation becomes real.
    """
    from traffic_sim.simulation.monthly_demand import (
        MonthlyDemandResolverRunner,
        recover_live_demand_release,
    )
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    return (ArchivedDemandSumoRunner, MonthlyDemandResolverRunner,
            recover_live_demand_release)


def _proxy_screen_builder(path, **kwargs):
    """Frozen-campaign proxy screening, imported on use.

    `screen_monthly_closures` is only needed by --screening-mode=proxy.
    """
    from screen_monthly_closures import build_screening_artifact
    from traffic_sim.simulation.monthly_proxy import (
        HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    )

    return build_screening_artifact(
        path,
        policy=HELD_OUT_VALIDATED_SHORTLIST_POLICY,
        **kwargs,
    )


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _bounded_exhaustive_builder(
    spec_path: Path,
    *,
    maximum_candidates: int,
    proxy_version: str = "bounded_exhaustive_sumo_v1",
) -> dict[str, Any]:
    spec = load_closure_search_spec(spec_path)
    # PR C: stream and stop at the cap.  Materialising first and checking the
    # length afterwards paid the full memory cost of exactly the searches the
    # cap exists to refuse.
    schedule_ids: list[str] = []
    for schedule in iter_closure_schedules(spec):
        schedule_ids.append(schedule.schedule_id)
        if len(schedule_ids) > maximum_candidates:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{maximum_candidates} candidates, above the explicit cap "
                f"{maximum_candidates}"
            )
    return _exhaustive_payload(
        spec_path,
        spec=spec,
        schedule_ids=schedule_ids,
        maximum_candidates=maximum_candidates,
        proxy_version=proxy_version,
    )


def _exhaustive_payload(
    spec_path: Path,
    *,
    spec,
    schedule_ids: Sequence[str],
    maximum_candidates: int,
    proxy_version: str,
) -> dict[str, Any]:
    if len(schedule_ids) > maximum_candidates:
        raise ValueError(
            f"bounded exhaustive screening generated {len(schedule_ids)} "
            f"candidates, above the explicit cap {maximum_candidates}"
        )
    return {
        "schema_version": 1,
        "kind": "monthly_closure_proxy_screening",
        "proxy_version": proxy_version,
        "search": spec.to_dict(),
        "claim_boundary": {
            "evidence_level": "no_proxy_bounded_exhaustive",
            "global_best_claim_allowed": False,
            "ui_exposure_allowed": False,
            "reason": "golden/diagnostic bounded exhaustive SUMO screening",
        },
        "candidate_count": len(schedule_ids),
        "scoreable_candidate_count": 0,
        "unavailable_candidates": [],
        "ranked_candidates": [],
        "shortlist": {
            "version": proxy_version,
            "selection_complete": True,
            "entries": [
                {
                    "schedule_id": schedule_id,
                    "selection_reasons": ["bounded_exhaustive"],
                    "proxy_rank": None,
                }
                for schedule_id in schedule_ids
            ],
        },
        "input_fingerprints": {
            "closure_search_spec": {
                "path": str(Path(spec_path).resolve()),
                "sha256": sha256_file(Path(spec_path)),
            }
        },
    }


def _independent_exhaustive_builder(
    spec_path: Path,
    *,
    maximum_candidates: int,
    maximum_daily_units: int,
    baseline_trip_duration_p99_s: int,
    preflight_report: ClosureSearchPreflight | None = None,
) -> dict[str, Any]:
    spec = load_closure_search_spec(spec_path)
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "independent exhaustive screening requires the independent policy"
        )
    report = preflight_report or _independent_exhaustive_preflight(
        spec,
        maximum_candidates=maximum_candidates,
        maximum_daily_units=maximum_daily_units,
        baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
    )
    if report is not None:
        if report.search_content_key != spec.content_key:
            raise ValueError(
                "independent exhaustive preflight belongs to another search")
        if (report.parent_schedule_limit != maximum_candidates
                or report.daily_unit_limit != maximum_daily_units):
            raise ValueError(
                "independent exhaustive preflight uses different resource caps")
    from traffic_sim.simulation.independent_daily import daily_unit_records

    source_year = 2027 if spec.source == "forecast" else 2025
    source_start = date(source_year, 1, 1)
    source_end = date(source_year + 1, 1, 1)

    # PR C: one streaming pass instead of a full parent tuple, a full unit
    # tuple and the reverse unit->parents graph.  Each parent is enumerated,
    # its own ordered units are checked, and the parent is released.  The only
    # retained state is per-UNIQUE-unit — the same thing the 10,000-unit cap
    # bounds — plus the IDs of the eligible parents.
    unavailable_units: dict[str, str] = {}
    evaluated_units: set[str] = set()
    unavailable_candidates = []
    eligible_ids: list[str] = []
    candidate_count = 0
    for schedule in iter_closure_schedules(spec):
        candidate_count += 1
        if candidate_count > maximum_candidates:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{maximum_candidates} parent schedules, above the explicit "
                f"cap {maximum_candidates}"
            )
        parent_units: list[str] = []
        for unit_id, _identity, build in daily_unit_records(spec, schedule):
            parent_units.append(unit_id)
            if unit_id in evaluated_units:
                continue
            evaluated_units.add(unit_id)
            # A schedule object is built only for a NEW unique unit, which is
            # also the only place an envelope needs checking.
            daily = build()
            if len(evaluated_units) > maximum_daily_units:
                raise ValueError(
                    f"independent exhaustive screening generated more than "
                    f"{maximum_daily_units} unique daily SUMO units, above "
                    f"the explicit cap {maximum_daily_units}"
                )
            try:
                envelope = build_simulation_envelope(
                    spec,
                    daily,
                    baseline_trip_duration_p99_s=(
                        baseline_trip_duration_p99_s
                    ),
                    policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
                )
            except ValueError as exc:
                unavailable_units[unit_id] = str(exc)
                continue
            envelope_start = date.fromisoformat(envelope.scenario_start[:10])
            envelope_end = date.fromisoformat(envelope.scenario_end[:10])
            if envelope_start < source_start or envelope_end > source_end:
                unavailable_units[unit_id] = (
                    "exact warm-up/recovery envelope lies outside the "
                    f"downloaded {source_year} {spec.source} demand year"
                )
        if len(set(parent_units)) != len(parent_units):
            raise ValueError("independent parent repeats a daily unit")
        failed = [
            (unit_id, unavailable_units[unit_id])
            for unit_id in parent_units
            if unit_id in unavailable_units
        ]
        if failed:
            unavailable_candidates.append({
                "schedule_id": schedule.schedule_id,
                "evidence": {
                    "reason": "independent daily envelope unavailable",
                    "daily_units": [
                        {"unit_id": unit_id, "reason": reason}
                        for unit_id, reason in failed
                    ],
                },
                "coverage": None,
            })
        else:
            eligible_ids.append(schedule.schedule_id)
    if report is not None and (
        report.parent_schedule_count != candidate_count
        or report.unique_daily_unit_count != len(evaluated_units)
    ):
        raise ValueError(
            "independent exhaustive preflight disagrees with streamed "
            "enumeration")
    if not eligible_ids:
        raise ValueError(
            "independent exhaustive screening has no schedules whose exact "
            "warm-up and recovery fit the downloaded demand year"
        )

    payload = _exhaustive_payload(
        spec_path,
        spec=spec,
        schedule_ids=eligible_ids,
        maximum_candidates=maximum_candidates,
        proxy_version="independent_daily_exhaustive_sumo_v1",
    )
    payload["candidate_count"] = candidate_count
    payload["scoreable_candidate_count"] = len(eligible_ids)
    payload["unavailable_candidates"] = unavailable_candidates
    payload["independent_daily_execution"] = {
        "interday_policy": spec.interday_policy,
        "work_allocation_policy": spec.work_allocation_policy,
        "unique_daily_unit_count": len(evaluated_units),
        "executable_daily_unit_count": (
            len(evaluated_units) - len(unavailable_units)
        ),
        "unavailable_daily_unit_count": len(unavailable_units),
        "maximum_daily_units": maximum_daily_units,
    }
    return payload


def _independent_exhaustive_preflight(
    spec,
    *,
    maximum_candidates: int,
    maximum_daily_units: int,
    baseline_trip_duration_p99_s: int,
) -> ClosureSearchPreflight | None:
    """Fail an over-budget independent search before ledger publication.

    The exact PR-B preflight counts without constructing schedules.  Running
    it before the monthly search workspace is opened prevents an over-budget
    search from writing a potentially large candidate ledger only to discover
    the unchanged parent/unit cap during screening.
    """
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "independent exhaustive screening requires the independent policy"
        )
    try:
        report = preflight(
            spec,
            baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
            envelope_policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
            parent_schedule_limit=maximum_candidates,
            daily_unit_limit=maximum_daily_units,
        )
    except UnsupportedPreflightSpec:
        # The streaming path still supports legacy allocation policies whose
        # exact closed-form preflight is deliberately unavailable.  Preserve
        # that compatibility and enforce both caps while streaming below;
        # supported policies get the earlier, allocation-free refusal.
        return None
    if report.parent_schedule_count > maximum_candidates:
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.parent_schedule_count} parent schedules, above the "
            f"explicit cap {maximum_candidates}"
        )
    if report.unique_daily_unit_count > maximum_daily_units:
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.unique_daily_unit_count} unique daily SUMO units, "
            f"above the explicit cap {maximum_daily_units}"
        )
    if report.parent_schedule_count == 0:
        raise ValueError("independent exhaustive search has no legal schedules")
    return report


def _publish_cost_ordered_shadow(spec, policy, *, root) -> dict:
    """Replay PR E's cost-ordered scan over a finished run, in shadow mode.

    Writes `cost-ordered-shadow.json` beside the search workspace — outside
    `artifacts/`, because it is a DIAGNOSTIC replay of a completed run, not one
    of the run's immutable artifacts, and a succeeded workspace is closed to
    publication anyway.

    Nothing about the run's result, ranking, finalists or claim boundary
    depends on this file.
    """
    from traffic_sim.simulation.cost_ordered_search import (
        shadow_from_pilot_selection,
    )
    from traffic_sim.simulation.search_workspace import load_search_workspace

    directory = Path(root) / spec.search_id
    workspace = load_search_workspace(directory, verify=False)
    records = [
        record for record in workspace.manifest.get("artifacts", ())
        if record.get("kind") == "monthly_pilot_selection"
    ]
    if len(records) != 1:
        raise ValueError(
            "cost-ordered shadow needs exactly one published pilot selection")
    payload = json.loads(
        (directory / str(records[0]["path"])).read_text(encoding="utf-8"))
    comparison = shadow_from_pilot_selection(
        payload,
        policy.pilot,
        search_content_key=spec.content_key,
        provider_identity={
            "schema": "deterministic_closure_disruption_v1",
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        },
        practical_equivalence_vehicle_hours=(
            policy.finalist.practical_equivalence_vehicle_hours
        ),
    )
    destination = directory / "cost-ordered-shadow.json"
    destination.write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return comparison


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--demand-archive",
        type=Path,
        help=(
            "Compatibility mode: one succeeded immutable demand archive "
            "covering every shortlist envelope. Omit to resolve/build and "
            "freeze one exact archive per shortlist envelope automatically."
        ),
    )
    parser.add_argument(
        "--demand-runs-root",
        type=Path,
        default=Path("runs"),
        help="Run registry searched for exact succeeded demand archives.",
    )
    parser.add_argument(
        "--demand-release-root",
        type=Path,
        default=Path("runs") / "monthly-demand-releases",
        help="Immutable envelope-to-archive release manifests.",
    )
    parser.add_argument(
        "--no-build-missing-demand",
        action="store_true",
        help="Fail instead of automatically calibrating missing envelopes.",
    )
    parser.add_argument(
        "--baseline-trip-duration-p99-s",
        type=int,
        required=True,
        help="Frozen baseline trip-duration p99 used to derive warm-up.",
    )
    parser.add_argument(
        "--screening-mode",
        choices=("proxy", "bounded-exhaustive", "independent-exhaustive",
                 "independent-cost-ordered-exact"),
        default="proxy",
        help=(
            "independent-cost-ordered-exact runs the SAME exhaustive "
            "screening and additionally replays the PR E cost-ordered scan "
            "against the run's own evidence, publishing a shadow comparison. "
            "It is SHADOW MODE: it changes no ranking, no finalist set and no "
            "claim, and it is not activated until the equivalence gate has "
            "passed on a named benchmark."
        ),
    )
    parser.add_argument(
        "--bounded-exhaustive-cap",
        type=int,
        default=12,
        help="Hard candidate cap when --screening-mode=bounded-exhaustive.",
    )
    parser.add_argument(
        "--independent-exhaustive-candidate-cap",
        type=int,
        default=100_000,
        help="Hard parent-schedule cap for exact independent daily search.",
    )
    parser.add_argument(
        "--independent-exhaustive-daily-cap",
        type=int,
        default=10_000,
        help="Hard unique daily SUMO-unit cap for exact independent search.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline-cache", type=Path)
    parser.add_argument(
        "--daily-result-cache",
        type=Path,
        default=Path("runs") / "closure-search-daily-results",
        help=(
            "Content-addressed independent-day evidence cache used when the "
            "search spec selects independent_daily_reset_v1."
        ),
    )
    parser.add_argument("--seed-workers", type=int, default=1)
    parser.add_argument(
        "--daily-workers",
        type=int,
        default=3,
        help=(
            "Isolated SUMO/TraCI worker interpreters for independent daily "
            "units. Ignored by continuous searches."
        ),
    )
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--warm-execution",
        dest="warm_execution",
        action="store_true",
        help=(
            "Use the validated SUMO warm-state path. Cache misses bootstrap "
            "a provisional prefix; any unusable warm attempt falls back to "
            "the unchanged cold path (default)."
        ),
    )
    execution.add_argument(
        "--cold-execution",
        dest="warm_execution",
        action="store_false",
        help="Disable warm-state execution and run every observation cold.",
    )
    parser.set_defaults(warm_execution=True)
    parser.add_argument("--workspace-wait-s", type=float, default=3600.0,
                        help="Seconds to wait for the shared demand "
                             "workspace (a horizon pre-warm or the web "
                             "app may hold it) before giving up.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.baseline_trip_duration_p99_s <= 0:
        raise SystemExit("--baseline-trip-duration-p99-s must be positive")
    if args.bounded_exhaustive_cap <= 0:
        raise SystemExit("--bounded-exhaustive-cap must be positive")
    if (
        args.independent_exhaustive_candidate_cap <= 0
        or args.independent_exhaustive_daily_cap <= 0
    ):
        raise SystemExit("independent exhaustive caps must be positive")
    if args.seed_workers < 1:
        raise SystemExit("--seed-workers must be at least 1")
    if args.daily_workers < 1:
        raise SystemExit("--daily-workers must be at least 1")
    if args.warm_execution and args.seed_workers != 1:
        raise SystemExit(
            "warm execution currently requires --seed-workers 1 because the "
            "production TraCI controller owns one active connection; use "
            "--cold-execution for parallel seed workers")
    approved = approved_seed_workers()
    if args.seed_workers > approved and os.environ.get(
            "MONTHLY_SEED_WORKER_BENCHMARK") == "1":
        # The benchmark that produces the approval must be able to exercise
        # the unapproved path exactly once, on purpose, and say so out loud.
        print(
            f"benchmark mode: running {args.seed_workers} SUMO workers "
            f"without an approval record (approved: {approved})",
            file=sys.stderr,
        )
        approved = args.seed_workers
    if args.seed_workers > approved:
        raise SystemExit(
            f"--seed-workers {args.seed_workers} exceeds the {approved} "
            "approved by the recorded resource benchmark "
            f"({SEED_WORKER_BENCHMARK_RECORD}). Run "
            "benchmark_seed_workers.py to establish identical evidence and a "
            "measured peak RSS first; parallel SUMO stays closed until then."
        )
    if args.daily_workers > approved:
        raise SystemExit(
            f"--daily-workers {args.daily_workers} exceeds the {approved} "
            "approved isolated SUMO worker count"
        )
    if args.seed_workers > 1 and args.daily_workers > 1:
        raise SystemExit(
            "parallel seed workers and parallel daily workers cannot be "
            "combined; that would multiply the approved SUMO process ceiling"
        )
    # Read the inputs and SIZE the search before anything expensive exists.
    # The exact preflight is read-only calendar arithmetic: it needs no demand
    # workspace, no network identity and no simulation stack. Running it here
    # means an over-budget search — the late surprise the scaling plan set out
    # to remove — is refused in about 22 MiB, instead of after ~110 MiB of
    # numeric imports and a wait for the shared demand lock.
    try:
        spec = load_closure_search_spec(args.spec)
        policy = MonthlySearchPolicy.from_dict(_read(args.policy))
        independent_preflight = (
            _independent_exhaustive_preflight(
                spec,
                maximum_candidates=(
                    args.independent_exhaustive_candidate_cap
                ),
                maximum_daily_units=args.independent_exhaustive_daily_cap,
                baseline_trip_duration_p99_s=(
                    args.baseline_trip_duration_p99_s
                ),
            )
            if args.screening_mode in {"independent-exhaustive",
                                       "independent-cost-ordered-exact"}
            else None
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc

    runner = None
    (ArchivedDemandSumoRunner, MonthlyDemandResolverRunner,
     recover_live_demand_release) = _simulation_backends()
    recovered = recover_live_demand_release()
    if recovered is not None:
        print(
            "restored the live demand release left behind by a killed run "
            f"({len(recovered.get('entries', []))} products, "
            f"{len(recovered.get('trees', []))} directories)",
            file=sys.stderr,
        )
    # A search owns the shared demand workspace for hours: it rebuilds
    # envelopes into sumo/ and snapshots the live release around them. The
    # web app and a horizon pre-warm run take the same lock, so this waits
    # for whichever of them is mid-build instead of interleaving files with
    # it - and says whose job it is waiting for.
    workspace = WorkspaceLock(f"run_monthly_closure_search {os.getpid()}")
    if not workspace.acquire(timeout=args.workspace_wait_s, poll_s=10.0):
        raise SystemExit(
            f"demand workspace busy: {workspace.holder_description()}; "
            "wait for it, stop it, or raise --workspace-wait-s")
    try:
        study_key = _digest({
            "kind": "monthly_closure_search_study",
            "search_content_key": spec.content_key,
            "policy_content_key": policy.content_key,
            "demand_release_id": spec.demand_build_id,
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        })
        runner_options = {
            "baseline_trip_duration_p99_s": (
                args.baseline_trip_duration_p99_s
            ),
            "study_provenance_key": study_key,
            "seed_workers": args.seed_workers,
            "include_disruption": (
                policy.objective_method == "closure_cost_v1"
            ),
        }
        if args.warm_execution:
            # Construction is process-free. TraCI is resolved and SUMO starts
            # only if an eligible observation actually enters the warm path.
            # A cache miss bootstraps a provisional prefix. The runner retains
            # its fail-closed cold fallback if that bootstrap or any later
            # warm-path step is unusable.
            from traffic_sim.simulation.warm_state_boundary import (
                WarmPrefixController,
            )
            runner_options.update({
                "warm_execution": True,
                "boundary_controller": WarmPrefixController(),
            })
        if args.baseline_cache is not None:
            runner_options["cache_root"] = args.baseline_cache
        if (
            spec.interday_policy == "independent_daily_reset_v1"
            and args.demand_archive is not None
        ):
            raise ValueError(
                "independent daily searches require exact per-date demand "
                "resolution; --demand-archive compatibility mode is not valid"
            )
        if args.demand_archive is not None:
            runner = ArchivedDemandSumoRunner(
                spec,
                archive=args.demand_archive,
                **runner_options,
            )
        else:
            resolved_runner = MonthlyDemandResolverRunner(
                spec,
                runs_root=args.demand_runs_root,
                release_root=args.demand_release_root,
                build_missing=not args.no_build_missing_demand,
                envelope_policy=(
                    INDEPENDENT_DAILY_ENVELOPE_POLICY
                    if spec.interday_policy == "independent_daily_reset_v1"
                    else EnvelopePolicy()
                ),
                **runner_options,
            )
            if spec.interday_policy == "independent_daily_reset_v1":
                daily_runner = (
                    IsolatedDailySumoRunner(
                        resolved_runner,
                        unit_workers=args.daily_workers,
                    )
                    if args.daily_workers > 1
                    else resolved_runner
                )
                runner = IndependentDailyRunner(
                    spec,
                    daily_runner=daily_runner,
                    cache_root=args.daily_result_cache,
                )
            else:
                runner = resolved_runner
        if args.screening_mode == "proxy":
            # Screen with EXACTLY the frozen v4 campaign policy. Until the
            # fresh gate passes, the new shortlist remains release-blocked;
            # road_domain_status matches the
            # validation runner (in_domain); per-worksite coverage scoring
            # is a future refinement the gate did not cover.
            screen_builder = lambda path: _proxy_screen_builder(
                path,
                road_domain_status="in_domain",
            )
        elif args.screening_mode == "bounded-exhaustive":
            screen_builder = lambda path: _bounded_exhaustive_builder(
                path,
                maximum_candidates=args.bounded_exhaustive_cap,
            )
        else:
            if spec.interday_policy != "independent_daily_reset_v1":
                raise ValueError(
                    f"--screening-mode={args.screening_mode} requires an "
                    "independent daily search spec"
                )
            screen_builder = lambda path: _independent_exhaustive_builder(
                path,
                maximum_candidates=(
                    args.independent_exhaustive_candidate_cap
                ),
                maximum_daily_units=args.independent_exhaustive_daily_cap,
                baseline_trip_duration_p99_s=(
                    args.baseline_trip_duration_p99_s
                ),
                preflight_report=independent_preflight,
            )
        result = run_monthly_search(
            spec,
            policy,
            runner=runner,
            screen_builder=screen_builder,
            root=args.root,
        )
        if args.screening_mode == "independent-cost-ordered-exact":
            # SHADOW ONLY. The exhaustive answer above is the result; this
            # replays the cost-ordered scan against the same evidence and
            # publishes the comparison. It changes nothing about the run.
            shadow = _publish_cost_ordered_shadow(
                spec, policy, root=args.root)
            print(
                "cost-ordered shadow: "
                f"equivalent={shadow.get('equivalent')}; "
                f"verified={shadow.get('verified_candidates')}/"
                f"{shadow.get('ordered_candidates')}; "
                f"saved={shadow.get('sumo_verifications_saved')}",
                file=sys.stderr,
            )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
        workspace.release()

    boundary = result.get("claim_boundary", {})
    print(
        f"Monthly closure search {spec.search_id}: {result['status']}; "
        f"winner={result.get('winner_id')}; "
        f"scope={boundary.get('best_result_scope')}; "
        f"global_best_claim_allowed={boundary.get('global_best_claim_allowed')}; "
        f"ui_exposure_allowed={boundary.get('ui_exposure_allowed')}"
    )


if __name__ == "__main__":
    main()
