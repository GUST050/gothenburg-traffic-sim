"""Adversarial tests for boundary-aware warm-state forensics v2."""

from __future__ import annotations

import json

import pytest

from traffic_sim.simulation import warm_state_forensics as f


def _record(loss, *, arrival):
    return {
        "timeLoss": float(loss),
        "depart": 0.0,
        "arrival": float(arrival),
        "duration": float(arrival),
        "routeLength": 100.0,
        "waitingTime": 0.0,
        "waitingCount": 0.0,
    }


def _identity(point=100):
    return {"schedule_id": "schedule", "demand_variant": "q50",
            "seed": 1001, "warm_point_s": point}


def _raw(*, cold=None, prefix=None, resumed=None, identity=None,
         recorded=None):
    cold = cold or {
        "before": _record(1, arrival=90),
        "boundary": _record(2, arrival=100),
        "after": _record(3, arrival=110),
    }
    prefix = prefix if prefix is not None else {
        "before": cold["before"], "boundary": cold["boundary"]}
    resumed = resumed if resumed is not None else {"after": cold["after"]}
    if recorded is None:
        recorded = {
            "cold_total_s": round(sum(x["timeLoss"] for x in cold.values()), 2),
            "prefix_total_s": round(
                sum(x["timeLoss"] for x in prefix.values()), 2),
            "resumed_total_s": round(
                sum(x["timeLoss"] for x in resumed.values()), 2),
            "warm_total_s": round(
                sum(x["timeLoss"] for x in prefix.values())
                + sum(x["timeLoss"] for x in resumed.values()), 2),
            "cold_trip_count": len(cold),
            "prefix_trip_count": len(prefix),
            "resumed_trip_count": len(resumed),
            "warm_trip_count": len(prefix) + len(resumed),
        }
    return f.build_raw_payload_v2(
        identity=identity or _identity(), cold=cold, prefix=prefix,
        resumed=resumed, recorded_totals=recorded)


def _tripinfo(path, records):
    rows = []
    for vehicle, record in records.items():
        attrs = " ".join([f'id="{vehicle}"'] + [
            f'{field}="{record[field]}"' for field in f.TRIP_FIELDS])
        rows.append(f"<tripinfo {attrs}/>")
    path.write_text("<tripinfos>" + "".join(rows) + "</tripinfos>")


class TestV2IdentityAndPartition:

    @pytest.mark.parametrize("identity", [
        {"schedule_id": "schedule", "demand_variant": "q50", "seed": 1001},
        {**_identity(), "extra": True},
        {**_identity(), "warm_point_s": 0},
        {**_identity(), "warm_point_s": True},
    ])
    def test_identity_is_exact_and_boundary_is_positive(self, identity):
        with pytest.raises(f.ForensicObservationError):
            _raw(identity=identity)

    def test_correct_partition_and_boundary_equality_are_exact(self):
        report = f.build_forensic_report_v2(_raw())
        assert report["classification"] == f.EXACT_AGREEMENT
        assert report["population"]["misplaced_in_prefix"] == []
        assert report["population"]["misplaced_in_resumed"] == []
        assert report["population"]["expected_prefix_count"] == 2

    def test_both_swapped_directions_fail_even_when_counts_totals_match(self):
        cold = {
            "before": _record(1, arrival=90),
            "after": _record(2, arrival=110),
        }
        report = f.build_forensic_report_v2(_raw(
            cold=cold, prefix={"after": cold["after"]},
            resumed={"before": cold["before"]}))
        assert report["classification"] == f.POPULATION_PARTITION_ERROR
        assert report["population"]["misplaced_in_prefix"] == ["after"]
        assert report["population"]["misplaced_in_resumed"] == ["before"]
        assert report["aggregate_deltas"][
            "split_minus_cold_time_loss_s"] == 0.0

    def test_boundary_equal_vehicle_is_misplaced_if_resumed(self):
        cold = {"before": _record(1, arrival=90),
                "boundary": _record(2, arrival=100)}
        report = f.build_forensic_report_v2(_raw(
            cold=cold, prefix={"before": cold["before"]},
            resumed={"boundary": cold["boundary"]}))
        assert report["classification"] == f.POPULATION_PARTITION_ERROR
        assert report["population"]["misplaced_in_resumed"] == ["boundary"]

    def test_overlap_is_reported_not_hidden_by_equal_totals(self):
        cold = {"before": _record(1, arrival=90)}
        report = f.build_forensic_report_v2(_raw(
            cold=cold, prefix=cold, resumed=cold,
            recorded={
                "cold_total_s": 1.0, "prefix_total_s": 1.0,
                "resumed_total_s": 1.0, "warm_total_s": 2.0,
                "cold_trip_count": 1, "prefix_trip_count": 1,
                "resumed_trip_count": 1, "warm_trip_count": 2}))
        assert report["classification"] == f.POPULATION_PARTITION_ERROR
        assert report["population"]["overlap"] == ["before"]


class TestV2Recomputation:

    def test_explicit_raw_and_recorded_deltas_are_independent(self):
        raw = _raw(recorded={
            "cold_total_s": 6.0, "prefix_total_s": 3.0,
            "resumed_total_s": 3.0, "warm_total_s": 5.99,
            "cold_trip_count": 3, "prefix_trip_count": 2,
            "resumed_trip_count": 1, "warm_trip_count": 3})
        report = f.build_forensic_report_v2(raw)
        assert report["aggregate_deltas"] == {
            "split_minus_cold_time_loss_s": 0.0,
            "warm_minus_cold_time_loss_s": pytest.approx(-0.01),
        }
        assert report["classification"] == \
            f.AGGREGATE_REPORTING_INCONSISTENCY

    def test_whole_report_must_recompute(self):
        raw = _raw()
        report = f.build_forensic_report_v2(raw)
        report["population"]["misplaced_in_prefix"] = ["forged"]
        with pytest.raises(f.ForensicObservationError, match="recompute"):
            f.validate_forensic_report_v2(report, raw)

    def test_global_verdict_binds_the_warm_point(self):
        raw = _raw()
        report = f.build_forensic_report_v2(raw)
        assert f.build_global_verdict_v2(
            [report], [_identity()])["all_exact"] is True
        with pytest.raises(f.ForensicObservationError, match="content differs"):
            f.build_global_verdict_v2([report], [_identity(101)])


class TestBoundaryAwareObserver:

    def test_configuration_rejects_empty_duplicate_and_conflicting_identity(self):
        with pytest.raises(f.ForensicObservationError):
            f.BoundaryAwareForensicObserver([])
        with pytest.raises(f.ForensicObservationError, match="conflicting"):
            f.BoundaryAwareForensicObserver([_identity(100), _identity(101)])

    def test_unknown_identity_and_duplicate_capture_fail(self, tmp_path):
        observer = f.BoundaryAwareForensicObserver([_identity()])
        path = tmp_path / "trip.xml"
        _tripinfo(path, {"before": _record(1, arrival=90)})
        with pytest.raises(f.ForensicObservationError, match="unknown"):
            observer.capture(
                schedule_id="other", demand_variant="q50", seed=1001,
                phase="cold_candidate", tripinfo_path=path,
                reported_total_s=1.0, reported_trip_count=1)
        observer.capture(
            schedule_id="schedule", demand_variant="q50", seed=1001,
            phase="cold_candidate", tripinfo_path=path,
            reported_total_s=1.0, reported_trip_count=1)
        with pytest.raises(f.ForensicObservationError, match="duplicate"):
            observer.capture(
                schedule_id="schedule", demand_variant="q50", seed=1001,
                phase="cold_candidate", tripinfo_path=path,
                reported_total_s=1.0, reported_trip_count=1)

    def test_records_survive_file_lifetime_and_missing_phase_fails(self,
                                                                   tmp_path):
        observer = f.BoundaryAwareForensicObserver([_identity()])
        path = tmp_path / "trip.xml"
        _tripinfo(path, {"before": _record(1, arrival=90)})
        observer.capture(
            schedule_id="schedule", demand_variant="q50", seed=1001,
            phase="cold_candidate", tripinfo_path=path,
            reported_total_s=1.0, reported_trip_count=1)
        path.unlink()
        assert observer.completed_ledger()[0]["identity"]["warm_point_s"] == 100
        with pytest.raises(f.ForensicObservationError, match="incomplete"):
            observer.raw_payloads()
