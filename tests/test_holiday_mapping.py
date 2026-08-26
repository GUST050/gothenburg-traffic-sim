"""The 2027 forecast borrows each holiday's measured 2025 profile.

The table's own rule is "same holiday TYPE, not same calendar date", and the
weekday baseline is applied separately underneath it. Two ways that can go
wrong silently, both of which it did:

* a 2027 date mapped to a 2025 date of a DIFFERENT holiday — Midsommardagen
  (Sat) was mapped onto Midsommarafton (Fri), so a Saturday received a
  Friday's holiday factor;
* a holiday simply missing from the 2027 side — Midsommarafton, the largest
  traffic anomaly of the Swedish summer, was forecast as an ordinary Friday.

Neither raises. Both change every number the forecast produces for those
dates, so they are pinned here.
"""
from __future__ import annotations

import datetime

import pytest

from build_agent1_flows import HOLIDAY_MAPPING_2027_TO_2025 as MAPPING
from train_agent1 import HOLIDAY_DATES_2025


def _date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(str(value))


class TestTheTableIsWellFormed:
    def test_every_key_is_a_2027_date_and_every_value_a_2025_date(self):
        for source, target in MAPPING.items():
            assert _date(source).year == 2027, source
            assert _date(target).year == 2025, target

    def test_every_target_is_a_holiday_the_model_measured(self):
        # The factor is read from the 2025 holiday profile. A target outside
        # that list has no factor to lend.
        measured = {str(day) for day in HOLIDAY_DATES_2025}
        missing = sorted(t for t in MAPPING.values() if t not in measured)
        assert not missing, f"mapped to non-holidays: {missing}"

    def test_no_two_holidays_borrow_the_same_profile(self):
        # Two 2027 dates sharing one 2025 factor means one of them is
        # mislabelled — exactly how the midsummer defect would reappear.
        seen: dict[str, str] = {}
        for source, target in sorted(MAPPING.items()):
            assert target not in seen, (
                f"{source} and {seen[target]} both borrow {target}")
            seen[target] = source


class TestMidsummer:
    """Sweden's midsummer holiday is the EVE, on both sides of the mapping."""

    def test_the_eve_is_mapped_and_is_a_friday_on_both_sides(self):
        assert "2027-06-25" in MAPPING, (
            "Midsommarafton 2027 has no holiday factor and would be "
            "forecast as an ordinary Friday")
        target = MAPPING["2027-06-25"]
        assert target == "2025-06-20"
        assert _date("2027-06-25").weekday() == _date(target).weekday() == 4

    def test_midsummer_day_is_not_given_the_eve_s_profile(self):
        # 2025 carries no Midsommardagen factor, so 2027's must not invent
        # one by borrowing the eve's.
        assert "2027-06-26" not in MAPPING
        assert "2025-06-21" not in {str(day) for day in HOLIDAY_DATES_2025}

    def test_the_eve_is_the_saturday_rule_applied_correctly(self):
        # Midsommardagen is the Saturday falling 20-26 June; the eve is the
        # Friday before it. Derived here rather than asserted as a constant,
        # so the test still means something for another year.
        for year, eve in ((2027, "2027-06-25"), (2025, "2025-06-20")):
            day = next(d for d in (datetime.date(year, 6, n) for n in range(20, 27))
                       if d.weekday() == 5)
            assert day - datetime.timedelta(days=1) == _date(eve)


class TestTheOtherEves:
    @pytest.mark.parametrize("source,target", [("2027-12-24", "2025-12-24"),
                                               ("2027-12-31", "2025-12-31")])
    def test_christmas_and_new_year_eves_are_mapped(self, source, target):
        # They always were. Midsummer's absence was the outlier, and this
        # states the convention the fix restored.
        assert MAPPING[source] == target


class TestTheEasterCycle:
    @pytest.mark.parametrize("source", ["2027-03-26", "2027-03-28",
                                        "2027-03-29", "2027-05-06",
                                        "2027-05-16"])
    def test_moveable_feasts_keep_their_weekday(self, source):
        # A moveable feast is defined BY its weekday, so a mapping that
        # moves it has picked the wrong ecclesiastical day.
        assert _date(source).weekday() == _date(MAPPING[source]).weekday()
