# Web guidance

- Keep the provider seam: render code reads flows through the active provider,
  not directly from a specific artifact format.
- Simulation logic stays server-side; the browser submits versioned specs and
  renders curated results.
- Preserve missing-versus-zero behavior and honest confidence/evidence labels.
- Update cache-busting identifiers when shipped static assets change.
- Run `make test-web`; user-visible changes also require a browser smoke test
  when browser tooling is available.
