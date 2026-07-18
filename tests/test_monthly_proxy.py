from datetime import datetime, timedelta

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.simulation.monthly_proxy import (
    DetourPath,
    ShortlistPolicy,
    plausible_detour_paths,
    rank_schedules,
    score_schedule,
    stratified_shortlist,
)


def _search(**overrides):
    values = {
        "search_id": "proxy-april",
        "directed_edges": ("closed",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-04-05",
        "permitted_date_end": "2027-04-06",
        "required_work_minutes": 60,
        "max_consecutive_start_days": 1,
        "permitted_daily_band": DailyTimeBand("08:00", "11:00"),
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _series(epoch, values_by_time, *, days=2, default=1.0):
    values = [default] * (days * 96)
    for when, value in values_by_time.items():
        index = int(
            (datetime.fromisoformat(when) - datetime.fromisoformat(epoch))
            .total_seconds()
            // 900
        )
        values[index] = value
    return values


def _path():
    return DetourPath(
        path_id="detour-one",
        edges=("upstream", "alternate", "downstream"),
        freeflow_s=30.0,
        boundary_pair=("upstream", "downstream"),
    )


def _score(schedule, *, closed=None, alternate=None, sources=None,
           confidence=None, road_domain_status="in_domain", paths=None):
    epoch = "2027-04-05T00:00:00"
    projected = {
        "closed": closed or _series(epoch, {}),
        "upstream": _series(epoch, {}),
        "alternate": alternate or _series(epoch, {}),
        "downstream": _series(epoch, {}),
    }
    return score_schedule(
        schedule,
        closed_edges=("closed",),
        projected_flows=projected,
        flow_epoch=epoch,
        interval_minutes=15,
        detour_paths=(_path(),) if paths is None else paths,
        capacity_veh_per_interval={
            "upstream": 100,
            "alternate": 100,
            "downstream": 100,
        },
        structural_sources=sources or {
            edge: "assignment_prior" for edge in projected
        },
        spatial_confidence=confidence or {
            edge: 0.9 for edge in projected
        },
        road_domain_status=road_domain_status,
    )


class TestPlausibleDetours:
    def test_finds_alternative_without_closed_edge(self):
        successors = {
            "in": ["closed", "a"],
            "closed": ["out"],
            "a": ["b"],
            "b": ["out"],
        }
        costs = {edge: 1.0 for edge in set(successors) | {"out"}}
        paths = plausible_detour_paths(
            ["closed"], successors, costs, max_paths_per_pair=1
        )
        assert len(paths) == 1
        assert paths[0].edges == ("in", "a", "b", "out")
        assert "closed" not in paths[0].edges
        assert paths[0].freeflow_s == 4.0

    def test_no_alternative_is_explicitly_empty(self):
        successors = {"in": ["closed"], "closed": ["out"]}
        costs = {"in": 1.0, "closed": 1.0, "out": 1.0}
        assert plausible_detour_paths(["closed"], successors, costs) == ()

    def test_returns_distinct_plausible_alternatives_deterministically(self):
        successors = {
            "in": ["closed", "a", "c"],
            "closed": ["out"],
            "a": ["b"],
            "b": ["out"],
            "c": ["d"],
            "d": ["out"],
        }
        costs = {edge: 1.0 for edge in set(successors) | {"out"}}
        first = plausible_detour_paths(["closed"], successors, costs)
        second = plausible_detour_paths(["closed"], successors, costs)
        assert first == second
        assert {path.edges for path in first} == {
            ("in", "a", "b", "out"),
            ("in", "c", "d", "out"),
        }


class TestScheduleScoring:
    def test_lower_closed_flow_and_detour_pressure_rank_better(self):
        schedules = generate_closure_schedules(_search())
        assert len(schedules) == 18
        epoch = "2027-04-05T00:00:00"
        closed = _series(
            epoch,
            {
                "2027-04-05T08:00:00": 80,
                "2027-04-05T08:15:00": 80,
                "2027-04-05T08:30:00": 80,
                "2027-04-05T08:45:00": 80,
                "2027-04-05T09:00:00": 5,
                "2027-04-05T09:15:00": 5,
                "2027-04-05T09:30:00": 5,
                "2027-04-05T09:45:00": 5,
            },
        )
        alternate = _series(
            epoch,
            {
                "2027-04-05T08:00:00": 80,
                "2027-04-05T08:15:00": 80,
                "2027-04-05T08:30:00": 80,
                "2027-04-05T08:45:00": 80,
                "2027-04-05T09:00:00": 5,
                "2027-04-05T09:15:00": 5,
                "2027-04-05T09:30:00": 5,
                "2027-04-05T09:45:00": 5,
            },
        )
        candidates = [
            _score(schedule, closed=closed, alternate=alternate)
            for schedule in schedules
        ]
        ranked = rank_schedules(candidates)
        by_start = {
            (candidate["first_work_date"], candidate["daily_start"]): candidate
            for candidate in ranked
        }
        assert (
            by_start[("2027-04-05", "09:00")]["proxy_rank"]
            < by_start[("2027-04-05", "08:00")]["proxy_rank"]
        )
        assert "delay" not in json_keys(ranked[0])

    def test_missing_closed_flow_is_not_zero_and_is_unscoreable(self):
        schedule = generate_closure_schedules(_search())[0]
        epoch = "2027-04-05T00:00:00"
        closed = _series(
            epoch, {"2027-04-05T08:00:00": None}
        )
        result = _score(schedule, closed=closed)
        assert result["scoreable"] is False
        assert result["features"]["closed_edge_vehicle_exposure"] is None
        assert result["evidence"]["support_level"] == "unavailable"

    def test_missing_detour_path_is_unscoreable(self):
        schedule = generate_closure_schedules(_search())[0]
        result = _score(schedule, paths=())
        assert result["scoreable"] is False
        assert result["features"]["detour_post_diversion_utilization_p90"] is None

    def test_low_confidence_and_out_of_domain_do_not_change_traffic_features(self):
        schedule = generate_closure_schedules(_search())[0]
        normal = _score(schedule)
        low = _score(
            schedule,
            confidence={
                "closed": 0.1,
                "upstream": 0.1,
                "alternate": 0.1,
                "downstream": 0.1,
            },
            road_domain_status="out_of_domain",
        )
        assert low["features"] == normal["features"]
        assert low["evidence"]["support_level"] == "low"
        assert "low_spatial_confidence" in low["evidence"]["reasons"]
        assert "out_of_domain_road_features" in low["evidence"]["reasons"]

    def test_missing_structural_source_reduces_support(self):
        schedule = generate_closure_schedules(_search())[0]
        result = _score(
            schedule,
            sources={
                "closed": "forecast_sensor",
                "upstream": "assignment_prior",
                "alternate": "missing",
                "downstream": "assignment_prior",
            },
        )
        assert result["scoreable"] is True
        assert result["coverage"]["structural_field_edges"] == 0.75
        assert result["evidence"]["support_level"] == "low"


def json_keys(value):
    if isinstance(value, dict):
        result = set(value)
        for child in value.values():
            result.update(json_keys(child))
        return result
    if isinstance(value, list):
        result = set()
        for child in value:
            result.update(json_keys(child))
        return result
    return set()


def _ranked_candidates(count=200, *, support="high"):
    beginning = datetime(2027, 4, 1)
    candidates = []
    for rank in range(count):
        day_count = rank % 3 + 1
        first = beginning + timedelta(days=rank % 30)
        candidates.append({
            "schedule_id": f"schedule-{rank:03}",
            "proxy_rank": rank,
            "combined_rank_score": float(rank),
            "day_count": day_count,
            "first_work_date": first.strftime("%Y-%m-%d"),
            "evidence": {"support_level": support},
        })
    return candidates


class TestStratifiedShortlist:
    def test_small_bounded_search_falls_back_to_exhaustive(self):
        ranked = _ranked_candidates(20)
        result = stratified_shortlist(
            ranked,
            all_candidate_count=20,
            unavailable_candidate_count=0,
        )
        assert result["selection_mode"] == "bounded_exhaustive_fallback"
        assert len(result["entries"]) == 20

    def test_bounded_fallback_includes_unscoreable_legal_candidates(self):
        ranked = _ranked_candidates(2)
        unavailable = [
            {
                "schedule_id": "unavailable-000",
                "day_count": 1,
                "first_work_date": "2027-04-03",
            }
        ]
        result = stratified_shortlist(
            ranked,
            all_candidate_count=3,
            unavailable_candidate_count=1,
            unavailable_candidates=unavailable,
        )
        assert result["selection_mode"] == "bounded_exhaustive_fallback"
        assert [entry["schedule_id"] for entry in result["entries"]] == [
            "schedule-000",
            "schedule-001",
            "unavailable-000",
        ]
        assert result["entries"][-1]["proxy_rank"] is None
        assert result["shortlist_fraction"] == 1.0
        assert result["withhold_recommendation"] is True

    def test_bounded_fallback_rejects_missing_unavailable_objects(self):
        with pytest.raises(
            ValueError,
            match="requires every unavailable candidate",
        ):
            stratified_shortlist(
                _ranked_candidates(2),
                all_candidate_count=3,
                unavailable_candidate_count=1,
            )

    def test_rejects_candidate_count_that_leaves_unclassified_gap(self):
        with pytest.raises(ValueError, match="candidate counts are inconsistent"):
            stratified_shortlist(
                _ranked_candidates(2),
                all_candidate_count=4,
                unavailable_candidate_count=1,
                policy=ShortlistPolicy(bounded_exhaustive_limit=1),
            )

    def test_bounded_fallback_handles_all_candidates_unscoreable(self):
        unavailable = [
            {
                "schedule_id": f"unavailable-{index:03}",
                "day_count": 1,
                "first_work_date": f"2027-04-{index + 1:02}",
            }
            for index in range(3)
        ]
        result = stratified_shortlist(
            [],
            all_candidate_count=3,
            unavailable_candidate_count=3,
            unavailable_candidates=unavailable,
        )
        assert len(result["entries"]) == 3
        assert all(entry["proxy_rank"] is None for entry in result["entries"])
        assert result["withhold_reasons"] == ["unscoreable_legal_candidates"]

    def test_includes_day_counts_date_blocks_and_controls(self):
        ranked = _ranked_candidates()
        result = stratified_shortlist(
            ranked,
            all_candidate_count=200,
            unavailable_candidate_count=0,
            policy=ShortlistPolicy(
                best_overall=5,
                best_per_day_count=1,
                best_per_date_block=1,
                date_block_days=7,
                validation_quantiles=(0.5, 1.0),
                bounded_exhaustive_limit=20,
            ),
        )
        reasons = {
            reason
            for entry in result["entries"]
            for reason in entry["selection_reasons"]
        }
        assert {"best_day_count:1", "best_day_count:2", "best_day_count:3"} <= reasons
        assert any(reason.startswith("best_date_block:") for reason in reasons)
        assert "validation_control_q0.50" in reasons
        assert "validation_control_q1.00" in reasons

    def test_low_support_expands_and_withholds(self):
        high = stratified_shortlist(
            _ranked_candidates(support="high"),
            all_candidate_count=200,
            unavailable_candidate_count=0,
            policy=ShortlistPolicy(
                best_overall=10,
                bounded_exhaustive_limit=20,
                validation_quantiles=(1.0,),
            ),
        )
        low = stratified_shortlist(
            _ranked_candidates(support="low"),
            all_candidate_count=200,
            unavailable_candidate_count=0,
            policy=ShortlistPolicy(
                best_overall=10,
                bounded_exhaustive_limit=20,
                validation_quantiles=(1.0,),
            ),
        )
        assert len(low["entries"]) > len(high["entries"])
        assert low["withhold_recommendation"] is True
        assert "low_proxy_support" in low["withhold_reasons"]

    def test_unscoreable_legal_candidates_expand_and_withhold(self):
        unavailable = [
            {
                "schedule_id": f"unavailable-{index:03}",
                "day_count": 1,
                "first_work_date": "2027-04-30",
            }
            for index in range(10)
        ]
        result = stratified_shortlist(
            _ranked_candidates(),
            all_candidate_count=210,
            unavailable_candidate_count=10,
            unavailable_candidates=unavailable,
            policy=ShortlistPolicy(
                best_overall=10,
                bounded_exhaustive_limit=20,
                validation_quantiles=(1.0,),
            ),
        )
        assert result["withhold_recommendation"] is True
        assert "unscoreable_legal_candidates" in result["withhold_reasons"]
        assert len(result["entries"]) >= 25
        assert result["unavailable_selected_count"] == 10
        assert {
            entry["schedule_id"]
            for entry in result["entries"]
            if entry["proxy_rank"] is None
        } == {candidate["schedule_id"] for candidate in unavailable}

    def test_unscoreable_controls_respect_cap_and_disclose_omissions(self):
        unavailable = [
            {
                "schedule_id": f"unavailable-{index:03}",
                "day_count": 1,
                "first_work_date": "2027-04-30",
            }
            for index in range(20)
        ]
        result = stratified_shortlist(
            _ranked_candidates(20),
            all_candidate_count=40,
            unavailable_candidate_count=20,
            unavailable_candidates=unavailable,
            policy=ShortlistPolicy(
                best_overall=5,
                bounded_exhaustive_limit=10,
                validation_quantiles=(1.0,),
                maximum_shortlist=10,
            ),
        )
        assert len(result["entries"]) == 10
        assert result["candidate_coverage_complete"] is False
        assert (
            "unscoreable_candidates_exceed_shortlist_capacity"
            in result["withhold_reasons"]
        )

    def test_all_unscoreable_large_search_keeps_explicit_sumo_controls(self):
        unavailable = [
            {
                "schedule_id": f"unavailable-{index:03}",
                "day_count": 1,
                "first_work_date": "2027-04-30",
            }
            for index in range(21)
        ]
        result = stratified_shortlist(
            [],
            all_candidate_count=21,
            unavailable_candidate_count=21,
            unavailable_candidates=unavailable,
            policy=ShortlistPolicy(
                bounded_exhaustive_limit=20,
                maximum_shortlist=25,
            ),
        )
        assert len(result["entries"]) == 21
        assert result["candidate_coverage_complete"] is True
        assert result["withhold_recommendation"] is True

    def test_required_strata_over_cap_is_deterministic_and_withheld(self):
        policy = ShortlistPolicy(
            best_overall=1,
            best_per_day_count=1,
            best_per_date_block=1,
            validation_quantiles=(0.25, 0.5, 0.75, 1.0),
            bounded_exhaustive_limit=1,
            maximum_shortlist=2,
        )
        first = stratified_shortlist(
            _ranked_candidates(),
            all_candidate_count=200,
            unavailable_candidate_count=0,
            policy=policy,
        )
        second = stratified_shortlist(
            _ranked_candidates(),
            all_candidate_count=200,
            unavailable_candidate_count=0,
            policy=policy,
        )
        assert first == second
        assert len(first["entries"]) == 2
        assert (
            "required_strata_exceed_shortlist_capacity"
            in first["withhold_reasons"]
        )

    def test_selection_is_deterministic(self):
        ranked = _ranked_candidates()
        first = stratified_shortlist(
            ranked, all_candidate_count=200, unavailable_candidate_count=0
        )
        second = stratified_shortlist(
            ranked, all_candidate_count=200, unavailable_candidate_count=0
        )
        assert first == second
