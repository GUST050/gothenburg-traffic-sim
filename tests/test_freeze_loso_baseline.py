"""Contract tests for the frozen corrected LOSO/TAG baseline (plan WP0).

The sweep evidence lives under the Git-ignored ``runs/`` tree, so the tracked
summary is the only thing a later actor can check.  These tests pin the
acceptance gate that decides whether a sweep may be frozen at all: an
incomplete manifest, a mixed protocol, a repeated candidate pool, a missing
assignment ceiling or drifted bound inputs must all be refused rather than
silently frozen into a baseline that later treatments are compared against.
"""

import json
from pathlib import Path

import pytest

from tools.freeze_loso_baseline import (EXPECTED_PROTOCOL, ROOT, build_summary,
                                        check_gate)

SEEDS = (20260807, 20260808, 20260809, 20260810, 20260811, 42)
STATIONS = (
    ("107", "station_total", ["a_b_0", "c_d_0"], "two_way_total"),
    ("1074", "e_f_0", ["e_f_0"], "directional"),
    ("1076", "g_h_0", ["g_h_0"], "directional"),
    ("133", "i_j_0", ["i_j_0"], "directional"),
    ("134", "j_k_0", ["j_k_0"], "directional"),
    ("2276", "j_l_0", ["j_l_0"], "directional"),
)


def _run(seed, index, share=70.0, protocol=EXPECTED_PROTOCOL, ceiling=True):
    edges = [
        {
            "station": station,
            "evaluation": evaluation,
            "component_edges": list(components),
            "measurement_semantics": semantics,
            "ratio": 1.0 + 0.1 * position + 0.01 * index,
            "geh_ok_pct": 50.0,
            "assignment_ceiling": ceiling,
        }
        for position, (station, evaluation, components, semantics)
        in enumerate(STATIONS)
    ]
    run = {
        "seed": seed,
        "protocol": protocol,
        "candidate_pool_sha256": f"pool{index}",
        "tag_satisfactory_cases": int(round(share * 1.43)),
        "tag_total_cases": 143,
        "tag_satisfactory_share_pct": share,
        "tag_verdict": "fail",
        "all_held_edges_received_assignment_ceiling": ceiling,
        "edges": edges,
    }
    if protocol in {"loso_pfe_meso_v6", "loso_pfe_meso_v7"}:
        run["demand_build_id"] = f"build-{seed}"
        run["artifacts_sha256"] = {
            name: f"hash-{index}-{position}"
            for position, name in enumerate((
                "sumo/demand_meta.json",
                "sumo/candidates.meta.json",
                "sumo/assignment_priors.json",
                "sumo/prior_flows.json",
                "sumo/direction_split.json",
                "web/data/observability.json",
                "loso_report.json",
                "tag_report.json",
            ))
        }
    return run


def _manifest(runs=None, status="complete"):
    runs = list(runs if runs is not None else
                [_run(seed, index) for index, seed in enumerate(SEEDS)])
    return {
        "schema_version": 1,
        "kind": "corrected_loso_multi_seed_sweep",
        "status": status,
        "git_head": "0" * 40,
        "date": "2025-09-16",
        "seeds": [run["seed"] for run in runs],
        "inputs_sha256": {"traffic_sim/demand/pfe.py": "abc"},
        "runs": runs,
    }


def _tag_reports(manifest):
    reports = {}
    for run in manifest["runs"]:
        reports[run["seed"]] = {
            "criteria": {
                "either_flow_difference_or_geh": {
                    "share_pct": run["tag_satisfactory_share_pct"],
                    "cases_total": run["tag_total_cases"],
                },
            },
            "by_edge": [
                {"station": edge["station"], "edge": edge["evaluation"],
                 "share_pct": 60.0}
                for edge in run["edges"]
            ],
        }
    return reports


def test_complete_sweep_passes_the_gate():
    manifest = _manifest()
    assert check_gate(manifest, _tag_reports(manifest), []) == []


def test_incomplete_manifest_is_rejected():
    manifest = _manifest(status="running")
    failures = check_gate(manifest, _tag_reports(manifest), [])
    assert any("not 'complete'" in failure for failure in failures)


def test_differing_per_seed_protocols_are_rejected():
    runs = [_run(seed, index) for index, seed in enumerate(SEEDS)]
    runs[2]["protocol"] = "loso_pfe_meso_v4"
    manifest = _manifest(runs)
    failures = check_gate(manifest, _tag_reports(manifest), [])
    assert any("protocols differ" in failure for failure in failures)


def test_new_protocol_can_be_checked_without_relabelling_v5():
    runs = [_run(seed, index, protocol="loso_pfe_meso_v6")
            for index, seed in enumerate(SEEDS)]
    manifest = _manifest(runs)
    assert check_gate(
        manifest, _tag_reports(manifest), [],
        expected_protocol="loso_pfe_meso_v6") == []


def test_v7_uses_the_same_artifact_bound_freeze_contract():
    runs = [_run(seed, index, protocol="loso_pfe_meso_v7")
            for index, seed in enumerate(SEEDS)]
    manifest = _manifest(runs)
    assert check_gate(
        manifest, _tag_reports(manifest), [],
        expected_protocol="loso_pfe_meso_v7") == []


def test_v6_requires_complete_run_artifact_provenance():
    runs = [_run(seed, index, protocol="loso_pfe_meso_v6")
            for index, seed in enumerate(SEEDS)]
    del runs[0]["artifacts_sha256"]["sumo/prior_flows.json"]
    manifest = _manifest(runs)
    failures = check_gate(
        manifest, _tag_reports(manifest), [],
        expected_protocol="loso_pfe_meso_v6")
    assert any("complete artifact-bound run hash set" in failure
               for failure in failures)


def test_too_few_seeds_are_rejected():
    runs = [_run(seed, index) for index, seed in enumerate(SEEDS[:4])]
    manifest = _manifest(runs)
    failures = check_gate(manifest, _tag_reports(manifest), [])
    assert any("fewer than the required 6" in failure for failure in failures)


def test_repeated_candidate_pool_is_rejected():
    runs = [_run(seed, index) for index, seed in enumerate(SEEDS)]
    runs[3]["candidate_pool_sha256"] = runs[0]["candidate_pool_sha256"]
    manifest = _manifest(runs)
    failures = check_gate(manifest, _tag_reports(manifest), [])
    assert any("candidate-pool hashes repeat" in failure for failure in failures)


def test_held_edge_without_assignment_ceiling_is_rejected():
    runs = [_run(seed, index) for index, seed in enumerate(SEEDS)]
    runs[1]["edges"][0]["assignment_ceiling"] = False
    manifest = _manifest(runs)
    failures = check_gate(manifest, _tag_reports(manifest), [])
    assert any("missing an assignment ceiling" in failure for failure in failures)


def test_tag_report_disagreeing_with_manifest_is_rejected():
    manifest = _manifest()
    reports = _tag_reports(manifest)
    reports[42]["criteria"]["either_flow_difference_or_geh"]["share_pct"] = 99.9
    failures = check_gate(manifest, reports, [])
    assert any("TAG share disagrees" in failure for failure in failures)


def test_missing_tag_report_is_rejected():
    manifest = _manifest()
    reports = _tag_reports(manifest)
    del reports[20260809]
    failures = check_gate(manifest, reports, [])
    assert any("no TAG report" in failure for failure in failures)


def test_drifted_bound_inputs_are_rejected():
    manifest = _manifest()
    failures = check_gate(manifest, _tag_reports(manifest),
                          ["traffic_sim/demand/pfe.py"])
    assert any("no longer match the manifest" in failure for failure in failures)


def test_build_summary_refuses_a_failing_gate():
    manifest = _manifest(status="failed")
    with pytest.raises(ValueError):
        build_summary(manifest, Path("runs/x/manifest.json"), "sha",
                      _tag_reports(manifest), [], {})


def test_summary_aggregates_one_row_per_physical_station():
    manifest = _manifest()
    summary = build_summary(manifest, ROOT / "runs/x/manifest.json", "sha",
                            _tag_reports(manifest), [], {})
    assert [row["station"] for row in summary["stations"]] == [
        "107", "1074", "1076", "133", "134", "2276"]
    total = next(row for row in summary["stations"] if row["station"] == "107")
    # A two-way total is one observable location assembled from two edges.
    assert total["measurement_semantics"] == "two_way_total"
    assert total["evaluation_key"] == "station_total"
    assert len(total["component_edges"]) == 2
    assert len(total["daily_ratio_by_seed"]) == len(SEEDS)


def test_summary_verdict_is_derived_from_all_seed_results():
    runs = [_run(seed, index, share=90.0)
            for index, seed in enumerate(SEEDS)]
    manifest = _manifest(runs)
    summary = build_summary(
        manifest, ROOT / "runs/x/manifest.json", "sha",
        _tag_reports(manifest), [], {})
    assert summary["tag"]["seeds_meeting_guideline"] == len(SEEDS)
    assert summary["verdict"] == "pass"


def test_tracked_baseline_matches_its_recorded_source():
    """The checked-in baseline must stay internally consistent."""
    path = ROOT / "validation/corrected_loso_sweep_v2_summary.json"
    if not path.is_file():
        pytest.skip("baseline summary has not been frozen in this worktree")
    summary = json.loads(path.read_text())
    assert summary["kind"] == "corrected_loso_sweep_baseline"
    assert summary["status"] == "frozen"
    assert summary["protocol"]["name"] == EXPECTED_PROTOCOL
    assert len(summary["seeds"]) == len(set(summary["seeds"])) >= 6
    pools = summary["candidate_pool_sha256_by_seed"]
    assert set(pools) == {str(seed) for seed in summary["seeds"]}
    assert len(set(pools.values())) == len(pools)
    assert summary["assignment_ceiling_complete"] is True
    per_seed = summary["tag"]["per_seed"]
    assert [row["seed"] for row in per_seed] == summary["seeds"]
    assert summary["tag"]["seeds_meeting_guideline"] == sum(
        row["satisfactory_share_pct"] > summary["tag"]["tag_guideline_pct"]
        for row in per_seed)
    assert summary["verdict"] == ("pass" if summary["tag"][
        "seeds_meeting_guideline"] == len(per_seed) else "fail")
    assert summary["source"]["manifest_tracked_in_git"] is False
    assert summary["evidence_class"] == "diagnostic"
