"""Offline diagnostic for route-departure and sensor-passage quarters.

PFE constrains how many published routes touch each measured edge in each
15-minute *departure* interval.  SUMO ``edgeData@entered`` records the later
event where a vehicle actually enters that edge.  A route can therefore meet
every integer PFE constraint while its simulated passage moves into the next
interval.

This module closes that clock mismatch without adding, deleting or rerouting a
vehicle.  It observes the production mesoscopic model for every configured
seed, derives a safe departure interval for each vehicle, and projects the
departures into those intervals while preserving the original XML order.
Preserving order is essential: SUMO samples the default per-vehicle speed
factor while loading the route file.  Reordering vehicles would attach a new
driver sample to an id on every reconciliation pass and make the calibration
oscillate.

The operation is intentionally offline.  An adopted demand build would pay for
the evidence runs once; interactive baseline and closure simulations would
read the reconciled route file with no new runtime option or extra output.  It is not wired into
the production builder: the active real-network candidate fails the departure-
dispersion gate below, so this remains a guarded diagnostic until a different
standard-pool structure clears every adoption gate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Mapping, Sequence


INTERVAL_S = 900
DEFAULT_GUARD_S = 60
DEFAULT_SEEDS = (1000, 1001, 1002)
MIN_DEPARTURE_GAP_S = 0.1
SUMO_TIMEOUT_S = 300


class DepartureReconciliationError(RuntimeError):
    """The route could not be reconciled without weakening its contract."""


@dataclass(frozen=True)
class RouteVehicle:
    vehicle_id: str
    depart_s: float
    edges: tuple[str, ...]


_VEHICLE_TAG_RE = re.compile(r"<vehicle\b[^>]*>")
_ID_ATTR_RE = re.compile(r'\bid="([^"]+)"')
_DEPART_ATTR_RE = re.compile(r'(\bdepart=")([^"]+)(")')


def read_route_vehicles(path: Path) -> list[RouteVehicle]:
    """Read the ordered vehicle contract from a concrete route file."""
    vehicles: list[RouteVehicle] = []
    seen: set[str] = set()
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag != "vehicle":
            continue
        vehicle_id = element.get("id")
        depart = element.get("depart")
        route = element.find("route")
        if not vehicle_id or vehicle_id in seen:
            raise DepartureReconciliationError(
                "route file has a missing or duplicate vehicle id")
        if depart is None or route is None or not route.get("edges"):
            raise DepartureReconciliationError(
                f"vehicle {vehicle_id} lacks numeric depart or concrete route")
        try:
            depart_s = float(depart)
        except ValueError as error:
            raise DepartureReconciliationError(
                f"vehicle {vehicle_id} has non-numeric depart") from error
        if not math.isfinite(depart_s):
            raise DepartureReconciliationError(
                f"vehicle {vehicle_id} has non-finite depart")
        vehicles.append(RouteVehicle(
            vehicle_id, depart_s, tuple(route.get("edges").split())))
        seen.add(vehicle_id)
        element.clear()
    if not vehicles:
        raise DepartureReconciliationError("route file contains no vehicles")
    if any(left.depart_s >= right.depart_s
           for left, right in zip(vehicles, vehicles[1:])):
        raise DepartureReconciliationError(
            "route departures must be strictly increasing before reconciliation")
    return vehicles


def validate_route_targets(
    vehicles: Sequence[RouteVehicle],
    targets: Mapping[str, Sequence[float]],
    *,
    n_intervals: int,
) -> None:
    """Prove that the source route already meets every integer PFE target."""
    measured = set(targets)
    if not measured:
        raise DepartureReconciliationError("sensor target map is empty")
    for edge_id, series in targets.items():
        if len(series) != n_intervals:
            raise DepartureReconciliationError(
                f"sensor {edge_id} target width differs from demand window")
    counts = {edge_id: [0] * n_intervals for edge_id in measured}
    for vehicle in vehicles:
        quarter = int(vehicle.depart_s // INTERVAL_S)
        if not 0 <= quarter < n_intervals:
            raise DepartureReconciliationError(
                f"vehicle {vehicle.vehicle_id} departs outside demand window")
        touches = [edge for edge in vehicle.edges if edge in measured]
        if len(touches) != len(set(touches)):
            raise DepartureReconciliationError(
                f"vehicle {vehicle.vehicle_id} repeats a measured edge")
        for edge_id in touches:
            counts[edge_id][quarter] += 1
    mismatches = []
    for edge_id, series in targets.items():
        for quarter, target in enumerate(series):
            expected = int(round(float(target)))
            if counts[edge_id][quarter] != expected:
                mismatches.append(
                    f"{edge_id} q{quarter}: {counts[edge_id][quarter]} != {expected}")
    if mismatches:
        raise DepartureReconciliationError(
            "source route is not exact before passage reconciliation: "
            + "; ".join(mismatches[:3]))


def parse_passage_evidence(
    path: Path,
    vehicles: Sequence[RouteVehicle],
    measured_edges: set[str],
) -> tuple[dict[str, list[float]], str]:
    """Parse one complete SUMO vehroute file into sensor-entry offsets.

    ``exitTimes[j-1]`` is the entry time for route edge ``j``.  For the first
    edge, the real vehicle departure is its entry time.  Offsets are measured
    against the intended departure in the source route, so insertion delay is
    included rather than silently assumed away.
    """
    expected = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
    offsets: dict[str, list[float]] = defaultdict(list)
    speed_factors: dict[str, str] = {}
    seen: set[str] = set()
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag != "vehicle":
            continue
        vehicle_id = element.get("id")
        if not vehicle_id or vehicle_id not in expected or vehicle_id in seen:
            raise DepartureReconciliationError(
                "vehroute evidence has an unknown or duplicate vehicle id")
        route = element.find("route")
        if route is None or route.get("edges") is None \
                or route.get("exitTimes") is None:
            raise DepartureReconciliationError(
                f"vehroute evidence for {vehicle_id} lacks exit times")
        edges = tuple(route.get("edges").split())
        if edges != expected[vehicle_id].edges:
            raise DepartureReconciliationError(
                f"SUMO route drifted for vehicle {vehicle_id}")
        try:
            actual_depart = float(element.get("depart"))
            exit_times = [float(value)
                          for value in route.get("exitTimes").split()]
        except (TypeError, ValueError) as error:
            raise DepartureReconciliationError(
                f"vehroute evidence for {vehicle_id} has invalid times") from error
        if len(exit_times) != len(edges):
            raise DepartureReconciliationError(
                f"vehroute evidence width differs from route for {vehicle_id}")
        factor = element.get("speedFactor")
        if factor is None:
            raise DepartureReconciliationError(
                "vehroute evidence must include per-vehicle speedFactor")
        speed_factors[vehicle_id] = factor
        intended = expected[vehicle_id].depart_s
        for index, edge_id in enumerate(edges):
            if edge_id in measured_edges:
                entry = actual_depart if index == 0 else exit_times[index - 1]
                offsets[vehicle_id].append(entry - intended)
        seen.add(vehicle_id)
        element.clear()
    if seen != set(expected):
        raise DepartureReconciliationError(
            f"vehroute evidence is incomplete ({len(seen)}/{len(expected)})")
    digest = hashlib.sha256()
    for vehicle in vehicles:
        digest.update(vehicle.vehicle_id.encode())
        digest.update(b"\0")
        digest.update(speed_factors[vehicle.vehicle_id].encode())
        digest.update(b"\n")
    return dict(offsets), digest.hexdigest()


def derive_monotone_departures(
    vehicles: Sequence[RouteVehicle],
    offsets_by_vehicle: Mapping[str, Sequence[float]],
    *,
    duration_s: int,
    guard_s: int = DEFAULT_GUARD_S,
    min_gap_s: float = MIN_DEPARTURE_GAP_S,
) -> dict[str, float]:
    """Project departures into robust passage windows without reordering.

    The intersection covers every measured edge crossed by the vehicle and
    every seed observation.  Forward/backward bounds then prove that a
    strictly ordered schedule exists before any value is changed.
    """
    if duration_s <= 0 or duration_s % INTERVAL_S:
        raise DepartureReconciliationError(
            "duration must be a positive whole number of 15-minute intervals")
    if guard_s < 0 or guard_s * 2 >= INTERVAL_S:
        raise DepartureReconciliationError("guard must be in [0, 450) seconds")
    if min_gap_s <= 0:
        raise DepartureReconciliationError("minimum departure gap must be positive")
    scale = 10
    gap_ticks = max(1, math.ceil(min_gap_s * scale))
    lower_ticks: list[int] = []
    upper_ticks: list[int] = []
    for vehicle in vehicles:
        offsets = list(offsets_by_vehicle.get(vehicle.vehicle_id, ()))
        if offsets:
            if any(not math.isfinite(float(value)) for value in offsets):
                raise DepartureReconciliationError(
                    f"vehicle {vehicle.vehicle_id} has non-finite evidence")
            quarter = int(vehicle.depart_s // INTERVAL_S)
            raw_low = max(quarter * INTERVAL_S - float(value)
                          for value in offsets)
            raw_high = min((quarter + 1) * INTERVAL_S - float(value)
                           for value in offsets)
            raw_low = max(0.0, raw_low) + guard_s
            raw_high = min(float(duration_s), raw_high) - guard_s
        else:
            # An unmeasured route contributes no passage constraint. Keep its
            # original time fixed; moving it would change demand for no gain.
            raw_low = raw_high = vehicle.depart_s
        low = math.ceil((raw_low - 1e-9) * scale)
        high = math.floor((raw_high + 1e-9) * scale)
        if low > high:
            raise DepartureReconciliationError(
                f"vehicle {vehicle.vehicle_id} has no guarded departure interval")
        lower_ticks.append(low)
        upper_ticks.append(high)

    forward: list[int] = []
    for low in lower_ticks:
        forward.append(max(low, forward[-1] + gap_ticks) if forward else low)
    backward = [0] * len(vehicles)
    for index in range(len(vehicles) - 1, -1, -1):
        backward[index] = min(
            upper_ticks[index],
            backward[index + 1] - gap_ticks
            if index + 1 < len(vehicles) else upper_ticks[index],
        )
    for index, vehicle in enumerate(vehicles):
        if forward[index] > backward[index]:
            raise DepartureReconciliationError(
                "no order-preserving departure schedule exists near vehicle "
                f"{vehicle.vehicle_id}")

    chosen: list[int] = []
    for index, vehicle in enumerate(vehicles):
        preferred = round(vehicle.depart_s * scale)
        value = min(max(preferred, forward[index]), backward[index])
        if chosen:
            value = max(value, chosen[-1] + gap_ticks)
        if value > backward[index]:
            raise DepartureReconciliationError(
                f"departure projection overflowed at {vehicle.vehicle_id}")
        chosen.append(value)
    return {vehicle.vehicle_id: chosen[index] / scale
            for index, vehicle in enumerate(vehicles)}


def departure_spacing_summary(departures: Sequence[float]) -> dict:
    """Describe insertion pressure introduced by a departure schedule."""
    if not departures:
        raise DepartureReconciliationError("departure schedule is empty")
    values = [float(value) for value in departures]
    if any(not math.isfinite(value) for value in values):
        raise DepartureReconciliationError("departure schedule is non-finite")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise DepartureReconciliationError(
            "departure schedule must be strictly increasing")
    gaps = [right - left for left, right in zip(values, values[1:])]
    per_second: Counter[int] = Counter(int(value) for value in values)
    return {
        "vehicles": len(values),
        "minimum_gap_s": round(min(gaps), 6) if gaps else None,
        "median_gap_s": round(statistics.median(gaps), 6) if gaps else None,
        "gaps_at_minimum": sum(
            value <= MIN_DEPARTURE_GAP_S + 0.01 for value in gaps),
        "maximum_departures_per_second": max(per_second.values()),
    }


def validate_departure_dispersion(source: dict, candidate: dict) -> None:
    """Reject exact schedules that manufacture insertion bursts.

    Exact counter totals are not an improvement if they are achieved by
    collapsing a formerly uniform route stream into convoys.  The relative
    limits scale with the input population and admit small rounding ties, but
    fail the large queue-forming compression observed in the real network.
    """
    source_median = source.get("median_gap_s")
    candidate_median = candidate.get("median_gap_s")
    if source_median is not None and candidate_median is not None \
            and candidate_median < source_median * 0.5:
        raise DepartureReconciliationError(
            "departure dispersion gate failed: median gap collapsed")
    source_peak = int(source["maximum_departures_per_second"])
    allowed_peak = max(source_peak + 1, math.ceil(source_peak * 1.5))
    if int(candidate["maximum_departures_per_second"]) > allowed_peak:
        raise DepartureReconciliationError(
            "departure dispersion gate failed: per-second burst increased")
    allowed_minimum_gaps = int(source["gaps_at_minimum"]) + max(
        10, math.ceil(int(source["vehicles"]) * 0.01))
    if int(candidate["gaps_at_minimum"]) > allowed_minimum_gaps:
        raise DepartureReconciliationError(
            "departure dispersion gate failed: too many minimum-gap vehicles")


def rewrite_departures_preserving_order(
    source_path: Path,
    output_path: Path,
    departures: Mapping[str, float],
) -> None:
    """Rewrite only ``depart`` values, preserving bytes and vehicle order."""
    source = source_path.read_text()
    replaced: set[str] = set()

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        id_match = _ID_ATTR_RE.search(tag)
        depart_match = _DEPART_ATTR_RE.search(tag)
        if id_match is None or depart_match is None:
            return tag
        vehicle_id = id_match.group(1)
        if vehicle_id not in departures or vehicle_id in replaced:
            raise DepartureReconciliationError(
                f"cannot rewrite departure for vehicle {vehicle_id}")
        replaced.add(vehicle_id)
        value = f"{departures[vehicle_id]:.1f}"
        return _DEPART_ATTR_RE.sub(
            lambda item: item.group(1) + value + item.group(3), tag, count=1)

    rewritten = _VEHICLE_TAG_RE.sub(replace_tag, source)
    if replaced != set(departures):
        raise DepartureReconciliationError(
            f"route rewrite covered {len(replaced)}/{len(departures)} vehicles")
    output_path.write_text(rewritten)


def _write_reconciled_agents(
    source_path: Path,
    output_path: Path,
    departures: Mapping[str, float],
) -> None:
    try:
        document = json.loads(source_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise DepartureReconciliationError("agent sidecar is unreadable") from error
    agents = document.get("agents") if isinstance(document, dict) else None
    if not isinstance(agents, list):
        raise DepartureReconciliationError("agent sidecar lacks agents list")
    seen: set[str] = set()
    for agent in agents:
        vehicle_id = agent.get("vehicle_id") if isinstance(agent, dict) else None
        if vehicle_id not in departures or vehicle_id in seen:
            raise DepartureReconciliationError(
                "agent sidecar has unknown or duplicate vehicle id")
        agent["departure_s"] = round(departures[vehicle_id], 1)
        seen.add(vehicle_id)
    if seen != set(departures):
        raise DepartureReconciliationError(
            f"agent sidecar covered {len(seen)}/{len(departures)} vehicles")
    output_path.write_text(json.dumps(document, separators=(",", ":")))


def _write_edgedata_additional(
    path: Path,
    output_name: str,
    measured_edges: Sequence[str],
    duration_s: int,
) -> None:
    root = ET.Element("additional")
    ET.SubElement(root, "edgeData", {
        "id": "sensor-passage-verification",
        "file": output_name,
        "period": str(INTERVAL_S),
        "begin": "0",
        "end": str(duration_s),
        "excludeEmpty": "true",
        "writeAttributes": "entered",
        "edges": " ".join(sorted(measured_edges)),
    })
    ET.ElementTree(root).write(path, encoding="unicode")


def _sumo_command(
    *,
    sumo_home: Path,
    net_path: Path,
    route_path: Path,
    seed: int,
    duration_s: int,
    stats_path: Path,
    additional_path: Path | None = None,
    vehroute_path: Path | None = None,
) -> list[str]:
    command = [
        str(sumo_home / "bin" / "sumo"),
        "--mesosim", "true",
        "--meso-junction-control", "true",
        "--meso-junction-control.limited", "true",
        "-n", str(net_path.resolve()),
        "-r", str(route_path.resolve()),
        "--seed", str(seed),
        "--begin", "0",
        "--end", str(duration_s + 3600),
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--ignore-route-errors", "true",
        "--statistic-output", str(stats_path.resolve()),
    ]
    if additional_path is not None:
        command.extend(["-a", str(additional_path.resolve())])
    if vehroute_path is not None:
        command.extend([
            "--vehroute-output", str(vehroute_path.resolve()),
            "--vehroute-output.exit-times", "true",
            "--vehroute-output.write-unfinished", "true",
            "--vehroute-output.speedfactor", "true",
        ])
    return command


def _run_sumo(command: Sequence[str], *, cwd: Path, sumo_home: Path) -> None:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, cwd=str(cwd),
            env={"SUMO_HOME": str(sumo_home)}, timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired as error:
        raise DepartureReconciliationError(
            f"SUMO passage evidence timed out after {SUMO_TIMEOUT_S}s") from error
    if result.returncode:
        raise DepartureReconciliationError(
            "SUMO passage evidence failed: " + result.stderr[-1000:])


def _validate_stats(path: Path, expected_vehicles: int) -> None:
    try:
        root = ET.parse(path).getroot()
        vehicles = root.find("vehicles")
        teleports = root.find("teleports")
        safety = root.find("safety")
        values = {
            "loaded": int(vehicles.get("loaded")),
            "inserted": int(vehicles.get("inserted")),
            "running": int(vehicles.get("running")),
            "waiting": int(vehicles.get("waiting")),
            "teleports": int(teleports.get("total")),
            "collisions": int(safety.get("collisions")),
        }
    except (AttributeError, OSError, TypeError, ValueError,
            ET.ParseError) as error:
        raise DepartureReconciliationError("SUMO statistics are invalid") from error
    expected = {
        "loaded": expected_vehicles,
        "inserted": expected_vehicles,
        "running": 0,
        "waiting": 0,
        "teleports": 0,
        "collisions": 0,
    }
    if values != expected:
        raise DepartureReconciliationError(
            f"SUMO passage evidence failed health contract: {values}")


def _parse_entered(path: Path, n_intervals: int,
                   measured_edges: Sequence[str]) -> dict[str, list[int]]:
    result = {edge_id: [0] * n_intervals for edge_id in measured_edges}
    try:
        for _event, element in ET.iterparse(path, events=("end",)):
            if element.tag != "interval":
                continue
            quarter = int(float(element.get("begin")) // INTERVAL_S)
            if 0 <= quarter < n_intervals:
                for edge in element.findall("edge"):
                    edge_id = edge.get("id")
                    if edge_id in result:
                        value = float(edge.get("entered") or 0)
                        if not value.is_integer():
                            raise DepartureReconciliationError(
                                "edgeData entered count is not integral")
                        result[edge_id][quarter] = int(value)
            element.clear()
    except (OSError, TypeError, ValueError, ET.ParseError) as error:
        if isinstance(error, DepartureReconciliationError):
            raise
        raise DepartureReconciliationError("SUMO edgeData is invalid") from error
    return result


def _validate_exact_output(
    entered: Mapping[str, Sequence[int]],
    targets: Mapping[str, Sequence[float]],
) -> None:
    mismatches = []
    for edge_id, series in targets.items():
        for quarter, target in enumerate(series):
            expected = int(round(float(target)))
            actual = entered[edge_id][quarter]
            if actual != expected:
                mismatches.append(
                    f"{edge_id} q{quarter}: {actual} != {expected}")
    if mismatches:
        raise DepartureReconciliationError(
            "reconciled SUMO output is not exact: "
            + "; ".join(mismatches[:3]))


def _validate_rewrite(
    source: Sequence[RouteVehicle],
    candidate_path: Path,
    departures: Mapping[str, float],
) -> None:
    candidate = read_route_vehicles(candidate_path)
    if len(candidate) != len(source):
        raise DepartureReconciliationError("route rewrite changed vehicle count")
    for before, after in zip(source, candidate):
        if before.vehicle_id != after.vehicle_id or before.edges != after.edges:
            raise DepartureReconciliationError(
                "route rewrite changed vehicle order, ids, or routes")
        if after.depart_s != round(departures[before.vehicle_id], 1):
            raise DepartureReconciliationError(
                f"route rewrite changed departure for {before.vehicle_id}")


def reconcile_sensor_passage_times(
    route_path: Path,
    agent_path: Path,
    targets: Mapping[str, Sequence[float]],
    *,
    duration_s: int,
    net_path: Path,
    sumo_home: Path,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    guard_s: int = DEFAULT_GUARD_S,
) -> dict:
    """Reconcile and atomically publish one calibrated route/agent pair.

    The live pair is replaced only after an independent SUMO verification for
    every seed proves exact raw ``entered`` counts and clean vehicle health.
    """
    if not seeds or any(isinstance(seed, bool) or not isinstance(seed, int)
                        for seed in seeds) or len(set(seeds)) != len(seeds):
        raise DepartureReconciliationError("seeds must be distinct integers")
    vehicles = read_route_vehicles(route_path)
    n_intervals = duration_s // INTERVAL_S
    validate_route_targets(vehicles, targets, n_intervals=n_intervals)
    measured_edges = set(targets)
    offsets: dict[str, list[float]] = defaultdict(list)
    profile_digests = []
    learning_started = time.perf_counter()

    with tempfile.TemporaryDirectory(
            prefix="passage-reconcile-", dir=str(route_path.parent)) as raw_tmp:
        workspace = Path(raw_tmp)
        for seed in seeds:
            run_dir = workspace / f"learn-{seed}"
            run_dir.mkdir()
            stats_path = run_dir / "stats.xml"
            vehroute_path = run_dir / "vehroute.xml"
            command = _sumo_command(
                sumo_home=sumo_home, net_path=net_path,
                route_path=route_path, seed=seed, duration_s=duration_s,
                stats_path=stats_path, vehroute_path=vehroute_path)
            _run_sumo(command, cwd=run_dir, sumo_home=sumo_home)
            _validate_stats(stats_path, len(vehicles))
            seed_offsets, profile_digest = parse_passage_evidence(
                vehroute_path, vehicles, measured_edges)
            for vehicle_id, values in seed_offsets.items():
                offsets[vehicle_id].extend(values)
            profile_digests.append(profile_digest)
            vehroute_path.unlink()
        if len(seeds) > 1 and len(set(profile_digests)) != len(seeds):
            raise DepartureReconciliationError(
                "SUMO seed driver profiles unexpectedly collapsed")
        learning_s = time.perf_counter() - learning_started

        departures = derive_monotone_departures(
            vehicles, offsets, duration_s=duration_s, guard_s=guard_s)
        source_spacing = departure_spacing_summary(
            [vehicle.depart_s for vehicle in vehicles])
        candidate_spacing = departure_spacing_summary(
            [departures[vehicle.vehicle_id] for vehicle in vehicles])
        validate_departure_dispersion(source_spacing, candidate_spacing)
        candidate_path = workspace / route_path.name
        candidate_agents = workspace / agent_path.name
        rewrite_departures_preserving_order(
            route_path, candidate_path, departures)
        _write_reconciled_agents(agent_path, candidate_agents, departures)
        _validate_rewrite(vehicles, candidate_path, departures)

        verification_started = time.perf_counter()
        for seed in seeds:
            run_dir = workspace / f"verify-{seed}"
            run_dir.mkdir()
            stats_path = run_dir / "stats.xml"
            edge_path = run_dir / "edge.xml"
            additional_path = run_dir / "edge.add.xml"
            _write_edgedata_additional(
                additional_path, edge_path.name,
                sorted(measured_edges), duration_s)
            command = _sumo_command(
                sumo_home=sumo_home, net_path=net_path,
                route_path=candidate_path, seed=seed,
                duration_s=duration_s, stats_path=stats_path,
                additional_path=additional_path)
            _run_sumo(command, cwd=run_dir, sumo_home=sumo_home)
            _validate_stats(stats_path, len(vehicles))
            entered = _parse_entered(
                edge_path, n_intervals, sorted(measured_edges))
            _validate_exact_output(entered, targets)
        verification_s = time.perf_counter() - verification_started

        # Preserve recoverable copies until both replacements succeed.  The
        # demand-build lock prevents concurrent readers, while rollback keeps
        # the old coherent pair if the second replace fails.
        route_backup = workspace / "original.rou.xml"
        agent_backup = workspace / "original.agents.json"
        shutil.copy2(route_path, route_backup)
        shutil.copy2(agent_path, agent_backup)
        try:
            os.replace(candidate_path, route_path)
            os.replace(candidate_agents, agent_path)
        except OSError:
            shutil.copy2(route_backup, route_path)
            shutil.copy2(agent_backup, agent_path)
            raise

    shifts = [departures[vehicle.vehicle_id] - vehicle.depart_s
              for vehicle in vehicles]
    return {
        "schema_version": 1,
        "contract": "sumo_entered_15min_monotone_departure_v1",
        "status": "exact",
        "seeds": list(seeds),
        "vehicles": len(vehicles),
        "routes_changed": 0,
        "vehicles_added": 0,
        "vehicles_removed": 0,
        "shifted_departures": sum(abs(value) >= 0.05 for value in shifts),
        "minimum_shift_s": round(min(shifts), 1),
        "maximum_shift_s": round(max(shifts), 1),
        "guard_s": guard_s,
        "constraints_per_seed": len(targets) * n_intervals,
        "exact_per_seed": len(targets) * n_intervals,
        "seed_profile_digests": profile_digests,
        "source_departure_spacing": source_spacing,
        "candidate_departure_spacing": candidate_spacing,
        "learning_s": round(learning_s, 3),
        "verification_s": round(verification_s, 3),
    }
