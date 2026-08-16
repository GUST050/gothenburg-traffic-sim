# Tooling guidance

- Tools are diagnostics, benchmarks, evidence builders or maintenance checks;
  product behavior belongs in an importable package.
- Default outputs should be explicit, non-clobbering and outside frozen
  evidence unless promotion is requested.
- Record input identities and distinguish diagnostic output from release
  evidence.
- Keep repository checks dependency-light so CI and coding agents can run them
  before expensive workflows.
