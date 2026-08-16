# Validation evidence guidance

- Treat frozen evidence as immutable. Never edit a record solely to make a
  changed source digest pass.
- New evidence gets a versioned path, complete source/input identities and an
  explicit claim boundary.
- Missing folds or incomplete evidence produce `INCONCLUSIVE` or failure, not
  an inferred pass.
- Large raw outputs should be compressed or stored externally according to
  `docs/ai/ARTIFACT_POLICY.md`; keep manifests and digests reviewable in Git.
