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


def test_aggregate_and_validate_require_every_seed_and_day():
    one = {"complete": True, "days": [
        {"loaded_delta": 10, "inserted_delta": 10, "teleports_delta": 0,
         "waiting_at_boundary": 2},
        {"loaded_delta": 12, "inserted_delta": 12, "teleports_delta": 1,
         "waiting_at_boundary": 0},
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

