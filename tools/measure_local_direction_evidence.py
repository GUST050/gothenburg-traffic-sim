"""Measure whether Gothenburg's OWN sensors carry usable intraday directional
signal for a two-way (TOT) sensor — and quantify the bias that makes the naive
use of that signal invalid.

Why this exists
---------------
The deployed direction split takes its NIVÅ (level) from local evidence —
sensor 107's published 2025 D-factor — but its intraday SHAPE from 81 Norwegian
stations, because no Gothenburg sensor measures a direction share per slot: a
TOT station delivers only the two-way total, and the five single-direction
stations each measure one carriageway.

The obvious objection is that the surrounding sensors ought to know something
about how the split moves during the day. They do. This tool measures exactly
how much, and it also measures the reason a neighbour's profile cannot simply be
read AS the split:

1. ``corridor_donors`` — for each TOT sensor with a verified
   ``directional_reference``, find single-direction sensors on the same street
   whose travel bearing aligns with one of the TOT sensor's directed edges.
   Report the per-slot ratio ``donor / tot_two_way_total``, its flow-weighted
   period mean, and the gap between that mean and the PUBLISHED share for the
   aligned direction. That gap is the *location bias*: the donor sits at a
   different point on the street, with intersections in between, so it carries
   different traffic. A method that wants to use the donor's shape must model
   this bias rather than ignore it.

2. ``node_conservation`` — where several directional sensors meet at one node,
   report the measured inflow over the measured outflow per slot. A closed node
   would pin the missing directions outright; an open one shows how much slack
   an inference would have to absorb, which is the same slack a solver could
   quietly hide error in.

Both are DIAGNOSTICS on tracked data, computed without preregistration:
``release_evidence`` is false and no gate may be decided from this file. Its
purpose is to size the opportunity and the obstacle before any method is built.

Usage
-----
    python3 -m tools.measure_local_direction_evidence
    python3 -m tools.measure_local_direction_evidence --out /dev/stdout
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dirsplit.geo import ang_diff_deg, bearing_deg, haversine_m
from traffic_sim.intake.sensors import DirectionalReference, load_registry

NETWORK_PATH = Path("web/data/network.geojson")
FLOWS_PATH = Path("web/data/flows.json")
REGISTRY_PATH = Path("data_in/sensors.json")
OUT_PATH = Path("validation/local_direction_evidence_v1.json")
SCHEMA_VERSION = "local_direction_evidence_v1"

#: A donor must sit on the same street within this distance of the TOT sensor.
#: 500 m keeps donors inside the same corridor block; beyond that the traffic
#: composition has almost certainly changed.
MAX_DONOR_DISTANCE_M = 500.0
#: ...and travel roughly parallel to the TOT edge it is a donor for.
MAX_DONOR_BEARING_DELTA_DEG = 30.0
#: Day types the profiles are averaged over. Weekend is reported separately
#: rather than averaged in, because the two have different direction structure.
DAY_TYPES = {"weekday": (0, 1, 2, 3, 4), "weekend": (5, 6)}


@dataclass(frozen=True)
class EdgeGeometry:
    """The published geometry of one directed edge."""

    edge_id: str
    street: str | None
    bearing_deg: float
    mid_lat: float
    mid_lon: float


def load_network(path: Path = NETWORK_PATH) -> dict[str, EdgeGeometry]:
    """Directed-edge geometry keyed by edge id, from the published network."""
    with open(path) as handle:
        geo = json.load(handle)
    out: dict[str, EdgeGeometry] = {}
    for feature in geo.get("features") or []:
        if (feature.get("geometry") or {}).get("type") != "LineString":
            continue
        properties = feature.get("properties") or {}
        edge_id = properties.get("id")
        coordinates = feature["geometry"]["coordinates"]
        if not edge_id or len(coordinates) < 2:
            continue
        first, last = coordinates[0], coordinates[-1]
        middle = coordinates[len(coordinates) // 2]
        out[str(edge_id)] = EdgeGeometry(
            edge_id=str(edge_id),
            street=properties.get("name"),
            # First->last is the direction of travel, the same convention
            # build_data.py snaps compass-labelled sensors with.
            bearing_deg=bearing_deg(first[1], first[0], last[1], last[0]),
            mid_lat=middle[1], mid_lon=middle[0])
    return out


def _street_key(street: Any) -> str | None:
    """Comparable street name. OSM sometimes delivers a list of names."""
    if isinstance(street, (list, tuple)):
        street = street[0] if street else None
    if not isinstance(street, str) or not street.strip():
        return None
    return street.strip().casefold()


def slot_profile(
    flows: Mapping[str, Sequence[float | None]],
    edge_ids: Iterable[str],
    *,
    epoch: datetime,
    interval_minutes: int,
    period_start: str,
    period_end_exclusive: str,
    weekdays: Sequence[int],
    slots_per_day: int = 96,
) -> tuple[list[float | None], list[int]]:
    """Mean measured volume per time-of-day slot, and the sample count per slot.

    ``None`` stays missing and is never counted as zero — a slot with no
    accepted observation returns ``None`` rather than 0.0, so a gap can never
    be mistaken for an empty street.

    A station delivered as a two-way ``Total`` carries the SAME value on both
    directed edges (CLAUDE.md's reporting trap). Identical series are therefore
    counted ONCE and genuinely differing series are summed, matching
    ``direction_anchor.period_slot_weights``.
    """
    series = [list(flows.get(edge_id) or []) for edge_id in edge_ids]
    series = [values for values in series if values]
    if not series:
        return [None] * slots_per_day, [0] * slots_per_day
    duplicate_total = len(series) > 1 and all(
        values == series[0] for values in series[1:])
    start = datetime.fromisoformat(period_start)
    end = datetime.fromisoformat(period_end_exclusive)
    allowed = set(weekdays)
    step = timedelta(minutes=interval_minutes)
    buckets: list[list[float]] = [[] for _ in range(slots_per_day)]
    for index in range(max(len(values) for values in series)):
        stamp = epoch + index * step
        if not start <= stamp < end or stamp.weekday() not in allowed:
            continue
        values = [float(v[index]) for v in series
                  if index < len(v) and v[index] is not None]
        if len(values) != len(series):
            continue          # a partially-missing slot is missing, not partial
        total = values[0] if duplicate_total else sum(values)
        buckets[index % slots_per_day].append(total)
    means = [statistics.fmean(bucket) if bucket else None for bucket in buckets]
    return means, [len(bucket) for bucket in buckets]


def hourly_from_slots(values: Sequence[float | None],
                      slots_per_hour: int) -> list[float | None]:
    """Collapse a per-slot profile to 24 hourly means, preserving missingness."""
    out: list[float | None] = []
    for hour in range(len(values) // slots_per_hour):
        window = [v for v in values[hour * slots_per_hour:
                                    (hour + 1) * slots_per_hour] if v is not None]
        out.append(statistics.fmean(window) if window else None)
    return out


def find_corridor_donors(
    registry: Any,
    network: Mapping[str, EdgeGeometry],
    tot_sensor_id: str,
    reference: DirectionalReference,
    *,
    max_distance_m: float = MAX_DONOR_DISTANCE_M,
    max_bearing_delta_deg: float = MAX_DONOR_BEARING_DELTA_DEG,
) -> list[dict[str, Any]]:
    """Single-direction sensors on the same street, aligned with a TOT edge.

    Discovery is entirely data-driven — street name, published geometry and the
    registry's own semantics — so a future TOT sensor gains donors without any
    code change, as the daily-split plan requires.
    """
    donors: list[dict[str, Any]] = []
    tot_edges = [network[edge] for edge in reference.edge_ids if edge in network]
    tot_streets = {_street_key(edge.street) for edge in tot_edges} - {None}
    for sensor_id in sorted(registry.records):
        if sensor_id == tot_sensor_id:
            continue
        record = registry[sensor_id]
        if record.measurement_semantics != "directional":
            # Only a directional delivery is a directional observation. A
            # two-way total tells us nothing about one carriageway.
            continue
        for donor_edge_id in record.approved_edge_ids:
            donor = network.get(donor_edge_id)
            if donor is None or _street_key(donor.street) not in tot_streets:
                continue
            for tot_edge in tot_edges:
                delta = ang_diff_deg(donor.bearing_deg, tot_edge.bearing_deg)
                distance = haversine_m(donor.mid_lat, donor.mid_lon,
                                       tot_edge.mid_lat, tot_edge.mid_lon)
                if delta > max_bearing_delta_deg or distance > max_distance_m:
                    continue
                donors.append({
                    "donor_sensor_id": sensor_id,
                    "donor_edge_id": donor_edge_id,
                    "aligned_tot_edge_id": tot_edge.edge_id,
                    "street": donor.street,
                    "bearing_delta_deg": round(delta, 1),
                    "distance_m": round(distance, 1),
                    "donor_bearing_deg": round(donor.bearing_deg, 1),
                    "aligned_tot_bearing_deg": round(tot_edge.bearing_deg, 1),
                })
    return donors


def measure_donor(
    donor: Mapping[str, Any],
    reference: DirectionalReference,
    flows: Mapping[str, Sequence[float | None]],
    *,
    epoch: datetime,
    interval_minutes: int,
    day_type: str,
    weekdays: Sequence[int],
    slots_per_day: int = 96,
) -> dict[str, Any]:
    """The donor's ratio profile against the TOT total, and its location bias."""
    total, total_n = slot_profile(
        flows, reference.edge_ids, epoch=epoch,
        interval_minutes=interval_minutes,
        period_start=reference.period_start,
        period_end_exclusive=reference.period_end_exclusive,
        weekdays=weekdays, slots_per_day=slots_per_day)
    donor_profile, donor_n = slot_profile(
        flows, [donor["donor_edge_id"]], epoch=epoch,
        interval_minutes=interval_minutes,
        period_start=reference.period_start,
        period_end_exclusive=reference.period_end_exclusive,
        weekdays=weekdays, slots_per_day=slots_per_day)

    ratio: list[float | None] = []
    donor_mass = total_mass = 0.0
    for donor_value, total_value in zip(donor_profile, total):
        if donor_value is None or total_value is None or total_value <= 0:
            ratio.append(None)
            continue
        ratio.append(donor_value / total_value)
        donor_mass += donor_value
        total_mass += total_value

    present = [value for value in ratio if value is not None]
    published = reference.share(donor["aligned_tot_edge_id"])
    flow_weighted = donor_mass / total_mass if total_mass > 0 else None
    hourly = hourly_from_slots(ratio, max(1, slots_per_day // 24))
    hourly_present = [value for value in hourly if value is not None]
    result: dict[str, Any] = {
        **donor,
        "day_type": day_type,
        "slots_with_ratio": len(present),
        "observations_per_slot_median": (
            statistics.median(n for n in donor_n if n) if any(donor_n) else 0),
        "published_share_of_aligned_direction": round(published, 6),
        "flow_weighted_ratio_mean": (
            None if flow_weighted is None else round(flow_weighted, 6)),
        "hourly_ratio": [None if v is None else round(v, 4) for v in hourly],
    }
    if flow_weighted is not None:
        # The headline obstacle: how far the donor's own level sits from the
        # share it would have to equal if it really measured this direction.
        result["location_bias_pp"] = round((flow_weighted - published) * 100, 2)
        result["level_is_usable_as_share"] = bool(
            abs(flow_weighted - published) <= reference.tolerance)
    if hourly_present:
        span = max(hourly_present) - min(hourly_present)
        result["hourly_ratio_span_pp"] = round(span * 100, 2)
        result["hourly_argmax"] = hourly.index(max(hourly_present))
        result["hourly_argmin"] = hourly.index(min(hourly_present))
        if flow_weighted:
            # Shape with the level divided out — the part a method could
            # legitimately borrow once the bias above is modelled.
            result["normalised_hourly_shape"] = [
                None if v is None else round(v / flow_weighted, 4) for v in hourly]
    return result


def _node_of(edge_id: str) -> tuple[str, str] | None:
    """(tail, head) node ids of a ``u_v_k`` directed edge id."""
    parts = edge_id.split("_")
    if len(parts) < 3:
        return None
    return parts[0], parts[1]


def measure_node_conservation(
    registry: Any,
    flows: Mapping[str, Sequence[float | None]],
    *,
    epoch: datetime,
    interval_minutes: int,
    period_start: str,
    period_end_exclusive: str,
    day_type: str,
    weekdays: Sequence[int],
    slots_per_day: int = 96,
) -> list[dict[str, Any]]:
    """Measured inflow over measured outflow at nodes several sensors share.

    Only ``directional`` deliveries take part: a two-way total is not a
    directional flow and would corrupt the balance.
    """
    inflow: dict[str, list[str]] = {}
    outflow: dict[str, list[str]] = {}
    for sensor_id in sorted(registry.records):
        record = registry[sensor_id]
        if record.measurement_semantics != "directional":
            continue
        for edge_id in record.approved_edge_ids:
            nodes = _node_of(edge_id)
            if nodes is None:
                continue
            tail, head = nodes
            outflow.setdefault(tail, []).append(edge_id)
            inflow.setdefault(head, []).append(edge_id)

    results: list[dict[str, Any]] = []
    for node in sorted(set(inflow) & set(outflow)):
        ins, outs = sorted(inflow[node]), sorted(outflow[node])
        in_profile, _ = slot_profile(
            flows, ins, epoch=epoch, interval_minutes=interval_minutes,
            period_start=period_start, period_end_exclusive=period_end_exclusive,
            weekdays=weekdays, slots_per_day=slots_per_day)
        out_profile, _ = slot_profile(
            flows, outs, epoch=epoch, interval_minutes=interval_minutes,
            period_start=period_start, period_end_exclusive=period_end_exclusive,
            weekdays=weekdays, slots_per_day=slots_per_day)
        ratio: list[float | None] = []
        in_mass = out_mass = 0.0
        for a, b in zip(in_profile, out_profile):
            if a is None or b is None or b <= 0:
                ratio.append(None)
                continue
            ratio.append(a / b)
            in_mass += a
            out_mass += b
        present = [value for value in ratio if value is not None]
        hourly = hourly_from_slots(ratio, max(1, slots_per_day // 24))
        hourly_present = [value for value in hourly if value is not None]
        entry: dict[str, Any] = {
            "node_id": node,
            "day_type": day_type,
            "measured_inflow_edges": ins,
            "measured_outflow_edges": outs,
            "slots_with_ratio": len(present),
            "flow_weighted_in_over_out": (
                round(in_mass / out_mass, 6) if out_mass > 0 else None),
            "hourly_in_over_out": [
                None if v is None else round(v, 4) for v in hourly],
        }
        if hourly_present:
            entry["hourly_span_pp"] = round(
                (max(hourly_present) - min(hourly_present)) * 100, 2)
            entry["hourly_argmin"] = hourly.index(min(hourly_present))
            entry["hourly_argmax"] = hourly.index(max(hourly_present))
        if out_mass > 0:
            # 1.0 would be a closed node whose unmeasured legs carry nothing.
            entry["unmeasured_share_of_outflow"] = round(
                1.0 - in_mass / out_mass, 6)
        results.append(entry)
    return results


def measure(network_path: Path = NETWORK_PATH, flows_path: Path = FLOWS_PATH,
            registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """The whole diagnostic, as a serialisable record."""
    from traffic_sim.intake.direction_anchor import load_measurements

    network = load_network(network_path)
    registry = load_registry(registry_path)
    flows, epoch, interval_minutes = load_measurements(flows_path)
    references = registry.directional_references()

    donors: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for sensor_id, reference in sorted(references.items()):
        found = find_corridor_donors(registry, network, sensor_id, reference)
        for donor in found:
            for day_type, weekdays in sorted(DAY_TYPES.items()):
                donors.append(measure_donor(
                    donor, reference, flows, epoch=epoch,
                    interval_minutes=interval_minutes,
                    day_type=day_type, weekdays=weekdays))

    # Node conservation is not tied to one reference; use the widest declared
    # period available so the balance is measured over the same year.
    if references:
        span = sorted(references.values(),
                      key=lambda r: (r.period_start, r.period_end_exclusive))
        period_start = span[0].period_start
        period_end = span[-1].period_end_exclusive
        for day_type, weekdays in sorted(DAY_TYPES.items()):
            nodes.extend(measure_node_conservation(
                registry, flows, epoch=epoch,
                interval_minutes=interval_minutes,
                period_start=period_start, period_end_exclusive=period_end,
                day_type=day_type, weekdays=weekdays))

    return {
        "schema_version": SCHEMA_VERSION,
        "measured_at": datetime.now().date().isoformat(),
        "release_evidence": False,
        "question": ("Do Gothenburg's own sensors carry intraday directional "
                     "signal usable for a TOT sensor's split, and how large is "
                     "the location bias that stops a neighbour ratio from being "
                     "read as the split directly?"),
        "inputs": {
            "network": str(network_path),
            "flows": str(flows_path),
            "registry": str(registry_path),
            "epoch": epoch.isoformat(),
            "interval_minutes": interval_minutes,
        },
        "discovery": {
            "max_donor_distance_m": MAX_DONOR_DISTANCE_M,
            "max_donor_bearing_delta_deg": MAX_DONOR_BEARING_DELTA_DEG,
            "note": ("Donors are discovered from street name, published "
                     "geometry and registry semantics — no sensor id is "
                     "hardcoded, so new TOT sensors are covered automatically."),
        },
        "corridor_donors": donors,
        "node_conservation": nodes,
        "limitations": [
            "Diagnostic only: not preregistered, so no gate may be decided "
            "from this file.",
            "A donor ratio is NOT a direction share. The donor sits at another "
            "point on the street with intersections in between; "
            "location_bias_pp measures exactly how far its level is from the "
            "published share it would have to equal.",
            "An open node cannot pin the unmeasured directions. "
            "unmeasured_share_of_outflow is the slack any network inference "
            "would have to absorb — and the slack a solver could hide error in.",
            "Missing slots stay missing. A slot is skipped unless every edge in "
            "the group reported, so partial sums never enter a ratio.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--network", type=Path, default=NETWORK_PATH)
    parser.add_argument("--flows", type=Path, default=FLOWS_PATH)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)

    record = measure(args.network, args.flows, args.registry)

    for donor in record["corridor_donors"]:
        if donor["day_type"] != "weekday":
            continue
        print(f"donor {donor['donor_sensor_id']} ({donor['street']}) "
              f"-> TOT edge {donor['aligned_tot_edge_id']}")
        print(f"  {donor['distance_m']} m away, bearing delta "
              f"{donor['bearing_delta_deg']} deg")
        print(f"  flow-weighted ratio {donor.get('flow_weighted_ratio_mean')} "
              f"vs published share "
              f"{donor['published_share_of_aligned_direction']}")
        print(f"  location bias {donor.get('location_bias_pp')} pp, "
              f"usable as share: {donor.get('level_is_usable_as_share')}")
        print(f"  intraday span {donor.get('hourly_ratio_span_pp')} pp "
              f"(min kl {donor.get('hourly_argmin')}, "
              f"max kl {donor.get('hourly_argmax')})")
    for node in record["node_conservation"]:
        if node["day_type"] != "weekday":
            continue
        print(f"node {node['node_id']}: in/out "
              f"{node.get('flow_weighted_in_over_out')}, unmeasured share of "
              f"outflow {node.get('unmeasured_share_of_outflow')}, "
              f"intraday span {node.get('hourly_span_pp')} pp")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
