"""Stage 3's teleport policy: the option, the provenance and the gate.

Stage 3 of docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md. Three properties
matter and each has cover here:

  * a caller that does not opt in gets a byte-identical SUMO command, because
    the warm/cold equivalence contract compares argv;
  * a closure run opts in and a baseline run does not, because the baseline's
    teleports are the only live integrity signal left in a paired study;
  * the gate is decided on the measured DIFFERENCE, and an unmeasured
    throughput fails it rather than passing quietly.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation import closure_teleport as ct


class TestPolicyValues:
    def test_disabled_is_the_deployed_closure_policy(self):
        assert ct.CLOSURE_TIME_TO_TELEPORT_S == -1
        assert ct.teleport_disabled(ct.CLOSURE_TIME_TO_TELEPORT_S)

    @pytest.mark.parametrize("value", [0, -2, 1.5, "300", True, None])
    def test_unusable_values_are_refused(self, value):
        if value is None:
            # None is legitimate — it means "do not pass the option at all".
            assert ct.sumo_arguments(None) == []
            return
        with pytest.raises(ct.ClosureTeleportPolicyError):
            ct.normalize_time_to_teleport(value)

    def test_zero_is_refused_because_sumo_reads_it_as_teleport_immediately(self):
        """A caller meaning "disabled" who types 0 would otherwise silently get
        the most aggressive relocation setting SUMO has."""
        with pytest.raises(ct.ClosureTeleportPolicyError):
            ct.normalize_time_to_teleport(0)

    def test_labels_distinguish_never_chosen_from_deliberately_kept(self):
        assert ct.policy_label(None) == "sumo_default_300s"
        assert ct.policy_label(-1) == "disabled"
        assert ct.policy_label(300) == "threshold_300s"

    def test_driver_give_up_threshold_is_independent_of_the_teleport_option(self):
        """MAX_CLOSURE_WAIT_S models the driver, not the simulator. It must not
        move when the teleport policy does, or truncation semantics would
        silently follow a SUMO option."""
        assert ct.MAX_CLOSURE_WAIT_S == 300
        assert ct.MAX_CLOSURE_WAIT_S != ct.CLOSURE_TIME_TO_TELEPORT_S


class TestArgv:
    def test_no_policy_emits_nothing(self):
        assert ct.sumo_arguments(None) == []

    def test_policy_emits_the_sumo_option(self):
        assert ct.sumo_arguments(-1) == ["--time-to-teleport", "-1"]
        assert ct.sumo_arguments(600) == ["--time-to-teleport", "600"]


class TestProvenance:
    def test_a_disabled_policy_marks_the_teleport_count_uninformative(self):
        """The whole reason the record travels with the run: once teleporting
        is off, `teleport_total == 0` is true by construction and says nothing
        about the run's health."""
        record = ct.policy_record(-1)
        assert record["teleporting_enabled"] is False
        assert record["teleport_count_is_informative"] is False

    def test_default_policy_keeps_the_teleport_count_informative(self):
        record = ct.policy_record(None)
        assert record["teleporting_enabled"] is True
        assert record["teleport_count_is_informative"] is True

    def test_record_carries_its_version(self):
        assert ct.policy_record(-1)["policy_version"] == ct.POLICY_VERSION


def _metrics(**overrides):
    base = {"unfinished_trips": 10, "dropped_unreachable": 2,
            "teleport_total": 0, "closed_edge_throughput": 0}
    base.update(overrides)
    return base


class TestStage3Gate:
    def test_passes_when_throughput_falls_to_zero_within_budget(self):
        before = _metrics(teleport_total=7, closed_edge_throughput=5)
        after = _metrics(unfinished_trips=13)      # +3 stuck, budget 4
        gate = ct.evaluate_stage3_gate(before=before, after=after,
                                       detour_less_vehicles=4)
        assert gate["passed"] is True
        assert gate["measured"]["stuck_growth"] == 3

    def test_fails_when_the_stuck_population_outgrows_the_detour_less_budget(self):
        """The plan's own limit: vehicles may stop arriving, but only the ones
        that genuinely have nowhere to go."""
        before = _metrics(teleport_total=7, closed_edge_throughput=5)
        after = _metrics(unfinished_trips=20)      # +10 stuck, budget 4
        gate = ct.evaluate_stage3_gate(before=before, after=after,
                                       detour_less_vehicles=4)
        assert gate["passed"] is False
        assert gate["checks"]["stuck_growth_within_detour_less"] is False

    def test_dropped_vehicles_count_toward_the_stuck_population(self):
        """A vehicle denied its departure did not arrive either; counting only
        unfinished trips would let the budget be evaded."""
        before = _metrics(teleport_total=7, closed_edge_throughput=5)
        after = _metrics(dropped_unreachable=12)   # +10 stuck, budget 1
        gate = ct.evaluate_stage3_gate(before=before, after=after,
                                       detour_less_vehicles=1)
        assert gate["passed"] is False

    def test_unmeasured_throughput_fails_rather_than_passing_quietly(self):
        """None is "we never looked", which is not the same statement as
        "we looked and it was zero" — and only the second clears a gate whose
        entire subject is a measurement."""
        before = _metrics(teleport_total=7, closed_edge_throughput=5)
        after = _metrics(closed_edge_throughput=None)
        gate = ct.evaluate_stage3_gate(before=before, after=after,
                                       detour_less_vehicles=99)
        assert gate["passed"] is False
        assert gate["checks"]["throughput_measured"] is False

    def test_a_surviving_teleport_fails_the_gate(self):
        after = _metrics(teleport_total=1)
        gate = ct.evaluate_stage3_gate(before=_metrics(), after=after,
                                       detour_less_vehicles=99)
        assert gate["passed"] is False
        assert gate["checks"]["no_teleports"] is False

    def test_a_negative_budget_is_refused(self):
        with pytest.raises(ct.ClosureTeleportPolicyError):
            ct.evaluate_stage3_gate(before=_metrics(), after=_metrics(),
                                    detour_less_vehicles=-1)

    def test_accepts_a_dataclass_shaped_record_too(self):
        from traffic_sim.simulation.metrics import DisruptionMetrics
        common = dict(total_time_loss_s=1.0, trip_count=5,
                      unfinished_waiting_trips=0, teleport_reasons={},
                      loaded=5, inserted=5, running_at_end=0, waiting_at_end=0)
        before = DisruptionMetrics(unfinished_trips=1, teleport_total=3,
                                   dropped_unreachable=0,
                                   closed_edge_throughput=4, **common)
        after = DisruptionMetrics(unfinished_trips=2, teleport_total=0,
                                  dropped_unreachable=0,
                                  closed_edge_throughput=0, **common)
        gate = ct.evaluate_stage3_gate(before=before, after=after,
                                       detour_less_vehicles=1)
        assert gate["passed"] is True
