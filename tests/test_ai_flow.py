"""Contract tests for the local Codex/Claude orchestrator."""

import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tools import ai_flow


ROOT = Path(__file__).resolve().parents[1]


def _config():
    return ai_flow.load_config(ROOT / ".ai-flow" / "config.toml", ROOT)


def _legacy_claude_role(name: str, *, effort: str = "high"):
    return ai_flow.Role(
        name=name,
        runner="claude",
        model="sonnet",
        effort=effort,
        prompt_path=ROOT / ".ai-flow" / "prompts" / f"{name}.md",
        permission_mode="auto",
        max_turns=260 if name == "worker" else 100,
        max_budget_usd=14.0 if name == "worker" else 5.0,
    )


def _with_role(config, name: str, role):
    return replace(config, roles={**config.roles, name: role})


def _ready_plan(summary="plan"):
    return {
        "status": "READY",
        "summary": summary,
        "implementation_steps": ["repair"],
        "likely_files": ["source.py"],
        "acceptance_criteria": ["verified"],
        "suggested_checks": ["pytest"],
        "risks": ["none"],
    }


def _staged_source_state(**overrides):
    state = {
        "schema_version": 1,
        "status": "BLOCKED",
        "task": "controlled staged source task",
        "next_stage": "complete",
        "execution_cycle": 2,
        "review_cycles": 2,
        "test_fix_cycles": 0,
        "evidence_generations": 0,
        "active_invocation": None,
        "active_session_id": None,
        "started_at": "2026-09-01T00:00:00+00:00",
        "workflow_protocol": "staged_evidence_v3",
    }
    state.update(overrides)
    return state


def _install_plan_schema(root):
    schema_dir = root / ".ai-flow" / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "plan.json").write_text(
        (ROOT / ".ai-flow" / "schemas" / "plan.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_role_routing_matches_requested_models_and_effort():
    config = _config()

    assert config.roles["planner"].runner == "codex"
    assert config.roles["planner"].model == "gpt-5.6-sol"
    assert config.roles["planner"].effort == "high"
    assert config.roles["worker"].runner == "codex"
    assert config.roles["worker"].model == "gpt-5.6-luna"
    assert config.roles["worker"].effort == "high"
    assert config.roles["worker"].sandbox == "workspace-write"
    assert config.roles["reviewer"].runner == "codex"
    assert config.roles["fixer"].runner == "codex"
    assert config.roles["fixer"].model == "gpt-5.6-luna"
    assert config.roles["fixer"].effort == "high"
    assert config.roles["fixer"].sandbox == "workspace-write"
    assert config.roles["worker"].max_turns is None
    assert config.roles["worker"].max_budget_usd is None
    assert config.roles["fixer"].max_turns is None
    assert config.roles["fixer"].max_budget_usd is None
    assert config.roles["fixer_fallback"].runner == "codex"
    assert config.roles["fixer_fallback"].effort == "medium"
    assert config.roles["fixer_fallback"].sandbox == "workspace-write"
    assert config.max_review_cycles == 3
    assert config.max_test_fix_cycles == 2
    assert config.max_blocker_repair_cycles == 2
    assert config.max_review_findings_per_repair == 5


def _evidence_policy():
    return ai_flow.EvidencePolicy(
        source_globs=("*.py",),
        registration_globs=(
            "validation/registration-*.json",
            "validation/full-month-*.json",
            "validation/phase-report-*.json",
        ),
        max_generations=2,
        max_new_registrations_per_glob=2,
        max_code_repair_cycles=1,
        phase_checkpoint_globs=("validation/registration-*.json",),
        phase6_registration_globs=(
            "validation/full-month-*.json",
            "validation/phase-report-*.json",
        ),
        phase_report_globs=("validation/phase-report-*.json",),
    )


def _write_phase_producer_fixtures(root, suffix=""):
    """Write minimal producer-shaped evidence for staged-flow fixtures."""
    validation = root / "validation"

    def write_evidence(filename, payload):
        payload = dict(payload)
        payload["content_key"] = ai_flow._canonical_digest(payload)
        (validation / filename).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    registration_path = root / f"bounded-registration-phase3{suffix}.json"
    registration = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": "phase3-producer",
        "content_key": "",
        "selection": {"selected_ids": []},
        "selected_cases": [],
    }
    registration["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in registration.items()
        if key not in {"content_key", "registered_at"}
    })
    registration_path.write_text(json.dumps(registration), encoding="utf-8")
    write_evidence(f"registration-phase3{suffix}.json", {
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "evidence_id": "phase3-producer",
        "registration": {
            "evidence_id": "phase3-producer",
            "content_key": registration["content_key"],
            "path": str(registration_path),
            "sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
        },
        "status": "INCONCLUSIVE",
        "selection": {"selected_ids": []},
        "case_results": [],
        "gate_s": {"population_complete": False},
        "suite_consumption": {
            "attempts": 0, "active_seconds": 0.0,
            "disk_growth_bytes": 0, "execution_started": False,
        },
        "resources": {
            "peak_rss_bytes": 0, "disk_growth_bytes": 0,
            "disk_roots": [str(root / "phase3-disk")],
            "rss_status": "not_run",
        },
    })
    write_evidence(f"registration-phase4{suffix}.json", {
        "schema": "monthly_cost_ledger_profile_v1",
        "kind": "monthly_cost_ledger_profile",
        "release_evidence": False,
        "evidence_id": "phase4-producer",
        "status": "INCONCLUSIVE",
        "wall_time_s": 0.0,
        "sumo_attempts": 0,
        "sumo_started": False,
        "population_complete": True,
        "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
        "peak_rss_bytes": 0,
        "disk_growth_bytes": 0,
        "fresh_roots": {"output": str(root / "phase4-disk")},
    })
    write_evidence(f"registration-phase5{suffix}.json", {
        "schema": "monthly_cost_ledger_profile_v1",
        "kind": "monthly_cost_ledger_profile",
        "release_evidence": False,
        "evidence_id": "phase5-producer",
        "status": "PASS",
        "phase_5_decision": "NOT_TRIGGERED",
        "population_complete": True,
        "phase_timing_complete": True,
        "sumo_zero_launch_gate": True,
        "population": {"daily_units": 1950,
                       "daily_variant_records": 5850,
                       "parents": 1690},
    })


def _write_dummy_evidence(path):
    payload = {"content_key": ai_flow._canonical_digest({})}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_not_allowed_phase_report(root, run_dir, filename="phase-report-1.json"):
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    checkpoint = state["phase_3_5_checkpoint"]
    review = state["phase_3_5_review"]
    frozen = state["code_freeze"]
    phase_values = {
        "phase_0": "PASS", "phase_1": "PASS", "phase_2": "PASS",
        # The fixture has no real Phase 3--5 producer artifacts.  Model that
        # fact as an inconclusive bounded result so the truth table correctly
        # makes Phase 6 NOT_ALLOWED instead of pretending it was skipped.
        "phase_3": "INCONCLUSIVE", "phase_4": "INCONCLUSIVE",
        "phase_5": "NOT_TRIGGERED",
    }
    status_artifacts = {}
    evidence_ids = {}
    for phase, status in phase_values.items():
        evidence_id = f"{phase}-status"
        references = []
        if phase in {"phase_3", "phase_4", "phase_5"}:
            producer_prefix = {
                "phase_3": "registration-phase3",
                "phase_4": "registration-phase4",
                "phase_5": "registration-phase5",
            }[phase]
            for pattern in state["phase_3_5_checkpoint"]["artifact_inventory"]:
                for relative, digest in state["phase_3_5_checkpoint"][
                        "artifact_inventory"][pattern].items():
                    if not (
                        Path(relative).name.startswith(producer_prefix)
                        or (
                            phase == "phase_3"
                            and not Path(relative).name.startswith(
                                ("registration-phase4", "registration-phase5")
                            )
                        )
                    ):
                        continue
                    producer_path = root / relative
                    producer = json.loads(producer_path.read_text(encoding="utf-8"))
                    references.append({
                        "path": str(producer_path), "sha256": digest,
                        "content_key": producer["content_key"],
                    })
        artifact = {
            "schema": "subhour_phase_status_v1",
            "kind": "subhour_phase_status",
            "phase": phase,
            "status": status,
            "release_evidence": False,
            "evidence_id": evidence_id,
            "lineage": {},
            "references": references,
        }
        artifact["content_key"] = ai_flow._canonical_digest(artifact)
        status_path = root / "validation" / f"{phase}-status.json"
        status_path.write_text(json.dumps(artifact), encoding="utf-8")
        status_artifacts[phase] = {
            "path": str(status_path),
            "sha256": hashlib.sha256(status_path.read_bytes()).hexdigest(),
            "content_key": artifact["content_key"],
        }
        producer_ids = []
        for reference in references:
            producer_path = Path(reference["path"])
            producer = json.loads(producer_path.read_text(encoding="utf-8"))
            if isinstance(producer.get("evidence_id"), str):
                producer_ids.append(producer["evidence_id"])
        evidence_ids[phase] = [evidence_id, *sorted(set(producer_ids))]
    evidence_ids.update({"phase_6": [], "phase_7": []})
    report = {
        "schema": "subhour_phase_report_v1",
        "kind": "subhour_phase_report",
        "release_evidence": False,
        "status": "COMPLETE",
        "phases": {**phase_values, "phase_6": "NOT_ALLOWED", "phase_7": "NOT_TRIGGERED"},
        "evidence_ids": evidence_ids,
        "measurements": {
            "sumo_attempts": 0,
            "active_time_s": 0.0,
            "cold_ledger_time_s": 0.0,
            "peak_rss_bytes": 0,
            "disk_growth_bytes": 0,
            "full_month_within_60_minutes": False,
        },
        "phase_resources": {
            "phase_3": {
                "status": "INCONCLUSIVE", "sumo_attempts": 0,
                "active_time_s": 0.0, "peak_rss_bytes": 0,
                "disk_growth_bytes": 0,
                "disk_roots": [str(root / "phase3-disk")],
            },
            "phase_4": {
                "status": "INCONCLUSIVE", "sumo_attempts": 0,
                "active_time_s": 0.0, "peak_rss_bytes": 0,
                "disk_growth_bytes": 0,
                "disk_roots": [str(root / "phase4-disk")],
            },
            "phase_6": {
                "status": "NOT_ALLOWED", "sumo_attempts": 0,
                "active_time_s": 0.0, "peak_rss_bytes": 0,
                "disk_growth_bytes": 0, "disk_roots": [],
                "publication_commit_elapsed_s": None,
            },
        },
        "status_artifacts": status_artifacts,
        "lineage": {
            "source_digest": frozen["digest"],
            "checkpoint_content_digest": checkpoint["content_digest"],
            "review_content_digest": review["content_digest"],
            "review_lineage_digest": checkpoint["lineage_digest"],
        },
        "artifacts": {},
    }
    report["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in report.items() if key != "content_key"
    })
    (root / "validation" / filename).write_text(
        json.dumps(report), encoding="utf-8"
    )


def test_complete_subhour_config_uses_bounded_staged_evidence():
    config = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.toml", ROOT
    )

    assert config.max_review_cycles == 3
    assert config.max_review_findings_per_repair == 50
    assert config.evidence_policy is not None
    assert config.evidence_policy.max_generations == 2
    assert config.evidence_policy.max_new_registrations_per_glob == 2
    assert config.evidence_policy.max_code_repair_cycles == 1
    assert "validation/subhour_bounded_sumo_registration_*.json" in (
        config.evidence_policy.registration_globs
    )
    assert "validation/subhour_bounded_sumo_outcome_*.json" in (
        config.evidence_policy.registration_globs
    )
    assert config.evidence_policy.phase_checkpoint_globs == (
        "validation/subhour_bounded_sumo_registration_*.json",
        "validation/subhour_bounded_sumo_outcome_*.json",
        "validation/monthly_cost_ledger_profile_subhour-*.json",
        "validation/window_cost_index_subhour-*.json",
    )
    assert "validation/subhour_full_month_registration_*.json" in (
        config.evidence_policy.phase6_registration_globs
    )
    assert ".ai-flow/config.complete-subhour.toml" in (
        config.evidence_policy.source_globs
    )
    assert "web/**/*" in config.evidence_policy.source_globs


def test_opus_subhour_config_keeps_the_policy_and_pins_requested_routing():
    """The live Opus continuation config must differ only in worker routing.

    The run that produces sub-hour evidence loads this file, not the Codex
    variant the other contract tests read, so an unnoticed edit here would
    change the bounded evidence policy with no test observing it.
    """
    opus_path = ROOT / ".ai-flow" / "config.complete-subhour.opus.toml"
    opus = ai_flow.load_config(opus_path, ROOT)
    codex = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.toml", ROOT
    )

    assert opus.roles["worker"].runner == "claude"
    assert opus.roles["worker"].model == "opus"
    assert opus.roles["worker"].effort == "high"
    assert opus.roles["fixer"].runner == "claude"
    assert opus.roles["fixer"].model == "opus"
    assert opus.roles["fixer"].effort == "high"
    for name in ("planner", "reviewer"):
        assert opus.roles[name].runner == "codex"
        assert opus.roles[name].model == "gpt-5.6-sol"
        assert opus.roles[name].effort == "high"
        assert opus.roles[name].sandbox == "read-only"

    # A Claude worker writes through the harness, so it must never be handed a
    # Codex sandbox string that would silently mean "unrestricted" here.
    assert opus.roles["worker"].sandbox == ""
    assert opus.roles["fixer"].sandbox == ""

    assert opus.checks == codex.checks
    assert opus.max_review_cycles == codex.max_review_cycles
    assert opus.max_review_findings_per_repair == (
        codex.max_review_findings_per_repair
    )
    policy = opus.evidence_policy
    assert policy is not None
    reference = codex.evidence_policy
    assert reference is not None
    assert policy.max_generations == reference.max_generations
    assert policy.max_new_registrations_per_glob == (
        reference.max_new_registrations_per_glob
    )
    assert policy.max_code_repair_cycles == reference.max_code_repair_cycles
    assert policy.registration_globs == reference.registration_globs
    assert policy.phase_checkpoint_globs == reference.phase_checkpoint_globs
    assert policy.phase6_registration_globs == (
        reference.phase6_registration_globs
    )
    assert policy.phase_report_globs == reference.phase_report_globs
    # Each config freezes itself, so the two source-glob sets differ only there.
    assert set(policy.source_globs) ^ set(reference.source_globs) == {
        ".ai-flow/config.complete-subhour.opus.toml",
        ".ai-flow/config.complete-subhour.toml",
    }
    assert ".ai-flow/config.complete-subhour.opus.toml" in policy.source_globs
    assert "tools/**/*.py" in policy.source_globs
    assert "tests/**/*.py" in policy.source_globs


def test_sonnet_subhour_config_keeps_the_policy_and_pins_requested_routing():
    """The Sonnet continuation config must differ only in Claude model routing.

    A second live config is a second way to change the bounded evidence policy
    with no test observing it, which is exactly the hole the Opus pin above
    closes.  This file exists because the Claude session limit, not model
    capability, is what stopped run 20260901-142528-90507, so the cheaper
    routing must inherit every gate unchanged rather than quietly relax one.
    """
    sonnet_path = ROOT / ".ai-flow" / "config.complete-subhour.sonnet.toml"
    sonnet = ai_flow.load_config(sonnet_path, ROOT)
    codex = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.toml", ROOT
    )

    for name in ("worker", "fixer"):
        assert sonnet.roles[name].runner == "claude"
        assert sonnet.roles[name].model == "sonnet"
        assert sonnet.roles[name].effort == "high"
        # A Claude role writes through the harness, so it must never be handed
        # a Codex sandbox string that would silently mean "unrestricted" here.
        assert sonnet.roles[name].sandbox == ""
    for name in ("planner", "reviewer"):
        assert sonnet.roles[name].runner == "codex"
        assert sonnet.roles[name].model == "gpt-5.6-sol"
        assert sonnet.roles[name].effort == "high"
        assert sonnet.roles[name].sandbox == "read-only"

    assert sonnet.checks == codex.checks
    assert sonnet.max_review_cycles == codex.max_review_cycles
    assert sonnet.max_review_findings_per_repair == (
        codex.max_review_findings_per_repair
    )
    policy = sonnet.evidence_policy
    assert policy is not None
    reference = codex.evidence_policy
    assert reference is not None
    assert policy.max_generations == reference.max_generations
    assert policy.max_new_registrations_per_glob == (
        reference.max_new_registrations_per_glob
    )
    assert policy.max_code_repair_cycles == reference.max_code_repair_cycles
    assert policy.registration_globs == reference.registration_globs
    assert policy.phase_checkpoint_globs == reference.phase_checkpoint_globs
    assert policy.phase6_registration_globs == (
        reference.phase6_registration_globs
    )
    assert policy.phase_report_globs == reference.phase_report_globs
    # Each config freezes itself, so the two source-glob sets differ only there.
    assert set(policy.source_globs) ^ set(reference.source_globs) == {
        ".ai-flow/config.complete-subhour.sonnet.toml",
        ".ai-flow/config.complete-subhour.toml",
    }
    assert ".ai-flow/config.complete-subhour.sonnet.toml" in policy.source_globs
    assert "tools/**/*.py" in policy.source_globs
    assert "tests/**/*.py" in policy.source_globs


def test_complete_subhour_freeze_protects_web_policy_assets(tmp_path):
    config = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.toml", ROOT
    )
    web = tmp_path / "web"
    web.mkdir()
    asset = web / "app.js"
    asset.write_text("const policy = 'q10-q50-q90';\n", encoding="utf-8")
    frozen = ai_flow.source_manifest(
        tmp_path, config.evidence_policy.source_globs
    )
    asset.write_text("const policy = 'q50-only';\n", encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="Protected source changed"):
        ai_flow.assert_source_frozen(tmp_path, config.evidence_policy, frozen)


def test_source_manifest_detects_same_size_same_mtime_content_change(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    original_stat = source.stat()
    frozen = ai_flow.source_manifest(tmp_path, ("*.py",))

    source.write_text("value = 2\n", encoding="utf-8")
    ai_flow.os.utime(
        source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns)
    )

    with pytest.raises(ai_flow.FlowError, match="Protected source changed"):
        ai_flow.assert_source_frozen(tmp_path, _evidence_policy(), frozen)


def test_phase_3_5_checkpoint_and_review_bind_fresh_artifact_bytes(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy = _evidence_policy()
    baseline = ai_flow.evidence_inventory(tmp_path, policy.registration_globs)
    evidence = validation / "registration-1.json"
    evidence.write_text('{"phase": 3, "status": "PASS"}\n', encoding="utf-8")
    frozen = ai_flow.source_manifest(tmp_path, policy.source_globs)

    checkpoint = ai_flow.build_phase_3_5_checkpoint(
        tmp_path, policy, baseline, frozen
    )
    review = {
        "status": "APPROVED",
        "summary": "all checkpoint bytes verified",
        "findings": [],
        "blocked_reason": "",
    }
    artifact = ai_flow.build_phase_3_5_review_artifact(
        checkpoint, review, "phase-review-01"
    )
    ai_flow.validate_phase_3_5_review_artifact(
        artifact, checkpoint, review, frozen
    )

    evidence.write_text('{"phase": 3, "status": "TAMPERED"}\n', encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="checkpoint lineage"):
        ai_flow.validate_phase_3_5_checkpoint(
            tmp_path, policy, baseline, frozen, checkpoint
        )


def test_phase_3_5_checkpoint_rejects_phase6_before_review(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy = _evidence_policy()
    baseline = ai_flow.evidence_inventory(tmp_path, policy.registration_globs)
    (validation / "registration-1.json").write_text("{}\n", encoding="utf-8")
    (validation / "full-month-1.json").write_text("{}\n", encoding="utf-8")
    frozen = ai_flow.source_manifest(tmp_path, policy.source_globs)

    with pytest.raises( ai_flow.FlowError, match="Phase 6/7 evidence"):
        ai_flow.build_phase_3_5_checkpoint(tmp_path, policy, baseline, frozen)


def test_evidence_budget_is_per_registration_series(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    baseline = ai_flow.evidence_inventory(
        tmp_path, _evidence_policy().registration_globs
    )
    for number in (1, 2):
        (validation / f"registration-{number}.json").write_text("{}")
        ai_flow.assert_evidence_budget(
            tmp_path, _evidence_policy(), baseline
        )
    (validation / "registration-3.json").write_text("{}")

    with pytest.raises(ai_flow.FlowError, match="budget exceeded"):
        ai_flow.assert_evidence_budget(tmp_path, _evidence_policy(), baseline)


def test_evidence_budget_rejects_removing_preserved_artifact(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    baseline = ai_flow.evidence_inventory(
        tmp_path, _evidence_policy().registration_globs
    )
    registration = validation / "registration-1.json"
    registration.write_text("{}", encoding="utf-8")
    preserved = ai_flow.evidence_inventory(
        tmp_path, _evidence_policy().registration_globs
    )
    registration.unlink()

    with pytest.raises(ai_flow.FlowError, match="Append-only evidence was removed"):
        ai_flow.assert_evidence_budget(
            tmp_path, _evidence_policy(), baseline, preserved
        )


def test_evidence_budget_rejects_overwriting_preserved_artifact(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    baseline = ai_flow.evidence_inventory(
        tmp_path, _evidence_policy().registration_globs
    )
    registration = validation / "registration-1.json"
    registration.write_text('{"version": 1}', encoding="utf-8")
    preserved = ai_flow.evidence_inventory(
        tmp_path, _evidence_policy().registration_globs
    )
    registration.write_text('{"version": 2}', encoding="utf-8")

    with pytest.raises(ai_flow.FlowError, match="Append-only evidence was modified"):
        ai_flow.assert_evidence_budget(
            tmp_path, _evidence_policy(), baseline, preserved
        )


def _staged_flow(tmp_path, monkeypatch, *, mutate_source_in_evidence=False,
                 create_registration_in_worker=False,
                 modify_registration_in_worker=False,
                 create_registration_in_code_checks=False,
                 mutate_source_in_final_checks=False,
                 create_phase6_in_evidence=False,
                 create_phase6_after_review=False,
                 code_review_results=None,
                 reuse_plan_run_id=None,
                 mutate_reused_plan_source_in_evidence=False):
    staged_policy = replace(_evidence_policy(), max_new_registrations_per_glob=10)
    config = replace(_config(), evidence_policy=staged_policy)
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "validation").mkdir()
    if modify_registration_in_worker:
        (tmp_path / "validation" / "registration-existing.json").write_text(
            '{"version": 1}', encoding="utf-8"
        )
    if reuse_plan_run_id:
        # A reused plan is adopted from a source run that has already reached
        # a reviewed terminal state; set that source run up before the fresh
        # run below adopts its plan.json.
        _install_plan_schema(tmp_path)
        source_dir = tmp_path / ".ai-flow" / "runs" / reuse_plan_run_id
        source_dir.mkdir(parents=True)
        (source_dir / "plan.json").write_text(
            json.dumps(_ready_plan("reused source plan")), encoding="utf-8"
        )
        (source_dir / "state.json").write_text(
            json.dumps(_staged_source_state()), encoding="utf-8"
        )
    (tmp_path / ".ai-flow").mkdir(exist_ok=True)
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    codex_calls = []
    work_calls = []
    code_review_results = list(code_review_results or [])

    def fake_codex(_config, role, _root, _run_dir, _schema, prompt,
                   invocation, *_args, **_kwargs):
        codex_calls.append((role, invocation, prompt))
        if role == "planner":
            return {"status": "READY", "summary": "plan", "blocked_reason": ""}
        if invocation.startswith("code-review-") and code_review_results:
            result = code_review_results.pop(0)
            (_run_dir / f"{invocation}.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            return result
        return {"status": "APPROVED", "summary": "approved", "findings": [],
                "blocked_reason": ""}

    def fake_work(_config, role, root, _run_dir, prompt, invocation,
                  *_args, **_kwargs):
        work_calls.append((role, invocation, prompt))
        if invocation == "worker-01" and create_registration_in_worker:
            (root / "validation" / "registration-worker.json").write_text("{}")
        if invocation == "worker-01" and modify_registration_in_worker:
            (root / "validation" / "registration-existing.json").write_text(
                '{"version": 2}', encoding="utf-8"
            )
        if invocation == "evidence-01":
            _write_dummy_evidence(root / "validation" / "registration-1.json")
            _write_phase_producer_fixtures(root)
            if create_phase6_in_evidence:
                (root / "validation" / "full-month-1.json").write_text("{}")
            if mutate_source_in_evidence:
                (root / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
            if mutate_reused_plan_source_in_evidence:
                source_plan_path = (
                    root / ".ai-flow" / "runs" / reuse_plan_run_id / "plan.json"
                )
                original = json.loads(source_plan_path.read_text(encoding="utf-8"))
                source_plan_path.write_text(
                    json.dumps({**original, "summary": "rewritten during evidence"}),
                    encoding="utf-8",
                )
        if invocation == "evidence-post-review-01" and create_phase6_after_review:
            (root / "validation" / "full-month-1.json").write_text("{}")
        if invocation == "evidence-post-review-01" and not create_phase6_after_review:
            _write_not_allowed_phase_report(root, _run_dir)
        return {"status": "IMPLEMENTED", "summary": "done", "changed_files": [],
                "checks": [], "blockers": []}

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    check_calls = {"count": 0}

    def fake_checks(*_args, **_kwargs):
        check_calls["count"] += 1
        if create_registration_in_code_checks and check_calls["count"] == 1:
            (tmp_path / "validation" / "registration-check.json").write_text(
                "{}", encoding="utf-8"
            )
        # Checks run once before each of: code-review-01, code-review-02
        # (the mandatory reserved verification review), the Phase 3-5
        # checkpoint review, and the final review -- so the 4th call is the
        # one immediately preceding final_review.
        if mutate_source_in_final_checks and check_calls["count"] == 4:
            (tmp_path / "source.py").write_text("VALUE = 3\n", encoding="utf-8")
        return [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ]

    monkeypatch.setattr(ai_flow, "run_checks", fake_checks)
    result = ai_flow.run_flow(
        config, tmp_path, "build evidence", allow_dirty=False, dry_run=False,
        extra_checks=(), no_checks=False,
        reuse_plan_run_id=reuse_plan_run_id,
    )
    return result, codex_calls, work_calls


def test_staged_evidence_requires_code_approval_before_one_generation(
    tmp_path, monkeypatch
):
    validator_calls = []
    original_validator = ai_flow.validate_post_review_terminal_artifacts

    def recording_validator(*args, **kwargs):
        result = original_validator(*args, **kwargs)
        validator_calls.append(result)
        return result

    monkeypatch.setattr(
        ai_flow, "validate_post_review_terminal_artifacts", recording_validator
    )
    result, codex_calls, work_calls = _staged_flow(tmp_path, monkeypatch)

    assert result == ai_flow.EXIT_APPROVED
    assert [(role, invocation) for role, invocation, _ in codex_calls] == [
        ("planner", "plan"),
        ("reviewer", "code-review-01"),
        ("reviewer", "code-review-02"),
        ("reviewer", "phase-review-01"),
        ("reviewer", "final-review-01"),
    ]
    assert [(role, invocation) for role, invocation, _ in work_calls] == [
        ("worker", "worker-01"),
        ("fixer", "evidence-01"),
        ("fixer", "evidence-post-review-01"),
    ]
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["evidence_generations"] == 1
    assert state["status"] == "APPROVED"
    assert (run_dir / "code-freeze-01.json").is_file()
    report = json.loads(
        (tmp_path / "validation" / "phase-report-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["phases"]["phase_6"] == "NOT_ALLOWED"
    assert "phase_6_registration" not in report["artifacts"]
    assert "phase_6_outcome" not in report["artifacts"]
    assert len(validator_calls) == 1


def test_staged_worker_cannot_freeze_evidence_before_code_review(
    tmp_path, monkeypatch
):
    with pytest.raises(ai_flow.FlowError, match="before CODE_APPROVED"):
        _staged_flow(
            tmp_path, monkeypatch, create_registration_in_worker=True
        )


def test_staged_worker_cannot_overwrite_existing_evidence(tmp_path, monkeypatch):
    with pytest.raises(ai_flow.FlowError, match="changed evidence"):
        _staged_flow(
            tmp_path, monkeypatch, modify_registration_in_worker=True
        )


def test_staged_code_checks_cannot_create_evidence(tmp_path, monkeypatch):
    with pytest.raises(ai_flow.FlowError, match="changed evidence"):
        _staged_flow(
            tmp_path, monkeypatch, create_registration_in_code_checks=True
        )


def test_staged_reserved_verification_review_never_launches_unreviewable_fix(
    tmp_path, monkeypatch
):
    changes_required = {
        "status": "CHANGES_REQUIRED",
        "summary": "material defect",
        "findings": [
            {
                "severity": "high",
                "file": "source.py",
                "evidence": "reproduced",
                "required_change": "repair the trust boundary",
            }
        ],
        "blocked_reason": "",
    }

    result, codex_calls, work_calls = _staged_flow(
        tmp_path,
        monkeypatch,
        code_review_results=[changes_required, changes_required],
    )

    assert result == ai_flow.EXIT_BLOCKED
    assert [
        invocation
        for role, invocation, _prompt in codex_calls
        if role == "reviewer"
    ] == ["code-review-01", "code-review-02"]
    assert [
        invocation
        for _role, invocation, _prompt in work_calls
        if invocation.startswith("code-review-fix-")
    ] == ["code-review-fix-01"]
    assert not any(
        invocation.startswith("evidence-")
        for _role, invocation, _prompt in work_calls
    )
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["code_review_cycles"] == 2
    assert state["code_repair_cycles"] == 1
    assert state["evidence_generations"] == 0


def test_staged_freeze_requires_two_independent_approved_reviews(
    tmp_path, monkeypatch
):
    """A single APPROVED code-review-01 must never be enough on its own.

    code-review-02 is the reserved verification review and must run -- as
    an independent invocation, with its own persisted artifact -- against
    the *same* code before any freeze or evidence is created.
    """
    approved = {
        "status": "APPROVED", "summary": "approved", "findings": [],
        "blocked_reason": "",
    }

    result, codex_calls, work_calls = _staged_flow(
        tmp_path, monkeypatch, code_review_results=[approved, dict(approved)],
    )

    assert result == ai_flow.EXIT_APPROVED
    assert [
        invocation
        for role, invocation, _prompt in codex_calls
        if role == "reviewer" and invocation.startswith("code-review-")
    ] == ["code-review-01", "code-review-02"]
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    # Both reviews are independently persisted -- code-review-02 is a real
    # second invocation, not a cached replay of code-review-01's verdict.
    assert (run_dir / "code-review-01.json").is_file()
    assert (run_dir / "code-review-02.json").is_file()
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["code_review_cycles"] == 2
    assert state["code_repair_cycles"] == 0
    assert state["evidence_generations"] == 1
    assert (run_dir / "code-freeze-01.json").is_file()


def test_staged_approval_reversed_by_verification_review_blocks_without_repair(
    tmp_path, monkeypatch
):
    """code-review-01 APPROVED, then code-review-02 CHANGES_REQUIRED.

    The reserved verification review is the last word: reversing an
    earlier approval must stop the run before any evidence and must never
    spend a repair, even though the repair budget (1) has not been used
    yet -- a fixer here would let unreviewed code slip toward evidence.
    """
    approved = {
        "status": "APPROVED", "summary": "approved", "findings": [],
        "blocked_reason": "",
    }
    changes_required = {
        "status": "CHANGES_REQUIRED",
        "summary": "material defect found on re-review",
        "findings": [
            {
                "severity": "high",
                "file": "source.py",
                "evidence": "reproduced",
                "required_change": "repair the trust boundary",
            }
        ],
        "blocked_reason": "",
    }

    result, codex_calls, work_calls = _staged_flow(
        tmp_path,
        monkeypatch,
        code_review_results=[approved, changes_required],
    )

    assert result == ai_flow.EXIT_BLOCKED
    assert [
        invocation
        for role, invocation, _prompt in codex_calls
        if role == "reviewer"
    ] == ["code-review-01", "code-review-02"]
    assert not any(
        invocation.startswith("code-review-fix-")
        for _role, invocation, _prompt in work_calls
    )
    assert not any(
        invocation.startswith("evidence-")
        for _role, invocation, _prompt in work_calls
    )
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["code_review_cycles"] == 2
    assert state["code_repair_cycles"] == 0
    assert state["evidence_generations"] == 0
    assert not (run_dir / "code-freeze-01.json").exists()
    assert "code_review_awaiting_confirmation" not in state


def test_staged_evidence_stops_on_protected_source_change(tmp_path, monkeypatch):
    with pytest.raises(ai_flow.FlowError, match="Protected source changed"):
        _staged_flow(tmp_path, monkeypatch, mutate_source_in_evidence=True)


def test_staged_evidence_stops_on_reused_plan_provenance_drift_mid_run(
    tmp_path, monkeypatch
):
    """A reused plan's source bytes can still be rewritten while a live run

    that adopted it is deep inside source-frozen evidence.  Verifying the
    binding once, at planner/worker adoption, is not enough: the source run
    directory stays writable for the whole life of this run.  This drives a
    full staged-v3 run through CODE_APPROVED (which is where the lineage is
    first proven and persisted) and into the evidence fixer itself, mutates
    the adopted plan's source run mid-fixer -- exactly the consumer path a
    real interrupted/concurrent source run would hit -- and proves the very
    next provenance re-check (the evidence guard that runs immediately after
    the fixer returns, before Phase 6/7 evidence or any terminal approval)
    fails closed instead of silently accepting the drifted lineage.
    """
    with pytest.raises(ai_flow.FlowError, match="source bytes drifted"):
        _staged_flow(
            tmp_path,
            monkeypatch,
            reuse_plan_run_id="20260901-000000-500",
            mutate_reused_plan_source_in_evidence=True,
        )
    run_dirs = [
        path for path in (tmp_path / ".ai-flow" / "runs").iterdir()
        if path.name != "20260901-000000-500"
    ]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    # The lineage was proven and persisted once, at the CODE_APPROVED freeze,
    # on the unmutated bytes -- that is what makes the later mutation a
    # detectable drift rather than an unverified first adoption.
    assert state.get("reused_plan_lineage")
    assert state.get("code_freeze") is not None
    # The run must have stopped before any Phase 6/7 evidence or terminal
    # approval was ever produced from the drifted lineage.
    assert not list((tmp_path / "validation").glob("full-month-*.json"))
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ERROR"


def test_staged_evidence_rejects_phase6_registration_before_checkpoint_review(
    tmp_path, monkeypatch
):
    with pytest.raises(ai_flow.FlowError, match="Phase 6/7 evidence"):
        _staged_flow(tmp_path, monkeypatch, create_phase6_in_evidence=True)


def test_staged_post_review_rejects_malformed_phase6_terminal(tmp_path, monkeypatch):
    with pytest.raises(ai_flow.FlowError, match="complete Phase 0-7 report"):
        _staged_flow(tmp_path, monkeypatch, create_phase6_after_review=True)


def test_terminal_report_rejects_missing_status_and_measurement_proof(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()
    report = {
        "schema": "subhour_phase_report_v1",
        "kind": "subhour_phase_report",
        "release_evidence": False,
        "status": "COMPLETE",
        "phases": {phase: "PASS" for phase in ai_flow._PHASE_REPORT_PHASES},
        "artifacts": {},
    }
    report["content_key"] = ai_flow._canonical_digest(report)
    report_path = validation / "phase-report-1.json"
    policy = _evidence_policy()
    baseline = ai_flow.evidence_inventory(tmp_path, policy.registration_globs)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    frozen = ai_flow.source_manifest(tmp_path, policy.source_globs)
    checkpoint = {
        "content_digest": "checkpoint", "lineage_digest": "lineage",
    }
    review = {"content_digest": "review"}
    with pytest.raises(ai_flow.FlowError, match="evidence IDs"):
        ai_flow.validate_post_review_terminal_artifacts(
            tmp_path, policy, frozen, checkpoint, review, baseline
        )


def test_terminal_report_rejects_status_artifact_mismatch(tmp_path, monkeypatch):
    _staged_flow(tmp_path, monkeypatch)
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report_path = tmp_path / "validation" / "phase-report-1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    status_path = Path(report["status_artifacts"]["phase_3"]["path"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["status"] = "NOT_TRIGGERED"
    status["content_key"] = ai_flow._canonical_digest(
        {key: value for key, value in status.items() if key != "content_key"}
    )
    status_path.write_text(json.dumps(status), encoding="utf-8")
    report["status_artifacts"]["phase_3"].update({
        "sha256": hashlib.sha256(status_path.read_bytes()).hexdigest(),
        "content_key": status["content_key"],
    })
    report["content_key"] = ai_flow._canonical_digest(
        {key: value for key, value in report.items() if key != "content_key"}
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")
    policy = _evidence_policy()
    with pytest.raises(ai_flow.FlowError, match="derived from its artifact"):
        ai_flow.validate_post_review_terminal_artifacts(
            tmp_path, policy, state["code_freeze"], state["phase_3_5_checkpoint"],
            state["phase_3_5_review"], state["evidence_generation_baseline"],
        )


def test_terminal_report_rejects_invented_measurement_values(tmp_path, monkeypatch):
    _staged_flow(tmp_path, monkeypatch)
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report_path = tmp_path / "validation" / "phase-report-1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["measurements"]["sumo_attempts"] = 99
    report["content_key"] = ai_flow._canonical_digest(
        {key: value for key, value in report.items() if key != "content_key"}
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="measurements"):
        ai_flow.validate_post_review_terminal_artifacts(
            tmp_path, _evidence_policy(), state["code_freeze"],
            state["phase_3_5_checkpoint"], state["phase_3_5_review"],
            state["evidence_generation_baseline"],
        )


def test_gate_s_truth_table_requires_eligible_bounded_phase3_when_phase6_not_allowed(
    tmp_path,
):
    source = {
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "status": "INCONCLUSIVE_PERFORMANCE_GATE",
        "decision_population_complete": True,
        "evidence_id": "bounded-gate-source",
        "selection": {"selected_ids": ["case-content"]},
        "case_results": [{
            "case_id": "case-1",
            "search_content_key": "case-content",
            "decision_population_complete": True,
        }],
        "gate_s": {"population_complete": True,
                    "variants": {"q10": {}, "q50": {}, "q90": {}}},
    }
    registration = {
        "schema": "subhour_cost_ordered_bounded_registration_v1",
        "evidence_id": "bounded-gate-source",
        "selection": {"selected_ids": ["case-content"]},
        "selected_cases": [{"case_id": "case-1",
                             "search_content_key": "case-content"}],
    }
    registration["content_key"] = ai_flow._canonical_digest(registration)
    registration_path = tmp_path / "registration.json"
    registration_path.write_text(json.dumps(registration), encoding="utf-8")
    source["registration"] = {
        "evidence_id": registration["evidence_id"],
        "content_key": registration["content_key"],
        "path": str(registration_path),
        "sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
    }
    source["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in source.items() if key != "content_key"
    })
    source_path = tmp_path / "bounded-outcome.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    status_artifact = {
        "references": [{"path": str(source_path)}],
    }

    assert ai_flow._phase3_gate_population_is_eligible(
        tmp_path, status_artifact
    )
    assert ai_flow._gate_s_is_required("NOT_ALLOWED", True)
    assert not ai_flow._gate_s_is_required("NOT_ALLOWED", False)


def test_gate_s_rejects_partial_or_unflagged_decision_population(tmp_path):
    source = {
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "status": "INCONCLUSIVE_PERFORMANCE_GATE",
        "selection": {"selected_ids": ["case-a", "case-b"]},
        "case_results": [{
            "case_id": "case-1",
            "search_content_key": "case-a",
            "decision_population_complete": True,
        }],
        "gate_s": {"population_complete": True,
                    "variants": {"q10": {}, "q50": {}, "q90": {}}},
    }
    source["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in source.items() if key != "content_key"
    })
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert not ai_flow._phase3_gate_population_is_eligible(
        tmp_path, {"references": [{"path": str(path)}]})

    source["case_results"].append({
        "case_id": "case-2", "search_content_key": "case-b",
        "decision_population_complete": False,
    })
    source["content_key"] = ai_flow._canonical_digest({
        key: value for key, value in source.items() if key != "content_key"
    })
    path.write_text(json.dumps(source), encoding="utf-8")
    assert not ai_flow._phase3_gate_population_is_eligible(
        tmp_path, {"references": [{"path": str(path)}]})


def test_final_report_measurements_are_derived_from_bound_producers(tmp_path):
    phase3 = {
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "kind": "subhour_bounded_sumo_outcome",
        "evidence_id": "bounded-1",
        "suite_consumption": {"attempts": 7, "active_seconds": 1.0,
                               "disk_growth_bytes": 0},
        "resources": {"peak_rss_bytes": 10, "disk_growth_bytes": 0,
                       "disk_roots": [str(tmp_path / "phase3-disk")]},
    }
    phase4 = {
        "schema": "monthly_cost_ledger_profile_v1",
        "kind": "monthly_cost_ledger_profile",
        "evidence_id": "ledger-1",
        "wall_time_s": 3.5,
        "sumo_attempts": 0,
        "sumo_started": False,
        "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
        "peak_rss_bytes": 12,
        "disk_growth_bytes": 14,
        "fresh_roots": {"output": str(tmp_path / "phase4-disk")},
    }
    paths = {}
    for name, value in (("phase3.json", phase3), ("phase4.json", phase4)):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    status_artifacts = {
        "phase_3": {"references": [{"path": str(paths["phase3.json"])}]},
        "phase_4": {"references": [{"path": str(paths["phase4.json"])}]},
    }
    phase6 = {
        "status": "READY",
        "authoritative_status": "READY",
        "ready_proof_valid": True,
        "work_stopped_elapsed_s": 3299,
        "publication_elapsed_s": 3500,
        "publication_deadline_s": 3600,
        "telemetry": {
            "sumo_attempts": 9,
            "peak_rss_bytes": 20,
            "disk_growth_bytes": 25,
            "active_elapsed_s": 3300.0,
            "disk_roots": [str(tmp_path / "phase6-disk")],
        },
    }
    phase6_payload = tmp_path / "phase6-output.json"
    phase6_payload.write_text("{}", encoding="utf-8")
    phase6_receipt = {
        "schema": "append_only_publication_receipt_v1",
        "path": str(phase6_payload),
        "payload_sha256": hashlib.sha256(phase6_payload.read_bytes()).hexdigest(),
        "status": "READY",
        "authoritative_status": "READY",
        "committed_elapsed_s": 3500.0,
        "publication_deadline_s": 3600.0,
        "within_deadline": True,
    }
    phase6_receipt["content_key"] = ai_flow._canonical_digest(phase6_receipt)
    phase6_receipt_path = tmp_path / ".phase6-output.receipt.json"
    phase6_receipt_path.write_text(json.dumps(phase6_receipt), encoding="utf-8")
    phase6["publication_receipt_path"] = str(phase6_receipt_path)
    assert ai_flow._derive_report_measurements(
        tmp_path, status_artifacts,
        {"phase_6": "PASS"}, phase6,
    ) == {
        "sumo_attempts": 16,
        "active_time_s": 3301.0,
        "cold_ledger_time_s": 3.5,
        "peak_rss_bytes": 20,
        "disk_growth_bytes": 39,
        "full_month_within_60_minutes": True,
        "phase_resources": {
            "phase_3": {
                "status": None, "sumo_attempts": 7,
                "active_time_s": 1.0, "peak_rss_bytes": 10,
                "disk_growth_bytes": 0,
                "disk_roots": [str(tmp_path / "phase3-disk")],
            },
            "phase_4": {
                "status": None, "sumo_attempts": 0,
                "active_time_s": 3.5, "peak_rss_bytes": 12,
                "disk_growth_bytes": 14,
                "disk_roots": [str(tmp_path / "phase4-disk")],
            },
            "phase_6": {
                "status": "PASS", "sumo_attempts": 9,
                "active_time_s": 3300.0, "peak_rss_bytes": 20,
                "disk_growth_bytes": 25,
                "disk_roots": [str(tmp_path / "phase6-disk")],
                "publication_commit_elapsed_s": 3500.0,
            },
        },
    }
    phase4["fresh_roots"] = {"output": str(tmp_path / "phase3-disk")}
    paths["phase4.json"].write_text(json.dumps(phase4), encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="overlap"):
        ai_flow._derive_report_measurements(
            tmp_path, status_artifacts, {"phase_6": "PASS"}, phase6)


def test_final_report_preserves_unavailable_phase4_rss_without_zero(
        tmp_path):
    phase3_path = tmp_path / "phase3.json"
    phase4_path = tmp_path / "phase4.json"
    phase3_path.write_text(json.dumps({
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "suite_consumption": {
            "attempts": 1, "active_seconds": 2.0,
            "disk_growth_bytes": 3, "execution_started": True,
        },
        "resources": {
            "peak_rss_bytes": 10, "disk_growth_bytes": 3,
            "disk_roots": [str(tmp_path / "phase3")],
            "rss_status": "measured",
        },
    }), encoding="utf-8")
    phase4_path.write_text(json.dumps({
        "schema": "monthly_cost_ledger_profile_v1",
        "wall_time_s": 4.0, "sumo_attempts": 0,
        "sumo_started": False,
        "population_complete": True,
        "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
        "peak_rss_bytes": None,
        "process_tree_rss_complete": False,
        "process_tree_rss_error": "census unavailable after profile",
        "disk_growth_bytes": 5,
        "fresh_roots": {"output": str(tmp_path / "phase4")},
    }), encoding="utf-8")
    status_artifacts = {
        "phase_3": {"references": [{"path": str(phase3_path)}]},
        "phase_4": {"references": [{"path": str(phase4_path)}]},
    }

    derived = ai_flow._derive_report_measurements(
        tmp_path, status_artifacts,
        {"phase_3": "PASS", "phase_4": "INCONCLUSIVE",
         "phase_6": "NOT_ALLOWED"}, None)

    unavailable = {"status": "unavailable",
                   "error": "census unavailable after profile"}
    assert derived["peak_rss_bytes"] == unavailable
    assert derived["phase_resources"]["phase_4"]["peak_rss_bytes"] == unavailable
    assert derived["phase_resources"]["phase_4"]["sumo_attempts"] == 0


def test_final_report_counts_contaminated_phase4_attempts(tmp_path):
    phase3_path = tmp_path / "phase3.json"
    phase4_path = tmp_path / "phase4.json"
    phase3_path.write_text(json.dumps({
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "suite_consumption": {"attempts": 2, "active_seconds": 1.0,
                               "disk_growth_bytes": 0},
        "resources": {"peak_rss_bytes": 10, "disk_growth_bytes": 0,
                       "disk_roots": [str(tmp_path / "phase3")]},
    }), encoding="utf-8")
    phase4_path.write_text(json.dumps({
        "schema": "monthly_cost_ledger_profile_v1",
        "wall_time_s": 4.0, "sumo_attempts": 3, "sumo_started": True,
        "sumo_start_observation": {"before": 0, "after": 3, "delta": 3},
        "peak_rss_bytes": 12, "disk_growth_bytes": 5,
        "fresh_roots": {"output": str(tmp_path / "phase4")},
    }), encoding="utf-8")
    derived = ai_flow._derive_report_measurements(
        tmp_path,
        {"phase_3": {"references": [{"path": str(phase3_path)}]},
         "phase_4": {"references": [{"path": str(phase4_path)}]}},
        {"phase_3": "PASS", "phase_4": "INCONCLUSIVE",
         "phase_6": "NOT_ALLOWED"}, None)
    assert derived["phase_resources"]["phase_4"]["sumo_attempts"] == 3
    assert derived["sumo_attempts"] == 5


def test_final_report_accepts_receipt_bound_phase6_census_loss_as_inconclusive(
        tmp_path):
    phase3_path = tmp_path / "phase3.json"
    phase4_path = tmp_path / "phase4.json"
    phase3_path.write_text(json.dumps({
        "schema": "subhour_cost_ordered_bounded_outcome_v1",
        "suite_consumption": {"attempts": 1, "active_seconds": 1.0,
                               "disk_growth_bytes": 2},
        "resources": {"peak_rss_bytes": 10, "disk_growth_bytes": 2,
                       "disk_roots": [str(tmp_path / "phase3")]},
    }), encoding="utf-8")
    phase4_path.write_text(json.dumps({
        "schema": "monthly_cost_ledger_profile_v1",
        "wall_time_s": 3.0, "sumo_attempts": 0, "sumo_started": False,
        "sumo_start_observation": {"before": 0, "after": 0, "delta": 0},
        "peak_rss_bytes": 12, "disk_growth_bytes": 4,
        "fresh_roots": {"output": str(tmp_path / "phase4")},
    }), encoding="utf-8")
    payload_path = tmp_path / "phase6-outcome.json"
    payload_path.write_text("{}\n", encoding="utf-8")
    receipt = {
        "schema": "append_only_publication_receipt_v1",
        "path": str(payload_path),
        "payload_sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
        "status": "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE",
        "authoritative_status": "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE",
        "committed_elapsed_s": 100.0,
        "publication_deadline_s": 3600.0,
        "within_deadline": True,
    }
    receipt["content_key"] = ai_flow._canonical_digest(receipt)
    receipt_path = tmp_path / ".phase6-outcome.receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    phase6 = {
        "status": "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE",
        "execution_started": True,
        "publication_receipt_path": str(receipt_path),
        "publication_outcome_path": str(payload_path),
        "telemetry": {
            "sumo_attempts": 4, "active_elapsed_s": 90.0,
            "peak_rss_bytes": None,
            "process_tree_rss_complete": False,
            "process_tree_rss_error": "census lost after work",
            "disk_growth_bytes": 6,
            "disk_roots": [str(tmp_path / "phase6")],
        },
    }
    derived = ai_flow._derive_report_measurements(
        tmp_path,
        {"phase_3": {"references": [{"path": str(phase3_path)}]},
         "phase_4": {"references": [{"path": str(phase4_path)}]}},
        {"phase_3": "PASS", "phase_4": "PASS",
         "phase_6": "INCONCLUSIVE"}, phase6)
    assert derived["sumo_attempts"] == 5
    assert derived["peak_rss_bytes"] == {
        "status": "unavailable", "error": "census lost after work"}
    assert derived["phase_resources"]["phase_6"]["peak_rss_bytes"] == {
        "status": "unavailable", "error": "census lost after work"}


def test_final_report_rejects_numeric_rss_after_census_loss(tmp_path):
    with pytest.raises(ai_flow.FlowError, match="incomplete census"):
        ai_flow._report_rss_value(
            {"peak_rss_bytes": 123, "process_tree_rss_complete": False},
            phase_status="INCONCLUSIVE", label="Phase 6 peak RSS",
            execution_started=True)


def test_phase6_receipt_must_name_the_reported_outcome(tmp_path):
    payload = tmp_path / "phase6-output.json"
    swapped = tmp_path / "swapped-output.json"
    payload.write_text('{"status": "READY"}\n', encoding="utf-8")
    swapped.write_text('{"status": "READY"}\n', encoding="utf-8")
    receipt = {
        "schema": "append_only_publication_receipt_v1",
        "path": str(swapped),
        "payload_sha256": hashlib.sha256(swapped.read_bytes()).hexdigest(),
        "status": "READY",
        "authoritative_status": "READY",
        "committed_elapsed_s": 5.0,
        "publication_deadline_s": 10.0,
        "within_deadline": True,
    }
    receipt["content_key"] = ai_flow._canonical_digest(receipt)
    receipt_path = tmp_path / ".phase6-output.receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    outcome = {
        "publication_receipt_path": str(receipt_path),
        "publication_outcome_path": str(payload),
    }
    with pytest.raises(ai_flow.FlowError, match="another outcome"):
        ai_flow._phase6_publication_receipt(
            tmp_path, outcome, expected_payload_path=payload)


def test_report_reference_rejects_historical_outside_and_swapped_paths(tmp_path):
    validation = tmp_path / "validation"
    validation.mkdir()

    def write_artifact(path, value):
        artifact = {"value": value}
        artifact["content_key"] = ai_flow._canonical_digest(artifact)
        path.write_text(json.dumps(artifact), encoding="utf-8")
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_key": artifact["content_key"],
        }

    historical = write_artifact(validation / "historical.json", "old")
    current = write_artifact(validation / "current.json", "new")
    with pytest.raises(ai_flow.FlowError, match="fresh member"):
        ai_flow._validate_report_reference(
            tmp_path, historical, label="historical", active_evidence_paths={
                (validation / "current.json").resolve(): "validation/current.json"
            }
        )
    outside = write_artifact(tmp_path.parent / "outside-report.json", "outside")
    with pytest.raises(ai_flow.FlowError, match="outside the repository"):
        ai_flow._validate_report_reference(
            tmp_path, outside, label="outside",
            active_evidence_paths={(tmp_path.parent / "outside-report.json").resolve(): "x"},
        )
    with pytest.raises(ai_flow.FlowError, match="fresh member"):
        ai_flow._validate_report_reference(
            tmp_path, current, label="swapped", active_evidence_paths={
                (validation / "historical.json").resolve(): "validation/historical.json"
            }
        )


def test_staged_final_checks_cannot_change_frozen_source(tmp_path, monkeypatch):
    with pytest.raises(ai_flow.FlowError, match="Protected source changed"):
        _staged_flow(
            tmp_path, monkeypatch, mutate_source_in_final_checks=True
        )


def test_staged_resume_reuses_interrupted_evidence_generation(tmp_path, monkeypatch):
    staged_policy = replace(_evidence_policy(), max_new_registrations_per_glob=10)
    config = replace(_config(), evidence_policy=staged_policy)
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "validation").mkdir()
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    invocations = []
    failed_once = {"value": False}

    def fake_codex(_config, role, _root, run_dir, _schema, _prompt,
                   invocation, *_args, **_kwargs):
        if role == "planner":
            result = {"status": "READY", "summary": "plan", "blocked_reason": ""}
            (run_dir / "plan.json").write_text(json.dumps(result), encoding="utf-8")
            return result
        return {"status": "APPROVED", "summary": "approved", "findings": [],
                "blocked_reason": ""}

    def fake_work(_config, _role, root, _run_dir, _prompt, invocation,
                  *_args, **_kwargs):
        invocations.append(invocation)
        if invocation == "evidence-01" and not failed_once["value"]:
            failed_once["value"] = True
            _write_dummy_evidence(root / "validation" / "registration-1.json")
            raise ai_flow.FlowError("transient evidence interruption")
        if invocation == "evidence-01":
            _write_phase_producer_fixtures(root)
        if invocation == "evidence-post-review-01":
            _write_not_allowed_phase_report(root, _run_dir)
        return {"status": "IMPLEMENTED", "summary": "done", "changed_files": [],
                "checks": [], "blockers": []}

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ]
    )

    with pytest.raises(ai_flow.FlowError, match="transient evidence"):
        ai_flow.run_flow(
            config, tmp_path, "build evidence", allow_dirty=False, dry_run=False,
            extra_checks=(), no_checks=False,
        )
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["evidence_generations"] == 1
    assert state["evidence_generation_in_progress"] == 1

    result = ai_flow.run_flow(
        config, tmp_path, "", allow_dirty=False, dry_run=False,
        extra_checks=(), no_checks=False, resume_run_id=run_dir.name,
        fresh_stage=True,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert invocations.count("evidence-01") == 2
    assert "evidence-02" not in invocations
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["evidence_generations"] == 1
    assert "evidence_generation_in_progress" not in state


def test_staged_final_review_repair_refreezes_generation_two_bytes(tmp_path, monkeypatch):
    staged_policy = replace(_evidence_policy(), max_new_registrations_per_glob=10)
    config = replace(_config(), evidence_policy=staged_policy)
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "validation").mkdir()
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    final_review_seen = {"value": False}

    def fake_codex(_config, role, _root, run_dir, _schema, _prompt,
                   invocation, *_args, **_kwargs):
        if role == "planner":
            return {"status": "READY", "summary": "plan", "blocked_reason": ""}
        if invocation == "final-review-01":
            final_review_seen["value"] = True
            result = {"status": "CHANGES_REQUIRED", "summary": "repair", "findings": [
                {"severity": "high", "file": "source.py", "evidence": "x",
                 "required_change": "repair final evidence"}
            ], "blocked_reason": ""}
            (run_dir / f"{invocation}.json").write_text(json.dumps(result), encoding="utf-8")
            return result
        return {"status": "APPROVED", "summary": "approved", "findings": [],
                "blocked_reason": ""}

    def fake_work(_config, _role, root, run_dir, _prompt, invocation,
                  *_args, **_kwargs):
        if invocation == "evidence-01":
            _write_dummy_evidence(root / "validation" / "registration-1.json")
            _write_dummy_evidence(root / "validation" / "registration-1-outcome.json")
            _write_phase_producer_fixtures(root, "-1")
        if invocation == "evidence-02":
            _write_dummy_evidence(root / "validation" / "registration-2.json")
            _write_dummy_evidence(root / "validation" / "registration-2-outcome.json")
            _write_phase_producer_fixtures(root, "-2")
        if invocation.startswith("evidence-post-review-"):
            generation = invocation.rsplit("-", 1)[-1]
            _write_not_allowed_phase_report(
                root, run_dir, filename=f"phase-report-{generation}.json")
        return {"status": "IMPLEMENTED", "summary": "done", "changed_files": [],
                "checks": [], "blockers": []}

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ]
    )

    assert ai_flow.run_flow(
        config, tmp_path, "build evidence", allow_dirty=False, dry_run=False,
        extra_checks=(), no_checks=False,
    ) == ai_flow.EXIT_APPROVED
    assert final_review_seen["value"] is True
    run_dir = next((tmp_path / ".ai-flow" / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert set(state["evidence_generation_baselines"]) == {"1", "2"}
    inventory = state["phase_3_5_checkpoint"]["artifact_inventory"][
        "validation/registration-*.json"
    ]
    assert inventory["validation/registration-2-outcome.json"] == hashlib.sha256(
        json.dumps({"content_key": ai_flow._canonical_digest({})}).encode()
    ).hexdigest()
    assert inventory["validation/registration-2.json"] == hashlib.sha256(
        json.dumps({"content_key": ai_flow._canonical_digest({})}).encode()
    ).hexdigest()
    assert {
        path for path in inventory if "registration-phase" in path
    } == {
        "validation/registration-phase3-2.json",
        "validation/registration-phase4-2.json",
        "validation/registration-phase5-2.json",
    }


def test_staged_code_review_sends_every_finding_in_one_repair(tmp_path, monkeypatch):
    staged_policy = replace(_evidence_policy(), max_new_registrations_per_glob=10)
    config = replace(_config(), evidence_policy=staged_policy)
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "validation").mkdir()
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    repair_prompts = []

    def fake_codex(_config, role, _root, run_dir, _schema, _prompt,
                   invocation, *_args, **_kwargs):
        if role == "planner":
            return {"status": "READY", "summary": "plan", "blocked_reason": ""}
        if invocation == "code-review-01":
            result = {
                "status": "CHANGES_REQUIRED", "summary": "all findings",
                "findings": [
                    {"severity": "high", "file": "source.py", "evidence": "x",
                     "required_change": f"repair {number}"}
                    for number in range(1, 8)
                ], "blocked_reason": "",
            }
            (run_dir / f"{invocation}.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            return result
        return {"status": "APPROVED", "summary": "approved", "findings": [],
                "blocked_reason": ""}

    def fake_work(_config, _role, root, _run_dir, prompt, invocation,
                  *_args, **_kwargs):
        if invocation == "code-review-fix-01":
            repair_prompts.append(prompt)
        if invocation == "evidence-01":
            _write_dummy_evidence(root / "validation" / "registration-1.json")
            _write_phase_producer_fixtures(root, "-1")
        if invocation == "evidence-post-review-01":
            _write_not_allowed_phase_report(root, _run_dir)
        return {"status": "IMPLEMENTED", "summary": "done", "changed_files": [],
                "checks": [], "blockers": []}

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ]
    )

    assert ai_flow.run_flow(
        config, tmp_path, "build evidence", allow_dirty=False, dry_run=False,
        extra_checks=(), no_checks=False,
    ) == ai_flow.EXIT_APPROVED
    assert len(repair_prompts) == 1
    assert "repair 1" in repair_prompts[0]
    assert "repair 7" in repair_prompts[0]


def test_worker_block_gets_bounded_independent_unblock_before_terminal(
    tmp_path, monkeypatch
):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    codex_results = iter(
        [
            {"status": "READY", "summary": "plan", "blocked_reason": ""},
            {"status": "APPROVED", "summary": "done", "findings": []},
        ]
    )
    work_results = iter(
        [
            {
                "status": "BLOCKED",
                "summary": "manifest missing",
                "blockers": ["fresh registration is required"],
            },
            {"status": "IMPLEMENTED", "summary": "derived it", "blockers": []},
        ]
    )
    roles = []
    prompts = []

    monkeypatch.setattr(
        ai_flow, "run_codex_role", lambda *_args, **_kwargs: next(codex_results)
    )

    def fake_work(_config, role, _root, _run_dir, prompt, *_args, **_kwargs):
        roles.append(role)
        prompts.append(prompt)
        return next(work_results)

    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_args, **_kwargs: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "complete the bounded task",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert roles == ["worker", "fixer"]
    assert "fresh registration is required" in prompts[1]
    assert "in-scope work and must be resolved" in prompts[1]


def test_review_fixer_block_gets_bounded_live_state_recheck(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    codex_results = iter([
        {"status": "READY", "summary": "plan", "blocked_reason": ""},
        {"status": "CHANGES_REQUIRED", "summary": "repair", "findings": [
            {"severity": "high", "required_change": "finish evidence"}]},
        {"status": "APPROVED", "summary": "done", "findings": []},
    ])
    work_results = iter([
        {"status": "IMPLEMENTED", "summary": "worker", "blockers": []},
        {"status": "BLOCKED", "summary": "busy", "blockers": [
            "lock metadata names a dead pid"]},
        {"status": "IMPLEMENTED", "summary": "rechecked flock", "blockers": []},
    ])
    prompts = []

    def fake_codex(_config, role, _root, run_dir, *_args, **_kwargs):
        result = next(codex_results)
        if role == "reviewer" and result["status"] == "CHANGES_REQUIRED":
            (run_dir / "review-01.json").write_text(
                json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)

    def fake_work(_config, _role, _root, _run_dir, prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return next(work_results)

    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda *_args, **_kwargs: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}])

    result = ai_flow.run_flow(
        config, tmp_path, "complete evidence", allow_dirty=False,
        dry_run=False, extra_checks=(), no_checks=False)

    assert result == ai_flow.EXIT_APPROVED
    assert len(prompts) == 3
    assert "Blocked review-repair attempt" in prompts[-1]
    assert "kernel lock is authoritative" in prompts[-1]
    state = json.loads(next((tmp_path / ".ai-flow" / "runs").iterdir()).joinpath(
        "state.json").read_text(encoding="utf-8"))
    assert state["review_fix_blocker_repair_cycles_01"] == 1


def test_missing_codex_profile_falls_back_to_equivalent_inline_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    role = _config().roles["planner"]

    command, profile_found = ai_flow.build_codex_command(
        role,
        ROOT,
        ROOT / ".ai-flow" / "schemas" / "plan.json",
        tmp_path / "plan.json",
        "plan this",
    )

    assert not profile_found
    assert "--profile" not in command
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="high"' in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert 'approval_policy="never"' in command


def test_luna_worker_uses_high_reasoning_and_workspace_write(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    role = _config().roles["worker"]

    command, profile_found = ai_flow.build_codex_command(
        role,
        ROOT,
        ROOT / ".ai-flow" / "schemas" / "work.json",
        tmp_path / "work.json",
        "implement this",
    )

    assert not profile_found
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="high"' in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"


def test_stale_codex_profile_is_not_silently_used(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "planner.config.toml").write_text(
        'model = "old-model"\nmodel_reasoning_effort = "xhigh"\n', encoding="utf-8"
    )

    command, profile_found = ai_flow.build_codex_command(
        _config().roles["planner"],
        ROOT,
        ROOT / ".ai-flow" / "schemas" / "plan.json",
        tmp_path / "plan.json",
        "plan this",
    )

    assert not profile_found
    assert "--profile" not in command
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"


def test_setup_installs_named_profiles_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = _config()

    assert ai_flow.setup_profiles(config, force=False) == 0
    planner = tmp_path / "planner.config.toml"
    reviewer = tmp_path / "reviewer.config.toml"
    assert planner.is_file()
    assert reviewer.is_file()
    assert 'model = "gpt-5.6-sol"' in planner.read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "high"' in planner.read_text(encoding="utf-8")
    assert 'sandbox_mode = "read-only"' in planner.read_text(encoding="utf-8")
    assert ai_flow.setup_profiles(config, force=False) == 0


def test_claude_worker_is_explicit_and_blocks_consequential_git_actions():
    role = _legacy_claude_role("worker")
    command = ai_flow.build_claude_command(
        role,
        ROOT / ".ai-flow" / "schemas" / "work.json",
        "implement this",
        session_id="00000000-0000-4000-8000-000000000001",
    )

    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert command[command.index("--max-turns") + 1] == "260"
    assert command[command.index("--max-budget-usd") + 1] == "14"
    assert command[command.index("--session-id") + 1].endswith("0001")
    assert "--no-session-persistence" not in command
    denied = command[command.index("--disallowedTools") + 1]
    assert "Bash(git push *)" in denied
    assert "Bash(git commit *)" in denied
    schema = json.loads(command[command.index("--json-schema") + 1])
    assert "$schema" not in schema
    assert schema["properties"]["status"]["enum"] == ["IMPLEMENTED", "BLOCKED"]


def test_claude_interrupted_stage_can_resume_the_same_persisted_session():
    role = _legacy_claude_role("fixer", effort="medium")
    command = ai_flow.build_claude_command(
        role,
        ROOT / ".ai-flow" / "schemas" / "work.json",
        "continue the bounded repair",
        session_id="00000000-0000-4000-8000-000000000002",
        resume_session=True,
    )

    assert command[command.index("--resume") + 1].endswith("0002")
    assert "--session-id" not in command
    assert command[command.index("--max-turns") + 1] == "100"
    assert command[command.index("--max-budget-usd") + 1] == "5"


def test_claude_retry_appends_attempt_artifacts_instead_of_overwriting(tmp_path, monkeypatch):
    old_log = tmp_path / "review-fix-01.log"
    old_log.write_text("original 429 evidence", encoding="utf-8")
    envelope = {
        "session_id": "00000000-0000-4000-8000-000000000003",
        "total_cost_usd": 1.25,
        "structured_output": {
            "status": "IMPLEMENTED",
            "summary": "fixed",
            "files_changed": [],
            "checks_run": [],
            "blockers": [],
        },
    }
    seen_logs = []

    def fake_process(_command, _root, _timeout, log_path, _dry_run, **_kwargs):
        seen_logs.append(log_path)
        log_path.write_text("new attempt", encoding="utf-8")
        return ai_flow.ProcessResult(0, json.dumps(envelope), "")

    monkeypatch.setattr(ai_flow, "run_process", fake_process)

    config = _with_role(_config(), "fixer", _legacy_claude_role("fixer"))
    result = ai_flow.run_claude_role(
        config,
        "fixer",
        ROOT,
        tmp_path,
        "repair",
        "review-fix-01",
        False,
    )

    assert result["status"] == "IMPLEMENTED"
    assert old_log.read_text(encoding="utf-8") == "original 429 evidence"
    assert seen_logs == [tmp_path / "review-fix-01-attempt-02.log"]
    assert (tmp_path / "review-fix-01-attempt-02.json").is_file()


def test_extra_checks_are_argv_not_shell_commands():
    checks = ai_flow.parse_extra_checks(["python3 -m pytest -q tests/test_auth.py"])

    assert checks == (("python3", "-m", "pytest", "-q", "tests/test_auth.py"),)


def test_task_file_becomes_the_recorded_ai_flow_task(tmp_path, monkeypatch):
    assignment = tmp_path / ".ai-flow" / "tasks" / "assignment.md"
    assignment.parent.mkdir(parents=True)
    assignment.write_text("Implement the bounded assignment.\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(
        ai_flow,
        "_config_and_root",
        lambda _config_path: (_config(), tmp_path),
    )

    def fake_run_flow(config, root, task, *args):
        captured.update(config=config, root=root, task=task, args=args)
        return 0

    monkeypatch.setattr(ai_flow, "run_flow", fake_run_flow)

    result = ai_flow.main(
        ["--task-file", ".ai-flow/tasks/assignment.md", "--allow-dirty"]
    )

    assert result == 0
    assert captured["root"] == tmp_path
    assert captured["task"] == "Implement the bounded assignment."


def test_stale_lock_is_recovered(tmp_path):
    lock = tmp_path / "active.lock"
    lock.write_text("pid=99999999 run=old\n", encoding="utf-8")

    ai_flow.acquire_lock(lock, "new")

    assert f"pid={ai_flow.os.getpid()}" in lock.read_text(encoding="utf-8")


def test_flow_repairs_failed_checks_and_review_findings(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")

    codex_results = iter(
        [
            {"status": "READY", "summary": "plan", "blocked_reason": ""},
            {
                "status": "CHANGES_REQUIRED",
                "summary": "fix",
                "findings": [
                    {"severity": "high", "required_change": f"repair {number}"}
                    for number in range(1, 5)
                ],
            },
            {"status": "APPROVED", "summary": "done", "findings": []},
        ]
    )
    claude_roles = []
    review_repair_prompts = []

    def fake_codex(*args, **_kwargs):
        result = next(codex_results)
        invocation = args[6]
        if invocation.startswith("review-"):
            (args[3] / f"{invocation}.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
        return result

    def fake_claude(_config, role_name, *args, **_kwargs):
        claude_roles.append(role_name)
        if role_name == "fixer" and args[3].startswith("review-fix-"):
            review_repair_prompts.append(args[2])
        return {"status": "IMPLEMENTED", "summary": "ok", "blockers": []}

    check_results = iter(
        [
            [{"command": "check", "returncode": 1, "stdout": "", "stderr": "bad"}],
            [{"command": "check", "returncode": 0, "stdout": "", "stderr": ""}],
            [{"command": "check", "returncode": 0, "stdout": "", "stderr": ""}],
        ]
    )
    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_claude)
    monkeypatch.setattr(ai_flow, "run_checks", lambda *_args, **_kwargs: next(check_results))

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "implement feature",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert claude_roles == ["worker", "fixer", "fixer"]
    assert '"selected": 4' in review_repair_prompts[0]
    assert "repair 4" in review_repair_prompts[0]
    assert not (tmp_path / ".ai-flow" / "active.lock").exists()


def test_ready_plan_continues_even_when_blocked_reason_contains_advisory_text(
    tmp_path, monkeypatch
):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")

    codex_results = iter(
        [
            {
                "status": "READY",
                "summary": "plan",
                "blocked_reason": "No current planning blocker.",
            },
            {"status": "APPROVED", "summary": "done", "findings": []},
        ]
    )
    worker_calls = []
    monkeypatch.setattr(
        ai_flow, "run_codex_role", lambda *_args, **_kwargs: next(codex_results)
    )
    monkeypatch.setattr(
        ai_flow,
        "run_work_role",
        lambda *_args, **_kwargs: worker_calls.append(True)
        or {"status": "IMPLEMENTED", "summary": "ok", "blockers": []},
    )
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_args, **_kwargs: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "implement feature",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert worker_calls == [True]


def _plan_status_flow(tmp_path, monkeypatch, plan):
    """Drive run_flow far enough to exercise plan-status routing only."""
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(ai_flow, "run_codex_role", lambda *_a, **_k: plan)
    worker_calls = []
    monkeypatch.setattr(
        ai_flow,
        "run_work_role",
        lambda *_a, **_k: worker_calls.append(True)
        or {"status": "IMPLEMENTED", "summary": "ok", "blockers": []},
    )
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )
    return config, worker_calls


def test_a_blocked_plan_stops_before_the_worker_runs(tmp_path, monkeypatch):
    """Only `status` decides; the worker must not be started."""
    config, worker_calls = _plan_status_flow(
        tmp_path,
        monkeypatch,
        {
            "status": "BLOCKED",
            "summary": "plan",
            "blocked_reason": "needs a credential the run does not have",
        },
    )

    result = ai_flow.run_flow(
        config, tmp_path, "implement feature", allow_dirty=False,
        dry_run=False, extra_checks=(), no_checks=False,
    )

    assert result == ai_flow.EXIT_BLOCKED
    assert worker_calls == []


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blocked_plan_without_a_reason_fails_instead_of_inventing_one(
    tmp_path, monkeypatch, reason
):
    """BLOCKED is the ONE status where the reason is genuinely required.

    Substituting a placeholder - which this flow used to do - ends the run
    with a message that names no blocker, so an unanswerable question and a
    planner that forgot to fill the field in look identical to the operator.
    """
    config, worker_calls = _plan_status_flow(
        tmp_path, monkeypatch,
        {"status": "BLOCKED", "summary": "plan", "blocked_reason": reason},
    )

    with pytest.raises(ai_flow.FlowError, match="blocked_reason"):
        ai_flow.run_flow(
            config, tmp_path, "implement feature", allow_dirty=False,
            dry_run=False, extra_checks=(), no_checks=False,
        )
    assert worker_calls == []


def test_a_ready_plan_needs_no_blocker_text_at_all(tmp_path, monkeypatch):
    """READY routing reads status only; the field may be absent entirely.

    The shared stub answers every Codex role with the same plan, so the run
    stops at the reviewer with an unknown status. That is past the decision
    under test: reaching it at all proves the worker ran.
    """
    config, worker_calls = _plan_status_flow(
        tmp_path, monkeypatch, {"status": "READY", "summary": "plan"},
    )

    with pytest.raises(ai_flow.FlowError, match="Reviewer"):
        ai_flow.run_flow(
            config, tmp_path, "implement feature", allow_dirty=False,
            dry_run=False, extra_checks=(), no_checks=False,
        )

    assert worker_calls


@pytest.mark.parametrize("status", [None, "", "ready", "MAYBE", "APPROVED"])
def test_an_unknown_plan_status_fails_instead_of_guessing(
    tmp_path, monkeypatch, status
):
    """A status the flow does not recognise must not be read as READY."""
    plan = {"summary": "plan", "blocked_reason": ""}
    if status is not None:
        plan["status"] = status
    config, worker_calls = _plan_status_flow(tmp_path, monkeypatch, plan)

    with pytest.raises(ai_flow.FlowError):
        ai_flow.run_flow(
            config, tmp_path, "implement feature", allow_dirty=False,
            dry_run=False, extra_checks=(), no_checks=False,
        )
    assert worker_calls == []


@pytest.mark.parametrize("reason", [None, 0, [], {"a": 1}, True])
def test_a_blocked_plan_with_a_non_string_reason_fails_instead_of_coercing(
    tmp_path, monkeypatch, reason
):
    """`str(None)` is the truthy text "None" - coercion would let it pass."""
    plan = {"status": "BLOCKED", "summary": "plan"}
    if reason is not None:
        plan["blocked_reason"] = reason
    config, worker_calls = _plan_status_flow(tmp_path, monkeypatch, plan)

    with pytest.raises(ai_flow.FlowError, match="blocked_reason"):
        ai_flow.run_flow(
            config, tmp_path, "implement feature", allow_dirty=False,
            dry_run=False, extra_checks=(), no_checks=False,
        )
    assert worker_calls == []


def _plan_schema():
    return json.loads(
        (ROOT / ".ai-flow" / "schemas" / "plan.json").read_text(encoding="utf-8")
    )


def test_the_plan_schema_makes_the_blocker_conditional_not_universal():
    """The canonical schema states the contract it actually has.

    `blocked_reason` is required when - and only when - the status is
    BLOCKED, and then it must hold at least one non-whitespace character.
    """
    schema = _plan_schema()

    assert "blocked_reason" not in schema["required"]
    (conditional,) = schema["allOf"]
    assert conditional["if"]["properties"]["status"]["const"] == "BLOCKED"
    assert conditional["if"]["required"] == ["status"]
    assert conditional["then"]["required"] == ["blocked_reason"]
    constrained = conditional["then"]["properties"]["blocked_reason"]
    assert constrained["minLength"] >= 1
    assert constrained["pattern"] == r"\S"


def test_the_plan_schema_requires_status_and_keeps_every_other_constraint():
    schema = _plan_schema()

    assert schema["properties"]["status"]["enum"] == ["READY", "BLOCKED"]
    assert schema["additionalProperties"] is False
    for field in (
        "status", "summary", "implementation_steps", "likely_files",
        "acceptance_criteria", "suggested_checks", "risks",
    ):
        assert field in schema["required"], field
    assert schema["properties"]["blocked_reason"]["type"] == "string"


@pytest.mark.parametrize(
    "document, valid",
    [
        ({}, True),
        ({"blocked_reason": "advisory text"}, True),
        ({"status": "BLOCKED", "blocked_reason": "needs a credential"}, True),
        ({"status": "BLOCKED"}, False),
        ({"status": "BLOCKED", "blocked_reason": ""}, False),
        ({"status": "BLOCKED", "blocked_reason": "  \t\n "}, False),
        ({"status": "MAYBE"}, False),
        ({"unexpected": 1}, False),
    ],
)
def test_the_plan_schema_validates_the_conditional_contract(document, valid):
    """Check the schema against a real validator, not only its shape."""
    jsonschema = pytest.importorskip("jsonschema")
    plan = {
        "status": "READY", "summary": "s", "implementation_steps": [],
        "likely_files": [], "acceptance_criteria": [], "suggested_checks": [],
        "risks": [],
    }
    plan.update(document)

    try:
        jsonschema.validate(plan, _plan_schema())
    except jsonschema.ValidationError:
        assert not valid, plan
    else:
        assert valid, plan


def test_the_codex_wire_schema_is_strict_without_weakening_the_canonical_one(
    tmp_path, monkeypatch
):
    """Codex takes strict objects only, so the conditional is translated.

    Flattening the canonical file instead would delete a real contract to
    satisfy a transport limitation. `run_flow` re-checks the same condition
    on the returned object, so nothing goes unpoliced.
    """
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    role = _config().roles["planner"]
    canonical_path = ROOT / ".ai-flow" / "schemas" / "plan.json"

    command, _found = ai_flow.build_codex_command(
        role, ROOT, canonical_path, tmp_path / "plan.json", "plan this"
    )

    sent_path = Path(command[command.index("--output-schema") + 1])
    assert sent_path != canonical_path
    sent = json.loads(sent_path.read_text(encoding="utf-8"))
    assert sent["required"] == list(sent["properties"])
    assert "blocked_reason" in sent["required"]
    assert sent["additionalProperties"] is False
    assert "allOf" not in sent
    assert sent["properties"]["status"]["enum"] == ["READY", "BLOCKED"]
    # The source of truth is untouched by having been transported.
    assert json.loads(canonical_path.read_text(encoding="utf-8")) == _plan_schema()


def test_the_transport_copy_drops_conditionals_at_every_depth():
    strict = ai_flow.strict_transport_schema(
        {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {
                    "type": "object",
                    "properties": {"c": {"type": "string"}},
                    "required": [],
                    "if": {"const": 1},
                },
            },
            "required": ["a"],
            "allOf": [{"if": {"const": 1}, "then": {"required": ["a"]}}],
        }
    )

    assert strict["required"] == ["a", "b"]
    assert strict["additionalProperties"] is False
    assert "allOf" not in strict
    assert "if" not in strict["properties"]["b"]
    assert strict["properties"]["b"]["required"] == ["c"]
    assert strict["properties"]["b"]["additionalProperties"] is False


def test_the_transport_copy_keeps_constraints_the_wire_can_carry():
    """Strict structured output accepts `anyOf`; dropping it would weaken it."""
    strict = ai_flow.strict_transport_schema(
        {
            "type": "object",
            "properties": {
                "a": {"anyOf": [{"type": "string"}, {"type": "integer"}]}
            },
            "required": ["a"],
        }
    )

    assert strict["properties"]["a"]["anyOf"] == [
        {"type": "string"}, {"type": "integer"}
    ]


def test_dry_run_shows_the_checks_the_real_run_would_execute(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow, "run_codex_role", lambda *_a, **_k: {})
    monkeypatch.setattr(ai_flow, "run_work_role", lambda *_a, **_k: {})
    seen: list[tuple[tuple[str, ...], ...]] = []
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda commands, *_a, **_k: seen.append(tuple(commands)) or []
    )

    ai_flow.run_flow(
        config,
        tmp_path,
        "inspect routing",
        allow_dirty=False,
        dry_run=True,
        extra_checks=(("python3", "-m", "pytest", "-q"),),
        no_checks=False,
    )

    assert seen == [(*config.checks, ("python3", "-m", "pytest", "-q"))]


def test_dry_run_honours_no_checks(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / ".ai-flow").mkdir()
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow, "run_codex_role", lambda *_a, **_k: {})
    monkeypatch.setattr(ai_flow, "run_work_role", lambda *_a, **_k: {})
    seen: list[tuple[tuple[str, ...], ...]] = []
    monkeypatch.setattr(
        ai_flow, "run_checks", lambda commands, *_a, **_k: seen.append(tuple(commands)) or []
    )

    ai_flow.run_flow(
        config,
        tmp_path,
        "inspect routing",
        allow_dirty=False,
        dry_run=True,
        extra_checks=(),
        no_checks=True,
    )

    assert seen == [()]


def test_failed_check_reports_its_real_exit_code_and_output(tmp_path):
    outcomes = ai_flow.run_checks(
        [("python3", "-c", "import sys; print('boom'); sys.exit(7)")],
        tmp_path,
        tmp_path,
        timeout=60,
        cycle=1,
        dry_run=False,
    )

    assert [item["returncode"] for item in outcomes] == [7]
    assert "boom" in outcomes[0]["stdout"]


def test_legacy_run_resumes_at_missing_review_repair_without_replanning(tmp_path):
    run_dir = tmp_path / "20260828-123359-15282"
    run_dir.mkdir()
    (run_dir / "plan.json").write_text(
        json.dumps({"status": "READY", "summary": "plan"}), encoding="utf-8"
    )
    (run_dir / "worker-01.json").write_text(
        json.dumps({"status": "IMPLEMENTED", "summary": "work"}), encoding="utf-8"
    )
    (run_dir / "checks-01.json").write_text("[]", encoding="utf-8")
    (run_dir / "review-01.json").write_text(
        json.dumps(
            {
                "status": "CHANGES_REQUIRED",
                "summary": "repair",
                "findings": [{"severity": "high", "required_change": "fix"}],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "review-fix-01.log").write_text("interrupted", encoding="utf-8")

    state = ai_flow._infer_legacy_state(run_dir, "continue")

    assert state["next_stage"] == "review_fix"
    assert state["execution_cycle"] == 1
    assert state["review_cycles"] == 1
    assert state["claude_sessions"] == {}


def test_resume_run_skips_completed_planner_worker_and_review(tmp_path, monkeypatch):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    run_id = "20260828-123359-15282"
    run_dir = tmp_path / ".ai-flow" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task.txt").write_text("continue safely\n", encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps({"status": "READY", "summary": "plan"}), encoding="utf-8"
    )
    (run_dir / "worker-01.json").write_text(
        json.dumps({"status": "IMPLEMENTED", "summary": "work"}), encoding="utf-8"
    )
    (run_dir / "checks-01.json").write_text("[]", encoding="utf-8")
    (run_dir / "review-01.json").write_text(
        json.dumps(
            {
                "status": "CHANGES_REQUIRED",
                "summary": "repair",
                "findings": [{"severity": "high", "required_change": "fix"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "dirty\n")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    codex_roles = []
    claude_roles = []

    def fake_codex(_config, role_name, *_args, **_kwargs):
        codex_roles.append(role_name)
        return {"status": "APPROVED", "summary": "done", "findings": []}

    def fake_claude(_config, role_name, *_args, **_kwargs):
        claude_roles.append(role_name)
        return {"status": "IMPLEMENTED", "summary": "fixed", "blockers": []}

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_claude)
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
        resume_run_id=run_id,
        fresh_stage=True,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert claude_roles == ["fixer"]
    assert codex_roles == ["reviewer"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "APPROVED"
    assert state["next_stage"] == "complete"


def test_fresh_run_reuses_ready_plan_but_keeps_new_task_for_worker_and_review(
    tmp_path, monkeypatch
):
    _install_plan_schema(tmp_path)
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    source_id = "20260901-000000-100"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    source_plan = _ready_plan("preserved phase plan")
    source_plan["implementation_steps"] = ["repair the bounded findings"]
    (source_dir / "plan.json").write_text(
        json.dumps(source_plan), encoding="utf-8"
    )
    (source_dir / "state.json").write_text(
        json.dumps(_staged_source_state()),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    codex_calls = []
    work_prompts = []

    def fake_codex(_config, role, *_args, **_kwargs):
        codex_calls.append(role)
        if role == "planner":
            raise AssertionError("reused READY plan must skip the planner")
        return {"status": "APPROVED", "summary": "approved", "findings": []}

    def fake_work(_config, role, _root, _run_dir, prompt, *_args, **_kwargs):
        work_prompts.append((role, prompt))
        return {
            "status": "IMPLEMENTED",
            "summary": "done",
            "changed_files": [],
            "checks": [],
            "blockers": [],
        }

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "repair the five new findings",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
        reuse_plan_run_id=source_id,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert codex_calls == ["reviewer"]
    assert work_prompts and "repair the five new findings" in work_prompts[0][1]
    assert "preserved phase plan" in work_prompts[0][1]
    new_dir = next(
        path for path in (tmp_path / ".ai-flow" / "runs").iterdir()
        if path.name != source_id
    )
    state = json.loads((new_dir / "state.json").read_text(encoding="utf-8"))
    assert state["reused_plan"]["source_run_id"] == source_id
    assert len(state["reused_plan"]["source_sha256"]) == 64
    assert len(state["reused_plan"]["target_sha256"]) == 64
    assert json.loads((new_dir / "plan.json").read_text(encoding="utf-8")) == source_plan


def test_reuse_plan_rejects_non_ready_source(tmp_path):
    _install_plan_schema(tmp_path)
    source_id = "20260901-000000-101"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    (source_dir / "plan.json").write_text(
        json.dumps({"status": "BLOCKED", "summary": "no"}), encoding="utf-8"
    )
    (source_dir / "state.json").write_text(
        json.dumps(_staged_source_state()),
        encoding="utf-8",
    )
    target_dir = tmp_path / ".ai-flow" / "runs" / "new"
    target_dir.mkdir()

    with pytest.raises(ai_flow.FlowError, match="status READY"):
        ai_flow.reuse_ready_plan(tmp_path, target_dir, source_id)


def test_reused_plan_binding_rejects_source_or_target_drift(tmp_path):
    _install_plan_schema(tmp_path)
    source_id = "20260901-000000-102"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    plan = _ready_plan()
    source_path = source_dir / "plan.json"
    source_path.write_text(json.dumps(plan), encoding="utf-8")
    (source_dir / "state.json").write_text(
        json.dumps(_staged_source_state()),
        encoding="utf-8",
    )
    target_dir = tmp_path / ".ai-flow" / "runs" / "new"
    target_dir.mkdir()
    binding = ai_flow.reuse_ready_plan(tmp_path, target_dir, source_id)

    (target_dir / "plan.json").write_text(
        json.dumps({**plan, "summary": "tampered"}), encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="target bytes drifted"):
        ai_flow.verify_reused_plan_binding(tmp_path, target_dir, binding)

    (target_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    source_path.write_text(json.dumps({**plan, "summary": "source drift"}),
                           encoding="utf-8")
    with pytest.raises(ai_flow.FlowError, match="source bytes drifted"):
        ai_flow.verify_reused_plan_binding(tmp_path, target_dir, binding)


def test_staged_v3_reuse_sends_new_task_to_worker_and_both_code_reviews(
    tmp_path, monkeypatch
):
    """Exercise reuse through the real staged-v3 controller boundary.

    The evidence actor is deliberately stopped immediately after CODE_APPROVED;
    this proves that both code reviews and the new task occur before any
    evidence generation, without manufacturing a Phase 3--7 result.
    """
    _install_plan_schema(tmp_path)
    config = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.toml", ROOT
    )
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    upstream_id = "20260901-000000-996"
    source_id = "20260901-000000-998"
    upstream_dir = tmp_path / ".ai-flow" / "runs" / upstream_id
    upstream_dir.mkdir(parents=True)
    (tmp_path / ".ai-flow" / "runs" / source_id).mkdir(parents=True)
    (tmp_path / "validation").mkdir()
    upstream_plan = _ready_plan("hash-bound staged plan")
    upstream_plan["implementation_steps"] = ["repair the complete finding set"]
    (upstream_dir / "plan.json").write_text(
        json.dumps(upstream_plan), encoding="utf-8"
    )
    upstream_state = _staged_source_state()
    (upstream_dir / "state.json").write_text(
        json.dumps(upstream_state), encoding="utf-8"
    )
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    ancestry = ai_flow.reuse_ready_plan(tmp_path, source_dir, upstream_id)
    (source_dir / "state.json").write_text(
        json.dumps({**_staged_source_state(),
                    "reused_plan": ancestry}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    reviewer_invocations = []
    reviewer_prompts = []
    worker_prompts = []
    inventory_at_second_review = None
    evidence_generations_at_second_review = None

    def fake_codex(_config, role, _root, run_dir, _schema, _prompt,
                   invocation, *_args, **_kwargs):
        nonlocal inventory_at_second_review, evidence_generations_at_second_review
        if role == "planner":
            raise AssertionError("--reuse-plan-from must not run the planner")
        if role == "reviewer":
            reviewer_invocations.append(invocation)
            reviewer_prompts.append((invocation, _prompt))
        if invocation == "code-review-01":
            result = {
                "status": "CHANGES_REQUIRED",
                "summary": "one bounded repair remains",
                "findings": [{
                    "severity": "high", "file": "source.py",
                    "evidence": "controlled review finding",
                    "required_change": "complete the repair",
                }],
                "blocked_reason": "",
            }
            (run_dir / f"{invocation}.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            return result
        if invocation == "code-review-02":
            inventory_at_second_review = ai_flow.evidence_inventory(
                tmp_path, config.evidence_policy.registration_globs
            )
            evidence_generations_at_second_review = json.loads(
                (run_dir / "state.json").read_text(encoding="utf-8")
            )["evidence_generations"]
            return {
                "status": "APPROVED", "summary": "code approved",
                "findings": [], "blocked_reason": "",
            }
        return {
            "status": "APPROVED", "summary": "unexpected reviewer",
            "findings": [], "blocked_reason": "",
        }

    def fake_work(_config, role, root, _run_dir, prompt, invocation,
                  *_args, **_kwargs):
        if invocation == "worker-01":
            worker_prompts.append(prompt)
        if invocation == "evidence-01":
            current_inventory = ai_flow.evidence_inventory(
                root, config.evidence_policy.registration_globs
            )
            assert current_inventory == inventory_at_second_review
            assert all(not entries for entries in current_inventory.values())
            return {
                "status": "BLOCKED", "summary": "test stops at approval",
                "findings": [], "blockers": ["test boundary"],
            }
        return {
            "status": "IMPLEMENTED", "summary": f"{role} completed",
            "changed_files": [], "checks": [], "blockers": [],
        }

    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_work_role", fake_work)
    monkeypatch.setattr(
        ai_flow, "run_checks",
        lambda *_args, **_kwargs: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config, tmp_path, "new repair task", allow_dirty=False, dry_run=False,
        extra_checks=(), no_checks=False, reuse_plan_run_id=source_id,
    )

    assert result == ai_flow.EXIT_BLOCKED
    assert reviewer_invocations == ["code-review-01", "code-review-02"]
    assert worker_prompts and "new repair task" in worker_prompts[0]
    assert all("new repair task" in prompt for _, prompt in reviewer_prompts)
    assert inventory_at_second_review is not None
    assert all(not entries for entries in inventory_at_second_review.values())
    assert evidence_generations_at_second_review == 0
    assert not list((tmp_path / "validation").glob("*.json"))
    run_dir = next(
        path for path in (tmp_path / ".ai-flow" / "runs").iterdir()
        if (path / "task.txt").is_file()
        and path.name not in {source_id, upstream_id}
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["reused_plan"]["source_run_id"] == source_id
    assert state["evidence_generations"] == 1


@pytest.mark.parametrize("drift_target", ["upstream", "middle"])
def test_chained_reuse_rejects_upstream_or_middle_target_drift(tmp_path, drift_target):
    _install_plan_schema(tmp_path)
    upstream_id = "20260901-000000-994"
    middle_id = "20260901-000000-995"
    upstream_dir = tmp_path / ".ai-flow" / "runs" / upstream_id
    middle_dir = tmp_path / ".ai-flow" / "runs" / middle_id
    upstream_dir.mkdir(parents=True)
    middle_dir.mkdir(parents=True)
    plan = _ready_plan("chain plan")
    upstream_path = upstream_dir / "plan.json"
    upstream_path.write_text(json.dumps(plan), encoding="utf-8")
    (upstream_dir / "state.json").write_text(
        json.dumps(_staged_source_state()),
        encoding="utf-8",
    )
    middle_binding = ai_flow.reuse_ready_plan(
        tmp_path, middle_dir, upstream_id
    )
    (middle_dir / "state.json").write_text(
        json.dumps({**_staged_source_state(),
                    "reused_plan": middle_binding}),
        encoding="utf-8",
    )
    if drift_target == "upstream":
        upstream_path.write_text(json.dumps({**plan, "summary": "drift"}),
                                 encoding="utf-8")
    else:
        (middle_dir / "plan.json").write_text(
            json.dumps({**plan, "summary": "drift"}), encoding="utf-8"
        )
    target_dir = tmp_path / ".ai-flow" / "runs" / "target"
    target_dir.mkdir()
    with pytest.raises(ai_flow.FlowError, match="drifted"):
        ai_flow.reuse_ready_plan(tmp_path, target_dir, middle_id)


@pytest.mark.parametrize(
    ("plan", "state", "message"),
    [
        (
            {"status": "READY", "summary": "missing implementation steps"},
            _staged_source_state(),
            "violates canonical schema",
        ),
        (
            _ready_plan("incompatible protocol"),
            {"workflow_protocol": "legacy_protocol"},
            "incompatible workflow protocol",
        ),
        (
            {**_ready_plan("invalid type"), "likely_files": "source.py"},
            _staged_source_state(),
            "array of strings",
        ),
        (
            {**_ready_plan("extra field"), "invented": True},
            _staged_source_state(),
            "violates canonical schema",
        ),
        (
            _ready_plan("missing state"),
            None,
            "source state is missing or malformed",
        ),
        (
            _ready_plan("malformed state"),
            [],
            "source run state output must be a JSON object",
        ),
        (
            _ready_plan("unknown staged stage"),
            _staged_source_state(next_stage="definitely-not-a-stage"),
            "invalid next_stage",
        ),
        (
            _ready_plan("malformed timestamp"),
            _staged_source_state(started_at="not-a-timestamp"),
            "invalid started_at",
        ),
        (
            _ready_plan("timezone-less timestamp"),
            _staged_source_state(started_at="2026-09-01T00:00:00"),
            "started_at must include a timezone",
        ),
        (
            _ready_plan("running complete state"),
            _staged_source_state(status="RUNNING", next_stage="complete"),
            "must be terminal before adoption",
        ),
        (
            _ready_plan("running worker state"),
            _staged_source_state(status="RUNNING", next_stage="worker"),
            "must be terminal before adoption",
        ),
        (
            _ready_plan("approved mid-stage state"),
            _staged_source_state(status="APPROVED", next_stage="evidence"),
            "terminal status before complete stage",
        ),
        (
            _ready_plan("terminal active state"),
            _staged_source_state(
                status="BLOCKED", next_stage="complete",
                active_invocation="worker-01",
            ),
            "active work after terminal status",
        ),
        (
            _ready_plan("empty active invocation"),
            _staged_source_state(active_invocation=""),
            "invalid active_invocation",
        ),
    ],
)
def test_reuse_plan_rejects_invalid_ready_provenance(
    tmp_path, plan, state, message
):
    _install_plan_schema(tmp_path)
    source_id = "20260901-000000-997"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    (source_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    if state is not None:
        (source_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    target_dir = tmp_path / ".ai-flow" / "runs" / "new"
    target_dir.mkdir()

    with pytest.raises(ai_flow.FlowError, match=message):
        ai_flow.reuse_ready_plan(tmp_path, target_dir, source_id)


def test_reused_plan_binding_rejects_malformed_digest_or_timestamp(tmp_path):
    _install_plan_schema(tmp_path)
    source_id = "20260901-000000-998"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    source_dir.joinpath("plan.json").write_text(
        json.dumps(_ready_plan()), encoding="utf-8"
    )
    source_dir.joinpath("state.json").write_text(
        json.dumps(_staged_source_state()), encoding="utf-8"
    )
    target_dir = tmp_path / ".ai-flow" / "runs" / "new"
    target_dir.mkdir()
    binding = ai_flow.reuse_ready_plan(tmp_path, target_dir, source_id)

    # Positive control: the producer's own binding verifies, so every rejection
    # below is caused by the tampering rather than by an unrelated failure.
    ai_flow.verify_reused_plan_binding(tmp_path, target_dir, binding)
    assert any(character.isalpha() for character in binding["target_sha256"])

    for field, value, message in (
        ("source_sha256", "z" * 64, "binding is incomplete"),
        ("source_sha256", binding["source_sha256"][:63], "binding is incomplete"),
        ("target_sha256", binding["target_sha256"].upper(), "binding is incomplete"),
        ("reused_at", None, "binding is incomplete"),
        ("reused_at", "not-a-timestamp", "invalid reused_at"),
        (
            "reused_at",
            "2026-09-01T00:00:00",
            "reused_at must include a timezone",
        ),
    ):
        tampered = {**binding, field: value}
        with pytest.raises(ai_flow.FlowError, match=message):
            ai_flow.verify_reused_plan_binding(tmp_path, target_dir, tampered)

    # A repaired binding must still verify, so the checks above reject only the
    # tampered field and do not leave the boundary permanently closed.
    ai_flow.verify_reused_plan_binding(tmp_path, target_dir, binding)


def test_chained_reuse_rejects_a_non_terminal_ancestor_state(tmp_path):
    """The terminal-source rule must hold through the ancestry recursion.

    Validating only the immediate source would let an in-flight upstream run,
    whose plan.json is still writable by its own controller, reach a fresh run
    one hop later behind a terminal-looking middle record.
    """
    _install_plan_schema(tmp_path)
    upstream_id = "20260901-000000-990"
    middle_id = "20260901-000000-991"
    upstream_dir = tmp_path / ".ai-flow" / "runs" / upstream_id
    middle_dir = tmp_path / ".ai-flow" / "runs" / middle_id
    upstream_dir.mkdir(parents=True)
    middle_dir.mkdir(parents=True)
    upstream_state_path = upstream_dir / "state.json"
    (upstream_dir / "plan.json").write_text(
        json.dumps(_ready_plan("ancestor plan")), encoding="utf-8"
    )
    upstream_state_path.write_text(
        json.dumps(_staged_source_state()), encoding="utf-8"
    )
    middle_binding = ai_flow.reuse_ready_plan(tmp_path, middle_dir, upstream_id)
    (middle_dir / "state.json").write_text(
        json.dumps({**_staged_source_state(), "reused_plan": middle_binding}),
        encoding="utf-8",
    )
    target_dir = tmp_path / ".ai-flow" / "runs" / "target"
    target_dir.mkdir()

    # Only the ancestor reverts to in-flight; the middle record stays terminal
    # and its bytes are untouched, so nothing except the recursion can catch it.
    upstream_state_path.write_text(
        json.dumps(
            _staged_source_state(
                status="RUNNING",
                next_stage="evidence",
                active_invocation="evidence-01",
                active_session_id="session-1",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ai_flow.FlowError, match="must be terminal before adoption"
    ):
        ai_flow.reuse_ready_plan(tmp_path, target_dir, middle_id)
    assert not (target_dir / "plan.json").exists()


def test_reused_plan_ancestry_rejects_a_cycle_instead_of_recursing(tmp_path):
    """A mutually referencing ancestry must fail closed, not exhaust the stack.

    Every digest below is genuine, so nothing except the cycle guard stops the
    walk; without it the controller would crash with a RecursionError whose
    message says nothing about provenance.
    """
    _install_plan_schema(tmp_path)
    first_id = "20260901-000000-988"
    second_id = "20260901-000000-989"
    runs = tmp_path / ".ai-flow" / "runs"
    first_dir = runs / first_id
    second_dir = runs / second_id
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    plan_bytes = json.dumps(_ready_plan("cyclic plan")).encode("utf-8")
    for run_dir in (first_dir, second_dir):
        (run_dir / "plan.json").write_bytes(plan_bytes)
    digest = hashlib.sha256(plan_bytes).hexdigest()

    def binding(source_run_id):
        return {
            "source_run_id": source_run_id,
            "source_path": f".ai-flow/runs/{source_run_id}/plan.json",
            "source_sha256": digest,
            "target_sha256": digest,
            "reused_at": "2026-09-01T00:00:00+00:00",
        }

    (first_dir / "state.json").write_text(
        json.dumps({**_staged_source_state(),
                    "reused_plan": binding(second_id)}),
        encoding="utf-8",
    )
    (second_dir / "state.json").write_text(
        json.dumps({**_staged_source_state(),
                    "reused_plan": binding(first_id)}),
        encoding="utf-8",
    )
    target_dir = runs / "target"
    target_dir.mkdir()

    with pytest.raises(ai_flow.FlowError, match="ancestry contains a cycle"):
        ai_flow.reuse_ready_plan(tmp_path, target_dir, first_id)
    assert not (target_dir / "plan.json").exists()


def test_staged_resume_fails_closed_when_the_reused_plan_source_drifted(
    tmp_path, monkeypatch
):
    """An interrupted staged-v3 run re-verifies plan provenance on resume.

    The binding is checked once when the plan is adopted, but the source run
    stays writable afterwards.  A resume that trusted only the persisted record
    would continue a source-frozen protocol on bytes nobody reviewed, so the
    controller must stop before any stage actor is invoked.
    """
    _install_plan_schema(tmp_path)
    config = ai_flow.load_config(
        ROOT / ".ai-flow" / "config.complete-subhour.opus.toml", ROOT
    )
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    (tmp_path / "validation").mkdir()
    source_id = "20260901-000000-992"
    source_dir = tmp_path / ".ai-flow" / "runs" / source_id
    source_dir.mkdir(parents=True)
    source_plan_path = source_dir / "plan.json"
    plan = _ready_plan("resumed provenance plan")
    source_plan_path.write_text(json.dumps(plan), encoding="utf-8")
    (source_dir / "state.json").write_text(
        json.dumps(_staged_source_state()), encoding="utf-8"
    )
    run_id = "20260901-000000-993"
    run_dir = tmp_path / ".ai-flow" / "runs" / run_id
    run_dir.mkdir()
    (run_dir / "task.txt").write_text("resumed staged task\n", encoding="utf-8")
    binding = ai_flow.reuse_ready_plan(tmp_path, run_dir, source_id)
    (run_dir / "state.json").write_text(
        json.dumps({
            "schema_version": 1,
            "status": "RUNNING",
            "task": "resumed staged task",
            "next_stage": "code_checks",
            "execution_cycle": 1,
            "review_cycles": 0,
            "test_fix_cycles": 0,
            "evidence_generations": 0,
            "active_invocation": None,
            "active_session_id": None,
            "claude_sessions": {},
            "claude_usage": {},
            "started_at": "2026-09-01T00:00:00+00:00",
            "workflow_protocol": "staged_evidence_v3",
            "reused_plan": binding,
        }),
        encoding="utf-8",
    )
    (run_dir / "worker-01.json").write_text(
        json.dumps({
            "status": "IMPLEMENTED", "summary": "work",
            "changed_files": [], "checks": [], "blockers": [],
        }),
        encoding="utf-8",
    )
    # The source run directory is not frozen; its controller may rewrite the
    # plan after this run adopted it.
    source_plan_path.write_text(
        json.dumps({**plan, "summary": "rewritten after adoption"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")

    def refuse(*_args, **_kwargs):
        raise AssertionError("no stage may run on drifted plan provenance")

    monkeypatch.setattr(ai_flow, "run_codex_role", refuse)
    monkeypatch.setattr(ai_flow, "run_work_role", refuse)
    monkeypatch.setattr(ai_flow, "run_checks", refuse)

    with pytest.raises(ai_flow.FlowError, match="source bytes drifted"):
        ai_flow.run_flow(
            config, tmp_path, "", allow_dirty=False, dry_run=False,
            extra_checks=(), no_checks=False, resume_run_id=run_id,
        )

    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ERROR"
    assert "drifted" in status["summary"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["next_stage"] == "code_checks"
    assert state["evidence_generations"] == 0
    assert not list((tmp_path / "validation").glob("*.json"))
    assert not (tmp_path / ".ai-flow" / "active.lock").exists()


def test_explicit_extension_reopens_only_the_terminal_changes_required_review(
    tmp_path, monkeypatch
):
    config = _config()
    (tmp_path / "AGENTS.md").write_text("# Test agents\n", encoding="utf-8")
    run_id = "20260828-123359-15282"
    run_dir = tmp_path / ".ai-flow" / "runs" / run_id
    run_dir.mkdir(parents=True)
    task = "continue safely"
    (run_dir / "task.txt").write_text(task + "\n", encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps({"status": "READY", "summary": "plan"}), encoding="utf-8"
    )
    (run_dir / "worker-01.json").write_text(
        json.dumps({"status": "IMPLEMENTED", "summary": "work"}), encoding="utf-8"
    )
    (run_dir / "checks-03.json").write_text("[]", encoding="utf-8")
    (run_dir / "review-03.json").write_text(
        json.dumps(
            {
                "status": "CHANGES_REQUIRED",
                "summary": "more repair",
                "findings": [{"severity": "high", "required_change": "fix"}],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "BLOCKED",
                "task": task,
                "next_stage": "complete",
                "execution_cycle": 3,
                "review_cycles": 3,
                "test_fix_cycles": 0,
                "review_cycle_limit": 3,
                "claude_sessions": {},
                "claude_usage": {},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "status.json").write_text(
        json.dumps({"status": "BLOCKED", "summary": "review limit"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_flow, "git_status", lambda _root: "dirty\n")
    monkeypatch.setattr(ai_flow.shutil, "which", lambda command: f"/bin/{command}")
    roles = []
    monkeypatch.setattr(
        ai_flow,
        "run_work_role",
        lambda _config, role, *_a, **_k: roles.append(role)
        or {"status": "IMPLEMENTED", "summary": "fixed", "blockers": []},
    )
    monkeypatch.setattr(
        ai_flow,
        "run_codex_role",
        lambda _config, role, *_a, **_k: roles.append(role)
        or {"status": "APPROVED", "summary": "done", "findings": []},
    )
    monkeypatch.setattr(
        ai_flow,
        "run_checks",
        lambda *_a, **_k: [
            {"command": "check", "returncode": 0, "stdout": "", "stderr": ""}
        ],
    )

    result = ai_flow.run_flow(
        config,
        tmp_path,
        "",
        allow_dirty=False,
        dry_run=False,
        extra_checks=(),
        no_checks=False,
        resume_run_id=run_id,
        additional_review_cycles=2,
        use_fallback_fixer=True,
    )

    assert result == ai_flow.EXIT_APPROVED
    assert roles == ["fixer_fallback", "reviewer"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["review_cycle_limit"] == 5
    assert state["review_cycles"] == 4


def test_keyboard_interrupt_reaps_owned_process_group_and_keeps_log(
    tmp_path, monkeypatch
):
    class FakeProcess:
        pid = 43210
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            self.returncode = -15
            return "partial stdout", "partial stderr"

        def poll(self):
            return self.returncode

    process = FakeProcess()
    killed = []
    monkeypatch.setattr(ai_flow.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(ai_flow.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    log = tmp_path / "child.log"

    with pytest.raises(KeyboardInterrupt):
        ai_flow.run_process(["claude"], tmp_path, 60, log, dry_run=False)

    assert killed == [(43210, ai_flow.signal.SIGTERM)]
    assert "partial stdout" in log.read_text(encoding="utf-8")


def test_keyboard_interrupt_reaps_descendant_new_sessions(
        tmp_path, monkeypatch):
    from tools import process_census

    class FakeProcess:
        pid = 43210
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            self.returncode = -15
            return "", ""

        def poll(self):
            return self.returncode

    process = FakeProcess()
    killed = []
    monkeypatch.setattr(ai_flow.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(process_census, "descendant_process_groups",
                        lambda _pid: {50002, 50001})
    monkeypatch.setattr(ai_flow.os, "killpg",
                        lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        ai_flow.run_process(
            ["codex"], tmp_path, 60, tmp_path / "child.log",
            dry_run=False, track_descendant_groups=True)

    assert killed == [
        (50001, ai_flow.signal.SIGTERM),
        (50002, ai_flow.signal.SIGTERM),
        (43210, ai_flow.signal.SIGTERM),
    ]


def test_successful_agent_cannot_leave_an_owned_tool_session_running(
        tmp_path, monkeypatch):
    from tools import process_census

    class FakeProcess:
        pid = 43210
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise ai_flow.subprocess.TimeoutExpired("codex", timeout)
            self.returncode = 0
            return "structured output", ""

        def poll(self):
            return self.returncode

    process = FakeProcess()
    live = {50001}
    signals = []

    def fake_killpg(group, signum):
        if signum == 0:
            if group not in live:
                raise ProcessLookupError
            return
        signals.append((group, signum))
        if signum in {ai_flow.signal.SIGTERM, ai_flow.signal.SIGKILL}:
            live.discard(group)

    monkeypatch.setattr(ai_flow.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(process_census, "descendant_process_groups",
                        lambda _pid: {50001})
    monkeypatch.setattr(ai_flow.os, "killpg", fake_killpg)

    with pytest.raises(ai_flow.FlowError, match="owned descendant process groups"):
        ai_flow.run_process(
            ["codex"], tmp_path, 120, tmp_path / "child.log",
            dry_run=False, track_descendant_groups=True)

    assert signals == [(50001, ai_flow.signal.SIGTERM)]
    assert live == set()
