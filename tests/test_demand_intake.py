import numpy as np

from demand.intake import real_day_shape


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
