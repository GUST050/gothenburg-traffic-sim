"""Synthetic SUMO outputs for the shared disruption scorecard."""

from pathlib import Path

from closure_metrics import (DisruptionMetrics, build_metrics, compare_metrics,
                             active_closure_throughput, disqualification_reasons,
                             is_disqualified, read_closed_edge_throughput,
                             read_statistics, read_summary_max_queue, read_tripinfo)


def _write_fixture_outputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text("""<tripinfos>
  <tripinfo id="finished" arrival="120.0" timeLoss="12.5" waitingTime="3.0"/>
  <tripinfo id="unfinished_waiting" arrival="-1" timeLoss="7.5" waitingTime="6.0"/>
  <tripinfo id="unfinished_moving" timeLoss="2.0" waitingTime="0.0"/>
</tripinfos>""")
    statistics = tmp_path / "statistics.xml"
    statistics.write_text("""<statistics>
  <vehicles loaded="5" inserted="4" running="1" waiting="1"/>
  <teleports total="2" jam="1" yield="1" wrongLane="0"/>
</statistics>""")
    edgedata = tmp_path / "edgedata.xml"
    edgedata.write_text("""<meandata>
  <interval><edge id="closed" entered="3"/><edge id="other" entered="8"/></interval>
  <interval><edge id="closed" entered="4"/></interval>
</meandata>""")
    summary = tmp_path / "summary.xml"
    summary.write_text("""<summary>
  <step time="0" halting="2"/><step time="900" halting="9"/>
</summary>""")
    return tripinfo, statistics, edgedata, summary


def test_extract_metrics_from_sumo_xml_fixtures(tmp_path):
    tripinfo, statistics, edgedata, summary = _write_fixture_outputs(tmp_path)

    trips = read_tripinfo(tripinfo)
    stats = read_statistics(statistics)
    metrics = build_metrics(
        tripinfo, statistics, truncated_unreachable=2,
        dropped_unreachable=1,
        summary_path=summary,
        closed_edge_throughput=read_closed_edge_throughput(edgedata, {"closed"}),
    )

    assert trips.trip_count == 3
    assert trips.total_time_loss_s == 22.0
    assert trips.unfinished_trips == 2
    assert trips.unfinished_waiting_trips == 1
    assert stats.teleport_total == 2
    assert stats.teleport_reasons == {"jam": 1, "yield": 1, "wrongLane": 0}
    assert metrics.truncated_unreachable == 2
    assert metrics.dropped_unreachable == 1
    assert metrics.stranded_unreachable == 3
    assert metrics.stranded_waiting == 1
    assert metrics.closed_edge_throughput == 7
    assert read_summary_max_queue(summary) == 9
    assert metrics.max_queue_vehicles == 9  # diagnostic proxy, never a score


def test_lower_time_loss_with_teleports_or_drops_is_disqualified():
    baseline = DisruptionMetrics(
        total_time_loss_s=100.0, trip_count=10, unfinished_trips=0,
        unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
        loaded=10, inserted=10, running_at_end=0, waiting_at_end=0,
    )
    candidate = DisruptionMetrics(
        total_time_loss_s=50.0, trip_count=9, unfinished_trips=0,
        unfinished_waiting_trips=0, teleport_total=1, teleport_reasons={"jam": 1},
        loaded=10, inserted=9, running_at_end=0, waiting_at_end=0,
        dropped_unreachable=1,
    )

    comparison = compare_metrics(baseline, candidate)

    assert comparison.delta_time_loss_s == -50.0
    assert is_disqualified(candidate)
    assert comparison.candidate_disqualified
    # 2026-08-06: a denied departure is an IMPACT of the closure, not an
    # artifact of the run, so it no longer disqualifies -- the teleport still
    # does. See metrics.access_impact_reasons and
    # tests/test_closure_access_impact.py. What this test protects, that a
    # lower time loss bought by losing access cannot win, now lives in the
    # RANKING: closure_disruption counts a denied departure as
    # vehicles_no_detour, which closure_ranking disqualifies on.
    assert comparison.disqualification_reasons == ("teleports",)


def test_active_closure_throughput_uses_only_complete_active_quarters():
    flows = {"closed": [4, 5, 6, 7]}
    # 850-2750 overlaps quarters 0 and 3, but only 1 and 2 are fully active.
    assert active_closure_throughput(
        flows, [{"edge_id": "closed", "begin_s": 850, "end_s": 2750}]) == 11


def test_active_closure_throughput_indexes_relative_to_a_trimmed_window():
    # Review finding 2 (2026-08-30): a trimmed independent-daily cold window
    # starts SUMO (and therefore `flows`' own array index 0) at a nonzero
    # absolute second, while `closures[*].begin_s`/`end_s` stay absolute
    # (epoch-relative). Without `window_begin_s` the closure's absolute
    # quarters index past the end of a short trimmed `flows` array, so
    # `measured` never turns True and a genuinely clean closure reads as
    # `None` ("never measured") instead of a proven `0` -- reproduced live
    # replaying daily-unit-24737391111be0e137537df7 before this fix.
    flows = {"closed": [0, 0, 0, 0]}  # a 4-quarter window starting at 26100s
    closures = [{"edge_id": "closed", "begin_s": 26100, "end_s": 29700}]
    assert active_closure_throughput(flows, closures) is None  # unfixed call
    assert active_closure_throughput(
        flows, closures, window_begin_s=26100) == 0


def test_active_closure_throughput_window_begin_s_still_counts_real_flow():
    flows = {"closed": [0, 3, 5, 0]}
    closures = [{"edge_id": "closed", "begin_s": 26100, "end_s": 29700}]
    assert active_closure_throughput(
        flows, closures, window_begin_s=26100) == 8


def test_active_closure_flow_is_an_integrity_disqualification():
    metrics = DisruptionMetrics(
        total_time_loss_s=1.0, trip_count=1, unfinished_trips=0,
        unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
        loaded=1, inserted=1, running_at_end=0, waiting_at_end=0,
        closed_edge_throughput=1,
    )
    assert "active_closure_edge_throughput" in disqualification_reasons(metrics)
