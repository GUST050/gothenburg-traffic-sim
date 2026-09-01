"""Run ONE product closure-search arm, exactly as the CLI would.

A benchmark that compares exhaustive against cost-ordered execution is only
worth anything if both arms are the product. The temptation when writing such a
tool is to assemble a runner that looks like the CLI's — and then the two drift,
and the benchmark measures a lookalike.

So this module is the single place that builds an arm, and it builds it out of
`run_monthly_closure_search`'s own helpers: the same screening builders, the
same demand resolver, the same independent-daily runner, the same
`_cost_source_for`. The benchmark and the independent-vs-continuous harness both
call it. If the CLI changes how an arm is constructed, there is one place that
has to follow, not three.

WHAT IT DELIBERATELY DOES NOT DO. It never builds demand: `build_missing=False`
everywhere, because a measurement that calibrates its own inputs is measuring
itself. It takes no workspace lock either — the caller holds one for the whole
campaign, since acquiring and releasing per arm would let another writer in
between them.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.process_census import (  # noqa: E402
    ProcessCensusUnavailable, process_group_snapshot)
from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402
from traffic_sim.simulation.closure_ranking import ClosureCost  # noqa: E402
from traffic_sim.simulation.finalist_decision import CandidateEvidence  # noqa: E402
from traffic_sim.simulation.cost_ordered_execution import ParentCost  # noqa: E402

#: Frozen resource shape for a comparison arm. Both the benchmark's main
#: gate and any isolated subprocess arm must run at exactly this width:
#: one daily unit worker, one seed worker, one live SUMO process at a time.
#: Extra parallelism is a different, legitimate optimisation, but it is not
#: the one this benchmark is allowed to credit as structural speedup, so the
#: benchmark itself never asks for more than this.
BENCHMARK_DAILY_WORKERS = 1
BENCHMARK_SEED_WORKERS = 1
BENCHMARK_MAX_ACTIVE_SUMO_SLOTS = 1

#: The product CLI's own defaults, named here so an arm cannot silently differ
#: from a real run in a parameter nobody thought to pass.
BASELINE_TRIP_DURATION_P99_S = 3600
MAXIMUM_CANDIDATES = 100_000
MAXIMUM_DAILY_UNITS = 10_000


class _FixtureCostSource:
    """Apply only the immutable, symmetric boundary fixture to both arms."""

    def __init__(self, inner: Any, controls: Mapping[str, Any], state: dict[str, Any]):
        self.inner = inner
        self.controls = controls
        self.state = state

    def identity(self):
        return self.inner.identity()

    def parent_cost(self, parent):
        result = self.inner.parent_cost(parent)
        no_detour = self.controls.get("no_detour") or {}
        if parent.schedule_id == str(no_detour.get("candidate_id")):
            forced = no_detour.get("forced_fields") or {}
            count = max(1, int(forced.get("vehicles_no_detour", 1)))
            variants = tuple({**dict(item), "vehicles_no_detour": count}
                             for item in result.per_variant)
            cost = replace(result.cost, vehicles_no_detour=count)
            self.state.setdefault("no_detour_records", {})[
                parent.schedule_id] = variants
            self.state.setdefault("applied", set()).add("no_detour")
            self.state.setdefault("runner_events", []).append({
                "fixture": "no_detour", "candidate_id": parent.schedule_id,
                "stage": "cost_ledger", "pre_sumo": True,
                "vehicles_no_detour": count,
            })
            return replace(result, cost=cost, per_variant=variants)
        dense = self.controls.get("dense_boundary") or {}
        targets = dense.get("candidate_ids") or ()
        if parent.schedule_id not in targets:
            return result
        # The values are part of the preregistered fixture, not measured data.
        # Keep the full per-variant payload coherent so reconciliation still
        # proves that the ordering and the runner judged the same price.
        fields = dense.get("forced_fields") or {
            "added_vehicle_hours": 0.0,
            "added_metres_total": 0.0,
            "vehicles_affected": 0,
            "vehicles_no_detour": 0,
        }
        variants = tuple({**dict(item), **fields} for item in result.per_variant)
        cost = ClosureCost(
            candidate_id=result.candidate_id,
            added_vehicle_hours=float(fields["added_vehicle_hours"]),
            added_metres_total=float(fields["added_metres_total"]),
            vehicles_affected=int(fields["vehicles_affected"]),
            vehicles_no_detour=int(fields["vehicles_no_detour"]),
        )
        self.state.setdefault("dense_records", {})[parent.schedule_id] = variants
        self.state.setdefault("applied", set()).add("dense_boundary")
        return replace(result, cost=cost, per_variant=variants)


class _FixtureRunner:
    """Inject registered pre-SUMO failures and observe cancellation telemetry."""

    def __init__(self, inner: Any, controls: Mapping[str, Any], state: dict[str, Any]):
        self.inner = inner
        self.controls = controls
        self.state = state
        self._seen = 0
        self._synthetic_pending = int(
            (controls.get("restart_cancel") or {}).get("synthetic_queued", 1))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run_candidate(self, schedule, *, target_repetitions, existing, stage):
        if existing is None and stage == "pilot":
            self._seen += 1
            no_detour = self.controls.get("no_detour") or {}
            if str(no_detour.get("candidate_id")) == schedule.schedule_id:
                self.state.setdefault("runner_events", []).append({
                    "fixture": "no_detour", "candidate_id": schedule.schedule_id,
                    "stage": stage, "pre_sumo": False,
                    "error": "deterministic no-detour candidate reached runner",
                })
                raise AssertionError(
                    "deterministic no-detour candidate must be rejected by the "
                    "cost ledger before SUMO")
            for name, failure in (("backfill", "fixture_backfill"),):
                control = self.controls.get(name) or {}
                if str(control.get("candidate_id")) == schedule.schedule_id:
                    self.state.setdefault("applied", set()).add(name)
                    self.state.setdefault("runner_events", []).append({
                        "fixture": name, "candidate_id": schedule.schedule_id,
                        "stage": stage, "pre_sumo": True,
                    })
                    return CandidateEvidence(
                        candidate_id=schedule.schedule_id,
                        observations=(), hard_failures=(failure,),
                        disruption=(), timeout_undecided=())
        result = self.inner.run_candidate(
            schedule, target_repetitions=target_repetitions,
            existing=existing, stage=stage)
        dense = self.state.get("dense_records", {}).get(schedule.schedule_id)
        if dense is not None:
            result = replace(result, disruption=dense)
        return result

    def stop_speculative_work(self) -> None:
        before = self._synthetic_pending
        self._synthetic_pending = 0
        self.state["cancel_observed"] = {
            "called": True, "queued_before": before, "queued_after": 0,
            "no_later_starter": True,
        }
        callback = getattr(self.inner, "stop_speculative_work", None)
        if callable(callback):
            callback()

    def timing_snapshot(self) -> dict[str, Any]:
        snapshot = {}
        callback = getattr(self.inner, "timing_snapshot", None)
        if callable(callback):
            raw = callback()
            if isinstance(raw, Mapping):
                snapshot.update(raw)
        snapshot["fixture_telemetry"] = {
            "applied": sorted(self.state.get("applied", set())),
            "runner_events": list(self.state.get("runner_events", [])),
            "cancel_observed": self.state.get("cancel_observed"),
        }
        return snapshot


def _runner_exact_launch_telemetry(runner: Any) -> dict[str, Any]:
    """Result-neutral S0 telemetry: real SUMO launches, pulled fresh.

    Diagnostic only — mirrors the fail-open contract of
    `monthly_search._runner_timing_snapshot`, which this deliberately does
    not import (that function is private to that module and folds this same
    data into a differently-shaped progress record).  A backend lacking the
    hook (a fake/legacy runner in a test) simply reports an empty snapshot.
    """
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        return {}
    try:
        raw = snapshot()
    except Exception:  # diagnostic hook: fail open, never break a run
        return {}
    if not isinstance(raw, Mapping):
        return {}
    telemetry = raw.get("exact_launch_telemetry")
    return dict(telemetry) if isinstance(telemetry, Mapping) else {}


def _runner_exact_launch_records(runner: Any) -> list[dict[str, Any]]:
    """Identity-bearing companion to `_runner_exact_launch_telemetry`.

    One record per real SUMO (candidate, work date, variant, seed, attempt)
    launch, so a downstream comparison can validate the exact-attempt
    POPULATION — not merely a total — between two arms. Fail-open like its
    aggregate counterpart: a backend lacking the hook reports an empty list.
    """
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        return []
    try:
        raw = snapshot()
    except Exception:  # diagnostic hook: fail open, never break a run
        return []
    if not isinstance(raw, Mapping):
        return []
    records = raw.get("exact_launch_records")
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, Mapping)]


def _runner_daily_results_cache_event_records(runner: Any) -> list[dict[str, Any]]:
    """Identity-bearing companion to `_runner_daily_results_cache_events`.

    One record per real daily-result cache lookup/publication, naming the
    daily unit and the event kind (`hit`/`miss`/`corrupt`/`publication`), so a
    downstream comparison can validate the cache-event POPULATION — which
    unit was hit, missed or published, not merely how many — between two
    arms. Fail-open like its aggregate counterpart: a backend lacking the
    hook reports an empty list.
    """
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        return []
    try:
        raw = snapshot()
    except Exception:  # diagnostic hook: fail open, never break a run
        return []
    if not isinstance(raw, Mapping):
        return []
    records = raw.get("cache_event_records")
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, Mapping)]


def _runner_daily_results_cache_events(runner: Any) -> dict[str, Any]:
    """Daily-RESULT cache hits/misses/corruption/publications, pulled fresh.

    Distinct from `daily_cost_cache_hits` above, which counts the
    deterministic-cost ledger's cache — this is `IndependentDailyRunner`'s
    own `_timing` counters for the real per-daily-unit SUMO evidence cache
    (`cache_hits`/`cache_misses`/`cache_corrupt`/`cache_publications`),
    published so a benchmark comparison can check the daily-result cache-
    event population between two arms, not merely the ledger's. Fail-open
    like its `exact_launch_telemetry` sibling: a backend lacking the hook
    reports an empty snapshot rather than breaking the run.
    """
    snapshot = getattr(runner, "timing_snapshot", None)
    if not callable(snapshot):
        return {}
    try:
        raw = snapshot()
    except Exception:  # diagnostic hook: fail open, never break a run
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        key: raw[key] for key in (
            "cache_hits", "cache_misses", "cache_corrupt",
            "cache_publications")
        if key in raw
    }


def _workspace_manifest(workspace_directory: Path) -> dict[str, Any]:
    manifest_path = Path(workspace_directory) / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _workspace_active_elapsed_s(workspace_directory: Path) -> float | None:
    value = _workspace_manifest(workspace_directory).get("active_elapsed_s")
    return float(value) if isinstance(value, (int, float)) else None


def _workspace_active_elapsed_basis(workspace_directory: Path) -> str | None:
    value = _workspace_manifest(workspace_directory).get(
        "active_elapsed_basis")
    return str(value) if isinstance(value, str) and value else None


def peak_rss_bytes() -> int:
    """Peak RSS of this process AND its children.

    A SUMO campaign spends most of its memory in child processes, and
    `ru_maxrss` for children survives fork+exec on Linux, so the larger of the
    two is what the arm actually cost.

    KNOWN LIMITATION: this is `max(self's own peak, any one already-reaped
    child's peak)`, never their SUM — `getrusage` has no notion of "what was
    alive at the same instant". A parent interpreter and a live SUMO child
    holding memory concurrently can genuinely need more than either number
    alone reports. `_process_tree_rss_bytes`/`ProcessTreeRSSSampler` below
    measure the real simultaneous total for the isolated-arm case, where it
    matters for the memory gate; this function stays as the cheap in-process
    fallback used when there is nothing to sample from the outside.
    """
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024
    return int(max(own, children)) * scale


def _process_group_snapshot() -> list[tuple[int, int, int]]:
    """`(pid, pgid, rss_kib)` for every visible process.

    Delegates to `tools.process_census`, which owns the mechanism ladder and
    the trust contract: it raises `ProcessCensusUnavailable` rather than
    returning an empty list when no mechanism can be believed, because an
    empty return is indistinguishable from "genuinely no processes" and would
    let a failed census masquerade as a clean reap or a zero RSS reading.

    This used to shell out to `ps` directly. It stopped working wherever the
    workflow actually runs: `/bin/ps` is setuid root on macOS, and a Seatbelt
    sandbox refuses to exec a setuid binary, so the census EPERM'd and the
    resource gates could only publish INCONCLUSIVE. The replacement reads the
    same kernel data through libproc — see that module's header for the
    measurement it makes and why it is the stronger one.
    """
    return process_group_snapshot()


def _process_group_pids(pgid: int) -> list[int]:
    """PIDs currently alive in process group `pgid`."""
    return [pid for pid, group, _ in _process_group_snapshot()
            if group == pgid]


def _process_tree_rss_bytes(pgid: int) -> int:
    """Sum of RSS, in bytes, of every process alive in group `pgid` RIGHT NOW.

    This is the simultaneous total `peak_rss_bytes()` cannot report: one
    census snapshot covers every member of the group at once, so a parent
    interpreter and a live SUMO child are counted together rather than
    reduced to whichever one's own peak was larger.
    """
    total_kib = sum(rss for _, group, rss in _process_group_snapshot()
                    if group == pgid)
    return total_kib * 1024


class ProcessTreeRSSSampler:
    """Background sampler for a process group's simultaneous peak RSS.

    Started right after the group's leader process exists and stopped once
    it has been reaped; `peak_bytes` is then the highest total RSS observed
    across every process alive in the group at any single sample, which is
    the number the 8 GiB memory gate actually cares about.
    """

    def __init__(self, pgid: int, *, interval_s: float = 0.5) -> None:
        self._pgid = pgid
        self._interval_s = interval_s
        self._peak = 0
        self._census_error: ProcessCensusUnavailable | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        try:
            value = _process_tree_rss_bytes(self._pgid)
        except ProcessCensusUnavailable as error:
            # Recorded, not raised here: this runs on the background thread,
            # where an exception would just be swallowed silently. `stop()`
            # is where the failure becomes visible to the caller.
            self._census_error = error
            return
        self._peak = max(self._peak, value)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval_s)

    def start(self) -> "ProcessTreeRSSSampler":
        self._sample()
        self._thread.start()
        return self

    def stop(self) -> int:
        """Stop sampling and return the peak — a verified, trustworthy one.

        If any sample (including this final one, taken after the group has
        had a chance to be reaped) lost the census, the peak this
        instance collected cannot be trusted as a true maximum: a failed
        sample could have missed the real peak entirely. Raises
        `ProcessCensusUnavailable` in that case instead of returning a
        possibly-under-reported number.
        """
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._sample()
        if self._census_error is not None:
            raise ProcessCensusUnavailable(
                "process-tree RSS sampling lost the process census at least "
                "once; the peak this sampler collected cannot be trusted: "
                f"{self._census_error}"
            ) from self._census_error
        return self._peak

    @property
    def peak_bytes(self) -> int:
        return self._peak


def write_spec(spec: ClosureSearchSpec, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec.to_dict(), indent=1, sort_keys=True),
                    encoding="utf-8")
    return path


def build_arm(
    spec: ClosureSearchSpec,
    *,
    cost_ordered: bool,
    runs_root: Path,
    release_root: Path,
    daily_cost_cache: Path,
    study_provenance_key: str,
    objective_method: str,
    seed_workers: int = 1,
    daily_workers: int = 1,
    max_active_sumo_slots: int = 1,
    daily_results_cache_root: Path | None = None,
    disable_early_stop: bool = False,
    window_cost_index: Any = None,
):
    """Return `(runner, screen_builder, cost_source)` for one arm.

    Three arm shapes come out of this one builder, controlled by
    `cost_ordered`/`disable_early_stop` together:

      * `cost_ordered=False` — the product's current exhaustive screening
        path (`_bounded_exhaustive_builder`/`_independent_exhaustive_builder`),
        unrelated code from the cost-ordered execution entirely.
      * `cost_ordered=True, disable_early_stop=False` — the real cost-ordered
        exact execution: price everything, simulate only the boundary set.
      * `cost_ordered=True, disable_early_stop=True` — the ORDERED-EXHAUSTIVE
        reference arm: the exact same cost source, ledger and candidate order
        as the row above, run through the exact same execution code path, but
        with the band-based stop disabled so every candidate is verified. This
        is what isolates early stopping as the only variable a structural-
        speedup claim is allowed to credit — comparing against the first
        bullet's `_bounded_exhaustive_builder` path would also be comparing
        two different code paths' instrumentation, caching and iteration
        order, which is not the same claim.

    Within a `cost_ordered=True` pair, the ONLY difference is
    `disable_early_stop` — spec, demand, policy, runner, ledger and candidate
    order are identical by construction, which is what makes a measured
    difference in the outcome attributable to the stopping decision and
    nothing else.

    `daily_workers`/`seed_workers`/`max_active_sumo_slots` are explicit here,
    not left to whatever `IndependentDailyRunner` would otherwise infer from
    the process environment: a benchmark that silently inherited an ambient
    `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS` from the calling shell would be
    measuring extra parallelism, not the ordering change, and could not tell
    the difference. Passing `queue_workers` explicitly closes that seam.

    `daily_results_cache_root` is the real per-daily-unit SUMO evidence
    cache (distinct from `daily_cost_cache`, the process-free deterministic
    COST prediction store used only to pick candidate order). It defaults to
    `daily_cost_cache.parent / "daily-results"` for callers outside a
    two-arm comparison. A benchmark comparing two arms MUST pass a distinct
    root per arm — see `tools.cost_ordered_benchmark._isolated_daily_results_cache_root`
    — or whichever arm runs second silently reuses the first arm's real SUMO
    results, corrupting the attempt-count/wall-time numbers being compared.
    """
    from run_monthly_closure_search import (
        _bounded_exhaustive_builder,
        _cost_source_for,
        _independent_exhaustive_builder,
    )
    from traffic_sim.simulation.envelope import EnvelopePolicy
    from traffic_sim.simulation.independent_daily import (
        INDEPENDENT_DAILY_ENVELOPE_POLICY,
        IndependentDailyRunner,
    )
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner

    if (
        isinstance(daily_workers, bool) or daily_workers < 1
        or isinstance(seed_workers, bool) or seed_workers < 1
        or isinstance(max_active_sumo_slots, bool) or max_active_sumo_slots < 1
    ):
        raise ValueError(
            "daily_workers, seed_workers and max_active_sumo_slots must be "
            "positive integers")
    if daily_workers * seed_workers > max_active_sumo_slots:
        raise ValueError(
            "daily_workers * seed_workers exceeds max_active_sumo_slots for "
            "this arm")
    if disable_early_stop and not cost_ordered:
        raise ValueError(
            "disable_early_stop only has meaning for a cost-ordered arm; "
            "the exhaustive screening path never stops early to begin with")

    independent = spec.interday_policy == "independent_daily_reset_v1"
    if cost_ordered and not independent:
        raise ValueError(
            "cost-ordered execution prices a parent from its daily units; it "
            "requires the independent daily reset policy")

    resolved = MonthlyDemandResolverRunner(
        spec,
        runs_root=Path(runs_root),
        release_root=Path(release_root),
        build_missing=False,
        baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
        study_provenance_key=study_provenance_key,
        seed_workers=seed_workers,
        include_disruption=objective_method == "closure_cost_v1",
        envelope_policy=(INDEPENDENT_DAILY_ENVELOPE_POLICY if independent
                         else EnvelopePolicy()),
    )
    if independent:
        resolved_cache_root = (
            Path(daily_results_cache_root) if daily_results_cache_root
            is not None else Path(daily_cost_cache).parent / "daily-results")
        runner = IndependentDailyRunner(
            spec, daily_runner=resolved,
            cache_root=resolved_cache_root,
            queue_workers=daily_workers)

        def screen_builder(path):
            return _independent_exhaustive_builder(
                path,
                maximum_candidates=MAXIMUM_CANDIDATES,
                maximum_daily_units=MAXIMUM_DAILY_UNITS,
                baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
                preflight_report=None)
    else:
        runner = resolved

        def screen_builder(path):
            return _bounded_exhaustive_builder(
                path, maximum_candidates=MAXIMUM_CANDIDATES)

    cost_source = None
    if cost_ordered:
        cost_source = _cost_source_for(
            spec, runner, daily_cost_cache=daily_cost_cache,
            window_cost_index=window_cost_index)
    return runner, screen_builder, cost_source


def run_arm(
    spec: ClosureSearchSpec,
    policy,
    *,
    cost_ordered: bool,
    workspace_root: Path,
    runs_root: Path,
    release_root: Path,
    daily_cost_cache: Path,
    study_provenance_key: str,
    seed_workers: int = 1,
    daily_workers: int = 1,
    max_active_sumo_slots: int = 1,
    daily_results_cache_root: Path | None = None,
    disable_early_stop: bool = False,
    window_cost_index: Any = None,
    max_verifications: int | None = None,
    max_exact_launches: int | None = None,
    fixture_controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one arm end to end and report what it cost.

    `disable_early_stop=True` (only valid with `cost_ordered=True`) produces
    the ordered-exhaustive reference arm — see `build_arm`.
    """
    from traffic_sim.simulation.monthly_search import (
        ActiveBudgetExceeded, run_monthly_search,
    )

    runner, screen_builder, cost_source = build_arm(
        spec,
        cost_ordered=cost_ordered,
        runs_root=runs_root,
        release_root=release_root,
        daily_cost_cache=daily_cost_cache,
        study_provenance_key=study_provenance_key,
        objective_method=policy.objective_method,
        seed_workers=seed_workers,
        daily_workers=daily_workers,
        max_active_sumo_slots=max_active_sumo_slots,
        daily_results_cache_root=daily_results_cache_root,
        disable_early_stop=disable_early_stop,
        window_cost_index=window_cost_index,
    )
    fixture_state: dict[str, Any] = {}
    if fixture_controls:
        runner = _FixtureRunner(runner, fixture_controls, fixture_state)
        if cost_source is not None:
            cost_source = _FixtureCostSource(
                cost_source, fixture_controls, fixture_state)
    started = time.monotonic()
    budget_error: ActiveBudgetExceeded | None = None
    execution_error: BaseException | None = None
    rss_before = peak_rss_bytes()
    try:
        try:
            result = run_monthly_search(
                spec, policy,
                runner=runner,
                screen_builder=screen_builder,
                root=Path(workspace_root),
                cost_source=cost_source,
                disable_early_stop=disable_early_stop,
                max_verifications=max_verifications,
                max_exact_launches=max_exact_launches,
            )
        except ActiveBudgetExceeded as error:
            # Preserve the runner's exact launch telemetry even when the
            # registered cap stops the search before a normal result exists.
            budget_error = error
            result = {
                "status": "inconclusive",
                "terminal_status": "INCONCLUSIVE_BUDGET_EXHAUSTED",
                "error": str(error),
            }
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
            # Preserve the producer's live telemetry when a real execution
            # fails after SUMO work has already happened.  Raising here made
            # the registered benchmark replace the whole case with a tiny
            # {case_id, error} dictionary, losing attempts, active time and
            # the runner's exact launch records before the validator saw it.
            execution_error = error
            result = {
                "status": "inconclusive",
                "terminal_status": "INCONCLUSIVE_EXECUTION_ERROR",
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    # Pulled after the search and before the counters can be discarded by a
    # caller.  This also runs for an exact-launch cap terminal.
    exact_launch_telemetry = _runner_exact_launch_telemetry(runner)
    exact_launch_records = _runner_exact_launch_records(runner)
    daily_results_cache_events = _runner_daily_results_cache_events(runner)
    daily_results_cache_event_records = (
        _runner_daily_results_cache_event_records(runner))
    if not cost_ordered:
        arm_name = "exhaustive"
    elif disable_early_stop:
        arm_name = "ordered_exhaustive"
    else:
        arm_name = "cost_ordered"
    workspace_directory = Path(workspace_root) / spec.search_id
    return {
        "arm": arm_name,
        "search_id": spec.search_id,
        "workspace": str(workspace_directory),
        "search_content_key": spec.content_key,
        "result": result,
        "budget_exhausted": budget_error is not None,
        "execution_error": (
            {"type": type(execution_error).__name__,
             "message": str(execution_error)}
            if execution_error is not None else None),
        "wall_time_s": round(time.monotonic() - started, 3),
        # Reported as a pair: the peak BEFORE this arm is what the process had
        # already reached, so a reader can tell a genuine arm cost from a high
        # watermark the arm merely inherited.
        "peak_rss_bytes": peak_rss_bytes(),
        "peak_rss_before_bytes": rss_before,
        "daily_cost_cache_hits": int(getattr(cost_source, "cache_hits", 0)),
        "computed_daily_units": int(getattr(cost_source, "computed_units", 0)),
        "daily_results_cache_root": (
            str(Path(daily_results_cache_root))
            if daily_results_cache_root is not None else None),
        # Result-neutral S0 telemetry: exact SUMO-launch counts (pilot and
        # finalist, split by timeout vs. any other outcome) and this arm's
        # own awake-active elapsed wall time on the declared existing basis
        # (`search_workspace.ACTIVE_ELAPSED_BASIS`), read from the workspace
        # manifest this run just published. Neither field feeds the search's
        # decision; both exist so a benchmark can gate on real launch/awake-
        # time reduction instead of approximating it from pilot-candidate
        # counts.
        "exact_launch_telemetry": exact_launch_telemetry,
        # Identity-bearing companion (candidate/work-date/variant/seed/
        # attempt/outcome) to the aggregate above — what makes an exact
        # ATTEMPT POPULATION comparison possible, not just a total.
        "exact_launch_records": exact_launch_records,
        # The daily-result cache's own hit/miss/corrupt/publication counts —
        # see `_runner_daily_results_cache_events` for why this is a
        # separate population from `daily_cost_cache_hits` above.
        "daily_results_cache_events": daily_results_cache_events,
        # Identity-bearing companion — see
        # `_runner_daily_results_cache_event_records` for why this is what
        # makes a cache-event POPULATION comparison possible, not just a
        # comparison of aggregate counts.
        "daily_results_cache_event_records": daily_results_cache_event_records,
        "active_elapsed_s": _workspace_active_elapsed_s(workspace_directory),
        "active_elapsed_basis": _workspace_active_elapsed_basis(
            workspace_directory),
        "fixture_telemetry": dict(fixture_state.get("cancel_observed") or {})
        | {"applied": sorted(fixture_state.get("applied", set())),
           "runner_events": list(fixture_state.get("runner_events", []))},
    }


def _kill_process_group(pid: int, grace_s: float) -> None:
    """Terminate, then (after a grace period) kill, an owned process group.

    Used only on the isolated-arm path's own subprocess, so this can never
    reach outside what this call itself started.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _ensure_process_group_reaped(pgid: int, grace_s: float) -> list[int]:
    """Verify `pgid` is empty; escalate TERM/KILL if anything survived.

    Called after EVERY isolated-arm outcome — success, failure, or timeout —
    not only the timeout branch: a worker that exited normally can still have
    left an orphaned SUMO grandchild behind (e.g. a crash mid-spawn), and a
    normal exit code proves nothing about the process GROUP being empty.
    Returns the PIDs that were still alive when this call STARTED, for the
    caller to report; an empty list means nothing needed reaping.

    Every census here (`_process_group_pids`, which calls
    `_process_group_snapshot`) raises `ProcessCensusUnavailable` rather than
    reporting an empty group when the census itself is untrustworthy, so an
    unavailable census fails this function loudly instead of being read as "no
    survivors". After any escalation, a FINAL verified post-termination
    census confirms the group is actually empty before this returns — an
    escalation that merely timed out without ever re-checking would let a
    still-alive process pass as reaped.
    """
    survivors = _process_group_pids(pgid)
    if survivors:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        else:
            deadline = time.monotonic() + grace_s
            while time.monotonic() < deadline and _process_group_pids(pgid):
                time.sleep(0.2)
            if _process_group_pids(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
    # A SIGKILL takes a moment to actually remove the process from the
    # kernel's table, so the verified post-termination census gets a short
    # settle window of its own rather than a single immediate snapshot.
    settle_deadline = time.monotonic() + max(grace_s, 2.0)
    remaining = _process_group_pids(pgid)
    while remaining and time.monotonic() < settle_deadline:
        time.sleep(0.2)
        remaining = _process_group_pids(pgid)
    if remaining:
        raise RuntimeError(
            f"process group {pgid} still held live processes {remaining} "
            "after termination was attempted; refusing to report it reaped")
    return survivors


def _run_arm_worker(args: Mapping[str, Any]) -> None:
    """Isolated-process entry point, invoked only via `--worker-run-arm`.

    Writes its outcome to `args["result_path"]` rather than returning it on
    stdout/a pipe: this project's evidence requirements make a
    silently-truncated or interleaved payload the wrong failure mode, and a
    JSON file the parent reads only after confirming the child's exit code
    has no such race. This matches how every other artifact here is
    published (write to a temp path, then atomic rename).

    Deliberately does NOT call ``os.setsid()`` itself. The session/process
    group this arm runs in is created by the PARENT's
    ``subprocess.Popen(..., start_new_session=True)`` — atomically, between
    fork and exec, before this file's code ever runs. An earlier version of
    this isolation called ``os.setsid()`` from inside a
    ``multiprocessing.Process`` target instead, which left a real window
    where the child still shared the parent's process group; a timeout
    landing in that window would have made `_kill_process_group` signal the
    PARENT's own group. `start_new_session=True` closes that window
    entirely rather than narrowing it.
    """
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

    os.chdir(args["data_root"])
    outcome: dict[str, Any]
    try:
        spec = ClosureSearchSpec.from_dict(args["spec"])
        policy = MonthlySearchPolicy.from_dict(args["policy"])
        daily_results_cache_root = args.get("daily_results_cache_root")
        result = run_arm(
            spec, policy,
            cost_ordered=bool(args["cost_ordered"]),
            workspace_root=Path(args["workspace_root"]),
            runs_root=Path(args["runs_root"]),
            release_root=Path(args["release_root"]),
            daily_cost_cache=Path(args["daily_cost_cache"]),
            study_provenance_key=str(args["study_provenance_key"]),
            seed_workers=int(args["seed_workers"]),
            daily_workers=int(args["daily_workers"]),
            max_active_sumo_slots=int(args["max_active_sumo_slots"]),
            daily_results_cache_root=(
                Path(daily_results_cache_root)
                if daily_results_cache_root is not None else None),
            disable_early_stop=bool(args.get("disable_early_stop", False)),
            max_verifications=(
                None if args.get("max_verifications") is None
                else int(args["max_verifications"])),
            max_exact_launches=(
                None if args.get("max_exact_launches") is None
                else int(args["max_exact_launches"])),
            fixture_controls=args.get("fixture_controls"),
        )
        outcome = {"ok": True, "result": result}
    except BaseException as exc:  # noqa: BLE001 - report every failure, then exit
        outcome = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    path = Path(args["result_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(outcome, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def run_arm_isolated(
    spec: ClosureSearchSpec,
    policy,
    *,
    cost_ordered: bool,
    workspace_root: Path,
    runs_root: Path,
    release_root: Path,
    daily_cost_cache: Path,
    study_provenance_key: str,
    data_root: Path,
    seed_workers: int = BENCHMARK_SEED_WORKERS,
    daily_workers: int = BENCHMARK_DAILY_WORKERS,
    max_active_sumo_slots: int = BENCHMARK_MAX_ACTIVE_SUMO_SLOTS,
    timeout_s: float = 7200.0,
    reap_grace_s: float = 30.0,
    daily_results_cache_root: Path | None = None,
    disable_early_stop: bool = False,
    max_verifications: int | None = None,
    max_exact_launches: int | None = None,
    fixture_controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one arm in its own process AND process group.

    This is real process isolation, not sequential-in-one-interpreter
    execution: a fresh interpreter, its own `resource.getrusage` domain (so
    the reported peak RSS is exactly this arm's, never a watermark the other
    arm or earlier setup work left behind), and — via
    `start_new_session=True` — its own session/process group from the
    instant it is created, so a hang or an orphaned SUMO child can be reaped
    without any risk of touching the parent's or the other arm's group. This
    is what the plan's cost-order-v5 remediation asks for under "run arms
    under the same isolated resource budget": the same BUDGET on both arms
    (`daily_workers=1`, `seed_workers=1`, `max_active_sumo_slots=1` by
    default here — see `BENCHMARK_*`), never a shared process or a shared
    ambient environment.

    Arguments cross the process boundary as a JSON args file rather than
    pickled Python objects, and `python3 -m tools.product_arm
    --worker-run-arm <path>` is the only way `_run_arm_worker` ever runs —
    this sidesteps every pickling/spawn-bootstrap subtlety of
    `multiprocessing` and gives a completely ordinary, easily-reaped child
    process.
    """
    workspace_root = Path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    if not cost_ordered:
        suffix = "exhaustive"
    elif disable_early_stop:
        suffix = "ordered_exhaustive"
    else:
        suffix = "cost_ordered"
    result_path = workspace_root / f"isolated-arm-result-{suffix}.json"
    args_path = workspace_root / f"isolated-arm-args-{suffix}.json"
    if result_path.exists():
        raise RuntimeError(
            f"a prior isolated-arm result already exists at {result_path}; "
            "refusing to silently overwrite it — a fresh comparison never "
            "reuses a pre-existing destination (remove it or use a fresh "
            "workspace_root)")
    args_path.write_text(json.dumps({
        "spec": spec.to_dict(),
        "policy": policy.to_dict(),
        "cost_ordered": cost_ordered,
        "workspace_root": str(workspace_root),
        "runs_root": str(runs_root),
        "release_root": str(release_root),
        "daily_cost_cache": str(daily_cost_cache),
        "study_provenance_key": study_provenance_key,
        "data_root": str(data_root),
        "seed_workers": seed_workers,
        "daily_workers": daily_workers,
        "max_active_sumo_slots": max_active_sumo_slots,
        "daily_results_cache_root": (
            str(Path(daily_results_cache_root))
            if daily_results_cache_root is not None else None),
        "disable_early_stop": disable_early_stop,
        "max_verifications": max_verifications,
        "max_exact_launches": max_exact_launches,
        "fixture_controls": dict(fixture_controls or {}),
        "result_path": str(result_path),
    }, indent=1, sort_keys=True), encoding="utf-8")

    process = subprocess.Popen(
        [sys.executable, "-m", "tools.product_arm",
         "--worker-run-arm", str(args_path)],
        cwd=str(ROOT),
        start_new_session=True,
    )
    # `start_new_session=True` makes this process its own session AND
    # process group leader, so its own pid is also the pgid for everything
    # it (or SUMO underneath it) spawns.
    pgid = process.pid
    sampler = ProcessTreeRSSSampler(pgid).start()
    try:
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_group(process.pid, reap_grace_s)
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass
            raise TimeoutError(
                f"isolated arm process did not finish within {timeout_s}s "
                "and was reaped; no evidence from this arm is trustworthy"
            )
        if not result_path.exists():
            raise RuntimeError(
                f"isolated arm process exited {process.returncode} without "
                "publishing a result file"
            )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"isolated arm failed: {payload.get('error')}")
        result = dict(payload["result"])
    finally:
        # Verified on EVERY exit path — success, failure or timeout — never
        # only after a timeout: a clean exit code says nothing about whether
        # a grandchild SUMO process is still alive in this group.
        sampled_peak = sampler.stop()
        survivors = _ensure_process_group_reaped(pgid, reap_grace_s)
        if survivors:
            raise RuntimeError(
                f"isolated arm process group {pgid} still held live "
                f"processes {survivors} after exit; they were reaped, but "
                "no evidence from this arm is trustworthy"
            )
    result["process_tree_peak_rss_bytes"] = sampled_peak
    # The worker's own getrusage-based number is `max(self, one reaped
    # child)`, never their simultaneous sum — take whichever is larger so a
    # brief spike between samples still cannot make this UNDER-report.
    result["peak_rss_bytes"] = max(
        int(result.get("peak_rss_bytes", 0)), sampled_peak)
    return result


def sumo_pilot_count(result: Mapping[str, Any]) -> int | None:
    """How many candidates the PILOT actually simulated.

    Cost-ordered runs publish it. An exhaustive run does not — its pilot count
    is the shortlist, which the screening record already states — so this
    returns None there and the caller reads the shortlist instead. Returning a
    guess would be worse than returning nothing.
    """
    execution = result.get("cost_ordered_execution")
    if not execution:
        return None
    return int(execution["cost_ordered_sumo_candidates"])


def _main(argv: list[str] | None = None) -> int:
    """CLI surface. The only supported use is the isolated-arm worker mode.

    `--worker-run-arm <args-path>` is what `run_arm_isolated` execs; it is
    not meant to be typed by hand. Keeping it a plain CLI flag (rather than,
    say, an env var) means the args file is visible in the process's own
    argv for anyone inspecting the process table while a benchmark runs.
    """
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--worker-run-arm":
        args = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        _run_arm_worker(args)
        return 0
    print(
        "usage: python3 -m tools.product_arm --worker-run-arm <args.json>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(_main())
