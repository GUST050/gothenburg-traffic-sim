"""
Agent F core — leave-one-station-out (LOSO) validation.

Run after build_sumo_demand (needs candidates + priors on disk):
  python3 validate_sim.py

For every station: rebuild the demand WITHOUT that station's measurements
(and without the priors derived from them), simulate, and compare the
simulated flows at the held-out edges against what the station actually
measured. This answers, empirically, the product's core honesty question:
"how wrong is the program on a street it cannot see?"

Level-2 bounds are excluded in all folds (they are derived from the full
measurement set and would leak the held-out station). Level 3 keeps only
priors NOT derived from the held-out station.

FIXED 2026-07-09 (found while auditing the whole codebase, confirmed still
open per ARCHITECTURE.md's own "KNOWN GAP" note): corridor_priors (Agent
B's "sensors helping each other" — same-direction station pairs linked by
a short path bound the edges BETWEEN them, see observability.corridor_
priors) were computed and used by the real, deployed build_sumo_demand.py
pipeline, but never wired into THIS validation script — meaning every LOSO
figure on record understated the deployed system's actual recovery. The
mechanism itself was already fully general (it scans every PAIR of
currently-measured sensors, no hardcoded IDs — new stations get corridor
priors automatically, no code changes), so this was a validation-accuracy
gap only, not a scalability one. A corridor prior is excluded from a
station's own LOSO fold whenever EITHER of its two anchor sensors is the
held-out one (same leakage-prevention principle already applied to
prior_flows.json's direction priors below).

Writes web/data/loso_report.json.
"""

from __future__ import annotations

import argparse

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

import pfe
from build_sumo_demand import build_targets, ensure_observability, load_sensor_edges
from build_sumo_net import sumo_home

SUMO_DIR = Path("sumo")
EPOCH    = pd.Timestamp("2025-01-01")


def load_inputs():
    with open("web/data/flows.json") as f:
        flows = json.load(f)["flows"]
    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    with open(SUMO_DIR / "prior_flows.json") as f:
        priors = json.load(f)["edges"]
    assign = {"weight": 0.0, "flows": {}}
    if Path(SUMO_DIR / "assignment_priors.json").exists():
        with open(SUMO_DIR / "assignment_priors.json") as f:
            assign = json.load(f)
    corridor = ensure_observability().get("corridor_priors", {})
    return flows, meta, priors, assign, corridor


def run_meso(route_file: Path, ed_file: Path, duration_s: int) -> None:
    home = sumo_home()
    add = SUMO_DIR / "loso_edgedata.add.xml"
    with open(add, "w") as f:
        f.write(f'<additional><edgeData id="ed" file="{ed_file.name}" '
                f'period="900" begin="0" end="{duration_s}" '
                f'excludeEmpty="true"/></additional>\n')
    cmd = [str(home / "bin" / "sumo"),
           "--mesosim", "true", "--meso-junction-control", "false",
           "-n", str((SUMO_DIR / "net.net.xml").resolve()),
           "-r", str(route_file.resolve()), "-a", str(add.resolve()),
           "--begin", "0", "--end", str(duration_s + 3600),
           "--no-step-log", "true", "--no-warnings", "true",
           "--ignore-route-errors", "true", "--seed", "1000"]
    # LOSO loops one sumo run per station unattended — a hang on one station
    # must not stall the whole run with no diagnostic. Same reasoning as
    # run_scenario.py's SUMO_TIMEOUT_S, found in the same review pass.
    subprocess.run(cmd, cwd=str(SUMO_DIR), capture_output=True, text=True,
                   check=True, timeout=300)


def corridor_priors_for_fold(corridor: dict, edge_to_sensor: dict[str, str],
                            held: str, qi: int) -> dict[str, tuple[float, float]]:
    """This fold's corridor-derived priors {edge: (value, weight)} — every
    corridor entry anchored on the held-out station (from OR to) is
    excluded, since it blends in that station's own measurement."""
    out: dict[str, tuple[float, float]] = {}
    for e, d in corridor.items():
        if (edge_to_sensor.get(d["from_sensor_edge"]) == held or
                edge_to_sensor.get(d["to_sensor_edge"]) == held):
            continue
        if qi >= len(d["prior"]) or d["prior"][qi] is None:
            continue
        band = d["band"][qi] or 8.0
        out[e] = (float(d["prior"][qi]), 1.0 / max(1.0, band))
    return out


def simulated_series(ed_file: Path, edge: str, nq: int) -> np.ndarray:
    out = np.zeros(nq)
    for iv in ET.parse(ed_file).getroot().findall("interval"):
        i = int(float(iv.get("begin")) // 900)
        if i >= nq:
            continue
        for e in iv.findall("edge"):
            if e.get("id") == edge:
                out[i] = float(e.get("entered") or 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-assignment-prior", action="store_true",
                   help="controlled A/B: disable the gravity-assignment "
                        "prior to isolate its effect on recovery")
    args = ap.parse_args()

    flows, meta, all_priors, assign, corridor = load_inputs()
    assign_w     = 0.0 if args.no_assignment_prior else assign.get("weight", 0.0)
    assign_flows = assign.get("flows", {})
    sensor_edges = load_sensor_edges()
    qi_start, nq = meta["qi_start"], meta["n_intervals"]
    duration_s = nq * 900
    cand_path = SUMO_DIR / "candidates.rou.xml"
    if corridor:
        print(f"  corridor coupling: {len(corridor)} edges between sensor "
              f"pairs get data-derived priors (excluded per-fold when "
              f"either anchor is the held-out station)")

    # edge -> sensor, so a corridor prior anchored on the held-out station's
    # own edges can be excluded (same leakage-prevention principle as
    # all_priors' d["sensor"] == held check below).
    edge_to_sensor = {e: sid for sid, edges in sensor_edges.items() for e in edges}

    report = {"window": f"{meta['date']} {meta['begin']}–{meta['end']}",
              "note": "leave-one-station-out — simulated vs measured at the "
                      "held-out station; level-2 bounds excluded in all folds "
                      "(they would leak the full measurement set)",
              "stations": {}}

    print(f"LOSO över {len(sensor_edges)} stationer, {nq} kvartar")
    for held, held_edges in sorted(sensor_edges.items()):
        se = {s: e for s, e in sensor_edges.items() if s != held}
        targets = []
        full = build_targets(flows, sensor_edges, qi_start, nq)
        drop = build_targets(flows, se, qi_start, nq)
        targets = drop

        priors_pq, bounds_pq = [], []
        for i in range(nq):
            slot = (qi_start + i) % 96
            qi = qi_start + i
            pq = {}
            for e, d in all_priors.items():
                if d["sensor"] == held:
                    continue
                val = d["prior"][slot]
                if val is None:
                    continue
                lo = d["prior_low"][slot] or 0.0
                hi = d["prior_high"][slot] or val
                pq[e] = (float(val), 1.0 / max(1.0, hi - lo))
            # Sensors helping each other: corridor blends between sensor
            # pairs, same as build_sumo_demand.py — excluded here whenever
            # either anchor sensor is the one being held out this fold.
            pq.update(corridor_priors_for_fold(corridor, edge_to_sensor, held, qi))
            priors_pq.append(pq)
            # Assignment field as a WIDE BOUND (see build_sumo_demand.py for
            # why: soft priors cost 2 LP variables each — 6500 of them made
            # a whole-day solve intractable; a bound is free variable-wise).
            # Structural (population/POI/gates), no leakage from held station.
            bq = {}
            if assign_w > 0:
                for e, series in assign_flows.items():
                    if e in pq or slot >= len(series) or series[slot] is None:
                        continue
                    bq[e] = (0.0, max(5.0, 5.0 * series[slot]))
            bounds_pq.append(bq)

        rou = SUMO_DIR / f"loso_{held}.rou.xml"
        rep = pfe.calibrate(cand_path, rou, targets, bounds_pq, priors_pq)
        ed = SUMO_DIR / f"loso_ed_{held}.xml"
        run_meso(rou, SUMO_DIR / f"loso_ed_{held}.xml", duration_s)

        st = {"edges": {}, "pfe_geh_pct": rep["geh_pct"]}
        for e in held_edges:
            meas = np.array([full[i].get(e, np.nan) for i in range(nq)])
            sim = simulated_series(ed, e, nq)
            ok = ~np.isnan(meas)
            m_tot, s_tot = float(meas[ok].sum()), float(sim[ok].sum())
            # hourly GEH at the held-out edge
            geh_ok = geh_n = 0
            for h in range(0, nq - 3, 4):
                mm, ss = float(np.nansum(meas[h:h+4])), float(sim[h:h+4].sum())
                if mm + ss > 0:
                    geh = np.sqrt(2 * (ss - mm) ** 2 / (ss + mm))
                    geh_n += 1
                    geh_ok += geh < 5
            st["edges"][e] = {
                "measured_total": round(m_tot), "simulated_total": round(s_tot),
                "ratio": round(s_tot / max(m_tot, 1), 3),
                "geh_ok_pct": round(100 * geh_ok / max(1, geh_n), 1),
            }
            print(f"  utan {held:<6} {e:<26} kvot {s_tot/max(m_tot,1):>5.2f}  "
                  f"GEH<5 {100*geh_ok/max(1,geh_n):>5.1f}%")
        report["stations"][held] = st

    out = Path("web/data/loso_report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=1)
    ratios = [e["ratio"] for s in report["stations"].values()
              for e in s["edges"].values()]
    print(f"\nLOSO-kvoter: min {min(ratios):.2f}  median "
          f"{sorted(ratios)[len(ratios)//2]:.2f}  max {max(ratios):.2f}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
