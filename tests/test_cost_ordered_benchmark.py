"""Stage 2: the benchmark must be selected before the outcome, or it is worth nothing.

The failure this file guards against is not a bug — it is a temptation. A
benchmark selected after seeing which case makes the new path look good proves
nothing, and a threshold moved after the run proves less. So the tests pin the
two properties that make the registration meaningful: selection consults only
structural facts, and the run writes a SEPARATE record rather than editing the
registration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "validation" / "cost_ordered_benchmark_registration_v1.json"


@pytest.fixture(scope="module")
def registration() -> dict:
    return json.loads(REGISTRATION.read_text(encoding="utf-8"))


class TestSelectionIsStructural:
    def test_selection_never_runs_a_search_or_prices_a_candidate(
            self, monkeypatch, tmp_path):
        """The selector must not be able to see an outcome, even by accident."""
        from traffic_sim.simulation import deterministic_disruption as dd

        def refuse(*args, **kwargs):
            raise AssertionError("selection consulted a cost or an outcome")

        monkeypatch.setattr(dd, "parent_closure_cost", refuse)
        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "disruption", refuse)

        selection = bench.select_case(tmp_path)
        assert selection["selected"] is None
        assert "no outcome" in selection["selection_rule"]

    def test_it_evaluates_every_case_on_knowable_properties(self, tmp_path):
        selection = bench.select_case(tmp_path)
        assert len(selection["evaluated"]) >= 4
        for item in selection["evaluated"]:
            assert item["candidate_count"] > 0
            assert item["unique_daily_unit_count"] > 0
            assert isinstance(item["work_dates"], list)
            # Nothing outcome-shaped may appear.
            assert "winner" not in item
            assert "cost" not in item
            assert "selected_ids" not in item

    def test_the_cases_are_large_enough_to_discriminate(self, tmp_path):
        """A case with too few candidates could not fail the gate."""
        selection = bench.select_case(tmp_path)
        for item in selection["evaluated"]:
            assert item["candidate_count"] >= bench.MINIMUM_STRUCTURAL_CANDIDATES

    def test_an_archive_missing_any_variant_is_not_counted(self, tmp_path):
        archive = tmp_path / "demand-partial"
        archive.mkdir()
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": "k", "epoch_sim": "2025-03-03T00:00:00",
            "n_intervals": 96, "n_variants": 3,
        }), encoding="utf-8")
        for filename in ("calibrated.rou.xml", "calibrated_v1.rou.xml"):
            (archive / filename).write_text("<routes/>", encoding="utf-8")

        assert bench._archive_index(tmp_path) == {}, (
            "a two-variant archive is not the case the policy describes")

    def test_a_complete_archive_is_indexed_with_its_digests(self, tmp_path):
        archive = tmp_path / "demand-complete"
        archive.mkdir()
        (archive / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": "key-1", "epoch_sim": "2025-03-03T00:00:00",
            "n_intervals": 96, "n_variants": 3,
        }), encoding="utf-8")
        for filename in bench.VARIANT_FILENAMES.values():
            (archive / filename).write_text(f"<routes id='{filename}'/>",
                                            encoding="utf-8")

        index = bench._archive_index(tmp_path)
        assert set(index) == {"key-1"}
        routes = index["key-1"]["routes"]
        assert set(routes) == {"q10", "q50", "q90"}
        digests = {item["sha256"] for item in routes.values()}
        assert len(digests) == 3, "each variant must be bound separately"


class TestTheRegistration:
    def test_it_is_frozen_and_activates_nothing(self, registration):
        assert registration["evidence_class"] == "preregistration"
        assert registration["release_evidence"] is False
        boundary = registration["claim_boundary"]
        assert boundary["activates_policy_v3"] is False
        assert boundary["opens_global_best"] is False
        assert boundary["permits_ui_claim"] is False

    def test_it_binds_every_input_the_stage_requires(self, registration):
        for key in ("policies", "sources", "network", "network_metadata",
                    "demand_variants", "resource_caps", "seeds",
                    "output_roots", "comparison_metrics", "gate_thresholds"):
            assert key in registration, key
        assert set(registration["policies"]) == {"exhaustive", "cost_ordered"}
        assert registration["network"]["sha256"]
        assert registration["resource_caps"]["maximum_daily_units"] == 10_000

    def test_the_thresholds_are_fixed_in_advance(self, registration):
        thresholds = registration["gate_thresholds"]
        assert thresholds["sumo_verifications_saved_minimum"] >= 1
        assert thresholds["selected_ids_identical"] is True
        assert thresholds["final_decision_identical"] is True
        assert thresholds["resource_cap_regression_allowed"] is False

    def test_its_content_key_still_describes_its_own_body(self, registration):
        """v1 is FROZEN historical evidence, so it is checked against itself.

        It binds source digests, and those sources have since moved — the
        stage-1 review changed `monthly_search.py`. Requiring a frozen record to
        recompute from a moving tree would force a choice between editing
        history and a permanently red test. Self-consistency is the property
        that actually matters for a preserved artifact; "recomputes from the
        tool" belongs to the CURRENT registration, where it is meaningful.
        """
        body = {key: value for key, value in registration.items()
                if key not in {"content_key", "registered_at"}}
        assert registration["content_key"] == bench._content_key(body)

    def test_it_reports_its_blocker_honestly(self, registration):
        """This environment has no calibrated archives; say so, do not guess."""
        if registration["selected_case"] is not None:
            pytest.skip("a case was selectable in this environment")
        assert registration["status"] == "blocked_no_structurally_eligible_case"
        blocked = registration["blocked_by"]
        assert blocked["archives_available"] == 0
        assert "cost_ordered_benchmark.py --preregister" in (
            blocked["reproducible_command"])

    def test_the_tool_refuses_to_silently_overwrite(self, tmp_path):
        network_dir = tmp_path / "sumo"
        network_dir.mkdir()
        (network_dir / "net.net.xml").write_text("<net/>", encoding="utf-8")
        (network_dir / "network_metadata.json").write_text(
            "{}", encoding="utf-8")
        destination = tmp_path / "registration.json"
        bench.main(["--preregister", "--registration", str(destination),
                    "--runs-root", str(tmp_path),
                    "--data-root", str(tmp_path)])
        with pytest.raises(SystemExit, match="frozen"):
            bench.main(["--preregister", "--registration", str(destination),
                        "--runs-root", str(tmp_path),
                        "--data-root", str(tmp_path)])

    def test_a_run_without_a_selected_case_refuses(self, tmp_path):
        network_dir = tmp_path / "sumo"
        network_dir.mkdir()
        (network_dir / "net.net.xml").write_text("<net/>", encoding="utf-8")
        (network_dir / "network_metadata.json").write_text(
            "{}", encoding="utf-8")
        destination = tmp_path / "registration.json"
        bench.main(["--preregister", "--registration", str(destination),
                    "--runs-root", str(tmp_path),
                    "--data-root", str(tmp_path)])
        with pytest.raises(SystemExit, match="nothing to run"):
            bench.main(["--run", "--registration", str(destination)])


class TestTheOutcomeIsSeparate:
    def _comparison(self, **overrides):
        comparison = {
            "candidate_costs_field_identical": True,
            "hard_failures_identical": True,
            "health_classifications_identical": True,
            "timeout_outcomes_identical": True,
            "ledger_population_complete": True,
            "status_identical": True,
            "selected_ids_identical": True,
            "final_decision_identical": True,
            "sumo_verifications_saved": 7,
            "stop_proof_valid": True,
            "cache_hits_consistent": True,
            "restart_equivalent": True,
            "no_resource_cap_regression": True,
        }
        comparison.update(overrides)
        return comparison

    def test_the_real_v2_failure_is_bound_and_opens_nothing(self):
        registration = json.loads((
            ROOT / "validation" /
            "cost_ordered_benchmark_registration_v2.json"
        ).read_text(encoding="utf-8"))
        outcome = json.loads((
            ROOT / "validation" / "cost_ordered_benchmark_outcome_v2.json"
        ).read_text(encoding="utf-8"))
        body = {key: value for key, value in registration.items()
                if key not in {"content_key", "registered_at"}}
        assert registration["content_key"] == bench._content_key(body)
        assert outcome["registration"]["content_key"] == (
            registration["content_key"])
        assert outcome["status"] == "failed_execution"
        assert outcome["gates"]["passed"] is False
        assert outcome["claim_boundary"]["activates_policy_v3"] is False

    def test_a_passing_outcome_still_does_not_activate_policy_v3(
            self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert outcome["gates"]["passed"] is True
        assert outcome["claim_boundary"]["activates_policy_v3"] is False, (
            "a benchmark alone must not activate the policy")
        assert outcome["claim_boundary"]["opens_global_best"] is False
        assert "held-out" in outcome["claim_boundary"]["reason"]

    def test_zero_saving_fails_the_gate(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(sumo_verifications_saved=0),
            status="measured")
        assert outcome["gates"]["checks"]["sumo_verifications_saved"] is False
        assert outcome["gates"]["passed"] is False

    def test_a_different_selected_set_fails_the_gate(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(selected_ids_identical=False),
            status="measured")
        assert outcome["gates"]["passed"] is False

    def test_an_inconclusive_run_is_recorded_as_it_happened(self,
                                                            registration):
        outcome = bench.build_outcome(
            registration,
            self._comparison(sumo_verifications_saved=0,
                             note="only one health-viable candidate"),
            status="inconclusive")
        assert outcome["status"] == "inconclusive"
        assert outcome["gates"]["passed"] is False
        assert outcome["comparison"]["note"]

    def test_the_outcome_names_its_registration_without_editing_it(
            self, registration):
        before = json.dumps(registration, sort_keys=True)
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert json.dumps(registration, sort_keys=True) == before
        assert outcome["registration"]["content_key"] == (
            registration["content_key"])
        assert outcome["schema"] != registration["schema"]

    def test_the_thresholds_travel_with_the_outcome(self, registration):
        outcome = bench.build_outcome(
            registration, self._comparison(), status="measured")
        assert outcome["gates"]["thresholds"] == bench.GATE_THRESHOLDS


class TestIsolatedDailyResultsCacheRoot:
    """Each arm must get its own real-SUMO-evidence cache, cloned once from a
    shared initial snapshot — see `_isolated_daily_results_cache_root`'s
    docstring for the v5-style contamination this closes.
    """

    def test_two_arms_get_two_distinct_roots(self, tmp_path):
        daily_cost_cache = tmp_path / "costs" / "cache"
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        exhaustive_root, _ = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "exhaustive", bound_source=bound_source)
        cost_ordered_root, _ = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "cost_ordered", bound_source=bound_source)
        assert exhaustive_root != cost_ordered_root
        assert exhaustive_root.parent == cost_ordered_root.parent

    def test_an_unknown_arm_name_is_rejected(self, tmp_path):
        daily_cost_cache = tmp_path / "cache"
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        with pytest.raises(ValueError, match="unknown arm"):
            bench._isolated_daily_results_cache_root(
                daily_cost_cache, "both", bound_source=bound_source)

    def test_the_clone_reproduces_the_source_snapshot_byte_for_byte(
            self, tmp_path):
        daily_cost_cache = tmp_path / "costs" / "cache"
        source = daily_cost_cache.parent / "daily-results"
        (source / "ab").mkdir(parents=True)
        (source / "ab" / "abcdef.json").write_text(
            '{"schema": "x"}', encoding="utf-8")
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        dest, digest = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "exhaustive", bound_source=bound_source)
        assert (dest / "ab" / "abcdef.json").read_text(
            encoding="utf-8") == '{"schema": "x"}'
        assert digest == bench._tree_digest(source)

    def test_a_pre_existing_destination_is_refused_not_reused(self, tmp_path):
        """A fresh comparison never silently reuses an arbitrary leftover root.

        Only an explicit restart probe may reuse a root — and it does so by
        holding the SAME already-obtained Python object, never by calling
        this function again for a destination that already exists.
        """
        daily_cost_cache = tmp_path / "costs" / "cache"
        dest = (daily_cost_cache.parent
               / f"{daily_cost_cache.name}-daily-results-exhaustive")
        dest.mkdir(parents=True)
        (dest / "leftover.json").write_text("{}", encoding="utf-8")
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        with pytest.raises(RuntimeError, match="already exists"):
            bench._isolated_daily_results_cache_root(
                daily_cost_cache, "exhaustive", bound_source=bound_source)

    def test_distinct_cases_sharing_a_parent_never_collide(self, tmp_path):
        """A suite case only varies `daily_cost_cache`'s BASENAME — the

        destination must be keyed on the full path, not merely its parent,
        or every case in a suite would collide on one shared per-arm root.
        """
        case_one = tmp_path / "costs" / "cache-case-1"
        case_two = tmp_path / "costs" / "cache-case-2"
        dest_one, _ = bench._isolated_daily_results_cache_root(
            case_one, "exhaustive",
            bound_source=bench._bind_daily_results_source_snapshot(case_one))
        dest_two, _ = bench._isolated_daily_results_cache_root(
            case_two, "exhaustive",
            bound_source=bench._bind_daily_results_source_snapshot(case_two))
        assert dest_one != dest_two

    def test_two_clones_from_the_same_source_have_identical_digests(
            self, tmp_path):
        daily_cost_cache = tmp_path / "costs" / "cache"
        source = daily_cost_cache.parent / "daily-results"
        (source / "cd").mkdir(parents=True)
        (source / "cd" / "cdef01.json").write_text("{}", encoding="utf-8")
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        _, exhaustive_digest = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "exhaustive", bound_source=bound_source)
        _, cost_ordered_digest = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "cost_ordered", bound_source=bound_source)
        assert exhaustive_digest == cost_ordered_digest

    def test_an_absent_source_still_produces_an_empty_verified_clone(
            self, tmp_path):
        daily_cost_cache = tmp_path / "nowhere" / "cache"
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        dest, digest = bench._isolated_daily_results_cache_root(
            daily_cost_cache, "exhaustive", bound_source=bound_source)
        assert dest.is_dir()
        assert list(dest.iterdir()) == []
        assert digest == bench._tree_digest(dest)

    def test_a_source_that_drifted_before_binding_is_still_a_valid_bind(
            self, tmp_path):
        """Binding just reads whatever the source currently is — drift is

        only ever detected AFTER a digest has been bound, by
        `_assert_daily_results_source_unchanged` or a second clone reusing
        the same bound digest.
        """
        daily_cost_cache = tmp_path / "costs" / "cache"
        source = daily_cost_cache.parent / "daily-results"
        source.mkdir(parents=True)
        bound_source = bench._bind_daily_results_source_snapshot(
            daily_cost_cache)
        (source / "late.json").write_text("{}", encoding="utf-8")
        with pytest.raises(RuntimeError, match="changed since it was bound"):
            bench._isolated_daily_results_cache_root(
                daily_cost_cache, "exhaustive", bound_source=bound_source)


class TestFreshSnapshotPairMatches:
    """A benchmark's two arms must be provably cloned from ONE snapshot.

    `_assert_fresh_snapshot_pair_matches` is the guard, now unconditional:
    every call into a comparison clones fresh (a pre-existing destination is
    refused by `_isolated_daily_results_cache_root` itself), so a mismatched
    pair can only mean the shared source drifted between the two clones —
    there is no more "legitimately resumed, so exempt" case to carve out.
    """

    def test_a_matching_fresh_pair_is_accepted(self, tmp_path):
        bench._assert_fresh_snapshot_pair_matches(
            tmp_path / "costs" / "cache", ("exhaustive", "cost_ordered"),
            {"exhaustive": {"digest": "same"},
             "cost_ordered": {"digest": "same"}})

    def test_a_drifted_fresh_pair_is_refused(self, tmp_path):
        with pytest.raises(RuntimeError, match="different content"):
            bench._assert_fresh_snapshot_pair_matches(
                tmp_path / "costs" / "cache", ("exhaustive", "cost_ordered"),
                {"exhaustive": {"digest": "one"},
                 "cost_ordered": {"digest": "two"}})
