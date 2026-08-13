"""A measured budget for daily units, replacing a bare 10,000 cap.

WHY THE OLD CAP HAD TO GO, AND WHY NOT BY RAISING IT.  The plan's final
acceptance criterion is a six-month 360-hour search: 11,813 parent schedules and
23,349 unique daily units. It is legal under the calendar contract and was
rejected for one reason only — a constant. Measured on this machine, streaming
that whole enumeration takes 14.3 s at 25.6 MiB peak RSS, comfortably inside the
64 MiB process gate. The enumeration was never memory-bound; the number was
arbitrary.

Replacing 10,000 with a bigger number would repeat the mistake at a new
threshold. The runtime contract therefore separates two different limits:

  * ``maximum_daily_units`` bounds NEW work in one invocation. Exceeding it
    pauses at the previous complete parent and the counter resets on resume;
  * ``maximum_total_daily_units`` bounds the cumulative exact search and is a
    hard, preflight-checked safety limit. It never resets;
  * an incomplete search can never be called exhaustive. `status` says
    `incomplete_budget_stopped`, and the checkpoint is a different artifact
    kind from the final screening, so downstream code cannot treat it as the
    complete one.

WHAT IS DELIBERATELY RETAINED.  The 100,000-parent protection stays exactly as
it was. It guards a different failure — an enumeration that would not terminate
in useful time — and nothing here measures it well enough to replace it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA = "daily_unit_budget_v3"

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
#: 23,349 units at 25.6 MiB. RSS and ledger size remain separate measured
#: validation gates; this runtime budget intentionally counts work units only.
SIX_MONTH_DAILY_UNIT_BUDGET = 30_000
DEFAULT_TOTAL_DAILY_UNIT_LIMIT = 100_000


@dataclass(frozen=True)
class DailyUnitBudget:
    """Bound one resumable enumeration leg and the complete search.

    ``maximum_daily_units`` is deliberately retained as the public field name
    used by the CLI, but its v3 meaning is *new unique units per invocation*.
    ``maximum_total_daily_units`` and ``maximum_parent_schedules`` are hard
    protections for the whole search. They are refused, not paged around.

    RSS and ledger-size measurements remain validation gates in their own
    frozen records; they are not represented here because the enumerator does
    not sample either value and a declared-but-unenforced runtime field is a
    false contract.
    """

    maximum_daily_units: int = LEGACY_DAILY_UNIT_LIMIT
    maximum_total_daily_units: int = DEFAULT_TOTAL_DAILY_UNIT_LIMIT
    maximum_parent_schedules: int = 100_000

    def __post_init__(self) -> None:
        for field in ("maximum_daily_units", "maximum_total_daily_units",
                      "maximum_parent_schedules"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "maximum_daily_units": self.maximum_daily_units,
            "maximum_total_daily_units": self.maximum_total_daily_units,
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
            maximum_total_daily_units=int(raw["maximum_total_daily_units"]),
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
    leg_daily_units: int = 0
    parent_schedules: int = 0
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
            "leg_daily_units": self.leg_daily_units,
            "parent_schedules": self.parent_schedules,
        }


ENFORCED_IN_PRODUCT_PATH = (
    "maximum_daily_units",
    "maximum_total_daily_units",
    "maximum_parent_schedules",
)
DECLARED_BUT_NOT_YET_SAMPLED: tuple[str, ...] = ()


def exceeded(
    budget: DailyUnitBudget,
    *,
    new_daily_units: int,
    total_daily_units: int,
) -> str | None:
    """Which budget line was crossed, or None.

    Checked in a fixed order so a record naming a cause is deterministic:
    two runs that cross two lines in the same step must blame the same one.
    """
    if total_daily_units > budget.maximum_total_daily_units:
        return "maximum_total_daily_units"
    if new_daily_units > budget.maximum_daily_units:
        return "maximum_daily_units"
    return None


def describe(state: BudgetState, budget: DailyUnitBudget) -> str:
    """A sentence a user can act on, not a stack trace."""
    if state.complete:
        return (f"enumerated {state.parent_schedules} parent schedules and "
                f"{state.daily_units} unique daily units within budget")
    stopped_by = state.stopped_by or "unknown"
    limit = getattr(budget, stopped_by, "unknown")
    return (
        f"paused after {state.parent_schedules} parent schedules and "
        f"{state.daily_units} total unique daily units "
        f"({state.leg_daily_units} in this invocation): the search reached "
        f"its "
        f"{stopped_by} budget ({limit}). "
        f"This result is INCOMPLETE and resumable — it is not an exhaustive "
        f"search, and must not be compared against one.")
