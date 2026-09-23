"""Read a completed delay profile without importing the simulation stack."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping


PROFILE_FILENAME = "delay-profile.json"
SCHEMA_VERSION = 1
MEASURE = "deterministic_detour_seconds_v1"


def read_delay_profile(search_dir: Path) -> dict[str, Any] | None:
    """Return a stored profile if it is a JSON object."""
    try:
        payload = json.loads((Path(search_dir) / PROFILE_FILENAME).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def profile_matches_search(
    payload: Mapping[str, Any], search_id: str, content_key: str,
) -> bool:
    """Bind a stored profile to the search and cost measure being displayed."""
    return (
        payload.get("kind") == "closure_delay_profile"
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("measure") == MEASURE
        and str(payload.get("search_id")) == str(search_id)
        and str(payload.get("search_content_key")) == str(content_key)
    )


def read_verified_result(search_dir: Path) -> tuple[dict[str, Any], str] | None:
    """Read the succeeded search result only when its recorded bytes match."""
    directory = Path(search_dir)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(
            encoding="utf-8"))
        if manifest.get("status") != "succeeded":
            return None
        records = [item for item in manifest.get("artifacts", ())
                   if isinstance(item, dict)
                   and item.get("kind") == "monthly_closure_search_result"]
        if len(records) != 1:
            return None
        record = records[0]
        relative = PurePosixPath(record["path"])
        if (relative.is_absolute() or len(relative.parts) < 2
                or relative.parts[0] != "artifacts"
                or any(part in {".", ".."} for part in relative.parts)):
            return None
        target = directory.joinpath(*relative.parts).resolve()
        if target.parent != (directory / "artifacts").resolve():
            return None
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if (digest != record.get("sha256")
                or len(raw) != record.get("bytes")):
            return None
        result = json.loads(raw)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return (result, digest) if isinstance(result, dict) else None


def _ranked_candidates(result: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    comparison = result.get("period_comparison")
    if isinstance(comparison, Mapping):
        rows = [(str(period["best_schedule"]["schedule_id"]), period["best_cost"])
                for period in comparison.get("periods", ())
                if isinstance(period, Mapping)
                and isinstance(period.get("best_schedule"), Mapping)
                and isinstance(period.get("best_cost"), Mapping)]
    else:
        statistics: dict[str, Mapping[str, Any]] = {}
        for source in ("robust_decision", "pilot_selection"):
            group = result.get(source) or {}
            for record in group.get("candidates", ()):
                if isinstance(record, Mapping):
                    statistics[str(record.get("candidate_id"))] = record
        rows = []
        for schedule in result.get("shortlisted_schedules", ()):
            if not isinstance(schedule, Mapping):
                continue
            candidate_id = str(schedule.get("schedule_id"))
            record = statistics.get(candidate_id)
            cost = record.get("closure_cost") if isinstance(record, Mapping) else None
            if (not isinstance(cost, Mapping) or record.get("hard_failures")
                    or int(cost.get("vehicles_no_detour", 0)) != 0):
                continue
            rows.append((candidate_id, cost))
    return sorted(rows, key=lambda item: (
        float(item[1]["added_vehicle_hours"]),
        float(item[1]["added_metres_total"]),
        int(item[1]["vehicles_affected"]), item[0]))


def profile_matches_result(
    payload: Mapping[str, Any], search_id: str, content_key: str,
    result: Mapping[str, Any], result_sha256: str,
) -> bool:
    """Check a cached diagram against the verified ranked result it describes."""
    result_spec = result.get("closure_search_spec")
    if (not profile_matches_search(payload, search_id, content_key)
            or result.get("search_id") != search_id
            or not isinstance(result_spec, Mapping)
            or result_spec.get("content_key") != content_key):
        return False
    if payload.get("result_sha256") not in (None, result_sha256):
        return False
    candidates = payload.get("candidates")
    bins = payload.get("bins")
    if (not isinstance(candidates, list) or not 1 <= len(candidates) <= 5
            or not isinstance(bins, list) or not bins):
        return False
    try:
        ranked = _ranked_candidates(result)
        if [item.get("schedule_id") for item in candidates] != [
                key for key, _cost in ranked[:len(candidates)]]:
            return False
        for candidate, (_identifier, cost) in zip(candidates, ranked):
            verification = candidate["cost_verification"]
            if (candidate["closure_cost"] != cost
                    or verification.get("matches") is not True):
                return False
            expected = {key: cost[key] for key in (
                "added_vehicle_hours", "added_metres_total",
                "vehicles_affected")}
            if (verification.get("published") != expected
                    or verification.get("recomputed") != expected):
                return False
            variants = candidate["variants"]
            if not isinstance(variants, Mapping) or "q50" not in variants:
                return False
            for variant in variants.values():
                vehicles = variant["vehicles"]
                if (not isinstance(vehicles, list)
                        or len(vehicles) != len(bins)
                        or any(type(count) is not int or count < 0
                               for count in vehicles)
                        or sum(vehicles) != variant["vehicles_affected"]):
                    return False
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False
    return True
