#!/usr/bin/env python3
"""Bounded multi-role Codex orchestration for local repository work."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
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
from typing import Any, Callable, Mapping, MutableMapping, Sequence

# ``./ai-flow`` executes this file by pathname, which otherwise places only
# ``tools/`` on sys.path. Runtime imports such as ``tools.process_census`` must
# resolve exactly as they do when the module is imported by the test suite.
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

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
    max_blocker_repair_cycles: int
    max_review_findings_per_repair: int
    agent_timeout_seconds: int
    check_timeout_seconds: int
    evidence_policy: "EvidencePolicy | None" = None


@dataclass(frozen=True)
class EvidencePolicy:
    """Deterministic guardrails for expensive, source-bound evidence runs."""

    source_globs: tuple[str, ...]
    registration_globs: tuple[str, ...]
    max_generations: int
    max_new_registrations_per_glob: int
    max_code_repair_cycles: int
    phase_checkpoint_globs: tuple[str, ...] = ()
    phase6_registration_globs: tuple[str, ...] = ()
    phase_report_globs: tuple[str, ...] = ()


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


def _string_array(raw: Any, key: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise FlowError(f"{key} must be a non-empty string array")
    return tuple(item.strip() for item in raw)


def _optional_string_array(raw: Any, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise FlowError(f"{key} must be a string array")
    return tuple(item.strip() for item in raw)


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
    allowed_runners = {
        "planner": {"codex"},
        "worker": {"codex", "claude"},
        "reviewer": {"codex"},
        "fixer": {"codex", "claude"},
        "fixer_fallback": {"codex"},
    }
    for name, permitted in allowed_runners.items():
        item = roles_raw.get(name)
        if not isinstance(item, dict):
            raise FlowError(f"config is missing [roles.{name}]")
        runner = item.get("runner")
        if runner not in permitted:
            choices = ", ".join(repr(item) for item in sorted(permitted))
            raise FlowError(f"roles.{name}.runner must be one of: {choices}")
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

    evidence_raw = raw.get("evidence")
    evidence_policy = None
    if evidence_raw is not None:
        if not isinstance(evidence_raw, dict):
            raise FlowError("evidence must be a TOML table")
        registration_globs = _string_array(
            evidence_raw.get("registration_globs"),
            "evidence.registration_globs",
        )
        phase_checkpoint_globs = _optional_string_array(
            evidence_raw.get("phase_checkpoint_globs"),
            "evidence.phase_checkpoint_globs",
        )
        phase6_registration_globs = _optional_string_array(
            evidence_raw.get("phase6_registration_globs"),
            "evidence.phase6_registration_globs",
        )
        phase_report_globs = _optional_string_array(
            evidence_raw.get("phase_report_globs"),
            "evidence.phase_report_globs",
        )
        unregistered_phase_globs = sorted(
            (set(phase_checkpoint_globs) | set(phase6_registration_globs)
             | set(phase_report_globs))
            - set(registration_globs)
        )
        if unregistered_phase_globs:
            raise FlowError(
                "evidence phase globs must also be present in registration_globs: "
                + ", ".join(unregistered_phase_globs)
            )
        evidence_policy = EvidencePolicy(
            source_globs=_string_array(
                evidence_raw.get("source_globs"), "evidence.source_globs"
            ),
            registration_globs=registration_globs,
            max_generations=_positive_int(
                evidence_raw.get("max_generations"), "evidence.max_generations"
            ),
            max_new_registrations_per_glob=_positive_int(
                evidence_raw.get("max_new_registrations_per_glob"),
                "evidence.max_new_registrations_per_glob",
            ),
            max_code_repair_cycles=_positive_int(
                evidence_raw.get("max_code_repair_cycles"),
                "evidence.max_code_repair_cycles",
            ),
            phase_checkpoint_globs=phase_checkpoint_globs,
            phase6_registration_globs=phase6_registration_globs,
            phase_report_globs=phase_report_globs,
        )

    return Config(
        path=path,
        roles=roles,
        checks=tuple(checks),
        max_review_cycles=_positive_int(raw.get("max_review_cycles"), "max_review_cycles"),
        max_test_fix_cycles=_positive_int(
            raw.get("max_test_fix_cycles"), "max_test_fix_cycles"
        ),
        max_blocker_repair_cycles=_positive_int(
            raw.get("max_blocker_repair_cycles"), "max_blocker_repair_cycles"
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
        evidence_policy=evidence_policy,
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


def _signal_process_groups(groups: Sequence[int], signum: int) -> None:
    for group in sorted({int(value) for value in groups if int(value) > 0}):
        try:
            os.killpg(group, signum)
        except ProcessLookupError:
            pass


def _process_group_alive(group: int) -> bool:
    try:
        os.killpg(int(group), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_owned_descendant_groups(groups: Sequence[int]) -> None:
    live = {int(group) for group in groups if _process_group_alive(int(group))}
    _signal_process_groups(tuple(live), signal.SIGTERM)
    deadline = time.monotonic() + 10.0
    while live and time.monotonic() < deadline:
        time.sleep(0.05)
        live = {group for group in live if _process_group_alive(group)}
    _signal_process_groups(tuple(live), signal.SIGKILL)


def _terminate_process_group(
    process: subprocess.Popen[str],
    owned_descendant_groups: Sequence[int] = (),
) -> tuple[str, str]:
    """Stop and reap an invocation plus new sessions it launched."""
    _signal_process_groups(owned_descendant_groups, signal.SIGTERM)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        _signal_process_groups(owned_descendant_groups, signal.SIGKILL)
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
    track_descendant_groups: bool = False,
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
    last_progress_report = started
    owned_descendant_groups: set[int] = set()

    def refresh_owned_groups() -> None:
        if not track_descendant_groups or process.poll() is not None:
            return
        try:
            from tools.process_census import descendant_process_groups
            owned_descendant_groups.update(descendant_process_groups(process.pid))
        except (OSError, RuntimeError, ValueError):
            # The root process group remains owned and is still reaped below.
            # A relation census failure is recorded indirectly if an orphan
            # survives; normal successful invocations must leave no such group.
            pass

    try:
        if progress:
            progress(process.pid, 0.0)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                refresh_owned_groups()
                stdout, stderr = _terminate_process_group(
                    process, tuple(owned_descendant_groups))
                _write_process_log(log_path, command, stdout, stderr)
                raise FlowError(f"Command timed out after {timeout}s: {command[0]}")
            try:
                # A tool command may create a new session/process group. Poll
                # often enough to observe it while the agent is still its
                # ancestor, but keep user-facing progress at the old cadence.
                census_interval = 5 if track_descendant_groups else 60
                stdout, stderr = process.communicate(
                    timeout=min(census_interval, remaining))
                break
            except subprocess.TimeoutExpired:
                refresh_owned_groups()
                elapsed = time.monotonic() - started
                if time.monotonic() - last_progress_report >= 60:
                    if progress:
                        progress(process.pid, elapsed)
                    print(
                        f"  {command[0]} is still running ({elapsed:.0f}s)...",
                        flush=True,
                    )
                    last_progress_report = time.monotonic()
    except (KeyboardInterrupt, SystemExit):
        refresh_owned_groups()
        stdout, stderr = _terminate_process_group(
            process, tuple(owned_descendant_groups))
        _write_process_log(log_path, command, stdout, stderr)
        raise
    except BaseException:
        if process.poll() is None:
            refresh_owned_groups()
            stdout, stderr = _terminate_process_group(
                process, tuple(owned_descendant_groups))
            _write_process_log(log_path, command, stdout, stderr)
        raise
    result = ProcessResult(process.returncode, stdout, stderr)
    _write_process_log(log_path, command, result.stdout, result.stderr)
    lingering_groups = sorted(
        group for group in owned_descendant_groups
        if _process_group_alive(group))
    if lingering_groups:
        _stop_owned_descendant_groups(lingering_groups)
        raise FlowError(
            f"{command[0]} exited while owned descendant process groups "
            f"were still running: {lingering_groups}; they were reaped")
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
    run_dir: Path,
    state: dict[str, Any],
    invocation: str,
    guard: Callable[[], None] | None = None,
) -> Callable[[int, float], None]:
    def update(pid: int, elapsed_s: float) -> None:
        if guard is not None:
            guard()
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
    progress_guard: Callable[[], None] | None = None,
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
            _progress_callback(run_dir, state, invocation, progress_guard)
            if state is not None
            else None
        ),
        track_descendant_groups=True,
    )
    if dry_run:
        return {}
    structured = read_json(output_path, role_name)
    if state is not None and append_attempt:
        state.setdefault("attempt_artifacts", {}).setdefault(invocation, []).append(
            artifact_invocation
        )
        usage_record = {
            "runner": "codex",
            "model": role.model,
            "effort": role.effort,
            "status": "SUCCESS",
        }
        state.setdefault("codex_usage", {})[artifact_invocation] = usage_record
        if role_name == "fixer_fallback":
            state.setdefault("fallback_usage", {})[artifact_invocation] = usage_record
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
    progress_guard: Callable[[], None] | None = None,
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
                _progress_callback(
                    run_dir, state, invocation, progress_guard
                )
                if state is not None
                else None
            ),
            track_descendant_groups=True,
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


def run_work_role(
    config: Config,
    role_name: str,
    root: Path,
    run_dir: Path,
    prompt: str,
    invocation: str,
    dry_run: bool,
    state: dict[str, Any] | None = None,
    resume_session: bool = False,
    progress_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run a mutable work role through its configured provider.

    Codex work attempts are append-only just like persisted Claude attempts.
    Codex has no provider session to resume, so the durable flow cursor and
    prior artifacts are the continuation boundary for that runner.
    """
    role = config.roles[role_name]
    if role.runner == "codex":
        return run_codex_role(
            config,
            role_name,
            root,
            run_dir,
            "work.json",
            prompt,
            invocation,
            dry_run,
            state,
            append_attempt=True,
            progress_guard=progress_guard,
        )
    if role.runner == "claude":
        return run_claude_role(
            config,
            role_name,
            root,
            run_dir,
            prompt,
            invocation,
            dry_run,
            state,
            resume_session=resume_session,
            progress_guard=progress_guard,
        )
    raise FlowError(f"Unsupported runner for {role_name}: {role.runner!r}")


def role_stage_label(role: Role) -> str:
    """Human-readable provider/model label for live stage output."""
    return f"{role.runner.upper()} {role.model}"


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


def _matched_files(root: Path, patterns: Sequence[str]) -> tuple[Path, ...]:
    """Resolve configured repository-relative globs without following escapes."""
    root = root.resolve()
    matched: dict[str, Path] = {}
    for pattern in patterns:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise FlowError(f"Evidence glob must stay inside the repository: {pattern}")
        for candidate in root.glob(pattern):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise FlowError(
                    f"Evidence glob resolved outside the repository: {candidate}"
                ) from exc
            matched[relative] = resolved
    return tuple(matched[key] for key in sorted(matched))


def source_manifest(root: Path, patterns: Sequence[str]) -> dict[str, Any]:
    """Content-bind every protected source selected by the evidence policy."""
    root = root.resolve()
    records = []
    for path in _matched_files(root, patterns):
        payload = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1,
        "files": records,
        "digest": hashlib.sha256(encoded).hexdigest(),
    }


def evidence_inventory(
    root: Path, patterns: Sequence[str]
) -> dict[str, dict[str, str]]:
    """Content-bind append-only artifacts separately for each evidence series."""
    root = root.resolve()
    inventory: dict[str, dict[str, str]] = {}
    for pattern in patterns:
        inventory[pattern] = {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _matched_files(root, (pattern,))
        }
    return inventory


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_inventory_delta(
    baseline: dict[str, dict[str, str]],
    current: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Return only newly appended evidence, retaining path and byte identity."""
    return {
        pattern: {
            path: digest
            for path, digest in current.get(pattern, {}).items()
            if path not in baseline.get(pattern, {})
        }
        for pattern in current
    }


def build_phase_3_5_checkpoint(
    root: Path,
    policy: EvidencePolicy,
    baseline: dict[str, dict[str, str]],
    source_freeze: dict[str, Any],
) -> dict[str, Any]:
    """Bind the fresh Phase 3--5 evidence before any later phase may start.

    This deliberately uses the controller's append-only byte inventory rather
    than trusting a producer's prose.  A configured Phase 6/7 artifact that
    appears in this delta is a protocol violation, not a review finding to
    waive.  The complete-subhour configuration names the Phase 3--5 series in
    ``phase_checkpoint_globs``; small non-evidence test configurations may
    leave it empty and still exercise the staged controller.
    """
    if not isinstance(source_freeze.get("digest"), str) or not source_freeze.get(
        "digest"
    ):
        raise FlowError("Phase 3-5 checkpoint requires a content-hashed source freeze")
    current = evidence_inventory(root, policy.registration_globs)
    added = _evidence_inventory_delta(baseline, current)
    forbidden = {
        pattern: records
        for pattern, records in added.items()
        if pattern in policy.phase6_registration_globs and records
    }
    if forbidden:
        raise FlowError(
            "Phase 6/7 evidence was registered before the independent Phase "
            f"3-5 review: {forbidden}"
        )
    missing = [
        pattern
        for pattern in policy.phase_checkpoint_globs
        if not added.get(pattern)
    ]
    if missing:
        raise FlowError(
            "Phase 3-5 checkpoint is missing fresh evidence series: "
            + ", ".join(missing)
        )
    if not any(added.values()):
        raise FlowError("Phase 3-5 checkpoint has no fresh evidence")

    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "ai_flow_phase_3_5_checkpoint",
        "status": "PENDING_INDEPENDENT_REVIEW",
        "source_digest": source_freeze.get("digest"),
        "artifact_inventory": added,
        "artifact_inventory_digest": _canonical_digest(added),
        "lineage_digest": _canonical_digest(
            {
                "source_digest": source_freeze.get("digest"),
                "artifact_inventory": added,
            }
        ),
        "phase6_registration_globs": list(policy.phase6_registration_globs),
    }
    return {**body, "content_digest": _canonical_digest(body)}


def build_phase_3_5_review_artifact(
    checkpoint: dict[str, Any],
    review: dict[str, Any],
    invocation: str,
) -> dict[str, Any]:
    """Create the run-local, digest-bound approval consumed by Phase 6."""
    if checkpoint.get("schema_version") != 1 or checkpoint.get(
        "kind"
    ) != "ai_flow_phase_3_5_checkpoint":
        raise FlowError("Phase 3-5 review requires a canonical checkpoint")
    if review.get("status") != "APPROVED":
        raise FlowError("Phase 3-5 review artifact requires APPROVED review")
    findings = review.get("findings")
    if findings:
        raise FlowError("Phase 3-5 review artifact cannot contain findings")
    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "ai_flow_phase_3_5_independent_review",
        "status": "PASS",
        "reviewer_invocation": invocation,
        "review_response_digest": _canonical_digest(review),
        "checkpoint_content_digest": checkpoint.get("content_digest"),
        "source_digest": checkpoint.get("source_digest"),
        "artifact_inventory_digest": checkpoint.get("artifact_inventory_digest"),
        "lineage_digest": checkpoint.get("lineage_digest"),
    }
    return {**body, "content_digest": _canonical_digest(body)}


def validate_phase_3_5_checkpoint(
    root: Path,
    policy: EvidencePolicy,
    baseline: dict[str, dict[str, str]],
    source_freeze: dict[str, Any],
    checkpoint: dict[str, Any],
) -> None:
    """Recompute the checkpoint from current bytes before a later phase."""
    if checkpoint.get("schema_version") != 1 or checkpoint.get(
        "kind"
    ) != "ai_flow_phase_3_5_checkpoint":
        raise FlowError("Phase 3-5 checkpoint has an unknown schema or kind")
    if checkpoint.get("status") != "PENDING_INDEPENDENT_REVIEW":
        raise FlowError("Phase 3-5 checkpoint has an invalid status")
    recomputed = build_phase_3_5_checkpoint(
        root, policy, baseline, source_freeze
    )
    if recomputed.get("content_digest") != checkpoint.get("content_digest"):
        raise FlowError("Phase 3-5 checkpoint lineage does not match current evidence")


def validate_phase_3_5_review_artifact(
    artifact: dict[str, Any],
    checkpoint: dict[str, Any],
    review: dict[str, Any],
    source_freeze: dict[str, Any],
) -> None:
    """Fail closed if the approval no longer names the reviewed bytes."""
    if artifact.get("schema_version") != 1 or artifact.get(
        "kind"
    ) != "ai_flow_phase_3_5_independent_review":
        raise FlowError("Phase 3-5 review artifact has an unknown schema or kind")
    if artifact.get("status") != "PASS":
        raise FlowError("Phase 6 requires a PASS Phase 3-5 independent review")
    body = {key: value for key, value in artifact.items() if key != "content_digest"}
    if artifact.get("content_digest") != _canonical_digest(body):
        raise FlowError("Phase 3-5 review artifact content digest mismatch")
    if artifact.get("checkpoint_content_digest") != checkpoint.get("content_digest"):
        raise FlowError("Phase 3-5 review is bound to a different checkpoint")
    if artifact.get("source_digest") != source_freeze.get("digest"):
        raise FlowError("Phase 3-5 review source lineage does not match CODE_APPROVED")
    if artifact.get("artifact_inventory_digest") != checkpoint.get(
        "artifact_inventory_digest"
    ) or artifact.get("lineage_digest") != checkpoint.get("lineage_digest"):
        raise FlowError("Phase 3-5 review lineage does not match checkpoint")
    if artifact.get("review_response_digest") != _canonical_digest(review):
        raise FlowError("Phase 3-5 review response was changed")
    if review.get("status") != "APPROVED" or review.get("findings"):
        raise FlowError("Phase 3-5 review response is not an approval")


_PHASE_REPORT_SCHEMA = "subhour_phase_report_v1"
_PHASE_REPORT_KIND = "subhour_phase_report"
_PHASE_TERMINAL_STATUSES = {"PASS", "INCONCLUSIVE", "NOT_TRIGGERED", "NOT_ALLOWED"}
_PHASE_REPORT_PHASES = tuple(f"phase_{number}" for number in range(8))


def _validate_report_reference(
    root: Path, reference: Any, *, label: str,
    expected_schema: str | None = None, expected_kind: str | None = None,
    active_evidence_paths: dict[Path, str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(reference, dict):
        raise FlowError(f"Phase report artifact reference {label} is malformed")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise FlowError(f"Phase report artifact reference {label} lacks a path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    resolved_root = root.resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as error:
        raise FlowError(
            f"Phase report artifact {label} is outside the repository root"
        ) from error
    if active_evidence_paths is not None and path not in active_evidence_paths:
        raise FlowError(
            f"Phase report artifact {label} is not a fresh member of the active "
            "evidence generation"
        )
    if not path.is_file():
        raise FlowError(f"Phase report artifact {label} is missing")
    raw = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if reference.get("sha256") != actual_sha256:
        raise FlowError(f"Phase report artifact {label} bytes drifted")
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowError(f"Phase report artifact {label} is not valid JSON") from error
    if not isinstance(artifact, dict):
        raise FlowError(f"Phase report artifact {label} is not an object")
    if expected_schema is not None and artifact.get("schema") != expected_schema:
        raise FlowError(f"Phase report artifact {label} has an invalid schema")
    if expected_kind is not None and artifact.get("kind") != expected_kind:
        raise FlowError(f"Phase report artifact {label} has an invalid kind")
    content_key = reference.get("content_key")
    if not isinstance(content_key, str) or artifact.get("content_key") != content_key:
        raise FlowError(f"Phase report artifact {label} content key is not bound")
    body = {key: value for key, value in artifact.items() if key != "content_key"}
    full = _canonical_digest(body)
    short = full[:32]
    if content_key not in {full, short}:
        raise FlowError(f"Phase report artifact {label} content key is invalid")
    return path, artifact


def _derive_report_phase_status(
    root: Path, phase_name: str, status_artifact: Mapping[str, Any]
) -> str:
    """Derive a Phase 3--5 status from the referenced producer bytes."""
    if phase_name in {"phase_0", "phase_1", "phase_2"}:
        return "PASS"
    references = status_artifact.get("references")
    if not isinstance(references, list) or not references:
        raise FlowError(f"{phase_name} status artifact lacks producer evidence")
    values: list[dict[str, Any]] = []
    for reference in references:
        if not isinstance(reference, Mapping) or not isinstance(
                reference.get("path"), str):
            continue
        path = Path(reference["path"])
        if not path.is_absolute():
            path = root / path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            values.append(value)
    for value in values:
        if value.get("schema") == "subhour_cost_ordered_bounded_outcome_v1":
            identity = value.get("evidence_id")
            registration = value.get("registration")
            if (not isinstance(identity, str) or not identity
                    or not isinstance(registration, Mapping)
                    or registration.get("evidence_id") != identity
                    or not isinstance(registration.get("content_key"), str)
                    or not registration.get("content_key")):
                raise FlowError(
                    f"{phase_name} bounded outcome is not bound to its registration")
            registration_path = registration.get("path")
            if not isinstance(registration_path, str) or not registration_path:
                raise FlowError(f"{phase_name} bounded outcome lacks registration path")
            registration_file = Path(registration_path).resolve()
            if not registration_file.is_file():
                raise FlowError(f"{phase_name} registration is missing")
            raw_registration = registration_file.read_bytes()
            if registration.get("sha256") != hashlib.sha256(
                    raw_registration).hexdigest():
                raise FlowError(f"{phase_name} registration bytes drifted")
            try:
                bound_registration = json.loads(raw_registration.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FlowError(
                    f"{phase_name} registration is not valid JSON") from error
            registration_body = {
                key: item for key, item in bound_registration.items()
                if key not in {"content_key", "registered_at"}
            } if isinstance(bound_registration, Mapping) else {}
            if (not isinstance(bound_registration, Mapping)
                    or bound_registration.get("schema")
                    != "subhour_cost_ordered_bounded_registration_v1"
                    or bound_registration.get("evidence_id") != identity
                    or bound_registration.get("content_key") != registration.get(
                        "content_key")
                    or bound_registration.get("content_key")
                    != _canonical_digest(registration_body)):
                raise FlowError(
                    f"{phase_name} registration identity does not match")
            source_selection = value.get("selection") or {}
            registered_selection = bound_registration.get("selection")
            selected_ids = (source_selection.get("selected_ids")
                            if isinstance(source_selection, Mapping) else None)
            registered_ids = (registered_selection.get("selected_ids")
                              if isinstance(registered_selection, Mapping) else None)
            source_cases = value.get("case_results")
            registered_cases = bound_registration.get("selected_cases")
            source_pairs = [
                (item.get("case_id"), item.get("search_content_key"))
                for item in source_cases
                if isinstance(item, Mapping)
            ] if isinstance(source_cases, list) else []
            registered_pairs = [
                (item.get("case_id"), item.get("search_content_key"))
                for item in registered_cases
                if isinstance(item, Mapping)
            ] if isinstance(registered_cases, list) else []
            if (not isinstance(selected_ids, list)
                    or selected_ids != registered_ids
                    or source_pairs != registered_pairs):
                raise FlowError(
                    f"{phase_name} outcome selection is not bound to its registration")
    if phase_name == "phase_3":
        candidates = [item for item in values if item.get("schema") ==
                      "subhour_cost_ordered_bounded_outcome_v1" and item.get(
                          "kind") == "subhour_bounded_sumo_outcome"]
        if len(candidates) != 1:
            raise FlowError("phase_3 status is not bound to one bounded outcome")
        value = candidates[0]
        if value.get("status") == "PASS":
            cases = value.get("case_results")
            if (not isinstance(cases, list) or not cases
                    or not all(isinstance(item, Mapping)
                               and item.get("gates_passed") is True
                               for item in cases)
                    or (value.get("gate_s") or {}).get(
                        "population_complete") is not True):
                raise FlowError("phase_3 PASS lacks complete paired gates")
            return "PASS"
        if isinstance(value.get("status"), str) and value["status"].startswith(
                "INCONCLUSIVE"):
            return "INCONCLUSIVE"
        raise FlowError("phase_3 outcome has no legal terminal status")
    if phase_name == "phase_4":
        candidates = [item for item in values if item.get("schema") ==
                      "monthly_cost_ledger_profile_v1" and item.get("kind") in {
                          "monthly_cost_ledger_profile",
                          "monthly_cost_ledger_profile_outcome"}]
        if len(candidates) != 1:
            raise FlowError("phase_4 status is not bound to one ledger profile")
        value = candidates[0]
        population = value.get("population") or {}
        observed_attempts = value.get("sumo_attempts")
        observation = value.get("sumo_start_observation")
        if (isinstance(observed_attempts, bool)
                or not isinstance(observed_attempts, int)
                or observed_attempts < 0
                or not isinstance(observation, Mapping)):
            raise FlowError("phase_4 profile lacks observed SUMO attempts")
        before = observation.get("before")
        after = observation.get("after")
        delta = observation.get("delta")
        if (any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in (before, after, delta))
                or after < before or delta != after - before
                or observed_attempts != delta
                or not isinstance(value.get("sumo_started"), bool)
                or value["sumo_started"] != bool(before or delta)):
            raise FlowError("phase_4 profile has inconsistent SUMO attempt telemetry")
        complete = (
            value.get("population_complete") is True
            and value.get("phase_timing_complete") is True
            and value.get("sumo_zero_launch_gate") is True
            and int(population.get("daily_units", 0)) == 1950
            and int(population.get("daily_variant_records", 0)) == 5850
            and int(population.get("parents", population.get(
                "parent_schedules", 0))) == 1690
        )
        if value.get("status") == "PASS" and complete:
            return "PASS"
        if isinstance(value.get("status"), str) and value["status"].startswith(
                "INCONCLUSIVE"):
            return "INCONCLUSIVE"
        raise FlowError("phase_4 profile has no legal terminal status")
    if phase_name == "phase_5":
        profiles = [item for item in values if item.get("schema") ==
                    "monthly_cost_ledger_profile_v1" and item.get("kind") in {
                        "monthly_cost_ledger_profile",
                        "monthly_cost_ledger_profile_outcome"}]
        indexes = [item for item in values if item.get("schema") ==
                   "subhour_phase5_window_cost_index_evidence_v1" and item.get(
                       "kind") == "subhour_phase5_window_cost_index"]
        if len(profiles) != 1:
            raise FlowError("phase_5 status is not bound to one ledger profile")
        profile = profiles[0]
        if profile.get("phase_5_decision") == "NOT_TRIGGERED":
            population = profile.get("population") or {}
            complete = (
                profile.get("status") == "PASS"
                and profile.get("population_complete") is True
                and profile.get("phase_timing_complete") is True
                and profile.get("sumo_zero_launch_gate") is True
                and int(population.get("daily_units", 0)) == 1950
                and int(population.get("daily_variant_records", 0)) == 5850
                and int(population.get("parents", population.get(
                    "parent_schedules", 0))) == 1690
            )
            if indexes or not complete:
                raise FlowError("phase_5 NOT_TRIGGERED has contradictory evidence")
            return "NOT_TRIGGERED"
        if profile.get("phase_5_decision") == "TRIGGERED" and len(indexes) == 1 \
                and indexes[0].get("status") == "PASS":
            oracle = indexes[0].get("oracle") or {}
            if oracle.get("field_identical") is not True \
                    or oracle.get("oracle_complete") is not True:
                raise FlowError("phase_5 PASS lacks a complete field-identical oracle")
            return "PASS"
        raise FlowError("phase_5 trigger and index evidence are not a legal terminal")
    raise FlowError(f"unsupported report phase: {phase_name}")


def _phase3_gate_source_paths(
    root: Path, status_artifact: Mapping[str, Any]
) -> tuple[Path, ...]:
    """Return reviewed bounded Phase 3 bytes that can feed Gate S.

    Phase 6 is conditional on the complete Phase 0--5 prerequisites, but Gate
    S is a separate policy evaluation.  A bounded Phase 3 run can therefore
    be a valid Gate S source even when Phase 6 is ``NOT_ALLOWED`` (for
    example, because a Phase 3 speed gate was inconclusive).  Keep this
    eligibility test tied to the actual Phase 3 producer bytes rather than to
    the report's caller-selected status.
    """
    eligible_paths: list[Path] = []
    references = status_artifact.get("references")
    if not isinstance(references, list):
        return ()
    for reference in references:
        if not isinstance(reference, Mapping) or not isinstance(
            reference.get("path"), str
        ):
            continue
        path = Path(reference["path"])
        if not path.is_absolute():
            path = root / path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping) or (
            value.get("schema") != "subhour_cost_ordered_bounded_outcome_v1"
            or value.get("kind") != "subhour_bounded_sumo_outcome"
            or value.get("release_evidence") is not False
            or value.get("status") not in {
                # A complete Gate S population survives a pure speed miss;
                # execution, census, budget and source-drift terminals do not.
                "PASS", "INCONCLUSIVE_PERFORMANCE_GATE",
            }
        ):
            continue
        selection = value.get("selection")
        selected_ids = selection.get("selected_ids") if isinstance(
            selection, Mapping
        ) else None
        cases = value.get("case_results")
        gate_s = value.get("gate_s")
        registration = value.get("registration")
        if not isinstance(registration, Mapping):
            continue
        registration_path = registration.get("path")
        if not isinstance(registration_path, str) or not registration_path:
            continue
        registration_file = Path(registration_path).resolve()
        try:
            registration_file.relative_to(root.resolve())
            registration_raw = registration_file.read_bytes()
            bound_registration = json.loads(registration_raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        registration_body = {
            key: item for key, item in bound_registration.items()
            if key not in {"content_key", "registered_at"}
        } if isinstance(bound_registration, Mapping) else {}
        registered_selection = (
            bound_registration.get("selection")
            if isinstance(bound_registration, Mapping) else None)
        registered_ids = (
            registered_selection.get("selected_ids")
            if isinstance(registered_selection, Mapping) else None)
        registered_cases = (
            bound_registration.get("selected_cases")
            if isinstance(bound_registration, Mapping) else None)
        if (
            not isinstance(bound_registration, Mapping)
            or bound_registration.get("schema")
            != "subhour_cost_ordered_bounded_registration_v1"
            or registration.get("evidence_id") != value.get("evidence_id")
            or registration.get("sha256") != hashlib.sha256(
                registration_raw).hexdigest()
            or registration.get("content_key") != bound_registration.get(
                "content_key")
            or bound_registration.get("content_key") not in {
                _canonical_digest(registration_body),
                _canonical_digest(registration_body)[:32],
            }
            or registered_ids != selected_ids
            or not isinstance(registered_cases, list)
        ):
            continue
        if (
            not isinstance(selected_ids, list)
            or not selected_ids
            or any(not isinstance(item, str) or not item for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
            or not isinstance(cases, list)
            # Gate S is a decision over the complete registered population.
            # A prefix (or an empty list) is useful diagnostic information, but
            # it cannot be promoted merely because the producer happened to
            # publish a gate_s mapping alongside it.
            or len(cases) != len(selected_ids)
            or value.get("decision_population_complete") is not True
            or not isinstance(gate_s, Mapping)
            or gate_s.get("population_complete") is not True
            or not isinstance(gate_s.get("variants"), Mapping)
            or set(gate_s["variants"]) != {"q10", "q50", "q90"}
        ):
            continue
        registered_pairs = [
            (item.get("case_id"), item.get("search_content_key"))
            for item in registered_cases if isinstance(item, Mapping)
        ]
        source_pairs = [
            (item.get("case_id"), item.get("search_content_key"))
            for item in cases if isinstance(item, Mapping)
        ]
        if source_pairs != registered_pairs:
            continue
        seen_case_ids: set[str] = set()
        seen_content_keys: set[str] = set()
        valid_cases = True
        for case in cases:
            if not isinstance(case, Mapping):
                valid_cases = False
                break
            case_id = case.get("case_id")
            content_key = case.get("search_content_key")
            if (
                not isinstance(case_id, str)
                or not case_id
                or case_id in seen_case_ids
                or not isinstance(content_key, str)
                or content_key not in selected_ids
                or content_key in seen_content_keys
                or case.get("decision_population_complete") is not True
            ):
                valid_cases = False
                break
            seen_case_ids.add(case_id)
            seen_content_keys.add(content_key)
        if valid_cases and seen_content_keys == set(selected_ids):
            eligible_paths.append(path.resolve())
    return tuple(eligible_paths)


def _phase3_gate_population_is_eligible(
    root: Path, status_artifact: Mapping[str, Any]
) -> bool:
    """Return whether reviewed bounded Phase 3 bytes can feed Gate S."""
    return bool(_phase3_gate_source_paths(root, status_artifact))


def _report_producer_values(
    root: Path, status_artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Load the already digest-bound producer values for a phase."""
    if "references" not in status_artifact and isinstance(
            status_artifact.get("path"), str):
        status_path = Path(status_artifact["path"])
        if not status_path.is_absolute():
            status_path = root / status_path
        try:
            loaded_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            loaded_status = None
        if isinstance(loaded_status, Mapping):
            status_artifact = loaded_status
    values: list[dict[str, Any]] = []
    for reference in status_artifact.get("references", ()):
        if not isinstance(reference, Mapping) or not isinstance(
                reference.get("path"), str):
            continue
        path = Path(reference["path"])
        if not path.is_absolute():
            path = root / path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _report_measurement_number(
    value: Any, *, label: str, integer: bool = False,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)) or value < 0 \
            or (integer and not isinstance(value, int)):
        raise FlowError(f"Phase 0-7 report producer has invalid {label}")
    return value


def _unavailable_rss_shape(error: Any, *, label: str) -> dict[str, str]:
    """Return the only legal non-numeric RSS value in a terminal report."""
    message = str(error or "").strip()
    if not message:
        raise FlowError(f"{label} lacks the census error for unavailable RSS")
    return {"status": "unavailable", "error": message}


def _report_rss_value(
    producer: Mapping[str, Any], *, phase_status: str, label: str,
    execution_started: bool | None = None,
) -> int | dict[str, str]:
    """Validate measured RSS or an explicit fail-closed unavailable shape."""
    raw = producer.get("peak_rss_bytes")
    if raw is not None:
        if execution_started is not False and (
                producer.get("rss_status") == "unavailable"
                or producer.get("process_tree_rss_complete") is False):
            raise FlowError(
                f"{label} has a numeric value despite an incomplete census")
        return _report_measurement_number(raw, label=label, integer=True)
    if execution_started is False:
        # No process was admitted, so zero is an observed empty population, not
        # a substitute for a missing census after work started.
        return _report_measurement_number(0, label=label, integer=True)
    status = producer.get("rss_status")
    error = producer.get("rss_error")
    if status is None and producer.get("process_tree_rss_complete") is False:
        status = "unavailable"
        error = producer.get("process_tree_rss_error")
    if phase_status != "INCONCLUSIVE" or status != "unavailable":
        raise FlowError(f"Phase 0-7 report lacks producer {label}")
    return _unavailable_rss_shape(error, label=label)


def _combine_report_rss(values: Sequence[int | dict[str, str]]) -> int | dict[str, str]:
    unavailable = [value for value in values if isinstance(value, Mapping)]
    if unavailable:
        errors = sorted({str(value["error"]) for value in unavailable})
        return {"status": "unavailable", "error": "; ".join(errors)}
    return int(max(values, default=0))


def _validated_report_disk_roots(
    root: Path, producer: Mapping[str, Any], *, label: str,
) -> set[Path]:
    """Return producer-owned roots, rejecting invented/overlapping paths."""
    raw_roots = producer.get("disk_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise FlowError(f"{label} lacks bound disk roots")
    resolved_root = Path(root).resolve()
    roots: set[Path] = set()
    for raw in raw_roots:
        if not isinstance(raw, str) or not raw:
            raise FlowError(f"{label} has malformed disk roots")
        path = Path(raw).resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError as error:
            raise FlowError(f"{label} disk root is outside the report root") from error
        if path in roots or any(path in other.parents or other in path.parents
                               for other in roots):
            raise FlowError(f"{label} disk roots overlap")
        roots.add(path)
    return roots


def _phase6_publication_receipt(
    root: Path, outcome: Mapping[str, Any],
    *, expected_payload_path: Path | None = None,
) -> Mapping[str, Any] | None:
    """Validate the post-commit receipt used for READY promotion."""
    raw_path = outcome.get("publication_receipt_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError as error:
        raise FlowError("Phase 6 publication receipt is outside the report root") from error
    if not path.is_file():
        raise FlowError("Phase 6 publication receipt is missing")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowError("Phase 6 publication receipt is malformed") from error
    if not isinstance(receipt, Mapping) or receipt.get(
            "schema") != "append_only_publication_receipt_v1":
        raise FlowError("Phase 6 publication receipt has an invalid schema")
    body = {key: value for key, value in receipt.items() if key != "content_key"}
    digest = _canonical_digest(body)
    if receipt.get("content_key") not in {digest, digest[:32]}:
        raise FlowError("Phase 6 publication receipt content key is invalid")
    payload_path = Path(str(receipt.get("path", ""))).resolve()
    if not payload_path.is_file() or not payload_path.is_relative_to(Path(root).resolve()):
        raise FlowError("Phase 6 publication receipt payload path is invalid")
    if expected_payload_path is not None \
            and payload_path != Path(expected_payload_path).resolve():
        raise FlowError("Phase 6 publication receipt is bound to another outcome")
    bound_outcome_path = outcome.get("publication_outcome_path")
    if bound_outcome_path is not None \
            and payload_path != Path(str(bound_outcome_path)).resolve():
        raise FlowError("Phase 6 outcome path is not bound to its receipt")
    if receipt.get("payload_sha256") != hashlib.sha256(
            payload_path.read_bytes()).hexdigest():
        raise FlowError("Phase 6 publication receipt payload bytes drifted")
    if receipt.get("status") != outcome.get("status"):
        raise FlowError("Phase 6 publication receipt status is not bound to outcome")
    recovered = receipt.get("recovered")
    if recovered is not None and not isinstance(recovered, bool):
        raise FlowError("Phase 6 publication receipt recovery flag is invalid")
    if recovered is True:
        # A publication interrupted between the outcome commit and its
        # receipt has no observed commit time.  The recovered receipt must
        # say exactly that: a claimed time here would be invented, and a
        # claimed deadline pass would promote unverifiable evidence.
        if (receipt.get("committed_elapsed_s") is not None
                or receipt.get("within_deadline") is not False):
            raise FlowError(
                "Phase 6 recovered publication receipt claims commit timing")
    elif (isinstance(receipt.get("committed_elapsed_s"), bool)
            or not isinstance(receipt.get("committed_elapsed_s"), (int, float))
            or not math.isfinite(float(receipt["committed_elapsed_s"]))
            or receipt["committed_elapsed_s"] < 0
            or not isinstance(receipt.get("within_deadline"), bool)):
        raise FlowError("Phase 6 publication receipt timing is incomplete")
    authoritative_status = receipt.get("authoritative_status")
    if recovered is True:
        expected_authoritative = (
            "INCONCLUSIVE_PUBLICATION_UNVERIFIED"
            if receipt.get("status") == "READY"
            else receipt.get("status")
        )
    else:
        expected_authoritative = (
            "INCONCLUSIVE_BUDGET_EXHAUSTED"
            if receipt.get("status") == "READY"
            and receipt.get("within_deadline") is False
            else receipt.get("status")
        )
    if authoritative_status != expected_authoritative:
        raise FlowError("Phase 6 publication receipt terminal is not authoritative")
    if recovered is True and authoritative_status == "READY":
        raise FlowError(
            "Phase 6 recovered publication receipt cannot carry a READY terminal")
    return receipt


def _derive_report_measurements(
    root: Path, status_artifacts: Mapping[str, Any], phases: Mapping[str, str],
    phase6_outcome: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Derive final measurements from checkpoint-bound producer artifacts."""
    phase3_values = _report_producer_values(
        root, status_artifacts["phase_3"])
    phase4_values = _report_producer_values(
        root, status_artifacts["phase_4"])
    phase3 = next((item for item in phase3_values if item.get("schema") ==
                   "subhour_cost_ordered_bounded_outcome_v1"), {})
    phase4 = next((item for item in phase4_values if item.get("schema") ==
                   "monthly_cost_ledger_profile_v1"), {})

    def required_number(value: Any, *, label: str, integer: bool = False):
        if value is None:
            raise FlowError(f"Phase 0-7 report lacks producer {label}")
        return _report_measurement_number(value, label=label, integer=integer)

    # Phase 3 publishes additive suite consumption plus a peak case resource
    # summary. These are separate from Phase 6 and must never be replaced by
    # a later phase's numbers.
    phase3_consumption = phase3.get("suite_consumption")
    phase3_resources = phase3.get("resources")
    if (not isinstance(phase3_consumption, Mapping)
            or not isinstance(phase3_resources, Mapping)):
        raise FlowError("Phase 3 producer lacks complete suite resources")
    phase3_roots = _validated_report_disk_roots(
        root, phase3_resources, label="Phase 3 resources")
    phase3_attempts = (required_number(
        phase3_consumption.get("attempts"), label="Phase 3 attempts", integer=True)
        if phase3_consumption.get("attempts") is not None else
        (_report_measurement_number(0, label="Phase 3 attempts", integer=True)
         if phase3_resources.get("rss_status") in {"not_run", "unavailable"}
         and phase3_consumption.get("execution_started") is False else
         required_number(None, label="Phase 3 attempts")))
    phase3_active = (required_number(
        phase3_consumption.get("active_seconds"), label="Phase 3 active time")
        if phase3_consumption.get("active_seconds") is not None else
        required_number(None, label="Phase 3 active time"))
    phase3_peak = _report_rss_value(
        phase3_resources, phase_status=phases.get("phase_3", "INCONCLUSIVE"),
        label="Phase 3 peak RSS",
        execution_started=phase3_consumption.get("execution_started"))
    phase3_disk = (required_number(
        phase3_resources.get("disk_growth_bytes"), label="Phase 3 disk growth", integer=True)
        if phase3_resources.get("disk_growth_bytes") is not None else
        required_number(None, label="Phase 3 disk growth"))

    phase4_roots = _validated_report_disk_roots(
        root, {"disk_roots": list((phase4.get("fresh_roots") or {}).values())},
        label="Phase 4 fresh roots")
    if phase4.get("wall_time_s") is None:
        raise FlowError("Phase 4 producer lacks cold ledger timing")
    phase4_wall = (required_number(
        phase4.get("wall_time_s"), label="Phase 4 cold ledger time")
        if phase4.get("wall_time_s") is not None else
        required_number(None, label="Phase 4 cold ledger time"))
    phase4_peak = _report_rss_value(
        phase4, phase_status=phases.get("phase_4", "INCONCLUSIVE"),
        label="Phase 4 peak RSS",
        execution_started=phase4.get("execution_started", True))
    phase4_disk = (required_number(
        phase4.get("disk_growth_bytes"), label="Phase 4 disk growth", integer=True)
        if phase4.get("disk_growth_bytes") is not None else
        required_number(None, label="Phase 4 disk growth"))
    # The cold profiler observes the runner's exact launch counter before and
    # after the ledger.  A hard-coded zero here used to hide SUMO
    # contamination and also made the aggregate attempt count disagree with
    # the producer.  Require the explicit delta and its source observation.
    phase4_attempts = required_number(
        phase4.get("sumo_attempts"), label="Phase 4 SUMO attempts", integer=True)
    sumo_observation = phase4.get("sumo_start_observation")
    if not isinstance(sumo_observation, Mapping):
        raise FlowError("Phase 4 producer lacks SUMO launch observation")
    before = sumo_observation.get("before")
    after = sumo_observation.get("after")
    delta = sumo_observation.get("delta")
    for value, label in ((before, "Phase 4 SUMO launches before"),
                         (after, "Phase 4 SUMO launches after"),
                         (delta, "Phase 4 SUMO launch delta")):
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < 0):
            raise FlowError(f"Phase 4 producer has invalid {label}")
    if after < before or delta != after - before or phase4_attempts != delta:
        raise FlowError("Phase 4 SUMO attempt count is not its observed delta")
    sumo_started = phase4.get("sumo_started")
    if not isinstance(sumo_started, bool) or sumo_started != bool(before or delta):
        raise FlowError("Phase 4 SUMO contamination flag is inconsistent")

    telemetry = (phase6_outcome or {}).get("telemetry")
    if not isinstance(telemetry, Mapping):
        telemetry = phase6_outcome or {}

    phase6_attempts = phase6_active = phase6_peak = phase6_disk = 0
    phase6_commit_elapsed: float | None = None
    if phases.get("phase_6") in {"PASS", "INCONCLUSIVE"}:
        if not isinstance(phase6_outcome, Mapping):
            raise FlowError("Phase 6 measurements lack a producer outcome")
        phase6_attempts = required_number(
            telemetry.get("sumo_attempts"), label="Phase 6 attempts", integer=True)
        phase6_active = required_number(
            telemetry.get("active_elapsed_s", phase6_outcome.get("active_elapsed_s")),
            label="Phase 6 active time")
        phase6_peak = _report_rss_value(
            telemetry, phase_status=phases.get("phase_6", "INCONCLUSIVE"),
            label="Phase 6 peak RSS",
            execution_started=phase6_outcome.get("execution_started", True))
        phase6_disk = required_number(
            telemetry.get("disk_growth_bytes"), label="Phase 6 disk growth", integer=True)
        phase6_roots = _validated_report_disk_roots(
            root, telemetry, label="Phase 6 telemetry")
        phase6_receipt = _phase6_publication_receipt(root, phase6_outcome)
        if phase6_receipt is None:
            raise FlowError("Phase 6 outcome lacks its publication receipt")
        if phase6_receipt.get("recovered") is True:
            # The commit time was lost with the interrupted process.  The
            # report must carry null rather than a reconstructed number, and
            # no deadline comparison is possible against a missing fact.
            phase6_commit_elapsed = None
        else:
            phase6_commit_elapsed = _report_measurement_number(
                phase6_receipt["committed_elapsed_s"],
                label="Phase 6 committed publication time")
            phase6_budget = phase6_outcome.get("budget_telemetry")
            if isinstance(phase6_budget, Mapping) and phase6_budget.get(
                    "publication_elapsed_s") is not None:
                advertised = _report_measurement_number(
                    phase6_budget["publication_elapsed_s"],
                    label="Phase 6 budget publication time")
                if float(advertised) > float(phase6_commit_elapsed):
                    raise FlowError(
                        "Phase 6 budget publication time exceeds committed receipt time")
    else:
        phase6_roots = set()
        phase6_receipt = None

    all_roots = list(phase3_roots) + list(phase4_roots) + list(phase6_roots)
    if any(left == right or left in right.parents or right in left.parents
           for index, left in enumerate(all_roots)
           for right in all_roots[index + 1:]):
        raise FlowError("Phase producer disk roots overlap")

    # No Phase 6 artifact means no full-month execution.  A READY claim also
    # needs the producer's explicit timing proof, not merely a status string.
    full_month = bool(
        phases.get("phase_6") == "PASS"
        and isinstance(phase6_outcome, Mapping)
        and phase6_outcome.get("status") == "READY"
        and phase6_outcome.get("ready_proof_valid") is True
        and isinstance(phase6_outcome.get("work_stopped_elapsed_s"), (int, float))
        and not isinstance(phase6_outcome.get("work_stopped_elapsed_s"), bool)
        and phase6_outcome["work_stopped_elapsed_s"] <= 55 * 60
        and isinstance(phase6_outcome.get("publication_elapsed_s"), (int, float))
        and not isinstance(phase6_outcome.get("publication_elapsed_s"), bool)
        and isinstance(phase6_receipt, Mapping)
        and phase6_receipt.get("recovered") is not True
        and isinstance(phase6_receipt.get("committed_elapsed_s"), (int, float))
        and not isinstance(phase6_receipt.get("committed_elapsed_s"), bool)
        and phase6_receipt["committed_elapsed_s"] <= phase6_outcome.get(
            "publication_deadline_s", 60 * 60)
        and phase6_receipt.get("status") == "READY"
        and phase6_receipt.get("authoritative_status") == "READY"
        and phase6_receipt.get("within_deadline") is True
    )
    phase_resources = {
        "phase_3": {
            "status": phases.get("phase_3"),
            "sumo_attempts": int(phase3_attempts),
            "active_time_s": float(phase3_active),
            "peak_rss_bytes": phase3_peak,
            "disk_growth_bytes": int(phase3_disk),
            "disk_roots": sorted(str(path) for path in phase3_roots),
        },
        "phase_4": {
            "status": phases.get("phase_4"),
            "sumo_attempts": int(phase4_attempts),
            "active_time_s": float(phase4_wall),
            "peak_rss_bytes": phase4_peak,
            "disk_growth_bytes": int(phase4_disk),
            "disk_roots": sorted(str(path) for path in phase4_roots),
        },
        "phase_6": {
            "status": phases.get("phase_6"),
            "sumo_attempts": int(phase6_attempts),
            "active_time_s": float(phase6_active),
            "peak_rss_bytes": phase6_peak,
            "disk_growth_bytes": int(phase6_disk),
            "disk_roots": sorted(str(path) for path in phase6_roots),
            "publication_commit_elapsed_s": phase6_commit_elapsed,
        },
    }
    return {
        "sumo_attempts": int(phase3_attempts + phase4_attempts + phase6_attempts),
        "active_time_s": float(phase3_active + phase6_active),
        "cold_ledger_time_s": float(phase4_wall),
        "peak_rss_bytes": _combine_report_rss(
            [phase3_peak, phase4_peak, phase6_peak]),
        # Phase 3, Phase 4 and Phase 6 use separately registered roots. Their
        # growth is additive; taking a maximum would hide executed work.
        "disk_growth_bytes": int(phase3_disk + phase4_disk + phase6_disk),
        "full_month_within_60_minutes": full_month,
        "phase_resources": phase_resources,
    }


def _gate_s_is_required(
    phase6_status: str, phase3_gate_population_eligible: bool
) -> bool:
    """Apply the Phase 7 truth table without trusting report prose."""
    return phase6_status in {"PASS", "INCONCLUSIVE"} or (
        phase6_status == "NOT_ALLOWED" and phase3_gate_population_eligible
    )


def _active_generation_evidence_paths(
    root: Path, policy: EvidencePolicy,
    generation_baseline: dict[str, dict[str, str]],
) -> dict[Path, str]:
    """Return only newly appended paths, retaining their exact configured glob."""
    current = evidence_inventory(root, policy.registration_globs)
    added = _evidence_inventory_delta(generation_baseline, current)
    resolved_root = root.resolve()
    paths: dict[Path, str] = {}
    for pattern in policy.registration_globs:
        for relative in added.get(pattern, {}):
            candidate = (resolved_root / relative).resolve()
            try:
                candidate.relative_to(resolved_root)
            except ValueError:
                raise FlowError(
                    f"Evidence inventory produced a path outside the repository: {relative}"
                )
            if not fnmatch.fnmatchcase(relative, pattern):
                raise FlowError(
                    f"Evidence inventory path does not match its configured glob: {relative}"
                )
            paths[candidate] = pattern
    return paths


def validate_post_review_terminal_artifacts(
    root: Path, policy: EvidencePolicy, source_freeze: dict[str, Any],
    checkpoint: dict[str, Any], phase_review: dict[str, Any],
    generation_baseline: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Require a complete, digest-bound terminal Phase 0--7 report.

    The report is producer output, not controller prose.  A missing report,
    malformed terminal, or artifact whose bytes are not referenced by it is a
    hard protocol error before the controller can approve the run.
    """
    patterns = policy.phase_report_globs or ("validation/subhour_phase_report_*.json",)
    current = evidence_inventory(root, patterns)
    added = _evidence_inventory_delta(generation_baseline, current)
    active_evidence_paths = _active_generation_evidence_paths(
        root, policy, generation_baseline
    )
    report_paths = [
        root / relative
        for pattern in patterns
        for relative in added.get(pattern, {})
    ]
    if len(report_paths) != 1:
        raise FlowError(
            "Final review requires exactly one fresh complete Phase 0-7 report"
        )
    report_path = report_paths[0].resolve()
    raw = report_path.read_bytes()
    report = json.loads(raw.decode("utf-8"))
    if not isinstance(report, dict) \
            or report.get("schema") != _PHASE_REPORT_SCHEMA \
            or report.get("kind") != _PHASE_REPORT_KIND \
            or report.get("release_evidence") is not False \
            or report.get("status") != "COMPLETE":
        raise FlowError("Phase 0-7 report has an invalid schema, kind or boundary")
    report_body = {key: value for key, value in report.items() if key != "content_key"}
    if report.get("content_key") != _canonical_digest(report_body):
        raise FlowError("Phase 0-7 report content key is invalid")
    phases = report.get("phases")
    if not isinstance(phases, dict) or set(phases) != set(_PHASE_REPORT_PHASES):
        raise FlowError("Phase 0-7 report must contain every phase exactly once")
    if any(phases[name] not in _PHASE_TERMINAL_STATUSES for name in _PHASE_REPORT_PHASES):
        raise FlowError("Phase 0-7 report contains an invalid terminal status")

    evidence_ids = report.get("evidence_ids")
    if not isinstance(evidence_ids, dict) \
            or set(evidence_ids) != set(_PHASE_REPORT_PHASES):
        raise FlowError("Phase 0-7 report must list evidence IDs for every phase")
    for phase_name, ids in evidence_ids.items():
        if (not isinstance(ids, list)
                or any(not isinstance(item, str) or not item for item in ids)
                or len(set(ids)) != len(ids)):
            raise FlowError(f"Phase 0-7 report evidence IDs are malformed for {phase_name}")
        if phases[phase_name] not in {"NOT_ALLOWED", "NOT_TRIGGERED"} and not ids:
            raise FlowError(f"Phase 0-7 report lacks evidence IDs for {phase_name}")

    measurements = report.get("measurements")
    required_measurements = {
        "sumo_attempts", "active_time_s", "cold_ledger_time_s",
        "peak_rss_bytes", "disk_growth_bytes", "full_month_within_60_minutes",
    }
    if not isinstance(measurements, dict) \
            or set(measurements) != required_measurements:
        raise FlowError("Phase 0-7 report lacks complete requested measurements")
    peak_measurement = measurements.get("peak_rss_bytes")
    peak_is_unavailable = (
        isinstance(peak_measurement, dict)
        and set(peak_measurement) == {"status", "error"}
        and peak_measurement.get("status") == "unavailable"
        and isinstance(peak_measurement.get("error"), str)
        and bool(peak_measurement["error"].strip())
    )
    if (isinstance(measurements.get("sumo_attempts"), bool)
            or not isinstance(measurements.get("sumo_attempts"), int)
            or measurements["sumo_attempts"] < 0
            or (not peak_is_unavailable and (
                isinstance(peak_measurement, bool)
                or not isinstance(peak_measurement, int)
                or peak_measurement < 0))
            or isinstance(measurements.get("disk_growth_bytes"), bool)
            or not isinstance(measurements.get("disk_growth_bytes"), int)
            or measurements["disk_growth_bytes"] < 0):
        raise FlowError("Phase 0-7 report has invalid integer measurements")
    for key in ("active_time_s", "cold_ledger_time_s"):
        if (isinstance(measurements.get(key), bool)
                or not isinstance(measurements.get(key), (int, float))
                or not math.isfinite(float(measurements[key]))
                or measurements[key] < 0):
            raise FlowError(f"Phase 0-7 report has invalid {key} measurement")
    if not isinstance(measurements.get("full_month_within_60_minutes"), bool):
        raise FlowError("Phase 0-7 report has an invalid full-month timing result")
    phase_resources = report.get("phase_resources")
    if not isinstance(phase_resources, dict) or set(phase_resources) != {
            "phase_3", "phase_4", "phase_6"}:
        raise FlowError("Phase 0-7 report lacks explicit Phase 3/4/6 resources")
    for phase_name, resource in phase_resources.items():
        if not isinstance(resource, dict):
            raise FlowError(f"{phase_name} report resources are malformed")
        required = {"status", "sumo_attempts", "active_time_s",
                    "peak_rss_bytes", "disk_growth_bytes", "disk_roots"}
        if phase_name == "phase_6":
            required.add("publication_commit_elapsed_s")
        if set(resource) != required:
            raise FlowError(f"{phase_name} report resources are incomplete")
        if resource.get("status") not in _PHASE_TERMINAL_STATUSES:
            raise FlowError(f"{phase_name} report resource status is invalid")
        if (isinstance(resource.get("sumo_attempts"), bool)
                or not isinstance(resource.get("sumo_attempts"), int)
                or resource["sumo_attempts"] < 0
                or (isinstance(resource.get("peak_rss_bytes"), dict)
                    and not (
                        set(resource["peak_rss_bytes"]) == {"status", "error"}
                        and resource["peak_rss_bytes"].get("status")
                        == "unavailable"
                        and isinstance(resource["peak_rss_bytes"].get("error"), str)
                        and bool(resource["peak_rss_bytes"]["error"].strip())
                    ))
                or (not isinstance(resource.get("peak_rss_bytes"), dict)
                    and (isinstance(resource.get("peak_rss_bytes"), bool)
                         or not isinstance(resource.get("peak_rss_bytes"), int)
                         or resource["peak_rss_bytes"] < 0))
                or isinstance(resource.get("disk_growth_bytes"), bool)
                or not isinstance(resource.get("disk_growth_bytes"), int)
                or resource["disk_growth_bytes"] < 0
                or not isinstance(resource.get("disk_roots"), list)):
                raise FlowError(f"{phase_name} report resources have invalid integers")
        if isinstance(resource.get("peak_rss_bytes"), dict) and (
                resource.get("status") != "INCONCLUSIVE"):
            raise FlowError(
                f"{phase_name} unavailable RSS is only legal for INCONCLUSIVE")
        if (isinstance(resource.get("active_time_s"), bool)
                or not isinstance(resource.get("active_time_s"), (int, float))
                or not math.isfinite(float(resource["active_time_s"]))
                or resource["active_time_s"] < 0):
            raise FlowError(f"{phase_name} report resources have invalid active time")
        if phase_name == "phase_6" and resource.get(
                "publication_commit_elapsed_s") is not None:
            _report_measurement_number(
                resource["publication_commit_elapsed_s"],
                label="Phase 6 report committed publication time")
    status_artifacts = report.get("status_artifacts")
    required_status_phases = _PHASE_REPORT_PHASES[:6]
    if not isinstance(status_artifacts, dict) \
            or set(status_artifacts) != set(required_status_phases):
        raise FlowError("Phase 0-7 report lacks phase status artifacts")
    raw_checkpoint_inventory = checkpoint.get("artifact_inventory")
    if not isinstance(raw_checkpoint_inventory, dict):
        raise FlowError("Reviewed checkpoint lacks an artifact inventory")
    checkpoint_inventory: dict[Path, str] = {}
    for pattern in policy.phase_checkpoint_globs:
        records = raw_checkpoint_inventory.get(pattern, {})
        if not isinstance(records, dict):
            raise FlowError("Reviewed checkpoint artifact inventory is malformed")
        for relative, digest in records.items():
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise FlowError("Reviewed checkpoint artifact inventory is malformed")
            checkpoint_inventory[(root.resolve() / relative).resolve()] = digest
    referenced_checkpoint_inventory: dict[Path, str] = {}
    for phase_name in required_status_phases:
        _, status_artifact = _validate_report_reference(
            root, status_artifacts[phase_name], label=f"{phase_name}_status",
            expected_schema="subhour_phase_status_v1",
            expected_kind="subhour_phase_status",
        )
        if (status_artifact.get("phase") != phase_name
                or status_artifact.get("status") != phases[phase_name]
                or status_artifact.get("release_evidence") is not False):
            raise FlowError(f"{phase_name} report status is not derived from its artifact")
        derived_status = _derive_report_phase_status(
            root, phase_name, status_artifact)
        if status_artifact.get("status") != derived_status:
            raise FlowError(
                f"{phase_name} report status is not derived from producer evidence"
            )
        status_evidence_id = status_artifact.get("evidence_id")
        if not isinstance(status_evidence_id, str) \
                or status_evidence_id not in evidence_ids[phase_name]:
            raise FlowError(f"{phase_name} status artifact evidence ID is not reported")
        if phase_name in {"phase_3", "phase_4", "phase_5"}:
            producer_ids = {
                str(value["evidence_id"])
                for value in _report_producer_values(root, status_artifact)
                if isinstance(value.get("evidence_id"), str)
                and value.get("evidence_id")
            }
            if not producer_ids and phases[phase_name] not in {
                "NOT_ALLOWED", "NOT_TRIGGERED"
            }:
                raise FlowError(
                    f"{phase_name} producer artifact lacks an evidence ID"
                )
            expected_ids = {status_evidence_id} | producer_ids
            if set(evidence_ids[phase_name]) != expected_ids:
                raise FlowError(
                    f"{phase_name} evidence IDs do not exactly match producer artifacts"
                )
        if phase_name in {"phase_3", "phase_4", "phase_5"}:
            references = status_artifact.get("references")
            if not isinstance(references, list) or not references:
                raise FlowError(
                    f"{phase_name} status artifact lacks current evidence references"
                )
            for reference in references:
                if not isinstance(reference, dict) or not isinstance(
                    reference.get("path"), str
                ):
                    raise FlowError(f"{phase_name} status artifact has an invalid evidence reference")
                ref_path = Path(reference["path"])
                if not ref_path.is_absolute():
                    ref_path = root / ref_path
                ref_path = ref_path.resolve()
                if ref_path not in active_evidence_paths:
                    raise FlowError(
                        f"{phase_name} status artifact references stale or unregistered evidence"
                    )
                actual_sha256 = hashlib.sha256(ref_path.read_bytes()).hexdigest()
                if reference.get("sha256") != actual_sha256:
                    raise FlowError(f"{phase_name} status artifact evidence bytes drifted")
                try:
                    producer = json.loads(ref_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise FlowError(
                        f"{phase_name} status artifact evidence is not valid JSON"
                    ) from error
                producer_key = producer.get("content_key") if isinstance(
                    producer, Mapping) else None
                if (not isinstance(producer_key, str)
                        or reference.get("content_key") != producer_key):
                    raise FlowError(
                        f"{phase_name} status artifact content key is not bound"
                    )
                producer_body = {
                    key: value for key, value in producer.items()
                    if key != "content_key"
                }
                producer_digest = _canonical_digest(producer_body)
                if producer_key not in {producer_digest, producer_digest[:32]}:
                    raise FlowError(
                        f"{phase_name} producer content key is invalid"
                    )
                expected_sha256 = checkpoint_inventory.get(ref_path)
                if expected_sha256 != actual_sha256:
                    raise FlowError(
                        f"{phase_name} status artifact is not bound to the reviewed checkpoint"
                    )
                referenced_checkpoint_inventory[ref_path] = actual_sha256
    if referenced_checkpoint_inventory != checkpoint_inventory:
        raise FlowError(
            "Phase 3-5 status artifacts do not cover the complete reviewed evidence inventory"
        )
    status_by_phase = {
        phase_name: _derive_report_phase_status(
            root, phase_name,
            _validate_report_reference(
                root, status_artifacts[phase_name],
                label=f"{phase_name}_status_recheck",
                expected_schema="subhour_phase_status_v1",
                expected_kind="subhour_phase_status",
            )[1],
        )
        for phase_name in required_status_phases
    }
    phase6_prerequisites_green = (
        all(status_by_phase[name] == "PASS"
            for name in ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4"))
        and status_by_phase["phase_5"] in {"PASS", "NOT_TRIGGERED"}
        and phase_review.get("status") == "PASS"
    )
    phase6_status = phases["phase_6"]
    phase7_status = phases["phase_7"]
    phase3_status_artifact = _validate_report_reference(
            root, status_artifacts["phase_3"],
            label="phase_3_status_gate_source",
            expected_schema="subhour_phase_status_v1",
            expected_kind="subhour_phase_status",
        )[1]
    phase3_gate_source_paths = set(_phase3_gate_source_paths(
        root, phase3_status_artifact
    ))
    phase3_gate_population_eligible = bool(phase3_gate_source_paths)
    if phase6_prerequisites_green:
        if phase6_status not in {"PASS", "INCONCLUSIVE"}:
            raise FlowError(
                "Phase 6 must execute when Phases 0-5 and the independent checkpoint review are green"
            )
    elif phase6_status != "NOT_ALLOWED":
        raise FlowError(
            "Phase 6 is not allowed without mechanically green Phase 0-5 prerequisites"
        )
    if _gate_s_is_required(phase6_status, phase3_gate_population_eligible):
        if phase7_status not in {"PASS", "INCONCLUSIVE"}:
            raise FlowError(
                "Phase 7 must execute after eligible bounded or full-month evidence"
            )
    elif phase7_status != "NOT_TRIGGERED":
        raise FlowError(
            "Phase 7 may be NOT_TRIGGERED only when no eligible bounded or full-month evidence exists"
        )
    lineage = report.get("lineage")
    if not isinstance(lineage, dict) \
            or lineage.get("source_digest") != source_freeze.get("digest") \
            or lineage.get("checkpoint_content_digest") != checkpoint.get("content_digest") \
            or lineage.get("review_content_digest") != phase_review.get("content_digest") \
            or lineage.get("review_lineage_digest") != checkpoint.get("lineage_digest"):
        raise FlowError("Phase 0-7 report lineage does not match the reviewed checkpoint")

    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise FlowError("Phase 0-7 report lacks terminal artifact references")
    phase6_required = phase6_status in {"PASS", "INCONCLUSIVE"}
    phase7_required = phase7_status in {"PASS", "INCONCLUSIVE"}

    phase6_registration = phase6_outcome = None
    phase6_outcome_path: Path | None = None
    if phase6_required:
        _, phase6_registration = _validate_report_reference(
            root, artifacts.get("phase_6_registration"),
            label="phase_6_registration",
            expected_schema="subhour_full_month_registration_v1",
            expected_kind="subhour_full_month_registration",
            active_evidence_paths=active_evidence_paths,
        )
        phase6_outcome_path, phase6_outcome = _validate_report_reference(
            root, artifacts.get("phase_6_outcome"), label="phase_6_outcome",
            expected_schema="subhour_full_month_outcome_v1",
            expected_kind="subhour_full_month_outcome",
            active_evidence_paths=active_evidence_paths,
        )
        registration_ref = phase6_outcome.get("registration") or {}
        if (registration_ref.get("evidence_id") != phase6_registration.get("evidence_id")
                or registration_ref.get("content_key") != phase6_registration.get("content_key")):
            raise FlowError("Phase 6 outcome is not bound to its registration")
        phase6_receipt = _phase6_publication_receipt(
            root, phase6_outcome, expected_payload_path=phase6_outcome_path)
        if phase6_receipt is None:
            raise FlowError("Phase 6 outcome lacks its publication receipt")
        phase6_outcome_status = phase6_outcome.get("status")
        phase6_authoritative_status = phase6_receipt.get("authoritative_status")
        if phase6_status == "PASS":
            if (phase6_outcome_status != "READY"
                    or phase6_outcome.get("ready_proof_valid") is not True
                    or phase6_authoritative_status != "READY"
                    or measurements["full_month_within_60_minutes"] is not True):
                raise FlowError("Phase 6 PASS is not backed by a READY proof within 60 minutes")
        elif not (
            isinstance(phase6_outcome_status, str)
            and phase6_outcome_status.startswith("INCONCLUSIVE")
            or isinstance(phase6_authoritative_status, str)
            and phase6_authoritative_status.startswith("INCONCLUSIVE")
        ):
            raise FlowError("Phase 6 INCONCLUSIVE does not match its terminal outcome")
        phase6_ids = set(evidence_ids["phase_6"])
        if phase6_ids != {str(phase6_registration.get("evidence_id"))}:
            raise FlowError("Phase 6 evidence IDs do not match its registration")
    elif measurements["full_month_within_60_minutes"] is not False:
        raise FlowError("A Phase 6 NOT_ALLOWED/NOT_TRIGGERED report cannot claim a full month")
    elif any(key in artifacts for key in ("phase_6_registration", "phase_6_outcome")):
        raise FlowError("Phase 6 NOT_ALLOWED/NOT_TRIGGERED must not publish Phase 6 artifacts")

    derived_measurements = _derive_report_measurements(
        root, status_artifacts, phases, phase6_outcome
    )
    derived_aggregates = {
        key: value for key, value in derived_measurements.items()
        if key != "phase_resources"
    }
    if measurements != derived_aggregates:
        # The explicit per-phase resources are intentionally outside the six
        # aggregate measurement keys for compatibility with existing report
        # consumers, but are still producer-derived and mandatory.
        raise FlowError(
            "Phase 0-7 report measurements are not derived from producer artifacts"
        )
    if phase_resources != derived_measurements["phase_resources"]:
        raise FlowError(
            "Phase 0-7 report phase resources are not derived from producer artifacts"
        )

    if phase7_required:
        gate_evidence_path, gate_evidence = _validate_report_reference(
            root, artifacts.get("phase_7_evidence"), label="phase_7_evidence",
            expected_schema="subhour_gate_s_evidence_v1",
            expected_kind="subhour_gate_s_evidence",
            active_evidence_paths=active_evidence_paths,
        )
        _, gate_outcome = _validate_report_reference(
            root, artifacts.get("phase_7_outcome"), label="phase_7_outcome",
            expected_schema="subhour_gate_s_outcome_v2",
            expected_kind="subhour_gate_s_outcome",
            active_evidence_paths=active_evidence_paths,
        )
        gate_registration_path, gate_registration = _validate_report_reference(
            root, artifacts.get("phase_7_registration"), label="phase_7_registration",
            expected_schema="subhour_gate_s_registration_v2",
            expected_kind="subhour_gate_s_registration",
            active_evidence_paths=active_evidence_paths,
        )
        bound = gate_registration.get("bound_evidence") or {}
        if (bound.get("path") != str(gate_evidence_path)
                or bound.get("sha256") != hashlib.sha256(gate_evidence_path.read_bytes()).hexdigest()
                or bound.get("content_key") != gate_evidence.get("content_key")):
            raise FlowError("Gate S registration is not bound to its evidence")
        outcome_registration = gate_outcome.get("registration") or {}
        if (outcome_registration.get("evidence_id") != gate_registration.get("evidence_id")
                or outcome_registration.get("content_key") != gate_registration.get("content_key")):
            raise FlowError("Gate S outcome is not bound to its registration")
        source_lineage = gate_evidence.get("source_lineage") or {}
        source_path = Path(str(source_lineage.get("path", ""))).resolve()
        source_matches_phase6 = (
            phase6_outcome_path is not None
            and source_path == phase6_outcome_path
            and source_lineage.get("sha256") == hashlib.sha256(
                phase6_outcome_path.read_bytes()).hexdigest()
            and source_lineage.get("content_key") == phase6_outcome.get("content_key")
            and isinstance(phase6_outcome, Mapping)
            and (
                isinstance(phase6_outcome.get("status"), str)
                and phase6_outcome.get("status", "").startswith("INCONCLUSIVE")
                or phase6_receipt is not None
                and phase6_receipt.get("authoritative_status") == "READY"
            )
        )
        source_matches_reviewed_phase3 = False
        if source_path in checkpoint_inventory and source_path in active_evidence_paths:
            if source_lineage.get("sha256") == hashlib.sha256(
                    source_path.read_bytes()).hexdigest() \
                    and source_lineage.get("sha256") == checkpoint_inventory[source_path]:
                try:
                    source_artifact = json.loads(source_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    source_artifact = None
                source_matches_reviewed_phase3 = (
                    source_path in phase3_gate_source_paths
                    and isinstance(source_artifact, dict)
                    and isinstance(source_artifact.get("content_key"), str)
                    and source_artifact.get("content_key") == source_lineage.get(
                        "content_key"
                    )
                )
        if not (source_matches_phase6 or source_matches_reviewed_phase3):
            raise FlowError(
                "Gate S source is not bound to the fresh Phase 3 or Phase 6 outcome"
            )
        allowed_gate_statuses = {"ROBUST_THREE_VARIANT", "FINALIST_STRESS", "Q50_ONLY", "INCONCLUSIVE"}
        if gate_outcome.get("status") not in allowed_gate_statuses:
            raise FlowError("Gate S outcome has an invalid terminal status")
        expected_phase7 = (
            "INCONCLUSIVE" if gate_outcome.get("status") == "INCONCLUSIVE" else "PASS"
        )
        if phase7_status != expected_phase7:
            raise FlowError("Phase 7 report status does not match Gate S terminal status")
        if set(evidence_ids["phase_7"]) != {
            str(gate_registration.get("evidence_id")),
            str(gate_evidence.get("evidence_id")),
        }:
            raise FlowError("Phase 7 evidence IDs do not match Gate S artifacts")
    elif any(key in artifacts for key in (
        "phase_7_registration", "phase_7_evidence", "phase_7_outcome"
    )):
        raise FlowError("Phase 7 NOT_ALLOWED/NOT_TRIGGERED must not publish Gate S artifacts")
    return report


def assert_evidence_budget(
    root: Path,
    policy: EvidencePolicy,
    baseline: dict[str, dict[str, str]],
    preserved: dict[str, dict[str, str]] | None = None,
) -> None:
    """Reject mutation/deletion and fail when a series exceeds its run budget."""
    current = evidence_inventory(root, policy.registration_globs)
    preserved = baseline if preserved is None else preserved
    for pattern in policy.registration_globs:
        before_records = baseline.get(pattern, {})
        preserved_records = preserved.get(pattern, {})
        current_records = current.get(pattern, {})
        before = set(before_records)
        now = set(current_records)
        removed = sorted(set(preserved_records) - now)
        if removed:
            raise FlowError(
                f"Append-only evidence was removed for {pattern}: {removed}"
            )
        modified = sorted(
            path
            for path in set(preserved_records) & now
            if preserved_records[path] != current_records[path]
        )
        if modified:
            raise FlowError(
                f"Append-only evidence was modified for {pattern}: {modified}"
            )
        added = sorted(now - before)
        if len(added) > policy.max_new_registrations_per_glob:
            raise FlowError(
                "Evidence registration budget exceeded for "
                f"{pattern}: {len(added)} new files, limit "
                f"{policy.max_new_registrations_per_glob}; files={added}"
            )


def assert_source_frozen(
    root: Path, policy: EvidencePolicy, frozen: dict[str, Any]
) -> None:
    current = source_manifest(root, policy.source_globs)
    if current.get("digest") != frozen.get("digest"):
        before = {item["path"]: item["sha256"] for item in frozen.get("files", [])}
        after = {item["path"]: item["sha256"] for item in current.get("files", [])}
        changed = sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )
        raise FlowError(
            "Protected source changed after CODE_APPROVED; evidence was stopped "
            f"before further work. changed={changed}"
        )


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
    _stage(f"{role_stage_label(config.roles['worker'])} WORKER")
    run_work_role(config, "worker", root, run_dir, worker_prompt, "worker-01", True)
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


def validate_ready_plan_contract(root: Path, plan: Mapping[str, Any]) -> None:
    """Validate a reusable READY plan against the complete canonical schema.

    The planner output is an interchange document, so checking a handful of
    fields here is not sufficient: a READY document with the right status can
    otherwise smuggle in missing, mistyped, or additional fields.  Keep this
    small validator local rather than making plan reuse depend on an optional
    third-party package; it implements the schema keywords used by the
    repository's canonical plan contract, including the conditional
    ``blocked_reason`` rule.
    """
    schema_path = root / ".ai-flow" / "schemas" / "plan.json"
    schema = read_json(schema_path, "canonical plan schema")
    if not isinstance(plan, Mapping):
        raise FlowError("Reusable READY plan must be a JSON object")
    if plan.get("status") != "READY":
        raise FlowError("--reuse-plan-from requires a source plan with status READY")

    def schema_error(path: str, message: str) -> FlowError:
        return FlowError(
            f"Reusable READY plan violates canonical schema at {path or '$'}: {message}"
        )

    def matches(value: Any, expected: str) -> bool:
        # JSON Schema's integer excludes booleans, unlike Python's bool/int
        # relationship.  This distinction is important for contract fields.
        if expected == "object":
            return isinstance(value, Mapping)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return (isinstance(value, (int, float))
                    and not isinstance(value, bool))
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "null":
            return value is None
        return False

    def validate(value: Any, contract: Mapping[str, Any], path: str = "") -> None:
        if not isinstance(contract, Mapping):
            raise schema_error(path, "schema node is malformed")
        expected_type = contract.get("type")
        if expected_type is not None:
            types = expected_type if isinstance(expected_type, list) else [expected_type]
            if (not types or not all(isinstance(item, str) for item in types)
                    or not any(matches(value, item) for item in types)):
                detail = f"expected JSON type {expected_type!r}"
                if expected_type == "array" and isinstance(contract.get("items"), Mapping) \
                        and contract["items"].get("type") == "string":
                    detail += " (array of strings)"
                raise schema_error(path, detail)
        if "enum" in contract:
            values = contract["enum"]
            if (not isinstance(values, list) or
                    not any(type(value) is type(item) and value == item
                            for item in values)):
                raise schema_error(path, f"value is not in enum {values!r}")
        if "const" in contract:
            constant = contract["const"]
            if type(value) is not type(constant) or value != constant:
                raise schema_error(path, f"value must equal {constant!r}")
        if isinstance(value, str):
            minimum = contract.get("minLength")
            if minimum is not None and (not isinstance(minimum, int)
                                        or len(value) < minimum):
                raise schema_error(path, "string is shorter than minLength")
            pattern = contract.get("pattern")
            if pattern is not None:
                if not isinstance(pattern, str):
                    raise schema_error(path, "schema pattern is malformed")
                try:
                    found = re.search(pattern, value)
                except re.error as error:
                    raise schema_error(path, "schema pattern is malformed") from error
                if found is None:
                    raise schema_error(path, "string does not match pattern")
        if isinstance(value, Mapping):
            properties = contract.get("properties", {})
            required = contract.get("required", [])
            if not isinstance(properties, Mapping) or not isinstance(required, list):
                raise schema_error(path, "object schema is malformed")
            missing = [name for name in required
                       if not isinstance(name, str) or name not in value]
            if missing:
                raise schema_error(path, f"missing required fields {sorted(missing)!r}")
            if contract.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    raise schema_error(path, f"additional fields {extra!r}")
            for name, child in properties.items():
                if name in value:
                    validate(value[name], child, f"{path}.{name}" if path else name)
        if isinstance(value, list) and "items" in contract:
            item_contract = contract["items"]
            for index, item in enumerate(value):
                validate(item, item_contract, f"{path}[{index}]")
        conditional = contract.get("if")
        if conditional is not None:
            applies = True
            try:
                validate(value, conditional, path)
            except FlowError:
                applies = False
            if applies and "then" in contract:
                validate(value, contract["then"], path)
            elif not applies and "else" in contract:
                validate(value, contract["else"], path)
        for branch in contract.get("allOf", []):
            validate(value, branch, path)

    validate(plan, schema)
_STAGED_SOURCE_STATE_REQUIRED = (
    "schema_version", "status", "task", "next_stage", "execution_cycle",
    "review_cycles", "test_fix_cycles", "evidence_generations",
    "active_invocation", "active_session_id", "started_at",
)
_STAGED_V3_NEXT_STAGES = frozenset({
    "planner", "worker", "worker_unblock", "checks", "test_fix",
    "review", "review_fix", "code_checks", "code_test_fix",
    "code_review", "code_review_fix", "evidence",
    "phase_checkpoint_checks", "phase_checkpoint_test_fix",
    "phase_3_5_review", "phase_3_5_review_fix", "post_review_evidence",
    "final_checks", "final_review", "final_review_fix", "complete",
})


def validate_staged_source_state(state: Mapping[str, Any]) -> None:
    """Validate the stable state contract required before plan adoption."""
    if not isinstance(state, Mapping):
        raise FlowError("Reusable plan source state is missing or malformed")
    if state.get("workflow_protocol") != "staged_evidence_v3":
        raise FlowError("Reusable plan comes from an incompatible workflow protocol")
    missing = [name for name in _STAGED_SOURCE_STATE_REQUIRED
               if name not in state]
    if missing:
        raise FlowError(
            "Reusable plan source state is not staged-compatible; missing "
            f"fields: {missing}"
        )
    if (isinstance(state["schema_version"], bool)
            or not isinstance(state["schema_version"], int)
            or state["schema_version"] != 1):
        raise FlowError("Reusable plan source state has an invalid schema_version")
    if not isinstance(state["status"], str) or state["status"] not in {
            "RUNNING", "APPROVED", "BLOCKED"}:
        raise FlowError("Reusable plan source state has an invalid status")
    if not isinstance(state["task"], str) or not state["task"].strip():
        raise FlowError("Reusable plan source state has an invalid task")
    if (not isinstance(state["next_stage"], str)
            or state["next_stage"] not in _STAGED_V3_NEXT_STAGES):
        raise FlowError("Reusable plan source state has an invalid next_stage")
    try:
        started_at = datetime.fromisoformat(
            state["started_at"].replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise FlowError(
            "Reusable plan source state has an invalid started_at"
        ) from error
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise FlowError(
            "Reusable plan source state started_at must include a timezone"
        )
    # A READY plan is only reusable after its source run has reached a
    # terminal staged-v3 state.  A RUNNING state can contain an optimistic
    # planner result that has not passed its worker/check/review boundary.
    if state["status"] == "RUNNING":
        raise FlowError(
            "Reusable plan source state must be terminal before adoption"
        )
    if state["next_stage"] != "complete":
        raise FlowError(
            "Reusable plan source state has a terminal status before complete stage"
        )
    for name in ("execution_cycle", "review_cycles", "test_fix_cycles",
                 "evidence_generations"):
        if (isinstance(state[name], bool) or not isinstance(state[name], int)
                or state[name] < 0):
            raise FlowError(f"Reusable plan source state has an invalid {name}")
    for name in ("active_invocation", "active_session_id"):
        if (state[name] is not None
                and (not isinstance(state[name], str)
                     or not state[name].strip())):
            raise FlowError(f"Reusable plan source state has an invalid {name}")
    if state["status"] != "RUNNING" and any(
            state[name] is not None
            for name in ("active_invocation", "active_session_id")
    ):
        raise FlowError(
            "Reusable plan source state has active work after terminal status"
        )
    if state["active_session_id"] is not None \
            and state["active_invocation"] is None:
        raise FlowError(
            "Reusable plan source state has a session without an invocation"
        )


def reuse_ready_plan(
    root: Path, run_dir: Path, source_run_id: str
) -> dict[str, Any]:
    """Copy one reviewed READY planning artifact into a fresh bounded run.

    This is an explicit continuation optimization, not an implicit cache.  The
    new task is still supplied independently to the worker and every reviewer,
    while the exact source-plan identity is persisted for auditability.
    """
    source_run_dir = _resume_run_dir(root, source_run_id)
    source_state = _read_existing_json(source_run_dir / "state.json", "source run state")
    validate_staged_source_state(source_state)
    # A READY plan may itself have been copied from another run.  Re-hashing
    # only the immediate source would make a changed ancestor look like a
    # valid fresh plan, so verify the complete recorded ancestry before this
    # run adopts the source bytes.
    if source_state.get("reused_plan") is not None:
        verify_reused_plan_binding(
            root, source_run_dir, source_state["reused_plan"]
        )
    source_path = source_run_dir / "plan.json"
    plan = _read_existing_json(source_path, "reusable planner output")
    if not isinstance(plan, dict):
        raise FlowError("--reuse-plan-from requires a source plan object")
    validate_ready_plan_contract(root, plan)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    # The plan only qualified because its source run had reached a terminal
    # staged-v3 state.  That state file stays writable, so its identity is
    # part of the provenance and is bound here rather than re-derived later.
    source_state_sha256 = hashlib.sha256(
        (source_run_dir / "state.json").read_bytes()).hexdigest()
    target_path = run_dir / "plan.json"
    if target_path.exists():
        raise FlowError("Fresh run already contains plan.json")
    _atomic_write_json(target_path, plan)
    return {
        "source_run_id": source_run_id,
        "source_path": str(source_path.relative_to(root)),
        "source_sha256": source_sha256,
        "source_state_sha256": source_state_sha256,
        "target_sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
        "reused_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_reused_plan_binding(
    root: Path, run_dir: Path, binding: Mapping[str, Any],
    *, _seen: set[Path] | None = None,
    _lineage: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Verify that a reused plan and its recorded source bytes still match.

    Returns the complete recomputed lineage, one record per adoption edge,
    ordered from this run's immediate source upward.  Each record carries the
    ancestor's plan bytes and its terminal state bytes, so a later comparison
    can detect a mutation anywhere in the ancestry, not only at its head.
    """
    if not isinstance(binding, Mapping):
        raise FlowError("Reused plan binding is malformed")
    source_name = binding.get("source_path")
    source_run_id = binding.get("source_run_id")
    source_sha256 = binding.get("source_sha256")
    target_sha256 = binding.get("target_sha256")
    if (not isinstance(source_name, str) or not source_name
            or not isinstance(source_run_id, str) or not source_run_id
            or not isinstance(source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
            or not isinstance(target_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", target_sha256) is None
            or not isinstance(binding.get("reused_at"), str)):
        raise FlowError("Reused plan binding is incomplete")
    recorded_state_sha256 = binding.get("source_state_sha256")
    if recorded_state_sha256 is not None and (
            not isinstance(recorded_state_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", recorded_state_sha256) is None):
        raise FlowError("Reused plan binding is incomplete")
    try:
        reused_at = datetime.fromisoformat(
            binding["reused_at"].replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise FlowError("Reused plan binding has an invalid reused_at") from error
    if reused_at.tzinfo is None or reused_at.utcoffset() is None:
        raise FlowError("Reused plan binding reused_at must include a timezone")
    root_path = Path(root).resolve()
    current_run_path = Path(run_dir).resolve()
    seen = set() if _seen is None else set(_seen)
    if current_run_path in seen:
        raise FlowError("Reused plan ancestry contains a cycle")
    seen.add(current_run_path)
    source_path = (root_path / source_name).resolve()
    target_path = (current_run_path / "plan.json").resolve()
    try:
        source_path.relative_to(root_path)
        target_path.relative_to(root_path)
    except ValueError as error:
        raise FlowError("Reused plan binding points outside the repository") from error
    expected_source_path = (
        root_path / ".ai-flow" / "runs" / source_run_id / "plan.json"
    ).resolve()
    if source_path != expected_source_path:
        raise FlowError("Reused plan binding source path does not match source run")
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256:
        raise FlowError("Reused plan source bytes drifted")
    if not target_path.is_file() or hashlib.sha256(target_path.read_bytes()).hexdigest() != target_sha256:
        raise FlowError("Reused plan target bytes drifted")
    try:
        source_value = json.loads(source_path.read_text(encoding="utf-8"))
        target_value = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowError("Reused plan source or target JSON is malformed") from error
    if source_value != target_value:
        raise FlowError("Reused plan source and target content differs")
    # A source run may itself have copied a READY plan.  Validate that recorded
    # edge as well, so a later reuse cannot promote an unchecked ancestor.
    source_state_path = source_path.parent / "state.json"
    source_state = _read_existing_json(source_state_path, "reused plan source state")
    validate_staged_source_state(source_state)
    validate_ready_plan_contract(root, source_value)
    validate_ready_plan_contract(root, target_value)
    source_state_sha256 = hashlib.sha256(source_state_path.read_bytes()).hexdigest()
    if recorded_state_sha256 is not None \
            and recorded_state_sha256 != source_state_sha256:
        raise FlowError("Reused plan source state bytes drifted")
    lineage = [] if _lineage is None else _lineage
    lineage.append({
        "target_run_id": current_run_path.name,
        "source_run_id": source_run_id,
        "source_sha256": source_sha256,
        "source_state_sha256": source_state_sha256,
        "target_sha256": target_sha256,
    })
    if source_state.get("reused_plan") is not None:
        verify_reused_plan_binding(
            root, source_path.parent, source_state["reused_plan"], _seen=seen,
            _lineage=lineage,
        )
    return lineage


def assert_reused_plan_provenance(
    root: Path, run_dir: Path, state: MutableMapping[str, Any]
) -> bool:
    """Continuously guard the complete reused-plan lineage of a live run.

    Verifying the binding once, when the controller is entered, is not enough.
    The source run directory, its terminal state file and this run's own
    plan.json all stay writable while mutable actors work, so a plan changed
    after adoption could otherwise survive every checkpoint check, the
    CODE_APPROVED freeze, the final checks and the final review.

    The lineage recomputed from repository bytes is persisted the first time
    it is proven and compared on every later call.  A recomputation that no
    longer matches, at any depth of the ancestry, is a hard protocol error:
    no stage may run, no freeze may be taken and no terminal may be returned
    on drifted plan provenance.

    Returns True when the persisted lineage was created or updated, so the
    caller can save the run state exactly once.
    """
    binding = state.get("reused_plan")
    recorded = state.get("reused_plan_lineage")
    if binding is None:
        if recorded is not None:
            raise FlowError(
                "Reused plan lineage is recorded without its plan binding")
        return False
    lineage = verify_reused_plan_binding(root, run_dir, binding)
    if recorded is None:
        state["reused_plan_lineage"] = lineage
        return True
    if recorded != lineage:
        raise FlowError(
            "Reused plan lineage drifted after adoption; refusing to run any "
            f"further stage. recorded={recorded}; current={lineage}"
        )
    return False


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
    reuse_plan_run_id: str | None = None,
) -> int:
    if not (root / "AGENTS.md").is_file():
        raise FlowError(f"AGENTS.md is required at repository root: {root}")
    initial_status = git_status(root)
    if initial_status and not allow_dirty and not dry_run and not resume_run_id:
        raise FlowError(
            "Worktree is not clean. Commit/stash your work or rerun with --allow-dirty "
            "after confirming the reviewer may include those changes."
        )

    if resume_run_id and reuse_plan_run_id:
        raise FlowError("--reuse-plan-from cannot be combined with --resume-run")
    if dry_run and reuse_plan_run_id:
        raise FlowError("--reuse-plan-from cannot be combined with --dry-run")

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
    reused_plan = (
        reuse_ready_plan(root, run_dir, reuse_plan_run_id)
        if reuse_plan_run_id
        else None
    )
    checks = () if no_checks else (*config.checks, *extra_checks)

    if dry_run:
        return _dry_run(config, root, task, run_dir, checks)

    required_commands = {
        *(role.runner for role in config.roles.values()),
        *(command[0] for command in checks),
    }
    missing_commands = sorted(name for name in required_commands if not shutil.which(name))
    if missing_commands:
        raise FlowError(f"Required commands are not on PATH: {', '.join(missing_commands)}")

    lock_path = root / ".ai-flow" / "active.lock"
    acquire_lock(lock_path, run_id)
    state = _load_or_create_state(run_dir, task, bool(resume_run_id))
    if reused_plan is not None:
        if state.get("next_stage") != "planner":
            raise FlowError("A reused plan can only initialize a fresh planner stage")
        state["reused_plan"] = reused_plan
        state["next_stage"] = "worker"
    _recover_claude_usage_from_logs(run_dir, state)
    evidence_policy = config.evidence_policy
    if evidence_policy is not None:
        protocol = state.get("workflow_protocol")
        if resume_run_id and protocol not in {None, "staged_evidence_v3"}:
            lock_path.unlink(missing_ok=True)
            raise FlowError(f"Unsupported persisted workflow protocol: {protocol}")
        if (
            resume_run_id
            and protocol is None
            and state.get("next_stage") != "planner"
        ):
            lock_path.unlink(missing_ok=True)
            raise FlowError(
                "A legacy in-flight run cannot be upgraded to staged evidence; "
                "preserve it and start a new run so the registration baseline is exact"
            )
        state["workflow_protocol"] = "staged_evidence_v3"
        state.setdefault(
            "evidence_baseline",
            evidence_inventory(root, evidence_policy.registration_globs),
        )
        state.setdefault("evidence_preserved", state["evidence_baseline"])
        state.setdefault("evidence_generations", 0)
        state.setdefault("evidence_generation_baselines", {})
        state.setdefault("code_review_cycles", 0)
        state.setdefault("code_repair_cycles", 0)
        state.setdefault("final_review_cycles", 0)
    if retry_checks:
        retry_stage = state.get("next_stage")
        retry_targets = {
            "test_fix": "checks",
            "code_test_fix": "code_checks",
            "phase_checkpoint_test_fix": "phase_checkpoint_checks",
        }
        if not resume_run_id or retry_stage not in retry_targets:
            raise FlowError(
                "--retry-checks requires a resumed run currently stopped at "
                "test_fix or code_test_fix"
            )
        state.setdefault("check_retry_events", []).append(
            {
                "failed_checks": state.get("last_failed_checks"),
                "reason": "explicit operator correction to deterministic check definition",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["next_stage"] = retry_targets[str(retry_stage)]
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

    def assert_plan_provenance() -> None:
        """Re-prove the reused-plan lineage, persisting it the first time."""
        if assert_reused_plan_provenance(root, run_dir, state):
            _save_run_state(run_dir, state)

    def finish(status: str, summary: str, code: int) -> int:
        # A terminal is a claim about work done on a specific reviewed plan.
        # Re-prove the provenance before recording it, so a lineage mutated
        # during the run can never be returned as APPROVED.
        assert_plan_provenance()
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

    def run_fixer(
        prompt: str,
        invocation: str,
        resume_session: bool,
        progress_guard: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        # Every fixer is a mutable actor.  Prove the plan lineage before it
        # starts; its progress guard and the caller re-prove it afterwards.
        assert_plan_provenance()
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
                progress_guard=progress_guard,
            )
        return run_work_role(
            config,
            "fixer",
            root,
            run_dir,
            prompt,
            invocation,
            False,
            state,
            resume_session=resume_session,
            progress_guard=progress_guard,
        )

    def repair_premature_block(
        blocked_work: dict[str, Any],
        plan_text: str,
        *,
        counter_key: str = "blocker_repair_cycles",
        invocation_prefix: str = "worker-unblock",
        stage_label: str = "WORKER UNBLOCK",
        blocked_attempt_label: str = "Blocked worker attempt",
    ) -> dict[str, Any]:
        """Give a mutable actor a bounded chance to re-check premature BLOCKED.

        A worker or fixer can mistake missing in-scope work, a stale lock-file
        payload, or another owned evidence process for an operator blocker.
        Those claims must be re-checked against live state.  The loop remains
        bounded; a repeated BLOCKED still terminates exactly as before.
        """
        current = blocked_work
        fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")
        completed = int(state.get(counter_key, 0))
        while current.get("status") == "BLOCKED":
            if completed >= config.max_blocker_repair_cycles:
                return current
            cycle = completed + 1
            invocation = f"{invocation_prefix}-{cycle:02d}"
            resuming = state.get("active_invocation") == invocation
            mark("worker_unblock", invocation)
            _stage(
                f"{role_stage_label(config.roles['fixer'])} {stage_label} "
                f"{cycle}/{config.max_blocker_repair_cycles}"
            )
            current = run_fixer(
                compose_prompt(
                    fixer_base,
                    (
                        ("User task", task),
                        ("Original plan", plan_text),
                        (
                            blocked_attempt_label,
                            json.dumps(current, ensure_ascii=False, indent=2),
                        ),
                        (
                            "Unblock contract",
                            "First classify every claimed blocker. Missing code, tests, "
                            "manifests, registrations, fixtures, fresh output roots, "
                            "resource caps, or inputs that can be derived read-only from "
                            "the repository are in-scope work and must be resolved. A "
                            "valid INCONCLUSIVE scientific outcome is a completed outcome, "
                            "not a workflow blocker. Re-check live external state before "
                            "repeating a blocker: lock-file JSON is metadata, while the "
                            "kernel lock is authoritative; an owned job should be awaited "
                            "or shut down cleanly, not reported as external. Return BLOCKED "
                            "only for a genuine user decision, missing credential, "
                            "destructive action, or unavailable external state, and name "
                            "the exact current evidence.",
                        ),
                    ),
                ),
                invocation,
                resuming,
            )
            completed = cycle
            state[counter_key] = completed
            _save_run_state(run_dir, state)
        return current

    def run_staged_evidence_protocol(plan_text: str) -> int:
        """Converge code first, then run source-frozen evidence at most twice."""
        policy = evidence_policy
        if policy is None:  # pragma: no cover - caller contract
            raise FlowError("staged evidence protocol requires an evidence policy")
        reviewer_base = config.roles["reviewer"].prompt_path.read_text(encoding="utf-8")
        fixer_base = config.roles["fixer"].prompt_path.read_text(encoding="utf-8")

        def registration_inventory() -> dict[str, dict[str, str]]:
            return evidence_inventory(root, policy.registration_globs)

        def assert_inventory_unchanged(before: dict[str, dict[str, str]]) -> None:
            # Every code-stage actor is guarded by this call before, during and
            # after its invocation, so the reused-plan lineage is re-proven on
            # exactly the same boundary as the evidence inventory.
            assert_plan_provenance()
            current = registration_inventory()
            if current != before:
                additions = {
                    pattern: sorted(
                        set(current.get(pattern, [])) - set(before.get(pattern, []))
                    )
                    for pattern in policy.registration_globs
                }
                removals = {
                    pattern: sorted(
                        set(before.get(pattern, {})) - set(current.get(pattern, {}))
                    )
                    for pattern in policy.registration_globs
                }
                modifications = {
                    pattern: sorted(
                        path
                        for path in set(before.get(pattern, {}))
                        & set(current.get(pattern, {}))
                        if before[pattern][path] != current[pattern][path]
                    )
                    for pattern in policy.registration_globs
                }
                raise FlowError(
                    "A code-stage actor changed evidence before CODE_APPROVED; "
                    f"additions={additions}; removals={removals}; "
                    f"modifications={modifications}"
                )

        def assert_frozen_evidence(
            frozen: dict[str, Any], *, checkpoint_new: bool = False
        ) -> None:
            # The frozen source manifest is built from configured globs that
            # deliberately exclude run directories, so it cannot see a plan or
            # ancestry state file.  Guard that lineage here, on the same
            # boundary that guards the protected source.
            assert_plan_provenance()
            assert_source_frozen(root, policy, frozen)
            preserved = state.get(
                "evidence_preserved", state.get("evidence_baseline", {})
            )
            # Registration limits apply to the active append-only generation.
            # ``preserved`` still covers every historical byte, so a repair
            # generation may append its bounded replacement series without
            # making generation one consume generation two's allowance.
            budget_baseline = state.get(
                "evidence_generation_baseline",
                state.get("evidence_baseline", {}),
            )
            assert_evidence_budget(
                root,
                policy,
                budget_baseline,
                preserved,
            )
            current = evidence_inventory(root, policy.registration_globs)
            if current != preserved:
                if not checkpoint_new:
                    raise FlowError(
                        "Evidence artifacts changed outside the source-frozen "
                        "evidence stage"
                    )
                state["evidence_preserved"] = current
                _save_run_state(run_dir, state)

        def run_code_fixer(
            *, invocation: str, reason_title: str, reason: str
        ) -> dict[str, Any]:
            assert_plan_provenance()
            before = registration_inventory()
            work = run_fixer(
                compose_prompt(
                    fixer_base,
                    (
                        ("User task", task),
                        ("Original plan", plan_text),
                        (reason_title, reason),
                        (
                            "CODE STABILIZATION CONTRACT",
                            "Repair the complete supplied defect set and run focused tests, "
                            "but do not freeze, create, execute, or modify any evidence "
                            "registration/outcome. The controller owns evidence only after "
                            "an independent CODE_APPROVED decision. Return IMPLEMENTED only "
                            "when all supplied findings are repaired together. Before returning, "
                            "perform a closure audit of each changed trust boundary: test the "
                            "reported exploit, adjacent self-attestation/freshness/status bypasses, "
                            "and the real multi-step producer path rather than only mocked units. "
                            "Re-read the complete changed files and add adversarial regression "
                            "tests so the verification review sees a stable implementation.",
                        ),
                    ),
                ),
                invocation,
                state.get("active_invocation") == invocation,
                progress_guard=lambda: assert_inventory_unchanged(before),
            )
            assert_inventory_unchanged(before)
            return work

        while True:
            next_stage = str(state.get("next_stage"))

            if next_stage == "code_checks":
                evidence_before_checks = registration_inventory()
                cycle = int(state.get("execution_cycle", 0)) + 1
                state["execution_cycle"] = cycle
                mark("code_checks", f"code-checks-{cycle:02d}")
                _stage("CODE STABILIZATION CHECKS")
                results = run_checks(
                    checks, root, run_dir, config.check_timeout_seconds, cycle, False
                )
                assert_inventory_unchanged(evidence_before_checks)
                _atomic_write_json(run_dir / f"checks-{cycle:02d}.json", results)
                failed = [item for item in results if item["returncode"] != 0]
                if failed:
                    fixes = int(state.get("test_fix_cycles", 0))
                    if fixes >= config.max_test_fix_cycles:
                        return finish(
                            "BLOCKED",
                            f"code checks still fail after {fixes} repair cycles",
                            EXIT_BLOCKED,
                        )
                    state["test_fix_cycles"] = fixes + 1
                    state["last_failed_checks"] = f"checks-{cycle:02d}.json"
                    mark("code_test_fix")
                else:
                    mark("code_review")
                continue

            if next_stage == "code_test_fix":
                path = run_dir / str(state.get("last_failed_checks", ""))
                work = run_code_fixer(
                    invocation=f"code-test-fix-{int(state.get('test_fix_cycles', 0)):02d}",
                    reason_title="Failed code checks",
                    reason=path.read_text(encoding="utf-8"),
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("code_checks")
                continue

            if next_stage == "code_review":
                # At least two review cycles are always required before a
                # freeze: code-review-01 (all-findings) and a reserved
                # verification review, even when code-review-01 approves on
                # the first pass. A repair budget of 0 must not shrink that
                # floor -- the verification pass is not a repair slot.
                #
                # `code_review_cycles` itself is a run-wide monotonic counter
                # (never reset) so every review invocation keeps a unique
                # artifact name across the whole run, including any later
                # freeze episode triggered by a phase-checkpoint or final
                # review repair. `code_review_episode_baseline` marks where
                # the CURRENT freeze episode's attempt budget starts, so
                # each fresh episode gets its own full code-review-01/-02
                # allowance instead of borrowing leftover attempts from an
                # episode that already froze and was approved once.
                max_review_attempts = max(2, policy.max_code_repair_cycles + 1)
                episode_baseline = int(state.get("code_review_episode_baseline", 0))
                pending_cycle = state.get("code_review_in_progress")
                if isinstance(pending_cycle, int) and pending_cycle > 0:
                    raw_cycle = pending_cycle
                else:
                    raw_cycle = int(state.get("code_review_cycles", 0)) + 1
                    episode_cycle = raw_cycle - episode_baseline
                    if episode_cycle > max_review_attempts:
                        return finish(
                            "BLOCKED",
                            "code did not converge after the reserved verification review; "
                            "no evidence started",
                            EXIT_BLOCKED,
                        )
                    state["code_review_cycles"] = raw_cycle
                    state["code_review_in_progress"] = raw_cycle
                episode_cycle = raw_cycle - episode_baseline
                invocation = f"code-review-{raw_cycle:02d}"
                mark("code_review", invocation)
                _stage(
                    f"CODE STABILITY REVIEW {episode_cycle}/{max_review_attempts}"
                )
                latest_checks = run_dir / f"checks-{int(state.get('execution_cycle', 0)):02d}.json"
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
                                latest_checks.read_text(encoding="utf-8"),
                            ),
                            (
                                "CODE STABILITY REVIEW CONTRACT",
                                "Review the complete implementation, executable evidence "
                                "tooling, tests, manifests, and acceptance contracts now. "
                                "Do not require the expensive evidence outcomes in this "
                                "stage. Return APPROVED only when no further protected-source "
                                "change is foreseeable from the repository evidence. Return "
                                "every material code/design finding in one response; do not "
                                "defer known findings to a later review.",
                            ),
                        ),
                    ),
                    invocation,
                    False,
                    state,
                )
                status = review.get("status")
                state.pop("code_review_in_progress", None)
                if status == "APPROVED":
                    if episode_cycle < 2:
                        # code-review-01 approved on the first pass, but the
                        # reserved verification review (code-review-02) has
                        # not run yet -- an approval is never sufficient on
                        # its own. Re-run checks and get a second, independent
                        # review of the same code before any freeze. That
                        # second review is confirmation-only: it does not
                        # draw on the repair budget, and reversing this
                        # approval must never launch a fixer (see the
                        # CHANGES_REQUIRED branch below).
                        state["code_review_awaiting_confirmation"] = True
                        mark("code_checks")
                        continue
                    state.pop("code_review_awaiting_confirmation", None)
                    # A freeze is only meaningful for the exact reviewed plan.
                    assert_plan_provenance()
                    epoch = int(state.get("code_freeze_epoch", 0)) + 1
                    frozen = source_manifest(root, policy.source_globs)
                    freeze = {
                        **frozen,
                        "kind": "ai_flow_code_freeze",
                        "epoch": epoch,
                        "approved_by": invocation,
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _atomic_write_json(run_dir / f"code-freeze-{epoch:02d}.json", freeze)
                    state["code_freeze_epoch"] = epoch
                    state["code_freeze"] = freeze
                    for key in (
                        "phase_3_5_checkpoint",
                        "phase_3_5_checkpoint_path",
                        "phase_3_5_review",
                        "phase_3_5_review_path",
                        "phase_3_5_review_response_path",
                        "last_phase_review",
                    ):
                        state.pop(key, None)
                    mark("evidence")
                    continue
                if status == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        str(review.get("blocked_reason") or review.get("summary", "")),
                        EXIT_BLOCKED,
                    )
                if status != "CHANGES_REQUIRED":
                    raise FlowError(f"Code reviewer returned unknown status: {status!r}")
                findings = review.get("findings", [])
                if not findings:
                    raise FlowError("CHANGES_REQUIRED code review has no findings")
                if state.pop("code_review_awaiting_confirmation", False):
                    # This review's only job was to confirm a prior
                    # approval; it reversed that approval instead. It is the
                    # reserved verification review by construction, so it
                    # never spends a repair -- launching a fixer here would
                    # let unreviewed code slip toward evidence.
                    return finish(
                        "BLOCKED",
                        "the reserved verification review found material defects "
                        "after an earlier approval; refusing an unreviewable "
                        "repair and starting no evidence",
                        EXIT_BLOCKED,
                    )
                repairs = int(state.get("code_repair_cycles", 0))
                if repairs >= policy.max_code_repair_cycles:
                    return finish(
                        "BLOCKED",
                        "the reserved verification review still found material defects; "
                        "refusing an unreviewable repair and starting no evidence",
                        EXIT_BLOCKED,
                    )
                state["code_repair_cycles"] = repairs + 1
                state["last_code_review"] = invocation + ".json"
                mark("code_review_fix")
                continue

            if next_stage == "code_review_fix":
                path = run_dir / str(state.get("last_code_review", ""))
                work = run_code_fixer(
                    invocation=f"code-review-fix-{int(state.get('code_review_cycles', 0)):02d}",
                    reason_title="Complete code review findings",
                    reason=path.read_text(encoding="utf-8"),
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("code_checks")
                continue

            if next_stage == "evidence":
                pending_generation = state.get("evidence_generation_in_progress")
                if isinstance(pending_generation, int) and pending_generation > 0:
                    generation = pending_generation
                    starting_generation = False
                else:
                    generation = int(state.get("evidence_generations", 0)) + 1
                    if generation > policy.max_generations:
                        return finish(
                            "BLOCKED",
                            "evidence generation budget exhausted; refusing another registration",
                            EXIT_BLOCKED,
                        )
                    state["evidence_generations"] = generation
                    state["evidence_generation_in_progress"] = generation
                    starting_generation = True
                frozen = state.get("code_freeze")
                if not isinstance(frozen, dict):
                    raise FlowError("Evidence stage has no persisted CODE_APPROVED freeze")
                generation_key = str(generation)
                generation_baselines = state.setdefault(
                    "evidence_generation_baselines", {}
                )
                if starting_generation:
                    generation_baseline = registration_inventory()
                    generation_baselines[generation_key] = generation_baseline
                    state["evidence_generation_baseline"] = generation_baseline
                    state["evidence_generation_freeze"] = frozen
                    _save_run_state(run_dir, state)
                else:
                    generation_baseline = state.get("evidence_generation_baseline")
                    if not isinstance(generation_baseline, dict):
                        generation_baseline = generation_baselines.get(generation_key)
                    if not isinstance(generation_baseline, dict):
                        raise FlowError(
                            "resumed evidence generation has no persisted baseline"
                        )
                    if state.get("evidence_generation_freeze") != frozen:
                        raise FlowError(
                            "resumed evidence generation has a different source freeze"
                        )

                evidence_guard = lambda: assert_frozen_evidence(
                    frozen, checkpoint_new=True
                )
                evidence_guard()
                invocation = f"evidence-{generation:02d}"
                mark("evidence", invocation)
                _stage(f"SOURCE-FROZEN EVIDENCE {generation}/{policy.max_generations}")

                work = run_fixer(
                    compose_prompt(
                        fixer_base,
                        (
                            ("User task", task),
                            ("Original plan", plan_text),
                            (
                                "CODE_APPROVED freeze",
                                json.dumps(frozen, ensure_ascii=False, indent=2),
                            ),
                            (
                                "EVIDENCE EXECUTION CONTRACT",
                                "The protected source is immutable. Execute the already "
                                "reviewed evidence tools, publish only append-only terminal "
                                "artifacts, and apply the preregistered gates mechanically. "
                                "Do not edit source, prompts, tests, policies, or thresholds. "
                                "A negative or INCONCLUSIVE result is complete. Create at most "
                                "one new registration per configured evidence series in this "
                                "generation, and return only after every owned producer exits.",
                            ),
                        ),
                    ),
                    invocation,
                    state.get("active_invocation") == invocation,
                    progress_guard=evidence_guard,
                )
                evidence_guard()
                state.pop("evidence_generation_in_progress", None)
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("phase_checkpoint_checks")
                continue

            if next_stage == "phase_checkpoint_checks":
                frozen = state.get("code_freeze")
                if not isinstance(frozen, dict):
                    raise FlowError(
                        "Phase 3-5 checkpoint has no persisted CODE_APPROVED freeze"
                    )
                assert_frozen_evidence(frozen)
                checkpoint = state.get("phase_3_5_checkpoint")
                if not isinstance(checkpoint, dict):
                    generation = int(state.get("evidence_generations", 0))
                    checkpoint = build_phase_3_5_checkpoint(
                        root,
                        policy,
                        state.get("evidence_generation_baseline", {}),
                        frozen,
                    )
                    checkpoint_path = (
                        run_dir / f"phase-3-5-checkpoint-{generation:02d}.json"
                    )
                    _atomic_write_json(checkpoint_path, checkpoint)
                    state["phase_3_5_checkpoint"] = checkpoint
                    state["phase_3_5_checkpoint_path"] = checkpoint_path.name
                    _save_run_state(run_dir, state)
                validate_phase_3_5_checkpoint(
                    root,
                    policy,
                    state.get("evidence_generation_baseline", {}),
                    frozen,
                    checkpoint,
                )
                cycle = int(state.get("execution_cycle", 0)) + 1
                state["execution_cycle"] = cycle
                mark("phase_checkpoint_checks", f"phase-checks-{cycle:02d}")
                _stage("SOURCE-FROZEN PHASE 3-5 CHECKPOINT CHECKS")
                results = run_checks(
                    checks, root, run_dir, config.check_timeout_seconds, cycle, False
                )
                assert_frozen_evidence(frozen)
                validate_phase_3_5_checkpoint(
                    root,
                    policy,
                    state.get("evidence_generation_baseline", {}),
                    frozen,
                    checkpoint,
                )
                _atomic_write_json(run_dir / f"checks-{cycle:02d}.json", results)
                failed = [item for item in results if item["returncode"] != 0]
                if failed:
                    fixes = int(state.get("test_fix_cycles", 0))
                    if fixes >= config.max_test_fix_cycles:
                        return finish(
                            "BLOCKED",
                            f"Phase 3-5 checkpoint checks still fail after {fixes} repair cycles",
                            EXIT_BLOCKED,
                        )
                    state["test_fix_cycles"] = fixes + 1
                    state["last_failed_checks"] = f"checks-{cycle:02d}.json"
                    mark("phase_checkpoint_test_fix")
                else:
                    mark("phase_3_5_review")
                continue

            if next_stage == "phase_checkpoint_test_fix":
                path = run_dir / str(state.get("last_failed_checks", ""))
                work = run_code_fixer(
                    invocation=f"phase-checkpoint-test-fix-{int(state.get('test_fix_cycles', 0)):02d}",
                    reason_title="Failed Phase 3-5 checkpoint checks",
                    reason=path.read_text(encoding="utf-8"),
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                # A fixer just ran against previously frozen source, so this
                # is a fresh freeze episode: it earns its own full
                # code-review-01/-02 budget rather than borrowing leftover
                # attempts from the episode that already froze and approved
                # the code once. `code_review_cycles` itself stays monotonic
                # (never reset) so every review artifact this run ever
                # produces keeps a unique name; only the episode baseline
                # moves, and the repair budget resets outright since it is
                # not used for naming.
                state["code_review_episode_baseline"] = int(
                    state.get("code_review_cycles", 0)
                )
                state["code_repair_cycles"] = 0
                mark("code_checks")
                continue

            if next_stage == "phase_3_5_review":
                frozen = state.get("code_freeze")
                checkpoint = state.get("phase_3_5_checkpoint")
                if not isinstance(frozen, dict) or not isinstance(checkpoint, dict):
                    raise FlowError("Phase 3-5 review has no complete checkpoint")
                assert_frozen_evidence(frozen)
                pending_cycle = state.get("phase_review_in_progress")
                if isinstance(pending_cycle, int) and pending_cycle > 0:
                    cycle = pending_cycle
                else:
                    cycle = int(state.get("phase_review_cycles", 0)) + 1
                    if cycle > policy.max_generations:
                        return finish(
                            "BLOCKED",
                            "Phase 3-5 evidence review did not converge before its limit",
                            EXIT_BLOCKED,
                        )
                    state["phase_review_cycles"] = cycle
                    state["phase_review_in_progress"] = cycle
                invocation = f"phase-review-{cycle:02d}"
                mark("phase_3_5_review", invocation)
                _stage(
                    f"INDEPENDENT PHASE 3-5 EVIDENCE REVIEW "
                    f"{cycle}/{policy.max_generations}"
                )
                latest_checks = run_dir / f"checks-{int(state.get('execution_cycle', 0)):02d}.json"
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
                                "Phase 3-5 checkpoint",
                                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                            ),
                            (
                                "Deterministic checkpoint checks",
                                latest_checks.read_text(encoding="utf-8"),
                            ),
                            (
                                "PHASE 3-5 INDEPENDENT REVIEW CONTRACT",
                                "Independently inspect every fresh artifact named by the "
                                "checkpoint, including its source, input, runtime, policy, "
                                "content, path and byte digests and its terminal scientific "
                                "status. Recompute the checkpoint lineage from repository "
                                "bytes. Phase 5 PASS or NOT_TRIGGERED is acceptable; a valid "
                                "INCONCLUSIVE result must remain truthful. Do not inspect or "
                                "approve any Phase 6 or Phase 7 registration. Return APPROVED "
                                "only when this bounded checkpoint is independently sound and "
                                "no material finding remains.",
                            ),
                        ),
                    ),
                    invocation,
                    False,
                    state,
                )
                state.pop("phase_review_in_progress", None)
                response_path = run_dir / f"phase-review-response-{cycle:02d}.json"
                _atomic_write_json(response_path, review)
                status = review.get("status")
                if status == "APPROVED":
                    review_artifact = build_phase_3_5_review_artifact(
                        checkpoint, review, invocation
                    )
                    review_path = run_dir / f"phase-3-5-review-{cycle:02d}.json"
                    _atomic_write_json(review_path, review_artifact)
                    state["phase_3_5_review"] = review_artifact
                    state["phase_3_5_review_path"] = review_path.name
                    state["phase_3_5_review_response_path"] = response_path.name
                    _save_run_state(run_dir, state)
                    mark("post_review_evidence")
                    continue
                if status == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        str(review.get("blocked_reason") or review.get("summary", "")),
                        EXIT_BLOCKED,
                    )
                if status != "CHANGES_REQUIRED":
                    raise FlowError(f"Phase 3-5 reviewer returned unknown status: {status!r}")
                if int(state.get("evidence_generations", 0)) >= policy.max_generations:
                    return finish(
                        "BLOCKED",
                        "Phase 3-5 evidence review still requires changes after the bounded rerun",
                        EXIT_BLOCKED,
                    )
                state["last_phase_review"] = response_path.name
                mark("phase_3_5_review_fix")
                continue

            if next_stage == "phase_3_5_review_fix":
                path = run_dir / str(state.get("last_phase_review", ""))
                work = run_code_fixer(
                    invocation=f"phase-review-fix-{int(state.get('phase_review_cycles', 0)):02d}",
                    reason_title="Complete Phase 3-5 evidence review findings",
                    reason=path.read_text(encoding="utf-8"),
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                # See phase_checkpoint_test_fix above: a fresh freeze episode
                # earns its own code-review-01/-02 budget, via a moved
                # baseline rather than resetting the monotonic naming
                # counter.
                state["code_review_episode_baseline"] = int(
                    state.get("code_review_cycles", 0)
                )
                state["code_repair_cycles"] = 0
                mark("code_checks")
                continue

            if next_stage == "post_review_evidence":
                frozen = state.get("code_freeze")
                checkpoint = state.get("phase_3_5_checkpoint")
                review_artifact = state.get("phase_3_5_review")
                response_path = run_dir / str(
                    state.get("phase_3_5_review_response_path", "")
                )
                if not all(
                    isinstance(value, dict)
                    for value in (frozen, checkpoint, review_artifact)
                ) or not response_path.is_file():
                    raise FlowError("Phase 6 has no persisted Phase 3-5 review lineage")
                review = read_json(response_path, "Phase 3-5 review response")
                validate_phase_3_5_review_artifact(
                    review_artifact, checkpoint, review, frozen
                )
                validate_phase_3_5_checkpoint(
                    root,
                    policy,
                    state.get("evidence_generation_baseline", {}),
                    frozen,
                    checkpoint,
                )
                # The post-review writer is still an append-only evidence
                # stage: it may add the now-permitted Phase 6/7 series, while
                # the source manifest and all earlier bytes remain immutable.
                evidence_guard = lambda: assert_frozen_evidence(
                    frozen, checkpoint_new=True
                )
                evidence_guard()
                invocation = f"evidence-post-review-{int(state.get('evidence_generations', 0)):02d}"
                resuming = state.get("active_invocation") == invocation
                mark("post_review_evidence", invocation)
                _stage("SOURCE-FROZEN POST-REVIEW EVIDENCE")
                work = run_fixer(
                    compose_prompt(
                        fixer_base,
                        (
                            ("User task", task),
                            ("Original plan", plan_text),
                            (
                                "PASS Phase 3-5 independent review",
                                json.dumps(review_artifact, ensure_ascii=False, indent=2),
                            ),
                            (
                                "POST-REVIEW EVIDENCE CONTRACT",
                                "The independent Phase 3-5 review is the sole prerequisite "
                                "for later evidence. Revalidate its digest-bound lineage "
                                "before doing anything. Only now may the already registered "
                                "plan continue to Phase 6 and, if scientifically allowed, "
                                "Phase 7. Never register Phase 6 or Phase 7 before this "
                                "stage, and never start Phase 6 when the reviewed Phase 0-4 "
                                "statuses do not permit it. Keep all artifacts append-only, "
                                "source frozen, and publish valid INCONCLUSIVE or "
                                "NOT_ALLOWED terminals instead of inventing PASS.",
                            ),
                        ),
                    ),
                    invocation,
                    resuming,
                    progress_guard=evidence_guard,
                )
                evidence_guard()
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                mark("final_checks")
                continue

            if next_stage == "final_checks":
                frozen = state.get("code_freeze")
                if not isinstance(frozen, dict):
                    raise FlowError("Final checks have no persisted CODE_APPROVED freeze")
                assert_frozen_evidence(frozen)
                cycle = int(state.get("execution_cycle", 0)) + 1
                state["execution_cycle"] = cycle
                mark("final_checks", f"final-checks-{cycle:02d}")
                _stage("FINAL DETERMINISTIC CHECKS")
                results = run_checks(
                    checks, root, run_dir, config.check_timeout_seconds, cycle, False
                )
                assert_frozen_evidence(frozen)
                _atomic_write_json(run_dir / f"checks-{cycle:02d}.json", results)
                failed = [item for item in results if item["returncode"] != 0]
                if failed:
                    if int(state.get("evidence_generations", 0)) >= policy.max_generations:
                        return finish(
                            "BLOCKED",
                            "final checks failed and the evidence rerun budget is exhausted",
                            EXIT_BLOCKED,
                        )
                    state["last_failed_checks"] = f"checks-{cycle:02d}.json"
                    mark("code_test_fix")
                else:
                    mark("final_review")
                continue

            if next_stage == "final_review":
                frozen = state.get("code_freeze")
                if not isinstance(frozen, dict):
                    raise FlowError("Final review has no persisted CODE_APPROVED freeze")
                checkpoint = state.get("phase_3_5_checkpoint")
                phase_review = state.get("phase_3_5_review")
                phase_review_response_path = run_dir / str(
                    state.get("phase_3_5_review_response_path", "")
                )
                if not all(
                    isinstance(value, dict) for value in (checkpoint, phase_review)
                ) or not phase_review_response_path.is_file():
                    raise FlowError(
                        "Final review has no persisted Phase 3-5 review lineage"
                    )
                phase_review_response = read_json(
                    phase_review_response_path, "Phase 3-5 review response"
                )
                validate_phase_3_5_review_artifact(
                    phase_review,
                    checkpoint,
                    phase_review_response,
                    frozen,
                )
                validate_post_review_terminal_artifacts(
                    root,
                    policy,
                    frozen,
                    checkpoint,
                    phase_review,
                    state.get("evidence_generation_baseline", {}),
                )

                def final_review_guard() -> None:
                    assert_frozen_evidence(frozen)

                final_review_guard()
                pending_cycle = state.get("final_review_in_progress")
                if isinstance(pending_cycle, int) and pending_cycle > 0:
                    cycle = pending_cycle
                else:
                    cycle = int(state.get("final_review_cycles", 0)) + 1
                    state["final_review_cycles"] = cycle
                    state["final_review_in_progress"] = cycle
                invocation = f"final-review-{cycle:02d}"
                mark("final_review", invocation)
                _stage(f"FINAL EVIDENCE REVIEW {cycle}/{policy.max_generations}")
                latest_checks = run_dir / f"checks-{int(state.get('execution_cycle', 0)):02d}.json"
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
                                latest_checks.read_text(encoding="utf-8"),
                            ),
                            (
                                "PASS Phase 3-5 independent review",
                                json.dumps(phase_review, ensure_ascii=False, indent=2),
                            ),
                            (
                                "FINAL REVIEW CONTRACT",
                                "Review the frozen-source evidence and complete user outcome, "
                                "including that the Phase 3-5 checkpoint and its independent "
                                "PASS artifact remain bound to the same lineage. Do not invent "
                                "improvements outside the accepted criteria. Return every "
                                "material remaining defect in one response.",
                            ),
                        ),
                    ),
                    invocation,
                    False,
                    state,
                    progress_guard=final_review_guard,
                )
                final_review_guard()
                status = review.get("status")
                state.pop("final_review_in_progress", None)
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
                    raise FlowError(f"Final reviewer returned unknown status: {status!r}")
                findings = review.get("findings", [])
                if not findings:
                    raise FlowError("CHANGES_REQUIRED final review has no findings")
                if int(state.get("evidence_generations", 0)) >= policy.max_generations:
                    state["last_final_review"] = invocation + ".json"
                    return finish(
                        "BLOCKED",
                        "final review still requires changes after the single bounded rerun",
                        EXIT_BLOCKED,
                    )
                state["last_final_review"] = invocation + ".json"
                mark("final_review_fix")
                continue

            if next_stage == "final_review_fix":
                path = run_dir / str(state.get("last_final_review", ""))
                work = run_code_fixer(
                    invocation=f"final-review-fix-{int(state.get('final_review_cycles', 0)):02d}",
                    reason_title="Complete final review findings",
                    reason=path.read_text(encoding="utf-8"),
                )
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", [])) or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
                # See phase_checkpoint_test_fix above: a fresh freeze episode
                # earns its own code-review-01/-02 budget, via a moved
                # baseline rather than resetting the monotonic naming
                # counter.
                state["code_review_episode_baseline"] = int(
                    state.get("code_review_cycles", 0)
                )
                state["code_repair_cycles"] = 0
                mark("code_checks")
                continue

            raise FlowError(f"Unknown staged-evidence state: {next_stage}")
    try:
        # Provenance is proven before anything is read, decided or returned --
        # including the already-complete short circuit below, which used to
        # return a terminal without revalidating the plan it was built on.
        assert_plan_provenance()
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
        assert_plan_provenance()
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
            _stage(f"{role_stage_label(config.roles['worker'])} WORKER")
            worker_base = config.roles["worker"].prompt_path.read_text(encoding="utf-8")
            worker_sections: list[tuple[str, str]] = [
                ("User task", task),
                ("Planner output", plan_text),
            ]
            registration_before = None
            progress_guard = None
            if evidence_policy is not None:
                registration_before = evidence_inventory(
                    root, evidence_policy.registration_globs
                )
                worker_sections.append(
                    (
                        "CODE STABILIZATION CONTRACT",
                        "Implement code, tests, manifests, and deterministic evidence "
                        "tooling only. Do not create, freeze, execute, or modify evidence "
                        "registrations/outcomes. An independent code review and controller "
                        "freeze must occur before any expensive evidence side effect. Treat "
                        "prior review artifacts named by the task as required input. Before "
                        "returning, perform one closure audit across each affected trust "
                        "boundary and its real producer/consumer path, including adversarial "
                        "self-attestation, stale-artifact, cross-generation, status-derivation, "
                        "budget and interrupted-resume cases. This run permits only one "
                        "all-findings repair followed by one reserved verification review, so "
                        "do not defer known defects to the reviewer.",
                    )
                )

                def progress_guard() -> None:
                    assert_plan_provenance()
                    current = evidence_inventory(
                        root, evidence_policy.registration_globs
                    )
                    if current != registration_before:
                        raise FlowError(
                            "Worker changed evidence before CODE_APPROVED; stopped"
                        )

            assert_plan_provenance()
            work = run_work_role(
                config,
                "worker",
                root,
                run_dir,
                compose_prompt(worker_base, worker_sections),
                invocation,
                False,
                state,
                resume_session=resuming,
                progress_guard=progress_guard,
            )
            assert_plan_provenance()
            if progress_guard is not None:
                progress_guard()
            if work.get("status") == "BLOCKED":
                work = repair_premature_block(work, plan_text)
                if work.get("status") == "BLOCKED":
                    return finish(
                        "BLOCKED",
                        "; ".join(work.get("blockers", []))
                        or work.get("summary", ""),
                        EXIT_BLOCKED,
                    )
            mark("code_checks" if evidence_policy is not None else "checks")

        if evidence_policy is not None:
            return run_staged_evidence_protocol(plan_text)

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
                fixer_runner = (
                    role_stage_label(config.roles["fixer_fallback"])
                    if use_fallback_fixer
                    else role_stage_label(config.roles["fixer"])
                )
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
                    work = repair_premature_block(
                        work,
                        plan_text,
                        counter_key=(
                            f"test_fix_blocker_repair_cycles_{test_fix_cycles:02d}"
                        ),
                        invocation_prefix=f"test-unblock-{test_fix_cycles:02d}",
                        stage_label="TEST-FIX UNBLOCK",
                        blocked_attempt_label="Blocked test-repair attempt",
                    )
                    if work.get("status") == "BLOCKED":
                        return finish(
                            "BLOCKED",
                            "; ".join(work.get("blockers", []))
                            or work.get("summary", ""),
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
                fixer_runner = (
                    role_stage_label(config.roles["fixer_fallback"])
                    if use_fallback_fixer
                    else role_stage_label(config.roles["fixer"])
                )
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
                    work = repair_premature_block(
                        work,
                        plan_text,
                        counter_key=(
                            f"review_fix_blocker_repair_cycles_{review_cycles:02d}"
                        ),
                        invocation_prefix=f"review-unblock-{review_cycles:02d}",
                        stage_label="REVIEW-FIX UNBLOCK",
                        blocked_attempt_label="Blocked review-repair attempt",
                    )
                    if work.get("status") == "BLOCKED":
                        return finish(
                            "BLOCKED",
                            "; ".join(work.get("blockers", []))
                            or work.get("summary", ""),
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
    profiled_roles = [
        role for role in config.roles.values()
        if role.runner == "codex" and role.profile
    ]
    for role in profiled_roles:
        role_name = role.name
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
    for executable in sorted({role.runner for role in config.roles.values()}):
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
    for role in config.roles.values():
        if role.runner != "codex" or not role.profile:
            continue
        role_name = role.name
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
        description="Run a bounded planner -> worker -> reviewer implementation loop.",
    )
    parser.add_argument("--config", default=".ai-flow/config.toml")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-checks", action="store_true")
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument(
        "--task-file",
        metavar="PATH",
        help="read the implementation assignment from a UTF-8 file inside the repository",
    )
    parser.add_argument(
        "--resume-run",
        metavar="RUN_ID",
        help="continue an existing run without repeating completed stages",
    )
    parser.add_argument(
        "--reuse-plan-from",
        metavar="RUN_ID",
        help=(
            "start a fresh run from a prior READY plan while keeping the new task "
            "authoritative for worker and review stages"
        ),
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
    if args.reuse_plan_from and args.resume_run:
        parser.error("--reuse-plan-from cannot be combined with --resume-run")
    if args.reuse_plan_from and args.dry_run:
        parser.error("--reuse-plan-from cannot be combined with --dry-run")
    if args.additional_review_cycles < 0:
        parser.error("--additional-review-cycles must be zero or positive")
    if args.use_fallback_fixer and not args.resume_run:
        parser.error("--use-fallback-fixer requires --resume-run")
    if args.retry_checks and not args.resume_run:
        parser.error("--retry-checks requires --resume-run")
    config, root = _config_and_root(args.config)
    positional_task = " ".join(args.task).strip()
    if args.task_file and positional_task:
        parser.error("--task-file cannot be combined with a positional task")
    if args.task_file and args.resume_run:
        parser.error("--task-file cannot replace a resumed run's recorded task")
    task = positional_task
    if args.task_file:
        task_path = Path(args.task_file)
        if not task_path.is_absolute():
            task_path = root / task_path
        task_path = task_path.resolve()
        try:
            task_path.relative_to(root)
        except ValueError:
            parser.error("--task-file must resolve inside the repository")
        try:
            task = task_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            parser.error(f"--task-file does not exist: {task_path}")
        if not task:
            parser.error("--task-file must not be empty")
    return run_flow(
        config,
        root,
        task,
        args.allow_dirty,
        args.dry_run,
        parse_extra_checks(args.check),
        args.no_checks,
        args.resume_run,
        args.fresh_stage,
        args.additional_review_cycles,
        args.use_fallback_fixer,
        args.retry_checks,
        args.reuse_plan_from,
    )


if __name__ == "__main__":
    started = time.monotonic()
    try:
        raise SystemExit(main())
    except FlowError as exc:
        elapsed = time.monotonic() - started
        print(f"ERROR after {elapsed:.1f}s: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
