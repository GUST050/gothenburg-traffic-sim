"""
Calibrate SUMO demand against the 6 sensors for a chosen time window.

Run after build_sumo_net.py:
  python3 build_sumo_demand.py                         # Tue 2025-09-16 06:00-10:00
  python3 build_sumo_demand.py --date 2025-09-16 --begin 07:00 --end 09:00

Method (SUMO's routeSampler workflow):
  1. Sensor counts for the window → 15-min edgeData intervals (counts.xml).
     "Total" sensors measure the SUM of both directions; direction is not
     recoverable (no directional re-export is coming), so the count is split
     50/50 over the two directed edges — the max-entropy assumption. Sensor
     1076 ("S") is a true single-direction count on one edge.
  2. randomTrips.py + duarouter → a large pool of CANDIDATE routes through
     the full network (diverse origins/destinations, through-traffic favored).
  3. routeSampler.py samples routes from the pool so that simulated 15-min
     flows match the sensor counts → calibrated.rou.xml.

Honesty note: only traffic that crosses a sensor is calibrated. Streets far
from sensors carry little/no sampled traffic — which is correct: we have no
ground truth there, and the per-edge confidence in the web app says exactly
that.

Writes (sumo/):
  counts.xml, candidates.rou.xml, calibrated.rou.xml, demand_meta.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from build_sumo_net import sumo_home

FLOWS_PATH = Path("web/data/flows.json")
GEO_PATH   = Path("web/data/network.geojson")
SUMO_DIR   = Path("sumo")
NET_PATH   = SUMO_DIR / "net.net.xml"

EPOCH    = pd.Timestamp("2025-01-01T00:00:00")
INTERVAL = pd.Timedelta(minutes=15)

# Candidate-pool density: one random trip every N seconds of the window.
# The pool needs route DIVERSITY, not volume — routeSampler repeats routes.
CANDIDATE_PERIOD_S = 2.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--date",  default="2025-09-16",
                   help="Simulation date (default: Tue 2025-09-16 — normal September weekday)")
    p.add_argument("--begin", default="06:00", help="Window start HH:MM (default 06:00)")
    p.add_argument("--end",   default="10:00", help="Window end HH:MM (default 10:00)")
    p.add_argument("--seed",  type=int, default=42)
    return p.parse_args()


def load_sensor_edges() -> dict[str, list[str]]:
    """{sensor_id: [edge_id, ...]} from network.geojson (1 or 2 edges)."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    result: dict[str, list[str]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id"):
            result.setdefault(str(p["sensor_id"]), []).append(p["id"])
    return result


def write_counts(
    flows: dict[str, list],
    sensor_edges: dict[str, list[str]],
    qi_start: int,
    n_intervals: int,
    out_path: Path,
) -> int:
    """15-min edgeData intervals; sim time 0 = window start. Returns n written."""
    n_measurements = 0
    with open(out_path, "w") as f:
        f.write("<data>\n")
        for i in range(n_intervals):
            qi = qi_start + i
            f.write(f'  <interval id="q{qi}" begin="{i * 900}" end="{(i + 1) * 900}">\n')
            for edges in sensor_edges.values():
                share = 1.0 / len(edges)   # Total → 50/50 split; S → 1.0
                for edge_id in edges:
                    v = flows.get(edge_id, [None])[qi] if qi < len(flows.get(edge_id, [])) else None
                    if v is None:
                        continue
                    f.write(f'    <edge id="{edge_id}" count="{v * share:.1f}"/>\n')
                    n_measurements += 1
            f.write("  </interval>\n")
        f.write("</data>\n")
    return n_measurements


def run_tool(script: str, args: list[str], home: Path) -> None:
    cmd = [sys.executable, str(home / "tools" / script), *args]
    env = {
        "SUMO_HOME": str(home),
        "PATH": f"{home / 'bin'}:/usr/bin:/bin",
        "HOME": str(Path.home()),
    }
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    tail = (res.stdout + res.stderr)[-2500:]
    print(tail)
    if res.returncode != 0:
        sys.exit(f"{script} failed")


def main() -> None:
    args = parse_args()
    if not NET_PATH.exists():
        sys.exit("sumo/net.net.xml missing — run build_sumo_net.py first")

    t0 = pd.Timestamp(f"{args.date} {args.begin}")
    t1 = pd.Timestamp(f"{args.date} {args.end}")
    qi_start    = int((t0 - EPOCH) / INTERVAL)
    n_intervals = int((t1 - t0) / INTERVAL)
    duration_s  = n_intervals * 900
    print(f"Window: {t0} → {t1}  ({n_intervals} × 15 min)")

    with open(FLOWS_PATH) as f:
        flows = json.load(f)["flows"]
    sensor_edges = load_sensor_edges()
    print(f"Sensors: { {sid: len(e) for sid, e in sensor_edges.items()} }")

    counts_path = SUMO_DIR / "counts.xml"
    n = write_counts(flows, sensor_edges, qi_start, n_intervals, counts_path)
    print(f"Wrote {counts_path}  ({n} edge×interval measurements)")

    home = sumo_home()

    print("\nGenerating candidate route pool (randomTrips + duarouter) …")
    cand_path = SUMO_DIR / "candidates.rou.xml"
    run_tool("randomTrips.py", [
        "-n", str(NET_PATH),
        "-r", str(cand_path),
        "-o", str(SUMO_DIR / "trips.trips.xml"),
        "-b", "0", "-e", str(duration_s),
        "-p", str(CANDIDATE_PERIOD_S),
        "--fringe-factor", "5",       # favor through-traffic entering at the edge of the net
        "--seed", str(args.seed),
        "--validate",
    ], home)

    print("Sampling routes to match sensor counts (routeSampler) …")
    calib_path = SUMO_DIR / "calibrated.rou.xml"
    run_tool("routeSampler.py", [
        "-r", str(cand_path),
        "--edgedata-files", str(counts_path),
        "--edgedata-attribute", "count",
        "-o", str(calib_path),
        "--seed", str(args.seed),
    ], home)

    meta = {
        "date": args.date, "begin": args.begin, "end": args.end,
        "qi_start": qi_start, "n_intervals": n_intervals,
        # ISO with 'T' — Safari/Firefox reject "YYYY-MM-DD HH:MM" in new Date()
        "epoch_sim": t0.isoformat(),
        "note": "Total sensor counts split 50/50 over the two directed edges "
                "(direction not recoverable from delivered data).",
    }
    with open(SUMO_DIR / "demand_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nWrote {calib_path} + demand_meta.json")


if __name__ == "__main__":
    main()
