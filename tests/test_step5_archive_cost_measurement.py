"""Step 5: measure how often archives are read, validated and hashed.

Measurement only. Nothing here may change a cache identity, an archive, a
score, a writer result or a stop decision -- the tests that matter most are
the ones proving exactly that, and the one proving a content change with a
preserved size and restored mtime is still caught.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from traffic_sim.ops import io_phases


def _valid_archive(tmp_path):
    """One succeeded demand archive that passes the real contract."""
    from tests.test_monthly_demand import (FakeChildRunner, _archive, _spec,
                                           generate_closure_schedules,
                                           MonthlyDemandResolverRunner)

    schedules = generate_closure_schedules(_spec(end_date="2027-07-15"))
    resolver = MonthlyDemandResolverRunner(
        _spec(end_date="2027-07-15"),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    required = resolver._required(schedules[0])
    archive = _archive(tmp_path, required, "demand-step5",
                       finished_at="2027-01-01T00:00:00Z")
    return archive, required


class TestTheCollectorCountsEventsNotOnlyTime:
    def test_counters_are_absent_until_something_counts(self):
        collector = io_phases.PhaseCollector()

        assert collector.report()['counters'] == {}

    def test_counting_is_inert_without_an_observer(self):
        io_phases.count('archive_validate')

        assert io_phases.current_collector() is None

    def test_counters_accumulate_and_carry_their_own_names(self):
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            io_phases.count('archive_validate')
            io_phases.count('archive_validate', 2)
            io_phases.count('archive_json_read')

        assert collector.report()['counters'] == {
            'archive_validate': 3, 'archive_json_read': 1}

    def test_unique_values_are_recorded_as_sets_not_totals(self):
        collector = io_phases.PhaseCollector()

        with io_phases.observe(collector):
            io_phases.count_unique('archive_path', '/a')
            io_phases.count_unique('archive_path', '/a')
            io_phases.count_unique('archive_path', '/b')

        report = collector.report()
        assert report['unique_counts'] == {'archive_path': 2}
        assert report['counters']['archive_path_events'] == 3


class TestArchiveValidationIsCounted:
    @pytest.fixture
    def archive(self, tmp_path):
        return _valid_archive(tmp_path)

    def test_one_validation_counts_its_reads_hashes_and_stats(self, archive):
        from traffic_sim.simulation import monthly_demand as md

        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            md.validate_demand_archive(archive[0], archive[1])

        report = collector.report()
        assert report['counters']['archive_validate'] == 1
        assert report['counters']['archive_json_read'] >= 3
        assert report['counters']['archive_sha256'] >= 1
        assert report['phases']['archive_sha256']['bytes']['hashed'] > 0
        assert report['unique_counts']['archive_validated_path'] == 1

    def test_validating_twice_is_counted_twice(self, archive):
        from traffic_sim.simulation import monthly_demand as md

        collector = io_phases.PhaseCollector()
        with io_phases.observe(collector):
            md.validate_demand_archive(archive[0], archive[1])
            md.validate_demand_archive(archive[0], archive[1])

        report = collector.report()
        assert report['counters']['archive_validate'] == 2
        assert report['unique_counts']['archive_validated_path'] == 1

    def test_measuring_returns_the_same_validation_result(self, archive):
        from traffic_sim.simulation import monthly_demand as md

        plain = md.validate_demand_archive(archive[0], archive[1])
        with io_phases.observe(io_phases.PhaseCollector()):
            measured = md.validate_demand_archive(archive[0], archive[1])

        assert measured == plain

    def test_production_installs_no_collector(self, archive):
        from traffic_sim.simulation import monthly_demand as md

        md.validate_demand_archive(archive[0], archive[1])

        assert io_phases.current_collector() is None


class TestContentChangesSurviveStatPreservation:
    """mtime and size are not a content proof."""

    def test_a_same_size_edit_with_a_restored_mtime_is_still_refused(
            self, tmp_path):
        from traffic_sim.simulation import monthly_demand as md

        archive, spec = _valid_archive(tmp_path)
        md.validate_demand_archive(archive, spec)

        target = archive / 'demand_meta.json'
        before = target.stat()
        original = target.read_bytes()
        edited = bytearray(original)
        # Flip one byte in the middle, keeping the length identical. The
        # point is the stat information, not which field moved.
        index = len(original) // 2
        edited[index] = original[index] ^ 0x01
        assert len(edited) == len(original)
        target.write_bytes(bytes(edited))
        os.utime(target, (before.st_atime, before.st_mtime))
        assert target.stat().st_size == before.st_size
        assert target.stat().st_mtime == before.st_mtime

        with pytest.raises(ValueError):
            md.validate_demand_archive(archive, spec)


class TestTheWarmDiscoveryCacheIsNotFooledByStatInformation:
    """find_demand_archives reuses a state keyed on path, size and mtime.

    That is the exact shape the plan warns about: if a content change with a
    preserved size and a restored mtime survived it, a stale archive could be
    selected without a single byte being re-read.
    """

    def test_a_content_change_under_preserved_stat_is_not_served_from_cache(
            self, tmp_path):
        from traffic_sim.simulation import monthly_demand as md

        archive, required = _valid_archive(tmp_path)
        first = md.find_demand_archives(tmp_path, required)
        assert first, 'the fixture archive must qualify before tampering'

        target = archive / 'calibrated.rou.xml'
        before = target.stat()
        original = target.read_bytes()
        edited = bytearray(original)
        index = len(original) // 2
        edited[index] = original[index] ^ 0x01
        assert len(edited) == len(original)
        target.write_bytes(bytes(edited))
        os.utime(target, (before.st_atime, before.st_mtime))
        assert target.stat().st_size == before.st_size
        assert target.stat().st_mtime == before.st_mtime

        again = md.find_demand_archives(tmp_path, required)

        assert not again, (
            'a tampered archive was still offered: the warm state was reused '
            'although the bytes changed under identical stat information')
