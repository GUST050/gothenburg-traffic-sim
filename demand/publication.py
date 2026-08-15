"""Publication stage — split from build_sumo_demand.py 2026-07-14
(IMPROVEMENT_PLAN.md H1).

Owns: stale-scenario clearing (with the empty-manifest guarantee),
routeSampler count-file writing (direction-share split of two-way
totals), and the OD-matrix export. Patch SUMO_DIR/SCEN_DIR HERE.
"""
from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from demand.intake import load_direction_split

SUMO_DIR = Path("sumo")
SCEN_DIR = Path("web/data/scenarios")

def clear_stale_scenarios() -> int:
    """Remove web scenarios generated from an older calibrated demand.

    A demand rebuild changes the route file that every scenario simulates.
    Leaving old baseline/closure JSON in web/data/scenarios makes the UI
    list scenarios that look current but were produced from the previous
    date/window/source. serve.py already did this for web-triggered
    recalibration; doing it here makes the CLI path equally safe.

    Deletes index.json along with the scenario files (it's as stale as
    they are), but immediately replaces it with an empty manifest rather
    than leaving it missing: web/index.html fetches index.json directly,
    and serve.py's version of this cleanup gets away with a momentary gap
    only because it always calls run_scenario.py right after — this CLI
    path has no such guarantee (`make demand` and `make scenario` are
    separate targets), so a missing manifest could sit there until the
    next `make scenario` run.
    """
    if not SCEN_DIR.exists():
        return 0
    n = 0
    for path in SCEN_DIR.glob("*.json"):
        path.unlink()
        n += 1
    # Atomic (write-temp-then-replace) so a live browser polling index.json
    # mid-cleanup never observes a truncated file — same reasoning as
    # run_scenario.py's atomic_write_json, duplicated here rather than
    # imported to keep this CLI-only path independent of run_scenario.py's
    # module-level state (2026-07-10).
    index_path = SCEN_DIR / "index.json"
    fd, tmp_name = tempfile.mkstemp(dir=SCEN_DIR, prefix=".index.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"scenarios": []}, f, indent=2)
        os.replace(tmp_name, index_path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    return n


def write_counts(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    out_path: Path,
    split_key: str = "edge_shares",
    anchor_day: str | None = None,
    anchor_epoch=None,
) -> int:
    """15-min edgeData intervals; sim time 0 = window start. Returns n written.

    The release share per Total edge is the registered maximum-entropy 50/50
    policy plus any applicable local anchor. Explicit q10/q90 ``split_key``
    values retain the transferred-model stress cases for diagnostics. "S"
    sensor edges always take the full count.

    ``anchor_day``/``anchor_epoch`` carry sensor 107's published local
    anchor, exactly as ``demand.intake.build_targets`` does. This is the
    routeSampler branch's copy of the same measured-target construction, and
    it must not silently disagree with the PFE branch about what a station
    measures: the anchor reaching one path and not the other would make the
    two branches calibrate to different targets from identical inputs.
    """
    est_shares = load_direction_split(
        split_key, anchor_day=anchor_day, anchor_flows=flows,
        anchor_epoch=anchor_epoch)
    if est_shares:
        label = ("RELEASE 50/50 direction policy"
                 if split_key == "edge_shares"
                 else "DIAGNOSTIC estimated direction stress")
        print(f"  Using {label} ({split_key})"
              + (f", anchored for {anchor_day}" if anchor_day else ""))
    else:
        print("  No direction_split.json — falling back to even split")

    n_measurements = 0
    with open(out_path, "w") as f:
        f.write("<data>\n")
        for i in range(n_intervals):
            qi   = qi_start + i
            slot = qi % 96
            f.write(f'  <interval id="q{qi}" begin="{i * 900}" end="{(i + 1) * 900}">\n')
            for edges in sensor_edges.values():
                for edge_id in edges:
                    # Split ONLY a two-way total, the same guard
                    # demand.intake.build_targets carries. A single-direction
                    # station already measures one carriageway, so its value
                    # IS that direction's count.
                    #
                    # This branch was missing the guard while build_targets
                    # had it (added 2026-08-06). Since the direction model
                    # started predicting BOTH carriageways at every station,
                    # a measured single-direction edge resolves to ~0.5 in
                    # est_shares, so a measured 50 was written out as 25 —
                    # silently, and at 100% GEH against the halved target.
                    share = (est_shares.get(edge_id, [1.0 / len(edges)] * 96)[slot]
                             if len(edges) > 1 else 1.0)
                    v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                    if v is None:
                        continue
                    f.write(f'    <edge id="{edge_id}" count="{v * share:.1f}"/>\n')
                    n_measurements += 1
            f.write("  </interval>\n")
        f.write("</data>\n")
    return n_measurements


SECTORS = ["N", "NO", "O", "SO", "S", "SV", "V", "NV"]


def export_od(calib_path: Path, sensor_edges: dict[str, list[str]], meta: dict) -> None:
    """
    Aggregate the calibrated routes into an origin/destination matrix.

    The sampled routes ARE trips with origins and destinations, so the OD
    matrix that falls out is by construction consistent with the sensor
    counts — one plausible OD among the many the 6 counters cannot
    distinguish. Zones: the two sensor cluster areas (<400 m from a cluster
    centre, named by geometry: western = Götaplatsen, eastern = Scandinavium)
    plus eight compass entry sectors around the network.

    Writes web/data/od_matrix.json + od_matrix.csv.
    """
    # Edge midpoints in metric EPSG:3007 from the plain edges file
    mids: dict[str, tuple[float, float]] = {}
    for e in ET.parse(SUMO_DIR / "plain.edg.xml").getroot().findall("edge"):
        pts = [tuple(map(float, p.split(","))) for p in e.get("shape").split()]
        mids[e.get("id")] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))

    # Cluster centres: group sensors whose edges lie within 600 m of each other
    import math
    sensor_pos = {sid: mids[edges[0]] for sid, edges in sensor_edges.items()
                  if edges[0] in mids}
    clusters: list[list[str]] = []
    for sid, pos in sensor_pos.items():
        for cl in clusters:
            cx = sum(sensor_pos[s][0] for s in cl) / len(cl)
            cy = sum(sensor_pos[s][1] for s in cl) / len(cl)
            if math.hypot(pos[0] - cx, pos[1] - cy) < 600:
                cl.append(sid)
                break
        else:
            clusters.append([sid])
    centres = [(sum(sensor_pos[s][0] for s in cl) / len(cl),
                sum(sensor_pos[s][1] for s in cl) / len(cl)) for cl in clusters]
    # Western cluster = Götaplatsen, eastern = Scandinavium (pure geometry)
    names = ["Götaplatsen-området", "Scandinavium-området"]
    cluster_zones = sorted(zip(centres, names))  # sorted by x (west first)

    net_cx = sum(m[0] for m in mids.values()) / len(mids)
    net_cy = sum(m[1] for m in mids.values()) / len(mids)

    def zone_of(edge_id: str) -> str:
        mid = mids.get(edge_id)
        if mid is None:
            return "okänd"
        for (cx, cy), zname in cluster_zones:
            if math.hypot(mid[0] - cx, mid[1] - cy) < 400:
                return zname
        ang = math.degrees(math.atan2(mid[0] - net_cx, mid[1] - net_cy)) % 360
        return f"Infart {SECTORS[int((ang + 22.5) // 45) % 8]}"

    support_vehicle_ids: set[str] = set()
    agent_path = calib_path.with_name(
        calib_path.name.replace(".rou.xml", ".agents.json"))
    if agent_path.exists():
        with open(agent_path) as handle:
            support_vehicle_ids = {
                str(agent.get("vehicle_id"))
                for agent in json.load(handle).get("agents", [])
                if agent.get("support_only") is True
            }
    od: dict[tuple[str, str], int] = {}
    n_trips = 0
    for veh in ET.parse(calib_path).getroot().findall("vehicle"):
        if veh.get("id") in support_vehicle_ids:
            continue
        edges = veh.find("route").get("edges").split()
        key = (zone_of(edges[0]), zone_of(edges[-1]))
        od[key] = od.get(key, 0) + 1
        n_trips += 1

    zones = sorted({z for pair in od for z in pair})
    matrix = {o: {d: od.get((o, d), 0) for d in zones} for o in zones}

    if "date" in meta:
        window = f"{meta['date']} {meta['begin']}–{meta['end']}"
    else:
        window = f"{meta['start_date']} → {meta['end_date_exclusive']} " \
                 f"({meta['days']} days)"

    out_json = Path("web/data/od_matrix.json")
    with open(out_json, "w") as f:
        json.dump({
            "window":  window,
            "n_trips": n_trips,
            "edge_support_routes_excluded": len(support_vehicle_ids),
            "zones":   zones,
            "matrix":  matrix,
            "note":    "ESTIMATED OD — one plausible matrix consistent with the "
                       "6 sensor counts and the estimated direction split; the "
                       "true OD is not identifiable from 6 counting points. "
                       "Explicit full-edge support routes are simulated but "
                       "excluded from this behavioural OD estimate.",
        }, f, ensure_ascii=False, indent=1)

    out_csv = Path("web/data/od_matrix.csv")
    with open(out_csv, "w") as f:
        f.write("origin\\destination," + ",".join(zones) + "\n")
        for o in zones:
            f.write(o + "," + ",".join(str(matrix[o][d]) for d in zones) + "\n")

    print(f"\nOD-matris ({n_trips} kalibrerade resor) → {out_json} + {out_csv}")
    top = sorted(od.items(), key=lambda kv: -kv[1])[:8]
    for (o, d), n in top:
        print(f"  {o:<22} → {d:<22} {n:>5}")
