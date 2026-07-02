"""
Run a SUMO scenario (baseline or road closure) and export flows for the web app.

Run after build_sumo_demand.py:
  python3 run_scenario.py                                    # baseline
  python3 run_scenario.py --close 60786979_3575001205_0      # closure scenario

Method:
  - Simulates the calibrated demand (sumo/calibrated.rou.xml) N times with
    different random seeds (Monte Carlo).
  - --close <edgeId> adds a rerouter that closes the edge for all traffic;
    SUMO reroutes vehicles around it by construction.
  - 15-min per-edge flows ("entered" vehicle counts) are averaged over seeds
    and written in the flows.json format with the SAME edge IDs as the map.

Per-edge confidence (0-1), written into the scenario file:
    confidence = spatial_prior × exp(-CV)
  where spatial_prior comes from network.geojson (distance to nearest sensor)
  and CV is the mean coefficient of variation across Monte Carlo seeds.
  Far from sensors AND unstable across seeds → low confidence.

Writes:
  web/data/scenarios/<name>.json   — flows + confidence for the web app
  web/data/scenarios/index.json    — manifest the web app lists scenarios from
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from build_sumo_net import sumo_home

SUMO_DIR  = Path("sumo")
NET_PATH  = SUMO_DIR / "net.net.xml"
GEO_PATH  = Path("web/data/network.geojson")
OUT_DIR   = Path("web/data/scenarios")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--close", default=None, metavar="EDGE_ID",
                   help="Edge to close (must exist in network.geojson). Omit for baseline.")
    p.add_argument("--name",  default=None,
                   help="Scenario name (default: 'baseline' or 'close_<edge>')")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo runs (default 3)")
    return p.parse_args()


def load_geojson_meta() -> tuple[dict[str, float], dict[str, str]]:
    """{edge_id: spatial confidence prior}, {edge_id: street name}."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    prior: dict[str, float] = {}
    names: dict[str, str] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        prior[p["id"]] = p.get("confidence") or 0.5
        names[p["id"]] = p.get("name") or p["id"]
    return prior, names


def write_additional(path: Path, edgedata_file: Path, close_edge: str | None,
                     all_edges: list[str], duration_s: int) -> None:
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <edgeData id="ed" file="{edgedata_file.name}" period="900" '
                f'begin="0" end="{duration_s}" excludeEmpty="true"/>\n')
        if close_edge:
            # Rerouter on every edge: any vehicle whose remaining route uses the
            # closed edge recomputes as soon as it enters its next edge.
            f.write(f'  <rerouter id="closure" edges="{" ".join(all_edges)}">\n')
            f.write(f'    <interval begin="0" end="{duration_s + 3600}">\n')
            f.write(f'      <closingReroute id="{close_edge}" disallow="all"/>\n')
            f.write("    </interval>\n")
            f.write("  </rerouter>\n")
        f.write("</additional>\n")


def demand_variants() -> list[Path]:
    """Calibrated route sets — q50 plus (if built) the q10/q90 direction-
    split variants. Monte Carlo seeds are spread over them so the seed
    spread — and the confidence — includes direction uncertainty."""
    paths = [SUMO_DIR / "calibrated.rou.xml"]
    for suffix in ("_v1", "_v2"):
        p = SUMO_DIR / f"calibrated{suffix}.rou.xml"
        if p.exists():
            paths.append(p)
    return paths


def run_sumo(seed: int, route_path: Path, add_path: Path,
             duration_s: int, home: Path) -> None:
    # cwd=SUMO_DIR so the edgeData output file (relative in the additional
    # file) lands in sumo/ — inputs must therefore be absolute paths.
    cmd = [
        str(home / "bin" / "sumo"),
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        "-a", str(add_path.resolve()),
        "--seed", str(seed),
        "--begin", "0",
        "--end", str(duration_s + 1800),   # let last departures finish
        "--no-step-log", "true",
        "--no-warnings", "true",
        # Vehicles whose destination IS the closed edge have no valid route —
        # drop them instead of aborting (standard for closure studies).
        "--ignore-route-errors", "true",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)})
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"sumo failed (seed {seed})")


def parse_edgedata(path: Path, n_intervals: int) -> dict[str, np.ndarray]:
    """{edge_id: array of 'entered' counts per 15-min interval}."""
    flows: dict[str, np.ndarray] = {}
    root = ET.parse(path).getroot()
    for interval in root.findall("interval"):
        i = int(float(interval.get("begin")) // 900)
        if i >= n_intervals:
            continue
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            entered = float(edge.get("entered") or 0)
            if eid not in flows:
                flows[eid] = np.zeros(n_intervals)
            flows[eid][i] = entered
    return flows


def main() -> None:
    args = parse_args()
    home = sumo_home()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    duration_s  = n_intervals * 900

    prior, names = load_geojson_meta()
    if args.close and args.close not in prior:
        sys.exit(f"--close {args.close}: not an edge in network.geojson")

    name  = args.name or (f"close_{args.close}" if args.close else "baseline")
    label = (f"Avstängning: {names[args.close]}" if args.close
             else "Baslinje (ingen avstängning)")

    # Edge list for the rerouter (net edge IDs = plain edge IDs, no internals)
    net_edges = [e.get("id") for e in ET.parse(NET_PATH).getroot().findall("edge")
                 if not e.get("function")]

    print(f"Scenario '{name}'  ({label})  —  {args.seeds} seeds × {n_intervals} × 15 min")

    variants = demand_variants()
    if len(variants) > 1:
        print(f"  {len(variants)} demand variants (q50 + direction-split bounds)")

    per_seed: list[dict[str, np.ndarray]] = []
    for s in range(args.seeds):
        seed = 1000 + s
        route_path = variants[s % len(variants)]
        ed_file  = SUMO_DIR / f"edgedata_{name}_{seed}.xml"
        add_path = SUMO_DIR / f"additional_{name}_{seed}.add.xml"
        write_additional(add_path, ed_file, args.close, net_edges, duration_s)
        run_sumo(seed, route_path, add_path, duration_s, home)
        per_seed.append(parse_edgedata(ed_file, n_intervals))
        print(f"  seed {seed} ({route_path.name}): "
              f"{len(per_seed[-1])} edges with traffic")

    # ── Aggregate: mean flows + Monte Carlo confidence ─────────────────────────
    web_edges = set(prior)   # only edges the map can draw
    all_ids   = set().union(*per_seed) & web_edges

    flows_out: dict[str, list[int]] = {}
    conf_out:  dict[str, float] = {}
    for eid in sorted(all_ids):
        stack = np.stack([ps.get(eid, np.zeros(n_intervals)) for ps in per_seed])
        mean  = stack.mean(axis=0)
        flows_out[eid] = [int(round(v)) for v in mean]

        busy = mean > 2           # CV is meaningless for near-zero flows
        cv   = float((stack.std(axis=0)[busy] / mean[busy]).mean()) if busy.any() else 0.0
        conf_out[eid] = round(prior[eid] * float(np.exp(-cv)), 3)

    payload = {
        "epoch":            meta["epoch_sim"],
        "interval_minutes": 15,
        "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
        "scenario": {
            "name": name, "label": label,
            "closed_edge": args.close,
            "date": meta["date"], "begin": meta["begin"], "end": meta["end"],
            "seeds": args.seeds,
        },
        "flows":      flows_out,
        "confidence": conf_out,
    }
    out_path = OUT_DIR / f"{name}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {out_path}  ({len(flows_out)} edges)")

    # ── Manifest ───────────────────────────────────────────────────────────────
    index_path = OUT_DIR / "index.json"
    index = json.load(open(index_path)) if index_path.exists() else {"scenarios": []}
    index["scenarios"] = [s for s in index["scenarios"] if s["name"] != name]
    index["scenarios"].append({
        "name": name, "label": label, "file": f"{name}.json",
        "closed_edge": args.close,
        "window": f"{meta['date']} {meta['begin']}–{meta['end']}",
    })
    index["scenarios"].sort(key=lambda s: s["name"])
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Updated {index_path}  ({len(index['scenarios'])} scenarios)")


if __name__ == "__main__":
    main()
