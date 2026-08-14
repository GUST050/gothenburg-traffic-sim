"""Fas 0B: the Gate S decision rule, frozen and fail-closed.

The plan requires the selection, tolerances, seeds and comparisons to be
frozen before the SUMO run, the same seed list for every stress case, and the
same (stress case, seed) pair for baseline and candidate.

These tests exercise the decision logic on synthetic observations, so they
run without SUMO and without the gitignored route artifacts. The real run's
result is recorded in the append-only outcome artifact, never here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import measure_direction_decision_sensitivity as sens


def registration(candidates=("edgeA", "edgeB"), seeds=(1000, 1001, 1002, 1003),
                 **overrides):
    payload = dict(
        protocol=sens.PROTOCOL,
        selection_rule=sens.SELECTION_RULE,
        date="2025-09-16", window_begin="06:00", window_end="10:00",
        seeds=tuple(seeds),
        stress_cases=("q50", "q10", "q90"),
        candidate_edges=tuple(candidates),
        candidate_selection={"rule": sens.SELECTION_RULE},
        timeout_s=300,
        objective="added_total_time_loss_s",
        comparison_fields=("hard_failure", "winner"),
        thresholds=sens.MaterialityThresholds(),
        source_digests={},
    )
    payload.update(overrides)
    return sens.Registration(**payload)


def observations(values, *, seeds=(1000, 1001, 1002, 1003), failures=()):
    """values: {candidate: {case: [per-seed added time loss]}}"""
    out = []
    for candidate, by_case in values.items():
        for case, series in by_case.items():
            for seed, value in zip(seeds, series):
                out.append(sens.Observation(
                    stress_case=case, seed=seed, candidate=candidate,
                    added_time_loss_s=value, hard_failure=False,
                    failure_reason=None, runtime_s=1.0))
    out.extend(failures)
    return out


# ── the matched-seed design ───────────────────────────────────────────────
class TestMatchedSeedDesign:
    def test_the_same_seed_list_is_declared_for_every_stress_case(self):
        reg = registration()
        assert reg.seeds == (1000, 1001, 1002, 1003)
        # One seed list, not one per case: there is no per-case seed field.
        assert not hasattr(reg, "seeds_by_case")

    def test_at_least_four_seeds_are_registered(self):
        """FHWA asks for several replications before any variation claim."""
        assert len(registration().seeds) >= 4

    def test_the_registration_key_changes_with_any_frozen_field(self):
        base = registration().content_key
        assert registration(seeds=(1000, 1001)).content_key != base
        assert registration(candidates=("edgeA",)).content_key != base
        assert registration(timeout_s=600).content_key != base

    def test_identical_registrations_share_a_key(self):
        assert registration().content_key == registration().content_key

    def test_the_artifact_is_not_release_evidence(self):
        assert registration().release_evidence is False
        assert registration().to_json()["release_evidence"] is False


# ── materiality: direction vs seed noise ──────────────────────────────────
class TestMaterialityComparesAgainstSeedNoise:
    def test_a_difference_smaller_than_seed_noise_is_not_material(self):
        """The whole point of matched seeds."""
        obs = observations({
            "edgeA": {
                # big seed spread, tiny between-case difference
                "q50": [100.0, 140.0, 60.0, 120.0],
                "q10": [101.0, 141.0, 61.0, 121.0],
                "q90": [ 99.0, 139.0, 59.0, 119.0],
            },
            "edgeB": {
                "q50": [200.0, 240.0, 160.0, 220.0],
                "q10": [201.0, 241.0, 161.0, 221.0],
                "q90": [199.0, 239.0, 159.0, 219.0],
            },
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "NO"
        assert not result["per_candidate"]["edgeA"]["material"]

    def test_a_difference_far_larger_than_seed_noise_is_material(self):
        obs = observations({
            "edgeA": {
                "q50": [100.0, 101.0, 99.0, 100.0],
                "q10": [ 40.0,  41.0, 39.0,  40.0],
                "q90": [160.0, 161.0, 159.0, 160.0],
            },
            "edgeB": {
                "q50": [300.0, 301.0, 299.0, 300.0],
                "q10": [300.0, 301.0, 299.0, 300.0],
                "q90": [300.0, 301.0, 299.0, 300.0],
            },
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "YES"
        assert result["per_candidate"]["edgeA"]["material"]

    def test_the_spread_ratio_is_reported(self):
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [90.0] * 4,
                      "q90": [110.0] * 4},
            "edgeB": {"q50": [200.0] * 4, "q10": [200.0] * 4,
                      "q90": [200.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        stats = result["per_candidate"]["edgeA"]
        assert stats["between_case_range"] == pytest.approx(20.0)
        assert stats["mean_within_case_seed_range"] == pytest.approx(0.0)

    def test_a_relative_difference_below_the_floor_is_not_material(self):
        """Large absolute numbers with a tiny relative difference."""
        obs = observations({
            "edgeA": {"q50": [10000.0] * 4, "q10": [9995.0] * 4,
                      "q90": [10005.0] * 4},
            "edgeB": {"q50": [20000.0] * 4, "q10": [20000.0] * 4,
                      "q90": [20000.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        assert not result["per_candidate"]["edgeA"]["material"]


# ── decision fields beyond the objective ──────────────────────────────────
class TestDecisionFields:
    def test_a_changed_winner_is_material_even_if_spreads_are_small(self):
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [103.0] * 4},
            "edgeB": {"q50": [101.0] * 4, "q10": [101.0] * 4,
                      "q90": [ 99.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "YES"
        assert not result["winner_identical"]
        assert any("winner differs" in r for r in result["reasons"])

    def test_an_identical_ranking_across_cases_is_reported(self):
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [100.0] * 4},
            "edgeB": {"q50": [200.0] * 4, "q10": [200.0] * 4,
                      "q90": [200.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["rankings_identical"]
        assert result["winner_identical"]
        assert result["gate_s"] == "NO"


# ── fail-closed ───────────────────────────────────────────────────────────
class TestFailClosed:
    def test_an_incomplete_matrix_is_inconclusive_not_no(self):
        """'We could not measure it' is not 'it does not matter'."""
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [100.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "incomplete matrix" in result["reasons"][0]

    def test_a_timeout_is_inconclusive(self):
        failing = sens.Observation(
            stress_case="q90", seed=1003, candidate="edgeB",
            added_time_loss_s=None, hard_failure=True,
            failure_reason="timeout after 300s", runtime_s=300.0)
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [100.0] * 4},
            "edgeB": {"q50": [200.0] * 4, "q10": [200.0] * 4,
                      "q90": [200.0, 200.0, 200.0, None]},
        })
        obs = [o for o in obs if o.added_time_loss_s is not None] + [failing]
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert any("timeout" in r for r in result.get("failure_reasons", []))

    def test_inconclusive_says_it_forbids_integration(self):
        result = sens.decide_gate_s([], registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "INCONCLUSIVE is not NO" in result["note"]

    def test_no_outcome_can_be_release_evidence(self):
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [100.0] * 4},
            "edgeB": {"q50": [200.0] * 4, "q10": [200.0] * 4,
                      "q90": [200.0] * 4},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["release_evidence"] is False

    def test_the_outcome_names_its_registration(self):
        reg = registration()
        obs = observations({
            "edgeA": {"q50": [100.0] * 4, "q10": [100.0] * 4,
                      "q90": [100.0] * 4},
            "edgeB": {"q50": [200.0] * 4, "q10": [200.0] * 4,
                      "q90": [200.0] * 4},
        })
        result = sens.decide_gate_s(obs, reg)
        assert result["registration_key"] == reg.content_key


# ── candidate selection is outcome-blind ──────────────────────────────────
class TestSelectionIsOutcomeBlind:
    def test_selection_reads_only_input_side_signals(self):
        """The selection BODY must not mention any outcome quantity.

        Checked against the executable statements rather than the source
        text, because the docstring legitimately contains the phrase
        "outcome-blind" and a naive grep would trip on its own explanation.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(sens.select_candidates))
        function = tree.body[0]
        body = function.body[1:] if (
            isinstance(function.body[0], ast.Expr)
            and isinstance(function.body[0].value, ast.Constant)
        ) else function.body
        names = {
            node.id for stmt in body for node in ast.walk(stmt)
            if isinstance(node, ast.Name)
        } | {
            node.attr for stmt in body for node in ast.walk(stmt)
            if isinstance(node, ast.Attribute)
        } | {
            node.value for stmt in body for node in ast.walk(stmt)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in ("time_loss", "total_time_loss_s", "winner",
                          "regret", "disruption", "added_time_loss_s"):
            assert forbidden not in names

    def test_exposure_is_computed_from_a_route_file_not_a_result(self, tmp_path):
        route = tmp_path / "r.rou.xml"
        route.write_text(
            '<routes>'
            '<vehicle id="1"><route edges="a b c"/></vehicle>'
            '<vehicle id="2"><route edges="b c d"/></vehicle>'
            '</routes>')
        counts = sens.route_edge_exposure(route)
        assert counts == {"a": 1, "b": 2, "c": 2, "d": 1}

    def test_sensor_edges_are_excluded_from_the_pool(self):
        import inspect
        source = inspect.getsource(sens.select_candidates)
        assert "excluded" in source and "sensor_edges" in source

    def test_the_rule_is_versioned(self):
        assert sens.SELECTION_RULE == "dirsplit_sensitivity_selection_v1"


# ── thresholds are frozen in code ─────────────────────────────────────────
class TestThresholdsAreFrozen:
    def test_the_defaults_are_the_registered_ones(self):
        thresholds = sens.MaterialityThresholds()
        assert thresholds.spread_ratio == 2.0
        assert thresholds.relative_objective == 0.10
        assert thresholds.min_rank_correlation == 0.90

    def test_thresholds_travel_inside_the_registration_key(self):
        loose = sens.MaterialityThresholds(spread_ratio=1.0)
        assert registration(thresholds=loose).content_key != \
            registration().content_key


# ── the real artifacts, when they exist ───────────────────────────────────
class TestRealArtifacts:
    def test_a_frozen_registration_matches_its_own_key(self):
        path = sens.REGISTRATION_PATH
        if not path.is_file():
            pytest.skip("no frozen registration in this checkout")
        payload = json.loads(path.read_text())
        recorded = payload.pop("content_key", None)
        assert recorded == sens.content_digest(payload)

    def test_an_outcome_names_the_frozen_registration(self):
        reg_path, out_path = sens.REGISTRATION_PATH, sens.OUTCOME_PATH
        if not (reg_path.is_file() and out_path.is_file()):
            pytest.skip("no completed run in this checkout")
        reg = json.loads(reg_path.read_text())
        outcome = json.loads(out_path.read_text())
        assert outcome["registration_key"] == reg["content_key"]
        assert outcome["release_evidence"] is False
        assert outcome["gate_s"] in ("NO", "YES", "INCONCLUSIVE")
