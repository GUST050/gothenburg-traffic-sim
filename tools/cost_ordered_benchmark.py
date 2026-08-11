"""Pre-register, then run, the discriminating cost-ordered benchmark.

Stage 2. The question is narrow: does cost-first execution reach the SAME
answer as exhaustive execution while running SUMO on strictly fewer candidates?

WHY THE SELECTION IS STRUCTURAL.  A benchmark chosen after looking at which
closure wins answers nothing. So `--preregister` selects a case from properties
that are knowable BEFORE any search runs — how many candidates the calendar
enumerates, how many daily units they need, which of those units have a
calibrated archive on disk, and how many structurally eligible candidates that
leaves — and refuses to consider any outcome. It then binds every input by
digest and writes an immutable registration.

`--run` executes both arms on those bound inputs and writes a SEPARATE outcome
record. The registration is never edited by the run: if the case turns out to
have too few viable candidates to discriminate, that result is recorded as it
happened and a NEW case is registered before its outcome is looked at.

WHAT THIS TOOL WILL NOT DO.  It will not invent an archive, will not relax the
gate to make a run pass, and will not report a saving it did not measure. If no
calibrated archive covers the case, it says so and stops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import iter_closure_schedules  # noqa: E402
from traffic_sim.core.contracts import (  # noqa: E402
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.deterministic_disruption import (  # noqa: E402
    DISRUPTION_SCHEMA,
    VARIANT_FILENAMES,
)

REGISTRATION_SCHEMA = "cost_ordered_benchmark_registration_v1"
OUTCOME_SCHEMA = "cost_ordered_benchmark_outcome_v1"

DEFAULT_REGISTRATION = (
    ROOT / "validation" / "cost_ordered_benchmark_registration_v1.json")
DEFAULT_OUTCOME = (
    ROOT / "validation" / "cost_ordered_benchmark_outcome_v1.json")

#: Where calibrated archives live. An archive is usable only if it carries all
#: three variants AND its own metadata.
DEFAULT_RUNS_ROOT = ROOT / "runs"
DEFAULT_RELEASE_ROOT = ROOT / "runs" / "monthly-demand-releases"

#: Policies the two arms use. v2 exhaustive is the reference; v3 is provisional
#: and changes execution order only.
EXHAUSTIVE_POLICY = "validation/monthly_search_policy_v2.json"
COST_ORDERED_POLICY = "validation/monthly_search_policy_v3.json"

#: Structural minimum for a DISCRIMINATING case. Below this the two arms cannot
#: differ in how much SUMO they run, so the benchmark could not fail even if
#: the implementation were wrong.
MINIMUM_STRUCTURAL_CANDIDATES = 6

#: Frozen pass/fail thresholds. Written before any outcome exists.
GATE_THRESHOLDS = {
    "candidate_costs_field_identical": True,
    "hard_failures_identical": True,
    "health_classifications_identical": True,
    "status_identical": True,
    "selected_ids_identical": True,
    "final_decision_identical": True,
    "sumo_verifications_saved_minimum": 1,
    "stop_proof_valid": True,
    "cache_hits_consistent": True,
    "restart_equivalent": True,
    "resource_cap_regression_allowed": False,
}

COMPARISON_METRICS = (
    "candidate_deterministic_costs",
    "hard_failures",
    "health_classification",
    "pilot_status",
    "selected_ids",
    "final_decision",
    "sumo_verifications",
    "daily_cost_cache_hits",
    "wall_time_s",
    "peak_rss_bytes",
)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _content_key(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _candidate_specs() -> tuple[ClosureSearchSpec, ...]:
    """Structurally varied cases, generated without consulting any outcome.

    One road, several months and several daily windows: the axes that decide
    how many candidates a calendar produces, which is what makes a case
    discriminating or not.
    """
    specs = []
    for month, start_day in ((3, 2), (5, 4), (9, 1), (11, 3)):
        for band, hours in ((("06:00", "12:00"), 4), (("09:00", "16:00"), 5)):
            start = date(2025, month, start_day)
            specs.append(ClosureSearchSpec(
                search_id=(f"cost-ordered-benchmark-{month:02d}"
                           f"-{band[0].replace(':', '')}"),
                directed_edges=("60786979_3575001205_0",),
                demand_build_id="historical-2025",
                source="historical",
                permitted_date_start=start.isoformat(),
                permitted_date_end=(start + timedelta(days=11)).isoformat(),
                required_work_minutes=hours * 60,
                max_consecutive_start_days=1,
                permitted_daily_band=DailyTimeBand(*band),
                allowed_weekdays=(0, 1, 2, 3, 4),
                interday_policy="independent_daily_reset_v1",
                work_allocation_policy="exact_equal_daily_v1",
                objective_profile="displaced_vehicles_and_detour_v1",
                period_comparison_policy="rolling_period_v1",
            ))
    return tuple(specs)


def _relative(path: Path) -> str:
    """Repository-relative when possible; absolute when the root differs.

    A benchmark may legitimately be pointed at an archive library outside the
    checkout, and a record that crashes rather than recording that path would
    be worse than one that is explicit about it.
    """
    path = Path(path)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _archive_index(runs_root: Path) -> dict[str, dict[str, Any]]:
    """Every calibrated archive on disk, keyed by its build key.

    An archive counts only if all three variant route files exist: a case
    priced from two variants is not the case the policy describes.
    """
    index: dict[str, dict[str, Any]] = {}
    root = Path(runs_root)
    if not root.is_dir():
        return index
    for archive in sorted(root.glob("demand-*")):
        if not archive.is_dir():
            continue
        meta_path = archive / "demand_meta.json"
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        routes = {
            variant: archive / filename
            for variant, filename in VARIANT_FILENAMES.items()
        }
        if not all(path.is_file() for path in routes.values()):
            continue
        key = str(metadata.get("demand_build_key", archive.name))
        index[key] = {
            "archive": _relative(archive),
            "epoch_sim": metadata.get("epoch_sim"),
            "n_intervals": metadata.get("n_intervals"),
            "routes": {
                variant: {
                    "path": _relative(path),
                    "sha256": sha256_file(path),
                }
                for variant, path in sorted(routes.items())
            },
            "demand_meta_sha256": sha256_file(meta_path),
        }
    return index


def _structural_profile(spec: ClosureSearchSpec) -> dict[str, Any]:
    """Everything about a case that is knowable before it runs."""
    from traffic_sim.simulation.independent_daily import daily_unit_records

    candidates = 0
    unit_ids: set[str] = set()
    work_dates: set[str] = set()
    for schedule in iter_closure_schedules(spec):
        candidates += 1
        for unit_id, identity, _build in daily_unit_records(spec, schedule):
            unit_ids.add(unit_id)
            work_dates.add(str(identity["work_date"]))
    return {
        "candidate_count": candidates,
        "unique_daily_unit_count": len(unit_ids),
        "work_dates": sorted(work_dates),
    }


def select_case(runs_root: Path = DEFAULT_RUNS_ROOT) -> dict[str, Any]:
    """Pick the case with the most structurally eligible candidates.

    Deliberately blind to outcomes: it never runs a search, never prices a
    candidate and never looks at which closure would win. The only ranking
    signal is how many candidates the case can discriminate between, which is a
    property of the calendar and the archives on disk.
    """
    archives = _archive_index(runs_root)
    covered_dates = {
        str(record.get("epoch_sim", ""))[:10]
        for record in archives.values()
    }
    evaluated = []
    for spec in _candidate_specs():
        profile = _structural_profile(spec)
        available = [value for value in profile["work_dates"]
                     if value in covered_dates]
        profile.update({
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "spec": spec.to_dict(),
            "work_dates_with_calibrated_archive": available,
            "structurally_eligible": (
                len(available) == len(profile["work_dates"])
                and profile["candidate_count"] >= MINIMUM_STRUCTURAL_CANDIDATES
            ),
        })
        evaluated.append(profile)
    eligible = [item for item in evaluated if item["structurally_eligible"]]
    # Most candidates first, then the smallest unit count, then the search ID:
    # a total order that never consults a result.
    eligible.sort(key=lambda item: (-item["candidate_count"],
                                    item["unique_daily_unit_count"],
                                    item["search_id"]))
    return {
        "archives_available": len(archives),
        "evaluated": evaluated,
        "selected": eligible[0] if eligible else None,
        "selection_rule": (
            "most candidates, then fewest unique daily units, then search_id; "
            "no outcome, cost or winner is consulted"),
    }


def build_registration(runs_root: Path = DEFAULT_RUNS_ROOT) -> dict[str, Any]:
    selection = select_case(runs_root)
    selected = selection["selected"]
    archives = _archive_index(runs_root)

    record: dict[str, Any] = {
        "schema": REGISTRATION_SCHEMA,
        "kind": "cost_ordered_benchmark_registration",
        "evidence_class": "preregistration",
        "release_evidence": False,
        "registered_at": time.strftime("%Y-%m-%d"),
        "status": "frozen_before_outcome",
        "hypothesis": (
            "Cost-first execution reaches the same pilot status, the same "
            "selected_ids and the same final decision as exhaustive "
            "execution, while running SUMO on strictly fewer candidates."),
        "selection": {
            "rule": selection["selection_rule"],
            "minimum_structural_candidates": MINIMUM_STRUCTURAL_CANDIDATES,
            "archives_available": selection["archives_available"],
            "evaluated": [
                {key: item[key] for key in (
                    "search_id", "candidate_count", "unique_daily_unit_count",
                    "structurally_eligible")}
                for item in selection["evaluated"]
            ],
        },
        "policies": {
            "exhaustive": {
                "path": EXHAUSTIVE_POLICY,
                "sha256": sha256_file(ROOT / EXHAUSTIVE_POLICY),
            },
            "cost_ordered": {
                "path": COST_ORDERED_POLICY,
                "sha256": sha256_file(ROOT / COST_ORDERED_POLICY),
            },
        },
        "sources": {
            name: sha256_file(ROOT / name)
            for name in (
                "traffic_sim/simulation/cost_ordered_execution.py",
                "traffic_sim/simulation/cost_ordered_search.py",
                "traffic_sim/simulation/deterministic_disruption.py",
                "traffic_sim/simulation/closure_ranking.py",
                "traffic_sim/simulation/pilot_selection.py",
                "traffic_sim/simulation/finalist_decision.py",
                "traffic_sim/simulation/monthly_search.py",
                "run_scenario.py",
                "run_monthly_closure_search.py",
                "tools/cost_ordered_benchmark.py",
            )
        },
        "network": {
            "path": "sumo/net.net.xml",
            "sha256": sha256_file(ROOT / "sumo" / "net.net.xml"),
        },
        "network_metadata": {
            "path": "sumo/network_metadata.json",
            "sha256": sha256_file(ROOT / "sumo" / "network_metadata.json"),
        },
        "disruption_schema": DISRUPTION_SCHEMA,
        "demand_variants": list(VARIANT_FILENAMES),
        "resource_caps": {
            "maximum_parent_schedules": 100_000,
            "maximum_daily_units": 10_000,
            "seed_workers": 1,
            "daily_workers": 1,
        },
        "seeds": {
            "basis": "canonical_seed(variant, repetition) — unchanged",
            "repetitions_per_variant": 1,
        },
        "output_roots": {
            "exhaustive": "runs/closure-search-benchmark-exhaustive",
            "cost_ordered": "runs/closure-search-benchmark-cost-ordered",
            "daily_cost_cache": "runs/closure-search-benchmark-daily-costs",
        },
        "comparison_metrics": list(COMPARISON_METRICS),
        "gate_thresholds": dict(GATE_THRESHOLDS),
        "outcome_record": str(
            DEFAULT_OUTCOME.relative_to(ROOT)),
        "claim_boundary": {
            "activates_policy_v3": False,
            "opens_global_best": False,
            "permits_ui_claim": False,
            "reason": (
                "a registration freezes a question; it measures nothing"),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
    }

    if selected is None:
        record["status"] = "blocked_no_structurally_eligible_case"
        record["selected_case"] = None
        record["blocked_by"] = {
            "reason": (
                "no case has a calibrated q10/q50/q90 archive for every work "
                "date it needs" if archives else
                "no calibrated demand archive exists under the runs root"),
            "archives_available": len(archives),
            "runs_root": str(Path(runs_root)),
            "reproducible_command": (
                "python3 tools/cost_ordered_benchmark.py --preregister "
                "--runs-root runs   # on a host whose runs/ holds the "
                "calibrated archive library"),
        }
    else:
        record["selected_case"] = {
            key: selected[key] for key in (
                "search_id", "search_content_key", "spec", "candidate_count",
                "unique_daily_unit_count", "work_dates",
                "work_dates_with_calibrated_archive")
        }
        record["archives"] = {
            key: value for key, value in archives.items()
            if str(value.get("epoch_sim", ""))[:10]
            in set(selected["work_dates"])
        }
    record["content_key"] = _content_key(
        {key: value for key, value in record.items()
         if key not in {"content_key", "registered_at"}})
    return record


def _gate_results(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the FROZEN thresholds. No threshold is chosen here."""
    saved = int(comparison.get("sumo_verifications_saved", 0))
    checks = {
        "candidate_costs_field_identical": bool(
            comparison.get("candidate_costs_field_identical")),
        "hard_failures_identical": bool(
            comparison.get("hard_failures_identical")),
        "health_classifications_identical": bool(
            comparison.get("health_classifications_identical")),
        "status_identical": bool(comparison.get("status_identical")),
        "selected_ids_identical": bool(comparison.get("selected_ids_identical")),
        "final_decision_identical": bool(
            comparison.get("final_decision_identical")),
        "sumo_verifications_saved": (
            saved >= GATE_THRESHOLDS["sumo_verifications_saved_minimum"]),
        "stop_proof_valid": bool(comparison.get("stop_proof_valid")),
        "cache_hits_consistent": bool(comparison.get("cache_hits_consistent")),
        "restart_equivalent": bool(comparison.get("restart_equivalent")),
        "no_resource_cap_regression": bool(
            comparison.get("no_resource_cap_regression")),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "thresholds": dict(GATE_THRESHOLDS),
    }


def build_outcome(
    registration: Mapping[str, Any],
    comparison: Mapping[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    """The separate record a run writes. Never edits the registration."""
    gates = _gate_results(comparison)
    record = {
        "schema": OUTCOME_SCHEMA,
        "kind": "cost_ordered_benchmark_outcome",
        "evidence_class": "preregistered_benchmark",
        "release_evidence": False,
        "measured_at": time.strftime("%Y-%m-%d"),
        "status": status,
        "registration": {
            "path": str(DEFAULT_REGISTRATION.relative_to(ROOT)),
            "content_key": registration.get("content_key"),
            "search_id": (registration.get("selected_case") or {}).get(
                "search_id"),
        },
        "comparison": dict(comparison),
        "gates": gates,
        "claim_boundary": {
            "activates_policy_v3": bool(
                gates["passed"] and status == "measured"
                and comparison.get("heldout_validated") is True),
            "opens_global_best": False,
            "permits_ui_claim": False,
            "reason": (
                "policy v3 activation additionally requires an untouched "
                "held-out campaign; a passing benchmark alone does not open "
                "it"),
        },
    }
    record["content_key"] = _content_key(
        {key: value for key, value in record.items()
         if key not in {"content_key", "measured_at"}})
    return record


def _write(path: Path, payload: Mapping[str, Any], *, overwrite: bool) -> None:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise SystemExit(
            f"{destination} already exists. A registration is frozen; pass "
            f"--overwrite only to correct it BEFORE any outcome exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preregister", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--registration", type=Path,
                        default=DEFAULT_REGISTRATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    if args.preregister == args.run:
        raise SystemExit("choose exactly one of --preregister or --run")

    if args.preregister:
        record = build_registration(args.runs_root)
        if args.stdout:
            print(json.dumps(record, indent=1, sort_keys=True))
        else:
            _write(args.registration, record, overwrite=args.overwrite)
            print(f"wrote {args.registration} (status={record['status']})")
        return 0 if record["selected_case"] is not None else 3

    registration = json.loads(
        Path(args.registration).read_text(encoding="utf-8"))
    if registration.get("selected_case") is None:
        raise SystemExit(
            "the registration selected no case; there is nothing to run. "
            f"Its blocker: {registration.get('blocked_by', {}).get('reason')}")
    raise SystemExit(
        "running the benchmark requires the calibrated archive library named "
        "in the registration. Execute both arms with "
        "run_monthly_closure_search.py --screening-mode independent-exhaustive "
        "and --screening-mode independent-cost-ordered-exact on the bound "
        "spec, then pass their results to build_outcome().")


if __name__ == "__main__":
    raise SystemExit(main())
