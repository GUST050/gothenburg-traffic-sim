"""
Confidence-stage core — leave-one-station-out (LOSO) validation.

Run after build_sumo_demand (needs candidates + priors on disk):
  python3 validate_sim.py

For every station: rebuild the demand WITHOUT that station's measurements
(and without the priors derived from them), simulate, and compare the
simulated flows at the held-out edges against what the station actually
measured. This answers, empirically, the product's core honesty question:
"how wrong is the program on a street it cannot see?"

Level-2 bounds are excluded in all folds (they are derived from the full
measurement set and would leak the held-out station). Level 3 keeps only
priors NOT derived from the held-out station.

FIXED 2026-07-09 (found while auditing the whole codebase, confirmed still
open per ARCHITECTURE.md's own "KNOWN GAP" note): corridor_priors (Agent
B's "sensors helping each other" — same-direction station pairs linked by
a short path bound the edges BETWEEN them, see observability.corridor_
priors) were computed and used by the real, deployed build_sumo_demand.py
pipeline, but never wired into THIS validation script — meaning every LOSO
figure on record understated the deployed system's actual recovery. The
mechanism itself was already fully general (it scans every PAIR of
currently-measured sensors, no hardcoded IDs — new stations get corridor
priors automatically, no code changes), so this was a validation-accuracy
gap only, not a scalability one. A corridor prior is excluded from a
station's own LOSO fold whenever EITHER of its two anchor sensors is the
held-out one (same leakage-prevention principle already applied to
prior_flows.json's direction priors below).

Writes web/data/loso_report.json.
"""

from __future__ import annotations

import argparse

from collections import Counter
from datetime import date as calendar_date
import json
import multiprocessing as mp
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

import assignment_priors as assignment_prior_model
from assignment_priors import (DEFAULT_N_SAMPLES, calibrate_assignment_priors,
                               compute_assignment_load)
from traffic_sim.demand import pfe
from build_sumo_demand import (GEO_PATH, build_targets, ensure_observability,
                               load_edge_geometry, load_sensor_edges,
                               structure_groups_for_shapes)
from demand.intake import (activity_purpose_shares_for_window, classify_day,
                           observed_sensor_series)
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.confidence import picker_diagnostics, route_support
from traffic_sim.confidence.controlled_rounding import (
    exact_condensed_contract_repair,
)
from traffic_sim.intake.sensors import load_registry
from traffic_sim.simulation.runtime import sumo_home

SUMO_DIR = Path("sumo")
EPOCH    = pd.Timestamp("2025-01-01")
MIN_TEMPORAL_COVERAGE = 0.90


def load_inputs():
    with open("web/data/flows.json") as f:
        flows = json.load(f)["flows"]
    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    with open(SUMO_DIR / "prior_flows.json") as f:
        priors = json.load(f)["edges"]
    corridor = ensure_observability().get("corridor_priors", {})
    return flows, meta, priors, corridor


def compute_assignment_load_for_fold(
    held: str,
    sensor_edges: dict[str, list[str]],
    assignment_config: dict | None = None,
) -> dict[str, float]:
    """Build the structural field with ``held`` treated as unmeasured.

    Production excludes every measured edge from possible synthetic endpoints.
    The old LOSO code computed that production field once and only removed the
    held station from the later scale fit.  Its edge therefore remained absent
    from the field and received no assignment ceiling: the fold made it freer
    than a genuinely unmeasured street.

    Keep this validation-only so measuring the corrected experiment does not
    move the annual warming plan's production-source seal.  ``compute_assignment_
    load`` reads ``assignment_priors.load_sensor_edges`` once at construction;
    replacing that lookup for this synchronous call gives it the exact registry
    production would have seen had the station never existed.  Restoration in a
    ``finally`` block makes the scoped override fail closed.
    """
    held = str(held)
    if held not in sensor_edges:
        raise ValueError(f"held-out sensor is unknown: {held}")
    fold_sensors = {
        str(sensor): list(edges)
        for sensor, edges in sensor_edges.items()
        if str(sensor) != held
    }
    original = assignment_prior_model.load_sensor_edges
    assignment_prior_model.load_sensor_edges = lambda: fold_sensors
    try:
        return compute_assignment_load(**(assignment_config or {}))
    finally:
        assignment_prior_model.load_sensor_edges = original


def include_held_assignment_flows(
    calibrated: dict,
    load: dict[str, float],
    released_edges: list[str],
) -> dict:
    """Add held-sensor-covered edges to the fold's structural field.

    ``calibrate_assignment_priors`` deliberately omits all registered sensor
    and prior-covered edges from its published production field. In this fold,
    constraints derived from the held sensor are absent, so format those edges'
    load with the same scale and daily shape used for every other unmeasured
    edge. No held-out count enters either the load or the scale.
    """
    out = dict(calibrated)
    flows = dict(calibrated.get("flows", {}))
    scale = calibrated.get("scale_veh_per_day")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) \
            or not np.isfinite(scale) or scale < 0:
        raise ValueError("fold assignment scale is invalid")
    shape96 = assignment_prior_model.daily_shape()
    for edge in released_edges:
        edge_load = float(load.get(edge, 0.0))
        if edge_load <= 0:
            continue
        daily = float(scale) * edge_load
        flows[edge] = [round(daily * float(slot), 2) for slot in shape96]
    out["flows"] = flows
    return out


def assignment_edges_released_for_fold(
    held: str,
    held_edges: list[str],
    direction_priors: dict[str, dict],
    corridor_priors: dict[str, dict],
    edge_to_sensor: dict[str, str],
) -> list[str]:
    """Edges whose measurement-derived coverage disappears in this fold."""
    held = str(held)
    released = set(held_edges)
    released.update(
        edge for edge, prior in direction_priors.items()
        if str(prior.get("sensor")) == held
    )
    for edge, prior in corridor_priors.items():
        anchors = {
            edge_to_sensor.get(prior.get("from_sensor_edge")),
            edge_to_sensor.get(prior.get("to_sensor_edge")),
        }
        if held in anchors:
            released.add(edge)
    return sorted(released)


def run_meso(route_file: Path, ed_file: Path, duration_s: int) -> None:
    home = sumo_home()
    add = SUMO_DIR / "loso_edgedata.add.xml"
    with open(add, "w") as f:
        f.write(f'<additional><edgeData id="ed" file="{ed_file.name}" '
                f'period="900" begin="0" end="{duration_s}" '
                f'excludeEmpty="true"/></additional>\n')
    cmd = [str(home / "bin" / "sumo"),
           "--mesosim", "true", "--meso-junction-control", "true",
           "--meso-junction-control.limited", "true",
           "-n", str((SUMO_DIR / "net.net.xml").resolve()),
           "-r", str(route_file.resolve()), "-a", str(add.resolve()),
           "--begin", "0", "--end", str(duration_s + 3600),
           "--no-step-log", "true", "--no-warnings", "true",
           "--ignore-route-errors", "true", "--seed", "1000"]
    # LOSO loops one sumo run per station unattended — a hang on one station
    # must not stall the whole run with no diagnostic. Same reasoning as
    # run_scenario.py's SUMO_TIMEOUT_S, found in the same review pass.
    subprocess.run(cmd, cwd=str(SUMO_DIR), capture_output=True, text=True,
                   check=True, timeout=300)


def corridor_priors_for_fold(corridor: dict, edge_to_sensor: dict[str, str],
                            held: str, qi: int) -> dict[str, tuple[float, float]]:
    """This fold's corridor-derived priors {edge: (value, weight)} — every
    corridor entry anchored on the held-out station (from OR to) is
    excluded, since it blends in that station's own measurement."""
    out: dict[str, tuple[float, float]] = {}
    for e, d in corridor.items():
        from_sensor = edge_to_sensor.get(d["from_sensor_edge"])
        to_sensor = edge_to_sensor.get(d["to_sensor_edge"])
        if from_sensor is None or to_sensor is None:
            raise ValueError(
                f"corridor prior {e} references non-sensor anchor(s): "
                f"{d['from_sensor_edge']}->{from_sensor}, "
                f"{d['to_sensor_edge']}->{to_sensor}"
            )
        if from_sensor == held or to_sensor == held:
            continue
        if qi >= len(d["prior"]) or d["prior"][qi] is None:
            continue
        band = d["band"][qi] or 8.0
        out[e] = (float(d["prior"][qi]), 1.0 / max(1.0, band))
    return out


_PFE_PAR_SHAPES = None
_PFE_PAR_ROUTE_COST = None
_PFE_PAR_STRUCTURE_GROUPS = None
_PFE_PAR_PURPOSE_MIXES = None


def _run_pfe_interval_job(job: dict):
    """ProcessPool worker for one independent quarter PFE solve.

    Delegates to the SAME pfe.solve_interval_with_structure_guard the
    deployed pipeline uses — LOSO must calibrate with the same constraint
    set as the system that ships, or it validates a different one (the
    exact mismatch class this project already fixed once for the meso
    junction-control config)."""
    if (_PFE_PAR_SHAPES is None or _PFE_PAR_ROUTE_COST is None
            or _PFE_PAR_PURPOSE_MIXES is None):
        raise RuntimeError("PFE interval worker was not initialized")
    sol, rung = pfe.solve_interval_with_structure_guard(
        _PFE_PAR_SHAPES,
        job["targets"],
        job["bounds"],
        job["priors"],
        route_cost=_PFE_PAR_ROUTE_COST,
        structure_groups=_PFE_PAR_STRUCTURE_GROUPS,
        purpose_mix=_PFE_PAR_PURPOSE_MIXES[job["quarter"]],
    )
    return job["quarter"], sol, rung


def controlled_publication_counts(
    shapes: list,
    solutions: list[np.ndarray | None],
    targets_per_q: list[dict[str, float]],
    bounds_per_q: list[dict[str, tuple[float, float]]],
    rungs: list[int],
    purpose_mixes_per_q: list,
    *,
    enforce_integer_bounds: bool,
    structure_groups: list[tuple[list[int], float]],
    stability_edges: list[str] | None = None,
) -> tuple[list[tuple[np.ndarray, bool] | None], dict]:
    """Precompute LOSO counts under the production publication contract.

    LOSO adds one narrow exact acceleration for its validation-only inactive
    sensor equalities: the unrestricted integer L1 model is algebraically
    condensed without changing its domain or objective. If it cannot prove an
    optimum, the untouched production general MILP is the fail-closed fallback.
    """
    precomputed: list[tuple[np.ndarray, bool] | None] = []
    residuals: list[int] = []
    route_l1 = 0.0
    inconsistent_quarters = 0
    processed = 0
    method_counts: Counter = Counter()
    production_repair = pfe.repair_integer_bounds

    def validation_repair(*args, **kwargs):
        accelerated = exact_condensed_contract_repair(*args, **kwargs)
        if accelerated is not None:
            repaired, metadata = accelerated
            method_counts[metadata["method"]] += 1
            return repaired
        method_counts["production_general_fallback"] += 1
        return production_repair(*args, **kwargs)

    # quarter_publish_counts resolves this symbol at call time. Scope the
    # override to the serial LOSO publication loop and restore it on failures.
    pfe.repair_integer_bounds = validation_repair
    try:
        for quarter, solution in enumerate(solutions):
            if solution is None:
                precomputed.append(None)
                continue
            purpose_mix = purpose_mixes_per_q[quarter]
            result = pfe.quarter_publish_counts(
                shapes,
                solution,
                targets_per_q[quarter],
                bounds_per_q[quarter],
                rungs[quarter],
                purpose_mix,
                bool(purpose_mix),
                enforce_integer_bounds,
                structure_groups,
                stability_edges,
            )
            precomputed.append(result)
            counts, _purpose_enforced = result
            quarter_residuals = []
            for edge, target in targets_per_q[quarter].items():
                touching = [j for j, shape in enumerate(shapes)
                            if edge in shape.edges]
                if not touching:
                    continue
                residual = abs(int(counts[touching].sum()) - int(round(target)))
                residuals.append(residual)
                quarter_residuals.append(residual)
            inconsistent_quarters += int(any(quarter_residuals))
            route_l1 += float(np.abs(counts.astype(float) - solution).sum())
            processed += 1
    finally:
        pfe.repair_integer_bounds = production_repair
    return precomputed, {
        "evidence_class": "validation_exact_equivalent_projection",
        "method_counts": dict(sorted(method_counts.items())),
        "quarters_processed": processed,
        "quarters_with_inconsistent_integer_margins": inconsistent_quarters,
        "max_abs_active_measurement_residual": max(residuals, default=0),
        "route_l1_from_continuous_total": round(route_l1, 6),
    }


def calibrate_fold_parallel(
    cand_path: Path,
    out_path: Path,
    targets: list[dict[str, float]],
    bounds_pq: list[dict[str, tuple[float, float]]],
    priors_pq: list[dict[str, tuple[float, float]]],
    max_workers: int | None = None,
    purpose_departure_offset_s: float = 0.0,
    activity_purpose_shares_by_quarter: list[dict[str, float]] | None = None,
    through_share_target: float | None = None,
    integer_bounds_pq: list[dict[str, tuple[float, float]]] | None = None,
    picker_diagnostics_path: Path | None = None,
    picker_diagnostics_context: dict | None = None,
    legal_movements: list[dict] | None = None,
    controlled_integer_rounding: bool = False,
    stability_edges: list[str] | None = None,
    observability_groups: dict[str, list[str]] | None = None,
) -> dict:
    """Solve this LOSO fold through one flat per-quarter worker pool."""
    global _PFE_PAR_SHAPES, _PFE_PAR_ROUTE_COST, _PFE_PAR_STRUCTURE_GROUPS
    global _PFE_PAR_PURPOSE_MIXES
    shapes, route_cost = pfe.prepare_calibration(cand_path)
    _PFE_PAR_SHAPES = shapes
    _PFE_PAR_ROUTE_COST = route_cost
    # Same structure-preservation groups as the deployed build (set BEFORE
    # the pool forks so workers inherit them). These depend only on route
    # geometry, never on which sensor a fold holds out — no leakage.
    _PFE_PAR_STRUCTURE_GROUPS = structure_groups_for_shapes(shapes)
    # Same enforced through-share level as the deployed build (read from
    # demand_meta by the caller) — LOSO must validate the configuration
    # that ships, and this level was itself SELECTED by LOSO + confirmed
    # on a second day (see pfe.apply_through_share_target).
    source_mixes = pfe._purpose_targets_per_quarter(
        shapes, len(targets), purpose_departure_offset_s)
    _PFE_PAR_PURPOSE_MIXES = pfe.apply_through_share_target(
        pfe.apply_category_margin(
            source_mixes, activity_purpose_shares_by_quarter),
        through_share_target)
    try:
        tasks = [
            {
                "quarter": i,
                "targets": targets[i],
                "bounds": bounds_pq[i],
                "priors": priors_pq[i],
            }
            for i in range(len(targets))
        ]
        solutions = [None] * len(targets)
        rungs = [pfe.RUNG_INFEASIBLE] * len(targets)
        n_workers = min(max_workers or (os.cpu_count() or 1), len(tasks))
        print(f"  PFE LOSO fold: solving {len(tasks)} independent quarter "
              f"intervals in one pool ({n_workers} workers)")
        with mp.get_context("fork").Pool(processes=n_workers) as pool:
            for quarter, sol, rung in pool.imap_unordered(_run_pfe_interval_job, tasks):
                solutions[quarter] = sol
                rungs[quarter] = rung
        # edge_length_m: LOSO stamps purposes with the same length-aware
        # allocation as the deployed build (validated == shipped).
        # The continuous fold solves against all assignment-field bounds, but
        # integer publication enforces only the explicitly selected held-edge
        # ceilings. Enforcing every wide assignment bound would change LOSO's
        # historical model; enforcing none lets measurement-preserving
        # rounding and optional structure repair multiply held-out flow after
        # the solver has respected its ceiling.
        publication_bounds = (
            integer_bounds_pq if integer_bounds_pq is not None else bounds_pq)
        precomputed_counts, controlled_rounding_report = (
            controlled_publication_counts(
                shapes,
                solutions,
                targets,
                publication_bounds,
                rungs,
                _PFE_PAR_PURPOSE_MIXES,
                enforce_integer_bounds=integer_bounds_pq is not None,
                structure_groups=[
                    (members, cap_share)
                    for _name, members, cap_share
                    in (_PFE_PAR_STRUCTURE_GROUPS or [])
                ],
                stability_edges=stability_edges,
            )
        )
        calibration_report = pfe.write_calibration_report(
            shapes, out_path, targets, solutions, publication_bounds, rungs,
            enforce_integer_bounds=integer_bounds_pq is not None,
            structure_groups=[(members, cap_share) for _n, members, cap_share
                              in (_PFE_PAR_STRUCTURE_GROUPS or [])],
            edge_length_m=load_edge_geometry()[2] if GEO_PATH.exists() else None,
            purpose_mixes_per_q=_PFE_PAR_PURPOSE_MIXES,
            precomputed_counts=precomputed_counts,
            stability_edges=stability_edges,
            observability_groups=observability_groups,
            required_anchor_edges=stability_edges)
        calibration_report["controlled_integer_rounding"] = (
            controlled_rounding_report)
        if picker_diagnostics_path is not None:
            context = dict(picker_diagnostics_context or {})
            diagnostic = picker_diagnostics.build_fold_diagnostics(
                shapes,
                route_cost,
                targets,
                bounds_pq,
                priors_pq,
                solutions,
                rungs,
                _PFE_PAR_STRUCTURE_GROUPS or [],
                _PFE_PAR_PURPOSE_MIXES,
                held_station=str(context.pop("held_station")),
                held_component_edges=context.pop("held_component_edges"),
                assignment_ceiling_edges=context.pop(
                    "assignment_ceiling_edges"),
                legal_movements=legal_movements or [],
                source=context,
            )
            picker_diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = picker_diagnostics_path.with_suffix(
                picker_diagnostics_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True,
                           allow_nan=False) + "\n",
                encoding="utf-8")
            os.replace(temporary, picker_diagnostics_path)
        return calibration_report
    finally:
        _PFE_PAR_SHAPES = None
        _PFE_PAR_ROUTE_COST = None
        _PFE_PAR_STRUCTURE_GROUPS = None
        _PFE_PAR_PURPOSE_MIXES = None


def simulated_series(ed_file: Path, edge: str, nq: int) -> np.ndarray:
    out = np.zeros(nq)
    for iv in ET.parse(ed_file).getroot().findall("interval"):
        i = int(float(iv.get("begin")) // 900)
        if i >= nq:
            continue
        for e in iv.findall("edge"):
            if e.get("id") == edge:
                out[i] = float(e.get("entered") or 0)
    return out


def held_out_evaluation_series(
    measured_by_edge: dict[str, list[float | None] | np.ndarray],
    simulated_by_edge: dict[str, list[float] | np.ndarray],
    held_edges: list[str],
    measurement_semantics: str,
) -> tuple[str, np.ndarray, np.ndarray]:
    """Return the independently observable series for one held station.

    A ``two_way_total`` station supplies one physical count duplicated under
    its two directed network edges.  The direction split used by calibration
    is estimated, not observed, so LOSO must compare that raw total once with
    the sum of the simulated directions.  Scoring each estimated direction
    separately confounds assignment of direction with recovery of station
    flow and can report two opposite errors from a correct total.
    """
    if not held_edges:
        raise ValueError("held-out station has no resolved edges")
    missing_measured = sorted(set(held_edges) - set(measured_by_edge))
    missing_simulated = sorted(set(held_edges) - set(simulated_by_edge))
    if missing_measured or missing_simulated:
        raise ValueError(
            "held-out evaluation series are missing edges: measured="
            f"{missing_measured}, simulated={missing_simulated}")

    measured = {
        edge: np.asarray(measured_by_edge[edge], dtype=float)
        for edge in held_edges
    }
    simulated = {
        edge: np.asarray(simulated_by_edge[edge], dtype=float)
        for edge in held_edges
    }
    shapes = {series.shape for series in (*measured.values(), *simulated.values())}
    if len(shapes) != 1:
        raise ValueError("held-out evaluation series have inconsistent lengths")

    if measurement_semantics == "directional":
        if len(held_edges) != 1:
            raise ValueError("directional sensor must resolve to exactly one edge")
        edge = held_edges[0]
        return edge, measured[edge], simulated[edge]
    if measurement_semantics != "two_way_total":
        raise ValueError(
            f"unsupported held-out measurement semantics: {measurement_semantics}")
    if len(held_edges) < 2:
        raise ValueError("two_way_total sensor must resolve to at least two edges")

    raw_total = measured[held_edges[0]]
    for edge in held_edges[1:]:
        if not np.array_equal(raw_total, measured[edge], equal_nan=True):
            raise ValueError(
                "two_way_total source values differ across duplicated edges: "
                + ", ".join(held_edges))
    simulated_total = np.sum(
        np.stack([simulated[edge] for edge in held_edges]), axis=0)
    return "station_total", raw_total, simulated_total


def select_station_folds(
    sensor_edges: dict[str, list[str]], requested: list[str] | None,
) -> list[str]:
    """Validate an opt-in diagnostic subset without changing fold inputs."""
    if not requested:
        return sorted(sensor_edges)
    normalized = [str(station) for station in requested]
    if len(normalized) != len(set(normalized)):
        raise ValueError("diagnostic station subset contains duplicates")
    unknown = sorted(set(normalized) - set(sensor_edges))
    if unknown:
        raise ValueError(f"unknown diagnostic station(s): {', '.join(unknown)}")
    return sorted(normalized)


def held_integer_publication_bounds(
    bounds_per_quarter: list[dict[str, tuple[float, float]]],
    held_assignment_edges: list[str],
) -> list[dict[str, tuple[float, float]]] | None:
    """Retain only held-edge ceilings at the integer publication boundary."""
    held = set(held_assignment_edges)
    selected = [
        {edge: bound for edge, bound in bounds.items() if edge in held}
        for bounds in bounds_per_quarter
    ]
    return selected if any(selected) else None


def historical_demand_window(meta: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the half-open historical-demand window declared in metadata.

    Single-day demand retains legacy ``date`` while the supported multi-day
    calibration contract uses ``start_date`` and ``end_date_exclusive``.
    Keeping this parsing in one place prevents LOSO from accepting a build it
    later cannot describe in its own report.
    """
    try:
        # A one-day build deliberately carries both the newer range fields
        # and legacy date/begin/end fields. Prefer its actual sub-day window;
        # only multi-day builds are necessarily full calendar days.
        if int(meta.get("days", 1)) > 1:
            start = pd.Timestamp(meta["start_date"]).normalize()
            end = pd.Timestamp(meta["end_date_exclusive"]).normalize()
        else:
            day = pd.Timestamp(meta.get("date") or meta["start_date"]).normalize()
            begin = str(meta.get("begin", "00:00"))
            finish = str(meta.get("end", "24:00"))
            start = pd.Timestamp(f"{day.date()} {begin}")
            end = (day + pd.Timedelta(days=1) if finish == "24:00"
                   else pd.Timestamp(f"{day.date()} {finish}"))
    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "LOSO kräver giltiga date eller start_date/end_date_exclusive i "
            "sumo/demand_meta.json."
        ) from None
    if end <= start:
        raise SystemExit(
            "LOSO kräver att end_date_exclusive ligger efter start_date i "
            "sumo/demand_meta.json."
        )
    return start, end


def require_historical_demand(meta: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Reject demand that cannot be compared with the 2025 observations."""
    source = meta.get("source")
    if source != "historical":
        raise SystemExit(
            "LOSO kräver kalibrerad HISTORISK demand (2025) — nuvarande "
            f"sumo/demand_meta.json har 'source': {source!r}. Kör om "
            "build_sumo_demand.py med --source historical först."
        )
    start, end = historical_demand_window(meta)
    # ``end`` is exclusive, so a 2025-12-31 one-day demand legitimately
    # ends on 2026-01-01 while all measured quarters remain in 2025.
    if start.year != EPOCH.year or (end - pd.Timedelta(nanoseconds=1)).year != EPOCH.year:
        raise SystemExit(
            "LOSO kräver kalibrerad historisk demand helt inom 2025, samma "
            "år som web/data/flows.json — demand_meta.json beskriver "
            f"{start.date()} till {end.date()} (exklusivt)."
        )
    return start, end


def demand_through_share_target(meta: dict) -> float | None:
    """Read the effective PFE through prior from demand metadata.

    Builds predating this option have no key and therefore retain their
    emergent candidate-pool mix.  New builds must carry the exact value so
    LOSO validates the demand configuration that scenarios actually use.
    """
    raw = (meta.get("build_options") or {}).get("through_share_target")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SystemExit(
            "LOSO kräver ett giltigt through_share_target i "
            "sumo/demand_meta.json.") from None
    if not np.isfinite(value) or value < 0 or value >= 1:
        raise SystemExit(
            "LOSO kräver through_share_target inom intervallet [0, 1) i "
            "sumo/demand_meta.json.")
    return value


def resolve_evaluation_window(
    meta: dict,
    holdout_date: str | None,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp, str]:
    """Return frozen-reference and evaluation windows for LOSO.

    Without ``holdout_date`` this preserves the established same-window LOSO
    contract. A temporal holdout moves the exact clock window to a distinct
    date while retaining the current build's candidate pool, network and
    structural priors. Only the non-held stations' counts from the evaluation
    date enter each fold; the held station remains evaluation-only.

    A weekday candidate release is not silently used to claim weekend/holiday
    transfer. Different behavioural day classes need their own frozen release
    and are rejected here rather than producing an unlike comparison.
    """
    reference_start, reference_end = require_historical_demand(meta)
    if holdout_date is None:
        return (reference_start, reference_end,
                reference_start, reference_end, "same_window_loso")
    if int(meta.get("days", 1)) != 1:
        raise SystemExit(
            "Temporal holdout stöder tills vidare en fryst endags-release; "
            "bygg en endags historisk demand först.")
    try:
        parsed = calendar_date.fromisoformat(holdout_date)
    except (TypeError, ValueError):
        raise SystemExit(
            "--holdout-date måste vara ett giltigt datum i formatet YYYY-MM-DD."
        ) from None
    evaluation_start = (
        pd.Timestamp(parsed)
        + (reference_start - reference_start.normalize())
    )
    evaluation_end = evaluation_start + (reference_end - reference_start)
    if evaluation_start.normalize() == reference_start.normalize():
        raise SystemExit(
            "--holdout-date måste vara en annan dag än den frysta "
            "referensreleasen.")
    if (evaluation_start.year != EPOCH.year
            or (evaluation_end - pd.Timedelta(nanoseconds=1)).year != EPOCH.year):
        raise SystemExit(
            "Temporal holdout måste ligga helt inom 2025, året med "
            "oberoende historiska sensorobservationer.")

    reference_kind = classify_day(
        str(reference_start.date()), reference_start.dayofweek)[1]
    evaluation_kind = classify_day(
        str(evaluation_start.date()), evaluation_start.dayofweek)[1]
    if evaluation_kind != reference_kind:
        raise SystemExit(
            "Temporal holdout kräver samma dagtyp som den frysta releasen "
            f"({reference_kind}); {evaluation_start.date()} är "
            f"{evaluation_kind}.")
    return (reference_start, reference_end,
            evaluation_start, evaluation_end, "temporal_holdout")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-assignment-prior", action="store_true",
                   help="controlled A/B: disable the gravity-assignment "
                        "prior to isolate its effect on recovery")
    ap.add_argument(
        "--holdout-date",
        help="evaluate the frozen demand release on a distinct historical "
             "YYYY-MM-DD date; held-station counts remain evaluation-only")
    ap.add_argument(
        "--output", type=Path,
        help="report path (default: loso_report.json, or "
             "temporal_holdout_report.json with --holdout-date)")
    ap.add_argument(
        "--candidate-pool", type=Path,
        help="diagnostic candidate-pool override; its metadata sidecar is required")
    ap.add_argument(
        "--stations", nargs="+",
        help="diagnostic replay of only these held stations; every selected "
             "fold still calibrates on all other stations")
    ap.add_argument(
        "--picker-diagnostics-dir", type=Path,
        help="new empty directory for validation-only per-fold picker "
             "decomposition JSON")
    ap.add_argument(
        "--experimental-controlled-rounding", action="store_true",
        help="emit detailed diagnostics for the production joint integer "
             "projection (deprecated option name retained for replay scripts)")
    args = ap.parse_args()

    flows, meta, all_priors, corridor = load_inputs()
    (reference_start, reference_end,
     demand_start, demand_end, evaluation_mode) = resolve_evaluation_window(
         meta, args.holdout_date)
    through_share_target = demand_through_share_target(meta)
    build_options = meta.get("build_options") or {}
    assignment_config = {
        key: build_options[key]
        for key in (
            "gravity_km", "through_fraction", "cross_fraction",
            "gravity_alpha", "seed",
        )
        if key in build_options
    }
    activity_purpose_shares = activity_purpose_shares_for_window(
        demand_start, meta["n_intervals"])
    sensor_edges = load_sensor_edges()
    try:
        fold_stations = select_station_folds(sensor_edges, args.stations)
    except ValueError as error:
        ap.error(str(error))
    diagnostics_dir = args.picker_diagnostics_dir
    if diagnostics_dir is not None:
        if diagnostics_dir.exists() and any(diagnostics_dir.iterdir()):
            ap.error(f"refusing nonempty diagnostics directory: {diagnostics_dir}")
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
    sensor_registry = load_registry(Path("data_in/sensors.json"))
    sensor_registry.validate_resolved_edges(
        sensor_edges, sensor_ids=sensor_edges.keys())
    nq = int(meta["n_intervals"])
    expected_nq = int((demand_end - demand_start) / pd.Timedelta(minutes=15))
    if expected_nq != nq:
        raise SystemExit(
            "Temporal validation window does not match demand_meta.n_intervals "
            f"({expected_nq} != {nq}).")
    qi_start = int((demand_start - EPOCH) / pd.Timedelta(minutes=15))
    duration_s = nq * 900
    cand_path = args.candidate_pool or (SUMO_DIR / "candidates.rou.xml")
    if not cand_path.is_file():
        ap.error(f"candidate pool does not exist: {cand_path}")
    if corridor:
        print(f"  corridor coupling: {len(corridor)} edges between sensor "
              f"pairs get data-derived priors (excluded per-fold when "
              f"either anchor is the held-out station)")

    # edge -> sensor, so a corridor prior anchored on the held-out station's
    # own edges can be excluded (same leakage-prevention principle as
    # all_priors' d["sensor"] == held check below).
    edge_to_sensor = {e: sid for sid, edges in sensor_edges.items() for e in edges}

    legal_movements = []
    if diagnostics_dir is not None:
        diagnostic_network = route_support.load_network(SUMO_DIR / "net.net.xml")
        legal_movements = route_support.legal_movements(
            diagnostic_network, "26355153")

    full = build_targets(flows, sensor_edges, qi_start, nq)
    observed = observed_sensor_series(flows, sensor_edges, qi_start, nq)
    coverage = {
        edge: sum(edge in quarter for quarter in full)
        for edges in sensor_edges.values() for edge in edges
    }
    if evaluation_mode == "temporal_holdout":
        insufficient = {
            edge: observed for edge, observed in coverage.items()
            if observed / max(1, nq) < MIN_TEMPORAL_COVERAGE
        }
        if insufficient:
            details = ", ".join(
                f"{edge}={observed}/{nq}"
                for edge, observed in sorted(insufficient.items()))
            raise SystemExit(
                "Temporal holdout saknar tillräcklig oberoende sensortäckning "
                f"(krav {MIN_TEMPORAL_COVERAGE:.0%}): {details}.")

    report = {"schema_version": 3,
              "comparison_contract": {
                  # Sensor-contribution reports use this to reject an
                  # accidental comparison of different dates, candidate
                  # pools, networks or LOSO procedures as a sensor benefit.
                  "protocol": (
                      "temporal_loso_pfe_meso_v10_exact_condensed_projection"
                      if evaluation_mode == "temporal_holdout"
                      else "loso_pfe_meso_v13_exact_condensed_projection"),
                  "source": meta.get("source"),
                  "window_start": demand_start.isoformat(),
                  "window_end": demand_end.isoformat(),
                  "reference_window_start": reference_start.isoformat(),
                  "reference_window_end": reference_end.isoformat(),
                  "evaluation_mode": evaluation_mode,
                  "n_intervals": nq,
                  "simulation_mode": "meso",
                  "candidate_pool_sha256": sha256_file(cand_path),
                  "network_sha256": sha256_file(SUMO_DIR / "net.net.xml"),
                  "assignment_prior_enabled": not args.no_assignment_prior,
                  "assignment_field_config": assignment_config,
                  "assignment_field_fold_rule": (
                      "held_sensor_removed_before_structural_loading; "
                      "all edges covered by held-sensor measurements or "
                      "derived priors receive the normal unmeasured-edge ceiling"
                      if not args.no_assignment_prior else "disabled"),
                  "activity_purpose_margin": "calendar_hour_day_type",
                  "through_share_target": through_share_target,
                  "minimum_temporal_coverage": (
                      MIN_TEMPORAL_COVERAGE
                      if evaluation_mode == "temporal_holdout" else None),
                  "diagnostic_station_subset": fold_stations,
                  "picker_diagnostics_enabled": diagnostics_dir is not None,
                  # Compatibility key retained for consumers of v8 reports;
                  # it now describes the production path and is always true.
                  "controlled_integer_rounding": True,
                  "integer_projection": (
                      "validation_exact_condensed_l1_then_"
                      "production_general_v1"),
                  "integer_projection_equivalence": (
                      "same exact margins, retained bounds, purpose groups, "
                      "rounded interval total and route-L1 objective; "
                      "production general MILP is fail-closed fallback"),
                  "inactive_sensor_shadow_margins": (
                      "rounded continuous fold prediction; observed held "
                      "counts absent; soft below active sensors and hard bounds"),
                  "sensor_observability_gate": (
                      "candidate incidence rank of held station total against "
                      "active measurement margins plus interval total; held "
                      "observations absent"),
                  "joint_integer_diagnostics_enabled": bool(
                      args.experimental_controlled_rounding),
              },
              "window": f"{demand_start.date()} → {demand_end.date()} (exclusive)",
              "note": (
                  "temporal leave-one-station-out — the candidate pool, "
                  "network and structural priors remain frozen from "
                  f"{reference_start.date()}; each fold calibrates only on "
                  "the other stations' evaluation-date counts and reserves "
                  "the held station's evaluation-date counts for comparison"
                  if evaluation_mode == "temporal_holdout"
                  else "leave-one-station-out — simulated vs measured at the "
                  "held-out station; the assignment field is rebuilt with the "
                  "held station treated as genuinely unmeasured, while "
                  "measurement-derived bounds and priors are excluded without "
                  "using its count"),
              "observed_quarters": dict(sorted(coverage.items())),
              "stations": {}}

    label = "Temporal LOSO" if evaluation_mode == "temporal_holdout" else "LOSO"
    print(f"{label} över {len(fold_stations)} stationer, {nq} kvartar "
          f"({demand_start.date()})")
    for held in fold_stations:
        held_edges = sensor_edges[held]
        released_assignment_edges = assignment_edges_released_for_fold(
            held, held_edges, all_priors, corridor, edge_to_sensor)
        assign = {"weight": 0.0, "flows": {}}
        assignment_load = None
        if not args.no_assignment_prior:
            print(f"  rebuilding assignment field without sensor {held} …")
            assignment_load = compute_assignment_load_for_fold(
                held, sensor_edges, assignment_config)
            assign = calibrate_assignment_priors(
                assignment_load,
                DEFAULT_N_SAMPLES,
                exclude_sensor=held,
                write_path=None,
            )
            assign = include_held_assignment_flows(
                assign, assignment_load, released_assignment_edges)
        assign_w     = 0.0 if args.no_assignment_prior else assign.get("weight", 0.0)
        assign_flows = assign.get("flows", {})
        se = {s: e for s, e in sensor_edges.items() if s != held}
        targets = []
        drop = build_targets(flows, se, qi_start, nq)
        targets = drop

        priors_pq, bounds_pq = [], []
        for i in range(nq):
            slot = (qi_start + i) % 96
            qi = qi_start + i
            pq = {}
            for e, d in all_priors.items():
                if d["sensor"] == held:
                    continue
                val = d["prior"][slot]
                if val is None:
                    continue
                lo = d["prior_low"][slot] or 0.0
                hi = d["prior_high"][slot] or val
                pq[e] = (float(val), 1.0 / max(1.0, hi - lo))
            # Sensors helping each other: corridor blends between sensor
            # pairs, same as build_sumo_demand.py — excluded here whenever
            # either anchor sensor is the one being held out this fold.
            pq.update(corridor_priors_for_fold(corridor, edge_to_sensor, held, qi))
            priors_pq.append(pq)
            # Assignment field as a WIDE BOUND (see build_sumo_demand.py for
            # why: soft priors cost 2 LP variables each — 6500 of them made
            # a whole-day solve intractable; a bound is free variable-wise).
            # Structural (population/POI/gates), no leakage from held station.
            bq = {}
            if assign_w > 0:
                for e, series in assign_flows.items():
                    if e in pq or slot >= len(series) or series[slot] is None:
                        continue
                    bq[e] = (0.0, max(5.0, 5.0 * series[slot]))
            bounds_pq.append(bq)

        rou = SUMO_DIR / f"loso_{held}.rou.xml"
        purpose_departure_offset_s = int(
            (demand_start - demand_start.normalize()).total_seconds())
        diagnostic_path = (
            diagnostics_dir / f"picker_{held}.json"
            if diagnostics_dir is not None else None)
        diagnostic_context = None
        if diagnostic_path is not None:
            diagnostic_context = {
                "held_station": held,
                "held_component_edges": list(held_edges),
                "assignment_ceiling_edges": released_assignment_edges,
                "candidate_pool_sha256": sha256_file(cand_path),
                "network_sha256": sha256_file(SUMO_DIR / "net.net.xml"),
                "loso_source_sha256": sha256_file(Path(__file__)),
                "pfe_source_sha256": sha256_file(Path(pfe.__file__).resolve()),
                "picker_diagnostics_source_sha256": sha256_file(
                    Path(picker_diagnostics.__file__).resolve()),
                "measurement_semantics": sensor_registry[held].measurement_semantics,
            }
        integer_bounds_pq = held_integer_publication_bounds(
            bounds_pq, released_assignment_edges)
        rep = calibrate_fold_parallel(
            cand_path, rou, targets, bounds_pq, priors_pq,
            purpose_departure_offset_s=purpose_departure_offset_s,
            activity_purpose_shares_by_quarter=activity_purpose_shares,
            through_share_target=through_share_target,
            integer_bounds_pq=integer_bounds_pq,
            picker_diagnostics_path=diagnostic_path,
            picker_diagnostics_context=diagnostic_context,
            legal_movements=legal_movements,
            controlled_integer_rounding=(
                args.experimental_controlled_rounding),
            # Registry geometry is public model structure. Inactive/held
            # observations are not passed; their continuous fold predictions
            # are preserved only across integer publication.
            stability_edges=sorted({
                edge for edges in sensor_edges.values() for edge in edges
            }),
            observability_groups={held: list(held_edges)})
        ed = SUMO_DIR / f"loso_ed_{held}.xml"
        run_meso(rou, SUMO_DIR / f"loso_ed_{held}.xml", duration_s)

        held_ceiling_edges = sorted(edge for edge in released_assignment_edges
                                    if edge in assign_flows)
        st = {
            "edges": {},
            "pfe_geh_pct": rep["geh_pct"],
            "sensor_anchor_contract": rep.get("sensor_anchor_contract", {
                "required": True,
                "registered_edges": sorted({
                    edge for edges in sensor_edges.values() for edge in edges
                }),
                "unanchored_vehicles": None,
                "pass": False,
                "reason": "LOSO calibration did not publish an anchor proof",
            }),
            "held_edge_assignment_ceiling": {
                "required_edges": released_assignment_edges,
                "eligible_edges": held_ceiling_edges,
                "missing_edges": sorted(
                    set(released_assignment_edges) - set(held_ceiling_edges)),
                "scale_veh_per_day": assign.get("scale_veh_per_day"),
                "integer_publication_enforced": integer_bounds_pq is not None,
            },
        }
        if diagnostic_path is not None:
            st["picker_diagnostics"] = {
                "path": str(diagnostic_path),
                "sha256": sha256_file(diagnostic_path),
                "evidence_class": "diagnostic_replay",
            }
        if "controlled_integer_rounding" in rep:
            st["controlled_integer_rounding"] = rep[
                "controlled_integer_rounding"]
        st["inactive_sensor_integer_stability"] = {
            edge: rep.get("inactive_sensor_integer_stability", {}).get(edge)
            for edge in held_edges
            if edge in rep.get("inactive_sensor_integer_stability", {})
        }
        st["observability_certificate"] = rep.get(
            "inactive_sensor_observability", {}).get(held, {
                "status": "missing",
                "onboarding_ready": False,
                "reason": "LOSO calibration did not publish a certificate",
            })
        measurement_semantics = sensor_registry[held].measurement_semantics
        evaluation_key, meas, sim = held_out_evaluation_series(
            observed,
            {edge: simulated_series(ed, edge, nq) for edge in held_edges},
            held_edges,
            measurement_semantics,
        )
        for e in [evaluation_key]:
            ok = ~np.isnan(meas)
            m_tot, s_tot = float(meas[ok].sum()), float(sim[ok].sum())
            # hourly GEH at the held-out edge
            geh_ok = geh_n = 0
            hourly_cases = []
            incomplete_hours = []
            for h in range(0, nq - 3, 4):
                measured_hour = meas[h:h+4]
                if np.isnan(measured_hour).any():
                    incomplete_hours.append(h // 4)
                    continue
                mm = float(measured_hour.sum())
                ss = float(sim[h:h+4].sum())
                hourly_cases.append({
                    "hour_index": h // 4,
                    "measured": round(mm, 3),
                    "simulated": round(ss, 3),
                })
                if mm + ss > 0:
                    geh = np.sqrt(2 * (ss - mm) ** 2 / (ss + mm))
                    geh_n += 1
                    geh_ok += geh < 5
            st["edges"][e] = {
                "measurement_semantics": measurement_semantics,
                "component_edges": list(held_edges),
                "measured_total": round(m_tot), "simulated_total": round(s_tot),
                "ratio": round(s_tot / max(m_tot, 1), 3),
                "geh_ok_pct": round(100 * geh_ok / max(1, geh_n), 1),
                "observed_quarters": int(ok.sum()),
                "hourly_cases": hourly_cases,
                "excluded_incomplete_hours": incomplete_hours,
            }
            print(f"  utan {held:<6} {e:<26} kvot {s_tot/max(m_tot,1):>5.2f}  "
                  f"GEH<5 {100*geh_ok/max(1,geh_n):>5.1f}%")
        report["stations"][held] = st

    out = args.output or Path(
        "web/data/temporal_holdout_report.json"
        if evaluation_mode == "temporal_holdout"
        else "web/data/loso_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out_tmp = out.with_suffix(out.suffix + ".tmp")
    with open(out_tmp, "w") as f:
        json.dump(report, f, indent=1)
        f.write("\n")
    os.replace(out_tmp, out)
    ratios = [e["ratio"] for s in report["stations"].values()
              for e in s["edges"].values()]
    print(f"\nLOSO-kvoter: min {min(ratios):.2f}  median "
          f"{sorted(ratios)[len(ratios)//2]:.2f}  max {max(ratios):.2f}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
