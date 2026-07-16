import numpy as np

from demand.intake import observed_sensor_series, real_day_shape, target_series


def _day(first_hour, other_hours=1):
    return [first_hour] * 4 + [other_hours] * 92


def test_two_way_total_station_is_not_double_weighted():
    shape = real_day_shape(
        {"north": _day(10), "south": _day(10), "other": _day(20)},
        {"two_way": ["north", "south"], "single": ["other"]},
        0,
    )
    # The two-way station contributes 10 and the single station 20. If the
    # two directed copies were counted independently, the first contribution
    # would incorrectly be 20.
    assert np.isclose(shape[0], 30 / 76)


def test_genuinely_directional_values_are_summed_once_per_station():
    shape = real_day_shape(
        {"a": _day(10), "b": _day(20)},
        {"station": ["a", "b"]},
        0,
    )
    assert np.isclose(shape[0], 30 / 76)


def test_audit_series_preserve_missing_values_and_total_source_values():
    targets = target_series([{"north": 4.5}, {}, {"north": 7.5}])
    observations = observed_sensor_series(
        {"north": [9, None, 15], "south": [9, None, 15]},
        {"total_station": ["north", "south"]}, 0, 3)

    assert targets == {"north": [4.5, None, 7.5]}
    # The duplicate Total is intentional evidence of one physical station;
    # the scenario UI receives metadata that labels it as such.
    assert observations == {
        "north": [9, None, 15],
        "south": [9, None, 15],
    }
