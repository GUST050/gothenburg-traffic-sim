from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.simulation import warm_state_forensics as f


def _record(loss, *, depart=0.0, arrival=10.0, duration=10.0,
            route=100.0, waiting=0.0, waiting_count=0.0):
    return {
        "timeLoss": float(loss), "depart": float(depart),
        "arrival": float(arrival), "duration": float(duration),
        "routeLength": float(route), "waitingTime": float(waiting),
        "waitingCount": float(waiting_count),
    }


IDENTITY = {"schedule_id": "schedule-a", "demand_variant": "q10",
            "seed": 1000}


def _raw(cold=None, prefix=None, resumed=None, *, recorded=None):
    cold = cold or {"a": _record(1), "b": _record(2), "c": _record(3)}
    prefix = prefix or {"a": cold["a"]}
    resumed = resumed or {"b": cold["b"], "c": cold["c"]}
    if recorded is None:
        recorded = {
            "cold_total_s": round(sum(x["timeLoss"] for x in cold.values()), 2),
            "prefix_total_s": round(sum(x["timeLoss"] for x in prefix.values()), 2),
            "resumed_total_s": round(sum(x["timeLoss"] for x in resumed.values()), 2),
            "warm_total_s": round(
                sum(x["timeLoss"] for x in prefix.values())
                + sum(x["timeLoss"] for x in resumed.values()), 2),
            "cold_trip_count": len(cold), "prefix_trip_count": len(prefix),
            "resumed_trip_count": len(resumed),
            "warm_trip_count": len(prefix) + len(resumed),
        }
    return f.build_raw_payload(identity=IDENTITY, cold=cold, prefix=prefix,
                               resumed=resumed, recorded_totals=recorded)


def _xml(path: Path, records, *, comment=""):
    lines = ["<tripinfos>"]
    if comment:
        lines.append(f"<!-- {comment} -->")
    for vehicle, record in records.items():
        attrs = " ".join(
            [f'id="{vehicle}"']
            + [f'{name}="{value}"' for name, value in record.items()])
        lines.append(f"<tripinfo {attrs}/>")
    lines.append("</tripinfos>")
    path.write_text("\n".join(lines) + "\n")


class TestStrictParser:

    def test_reads_only_exact_elements_and_all_fields(self, tmp_path):
        path = tmp_path / "trip.xml"
        _xml(path, {"v": _record(1.25)},
             comment='<tripinfo-output value="not-a-record"/>')
        assert f.parse_tripinfo(path) == {"v": _record(1.25)}

    @pytest.mark.parametrize("broken", [
        "<tripinfos><tripinfo",
        "<tripinfos><tripinfo id='v' timeLoss='nan' depart='0' arrival='1' "
        "duration='1' routeLength='1' waitingTime='0' waitingCount='0'/></tripinfos>",
        "<tripinfos><tripinfo id='' timeLoss='1' depart='0' arrival='1' "
        "duration='1' routeLength='1' waitingTime='0' waitingCount='0'/></tripinfos>",
        "<tripinfos><tripinfo id='v' timeLoss='1'/></tripinfos>",
    ])
    def test_rejects_malformed_missing_or_nonfinite(self, tmp_path, broken):
        path = tmp_path / "broken.xml"
        path.write_text(broken)
        with pytest.raises(f.ForensicObservationError):
            f.parse_tripinfo(path)

    def test_rejects_duplicate_ids(self, tmp_path):
        path = tmp_path / "duplicate.xml"
        attrs = 'timeLoss="1" depart="0" arrival="1" duration="1" ' \
                'routeLength="1" waitingTime="0" waitingCount="0"'
        path.write_text(
            f"<tripinfos><tripinfo id=\"v\" {attrs}/><tripinfo id=\"v\" "
            f"{attrs}/></tripinfos>")
        with pytest.raises(f.ForensicObservationError, match="duplicate"):
            f.parse_tripinfo(path)


class TestClassification:

    def test_exact_agreement_recomputes(self):
        raw = _raw()
        report = f.build_forensic_report(raw)
        assert report["classification"] == f.EXACT_AGREEMENT
        assert report["differing_vehicle_count"] == 0
        assert f.validate_forensic_report(report, raw) == report

    def test_equal_totals_cannot_hide_population_loss(self):
        cold = {"a": _record(1), "b": _record(2), "c": _record(3)}
        prefix = {"a": cold["a"]}
        resumed = {"b": _record(5)}  # same total 6, but c vanished
        report = f.build_forensic_report(_raw(cold, prefix, resumed))
        assert report["classification"] == f.POPULATION_PARTITION_ERROR
        assert report["population"]["missing_from_split"] == ["c"]

    def test_time_loss_only_drift(self):
        cold = {"a": _record(1), "b": _record(2), "c": _record(3)}
        prefix = {"a": cold["a"]}
        resumed = {"b": _record(1.9), "c": cold["c"]}
        report = f.build_forensic_report(_raw(cold, prefix, resumed))
        assert report["classification"] == f.TIME_LOSS_ONLY_DRIFT
        assert report["field_mismatch_counts"]["timeLoss"] == 1
        assert report["time_loss_delta_distribution"]["sum_s"] == \
            pytest.approx(-0.1)

    def test_kinematic_output_drift_has_priority_over_time_loss_label(self):
        cold = {"a": _record(1), "b": _record(2), "c": _record(3)}
        prefix = {"a": cold["a"]}
        resumed = {"b": _record(1.9, duration=11), "c": cold["c"]}
        report = f.build_forensic_report(_raw(cold, prefix, resumed))
        assert report["classification"] == f.KINEMATIC_OUTPUT_DRIFT
        assert report["field_mismatch_counts"]["duration"] == 1

    def test_forged_aggregate_is_not_called_exact(self):
        raw = _raw()
        raw["recorded_totals"]["warm_total_s"] = 999.0
        body = {k: v for k, v in raw.items() if k != "content_digest"}
        raw["content_digest"] = f.canonical_digest(body)
        report = f.build_forensic_report(raw)
        assert report["classification"] == \
            f.AGGREGATE_REPORTING_INCONSISTENCY
        assert report["aggregate_mismatches"]

    def test_prefix_and_resumed_membership_must_be_disjoint(self):
        with pytest.raises(f.ForensicObservationError, match="both prefix"):
            _raw(prefix={"a": _record(1)}, resumed={"a": _record(1),
                                                     "b": _record(2)})

    def test_tampered_report_is_rejected(self):
        raw = _raw()
        report = f.build_forensic_report(raw)
        report["classification"] = f.TIME_LOSS_ONLY_DRIFT
        with pytest.raises(f.ForensicObservationError, match="does not recompute"):
            f.validate_forensic_report(report, raw)

    def test_global_verdict_requires_exact_identity_coverage(self):
        report = f.build_forensic_report(_raw())
        verdict = f.build_global_verdict([report], [IDENTITY])
        assert verdict["all_exact"] is True
        with pytest.raises(f.ForensicObservationError, match="coverage"):
            f.build_global_verdict([], [IDENTITY])


class TestObserver:

    def test_captures_before_files_are_deleted_and_rebuilds(self, tmp_path):
        observer = f.ForensicObserver()
        cold, prefix, resumed = ({"a": _record(1), "b": _record(2),
                                  "c": _record(3)},
                                 {"a": _record(1)},
                                 {"b": _record(2), "c": _record(3)})
        for phase, records in zip(f.PHASES, (cold, prefix, resumed)):
            path = tmp_path / f"{phase}.xml"
            _xml(path, records)
            observer.capture(
                **IDENTITY, phase=phase, tripinfo_path=path,
                reported_total_s=sum(x["timeLoss"] for x in records.values()),
                reported_trip_count=len(records))
            path.unlink()
        observer.capture_warm_summary(**IDENTITY, reported_total_s=6.0,
                                      reported_trip_count=3)
        payload = observer.raw_payloads([IDENTITY])[0]
        assert f.build_forensic_report(payload)["classification"] == \
            f.EXACT_AGREEMENT

    def test_duplicate_phase_and_incomplete_coverage_fail(self, tmp_path):
        observer = f.ForensicObserver()
        path = tmp_path / "cold.xml"
        _xml(path, {"a": _record(1)})
        observer.capture(**IDENTITY, phase="cold_candidate", tripinfo_path=path,
                         reported_total_s=1.0, reported_trip_count=1)
        with pytest.raises(f.ForensicObservationError, match="duplicate"):
            observer.capture(**IDENTITY, phase="cold_candidate",
                             tripinfo_path=path, reported_total_s=1.0,
                             reported_trip_count=1)
        with pytest.raises(f.ForensicObservationError, match="phases"):
            observer.raw_payloads([IDENTITY])
