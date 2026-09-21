"""Read-only analysis of finished runs.

Nothing in this package may be imported by the code that PRODUCES evidence.
`deterministic_disruption.COSTING_SOURCES` hashes the bytes of every module
that decides a closure cost into the daily-cost cache key, so a module that
merely READS those numbers must live outside that set — otherwise improving a
chart would invalidate a search that took hours to run.
"""
