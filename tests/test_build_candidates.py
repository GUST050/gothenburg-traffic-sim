"""Unit tests for build_candidates.py's pure/testable pieces — synthetic
tiny graphs and temp files only, no real network/DeSO/OSM data needed.

Covers this session's U-turn fix directly: upstream_downstream_gates() and
drop_uturn_routes() are the two mechanisms that eliminated the literal
edge-then-its-reverse pattern verified in sumo/candidates.rou.xml (see
git history) — these tests pin that behaviour so it can't silently regress."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_candidates as bc


def node(lat, lon):
    return {"y": lat, "x": lon}


def write_routes(path, vehicles):
    """vehicles: list of (id, list-of-edge-ids)."""
    root = ET.Element("routes")
    for vid, edges in vehicles:
        veh = ET.SubElement(root, "vehicle", id=vid, depart="0.0")
        ET.SubElement(veh, "route", edges=" ".join(edges))
    ET.ElementTree(root).write(path)


def read_vehicle_ids(path):
    return [veh.get("id") for veh in ET.parse(path).getroot().iter("vehicle")]


class TestReverseEdgeId:
    def test_swaps_endpoints_keeps_key(self):
        assert bc.reverse_edge_id("100_200_0") == "200_100_0"

    def test_double_reverse_is_identity(self):
        eid = "12345_6789_1"
        assert bc.reverse_edge_id(bc.reverse_edge_id(eid)) == eid


class TestDropUturnRoutes:
    def test_route_with_immediate_reversal_is_dropped(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("clean", ["1_2_0", "2_3_0", "3_4_0"]),
            ("uturn", ["1_2_0", "2_1_0", "1_5_0"]),   # 2_1_0 reverses 1_2_0
        ])
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == ["clean"]

    def test_no_uturns_leaves_file_untouched(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [("a", ["1_2_0", "2_3_0"]), ("b", ["5_6_0"])])
        mtime_before = path.stat().st_mtime_ns
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == ["a", "b"]
        assert path.stat().st_mtime_ns == mtime_before   # write() skipped when dropped==0

    def test_uturn_deep_inside_a_long_route_is_still_caught(self, tmp_path):
        path = tmp_path / "candidates.rou.xml"
        write_routes(path, [
            ("longuturn", ["1_2_0", "2_3_0", "3_4_0", "4_3_0", "3_9_0"]),
        ])
        bc.drop_uturn_routes(path)
        assert read_vehicle_ids(path) == []


class TestUpstreamDownstreamGates:
    def make_via_edge_graph(self):
        """Sensor edge 10->11 bears due north. Two candidate entry gates
        (one behind it to the south — a genuine upstream approach, one
        beyond it to the north — already past the edge, the wrong side for
        an entry) and two candidate exit gates (one ahead to the north, one
        behind to the south)."""
        G = nx.MultiDiGraph()
        G.add_node(10, **node(57.700, 11.900))
        G.add_node(11, **node(57.701, 11.900))     # 10->11: due north
        G.add_edge(10, 11, key=0)

        G.add_node(1, **node(57.698, 11.900))        # south of the edge: behind
        G.add_node(2, **node(57.705, 11.900))        # north of the edge: already
                                                        # past it, wrong side
        entries = [("1_10_0", 1), ("2_10_0", 2)]

        G.add_node(20, **node(57.703, 11.900))       # north of the edge: ahead
        G.add_node(21, **node(57.698, 11.900))       # south of the edge: behind
        exits = [("11_20_0", 20), ("11_21_0", 21)]
        return G, entries, exits

    def test_entry_gate_behind_the_edge_is_kept(self):
        G, entries, exits = self.make_via_edge_graph()
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "1_10_0" in ins

    def test_entry_gate_on_the_wrong_side_is_excluded(self):
        G, entries, exits = self.make_via_edge_graph()
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "2_10_0" not in ins

    def test_exit_gate_ahead_of_the_edge_is_kept(self):
        G, entries, exits = self.make_via_edge_graph()
        _, outs = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "11_20_0" in outs

    def test_exit_gate_behind_the_edge_is_excluded(self):
        G, entries, exits = self.make_via_edge_graph()
        _, outs = bc.upstream_downstream_gates(G, "10_11_0", entries, exits)
        assert "11_21_0" not in outs

    def test_falls_back_to_full_pool_when_nothing_matches(self):
        """If every gate happens to be on the wrong side, degrade to the
        unrestricted pool rather than leaving a sensor edge with zero
        via-trip gates."""
        G = nx.MultiDiGraph()
        G.add_node(10, **node(57.700, 11.900))
        G.add_node(11, **node(57.701, 11.900))
        G.add_edge(10, 11, key=0)
        G.add_node(2, **node(57.705, 11.900))   # north of the edge — wrong side
        entries = [("2_10_0", 2)]               # no valid "behind" gate at all
        ins, _ = bc.upstream_downstream_gates(G, "10_11_0", entries, [])
        assert ins == ["2_10_0"]   # fallback: the full (unfiltered) entry list


class TestFindGates:
    def test_entry_and_exit_gates_by_degree(self):
        G = nx.MultiDiGraph()
        # 1 -> 2 -> 3: node 1 has no predecessor (entry gate on 1->2),
        # node 3 has no successor (exit gate on 2->3).
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 3, key=0)
        entries, exits = bc.find_gates(G)
        assert entries == [("1_2_0", 1)]
        assert exits == [("2_3_0", 3)]

    def test_interior_edge_is_neither_entry_nor_exit(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0)
        G.add_edge(2, 3, key=0)
        G.add_edge(3, 1, key=0)   # closes the loop: every node has in+out
        entries, exits = bc.find_gates(G)
        assert entries == []
        assert exits == []


class TestGateWeights:
    def test_motorway_outweighs_residential(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="motorway")
        G.add_edge(3, 4, key=0, highway="residential")
        w = bc.gate_weights(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert w[0] > w[1]

    def test_weights_sum_to_one(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="primary")
        G.add_edge(3, 4, key=0, highway="tertiary")
        w = bc.gate_weights(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert w.sum() == pytest.approx(1.0)

    def test_unknown_highway_type_gets_default_weight_one(self):
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0, highway="cycleway")   # not in GATE_WEIGHT
        w = bc.gate_weights(G, [("1_2_0", 1)])
        assert w[0] == pytest.approx(1.0)


class TestGateLatLon:
    """E-I/I-E cross-boundary tours (added 2026-07-08) need gate
    coordinates to gravity-weight the internal endpoint by distance from
    the gate — same midpoint convention as load_graph_edges()."""

    def test_midpoint_of_edge_endpoints(self):
        G = nx.MultiDiGraph()
        G.add_node(1, y=57.700, x=11.900)
        G.add_node(2, y=57.702, x=11.904)
        G.add_edge(1, 2, key=0)
        lats, lons = bc.gate_latlon(G, [("1_2_0", 1)])
        assert lats[0] == pytest.approx((57.700 + 57.702) / 2)
        assert lons[0] == pytest.approx((11.900 + 11.904) / 2)

    def test_multiple_gates_preserve_order(self):
        G = nx.MultiDiGraph()
        G.add_node(1, y=57.70, x=11.90)
        G.add_node(2, y=57.71, x=11.91)
        G.add_node(3, y=57.80, x=12.00)
        G.add_node(4, y=57.81, x=12.01)
        G.add_edge(1, 2, key=0)
        G.add_edge(3, 4, key=0)
        lats, lons = bc.gate_latlon(G, [("1_2_0", 1), ("3_4_0", 3)])
        assert len(lats) == 2
        assert lats[1] > lats[0]   # second gate is further north


class TestPurposeSharesForDayType:
    """Found 2026-07-09: PURPOSE_SHARES was a single flat 43/33/24 constant
    (RVU's own WEEKLY average, no day-type qualifier in Fig.11's caption)
    applied on EVERY simulated day regardless of weekday/weekend/holiday —
    silently overstating 'arbete' on real weekends/holidays. Split into
    PURPOSE_SHARES_WEEKDAY/WEEKEND using NHTS 2017's verified weekday/
    weekend purpose-shift RATIOS (home-based work/shop+other/social),
    solved so the 5-weekday/2-weekend weekly average reproduces RVU's own
    43/33/24 exactly — the total stays anchored to the real Swedish
    measurement, only the day-type split is externally informed."""

    def test_weekday_shares_sum_to_one(self):
        assert sum(bc.PURPOSE_SHARES_WEEKDAY.values()) == pytest.approx(1.0)

    def test_weekend_shares_sum_to_one(self):
        assert sum(bc.PURPOSE_SHARES_WEEKEND.values()) == pytest.approx(1.0)

    def test_weekend_has_far_less_arbete_than_weekday(self):
        assert bc.PURPOSE_SHARES_WEEKEND["arbete"] < bc.PURPOSE_SHARES_WEEKDAY["arbete"]

    def test_weekend_has_more_fritid_than_weekday(self):
        assert bc.PURPOSE_SHARES_WEEKEND["fritid"] > bc.PURPOSE_SHARES_WEEKDAY["fritid"]

    def test_annual_average_reproduces_rvu_reported_total(self):
        """The load-bearing constraint: the EXACT 2025 annual day-type mix
        (249 true-weekday days + 116 weekend-shaped days [104 real weekend
        + 12 weekday-that-are-holidays] out of 365 — NOT a naive 5/7-2/7
        week, since ~12 weekday holidays/year measurably shift the
        composition) must reproduce RVU's actual measured 43/33/24 for
        each category — if someone tweaks either dict later without
        re-solving this, the anchor to real data silently breaks."""
        f_weekday, f_weekend_shaped = 249 / 365, 116 / 365
        rvu_annual_avg = {"arbete": 0.43, "service": 0.33, "fritid": 0.24}
        for cat, target in rvu_annual_avg.items():
            wd = bc.PURPOSE_SHARES_WEEKDAY[cat]
            we = bc.PURPOSE_SHARES_WEEKEND[cat]
            avg = f_weekday * wd + f_weekend_shaped * we
            assert avg == pytest.approx(target, abs=0.005)

    def test_purpose_shares_for_selects_correct_profile(self):
        assert bc.purpose_shares_for(is_weekend=False) == bc.PURPOSE_SHARES_WEEKDAY
        assert bc.purpose_shares_for(is_weekend=True) == bc.PURPOSE_SHARES_WEEKEND


class TestPurposeHourlyTables:
    """Purpose ALSO varies by hour, not just day-type — "kl 8 nästan 100%
    jobb" was the original observation. Calibrated from a THIRD external
    source with genuine hourly granularity (UK NTS table NTSQ03018,
    weekday) rescaled against our own real measured Gothenburg hourly
    shape; weekend uses a simpler model (only fritid varies by hour, since
    a 3-parameter fit against real weekend rush-hour anchors bought
    essentially nothing over a 1-parameter one). Added 2026-07-09."""

    def test_every_weekday_hour_sums_to_one(self):
        for h, row in enumerate(bc.PURPOSE_HOURLY_WEEKDAY):
            assert sum(row) == pytest.approx(1.0, abs=1e-6), f"hour {h}"

    def test_every_weekend_hour_sums_to_one(self):
        for h, row in enumerate(bc.PURPOSE_HOURLY_WEEKEND):
            assert sum(row) == pytest.approx(1.0, abs=1e-6), f"hour {h}"

    def test_weekday_morning_commute_peak_is_arbete_dominant(self):
        shares = bc.purpose_shares_for_hour(8, is_weekend=False)
        assert shares["arbete"] > 0.7

    def test_weekday_midday_is_service_dominant(self):
        shares = bc.purpose_shares_for_hour(11, is_weekend=False)
        assert shares["service"] > shares["arbete"]
        assert shares["service"] > shares["fritid"]

    def test_weekday_evening_has_more_fritid_than_morning_commute(self):
        morning = bc.purpose_shares_for_hour(8, is_weekend=False)
        evening = bc.purpose_shares_for_hour(20, is_weekend=False)
        assert evening["fritid"] > morning["fritid"]

    def test_weekend_arbete_share_stays_far_below_weekday_commute_peak(self):
        weekday_peak = bc.purpose_shares_for_hour(8, is_weekend=False)["arbete"]
        weekend_same_hour = bc.purpose_shares_for_hour(8, is_weekend=True)["arbete"]
        assert weekend_same_hour < weekday_peak / 2

    def test_weekend_fritid_rises_toward_evening(self):
        midday = bc.purpose_shares_for_hour(12, is_weekend=True)["fritid"]
        evening = bc.purpose_shares_for_hour(20, is_weekend=True)["fritid"]
        assert evening > midday

    def test_hour_wraps_modulo_24(self):
        assert bc.purpose_shares_for_hour(24, is_weekend=False) == \
            bc.purpose_shares_for_hour(0, is_weekend=False)

    def test_weekday_weighted_by_real_shape_reproduces_daily_average(self):
        """Load-bearing: the hourly table isn't just plausible-looking —
        weighted by our own real measured hourly shape, it must integrate
        back to PURPOSE_SHARES_WEEKDAY exactly (how it was solved)."""
        shape = bc.daily_shape(is_weekend=False)
        totals = {c: 0.0 for c in bc.PURPOSE_CATEGORIES}
        for h in range(24):
            shares = bc.purpose_shares_for_hour(h, is_weekend=False)
            for c in bc.PURPOSE_CATEGORIES:
                totals[c] += shape[h] * shares[c]
        for c in bc.PURPOSE_CATEGORIES:
            assert totals[c] == pytest.approx(bc.PURPOSE_SHARES_WEEKDAY[c], abs=0.01)

    def test_weekend_weighted_by_real_shape_reproduces_daily_average(self):
        shape = bc.daily_shape(is_weekend=True)
        totals = {c: 0.0 for c in bc.PURPOSE_CATEGORIES}
        for h in range(24):
            shares = bc.purpose_shares_for_hour(h, is_weekend=True)
            for c in bc.PURPOSE_CATEGORIES:
                totals[c] += shape[h] * shares[c]
        for c in bc.PURPOSE_CATEGORIES:
            assert totals[c] == pytest.approx(bc.PURPOSE_SHARES_WEEKEND[c], abs=0.01)


class TestDailyShape:
    def test_normalizes_to_one_and_matches_peak_hour(self, tmp_path, monkeypatch):
        profiles = {
            "edgeA": {"weekday": [0.0] * 96},
            "edgeB": {"weekday": [0.0] * 96},
        }
        # Put all traffic for both edges in hour 8 (slots 32-35).
        for e in profiles.values():
            for i in range(32, 36):
                e["weekday"][i] = 10.0
        (tmp_path / "web" / "data").mkdir(parents=True)
        import json
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))

        monkeypatch.chdir(tmp_path)
        shape = bc.daily_shape()
        assert shape.sum() == pytest.approx(1.0)
        assert shape[8] == pytest.approx(1.0)
        assert shape[7] == pytest.approx(0.0)

    def test_is_weekend_reads_the_weekend_profile_not_weekday(self, tmp_path, monkeypatch):
        """Found 2026-07-08: this always read 'weekday' regardless of which
        --date was actually being calibrated, even for a real Saturday/
        Sunday — normal_profile.json has carried a real 'weekend' profile
        (different shape: later start, broader single afternoon peak) the
        whole time, it just was never read."""
        profiles = {
            "edgeA": {"weekday": [0.0] * 96, "weekend": [0.0] * 96},
        }
        for i in range(32, 36):
            profiles["edgeA"]["weekday"][i] = 10.0   # weekday: hour 8 peak
        for i in range(64, 68):
            profiles["edgeA"]["weekend"][i] = 10.0   # weekend: hour 16 peak
        (tmp_path / "web" / "data").mkdir(parents=True)
        import json
        (tmp_path / "web" / "data" / "normal_profile.json").write_text(
            json.dumps({"profiles": profiles}))

        monkeypatch.chdir(tmp_path)
        weekday_shape = bc.daily_shape(is_weekend=False)
        weekend_shape = bc.daily_shape(is_weekend=True)
        assert weekday_shape[8] == pytest.approx(1.0)
        assert weekend_shape[16] == pytest.approx(1.0)
        assert weekend_shape[8] == pytest.approx(0.0)


class TestBlendDayShape:
    """--real-day-shape-file's measured/forecast shape, shrunk toward the
    weekday/weekend/holiday fallback rather than fully trusted (hedges
    against one day's sampling noise across just 6-7 sensors). Added
    2026-07-09."""

    def test_blend_weights_toward_real_by_default(self):
        real = np.zeros(24); real[10] = 1.0
        fallback = np.zeros(24); fallback[10] = 1.0
        blended = bc.blend_day_shape(real, fallback)
        assert blended[10] == pytest.approx(1.0)

    def test_real_and_fallback_disagreement_is_a_genuine_blend(self):
        real = np.zeros(24); real[10] = 1.0
        fallback = np.zeros(24); fallback[18] = 1.0
        blended = bc.blend_day_shape(real, fallback, weight=0.7)
        assert blended[10] == pytest.approx(0.7)
        assert blended[18] == pytest.approx(0.3)

    def test_result_sums_to_one(self):
        real = np.array([1.0, 2.0] + [0.0] * 22)
        fallback = np.array([0.0, 1.0, 1.0] + [0.0] * 21)
        blended = bc.blend_day_shape(real, fallback)
        assert blended.sum() == pytest.approx(1.0)


class TestHomeMass:
    def test_population_distributed_by_residential_street_length(self, monkeypatch):
        """Two residential edges in one DeSO zone split its population
        proportionally to their length; a non-residential edge in the same
        zone gets none."""
        zones = [{
            "properties": {"desokod": "Z1"},
            "geometry": {"type": "Polygon",
                        "coordinates": [[[11.0, 57.0], [12.0, 57.0],
                                       [12.0, 58.0], [11.0, 58.0], [11.0, 57.0]]]},
        }]
        pop = {"Z1": 900}
        monkeypatch.setattr(bc, "ensure_deso", lambda: (zones, pop))

        edges = [
            {"id": "a", "lat": 57.5, "lon": 11.5, "hw": "residential", "len": 100.0},
            {"id": "b", "lat": 57.5, "lon": 11.5, "hw": "residential", "len": 200.0},
            {"id": "c", "lat": 57.5, "lon": 11.5, "hw": "primary", "len": 500.0},
        ]
        mass = bc.home_mass(edges)
        assert mass[2] == 0.0                       # non-residential: no home mass
        assert mass[0] + mass[1] == pytest.approx(900.0)
        assert mass[1] == pytest.approx(2 * mass[0])  # proportional to length

    def test_edge_outside_any_zone_gets_no_mass(self, monkeypatch):
        zones = [{
            "properties": {"desokod": "Z1"},
            "geometry": {"type": "Polygon",
                        "coordinates": [[[11.0, 57.0], [12.0, 57.0],
                                       [12.0, 58.0], [11.0, 58.0], [11.0, 57.0]]]},
        }]
        pop = {"Z1": 500}
        monkeypatch.setattr(bc, "ensure_deso", lambda: (zones, pop))
        edges = [{"id": "far", "lat": 0.0, "lon": 0.0, "hw": "residential", "len": 100.0}]
        mass = bc.home_mass(edges)
        assert mass[0] == 0.0


class TestDuarouterWeightArgs:
    """Congestion-feedback loop (build_sumo_demand.py --congestion-iterations):
    candidates must route by free-flow cost when no feedback yet exists, and
    by the prior iteration's MEASURED travel time once it does."""

    def test_no_weight_file_means_free_flow_routing(self):
        assert bc.duarouter_weight_args(None) == []

    def test_weight_file_adds_traveltime_attribute_args(self):
        args = bc.duarouter_weight_args("sumo/feedback_edgedata_0.xml")
        assert args == ["--weight-files", "sumo/feedback_edgedata_0.xml",
                        "--weight-attribute", "traveltime"]

    def test_weight_period_passed_through_for_time_varying_files(self):
        args = bc.duarouter_weight_args("sumo/feedback_weights_0.xml", 3600.0)
        assert args == ["--weight-files", "sumo/feedback_weights_0.xml",
                        "--weight-attribute", "traveltime",
                        "--weight-period", "3600.0"]


class TestGravityDistanceKm:
    """A bare degree-distance treats 1° lat and 1° lon as equal in km, which
    is wrong at Gothenburg's latitude (cos(57.7°)≈0.535) and overstates
    east-west distance ~1.87x relative to north-south — found 2026-07-06,
    biasing gravity-weighted OD generation and the assignment-prior field
    against E-W trips."""

    def test_one_degree_north_south_is_about_110_km(self):
        d = bc.gravity_distance_km(np.array([58.7]), np.array([11.5]), 57.7, 11.5)
        assert d[0] == pytest.approx(110.54, rel=0.01)

    def test_one_degree_east_west_is_cos_corrected_not_110_km(self):
        d = bc.gravity_distance_km(np.array([57.7]), np.array([12.5]), 57.7, 11.5)
        assert d[0] == pytest.approx(111.32 * np.cos(np.radians(57.7)), rel=0.01)
        assert d[0] < 70.0   # NOT ~111 km, the bare-degree bug's answer

    def test_equal_lat_lon_offsets_give_unequal_km_distance(self):
        # Same 0.1 degree offset in each axis must NOT produce equal km
        # distances (that would mean the cos-correction silently vanished).
        d_ns = bc.gravity_distance_km(np.array([57.8]), np.array([11.5]), 57.7, 11.5)
        d_ew = bc.gravity_distance_km(np.array([57.7]), np.array([11.6]), 57.7, 11.5)
        assert d_ns[0] > d_ew[0]


class TestTripLengthFit:
    """RVU Västra Götaland's trip-length bins (p.12 table 2: 0-1/1.1-5/
    5.1-10/>10 km = 9/31/19/41%) were documented since 2026-07-05 as having
    replaced GEH-only scoring in calibrate_theta.py's theta search — but
    calibrate_theta.py never actually implemented that fit (confirmed
    2026-07-08 by reading its committed history: only ever GEH-based).
    This is the first real implementation."""

    def test_all_trips_in_shortest_bin_gives_shares_1_0_0(self):
        fit = bc.trip_length_fit([0.5, 0.8, 0.3])
        assert fit["shares"] == [1.0, 0.0, 0.0]
        assert fit["over_10km_pct"] == 0.0

    def test_matches_rvu_exactly_gives_zero_l1_distance(self):
        # RVU short-bin shares renormalized: 9/59, 31/59, 19/59
        lengths = [0.5] * 9 + [3.0] * 31 + [7.0] * 19   # exact real proportions
        fit = bc.trip_length_fit(lengths)
        assert fit["l1_distance"] == pytest.approx(0.0, abs=1e-6)

    def test_over_10km_trips_excluded_from_share_denominator(self):
        # 10 trips at 0.5km (short bin) + 10 trips at 50km (excluded) ->
        # shares among the SHORT bins only must still be [1.0, 0.0, 0.0],
        # not diluted by the long trips that can't occur locally.
        fit = bc.trip_length_fit([0.5] * 10 + [50.0] * 10)
        assert fit["shares"] == [1.0, 0.0, 0.0]
        assert fit["over_10km_pct"] == 50.0

    def test_empty_input_does_not_crash(self):
        fit = bc.trip_length_fit([])
        assert fit["l1_distance"] == float("inf")
        assert fit["n"] == 0

    def test_rvu_short_bin_shares_sum_to_one(self):
        assert sum(bc.RVU_SHORT_BIN_SHARES) == pytest.approx(1.0)
