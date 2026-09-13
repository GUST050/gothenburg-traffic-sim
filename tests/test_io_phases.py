"""Diagnostic I/O phase accounting for step 4.

Step 3 measured a solve. Step 4 measures bytes: reading, compressing,
writing, hashing, verifying and publishing evidence. The rules that make
such a measurement trustworthy are the ones under test here -- a phase must
not count its children twice, concurrent children must not be added as if
they ran in sequence, and the observer must never survive the call that
installed it.
"""
import threading
import time

import pytest

from traffic_sim.ops import io_phases

# The report rounds every time to microseconds, so an identity over three
# reported values can be off by up to 1.5 rounding steps. The identity itself
# is exact; only its printed resolution is not.
ROUNDING_TOLERANCE_S = 2e-6


def _run_together(worker, count):
    """Run one worker in several real threads at the same time."""
    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)


def test_no_observer_means_no_work_and_no_state():
    with io_phases.phase('unobserved'):
        pass

    assert io_phases.current_collector() is None


def test_a_single_phase_reports_its_own_wall_time():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with io_phases.phase('read'):
            pass

    report = collector.report()
    assert report['phases']['read']['calls'] == 1
    assert report['phases']['read']['inclusive_s'] >= 0
    assert report['phases']['read']['exclusive_s'] >= 0


def test_a_sequential_parent_excludes_its_children():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with io_phases.phase('parent'):
            with io_phases.phase('child'):
                time.sleep(0.02)
            time.sleep(0.02)

    phases = collector.report()['phases']
    assert phases['parent']['inclusive_s'] >= phases['child']['inclusive_s']
    assert phases['parent']['exclusive_s'] == pytest.approx(
        phases['parent']['inclusive_s'] - phases['child']['inclusive_s'],
        abs=ROUNDING_TOLERANCE_S)


def test_two_children_are_both_subtracted_from_one_parent():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with io_phases.phase('parent'):
            for _ in range(2):
                with io_phases.phase('child'):
                    time.sleep(0.01)

    phases = collector.report()['phases']
    assert phases['child']['calls'] == 2
    assert phases['parent']['exclusive_s'] == pytest.approx(
        phases['parent']['inclusive_s'] - phases['child']['inclusive_s'],
        abs=ROUNDING_TOLERANCE_S)


def test_a_repeated_phase_accumulates_rather_than_overwriting():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        for _ in range(3):
            with io_phases.phase('hash'):
                time.sleep(0.005)

    entry = collector.report()['phases']['hash']
    assert entry['calls'] == 3
    assert entry['inclusive_s'] >= 0.015


def test_a_concurrent_parent_refuses_to_pretend_its_children_were_sequential():
    collector = io_phases.PhaseCollector()

    def worker():
        with io_phases.phase('compress'):
            time.sleep(0.05)

    with io_phases.observe(collector):
        with io_phases.phase('retention', concurrent=True):
            _run_together(io_phases.in_current_context(worker), 3)

    entry = collector.report()['phases']['retention']
    assert entry['exclusive_s'] is None
    assert entry['concurrent'] is True
    assert entry['children_sum_s'] == pytest.approx(
        collector.report()['phases']['compress']['inclusive_s'],
        abs=ROUNDING_TOLERANCE_S)
    assert entry['children_max_s'] <= entry['children_sum_s']
    assert entry['children_max_s'] >= 0.05
    # The honest bound: three 50 ms children cannot have cost 150 ms of wall
    # time on a parent that ran them at the same time.
    assert entry['inclusive_s'] < entry['children_sum_s']


def test_children_started_in_worker_threads_still_find_their_parent():
    collector = io_phases.PhaseCollector()

    def worker():
        with io_phases.phase('compress'):
            pass

    with io_phases.observe(collector):
        with io_phases.phase('retention', concurrent=True):
            _run_together(io_phases.in_current_context(worker), 2)

    report = collector.report()
    assert report['phases']['compress']['calls'] == 2
    assert report['phases']['compress']['parent'] == 'retention'


def test_bytes_are_attributed_to_the_phase_that_moved_them():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with io_phases.phase('read'):
            io_phases.add_bytes(read=1024)
            io_phases.add_bytes(read=1024)
        with io_phases.phase('write'):
            io_phases.add_bytes(written=512)

    phases = collector.report()['phases']
    assert phases['read']['bytes'] == {'read': 2048}
    assert phases['write']['bytes'] == {'written': 512}


def test_the_observer_is_removed_after_an_exception():
    collector = io_phases.PhaseCollector()

    with pytest.raises(RuntimeError):
        with io_phases.observe(collector):
            raise RuntimeError('boom')

    assert io_phases.current_collector() is None


def test_a_phase_that_raises_is_still_recorded_and_unwinds_the_stack():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with pytest.raises(ValueError):
            with io_phases.phase('parent'):
                with io_phases.phase('child'):
                    raise ValueError('inner')
        with io_phases.phase('after'):
            pass

    phases = collector.report()['phases']
    assert phases['child']['calls'] == 1
    assert phases['after']['parent'] is None


def test_two_concurrent_observers_do_not_share_a_collector():
    first, second = io_phases.PhaseCollector(), io_phases.PhaseCollector()
    first_entered = threading.Event()
    second_entered = threading.Event()
    first_exited = threading.Event()

    def run_first():
        with io_phases.observe(first):
            first_entered.set()
            assert second_entered.wait(2)
            with io_phases.phase('only-first'):
                pass
        first_exited.set()

    def run_second():
        assert first_entered.wait(2)
        with io_phases.observe(second):
            second_entered.set()
            assert first_exited.wait(2)
            with io_phases.phase('only-second'):
                pass

    threads = [threading.Thread(target=run_first),
               threading.Thread(target=run_second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)

    assert set(first.report()['phases']) == {'only-first'}
    assert set(second.report()['phases']) == {'only-second'}
    assert io_phases.current_collector() is None


def test_the_report_states_its_own_residual_and_unmeasured_categories():
    collector = io_phases.PhaseCollector(
        unmeasured_categories=['SUMO subprocess time'])

    with io_phases.observe(collector):
        with io_phases.phase('root'):
            with io_phases.phase('child'):
                time.sleep(0.01)
            time.sleep(0.01)

    report = collector.report(root='root')
    assert report['root'] == 'root'
    assert report['residual_s'] == pytest.approx(
        report['phases']['root']['exclusive_s'], abs=ROUNDING_TOLERANCE_S)
    assert report['unmeasured_categories'] == ['SUMO subprocess time']


def test_phases_are_ranked_by_measured_wall_share():
    collector = io_phases.PhaseCollector()

    with io_phases.observe(collector):
        with io_phases.phase('slow'):
            time.sleep(0.03)
        with io_phases.phase('fast'):
            time.sleep(0.005)

    ranking = collector.report()['ranking']
    assert [entry['phase'] for entry in ranking[:2]] == ['slow', 'fast']
    assert ranking[0]['share_percent'] > ranking[1]['share_percent']
