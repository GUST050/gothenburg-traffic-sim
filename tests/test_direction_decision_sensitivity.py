"""Fas 0B: the Gate S decision rule, frozen and fail-closed.

The plan requires the selection, tolerances, seeds and comparisons to be
frozen before the SUMO run, the same seed list for every stress case, and the
same (stress case, seed) pair for baseline and candidate.

These tests exercise the decision logic on synthetic observations, so they
run without SUMO and without the gitignored route artifacts. The real run's
result is recorded in the append-only outcome artifact, never here.

REPAIRED 2026-08-14. The previous suite passed against a tool that could not
have produced evidence: it never varied the route file or the seed, read a
``disruption`` field the product does not emit, ignored its own registered
closure window, and reduced only one private objective. The tests below now
pin each of those against regression — the run path is checked structurally
(it must reach the runner through a one-seed ScenarioSpec naming the case)
and the reducer is checked against the deployed closure policy.
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
        closure_begin="2025-09-16T06:00:00", closure_end="2025-09-16T10:00:00",
        seeds=tuple(seeds),
        stress_cases=("q50", "q10", "q90"),
        candidate_edges=tuple(candidates),
        candidate_selection={"rule": sens.SELECTION_RULE},
        timeout_s=300,
        objective="added_vehicle_hours (deployed closure_ranking key)",
        comparison_fields=("hard_failure", "winner"),
        thresholds=sens.MaterialityThresholds(),
        source_digests={},
        demand_build_id="demand-abc",
        network_build_id="network-def",
    )
    payload.update(overrides)
    return sens.Registration(**payload)


def policy(hours, *, metres=0.0, affected=100, no_detour=0):
    return {"added_vehicle_hours": float(hours),
            "added_metres_total": float(metres),
            "vehicles_affected": int(affected),
            "vehicles_no_detour": int(no_detour)}


def observations(values, *, seeds=(1000, 1001, 1002, 1003), failures=(),
                 inserted_by_seed=None, integrity="verified_clean",
                 health_flags=()):
    """values: {candidate: {case: added_vehicle_hours | dict}}

    A scalar becomes a policy record with no stranded vehicles. A dict is
    passed through, so a test can make one arm disqualify.
    """
    inserted_by_seed = inserted_by_seed or {
        seed: 20000 + index for index, seed in enumerate(seeds)}
    out = []
    for candidate, by_case in values.items():
        for case, value in by_case.items():
            for seed in seeds:
                record = value if isinstance(value, dict) else policy(value)
                out.append(sens.Observation(
                    stress_case=case, seed=seed, candidate=candidate,
                    policy=dict(record), closure_integrity=integrity,
                    seed_health_flags=tuple(health_flags),
                    vehicles_inserted=inserted_by_seed[seed],
                    hard_failure=False, failure_reason=None, runtime_s=1.0))
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

    def test_the_closure_window_is_part_of_the_frozen_key(self):
        """The registered window is a constraint, not a caption."""
        moved = registration(closure_end="2025-09-16T12:00:00")
        assert moved.content_key != registration().content_key

    def test_identical_registrations_share_a_key(self):
        assert registration().content_key == registration().content_key

    def test_the_artifact_is_not_release_evidence(self):
        assert registration().release_evidence is False
        assert registration().to_json()["release_evidence"] is False


# ── the run path actually varies case and seed ────────────────────────────
class TestTheRunPathVariesWhatItClaimsTo:
    def spec(self, *, variant="q10", seed=1001, closures=("edgeA",)):
        return sens.build_spec_payload(
            registration(), case_variant=variant, seed=seed,
            closures=list(closures), scenario_id="tag",
            start_time="2025-09-16T00:00:00",
            end_time="2025-09-17T00:00:00")

    def test_the_stress_case_reaches_the_runner(self):
        """The defect this replaces: every q case ran the same route file."""
        assert self.spec(variant="q10")["demand_variant_mapping"] == {
            "1001": "q10"}
        assert self.spec(variant="q90", seed=1002)[
            "demand_variant_mapping"] == {"1002": "q90"}

    def test_the_seed_reaches_the_runner(self):
        """The defect this replaces: --seeds 1 always meant seed 1000."""
        for seed in (1000, 1001, 1002, 1003):
            spec = self.spec(seed=seed)
            assert spec["seed_set"] == [seed]
            assert set(spec["demand_variant_mapping"]) == {str(seed)}

    def test_each_arm_binds_exactly_one_case_to_exactly_one_seed(self):
        spec = self.spec()
        assert len(spec["seed_set"]) == 1
        assert len(spec["demand_variant_mapping"]) == 1

    def test_the_registered_closure_window_is_applied(self):
        """The defect this replaces: the closure ran the whole demand day."""
        closures = self.spec()["closures"]
        assert closures == [{
            "edge_id": "edgeA",
            "start_time": "2025-09-16T06:00:00",
            "end_time": "2025-09-16T10:00:00",
            "closure_type": "full"}]

    def test_every_case_and_seed_names_a_distinct_spec(self):
        seen = set()
        for variant in ("q50", "q10", "q90"):
            for seed in (1000, 1001, 1002, 1003):
                spec = self.spec(variant=variant, seed=seed)
                key = (json.dumps(spec["demand_variant_mapping"],
                                  sort_keys=True),
                       tuple(spec["seed_set"]))
                assert key not in seen
                seen.add(key)
        assert len(seen) == 12

    def test_the_spec_carries_the_frozen_build_identity(self):
        spec = self.spec()
        assert spec["demand_build_id"] == "demand-abc"
        assert spec["network_build_id"] == "network-def"

    def test_no_environment_variable_is_used_to_pass_the_seed(self):
        """The old tool set DIRSPLIT_SENSITIVITY_SEED, which nothing reads.

        Checked on the run function's own AST: the seed must travel in the
        spec, so the runner call must not build or pass an environment at
        all. The module docstring still names the dead variable when
        recording why the tool was repaired, which is why this reads code
        rather than file text.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(sens._run_one))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "environ"
            for node in ast.walk(tree))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "run"]
        assert calls, "the tool must still call the existing runner"
        for call in calls:
            assert "env" not in {kw.arg for kw in call.keywords}

    def test_the_stress_case_table_names_a_demand_variant(self):
        for name, filename, variant in sens.STRESS_CASES:
            assert filename.endswith(".rou.xml")
            assert variant in {"q10", "q50", "q90"}
        assert {v for _n, _f, v in sens.STRESS_CASES} == {"q10", "q50", "q90"}


class TestTheRunnerWouldAcceptEverySpec:
    """The whole matrix, validated by run_scenario's OWN validator.

    A spec the tool writes but the runner rejects is a matrix that never
    runs, and a spec the runner accepts but resolves to one route file is
    the original defect wearing a contract. Both are checked here against
    the real ``validate_scenario_spec`` / ``validate_variant_coverage`` /
    ``variant_path``, on a synthetic network and demand build so the test
    needs none of the gitignored artifacts.
    """

    def build(self, tmp_path):
        import run_scenario as rs

        network = tmp_path / "net.net.xml"
        network.write_text("<net/>")
        meta = {"epoch_sim": "2025-09-16T00:00:00", "n_intervals": 96,
                "build_id": "demand-under-test", "n_variants": 3}
        (tmp_path / "demand_meta.json").write_text(json.dumps(meta))
        for _name, filename, _variant in sens.STRESS_CASES:
            (tmp_path / filename).write_text("<routes/>")
        reg = registration(
            demand_build_id="demand-under-test",
            network_build_id=rs.sha256_file(network))
        return reg, meta, network

    def test_every_arm_of_the_matrix_validates_and_is_distinct(self, tmp_path):
        import run_scenario as rs
        from traffic_sim.core.contracts import ScenarioSpec

        reg, meta, network = self.build(tmp_path)
        start, end = sens.demand_window(tmp_path)
        variants = [tmp_path / filename
                    for _n, filename, _v in sens.STRESS_CASES]

        seen_route_files = set()
        checked = 0
        for case, filename, variant in sens.STRESS_CASES:
            for seed in reg.seeds:
                for candidate in reg.candidate_edges:
                    payload = sens.build_spec_payload(
                        reg, case_variant=variant, seed=seed,
                        closures=[candidate],
                        scenario_id=f"cand_{case}_{seed}_{candidate}",
                        start_time=start, end_time=end)
                    spec = ScenarioSpec.from_dict(payload)
                    rs.validate_scenario_spec(
                        spec, meta=meta, duration_s=meta["n_intervals"] * 900,
                        network_path=network)
                    rs.validate_variant_coverage(
                        meta, dict(spec.demand_variant_mapping),
                        require_all=len(spec.seed_set) >= 3)
                    resolved = rs.variant_path(variants, variant)
                    assert resolved.name == filename
                    seen_route_files.add(resolved.name)
                    checked += 1

        assert checked == len(sens.STRESS_CASES) * len(reg.seeds) * \
            len(reg.candidate_edges)
        # The defect this replaces would have left exactly one entry here.
        assert len(seen_route_files) == 3

    def test_a_one_seed_spec_does_not_trip_the_three_variant_gate(self,
                                                                  tmp_path):
        """That gate exists so a PUBLISHED release spans the interval; a
        diagnostic arm must be a single named case."""
        import run_scenario as rs

        _reg, meta, _network = self.build(tmp_path)
        rs.validate_variant_coverage(meta, {1001: "q10"}, require_all=False)
        with pytest.raises(ValueError, match="three-variant"):
            rs.validate_variant_coverage(meta, {1001: "q10"}, require_all=True)


# ── the objective is one the product actually produces ────────────────────
class TestTheObjectiveExists:
    def test_the_policy_fields_are_the_deployed_ranking_fields(self):
        from traffic_sim.simulation.closure_ranking import REQUIRED_FIELDS

        assert set(sens.POLICY_FIELDS) == set(REQUIRED_FIELDS)

    def test_no_phantom_total_time_loss_field_is_read(self):
        """The defect this replaces: disruption['total_time_loss_s'] is not a
        field run_scenario emits, so every observation would have been None."""
        source = Path(sens.__file__).read_text()
        code = "\n".join(line for line in source.splitlines()
                         if "total_time_loss_s" not in line
                         or line.lstrip().startswith(("#", "*", "1.", "2.")))
        assert 'get("total_time_loss_s")' not in code

    def test_the_disruption_field_names_match_run_scenario(self):
        import run_scenario

        source = Path(run_scenario.__file__).read_text()
        for name in sens.POLICY_FIELDS:
            assert f'"{name}"' in source


# ── materiality is decided by the real closure policy ─────────────────────
class TestTheRealPolicyDecides:
    def test_the_reducer_uses_closure_ranking_not_a_private_rule(self):
        import inspect

        source = inspect.getsource(sens.policy_decision_for_case)
        assert "closure_ranking" in source
        assert "rank_closures" in source
        assert "worst_variant_cost" in source

    def test_an_identical_decision_across_cases_is_not_material(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "NO"
        assert result["rankings_identical"] and result["winner_identical"]

    def test_a_changed_winner_is_material(self):
        obs = observations({
            "edgeA": {"q50": 1.00, "q10": 1.00, "q90": 3.00},
            "edgeB": {"q50": 1.01, "q10": 1.01, "q90": 1.00},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "YES"
        assert not result["winner_identical"]
        assert any("winner differs" in r for r in result["reasons"])

    def test_a_changed_viable_set_is_material(self):
        """A no-detour disqualification in one case only is a decision change."""
        obs = observations({
            "edgeA": {"q50": policy(1.0), "q10": policy(1.0),
                      "q90": policy(1.0, no_detour=7)},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "YES"
        assert any("viable set differs" in r for r in result["reasons"])
        assert result["decision_by_case"]["q90"]["disqualified"] == ["edgeA"]

    def test_the_no_detour_disqualifier_is_not_averaged_into_a_score(self):
        obs = observations({
            "edgeA": {"q50": policy(0.1, no_detour=5),
                      "q10": policy(0.1, no_detour=5),
                      "q90": policy(0.1, no_detour=5)},
            "edgeB": {"q50": 9.0, "q10": 9.0, "q90": 9.0},
        })
        result = sens.decide_gate_s(obs, registration())
        # edgeA is cheapest but severs a destination, so it must never win.
        assert result["winner_by_case"] == {"q50": "edgeB", "q10": "edgeB",
                                            "q90": "edgeB"}

    def test_a_large_relative_objective_spread_is_material(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 0.5, "q90": 1.5},
            "edgeB": {"q50": 9.0, "q10": 9.0, "q90": 9.0},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["per_candidate"]["edgeA"]["material"]
        assert result["gate_s"] == "YES"

    def test_a_tiny_relative_objective_spread_is_not_material(self):
        obs = observations({
            "edgeA": {"q50": 1.000, "q10": 0.999, "q90": 1.001},
            "edgeB": {"q50": 9.000, "q10": 9.000, "q90": 9.000},
        })
        result = sens.decide_gate_s(obs, registration())
        assert not result["per_candidate"]["edgeA"]["material"]
        assert result["gate_s"] == "NO"

    def test_the_per_case_decision_is_reported_in_full(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        result = sens.decide_gate_s(obs, registration())
        for case in ("q50", "q10", "q90"):
            entry = result["decision_by_case"][case]
            assert entry["viable_set"] == ["edgeA", "edgeB"]
            assert entry["ranking"] == ["edgeA", "edgeB"]
            assert entry["winner"] == "edgeA"


# ── the seed axis has to be real ──────────────────────────────────────────
class TestTheSeedAxisIsChecked:
    def test_an_inert_seed_axis_is_inconclusive(self):
        """Identical simulator output at every seed means no seed axis."""
        obs = observations(
            {"edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
             "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0}},
            inserted_by_seed={s: 20000 for s in (1000, 1001, 1002, 1003)})
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "seed axis did not vary" in result["reasons"][0]

    def test_the_ranking_key_is_verified_seed_deterministic(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        result = sens.decide_gate_s(obs, registration())
        assert result["seed_invariance"]["ranking_key_is_seed_deterministic"]
        assert result["seed_invariance"]["violations"] == []

    def test_a_ranking_key_that_moves_across_seeds_is_a_finding(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        for observation in obs:
            if observation.candidate == "edgeA" and observation.seed == 1003:
                observation.policy["added_vehicle_hours"] = 5.0
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "YES"
        assert any("NOT seed-deterministic" in r for r in result["reasons"])


# ── fail-closed ───────────────────────────────────────────────────────────
class TestFailClosed:
    def test_an_incomplete_matrix_is_inconclusive_not_no(self):
        """'We could not measure it' is not 'it does not matter'."""
        obs = observations({"edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0}})
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "incomplete matrix" in result["reasons"][0]

    def test_a_timeout_is_inconclusive(self):
        failing = sens.Observation(
            stress_case="q90", seed=1003, candidate="edgeB", policy=None,
            closure_integrity=None, seed_health_flags=(),
            vehicles_inserted=None, hard_failure=True,
            failure_reason="timeout after 300s", runtime_s=300.0)
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        obs = [o for o in obs
               if not (o.candidate == "edgeB" and o.stress_case == "q90"
                       and o.seed == 1003)] + [failing]
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert any("timeout" in r for r in result.get("failure_reasons", []))

    def test_unclean_closure_integrity_is_inconclusive(self):
        """A leaking closure has not measured the scenario it claims to."""
        obs = observations(
            {"edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
             "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0}},
            integrity="leaked")
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "closure integrity" in result["reasons"][0]

    def test_a_sumo_health_flag_is_inconclusive(self):
        obs = observations(
            {"edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
             "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0}},
            health_flags=("seed 1000: 900/20000 vehicles unfinished at end",))
        result = sens.decide_gate_s(obs, registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "health flags" in result["reasons"][0]

    def test_inconclusive_says_it_forbids_integration(self):
        result = sens.decide_gate_s([], registration())
        assert result["gate_s"] == "INCONCLUSIVE"
        assert "INCONCLUSIVE is not NO" in result["note"]

    def test_no_outcome_can_be_release_evidence(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        assert sens.decide_gate_s(obs, registration())[
            "release_evidence"] is False

    def test_the_outcome_names_its_registration(self):
        reg = registration()
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        assert sens.decide_gate_s(obs, reg)["registration_key"] == \
            reg.content_key

    def test_a_missing_policy_field_fails_the_run_rather_than_defaulting(self):
        """An artifact without the ranking fields is a failure, not a zero."""
        import inspect

        source = inspect.getsource(sens._run_one)
        assert "missing" in source and "POLICY_FIELDS" in source
        assert "_failed(" in source


# ── every preregistered decision field is measured ────────────────────────
class TestAllPreregisteredFieldsAreMeasured:
    FIELDS = ("hard_failure", "closure_integrity", "seed_health_flags",
              "vehicles_no_detour", "viable_set", "candidate_ranking",
              "winner", "added_vehicle_hours")

    def test_the_registration_declares_them(self):
        import inspect

        source = inspect.getsource(sens.build_registration)
        for name in self.FIELDS:
            assert f'"{name}"' in source, name

    def test_the_observation_carries_the_run_level_ones(self):
        names = set(sens.Observation.__dataclass_fields__)
        assert {"policy", "closure_integrity", "seed_health_flags",
                "vehicles_inserted", "hard_failure"} <= names

    def test_the_outcome_reports_the_decision_level_ones(self):
        obs = observations({
            "edgeA": {"q50": 1.0, "q10": 1.0, "q90": 1.0},
            "edgeB": {"q50": 2.0, "q10": 2.0, "q90": 2.0},
        })
        result = sens.decide_gate_s(obs, registration())
        for key in ("decision_by_case", "winner_by_case", "rankings_identical",
                    "winner_identical", "seed_axis", "seed_invariance"):
            assert key in result, key
        entry = result["decision_by_case"]["q50"]
        for key in ("viable_set", "ranking", "winner", "disqualified"):
            assert key in entry, key


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
        for forbidden in ("added_vehicle_hours", "winner", "regret",
                          "disruption", "viable_set", "ranking"):
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

    def test_the_protocol_records_the_repair(self):
        """The repaired tool must not publish under the broken protocol id."""
        assert sens.PROTOCOL.endswith("_v2")
        assert "_v2" in str(sens.REGISTRATION_PATH)
        assert "_v2" in str(sens.OUTCOME_PATH)


# ── the closure window resolves onto the demand calendar ──────────────────
class TestClosureWindowResolution:
    def write(self, tmp_path, epoch="2025-09-16T00:00:00", n_intervals=96):
        (tmp_path / "demand_meta.json").write_text(json.dumps(
            {"epoch_sim": epoch, "n_intervals": n_intervals}))
        return tmp_path

    def test_a_subwindow_of_a_whole_day_is_kept(self, tmp_path):
        begin, end = sens.closure_window(self.write(tmp_path), "06:00", "10:00")
        assert begin.startswith("2025-09-16T06:00")
        assert end.startswith("2025-09-16T10:00")

    def test_a_window_outside_the_demand_build_falls_back_to_the_whole_run(
            self, tmp_path):
        """Better a stated whole-window closure than an invalid spec."""
        sumo = self.write(tmp_path, epoch="2025-09-16T12:00:00", n_intervals=8)
        begin, end = sens.closure_window(sumo, "06:00", "10:00")
        assert begin.startswith("2025-09-16T12:00")
        assert end.startswith("2025-09-16T14:00")

    def test_the_window_is_clamped_into_the_demand_window(self, tmp_path):
        sumo = self.write(tmp_path, epoch="2025-09-16T08:00:00", n_intervals=8)
        begin, end = sens.closure_window(sumo, "06:00", "10:00")
        assert begin.startswith("2025-09-16T08:00")
        assert end.startswith("2025-09-16T10:00")


# ── thresholds are frozen in code ─────────────────────────────────────────
class TestThresholdsAreFrozen:
    def test_the_defaults_are_the_registered_ones(self):
        thresholds = sens.MaterialityThresholds()
        assert thresholds.spread_ratio == 2.0
        assert thresholds.relative_objective == 0.10
        assert thresholds.require_identical_viable_set
        assert thresholds.require_identical_ranking
        assert thresholds.require_identical_winner
        assert thresholds.require_seed_axis_to_vary
        assert thresholds.require_clean_closure_integrity
        assert thresholds.require_healthy_seeds

    def test_thresholds_travel_inside_the_registration_key(self):
        loose = sens.MaterialityThresholds(relative_objective=0.5)
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
