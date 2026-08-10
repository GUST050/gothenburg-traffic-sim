"""The frozen scaling baseline must be trustworthy to compare against.

A performance baseline is only useful if a later change cannot quietly beat it
by measuring something else. So these tests pin the properties that make the
record comparable rather than the numbers themselves, which legitimately vary:
phases stay separate, an absent input is recorded as unmeasured WITH a reason
instead of being zero-filled, semantic counts are recorded beside the timings,
the record is content-addressed over its inputs, and nothing is written outside
a private scratch root.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import benchmark_closure_search_scaling as bench


#: Captured at import, BEFORE any test monkeypatches `benchmark_cases`.
#: Reading it through the patched function made the helper call itself.
_SMALL_CASE = next(case for case in bench.benchmark_cases()
                   if case["case_id"] == "brute-force-single-day")


def _small_case():
    """The cheapest frozen case, so tests never pay for a six-month run."""
    return dict(_SMALL_CASE)


@pytest.fixture(scope="module")
def small_case_result(tmp_path_factory):
    """One measured run shared by every phase assertion.

    `run_case` spawns two child interpreters for its isolated RSS arms, so
    re-running it per test turned this file into an eleven-minute suite.
    """
    scratch = tmp_path_factory.mktemp("scaling-case")
    return bench.run_case(_small_case(), repeats=5, scratch=scratch,
                          identities=bench._identities())


@pytest.fixture(scope="module")
def small_record():
    """One built record shared by the contract assertions."""
    cases = (_small_case(),)
    original = bench.benchmark_cases
    bench.benchmark_cases = lambda: cases
    try:
        return bench.build_record(repeats=5)
    finally:
        bench.benchmark_cases = original


class TestCaseSet:
    def test_it_freezes_both_documented_six_month_cases(self):
        ids = {case["case_id"] for case in bench.benchmark_cases()}
        assert {"six-month-720h", "six-month-360h"} <= ids

    def test_it_includes_small_brute_force_fixtures(self):
        kinds = [case["kind"] for case in bench.benchmark_cases()]
        assert kinds.count("small_brute_force") >= 3

    def test_the_six_month_cases_match_the_plans_contract(self):
        """The plan's table is 2027-01-01..2027-06-30, weekdays, 06-18, up to
        90 workdays. A baseline measured on a different contract would not be
        the baseline the plan refers to."""
        for case in bench.benchmark_cases():
            if case["kind"] != "six_month_rolling":
                continue
            spec = case["spec"]
            assert spec["permitted_date_start"] == "2027-01-01"
            assert spec["permitted_date_end"] == "2027-06-30"
            assert spec["allowed_weekdays"] == [0, 1, 2, 3, 4]
            assert spec["max_consecutive_start_days"] == 90
            assert spec["permitted_daily_band"] == {
                "earliest_start": "06:00", "latest_end": "18:00"}
        hours = sorted(case["spec"]["required_work_minutes"] // 60
                       for case in bench.benchmark_cases()
                       if case["kind"] == "six_month_rolling")
        assert hours == [360, 720]

    def test_every_frozen_case_is_a_valid_contract(self):
        from traffic_sim.core.contracts import ClosureSearchSpec
        for case in bench.benchmark_cases():
            ClosureSearchSpec.from_dict(dict(case["spec"]))

    def test_the_dst_fixture_really_spans_the_autumn_transition(self):
        case = next(item for item in bench.benchmark_cases()
                    if item["case_id"] == "brute-force-dst")
        spec = case["spec"]
        assert spec["permitted_date_start"] <= "2027-10-31"
        assert spec["permitted_date_end"] >= "2027-10-31"


class TestRepeats:
    def test_fewer_than_five_repeats_is_refused(self):
        """The plan requires at least five runs of every pure performance
        case; a median over fewer is not the statistic it asks for."""
        with pytest.raises(SystemExit):
            bench.build_record(repeats=4)

    def test_a_measured_phase_reports_median_and_p95_over_the_repeats(self):
        record, value = bench._measure("probe", lambda: 42, repeats=5)
        assert value == 42
        assert record["status"] == "measured" and record["repeats"] == 5
        assert record["min_s"] <= record["median_s"] <= record["max_s"]
        assert record["min_s"] <= record["p95_s"] <= record["max_s"]
        assert record["peak_traced_bytes"] >= 0


class TestUnmeasuredPhases:
    def test_an_unmeasured_phase_carries_a_reason_and_no_timing(self):
        record = bench._unmeasured("sumo_wall_time", "no calibrated demand")
        assert record["status"] == "unmeasured"
        assert record["reason"]
        assert "median_s" not in record and "p95_s" not in record

    def test_absent_routes_make_the_cost_phases_unmeasured_not_zero(
            self, tmp_path, monkeypatch):
        """Zero-filling an unmeasurable phase would make a future measurement
        look like a regression against a baseline that never measured it."""
        identities = dict(bench._identities())
        identities["routes"] = {name: None for name in bench.ROUTE_FILES}
        result = bench.run_case(_small_case(), repeats=5, scratch=tmp_path,
                                identities=identities)
        phases = {phase["phase"]: phase for phase in result["phases"]}
        for name in ("deterministic_cost", "sumo_wall_time"):
            assert phases[name]["status"] == "unmeasured"
            assert "calibrated demand routes are absent" in phases[name]["reason"]

    def test_sumo_wall_time_stays_unmeasured_even_with_routes_present(
            self, tmp_path):
        """Per-case sizing stays pure; the opt-in external probe owns SUMO."""
        identities = dict(bench._identities())
        identities["routes"] = {name: "0" * 64 for name in bench.ROUTE_FILES}
        result = bench.run_case(_small_case(), repeats=5, scratch=tmp_path,
                                identities=identities)
        phases = {phase["phase"]: phase for phase in result["phases"]}
        assert phases["sumo_wall_time"]["status"] == "unmeasured"
        assert "outcomes" in phases["sumo_wall_time"]["reason"]

    def test_external_probe_is_explicitly_unmeasured_without_authorization(
            self, tmp_path):
        probe = bench._external_phase_probe(
            _small_case(), repeats=5, scratch=tmp_path,
            identities=bench._identities(), execute=False)
        assert probe["deterministic_cost"]["status"] == "unmeasured"
        assert probe["sumo_wall_time"]["status"] == "unmeasured"
        assert "--execute-external-phases" in (
            probe["sumo_wall_time"]["reason"])


class TestPhaseSeparation:
    def test_the_plans_phases_are_reported_separately(self, small_case_result):
        names = [phase["phase"] for phase in small_case_result["phases"]]
        assert names == ["calendar_generation", "daily_unit_decomposition",
                         "preflight", "cache_lookup", "deterministic_cost",
                         "sumo_wall_time"]
        assert len(names) == len(set(names)), "a phase may not be reported twice"

    def test_artifact_size_is_recorded_for_both_materializations(
            self, small_case_result):
        sizes = small_case_result["artifact_bytes"]
        assert sizes["parent_schedules_json_bytes"] > 0
        assert sizes["daily_units_json_bytes"] > 0

    def test_peak_rss_is_measured_per_case_in_a_fresh_interpreter(
            self, small_case_result):
        """A resident-memory figure from the parent would be the cumulative
        high-water mark of every case already run."""
        for arm in ("materialize_calendar_and_units", "preflight_only"):
            entry = small_case_result["peak_rss"][arm]
            assert entry["status"] == "measured", entry
            assert entry["peak_rss_bytes"] > 0

    def test_the_child_does_not_inherit_the_parents_high_water_mark(self):
        """Regression. Linux keeps `ru_maxrss` on signal_struct, which survives
        fork+exec, so a child launched from a large parent reported the
        PARENT's peak — every case came back with an identical 997 MiB. The
        child reads /proc VmHWM instead, which the new mm resets."""
        ballast = bytearray(400 * 1024 * 1024)     # make the parent large
        try:
            entry = bench._isolated_peak_rss(_small_case()["spec"], "preflight")
        finally:
            del ballast
        assert entry["status"] == "measured"
        assert entry["peak_rss_bytes"] < 300 * 1024 * 1024, (
            "the child reported a peak it could not have reached on its own")


class TestSemanticAgreement:
    def test_preflight_and_generator_counts_are_compared_and_recorded(
            self, small_case_result):
        """The record must carry semantics, not only speed: a faster path that
        changed a count has to fail here rather than read as an improvement."""
        agreement = small_case_result["semantic_agreement"]
        assert agreement["parent_schedule_count_matches"] is True
        assert agreement["unique_daily_unit_count_matches"] is True
        assert (agreement["generator_parent_schedule_count"]
                == agreement["preflight_parent_schedule_count"])
        assert (agreement["generator_unique_daily_unit_count"]
                == agreement["preflight_unique_daily_unit_count"])


class TestIsolation:
    def test_the_cache_probe_uses_only_the_supplied_scratch_root(self, tmp_path):
        record = bench._cache_probe_phase(
            [f"{index:064x}" for index in range(10)], tmp_path, repeats=5)
        assert record["status"] == "measured"
        assert record["probed_entries"] == 10
        assert record["present_entries"] == 5      # half are seeded
        written = list(tmp_path.rglob("*.json"))
        assert written and all(tmp_path in path.parents for path in written)

    def test_running_a_case_writes_nothing_outside_its_scratch(self, tmp_path):
        watched = [ROOT / "runs", ROOT / "web" / "data" / "scenarios"]
        before = [sorted(str(p) for p in root.rglob("*")) if root.exists() else None
                  for root in watched]
        bench.run_case(_small_case(), repeats=5, scratch=tmp_path,
                       identities=bench._identities())
        after = [sorted(str(p) for p in root.rglob("*")) if root.exists() else None
                 for root in watched]
        assert after == before

    def test_it_refuses_to_overwrite_a_frozen_baseline(self, tmp_path):
        target = tmp_path / "baseline.json"
        target.write_text("{}")
        with pytest.raises(SystemExit) as error:
            bench.main(["--out", str(target)])
        assert "refusing to overwrite" in str(error.value)


class TestRecordContract:
    def test_it_is_labelled_diagnostic_and_opens_no_gate(self, small_record):
        record = small_record
        assert record["evidence_class"] == "diagnostic_baseline"
        assert record["claim_boundary"]["release_evidence"] is False
        assert record["claim_boundary"]["opens_no_gate"] is True

    def test_it_binds_the_exact_identities_the_plan_lists(self, small_record):
        identities = small_record["identities"]
        assert identities["python"]["version"]
        assert "sumo_version" in identities
        assert set(identities["sumo_binary"]) == {"path", "sha256"}
        assert identities["demand_meta"]["path"] == bench.DEMAND_META_FILE
        assert "sha256" in identities["demand_meta"]
        assert identities["network"]["path"] == "sumo/net.net.xml"
        assert set(identities["routes"]) == set(bench.ROUTE_FILES)
        assert set(identities["policies"]) == set(bench.BOUND_POLICIES)
        assert set(identities["sources"]) == set(bench.BOUND_SOURCES)
        assert identities["sources"][
            "traffic_sim/simulation/closure_preflight.py"]

    def test_closure_integrity_sources_stay_bound(self, small_record):
        """Teleport policy and survivability decide what a closure run IS, so a
        baseline measured before they changed must not read as comparable
        afterwards."""
        sources = small_record["identities"]["sources"]
        assert sources["traffic_sim/simulation/closure_teleport.py"]
        assert sources["traffic_sim/simulation/closure_survivability.py"]

    def test_the_input_content_key_is_stable_and_input_dependent(
            self, small_record, monkeypatch):
        """The same inputs must address the same record even though the
        measured timings differ; a changed input must not."""
        identical = bench._content_key({
            "schema": bench.SCHEMA,
            "identities": small_record["identities"],
            "cases": [_small_case()["spec"]],
            "repeats": 5,
            "external_phases_requested": False,
        })
        assert identical == small_record["input_content_key"]
        changed = bench._content_key({
            "schema": bench.SCHEMA,
            "identities": small_record["identities"],
            "cases": [{**_small_case()["spec"], "required_work_minutes": 495}],
            "repeats": 5,
            "external_phases_requested": False,
        })
        assert changed != small_record["input_content_key"]

    def test_the_record_serializes_as_canonical_json(self, small_record):
        json.dumps(small_record, sort_keys=True, allow_nan=False)

    def test_the_process_peak_is_labelled_as_cumulative(self, small_record):
        assert "cumulative" in small_record["process_peak_rss_note"]


class TestFrozenArtifact:
    """The published baseline this PR freezes, checked as a contract."""

    PATH = ROOT / "validation" / "closure_search_scaling_baseline_v1.json"

    def test_the_baseline_exists_and_declares_its_schema(self):
        record = json.loads(self.PATH.read_text())
        assert record["schema"] == "closure_search_scaling_baseline_v1"
        assert record["evidence_class"] == "diagnostic_baseline"

    def test_frozen_input_key_and_tracked_sources_recompute(self):
        record = json.loads(self.PATH.read_text())
        expected = bench._content_key({
            "schema": bench.SCHEMA,
            "identities": record["identities"],
            "cases": [case["spec"] for case in bench.benchmark_cases()],
            "repeats": record["repeats"],
            "external_phases_requested":
                record["external_phases_requested"],
        })
        assert record["input_content_key"] == expected
        assert record["identities"]["sources"] == {
            name: bench._sha256(ROOT / name) for name in bench.BOUND_SOURCES
        }

    def test_it_covers_every_frozen_case(self):
        record = json.loads(self.PATH.read_text())
        assert {case["case_id"] for case in record["cases"]} == {
            case["case_id"] for case in bench.benchmark_cases()}

    def test_the_six_month_counts_match_the_plans_measured_table(self):
        """The plan documents 2,186/5,676 and 11,813/23,349. The baseline
        reproducing them is what makes it the same reference."""
        record = json.loads(self.PATH.read_text())
        counts = {case["case_id"]: case["counts"] for case in record["cases"]}
        assert counts["six-month-720h"]["parent_schedule_count"] == 2186
        assert counts["six-month-720h"]["unique_daily_unit_count"] == 5676
        assert counts["six-month-360h"]["parent_schedule_count"] == 11813
        assert counts["six-month-360h"]["unique_daily_unit_count"] == 23349

    def test_every_case_agrees_semantically(self):
        record = json.loads(self.PATH.read_text())
        for case in record["cases"]:
            assert case["semantic_agreement"]["parent_schedule_count_matches"]
            assert case["semantic_agreement"]["unique_daily_unit_count_matches"]

    def test_materializing_costs_more_memory_than_counting(self):
        """The measurement has to be able to SEE the difference it exists to
        track: building 11,813 schedules and 23,349 units must cost more than
        counting them. If both arms matched, the figure would be measuring the
        interpreter rather than the work."""
        record = json.loads(self.PATH.read_text())
        case = next(item for item in record["cases"]
                    if item["case_id"] == "six-month-360h")
        materialize = case["peak_rss"]["materialize_calendar_and_units"]
        counting = case["peak_rss"]["preflight_only"]
        assert materialize["status"] == counting["status"] == "measured"
        assert materialize["peak_rss_bytes"] > counting["peak_rss_bytes"]

    def test_the_over_budget_case_is_classified_as_such(self):
        """The 360-hour case is a valid contract the cap refuses; the whole
        point of PR B is that this is known before the run."""
        record = json.loads(self.PATH.read_text())
        classes = {case["case_id"]: case["counts"]["size_class"]
                   for case in record["cases"]}
        assert classes["six-month-360h"] == "over_resource_budget"
        assert classes["six-month-720h"] == "large_but_runnable"

    def test_required_external_phases_were_measured(self):
        record = json.loads(self.PATH.read_text())
        assert record["external_phases_requested"] is True
        assert record["identities"]["sumo_binary"]["sha256"]
        assert record["identities"]["demand_meta"]["sha256"]
        assert all(record["identities"]["routes"].values())
        probe = record["external_phase_probe"]
        assert probe["deterministic_cost"]["status"] == "measured"
        assert probe["sumo_wall_time"]["status"] == "measured"
        assert probe["deterministic_cost"]["repeats"] >= 5
        assert probe["sumo_wall_time"]["repeats"] >= 5
