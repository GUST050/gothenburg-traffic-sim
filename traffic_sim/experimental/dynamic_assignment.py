"""Time-expanded route-flow calibration, isolated from production publication.

Columns are explicit route/departure alternatives; rows are sensor PASSAGE
quarters in each supplied travel-time scenario. A trip may span any number of
quarters. No shifting, reordering, driver-profile rewriting or route generation
occurs here. Callers own physical route provenance and travel-time estimation.

The integer fit preserves declared OD/purpose totals and column capacities,
minimizing absolute changes from prior route/departure flows. A zero prior does
not force route activation. Predicted exactness is NOT SUMO validation: changed
flows change congestion, so the observation operator must be re-estimated and
checked on independent simulation runs before any production adoption.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from contextvars import ContextVar
import math
import warnings
from typing import AbstractSet, Mapping, Sequence

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix, eye, hstack, vstack


#: Phases the solver reports when a diagnostic observer is installed. Declared
#: here so a report can show a phase that did NOT run — on a cache hit, an
#: absent ``milp_solve`` is the evidence that the solver was never invoked.
SOLVER_PHASE_NAMES = (
    'input_validation_and_hard_rhs',
    'scenario_difference_matrix',
    'departure_bound_constraints',
    'departure_group_bound_constraints',
    'constraint_assembly',
    'column_equivalence_reduction',
    'checkpoint_request_serialization',
    'checkpoint_request_key',
    'checkpoint_request_write',
    'checkpoint_cache_lookup',
    'checkpoint_cache_write',
    'milp_solve',
    'reexpansion_and_verification',
)

#: DIAGNOSTIC ONLY; its context-local value is None in production. A tool
#: installs an observer for one measured call. Nothing here changes the model,
#: solver options, column order, cache identity or checkpoint format.
_SOLVER_PHASE_OBSERVER = ContextVar('solver_phase_observer', default=None)


@contextmanager
def _observe_solver_phases(observer):
    """Install a phase observer for one measured section, then remove it.

    Removal is unconditional: a timeout, an infeasible model or any other
    exception must not leave production code reporting into a dead collector.
    """
    token = _SOLVER_PHASE_OBSERVER.set(observer)
    try:
        yield observer
    finally:
        _SOLVER_PHASE_OBSERVER.reset(token)


@contextmanager
def _solver_phase(name: str):
    """Time one solver phase when observed; do nothing at all otherwise."""
    observer = _SOLVER_PHASE_OBSERVER.get()
    if observer is None:
        yield
        return
    with observer.phase(name):
        yield


class DynamicAssignmentError(RuntimeError):
    """No verified feasible integer flow vector was obtained."""


class DynamicAssignmentTimeout(DynamicAssignmentError):
    """The time budget expired without a feasible incumbent; feasibility is unknown."""


class DynamicAssignmentInfeasible(DynamicAssignmentError):
    """HiGHS proved that the supplied constraints have no feasible solution."""


@dataclass(frozen=True)
class DepartureGroupBounds:
    """Per-departure-interval bounds for an explicit route class.

    ``option_ids`` names columns in the supplied :class:`PassageSystem`.
    This keeps behavioural structure such as a trip-length class in the same
    departure-time basis as the demand model while sensor rows remain in
    actual passage time.
    """
    name: str
    option_ids: AbstractSet[str]
    bounds: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class RouteDeparture:
    """One physical route at a fixed time, with full per-edge entry offsets.

    ``group`` identifies the caller's OD/purpose conservation group. Offsets
    are relative to intended departure, including insertion delay. The first
    edge is an emission, not an entered event. Repeated visits are counted.
    Negative departures allow an explicit preceding-period warm-up population.
    """
    option_id: str
    group: str
    departure_s: float
    edges: tuple[str, ...]
    entry_offsets_s: Mapping[str, tuple[float, ...]]
    prior: float
    capacity: int
    preference_cost: float = 0.0


@dataclass(frozen=True)
class PassageSystem:
    options: tuple[RouteDeparture, ...]
    sensors: tuple[str, ...]
    scenarios: tuple[str, ...]
    n_intervals: int
    interval_s: int
    matrix: csr_matrix
    boundary_matrix: csr_matrix

    def _vector(self, counts: Sequence[float]) -> np.ndarray:
        values = np.asarray(counts, dtype=float)
        if values.shape != (len(self.options),) or not np.all(np.isfinite(values)) \
                or np.any(values < 0):
            raise ValueError('counts must be a finite nonnegative column vector')
        return values

    def project(self, counts: Sequence[float]) -> dict:
        """Project flows to sensor entry quarters, never departure quarters."""
        values = (self.matrix @ self._vector(counts)).reshape(
            len(self.scenarios), len(self.sensors), self.n_intervals)
        return {scenario: {edge: values[a, e].tolist()
                           for e, edge in enumerate(self.sensors)}
                for a, scenario in enumerate(self.scenarios)}

    def boundary_counts(self, counts: Sequence[float]) -> dict:
        """Expose preceding/tail passages rather than dropping their mass."""
        values = (self.boundary_matrix @ self._vector(counts)).reshape(
            len(self.scenarios), len(self.sensors), 2)
        return {scenario: {edge: {'before': float(values[a, e, 0]),
                                  'after': float(values[a, e, 1])}
                           for e, edge in enumerate(self.sensors)}
                for a, scenario in enumerate(self.scenarios)}


def build_passage_system(
    options: Sequence[RouteDeparture], sensors: Sequence[str], n_intervals: int,
    *, interval_s: int = 900,
) -> PassageSystem:
    """Build sparse actual-entry incidence across all route/time alternatives."""
    for value in (n_intervals, interval_s):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('interval count and width must be positive integers')
    options = tuple(options)
    sensor_ids = tuple(sorted(sensors))
    if not options or not sensor_ids or len(set(sensor_ids)) != len(sensor_ids) \
            or any(not isinstance(edge, str) or not edge for edge in sensor_ids):
        raise ValueError('nonempty options and unique sensor IDs are required')
    if len({o.option_id for o in options}) != len(options):
        raise ValueError('duplicate option IDs')
    scenarios = tuple(sorted(options[0].entry_offsets_s))
    if not scenarios or any(not isinstance(s, str) or not s for s in scenarios):
        raise ValueError('named travel-time scenarios are required')
    lookup = {edge: i for i, edge in enumerate(sensor_ids)}
    rows, columns, boundary_rows, boundary_columns = [], [], [], []
    # Shift alternatives share retained route/time objects. Validate and project
    # that common path once per call; scalar bounds/departures stay per option.
    # The options tuple retains all objects, so identities cannot be recycled.
    observations = {}
    for column, option in enumerate(options):
        if not option.option_id or not option.group or not option.edges:
            raise ValueError('each alternative needs identity, group and physical edges')
        if not math.isfinite(option.preference_cost) or option.preference_cost < 0:
            raise ValueError('preference cost must be finite and nonnegative')
        if not math.isfinite(option.departure_s) or not math.isfinite(option.prior) \
                or option.prior < 0:
            raise ValueError('departure and nonnegative prior must be finite')
        if isinstance(option.capacity, bool) or not isinstance(option.capacity, int) \
                or option.capacity < 0:
            raise ValueError('capacity must be a nonnegative integer')
        key = (id(option.edges), id(option.entry_offsets_s))
        observed = observations.get(key)
        if observed is None:
            if any(not isinstance(edge, str) or not edge for edge in option.edges):
                raise ValueError('each alternative needs identity, group and physical edges')
            if tuple(sorted(option.entry_offsets_s)) != scenarios:
                raise ValueError('every alternative must have every travel-time scenario')
            observed = []
            for arm, scenario in enumerate(scenarios):
                offsets = option.entry_offsets_s[scenario]
                if len(offsets) != len(option.edges) or any(
                        not math.isfinite(value) or value < 0 for value in offsets) \
                        or any(a > b for a, b in zip(offsets, offsets[1:])):
                    raise ValueError('complete finite monotone entry offsets are required')
                observed.extend((arm * len(sensor_ids) + lookup[edge], offset)
                                for edge, offset in zip(option.edges[1:], offsets[1:])
                                if edge in lookup)
            observations[key] = observed
        for base, offset in observed:
            quarter = math.floor((option.departure_s + offset) / interval_s)
            if 0 <= quarter < n_intervals:
                rows.append(base * n_intervals + quarter)
                columns.append(column)
            else:
                boundary_rows.append(base * 2 + int(quarter >= n_intervals))
                boundary_columns.append(column)
    shape = (len(scenarios) * len(sensor_ids) * n_intervals, len(options))
    matrix = coo_matrix((np.ones(len(rows)), (rows, columns)), shape=shape).tocsr()
    boundary = coo_matrix((np.ones(len(boundary_rows)), (boundary_rows, boundary_columns)),
                          shape=(len(scenarios) * len(sensor_ids) * 2, len(options))).tocsr()
    return PassageSystem(options, sensor_ids, scenarios, n_intervals, interval_s,
                         matrix, boundary)


@dataclass(frozen=True)
class FlowFit:
    counts: np.ndarray
    status: str
    absolute_prior_change: float
    solver_message: str


def fit_integer_flows(
    system: PassageSystem, targets: Mapping[str, Sequence[int]],
    group_totals: Mapping[str, int], *, time_limit_s: float = 30,
    departure_bounds: Sequence[Mapping[str, tuple[float, float]]] | None = None,
    departure_totals: Sequence[int] | None = None,
    departure_group_bounds: Sequence[DepartureGroupBounds] | None = None,
    checkpoint_dir=None,
    cache_dir=None,
) -> FlowFit:
    """Joint integer route/time fit with hard observations and conserved groups.

    Minimize L1 deviation from prior flows. This is a transparent baseline
    regularizer, not a replacement for production entropy/purpose/structure
    contracts. Infeasibility is reported; no measured row or group is relaxed.
    A time-limited feasible incumbent is allowed only after full verification.
    """
    with _solver_phase('input_validation_and_hard_rhs'):
        if not math.isfinite(time_limit_s) or not 0 < time_limit_s <= 120:
            raise ValueError('time limit must be in (0, 120] seconds')
        if set(targets) != set(system.sensors):
            raise ValueError('target sensors differ from observation system')
        values = []
        for edge in system.sensors:
            series = targets[edge]
            if len(series) != system.n_intervals:
                raise ValueError('target interval count differs')
            for value in series:
                if isinstance(value, bool) or not math.isfinite(value) \
                        or value < 0 or float(value) != int(value):
                    raise ValueError('explicit nonnegative integer targets are required')
                values.append(int(value))
        groups = tuple(sorted({option.group for option in system.options}))
        if set(group_totals) != set(groups):
            raise ValueError('every OD/purpose group needs an explicit total')
        for value in group_totals.values():
            if isinstance(value, bool) or not math.isfinite(value) \
                    or value < 0 or float(value) != int(value):
                raise ValueError('group totals must be nonnegative integers')
        n = len(system.options)
        group_index = {group: i for i, group in enumerate(groups)}
        conservation = coo_matrix((np.ones(n),
            ([group_index[option.group] for option in system.options], np.arange(n))),
            shape=(len(groups), n)).tocsr()
        hard = vstack([system.matrix, conservation], format='csr')
        rhs = np.array(values * len(system.scenarios) + [group_totals[g] for g in groups])
        if departure_totals is not None:
            if len(departure_totals) != system.n_intervals or any(
                    isinstance(v, bool) or not math.isfinite(v) or v < 0 or int(v) != v
                    for v in departure_totals):
                raise ValueError('departure totals must cover every interval '
                                 'with nonnegative integers')
            quarters = [math.floor(o.departure_s / system.interval_s) for o in system.options]
            entries = [(q, i) for i, q in enumerate(quarters) if 0 <= q < system.n_intervals]
            population = coo_matrix((np.ones(len(entries)),
                ([q for q, _ in entries], [i for _, i in entries])),
                shape=(system.n_intervals, n)).tocsr()
            hard = vstack([hard, population], format='csr')
            rhs = np.r_[rhs, departure_totals]
    with _solver_phase('scenario_difference_matrix'):
        # Every travel-time scenario must hit the SAME target. Expressing that
        # as A0*x=b and (Ai-A0)*x=0 preserves the feasible set exactly, while
        # cancelling the many unchanged passage incidences between scenarios.
        # Keep the original equations above for independent output verification.
        block = len(system.sensors) * system.n_intervals
        scenario_rows = block * len(system.scenarios)
        baseline = hard[:block]
        solve_hard = vstack(
            [baseline] + [hard[start:start+block] - baseline
                          for start in range(block, scenario_rows, block)]
            + [hard[scenario_rows:]], format='csr')
        solve_hard.eliminate_zeros()
        solve_rhs = rhs.copy()
        solve_rhs[block:scenario_rows] = 0
        prior = np.array([option.prior for option in system.options])
        upper = np.array([option.capacity for option in system.options])
        preferences = np.array([option.preference_cost for option in system.options])
        # On [0, capacity], |x-prior| is linear when the prior is at/outside
        # either bound. This includes every binary route/time alternative.
        # Keep the general epigraph formulation for interior/fractional priors.
        linear_prior = bool(np.all((prior <= 0) | (prior >= upper)))
        if linear_prior:
            constraints = [LinearConstraint(solve_hard, solve_rhs, solve_rhs)]
            cost = preferences + np.where(prior >= upper, -1., 1.)
            integrality = np.ones(n)
            limits = Bounds(np.zeros(n), upper)
        else:
            identity = eye(n, format='csr')
            constraints = [
                LinearConstraint(hstack([solve_hard, csr_matrix(solve_hard.shape)], format='csr'),
                                 solve_rhs, solve_rhs),
                LinearConstraint(hstack([identity, -identity], format='csr'), -np.inf, prior),
                LinearConstraint(hstack([-identity, -identity], format='csr'), -np.inf, -prior),
            ]
            cost = np.r_[preferences, np.ones(n)]
            integrality = np.r_[np.ones(n), np.zeros(n)]
            limits = Bounds(np.zeros(2*n), np.r_[upper, np.full(n, np.inf)])
    with _solver_phase('departure_bound_constraints'):
        edge_matrix, edge_lower, edge_upper = departure_bound_constraints(
            system, departure_bounds)
    with _solver_phase('departure_group_bound_constraints'):
        group_matrix, group_lower, group_upper = departure_group_bound_constraints(
            system, departure_group_bounds)
    with _solver_phase('constraint_assembly'):
        bound_matrix = vstack([edge_matrix, group_matrix], format='csr')
        bound_lower = np.r_[edge_lower, group_lower]
        bound_upper = np.r_[edge_upper, group_upper]
        if len(bound_lower):
            bounded = bound_matrix if linear_prior else hstack([
                bound_matrix, csr_matrix(bound_matrix.shape)], format='csr')
            constraints.append(LinearConstraint(bounded, bound_lower, bound_upper))
    with _solver_phase('column_equivalence_reduction'):
        members = None
        if linear_prior:
            full = vstack([solve_hard, bound_matrix], format='csc')
            keys = vstack([full, system.boundary_matrix], format='csc')
            keys.sort_indices()
            groups_by_column, representatives, members = {}, [], []
            for column in range(n):
                start, end = keys.indptr[column:column+2]
                key = (keys.indices[start:end].tobytes(),
                       keys.data[start:end].tobytes(), cost[column])
                group = groups_by_column.get(key)
                if group is None:
                    group = len(members)
                    groups_by_column[key] = group
                    representatives.append(column)
                    members.append([])
                members[group].append(column)
            # Merge only columns with identical observations, conservation,
            # structural bounds, boundary accounting AND objective coefficient.
            # Re-expand into original physical alternatives before verification.
            cost = cost[representatives]
            constraints = [LinearConstraint(full[:, representatives],
                            np.r_[solve_rhs, bound_lower], np.r_[solve_rhs, bound_upper])]
            limits = Bounds(np.zeros(len(members)), [upper[indices].sum() for indices in members])
            integrality = np.ones(len(members))
    # This solve runs in the parent between daily PFE fork pools. A native
    # HiGHS thread pool here is inherited broken by the next day's children;
    # applying their threads=1 option then spins in HighsTaskExecutor.shutdown.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=r"Unrecognized options detected: .*'threads'.*",
                                category=Warning)
        arguments = dict(c=cost, integrality=integrality, bounds=limits,
                         constraints=constraints,
                         options={'time_limit': time_limit_s, 'threads': 1})
        if checkpoint_dir is None:
            with _solver_phase('milp_solve'):
                result = milp(**arguments)
        else:
            from traffic_sim.demand.passage_solver import solve_checkpointed
            if cache_dir is None:
                raise ValueError('checkpointed fits require an explicit cache directory')
            result = solve_checkpointed(milp, checkpoint_dir, cache_dir, **arguments)
    if result.status == 2:
        raise DynamicAssignmentInfeasible(f'infeasible dynamic fit: {result.message}')
    if result.status == 1 and result.x is None:
        checkpoint = f'; replay request: {checkpoint_dir}/request.npz' if checkpoint_dir else ''
        raise DynamicAssignmentTimeout(
            f'dynamic fit time limit; feasibility unknown: {result.message}{checkpoint}')
    if result.x is None or result.status not in (0, 1):
        raise DynamicAssignmentError(f'infeasible or unsolved dynamic fit: {result.message}')
    with _solver_phase('reexpansion_and_verification'):
        raw = result.x[:n]
        if members is not None:
            aggregated = result.x
            if not np.all(np.isfinite(aggregated)) or np.any(aggregated < -1e-6) \
                    or np.any(np.abs(aggregated - np.rint(aggregated)) > 1e-6):
                raise DynamicAssignmentError('invalid aggregated integer flow')
            raw = np.zeros(n)
            for indices, total in zip(members, np.maximum(0, np.rint(aggregated))):
                for column in indices:
                    raw[column] = min(total, upper[column])
                    total -= raw[column]
                if total != 0:
                    raise DynamicAssignmentError('aggregated flow exceeds original capacity')
        if not np.all(np.isfinite(raw)):
            raise DynamicAssignmentError('solver output is non-finite')
        counts = np.rint(raw).astype(np.int64)
        if np.any(np.abs(raw - counts) > 1e-6) \
                or np.any(counts < 0) or np.any(counts > upper) \
                or not np.array_equal(hard @ counts, rhs):
            raise DynamicAssignmentError('solver output violates integer passage constraints')
        bounded = bound_matrix @ counts
        if np.any(bounded < bound_lower) or np.any(bounded > bound_upper):
            raise DynamicAssignmentError('solver output violates production departure bounds')
    return FlowFit(counts, 'predicted_exact_requires_sumo',
                   float(np.abs(counts - prior).sum()), result.message)


def _validated_shift_grid(shifts_s: Sequence[int], begin_s: float,
                          end_s: float, guard_s: float) -> list[int]:
    """Check the grid and horizon, independently of who validated the options."""
    shifts = sorted(shifts_s)
    if not shifts or 0 not in shifts or len(set(shifts)) != len(shifts) or any(
            isinstance(v, bool) or not isinstance(v, int) for v in shifts):
        raise ValueError('unique integer shifts including zero are required')
    if not all(math.isfinite(v) for v in (begin_s, end_s, guard_s)) \
            or begin_s >= end_s or guard_s < 0:
        raise ValueError('finite horizon and nonnegative guard are required')
    return shifts


def expand_departure_support(
    base_options: Sequence[RouteDeparture], shifts_s: Sequence[int], *,
    begin_s: float, end_s: float, guard_s: float = 0,
) -> list[RouteDeparture]:
    """Make bounded time alternatives; retain each source prior at shift zero.

    Nearby-time offsets are a LOCAL prediction, to be refreshed after SUMO
    loading. Envelope scenarios use each route's observed spread plus the
    declared guard; they do not force all its sensors into the same quarter.
    All generated alternatives have capacity one to avoid point-mass cloning.

    Callers holding a system that already validated these exact options use
    ``_expand_departure_support_verified`` instead of paying for the throwaway
    validation system below.
    """
    shifts = _validated_shift_grid(shifts_s, begin_s, end_s, guard_s)
    # Reuse the observation builder's complete travel-time validation.
    if not base_options:
        raise ValueError('base alternatives are required')
    build_passage_system(base_options, [base_options[0].edges[0]], 1)
    return _shifted_alternatives(base_options, shifts, begin_s=begin_s,
                                 end_s=end_s, guard_s=guard_s)


def _expand_departure_support_verified(
    system: PassageSystem, shifts_s: Sequence[int], *,
    begin_s: float, end_s: float, guard_s: float = 0,
) -> list[RouteDeparture]:
    """Expand the options a built system has ALREADY validated.

    PRIVATE on purpose. Skipping validation is only sound for a caller that
    holds the very system which performed it, so this is not advertised as a
    public entry point that arbitrary code may reach for.

    ``build_passage_system`` checks every option it accepts — identity, group,
    physical edges, complete finite monotone offsets per named scenario,
    capacity and prior — so the throwaway system the public entry point builds
    cannot fail for options that are already inside one. Taking the system
    rather than a skip flag makes that proof unforgeable: a ``PassageSystem``
    cannot exist without its options having passed. Every per-option envelope
    rule that belongs to the EXPANSION (unit capacity, declared horizon,
    reserved scenario names) is still enforced here.
    """
    if not isinstance(system, PassageSystem):
        raise ValueError('a built passage system is required')
    shifts = _validated_shift_grid(shifts_s, begin_s, end_s, guard_s)
    if not system.options:
        raise ValueError('base alternatives are required')
    return _shifted_alternatives(system.options, shifts, begin_s=begin_s,
                                 end_s=end_s, guard_s=guard_s)


def _shifted_alternatives(
    base_options: Sequence[RouteDeparture], shifts: Sequence[int], *,
    begin_s: float, end_s: float, guard_s: float,
) -> list[RouteDeparture]:
    """Build the alternatives themselves; the options are already validated."""
    scale = max(1, max(abs(v) for v in shifts))
    result = []
    for option in base_options:
        if not begin_s <= option.departure_s < end_s or option.prior > 1:
            raise ValueError('source prior must fit unit capacity and declared horizon')
        if {'uncertainty_lower', 'uncertainty_upper'} & set(option.entry_offsets_s):
            raise ValueError('uncertainty scenario names are reserved')
        samples = list(option.entry_offsets_s.values())
        offsets = dict(option.entry_offsets_s)
        if guard_s:
            offsets['uncertainty_lower'] = tuple(
                max(0., min(sample[i] for sample in samples) - guard_s)
                for i in range(len(option.edges)))
            offsets['uncertainty_upper'] = tuple(
                max(sample[i] for sample in samples) + guard_s
                for i in range(len(option.edges)))
        for shift in shifts:
            depart = option.departure_s + shift
            if not begin_s <= depart < end_s:
                continue
            result.append(replace(
                option, option_id=f'{option.option_id}@{shift}', departure_s=depart,
                entry_offsets_s=offsets, prior=option.prior if shift == 0 else 0.,
                capacity=min(option.capacity, 1),
                preference_cost=option.preference_cost + .01 * abs(shift) / scale))
    return result


def expand_passage_boundary_support(
    base_options: Sequence[RouteDeparture], sensors: Sequence[str], *,
    max_shift_s: float, begin_s: float, end_s: float, guard_s: float = 0,
    interval_s: int = 900, time_resolution_s: float = .1,
) -> list[RouteDeparture]:
    """Generate one closest alternative per distinct time-observation state.

    A fixed shift grid can miss the route-specific instant where a measured
    passage changes interval.  These alternatives are derived from those
    change points and then deduplicated by their full passage/departure
    signature.  This expands representation only; routes and OD/purpose
    groups remain unchanged.
    """
    sensor_ids = frozenset(str(sensor) for sensor in sensors)
    if not base_options or not sensor_ids:
        raise ValueError('base alternatives and sensors are required')
    if not all(math.isfinite(value) for value in (
            max_shift_s, begin_s, end_s, guard_s, time_resolution_s)) \
            or max_shift_s <= 0 or begin_s >= end_s or guard_s < 0 \
            or interval_s <= 0 or time_resolution_s <= 0:
        raise ValueError('invalid passage-boundary support envelope')
    # Validate the source options and their complete observation scenarios.
    build_passage_system(base_options, sorted(sensor_ids),
                         max(1, math.ceil((end_s - begin_s) / interval_s)),
                         interval_s=interval_s)
    result: list[RouteDeparture] = []
    for option in base_options:
        samples = list(option.entry_offsets_s.values())
        offsets = dict(option.entry_offsets_s)
        if guard_s:
            offsets['uncertainty_lower'] = tuple(
                max(0., min(sample[i] for sample in samples) - guard_s)
                for i in range(len(option.edges)))
            offsets['uncertainty_upper'] = tuple(
                max(sample[i] for sample in samples) + guard_s
                for i in range(len(option.edges)))
        candidates = {option.departure_s}
        observed_offsets = [0.0]
        for values in offsets.values():
            observed_offsets.extend(
                offset for edge, offset in zip(option.edges[1:], values[1:])
                if edge in sensor_ids)
        for offset in observed_offsets:
            low = option.departure_s + offset - max_shift_s
            high = option.departure_s + offset + max_shift_s
            for quarter in range(math.floor(low / interval_s) + 1,
                                 math.floor(high / interval_s) + 1):
                change = quarter * interval_s - offset
                right = (math.ceil(change / time_resolution_s - 1e-9)
                         * time_resolution_s)
                for departure in (right - time_resolution_s, right):
                    departure = round(departure / time_resolution_s) * time_resolution_s
                    if begin_s <= departure < end_s \
                            and abs(departure - option.departure_s) <= max_shift_s + 1e-9:
                        candidates.add(round(departure, 9))
        # The sparse model only observes passage cells, boundary side and the
        # departure quarter used by structural bounds. Retain the closest
        # physical time for each distinct state.
        representatives: dict[tuple[int, ...], float] = {}
        for departure in sorted(candidates):
            signature = [math.floor(departure / interval_s)]
            for scenario in sorted(offsets):
                signature.extend(
                    math.floor((departure + offset) / interval_s)
                    for edge, offset in zip(option.edges[1:], offsets[scenario][1:])
                    if edge in sensor_ids)
            key = tuple(signature)
            current = representatives.get(key)
            if current is None or (abs(departure - option.departure_s), departure) \
                    < (abs(current - option.departure_s), current):
                representatives[key] = departure
        for departure in sorted(representatives.values()):
            shift = departure - option.departure_s
            result.append(replace(
                option, option_id=f'{option.option_id}@{shift:.1f}',
                departure_s=departure, entry_offsets_s=offsets,
                prior=option.prior if abs(shift) < time_resolution_s / 2 else 0.,
                capacity=min(option.capacity, 1),
                preference_cost=(option.preference_cost
                                 + .01 * abs(shift) / max_shift_s)))
    return result


def departure_bound_constraints(system: PassageSystem, bounds) -> tuple:
    """Keep the production unmeasured-edge bounds in their declared time basis.

    These structural bounds count unique route incidence per departure quarter,
    just as PFE does. Measured sensor constraints remain passage-time rows.
    Only the caller may omit a previously relaxed bound; this solver never does.
    """
    if bounds is None:
        return csr_matrix((0, len(system.options))), np.array([]), np.array([])
    if len(bounds) != system.n_intervals:
        raise ValueError('departure bounds must cover every interval')
    keys, lower, upper = {}, [], []
    for quarter, row in enumerate(bounds):
        for edge, span in sorted(row.items()):
            if len(span) != 2 or not all(math.isfinite(v) for v in span) \
                    or span[0] < 0 or span[0] > span[1]:
                raise ValueError('invalid production bound')
            keys[quarter, edge] = len(lower)
            lower.append(math.ceil(span[0] - .5))
            upper.append(math.floor(span[1] + .5))
    rows, columns = [], []
    for column, option in enumerate(system.options):
        quarter = math.floor(option.departure_s / system.interval_s)
        for edge in set(option.edges):
            row = keys.get((quarter, edge))
            if row is not None:
                rows.append(row)
                columns.append(column)
    matrix = coo_matrix((np.ones(len(rows)), (rows, columns)),
                        shape=(len(lower), len(system.options))).tocsr()
    return matrix, np.asarray(lower), np.asarray(upper)


def departure_group_bound_constraints(
    system: PassageSystem,
    groups: Sequence[DepartureGroupBounds] | None,
) -> tuple[csr_matrix, np.ndarray, np.ndarray]:
    """Build sparse departure-quarter bounds for named route classes."""
    if groups is None:
        return csr_matrix((0, len(system.options))), np.array([]), np.array([])
    option_ids = {option.option_id for option in system.options}
    names: set[str] = set()
    rows, columns, lower, upper = [], [], [], []
    for group in groups:
        if not isinstance(group, DepartureGroupBounds) or not group.name \
                or group.name in names:
            raise ValueError('departure groups require unique nonempty names')
        names.add(group.name)
        members = set(group.option_ids)
        if not members or not members <= option_ids:
            raise ValueError(f'departure group {group.name!r} has invalid option IDs')
        if len(group.bounds) != system.n_intervals:
            raise ValueError(f'departure group {group.name!r} must cover every interval')
        offset = len(lower)
        for span in group.bounds:
            if len(span) != 2 or not all(math.isfinite(value) for value in span) \
                    or span[0] < 0 or span[0] > span[1]:
                raise ValueError(f'departure group {group.name!r} has invalid bounds')
            lower.append(math.ceil(span[0] - .5))
            upper.append(math.floor(span[1] + .5))
        for column, option in enumerate(system.options):
            if option.option_id not in members:
                continue
            quarter = math.floor(option.departure_s / system.interval_s)
            if 0 <= quarter < system.n_intervals:
                rows.append(offset + quarter)
                columns.append(column)
    matrix = coo_matrix((np.ones(len(rows)), (rows, columns)),
                        shape=(len(lower), len(system.options))).tocsr()
    return matrix, np.asarray(lower), np.asarray(upper)
