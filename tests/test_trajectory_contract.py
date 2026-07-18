from traffic_sim.simulation.trajectory_contract import (
    validate_multiday_trajectory,
)


def valid_payload(days=3):
    vehicles = [
        {"d": day * 86400, "e": [0, 1],
         "x": [day * 86400 + 5, day * 86400 + 10]}
        for day in range(days)
    ]
    return {
        "n_vehicles": days,
        "n_unfinished": 0,
        "inserted_in_run": days,
        "displayed_share": 1.0,
        "sampling": {
            "enabled": True,
            "method": "sha256_vehicle_id_per_day",
            "max_vehicles_per_day": 10_000,
            "eligible_vehicles": days,
            "selected_vehicles": days,
            "per_day": [
                {"day": day + 1, "eligible": 1, "selected": 1}
                for day in range(days)
            ],
        },
        "vehicles": vehicles,
    }


def test_valid_bounded_multiday_trajectory_passes():
    assert validate_multiday_trajectory(
        valid_payload(), days=3, expected_inserted=3) == []


def test_missing_middle_day_fails_closed():
    payload = valid_payload()
    payload["vehicles"][1]["d"] = 0
    assert any("faktiska dagar" in error for error in
               validate_multiday_trajectory(payload, days=3))


def test_nonmonotonic_exit_series_fails_closed():
    payload = valid_payload()
    payload["vehicles"][0]["x"] = [10, 5]
    assert any("icke-monotona" in error for error in
               validate_multiday_trajectory(payload, days=3))


def test_per_day_cap_is_enforced():
    payload = valid_payload()
    payload["sampling"]["max_vehicles_per_day"] = 10_001
    assert any("överskrider" in error for error in
               validate_multiday_trajectory(payload, days=3))
