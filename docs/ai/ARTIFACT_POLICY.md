# Artifact policy

The repository keeps source, reviewable manifests, small fixtures and selected
frozen evidence in Git. It does not accept new large generated files by
default.

## Rules

- `make repo-hygiene` rejects any unapproved tracked or unignored file at or
  above 5 MiB.
- An allowlisted large file is pinned by exact byte size and SHA-256. Changing
  it fails until a deliberate replacement or migration is reviewed.
- New model binaries, training tables and raw run output belong in object
  storage, a release asset or Git LFS. Commit a small manifest containing
  origin, version, size, digest, schema and reproduction command.
- Keep only minimal deterministic fixtures needed by tests in normal Git.
- Generated `sumo/`, `runs/` and cache contents remain ignored. Frozen
  validation output must follow `validation/README.md` and be versioned rather
  than overwritten.

## Existing legacy exceptions

Three files currently exceed 5 MiB. They remain temporarily because migrating
them requires choosing and configuring shared external storage; rewriting Git
history is not part of an ordinary refactor. Their exact identities live in
`tools/repo_hygiene_allowlist.json`, which prevents silent growth or drift.

When storage is selected, migrate the two dirsplit artifacts first. Preserve
their hashes in a manifest and add a deterministic fetch/verify command before
removing the Git copies. The annual warm plan is frozen evidence and needs an
evidence-aware migration rather than a generic data move.

## Review checklist for an exception

An exception must state why the file must be clone-time available, why a small
fixture plus external artifact is insufficient, how it is reproduced, and
when the exception expires. Never update only the allowlist to make CI pass.
