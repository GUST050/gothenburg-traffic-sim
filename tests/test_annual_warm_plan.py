import copy
import hashlib
import json
from datetime import datetime

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.annual_warm_plan import (
    AnnualWarmCoverageSpec,
    AnnualWarmPlanIndex,
    PLAN_STATUS,
    SEED_VARIANTS,
    build_annual_warm_plan,
    default_2027_coverage_spec,
    iter_work_units,
    load_annual_warm_plan,
    validate_annual_warm_plan,
    verify_fingerprint_inputs,
    write_annual_warm_plan,
)


def _fingerprints():
    return {
        "forecast": {"path": "forecast.json", "bytes": 1, "sha256": "1" * 64},
        "network": {"path": "net.xml", "bytes": 2, "sha256": "2" * 64},
    }


def _runtime():
    return {
        "sumo_version": "SUMO test",
        "platform": "test-platform",
        "simulation_mode": "meso",
        "metric_schema": "closure_decision_metrics_v1",
    }


@pytest.fixture(scope="module")
def plan():
    return build_annual_warm_plan(
        default_2027_coverage_spec(), input_fingerprints=_fingerprints(),
        runtime_identity=_runtime(),
    )


def _content_key(payload):
    body = {key: value for key, value in payload.items() if key != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def test_2027_plan_covers_the_complete_full_day_contract(plan):
    assert plan["status"] == PLAN_STATUS
    assert plan["coverage"] == {
        "calendar_days": 365,
        "daily_clock_slots": 96,
        "possible_daily_intervals": 1_699_440,
        "supported_daily_intervals": 1_682_634,
        "unavailable_daily_intervals": 16_806,
        "unavailable_reasons": {
            "excluded_dst_envelope": 14_308,
            "source_year_boundary": 2_498,
        },
        "checkpoint_requests": 34_895,
        "demand_builds": 367,
        "state_requests": 104_685,
    }
    coverage = plan["coverage_spec"]
    assert (coverage["earliest_start"], coverage["latest_end"]) == (
        "00:00", "24:00"
    )
    assert coverage["maximum_daily_duration_minutes"] == 1440


def test_plan_is_candidate_free_independent_and_default_off(plan):
    serialized = json.dumps(plan, sort_keys=True)
    assert "directed_edges" not in serialized
    assert plan["safety"]["road_scope"] == "none_calendar_and_demand_only"
    assert plan["safety"]["interday_policy"] == "independent_daily_reset_v1"
    assert plan["safety"]["activation"] == "none"
    assert plan["safety"]["accuracy_authority"] == \
        "unchanged full SUMO cold execution"
    assert plan["seed_variants"] == [
        {"seed": seed, "variant": variant} for seed, variant in SEED_VARIANTS
    ]


def test_canonical_archives_collapse_the_annual_download_domain(plan):
    demands = {item["build_key"]: item for item in plan["demand_build_specs"]}
    assert len(demands) == 367
    assert all(item["source"] == "forecast" for item in demands.values())
    assert {item["days"] for item in demands.values()} == {1, 2, 3}
    assert all(
        request["demand_build_key"] in demands
        for request in plan["checkpoint_requests"]
    )
    for request in plan["checkpoint_requests"]:
        checkpoint = datetime.fromisoformat(request["checkpoint_time"])
        closure = datetime.fromisoformat(request["closure_start"])
        assert (closure - checkpoint).total_seconds() == 900
        assert request["checkpoint_s"] > 0
        assert request["checkpoint_s"] % 900 == 0


def test_work_units_are_stable_unique_request_seed_identities(plan):
    units = list(iter_work_units(plan))
    assert len(units) == plan["coverage"]["state_requests"]
    assert len({item["unit_id"] for item in units}) == len(units)
    assert {(item["seed"], item["variant"]) for item in units} == \
        set(SEED_VARIANTS)
    assert {item["state"] for item in units} == {"pending"}


def _road_search(edge, *, year=2027, required_work_minutes=480):
    return ClosureSearchSpec(
        search_id=f"road-{year}-{required_work_minutes}",
        directed_edges=(edge,),
        demand_build_id="resolved-later",
        permitted_date_start=f"{year}-01-01",
        permitted_date_end=f"{year}-12-31",
        required_work_minutes=required_work_minutes,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("07:00", "15:30"),
        source="forecast",
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )


def test_index_resolves_different_roads_to_one_candidate_free_request(plan):
    index = AnnualWarmPlanIndex(plan)
    schedules = [
        next(
            item for item in generate_closure_schedules(_road_search(edge))
            if item.first_work_date == "2027-06-14" and item.daily_start == "07:15"
        )
        for edge in ("first_road_0", "different_road_0")
    ]
    first, second = map(index.resolve_schedule, schedules)
    assert index.request_count == 34_895
    assert first == second
    assert first["request"]["checkpoint_time"] == "2027-06-14T07:00:00"
    assert first["safety"]["activation"] == "none"
    assert "first_road_0" not in json.dumps(first)


def test_index_fails_cold_outside_year(plan):
    index = AnnualWarmPlanIndex(plan)
    outside = generate_closure_schedules(_road_search("edge_0", year=2028))[0]
    assert index.resolve_schedule(outside) is None


def test_plan_recomposes_round_trips_and_rejects_tampering(plan, tmp_path):
    assert plan["content_key"] == _content_key(plan)
    assert validate_annual_warm_plan(plan) == plan
    tampered = copy.deepcopy(plan)
    tampered["checkpoint_requests"][0]["checkpoint_s"] += 900
    tampered["content_key"] = _content_key(tampered)
    with pytest.raises(ValueError, match="recompose"):
        validate_annual_warm_plan(tampered)
    path = tmp_path / "annual.json"
    write_annual_warm_plan(path, plan)
    assert load_annual_warm_plan(path) == plan


def test_bound_inputs_are_rechecked_against_live_bytes(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("before")
    recorded = {
        "source": {
            "path": "source.py",
            "bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    }
    verify_fingerprint_inputs(recorded, base=tmp_path)
    source.write_text("after")
    with pytest.raises(ValueError, match="changed during population"):
        verify_fingerprint_inputs(recorded, base=tmp_path)


@pytest.mark.parametrize("year", [1999, 9999])
def test_invalid_year_bounds_fail_before_enumeration(year):
    with pytest.raises(ValueError, match="year"):
        AnnualWarmCoverageSpec(year=year)
