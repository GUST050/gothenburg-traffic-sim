# Canonical package guidance

- Shared implementation belongs under `traffic_sim/`; root scripts remain
  stable command entry points.
- Import through the canonical package. Do not add new root compatibility
  shims or import command modules from package code.
- Keep dependency flow inward: `core` must not depend on demand, simulation,
  confidence or ops; domain packages may depend on `core`.
- Add or update contract tests when moving an implementation boundary.
- Update `ARCHITECTURE.md` when a public contract or dependency direction
  changes.
- Run `make test-fast` plus the nearest domain test target.
