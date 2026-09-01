"""Digest-bound opt-in index for exact daily window costs.

The index is intentionally a narrow acceleration layer.  It stores the exact
three variant records produced by the deterministic oracle for each daily
unit, keyed by both the daily-unit identity and its schedule identity.  It can
only be used after a complete oracle population has been built and compared
field-for-field; partial, stale, or swapped state is rejected.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

VARIANTS = ("q10", "q50", "q90")
SCHEMA = "window_cost_index_v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class WindowCostIndexError(ValueError):
    """The index is incomplete or does not match the bound oracle."""


def _normalise_records(
    records: Mapping[str, Mapping[str, Any]],
    *,
    expected_daily_units: int | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, Mapping) or not records:
        raise WindowCostIndexError("window cost index has no daily records")
    normalised: dict[str, dict[str, Any]] = {}
    for raw_unit_id, raw_value in records.items():
        unit_id = str(raw_unit_id)
        if not unit_id or unit_id in normalised:
            raise WindowCostIndexError("window cost index repeats a unit")
        if not isinstance(raw_value, Mapping):
            raise WindowCostIndexError("window cost index unit is not an object")
        schedule_id = str(raw_value.get("schedule_id", ""))
        raw_records = raw_value.get("records")
        if not schedule_id or not isinstance(raw_records, Sequence) \
                or isinstance(raw_records, (str, bytes)) \
                or len(raw_records) != len(VARIANTS):
            raise WindowCostIndexError(
                f"window cost index unit {unit_id!r} lacks q10/q50/q90")
        variants = []
        for expected, raw_record in zip(VARIANTS, raw_records):
            if not isinstance(raw_record, Mapping):
                raise WindowCostIndexError(
                    f"window cost index record {unit_id!r} is not an object")
            record = dict(raw_record)
            if str(record.get("demand_variant", "")) != expected:
                raise WindowCostIndexError(
                    f"window cost index unit {unit_id!r} has swapped variants")
            variants.append(record)
        normalised[unit_id] = {
            "unit_id": unit_id,
            "schedule_id": schedule_id,
            "records": variants,
        }
    if expected_daily_units is not None \
            and len(normalised) != expected_daily_units:
        raise WindowCostIndexError(
            f"window cost index has {len(normalised)} units, expected "
            f"{expected_daily_units}")
    return normalised


class WindowCostIndex:
    """An exact, identity-bound lookup table for daily cost records."""

    def __init__(
        self,
        *,
        bound_identity: Mapping[str, Any],
        records: Mapping[str, Mapping[str, Any]],
        preparation_time_s: float = 0.0,
    ) -> None:
        if not isinstance(bound_identity, Mapping) or not bound_identity:
            raise WindowCostIndexError("index identity must be non-empty")
        if preparation_time_s < 0:
            raise WindowCostIndexError("index preparation time cannot be negative")
        if not math.isfinite(float(preparation_time_s)):
            raise WindowCostIndexError("index preparation time must be finite")
        self.bound_identity = json.loads(_canonical(bound_identity))
        self.identity_digest = _digest(self.bound_identity)
        self.records = _normalise_records(records)
        self.preparation_time_s = float(preparation_time_s)
        self.content_key = _digest(self.to_dict(include_content_key=False))

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
        *,
        expected_identity: Mapping[str, Any] | None = None,
        expected_daily_units: int | None = None,
        expected_variant_records: int | None = None,
    ) -> "WindowCostIndex":
        if not isinstance(raw, Mapping) or raw.get("schema") != SCHEMA:
            raise WindowCostIndexError("unsupported window cost index schema")
        body = {key: value for key, value in raw.items() if key != "content_key"}
        if raw.get("content_key") != _digest(body):
            raise WindowCostIndexError("window cost index content key mismatch")
        identity = raw.get("bound_identity")
        if not isinstance(identity, Mapping):
            raise WindowCostIndexError("window cost index identity is missing")
        if raw.get("identity_digest") != _digest(identity):
            raise WindowCostIndexError("window cost index identity digest mismatch")
        if expected_identity is not None \
                and dict(identity) != dict(expected_identity):
            raise WindowCostIndexError("window cost index identity is stale")
        records = raw.get("records")
        normalised = _normalise_records(
            records if isinstance(records, Mapping) else {},
            expected_daily_units=expected_daily_units,
        )
        if expected_variant_records is not None \
                and len(normalised) * len(VARIANTS) != expected_variant_records:
            raise WindowCostIndexError("window cost index population is partial")
        return cls(
            bound_identity=identity,
            records=normalised,
            preparation_time_s=float(raw.get("preparation_time_s", 0.0)),
        )

    def to_dict(self, *, include_content_key: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "bound_identity": self.bound_identity,
            "identity_digest": self.identity_digest,
            "daily_unit_count": len(self.records),
            "daily_variant_record_count": len(self.records) * len(VARIANTS),
            "preparation_time_s": self.preparation_time_s,
            "records": self.records,
        }
        if include_content_key:
            payload["content_key"] = _digest(payload)
        return payload

    def lookup(self, unit_id: str, schedule_id: str) -> tuple[Mapping[str, Any], ...]:
        item = self.records.get(str(unit_id))
        if item is None:
            raise WindowCostIndexError(
                f"window cost index has no unit {unit_id!r}")
        if item["schedule_id"] != str(schedule_id):
            raise WindowCostIndexError(
                f"window cost index schedule identity swapped for {unit_id!r}")
        return tuple(dict(record) for record in item["records"])

    def compare_oracle(
        self,
        oracle_records: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Compare every stored field against the complete exact oracle."""
        oracle = _normalise_records(oracle_records,
                                    expected_daily_units=len(self.records))
        mismatches: list[dict[str, Any]] = []
        for unit_id in sorted(self.records):
            indexed = self.records[unit_id]
            expected = oracle.get(unit_id)
            if expected is None:
                mismatches.append({"unit_id": unit_id, "reason": "missing"})
                continue
            if indexed["schedule_id"] != expected["schedule_id"]:
                mismatches.append({"unit_id": unit_id, "reason": "swapped"})
                continue
            for variant, left, right in zip(
                VARIANTS, indexed["records"], expected["records"]
            ):
                if left != right:
                    mismatches.append({"unit_id": unit_id,
                                       "variant": variant,
                                       "reason": "field_mismatch"})
        return {
            "oracle_complete": len(oracle) == len(self.records),
            "indexed_daily_units": len(self.records),
            "indexed_variant_records": len(self.records) * len(VARIANTS),
            "field_identical": not mismatches,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }


def write_index(path: Path, index: WindowCostIndex) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite window cost index: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def load_index(
    path: Path,
    *,
    expected_identity: Mapping[str, Any] | None = None,
    expected_daily_units: int,
    expected_variant_records: int,
) -> WindowCostIndex:
    """Load the opt-in index only after validating its complete binding.

    Keeping file loading here gives the product path one fail-closed seam:
    callers cannot accidentally pass a partial, swapped, or stale JSON object
    merely because its filename looks right.  When the active resolver is the
    authority for the identity, it may perform the second-stage provider/input
    check before the first lookup; using an index remains opt-in and never
    changes the default cost path.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WindowCostIndexError(
            f"window cost index is unreadable: {path}") from error
    return WindowCostIndex.from_dict(
        raw,
        expected_identity=expected_identity,
        expected_daily_units=expected_daily_units,
        expected_variant_records=expected_variant_records,
    )
