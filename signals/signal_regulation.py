"""
signal_regulation.py — IMPROVEMENT_PLAN.md Phase D6 (partial): a synthetic
signal-timing baseline informed by Sweden's binding national regulation
for traffic signals — Transportstyrelsens föreskrifter TSFS 2014:30,
"Transportstyrelsens föreskrifter och allmänna råd om trafiksignaler" —
instead of netconvert --tls.guess's arbitrary uniform 90 s-cycle guess.

NOT Gothenburg's real signal plans. D6's original ask (signal-object <->
intersection <-> SUMO TLS-ID mapping, phase diagrams, cycle/green/offset
per time-of-day plan, detector logic, bus priority) genuinely requires the
city (researched 2026-07-11: checked data.goteborg.se, goteborg.se/psidata,
and the Teknisk Handbok — no public dataset of operational signal PROGRAMS
exists; Trafikverket's NVDB covers road infrastructure, not municipal
signal control, confirming this needs a direct request via Miroslaw, not
something obtainable by more web research). This module fixes something
different and useful: a conservative synthetic timing template derived from
selected TSFS minimums using data already in this repository. It does not
establish compliance for a physical junction because it lacks a verified
movement-conflict matrix and city controller data. tls_provenance is the
NEW value "synthetic_tsfs_informed" — more grounded than
"synthetic", still not "city-configured".

SOURCES (every number below is directly cited, not invented) — TSFS
2014:30, https://www.transportstyrelsen.se/tsfs/TSFS%202014_30.pdf (read
in full, all 14 pages, 2026-07-11):
  - kap 2 § 11: red+yellow ("röd+gul") shown 1.5 s before every green phase
    (vehicle and cycle signals).
  - kap 2 § 12: yellow shown 4 s where the speed limit is <60 km/h, 5 s
    where >=60 km/h.
  - kap 2 § 14: green shown >=4 s (vehicle signals).
  - kap 2 §§ 2-10 ("Separering i tid"): the inter-green clearance time must
    satisfy t_s = (s_out + l_f)/v_out - s_in/v_in > 0, using the
    standardized speed table in § 9 (vehicles: 8/10/12/14/15 m/s for
    posted limits 30/40/50/60/70 km/h) and the default vehicle length in
    § 10 (6 m).

MEASURED against our own deployed net.net.xml before writing this
(2026-07-11): yellow phases were only ever 3.0 s or 5.0 s — the 3.0 s ones
violate the >=4 s minimum for any approach under 60 km/h, which is nearly
every inner-city street; only 3 of the network's ~330 phases across 57
real TLS junctions have ANY red-red clearance interval at all, and there
is no red+yellow transition anywhere. netconvert's guess is not merely
unlabelled, it is measurably non-compliant with the law every real Swedish
signal must follow.

DELIBERATE SIMPLIFICATIONS (engineering judgement, kept separate from the
citations above so a future reader can tell law from choice):
  - s_in (the entering direction's own distance-to-conflict-point) is
    taken as 0 rather than measured per movement pair. This can only ever
    make the computed clearance LARGER (more conservative), never smaller
    — it drops a term that would otherwise REDUCE t_s.
  - s_out (the clearing direction's distance to the conflict point) uses
    the REAL internal-junction lane length SUMO already computed for that
    connection (net.net.xml's own internal <lane length=...>), taking the
    MAX among the phase's clearing links — a real, not fabricated,
    distance, conservatively worst-cased across simultaneous movements
    rather than resolved per specific conflicting pair (which would need
    the junction's own foes/request bitstring — out of scope here).
  - v_out uses the MIN (slowest, most conservative) TSFS speed-bucket
    value among the phase's clearing links.
  - Sequencing choice (not itself dictated by a single TSFS paragraph,
    since §§2-10's clearance and §11's red+yellow are two separate
    requirements): the computed all-red interval is shown FIRST, then the
    1.5 s red+yellow warm-up for the entering direction is appended after
    it — this satisfies both requirements independently rather than
    assuming one covers the other.
  - TSFS's speed table (§9) only covers 30-70 km/h; edges below/above that
    range are clamped to the table's own end values (8 / 15 m/s) rather
    than extrapolated, since the regulation defines no tier outside it.
  - Any link that is already green (G/g) going into a yellow-phase
    transition and stays green in the following phase (an unrelated,
    continuously-flowing movement at a junction with more than two
    alternating groups) is left green through the inserted all-red/
    red+yellow phases rather than forced red — those movements were never
    part of the conflict this clearance interval protects against.

Usage:
  python3 signal_regulation.py [--out PATH]

Reads sumo/net.net.xml (build_sumo_net.py). Writes a content-addressed
<additional> file (programID="reg" per real TLS) — content-addressed by
net_fingerprint alone, since this transform depends only on network
geometry, never on demand/window. Loading it as run_sumo's sole add_path
makes SUMO activate "reg" (SUMO's own "last-loaded program wins" behaviour
for a given TLS id, verified via TraCI in D2) exactly the same way D2's
tlsCycleAdaptation output already does — no new activation machinery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__ in (None, ""):  # support `python3 signals/<name>.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scenario as rs
from signals.signal_lab import net_fingerprint

MIN_GREEN_S = 4.0             # TSFS 2014:30 kap 2 § 14
REDYELLOW_S = 1.5             # TSFS 2014:30 kap 2 § 11
DEFAULT_VEHICLE_LENGTH_M = 6.0  # TSFS 2014:30 kap 2 § 10

# TSFS 2014:30 kap 2 § 9 — standardized speed values (m/s) for the
# separering-i-tid formula, keyed by posted speed limit (km/h).
_SPEED_TABLE_KMH_MS = [(30, 8.0), (40, 10.0), (50, 12.0), (60, 14.0), (70, 15.0)]


def speed_bucket_ms(speed_kmh: float) -> float:
    """TSFS 2014:30 § 9's standardized speed table, nearest tier. Clamped
    to the table's own 30-70 km/h range (see module docstring)."""
    if speed_kmh <= _SPEED_TABLE_KMH_MS[0][0]:
        return _SPEED_TABLE_KMH_MS[0][1]
    if speed_kmh >= _SPEED_TABLE_KMH_MS[-1][0]:
        return _SPEED_TABLE_KMH_MS[-1][1]
    return min(_SPEED_TABLE_KMH_MS, key=lambda t: abs(t[0] - speed_kmh))[1]


def yellow_time_s(speed_kmh: float) -> float:
    """TSFS 2014:30 kap 2 § 12: 4 s if speed limit <60 km/h, 5 s if >=60."""
    return 5.0 if speed_kmh >= 60 else 4.0


def parse_tls_link_geometry(net_path: Path) -> dict[str, dict[int, dict]]:
    """tls_id -> link geometry keyed by SUMO linkIndex.

    ``speed_kmh`` is the minimum speed among grouped connections and remains
    the conservative clearance-speed input. ``yellow_speed_kmh`` is the
    maximum: a shared yellow indication must be long enough for the fastest
    approach it serves. ``clear_dist_m`` is the maximum internal-lane
    distance. Keeping both speed extrema avoids incorrectly using the
    clearance-safe minimum for the distinct statutory yellow minimum.

    Values are built from net.net.xml's real <connection tl=...> elements:
    speed comes from the approach edge's own lane and distance from SUMO's
    internal-junction lane. Multiple connections sharing a linkIndex are
    collapsed conservatively for their respective calculations.
    """
    root = ET.parse(net_path).getroot()

    edge_speed_ms: dict[str, float] = {}
    internal_lane_length_m: dict[str, float] = {}
    for e in root.findall("edge"):
        lanes = e.findall("lane")
        if e.get("function") == "internal":
            for l in lanes:
                internal_lane_length_m[l.get("id")] = float(l.get("length", 0))
        elif lanes:
            edge_speed_ms[e.get("id")] = max(float(l.get("speed", 0)) for l in lanes)

    tls_links: dict[str, dict[int, dict]] = {}
    for c in root.findall("connection"):
        tl = c.get("tl")
        if not tl:
            continue
        idx = int(c.get("linkIndex"))
        speed_ms = edge_speed_ms.get(c.get("from"))
        speed_kmh = speed_ms * 3.6 if speed_ms is not None else 50.0
        via = c.get("via")
        dist_m = internal_lane_length_m.get(via, 0.0) if via else 0.0
        bucket = tls_links.setdefault(tl, {}).setdefault(
            idx, {"speed_kmh": [], "clear_dist_m": []})
        bucket["speed_kmh"].append(speed_kmh)
        bucket["clear_dist_m"].append(dist_m)

    for links in tls_links.values():
        for agg in links.values():
            speeds = agg["speed_kmh"]
            agg["speed_kmh"] = min(speeds)
            agg["yellow_speed_kmh"] = max(speeds)
            agg["clear_dist_m"] = max(agg["clear_dist_m"])
    return tls_links


def rebuild_phases(phases: list[tuple[float, str]],
                   link_geo: dict[int, dict]) -> list[tuple[float, str]]:
    """Apply TSFS 2014:30's minimums/formula to one tlLogic's phase list
    (list of (duration_s, state_str) in netconvert's original order).
    Green phases are floored to MIN_GREEN_S; yellow phases are resized per
    § 12; an all-red clearance phase (sized via the § 2-10 formula) plus a
    REDYELLOW_S red+yellow warm-up (§ 11) are inserted before every
    transition into a green phase. See module docstring for the exact
    simplifications this makes when real per-movement conflict geometry
    isn't cheaply available."""
    n = len(phases)
    n_links = len(phases[0][1]) if phases else 0
    out: list[tuple[float, str]] = []
    for i, (dur, state) in enumerate(phases):
        # 'y' checked first: a phase with ANY yellow character is a
        # clearing phase needing yellow-time/clearance handling for that
        # link, even if some OTHER link in the same phase is already (and
        # stays) green -- a legitimate SUMO construct for asymmetric ring
        # designs, not just this network's simple all-green/all-yellow
        # phases. Only a phase with no 'y' anywhere is a pure green phase.
        is_yellow = "y" in state
        is_green = (not is_yellow) and any(c in "Gg" for c in state)
        if is_green:
            out.append((max(dur, MIN_GREEN_S), state))
            continue
        if not is_yellow:
            # Not a plain green/yellow alternation phase (shouldn't occur
            # for netconvert --tls.guess's own output) -- pass through
            # unchanged rather than silently dropping it.
            out.append((dur, state))
            continue

        clearing_idxs = [j for j, c in enumerate(state) if c == "y"]
        clearance_speeds = [link_geo[j]["speed_kmh"]
                            for j in clearing_idxs if j in link_geo]
        yellow_speeds = [link_geo[j].get("yellow_speed_kmh", link_geo[j]["speed_kmh"])
                         for j in clearing_idxs if j in link_geo]
        clearance_kmh = min(clearance_speeds) if clearance_speeds else 50.0
        yellow_kmh = max(yellow_speeds) if yellow_speeds else 50.0
        out.append((yellow_time_s(yellow_kmh), state))

        nxt_state = phases[(i + 1) % n][1]
        newly_green = [j for j, c in enumerate(nxt_state)
                      if c in "Gg" and state[j] not in "Gg"]
        if not newly_green:
            continue

        dists = [link_geo[j]["clear_dist_m"] for j in clearing_idxs if j in link_geo]
        s_out = max(dists) if dists else 0.0
        v_out = speed_bucket_ms(clearance_kmh)
        t_s = max(0.0, (s_out + DEFAULT_VEHICLE_LENGTH_M) / v_out)
        allred_s = round(max(0.0, t_s - REDYELLOW_S), 1)

        if allred_s > 0:
            allred_state = "".join(
                state[j] if state[j] in "Gg" else "r" for j in range(n_links))
            out.append((allred_s, allred_state))
        redyellow_state = "".join(
            "u" if j in newly_green else (state[j] if state[j] in "Gg" else "r")
            for j in range(n_links))
        out.append((REDYELLOW_S, redyellow_state))
    return out


def build_regulation_compliant_tls(net_path: Path, out_path: Path) -> int:
    """Write a programID="reg" <additional> file with a TSFS 2014:30-
    TSFS-informed synthetic phase list for every real <tlLogic> in net_path. Returns the
    number of TLS programs written."""
    root = ET.parse(net_path).getroot()
    link_geo = parse_tls_link_geometry(net_path)

    out_root = ET.Element("additional")
    n = 0
    for tl in root.findall("tlLogic"):
        tl_id = tl.get("id")
        phases = [(float(p.get("duration")), p.get("state")) for p in tl.findall("phase")]
        if not phases:
            continue
        new_phases = rebuild_phases(phases, link_geo.get(tl_id, {}))
        out_tl = ET.SubElement(out_root, "tlLogic", {
            "id": tl_id, "type": "static", "programID": "reg", "offset": "0",
        })
        for dur, state in new_phases:
            ET.SubElement(out_tl, "phase", {"duration": str(dur), "state": state})
        n += 1

    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return n


def enforce_timing_minimums(net_path: Path, proposed_path: Path,
                            out_path: Path) -> int:
    """Apply the conservative TSFS timing envelope to an optimizer proposal.

    ``tlsCycleAdaptation.py`` is free to optimize green splits and cycle
    lengths, but its output is not a safety proof. This preserves its green
    allocations where they exceed the legal minimum, derives red implicitly
    from the complete phase sequence, and replaces unsafe yellow/intergreen/
    red-yellow transitions using ``rebuild_phases``. The program ID is kept
    unchanged so ``tlsCoordinator.py`` can subsequently optimize offsets for
    the exact same program.

    This remains a synthetic safety envelope, not a certified physical signal
    plan: real conflict matrices, pedestrians, cyclists, transit priority,
    and controller logic are unavailable.
    """
    proposed_root = ET.parse(proposed_path).getroot()
    link_geo = parse_tls_link_geometry(net_path)
    out_root = ET.Element("additional")
    count = 0
    for tl in proposed_root.findall("tlLogic"):
        phases = [(float(p.get("duration")), p.get("state"))
                  for p in tl.findall("phase")]
        if not phases:
            continue
        attrs = dict(tl.attrib)
        attrs["type"] = "static"
        attrs.setdefault("programID", "a")
        out_tl = ET.SubElement(out_root, "tlLogic", attrs)
        for duration, state in rebuild_phases(phases, link_geo.get(tl.get("id"), {})):
            ET.SubElement(out_tl, "phase", {"duration": str(duration), "state": state})
        count += 1
    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return count


def _effective_tls_programs(net_path: Path, plan_path: Path,
                            overlay_paths: tuple[Path, ...] | list[Path] = ()) \
        -> tuple[dict[str, ET.Element], dict[str, dict[str, str]]]:
    """Resolve the active TLS program after ordered SUMO additional files.

    ``tlsCoordinator.py`` emits offset-only ``tlLogic`` entries, while cycle
    adaptation emits complete phase programs.  Treating either file by itself
    as "the plan" loses the actual simulation input.  This small resolver
    follows SUMO's load order at the program level: phase entries replace a
    program, offset-only entries update that program, and the last loaded
    program for a TLS becomes active.
    """
    programs: dict[tuple[str, str], ET.Element] = {}
    sources: dict[tuple[str, str], dict[str, str]] = {}
    active: dict[str, tuple[str, str]] = {}

    def apply(path: Path) -> None:
        root = ET.parse(path).getroot()
        for tls in root.findall("tlLogic"):
            tls_id = tls.get("id")
            if not tls_id:
                continue
            program_id = tls.get("programID", "0")
            key = (tls_id, program_id)
            phases = tls.findall("phase")
            current = programs.get(key)
            if phases or current is None:
                current = ET.fromstring(ET.tostring(tls, encoding="utf-8"))
                programs[key] = current
                sources.setdefault(key, {})["phase_source"] = str(path)
            elif tls.get("offset") is not None:
                # Coordinator outputs carry no phase children. Preserve the
                # active phase program and apply only its offset attribute.
                current.set("offset", tls.get("offset"))
            if tls.get("offset") is not None:
                sources.setdefault(key, {})["offset_source"] = str(path)
            active[tls_id] = key

    apply(net_path)
    net_resolved = net_path.resolve()
    seen = {net_resolved}
    for raw_path in [plan_path, *overlay_paths]:
        path = Path(raw_path)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        apply(path)

    return ({tls_id: programs[key] for tls_id, key in active.items()},
            {tls_id: dict(sources.get(key, {})) for tls_id, key in active.items()})


def _plan_file_records(plan_path: Path,
                       overlay_paths: tuple[Path, ...] | list[Path]) -> tuple[list[dict], str]:
    """Stable ordered file evidence for a composite SignalPlan."""
    paths = [Path(plan_path), *(Path(path) for path in overlay_paths)]
    records = [{"path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in paths]
    digest = hashlib.sha256(json.dumps(
        [record["sha256"] for record in records], separators=(",", ":")
    ).encode()).hexdigest()
    return records, digest


def timing_certificate(net_path: Path, plan_path: Path, *,
                       overlay_paths: tuple[Path, ...] | list[Path] = (),
                       provenance: str = "synthetic_tsfs_informed") -> dict:
    """Return a machine-checkable safety envelope for one signal plan.

    This is deliberately a *synthetic timing* certificate, not an approval of
    a physical controller: SUMO's conflict matrix, pedestrian phases and
    municipal controller logic are outside the available inputs.  It proves
    the invariants this module actually enforces and fails closed when a plan
    is incomplete or contains malformed states.
    """
    baseline_root = ET.parse(net_path).getroot()
    baseline_ids = {tl.get("id") for tl in baseline_root.findall("tlLogic")}
    plans, _sources = _effective_tls_programs(net_path, plan_path, overlay_paths)
    errors: list[str] = []
    if not baseline_ids:
        errors.append("network saknar tlLogic")
    missing = sorted(tl for tl in baseline_ids if tl not in plans)
    if missing:
        errors.append(f"signalplan saknar {len(missing)} signaler")
    checked = 0
    all_geometry = parse_tls_link_geometry(net_path)
    for tls_id in sorted(baseline_ids & set(plans)):
        phases = plans[tls_id].findall("phase")
        if not phases:
            errors.append(f"{tls_id}: faslista saknas")
            continue
        widths = {len(phase.get("state", "")) for phase in phases}
        if not widths or 0 in widths or len(widths) != 1:
            errors.append(f"{tls_id}: signalsträngarnas längd är inkonsekvent")
            continue
        geometry = all_geometry.get(tls_id, {})
        for index, phase in enumerate(phases):
            state = phase.get("state", "")
            try:
                duration = float(phase.get("duration", "nan"))
            except (TypeError, ValueError):
                duration = float("nan")
            if not math.isfinite(duration) or duration <= 0:
                errors.append(f"{tls_id}: fas {index} har ogiltig längd")
                continue
            if any(signal in "Gg" for signal in state) and "y" not in state:
                if duration + 1e-9 < MIN_GREEN_S:
                    errors.append(f"{tls_id}: grön fas {index} under {MIN_GREEN_S}s")
            if "y" in state:
                required = yellow_time_s(max(
                    [geometry[j].get("yellow_speed_kmh", geometry[j]["speed_kmh"])
                     for j, signal in enumerate(state)
                     if signal == "y" and j in geometry] or [50.0]))
                if duration + 1e-9 < required:
                    errors.append(f"{tls_id}: gul fas {index} under {required:g}s")
            if "u" in state and duration + 1e-9 < REDYELLOW_S:
                errors.append(f"{tls_id}: röd+gul fas {index} under {REDYELLOW_S:g}s")
        checked += 1
    return {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "provenance": provenance,
        "plan_file": str(plan_path),
        "plan_files": _plan_file_records(plan_path, overlay_paths)[0],
        "signals_expected": len(baseline_ids),
        "signals_checked": checked,
        "errors": errors,
        "limitations": [
            "syntetisk plan; fysisk konfliktmatris, gång/cykel och kommunal controller saknas",
            "TSFS-informerade tidsminima är inte ett myndighetsgodkännande",
        ],
    }


def write_signal_plan_artifact(net_path: Path, plan_path: Path,
                               out_path: Path, *,
                               overlay_paths: tuple[Path, ...] | list[Path] = (),
                               provenance: str = "synthetic_tsfs_informed") -> dict:
    """Export the effective phase/link structure as a versioned SignalPlan."""
    network_sha = hashlib.sha256(net_path.read_bytes()).hexdigest()
    plan_files, plan_sha = _plan_file_records(plan_path, overlay_paths)
    root = ET.parse(net_path).getroot()
    effective_plans, plan_sources = _effective_tls_programs(
        net_path, plan_path, overlay_paths)
    links: dict[str, dict[int, dict]] = {}
    for connection in root.findall("connection"):
        tls_id, raw_index = connection.get("tl"), connection.get("linkIndex")
        if not tls_id or raw_index is None:
            continue
        index = int(raw_index)
        links.setdefault(tls_id, {})[index] = {
            "from_edge": connection.get("from"),
            "to_edge": connection.get("to"),
            "via": connection.get("via"),
        }
    controllers = []
    for tls_id, tls in sorted(effective_plans.items()):
        phases = []
        for number, phase in enumerate(tls.findall("phase")):
            state = phase.get("state", "")
            duration = float(phase.get("duration", "nan"))
            phases.append({
                "index": number,
                "duration_s": duration,
                "state": state,
                "green_links": [i for i, signal in enumerate(state) if signal in "Gg"],
                "yellow_links": [i for i, signal in enumerate(state) if signal == "y"],
                "redyellow_links": [i for i, signal in enumerate(state) if signal == "u"],
            })
        controllers.append({
            "tls_id": tls_id,
            "program_id": tls.get("programID"),
            "offset_s": float(tls.get("offset", 0)),
            "cycle_s": round(sum(phase["duration_s"] for phase in phases), 3),
            "links": {str(index): movement for index, movement
                      in sorted(links.get(tls.get("id"), {}).items())},
            "phases": phases,
            **plan_sources.get(tls_id, {}),
        })
    payload = {
        "schema_version": 2,
        "kind": "SignalPlan",
        "plan_id": hashlib.sha256((network_sha + plan_sha).encode()).hexdigest()[:16],
        "provenance": provenance,
        "network_sha256": network_sha,
        "plan_sha256": plan_sha,
        "plan_files": plan_files,
        "controllers": controllers,
        "limitations": [
            "green_links reflect SUMO phase states, not a verified municipal conflict matrix",
            "pedestrian/cycle, detector, transit-priority and plan-selection logic are absent",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp")
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, out_path)
    return payload


def parse_tls_movements(net_path: Path) -> dict[str, dict[int, tuple[str, str]]]:
    """Map each TLS link index to its incoming/outgoing edge movement."""
    out: dict[str, dict[int, tuple[str, str]]] = {}
    for connection in ET.parse(net_path).getroot().findall("connection"):
        tls_id = connection.get("tl")
        link_index = connection.get("linkIndex")
        if not tls_id or link_index is None:
            continue
        out.setdefault(tls_id, {})[int(link_index)] = (
            connection.get("from", ""), connection.get("to", ""))
    return out


def route_link_demands(route_path: Path,
                       movements: dict[str, dict[int, tuple[str, str]]],
                       begin_s: float | None = None,
                       end_s: float | None = None,
                       ) -> dict[str, dict[int, float]]:
    """Count planned vehicle movements for every controlled TLS link.

    begin_s/end_s (default None = every vehicle in route_path) restrict
    counting to vehicles departing within [begin_s, end_s). Found in review
    2026-07-12: without this, the demand-weighted TLS condition allocated
    green time from route_path's FULL movement mix (typically a whole
    day's calibrated demand) rather than the actual analysis window it was
    being optimized for and compared against — a vehicle with an
    unparseable depart (e.g. "triggered") is skipped rather than guessed
    into or out of the window."""
    by_pair: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for tls_id, links in movements.items():
        for link_index, pair in links.items():
            by_pair.setdefault(pair, []).append((tls_id, link_index))
    demands: dict[str, dict[int, float]] = {}
    for vehicle in ET.parse(route_path).getroot().findall("vehicle"):
        if begin_s is not None or end_s is not None:
            try:
                depart_s = float(vehicle.get("depart"))
            except (TypeError, ValueError):
                continue
            if begin_s is not None and depart_s < begin_s:
                continue
            if end_s is not None and depart_s >= end_s:
                continue
        route = vehicle.find("route")
        if route is None or not route.get("edges"):
            continue
        edges = route.get("edges").split()
        for pair in zip(edges, edges[1:]):
            for tls_id, link_index in by_pair.get(pair, []):
                per_tls = demands.setdefault(tls_id, {})
                per_tls[link_index] = per_tls.get(link_index, 0.0) + 1.0
    return demands


def allocate_junction_green_times(phases: list[tuple[float, str]],
                                  link_demand: dict[int, float]
                                  ) -> list[tuple[float, str]]:
    """Allocate one junction's green budget jointly across phase groups.

    The phase states define compatibility and therefore cannot be changed by
    this synthetic optimizer. Every pure-green phase receives the legal
    minimum; remaining green seconds are split in proportion to the demand
    of its simultaneously served links. Yellow, all-red, and red-yellow
    durations stay fixed, so red time is the safe residual of the cycle.
    """
    green_indexes = [i for i, (_, state) in enumerate(phases)
                     if "y" not in state and any(c in "Gg" for c in state)]
    if not green_indexes:
        return phases
    cycle_s = sum(duration for duration, _ in phases)
    fixed_s = cycle_s - sum(phases[i][0] for i in green_indexes)
    green_budget = max(cycle_s - fixed_s, MIN_GREEN_S * len(green_indexes))
    weights = []
    for i in green_indexes:
        state = phases[i][1]
        weights.append(sum(link_demand.get(link, 0.0)
                           for link, signal in enumerate(state)
                           if signal in "Gg"))
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0] * len(green_indexes)
        total_weight = float(len(green_indexes))
    remaining = max(0.0, green_budget - MIN_GREEN_S * len(green_indexes))
    out = list(phases)
    for index, weight in zip(green_indexes, weights):
        out[index] = (round(MIN_GREEN_S + remaining * weight / total_weight, 1),
                      phases[index][1])
    return out


def build_demand_weighted_tls(net_path: Path, route_path: Path,
                              out_path: Path, program_id: str = "dw",
                              begin_s: float | None = None,
                              end_s: float | None = None) -> int:
    """Build independently timed but jointly allocated plans for all TLS.

    Each junction has its own movement demand and cycle. The function first
    builds the conservative timing envelope, then redistributes only its
    safe green phases jointly within that junction. It is a deterministic
    candidate generator for simulation-based selection, not a claim of a
    globally optimal controller.

    begin_s/end_s (found in review 2026-07-12): the analysis window this
    plan is being built for and will be evaluated against — passed through
    to route_link_demands so green-time allocation reflects the movement
    mix DURING that window, not route_path's full (often whole-day)
    demand. Default None preserves the prior whole-file behaviour for any
    caller that genuinely wants it."""
    network_root = ET.parse(net_path).getroot()
    movements = parse_tls_movements(net_path)
    demands = route_link_demands(route_path, movements, begin_s=begin_s, end_s=end_s)
    link_geometry = parse_tls_link_geometry(net_path)
    out_root = ET.Element("additional")
    count = 0
    for tls in network_root.findall("tlLogic"):
        tls_id = tls.get("id")
        raw = [(float(phase.get("duration")), phase.get("state"))
               for phase in tls.findall("phase")]
        if not raw:
            continue
        safe_phases = rebuild_phases(raw, link_geometry.get(tls_id, {}))
        allocated = allocate_junction_green_times(safe_phases, demands.get(tls_id, {}))
        out_tls = ET.SubElement(out_root, "tlLogic", {
            "id": tls_id, "type": "static", "programID": program_id, "offset": "0",
        })
        for duration, state in allocated:
            ET.SubElement(out_tls, "phase", {"duration": str(duration), "state": state})
        count += 1
    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, default=None,
                   help="Additional-file output path (default: "
                        "sumo/tls_regulation_<net_fingerprint>.add.xml).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not rs.NET_PATH.exists():
        sys.exit(f"{rs.NET_PATH} not found — run build_sumo_net.py first")
    net_fp = net_fingerprint(rs.NET_PATH)
    out_path = args.out or rs.SUMO_DIR / f"tls_regulation_{net_fp}.add.xml"
    n = build_regulation_compliant_tls(rs.NET_PATH, out_path)
    print(f"Wrote {n} TSFS-informed synthetic TLS program(s) to {out_path}")


if __name__ == "__main__":
    main()
