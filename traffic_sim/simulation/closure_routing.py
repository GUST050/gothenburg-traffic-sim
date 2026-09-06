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
reachability. If the original destination edge itself is closed, its
`arrivalPos` is treated as the physical destination: the route is instead
ended at the nearest reachable open road position within the same 180-metre
access radius used by demand generation, with both the replacement edge and
walking/access distance recorded. A vehicle is only left at the network
boundary (never simulated, waited or teleported) when no such nearby access
or no legal path exists — see `AccessImpactRecord`. Because
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
import hashlib
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
#:
#: v5 -> v6 (2026-09-04): a trip whose physical destination lies on a closed
#: final edge is routed to the nearest reachable open road position within
#: the demand model's existing 180 m access radius. The transformed route's
#: final edge/arrivalPos and the physical access distance are bound into the
#: access-impact evidence. A v5 result denied these trips outright, so its
#: decisions and evidence are incompatible with v6.
#:
#: v6 -> v7 (2026-09-04): destination access now uses the selected lane's
#: geometry rather than an edge centre line, measures after the two-metre
#: endpoint inset, and validates the written position/distance against the
#: bound network. v6 reports can therefore understate access distance.
#: v7 -> v8 (2026-09-05, review findings 1-3 and the follow-up review):
#: three changes, each of which can move a trip between
#: unaffected/rerouted/relocated/denied for the SAME input.
#:  * The overlap rule reads `begin_s` and bounds occupancy above by the
#:    DECLARED `disruption.MAX_ASSUMED_CONGESTION_DELAY_S` instead of
#:    treating every window before `end_s` as reachable, so a vehicle
#:    provably ahead of the roadworks is no longer rerouted. That bound is a
#:    modelling assumption, not a measurement: it is a parameter of this
#:    function, and a study that varies it is varying the routing policy.
#:  * The whole per-vehicle decision -- applicability, the detour fixed
#:    point AND the destination-access choice -- moved into
#:    `disruption.ClosureRouteResolver`, which the deterministic cost shares.
#:    This function no longer holds a second copy of any of it.
#:  * A destination whose own window is discovered only DURING replanning
#:    (the detour arrives later than the original route would have) is now
#:    offered the same nearby-access remedy as one closed from the outset,
#:    instead of being denied `no_legal_path`.
#: v7 evidence, caches and provenance are therefore incompatible with v8.
POLICY_VERSION = "closure_origin_routing_v8"

#: Reasons `AccessImpactRecord.reason` is allowed to take. Nothing else may
#: ever be written; a caller adding a new denial path must extend this set
#: explicitly rather than let an ad-hoc string drift into evidence.
DESTINATION_CLOSED = disruption_analysis.DESTINATION_CLOSED
NO_LEGAL_PATH = disruption_analysis.NO_LEGAL_PATH
ACCESS_IMPACT_REASONS = frozenset({DESTINATION_CLOSED, NO_LEGAL_PATH})

ACCESS_IMPACT_SCHEMA = "closure_access_impact_v1"
ACCESS_IMPACT_SCHEMA_VERSION = 5
ACCESS_IMPACT_DIAGNOSTIC_SCHEMA = "closure_access_impact_diagnostic_v1"
ACCESS_IMPACT_DIAGNOSTIC_SCHEMA_VERSION = 1

_ACCESS_IMPACT_REPORT_FIELDS = frozenset({
    "schema_version", "kind", "policy_version", "identity", "windowed",
    "close_edges", "closures", "source_route_sha256", "output_route_sha256",
    "network_sha256", "summary", "access_impact", "destination_relocations",
    "rerouted_vehicle_ids",
})
_ACCESS_IMPACT_IDENTITY_FIELDS = frozenset({
    "unit_id", "candidate_id", "work_date", "demand_variant", "seed",
    "execution_arm", "vehicle_class",
})
_ACCESS_IMPACT_SUMMARY_FIELDS = frozenset({
    "unaffected", "rerouted", "destination_relocated", "denied",
})
_ACCESS_IMPACT_RECORD_FIELDS = frozenset({
    "vehicle_id", "reason", "original_origin", "original_destination",
    "original_depart_s", "applicable_closed_edges",
})
_DESTINATION_RELOCATION_RECORD_FIELDS = frozenset({
    "vehicle_id", "original_origin", "original_destination",
    "replacement_destination", "original_depart_s", "original_arrival_pos",
    "replacement_arrival_pos", "access_distance_m",
    "applicable_closed_edges",
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
#: The shared rule in `disruption.applicable_closed_edges` replaces the margin
#: with the one interval fact that IS
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
#: affected relative to the old rule, never narrow it. Both pre-SUMO routing
#: and deterministic costing call that exact function.


class ClosureRoutingError(ValueError):
    """Raised when a route cannot be classified or rewritten deterministically."""


def require_sumo_population_identity(
    expected: int,
    *,
    loaded: int,
    inserted: int,
    trip_count: int,
    context: str,
) -> None:
    """Reject a SUMO run that did not execute its transformed population."""
    values = (expected, loaded, inserted, trip_count)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in values):
        raise ClosureRoutingError(
            f"{context}: SUMO population counters must be non-negative ints")
    if not expected == loaded == inserted == trip_count:
        raise ClosureRoutingError(
            f"{context}: transformed route population={expected}, "
            f"loaded={loaded}, inserted={inserted}, trip_count={trip_count}")


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
_ARRIVAL_POS_ATTR_RE = re.compile(r'\barrivalPos="([^"]*)"')
_TYPE_ATTR_RE = re.compile(r'\btype="([^"]*)"')
_ROUTE_TAG_RE = re.compile(r"<route\b[^>]*/>|<route\b[^>]*>.*?</route>", re.DOTALL)
_EDGES_ATTR_RE = re.compile(r'\bedges="([^"]*)"')
_VTYPE_TAG_RE = re.compile(r"<vType\b[^>]*/>|<vType\b[^>]*>")
_VCLASS_ATTR_RE = re.compile(r'\bvClass="([^"]*)"')


def _set_xml_attribute(tag: str, name: str, value: str) -> str:
    """Replace or append one double-quoted attribute in an opening tag."""
    pattern = re.compile(rf'\b{re.escape(name)}="[^"]*"')
    replacement = f'{name}="{value}"'
    if pattern.search(tag):
        return pattern.sub(replacement, tag, count=1)
    if not tag.endswith(">"):
        raise ClosureRoutingError(f"cannot set {name} on malformed XML tag")
    return tag[:-1] + f" {replacement}>"


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
class DestinationRelocationRecord:
    """One completed trip moved to a legal road point near the same place."""

    vehicle_id: str
    original_origin: str
    original_destination: str
    replacement_destination: str
    original_depart_s: float
    original_arrival_pos: str | None
    replacement_arrival_pos: float
    access_distance_m: float
    applicable_closed_edges: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "original_origin": self.original_origin,
            "original_destination": self.original_destination,
            "replacement_destination": self.replacement_destination,
            "original_depart_s": self.original_depart_s,
            "original_arrival_pos": self.original_arrival_pos,
            "replacement_arrival_pos": self.replacement_arrival_pos,
            "access_distance_m": self.access_distance_m,
            "applicable_closed_edges": list(self.applicable_closed_edges),
        }


@dataclass(frozen=True)
class ClosureRoutingResult:
    """Outcome of rewriting one route file for one closure."""

    unaffected: int
    rerouted: int
    denied: int
    access_impact: tuple[AccessImpactRecord, ...]
    destination_relocations: tuple[DestinationRelocationRecord, ...] = ()
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
        relocation_ids = {
            record.vehicle_id for record in self.destination_relocations
        }
        if not relocation_ids <= set(self.rerouted_vehicle_ids):
            raise ClosureRoutingError(
                "destination relocation is not a completed rerouted vehicle")


def _closures_overlapping(
    edges: Sequence[str],
    depart_s: float,
    closures: Sequence[Mapping[str, Any]] | None,
    close_edges_set: frozenset[str],
    edge_travel_s: Mapping[str, float],
) -> frozenset[str]:
    """Delegate to the timing predicate shared with deterministic costing.

    Keeping the predicate in one place is an evidence invariant: the route
    population actually presented to SUMO must be the population the ledger
    prices, including conservative treatment of future windows.
    """
    return disruption_analysis.applicable_closed_edges(
        edges, depart_s, closures, close_edges_set, edge_travel_s)


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
    """Delegate the detour fixed point to the shared closure decision.

    Retained as a thin seam so existing tests keep addressing the rule by
    this name; the rule itself now lives in `disruption.ClosureRouteResolver`
    so the deterministic cost runs the identical fixed point instead of
    banning the whole closed-edge set for every vehicle.
    """
    resolver = disruption_analysis.ClosureRouteResolver(
        adjacency, costs, None, close_edges_set)
    route, banned, reason = resolver.plan(
        origin, destination, depart_s, closures, initial_banned)
    return (list(route) if route is not None else None), banned, reason


def rewrite_route_file(
    route_path: Path,
    close_edges: Sequence[str],
    out_path: Path,
    adjacency: Mapping[str, Sequence[str]],
    *,
    edge_travel_s: Mapping[str, float],
    closures: Sequence[Mapping[str, Any]] | None = None,
    destination_access: (
        disruption_analysis.DestinationAccessResolver | None) = None,
    max_assumed_delay_s: float = (
        disruption_analysis.MAX_ASSUMED_CONGESTION_DELAY_S),
) -> ClosureRoutingResult:
    """Rewrite every affected vehicle's route around a closure, in place.

    `adjacency` MUST be the full, un-banned edge graph (e.g.
    `run_scenario.build_edge_graph(set())`) — the shared decision object
    evaluates a per-vehicle banned set itself, so a caller-pre-banned graph
    would silently apply the wrong exclusion set to every vehicle.

    `max_assumed_delay_s` is the DECLARED congestion-delay bound documented on
    `disruption.MAX_ASSUMED_CONGESTION_DELAY_S`. It changes which vehicles a
    window reaches, so a study that varies it is varying the routing policy,
    not a tuning knob; the value in force is recorded beside every cost this
    same decision produces.

    Unaffected vehicles (their route never touches `close_edges` at all, or
    it does but the closure's declared window never overlaps their transit)
    are copied BYTE-FOR-BYTE from the source file — this function only ever
    substitutes the `edges="..."` attribute VALUE of an affected vehicle's
    `<route>` child. The sole additional mutation is a destination relocation:
    when the original final edge is closed but the same physical arrival point
    has a nearby reachable open access, `arrivalPos` is replaced with that
    access point's position. Every other byte and vehicle identity is retained.

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
    resolver = disruption_analysis.ClosureRouteResolver(
        adjacency, edge_travel_s, None, close_edges_set,
        destination_access=destination_access,
        max_assumed_delay_s=max_assumed_delay_s)

    out_parts: list[str] = []
    last_end = 0
    n_unaffected = 0
    n_rerouted = 0
    records: list[AccessImpactRecord] = []
    relocations: list[DestinationRelocationRecord] = []
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

        destination = edges[-1]
        arrival_match = _ARRIVAL_POS_ATTR_RE.search(open_tag)
        original_arrival_pos = (
            arrival_match.group(1) if arrival_match is not None else None)
        # ONE decision, shared with the deterministic cost. This function used
        # to keep its own destination-access loop beside the shared fixed
        # point, which is exactly the divergence risk the shared object exists
        # to remove: a later change to how a closed destination is handled
        # would have had to be made twice, correctly, in both places.
        outcome = resolver.resolve(
            edges, depart_s, original_arrival_pos, closures)
        if outcome is None:
            # Raw route touches a member of close_edges_set, but (windowed
            # mode only) the closure's declared window never overlaps this
            # vehicle's own transit -- not actually affected. Leaving it
            # untouched here, rather than routing it through an unbanned
            # (and therefore unconstrained/generic-shortest-path) planner,
            # is what keeps a genuinely unaffected calibrated route exact.
            out_parts.append(fragment)
            n_unaffected += 1
            continue

        new_edges = list(outcome.route) if outcome.route is not None else None
        banned = outcome.banned
        reason = outcome.reason
        replacement_arrival_pos: float | None = None
        if outcome.access is not None:
            replacement_arrival_pos = outcome.access.position_m
            relocations.append(DestinationRelocationRecord(
                vehicle_id=vehicle_id,
                original_origin=edges[0],
                original_destination=destination,
                replacement_destination=outcome.access.edge_id,
                original_depart_s=depart_s,
                original_arrival_pos=original_arrival_pos,
                replacement_arrival_pos=replacement_arrival_pos,
                access_distance_m=outcome.access.distance_m,
                applicable_closed_edges=tuple(sorted(banned))))

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
        # fixed point's own termination condition implicitly. Re-evaluating
        # the predicate on the FINAL route is a real second check: the fixed
        # point stops when `applicable_on(route)` is contained in `banned`,
        # while the route by construction avoids `banned` entirely, so a
        # converged plan must leave NOTHING here. Anything residual means the
        # plan did not converge, and a vehicle must never reach SUMO on a
        # route this function itself would flag as affected.
        residual = resolver.applicable_on(new_edges, depart_s, closures)
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
        if replacement_arrival_pos is not None:
            new_open_tag = _set_xml_attribute(
                open_tag, "arrivalPos", f"{replacement_arrival_pos:.2f}")
            new_fragment = new_fragment.replace(open_tag, new_open_tag, 1)
        out_parts.append(new_fragment)
        n_rerouted += 1
        rerouted_vehicle_ids.append(vehicle_id)

    out_parts.append(text[last_end:])
    Path(out_path).write_text("".join(out_parts), encoding="utf-8")

    return ClosureRoutingResult(
        unaffected=n_unaffected, rerouted=n_rerouted, denied=len(records),
        access_impact=tuple(records),
        destination_relocations=tuple(relocations),
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
    "access_impact_sha256", "access_impact_semantic_sha256",
    "transformed_route_sha256",
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
    access_impact_semantic_sha256: str
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
        _require_hex64(
            self.access_impact_semantic_sha256,
            "access_impact_semantic_sha256")
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
            "access_impact_semantic_sha256": (
                self.access_impact_semantic_sha256),
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


def access_impact_semantic_sha256(raw: Mapping[str, Any]) -> str:
    """Digest all access-impact facts after removing only the arm label."""
    if not isinstance(raw, Mapping):
        raise ClosureRoutingError("access-impact semantic payload is not an object")
    normalized = dict(raw)
    identity = normalized.get("identity")
    if not isinstance(identity, Mapping):
        raise ClosureRoutingError("access-impact semantic payload lacks identity")
    normalized_identity = dict(identity)
    normalized_identity.pop("execution_arm", None)
    normalized["identity"] = normalized_identity
    canonical = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_access_impact_report(
    raw: Mapping[str, Any], provenance: RoutingProvenance,
    *, transformed_route_path: Path, network_path: Path | None = None,
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

    raw_relocations = report["destination_relocations"]
    if not isinstance(raw_relocations, list):
        raise ClosureRoutingError(
            "access-impact destination relocations must be a list")
    relocation_ids: list[str] = []
    geometry_validator = None
    if raw_relocations:
        if network_path is None:
            raise ClosureRoutingError(
                "destination relocation validation requires the SUMO network")
        resolved_network_path = Path(network_path).resolve()
        if (report["network_sha256"] is None
                or sha256_file(resolved_network_path)
                != report["network_sha256"]):
            raise ClosureRoutingError(
                "destination relocation network does not match report")
        geometry_validator = disruption_analysis.DestinationAccessResolver(
            resolved_network_path, permitted_edges=())
    for relocation in raw_relocations:
        if (not isinstance(relocation, Mapping)
                or set(relocation) != _DESTINATION_RELOCATION_RECORD_FIELDS):
            raise ClosureRoutingError(
                "destination relocation fields do not match the current schema")
        vehicle_id = relocation["vehicle_id"]
        origin = relocation["original_origin"]
        original = relocation["original_destination"]
        replacement = relocation["replacement_destination"]
        depart_s = relocation["original_depart_s"]
        original_pos = relocation["original_arrival_pos"]
        replacement_pos = relocation["replacement_arrival_pos"]
        distance_m = relocation["access_distance_m"]
        applicable = relocation["applicable_closed_edges"]
        try:
            numeric_original_pos = (
                None if original_pos in (None, "", "max")
                else float(original_pos))
        except (TypeError, ValueError):
            numeric_original_pos = float("nan")
        if (not isinstance(vehicle_id, str) or not vehicle_id
                or not isinstance(origin, str) or not origin
                or not isinstance(original, str) or original not in close_edges
                or not isinstance(replacement, str) or not replacement
                or replacement in close_edges
                or isinstance(depart_s, bool)
                or not isinstance(depart_s, (int, float))
                or not math.isfinite(depart_s) or depart_s < 0
                or (numeric_original_pos is not None
                    and not math.isfinite(numeric_original_pos))
                or isinstance(replacement_pos, bool)
                or not isinstance(replacement_pos, (int, float))
                or not math.isfinite(replacement_pos) or replacement_pos < 0
                or isinstance(distance_m, bool)
                or not isinstance(distance_m, (int, float))
                or not math.isfinite(distance_m) or distance_m < 0
                or distance_m > disruption_analysis.DESTINATION_ACCESS_RADIUS_M
                or not isinstance(applicable, list)
                or original not in applicable
                or any(not isinstance(edge, str) or edge not in close_edges
                       for edge in applicable)
                or applicable != sorted(set(applicable))):
            raise ClosureRoutingError("destination relocation record is invalid")
        replacement_length = geometry_validator.edge_length_m(replacement)
        recomputed_distance = geometry_validator.access_distance_m(
            original, original_pos, replacement, replacement_pos)
        if (replacement_length is None
                or replacement_pos > replacement_length + 1e-9):
            raise ClosureRoutingError(
                "destination replacement arrivalPos is outside its edge")
        if (recomputed_distance is None
                or abs(recomputed_distance - float(distance_m)) > 0.011):
            raise ClosureRoutingError(
                "destination relocation access distance disagrees with network")
        relocation_ids.append(vehicle_id)
    if len(relocation_ids) != len(set(relocation_ids)):
        raise ClosureRoutingError(
            "destination relocation vehicle ids are duplicated")

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
    if not set(relocation_ids) <= set(rerouted_ids):
        raise ClosureRoutingError(
            "destination relocation is not included among rerouted vehicles")
    if (summary["denied"] != len(raw_records)
            or summary["denied"] != provenance.denied_count):
        raise ClosureRoutingError(
            "access-impact denied counts disagree with routing provenance")
    if (summary["rerouted"] != len(rerouted_ids)
            or summary["rerouted"] != provenance.rerouted_around_closure):
        raise ClosureRoutingError(
            "access-impact rerouted counts disagree with routing provenance")
    if summary["destination_relocated"] != len(raw_relocations):
        raise ClosureRoutingError(
            "destination relocation count disagrees with report records")

    transformed_text = _read_route_text(Path(transformed_route_path))
    transformed_ids: list[str] = []
    transformed_endpoints: dict[str, tuple[str, str | None]] = {}
    for match in _VEHICLE_FRAGMENT_RE.finditer(transformed_text):
        open_tag = _OPEN_TAG_RE.match(match.group())
        vehicle_match = (
            _ID_ATTR_RE.search(open_tag.group()) if open_tag is not None else None)
        if vehicle_match is None or not vehicle_match.group(1):
            raise ClosureRoutingError(
                "transformed route contains a vehicle without an id")
        vehicle_id = vehicle_match.group(1)
        transformed_ids.append(vehicle_id)
        route_match = _ROUTE_TAG_RE.search(match.group())
        edges_match = (
            _EDGES_ATTR_RE.search(route_match.group())
            if route_match is not None else None)
        if edges_match is None or not edges_match.group(1).split():
            raise ClosureRoutingError(
                "transformed route contains a vehicle without route edges")
        arrival_match = _ARRIVAL_POS_ATTR_RE.search(open_tag.group())
        transformed_endpoints[vehicle_id] = (
            edges_match.group(1).split()[-1],
            arrival_match.group(1) if arrival_match is not None else None,
        )
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
    for relocation in raw_relocations:
        endpoint = transformed_endpoints.get(relocation["vehicle_id"])
        expected_pos = f'{float(relocation["replacement_arrival_pos"]):.2f}'
        if endpoint != (relocation["replacement_destination"], expected_pos):
            raise ClosureRoutingError(
                "destination relocation disagrees with transformed endpoint")

    # Deliberately LAST. The checks above each name the exact fact that is
    # wrong, which is what a person debugging real evidence needs; running the
    # whole-report digest first collapsed every distinct tampering into one
    # generic "digest" message. This stays a hard gate -- the function cannot
    # return without it -- and it is the only check that covers report facts
    # nothing above cross-references, such as `source_route_sha256` and
    # `network_sha256`, which are otherwise validated for shape alone.
    if access_impact_semantic_sha256(report) != (
            provenance.access_impact_semantic_sha256):
        raise ClosureRoutingError(
            "access-impact semantic digest does not match routing provenance")
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
        # 1->2 added rerouted ids; 2->3 made monthly identity complete;
        # 3->4 records successful nearby destination relocations.
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
            "destination_relocated": len(result.destination_relocations),
            "denied": result.denied,
        },
        "access_impact": [record.to_dict() for record in result.access_impact],
        "destination_relocations": [
            record.to_dict() for record in result.destination_relocations
        ],
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
    destination_access: (
        disruption_analysis.DestinationAccessResolver | None) = None,
) -> ClosureRoutingResult:
    """`rewrite_route_file` plus, when requested, the access-impact evidence
    file alongside it. The single entry point production callers use."""
    result = rewrite_route_file(
        route_path, close_edges, out_path, adjacency,
        edge_travel_s=edge_travel_s, closures=closures,
        destination_access=destination_access)
    if access_impact_path is not None:
        write_access_impact_report(
            access_impact_path, result=result, close_edges=close_edges,
            closures=closures, source_route_path=route_path,
            out_route_path=out_path, network_path=network_path,
            identity=identity)
    return result
