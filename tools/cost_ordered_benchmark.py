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
import os
import platform
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.product_arm as pa  # noqa: E402
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

#: The CURRENT contract. v3 differs from v2 in two ways that change what a
#: registration MEANS, which is why it is a new schema rather than new
#: filenames under the old one:
#:
#:   * `outcome_record` names the outcome the caller actually asked for. v2
#:     hard-coded its own default, so a v3 registration written to a custom
#:     path still claimed the v2 outcome — the record pointed at a file it had
#:     nothing to do with.
#:   * `sources` seals every project module on the arms' real import path, not
#:     the ten that were listed by hand. The gap was not academic: the runtime
#:     correction in adf765b changed `monthly_sumo.py` and `suggest_closure_
#:     time.py`, and a v2 registration would have reported no drift at all.
#:
#: v2 stays readable so the frozen v2 history keeps validating; it is never
#: written again.
REGISTRATION_SCHEMA = "cost_ordered_benchmark_registration_v3"
OUTCOME_SCHEMA = "cost_ordered_benchmark_outcome_v3"
REGISTRATION_SCHEMA_V2 = "cost_ordered_benchmark_registration_v2"
OUTCOME_SCHEMA_V2 = "cost_ordered_benchmark_outcome_v2"
SUPPORTED_REGISTRATION_SCHEMAS = (REGISTRATION_SCHEMA, REGISTRATION_SCHEMA_V2)
#: An outcome must speak its registration's dialect: replaying a frozen v2
#: registration must still produce a v2-schema outcome, or the v2 history
#: acquires a successor it never licensed.
OUTCOME_SCHEMA_FOR_REGISTRATION = {
    REGISTRATION_SCHEMA: OUTCOME_SCHEMA,
    REGISTRATION_SCHEMA_V2: OUTCOME_SCHEMA_V2,
}

DEFAULT_REGISTRATION = (
    ROOT / "validation" / "cost_ordered_benchmark_registration_v3.json")
DEFAULT_OUTCOME = (
    ROOT / "validation" / "cost_ordered_benchmark_outcome_v3.json")

#: Every project module on the two arms' real construction and execution path.
#: Derived by importing exactly what `product_arm.build_arm` and
#: `run_monthly_search` touch and taking the project-local closure; pinned by
#: `test_the_seal_covers_the_real_import_closure`, which re-derives it, so a
#: module added to the path later cannot slip past the seal unnoticed.
#:
#: The list is explicit rather than computed at registration time: a seal that
#: discovers its own contents would also quietly shrink when an import moved,
#: and a registration must fail loudly instead.
SEMANTIC_SOURCES = (
    "run_monthly_closure_search.py",
    "run_monthly_proxy_validation.py",
    "run_scenario.py",
    "screen_monthly_closures.py",
    "suggest_closure_time.py",
    "tools/__init__.py",
    "tools/cost_ordered_benchmark.py",
    "tools/product_arm.py",
    "traffic_sim/__init__.py",
    "traffic_sim/core/__init__.py",
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/core/fingerprint.py",
    "traffic_sim/demand/__init__.py",
    "traffic_sim/demand/build_lock.py",
    "traffic_sim/demand/route_support.py",
    "traffic_sim/demand/source_identity.py",
    "traffic_sim/simulation/__init__.py",
    "traffic_sim/simulation/closure_ledgers.py",
    "traffic_sim/simulation/closure_preflight.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/closure_teleport.py",
    "traffic_sim/simulation/cost_ordered_execution.py",
    "traffic_sim/simulation/cost_ordered_search.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/envelope.py",
    "traffic_sim/simulation/execution.py",
    "traffic_sim/simulation/finalist_decision.py",
    # Lazily imported by `load_passing_heldout_gate`, which decides the CLAIM
    # BOUNDARY on every result. A static import probe misses them; the full
    # suite caught them. Anything that can change what a result is allowed to
    # claim belongs in the seal.
    "traffic_sim/simulation/heldout_gate.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/metadata.py",
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/monthly_proxy.py",
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/multiday.py",
    "traffic_sim/simulation/period_comparison.py",
    "traffic_sim/simulation/pilot_selection.py",
    "traffic_sim/simulation/proxy_projection.py",
    "traffic_sim/simulation/proxy_validation.py",
    "traffic_sim/simulation/runtime.py",
    "traffic_sim/simulation/search_workspace.py",
    "traffic_sim/simulation/seed_worker_budget.py",
    "traffic_sim/simulation/sensor_fit.py",
    "traffic_sim/simulation/trajectory_contract.py",
    "traffic_sim/simulation/unit_budget.py",
    "traffic_sim/simulation/warm_route_windows.py",
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/warm_state_cache.py",
    "traffic_sim/simulation/warm_state_forensics.py",
    "traffic_sim/simulation/workspace.py",
    "traffic_sim/storage/__init__.py",
    "traffic_sim/storage/singleflight.py",
)

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


# --------------------------------------------------------------------------
# v2: discover the cases from the archive library instead of guessing dates.
# --------------------------------------------------------------------------

#: Daily windows to try. Several start times on an available date is what makes
#: a case discriminating without needing dates the library does not have.
DISCOVERY_BANDS = (
    (("06:00", "12:00"), 4),
    (("07:00", "15:00"), 5),
    (("09:00", "16:00"), 5),
    (("10:00", "18:00"), 6),
)

#: How many surviving roads to build cases for. Structural, deterministic, and
#: capped so discovery cannot enumerate for minutes on a large library.
DISCOVERY_ROAD_LIMIT = 6

SURVIVABILITY_SCREEN = (
    ROOT / "validation" / "closure_survivability_screen_v2.json")


def archive_calendar(runs_root: Path) -> dict[str, dict[str, Any]]:
    """Single-day calibrated archives on disk, keyed by their work date.

    Only whole-day, three-variant archives count. An independent daily unit
    resolves to exactly such an archive, so anything else cannot serve a case
    however promising its filename looks.
    """
    calendar: dict[str, dict[str, Any]] = {}
    root = Path(runs_root)
    if not root.is_dir():
        return calendar
    for archive in sorted(root.glob("demand-*")):
        meta_path = archive / "demand_meta.json"
        if not archive.is_dir() or not meta_path.is_file():
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            spec = dict(metadata.get("demand_spec") or {})
        except (OSError, ValueError):
            continue
        routes = {variant: archive / filename
                  for variant, filename in VARIANT_FILENAMES.items()}
        if not all(path.is_file() for path in routes.values()):
            continue
        if int(spec.get("days", 0)) != 1:
            continue
        if spec.get("begin") != "00:00" or spec.get("end") != "24:00":
            continue
        date_text = str(spec.get("start_date", ""))
        if not date_text:
            continue
        calendar.setdefault(date_text, {
            "date": date_text,
            "source": str(spec.get("source", "historical")),
            "demand_build_key": str(metadata.get("demand_build_key", "")),
            "archive": _relative(archive),
            "routes": {
                variant: {"path": _relative(path), "sha256": sha256_file(path)}
                for variant, path in sorted(routes.items())},
            "demand_meta_sha256": sha256_file(meta_path),
        })
    return calendar


def candidate_work_calendar(runs_root: Path) -> dict[str, set[str]]:
    """Possible work dates, derived from complete archive envelopes.

    Independent daily demand is normally a multi-day warm-up envelope: for a
    closure on 2027-07-15 the exact archive can start on 2027-07-14 and span
    three days. Treating only one-day archive start dates as work dates made
    discovery select archives the product resolver would later refuse. This
    function only proposes dates; `_resolved_archives_for_spec` below proves
    the exact product demand contract before a case becomes eligible.
    """
    by_source: dict[str, set[str]] = {}
    root = Path(runs_root)
    if not root.is_dir():
        return by_source
    for archive in sorted(root.glob("demand-*")):
        meta_path = archive / "demand_meta.json"
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            spec = dict(metadata.get("demand_spec") or {})
            first = date.fromisoformat(str(spec["start_date"]))
            days = int(spec["days"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        routes = [archive / filename for filename in VARIANT_FILENAMES.values()]
        if days < 1 or not all(path.is_file() for path in routes):
            continue
        source = str(spec.get("source", "historical"))
        dates = by_source.setdefault(source, set())
        for offset in range(days):
            dates.add((first + timedelta(days=offset)).isoformat())
    return by_source


def _date_runs(dates: Sequence[str],
               weekdays: Sequence[int]) -> list[list[str]]:
    """Maximal runs of consecutive eligible dates present in the library.

    A case's permitted window must contain dates that actually have archives,
    which is exactly what v1 got wrong: its windows were written by hand and
    most of them named dates the library never held.
    """
    available = sorted(set(dates))
    runs: list[list[str]] = []
    current: list[str] = []
    previous: date | None = None
    for text in available:
        try:
            value = date.fromisoformat(text)
        except ValueError:
            continue
        if value.weekday() not in set(weekdays):
            continue
        if previous is not None and (value - previous).days > 3:
            runs.append(current)
            current = []
        current.append(text)
        previous = value
    if current:
        runs.append(current)
    return [item for item in runs if item]


def surviving_roads(path: Path = SURVIVABILITY_SCREEN) -> list[dict[str, Any]]:
    """Edges that survive their own closure, from the frozen topology screen.

    A structural criterion, available before any outcome: an edge that severs a
    successor produces a search whose candidates are all degenerate for reasons
    that have nothing to do with execution order. v1's road
    (`60786979_3575001205_0`) is precisely such an edge — the documented
    single-incoming-connection case — so v2 prefers roads that survive.
    """
    try:
        screen = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    edges = (screen.get("candidate_pool") or {}).get("edges") or []
    surviving = [item for item in edges if item.get("survives_topology")]
    # Deterministic and outcome-blind: nearest to a sensor first (the best
    # measured part of the network), then by edge id.
    surviving.sort(key=lambda item: (float(item.get("dist_sensor_m", 1e9)),
                                     str(item.get("edge_id"))))
    return surviving[:DISCOVERY_ROAD_LIMIT]


def discovered_specs(runs_root: Path) -> tuple[ClosureSearchSpec, ...]:
    """Cases built around dates the archive library actually contains."""
    by_source = candidate_work_calendar(runs_root)
    if not by_source:
        return ()
    roads = surviving_roads()
    if not roads:
        return ()
    weekdays = (0, 1, 2, 3, 4)
    specs: list[ClosureSearchSpec] = []
    for source in sorted(by_source):
        for run in _date_runs(sorted(by_source[source]), weekdays):
            # Include every individual work date as well as the maximal run.
            # A maximal calendar run can straddle two distinct warm-up demand
            # envelopes even when one of its days is fully calibrated. Making
            # only the maximal case expressible caused one missing neighbour
            # to hide a perfectly discriminating 9-13-candidate day.
            windows = [[value] for value in run]
            if len(run) > 1:
                windows.append(run)
            for window in windows:
                for road in roads:
                    for band, hours in DISCOVERY_BANDS:
                        edge = str(road["edge_id"])
                        # The old identifier used only the first numeric edge
                        # component. Distinct directed edges that share their
                        # upstream junction therefore shared one search_id and
                        # could resume each other's benchmark workspace. Bind
                        # the complete directed edge into a short stable token.
                        edge_token = hashlib.sha256(
                            edge.encode("utf-8")).hexdigest()[:10]
                        specs.append(ClosureSearchSpec(
                            search_id=(
                                f"cob2-{source[:4]}"
                                f"-{window[0].replace('-', '')}"
                                f"-{window[-1].replace('-', '')}"
                                f"-{edge_token}"
                                f"-{band[0].replace(':', '')}"),
                            directed_edges=(edge,),
                            demand_build_id=f"{source}-{window[0][:4]}",
                            source=source,
                            permitted_date_start=window[0],
                            permitted_date_end=window[-1],
                            required_work_minutes=hours * 60,
                            max_consecutive_start_days=1,
                            permitted_daily_band=DailyTimeBand(*band),
                            allowed_weekdays=weekdays,
                            interday_policy="independent_daily_reset_v1",
                            work_allocation_policy="exact_equal_daily_v1",
                            objective_profile=(
                                "displaced_vehicles_and_detour_v1"),
                            period_comparison_policy="rolling_period_v1",
                        ))
    return tuple(specs)


def _resolved_archives_for_spec(
    spec: ClosureSearchSpec,
    runs_root: Path,
    cache: dict[str, tuple[dict[str, Any], ...]] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Resolve the exact archives the product runner would use.

    Metadata shape and route filenames are not sufficient evidence. The
    product validates manifests, generator/runtime fingerprints, provenance,
    output digests and the exact warm-up envelope. Selection remains
    outcome-blind, but it may only call a case runnable when this resolver does.
    """
    from traffic_sim.simulation.independent_daily import daily_unit_records
    from traffic_sim.simulation.monthly_demand import (
        MonthlyDemandResolverRunner,
        find_demand_archives,
    )

    resolved_cache = cache if cache is not None else {}
    resolver = MonthlyDemandResolverRunner(
        spec,
        runs_root=Path(runs_root),
        build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="cost-ordered-benchmark-discovery",
    )
    builds: dict[str, Any] = {}
    for parent in iter_closure_schedules(spec):
        for _unit_id, _identity, build_schedule in daily_unit_records(
                spec, parent):
            daily = build_schedule()
            required = resolver._required(daily)
            builds[required.build_key] = required

    bound: dict[str, dict[str, Any]] = {}
    for build_key, required in sorted(builds.items()):
        matches = resolved_cache.get(build_key)
        if matches is None:
            matches = tuple(find_demand_archives(Path(runs_root), required))
            resolved_cache[build_key] = matches
        if not matches:
            return None
        archive = Path(matches[0]["archive"]).resolve()
        meta_path = archive / "demand_meta.json"
        routes = {
            variant: archive / filename
            for variant, filename in VARIANT_FILENAMES.items()
        }
        bound[build_key] = {
            "archive": str(archive),
            "epoch_sim": f"{required.start_date}T00:00:00",
            "n_intervals": required.days * 96,
            "demand_build_spec": required.to_dict(),
            "routes": {
                variant: {"path": str(path), "sha256": sha256_file(path)}
                for variant, path in sorted(routes.items())
            },
            "demand_meta_sha256": sha256_file(meta_path),
        }
    return bound


#: External artifacts that decide the CLAIM BOUNDARY rather than the numbers.
#: `load_passing_heldout_gate` reads both, and the certificate names a manifest
#: that is bound too. Their ABSENCE is bound as explicitly as their contents:
#: a registration frozen while no gate existed, replayed after one appears,
#: describes a different claim boundary, and must say so.
EXTERNAL_GATE_ARTIFACTS = (
    "validation/monthly_gate_record.json",
    "validation/monthly_gate_adoption_certificate.json",
)


def sumo_runtime_identity(data_root: Path = ROOT) -> dict[str, Any]:
    """The SUMO that will actually run, bound by path, version and bytes.

    A benchmark that seals every line of Python and none of the simulator is
    sealing the easy half. SUMO decides every observation, so a different
    executable or a different version is exactly as much of a semantic change
    as an edited source file — and neither is visible in a source digest.

    Resolution mirrors the product: `runtime.sumo_home()` first, then PATH, so
    the identity recorded is the binary the run will invoke rather than
    whichever one happens to be first on an interactive shell's PATH.
    """
    import shutil

    report: dict[str, Any] = {
        "executable": None, "version": None, "sha256": None,
        "resolved_by": None,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    candidates: list[tuple[str, Path]] = []
    try:
        from traffic_sim.simulation.runtime import sumo_home

        candidates.append(("sumo_home", Path(sumo_home()) / "bin" / "sumo"))
    except Exception:                                          # noqa: BLE001
        pass
    found = shutil.which("sumo")
    if found:
        candidates.append(("PATH", Path(found)))
    for how, candidate in candidates:
        if not candidate.is_file():
            continue
        report["executable"] = str(candidate)
        report["resolved_by"] = how
        report["sha256"] = sha256_file(candidate)
        try:
            completed = subprocess.run(
                [str(candidate), "--version"], capture_output=True,
                text=True, timeout=60)
            report["version"] = (
                completed.stdout.splitlines() or [""])[0].strip()
        except (OSError, subprocess.SubprocessError):
            report["version"] = None
        break
    return report


def external_gate_state(root: Path | None = None) -> dict[str, Any]:
    """Bind the held-out gate artifacts, present or absent.

    Absence is a binding, not a gap. "There was no adopted gate when this
    question was frozen" is a claim about the experiment, and a later-appearing
    certificate silently widening what a replay may claim is precisely the
    drift this exists to catch.
    """
    # The gate artifacts the RUN will consult live under the data root the
    # registration binds. Reading them from the tool's own checkout would seal
    # one set of bytes and let the search read another — the same confusion the
    # data_root parameter exists to prevent for the network.
    root = ROOT if root is None else Path(root)
    state: dict[str, Any] = {"data_root": str(Path(root).resolve())}
    for name in EXTERNAL_GATE_ARTIFACTS:
        path = Path(root) / name
        if path.is_file():
            state[name] = {"present": True, "sha256": sha256_file(path),
                           "bytes": path.stat().st_size}
        else:
            state[name] = {"present": False, "sha256": None, "bytes": None}

    # The certificate names a manifest; that manifest is part of the same
    # claim and is bound with it.
    certificate = (Path(root)
                   / "validation/monthly_gate_adoption_certificate.json")
    manifest_state: dict[str, Any] = {"named_by_certificate": None,
                                      "present": False, "sha256": None}
    if certificate.is_file():
        try:
            payload = json.loads(certificate.read_text(encoding="utf-8"))
            named = str(payload.get("manifest_path", "") or "")
        except (OSError, ValueError):
            named = ""
        if named:
            manifest_state["named_by_certificate"] = named
            manifest_path = Path(root) / named
            if manifest_path.is_file():
                manifest_state["present"] = True
                manifest_state["sha256"] = sha256_file(manifest_path)
    state["certificate_manifest"] = manifest_state
    return state


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


def select_case(runs_root: Path = DEFAULT_RUNS_ROOT,
                *, from_archives: bool = False) -> dict[str, Any]:
    """Pick the case with the most structurally eligible candidates.

    Deliberately blind to outcomes: it never runs a search, never prices a
    candidate and never looks at which closure would win. The only ranking
    signal is how many candidates the case can discriminate between, which is a
    property of the calendar and the archives on disk.
    """
    archives = _archive_index(runs_root)
    evaluated = []
    resolution_cache: dict[str, tuple[dict[str, Any], ...]] = {}
    specs = (discovered_specs(runs_root) if from_archives
             else _candidate_specs())
    for spec in specs:
        profile = _structural_profile(spec)
        resolved = (_resolved_archives_for_spec(
            spec, runs_root, resolution_cache) if from_archives else None)
        covered_dates = {
            str(record.get("epoch_sim", ""))[:10]
            for record in archives.values()
        }
        available = ([value for value in profile["work_dates"]
                      if value in covered_dates]
                     if not from_archives else
                     list(profile["work_dates"]) if resolved is not None else [])
        profile.update({
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "spec": spec.to_dict(),
            "work_dates_with_calibrated_archive": available,
            "structurally_eligible": (
                len(available) == len(profile["work_dates"])
                and profile["candidate_count"] >= MINIMUM_STRUCTURAL_CANDIDATES
            ),
            "resolved_archives": resolved,
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
        "case_source": ("discovered_from_archive_metadata" if from_archives
                        else "fixed_v1_candidate_specs"),
        "evaluated_case_count": len(evaluated),
    }


def build_registration(runs_root: Path = DEFAULT_RUNS_ROOT,
                       *, from_archives: bool = False,
                       data_root: Path = ROOT,
                       outcome_path: Path = DEFAULT_OUTCOME) -> dict[str, Any]:
    data_root = Path(data_root).resolve()
    selection = select_case(runs_root, from_archives=from_archives)
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
            "case_source": selection["case_source"],
            "evaluated_case_count": selection["evaluated_case_count"],
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
        "sources": {name: sha256_file(ROOT / name)
                    for name in SEMANTIC_SOURCES},
        "sumo_runtime": sumo_runtime_identity(data_root),
        "external_gate_state": external_gate_state(data_root),
        # These paths are deliberately absolute. Source files belong to this
        # checkout, but ignored SUMO data commonly lives in the primary
        # worktree. Binding ROOT here while run_scenario reads relative to the
        # process cwd would register one network and simulate another.
        "data_root": str(data_root),
        "network": {
            "path": str(data_root / "sumo" / "net.net.xml"),
            "sha256": sha256_file(data_root / "sumo" / "net.net.xml"),
        },
        "network_metadata": {
            "path": str(data_root / "sumo" / "network_metadata.json"),
            "sha256": sha256_file(
                data_root / "sumo" / "network_metadata.json"),
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
        # The outcome the CALLER asked for, not this tool's default. v2 bound
        # its own default regardless of --out, so a registration written to a
        # v3 path still named the v2 outcome — a provenance claim about a file
        # it had nothing to do with, and one nobody would notice until two
        # records disagreed about which run produced which.
        "outcome_record": _relative(Path(outcome_path).resolve()),
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

    if selected is not None and (
            record["network"]["sha256"] is None
            or record["network_metadata"]["sha256"] is None):
        raise ValueError(
            "cannot freeze a runnable benchmark without the active SUMO "
            f"network and metadata under {data_root / 'sumo'}")

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
        record["archives"] = (
            selected.get("resolved_archives")
            if from_archives else {
                key: value for key, value in archives.items()
                if str(value.get("epoch_sim", ""))[:10]
                in set(selected["work_dates"])
            })
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
    registration_path: Path = DEFAULT_REGISTRATION,
) -> dict[str, Any]:
    """The separate record a run writes. Never edits the registration."""
    gates = _gate_results(comparison)
    schema = OUTCOME_SCHEMA_FOR_REGISTRATION.get(
        str(registration.get("schema")), OUTCOME_SCHEMA)
    record = {
        "schema": schema,
        "kind": "cost_ordered_benchmark_outcome",
        "evidence_class": "preregistered_benchmark",
        "release_evidence": False,
        "measured_at": time.strftime("%Y-%m-%d"),
        "status": status,
        "registration": {
            "path": _relative(Path(registration_path).resolve()),
            "schema": registration.get("schema"),
            "content_key": registration.get("content_key"),
            "search_id": (registration.get("selected_case") or {}).get(
                "search_id"),
        },
        "comparison": dict(comparison),
        "gates": gates,
        "claim_boundary": {
            "activates_policy_v3": False,
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


# --------------------------------------------------------------------------
# The run: both arms, on the bound inputs, compared field by field.
# --------------------------------------------------------------------------


def verify_bindings(registration: Mapping[str, Any],
                    runs_root: Path) -> list[str]:
    """Every digest the registration froze must still describe the tree.

    A benchmark whose inputs moved under it measures something nobody
    registered. Reported as a list rather than raised one at a time, so a
    reviewer sees ALL the drift at once instead of fixing it one run per
    finding.
    """
    drift: list[str] = []

    body = {key: value for key, value in registration.items()
            if key not in {"content_key", "registered_at"}}
    if registration.get("content_key") != _content_key(body):
        drift.append("the registration's own content key does not describe it")

    for name, digest in (registration.get("sources") or {}).items():
        path = ROOT / name
        if not path.is_file():
            drift.append(f"bound source is missing: {name}")
        elif sha256_file(path) != digest:
            drift.append(f"bound source changed since registration: {name}")

    for arm, bound in (registration.get("policies") or {}).items():
        path = ROOT / str(bound["path"])
        if not path.is_file():
            drift.append(f"bound {arm} policy is missing: {bound['path']}")
        elif sha256_file(path) != bound["sha256"]:
            drift.append(f"bound {arm} policy changed: {bound['path']}")

    for key in ("network", "network_metadata"):
        bound = registration.get(key) or {}
        path = Path(str(bound.get("path", "")))
        if not path.is_absolute():
            # Backward compatibility for the frozen v1 registration.
            path = ROOT / path
        if not path.is_file():
            drift.append(f"bound {key} is missing: {bound.get('path')}")
        elif sha256_file(path) != bound.get("sha256"):
            drift.append(f"bound {key} changed: {bound.get('path')}")

    bound_runtime = registration.get("sumo_runtime")
    if bound_runtime is not None:
        live = sumo_runtime_identity()
        for field, label in (("sha256", "executable bytes"),
                             ("version", "version"),
                             ("executable", "resolved path")):
            if bound_runtime.get(field) != live.get(field):
                drift.append(
                    f"SUMO runtime {label} changed: registered "
                    f"{bound_runtime.get(field)!r}, live {live.get(field)!r}")
        for field in ("platform", "machine"):
            if bound_runtime.get(field) != live.get(field):
                drift.append(
                    f"SUMO runtime {field} changed: registered "
                    f"{bound_runtime.get(field)!r}, live {live.get(field)!r}")

    bound_gate = registration.get("external_gate_state")
    if bound_gate is not None:
        live_gate = external_gate_state(bound_gate.get("data_root") or ROOT)
        if bound_gate.get("data_root") != live_gate.get("data_root"):
            drift.append(
                f"external gate data root changed: registered "
                f"{bound_gate.get('data_root')!r}, live "
                f"{live_gate.get('data_root')!r}")
        for name in EXTERNAL_GATE_ARTIFACTS:
            was = bound_gate.get(name) or {}
            now = live_gate.get(name) or {}
            if bool(was.get("present")) != bool(now.get("present")):
                # The direction matters and both directions are drift: a gate
                # that appeared widens what a replay may claim, and one that
                # vanished narrows it.
                drift.append(
                    f"external gate artifact {name} was "
                    f"{'present' if was.get('present') else 'absent'} at "
                    f"registration and is now "
                    f"{'present' if now.get('present') else 'absent'}")
            elif was.get("sha256") != now.get("sha256"):
                drift.append(f"external gate artifact changed: {name}")
        was_manifest = bound_gate.get("certificate_manifest") or {}
        now_manifest = live_gate.get("certificate_manifest") or {}
        if was_manifest != now_manifest:
            drift.append(
                "the manifest named by the adoption certificate changed: "
                f"registered {was_manifest.get('named_by_certificate')!r} "
                f"(sha {was_manifest.get('sha256')}), live "
                f"{now_manifest.get('named_by_certificate')!r} "
                f"(sha {now_manifest.get('sha256')})")

    for build_key, archive in (registration.get("archives") or {}).items():
        base = Path(archive["archive"])
        if not base.is_absolute():
            base = ROOT / base
        for variant, route in (archive.get("routes") or {}).items():
            path = Path(route["path"])
            if not path.is_absolute():
                path = ROOT / path
            if not path.is_file():
                drift.append(
                    f"bound route is missing: {build_key}/{variant} "
                    f"({route['path']})")
            elif sha256_file(path) != route["sha256"]:
                drift.append(
                    f"bound route changed: {build_key}/{variant} "
                    f"({route['path']})")
        meta = base / "demand_meta.json"
        if not meta.is_file():
            drift.append(f"bound demand metadata is missing: {meta}")
        elif sha256_file(meta) != archive.get("demand_meta_sha256"):
            drift.append(f"bound demand metadata changed: {meta}")
    return drift


def _workspace_artifact(arm: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """One published artifact from an arm's workspace, by kind."""
    workspace = Path(arm.get("workspace", ""))
    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest.get("artifacts", ()):
        if record.get("kind") == kind:
            return json.loads((workspace / record["path"])
                              .read_text(encoding="utf-8"))
    return {}


def _pilot_statistics(arm: Mapping[str, Any]) -> dict[str, Any]:
    """The COMPLETE pilot selection, from the workspace rather than the API.

    `_final_result` truncates `pilot_selection.candidates` to the finalists for
    a broad independent search — tens of thousands of interval-heavy records
    would otherwise be copied into every response. The full statistics live in
    the workspace's `pilot-selection.json`, and comparing the truncated view
    instead would quietly reduce a field-by-field gate over every candidate to
    a check on the two both arms simulated anyway. Falls back to the response
    when no workspace is available (the API-only case).
    """
    published = _workspace_artifact(arm, "monthly_pilot_selection")
    return published or (arm.get("result", {}).get("pilot_selection") or {})


def _candidate_costs(arm_or_result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-candidate deterministic cost and health, keyed by schedule id."""
    selection = (_pilot_statistics(arm_or_result)
                 if "workspace" in arm_or_result or "result" in arm_or_result
                 else (arm_or_result.get("pilot_selection") or {}))
    out: dict[str, dict[str, Any]] = {}
    for item in selection.get("candidates", ()):
        cost = item.get("closure_cost") or {}
        out[str(item.get("candidate_id"))] = {
            "added_vehicle_hours": cost.get("added_vehicle_hours"),
            "added_metres_total": cost.get("added_metres_total"),
            "vehicles_affected": cost.get("vehicles_affected"),
            "vehicles_no_detour": cost.get("vehicles_no_detour"),
            "eligible": item.get("eligible"),
            "complete": item.get("complete"),
            "hard_failures": sorted(item.get("hard_failures") or ()),
        }
    return out


def _ledger_costs(arm: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """EVERY candidate's deterministic price, from the published cost ledger.

    The cost-ordered arm's pilot statistics only cover the candidates it
    simulated — two of forty-five on a typical case — so comparing those alone
    would check the gate on the handful where the arms trivially agree. The
    ledger holds the price of every candidate, computed before any SUMO ran,
    which is exactly what the exhaustive arm's disruption evidence should
    reproduce. Comparing THOSE is the field-by-field check worth having.
    """
    workspace = Path(arm.get("workspace", ""))
    ledger = workspace / "artifacts" / "cost-ledger.json"
    if not ledger.is_file():
        return {}
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    return {
        str(item["candidate_id"]): {
            key: item["cost"][key] for key in (
                "added_vehicle_hours", "added_metres_total",
                "vehicles_affected", "vehicles_no_detour")}
        for item in payload.get("costs", ())
    }


def _final_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    decision = result.get("robust_decision") or {}
    return {
        "status": result.get("status"),
        "winner_id": result.get("winner_id"),
        "tie_ids": sorted(result.get("tie_ids") or ()),
        "decision_status": decision.get("status"),
        "decision_winner": decision.get("winner_id"),
    }


def _stop_proof_valid(execution: Mapping[str, Any] | None) -> dict[str, Any]:
    """Re-derive the stop rule from the published proof, rather than trust it.

    A stop proof produced only by the code it justifies proves nothing, so the
    arithmetic is redone here against the proof's OWN declared numbers:

      * `band_exhausted` — the first unexamined candidate must be STRICTLY
        above cutoff + practical equivalence. That is the entire argument for
        not having simulated it, and `>=` would be the classic off-by-one that
        skips a candidate still inside the band.
      * `search_space_exhausted` — nothing was skipped, so `unexamined` must be
        zero and `examined` must equal the ordered total.
      * anything else — nothing survived the deterministic no-detour gate, so
        there must have been nothing to order.
    """
    if not execution:
        return {"valid": False, "reason": "no execution record was published"}
    proof = execution.get("stop_proof") or {}
    if not proof:
        return {"valid": False, "reason": "the execution record has no proof"}
    reason = str(proof.get("stop_reason", ""))
    if reason == "band_exhausted":
        band = proof.get("selection_band_added_vehicle_hours")
        first = proof.get("first_unexamined_added_vehicle_hours")
        if band is None or first is None:
            return {"valid": False, "stop_reason": reason,
                    "reason": "a band stop must name its band and the first "
                              "unexamined candidate"}
        valid = float(first) > float(band)
        return {
            "valid": valid, "stop_reason": reason,
            "selection_band_added_vehicle_hours": float(band),
            "first_unexamined_added_vehicle_hours": float(first),
            "reason": ("the first unexamined candidate is strictly above the "
                       "band, so no unexamined candidate could be retained"
                       if valid else
                       "the first unexamined candidate is INSIDE the band; it "
                       "should have been verified"),
        }
    if reason == "search_space_exhausted":
        valid = (int(proof.get("unexamined", -1)) == 0
                 and int(proof.get("examined", -1))
                 == int(proof.get("total_ordered", -2)))
        return {"valid": valid, "stop_reason": reason,
                "reason": ("every ordered candidate was verified"
                           if valid else
                           "the proof claims exhaustion but left candidates "
                           "unexamined")}
    valid = int(proof.get("total_ordered", -1)) == 0
    return {"valid": valid, "stop_reason": reason,
            "reason": ("nothing survived the deterministic no-detour gate"
                       if valid else
                       f"unrecognised stop reason {reason!r} with candidates "
                       f"still ordered")}


def compare_arms(exhaustive: Mapping[str, Any],
                 cost_ordered: Mapping[str, Any],
                 *, restart: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Field-by-field comparison of the two arms. Reports; judges nothing."""
    left = _candidate_costs(exhaustive)
    right = _candidate_costs(cost_ordered)
    shared = sorted(set(left) & set(right))

    cost_fields = ("added_vehicle_hours", "added_metres_total",
                   "vehicles_affected", "vehicles_no_detour")
    cost_mismatches = []
    failure_mismatches = []
    health_mismatches = []
    for candidate_id in shared:
        a, b = left[candidate_id], right[candidate_id]
        for field in cost_fields:
            if a.get(field) != b.get(field):
                cost_mismatches.append({
                    "candidate_id": candidate_id, "field": field,
                    "exhaustive": a.get(field), "cost_ordered": b.get(field)})
        if a["hard_failures"] != b["hard_failures"]:
            failure_mismatches.append({
                "candidate_id": candidate_id,
                "exhaustive": a["hard_failures"],
                "cost_ordered": b["hard_failures"]})
        if (a.get("eligible"), a.get("complete")) != (
                b.get("eligible"), b.get("complete")):
            health_mismatches.append({
                "candidate_id": candidate_id,
                "exhaustive": [a.get("eligible"), a.get("complete")],
                "cost_ordered": [b.get("eligible"), b.get("complete")]})

    execution = cost_ordered["result"].get("cost_ordered_execution")
    exhaustive_pilots = int(
        (exhaustive["result"].get("screening") or {}).get("shortlist_count")
        or len(left))
    cost_ordered_pilots = (
        int(execution["cost_ordered_sumo_candidates"]) if execution
        else exhaustive_pilots)

    left_selected = sorted(
        (exhaustive["result"].get("pilot_selection") or {}).get(
            "selected_ids", ()))
    right_selected = sorted(
        (cost_ordered["result"].get("pilot_selection") or {}).get(
            "selected_ids", ()))

    caps = {
        "maximum_parent_schedules": pa.MAXIMUM_CANDIDATES,
        "maximum_daily_units": pa.MAXIMUM_DAILY_UNITS,
    }
    stop_proof_check = _stop_proof_valid(execution)

    # The stronger check: every candidate the LEDGER priced, against the
    # exhaustive arm's own evidence for the same candidate.
    ledger = _ledger_costs(cost_ordered)
    ledger_mismatches = []
    for candidate_id, priced in sorted(ledger.items()):
        observed = left.get(candidate_id)
        if observed is None:
            continue
        for field in cost_fields:
            if observed.get(field) != priced.get(field):
                ledger_mismatches.append({
                    "candidate_id": candidate_id, "field": field,
                    "exhaustive": observed.get(field),
                    "cost_ordered_ledger": priced.get(field)})
    ledger_compared = sorted(set(ledger) & set(left))

    comparison: dict[str, Any] = {
        # The cost-ordered arm SIMULATES fewer candidates, so its pilot
        # statistics cover only those. Candidates only the exhaustive arm
        # simulated are reported, never silently dropped — and the ledger
        # comparison below covers ALL of them anyway.
        "compared_candidate_count": len(shared),
        "ledger_compared_candidate_count": len(ledger_compared),
        "ledger_costs_field_identical": not ledger_mismatches,
        "ledger_cost_mismatches": ledger_mismatches[:50],
        "exhaustive_only_candidates": sorted(set(left) - set(right)),
        "cost_ordered_only_candidates": sorted(set(right) - set(left)),
        "candidate_costs_field_identical": (
            not cost_mismatches and not ledger_mismatches),
        "candidate_cost_mismatches": cost_mismatches[:50],
        "hard_failures_identical": not failure_mismatches,
        "hard_failure_mismatches": failure_mismatches[:50],
        "health_classifications_identical": not health_mismatches,
        "health_mismatches": health_mismatches[:50],
        "status_identical": (
            (exhaustive["result"].get("pilot_selection") or {}).get("status")
            == (cost_ordered["result"].get("pilot_selection") or {}).get(
                "status")),
        "selected_ids_identical": left_selected == right_selected,
        "selected_ids": {"exhaustive": left_selected,
                         "cost_ordered": right_selected},
        "final_decision_identical": (
            _final_decision(exhaustive["result"])
            == _final_decision(cost_ordered["result"])),
        "final_decision": {
            "exhaustive": _final_decision(exhaustive["result"]),
            "cost_ordered": _final_decision(cost_ordered["result"])},
        "exhaustive_sumo_candidates": exhaustive_pilots,
        "cost_ordered_sumo_candidates": cost_ordered_pilots,
        "sumo_verifications_saved": exhaustive_pilots - cost_ordered_pilots,
        "stop_proof": (execution or {}).get("stop_proof"),
        "stop_proof_check": stop_proof_check,
        "stop_proof_valid": bool(stop_proof_check["valid"]),
        "cursor": (execution or {}).get("cursor"),
        "verified_prefix": list(
            ((execution or {}).get("cursor") or {}).get("verified", ())),
        "daily_cost_cache_hits": {
            "exhaustive": exhaustive["daily_cost_cache_hits"],
            "cost_ordered": cost_ordered["daily_cost_cache_hits"],
            "published_by_execution_record": (
                (execution or {}).get("daily_cost_cache_hits"))},
        # The cost source counts cache hits as it prices; the published record
        # snapshots the ledger's count. All pricing happens during ledger
        # construction, so the two must agree — if they diverge, something
        # priced a candidate outside the ledger and the ordering was built on
        # numbers the record does not describe.
        "cache_hits_consistent": bool(
            execution is not None
            and int(execution.get("daily_cost_cache_hits", -1))
            == int(cost_ordered["daily_cost_cache_hits"])),
        "wall_time_s": {"exhaustive": exhaustive["wall_time_s"],
                        "cost_ordered": cost_ordered["wall_time_s"]},
        "peak_rss_bytes": {"exhaustive": exhaustive["peak_rss_bytes"],
                           "cost_ordered": cost_ordered["peak_rss_bytes"]},
        "resource_caps": caps,
        "no_resource_cap_regression": (
            caps["maximum_daily_units"] == 10_000
            and caps["maximum_parent_schedules"] == 100_000),
    }
    if restart is None:
        comparison["restart_equivalent"] = False
        comparison["restart"] = {
            "performed": False,
            "reason": "no interrupted run was executed"}
    else:
        comparison["restart_equivalent"] = bool(restart.get("equivalent"))
        comparison["restart"] = dict(restart)
    return comparison


def run_benchmark(registration: Mapping[str, Any], *, runs_root: Path,
                  release_root: Path, workspace_root: Path,
                  data_root: Path = ROOT,
                  fault_injection: bool = True) -> dict[str, Any]:
    """Execute both arms on the bound inputs and compare them.

    Holds the shared demand-workspace lock for the WHOLE benchmark: acquiring
    it per arm would let another writer rearrange `sumo/` between the two, and
    then the arms would not have run against the same demand.
    """
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    from traffic_sim.simulation.workspace import WorkspaceLock

    selected = registration["selected_case"]
    spec = ClosureSearchSpec.from_dict(
        {key: value for key, value in selected["spec"].items()
         if key != "content_key"})
    if spec.content_key != selected["search_content_key"]:
        raise SystemExit(
            "the registered spec no longer rebuilds to its content key; the "
            "contract changed since the registration was frozen")

    policies = {
        arm: MonthlySearchPolicy.from_dict(json.loads(
            (ROOT / registration["policies"][arm]["path"])
            .read_text(encoding="utf-8")))
        for arm in ("exhaustive", "cost_ordered")
    }
    roots = registration["output_roots"]
    daily_cost_cache = ROOT / roots["daily_cost_cache"]

    data_root = Path(data_root).resolve()
    registered_root = Path(registration.get("data_root", data_root)).resolve()
    if registered_root != data_root:
        raise SystemExit(
            "the benchmark data root differs from the registered data root: "
            f"{data_root} != {registered_root}")

    lock = WorkspaceLock(
        f"cost_ordered_benchmark {os.getpid()}",
        path=data_root / "runs" / ".demand-workspace.lock",
    )
    if not lock.acquire(timeout=3600.0, poll_s=10.0):
        raise SystemExit(
            f"demand workspace busy: {lock.holder_description()}")
    previous_cwd = Path.cwd()
    try:
        # run_scenario's deployed network paths are intentionally relative.
        # Execute both arms from the root whose network was registered so the
        # bytes verified above are exactly the bytes SUMO consumes.
        os.chdir(data_root)
        arms = {}
        for arm, cost_ordered in (("exhaustive", False),
                                  ("cost_ordered", True)):
            arms[arm] = pa.run_arm(
                spec, policies[arm],
                cost_ordered=cost_ordered,
                # Separate roots per arm: one workspace is keyed by search_id,
                # so both arms in one root would resume each other's evidence
                # and the comparison would be with itself.
                workspace_root=Path(workspace_root) / roots[arm].split("/")[-1],
                runs_root=runs_root,
                release_root=release_root,
                daily_cost_cache=daily_cost_cache,
                study_provenance_key=f"cost-ordered-benchmark-{arm}",
            )
        restart = None
        if fault_injection:
            restart = _restart_probe(
                spec, policies["cost_ordered"],
                workspace_root=Path(workspace_root) / "restart",
                runs_root=runs_root, release_root=release_root,
                daily_cost_cache=daily_cost_cache,
                reference=arms["cost_ordered"])
    finally:
        os.chdir(previous_cwd)
        lock.release()
    return {"arms": arms,
            "comparison": compare_arms(arms["exhaustive"],
                                       arms["cost_ordered"], restart=restart)}


def _restart_probe(spec, policy, *, workspace_root: Path, runs_root: Path,
                   release_root: Path, daily_cost_cache: Path,
                   reference: Mapping[str, Any]) -> dict[str, Any]:
    """Interrupt a cost-ordered run, resume it, and compare the outcome.

    A durable cursor nobody ever crashes into is a claim, not a property. The
    probe kills the arm after its first verification and resumes it in a fresh
    process-equivalent call; the resumed answer must equal the uninterrupted
    one, and the resume must not re-simulate published work.
    """
    from traffic_sim.simulation.monthly_search import run_monthly_search

    class _Interrupt(RuntimeError):
        pass

    runner, screen_builder, cost_source = pa.build_arm(
        spec, cost_ordered=True, runs_root=runs_root,
        release_root=release_root, daily_cost_cache=daily_cost_cache,
        study_provenance_key="cost-ordered-benchmark-restart",
        objective_method=policy.objective_method)

    original = runner.run_candidate
    simulated: list[str] = []

    def interrupting(schedule, **kwargs):
        if kwargs.get("stage") == "pilot" and simulated:
            raise _Interrupt("benchmark fault injection")
        if kwargs.get("stage") == "pilot":
            simulated.append(schedule.schedule_id)
        return original(schedule, **kwargs)

    runner.run_candidate = interrupting            # type: ignore[assignment]
    interrupted = False
    try:
        run_monthly_search(spec, policy, runner=runner,
                           screen_builder=screen_builder,
                           root=Path(workspace_root), cost_source=cost_source)
    except _Interrupt:
        interrupted = True
    except (OSError, ValueError, RuntimeError) as error:
        return {"performed": True, "equivalent": False,
                "reason": f"the interrupted arm failed for another reason: {error}"}
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    if not interrupted:
        return {"performed": True, "equivalent": False,
                "reason": "the arm finished before it could be interrupted; "
                          "no restart was exercised"}

    resumed = pa.run_arm(
        spec, policy, cost_ordered=True,
        workspace_root=Path(workspace_root),
        runs_root=runs_root, release_root=release_root,
        daily_cost_cache=daily_cost_cache,
        study_provenance_key="cost-ordered-benchmark-restart")
    equivalent = (
        _final_decision(resumed["result"])
        == _final_decision(reference["result"])
        and _candidate_costs(resumed) == _candidate_costs(reference))
    return {
        "performed": True,
        "equivalent": bool(equivalent),
        "interrupted_after_pilots": len(simulated),
        "resumed_final_decision": _final_decision(resumed["result"]),
        "reference_final_decision": _final_decision(reference["result"]),
        "reason": ("the resumed run reproduced the uninterrupted outcome"
                   if equivalent else
                   "the resumed run did NOT reproduce the uninterrupted "
                   "outcome"),
    }


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
    parser.add_argument(
        "--data-root", type=Path, default=ROOT,
        help="root containing the active sumo/ network used by both arms")
    parser.add_argument("--registration", type=Path,
                        default=DEFAULT_REGISTRATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--release-root", type=Path,
                        default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--workspace-root", type=Path,
                        default=ROOT / "runs")
    parser.add_argument("--no-fault-injection", action="store_true",
                        help="skip the interrupt/resume probe (the restart "
                             "gate then fails, which is the honest outcome)")
    parser.add_argument("--from-archives", action="store_true",
                        help="discover cases from the archive library's own "
                             "metadata instead of the fixed v1 specs")
    parser.add_argument("--allow-drift", action="store_true",
                        help="run even though bound inputs moved; the drift "
                             "is recorded in the outcome")
    args = parser.parse_args(argv)

    if args.preregister == args.run:
        raise SystemExit("choose exactly one of --preregister or --run")

    if args.preregister:
        record = build_registration(
            args.runs_root,
            from_archives=args.from_archives,
            data_root=args.data_root,
            outcome_path=args.out,
        )
        if args.from_archives and record["selected_case"] is None:
            # Refusing here is the whole lesson of v1, which was frozen against
            # an empty runs directory and then could not be corrected without
            # editing history. A registration that selected nothing is not
            # evidence of anything except which machine it ran on.
            raise SystemExit(
                "discovery found no structurally eligible case under "
                f"{args.runs_root} "
                f"({record['blocked_by']['archives_available']} complete "
                "archives). Refusing to freeze an empty registration: run this "
                "on the host that holds the calibrated archive library.")
        if args.stdout:
            print(json.dumps(record, indent=1, sort_keys=True))
        else:
            _write(args.registration, record, overwrite=args.overwrite)
            print(f"wrote {args.registration} (status={record['status']})")
        return 0 if record["selected_case"] is not None else 3

    registration = json.loads(
        Path(args.registration).read_text(encoding="utf-8"))
    schema = str(registration.get("schema"))
    if schema not in SUPPORTED_REGISTRATION_SCHEMAS:
        raise SystemExit(
            f"unsupported registration schema {schema!r}; this tool reads "
            f"{', '.join(SUPPORTED_REGISTRATION_SCHEMAS)}")
    if registration.get("selected_case") is None:
        raise SystemExit(
            "the registration selected no case; there is nothing to run. "
            f"Its blocker: {registration.get('blocked_by', {}).get('reason')}")

    drift = verify_bindings(registration, args.runs_root)
    if drift and not args.allow_drift:
        raise SystemExit(
            "the registration's bound inputs no longer describe this tree, so "
            "a run would measure something nobody registered:\n  - "
            + "\n  - ".join(drift)
            + "\nRe-register (a NEW version; never edit a frozen one) or "
              "restore the bound inputs.")

    # The registration named an outcome; a run must write exactly that file,
    # or the two records stop describing the same experiment. Checked AFTER
    # binding verification so a reviewer who also got --out wrong still learns
    # that the inputs moved, which is the more important finding.
    declared_outcome = registration.get("outcome_record")
    if declared_outcome and _relative(Path(args.out).resolve()) != (
            declared_outcome):
        raise SystemExit(
            f"the registration names {declared_outcome} as its outcome but "
            f"--out is {_relative(Path(args.out).resolve())}. A run must write "
            f"the record its registration froze, or the two stop describing "
            f"the same experiment.")

    try:
        executed = run_benchmark(
            registration,
            runs_root=args.runs_root,
            release_root=args.release_root,
            workspace_root=args.workspace_root,
            data_root=args.data_root,
            fault_injection=not args.no_fault_injection,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        comparison = {
            "execution_error": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "binding_drift_accepted": drift,
            "workspace_root": str(Path(args.workspace_root).resolve()),
        }
        outcome = build_outcome(
            registration,
            comparison,
            status="failed_execution",
            registration_path=args.registration,
        )
        if args.stdout:
            print(json.dumps(outcome, indent=1, sort_keys=True))
        else:
            _write(args.out, outcome, overwrite=args.overwrite)
            print(
                f"wrote {args.out} (status=failed_execution, "
                f"error={type(error).__name__}: {error})",
                file=sys.stderr,
            )
        return 5
    comparison = dict(executed["comparison"])
    if drift:
        comparison["binding_drift_accepted"] = drift
    outcome = build_outcome(
        registration,
        comparison,
        status="measured",
        registration_path=args.registration,
    )
    if args.stdout:
        print(json.dumps(outcome, indent=1, sort_keys=True))
        return 0 if outcome["gates"]["passed"] else 4
    _write(args.out, outcome, overwrite=args.overwrite)
    print(f"wrote {args.out} (status={outcome['status']}, "
          f"gates_passed={outcome['gates']['passed']}, "
          f"saved={comparison['sumo_verifications_saved']} of "
          f"{comparison['exhaustive_sumo_candidates']})")
    return 0 if outcome["gates"]["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
