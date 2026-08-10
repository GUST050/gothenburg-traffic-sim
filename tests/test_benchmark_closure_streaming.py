"""The PR C comparison record must not be able to flatter itself.

A benchmark that reports its own improvement is worth exactly as much as the
constraints around it, so these tests pin those constraints rather than the
numbers: the frozen PR A baseline is named but never rewritten, a memory figure
measured on the wrong host cannot be reported as a pass, a count that changed
is a failure rather than a speed-up, and the 10,000-unit cap is reported rather
than moved.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import benchmark_closure_search_scaling as baseline_bench
from tools import benchmark_closure_streaming as bench


_SMALL_CASE = next(case for case in baseline_bench.benchmark_cases()
                   if case["case_id"] == "brute-force-single-day")

PUBLISHED = ROOT / "validation" / "closure_search_streaming_v1.json"


@pytest.fixture(scope="module")
def small_record():
    """One built record shared by the contract assertions.

    Module scope on purpose: `build_record` spawns three child interpreters per
    case, so rebuilding it per test would turn this file into a benchmark.
    """
    original = bench.benchmark_cases
    bench.benchmark_cases = lambda: (_SMALL_CASE,)
    try:
        return bench.build_record(repeats=5, timeout=600)
    finally:
        bench.benchmark_cases = original


@pytest.fixture(scope="module")
def published_record():
    return json.loads(PUBLISHED.read_text())


def _phase(name, **overrides):
    record = {
        "phase": name,
        "status": "measured",
        "repeats": 5,
        "peak_rss_bytes": 20 * 1024 * 1024,
        "import_peak_rss_bytes": 16 * 1024 * 1024,
        "peak_rss_over_imports_bytes": 4 * 1024 * 1024,
        "median_s": 1.0,
        "p95_s": 1.1,
        "min_s": 0.9,
        "max_s": 1.1,
        "parent_schedule_count": 10,
        "unique_daily_unit_count": 20,
        "parent_unit_edge_count": 30,
    }
    record.update(overrides)
    return record


class TestSeparationFromTheFrozenBaseline:
    def test_it_writes_a_different_artifact(self):
        assert bench.DEFAULT_OUT != bench.BASELINE_RECORD
        assert bench.DEFAULT_OUT.name == "closure_search_streaming_v1.json"

    def test_it_quotes_the_frozen_baselines_own_input_key(self):
        binding = bench._baseline_binding()
        assert binding["rewritten_by_this_tool"] is False
        assert binding["declared_input_content_key"] == (
            bench.BASELINE_INPUT_CONTENT_KEY)
        assert binding["input_content_key_matches"] is True
        assert binding["stored_input_content_key"] == (
            json.loads(bench.BASELINE_RECORD.read_text())["input_content_key"])

    def test_it_states_that_source_drift_on_the_baseline_is_expected(self):
        binding = bench._baseline_binding()
        assert "not a defect" in binding["expected_source_drift"]
        assert set(binding["external_phases_not_rerun"]) == {
            "deterministic_cost", "sumo_wall_time"}

    def test_building_the_record_leaves_the_frozen_baseline_untouched(
            self, monkeypatch):
        before = bench._sha256(bench.BASELINE_RECORD)
        monkeypatch.setattr(bench, "benchmark_cases", lambda: (_SMALL_CASE,))
        bench.build_record(repeats=5, timeout=600)
        assert bench._sha256(bench.BASELINE_RECORD) == before


class TestRepeats:
    def test_fewer_than_five_repeats_is_refused(self):
        with pytest.raises(SystemExit):
            bench.build_record(repeats=4, timeout=600)


class TestMemoryGate:
    """The gate is a claim about a machine, so it has to name the machine."""

    IDENTITIES_LINUX = {"platform": {"system": "Linux", "machine": "x86_64"}}
    IDENTITIES_MAC = {"platform": {"system": "Darwin", "machine": "arm64"}}

    @staticmethod
    def _cases(peak, over_imports):
        return [{
            "case_id": "six-month-720h",
            "phases": [_phase("stream", peak_rss_bytes=peak,
                              peak_rss_over_imports_bytes=over_imports)],
        }]

    def test_under_the_gate_on_the_baselines_own_host_passes(self):
        gate = bench._memory_gate(
            self._cases(30 * 1024 * 1024, 8 * 1024 * 1024),
            self.IDENTITIES_MAC)
        assert gate["status"] == "passed"
        assert gate["comparable_to_frozen_baseline_host"] is True

    def test_under_the_gate_on_another_host_does_not_claim_a_pass(self):
        gate = bench._memory_gate(
            self._cases(30 * 1024 * 1024, 8 * 1024 * 1024),
            self.IDENTITIES_LINUX)
        assert gate["status"] == "open_pending_baseline_host"
        assert gate["comparable_to_frozen_baseline_host"] is False
        assert "stays open" in gate["reason"]

    def test_a_fixed_import_cost_is_named_rather_than_subtracted_silently(self):
        gate = bench._memory_gate(
            self._cases(140 * 1024 * 1024, 8 * 1024 * 1024),
            self.IDENTITIES_LINUX)
        assert gate["status"] == "open_fixed_import_cost_dominates"
        assert gate["total_under_gate"] is False
        assert gate["enumeration_under_gate"] is True
        assert "scipy" in gate["reason"]

    def test_an_enumeration_over_the_gate_fails(self):
        gate = bench._memory_gate(
            self._cases(400 * 1024 * 1024, 300 * 1024 * 1024),
            self.IDENTITIES_MAC)
        assert gate["status"] == "failed"

    def test_an_unmeasured_case_is_not_reported_as_a_pass(self):
        gate = bench._memory_gate([], self.IDENTITIES_MAC)
        assert gate["status"] == "unmeasured"
        assert gate["gate_bytes"] == bench.STREAMING_PEAK_RSS_GATE_BYTES


class TestSemanticAgreement:
    def test_matching_counts_agree(self):
        agreement = bench._semantic_agreement(_phase("materialize"),
                                              _phase("stream"))
        assert agreement["all_match"] is True

    def test_a_changed_count_is_reported_as_disagreement(self):
        agreement = bench._semantic_agreement(
            _phase("materialize"),
            _phase("stream", unique_daily_unit_count=19))
        assert agreement["unique_daily_unit_count_matches"] is False
        assert agreement["all_match"] is False

    def test_an_unmeasured_arm_never_reads_as_agreement(self):
        agreement = bench._semantic_agreement(
            _phase("materialize"),
            {"phase": "stream", "status": "unmeasured", "reason": "boom"})
        assert agreement["status"] == "unavailable"
        assert "all_match" not in agreement


class TestRecordContract:
    def test_it_opens_no_gate_and_is_not_release_evidence(self, small_record):
        assert small_record["evidence_class"] == "diagnostic_comparison"
        assert small_record["claim_boundary"]["release_evidence"] is False
        assert small_record["claim_boundary"]["opens_no_gate"] is True

    def test_it_binds_the_ledger_contract_and_its_own_tool(self, small_record):
        identities = small_record["identities"]
        assert identities["ledger_contract"] == {
            "schema": bench.LEDGER_SCHEMA, "version": bench.LEDGER_VERSION}
        assert identities["benchmark_tool"]["sha256"] == bench._sha256(
            ROOT / "tools" / "benchmark_closure_streaming.py")
        assert identities["sources"] == {
            name: bench._sha256(ROOT / name) for name in bench.BOUND_SOURCES}

    def test_it_is_content_addressed_over_its_inputs(self, small_record):
        expected = bench._content_key({
            "schema": bench.SCHEMA,
            "identities": small_record["identities"],
            "cases": [case["spec"] for case in small_record["cases"]],
            "baseline_input_content_key": bench.BASELINE_INPUT_CONTENT_KEY,
            "repeats": small_record["repeats"],
        })
        assert small_record["input_content_key"] == expected

    def test_it_never_runs_sumo(self, small_record):
        assert small_record["isolation"]["ran_sumo"] is False
        assert small_record["isolation"]["wrote_into_evidence_roots"] is False

    def test_each_case_measures_both_paths_in_child_interpreters(self, small_record):
        case = small_record["cases"][0]
        phases = {item["phase"]: item for item in case["phases"]}
        assert set(phases) == {"materialize", "stream", "preflight"}
        for item in phases.values():
            assert item["status"] == "measured"
            assert item["repeats"] == 5
            assert item["min_s"] <= item["median_s"] <= item["max_s"]
            assert item["min_s"] <= item["p95_s"] <= item["max_s"]
            assert item["peak_rss_bytes"] >= item["import_peak_rss_bytes"]

    def test_the_streaming_path_holds_less_than_the_materialising_one(
            self, small_record):
        """The comparison has to be able to see the thing it exists to show."""
        case = small_record["cases"][0]
        reduction = case["peak_rss_reduction"]
        assert (reduction["stream_over_imports_bytes"]
                < reduction["materialize_over_imports_bytes"])

    def test_the_unit_cap_is_reported_and_unchanged(self, small_record):
        budget = small_record["cases"][0]["resource_budget"]
        assert budget["maximum_daily_units"] == 10_000
        assert budget["cap_changed_by_pr_c"] is False


@pytest.mark.skipif(not PUBLISHED.is_file(),
                    reason="the PR C comparison record has not been built")
class TestPublishedRecord:
    def test_it_declares_its_schema_and_boundary(self, published_record):
        assert published_record["schema"] == "closure_search_streaming_v1"
        assert published_record["evidence_class"] == "diagnostic_comparison"
        assert published_record["claim_boundary"]["opens_no_gate"] is True

    def test_it_is_self_consistent_and_fully_identified(self, published_record):
        """Checked against ITSELF, not against the tree.

        Requiring the stored digests to equal today's files would make every
        later PR that touches the search fail here until someone re-ran a
        benchmark — the same trap PR A's baseline test fell into. What must
        hold is that the record names every bound source, records a real digest
        for each, and content-addresses its own inputs.
        """
        import re

        identities = published_record["identities"]
        assert set(identities["sources"]) == set(bench.BOUND_SOURCES)
        for name, digest in identities["sources"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest or ""), name
        assert re.fullmatch(
            r"[0-9a-f]{64}", identities["benchmark_tool"]["sha256"] or "")
        assert identities["ledger_contract"]["schema"] == bench.LEDGER_SCHEMA
        assert published_record["input_content_key"] == bench._content_key({
            "schema": bench.SCHEMA,
            "identities": identities,
            "cases": [case["spec"] for case in published_record["cases"]],
            "baseline_input_content_key": bench.BASELINE_INPUT_CONTENT_KEY,
            "repeats": published_record["repeats"],
        })

    def test_it_does_not_claim_to_have_rerun_the_frozen_external_phases(
            self, published_record):
        baseline = published_record["baseline"]
        assert baseline["rewritten_by_this_tool"] is False
        assert baseline["declared_input_content_key"] == (
            bench.BASELINE_INPUT_CONTENT_KEY)
        assert set(baseline["external_phases_not_rerun"]) == {
            "deterministic_cost", "sumo_wall_time"}
        assert published_record["isolation"]["ran_sumo"] is False

    def test_it_covers_every_frozen_case(self, published_record):
        assert {case["case_id"] for case in published_record["cases"]} == {
            case["case_id"] for case in baseline_bench.benchmark_cases()}

    def test_every_case_agrees_with_the_v1_decomposition(self, published_record):
        for case in published_record["cases"]:
            agreement = case["semantic_agreement"]
            assert agreement["status"] == "measured", case["case_id"]
            assert agreement["all_match"] is True, case["case_id"]

    def test_the_six_month_counts_still_match_the_plans_table(self, published_record):
        counts = {
            case["case_id"]: case["semantic_agreement"]["stream"]
            for case in published_record["cases"]
        }
        assert counts["six-month-720h"]["parent_schedule_count"] == 2186
        assert counts["six-month-720h"]["unique_daily_unit_count"] == 5676
        assert counts["six-month-360h"]["parent_schedule_count"] == 11813
        assert counts["six-month-360h"]["unique_daily_unit_count"] == 23349

    def test_the_over_budget_case_is_still_refused_by_the_cap(self, published_record):
        budgets = {case["case_id"]: case["resource_budget"]
                   for case in published_record["cases"]}
        assert budgets["six-month-360h"]["refused_by_unit_cap"] is True
        assert budgets["six-month-720h"]["refused_by_unit_cap"] is False
        for budget in budgets.values():
            assert budget["maximum_daily_units"] == 10_000
            assert budget["cap_changed_by_pr_c"] is False

    def test_the_360h_case_no_longer_fails_for_memory(self, published_record):
        """PR C's stated target: the case may be refused by the cap, but it
        must no longer fail because the object graph could not be held."""
        case = next(item for item in published_record["cases"]
                    if item["case_id"] == "six-month-360h")
        stream = next(item for item in case["phases"]
                      if item["phase"] == "stream")
        assert stream["status"] == "measured"
        assert stream["parent_schedule_count"] == 11813

    def test_the_streaming_path_costs_less_on_both_six_month_cases(self, published_record):
        for case_id in ("six-month-720h", "six-month-360h"):
            case = next(item for item in published_record["cases"]
                        if item["case_id"] == case_id)
            reduction = case["peak_rss_reduction"]
            assert (reduction["stream_over_imports_bytes"]
                    < reduction["materialize_over_imports_bytes"]), case_id

    def test_the_memory_gate_states_its_comparability(self, published_record):
        gate = published_record["memory_gate"]
        assert gate["status"] in {
            "passed", "open_pending_baseline_host",
            "open_fixed_import_cost_dominates", "failed"}
        assert gate["gate_bytes"] == 64 * 1024 * 1024
        if gate["status"] == "passed":
            assert gate["comparable_to_frozen_baseline_host"] is True
        else:
            assert gate["reason"]
