"""Row schema and validation for `training_table_v2` (plan step 1).

The v1 table (`dirsplit/dataset.py`) writes ONE row per
``(station, heading, hour, day-type)`` holding the **mean** share across all
observed days. That average is the reason the quantile models could not mean
what their names say: they saw variation *between aggregated station-hours*,
never the day-to-day variation that a future day actually has. Fitting a q10
to between-station spread and then labelling it "the 10th percentile day" is
a category error, and no amount of post-hoc calibration on the aggregate can
recover the distribution that was averaged away before training started.

v2 therefore keeps the grain the label is generated at: one row per
``(station_id, local_date, hour, heading)``, with the **raw counts** rather
than only the derived share. Raw counts matter for two reasons the plan names:
a share of 3/5 vehicles is noise while 300/500 is evidence, and a count pair
is the natural likelihood for a beta-binomial model (step 2's fourth
candidate).

Three further v1 problems this schema fixes structurally:

* **`is_weekend` was constant.** v1 wrote the column, but `train.py` filtered
  to weekdays 06-20, so the model never saw a nonzero value while
  `predict.py` still emitted all 24 hours with ``is_weekend=0``. Here the
  field is real, and `temporal_support` records whether a given cell was
  actually observed rather than extrapolated.
* **Dropped rows vanished silently.** v1 filtered low-coverage and low-volume
  rows out of existence. v2 keeps them with an explicit
  :class:`MissingnessReason`, so a consumer chooses what to exclude and the
  count of what was excluded is auditable.
* **No blocking key.** Held-out validation, calibration and the step-4
  residual library all need to keep simultaneous observations together;
  independent row sampling would leak within-day correlation across the fold
  boundary. ``day_block_id`` (city×date) and ``station_day_id``
  (station×date) are written once here so every later stage blocks the same
  way.

The time resolution of the source is recorded rather than assumed. Statens
vegvesen delivers hourly volumes; the 15-minute deployment profile is derived
downstream, and ``time_resolution="hour"`` keeps that derivation honest — the
model must not appear to have observed quarter-hour variation it never saw.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date as date_cls
from datetime import datetime
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "dirsplit_training_table_v2"

#: Coverage below which the source itself flags the hour as unreliable.
MIN_COVERAGE_PCT = 80.0

#: Total vehicles/hour below which a share is noise rather than signal.
MIN_TOTAL_VEH_H = 30.0

#: Recognised source time resolutions.
TIME_RESOLUTIONS: frozenset[str] = frozenset(("hour", "quarter"))


class DatasetError(ValueError):
    """Raised when a row would violate the v2 schema."""


class MissingnessReason:
    """Why a row is not usable as a training observation.

    ``OK`` rows are usable. Every other value marks a row that v1 would have
    deleted; keeping it makes the exclusion count auditable instead of
    invisible.
    """

    OK = "ok"
    LOW_COVERAGE = "low_coverage"
    LOW_VOLUME = "low_volume"
    MISSING_DIRECTION = "missing_direction"
    ZERO_TOTAL = "zero_total"

    ALL: frozenset[str] = frozenset(
        (OK, LOW_COVERAGE, LOW_VOLUME, MISSING_DIRECTION, ZERO_TOTAL)
    )


def day_block_id(city: str, local_date: str) -> str:
    """Block key holding one city-date together.

    Step 4's residual library keeps each city-date as a whole block so that
    the correlation between roads on the same day survives into the generated
    scenarios. Blocking validation by the same key is what stops that
    correlation leaking across a fold boundary.
    """
    return f"{city}:{local_date}"


def station_day_id(station_id: str, local_date: str) -> str:
    """Block key holding one station-date together."""
    return f"{station_id}:{local_date}"


@dataclass(frozen=True)
class DirectionRow:
    """One station, one calendar date, one hour, one heading."""

    station_id: str
    city: str
    heading: str
    local_date: str
    hour: int
    weekday: int
    is_weekend: int
    month: int
    toward_count: float
    away_count: float
    total_count: float
    share: float | None
    coverage_pct: float
    missingness: str
    time_resolution: str
    day_block_id: str
    station_day_id: str
    is_holiday: int | None = None
    features: Mapping[str, float] = field(default_factory=dict)
    profile: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("station_id", "city", "heading", "local_date"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DatasetError(f"{name} must be a non-empty string")
        try:
            parsed = date_cls.fromisoformat(self.local_date)
        except ValueError as exc:
            raise DatasetError(
                f"local_date must be ISO YYYY-MM-DD, got {self.local_date!r}"
            ) from exc
        if not 0 <= int(self.hour) <= 23:
            raise DatasetError(f"hour must be 0..23, got {self.hour!r}")
        if not 0 <= int(self.weekday) <= 6:
            raise DatasetError(f"weekday must be 0..6, got {self.weekday!r}")
        if int(self.weekday) != parsed.weekday():
            raise DatasetError(
                f"weekday {self.weekday} disagrees with {self.local_date} "
                f"(which is {parsed.weekday()})"
            )
        expected_weekend = 1 if parsed.weekday() >= 5 else 0
        if int(self.is_weekend) != expected_weekend:
            raise DatasetError(
                f"is_weekend {self.is_weekend} disagrees with {self.local_date}"
            )
        if int(self.month) != parsed.month:
            raise DatasetError(
                f"month {self.month} disagrees with {self.local_date}"
            )
        for name in ("toward_count", "away_count", "total_count"):
            value = float(getattr(self, name))
            if value < 0:
                raise DatasetError(f"{name} must be non-negative, got {value}")
        if self.missingness not in MissingnessReason.ALL:
            raise DatasetError(
                f"missingness must be one of {sorted(MissingnessReason.ALL)}, "
                f"got {self.missingness!r}"
            )
        if self.time_resolution not in TIME_RESOLUTIONS:
            raise DatasetError(
                f"time_resolution must be one of {sorted(TIME_RESOLUTIONS)}"
            )
        if self.share is not None and not 0.0 <= float(self.share) <= 1.0:
            raise DatasetError(f"share must lie in [0, 1], got {self.share!r}")
        if self.is_usable and self.share is None:
            raise DatasetError("a usable row must carry a share")
        if self.day_block_id != day_block_id(self.city, self.local_date):
            raise DatasetError("day_block_id does not match city/local_date")
        if self.station_day_id != station_day_id(self.station_id, self.local_date):
            raise DatasetError(
                "station_day_id does not match station_id/local_date"
            )

    @property
    def is_usable(self) -> bool:
        return self.missingness == MissingnessReason.OK

    def flat(self) -> dict[str, Any]:
        """Flat dict for CSV writing — features/profile inlined."""
        payload = asdict(self)
        payload.pop("features")
        payload.pop("profile")
        payload.update(self.features)
        payload.update(self.profile)
        return payload


def classify(
    *,
    heading_present: bool,
    total_count: float,
    coverage_pct: float,
    min_coverage: float = MIN_COVERAGE_PCT,
    min_total: float = MIN_TOTAL_VEH_H,
) -> str:
    """Decide a row's missingness reason.

    Order matters and is deliberate: a missing direction is a structural gap,
    a low coverage figure is the source disclaiming its own hour, and a low
    volume is a statistical judgement about the share. Reporting the first
    applicable reason keeps the diagnosis specific.
    """
    if not heading_present:
        return MissingnessReason.MISSING_DIRECTION
    if total_count <= 0:
        return MissingnessReason.ZERO_TOTAL
    if coverage_pct < min_coverage:
        return MissingnessReason.LOW_COVERAGE
    if total_count < min_total:
        return MissingnessReason.LOW_VOLUME
    return MissingnessReason.OK


def make_row(
    *,
    station_id: str,
    city: str,
    heading: str,
    timestamp: str | datetime,
    toward_count: float,
    away_count: float,
    coverage_pct: float,
    features: Mapping[str, float] | None = None,
    profile: Mapping[str, float] | None = None,
    time_resolution: str = "hour",
    is_holiday: int | None = None,
    min_coverage: float = MIN_COVERAGE_PCT,
    min_total: float = MIN_TOTAL_VEH_H,
    heading_present: bool = True,
) -> DirectionRow:
    """Build one validated v2 row from a raw observation."""
    moment = (timestamp if isinstance(timestamp, datetime)
              else datetime.fromisoformat(timestamp))
    local_date = moment.date().isoformat()
    total = float(toward_count) + float(away_count)
    reason = classify(
        heading_present=heading_present,
        total_count=total,
        coverage_pct=float(coverage_pct),
        min_coverage=min_coverage,
        min_total=min_total,
    )
    share = (float(toward_count) / total) if total > 0 else None
    return DirectionRow(
        station_id=station_id,
        city=city,
        heading=heading,
        local_date=local_date,
        hour=moment.hour,
        weekday=moment.weekday(),
        is_weekend=1 if moment.weekday() >= 5 else 0,
        month=moment.month,
        toward_count=float(toward_count),
        away_count=float(away_count),
        total_count=total,
        share=share,
        coverage_pct=float(coverage_pct),
        missingness=reason,
        time_resolution=time_resolution,
        day_block_id=day_block_id(city, local_date),
        station_day_id=station_day_id(station_id, local_date),
        is_holiday=is_holiday,
        features=dict(features or {}),
        profile=dict(profile or {}),
    )


# ──────────────────────────────────────────────────────────────────────────
# diagnostics — the v1 view, derived rather than stored
# ──────────────────────────────────────────────────────────────────────────
def aggregate_like_v1(rows: Iterable[DirectionRow]) -> list[dict[str, Any]]:
    """Reproduce v1's `(station, heading, hour, day-type)` mean-share table.

    Kept as a DIAGNOSTIC only, and derived from v2 rather than stored
    alongside it. Two reasons: it proves v2 is a strict superset of v1 (the
    aggregate is recoverable, so nothing was lost), and it keeps exactly one
    place where the averaging happens, so training code cannot reach for the
    averaged table by accident.

    v1 computed the mean over usable observations, then dropped the cell if
    the MEAN total fell below the volume floor. That order is reproduced here
    so the comparison is like-for-like.
    """
    buckets: dict[tuple[str, str, int, int], list[DirectionRow]] = {}
    for row in rows:
        if row.missingness in (MissingnessReason.MISSING_DIRECTION,
                               MissingnessReason.ZERO_TOTAL,
                               MissingnessReason.LOW_COVERAGE):
            continue
        key = (row.station_id, row.heading, row.hour, row.is_weekend)
        buckets.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    for (station_id, heading, hour, is_weekend), group in sorted(buckets.items()):
        mean_total = sum(r.total_count for r in group) / len(group)
        if mean_total < MIN_TOTAL_VEH_H:
            continue
        shares = [r.share for r in group if r.share is not None]
        if not shares:
            continue
        out.append({
            "station_id": station_id,
            "city": group[0].city,
            "heading": heading,
            "hour": hour,
            "is_weekend": is_weekend,
            "share": round(sum(shares) / len(shares), 4),
            "n_obs": len(group),
            "mean_total_veh_h": round(mean_total, 1),
        })
    return out


def summarise(rows: Iterable[DirectionRow]) -> dict[str, Any]:
    """Counts by missingness, plus block and temporal-support inventory."""
    rows = list(rows)
    by_reason: dict[str, int] = {}
    for row in rows:
        by_reason[row.missingness] = by_reason.get(row.missingness, 0) + 1
    usable = [r for r in rows if r.is_usable]
    hours_seen = sorted({r.hour for r in usable})
    return {
        "schema_version": SCHEMA_VERSION,
        "n_rows": len(rows),
        "n_usable": len(usable),
        "by_missingness": dict(sorted(by_reason.items())),
        "n_stations": len({r.station_id for r in rows}),
        "n_day_blocks": len({r.day_block_id for r in rows}),
        "n_station_days": len({r.station_day_id for r in rows}),
        "n_dates": len({r.local_date for r in rows}),
        "cities": sorted({r.city for r in rows}),
        "hours_observed": hours_seen,
        "weekend_rows_usable": sum(1 for r in usable if r.is_weekend),
        "weekday_rows_usable": sum(1 for r in usable if not r.is_weekend),
        "time_resolutions": sorted({r.time_resolution for r in rows}),
    }


def temporal_support(rows: Iterable[DirectionRow]) -> dict[str, Any]:
    """Which (hour, day-type) cells are genuinely observed.

    Step 3's applicability profile reads this. The v1 pipeline could not
    answer the question at all: it trained on weekdays 06-20 and predicted
    all 24 hours, so every off-hour and every weekend prediction was silent
    extrapolation. Naming the supported set makes that visible instead.
    """
    rows = list(rows)
    supported: dict[str, set[int]] = {"weekday": set(), "weekend": set()}
    counts: dict[tuple[str, int], int] = {}
    for row in rows:
        if not row.is_usable:
            continue
        key = "weekend" if row.is_weekend else "weekday"
        supported[key].add(row.hour)
        counts[(key, row.hour)] = counts.get((key, row.hour), 0) + 1
    return {
        "weekday_hours": sorted(supported["weekday"]),
        "weekend_hours": sorted(supported["weekend"]),
        "unsupported_weekday_hours": sorted(
            set(range(24)) - supported["weekday"]
        ),
        "unsupported_weekend_hours": sorted(
            set(range(24)) - supported["weekend"]
        ),
        "observations": {f"{k[0]}:{k[1]:02d}": v
                         for k, v in sorted(counts.items())},
    }


__all__ = [
    "SCHEMA_VERSION", "MIN_COVERAGE_PCT", "MIN_TOTAL_VEH_H",
    "TIME_RESOLUTIONS", "DatasetError", "MissingnessReason", "DirectionRow",
    "classify", "make_row", "day_block_id", "station_day_id",
    "aggregate_like_v1", "summarise", "temporal_support",
]
