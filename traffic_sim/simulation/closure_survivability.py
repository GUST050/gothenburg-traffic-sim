"""Does an edge survive its own closure?

Stage 4 of `docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md`, which asks for a
third held-out selection condition beside the 400 m band and
``MIN_WINDOW_SUPPORT``: **the edge must survive its own closure**, checked
BEFORE any SUMO run.

WHY IT IS NEEDED.  v9 failed one gate check of seven — ``ranking_case_fraction``
0.4 against a 0.5 floor — because three of its five cases produced zero
rankable schedules.  Selection had proved the edges carried demand; nothing had
asked whether closing them leaves anyone stranded.  A campaign that discovers
that only after the SUMO runs has spent the runs to learn something the tracked
topology already knew.

WHAT "SURVIVES" MEANS, PRECISELY.  Two facts, deliberately kept apart:

  * **structural severance** — with the edge removed, a vehicle that was
    driving it can no longer reach its destination BY CAR from the point where
    the closure stops it.  This is a property of the network, it disqualifies,
    and it is what this module gates on.
  * **denied departure** — the vehicle's own first edge is the closed one, so
    it never starts.  This is access the closure genuinely removes.  EVERY
    street with departures on it has some, so gating on it would refuse to
    close any real street — exactly the mistake C1 fixed on 2026-08-06
    (`metrics.access_impact_reasons`) after it had made every edge trips depart
    from permanently uncloseable.  It is REPORTED here and never gates.

Conflating the two is what makes a survivability rule useless, so
``closure_disruption`` was split to report each half separately and this module
consumes the precise one.

REACHABILITY IS MEASURED FROM THE APPROACH, NOT THE ORIGIN.  For each vehicle
the question is asked from the edge immediately BEFORE the closed edge on that
vehicle's own route — the exact position SUMO's live rerouter re-plans from,
and the same correction the 2026-07-09 review forced on
`run_scenario.truncate_stranded_vehicles` after it used the origin as a proxy.

OUTCOME-BLIND BY CONSTRUCTION.  The only inputs are the tracked/derived network
topology and the canonical archived demand routes a campaign is already bound
to.  No campaign outcome, report or run root is read, and no process is run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from traffic_sim.simulation.heldout_selection import (
    ROUTE_FILES, VARIANT_OF, stream_closure_exposure)

#: Version tag written into every artifact produced from this rule.
RULE_VERSION = "survives_own_closure_v1"

#: An edge with no exposure at all in a variant, so the shape is uniform.
_EMPTY_EXPOSURE = {"vehicles": 0, "denied_departures": 0, "resume_pairs": {}}


@dataclass(frozen=True)
class SurvivabilityReport:
    """One edge's verdict, with the evidence that produced it."""

    edge_id: str
    survives: bool
    severed_destination: int
    denied_departures: int
    vehicles_crossing: int
    successors_cut_off: tuple[str, ...]
    per_variant: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_version": RULE_VERSION,
            "edge_id": self.edge_id,
            "survives_own_closure": self.survives,
            # The gating quantity.
            "vehicles_severed_destination": self.severed_destination,
            # Reported, never gating — see the module docstring.
            "vehicles_denied_departure": self.denied_departures,
            "vehicles_crossing": self.vehicles_crossing,
            "successors_cut_off": list(self.successors_cut_off),
            "per_variant": {name: dict(values)
                            for name, values in sorted(self.per_variant.items())},
        }


def without_edges(adjacency: Mapping[str, Sequence[str]],
                  banned: set) -> dict[str, list[str]]:
    """`adjacency` with `banned` removed from both ends of every connection.

    Reproduces `run_scenario.build_edge_graph(banned)` exactly, INCLUDING its
    omission rule: a source whose every target is banned disappears rather than
    keeping an empty list, because that builder only creates the key inside the
    per-target test. Both are equivalent to `reachable`, which reads through
    `adj.get(...)` — but "equivalent if you look closely" is how two graphs
    quietly diverge, so the rule is reproduced rather than relied upon.

    Deriving beats rebuilding: `build_edge_graph` re-reads and re-hashes
    net.net.xml on every call, so a sixty-candidate pool would hash a
    multi-megabyte network sixty times to remove one edge each time.
    """
    out: dict[str, list[str]] = {}
    for source, targets in adjacency.items():
        if source in banned:
            continue
        kept = [target for target in targets if target not in banned]
        if kept:
            out[source] = kept
    return out


def successors_cut_off(edge_id: str,
                       adjacency_with: Mapping[str, Sequence[str]],
                       adjacency_without: Mapping[str, Sequence[str]]
                       ) -> tuple[str, ...]:
    """Edges reachable ONLY through `edge_id` in one hop — pure topology.

    This is the Skånegatan/Engelbrektsgatan shape recorded in `CLAUDE.md`:
    node 3575001205 had exactly ONE incoming connection in the whole network,
    so closing that one edge structurally cut off everything downstream. Such a
    successor is severed regardless of what today's demand happens to use, and
    a demand-only check would miss it on any quarter where nothing drove there.

    Deliberately one hop and not a component analysis: a successor that keeps
    another incoming connection is reachable by definition, and one that keeps
    none is unreachable by definition. Anything beyond that is already covered
    by the per-vehicle destination check.
    """
    downstream = set(adjacency_with.get(edge_id, ()))
    if not downstream:
        return ()
    still_reachable = {target
                       for source, targets in adjacency_without.items()
                       if source != edge_id
                       for target in targets}
    return tuple(sorted(downstream - still_reachable))


def evaluate_edge(edge_id: str, *,
                  exposure_by_variant: Mapping[str, Mapping],
                  adjacency_with: Mapping[str, Sequence[str]],
                  reachable: Callable[..., bool]) -> SurvivabilityReport:
    """Decide one edge from already-streamed exposure and the unclosed graph.

    `reachable` is injected rather than imported so the rule is testable on a
    hand-built graph and so the caller controls which network the probe runs
    against — but production supplies `run_scenario.reachable` over
    `run_scenario.build_edge_graph`'s output, i.e. the SAME connection graph the
    closure filter and SUMO's own router use. A second, independently written
    reachability implementation would be free to disagree with the simulation,
    which is the one thing this check may not do.

    WORST VARIANT WINS. q10/q50/q90 are three plausible directional assignments
    of the same measured counts; an edge that severs someone under any of them
    has not survived. Same conservative reduction as
    `closure_disruption_across_variants`.
    """
    if not exposure_by_variant:
        raise ValueError(f"no demand variant was supplied for {edge_id}")
    closed = {edge_id}
    adjacency_without = without_edges(adjacency_with, closed)
    cut_off = successors_cut_off(edge_id, adjacency_with, adjacency_without)

    # One reachability answer per DISTINCT approach/destination pair, shared
    # across variants: the same pair cannot have two answers on one network,
    # and the pairs repeat heavily across thousands of vehicles.
    verdicts: dict[tuple[str, str], bool] = {}
    per_variant: dict[str, dict[str, int]] = {}
    for variant, exposure in exposure_by_variant.items():
        severed = 0
        for pair, count in exposure["resume_pairs"].items():
            if pair not in verdicts:
                verdicts[pair] = reachable(adjacency_without, pair[0], pair[1],
                                           closed)
            if not verdicts[pair]:
                severed += count
        per_variant[variant] = {
            "vehicles": exposure["vehicles"],
            "denied_departures": exposure["denied_departures"],
            "severed_destination": severed,
        }

    severed_total = max(v["severed_destination"] for v in per_variant.values())
    return SurvivabilityReport(
        edge_id=edge_id,
        # A cut-off successor severs access even where no vehicle drove there
        # in this particular demand, so it disqualifies on its own.
        survives=severed_total == 0 and not cut_off,
        severed_destination=severed_total,
        denied_departures=max(v["denied_departures"] for v in per_variant.values()),
        vehicles_crossing=max(v["vehicles"] for v in per_variant.values()),
        successors_cut_off=cut_off,
        per_variant=per_variant,
    )


def evaluate_pool(edge_ids: Sequence[str], *, archive_path: Path,
                  build_adjacency: Callable[[set], Mapping[str, Sequence[str]]],
                  reachable: Callable[..., bool],
                  route_files: Sequence[str] = ROUTE_FILES
                  ) -> dict[str, SurvivabilityReport]:
    """Evaluate a whole candidate pool against one archived demand build.

    Each route file is streamed ONCE for every candidate at the same time — a
    per-edge pass over a 16 000-vehicle route set, times three variants, times
    a pool of sixty, is work for nothing. The unclosed adjacency is likewise
    built once and each candidate's closed graph derived from it, for the same
    reason: `build_adjacency` re-hashes the whole SUMO network on every call.
    """
    archive_path = Path(archive_path)
    paths = [archive_path / name for name in route_files]
    missing = sorted(p.name for p in paths if not p.is_file())
    if missing:
        raise FileNotFoundError(
            f"canonical archive {archive_path} is missing {missing}")

    wanted = list(dict.fromkeys(edge_ids))
    streamed: dict[str, dict[str, Mapping]] = {edge: {} for edge in wanted}
    for path in paths:
        variant = VARIANT_OF.get(path.name, path.name)
        found = stream_closure_exposure(path, set(wanted))
        for edge in wanted:
            streamed[edge][variant] = found.get(edge, _EMPTY_EXPOSURE)

    adjacency_with = build_adjacency(set())
    return {edge: evaluate_edge(edge,
                                exposure_by_variant=streamed[edge],
                                adjacency_with=adjacency_with,
                                reachable=reachable)
            for edge in wanted}
