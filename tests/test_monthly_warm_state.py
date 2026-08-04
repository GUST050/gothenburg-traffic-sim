"""Warm-state contracts: eligibility, identity, shared payload, cold default.

Process-free by construction. A module-level guard fails any test that reaches
subprocess or a SUMO entry point, so "no SUMO ran" is enforced rather than
asserted in prose.
"""
import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

# Imported BEFORE subprocess is patched: importing the production module is not
# the same as calling into SUMO, and some of its dependencies use subprocess at
# import time.
import suggest_closure_time as _legacy

from traffic_sim.simulation.monthly_warm_state import (
    MINIMUM_PREFIX_S, OBSERVATION_SCHEMA, WARM_ALIGNMENT_S,
    WarmStateContractError, build_monthly_observation, evaluate_warm_eligibility,
    monthly_warm_identity, observation_digest, semantic_payload)


from tests.test_warm_state_boundary import bounded_sections


def _empty_accumulator(point=900):
    population = {"schema": "warm_boundary_active_v3",
                  "warm_point_s": point, "captured_at_s": float(point),
                  "vehicle_ids": []}
    population_digest = hashlib.sha256(json.dumps(
        population, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    body = {
        "schema": "warm_prefix_meso_accumulator_v3",
        "warm_point_s": point,
        "output_precision": 16,
        "active_population_digest": population_digest,
        "entries": {},
    }
    body["digest"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    return body


def _completed_100():
    return {"a": 33.33, "b": 33.33, "c": 33.34}


def _audit(**over):
    """A structurally valid route-mutation audit for fixtures."""
    base = {"schema": "warm_route_mutation_audit_v1",
            "original_sha256": "a" * 64, "filtered_sha256": "b" * 64,
            "original_vehicles": 10, "filtered_vehicles": 10,
            "changed_vehicles": 1, "dropped_vehicles": 0,
            "earliest_affected_depart_s": 90000.0}
    base.update(over)
    return base


def _full_metrics(**over):
    base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 1,
            "unfinished_waiting_trips": 1, "teleport_total": 1,
            "teleport_reasons": {"jam": 1}, "loaded": 5, "inserted": 5,
            "running_at_end": 0, "waiting_at_end": 0,
            "truncated_unreachable": 0, "dropped_unreachable": 0,
            "max_queue_vehicles": 5, "closed_edge_throughput": 0}
    base.update(over)
    return base


def _correction(raw, corrected=None, *, applied=0, deficit=0.0,
                combined_total=None):
    corrected = dict(raw if corrected is None else corrected)
    raw = dict(raw)
    return {
        "schema": "warm_meso_reconciliation_v2",
        "active_count": applied,
        "resumed_count": corrected["trip_count"],
        "corrected_count": applied,
        "prefix_accumulator_total_s": deficit,
        "raw_total_time_loss_s": raw["total_time_loss_s"],
        "corrected_total_time_loss_s": corrected["total_time_loss_s"],
        "combined_total_time_loss_s": (corrected["total_time_loss_s"]
                                       if combined_total is None
                                       else combined_total),
        "correction_delta_s": (corrected["total_time_loss_s"]
                               - raw["total_time_loss_s"]),
        "trip_count": corrected["trip_count"],
        "tripinfo_precision": 2,
        "prefix_accumulator_digest": "a" * 64,
        "records_digest": "b" * 64,
    }


def _diag(point, candidate=None, **over):
    """Split diagnostics that RECONSTRUCT to `candidate` exactly.

    The prefix takes half the completed trips and time loss; the post-warm
    segment takes the rest and carries the cumulative counters and end state.
    Reconstruction therefore reproduces `candidate`, which is what the
    cross-section check requires.
    """
    target = dict(candidate if candidate is not None else _full_metrics(
        total_time_loss_s=10.0, trip_count=5))
    prefix_trips = min(target["trip_count"] // 2, target["inserted"])
    prefix_loss = round(target["total_time_loss_s"] / 2, 6)
    post = dict(target,
                total_time_loss_s=target["total_time_loss_s"] - prefix_loss,
                trip_count=target["trip_count"] - prefix_trips)
    post["unfinished_trips"] = min(post["unfinished_trips"], post["trip_count"])
    post["unfinished_waiting_trips"] = min(post["unfinished_waiting_trips"],
                                           post["unfinished_trips"])
    base = {"route_audit": _audit(), "selected_warm_point_s": point,
            "prefix_completed_trips": {"total_time_loss_s": prefix_loss,
                                       "trip_count": prefix_trips},
            "prefix_counters": {"loaded": target["inserted"],
                                "inserted": target["inserted"],
                                "teleport_total": target["teleport_total"]},
            "prefix_teleport_reasons": dict(target["teleport_reasons"]),
            "prefix_queue_maximum": (None if target["max_queue_vehicles"] is None
                                     else 0),
            **bounded_sections(
                point, total_time_loss_s=target["total_time_loss_s"],
                prefix_trips=prefix_trips,
                resumed_trips=target["trip_count"] - prefix_trips),
            "raw_post_metrics": post,
            "corrected_post_metrics": post,
            "restore_correction": _correction(
                post, combined_total=target["total_time_loss_s"]),
            "reconstructed_metrics": dict(target,
                                          unfinished_trips=post["unfinished_trips"],
                                          unfinished_waiting_trips=post[
                                              "unfinished_waiting_trips"])}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _no_subprocess_or_sumo(monkeypatch):
    """This task may not run SUMO or any subprocess. Enforce it."""
    def forbidden(*args, **kwargs):
        raise AssertionError("no subprocess or SUMO call is permitted here")

    for name in ("run", "check_output", "call", "check_call", "Popen"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(_legacy, "simulate_closure", forbidden)
    yield


# ── Fixtures shaped exactly like the production dataclasses ──────────────────
def _metrics(**over):
    base = {
        "total_time_loss_s": 1234.5, "trip_count": 900, "unfinished_trips": 3,
        "unfinished_waiting_trips": 1, "teleport_total": 2,
        "teleport_reasons": {"jam": 2}, "loaded": 1000, "inserted": 995,
        "running_at_end": 4, "waiting_at_end": 2, "truncated_unreachable": 1,
        "dropped_unreachable": 0, "max_queue_vehicles": 17,
        "closed_edge_throughput": None,
    }
    base.update(over)
    return base


def _recovery(**over):
    base = {"status": "recovered", "recovered": True, "recovered_at_s": 7200,
            "recovery_time_s": 900, "evaluated_buckets": 8,
            "required_consecutive_buckets": 2, "max_excess_time_loss_s": 12.5}
    base.update(over)
    return base


def _feasibility(**over):
    base = {"eligible": True, "hard_failures": [], "detour": {"status": "ok"},
            "queue": {"status": "measured"}, "paired_delta_time_loss": None}
    base.update(over)
    return base


def _observation(**over):
    kwargs = dict(
        schedule_id="closure-abc", variant="q50", seed=1000,
        simulation_mode="meso",
        baseline_metrics=_metrics(total_time_loss_s=1000.0),
        candidate_metrics=_metrics(total_time_loss_s=1234.5),
        baseline_buckets=[{"start_s": 0, "end_s": 900, "time_loss_s": 5.0}],
        candidate_buckets=[{"start_s": 0, "end_s": 900, "time_loss_s": 6.0}],
        feasibility=_feasibility(), recovery=_recovery(), failures=[],
        matched_baseline_id="mb-1", provenance_key="study-1",
        provenance={"demand_build_id": "d1", "simulation_mode": "meso"},
    )
    kwargs.update(over)
    if kwargs.get("execution_arm") == "warm" and \
            kwargs.get("split_diagnostics") is None and \
            kwargs.get("warm_point_s") is not None:
        kwargs["split_diagnostics"] = _diag(
            kwargs["warm_point_s"],
            candidate=_metrics_of(kwargs["candidate_metrics"]))
    return build_monthly_observation(**kwargs)


def _metrics_of(value):
    import dataclasses
    return dict(value) if isinstance(value, dict) else dataclasses.asdict(value)


class TestWarmEligibility:
    """Criterion 2: deterministic and fail-closed. Anything odd stays cold."""

    def _decide(self, **over):
        kwargs = dict(closures=[{"edge_id": "e", "begin_s": 25200, "end_s": 54000}],
                      scenario_start_s=0, simulation_mode="meso",
                      duration_s=432000)
        kwargs.update(over)
        return evaluate_warm_eligibility(**kwargs)

    def test_a_normal_time_windowed_closure_is_eligible(self):
        decision = self._decide()
        assert decision.eligible and decision.reason == "eligible"
        # 25200 is itself aligned, but the warm point must be STRICTLY before
        # the closure, so it is the previous boundary, not 25200 itself.
        assert decision.warm_point_s == 25200 - WARM_ALIGNMENT_S == 24300
        assert decision.warm_point_s < 25200

    def test_the_warm_point_is_strictly_before_the_closure(self):
        # a closure exactly on an alignment boundary must not warm up TO it
        decision = self._decide(
            closures=[{"edge_id": "e", "begin_s": WARM_ALIGNMENT_S * 4,
                       "end_s": WARM_ALIGNMENT_S * 8}])
        assert decision.warm_point_s < WARM_ALIGNMENT_S * 4
        assert decision.warm_point_s % WARM_ALIGNMENT_S == 0

    def test_offset_zero_stays_cold(self):
        d = self._decide(closures=[{"edge_id": "e", "begin_s": 0, "end_s": 3600}])
        assert not d.eligible and d.reason == "whole_window_or_offset_zero"

    def test_baseline_only_stays_cold(self):
        for empty in (None, [], ()):
            d = self._decide(closures=empty)
            assert not d.eligible and d.reason == "baseline_only"

    def test_unsupported_mode_stays_cold(self):
        d = self._decide(simulation_mode="micro")
        assert not d.eligible and d.reason == "unsupported_mode"

    def test_a_nonzero_scenario_start_is_ambiguous_and_stays_cold(self):
        d = self._decide(scenario_start_s=900)
        assert not d.eligible and d.reason == "ambiguous_envelope_start"

    def test_a_prefix_shorter_than_required_stays_cold(self):
        d = self._decide(closures=[{"edge_id": "e", "begin_s": MINIMUM_PREFIX_S,
                                    "end_s": MINIMUM_PREFIX_S + 3600}])
        assert not d.eligible and d.reason == "prefix_shorter_than_required"

    def test_a_closure_inside_the_first_interval_stays_cold(self):
        d = self._decide(closures=[{"edge_id": "e", "begin_s": 60, "end_s": 3600}])
        assert not d.eligible and d.reason == "no_positive_aligned_warm_point"

    def test_the_earliest_closure_governs_a_multi_interval_schedule(self):
        d = self._decide(closures=[
            {"edge_id": "e", "begin_s": 90000, "end_s": 93600},
            {"edge_id": "e", "begin_s": 25200, "end_s": 54000}])
        assert d.eligible and d.warm_point_s < 25200

    def test_a_closure_beyond_the_archive_stays_cold(self):
        d = self._decide(closures=[{"edge_id": "e", "begin_s": 25200,
                                    "end_s": 999999}])
        assert not d.eligible and d.reason == "closure_outside_archive"

    @pytest.mark.parametrize("closure", [
        {"edge_id": "e", "begin_s": "25200", "end_s": 54000},
        {"edge_id": "e", "begin_s": 25200, "end_s": 25200},
        {"edge_id": "e", "begin_s": 25200, "end_s": 3600},
        {"edge_id": "e", "begin_s": True, "end_s": 54000},
        {"edge_id": "e"},
    ])
    def test_malformed_closures_stay_cold(self, closure):
        d = self._decide(closures=[closure])
        assert not d.eligible

    def test_an_ineligible_decision_has_no_warm_point(self):
        assert self._decide(simulation_mode="micro").warm_point_s is None


class TestSharedObservationPayload:
    """Criterion 1: one canonical payload, no reduced substitute."""

    def test_the_payload_carries_every_required_group(self):
        obs = _observation()
        assert obs["schema"] == OBSERVATION_SCHEMA
        for key in ("baseline_time_loss_s", "candidate_time_loss_s",
                    "baseline_metrics", "candidate_metrics", "hard_failures",
                    "feasibility", "health", "truncation", "recovery",
                    "recovery_buckets", "matched_baseline_id", "provenance_key",
                    "provenance"):
            assert key in obs, key

    def test_health_and_truncation_cover_both_sides(self):
        obs = _observation()
        assert set(obs["health"]) == {"baseline", "candidate"}
        assert set(obs["truncation"]) == {"baseline", "candidate"}
        assert obs["truncation"]["candidate"]["truncated_unreachable"] == 1
        assert obs["health"]["candidate"]["running_at_end"] == 4

    def test_recovery_buckets_are_kept_not_just_the_verdict(self):
        obs = _observation()
        assert obs["recovery"]["status"] == "recovered"
        assert obs["recovery_buckets"]["baseline"][0]["time_loss_s"] == 5.0

    def test_a_reduced_metrics_object_is_refused(self):
        """The exact failure mode criterion 1 exists to prevent."""
        with pytest.raises(WarmStateContractError, match="missing production field"):
            _observation(candidate_metrics={"total_time_loss_s": 1.0})

    def test_a_production_dataclass_is_accepted(self):
        from traffic_sim.simulation.metrics import DisruptionMetrics
        metrics = DisruptionMetrics(**{k: v for k, v in _metrics().items()})
        obs = _observation(candidate_metrics=metrics)
        assert obs["candidate_metrics"]["trip_count"] == 900

    def test_missing_recovery_or_feasibility_is_refused(self):
        with pytest.raises(WarmStateContractError):
            _observation(recovery=None)
        with pytest.raises(WarmStateContractError):
            _observation(feasibility={"eligible": True})

    def test_non_finite_values_are_refused_at_build_time(self):
        with pytest.raises(ValueError):
            _observation(candidate_metrics=_metrics(total_time_loss_s=float("nan")))

    def test_warm_and_cold_arms_must_declare_their_warm_point_honestly(self):
        with pytest.raises(WarmStateContractError):
            _observation(execution_arm="warm")            # no warm point
        with pytest.raises(WarmStateContractError):
            _observation(warm_point_s=1800)               # cold with a point
        assert _observation(execution_arm="warm",
                            warm_point_s=1800)["warm_point_s"]


class TestCrossArmComparison:
    """Criterion 7: arms compare semantics, not execution detail."""

    def test_identical_semantics_match_across_arms(self):
        cold = _observation()
        warm = _observation(execution_arm="warm", warm_point_s=25200)
        assert semantic_payload(cold) == semantic_payload(warm)
        assert observation_digest(cold) == observation_digest(warm)

    def test_execution_detail_is_excluded_from_the_comparison(self):
        cold = _observation()
        warm = _observation(execution_arm="warm", warm_point_s=25200)
        assert cold["execution_arm"] != warm["execution_arm"]
        assert "execution_arm" not in semantic_payload(cold)
        assert "warm_point_s" not in semantic_payload(cold)

    @pytest.mark.parametrize("field, value", [
        ("candidate_metrics", _metrics(total_time_loss_s=1234.6)),
        ("baseline_metrics", _metrics(running_at_end=99)),
        ("recovery", _recovery(status="congestion_not_dissipated", recovered=False)),
        ("failures", ["recovery_congestion_not_dissipated"]),
        ("feasibility", _feasibility(eligible=False, hard_failures=["x"])),
        ("matched_baseline_id", "mb-2"),
        ("candidate_buckets", [{"start_s": 0, "end_s": 900, "time_loss_s": 6.5}]),
    ])
    def test_any_semantic_difference_is_detected(self, field, value):
        cold = _observation()
        warm = _observation(execution_arm="warm", warm_point_s=25200,
                            **{field: value})
        assert observation_digest(cold) != observation_digest(warm), field

    def test_a_health_only_difference_is_still_a_mismatch(self):
        """Health is semantic: a warm run that ends differently is NOT equal."""
        cold = _observation()
        warm = _observation(execution_arm="warm", warm_point_s=25200,
                            candidate_metrics=_metrics(teleport_total=3))
        assert observation_digest(cold) != observation_digest(warm)


class TestWarmIdentityUsesUnfilteredRoute:
    """Criterion 3: identity binds the archive's own variant, STRUCTURALLY.

    Sol review 2026-07-28: a filename check passed the same filtered bytes under
    another name. Binding is now by resolved path AND digest against the archive.
    """

    def _archive(self, tmp_path, monkeypatch):
        import traffic_sim.simulation.warm_state_cache as wsc
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "calibrated.rou.xml").write_text("<routes/>")
        net = tmp_path / "net.net.xml"
        net.write_text("<net/>")
        return archive, net

    def _build(self, archive, net, tmp_path, **over):
        kwargs = dict(demand_build_id="d1", demand_variant="q50", seed=1000,
                      simulation_mode="meso", warm_point_s=25200,
                      network_path=net,
                      unfiltered_route_path=archive / "calibrated.rou.xml",
                      source_files={}, sumo_home=tmp_path,
                      archive_dir=archive,
                      expected_variant_filename="calibrated.rou.xml")
        kwargs.update(over)
        return monthly_warm_identity(**kwargs)

    def test_the_archive_variant_binds(self, tmp_path, monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        assert self._build(archive, net, tmp_path).content_key

    def test_a_route_outside_the_archive_is_refused(self, tmp_path, monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        outside = tmp_path / "calibrated.rou.xml"
        outside.write_text("<routes/>")            # identical BYTES, wrong file
        with pytest.raises(WarmStateContractError, match="archive's own"):
            self._build(archive, net, tmp_path, unfiltered_route_path=outside)

    def test_filtered_bytes_renamed_to_look_clean_are_refused(self, tmp_path,
                                                              monkeypatch):
        """The exact hole Sol found: a name check would have passed this."""
        archive, net = self._archive(tmp_path, monkeypatch)
        disguised = tmp_path / "calibrated.rou.xml"
        disguised.write_text("<routes><!-- closure filtered --></routes>")
        with pytest.raises(WarmStateContractError, match="archive's own"):
            self._build(archive, net, tmp_path, unfiltered_route_path=disguised)

    def test_a_missing_archive_binding_is_refused(self, tmp_path, monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        with pytest.raises(WarmStateContractError, match="archive directory"):
            self._build(archive, net, tmp_path, archive_dir=None)
        with pytest.raises(WarmStateContractError, match="archive directory"):
            self._build(archive, net, tmp_path, expected_variant_filename=None)

    def test_a_missing_archive_variant_is_refused(self, tmp_path, monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        with pytest.raises(WarmStateContractError, match="archive variant is missing"):
            self._build(archive, net, tmp_path,
                        expected_variant_filename="calibrated_v1.rou.xml")

    def test_identity_binds_seed_variant_mode_and_warm_point(self, tmp_path,
                                                             monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        base = self._build(archive, net, tmp_path).content_key
        for over in ({"seed": 1001}, {"demand_variant": "q10"},
                     {"warm_point_s": 26100}, {"demand_build_id": "d2"},
                     {"simulation_mode": "micro"}):
            assert self._build(archive, net, tmp_path, **over).content_key != base, over

    def test_identity_changes_when_the_archive_route_bytes_change(self, tmp_path,
                                                                  monkeypatch):
        archive, net = self._archive(tmp_path, monkeypatch)
        before = self._build(archive, net, tmp_path).content_key
        (archive / "calibrated.rou.xml").write_text("<routes><vehicle id='v'/></routes>")
        assert self._build(archive, net, tmp_path).content_key != before


class TestDefaultIsCold:
    """Criterion 6: default construction and all existing callers stay cold."""

    def test_the_runner_defaults_to_cold(self):
        import inspect
        from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
        signature = inspect.signature(ArchivedDemandSumoRunner.__init__)
        assert signature.parameters["warm_execution"].default is False

    def test_no_production_caller_enables_warm_execution(self):
        """Product, API and monthly search must never opt in."""
        for path in ("traffic_sim/simulation/monthly_search.py",
                     "run_monthly_proxy_validation.py", "serve.py",
                     "run_scenario.py", "suggest_closure_time.py"):
            source = Path(path).read_text()
            assert "warm_execution" not in source, path

    def test_only_the_validation_harness_may_opt_in(self):
        harness = Path("run_monthly_warm_state_validation.py")
        assert harness.is_file()
        assert "warm_execution" in harness.read_text()


class TestForgedAggregationIsRefused:
    """Sol review round 2: the summary is a CLAIM about two numbers we already
    hold, so it must be recomputed from them — not merely checked for shape.

    Before this, a summary claiming 123.0 was accepted while the recorded prefix
    and post-warm totals summed to 999.0. Types, field sets and trip counts all
    passed; the one value the summary exists to assert went unverified.
    """

    def _post(self, **over):
        base = _full_metrics(total_time_loss_s=499.0, trip_count=2)
        base.update(over)
        return base

    def _diagnostics(self, *, claimed_total, prefix_total=500.0,
                     prefix_trips=3, post_total=499.0, post_trips=2):
        from tests.test_warm_state_boundary import aggregation, snapshot_facts
        post = self._post(total_time_loss_s=post_total, trip_count=post_trips)
        return {
            "route_audit": _audit(),
            "selected_warm_point_s": 900,
            "prefix_completed_trips": {"total_time_loss_s": prefix_total,
                                       "trip_count": prefix_trips},
            "prefix_counters": {"loaded": 5, "inserted": 5, "teleport_total": 0},
            "prefix_teleport_reasons": {},
            "prefix_queue_maximum": 0,
            "snapshot_facts": snapshot_facts(900),
            "prefix_aggregation": aggregation(
                total_time_loss_s=claimed_total, prefix_trips=prefix_trips,
                resumed_trips=post_trips),
            "raw_post_metrics": post,
            "corrected_post_metrics": post,
            "restore_correction": _correction(
                post, combined_total=prefix_total + post_total),
            "reconstructed_metrics": _full_metrics(
                total_time_loss_s=claimed_total,
                trip_count=prefix_trips + post_trips,
                unfinished_trips=post["unfinished_trips"],
                unfinished_waiting_trips=post["unfinished_waiting_trips"]),
        }

    def test_the_exact_reported_forgery_is_refused(self):
        """Claimed 123.0; recorded inputs sum to 999.0."""
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_split_diagnostics)
        with pytest.raises(WarmStateContractError,
                           match="does not follow from the evidence"):
            validate_split_diagnostics(
                self._diagnostics(claimed_total=123.0), 900)

    def test_an_honest_total_still_passes(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        assert validate_split_diagnostics(
            self._diagnostics(claimed_total=999.0), 900)

    @pytest.mark.parametrize("claimed", [0.0, 998.99, 999.01, 1e9])
    def test_any_total_other_than_the_recomputed_one_is_refused(self, claimed):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, validate_split_diagnostics)
        with pytest.raises(WarmStateContractError):
            validate_split_diagnostics(
                self._diagnostics(claimed_total=claimed), 900)

    def test_a_forged_caller_supplied_summary_is_refused(self):
        """The OTHER path: `reconstruct_metrics` accepts a precomputed summary so
        an observation and its diagnostics cannot disagree — which is only safe
        if the summary is re-derived from the caller's own inputs."""
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, build_prefix_evidence, reconstruct_metrics)
        from tests.test_warm_state_boundary import aggregation, snapshot_facts
        evidence = build_prefix_evidence(
            completed_trips={"total_time_loss_s": 100.0, "trip_count": 3},
            queue_maximum=0, warm_point_s=900,
            counters={"loaded": 5, "inserted": 5, "teleport_total": 0,
                      "teleport_reasons": {}},
            recovery_buckets=[{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
            snapshot_facts=snapshot_facts(900),
            active_accumulator=_empty_accumulator(),
            completed_records=_completed_100(),
            completed_record_order=list(_completed_100()))
        post = _full_metrics(total_time_loss_s=50.0, trip_count=2)
        with pytest.raises(WarmStateContractError,
                           match="does not follow from the evidence"):
            reconstruct_metrics(
                evidence, post, expected_warm_point_s=900,
                aggregation=aggregation(total_time_loss_s=7.0, prefix_trips=3,
                                        resumed_trips=2))

    def test_a_forged_trip_count_in_a_supplied_summary_is_refused(self):
        from traffic_sim.simulation.monthly_warm_state import (
            WarmStateContractError, build_prefix_evidence, reconstruct_metrics)
        from tests.test_warm_state_boundary import aggregation, snapshot_facts
        evidence = build_prefix_evidence(
            completed_trips={"total_time_loss_s": 100.0, "trip_count": 3},
            queue_maximum=0, warm_point_s=900,
            counters={"loaded": 5, "inserted": 5, "teleport_total": 0,
                      "teleport_reasons": {}},
            recovery_buckets=[{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
            snapshot_facts=snapshot_facts(900),
            active_accumulator=_empty_accumulator(),
            completed_records=_completed_100(),
            completed_record_order=list(_completed_100()))
        post = _full_metrics(total_time_loss_s=50.0, trip_count=2)
        with pytest.raises(WarmStateContractError,
                           match="does not follow from the evidence"):
            reconstruct_metrics(
                evidence, post, expected_warm_point_s=900,
                aggregation=aggregation(total_time_loss_s=150.0, prefix_trips=99,
                                        resumed_trips=2))

    def test_the_honest_supplied_summary_still_reconstructs(self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_prefix_evidence, reconstruct_metrics)
        from tests.test_warm_state_boundary import aggregation, snapshot_facts
        evidence = build_prefix_evidence(
            completed_trips={"total_time_loss_s": 100.0, "trip_count": 3},
            queue_maximum=0, warm_point_s=900,
            counters={"loaded": 5, "inserted": 5, "teleport_total": 0,
                      "teleport_reasons": {}},
            recovery_buckets=[{"begin_s": 0, "end_s": 900, "time_loss_s": 1.0}],
            snapshot_facts=snapshot_facts(900),
            active_accumulator=_empty_accumulator(),
            completed_records=_completed_100(),
            completed_record_order=list(_completed_100()))
        post = _full_metrics(total_time_loss_s=50.0, trip_count=2)
        out = reconstruct_metrics(
            evidence, post, expected_warm_point_s=900,
            aggregation=aggregation(total_time_loss_s=150.0, prefix_trips=3,
                                    resumed_trips=2))
        assert out["total_time_loss_s"] == 150.0
        assert out["trip_count"] == 5

    def test_a_continued_xml_order_total_survives_without_segment_regrouping(
            self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_prefix_evidence, reconstruct_metrics)
        from traffic_sim.simulation.warm_state_boundary import (
            aggregate_split_time_loss)
        from tests.test_warm_state_boundary import snapshot_facts
        evidence = build_prefix_evidence(
            completed_trips={"total_time_loss_s": 0.01, "trip_count": 1},
            queue_maximum=0, warm_point_s=900,
            counters={"loaded": 3, "inserted": 3, "teleport_total": 0,
                      "teleport_reasons": {}},
            recovery_buckets=[{"begin_s": 0, "end_s": 900,
                               "time_loss_s": 0.0}],
            snapshot_facts=snapshot_facts(900),
            active_accumulator=_empty_accumulator(),
            completed_records={"prefix": 0.01},
            completed_record_order=["prefix"])
        post = _full_metrics(total_time_loss_s=0.08, trip_count=2,
                             loaded=3, inserted=3)
        continued = 0.09000000000000001
        summary = aggregate_split_time_loss(
            completed_prefix_total=0.01, completed_prefix_trips=1,
            resumed_total=0.08, resumed_trips=2,
            continued_total=continued)
        out = reconstruct_metrics(
            evidence, post, expected_warm_point_s=900,
            aggregation=summary, continued_total=continued)
        assert out["total_time_loss_s"] == continued


class TestTraciPreflightOrdering:
    """LUNA-WARM-15 criterion 3.

    v6 burned an approved, non-resumable campaign discovering that TraCI could
    not be imported. The resolver now runs as a mandatory preflight AFTER the
    approval token is validated and BEFORE the output root is checked or
    created, so an environment that cannot warm never reaches the point of
    producing evidence.
    """

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "warm_harness_pre", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _order(self, monkeypatch, *, traci_ok, token_ok=True):
        """Record the order of the guarded steps for one --execute call."""
        harness = self._harness()
        calls = []
        manifest = {"content_key": "k", "artifact_root": "runs/x",
                    "cases": [1], "seeds": [1], "simulation_mode": "meso"}

        monkeypatch.setattr(harness, "load_frozen_manifest",
                            lambda path: manifest)

        def approval(m, token):
            calls.append("approval")
            if not token_ok:
                raise harness.HarnessError("requires an approval token")

        def preflight(home=None):
            calls.append("traci")
            if not traci_ok:
                raise harness.HarnessError(
                    "TraCI preflight failed, so no campaign was started and no "
                    "artifact root was created: boom")
            return {"origin": "/fake/traci/__init__.py"}

        def absent_root(m):
            calls.append("root")
            from pathlib import Path
            return Path("runs/x/k")

        def campaign(m, root):
            calls.append("campaign")
            return {"status": "pass", "comparison_count": 0,
                    "mismatch_count": 0}

        monkeypatch.setattr(harness, "require_approval", approval)
        monkeypatch.setattr(harness, "require_resolvable_traci", preflight)
        monkeypatch.setattr(harness, "require_absent_root", absent_root)
        monkeypatch.setattr(harness, "run_paired_campaign", campaign)
        try:
            harness.main(["--manifest", "m.json", "--execute",
                          "--approval-token", "k"])
        except SystemExit:
            pass
        return calls

    def test_the_resolver_runs_after_approval_and_before_the_root(
            self, monkeypatch):
        calls = self._order(monkeypatch, traci_ok=True)
        assert calls == ["approval", "traci", "root", "campaign"], calls

    def test_a_resolver_failure_creates_no_root_and_runs_no_campaign(
            self, monkeypatch):
        """The whole point: an unusable environment costs nothing."""
        calls = self._order(monkeypatch, traci_ok=False)
        assert calls == ["approval", "traci"], calls
        assert "root" not in calls and "campaign" not in calls

    def test_an_invalid_token_stops_before_the_resolver(self, monkeypatch):
        """Approval is still the first gate; the preflight does not run for an
        unapproved caller."""
        calls = self._order(monkeypatch, traci_ok=True, token_ok=False)
        assert calls == ["approval"], calls

    def test_the_preflight_uses_the_production_resolver(self):
        """A preflight that checked something else could pass while the
        controller still failed."""
        source = Path("run_monthly_warm_state_validation.py").read_text()
        assert "from traffic_sim.simulation.runtime import" in source
        assert "resolve_traci" in source and "validate_traci_api" in source

    def test_the_import_only_probe_needs_no_manifest_and_creates_nothing(
            self, monkeypatch, tmp_path):
        harness = self._harness()
        seen = []
        monkeypatch.setattr(harness, "require_resolvable_traci",
                            lambda home=None: seen.append("probe") or
                            {"origin": "/fake"})
        monkeypatch.setattr(harness, "load_frozen_manifest",
                            lambda path: pytest.fail("no manifest is needed"))
        assert harness.main(["--check-traci-import-only"]) == 0
        assert seen == ["probe"]
