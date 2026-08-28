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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

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
    max_turns: int | None = None
    max_budget_usd: float | None = None


@dataclass(frozen=True)
class Config:
    path: Path
    roles: dict[str, Role]
    checks: tuple[tuple[str, ...], ...]
    max_review_cycles: int
    max_test_fix_cycles: int
    max_review_findings_per_repair: int
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


def _optional_positive_int(raw: Any, key: str) -> int | None:
    if raw is None:
        return None
    return _positive_int(raw, key)


def _optional_positive_number(raw: Any, key: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        raise FlowError(f"{key} must be a positive number")
    return float(raw)


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
        "fixer_fallback": "codex",
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
            max_turns=_optional_positive_int(
                item.get("max_turns"), f"roles.{name}.max_turns"
            ),
            max_budget_usd=_optional_positive_number(
                item.get("max_budget_usd"), f"roles.{name}.max_budget_usd"
            ),
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
        max_review_findings_per_repair=_positive_int(
            raw.get("max_review_findings_per_repair"),
            "max_review_findings_per_repair",
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
    profile_found = bool(
        profile_path
        and profile_path.is_file()
        and profile_path.read_text(encoding="utf-8") == profile_toml(role)
    )
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


def build_claude_command(
    role: Role,
    schema_path: Path,
    prompt: str,
    *,
    session_id: str | None = None,
    resume_session: bool = False,
) -> list[str]:
    schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
    # Claude Code validates the supplied output schema with its bundled
    # validator, which rejects the draft-2020-12 meta-schema URI even though
    # the schema keywords used by ai-flow are supported.  The declaration is
    # documentation only, so omit it from the CLI payload without weakening
    # any of the actual output constraints.
    schema_value.pop("$schema", None)
    schema = json.dumps(schema_value, separators=(",", ":"))
    command = [
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
    ]
    if role.max_turns is not None:
        command.extend(["--max-turns", str(role.max_turns)])
    if role.max_budget_usd is not None:
        command.extend(["--max-budget-usd", f"{role.max_budget_usd:g}"])
    if session_id:
        command.extend(["--resume" if resume_session else "--session-id", session_id])
    command.append(prompt)
    return command


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


def _terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Stop and reap the exact process group created for one invocation."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def _write_process_log(
    log_path: Path, command: Sequence[str], stdout: str, stderr: str
) -> None:
    log_path.write_text(
        f"$ {_redacted_command(command)}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}",
        encoding="utf-8",
    )


def run_process(
    command: Sequence[str],
    root: Path,
    timeout: int,
    log_path: Path,
    dry_run: bool,
    check: bool = True,
    progress: Callable[[int, float], None] | None = None,
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
    started = time.monotonic()
    deadline = time.monotonic() + timeout
    try:
        if progress:
            progress(process.pid, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stdout, stderr = _terminate_process_group(process)
                _write_process_log(log_path, command, stdout, stderr)
                raise FlowError(f"Command timed out after {timeout}s: {command[0]}")
            try:
                stdout, stderr = process.communicate(timeout=min(60, remaining))
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                if progress:
                    progress(process.pid, elapsed)
                print(f"  {command[0]} is still running ({elapsed:.0f}s)...", flush=True)
    except (KeyboardInterrupt, SystemExit):
        stdout, stderr = _terminate_process_group(process)
        _write_process_log(log_path, command, stdout, stderr)
        raise
    except BaseException:
        if process.poll() is None:
            stdout, stderr = _terminate_process_group(process)
            _write_process_log(log_path, command, stdout, stderr)
        raise
    result = ProcessResult(process.returncode, stdout, stderr)
    _write_process_log(log_path, command, result.stdout, result.stderr)
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


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _atomic_write_text(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _save_run_state(run_dir: Path, state: dict[str, Any], **changes: Any) -> None:
    state.update(changes)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(run_dir / "state.json", state)


def _progress_callback(
    run_dir: Path, state: dict[str, Any], invocation: str
) -> Callable[[int, float], None]:
    def update(pid: int, elapsed_s: float) -> None:
        _save_run_state(
            run_dir,
            state,
            status="RUNNING",
            active_invocation=invocation,
            child_pid=pid,
            active_elapsed_s=round(elapsed_s, 1),
        )
        _write_status(
            run_dir,
            "RUNNING",
            f"{invocation} active for {elapsed_s:.0f}s",
            stage=invocation,
            child_pid=pid,
        )
        lock_path = run_dir.parents[1] / "active.lock"
        if lock_path.is_file():
            _atomic_write_text(
                lock_path, f"pid={os.getpid()} run={run_dir.name} child={pid}\n"
            )

    return update


def _next_artifact_invocation(run_dir: Path, invocation: str) -> str:
    """Choose an append-only artifact stem without replacing an earlier attempt."""
    if not (run_dir / f"{invocation}.log").exists() and not (
        run_dir / f"{invocation}.json"
    ).exists():
        return invocation
    attempt = 2
    while True:
        candidate = f"{invocation}-attempt-{attempt:02d}"
        if not (run_dir / f"{candidate}.log").exists() and not (
            run_dir / f"{candidate}.json"
        ).exists():
            return candidate
        attempt += 1


def run_codex_role(
    config: Config,
    role_name: str,
    root: Path,
    run_dir: Path,
    schema_name: str,
    prompt: str,
    invocation: str,
    dry_run: bool,
    state: dict[str, Any] | None = None,
    append_attempt: bool = False,
) -> dict[str, Any]:
    role = config.roles[role_name]
    schema_path = root / ".ai-flow" / "schemas" / schema_name
    artifact_invocation = (
        _next_artifact_invocation(run_dir, invocation) if append_attempt else invocation
    )
    output_path = run_dir / f"{artifact_invocation}.json"
    command, profile_found = build_codex_command(role, root, schema_path, output_path, prompt)
    if role.profile and not profile_found:
        print(
            f"  profile {role.profile!r} is missing or stale; using equivalent inline Codex settings"
        )
    run_process(
        command,
        root,
        config.agent_timeout_seconds,
        run_dir / f"{artifact_invocation}.log",
        dry_run,
        progress=(
            _progress_callback(run_dir, state, invocation) if state is not None else None
        ),
    )
    if dry_run:
        return {}
    structured = read_json(output_path, role_name)
    if state is not None and append_attempt:
        state.setdefault("attempt_artifacts", {}).setdefault(invocation, []).append(
            artifact_invocation
        )
        state.setdefault("fallback_usage", {})[artifact_invocation] = {
            "runner": "codex",
            "model": role.model,
            "effort": role.effort,
            "status": "SUCCESS",
        }
        _save_run_state(
            run_dir,
            state,
            active_invocation=None,
            active_session_id=None,
            child_pid=None,
        )
    return structured


def run_claude_role(
    config: Config,
    role_name: str,
    root: Path,
    run_dir: Path,
    prompt: str,
    invocation: str,
    dry_run: bool,
    state: dict[str, Any] | None = None,
    resume_session: bool = False,
) -> dict[str, Any]:
    role = config.roles[role_name]
    schema_path = root / ".ai-flow" / "schemas" / "work.json"
    artifact_invocation = _next_artifact_invocation(run_dir, invocation)
    session_id: str | None = None
    if not dry_run:
        if state is None:
            session_id = str(uuid.uuid4())
        else:
            sessions = state.setdefault("claude_sessions", {})
            session_id = sessions.get(invocation)
            if not session_id:
                session_id = str(uuid.uuid4())
                sessions[invocation] = session_id
                resume_session = False
            _save_run_state(
                run_dir,
                state,
                active_invocation=invocation,
                active_session_id=session_id,
            )
    command = build_claude_command(
        role,
        schema_path,
        prompt,
        session_id=session_id,
        resume_session=resume_session,
    )
    log_path = run_dir / f"{artifact_invocation}.log"
    try:
        result = run_process(
            command,
            root,
            config.agent_timeout_seconds,
            log_path,
            dry_run,
            progress=(
                _progress_callback(run_dir, state, invocation)
                if state is not None
                else None
            ),
        )
    except FlowError:
        if state is not None and log_path.is_file():
            log_text = log_path.read_text(encoding="utf-8")
            stdout = log_text.partition("\nSTDOUT\n")[2].partition("\n\nSTDERR\n")[0]
            try:
                failed_envelope = json.loads(stdout)
            except json.JSONDecodeError:
                failed_envelope = {}
            usage = {
                key: failed_envelope.get(key)
                for key in (
                    "session_id",
                    "total_cost_usd",
                    "num_turns",
                    "duration_ms",
                    "terminal_reason",
                    "api_error_status",
                    "result",
                )
                if failed_envelope.get(key) is not None
            }
            usage["status"] = "ERROR"
            state.setdefault("claude_usage", {})[artifact_invocation] = usage
            state.setdefault("attempt_artifacts", {}).setdefault(invocation, []).append(
                artifact_invocation
            )
            _save_run_state(run_dir, state, child_pid=None)
        raise
    if dry_run:
        return {}
    try:
        envelope = json.loads(result.stdout)
        structured = envelope["structured_output"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FlowError(f"{role_name} did not return Claude structured_output") from exc
    if not isinstance(structured, dict):
        raise FlowError(f"{role_name} structured_output must be an object")
    (run_dir / f"{artifact_invocation}.json").write_text(
        json.dumps(structured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if state is not None:
        usage = {
            key: envelope.get(key)
            for key in ("session_id", "total_cost_usd", "num_turns", "duration_ms")
            if envelope.get(key) is not None
        }
        state.setdefault("claude_usage", {})[artifact_invocation] = usage
        state.setdefault("attempt_artifacts", {}).setdefault(invocation, []).append(
            artifact_invocation
        )
        _save_run_state(
            run_dir,
            state,
            active_invocation=None,
            active_session_id=None,
            child_pid=None,
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


def _write_status(
    run_dir: Path,
    status: str,
    summary: str,
    *,
    stage: str | None = None,
    child_pid: int | None = None,
) -> None:
    payload = {
        "schema_version": 2,
        "status": status,
        "summary": summary,
        "stage": stage,
        "controller_pid": os.getpid(),
        "child_pid": child_pid,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(run_dir / "status.json", payload)


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
                child_pid = int(fields.get("child", "0"))
            except (OSError, ValueError, KeyError) as parse_error:
                raise FlowError(
                    f"Another or malformed ai-flow lock exists: {lock_path}"
                ) from parse_error
            try:
                os.kill(owner_pid, 0)
            except ProcessLookupError:
                if attempt == 0:
                    if child_pid:
                        try:
                            os.kill(child_pid, 0)
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            raise FlowError(
                                f"Previous ai-flow child PID {child_pid} cannot be inspected"
                            ) from exc
                        else:
                            raise FlowError(
                                f"Previous controller is gone but its child PID {child_pid} "
                                "is still active; inspect it before resuming"
                            ) from exc
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


def _resume_run_dir(root: Path, run_id: str) -> Path:
    if not run_id or any(character not in "0123456789-" for character in run_id):
        raise FlowError(f"Invalid run id: {run_id!r}")
    runs_root = (root / ".ai-flow" / "runs").resolve()
    run_dir = (runs_root / run_id).resolve()
    if run_dir.parent != runs_root or not run_dir.is_dir():
        raise FlowError(f"Run does not exist: {run_id}")
    return run_dir


def _read_existing_json(path: Path, label: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return read_json(path, label)


def _infer_legacy_state(run_dir: Path, task: str) -> dict[str, Any]:
    """Infer a safe restart point for runs created before state.json existed."""
    plan = _read_existing_json(run_dir / "plan.json", "planner")
    worker = _read_existing_json(run_dir / "worker-01.json", "worker")
    reviews = sorted(run_dir.glob("review-[0-9][0-9].json"))
    checks = sorted(run_dir.glob("checks-[0-9][0-9].json"))
    test_fixes = sorted(run_dir.glob("test-fix-[0-9][0-9].json"))
    review_cycles = len(reviews)
    execution_cycle = len(checks)
    test_fix_cycles = len(test_fixes)

    next_stage = "planner"
    if plan is not None:
        if plan.get("status") == "BLOCKED":
            next_stage = "complete"
        elif worker is None:
            next_stage = "worker"
        elif reviews:
            latest_review = read_json(reviews[-1], "reviewer")
            review_fix = run_dir / f"review-fix-{review_cycles:02d}.json"
            if latest_review.get("status") in {"APPROVED", "BLOCKED"}:
                next_stage = "complete"
            elif latest_review.get("status") == "CHANGES_REQUIRED" and not review_fix.is_file():
                next_stage = "review_fix"
            else:
                next_stage = "checks"
        elif checks:
            latest_checks = json.loads(checks[-1].read_text(encoding="utf-8"))
            failed = [item for item in latest_checks if item.get("returncode") != 0]
            next_stage = "test_fix" if failed and not test_fixes else "review"
            if failed and not test_fixes:
                test_fix_cycles = 1
        else:
            next_stage = "checks"

    return {
        "schema_version": 1,
        "status": "RUNNING",
        "task": task,
        "next_stage": next_stage,
        "execution_cycle": execution_cycle,
        "review_cycles": review_cycles,
        "test_fix_cycles": test_fix_cycles,
        "active_invocation": None,
        "active_session_id": None,
        "claude_sessions": {},
        "claude_usage": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "recovered_from_legacy_artifacts": True,
    }


def _load_or_create_state(run_dir: Path, task: str, resume: bool) -> dict[str, Any]:
    state_path = run_dir / "state.json"
    if state_path.is_file():
        state = read_json(state_path, "run state")
        if state.get("task") != task:
            raise FlowError("Run state task does not match task.txt")
        return state
    if resume:
        return _infer_legacy_state(run_dir, task)
    return {
        "schema_version": 1,
        "status": "RUNNING",
        "task": task,
        "next_stage": "planner",
        "execution_cycle": 0,
        "review_cycles": 0,
        "test_fix_cycles": 0,
        "active_invocation": None,
        "active_session_id": None,
        "claude_sessions": {},
        "claude_usage": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _recover_claude_usage_from_logs(run_dir: Path, state: dict[str, Any]) -> None:
    """Index successful and failed Claude envelopes already present in append-only logs."""
    usage_index = state.setdefault("claude_usage", {})
    artifact_index = state.setdefault("attempt_artifacts", {})
    for pattern in ("worker-*.log", "test-fix-*.log", "review-fix-*.log"):
        for log_path in sorted(run_dir.glob(pattern)):
            artifact = log_path.stem
            if artifact in usage_index:
                continue
            log_text = log_path.read_text(encoding="utf-8")
            stdout = log_text.partition("\nSTDOUT\n")[2].partition("\n\nSTDERR\n")[0]
            try:
                envelope = json.loads(stdout)
            except json.JSONDecodeError:
                continue
            if not isinstance(envelope, dict) or not envelope.get("session_id"):
                continue
            usage = {
                key: envelope.get(key)
                for key in (
                    "session_id",
                    "total_cost_usd",
                    "num_turns",
                    "duration_ms",
                    "terminal_reason",
                    "api_error_status",
                    "result",
                )
                if envelope.get(key) is not None
            }
            usage["status"] = "ERROR" if envelope.get("is_error") else "SUCCESS"
            usage_index[artifact] = usage
            logical = artifact.partition("-attempt-")[0]
            artifacts = artifact_index.setdefault(logical, [])
            if artifact not in artifacts:
                artifacts.append(artifact)


def run_flow(
    config: Config,
    root: Path,
    task: str,
    allow_dirty: bool,
    dry_run: bool,
    extra_checks: Sequence[Sequence[str]],
    no_checks: bool,
    resume_run_id: str | None = None,
    fresh_stage: bool = False,
    additional_review_cycles: int = 0,
    use_fallback_fixer: bool = False,
    retry_checks: bool = False,
) -> int:
    if not (root / "AGENTS.md").is_file():
        raise FlowError(f"AGENTS.md is required at repository root: {root}")
    initial_status = git_status(root)
    if initial_status and not allow_dirty and not dry_run and not resume_run_id:
        raise FlowError(
            "Worktree is not clean. Commit/stash your work or rerun with --allow-dirty "
            "after confirming the reviewer may include those changes."
        )

    if resume_run_id:
        if dry_run:
            raise FlowError("--dry-run cannot be combined with --resume-run")
        run_id = resume_run_id
        run_dir = _resume_run_dir(root, run_id)
        recorded_task = (run_dir / "task.txt").read_text(encoding="utf-8").strip()
        if task and task != recorded_task:
            raise FlowError("Do not replace a resumed run's task; omit the task argument")
        task = recorded_task
    else:
        if not task:
            raise FlowError("A task is required for a new run")
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
    state = _load_or_create_state(run_dir, task, bool(resume_run_id))
    _recover_claude_usage_from_logs(run_dir, state)
    if retry_checks:
        if not resume_run_id or state.get("next_stage") != "test_fix":
            raise FlowError(
                "--retry-checks requires a resumed run currently stopped at test_fix"
            )
        state.setdefault("check_retry_events", []).append(
            {
                "failed_checks": state.get("last_failed_checks"),
                "reason": "explicit operator correction to deterministic check definition",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["next_stage"] = "checks"
        state["active_invocation"] = None
        state["active_session_id"] = None
    review_cycle_limit = int(
        state.get("review_cycle_limit", config.max_review_cycles)
    )
    if additional_review_cycles:
        if not resume_run_id:
            raise FlowError("--additional-review-cycles requires --resume-run")
        review_cycle_limit += additional_review_cycles
        state["review_cycle_limit"] = review_cycle_limit
    if fresh_stage:
        active = state.get("active_invocation")
        if active:
            state.setdefault("claude_sessions", {}).pop(active, None)
        state["active_invocation"] = None
        state["active_session_id"] = None
    _save_run_state(run_dir, state, status="RUNNING")

    def finish(status: str, summary: str, code: int) -> int:
        _save_run_state(
            run_dir,
            state,
            status=status,
            next_stage="complete",
            active_invocation=None,
            active_session_id=None,
            child_pid=None,
        )
        return _final(status, summary, run_dir, code)

    def mark(next_stage: str, invocation: str | None = None) -> None:
        _save_run_state(
            run_dir,
            state,
            status="RUNNING",
            next_stage=next_stage,
            active_invocation=invocation,
            child_pid=None,
            active_elapsed_s=0.0,
        )
        _write_status(
            run_dir,
            "RUNNING",
            f"next stage: {next_stage}",
            stage=invocation or next_stage,
        )

    def run_fixer(prompt: str, invocation: str, resume_session: bool) -> dict[str, Any]:
        if use_fallback_fixer:
            state.setdefault("fallback_events", []).append(
                {
                    "invocation": invocation,
                    "from": "fixer",
                    "to": "fixer_fallback",
                    "reason": "explicit operator selection",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
            _save_run_state(run_dir, state)
            return run_codex_role(
                config,
                "fixer_fallback",
                root,
                run_dir,
                "work.json",
                prompt,
                invocation,
                False,
                state,
                append_attempt=True,
            )
        return run_claude_role(
            config,
            "fixer",
            root,
            run_dir,
            prompt,
            invocation,
            False,
            state,
            resume_session=resume_session,
        )
    try:
        if state.get("next_stage") == "complete":
            terminal = _read_existing_json(run_dir / "status.json", "status") or {}
            review_cycles = int(state.get("review_cycles", 0))
            latest_review_path = run_dir / f"review-{review_cycles:02d}.json"
            latest_review = _read_existing_json(latest_review_path, "reviewer")
            can_extend = (
                additional_review_cycles > 0
                and terminal.get("status") == "BLOCKED"
                and latest_review is not None
                and latest_review.get("status") == "CHANGES_REQUIRED"
            )
            if can_extend:
                state["last_review"] = latest_review_path.name
                mark("review_fix")
            else:
                status = str(terminal.get("status", "BLOCKED"))
                code = EXIT_APPROVED if status == "APPROVED" else EXIT_BLOCKED
                return finish(
                    status, str(terminal.get("summary", "run already complete")), code
                )

        plan = _read_existing_json(run_dir / "plan.json", "planner")
        if state.get("next_stage") == "planner":
            invocation = "plan"
            mark("planner", invocation)
            _stage("CODEX PLANNER")
            planner_base = config.roles["planner"].prompt_path.read_text(encoding="utf-8")
            plan = run_codex_role(
                config,
                "planner",
                root,
                run_dir,
                "plan.json",
                compose_prompt(planner_base, (("User task", task),)),
                invocation,
                False,
                state,
            )
            plan_status = plan.get("status")
            if plan_status == "BLOCKED":
                return finish("BLOCKED", planner_blocked_reason(plan), EXIT_BLOCKED)
            if plan_status != "READY":
                raise FlowError(f"Planner returned unknown status: {plan_status!r}")
            mark("worker")
        if plan is None:
            raise FlowError("Cannot continue without plan.json")

        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
        work = _read_existing_json(run_dir / "worker-01.json", "worker")
        if state.get("next_stage") == "worker":
            invocation = "worker-01"
            resuming = state.get("active_invocation") == invocation
            mark("worker", invocation)
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
                invocation,
                False,
                state,
                resume_session=resuming,
            )
            if work.get("status") == "BLOCKED":
                return finish(
                    "BLOCKED",
                    "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                    EXIT_BLOCKED,
                )
            mark("checks")

        while int(state.get("review_cycles", 0)) < review_cycle_limit:
            next_stage = str(state.get("next_stage"))
            if next_stage == "checks":
                execution_cycle = int(state.get("execution_cycle", 0)) + 1
                state["execution_cycle"] = execution_cycle
                mark("checks", f"checks-{execution_cycle:02d}")
            else:
                execution_cycle = int(state.get("execution_cycle", 0))

            if next_stage == "checks":
                _stage("DETERMINISTIC CHECKS")
                check_results = run_checks(
                    checks,
                    root,
                    run_dir,
                    config.check_timeout_seconds,
                    execution_cycle,
                    False,
                )
                _atomic_write_json(
                    run_dir / f"checks-{execution_cycle:02d}.json",
                    check_results,
                )
                failed = [item for item in check_results if item["returncode"] != 0]
                if failed:
                    test_fix_cycles = int(state.get("test_fix_cycles", 0))
                    if test_fix_cycles >= config.max_test_fix_cycles:
                        return finish(
                            "BLOCKED",
                            f"checks still fail after {test_fix_cycles} repair cycles",
                            EXIT_BLOCKED,
                        )
                    state["test_fix_cycles"] = test_fix_cycles + 1
                    state["last_failed_checks"] = f"checks-{execution_cycle:02d}.json"
                    mark("test_fix")
                    continue
                mark("review")
                continue

            if next_stage == "test_fix":
                test_fix_cycles = int(state.get("test_fix_cycles", 0))
                failed_path = run_dir / str(state.get("last_failed_checks", ""))
                if not failed_path.is_file():
                    candidates = sorted(run_dir.glob("checks-[0-9][0-9].json"))
                    if not candidates:
                        raise FlowError("Test repair has no recorded failed checks")
                    failed_path = candidates[-1]
                latest_reason = failed_path.read_text(encoding="utf-8")
                invocation = f"test-fix-{test_fix_cycles:02d}"
                resuming = state.get("active_invocation") == invocation
                mark("test_fix", invocation)
                fixer_runner = "CODEX FALLBACK" if use_fallback_fixer else "CLAUDE"
                _stage(
                    f"{fixer_runner} TEST REPAIR "
                    f"{test_fix_cycles}/{config.max_test_fix_cycles}"
                )
                fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")
                work = run_fixer(
                    compose_prompt(
                        fixer_base,
                        (
                            ("User task", task),
                            ("Original plan", plan_text),
                            ("Failed checks", latest_reason),
                        ),
                    ),
                    invocation,
                    resuming,
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("checks")
                continue

            if next_stage == "review":
                review_cycles = int(state.get("review_cycles", 0)) + 1
                state["review_cycles"] = review_cycles
                invocation = f"review-{review_cycles:02d}"
                mark("review", invocation)
                check_path = run_dir / f"checks-{execution_cycle:02d}.json"
                recorded_checks = json.loads(check_path.read_text(encoding="utf-8"))
                check_results = (
                    recorded_checks.get("results", [])
                    if isinstance(recorded_checks, dict)
                    else recorded_checks
                )
                _stage(f"CODEX REVIEW {review_cycles}/{review_cycle_limit}")
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
                    invocation,
                    False,
                    state,
                )
                status = review.get("status")
                if status == "APPROVED":
                    return finish(
                        "APPROVED", str(review.get("summary", "task complete")), EXIT_APPROVED
                    )
                if status == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        str(review.get("blocked_reason") or review.get("summary", "")),
                        EXIT_BLOCKED,
                    )
                if status != "CHANGES_REQUIRED":
                    raise FlowError(f"Reviewer returned unknown status: {status!r}")
                if review_cycles >= review_cycle_limit:
                    return finish(
                        "BLOCKED",
                        f"review still requires changes after {review_cycles} cycles",
                        EXIT_BLOCKED,
                    )
                state["last_review"] = f"review-{review_cycles:02d}.json"
                mark("review_fix")
                continue

            if next_stage == "review_fix":
                review_cycles = int(state.get("review_cycles", 0))
                review_path = run_dir / str(
                    state.get("last_review", f"review-{review_cycles:02d}.json")
                )
                if not review_path.is_file():
                    raise FlowError("Review repair has no recorded review")
                review_payload = read_json(review_path, "reviewer")
                findings = review_payload.get("findings", [])
                if not isinstance(findings, list) or not findings:
                    raise FlowError("CHANGES_REQUIRED review has no repairable findings")
                selected = findings[: config.max_review_findings_per_repair]
                bounded_review = dict(review_payload)
                bounded_review["findings"] = selected
                bounded_review["repair_batch"] = {
                    "selected": len(selected),
                    "total": len(findings),
                    "instruction": "Repair only this severity-ordered batch; later review handles the remainder.",
                }
                latest_reason = json.dumps(bounded_review, ensure_ascii=False, indent=2)
                invocation = f"review-fix-{review_cycles:02d}"
                resuming = state.get("active_invocation") == invocation
                mark("review_fix", invocation)
                fixer_runner = "CODEX FALLBACK" if use_fallback_fixer else "CLAUDE"
                _stage(
                    f"{fixer_runner} REVIEW REPAIR "
                    f"{review_cycles}/{review_cycle_limit - 1}"
                )
                fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")
                work = run_fixer(
                    compose_prompt(
                        fixer_base,
                        (
                            ("User task", task),
                            ("Original plan", plan_text),
                            ("Review requiring changes", latest_reason),
                        ),
                    ),
                    invocation,
                    resuming,
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("checks")
                continue

            raise FlowError(f"Unknown persisted next stage: {next_stage!r}")

        return finish("BLOCKED", "maximum review cycles reached", EXIT_BLOCKED)
    except KeyboardInterrupt:
        _save_run_state(run_dir, state, status="INTERRUPTED")
        _write_status(
            run_dir,
            "INTERRUPTED",
            "controller interrupted; rerun with --resume-run",
            stage=str(state.get("next_stage")),
        )
        raise
    except FlowError as exc:
        _save_run_state(run_dir, state, status="ERROR", error=str(exc))
        _write_status(
            run_dir,
            "ERROR",
            str(exc),
            stage=str(state.get("next_stage")),
        )
        raise
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
    parser.add_argument(
        "--resume-run",
        metavar="RUN_ID",
        help="continue an existing run without repeating completed stages",
    )
    parser.add_argument(
        "--fresh-stage",
        action="store_true",
        help="with --resume-run, retry the interrupted stage in a new model session",
    )
    parser.add_argument(
        "--additional-review-cycles",
        type=int,
        default=0,
        metavar="N",
        help="explicitly extend a review-blocked resumed run by N bounded cycles",
    )
    parser.add_argument(
        "--use-fallback-fixer",
        action="store_true",
        help="use the configured Codex fixer for this run/resume instead of Claude",
    )
    parser.add_argument(
        "--retry-checks",
        action="store_true",
        help="after correcting an operator-supplied check, rerun checks without a code repair",
    )
    parser.add_argument("task", nargs="*", help="implementation task")
    args = parser.parse_args(arguments)
    if args.fresh_stage and not args.resume_run:
        parser.error("--fresh-stage requires --resume-run")
    if args.additional_review_cycles < 0:
        parser.error("--additional-review-cycles must be zero or positive")
    if args.use_fallback_fixer and not args.resume_run:
        parser.error("--use-fallback-fixer requires --resume-run")
    if args.retry_checks and not args.resume_run:
        parser.error("--retry-checks requires --resume-run")
    config, root = _config_and_root(args.config)
    return run_flow(
        config,
        root,
        " ".join(args.task).strip(),
        args.allow_dirty,
        args.dry_run,
        parse_extra_checks(args.check),
        args.no_checks,
        args.resume_run,
        args.fresh_stage,
        args.additional_review_cycles,
        args.use_fallback_fixer,
        args.retry_checks,
    )


if __name__ == "__main__":
    started = time.monotonic()
    try:
        raise SystemExit(main())
    except FlowError as exc:
        elapsed = time.monotonic() - started
        print(f"ERROR after {elapsed:.1f}s: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
