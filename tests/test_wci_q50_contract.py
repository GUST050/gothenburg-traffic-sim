"""The q50 monthly index cannot claim or accept stress-variant evidence."""
import pytest

from traffic_sim.simulation.window_cost_index import WindowCostIndex, WindowCostIndexError
from tools import build_window_cost_index, profile_monthly_cost_ledger
from tools import closure_effect_eligibility, freeze_wci_month_case


def test_q50_index_round_trip_and_explicit_schema():
    records = {"u": {"schedule_id": "s", "records": [
        {"demand_variant": "q50", "vehicles_affected": 2}]}}
    index = WindowCostIndex(bound_identity={"input": "a"}, records=records)
    payload = index.to_dict()
    assert payload["schema"] == "window_cost_index_q50_v2"
    assert payload["daily_variant_record_count"] == 1
    assert WindowCostIndex.from_dict(payload).lookup("u", "s")[0]["demand_variant"] == "q50"


def test_old_three_variant_index_is_rejected():
    with pytest.raises(WindowCostIndexError, match="exactly q50"):
        WindowCostIndex(bound_identity={"input": "a"}, records={"u": {
            "schedule_id": "s", "records": [
                {"demand_variant": v} for v in ("q10", "q50", "q90")]}})
    with pytest.raises(WindowCostIndexError, match="schema"):
        WindowCostIndex.from_dict({"schema": "window_cost_index_v1"})


def test_month_profile_and_wci_count_one_variant_per_unit():
    assert profile_monthly_cost_ledger.EXPECTED_VARIANTS == 1
    assert build_window_cost_index.EXPECTED_VARIANT_RECORDS == build_window_cost_index.EXPECTED_DAILY_UNITS


def test_old_profile_and_census_are_not_reinterpreted_as_q50(tmp_path):
    with pytest.raises(WindowCostIndexError, match="q50 profile schema"):
        build_window_cost_index._validate_profile_binding(
            {"schema": "monthly_cost_ledger_profile_v1"}, tmp_path / "profile.json")
    assert closure_effect_eligibility.verify_inventory({
        "schema": "closure_effect_inventory_v1",
        "policy": "closure_effect_eligibility_v1",
    }) == ["unsupported q50 inventory schema or policy"]
    assert freeze_wci_month_case.verify_registration({
        "schema": "wci_month_case_registration_v1",
    }) == ["unexpected schema: wci_month_case_registration_v1"]
