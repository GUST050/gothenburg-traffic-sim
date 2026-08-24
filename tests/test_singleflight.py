"""Cross-process content-key single-flight contracts."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import time

import pytest

from traffic_sim.storage.singleflight import content_key_lock


def _hold(lock_root: str, start, events, worker: int) -> None:
    start.wait(timeout=5.0)
    with content_key_lock(Path(lock_root), "same-content-key"):
        events.put(("enter", worker))
        time.sleep(0.05)
        events.put(("exit", worker))


def _hold_until_released(lock_root: str, entered, release) -> None:
    with content_key_lock(Path(lock_root), "blocked-content-key"):
        entered.set()
        release.wait(timeout=5.0)


def test_content_key_lock_serializes_spawned_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    events = context.Queue()
    workers = [
        context.Process(
            target=_hold,
            args=(str(tmp_path), start, events, worker),
        )
        for worker in range(3)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10.0)
        assert worker.exitcode == 0

    active = maximum = 0
    for _ in range(2 * len(workers)):
        event, _worker = events.get(timeout=2.0)
        active += 1 if event == "enter" else -1
        maximum = max(maximum, active)
        assert active >= 0
    assert active == 0
    assert maximum == 1


@pytest.mark.parametrize("key", ["../escape", "has/slash", "", "x" * 129])
def test_content_key_lock_rejects_unsafe_keys(tmp_path, key):
    with pytest.raises(ValueError, match="filesystem-safe"):
        with content_key_lock(tmp_path, key):
            pass


def test_content_key_lock_reports_and_times_out_on_a_hung_producer(
        tmp_path, capsys):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_until_released,
        args=(str(tmp_path), entered, release),
    )
    holder.start()
    assert entered.wait(timeout=3.0)
    try:
        with pytest.raises(TimeoutError, match="blocked-content-key"):
            with content_key_lock(
                    tmp_path, "blocked-content-key",
                    timeout_s=0.08, poll_s=0.01):
                pass
        assert "waiting for another producer" in capsys.readouterr().err
    finally:
        release.set()
        holder.join(timeout=3.0)
    assert holder.exitcode == 0


@pytest.mark.parametrize("field", ["timeout_s", "poll_s"])
def test_content_key_lock_rejects_non_positive_wait_controls(tmp_path, field):
    options = {"timeout_s": 1.0, "poll_s": 0.01, field: 0.0}
    with pytest.raises(ValueError, match="must be positive"):
        with content_key_lock(tmp_path, "safe", **options):
            pass
