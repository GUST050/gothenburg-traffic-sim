#!/usr/bin/env python3
"""Measure dirsplit's magnitude and transferable time-shape evidence.

This is a diagnostic instrument, not Gate M or Gate S.  It answers three
questions from the live, provenance-bound inputs:

* How much measured Level-1 traffic moves relative to flat 50/50, separating
  the transferred model from the local Göteborg directional anchor?
* How large is the unmeasured Level-2/3 opposite-carriageway footprint before
  the PFE relaxation ladder is applied?
* Does a time-of-day direction shape transfer to a held-out city?

The date-level table remains the training/Gate-M source of truth.  Its tracked
aggregate is read only for the explicitly labelled station-hour diagnostic.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gs-dirsplit-mpl")

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demand.intake import load_direction_split  # noqa: E402
from dirsplit.evaluate import (in_deployed_training_window,  # noqa: E402
                               load_rows)


PROTOCOL = "dirsplit_magnitude_shape_diagnostic_v1"
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260814
ARM_KEYS = {
    "q50": "edge_shares",
    "q10_stress": "edge_shares_q10",
    "q90_stress": "edge_shares_q90",
}


@dataclass(frozen=True)
class EvidencePaths:
    flows: Path = ROOT / "web/data/flows.json"
    network: Path = ROOT / "web/data/network.geojson"
    sensors: Path = ROOT / "data_in/sensors.json"
    split: Path = ROOT / "sumo/direction_split.json"
    priors: Path = ROOT / "sumo/prior_flows.json"
    primary_table: Path = ROOT / "data/dirsplit/training_table.csv"
    aggregate_table: Path = ROOT / "data/dirsplit/training_table_aggregated.csv"
    dataset_meta: Path = ROOT / "data/dirsplit/training_table_meta.json"


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read diagnostic input {path}") from error


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _finite_share_series(values: Any, *, context: str) -> list[float]:
    if not isinstance(values, list) or len(values) != 96:
        raise ValueError(f"{context} must contain exactly 96 slots")
    out = []
    for slot, raw in enumerate(values):
        try:
            value = float(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{context}[{slot}] is not numeric") from error
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{context}[{slot}] is not a finite share")
        out.append(value)
    return out


def validate_direction_split(data: Any) -> dict[str, Any]:
    """Validate every station and arm, not just two-way Level-1 sensors."""
    if not isinstance(data, dict) or not data:
        raise ValueError("direction split must be a non-empty object")
    deviations = []
    slots = 0
    for sensor_id, record in sorted(data.items()):
        if not isinstance(record, dict):
            raise ValueError(f"split sensor {sensor_id!r} is not an object")
        for arm, key in ARM_KEYS.items():
            block = record.get(key)
            if not isinstance(block, dict) or len(block) != 2:
                raise ValueError(
                    f"split sensor {sensor_id!r}/{arm} needs exactly two edges")
            series = [
                _finite_share_series(values,
                                     context=f"{sensor_id}/{arm}/{edge_id}")
                for edge_id, values in block.items()
            ]
            for left, right in zip(*series):
                deviations.append(abs(left + right - 1.0))
                slots += 1
    return {
        "sensors": len(data),
        "arms": list(ARM_KEYS),
        "slots_checked": slots,
        "max_abs_pair_sum_deviation": max(deviations),
        "passes_exact_pair_sum": max(deviations) <= 1e-12,
    }


def _registry_rows(data: Any) -> list[dict[str, Any]]:
    rows = data if isinstance(data, list) else data.get("sensors", data)
    if not isinstance(rows, list):
        raise ValueError("sensor registry must contain a sensor list")
    return rows


def _numeric_flow(values: Sequence[Any], *, edge_id: str) -> list[float | None]:
    out: list[float | None] = []
    for index, raw in enumerate(values):
        if raw is None:
            out.append(None)
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"flow {edge_id}[{index}] is not numeric") from error
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"flow {edge_id}[{index}] is not a finite count")
        out.append(value)
    return out


def measured_mass(flows: Mapping[str, Sequence[Any]],
                  registry_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count each physical measurement once; Total twins duplicate a total."""
    by_sensor: dict[str, float] = {}
    semantics: dict[str, str] = {}
    for row in registry_rows:
        sensor_id = str(row.get("sensor_id"))
        edges = [str(edge) for edge in row.get("approved_edge_ids", [])]
        if not sensor_id or sensor_id == "None" or not edges:
            continue
        series = []
        for edge in edges:
            if edge not in flows:
                raise ValueError(f"registered measured edge {edge} has no flow")
            series.append(_numeric_flow(flows[edge], edge_id=edge))
        measurement_semantics = str(row.get("measurement_semantics"))
        if measurement_semantics == "two_way_total":
            if len(series) != 2:
                raise ValueError(f"two-way sensor {sensor_id} needs two edges")
            if series[0] != series[1]:
                raise ValueError(
                    f"two-way sensor {sensor_id} twins do not carry one total")
            chosen = series[0]
        elif measurement_semantics == "directional":
            if len(series) != 1:
                raise ValueError(
                    f"directional sensor {sensor_id} needs one measured edge")
            chosen = series[0]
        else:
            raise ValueError(
                f"sensor {sensor_id} has unknown measurement semantics")
        by_sensor[sensor_id] = sum(value for value in chosen if value is not None)
        semantics[sensor_id] = measurement_semantics
    total = sum(by_sensor.values())
    if total <= 0:
        raise ValueError("measured mass is empty")
    return {
        "total": total,
        "by_sensor": by_sensor,
        "semantics": semantics,
        "deduplication": (
            "each directional station is counted once; the identical flow "
            "copied onto both edges of a two-way Total sensor is counted once"
        ),
    }


def moved_vehicles(flow: Sequence[float | None], shares: Sequence[float],
                   baseline: float | Sequence[float] = 0.5) -> float:
    if len(shares) != 96:
        raise ValueError("a direction share profile must contain 96 slots")
    base = ([float(baseline)] * 96 if isinstance(baseline, (int, float))
            else [float(value) for value in baseline])
    if len(base) != 96:
        raise ValueError("a baseline share profile must contain 96 slots")
    return sum(
        value * abs(share - base[slot % 96])
        for slot, (value, share) in enumerate(
            zip(flow, itertools.cycle(shares)))
        if value is not None
    )


def weighted_share(flow: Sequence[float | None], shares: Sequence[float]) -> float:
    numerator = denominator = 0.0
    for slot, (value, share) in enumerate(zip(flow, itertools.cycle(shares))):
        if value is None:
            continue
        numerator += value * share
        denominator += value
    if denominator <= 0:
        raise ValueError("cannot weight a split by an empty flow")
    return numerator / denominator


def _edge_arm(split: Mapping[str, Any], sensor_id: str, key: str,
              edge_id: str) -> list[float]:
    try:
        block = split[sensor_id][key]
        values = block[edge_id]
    except (KeyError, TypeError) as error:
        raise ValueError(
            f"split has no {sensor_id}/{key}/{edge_id} series") from error
    return _finite_share_series(values,
                                context=f"{sensor_id}/{key}/{edge_id}")


def level1_magnitude(*, flows: Mapping[str, Sequence[Any]],
                     registry_rows: Sequence[Mapping[str, Any]],
                     split: Mapping[str, Any], measured_total: float,
                     active_anchor_day: str, epoch: Any) -> dict[str, Any]:
    raw: dict[str, float] = {name: 0.0 for name in ARM_KEYS}
    raw_max = {name: 0.0 for name in ARM_KEYS}
    active_q50_moved = 0.0
    active_shape_only_moved = 0.0
    active_means: dict[str, float] = {}
    two_way_mass = 0.0

    active = load_direction_split(
        "edge_shares", anchor_day=active_anchor_day,
        anchor_flows=flows, anchor_epoch=epoch)

    for row in registry_rows:
        if row.get("measurement_semantics") != "two_way_total":
            continue
        sensor_id = str(row["sensor_id"])
        edges = [str(edge) for edge in row.get("approved_edge_ids", [])]
        if len(edges) != 2:
            raise ValueError(f"two-way sensor {sensor_id} needs two edges")
        measured_edge = edges[0]
        flow = _numeric_flow(flows[measured_edge], edge_id=measured_edge)
        sensor_mass = sum(value for value in flow if value is not None)
        two_way_mass += sensor_mass
        for arm, key in ARM_KEYS.items():
            shares = _edge_arm(split, sensor_id, key, measured_edge)
            raw[arm] += moved_vehicles(flow, shares)
            raw_max[arm] = max(raw_max[arm],
                               max(abs(value - 0.5) for value in shares))

        active_shares = _finite_share_series(
            active[measured_edge], context=f"active/{sensor_id}/{measured_edge}")
        mean = weighted_share(flow, active_shares)
        active_means[sensor_id] = mean
        active_q50_moved += moved_vehicles(flow, active_shares)
        # Same aggregate level, flat over time: isolates the model's shape.
        active_shape_only_moved += moved_vehicles(flow, active_shares, mean)

    def result(value: float) -> dict[str, float]:
        return {
            "reallocated_vehicles": round(value, 3),
            "pct_of_all_measured_mass": round(100.0 * value / measured_total, 6),
        }

    return {
        "two_way_level1_mass": two_way_mass,
        "two_way_pct_of_measured_mass": round(
            100.0 * two_way_mass / measured_total, 6),
        "raw_transferred_policy_vs_flat_5050": {
            arm: {**result(value),
                  "max_abs_share_deviation_from_5050": round(raw_max[arm], 6)}
            for arm, value in raw.items()
        },
        "active_q50_with_local_anchor_vs_flat_5050": result(active_q50_moved),
        "active_q50_shape_vs_flat_same_local_level": result(
            active_shape_only_moved),
        "active_volume_weighted_share_by_sensor": active_means,
        "anchor_day": active_anchor_day,
        "interpretation": (
            "raw_transferred_policy excludes local directional references; "
            "active_q50 includes them. The shape-only comparison holds each "
            "active sensor's volume-weighted level fixed."
        ),
    }


def level23_footprint(*, flows: Mapping[str, Sequence[Any]],
                      registry_rows: Sequence[Mapping[str, Any]],
                      split: Mapping[str, Any]) -> dict[str, Any]:
    """Annualise the implicit opposite flow before any PFE relaxation."""
    central = low = high = 0.0
    measured_mass = 0.0
    by_sensor: dict[str, Any] = {}
    for row in registry_rows:
        spec = row.get("opposite_direction") or {}
        edges = [str(edge) for edge in row.get("approved_edge_ids", [])]
        opposite = spec.get("edge_id")
        if not opposite or len(edges) != 1:
            continue
        sensor_id = str(row["sensor_id"])
        measured_edge = edges[0]
        flow = _numeric_flow(flows[measured_edge], edge_id=measured_edge)
        arms = {
            name: _edge_arm(split, sensor_id, key, measured_edge)
            for name, key in ARM_KEYS.items()
        }
        totals = {name: 0.0 for name in arms}
        sensor_low = sensor_high = 0.0
        for index, value in enumerate(flow):
            if value is None:
                continue
            estimates: dict[str, float] = {}
            for name, shares in arms.items():
                share = shares[index % 96]
                if not 0.0 < share < 1.0:
                    raise ValueError(
                        f"{sensor_id}/{name} cannot imply an opposite flow")
                estimate = value * (1.0 - share) / share
                totals[name] += estimate
                estimates[name] = estimate
            stress = [estimates["q10_stress"], estimates["q90_stress"]]
            sensor_low += min(stress)
            sensor_high += max(stress)
        mass = sum(value for value in flow if value is not None)
        measured_mass += mass
        central += totals["q50"]
        low += sensor_low
        high += sensor_high
        by_sensor[sensor_id] = {
            "measured_edge": measured_edge,
            "opposite_edge": str(opposite),
            "measured_annual_mass": mass,
            "implicit_opposite_q50": round(totals["q50"], 3),
            "stress_envelope_low": round(sensor_low, 3),
            "stress_envelope_high": round(sensor_high, 3),
        }
    return {
        "single_direction_measured_mass": measured_mass,
        "implicit_opposite_q50_annualised": round(central, 3),
        "stress_envelope_low_annualised": round(low, 3),
        "stress_envelope_high_annualised": round(high, 3),
        "stress_envelope_width_annualised": round(high - low, 3),
        "by_sensor": by_sensor,
        "authority": (
            "q50 is a soft Level-3 prior; the stress envelope contributes a "
            "zero-to-upper Level-2 ceiling. These are pre-PFE quantities, not "
            "realised simulated traffic, and the ceiling/priors yield before "
            "the measured Level-1 band is widened."
        ),
    }


def _shape_rows(path: Path):
    rows, meta = load_rows(path)
    supported = [row for row in rows if in_deployed_training_window(row)]
    if not supported:
        raise ValueError("aggregate table has no supported diagnostic rows")
    return supported, meta


def level_shape_decomposition(rows) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    station_ids = sorted({row.station_id for row in rows})
    means = {
        station: float(np.mean([row.share for row in rows
                                if row.station_id == station]))
        for station in station_ids
    }
    level = np.array([means[row.station_id] - 0.5 for row in rows])
    shape = np.array([row.share - means[row.station_id] for row in rows])
    total = np.array([row.share - 0.5 for row in rows])
    denominator = float(np.sum(total ** 2))
    if denominator <= 0:
        raise ValueError("direction shares contain no variation")
    if not np.allclose(level + shape, total, rtol=0.0, atol=1e-12):
        raise AssertionError("level/shape decomposition is not exact")
    return level, shape, means


def leave_city_hour_shape(rows, shape: np.ndarray) -> np.ndarray:
    predictions = np.zeros(len(rows), dtype=float)
    for held_city in sorted({row.city for row in rows}):
        train_indices = [i for i, row in enumerate(rows)
                         if row.city != held_city]
        hourly = {
            hour: float(np.mean([shape[i] for i in train_indices
                                 if rows[i].hour == hour]))
            for hour in range(6, 21)
        }
        for index, row in enumerate(rows):
            if row.city == held_city:
                predictions[index] = hourly[row.hour]
    return predictions


def leave_city_lgbm_shape(rows, shape: np.ndarray) -> np.ndarray:
    try:
        import lightgbm as lgb
    except ImportError as error:
        raise RuntimeError("LightGBM is required for the shape diagnostic") \
            from error

    matrix = np.array([
        list(row.features) + list(row.profile) + [
            math.sin(2 * math.pi * row.hour / 24),
            math.cos(2 * math.pi * row.hour / 24),
            float(row.is_weekend),
        ]
        for row in rows
    ], dtype=float)
    cities = np.array([row.city for row in rows])
    predictions = np.zeros(len(rows), dtype=float)
    for held_city in sorted(set(cities)):
        train = cities != held_city
        test = ~train
        model = lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, max_depth=5,
            num_leaves=31, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=20, random_state=42, n_jobs=1, verbose=-1)
        model.fit(matrix[train], shape[train])
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="X does not have valid feature names, but .*",
                category=UserWarning)
            predictions[test] = model.predict(matrix[test])
    return predictions


def station_block_bootstrap(diff: np.ndarray, station_ids: Sequence[str], *,
                            samples: int = BOOTSTRAP_SAMPLES,
                            seed: int = BOOTSTRAP_SEED) -> list[float]:
    if len(diff) != len(station_ids) or not len(diff):
        raise ValueError("bootstrap needs aligned, non-empty observations")
    stations = sorted(set(station_ids))
    ids = np.asarray(station_ids)
    block_sums = np.array([float(np.sum(diff[ids == station]))
                           for station in stations])
    block_counts = np.array([int(np.sum(ids == station))
                             for station in stations])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(stations), size=(samples, len(stations)))
    estimates = block_sums[draws].sum(axis=1) / block_counts[draws].sum(axis=1)
    return [float(value) for value in np.quantile(estimates, [0.025, 0.975])]


def shape_transfer(path: Path) -> dict[str, Any]:
    rows, meta = _shape_rows(path)
    level, shape, _means = level_shape_decomposition(rows)
    total = level + shape
    level_fraction = float(np.sum(level ** 2) / np.sum(total ** 2))
    shape_fraction = float(np.sum(shape ** 2) / np.sum(total ** 2))

    flat = np.zeros(len(rows), dtype=float)
    hourly = leave_city_hour_shape(rows, shape)
    lgbm = leave_city_lgbm_shape(rows, shape)
    errors = {
        "flat_no_shape": np.abs(flat - shape),
        "leave_city_hour_lookup_15_values": np.abs(hourly - shape),
        "leave_city_lgbm_19_features": np.abs(lgbm - shape),
    }
    mae = {name: float(np.mean(values)) for name, values in errors.items()}
    paired = errors["leave_city_hour_lookup_15_values"] - errors["flat_no_shape"]
    ci = station_block_bootstrap(
        paired, [row.station_id for row in rows])
    passes = ci[1] < 0.0

    hour_profile = {
        str(hour): round(float(np.mean([shape[i] for i, row in enumerate(rows)
                                        if row.hour == hour])), 6)
        for hour in range(6, 21)
    }
    return {
        "source": str(path.relative_to(ROOT)),
        "source_role": "diagnostic aggregate only; not used by Gate M/training",
        "raw_rows": meta.get("raw_rows"),
        "screened_rows": meta.get("rows_kept"),
        "deployment_window_rows": len(rows),
        "stations": len({row.station_id for row in rows}),
        "cities": sorted({row.city for row in rows}),
        "level_variance_fraction": round(level_fraction, 6),
        "shape_variance_fraction": round(shape_fraction, 6),
        "mean_shape_by_hour": hour_profile,
        "leave_city_out_mae": {name: round(value, 8)
                               for name, value in mae.items()},
        "hour_lookup_improvement_vs_flat_pct": round(
            100.0 * (1.0 - mae["leave_city_hour_lookup_15_values"] /
                     mae["flat_no_shape"]), 4),
        "lgbm_improvement_vs_flat_pct": round(
            100.0 * (1.0 - mae["leave_city_lgbm_19_features"] /
                     mae["flat_no_shape"]), 4),
        "hour_lookup_paired_station_block_bootstrap": {
            "difference": "hour_lookup_absolute_error - flat_absolute_error",
            "samples": BOOTSTRAP_SAMPLES,
            "seed": BOOTSTRAP_SEED,
            "ci95": [round(value, 8) for value in ci],
            "significant_win": passes,
        },
        "decision": ("CANDIDATE" if passes else "NEGATIVE_EVIDENCE"),
        "decision_text": (
            "A level/shape product candidate is not justified unless the "
            "station-blocked leave-city-out confidence interval is wholly "
            "below zero."
        ),
    }


def _validate_dataset_binding(paths: EvidencePaths) -> dict[str, Any]:
    meta = _json(paths.dataset_meta)
    primary_sha = sha256_file(paths.primary_table)
    aggregate_sha = sha256_file(paths.aggregate_table)
    if meta.get("primary_table_sha256") != primary_sha:
        raise ValueError("primary table does not match dataset manifest")
    if meta.get("aggregate_diagnostic_sha256") != aggregate_sha:
        raise ValueError("aggregate diagnostic does not match dataset manifest")
    with paths.primary_table.open(newline="") as handle:
        primary_rows = sum(1 for _ in csv.reader(handle)) - 1
    with paths.aggregate_table.open(newline="") as handle:
        aggregate_rows = sum(1 for _ in csv.reader(handle)) - 1
    if primary_rows != meta.get("rows"):
        raise ValueError("primary row count does not match dataset manifest")
    return {
        "protocol": meta.get("protocol"),
        "primary_rows": primary_rows,
        "primary_usable_rows": meta.get("usable_rows"),
        "aggregate_raw_rows": aggregate_rows,
        "source_volume_files": len(meta.get("source_volume_files") or []),
        "day_blocks": meta.get("day_blocks"),
        "source_content_key": meta.get("source_content_key"),
        "note": meta.get("note"),
    }


def build_evidence(paths: EvidencePaths = EvidencePaths(), *,
                   anchor_day: str = "2025-09-16") -> dict[str, Any]:
    dataset = _validate_dataset_binding(paths)
    flows_payload = _json(paths.flows)
    flows = flows_payload.get("flows")
    if not isinstance(flows, dict):
        raise ValueError("flows input has no flow mapping")
    registry_rows = _registry_rows(_json(paths.sensors))
    split = _json(paths.split)
    pair_sum = validate_direction_split(split)
    if not pair_sum["passes_exact_pair_sum"]:
        raise ValueError("direction split is not pair-sum preserving")
    mass = measured_mass(flows, registry_rows)

    source_paths = {
        "flows": paths.flows,
        "network": paths.network,
        "sensors": paths.sensors,
        "direction_split": paths.split,
        "prior_flows": paths.priors,
        "primary_table": paths.primary_table,
        "aggregate_diagnostic": paths.aggregate_table,
        "dataset_meta": paths.dataset_meta,
        "measurement_script": Path(__file__).resolve(),
    }
    payload: dict[str, Any] = {
        "protocol": PROTOCOL,
        "recorded": date.today().isoformat(),
        "release_evidence": False,
        "decision_authority": "diagnostic_only",
        "reproduction_command": (
            "MPLCONFIGDIR=/tmp/gs-dirsplit-mpl PYTHONDONTWRITEBYTECODE=1 "
            "python3 tools/measure_dirsplit_magnitude_shape.py "
            "--emit-evidence "
            "validation/dirsplit_magnitude_shape_diagnostic_v1.json"
        ),
        "inputs": {
            name: {"path": str(path.relative_to(ROOT)),
                   "sha256": sha256_file(path)}
            for name, path in source_paths.items()
        },
        "dataset": dataset,
        "pair_sum_validation": pair_sum,
        "measured_mass": mass,
        "level1": level1_magnitude(
            flows=flows, registry_rows=registry_rows, split=split,
            measured_total=mass["total"], active_anchor_day=anchor_day,
            epoch=flows_payload.get("epoch")),
        "level23": level23_footprint(
            flows=flows, registry_rows=registry_rows, split=split),
        "shape_transfer": shape_transfer(paths.aggregate_table),
        "claim_boundary": (
            "This artifact measures magnitude and a rejected shape candidate. "
            "It does not replace Gate M, calibrate q10/q90 probabilities, "
            "measure realised PFE opposite flow, or decide closure sensitivity."
        ),
    }
    payload["content_key"] = content_digest(payload)
    return payload


def emit_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--emit-evidence", type=Path)
    parser.add_argument("--anchor-day", default="2025-09-16")
    args = parser.parse_args(argv)
    evidence = build_evidence(anchor_day=args.anchor_day)
    if args.emit_evidence:
        output = args.emit_evidence
        if not output.is_absolute():
            output = ROOT / output
        emit_evidence(output, evidence)
        print(f"Wrote {output.relative_to(ROOT)}")
    print(json.dumps({
        "content_key": evidence["content_key"],
        "level1": evidence["level1"],
        "level23": evidence["level23"],
        "shape_transfer": evidence["shape_transfer"],
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
