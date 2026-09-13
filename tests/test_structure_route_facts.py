"""Step 2: compute geometry facts once per unique route, never per vehicle.

Deduplication applies to CALCULATIONS only. Every vehicle keeps its own
observation, in document order, so counts, medians, shares and per-quarter
fields are the ones the report has always produced.
"""
import json
from pathlib import Path

import pytest

from demand import structure as dstructure

KM_PER_DEGREE_LAT = dstructure.KLAT_M / 1000.0
BASE_LAT = 57.700


def _lat_for(km: float) -> float:
    """A latitude exactly ``km`` north of the origin edge."""
    return BASE_LAT + km / KM_PER_DEGREE_LAT


def _feature(edge_id: str, lat: float, sensor: str | None = None) -> dict:
    properties = {"id": edge_id}
    if sensor:
        properties["sensor_id"] = sensor
    return {"type": "Feature", "properties": properties,
            "geometry": {"type": "LineString",
                         "coordinates": [[11.97, lat], [11.971, lat]]}}


def geometry(tmp_path: Path, *, sensors=("S",)) -> Path:
    path = tmp_path / "network.geojson"
    features = [
        _feature("O", BASE_LAT),
        _feature("S", _lat_for(0.5), sensor="s1" if "S" in sensors else None),
        _feature("T", _lat_for(0.75), sensor="s2" if "T" in sensors else None),
        _feature("D09", _lat_for(0.9)),
        _feature("D1", _lat_for(1.0)),
        _feature("D5", _lat_for(5.0)),
        _feature("D10", _lat_for(10.0)),
        _feature("FAR", _lat_for(30.0)),
    ]
    path.write_text(json.dumps({"features": features}))
    return path


def route_file(tmp_path: Path, vehicles, *, name="calibrated.rou.xml",
               named_routes=None) -> Path:
    body = "".join(
        f'<route id="{key}" edges="{edges}"/>' for key, edges in (named_routes or {}).items())
    for index, (edges, depart) in enumerate(vehicles):
        if edges is None:
            body += f'<vehicle id="v{index}" depart="{depart}" route="shared"/>'
        else:
            body += (f'<vehicle id="v{index}" depart="{depart}">'
                     f'<route edges="{edges}"/></vehicle>')
    path = tmp_path / name
    path.write_text(f"<routes>{body}</routes>")
    return path


def agents_for(route_path: Path, records) -> Path:
    path = route_path.with_name(route_path.name.replace(".rou.xml", ".agents.json"))
    path.write_text(json.dumps({"agents": list(records)}))
    return path


class TestReportEquivalence:
    def test_the_report_is_value_identical_with_and_without_a_context(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D5", 0.0), ("O S D5", 1000.0),
                                      ("O D1", 2000.0), ("O S T D10", 3000.0)])
        agents_for(route, [
            {"purpose": "arbete", "origin_edge": "O", "destination_edge": "D5"},
            {"purpose": "arbete", "origin_edge": "O", "destination_edge": "D5"},
            {"purpose": "fritid", "origin_edge": "O", "destination_edge": "D1"},
            {"purpose": "fritid", "origin_edge": "O", "destination_edge": "D10"}])

        plain = dstructure.calibrated_structure_report(route)
        shared = dstructure.calibrated_structure_report(
            route, _context=dstructure.structure_context())

        assert json.dumps(shared, sort_keys=True) == json.dumps(plain, sort_keys=True)

    def test_pool_comparison_and_flags_are_identical(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D1", 0.0)] * 8 + [("O D10", 900.0)] * 2)
        agents_for(route, [{"purpose": "arbete", "origin_edge": "O",
                            "destination_edge": "D1"}] * 8
                   + [{"purpose": "fritid", "origin_edge": "O",
                       "destination_edge": "D10"}] * 2)
        pool = route_file(tmp_path, [("O D10", 0.0)] * 5 + [("O S D1", 900.0)] * 5,
                          name="candidates.rou.xml")

        plain = dstructure.calibrated_structure_report(route, pool_path=pool)
        shared = dstructure.calibrated_structure_report(
            route, pool_path=pool, _context=dstructure.structure_context())

        assert json.dumps(shared, sort_keys=True) == json.dumps(plain, sort_keys=True)
        assert shared["structure_flags"] == plain["structure_flags"]


class TestRouteForms:
    def test_named_and_inline_routes_give_the_same_report(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        inline = route_file(tmp_path, [("O S D5", 0.0), ("O S D5", 900.0)])
        named = route_file(tmp_path, [(None, 0.0), (None, 900.0)],
                           name="named.rou.xml",
                           named_routes={"shared": "O S D5"})

        assert dstructure.calibrated_structure_report(named) \
            == dstructure.calibrated_structure_report(inline)

    def test_an_unresolvable_named_reference_still_fails_closed(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        broken = route_file(tmp_path, [(None, 0.0)], named_routes={"other": "O S"})

        with pytest.raises(ValueError, match="no resolvable route"):
            dstructure.calibrated_structure_report(broken)


class TestObservationsAreNeverDeduplicated:
    def test_identical_routes_keep_one_observation_per_vehicle(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D5", 0.0)] * 7)

        report = dstructure.calibrated_structure_report(route)

        assert report["trip_length_fit"]["n"] == 7
        # Counter keys exist only where observed; seven vehicles, one passage.
        assert report["sensor_passages"] == {"1": 7}
        assert report["dest_sensor_proximity"]["n"] == 7

    def test_identical_routes_in_different_quarters_are_counted_apart(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        # 0.9 km: comfortably under the 1 km edge, whose exact boundary the
        # formula's floating point decides (see the bin-edge test).
        route = route_file(tmp_path, [("O D09", 0.0), ("O D09", 900.0),
                                      ("O D09", 1800.0), ("O D09", 1801.0)])

        fit = dstructure.calibrated_structure_report(route)["trip_length_fit"]

        assert fit["quarter_totals"] == {0: 1, 1: 1, 2: 2}
        assert fit["under_1km_by_quarter"] == {0: 1, 1: 1, 2: 2}

    def test_identical_routes_with_different_purposes_stay_separate(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D5", 0.0)] * 4)
        agents_for(route, [
            {"purpose": "arbete", "origin_edge": "O", "destination_edge": "D5"},
            {"purpose": "arbete", "origin_edge": "O", "destination_edge": "D5"},
            {"purpose": "fritid", "origin_edge": "O", "destination_edge": "D1"},
            {"purpose": "fritid", "origin_edge": "O", "destination_edge": "D10"}])

        lengths = dstructure.calibrated_structure_report(route)["purpose_length_km"]

        assert lengths["arbete"]["n"] == 2 and lengths["fritid"]["n"] == 2
        assert lengths["arbete"]["median_km"] == 5.0
        assert lengths["fritid"]["median_km"] == 10.0


class TestRouteGeometryFacts:
    def test_revisits_to_one_sensor_are_counted_every_time(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S O S D5", 0.0)])

        report = dstructure.calibrated_structure_report(route)

        assert report["sensor_passages"] == {"2": 1}

    def test_a_loop_uses_the_last_sensor_position_for_onward_distance(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        looping = route_file(tmp_path, [("O S D5 S D1", 0.0)])
        trailing = route_file(tmp_path, [("O S D1", 0.0)], name="trailing.rou.xml")

        loop_report = dstructure.calibrated_structure_report(looping)
        trail_report = dstructure.calibrated_structure_report(trailing)

        assert loop_report["onward_after_last_sensor"]["median_m"] \
            == trail_report["onward_after_last_sensor"]["median_m"]

    @pytest.mark.parametrize("km,expected_bin_index", [
        (0.999, 0), (1.001, 1), (4.999, 1), (5.001, 2),
        (9.999, 2), (10.001, 3)])
    def test_the_one_five_and_ten_km_bin_edges_keep_their_sides(
            self, tmp_path, monkeypatch, km, expected_bin_index):
        """The bin rule is ``km <= edge``; pin which side each length lands on.

        An exactly-on-the-edge length is not assertable here: a latitude that
        should be 5.000 km away computes as 5.000000000000376, so the edge case
        is decided by the formula's own floating point. That is existing
        behaviour and must not change, so the test brackets it instead of
        pretending the boundary is exact.
        """
        path = tmp_path / "network.geojson"
        path.write_text(json.dumps({"features": [
            _feature("O", BASE_LAT), _feature("X", _lat_for(km))]}))
        monkeypatch.setattr(dstructure, "GEO_PATH", path)
        route = route_file(tmp_path, [("O X", 0.0)])
        agents_for(route, [{"purpose": "arbete", "origin_edge": "O",
                            "destination_edge": "X"}])

        bins = dstructure.calibrated_structure_report(route)["purpose_length_bins"]

        shares = bins["arbete"]["shares"]
        assert shares[expected_bin_index] == 1.0
        assert sum(shares) == 1.0


class TestContextIdentity:
    def test_changed_geometry_bytes_are_not_reused(self, tmp_path, monkeypatch):
        path = geometry(tmp_path)
        monkeypatch.setattr(dstructure, "GEO_PATH", path)
        context = dstructure.structure_context()
        route = route_file(tmp_path, [("O D5", 0.0)])
        first = dstructure.calibrated_structure_report(route, _context=context)

        # Move it into a DIFFERENT length bin, so a stale reuse is visible.
        moved = json.loads(path.read_text())
        for feature in moved["features"]:
            if feature["properties"]["id"] == "D5":
                feature["geometry"]["coordinates"] = [[11.97, _lat_for(0.5)],
                                                      [11.971, _lat_for(0.5)]]
        path.write_text(json.dumps(moved))

        second = dstructure.calibrated_structure_report(route, _context=context)

        assert second["trip_length_fit"]["shares"] != first["trip_length_fit"]["shares"]
        assert second == dstructure.calibrated_structure_report(route)

    def test_a_changed_sensor_set_is_not_reused(self, tmp_path, monkeypatch):
        path = geometry(tmp_path)
        monkeypatch.setattr(dstructure, "GEO_PATH", path)
        context = dstructure.structure_context()
        route = route_file(tmp_path, [("O S T D5", 0.0)])
        assert dstructure.calibrated_structure_report(
            route, _context=context)["sensor_passages"] == {"1": 1}

        widened = json.loads(path.read_text())
        for feature in widened["features"]:
            if feature["properties"]["id"] == "T":
                feature["properties"]["sensor_id"] = "s2"
        path.write_text(json.dumps(widened))

        report = dstructure.calibrated_structure_report(route, _context=context)

        assert report["sensor_passages"] == {"2": 1}

    def test_the_context_is_bound_to_content_not_mtime_or_size(
            self, tmp_path, monkeypatch):
        path = geometry(tmp_path)
        monkeypatch.setattr(dstructure, "GEO_PATH", path)
        context = dstructure.structure_context()

        assert len(context.geometry_sha256) == 64
        assert len(context.sensor_identity) == 64
        assert context.is_current()


class TestFailClosed:
    def test_a_missing_route_file_still_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))

        assert dstructure.calibrated_structure_report(tmp_path / "absent.rou.xml") is None

    def test_missing_geometry_still_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", tmp_path / "absent.geojson")
        route = route_file(tmp_path, [("O D5", 0.0)])

        assert dstructure.calibrated_structure_report(route) is None

    def test_a_malformed_agent_sidecar_still_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O D5", 0.0)])
        route.with_name("calibrated.agents.json").write_text("{not json")

        with pytest.raises(ValueError):
            dstructure.calibrated_structure_report(route)


class TestWorkIsProportionalToUniqueRoutes:
    def _count_distance_calls(self, monkeypatch):
        calls = []
        real = dstructure.gravity_distance_km

        def spy(lats, lons, lat0, lon0):
            calls.append(1)
            return real(lats, lons, lat0, lon0)

        monkeypatch.setattr(dstructure, "gravity_distance_km", spy)
        return calls

    def test_many_vehicles_on_one_route_cost_one_route_calculation(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        few = route_file(tmp_path, [("O S D5", 0.0)] * 4)
        many = route_file(tmp_path, [("O S D5", float(i)) for i in range(40)],
                          name="many.rou.xml")

        dstructure.calibrated_structure_report(few)
        calls = self._count_distance_calls(monkeypatch)
        dstructure.calibrated_structure_report(many)

        # 40 vehicles, one distinct route: the per-route work must not scale
        # with the population.
        assert len(calls) < 40

    def test_the_pool_is_measured_once_per_operation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D5", 0.0)])
        candidate = route_file(tmp_path, [("O S D1", 0.0)], name="candidate.rou.xml")
        pool = route_file(tmp_path, [("O D10", 0.0)] * 3, name="candidates.rou.xml")
        parsed = []
        real = dstructure._parse_route_file

        def spy(path):
            parsed.append(Path(path))
            return real(path)

        monkeypatch.setattr(dstructure, "_parse_route_file", spy)
        context = dstructure.structure_context()

        dstructure.calibrated_structure_report(route, pool_path=pool, _context=context)
        dstructure.calibrated_structure_report(candidate, pool_path=pool, _context=context)

        assert parsed.count(pool) == 1

    def test_source_and_candidate_never_share_mutable_report_data(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
        route = route_file(tmp_path, [("O S D5", 0.0)])
        candidate = route_file(tmp_path, [("O S D5", 0.0)], name="candidate.rou.xml")
        pool = route_file(tmp_path, [("O D10", 0.0)], name="candidates.rou.xml")
        context = dstructure.structure_context()

        first = dstructure.calibrated_structure_report(
            route, pool_path=pool, _context=context)
        second = dstructure.calibrated_structure_report(
            candidate, pool_path=pool, _context=context)

        assert first is not second
        assert first["pool"] is not second["pool"]
        assert first["trip_length_fit"] is not second["trip_length_fit"]
        first["pool"]["sensor_passages"]["0"] = 9999
        assert second["pool"]["sensor_passages"]["0"] != 9999


def test_automatic_passage_output_is_unchanged_by_the_context(tmp_path, monkeypatch):
    """The acceptance gate: same selection, routes, agents and solver request."""
    import sys
    from traffic_sim.demand import automatic_passage as auto
    sys.path.insert(0, 'tests')
    from tests.test_automatic_passage import fixture

    inputs, reports, network, _calls = fixture(tmp_path, monkeypatch)
    result = auto.refine_variants(inputs, reports, network, tmp_path / 'evidence')

    record = result['']['passage_calibration']
    assert record['status'] == 'validated'
    assert record['selection_sha256'] and record['routes_sha256']
    assert record['agents_sha256']
    assert record['validation']['candidate_absolute_error'] == 0


def test_one_context_measures_the_pool_once_across_a_whole_operation(
        tmp_path, monkeypatch):
    monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
    pool = route_file(tmp_path, [("O D10", 0.0)] * 3, name="candidates.rou.xml")
    routes = [route_file(tmp_path, [("O S D5", 0.0)], name=f"stage{i}.rou.xml")
              for i in range(4)]
    parsed = []
    real = dstructure._parse_route_file
    monkeypatch.setattr(dstructure, "_parse_route_file",
                        lambda path: (parsed.append(Path(path)), real(path))[1])
    context = dstructure.structure_context()

    for route in routes:
        dstructure.calibrated_structure_report(route, pool_path=pool,
                                               _context=context)

    assert parsed.count(pool) == 1
    assert sorted(parsed.count(route) for route in routes) == [1, 1, 1, 1]


def test_the_network_baseline_is_computed_once_per_context(tmp_path, monkeypatch):
    """The repeated work was network-wide, not only per vehicle."""
    monkeypatch.setattr(dstructure, "GEO_PATH", geometry(tmp_path))
    first = route_file(tmp_path, [("O S D5", 0.0)], name="a.rou.xml")
    second = route_file(tmp_path, [("O S D1", 0.0)], name="b.rou.xml")
    context = dstructure.structure_context()

    dstructure.calibrated_structure_report(first, _context=context)
    calls = []
    real = dstructure.gravity_distance_km
    monkeypatch.setattr(dstructure, "gravity_distance_km",
                        lambda *args: (calls.append(1), real(*args))[1])
    report = dstructure.calibrated_structure_report(second, _context=context)

    # The second report reuses the baseline; it cannot cost one distance call
    # per edge in the network again.
    assert len(calls) < len(context.edge_latlon)
    assert report["dest_sensor_proximity"]["baseline_pct_within"] is not None
