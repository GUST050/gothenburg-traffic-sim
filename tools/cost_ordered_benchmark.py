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
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
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
from traffic_sim.simulation import cost_ordered_execution as coe  # noqa: E402
from traffic_sim.simulation import cost_ordered_search as cos  # noqa: E402
from traffic_sim.simulation.monthly_search import (  # noqa: E402
    MonthlySearchPolicy,
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
    # The bounded Phase 3 runner imports this before either arm starts so RSS
    # and reap evidence cannot be published without a trusted process-tree
    # census.  It is therefore part of the executable evidence contract, not
    # merely a diagnostic helper.
    "tools/process_census.py",
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
    # `run_scenario.py` (already sealed) imports this on every closure/
    # candidate run since the 2026-08-29 root-cause fix (pre-SUMO origin-
    # to-destination rerouting) -- it decides which trips are unaffected,
    # rerouted, or denied, so a change here can change a benchmark result
    # exactly as much as a change to `disruption.py` can.
    "traffic_sim/simulation/closure_routing.py",
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
    # The shared Phase 6 eligibility predicate. `_run_case` calls
    # `decision_population_complete` on every paired comparison, so this
    # module decides whether a bounded population may become Gate S input
    # at all. Leaving it unsealed would let the rule that admits evidence
    # change without registering as drift.
    "traffic_sim/simulation/phase6_eligibility.py",
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
#: The ONE semantic study identity both compared arms run under. Every
#: observation's `provenance_key` (part of the cached, comparable evidence
#: content — see `PairedObservation`) is stamped with whatever
#: `study_provenance_key` the arm was built with, so giving the two arms
#: DIFFERENT values — as this benchmark used to, via an f-string suffixed
#: with the arm name — made their evidence differ by a label baked into
#: semantic content even when everything the simulation actually measured
#: was identical, and neither `_candidate_costs` nor `compare_arms` ever
#: looked at the field to notice. Which arm produced a given result is an
#: ORCHESTRATION fact (the caller already has it — `run_arm`'s own
#: returned `"arm"` key, the workspace path, the cache-snapshot root) and
#: belongs there, never inside evidence bytes the two arms are supposed to
#: be comparable on. `study_provenance_key` itself is confirmed unused for
#: cache-identity or path derivation elsewhere —
#: `IndependentDailyRunner._stable_backend_identity` already strips it
#: before hashing a daily unit's cache key, and `_baseline_cache_key` never
#: reads it — so unifying it changes no cache behaviour, only what
#: `provenance_key` reads on a `PairedObservation`.
BENCHMARK_STUDY_PROVENANCE_KEY = "cost-ordered-benchmark"

GATE_THRESHOLDS = {
    "candidate_costs_field_identical": True,
    "hard_failures_identical": True,
    "health_classifications_identical": True,
    "timeout_outcomes_identical": True,
    "ledger_population_complete": True,
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
    "timeout_outcomes",
    "ledger_population_completeness",
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


def _workspace_tree_size(path: Path) -> int:
    """Return durable bytes under one arm's workspace, or zero if empty."""
    return sum(item.stat().st_size for item in path.rglob("*")
               if item.is_file())


def _tree_digest(root: Path) -> str:
    """Stable content digest of every file under `root`.

    A missing directory and an existing-but-empty one both digest as "zero
    files" — they are the same cache content, and a clone of an absent
    source is legitimately an empty directory. Used to prove a cloned
    per-arm cache started from EXACTLY the bytes its source snapshot held,
    never merely "the same directory name".
    """
    root = Path(root)
    entries = [
        f"{path.relative_to(root).as_posix()}:{sha256_file(path)}"
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ] if root.is_dir() else []
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _bind_daily_results_source_snapshot(daily_cost_cache: Path) -> dict[str, str]:
    """Bind ONE immutable source digest before any arm clones from it.

    `_isolated_daily_results_cache_root` used to recompute the source
    digest independently on every call, so if the shared `daily-results`
    directory had already drifted once — before either arm's clone
    started, not merely between them — both clones would silently agree on
    the same drifted content and `_assert_fresh_snapshot_pair_matches`
    (which only ever compared the two DESTINATIONS to each other) could
    never see it. Calling this once, before either arm starts, and requiring
    every later clone to reproduce this exact digest turns that gap into a
    hard failure instead of a silent acceptance.
    """
    source = Path(daily_cost_cache).parent / "daily-results"
    return {"path": str(source), "digest": _tree_digest(source)}


def _assert_daily_results_source_unchanged(bound_source: Mapping[str, str]) -> None:
    """The bound source must still read back exactly as it did when bound."""
    current_digest = _tree_digest(Path(bound_source["path"]))
    if current_digest != bound_source["digest"]:
        raise RuntimeError(
            "the shared daily-results source at "
            f"{bound_source['path']} changed since it was bound for this "
            f"comparison ({current_digest} != {bound_source['digest']}); a "
            "benchmark comparison requires one stable immutable source "
            "snapshot for every arm it clones")


def _isolated_daily_results_cache_root(
    daily_cost_cache: Path, arm: str, *, bound_source: Mapping[str, str],
) -> tuple[Path, str]:
    """One (case, arm)'s own FRESH daily-results cache, cloned from the

    shared immutable snapshot.

    `product_arm.build_arm` used to derive the real per-daily-unit SUMO
    evidence cache for BOTH arms from `daily_cost_cache.parent /
    "daily-results"` unchanged, so whichever arm ran second could silently
    reuse the first arm's already-computed results — corrupting exactly the
    attempt-count/wall-time numbers this benchmark exists to measure. A
    first repair keyed the destination on the arm name alone
    (`daily_cost_cache.parent / "daily-results-<arm>"`), but a suite case
    only ever changes `daily_cost_cache`'s BASENAME (see
    `cost_ordered_benchmark_suite.run_suite`), not its parent — so every
    case in a suite still collided on the SAME per-arm root, and a
    pre-existing destination was silently reused unconditionally, accepting
    arbitrary leftover or cross-case state with no verification at all.

    The destination is now keyed on `daily_cost_cache`'s FULL path (parent
    AND basename) plus the arm name, so distinct cases — which get distinct
    `daily_cost_cache` basenames — can never collide. A pre-existing
    destination is now ALWAYS refused rather than silently reused: a fresh
    clone from the shared immutable source is required every time this is
    called. The one legitimate exception — a restart probe resuming its own
    just-interrupted attempt — never calls this function twice; it reuses
    the SAME already-obtained root object across its own interrupt/resume
    pair (see `_restart_probe`), so reuse is possible only where the caller
    itself is provably the same probe, not through any flag or bypass this
    function offers.
    """
    if arm not in (
            "exhaustive", "cost_ordered", "ordered_exhaustive",
            "restart_probe"):
        raise ValueError(f"unknown arm: {arm!r}")
    daily_cost_cache = Path(daily_cost_cache)
    source = daily_cost_cache.parent / "daily-results"
    if str(source) != bound_source["path"]:
        raise RuntimeError(
            f"the {arm} arm's daily-results source ({source}) does not "
            f"match the source bound for this comparison "
            f"({bound_source['path']})")
    _assert_daily_results_source_unchanged(bound_source)
    dest = daily_cost_cache.parent / f"{daily_cost_cache.name}-daily-results-{arm}"
    if dest.exists():
        raise RuntimeError(
            f"a daily-results cache root already exists at {dest}; a fresh "
            "comparison never reuses a pre-existing destination — remove it "
            "or choose a new case/output-root identity. (A restart probe "
            "reuses its OWN interrupted root by holding the same Python "
            "object across its interrupt/resume pair, never by calling "
            "this function a second time for the same destination.)")
    source_digest = bound_source["digest"]
    if source.is_dir():
        import shutil

        shutil.copytree(source, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    dest_digest = _tree_digest(dest)
    if dest_digest != source_digest:
        raise RuntimeError(
            "cloning the initial daily-results cache snapshot for the "
            f"{arm} arm did not reproduce its source content "
            f"({dest_digest} != {source_digest})")
    return dest, dest_digest


def _assert_fresh_snapshot_pair_matches(
    daily_cost_cache: Path, arms: Sequence[str],
    cache_snapshots: Mapping[str, Mapping[str, str]],
) -> None:
    """Both arms must start from the identical immutable initial snapshot.

    Unconditional: `_isolated_daily_results_cache_root` never tolerates a
    pre-existing destination any more, so every call into this comparison
    clones fresh from the shared source, and a digest mismatch between the
    two arms can only mean that shared source drifted between the two
    clones — never a legitimately resumed root's expected divergence, which
    no longer exists as a case to exempt.
    """
    digests = {arm: cache_snapshots[arm]["digest"] for arm in arms}
    if len(set(digests.values())) > 1:
        raise RuntimeError(
            "the two arms' daily-results cache snapshots were cloned from "
            f"different content ({digests}); the shared source at "
            f"{Path(daily_cost_cache).parent / 'daily-results'} must not "
            "change while a benchmark comparison is cloning both arms from "
            "it")


def _assert_fresh_arm_workspace(
    workspace_root: Path, spec: ClosureSearchSpec,
) -> None:
    """One arm's search workspace must start with no prior evidence.

    `product_arm.build_arm` opens the workspace via `open_search_workspace`,
    which happily RESUMES an existing directory that matches the same spec
    `content_key` — exactly right for a real interrupted campaign, and
    exactly wrong for a benchmark comparison arm, which must start from a
    clean slate or its attempt/wall-time numbers silently include whatever
    an earlier, unrelated invocation already published there. The one
    legitimate exception, an interrupted-then-resumed restart probe, never
    calls this: it runs from its own dedicated `workspace_root / "restart"`
    and deliberately resumes the SAME interrupted attempt within one probe.
    """
    candidate = Path(workspace_root) / spec.search_id
    if candidate.exists():
        raise RuntimeError(
            f"a search workspace already exists at {candidate}; a fresh "
            "comparison arm never resumes prior evidence — remove it or "
            "choose a new workspace root/search_id")


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
        "timeout_outcomes_identical": bool(
            comparison.get("timeout_outcomes_identical")),
        "ledger_population_complete": bool(
            comparison.get("ledger_population_complete")),
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
    from traffic_sim.simulation.search_workspace import load_search_workspace

    workspace = Path(arm.get("workspace", ""))
    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        return {}
    loaded = load_search_workspace(workspace, verify=True)
    records = [record for record in loaded.manifest.get("artifacts", ())
               if record.get("kind") == kind]
    if len(records) > 1:
        raise ValueError(f"workspace has duplicate {kind} artifacts")
    if records:
        payload = json.loads(
            (workspace / records[0]["path"]).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"workspace {kind} artifact must be an object")
        return payload
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
            # `timeout_undecided` entries are now validated TimeoutIdentity
            # records (published as objects, not bare strings), so they sort
            # by their canonical JSON form rather than natively.
            "timeout_undecided": sorted(
                (item.get("timeout_undecided") or ()), key=_canonical),
        }
    return out


def _candidate_semantic_evidence(
    arm: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Full pilot+finalist evidence per candidate: observations, disruption,

    provenance — none of which `_candidate_costs` carries.

    `PilotCandidateStatistics` (what the published `monthly_pilot_selection`
    artifact and therefore `_candidate_costs` are built from) never held
    observations, disruption records or provenance keys at all — those live
    only in the per-candidate `monthly_pilot_candidate`/
    `monthly_finalist_candidate` evidence artifacts each stage publishes.
    Reusing `monthly_search._evidence_records` for both kinds means the same
    round-dedup and candidate/provenance consistency checks the product
    itself runs also gate this comparison, rather than re-deriving a second,
    possibly divergent, notion of "the latest published evidence" here.
    """
    from traffic_sim.simulation import monthly_search as _ms
    from traffic_sim.simulation.search_workspace import load_search_workspace

    workspace_dir = Path(arm.get("workspace", ""))
    if not (workspace_dir / "manifest.json").is_file():
        return {}
    workspace = load_search_workspace(workspace_dir, verify=True)
    merged: dict[str, dict[str, Any]] = {}
    for kind, stage in (
            ("monthly_pilot_candidate", "pilot"),
            ("monthly_finalist_candidate", "finalist")):
        grouped = _ms._evidence_records(workspace, kind=kind)
        for candidate_id, rounds in grouped.items():
            _round_index, evidence = rounds[-1]
            merged.setdefault(candidate_id, {})[stage] = {
                "hard_failures": sorted(evidence.hard_failures),
                "observations": sorted(
                    (asdict(item) for item in evidence.observations),
                    key=_canonical),
                "disruption": sorted(
                    (dict(item) for item in evidence.disruption),
                    key=_canonical),
                "timeout_undecided": sorted(
                    (item.to_dict() for item in evidence.timeout_undecided),
                    key=_canonical),
                "canonical_observation_digests": sorted(
                    (item.to_dict() for item in
                     evidence.canonical_observation_digests),
                    key=_canonical),
                "provenance_keys": sorted({
                    observation.provenance_key
                    for observation in evidence.observations}),
            }
    return merged


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


def _raw_cost_ledger(arm: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The published cost ledger's raw, self-verifying bytes.

    Unlike `_ledger_costs`, which reduces this to a per-candidate cost view,
    this is the whole artifact — needed to reconstruct a real `CostLedger`
    object via `CostLedger.from_dict` (which checks its own `content_key`
    against its own body) for an independent `bound_identity()` call.
    """
    workspace = Path(arm.get("workspace", ""))
    ledger_path = workspace / "artifacts" / "cost-ledger.json"
    if not ledger_path.is_file():
        return None
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, Mapping) else None


def _published_search_policy(arm: Mapping[str, Any]) -> MonthlySearchPolicy | None:
    """The full `MonthlySearchPolicy` this arm actually ran under.

    Reads the `monthly_search_policy` workspace artifact rather than any
    value carried inside the execution record or its proof — the whole
    point is a policy the proof did not author. Callers needing only the
    pilot half (`bound_identity`) or only the finalist half (the
    practical-equivalence band) both read it from this ONE object, so there
    is exactly one place a policy artifact is parsed.
    """
    raw = _workspace_artifact(arm, "monthly_search_policy")
    if not raw:
        return None
    return MonthlySearchPolicy.from_dict(raw)


def _final_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    decision = result.get("robust_decision") or {}
    return {
        "status": result.get("status"),
        "winner_id": result.get("winner_id"),
        "tie_ids": sorted(result.get("tie_ids") or ()),
        "robust_decision": dict(decision),
    }


def _independently_recompute_stop_proof(
    execution: Mapping[str, Any], proof: Mapping[str, Any],
    arm: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute EVERY bound field of the proof from OTHER published artifacts.

    Every field below is mandatory — a proof that simply omits a binding
    fails exactly as hard as one that states it wrong, because an absent
    field previously passed this check by default. Everything is checked
    against something the proof itself did not produce, so a proof edited in
    isolation — the exact failure mode a proof exists to rule out — cannot
    pass silently:

      * the execution record's OWN `content_key` is recomputed over its own
        body first. `stop_proof`, `cursor` and `disable_early_stop` are all
        nested fields of this ONE published record, so any isolated edit to
        any of them breaks this one check without this function needing to
        know in advance which nested field moved.
      * `verified_prefix_digest`/`evidence_digest` are recomputed from
        `execution["cursor"]["verified"]`/`["viable"]`, published by
        `CostOrderedState.to_dict()` independently of `stop_proof()` itself
        (same source object, but a SEPARATE field an editor would also have
        to touch), using the identical canonical-JSON digest formula. Each
        verified candidate's cursor `viable` bit is then cross-checked
        against its full published `eligible` evidence (`_candidate_costs`),
        so the digest's own REDUCTION of the evidence is also checked for
        faithfulness, not merely for internal consistency.
      * the undecided set is recomputed from the per-candidate evidence this
        comparison already parsed (`_candidate_costs`, which reads
        `timeout_undecided` off the published pilot-selection candidates),
        not merely echoed from the proof — this is cost-order v5's exact
        failure mode: a timeout dropped from the proof while still present
        in the evidence it was supposed to summarise.
      * for a band stop, the first unexamined candidate's cost is looked up
        directly in the published cost ledger (`cost-ledger.json`), a file
        the proof never wrote.
      * `identity_key` is recomputed from scratch by calling
        `cost_ordered_execution.bound_identity()` — the SAME function that
        produced it — over a `CostLedger` and `PilotPolicy` loaded from two
        SEPARATELY published artifacts (`cost-ledger.json`, whose own
        `content_key` is verified by `CostLedger.from_dict`, and the
        `monthly_search_policy` workspace artifact), rather than trusted
        from anything the proof or execution record declares. The ledger's
        own `search_content_key`/`provider_identity` are cross-checked
        against the arm's independently published values too.
    """
    problems: list[str] = []

    execution_body = {key: value for key, value in execution.items()
                      if key != "content_key"}
    claimed_execution_key = execution.get("content_key")
    if not claimed_execution_key:
        problems.append("execution record is missing its own content_key")
    elif claimed_execution_key != _content_key(execution_body):
        problems.append(
            "execution record content_key does not match its own body "
            "(stop_proof, cursor or disable_early_stop was edited without "
            "the record's own digest following)")

    cursor = execution.get("cursor") or {}
    validated_cursor = None
    try:
        validated_cursor = cos.CostOrderedState.from_dict(cursor)
    except (TypeError, ValueError) as error:
        problems.append(f"published cursor is invalid: {error}")
    published_verified = [str(item) for item in (cursor.get("verified") or ())]
    published_viable_order = [str(item)
                              for item in (cursor.get("viable") or ())]
    published_viable = set(published_viable_order)
    published_order = [str(item) for item in (cursor.get("order") or ())]
    published_position = (
        validated_cursor.cursor if validated_cursor is not None else -1)
    if tuple(published_order[:max(published_position, 0)]) != tuple(
            published_verified):
        problems.append(
            "published cursor position does not match the published "
            "order/verified prefix")

    recomputed_verified_prefix_digest = _content_key(published_verified)
    claimed_verified_prefix_digest = proof.get("verified_prefix_digest")
    if not claimed_verified_prefix_digest:
        problems.append("proof is missing verified_prefix_digest")
    elif claimed_verified_prefix_digest != recomputed_verified_prefix_digest:
        problems.append(
            "verified_prefix_digest does not match the published cursor's "
            "verified list")

    semantic_evidence = _candidate_semantic_evidence(arm)
    recomputed_evidence: list[dict[str, Any]] = []
    for candidate_id in published_verified:
        stage = (semantic_evidence.get(candidate_id) or {}).get("pilot")
        if stage is None:
            problems.append(
                f"{candidate_id}: published pilot decision evidence is missing")
            continue
        recomputed_evidence.append({
            "candidate_id": candidate_id,
            "eligible": not bool(stage["hard_failures"]),
            "hard_failures": stage["hard_failures"],
            "observations": stage["observations"],
            "disruption": stage["disruption"],
            "timeout_undecided": stage["timeout_undecided"],
        })
    recomputed_evidence_digest = _content_key(recomputed_evidence)
    claimed_evidence_digest = proof.get("evidence_digest")
    if not claimed_evidence_digest:
        problems.append("proof is missing evidence_digest")
    elif claimed_evidence_digest != recomputed_evidence_digest:
        problems.append(
            "evidence_digest does not match the full published pilot "
            "decision evidence")

    candidate_costs = _candidate_costs(arm)
    for candidate_id in published_verified:
        published = candidate_costs.get(candidate_id) or {}
        cursor_viable = candidate_id in published_viable
        evidence_eligible = bool(published.get("eligible"))
        if cursor_viable != evidence_eligible:
            problems.append(
                f"{candidate_id}: cursor viable={cursor_viable} does not "
                f"match its published eligible={evidence_eligible}")

    actual_undecided = sorted(
        candidate_id for candidate_id in published_verified
        if (candidate_costs.get(candidate_id) or {}).get("timeout_undecided"))
    if "undecided_candidate_ids" not in proof:
        problems.append("proof is missing undecided_candidate_ids")
    claimed_undecided = sorted(str(item) for item in
                               (proof.get("undecided_candidate_ids") or ()))
    if actual_undecided != claimed_undecided:
        problems.append(
            "undecided_candidate_ids does not match the published "
            f"per-candidate timeout evidence (evidence names {actual_undecided}, "
            f"proof names {claimed_undecided})")

    expected_disable_early_stop = arm.get("arm") == "ordered_exhaustive"
    execution_disable = execution.get("disable_early_stop")
    proof_disable = proof.get("disable_early_stop")
    if not isinstance(execution_disable, bool):
        problems.append("execution disable_early_stop is not a native boolean")
    elif execution_disable != expected_disable_early_stop:
        problems.append(
            "execution disable_early_stop does not match the independently "
            f"published arm mode ({execution_disable} != "
            f"{expected_disable_early_stop})")
    if not isinstance(proof_disable, bool):
        problems.append("proof disable_early_stop is not a native boolean")
    elif proof_disable != expected_disable_early_stop:
        problems.append(
            "proof disable_early_stop does not match the independently "
            f"published arm mode ({proof_disable} != "
            f"{expected_disable_early_stop})")

    claimed_identity_key = proof.get("identity_key")
    if not claimed_identity_key:
        problems.append("proof is missing identity_key")
    raw_ledger = _raw_cost_ledger(arm)
    search_policy = _published_search_policy(arm)
    pilot_policy = search_policy.pilot if search_policy is not None else None
    if raw_ledger is None:
        problems.append(
            "no published cost ledger to recompute identity_key/order against")
    if search_policy is None:
        problems.append(
            "no published pilot policy to recompute identity_key/band "
            "against")

    ledger = None
    if raw_ledger is not None:
        try:
            ledger = coe.CostLedger.from_dict(raw_ledger)
        except ValueError as error:
            problems.append(f"published cost ledger is invalid: {error}")
            ledger = None

    # The order and the first-unexamined identity are recomputed from the
    # ledger and the published cursor's OWN position, not accepted from
    # whatever the proof names — a proof could otherwise point at a
    # different, cheaper ledger candidate than the one the cursor actually
    # stopped before, and this check would never see it.
    recomputed_order: list[str] = []
    recomputed_first_unexamined: str | None = None
    recomputed_cutoff: float | None = None
    recomputed_selection_band: float | None = None
    recomputed_stop_reason: str | None = None
    recomputed_argument: str | None = None
    recomputed_total = 0
    recomputed_examined = published_position
    recomputed_unexamined = -1
    if ledger is not None:
        ordered_costs, _disqualified = cos.plan_order(ledger.candidates)
        recomputed_order = [cost.candidate_id for cost in ordered_costs]
        if recomputed_order != published_order:
            problems.append(
                "the published cursor order does not match the cost order "
                "independently recomputed from the published cost ledger")
        recomputed_first_unexamined = (
            published_order[published_position]
            if 0 <= published_position < len(published_order) else None)

        recomputed_total = len(recomputed_order)
        recomputed_unexamined = (
            recomputed_total - published_position
            if 0 <= published_position <= recomputed_total else -1)
        if len(published_viable_order) >= (
                pilot_policy.minimum_finalists if pilot_policy else 10**18):
            kth_id = published_viable_order[
                pilot_policy.minimum_finalists - 1]
            ledger_costs = {cost.candidate_id: cost for cost in ordered_costs}
            kth_cost = ledger_costs.get(kth_id)
            if kth_cost is None:
                problems.append(
                    "the kth viable candidate is absent from the verified "
                    "published cost ledger")
            else:
                recomputed_cutoff = float(kth_cost.added_vehicle_hours)
        if (recomputed_cutoff is not None
                and search_policy is not None
                and str((execution or {}).get("terminal_status", ""))
                not in {"INCONCLUSIVE_TIMEOUT", "INCONCLUSIVE_CAPACITY",
                        "INCONCLUSIVE_BUDGET_EXHAUSTED"}):
            recomputed_selection_band = (
                recomputed_cutoff
                + float(search_policy.finalist
                        .practical_equivalence_vehicle_hours))

        terminal_reason = {
            "INCONCLUSIVE_TIMEOUT": "inconclusive_timeout",
            "INCONCLUSIVE_CAPACITY": "inconclusive_capacity",
            "INCONCLUSIVE_BUDGET_EXHAUSTED": (
                "inconclusive_budget_exhausted"),
        }.get(str((execution or {}).get("terminal_status", "")))
        if terminal_reason == "inconclusive_timeout" and not actual_undecided:
            problems.append(
                "timeout terminal is missing unresolved timeout evidence")
        if terminal_reason is not None:
            recomputed_stop_reason = terminal_reason
        elif recomputed_total == 0:
            recomputed_stop_reason = "no_viable_candidates"
        elif published_position == recomputed_total:
            recomputed_stop_reason = "search_space_exhausted"
        elif (
            0 <= published_position < recomputed_total
            and not expected_disable_early_stop
            and not actual_undecided
            and recomputed_selection_band is not None
        ):
            ledger_costs = {cost.candidate_id: cost for cost in ordered_costs}
            first_cost = ledger_costs.get(str(recomputed_first_unexamined))
            if (first_cost is not None
                    and float(first_cost.added_vehicle_hours)
                    > recomputed_selection_band):
                recomputed_stop_reason = "band_exhausted"

        if recomputed_stop_reason in {
                "inconclusive_timeout", "inconclusive_capacity",
                "inconclusive_budget_exhausted"}:
            recomputed_argument = (
                "the execution reached a terminal inconclusive condition; no "
                "unexamined candidate is claimed decision-irrelevant")
        elif recomputed_stop_reason == "band_exhausted":
            recomputed_argument = (
                "the order is by added_vehicle_hours first, so every unexamined "
                "candidate costs at least as much as the first one, which is "
                "strictly above the selection band; none can be retained, and "
                "none can move the cutoff because every cheaper candidate was "
                "already verified")
        elif recomputed_stop_reason == "search_space_exhausted":
            recomputed_argument = (
                "every candidate that survived the deterministic no-detour gate "
                "was verified, so the finalist set is the exhaustive one")
        elif recomputed_stop_reason == "no_viable_candidates":
            recomputed_argument = (
                "no candidate survived the deterministic no-detour gate, so "
                "there was nothing to verify")

        ledger_costs = {cost.candidate_id: cost for cost in ordered_costs}
        recomputed_first_cost = (
            ledger_costs.get(str(recomputed_first_unexamined))
            if recomputed_first_unexamined is not None else None)

        expected_fields = {
            "schema": cos.SCHEMA,
            "minimum_finalists": (
                pilot_policy.minimum_finalists if pilot_policy else None),
            "cutoff_added_vehicle_hours": recomputed_cutoff,
            "selection_band_added_vehicle_hours": recomputed_selection_band,
            "examined": recomputed_examined,
            "total_ordered": recomputed_total,
            "unexamined": recomputed_unexamined,
            "stop_reason": recomputed_stop_reason,
            "argument": recomputed_argument,
            "first_unexamined_candidate_id": recomputed_first_unexamined,
            "first_unexamined_added_vehicle_hours": (
                None if recomputed_first_cost is None
                else float(recomputed_first_cost.added_vehicle_hours)),
        }
        # These fields were added with terminal results. Keep old diagnostic
        # records readable, but require them whenever a new terminal proof is
        # being checked.
        if ("terminal_status" in proof
                or recomputed_stop_reason in {
                    "inconclusive_timeout", "inconclusive_capacity",
                    "inconclusive_budget_exhausted"}):
            expected_fields.update({
                "terminal_status": (
                    {"inconclusive_timeout": "INCONCLUSIVE_TIMEOUT",
                     "inconclusive_capacity": "INCONCLUSIVE_CAPACITY",
                     "inconclusive_budget_exhausted": (
                         "INCONCLUSIVE_BUDGET_EXHAUSTED")}
                    .get(recomputed_stop_reason)),
                "valid_for_ready": recomputed_stop_reason not in {
                    "inconclusive_timeout", "inconclusive_capacity",
                    "inconclusive_budget_exhausted"},
            })
        for field, expected in expected_fields.items():
            if proof.get(field) != expected:
                problems.append(
                    f"proof {field} does not match the value independently "
                    f"recomputed from cursor/evidence/policy/ledger "
                    f"({proof.get(field)!r} != {expected!r})")
        cursor_reason = cursor.get("stop_reason")
        if cursor_reason != recomputed_stop_reason:
            problems.append(
                "cursor stop_reason does not match the terminal condition "
                "independently recomputed from cursor/evidence/policy/ledger "
                f"({cursor_reason!r} != {recomputed_stop_reason!r})")
        if cursor.get("cutoff") != recomputed_cutoff:
            problems.append(
                "cursor cutoff does not match the kth viable candidate cost "
                f"({cursor.get('cutoff')!r} != {recomputed_cutoff!r})")

        if str(proof.get("stop_reason", "")) == "band_exhausted":
            claimed_first_id = proof.get("first_unexamined_candidate_id")
            if not claimed_first_id:
                problems.append(
                    "band stop is missing first_unexamined_candidate_id")
            elif str(claimed_first_id) != str(recomputed_first_unexamined):
                problems.append(
                    "first_unexamined_candidate_id does not match the "
                    "candidate independently derived from the published "
                    f"order and cursor position (proof names "
                    f"{claimed_first_id}, order/cursor name "
                    f"{recomputed_first_unexamined})")
            claimed_cost = proof.get("first_unexamined_added_vehicle_hours")
            if claimed_cost is None:
                problems.append(
                    "band stop is missing "
                    "first_unexamined_added_vehicle_hours")
            ledger_costs = {cost.candidate_id: cost for cost in ordered_costs}
            recomputed_cost = (
                ledger_costs.get(str(recomputed_first_unexamined))
                if recomputed_first_unexamined is not None else None)
            if recomputed_first_unexamined is not None and recomputed_cost is None:
                problems.append(
                    "the independently derived first unexamined candidate "
                    "is not in the published cost ledger")
            if (recomputed_cost is not None and claimed_cost is not None
                    and float(recomputed_cost.added_vehicle_hours)
                    != float(claimed_cost)):
                problems.append(
                    "first_unexamined_added_vehicle_hours does not match "
                    "the published cost ledger for the independently "
                    f"derived candidate ({claimed_cost} != "
                    f"{recomputed_cost.added_vehicle_hours})")
    elif str(proof.get("stop_reason", "")) == "band_exhausted":
        problems.append(
            "cannot verify a band stop without a valid published cost "
            "ledger")

    # The band width is read from the published finalist policy, not from
    # the proof's own declared value — the whole point of an independent
    # check is that the proof cannot supply the number it is being judged
    # against.
    claimed_equivalence = proof.get("practical_equivalence_vehicle_hours")
    independent_equivalence = (
        search_policy.finalist.practical_equivalence_vehicle_hours
        if search_policy is not None else None)
    if independent_equivalence is None:
        problems.append(
            "no published search policy to recompute the practical "
            "equivalence band against")
    elif (claimed_equivalence is None
          or float(claimed_equivalence) != float(independent_equivalence)):
        problems.append(
            "practical_equivalence_vehicle_hours does not match the "
            "published finalist policy's own value (proof names "
            f"{claimed_equivalence}, policy names {independent_equivalence})")

    recomputed_identity_key = None
    if ledger is not None and pilot_policy is not None:
        equivalence_for_identity = (
            0.0 if independent_equivalence is None
            else float(independent_equivalence))
        recomputed_identity_key = coe.bound_identity(
            ledger, pilot_policy,
            practical_equivalence_vehicle_hours=equivalence_for_identity,
            disable_early_stop=expected_disable_early_stop,
        )
        if (claimed_identity_key
                and claimed_identity_key != recomputed_identity_key):
            problems.append(
                "identity_key does not match the ledger/policy/band "
                "independently recomputed via bound_identity()")
        if ledger.search_content_key != str(
                arm.get("search_content_key", "")):
            problems.append(
                "cost ledger search_content_key does not match the "
                "arm's own published search_content_key")
        if execution.get("provider_identity") != dict(
                ledger.provider_identity):
            problems.append(
                "execution provider_identity does not match the "
                "published cost ledger's provider_identity")

    return {
        "valid": not problems,
        "problems": problems,
        "recomputed_verified_prefix_digest": recomputed_verified_prefix_digest,
        "recomputed_evidence_digest": recomputed_evidence_digest,
        "recomputed_undecided_candidate_ids": actual_undecided,
        "recomputed_order": recomputed_order,
        "recomputed_first_unexamined_candidate_id": recomputed_first_unexamined,
        "recomputed_minimum_finalists": (
            pilot_policy.minimum_finalists if pilot_policy else None),
        "recomputed_cutoff_added_vehicle_hours": recomputed_cutoff,
        "recomputed_selection_band_added_vehicle_hours": (
            recomputed_selection_band),
        "recomputed_examined": recomputed_examined,
        "recomputed_total_ordered": recomputed_total,
        "recomputed_unexamined": recomputed_unexamined,
        "recomputed_stop_reason": recomputed_stop_reason,
        "recomputed_disable_early_stop": expected_disable_early_stop,
        "recomputed_identity_key": recomputed_identity_key,
    }


def _stop_proof_valid(
    execution: Mapping[str, Any] | None,
    *, arm: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-derive the stop rule from the published proof, rather than trust it.

    A stop proof produced only by the code it justifies proves nothing, so the
    arithmetic is redone here against the proof's OWN declared numbers, and —
    when `arm` is supplied — every bound field is ALSO independently
    recomputed from other published artifacts
    (`_independently_recompute_stop_proof`), not merely re-checked against
    itself:

      * `band_exhausted` — the first unexamined candidate must be STRICTLY
        above cutoff + practical equivalence. That is the entire argument for
        not having simulated it, and `>=` would be the classic off-by-one that
        skips a candidate still inside the band. A band stop is ALSO invalid
        if the proof names any `undecided_candidate_ids`: an unresolved
        timeout means the cutoff it was computed from cannot be trusted, so
        it can never justify skipping anything (cost-order v5's failure
        mode). Proofs from before this field existed omit it entirely and
        are not penalised for that absence — only a proof that explicitly
        lists undecided candidates is failed on this ground.
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
            result = {"valid": False, "stop_reason": reason,
                      "reason": "a band stop must name its band and the "
                                "first unexamined candidate"}
        else:
            undecided = proof.get("undecided_candidate_ids") or []
            if undecided:
                result = {
                    "valid": False, "stop_reason": reason,
                    "undecided_candidate_ids": list(undecided),
                    "reason": (
                        "the proof stopped at the band while unresolved "
                        f"timeout evidence remained for {sorted(undecided)}; "
                        "a stop can never rest on undecided evidence"),
                }
            else:
                valid = float(first) > float(band)
                result = {
                    "valid": valid, "stop_reason": reason,
                    "selection_band_added_vehicle_hours": float(band),
                    "first_unexamined_added_vehicle_hours": float(first),
                    "reason": (
                        "the first unexamined candidate is strictly above "
                        "the band, so no unexamined candidate could be "
                        "retained" if valid else
                        "the first unexamined candidate is INSIDE the band; "
                        "it should have been verified"),
                }
    elif reason in {"inconclusive_timeout", "inconclusive_capacity",
                    "inconclusive_budget_exhausted"}:
        expected_status = {
            "inconclusive_timeout": "INCONCLUSIVE_TIMEOUT",
            "inconclusive_capacity": "INCONCLUSIVE_CAPACITY",
            "inconclusive_budget_exhausted": "INCONCLUSIVE_BUDGET_EXHAUSTED",
        }[reason]
        declared_status = execution.get("terminal_status")
        timeout_ids = proof.get("undecided_candidate_ids") or []
        valid = (
            declared_status == expected_status
            and proof.get("valid_for_ready") is False
            and proof.get("selection_band_added_vehicle_hours") is None
            and (reason != "inconclusive_timeout" or bool(timeout_ids))
        )
        result = {
            "valid": valid,
            "stop_reason": reason,
            "terminal_status": declared_status,
            "reason": (
                "terminal result is explicit and publishes no READY proof"
                if valid else
                "terminal result is missing its fail-closed bindings"),
        }
    elif reason == "search_space_exhausted":
        valid = (int(proof.get("unexamined", -1)) == 0
                 and int(proof.get("examined", -1))
                 == int(proof.get("total_ordered", -2)))
        result = {"valid": valid, "stop_reason": reason,
                  "reason": ("every ordered candidate was verified"
                             if valid else
                             "the proof claims exhaustion but left "
                             "candidates unexamined")}
    else:
        valid = int(proof.get("total_ordered", -1)) == 0
        result = {"valid": valid, "stop_reason": reason,
                  "reason": ("nothing survived the deterministic no-detour "
                             "gate" if valid else
                             f"unrecognised stop reason {reason!r} with "
                             "candidates still ordered")}
    if arm is not None:
        independent = _independently_recompute_stop_proof(execution, proof, arm)
        result["independent_recomputation"] = independent
        result["valid"] = bool(result["valid"]) and bool(independent["valid"])
    return result


def _execution_contract(arm: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the decision-facing execution fields with fail-closed shape checks."""
    execution = (arm.get("result") or {}).get("cost_ordered_execution")
    if not isinstance(execution, Mapping):
        return {"valid": False, "reason": "execution record is missing"}
    required = {"status", "terminal_status", "selected_ids", "cursor"}
    if not required <= set(execution):
        return {"valid": False, "reason": "execution contract fields are missing"}
    selected_ids = execution.get("selected_ids")
    cursor = execution.get("cursor")
    if (not isinstance(selected_ids, list)
            or any(not isinstance(item, str) or not item for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
            or not isinstance(cursor, Mapping)):
        return {"valid": False, "reason": "execution contract fields are malformed"}
    return {
        "valid": True,
        "status": execution.get("status"),
        "terminal_status": execution.get("terminal_status"),
        "selected_ids": list(selected_ids),
        "cursor": dict(cursor),
    }


def _restart_attempt_surface(arm: Mapping[str, Any]) -> tuple[bool, dict[tuple, dict[str, Any]]]:
    """Return the exact launch identity population, rejecting duplicates."""
    records = arm.get("exact_launch_records")
    if not isinstance(records, list) or not records:
        return False, {}
    fields = {"candidate_id", "work_date", "stage", "variant", "seed",
              "attempt", "timed_out", "outcome"}
    result: dict[tuple, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != fields:
            return False, {}
        identity = tuple(record.get(key) for key in (
            "candidate_id", "work_date", "stage", "variant", "seed", "attempt"))
        if identity in result:
            return False, {}
        result[identity] = dict(record)
    return True, result


def _runner_exact_launch_records(runner: Any) -> list[dict[str, Any]]:
    """Snapshot exact launch records retained by an interrupted runner."""
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        return []
    try:
        raw = snapshot()
    except Exception:  # optional diagnostic hook; the caller fails closed
        return []
    records = raw.get("exact_launch_records") if isinstance(raw, Mapping) else None
    return [dict(item) for item in records
            if isinstance(item, Mapping)] if isinstance(records, list) else []


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
    timeout_mismatches = []
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
        # A candidate that hit the frozen SUMO timeout in one arm and not the
        # other is not a benign difference: it means the two arms cannot agree
        # this candidate was ever DECIDED, undecided-timeout evidence must
        # never be silently dropped from the comparison it is supposed to
        # block (see CandidateEvidence.timeout_undecided).
        if a["timeout_undecided"] != b["timeout_undecided"]:
            timeout_mismatches.append({
                "candidate_id": candidate_id,
                "exhaustive": a["timeout_undecided"],
                "cost_ordered": b["timeout_undecided"]})

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
    stop_proof_check = _stop_proof_valid(execution, arm=cost_ordered)

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
    # The ledger is built BEFORE any simulation and is supposed to price the
    # EXACT same candidate population the exhaustive arm evaluated — that is
    # what makes "every ledger-priced candidate matches exhaustive" a claim
    # about the COMPLETE required population rather than about whichever
    # subset both happened to cover. An intersection-only comparison would
    # silently pass even if the two populations barely overlapped, so any
    # divergence between them is reported and gated on explicitly.
    ledger_only_candidates = sorted(set(ledger) - set(left))
    left_only_vs_ledger_candidates = sorted(set(left) - set(ledger))
    ledger_population_complete = (
        not ledger_only_candidates and not left_only_vs_ledger_candidates)

    comparison: dict[str, Any] = {
        # The cost-ordered arm SIMULATES fewer candidates, so its pilot
        # statistics cover only those. Candidates only the exhaustive arm
        # simulated are reported, never silently dropped — and the ledger
        # comparison below covers ALL of them anyway.
        "compared_candidate_count": len(shared),
        "ledger_compared_candidate_count": len(ledger_compared),
        "ledger_costs_field_identical": not ledger_mismatches,
        "ledger_cost_mismatches": ledger_mismatches[:50],
        "ledger_population_complete": ledger_population_complete,
        "ledger_only_candidates": ledger_only_candidates[:50],
        "left_only_vs_ledger_candidates": left_only_vs_ledger_candidates[:50],
        "exhaustive_only_candidates": sorted(set(left) - set(right)),
        "cost_ordered_only_candidates": sorted(set(right) - set(left)),
        "candidate_costs_field_identical": (
            not cost_mismatches and not ledger_mismatches),
        "candidate_cost_mismatches": cost_mismatches[:50],
        "hard_failures_identical": not failure_mismatches,
        "hard_failure_mismatches": failure_mismatches[:50],
        "health_classifications_identical": not health_mismatches,
        "health_mismatches": health_mismatches[:50],
        "timeout_outcomes_identical": not timeout_mismatches,
        "timeout_outcome_mismatches": timeout_mismatches[:50],
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
                  fault_injection: bool = True,
                  counterbalance: bool = False,
                  isolate_arms: bool = True) -> dict[str, Any]:
    """Execute both arms on the bound inputs and compare them.

    Holds the shared demand-workspace lock for the WHOLE benchmark: acquiring
    it per arm would let another writer rearrange `sumo/` between the two, and
    then the arms would not have run against the same demand.

    Each arm runs in its own process and process group
    (`product_arm.run_arm_isolated`) at the frozen benchmark resource shape —
    `daily_workers=1`, `seed_workers=1`, `max_active_sumo_slots=1` — so
    neither arm's timing or peak RSS can be contaminated by the other arm's
    interpreter state, and a hang in one can be reaped without touching the
    other. `counterbalance=True` runs cost_ordered before exhaustive instead
    of the reverse, for a multi-case suite that wants arm order to average
    out across cases rather than always giving the same arm the
    later-running (and therefore differently host-loaded) position.
    `isolate_arms=False` runs both arms in this process instead — this
    exists ONLY so tests can monkeypatch `product_arm.build_arm` with a
    SUMO-free fake (a subprocess would not see that monkeypatch); a real
    benchmark run must never pass it.
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

    data_root = Path(data_root).resolve()
    registered_root = Path(registration.get("data_root", data_root)).resolve()
    if registered_root != data_root:
        raise SystemExit(
            "the benchmark data root differs from the registered data root: "
            f"{data_root} != {registered_root}")
    # Anchored on `data_root`, not the module-global `ROOT`: `data_root`
    # defaults to `ROOT` (see `--data-root`'s CLI default), so real usage is
    # unaffected, but a test that binds a different `data_root` now also gets
    # an isolated daily-results cache instead of colliding with whatever a
    # previous real run already published under the real repo's `runs/`.
    daily_cost_cache = data_root / roots["daily_cost_cache"]

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
        arm_order = (("exhaustive", False), ("cost_ordered", True))
        if counterbalance:
            arm_order = tuple(reversed(arm_order))
        arms = {}
        cache_snapshots: dict[str, dict[str, str]] = {}
        # Bound ONCE, before either arm clones from it — see
        # `_bind_daily_results_source_snapshot` for why binding it per-call
        # (the old behaviour) could not detect a source that had already
        # drifted before the first clone even started.
        bound_source = _bind_daily_results_source_snapshot(daily_cost_cache)
        for arm, cost_ordered in arm_order:
            arm_workspace_root = (
                Path(workspace_root) / roots[arm].split("/")[-1])
            _assert_fresh_arm_workspace(arm_workspace_root, spec)
            # Each arm gets its OWN daily-results cache, cloned once from a
            # shared initial snapshot. Passing the same `daily_cost_cache`
            # unchanged to both arms used to let whichever ran second reuse
            # the first arm's real SUMO results — see
            # `_isolated_daily_results_cache_root`.
            arm_cache_root, arm_cache_digest = (
                _isolated_daily_results_cache_root(
                    daily_cost_cache, arm, bound_source=bound_source))
            cache_snapshots[arm] = {
                "root": str(arm_cache_root), "digest": arm_cache_digest}
            if isolate_arms:
                arms[arm] = pa.run_arm_isolated(
                    spec, policies[arm],
                    cost_ordered=cost_ordered,
                    # Separate roots per arm: one workspace is keyed by
                    # search_id, so both arms in one root would resume each
                    # other's evidence and the comparison would be with
                    # itself.
                    workspace_root=arm_workspace_root,
                    runs_root=runs_root,
                    release_root=release_root,
                    daily_cost_cache=daily_cost_cache,
                    daily_results_cache_root=arm_cache_root,
                    study_provenance_key=BENCHMARK_STUDY_PROVENANCE_KEY,
                    data_root=data_root,
                    seed_workers=pa.BENCHMARK_SEED_WORKERS,
                    daily_workers=pa.BENCHMARK_DAILY_WORKERS,
                    max_active_sumo_slots=pa.BENCHMARK_MAX_ACTIVE_SUMO_SLOTS,
                )
            else:
                arms[arm] = pa.run_arm(
                    spec, policies[arm],
                    cost_ordered=cost_ordered,
                    workspace_root=arm_workspace_root,
                    runs_root=runs_root,
                    release_root=release_root,
                    daily_cost_cache=daily_cost_cache,
                    daily_results_cache_root=arm_cache_root,
                    study_provenance_key=BENCHMARK_STUDY_PROVENANCE_KEY,
                    seed_workers=pa.BENCHMARK_SEED_WORKERS,
                    daily_workers=pa.BENCHMARK_DAILY_WORKERS,
                    max_active_sumo_slots=pa.BENCHMARK_MAX_ACTIVE_SUMO_SLOTS,
                )
        if cache_snapshots["exhaustive"]["root"] == (
                cache_snapshots["cost_ordered"]["root"]):
            raise RuntimeError(
                "both arms were assigned the same daily-results cache root; "
                "refusing to publish a comparison that could not tell a "
                "structural saving from a shared cache hit")
        _assert_fresh_snapshot_pair_matches(
            daily_cost_cache, ("exhaustive", "cost_ordered"), cache_snapshots)
        # Re-verify the bound source is still exactly what it was when
        # bound: a mismatch here means the shared source mutated WHILE the
        # arms were cloning from it, which the pair-match check above cannot
        # see (both clones could still agree with each other on drifted
        # content if they happened to run after the same mutation).
        _assert_daily_results_source_unchanged(bound_source)
        cache_snapshots["_source"] = dict(bound_source)
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
    comparison = compare_arms(
        arms["exhaustive"], arms["cost_ordered"], restart=restart)
    comparison["daily_results_cache_snapshots"] = cache_snapshots
    return {"arms": arms, "comparison": comparison}


def _restart_probe(spec, policy, *, workspace_root: Path, runs_root: Path,
                   release_root: Path, daily_cost_cache: Path,
                   reference: Mapping[str, Any],
                   fixture_controls: Mapping[str, Any] | None = None,
                   require_attempt_identity: bool = False,
                   max_exact_launches: int | None = None,
                   timeout_s: float | None = None,
                   qualified_demand_manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Interrupt a cost-ordered run, resume it, and compare the outcome.

    A durable cursor nobody ever crashes into is a claim, not a property. The
    probe kills the arm after its first verification and resumes it in a fresh
    process-equivalent call; the resumed answer must equal the uninterrupted
    one, and the resume must not re-simulate published work.

    Uses its OWN dedicated daily-results cache root (`arm="restart_probe"`),
    obtained ONCE and held across both the interrupted attempt and the
    resumed one — the one place in this module reuse of a daily-results
    cache root is legitimate, because it is the SAME Python object handed to
    two calls belonging to one probe, never a second, independent call into
    `_isolated_daily_results_cache_root` for an already-existing
    destination. Without this the probe fell back to the shared, unisolated
    `daily-results` source directory itself, so an interrupted/resumed probe
    run could mutate the very snapshot every other arm clones from.
    """
    from traffic_sim.simulation.monthly_search import run_monthly_search

    class _Interrupt(RuntimeError):
        pass

    class _RestartTimeout(TimeoutError):
        pass

    def bounded_call(operation):
        """Bound in-process restart work while preserving runner cleanup."""
        if timeout_s is None:
            return operation()
        limit = float(timeout_s)
        if limit <= 0:
            raise ValueError("restart probe timeout must be positive")
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("bounded restart probe must run on the main thread")
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 0.0)

        def alarm(_signum, _frame):
            raise _RestartTimeout(
                f"restart probe operation exceeded {limit:.3f}s")

        signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, limit)
        try:
            return operation()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)

    def timeout_result(error: BaseException) -> dict[str, Any]:
        return {
            "performed": True,
            "equivalent": False,
            "status": "INCONCLUSIVE_RESTART_TIMEOUT",
            "terminal": True,
            "timeout_s": float(timeout_s),
            "reason": str(error),
            "cancellation": {
                "called": False,
                "queued_work_cancelled": False,
                "no_later_starter": False,
            },
        }

    probe_bound_source = _bind_daily_results_source_snapshot(daily_cost_cache)
    probe_cache_root, _probe_cache_digest = _isolated_daily_results_cache_root(
        daily_cost_cache, "restart_probe", bound_source=probe_bound_source)

    runner, screen_builder, cost_source = pa.build_arm(
        spec, cost_ordered=True, runs_root=runs_root,
        release_root=release_root, daily_cost_cache=daily_cost_cache,
        daily_results_cache_root=probe_cache_root,
        study_provenance_key="cost-ordered-benchmark-restart",
        objective_method=policy.objective_method,
        qualified_demand_manifest=qualified_demand_manifest)
    fixture_state: dict[str, Any] = {}
    if fixture_controls:
        runner = pa._FixtureRunner(runner, fixture_controls, fixture_state)
        if cost_source is not None:
            cost_source = pa._FixtureCostSource(
                cost_source, fixture_controls, fixture_state)

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
    active_started = time.monotonic()
    try:
        bounded_call(lambda: run_monthly_search(
            spec, policy, runner=runner, screen_builder=screen_builder,
            root=Path(workspace_root), cost_source=cost_source,
            max_exact_launches=max_exact_launches))
    except _RestartTimeout as error:
        result = timeout_result(error)
        result["active_elapsed_s"] = time.monotonic() - active_started
        return result
    except _Interrupt:
        interrupted = True
    except (OSError, ValueError, RuntimeError) as error:
        return {"performed": True, "equivalent": False,
                "reason": f"the interrupted arm failed for another reason: {error}",
                "active_elapsed_s": time.monotonic() - active_started}
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    if not interrupted:
        return {"performed": True, "equivalent": False,
                "reason": "the arm finished before it could be interrupted; "
                          "no restart was exercised",
                "active_elapsed_s": time.monotonic() - active_started,
                "cancellation": {"called": False,
                                 "queued_work_cancelled": False,
                                 "no_later_starter": False}}

    cancel = getattr(runner, "stop_speculative_work", None)
    if not fixture_controls:
        cancellation = {
            "called": False,
            "queued_work_cancelled": False,
            "no_later_starter": True,
        }
    elif not callable(cancel):
        return {"performed": True, "equivalent": False,
                "reason": "restart was interrupted but no cancellation hook exists",
                "active_elapsed_s": time.monotonic() - active_started,
                "cancellation": {"called": False,
                                 "queued_work_cancelled": False,
                                 "no_later_starter": False}}
    else:
        cancel()
        cancellation = dict(fixture_state.get("cancel_observed") or {})
        cancellation.update({
            "called": True,
            "queued_work_cancelled": cancellation.get("queued_after") == 0,
            "no_later_starter": cancellation.get("no_later_starter") is True,
        })

    try:
        resumed = bounded_call(lambda: pa.run_arm(
            spec, policy, cost_ordered=True,
            workspace_root=Path(workspace_root),
            runs_root=runs_root, release_root=release_root,
            daily_cost_cache=daily_cost_cache,
            daily_results_cache_root=probe_cache_root,
            study_provenance_key="cost-ordered-benchmark-restart",
            fixture_controls=fixture_controls,
            max_exact_launches=max_exact_launches,
            qualified_demand_manifest=qualified_demand_manifest))
    except _RestartTimeout as error:
        result = timeout_result(error)
        result["active_elapsed_s"] = time.monotonic() - active_started
        return result
    resumed_contract = _execution_contract(resumed)
    reference_contract = _execution_contract(reference)
    cursor_identical = bool(
        resumed_contract["valid"] and reference_contract["valid"]
        and resumed_contract["cursor"] == reference_contract["cursor"])
    evidence_identical = False
    try:
        evidence_identical = (
            _candidate_semantic_evidence(resumed)
            == _candidate_semantic_evidence(reference))
    except (OSError, KeyError, TypeError, ValueError):
        evidence_identical = False
    resumed_attempts_valid, resumed_attempts = _restart_attempt_surface(resumed)
    reference_attempts_valid, reference_attempts = _restart_attempt_surface(reference)
    attempt_identity_identical = bool(
        resumed_attempts_valid and reference_attempts_valid
            and resumed_attempts == reference_attempts)
    interrupted_attempts_valid, interrupted_attempts = _restart_attempt_surface({
        "exact_launch_records": _runner_exact_launch_records(runner)
    })
    if not require_attempt_identity and not resumed_attempts and not reference_attempts:
        # Older in-process diagnostic fakes do not expose the launch seam. The
        # registered subhour path passes require_attempt_identity=True; keep
        # this compatibility mode only for the legacy unregistered benchmark.
        attempt_identity_identical = True
    terminal_status_identical = bool(
        resumed_contract["valid"] and reference_contract["valid"]
        and resumed_contract["terminal_status"]
        == reference_contract["terminal_status"])
    selected_ids_identical = bool(
        resumed_contract["valid"] and reference_contract["valid"]
        and resumed_contract["selected_ids"]
        == reference_contract["selected_ids"])
    if not require_attempt_identity and not resumed_contract["valid"] \
            and not reference_contract["valid"]:
        terminal_status_identical = True
        selected_ids_identical = True
    equivalent = bool(
        _final_decision(resumed["result"])
        == _final_decision(reference["result"])
        and _candidate_costs(resumed) == _candidate_costs(reference)
        and cursor_identical and evidence_identical
        and attempt_identity_identical and terminal_status_identical
        and selected_ids_identical)
    return {
        "performed": True,
        "equivalent": bool(equivalent),
        "resumed_final_decision": _final_decision(resumed["result"]),
        "reference_final_decision": _final_decision(reference["result"]),
        "cursor_identical": cursor_identical,
        "evidence_identical": evidence_identical,
        "attempt_identity_identical": attempt_identity_identical,
        "exact_launch_attempts": (
            len(interrupted_attempts) if interrupted_attempts_valid else 0
        ) + (len(resumed_attempts) if resumed_attempts_valid else 0),
        "active_elapsed_s": time.monotonic() - active_started,
        "terminal_status_identical": terminal_status_identical,
        "selected_ids_identical": selected_ids_identical,
        "reason": ("the resumed run reproduced the uninterrupted outcome"
                   if equivalent else
                   "the resumed run did NOT reproduce the uninterrupted "
                   "outcome"),
        "cancellation": cancellation,
        "interrupted_after_pilots": len(simulated),
    }


def _total_exact_attempts(arm: Mapping[str, Any]) -> int | None:
    """Every real SUMO (variant, seed) launch this arm made, pilot+finalist.

    Reads `product_arm.run_arm`'s `exact_launch_telemetry` — the actual
    launch seam inside `ArchivedDemandSumoRunner`, not `sumo_pilot_count`'s
    approximation from the pilot CANDIDATE count. A cache hit at the daily-
    unit level never reaches that seam, so it is structurally excluded here
    too. Returns `None` only when the arm published no telemetry at all
    (an older result, or a backend without the hook), so a caller can tell
    "zero real launches" from "no data" instead of conflating them.
    """
    telemetry = arm.get("exact_launch_telemetry")
    if not isinstance(telemetry, Mapping) or not telemetry:
        return None
    total = 0
    for stage in ("pilot", "finalist"):
        counts = telemetry.get(stage)
        if not isinstance(counts, Mapping):
            continue
        total += int(counts.get("attempts", 0))
    return total


def _exact_attempt_population_check(
    cost_ordered: Mapping[str, Any], ordered_exhaustive: Mapping[str, Any],
) -> dict[str, Any]:
    """cost_ordered's real SUMO launches vs. ordered_exhaustive's, by identity.

    The candidate-level "verified sequence" prefix check
    (`cost_ordered_attempt_population_is_valid_prefix` below) has no
    variant/seed/attempt granularity at all — a candidate can need several
    (variant, seed) launches, and a launch can be retried — so it cannot by
    itself support calling anything an exact-ATTEMPT population. This checks
    the real thing: every (candidate, work date, stage, variant, seed,
    attempt) launch cost_ordered made must also appear, with an identical
    outcome, among ordered_exhaustive's launches.  For candidates in the
    shared verified prefix the populations must be identical; exhaustive may
    have additional launches only for candidates strictly after that prefix.
    """
    def records(arm: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
        raw = arm.get("exact_launch_records")
        return raw if isinstance(raw, list) else None

    left_records = records(cost_ordered)
    right_records = records(ordered_exhaustive)
    if left_records is None or right_records is None:
        return {
            "valid": False,
            "problems": [
                "one or both arms published no exact_launch_records; the "
                "exact-attempt population cannot be independently checked"],
            "extra_cost_ordered_launches": [],
            "outcome_mismatches": [],
            "cost_ordered_launch_count": None,
            "ordered_exhaustive_launch_count": None,
        }

    expected_record_fields = {
        "candidate_id", "work_date", "stage", "variant", "seed", "attempt",
        "timed_out", "outcome",
    }

    def identity(record: Any) -> tuple:
        if not isinstance(record, Mapping) or set(record) != expected_record_fields:
            raise ValueError("exact launch record fields are invalid")
        if not isinstance(record["candidate_id"], str) or not record["candidate_id"]:
            raise ValueError("exact launch candidate_id is invalid")
        if not isinstance(record["work_date"], str):
            raise ValueError("exact launch work_date is invalid")
        try:
            parsed_date = date.fromisoformat(record["work_date"])
        except ValueError as error:
            raise ValueError("exact launch work_date is invalid") from error
        if parsed_date.isoformat() != record["work_date"]:
            raise ValueError("exact launch work_date is not canonical")
        if record["stage"] not in {"pilot", "finalist"}:
            raise ValueError("exact launch stage is invalid")
        if record["variant"] not in VARIANT_FILENAMES:
            raise ValueError("exact launch variant is invalid")
        for field, minimum in (("seed", 0), ("attempt", 1)):
            value = record[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"exact launch {field} is invalid")
        if not isinstance(record["timed_out"], bool):
            raise ValueError("exact launch timed_out must be boolean")
        if not isinstance(record["outcome"], str) or not record["outcome"]:
            raise ValueError("exact launch outcome is invalid")
        return (
            record["candidate_id"], record["work_date"], record["stage"],
            record["variant"], record["seed"], record["attempt"])

    def index_with_duplicates(
        records_list: list[Mapping[str, Any]], label: str,
    ) -> tuple[dict[tuple, Mapping[str, Any]], list[dict[str, Any]]]:
        by_identity: dict[tuple, Mapping[str, Any]] = {}
        duplicates: list[dict[str, Any]] = []
        for record in records_list:
            key = identity(record)
            if key in by_identity:
                duplicates.append({
                    "identity": list(key),
                    "reason": f"duplicate launch identity in {label}"})
                continue
            by_identity[key] = record
        return by_identity, duplicates

    # Both arms are indexed the SAME way. The original check only detected
    # duplicates on the cost_ordered side and built ordered_exhaustive's
    # index with a bare dict comprehension, which silently collapses a
    # duplicate ordered_exhaustive identity into whichever record happened
    # to be last — a probe with one cost_ordered record and the same
    # ordered_exhaustive record duplicated used to report `valid: True`.
    left_by_identity, left_duplicates = index_with_duplicates(
        left_records, "cost_ordered")
    right_by_identity, right_duplicates = index_with_duplicates(
        right_records, "ordered_exhaustive")

    problems: list[str] = []
    extras: list[list[Any]] = []
    mismatches: list[dict[str, Any]] = list(left_duplicates) + list(
        right_duplicates)
    for key, record in left_by_identity.items():
        other = right_by_identity.get(key)
        if other is None:
            extras.append(list(key))
            continue
        if (record["timed_out"] != other["timed_out"]
                or record["outcome"] != other["outcome"]):
            mismatches.append({
                "identity": list(key),
                "cost_ordered": {"timed_out": record.get("timed_out"),
                                 "outcome": record.get("outcome")},
                "ordered_exhaustive": {"timed_out": other.get("timed_out"),
                                       "outcome": other.get("outcome")},
            })
    shared_candidates = {
        str(item) for item in
        (((cost_ordered.get("result") or {}).get("cost_ordered_execution") or {})
         .get("cursor") or {}).get("verified", ())
    }
    exhaustive_verified = [
        str(item) for item in
        (((ordered_exhaustive.get("result") or {}).get(
            "cost_ordered_execution") or {}).get("cursor") or {}).get(
                "verified", ())
    ]
    later_candidates = set(exhaustive_verified) - shared_candidates

    # Child-to-parent membership is derived STRUCTURALLY from the published
    # closure-search spec and `daily_unit_records`, not inferred from which
    # evidence collections happen to be non-empty. `_candidate_semantic_
    # evidence`'s `canonical_observation_digests`/`timeout_undecided`
    # collections only capture a launch that either produced a canonical
    # observation or hit the frozen SUMO timeout — a recognized non-timeout
    # SUMO failure (`_timeout_identity`: "a genuine hard failure, not an
    # undecided outcome, and returns no identity here") produces neither, so
    # inferring parents from those two collections alone silently drops such
    # a launch's parent and misreports it as unexplained. Independent daily
    # units are also intentionally reusable across parents
    # (`independent_daily.decompose_schedules`), so a child legitimately
    # belongs to a SET of parents, not exactly one.
    from traffic_sim.simulation.independent_daily import daily_unit_records

    exhaustive_launch_parents: dict[str, set[str]] = {}
    raw_exhaustive_spec = (
        (ordered_exhaustive.get("result") or {}).get("closure_search_spec"))
    if isinstance(raw_exhaustive_spec, Mapping):
        exhaustive_spec = ClosureSearchSpec.from_dict(raw_exhaustive_spec)
        for parent_schedule in iter_closure_schedules(exhaustive_spec):
            for _unit_id, _identity, build in daily_unit_records(
                    exhaustive_spec, parent_schedule):
                child_id = build().schedule_id
                exhaustive_launch_parents.setdefault(
                    child_id, set()).add(parent_schedule.schedule_id)
    else:
        problems.append(
            "ordered_exhaustive published no closure_search_spec; exact "
            "launch parent membership cannot be independently derived")

    left_launch_candidates = {key[0] for key in left_by_identity}
    exhaustive_verified_set = set(exhaustive_verified)
    unexplained_exhaustive_launches = []
    for key in right_by_identity:
        if key in left_by_identity:
            continue
        candidate_id = key[0]
        # A launch is explained as exhaustive-only when EVERY verified
        # parent that structurally contains this child lies beyond the
        # cost-ordered prefix. A child associated with a shared-prefix
        # parent (or with no verified parent at all, which is fail-closed
        # rather than assumed benign) is unexplained.
        applicable_parents = (
            exhaustive_launch_parents.get(candidate_id, set())
            & exhaustive_verified_set)
        explained = (
            candidate_id not in left_launch_candidates
            and bool(applicable_parents)
            and applicable_parents <= later_candidates)
        if not explained:
            unexplained_exhaustive_launches.append(list(key))
    if unexplained_exhaustive_launches:
        problems.append(
            "ordered_exhaustive published extra attempts for a shared or "
            "otherwise unexplained candidate")
    if extras:
        problems.append(
            "cost_ordered launched attempts that ordered_exhaustive's "
            "complete scan never launched")
    if left_duplicates:
        problems.append(
            "cost_ordered published duplicate launch identities")
    if right_duplicates:
        problems.append(
            "ordered_exhaustive published duplicate launch identities")
    if any("cost_ordered" in item and "ordered_exhaustive" in item
           for item in mismatches):
        problems.append(
            "a launch identity shared by both arms has a different outcome")

    # Reconcile the validated, de-duplicated record populations against
    # each arm's own AGGREGATE `exact_launch_telemetry` counters — the
    # original check trusted the aggregate blindly, so an aggregate
    # inflated (or deflated) relative to the identity-bearing records could
    # never be caught here.
    def validate_aggregate(
        arm: Mapping[str, Any], indexed: Mapping[tuple, Mapping[str, Any]],
        label: str,
    ) -> None:
        telemetry = arm.get("exact_launch_telemetry")
        if not isinstance(telemetry, Mapping) or set(telemetry) != {
                "pilot", "finalist"}:
            problems.append(f"{label}'s exact_launch_telemetry is malformed")
            return
        for stage in ("pilot", "finalist"):
            counts = telemetry[stage]
            if not isinstance(counts, Mapping) or set(counts) != {
                    "attempts", "timeouts", "other_outcomes"}:
                problems.append(f"{label}'s {stage} launch counters are malformed")
                continue
            if any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 0 for value in counts.values()):
                problems.append(f"{label}'s {stage} launch counters are malformed")
                continue
            stage_records = [record for key, record in indexed.items()
                             if key[2] == stage]
            expected = {
                "attempts": len(stage_records),
                "timeouts": sum(record["timed_out"] for record in stage_records),
                "other_outcomes": sum(not record["timed_out"]
                                      for record in stage_records),
            }
            if dict(counts) != expected:
                problems.append(
                    f"{label}'s {stage} aggregate launch counters disagree "
                    "with its validated exact_launch_records population")

    validate_aggregate(cost_ordered, left_by_identity, "cost_ordered")
    validate_aggregate(
        ordered_exhaustive, right_by_identity, "ordered_exhaustive")

    return {
        "valid": bool(not problems),
        "problems": problems,
        "extra_cost_ordered_launches": extras[:50],
        "unexplained_ordered_exhaustive_launches": (
            unexplained_exhaustive_launches[:50]),
        "outcome_mismatches": mismatches[:50],
        "cost_ordered_launch_count": len(left_by_identity),
        "ordered_exhaustive_launch_count": len(right_by_identity),
        "cost_ordered_raw_record_count": len(left_records),
        "ordered_exhaustive_raw_record_count": len(right_records),
    }


def compare_ordered_exhaustive(
    cost_ordered: Mapping[str, Any],
    ordered_exhaustive: Mapping[str, Any],
) -> dict[str, Any]:
    """Isolate early stopping as the ONLY variable between two exact runs.

    Both arms share the same cost ledger, candidate order, canonical seeds,
    retry policy and one-worker resource shape (`run_ordered_exhaustive_
    comparison` builds them that way); `ordered_exhaustive` only differs by
    `disable_early_stop=True`. Any measured difference here is therefore
    attributable to the stopping decision alone — unlike `compare_arms`
    against the product's `_bounded_exhaustive_builder` screening path, which
    also differs in code path, instrumentation and iteration order.

    This used to compare only the four numeric cost fields and the reduced
    final decision — a hard-failure or eligibility difference between the
    two arms passed silently. It now covers the complete required
    population: candidate costs, failures, health, timeouts, ledger
    identity, both arms' own stop proofs, and the claim that cost_ordered's
    verified sequence is an evidence-identical PREFIX of ordered_exhaustive's
    (the two scan the identical cost order, so anything else means the
    orders diverged or one arm skipped/duplicated a candidate the other
    did not).
    """
    left = _candidate_costs(cost_ordered)
    right = _candidate_costs(ordered_exhaustive)
    shared = sorted(set(left) & set(right))
    cost_fields = ("added_vehicle_hours", "added_metres_total",
                   "vehicles_affected", "vehicles_no_detour")
    cost_mismatches = []
    failure_mismatches = []
    health_mismatches = []
    timeout_mismatches = []
    for candidate_id in shared:
        a, b = left[candidate_id], right[candidate_id]
        for field in cost_fields:
            if a.get(field) != b.get(field):
                cost_mismatches.append({
                    "candidate_id": candidate_id, "field": field,
                    "cost_ordered": a.get(field),
                    "ordered_exhaustive": b.get(field)})
        if a["hard_failures"] != b["hard_failures"]:
            failure_mismatches.append({
                "candidate_id": candidate_id,
                "cost_ordered": a["hard_failures"],
                "ordered_exhaustive": b["hard_failures"]})
        if (a.get("eligible"), a.get("complete")) != (
                b.get("eligible"), b.get("complete")):
            health_mismatches.append({
                "candidate_id": candidate_id,
                "cost_ordered": [a.get("eligible"), a.get("complete")],
                "ordered_exhaustive": [b.get("eligible"), b.get("complete")]})
        if a["timeout_undecided"] != b["timeout_undecided"]:
            timeout_mismatches.append({
                "candidate_id": candidate_id,
                "cost_ordered": a["timeout_undecided"],
                "ordered_exhaustive": b["timeout_undecided"]})
    cost_ordered_only_candidates = sorted(set(left) - set(right))
    ordered_exhaustive_only_candidates = sorted(set(right) - set(left))

    # Pilot-candidate counts, kept for readability/back-compat reporting —
    # not what the 30% gates below are computed from.
    cost_ordered_pilot_candidates = pa.sumo_pilot_count(cost_ordered["result"])
    ordered_exhaustive_pilot_candidates = pa.sumo_pilot_count(
        ordered_exhaustive["result"])

    # The real gate: every SUMO (variant, seed) launch across BOTH pilot and
    # finalist stages, including hard failures, timeouts and retries as
    # attempts — never approximated from the pilot candidate count, which
    # both undercounts (misses finalist re-runs) and overcounts (one
    # candidate can need several variant/seed launches).
    cost_ordered_attempts = _total_exact_attempts(cost_ordered)
    ordered_exhaustive_attempts = _total_exact_attempts(ordered_exhaustive)
    attempts_reduction = (
        None
        if cost_ordered_attempts is None or not ordered_exhaustive_attempts
        else 1.0 - (float(cost_ordered_attempts)
                    / float(ordered_exhaustive_attempts)))

    wall_cost_ordered = float(cost_ordered["wall_time_s"])
    wall_ordered_exhaustive = float(ordered_exhaustive["wall_time_s"])
    wall_time_reduction = (
        None if wall_ordered_exhaustive <= 0 else
        1.0 - (wall_cost_ordered / wall_ordered_exhaustive))

    # Awake-active elapsed time (`search_workspace.ACTIVE_ELAPSED_BASIS`):
    # excludes wall-clock changes and system sleep, so a laptop suspended
    # mid-run cannot manufacture a speedup or a slowdown here the way raw
    # `wall_time_s` could. The two arms must declare the SAME basis — `or`
    # used to accept whichever one happened to be present, which could
    # silently compare two differently-defined clocks against each other.
    active_cost_ordered = cost_ordered.get("active_elapsed_s")
    active_ordered_exhaustive = ordered_exhaustive.get("active_elapsed_s")
    basis_cost_ordered = cost_ordered.get("active_elapsed_basis")
    basis_ordered_exhaustive = ordered_exhaustive.get("active_elapsed_basis")
    active_elapsed_basis_consistent = bool(
        basis_cost_ordered and basis_cost_ordered == basis_ordered_exhaustive)
    active_elapsed_basis = (
        basis_cost_ordered if active_elapsed_basis_consistent else None)
    awake_active_time_reduction = (
        None
        if (not active_elapsed_basis_consistent
            or active_cost_ordered is None or not active_ordered_exhaustive)
        else 1.0 - (float(active_cost_ordered)
                    / float(active_ordered_exhaustive)))

    final_decision_identical = (
        _final_decision(cost_ordered["result"])
        == _final_decision(ordered_exhaustive["result"]))

    # Both arms' own stop proofs are independently re-derived, not merely
    # each arm's own opinion of itself.
    cost_ordered_execution = cost_ordered["result"].get(
        "cost_ordered_execution")
    ordered_exhaustive_execution = ordered_exhaustive["result"].get(
        "cost_ordered_execution")
    cost_ordered_verified = [
        str(item) for item in
        ((cost_ordered_execution or {}).get("cursor") or {}).get(
            "verified", ())]
    ordered_exhaustive_verified = [
        str(item) for item in
        ((ordered_exhaustive_execution or {}).get("cursor") or {}).get(
            "verified", ())]
    cost_ordered_stop_proof_check = _stop_proof_valid(
        cost_ordered_execution, arm=cost_ordered)
    ordered_exhaustive_stop_proof_check = _stop_proof_valid(
        ordered_exhaustive_execution, arm=ordered_exhaustive)
    # `disable_early_stop=True` makes `band()` return `None` unconditionally
    # (see `run_cost_ordered_search`), so a genuine reference arm can never
    # legitimately report `band_exhausted` — only full exhaustion or an
    # empty candidate set. Seeing it anyway means the reference arm was not
    # actually built with early stopping disabled.
    ordered_exhaustive_stop_reason = str(
        ((ordered_exhaustive_execution or {}).get("stop_proof") or {})
        .get("stop_reason", ""))
    ordered_exhaustive_genuinely_exhaustive = bool(
        ordered_exhaustive_stop_proof_check["valid"]
        and ordered_exhaustive_stop_reason != "band_exhausted")

    # Ledger identity: both arms are built from what should be the SAME
    # frozen cost ledger. Comparing the published artifacts' own
    # self-verifying `content_key` (see `CostLedger.to_dict`) is a single
    # check that subsumes candidate population, prices and provider
    # identity/provenance together — any divergence in any of those changes
    # the digest.
    raw_ledger_cost_ordered = _raw_cost_ledger(cost_ordered)
    raw_ledger_ordered_exhaustive = _raw_cost_ledger(ordered_exhaustive)
    ledger_content_keys_identical = bool(
        raw_ledger_cost_ordered is not None
        and raw_ledger_ordered_exhaustive is not None
        and raw_ledger_cost_ordered.get("content_key")
        == raw_ledger_ordered_exhaustive.get("content_key"))

    # Cache-event population: both arms priced from the SAME deterministic
    # cost ledger BEFORE any SUMO ran, so that lookup population must agree
    # exactly regardless of which candidates a given arm went on to
    # simulate.
    cache_hits_consistent = bool(
        cost_ordered.get("daily_cost_cache_hits")
        == ordered_exhaustive.get("daily_cost_cache_hits"))

    # The daily-RESULT cache (real per-daily-unit SUMO evidence) is a
    # DIFFERENT population from the cost-ledger cache above, and unlike it
    # the two arms are NOT expected to match exactly: ordered_exhaustive
    # verifies a superset of cost_ordered's candidates, so it necessarily
    # performs at least as many real daily-unit cache lookups and
    # publications. What must still hold, and is checked here, is that
    # relationship — ordered_exhaustive can never show FEWER lookups or
    # publications than cost_ordered — and that neither arm recorded a
    # corrupt cache entry, which would make the evidence built from it
    # untrustworthy for any comparison.
    cost_ordered_cache_events = cost_ordered.get(
        "daily_results_cache_events") or {}
    ordered_exhaustive_cache_events = ordered_exhaustive.get(
        "daily_results_cache_events") or {}
    daily_results_cache_event_problems: list[str] = []
    if not cost_ordered_cache_events or not ordered_exhaustive_cache_events:
        daily_results_cache_event_problems.append(
            "one or both arms published no daily_results_cache_events; the "
            "daily-result cache-event population cannot be checked")
    else:
        expected_cache_counters = {
            "cache_hits", "cache_misses", "cache_corrupt",
            "cache_publications",
        }

        def validated_cache_counters(
            raw: Any, label: str,
        ) -> dict[str, int] | None:
            if not isinstance(raw, Mapping) or set(raw) != expected_cache_counters:
                daily_results_cache_event_problems.append(
                    f"{label} daily-results cache counters are malformed")
                return None
            if any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 0 for value in raw.values()):
                daily_results_cache_event_problems.append(
                    f"{label} daily-results cache counters are malformed")
                return None
            return dict(raw)

        left_cache_counts = validated_cache_counters(
            cost_ordered_cache_events, "cost_ordered")
        right_cache_counts = validated_cache_counters(
            ordered_exhaustive_cache_events, "ordered_exhaustive")
        if ((left_cache_counts or {}).get("cache_corrupt", 0)
                or (right_cache_counts or {}).get("cache_corrupt", 0)):
            daily_results_cache_event_problems.append(
                "a corrupt daily-results cache entry was recorded; its "
                "evidence cannot be trusted for this comparison")
        cost_ordered_lookups = (
            (left_cache_counts or {}).get("cache_hits", 0)
            + (left_cache_counts or {}).get("cache_misses", 0))
        ordered_exhaustive_lookups = (
            (right_cache_counts or {}).get("cache_hits", 0)
            + (right_cache_counts or {}).get("cache_misses", 0))
        if ordered_exhaustive_lookups < cost_ordered_lookups:
            daily_results_cache_event_problems.append(
                "ordered_exhaustive recorded fewer daily-results cache "
                "lookups than cost_ordered, which is impossible for an arm "
                "that verifies a superset of cost_ordered's candidates")
        cost_ordered_publications = (left_cache_counts or {}).get(
            "cache_publications", 0)
        ordered_exhaustive_publications = (right_cache_counts or {}).get(
            "cache_publications", 0)
        if ordered_exhaustive_publications < cost_ordered_publications:
            daily_results_cache_event_problems.append(
                "ordered_exhaustive published fewer daily-results cache "
                "entries than cost_ordered, which is impossible for an arm "
                "that verifies a superset of cost_ordered's candidates")

        # The aggregate lookup/publication counts above cannot rule out two
        # arms that coincidentally recorded the SAME NUMBER of cache events
        # against DIFFERENT daily units — a real divergence the counts alone
        # would hide. `daily_results_cache_event_records` names the unit_id
        # behind every event, so this checks the actual population:
        # ordered_exhaustive, which scans a superset of cost_ordered's
        # candidates, must have recorded at least one event for every unit
        # cost_ordered did, and must have PUBLISHED (or already held cached
        # for) every unit cost_ordered published.
        cost_ordered_event_records = cost_ordered.get(
            "daily_results_cache_event_records") or []
        ordered_exhaustive_event_records = ordered_exhaustive.get(
            "daily_results_cache_event_records") or []
        if not cost_ordered_event_records or not ordered_exhaustive_event_records:
            daily_results_cache_event_problems.append(
                "one or both arms published no "
                "daily_results_cache_event_records; the identity-bearing "
                "daily-result cache-event population cannot be checked")
        else:
            def event_population(
                raw: Sequence[Any], label: str,
            ) -> tuple[dict[str, int], dict[str, int]]:
                population: dict[str, int] = {}
                aggregate = {
                    "cache_hits": 0, "cache_misses": 0,
                    "cache_corrupt": 0, "cache_publications": 0,
                }
                counter_for_event = {
                    "hit": "cache_hits", "miss": "cache_misses",
                    "corrupt": "cache_corrupt",
                    "publication": "cache_publications",
                }
                for record in raw:
                    if not isinstance(record, Mapping) or set(record) != {
                            "unit_id", "event"}:
                        raise ValueError(
                            "daily-results cache event record must be an object")
                    if (not isinstance(record["unit_id"], str)
                            or not record["unit_id"]
                            or record["event"] not in counter_for_event):
                        raise ValueError(
                            "daily-results cache event identity is malformed")
                    identity = _canonical(dict(record))
                    population[identity] = population.get(identity, 0) + 1
                    aggregate[counter_for_event[record["event"]]] += 1
                expected = (left_cache_counts if label == "cost_ordered"
                            else right_cache_counts)
                if expected is None or aggregate != expected:
                    daily_results_cache_event_problems.append(
                        f"{label} daily-results cache counters disagree with "
                        "its identity-bearing event records")
                return population, aggregate

            cost_ordered_population, _ = event_population(
                cost_ordered_event_records, "cost_ordered")
            ordered_exhaustive_population, _ = event_population(
                ordered_exhaustive_event_records, "ordered_exhaustive")
            missing_event_identities = sorted(
                identity for identity, count in cost_ordered_population.items()
                if ordered_exhaustive_population.get(identity, 0) < count)
            if missing_event_identities:
                daily_results_cache_event_problems.append(
                    "ordered_exhaustive is missing identity-bearing cache "
                    "event records published by cost_ordered: "
                    f"{missing_event_identities[:20]}")
    daily_results_cache_events_valid = bool(
        not daily_results_cache_event_problems)

    # Full pilot+finalist semantic evidence (observations, disruption,
    # provenance) — see `_candidate_semantic_evidence` for why
    # `_candidate_costs` alone cannot cover this.
    left_semantic = _candidate_semantic_evidence(cost_ordered)
    right_semantic = _candidate_semantic_evidence(ordered_exhaustive)
    semantic_shared = sorted(set(left_semantic) & set(right_semantic))
    expected_cost_ordered_stages = {
        candidate_id: {"pilot"} for candidate_id in cost_ordered_verified
    }
    expected_ordered_exhaustive_stages = {
        candidate_id: {"pilot"} for candidate_id in ordered_exhaustive_verified
    }
    for candidate_id in _pilot_statistics(cost_ordered).get("selected_ids", ()):
        expected_cost_ordered_stages.setdefault(str(candidate_id), set()).add(
            "finalist")
    for candidate_id in _pilot_statistics(ordered_exhaustive).get(
            "selected_ids", ()):
        expected_ordered_exhaustive_stages.setdefault(
            str(candidate_id), set()).add("finalist")

    semantic_population_problems: list[str] = []
    for label, arm, expected, actual in (
            ("cost_ordered", cost_ordered,
             expected_cost_ordered_stages, left_semantic),
            ("ordered_exhaustive", ordered_exhaustive,
             expected_ordered_exhaustive_stages, right_semantic)):
        from traffic_sim.simulation.independent_daily import daily_unit_records

        raw_spec = (arm.get("result") or {}).get("closure_search_spec")
        schedule_units: dict[str, set[tuple[str, str]]] = {}
        if isinstance(raw_spec, Mapping):
            arm_spec = ClosureSearchSpec.from_dict(raw_spec)
            for schedule in iter_closure_schedules(arm_spec):
                if schedule.schedule_id not in expected:
                    continue
                schedule_units[schedule.schedule_id] = {
                    (build().schedule_id, str(identity["work_date"]))
                    for _unit_id, identity, build in daily_unit_records(
                        arm_spec, schedule)
                }
        if set(actual) != set(expected):
            semantic_population_problems.append(
                f"{label} semantic candidate population differs: "
                f"expected={sorted(expected)}, actual={sorted(actual)}")
        for candidate_id, stages in expected.items():
            actual_stages = set((actual.get(candidate_id) or {}).keys())
            if actual_stages != stages:
                semantic_population_problems.append(
                    f"{label} {candidate_id} stage population differs: "
                    f"expected={sorted(stages)}, actual={sorted(actual_stages)}")
            for stage in actual_stages:
                stage_evidence = actual[candidate_id][stage]
                observations = stage_evidence.get("observations") or ()
                digests = stage_evidence.get(
                    "canonical_observation_digests") or ()
                observation_pairs: set[tuple[Any, Any]] = set()
                observation_records_valid = True
                for item in observations:
                    if (not isinstance(item, Mapping)
                            or item.get("candidate_id") != candidate_id
                            or not isinstance(item.get("demand_variant"), str)
                            or isinstance(item.get("seed"), bool)
                            or not isinstance(item.get("seed"), int)
                            or item.get("seed") < 0):
                        observation_records_valid = False
                        continue
                    pair = (item["demand_variant"], item["seed"])
                    if pair in observation_pairs:
                        observation_records_valid = False
                    observation_pairs.add(pair)

                digest_pairs: set[tuple[Any, Any]] = set()
                digest_identities: set[tuple[Any, ...]] = set()
                digest_records_valid = True
                for item in digests:
                    if not isinstance(item, Mapping) or set(item) != {
                            "candidate_id", "work_date", "variant", "seed",
                            "sha256"}:
                        digest_records_valid = False
                        continue
                    try:
                        parsed_work_date = date.fromisoformat(item["work_date"])
                    except (TypeError, ValueError):
                        digest_records_valid = False
                        continue
                    identity = (
                        item["candidate_id"], item["work_date"],
                        item["variant"], item["seed"])
                    if (not isinstance(item["candidate_id"], str)
                            or not item["candidate_id"]
                            or parsed_work_date.isoformat() != item["work_date"]
                            or not isinstance(item["variant"], str)
                            or not item["variant"]
                            or isinstance(item["seed"], bool)
                            or not isinstance(item["seed"], int)
                            or item["seed"] < 0
                            or not isinstance(item["sha256"], str)
                            or len(item["sha256"]) != 64
                            or any(character not in "0123456789abcdef"
                                   for character in item["sha256"])
                            or identity in digest_identities
                            or identity[:2]
                            not in schedule_units.get(candidate_id, set())):
                        digest_records_valid = False
                    digest_identities.add(identity)
                    digest_pairs.add((item["variant"], item["seed"]))

                if not observation_records_valid or not digest_records_valid:
                    semantic_population_problems.append(
                        f"{label} {candidate_id} {stage} has malformed or "
                        "unassociated observation/digest identities")
                if observation_pairs != digest_pairs:
                    semantic_population_problems.append(
                        f"{label} {candidate_id} {stage} observation and "
                        "canonical-digest identities are not bijective: "
                        f"observations={sorted(observation_pairs)}, "
                        f"digests={sorted(digest_pairs)}")
    semantic_evidence_mismatches: list[dict[str, Any]] = []
    for candidate_id in semantic_shared:
        for stage in ("pilot", "finalist"):
            a_stage = left_semantic[candidate_id].get(stage)
            b_stage = right_semantic[candidate_id].get(stage)
            if a_stage != b_stage:
                semantic_evidence_mismatches.append({
                    "candidate_id": candidate_id, "stage": stage,
                    "cost_ordered": a_stage,
                    "ordered_exhaustive": b_stage})
    semantic_evidence_identical = bool(
        semantic_shared and not semantic_population_problems
        and not semantic_evidence_mismatches)

    # The cost-ordered attempt population must be an evidence-identical
    # PREFIX of the ordered-exhaustive one: same cost order, so anything
    # cost_ordered verified before stopping must appear, in the same
    # position, with IDENTICAL evidence, in ordered_exhaustive's complete
    # scan. No unexplained extras (candidates verified in one but not
    # reachable from the other's own order) and no omissions inside the
    # claimed prefix are permitted.
    prefix_problems: list[str] = []
    if not cost_ordered_verified:
        prefix_problems.append(
            "cost_ordered published no verified sequence to check")
    if (len(cost_ordered_verified) > len(ordered_exhaustive_verified)
            or ordered_exhaustive_verified[:len(cost_ordered_verified)]
            != cost_ordered_verified):
        prefix_problems.append(
            "cost_ordered's verified sequence is not a prefix of "
            "ordered_exhaustive's")
    prefix_evidence_mismatches: list[dict[str, Any]] = []
    for candidate_id in cost_ordered_verified:
        a, b = left.get(candidate_id), right.get(candidate_id)
        if a is None or b is None:
            prefix_evidence_mismatches.append({
                "candidate_id": candidate_id,
                "reason": "missing from one arm's published evidence"})
            continue
        for field in cost_fields:
            if a.get(field) != b.get(field):
                prefix_evidence_mismatches.append({
                    "candidate_id": candidate_id, "field": field,
                    "cost_ordered": a.get(field),
                    "ordered_exhaustive": b.get(field)})
        for field in ("hard_failures", "timeout_undecided", "eligible"):
            if a.get(field) != b.get(field):
                prefix_evidence_mismatches.append({
                    "candidate_id": candidate_id, "field": field,
                    "cost_ordered": a.get(field),
                    "ordered_exhaustive": b.get(field)})
    cost_ordered_attempt_population_is_valid_prefix = bool(
        not prefix_problems and not prefix_evidence_mismatches)

    # Identity-bearing exact-attempt population check — see
    # `_exact_attempt_population_check` for why the candidate-level prefix
    # check above cannot substitute for this.
    exact_attempt_population_check = _exact_attempt_population_check(
        cost_ordered, ordered_exhaustive)

    # An early stop is USEFUL precisely when ordered_exhaustive verifies
    # strictly more candidates than cost_ordered — so requiring the two
    # candidate populations to be IDENTICAL made `semantic_comparison_
    # complete` false for every discriminating run and true only for a
    # degenerate one where nothing was saved. The real requirement is
    # narrower: cost_ordered must never have examined a candidate absent
    # from ordered_exhaustive's complete scan (that would be a genuine
    # anomaly, since ordered_exhaustive scans the identical order and
    # more), and every candidate ordered_exhaustive examined that
    # cost_ordered did not must be explained by the stop itself — i.e. it
    # must lie strictly after cost_ordered's own verified prefix in
    # ordered_exhaustive's cost order.
    unexamined_by_cost_ordered = sorted(
        set(ordered_exhaustive_verified) - set(cost_ordered_verified))
    unjustified_ordered_exhaustive_only_candidates = sorted(
        set(ordered_exhaustive_only_candidates)
        - set(unexamined_by_cost_ordered))
    candidate_population_justified = bool(
        not cost_ordered_only_candidates
        and not unjustified_ordered_exhaustive_only_candidates)

    cost_execution_contract = _execution_contract(cost_ordered)
    exhaustive_execution_contract = _execution_contract(ordered_exhaustive)
    execution_contract_valid = bool(
        cost_execution_contract["valid"]
        and exhaustive_execution_contract["valid"])
    terminal_status_identical = bool(
        execution_contract_valid
        and cost_execution_contract["terminal_status"]
        == exhaustive_execution_contract["terminal_status"])
    selected_ids_identical = bool(
        execution_contract_valid
        and cost_execution_contract["selected_ids"]
        == exhaustive_execution_contract["selected_ids"])
    execution_status_identical = bool(
        execution_contract_valid
        and cost_execution_contract["status"]
        == exhaustive_execution_contract["status"])
    stop_proof_valid = bool(
        cost_ordered_stop_proof_check["valid"]
        and ordered_exhaustive_stop_proof_check["valid"])

    # A paired result must carry per-arm resource observations.  The subhour
    # wrapper applies the registered numeric caps; this comparator makes the
    # measurements themselves mandatory and exposes disk by arm as well as the
    # existing RSS values, so a missing arm cannot masquerade as zero.
    disk_growth_bytes_by_arm: dict[str, int | None] = {}
    for label, arm in (("cost_ordered", cost_ordered),
                       ("ordered_exhaustive", ordered_exhaustive)):
        workspace = Path(str(arm.get("workspace", "")))
        disk_growth_bytes_by_arm[label] = (
            _workspace_tree_size(workspace) if workspace.is_dir() else None)
    peak_values = {
        label: arm.get("peak_rss_bytes")
        for label, arm in (("cost_ordered", cost_ordered),
                           ("ordered_exhaustive", ordered_exhaustive))}
    resource_measurements_complete = bool(
        all(isinstance(value, (int, float)) and not isinstance(value, bool)
            and float(value) >= 0 for value in peak_values.values())
        and all(isinstance(value, int) and value >= 0
                for value in disk_growth_bytes_by_arm.values()))

    semantic_comparison_complete = bool(
        not cost_mismatches
        and not failure_mismatches
        and not health_mismatches
        and not timeout_mismatches
        and candidate_population_justified
        and ledger_content_keys_identical
        and cache_hits_consistent
        and daily_results_cache_events_valid
        and semantic_evidence_identical
        and cost_ordered_stop_proof_check["valid"]
        and ordered_exhaustive_genuinely_exhaustive
        and cost_ordered_attempt_population_is_valid_prefix
        and exact_attempt_population_check["valid"]
        and active_elapsed_basis_consistent
        and final_decision_identical
        and execution_contract_valid
        and terminal_status_identical
        and selected_ids_identical
        and execution_status_identical
        and stop_proof_valid
        and resource_measurements_complete)

    return {
        "schema": "cost_ordered_vs_ordered_exhaustive_v1",
        "release_evidence": False,
        "evidence_class": "diagnostic",
        "cost_ordered_sumo_candidates": cost_ordered_pilot_candidates,
        "ordered_exhaustive_sumo_candidates": (
            ordered_exhaustive_pilot_candidates),
        "sumo_verifications_saved": (
            None if cost_ordered_pilot_candidates is None
            or ordered_exhaustive_pilot_candidates is None else
            int(ordered_exhaustive_pilot_candidates)
            - int(cost_ordered_pilot_candidates)),
        "exact_attempts": {"cost_ordered": cost_ordered_attempts,
                           "ordered_exhaustive": ordered_exhaustive_attempts},
        "exact_attempts_reduction_fraction": attempts_reduction,
        "exact_attempts_reduction_meets_30_percent": bool(
            attempts_reduction is not None and attempts_reduction >= 0.30),
        # Kept under the old name too: nothing published before this pass
        # ever set it from real telemetry, so no reader can regress by this
        # rename, but a reader keyed on the old name still finds a value.
        "attempts_reduction_fraction": attempts_reduction,
        "attempts_reduction_meets_30_percent": bool(
            attempts_reduction is not None and attempts_reduction >= 0.30),
        "wall_time_s": {"cost_ordered": wall_cost_ordered,
                        "ordered_exhaustive": wall_ordered_exhaustive},
        "wall_time_reduction_fraction": wall_time_reduction,
        "wall_time_reduction_meets_30_percent": bool(
            wall_time_reduction is not None and wall_time_reduction >= 0.30),
        "active_elapsed_s": {"cost_ordered": active_cost_ordered,
                             "ordered_exhaustive": active_ordered_exhaustive},
        "active_elapsed_basis": active_elapsed_basis,
        "active_elapsed_basis_consistent": active_elapsed_basis_consistent,
        "awake_active_time_reduction_fraction": awake_active_time_reduction,
        "awake_active_time_reduction_meets_30_percent": bool(
            awake_active_time_reduction is not None
            and awake_active_time_reduction >= 0.30),
        "peak_rss_bytes": {
            "cost_ordered": cost_ordered["peak_rss_bytes"],
            "ordered_exhaustive": ordered_exhaustive["peak_rss_bytes"]},
        "disk_growth_bytes_by_arm": disk_growth_bytes_by_arm,
        "resource_measurements_complete": resource_measurements_complete,
        "no_resource_cap_regression": resource_measurements_complete,
        "candidate_costs_field_identical": not cost_mismatches,
        "candidate_cost_mismatches": cost_mismatches[:50],
        "hard_failures_identical": not failure_mismatches,
        "hard_failure_mismatches": failure_mismatches[:50],
        "health_classifications_identical": not health_mismatches,
        "health_mismatches": health_mismatches[:50],
        "timeout_outcomes_identical": not timeout_mismatches,
        "timeout_outcome_mismatches": timeout_mismatches[:50],
        "cost_ordered_only_candidates": cost_ordered_only_candidates[:50],
        "ordered_exhaustive_only_candidates": (
            ordered_exhaustive_only_candidates[:50]),
        "unexamined_by_cost_ordered": unexamined_by_cost_ordered[:50],
        "unjustified_ordered_exhaustive_only_candidates": (
            unjustified_ordered_exhaustive_only_candidates[:50]),
        "candidate_population_justified": candidate_population_justified,
        "ledger_content_keys_identical": ledger_content_keys_identical,
        "cache_hits_consistent": cache_hits_consistent,
        "daily_results_cache_events": {
            "cost_ordered": cost_ordered_cache_events,
            "ordered_exhaustive": ordered_exhaustive_cache_events},
        "daily_results_cache_events_valid": daily_results_cache_events_valid,
        "daily_results_cache_event_problems": (
            daily_results_cache_event_problems),
        "semantic_evidence_identical": semantic_evidence_identical,
        "semantic_evidence_mismatches": semantic_evidence_mismatches[:50],
        "semantic_population_problems": semantic_population_problems[:50],
        "cost_ordered_stop_proof_check": cost_ordered_stop_proof_check,
        "ordered_exhaustive_stop_proof_check": (
            ordered_exhaustive_stop_proof_check),
        "stop_proof_valid": stop_proof_valid,
        "both_stop_proofs_valid": stop_proof_valid,
        "ordered_exhaustive_genuinely_exhaustive": (
            ordered_exhaustive_genuinely_exhaustive),
        "cost_ordered_attempt_population_is_valid_prefix": (
            cost_ordered_attempt_population_is_valid_prefix),
        "prefix_problems": prefix_problems,
        "prefix_evidence_mismatches": prefix_evidence_mismatches[:50],
        "exact_attempt_population_check": exact_attempt_population_check,
        "semantic_comparison_complete": semantic_comparison_complete,
        "execution_contract_valid": execution_contract_valid,
        "execution_status_identical": execution_status_identical,
        "terminal_status_identical": terminal_status_identical,
        "terminal_status": {
            "cost_ordered": cost_execution_contract.get("terminal_status"),
            "ordered_exhaustive": exhaustive_execution_contract.get(
                "terminal_status")},
        "selected_ids_identical": selected_ids_identical,
        "selected_ids": {
            "cost_ordered": cost_execution_contract.get("selected_ids", []),
            "ordered_exhaustive": exhaustive_execution_contract.get(
                "selected_ids", [])},
        "final_decision_identical": final_decision_identical,
        "final_decision": {
            "cost_ordered": _final_decision(cost_ordered["result"]),
            "ordered_exhaustive": _final_decision(
                ordered_exhaustive["result"])},
    }


def run_ordered_exhaustive_comparison(
    spec: ClosureSearchSpec,
    policy,
    *,
    runs_root: Path,
    release_root: Path,
    workspace_root: Path,
    daily_cost_cache: Path,
    data_root: Path = ROOT,
    counterbalance: bool = False,
    isolate_arms: bool = True,
    arm_timeout_s: float = 7200.0,
    max_verifications: int | None = None,
    max_exact_launches: int | None = None,
    fixture_controls: Mapping[str, Any] | None = None,
    qualified_demand_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the cost-ordered exact arm against the ordered-exhaustive reference.

    DIAGNOSTIC, not a registered/preregistered benchmark: it produces
    `release_evidence: False` evidence, is not bound to any registration
    schema, and activates nothing. This is deliberate — the full outcome-
    blind registration this measurement should eventually feed (bound
    inputs, immutable case selection, a frozen GO/NO-GO gate) is a separate,
    larger piece of work. What this function DOES provide is the actual
    isolated, apples-to-apples measurement the structural-speedup claim
    needs: both arms share one cost ledger and candidate order, run in their
    own process and process group at the frozen one-worker benchmark
    resource shape, and never share a daily-results cache.

    `isolate_arms=False` exists only so tests can drive this with an
    in-process, SUMO-free `build_arm` fake, exactly as `run_benchmark`'s own
    `isolate_arms` does; a real measurement must never pass it.
    """
    from traffic_sim.simulation.workspace import WorkspaceLock

    data_root = Path(data_root).resolve()
    lock = WorkspaceLock(
        f"cost_ordered_benchmark {os.getpid()}",
        path=data_root / "runs" / ".demand-workspace.lock",
    )
    if not lock.acquire(timeout=3600.0, poll_s=10.0):
        raise SystemExit(f"demand workspace busy: {lock.holder_description()}")
    previous_cwd = Path.cwd()
    try:
        os.chdir(data_root)
        arm_order = (("cost_ordered", False), ("ordered_exhaustive", True))
        if counterbalance:
            arm_order = tuple(reversed(arm_order))
        arms: dict[str, Any] = {}
        cache_snapshots: dict[str, dict[str, str]] = {}
        bound_source = _bind_daily_results_source_snapshot(daily_cost_cache)
        for arm, disable_early_stop in arm_order:
            arm_workspace_root = Path(workspace_root) / arm.replace("_", "-")
            _assert_fresh_arm_workspace(arm_workspace_root, spec)
            arm_cache_root, arm_cache_digest = (
                _isolated_daily_results_cache_root(
                    daily_cost_cache, arm, bound_source=bound_source))
            cache_snapshots[arm] = {
                "root": str(arm_cache_root), "digest": arm_cache_digest}
            if isolate_arms:
                arms[arm] = pa.run_arm_isolated(
                    spec, policy,
                    cost_ordered=True,
                    workspace_root=arm_workspace_root,
                    runs_root=runs_root,
                    release_root=release_root,
                    daily_cost_cache=daily_cost_cache,
                    daily_results_cache_root=arm_cache_root,
                    study_provenance_key=BENCHMARK_STUDY_PROVENANCE_KEY,
                    data_root=data_root,
                    seed_workers=pa.BENCHMARK_SEED_WORKERS,
                    daily_workers=pa.BENCHMARK_DAILY_WORKERS,
                    max_active_sumo_slots=pa.BENCHMARK_MAX_ACTIVE_SUMO_SLOTS,
                    max_exact_launches=max_exact_launches,
                    timeout_s=arm_timeout_s,
                    max_verifications=max_verifications,
                    disable_early_stop=disable_early_stop,
                    fixture_controls=fixture_controls,
                    qualified_demand_manifest=qualified_demand_manifest,
                )
            else:
                arms[arm] = pa.run_arm(
                    spec, policy,
                    cost_ordered=True,
                    workspace_root=arm_workspace_root,
                    runs_root=runs_root,
                    release_root=release_root,
                    daily_cost_cache=daily_cost_cache,
                    daily_results_cache_root=arm_cache_root,
                    study_provenance_key=BENCHMARK_STUDY_PROVENANCE_KEY,
                    seed_workers=pa.BENCHMARK_SEED_WORKERS,
                    daily_workers=pa.BENCHMARK_DAILY_WORKERS,
                    max_active_sumo_slots=pa.BENCHMARK_MAX_ACTIVE_SUMO_SLOTS,
                    max_exact_launches=max_exact_launches,
                    max_verifications=max_verifications,
                    disable_early_stop=disable_early_stop,
                    fixture_controls=fixture_controls,
                    qualified_demand_manifest=qualified_demand_manifest,
                )
        if cache_snapshots["cost_ordered"]["root"] == (
                cache_snapshots["ordered_exhaustive"]["root"]):
            raise RuntimeError(
                "both arms were assigned the same daily-results cache root; "
                "refusing to publish a comparison that could not tell a "
                "structural saving from a shared cache hit")
        _assert_fresh_snapshot_pair_matches(
            daily_cost_cache, ("cost_ordered", "ordered_exhaustive"),
            cache_snapshots)
        _assert_daily_results_source_unchanged(bound_source)
        cache_snapshots["_source"] = dict(bound_source)
    finally:
        os.chdir(previous_cwd)
        lock.release()
    comparison = compare_ordered_exhaustive(
        arms["cost_ordered"], arms["ordered_exhaustive"])
    comparison["daily_results_cache_snapshots"] = cache_snapshots
    return {"arms": arms, "comparison": comparison}


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
    # No --no-isolate-arms CLI flag, deliberately: the CLI is what emits a
    # preregistered benchmark outcome, so it must always isolate arms. See
    # `_execute_and_publish`'s docstring.
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

    return _run_registered(
        registration_path=args.registration,
        runs_root=args.runs_root,
        release_root=args.release_root,
        workspace_root=args.workspace_root,
        data_root=args.data_root,
        out=args.out,
        overwrite=args.overwrite,
        stdout=args.stdout,
        fault_injection=not args.no_fault_injection,
        allow_drift=args.allow_drift,
    )


def _run_registered(
    *,
    registration_path: Path,
    runs_root: Path,
    release_root: Path,
    workspace_root: Path,
    data_root: Path,
    out: Path,
    overwrite: bool,
    stdout: bool,
    fault_injection: bool,
    allow_drift: bool,
) -> int:
    """Load, verify and run a registration. `main()`'s entire `--run` body.

    This function and `_execute_and_publish` below are the ONLY two places
    that may write a preregistered benchmark outcome, and neither accepts an
    `isolate_arms` argument — both always isolate. A non-isolated run that
    could still reach `_write`/`build_outcome` would let cost-order v5's
    cross-arm timeout leak back in through a code path nobody meant to call
    from production. Tests that need to drive this same load/verify/execute/
    publish body with an in-process, SUMO-free `build_arm` fake own a
    parallel, non-production helper for that (see
    `tests/test_cost_ordered_benchmark_run.py`'s
    `_run_registered_unisolated_for_tests`) rather than a parameter here.
    """
    registration = json.loads(
        Path(registration_path).read_text(encoding="utf-8"))
    schema = str(registration.get("schema"))
    if schema not in SUPPORTED_REGISTRATION_SCHEMAS:
        raise SystemExit(
            f"unsupported registration schema {schema!r}; this tool reads "
            f"{', '.join(SUPPORTED_REGISTRATION_SCHEMAS)}")
    if registration.get("selected_case") is None:
        raise SystemExit(
            "the registration selected no case; there is nothing to run. "
            f"Its blocker: {registration.get('blocked_by', {}).get('reason')}")

    drift = verify_bindings(registration, runs_root)
    if drift and not allow_drift:
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
    if declared_outcome and _relative(Path(out).resolve()) != (
            declared_outcome):
        raise SystemExit(
            f"the registration names {declared_outcome} as its outcome but "
            f"--out is {_relative(Path(out).resolve())}. A run must write "
            f"the record its registration froze, or the two stop describing "
            f"the same experiment.")

    return _execute_and_publish(
        registration,
        runs_root=runs_root,
        release_root=release_root,
        workspace_root=workspace_root,
        data_root=data_root,
        out=out,
        overwrite=overwrite,
        stdout=stdout,
        fault_injection=fault_injection,
        drift=drift,
        registration_path=registration_path,
    )


def _execute_and_publish(
    registration: Mapping[str, Any],
    *,
    runs_root: Path,
    release_root: Path,
    workspace_root: Path,
    data_root: Path,
    out: Path,
    overwrite: bool,
    stdout: bool,
    fault_injection: bool,
    drift: Sequence[str],
    registration_path: Path,
) -> int:
    """Run both arms and publish the outcome. The CLI's only entry point.

    Always isolates: this function is what produces a preregistered
    benchmark outcome, and a non-isolated run shares one process (and,
    historically, one daily-results cache) between arms — exactly the shape
    that let cost-order v5's timeout classification cross between arms.
    There is deliberately no parameter here to relax that; a caller that
    needs an in-process, SUMO-free `build_arm` fake for testing has to use
    `run_benchmark(..., isolate_arms=False)` directly and build its own,
    clearly-non-production publication path (see
    `_run_registered_unisolated_for_tests` in
    `tests/test_cost_ordered_benchmark_run.py`) rather than reach it through
    this one.
    """
    try:
        executed = run_benchmark(
            registration,
            runs_root=runs_root,
            release_root=release_root,
            workspace_root=workspace_root,
            data_root=data_root,
            fault_injection=fault_injection,
            isolate_arms=True,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        comparison = {
            "execution_error": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "binding_drift_accepted": list(drift),
            "workspace_root": str(Path(workspace_root).resolve()),
        }
        outcome = build_outcome(
            registration,
            comparison,
            status="failed_execution",
            registration_path=registration_path,
        )
        if stdout:
            print(json.dumps(outcome, indent=1, sort_keys=True))
        else:
            _write(out, outcome, overwrite=overwrite)
            print(
                f"wrote {out} (status=failed_execution, "
                f"error={type(error).__name__}: {error})",
                file=sys.stderr,
            )
        return 5
    comparison = dict(executed["comparison"])
    if drift:
        comparison["binding_drift_accepted"] = list(drift)
    outcome = build_outcome(
        registration,
        comparison,
        status="measured",
        registration_path=registration_path,
    )
    if stdout:
        print(json.dumps(outcome, indent=1, sort_keys=True))
        return 0 if outcome["gates"]["passed"] else 4
    _write(out, outcome, overwrite=overwrite)
    print(f"wrote {out} (status={outcome['status']}, "
          f"gates_passed={outcome['gates']['passed']}, "
          f"saved={comparison['sumo_verifications_saved']} of "
          f"{comparison['exhaustive_sumo_candidates']})")
    return 0 if outcome["gates"]["passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
