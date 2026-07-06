"""
Level-3 priors: learned flow estimates for the UNMEASURED opposite directions
at single-direction stations.

Run after dirsplit training + build_data:
  python3 prior_flows.py [--date 2025-09-16]

For each single-direction sensor (1074 V, 1076 S, 133 V, 134 SO, 2276 V) the
opposite carriageway/direction is unmeasured. The validated dirsplit model
(trained on Norwegian directional data, leave-city-out validated, shrinkage
λ applied) predicts the measured direction's share s(t) of the two-way total;
the prior for the opposite is then

    prior_opp(t) = measured(t) · (1 − s(t)) / s(t)     [q10/q90 band likewise]

This is explicitly a PRIOR (level 3): Agent C pulls toward it softly, inside
the level-2 bounds, and it can never override a measurement.

Writes sumo/prior_flows.json.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from build_data import find_antiparallel_edge
from dirsplit.features import edge_bearing_from_graph, edge_features, load_city_graph
from dirsplit.predict import (CLAMP, hourly_to_slots, sensor_profile_features)
from dirsplit.train import FEATURE_NAMES, MODEL_PATH, PROFILE_FEATURES

GEO_PATH     = Path("web/data/network.geojson")
FLOWS_PATH   = Path("web/data/flows.json")
PROFILE_PATH = Path("web/data/normal_profile.json")
OUT_PATH     = Path("sumo/prior_flows.json")


def opposite_edge(G, e: str, lat: float, lon: float) -> str | None:
    u, v, k = map(int, e.split("_"))
    if G.has_edge(v, u):
        return f"{v}_{u}_{min(G[v][u].keys())}"
    opp = find_antiparallel_edge(G, u, v, k, lat, lon, max_dist_m=80)
    return f"{opp[0]}_{opp[1]}_{opp[2]}" if opp else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default="2025-09-16")
    args = ap.parse_args()

    with open(MODEL_PATH, "rb") as f:
        pkg = pickle.load(f)
    lam = pkg.get("shrinkage_lambda", 1.0)

    with open(GEO_PATH) as f:
        geo = json.load(f)
    with open(FLOWS_PATH) as f:
        flows = json.load(f)["flows"]
    with open(PROFILE_PATH) as f:
        profiles = json.load(f)["profiles"]

    G = load_city_graph("goteborg")
    epoch = pd.Timestamp("2025-01-01")
    q0 = int((pd.Timestamp(args.date) - epoch) / pd.Timedelta(minutes=15))

    result: dict[str, dict] = {}
    print(f"{'Sensor':<7} {'mätt kant → motriktning':<58} {'andel kl 08 (q10–q90)'}")
    for feat in geo["features"]:
        p = feat["properties"]
        sid, level = p.get("sensor_id"), p.get("level")
        if not sid or level in (None, "Total"):
            continue   # only single-direction stations have an unmeasured twin
        if sid not in pkg["sensors"]:
            print(f"{sid:<7} no model — run dirsplit training")
            continue

        c = feat["geometry"]["coordinates"]
        mid_lat = (c[0][1] + c[-1][1]) / 2
        mid_lon = (c[0][0] + c[-1][0]) / 2
        e_meas = p["id"]
        e_opp = opposite_edge(G, e_meas, mid_lat, mid_lon)
        if e_opp is None:
            print(f"{sid:<7} {e_meas}: no opposite edge (one-way street?) — skipped")
            continue

        # Predict the toward-centre share hourly, orient it to the measured edge
        u, v, k = map(int, e_meas.split("_"))
        data = G.get_edge_data(u, v, k)
        bearing = edge_bearing_from_graph(G, u, v, k, data)
        feats = edge_features("goteborg", mid_lat, mid_lon, bearing, data)
        prof = sensor_profile_features(profiles, e_meas)

        rows = [[feats[cn] for cn in FEATURE_NAMES]
                + [prof[cn] for cn in PROFILE_FEATURES]
                + [np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24), 0.0]
                for h in range(24)]
        q = {name: np.clip(m.predict(np.array(rows)), *CLAMP)
             for name, m in pkg["sensors"][sid].items()}
        q10 = np.minimum.reduce(list(q.values()))
        q90 = np.maximum.reduce(list(q.values()))
        q50 = np.clip(q["q50"], q10, q90)
        # shrinkage toward 50/50, interval re-centred (same rule as predict.py)
        q50s  = 0.5 + lam * (q50 - 0.5)
        shift = q50s - q50
        q10, q50, q90 = (np.clip(a + shift, *CLAMP) for a in (q10, q50, q90))

        s10, s50, s90 = (np.array(hourly_to_slots(a)) for a in (q10, q50, q90))
        meas = np.array([v if v is not None else np.nan
                         for v in flows[e_meas][q0:q0 + 96]], dtype=float)

        def opp_series(share):
            with np.errstate(divide="ignore", invalid="ignore"):
                s = meas * (1 - share) / share
            return [None if not np.isfinite(x) else round(float(x), 1)
                    for x in s]

        result[e_opp] = {
            "sensor": sid, "measured_edge": e_meas,
            "prior":     opp_series(s50),
            # wider share → SMALLER opposite flow, so q90 share gives the low band
            "prior_low":  opp_series(s90),
            "prior_high": opp_series(s10),
            "provenance": "level-3 learned prior (dirsplit model, shrinkage "
                          f"λ={lam:.2f}) applied to the measured twin",
        }
        print(f"{sid:<7} {e_meas} → {e_opp:<24} "
              f"{1-q50[8]:.2f} ({1-q90[8]:.2f}–{1-q10[8]:.2f})")

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"date": args.date, "n_quarters": 96, "edges": result}, f)
    print(f"Wrote {OUT_PATH}  ({len(result)} opposite-direction priors)")


if __name__ == "__main__":
    main()
