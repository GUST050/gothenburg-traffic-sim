"""Regression tests for the TAG M3.1 held-out-flow evaluator."""

import pytest

from tools.validate_dmrb import (
    aggregate_volume_test,
    evaluate,
    flow_difference_ok,
    geh,
    presentation_markdown,
)


def _report(cases):
    n_hours = max((hour for hour, _measured, _simulated in cases), default=0) + 1
    n_intervals = 4 * n_hours
    return {
        "schema_version": 3,
        "comparison_contract": {"n_intervals": n_intervals},
        "window": "test",
        "stations": {
            "s": {"edges": {"e": {"hourly_cases": [
                {"hour_index": hour, "measured": measured,
                 "simulated": simulated}
                for hour, measured, simulated in cases
            ], "excluded_incomplete_hours": [],
                "observed_quarters": n_intervals}}},
        },
    }


@pytest.mark.parametrize(
    ("observed", "modelled", "expected"),
    [
        (699, 799, True),
        (699, 800, False),
        (700, 805, True),
        (700, 806, False),
        (2700, 3105, True),
        (2700, 3106, False),
        (2701, 3101, True),
        (2701, 3102, False),
    ],
)
def test_flow_difference_uses_tag_observed_flow_bands(observed, modelled, expected):
    assert flow_difference_ok(modelled, observed) is expected


def test_zero_zero_geh_is_zero():
    assert geh(0, 0) == 0


def test_either_flow_or_geh_is_satisfactory():
    # The first case misses GEH but is within 100 veh/h. The second misses both.
    result = evaluate(_report([(0, 100, 190), (1, 100, 250)]))
    assert result["criteria"]["geh_lt_5"]["cases_meeting"] == 0
    assert result["criteria"]["flow_difference_by_observed_band"]["cases_meeting"] == 1
    assert result["criteria"]["either_flow_difference_or_geh"]["cases_meeting"] == 1


def test_guideline_is_strictly_more_than_85_percent():
    exactly_85 = [(hour, 100, 100 if hour < 17 else 400) for hour in range(20)]
    over_85 = [(hour, 100, 100 if hour < 6 else 400) for hour in range(7)]
    assert evaluate(_report(exactly_85))["verdict"] == "fail"
    assert evaluate(_report(over_85))["verdict"] == "pass"


def test_combined_union_cannot_turn_formal_tag_verdict_into_a_pass():
    # Every case passes the under-700 flow-difference band, while only one has
    # GEH < 5. The combined union is 100%, but formal TAG remains a failure.
    report = _report([(hour, 100, 100 if hour == 0 else 190)
                      for hour in range(7)])
    result = evaluate(report)
    assert result["criteria"]["flow_difference_by_observed_band"]["pass"] is True
    assert result["criteria"]["either_flow_difference_or_geh"]["share_pct"] == 100
    assert result["criteria"]["geh_lt_5"]["pass"] is False
    assert result["verdict"] == "fail"


def test_daily_only_legacy_report_is_rejected():
    report = {"stations": {"s": {"edges": {"e": {
        "measured_total": 100, "simulated_total": 100, "geh_ok_pct": 100,
    }}}}}
    with pytest.raises(ValueError, match="schema_version"):
        evaluate(report)


def test_duplicate_hour_is_rejected():
    with pytest.raises(ValueError, match="index is invalid"):
        evaluate(_report([(0, 100, 100), (0, 100, 100)]))


def test_missing_hour_cannot_be_silently_dropped():
    report = _report([(0, 100, 100), (1, 100, 400)])
    report["stations"]["s"]["edges"]["e"]["hourly_cases"] = [
        {"hour_index": 0, "measured": 100, "simulated": 100}]
    with pytest.raises(ValueError, match="complete window"):
        evaluate(report)


def test_excluded_hour_must_agree_with_quarter_coverage():
    report = _report([(0, 100, 100), (1, 100, 400)])
    edge = report["stations"]["s"]["edges"]["e"]
    edge["hourly_cases"] = edge["hourly_cases"][:1]
    edge["excluded_incomplete_hours"] = [1]
    with pytest.raises(ValueError, match="observed-quarter coverage"):
        evaluate(report)
    edge["observed_quarters"] = 7
    assert evaluate(report)["hourly_cases"] == 1


def test_all_zero_flows_have_a_null_ratio_summary():
    result = evaluate(_report([(0, 0, 0)]))
    assert result["ratio_summary"] == {
        "median": None, "min": None, "max": None,
        "unit": "hourly modelled / observed",
    }


def test_per_edge_report_exposes_each_tag_measure_and_error_shape():
    result = evaluate(_report([(0, 100, 190), (1, 100, 250)]))
    edge = result["by_edge"][0]
    assert edge["measured_total"] == 200
    assert edge["simulated_total"] == 440
    assert edge["ratio"] == 2.2
    assert edge["mean_absolute_error"] == 120
    assert edge["criteria"]["geh_lt_5"]["share_pct"] == 0
    assert edge["criteria"]["flow_difference_by_observed_band"]["share_pct"] == 50
    assert edge["criteria"]["either_flow_difference_or_geh"]["share_pct"] == 50


def test_presentation_explains_ratio_geh_and_rank_gain_without_calling_it_a_score():
    report = _report([(0, 100, 100)])
    station = report["stations"]["s"]
    station["observability_certificate"] = {"rank_gain": 1}
    rendered = presentation_markdown(report, evaluate(report))
    assert "Ratio 1,000" in rendered
    assert "GEH < 5" in rendered
    assert "Rank gain är inte ett betyg" in rendered
    assert "| s | 1/24 | 100 | 100 | 1.000" in rendered


def _calibration(*, geh_pct=100.0, unfinished=0, teleports=0):
    return {"sections": {
        "sensor_output": {"status": "pass", "geh_lt_5_pct": geh_pct},
        "simulation": {"status": "pass", "seeds": [{
            "inserted": 100,
            "loaded": 100,
            "unfinished": unfinished,
            "teleports": teleports,
        }]},
    }}


def test_aggregate_volume_test_includes_underidentified_station():
    report = _report([(0, 100, 92)])
    report["stations"]["s"]["observability_certificate"] = {
        "status": "underidentified", "rank_gain": 1,
    }
    result = aggregate_volume_test(report)
    assert result["verdict"] == "pass"
    assert result["stations_included"] == ["s"]
    assert result["station_count"] == 1
    assert result["all_stations_required"] is True
    assert result["absolute_error_pct"] == 8.0


def test_aggregate_volume_test_fails_above_ten_percent_error():
    assert aggregate_volume_test(_report([(0, 100, 89)]))["verdict"] == "fail"


def test_aggregate_volume_test_rejects_an_omitted_report_station():
    report = _report([(0, 100, 100)])
    report["stations"]["omitted"] = {"edges": {}}
    with pytest.raises(ValueError, match="requires every report station"):
        aggregate_volume_test(report)


def test_presentation_leads_with_clear_all_sensor_volume_test():
    report = _report([(0, 100, 92)])
    report["stations"]["s"]["observability_certificate"] = {
        "status": "underidentified", "rank_gain": 1,
    }
    rendered = presentation_markdown(
        report, evaluate(report), calibration=_calibration())
    assert "## Tydligt huvudtest: total trafikvolym, alla sensorer" in rendered
    assert "**Resultat: PASS**" in rendered
    assert "Alla 1 sensorer ingår: s. Ingen sensor har tagits bort" in rendered
    assert "Skillnaden är 8.0 procent" in rendered
    assert "DATA SAKNAS" not in rendered


def test_presentation_summarizes_all_quality_test_categories():
    report = _report([(0, 100, 92)])
    report["stations"]["s"]["observability_certificate"] = {
        "status": "underidentified", "rank_gain": 1,
    }
    calibration = _calibration()
    calibration["sections"].update({
        "counts_fit": {"status": "pass", "geh_pct": 100.0,
                       "infeasible_intervals": 0},
        "structure": {"status": "pass", "dest_within_200m_pct": 3.8,
                      "dest_baseline_pct": 1.9,
                      "onward_after_sensor_median_m": 2759.5},
        "purposes": {"status": "pass", "purpose_counts": {"arbete": 100},
                     "purpose_incompatible_quarters_by_variant": {"q": 0},
                     "ordering_violated": False},
        "multi_day": {"status": "pass", "not_applicable": True, "days": 1},
        "held_out": {"ratios": {"s:e": 0.92}},
    })
    closure = {
        "scenario": {"closure_integrity": "verified_clean",
                     "active_closure_edge_entries": 0},
        "seed_health": [{"teleports": 0, "collisions": 0}],
    }
    rendered = presentation_markdown(
        report, evaluate(report), calibration=calibration, closure=closure)
    for label in (
        "Totalvolym, alla sensorer",
        "Kalibrering mot sensorintervall",
        "Rå SUMO-sensorutdata",
        "SUMO-körhälsa",
        "Resestruktur",
        "Ändamål och proveniens",
        "Flerdagstest",
        "Samma-dag LOSO",
        "Temporalt holdout, TAG",
        "Observabilitet",
        "Avstängningsstresstest",
    ):
        assert label in rendered
