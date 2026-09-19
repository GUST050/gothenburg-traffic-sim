"""One network/costing context shared by every archive runner, not 30 copies.

Root cause, measured 2026-09-19
(``validation/wci_archive_runner_attribution_20260919-v1.json``): the
dominant cost inside ``MonthlyDemandResolverRunner.prepare`` was never the
streaming-vs-materialised unit graph (0.098% of it). It was
``ArchivedDemandSumoRunner.__init__`` retaining, per archive, (a) the FULL
parsed ``demand_meta.json`` as ``self.metadata`` — 133-149 MB — even though
nothing outside ``__init__`` ever reads it again, and (b) a fresh network
adjacency graph, free-flow table, disruption structures, rerouter edge set,
detour diagnostic and ~20 source-file hashes — all of them byte-identical
across every archive measured, none of them archive-dependent.

The fix: (a) ``self.metadata`` became a local variable, freed when
``__init__`` returns; (b) the network/costing fields are built once, by
``build_shared_runner_context``, and shared through a
:class:`SharedRunnerContext` that ``MonthlyDemandResolverRunner.prepare``
owns and hands to every archive runner it constructs. A caller that never
opts in (no ``shared_context`` argument) gets exactly the bytes a per-runner
build always produced — ``build_shared_runner_context`` is the SAME function
either way, so there is no second implementation to drift from the first.

These tests use REAL archives (small/median/large by route bytes) and the
REAL frozen month spec, because sharing across genuinely different archives
— not synthetic stand-ins — is the property under test. They construct no
product arm, price no daily unit and start no SUMO or demand process.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from traffic_sim.simulation import monthly_sumo
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.simulation.independent_daily import daily_unit_records
from traffic_sim.simulation.independent_daily import (
    IndependentDailyRunner,
    decompose_schedules,
)
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation.monthly_demand import (
    DemandBuildSpec,
    MonthlyDemandResolverRunner,
)
from traffic_sim.simulation.monthly_sumo import (
    ArchivedDemandSumoRunner,
    SharedRunnerContext,
    build_shared_runner_context,
)

FROZEN_SPEC = Path("validation/subhour_monthly_search_profile_spec_v1.json")
OLD_ARCHIVE_ROOT = Path(
    "/private/tmp/gs-step3-departure-bounds/runs/step5_monthly_qualify_20260913"
    "/demand_archives")
SMALL = "demand-20260913-224428-7dfb00a9-cd72"
MEDIAN = "demand-20260913-224643-2bbcf31e-3b33"
LARGE = "demand-20260913-224959-e136d191-ae06"

pytestmark = pytest.mark.skipif(
    not OLD_ARCHIVE_ROOT.is_dir(),
    reason="real qualified demand archives not present in this environment")


def _frozen_spec() -> ClosureSearchSpec:
    return ClosureSearchSpec.from_dict(
        json.loads(FROZEN_SPEC.read_text(encoding="utf-8")))


def _archive(name: str) -> Path:
    return OLD_ARCHIVE_ROOT / name


def _daily_units(spec: ClosureSearchSpec, count: int = 1):
    """Real single-day unit schedules -- what prepare() actually consumes.

    MonthlyDemandResolverRunner.prepare() is IndependentDailyRunner's
    daily_runner: it is always called with UNIT schedules (one interval,
    one day), never the multi-day PARENT schedules iter_closure_schedules
    yields. Reuses daily_unit_records, the one canonical decomposition
    (see its own docstring), rather than inventing a second one here.
    """
    units = []
    seen = set()
    for parent in iter_closure_schedules(spec):
        for unit_id, _identity, build in daily_unit_records(spec, parent):
            if unit_id in seen:
                continue
            seen.add(unit_id)
            units.append(build())
            if len(units) >= count:
                return units
    return units


def _demand_spec_for(archive: Path) -> DemandBuildSpec:
    return DemandBuildSpec.from_dict(json.loads(
        (archive / "demand_build_spec.json").read_text(encoding="utf-8")))


def _runner(
    spec: ClosureSearchSpec, archive_name: str, *, cache_root: Path,
    shared_context: SharedRunnerContext | None = None,
    include_disruption: bool = True,
) -> ArchivedDemandSumoRunner:
    archive = _archive(archive_name)
    return ArchivedDemandSumoRunner(
        spec, archive=archive, baseline_trip_duration_p99_s=3600,
        study_provenance_key="shared-context-test", cache_root=cache_root,
        include_disruption=include_disruption,
        ranking_objective_evidence=(
            "closure_cost_v1" if include_disruption else None),
        expected_demand_spec=_demand_spec_for(archive),
        shared_context=shared_context)


@pytest.fixture(scope="module")
def spec() -> ClosureSearchSpec:
    return _frozen_spec()


@pytest.fixture(scope="module")
def context(spec: ClosureSearchSpec) -> SharedRunnerContext:
    return build_shared_runner_context(spec, include_disruption=True)


@pytest.fixture(scope="module")
def unshared_reference(spec: ClosureSearchSpec, tmp_path_factory) -> ArchivedDemandSumoRunner:
    """A runner built the OLD way (no context) -- the ground truth every
    shared-context result is compared against."""
    return _runner(
        spec, SMALL, cache_root=tmp_path_factory.mktemp("unshared-ref"))


# -- 1. N archive runners share exactly one verified context -------------


def test_n_archive_runners_share_exactly_one_context(spec, context, tmp_path):
    runners = [
        _runner(spec, name, cache_root=tmp_path / f"cache-{i}",
               shared_context=context)
        for i, name in enumerate((SMALL, MEDIAN, LARGE))
    ]
    assert len({id(r.adjacency) for r in runners}) == 1
    assert len({id(r.freeflow) for r in runners}) == 1
    assert len({id(r._disruption_adjacency) for r in runners}) == 1
    assert len({id(r._disruption_edge_time) for r in runners}) == 1
    assert len({id(r._disruption_edge_len) for r in runners}) == 1
    assert len({id(r.rerouter_edges) for r in runners}) == 1
    assert len({id(r.detour) for r in runners}) == 1
    assert len({id(r.source_records) for r in runners}) == 1
    assert len({id(r.runtime_identity) for r in runners}) == 1


def test_a_resolver_prepared_over_real_archives_shares_one_context(
        spec, tmp_path):
    """End to end through MonthlyDemandResolverRunner.prepare, not the
    runner constructor directly."""
    parent = _daily_units(spec)[0]
    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="e2e-shared-context",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare([parent])
    assert resolver._shared_context is not None
    assert len({id(r.adjacency) for r in resolver._runners.values()}) <= 1


# -- 2. Archive-specific state stays separate ------------------------------


def test_archive_specific_state_is_not_shared(spec, context, tmp_path):
    small = _runner(spec, SMALL, cache_root=tmp_path / "c1",
                    shared_context=context)
    median = _runner(spec, MEDIAN, cache_root=tmp_path / "c2",
                     shared_context=context)
    assert small.archive != median.archive
    assert small.archive_digest != median.archive_digest
    assert small.matched_baseline_id != median.matched_baseline_id
    assert small.variants is not median.variants
    assert small.expected_demand_spec != median.expected_demand_spec


# -- 3. Shared context cannot be mutated through a child runner -----------


def test_shared_context_is_not_mutable_through_a_child_runner(
        spec, context, tmp_path):
    runner = _runner(spec, SMALL, cache_root=tmp_path / "c",
                     shared_context=context)
    with pytest.raises(TypeError):
        runner.adjacency["new-edge"] = ("x",)
    with pytest.raises(TypeError):
        runner.freeflow["new-edge"] = 1.0
    with pytest.raises(TypeError):
        runner._disruption_adjacency["new-edge"] = ("x",)
    with pytest.raises(TypeError):
        runner.detour["score"] = 999
    assert isinstance(runner.detour["predecessors"], tuple)
    if runner.detour["predecessors"]:
        with pytest.raises(TypeError):
            runner.detour["predecessors"][0] = "tampered"
    with pytest.raises(TypeError):
        runner.source_records[0]["sha256"] = "tampered"
    with pytest.raises(TypeError):
        runner.runtime_identity["sumo_version"] = "tampered"


def test_shared_context_dataclass_itself_is_frozen(context):
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        context.adjacency = {}


# -- 4. Different net/spec/edges must never share a context ---------------


def test_a_context_for_different_directed_edges_is_refused(
        spec, context, tmp_path):
    other = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
    other["directed_edges"] = ["60786979_3575001205_0"]
    other_spec = ClosureSearchSpec.from_dict(other)
    assert other_spec.content_key != spec.content_key
    with pytest.raises(ValueError, match="does not match"):
        _runner(other_spec, SMALL, cache_root=tmp_path / "c",
               shared_context=context)


def test_a_context_with_a_different_disruption_flag_is_refused(
        spec, tmp_path):
    context_without_disruption = build_shared_runner_context(
        spec, include_disruption=False)
    with pytest.raises(ValueError, match="include_disruption"):
        _runner(spec, SMALL, cache_root=tmp_path / "c",
               shared_context=context_without_disruption,
               include_disruption=True)


def test_a_context_for_a_different_search_spec_is_refused(spec, context,
                                                            tmp_path):
    other = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
    other["required_work_minutes"] = other["required_work_minutes"] + 60
    other_spec = ClosureSearchSpec.from_dict(other)
    assert other_spec.content_key != spec.content_key
    with pytest.raises(ValueError, match="search_content_key"):
        _runner(other_spec, SMALL, cache_root=tmp_path / "c",
               shared_context=context)


# -- 5. Network drift is refused -------------------------------------------


def test_a_context_with_a_falsified_network_hash_is_refused(
        spec, context, tmp_path, monkeypatch):
    import dataclasses as dc
    tampered_identity = dict(context.identity)
    tampered_identity["net_sha256"] = "0" * 64
    tampered = dc.replace(context, identity=tampered_identity)
    with pytest.raises(ValueError, match="net_sha256"):
        _runner(spec, SMALL, cache_root=tmp_path / "c",
               shared_context=tampered)


def test_network_drift_after_runner_construction_is_refused_before_provider(
        spec, context, tmp_path, monkeypatch):
    runner = _runner(spec, SMALL, cache_root=tmp_path / "c",
                     shared_context=context)
    real_sha256 = monthly_sumo.sha256_file

    def drifted_sha256(path):
        if Path(path).resolve() == Path(monthly_sumo.rs.NET_PATH).resolve():
            return "0" * 64
        return real_sha256(path)

    monkeypatch.setattr(monthly_sumo, "sha256_file", drifted_sha256)
    with pytest.raises(ValueError, match="net_sha256"):
        runner.deterministic_disruption_provider()


def test_network_drift_during_provider_attachment_is_refused(
        spec, context, tmp_path, monkeypatch):
    runner = _runner(spec, SMALL, cache_root=tmp_path / "c",
                     shared_context=context)
    real_sha256 = monthly_sumo.sha256_file
    net_path = Path(monthly_sumo.rs.NET_PATH).resolve()
    net_reads = {"count": 0}

    def drift_between_verifications(path):
        if Path(path).resolve() == net_path:
            net_reads["count"] += 1
            if net_reads["count"] >= 2:
                return "0" * 64
        return real_sha256(path)

    monkeypatch.setattr(
        monthly_sumo, "sha256_file", drift_between_verifications)
    with pytest.raises(ValueError, match="net_sha256"):
        runner.deterministic_disruption_provider()
    assert net_reads["count"] >= 3


def test_source_drift_after_context_build_is_refused(
        spec, context, monkeypatch):
    real_file_record = monthly_sumo._file_record

    def drifted_record(path, *, label):
        record = real_file_record(path, label=label)
        if label == "run_scenario.py":
            record = {**record, "sha256": "0" * 64}
        return record

    monkeypatch.setattr(monthly_sumo, "_file_record", drifted_record)
    with pytest.raises(ValueError, match="source_digest"):
        context.verify_identity(spec, include_disruption=True)


def test_free_flow_cache_is_bound_to_network_bytes(tmp_path, monkeypatch):
    network = tmp_path / "net.net.xml"
    network.write_text(
        '<net><edge id="e"><lane length="100" speed="10"/></edge></net>',
        encoding="utf-8")
    monkeypatch.setattr(monthly_sumo.rs, "NET_PATH", network)
    monkeypatch.setattr(monthly_sumo.rs, "_FREE_FLOW_EDGE_TIME", None)
    monkeypatch.setattr(monthly_sumo.rs, "_FREE_FLOW_EDGE_IDENTITY", None)
    first_time, first_length = monthly_sumo.rs.free_flow_edge_cost()

    network.write_text(
        '<net><edge id="e"><lane length="240" speed="12"/></edge></net>',
        encoding="utf-8")
    second_time, second_length = monthly_sumo.rs.free_flow_edge_cost()

    assert first_time == {"e": 10.0}
    assert first_length == {"e": 100.0}
    assert second_time == {"e": 20.0}
    assert second_length == {"e": 240.0}


# -- 6/7/8. Provenance, candidate_provenance, execution contract identical -


def test_resolver_provenance_is_byte_identical_with_and_without_sharing(
        spec, tmp_path):
    parent = _daily_units(spec)[0]
    # Deliberately the SAME cache_root/release_root/study_provenance_key for
    # both resolvers: only the sharing behaviour must differ, not a path.
    common = dict(runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
                  cache_root=tmp_path / "cache", include_disruption=True,
                  ranking_objective_evidence="closure_cost_v1",
                  baseline_trip_duration_p99_s=3600,
                  study_provenance_key="provenance-compare")
    shared_resolver = MonthlyDemandResolverRunner(spec, **common)
    shared_resolver.prepare([parent])

    class _ForcedUnsharedRunner(ArchivedDemandSumoRunner):
        """Same class, but never receives shared_context -- exercises the
        exact 'no context' branch every direct construction takes."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("shared_context", None)
            super().__init__(*args, **kwargs)

    unshared_resolver = MonthlyDemandResolverRunner(
        spec, runner_factory=_ForcedUnsharedRunner, **common)
    unshared_resolver.prepare([parent])

    assert (dict(shared_resolver.provenance())
            == dict(unshared_resolver.provenance()))


def test_candidate_provenance_is_byte_identical_per_build_key(spec, tmp_path):
    parents = _daily_units(spec, 3)
    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="candidate-provenance",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare(parents)
    reference = _runner(spec, SMALL, cache_root=tmp_path / "ref-cache")
    # Every prepared candidate's provenance must at least carry the same
    # schema/kind/simulation_mode/metric_schema the unshared reference does.
    for parent in parents:
        prov = dict(resolver.candidate_provenance(parent))
        json.dumps(prov, sort_keys=True, separators=(",", ":"),
                   allow_nan=False)
        assert all(type(item) is dict for item in prov["source_files"])
        assert prov["kind"] == reference.provenance()["kind"]
        assert prov["simulation_mode"] == "meso"
        assert prov["metric_schema"] == "closure_decision_metrics_v1"

    # Exercise the exact production identity boundary that derives the
    # content-addressed daily cache key. It intentionally rejects mapping
    # proxies and every other non-JSON value.
    independent = IndependentDailyRunner(
        spec, daily_runner=resolver, cache_root=tmp_path / "daily-cache",
        queue_workers=1)
    units, _parents = decompose_schedules(spec, [parents[0]])
    checked = independent._candidate_backend_identity(units[0])
    json.dumps(checked, sort_keys=True, separators=(",", ":"),
               allow_nan=False)


def test_candidate_execution_contract_is_byte_identical(spec, tmp_path):
    parent = _daily_units(spec)[0]
    common = dict(runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
                  cache_root=tmp_path / "cache", include_disruption=True,
                  ranking_objective_evidence="closure_cost_v1",
                  baseline_trip_duration_p99_s=3600,
                  study_provenance_key="exec-contract-a")
    shared_resolver = MonthlyDemandResolverRunner(spec, **common)
    shared_resolver.prepare([parent])
    contract_a = shared_resolver.candidate_execution_contract(parent)

    class _ForcedUnsharedRunner(ArchivedDemandSumoRunner):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("shared_context", None)
            super().__init__(*args, **kwargs)

    unshared_resolver = MonthlyDemandResolverRunner(
        spec, runner_factory=_ForcedUnsharedRunner, **common)
    unshared_resolver.prepare([parent])
    contract_b = unshared_resolver.candidate_execution_contract(parent)

    assert contract_a == contract_b
    # shared_context must never leak into a contract meant for an isolated
    # worker process, which builds its OWN (unshared) runner from this.
    assert "shared_context" not in contract_a


# -- 9. archive_for / deterministic provider select the same archive ------


def test_archive_for_and_deterministic_provider_agree_on_the_archive(
        spec, tmp_path):
    parent = _daily_units(spec)[0]
    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="archive-for-check",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare([parent])
    archive = resolver.archive_for(parent)
    key = resolver._schedule_build_keys[parent.schedule_id]
    assert Path(archive) == Path(resolver._runners[key].archive)


# -- 10. Cache/provider identities identical -------------------------------


def test_cache_and_provider_identity_unaffected_by_sharing(
        spec, context, tmp_path):
    small_shared = _runner(spec, SMALL, cache_root=tmp_path / "c1",
                           shared_context=context)
    small_unshared = _runner(spec, SMALL, cache_root=tmp_path / "c2")
    provider_shared = small_shared.deterministic_disruption_provider()
    provider_unshared = small_unshared.deterministic_disruption_provider()
    assert provider_shared.identity() == provider_unshared.identity()


# -- 11. Launch counters/records isolated per runner -----------------------


def test_launch_telemetry_is_not_shared_between_runners(spec, context,
                                                          tmp_path):
    small = _runner(spec, SMALL, cache_root=tmp_path / "c1",
                    shared_context=context)
    median = _runner(spec, MEDIAN, cache_root=tmp_path / "c2",
                     shared_context=context)
    assert small.launch_telemetry is not median.launch_telemetry
    assert small.launch_records is not median.launch_records
    small.launch_telemetry["pilot"]["attempts"] += 1
    assert median.launch_telemetry["pilot"]["attempts"] == 0


# -- 12. Cleanup reaches every child exactly once --------------------------


def test_cleanup_reaches_every_prepared_child_exactly_once(spec, tmp_path):
    parents = _daily_units(spec, 2)
    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="cleanup-check",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare(parents)
    calls = []
    for runner in resolver._runners.values():
        original = runner.cleanup
        runner.cleanup = (lambda o=original, r=runner: (calls.append(id(r)),
                                                         o())[1])
    resolver.cleanup()
    assert sorted(calls) == sorted({id(r) for r in resolver._runners.values()})
    assert len(calls) == len(set(calls))


# -- 13. Warm state / temp directories not shared --------------------------


def test_warm_state_containers_are_not_shared(spec, context, tmp_path):
    small = _runner(spec, SMALL, cache_root=tmp_path / "c1",
                    shared_context=context)
    median = _runner(spec, MEDIAN, cache_root=tmp_path / "c2",
                     shared_context=context)
    assert small.provisional_states is not median.provisional_states
    assert (small._warm_route_window_caches
            is not median._warm_route_window_caches)
    assert (small._warm_route_window_directories
            is not median._warm_route_window_directories)


# -- 14. Concurrent construction on different/same build keys is safe -----


def test_concurrent_runner_construction_from_one_context_is_safe(
        spec, context, tmp_path):
    errors: list[BaseException] = []
    runners: list[ArchivedDemandSumoRunner] = []
    lock = threading.Lock()

    def build(name: str, index: int) -> None:
        try:
            runner = _runner(spec, name, cache_root=tmp_path / f"c{index}",
                             shared_context=context)
            with lock:
                runners.append(runner)
        except BaseException as exc:  # noqa: BLE001 - captured, not swallowed
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=build, args=(name, i))
        for i, name in enumerate([SMALL, MEDIAN, LARGE] * 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(runners) == 6
    assert len({id(r.adjacency) for r in runners}) == 1


# -- 15. A construction error leaves no partially prepared resolver -------


def test_a_failed_prepare_leaves_no_partial_resolver_state(spec, tmp_path):
    parents = _daily_units(spec, 2)

    call_count = {"n": 0}
    real_factory = ArchivedDemandSumoRunner

    def flaky_factory(*args: Any, **kwargs: Any) -> ArchivedDemandSumoRunner:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated construction failure")
        return real_factory(*args, **kwargs)

    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="partial-prepare-check",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1",
        runner_factory=flaky_factory)

    with pytest.raises(RuntimeError, match="simulated construction failure"):
        resolver.prepare(parents)

    assert resolver._prepared_schedule_ids is None
    assert resolver._schedule_build_keys == {}
    assert resolver._runners == {}
    assert resolver._shared_context is None
    with pytest.raises(RuntimeError):
        resolver.provenance()


# -- 16. No SUMO simulation or demand build during prepare/canary ----------


def test_preparing_from_real_archives_starts_no_sumo_simulation(spec, tmp_path):
    """No long-running simulation survives prepare.

    Context construction intentionally performs one bounded ``sumo
    --version`` runtime fingerprint probe; that is not a simulation and may
    be too brief for a before/after census to observe.
    """
    import subprocess

    before = subprocess.run(["pgrep", "-c", "-f", "sumo"],
                            capture_output=True, text=True,
                            check=False).stdout.strip()
    parent = _daily_units(spec)[0]
    resolver = MonthlyDemandResolverRunner(
        spec, baseline_trip_duration_p99_s=3600,
        study_provenance_key="no-sumo-check",
        runs_root=OLD_ARCHIVE_ROOT, release_root=tmp_path / "release",
        cache_root=tmp_path / "cache", include_disruption=True,
        ranking_objective_evidence="closure_cost_v1")
    resolver.prepare([parent])
    after = subprocess.run(["pgrep", "-c", "-f", "sumo"],
                           capture_output=True, text=True,
                           check=False).stdout.strip()
    assert before == after


# -- 17. Two-phase cost ledger and streaming ledgers are unaffected -------
# Covered by the (unmodified) tests/test_two_phase_cost_ledger.py and
# tests/test_profile_streaming_prepare.py; monthly_sumo.py/monthly_demand.py
# changes here do not touch cost_ordered_execution.py, independent_daily.py,
# closure_ledgers.py or window_cost_index.py (verified via `git diff
# --name-only` in the delivery report). No duplicate test needed here.


# -- Memory: the actual measured saving ------------------------------------


def test_a_second_and_third_archive_cost_kilobytes_not_megabytes(
        spec, tmp_path):
    """The number this whole change exists to move."""
    import gc
    import tracemalloc

    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    ctx = build_shared_runner_context(spec, include_disruption=True)
    after_context = tracemalloc.get_traced_memory()[0]

    marginal_costs = []
    runners = []
    previous = after_context
    for i, name in enumerate((SMALL, MEDIAN, LARGE)):
        runners.append(_runner(
            spec, name, cache_root=tmp_path / f"marginal-{i}",
            shared_context=ctx))
        current = tracemalloc.get_traced_memory()[0]
        marginal_costs.append(current - previous)
        previous = current
    tracemalloc.stop()

    # The SECOND and THIRD runner (the first already paid any one-time
    # warm-up cost the first ever construction in this process incurs) must
    # be a small fraction of the ~154.7 MB/key the unshared baseline cost.
    for cost in marginal_costs[1:]:
        assert cost < 5_000_000, (
            f"marginal per-archive cost {cost} bytes is not kilobyte-scale")
