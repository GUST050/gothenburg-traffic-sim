"""Re-scoring a stored campaign's disqualifications under today's rules.

Closes the OPEN item in docs/OPEN_ISSUES_2026-08-06.md §5 — "21 of 61
disqualified v9 schedules carried no teleports and no throughput ... it has not
been re-measured".

The property that matters is restraint: the tool must partition recorded
reasons and must NOT turn that partition into a claim about a future campaign.
A re-score cannot know what a run without teleporting would produce.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import remeasure_closure_disqualification as rescore


def _outcomes(tmp_path, cases):
    path = Path(tmp_path) / "outcomes.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "monthly_proxy_validation_outcomes",
        "campaign_version": "v9",
        "manifest_content_key": "deadbeef",
        "cases": cases,
    }))
    return path


def _case(case_id, candidates):
    return {"case_id": case_id,
            "candidates": {c["schedule_id"]: c for c in candidates}}


def _candidate(schedule_id, reasons):
    return {"schedule_id": schedule_id, "eligible": not reasons,
            "hard_failures": list(reasons)}


class TestClassification:
    def test_access_only_failures_are_already_fixed_by_c1(self):
        verdict = rescore.classify(["dropped_unreachable_vehicles"])
        assert verdict["projected_eligible"] is True
        assert verdict["projection_depends_on_teleport_removal"] is False

    def test_teleport_failures_depend_on_stage_3(self):
        verdict = rescore.classify(["teleports"])
        assert verdict["projected_eligible"] is True
        assert verdict["projection_depends_on_teleport_removal"] is True

    def test_throughput_is_its_own_bucket_because_it_is_only_expected_to_follow(self):
        """Stage 1 measured a teleport as NECESSARY for throughput, not as
        identical to it. Collapsing the two would overstate the projection."""
        verdict = rescore.classify(["active_closure_edge_throughput"])
        assert verdict["throughput_reasons"] == ["active_closure_edge_throughput"]
        assert verdict["teleport_reasons"] == []
        assert verdict["projection_depends_on_teleport_removal"] is True

    def test_an_untouched_reason_still_disqualifies(self):
        verdict = rescore.classify(["queue_proxy_unmeasured"])
        assert verdict["projected_eligible"] is False
        assert verdict["reasons_still_disqualifying"] == ["queue_proxy_unmeasured"]

    def test_one_untouched_reason_is_enough_to_refuse(self):
        """Projected eligibility must never be granted because the interesting
        reasons happened to dominate."""
        verdict = rescore.classify(
            ["teleports", "dropped_unreachable_vehicles", "baseline_teleports"])
        assert verdict["projected_eligible"] is False
        assert verdict["reasons_still_disqualifying"] == ["baseline_teleports"]

    def test_no_reasons_at_all_is_eligible(self):
        assert rescore.classify([])["projected_eligible"] is True


class TestReport:
    def test_totals_partition_the_disqualified_schedules(self, tmp_path):
        path = _outcomes(tmp_path, [_case("c1", [
            _candidate("s1", []),
            _candidate("s2", ["dropped_unreachable_vehicles"]),
            _candidate("s3", ["teleports", "active_closure_edge_throughput"]),
            _candidate("s4", ["teleports", "truncated_unreachable_vehicles"]),
            _candidate("s5", ["unfinished_vehicle_share"]),
        ])])
        report = rescore.build_report(path)
        totals = report["totals"]
        assert totals["schedules"] == 5
        assert totals["disqualified_as_recorded"] == 4
        assert totals["access_only"] == 1
        assert totals["teleport_or_throughput_only"] == 1
        assert totals["access_and_teleport_only"] == 1
        assert totals["still_disqualified"] == 1
        # The partition must be exhaustive; a schedule falling through every
        # bucket would silently vanish from the count.
        assert (totals["access_only"] + totals["teleport_or_throughput_only"]
                + totals["access_and_teleport_only"]
                + totals["still_disqualified"]
                == totals["disqualified_as_recorded"])

    def test_a_case_needs_two_eligible_schedules_to_be_rankable(self, tmp_path):
        """One eligible schedule has nothing to be ranked against."""
        path = _outcomes(tmp_path, [
            _case("one", [_candidate("s1", []),
                          _candidate("s2", ["unfinished_vehicle_share"])]),
            _case("two", [_candidate("s1", []),
                          _candidate("s2", ["teleports"])]),
        ])
        report = rescore.build_report(path)
        by_id = {case["case_id"]: case for case in report["cases"]}
        assert by_id["one"]["projected_rankable"] is False
        assert by_id["two"]["projected_rankable"] is True
        assert report["case_summary"]["rankable_as_recorded"] == 0
        assert report["case_summary"]["rankable_projected"] == 1

    def test_the_report_states_that_it_is_not_a_prediction(self, tmp_path):
        path = _outcomes(tmp_path, [_case("c", [_candidate("s", ["teleports"])])])
        report = rescore.build_report(path)
        assert report["interpretation_limits"], (
            "a re-score that did not say it was a re-score would be read as a "
            "campaign result")
        assert any("not a re-run" in limit
                   for limit in report["interpretation_limits"])
        assert "passed" not in report and "gate_passed" not in report

    def test_the_source_is_pinned_by_digest(self, tmp_path):
        path = _outcomes(tmp_path, [_case("c", [_candidate("s", [])])])
        report = rescore.build_report(path)
        assert len(report["source"]["outcomes_sha256"]) == 64
        assert report["source"]["campaign_version"] == "v9"

    def test_a_list_of_candidates_is_accepted_as_well_as_a_map(self, tmp_path):
        path = Path(tmp_path) / "outcomes.json"
        path.write_text(json.dumps({"cases": [
            {"case_id": "c", "candidates": [_candidate("s", ["teleports"])]}]}))
        report = rescore.build_report(path)
        assert report["totals"]["schedules"] == 1

    def test_an_empty_outcomes_file_is_refused(self, tmp_path):
        path = Path(tmp_path) / "outcomes.json"
        path.write_text(json.dumps({"cases": []}))
        with pytest.raises(SystemExit):
            rescore.build_report(path)


class TestCli:
    def test_it_refuses_to_overwrite_an_existing_report(self, tmp_path):
        outcomes = _outcomes(tmp_path, [_case("c", [_candidate("s", [])])])
        out = Path(tmp_path) / "report.json"
        out.write_text("{}")
        with pytest.raises(SystemExit):
            rescore.main(["--outcomes", str(outcomes), "--out", str(out)])

    def test_it_writes_the_report(self, tmp_path, capsys):
        outcomes = _outcomes(tmp_path, [_case("c", [_candidate("s", [])])])
        out = Path(tmp_path) / "report.json"
        assert rescore.main(["--outcomes", str(outcomes), "--out", str(out)]) == 0
        assert json.loads(out.read_text())["kind"] == \
            "closure_disqualification_rescore"
