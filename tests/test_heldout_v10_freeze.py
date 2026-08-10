"""The v10 held-out package: v9 plus one pre-outcome condition.

v10 exists because v9 failed exactly one gate check of seven —
`ranking_case_fraction` 0.4 against a 0.5 floor — with three of five cases
producing zero eligible schedules. Everything v9 got right is deliberately
carried over unchanged; only case selection gains the survivability condition.

These tests do not freeze anything and do not need the canonical archive: they
assert the contract the freeze must satisfy, including the two traps this
project has already paid for — a campaign registered in
EXACT_DEMAND_BINDING_CAMPAIGNS only AFTER its freeze (which voided two
campaigns), and a selection rule that changes more than one thing at a time.
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_monthly_proxy_validation as runner
from tools import freeze_heldout_v10 as v10
from tools import freeze_heldout_v9 as v9
from traffic_sim.simulation import closure_survivability as survivability


class TestCampaignRegistration:
    def test_v10_binds_its_demand_exactly(self):
        """Registered BEFORE the freeze. The freeze fingerprints this file, so
        registering afterwards changes the digest the frozen manifest recorded
        and voids the campaign — the trap that has already voided two."""
        assert "v10" in runner.EXACT_DEMAND_BINDING_CAMPAIGNS

    def test_the_registration_is_visible_in_the_fingerprinted_sources(self):
        source = inspect.getsource(v10.compose)
        assert "run_monthly_proxy_validation.py" in source


class TestInheritedFromV9:
    """A campaign that changed two things at once could not attribute its
    result to either. Each of these must be the same object v9 froze."""

    def test_the_canonical_archive_is_v9s(self):
        assert v10.CANONICAL_PATH is v9.CANONICAL_PATH
        assert v10.CANONICAL_IDENTITY is v9.CANONICAL_IDENTITY

    def test_the_gate_thresholds_are_v9s(self):
        assert v10.GATE == v9.GATE

    def test_the_band_and_support_floor_are_v9s(self):
        assert v10.MAX_SENSOR_DISTANCE_M == v9.MAX_SENSOR_DISTANCE_M == 400.0
        assert v10.MIN_WINDOW_SUPPORT == v9.MIN_WINDOW_SUPPORT == 10
        assert v10.MIN_EDGE_LENGTH_M == v9.MIN_EDGE_LENGTH_M

    def test_the_case_and_schedule_counts_are_v9s(self):
        assert v10.CASES_REQUIRED == v9.CASES_REQUIRED == 5
        assert v10.SCHEDULES_PER_CASE == v9.SCHEDULES_PER_CASE == 15

    def test_the_ranking_signal_is_unchanged(self):
        """The formula version bump records that ELIGIBILITY gained a
        condition, not that the ranking moved."""
        features = {v: {"e": [10, 20, 30]} for v in ("q50", "q10", "q90")}
        assert v10.score(features, "e") == v9.score(features, "e")

    def test_the_formula_version_still_changed(self):
        assert v10.FORMULA_VERSION != v9.FORMULA_VERSION


def _pool():
    return [
        {"edge_id": "hi_var", "road_class": "secondary", "u": "1", "v": "2",
         "dist_sensor_m": 10.0, "length_m": 90.0},
        {"edge_id": "mid_var", "road_class": "tertiary", "u": "3", "v": "4",
         "dist_sensor_m": 20.0, "length_m": 90.0},
        {"edge_id": "lo_var", "road_class": "tertiary", "u": "5", "v": "6",
         "dist_sensor_m": 30.0, "length_m": 90.0},
        {"edge_id": "spare_a", "road_class": "tertiary", "u": "7", "v": "8",
         "dist_sensor_m": 40.0, "length_m": 90.0},
        {"edge_id": "spare_b", "road_class": "tertiary", "u": "9", "v": "10",
         "dist_sensor_m": 50.0, "length_m": 90.0},
        {"edge_id": "spare_c", "road_class": "secondary", "u": "11", "v": "12",
         "dist_sensor_m": 60.0, "length_m": 90.0},
    ]


def _features(exposures):
    return {variant: dict(exposures) for variant in ("q50", "q10", "q90")}


def _report(edge_id, *, survives, severed=0, denied=0, cut=()):
    return survivability.SurvivabilityReport(
        edge_id=edge_id, survives=survives, severed_destination=severed,
        denied_departures=denied, vehicles_crossing=100,
        successors_cut_off=tuple(cut),
        per_variant={"q50": {"vehicles": 100, "denied_departures": denied,
                             "severed_destination": severed}})


class TestSelection:
    def _exposures(self):
        # Descending temporal variation, all comfortably above the support
        # floor so eligibility turns purely on survivability.
        return {
            "hi_var": [10, 100],
            "mid_var": [40, 100],
            "lo_var": [70, 100],
            "spare_a": [80, 100],
            "spare_b": [85, 100],
            "spare_c": [90, 100],
        }

    def test_a_severing_edge_never_takes_a_slot(self):
        """The whole point of moving the check ahead of the SUMO runs: a
        candidate that strands someone must not outrank one that does not."""
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True)
                   for edge in pool}
        reports["hi_var"] = _report("hi_var", survives=False, severed=12)
        chosen, eligible, refused = v10.select(pool, _features(self._exposures()),
                                               reports)
        assert "hi_var" not in [c["edge_id"] for c in chosen]
        assert [entry["edge_id"] for entry in refused] == ["hi_var"]
        assert eligible == len(pool) - 1

    def test_denied_departures_alone_do_not_refuse_a_candidate(self):
        """C1's lesson, one level up: a busy street has departures on it, and
        refusing those would make every busy street uncloseable again."""
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True,
                                            denied=85)
                   for edge in pool}
        chosen, _eligible, refused = v10.select(
            pool, _features(self._exposures()), reports)
        assert refused == []
        assert len(chosen) == v10.CASES_REQUIRED

    def test_a_structurally_cut_off_successor_refuses(self):
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True)
                   for edge in pool}
        reports["mid_var"] = _report("mid_var", survives=False, cut=("spur",))
        _chosen, _eligible, refused = v10.select(
            pool, _features(self._exposures()), reports)
        assert [entry["survivability"]["successors_cut_off"]
                for entry in refused] == [["spur"]]

    def test_too_few_survivors_refuses_to_freeze(self):
        """Stage 4's gate is that ranking_case_fraction >= 0.5 is ACHIEVABLE
        before any SUMO runs. Fewer surviving cases than that is a refusal,
        not a smaller campaign."""
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=False,
                                            severed=1)
                   for edge in pool}
        reports["hi_var"] = _report("hi_var", survives=True)
        reports["mid_var"] = _report("mid_var", survives=True)
        with pytest.raises(SystemExit) as error:
            v10.select(pool, _features(self._exposures()), reports)
        assert "survive" in str(error.value)

    def test_both_road_classes_are_still_guaranteed(self):
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True)
                   for edge in pool}
        chosen, _eligible, _refused = v10.select(
            pool, _features(self._exposures()), reports)
        assert {c["road_class"] for c in chosen} >= set(v10.ROAD_CLASSES)

    def test_selected_cases_carry_their_survivability_evidence(self):
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True,
                                            denied=3)
                   for edge in pool}
        chosen, _eligible, _refused = v10.select(
            pool, _features(self._exposures()), reports)
        for case in chosen:
            assert case["survivability"]["rule_version"] == \
                survivability.RULE_VERSION
            assert case["survivability"]["vehicles_denied_departure"] == 3

    def test_the_top_two_by_rank_are_still_the_discriminating_pair(self):
        pool = _pool()
        reports = {edge["edge_id"]: _report(edge["edge_id"], survives=True)
                   for edge in pool}
        chosen, _eligible, _refused = v10.select(
            pool, _features(self._exposures()), reports)
        assert [c["edge_id"] for c in chosen[:2]] == ["hi_var", "mid_var"]
        assert [c["expected_discriminating"] for c in chosen] == \
            [True, True, False, False, False]


class TestExclusion:
    def test_prior_campaign_selections_are_globbed_not_hand_listed(self):
        """v9 named its predecessors' selection files one by one. A
        hand-maintained list is one forgotten line away from re-using an
        already-validated edge."""
        source = inspect.getsource(v10.prior_edges_and_junctions)
        assert 'glob("heldout_v*_selection.json")' in source
        assert "heldout_v9_selection.json" not in source

    def test_the_campaigns_own_artifacts_are_excluded_from_its_own_exclusion(self):
        source = inspect.getsource(v10.prior_edges_and_junctions)
        assert 'f"heldout_{CAMPAIGN}_selection.json"' in source


class TestSurvivabilityProbeBinding:
    def test_the_probe_uses_run_scenarios_own_graph_and_search(self):
        """A second, independently written reachability implementation would be
        free to disagree with the simulation — the one thing this check may not
        do."""
        source = inspect.getsource(v10.survivability)
        assert "rs.build_edge_graph" in source
        assert "rs.reachable" in source

    def test_a_missing_sumo_network_fails_closed_with_the_fix(self):
        source = inspect.getsource(v10.survivability)
        assert "make sumo-net" in source

    def test_the_simulation_semantics_are_in_the_fingerprinted_sources(self):
        """Stage 3 changed what a closure run DOES. A v10 frozen against that
        behaviour must not be replayable against a tree where it changed."""
        source = inspect.getsource(v10.compose)
        assert "traffic_sim/simulation/closure_teleport.py" in source
        assert "traffic_sim/simulation/closure_survivability.py" in source
