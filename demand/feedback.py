"""Congestion-feedback stage of the demand pipeline — split from
build_sumo_demand.py 2026-07-14 (IMPROVEMENT_PLAN.md H1). One meso measurement pass +
BPR travel-time updates feeding duarouter's next candidate generation.

Import via `from demand import feedback` (or the re-exports kept on
build_sumo_demand for existing callers). Patchable module globals
(SUMO_DIR, NET_PATH, GEO_PATH, subprocess) live HERE now — tests that
monkeypatch them must target this module.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GEO_PATH = Path("web/data/network.geojson")
SUMO_DIR = Path("sumo")
NET_PATH = SUMO_DIR / "net.net.xml"
FEEDBACK_SIM_TIMEOUT_S = 300

def run_feedback_simulation(route_path: Path, duration_s: int, home: Path,
                            iteration: int) -> Path:
    """One meso pass over the CURRENT calibrated routes, aggregated into a
    single interval spanning the whole window, to get each edge's actual
    (congestion-adjusted) travel time. This is the missing half of the
    calibration: PFE picks route USE COUNTS to match sensor totals, but the
    candidate routes it picks from were generated against FREE-FLOW cost —
    a route that's actually congested under the calibrated demand never
    gets deprioritized. Feeding this back into duarouter (as --weight-files)
    for the next candidate-generation pass closes that loop.

    Research check (2026-07-08): simultaneous count+equilibrium calibration
    is documented to significantly outperform a one-shot sequential run —
    hence iterating this a few times rather than doing it once and freezing.
    """
    ed_file = SUMO_DIR / f"feedback_edgedata_{iteration}.xml"
    add_file = SUMO_DIR / f"feedback_additional_{iteration}.add.xml"
    with open(add_file, "w") as f:
        f.write(f'<additional><edgeData id="fb" file="{ed_file.name}" '
                f'begin="0" end="{duration_s}"/></additional>\n')
    cmd = [
        str(home / "bin" / "sumo"),
        "--mesosim", "true",
        "--meso-junction-control", "true",
        "--meso-junction-control.limited", "true",
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        "-a", str(add_file.resolve()),
        "--begin", "0", "--end", str(duration_s + 3600),
        "--no-step-log", "true", "--no-warnings", "true",
        "--ignore-route-errors", "true", "--seed", "1000",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=FEEDBACK_SIM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        sys.exit(f"feedback simulation timed out after {FEEDBACK_SIM_TIMEOUT_S}s")
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit("feedback simulation failed")
    return ed_file


# BPR (Bureau of Public Roads 1964) volume-delay function: t = t_free *
# (1 + alpha*(v/c)^beta) — the standard closed-form Frank-Wolfe/MSA traffic
# assignment travel-time estimate, computed directly from PFE's own solved
# flow with NO extra simulation needed. ~1000x cheaper per iteration than
# run_feedback_simulation (a real meso pass), which is what makes iterating
# enough times to actually converge (research: MSA/Frank-Wolfe typically
# need 10-25+ iterations, not 1-2) practical instead of prohibitively slow.
BPR_ALPHA = 0.15
BPR_BETA  = 4.0
# HCM-consistent effective urban-street capacity per lane (veh/h) — signals/
# turning movements make this well below highway free-flow capacity (~1900
# pcu/h/lane); matches build_sumo_net.py's DEFAULT_SPEED_KMH/DEFAULT_LANES
# road-type granularity.
CAPACITY_PER_LANE_VPH = {
    "motorway": 1900, "motorway_link": 1500, "trunk": 1700, "trunk_link": 1300,
    "primary": 900, "primary_link": 800, "secondary": 800, "secondary_link": 700,
    "tertiary": 700, "tertiary_link": 600,
    "residential": 500, "living_street": 300, "unclassified": 600,
}
DEFAULT_CAPACITY_PER_LANE_VPH = 600


BPR_PERIOD_S = 3600.0   # 1 hour: fine enough to catch a rush-hour peak
                        # without so many periods duarouter's routing table
                        # rebuild (--weight-period) gets expensive.


def bpr_travel_times(achieved: dict[str, list[float]], quarter_s: float = 900.0,
                     net_path: Path = NET_PATH, geo_path: Path = GEO_PATH,
                     period_s: float = BPR_PERIOD_S,
                     ) -> dict[str, list[float]]:
    """{edge_id: [travel_time_s per period_s-sized period]} from PFE's own
    achieved per-quarter flow — no simulation needed. A SINGLE flat average
    over the whole calibration window (the first version of this function)
    dilutes a sharp rush-hour peak into a mild multi-hour average — real
    dynamic/time-dependent traffic assignment practice computes this PER
    PERIOD, which is what lets duarouter (--weight-period) route trips
    against the congestion that's actually present when they depart,
    instead of a watered-down daily mean. Free-flow time + lane count come
    from the SUMO net (the routing graph itself); road type (for capacity)
    from network.geojson."""
    net = ET.parse(net_path).getroot()
    freeflow: dict[str, float] = {}
    lanes: dict[str, int] = {}
    for edge in net.findall("edge"):
        if edge.get("function") == "internal":
            continue
        lane_els = edge.findall("lane")
        if not lane_els:
            continue
        eid = edge.get("id")
        length = float(lane_els[0].get("length"))
        speed = float(lane_els[0].get("speed"))
        freeflow[eid] = length / speed if speed > 0 else 1.0
        lanes[eid] = len(lane_els)

    highway: dict[str, str] = {}
    with open(geo_path) as f:
        for feat in json.load(f)["features"]:
            p = feat["properties"]
            highway[p["id"]] = p.get("highway") or "unclassified"

    quarters_per_period = max(1, round(period_s / quarter_s))
    tt: dict[str, list[float]] = {}
    for eid, series in achieved.items():
        if eid not in freeflow:
            continue
        cap_per_lane = CAPACITY_PER_LANE_VPH.get(highway.get(eid),
                                                 DEFAULT_CAPACITY_PER_LANE_VPH)
        capacity = max(1, lanes[eid]) * cap_per_lane
        t_free = freeflow[eid]
        periods = []
        for start in range(0, len(series), quarters_per_period):
            chunk = series[start:start + quarters_per_period]
            v_per_hour = sum(chunk) / (len(chunk) * quarter_s / 3600.0)
            periods.append(t_free * (1 + BPR_ALPHA * (v_per_hour / capacity) ** BPR_BETA))
        tt[eid] = periods
    return tt


def damp_travel_times(
    new: dict[str, list[float]], prev: dict[str, list[float]] | None,
    iteration: int,
) -> dict[str, list[float]]:
    """Method of Successive Averages step, applied per period: blend this
    iteration's estimate with the running average instead of fully
    replacing it. A full replacement each round is a known-worse
    convergence strategy than even plain MSA (let alone Frank-Wolfe) — it
    can oscillate between two congestion patterns instead of settling.
    Step size 1/(iteration+1) is the classic MSA schedule."""
    if prev is None:
        return {eid: list(periods) for eid, periods in new.items()}
    step = 1.0 / (iteration + 1)
    out = {eid: list(periods) for eid, periods in prev.items()}
    for eid, new_periods in new.items():
        prev_periods = prev.get(eid, new_periods)
        out[eid] = [(1 - step) * p + step * n
                   for p, n in zip(prev_periods, new_periods)]
    return out


def write_weight_file(travel_times: dict[str, list[float]], out_path: Path,
                      period_s: float = BPR_PERIOD_S) -> None:
    """meandata XML, one <interval> per period — the format
    run_feedback_simulation's real edgeData output also produces (as
    consecutive 900s intervals), so duarouter -w/--weight-attribute
    traveltime --weight-period reads either interchangeably."""
    n_periods = max((len(p) for p in travel_times.values()), default=0)
    with open(out_path, "w") as f:
        f.write("<meandata>\n")
        for i in range(n_periods):
            f.write(f'  <interval begin="{i * period_s:.2f}" '
                    f'end="{(i + 1) * period_s:.2f}">\n')
            for eid, periods in travel_times.items():
                if i < len(periods):
                    f.write(f'    <edge id="{eid}" traveltime="{periods[i]:.2f}"/>\n')
            f.write("  </interval>\n")
        f.write("</meandata>\n")


def run_tool(script: str, args: list[str], home: Path) -> None:
    cmd = [sys.executable, str(home / "tools" / script), *args]
    env = {
        "SUMO_HOME": str(home),
        "PATH": f"{home / 'bin'}:/usr/bin:/bin",
        "HOME": str(Path.home()),
    }
    res = subprocess.run(cmd, capture_output=True, text=True, env=env,
                         timeout=1200)
    tail = (res.stdout + res.stderr)[-2500:]
    print(tail)
    if res.returncode != 0:
        sys.exit(f"{script} failed")


_PFE_PAR_SHAPES = None
_PFE_PAR_ROUTE_COST = None
_PFE_PAR_STRUCTURE_GROUPS = None   # list of (name, member indices, cap share)
