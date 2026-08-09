"""Read-only route- and movement-support diagnostics for LOSO folds.

Work package 1 of ``docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md``.

The corrected six-seed LOSO baseline shows a stable, junction-shaped error:
station 134 and 2276 overpredict while 133 underpredicts, all three touching
node ``26355153``.  Before changing candidate generation or the picker, this
module explains *what the picker was given*: how many distinct plausible
routes cross each held location, how many of them are anchored by another
station's constraint, how the legal turning movements at the shared junction
are supported, and how stable all of that is across seeds.

Everything here is a MEASUREMENT of the pool, the network and the published
fold artifacts.  Nothing in this module regenerates candidates, re-solves the
PFE or changes any published artifact, and candidate frequency is never
converted into a claimed turn ratio — the plan forbids that explicitly.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from traffic_sim.demand import pfe

# The path-size weight published by pfe is EPS_PARSIMONY / clip(PS, floor, 1).
# Inverting it recovers the clipped PS itself, so this module reports the same
# quantity the solver actually used instead of a second, drifting definition.
_PS_FLOOR = pfe.PATH_SIZE_FLOOR
_EPS_PARSIMONY = pfe.EPS_PARSIMONY

SAMPLE_CAP = 300


@dataclass(frozen=True)
class Leg:
    """One routed candidate leg exactly as the pool published it."""

    candidate_id: str
    edges: tuple[str, ...]
    depart_s: float
    purpose: str
    tour_id: str
    leg: str
    origin_edge: str
    destination_edge: str
    tour_partner_dropped: bool

    @property
    def geometry_key(self) -> str:
        return " ".join(self.edges)

    @property
    def od_group(self) -> tuple[str, str]:
        return (self.origin_edge, self.destination_edge)


@dataclass
class Variable:
    """One deduplicated ``route x purpose`` PFE variable.

    The key matches ``pfe.prepare_calibration`` exactly: a geometry that is
    generated under two purposes stays two variables, because the purpose of a
    selected route is an invariant of this pipeline, not a post-hoc label.
    """

    edges: tuple[str, ...]
    purpose: str
    legs: list[Leg] = field(default_factory=list)
    path_size: float = 1.0

    @property
    def geometry_key(self) -> str:
        return " ".join(self.edges)

    @property
    def od_group(self) -> tuple[str, str]:
        return (self.edges[0], self.edges[-1]) if self.edges else ("", "")


@dataclass(frozen=True)
class Location:
    """One physically observable count location.

    A two-way-total station is ONE location assembled from two directed edges;
    scoring its directions separately confounds direction assignment with
    recovery of station flow.  The directional component rows are retained for
    diagnosis only.
    """

    station: str
    measurement_semantics: str
    component_edges: tuple[str, ...]


@dataclass
class Network:
    """Free-flow cost model and connection graph read from ``net.net.xml``."""

    edge_cost_s: dict[str, float]
    edge_from: dict[str, str]
    edge_to: dict[str, str]
    successors: dict[str, tuple[str, ...]]

    def incoming(self, node: str) -> tuple[str, ...]:
        return tuple(sorted(e for e, n in self.edge_to.items() if n == node))

    def outgoing(self, node: str) -> tuple[str, ...]:
        return tuple(sorted(e for e, n in self.edge_from.items() if n == node))


def load_network(net_path: Path) -> Network:
    """Read edge free-flow costs and the legal edge-to-edge connection graph."""
    root = ET.parse(net_path).getroot()
    edge_cost_s: dict[str, float] = {}
    edge_from: dict[str, str] = {}
    edge_to: dict[str, str] = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        edge_id = edge.get("id")
        lanes = edge.findall("lane")
        speeds = [float(lane.get("speed")) for lane in lanes if lane.get("speed")]
        lengths = [float(lane.get("length")) for lane in lanes if lane.get("length")]
        length = float(edge.get("length") or (max(lengths) if lengths else 0.0))
        speed = max(speeds) if speeds else 13.9
        edge_cost_s[edge_id] = length / speed if speed > 0 else float("inf")
        edge_from[edge_id] = edge.get("from")
        edge_to[edge_id] = edge.get("to")
    successors: dict[str, set[str]] = {}
    for connection in root.findall("connection"):
        source = connection.get("from")
        target = connection.get("to")
        if source in edge_cost_s and target in edge_cost_s:
            successors.setdefault(source, set()).add(target)
    return Network(
        edge_cost_s=edge_cost_s,
        edge_from=edge_from,
        edge_to=edge_to,
        successors={key: tuple(sorted(value)) for key, value in successors.items()},
    )


def legal_movements(network: Network, node: str) -> list[dict]:
    """Return every legal ``(incoming, outgoing)`` movement through ``node``.

    A movement whose two edges are the two directions of the same street is a
    U-turn; it is reported and flagged rather than dropped, because SUMO does
    permit it and a route that uses one is a real (if implausible) support
    path.
    """
    incoming = network.incoming(node)
    outgoing = set(network.outgoing(node))
    movements = []
    for source in incoming:
        for target in sorted(network.successors.get(source, ())):
            if target not in outgoing:
                continue
            movements.append({
                "from_edge": source,
                "to_edge": target,
                "u_turn": network.edge_from[source] == network.edge_to[target],
            })
    return movements


def shortest_costs_from(network: Network, origin: str) -> dict[str, float]:
    """Single-source free-flow cost from ``origin`` to every reachable edge.

    Cost accrues on ENTERING an edge, so the origin edge costs its own
    traversal.  A route's cost uses the same accounting, which keeps the
    stretch ratio dimensionally honest.
    """
    if origin not in network.edge_cost_s:
        return {}
    best = {origin: network.edge_cost_s[origin]}
    queue = [(best[origin], origin)]
    while queue:
        cost, edge = heapq.heappop(queue)
        if cost > best.get(edge, math.inf):
            continue
        for nxt in network.successors.get(edge, ()):
            candidate = cost + network.edge_cost_s[nxt]
            if candidate < best.get(nxt, math.inf):
                best[nxt] = candidate
                heapq.heappush(queue, (candidate, nxt))
    return best


class CostModel:
    """Free-flow route cost and per-OD shortest cost, cached by origin."""

    def __init__(self, network: Network) -> None:
        self.network = network
        self._cache: dict[str, dict[str, float]] = {}

    def route_cost_s(self, edges: Sequence[str]) -> float:
        return sum(self.network.edge_cost_s.get(edge, 0.0) for edge in edges)

    def shortest_cost_s(self, origin: str, destination: str) -> float | None:
        if origin not in self._cache:
            self._cache[origin] = shortest_costs_from(self.network, origin)
        return self._cache[origin].get(destination)

    def stretch(self, edges: Sequence[str]) -> float | None:
        """Route cost divided by the fastest legal cost for the same OD."""
        if not edges:
            return None
        shortest = self.shortest_cost_s(edges[0], edges[-1])
        if shortest is None or shortest <= 0:
            return None
        return self.route_cost_s(edges) / shortest


def load_pool(rou_path: Path, meta_path: Path | None = None) -> list[Leg]:
    """Load one candidate pool with its provenance metadata.

    Provenance is REQUIRED: an audit that silently accepted routes it cannot
    map back to the pool would violate the plan's stop condition on candidate
    provenance.
    """
    rou_path = Path(rou_path)
    if meta_path is None:
        meta_path = rou_path.with_name(
            rou_path.name.replace(".rou.xml", ".meta.json"))
    document = json.loads(Path(meta_path).read_text())
    metadata = document.get("candidates", {})
    legs: list[Leg] = []
    unmapped: list[str] = []
    for vehicle in ET.parse(rou_path).getroot().iter("vehicle"):
        candidate_id = vehicle.get("id") or ""
        record = metadata.get(candidate_id)
        if record is None:
            unmapped.append(candidate_id)
            continue
        edges = tuple(vehicle.find("route").get("edges").split())
        legs.append(Leg(
            candidate_id=candidate_id,
            edges=edges,
            depart_s=float(vehicle.get("depart")),
            purpose=str(record.get("purpose", "")),
            tour_id=str(record.get("tour_id", "")),
            leg=str(record.get("leg", "")),
            origin_edge=str(record.get("origin_edge", edges[0] if edges else "")),
            destination_edge=str(
                record.get("destination_edge", edges[-1] if edges else "")),
            tour_partner_dropped=bool(record.get("tour_partner_dropped", False)),
        ))
    if unmapped:
        raise ValueError(
            f"{len(unmapped)} candidate route(s) have no pool provenance, "
            f"first: {unmapped[0]}")
    return legs


def dedup_variables(legs: Sequence[Leg]) -> list[Variable]:
    """Collapse legs into the solver's ``route x purpose`` variables."""
    seen: dict[tuple[str, str], Variable] = {}
    order: list[Variable] = []
    for leg in legs:
        key = (leg.geometry_key, leg.purpose)
        variable = seen.get(key)
        if variable is None:
            variable = Variable(edges=leg.edges, purpose=leg.purpose)
            seen[key] = variable
            order.append(variable)
        variable.legs.append(leg)
    _attach_path_sizes(order)
    return order


def _attach_path_sizes(variables: list[Variable]) -> None:
    """Fill each variable's path-size share using the solver's own function."""
    if not variables:
        return
    shapes = [pfe.Candidate(depart=0.0, edges=list(variable.edges))
              for variable in variables]
    weights = pfe.path_size_weights(shapes)
    for variable, weight in zip(variables, weights):
        variable.path_size = float(_EPS_PARSIMONY / weight) if weight else _PS_FLOOR


def effective_alternatives(path_sizes: Sequence[float]) -> float:
    """Hill number of order 1 over normalized path-size weights.

    Counting raw distinct routes overstates choice when the alternatives are
    near-copies of one another: three routes that differ by one edge are not
    three independent options.  Normalizing the path-size weights and taking
    ``exp(entropy)`` reports how many *effectively distinct* alternatives the
    picker had.  One unique route always yields exactly 1.0.
    """
    values = [value for value in path_sizes if value > 0]
    if not values:
        return 0.0
    total = sum(values)
    shares = [value / total for value in values]
    entropy = -sum(share * math.log(share) for share in shares if share > 0)
    return math.exp(entropy)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _sample(items: Sequence, cap: int = SAMPLE_CAP) -> list:
    """Deterministic evenly-strided subsample, order preserved."""
    if len(items) <= cap:
        return list(items)
    step = len(items) / cap
    return [items[int(index * step)] for index in range(cap)]


def overlap_summary(variables: Sequence[Variable]) -> dict:
    """Pairwise edge-set Jaccard over a deterministic subsample."""
    sampled = _sample(sorted(variables, key=lambda v: (v.geometry_key, v.purpose)))
    sets = [set(variable.edges) for variable in sampled]
    values = [
        _jaccard(sets[i], sets[j])
        for i in range(len(sets)) for j in range(i + 1, len(sets))
    ]
    if not values:
        return {"pairs": 0, "median": None, "p90": None, "sampled_routes": len(sampled)}
    ordered = sorted(values)
    return {
        "pairs": len(values),
        "median": round(statistics.median(ordered), 4),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 4),
        "sampled_routes": len(sampled),
    }


def _quantiles(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "p90": None}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 4),
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 4),
    }


def _composition(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def crosses(edges: Iterable[str], targets: Iterable[str]) -> bool:
    target_set = set(targets)
    return any(edge in target_set for edge in edges)


def location_support(
    location: Location,
    variables: Sequence[Variable],
    other_locations: Sequence[Location],
    cost_model: CostModel | None = None,
) -> dict:
    """Measure the route support the picker had at one count location."""
    component = set(location.component_edges)
    crossing = [variable for variable in variables
                if component & set(variable.edges)]
    legs = [leg for variable in crossing for leg in variable.legs]

    od_groups: dict[tuple[str, str], list[Variable]] = {}
    for variable in crossing:
        od_groups.setdefault(variable.od_group, []).append(variable)
    alternatives = [len(group) for group in od_groups.values()]
    effective = [effective_alternatives([v.path_size for v in group])
                 for group in od_groups.values()]

    other_edges = {other.station: set(other.component_edges)
                   for other in other_locations if other.station != location.station}
    shared = {station: sum(1 for variable in crossing
                           if edges & set(variable.edges))
              for station, edges in other_edges.items()}
    all_other = set().union(*other_edges.values()) if other_edges else set()
    unanchored = [variable for variable in crossing
                  if not (all_other & set(variable.edges))]

    stretches: list[float] = []
    if cost_model is not None:
        for variable in crossing:
            value = cost_model.stretch(variable.edges)
            if value is not None:
                stretches.append(value)

    directional = {}
    if len(location.component_edges) > 1:
        for edge in location.component_edges:
            directional[edge] = {
                "raw_candidate_legs": sum(
                    1 for variable in variables for leg in variable.legs
                    if edge in variable.edges),
                "variables": sum(1 for variable in variables
                                 if edge in variable.edges),
            }

    return {
        "station": location.station,
        "measurement_semantics": location.measurement_semantics,
        "component_edges": list(location.component_edges),
        "raw_candidate_legs": len(legs),
        "variables": len(crossing),
        "unique_edge_sequences": len({variable.geometry_key
                                      for variable in crossing}),
        "od_groups": len(od_groups),
        "alternatives_per_od_group": _quantiles(alternatives),
        "od_groups_with_single_route": sum(1 for value in alternatives if value == 1),
        "od_groups_with_single_route_pct": round(
            100.0 * sum(1 for value in alternatives if value == 1)
            / max(1, len(alternatives)), 2),
        "effective_alternatives_per_od_group": _quantiles(effective),
        "purpose_composition": _composition(leg.purpose for leg in legs),
        "gate_pair_composition": _composition(
            f"{leg.origin_edge}->{leg.destination_edge}"
            for leg in legs if _od_class(leg) == "through"),
        "tour_leg_composition": _composition(leg.leg for leg in legs),
        "od_class_composition": _composition(
            _od_class(leg) for leg in legs),
        "orphan_tour_legs": sum(1 for leg in legs if leg.tour_partner_dropped),
        "orphan_tour_legs_pct": round(
            100.0 * sum(1 for leg in legs if leg.tour_partner_dropped)
            / max(1, len(legs)), 2),
        "orphan_tour_legs_by_leg": _composition(
            leg.leg for leg in legs if leg.tour_partner_dropped),
        "variables_shared_with_station": dict(sorted(shared.items())),
        "variables_crossing_no_other_station": len(unanchored),
        "variables_crossing_no_other_station_pct": round(
            100.0 * len(unanchored) / max(1, len(crossing)), 2),
        "cost_stretch_vs_fastest": _quantiles(stretches),
        "path_size": _quantiles([variable.path_size for variable in crossing]),
        "route_overlap_jaccard": overlap_summary(crossing),
        "directional_components": directional,
    }


def _od_class(leg: Leg) -> str:
    """Report the pool's own OD class label, never an inferred one."""
    tour = leg.tour_id
    if leg.purpose == "through" or leg.leg == "through":
        return "through"
    for token in ("ii", "ei", "ie", "ee"):
        if f"{token}-" in tour:
            return token
    return "unclassified"


def movement_support(
    movements: Sequence[dict],
    variables: Sequence[Variable],
    locations: Sequence[Location],
) -> list[dict]:
    """Count route support for each legal movement through the junction.

    This is a ROUTE-TRANSITION diagnostic. Candidate frequency is support, not
    an observed turn ratio, and the plan explicitly forbids normalizing these
    counts into claimed turn shares.
    """
    wanted = {(movement["from_edge"], movement["to_edge"]) for movement in movements}
    raw: dict[tuple[str, str], int] = {key: 0 for key in wanted}
    variable_hits: dict[tuple[str, str], int] = {key: 0 for key in wanted}
    stations: dict[tuple[str, str], dict[str, int]] = {
        key: {} for key in wanted}
    station_edges = {location.station: set(location.component_edges)
                     for location in locations}
    for variable in variables:
        edges = variable.edges
        used = {(edges[index], edges[index + 1]) for index in range(len(edges) - 1)}
        touched = used & wanted
        if not touched:
            continue
        variable_edges = set(edges)
        for key in touched:
            variable_hits[key] += 1
            raw[key] += len(variable.legs)
            for station, component in station_edges.items():
                if component & variable_edges:
                    stations[key][station] = stations[key].get(station, 0) + 1
    out = []
    for movement in movements:
        key = (movement["from_edge"], movement["to_edge"])
        out.append({
            **movement,
            "raw_candidate_legs": raw[key],
            "variables": variable_hits[key],
            "measured_stations_on_the_same_routes": dict(sorted(
                stations[key].items())),
        })
    return out


def movement_incidence(movements: Sequence[dict], legs: Sequence[str]) -> list[list[int]]:
    """Rows = leg observations, columns = movements.

    A leg's count equals the sum of the movements that use it: for an
    incoming leg the movements leaving it, for an outgoing leg the movements
    entering it.  This is the exact algebra a junction fixpoint would need,
    written down so its rank can be measured instead of assumed.
    """
    rows = []
    for leg in legs:
        rows.append([
            1 if (movement["from_edge"] == leg or movement["to_edge"] == leg) else 0
            for movement in movements
        ])
    return rows


def _rank(rows: Sequence[Sequence[int]]) -> int:
    if not rows or not rows[0]:
        return 0
    import numpy as np

    return int(np.linalg.matrix_rank(np.array(rows, dtype=float)))


def junction_identifiability(
    movements: Sequence[dict],
    measured_legs: Mapping[str, str],
    all_legs: Sequence[str],
    held: str | None = None,
) -> dict:
    """Rank and null-space of the movement system given the measured legs.

    Nullity is the number of independent movement patterns the counts cannot
    tell apart.  It is an EXACT property of topology plus which legs are
    observed — no traffic assumption enters it — so it is the honest way to
    say how underdetermined this junction is, and how much any hypothetical
    extra observation would help.
    """
    active = {leg: station for leg, station in measured_legs.items()
              if held is None or station != held}
    legs = sorted(active)
    columns = len(movements)
    rank = _rank(movement_incidence(movements, legs))
    unmeasured = [leg for leg in all_legs if leg not in active]
    gains = []
    for candidate in unmeasured:
        with_candidate = _rank(movement_incidence(movements, legs + [candidate]))
        gains.append({
            "leg": candidate,
            "rank_with_leg": with_candidate,
            "rank_gain": with_candidate - rank,
            "nullity_with_leg": columns - with_candidate,
        })
    all_legs_rank = _rank(movement_incidence(movements, list(all_legs)))
    return {
        "held_station": held,
        "measured_legs": legs,
        "movements": columns,
        "rank": rank,
        "nullity": columns - rank,
        "rank_if_every_leg_were_measured": all_legs_rank,
        "nullity_if_every_leg_were_measured": columns - all_legs_rank,
        "unmeasured_leg_rank_gain": sorted(
            gains, key=lambda row: (-row["rank_gain"], row["leg"])),
    }


def constraint_coverage(edges: Sequence[str], direction_priors: Mapping[str, dict],
                        assignment_flows: Mapping[str, Sequence[float | None]],
                        measured_edges: Mapping[str, str]) -> list[dict]:
    """Report which constraint classes actually reach each edge.

    ``calibrate_assignment_priors`` deliberately skips any edge already
    ``covered`` by a measurement or a prior, so an edge carrying only a WEAK
    soft prior receives no hard plausibility ceiling at all.  Whether that
    combination leaves an edge effectively unbounded above is a question this
    table answers with the published artifacts rather than by inspection.
    """
    rows = []
    for edge in edges:
        prior = direction_priors.get(edge)
        prior_values = [value for value in (prior or {}).get("prior", [])
                        if value is not None]
        bands = [
            (high or 0.0) - (low or 0.0)
            for low, high, value in zip((prior or {}).get("prior_low", []),
                                        (prior or {}).get("prior_high", []),
                                        (prior or {}).get("prior", []))
            if value is not None
        ]
        series = assignment_flows.get(edge)
        ceiling = None
        if series is not None:
            ceiling = round(sum(max(5.0, 5.0 * float(value))
                                for value in series if value is not None), 2)
        rows.append({
            "edge": edge,
            "measured_by_station": measured_edges.get(edge),
            "direction_prior_from_station": (prior or {}).get("sensor"),
            "direction_prior_daily_total": round(sum(prior_values), 2)
            if prior_values else None,
            "direction_prior_median_band": round(statistics.median(bands), 3)
            if bands else None,
            "direction_prior_median_weight": round(
                1.0 / max(1.0, statistics.median(bands)), 5) if bands else None,
            "assignment_ceiling_daily_total": ceiling,
            "has_hard_upper_bound": ceiling is not None,
            "constraint_classes": [
                name for name, present in (
                    ("measurement", edge in measured_edges),
                    ("direction_prior", prior is not None),
                    ("assignment_ceiling", ceiling is not None),
                ) if present
            ],
        })
    return rows


def iter_route_vehicles(rou_path: Path) -> Iterator[tuple[float, tuple[str, ...]]]:
    """Stream ``(depart_s, edges)`` from a published route file.

    Fold route files are tens of megabytes each; streaming keeps the audit's
    footprint small enough to run over every station without holding a whole
    calibration in memory.
    """
    for _event, element in ET.iterparse(str(rou_path), events=("end",)):
        if element.tag != "vehicle":
            continue
        route = element.find("route")
        if route is not None:
            yield float(element.get("depart")), tuple(route.get("edges").split())
        element.clear()


def selected_movement_flows(
    rou_path: Path,
    movements: Sequence[dict],
    legs: Sequence[str],
    n_quarters: int = 96,
) -> dict:
    """Count PFE-selected vehicles per movement and per leg, by quarter.

    The quarter is the vehicle's PLANNED departure quarter, not the quarter it
    reaches the junction; the difference between this and the simulated edge
    counts is exactly the timing displacement the plan asks about.
    """
    wanted = {(movement["from_edge"], movement["to_edge"]) for movement in movements}
    leg_set = set(legs)
    movement_counts = {key: [0] * n_quarters for key in wanted}
    leg_counts = {edge: [0] * n_quarters for edge in leg_set}
    vehicles = 0
    for depart, edges in iter_route_vehicles(rou_path):
        vehicles += 1
        quarter = min(n_quarters - 1, max(0, int(depart // 900)))
        for index in range(len(edges) - 1):
            key = (edges[index], edges[index + 1])
            if key in wanted:
                movement_counts[key][quarter] += 1
        for edge in set(edges) & leg_set:
            leg_counts[edge][quarter] += 1
    return {
        "vehicles": vehicles,
        "by_movement": {f"{a}->{b}": counts
                        for (a, b), counts in sorted(movement_counts.items())},
        "by_leg": {edge: counts for edge, counts in sorted(leg_counts.items())},
    }


def simulated_edge_flows(edgedata_path: Path, edges: Sequence[str],
                         n_quarters: int = 96) -> dict[str, list[float]]:
    """Read simulated ``entered`` counts per quarter for the given edges."""
    wanted = set(edges)
    out = {edge: [0.0] * n_quarters for edge in wanted}
    for _event, element in ET.iterparse(str(edgedata_path), events=("end",)):
        if element.tag != "interval":
            continue
        quarter = int(float(element.get("begin")) // 900)
        if 0 <= quarter < n_quarters:
            for edge in element.findall("edge"):
                edge_id = edge.get("id")
                if edge_id in wanted:
                    out[edge_id][quarter] += float(edge.get("entered") or 0)
        element.clear()
    return out


def geometry_hashes(variables: Sequence[Variable], component: Iterable[str]) -> set[str]:
    """Stable identity for the routes crossing one location, for seed stability."""
    component_set = set(component)
    return {
        hashlib.sha256(variable.geometry_key.encode()).hexdigest()[:16]
        for variable in variables if component_set & set(variable.edges)
    }


def across_seed_stability(support_by_seed: Mapping[str, Mapping[str, set[str]]]
                          ) -> dict[str, dict]:
    """Pairwise Jaccard of each location's crossing route set across seeds.

    A treatment that only reshuffles which arbitrary routes happen to cross a
    sensor will show low stability here; a treatment that genuinely adds
    plausible support should raise it.
    """
    seeds = sorted(support_by_seed)
    stations: set[str] = set()
    for record in support_by_seed.values():
        stations.update(record)
    out: dict[str, dict] = {}
    for station in sorted(stations):
        values = []
        for i in range(len(seeds)):
            for j in range(i + 1, len(seeds)):
                left = support_by_seed[seeds[i]].get(station, set())
                right = support_by_seed[seeds[j]].get(station, set())
                values.append(_jaccard(left, right))
        sizes = [len(support_by_seed[seed].get(station, set())) for seed in seeds]
        out[station] = {
            "pairwise_jaccard": _quantiles(values),
            "crossing_variables_by_seed": {
                seed: len(support_by_seed[seed].get(station, set())) for seed in seeds},
            "crossing_variables": _quantiles([float(size) for size in sizes]),
            "crossing_variables_range_ratio": round(
                max(sizes) / max(1, min(sizes)), 4) if sizes else None,
        }
    return out
