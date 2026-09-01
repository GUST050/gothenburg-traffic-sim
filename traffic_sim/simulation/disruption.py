"""Deterministic, congestion-independent road-closure disruption.

The public calculation groups shortest-path requests that share an origin.
``reference_closure_disruption`` deliberately retains the former one-query-
per-OD implementation as a small, independent oracle for equivalence tests.
Neither function performs publication or starts SUMO.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import heapq
from pathlib import Path
import time
import xml.etree.ElementTree as ET
from typing import Callable, Iterable, Mapping, Sequence


Adjacency = Mapping[str, Sequence[str]]
Costs = Mapping[str, float]
OdPair = tuple[str, str]
ParsedVehicle = tuple[tuple[str, ...], float]
SPARSE_ROUTING_MIN_PAIRS = 32


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
        vehicles.append((tuple(route.get("edges").split()),
                         float(vehicle.get("depart", "0"))))
    if timing is not None:
        timing("xml_parse", time.perf_counter() - started)
    return tuple(vehicles)


def _affected_od_counts(
    route_path: Path | None,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    *,
    timing: Callable[[str, float], None] | None = None,
    parsed_vehicles: Sequence[ParsedVehicle] | None = None,
) -> tuple[int, int, int, Counter[OdPair]]:
    """Return considered, affected, denied and affected OD multiplicities."""
    windows = [
        (float(closure["begin_s"]), float(closure["end_s"]))
        for closure in closures
        if closure.get("begin_s") is not None
        and closure.get("end_s") is not None
    ]
    considered = affected = denied = 0
    pairs: Counter[OdPair] = Counter()
    if parsed_vehicles is None:
        if route_path is None:
            raise ValueError("route_path is required without parsed vehicles")
        parsed_vehicles = parse_route_vehicles(route_path, timing=timing)
    grouped_at = time.perf_counter()
    for edges, depart in parsed_vehicles:
        considered += 1
        clock = depart
        struck = False
        for edge in edges:
            if edge in closed_edges:
                if not windows or any(begin <= clock < end
                                      for begin, end in windows):
                    struck = True
                    break
            clock += edge_time.get(edge, 0.0)
        if not struck:
            continue
        affected += 1
        if edges[0] in closed_edges:
            denied += 1
            continue
        pairs[(edges[0], edges[-1])] += 1
    if timing is not None:
        timing("route_vehicle_grouping", time.perf_counter() - grouped_at)
    return considered, affected, denied, pairs


def _report(
    *,
    considered: int,
    affected: int,
    denied: int,
    od_counts: Counter[OdPair],
    base_time: Mapping[OdPair, float | None],
    base_length: Mapping[OdPair, float | None],
    detour_time: Mapping[OdPair, float | None],
    detour_length: Mapping[OdPair, float | None],
    timing: Callable[[str, float], None] | None = None,
) -> dict:
    aggregation_at = time.perf_counter()
    severed_destination = 0
    added_seconds: list[float] = []
    added_metres: list[float] = []
    for pair, multiplicity in od_counts.items():
        baseline_s = base_time[pair]
        baseline_m = base_length[pair]
        detour_s = detour_time[pair]
        detour_m = detour_length[pair]
        if baseline_s is None:
            continue
        if detour_s is None:
            severed_destination += multiplicity
            continue
        seconds = max(detour_s - baseline_s, 0.0)
        metres = max((detour_m or 0.0) - (baseline_m or 0.0), 0.0)
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
        "added_vehicle_hours": round(sum(added_seconds) / 3600.0, 4),
        "added_metres_total": round(sum(added_metres), 1),
        "added_seconds_median": upper_median(added_seconds),
        "added_metres_median": upper_median(added_metres),
        "basis": "calibrated baseline routes; cheapest legal path with vs "
                 "without the closure, free-flow cost",
        "congestion_model": None,
        "capacity_model": None,
        "lane_count_used": False,
    }
    if timing is not None:
        timing("window_aggregation", time.perf_counter() - aggregation_at)
    return result


class ParsedWindowCostIndex:
    """Reusable exact costs for one parsed archive/variant.

    The route archive and closure edge set are immutable for all daily units
    backed by an archive.  Precompute the times at which each vehicle reaches
    a closed edge, group the affected OD pairs, and price each unique pair
    once.  A daily window then only performs an interval-membership scan and
    the final aggregation.  This preserves the old per-window semantics while
    removing repeated XML grouping and shortest-path work.
    """

    def __init__(
        self,
        parsed_vehicles: Sequence[ParsedVehicle],
        closed_edges: set[str],
        edge_time: Costs,
        edge_length: Costs,
        *,
        adjacency: Adjacency,
        timing: Callable[[str, float], None] | None = None,
    ) -> None:
        if not closed_edges:
            raise ValueError("a parsed window index needs closed edges")
        self._vehicles = tuple(parsed_vehicles)
        self._events: list[tuple[tuple[float, ...], OdPair | None, bool]] = []
        pairs: set[OdPair] = set()
        grouping_at = time.perf_counter()
        for edges, depart in self._vehicles:
            clock = float(depart)
            closed_times: list[float] = []
            for edge in edges:
                if edge in closed_edges:
                    closed_times.append(clock)
                clock += edge_time.get(edge, 0.0)
            pair = (edges[0], edges[-1]) if closed_times and edges else None
            denied = bool(edges and edges[0] in closed_edges)
            self._events.append((tuple(closed_times), pair, denied))
            if pair is not None:
                pairs.add(pair)
        if timing is not None:
            timing("route_vehicle_grouping", time.perf_counter() - grouping_at)

        routing_at = time.perf_counter()
        pair_list = tuple(sorted(pairs))
        if len(pair_list) >= SPARSE_ROUTING_MIN_PAIRS:
            router = SparsePathBatch(pair_list, adjacency)
            price = router.path_costs
        else:
            price = lambda costs, banned: grouped_path_costs(  # noqa: E731
                pair_list, adjacency, costs, banned)
        self._base_time = price(edge_time, frozenset())
        self._base_length = price(edge_length, frozenset())
        self._detour_time = price(edge_time, frozenset(closed_edges))
        self._detour_length = price(edge_length, frozenset(closed_edges))
        if timing is not None:
            timing("shortest_path_detour", time.perf_counter() - routing_at)

    def disruption(
        self,
        closures: Sequence[Mapping],
        *,
        timing: Callable[[str, float], None] | None = None,
    ) -> dict | None:
        """Aggregate one exact daily window from the precomputed facts."""
        windows = tuple(
            (float(closure["begin_s"]), float(closure["end_s"]))
            for closure in closures
            if closure.get("begin_s") is not None
            and closure.get("end_s") is not None
        )
        aggregation_at = time.perf_counter()
        affected = denied = 0
        od_counts: Counter[OdPair] = Counter()
        for closed_times, pair, cannot_depart in self._events:
            if not closed_times:
                continue
            if windows and not any(
                    begin <= event < end
                    for event in closed_times
                    for begin, end in windows):
                continue
            affected += 1
            if cannot_depart:
                denied += 1
            elif pair is not None:
                od_counts[pair] += 1
        report = _report(
            considered=len(self._vehicles), affected=affected, denied=denied,
            od_counts=od_counts, base_time=self._base_time,
            base_length=self._base_length, detour_time=self._detour_time,
            detour_length=self._detour_length, timing=None)
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
    timing: Callable[[str, float], None] | None = None,
) -> ParsedWindowCostIndex:
    """Build the reusable exact archive/variant window-cost structure."""
    return ParsedWindowCostIndex(
        parsed_vehicles, closed_edges, edge_time, edge_length,
        adjacency=adjacency, timing=timing)


def closure_disruption(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    timing: Callable[[str, float], None] | None = None,
) -> dict | None:
    """Compute exact disruption with grouped shortest-path traversals."""
    route_path = Path(route_path)
    if not closed_edges or not route_path.exists():
        return None
    considered, affected, denied, od_counts = _affected_od_counts(
        route_path, closed_edges, closures, edge_time, timing=timing
    )
    pairs = tuple(od_counts)
    closed = frozenset(closed_edges)
    if len(pairs) >= SPARSE_ROUTING_MIN_PAIRS:
        router = SparsePathBatch(pairs, adjacency)
        price = router.path_costs
    else:
        price = lambda costs, banned: grouped_path_costs(  # noqa: E731
            pairs, adjacency, costs, banned)
    routing_at = time.perf_counter()
    base_time = price(edge_time, frozenset())
    base_length = price(edge_length, frozenset())
    detour_time = price(edge_time, closed)
    detour_length = price(edge_length, closed)
    if timing is not None:
        timing("shortest_path_detour", time.perf_counter() - routing_at)
    result = _report(
        considered=considered,
        affected=affected,
        denied=denied,
        od_counts=od_counts,
        base_time=base_time,
        base_length=base_length,
        detour_time=detour_time,
        detour_length=detour_length,
        timing=timing,
    )
    return result


def closure_disruption_from_parsed_vehicles(
    parsed_vehicles: Sequence[ParsedVehicle],
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
    timing: Callable[[str, float], None] | None = None,
) -> dict | None:
    """Exact disruption calculation over a previously parsed route file.

    This is the compatibility wrapper for the reusable structural index.  The
    index precomputes route crossing events and unique-OD detours once, then
    applies the exact daily window aggregation.  Consequently the result is
    equivalent to :func:`closure_disruption`, but cannot reuse a cost answer
    from the oracle.
    """
    if not closed_edges:
        return None
    index = build_parsed_window_cost_index(
        parsed_vehicles, closed_edges, edge_time, edge_length,
        adjacency=adjacency, timing=timing)
    return index.disruption(closures, timing=timing)


def reference_closure_disruption(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
) -> dict | None:
    """Former per-OD algorithm, retained only as an equivalence oracle."""
    route_path = Path(route_path)
    if not closed_edges or not route_path.exists():
        return None
    considered, affected, denied, od_counts = _affected_od_counts(
        route_path, closed_edges, closures, edge_time
    )
    pairs = tuple(od_counts)
    closed = frozenset(closed_edges)

    def price(costs: Costs, banned: frozenset[str]):
        return {
            pair: shortest_path_cost(
                adjacency, costs, pair[0], pair[1], banned
            )
            for pair in pairs
        }

    return _report(
        considered=considered,
        affected=affected,
        denied=denied,
        od_counts=od_counts,
        base_time=price(edge_time, frozenset()),
        base_length=price(edge_length, frozenset()),
        detour_time=price(edge_time, closed),
        detour_length=price(edge_length, closed),
    )
