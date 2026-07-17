"""build_sumo_demand's direction-share split: the specific behaviour that
prevents a two-way ('Total') sensor's raw count from silently being
duplicated in full onto both of its directed edges (which would make a
perfectly-calibrated direction look like it only delivers ~50%, an artifact
found 2026-07-06 while investigating sensor 107 — see CLAUDE.md)."""

import json
import sys
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import build_sumo_demand as bsd
from demand import intake as dintake
from demand import publication as dpub
from demand import priors as dpriors
from demand import structure as dstructure
from demand import calibration as dcal
from traffic_sim.demand import cache as candidate_cache


class TestPurposeMarginAfterRouteFiltering:
    def test_restores_only_activity_categories(self):
        source = Counter({
            "through": 40, "external": 10,
            "arbete": 11, "service": 4, "fritid": 5,
        })

        result = dcal.apply_activity_purpose_margin(
            [source], [{"arbete": 0.2, "service": 0.3, "fritid": 0.5}])

        assert result == [Counter({
            "through": 40, "external": 10,
            "arbete": 4, "service": 6, "fritid": 10,
        })]

    def test_keeps_sparse_non_activity_quarter_unchanged(self):
        source = Counter({"through": 5, "external": 2})
        assert dcal.apply_activity_purpose_margin(
            [source], [{"arbete": 0.2, "service": 0.3, "fritid": 0.5}]) == [source]

    def test_requires_one_valid_share_vector_per_quarter(self):
        with pytest.raises(ValueError, match="cover every PFE quarter"):
            dcal.apply_activity_purpose_margin([Counter({"arbete": 1})], [])
        with pytest.raises(ValueError, match="invalid activity purpose shares"):
            dcal.apply_activity_purpose_margin(
                [Counter({"arbete": 1})], [{"arbete": -1.0}])

    def test_window_share_source_crosses_weekend_boundary(self):
        shares = dintake.activity_purpose_shares_for_window(
            pd.Timestamp("2025-09-19 23:45"), 2)

        assert shares[0]["arbete"] == pytest.approx(0.491)
        assert shares[1]["arbete"] == pytest.approx(0.222)


class TestB1DateRangeContract:
    def test_run_products_ignore_stale_closure_routes(self, tmp_path):
        for name in (
            "demand_meta.json", "demand_build_spec.json",
            "calibrated.rou.xml", "calibrated.agents.json",
            "calibrated_v1.rou.xml", "calibrated_v1.agents.json",
        ):
            (tmp_path / name).write_text(name)
        (tmp_path / "calibrated_v1.rou_close_old_edge.rou.xml").write_text("stale")
        products = {path.name for path in bsd.demand_run_products(tmp_path)}
        assert "calibrated_v1.rou_close_old_edge.rou.xml" not in products
        assert "calibrated_v1.rou.xml" in products

    def test_date_is_a_backward_compatible_single_day_alias(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["build_sumo_demand.py", "--date", "2025-09-17"])
        args = bsd.parse_args()
        assert args.start_date == "2025-09-17"
        assert args.days == 1

    def test_validate_range_rejects_year_boundary_crossing(self):
        with pytest.raises(ValueError, match="crosses"):
            bsd.validate_date_range("2025-12-31", days=2, source_year=2025)

    def test_validate_range_allows_last_single_day_of_year(self):
        start, end = bsd.validate_date_range("2025-12-31", days=1, source_year=2025)
        assert start.strftime("%Y-%m-%d") == "2025-12-31"
        assert end.strftime("%Y-%m-%d") == "2026-01-01"

    def test_multi_day_blocks_keep_each_days_own_real_shape(self, monkeypatch):
        fallback = np.full(24, 1 / 24)
        monkeypatch.setattr("build_candidates.daily_shape", lambda weekend: fallback)
        monkeypatch.setattr("build_candidates.blend_day_shape", lambda real, _: real)
        day0 = TestRealDayShape()._flat_day(peak_hour=8)
        day1 = TestRealDayShape()._flat_day(peak_hour=20)
        blocks = bsd.multi_day_blocks(
            {"e1": day0 + day1}, {"S1": ["e1"]},
            pd.Timestamp("2025-09-16"), days=2, qi_start=0)
        assert np.argmax(blocks[0]["profile"]) == 8
        assert np.argmax(blocks[1]["profile"]) == 20
        assert [b["offset_s"] for b in blocks] == [0, 86400]
        assert [b["id_prefix"] for b in blocks] == ["d0_", "d1_"]

    def test_single_day_metadata_keeps_legacy_fields_and_adds_range_contract(self):
        meta = bsd.demand_metadata(
            start_date="2025-09-16", days=1, source="historical",
            begin="06:00", end="10:00", qi_start=2472, n_intervals=16,
            epoch_sim=pd.Timestamp("2025-09-16 06:00"),
            direction_split="estimated", n_variants=3,
        )
        assert {"start_date", "days", "end_date_exclusive", "day_boundaries_s", "day_kinds"} <= set(meta)
        assert meta["start_date"] == meta["date"] == "2025-09-16"
        assert meta["days"] == 1
        assert meta["end_date_exclusive"] == "2025-09-17"
        assert meta["day_boundaries_s"] == [0, 86400]
        assert meta["day_kinds"] == ["weekday"]
        assert meta["begin"] == "06:00"
        assert meta["end"] == "10:00"

    def test_multi_day_metadata_uses_range_fields_not_legacy_single_day_fields(self):
        meta = bsd.demand_metadata(
            start_date="2025-09-16", days=2, source="historical",
            begin="00:00", end="24:00", qi_start=0, n_intervals=192,
            epoch_sim=pd.Timestamp("2025-09-16"),
            direction_split="estimated", n_variants=3,
        )
        assert meta["end_date_exclusive"] == "2025-09-18"
        assert meta["day_boundaries_s"] == [0, 86400, 172800]
        assert meta["day_kinds"] == ["weekday", "weekday"]
        assert not {"date", "begin", "end"} & set(meta)

    def test_bounds_and_priors_remain_tied_to_structural_reference(self, monkeypatch):
        calls = []
        # structural_bounds_and_priors lives in demand.priors now — its
        # internal calls resolve against THAT module's globals.
        monkeypatch.setattr(dpriors, "ensure_bounds", lambda date, begin, end: calls.append(
            ("bounds", date, begin, end)) or {"bounds": {}})
        monkeypatch.setattr(dpriors, "ensure_priors", lambda date: calls.append(
            ("priors", date)) or {"edges": {}})

        bsd.structural_bounds_and_priors("00:00", "24:00")

        assert calls == [
            ("bounds", bsd.STRUCTURAL_REFERENCE_DATE, "00:00", "24:00"),
            ("priors", bsd.STRUCTURAL_REFERENCE_DATE),
        ]

    def test_interval_constraints_keep_hard_bounds_above_assignment_ceiling(self):
        bounds_data = {"bounds": {"hard": [[0.0, 100.0], [0.0, 200.0]]}}
        priors_data = {"edges": {
            "soft": {
                "prior": [12.0, 13.0],
                "prior_low": [8.0, 9.0],
                "prior_high": [16.0, 17.0],
            },
        }}
        corridor = {"corridor": {
            "prior": [20.0, 21.0], "band": [4.0, 5.0],
        }}
        assignment = {"weight": 0.1, "flows": {
            "hard": [4.0, 5.0],
            "soft": [6.0, 7.0],
            "corridor": [8.0, 9.0],
            "free": [3.0, 4.0],
        }}

        bounds, priors, hard = dpriors.build_interval_constraints(
            2, 0, bounds_data, priors_data, corridor, assignment)

        assert hard == [{"hard": (0.0, 100.0)}, {"hard": (0.0, 200.0)}]
        assert bounds == [
            {"hard": (0.0, 100.0), "free": (0.0, 15.0)},
            {"hard": (0.0, 200.0), "free": (0.0, 20.0)},
        ]
        assert priors[0]["soft"][0] == 12.0
        assert priors[1]["corridor"][0] == 21.0


def write_direction_split(tmp_path, shares: dict[str, list[float]]) -> None:
    (tmp_path / "direction_split.json").write_text(json.dumps({
        "107": {"edge_shares": shares},
    }))


def test_build_targets_splits_two_way_total_by_direction_share(monkeypatch, tmp_path):
    monkeypatch.setattr(dintake, "SUMO_DIR", tmp_path)
    write_direction_split(tmp_path, {"edgeN": [0.6] * 96, "edgeS": [0.4] * 96})

    flows = {"edgeN": [100.0], "edgeS": [100.0]}
    sensor_edges = {"107": ["edgeN", "edgeS"]}
    targets = bsd.build_targets(flows, sensor_edges, qi_start=0, n_intervals=1)

    assert targets[0]["edgeN"] == 60.0
    assert targets[0]["edgeS"] == 40.0
    # the two directions must sum back to the raw measured total, not 2x it
    assert targets[0]["edgeN"] + targets[0]["edgeS"] == 100.0


def test_build_targets_single_direction_sensor_takes_full_count(monkeypatch, tmp_path):
    monkeypatch.setattr(dintake, "SUMO_DIR", tmp_path)
    # no direction_split.json at all -> even-split fallback, which for a
    # lone edge is 1/1 = the full count (matches single-direction sensors
    # like 1076, 133, 134, 2276, 1074)
    flows = {"edgeS": [80.0]}
    sensor_edges = {"1076": ["edgeS"]}
    targets = bsd.build_targets(flows, sensor_edges, qi_start=0, n_intervals=1)

    assert targets[0]["edgeS"] == 80.0


def test_build_targets_multi_day_range_skips_dst_null_quarters(monkeypatch, tmp_path):
    """2025-03-30's four absent export quarters stay absent inside a range."""
    monkeypatch.setattr(dintake, "SUMO_DIR", tmp_path)
    march_29_qi = 87 * 96  # Jan+Feb+28 days of March before 29 March
    arr = [10.0] * (march_29_qi + 192)
    missing = [march_29_qi + 96 + q for q in range(8, 12)]
    for qi in missing:
        arr[qi] = None
    targets = bsd.build_targets({"edgeS": arr}, {"S": ["edgeS"]},
                                qi_start=march_29_qi, n_intervals=192)
    assert all("edgeS" in targets[i] for i in range(192) if i not in range(104, 108))
    assert [targets[i] for i in range(104, 108)] == [{}, {}, {}, {}]


def test_unserviceable_measured_edges_emit_explicit_warning(capsys):
    bsd.warn_unserviceable_measured_edges(
        {"unserviceable_edges": ["a_b_0", "b_c_1"]}, "edge_shares")

    out = capsys.readouterr().out
    assert "UNSERVICEABLE MEASURED EDGES" in out
    assert "a_b_0, b_c_1" in out


def test_bound_violations_emit_explicit_warning(capsys):
    bsd.warn_bound_violations(
        {"bound_violations": [
            {"edge": "a_b_0", "quarter": 3, "achieved": 12.0,
             "bound_lo": 0.0, "bound_hi": 5.0},
        ]}, "edge_shares")

    out = capsys.readouterr().out
    assert "BOUND VIOLATIONS FROM INTEGER ROUNDING" in out
    assert "a_b_0@q3" in out


def test_no_bound_violations_is_silent(capsys):
    bsd.warn_bound_violations({"bound_violations": []}, "edge_shares")
    assert capsys.readouterr().out == ""


def test_calibrated_agent_summary_reports_real_purpose_counts(tmp_path):
    route = tmp_path / "calibrated.rou.xml"
    route.write_text("<routes/>")
    (tmp_path / "calibrated.agents.json").write_text(json.dumps({
        "schema_version": 1,
        "agents": [{"purpose": "arbete"}, {"purpose": "arbete"},
                   {"purpose": "service", "departure_s": 900}],
    }))

    assert bsd.calibrated_agent_summary(route, 2) == {
        "agent_file": "calibrated.agents.json",
        "n_agents": 3,
        "purpose_counts": {"arbete": 2, "service": 1},
        "purpose_counts_by_quarter": [{"arbete": 2}, {"service": 1}],
    }


def test_write_counts_splits_two_way_total_by_direction_share(monkeypatch, tmp_path):
    monkeypatch.setattr(dintake, "SUMO_DIR", tmp_path)
    write_direction_split(tmp_path, {"edgeN": [0.6] * 96, "edgeS": [0.4] * 96})

    flows = {"edgeN": [100.0], "edgeS": [100.0]}
    sensor_edges = {"107": ["edgeN", "edgeS"]}
    out_path = tmp_path / "counts.xml"
    bsd.write_counts(flows, sensor_edges, qi_start=0, n_intervals=1, out_path=out_path)

    root = ET.parse(out_path).getroot()
    counts = {e.get("id"): float(e.get("count")) for e in root.find("interval")}
    assert counts["edgeN"] == 60.0
    assert counts["edgeS"] == 40.0


def test_clear_stale_scenarios_removes_only_json(monkeypatch, tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "baseline.json").write_text("{}")
    (scen_dir / "close_x.json").write_text("{}")
    (scen_dir / "README.txt").write_text("keep")
    monkeypatch.setattr(dpub, "SCEN_DIR", scen_dir)

    assert bsd.clear_stale_scenarios() == 2
    assert not (scen_dir / "baseline.json").exists()
    assert not (scen_dir / "close_x.json").exists()
    assert (scen_dir / "README.txt").exists()


def test_clear_stale_scenarios_leaves_a_valid_empty_manifest(monkeypatch, tmp_path):
    """A CLI-only `python3 build_sumo_demand.py` run (no immediate
    run_scenario.py after it, unlike serve.py's recalibration path) must
    not leave web/index.html's `fetch('data/scenarios/index.json')`
    404ing — that silently breaks the Simulering picker until someone
    happens to run run_scenario.py next."""
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "baseline.json").write_text("{}")
    (scen_dir / "index.json").write_text(json.dumps({"scenarios": [{"name": "old"}]}))
    monkeypatch.setattr(dpub, "SCEN_DIR", scen_dir)

    assert bsd.clear_stale_scenarios() == 2
    index_path = scen_dir / "index.json"
    assert index_path.exists()
    assert json.loads(index_path.read_text()) == {"scenarios": []}


class TestClassifyDay:
    """Found 2026-07-08/09: build_candidates.py's departure-time shape
    (daily_shape()) always read the 'weekday' profile regardless of the
    actual --date being calibrated — no notion of weekend OR holiday. This
    is the fix's decision logic, extracted so it's testable without a real
    pandas Timestamp or a real calendar date lookup."""

    def test_weekday_non_holiday(self):
        use_weekend, kind = bsd.classify_day("2025-09-16", dayofweek=1)  # Tuesday
        assert use_weekend is False
        assert kind == "weekday"

    def test_saturday_is_weekend(self):
        use_weekend, kind = bsd.classify_day("2025-09-20", dayofweek=5)  # Saturday
        assert use_weekend is True
        assert kind == "weekend"

    def test_sunday_is_weekend(self):
        use_weekend, kind = bsd.classify_day("2025-09-21", dayofweek=6)  # Sunday
        assert use_weekend is True
        assert kind == "weekend"

    def test_holiday_on_a_weekday_uses_weekend_shape(self):
        # Midsommardagen 2025-06-20 is a real Friday (dayofweek=4) in
        # HOLIDAY_DATES_2025 -- the exact case a pure weekday/weekend check
        # would misclassify as a normal commute-peaked weekday.
        use_weekend, kind = bsd.classify_day("2025-06-20", dayofweek=4)
        assert use_weekend is True
        assert kind == "holiday"

    def test_2027_forecast_holiday_also_detected(self):
        # Nyårsdagen 2027-01-01 is a Friday (dayofweek=4) via the forecast-
        # year holiday mapping, not the 2025 set directly.
        use_weekend, kind = bsd.classify_day("2027-01-01", dayofweek=4)
        assert use_weekend is True
        assert kind == "holiday"

    def test_weekend_takes_priority_in_label_even_if_also_a_holiday(self):
        # 2025-01-01 (Nyårsdagen) happens to be a Wednesday in 2025, so this
        # just checks label precedence logic directly with a constructed
        # weekend+holiday date rather than relying on calendar coincidence.
        use_weekend, kind = bsd.classify_day("2025-01-01", dayofweek=5)
        assert use_weekend is True
        assert kind == "weekend"   # weekend checked first, label reflects that


class TestRealDayShape:
    """The day's OWN measured (or forecast) shape, preferred over a generic
    weekday/weekend/holiday bucket average since it directly reflects
    whatever actually happened/will happen that exact date -- no holiday
    list to maintain, and it catches atypical days (school breaks, weather,
    local events) a fixed list never could. Added 2026-07-09."""

    def _flat_day(self, peak_hour: int, n_days: int = 1) -> list:
        """96*n_days quarters, value 1.0 everywhere except 4 quarters at
        peak_hour in EVERY day, which get 10.0."""
        day = [1.0] * 96
        for q in range(peak_hour * 4, peak_hour * 4 + 4):
            day[q] = 10.0
        return day * n_days

    def test_full_day_recovers_the_real_peak_hour(self):
        flows = {"e1": self._flat_day(peak_hour=14)}
        sensor_edges = {"S1": ["e1"]}
        shape = bsd.real_day_shape(flows, sensor_edges, qi_start=0)
        assert shape is not None
        assert np.argmax(shape) == 14
        assert shape.sum() == pytest.approx(1.0)

    def test_qi_start_mid_window_still_uses_the_full_day(self):
        """qi_start pointing at e.g. a 06:00-10:00 window's start (qi 24,
        not a day boundary) must still pull hours 0-23 of THAT day, since
        departure-time shape must cover the whole day regardless of the
        calibration window."""
        flows = {"e1": self._flat_day(peak_hour=14)}
        sensor_edges = {"S1": ["e1"]}
        shape = bsd.real_day_shape(flows, sensor_edges, qi_start=24)
        assert shape is not None
        assert np.argmax(shape) == 14

    def test_second_day_uses_its_own_data_not_the_first_days(self):
        day0 = self._flat_day(peak_hour=8)
        day1 = self._flat_day(peak_hour=20)
        flows = {"e1": day0 + day1}
        sensor_edges = {"S1": ["e1"]}
        shape = bsd.real_day_shape(flows, sensor_edges, qi_start=96)
        assert shape is not None
        assert np.argmax(shape) == 20

    def test_too_sparse_a_day_returns_none(self):
        """Only a handful of real hours -- e.g. most of the day's quarters
        are null (sensor outage) -- must not be trusted as a real shape."""
        day = [None] * 96
        for q in range(0, 12):   # 3 real hours out of 24
            day[q] = 5.0
        flows = {"e1": day}
        sensor_edges = {"S1": ["e1"]}
        shape = bsd.real_day_shape(flows, sensor_edges, qi_start=0)
        assert shape is None

    def test_multiple_sensor_edges_are_aggregated(self):
        flows = {
            "e1": self._flat_day(peak_hour=9),
            "e2": self._flat_day(peak_hour=9),
        }
        sensor_edges = {"S1": ["e1"], "S2": ["e2"]}
        shape = bsd.real_day_shape(flows, sensor_edges, qi_start=0)
        assert shape is not None
        assert np.argmax(shape) == 9


class TestBprTravelTimes:
    """BPR volume-delay function computed directly from PFE's own achieved
    flow, replacing an expensive real simulation for most congestion-
    feedback iterations. Added 2026-07-08 after research showed MSA/Frank-
    Wolfe-style assignment typically needs 10-25+ iterations to converge —
    impractical if each one costs a real meso simulation pass. Computed
    PER PERIOD (default hourly), not as one flat average for the whole
    calibration window -- a single daily average dilutes a sharp rush-hour
    peak into a mild multi-hour mean, hiding exactly the congestion this
    mechanism exists to react to."""

    def _fake_net_and_geo(self, tmp_path, highway="residential", lanes=1,
                          length=100.0, speed=10.0):
        net = tmp_path / "net.net.xml"
        lane_xml = "".join(f'<lane length="{length}" speed="{speed}"/>'
                           for _ in range(lanes))
        net.write_text(f'<net><edge id="e1">{lane_xml}</edge></net>')
        geo = tmp_path / "network.geojson"
        geo.write_text(json.dumps({"features": [
            {"properties": {"id": "e1", "highway": highway}}]}))
        return net, geo

    def test_at_capacity_matches_bpr_formula(self, tmp_path):
        net, geo = self._fake_net_and_geo(tmp_path)   # residential: 500 veh/h/lane
        # 4 quarters (1 hour) of 125 veh each = 500 veh/h = exactly at capacity
        tt = bsd.bpr_travel_times({"e1": [125.0] * 4}, net_path=net, geo_path=geo,
                                  period_s=3600.0)
        t_free = 100.0 / 10.0   # length/speed = 10s
        assert tt["e1"] == [pytest.approx(t_free * (1 + 0.15 * 1.0 ** 4))]

    def test_below_capacity_close_to_free_flow(self, tmp_path):
        net, geo = self._fake_net_and_geo(tmp_path)
        tt = bsd.bpr_travel_times({"e1": [2.5] * 4}, net_path=net, geo_path=geo,
                                  period_s=3600.0)
        assert tt["e1"][0] == pytest.approx(10.0, rel=0.05)

    def test_unknown_highway_type_uses_default_capacity(self, tmp_path):
        net, geo = self._fake_net_and_geo(tmp_path, highway="mystery_type")
        tt = bsd.bpr_travel_times({"e1": [75.0] * 4}, net_path=net, geo_path=geo,
                                  period_s=3600.0)
        assert "e1" in tt   # doesn't crash / drop the edge for an unknown type

    def test_peak_quarter_not_diluted_by_quiet_quarters(self, tmp_path):
        """A single congested hour amid quiet ones must show up as its OWN
        period's travel time, not get averaged into a mild overall mean."""
        net, geo = self._fake_net_and_geo(tmp_path)   # 500 veh/h/lane capacity
        # hour 1: light (40 veh/h); hour 2: at-capacity (500 veh/h)
        series = [10.0] * 4 + [125.0] * 4
        tt = bsd.bpr_travel_times({"e1": series}, net_path=net, geo_path=geo,
                                  period_s=3600.0)
        assert len(tt["e1"]) == 2
        assert tt["e1"][0] < tt["e1"][1]   # quiet hour must be cheaper than peak hour
        assert tt["e1"][1] == pytest.approx(10.0 * (1 + 0.15 * 1.0 ** 4))


class TestDampTravelTimes:
    def test_first_call_is_unweighted(self):
        assert bsd.damp_travel_times({"a": [20.0]}, None, iteration=0) == {"a": [20.0]}

    def test_blends_with_previous_msa_style(self):
        result = bsd.damp_travel_times({"a": [20.0]}, {"a": [10.0]}, iteration=1)
        assert result["a"][0] == pytest.approx(15.0)   # step=1/2 -> 0.5*10+0.5*20

    def test_edges_missing_from_new_estimate_are_kept(self):
        result = bsd.damp_travel_times({"a": [20.0]}, {"a": [10.0], "b": [5.0]}, iteration=1)
        assert result["b"] == [5.0]


class TestWriteWeightFile:
    def test_roundtrip(self, tmp_path):
        out = tmp_path / "weights.xml"
        bsd.write_weight_file({"e1": [12.34]}, out, period_s=900)
        edge = ET.parse(out).getroot().find("interval").find("edge")
        assert edge.get("id") == "e1"
        assert float(edge.get("traveltime")) == pytest.approx(12.34)

    def test_multiple_periods_become_multiple_intervals(self, tmp_path):
        out = tmp_path / "weights.xml"
        bsd.write_weight_file({"e1": [10.0, 20.0]}, out, period_s=3600.0)
        intervals = ET.parse(out).getroot().findall("interval")
        assert len(intervals) == 2
        assert float(intervals[0].find("edge").get("traveltime")) == pytest.approx(10.0)
        assert float(intervals[1].find("edge").get("traveltime")) == pytest.approx(20.0)
        assert intervals[1].get("begin") == "3600.00"

    def test_feedback_weights_change_candidate_cache_identity(self, tmp_path, monkeypatch):
        """A feedback iteration must not reuse the initial free-flow pool."""
        monkeypatch.setattr(bsd, "SUMO_DIR", tmp_path)
        weights = tmp_path / "feedback.xml"
        weights.write_text('<meandata><interval><edge id="e" traveltime="10"/>'
                           '</interval></meandata>')

        free_key = candidate_cache.cache_key(
            {"routing_cost_mode": "free_flow"},
            {"routing_weights": bsd.candidate_routing_weight_cache_input(None)}, {})
        weighted_key = candidate_cache.cache_key(
            {"routing_cost_mode": "feedback"},
            {"routing_weights": bsd.candidate_routing_weight_cache_input(weights)}, {})
        weights.write_text('<meandata><interval><edge id="e" traveltime="20"/>'
                           '</interval></meandata>')
        changed_weight_key = candidate_cache.cache_key(
            {"routing_cost_mode": "feedback"},
            {"routing_weights": bsd.candidate_routing_weight_cache_input(weights)}, {})

        assert free_key != weighted_key
        assert weighted_key != changed_weight_key


class TestFeedbackSimulationTimeout:
    """The congestion-feedback loop (--congestion-iterations) runs one extra
    real sumo pass per iteration to measure travel times — it needs the same
    bounded timeout as every other sumo subprocess call in this codebase
    (run_scenario.py's SUMO_TIMEOUT_S), or a hang here blocks the whole
    demand-build with no diagnostic."""

    def test_timeout_exits_cleanly(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="sumo", timeout=kw.get("timeout"))
        monkeypatch.setattr(bsd.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            bsd.run_feedback_simulation(tmp_path / "calibrated.rou.xml",
                                        duration_s=900, home=tmp_path, iteration=0)


class TestGracefulDegradationOnSubprocessFailure:
    """subprocess.run's check=True raises CalledProcessError BEFORE the
    following line ever runs — a `check=True` call immediately followed by
    `if res.returncode != 0: ...` makes that whole branch dead code, silently
    replacing graceful degradation (or, for run_tool, a clean sys.exit with
    the tool's own stderr) with an uncaught traceback carrying none of the
    subprocess's diagnostic output. Found during a self-review 2026-07-10;
    these four call sites (ensure_observability, ensure_assignment_priors,
    ensure_priors, and the build_candidates.py invocation in main()) had
    exactly this bug after B2 added check=True to every subprocess call
    per the B0-derived 'always use check=True' lesson, without noticing the
    pre-existing manual check right below became unreachable.

    The fake `run` below deliberately mirrors the REAL subprocess.run's
    check=True semantics (raise CalledProcessError on a non-zero return)
    instead of always just returning a CompletedProcess — a fake that
    ignores the check kwarg can't distinguish the fixed code from the
    buggy version (confirmed: an earlier draft of these tests passed
    against both, which is a useless test)."""

    @staticmethod
    def _fake_run_returncode_1(*args, **kwargs):
        result = subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(1, args, output="", stderr="boom")
        return result

    def test_ensure_observability_degrades_gracefully_on_failure(self, monkeypatch, tmp_path):
        geo = tmp_path / "network.geojson"
        geo.write_text(json.dumps({"features": []}))
        monkeypatch.setattr(dpriors, "GEO_PATH", geo)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(dpriors.subprocess, "run", self._fake_run_returncode_1)
        result = bsd.ensure_observability()
        assert result == {"corridor_priors": {}, "derived_flows": {}}

    def test_ensure_assignment_priors_degrades_gracefully_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(dpriors.subprocess, "run", self._fake_run_returncode_1)
        result = bsd.ensure_assignment_priors()
        assert result == {"weight": 0.0, "flows": {}}

    def test_matching_assignment_prior_identity_skips_rebuild(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "sumo" / "assignment_priors.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "schema_version": 2, "input_key": "current-key",
            "weight": 0.15, "flows": {"edge": [1.0]},
        }))
        monkeypatch.setattr(dpriors, "_assignment_prior_cache_contract",
                            lambda **_config: (2, "current-key"))

        def must_not_run(*_args, **_kwargs):
            raise AssertionError("matching artifact should be reused")
        monkeypatch.setattr(dpriors.subprocess, "run", must_not_run)

        assert bsd.ensure_assignment_priors()["flows"] == {"edge": [1.0]}

    def test_stale_assignment_prior_is_rebuilt_before_use(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "sumo" / "assignment_priors.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "schema_version": 1, "input_key": "old-key",
            "weight": 0.15, "flows": {"stale": [99.0]},
        }))
        monkeypatch.setattr(dpriors, "_assignment_prior_cache_contract",
                            lambda **_config: (2, "current-key"))
        calls = []

        def rebuild(*args, **kwargs):
            calls.append((args, kwargs))
            path.write_text(json.dumps({
                "schema_version": 2, "input_key": "current-key",
                "weight": 0.15, "flows": {"fresh": [2.0]},
            }))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        monkeypatch.setattr(dpriors.subprocess, "run", rebuild)

        result = bsd.ensure_assignment_priors()
        assert len(calls) == 1
        assert result["flows"] == {"fresh": [2.0]}

    def test_failed_stale_assignment_prior_rebuild_never_uses_old_field(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "sumo" / "assignment_priors.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "schema_version": 1, "input_key": "old-key",
            "weight": 0.15, "flows": {"stale": [99.0]},
        }))
        monkeypatch.setattr(dpriors, "_assignment_prior_cache_contract",
                            lambda **_config: (2, "current-key"))
        monkeypatch.setattr(dpriors.subprocess, "run", self._fake_run_returncode_1)

        assert bsd.ensure_assignment_priors() == {"weight": 0.0, "flows": {}}

    def test_assignment_prior_rebuild_uses_candidate_model_parameters(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "sumo" / "assignment_priors.json"
        expected_config = {
            "gravity_km": 2.1, "through_fraction": 0.4,
            "cross_fraction": 0.25, "gravity_alpha": 1.2, "seed": 7,
        }
        seen = {}

        def contract(**config):
            seen["contract"] = config
            return 2, "custom-key"
        monkeypatch.setattr(dpriors, "_assignment_prior_cache_contract", contract)

        def rebuild(command, **_kwargs):
            seen["command"] = command
            path.parent.mkdir()
            path.write_text(json.dumps({
                "schema_version": 2, "input_key": "custom-key",
                "weight": 0.15, "flows": {},
            }))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        monkeypatch.setattr(dpriors.subprocess, "run", rebuild)

        result = bsd.ensure_assignment_priors(**expected_config)
        assert result["input_key"] == "custom-key"
        assert seen["contract"] == expected_config
        for name, value in expected_config.items():
            flag = {
                "gravity_km": "--gravity-km",
                "through_fraction": "--through-fraction",
                "cross_fraction": "--cross-fraction",
                "gravity_alpha": "--gravity-alpha",
                "seed": "--seed",
            }[name]
            index = seen["command"].index(flag)
            assert seen["command"][index + 1] == str(value)

    def test_ensure_priors_degrades_gracefully_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(dpriors.subprocess, "run", self._fake_run_returncode_1)
        result = bsd.ensure_priors("2025-09-16")
        assert result == {"edges": {}}

    def test_run_tool_prints_stderr_tail_before_exiting(self, monkeypatch, tmp_path, capsys):
        def fake_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args, 1, stdout="", stderr="the real error")
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, args, output="", stderr="the real error")
            return result
        monkeypatch.setattr(bsd.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            bsd.run_tool("randomTrips.py", [], tmp_path)
        assert "the real error" in capsys.readouterr().out


class TestPurposeLengthOrdering:
    """Purpose-conditional trip length is the piece of the distance data
    (Trafikanalys Tabell 3: fritid LONGEST) that must survive calibration.
    Found 2026-07-13: the pool had the right ordering but the calibrated
    output inverted it — this gate makes that visible on every build."""

    @staticmethod
    def _write_fixture(tmp_path, fritid_dest):
        # Three edges: A (origin), B ~5.5 km north, C ~100 m north of A.
        def feat(eid, lat, sensor=None):
            props = {"id": eid}
            if sensor:
                props["sensor_id"] = sensor
            return {"type": "Feature", "properties": props,
                    "geometry": {"type": "LineString",
                                 "coordinates": [[11.97, lat], [11.971, lat]]}}
        geo = tmp_path / "network.geojson"
        geo.write_text(json.dumps({"features": [
            feat("A", 57.700, sensor="s1"), feat("B", 57.750), feat("C", 57.701),
        ]}))
        rou = tmp_path / "calibrated.rou.xml"
        rou.write_text('<routes><vehicle id="0" depart="0.0">'
                       '<route edges="A B"/></vehicle></routes>')
        agents = ([{"purpose": "arbete", "origin_edge": "A",
                    "destination_edge": "B"}] * 60
                  + [{"purpose": "fritid", "origin_edge": "A",
                      "destination_edge": fritid_dest}] * 60)
        (tmp_path / "calibrated.agents.json").write_text(
            json.dumps({"agents": agents}))
        return geo, rou

    def test_inverted_ordering_is_flagged(self, monkeypatch, tmp_path):
        geo, rou = self._write_fixture(tmp_path, fritid_dest="C")  # short
        monkeypatch.setattr(dstructure, "GEO_PATH", geo)
        report = bsd.calibrated_structure_report(rou)
        pl = report["purpose_length_km"]
        assert pl["arbete"]["n"] == 60 and pl["fritid"]["n"] == 60
        assert pl["fritid"]["mean_km"] < pl["arbete"]["mean_km"]
        assert any("purpose_length_ordering" in f
                   for f in report["structure_flags"])

    def test_correct_ordering_is_not_flagged(self, monkeypatch, tmp_path):
        geo, rou = self._write_fixture(tmp_path, fritid_dest="B")  # long
        monkeypatch.setattr(dstructure, "GEO_PATH", geo)
        report = bsd.calibrated_structure_report(rou)
        assert not any("purpose_length_ordering" in f
                       for f in report["structure_flags"])

    def test_missing_agents_sidecar_is_tolerated(self, monkeypatch, tmp_path):
        geo, rou = self._write_fixture(tmp_path, fritid_dest="B")
        (tmp_path / "calibrated.agents.json").unlink()
        monkeypatch.setattr(dstructure, "GEO_PATH", geo)
        report = bsd.calibrated_structure_report(rou)
        assert report is not None
        assert "purpose_length_km" not in report


class TestVariantPublication:
    """All direction variants must publish atomically as one demand build."""

    def test_rejected_variant_keeps_every_previous_output(self, monkeypatch, tmp_path):
        import pfe

        final_q50 = tmp_path / "calibrated.rou.xml"
        final_q10 = tmp_path / "calibrated_v1.rou.xml"
        for route in (final_q50, final_q10):
            route.write_text(f"previous {route.name}")
            dcal._agent_path_for(route).write_text(f"previous {route.name} agent")

        monkeypatch.setattr(dcal.pfe, "prepare_calibration",
                            lambda _path: ([object()], np.array([1.0])))
        monkeypatch.setattr(dcal.pfe, "_purpose_targets_per_quarter",
                            lambda _shapes, n, _offset=0.0: [{} for _ in range(n)])
        monkeypatch.setattr(dcal, "structure_groups_for_shapes", lambda _shapes: [])
        monkeypatch.setattr(dcal, "GEO_PATH", tmp_path / "absent.geojson")

        class FakePool:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def imap_unordered(self, _worker, tasks):
                for suffix, key, quarter in tasks:
                    yield suffix, key, quarter, np.array([1.0]), pfe.RUNG_CLEAN

        monkeypatch.setattr(dcal.mp, "get_context",
                            lambda _method: SimpleNamespace(Pool=FakePool))

        def fake_writer(_shapes, out_path, _targets, _solutions, *_args, **_kwargs):
            out_path.write_text(f"staged {out_path.name}")
            dcal._agent_path_for(out_path).write_text(f"staged {out_path.name} agent")
            return {
                "vehicles": 1,
                "geh_pct": 100.0 if "_v1" not in out_path.name else 75.0,
                "infeasible_intervals": 0,
                "bound_violations": [],
                "unserviceable_edges": [],
                "purpose_allocation_summary": {},
            }

        monkeypatch.setattr(dcal.pfe, "write_calibration_report", fake_writer)
        variants = [("", "edge_shares"), ("_v1", "edge_shares_q10")]
        inputs = {
            "": {"out_path": final_q50, "targets": [{}], "bounds_pq": [{}],
                 "hard_bounds_pq": [{}], "priors_pq": [{}]},
            "_v1": {"out_path": final_q10, "targets": [{}], "bounds_pq": [{}],
                    "hard_bounds_pq": [{}], "priors_pq": [{}]},
        }

        with pytest.raises(RuntimeError, match="no route variants were published"):
            dcal.run_pfe_variants_flat_parallel(tmp_path / "candidates.xml", variants,
                                                inputs, max_workers=1)

        for route in (final_q50, final_q10):
            assert route.read_text() == f"previous {route.name}"
            assert dcal._agent_path_for(route).read_text() == f"previous {route.name} agent"
            assert not dcal._staged_route_path(route).exists()
            assert not dcal._agent_path_for(dcal._staged_route_path(route)).exists()
