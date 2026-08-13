"""Product activation checks for validated monthly SUMO warming."""

from __future__ import annotations

import sys

import pytest

import run_monthly_closure_search as command
from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner


def _arguments(monkeypatch, *extra):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_monthly_closure_search.py",
            "--spec", "spec.json",
            "--policy", "policy.json",
            "--baseline-trip-duration-p99-s", "1800",
            *extra,
        ],
    )
    return command.parse_args()


def test_monthly_command_enables_warming_by_default(monkeypatch):
    args = _arguments(monkeypatch)
    assert args.warm_execution is True
    assert args.daily_workers == 3


def test_monthly_command_has_an_explicit_cold_escape_hatch(monkeypatch):
    assert _arguments(monkeypatch, "--cold-execution").warm_execution is False


@pytest.mark.parametrize("mode", ["proxy", "bounded-exhaustive"])
def test_daily_unit_budget_is_refused_for_modes_that_cannot_use_it(
        monkeypatch, mode):
    args = _arguments(
        monkeypatch,
        "--screening-mode", mode,
        "--daily-unit-budget", "100",
    )
    monkeypatch.setattr(command, "parse_args", lambda: args)
    with pytest.raises(SystemExit, match="requires --screening-mode"):
        command.main()


def test_parallel_warm_connections_are_refused_before_any_run(monkeypatch):
    args = _arguments(monkeypatch, "--seed-workers", "3")
    monkeypatch.setattr(command, "parse_args", lambda: args)
    with pytest.raises(SystemExit, match="requires --seed-workers 1"):
        command.main()


def test_resolver_requires_a_controller_when_warming_is_enabled():
    from tests.test_monthly_demand import _spec

    with pytest.raises(ValueError, match="requires an explicit boundary"):
        MonthlyDemandResolverRunner(
            _spec(),
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            warm_execution=True,
        )


def test_resolver_forwards_warm_activation_to_every_child(tmp_path):
    from tests.test_monthly_demand import (
        FakeChildRunner,
        _archive,
        _spec,
    )
    from traffic_sim.core.closure_calendar import generate_closure_schedules

    FakeChildRunner.created = []
    spec = _spec(end_date="2027-07-15")
    schedules = generate_closure_schedules(spec)
    controller = object()
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        warm_execution=True,
        boundary_controller=controller,
        runner_factory=FakeChildRunner,
    )
    required = resolver._required(schedules[0])
    _archive(
        tmp_path,
        required,
        "demand-shared",
        finished_at="2027-01-01T00:00:00Z",
    )

    resolver.prepare(schedules)

    assert len(FakeChildRunner.created) == 1
    options = FakeChildRunner.created[0].options
    assert options["warm_execution"] is True
    assert options["boundary_controller"] is controller
