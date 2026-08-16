# Direction-split guidance

- Preserve every measured two-way total exactly: directed shares must be
  complementary, bounded and sum to one for every valid slot.
- Never present an estimated direction, q10/q90 stress arm or transferred
  prior as a measurement or calibrated probability interval.
- Keep sensor-specific facts in the registry/policy data, not hardcoded model
  branches.
- Gate M remains authoritative for the deployed model; daily-anchored TOT work
  requires the separate Gate D defined in the current plan.
- Use blocked-date, leave-station-out and leave-city-out evidence for changes
  that claim transfer to new sensors.
- Run `make test-dirsplit` and the provenance-bound gate tests affected by the
  change.
