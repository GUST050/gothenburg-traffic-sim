"""Pre-simulation origin-to-original-destination closure routing.

ROOT CAUSE, root-cause-analysed 2026-08-29 replacing the earlier
`truncate_stranded_vehicles` + runtime `closingReroute` + `time-to-teleport
=-1` combination (`run_scenario.truncate_stranded_vehicles`,
`traffic_sim.simulation.closure_teleport`). Four distinct things were
tangled together and only the last one was visible:

  1. PREPROCESSOR BEHAVIOUR. The old preprocessor only ever handled the
     narrow "no detour exists at all" case (truncating the route short of
     the closure). Every vehicle that DID have a detour was left with its
     ORIGINAL, closure-crossing route and handed to SUMO unchanged, on the
     assumption that the live rerouter below would fix it.
  2. LOCAL REROUTER TIMING. SUMO's `<rerouter>`/`closingReroute` (see
     https://sumo.dlr.de/docs/Simulation/Rerouter.html) only evaluates a
     vehicle when it reaches one of the rerouter's OWN trigger edges — here,
     edges within `REROUTER_RADIUS_M` (400 m) of the closure
     (https://sumo.dlr.de/docs/Simulation/Routing.html documents rerouting
     as a runtime, position-triggered mechanism, not a departure-time one).
     A vehicle therefore drives most of its route on its original plan and
     only discovers the closure seconds before reaching it, so every
     affected vehicle's replanning is concentrated at the closure's
     doorstep instead of being spread across the whole route from
     departure.
  3. DISABLED GRIDLOCK/TELEPORT HANDLING. `CLOSURE_TIME_TO_TELEPORT_S = -1`
     (see `closure_teleport.py`) was deployed specifically so a vehicle that
     could not be routed around the closure would never be relocated PAST
     it — but it also disabled SUMO's normal stuck-vehicle relief for
     everything else. Under (2)'s doorstep-concentrated replanning, a busy
     closure produces a real queue of vehicles converging on the rerouter
     edges at once; the mesoscopic model's own gridlock recovery
     (https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html
     documents teleporting as SUMO's designed mechanism for this) was the
     only thing that could ever clear such a queue, and it was switched off
     for the entire closure run, not just the closed edge.
  4. WALL-TIME TIMEOUT — a SYMPTOM, not a cause. `run_sumo`'s subprocess
     timeout (300 s first attempt) is what actually stopped a run that
     entered (3)'s state; a diagnostic replay confirmed this directly
     (`ui-monthly-12hg8f3`'s partial SUMO summary output showed running/
     halting vehicle counts growing without bound during the active
     closure). Raising the timeout — which the existing 1,800 s registered
     retry already does — lets a caught-in-(3) run occasionally finish, but
     does not remove (1)-(3), which is why it is a symptom fix, not a root
     one, and is retained here only as an unrelated, still-useful defence
     against genuinely slow-but-healthy runs.

THE FIX. Every vehicle whose route would cross a closed edge during the
closure's active window is rewritten HERE, before any SUMO process starts,
from its ORIGINAL origin to its ORIGINAL destination, along the deterministic
fastest legal path with every applicable closed edge excluded — using the
same shortest-path engine (`disruption.shortest_path_edges`) that also
prices closure severity, so routing and ranking can never disagree about
reachability. A vehicle is only ever left undecided at the network boundary
(never simulated, never waited, never teleported) when its ORIGINAL
destination itself sits on a closed edge, or when no legal path exists at
all once the closure(s) are excluded — see `AccessImpactRecord`. Because
every affected vehicle's route is closure-free before SUMO ever starts,
`write_closure_additional`'s runtime `<rerouter>` is no longer load-bearing:
it is retained (see `run_scenario.py`) purely as a fail-closed structural
declaration that keeps a routing defect from ever silently producing closed-
edge throughput, not as the mechanism that produces correct detours.
Closure runs also no longer need a disabled teleport threshold — see
`closure_teleport.py`'s `CLOSURE_TIME_TO_TELEPORT_S`, now a labelled legacy
constant kept only for historical diagnostic reproduction; production
closure runs use SUMO's ordinary default, exactly like a baseline run,
because the actual closure hazard is now eliminated before simulation
begins rather than suppressed during it.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation import disruption as disruption_analysis
from traffic_sim.simulation.metadata import DEFAULT_VCLASS

#: Version tag written into every access-impact record and evidence file
#: this module produces. Bump this whenever the routing rule itself changes
#: so a cached result computed under an older rule can never satisfy a
#: lookup for this one.
#:
#: v1 -> v2 (2026-08-29, review repair pass): the overlap rule changed from
#: an unproven fixed 900 s margin to the provable one-directional
#: "already past end_s" rule (see `_closures_overlapping`), and
#: destination-closed denial became window-aware instead of firing on bare
#: membership in the closed-edge set. Both changes can move a trip between
#: unaffected/rerouted/denied relative to v1 for the SAME input route and
#: closure, so any v1 evidence, cache entry, or provenance record is
#: incompatible with v2 and must never satisfy a v2 lookup.
#:
#: v2 -> v3 (2026-08-29, same repair-batch pass, finding 3): `adjacency` is
#: now single-vClass permission-filtered (`run_scenario.build_edge_graph`,
#: `traffic_sim.simulation.metadata.DEFAULT_VCLASS`) instead of the raw
#: geometric connection graph, and a vehicle declaring an unrecognised
#: `type=` now fails closed (`_check_vehicle_class`) instead of being routed
#: on an unproven legality assumption. On the production network today
#: neither change alters any existing decision (no lane/edge/connection in
#: `net.net.xml` declares `allow`/`disallow`, and no route file declares
#: `type=`), but a network or demand file that DID would have been
#: misrouted or silently accepted under v2, so v2 evidence must never
#: satisfy a v3 lookup.
#:
#: v3 -> v4 (2026-08-30, review-03 continuation, finding 1): `RoutingProvenance`
#: gained `unit_id` (the `daily-unit-*` identity a monthly ledger keys
#: evidence by, distinct from `candidate_id`/`schedule.schedule_id`) and
#: `transformed_route_sha256` (the digest of the route file actually handed
#: to SUMO, previously only reachable indirectly by resolving the whole
#: access-impact report's `output_route_sha256`). `access_impact_sha256`
#: changed from optional to REQUIRED -- every successful closure observation
#: writes an access-impact report unconditionally (`write_access_impact_
#: report` is called whenever `close_edges` is non-empty, regardless of
#: whether anything was actually rerouted or denied), so a `None` digest on
#: a real closure run was always a sign the evidence had gone missing, never
#: a legitimate state. Both digests are now validated as lowercase hex
#: SHA-256 (`_HEX64`), not merely checked for length. A v3 provenance dict
#: has neither new field and a possibly-null `access_impact_sha256`, so it
#: must never satisfy a v4 lookup.
POLICY_VERSION = "closure_origin_routing_v4"

#: Reasons `AccessImpactRecord.reason` is allowed to take. Nothing else may
#: ever be written; a caller adding a new denial path must extend this set
#: explicitly rather than let an ad-hoc string drift into evidence.
DESTINATION_CLOSED = "destination_closed"
NO_LEGAL_PATH = "no_legal_path"
ACCESS_IMPACT_REASONS = frozenset({DESTINATION_CLOSED, NO_LEGAL_PATH})

ACCESS_IMPACT_SCHEMA = "closure_access_impact_v1"
ACCESS_IMPACT_SCHEMA_VERSION = 3
ACCESS_IMPACT_DIAGNOSTIC_SCHEMA = "closure_access_impact_diagnostic_v1"
ACCESS_IMPACT_DIAGNOSTIC_SCHEMA_VERSION = 1

_ACCESS_IMPACT_REPORT_FIELDS = frozenset({
    "schema_version", "kind", "policy_version", "identity", "windowed",
    "close_edges", "closures", "source_route_sha256", "output_route_sha256",
    "network_sha256", "summary", "access_impact", "rerouted_vehicle_ids",
})
_ACCESS_IMPACT_IDENTITY_FIELDS = frozenset({
    "unit_id", "candidate_id", "work_date", "demand_variant", "seed",
    "execution_arm", "vehicle_class",
})
_ACCESS_IMPACT_SUMMARY_FIELDS = frozenset({
    "unaffected", "rerouted", "denied",
})
_ACCESS_IMPACT_RECORD_FIELDS = frozenset({
    "vehicle_id", "reason", "original_origin", "original_destination",
    "original_depart_s", "applicable_closed_edges",
})
_ACCESS_IMPACT_CLOSURE_FIELDS = frozenset({"edge_id", "begin_s", "end_s"})

#: REVISED 2026-08-29 (review finding, POLICY_VERSION bumped to v2): the
#: previous implementation padded both sides of a closure window by a fixed
#: `CLOSURE_TIMING_SAFETY_MARGIN_S = 900.0` and called that "safe". It was
#: not: congestion delay has no demonstrated, evidence-backed upper bound,
#: so no FINITE additive margin can ever prove a vehicle arrives before a
#: still-open closure ends -- a vehicle whose true (congested) transit lands
#: more than 900 s later than its free-flow estimate would still have been
#: classified "not applicable" and handed to SUMO on its original,
#: closure-crossing route, exactly the failure mode this module exists to
#: remove.
#:
#: The rule below replaces the margin with the one interval fact that IS
#: provable without bounding congestion at all: real transit is never
#: faster than free-flow transit (SUMO's mesoscopic/microscopic models only
#: ever add delay relative to free speed; they cannot subtract it), so
#: `depart_s + free_flow_elapsed` is a true LOWER BOUND on the instant a
#: vehicle can occupy a given edge -- never later than reality, and no
#: assumption about how much later reality can be is required. That gives
#: exactly one sound one-directional proof of safety: if this lower bound
#: has already reached or passed a window's `end_s`, the vehicle cannot
#: possibly occupy that edge before the closure has reopened, because any
#: additional congestion delay only pushes the real arrival later still,
#: never back into the window. There is deliberately no symmetric proof for
#: "occupancy ends before the window OPENS": that would require an upper
#: bound on how much later than free-flow the vehicle could still be
#: delayed, which does not exist. A trip that cannot be proven safe this way
#: is therefore always classified as APPLICABLE (affected), never given the
#: benefit of an unproven margin -- this can only widen who is treated as
#: affected relative to the old rule, never narrow it.
def _edge_occupancy_lower_bound(depart_s: float, elapsed_free_flow_s: float) -> float:
    """Earliest possible instant a vehicle can occupy an edge; a true lower
    bound because real transit time is never less than free-flow transit
    time. See the module-level note above this function for the proof this
    supports and the one it deliberately does not attempt."""
    return depart_s + elapsed_free_flow_s


class ClosureRoutingError(ValueError):
    """Raised when a route cannot be classified or rewritten deterministically."""


def _read_route_text(route_path: Path) -> str:
    """Read plain or losslessly compressed route XML as text."""
    path = Path(route_path)
    if path.name.endswith(".gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, EOFError) as error:
            raise ClosureRoutingError(
                f"compressed route artifact is invalid: {path}") from error
    return path.read_text(encoding="utf-8")


_VEHICLE_FRAGMENT_RE = re.compile(r"<vehicle\b.*?</vehicle>", re.DOTALL)
_OPEN_TAG_RE = re.compile(r"<vehicle\b[^>]*>")
_ID_ATTR_RE = re.compile(r'\bid="([^"]*)"')
_DEPART_ATTR_RE = re.compile(r'\bdepart="([^"]*)"')
_TYPE_ATTR_RE = re.compile(r'\btype="([^"]*)"')
_ROUTE_TAG_RE = re.compile(r"<route\b[^>]*/>|<route\b[^>]*>.*?</route>", re.DOTALL)
_EDGES_ATTR_RE = re.compile(r'\bedges="([^"]*)"')
_VTYPE_TAG_RE = re.compile(r"<vType\b[^>]*/>|<vType\b[^>]*>")
_VCLASS_ATTR_RE = re.compile(r'\bvClass="([^"]*)"')


def _compatible_vtype_ids(text: str) -> frozenset[str]:
    """`<vType>` ids this policy can legally route (those declaring no
    `vClass`, which SUMO defaults to `DEFAULT_VCLASS`
    (https://sumo.dlr.de/docs/Definition_of_Vehicles,_Vehicle_Types,_and_Routes.html#vehicle_types),
    or declaring `vClass` equal to it). This project models exactly one
    vehicle category (see the module docstring and
    `traffic_sim.simulation.metadata.DEFAULT_VCLASS`); a `<vType>` for any
    other class is a real declaration this policy cannot prove legal routing
    for, so its id is deliberately excluded here and any vehicle referencing
    it fails closed in `_check_vehicle_class` rather than being silently
    routed on the single-category graph anyway."""
    compatible: set[str] = set()
    for match in _VTYPE_TAG_RE.finditer(text):
        tag = match.group()
        vtype_id = _ID_ATTR_RE.search(tag)
        if vtype_id is None:
            continue
        vclass_match = _VCLASS_ATTR_RE.search(tag)
        vclass = vclass_match.group(1) if vclass_match else DEFAULT_VCLASS
        if vclass == DEFAULT_VCLASS:
            compatible.add(vtype_id.group(1))
    return frozenset(compatible)


def _check_vehicle_class(
        vehicle_id: str, open_tag: str, compatible_vtype_ids: frozenset[str],
        route_path: Path) -> None:
    """Fail closed rather than route a vehicle whose declared type this
    single-category policy cannot prove is `DEFAULT_VCLASS`.

    No vehicle fragment in production route/candidate files declares `type=`
    at all (verified: no `<vType>` or per-vehicle `type` attribute is
    produced anywhere in this project's demand pipeline) -- every vehicle
    implicitly gets SUMO's own default vType, whose vClass is exactly
    `DEFAULT_VCLASS`. This check exists for the input this policy has never
    seen: a `type=` attribute referencing an undeclared or genuinely
    incompatible `<vType>`. Silently routing it on the single-vClass
    permission graph built for `DEFAULT_VCLASS` would be an unproven claim
    of legality, exactly the failure mode `run_scenario.build_edge_graph`'s
    permission filtering exists to prevent -- so this raises instead.
    """
    type_match = _TYPE_ATTR_RE.search(open_tag)
    if type_match is None:
        return
    declared_type = type_match.group(1)
    if declared_type not in compatible_vtype_ids:
        raise ClosureRoutingError(
            f"vehicle {vehicle_id!r} in {route_path} declares type="
            f"{declared_type!r}, which is not a known {DEFAULT_VCLASS!r} "
            "vType; this policy models exactly one vehicle category and "
            "cannot prove routing legality for an unrecognised type "
            "(fail-closed, not an execution error)")


@dataclass(frozen=True)
class AccessImpactRecord:
    """One vehicle this policy could not legally place on the network.

    Never a successful trip and never a generic execution timeout: a denial
    is decided here, deterministically, before SUMO runs at all, and is
    recorded with enough identity to reproduce the decision independently
    (`vehicle_id`, the trip's own original endpoints, the exact closed-edge
    set it was evaluated against, and the policy version that decided it).
    """

    vehicle_id: str
    reason: str
    original_origin: str
    original_destination: str
    original_depart_s: float
    applicable_closed_edges: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.reason not in ACCESS_IMPACT_REASONS:
            raise ClosureRoutingError(
                f"unknown access-impact reason {self.reason!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "reason": self.reason,
            "original_origin": self.original_origin,
            "original_destination": self.original_destination,
            "original_depart_s": self.original_depart_s,
            "applicable_closed_edges": list(self.applicable_closed_edges),
        }


@dataclass(frozen=True)
class ClosureRoutingResult:
    """Outcome of rewriting one route file for one closure."""

    unaffected: int
    rerouted: int
    denied: int
    access_impact: tuple[AccessImpactRecord, ...]
    #: Vehicle ids whose route WAS rewritten (a completed, successful
    #: detour -- never a denial). Needed alongside `access_impact` (denied
    #: ids only) so an independent reader can classify every vehicle in the
    #: source route into exactly one of unaffected/rerouted/denied by id,
    #: and in particular byte-diff the truly UNAFFECTED fragments between
    #: source and transformed route files (review finding 3: the previous
    #: verification tool gave up on this whenever anything at all was
    #: rerouted, because it had no way to name which vehicles were safe to
    #: compare).
    rerouted_vehicle_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.denied != len(self.access_impact):
            raise ClosureRoutingError(
                "denied count disagrees with the access-impact record count")
        if self.rerouted != len(self.rerouted_vehicle_ids):
            raise ClosureRoutingError(
                "rerouted count disagrees with the rerouted-vehicle-id count")


def _closures_overlapping(
    edges: Sequence[str],
    depart_s: float,
    closures: Sequence[Mapping[str, Any]] | None,
    close_edges_set: frozenset[str],
    edge_travel_s: Mapping[str, float],
) -> frozenset[str]:
    """Closed edges this edge sequence's transit cannot be PROVEN to miss.

    For each edge with a declared window, this asks one question only: has
    the free-flow lower bound on this vehicle's occupancy already reached or
    passed the window's `end_s`? If yes, the edge is provably safe (real,
    slower-than-free-flow transit only pushes the true arrival later, i.e.
    further past `end_s`, never back into the window) and is left out of the
    result. If no -- including every case where the window is still in the
    future relative to the free-flow estimate, no matter how far -- there is
    no available proof of safety (that would require an upper bound on
    congestion delay, which does not exist), so the edge is APPLICABLE. This
    can only ever classify MORE trips as affected than a margin-based guess
    would, never fewer, which is the required direction of error.

    `closures is None` is the legacy whole-run form (`--close` with no
    explicit window): every edge in `close_edges_set` that appears in
    `edges` applies unconditionally, with no timing computed at all.
    """
    if closures is None:
        return frozenset(edge for edge in edges if edge in close_edges_set)
    windows_by_edge: dict[str, list[tuple[int, int]]] = {}
    for closure in closures:
        windows_by_edge.setdefault(closure["edge_id"], []).append(
            (closure["begin_s"], closure["end_s"]))
    applicable: set[str] = set()
    elapsed = 0.0
    for edge in edges:
        windows = windows_by_edge.get(edge)
        if windows:
            occupancy_lower_bound = _edge_occupancy_lower_bound(depart_s, elapsed)
            for _begin_s, end_s in windows:
                if occupancy_lower_bound < end_s:
                    applicable.add(edge)
                    break
        elapsed += edge_travel_s.get(edge, 0.0)
    return frozenset(applicable)


def _plan_detour(
    origin: str,
    destination: str,
    depart_s: float,
    *,
    adjacency: Mapping[str, Sequence[str]],
    costs: Mapping[str, float],
    closures: Sequence[Mapping[str, Any]] | None,
    close_edges_set: frozenset[str],
    edge_travel_s: Mapping[str, float],
    initial_banned: frozenset[str],
) -> tuple[list[str] | None, frozenset[str], str | None]:
    """Fixed point: reroute, re-check which closures the new path crosses,
    repeat until the banned set stops growing.

    `banned` only ever grows (set union), which makes termination provable
    rather than merely likely: each non-terminal iteration adds at least one
    new edge to `banned`, and `banned` is bounded above by
    `close_edges_set`, so at most `len(close_edges_set)` growth steps can
    occur before the loop must stabilize. The `+ 1` iteration budget is that
    bound plus one final stability check — not an arbitrary cutoff. If a
    caller-supplied graph ever violated the bound (it cannot, given a finite
    `close_edges_set`), this fails closed to `no_legal_path` rather than
    publish an unstable route.
    """
    banned = initial_banned
    for _ in range(len(close_edges_set) + 1):
        path = disruption_analysis.shortest_path_edges(
            adjacency, costs, origin, destination, banned)
        if path is None:
            return None, banned, NO_LEGAL_PATH
        newly_applicable = _closures_overlapping(
            path, depart_s, closures, close_edges_set, edge_travel_s)
        combined = banned | newly_applicable
        if combined == banned:
            return path, banned, None
        banned = combined
    return None, banned, NO_LEGAL_PATH


def rewrite_route_file(
    route_path: Path,
    close_edges: Sequence[str],
    out_path: Path,
    adjacency: Mapping[str, Sequence[str]],
    *,
    edge_travel_s: Mapping[str, float],
    closures: Sequence[Mapping[str, Any]] | None = None,
) -> ClosureRoutingResult:
    """Rewrite every affected vehicle's route around a closure, in place.

    `adjacency` MUST be the full, un-banned edge graph (e.g.
    `run_scenario.build_edge_graph(set())`) — this function evaluates a
    per-vehicle banned set itself (`_plan_detour`), so a caller-pre-banned
    graph would silently apply the wrong exclusion set to every vehicle.

    Unaffected vehicles (their route never touches `close_edges` at all, or
    it does but the closure's declared window never overlaps their transit)
    are copied BYTE-FOR-BYTE from the source file — this function only ever
    substitutes the `edges="..."` attribute VALUE of an affected vehicle's
    `<route>` child, so every other byte of every vehicle element (id,
    depart, departPos/arrivalPos, any other attribute, and all inter-vehicle
    whitespace) is preserved exactly, matching the requirement that a
    rewrite never touches origin/destination positions or vehicle identity.

    Fails closed (`ClosureRoutingError`) on any vehicle fragment shape this
    parser does not recognise (missing id/depart, zero or multiple `<route>`
    children, an empty edge list) rather than silently miscounting it.
    """
    close_edges_set = frozenset(close_edges)
    if not close_edges_set:
        raise ClosureRoutingError(
            "rewrite_route_file requires at least one closed edge")

    text = _read_route_text(Path(route_path))
    matches = list(_VEHICLE_FRAGMENT_RE.finditer(text))
    compatible_vtype_ids = _compatible_vtype_ids(text)

    out_parts: list[str] = []
    last_end = 0
    n_unaffected = 0
    n_rerouted = 0
    records: list[AccessImpactRecord] = []
    rerouted_vehicle_ids: list[str] = []

    for match in matches:
        out_parts.append(text[last_end:match.start()])
        last_end = match.end()
        fragment = match.group()

        open_tag_match = _OPEN_TAG_RE.match(fragment)
        if open_tag_match is None:
            raise ClosureRoutingError(
                f"unsupported vehicle fragment shape in {route_path}")
        open_tag = open_tag_match.group()
        id_match = _ID_ATTR_RE.search(open_tag)
        depart_match = _DEPART_ATTR_RE.search(open_tag)
        if id_match is None or depart_match is None:
            raise ClosureRoutingError(
                f"vehicle fragment missing id/depart in {route_path}")
        vehicle_id = id_match.group(1)
        depart_s = float(depart_match.group(1))
        _check_vehicle_class(vehicle_id, open_tag, compatible_vtype_ids, route_path)

        route_matches = list(_ROUTE_TAG_RE.finditer(fragment))
        if len(route_matches) != 1:
            raise ClosureRoutingError(
                f"vehicle {vehicle_id!r} in {route_path} does not have "
                f"exactly one <route> element ({len(route_matches)} found); "
                "unsupported route shape")
        route_match = route_matches[0]
        route_tag = route_match.group()
        edges_match = _EDGES_ATTR_RE.search(route_tag)
        if edges_match is None:
            raise ClosureRoutingError(
                f"vehicle {vehicle_id!r} in {route_path} has a <route> "
                "with no edges attribute")
        edges = edges_match.group(1).split()
        if not edges:
            raise ClosureRoutingError(
                f"vehicle {vehicle_id!r} in {route_path} has an empty "
                "route edges list")

        if not (close_edges_set & set(edges)):
            out_parts.append(fragment)
            n_unaffected += 1
            continue

        # Evaluate applicability ONCE, up front, and reuse it both for the
        # destination-closed decision and as the detour planner's starting
        # banned set. Deciding destination_closed from bare membership in
        # close_edges_set (the old behaviour) denied every trip whose
        # destination ever appears in the closed-edge list, even one that
        # arrives long before the closure begins or well after it has
        # reopened -- see review finding on windowed destination denial.
        destination = edges[-1]
        initial_applicable = _closures_overlapping(
            edges, depart_s, closures, close_edges_set, edge_travel_s)
        if not initial_applicable:
            # Raw route touches a member of close_edges_set, but (windowed
            # mode only) the closure's declared window never overlaps this
            # vehicle's own transit -- not actually affected. Leaving it
            # untouched here, rather than routing it through an unbanned
            # (and therefore unconstrained/generic-shortest-path) planner,
            # is what keeps a genuinely unaffected calibrated route exact.
            out_parts.append(fragment)
            n_unaffected += 1
            continue

        if destination in initial_applicable:
            records.append(AccessImpactRecord(
                vehicle_id=vehicle_id, reason=DESTINATION_CLOSED,
                original_origin=edges[0], original_destination=destination,
                original_depart_s=depart_s,
                applicable_closed_edges=tuple(sorted(initial_applicable))))
            continue

        initial_banned = initial_applicable

        new_edges, banned, reason = _plan_detour(
            edges[0], destination, depart_s,
            adjacency=adjacency, costs=edge_travel_s, closures=closures,
            close_edges_set=close_edges_set, edge_travel_s=edge_travel_s,
            initial_banned=initial_banned)

        if reason is not None:
            records.append(AccessImpactRecord(
                vehicle_id=vehicle_id, reason=reason,
                original_origin=edges[0], original_destination=destination,
                original_depart_s=depart_s,
                applicable_closed_edges=tuple(sorted(banned))))
            continue

        if new_edges == edges:
            out_parts.append(fragment)
            n_unaffected += 1
            continue

        # Independently confirm the route this vehicle is about to be handed
        # cannot itself encounter an active closure, rather than trust the
        # fixed point's own termination condition implicitly. This calls the
        # SAME predicate as the planner, which is legitimate here (not the
        # circular "prove safety with an unproven margin" the old 900s
        # constant did): `_closures_overlapping` is now a one-directional,
        # evidence-backed proof rule (see its docstring), not a heuristic
        # guess, so re-evaluating it on the FINAL route is a real second
        # check that the fixed point actually converged on a safe path, not
        # a repetition of an unsound estimate. A vehicle must never reach
        # SUMO on a route this function itself would flag as affected.
        residual = _closures_overlapping(
            new_edges, depart_s, closures, close_edges_set, edge_travel_s)
        if residual:
            raise ClosureRoutingError(
                f"vehicle {vehicle_id!r} in {route_path}: rewritten route "
                f"still overlaps closure window on {sorted(residual)}")

        new_edges_str = " ".join(new_edges)
        new_route_tag = (route_tag[:edges_match.start()]
                         + f'edges="{new_edges_str}"'
                         + route_tag[edges_match.end():])
        new_fragment = (fragment[:route_match.start()] + new_route_tag
                        + fragment[route_match.end():])
        out_parts.append(new_fragment)
        n_rerouted += 1
        rerouted_vehicle_ids.append(vehicle_id)

    out_parts.append(text[last_end:])
    Path(out_path).write_text("".join(out_parts), encoding="utf-8")

    return ClosureRoutingResult(
        unaffected=n_unaffected, rerouted=n_rerouted, denied=len(records),
        access_impact=tuple(records),
        rerouted_vehicle_ids=tuple(rerouted_vehicle_ids))


#: Fields `RoutingProvenance.from_dict` requires, exactly -- no more, no
#: fewer. A dict from an older pass (missing `vehicle_class`/`denied_count`,
#: or naming the field `truncated_unreachable`) fails validation rather than
#: silently being accepted with a `None`/`0` default standing in for a
#: fact nobody actually recorded. See the module version-bump note above
#: `POLICY_VERSION` for why "old evidence must never satisfy a new lookup"
#: is the standing rule this dataclass exists to enforce for one specific,
#: previously free-form provenance dict (review finding 4).
_ROUTING_PROVENANCE_FIELDS = frozenset({
    "routing_policy_version", "vehicle_class", "unit_id", "candidate_id",
    "work_date", "demand_variant", "seed", "execution_arm",
    "access_impact_sha256", "transformed_route_sha256",
    "rerouted_around_closure", "denied_count",
})

#: A lowercase SHA-256 hex digest, exactly. `len(...) == 64` alone accepts
#: any 64-character string (including non-hex garbage); this is the actual
#: content check review finding 1 asked for.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _require_hex64(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ClosureRoutingError(
            f"routing provenance {field} must be a lowercase sha256 hex "
            f"digest, got {value!r}")


@dataclass(frozen=True)
class RoutingProvenance:
    """Validated identity bound into one monthly candidate observation's
    `provenance["routing_provenance"]`.

    Carries exactly what a reader needs to (a) know which routing policy and
    vehicle category produced this observation, well enough that an
    incompatible one can never satisfy a lookup meant for another, (b)
    identify the daily unit/schedule this observation belongs to
    (`unit_id`, `candidate_id`), and (c) resolve BOTH the transformed route
    that was actually handed to SUMO (`transformed_route_sha256`, checked
    directly against the file) and the FULL per-vehicle access-impact
    evidence (`access_impact_sha256`, content-addressed under the cache's
    `access-impact/` store) rather than trusting a bare count. Denial
    REASONS live in the resolvable access-impact report, not duplicated
    here — see `AccessImpactRecord`/`write_access_impact_report`. Both
    digest fields are REQUIRED (never `None`): a successful closure
    observation always ran `write_access_impact_report` against a real
    output route file, so a missing digest here is lost evidence, not a
    legitimate state — see the v3->v4 POLICY_VERSION note.
    """

    routing_policy_version: str
    vehicle_class: str
    unit_id: str
    candidate_id: str
    work_date: str
    demand_variant: str
    seed: int
    execution_arm: str
    access_impact_sha256: str
    transformed_route_sha256: str
    rerouted_around_closure: int
    denied_count: int

    def __post_init__(self) -> None:
        if self.routing_policy_version != POLICY_VERSION:
            raise ClosureRoutingError(
                f"routing provenance policy version "
                f"{self.routing_policy_version!r} does not match the "
                f"currently deployed {POLICY_VERSION!r}")
        if self.vehicle_class != DEFAULT_VCLASS:
            raise ClosureRoutingError(
                f"routing provenance vehicle_class {self.vehicle_class!r} "
                f"is not the single modeled category {DEFAULT_VCLASS!r}")
        if not self.unit_id or not self.candidate_id or not self.work_date \
                or not self.demand_variant:
            raise ClosureRoutingError(
                "routing provenance requires unit_id/candidate_id/work_date/"
                "demand_variant")
        try:
            parsed_work_date = date.fromisoformat(self.work_date)
        except (TypeError, ValueError) as error:
            raise ClosureRoutingError(
                "routing provenance work_date must be canonical ISO date") from error
        if parsed_work_date.isoformat() != self.work_date:
            raise ClosureRoutingError(
                "routing provenance work_date must be canonical ISO date")
        if self.demand_variant not in {"q10", "q50", "q90"}:
            raise ClosureRoutingError(
                "routing provenance demand_variant must be q10, q50 or q90")
        if self.execution_arm not in ("cold", "warm"):
            raise ClosureRoutingError(
                f"routing provenance execution_arm must be 'cold' or "
                f"'warm', got {self.execution_arm!r}")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ClosureRoutingError("routing provenance seed must be a non-negative int")
        if (isinstance(self.rerouted_around_closure, bool)
                or not isinstance(self.rerouted_around_closure, int)
                or self.rerouted_around_closure < 0):
            raise ClosureRoutingError(
                "routing provenance rerouted_around_closure must be a "
                "non-negative int")
        if (isinstance(self.denied_count, bool)
                or not isinstance(self.denied_count, int)
                or self.denied_count < 0):
            raise ClosureRoutingError(
                "routing provenance denied_count must be a non-negative int")
        _require_hex64(self.access_impact_sha256, "access_impact_sha256")
        _require_hex64(self.transformed_route_sha256, "transformed_route_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "routing_policy_version": self.routing_policy_version,
            "vehicle_class": self.vehicle_class,
            "unit_id": self.unit_id,
            "candidate_id": self.candidate_id,
            "work_date": self.work_date,
            "demand_variant": self.demand_variant,
            "seed": self.seed,
            "execution_arm": self.execution_arm,
            "access_impact_sha256": self.access_impact_sha256,
            "transformed_route_sha256": self.transformed_route_sha256,
            "rerouted_around_closure": self.rerouted_around_closure,
            "denied_count": self.denied_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RoutingProvenance":
        if not isinstance(raw, Mapping) or set(raw) != _ROUTING_PROVENANCE_FIELDS:
            raise ClosureRoutingError(
                "routing provenance payload fields do not match the "
                "current schema (incompatible/legacy evidence)")
        return cls(**dict(raw))


def validate_access_impact_report(
    raw: Mapping[str, Any], provenance: RoutingProvenance,
    *, transformed_route_path: Path,
) -> dict[str, Any]:
    """Strictly bind one durable access-impact report to its observation.

    Hash verification proves only that a file has not changed since it was
    named.  It does not prove that the file belongs to this daily unit, SUMO
    arm, variant or transformed route.  This parser therefore validates the
    complete current report schema and cross-checks every duplicated identity
    and count against ``RoutingProvenance``.  Old, partial or individually
    valid-but-swapped reports fail closed.
    """
    if not isinstance(raw, Mapping) or set(raw) != _ACCESS_IMPACT_REPORT_FIELDS:
        raise ClosureRoutingError(
            "access-impact report fields do not match the current schema")
    report = dict(raw)
    if (isinstance(report["schema_version"], bool)
            or report["schema_version"] != ACCESS_IMPACT_SCHEMA_VERSION):
        raise ClosureRoutingError(
            "access-impact report schema_version is incompatible")
    if report["kind"] != ACCESS_IMPACT_SCHEMA:
        raise ClosureRoutingError("access-impact report kind is incompatible")
    if report["policy_version"] != provenance.routing_policy_version:
        raise ClosureRoutingError(
            "access-impact report policy does not match routing provenance")

    identity = report["identity"]
    if (not isinstance(identity, Mapping)
            or set(identity) != _ACCESS_IMPACT_IDENTITY_FIELDS):
        raise ClosureRoutingError(
            "access-impact report identity fields do not match the current schema")
    expected_identity = {
        "unit_id": provenance.unit_id,
        "candidate_id": provenance.candidate_id,
        "work_date": provenance.work_date,
        "demand_variant": provenance.demand_variant,
        "seed": provenance.seed,
        "execution_arm": provenance.execution_arm,
        "vehicle_class": provenance.vehicle_class,
    }
    if dict(identity) != expected_identity:
        raise ClosureRoutingError(
            "access-impact report identity does not match routing provenance")

    if not isinstance(report["windowed"], bool):
        raise ClosureRoutingError("access-impact report windowed must be boolean")
    close_edges = report["close_edges"]
    if (not isinstance(close_edges, list)
            or any(not isinstance(edge, str) or not edge for edge in close_edges)
            or close_edges != sorted(set(close_edges))):
        raise ClosureRoutingError(
            "access-impact report close_edges must be sorted unique strings")
    closures = report["closures"]
    if report["windowed"] != (closures is not None):
        raise ClosureRoutingError(
            "access-impact report windowed flag disagrees with closures")
    if closures is not None:
        if not isinstance(closures, list):
            raise ClosureRoutingError("access-impact report closures must be a list")
        for closure in closures:
            if (not isinstance(closure, Mapping)
                    or set(closure) != _ACCESS_IMPACT_CLOSURE_FIELDS):
                raise ClosureRoutingError(
                    "access-impact closure fields do not match the current schema")
            edge_id = closure["edge_id"]
            begin_s = closure["begin_s"]
            end_s = closure["end_s"]
            if (not isinstance(edge_id, str) or edge_id not in close_edges
                    or isinstance(begin_s, bool) or not isinstance(begin_s, int)
                    or isinstance(end_s, bool) or not isinstance(end_s, int)
                    or begin_s < 0 or end_s <= begin_s):
                raise ClosureRoutingError("access-impact closure is invalid")

    for field in ("source_route_sha256", "output_route_sha256"):
        _require_hex64(report[field], f"access-impact {field}")
    if report["network_sha256"] is not None:
        _require_hex64(report["network_sha256"], "access-impact network_sha256")
    if report["output_route_sha256"] != provenance.transformed_route_sha256:
        raise ClosureRoutingError(
            "access-impact output route does not match routing provenance")

    summary = report["summary"]
    if (not isinstance(summary, Mapping)
            or set(summary) != _ACCESS_IMPACT_SUMMARY_FIELDS):
        raise ClosureRoutingError(
            "access-impact summary fields do not match the current schema")
    for field in _ACCESS_IMPACT_SUMMARY_FIELDS:
        value = summary[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ClosureRoutingError(
                f"access-impact summary {field} must be a non-negative int")

    raw_records = report["access_impact"]
    if not isinstance(raw_records, list):
        raise ClosureRoutingError("access-impact records must be a list")
    denied_ids: list[str] = []
    for raw_record in raw_records:
        if (not isinstance(raw_record, Mapping)
                or set(raw_record) != _ACCESS_IMPACT_RECORD_FIELDS):
            raise ClosureRoutingError(
                "access-impact record fields do not match the current schema")
        vehicle_id = raw_record["vehicle_id"]
        origin = raw_record["original_origin"]
        destination = raw_record["original_destination"]
        depart_s = raw_record["original_depart_s"]
        applicable = raw_record["applicable_closed_edges"]
        if (not isinstance(vehicle_id, str) or not vehicle_id
                or not isinstance(origin, str) or not origin
                or not isinstance(destination, str) or not destination
                or isinstance(depart_s, bool)
                or not isinstance(depart_s, (int, float))
                or not math.isfinite(depart_s) or depart_s < 0
                or not isinstance(applicable, list)
                or any(not isinstance(edge, str) or edge not in close_edges
                       for edge in applicable)
                or applicable != sorted(set(applicable))
                or raw_record["reason"] not in ACCESS_IMPACT_REASONS):
            raise ClosureRoutingError("access-impact record is invalid")
        denied_ids.append(vehicle_id)
    if len(denied_ids) != len(set(denied_ids)):
        raise ClosureRoutingError("access-impact denied vehicle ids are duplicated")

    rerouted_ids = report["rerouted_vehicle_ids"]
    if (not isinstance(rerouted_ids, list)
            or any(not isinstance(vehicle_id, str) or not vehicle_id
                   for vehicle_id in rerouted_ids)
            or rerouted_ids != sorted(set(rerouted_ids))):
        raise ClosureRoutingError(
            "access-impact rerouted vehicle ids must be sorted unique strings")
    if set(denied_ids) & set(rerouted_ids):
        raise ClosureRoutingError(
            "access-impact vehicle cannot be both rerouted and denied")
    if (summary["denied"] != len(raw_records)
            or summary["denied"] != provenance.denied_count):
        raise ClosureRoutingError(
            "access-impact denied counts disagree with routing provenance")
    if (summary["rerouted"] != len(rerouted_ids)
            or summary["rerouted"] != provenance.rerouted_around_closure):
        raise ClosureRoutingError(
            "access-impact rerouted counts disagree with routing provenance")

    transformed_text = _read_route_text(Path(transformed_route_path))
    transformed_ids: list[str] = []
    for match in _VEHICLE_FRAGMENT_RE.finditer(transformed_text):
        open_tag = _OPEN_TAG_RE.match(match.group())
        vehicle_match = (
            _ID_ATTR_RE.search(open_tag.group()) if open_tag is not None else None)
        if vehicle_match is None or not vehicle_match.group(1):
            raise ClosureRoutingError(
                "transformed route contains a vehicle without an id")
        transformed_ids.append(vehicle_match.group(1))
    if len(transformed_ids) != len(set(transformed_ids)):
        raise ClosureRoutingError("transformed route vehicle ids are duplicated")
    transformed_id_set = set(transformed_ids)
    if len(transformed_ids) != summary["unaffected"] + summary["rerouted"]:
        raise ClosureRoutingError(
            "access-impact unaffected/rerouted summary disagrees with "
            "transformed route population")
    if not set(rerouted_ids) <= transformed_id_set:
        raise ClosureRoutingError(
            "access-impact rerouted vehicle is missing from transformed route")
    if set(denied_ids) & transformed_id_set:
        raise ClosureRoutingError(
            "access-impact denied vehicle is present in transformed route")
    return report


def write_access_impact_report(
    path: Path,
    *,
    result: ClosureRoutingResult,
    close_edges: Sequence[str],
    closures: Sequence[Mapping[str, Any]] | None,
    source_route_path: Path,
    out_route_path: Path,
    network_path: Path | None = None,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the stable, hashable per-run access-impact evidence file.

    Carries reason, vehicle identity, original endpoints, the exact closed-
    edge set, the policy version, and content digests for the source route,
    output route and (when known) the network — enough for an independent
    reader to reproduce or audit the decision without re-running SUMO.

    `identity`, when complete, binds this evidence to the caller's own run
    identity (candidate/unit id, schedule id, work date, demand variant,
    vehicle class, ...) so a monthly reader can trace one denial back to the
    exact run that produced it without guessing from file layout. Reports
    without that exact identity population remain useful diagnostics, but
    are deliberately labelled with a distinct diagnostic kind/version so
    they can never be mistaken for durable monthly evidence.
    """
    identity_payload = dict(identity) if identity is not None else None
    durable_identity = (
        isinstance(identity, Mapping)
        and set(identity_payload) == _ACCESS_IMPACT_IDENTITY_FIELDS
    )
    payload = {
        # 1->2 added rerouted ids; 2->3 makes the monthly identity complete
        # and is strictly validated by `validate_access_impact_report`.
        "schema_version": (
            ACCESS_IMPACT_SCHEMA_VERSION if durable_identity
            else ACCESS_IMPACT_DIAGNOSTIC_SCHEMA_VERSION),
        "kind": (
            ACCESS_IMPACT_SCHEMA if durable_identity
            else ACCESS_IMPACT_DIAGNOSTIC_SCHEMA),
        "policy_version": POLICY_VERSION,
        "identity": identity_payload,
        "windowed": closures is not None,
        "close_edges": sorted(close_edges),
        "closures": (
            [dict(closure) for closure in closures]
            if closures is not None else None),
        "source_route_sha256": sha256_file(Path(source_route_path)),
        "output_route_sha256": sha256_file(Path(out_route_path)),
        "network_sha256": (
            sha256_file(Path(network_path)) if network_path is not None
            else None),
        "summary": {
            "unaffected": result.unaffected,
            "rerouted": result.rerouted,
            "denied": result.denied,
        },
        "access_impact": [record.to_dict() for record in result.access_impact],
        "rerouted_vehicle_ids": sorted(result.rerouted_vehicle_ids),
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def prepare_route_file(
    route_path: Path,
    close_edges: Sequence[str],
    out_path: Path,
    adjacency: Mapping[str, Sequence[str]],
    *,
    edge_travel_s: Mapping[str, float],
    closures: Sequence[Mapping[str, Any]] | None = None,
    access_impact_path: Path | None = None,
    network_path: Path | None = None,
    identity: Mapping[str, Any] | None = None,
) -> ClosureRoutingResult:
    """`rewrite_route_file` plus, when requested, the access-impact evidence
    file alongside it. The single entry point production callers use."""
    result = rewrite_route_file(
        route_path, close_edges, out_path, adjacency,
        edge_travel_s=edge_travel_s, closures=closures)
    if access_impact_path is not None:
        write_access_impact_report(
            access_impact_path, result=result, close_edges=close_edges,
            closures=closures, source_route_path=route_path,
            out_route_path=out_path, network_path=network_path,
            identity=identity)
    return result
