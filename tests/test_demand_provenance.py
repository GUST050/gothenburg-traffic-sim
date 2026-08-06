import json

import pytest

from traffic_sim.demand.provenance import validate_calibrated_provenance
from traffic_sim.demand.route_support import combined_route_edges, route_edges


def _artifacts(tmp_path, *, candidate_id="c0"):
    metadata = tmp_path / "candidates.meta.json"
    candidate_routes = tmp_path / "candidates.rou.xml"
    routes = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    metadata.write_text(json.dumps({
        "schema_version": 1,
        "candidates": {
            "c0": {
                "purpose": "arbete",
                "origin_edge": "A",
                "destination_edge": "C",
            }
        },
    }))
    candidate_routes.write_text(
        '<routes><vehicle id="c0" depart="7.5">'
        '<route edges="A B C"/></vehicle></routes>')
    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A B C"/></vehicle></routes>')
    agents.write_text(json.dumps({
        "schema_version": 1,
        "agents": [{
            "vehicle_id": "pfe0",
            "candidate_id": candidate_id,
            "purpose": "arbete",
            "origin_edge": "A",
            "destination_edge": "C",
            "purpose_route_compatible": True,
            "departure_s": 12.5,
        }],
    }))
    return candidate_routes, metadata, routes, agents


def test_calibrated_provenance_binds_candidate_agent_and_route(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(tmp_path)

    report = validate_calibrated_provenance(
        candidate_routes, metadata, [(routes, agents)])

    assert report["status"] == "pass"
    assert report["vehicles"] == 1
    assert report["candidate_records"] == 1


def test_calibrated_provenance_rejects_unknown_candidate(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(
        tmp_path, candidate_id="missing")

    with pytest.raises(ValueError, match="unknown candidate"):
        validate_calibrated_provenance(
            candidate_routes, metadata, [(routes, agents)])


def test_calibrated_provenance_rejects_candidate_path_substitution(tmp_path):
    candidate_routes, metadata, routes, agents = _artifacts(tmp_path)
    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A X C"/></vehicle></routes>')

    with pytest.raises(ValueError, match="route differs from candidate"):
        validate_calibrated_provenance(
            candidate_routes, metadata, [(routes, agents)])


def test_route_support_reads_every_route_and_invalidates_on_change(tmp_path):
    _candidate_routes, _metadata, routes, _agents = _artifacts(tmp_path)
    other = tmp_path / "other.rou.xml"
    other.write_text('<routes><route id="r0" edges="D E"/></routes>')

    assert route_edges(routes) == frozenset({"A", "B", "C"})
    assert combined_route_edges((routes, other)) == frozenset(
        {"A", "B", "C", "D", "E"})

    routes.write_text(
        '<routes><vehicle id="pfe0" depart="12.5">'
        '<route edges="A B C F"/></vehicle></routes>')
    assert "F" in route_edges(routes)


# ── Day-assembled windows (2026-08-06) ────────────────────────────────────────
# Regression cover for the fault that stopped the annual warming launch on its
# first demand build: a window assembled from days that each drew their own
# candidate pool, where every day reuses the "d0_" id prefix.

from traffic_sim.demand.provenance import (DAY_PROVENANCE_NAME,
                                           validate_assembled_provenance)


def _day_dir(tmp_path, name, *, vehicles=1, records=1, status="pass",
             routes=("calibrated.rou.xml",)):
    """A stored day directory carrying only its provenance proof."""
    directory = tmp_path / name
    directory.mkdir()
    (directory / DAY_PROVENANCE_NAME).write_text(json.dumps({
        "schema_version": 1,
        "status": status,
        "mode": "single_pool",
        "candidate_records": records,
        "vehicles": vehicles * len(routes),
        "variants": [{"route": route, "agents": route.replace(
            ".rou.xml", ".agents.json"), "vehicles": vehicles}
            for route in routes],
    }))
    return directory


def _assembled(tmp_path, *, agents_extra=None, n=2):
    """A window assembled from two days whose candidate ids COLLIDE."""
    routes = tmp_path / "calibrated.rou.xml"
    agents = tmp_path / "calibrated.agents.json"
    edges = ["A B C", "X Y Z"]
    routes.write_text(
        "<routes>" + "".join(
            f'<vehicle id="pfe{i}" depart="{10.0 + i}">'
            f'<route edges="{edges[i]}"/></vehicle>' for i in range(n))
        + "</routes>")
    records = []
    for i in range(n):
        first, last = edges[i].split()[0], edges[i].split()[-1]
        record = {
            "vehicle_id": f"pfe{i}",
            # Both days named their candidate "d0_1" — the collision.
            "candidate_id": "d0_1",
            "purpose": "arbete",
            "origin_edge": first,
            "destination_edge": last,
            "purpose_route_compatible": True,
            "departure_s": 10.0 + i,
        }
        record.update((agents_extra or {}).get(i, {}))
        records.append(record)
    agents.write_text(json.dumps({"schema_version": 1, "agents": records}))
    return routes, agents


def test_assembled_window_is_proven_by_its_days(tmp_path):
    days = [_day_dir(tmp_path, "day0"), _day_dir(tmp_path, "day1")]
    routes, agents = _assembled(tmp_path)

    report = validate_assembled_provenance(days, [(routes, agents)])

    assert report["status"] == "pass"
    assert report["mode"] == "assembled_day_library"
    assert report["vehicles"] == 2
    assert [day["day"] for day in report["days"]] == ["day0", "day1"]


def test_single_pool_check_is_wrong_for_a_day_assembled_window(tmp_path):
    """WHY validate_assembled_provenance exists — pin the original failure.

    Resolving an assembled window's ids against the one pool left on disk asks
    day 0's agents to resolve against day 1's candidates. This is the exact
    error the 2026-08-06 warming launch died on.
    """
    routes, agents = _assembled(tmp_path)
    # Only the LAST day's pool survives the window loop, and it does not
    # contain day 0's "d0_1".
    metadata = tmp_path / "last_day.meta.json"
    metadata.write_text(json.dumps({
        "schema_version": 1,
        "candidates": {"d0_2": {"purpose": "arbete", "origin_edge": "X",
                                "destination_edge": "Z"}},
    }))
    pool = tmp_path / "last_day.rou.xml"
    pool.write_text('<routes><vehicle id="d0_2" depart="0.0">'
                    '<route edges="X Y Z"/></vehicle></routes>')

    with pytest.raises(ValueError, match="unknown candidate"):
        validate_calibrated_provenance(pool, metadata, [(routes, agents)])


def test_assembled_window_refuses_a_day_missing_its_proof(tmp_path):
    days = [_day_dir(tmp_path, "day0"), tmp_path / "unproven"]
    (tmp_path / "unproven").mkdir()
    routes, agents = _assembled(tmp_path)

    with pytest.raises(ValueError, match="day provenance record is missing"):
        validate_assembled_provenance(days, [(routes, agents)])


def test_assembled_window_refuses_a_day_whose_proof_did_not_pass(tmp_path):
    days = [_day_dir(tmp_path, "day0"),
            _day_dir(tmp_path, "day1", status="fail")]
    routes, agents = _assembled(tmp_path)

    with pytest.raises(ValueError, match="did not pass"):
        validate_assembled_provenance(days, [(routes, agents)])


def test_assembled_window_refuses_a_vehicle_count_mismatch(tmp_path):
    """The binding that makes per-day proofs cover the published window."""
    days = [_day_dir(tmp_path, "day0"), _day_dir(tmp_path, "day1")]
    routes, agents = _assembled(tmp_path, n=1)   # a vehicle went missing

    with pytest.raises(ValueError, match="day\\(s\\) proved"):
        validate_assembled_provenance(days, [(routes, agents)])


def test_assembled_window_still_catches_a_corrupt_agent(tmp_path):
    """Dropping the candidate cross-reference must not drop everything else."""
    days = [_day_dir(tmp_path, "day0"), _day_dir(tmp_path, "day1")]
    routes, agents = _assembled(
        tmp_path, agents_extra={1: {"destination_edge": "WRONG"}})

    with pytest.raises(ValueError, match="endpoints differ"):
        validate_assembled_provenance(days, [(routes, agents)])


def test_assembled_window_refuses_an_agent_with_no_candidate_id(tmp_path):
    days = [_day_dir(tmp_path, "day0"), _day_dir(tmp_path, "day1")]
    routes, agents = _assembled(tmp_path, agents_extra={0: {"candidate_id": ""}})

    with pytest.raises(ValueError, match="carries no candidate id"):
        validate_assembled_provenance(days, [(routes, agents)])


def test_assembled_window_refuses_an_uncovered_variant(tmp_path):
    days = [_day_dir(tmp_path, "day0"), _day_dir(tmp_path, "day1")]
    routes, agents = _assembled(tmp_path)
    other = tmp_path / "calibrated_v1.rou.xml"
    other.write_text(routes.read_text())
    other_agents = tmp_path / "calibrated_v1.agents.json"
    other_agents.write_text(agents.read_text())

    with pytest.raises(ValueError, match="no day provenance covers variant"):
        validate_assembled_provenance(days, [(other, other_agents)])
