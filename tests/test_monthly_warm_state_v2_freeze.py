"""The route-safe v2 warm-state contract (LUNA-WARM-06).

Process-free: reads tracked artifacts only. No SUMO, no TraCI, no campaign, no
outcome root. An autouse guard fails any test that reaches a subprocess.

These tests exist because LUNA-WARM-05 produced the first real cold-versus-warm
comparison and it FAILED. Each regression below reproduces one of the three
mechanisms that failure exposed.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Imported before subprocess is patched (its dependencies shell out on import).
import suggest_closure_time as _legacy
from tests.test_warm_state_boundary import (
    bounded_sections, split_aggregation)

from traffic_sim.simulation.monthly_warm_state import (
    ROUTE_AUDIT_SCHEMA, WarmStateContractError, audit_route_mutation,
    reconstruct_metrics as _production_reconstruct_metrics, route_safe_warm_point, stream_route_vehicles,
    validate_post_warm_metrics)

MANIFEST = Path("validation/monthly_warm_state_manifest_v2.json")
ARCHIVE = Path("runs/demand-20260721-222017-41bc682a-bbe1")


APPROVED_ARCHIVE = "demand-20260721-222017-41bc682a-bbe1"
APPROVED_ARCHIVE_FILES = {
    "demand_meta.json", "manifest.json", "calibrated.rou.xml",
    "calibrated_v1.rou.xml", "calibrated_v2.rou.xml",
}


@pytest.fixture(autouse=True)
def _no_subprocess_or_sumo(monkeypatch):
    """No subprocess, and no touching an unapproved `runs/` path.

    An `open` hook is not enough: `Path.exists()` and `Path.stat()` reach an
    unapproved location without ever opening it, and this revision's approval
    covers exactly five archive files. The guard therefore covers existence and
    stat operations too, and permits only those five.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("no subprocess or SUMO call is permitted here")

    for name in ("run", "check_output", "call", "check_call", "Popen"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(_legacy, "simulate_closure", forbidden)

    import builtins
    import io
    import pathlib
    real_stat, real_exists = pathlib.Path.stat, pathlib.Path.exists
    real_open = builtins.open

    def guarded_open(file, *a, **k):
        if isinstance(file, (str, pathlib.Path)):
            _check(pathlib.Path(str(file)))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    # `Path.open`, `Path.read_text` and `Path.read_bytes` reach `io.open`, not
    # `builtins.open`, so patching only the latter left every Path read
    # unguarded — which is exactly how a forbidden read would slip through.
    monkeypatch.setattr(io, "open", guarded_open)

    def _check(self):
        """EXACT parent and filename, not a substring match.

        A substring test would accept any `runs/` path that merely CONTAINS the
        approved archive name, so it could not prove the five-file boundary this
        task's approval grants. Comparison is on the normalised parent directory
        and the exact filename.
        """
        candidate = pathlib.Path(str(self))
        parts = candidate.parts
        if "runs" not in parts:
            return
        index = parts.index("runs")
        tail = parts[index:]
        if len(tail) == 1:                       # `runs` itself
            raise AssertionError(f"unapproved runs/ path touched: {candidate}")
        if tail[1] != APPROVED_ARCHIVE:
            raise AssertionError(f"unapproved runs/ path touched: {candidate}")
        if len(tail) == 2:                       # the archive directory
            return
        if len(tail) != 3 or tail[2] not in APPROVED_ARCHIVE_FILES:
            raise AssertionError(f"unapproved archive path touched: {candidate}")

    def guarded_stat(self, *a, **k):
        _check(self)
        return real_stat(self, *a, **k)

    def guarded_exists(self, *a, **k):
        _check(self)
        return real_exists(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", guarded_stat)
    monkeypatch.setattr(pathlib.Path, "exists", guarded_exists)
    yield


def _attempts(identities, outcome="warm_executed"):
    """Finalized warm-attempt records; evidence now requires one per identity."""
    terminal = ("warm_completed" if outcome == "warm_executed"
                else "invoker_declined")
    return [{"schema": "monthly_warm_attempt_v1", "schedule_id": s,
             "demand_variant": v, "seed": d, "outcome": outcome,
             "events": [{"code": terminal, "details": {}}]}
            for s, v, d in sorted(identities)]


def _load():
    return json.loads(MANIFEST.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _route(tmp_path, name, vehicles):
    path = tmp_path / name
    lines = ["<routes>"]
    for vid, depart, edges in vehicles:
        lines.append(f'<vehicle id="{vid}" depart="{depart}">'
                     f'<route edges="{edges}"/></vehicle>')
    lines.append("</routes>")
    path.write_text("\n".join(lines) + "\n")
    return path



def reconstruct_metrics(prefix_evidence, post_warm, **kwargs):
    """Test shim over the production combiner.

    The fixtures in this module describe splits with an EMPTY boundary ledger,
    so their reconciliation is fully determined by the two aggregates. Supplying
    it here keeps every existing assertion about the OTHER field rules intact
    without restating it at twenty call sites. `split_aggregation` refuses a
    non-empty ledger, so this can never fabricate the boundary case; tests that
    exercise boundary offsets, or that assert production REFUSES an
    unreconciled split, call the production function directly.
    """
    if "aggregation" not in kwargs:
        kwargs["aggregation"] = split_aggregation(prefix_evidence, post_warm)
    return _production_reconstruct_metrics(prefix_evidence, post_warm, **kwargs)


def _prefix(**over):
    """Prefix evidence whose per-vehicle map AGREES with its aggregate.

    The two must describe the same run, so when a test overrides
    `completed_trips` the map is regenerated to match rather than left stale.
    """
    from traffic_sim.simulation.monthly_warm_state import build_prefix_evidence
    kwargs = dict(
        completed_trips={"total_time_loss_s": 5.0, "trip_count": 3},
        queue_maximum=0, warm_point_s=24300,
        counters={"loaded": 3, "inserted": 3, "teleport_total": 1,
                  "teleport_reasons": {"jam": 1}},
        recovery_buckets=[{"begin_s": 0, "end_s": 24300, "time_loss_s": 1.0}],
        snapshot_facts={"schema": "warm_snapshot_facts_v1",
                        "warm_point_s": 24300, "captured_at_s": 24300.0,
                        "active_vehicle_count": 0, "save_state_rng": True,
                        "save_state_precision": 16},
    )
    kwargs.update(over)
    return build_prefix_evidence(**kwargs)


def _post(**over):
    base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 1,
            "unfinished_waiting_trips": 1, "teleport_total": 1,
            "teleport_reasons": {"jam": 1}, "loaded": 5, "inserted": 5,
            "running_at_end": 4, "waiting_at_end": 2,
            "truncated_unreachable": 3, "dropped_unreachable": 1,
            "max_queue_vehicles": 5, "closed_edge_throughput": 0}
    base.update(over)
    return base


class TestTheLunaWarm05DoubleCount:
    """+1081 loaded / +1065 inserted: cumulative counters were being summed."""

    def test_cumulative_counters_are_not_summed(self):
        """The measured shape: prefix 84065-ish, post already includes it."""
        out = reconstruct_metrics(
            _prefix(counters={"loaded": 1081, "inserted": 1065,
                              "teleport_total": 0, "teleport_reasons": {}},
                    completed_trips={"total_time_loss_s": 5.0,
                                     "trip_count": 1000}),
            _post(loaded=84065, inserted=84065))
        assert out["loaded"] == 84065, "summing would have produced 85146"
        assert out["inserted"] == 84065, "summing would have produced 85130"

    def test_a_counter_falling_below_its_prefix_floor_is_refused(self):
        """A restored state that lost history is not a valid split."""
        # both lowered together, so the relational check passes and the
        # cumulative floor is what fires
        with pytest.raises(WarmStateContractError, match="lost history"):
            reconstruct_metrics(_prefix(), _post(loaded=2, inserted=2))

    def test_teleport_reasons_are_cumulative_per_key(self):
        # teleport_total must cover the reason counts, so it rises with them
        out = reconstruct_metrics(
            _prefix(), _post(teleport_total=4, teleport_reasons={"jam": 4}))
        assert out["teleport_reasons"] == {"jam": 4}, "merging would give 5"

    def test_a_teleport_reason_falling_below_its_floor_is_refused(self):
        with pytest.raises(WarmStateContractError, match="fell from 1"):
            reconstruct_metrics(_prefix(), _post(teleport_reasons={"jam": 0}))

    def test_completed_trips_remain_disjoint_and_additive(self):
        out = reconstruct_metrics(_prefix(), _post())
        assert out["trip_count"] == 5
        assert out["total_time_loss_s"] == 10.0


class TestMissingVersusZeroThroughput:
    """cold=0 vs warm=None: "we did not look" is not "nothing crossed"."""

    def test_measured_zero_and_missing_are_different_values(self):
        measured = reconstruct_metrics(_prefix(), _post(closed_edge_throughput=0))
        missing = reconstruct_metrics(_prefix(),
                                      _post(closed_edge_throughput=None))
        assert measured["closed_edge_throughput"] == 0
        assert missing["closed_edge_throughput"] is None
        assert measured != missing

    def test_the_invoker_measures_throughput_when_a_closure_is_present(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        invoker = source.split("def _default_warm_invoker(")[1].split("\n    def ")[0]
        assert "active_closure_throughput" in invoker
        assert "measured_empty_edges" in invoker
        assert "closed_edge_throughput=active_throughput" in invoker

    def test_an_unmeasured_closure_domain_fails_closed(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        invoker = source.split("def _default_warm_invoker(")[1].split("\n    def ")[0]
        assert "if not edge_data.exists():" in invoker
        assert "if active_throughput is None:" in invoker


class TestRouteSafeSplit:
    """A changed vehicle departing before the snapshot invalidates the prefix."""

    def test_an_unchanged_filtering_leaves_the_closure_bound(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml", [("v1", 100.0, "a_b_0")])
        filtered = _route(tmp_path, "f.rou.xml", [("v1", 100.0, "a_b_0")])
        audit = audit_route_mutation(original, filtered)
        assert audit["changed_vehicles"] == 0 and audit["dropped_vehicles"] == 0
        assert audit["earliest_affected_depart_s"] is None
        assert route_safe_warm_point(audit, closure_begin_s=25200) == 24300

    def test_a_changed_vehicle_before_the_old_point_moves_the_split(self, tmp_path):
        """The exact shape criterion 8 asks for: changed depart < 24300."""
        original = _route(tmp_path, "o.rou.xml",
                          [("v1", 12000.0, "a_b_0 b_c_0"), ("v2", 30000.0, "x_y_0")])
        filtered = _route(tmp_path, "f.rou.xml",
                          [("v1", 12000.0, "a_b_0"), ("v2", 30000.0, "x_y_0")])
        audit = audit_route_mutation(original, filtered)
        assert audit["changed_vehicles"] == 1
        assert audit["earliest_affected_depart_s"] == 12000.0
        point = route_safe_warm_point(audit, closure_begin_s=25200)
        assert point == 11700, "the split must precede the changed departure"
        assert point < 12000.0

    def test_a_dropped_vehicle_counts_as_affected(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml",
                          [("v1", 9000.0, "a_b_0"), ("v2", 30000.0, "x_y_0")])
        filtered = _route(tmp_path, "f.rou.xml", [("v2", 30000.0, "x_y_0")])
        audit = audit_route_mutation(original, filtered)
        assert audit["dropped_vehicles"] == 1
        assert audit["earliest_affected_depart_s"] == 9000.0
        assert route_safe_warm_point(audit, closure_begin_s=25200) == 8100

    def test_an_early_affected_vehicle_makes_warming_impossible(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml", [("v1", 100.0, "a_b_0 b_c_0")])
        filtered = _route(tmp_path, "f.rou.xml", [("v1", 100.0, "a_b_0")])
        audit = audit_route_mutation(original, filtered)
        assert route_safe_warm_point(audit, closure_begin_s=25200) is None

    def test_the_warm_path_falls_back_cold_when_no_safe_point_exists(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        assert "no_route_safe_warm_point" in source
        warm = source.split("def run_warm_observation(")[1]
        assert warm.index("route_safe_warm_point") < warm.index("restore_warm_state"), \
            "route safety must be decided BEFORE any state lookup"

    def test_a_filtering_that_invents_a_vehicle_is_refused(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml", [("v1", 100.0, "a_b_0")])
        filtered = _route(tmp_path, "f.rou.xml",
                          [("v1", 100.0, "a_b_0"), ("v9", 200.0, "z_z_0")])
        with pytest.raises(WarmStateContractError, match="unknown vehicle"):
            audit_route_mutation(original, filtered)

    def test_a_duplicate_vehicle_id_is_refused(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml",
                          [("v1", 100.0, "a_b_0"), ("v1", 200.0, "b_c_0")])
        with pytest.raises(WarmStateContractError, match="duplicate vehicle id"):
            audit_route_mutation(original, original)

    def test_an_unsupported_audit_schema_is_refused(self):
        with pytest.raises(WarmStateContractError, match="route audit schema"):
            route_safe_warm_point({"schema": "other"}, closure_begin_s=25200)

    def test_an_unparsable_vehicle_raises_rather_than_being_skipped(self, tmp_path):
        path = tmp_path / "bad.rou.xml"
        path.write_text('<routes>\n<vehicle id="v1"><route edges="a"/></vehicle>\n</routes>\n')
        with pytest.raises(WarmStateContractError, match="unparsable"):
            list(stream_route_vehicles(path))


class TestFrozenV2Contract:

    def test_content_key_recomputes(self):
        assert _canonical_key(_load()) == _load()["content_key"]

    def test_status_is_frozen_unapproved_and_unexecuted(self):
        manifest = _load()
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert "approved_content_key" not in manifest

    def test_the_key_differs_from_the_spent_v1_key(self):
        spent = json.loads(
            Path("validation/monthly_warm_state_manifest_v1.json").read_text())
        assert _load()["content_key"] != spent["content_key"]

    def test_v1_is_not_overwritten(self):
        assert Path("validation/monthly_warm_state_manifest_v1.json").is_file()
        assert MANIFEST.is_file() and MANIFEST.name.endswith("_v2.json")

    def test_the_spent_v2_sources_have_drifted_from_the_live_tree(self):
        """Supersession is VISIBLE as drift, and drift is what blocks reuse.

        Boundary-active accounting changed the files v2 bound. The frozen bytes
        are untouched; they simply no longer describe the live tree, so this
        campaign can never be adopted again.
        """
        fingerprints = _load()["source_fingerprints"]
        drifted = [name for name, digest in fingerprints.items()
                   if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
        assert drifted, "v2 still matches the live tree; it must not after v3"
        assert "traffic_sim/simulation/monthly_warm_state.py" in drifted

    def test_the_five_approved_archive_files_are_bound(self):
        bound = _load()["archive_files_sha256"]
        assert set(bound) == {"demand_meta.json", "manifest.json",
                              "calibrated.rou.xml", "calibrated_v1.rou.xml",
                              "calibrated_v2.rou.xml"}
        for name, digest in bound.items():
            assert hashlib.sha256((ARCHIVE / name).read_bytes()).hexdigest() == digest

    def test_every_variant_has_an_audit_and_a_safe_point(self):
        safety = _load()["route_safety"]
        assert set(safety) == {"q10", "q50", "q90"}
        for variant, record in safety.items():
            audit = record["audit"]
            assert audit["schema"] == ROUTE_AUDIT_SCHEMA
            assert audit["original_vehicles"] > 0
            assert isinstance(record["safe_warm_point_s"], int)
            assert record["safe_warm_point_s"] > 0

    def test_each_safe_point_precedes_its_earliest_affected_departure(self):
        for variant, record in _load()["route_safety"].items():
            earliest = record["audit"]["earliest_affected_depart_s"]
            if earliest is not None:
                assert record["safe_warm_point_s"] < earliest, variant

    def test_each_safe_point_precedes_the_closure(self):
        bound = _load()["cases"][0]["closure_begin_s"]
        for variant, record in _load()["route_safety"].items():
            assert record["safe_warm_point_s"] < bound, variant

    def test_the_audit_recorded_real_route_mutation(self):
        """This closure really does change vehicles — the audit is not vacuous."""
        changed = {v: r["audit"]["changed_vehicles"]
                   for v, r in _load()["route_safety"].items()}
        assert all(count > 0 for count in changed.values()), changed

    def test_the_frozen_case_and_identity_set_are_unchanged_from_v1(self):
        v1 = json.loads(
            Path("validation/monthly_warm_state_manifest_v1.json").read_text())
        manifest = _load()
        assert manifest["seeds"] == v1["seeds"]
        assert manifest["demand_variants"] == v1["demand_variants"]
        assert manifest["cases"][0]["directed_edges"] == v1["cases"][0]["directed_edges"]
        assert manifest["demand_requirement"] == v1["demand_requirement"]
        assert manifest["network_requirement"] == v1["network_requirement"]

    def test_the_comparison_policy_is_closed_and_exact(self):
        policy = _load()["comparison_policy"]
        assert "exact" in policy["tolerance"]
        assert "no cache" in policy["on_mismatch"]

    def test_the_v2_policy_matches_the_live_execution_only_set(self):
        """The CURRENT contract must track the live exclusion set exactly."""
        from traffic_sim.simulation.monthly_warm_state import _EXECUTION_ONLY
        excluded = set(_load()["comparison_policy"]["excluded_from_comparison"])
        assert excluded == set(_EXECUTION_ONLY)
        assert "split_diagnostics" in excluded, (
            "criterion 6: diagnostics are recorded but never compared")

    def test_the_schemas_it_was_frozen_with_are_recorded(self):
        """v2 records the accounting IT was frozen with, not today's.

        v2 was spent by LUNA-WARM-07 and superseded by v3, which introduced
        boundary-active accounting and the v2 prefix-evidence schema. Asserting
        this manifest tracks the LIVE schema would force a spent campaign to
        follow the code forward — exactly the drift its frozen key exists to
        prevent.
        """
        manifest = _load()
        assert manifest["prefix_evidence_schema"] == "monthly_prefix_evidence_v1"
        assert manifest["route_audit_schema"] == ROUTE_AUDIT_SCHEMA

    def test_the_spent_v2_package_no_longer_recomposes(self):
        """Same invariant v1 carries once spent: drift without byte mutation."""
        sys.path.insert(0, "tools")
        from freeze_monthly_warm_state_v2 import build_artifacts
        before = MANIFEST.read_bytes()
        rebuilt = build_artifacts()[str(MANIFEST)]
        assert MANIFEST.read_text() != rebuilt, (
            "the spent v2 contract still reproduces; it must not")
        assert MANIFEST.read_bytes() == before, "recomposition mutated v2"


class TestAllIdentityOrchestration:
    """A q10 hard failure must not suppress q50/q90 evidence."""

    def test_the_harness_requests_every_identity_directly(self):
        source = Path("run_monthly_warm_state_validation.py").read_text()
        campaign = source.split("def run_paired_campaign(")[1]
        assert "_run_observation(" in campaign
        assert "canonical_seed(" in campaign
        assert "run_candidate(" not in campaign, (
            "the harness must not use the fail-fast search entry point")

    def test_production_search_keeps_its_fail_fast_ordering(self):
        source = Path("traffic_sim/simulation/monthly_sumo.py").read_text()
        candidate = source.split("def run_candidate(")[1]
        assert "if failures:" in candidate and "break" in candidate, (
            "production search must still stop at the first hard failure")

    def test_a_failure_on_one_identity_is_recorded_not_swallowed(self):
        source = Path("run_monthly_warm_state_validation.py").read_text()
        assert "observation_failures" in source
        assert "log_failures.append(" in source


class TestNoSumoRanHere:

    def test_the_freeze_tool_runs_no_subprocess(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("the freeze tool must not shell out")

        for name in ("run", "check_output", "call", "check_call", "Popen"):
            monkeypatch.setattr(subprocess, name, forbidden)
        sys.path.insert(0, "tools")
        import freeze_monthly_warm_state_v2 as freeze
        assert freeze.ARCHIVE.name == "demand-20260721-222017-41bc682a-bbe1"

    def test_the_v2_contract_is_marked_unexecuted(self):
        """Declared, not probed.

        Asserting a campaign root does not exist requires stat-ing an
        unapproved `runs/` path, which this revision forbids. The contract's own
        status is the evidence, and the harness independently refuses a
        pre-existing root (covered against tmp_path).
        """
        manifest = _load()
        assert manifest["status"] == "frozen_unapproved_unexecuted"
        assert "approved_content_key" not in manifest
        harness = Path("run_monthly_warm_state_validation.py").read_text()
        assert "artifact root already exists" in harness


class TestSolReviewRound11:
    """Behavioural regressions for repairs that previously had none."""

    # [1] the PARSER must reject impossible stored teleport reasons
    def _stored(self, **over):
        base = {"schema": "monthly_prefix_evidence_v3", "warm_point_s": 24300,
                "completed_trips": {"total_time_loss_s": 5.0, "trip_count": 3},
                "snapshot_facts": {"schema": "warm_snapshot_facts_v1",
                                   "warm_point_s": 24300,
                                   "captured_at_s": 24300.0,
                                   "active_vehicle_count": 0,
                                   "save_state_rng": True,
                                   "save_state_precision": 16},

                "queue_maximum": 0,
                "counters": {"loaded": 3, "inserted": 3, "teleport_total": 1},
                "teleport_reasons": {"jam": 1},
                "recovery_buckets": [{"begin_s": 0, "end_s": 24300,
                                      "time_loss_s": 1.0}]}
        base.update(over)
        return base

    def test_stored_teleport_reasons_may_not_exceed_the_total(self):
        from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence
        payload = self._stored(teleport_reasons={"jam": 5})
        with pytest.raises(WarmStateContractError, match="stored prefix teleport"):
            parse_prefix_evidence(payload)

    def test_a_consistent_stored_payload_still_parses(self):
        from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence
        assert parse_prefix_evidence(self._stored())["counters"]["loaded"] == 3

    # [2] cold observations keep their exact prior structure
    def test_a_cold_observation_has_no_split_diagnostics_key(self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 1.0, "trip_count": 1,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 1,
                   "inserted": 1, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}
        cold = build_monthly_observation(
            schedule_id="s", variant="q50", seed=1001, simulation_mode="meso",
            baseline_metrics=metrics, candidate_metrics=dict(metrics),
            baseline_buckets=[], candidate_buckets=[],
            feasibility={"eligible": True, "hard_failures": []},
            recovery={"status": "recovered", "recovered": True}, failures=[],
            matched_baseline_id="mb", provenance_key="p", provenance={"a": 1})
        assert "split_diagnostics" not in cold, (
            "criterion 9: the cold canonical structure must not change")

    def test_a_cold_observation_with_diagnostics_is_refused(self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        metrics = {"total_time_loss_s": 1.0, "trip_count": 1,
                   "unfinished_trips": 0, "unfinished_waiting_trips": 0,
                   "teleport_total": 0, "teleport_reasons": {}, "loaded": 1,
                   "inserted": 1, "running_at_end": 0, "waiting_at_end": 0,
                   "truncated_unreachable": 0, "dropped_unreachable": 0,
                   "max_queue_vehicles": None, "closed_edge_throughput": None}
        with pytest.raises(WarmStateContractError, match="no split to diagnose"):
            build_monthly_observation(
                schedule_id="s", variant="q50", seed=1001,
                simulation_mode="meso", baseline_metrics=metrics,
                candidate_metrics=dict(metrics), baseline_buckets=[],
                candidate_buckets=[],
                feasibility={"eligible": True, "hard_failures": []},
                recovery={"status": "recovered", "recovered": True},
                failures=[], matched_baseline_id="mb", provenance_key="p",
                provenance={"a": 1}, split_diagnostics={"x": 1})

    # [3] diagnostics must be complete and agree with the split
    def _audit(self, **over):
        base = {"schema": ROUTE_AUDIT_SCHEMA, "original_sha256": "a" * 64,
                "filtered_sha256": "b" * 64, "original_vehicles": 10,
                "filtered_vehicles": 10, "changed_vehicles": 1,
                "dropped_vehicles": 0, "earliest_affected_depart_s": 90000.0}
        base.update(over)
        return base

    def _post_metrics(self, **over):
        base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 0,
                "unfinished_waiting_trips": 0, "teleport_total": 0,
                "teleport_reasons": {}, "loaded": 5, "inserted": 5,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": None, "closed_edge_throughput": None}
        base.update(over)
        return base

    def _diag(self, point=24300, **over):
        """Self-consistent: prefix (3 trips, 5.0 s) + post (2 trips, 5.0 s)
        reconstructs to 5 trips and 10.0 s, with cumulative counters."""
        base = {"route_audit": self._audit(), "selected_warm_point_s": point,
                "prefix_completed_trips": {"total_time_loss_s": 5.0,
                                           "trip_count": 3},
                "prefix_counters": {"loaded": 5, "inserted": 5,
                                    "teleport_total": 0},
                "prefix_teleport_reasons": {}, "prefix_queue_maximum": None,
                **bounded_sections(point, total_time_loss_s=10.0,
                                   prefix_trips=3, resumed_trips=2),
                "raw_post_metrics": self._post_metrics(),
                "reconstructed_metrics": self._post_metrics(
                    total_time_loss_s=10.0, trip_count=5)}
        base.update(over)
        return base

    def test_empty_diagnostics_no_longer_satisfy_the_contract(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        with pytest.raises(WarmStateContractError, match="field set is wrong"):
            validate_split_diagnostics({}, 24300)

    def test_diagnostics_must_agree_with_the_observation_point(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        with pytest.raises(WarmStateContractError, match="describe warm point"):
            validate_split_diagnostics(self._diag(point=900), 24300)

    def test_diagnostics_carry_a_valid_route_audit(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        with pytest.raises(WarmStateContractError, match="route audit"):
            validate_split_diagnostics(
                self._diag(route_audit={"schema": ROUTE_AUDIT_SCHEMA}), 24300)

    @pytest.mark.parametrize("field, match", [
        ("prefix_completed_trips", "must be exactly"),
        ("prefix_counters", "must be exactly"),
        ("raw_post_metrics", "missing production field"),
        ("reconstructed_metrics", "missing production field")])
    def test_empty_required_diagnostic_sections_are_refused(self, field, match):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        with pytest.raises(WarmStateContractError, match=match):
            validate_split_diagnostics(self._diag(**{field: {}}), 24300)

    @pytest.mark.parametrize("field, value, match", [
        ("prefix_completed_trips", {"wrong": 1}, "must be exactly"),
        ("prefix_counters", {"wrong": 1}, "must be exactly"),
        ("raw_post_metrics", {"wrong": 1}, "missing production field"),
        ("reconstructed_metrics", {"wrong": 1}, "missing production field"),
        ("prefix_teleport_reasons", {"jam": -1}, "non-negative"),
        ("prefix_teleport_reasons", {"": 1}, "non-empty string"),
        ("prefix_teleport_reasons", [], "must be an object"),
    ])
    def test_arbitrary_nested_diagnostic_objects_are_refused(self, field, value,
                                                             match):
        """[finding 2] `{"wrong": 1}` used to satisfy every nested section."""
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        with pytest.raises(WarmStateContractError, match=match):
            validate_split_diagnostics(self._diag(**{field: value}), 24300)

    def test_nested_diagnostic_values_are_typed(self):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        bad = dict(self._diag()["prefix_completed_trips"], trip_count="1")
        with pytest.raises(WarmStateContractError, match="non-negative"):
            validate_split_diagnostics(
                self._diag(prefix_completed_trips=bad), 24300)

    def test_an_empty_teleport_reason_map_is_legitimate(self):
        """No teleports is evidence, not a missing field."""
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        assert validate_split_diagnostics(
            self._diag(prefix_teleport_reasons={}), 24300)

    # [4] provisional state identities are checked against the frozen point
    def _harness(self):
        if "warm_harness" in sys.modules:
            return sys.modules["warm_harness"]
        spec = importlib.util.spec_from_file_location(
            "warm_harness", "run_monthly_warm_state_validation.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["warm_harness"] = module
        spec.loader.exec_module(module)
        return module

    def _comparison(self, point=24300):
        return {"equivalent": True, "mismatches": [], "cold_digest": "c",
                "warm_digest": "w", "schedule_id": "s1",
                "demand_variant": "q50", "seed": 1001, "cold_arm": "cold",
                "warm_arm": "warm", "warm_point_s": point,
                "cold_semantic": {"x": 1}, "warm_semantic": {"x": 1}}

    def _state(self, point):
        class _Id:
            warmup_end_s = point
        return {"schedule_id": "s1", "variant": "q50", "seed": 1001,
                "identity": _Id()}

    def test_a_wrong_point_promotable_state_is_incomplete(self):
        report = self._harness().execution_evidence_report(
            [self._comparison()], [self._state(23400)],
            {("s1", "q50", 1001)}, {"q50": 24300}, ["k"])
        assert not report["complete"]
        assert any("promotable state identity was warmed to 23400" in p
                   for p in report["problems"])

    def test_a_matching_point_promotable_state_is_complete(self):
        report = self._harness().execution_evidence_report(
            [self._comparison()], [self._state(24300)],
            {("s1", "q50", 1001)}, {"q50": 24300}, ["k"],
            warm_attempts=_attempts({("s1", "q50", 1001)}))
        assert report["complete"], report["problems"]

    def test_a_state_without_an_identity_point_is_incomplete(self):
        report = self._harness().execution_evidence_report(
            [self._comparison()],
            [{"schedule_id": "s1", "variant": "q50", "seed": 1001}],
            {("s1", "q50", 1001)}, {"q50": 24300}, ["k"])
        assert not report["complete"]

    # [1 of the prior round] the audit really is bound into the identity
    def test_the_route_audit_changes_the_warm_state_identity(self, tmp_path,
                                                             monkeypatch):
        import traffic_sim.simulation.warm_state_cache as wsc
        from traffic_sim.simulation.monthly_warm_state import monthly_warm_identity
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "calibrated.rou.xml").write_text("<routes/>")
        (tmp_path / "net.net.xml").write_text("<net/>")

        def build(audit):
            return monthly_warm_identity(
                demand_build_id="d1", demand_variant="q50", seed=1000,
                simulation_mode="meso", warm_point_s=24300,
                network_path=tmp_path / "net.net.xml",
                unfiltered_route_path=archive / "calibrated.rou.xml",
                source_files={}, sumo_home=tmp_path, archive_dir=archive,
                expected_variant_filename="calibrated.rou.xml",
                route_audit=audit,
                audit_record_path=tmp_path / "audit.json").content_key

        base = build(self._audit())
        assert base != build(self._audit(changed_vehicles=2,
                                         filtered_vehicles=10)), \
            "a different mutation must produce a different cache identity"
        assert base != build(self._audit(filtered_sha256="c" * 64))
        assert base == build(self._audit()), "the same audit must be stable"

    def test_an_invalid_audit_cannot_enter_an_identity(self, tmp_path,
                                                       monkeypatch):
        import traffic_sim.simulation.warm_state_cache as wsc
        from traffic_sim.simulation.monthly_warm_state import monthly_warm_identity
        monkeypatch.setattr(wsc, "sumo_version", lambda home: "Eclipse SUMO 1.27.1")
        monkeypatch.setattr(wsc, "git_commit", lambda: "a" * 40)
        monkeypatch.setattr(wsc.platform, "platform", lambda: "test-platform")
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "calibrated.rou.xml").write_text("<routes/>")
        (tmp_path / "net.net.xml").write_text("<net/>")
        with pytest.raises(WarmStateContractError, match="field set is wrong"):
            monthly_warm_identity(
                demand_build_id="d1", demand_variant="q50", seed=1000,
                simulation_mode="meso", warm_point_s=24300,
                network_path=tmp_path / "net.net.xml",
                unfiltered_route_path=archive / "calibrated.rou.xml",
                source_files={}, sumo_home=tmp_path, archive_dir=archive,
                expected_variant_filename="calibrated.rou.xml",
                route_audit={"schema": ROUTE_AUDIT_SCHEMA},
                audit_record_path=tmp_path / "audit.json")

    # audit consistency and shifted departures
    @pytest.mark.parametrize("over, match", [
        ({"filtered_vehicles": 11}, "may only drop"),
        ({"dropped_vehicles": 5}, "disagrees with the vehicle counts"),
        ({"changed_vehicles": 99, "filtered_vehicles": 10}, "more changed"),
        ({"changed_vehicles": 0, "dropped_vehicles": 0}, "no affected vehicles"),
        ({"earliest_affected_depart_s": None}, "no earliest affected"),
    ])
    def test_inconsistent_audits_are_refused(self, over, match):
        from traffic_sim.simulation.monthly_warm_state import validate_route_audit
        with pytest.raises(WarmStateContractError, match=match):
            validate_route_audit(self._audit(**over))

    def test_a_departure_shifted_later_still_bounds_the_split(self, tmp_path):
        """The vehicle departed at its ORIGINAL time in the prefix."""
        original = _route(tmp_path, "o.rou.xml", [("v1", 5000.0, "a_b_0")])
        filtered = _route(tmp_path, "f.rou.xml", [("v1", 30000.0, "a_b_0")])
        audit = audit_route_mutation(original, filtered)
        assert audit["changed_vehicles"] == 1
        assert audit["earliest_affected_depart_s"] == 5000.0, (
            "taking only the new time would place the split after a vehicle "
            "that had already departed")
        assert route_safe_warm_point(audit, closure_begin_s=25200) == 4500

    def test_a_duplicate_filtered_vehicle_is_refused(self, tmp_path):
        original = _route(tmp_path, "o.rou.xml",
                          [("v1", 100.0, "a_b_0"), ("v2", 200.0, "b_c_0")])
        filtered = _route(tmp_path, "f.rou.xml",
                          [("v1", 100.0, "a_b_0"), ("v1", 100.0, "a_b_0")])
        with pytest.raises(WarmStateContractError, match="duplicate vehicle id"):
            audit_route_mutation(original, filtered)


class TestTheBoundaryGuardItself:
    """[findings 3-5] The guard must actually be able to catch a violation.

    Every probe below stays under `tmp_path` or names a path WITHOUT touching
    it, so proving the guard works never itself crosses the boundary.
    """

    def test_a_path_read_on_an_unapproved_runs_path_is_caught(self, tmp_path):
        """`Path.read_text` goes through `io.open`, not `builtins.open`."""
        sandbox = tmp_path / "runs" / "closure-search-cache" / "warm-state"
        sandbox.mkdir(parents=True)
        target = sandbox / "state.json"
        # created before the guard would see it? No: the guard is autouse and
        # already active, so even this write must be caught.
        with pytest.raises(AssertionError, match="unapproved runs/ path"):
            target.write_text("{}")

    def test_path_read_text_is_guarded(self, tmp_path):
        target = tmp_path / "runs" / "monthly-warm-state-validation" / "x.json"
        with pytest.raises(AssertionError, match="unapproved runs/ path"):
            target.read_text()

    def test_path_read_bytes_is_guarded(self, tmp_path):
        target = tmp_path / "runs" / "some-other-campaign" / "outcomes.json"
        with pytest.raises(AssertionError, match="unapproved runs/ path"):
            target.read_bytes()

    def test_a_substring_lookalike_path_is_rejected(self, tmp_path):
        """A different `runs/` path CONTAINING the approved archive name.

        The earlier substring guard would have accepted this.
        """
        lookalike = (tmp_path / "runs"
                     / f"{APPROVED_ARCHIVE}-copy" / "calibrated.rou.xml")
        with pytest.raises(AssertionError, match="unapproved runs/ path"):
            lookalike.exists()

    def test_an_unapproved_file_inside_the_approved_archive_is_rejected(
            self, tmp_path):
        target = tmp_path / "runs" / APPROVED_ARCHIVE / "secret.json"
        with pytest.raises(AssertionError, match="unapproved archive path"):
            target.exists()

    def test_a_nested_path_under_the_approved_archive_is_rejected(self, tmp_path):
        target = tmp_path / "runs" / APPROVED_ARCHIVE / "sub" / "calibrated.rou.xml"
        with pytest.raises(AssertionError, match="unapproved archive path"):
            target.exists()

    def test_the_approved_five_files_are_permitted(self, tmp_path):
        for name in sorted(APPROVED_ARCHIVE_FILES):
            target = tmp_path / "runs" / APPROVED_ARCHIVE / name
            assert target.exists() is False        # permitted, simply absent

    def test_the_approved_archive_directory_itself_is_permitted(self, tmp_path):
        assert (tmp_path / "runs" / APPROVED_ARCHIVE).exists() is False

    def test_paths_outside_runs_are_untouched_by_the_guard(self, tmp_path):
        target = tmp_path / "scratch" / "notes.txt"
        target.parent.mkdir(parents=True)
        target.write_text("fine")
        assert target.read_text() == "fine"


class TestDiagnosticsMustBeSelfConsistent:
    """[findings 2-5] Diagnostics reuse the production validators.

    Naming the nested fields exactly while typing only one value left strings,
    negatives and impossible counter relationships passing everywhere else, and
    a fully valid but DIFFERENT `reconstructed_metrics` was never checked
    against the inputs it claims to summarise.
    """

    def _audit(self):
        return {"schema": ROUTE_AUDIT_SCHEMA, "original_sha256": "a" * 64,
                "filtered_sha256": "b" * 64, "original_vehicles": 10,
                "filtered_vehicles": 10, "changed_vehicles": 1,
                "dropped_vehicles": 0, "earliest_affected_depart_s": 90000.0}

    def _post(self, **over):
        base = {"total_time_loss_s": 5.0, "trip_count": 2, "unfinished_trips": 1,
                "unfinished_waiting_trips": 1, "teleport_total": 1,
                "teleport_reasons": {"jam": 1}, "loaded": 5, "inserted": 5,
                "running_at_end": 0, "waiting_at_end": 0,
                "truncated_unreachable": 0, "dropped_unreachable": 0,
                "max_queue_vehicles": 5, "closed_edge_throughput": 0}
        base.update(over)
        return base

    def _diag(self, **over):
        base = {"route_audit": self._audit(), "selected_warm_point_s": 900,
                "prefix_completed_trips": {"total_time_loss_s": 5.0,
                                           "trip_count": 3},
                "prefix_counters": {"loaded": 3, "inserted": 3,
                                    "teleport_total": 1},
                "prefix_teleport_reasons": {"jam": 1},
                "prefix_queue_maximum": 0,
                **bounded_sections(900, total_time_loss_s=10.0,
                                   prefix_trips=3, resumed_trips=2),
                "raw_post_metrics": self._post(),
                "reconstructed_metrics": self._post(total_time_loss_s=10.0,
                                                    trip_count=5)}
        base.update(over)
        return base

    def _validate(self, **over):
        from traffic_sim.simulation.monthly_warm_state import (
            validate_split_diagnostics)
        return validate_split_diagnostics(self._diag(**over), 900)

    def test_a_self_consistent_set_is_accepted(self):
        assert self._validate()

    # [2] every production value is typed, not just total_time_loss_s
    @pytest.mark.parametrize("over, match", [
        ({"running_at_end": "4"}, "non-negative"),
        ({"unfinished_trips": True}, "non-negative"),
        ({"loaded": -1}, "non-negative"),
        ({"max_queue_vehicles": "5"}, "non-negative"),
        ({"teleport_reasons": {"jam": -1}}, "non-negative"),
    ])
    def test_untyped_post_values_are_refused(self, over, match):
        with pytest.raises(WarmStateContractError, match=match):
            self._validate(raw_post_metrics=self._post(**over))

    # [2] impossible relationships inside the post section
    @pytest.mark.parametrize("over, match", [
        ({"inserted": 99}, "inserted 99 exceeds loaded 5"),
        ({"unfinished_trips": 9}, "unfinished_trips 9 exceeds trip_count 2"),
        ({"teleport_total": 0}, "exceeding teleport_total 0"),
    ])
    def test_impossible_post_relationships_are_refused(self, over, match):
        with pytest.raises(WarmStateContractError, match=match):
            self._validate(raw_post_metrics=self._post(**over))

    # [3] prefix relationships
    def test_prefix_inserted_may_not_exceed_loaded(self):
        with pytest.raises(WarmStateContractError, match="inserted 9 exceeds loaded 1"):
            self._validate(prefix_counters={"loaded": 1, "inserted": 9,
                                            "teleport_total": 1})

    def test_prefix_trips_may_not_exceed_inserted(self):
        with pytest.raises(WarmStateContractError, match="completed trip_count"):
            self._validate(prefix_completed_trips={"total_time_loss_s": 5.0,
                                                   "trip_count": 99})

    def test_prefix_teleport_reasons_may_not_exceed_the_total(self):
        with pytest.raises(WarmStateContractError, match="exceeding teleport_total"):
            self._validate(prefix_teleport_reasons={"jam": 9})

    # [4] a valid but DIFFERENT reconstruction is caught
    def test_a_valid_but_different_reconstruction_is_refused(self):
        with pytest.raises(WarmStateContractError, match="does not follow from"):
            self._validate(reconstructed_metrics=self._post(
                total_time_loss_s=11.0, trip_count=5))

    def test_a_reconstruction_that_sums_cumulative_counters_is_refused(self):
        """The LUNA-WARM-05 error, caught inside the diagnostics themselves."""
        with pytest.raises(WarmStateContractError, match="does not follow from"):
            self._validate(reconstructed_metrics=self._post(
                total_time_loss_s=10.0, trip_count=5, loaded=8, inserted=8))

    def test_the_error_names_the_differing_fields(self):
        with pytest.raises(WarmStateContractError, match=r"trip_count"):
            self._validate(reconstructed_metrics=self._post(
                total_time_loss_s=10.0, trip_count=99))

    # [4] diagnostics must match the observation they accompany
    def test_diagnostics_must_agree_with_the_observation_metrics(self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        baseline = self._post(total_time_loss_s=1.0)
        with pytest.raises(WarmStateContractError,
                           match="the observation does not report"):
            build_monthly_observation(
                schedule_id="s", variant="q50", seed=1001,
                simulation_mode="meso", baseline_metrics=baseline,
                # the observation reports something else entirely
                candidate_metrics=self._post(total_time_loss_s=999.0),
                baseline_buckets=[], candidate_buckets=[],
                feasibility={"eligible": True, "hard_failures": []},
                recovery={"status": "recovered", "recovered": True},
                failures=[], matched_baseline_id="mb", provenance_key="p",
                provenance={"a": 1}, execution_arm="warm", warm_point_s=900,
                split_diagnostics=self._diag())

    def test_agreeing_diagnostics_are_accepted_on_an_observation(self):
        from traffic_sim.simulation.monthly_warm_state import (
            build_monthly_observation)
        candidate = self._post(total_time_loss_s=10.0, trip_count=5)
        observation = build_monthly_observation(
            schedule_id="s", variant="q50", seed=1001, simulation_mode="meso",
            baseline_metrics=self._post(), candidate_metrics=candidate,
            baseline_buckets=[], candidate_buckets=[],
            feasibility={"eligible": True, "hard_failures": []},
            recovery={"status": "recovered", "recovered": True}, failures=[],
            matched_baseline_id="mb", provenance_key="p", provenance={"a": 1},
            execution_arm="warm", warm_point_s=900,
            split_diagnostics=self._diag())
        assert observation["split_diagnostics"]["selected_warm_point_s"] == 900

    # [5] one registry, not two
    def test_the_validator_reuses_the_production_helpers(self):
        source = Path("traffic_sim/simulation/monthly_warm_state.py").read_text()
        body = source.split("def validate_split_diagnostics(")[1].split("\ndef ")[0]
        # The helpers moved when boundary accounting landed: split diagnostics
        # carry bounded ledger FACTS, not the per-vehicle maps, so they share
        # the extracted section validator and combiner rather than the full
        # evidence builder. The invariant is unchanged — no second, partial
        # copy of the rules may exist here.
        assert "validate_prefix_sections(" in body
        assert "validate_post_warm_metrics(" in body
        assert "_reconstruct_from_sections(" in body
        assert "validate_aggregation_summary(" in body
        assert "validate_snapshot_evidence(" in body
