#!/usr/bin/env python3
"""Bounded Codex -> Claude -> Codex orchestration for local repository work."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover - environment error
        raise SystemExit("ai-flow requires tomli when run with Python older than 3.11") from exc


EXIT_APPROVED = 0
EXIT_BLOCKED = 2
EXIT_ERROR = 3
DISALLOWED_CLAUDE_TOOLS = ",".join(
    (
        "Bash(git push *)",
        "Bash(git commit *)",
        "Bash(git reset *)",
        "Bash(git clean *)",
        "Bash(git checkout *)",
        "Bash(git switch *)",
        "Bash(git merge *)",
        "Bash(rm *)",
    )
)


class FlowError(RuntimeError):
    """A user-actionable orchestration failure."""


@dataclass(frozen=True)
class Role:
    name: str
    runner: str
    model: str
    effort: str
    prompt_path: Path
    profile: str = ""
    sandbox: str = ""
    permission_mode: str = ""


@dataclass(frozen=True)
class Config:
    path: Path
    roles: dict[str, Role]
    checks: tuple[tuple[str, ...], ...]
    max_review_cycles: int
    max_test_fix_cycles: int
    agent_timeout_seconds: int
    check_timeout_seconds: int


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def _positive_int(raw: Any, key: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        raise FlowError(f"{key} must be a positive integer")
    return raw


def repository_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise FlowError("Run ai-flow inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def load_config(path: Path, root: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FlowError(f"Configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise FlowError(f"Invalid TOML in {path}: {exc}") from exc

    if raw.get("version") != 1:
        raise FlowError(f"Unsupported ai-flow config version in {path}")
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict):
        raise FlowError("config must define [roles.planner/worker/reviewer/fixer]")

    roles: dict[str, Role] = {}
    expected_runners = {
        "planner": "codex",
        "worker": "claude",
        "reviewer": "codex",
        "fixer": "claude",
    }
    for name, expected_runner in expected_runners.items():
        item = roles_raw.get(name)
        if not isinstance(item, dict):
            raise FlowError(f"config is missing [roles.{name}]")
        runner = item.get("runner")
        if runner != expected_runner:
            raise FlowError(f"roles.{name}.runner must be {expected_runner!r}")
        required = ("model", "effort", "prompt")
        missing = [key for key in required if not isinstance(item.get(key), str) or not item[key]]
        if missing:
            raise FlowError(f"roles.{name} is missing string fields: {', '.join(missing)}")
        prompt_path = (root / item["prompt"]).resolve()
        if not prompt_path.is_file():
            raise FlowError(f"Prompt file not found for {name}: {prompt_path}")
        role = Role(
            name=name,
            runner=runner,
            model=item["model"],
            effort=item["effort"],
            prompt_path=prompt_path,
            profile=str(item.get("profile", "")),
            sandbox=str(item.get("sandbox", "")),
            permission_mode=str(item.get("permission_mode", "")),
        )
        if runner == "codex" and role.sandbox not in {
            "read-only",
            "workspace-write",
            "danger-full-access",
        }:
            raise FlowError(f"roles.{name}.sandbox is invalid")
        if runner == "claude" and not role.permission_mode:
            raise FlowError(f"roles.{name}.permission_mode is required")
        roles[name] = role

    checks_raw = raw.get("checks", {}).get("commands", [])
    if not isinstance(checks_raw, list):
        raise FlowError("checks.commands must be an array of argv arrays")
    checks: list[tuple[str, ...]] = []
    for index, command in enumerate(checks_raw):
        if not isinstance(command, list) or not command or not all(
            isinstance(arg, str) and arg for arg in command
        ):
            raise FlowError(f"checks.commands[{index}] must be a non-empty string array")
        checks.append(tuple(command))

    return Config(
        path=path,
        roles=roles,
        checks=tuple(checks),
        max_review_cycles=_positive_int(raw.get("max_review_cycles"), "max_review_cycles"),
        max_test_fix_cycles=_positive_int(
            raw.get("max_test_fix_cycles"), "max_test_fix_cycles"
        ),
        agent_timeout_seconds=_positive_int(
            raw.get("agent_timeout_seconds"), "agent_timeout_seconds"
        ),
        check_timeout_seconds=_positive_int(
            raw.get("check_timeout_seconds"), "check_timeout_seconds"
        ),
    )


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def codex_profile_path(role: Role) -> Path | None:
    if not role.profile:
        return None
    return codex_home() / f"{role.profile}.config.toml"


# Keywords a strict structured-output transport cannot express. They are
# dropped from the WIRE copy only; the canonical schema keeps them and
# ``run_flow`` re-checks the same condition on the returned object, so
# nothing the provider cannot police goes unpoliced.  ``anyOf`` is
# deliberately NOT in this list - strict structured output accepts it, and
# discarding a constraint the transport can carry would weaken the wire
# schema for no reason.
_UNSUPPORTED_TRANSPORT_KEYWORDS = (
    "allOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
)


def strict_transport_schema(schema: Any) -> Any:
    """A strict-object copy of a canonical schema, for Codex's --output-schema.

    Codex accepts strict objects only: every declared property listed in
    ``required`` and ``additionalProperties`` false, with no conditional
    keywords anywhere.  The canonical schema in ``.ai-flow/schemas`` is the
    source of truth and says what is actually true - ``blocked_reason`` is
    required only when ``status`` is BLOCKED - so it is TRANSLATED here
    rather than flattened in place.  Weakening the canonical file to fit the
    wire would delete a real contract to satisfy a transport limitation; this
    keeps the contract and pays the limitation where it belongs.
    """
    if isinstance(schema, list):
        return [strict_transport_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    transported = {
        key: strict_transport_schema(value)
        for key, value in schema.items()
        if key not in _UNSUPPORTED_TRANSPORT_KEYWORDS
    }
    properties = transported.get("properties")
    if isinstance(properties, dict):
        transported["required"] = list(properties)
        transported["additionalProperties"] = False
    return transported


def write_transport_schema(schema_path: Path, destination: Path) -> Path:
    """Materialise the wire copy beside the run's other artifacts."""
    canonical = json.loads(schema_path.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(strict_transport_schema(canonical), indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def build_codex_command(
    role: Role,
    root: Path,
    schema_path: Path,
    output_path: Path,
    prompt: str,
) -> tuple[list[str], bool]:
    """Build the Codex invocation, writing the strict wire schema it needs.

    The canonical schema is never handed to Codex directly: the transport
    copy is written next to this invocation's output so the run directory
    records exactly what was sent.
    """
    transport_path = write_transport_schema(
        schema_path, output_path.with_name(f"{output_path.stem}.schema.json")
    )
    command = ["codex", "exec", "--strict-config", "-C", str(root)]
    profile_path = codex_profile_path(role)
    profile_found = bool(profile_path and profile_path.is_file())
    if profile_found:
        command.extend(["--profile", role.profile])
    else:
        command.extend(
            [
                "--model",
                role.model,
                "--config",
                f'model_reasoning_effort="{role.effort}"',
                "--config",
                'approval_policy="never"',
                "--sandbox",
                role.sandbox,
            ]
        )
    command.extend(
        [
            "--output-schema",
            str(transport_path),
            "--output-last-message",
            str(output_path),
            prompt,
        ]
    )
    return command, profile_found


def build_claude_command(role: Role, schema_path: Path, prompt: str) -> list[str]:
    schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
    # Claude Code validates the supplied output schema with its bundled
    # validator, which rejects the draft-2020-12 meta-schema URI even though
    # the schema keywords used by ai-flow are supported.  The declaration is
    # documentation only, so omit it from the CLI payload without weakening
    # any of the actual output constraints.
    schema_value.pop("$schema", None)
    schema = json.dumps(schema_value, separators=(",", ":"))
    return [
        "claude",
        "--print",
        "--model",
        role.model,
        "--effort",
        role.effort,
        "--permission-mode",
        role.permission_mode,
        "--disallowedTools",
        DISALLOWED_CLAUDE_TOOLS,
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--no-session-persistence",
        prompt,
    ]


def _redacted_command(command: Sequence[str]) -> str:
    redacted: list[str] = []
    skip_next = False
    for arg in command:
        if skip_next:
            redacted.append("<schema-or-prompt>")
            skip_next = False
        elif arg in {"--json-schema", "--output-last-message"}:
            redacted.append(arg)
            skip_next = True
        elif len(arg) > 400:
            redacted.append("<prompt>")
        else:
            redacted.append(arg)
    return shlex.join(redacted)


def run_process(
    command: Sequence[str],
    root: Path,
    timeout: int,
    log_path: Path,
    dry_run: bool,
    check: bool = True,
) -> ProcessResult:
    if dry_run:
        rendered = _redacted_command(command)
        print(f"DRY RUN: {rendered}")
        log_path.write_text(rendered + "\n", encoding="utf-8")
        return ProcessResult(0, "", "")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise FlowError(f"Required command not found: {command[0]}") from exc
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise FlowError(f"Command timed out after {timeout}s: {command[0]}")
        try:
            stdout, stderr = process.communicate(timeout=min(60, remaining))
            break
        except subprocess.TimeoutExpired:
            print(f"  {command[0]} is still running...", flush=True)
    result = ProcessResult(process.returncode, stdout, stderr)
    log_path.write_text(
        f"$ {_redacted_command(command)}\n\nSTDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}",
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise FlowError(
            f"{command[0]} exited {result.returncode}: {detail[-2000:] or 'no diagnostic'}"
        )
    return result


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise FlowError(f"{label} did not produce valid structured output: {path}") from exc
    if not isinstance(value, dict):
        raise FlowError(f"{label} output must be a JSON object")
    return value


def planner_blocked_reason(plan: dict[str, Any]) -> str:
    """The blocker text of a BLOCKED plan, or a loud failure.

    ``blocked_reason`` is REQUIRED here and only here, which is exactly what
    the canonical plan schema says; this re-checks it because the strict wire
    schema Codex is handed cannot carry a conditional.  Inventing a
    placeholder for a missing one - which this flow used to do - ends the run
    with a message that names no blocker, so nobody can tell an unanswerable
    question from a planner that simply forgot to fill the field in.  A
    non-string is failed rather than coerced: ``str(None)`` is the truthy
    text "None", which would sail straight through a strip-and-test.
    """
    reason = plan.get("blocked_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise FlowError(
            "Planner returned status BLOCKED without a usable blocked_reason "
            f"({reason!r}); a blocker must name what it is"
        )
    return reason.strip()


def compose_prompt(base: str, sections: Sequence[tuple[str, str]]) -> str:
    blocks = [base.rstrip()]
    for title, body in sections:
        blocks.append(f"## {title}\n\n{body.strip()}")
    return "\n\n".join(blocks) + "\n"


def run_codex_role(
    config: Config,
    role_name: str,
    root: Path,
    run_dir: Path,
    schema_name: str,
    prompt: str,
    invocation: str,
    dry_run: bool,
) -> dict[str, Any]:
    role = config.roles[role_name]
    schema_path = root / ".ai-flow" / "schemas" / schema_name
    output_path = run_dir / f"{invocation}.json"
    command, profile_found = build_codex_command(role, root, schema_path, output_path, prompt)
    if role.profile and not profile_found:
        print(
            f"  profile {role.profile!r} is not installed; using equivalent inline Codex settings"
        )
    run_process(
        command,
        root,
        config.agent_timeout_seconds,
        run_dir / f"{invocation}.log",
        dry_run,
    )
    if dry_run:
        return {}
    return read_json(output_path, role_name)


def run_claude_role(
    config: Config,
    role_name: str,
    root: Path,
    run_dir: Path,
    prompt: str,
    invocation: str,
    dry_run: bool,
) -> dict[str, Any]:
    role = config.roles[role_name]
    schema_path = root / ".ai-flow" / "schemas" / "work.json"
    command = build_claude_command(role, schema_path, prompt)
    result = run_process(
        command,
        root,
        config.agent_timeout_seconds,
        run_dir / f"{invocation}.log",
        dry_run,
    )
    if dry_run:
        return {}
    try:
        envelope = json.loads(result.stdout)
        structured = envelope["structured_output"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FlowError(f"{role_name} did not return Claude structured_output") from exc
    if not isinstance(structured, dict):
        raise FlowError(f"{role_name} structured_output must be an object")
    (run_dir / f"{invocation}.json").write_text(
        json.dumps(structured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return structured


def git_status(root: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise FlowError(f"git status failed: {result.stderr.strip()}")
    return result.stdout


def run_checks(
    commands: Sequence[Sequence[str]],
    root: Path,
    run_dir: Path,
    timeout: int,
    cycle: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        label = f"check-{cycle:02d}-{index:02d}"
        print(f"  check: {shlex.join(command)}")
        try:
            result = run_process(
                command, root, timeout, run_dir / f"{label}.log", dry_run, check=False
            )
            if result.returncode != 0:
                print(f"    failed with exit code {result.returncode}")
            outcomes.append(
                {
                    "command": shlex.join(command),
                    "returncode": result.returncode,
                    "stdout": result.stdout[-12000:],
                    "stderr": result.stderr[-12000:],
                }
            )
        except FlowError as exc:
            outcomes.append(
                {
                    "command": shlex.join(command),
                    "returncode": 1,
                    "stdout": "",
                    "stderr": str(exc),
                }
            )
    (run_dir / f"checks-{cycle:02d}.json").write_text(
        json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return outcomes


def _stage(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _write_status(run_dir: Path, status: str, summary: str) -> None:
    payload = {
        "status": status,
        "summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _final(status: str, summary: str, run_dir: Path, code: int) -> int:
    _write_status(run_dir, status, summary)
    _stage(f"{status}: {summary}")
    print(f"Artifacts: {run_dir}")
    return code


def acquire_lock(lock_path: Path, run_id: str) -> None:
    """Acquire the run lock, recovering only a well-formed lock with a dead PID."""
    for attempt in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                fields = dict(
                    item.split("=", 1)
                    for item in lock_path.read_text(encoding="utf-8").strip().split()
                )
                owner_pid = int(fields["pid"])
            except (OSError, ValueError, KeyError) as parse_error:
                raise FlowError(
                    f"Another or malformed ai-flow lock exists: {lock_path}"
                ) from parse_error
            try:
                os.kill(owner_pid, 0)
            except ProcessLookupError:
                if attempt == 0:
                    lock_path.unlink()
                    print(f"Recovered stale ai-flow lock from PID {owner_pid}")
                    continue
            except PermissionError:
                pass
            raise FlowError(
                f"Another ai-flow run appears active (PID {owner_pid}): {lock_path}"
            ) from exc
        try:
            os.write(descriptor, f"pid={os.getpid()} run={run_id}\n".encode())
        finally:
            os.close(descriptor)
        return
    raise FlowError(f"Could not acquire ai-flow lock: {lock_path}")


def _dry_run(
    config: Config,
    root: Path,
    task: str,
    run_dir: Path,
    checks: Sequence[Sequence[str]],
) -> int:
    base_plan = config.roles["planner"].prompt_path.read_text(encoding="utf-8")
    planner_prompt = compose_prompt(base_plan, (("User task", task),))
    _stage("CODEX PLANNER")
    run_codex_role(
        config, "planner", root, run_dir, "plan.json", planner_prompt, "plan", True
    )
    placeholder_plan = '{"summary":"dry-run placeholder"}'
    base_worker = config.roles["worker"].prompt_path.read_text(encoding="utf-8")
    worker_prompt = compose_prompt(
        base_worker, (("User task", task), ("Planner output", placeholder_plan))
    )
    _stage("CLAUDE WORKER")
    run_claude_role(config, "worker", root, run_dir, worker_prompt, "worker-01", True)
    _stage("DETERMINISTIC CHECKS")
    run_checks(checks, root, run_dir, config.check_timeout_seconds, 1, True)
    base_review = config.roles["reviewer"].prompt_path.read_text(encoding="utf-8")
    review_prompt = compose_prompt(
        base_review,
        (
            ("User task", task),
            ("Planner output", placeholder_plan),
            ("Check results", "[]"),
        ),
    )
    _stage("CODEX REVIEWER")
    run_codex_role(
        config, "reviewer", root, run_dir, "review.json", review_prompt, "review-01", True
    )
    return _final("DRY_RUN_COMPLETE", "no model calls or edits were made", run_dir, 0)


def run_flow(
    config: Config,
    root: Path,
    task: str,
    allow_dirty: bool,
    dry_run: bool,
    extra_checks: Sequence[Sequence[str]],
    no_checks: bool,
) -> int:
    if not (root / "AGENTS.md").is_file():
        raise FlowError(f"AGENTS.md is required at repository root: {root}")
    initial_status = git_status(root)
    if initial_status and not allow_dirty and not dry_run:
        raise FlowError(
            "Worktree is not clean. Commit/stash your work or rerun with --allow-dirty "
            "after confirming the reviewer may include those changes."
        )

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    run_dir = root / ".ai-flow" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task.txt").write_text(task + "\n", encoding="utf-8")
    (run_dir / "initial-status.txt").write_text(initial_status, encoding="utf-8")
    checks = () if no_checks else (*config.checks, *extra_checks)

    if dry_run:
        return _dry_run(config, root, task, run_dir, checks)

    required_commands = {"codex", "claude", *(command[0] for command in checks)}
    missing_commands = sorted(name for name in required_commands if not shutil.which(name))
    if missing_commands:
        raise FlowError(f"Required commands are not on PATH: {', '.join(missing_commands)}")

    lock_path = root / ".ai-flow" / "active.lock"
    acquire_lock(lock_path, run_id)

    try:
        _stage("CODEX PLANNER")
        planner_base = config.roles["planner"].prompt_path.read_text(encoding="utf-8")
        plan = run_codex_role(
            config,
            "planner",
            root,
            run_dir,
            "plan.json",
            compose_prompt(planner_base, (("User task", task),)),
            "plan",
            False,
        )
        plan_status = plan.get("status")
        if plan_status == "BLOCKED":
            return _final(
                "BLOCKED", planner_blocked_reason(plan), run_dir, EXIT_BLOCKED
            )
        if plan_status != "READY":
            raise FlowError(f"Planner returned unknown status: {plan_status!r}")

        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
        _stage("CLAUDE WORKER")
        worker_base = config.roles["worker"].prompt_path.read_text(encoding="utf-8")
        work = run_claude_role(
            config,
            "worker",
            root,
            run_dir,
            compose_prompt(
                worker_base, (("User task", task), ("Planner output", plan_text))
            ),
            "worker-01",
            False,
        )
        if work.get("status") == "BLOCKED":
            return _final(
                "BLOCKED", "; ".join(work.get("blockers", [])) or work.get("summary", ""), run_dir, EXIT_BLOCKED
            )

        test_fix_cycles = 0
        review_cycles = 0
        execution_cycle = 0
        latest_reason = ""
        while review_cycles < config.max_review_cycles:
            execution_cycle += 1
            _stage("DETERMINISTIC CHECKS")
            check_results = run_checks(
                checks,
                root,
                run_dir,
                config.check_timeout_seconds,
                execution_cycle,
                False,
            )
            failed = [item for item in check_results if item["returncode"] != 0]
            if failed:
                if test_fix_cycles >= config.max_test_fix_cycles:
                    return _final(
                        "BLOCKED",
                        f"checks still fail after {test_fix_cycles} repair cycles",
                        run_dir,
                        EXIT_BLOCKED,
                    )
                test_fix_cycles += 1
                latest_reason = json.dumps(failed, ensure_ascii=False, indent=2)
                _stage(f"CLAUDE TEST REPAIR {test_fix_cycles}/{config.max_test_fix_cycles}")
                fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")
                work = run_claude_role(
                    config,
                    "fixer",
                    root,
                    run_dir,
                    compose_prompt(
                        fixer_base,
                        (
                            ("User task", task),
                            ("Original plan", plan_text),
                            ("Failed checks", latest_reason),
                        ),
                    ),
                    f"test-fix-{test_fix_cycles:02d}",
                    False,
                )
                if work.get("status") == "BLOCKED":
                    return _final(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        run_dir,
                        EXIT_BLOCKED,
                    )
                continue

            review_cycles += 1
            _stage(f"CODEX REVIEW {review_cycles}/{config.max_review_cycles}")
            reviewer_base = config.roles["reviewer"].prompt_path.read_text(encoding="utf-8")
            review = run_codex_role(
                config,
                "reviewer",
                root,
                run_dir,
                "review.json",
                compose_prompt(
                    reviewer_base,
                    (
                        ("User task", task),
                        ("Planner output", plan_text),
                        (
                            "Deterministic check results",
                            json.dumps(check_results, ensure_ascii=False, indent=2),
                        ),
                    ),
                ),
                f"review-{review_cycles:02d}",
                False,
            )
            status = review.get("status")
            if status == "APPROVED":
                return _final(
                    "APPROVED",
                    str(review.get("summary", "task complete")),
                    run_dir,
                    EXIT_APPROVED,
                )
            if status == "BLOCKED":
                return _final(
                    "BLOCKED",
                    str(review.get("blocked_reason") or review.get("summary", "")),
                    run_dir,
                    EXIT_BLOCKED,
                )
            if status != "CHANGES_REQUIRED":
                raise FlowError(f"Reviewer returned unknown status: {status!r}")
            if review_cycles >= config.max_review_cycles:
                return _final(
                    "BLOCKED",
                    f"review still requires changes after {review_cycles} cycles",
                    run_dir,
                    EXIT_BLOCKED,
                )

            latest_reason = json.dumps(review, ensure_ascii=False, indent=2)
            _stage(f"CLAUDE REVIEW REPAIR {review_cycles}/{config.max_review_cycles - 1}")
            fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")
            work = run_claude_role(
                config,
                "fixer",
                root,
                run_dir,
                compose_prompt(
                    fixer_base,
                    (
                        ("User task", task),
                        ("Original plan", plan_text),
                        ("Review requiring changes", latest_reason),
                    ),
                ),
                f"review-fix-{review_cycles:02d}",
                False,
            )
            if work.get("status") == "BLOCKED":
                return _final(
                    "BLOCKED",
                    "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                    run_dir,
                    EXIT_BLOCKED,
                )
        return _final("BLOCKED", "maximum review cycles reached", run_dir, EXIT_BLOCKED)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def profile_toml(role: Role) -> str:
    return textwrap.dedent(
        f'''\
        # Generated by ai-flow for the {role.name} role.
        model = "{role.model}"
        model_reasoning_effort = "{role.effort}"
        sandbox_mode = "{role.sandbox}"
        approval_policy = "never"
        '''
    )


def setup_profiles(config: Config, force: bool) -> int:
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    for role_name in ("planner", "reviewer"):
        role = config.roles[role_name]
        target = codex_profile_path(role)
        if target is None:
            raise FlowError(f"roles.{role_name}.profile is not configured")
        content = profile_toml(role)
        if target.exists() and not force:
            if target.read_text(encoding="utf-8") == content:
                print(f"unchanged: {target}")
                continue
            raise FlowError(f"Refusing to overwrite existing profile without --force: {target}")
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=home, delete=False
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
        print(f"installed: {target}")
    return 0


def doctor(config: Config, root: Path) -> int:
    healthy = True
    print(f"repository: {root}")
    print(f"config:     {config.path}")
    for executable in ("codex", "claude"):
        resolved = shutil.which(executable)
        if not resolved:
            print(f"ERROR: {executable} is not on PATH")
            healthy = False
            continue
        result = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, timeout=15, check=False
        )
        version = (result.stdout or result.stderr).strip()
        print(f"{executable}: {version} ({resolved})")
        if result.returncode != 0:
            healthy = False
    for role_name in ("planner", "reviewer"):
        role = config.roles[role_name]
        path = codex_profile_path(role)
        if path and path.is_file():
            expected = profile_toml(role)
            state = "ok" if path.read_text(encoding="utf-8") == expected else "differs from config"
            print(f"{role_name} profile: {path} ({state})")
            healthy = healthy and state == "ok"
        else:
            print(f"WARN: {role_name} profile missing; runs will use equivalent inline settings")
    print(f"worktree: {'clean' if not git_status(root) else 'dirty'}")
    print(f"result: {'healthy' if healthy else 'needs attention'}")
    return 0 if healthy else EXIT_ERROR


def parse_extra_checks(values: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    checks: list[tuple[str, ...]] = []
    for raw in values:
        try:
            command = tuple(shlex.split(raw))
        except ValueError as exc:
            raise FlowError(f"Invalid --check value {raw!r}: {exc}") from exc
        if not command:
            raise FlowError("--check cannot be empty")
        checks.append(command)
    return tuple(checks)


def _config_and_root(config_arg: str) -> tuple[Config, Path]:
    root = repository_root(Path.cwd())
    config_path = Path(config_arg)
    if not config_path.is_absolute():
        config_path = root / config_path
    return load_config(config_path.resolve(), root), root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    command = arguments[0] if arguments and arguments[0] in {"setup", "doctor"} else "run"
    if command != "run":
        arguments.pop(0)

    if command == "setup":
        parser = argparse.ArgumentParser(prog="ai-flow setup")
        parser.add_argument("--config", default=".ai-flow/config.toml")
        parser.add_argument("--force", action="store_true")
        args = parser.parse_args(arguments)
        config, _ = _config_and_root(args.config)
        return setup_profiles(config, args.force)

    if command == "doctor":
        parser = argparse.ArgumentParser(prog="ai-flow doctor")
        parser.add_argument("--config", default=".ai-flow/config.toml")
        args = parser.parse_args(arguments)
        config, root = _config_and_root(args.config)
        return doctor(config, root)

    parser = argparse.ArgumentParser(
        prog="ai-flow",
        description="Run a bounded Codex planner -> Claude worker -> Codex reviewer loop.",
    )
    parser.add_argument("--config", default=".ai-flow/config.toml")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-checks", action="store_true")
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("task", nargs="+", help="implementation task")
    args = parser.parse_args(arguments)
    config, root = _config_and_root(args.config)
    return run_flow(
        config,
        root,
        " ".join(args.task).strip(),
        args.allow_dirty,
        args.dry_run,
        parse_extra_checks(args.check),
        args.no_checks,
    )


if __name__ == "__main__":
    started = time.monotonic()
    try:
        raise SystemExit(main())
    except FlowError as exc:
        elapsed = time.monotonic() - started
        print(f"ERROR after {elapsed:.1f}s: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
