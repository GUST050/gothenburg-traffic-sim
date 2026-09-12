"""Stage 3 of Item 1: the isolated day-reuse policy experiment.

The question the experiment answers is narrow and worth stating exactly.
A calibrated day's identity currently carries ``pool_composition`` — the set
of day-type geometry pools the window it was calibrated inside draws from —
so the SAME calendar day calibrated inside a weekday-only window and inside a
mixed weekday/weekend window are two different entries. That is why 19 of the
frozen June repetitions are repetitions at all.

Two cheaper policies are on the table: always use the canonical union of
``POOL_KEYS``, or always use only the day's own pool key. Either would collapse
those repetitions. Either is a **safe performance fix only if the day it
produces is the same day**. If the output differs it is a model change, and it
must be rejected for Item 1 rather than described as a speed-up.

These tests pin the machinery that decides that, and in particular the four
ways the decision could be got wrong quietly:

* comparing gzip container bytes instead of the day (the stored ``.gz``
  carries an mtime, so two identical days never hash equal);
* letting wall time or peak RSS leak into an equivalence verdict;
* comparing two days that were produced by different source bytes and calling
  the difference a composition effect;
* letting a policy that reproduces only its OWN control count as a pass.
"""
import gzip
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demand import day_library as dl
from demand.intake import POOL_KEYS
from traffic_sim.demand.source_identity import demand_source_paths

from tools import experiment_day_reuse_policy as exp

ROOT = Path(__file__).resolve().parent.parent

WEEKDAY_DATE = "2027-06-03"      # Thursday
WEEKEND_DATE = "2027-06-25"      # Midsummer Day 2027 (Friday, holiday block)

PURE_WEEKDAY = ("weekday",)
MIXED = ("weekday", "weekend")


# ── fixtures ────────────────────────────────────────────────────────────────

def _route_xml(vehicles):
    lines = ["<routes>\n"]
    for vid, depart, edges in vehicles:
        lines.append(f'  <vehicle id="{vid}" depart="{depart:.1f}">'
                     f'<route edges="{edges}"/></vehicle>\n')
    lines.append("</routes>\n")
    return "".join(lines)


def _agents(vehicles):
    return {"schema_version": 1, "agents": [
        {"vehicle_id": vid, "candidate_id": f"d0_{i}", "purpose": "arbete",
         "purpose_route_compatible": True, "tour_id": f"t{i}", "leg": "outbound",
         "origin_edge": edges.split()[0], "destination_edge": edges.split()[-1],
         "departure_s": depart}
        for i, (vid, depart, edges) in enumerate(vehicles)]}


def _fit(vehicles, *, geh_pct=100.0, constraints=4, exact=4, max_abs=0.0,
         unserviceable=(), guards=None):
    return {
        "vehicles": len(vehicles),
        "infeasible_intervals": 0,
        "geh_ok": 96, "geh_total": 96, "geh_pct": geh_pct,
        "integer_sensor_constraints": constraints,
        "integer_sensor_exact": exact,
        "integer_sensor_max_abs_error": max_abs,
        "integer_sensor_sum_abs_error": max_abs,
        "integer_sensor_target_rule": "int(round(target))",
        "unserviceable_edges": list(unserviceable),
        "bound_violations": [],
        "relaxed_bound_violations": [],
        "purpose_allocation": [],
        "purpose_allocation_summary": guards if guards is not None else {
            "quarters_with_incompatible_routes": 0,
            "mix_reallocation_vehicles": 0,
            "protected_signature_coverage": {"signatures": 12,
                                             "missing_by_purpose": {}},
        },
        "relaxation_summary": {"clean": 96},
        "passage_calibration": {
            "policy": "automatic_dynamic_passage_v3",
            "status": "validated",
            "measurement_basis": "sensor_edge_entry_quarter",
            "prediction_exact": True,
            "retained_constraints": constraints,
            "new_relaxations": 0,
            "departure_population": [len(vehicles)],
            "validation": {"candidate_absolute_error": 0},
        },
    }


def _write_entry(tmp_path, library_root, date, composition, *,
                 vehicles=None, fit_overrides=None, agents_override=None,
                 source_hashes=None, provenance_override=None,
                 candidate_pool="a" * 64, scratch_name="scratch"):
    """Store one realistic day entry through the real DayLibrary writer."""
    vehicles = vehicles or [("pfe0", 450.0, "A B C"), ("pfe1", 1350.0, "B C D")]
    scratch = tmp_path / scratch_name
    scratch.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for suffix in exp.VARIANT_SUFFIXES:
        route = scratch / f"calibrated{suffix}.rou.xml"
        route.write_text(_route_xml(vehicles))
        artifacts[route.name] = route
        agents = scratch / f"calibrated{suffix}.agents.json"
        payload = (agents_override if agents_override is not None
                   else _agents(vehicles))
        agents.write_text(json.dumps(payload, separators=(",", ":")))
        artifacts[agents.name] = agents
        fit = scratch / f"fit{suffix}.json"
        report = _fit(vehicles)
        report.update((fit_overrides or {}))
        fit.write_text(json.dumps(report, separators=(",", ":")))
        artifacts[fit.name] = fit
    provenance = scratch / "provenance.json"
    provenance.write_text(json.dumps(provenance_override or {
        "schema_version": 1, "status": "pass", "mode": "single_pool",
        "candidate_records": 6000, "vehicles": len(vehicles) * 3,
        "variants": [{"route": f"calibrated{s}.rou.xml",
                      "agents": f"calibrated{s}.agents.json",
                      "vehicles": len(vehicles)}
                     for s in exp.VARIANT_SUFFIXES],
    }, separators=(",", ":")))
    artifacts[provenance.name] = provenance

    identity = dl.DayIdentity(
        date=date, source="forecast", pool_composition=composition,
        inputs={"candidate_pool": candidate_pool,
                "candidate_metadata": "b" * 64,
                "variants": ["edge_shares", "edge_shares_q10",
                             "edge_shares_q90"]},
        source_hashes=source_hashes or {"pfe": "c" * 64,
                                        "build_sumo_demand": "d" * 64})
    library = dl.DayLibrary(library_root)
    library.put(identity, artifacts, fit={"geh_pct": 100.0,
                                          "vehicles": len(vehicles)})
    return library.path_for(identity)


# ── Stage 3 step 1: can the experiment live in tools/ and tests/ at all ─────

class TestFingerprintNeutrality:
    """The experiment must not invalidate the warm day library it measures."""

    def test_the_experiment_files_are_outside_the_demand_source_inventory(self):
        inventory = {p.resolve() for p in demand_source_paths(ROOT).values()}
        for path in exp.experiment_source_files():
            assert path.resolve() not in inventory, path

    def test_neutrality_is_reported_as_an_explicit_named_check(self):
        report = exp.fingerprint_neutrality(ROOT)
        assert report["fingerprint_neutral"] is True
        assert report["inventory_size"] == len(demand_source_paths(ROOT))
        assert set(report["experiment_files"]) == {
            str(p.relative_to(ROOT)) for p in exp.experiment_source_files()}

    def test_a_demand_source_file_is_refused_as_an_experiment_file(self,
                                                                   monkeypatch):
        monkeypatch.setattr(exp, "experiment_source_files",
                            lambda: (ROOT / "build_sumo_demand.py",))
        report = exp.fingerprint_neutrality(ROOT)
        assert report["fingerprint_neutral"] is False
        assert "build_sumo_demand.py" in report["inside_inventory"]

    def test_the_sumo_output_directory_is_hardcoded_in_inventory_files(self):
        """Why an isolated working copy is required, not a flag.

        ``SUMO_DIR`` is a module constant in files the day identity hashes, so
        redirecting it by editing source would invalidate every warm day.
        """
        sites = exp.sumo_dir_binding_sites(ROOT)
        assert "build_sumo_demand.py" in sites
        assert "build_candidates.py" in sites
        assert "demand/intake.py" in sites
        inventory = {str(p.relative_to(ROOT))
                     for p in demand_source_paths(ROOT).values()}
        assert set(sites) <= inventory


class TestOutputIsolation:
    def test_output_inside_the_day_library_is_refused(self, tmp_path):
        with pytest.raises(exp.ExperimentRefused, match="day library"):
            exp.check_output_path(tmp_path / "lib" / "out.json",
                                  day_library_root=tmp_path / "lib",
                                  sumo_dir=tmp_path / "sumo")

    def test_output_inside_sumo_is_refused(self, tmp_path):
        with pytest.raises(exp.ExperimentRefused, match="sumo"):
            exp.check_output_path(tmp_path / "sumo" / "out.json",
                                  day_library_root=tmp_path / "lib",
                                  sumo_dir=tmp_path / "sumo")

    def test_an_existing_artifact_is_never_silently_overwritten(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text("{}")
        with pytest.raises(exp.ExperimentRefused, match="exists"):
            exp.check_output_path(target, day_library_root=tmp_path / "lib",
                                  sumo_dir=tmp_path / "sumo")

    def test_overwriting_requires_an_explicit_flag(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text("{}")
        exp.check_output_path(target, day_library_root=tmp_path / "lib",
                              sumo_dir=tmp_path / "sumo", allow_overwrite=True)


# ── the three policies ──────────────────────────────────────────────────────

class TestPolicyComposition:
    def test_canonical_union_is_the_full_ordered_pool_key_set(self):
        assert exp.policy_composition("canonical_union", WEEKDAY_DATE) == \
            tuple(sorted(POOL_KEYS))
        assert exp.policy_composition("canonical_union", WEEKEND_DATE) == \
            tuple(sorted(POOL_KEYS))

    def test_day_type_local_is_only_the_dates_own_pool(self):
        assert exp.policy_composition("day_type_local", WEEKDAY_DATE) == \
            ("weekday",)
        assert len(exp.policy_composition("day_type_local", WEEKEND_DATE)) == 1

    def test_the_two_control_dates_are_different_day_types(self):
        assert exp.policy_composition("day_type_local", WEEKDAY_DATE) != \
            exp.policy_composition("day_type_local", WEEKEND_DATE)

    def test_context_control_is_not_derivable_from_the_date_alone(self):
        """It is a property of the window a control entry came from."""
        with pytest.raises(exp.ExperimentRefused, match="control entry"):
            exp.policy_composition("context_control", WEEKDAY_DATE)

    def test_the_candidate_policies_are_exactly_the_two_under_test(self):
        assert exp.CANDIDATE_POLICIES == ("canonical_union", "day_type_local")


class TestControlDiscovery:
    def test_both_the_pure_and_the_mixed_control_are_found(self, tmp_path):
        lib = tmp_path / "lib"
        pure = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                            scratch_name="s1")
        mixed = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED,
                             scratch_name="s2")
        found = exp.discover_controls(lib, WEEKDAY_DATE)
        assert found["pure"] == pure
        assert found["mixed"] == mixed

    def test_a_missing_mixed_control_is_a_named_refusal(self, tmp_path):
        lib = tmp_path / "lib"
        _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        with pytest.raises(exp.ExperimentRefused, match="mixed"):
            exp.discover_controls(lib, WEEKDAY_DATE)

    def test_a_structurally_invalid_manifest_is_refused_by_name(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                             scratch_name="s1")
        _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED, scratch_name="s2")
        manifest = json.loads((entry / "manifest.json").read_text())
        del manifest["artifacts"]
        (entry / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(exp.ExperimentRefused) as caught:
            exp.discover_controls(lib, WEEKDAY_DATE)
        assert "artifacts" in str(caught.value)


# ── semantic comparison (item 8) ────────────────────────────────────────────

class TestSemanticComparison:
    def _pair(self, tmp_path, **second):
        lib = tmp_path / "lib"
        a = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                         scratch_name="s1")
        b = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED,
                         scratch_name="s2", **second)
        return exp.read_day_record(a), exp.read_day_record(b)

    def test_gzip_container_bytes_differ_while_the_day_is_identical(self,
                                                                    tmp_path):
        """The trap the plan names: .gz bytes are not the day.

        gzip stores an mtime, so the same route file stored twice never has
        the same container hash. A comparison that stopped at the manifest
        would report a difference that does not exist.
        """
        lib = tmp_path / "lib"
        a = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                         scratch_name="s1")
        time.sleep(1.05)
        b = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED, scratch_name="s2")

        manifest_a = json.loads((a / "manifest.json").read_text())["artifacts"]
        manifest_b = json.loads((b / "manifest.json").read_text())["artifacts"]
        assert manifest_a["calibrated.rou.xml.gz"]["sha256"] != \
            manifest_b["calibrated.rou.xml.gz"]["sha256"], \
            "fixture no longer exercises the gzip mtime trap"
        assert gzip.open(a / "calibrated.rou.xml.gz", "rb").read() == \
            gzip.open(b / "calibrated.rou.xml.gz", "rb").read()

        verdict = exp.compare_records(exp.read_day_record(a),
                                      exp.read_day_record(b))
        assert verdict["equivalent"] is True, verdict["mismatched_dimensions"]

    def test_a_changed_departure_time_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(
            tmp_path, vehicles=[("pfe0", 451.0, "A B C"),
                                ("pfe1", 1350.0, "B C D")])
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "route_departures" in verdict["mismatched_dimensions"]

    def test_a_changed_route_edge_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(
            tmp_path, vehicles=[("pfe0", 450.0, "A B X"),
                                ("pfe1", 1350.0, "B C D")])
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "route_edges" in verdict["mismatched_dimensions"]

    def test_reordered_agent_keys_are_still_the_same_agents(self, tmp_path):
        """Canonical JSON, not raw bytes (item 8)."""
        vehicles = [("pfe0", 450.0, "A B C"), ("pfe1", 1350.0, "B C D")]
        reordered = {"agents": [
            dict(reversed(list(agent.items())))
            for agent in _agents(vehicles)["agents"]], "schema_version": 1}
        lib = tmp_path / "lib"
        a = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                         vehicles=vehicles, scratch_name="s1")
        b = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED, vehicles=vehicles,
                         agents_override=reordered, scratch_name="s2")
        raw_a = gzip.open(a / "calibrated.agents.json.gz", "rb").read()
        raw_b = gzip.open(b / "calibrated.agents.json.gz", "rb").read()
        assert raw_a != raw_b, "fixture no longer exercises key ordering"
        verdict = exp.compare_records(exp.read_day_record(a),
                                      exp.read_day_record(b))
        assert verdict["equivalent"] is True, verdict["mismatched_dimensions"]

    def test_a_changed_agent_purpose_is_a_mismatch(self, tmp_path):
        vehicles = [("pfe0", 450.0, "A B C"), ("pfe1", 1350.0, "B C D")]
        altered = _agents(vehicles)
        altered["agents"][0]["purpose"] = "fritid"
        control, candidate = self._pair(tmp_path, vehicles=vehicles,
                                        agents_override=altered)
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "agent_records" in verdict["mismatched_dimensions"]

    def test_a_changed_integer_sensor_target_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(
            tmp_path, fit_overrides={"integer_sensor_exact": 3,
                                     "integer_sensor_max_abs_error": 1.0})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "integer_sensor_targets" in verdict["mismatched_dimensions"]

    def test_a_changed_population_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(
            tmp_path, vehicles=[("pfe0", 450.0, "A B C")])
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "population" in verdict["mismatched_dimensions"]

    def test_a_lost_publication_health_gate_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(
            tmp_path, fit_overrides={"unserviceable_edges": ["A"]})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "publication_health" in verdict["mismatched_dimensions"]

    def test_a_changed_structure_guard_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(tmp_path, fit_overrides={
            "purpose_allocation_summary": {
                "quarters_with_incompatible_routes": 2,
                "mix_reallocation_vehicles": 7,
                "protected_signature_coverage": {
                    "signatures": 11, "missing_by_purpose": {"arbete": 1}}}})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "structure_guards" in verdict["mismatched_dimensions"]

    def test_a_changed_route_provenance_record_is_a_mismatch(self, tmp_path):
        control, candidate = self._pair(tmp_path, provenance_override={
            "schema_version": 1, "status": "fail", "mode": "single_pool",
            "candidate_records": 6000, "vehicles": 6, "variants": []})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "provenance_health" in verdict["mismatched_dimensions"]

    def test_the_pool_composition_itself_is_reported_not_compared(self,
                                                                  tmp_path):
        """The arms differ by composition by construction.

        Comparing the identity's composition would make every comparison fail
        for the one reason the experiment is holding constant.
        """
        control, candidate = self._pair(tmp_path)
        assert control["identity"]["pool_composition"] != \
            candidate["identity"]["pool_composition"]
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is True
        assert verdict["reported"]["pool_composition"] == {
            "control": list(PURE_WEEKDAY), "candidate": list(MIXED)}

    def test_differing_source_hashes_make_the_result_inconclusive(self,
                                                                  tmp_path):
        """Different code is a confound, not a composition effect."""
        control, candidate = self._pair(
            tmp_path, source_hashes={"pfe": "e" * 64,
                                     "build_sumo_demand": "d" * 64})
        verdict = exp.compare_records(control, candidate)
        assert verdict["status"] == "inconclusive"
        assert verdict["equivalent"] is None
        assert "source_hashes" in verdict["confounds"]

    def test_an_input_hash_difference_is_reported_with_its_explanation(self,
                                                                       tmp_path):
        control, candidate = self._pair(tmp_path, candidate_pool="f" * 64)
        verdict = exp.compare_records(control, candidate)
        assert verdict["reported"]["candidate_pool_hash"]["control"] != \
            verdict["reported"]["candidate_pool_hash"]["candidate"]


class TestPerformanceNeverDecidesEquivalence:
    def test_timings_and_peak_rss_do_not_affect_the_verdict(self, tmp_path):
        lib = tmp_path / "lib"
        a = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                         scratch_name="s1")
        b = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED,
                         scratch_name="s2")
        control = exp.read_day_record(a)
        candidate = exp.read_day_record(
            b, performance={"total_calibration_s": 311.4,
                            "dynamic_passage_s": 12.5,
                            "peak_rss_bytes": 4 * 1024 ** 3})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is True
        assert set(verdict["mismatched_dimensions"]) == set()

    def test_the_performance_fields_are_still_reported(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        record = exp.read_day_record(entry)
        assert set(record["performance"]) == set(exp.PERFORMANCE_FIELDS)
        assert all(record["performance"][f] is None
                   for f in exp.PERFORMANCE_FIELDS)

    def test_no_performance_field_is_an_equivalence_dimension(self):
        assert set(exp.PERFORMANCE_FIELDS) & set(exp.EQUIVALENCE_DIMENSIONS) \
            == set()


class TestDimensionsThatCannotBeEvaluated:
    def test_required_passage_evidence_absent_on_both_is_inconclusive(self,
                                                                      tmp_path):
        lib = tmp_path / "lib"
        a = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                         scratch_name="s1",
                         fit_overrides={"passage_calibration": None})
        b = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED,
                         scratch_name="s2",
                         fit_overrides={"passage_calibration": None})
        verdict = exp.compare_records(exp.read_day_record(a),
                                      exp.read_day_record(b))
        assert "dynamic_passage_fit" in verdict["unevaluated_dimensions"]
        assert verdict["status"] == "inconclusive"
        assert verdict["equivalent"] is None

    def test_a_dimension_present_on_only_one_side_is_a_mismatch(self,
                                                                tmp_path):
        control, candidate = TestSemanticComparison()._pair(
            tmp_path, fit_overrides={"passage_calibration": {"fit": 0.97}})
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "dynamic_passage_fit" in verdict["mismatched_dimensions"]

    def test_real_passage_calibration_key_is_read(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(
            tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
            fit_overrides={"passage_calibration": {
                "policy": "automatic_dynamic_passage_v3",
                "status": "validated",
                "validation": {"candidate_absolute_error": 0},
            }})
        record = exp.read_day_record(entry)
        assert record["variants"][""]["dynamic_passage_fit"]["status"] \
            == "validated"


class TestExactArtifactComparison:
    def test_route_bytes_are_an_equivalence_dimension(self, tmp_path):
        control, candidate = TestSemanticComparison()._pair(tmp_path)
        candidate["variants"][""]["route_uncompressed_sha256"] = "0" * 64
        verdict = exp.compare_records(control, candidate)
        assert verdict["equivalent"] is False
        assert "route_artifacts" in verdict["mismatched_dimensions"]

    def test_manifest_digest_mismatch_is_refused(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        (entry / "fit.json").write_text("{}")
        with pytest.raises(exp.ExperimentRefused, match="(size|digest) mismatch"):
            exp.read_day_record(entry)

    def test_non_policy_input_drift_is_a_confound(self, tmp_path):
        control, candidate = TestSemanticComparison()._pair(tmp_path)
        candidate["identity_inputs"] = dict(candidate["identity_inputs"])
        candidate["identity_inputs"]["constraints"] = "changed"
        verdict = exp.compare_records(control, candidate)
        assert verdict["status"] == "inconclusive"
        assert "identity_inputs.constraints" in verdict["confounds"]

    def test_candidate_count_is_reported_but_not_a_provenance_regression(
            self, tmp_path):
        control, candidate = TestSemanticComparison()._pair(tmp_path)
        candidate["route_provenance"] = dict(candidate["route_provenance"])
        candidate["route_provenance"]["candidate_records"] += 1
        candidate["candidate_count"] += 1
        candidate["pfe_shape_variables"] += 1
        verdict = exp.compare_records(control, candidate)
        assert "provenance_health" not in verdict["mismatched_dimensions"]
        assert verdict["reported"]["candidate_count"]["control"] != \
            verdict["reported"]["candidate_count"]["candidate"]


# ── budget and scope (item 5) ───────────────────────────────────────────────

class TestBudget:
    def test_the_budget_is_frozen_in_source(self):
        assert exp.MAX_COLD_CALIBRATIONS == 8
        assert exp.MAX_CALIBRATION_SECONDS == 30 * 60

    def test_a_ninth_cold_calibration_is_refused(self):
        budget = exp.Budget()
        for _ in range(exp.MAX_COLD_CALIBRATIONS):
            budget.check()
            budget.record(1.0)
        with pytest.raises(exp.BudgetExceeded, match="calibrations"):
            budget.check()

    def test_exceeding_the_total_calibration_time_is_refused(self):
        budget = exp.Budget()
        budget.check()
        budget.record(exp.MAX_CALIBRATION_SECONDS + 1)
        with pytest.raises(exp.BudgetExceeded, match="seconds"):
            budget.check()

    def test_the_planned_run_fits_the_budget(self):
        """Two dates x two candidate compositions = four cold days."""
        assert exp.planned_cold_calibrations() == 4
        assert exp.planned_cold_calibrations() <= exp.MAX_COLD_CALIBRATIONS

    def test_the_experiment_never_reaches_for_a_monthly_search(self):
        source = (ROOT / "tools" / "experiment_day_reuse_policy.py").read_text()
        for forbidden in ("run_monthly_closure_search",
                          "screen_monthly_closures",
                          "run_monthly_warm_state_validation"):
            assert forbidden not in source


class TestStopAtFirstRegression:
    def _verdict(self, equivalent, dimensions=()):
        return {"status": "measured", "equivalent": equivalent,
                "mismatched_dimensions": list(dimensions),
                "unevaluated_dimensions": [], "confounds": [], "reported": {}}

    def test_the_run_stops_at_the_first_regression(self):
        seen = []

        def compare(control, candidate):
            seen.append(candidate)
            return self._verdict(candidate != "bad", ()
                                 if candidate != "bad" else ("population",))

        outcome = exp.run_comparisons(
            [("2027-06-03", "pure", "ok"), ("2027-06-03", "mixed", "bad"),
             ("2027-06-25", "pure", "ok"), ("2027-06-25", "mixed", "ok")],
            compare_fn=lambda control, candidate: compare(control, candidate),
            control_fn=lambda key: key)
        assert outcome["stopped_early"] is True
        assert outcome["stopped_on"]["mismatched_dimensions"] == ["population"]
        assert len(seen) == 2, "comparisons continued past the first regression"

    def test_a_clean_run_visits_every_comparison(self):
        outcome = exp.run_comparisons(
            [("2027-06-03", "pure", "ok"), ("2027-06-03", "mixed", "ok")],
            compare_fn=lambda control, candidate: self._verdict(True),
            control_fn=lambda key: key)
        assert outcome["stopped_early"] is False
        assert len(outcome["comparisons"]) == 2


# ── the decision rule ───────────────────────────────────────────────────────

class TestDecisionRule:
    def _c(self, date, control, equivalent, unevaluated=()):
        return {"date": date, "control": control, "equivalent": equivalent,
                "status": "measured" if equivalent is not None
                          else "inconclusive",
                "mismatched_dimensions": [] if equivalent else ["population"],
                "unevaluated_dimensions": list(unevaluated)}

    def test_a_policy_passes_only_by_matching_both_controls_on_both_dates(self):
        verdict = exp.policy_verdict([
            self._c(WEEKDAY_DATE, "pure", True),
            self._c(WEEKDAY_DATE, "mixed", True),
            self._c(WEEKEND_DATE, "pure", True),
            self._c(WEEKEND_DATE, "mixed", True)])
        assert verdict["decision"] == "candidate_for_next_check"
        assert verdict["activate_in_production"] is False

    def test_matching_only_its_own_composition_is_a_rejection(self):
        verdict = exp.policy_verdict([
            self._c(WEEKDAY_DATE, "pure", True),
            self._c(WEEKDAY_DATE, "mixed", False),
            self._c(WEEKEND_DATE, "pure", True),
            self._c(WEEKEND_DATE, "mixed", False)])
        assert verdict["decision"] == "rejected_as_model_change"
        assert "not a safe performance fix" in verdict["statement"]

    def test_one_missing_date_cannot_pass(self):
        verdict = exp.policy_verdict([
            self._c(WEEKDAY_DATE, "pure", True),
            self._c(WEEKDAY_DATE, "mixed", True)])
        assert verdict["decision"] == "inconclusive"

    def test_an_inconclusive_comparison_blocks_a_pass(self):
        verdict = exp.policy_verdict([
            self._c(WEEKDAY_DATE, "pure", True),
            self._c(WEEKDAY_DATE, "mixed", None),
            self._c(WEEKEND_DATE, "pure", True),
            self._c(WEEKEND_DATE, "mixed", True)])
        assert verdict["decision"] == "inconclusive"

    def test_a_passing_policy_is_still_not_activated(self):
        verdict = exp.policy_verdict([
            self._c(d, c, True) for d in (WEEKDAY_DATE, WEEKEND_DATE)
            for c in ("pure", "mixed")])
        assert verdict["activate_in_production"] is False
        assert verdict["next_check"] == "overlapping_window_check"

    def test_both_policies_failing_keeps_the_current_identity(self):
        rejected = exp.policy_verdict([
            self._c(d, c, c == "pure") for d in (WEEKDAY_DATE, WEEKEND_DATE)
            for c in ("pure", "mixed")])
        outcome = exp.experiment_decision({"canonical_union": rejected,
                                           "day_type_local": rejected})
        assert outcome["decision"] == "keep_composition_aware_identity"
        assert "semantically necessary" in outcome["statement"]


# ── the artifact ────────────────────────────────────────────────────────────

class TestArtifact:
    def _payload(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        return exp.build_artifact(
            root=ROOT,
            controls={WEEKDAY_DATE: {"pure": entry, "mixed": entry}},
            policies={},
            decision={"decision": "inconclusive", "statement": "not run"},
            budget=exp.Budget(),
            preflight=exp.fingerprint_neutrality(ROOT))

    def test_the_artifact_is_explicitly_not_release_evidence(self, tmp_path):
        assert self._payload(tmp_path)["release_evidence"] is False

    def test_the_artifact_binds_tool_code_input_and_control_hashes(self,
                                                                   tmp_path):
        payload = self._payload(tmp_path)
        binding = payload["binding"]
        assert binding["tool_sha256"]
        assert binding["demand_source_fingerprint"]
        assert binding["controls"][WEEKDAY_DATE]["pure"]["key"]
        assert binding["controls"][WEEKDAY_DATE]["pure"]["manifest_sha256"]

    def test_the_artifact_names_every_required_reported_dimension(self,
                                                                  tmp_path):
        payload = self._payload(tmp_path)
        assert set(payload["reported_dimensions"]) == set(
            exp.EQUIVALENCE_DIMENSIONS) | set(exp.PERFORMANCE_FIELDS) | {
                "identity_inputs", "source_hashes", "candidate_count",
                "candidate_semantic_hash", "pfe_shape_variables",
                "route_provenance"}

    def test_writing_refuses_to_clobber_an_existing_artifact(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text("{}")
        with pytest.raises(exp.ExperimentRefused, match="exists"):
            exp.write_artifact({"a": 1}, target)
        assert target.read_text() == "{}"


class TestReadRecordReportsEveryRequiredField:
    def test_a_stored_entry_yields_the_full_reported_record(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        record = exp.read_day_record(entry)
        assert record["identity"]["source_hashes"]["pfe"] == "c" * 64
        assert record["candidate_semantic_hash"] == "a" * 64
        assert record["candidate_count"] == 6000
        assert record["pfe_shape_variables"] == 6000
        assert set(record["variants"]) == set(exp.VARIANT_SUFFIXES)
        assert record["variants"][""]["population"] == 2
        assert record["variants"][""]["publication_health"]["publishable"] \
            is True

    def test_the_route_hash_is_taken_from_uncompressed_bytes(self, tmp_path):
        lib = tmp_path / "lib"
        entry = _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY)
        record = exp.read_day_record(entry)
        import hashlib
        plain = gzip.open(entry / "calibrated.rou.xml.gz", "rb").read()
        assert record["variants"][""]["route_uncompressed_sha256"] == \
            hashlib.sha256(plain).hexdigest()


# ── the isolated cold build: what CAN be checked without SUMO ──────────────

class TestLocatingTheFreshlyBuiltDay:
    """A scratch library holds exactly one arm, so control discovery is wrong.

    ``discover_controls`` fails closed unless BOTH a pure and a mixed entry
    exist — correct for the real library, fatal for the isolated one, which
    holds only the day just built.
    """

    def test_the_single_built_day_is_found(self, tmp_path):
        lib = tmp_path / "scratch-lib"
        built = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED)
        assert exp.find_built_day(lib, WEEKDAY_DATE) == built

    def test_an_empty_scratch_library_is_a_named_refusal(self, tmp_path):
        (tmp_path / "scratch-lib" / WEEKDAY_DATE).mkdir(parents=True)
        with pytest.raises(exp.ExperimentRefused, match="no full calibration"):
            exp.find_built_day(tmp_path / "scratch-lib", WEEKDAY_DATE)

    def test_two_entries_for_one_date_are_refused_as_ambiguous(self, tmp_path):
        lib = tmp_path / "scratch-lib"
        _write_entry(tmp_path, lib, WEEKDAY_DATE, PURE_WEEKDAY,
                     scratch_name="s1")
        _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED, scratch_name="s2")
        with pytest.raises(exp.ExperimentRefused, match="ambiguous"):
            exp.find_built_day(lib, WEEKDAY_DATE)

    def test_a_q50_only_subset_entry_is_not_a_full_calibration(self, tmp_path):
        """Only the exact three-variant set counts (Stage 1's own rule)."""
        lib = tmp_path / "scratch-lib"
        full = _write_entry(tmp_path, lib, WEEKDAY_DATE, MIXED,
                            scratch_name="s1")
        subset = _write_entry(tmp_path, lib, WEEKDAY_DATE, ("weekend",),
                              scratch_name="s2")
        for name in list(subset.iterdir()):
            if name.name.startswith(("calibrated_v", "fit_v")):
                name.unlink()
        manifest = json.loads((subset / "manifest.json").read_text())
        manifest["artifacts"] = {
            k: v for k, v in manifest["artifacts"].items()
            if not k.startswith(("calibrated_v", "fit_v"))}
        (subset / "manifest.json").write_text(json.dumps(manifest))
        assert exp.find_built_day(lib, WEEKDAY_DATE) == full


class TestIsolatedBuildPlan:
    def test_the_plan_never_targets_the_real_sumo_or_day_library(self,
                                                                 tmp_path):
        plan = exp.isolated_build_plan(root=ROOT, workspace=tmp_path,
                                       date=WEEKDAY_DATE,
                                       composition=MIXED)
        assert Path(plan["tree"]).is_relative_to(tmp_path)
        assert Path(plan["day_library_root"]).is_relative_to(tmp_path)
        assert not Path(plan["tree"]).is_relative_to(ROOT)
        assert plan["guards"]["writes_real_sumo_dir"] is False
        assert plan["guards"]["edits_demand_sources"] is False

    def test_the_library_root_is_passed_to_the_build(self, tmp_path):
        plan = exp.isolated_build_plan(root=ROOT, workspace=tmp_path,
                                       date=WEEKDAY_DATE, composition=MIXED)
        assert "--day-library-root" in plan["argv"]
        index = plan["argv"].index("--day-library-root")
        assert plan["argv"][index + 1] == plan["day_library_root"]

    def test_the_workspace_copy_leaves_the_source_tree_untouched(self,
                                                                 tmp_path):
        source = tmp_path / "repo"
        (source / "sumo").mkdir(parents=True)
        (source / "sumo" / "net.net.xml").write_text("<net/>")
        (source / "runs" / "demand-days").mkdir(parents=True)
        (source / "runs" / "demand-days" / "keep.json").write_text("{}")
        (source / "build_sumo_demand.py").write_text("# source\n")
        before = sorted(p.relative_to(source) for p in source.rglob("*"))

        tree = exp.prepare_workspace(source, tmp_path / "ws")

        assert sorted(p.relative_to(source)
                      for p in source.rglob("*")) == before
        assert (tree / "build_sumo_demand.py").read_text() == "# source\n"
        assert (tree / "sumo" / "net.net.xml").is_file()
        assert not (tree / "runs").exists(), "the day library must not be copied"
        assert (tree / "_forced_composition_run.py").is_file()

    def test_a_source_tree_without_sumo_prerequisites_is_refused(self,
                                                                 tmp_path):
        source = tmp_path / "repo"
        source.mkdir()
        (source / "build_sumo_demand.py").write_text("# source\n")
        with pytest.raises(exp.ExperimentRefused, match="sumo"):
            exp.prepare_workspace(source, tmp_path / "ws")

    def test_an_existing_workspace_is_never_deleted(self, tmp_path):
        source = tmp_path / "repo"
        (source / "sumo").mkdir(parents=True)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        marker = workspace / "user-file.txt"
        marker.write_text("keep")
        with pytest.raises(exp.ExperimentRefused, match="already exists"):
            exp.prepare_workspace(source, workspace)
        assert marker.read_text() == "keep"


class TestControlDivergenceProof:
    def test_different_archived_outputs_eliminate_both_candidate_policies(
            self, tmp_path):
        lib = tmp_path / "lib"
        controls = {}
        for date in exp.CONTROL_DATES:
            pure = _write_entry(tmp_path, lib, date, PURE_WEEKDAY,
                                scratch_name=f"{date}-pure")
            mixed = _write_entry(
                tmp_path, lib, date, MIXED,
                vehicles=[("other", 450.0, "A B C")],
                scratch_name=f"{date}-mixed")
            controls[date] = {
                "pure": exp.read_day_record(pure),
                "mixed": exp.read_day_record(mixed),
            }
        proof = exp.control_divergence_proof(controls)
        assert proof["decisive"] is True
        assert proof["decision"] == "keep_composition_aware_identity"
        assert proof["cold_calibrations_needed"] == 0
        assert set(proof["eliminated_policies"]) == \
            set(exp.CANDIDATE_POLICIES)

    def test_equal_controls_do_not_short_circuit_the_experiment(self, tmp_path):
        lib = tmp_path / "lib"
        controls = {}
        for date in exp.CONTROL_DATES:
            pure = _write_entry(tmp_path, lib, date, PURE_WEEKDAY,
                                scratch_name=f"{date}-pure")
            mixed = _write_entry(tmp_path, lib, date, MIXED,
                                 scratch_name=f"{date}-mixed")
            controls[date] = {
                "pure": exp.read_day_record(pure),
                "mixed": exp.read_day_record(mixed),
            }
        proof = exp.control_divergence_proof(controls)
        assert proof["decisive"] is False
        assert proof["cold_calibrations_needed"] == \
            exp.planned_cold_calibrations()


class TestAnUnmeasuredPolicyCannotSettleTheQuestion:
    def _rejected(self):
        return {"decision": "rejected_as_model_change", "statement": "x"}

    def test_a_missing_policy_leaves_the_experiment_inconclusive(self):
        outcome = exp.experiment_decision({"canonical_union": self._rejected()})
        assert outcome["decision"] == "inconclusive"
        assert "day_type_local" in outcome["unmeasured_policies"]

    def test_a_policy_abandoned_at_a_regression_is_not_a_rejection(self):
        outcome = exp.experiment_decision({
            "canonical_union": self._rejected(),
            "day_type_local": {"decision": "not_measured",
                               "statement": "run stopped at a regression"}})
        assert outcome["decision"] == "inconclusive"
        assert "day_type_local" in outcome["unmeasured_policies"]

    def test_both_measured_rejections_still_keep_the_current_identity(self):
        outcome = exp.experiment_decision({"canonical_union": self._rejected(),
                                           "day_type_local": self._rejected()})
        assert outcome["decision"] == "keep_composition_aware_identity"
        assert outcome["unmeasured_policies"] == []


class TestTheForcedCompositionRunner:
    """Properties of the script written into the isolated workspace.

    It cannot be executed without SUMO and a warm tree, so the two ways it
    silently produced wrong performance numbers are pinned by reading it.
    """

    def test_the_timing_path_is_captured_before_argv_is_rewritten(self):
        source = exp._RUNNER
        assert source.index("TIMING_PATH = sys.argv[0]") < \
            source.index('sys.argv = ["build_sumo_demand.py"]'), \
            "the timing file would be written under the build's own argv[0]"

    def test_peak_rss_covers_children_and_the_platform_unit(self):
        source = exp._RUNNER
        assert "RUSAGE_CHILDREN" in source, \
            "the PFE solves in a fork pool; the parent's peak is not the run's"
        assert 'sys.platform == "darwin"' in source, \
            "ru_maxrss is KiB on Linux and bytes on macOS"

    def test_the_composition_is_forced_by_patching_not_by_editing_source(self):
        assert "build_sumo_demand.window_pool_composition = " in exp._RUNNER
        assert "open(" not in exp._RUNNER.split("import build_sumo_demand")[0]

    def test_dynamic_passage_wall_time_is_read_from_build_metadata(self):
        assert 'meta["timings_s"].get("dynamic_passage")' in exp._RUNNER


class TestPreflightCommandLine:
    def test_preflight_only_succeeds_and_writes_nothing(self, tmp_path,
                                                        capsys):
        assert exp.main(["--preflight-only",
                         "--output", str(tmp_path / "unused.json")]) == 0
        assert not (tmp_path / "unused.json").exists()
        printed = json.loads(capsys.readouterr().out)
        assert printed["fingerprint_neutral"] is True
        assert printed["planned_cold_calibrations"] == 4
