"""Validation-only controlled rounding experiments for completed PFE solutions.

This module does not participate in production publication.  It compares the
legacy sequential measurement repair with a joint integer projection that
preserves every jointly feasible active measurement and the interval total. If
requested, exact-infeasible margins stay inside the continuous solver's
declared measurement band and are resolved lexicographically.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import hstack, identity, lil_matrix, vstack

from traffic_sim.demand import pfe


class ControlledRoundingError(RuntimeError):
    """Raised when no projection satisfying the selected contract is found."""


def _edge_rows(shapes: Sequence, edges: Sequence[str], n_columns: int):
    rows = []
    for edge in edges:
        row = lil_matrix((1, n_columns), dtype=float)
        for index, shape in enumerate(shapes):
            if edge in shape.edges:
                row[0, index] = 1.0
        rows.append(row.tocsr())
    return rows


def _result_metadata(result, method: str, counts: np.ndarray,
                     solution: np.ndarray, edges: Sequence[str],
                     shapes: Sequence, measured: Mapping[str, float]) -> dict:
    residuals = {
        edge: int(sum(int(counts[index]) for index, shape in enumerate(shapes)
                      if edge in shape.edges) - round(float(measured[edge])))
        for edge in edges
    }
    return {
        "method": method,
        "mip_status": int(result.status),
        "mip_nodes": int(getattr(result, "mip_node_count", 0) or 0),
        "mip_gap": round(float(getattr(result, "mip_gap", 0.0) or 0.0), 12),
        "route_l1_from_continuous": round(
            float(np.abs(counts.astype(float) - solution).sum()), 6),
        "continuous_total": round(float(solution.sum()), 6),
        "integer_total": int(counts.sum()),
        "measurement_residuals": residuals,
    }


def integer_margin_feasibility(
    solution: np.ndarray,
    shapes: Sequence,
    measured: Mapping[str, float],
    *,
    include_interval_total: bool = True,
    measurement_tol_mult: float | None = None,
    time_limit_s: float = 5.0,
) -> dict:
    """Test joint integer feasibility without selecting a quality treatment."""
    x = np.asarray(solution, dtype=float)
    edges = tuple(str(edge) for edge in measured)
    n = len(shapes)
    rows = _edge_rows(shapes, edges, n)
    lower = []
    upper = []
    for edge in edges:
        target = float(measured[edge])
        if measurement_tol_mult is None:
            lo = hi = float(round(target))
        else:
            tol = max(pfe.MEAS_TOL_MIN, pfe.MEAS_TOL_FRAC * target)
            tol *= float(measurement_tol_mult)
            lo = float(max(0, int(np.ceil(target - tol - 0.5))))
            hi = float(max(lo, int(np.floor(target + tol + 0.5))))
        lower.append(lo)
        upper.append(hi)
    total_target = float(round(float(x.sum())))
    if include_interval_total:
        total_row = lil_matrix((1, n), dtype=float)
        total_row[0, :] = 1.0
        rows.append(total_row.tocsr())
        lower.append(total_target)
        upper.append(total_target)
    if not rows:
        return {"feasible": True, "status": 0, "message": "no constraints"}
    route_upper = max(
        total_target if include_interval_total else 0.0,
        max(upper, default=0.0),
        1.0,
    )
    result = milp(
        c=np.zeros(n),
        integrality=np.ones(n),
        bounds=Bounds(np.zeros(n), np.full(n, route_upper)),
        constraints=LinearConstraint(
            vstack(rows, format="csc"), lower, upper),
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    return {
        "feasible": bool(result.success and result.x is not None),
        "status": int(result.status),
        "message": str(result.message),
        "mip_nodes": int(getattr(result, "mip_node_count", 0) or 0),
        "measurement_tol_mult": measurement_tol_mult,
        "interval_total_included": include_interval_total,
        "integer_interval_total": int(total_target),
    }


def minimal_inconsistent_integer_margins(
    solution: np.ndarray,
    shapes: Sequence,
    measured: Mapping[str, float],
    *,
    time_limit_s: float = 5.0,
) -> list[str]:
    """Return one deletion-minimal exact-margin conflict.

    This is an irreducible subset under single-constraint deletion, not
    necessarily the minimum-cardinality conflict.
    """
    labels = [str(edge) for edge in measured] + ["__interval_total__"]

    def feasible(selected: list[str]) -> bool:
        chosen = {
            edge: measured[edge]
            for edge in selected if edge != "__interval_total__"
        }
        return bool(integer_margin_feasibility(
            solution,
            shapes,
            chosen,
            include_interval_total="__interval_total__" in selected,
            time_limit_s=time_limit_s,
        )["feasible"])

    if feasible(labels):
        return []
    for label in list(labels):
        candidate = [item for item in labels if item != label]
        if not feasible(candidate):
            labels = candidate
    return labels


def controlled_round_preserving_measured(
    solution: np.ndarray,
    shapes: Sequence,
    measured: Mapping[str, float],
    *,
    time_limit_s: float = 10.0,
    measurement_tol_mult: float | None = None,
) -> tuple[np.ndarray, dict]:
    """Jointly round route flows while preserving counts and interval total.

    The fast model restricts every route to floor/ceil of its continuous flow.
    Within that binary domain, ``1 - 2*fraction`` is the exact change in L1
    error from choosing ceil instead of floor.  If the exact measurement
    margins cannot coexist inside floor/ceil, a general L1 MILP permits any
    nonnegative integer allocation with the same total. If exact margins remain
    impossible and ``measurement_tol_mult`` is supplied, the last model stays
    inside that declared band and minimises maximum residual, residual sum and
    route L1 in that order. Failure is explicit; no greedy fallback can silently
    leave a measurement residual.
    """
    x = np.asarray(solution, dtype=float)
    if x.ndim != 1 or len(x) != len(shapes):
        raise ValueError("solution length differs from shape count")
    if not np.all(np.isfinite(x)) or np.any(x < -1e-9):
        raise ValueError("solution must be finite and nonnegative")
    if isinstance(time_limit_s, bool) or time_limit_s <= 0:
        raise ValueError("time_limit_s must be positive")
    edges = tuple(str(edge) for edge in measured)
    n = len(shapes)
    rows = _edge_rows(shapes, edges, n)
    total_row = lil_matrix((1, n), dtype=float)
    total_row[0, :] = 1.0
    rows.append(total_row.tocsr())
    lower = [float(round(float(measured[edge]))) for edge in edges]
    lower.append(float(round(float(x.sum()))))
    constraints = LinearConstraint(
        vstack(rows, format="csc"), lower, lower)
    floor = np.floor(np.maximum(x, 0.0))
    fraction = x - floor
    fast = milp(
        c=1.0 - 2.0 * fraction,
        integrality=np.ones(n),
        bounds=Bounds(floor, np.ceil(x)),
        constraints=constraints,
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    if fast.success and fast.x is not None:
        counts = np.rint(fast.x).astype(int)
        return counts, _result_metadata(
            fast, "floor_ceil_l1", counts, x, edges, shapes, measured)

    # General exact L1 projection: z - positive + negative = x.  z remains
    # integer; deviation auxiliaries are continuous and nonnegative.
    basis = identity(n, format="csr")
    deviation_rows = hstack((basis, -basis, basis), format="csr")
    constraint_rows = []
    for row in rows:
        constraint_rows.append(hstack((row, lil_matrix((1, 2 * n))),
                                      format="csr"))
    combined = vstack([deviation_rows, *constraint_rows], format="csc")
    combined_lower = np.r_[x, lower]
    # The exact total constraint already bounds every nonnegative route.  Using
    # that bound avoids declaring a feasible projection impossible merely
    # because it must move more than an arbitrary local window.
    upper_route = np.full(n, float(round(float(x.sum()))))
    general = milp(
        c=np.r_[np.zeros(n), np.ones(2 * n)],
        integrality=np.r_[np.ones(n), np.zeros(2 * n)],
        bounds=Bounds(
            np.r_[np.zeros(n), np.zeros(2 * n)],
            np.r_[upper_route, np.full(2 * n, np.inf)]),
        constraints=LinearConstraint(
            combined, combined_lower, combined_lower),
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    if general.success and general.x is not None:
        counts = np.rint(general.x[:n]).astype(int)
        return counts, _result_metadata(
            general, "general_l1", counts, x, edges, shapes, measured)

    # Some observed margins are mutually inconsistent after each is rounded to
    # an integer (for example, nested detector edges can imply incompatible
    # differences). Resolve that explicitly and lexicographically:
    #   1. minimise the maximum absolute measurement residual;
    #   2. within that optimum, minimise the residual sum;
    #   3. within both optima, minimise route-level L1 deviation.
    # The held-out station is absent from all three objectives and constraints.
    m = len(edges)
    zero_n_2m1 = lil_matrix((n, 2 * m + 1), dtype=float)
    relaxed_deviation = hstack(
        (basis, -basis, basis, zero_n_2m1), format="csr")
    relaxed_measurement_rows = []
    for edge_index, row in enumerate(rows[:-1]):
        error = lil_matrix((1, 2 * m + 1), dtype=float)
        error[0, edge_index] = -1.0
        error[0, m + edge_index] = 1.0
        relaxed_measurement_rows.append(hstack(
            (row, lil_matrix((1, 2 * n)), error), format="csr"))
    relaxed_total = hstack((
        rows[-1], lil_matrix((1, 2 * n + 2 * m + 1))), format="csr")
    relaxed_equalities = vstack(
        [relaxed_deviation, *relaxed_measurement_rows, relaxed_total],
        format="csc")
    target_values = np.asarray(lower[:-1], dtype=float)
    total_target = float(lower[-1])
    equality_values = np.r_[x, target_values, total_target]

    max_error_rows = lil_matrix((m, 3 * n + 2 * m + 1), dtype=float)
    for edge_index in range(m):
        max_error_rows[edge_index, 3 * n + edge_index] = 1.0
        max_error_rows[edge_index, 3 * n + m + edge_index] = 1.0
        max_error_rows[edge_index, -1] = -1.0
    base_constraints = [
        LinearConstraint(relaxed_equalities, equality_values, equality_values),
        LinearConstraint(max_error_rows.tocsc(), np.full(m, -np.inf), 0.0),
    ]
    deviation_upper = max(total_target, float(np.max(x, initial=0.0)))
    error_upper = total_target + float(np.max(target_values, initial=0.0))
    error_variable_upper = np.full(2 * m, error_upper)
    permitted = None
    if measurement_tol_mult is not None:
        permitted = {}
        for edge_index, edge in enumerate(edges):
            target = float(measured[edge])
            rounded_target = float(round(target))
            tol = max(pfe.MEAS_TOL_MIN, pfe.MEAS_TOL_FRAC * target)
            tol *= float(measurement_tol_mult)
            lo = float(max(0, int(np.ceil(target - tol - 0.5))))
            hi = float(max(lo, int(np.floor(target + tol + 0.5))))
            permitted[edge] = [int(lo), int(hi)]
            error_variable_upper[edge_index] = max(0.0, hi - rounded_target)
            error_variable_upper[m + edge_index] = max(
                0.0, rounded_target - lo)
    relaxed_bounds = Bounds(
        np.zeros(3 * n + 2 * m + 1),
        np.r_[
            np.full(n, total_target),
            np.full(2 * n, deviation_upper),
            np.r_[error_variable_upper, error_upper],
        ],
    )
    relaxed_integrality = np.r_[np.ones(n), np.zeros(2 * n + 2 * m + 1)]

    max_objective = np.zeros(3 * n + 2 * m + 1)
    max_objective[-1] = 1.0
    min_max = milp(
        c=max_objective,
        integrality=relaxed_integrality,
        bounds=relaxed_bounds,
        constraints=base_constraints,
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    if not min_max.success or min_max.x is None:
        raise ControlledRoundingError(
            "controlled rounding failed including residual relaxation: "
            f"floor/ceil={fast.message}; exact={general.message}; "
            f"min-max={min_max.message}")
    optimum_max = float(min_max.fun)
    max_selector = lil_matrix((1, 3 * n + 2 * m + 1), dtype=float)
    max_selector[0, -1] = 1.0
    fixed_max = LinearConstraint(
        max_selector.tocsc(), -np.inf, optimum_max + 1e-7)

    sum_objective = np.zeros(3 * n + 2 * m + 1)
    sum_objective[3 * n:3 * n + 2 * m] = 1.0
    min_sum = milp(
        c=sum_objective,
        integrality=relaxed_integrality,
        bounds=relaxed_bounds,
        constraints=[*base_constraints, fixed_max],
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    if not min_sum.success or min_sum.x is None:
        raise ControlledRoundingError(
            f"minimum-residual-sum rounding failed: {min_sum.message}")
    optimum_sum = float(min_sum.fun)
    sum_selector = lil_matrix((1, 3 * n + 2 * m + 1), dtype=float)
    sum_selector[0, 3 * n:3 * n + 2 * m] = 1.0
    fixed_sum = LinearConstraint(
        sum_selector.tocsc(), -np.inf, optimum_sum + 1e-7)

    route_objective = np.zeros(3 * n + 2 * m + 1)
    route_objective[n:3 * n] = 1.0
    relaxed = milp(
        c=route_objective,
        integrality=relaxed_integrality,
        bounds=relaxed_bounds,
        constraints=[*base_constraints, fixed_max, fixed_sum],
        options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0},
    )
    if not relaxed.success or relaxed.x is None:
        raise ControlledRoundingError(
            f"minimum-route-deviation rounding failed: {relaxed.message}")
    counts = np.rint(relaxed.x[:n]).astype(int)
    method = (
        "measurement_band_lexicographic_l1"
        if measurement_tol_mult is not None
        else "minimum_measurement_error_l1")
    metadata = _result_metadata(
        relaxed, method, counts, x,
        edges, shapes, measured)
    metadata["minimum_max_abs_measurement_residual"] = round(optimum_max, 6)
    metadata["minimum_sum_abs_measurement_residual"] = round(optimum_sum, 6)
    if measurement_tol_mult is not None:
        metadata["measurement_tol_mult"] = float(measurement_tol_mult)
        metadata["permitted_integer_measurement_bounds"] = permitted
    return counts, metadata


def legacy_rounding_trace(
    solution: np.ndarray,
    shapes: Sequence,
    measured: Mapping[str, float],
    held_edges: Sequence[str],
) -> tuple[np.ndarray, dict]:
    """Reproduce and attribute the current sequential rounding heuristic."""
    x = np.asarray(solution, dtype=float)
    counts = pfe.largest_remainder_round(x)
    held = set(held_edges)
    held_entries = np.asarray([
        sum(edge in held for edge in set(shape.edges)) for shape in shapes
    ], dtype=int)
    edge_routes: dict[str, list[int]] = {}
    measured_touches: dict[int, int] = {}
    for index, shape in enumerate(shapes):
        touched = set(shape.edges) & measured.keys()
        if touched:
            measured_touches[index] = len(touched)
            for edge in touched:
                edge_routes.setdefault(edge, []).append(index)

    attribution: dict[str, Counter] = defaultdict(Counter)
    for pass_index in range(4):
        moved = False
        for edge, target in measured.items():
            indices = edge_routes.get(edge, [])
            current = sum(counts[index] for index in indices)
            deficit = int(round(float(target) - current))
            if not deficit:
                continue
            moved = True
            if deficit > 0:
                chosen = sorted(
                    indices,
                    key=lambda index: (
                        measured_touches[index], counts[index] - x[index]),
                )[:deficit]
                sign = 1
            else:
                chosen = sorted(
                    (index for index in indices if counts[index] > 0),
                    key=lambda index: (
                        measured_touches[index], x[index] - counts[index]),
                )[:-deficit]
                sign = -1
            row = attribution[str(edge)]
            row["passes_touched"] += int(not row.get(f"pass_{pass_index}", 0))
            row[f"pass_{pass_index}"] += sign * len(chosen)
            if sign > 0:
                row["routes_added"] += len(chosen)
                row["held_entries_added"] += sum(
                    int(held_entries[index]) for index in chosen)
            else:
                row["routes_removed"] += len(chosen)
                row["held_entries_removed"] += sum(
                    int(held_entries[index]) for index in chosen)
            for index in chosen:
                counts[index] += sign
        if not moved:
            break

    expected = pfe.round_preserving_measured(x, list(shapes), dict(measured))
    if not np.array_equal(counts, expected):
        raise RuntimeError("legacy rounding trace differs from production helper")
    residuals = {
        edge: int(sum(int(counts[index]) for index in indices)
                  - round(float(measured[edge])))
        for edge, indices in edge_routes.items()
    }
    return counts, {
        "global_largest_remainder_held_entries": int(
            pfe.largest_remainder_round(x) @ held_entries),
        "direct_round_held_entries": int(counts @ held_entries),
        "measurement_residuals": dict(sorted(residuals.items())),
        "correction_by_measured_edge": {
            edge: dict(sorted(values.items()))
            for edge, values in sorted(attribution.items())
        },
    }
