# Demand and PFE guidance

- Measured sensor counts are Level-1 hard constraints; missing is not zero.
- Preserve source identity, registry identity, cache keys and provenance on
  every new input or solver path.
- Do not let a free solver variable silently absorb direction-split residuals.
- A fallback or relaxed rung must be explicit in outputs and must not weaken a
  publication gate.
- Keep pure numerical kernels separate from artifact I/O and orchestration.
- Run `make test-demand`; use the full suite for solver, provenance or cache-key
  changes.
