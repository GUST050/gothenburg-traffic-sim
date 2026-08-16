# Test guidance

- Tests protect behavior, scientific claims and provenance. Do not loosen an
  assertion merely because new code fails it.
- Separate deterministic unit/contract checks from SUMO, browser, slow and
  release-evidence checks.
- Frozen-version tests are append-only historical contracts; add a successor
  rather than mutating the frozen expectation without an explicit supersession.
- Reproduce a failure before fixing it and add the smallest regression test
  that proves the root cause.
- Run the narrowest target first, then `make test` for cross-domain changes.
