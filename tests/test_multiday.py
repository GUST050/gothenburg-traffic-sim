from traffic_sim.simulation.multiday import aggregate, parse_summary, validate


def _summary(path):
    path.write_text(
        '<summary>'
        '<step time="0" loaded="0" inserted="0" running="0" waiting="0" '
        'teleports="0" collisions="0" halting="0"/>'
        '<step time="86400" loaded="10" inserted="10" running="2" waiting="1" '
        'teleports="1" collisions="0" halting="1"/>'
        '<step time="172800" loaded="25" inserted="25" running="0" waiting="0" '
        'teleports="1" collisions="0" halting="0"/>'
        '</summary>')


def test_parse_summary_uses_cumulative_deltas_and_boundary_snapshots(tmp_path):
    payload = parse_summary(tmp_path / "summary.xml",
                            day_boundaries_s=[0, 86400, 172800], days=2)
    assert payload["complete"] is False

    path = tmp_path / "summary.xml"
    _summary(path)
    payload = parse_summary(path, day_boundaries_s=[0, 86400, 172800], days=2)
    assert payload["complete"] is True
    assert payload["days"][0]["loaded_delta"] == 10
    assert payload["days"][1]["loaded_delta"] == 15
    assert payload["days"][0]["teleports_delta"] == 1
    assert payload["days"][0]["waiting_at_boundary"] == 1
    assert payload["days"][0]["loaded_at_boundary"] == 10
    assert payload["days"][0]["inserted_at_boundary"] == 10
    assert payload["days"][0]["boundary_lag_s"] == 0.0


def test_aggregate_and_validate_require_every_seed_and_day():
    one = {"complete": True, "days": [
        {"day": 1, "loaded_delta": 10, "inserted_delta": 10,
         "teleports_delta": 0, "waiting_at_boundary": 2,
         "loaded_at_boundary": 10, "inserted_at_boundary": 10,
         "boundary_lag_s": 0},
        {"day": 2, "loaded_delta": 12, "inserted_delta": 12,
         "teleports_delta": 1, "waiting_at_boundary": 0,
         "loaded_at_boundary": 22, "inserted_at_boundary": 22,
         "boundary_lag_s": 0},
    ]}
    payload = aggregate([one, one], days=2,
                        day_boundaries_s=[0, 86400, 172800])
    assert payload["complete"] is True
    assert payload["per_day"][1]["loaded_delta_min"] == 12
    assert validate(payload, days=2,
                    day_boundaries_s=[0, 86400, 172800]) == []

    payload["seeds"].pop()
    assert validate(payload, days=2,
                    day_boundaries_s=[0, 86400, 172800])


def test_validate_rejects_empty_day_even_when_schema_is_complete():
    seed = {"complete": True, "days": [
        {"day": 1, "loaded_delta": 0, "inserted_delta": 0,
         "teleports_delta": 0, "loaded_at_boundary": 0,
         "inserted_at_boundary": 0, "boundary_lag_s": 0},
    ]}
    payload = aggregate([seed], days=1, day_boundaries_s=[0, 86400])

    errors = validate(payload, days=1, day_boundaries_s=[0, 86400])

    assert any("ingen laddad demand" in error for error in errors)


def test_validate_recomputes_aggregate_from_each_seed_boundary_evidence():
    seed = {"complete": True, "days": [
        {"day": 1, "loaded_delta": 10, "inserted_delta": 10,
         "teleports_delta": 0, "loaded_at_boundary": 10,
         "inserted_at_boundary": 10, "boundary_lag_s": 0},
    ]}
    payload = aggregate([seed], days=1, day_boundaries_s=[0, 86400])
    payload["per_day"][0]["loaded_delta_min"] = 999

    errors = validate(payload, days=1, day_boundaries_s=[0, 86400])

    assert any("inkonsekvent loaded_delta_min" in error for error in errors)


def test_validate_allows_midnight_insertion_carryover():
    """A queue at day one can drain after midnight without losing demand."""
    seed = {"complete": True, "days": [
        {"day": 1, "loaded_delta": 10, "inserted_delta": 8,
         "teleports_delta": 0, "loaded_at_boundary": 10,
         "inserted_at_boundary": 8, "boundary_lag_s": 0},
        {"day": 2, "loaded_delta": 10, "inserted_delta": 12,
         "teleports_delta": 0, "loaded_at_boundary": 20,
         "inserted_at_boundary": 20, "boundary_lag_s": 0},
    ]}
    payload = aggregate([seed], days=2,
                        day_boundaries_s=[0, 86400, 172800])

    assert validate(payload, days=2,
                    day_boundaries_s=[0, 86400, 172800]) == []
