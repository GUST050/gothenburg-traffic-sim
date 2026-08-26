# AI flow

`ai-flow` runs a complete bounded handoff loop:

1. Codex planner (`gpt-5.6-sol`, `xhigh`, read-only)
2. Claude worker (`opus`, `high`, auto permission mode)
3. configured deterministic checks
4. Codex reviewer (`gpt-5.6-sol`, `xhigh`, read-only)
5. Claude repair and re-review when required

The loop stops on approval, a real blocker, a failed runner, four review cycles, or three
test-fix cycles. It never commits, pushes, publishes, deploys, or creates a branch.

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

To make the command available without `./`, add the repository to `PATH` or create a symlink:

```bash
ln -s "$(pwd)/ai-flow" "$HOME/.local/bin/ai-flow"
```

## Configuration

Edit `.ai-flow/config.toml` to change models, effort, permission mode, timeouts, loop bounds,
or deterministic checks. Role prompts and response schemas are separate tracked files under
`.ai-flow/`.
