"""Monthly median-only execution retains explicit legacy compatibility."""
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.deterministic_disruption import (
    DailyCostCache, DailyCostCacheCorrupt, DisruptionUnavailable, sum_daily_disruption,
)
from traffic_sim.simulation.monthly_search import MonthlySearchPolicy


def record(variant="q50"):
    return {"demand_variant": variant, "vehicles_affected": 2,
            "vehicles_no_detour": 0, "added_vehicle_hours": 3.0,
            "added_metres_total": 2.0}


def test_current_policy_defaults_are_median_only():
    policy = MonthlySearchPolicy.from_dict(json.loads(Path(
        "validation/monthly_search_policy_v5_q50_only.json").read_text()))
    assert policy.pilot.variants == ("q50",)
    assert policy.finalist.variants == ("q50",)


def test_daily_cache_roundtrips_median_only(tmp_path):
    cache = DailyCostCache(tmp_path)
    records = (record(),)
    cache.store({"scope": "q50"}, records)
    assert cache.load({"scope": "q50"}) == records


def test_sum_accepts_median_and_rejects_mixed_scopes():
    result = sum_daily_disruption([(record(),), (record(),)])
    assert [r["demand_variant"] for r in result] == ["q50"]
    assert result[0]["vehicles_affected"] == 4
    with pytest.raises(DisruptionUnavailable):
        sum_daily_disruption([(record(),), tuple(record(v) for v in ("q10", "q50", "q90"))])


def test_daily_cache_rejects_median_record_under_stress_identity(tmp_path):
    cache = DailyCostCache(tmp_path)
    identity = {"demand": {"variant_routes": {v: {} for v in ("q10", "q50", "q90")}}}
    cache.store(identity, (record(),))
    with pytest.raises(DailyCostCacheCorrupt, match="scope"):
        cache.load(identity)
