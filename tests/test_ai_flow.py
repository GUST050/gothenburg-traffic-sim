"""Contract tests for the local Codex/Claude orchestrator."""

import json
from pathlib import Path

import pytest

from tools import ai_flow


ROOT = Path(__file__).resolve().parents[1]


def _config():
    return ai_flow.load_config(ROOT / ".ai-flow" / "config.toml", ROOT)


def test_role_routing_matches_requested_models_and_effort():
    config = _config()

    assert config.roles["planner"].runner == "codex"
    assert config.roles["planner"].model == "gpt-5.6-sol"
    assert config.roles["planner"].effort == "xhigh"
    assert config.roles["worker"].runner == "claude"
    assert config.roles["worker"].model == "opus"
    assert config.roles["worker"].effort == "high"
    assert config.roles["reviewer"].runner == "codex"
    assert config.max_review_cycles == 4
    assert config.max_test_fix_cycles == 3


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
    assert 'model_reasoning_effort="xhigh"' in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert 'approval_policy="never"' in command


def test_setup_installs_named_profiles_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = _config()

    assert ai_flow.setup_profiles(config, force=False) == 0
    planner = tmp_path / "planner.config.toml"
    reviewer = tmp_path / "reviewer.config.toml"
    assert planner.is_file()
    assert reviewer.is_file()
    assert 'model = "gpt-5.6-sol"' in planner.read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "xhigh"' in planner.read_text(encoding="utf-8")
    assert 'sandbox_mode = "read-only"' in planner.read_text(encoding="utf-8")
    assert ai_flow.setup_profiles(config, force=False) == 0


def test_claude_worker_is_explicit_and_blocks_consequential_git_actions():
    role = _config().roles["worker"]
    command = ai_flow.build_claude_command(
        role, ROOT / ".ai-flow" / "schemas" / "work.json", "implement this"
    )

    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--permission-mode") + 1] == "auto"
    denied = command[command.index("--disallowedTools") + 1]
    assert "Bash(git push *)" in denied
    assert "Bash(git commit *)" in denied
    schema = json.loads(command[command.index("--json-schema") + 1])
    assert "$schema" not in schema
    assert schema["properties"]["status"]["enum"] == ["IMPLEMENTED", "BLOCKED"]


def test_extra_checks_are_argv_not_shell_commands():
    checks = ai_flow.parse_extra_checks(["python3 -m pytest -q tests/test_auth.py"])

    assert checks == (("python3", "-m", "pytest", "-q", "tests/test_auth.py"),)


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
            {"status": "CHANGES_REQUIRED", "summary": "fix", "findings": []},
            {"status": "APPROVED", "summary": "done", "findings": []},
        ]
    )
    claude_roles = []

    def fake_codex(*_args, **_kwargs):
        return next(codex_results)

    def fake_claude(_config, role_name, *_args, **_kwargs):
        claude_roles.append(role_name)
        return {"status": "IMPLEMENTED", "summary": "ok", "blockers": []}

    check_results = iter(
        [
            [{"command": "check", "returncode": 1, "stdout": "", "stderr": "bad"}],
            [{"command": "check", "returncode": 0, "stdout": "", "stderr": ""}],
            [{"command": "check", "returncode": 0, "stdout": "", "stderr": ""}],
        ]
    )
    monkeypatch.setattr(ai_flow, "run_codex_role", fake_codex)
    monkeypatch.setattr(ai_flow, "run_claude_role", fake_claude)
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
        "run_claude_role",
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
        "run_claude_role",
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
    monkeypatch.setattr(ai_flow, "run_claude_role", lambda *_a, **_k: {})
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
    monkeypatch.setattr(ai_flow, "run_claude_role", lambda *_a, **_k: {})
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
