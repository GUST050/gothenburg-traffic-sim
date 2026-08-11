"""A measured budget for daily units, replacing a bare 10,000 cap.

WHY THE OLD CAP HAD TO GO, AND WHY NOT BY RAISING IT.  The plan's final
acceptance criterion is a six-month 360-hour search: 11,813 parent schedules and
23,349 unique daily units. It is legal under the calendar contract and was
rejected for one reason only — a constant. Measured on this machine, streaming
that whole enumeration takes 14.3 s at 25.6 MiB peak RSS, comfortably inside the
64 MiB process gate. The enumeration was never memory-bound; the number was
arbitrary.

Replacing 10,000 with a bigger number would repeat the mistake at a new
threshold. So the limit becomes a BUDGET with three properties the old cap
lacked:

  * it is measured, not guessed — units, ledger bytes on disk and process peak
    RSS, each declared in advance;
  * exceeding it PAUSES with a resumable, explicitly incomplete result rather
    than raising or, far worse, silently truncating;
  * an incomplete search can never be called exhaustive. `status` says
    `incomplete_budget_stopped`, and the shortlist it produces carries that
    status with it, so nothing downstream can quietly treat a partial
    enumeration as the complete one.

WHAT IS DELIBERATELY RETAINED.  The 100,000-parent protection stays exactly as
it was. It guards a different failure — an enumeration that would not terminate
in useful time — and nothing here measures it well enough to replace it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA = "daily_unit_budget_v2"

#: The status a search reports when a budget stopped it. Deliberately not a
#: variant of "ready": a caller that has not been taught this word must not
#: mistake a partial enumeration for a complete one.
INCOMPLETE_STATUS = "incomplete_budget_stopped"
COMPLETE_STATUS = "complete"

#: The legacy cap, retained as the DEFAULT budget so no existing search changes
#: behaviour by accident. A caller that wants the six-month case declares a
#: budget that admits it, and says so in the record.
LEGACY_DAILY_UNIT_LIMIT = 10_000

#: Measured headroom for the largest case the plan names, with room to spare:
#: 23,349 units at 25.6 MiB. The RSS figure is the process gate PR C froze; the
#: disk figure is the plan's measured 121 MB of NDJSON for that case, rounded
#: up to a round budget rather than fitted to the observation.
SIX_MONTH_DAILY_UNIT_BUDGET = 30_000
DEFAULT_PEAK_RSS_BUDGET_BYTES = 64 * 1024 * 1024
DEFAULT_LEDGER_BUDGET_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class DailyUnitBudget:
    """What one search is allowed to spend before it must pause.

    Every field is a number someone declared in advance and a run can be
    measured against. `maximum_parent_schedules` is carried here so a record
    shows both protections together, but it is unchanged and still fatal —
    a search that would enumerate more parents than that is refused, not
    paused.
    """

    maximum_daily_units: int = LEGACY_DAILY_UNIT_LIMIT
    maximum_peak_rss_bytes: int = DEFAULT_PEAK_RSS_BUDGET_BYTES
    maximum_ledger_bytes: int = DEFAULT_LEDGER_BUDGET_BYTES
    maximum_parent_schedules: int = 100_000

    def __post_init__(self) -> None:
        for field in ("maximum_daily_units", "maximum_peak_rss_bytes",
                      "maximum_ledger_bytes", "maximum_parent_schedules"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "maximum_daily_units": self.maximum_daily_units,
            "maximum_peak_rss_bytes": self.maximum_peak_rss_bytes,
            "maximum_ledger_bytes": self.maximum_ledger_bytes,
            "maximum_parent_schedules": self.maximum_parent_schedules,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DailyUnitBudget":
        if not isinstance(raw, Mapping):
            raise ValueError("daily unit budget must be an object")
        if raw.get("schema") != SCHEMA:
            raise ValueError("daily unit budget schema is unsupported")
        return cls(
            maximum_daily_units=int(raw["maximum_daily_units"]),
            maximum_peak_rss_bytes=int(raw["maximum_peak_rss_bytes"]),
            maximum_ledger_bytes=int(raw["maximum_ledger_bytes"]),
            maximum_parent_schedules=int(raw.get(
                "maximum_parent_schedules", 100_000)),
        )

    @property
    def content_key(self) -> str:
        import hashlib

        canonical = json.dumps(self.to_dict(), sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class BudgetState:
    """How much of a budget one enumeration actually spent."""

    daily_units: int = 0
    parent_schedules: int = 0
    ledger_bytes: int = 0
    peak_rss_bytes: int = 0
    status: str = COMPLETE_STATUS
    stopped_by: str | None = None
    resume_after_parent_id: str | None = None

    @property
    def complete(self) -> bool:
        return self.status == COMPLETE_STATUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "complete": self.complete,
            "stopped_by": self.stopped_by,
            "resume_after_parent_id": self.resume_after_parent_id,
            "daily_units": self.daily_units,
            "parent_schedules": self.parent_schedules,
            "ledger_bytes": self.ledger_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
        }


def exceeded(budget: DailyUnitBudget, *, daily_units: int,
             ledger_bytes: int = 0, peak_rss_bytes: int = 0) -> str | None:
    """Which budget line was crossed, or None.

    Checked in a fixed order so a record naming a cause is deterministic:
    two runs that cross two lines in the same step must blame the same one.
    """
    if daily_units > budget.maximum_daily_units:
        return "maximum_daily_units"
    if ledger_bytes > budget.maximum_ledger_bytes:
        return "maximum_ledger_bytes"
    if peak_rss_bytes > budget.maximum_peak_rss_bytes:
        return "maximum_peak_rss_bytes"
    return None


def describe(state: BudgetState, budget: DailyUnitBudget) -> str:
    """A sentence a user can act on, not a stack trace."""
    if state.complete:
        return (f"enumerated {state.parent_schedules} parent schedules and "
                f"{state.daily_units} unique daily units within budget")
    return (
        f"paused after {state.parent_schedules} parent schedules and "
        f"{state.daily_units} unique daily units: the search reached its "
        f"{state.stopped_by} budget "
        f"({getattr(budget, state.stopped_by or 'maximum_daily_units')}). "
        f"This result is INCOMPLETE and resumable — it is not an exhaustive "
        f"search, and must not be compared against one.")
