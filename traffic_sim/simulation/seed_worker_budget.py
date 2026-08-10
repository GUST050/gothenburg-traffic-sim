"""How many concurrent SUMO seed workers the recorded benchmark approves.

Split out of `monthly_sumo.py` unchanged, for one reason: it is a JSON reader
with no SUMO, numeric or simulation dependency, and it is consulted by the
closure-search CLI BEFORE any simulation exists. Importing it from
`monthly_sumo` dragged in `run_scenario` (pandas) and `suggest_closure_time`
(SciPy) — about 110 MiB of resident memory — just to read one integer out of a
validation record, including on runs the exact preflight then refuses.

`monthly_sumo` re-exports both names, so every existing importer is unaffected
and the record's contract is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

SEED_WORKER_BENCHMARK_RECORD = (
    Path("validation") / "a2_parallel_seed_benchmark_v1.json"
)


def approved_seed_workers(path: Path | None = None) -> int:
    """How many concurrent SUMO seeds the recorded benchmark approves.

    Fail closed at 1 (today's proven behaviour) unless a benchmark record
    exists that actually demonstrates what the speed plan's stage-A2 gate
    asks for: identical evidence between serial and parallel execution, and
    a measured peak memory that fits the machine. A record that merely
    asserts a number, without those two facts, unlocks nothing.
    """
    try:
        record = json.loads(
            Path(path if path is not None else SEED_WORKER_BENCHMARK_RECORD)
            .read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return 1
    approved = record.get("approved_seed_workers")
    if (
        not isinstance(record, dict)
        or record.get("kind") != "monthly_seed_worker_benchmark_record"
        or record.get("gate_status") != "pass"
        or record.get("evidence_identical") is not True
        or record.get("peak_rss_within_budget") is not True
        or isinstance(approved, bool)
        or not isinstance(approved, int)
        or approved < 1
    ):
        return 1
    return approved
