# AI flow

`ai-flow` runs a complete bounded handoff loop:

1. Codex planner (`gpt-5.6-sol`, `high`, read-only)
2. Codex worker (`gpt-5.6-luna`, `high`, workspace-write)
3. configured deterministic checks
4. Codex reviewer (`gpt-5.6-sol`, `high`, read-only)
5. Codex repair (`gpt-5.6-luna`, `high`, workspace-write) and re-review when required

The loop stops on approval, a repeated real blocker, a failed runner, three review cycles, or
two test-fix cycles. If the initial worker returns `BLOCKED`, an independent fixer gets at most
two bounded attempts to distinguish a real authority/external blocker from missing in-scope
code, manifests, fixtures, or derived inputs. A repair receives at most five
severity-ordered findings at a time. It never commits, pushes, publishes, deploys, or creates
a branch.

Expensive source-bound evidence uses an opt-in staged protocol configured by `[evidence]`.
That protocol does **not** use the ordinary free-running repair loop:

1. worker and repair actors may change code but cannot create registrations;
2. deterministic checks and a complete independent code-stability review must return
   `APPROVED`; at most one all-findings repair is followed by one reserved
   verification review, and a failed verification stops without another fixer;
3. the controller persists a content-hashed `code-freeze-*.json` manifest;
4. one evidence actor writes only the Phase 3–5 append-only artifacts and cannot change protected
   source;
5. source-frozen checkpoint checks and an independent Sol review persist a digest-bound
   `PASS` artifact;
6. only a later evidence stage may register the conditionally allowed Phase 6/7 artifacts;
7. final checks and a separate full-outcome review decide completion;
8. one source-repair/refreeze/rerun is allowed, then the run stops fail-closed.

The controller checks the source manifest and SHA-256-bound per-artifact-series budget at launch,
every progress heartbeat, after final checks, and during final review. Existing evidence cannot
be overwritten or deleted. Interrupted evidence resumes the same persisted generation instead
of consuming a new version. This makes the expensive side effect deterministic and bounded while
leaving ordinary implementation tasks lightweight.

Every stage transition is written atomically to `state.json`; live PID/stage information is
also refreshed in `status.json`. Providers that support resumable sessions keep their session
ID; Codex roles resume from the durable flow cursor and append-only artifacts. A controller
interruption terminates and reaps only its owned child process group, leaves the next stage
recorded, and never deletes or rewrites earlier evidence.
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

For a versioned repository assignment, pass the document directly without shell expansion:

```bash
./ai-flow --task-file .ai-flow/tasks/subhour-closure-search.md
```

The continuation that completes the remaining sub-hour phases, including conditionally gated
full-month and q-policy stages, is:

```bash
./ai-flow --config .ai-flow/config.complete-subhour.toml \
  --task-file .ai-flow/tasks/complete-subhour-plan.md --allow-dirty
```

When a terminal verification review supplies a new bounded repair batch but the existing
Fas 0–7 plan remains valid, start a fresh review budget without paying for another broad
planner pass:

```bash
./ai-flow --config .ai-flow/config.complete-subhour.toml \
  --task-file .ai-flow/tasks/complete-subhour-plan.md --allow-dirty \
  --reuse-plan-from <prior-run-id>
```

The source plan must be `READY`. Its run ID and source/target SHA-256 digests are persisted
in the new `state.json`. The new task remains authoritative and is still sent to the worker
and every reviewer; plan reuse never resumes terminal evidence or skips code review.

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

An interrupted stage resumes from its persisted flow cursor and, when supported, its provider
session. Use `--fresh-stage` to force a new model invocation while retaining prior artifacts,
for example after changing accounts or when a legacy session is unavailable:

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

If the configured fixer cannot continue, keep the checkpoint and explicitly route only the
pending fixer role to the stronger configured Codex workspace-write fallback:

```bash
./ai-flow --resume-run <run-id> --use-fallback-fixer
```

Fallback selection is never automatic: `state.json` records the runner transition, and the
same bounded finding batch, safety prompt, deterministic checks, and independent reviewer are
retained.

If an operator-supplied `--check` command itself was malformed, correct the command and use
`--retry-checks` on the resumed run. This records the retry and bypasses an unnecessary code
repair; it is accepted only while the run is stopped at `test_fix`.
For a staged-evidence run, the equivalent accepted state is `code_test_fix` and the
controller returns to `code_checks`.

Legacy runs are recovered conservatively from their existing `plan.json`, `worker-*.json`,
`checks-*.json`, `review-*.json`, and repair artifacts. Recovery never alters them until the
operator explicitly runs `--resume-run`.

To make the command available without `./`, add the repository to `PATH` or create a symlink:

```bash
ln -s "$(pwd)/ai-flow" "$HOME/.local/bin/ai-flow"
```

## Configuration

Edit `.ai-flow/config.toml` to change runners, models, effort, sandboxes, provider-specific
limits, timeouts, loop bounds, repair-batch size, or deterministic checks. Add task-specific
checks with `--check`; planner-suggested shell text is never executed automatically. Role
prompts and response schemas are separate tracked files under `.ai-flow/`.

For costly scientific workflows, configure `[evidence]` with protected `source_globs`, separate
SHA-256-bound artifact series in `registration_globs`, `max_code_repair_cycles`, `max_generations`, and
`max_new_registrations_per_glob`. Existing files are captured as the run baseline, so historical
append-only artifacts do not consume the new run's budget. A legacy run that already executed
under the old protocol cannot be silently upgraded; preserve it and start a new staged run.
