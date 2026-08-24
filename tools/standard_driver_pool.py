"""Build an isolated, day-specific pool of fixed SUMO driver profiles.

The demand picker remains responsible for *which* vehicles and routes exist on
one date.  This tool runs after that choice.  It gives every vehicle a stable
``speedFactor`` in each declared arm, derives one shared departure schedule,
and accepts the pool only when all arms reproduce every integer 15-minute
sensor target in raw SUMO ``edgeData@entered`` output.

Nothing is published into ``sumo/``.  A successful pool is written under a
content-addressed output directory and is therefore an experiment until the
paired baseline/closure latency gate is run separately.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
from pathlib import Path
from statistics import NormalDist, mean, median, pstdev
import os
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import departure_reconciliation as passage
from traffic_sim.simulation.runtime import sumo_home


SCHEMA_VERSION = 1
DEFAULT_ARMS = (1000, 1001, 1002)
DEFAULT_GUARD_S = 60
DEFAULT_MAX_ITERATIONS = 5
SPEED_FACTOR_MEAN = 1.0
SPEED_FACTOR_DEVIATION = 0.1
SPEED_FACTOR_MINIMUM = 0.2
SPEED_FACTOR_MAXIMUM = 2.0
TICK_SCALE = 10


class StandardDriverPoolError(RuntimeError):
    """The proposed standard pool failed a construction or evidence gate."""


@dataclass(frozen=True)
class DepartureWindow:
    """Allowed shared departure interval for one vehicle, in 0.1 s ticks."""

    vehicle_id: str
    preferred: int
    lower: int
    upper: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pool_identity(
    route_path: Path,
    metadata: Mapping[str, Any],
    arms: Sequence[int],
    *,
    network_path: Path | None = None,
    guard_s: int = DEFAULT_GUARD_S,
) -> dict[str, Any]:
    """Bind profiles to this picker result, date, route bytes and arm set."""
    if not arms or len(set(arms)) != len(arms) or any(
            isinstance(arm, bool) or not isinstance(arm, int) for arm in arms):
        raise StandardDriverPoolError("driver-profile arms must be unique integers")
    date = metadata.get("date") or metadata.get("start_date")
    build_id = metadata.get("build_id")
    if not isinstance(date, str) or not date or not isinstance(build_id, str) \
            or not build_id:
        raise StandardDriverPoolError(
            "demand metadata must identify its date and build_id")
    profile_identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": "day_specific_standard_driver_pool",
        "date": date,
        "source": metadata.get("source"),
        "days": metadata.get("days"),
        "demand_build_id": build_id,
        "demand_build_key": metadata.get("demand_build_key"),
        "route_sha256": _sha256(route_path),
        "arms": list(arms),
        "speed_factor_distribution": {
            "name": "truncated_normal",
            "mean": SPEED_FACTOR_MEAN,
            "deviation": SPEED_FACTOR_DEVIATION,
            "minimum": SPEED_FACTOR_MINIMUM,
            "maximum": SPEED_FACTOR_MAXIMUM,
        },
    }
    encoded = json.dumps(
        profile_identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    profile_key = hashlib.sha256(encoded.encode()).hexdigest()[:32]
    identity = {
        **profile_identity,
        "profile_key": profile_key,
        "network_sha256": (
            _sha256(network_path) if network_path is not None else None),
        "sensor_targets_sha256": hashlib.sha256(json.dumps(
            metadata.get("sensor_targets"), sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "guard_s": guard_s,
        "construction_algorithm": "fixed-profiles-edf-near-slots-v1",
    }
    build_encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity["pool_key"] = hashlib.sha256(build_encoded.encode()).hexdigest()[:32]
    return identity


def stable_speed_factor(pool_key: str, arm: int, vehicle_id: str) -> float:
    """Return a reproducible draw from SUMO's default passenger distribution.

    Hashing makes the assignment independent of XML load order.  The open
    midpoint conversion avoids the infinities at a normal CDF's endpoints.
    """
    if not pool_key or not vehicle_id:
        raise StandardDriverPoolError("pool key and vehicle id must be nonempty")
    digest = hashlib.sha256(
        f"standard-driver-pool-v1\0{pool_key}\0{arm}\0{vehicle_id}".encode()
    ).digest()
    numerator = int.from_bytes(digest[:8], "big") + 0.5
    probability = numerator / (1 << 64)
    value = NormalDist(
        mu=SPEED_FACTOR_MEAN,
        sigma=SPEED_FACTOR_DEVIATION,
    ).inv_cdf(probability)
    return round(min(SPEED_FACTOR_MAXIMUM,
                     max(SPEED_FACTOR_MINIMUM, value)), 6)


def build_driver_profiles(
    vehicles: Sequence[passage.RouteVehicle],
    *,
    pool_key: str,
    arms: Sequence[int],
) -> dict[int, dict[str, float]]:
    """Build distinct, complete per-arm mappings without changing demand."""
    profiles = {
        arm: {
            vehicle.vehicle_id: stable_speed_factor(
                pool_key, arm, vehicle.vehicle_id)
            for vehicle in vehicles
        }
        for arm in arms
    }
    expected = {vehicle.vehicle_id for vehicle in vehicles}
    for arm, profile in profiles.items():
        if set(profile) != expected:
            raise StandardDriverPoolError(f"arm {arm} is incomplete")
    digests = {
        hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        for profile in profiles.values()
    }
    if len(profiles) > 1 and len(digests) != len(profiles):
        raise StandardDriverPoolError("driver-profile arms are not distinct")
    return profiles


def profile_summary(profile: Mapping[str, float]) -> dict[str, float | int]:
    """Summarize a fixed profile for provenance and distribution checks."""
    values = [float(value) for value in profile.values()]
    if not values or any(not math.isfinite(value) for value in values):
        raise StandardDriverPoolError("driver profile is empty or non-finite")
    if min(values) < SPEED_FACTOR_MINIMUM or max(values) > SPEED_FACTOR_MAXIMUM:
        raise StandardDriverPoolError("driver profile exceeds declared bounds")
    return {
        "vehicles": len(values),
        "mean": round(mean(values), 6),
        "deviation": round(pstdev(values), 6),
        "minimum": round(min(values), 6),
        "maximum": round(max(values), 6),
    }


def validate_profile_distribution(summary: Mapping[str, float | int]) -> None:
    """Reject a large pool whose deterministic draw is implausibly distorted."""
    vehicles = int(summary["vehicles"])
    if vehicles < 100:
        return
    if abs(float(summary["mean"]) - SPEED_FACTOR_MEAN) > 0.02:
        raise StandardDriverPoolError("driver-profile mean drift exceeds 0.02")
    deviation = float(summary["deviation"])
    if not 0.08 <= deviation <= 0.12:
        raise StandardDriverPoolError(
            "driver-profile deviation is outside [0.08, 0.12]")


def load_sensor_targets(
    metadata: Mapping[str, Any],
    *,
    variant: str = "edge_shares",
) -> tuple[dict[str, list[float]], int]:
    """Read the exact target map and duration from one demand build."""
    sensor_targets = metadata.get("sensor_targets")
    variants = sensor_targets.get("variants") \
        if isinstance(sensor_targets, Mapping) else None
    target_map = variants.get(variant) if isinstance(variants, Mapping) else None
    n_intervals = metadata.get("n_intervals")
    if not isinstance(target_map, Mapping) or not target_map:
        raise StandardDriverPoolError(
            f"demand metadata lacks sensor target variant {variant!r}")
    if isinstance(n_intervals, bool) or not isinstance(n_intervals, int) \
            or n_intervals <= 0:
        raise StandardDriverPoolError("demand metadata has invalid n_intervals")
    targets: dict[str, list[float]] = {}
    for edge_id, values in target_map.items():
        if not isinstance(edge_id, str) or not isinstance(values, list) \
                or len(values) != n_intervals:
            raise StandardDriverPoolError("sensor target shape is invalid")
        try:
            converted = [float(value) for value in values]
        except (TypeError, ValueError) as error:
            raise StandardDriverPoolError("sensor target is non-numeric") from error
        if any(not math.isfinite(value) or value < 0 for value in converted):
            raise StandardDriverPoolError("sensor target is negative or non-finite")
        targets[edge_id] = converted
    return targets, n_intervals * passage.INTERVAL_S


def write_profile_route(
    source_path: Path,
    output_path: Path,
    profile: Mapping[str, float],
    departures: Mapping[str, float],
) -> None:
    """Write the same ids/routes with explicit factors, sorted by departure."""
    try:
        tree = ET.parse(source_path)
    except (OSError, ET.ParseError) as error:
        raise StandardDriverPoolError("source route XML is unreadable") from error
    root = tree.getroot()
    vehicle_nodes = [child for child in root if child.tag == "vehicle"]
    expected = {node.get("id") for node in vehicle_nodes}
    if None in expected or len(expected) != len(vehicle_nodes):
        raise StandardDriverPoolError("source route ids are missing or duplicated")
    if set(profile) != expected or set(departures) != expected:
        raise StandardDriverPoolError(
            "profile/departure mappings do not cover the route exactly")
    nonvehicles = [child for child in root if child.tag != "vehicle"]
    for node in vehicle_nodes:
        vehicle_id = node.get("id")
        node.set("depart", f"{float(departures[vehicle_id]):.1f}")
        node.set("speedFactor", f"{float(profile[vehicle_id]):.6f}")
    vehicle_nodes.sort(
        key=lambda node: (float(node.get("depart")), str(node.get("id"))))
    root[:] = nonvehicles + vehicle_nodes
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="unicode")


def validate_profile_route(
    source: Sequence[passage.RouteVehicle],
    candidate_path: Path,
    profile: Mapping[str, float],
    departures: Mapping[str, float],
) -> None:
    """Prove population, ids and each vehicle's route are unchanged."""
    candidate = passage.read_route_vehicles(candidate_path)
    source_by_id = {vehicle.vehicle_id: vehicle for vehicle in source}
    candidate_by_id = {vehicle.vehicle_id: vehicle for vehicle in candidate}
    if set(source_by_id) != set(candidate_by_id):
        raise StandardDriverPoolError("profile route changed the vehicle set")
    for vehicle_id, before in source_by_id.items():
        after = candidate_by_id[vehicle_id]
        if after.edges != before.edges:
            raise StandardDriverPoolError(
                f"profile route changed edges for {vehicle_id}")
        if after.depart_s != round(float(departures[vehicle_id]), 1):
            raise StandardDriverPoolError(
                f"profile route changed departure for {vehicle_id}")
    factors: dict[str, float] = {}
    for _event, node in ET.iterparse(candidate_path, events=("end",)):
        if node.tag == "vehicle":
            try:
                factors[str(node.get("id"))] = float(node.get("speedFactor"))
            except (TypeError, ValueError) as error:
                raise StandardDriverPoolError(
                    "profile route has invalid speedFactor") from error
            node.clear()
    if factors != {key: float(value) for key, value in profile.items()}:
        raise StandardDriverPoolError("profile route changed driver factors")


def _departure_windows(
    original: Sequence[passage.RouteVehicle],
    offsets: Mapping[str, Sequence[float]],
    *,
    duration_s: int,
    guard_s: int,
) -> list[DepartureWindow]:
    if guard_s < 0 or guard_s * 2 >= passage.INTERVAL_S:
        raise StandardDriverPoolError("guard must be in [0, 450) seconds")
    windows = []
    for vehicle in original:
        preferred = round(vehicle.depart_s * TICK_SCALE)
        values = list(offsets.get(vehicle.vehicle_id, ()))
        if values:
            if any(not math.isfinite(float(value)) for value in values):
                raise StandardDriverPoolError("passage offset is non-finite")
            quarter = int(vehicle.depart_s // passage.INTERVAL_S)
            passage_low = max(
                quarter * passage.INTERVAL_S - float(value) for value in values)
            passage_high = min(
                (quarter + 1) * passage.INTERVAL_S - float(value)
                for value in values)
            # Sensor observations timestamp the later passage, not a hidden
            # trip origin. A route may therefore have to start in the previous
            # quarter. The full set of departure slots is preserved below;
            # only ownership of those slots changes.
            raw_low = max(0.0, passage_low + guard_s)
            raw_high = min(float(duration_s), passage_high - guard_s)
        else:
            # The picker constrains demand in 15-minute buckets, not a
            # particular unmeasured route to one tenth of a second. Let these
            # vehicles exchange the picker's existing slots inside their own
            # bucket; the global loading pattern and all quarter totals remain
            # exact, while fixed slots would needlessly block constrained
            # sensor vehicles.
            raw_low = max(0.0, vehicle.depart_s - passage.INTERVAL_S)
            raw_high = min(float(duration_s),
                           vehicle.depart_s + passage.INTERVAL_S)
        lower = math.ceil((raw_low - 1e-9) * TICK_SCALE)
        upper = math.floor((raw_high + 1e-9) * TICK_SCALE)
        if lower > upper:
            raise StandardDriverPoolError(
                f"vehicle {vehicle.vehicle_id} has no robust departure window")
        windows.append(DepartureWindow(
            vehicle.vehicle_id, preferred, lower, upper))
    return windows


def _schedule_with_gap(
    windows: Sequence[DepartureWindow],
    *,
    gap_ticks: int,
    preferred_slots: Sequence[int] | None = None,
) -> dict[str, float]:
    """Find an EDF-feasible order, then project times toward their originals."""
    pending = sorted(windows, key=lambda item: (item.lower, item.upper,
                                                item.vehicle_id))
    available: list[tuple[int, int, str, DepartureWindow]] = []
    order: list[DepartureWindow] = []
    cursor = pending[0].lower
    index = 0
    while index < len(pending) or available:
        if not available and index < len(pending) and cursor < pending[index].lower:
            cursor = pending[index].lower
        while index < len(pending) and pending[index].lower <= cursor:
            item = pending[index]
            heapq.heappush(
                available, (item.upper, item.preferred, item.vehicle_id, item))
            index += 1
        if not available:
            continue
        _upper, _preferred, _vehicle_id, item = heapq.heappop(available)
        if cursor > item.upper:
            raise StandardDriverPoolError("departure windows cannot meet spacing")
        order.append(item)
        cursor += gap_ticks

    earliest: list[int] = []
    for item in order:
        value = max(item.lower, earliest[-1] + gap_ticks) if earliest \
            else item.lower
        if value > item.upper:
            raise StandardDriverPoolError("departure schedule forward pass failed")
        earliest.append(value)
    latest = [0] * len(order)
    for index in range(len(order) - 1, -1, -1):
        latest[index] = min(
            order[index].upper,
            latest[index + 1] - gap_ticks
            if index + 1 < len(order) else order[index].upper,
        )
        if latest[index] < earliest[index]:
            raise StandardDriverPoolError("departure schedule backward pass failed")
    chosen: list[int] = []
    if preferred_slots is not None and len(preferred_slots) != len(order):
        raise StandardDriverPoolError("preferred departure-slot width differs")
    for index, item in enumerate(order):
        lower = max(earliest[index], chosen[-1] + gap_ticks) if chosen \
            else earliest[index]
        preferred = item.preferred if preferred_slots is None \
            else int(preferred_slots[index])
        value = min(max(preferred, lower), latest[index])
        if value < lower:
            raise StandardDriverPoolError("departure schedule projection failed")
        chosen.append(value)
    return {
        item.vehicle_id: chosen[index] / TICK_SCALE
        for index, item in enumerate(order)
    }


def derive_distributed_departures(
    original: Sequence[passage.RouteVehicle],
    offsets: Mapping[str, Sequence[float]],
    *,
    duration_s: int,
    guard_s: int = DEFAULT_GUARD_S,
) -> dict[str, float]:
    """Assign the picker's original time slots to feasible vehicles.

    Keeping the complete multiset of departure times is stronger than merely
    passing a median-gap gate: every within-day loading gap and every
    per-second departure count remains byte-for-byte numerically identical.
    Fixed speed factors make it safe to change which vehicle owns a slot.
    """
    windows = _departure_windows(
        original, offsets, duration_s=duration_s, guard_s=guard_s)
    source = passage.departure_spacing_summary(
        sorted(vehicle.depart_s for vehicle in original))
    slots = sorted(round(vehicle.depart_s * TICK_SCALE) for vehicle in original)
    pending = sorted(windows, key=lambda item: (
        item.lower, item.upper, item.preferred, item.vehicle_id))
    available: list[tuple[int, int, str, DepartureWindow]] = []
    best: dict[str, float] = {}
    index = 0
    slot_failure: str | None = None
    for slot in slots:
        while index < len(pending) and pending[index].lower <= slot:
            item = pending[index]
            heapq.heappush(
                available, (item.upper, item.preferred, item.vehicle_id, item))
            index += 1
        if not available:
            slot_failure = f"no eligible vehicle at {slot / TICK_SCALE:.1f}s"
            break
        if available[0][0] < slot:
            overdue = available[0][3]
            slot_failure = (
                f"{overdue.vehicle_id} expired at "
                f"{overdue.upper / TICK_SCALE:.1f}s before slot "
                f"{slot / TICK_SCALE:.1f}s")
            break
        _upper, _preferred, _vehicle_id, item = heapq.heappop(available)
        best[item.vehicle_id] = slot / TICK_SCALE
    if slot_failure is None and index == len(pending) and not available \
            and len(best) == len(windows):
        candidate_summary = passage.departure_spacing_summary(
            sorted(best.values()))
        if candidate_summary != source:
            raise StandardDriverPoolError(
                "departure-slot assignment changed the picker's loading pattern")
        passage.validate_departure_dispersion(source, candidate_summary)
        return best

    # The first observed interval has no previous-day warm-up in a one-day
    # file, so an early passage deadline can precede the next existing slot.
    # Retain the EDF-feasible vehicle order but project each position toward
    # the original global slot. Only deadlines can move a slot away from that
    # value, and the same anti-convoy gate still applies.
    source_minimum = float(source["minimum_gap_s"] or passage.MIN_DEPARTURE_GAP_S)
    source_median = float(source["median_gap_s"] or source_minimum)
    floor_ticks = math.ceil(max(
        passage.MIN_DEPARTURE_GAP_S,
        min(source_minimum, source_median * 0.5),
    ) * TICK_SCALE)
    start_ticks = max(floor_ticks, round(source_minimum * TICK_SCALE))
    for gap_ticks in range(start_ticks, floor_ticks - 1, -1):
        try:
            candidate = _schedule_with_gap(
                windows, gap_ticks=gap_ticks, preferred_slots=slots)
            candidate_summary = passage.departure_spacing_summary(
                sorted(candidate.values()))
            passage.validate_departure_dispersion(source, candidate_summary)
        except (StandardDriverPoolError,
                passage.DepartureReconciliationError):
            continue
        return candidate
    raise StandardDriverPoolError(
        "original departure slots and bounded minimal shifts both failed: "
        + (slot_failure or "incomplete slot assignment"))


def _write_agents(
    source_path: Path,
    output_path: Path,
    departures: Mapping[str, float],
) -> None:
    try:
        document = json.loads(source_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise StandardDriverPoolError("agent sidecar is unreadable") from error
    agents = document.get("agents") if isinstance(document, dict) else None
    if not isinstance(agents, list):
        raise StandardDriverPoolError("agent sidecar lacks agents")
    seen: set[str] = set()
    for agent in agents:
        vehicle_id = agent.get("vehicle_id") if isinstance(agent, dict) else None
        if vehicle_id not in departures or vehicle_id in seen:
            raise StandardDriverPoolError(
                "agent sidecar has unknown or duplicate vehicle id")
        agent["departure_s"] = round(float(departures[vehicle_id]), 1)
        seen.add(vehicle_id)
    if seen != set(departures):
        raise StandardDriverPoolError("agent sidecar is incomplete")
    output_path.write_text(json.dumps(document, separators=(",", ":")))


def _run_arm(
    *,
    arm: int,
    route_path: Path,
    run_dir: Path,
    net_path: Path,
    home: Path,
    duration_s: int,
    measured_edges: Sequence[str],
    expected_vehicles: int,
) -> tuple[dict[str, list[float]], dict[str, list[int]], str, float]:
    run_dir.mkdir()
    stats_path = run_dir / "stats.xml"
    edge_path = run_dir / "edge.xml"
    additional_path = run_dir / "edge.add.xml"
    vehroute_path = run_dir / "vehroute.xml"
    passage._write_edgedata_additional(
        additional_path, edge_path.name, measured_edges, duration_s)
    command = passage._sumo_command(
        sumo_home=home, net_path=net_path, route_path=route_path,
        seed=arm, duration_s=duration_s, stats_path=stats_path,
        additional_path=additional_path, vehroute_path=vehroute_path)
    started = time.perf_counter()
    passage._run_sumo(command, cwd=run_dir, sumo_home=home)
    wall_s = time.perf_counter() - started
    passage._validate_stats(stats_path, expected_vehicles)
    vehicles = passage.read_route_vehicles(route_path)
    offsets, digest = passage.parse_passage_evidence(
        vehroute_path, vehicles, set(measured_edges))
    entered = passage._parse_entered(
        edge_path, duration_s // passage.INTERVAL_S, measured_edges)
    return offsets, entered, digest, wall_s


def _is_exact(
    entered: Mapping[str, Sequence[int]],
    targets: Mapping[str, Sequence[float]],
) -> bool:
    return all(
        int(entered[edge_id][quarter]) == int(round(float(target)))
        for edge_id, values in targets.items()
        for quarter, target in enumerate(values)
    )


def _publish_manifest(directory: Path, payload: Mapping[str, Any]) -> None:
    records = {}
    for path in sorted(directory.iterdir()):
        if path.name == "manifest.json" or not path.is_file():
            continue
        records[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    document = dict(payload)
    document["artifacts"] = records
    (directory / "manifest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n")


def build_standard_driver_pool(
    route_path: Path,
    agent_path: Path,
    metadata_path: Path,
    net_path: Path,
    output_root: Path,
    *,
    arms: Sequence[int] = DEFAULT_ARMS,
    guard_s: int = DEFAULT_GUARD_S,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    home: Path | None = None,
) -> Path:
    """Construct and verify one isolated pool; return its final directory."""
    if max_iterations <= 0:
        raise StandardDriverPoolError("max_iterations must be positive")
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise StandardDriverPoolError("demand metadata is unreadable") from error
    if not isinstance(metadata, dict):
        raise StandardDriverPoolError("demand metadata must be an object")
    original = passage.read_route_vehicles(route_path)
    targets, duration_s = load_sensor_targets(metadata)
    passage.validate_route_targets(
        original, targets, n_intervals=duration_s // passage.INTERVAL_S)
    identity = pool_identity(
        route_path, metadata, arms, network_path=net_path, guard_s=guard_s)
    final_dir = Path(output_root) / identity["date"] / identity["pool_key"]
    if final_dir.exists():
        raise StandardDriverPoolError(
            f"standard pool already exists: {final_dir}")
    profiles = build_driver_profiles(
        original, pool_key=identity["profile_key"], arms=arms)
    profile_summaries = {
        str(arm): profile_summary(profile) for arm, profile in profiles.items()}
    for summary in profile_summaries.values():
        validate_profile_distribution(summary)
    departures = {vehicle.vehicle_id: vehicle.depart_s for vehicle in original}
    source_spacing = passage.departure_spacing_summary(
        sorted(departures.values()))
    all_iterations = []
    home = Path(home) if home is not None else Path(sumo_home())
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix=identity["pool_key"] + ".staging-",
            dir=str(final_dir.parent)) as raw_staging:
        staging = Path(raw_staging)
        accepted = False
        last_routes: dict[int, Path] = {}
        profile_digests: list[str] = []
        for iteration in range(1, max_iterations + 1):
            combined_offsets: dict[str, list[float]] = defaultdict(list)
            exact_arms = []
            walls = {}
            profile_digests = []
            last_routes = {}
            for arm in arms:
                route = staging / f"driver-arm-{arm}.rou.xml"
                write_profile_route(
                    route_path, route, profiles[arm], departures)
                validate_profile_route(
                    original, route, profiles[arm], departures)
                run_dir = staging / f"iteration-{iteration}-arm-{arm}"
                offsets, entered, digest, wall_s = _run_arm(
                    arm=arm, route_path=route, run_dir=run_dir,
                    net_path=net_path, home=home, duration_s=duration_s,
                    measured_edges=sorted(targets),
                    expected_vehicles=len(original))
                for vehicle_id, values in offsets.items():
                    combined_offsets[vehicle_id].extend(values)
                exact_arms.append(_is_exact(entered, targets))
                profile_digests.append(digest)
                walls[str(arm)] = round(wall_s, 3)
                last_routes[arm] = route
            if len(set(profile_digests)) != len(arms):
                raise StandardDriverPoolError(
                    "SUMO did not preserve distinct fixed driver profiles")
            all_iterations.append({
                "iteration": iteration,
                "exact_arms": {
                    str(arm): exact_arms[index]
                    for index, arm in enumerate(arms)
                },
                "sumo_wall_s": walls,
            })
            if all(exact_arms):
                accepted = True
                break
            updated = derive_distributed_departures(
                original, combined_offsets,
                duration_s=duration_s, guard_s=guard_s)
            if updated == departures:
                raise StandardDriverPoolError(
                    "standard-pool schedule stopped changing before exactness")
            departures = updated
        if not accepted:
            raise StandardDriverPoolError(
                f"standard pool was not exact after {max_iterations} iterations")

        candidate_spacing = passage.departure_spacing_summary(
            sorted(departures.values()))
        passage.validate_departure_dispersion(source_spacing, candidate_spacing)
        shifts = [
            float(departures[vehicle.vehicle_id]) - vehicle.depart_s
            for vehicle in original
        ]
        for arm, route in last_routes.items():
            target = staging / f"calibrated.driver-{arm}.rou.xml"
            os.replace(route, target)
            validate_profile_route(
                original, target, profiles[arm], departures)
        _write_agents(
            agent_path, staging / "calibrated.driver-pool.agents.json", departures)
        for child in list(staging.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "day_specific_standard_driver_pool",
            "status": "verified_exact_isolated",
            "identity": identity,
            "active_production": False,
            "vehicles": len(original),
            "vehicles_added": 0,
            "vehicles_removed": 0,
            "routes_changed": 0,
            "targets": len(targets) * (duration_s // passage.INTERVAL_S),
            "exact_targets_per_arm": len(targets) * (
                duration_s // passage.INTERVAL_S),
            "profile_summaries": profile_summaries,
            "profile_digests": profile_digests,
            "source_departure_spacing": source_spacing,
            "candidate_departure_spacing": candidate_spacing,
            "departure_adjustment": {
                "shifted_vehicles": sum(abs(value) >= 0.05 for value in shifts),
                "minimum_shift_s": round(min(shifts), 1),
                "maximum_shift_s": round(max(shifts), 1),
                "median_absolute_shift_s": round(
                    median(abs(value) for value in shifts), 1),
            },
            "iterations": all_iterations,
            "adoption_gate": {
                "status": "pending_paired_baseline_closure_latency",
                "minimum_trials": 10,
                "allowed_latency_regression_pct": 0.0,
            },
        }
        _publish_manifest(staging, manifest)
        os.replace(staging, final_dir)
    return final_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path,
                        default=Path("sumo/calibrated.rou.xml"))
    parser.add_argument("--agents", type=Path,
                        default=Path("sumo/calibrated.agents.json"))
    parser.add_argument("--metadata", type=Path,
                        default=Path("sumo/demand_meta.json"))
    parser.add_argument("--network", type=Path,
                        default=Path("sumo/net.net.xml"))
    parser.add_argument("--output-root", type=Path,
                        default=Path("runs/standard-driver-pools"))
    parser.add_argument("--arms", type=int, nargs="+", default=list(DEFAULT_ARMS))
    parser.add_argument("--guard-s", type=int, default=DEFAULT_GUARD_S)
    parser.add_argument("--max-iterations", type=int,
                        default=DEFAULT_MAX_ITERATIONS)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = build_standard_driver_pool(
        args.route, args.agents, args.metadata, args.network, args.output_root,
        arms=tuple(args.arms), guard_s=args.guard_s,
        max_iterations=args.max_iterations)
    print(result)


if __name__ == "__main__":
    main()
