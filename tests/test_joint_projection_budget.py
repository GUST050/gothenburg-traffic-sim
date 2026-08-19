"""An undecided solver must not be mistaken for an impossible problem.

The production joint projection fails CLOSED: rather than descend the
relaxation ladder it refuses to publish. That is right for a proven
infeasibility and wrong for a solver that merely ran out of wall clock —
and the limit IS wall clock, spent ten-wide in a fork pool, so how much CPU
a quarter receives depends on what else the machine is doing. The
2027-07-27 envelope died on "joint integer projection was not resolved"
during a closure search and then rebuilt cleanly, unchanged, on an idle
machine. Machine load decided whether evidence existed.

What is pinned here is the distinction: infeasible is answered at once,
undecided is retried with a real budget, and still-undecided still refuses.
"""
from __future__ import annotations

import numpy as np
import pytest

from traffic_sim.demand import pfe
from traffic_sim.demand.pfe import Candidate, repair_integer_bounds


def cand(*edges):
    return Candidate(depart=0.0, edges=list(edges))


class FakeResult:
    def __init__(self, status, x=None, message="fake"):
        self.status = status
        self.x = x
        self.success = status == 0
        self.message = message


@pytest.fixture
def recorder(monkeypatch):
    """Replace HiGHS with a scripted sequence, recording each time limit."""
    calls: list[float] = []

    def install(results):
        sequence = list(results)

        def fake_milp(*_args, **kwargs):
            calls.append(kwargs["options"]["time_limit"])
            return sequence.pop(0) if sequence else FakeResult(1)

        monkeypatch.setattr(pfe, "milp", fake_milp)
        return calls

    return install


def forced(**overrides):
    """One forced production projection: two routes over one measured edge."""
    kwargs = dict(
        counts=np.array([6, 4]),
        shapes=[cand("m"), cand("m")],
        measured={"m": 10.0},
        bounds={},
        reference=np.array([6.0, 4.0]),
        preserve_total=True,
        force=True,
    )
    kwargs.update(overrides)
    return kwargs


class TestTheBudget:
    def test_a_decided_problem_is_solved_once(self, recorder):
        calls = recorder([FakeResult(0, x=np.array([6.0, 4.0, 0, 0, 0, 0]))])
        result = repair_integer_bounds(**forced())
        assert result is not None
        assert calls == [pfe.JOINT_PROJECTION_TIME_LIMIT_S], (
            "a solver that answered must not be asked again")

    def test_a_proven_infeasibility_is_not_retried(self, recorder):
        # Status 2 is the one answer that means the model itself is
        # impossible. Retrying it would only burn three more minutes per
        # quarter to hear the same thing.
        calls = recorder([FakeResult(pfe.HIGHS_STATUS_INFEASIBLE)])
        assert repair_integer_bounds(**forced()) is None
        assert calls == [pfe.JOINT_PROJECTION_TIME_LIMIT_S]

    def test_an_undecided_problem_is_retried_with_a_real_budget(self, recorder):
        calls = recorder([FakeResult(1),
                          FakeResult(0, x=np.array([6.0, 4.0, 0, 0, 0, 0]))])
        result = repair_integer_bounds(**forced())
        assert result is not None, (
            "a quarter that only needed more time must still publish")
        assert calls == [pfe.JOINT_PROJECTION_TIME_LIMIT_S,
                         pfe.JOINT_PROJECTION_RETRY_LIMIT_S]

    def test_the_retry_budget_is_larger_than_the_first(self):
        assert pfe.JOINT_PROJECTION_RETRY_LIMIT_S > \
            pfe.JOINT_PROJECTION_TIME_LIMIT_S

    def test_still_undecided_after_the_retry_still_refuses(self, recorder):
        recorder([FakeResult(1), FakeResult(1)])
        with pytest.raises(RuntimeError, match="was not resolved"):
            repair_integer_bounds(**forced())

    def test_the_refusal_names_the_status_and_both_budgets(self, recorder):
        recorder([FakeResult(4, message="numerical trouble"),
                  FakeResult(4, message="numerical trouble")])
        with pytest.raises(RuntimeError) as raised:
            repair_integer_bounds(**forced())
        message = str(raised.value)
        assert "HiGHS status 4" in message
        assert "numerical trouble" in message
        assert f"{pfe.JOINT_PROJECTION_TIME_LIMIT_S:g}s then" in message
        assert f"{pfe.JOINT_PROJECTION_RETRY_LIMIT_S:g}s" in message
        assert "route variables" in message and "constraints" in message


class TestTheUnforcedPath:
    """Best-effort repairs keep their historical fast failure."""

    def test_an_unforced_repair_is_never_retried(self, recorder):
        # A violated group is what drives a best-effort caller into the MILP
        # at all; without one it never reaches the solver.
        calls = recorder([FakeResult(1)])
        assert repair_integer_bounds(
            **forced(force=False, groups=[([0], 0.0, 3.0)])) is None
        assert calls == [pfe.JOINT_PROJECTION_TIME_LIMIT_S], (
            "a best-effort caller has a cheaper answer than three minutes "
            "of solver: give up and let the ladder continue")

    def test_a_repair_without_a_reference_is_never_retried(self, recorder):
        calls = recorder([FakeResult(1)])
        assert repair_integer_bounds(**forced(reference=None,
                                              preserve_total=False)) is None
        assert calls == [pfe.JOINT_PROJECTION_TIME_LIMIT_S]


class TestTheRealSolver:
    """The scripted tests above must not drift from the actual model."""

    def test_a_real_forced_projection_still_publishes(self):
        result = repair_integer_bounds(**forced())
        assert result is not None
        assert result.sum() == 10, "the measured total must survive repair"

    def test_a_real_impossible_projection_returns_none_without_raising(self):
        # One route serving the edge cannot satisfy a cap of 3 at a measured
        # 10: genuinely infeasible, and HiGHS proves it rather than timing out.
        assert repair_integer_bounds(
            counts=np.array([10]), shapes=[cand("m")], measured={"m": 10.0},
            bounds={}, groups=[([0], 0.0, 3.0)],
            reference=np.array([10.0]), preserve_total=True, force=True,
        ) is None
