# Simulation guidance

- Preserve deterministic seeds, exact demand/network identity and immutable
  run workspaces.
- A closure must not leak traffic through closed edges; teleports, stranded
  vehicles and incomplete seed evidence remain fail-closed guards.
- Diagnostic, proxy and replay results are not release evidence unless the
  relevant contract explicitly promotes them.
- Do not rewrite frozen benchmark or gate records to match new code. Produce a
  versioned successor with bound inputs.
- Keep subprocesses bounded, cancellable and isolated from shared artifacts.
- Run `make test-simulation`; run real SUMO only when the requested outcome
  requires it and label the evidence class.
