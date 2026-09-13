"""Deterministic, congestion-independent road-closure disruption.

The public calculation groups shortest-path requests that share an origin.
``reference_closure_disruption`` deliberately retains the former one-query-
per-OD implementation as a small, independent oracle for equivalence tests.
Neither function performs publication or starts SUMO.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET
from typing import Callable, Iterable, Mapping, Optional, Sequence, Union


Adjacency = Mapping[str, Sequence[str]]
Costs = Mapping[str, float]
OdPair = tuple[str, str]
ParsedVehicle = Union[
    tuple[tuple[str, ...], float],
    tuple[tuple[str, ...], float, Optional[str]],
]
RoutedOd = tuple[
    OdPair, OdPair, frozenset, Optional[float], float, float]
ClosureTransitEvent = tuple[str, float]
SPARSE_ROUTING_MIN_PAIRS = 32

# The demand endpoint mapper already accepts a road access within 180 metres
# of a physical home/POI.  A closure must not silently move a destination
# farther than the same established access contract merely to make a route
# feasible.
DESTINATION_ACCESS_RADIUS_M = 180.0


#: Declared upper bound on how long congestion may delay a vehicle beyond its
#: free-flow arrival at a closed edge.
#:
#: This model runs at free flow and therefore cannot MEASURE congestion delay.
#: The predicate below still needs an upper bound on real occupancy, and the
#: previous rule supplied one implicitly: infinity. Every vehicle reaching a
#: closed edge at any time before the window's end was treated as blocked, so a
#: window's cost grew monotonically with its END time rather than with the
#: traffic inside it. Measured on the tracked archive for a fixed two-hour
#: shift on 26355153_96523321_0: a 22:00-24:00 window scored 3025 vehicles as
#: affected where 72 actually cross during it, a 42x overstatement, while the
#: 00:00-02:00 window scored 18 against 18. An unbounded bound is not
#: conservative, it is vacuous -- it asserts a vehicle passing at 00:30 may
#: still occupy the edge at 22:00.
#:
#: One hour is DECLARED, not measured: it is generous against inner-city
#: free-flow trip durations of a few minutes, and it is finite. Studies may
#: vary it through the ``max_assumed_delay_s`` parameter; nothing in the
#: pipeline infers it from data.
MAX_ASSUMED_CONGESTION_DELAY_S = 3600.0


def applicable_closed_edges_from_events(
    events: Sequence[ClosureTransitEvent],
    closures: Sequence[Mapping] | None,
    closed_edges: frozenset[str],
    *,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> frozenset[str]:
    """Return closure edges whose transit cannot be proven safely after closure.

    Each event is ``(edge_id, earliest_occupancy_s)``. Free-flow transit is a
    lower bound on real occupancy; ``max_assumed_delay_s`` supplies the
    matching upper bound, so the vehicle is taken to occupy the edge somewhere
    in ``[occupancy, occupancy + max_assumed_delay_s]``. A window applies when
    that interval overlaps ``[begin_s, end_s)`` -- the vehicle is provably past
    the closure once its LOWER bound reaches ``end_s``, and provably ahead of
    it once its UPPER bound still falls short of ``begin_s``.

    Reading ``begin_s`` is what stops a window being charged for traffic that
    ran long before the roadworks start; see
    ``MAX_ASSUMED_CONGESTION_DELAY_S`` for the measured size of that error.
    A record without ``begin_s`` is read as beginning at 0, which reproduces
    the previous whole-run semantics exactly. Empty/absent windows retain the
    legacy whole-run closure meaning.

    This is the single timing predicate used by deterministic scoring, its
    structural window index, and the pre-SUMO route writer.
    """
    if not closures:
        return frozenset(
            edge for edge, _occupancy in events if edge in closed_edges)
    if not math.isfinite(max_assumed_delay_s) or max_assumed_delay_s < 0:
        raise ValueError("max assumed delay must be finite and non-negative")
    windows_by_edge: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for closure in closures:
        edge_id = str(closure["edge_id"])
        if edge_id in closed_edges:
            windows_by_edge[edge_id].append((
                float(closure.get("begin_s", 0.0) or 0.0),
                float(closure["end_s"]),
            ))
    return frozenset(
        edge
        for edge, occupancy_lower_bound in events
        if edge in closed_edges
        and any(
            occupancy_lower_bound < end_s
            and occupancy_lower_bound + max_assumed_delay_s >= begin_s
            for begin_s, end_s in windows_by_edge.get(edge, ())
        )
    )


def closure_transit_offsets(
    edges: Sequence[str],
    closed_edges: frozenset[str],
    edge_travel_s: Costs,
) -> tuple[tuple[str, float], ...]:
    """Free-flow offsets from departure to each closed edge on ``edges``.

    Occupancy is ``depart_s + offset``, so a route's offsets can be computed
    once and reused for every departure time and every window that route is
    evaluated against. The detour fixed point relies on this to stay cheap.
    """
    elapsed = 0.0
    offsets: list[tuple[str, float]] = []
    for edge in edges:
        if edge in closed_edges:
            offsets.append((edge, elapsed))
        elapsed += float(edge_travel_s.get(edge, 0.0))
    return tuple(offsets)


def applicable_closed_edges(
    edges: Sequence[str],
    depart_s: float,
    closures: Sequence[Mapping] | None,
    closed_edges: frozenset[str],
    edge_travel_s: Costs,
    *,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> frozenset[str]:
    """Build per-edge free-flow occupancy bounds and apply the shared rule."""
    events = [
        (edge, float(depart_s) + offset)
        for edge, offset in closure_transit_offsets(
            edges, closed_edges, edge_travel_s)
    ]
    return applicable_closed_edges_from_events(
        events, closures, closed_edges,
        max_assumed_delay_s=max_assumed_delay_s)


@dataclass(frozen=True)
class DestinationAccess:
    """One legal road position near the original physical destination."""

    edge_id: str
    position_m: float
    distance_m: float


@dataclass(frozen=True)
class ResolvedDestinationAccess:
    """Nearest reachable access and the deterministic route that reaches it."""

    access: DestinationAccess
    route_edges: tuple[str, ...]
    route_cost: float


@dataclass(frozen=True)
class _EdgeGeometry:
    edge_id: str
    points: tuple[tuple[float, float], ...]
    shape_length_m: float
    lane_length_m: float
    bounds: tuple[float, float, float, float]


def _shape_points(raw: str | None) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for token in str(raw or "").split():
        try:
            x_raw, y_raw = token.split(",", 1)
            point = (float(x_raw), float(y_raw))
        except (TypeError, ValueError):
            return ()
        if not all(math.isfinite(value) for value in point):
            return ()
        points.append(point)
    return tuple(points) if len(points) >= 2 else ()


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.hypot(bx - ax, by - ay)
        for (ax, ay), (bx, by) in zip(points, points[1:])
    )


def _point_at_lane_position(
    geometry: _EdgeGeometry, raw_position: str | None,
) -> tuple[float, float] | None:
    """Convert SUMO lane position to one point on the lane polyline."""
    if raw_position in (None, "", "max"):
        lane_position = geometry.lane_length_m
    else:
        try:
            lane_position = float(raw_position)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(lane_position):
            return None
        if lane_position < 0:
            lane_position = geometry.lane_length_m + lane_position
    lane_position = min(max(lane_position, 0.0), geometry.lane_length_m)
    target = (
        geometry.shape_length_m * lane_position / geometry.lane_length_m
        if geometry.lane_length_m > 0 else 0.0
    )
    walked = 0.0
    for (ax, ay), (bx, by) in zip(geometry.points, geometry.points[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length <= 1e-12:
            continue
        if walked + length >= target:
            share = (target - walked) / length
            return ax + share * (bx - ax), ay + share * (by - ay)
        walked += length
    return geometry.points[-1]


def _project_to_geometry(
    point: tuple[float, float], geometry: _EdgeGeometry,
) -> tuple[float, float]:
    """Return ``(lane position, straight access distance)`` in metres."""
    px, py = point
    walked = 0.0
    best_distance = float("inf")
    best_shape_position = 0.0
    for (ax, ay), (bx, by) in zip(geometry.points, geometry.points[1:]):
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        share = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy)
                                  / (length * length)))
        qx, qy = ax + share * dx, ay + share * dy
        distance = math.hypot(px - qx, py - qy)
        if distance < best_distance:
            best_distance = distance
            best_shape_position = walked + share * length
        walked += length
    lane_position = (
        geometry.lane_length_m * best_shape_position / geometry.shape_length_m
        if geometry.shape_length_m > 0 else 0.0
    )
    # SUMO route endpoints exactly at a junction can be insertion/removal
    # hazards. Match the demand endpoint mapper's existing two-metre inset.
    if geometry.lane_length_m > 4.0:
        lane_position = min(max(2.0, lane_position), geometry.lane_length_m - 2.0)
    # The two-metre insertion/removal inset changes the written endpoint.
    # Measure the contract distance to that final point, not to the unclamped
    # projection that is no longer used by the vehicle.
    final_point = _point_at_lane_position(geometry, str(lane_position))
    if final_point is None:
        return lane_position, float("inf")
    return lane_position, math.hypot(px - final_point[0], py - final_point[1])


class DestinationAccessResolver:
    """Find nearby legal arrival edges from the immutable SUMO geometry.

    The original route's numeric ``arrivalPos`` identifies the physical
    destination point even when its final edge is closed.  Candidate road
    positions are projected from that point onto passenger-routable edges in
    the same network.  A small grid avoids scanning the whole network for
    every repeated calibrated vehicle; results are cached by endpoint and
    closed-edge set.
    """

    def __init__(
        self,
        network_path: Path,
        *,
        permitted_edges: Iterable[str],
        radius_m: float = DESTINATION_ACCESS_RADIUS_M,
    ) -> None:
        if not math.isfinite(radius_m) or radius_m <= 0:
            raise ValueError("destination access radius must be positive")
        self.network_path = Path(network_path).resolve()
        self.radius_m = float(radius_m)
        self._geometry: dict[str, _EdgeGeometry] = {}
        self._grid: dict[tuple[int, int], set[str]] = defaultdict(set)
        self._candidate_cache: dict[
            tuple[str, str | None, tuple[str, ...]], tuple[DestinationAccess, ...]
        ] = {}
        permitted = {str(edge) for edge in permitted_edges}
        root = ET.parse(self.network_path).getroot()
        for edge in root.findall("edge"):
            edge_id = str(edge.get("id") or "")
            if (not edge_id or edge.get("function") == "internal"
                    or edge_id.startswith(":")):
                continue
            lanes = edge.findall("lane")
            lane = next((item for item in lanes if item.get("shape")), None)
            if lane is None:
                continue
            # Edge shapes are commonly centre lines shared by opposing
            # directions. Arrival positions live on lanes, so access geometry
            # must use the selected lane's own polyline.
            points = _shape_points(lane.get("shape"))
            try:
                lane_length = float(edge.get("length") or lane.get("length") or 0)
            except (TypeError, ValueError):
                continue
            shape_length = _polyline_length(points)
            if not points or lane_length <= 0 or shape_length <= 0:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            geometry = _EdgeGeometry(
                edge_id=edge_id,
                points=points,
                shape_length_m=shape_length,
                lane_length_m=lane_length,
                bounds=(min(xs), min(ys), max(xs), max(ys)),
            )
            self._geometry[edge_id] = geometry
            if edge_id not in permitted:
                continue
            min_x, min_y, max_x, max_y = geometry.bounds
            for gx in range(math.floor(min_x / self.radius_m),
                            math.floor(max_x / self.radius_m) + 1):
                for gy in range(math.floor(min_y / self.radius_m),
                                math.floor(max_y / self.radius_m) + 1):
                    self._grid[(gx, gy)].add(edge_id)

    def candidates(
        self,
        original_edge: str,
        arrival_pos: str | None,
        banned: frozenset[str],
    ) -> tuple[DestinationAccess, ...]:
        key = (str(original_edge), arrival_pos, tuple(sorted(banned)))
        remembered = self._candidate_cache.get(key)
        if remembered is not None:
            return remembered
        original = self._geometry.get(str(original_edge))
        point = (
            _point_at_lane_position(original, arrival_pos)
            if original is not None else None
        )
        if point is None:
            self._candidate_cache[key] = ()
            return ()
        px, py = point
        nearby: set[str] = set()
        for gx in range(math.floor((px - self.radius_m) / self.radius_m),
                        math.floor((px + self.radius_m) / self.radius_m) + 1):
            for gy in range(math.floor((py - self.radius_m) / self.radius_m),
                            math.floor((py + self.radius_m) / self.radius_m) + 1):
                nearby.update(self._grid.get((gx, gy), ()))
        accesses: list[DestinationAccess] = []
        for edge_id in nearby - set(banned):
            position, distance = _project_to_geometry(point, self._geometry[edge_id])
            if distance <= self.radius_m + 1e-9:
                accesses.append(DestinationAccess(
                    edge_id=edge_id,
                    position_m=round(position, 2),
                    distance_m=round(distance, 2),
                ))
        result = tuple(sorted(
            accesses,
            key=lambda item: (item.distance_m, item.edge_id, item.position_m),
        ))
        self._candidate_cache[key] = result
        return result

    def edge_length_m(self, edge_id: str) -> float | None:
        """Return the lane length used by arrival-position validation."""
        geometry = self._geometry.get(str(edge_id))
        return geometry.lane_length_m if geometry is not None else None

    def access_distance_m(
        self,
        original_edge: str,
        original_arrival_pos: str | None,
        replacement_edge: str,
        replacement_arrival_pos: str | float,
    ) -> float | None:
        """Recompute physical endpoint distance from immutable lane shapes."""
        original = self._geometry.get(str(original_edge))
        replacement = self._geometry.get(str(replacement_edge))
        if original is None or replacement is None:
            return None
        original_point = _point_at_lane_position(
            original, original_arrival_pos)
        replacement_point = _point_at_lane_position(
            replacement, str(replacement_arrival_pos))
        if original_point is None or replacement_point is None:
            return None
        return math.hypot(
            original_point[0] - replacement_point[0],
            original_point[1] - replacement_point[1])

    def resolve(
        self,
        origin: str,
        original_edge: str,
        arrival_pos: str | None,
        banned: frozenset[str],
        adjacency: Adjacency,
        costs: Costs,
    ) -> ResolvedDestinationAccess | None:
        """Return the physically nearest reachable legal access point."""
        best_distance: float | None = None
        reachable: list[ResolvedDestinationAccess] = []
        for access in self.candidates(original_edge, arrival_pos, banned):
            if best_distance is not None and access.distance_m > best_distance:
                break
            path = shortest_path_edges(
                adjacency, costs, origin, access.edge_id, banned)
            if path is None:
                continue
            cost = sum(float(costs[edge]) for edge in path[1:])
            best_distance = access.distance_m
            reachable.append(ResolvedDestinationAccess(
                access=access, route_edges=tuple(path), route_cost=cost))
        return min(
            reachable,
            key=lambda item: (
                item.access.distance_m,
                item.route_cost,
                item.access.edge_id,
                item.access.position_m,
            ),
            default=None,
        )


#: Reasons a calibrated vehicle cannot keep its route under a closure.
#: Owned here because BOTH the pre-SUMO route writer and the deterministic
#: cost now reach them through the one shared decision below;
#: `closure_routing` re-exports them for its evidence schema.
DESTINATION_CLOSED = "destination_closed"
NO_LEGAL_PATH = "no_legal_path"


@dataclass(frozen=True)
class VehicleClosureOutcome:
    """What one vehicle actually does under a closure.

    This is the record the reviewer's finding asks for: route, destination
    and applicable closures decided ONCE and handed to both the cost and the
    writer, so the two cannot disagree about whether a detour exists.
    """

    origin: str
    original_destination: str
    destination: str
    applicable: frozenset[str]
    banned: frozenset[str]
    route: tuple[str, ...] | None
    reason: str | None
    access: DestinationAccess | None
    endpoint_seconds: float
    endpoint_metres: float

    @property
    def denied(self) -> bool:
        return self.reason is not None


class ClosureRouteResolver:
    """The single per-vehicle closure decision, shared by cost and writer.

    WHY THIS EXISTS (review finding 3). The deterministic cost used to price
    every detour against the WHOLE closed-edge set while the route writer
    built a per-vehicle banned set by fixed point. The two therefore answered
    different questions whenever a multi-edge closure had per-edge windows:
    the writer could route a vehicle through an edge that had already
    reopened, while the cost recorded the same vehicle as stranded. Both now
    call `resolve`, so the population SUMO runs is the population the ledger
    prices, edge for edge and route for route.

    It also fixes review finding 1. Time and distance are measured along the
    SAME concrete route rather than by two independent shortest-path searches
    with different cost functions -- see `path_cost`.

    Every expensive step is memoised on facts that do not depend on departure
    time (paths by origin/destination/banned, closure offsets by route), so
    the per-vehicle work left in a window is arithmetic over the handful of
    closed edges a route actually touches.
    """

    def __init__(
        self,
        adjacency: Adjacency,
        edge_time: Costs,
        edge_length: Costs | None,
        closed_edges: frozenset[str],
        *,
        destination_access: "DestinationAccessResolver | None" = None,
        max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
    ) -> None:
        # `edge_length` is optional because the route writer needs routes and
        # reasons but never metres. Asking it for a length it does not have
        # fails loudly in `path_cost` rather than silently costing zero.
        self.adjacency = adjacency
        self.edge_time = edge_time
        self.edge_length = edge_length
        self.closed_edges = frozenset(closed_edges)
        self.destination_access = destination_access
        self.max_assumed_delay_s = float(max_assumed_delay_s)
        self._paths: dict[
            tuple[str, str, frozenset[str]], tuple[str, ...] | None] = {}
        self._costs: dict[
            tuple[str, str, frozenset[str]], tuple[float, float] | None] = {}
        self._offsets: dict[
            tuple[str, ...], tuple[tuple[str, float], ...]] = {}

    def path(
        self, origin: str, destination: str, banned: frozenset[str],
    ) -> tuple[str, ...] | None:
        """Memoised deterministic fastest legal route, or None."""
        key = (origin, destination, banned)
        if key not in self._paths:
            found = shortest_path_edges(
                self.adjacency, self.edge_time, origin, destination, banned)
            self._paths[key] = tuple(found) if found is not None else None
        return self._paths[key]

    def path_cost(
        self, origin: str, destination: str, banned: frozenset[str],
    ) -> tuple[float, float] | None:
        """(seconds, metres) along ONE route -- never two separate searches.

        Pricing time on the fastest path and distance on the SHORTEST path
        made the two answers describe different journeys. When a closure
        removed the fast route but left an equally short one, the metre
        difference was zero by construction while real time had been added,
        and the caller's coherence rule then discarded both. Measured on the
        tracked archive, 182 of 400 swept closures lost real added time this
        way, and the six cheapest-ranked candidates all reported exactly zero
        against a true cost of up to 8.59 vehicle-hours.

        Both figures are now read off the same edge sequence. The summation
        excludes the source edge, matching `shortest_path_cost`'s convention
        that a vehicle already standing on an edge does not enter it.
        """
        if self.edge_length is None:
            raise ValueError(
                "this resolver was built without edge lengths and cannot "
                "price distance")
        key = (origin, destination, banned)
        if key not in self._costs:
            route = self.path(origin, destination, banned)
            if route is None:
                self._costs[key] = None
            else:
                self._costs[key] = (
                    sum(float(self.edge_time[edge]) for edge in route[1:]),
                    sum(float(self.edge_length[edge]) for edge in route[1:]),
                )
        return self._costs[key]

    def offsets(self, route: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
        """Memoised free-flow offsets to each closed edge on ``route``."""
        if route not in self._offsets:
            self._offsets[route] = closure_transit_offsets(
                route, self.closed_edges, self.edge_time)
        return self._offsets[route]

    def applicable_on(
        self,
        route: Sequence[str],
        depart_s: float,
        closures: Sequence[Mapping] | None,
    ) -> frozenset[str]:
        """Which closures actually reach this vehicle on this route."""
        events = [
            (edge, float(depart_s) + offset)
            for edge, offset in self.offsets(tuple(route))
        ]
        return applicable_closed_edges_from_events(
            events, closures, self.closed_edges,
            max_assumed_delay_s=self.max_assumed_delay_s)

    def plan(
        self,
        origin: str,
        destination: str,
        depart_s: float,
        closures: Sequence[Mapping] | None,
        initial_banned: frozenset[str],
    ) -> tuple[tuple[str, ...] | None, frozenset[str], str | None]:
        """Fixed point: reroute, re-check, repeat until ``banned`` stabilises.

        `banned` only ever grows and is bounded by `closed_edges`, so at most
        `len(closed_edges)` growth steps can occur; the `+ 1` is the final
        stability check, not an arbitrary cutoff.
        """
        banned = initial_banned
        for _ in range(len(self.closed_edges) + 1):
            route = self.path(origin, destination, banned)
            if route is None:
                return None, banned, NO_LEGAL_PATH
            combined = banned | self.applicable_on(route, depart_s, closures)
            if combined == banned:
                return route, banned, None
            banned = combined
        return None, banned, NO_LEGAL_PATH

    def _relocate(
        self,
        origin: str,
        destination: str,
        arrival_pos: str | None,
        depart_s: float,
        closures: Sequence[Mapping] | None,
        banned: frozenset[str],
    ) -> tuple[tuple[str, ...], frozenset[str], DestinationAccess] | None:
        """Nearest reachable open access to a closed destination, or None.

        `banned` is the exclusion set the search starts from, so this works
        both for a destination that was closed from the outset and for one
        discovered mid-replanning, where `banned` has already grown.
        """
        if self.destination_access is None:
            return None
        best: tuple | None = None
        nearest: float | None = None
        for access in self.destination_access.candidates(
                destination, arrival_pos, banned):
            if nearest is not None and access.distance_m > nearest:
                break
            route, final_banned, reason = self.plan(
                origin, access.edge_id, depart_s, closures, banned)
            if reason is not None or route is None:
                continue
            nearest = access.distance_m
            cost = sum(float(self.edge_time[edge]) for edge in route[1:])
            key = (access.distance_m, cost, access.edge_id, access.position_m)
            if best is None or key < best[0]:
                best = (key, route, final_banned, access)
        if best is None:
            return None
        _key, route, final_banned, access = best
        return route, final_banned, access

    def _relocated(
        self,
        origin: str,
        destination: str,
        arrival_pos: str | None,
        applicable: frozenset[str],
        relocation: tuple[tuple[str, ...], frozenset[str], DestinationAccess],
    ) -> VehicleClosureOutcome:
        route, banned, access = relocation
        endpoint_seconds = endpoint_metres = 0.0
        if self.edge_length is not None:
            endpoint_seconds, endpoint_metres = (
                _relocation_endpoint_adjustment(
                    destination, arrival_pos, access,
                    self.edge_time, self.edge_length))
        return VehicleClosureOutcome(
            origin=origin, original_destination=destination,
            destination=access.edge_id, applicable=applicable, banned=banned,
            route=route, reason=None, access=access,
            endpoint_seconds=endpoint_seconds, endpoint_metres=endpoint_metres)

    def resolve(
        self,
        edges: Sequence[str],
        depart_s: float,
        arrival_pos: str | None,
        closures: Sequence[Mapping] | None,
    ) -> VehicleClosureOutcome | None:
        """Decide one vehicle, or None when the closure never reaches it."""
        if not self.closed_edges.intersection(edges):
            return None
        applicable = self.applicable_on(edges, depart_s, closures)
        if not applicable:
            return None

        # Branch order is the route writer's, deliberately: a vehicle whose
        # origin is itself closed falls out of `plan` as NO_LEGAL_PATH rather
        # than being classified here, so both consumers keep the reason they
        # already record. The cost path classifies denied departures by
        # testing `origin in applicable` itself, before calling this.
        origin, destination = edges[0], edges[-1]
        if destination in applicable:
            relocation = self._relocate(
                origin, destination, arrival_pos, depart_s, closures,
                applicable)
            if relocation is None:
                return VehicleClosureOutcome(
                    origin=origin, original_destination=destination,
                    destination=destination, applicable=applicable,
                    banned=applicable, route=None, reason=DESTINATION_CLOSED,
                    access=None, endpoint_seconds=0.0, endpoint_metres=0.0)
            return self._relocated(
                origin, destination, arrival_pos, applicable, relocation)

        route, banned, reason = self.plan(
            origin, destination, depart_s, closures, applicable)
        if reason is not None and destination in banned:
            # The destination's own window was not reachable on the original
            # route but IS on the detour, because the detour arrives later.
            # Discovering that mid-replanning is the same physical situation
            # as a destination closed from the outset -- the driver parks at
            # the nearest open access and walks -- so it must offer the same
            # remedy rather than deny the trip outright. Denying it erased
            # the vehicle's contribution to every other edge on its route,
            # which is the exact mistake `truncate_stranded_vehicles` was
            # written to avoid.
            relocation = self._relocate(
                origin, destination, arrival_pos, depart_s, closures, banned)
            if relocation is not None:
                return self._relocated(
                    origin, destination, arrival_pos, applicable, relocation)
            # The destination is what is shut, and nothing near it is
            # reachable. Report that, not a generic routing failure.
            reason = DESTINATION_CLOSED
        return VehicleClosureOutcome(
            origin=origin, original_destination=destination,
            destination=destination, applicable=applicable, banned=banned,
            route=route, reason=reason, access=None,
            endpoint_seconds=0.0, endpoint_metres=0.0)


def _vehicle_parts(
    vehicle: ParsedVehicle,
) -> tuple[tuple[str, ...], float, str | None]:
    """Accept old two-field fixtures and new arrival-position-aware facts."""
    if len(vehicle) == 2:
        edges, depart = vehicle
        return edges, depart, None
    edges, depart, arrival_pos = vehicle
    return edges, depart, arrival_pos


def _normalised_arrival_position(
    raw_position: str | float | None,
    edge_length_m: float,
) -> float:
    """Resolve SUMO arrivalPos to metres from the lane start.

    SUMO's default/``max`` is the lane end, negative values count backwards
    from the end, and out-of-range numeric positions are clipped to the nearest
    edge border. ``random`` cannot support deterministic endpoint costing and
    therefore fails closed if it ever reaches this path.
    """
    length = float(edge_length_m)
    if not math.isfinite(length) or length <= 0:
        raise ValueError("arrival edge length must be positive and finite")
    if raw_position in (None, "", "max"):
        return length
    try:
        position = float(raw_position)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"non-deterministic or invalid arrivalPos {raw_position!r}"
        ) from error
    if not math.isfinite(position):
        raise ValueError("arrivalPos must be finite")
    if position < 0:
        position = length + position
    return min(max(position, 0.0), length)


def _relocation_endpoint_adjustment(
    original_edge: str,
    original_arrival_pos: str | None,
    replacement: DestinationAccess,
    edge_time: Costs,
    edge_length: Costs,
) -> tuple[float, float]:
    """Correct full-final-edge deltas to the two actual arrival positions.

    Shortest-path pricing includes each destination edge in full. The returned
    adjustments implement, for time, exactly
    ``-(L_new-pos_new)/v_new + (L_old-pos_old)/v_old``; the metre correction is
    the same expression without division by speed.
    """
    old_length = float(edge_length[original_edge])
    new_length = float(edge_length[replacement.edge_id])
    old_position = _normalised_arrival_position(
        original_arrival_pos, old_length)
    new_position = _normalised_arrival_position(
        replacement.position_m, new_length)
    old_full_time = float(edge_time[original_edge])
    new_full_time = float(edge_time[replacement.edge_id])
    if (not math.isfinite(old_full_time) or old_full_time <= 0
            or not math.isfinite(new_full_time) or new_full_time <= 0):
        raise ValueError("arrival edge travel time must be positive and finite")
    old_tail_m = old_length - old_position
    new_tail_m = new_length - new_position
    seconds = (
        old_tail_m * old_full_time / old_length
        - new_tail_m * new_full_time / new_length
    )
    return seconds, old_tail_m - new_tail_m


def shortest_path_edges(
    adjacency: Adjacency,
    costs: Costs,
    source: str,
    destination: str,
    banned: frozenset[str],
) -> list[str] | None:
    """Deterministic fastest legal edge path ``source``->``destination``.

    Same Dijkstra as `shortest_path_cost` (identical `seen`/`spent`/`queue`
    shape, so the two can never silently diverge on cost), extended with
    predecessor tracking so the caller gets the actual edge sequence, not
    just its price. Used by `closure_routing` to rewrite a vehicle's route
    around a closure BEFORE simulation, so ranking/disruption scoring and the
    routes actually handed to SUMO are computed by the exact same engine and
    can never disagree about what is reachable.

    `source`/`destination` and every edge visited must be present as keys in
    `costs` to be entered (the same rule `shortest_path_cost` applies to
    successors); `source` itself is exempt, matching `reachable()`'s
    convention that a vehicle already standing on an edge does not need to
    "enter" it. `source`/`destination` in `banned` fails closed: a route
    cannot legally start or end on a closed edge.

    Deterministic tie-breaking: `heapq` compares `(cost, edge_id)` tuples, so
    equal-cost alternatives are always resolved by edge-id string order, the
    same convention `shortest_path_cost` and `grouped_path_costs` already
    rely on.
    """
    if source in banned or destination in banned:
        return None
    if source == destination:
        return [source]
    seen = {source: 0.0}
    prev: dict[str, str] = {}
    queue = [(0.0, source)]
    while queue:
        spent, edge = heapq.heappop(queue)
        if edge == destination:
            path = [destination]
            while path[-1] != source:
                path.append(prev[path[-1]])
            path.reverse()
            return path
        if spent > seen.get(edge, float("inf")):
            continue
        for successor in adjacency.get(edge, ()):
            if successor in banned or successor not in costs:
                continue
            through = spent + costs[successor]
            if through < seen.get(successor, float("inf")):
                seen[successor] = through
                prev[successor] = edge
                heapq.heappush(queue, (through, successor))
    return None


def shortest_path_cost(
    adjacency: Adjacency,
    costs: Costs,
    source: str,
    destination: str,
    banned: frozenset[str],
) -> float | None:
    """Former single-pair Dijkstra semantics, retained for the oracle."""
    if source == destination:
        return 0.0
    seen = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        spent, edge = heapq.heappop(queue)
        if edge == destination:
            return spent
        if spent > seen.get(edge, float("inf")):
            continue
        for successor in adjacency.get(edge, ()):
            if successor in banned or successor not in costs:
                continue
            through = spent + costs[successor]
            if through < seen.get(successor, float("inf")):
                seen[successor] = through
                heapq.heappush(queue, (through, successor))
    return None


def _shortest_path_costs(
    adjacency: Adjacency,
    costs: Costs,
    source: str,
    destinations: Iterable[str],
    banned: frozenset[str],
) -> dict[str, float | None]:
    """Resolve all requested destinations in one Dijkstra traversal."""
    requested = set(destinations)
    resolved: dict[str, float | None] = {}
    if source in requested:
        resolved[source] = 0.0
        requested.remove(source)
    if not requested:
        return resolved

    seen = {source: 0.0}
    queue = [(0.0, source)]
    while queue and requested:
        spent, edge = heapq.heappop(queue)
        if spent > seen.get(edge, float("inf")):
            continue
        if edge in requested:
            resolved[edge] = spent
            requested.remove(edge)
            if not requested:
                break
        for successor in adjacency.get(edge, ()):
            if successor in banned or successor not in costs:
                continue
            through = spent + costs[successor]
            if through < seen.get(successor, float("inf")):
                seen[successor] = through
                heapq.heappush(queue, (through, successor))
    resolved.update((destination, None) for destination in requested)
    return resolved


def grouped_path_costs(
    pairs: Iterable[OdPair],
    adjacency: Adjacency,
    costs: Costs,
    banned: frozenset[str],
) -> dict[OdPair, float | None]:
    """Price OD pairs with one traversal per distinct origin.

    SUMO's own bulk-routing option uses the same common-origin grouping. This
    pure-Python form keeps the exact existing graph and cost semantics, avoids
    subprocess overhead, and is easy to compare against the retained oracle.
    """
    by_origin: dict[str, set[str]] = defaultdict(set)
    for source, destination in pairs:
        by_origin[source].add(destination)

    result: dict[OdPair, float | None] = {}
    for source in sorted(by_origin):
        destinations = by_origin[source]
        costs_from_source = _shortest_path_costs(
            adjacency, costs, source, destinations, banned
        )
        result.update(
            ((source, destination), costs_from_source[destination])
            for destination in destinations
        )
    return result


class SparsePathBatch:
    """Reusable sparse graph structure for several cost/banned combinations.

    SciPy is already a project dependency for calibration. Keeping the import
    lazy makes the pure-Python path usable in small tools and gives a safe
    fallback if a reduced runtime environment omits SciPy.
    """

    def __init__(self, pairs: Iterable[OdPair], adjacency: Adjacency) -> None:
        self.pairs = tuple(dict.fromkeys(pairs))
        pair_nodes = {node for pair in self.pairs for node in pair}
        graph_nodes = set(adjacency)
        graph_nodes.update(
            successor
            for successors in adjacency.values()
            for successor in successors
        )
        self.nodes = tuple(sorted(graph_nodes | pair_nodes))
        self.index = {node: position
                      for position, node in enumerate(self.nodes)}
        # Metadata can contain duplicate connections. A sparse constructor
        # would SUM duplicate weights, changing the graph, so deduplicate the
        # transitions explicitly.
        self.transitions = tuple(sorted({
            (source, successor)
            for source, successors in adjacency.items()
            for successor in successors
        }))
        self.sources = tuple(sorted({source for source, _ in self.pairs}))

    def path_costs(
        self,
        costs: Costs,
        banned: frozenset[str],
    ) -> dict[OdPair, float | None]:
        """Return pair costs through SciPy, or the exact Python fallback."""
        try:
            import numpy as np
            from scipy.sparse import csr_matrix
            from scipy.sparse.csgraph import dijkstra
        except ImportError:
            # This branch is intentionally rare but correctness-preserving.
            adjacency: dict[str, list[str]] = defaultdict(list)
            for source, successor in self.transitions:
                adjacency[source].append(successor)
            return grouped_path_costs(
                self.pairs, adjacency, costs, banned)

        rows: list[int] = []
        columns: list[int] = []
        values: list[float] = []
        for source, successor in self.transitions:
            if successor in banned or successor not in costs:
                continue
            rows.append(self.index[source])
            columns.append(self.index[successor])
            values.append(float(costs[successor]))
        graph = csr_matrix(
            (values, (rows, columns)),
            shape=(len(self.nodes), len(self.nodes)),
            dtype=float,
        )
        distances = dijkstra(
            graph,
            directed=True,
            indices=[self.index[source] for source in self.sources],
            return_predecessors=False,
        )
        if distances.ndim == 1:
            distances = distances.reshape(1, -1)
        source_row = {source: row
                      for row, source in enumerate(self.sources)}
        result: dict[OdPair, float | None] = {}
        for pair in self.pairs:
            value = float(distances[
                source_row[pair[0]], self.index[pair[1]]
            ])
            result[pair] = value if np.isfinite(value) else None
        return result


def parse_route_vehicles(
    route_path: Path,
    *,
    timing: Callable[[str, float], None] | None = None,
) -> tuple[ParsedVehicle, ...]:
    """Parse one immutable route XML file into reusable vehicle facts.

    The deterministic monthly ledger evaluates many windows over the same
    archive.  Keeping this representation separate from a window makes the
    index builder pay XML parsing once per archive/variant, while preserving
    the exact vehicle ordering and route semantics of the XML implementation.
    """
    started = time.perf_counter()
    root = ET.parse(Path(route_path)).getroot()
    vehicles: list[ParsedVehicle] = []
    for vehicle in root.iter("vehicle"):
        route = vehicle.find("route")
        if route is None or not route.get("edges"):
            continue
        vehicles.append((
            tuple(route.get("edges").split()),
            float(vehicle.get("depart", "0")),
            vehicle.get("arrivalPos"),
        ))
    if timing is not None:
        timing("xml_parse", time.perf_counter() - started)
    return tuple(vehicles)


def _movement(outcome: VehicleClosureOutcome) -> RoutedOd:
    """The costed identity of one routed vehicle.

    Carries the vehicle's OWN banned set, so two vehicles sharing an origin
    and destination but subject to different closure windows are priced as
    the different journeys they are.
    """
    return (
        (outcome.origin, outcome.original_destination),
        (outcome.origin, outcome.destination),
        outcome.banned,
        outcome.access.distance_m if outcome.access is not None else None,
        outcome.endpoint_seconds,
        outcome.endpoint_metres,
    )


def _classify(
    outcome: VehicleClosureOutcome | None,
) -> tuple[bool, bool, bool, RoutedOd | None]:
    """(affected, denied_departure, severed, movement) for one vehicle."""
    if outcome is None:
        return False, False, False, None
    if outcome.origin in outcome.applicable:
        # No partial trip exists when the closed edge is the first one.
        return True, True, False, None
    if outcome.route is None:
        return True, False, True, None
    return True, False, False, _movement(outcome)


def _affected_od_counts(
    route_path: Path | None,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    timing: Callable[[str, float], None] | None = None,
    parsed_vehicles: Sequence[ParsedVehicle] | None = None,
    adjacency: Adjacency | None = None,
    destination_access: DestinationAccessResolver | None = None,
    resolver: ClosureRouteResolver | None = None,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> tuple[int, int, int, int, Counter[RoutedOd]]:
    """Return considered, affected, denied, severed and routed multiplicities."""
    considered = affected = denied = severed = 0
    movements: Counter[RoutedOd] = Counter()
    if parsed_vehicles is None:
        if route_path is None:
            raise ValueError("route_path is required without parsed vehicles")
        parsed_vehicles = parse_route_vehicles(route_path, timing=timing)
    grouped_at = time.perf_counter()
    if resolver is None:
        if adjacency is None:
            raise ValueError("closure costing requires adjacency")
        resolver = ClosureRouteResolver(
            adjacency, edge_time, edge_length, frozenset(closed_edges),
            destination_access=destination_access,
            max_assumed_delay_s=max_assumed_delay_s)
    for vehicle in parsed_vehicles:
        edges, depart, arrival_pos = _vehicle_parts(vehicle)
        considered += 1
        is_affected, is_denied, is_severed, movement = _classify(
            resolver.resolve(edges, depart, arrival_pos, closures))
        affected += int(is_affected)
        denied += int(is_denied)
        severed += int(is_severed)
        if movement is not None:
            movements[movement] += 1
    if timing is not None:
        timing("route_vehicle_grouping", time.perf_counter() - grouped_at)
    return considered, affected, denied, severed, movements


def _report(
    *,
    considered: int,
    affected: int,
    denied: int,
    severed: int,
    od_counts: Counter[RoutedOd],
    pricer: ClosureRouteResolver,
    assumed_delay_s: float,
    timing: Callable[[str, float], None] | None = None,
) -> dict:
    aggregation_at = time.perf_counter()
    severed_destination = severed
    added_seconds: list[float] = []
    added_metres: list[float] = []
    destination_relocated = 0
    relocation_distances: list[float] = []
    for movement, multiplicity in od_counts.items():
        (baseline_pair, detour_pair, banned, relocation_distance_m,
         endpoint_seconds, endpoint_metres) = movement
        baseline = pricer.path_cost(*baseline_pair, frozenset())
        detour = pricer.path_cost(*detour_pair, banned)
        if baseline is None:
            continue
        if detour is None:
            severed_destination += multiplicity
            continue
        if relocation_distance_m is not None:
            destination_relocated += multiplicity
            relocation_distances.extend([relocation_distance_m] * multiplicity)
        # Both figures come off the SAME route (see `path_cost`), so a
        # detour that is slower but shorter -- an ordinary outcome, measured
        # at +7.0 s and -197.3 m on the tracked archive -- keeps its real
        # added time instead of having it discarded for being "incoherent"
        # with a distance that was never measured on the same journey.
        # Each currency is floored at zero independently because both are
        # reported as ADDED quantities and the ranking contract forbids
        # negative fields.
        seconds = max(detour[0] - baseline[0] + endpoint_seconds, 0.0)
        metres = max(detour[1] - baseline[1] + endpoint_metres, 0.0)
        added_seconds.extend([seconds] * multiplicity)
        added_metres.extend([metres] * multiplicity)

    def upper_median(values: list[float]) -> float:
        return round(sorted(values)[len(values) // 2], 1) if values else 0.0

    no_detour = denied + severed_destination
    result = {
        "vehicles_affected": affected,
        "vehicles_considered": considered,
        "vehicles_no_detour": no_detour,
        "vehicles_denied_departure": denied,
        "vehicles_severed_destination": severed_destination,
        "vehicles_destination_relocated": destination_relocated,
        "destination_relocation_metres_total": round(
            sum(relocation_distances), 1),
        "destination_relocation_metres_max": round(
            max(relocation_distances, default=0.0), 1),
        "added_vehicle_hours": round(sum(added_seconds) / 3600.0, 4),
        "added_metres_total": round(sum(added_metres), 1),
        "added_seconds_median": upper_median(added_seconds),
        "added_metres_median": upper_median(added_metres),
        "basis": "calibrated baseline routes; cheapest legal path with vs "
                 "without the closure, free-flow cost",
        "congestion_model": None,
        "capacity_model": None,
        "lane_count_used": False,
        # The DECLARED congestion-delay bound this number was produced under.
        # It decides which vehicles a window reaches, so a cost is only
        # comparable to another cost carrying the same value. Recorded here
        # so a reader never has to infer it from the code that ran.
        "assumed_congestion_delay_s": float(assumed_delay_s),
    }
    if timing is not None:
        timing("window_aggregation", time.perf_counter() - aggregation_at)
    return result


class ParsedWindowCostIndex:
    """Reusable exact costs for one parsed archive/variant.

    The route archive and closure edge set are immutable for all daily units
    backed by an archive.  Precompute each vehicle's free-flow offsets to the
    closed edges it crosses, then let a daily window apply the shared
    per-vehicle decision and aggregate. Routes, their prices and their
    closure offsets are memoised inside the shared resolver, so repeated
    windows re-use every shortest path rather than recomputing it.

    The detour a window prices is now the detour the route writer would
    actually publish for that same vehicle, because both call
    `ClosureRouteResolver.resolve`.
    """

    def __init__(
        self,
        parsed_vehicles: Sequence[ParsedVehicle],
        closed_edges: set[str],
        edge_time: Costs,
        edge_length: Costs,
        *,
        adjacency: Adjacency,
        destination_access: DestinationAccessResolver | None = None,
        timing: Callable[[str, float], None] | None = None,
        max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
    ) -> None:
        if not closed_edges:
            raise ValueError("a parsed window index needs closed edges")
        self._vehicles = tuple(parsed_vehicles)
        self._closed_edges = frozenset(closed_edges)
        self._resolver = ClosureRouteResolver(
            adjacency, edge_time, edge_length, self._closed_edges,
            destination_access=destination_access,
            max_assumed_delay_s=max_assumed_delay_s)
        grouping_at = time.perf_counter()
        # Departure-independent: computed once per archive, reused by every
        # window this index serves.
        self._crossing = tuple(
            vehicle for vehicle in self._vehicles
            if self._closed_edges.intersection(_vehicle_parts(vehicle)[0])
        )
        for vehicle in self._crossing:
            self._resolver.offsets(tuple(_vehicle_parts(vehicle)[0]))
        if timing is not None:
            timing("route_vehicle_grouping", time.perf_counter() - grouping_at)

    def disruption(
        self,
        closures: Sequence[Mapping],
        *,
        timing: Callable[[str, float], None] | None = None,
    ) -> dict | None:
        """Aggregate one exact daily window from the precomputed facts."""
        aggregation_at = time.perf_counter()
        affected = denied = severed = 0
        od_counts: Counter[RoutedOd] = Counter()
        for vehicle in self._crossing:
            edges, depart, arrival_pos = _vehicle_parts(vehicle)
            is_affected, is_denied, is_severed, movement = _classify(
                self._resolver.resolve(edges, depart, arrival_pos, closures))
            affected += int(is_affected)
            denied += int(is_denied)
            severed += int(is_severed)
            if movement is not None:
                od_counts[movement] += 1
        report = _report(
            considered=len(self._vehicles), affected=affected, denied=denied,
            severed=severed, od_counts=od_counts, pricer=self._resolver,
            assumed_delay_s=self._resolver.max_assumed_delay_s)
        if timing is not None:
            timing("window_aggregation", time.perf_counter() - aggregation_at)
        return report


def build_parsed_window_cost_index(
    parsed_vehicles: Sequence[ParsedVehicle],
    closed_edges: set[str],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    destination_access: DestinationAccessResolver | None = None,
    timing: Callable[[str, float], None] | None = None,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> ParsedWindowCostIndex:
    """Build the reusable exact archive/variant window-cost structure."""
    return ParsedWindowCostIndex(
        parsed_vehicles, closed_edges, edge_time, edge_length,
        adjacency=adjacency, destination_access=destination_access,
        timing=timing, max_assumed_delay_s=max_assumed_delay_s)


def closure_disruption(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    destination_access: DestinationAccessResolver | None = None,
    timing: Callable[[str, float], None] | None = None,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> dict | None:
    """Exact disruption for one route file under one closure."""
    route_path = Path(route_path)
    if not closed_edges or not route_path.exists():
        return None
    resolver = ClosureRouteResolver(
        adjacency, edge_time, edge_length, frozenset(closed_edges),
        destination_access=destination_access,
        max_assumed_delay_s=max_assumed_delay_s)
    considered, affected, denied, severed, od_counts = _affected_od_counts(
        route_path, closed_edges, closures, edge_time, edge_length,
        timing=timing, resolver=resolver,
    )
    routing_at = time.perf_counter()
    report = _report(
        considered=considered, affected=affected, denied=denied,
        severed=severed, od_counts=od_counts, pricer=resolver,
        assumed_delay_s=resolver.max_assumed_delay_s, timing=timing,
    )
    if timing is not None:
        timing("shortest_path_detour", time.perf_counter() - routing_at)
    return report


def closure_disruption_from_parsed_vehicles(
    parsed_vehicles: Sequence[ParsedVehicle],
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    destination_access: DestinationAccessResolver | None = None,
    timing: Callable[[str, float], None] | None = None,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> dict | None:
    """Exact disruption calculation over a previously parsed route file.

    This is the compatibility wrapper for the reusable structural index.  The
    index precomputes route crossing events once, then applies the shared
    per-vehicle decision and aggregation. Consequently the result is
    equivalent to :func:`closure_disruption`, but cannot reuse a cost answer
    from the oracle.
    """
    if not closed_edges:
        return None
    index = build_parsed_window_cost_index(
        parsed_vehicles, closed_edges, edge_time, edge_length,
        adjacency=adjacency, destination_access=destination_access,
        timing=timing, max_assumed_delay_s=max_assumed_delay_s)
    return index.disruption(closures, timing=timing)


def reference_closure_disruption(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    destination_access: DestinationAccessResolver | None = None,
    max_assumed_delay_s: float = MAX_ASSUMED_CONGESTION_DELAY_S,
) -> dict | None:
    """Former per-OD algorithm, retained only as an equivalence oracle.

    Independence is preserved where it matters: this path prices every pair
    with its own un-memoised `shortest_path_edges` call rather than reading
    the resolver's cache, so a caching or grouping defect there cannot hide
    behind an oracle that shares it.
    """
    route_path = Path(route_path)
    if not closed_edges or not route_path.exists():
        return None
    resolver = ClosureRouteResolver(
        adjacency, edge_time, edge_length, frozenset(closed_edges),
        destination_access=destination_access,
        max_assumed_delay_s=max_assumed_delay_s)
    considered, affected, denied, severed, od_counts = _affected_od_counts(
        route_path, closed_edges, closures, edge_time, edge_length,
        resolver=resolver,
    )

    class _OraclePricer:
        """Price each pair independently, without the resolver's memo."""

        @staticmethod
        def path_cost(origin, destination, banned):
            route = shortest_path_edges(
                adjacency, edge_time, origin, destination, banned)
            if route is None:
                return None
            return (
                sum(float(edge_time[edge]) for edge in route[1:]),
                sum(float(edge_length[edge]) for edge in route[1:]),
            )

    return _report(
        considered=considered, affected=affected, denied=denied,
        severed=severed, od_counts=od_counts, pricer=_OraclePricer(),
        assumed_delay_s=max_assumed_delay_s,
    )
