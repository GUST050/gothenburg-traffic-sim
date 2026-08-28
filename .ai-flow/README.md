# AI flow

`ai-flow` runs a complete bounded handoff loop:

1. Codex planner (`gpt-5.6-sol`, `high`, read-only)
2. Claude worker (`sonnet`, `high`, auto permission mode, bounded turns and budget)
3. configured deterministic checks
4. Codex reviewer (`gpt-5.6-sol`, `high`, read-only)
5. Claude repair (`sonnet`, `medium`, smaller turn/budget cap) and re-review when required

The loop stops on approval, a real blocker, a failed runner, three review cycles, or two
test-fix cycles. A repair receives at most three severity-ordered findings at a time. It never
commits, pushes, publishes, deploys, or creates a branch.

Every stage transition is written atomically to `state.json`; live PID/stage information is
also refreshed in `status.json`. Each Claude invocation has a controller-assigned persisted
session ID. A controller interruption terminates and reaps only its owned child process group,
leaves the next stage recorded, and never deletes or rewrites earlier evidence.
Retries use append-only artifact names such as `review-fix-01-attempt-02.log`.

## First-time setup

Install the two Codex profile files next to your user config:

```bash
./ai-flow setup
./ai-flow doctor
```

The setup command never overwrites an existing profile unless `--force` is supplied. If the
profiles are absent, a normal run still works by applying the same model, effort, and sandbox
settings as per-invocation Codex overrides.

## Run

```bash
./ai-flow "implementera OAuth-login och tester"
```

The worktree must be clean by default, which keeps review provenance unambiguous. Resume work
in an intentionally dirty tree only with `--allow-dirty`. Add deterministic checks without a
shell by repeating `--check`:

```bash
./ai-flow --check "python3 -m pytest -q tests/test_auth.py" "lägg till OAuth"
```

Use `--dry-run` to inspect role routing and commands without calling either model. Run artifacts
are written below `.ai-flow/runs/` and ignored by git.

## Status and continuation

Inspect a run without changing it:

```bash
jq . .ai-flow/runs/<run-id>/status.json
jq . .ai-flow/runs/<run-id>/state.json
```

Continue an interrupted run without repeating a completed planner, worker, check, or review:

```bash
./ai-flow --resume-run <run-id>
```

The interrupted Claude stage resumes its persisted session. Use `--fresh-stage` only when that
session is unavailable, for example after changing Claude accounts or for a legacy run created
before session persistence was enabled:

```bash
./ai-flow --resume-run <run-id> --fresh-stage
```

If a run truthfully stops because its declared review-cycle limit was reached, inspect the
terminal review first. When its remaining findings are still safely repairable, extend the
same run explicitly instead of silently making every run unbounded:

```bash
./ai-flow --resume-run <run-id> --additional-review-cycles 2
```

The extension is persisted in `state.json` and is accepted only for a terminal
`CHANGES_REQUIRED` review; it cannot reopen an approved run or a different blocker.

If Claude reports a provider/session-capacity limit, keep the checkpoint and either wait for
that session to reset or explicitly route only the pending fixer role to the configured Codex
workspace-write fallback:

```bash
./ai-flow --resume-run <run-id> --use-fallback-fixer
```

Fallback selection is never automatic: `state.json` records the runner transition, and the
same bounded finding batch, safety prompt, deterministic checks, and independent reviewer are
retained.

If an operator-supplied `--check` command itself was malformed, correct the command and use
`--retry-checks` on the resumed run. This records the retry and bypasses an unnecessary code
repair; it is accepted only while the run is stopped at `test_fix`.

Legacy runs are recovered conservatively from their existing `plan.json`, `worker-*.json`,
`checks-*.json`, `review-*.json`, and repair artifacts. Recovery never alters them until the
operator explicitly runs `--resume-run`.

To make the command available without `./`, add the repository to `PATH` or create a symlink:

```bash
ln -s "$(pwd)/ai-flow" "$HOME/.local/bin/ai-flow"
```

## Configuration

Edit `.ai-flow/config.toml` to change models, effort, permission mode, per-Claude turn/budget
caps, timeouts, loop bounds, repair-batch size, or deterministic checks. Add task-specific
checks with `--check`; planner-suggested shell text is never executed automatically. Role
prompts and response schemas are separate tracked files under `.ai-flow/`.
