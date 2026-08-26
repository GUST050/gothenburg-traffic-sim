"""Contract tests for the local Codex/Claude orchestrator."""

import json
from pathlib import Path

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
            {"summary": "plan", "blocked_reason": ""},
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
