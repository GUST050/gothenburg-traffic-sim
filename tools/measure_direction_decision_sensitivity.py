"""Fas 0B — does the direction axis change a closure decision? (Gate S)

The question this answers is narrow on purpose. Before any scenario, monthly,
warm-state, API or UI work is justified, the plan requires evidence that
plausible direction variation changes something a user would act on. If it
does not, the whole branch closes and 50/50 plus sensor 107's local anchor is
a complete, successful outcome.

Design, all of it frozen before execution by the preregistration:

* The existing q10/q50/q90 route artifacts are used as NAMED STRESS CASES.
  They are not probability statements — their nominal coverage has never been
  validated — so this measures a spread, not a distribution.
* The SAME seed list runs for every stress case, and the same
  ``(stress case, seed)`` pair runs the baseline and the candidate. Each pair
  is bound through a per-run ``ScenarioSpec`` with a ONE-SEED ``seed_set`` and
  an explicit ``demand_variant_mapping``, which is the existing contract's own
  way of naming (demand case, seed) orthogonally. Nothing about
  ``ScenarioSpec``, monthly, warm-state, the API or the UI is changed.
* The decision is read from the REAL closure policy — the same
  ``closure_ranking.worst_variant_cost``/``rank_closures`` the product uses —
  so Gate S answers the plan's question ("does the viable set, the ranking or
  the winner change?") rather than a proxy of it.

REPAIRED 2026-08-14 after review. The first cut of this tool was
non-functional as evidence in four separate ways, all of which would have
produced a confident but empty answer:

1. ``_run_one`` built its command without the route file and without the
   seed, so every q case and every seed ran the SAME simulation. It compared
   a thing with itself. The ``DIRSPLIT_SENSITIVITY_SEED`` environment
   variable it set is read by nothing.
2. It read ``disruption["total_time_loss_s"]``, a field the product does not
   produce. Every observation would have been ``None`` and Gate S would have
   returned INCONCLUSIVE for a reason that had nothing to do with direction.
3. The registered 06:00-10:00 window was recorded but never applied; the
   closure ran for the whole demand window.
4. The reducer checked only process failures and the mean of one objective,
   not the preregistered decision fields (SUMO health, closure integrity,
   no-detour, viable set, ranking, winner).

One structural fact drives the repaired materiality rule, and it is recorded
here because it is easy to get wrong: the deployed ranking key comes from
``run_scenario.closure_disruption``, which reads the calibrated route file and
free-flow costs. It is DEMAND-SIDE and seed-independent BY CONSTRUCTION. So
the within-case seed range on that key is structurally zero, and a
"between-case spread beats seed noise" ratio on it would be a tautology. The
tool therefore:

* VERIFIES the invariant per case rather than assuming it (a case whose
  ranking key moves across seeds is a finding, not noise to average);
* verifies the executed seed and resolved variant from the scenario and
  seed-health records. It does not demand that an arbitrary outcome such as
  inserted-vehicle count differ: a valid seed can legitimately produce the
  same count in a fixed-route run.

This tool deliberately does not touch `ScenarioSpec`, the monthly path, the
warm-state cache, the API or the UI. It calls the existing runner and reduces
its output with the existing policy. Its artifacts carry
``release_evidence: false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def content_digest(payload: Any) -> str:
    """Stable SHA-256 over a JSON-serialisable payload.

    Local on purpose: this tool must stay runnable from a checkout that has
    nothing but the existing pipeline, so it does not reach for a shared
    helper that a later, conditional phase might introduce.
    """
    if isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SELECTION_RULE = "dirsplit_sensitivity_selection_v1"
PROTOCOL = "dirsplit_direction_decision_sensitivity_v3"

REGISTRATION_PATH = Path(
    "validation/dirsplit_direction_sensitivity_registration_v3.json")
OUTCOME_PATH = Path(
    "validation/dirsplit_direction_sensitivity_outcome_v3.json")

#: Route artifacts written by build_sumo_demand, in a fixed order, each with
#: the ScenarioSpec demand-variant name that selects it. The name is what
#: binds a stress case to a route file inside run_scenario, so the two can
#: never drift apart the way a bare filename could.
STRESS_CASES: tuple[tuple[str, str, str], ...] = (
    ("q50", "calibrated.rou.xml", "q50"),
    ("q10", "calibrated_v1.rou.xml", "q10"),
    ("q90", "calibrated_v2.rou.xml", "q90"),
)

#: The fields the deployed closure policy ranks on. Read straight from the
#: product's own ``disruption`` record — see closure_ranking.REQUIRED_FIELDS.
POLICY_FIELDS = ("added_vehicle_hours", "added_metres_total",
                 "vehicles_affected", "vehicles_no_detour")


# ──────────────────────────────────────────────────────────────────────────
# frozen materiality thresholds
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MaterialityThresholds:
    """What counts as a decision difference. Frozen before execution.

    ``relative_objective`` is the load-bearing one for the deployed ranking
    key, because that key is seed-deterministic (see the module docstring):
    there is no simulator noise to clear, so the question is whether the
    between-case difference is large relative to the decision itself.

    Seed execution is verified from runner identity records, not inferred
    from an outcome threshold.
    """

    relative_objective: float = 0.10
    require_identical_viable_set: bool = True
    require_identical_ranking: bool = True
    require_identical_winner: bool = True
    require_seed_execution_identity: bool = True
    require_clean_closure_integrity: bool = True
    require_healthy_seeds: bool = True
    require_direction_isolating_stress_cases: bool = True
    #: How far a direction pair's per-slot shares may sum from 1.0 before the
    #: stress case is changing VOLUME as well as direction.
    pair_sum_tolerance: float = 1e-3


#: What a direction stress case must be for Gate S to mean anything: the two
#: directed edges of a station must still sum to the measured two-way total,
#: so the case moves traffic BETWEEN carriageways without changing how much
#: there is. See ``measure_pair_sum_isolation``.
PAIR_SUM_REQUIREMENT = (
    "each station's two directed shares must sum to 1.0 in every slot, in "
    "every stress case; otherwise the case changes total demand as well as "
    "its direction and Gate S cannot attribute a difference to direction"
)

ROUTE_ARTIFACT_KEYS = {
    "q50": "calibrated_q50",
    "q10": "calibrated_v1",
    "q90": "calibrated_v2",
}


@dataclass(frozen=True)
class Registration:
    protocol: str
    selection_rule: str
    date: str
    window_begin: str
    window_end: str
    closure_begin: str
    closure_end: str
    seeds: tuple[int, ...]
    stress_cases: tuple[str, ...]
    candidate_edges: tuple[str, ...]
    candidate_selection: Mapping[str, Any]
    timeout_s: int
    objective: str
    comparison_fields: tuple[str, ...]
    thresholds: MaterialityThresholds
    source_digests: Mapping[str, str]
    demand_build_id: str = ""
    network_build_id: str = ""
    stress_case_isolation: Mapping[str, Any] = field(default_factory=dict)
    release_evidence: bool = False
    note: str = ""

    @property
    def stress_cases_isolate_direction(self) -> bool:
        """True only when every stress case preserves the pair sum.

        Absent evidence counts as NOT isolating: a registration that never
        measured this cannot vouch for it.
        """
        return bool(self.stress_case_isolation.get("isolates_direction"))

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["seeds"] = list(self.seeds)
        payload["stress_cases"] = list(self.stress_cases)
        payload["candidate_edges"] = list(self.candidate_edges)
        payload["comparison_fields"] = list(self.comparison_fields)
        payload["thresholds"] = asdict(self.thresholds)
        return payload

    @property
    def content_key(self) -> str:
        return content_digest(self.to_json())


def measure_pair_sum_isolation(
        sumo_dir: Path,
        tolerance: float = MaterialityThresholds().pair_sum_tolerance,
) -> dict[str, Any]:
    """Do the q10/q50/q90 splits change direction only, or volume too?

    Gate S asks whether the DIRECTION axis changes a closure decision. That
    question is only answerable if the stress cases differ in direction and
    in nothing else. They do not today: ``dirsplit/predict.py`` writes
    ``edge_shares_q10`` as ``(e0 -> s10, e1 -> 1 - s90)`` and
    ``edge_shares_q90`` as ``(e0 -> s90, e1 -> 1 - s10)``, so each outer
    pair sums to ``1 -/+ (s90 - s10)`` rather than 1. Measured on the
    tracked model that is about -/+0.12, which means the q10 arm hands the
    calibrator Level-1 targets summing to well under the measured two-way
    total and the q90 arm well over it.

    A Gate S run on those artifacts could react to TOTAL VOLUME rather than
    direction, and there is no way to tell the two apart after the fact. So
    this is measured before the run, recorded in the frozen registration,
    and enforced by the gate rather than left as a footnote.
    """
    import math
    import xml.etree.ElementTree as ET

    path = sumo_dir / "direction_split.json"
    if not path.exists():
        return {"isolates_direction": False,
                "reason": f"{path} is missing, so isolation is unverified",
                "requirement": PAIR_SUM_REQUIREMENT}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"isolates_direction": False,
                "reason": f"{path} is unreadable: {error}",
                "requirement": PAIR_SUM_REQUIREMENT}
    if not isinstance(data, dict) or not data:
        return {"isolates_direction": False,
                "reason": f"{path} carries no sensor records",
                "requirement": PAIR_SUM_REQUIREMENT}

    meta_path = sumo_dir / "demand_meta.json"
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"isolates_direction": False,
                "reason": f"{meta_path} is unreadable: {error}",
                "requirement": PAIR_SUM_REQUIREMENT}
    fingerprint = meta.get("build_fingerprint") or {}
    recorded_artifacts = fingerprint.get("artifacts") or {}

    def sha256_file(file_path: Path) -> str:
        digest = hashlib.sha256()
        with open(file_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    lineage_errors: list[str] = []
    live_digests: dict[str, str] = {}
    lineage_paths = {"direction_split": path}
    lineage_paths.update({
        ROUTE_ARTIFACT_KEYS[case]: sumo_dir / filename
        for case, filename, _variant in STRESS_CASES
    })
    for artifact, artifact_path in lineage_paths.items():
        recorded = (recorded_artifacts.get(artifact) or {}).get("sha256")
        if not artifact_path.is_file():
            lineage_errors.append(f"{artifact}: missing {artifact_path}")
            continue
        live = sha256_file(artifact_path)
        live_digests[artifact] = live
        if not recorded:
            lineage_errors.append(
                f"{artifact}: demand_meta build fingerprint has no digest")
        elif live != recorded:
            lineage_errors.append(
                f"{artifact}: live digest does not match demand_meta build")

    n_intervals = meta.get("n_intervals")
    if isinstance(n_intervals, bool) or not isinstance(n_intervals, int) \
            or n_intervals <= 0:
        lineage_errors.append("demand_meta.n_intervals is not a positive integer")
        n_intervals = 0

    keys = {"q50": "edge_shares", "q10": "edge_shares_q10",
            "q90": "edge_shares_q90"}
    per_case: dict[str, Any] = {}
    worst_overall = 0.0
    for case, key in keys.items():
        worst = 0.0
        offenders: list[str] = []
        for sensor, record in sorted(data.items()):
            if not isinstance(record, dict):
                offenders.append(f"{sensor}: record is not an object")
                continue
            shares = record.get(key)
            if not isinstance(shares, dict) or not shares:
                offenders.append(f"{sensor}: no {key}")
                continue
            series = list(shares.values())
            if len(series) != 2:
                offenders.append(
                    f"{sensor}: {key} must contain exactly two directed edges")
                continue
            if not all(isinstance(values, list) for values in series):
                offenders.append(f"{sensor}: {key} series must be arrays")
                continue
            lengths = {len(values) for values in series}
            if lengths != {96}:
                offenders.append(
                    f"{sensor}: {key} needs two complete 96-slot series, got "
                    f"{sorted(lengths)}")
                continue
            pairs: list[tuple[float, float]] = []
            malformed = False
            for slot, (left, right) in enumerate(zip(series[0], series[1])):
                if (isinstance(left, bool) or isinstance(right, bool)
                        or not isinstance(left, (int, float))
                        or not isinstance(right, (int, float))
                        or not math.isfinite(float(left))
                        or not math.isfinite(float(right))
                        or not 0.0 <= float(left) <= 1.0
                        or not 0.0 <= float(right) <= 1.0):
                    offenders.append(
                        f"{sensor}: {key} slot {slot} is not a finite share")
                    malformed = True
                    break
                pairs.append((float(left), float(right)))
            if malformed:
                continue
            deviation = max(abs(a + b - 1.0) for a, b in pairs)
            worst = max(worst, deviation)
            if deviation > tolerance:
                offenders.append(f"{sensor}: max |sum-1| = {deviation:.4f}")
        worst_overall = max(worst_overall, worst)
        per_case[case] = {
            "max_abs_pair_sum_deviation": round(worst, 6),
            "within_tolerance": not offenders and worst <= tolerance,
            "offenders": offenders,
        }

    # The split is only an input. Gate S simulates the calibrated route files,
    # so certify the live files too: all must belong to this exact demand build
    # and carry the same 15-minute departure population. Otherwise a stale
    # route file or calibration drift can reintroduce total-volume differences
    # even when the split itself sums perfectly.
    departure_profiles: dict[str, list[int]] = {}
    route_errors: list[str] = []
    if n_intervals:
        for case, filename, _variant in STRESS_CASES:
            route_path = sumo_dir / filename
            profile = [0] * n_intervals
            if not route_path.is_file():
                route_errors.append(f"{case}: missing {route_path}")
                continue
            try:
                for _event, element in ET.iterparse(route_path, events=("end",)):
                    if element.tag in {"flow", "trip"}:
                        route_errors.append(
                            f"{case}: unsupported <{element.tag}> demand; exact "
                            "vehicle departures cannot be certified")
                    elif element.tag == "vehicle":
                        try:
                            depart = float(element.attrib["depart"])
                        except (KeyError, TypeError, ValueError):
                            route_errors.append(
                                f"{case}: vehicle has no finite numeric depart")
                        else:
                            if not math.isfinite(depart) or depart < 0:
                                route_errors.append(
                                    f"{case}: vehicle has invalid depart {depart}")
                            else:
                                slot = int(depart // 900)
                                if not 0 <= slot < n_intervals:
                                    route_errors.append(
                                        f"{case}: departure {depart} lies outside "
                                        "the demand window")
                                else:
                                    profile[slot] += 1
                    element.clear()
            except ET.ParseError as error:
                route_errors.append(f"{case}: route XML is invalid: {error}")
            departure_profiles[case] = profile
    profiles_match = (len(departure_profiles) == len(STRESS_CASES)
                      and len({tuple(profile)
                               for profile in departure_profiles.values()}) == 1)
    if not profiles_match:
        route_errors.append(
            "q10/q50/q90 do not carry the same 15-minute departure profile")

    isolates = (not lineage_errors and not route_errors
                and all(entry["within_tolerance"]
                        for entry in per_case.values()))
    return {
        "isolates_direction": isolates,
        "tolerance": tolerance,
        "max_abs_pair_sum_deviation": round(worst_overall, 6),
        "by_case": per_case,
        "lineage": {
            "verified": not lineage_errors,
            "errors": lineage_errors,
            "live_digests": live_digests,
            "demand_build_id": meta.get("build_id"),
        },
        "route_departure_population": {
            "verified_equal": not route_errors and profiles_match,
            "errors": route_errors,
            "vehicles_by_case": {
                case: sum(profile)
                for case, profile in departure_profiles.items()
            },
            "profiles_by_case": departure_profiles,
        },
        "requirement": PAIR_SUM_REQUIREMENT,
        "reason": (None if isolates else
                   "the stress artifacts failed strict split, build-lineage, "
                   "or equal-departure validation; direction is not isolated "
                   f"(worst |sum-1| = {worst_overall:.4f})"),
        "source": str(path),
    }


# ──────────────────────────────────────────────────────────────────────────
# outcome-blind candidate selection
# ──────────────────────────────────────────────────────────────────────────
def route_edge_exposure(route_path: Path) -> dict[str, int]:
    """Vehicles whose route traverses each edge, from a SUMO route file.

    Demand exposure is an INPUT-side signal: it is computable before any
    simulation runs, so selecting on it cannot see an outcome. The same
    pattern is already precedented in this repository's `demand_exposure_v1`
    held-out selection rule.
    """
    import xml.etree.ElementTree as ET

    counts: dict[str, int] = {}
    for _event, element in ET.iterparse(route_path, events=("end",)):
        if element.tag != "route":
            continue
        for edge in (element.get("edges") or "").split():
            counts[edge] = counts.get(edge, 0) + 1
        element.clear()
    return counts


def load_network(net_path: Path):
    """Read the SUMO network, or refuse to select without it.

    FAIL-CLOSED. The previous version returned ``True`` from the topology
    filter when ``sumolib`` was absent, so a checkout without SUMO silently
    selected candidates that sever their own downstream pocket — a topology
    failure dressed up as a direction-sensitivity observation, inside a
    selection whose whole purpose is to be frozen and defensible. An
    unverifiable filter is not a passed filter.
    """
    # eclipse-sumo installs ``sumolib`` below <SUMO_HOME>/tools rather than as
    # a top-level site package.  Use the same deterministic installation
    # resolver as the simulator instead of relying on an ambient PYTHONPATH.
    # The origin check matters: accepting a different globally installed
    # sumolib would validate topology with a SUMO nobody selected.
    try:
        import importlib

        from sumo_runtime import sumo_home

        tools = (sumo_home() / "tools").resolve()
        expected = (tools / "sumolib").resolve()
        if not expected.is_dir():
            raise RuntimeError(f"{expected} does not exist")
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        sumolib = importlib.import_module("sumolib")
        origin = Path(sumolib.__file__).resolve()
        origin.relative_to(expected)
    except Exception as error:                         # pragma: no cover
        raise RuntimeError(
            "sumolib is required from the active SUMO installation to verify "
            "that a candidate survives its own closure; refusing to freeze "
            "a selection whose topology filter could not run") from error
    if not net_path.exists():
        raise FileNotFoundError(f"network artifact missing: {net_path}")
    return sumolib.net.readNet(str(net_path))


def survives_own_closure(edge_id: str, net_path: Path, net=None) -> bool:
    """Can traffic still reach this edge's successors once it is closed?

    A candidate that severs its own downstream pocket is a topology failure
    rather than a direction-sensitivity observation, so it is filtered out
    before the experiment rather than becoming a confound inside it.

    ``net`` may be a preloaded network; the caller probes many edges against
    one network and re-reading a 15 MB file per edge is pure waste.
    """
    net = net if net is not None else load_network(net_path)
    if not net.hasEdge(edge_id):
        return False
    edge = net.getEdge(edge_id)
    successors = {e.getID() for e in edge.getOutgoing()}
    if not successors:
        return False
    frontier = [e.getID() for e in edge.getIncoming()]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current in seen or current == edge_id:
            continue
        seen.add(current)
        if current in successors:
            return True
        if not net.hasEdge(current):
            continue
        frontier.extend(e.getID() for e in net.getEdge(current).getOutgoing()
                        if e.getID() not in seen)
    return False


def select_candidates(sumo_dir: Path, sensor_edges: Iterable[str],
                      count: int) -> tuple[list[str], dict[str, Any]]:
    """Deterministic, outcome-blind candidate selection.

    Rule ``dirsplit_sensitivity_selection_v1``:
      1. exclude the measured sensor edges (closing a constraint edge is a
         different experiment);
      2. require strictly positive exposure in EVERY stress case, so no arm
         of the comparison is degenerate;
      3. require the edge to survive its own closure;
      4. rank by q50 exposure descending, tie-break by edge id ascending;
      5. take the first ``count``.
    """
    exposures: dict[str, dict[str, int]] = {}
    for name, filename, _variant in STRESS_CASES:
        path = sumo_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"stress case {name} route file is missing: {path}")
        exposures[name] = route_edge_exposure(path)

    excluded = set(sensor_edges)
    common = set(exposures["q50"])
    for name in exposures:
        common &= {edge for edge, n in exposures[name].items() if n > 0}
    pool = sorted(common - excluded,
                  key=lambda edge: (-exposures["q50"][edge], edge))

    net_path = sumo_dir / "net.net.xml"
    net = load_network(net_path)      # fail-closed: no network, no selection
    chosen: list[str] = []
    probed = 0
    for edge in pool:
        if len(chosen) >= count:
            break
        probed += 1
        if survives_own_closure(edge, net_path, net=net):
            chosen.append(edge)

    if len(chosen) < count:
        raise RuntimeError(
            f"only {len(chosen)} of {count} requested candidates survive their "
            f"own closure after probing {probed} of {len(pool)} pool edges; "
            "freezing a short selection would silently change the "
            "preregistered experiment")

    return chosen, {
        "rule": SELECTION_RULE,
        "pool_size": len(pool),
        "probed": probed,
        "topology_filter": "sumolib reachability, verified (never fail-open)",
        "excluded_sensor_edges": sorted(excluded),
        "exposure_q50": {edge: exposures["q50"][edge] for edge in chosen},
        "exposure_q10": {edge: exposures["q10"].get(edge, 0) for edge in chosen},
        "exposure_q90": {edge: exposures["q90"].get(edge, 0) for edge in chosen},
    }


# ──────────────────────────────────────────────────────────────────────────
# running the matrix
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Observation:
    """One (stress case, seed, candidate) run, with everything the gate reads.

    ``policy`` carries the deployed ranking fields verbatim, so the reducer
    can hand them straight to ``closure_ranking`` rather than re-deriving a
    private objective that the product would never use.
    """

    stress_case: str
    seed: int
    candidate: str
    policy: dict[str, float] | None
    closure_integrity: str | None
    seed_health_flags: tuple[str, ...]
    vehicles_inserted: int | None
    hard_failure: bool
    failure_reason: str | None
    runtime_s: float
    seed_identity_verified: bool = True
    executed_seed: int | None = None
    executed_variant: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["seed_health_flags"] = list(self.seed_health_flags)
        return payload


def demand_identity(sumo_dir: Path) -> tuple[str, str]:
    """(demand_build_id, network_build_id) exactly as run_scenario computes.

    Reusing run_scenario's own helpers rather than re-implementing them is
    what keeps a spec this tool writes acceptable to the runner: a private
    copy of the signature would drift the first time the demand contract
    changed.
    """
    import run_scenario as rs

    with open(sumo_dir / "demand_meta.json") as handle:
        meta = json.load(handle)
    network_id = rs.sha256_file(sumo_dir / "net.net.xml")
    if network_id is None:
        raise FileNotFoundError(f"network artifact missing: {sumo_dir}/net.net.xml")
    return str(meta.get("build_id") or rs.demand_signature(meta)), network_id


def demand_window(sumo_dir: Path) -> tuple[str, str]:
    """(start_time, end_time) of the loaded demand build, ISO, as the spec
    validator requires them: exactly epoch_sim and epoch_sim + duration."""
    import pandas as pd

    with open(sumo_dir / "demand_meta.json") as handle:
        meta = json.load(handle)
    start = pd.Timestamp(meta["epoch_sim"])
    end = start + pd.Timedelta(seconds=int(meta["n_intervals"]) * 900)
    return start.isoformat(), end.isoformat()


class RegistrationError(RuntimeError):
    """The frozen experiment cannot be built as specified."""


def closure_window(sumo_dir: Path, begin: str, end: str,
                   date: str | None = None) -> tuple[str, str]:
    """Resolve the registered HH:MM closure window onto the demand calendar.

    The registered window is a real constraint, not a label: a closure that
    silently ran for the whole demand day would measure a different scenario
    from the one that was frozen.

    FAIL-CLOSED on a window that does not fit. The previous version quietly
    substituted the entire demand window when the requested one fell outside
    the build — which is the one thing a preregistration exists to prevent:
    the experiment that ran would not be the experiment that was frozen, and
    nothing in the artifact would say so. Both a window outside the build
    and a window that only partly overlaps it now raise, so the operator
    fixes the registration or the demand build rather than discovering
    afterwards that a four-hour closure ran for twenty-four.
    """
    import pandas as pd

    start_iso, end_iso = demand_window(sumo_dir)
    start, finish = pd.Timestamp(start_iso), pd.Timestamp(end_iso)
    day = pd.Timestamp(date).normalize() if date else start.normalize()
    try:
        lo = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {begin}")
        hi = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {end}")
    except ValueError as error:
        raise RegistrationError(
            f"closure window {begin!r}-{end!r} is not a valid HH:MM range"
        ) from error
    if hi <= lo:
        raise RegistrationError(
            f"closure window {begin}-{end} on {day.date()} is empty or "
            "reversed")
    if lo < start or hi > finish:
        raise RegistrationError(
            f"the registered closure window {lo.isoformat()} - "
            f"{hi.isoformat()} does not fit inside the loaded demand window "
            f"{start.isoformat()} - {finish.isoformat()}. Refusing to "
            "substitute the whole demand window: that would silently run a "
            "different experiment from the one being frozen. Rebuild the "
            "demand for a window that contains the closure, or register a "
            "closure window this build actually covers.")
    return lo.isoformat(), hi.isoformat()


def validate_registered_date(sumo_dir: Path, date: str) -> str:
    """The registered date must be the date the demand build actually is.

    A registration that names 2025-09-16 while the loaded demand is a 2027
    forecast day describes an experiment nobody ran. The demand metadata is
    the authority; the CLI argument is a claim about it.
    """
    with open(sumo_dir / "demand_meta.json") as handle:
        meta = json.load(handle)
    actual = str(meta.get("date") or meta.get("start_date") or "")
    if not actual:
        raise RegistrationError(
            f"{sumo_dir}/demand_meta.json carries no date or start_date, so "
            "the registered date cannot be verified against it")
    if date != actual:
        raise RegistrationError(
            f"registered date {date!r} does not match the loaded demand build "
            f"({actual!r}). Pass --date {actual} or rebuild the demand.")
    return actual


def build_spec_payload(registration: Registration, *, case_variant: str,
                       seed: int, closures: Sequence[str],
                       scenario_id: str, start_time: str,
                       end_time: str) -> dict[str, Any]:
    """One ScenarioSpec binding exactly one demand case to exactly one seed.

    ``seed_set`` has a single member, so ``run_scenario``'s three-variant
    coverage requirement (which applies from three seeds up) does not fire —
    that requirement exists so a PUBLISHED release spans the direction
    interval, which is the opposite of what this diagnostic wants: here each
    arm must be a single, named case.
    """
    return {
        "scenario_id": scenario_id,
        "demand_build_id": registration.demand_build_id,
        "network_build_id": registration.network_build_id,
        "start_time": start_time,
        "end_time": end_time,
        "closures": [
            {"edge_id": edge, "start_time": registration.closure_begin,
             "end_time": registration.closure_end, "closure_type": "full"}
            for edge in closures
        ],
        "simulation_mode": "meso",
        "seed_set": [seed],
        "demand_variant_mapping": {str(seed): case_variant},
    }


def run_matrix(registration: Registration, sumo_dir: Path,
               out_dir: Path, *, reuse_existing: bool = False,
               replay_runtimes: Mapping[tuple[str, int, str], float] | None = None,
               ) -> list[Observation]:
    """Run every (stress case, seed, candidate) plus its matched baseline."""
    observations: list[Observation] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    start_time, end_time = demand_window(sumo_dir)

    for case_name, _filename, variant in STRESS_CASES:
        if case_name not in registration.stress_cases:
            continue
        for seed in registration.seeds:
            for candidate in registration.candidate_edges:
                tag = f"cand_{case_name}_{seed}_{candidate}"
                spec_payload = build_spec_payload(
                    registration, case_variant=variant, seed=seed,
                    closures=[candidate], scenario_id=tag,
                    start_time=start_time, end_time=end_time)
                result = _run_one(
                    spec_payload, registration, out_dir, tag,
                    reuse_existing=reuse_existing,
                    replay_runtime_s=(replay_runtimes or {}).get(
                        (case_name, seed, candidate)))
                observations.append(Observation(
                    stress_case=case_name, seed=seed, candidate=candidate,
                    policy=result["policy"],
                    closure_integrity=result["closure_integrity"],
                    seed_health_flags=tuple(result["seed_health_flags"]),
                    vehicles_inserted=result["vehicles_inserted"],
                    hard_failure=result["hard_failure"],
                    failure_reason=result["reason"],
                    runtime_s=result["runtime_s"],
                    seed_identity_verified=result["seed_identity_verified"],
                    executed_seed=result["executed_seed"],
                    executed_variant=result["executed_variant"],
                ))
    return observations


def _run_one(spec_payload: Mapping[str, Any], registration: Registration,
             out_dir: Path, tag: str, *, reuse_existing: bool = False,
             replay_runtime_s: float | None = None
             ) -> dict[str, Any]:
    """One SUMO run through the existing runner, with a fixed timeout.

    The (demand case, seed) pair travels in a ScenarioSpec file rather than
    in ad-hoc flags. That is what makes the arms genuinely different runs:
    ``run_scenario`` reads ``seed_set`` and ``demand_variant_mapping`` from
    the spec and resolves the variant to its route file itself.
    """
    run_dir = out_dir / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.json"
    if reuse_existing:
        try:
            stored_spec = json.loads(spec_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            return _failed(f"existing spec is unavailable: {error}", 0.0)
        if stored_spec != dict(spec_payload):
            return _failed(
                "existing spec does not match the frozen registration", 0.0)
    else:
        spec_path.write_text(json.dumps(spec_payload, indent=1, sort_keys=True))

    command = [
        sys.executable, str(ROOT / "run_scenario.py"),
        "--scenario-spec", str(spec_path),
        "--no-trajectories",
        "--out-dir", str(run_dir),
    ]

    started = time.time()
    if reuse_existing:
        runtime = float(replay_runtime_s or 0.0)
    else:
        try:
            completed = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True,
                timeout=registration.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return _failed(f"timeout after {registration.timeout_s}s",
                           time.time() - started)
        runtime = time.time() - started
        if completed.returncode != 0:
            return _failed(f"exit {completed.returncode}: "
                           f"{completed.stderr.strip()[-300:]}", runtime)

    scenario = run_dir / f"{tag}.json"
    if not scenario.exists():
        found = sorted(p for p in run_dir.glob("*.json")
                       if p.name not in {"spec.json", "index.json"})
        scenario = found[0] if found else None
    if scenario is None:
        return _failed("no scenario artifact produced", runtime)

    payload = json.loads(scenario.read_text())
    disruption = payload.get("disruption") or {}
    missing = [name for name in POLICY_FIELDS if name not in disruption]
    if missing:
        return _failed(
            f"scenario artifact carries no {', '.join(missing)} in disruption",
            runtime)

    health = [record for record in (payload.get("seed_health") or [])
              if isinstance(record, dict)]
    expected_seeds = [int(seed) for seed in spec_payload["seed_set"]]
    expected_mapping = {int(seed): str(variant) for seed, variant in
                        spec_payload["demand_variant_mapping"].items()}
    executed_spec = payload.get("scenario_spec") or {}
    scenario_seeds = executed_spec.get("seed_set")
    executed_mapping = {
        int(seed): str(variant) for seed, variant in
        (executed_spec.get("demand_variant_mapping") or {}).items()
    }
    observed_pairs = [
        (record.get("seed"), _semantic_health_variant(record.get("variant")))
                      for record in health]
    expected_pairs = [(seed, expected_mapping[seed]) for seed in expected_seeds]
    identity_verified = (scenario_seeds == expected_seeds
                         and executed_mapping == expected_mapping
                         and observed_pairs == expected_pairs)
    if not identity_verified:
        return _failed(
            "scenario artifact does not prove the requested seed/variant "
            f"identity: expected {expected_pairs}, scenario seeds "
            f"{scenario_seeds}, mapping {executed_mapping}, health "
            f"{observed_pairs}", runtime)
    inserted = None
    if health:
        values = [record.get("inserted") for record in health
                  if record.get("inserted") is not None]
        inserted = int(sum(values)) if values else None

    return {
        "policy": {name: float(disruption[name]) for name in POLICY_FIELDS},
        "closure_integrity": (payload.get("scenario") or {}).get(
            "closure_integrity"),
        "seed_health_flags": list(payload.get("seed_health_flags") or []),
        "vehicles_inserted": inserted,
        "seed_identity_verified": True,
        "executed_seed": expected_seeds[0],
        "executed_variant": expected_mapping[expected_seeds[0]],
        "hard_failure": False,
        "reason": None,
        "runtime_s": runtime,
    }


def _semantic_health_variant(route_label: Any) -> str | None:
    """Map the route SUMO actually loaded back to the semantic q arm.

    ``seed_health.variant`` is deliberately the private closure-route
    filename, not the ScenarioSpec label. The old Gate S reader compared it
    directly with ``q10``/``q50``/``q90`` and rejected every valid run. The
    private name retains the canonical source prefix, so require both that
    physical evidence and the independent semantic mapping in
    ``scenario_spec``.
    """
    if not isinstance(route_label, str):
        return None
    name = Path(route_label).name
    if name == "calibrated_v1.rou.xml" or name.startswith("calibrated_v1.rou_"):
        return "q10"
    if name == "calibrated_v2.rou.xml" or name.startswith("calibrated_v2.rou_"):
        return "q90"
    if name == "calibrated.rou.xml" or name.startswith("calibrated.rou_"):
        return "q50"
    return None


def _failed(reason: str, runtime: float) -> dict[str, Any]:
    return {"policy": None, "closure_integrity": None,
            "seed_health_flags": [], "vehicles_inserted": None,
            "seed_identity_verified": False, "executed_seed": None,
            "executed_variant": None,
            "hard_failure": True, "reason": reason, "runtime_s": runtime}


def execution_provenance(registration: Registration, out_dir: Path, *,
                         reused_existing: bool,
                         runtime_source: Path | None = None) -> dict[str, Any]:
    """Bind the reducer and every stored execution artifact by SHA-256."""
    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    sources = {
        "measure_direction_decision_sensitivity.py": Path(__file__).resolve(),
        "run_scenario.py": ROOT / "run_scenario.py",
        "closure_ranking.py": (
            ROOT / "traffic_sim/simulation/closure_ranking.py"),
        "contracts.py": ROOT / "traffic_sim/core/contracts.py",
    }
    artifacts: dict[str, dict[str, str]] = {}
    for case in registration.stress_cases:
        for seed in registration.seeds:
            for candidate in registration.candidate_edges:
                tag = f"cand_{case}_{seed}_{candidate}"
                run_dir = out_dir / tag
                for kind, path in (
                    ("spec", run_dir / "spec.json"),
                    ("scenario", run_dir / f"{tag}.json"),
                ):
                    if not path.is_file():
                        raise RegistrationError(
                            f"cannot seal Gate S: missing {path}")
                    artifacts[f"{tag}/{kind}"] = {
                        "path": str(path), "sha256": digest(path)}

    payload = {
        "protocol": "dirsplit_gate_s_execution_provenance_v1",
        "registration_key": registration.content_key,
        "reused_existing": bool(reused_existing),
        "sources": {name: {"path": str(path), "sha256": digest(path)}
                    for name, path in sources.items()},
        "execution_artifacts": artifacts,
        "execution_artifact_count": len(artifacts),
    }
    if runtime_source is not None:
        payload["runtime_source"] = {
            "path": str(runtime_source), "sha256": digest(runtime_source)}
    payload["content_key"] = content_digest(payload)
    return payload


# ──────────────────────────────────────────────────────────────────────────
# deciding Gate S
# ──────────────────────────────────────────────────────────────────────────
def policy_decision_for_case(observations: Sequence[Observation],
                             case: str) -> dict[str, Any]:
    """Run the REAL closure policy over one stress case's candidates.

    Uses ``closure_ranking`` unchanged: the same worst-across-variants
    reduction, the same no-detour disqualifier, the same lexicographic sort.
    Anything else would answer a question the product never asks.
    """
    from traffic_sim.simulation.closure_ranking import (rank_closures,
                                                        worst_variant_cost)

    by_candidate: dict[str, list[dict[str, float]]] = {}
    for obs in observations:
        if obs.stress_case == case and obs.policy is not None:
            by_candidate.setdefault(obs.candidate, []).append(obs.policy)

    costs = [worst_variant_cost(candidate, records)
             for candidate, records in sorted(by_candidate.items())]
    viable, refused = rank_closures(costs)
    ranking = [cost.candidate_id for cost in viable]
    return {
        "viable_set": sorted(cost.candidate_id for cost in viable),
        "ranking": ranking,
        "winner": ranking[0] if ranking else None,
        "disqualified": sorted(cost.candidate_id for cost in refused),
        "added_vehicle_hours": {cost.candidate_id: cost.added_vehicle_hours
                                for cost in viable + refused},
    }


def seed_invariance(observations: Sequence[Observation]) -> dict[str, Any]:
    """Check, don't assume, that the ranking key is seed-independent.

    ``closure_disruption`` reads the calibrated route file and free-flow
    costs, so for a fixed (case, candidate) the policy fields must be
    identical at every seed. Verifying it is what makes the between-case
    comparison meaningful: if the key DID move across seeds, the deployed
    ranking would carry Monte Carlo noise it does not declare, and that is a
    finding rather than a nuisance to average away.
    """
    grouped: dict[tuple[str, str], list[dict[str, float]]] = {}
    for obs in observations:
        if obs.policy is not None:
            grouped.setdefault((obs.stress_case, obs.candidate),
                               []).append(obs.policy)
    violations = []
    for (case, candidate), records in sorted(grouped.items()):
        for name in POLICY_FIELDS:
            values = {round(float(record[name]), 6) for record in records}
            if len(values) > 1:
                violations.append(
                    f"{case}/{candidate}: {name} varies across seeds "
                    f"{sorted(values)}")
    return {
        "ranking_key_is_seed_deterministic": not violations,
        "violations": violations,
        "basis": ("closure_disruption is demand-side and congestion-"
                  "independent, so this is an invariant of the deployed "
                  "policy, verified here rather than assumed"),
    }


def seed_execution_identity(observations: Sequence[Observation]) -> dict[str, Any]:
    """Verify seed identity from runner output, not from an outcome heuristic.

    Fixed-route simulations may insert exactly the same number of vehicles at
    several legitimate seeds. Requiring that count to vary confuses "the seed
    was executed" with "one chosen observable happened to move". The runner's
    scenario and health records carry the actual seed and resolved variant;
    those identities are the auditable contract.
    """
    violations = []
    for obs in observations:
        expected_variant = obs.stress_case
        executed_seed = (obs.seed if obs.executed_seed is None
                         and obs.seed_identity_verified else obs.executed_seed)
        executed_variant = (expected_variant if obs.executed_variant is None
                            and obs.seed_identity_verified
                            else obs.executed_variant)
        if (not obs.seed_identity_verified or executed_seed != obs.seed
                or executed_variant != expected_variant):
            violations.append(
                f"{obs.stress_case}/{obs.seed}/{obs.candidate}: executed "
                f"seed={executed_seed}, variant={executed_variant}")
    return {
        "verified": not violations,
        "violations": violations,
        "basis": ("scenario_spec seed/mapping and the semantic prefix of "
                  "the private route recorded by seed_health must both "
                  "exactly match the preregistered ScenarioSpec"),
    }


def decide_gate_s(observations: Sequence[Observation],
                  registration: Registration) -> dict[str, Any]:
    """Decide Gate S on the preregistered decision fields.

    Fail-closed: missing observations, timeouts, unclean closure integrity,
    unhealthy seeds or an unverified seed identity produce ``INCONCLUSIVE``, never
    ``NO``. "We could not measure it" and "we measured it and it does not
    matter" are different answers and must not be collapsed.
    """
    expected = (len(registration.stress_cases) * len(registration.seeds)
                * len(registration.candidate_edges))
    if len(observations) != expected:
        return _inconclusive(
            f"incomplete matrix: {len(observations)} of {expected} "
            "observations", observations, registration)

    failures = [o for o in observations if o.hard_failure]
    usable = [o for o in observations
              if not o.hard_failure and o.policy is not None]
    if len(usable) < expected:
        return _inconclusive(
            f"{expected - len(usable)} observation(s) failed or produced no "
            "policy record", observations, registration,
            failure_reasons=sorted({o.failure_reason for o in failures
                                    if o.failure_reason}))

    thresholds = registration.thresholds

    if thresholds.require_clean_closure_integrity:
        unclean = sorted({f"{o.stress_case}/{o.seed}/{o.candidate}="
                          f"{o.closure_integrity}" for o in usable
                          if o.closure_integrity != "verified_clean"})
        if unclean:
            return _inconclusive(
                "closure integrity is not verified_clean in "
                f"{len(unclean)} run(s): {unclean[:5]}",
                observations, registration)

    if thresholds.require_healthy_seeds:
        unhealthy = sorted({f"{o.stress_case}/{o.seed}/{o.candidate}: "
                            f"{'; '.join(o.seed_health_flags)}"
                            for o in usable if o.seed_health_flags})
        if unhealthy:
            return _inconclusive(
                f"SUMO health flags raised in {len(unhealthy)} run(s): "
                f"{unhealthy[:5]}", observations, registration)

    if (thresholds.require_direction_isolating_stress_cases
            and not registration.stress_cases_isolate_direction):
        return _inconclusive(
            "the stress cases do not isolate the direction axis: "
            + (registration.stress_case_isolation.get("reason")
               or "the direction-split pairs do not sum to 1"),
            observations, registration,
            isolation=registration.stress_case_isolation)

    seed_identity = seed_execution_identity(usable)
    if (thresholds.require_seed_execution_identity
            and not seed_identity["verified"]):
        return _inconclusive(
            "the runner output does not match the preregistered seed/variant "
            "identity", observations, registration,
            seed_execution_identity=seed_identity)

    # A ranking key that moves across seeds is a BROKEN MEASUREMENT, not
    # evidence about direction. The deployed key is demand-side and cannot
    # legitimately vary with the seed, so if it does, this run has not
    # measured what it claims to and neither YES nor NO is available. The
    # previous version appended it to material_reasons, which turned a
    # measurement fault into a YES — the single worst failure mode for a
    # gate whose YES unlocks product work.
    invariance = seed_invariance(usable)
    if not invariance["ranking_key_is_seed_deterministic"]:
        return _inconclusive(
            "the deployed ranking key is NOT seed-deterministic, so this "
            "matrix did not isolate direction from simulator noise: "
            + "; ".join(invariance["violations"][:3]),
            observations, registration, seed_invariance=invariance)

    material_reasons: list[str] = []

    # ── the real policy, applied once per stress case ────────────────────
    decision_by_case = {case: policy_decision_for_case(usable, case)
                        for case in registration.stress_cases}

    viable_sets = {tuple(d["viable_set"]) for d in decision_by_case.values()}
    if thresholds.require_identical_viable_set and len(viable_sets) > 1:
        material_reasons.append(
            "the viable set differs between stress cases: "
            + json.dumps({c: d["viable_set"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    rankings = {tuple(d["ranking"]) for d in decision_by_case.values()}
    rankings_identical = len(rankings) == 1
    if thresholds.require_identical_ranking and not rankings_identical:
        material_reasons.append(
            "the candidate ranking differs between stress cases: "
            + json.dumps({c: d["ranking"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    winners = {case: d["winner"] for case, d in decision_by_case.items()}
    winner_identical = len(set(winners.values())) == 1
    if thresholds.require_identical_winner and not winner_identical:
        material_reasons.append(
            f"the winner differs between stress cases: {winners}")

    disqualified = {tuple(d["disqualified"]) for d in decision_by_case.values()}
    if len(disqualified) > 1:
        material_reasons.append(
            "no-detour disqualification differs between stress cases: "
            + json.dumps({c: d["disqualified"]
                          for c, d in decision_by_case.items()},
                         sort_keys=True))

    # ── per-candidate spread on the deployed ranking key ─────────────────
    # Only a candidate that is VIABLE somewhere can have its cost spread
    # count. The deployed policy never ranks a disqualified candidate
    # against a viable one — a schedule that severs someone's destination is
    # refused whatever it costs — so a candidate disqualified in every case
    # has the same fate in every case, and its cost spread cannot change a
    # decision. Letting it open the gate was a false YES: it credited
    # direction with moving a number the policy does not read.
    always_disqualified = {
        candidate for candidate in registration.candidate_edges
        if all(candidate in decision_by_case[case]["disqualified"]
               for case in registration.stress_cases)
    }
    per_candidate: dict[str, Any] = {}
    for candidate in registration.candidate_edges:
        case_values = {
            case: decision_by_case[case]["added_vehicle_hours"].get(candidate)
            for case in registration.stress_cases
        }
        present = [value for value in case_values.values() if value is not None]
        if not present:
            continue
        between = max(present) - min(present)
        reference = abs(statistics.fmean(present)) or 1.0
        relative = between / reference
        decision_relevant = candidate not in always_disqualified
        exceeds = (decision_relevant
                   and relative >= thresholds.relative_objective)
        per_candidate[candidate] = {
            "added_vehicle_hours_by_case": {
                case: (None if value is None else round(value, 4))
                for case, value in case_values.items()},
            "between_case_range": round(between, 4),
            "relative_between_case": round(relative, 4),
            "seed_range_within_case": 0.0,
            "decision_relevant": decision_relevant,
            "material": bool(exceeds),
            **({"why_not_decision_relevant":
                "disqualified by the no-detour rule in every stress case, so "
                "the deployed policy never reads its cost"}
               if not decision_relevant else {}),
        }
        if exceeds:
            material_reasons.append(
                f"{candidate}: added_vehicle_hours spans {between:.4f} h "
                f"across stress cases, {relative:.1%} of its own mean")

    if always_disqualified and always_disqualified == set(
            registration.candidate_edges):
        # Nothing was ever viable, so there was no decision to be sensitive
        # about. Reporting NO here would claim direction is irrelevant on
        # the strength of an experiment that never ranked anything.
        return _inconclusive(
            "every candidate was disqualified by the no-detour rule in every "
            "stress case, so no viable set was ever formed and there was no "
            "decision for direction to change",
            observations, registration,
            always_disqualified=sorted(always_disqualified))

    gate = "YES" if material_reasons else "NO"
    return {
        "protocol": PROTOCOL,
        "gate_s": gate,
        "registration_key": registration.content_key,
        "release_evidence": False,
        "reasons": material_reasons,
        "decision_by_case": decision_by_case,
        "per_candidate": per_candidate,
        "rankings_identical": rankings_identical,
        "winner_identical": winner_identical,
        "winner_by_case": winners,
        "seed_execution_identity": seed_identity,
        "seed_invariance": invariance,
        "policy": "traffic_sim.simulation.closure_ranking (deployed, unchanged)",
        "n_observations": len(observations),
        "n_usable": len(usable),
        "n_hard_failures": len(failures),
        "total_runtime_s": round(sum(o.runtime_s for o in observations), 1),
        "observations": [o.to_json() for o in observations],
    }


def _inconclusive(reason: str, observations: Sequence[Observation],
                  registration: Registration,
                  failure_reasons: Sequence[str] = (),
                  **context: Any) -> dict[str, Any]:
    """One INCONCLUSIVE shape, with whatever evidence the caller measured.

    ``context`` carries the diagnostic the specific refusal produced —
    ``seed_axis``, ``seed_invariance``, ``isolation``,
    ``always_disqualified`` — so the artifact records WHY it could not
    decide, not merely that it could not.
    """
    payload = {
        "protocol": PROTOCOL,
        "gate_s": "INCONCLUSIVE",
        "registration_key": registration.content_key,
        "release_evidence": False,
        "reasons": [reason],
        "failure_reasons": list(failure_reasons),
        "n_observations": len(observations),
        "note": ("INCONCLUSIVE is not NO. It forbids product integration and "
                 "requires repairing measurability, then rerunning a fresh "
                 "frozen version without selecting cases from this outcome."),
        "observations": [o.to_json() for o in observations],
    }
    for key, value in context.items():
        if value is not None:
            payload[key] = dict(value) if isinstance(value, Mapping) else value
    return payload


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────
def build_registration(sumo_dir: Path, *, date: str, begin: str, end: str,
                       seeds: Sequence[int], count: int,
                       timeout_s: int) -> Registration:
    from traffic_sim.intake.sensors import load_registry

    registry = load_registry(Path("data_in/sensors.json"))
    sensor_edges = {edge for record in registry.records.values()
                    for edge in record.approved_edge_ids}
    # Validate the claim BEFORE selecting: a registration naming a date the
    # loaded demand is not describes an experiment nobody ran, and the
    # candidate selection would already be computed from the wrong build.
    validate_registered_date(sumo_dir, date)
    candidates, selection = select_candidates(sumo_dir, sensor_edges, count)
    demand_id, network_id = demand_identity(sumo_dir)
    closure_begin, closure_end = closure_window(sumo_dir, begin, end, date)
    isolation = measure_pair_sum_isolation(
        sumo_dir, MaterialityThresholds().pair_sum_tolerance)

    digests = {}
    for _name, filename, _variant in STRESS_CASES:
        path = sumo_dir / filename
        if path.exists():
            digests[filename] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))
    for extra in ("net.net.xml", "direction_split.json", "demand_meta.json"):
        path = sumo_dir / extra
        if path.exists():
            digests[extra] = content_digest(
                path.read_bytes().decode("utf-8", "replace"))

    return Registration(
        protocol=PROTOCOL,
        selection_rule=SELECTION_RULE,
        date=date, window_begin=begin, window_end=end,
        closure_begin=closure_begin, closure_end=closure_end,
        seeds=tuple(seeds),
        stress_cases=tuple(name for name, _f, _v in STRESS_CASES),
        candidate_edges=tuple(candidates),
        candidate_selection=selection,
        timeout_s=timeout_s,
        objective="added_vehicle_hours (deployed closure_ranking key)",
        comparison_fields=("hard_failure", "closure_integrity",
                           "seed_health_flags", "vehicles_no_detour",
                           "viable_set", "candidate_ranking", "winner",
                           "added_vehicle_hours"),
        thresholds=MaterialityThresholds(),
        source_digests=digests,
        demand_build_id=demand_id,
        network_build_id=network_id,
        stress_case_isolation=isolation,
        note=("q10/q50/q90 are NAMED STRESS CASES with unvalidated nominal "
              "coverage, not probability statements. This measures spread, "
              "not a distribution. Each (case, seed) pair is bound through "
              "its own one-seed ScenarioSpec, and the decision is read from "
              "the deployed closure_ranking policy. The ranking key is "
              "demand-side and therefore seed-deterministic by construction; "
              "that invariant is verified per run — a violation is a broken "
              "measurement and yields INCONCLUSIVE, never YES — and the seed "
              "axis is verified from scenario and seed-health identity "
              "records. Gate S also "
              "requires the stress cases to ISOLATE direction: see "
              "stress_case_isolation."),
    )


def require_runnable_registration(registration: Registration) -> None:
    """Stop a known-invalid Gate S before the first expensive SUMO run."""
    if not registration.stress_cases_isolate_direction:
        raise RegistrationError(
            "Gate S is INCONCLUSIVE before simulation: the frozen stress "
            "artifacts failed strict split, build-lineage, or equal-departure "
            "validation. No SUMO matrix was started. Rebuild the demand "
            "artifacts and freeze a new v3 registration.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumo-dir", type=Path, default=Path("sumo"))
    parser.add_argument("--date", default="2025-09-16")
    parser.add_argument("--begin", default="06:00")
    parser.add_argument("--end", default="10:00")
    parser.add_argument("--seeds", type=int, nargs="+",
                        default=[1000, 1001, 1002, 1003])
    parser.add_argument("--candidates", type=int, default=4)
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--out-dir", type=Path,
                        default=Path("runs/dirsplit_sensitivity"))
    parser.add_argument("--registration", type=Path, default=REGISTRATION_PATH)
    parser.add_argument("--outcome", type=Path, default=OUTCOME_PATH)
    parser.add_argument("--freeze-only", action="store_true",
                        help="write the registration and stop, so selection "
                             "is provably frozen before execution")
    parser.add_argument(
        "--reuse-existing", action="store_true",
        help="re-reduce an already completed matrix after validating every "
             "stored spec and executed seed/route identity; never starts SUMO")
    args = parser.parse_args(argv)

    try:
        registration = build_registration(
            args.sumo_dir, date=args.date, begin=args.begin, end=args.end,
            seeds=args.seeds, count=args.candidates, timeout_s=args.timeout_s)
    except (RegistrationError, RuntimeError, FileNotFoundError) as error:
        raise SystemExit(f"cannot freeze this experiment: {error}")

    # A registration is append-only evidence.  Do not spend its canonical
    # version key on inputs that are already known to confound direction with
    # total volume (or whose route lineage cannot be verified).  The blocker
    # record is the right artifact for that diagnostic state; a Gate S
    # registration is only meaningful once the experiment is runnable.
    try:
        require_runnable_registration(registration)
    except RegistrationError as error:
        raise SystemExit(f"cannot freeze this experiment: {error}")

    payload = registration.to_json()
    payload["content_key"] = registration.content_key
    if args.registration.exists():
        existing = json.loads(args.registration.read_text())
        if existing.get("content_key") != registration.content_key:
            raise SystemExit(
                f"{args.registration} already exists with a DIFFERENT key. "
                "A registration is frozen evidence: publish a new version "
                "rather than editing a recorded one.")
        print(f"registration already frozen: {registration.content_key}")
    else:
        args.registration.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(args.registration,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(handle, "w") as file:
            json.dump(payload, file, indent=1, sort_keys=True)
            file.write("\n")
        print(f"froze registration {registration.content_key}")

    print(f"  candidates: {list(registration.candidate_edges)}")
    print(f"  seeds     : {list(registration.seeds)}")
    print(f"  cases     : {list(registration.stress_cases)}")
    print(f"  closure   : {registration.closure_begin} → "
          f"{registration.closure_end}")

    isolation = registration.stress_case_isolation
    print(f"  isolation : direction only (max |pair sum - 1| = "
          f"{isolation.get('max_abs_pair_sum_deviation')})")
    if args.freeze_only:
        return 0

    replay_runtimes: dict[tuple[str, int, str], float] = {}
    runtime_source = None
    if args.reuse_existing and OUTCOME_PATH.is_file():
        previous = json.loads(OUTCOME_PATH.read_text())
        for observation in previous.get("observations", []):
            replay_runtimes[(
                str(observation["stress_case"]), int(observation["seed"]),
                str(observation["candidate"]),
            )] = float(observation.get("runtime_s") or 0.0)
        runtime_source = OUTCOME_PATH

    observations = run_matrix(
        registration, args.sumo_dir, args.out_dir,
        reuse_existing=args.reuse_existing,
        replay_runtimes=replay_runtimes)
    outcome = decide_gate_s(observations, registration)
    outcome["execution_provenance"] = execution_provenance(
        registration, args.out_dir,
        reused_existing=args.reuse_existing,
        runtime_source=runtime_source)

    args.outcome.parent.mkdir(parents=True, exist_ok=True)
    if args.outcome.exists():
        raise SystemExit(f"{args.outcome} already exists; outcomes are "
                         "append-only")
    handle = os.open(args.outcome, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(handle, "w") as file:
        json.dump(outcome, file, indent=1, sort_keys=True)
        file.write("\n")

    print(f"\nGate S = {outcome['gate_s']}")
    for reason in outcome.get("reasons", []):
        print(f"  {reason}")
    print(f"Wrote {args.outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
