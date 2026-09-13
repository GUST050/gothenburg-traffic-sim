"""Step 5 metric groups 5-7: assembly, costing and result identity.

Measurement only. The load-bearing assertions are the negative ones: an
instrumented run must produce byte-identical output, the same errors, and the
same ledger, winner and stop proof as an unmeasured one.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from demand import day_library as dl
from tools import profile_monthly_cost_ledger as monthly_profile
from traffic_sim.ops import io_phases


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _day(root, index, *, vehicles=2, compress=False, agents=None):
    """One stored day directory shaped like the writer produces."""
    day = Path(root) / f"day{index}"
    day.mkdir(parents=True, exist_ok=True)
    lines = ["<routes>\n"]
    for n in range(vehicles):
        lines.append(
            f'  <vehicle id="pfe{n}" depart="{n * 10.0:.1f}">'
            f'<route edges="a b"/></vehicle>\n')
    lines.append("</routes>\n")
    text = "".join(lines)
    payload = json.dumps({"schema_version": 1, "agents": [
        {"vehicle_id": f"pfe{n}", "departure_s": n * 10.0, "purpose": "arbete"}
        for n in range(vehicles if agents is None else agents)]})
    if compress:
        with gzip.open(day / "calibrated.rou.xml.gz", "wt") as handle:
            handle.write(text)
        with gzip.open(day / "calibrated.agents.json.gz", "wt") as handle:
            handle.write(payload)
    else:
        (day / "calibrated.rou.xml").write_text(text)
        (day / "calibrated.agents.json").write_text(payload)
    return day


class TestAssemblyAccountsForItself:
    def test_route_input_remains_streamed_while_measuring(self, tmp_path,
                                                          monkeypatch):
        """Instrumentation must not turn the route file into one large list."""
        days = [_day(tmp_path, 0, vehicles=3)]
        original = dl._open_day_text

        class NoReadlines(io.TextIOBase):
            def __init__(self, handle):
                self.handle = handle

            def __iter__(self):
                return iter(self.handle)

            def readlines(self, *args, **kwargs):
                raise AssertionError("route assembly must stay streaming")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

        def guarded(directory, name):
            handle = original(directory, name)
            return NoReadlines(handle) if name.endswith(".rou.xml") else handle

        monkeypatch.setattr(dl, "_open_day_text", guarded)
        with io_phases.observe(io_phases.PhaseCollector()):
            dl.assemble_window(days, tmp_path / "o.rou.xml",
                               tmp_path / "o.agents.json")

    def test_every_stage_is_a_separate_phase_with_bytes(self, tmp_path):
        days = [_day(tmp_path, i) for i in range(3)]
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            dl.assemble_window(days, tmp_path / "out.rou.xml",
                               tmp_path / "out.agents.json")

        report = collector.report()
        for name in ('assemble_route_read', 'assemble_route_transform',
                     'assemble_agents_json', 'assemble_route_publish',
                     'assemble_agents_publish'):
            assert name in report['phases'], f'{name} was not measured'
        assert report['counters']['assemble_days'] == 3
        assert report['counters']['assemble_route_rows'] == 6
        assert report['counters']['assemble_agents'] == 6
        assert report['phases']['assemble_route_read']['bytes']['read'] > 0
        assert report['phases']['assemble_route_publish']['bytes']['written'] > 0

    def test_the_report_carries_the_output_digests_and_totals(self, tmp_path):
        days = [_day(tmp_path, i) for i in range(2)]
        route, agents = tmp_path / "out.rou.xml", tmp_path / "out.agents.json"
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            result = dl.assemble_window(days, route, agents)

        report = collector.report()
        assert report['digests']['assembled_routes_sha256'] == _sha(route)
        assert report['digests']['assembled_agents_sha256'] == _sha(agents)
        assert result == {'vehicles': 4, 'days': 2}

    def test_gzipped_days_are_measured_the_same_way(self, tmp_path):
        days = [_day(tmp_path, i, compress=True) for i in range(2)]
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            dl.assemble_window(days, tmp_path / "o.rou.xml",
                               tmp_path / "o.agents.json")

        assert collector.report()['counters']['assemble_route_rows'] == 4

    def test_measuring_produces_byte_identical_output(self, tmp_path):
        plain_days = [_day(tmp_path / 'a', i) for i in range(3)]
        measured_days = [_day(tmp_path / 'b', i) for i in range(3)]
        plain = (tmp_path / 'p.rou.xml', tmp_path / 'p.agents.json')
        measured = (tmp_path / 'm.rou.xml', tmp_path / 'm.agents.json')

        dl.assemble_window(plain_days, *plain)
        with io_phases.observe(io_phases.PhaseCollector()):
            dl.assemble_window(measured_days, *measured)

        assert _sha(measured[0]) == _sha(plain[0])
        assert _sha(measured[1]) == _sha(plain[1])

    def test_the_day_offset_and_id_sequence_are_unchanged(self, tmp_path):
        days = [_day(tmp_path, i, vehicles=2) for i in range(2)]
        route = tmp_path / "out.rou.xml"

        with io_phases.observe(io_phases.PhaseCollector()):
            dl.assemble_window(days, route, tmp_path / "out.agents.json")

        text = route.read_text()
        assert 'id="pfe0" depart="0.0"' in text
        assert 'id="pfe2" depart="86400.0"' in text
        assert 'id="pfe3" depart="86410.0"' in text

    def test_a_route_agent_mismatch_still_raises_and_unwinds(self, tmp_path):
        days = [_day(tmp_path, 0, vehicles=2, agents=1)]

        with io_phases.observe(io_phases.PhaseCollector()):
            with pytest.raises(ValueError, match='disagree'):
                dl.assemble_window(days, tmp_path / "o.rou.xml",
                                   tmp_path / "o.agents.json")

        assert io_phases.current_collector() is None

    def test_production_installs_no_collector(self, tmp_path):
        days = [_day(tmp_path, 0)]

        dl.assemble_window(days, tmp_path / "o.rou.xml",
                           tmp_path / "o.agents.json")

        assert io_phases.current_collector() is None


class TestCostingAndTheResolverAccountForThemselves:
    """Metric group 6, on the existing hermetic ledger fixtures."""

    @staticmethod
    def _ledger(spec=None, schedules=None, source=None):
        from tests.test_cost_ordered_execution import (FakeCostSource, _spec,
                                                       generate_closure_schedules)
        from traffic_sim.simulation import cost_ordered_execution as coe

        spec = spec or _spec()
        schedules = schedules or generate_closure_schedules(spec)[:4]
        if source is None:
            # Price every candidate deterministically so the ledger, its
            # ordering and its winner are reproducible across runs.
            source = FakeCostSource({s.schedule_id: float(index + 1)
                                     for index, s in enumerate(schedules)})
        return coe.build_cost_ledger(spec, schedules, source)

    def test_the_ledger_counts_the_parent_candidates_it_prices(self):
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            ledger = self._ledger()

        counters = collector.report()['counters']
        assert counters['cost_parent_candidates'] == len(ledger.costs)

    def test_the_pricer_mirrors_its_own_cache_counters(self):
        """Read from the pricer, not recounted beside it.

        The hermetic ledger fixture uses a fake cost source and never reaches
        the real pricer, so asserting these through build_cost_ledger would
        pass while measuring nothing. They are taken where the pricer
        publishes them.
        """
        from tests.test_cost_ordered_execution import (
            FakeCostSource, _spec, generate_closure_schedules)
        from traffic_sim.simulation import cost_ordered_execution as coe

        spec = _spec()
        schedules = generate_closure_schedules(spec)[:2]
        source = FakeCostSource({schedule.schedule_id: float(index + 1)
                                 for index, schedule in enumerate(schedules)})
        source.cache_snapshot = lambda: {
            'memory_cache_hits': 3, 'memory_cache_misses': 2,
            'disk_cache_hits': 1, 'disk_cache_misses': 0,
        }
        source.population_snapshot = lambda: {'daily_units': 2}

        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            coe.build_cost_ledger(spec, schedules, source)

        counters = collector.report()['counters']
        assert counters['cost_daily_units'] == 2
        assert counters['cost_unit_cache_hits'] == 4
        assert counters['cost_unit_cache_misses'] == 2

    def test_the_resolver_counts_instances_calls_and_unique_routes(self):
        """Exercised directly: the hermetic ledger fixture uses a fake cost
        source and never constructs a resolver, so asserting through it would
        pass without measuring anything."""
        from traffic_sim.simulation import disruption

        adjacency = {'a': ('b',), 'b': ()}
        times = {'a': 1.0, 'b': 1.0}
        collector = io_phases.PhaseCollector()

        with monthly_profile._observe_resolver_activity(collector):
            first = disruption.ClosureRouteResolver(
                adjacency, times, None, frozenset({'b'}))
            second = disruption.ClosureRouteResolver(
                adjacency, times, None, frozenset({'a'}))
            first.resolve(['a', 'b'], 0.0, None, None)
            second.resolve(['a', 'b'], 0.0, None, None)
            first.resolve(['a'], 0.0, None, None)

        report = collector.report()
        assert report['counters']['closure_resolver_instances'] == 2
        assert report['counters']['closure_resolve_calls'] == 3
        assert report['unique_counts']['closure_route_edges'] == 2

    def test_equal_route_content_is_one_identity_across_distinct_objects(self):
        from traffic_sim.simulation import disruption

        adjacency = {'a': ('b',), 'b': ()}
        times = {'a': 1.0, 'b': 1.0}
        collector = io_phases.PhaseCollector()

        with monthly_profile._observe_resolver_activity(collector):
            disruption.ClosureRouteResolver(
                adjacency, times, None, frozenset({'b'}))
            disruption.ClosureRouteResolver(
                {'a': ('b',), 'b': ()}, dict(times), None,
                frozenset({'b'}))
            first = disruption.ClosureRouteResolver(
                adjacency, times, None, frozenset({'b'}))
            first.resolve(['a', 'b'], 0.0, None, None)
            first.resolve(tuple(['a', 'b']), 1.0, None, None)

        report = collector.report()
        assert report['counters']['closure_resolver_instances'] == 3
        assert report['unique_counts']['closure_route_edges'] == 1

    def test_provider_identity_binds_archive_network_and_schedule(
            self, tmp_path, monkeypatch):
        from tests.test_deterministic_disruption import (
            FakeNetwork, _archive, _schedule, _spec)
        from traffic_sim.simulation import deterministic_disruption as dd

        monkeypatch.setattr(dd, "costing_source_identity",
                            lambda: {"source.py": "digest"})
        spec = _spec()
        provider = dd.ArchiveDisruptionProvider(
            spec, archive=_archive(tmp_path / "archive"),
            network=FakeNetwork())
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            provider.cache_identity(_schedule(spec, start="06:00", end="07:00"))
            provider.cache_identity(_schedule(spec, start="06:00", end="07:00"))
            provider.cache_identity(_schedule(spec, start="08:00", end="09:00"))

        report = collector.report()
        assert report['counters']['costing_provider_identity_events'] == 3
        assert report['unique_counts']['costing_provider_identity'] == 2

    def test_monthly_resolver_reuses_verified_inputs_only_inside_its_operation(
            self, tmp_path, monkeypatch):
        from tests.test_deterministic_disruption import (
            FakeNetwork, _archive, _schedule, _spec)
        from traffic_sim.simulation import deterministic_disruption as dd
        from traffic_sim.simulation.monthly_demand import (
            MonthlyDemandResolverRunner)

        spec = _spec()
        schedule = _schedule(spec)
        archive = _archive(tmp_path / "archive")
        resolver = MonthlyDemandResolverRunner.__new__(
            MonthlyDemandResolverRunner)
        resolver.spec = spec
        resolver._archive_inputs = {}
        resolver._prepared_schedule_ids = (schedule.schedule_id,)
        resolver._schedule_build_keys = {schedule.schedule_id: "key"}
        resolver._runners = {"key": SimpleNamespace(archive=archive)}
        original = dd.ArchiveInputs.from_archive.__func__
        calls = []

        def counted(cls, path):
            calls.append(Path(path))
            return original(cls, path)

        monkeypatch.setattr(dd.ArchiveInputs, "from_archive",
                            classmethod(counted))
        first = resolver.deterministic_disruption_provider(
            schedule, network=FakeNetwork())
        second = resolver.deterministic_disruption_provider(
            schedule, network=FakeNetwork())

        assert first.inputs is second.inputs
        assert calls == [archive.resolve()]

    def test_production_installs_no_collector(self):
        self._ledger()

        assert io_phases.current_collector() is None


class TestMeasuringChangesNoCostResult:
    """Metric group 7: the observer may not move a single decision."""

    def test_the_ledger_winner_and_stop_proof_are_identical(self):
        plain = TestCostingAndTheResolverAccountForThemselves._ledger()
        with io_phases.observe(io_phases.PhaseCollector()):
            measured = TestCostingAndTheResolverAccountForThemselves._ledger()

        assert measured.to_dict() == plain.to_dict()

    def test_the_serialised_ledger_is_byte_identical(self):
        plain = TestCostingAndTheResolverAccountForThemselves._ledger()
        with io_phases.observe(io_phases.PhaseCollector()):
            measured = TestCostingAndTheResolverAccountForThemselves._ledger()

        def canonical(ledger):
            return json.dumps(ledger.to_dict(), sort_keys=True,
                              separators=(',', ':')).encode()

        assert hashlib.sha256(canonical(measured)).hexdigest() == \
            hashlib.sha256(canonical(plain)).hexdigest()

    def test_execution_winner_disqualifications_and_stop_proof_are_identical(
            self):
        from tests.test_cost_ordered_execution import (
            FakeCostSource, _ordered_prices, _policy, _spec, _variant_records)
        from traffic_sim.simulation import cost_ordered_execution as coe
        from traffic_sim.simulation.finalist_decision import (
            CandidateEvidence, PairedObservation)

        spec = _spec()
        schedules, prices = _ordered_prices(spec)
        schedules = schedules[:6]
        refused = schedules[2].schedule_id

        def execute(measured):
            source = FakeCostSource(prices, no_detour=(refused,))
            ledger = coe.build_cost_ledger(spec, schedules, source)

            def verify(candidate_id):
                return CandidateEvidence(
                    candidate_id=candidate_id,
                    observations=tuple(PairedObservation(
                        candidate_id=candidate_id,
                        demand_variant=variant,
                        seed=1000 + index,
                        baseline_time_loss_s=1.0,
                        candidate_time_loss_s=2.0,
                        matched_baseline_id="b",
                        provenance_key="p",
                    ) for index, variant in enumerate(
                        ("q10", "q50", "q90"))),
                    disruption=_variant_records(prices[candidate_id]),
                )

            result = coe.run_cost_ordered_execution(
                spec, ledger, _policy(minimum=2).pilot, verify=verify)
            record = coe.execution_record(
                spec, ledger, result,
                exhaustive_candidate_count=len(schedules))
            return ledger.to_dict(), result.to_dict(), record

        plain = execute(False)
        with io_phases.observe(io_phases.PhaseCollector()):
            measured = execute(True)

        assert measured == plain
        _ledger, result, record = measured
        assert result['selected_ids']
        assert result['disqualified'] == [{
            'candidate_id': refused,
            'vehicles_no_detour': 1,
            'added_vehicle_hours': prices[refused],
        }]
        assert result['stop_proof']['stop_reason'] == 'band_exhausted'
        assert record['provider_identity']
        assert record['cursor']['cursor'] == result['cursor']


class TestTheAssemblyMeasurementAccountsForItsOwnCost:
    """A profile that hides its own work misreports what it measures.

    The output digests are computed by the instrumentation, not by
    production. Measured on 12000 vehicles they were 45.8% of the observed
    run and appeared in no phase at all, so a reader saw 0.1147 s of phases
    for a run that took 0.2115 s and could not tell the difference.
    """

    def test_the_digest_work_is_a_named_phase_marked_as_measurement_only(
            self, tmp_path):
        days = [_day(tmp_path, i, vehicles=50) for i in range(2)]
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            dl.assemble_window(days, tmp_path / 'o.rou.xml',
                               tmp_path / 'o.agents.json')

        report = collector.report()
        assert 'assemble_output_digest' in report['phases']
        assert 'assemble_output_digest' in report['measurement_only_phases']

    def test_the_root_phase_publishes_a_residual(self, tmp_path):
        days = [_day(tmp_path, i, vehicles=50) for i in range(2)]
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            dl.assemble_window(days, tmp_path / 'o.rou.xml',
                               tmp_path / 'o.agents.json')

        report = collector.report(root='assemble_window')
        assert report['residual_s'] is not None
        assert 'assemble_window' in report['phases']

    def test_the_ranked_phases_account_for_the_whole_observed_region(
            self, tmp_path):
        days = [_day(tmp_path, i, vehicles=400) for i in range(3)]
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            dl.assemble_window(days, tmp_path / 'o.rou.xml',
                               tmp_path / 'o.agents.json')

        report = collector.report()
        root = report['phases']['assemble_window']['inclusive_s']
        ranked = sum(entry['wall_s'] for entry in report['ranking'])
        # Everything the observed region spent is now inside a named phase.
        # Every reported time is rounded to microseconds, so an identity over
        # the root plus its rows can be off by half a step per value; the
        # identity itself is exact.
        assert ranked == pytest.approx(root, abs=5e-6)

    def test_production_output_is_still_byte_identical(self, tmp_path):
        plain_days = [_day(tmp_path / 'a', i, vehicles=20) for i in range(2)]
        measured_days = [_day(tmp_path / 'b', i, vehicles=20) for i in range(2)]
        plain = (tmp_path / 'p.rou.xml', tmp_path / 'p.agents.json')
        measured = (tmp_path / 'm.rou.xml', tmp_path / 'm.agents.json')

        dl.assemble_window(plain_days, *plain)
        with io_phases.observe(io_phases.PhaseCollector()):
            dl.assemble_window(measured_days, *measured)

        assert _sha(measured[0]) == _sha(plain[0])
        assert _sha(measured[1]) == _sha(plain[1])
