# Research basis for coding-agent structure

The repository conventions use primary vendor and tool documentation, then
adapt those recommendations to this project's scientific-evidence contracts.

## Sources and applied decisions

- [OpenAI: Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
  documents repository and nested-directory instruction discovery. Therefore
  the root guide is short and stable, while domain invariants live in the
  nearest scoped `AGENTS.md`.
- [Anthropic: Manage Claude's memory](https://code.claude.com/docs/en/memory)
  documents hierarchical `CLAUDE.md` files and `@path` imports. Therefore each
  Claude file imports the adjacent shared agent guide instead of duplicating
  rules that can drift.
- [pytest: custom markers](https://docs.pytest.org/en/stable/example/markers.html)
  documents registered test categories and selection. Therefore the repository
  names test classes explicitly and exposes stable domain commands through the
  Makefile.
- [GitHub: About Git Large File Storage](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)
  explains pointer-based large-file storage. LFS is not installed or configured
  here, so existing large artifacts are digest-pinned and all new ones fail
  closed until shared storage is deliberately selected.

## Repository-specific finding

Generic advice to split large files is unsafe when validation records bind
source bytes. Trial extractions from `serve.py`, `build_candidates.py` and
`run_scenario.py` preserved Python behavior but triggered frozen provenance
checks. They were reverted byte-for-byte. Future extraction needs a versioned
successor-evidence lifecycle; line count alone does not authorize it.

## How to measure improvement

Use `docs/ai/AI_EVALS.md` before and after major layout changes. Compare task
correctness, irrelevant files read, time to the correct owning code, unrelated
edits and exact checks run. A smaller prompt or source file is not an
improvement when it weakens provenance or adds indirection.
