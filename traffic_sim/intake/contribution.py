"""Evidence-bound sensor contribution and placement reports.

The report compares immutable *before* and *after* artifacts.  It never
claims that a sensor improves the whole city: a station with no comparable
holdout or temporal fold is explicitly ``insufficient_evidence``.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any


def _load_ratios(report: dict | None, *, exclude: str | None = None) -> list[float]:
    values: list[float] = []
    for sensor_id, station in (report or {}).get("stations", {}).items():
        if exclude is not None and str(sensor_id) == str(exclude):
            continue
        for edge in (station or {}).get("edges", {}).values():
            try:
                ratio = float(edge.get("ratio"))
            except (AttributeError, TypeError, ValueError):
                continue
            if math.isfinite(ratio) and ratio > 0:
                values.append(ratio)
    return values


def _log_error(ratios: list[float]) -> float | None:
    if not ratios:
        return None
    return median(abs(math.log(ratio)) for ratio in ratios)


def _station(report: dict | None, sensor_id: str) -> dict | None:
    stations = (report or {}).get("stations", {})
    return stations.get(sensor_id) or stations.get(str(sensor_id))


def _ratio_keys(report: dict | None, *, exclude: str) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for station_id, station in (report or {}).get("stations", {}).items():
        if str(station_id) == str(exclude):
            continue
        for edge_id, edge in (station or {}).get("edges", {}).items():
            try:
                ratio = float(edge.get("ratio"))
            except (AttributeError, TypeError, ValueError):
                continue
            if math.isfinite(ratio) and ratio > 0:
                keys.add((str(station_id), str(edge_id)))
    return keys


def _network_edge_ids(network: dict | None) -> set[str]:
    return {str(feature.get("properties", {}).get("id"))
            for feature in (network or {}).get("features", [])
            if feature.get("properties", {}).get("id") is not None}


def _comparability(*, sensor_id: str, network_before: dict | None,
                   network_after: dict | None, loso_before: dict | None,
                   loso_after: dict | None) -> dict:
    """Prove that a before/after contribution result is a controlled comparison."""
    reasons: list[str] = []
    before_contract = (loso_before or {}).get("comparison_contract")
    after_contract = (loso_after or {}).get("comparison_contract")
    required = ("protocol", "source", "window_start", "window_end",
                "n_intervals", "simulation_mode", "candidate_pool_sha256",
                "network_sha256", "assignment_prior_enabled")
    if not isinstance(before_contract, dict) or not isinstance(after_contract, dict):
        reasons.append("LOSO-artifakterna saknar jämförelsekontrakt")
    else:
        for key in required:
            before_value, after_value = before_contract.get(key), after_contract.get(key)
            if before_value is None or after_value is None:
                reasons.append(f"LOSO-kontrakt saknar {key}")
            elif before_value != after_value:
                reasons.append(f"LOSO-kontrakt skiljer sig för {key}")

    before_edges, after_edges = _network_edge_ids(network_before), _network_edge_ids(network_after)
    if not before_edges or not after_edges:
        reasons.append("nätverksartifakt saknas eller saknar kant-ID:n")
    elif before_edges != after_edges:
        reasons.append("nätverkstopologin skiljer sig mellan före och efter")

    before_keys = _ratio_keys(loso_before, exclude=str(sensor_id))
    after_keys = _ratio_keys(loso_after, exclude=str(sensor_id))
    if not before_keys or not after_keys:
        reasons.append("saknar jämförbara held-out-sensorer")
    elif before_keys != after_keys:
        reasons.append("olika held-out-kanter jämförs före och efter")
    return {"comparable": not reasons, "reasons": reasons,
            "holdout_edge_count": len(before_keys & after_keys)}


def build_contribution(*, sensor_id: str, registry: dict,
                       network_before: dict | None,
                       network_after: dict | None,
                       loso_before: dict | None,
                       loso_after: dict | None,
                       flows_after: dict | None = None) -> dict:
    """Create a JSON-serialisable contribution report for one station."""
    entry = next((item for item in registry.get("sensors", [])
                  if str(item.get("sensor_id")) == str(sensor_id)), None)
    if entry is None:
        raise ValueError(f"sensor {sensor_id!r} is not in the registry")

    comparability = _comparability(
        sensor_id=str(sensor_id), network_before=network_before,
        network_after=network_after, loso_before=loso_before,
        loso_after=loso_after)
    before_ratios = _load_ratios(loso_before, exclude=sensor_id)
    after_ratios = _load_ratios(loso_after, exclude=sensor_id)
    before_error, after_error = _log_error(before_ratios), _log_error(after_ratios)
    if not comparability["comparable"] or before_error is None or after_error is None:
        outcome = "insufficient_evidence"
    else:
        delta = before_error - after_error
        outcome = ("improved" if delta > 0.01 else
                   "neutral" if delta >= -0.01 else
                   "worsened")

    before_edges = (network_before or {}).get("features", [])
    after_edges = (network_after or {}).get("features", [])
    before_conf = {f.get("properties", {}).get("id"): f.get("properties", {}).get("confidence")
                   for f in before_edges}
    after_conf = {f.get("properties", {}).get("id"): f.get("properties", {}).get("confidence")
                  for f in after_edges}
    confidence_deltas = []
    for edge_id, value in after_conf.items():
        try:
            old = float(before_conf[edge_id])
            new = float(value)
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(old) and math.isfinite(new):
            confidence_deltas.append(new - old)
    measured_edges = list(entry.get("approved_edge_ids") or [])
    coverage = {}
    for edge_id in measured_edges:
        values = (flows_after or {}).get(edge_id)
        if isinstance(values, list):
            present = sum(value is not None for value in values)
            coverage[edge_id] = {"intervals": len(values),
                                 "observed_intervals": present,
                                 "coverage_pct": round(100 * present / len(values), 2)
                                 if values else 0.0}

    station_after = _station(loso_after, str(sensor_id))
    own_ratios = []
    for edge in (station_after or {}).get("edges", {}).values():
        try:
            value = float(edge.get("ratio"))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            own_ratios.append(value)
    return {
        "schema_version": 1,
        "sensor_id": str(sensor_id),
        "registry": {
            "quality_status": entry.get("quality_status"),
            "snap_status": entry.get("snap_status"),
            "approved_edge_ids": measured_edges,
            "active_from": entry.get("active_from"),
            "active_to": entry.get("active_to"),
        },
        "coverage": coverage,
        "comparability": comparability,
        "confidence": {
            "edges_compared": len(confidence_deltas),
            "improved_edges": sum(delta > 1e-9 for delta in confidence_deltas),
            "median_delta": (round(median(confidence_deltas), 6)
                              if confidence_deltas else None),
        },
        "holdout": {
            "before_median_abs_log_error": before_error,
            "after_median_abs_log_error": after_error,
            "delta_error_before_minus_after": (before_error - after_error
                                                if before_error is not None and after_error is not None
                                                else None),
            "new_station_ratios": own_ratios,
            "isolation_context": (station_after or {}).get("edges", {}),
        },
        "outcome": outcome,
        "claim": (
            "bidraget är mätt mot jämförbara holdout-artifakter"
            if outcome in {"improved", "neutral", "worsened"}
            else "ingen jämförbar före/efter-evidens; resultatet får inte beskrivas som förbättring"
        ),
    }


def rank_placements(network: dict, *, top_n: int = 20) -> list[dict]:
    """Rank unmeasured edges by measurable information-gap indicators.

    This is a placement *screen*, not a prediction of benefit.  It prefers
    low-confidence/long-distance edges and returns the source values used for
    the ranking so a human can review connectivity and operational feasibility.
    """
    rows = []
    for feature in network.get("features", []):
        props = feature.get("properties", {})
        if props.get("sensor_id"):
            continue
        try:
            confidence = float(props.get("confidence"))
            distance = float(props.get("dist_sensor_m"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(confidence) or not math.isfinite(distance):
            continue
        gap = max(0.0, 1.0 - confidence)
        score = gap * math.log1p(max(0.0, distance))
        rows.append({"edge_id": props.get("id"), "score": round(score, 6),
                     "confidence": confidence, "dist_sensor_m": distance,
                     "reason": "low measured confidence / spatial gap"})
    rows.sort(key=lambda row: (-row["score"], row["edge_id"] or ""))
    return rows[:max(0, int(top_n))]
