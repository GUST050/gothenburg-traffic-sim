"""Lost access is an impact of a closure, not a failure of the run.

WHY (2026-08-06). `dropped_unreachable` and `truncated_unreachable` were hard,
zero-tolerance disqualifiers. Because truncate_stranded_vehicles drops a
vehicle when the closed edge is its FIRST one, that made every edge trips
depart from permanently uncloseable — and busy streets have the most
departures, so the tool refused to answer exactly where the answer matters.

Measured over the v9 campaign's 75 schedules: the one case that produced a
usable ranking was the one case with zero unreachability, the worst dropped 85
vehicles with zero truncations (pure origin-drops), and 21 of the 61
disqualified schedules carried no teleports and no closed-edge throughput at
all — they failed on this alone.

The split is between an ANSWER and an ARTIFACT. A car that cannot start from a
closed street is part of what the closure costs. A vehicle teleported through
the closure, or throughput recorded on a closed edge, is the simulator doing
something a real closure does not.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation import metrics as cm


def _metrics(**overrides):
    base = dict(
        total_time_loss_s=100.0, trip_count=10, unfinished_trips=0,
        unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
        loaded=10, inserted=10, running_at_end=0, waiting_at_end=0,
    )
    base.update(overrides)
    return cm.DisruptionMetrics(**base)


class TestAccessLossIsNotAFailure:
    def test_denied_departures_do_not_disqualify(self):
        """The v9 killer: 85 vehicles whose trips start on the closed edge."""
        m = _metrics(dropped_unreachable=85)
        assert cm.disqualification_reasons(m) == ()
        assert not cm.is_disqualified(m)

    def test_trips_ending_short_do_not_disqualify(self):
        m = _metrics(truncated_unreachable=34)
        assert cm.disqualification_reasons(m) == ()
        assert not cm.is_disqualified(m)

    def test_access_loss_is_still_reported(self):
        """Not disqualifying must not mean not counting."""
        m = _metrics(dropped_unreachable=85, truncated_unreachable=34)
        assert cm.access_impact_reasons(m) == (
            "dropped_unreachable_vehicles", "truncated_unreachable_vehicles")
        assert m.stranded_unreachable == 119

    def test_a_clean_closure_reports_no_impact(self):
        m = _metrics()
        assert cm.access_impact_reasons(m) == ()


class TestArtifactsStillDisqualify:
    """The integrity gate keeps its teeth for things a real closure cannot do."""

    def test_teleports_still_disqualify(self):
        m = _metrics(teleport_total=1)
        assert "teleports" in cm.disqualification_reasons(m)
        assert cm.is_disqualified(m)

    def test_closed_edge_throughput_still_disqualifies(self):
        m = _metrics(closed_edge_throughput=3)
        assert "active_closure_edge_throughput" in cm.disqualification_reasons(m)
        assert cm.is_disqualified(m)

    def test_measured_zero_throughput_is_not_a_leak(self):
        """0 means measured and clean; None means never measured. Neither is
        a leak, and they must not be conflated."""
        assert cm.disqualification_reasons(_metrics(closed_edge_throughput=0)) == ()
        assert cm.disqualification_reasons(
            _metrics(closed_edge_throughput=None)) == ()

    def test_an_artifact_disqualifies_even_beside_access_loss(self):
        """Reclassifying access loss must not let a real artifact through."""
        m = _metrics(dropped_unreachable=85, teleport_total=2)
        assert cm.disqualification_reasons(m) == ("teleports",)
        assert cm.is_disqualified(m)
        assert "dropped_unreachable_vehicles" in cm.access_impact_reasons(m)


class TestTheV9CaseBecomesRankable:
    """The concrete regression: v9-discriminating-tertiary-b's profile was 85
    dropped, 0 truncated, no teleports, no throughput — disqualified on 15 of
    15 schedules. Under the split it is eligible and its impact is visible."""

    def test_the_profile_that_killed_v9_is_now_eligible(self):
        m = _metrics(dropped_unreachable=85, truncated_unreachable=0,
                     teleport_total=0, closed_edge_throughput=0)
        assert not cm.is_disqualified(m)
        assert cm.access_impact_reasons(m) == ("dropped_unreachable_vehicles",)
