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
from demand import feedback as dfeedback
from demand import intake as dintake
from demand import publication as dpub
from demand import priors as dpriors
from demand import structure as dstructure
from demand import calibration as dcal
from traffic_sim.core.fingerprint import fingerprint_files
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
            "candidates.rou.xml", "candidates.meta.json",
            "calibrated.rou.xml", "calibrated.agents.json",
            "calibrated_v1.rou.xml", "calibrated_v1.agents.json",
        ):
            (tmp_path / name).write_text(name)
        (tmp_path / "calibrated_v1.rou_close_old_edge.rou.xml").write_text("stale")
        products = {path.name for path in bsd.demand_run_products(tmp_path)}
        assert "calibrated_v1.rou_close_old_edge.rou.xml" not in products
        assert "calibrated_v1.rou.xml" in products
        assert "candidates.rou.xml" in products
        assert "candidates.meta.json" in products

    def test_date_is_a_backward_compatible_single_day_alias(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["build_sumo_demand.py", "--date", "2025-09-17"])
        args = bsd.parse_args()
        assert args.start_date == "2025-09-17"
        assert args.days == 1

    def test_through_share_target_is_validated_before_build(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["build_sumo_demand.py", "--through-share-target", "nan"])
        with pytest.raises(SystemExit):
            bsd.parse_args()

    def test_pfe_workers_supports_serial_publication(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["build_sumo_demand.py", "--pfe-workers", "1"])
        assert bsd.parse_args().pfe_workers == 1

    def test_pfe_workers_rejects_non_positive_values(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["build_sumo_demand.py", "--pfe-workers", "0"])
        with pytest.raises(SystemExit):
            bsd.parse_args()

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

    def test_multi_day_pfe_fit_is_split_by_day(self):
        targets = [{"edge": 10.0} for _ in range(192)]
        report = {
            "achieved": {
                "edge": [10.0] * 96 + [100.0] * 96,
            },
        }

        rows = bsd.pfe_fit_by_day(report, targets, days=2)

        assert rows[0]["geh_pct"] == 100.0
        assert rows[1]["geh_pct"] == 0.0
        assert [row["quarter_start"] for row in rows] == [0, 96]

    def test_multi_day_pfe_fit_requires_exact_full_days(self):
        with pytest.raises(ValueError, match="96 quarters"):
            bsd.pfe_fit_by_day(
                {"achieved": {"edge": [1.0] * 191}},
                [{"edge": 1.0} for _ in range(191)], days=2)

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
        "n_behavioural_agents": 3,
        "n_edge_support_agents": 0,
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

    def test_duarouter_binary_changes_candidate_cache_identity(self, tmp_path):
        """A SUMO upgrade must not restore routes made by the old router."""
        home = tmp_path / "sumo-home"
        binary = bsd.candidate_router_cache_input(home)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"duarouter-v1")

        first_key = candidate_cache.cache_key(
            {}, {"duarouter_binary": binary}, {})
        binary.write_bytes(b"duarouter-v2")
        second_key = candidate_cache.cache_key(
            {}, {"duarouter_binary": binary}, {})

        assert binary == home / "bin" / "duarouter"
        assert first_key != second_key


class TestFeedbackSimulationTimeout:
    """The congestion-feedback loop (--congestion-iterations) runs one extra
    real sumo pass per iteration to measure travel times — it needs the same
    bounded timeout as every other sumo subprocess call in this codebase
    (run_scenario.py's SUMO_TIMEOUT_S), or a hang here blocks the whole
    demand-build with no diagnostic."""

    def test_timeout_exits_cleanly(self, monkeypatch, tmp_path):
        # run_feedback_simulation writes its edgeData .add.xml into SUMO_DIR
        # before launching sumo. On a fresh clone the repo's gitignored sumo/
        # directory does not exist, so without this redirect the open() fails
        # first and the test both breaks and litters the working tree.
        monkeypatch.setattr(dfeedback, "SUMO_DIR", tmp_path)

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



class TestSharedRouteReferencesAreResolved:
    """A vehicle may carry its route inline OR reference a shared
    <route id=...>. Found 2026-08-05: the metric loop skipped the
    referencing form outright, so those vehicles vanished from EVERY
    structure metric and the remainder was reported as the whole
    population. Inert while the publisher emits inline routes, but the
    candidate pool already uses shared routes and DEMAND_PIPELINE_REVIEW's
    S4 proposes them for the pool file."""

    @staticmethod
    def _geo(tmp_path):
        def feat(eid, lat, sensor=None):
            props = {"id": eid}
            if sensor:
                props["sensor_id"] = sensor
            return {"type": "Feature", "properties": props,
                    "geometry": {"type": "LineString",
                                 "coordinates": [[11.97, lat], [11.971, lat]]}}
        geo = tmp_path / "network.geojson"
        geo.write_text(json.dumps({"features": [
            feat("A", 57.700, sensor="s1"), feat("B", 57.750),
        ]}))
        return geo

    def test_a_referenced_route_is_counted_not_skipped(self, monkeypatch, tmp_path):
        geo = self._geo(tmp_path)
        monkeypatch.setattr(dstructure, "GEO_PATH", geo)
        rou = tmp_path / "calibrated.rou.xml"
        # one inline vehicle, one referencing a shared route — both cross
        # the sensor edge A, so both must appear in sensor_passages.
        rou.write_text(
            '<routes>'
            '<route id="r0" edges="A B"/>'
            '<vehicle id="0" depart="0.0"><route edges="A B"/></vehicle>'
            '<vehicle id="1" depart="1.0" route="r0"/>'
            '</routes>')
        report = bsd.calibrated_structure_report(rou)
        assert report is not None
        counted = sum(report["sensor_passages"].values())
        assert counted == 2, (
            f"both vehicles must be counted, got {counted}: "
            f"{report['sensor_passages']}")

    def test_an_unresolvable_route_raises_instead_of_under_reporting(
            self, monkeypatch, tmp_path):
        geo = self._geo(tmp_path)
        monkeypatch.setattr(dstructure, "GEO_PATH", geo)
        rou = tmp_path / "calibrated.rou.xml"
        rou.write_text(
            '<routes>'
            '<vehicle id="0" depart="0.0"><route edges="A B"/></vehicle>'
            '<vehicle id="1" depart="1.0" route="missing"/>'
            '</routes>')
        with pytest.raises(ValueError, match="no resolvable route"):
            bsd.calibrated_structure_report(rou)


_FAKE_PUBLISH_STAGED: dict = {}


def _fake_publish_job(job):
    """Module-level stand-in for _write_pfe_variant_report_job (picklable,
    fork-inheritable) writing deterministic staged artifacts."""
    suffix, key = job
    path = _FAKE_PUBLISH_STAGED[suffix]
    path.write_text(f"routes for {key}")
    dcal._agent_path_for(path).write_text(f"agents for {key}")
    return suffix, key, {"vehicles": 1, "geh_pct": 100.0,
                         "infeasible_intervals": 0}, 0.5


class TestPublishWorkerBudget:
    """Memory-gated publish pool (speed lever 1, 2026-07-21): parallel only
    when the machine can hold worker-count copies of the parent."""

    def test_single_variant_never_pools(self):
        assert dcal._publish_worker_budget(1) == 1

    def test_measurement_failure_falls_back_to_serial(self, monkeypatch):
        monkeypatch.setattr(dcal.resource, "getrusage",
                            lambda _who: (_ for _ in ()).throw(OSError("no")))
        assert dcal._publish_worker_budget(3) == 1

    def test_tight_memory_falls_back_to_serial(self, monkeypatch):
        class Usage:
            ru_maxrss = 20 * 1024**3 if dcal.sys.platform == "darwin" \
                else 20 * 1024**2
        monkeypatch.setattr(dcal.resource, "getrusage", lambda _who: Usage())
        assert dcal._publish_worker_budget(3) == 1

    def test_ample_memory_grants_all_variants(self, monkeypatch):
        class Usage:
            ru_maxrss = 1 * 1024**3 if dcal.sys.platform == "darwin" \
                else 1 * 1024**2
        monkeypatch.setattr(dcal.resource, "getrusage", lambda _who: Usage())
        assert dcal._publish_worker_budget(3) == 3

    def test_pool_and_serial_publish_identical_files(self, tmp_path):
        """The pool path must write byte-identical staged artifacts to the
        serial path — same worker function, distinct files per variant.
        The worker and its path map are module-level so fork children can
        unpickle/inherit them, mirroring production's fork-inherited state."""
        global _FAKE_PUBLISH_STAGED
        _FAKE_PUBLISH_STAGED = {
            suffix: dcal._staged_route_path(
                tmp_path / f"calibrated{suffix}.rou.xml")
            for suffix in ("", "_v1", "_v2")
        }
        variants = [("", "edge_shares"), ("_v1", "edge_shares_q10"),
                    ("_v2", "edge_shares_q90")]

        def snapshot():
            return {s: (path.read_text(),
                        dcal._agent_path_for(path).read_text())
                    for s, path in _FAKE_PUBLISH_STAGED.items()}

        serial_reports = [_fake_publish_job(v) for v in variants]
        serial_files = snapshot()

        with dcal.mp.get_context("fork").Pool(processes=3) as pool:
            pool_reports = pool.map(_fake_publish_job, variants)
        assert snapshot() == serial_files
        assert pool_reports == serial_reports


class TestVariantPublication:
    """All direction variants must publish atomically as one demand build."""

    def test_rejected_variant_keeps_every_previous_output(self, monkeypatch, tmp_path):
        from traffic_sim.demand import pfe

        final_q50 = tmp_path / "calibrated.rou.xml"
        final_q10 = tmp_path / "calibrated_v1.rou.xml"
        for route in (final_q50, final_q10):
            route.write_text(f"previous {route.name}")
            dcal._agent_path_for(route).write_text(f"previous {route.name} agent")

        monkeypatch.setattr(dcal.pfe, "prepare_calibration",
                            lambda _path: ([object()], np.array([1.0])))
        monkeypatch.setattr(dcal.pfe, "build_touch_index", lambda _shapes: {})
        monkeypatch.setattr(dcal.pfe, "_purpose_targets_per_quarter",
                            lambda _shapes, n, _offset=0.0: [{} for _ in range(n)])
        monkeypatch.setattr(dcal, "structure_groups_for_shapes", lambda _shapes: [])
        monkeypatch.setattr(dcal, "GEO_PATH", tmp_path / "absent.geojson")
        # This test exercises atomic rejection, not publish parallelism —
        # force the serial path so FakePool only fakes the SOLVE pool.
        monkeypatch.setattr(dcal, "_publish_worker_budget", lambda _n: 1)

        class FakePool:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def imap_unordered(self, worker, tasks):
                # One fake stands in for BOTH flat pools: the interval solve
                # and the stage-A1 integer repair, which have different task
                # and result shapes.
                if worker is dcal._run_pfe_counts_job:
                    for suffix, quarter in tasks:
                        yield suffix, quarter, None
                    return
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


class TestParallelIntegerRepairIdentity:
    """Stage A1: the flat (variant x quarter) integer-repair pool must be a
    pure speed change.

    The end-to-end guard — same orchestration, same inputs, once with the
    repair pool live and once with it neutralised so the writer repairs
    inline exactly as it did before the change — must publish byte-identical
    route XML and agent provenance.
    """

    @staticmethod
    def _candidates_file(path):
        # Two purposes over a shared measured edge M, one of them also
        # touching a separately bounded edge U so the constrained integer
        # repair genuinely fires.
        routes = [
            ("t1", "through", "O_t M D_t"),
            ("w1", "arbete", "O_w M U D_w"),
            ("f1", "fritid", "O_f M U D_f"),
        ]
        path.write_text(
            "<routes>\n" + "".join(
                f'  <vehicle id="{vid}" depart="0.0">'
                f'<route edges="{edges}"/></vehicle>\n'
                for vid, _purpose, edges in routes) + "</routes>\n")
        meta = {"candidates": {vid: {"purpose": purpose}
                               for vid, purpose, _edges in routes}}
        path.with_name(path.name.replace(".rou.xml", ".meta.json")).write_text(
            json.dumps(meta))
        return path

    def _run(self, tmp_path, tag, monkeypatch, neutralise_pool):
        out_dir = tmp_path / tag
        out_dir.mkdir()
        if neutralise_pool:
            monkeypatch.setattr(dcal, "_run_pfe_counts_job",
                                _legacy_counts_job)
        variants = [("", "edge_shares"), ("_v1", "edge_shares_q10")]
        nq = 4
        inputs = {
            suffix: {
                "targets": [{"M": 9.0 + q} for q in range(nq)],
                "bounds_pq": [{"U": (0.0, 4.0)} for _ in range(nq)],
                "hard_bounds_pq": [{"U": (0.0, 4.0)} for _ in range(nq)],
                "priors_pq": [{} for _ in range(nq)],
                "out_path": out_dir / f"calibrated{suffix}.rou.xml",
                "keep_achieved": True,
            }
            for suffix, _key in variants
        }
        reports = dcal.run_pfe_variants_flat_parallel(
            self._candidates_file(tmp_path / "candidates.rou.xml"),
            variants, inputs, max_workers=2)
        published = {}
        for suffix, _key in variants:
            route = out_dir / f"calibrated{suffix}.rou.xml"
            published[suffix] = (
                route.read_bytes(),
                dcal._agent_path_for(route).read_bytes(),
                {k: v for k, v in reports[suffix].items() if k != "timings_s"},
            )
        return published

    def test_parallel_repair_publishes_identical_artifacts(
            self, tmp_path, monkeypatch):
        with monkeypatch.context() as legacy_ctx:
            legacy = self._run(tmp_path, "legacy", legacy_ctx,
                               neutralise_pool=True)
        parallel = self._run(tmp_path, "parallel", monkeypatch,
                             neutralise_pool=False)

        assert set(parallel) == set(legacy)
        for suffix, (xml, agents, report) in parallel.items():
            assert xml == legacy[suffix][0]
            assert agents == legacy[suffix][1]
            assert report == legacy[suffix][2]
            assert report["vehicles"] > 0


def _legacy_counts_job(job):
    """Module level so the fork pool can pickle it: leaves every quarter
    unfilled, which sends the writer down its historical inline path."""
    return job[0], job[1], None


class TestDayPoolBlocks:
    """Stage B: candidate blocks for calibrating ONE day inside a composition.

    A day calibrated alone must see the same variable set as that day inside
    a window (so the other day types in the composition contribute geometry)
    while its own quarters receive only its own candidates (so the per-quarter
    purpose mix is unchanged).
    """

    WEEKDAY = pd.Timestamp("2025-09-16")   # Tuesday
    WEEKEND = pd.Timestamp("2025-09-20")   # Saturday

    def _blocks(self, day, composition):
        return dintake.day_pool_blocks({}, {}, day, 0, composition)

    def test_single_type_composition_is_just_the_day(self):
        blocks = self._blocks(self.WEEKDAY, ("weekday",))
        assert len(blocks) == 1
        assert blocks[0]["offset_s"] == 0
        assert blocks[0]["date"] == "2025-09-16"
        assert blocks[0]["pool_key"] == "weekday"

    def test_other_day_types_are_carried_outside_the_calibrated_window(self):
        blocks = self._blocks(self.WEEKDAY, ("weekday", "weekend"))
        own, carrier = blocks
        assert own["offset_s"] == 0 and own["pool_key"] == "weekday"
        # A 24 h calibration covers 0..86400; the carrier must start beyond it
        # so its departures cannot land in any calibrated quarter.
        assert carrier["offset_s"] >= 86400
        assert carrier["pool_key"] == "weekend"
        assert carrier["shape_carrier"] is True

    def test_carrier_blocks_are_canonical_not_calendar(self):
        # A carrier exists to bring a day type's geometry, so its draws must
        # depend on the day type alone — otherwise the same composition would
        # produce different pools on different dates.
        weekday_side = self._blocks(self.WEEKDAY, ("weekday", "weekend"))[1]
        weekend_side = self._blocks(self.WEEKEND, ("weekday", "weekend"))[1]
        assert weekday_side["date"] == "pool-template:weekend"
        assert weekend_side["date"] == "pool-template:weekday"

    def test_exactly_one_block_per_day_type(self):
        blocks = self._blocks(self.WEEKDAY, ("weekday", "weekend"))
        keys = [block["pool_key"] for block in blocks]
        assert sorted(keys) == ["weekday", "weekend"]
        # Repeating a type once per calendar day (what a monolithic window
        # does) would make structure-guard pool shares depend on how many
        # weekdays and weekend days shared the envelope.
        assert len(keys) == len(set(keys))

    def test_day_outside_its_composition_is_refused(self):
        with pytest.raises(ValueError, match="composition"):
            self._blocks(self.WEEKEND, ("weekday",))

    def test_window_composition_covers_every_day_type_present(self):
        assert dintake.window_pool_composition(self.WEEKDAY, 1) == ("weekday",)
        assert dintake.window_pool_composition(self.WEEKDAY, 7) == (
            "weekday", "weekend")
        assert dintake.window_pool_composition(self.WEEKEND, 2) == ("weekend",)

    def test_holidays_share_the_weekend_geometry_pool(self):
        # classify_day already treats a holiday as weekend-shaped; the pool
        # key must follow it, or a holiday would demand its own template.
        assert dintake.pool_key_for(pd.Timestamp("2025-12-25")) == "weekend"


class TestStartupSourceHashes:
    """A build's provenance must describe the code that actually ran.

    Demand builds run for hours. Hashing the sources when the metadata is
    written records whatever is on disk by then — after a mid-build edit,
    code that never ran. Found live 2026-07-21 on a running search.
    """

    def test_fingerprint_uses_hashes_captured_before_the_build(self, tmp_path):
        from traffic_sim.core.fingerprint import make_fingerprint

        source = tmp_path / "generator.py"
        source.write_text("original\n")
        captured = fingerprint_files({"generator": source})
        source.write_text("edited mid-build\n")

        record = make_fingerprint(
            contract={"kind": "test"}, artifacts={},
            source_files={"generator": source},
            source_file_records=captured)

        assert record["source_files"] == captured
        assert record["source_files"]["generator"]["sha256"] != (
            fingerprint_files({"generator": source})["generator"]["sha256"])

    def test_without_captured_hashes_the_current_files_are_used(self, tmp_path):
        from traffic_sim.core.fingerprint import make_fingerprint

        source = tmp_path / "generator.py"
        source.write_text("original\n")
        record = make_fingerprint(
            contract={"kind": "test"}, artifacts={},
            source_files={"generator": source})

        assert record["source_files"] == fingerprint_files({"generator": source})

    def test_the_demand_builder_captures_its_generators_at_import(self):
        assert "build_candidates" in bsd.STARTUP_SOURCE_HASHES
        assert "pfe" in bsd.STARTUP_SOURCE_HASHES
        assert bsd.STARTUP_SOURCE_HASHES["build_candidates"]["sha256"]

    def test_day_identity_uses_every_canonical_demand_source(self):
        hashes = bsd.demand_day_source_hashes()

        assert set(hashes) == set(bsd.STARTUP_SOURCE_HASHES)
        assert "traffic_sim/demand/structure_caps.py" in hashes
        assert hashes["traffic_sim/demand/structure_caps.py"]

    def test_new_picker_helper_changes_day_identity(self):
        base = {
            "pfe": {"sha256": "pfe-v1"},
            "traffic_sim/demand/structure_caps.py": {"sha256": "caps-v1"},
        }
        changed = {
            **base,
            "traffic_sim/demand/structure_caps.py": {"sha256": "caps-v2"},
        }
        identity = dict(
            date="2027-01-01", source="forecast",
            pool_composition=("weekday",), inputs={})

        first = bsd.DayIdentity(
            **identity, source_hashes=bsd.demand_day_source_hashes(base))
        second = bsd.DayIdentity(
            **identity, source_hashes=bsd.demand_day_source_hashes(changed))

        assert first.key != second.key

    def test_numerical_runtime_identity_records_versions(self):
        identity = bsd.runtime_package_identity(("numpy", "scipy"))

        assert identity["python"]
        assert identity["platform"]
        assert identity["numpy"] == np.__version__
        assert identity["scipy"]
