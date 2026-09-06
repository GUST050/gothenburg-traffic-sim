from tools import benchmark_speed


def test_canonical_digest_ignores_only_runtime_fields():
    base = {"generated_at": "old", "flows": {"a": [1, 2]},
            "scenario": {"name": "baseline"}}
    changed_timestamp = {**base, "generated_at": "new"}
    changed_flow = {"generated_at": "new", "flows": {"a": [1, 3]},
                    "scenario": {"name": "baseline"}}
    assert benchmark_speed.canonical_digest(base) == \
        benchmark_speed.canonical_digest(changed_timestamp)
    assert benchmark_speed.canonical_digest(base) != \
        benchmark_speed.canonical_digest(changed_flow)


# ── Campaign freeze (LUNA-PERF-04) ───────────────────────────────────────────
# A frozen campaign only means something if the harness runs exactly what was
# frozen and refuses everything else. These tests are non-SUMO: no case is
# ever executed, and the preflight path is asserted to create nothing.
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The seed-parallel phase-profile campaign line is CLOSED (LUNA-PERF-17): the
# production harness has CURRENT_CAMPAIGN_ID = None and no real contract loads.
# v1 (aborted on the import defect), v2 (consumed), v3 (spent single-worker),
# v4/v5/v6 (spent paired, all non-adoptable) are retired identities the loader
# refuses by name. Nothing here opens any retired report or run-tree content.
#
# The loader/preflight/adoption MACHINERY must still work for a future line, so
# the tests below exercise it against a SYNTHETIC "current" campaign — a
# byte-copy of the v6 contract under a test identity, with fingerprints
# recomputed to the live files — made current by the autouse fixture. The real
# v6 contract is exercised separately, as a retired identity, via non-loader
# helpers.
CAMPAIGN_V1 = Path("validation/scenario_phase_profile_campaign_v1.json")
CAMPAIGN_V2 = Path("validation/scenario_phase_profile_campaign_v2.json")
CAMPAIGN_V3 = Path("validation/scenario_phase_profile_campaign_v3.json")
CAMPAIGN_V4 = Path("validation/scenario_phase_profile_campaign_v4.json")
CAMPAIGN_V5 = Path("validation/scenario_phase_profile_campaign_v5.json")
CAMPAIGN_V6 = Path("validation/scenario_phase_profile_campaign_v6.json")
RETIRED = ((CAMPAIGN_V1, "scenario_phase_profile_v1"),
           (CAMPAIGN_V2, "scenario_phase_profile_v2"),
           (CAMPAIGN_V3, "scenario_phase_profile_v3"),
           (CAMPAIGN_V4, "scenario_phase_profile_v4"),
           (CAMPAIGN_V5, "scenario_phase_profile_v5"),
           (CAMPAIGN_V6, "scenario_phase_profile_v6"))

TEST_CAMPAIGN_ID = "scenario_phase_profile_test"

# The harness resolves every input it fingerprints, and the live demand it
# verifies a contract against, through its module-level ROOT. Pointing that at
# the developer's real tree coupled this module to a git-ignored build artifact
# twice over, and both couplings fired:
#
#   * `sumo/demand_meta.json` does not exist on CI, so reading it at IMPORT
#     time raised FileNotFoundError during collection. pytest aborts the whole
#     run on a collection error, so from 2026-08-26 no test in this suite ran
#     in CI at all — the failure cost the entire suite, not one module.
#   * Where the file did exist, its window was spliced into a contract whose
#     `cases[*].closure_window` stays frozen at a whole day. After
#     `make demand-morning` (16 intervals = 14 400 s) that contradicted the
#     frozen 86 400 s and the loader refused the campaign, so the project's own
#     documented fast-iteration target turned 63 tests red.
#
# The module is hermetic instead: a throwaway ROOT holding exactly the
# artifacts REQUIRED_FINGERPRINT_LABELS names, whose demand_meta is generated
# FROM the frozen contract's own declared window and identity. The loader
# therefore checks the contract against the demand the contract itself
# declares. No frozen byte is read for anything but its declared values, none
# is written, and the result no longer depends on what was last built locally.
def _build_hermetic_root(contract: dict) -> Path:
    """A self-contained ROOT that satisfies the harness's input verification."""
    root = Path(tempfile.mkdtemp(prefix="pp_root_"))
    (root / "sumo").mkdir()
    # The live-demand check compares every demand_window and demand_identity
    # field against this file, so it is built from those very fields.
    (root / "sumo" / "demand_meta.json").write_text(json.dumps(
        {**contract["demand_window"], **contract["demand_identity"]},
        sort_keys=True))
    # Content is irrelevant and deliberately constant: the campaign freezes
    # these digests and then verifies them against the same files, so what is
    # under test is the binding, not the bytes.
    for name in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                 "calibrated_v2.rou.xml", "net.net.xml"):
        (root / "sumo" / name).write_text(f"<!-- hermetic {name} -->\n")
    (root / "run_scenario.py").write_text("# hermetic stand-in\n")
    # Provenance is collected by running git with cwd=ROOT, and the report gate
    # requires git_dirty to be a real bool — an unreadable repository is
    # "unknown", never "clean", which is the correct production behaviour and
    # is left untouched. Giving the fixture its own committed repository keeps
    # `_command_output` genuinely exercised (the tests that pin its
    # failure-vs-clean distinction patch subprocess.run themselves and must
    # keep seeing the real helper) while making git_dirty deterministically
    # False. Previously the recorded provenance changed depending on whether
    # the developer happened to have uncommitted edits.
    identity = ("-c", "user.email=hermetic@example.invalid",
                "-c", "user.name=hermetic", "-c", "commit.gpgsign=false")
    for command in (("git", "init", "-q"),
                    ("git", *identity, "add", "-A"),
                    ("git", *identity, "commit", "-q", "-m", "hermetic fixture",
                     "--no-gpg-sign")):
        # Never raise here: this runs at import, and an exception at import is
        # a COLLECTION error, which aborts the entire pytest run rather than
        # this module. A missing git fails the few provenance tests loudly
        # instead, which is a proportionate failure.
        subprocess.run(command, cwd=root, capture_output=True, check=False)
    return root


# A synthetic loadable "current" campaign, generated once for this module: the
# v6 contract under a test identity, with fingerprints taken from the hermetic
# ROOT so it verifies against it. `CAMPAIGN` points at it; the autouse fixture
# marks TEST_CAMPAIGN_ID current so `load_campaign(CAMPAIGN)` succeeds.
def _build_synthetic_current(contract: dict) -> Path:
    contract = json.loads(json.dumps(contract))   # never mutate the caller's
    contract["campaign_id"] = TEST_CAMPAIGN_ID
    # demand_window and demand_identity stay exactly as frozen — the hermetic
    # demand_meta was generated from them.
    inputs = dict(benchmark_speed.file_fingerprints())
    inputs["harness:benchmark_speed.py"] = benchmark_speed.sha256_file(
        benchmark_speed.HARNESS)
    contract["frozen_fingerprints"] = {
        label: inputs[label]
        for label in benchmark_speed.REQUIRED_FINGERPRINT_LABELS}
    contract["content_key"] = benchmark_speed.campaign_content_key(contract)
    path = Path(tempfile.mkdtemp(prefix="pp_current_")) / "current_campaign.json"
    path.write_text(json.dumps(contract))
    return path


_V6_CONTRACT = json.loads(CAMPAIGN_V6.read_text())
HERMETIC_ROOT = _build_hermetic_root(_V6_CONTRACT)

# tests/test_scenario_timing.py imports the same module object, so the patch is
# applied only for the duration of this build and re-applied per test by the
# autouse fixture below, which undoes it again afterwards.
_REAL_ROOT = benchmark_speed.ROOT
benchmark_speed.ROOT = HERMETIC_ROOT
try:
    CAMPAIGN = _build_synthetic_current(_V6_CONTRACT)
finally:
    benchmark_speed.ROOT = _REAL_ROOT


@pytest.fixture(autouse=True)
def _seed_parallel_line_current(monkeypatch):
    """Mark the synthetic `CAMPAIGN` current so the loader machinery is testable
    even though production's CURRENT_CAMPAIGN_ID is None. Retirement/closure
    tests that need the real None behavior reset it inside the test.

    ROOT is repointed at the hermetic tree for the same reason `CAMPAIGN` was
    built against it: input verification must resolve to the fixture, never to
    whatever the developer last built."""
    monkeypatch.setattr(benchmark_speed, "ROOT", HERMETIC_ROOT)
    monkeypatch.setattr(benchmark_speed, "CURRENT_CAMPAIGN_ID", TEST_CAMPAIGN_ID)


def _campaign(**overrides):
    campaign = json.loads(CAMPAIGN.read_text())
    campaign.update(overrides)
    if "content_key" not in overrides:
        campaign["content_key"] = benchmark_speed.campaign_content_key(campaign)
    return campaign


def _write(tmp_path, campaign) -> Path:
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(campaign))
    return path


def _fake_sha(text: str) -> str:
    """A deterministic 64-hex digest for synthetic report rows."""
    return hashlib.sha256(text.encode()).hexdigest()


class TestFrozenCampaignMatchesItsExecutableMatrix:

    def test_the_tracked_campaign_loads_and_recomputes(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        assert campaign["campaign_id"] == TEST_CAMPAIGN_ID
        assert (benchmark_speed.campaign_content_key(campaign)
                == campaign["content_key"])

    def test_the_matrix_is_two_cases_times_two_arms_times_five_trials(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        matrix = benchmark_speed.campaign_matrix(campaign)
        assert len(matrix) == 20
        assert {row["campaign_case"] for row in matrix} == {
            "baseline_whole_day", "closure_whole_window"}
        counts = Counter((row["campaign_case"], row["workers"])
                         for row in matrix)
        assert counts == {("baseline_whole_day", 1): 5,
                          ("baseline_whole_day", 3): 5,
                          ("closure_whole_window", 1): 5,
                          ("closure_whole_window", 3): 5}
        for row in matrix:
            assert row["workers"] in (1, 3) and row["seeds"] == 3
            assert row["seed_set"] == [1000, 1001, 1002]
            assert row["demand_variant_mapping"] == {
                "1000": "q50", "1001": "q10", "1002": "q90"}
            assert row["micro"] is False and row["timeout"] == 1800
        # Every paired row differs from its partner ONLY in worker count.
        for case in {row["campaign_case"] for row in matrix}:
            paired = [row for row in matrix if row["campaign_case"] == case]
            keys = {tuple(sorted((k, json.dumps(v, sort_keys=True))
                                 for k, v in row.items() if k != "workers"))
                    for row in paired}
            assert len(keys) == 5                    # five trials, two arms each
        closure = [row for row in matrix
                   if row["campaign_case"] == "closure_whole_window"]
        assert all(row["closed_edges"] == ["26842525_26355153_0"]
                   for row in closure)

    def test_the_frozen_case_kind_matches_the_harness_closure_constant(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        closure = next(case for case in campaign["cases"]
                       if case["case"] == "closure")
        assert closure["closed_edges"] == [benchmark_speed.KNOWN_CLOSURE]
        assert closure["scenario_name"] == "close_" + benchmark_speed.KNOWN_CLOSURE

    def test_no_microscopic_case_is_frozen(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        assert {case["case"] for case in campaign["cases"]} == {
            "baseline", "closure"}
        assert campaign["execution"]["simulation_mode"] == "meso"

    def test_the_contract_states_it_is_not_a_speed_claim(self):
        campaign = json.loads(CAMPAIGN.read_text())
        assert campaign["evidence_class"] == "diagnostic performance evidence only"
        assert any("speed-up" in item for item in campaign["not_evidence_for"])


class TestPreflightRunsNothing:

    def test_preflight_through_the_cli_creates_no_artifact_directory(
            self, tmp_path, monkeypatch, capsys):
        def explode(*args, **kwargs):                      # pragma: no cover
            raise AssertionError("preflight must not spawn a subprocess")

        monkeypatch.setattr(benchmark_speed, "run_case", explode)
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--preflight-only", "--artifact-dir", str(tmp_path / "artifacts")])
        assert benchmark_speed.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["executed"] is False
        assert payload["artifact_dir"] is None
        assert payload["runs_planned"] == 20
        assert not (tmp_path / "artifacts").exists()

    def test_preflight_requires_a_campaign(self, monkeypatch):
        monkeypatch.setattr(sys, "argv",
                            ["benchmark_speed.py", "--preflight-only"])
        with pytest.raises(SystemExit):
            benchmark_speed.main()


class TestMalformedOrStaleCampaignsFailBeforeExecution:

    def test_an_edited_contract_fails_the_content_key(self, tmp_path):
        campaign = json.loads(CAMPAIGN.read_text())
        campaign["execution"]["trials"] = 9          # key not recomputed
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="content key"):
            benchmark_speed.load_campaign(_write(tmp_path, campaign))

    @pytest.mark.parametrize("execution,match", [
        ({"simulation_mode": "micro"}, "mesoscopic"),
        ({"cache_substitution": True}, "cache substitution"),
        ({"warmup_trials": 2}, "fresh trials"),
        ({"worker_arms": [1, 0]}, "unique integers >= 1"),
        ({"worker_arms": [1, 1]}, "unique integers >= 1"),
        ({"worker_arms": [3, 1]}, "must start with the serial arm"),
        ({"worker_arms": []}, "unique integers >= 1"),
        ({"seeds": [1000, 1000, 1002]}, "unique integers"),
        ({"demand_variant_mapping": {"1000": "q50"}}, "mapping is incomplete"),
        ({"demand_variant_mapping": {"1000": "q50", "1001": "q10",
                                     "1002": "invented"}}, "invalid"),
    ])
    def test_execution_drift_is_refused(self, tmp_path, execution, match):
        campaign = json.loads(CAMPAIGN.read_text())
        campaign["execution"] = {**campaign["execution"], **execution}
        with pytest.raises(benchmark_speed.CampaignRefused, match=match):
            benchmark_speed.load_campaign(_write(tmp_path, _campaign(
                execution=campaign["execution"])))

    def test_case_drift_is_refused(self, tmp_path):
        campaign = json.loads(CAMPAIGN.read_text())
        cases = [dict(case) for case in campaign["cases"]]
        cases[0]["closed_edges"] = ["some_other_edge_0"]
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="baseline closes nothing"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=cases)))
        renamed = [dict(case) for case in campaign["cases"]]
        renamed[1]["scenario_name"] = "close_something_else"
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="scenario_name"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=renamed)))

    def test_a_non_campaign_document_is_refused(self, tmp_path):
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="not a phase-profile campaign"):
            benchmark_speed.load_campaign(_write(tmp_path, {
                "schema_version": 1, "kind": "something_else"}))

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(benchmark_speed.CampaignRefused, match="cannot read"):
            benchmark_speed.load_campaign(tmp_path / "absent.json")

    def test_drifted_input_fingerprints_are_refused(self, monkeypatch):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        drifted = {**benchmark_speed.file_fingerprints(),
                   "network": "f" * 64}
        monkeypatch.setattr(benchmark_speed, "file_fingerprints",
                            lambda: drifted)
        with pytest.raises(benchmark_speed.CampaignRefused, match="drifted"):
            benchmark_speed.verify_campaign_inputs(campaign)

    def test_a_stale_demand_window_is_refused(self, tmp_path):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        campaign = dict(campaign)
        campaign["demand_window"] = {**campaign["demand_window"],
                                     "date": "2027-09-15"}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="not the frozen campaign demand"):
            benchmark_speed.verify_campaign_inputs(campaign)

    def test_the_live_inputs_currently_satisfy_the_frozen_campaign(self):
        """The tracked contract must be executable as frozen, today."""
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        verified = benchmark_speed.verify_campaign_inputs(campaign)
        assert verified["inputs"]["harness:benchmark_speed.py"] == (
            campaign["frozen_fingerprints"]["harness:benchmark_speed.py"])
        assert verified["demand_identity"] == campaign["demand_identity"]


class TestCampaignCannotTouchTheSemanticComparison:

    def test_a_campaign_cannot_be_overridden_from_the_command_line(self,
                                                                   monkeypatch):
        for flag, value in (("--trials", "9"), ("--workers", "4"),
                            ("--seeds", "1"), ("--timeout", "60"),
                            ("--case", "micro"),
                            ("--rerouting-threads", "2"),
                            ("--routing-algorithm", "astar"),
                            ("--minimal-edgedata", None),
                            ("--full-edgedata", None)):
            monkeypatch.setattr(sys, "argv", [
                "benchmark_speed.py", "--campaign", str(CAMPAIGN), flag]
                + ([value] if value is not None else []))
            with pytest.raises(SystemExit):
                benchmark_speed.parse_args()

    def test_the_ad_hoc_cli_keeps_its_defaults(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["benchmark_speed.py"])
        args = benchmark_speed.parse_args()
        assert args.case == ["baseline", "closure", "micro"]
        assert args.workers == [1, 2] and args.seeds == 3
        assert args.trials == 1 and args.timeout == 1800
        assert args.campaign is None and args.preflight_only is False

    def test_ad_hoc_rerouting_threads_must_be_positive(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--rerouting-threads", "0",
        ])
        with pytest.raises(SystemExit):
            benchmark_speed.parse_args()

    def test_edge_data_modes_are_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--minimal-edgedata", "--full-edgedata",
        ])
        with pytest.raises(SystemExit):
            benchmark_speed.parse_args()

    def test_ad_hoc_routing_options_reach_the_case_boundary(
            self, monkeypatch, tmp_path, capsys):
        seen = []

        def fake_case(case, workers, seeds, micro, timeout, root, **kwargs):
            seen.append(kwargs)
            return {
                "case": case, "workers": workers, "seeds": seeds,
                "micro": micro, "command": ["stub"], "returncode": 0,
                "wall_s": 1.0, "peak_child_rss_bytes": 0,
                "scenario_bytes": 1, "trajectory_bytes": 1,
                "scenario_digest": "s" * 64,
                "trajectory_digest": "t" * 64,
                "phase_profile": None, "closure_integrity": None,
                "seed_health": None, "log": str(root),
            }

        monkeypatch.setattr(benchmark_speed, "run_case", fake_case)
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO test")
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--case", "closure", "--workers", "1",
            "--trials", "1", "--rerouting-threads", "2",
            "--routing-algorithm", "astar", "--artifact-dir",
            str(tmp_path / "artifacts"),
        ])

        assert benchmark_speed.main() == 0
        capsys.readouterr()
        assert seen == [{
            "rerouting_threads": 2,
            "routing_algorithm": "astar",
        }]

    def test_ad_hoc_minimal_edgedata_reaches_the_case_boundary(
            self, monkeypatch, tmp_path, capsys):
        seen = []

        def fake_case(case, workers, seeds, micro, timeout, root, **kwargs):
            seen.append(kwargs)
            return {
                "case": case, "workers": workers, "seeds": seeds,
                "micro": micro, "command": ["stub"], "returncode": 0,
                "wall_s": 1.0, "peak_child_rss_bytes": 0,
                "scenario_bytes": 1, "trajectory_bytes": 1,
                "scenario_digest": "s" * 64,
                "trajectory_digest": "t" * 64,
                "phase_profile": None, "closure_integrity": None,
                "seed_health": None, "log": str(root),
            }

        monkeypatch.setattr(benchmark_speed, "run_case", fake_case)
        monkeypatch.setattr(benchmark_speed, "sumo_version", lambda: "SUMO test")
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--case", "closure", "--workers", "3",
            "--trials", "1", "--minimal-edgedata", "--artifact-dir",
            str(tmp_path / "artifacts"),
        ])

        assert benchmark_speed.main() == 0
        capsys.readouterr()
        assert seen == [{"minimal_edgedata": True}]

    def test_ad_hoc_full_edgedata_reaches_the_case_boundary(
            self, monkeypatch, tmp_path, capsys):
        seen = []

        def fake_case(case, workers, seeds, micro, timeout, root, **kwargs):
            seen.append(kwargs)
            return {
                "case": case, "workers": workers, "seeds": seeds,
                "micro": micro, "command": ["stub"], "returncode": 0,
                "wall_s": 1.0, "peak_child_rss_bytes": 0,
                "scenario_bytes": 1, "trajectory_bytes": 1,
                "scenario_digest": "s" * 64,
                "trajectory_digest": "t" * 64,
                "phase_profile": None, "closure_integrity": None,
                "seed_health": None, "log": str(root),
            }

        monkeypatch.setattr(benchmark_speed, "run_case", fake_case)
        monkeypatch.setattr(benchmark_speed, "sumo_version", lambda: "SUMO test")
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--case", "closure", "--workers", "3",
            "--trials", "1", "--full-edgedata", "--artifact-dir",
            str(tmp_path / "artifacts"),
        ])

        assert benchmark_speed.main() == 0
        capsys.readouterr()
        assert seen == [{"full_edgedata": True}]

    def test_campaign_metadata_is_not_part_of_any_digest(self):
        payload = {"generated_at": "t", "flows": {"a_b_0": [1, 2]}}
        campaign_row = {"campaign_case": "baseline_whole_day", "trial": 3}
        # The digest is computed from the scenario payload alone; campaign
        # metadata lives on the report row beside it.
        assert (benchmark_speed.canonical_digest(payload)
                == benchmark_speed.canonical_digest(dict(payload)))
        assert "campaign_case" not in payload
        assert set(campaign_row) & set(payload) == set()

    def test_semantic_mismatch_detection_still_uses_only_the_digests(self):
        source = Path("tools/benchmark_speed.py").read_text()
        comparison = source[source.index("    mismatches = []"):]
        assert comparison.count('"scenario_digest", "trajectory_digest"') == 2
        for field in ("campaign", "phase_profile", "wall_s"):
            assert f'"{field}"' not in comparison.split("reference_mismatches = []")[0]


# ── Executable binding (LUNA-PERF-04 fix) ────────────────────────────────────
# A campaign field that the production call path ignores is decorative: the
# run would proceed with the old canonical seeds and the hard-coded edge while
# the contract claimed otherwise. Every mutation below RECOMPUTES the content
# key, so passing it would mean the declaration and the execution had genuinely
# diverged — and each is proven to be refused before any artifact directory,
# subprocess or case call.

class TestDeclarationsAreBoundToExecutableBehaviour:

    @pytest.mark.parametrize("execution,match", [
        ({"seeds": [2000, 2001, 2002],
          "demand_variant_mapping": {"2000": "q50", "2001": "q10",
                                     "2002": "q90"}}, "execution.seeds"),
        ({"seeds": [1002, 1001, 1000]}, "execution.seeds"),      # order matters
        ({"demand_variant_mapping": {"1000": "q10", "1001": "q50",
                                     "1002": "q90"}},
         "execution.demand_variant_mapping"),
        ({"trials": 3}, "execution.trials"),
        ({"worker_arms": [1, 2]}, "execution.worker_arms"),
        ({"worker_arms": [1]}, "execution.worker_arms"),
        ({"worker_arms": [1, 3, 5]}, "execution.worker_arms"),
        ({"timeout_seconds": 600}, "execution.timeout_seconds"),
    ])
    def test_execution_that_the_runner_cannot_honour_is_refused(
            self, tmp_path, execution, match):
        campaign = _campaign(execution={**json.loads(CAMPAIGN.read_text())
                                        ["execution"], **execution})
        with pytest.raises(benchmark_speed.CampaignRefused, match=match):
            benchmark_speed.load_campaign(_write(tmp_path, campaign))

    def test_another_closure_edge_is_refused_even_when_self_consistent(
            self, tmp_path):
        """The runner always closes KNOWN_CLOSURE; a contract naming another
        edge would produce timings for a closure that never happened."""
        cases = [dict(case) for case in json.loads(CAMPAIGN.read_text())["cases"]]
        cases[1] = {**cases[1], "closed_edges": ["not_the_frozen_edge_0"],
                    "scenario_name": "close_not_the_frozen_edge_0"}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="closed_edges"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=cases)))

    def test_reordered_or_extra_cases_are_refused(self, tmp_path):
        original = json.loads(CAMPAIGN.read_text())["cases"]
        with pytest.raises(benchmark_speed.CampaignRefused, match="name"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=list(reversed(original)))))
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="executes exactly"):
            benchmark_speed.load_campaign(_write(tmp_path, _campaign(
                cases=original + [{**original[0], "name": "third"}])))

    @pytest.mark.parametrize("window,match", [
        ({"kind": "partial", "start_offset_s": 0, "end_offset_s": 86400},
         "closure window"),
        ({"kind": "whole_simulated_window", "start_offset_s": 3600,
          "end_offset_s": 86400}, "closure window"),
        ({"kind": "whole_simulated_window", "start_offset_s": 0,
          "end_offset_s": 43200}, "closure window"),
    ])
    def test_a_closure_window_the_runner_does_not_execute_is_refused(
            self, tmp_path, window, match):
        cases = [dict(case) for case in json.loads(CAMPAIGN.read_text())["cases"]]
        cases[1] = {**cases[1], "closure_window": window}
        with pytest.raises(benchmark_speed.CampaignRefused, match=match):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=cases)))

    def test_a_missing_closure_window_is_refused(self, tmp_path):
        cases = [dict(case) for case in json.loads(CAMPAIGN.read_text())["cases"]]
        cases[1].pop("closure_window")
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="machine-readable closure window"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(cases=cases)))

    def test_the_window_identity_reaches_the_executable_matrix(self):
        matrix = benchmark_speed.campaign_matrix(
            benchmark_speed.load_campaign(CAMPAIGN))
        closure = [row for row in matrix
                   if row["campaign_case"] == "closure_whole_window"]
        assert all(row["closure_window"] == {
            "kind": benchmark_speed.WHOLE_WINDOW, "start_offset_s": 0,
            "end_offset_s": 86400} for row in closure)
        assert all(row["seed_set"] == [1000, 1001, 1002] for row in matrix)
        assert all(row["demand_variant_mapping"]["1000"] == "q50"
                   for row in matrix)

    def test_no_mutated_campaign_reaches_a_run(self, tmp_path, monkeypatch):
        """Refusal happens before any artifact directory or subprocess."""
        def explode(*args, **kwargs):                       # pragma: no cover
            raise AssertionError("a refused campaign must not execute")

        monkeypatch.setattr(benchmark_speed, "run_case", explode)
        monkeypatch.setattr(benchmark_speed.subprocess, "run", explode)
        cases = [dict(case) for case in json.loads(CAMPAIGN.read_text())["cases"]]
        cases[1] = {**cases[1], "closed_edges": ["not_the_frozen_edge_0"],
                    "scenario_name": "close_not_the_frozen_edge_0"}
        path = _write(tmp_path, _campaign(cases=cases))
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(path),
            "--artifact-dir", str(artifacts)])
        assert benchmark_speed.main() == 2
        assert not artifacts.exists()


class TestDeclaredDemandIdentityIsVerified:

    @pytest.mark.parametrize("identity,match", [
        ({"demand_build_key": "not_the_live_key"}, "demand_build_key"),
        ({"build_id": "not_the_live_build"}, "build_id"),
        ({"n_variants": 999}, "n_variants"),
    ])
    def test_a_false_demand_identity_is_refused(self, identity, match):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        campaign = {**campaign,
                    "demand_identity": {**campaign["demand_identity"],
                                        **identity}}
        with pytest.raises(benchmark_speed.CampaignRefused, match=match):
            benchmark_speed.verify_campaign_inputs(campaign)

    def test_an_incomplete_demand_identity_is_refused(self, tmp_path):
        identity = dict(json.loads(CAMPAIGN.read_text())["demand_identity"])
        identity.pop("n_variants")
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="demand_identity must declare exactly"):
            benchmark_speed.load_campaign(
                _write(tmp_path, _campaign(demand_identity=identity)))

    def test_the_verified_identity_reaches_the_preflight(self, monkeypatch,
                                                         capsys):
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--preflight-only"])
        assert benchmark_speed.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["demand_identity_verified"] == json.loads(
            CAMPAIGN.read_text())["demand_identity"]


class TestCampaignReportProvenanceFailsClosed:

    def _report(self, campaign, **overrides):
        report = {
            "platform": "macOS-15", "cpu_count": 10, "python": "3.9.6",
            "sumo_version": "SUMO 1.27.1", "git_commit": "a" * 40,
            "git_dirty": False,
            "inputs": dict(campaign["frozen_fingerprints"]),
            "campaign": {
                "campaign_id": campaign["campaign_id"],
                "content_key": campaign["content_key"],
                "executable_matrix": benchmark_speed.campaign_matrix(campaign),
                "demand_identity": campaign["demand_identity"],
            },
        }
        report.update(overrides)
        return report

    def test_a_complete_report_is_accepted(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        benchmark_speed.validate_campaign_report(self._report(campaign),
                                                 campaign)

    @pytest.mark.parametrize("field", ["platform", "cpu_count", "python",
                                       "sumo_version", "git_commit"])
    def test_missing_hardware_or_environment_provenance_is_refused(self, field):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="missing required provenance"):
            benchmark_speed.validate_campaign_report(
                self._report(campaign, **{field: None}), campaign)

    def test_a_non_boolean_git_state_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        with pytest.raises(benchmark_speed.CampaignRefused, match="git_dirty"):
            benchmark_speed.validate_campaign_report(
                self._report(campaign, git_dirty="maybe"), campaign)

    @pytest.mark.parametrize("field", ["campaign_id", "content_key",
                                       "executable_matrix", "demand_identity"])
    def test_a_report_missing_campaign_binding_is_refused(self, field):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["campaign"] = {k: v for k, v in report["campaign"].items()
                              if k != field}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match=f"campaign.{field}"):
            benchmark_speed.validate_campaign_report(report, campaign)

    def test_a_report_bound_to_another_campaign_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["campaign"] = {**report["campaign"], "content_key": "f" * 64}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="identity does not match"):
            benchmark_speed.validate_campaign_report(report, campaign)

    def test_a_report_with_drifted_input_provenance_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["inputs"] = {**report["inputs"], "network": "f" * 64}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="input provenance"):
            benchmark_speed.validate_campaign_report(report, campaign)

    def test_provenance_is_checked_before_any_case_runs(self, tmp_path,
                                                        monkeypatch):
        """A report that could not be attributed must not cost hours first."""
        def explode(*args, **kwargs):                       # pragma: no cover
            raise AssertionError("provenance must fail before execution")

        monkeypatch.setattr(benchmark_speed, "run_case", explode)
        monkeypatch.setattr(benchmark_speed, "sumo_version", lambda: None)
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(artifacts)])
        assert benchmark_speed.main() == 2


# ── Provenance order, exact bindings, pinned fingerprints (second fix) ───────

class TestProvenanceReachesTheCaseBoundary:
    """The first fix validated provenance before git identity was collected,
    so every real campaign was refused on a valid checkout."""

    def _stub_case(self, calls):
        def run_case(case, workers, seeds, micro, timeout, root):
            calls.append({"case": case, "workers": workers, "seeds": seeds,
                          "micro": micro, "timeout": timeout})
            # A complete, adoptable result: paired arms share digests, the
            # parallel arm (workers 3) is well under the latency ceiling and
            # beats the serial arm by more than the frozen floor, and every
            # health/closure field is present. This lets the provenance path
            # reach a clean exit through the now-bound adoption evaluator.
            return {"case": case, "workers": workers, "seeds": seeds,
                    "micro": micro, "command": ["stub"], "returncode": 0,
                    "wall_s": 8.0 if workers == 1 else 3.0,
                    "peak_child_rss_bytes": 0,
                    "scenario_bytes": 1, "trajectory_bytes": 0,
                    "scenario_digest": _fake_sha(case + "scenario"),
                    "trajectory_digest": _fake_sha(case + "traj"),
                    "phase_profile": {"total": 8.0 if workers == 1 else 3.0},
                    "closure_integrity": ("verified_clean"
                                          if case == "closure" else None),
                    "seed_health": [{"seed": s, "collisions": 0, "teleports": 0,
                                     "running_at_end": 0, "waiting_at_end": 0,
                                     "loaded": 10, "inserted": 10}
                                    for s in (1000, 1001, 1002)],
                    "log": str(root)}
        return run_case

    def test_a_valid_checkout_reaches_execution_with_full_provenance(
            self, tmp_path, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(benchmark_speed, "run_case", self._stub_case(calls))
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO 1.27.1")
        written = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(tmp_path / "artifacts"),
            "--write", str(written)])

        assert benchmark_speed.main() == 0
        capsys.readouterr()
        assert len(calls) == 20                     # the frozen matrix, no SUMO
        assert {call["case"] for call in calls} == {"baseline", "closure"}
        assert all(call["seeds"] == 3 and call["micro"] is False
                   and call["timeout"] == 1800 for call in calls)
        assert Counter((call["case"], call["workers"]) for call in calls) == {
            ("baseline", 1): 5, ("baseline", 3): 5,
            ("closure", 1): 5, ("closure", 3): 5}
        report = json.loads(written.read_text())
        assert report["git_commit"] and report["git_dirty"] in (True, False)
        assert report["campaign"]["content_key"] == json.loads(
            CAMPAIGN.read_text())["content_key"]
        # The written report must itself satisfy the publication gate.
        benchmark_speed.validate_campaign_report(
            report, benchmark_speed.load_campaign(CAMPAIGN))

    def test_a_parallel_arm_that_changes_the_result_makes_main_return_nonzero(
            self, tmp_path, monkeypatch, capsys):
        """The whole point of pairing: if worker=3 produces a different scenario
        or trajectory than worker=1 in the same case/trial, the run must report
        it and exit non-zero so no faster-but-different result is adopted."""
        def run_case(case, workers, seeds, micro, timeout, root):
            # Serial and parallel agree everywhere EXCEPT one closure trajectory.
            traj = "t" * 64
            if case == "closure" and workers == 3:
                traj = "DIVERGED" + "t" * 56
            return {"case": case, "workers": workers, "seeds": seeds,
                    "micro": micro, "command": ["stub"], "returncode": 0,
                    "wall_s": 1.0, "peak_child_rss_bytes": 0,
                    "scenario_bytes": 1, "trajectory_bytes": 0,
                    "scenario_digest": "s" * 64, "trajectory_digest": traj,
                    "phase_profile": None, "seed_health": None,
                    "closure_integrity": None, "log": str(root)}

        monkeypatch.setattr(benchmark_speed, "run_case", run_case)
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO 1.27.1")
        written = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(tmp_path / "artifacts"),
            "--write", str(written)])

        assert benchmark_speed.main() == 2                  # not adopted
        capsys.readouterr()
        report = json.loads(written.read_text())
        mismatches = report["semantic_mismatches"]
        assert mismatches and all(
            m["case"] == "closure" and m["workers"] == 3
            and m["field"] == "trajectory_digest" for m in mismatches)

    def test_a_checkout_without_git_identity_is_refused_before_any_case(
            self, tmp_path, monkeypatch):
        def explode(*args, **kwargs):                       # pragma: no cover
            raise AssertionError("must not run without git provenance")

        monkeypatch.setattr(benchmark_speed, "run_case", explode)
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO 1.27.1")

        real_run = benchmark_speed.subprocess.run

        def no_git(command, *args, **kwargs):
            if command and command[0] == "git":
                raise OSError("git is not available")
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(benchmark_speed.subprocess, "run", no_git)
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(artifacts)])
        assert benchmark_speed.main() == 2
        assert not artifacts.exists()


class TestReportBindingsAreComparedExactly:

    def _report(self, campaign, **overrides):
        report = {
            "platform": "macOS-15", "cpu_count": 10, "python": "3.9.6",
            "sumo_version": "SUMO 1.27.1", "git_commit": "a" * 40,
            "git_dirty": False,
            "inputs": dict(campaign["frozen_fingerprints"]),
            "campaign": {
                "campaign_id": campaign["campaign_id"],
                "content_key": campaign["content_key"],
                "executable_matrix": benchmark_speed.campaign_matrix(campaign),
                "demand_identity": campaign["demand_identity"],
            },
        }
        report.update(overrides)
        return report

    def test_a_fabricated_matrix_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["campaign"] = {**report["campaign"],
                              "executable_matrix": [{"wrong": True}]}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="executable matrix"):
            benchmark_speed.validate_campaign_report(report, campaign)

    def test_a_truncated_matrix_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["campaign"] = {
            **report["campaign"],
            "executable_matrix": benchmark_speed.campaign_matrix(campaign)[:9]}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="executable matrix"):
            benchmark_speed.validate_campaign_report(report, campaign)

    def test_a_fabricated_demand_identity_is_refused(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report(campaign)
        report["campaign"] = {**report["campaign"],
                              "demand_identity": {"wrong": True}}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="demand identity"):
            benchmark_speed.validate_campaign_report(report, campaign)

    @pytest.mark.parametrize("field,value", [
        ("platform", "   "), ("cpu_count", 0), ("cpu_count", "10"),
        ("cpu_count", True), ("python", 3.9), ("sumo_version", 127),
        ("git_commit", "abc123"), ("git_commit", "A" * 40),
        ("git_dirty", "false"),
    ])
    def test_invalid_provenance_types_are_refused(self, field, value):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="missing required provenance|invalid"):
            benchmark_speed.validate_campaign_report(
                self._report(campaign, **{field: value}), campaign)


class TestFrozenFingerprintSetIsPinned:

    def test_a_dropped_fingerprint_is_refused(self, tmp_path):
        fingerprints = dict(json.loads(CAMPAIGN.read_text())
                            ["frozen_fingerprints"])
        fingerprints.pop("harness:benchmark_speed.py")
        with pytest.raises(benchmark_speed.CampaignRefused, match="missing"):
            benchmark_speed.load_campaign(_write(tmp_path, _campaign(
                frozen_fingerprints=fingerprints)))

    def test_an_extra_fingerprint_is_refused(self, tmp_path):
        fingerprints = {**json.loads(CAMPAIGN.read_text())
                        ["frozen_fingerprints"], "invented": "a" * 64}
        with pytest.raises(benchmark_speed.CampaignRefused, match="unexpected"):
            benchmark_speed.load_campaign(_write(tmp_path, _campaign(
                frozen_fingerprints=fingerprints)))

    @pytest.mark.parametrize("digest", ["short", "A" * 64, "z" * 64, 12345])
    def test_a_malformed_digest_is_refused(self, tmp_path, digest):
        fingerprints = {**json.loads(CAMPAIGN.read_text())
                        ["frozen_fingerprints"], "network": digest}
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="lowercase sha256"):
            benchmark_speed.load_campaign(_write(tmp_path, _campaign(
                frozen_fingerprints=fingerprints)))

    def test_the_required_labels_are_the_seven_frozen_ones(self):
        assert set(benchmark_speed.REQUIRED_FINGERPRINT_LABELS) == set(
            json.loads(CAMPAIGN.read_text())["frozen_fingerprints"])

    def test_a_dropped_fingerprint_never_reaches_a_run(self, tmp_path,
                                                       monkeypatch):
        def explode(*args, **kwargs):                       # pragma: no cover
            raise AssertionError("an incomplete campaign must not execute")

        monkeypatch.setattr(benchmark_speed, "run_case", explode)
        fingerprints = dict(json.loads(CAMPAIGN.read_text())
                            ["frozen_fingerprints"])
        fingerprints.pop("network")
        path = _write(tmp_path, _campaign(frozen_fingerprints=fingerprints))
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(path),
            "--artifact-dir", str(artifacts)])
        assert benchmark_speed.main() == 2
        assert not artifacts.exists()


# ── Provenance commands must succeed, not merely run (third fix) ─────────────

class TestFailedProvenanceCommandsFailClosed:
    """`check=False` without reading `returncode` turned a broken repository
    into a clean checkout: `git status --porcelain` exiting 128 prints
    nothing, which read as "no local changes"."""

    def _stub_case(self, calls):
        def run_case(case, workers, seeds, micro, timeout, root):
            calls.append(case)                              # pragma: no cover
            raise AssertionError("must not run without valid provenance")
        return run_case

    def _run_with_git(self, tmp_path, monkeypatch, failing: str):
        monkeypatch.setattr(benchmark_speed, "run_case",
                            self._stub_case([]))
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO 1.27.1")
        real_run = benchmark_speed.subprocess.run

        class Failed:
            returncode = 128
            stdout = ""
            stderr = "fatal: not a git repository"

        def maybe_fail(command, *args, **kwargs):
            if command[:2] == ["git", failing]:
                return Failed()
            return real_run(command, *args, **kwargs)

        monkeypatch.setattr(benchmark_speed.subprocess, "run", maybe_fail)
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(artifacts)])
        code = benchmark_speed.main()
        return code, artifacts

    def test_a_failed_git_status_is_not_a_clean_checkout(self, tmp_path,
                                                         monkeypatch):
        code, artifacts = self._run_with_git(tmp_path, monkeypatch, "status")
        assert code == 2
        assert not artifacts.exists()

    def test_a_failed_git_rev_parse_refuses_the_run(self, tmp_path,
                                                    monkeypatch):
        code, artifacts = self._run_with_git(tmp_path, monkeypatch, "rev-parse")
        assert code == 2
        assert not artifacts.exists()

    def test_the_helper_separates_failure_from_a_clean_tree(self, monkeypatch):
        class Result:
            def __init__(self, code, out):
                self.returncode, self.stdout, self.stderr = code, out, ""

        monkeypatch.setattr(benchmark_speed.subprocess, "run",
                            lambda *a, **k: Result(0, ""))
        assert benchmark_speed._command_output(["git", "status"]) == ""
        monkeypatch.setattr(benchmark_speed.subprocess, "run",
                            lambda *a, **k: Result(128, ""))
        assert benchmark_speed._command_output(["git", "status"]) is None
        monkeypatch.setattr(benchmark_speed.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert benchmark_speed._command_output(["git", "status"]) is None

    def test_a_failed_sumo_version_is_not_a_version(self, monkeypatch):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "Error: cannot load libproj"

        monkeypatch.setattr(benchmark_speed.subprocess, "run",
                            lambda *a, **k: Result())
        assert benchmark_speed.sumo_version() is None

    def test_a_successful_sumo_version_is_used(self, monkeypatch):
        class Result:
            returncode = 0
            stdout = " SUMO 1.27.1 \n"
            stderr = ""

        monkeypatch.setattr(benchmark_speed.subprocess, "run",
                            lambda *a, **k: Result())
        assert benchmark_speed.sumo_version() == "SUMO 1.27.1"

    def test_a_missing_sumo_version_refuses_the_campaign(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setattr(benchmark_speed, "run_case", self._stub_case([]))
        monkeypatch.setattr(benchmark_speed, "sumo_version", lambda: None)
        artifacts = tmp_path / "artifacts"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(artifacts)])
        assert benchmark_speed.main() == 2
        assert not artifacts.exists()


# ── Script-context import and v1/v2 lineage (LUNA-PERF-06) ───────────────────

class TestTheHarnessWorksInItsRealScriptContext:
    """The v1 campaign aborted AFTER running a scenario because
    `python3 tools/benchmark_speed.py` puts `tools/` on sys.path, not the
    repository root, so the production validator could not be imported. The
    in-process tests never saw it, so this one uses a child process."""

    def _synthetic(self, tmp_path):
        phases = {name: 1.0 for name in (
            "input_validation", "closure_preparation", "job_preparation",
            "sumo_execution", "aggregation_validation",
            "trajectory_publication", "disruption_analysis",
            "payload_construction", "artifact_publication", "cleanup")}
        sidecar = tmp_path / "phase_profile.json"
        sidecar.write_text(json.dumps({
            "schema_version": 2, "kind": "scenario_phase_profile",
            "status": "succeeded", "scenario_id": "baseline", "closures": [],
            "simulation_mode": "meso", "seed_set": [1000, 1001, 1002],
            "demand_variant_mapping": {"1000": "q50", "1001": "q10",
                                       "1002": "q90"},
            "demand_signature": "sig-abc", "demand_build_key": "demand-key",
            "network_build_id": "a" * 64,
            "source_fingerprints": {"run_scenario": "b" * 64,
                                    "network": "a" * 64,
                                    "demand_meta": "c" * 64},
            "phase_schema": list(phases), "phases": phases,
            "total": float(len(phases) + 2), "unattributed": 2.0,
            "sumo_seconds_by_seed": {"1000": 3.0, "1001": 3.1, "1002": 2.9},
            "seed_job_seconds_by_seed": {"1000": 3.4, "1001": 3.5,
                                         "1002": 3.2},
        }))
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({
            "scenario_spec": {
                "scenario_id": "baseline", "simulation_mode": "meso",
                "network_build_id": "a" * 64, "seed_set": [1000, 1001, 1002],
                "demand_variant_mapping": {"1000": "q50", "1001": "q10",
                                           "1002": "q90"},
                "closures": []},
            "scenario": {"scenario_id": "baseline",
                         "demand_signature": "sig-abc",
                         "demand_build_key": "demand-key"}}))
        return sidecar, payload

    def _child(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        sidecar, payload = self._synthetic(tmp_path)
        driver = tmp_path / "probe.py"
        driver.write_text(f'''
import json, sys
from pathlib import Path
ROOT, TOOLS = {str(root)!r}, {str(root / "tools")!r}
# Exactly the interpreter state of `python3 tools/benchmark_speed.py`.
sys.path = [TOOLS] + [p for p in sys.path[1:] if p not in (ROOT, "", ".")]
try:
    import run_scenario
except ModuleNotFoundError:
    pass
else:
    raise SystemExit("probe invalid: the root was importable to begin with")
import benchmark_speed as bench
bench.load_phase_profile(
    Path({str(sidecar)!r}),
    payload=json.loads(Path({str(payload)!r}).read_text()),
    inputs={{"source:run_scenario.py": "b" * 64, "network": "a" * 64,
            "demand_meta": "c" * 64}})
print("REACHED_SIDECAR_VALIDATION")
''')
        environment = {key: value for key, value in os.environ.items()
                       if key != "PYTHONPATH"}
        return subprocess.run([sys.executable, str(driver)], cwd=tmp_path,
                              capture_output=True, text=True, timeout=120,
                              env=environment)

    def test_sidecar_loading_is_reachable_from_the_script_context(self,
                                                                  tmp_path):
        result = self._child(tmp_path)
        assert result.returncode == 0, result.stderr[-800:]
        assert "REACHED_SIDECAR_VALIDATION" in result.stdout
        assert "ModuleNotFoundError" not in result.stderr
        # No SUMO or scenario subprocess was involved: the inputs are synthetic.
        assert "run_scenario.py" not in result.stdout

    def test_the_harness_puts_the_repository_root_on_the_path(self):
        source = Path("tools/benchmark_speed.py").read_text()
        assert "sys.path.insert(0, str(ROOT))" in source
        # ... and still uses the production validator, not a copy.
        assert "from run_scenario import validate_phase_profile" in source


class TestRetiredCampaignsCannotRun:
    """A recomputing content key proves a contract is unedited, not unspent.
    Every retired identity must be refused by name, before `load_campaign()`
    can hand anything to an artifact directory or a subprocess. These tests
    read retired CONTRACT JSON only — never a report, sidecar or run tree."""

    FROZEN_KEYS = {
        "scenario_phase_profile_v1":
            "60188b6cdba0c62d7258c53f2d306dd11dff7d29fa134151a584cd80134d1912",
        "scenario_phase_profile_v2":
            "8557b6f54e4b53db7dc68d57583dd5939d78b0b8836cc1a5ea89b59ef48d1ddd",
        "scenario_phase_profile_v3":
            "28402170953b8908b4abc9afb9328699e12c98a3183cd24bdfefdd23cb31dd16",
        "scenario_phase_profile_v4":
            "feeed57cb38a0c189aa9002c3e3611cb7233c467c9ff8cc1906d1c0daf712fa6",
        "scenario_phase_profile_v5":
            "1578d3506291011ff88d4edfb12fd291935171950914feb97e64d0b2d0726f14",
        "scenario_phase_profile_v6":
            "ec3449a07be6cbaf2460086db8cc413ccafef8f075b2f79376dd3ae66610fbc6",
    }

    @pytest.mark.parametrize("path, name", RETIRED)
    def test_a_retired_contract_is_still_its_frozen_self(self, path, name):
        contract = json.loads(path.read_text())
        assert contract["campaign_id"] == name
        assert contract["content_key"] == self.FROZEN_KEYS[name]
        assert (benchmark_speed.campaign_content_key(contract)
                == contract["content_key"])

    @pytest.mark.parametrize("path, name", RETIRED)
    def test_the_loader_refuses_a_retired_identity(self, path, name):
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="not the executable campaign"):
            benchmark_speed.load_campaign(path)
        assert name in benchmark_speed.RETIRED_CAMPAIGN_IDS

    @pytest.mark.parametrize("path, name", RETIRED)
    def test_a_retired_contract_cannot_be_revived_by_renaming_it(
            self, tmp_path, monkeypatch, path, name):
        """Rewriting the id and recomputing the key is exactly the mutation the
        guard exists to stop. With the line CLOSED (production CURRENT is None),
        a renamed contract is refused as an unknown identity — no campaign is
        executable."""
        monkeypatch.setattr(benchmark_speed, "CURRENT_CAMPAIGN_ID", None)
        revived = json.loads(path.read_text())
        revived["campaign_id"] = "scenario_phase_profile_v9"
        revived["content_key"] = benchmark_speed.campaign_content_key(revived)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="no campaign is executable"):
            benchmark_speed.load_campaign(_write(tmp_path, revived))

    def test_an_edited_current_contract_still_fails_on_its_key(self, tmp_path):
        edited = json.loads(CAMPAIGN.read_text())
        edited["execution"] = dict(edited["execution"], trials=7)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="does not recompute"):
            benchmark_speed.load_campaign(_write(tmp_path, edited))


class TestV6ContractIsPreserved:
    """v6 is retired (its one run is spent and closed the line), so it no longer
    loads. Its frozen contract is still history that must be preserved exactly —
    lineage, gates, terminal rule, fingerprints, no-observed-value — so these
    read the real v6 file with NON-LOADER helpers (json + campaign_content_key /
    campaign_matrix), never load_campaign()."""

    def test_v6_recomputes_to_its_immutable_frozen_key(self):
        v6 = json.loads(CAMPAIGN_V6.read_text())
        assert v6["campaign_id"] == "scenario_phase_profile_v6"
        assert v6["content_key"] == (
            "ec3449a07be6cbaf2460086db8cc413ccafef8f075b2f79376dd3ae66610fbc6")
        assert benchmark_speed.campaign_content_key(v6) == v6["content_key"]

    def test_v6_matrix_is_still_the_20_paired_rows(self):
        matrix = benchmark_speed.campaign_matrix(json.loads(CAMPAIGN_V6.read_text()))
        assert len(matrix) == 20
        assert Counter((row["campaign_case"], row["workers"])
                       for row in matrix) == {
            ("baseline_whole_day", 1): 5, ("baseline_whole_day", 3): 5,
            ("closure_whole_window", 1): 5, ("closure_whole_window", 3): 5}

    def test_v6_declares_its_lineage_to_every_retired_identity(self):
        v6 = json.loads(CAMPAIGN_V6.read_text())
        lineage = v6["lineage"]
        assert lineage["supersedes_campaign_id"] == "scenario_phase_profile_v5"
        for key, path in (("supersedes_content_key", CAMPAIGN_V5),
                          ("v4_content_key", CAMPAIGN_V4),
                          ("v3_content_key", CAMPAIGN_V3),
                          ("v2_content_key", CAMPAIGN_V2),
                          ("v1_content_key", CAMPAIGN_V1)):
            assert lineage[key] == json.loads(path.read_text())["content_key"]
        assert lineage["v5_disposition"].startswith("retired_spent")
        assert "ModuleNotFoundError" in lineage["v1_disposition"]
        assert v6["outcomes_present_at_freeze"] is False

    def test_v6_names_only_the_approved_source_changes(self):
        change = json.loads(CAMPAIGN_V6.read_text())["lineage"]["change_from_v5"]
        lowered = change.lower()
        assert "serial" in lowered and "closure prep" in lowered
        assert "parsing" in lowered or "parser" in lowered

    def test_v6_records_no_claim_it_cannot_prove(self):
        contract = json.loads(CAMPAIGN_V6.read_text())

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield f"{path}.{key}", key, value
                    yield from walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    yield from walk(item, f"{path}[{index}]")

        banned_keys = ("user_message", "user_approval", "approval_quote",
                       "approved_by", "approver", "quoted")
        for where, key, value in walk(contract):
            assert not [m for m in banned_keys if m in key.lower()], where
            if isinstance(value, str):
                assert "i explicitly approve" not in value.lower(), where
        access = contract["lineage"]["outcome_access"]
        assert "no observed value" in access
        assert "opened" in access
        for overclaim in ("neither read nor used", "never read",
                          "nothing was used"):
            assert overclaim not in access.lower()

    def test_v6_lineage_leaks_no_v4_or_v5_observed_measurement(self):
        blob = json.dumps(json.loads(CAMPAIGN_V6.read_text()))
        for observed in ("0.318", "0.557", "10.318", "10.557", "10.833",
                         "6.442", "6.506", "17.384", "17.470", "~40", "40%",
                         "39.6", "39.8", "40.5", "40.6"):
            assert observed not in blob, observed

    def test_the_retired_messages_leak_no_observed_measurement(self):
        """The harness's refusal strings are contract-facing text; the v4, v5
        and v6 messages must carry no observed timing."""
        for name in ("scenario_phase_profile_v4", "scenario_phase_profile_v5",
                     "scenario_phase_profile_v6"):
            message = benchmark_speed.RETIRED_CAMPAIGN_IDS[name]
            for observed in ("0.318", "0.423", "0.557", "10.318", "10.423",
                             "10.557", "~40", "40%", "43.8", "40.8"):
                assert observed not in message, (name, observed)
            assert "ceiling" in message

    def test_v6_carries_no_observed_outcome_or_timing_value(self):
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        banned = {"phase_profile", "phases", "wall_s", "total", "unattributed",
                  "sumo_seconds_by_seed", "seed_job_seconds_by_seed",
                  "peak_child_rss_bytes", "scenario_digest", "seed_health",
                  "trajectory_digest", "runs", "p50", "p95", "observed",
                  "results", "report", "returncode"}
        present = banned & set(keys(json.loads(CAMPAIGN_V6.read_text())))
        assert not present, present

    def test_v6_retains_every_approved_execution_value(self):
        v5, v6 = (json.loads(p.read_text()) for p in (CAMPAIGN_V5, CAMPAIGN_V6))
        for field in ("cases", "demand_window", "demand_identity",
                      "required_report_fields", "evidence_class", "execution",
                      "adoption_gates", "not_evidence_for", "excluded_by_design"):
            assert v6[field] == v5[field], field
        assert v6["execution"]["worker_arms"] == [1, 3]
        moved = {label for label in benchmark_speed.REQUIRED_FINGERPRINT_LABELS
                 if v6["frozen_fingerprints"][label]
                 != v5["frozen_fingerprints"][label]}
        assert moved == {"harness:benchmark_speed.py", "source:run_scenario.py"}

    def test_v6_keeps_the_pinned_adoption_gates(self):
        gates = json.loads(CAMPAIGN_V6.read_text())["adoption_gates"]
        assert gates["max_parallel_p95_wall_seconds"] == 10.0
        assert gates["min_p95_wall_improvement_fraction"] == 0.2
        assert gates["authorizes"] == benchmark_speed.ADOPTION_AUTHORIZES_NOTHING
        benchmark_speed.validate_adoption_gates(json.loads(CAMPAIGN_V6.read_text()))

    def test_v6_freezes_the_terminal_decision_rule(self):
        rule = json.loads(CAMPAIGN_V6.read_text())["terminal_decision_rule"]
        blob = json.dumps(rule).lower()
        assert "release validation" in blob
        assert "no retry" in blob or "no mechanical" in blob or "not a retry" in blob
        assert "asynchronous" in blob or "different architecture" in blob


class TestTheCampaignLineIsClosed:
    """No phase-profile campaign is executable any more: production
    CURRENT_CAMPAIGN_ID is None. The loader must refuse the retired v6 by name
    and any unknown/future identity as 'no campaign executable', before touching
    fingerprints, demand, a subprocess, or an artifact directory."""

    def test_no_campaign_is_current_in_production(self):
        # The autouse fixture marks a synthetic campaign current in-process, so
        # read the real production constant from a clean interpreter.
        result = subprocess.run(
            [sys.executable, "-c",
             "from tools import benchmark_speed as b; print(b.CURRENT_CAMPAIGN_ID)"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert result.stdout.strip() == "None"

    def test_the_loader_refuses_v6_by_name(self, monkeypatch):
        monkeypatch.setattr(benchmark_speed, "CURRENT_CAMPAIGN_ID", None)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="spent") as excinfo:
            benchmark_speed.load_campaign(CAMPAIGN_V6)
        assert "not the executable campaign" in str(excinfo.value)

    def test_an_unknown_or_future_identity_is_refused(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setattr(benchmark_speed, "CURRENT_CAMPAIGN_ID", None)
        invented = json.loads(CAMPAIGN_V6.read_text())
        invented["campaign_id"] = "scenario_phase_profile_v7"
        invented["content_key"] = benchmark_speed.campaign_content_key(invented)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="no campaign is executable"):
            benchmark_speed.load_campaign(_write(tmp_path, invented))

    def test_v6_cli_preflight_is_refused_and_creates_nothing(self, tmp_path):
        artifact = tmp_path / "artifacts"
        result = subprocess.run(
            [sys.executable, "tools/benchmark_speed.py", "--campaign",
             str(CAMPAIGN_V6), "--preflight-only",
             "--artifact-dir", str(artifact)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert result.returncode == 2
        assert "refused" in result.stderr.lower()
        assert "spent" in result.stderr.lower()
        assert not artifact.exists()


class TestAdoptionGatesAreBoundToEvidence:
    """A gate nobody can evaluate is decoration, and a gate a worse run could
    clear is worse than none. The contract side is validated on load; the
    evaluation side is exercised here on synthetic reports only."""

    def _report(self, *, serial=20.0, parallel=8.0, **overrides):
        rows = []
        for case, name in (("baseline", "baseline_whole_day"),
                           ("closure", "closure_whole_window")):
            for workers, wall in ((1, serial), (3, parallel)):
                for trial in range(1, 6):
                    rows.append({
                        "case": case, "campaign_case": name, "trial": trial,
                        "workers": workers, "wall_s": wall, "returncode": 0,
                        "phase_profile": {"total": wall},
                        # Paired arms agree by construction: both carry the same
                        # per-(case,trial) digests, so a healthy report is
                        # result-preserving unless a test perturbs it.
                        "scenario_digest": _fake_sha(f"{case}{trial}scenario"),
                        "trajectory_digest": _fake_sha(f"{case}{trial}traj"),
                        "closure_integrity": ("verified_clean"
                                              if case == "closure" else None),
                        "seed_health": [{"seed": seed, "collisions": 0,
                                         "teleports": 0, "running_at_end": 0,
                                         "waiting_at_end": 0, "loaded": 100,
                                         "inserted": 100}
                                        for seed in (1000, 1001, 1002)],
                    })
        report = {"cases": rows, "semantic_mismatches": [],
                  "reference_mismatches": []}
        report.update(overrides)
        return report

    def test_a_result_preserving_faster_arm_clears_every_gate(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        verdict = benchmark_speed.evaluate_adoption_gates(
            self._report(), campaign)
        assert verdict["adoptable"] is True and verdict["failed_gates"] == []
        assert set(verdict["per_case"]) == {"baseline_whole_day",
                                            "closure_whole_window"}
        for entry in verdict["per_case"].values():
            assert entry["parallel_p95_wall_s"] == 8.0
            assert entry["p95_wall_improvement_fraction"] == pytest.approx(0.6)
        assert verdict["authorizes"] == benchmark_speed.ADOPTION_AUTHORIZES_NOTHING

    @pytest.mark.parametrize("report_kwargs, failed", [
        ({"parallel": 10.5}, "parallel_latency_ceiling:baseline_whole_day"),
        ({"serial": 9.0, "parallel": 8.5}, "p95_improvement_floor:baseline_whole_day"),
    ])
    def test_a_slow_or_barely_faster_arm_is_not_adoptable(self, report_kwargs,
                                                          failed):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        verdict = benchmark_speed.evaluate_adoption_gates(
            self._report(**report_kwargs), campaign)
        assert verdict["adoptable"] is False
        assert failed in verdict["failed_gates"]

    @pytest.mark.parametrize("overrides, failed", [
        ({"semantic_mismatches": [{"case": "baseline"}]},
         "semantic_digest_equivalence"),
        ({"reference_mismatches": [{"key": "x"}]}, "reference_equivalence"),
    ])
    def test_a_result_changing_arm_is_never_adoptable(self, overrides, failed):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        verdict = benchmark_speed.evaluate_adoption_gates(
            self._report(**overrides), campaign)
        assert verdict["adoptable"] is False
        assert failed in verdict["failed_gates"]

    def test_unhealthy_seeds_or_failed_rows_block_adoption(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"][0]["seed_health"][0]["teleports"] = 4
        report["cases"][1]["returncode"] = 1
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert {"seed_health", "hard_failure_refusal"} <= set(
            verdict["failed_gates"])

    def test_a_missing_arm_cannot_pass_by_absence(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"] = [row for row in report["cases"]
                           if row["workers"] == 3]
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert any(item.startswith("missing_arm:")
                   for item in verdict["failed_gates"])

    def test_baseline_only_evidence_is_not_adoptable(self):
        """A report that ran only the baseline case must not clear the gates on
        the strength of the rows it happens to have — the frozen matrix wants
        the closure case too."""
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"] = [row for row in report["cases"]
                           if row["case"] == "baseline"]
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "matrix_incomplete" in verdict["failed_gates"]

    def test_one_trial_per_arm_is_not_adoptable(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"] = [row for row in report["cases"] if row["trial"] == 1]
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "matrix_incomplete" in verdict["failed_gates"]

    def test_a_row_with_missing_seed_health_disqualifies_the_campaign(self):
        """A missing arm's worth of seed health used to be averaged away because
        health was flattened across rows; each row must stand on its own."""
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"][7]["seed_health"] = None
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "seed_health" in verdict["failed_gates"]

    def test_incomplete_seed_health_is_not_read_as_healthy(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"][3]["seed_health"] = report["cases"][3]["seed_health"][:2]
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "seed_health" in verdict["failed_gates"]

    @pytest.mark.parametrize("dropped", ["semantic_mismatches",
                                         "reference_mismatches"])
    def test_a_missing_mismatch_list_is_unknown_not_empty(self, dropped):
        """Dropping the list must fail closed, never read as zero mismatches."""
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        del report[dropped]
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert f"missing_{dropped}" in verdict["failed_gates"]

    def test_an_unequal_paired_digest_is_derived_independently(self):
        """Even with an empty semantic_mismatches list, a real per-pair digest
        divergence must be caught by the evaluator's own comparison."""
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["semantic_mismatches"] = []          # report claims agreement
        parallel = next(r for r in report["cases"]
                        if r["case"] == "closure" and r["workers"] == 3)
        parallel["trajectory_digest"] = _fake_sha("divergent")
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "paired_digest_mismatch" in verdict["failed_gates"]

    def test_a_missing_paired_digest_fails_closed(self):
        campaign = benchmark_speed.load_campaign(CAMPAIGN)
        report = self._report()
        report["cases"][2]["scenario_digest"] = None
        verdict = benchmark_speed.evaluate_adoption_gates(report, campaign)
        assert verdict["adoptable"] is False
        assert "digest_missing" in verdict["failed_gates"]

    def test_main_binds_success_to_the_adoption_verdict(self, tmp_path,
                                                        monkeypatch, capsys):
        """A finished campaign whose parallel arm is no faster must return
        non-zero even though nothing crashed and no digest changed."""
        def run_case(case, workers, seeds, micro, timeout, root):
            # Both arms identical AND equally slow: result-preserving, but the
            # 20% improvement floor is not met.
            return {"case": case, "workers": workers, "seeds": seeds,
                    "micro": micro, "command": ["stub"], "returncode": 0,
                    "wall_s": 5.0, "peak_child_rss_bytes": 0,
                    "scenario_bytes": 1, "trajectory_bytes": 0,
                    "scenario_digest": _fake_sha(case + "s"),
                    "trajectory_digest": _fake_sha(case + "t"),
                    "phase_profile": {"total": 5.0},
                    "closure_integrity": ("verified_clean"
                                          if case == "closure" else None),
                    "seed_health": [{"seed": s, "collisions": 0, "teleports": 0,
                                     "running_at_end": 0, "waiting_at_end": 0,
                                     "loaded": 10, "inserted": 10}
                                    for s in (1000, 1001, 1002)],
                    "log": str(root)}

        monkeypatch.setattr(benchmark_speed, "run_case", run_case)
        monkeypatch.setattr(benchmark_speed, "sumo_version",
                            lambda: "SUMO 1.27.1")
        written = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "benchmark_speed.py", "--campaign", str(CAMPAIGN),
            "--artifact-dir", str(tmp_path / "artifacts"),
            "--write", str(written)])

        assert benchmark_speed.main() == 2          # not adoptable, no crash
        capsys.readouterr()
        report = json.loads(written.read_text())
        assert report["adoption"]["adoptable"] is False
        assert any(item.startswith("p95_improvement_floor")
                   for item in report["adoption"]["failed_gates"])

    @pytest.mark.parametrize("gates, match", [
        ({"max_semantic_mismatches": 1}, "must be 0"),
        ({"max_reference_mismatches": 2}, "must be 0"),
        ({"max_parallel_p95_wall_seconds": 30.0}, "validated-"),
        ({"min_p95_wall_improvement_fraction": 0.01}, "frozen 20% floor"),
        ({"min_p95_wall_improvement_fraction": 0.0}, "frozen 20% floor"),
        ({"authorizes": "deployment"}, "literal no-authority"),
        ({"authorizes": "adopt the parallel arm"}, "literal no-authority"),
        ({"required_existing_gates": ["seed_health"]}, "must list exactly"),
        ({"parallel_arm_workers": 8}, "frozen parallel arm"),
        ({"serial_arm_workers": 3}, "serial arm"),
    ])
    def test_a_loosened_gate_block_is_refused_on_load(self, tmp_path, gates,
                                                      match):
        contract = json.loads(CAMPAIGN.read_text())
        contract["adoption_gates"] = {**contract["adoption_gates"], **gates}
        contract["content_key"] = benchmark_speed.campaign_content_key(contract)
        with pytest.raises(benchmark_speed.CampaignRefused, match=match):
            benchmark_speed.load_campaign(_write(tmp_path, contract))

    def test_a_contract_without_gates_is_refused_on_load(self, tmp_path):
        contract = json.loads(CAMPAIGN.read_text())
        contract.pop("adoption_gates")
        contract["content_key"] = benchmark_speed.campaign_content_key(contract)
        with pytest.raises(benchmark_speed.CampaignRefused,
                           match="no machine-readable adoption_gates"):
            benchmark_speed.load_campaign(_write(tmp_path, contract))
