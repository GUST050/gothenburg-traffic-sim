#!/usr/bin/env python3
"""Run or resume a robust recurring closure search with archived SUMO demand."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
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
from traffic_sim.simulation import unit_budget
from traffic_sim.simulation.unit_budget import DailyUnitBudget
from traffic_sim.simulation.seed_worker_budget import (
    SEED_WORKER_BENCHMARK_RECORD,
    approved_seed_workers,
)
from traffic_sim.simulation.search_workspace import DEFAULT_ROOT


# `-i` alone holds only the IDLE-sleep assertion. A multi-hour search also
# has to survive the disk idle-sleep path (`-m`) and the plain system-sleep
# path (`-s`, honoured only on AC power), so assert all three rather than
# discovering at hour six that one of them was never held. `-d` is
# deliberately NOT asserted: keeping the panel lit burns power and buys no
# compute, and the display sleeping has never paused a unit.
KEEP_AWAKE_FLAGS = ("-i", "-m", "-s")


def _on_ac_power() -> bool | None:
    """Return True on AC, False on battery, None when it cannot be read.

    This is not decoration.  `caffeinate -s` is documented as valid ONLY on
    AC power, so on battery the strongest assertion this process can make is
    silently weaker than the flags suggest.  A long search deserves to be
    told that at start rather than to be found asleep.
    """
    executable = shutil.which("pmset")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "-g", "batt"], capture_output=True, text=True,
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.lower()
    if "'ac power'" in text or "drawing from 'ac power'" in text:
        return True
    if "'battery power'" in text:
        return False
    return None


def _start_macos_keep_awake() -> subprocess.Popen[bytes] | None:
    """Hold idle/disk/system sleep assertions while this monthly CLI is alive.

    HONEST LIMIT, stated here because no flag can remove it: caffeinate
    asserts against SOFTWARE sleep paths only.  Closing the lid of a Mac
    laptop triggers clamshell sleep in the power-management layer below any
    assertion this process can hold, unless the machine is on AC power with
    an external display attached.  The operator instruction "keep it plugged
    in and the lid open" is therefore part of the contract, not a
    superstition, and the warning below says so at the one moment it can
    still be acted on.
    """
    if sys.platform != "darwin":
        return None
    executable = shutil.which("caffeinate")
    if executable is None:
        print(
            "warning: caffeinate is unavailable; this long search cannot "
            "prevent laptop sleep",
            file=sys.stderr,
        )
        return None
    try:
        process = subprocess.Popen(
            [executable, *KEEP_AWAKE_FLAGS, "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(
            f"warning: could not start caffeinate ({exc}); this long search "
            "cannot prevent laptop sleep",
            file=sys.stderr,
        )
        return None
    print(
        f"keep-awake: {executable} {' '.join(KEEP_AWAKE_FLAGS)} "
        f"-w {os.getpid()}",
        file=sys.stderr,
    )
    on_ac = _on_ac_power()
    if on_ac is False:
        print(
            "warning: this machine is on BATTERY power; caffeinate -s is "
            "honoured only on AC, so system sleep can still interrupt this "
            "search. Connect the charger.",
            file=sys.stderr,
        )
    elif on_ac is None:
        print(
            "warning: power source could not be read; connect the charger "
            "before leaving this search unattended",
            file=sys.stderr,
        )
    print(
        "note: closing the laptop lid suspends the machine below any "
        "caffeinate assertion. Leave the lid OPEN for the whole run.",
        file=sys.stderr,
    )
    return process


def _stop_macos_keep_awake(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


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
    budget: "DailyUnitBudget | None" = None,
    resume_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stream exact independent enumeration in transactional parent pages.

    A declared budget limits NEW unique units in this invocation. State from a
    previous invocation is cumulative, but the per-leg counter starts at zero.
    A parent is classified and committed only after every one of its units has
    been envelope-checked. If it would cross the leg budget, none of that
    parent's state is retained and the cursor remains on the previous complete
    parent. Without a budget the legacy hard cap is unchanged.
    """
    spec = load_closure_search_spec(spec_path)
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "independent exhaustive screening requires the independent policy"
        )
    if (
        budget is not None
        and maximum_candidates != budget.maximum_parent_schedules
    ):
        raise ValueError(
            "independent exhaustive parent cap differs from the declared "
            "budget"
        )
    parent_limit = (
        budget.maximum_parent_schedules
        if budget is not None else maximum_candidates
    )
    effective_total_limit = (
        budget.maximum_total_daily_units
        if budget is not None else maximum_daily_units
    )
    report = preflight_report or _independent_exhaustive_preflight(
        spec,
        budget=budget,
        maximum_candidates=parent_limit,
        maximum_daily_units=effective_total_limit,
        baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
    )
    if report is not None:
        if report.search_content_key != spec.content_key:
            raise ValueError(
                "independent exhaustive preflight belongs to another search")
        if (report.parent_schedule_limit != parent_limit
                or report.daily_unit_limit != effective_total_limit):
            raise ValueError(
                "independent exhaustive preflight uses different resource caps")
    from traffic_sim.simulation.independent_daily import daily_unit_records

    source_year = 2027 if spec.source == "forecast" else 2025
    source_start = date(source_year, 1, 1)
    source_end = date(source_year + 1, 1, 1)

    unavailable_units: dict[str, str] = {}
    evaluated_units: set[str] = set()
    unavailable_candidates: list[dict[str, Any]] = []
    eligible_ids: list[str] = []
    candidate_count = 0
    last_complete_parent: str | None = None
    resume_after: str | None = None
    resumed = True
    if resume_state is not None:
        if not isinstance(resume_state, Mapping):
            raise ValueError(
                "independent screening checkpoint must be an object")
        if resume_state.get("schema") != (
                "independent_daily_enumeration_checkpoint_v1"):
            raise ValueError("independent screening checkpoint schema is invalid")
        if resume_state.get("search_content_key") != spec.content_key:
            raise ValueError(
                "independent screening checkpoint belongs to another search")
        if budget is None or resume_state.get(
                "budget_content_key") != budget.content_key:
            raise ValueError("independent screening checkpoint budget differs")
        resume_after_raw = resume_state.get("resume_after_parent_id")
        if not isinstance(resume_after_raw, str) or not resume_after_raw:
            raise ValueError("independent screening checkpoint has no resume cursor")
        resume_after = resume_after_raw
        resumed = False
        raw_units = resume_state.get("evaluated_unit_ids")
        raw_eligible = resume_state.get("eligible_schedule_ids")
        raw_unavailable_units = resume_state.get("unavailable_units")
        raw_unavailable_candidates = resume_state.get("unavailable_candidates")
        if not isinstance(raw_units, list) or any(
                not isinstance(item, str) or not item for item in raw_units):
            raise ValueError("checkpoint evaluated units are invalid")
        if len(raw_units) != len(set(raw_units)):
            raise ValueError("checkpoint repeats an evaluated unit")
        if not isinstance(raw_eligible, list) or any(
                not isinstance(item, str) or not item for item in raw_eligible):
            raise ValueError("checkpoint eligible schedules are invalid")
        if len(raw_eligible) != len(set(raw_eligible)):
            raise ValueError("checkpoint repeats an eligible schedule")
        if not isinstance(raw_unavailable_units, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in raw_unavailable_units.items()):
            raise ValueError("checkpoint unavailable units are invalid")
        if not isinstance(raw_unavailable_candidates, list) or any(
                not isinstance(item, Mapping)
                for item in raw_unavailable_candidates):
            raise ValueError("checkpoint unavailable candidates are invalid")
        evaluated_units.update(raw_units)
        unavailable_units.update({
            str(key): str(value)
            for key, value in raw_unavailable_units.items()
        })
        if not set(unavailable_units).issubset(evaluated_units):
            raise ValueError("checkpoint has an unavailable unevaluated unit")
        eligible_ids.extend(raw_eligible)
        unavailable_candidates.extend(
            dict(item) for item in raw_unavailable_candidates)
        candidate_count = int(resume_state.get("parent_schedules", -1))
        if candidate_count < 1:
            raise ValueError("checkpoint parent count is invalid")
        unavailable_ids = [
            str(item.get("schedule_id", ""))
            for item in unavailable_candidates
        ]
        classified_ids = [*eligible_ids, *unavailable_ids]
        if (
            any(not item for item in unavailable_ids)
            or len(classified_ids) != candidate_count
            or len(classified_ids) != len(set(classified_ids))
        ):
            raise ValueError("checkpoint parent classification is inconsistent")
        last_complete_parent = resume_after

    leg_new_units = 0
    prefix_units: set[str] = set()
    prefix_schedule_ids: list[str] = []
    budget_stop: dict[str, Any] | None = None
    for schedule in iter_closure_schedules(spec):
        if resume_after and not resumed:
            records = list(daily_unit_records(spec, schedule))
            unit_ids = [unit_id for unit_id, _identity, _build in records]
            if len(unit_ids) != len(set(unit_ids)):
                raise ValueError("independent parent repeats a daily unit")
            prefix_units.update(unit_ids)
            prefix_schedule_ids.append(schedule.schedule_id)
            if schedule.schedule_id == resume_after:
                unavailable_ids = [
                    str(item["schedule_id"])
                    for item in unavailable_candidates
                ]
                if prefix_units != evaluated_units:
                    raise ValueError("checkpoint evaluated-unit prefix differs")
                if set(prefix_schedule_ids) != set(
                        [*eligible_ids, *unavailable_ids]):
                    raise ValueError("checkpoint parent prefix differs")
                resumed = True
            continue
        next_parent_count = candidate_count + 1
        if next_parent_count > parent_limit:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{parent_limit} parent schedules, above the explicit "
                f"cap {parent_limit}"
            )
        records = list(daily_unit_records(spec, schedule))
        parent_units = [unit_id for unit_id, _identity, _build in records]
        if len(parent_units) != len(set(parent_units)):
            raise ValueError("independent parent repeats a daily unit")
        new_records = [
            (unit_id, build)
            for unit_id, _identity, build in records
            if unit_id not in evaluated_units
        ]
        parent_new_count = len(new_records)
        if budget is not None:
            crossed = unit_budget.exceeded(
                budget,
                new_daily_units=leg_new_units + parent_new_count,
                total_daily_units=len(evaluated_units) + parent_new_count,
            )
            if crossed == "maximum_total_daily_units":
                raise ValueError(
                    "independent exhaustive screening exceeds the declared "
                    f"total daily-unit safety limit "
                    f"{budget.maximum_total_daily_units}"
                )
            if crossed == "maximum_daily_units":
                if leg_new_units == 0:
                    raise ValueError(
                        f"one parent requires {parent_new_count} new daily "
                        f"units, above the per-invocation budget "
                        f"{budget.maximum_daily_units}"
                    )
                budget_stop = {
                    "crossed": crossed,
                    "abandoned_parent_id": schedule.schedule_id,
                }
                break
        elif len(evaluated_units) + parent_new_count > maximum_daily_units:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{maximum_daily_units} unique daily SUMO units, above "
                f"the explicit cap {maximum_daily_units}"
            )
        parent_unavailable: dict[str, str] = {}
        for unit_id, build in new_records:
            daily = build()
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
                parent_unavailable[unit_id] = str(exc)
                continue
            envelope_start = date.fromisoformat(envelope.scenario_start[:10])
            envelope_end = date.fromisoformat(envelope.scenario_end[:10])
            if envelope_start < source_start or envelope_end > source_end:
                parent_unavailable[unit_id] = (
                    "exact warm-up/recovery envelope lies outside the "
                    f"downloaded {source_year} {spec.source} demand year"
                )
        # Commit the parent atomically only after all new units were checked.
        evaluated_units.update(unit_id for unit_id, _build in new_records)
        unavailable_units.update(parent_unavailable)
        leg_new_units += parent_new_count
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
        candidate_count = next_parent_count
        last_complete_parent = schedule.schedule_id
    if resume_after and not resumed:
        raise ValueError("checkpoint resume cursor is absent from the search")
    if budget_stop is not None:
        if last_complete_parent is None:
            raise ValueError("budget cannot pause before one complete parent")
        state = unit_budget.BudgetState(
            daily_units=len(evaluated_units),
            leg_daily_units=leg_new_units,
            parent_schedules=candidate_count,
            status=unit_budget.INCOMPLETE_STATUS,
            stopped_by=str(budget_stop["crossed"]),
            resume_after_parent_id=last_complete_parent,
        )
        checkpoint_state = {
            "schema": "independent_daily_enumeration_checkpoint_v1",
            "search_content_key": spec.content_key,
            "budget_content_key": budget.content_key,
            "resume_after_parent_id": last_complete_parent,
            "evaluated_unit_ids": sorted(evaluated_units),
            "unavailable_units": dict(sorted(unavailable_units.items())),
            "eligible_schedule_ids": list(eligible_ids),
            "unavailable_candidates": unavailable_candidates,
            "parent_schedules": candidate_count,
        }
        resume_token = _digest({
            "checkpoint": checkpoint_state,
            "budget": budget.to_dict(),
        })
        return {
            "schema_version": 1,
            "kind": "monthly_closure_screening_checkpoint",
            "search": spec.to_dict(),
            "status": "paused",
            "exhaustive": False,
            "resume_token": resume_token,
            "budget": budget.to_dict(),
            "budget_state": state.to_dict(),
            "budget_message": unit_budget.describe(state, budget),
            "checkpoint_state": checkpoint_state,
            "abandoned_parent_id": budget_stop["abandoned_parent_id"],
        }
    if budget is None and report is not None and (
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
    if budget is not None:
        state = unit_budget.BudgetState(
            daily_units=len(evaluated_units),
            leg_daily_units=leg_new_units,
            parent_schedules=candidate_count,
            status=unit_budget.COMPLETE_STATUS)
        payload["budget_state"] = state.to_dict()
        payload["budget"] = budget.to_dict()
        payload["exhaustive"] = True
        payload["budget_message"] = unit_budget.describe(state, budget)
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


class _IndependentExhaustiveScreenBuilder:
    """Callable adapter exposing explicit checkpoint continuation."""

    def __init__(
        self,
        *,
        maximum_candidates: int,
        maximum_daily_units: int,
        baseline_trip_duration_p99_s: int,
        preflight_report: ClosureSearchPreflight | None,
        budget: DailyUnitBudget | None,
    ) -> None:
        self.maximum_candidates = maximum_candidates
        self.maximum_daily_units = maximum_daily_units
        self.baseline_trip_duration_p99_s = baseline_trip_duration_p99_s
        self.preflight_report = preflight_report
        self.budget = budget

    def _build(
        self,
        path: Path,
        resume_state: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return _independent_exhaustive_builder(
            path,
            maximum_candidates=self.maximum_candidates,
            maximum_daily_units=self.maximum_daily_units,
            baseline_trip_duration_p99_s=(
                self.baseline_trip_duration_p99_s),
            preflight_report=self.preflight_report,
            budget=self.budget,
            resume_state=resume_state,
        )

    def __call__(self, path: Path) -> dict[str, Any]:
        return self._build(path, None)

    def resume(
        self,
        path: Path,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = checkpoint.get("checkpoint_state")
        if not isinstance(state, Mapping):
            raise ValueError("screening checkpoint has no resumable state")
        return self._build(path, state)


def _independent_exhaustive_preflight(
    spec,
    *,
    budget: "DailyUnitBudget | None" = None,
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
    if (
        budget is not None
        and maximum_candidates != budget.maximum_parent_schedules
    ):
        raise ValueError(
            "independent exhaustive parent cap differs from the declared "
            "budget"
        )
    parent_limit = (
        budget.maximum_parent_schedules
        if budget is not None else maximum_candidates
    )
    try:
        report = preflight(
            spec,
            baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
            envelope_policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
            parent_schedule_limit=parent_limit,
            daily_unit_limit=maximum_daily_units,
        )
    except UnsupportedPreflightSpec:
        # The streaming path still supports legacy allocation policies whose
        # exact closed-form preflight is deliberately unavailable.  Preserve
        # that compatibility and enforce both caps while streaming below;
        # supported policies get the earlier, allocation-free refusal.
        return None
    if report.parent_schedule_count > parent_limit:
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.parent_schedule_count} parent schedules, above the "
            f"explicit cap {parent_limit}"
        )
    if report.unique_daily_unit_count > maximum_daily_units and budget is None:
        # Without a declared budget this stays fail-closed, exactly as before.
        # WITH one, refusing here would defeat the point: the budget's contract
        # is to PAUSE mid-enumeration with a resumable cursor, and a search
        # refused before it starts can never pause. The streaming loop enforces
        # it instead.
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.unique_daily_unit_count} unique daily SUMO units, "
            f"above the explicit cap {maximum_daily_units}"
        )
    if report.parent_schedule_count == 0:
        raise ValueError("independent exhaustive search has no legal schedules")
    return report


def _cost_source_for(spec, runner, args=None, *, daily_cost_cache=None):
    """Build the deterministic cost source for real cost-first execution.

    Takes either the parsed CLI ``args`` or an explicit ``daily_cost_cache``,
    so a benchmark can build the SAME cost source the product uses without
    fabricating an argparse namespace. One implementation, two callers.

    Prices a parent by pricing its daily units through the SAME calibrated
    archives the simulation would use, resolved by the demand resolver. No SUMO
    process is started to do it.
    """
    from traffic_sim.simulation.cost_ordered_execution import (
        IndependentDailyCostSource,
    )
    from traffic_sim.simulation.deterministic_disruption import (
        DailyCostCache,
        NetworkCostModel,
    )

    daily_runner = getattr(runner, "daily_runner", None)
    units_for = getattr(runner, "daily_units_for", None)
    if daily_runner is None or units_for is None:
        raise ValueError(
            "cost-ordered execution requires the independent daily runner; "
            "it prices a parent from its daily units")
    resolver = getattr(daily_runner, "deterministic_disruption_provider", None)
    if resolver is None:
        # IsolatedDailySumoRunner wraps the resolver for parallel execution;
        # the prices come from the resolver underneath it, never from a worker.
        inner = getattr(daily_runner, "runner", None)
        resolver = getattr(inner, "deterministic_disruption_provider", None)
    if resolver is None:
        raise ValueError(
            "cost-ordered execution needs a demand resolver that can produce a "
            "process-free disruption provider")

    # One network model for the whole search: the adjacency and free-flow
    # tables are per-network, and rebuilding them per candidate would cost more
    # than the simulations being avoided.
    network = NetworkCostModel()
    if daily_cost_cache is None:
        if args is None:
            raise ValueError(
                "a cost source needs a daily cost cache location")
        daily_cost_cache = args.daily_cost_cache
    cache = DailyCostCache(Path(daily_cost_cache))
    return IndependentDailyCostSource(
        spec,
        daily_units_for=units_for,
        provider_for=lambda unit_schedule: resolver(
            unit_schedule, cache=cache, network=network),
        cache=cache,
    )


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
    parser.add_argument(
        "--daily-cost-cache",
        type=Path,
        default=Path("runs") / "closure-search-daily-costs",
        help=(
            "Content-addressed DETERMINISTIC daily cost cache used by "
            "--screening-mode=independent-cost-ordered-exact. Separate from "
            "--daily-result-cache because a price and a simulation outcome "
            "have different identities: a price binds routes, network and "
            "costing sources, an outcome also binds the SUMO runtime."
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
    parser.add_argument(
        "--max-active-sumo-slots",
        type=int,
        default=8,
        help=(
            "One declared outer/inner SUMO process budget. The product of "
            "--daily-workers and --seed-workers may not exceed this value "
            "(default 8; evidence budget, not a speed claim)."
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
    parser.add_argument("--daily-unit-budget", type=int, default=None,
                        help="Maximum NEW unique daily units enumerated in "
                             "one invocation. Crossing it pauses at a complete "
                             "parent and the counter resets on resume. Without "
                             "this flag the legacy hard cap applies unchanged.")
    parser.add_argument(
        "--daily-unit-total-cap",
        type=int,
        default=unit_budget.DEFAULT_TOTAL_DAILY_UNIT_LIMIT,
        help="Hard cumulative daily-unit safety cap for the complete search.",
    )
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
    max_active_slots = args.max_active_sumo_slots
    if max_active_slots < 1:
        raise SystemExit("--max-active-sumo-slots must be at least 1")
    if args.daily_workers * args.seed_workers > max_active_slots:
        raise SystemExit(
            "daily-workers * seed-workers exceeds the declared "
            "--max-active-sumo-slots process budget"
        )
    if args.seed_workers > 1 and args.daily_workers > 1:
        raise SystemExit(
            "parallel seed workers and parallel daily workers cannot yet be "
            "combined: isolated daily children currently own fresh SUMO "
            "processes and shared baseline-cache publication has not passed "
            "the nested-concurrency equivalence gate"
        )
    if args.daily_unit_budget is not None and args.daily_unit_budget < 1:
        raise SystemExit("--daily-unit-budget must be positive")
    if (
        args.daily_unit_budget is not None
        and args.screening_mode not in (
            "independent-exhaustive",
            "independent-cost-ordered-exact",
        )
    ):
        raise SystemExit(
            "--daily-unit-budget requires --screening-mode="
            "independent-exhaustive or independent-cost-ordered-exact"
        )
    if args.daily_unit_total_cap < 1:
        raise SystemExit("--daily-unit-total-cap must be positive")
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
                # The preflight REPORTS against the effective limit. With a
                # declared budget that limit is the budget, so a search the
                # budget admits is not refused before it starts by the very cap
                # the budget replaces.
                maximum_daily_units=(
                    args.daily_unit_total_cap
                    if args.daily_unit_budget is not None
                    else args.independent_exhaustive_daily_cap),
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
    keep_awake_process = _start_macos_keep_awake()
    try:
        # Only a run that owns the workspace may load or mutate the demand
        # backend. A refused lock therefore stays on the same light import
        # path as a refused preflight, and recovery cannot race a live owner.
        (ArchivedDemandSumoRunner, MonthlyDemandResolverRunner,
         recover_live_demand_release) = _simulation_backends()
        recovered = recover_live_demand_release()
        if recovered is not None:
            print(
                "restored the live demand release left behind by a killed "
                f"run ({len(recovered.get('entries', []))} products, "
                f"{len(recovered.get('trees', []))} directories)",
                file=sys.stderr,
            )
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
            declared_budget = (
                DailyUnitBudget(
                    maximum_daily_units=args.daily_unit_budget,
                    maximum_total_daily_units=args.daily_unit_total_cap,
                    maximum_parent_schedules=(
                        args.independent_exhaustive_candidate_cap))
                if args.daily_unit_budget is not None else None)
            screen_builder = _IndependentExhaustiveScreenBuilder(
                maximum_candidates=(
                    args.independent_exhaustive_candidate_cap
                ),
                maximum_daily_units=args.independent_exhaustive_daily_cap,
                baseline_trip_duration_p99_s=(
                    args.baseline_trip_duration_p99_s
                ),
                preflight_report=independent_preflight,
                budget=declared_budget,
            )
        cost_source = None
        if args.screening_mode == "independent-cost-ordered-exact":
            # REAL cost-first execution: candidates are priced from the
            # calibrated routes before anything is simulated, and SUMO runs
            # only for the ones the ordering boundary requires. The exhaustive
            # mode remains the untouched reference.
            cost_source = _cost_source_for(spec, runner, args)
        result = run_monthly_search(
            spec,
            policy,
            runner=runner,
            screen_builder=screen_builder,
            root=args.root,
            cost_source=cost_source,
        )
        if cost_source is not None:
            print(
                "cost-ordered execution: "
                f"priced {getattr(cost_source, 'computed_units', 0)} daily "
                f"units, {getattr(cost_source, 'cache_hits', 0)} cache hits",
                file=sys.stderr,
            )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        cleanup = getattr(runner, "cleanup", None)
        try:
            if callable(cleanup):
                cleanup()
        finally:
            try:
                _stop_macos_keep_awake(keep_awake_process)
            finally:
                workspace.release()

    if result.get("status") == "paused":
        print(json.dumps(result, sort_keys=True))
        return
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
