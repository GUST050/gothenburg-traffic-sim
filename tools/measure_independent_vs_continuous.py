"""Measure the frozen independent-vs-continuous question. Stage 4.

`validation/independent_vs_continuous_preregistration_v1.json` froze the
question, the 84 cases, the nine compared metrics and the tolerances before any
outcome existed. This is the harness that answers it — and it NEVER edits that
file. It reads it, binds to it by content key, and writes a separate outcome
record.

FOUR BUCKETS, REPORTED SEPARATELY, because they are four different facts and
collapsing them into one "did not run" would hide what was actually learned:

  * `unsupported_by_contract` — the case asks for a continuous closure longer
    than `_CONTINUOUS_MAX_WORKDAYS`, which `ClosureSearchSpec` refuses. There is
    no continuous counterfactual to compare against, so there is nothing to
    measure. This harness does NOT lift that ceiling: if a product requirement
    needs 45- or 90-workday continuous searches, that is a deliberate contract
    change with its own consequences, not an incidental benchmark fix.
  * `unpairable` — both policies are expressible but they do not describe the
    same closure (an overnight band the independent policy cannot express; work
    dates that differ because independent walks consecutive ELIGIBLE dates and
    continuous walks consecutive CALENDAR ones). Comparing them would compare
    two different closures.
  * `blocked_missing_demand` — the case is pairable and supported, but at least
    one required calibrated q10/q50/q90 archive is absent. The product's own
    `find_demand_archives` decides this, not a guess about filenames.
  * `measured` — both arms ran, and every compared metric is reported against
    its frozen tolerance.

WHAT IS MEASURABLE WITHOUT ANY DEMAND.  Candidate correspondence is a property
of the two calendars alone. The harness therefore always measures, for every
pairable case, which candidates the two arms have in common and which exist in
only one — the structural precondition for comparing rankings at all. A case
whose candidate sets barely overlap could not produce a meaningful rank
comparison even with perfect demand, and that is worth knowing before spending
a SUMO campaign on it.

WHAT IT WILL NOT DO.  It will not build demand, will not widen a tolerance,
will not extrapolate a 21-workday result into the 45/90 band, and will not
report a metric it did not read from a real run.

Run:
    python3 tools/measure_independent_vs_continuous.py --classify-only
    python3 tools/measure_independent_vs_continuous.py            # + measure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import iter_closure_schedules  # noqa: E402
from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402

OUTCOME_SCHEMA = "independent_vs_continuous_outcome_v1"

DEFAULT_REGISTRATION = (
    ROOT / "validation" / "independent_vs_continuous_preregistration_v1.json")
DEFAULT_OUTCOME = (
    ROOT / "validation" / "independent_vs_continuous_outcome_v1.json")
DEFAULT_RUNS_ROOT = ROOT / "runs"
DEFAULT_RELEASE_ROOT = ROOT / "runs" / "monthly-demand-releases"

#: The reference policy the pre-registration's own reproducible command names.
#: Both arms use the SAME policy — the comparison is between interday policies,
#: so anything else varying would confound it.
DEFAULT_POLICY = ROOT / "validation" / "monthly_search_policy_v2.json"

#: Enumeration cap. A case that exceeds it is recorded as such rather than
#: silently truncated: a partial candidate set would make the correspondence
#: statistics wrong in a direction that flatters the comparison.
MAXIMUM_ENUMERATED_CANDIDATES = 50_000

BUCKETS = (
    "measured",
    "blocked_missing_demand",
    "unpairable",
    "unsupported_by_contract",
)

#: Baseline trip-duration p99 the envelope builder needs. The product CLI's own
#: default; recorded in the outcome so the binding is explicit.
BASELINE_TRIP_DURATION_P99_S = 3600


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _content_key(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _relative(path: Path) -> str:
    path = Path(path)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _peak_rss_bytes() -> int:
    """Peak RSS of this process tree.

    `ru_maxrss` for children survives fork+exec and is what a SUMO campaign
    actually costs, so both are reported and the larger is the peak.
    """
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024
    return int(max(own, children)) * scale


def load_registration(path: Path = DEFAULT_REGISTRATION) -> dict[str, Any]:
    """Read the frozen pre-registration. Never written to, only bound to."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "independent_vs_continuous_preregistration_v1":
        raise SystemExit(f"not an independent-vs-continuous registration: {path}")
    if payload.get("status") != "frozen_before_outcome":
        raise SystemExit(
            f"the registration is not frozen ({payload.get('status')!r}); "
            f"refusing to measure against a moving target")
    return payload


def _spec_from_case(case: Mapping[str, Any], key: str) -> ClosureSearchSpec:
    """Rebuild an arm's spec from the frozen record, verifying its identity.

    The registration stores each spec WITH its content key. Rebuilding and
    re-deriving that key proves the harness is running the case that was
    registered, not a spec that drifted with the contract code.
    """
    raw = dict(case[key])
    declared = str(raw.pop("content_key", ""))
    spec = ClosureSearchSpec.from_dict(raw)
    if declared and spec.content_key != declared:
        raise SystemExit(
            f"{case['case_id']}: the frozen {key} no longer rebuilds to its "
            f"registered content key ({declared} -> {spec.content_key}); the "
            f"spec contract changed since the registration was frozen")
    return spec


def _schedule_shape(schedule) -> tuple:
    """What makes two schedules THE SAME CLOSURE across the two policies.

    Not the schedule_id — the two policies mint different ids for identical
    closures. The closure itself is its work dates and its daily clock.
    """
    return (
        tuple(item.work_date for item in schedule.intervals),
        schedule.daily_start,
        schedule.daily_end,
    )


def _enumerate(spec: ClosureSearchSpec) -> tuple[dict[tuple, str],
                                                 dict[str, Any], bool]:
    """Map every candidate's closure shape to its schedule id.

    The profile alongside it — how many days each candidate spans and how many
    minutes it actually schedules — is what explains a divergence rather than
    merely reporting one.
    """
    shapes: dict[tuple, str] = {}
    day_counts: dict[int, int] = {}
    minutes: set[int] = set()
    capped = False
    for count, schedule in enumerate(iter_closure_schedules(spec), start=1):
        if count > MAXIMUM_ENUMERATED_CANDIDATES:
            capped = True
            break
        shapes.setdefault(_schedule_shape(schedule), schedule.schedule_id)
        day_counts[schedule.day_count] = day_counts.get(schedule.day_count, 0) + 1
        minutes.add(int(schedule.scheduled_work_minutes))
    profile = {
        "day_count_histogram": {str(key): day_counts[key]
                                for key in sorted(day_counts)},
        "scheduled_work_minutes": sorted(minutes),
        "required_work_minutes": spec.required_work_minutes,
    }
    return shapes, profile, capped


def candidate_correspondence(case: Mapping[str, Any]) -> dict[str, Any]:
    """How much of the two candidate spaces is actually comparable.

    Needs no demand and no SUMO: it is a property of the two calendars. A
    ranking comparison is only meaningful over the candidates both arms
    contain, so this is measured for every pairable case whether or not the
    outcome comparison can run.
    """
    independent = _spec_from_case(case, "independent_spec")
    continuous = _spec_from_case(case, "continuous_spec")
    left, left_profile, left_capped = _enumerate(independent)
    right, right_profile, right_capped = _enumerate(continuous)
    shared = sorted(set(left) & set(right))
    return {
        "profile": {"independent": left_profile, "continuous": right_profile},
        "independent_candidate_count": len(left),
        "continuous_candidate_count": len(right),
        "paired_candidate_count": len(shared),
        "independent_only_count": len(set(left) - set(right)),
        "continuous_only_count": len(set(right) - set(left)),
        # The pre-registration's pairability test compares the FIRST schedule
        # each policy enumerates. That is necessary but not sufficient: two
        # policies can agree on the first closure and still search different
        # spaces, in which case an arm's WINNER may be a candidate the other
        # arm cannot express at all. Recorded per case so a rank comparison is
        # never read as more than a comparison over the shared subset.
        "candidate_space_identical": (set(left) == set(right)),
        "enumeration_capped": bool(left_capped or right_capped),
        "enumeration_cap": MAXIMUM_ENUMERATED_CANDIDATES,
        "pairs": [
            {"shape": {"work_dates": list(shape[0]),
                       "daily_start": shape[1],
                       "daily_end": shape[2]},
             "independent_id": left[shape],
             "continuous_id": right[shape]}
            for shape in shared
        ],
    }


def demand_availability(case: Mapping[str, Any], *,
                        runs_root: Path) -> dict[str, Any]:
    """Which required calibrated archives exist, per arm.

    Uses the product's own `find_demand_archives` against the product's own
    `DemandBuildSpec`, so a "missing" verdict here means exactly what a real
    run would find — not that a filename pattern failed to match.
    """
    from traffic_sim.simulation.envelope import (
        EnvelopePolicy,
        build_simulation_envelope,
        envelope_demand_spec,
        independent_daily_demand_spec,
    )
    from traffic_sim.simulation.independent_daily import (
        INDEPENDENT_DAILY_ENVELOPE_POLICY,
        daily_unit_records,
    )
    from traffic_sim.simulation.monthly_demand import find_demand_archives

    report: dict[str, Any] = {"arms": {}, "missing_total": 0}
    for arm, key in (("independent", "independent_spec"),
                     ("continuous", "continuous_spec")):
        spec = _spec_from_case(case, key)
        independent = spec.interday_policy == "independent_daily_reset_v1"
        policy = (INDEPENDENT_DAILY_ENVELOPE_POLICY if independent
                  else EnvelopePolicy())
        required: dict[str, Any] = {}
        seen_units: set[str] = set()
        for schedule in iter_closure_schedules(spec):
            # The independent policy resolves demand PER DAILY UNIT, so the
            # requirement is the union over units — asking for a whole-parent
            # archive would both invent a build the product never requests and
            # miss the reuse that makes the policy cheap.
            units = []
            if independent:
                for unit_id, _identity, build in daily_unit_records(spec,
                                                                    schedule):
                    if unit_id in seen_units:
                        continue
                    seen_units.add(unit_id)
                    units.append(build())
            else:
                units.append(schedule)
            for unit in units:
                envelope = build_simulation_envelope(
                    spec, unit,
                    baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
                    policy=policy)
                build = (independent_daily_demand_spec(spec, unit, envelope)
                         if independent
                         else envelope_demand_spec(spec, envelope))
                required.setdefault(build.build_key, build)
        present = sorted(
            key for key, build in required.items()
            if find_demand_archives(runs_root, build))
        missing = sorted(set(required) - set(present))
        report["arms"][arm] = {
            "required_build_count": len(required),
            "present_build_count": len(present),
            "missing_build_keys": missing[:20],
            "missing_build_count": len(missing),
        }
        report["missing_total"] += len(missing)
    return report


def classify(case: Mapping[str, Any], *, runs_root: Path,
             check_demand: bool = True) -> dict[str, Any]:
    """One case's bucket, with the reason that put it there."""
    record: dict[str, Any] = {
        "case_id": case["case_id"],
        "workday_count": case["workday_count"],
        "weekday_policy": case["weekday_policy"],
        "time_band": case["time_band"],
        "road": case["road"],
        "demand_level": case["demand_level"],
    }
    if not case.get("continuous_expressible", False):
        record.update({
            "bucket": "unsupported_by_contract",
            "reason": (
                f"ClosureSearchSpec refuses a continuous closure of "
                f"{case['workday_count']} workdays, so no continuous "
                f"counterfactual exists to compare against"),
            "contract_reasons": list(case.get("unpairable_reasons", ())),
        })
        return record
    if not case.get("pairable", False):
        record.update({
            "bucket": "unpairable",
            "reason": (
                "the two policies do not describe the same closure for this "
                "case"),
            "contract_reasons": list(case.get("unpairable_reasons", ())),
        })
        return record

    record["correspondence"] = candidate_correspondence(case)
    if not check_demand:
        record.update({"bucket": "not_evaluated",
                       "reason": "demand availability was not checked"})
        return record
    demand = demand_availability(case, runs_root=runs_root)
    record["demand"] = demand
    if demand["missing_total"]:
        record.update({
            "bucket": "blocked_missing_demand",
            "reason": (
                f"{demand['missing_total']} required calibrated q10/q50/q90 "
                f"archives are absent under {_relative(runs_root)}"),
        })
        return record
    record.update({"bucket": "measurable",
                   "reason": "both arms have every required archive"})
    return record


# --------------------------------------------------------------------------
# The measured arm.
# --------------------------------------------------------------------------


def _run_arm(case: Mapping[str, Any], arm: str, *, policy, runs_root: Path,
             release_root: Path, workspace_root: Path) -> dict[str, Any]:
    """Run one arm end to end and return everything the comparison needs.

    Both arms go through the SAME `run_monthly_search` with the SAME policy;
    only the spec's interday policy — and therefore the runner and the screen
    builder the contracts demand for it — differs. That is the whole point of
    the comparison, so nothing else may vary.
    """
    from traffic_sim.simulation.envelope import EnvelopePolicy
    from traffic_sim.simulation.independent_daily import (
        INDEPENDENT_DAILY_ENVELOPE_POLICY,
        IndependentDailyRunner,
    )
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner
    from traffic_sim.simulation.monthly_search import run_monthly_search
    from run_monthly_closure_search import (
        _bounded_exhaustive_builder,
        _independent_exhaustive_builder,
    )

    spec = _spec_from_case(case, f"{arm}_spec")
    independent = spec.interday_policy == "independent_daily_reset_v1"
    spec_path = workspace_root / f"{case['case_id']}-{arm}-spec.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec.to_dict(), indent=1, sort_keys=True),
                         encoding="utf-8")

    resolved = MonthlyDemandResolverRunner(
        spec,
        runs_root=runs_root,
        release_root=release_root,
        # NEVER build demand from inside a measurement: a campaign that
        # calibrates its own inputs is measuring itself.
        build_missing=False,
        baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
        study_provenance_key=f"ivc-{case['case_id']}-{arm}",
        include_disruption=policy.objective_method == "closure_cost_v1",
        envelope_policy=(INDEPENDENT_DAILY_ENVELOPE_POLICY if independent
                         else EnvelopePolicy()),
    )
    runner = (IndependentDailyRunner(spec, daily_runner=resolved)
              if independent else resolved)
    if independent:
        def screen_builder(path):
            return _independent_exhaustive_builder(
                path,
                maximum_candidates=MAXIMUM_ENUMERATED_CANDIDATES,
                maximum_daily_units=10_000,
                baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
                preflight_report=None,
            )
    else:
        def screen_builder(path):
            return _bounded_exhaustive_builder(
                path, maximum_candidates=MAXIMUM_ENUMERATED_CANDIDATES)

    started = time.monotonic()
    try:
        result = run_monthly_search(
            spec, policy, runner=runner, screen_builder=screen_builder,
            root=workspace_root / arm)
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    return {
        "arm": arm,
        "search_id": spec.search_id,
        "search_content_key": spec.content_key,
        "interday_policy": spec.interday_policy,
        "work_allocation_policy": spec.work_allocation_policy,
        "wall_time_s": round(time.monotonic() - started, 3),
        "result": result,
        # Queue/halting, recovery and teleports are not part of the published
        # evidence contract; they live on the runner's canonical observations.
        "canonical_observations": list(
            getattr(resolved, "canonical_observations", ()) or ()),
    }


def _by_candidate(arm: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-candidate metrics, keyed by the arm's own schedule id."""
    out: dict[str, dict[str, Any]] = {}
    selection = arm["result"].get("pilot_selection") or {}
    for item in selection.get("candidates", ()):
        cost = item.get("closure_cost") or {}
        out[str(item.get("candidate_id"))] = {
            "eligible": item.get("eligible"),
            "hard_failures": list(item.get("hard_failures") or ()),
            "worst_variant_delta_s": item.get("worst_variant_delta_s"),
            "added_vehicle_hours": cost.get("added_vehicle_hours"),
            "added_metres_total": cost.get("added_metres_total"),
            "vehicles_affected": cost.get("vehicles_affected"),
            "vehicles_no_detour": cost.get("vehicles_no_detour"),
        }
    for observation in arm["canonical_observations"]:
        record = out.setdefault(str(observation.get("schedule_id")), {})
        health = observation.get("health") or {}
        candidate = observation.get("candidate_metrics") or {}
        record.setdefault("teleports", []).append(
            (health.get("candidate") or {}).get("teleport_total"))
        record.setdefault("max_queue_vehicles", []).append(
            candidate.get("max_queue_vehicles"))
        record.setdefault("waiting_at_end", []).append(
            candidate.get("waiting_at_end"))
        record.setdefault("recovery_status", []).append(
            (observation.get("recovery") or {}).get("status"))
        record.setdefault("recovery_seconds", []).append(
            (observation.get("recovery") or {}).get("recovered_at_s"))
    return out


def _relative_gap(left: Any, right: Any) -> float | None:
    try:
        left = float(left)
        right = float(right)
    except (TypeError, ValueError):
        return None
    scale = max(abs(left), abs(right))
    if scale == 0.0:
        return 0.0
    return abs(left - right) / scale


def compare_arms(case: Mapping[str, Any], correspondence: Mapping[str, Any],
                 independent: Mapping[str, Any], continuous: Mapping[str, Any],
                 tolerances: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the two arms over the candidates they have in common.

    Every metric the pre-registration named is reported. A metric the run did
    not produce is reported as `null` with a reason — never as a pass.
    """
    left = _by_candidate(independent)
    right = _by_candidate(continuous)
    top_k = int(tolerances.get("rank_flip_material_if_top_k", 3))

    per_candidate = []
    breaches: list[str] = []
    for pair in correspondence["pairs"]:
        a = left.get(pair["independent_id"])
        b = right.get(pair["continuous_id"])
        if a is None or b is None:
            per_candidate.append({
                "shape": pair["shape"],
                "compared": False,
                "reason": "one arm produced no statistics for this candidate",
            })
            continue
        entry: dict[str, Any] = {"shape": pair["shape"], "compared": True,
                                 "metrics": {}}
        for metric, tolerance_key, absolute in (
                ("added_vehicle_hours", "added_vehicle_hours_relative", False),
                ("added_metres_total", "added_metres_total_relative", False),
                ("vehicles_affected", "vehicles_affected_relative", False),
                ("vehicles_no_detour", "vehicles_no_detour_absolute", True),
        ):
            limit = tolerances.get(tolerance_key)
            if absolute:
                try:
                    gap = abs(float(a.get(metric)) - float(b.get(metric)))
                except (TypeError, ValueError):
                    gap = None
            else:
                gap = _relative_gap(a.get(metric), b.get(metric))
            within = None if gap is None or limit is None else gap <= limit
            entry["metrics"][metric] = {
                "independent": a.get(metric), "continuous": b.get(metric),
                "gap": gap, "tolerance": limit, "within_tolerance": within,
            }
            if within is False:
                breaches.append(f"{pair['independent_id']}:{metric}")
        queue_gap = _relative_gap(
            max((v for v in (a.get("max_queue_vehicles") or []) if v is not None),
                default=None),
            max((v for v in (b.get("max_queue_vehicles") or []) if v is not None),
                default=None))
        queue_limit = tolerances.get("queue_and_halting_relative")
        entry["metrics"]["queue_and_halting"] = {
            "independent": a.get("max_queue_vehicles"),
            "continuous": b.get("max_queue_vehicles"),
            "gap": queue_gap, "tolerance": queue_limit,
            "within_tolerance": (None if queue_gap is None or queue_limit is None
                                 else queue_gap <= queue_limit),
        }
        if entry["metrics"]["queue_and_halting"]["within_tolerance"] is False:
            breaches.append(f"{pair['independent_id']}:queue_and_halting")
        teleport_gap = None
        if a.get("teleports") and b.get("teleports"):
            teleport_gap = abs(max(a["teleports"]) - max(b["teleports"]))
        entry["metrics"]["teleports"] = {
            "independent": a.get("teleports"), "continuous": b.get("teleports"),
            "gap": teleport_gap,
            "tolerance": tolerances.get("teleports_absolute"),
            "within_tolerance": (None if teleport_gap is None
                                 else teleport_gap
                                 <= float(tolerances.get("teleports_absolute", 0))),
        }
        if entry["metrics"]["teleports"]["within_tolerance"] is False:
            breaches.append(f"{pair['independent_id']}:teleports")
        entry["metrics"]["recovery"] = {
            "independent": a.get("recovery_status"),
            "continuous": b.get("recovery_status"),
            "tolerance_s": tolerances.get("recovery_seconds_absolute"),
            "within_tolerance": (
                None if not a.get("recovery_status") or not b.get("recovery_status")
                else sorted(a["recovery_status"]) == sorted(b["recovery_status"])),
        }
        if entry["metrics"]["recovery"]["within_tolerance"] is False:
            breaches.append(f"{pair['independent_id']}:recovery")
        per_candidate.append(entry)

    def _order(arm_records, ids):
        scored = [(arm_records[item]["added_vehicle_hours"], item)
                  for item in ids
                  if item in arm_records
                  and arm_records[item].get("added_vehicle_hours") is not None]
        return [item for _, item in sorted(scored, key=lambda x: (x[0], x[1]))]

    shape_by_left = {pair["independent_id"]: tuple(pair["shape"]["work_dates"])
                     for pair in correspondence["pairs"]}
    shape_by_right = {pair["continuous_id"]: tuple(pair["shape"]["work_dates"])
                      for pair in correspondence["pairs"]}
    left_rank = [shape_by_left[item]
                 for item in _order(left, list(shape_by_left))]
    right_rank = [shape_by_right[item]
                  for item in _order(right, list(shape_by_right))]
    material_flip = (left_rank[:top_k] != right_rank[:top_k])

    independent_result = independent["result"]
    continuous_result = continuous["result"]
    winner_agrees = None
    left_winner = independent_result.get("winner_id")
    right_winner = continuous_result.get("winner_id")
    if left_winner in shape_by_left and right_winner in shape_by_right:
        winner_agrees = shape_by_left[left_winner] == shape_by_right[right_winner]
    elif left_winner is None and right_winner is None:
        winner_agrees = True
    # A winner the other arm cannot express is not a rank flip — it is a
    # comparison that cannot speak to the decision at all, and it must not be
    # reported as agreement just because the shared subset happened to match.
    winner_outside_paired_set = (
        (left_winner is not None and left_winner not in shape_by_left)
        or (right_winner is not None and right_winner not in shape_by_right))

    risk = ("high" if (material_flip or breaches or winner_outside_paired_set
                       or not correspondence["candidate_space_identical"])
            else "low")
    return {
        "paired_candidate_count": correspondence["paired_candidate_count"],
        "compared_candidate_count": sum(
            1 for item in per_candidate if item.get("compared")),
        "ranking": {
            "independent": [list(item) for item in left_rank],
            "continuous": [list(item) for item in right_rank],
            "top_k": top_k,
            "material_rank_flip": material_flip,
        },
        "winner_and_ties": {
            "independent_winner": left_winner,
            "continuous_winner": right_winner,
            "independent_ties": independent_result.get("tie_ids"),
            "continuous_ties": continuous_result.get("tie_ids"),
            "winner_agrees": winner_agrees,
            "winner_outside_paired_set": winner_outside_paired_set,
        },
        "candidate_space_identical": correspondence["candidate_space_identical"],
        "status": {"independent": independent_result.get("status"),
                   "continuous": continuous_result.get("status")},
        "tolerance_breaches": sorted(set(breaches)),
        "per_candidate": per_candidate,
        "risk_class": risk,
        "wall_time_s": {"independent": independent["wall_time_s"],
                        "continuous": continuous["wall_time_s"]},
    }


def measure_case(case: Mapping[str, Any], record: Mapping[str, Any], *,
                 policy_path: Path, runs_root: Path, release_root: Path,
                 workspace_root: Path) -> dict[str, Any]:
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

    policy = MonthlySearchPolicy.from_dict(
        json.loads(Path(policy_path).read_text(encoding="utf-8")))
    arms = {
        arm: _run_arm(case, arm, policy=policy, runs_root=runs_root,
                      release_root=release_root,
                      workspace_root=workspace_root / case["case_id"])
        for arm in ("independent", "continuous")
    }
    return {"arms": {arm: {key: value for key, value in payload.items()
                           if key != "canonical_observations"}
                     for arm, payload in arms.items()},
            "policy_content_key": policy.content_key,
            "comparison": None, "arms_raw": arms}


def build_outcome(registration: Mapping[str, Any],
                  records: Sequence[Mapping[str, Any]], *,
                  runs_root: Path, measured: Mapping[str, Any] | None = None,
                  wall_time_s: float = 0.0,
                  peak_rss_bytes: int = 0) -> dict[str, Any]:
    """The separate outcome record. The registration is never edited."""
    measured = dict(measured or {})
    buckets: dict[str, list[str]] = {name: [] for name in BUCKETS}
    for record in records:
        bucket = record["bucket"]
        if bucket == "measurable":
            bucket = ("measured" if record["case_id"] in measured
                      else "blocked_missing_demand")
        buckets.setdefault(bucket, []).append(record["case_id"])
    # Every case lands in exactly one bucket, and the counts must add up to the
    # cases examined. A silently dropped case is the one failure mode that
    # would make a "0 blocked" line mean nothing.
    total = sum(len(value) for value in buckets.values())
    if total != len(records):
        raise ValueError(
            f"bucket totals ({total}) do not account for every examined case "
            f"({len(records)})")
    outcome = {
        "schema": OUTCOME_SCHEMA,
        "kind": "independent_vs_continuous_outcome",
        "stage": "closure_search_scaling_plan_pr_h_measurement",
        "evidence_class": "measurement",
        "release_evidence": False,
        "registration": {
            "path": _relative(DEFAULT_REGISTRATION),
            "schema": registration["schema"],
            "input_content_key": registration["input_content_key"],
            "case_count": registration["case_count"],
            "pairable_case_count": registration["pairable_case_count"],
        },
        "tolerances": dict(registration["tolerances"]),
        "compared_metrics": list(registration["compared_metrics"]),
        "continuous_max_workdays": registration["continuous_max_workdays"],
        "extrapolation_rule": registration["extrapolation_rule"],
        "bucket_counts": {name: len(value)
                          for name, value in sorted(buckets.items())},
        "buckets": {name: sorted(value)
                    for name, value in sorted(buckets.items())},
        "examined_case_count": len(records),
        "cases": list(records),
        "measured_cases": measured,
        "environment": {
            "runs_root": _relative(runs_root),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "wall_time_s": round(wall_time_s, 3),
            "peak_rss_bytes": peak_rss_bytes,
        },
        "claim_boundary": {
            "activates_policy_v3": False,
            "opens_global_best": False,
            "permits_ui_claim": False,
            "licenses_extrapolation_beyond_measured_workdays": False,
            "reason": (
                "a paired equivalence measurement between two interday "
                "policies. It says nothing about held-out generalisation, "
                "nothing about workday counts it did not measure, and nothing "
                "about which closure a city should choose."),
        },
    }
    outcome["content_key"] = _content_key(
        {key: value for key, value in outcome.items()
         if key not in ("content_key", "environment")})
    return outcome


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", default=str(DEFAULT_REGISTRATION))
    parser.add_argument("--out", default=str(DEFAULT_OUTCOME))
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--workspace-root",
                        default=str(ROOT / "runs" / "ivc-measurement"))
    parser.add_argument("--case", action="append", default=None,
                        help="measure only these case ids")
    parser.add_argument("--classify-only", action="store_true",
                        help="structural buckets only; run no SUMO")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    started = time.monotonic()
    registration = load_registration(Path(args.registration))
    runs_root = Path(args.runs_root)
    wanted = set(args.case or ())

    records = []
    for case in registration["cases"]:
        if wanted and case["case_id"] not in wanted:
            continue
        records.append(classify(case, runs_root=runs_root,
                                check_demand=not args.classify_only))

    measured: dict[str, Any] = {}
    if not args.classify_only:
        by_id = {case["case_id"]: case for case in registration["cases"]}
        for record in records:
            if record["bucket"] != "measurable":
                continue
            case = by_id[record["case_id"]]
            run = measure_case(
                case, record, policy_path=Path(args.policy),
                runs_root=runs_root, release_root=Path(args.release_root),
                workspace_root=Path(args.workspace_root))
            run["comparison"] = compare_arms(
                case, record["correspondence"],
                run["arms_raw"]["independent"], run["arms_raw"]["continuous"],
                registration["tolerances"])
            run.pop("arms_raw")
            measured[record["case_id"]] = run

    outcome = build_outcome(
        registration, records, runs_root=runs_root, measured=measured,
        wall_time_s=time.monotonic() - started,
        peak_rss_bytes=_peak_rss_bytes())
    payload = json.dumps(outcome, indent=1, sort_keys=True) + "\n"
    if args.stdout:
        print(payload, end="")
        return 0
    destination = Path(args.out)
    if destination.exists() and not args.overwrite:
        raise SystemExit(
            f"{destination} already exists; an outcome record is evidence. "
            f"Pass --overwrite only to replace a run you intend to discard.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    counts = outcome["bucket_counts"]
    print(f"wrote {destination} ("
          + ", ".join(f"{name}={value}" for name, value in counts.items())
          + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
