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
import xml.etree.ElementTree as ET
from typing import Iterable, Mapping, Sequence


Adjacency = Mapping[str, Sequence[str]]
Costs = Mapping[str, float]
OdPair = tuple[str, str]
SPARSE_ROUTING_MIN_PAIRS = 32


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


def _affected_od_counts(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
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
    for vehicle in ET.parse(route_path).getroot().iter("vehicle"):
        route = vehicle.find("route")
        if route is None or not route.get("edges"):
            continue
        considered += 1
        edges = route.get("edges").split()
        clock = float(vehicle.get("depart", "0"))
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
) -> dict:
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
    return {
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


def closure_disruption(
    route_path: Path,
    closed_edges: set[str],
    closures: Sequence[Mapping],
    edge_time: Costs,
    edge_length: Costs,
    *,
    adjacency: Adjacency,
) -> dict | None:
    """Compute exact disruption with grouped shortest-path traversals."""
    route_path = Path(route_path)
    if not closed_edges or not route_path.exists():
        return None
    considered, affected, denied, od_counts = _affected_od_counts(
        route_path, closed_edges, closures, edge_time
    )
    pairs = tuple(od_counts)
    closed = frozenset(closed_edges)
    if len(pairs) >= SPARSE_ROUTING_MIN_PAIRS:
        router = SparsePathBatch(pairs, adjacency)
        price = router.path_costs
    else:
        price = lambda costs, banned: grouped_path_costs(  # noqa: E731
            pairs, adjacency, costs, banned)
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
