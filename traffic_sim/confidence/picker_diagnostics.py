"""Validation-only decomposition of a completed LOSO PFE fold.

The production solver remains untouched.  This module runs only after all
quarter solutions have been computed and records what those solutions use:
constraint coverage, final ceiling/guard utilisation and selected flow by
route, OD, purpose and legal junction movement.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from traffic_sim.demand import pfe

SCHEMA_VERSION = 1
KIND = "loso_picker_decomposition"
POSITIVE_TOLERANCE = 1e-9
CEILING_BINDING_ABS_TOLERANCE = 1.0
CEILING_BINDING_REL_TOLERANCE = 0.01

RUNG_NAMES = {
    pfe.RUNG_INFEASIBLE: "infeasible",
    pfe.RUNG_CLEAN: "clean",
    pfe.RUNG_NOBND_TOL1: "drop_bounds_tol1",
    pfe.RUNG_NOQUOTA_TOL1: "drop_bounds_and_purpose_quotas_tol1",
    pfe.RUNG_NOPRIOR_TOL1: "drop_bounds_quotas_and_priors_tol1",
    pfe.RUNG_LP_FALLBACK: "lp_fallback_tol1",
    pfe.RUNG_RELAX_TOL2X: "measurement_tolerance_2x",
    pfe.RUNG_RELAX_TOL4X: "measurement_tolerance_4x",
    pfe.RUNG_RELAX_NOBND: "measurement_tolerance_4x_without_bounds",
}

FIELD_SEMANTICS = {
    "targets": "measurement",
    "bounds": "model_assumption",
    "priors": "model_assumption",
    "structure_groups": "model_assumption",
    "purpose_mix": "model_assumption",
    "selected_flow": "diagnostic",
    "assignment_ceiling_utilisation": "diagnostic",
    "route_od_purpose_movement_contribution": "diagnostic",
    "relaxation_rung": "diagnostic",
}


def _purpose(shape) -> str:
    sources = shape.source_candidates or [shape]
    purposes = sorted({str(source.intent.get("purpose", "unknown"))
                       for source in sources})
    return "|".join(purposes)


def _movement_keys(edges: Sequence[str], movement_pairs: set[tuple[str, str]]) -> list[str]:
    return [f"{left}->{right}" for left, right in zip(edges, edges[1:])
            if (left, right) in movement_pairs]


def _touch_index(shapes: Sequence) -> dict[str, list[int]]:
    touch: dict[str, list[int]] = defaultdict(list)
    for index, shape in enumerate(shapes):
        for edge in set(shape.edges):
            touch[edge].append(index)
    return dict(touch)


def _sum_indices(solution: np.ndarray, indices: Sequence[int]) -> float:
    return float(solution[list(indices)].sum()) if indices else 0.0


def _rounded_mapping(values: Mapping[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in sorted(values.items())}


def _flow_decomposition(
    shapes: Sequence,
    solution: np.ndarray,
    route_indices: Sequence[int],
    movement_pairs: set[tuple[str, str]],
) -> dict:
    by_od: dict[str, float] = defaultdict(float)
    by_purpose: dict[str, float] = defaultdict(float)
    by_movement: dict[str, float] = defaultdict(float)
    for index in route_indices:
        flow = float(solution[index])
        if flow <= POSITIVE_TOLERANCE:
            continue
        shape = shapes[index]
        od = f"{shape.edges[0]}->{shape.edges[-1]}" if shape.edges else "->"
        by_od[od] += flow
        by_purpose[_purpose(shape)] += flow
        for movement in _movement_keys(shape.edges, movement_pairs):
            by_movement[movement] += flow
    return {
        "by_od": _rounded_mapping(by_od),
        "by_purpose": _rounded_mapping(by_purpose),
        "by_movement": _rounded_mapping(by_movement),
    }


def _constraint_rows(
    values: Mapping[str, object],
    solution: np.ndarray,
    touch: Mapping[str, Sequence[int]],
    kind: str,
) -> list[dict]:
    rows = []
    for edge, declared in sorted(values.items()):
        indices = touch.get(edge, ())
        row = {
            "edge": edge,
            "routes_touched": len(indices),
            "achieved_flow": round(_sum_indices(solution, indices), 6),
        }
        if kind == "target":
            row["target"] = float(declared)
            row["residual"] = round(row["achieved_flow"] - float(declared), 6)
        elif kind == "bound":
            lo, hi = declared
            row.update({"lower": float(lo), "upper": float(hi)})
        else:
            target, weight = declared
            row.update({"target": float(target), "weight": float(weight),
                        "residual": round(row["achieved_flow"] - float(target), 6)})
        rows.append(row)
    return rows


def build_fold_diagnostics(
    shapes: Sequence,
    route_cost: np.ndarray,
    targets_per_quarter: Sequence[Mapping[str, float]],
    bounds_per_quarter: Sequence[Mapping[str, tuple[float, float]]],
    priors_per_quarter: Sequence[Mapping[str, tuple[float, float]]],
    solutions: Sequence[np.ndarray | None],
    rungs: Sequence[int],
    structure_groups: Sequence[tuple[str, Sequence[int], float]],
    purpose_mixes: Sequence[Mapping[str, float]],
    *,
    held_station: str,
    held_component_edges: Sequence[str],
    assignment_ceiling_edges: Sequence[str],
    legal_movements: Sequence[Mapping[str, str]],
    source: Mapping[str, object],
) -> dict:
    """Build an exact, JSON-ready diagnostic without changing a solution."""
    n_quarters = len(targets_per_quarter)
    if not (len(bounds_per_quarter) == len(priors_per_quarter)
            == len(solutions) == len(rungs) == len(purpose_mixes) == n_quarters):
        raise ValueError("picker diagnostic quarter arrays have different lengths")
    if len(route_cost) != len(shapes):
        raise ValueError("route_cost length differs from shape count")

    touch = _touch_index(shapes)
    held_indices = sorted({index for edge in held_component_edges
                           for index in touch.get(edge, ())})
    movement_pairs = {(str(row["from_edge"]), str(row["to_edge"]))
                      for row in legal_movements}
    path_sizes = pfe.EPS_PARSIMONY / np.clip(
        np.asarray(route_cost, dtype=float), pfe.EPS_PARSIMONY, None)

    quarters = []
    rung_counts: Counter[str] = Counter()
    ceiling_binding_counts: Counter[str] = Counter()
    ceiling_max_utilisation: dict[str, float] = defaultdict(float)
    group_binding_counts: Counter[str] = Counter()
    aggregate_held_purpose: dict[str, float] = defaultdict(float)
    aggregate_held_movement: dict[str, float] = defaultdict(float)
    aggregate_held_od: dict[str, float] = defaultdict(float)

    for quarter in range(n_quarters):
        solution = solutions[quarter]
        rung_name = RUNG_NAMES.get(int(rungs[quarter]), f"unknown_{rungs[quarter]}")
        rung_counts[rung_name] += 1
        if solution is None:
            quarters.append({"quarter": quarter, "relaxation_rung": rung_name,
                             "solution": None})
            continue
        vector = np.asarray(solution, dtype=float)
        positive = np.flatnonzero(vector > POSITIVE_TOLERANCE).tolist()
        held_positive = [index for index in held_indices
                         if vector[index] > POSITIVE_TOLERANCE]

        route_rows = []
        for index in positive:
            shape = shapes[index]
            route_rows.append({
                "route_index": int(index),
                "route_geometry_sha256": hashlib.sha256(
                    " ".join(shape.edges).encode("utf-8")).hexdigest(),
                "selected_flow": round(float(vector[index]), 6),
                "path_size_seed_weight": round(float(path_sizes[index]), 6),
                "origin": shape.edges[0] if shape.edges else "",
                "destination": shape.edges[-1] if shape.edges else "",
                "purpose": _purpose(shape),
                "junction_movements": _movement_keys(shape.edges, movement_pairs),
                "touches_held_location": index in held_indices,
            })

        ceiling_rows = []
        for edge in sorted(set(assignment_ceiling_edges)):
            bound = bounds_per_quarter[quarter].get(edge)
            achieved = _sum_indices(vector, touch.get(edge, ()))
            if bound is None:
                ceiling_rows.append({"edge": edge, "present": False,
                                     "routes_touched": len(touch.get(edge, ())),
                                     "achieved_flow": round(achieved, 6)})
                continue
            lo, hi = bound
            tolerance = max(CEILING_BINDING_ABS_TOLERANCE,
                            CEILING_BINDING_REL_TOLERANCE * float(hi))
            binding = float(hi) - achieved <= tolerance
            utilisation = achieved / float(hi) if float(hi) > 0 else None
            if binding:
                ceiling_binding_counts[edge] += 1
            if utilisation is not None:
                ceiling_max_utilisation[edge] = max(
                    ceiling_max_utilisation[edge], utilisation)
            ceiling_rows.append({
                "edge": edge,
                "present": True,
                "lower": float(lo),
                "upper": float(hi),
                "routes_touched": len(touch.get(edge, ())),
                "achieved_flow": round(achieved, 6),
                "utilisation": round(utilisation, 6) if utilisation is not None else None,
                "binding": binding,
                "binding_tolerance": round(tolerance, 6),
            })

        total = float(vector.sum())
        group_rows = []
        for name, members, cap_share in structure_groups:
            achieved = _sum_indices(vector, members)
            cap = float(cap_share) * total
            tolerance = max(CEILING_BINDING_ABS_TOLERANCE,
                            CEILING_BINDING_REL_TOLERANCE * cap)
            binding = cap - achieved <= tolerance if cap > 0 else achieved <= tolerance
            if binding:
                group_binding_counts[name] += 1
            group_rows.append({
                "name": name,
                "members": len(members),
                "cap_share": float(cap_share),
                "cap_flow": round(cap, 6),
                "achieved_flow": round(achieved, 6),
                "utilisation": round(achieved / cap, 6) if cap > 0 else None,
                "binding": binding,
            })

        held_decomposition = _flow_decomposition(
            shapes, vector, held_indices, movement_pairs)
        for key, value in held_decomposition["by_od"].items():
            aggregate_held_od[key] += value
        for key, value in held_decomposition["by_purpose"].items():
            aggregate_held_purpose[key] += value
        for key, value in held_decomposition["by_movement"].items():
            aggregate_held_movement[key] += value

        quarters.append({
            "quarter": quarter,
            "relaxation_rung": rung_name,
            "total_selected_flow": round(total, 6),
            "positive_routes": len(positive),
            "held_location_selected_flow": round(
                _sum_indices(vector, held_indices), 6),
            "held_location_positive_routes": len(held_positive),
            "targets": _constraint_rows(
                targets_per_quarter[quarter], vector, touch, "target"),
            "bounds_summary": {
                "declared": len(bounds_per_quarter[quarter]),
                "with_route_support": sum(
                    bool(touch.get(edge)) for edge in bounds_per_quarter[quarter]),
            },
            "assignment_ceiling_utilisation": ceiling_rows,
            "priors": _constraint_rows(
                priors_per_quarter[quarter], vector, touch, "prior"),
            "structure_groups": group_rows,
            "purpose_mix": dict(sorted(purpose_mixes[quarter].items())),
            "held_location_flow_contribution": held_decomposition,
            "selected_routes": route_rows,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "evidence_class": "diagnostic_replay",
        "field_semantics": dict(FIELD_SEMANTICS),
        "solution_stage": "continuous_pre_integer_publication",
        "source": dict(source),
        "held_station": str(held_station),
        "held_component_edges": list(held_component_edges),
        "assignment_ceiling_edges": sorted(set(assignment_ceiling_edges)),
        "shape_count": len(shapes),
        "n_quarters": n_quarters,
        "summary": {
            "relaxation_rung_counts": dict(sorted(rung_counts.items())),
            "assignment_ceiling_binding_quarters": dict(
                sorted(ceiling_binding_counts.items())),
            "assignment_ceiling_max_utilisation": {
                key: round(value, 6)
                for key, value in sorted(ceiling_max_utilisation.items())
            },
            "structure_group_binding_quarters": dict(
                sorted(group_binding_counts.items())),
            "held_location_flow_by_od": _rounded_mapping(aggregate_held_od),
            "held_location_flow_by_purpose": _rounded_mapping(
                aggregate_held_purpose),
            "held_location_flow_by_movement": _rounded_mapping(
                aggregate_held_movement),
        },
        "quarters": quarters,
    }
