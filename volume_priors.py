"""
Level-3 network-wide volume priors: "a street of this type carries roughly
X veh/h at this hour" — learned from the Norwegian stations, applied WEAKLY
to every unconstrained edge so side streets get plausible levels instead of
the LP's parsimony-zero.

Run after dirsplit dataset + build_data:
  python3 volume_priors.py

Model: LightGBM on the dirsplit training table — target log(1 + veh/h per
direction), inputs = the shared street features + hour. Leave-city-out MAE
is reported so the prior's uncertainty is known, and the q10/q90 spread sets
the per-edge weight in the PFE (wide band ⇒ weak pull).

Excluded: measured edges and their opposite twins (they have stronger
priors/constraints already).

Writes sumo/volume_priors.json  {edge: {veh_q, low, high}} (96 slots).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from dirsplit.config import DATA_DIR
from dirsplit.features import (FEATURE_NAMES, edge_bearing_from_graph,
                               edge_features, load_city_graph)

TABLE = DATA_DIR / "training_table.csv"
OUT   = Path("sumo/volume_priors.json")

INPUTS = FEATURE_NAMES + ["hour_sin", "hour_cos"]
QUANTS = (0.1, 0.5, 0.9)


def make_model(alpha):
    import lightgbm as lgb
    return lgb.LGBMRegressor(objective="quantile", alpha=alpha,
                             n_estimators=300, learning_rate=0.05,
                             max_depth=5, num_leaves=31, subsample=0.8,
                             colsample_bytree=0.8, min_child_samples=20,
                             random_state=42, n_jobs=-1, verbose=-1)


def load_rows():
    """One row per (station, heading, hour): directed veh/h level."""
    X, y, cities, keys = [], [], [], []
    with open(TABLE) as f:
        for r in csv.DictReader(f):
            if r["is_weekend"] != "0":
                continue
            h = float(r["hour"])
            # directed level = total × this direction's share
            veh_h = float(r["mean_total_veh_h"]) * float(r["share"])
            X.append([float(r[c]) for c in FEATURE_NAMES]
                     + [np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24)])
            y.append(np.log1p(veh_h))
            cities.append(r["city"])
            keys.append((r["station_id"], r["heading"]))
    return np.array(X), np.array(y), np.array(cities), keys


def main() -> None:
    X, y, cities, keys = load_rows()
    print(f"{len(y)} rows from {len(set(keys))} station-directions")

    # Leave-city-out honesty check (q50 model)
    for held in sorted(set(cities)):
        tr, te = cities != held, cities == held
        m = make_model(0.5).fit(X[tr], y[tr])
        pred = m.predict(X[te])
        mae_log = float(np.mean(np.abs(pred - y[te])))
        print(f"  LOCO {held:<10} MAE(log) {mae_log:.3f} "
              f"(≈ faktor {np.exp(mae_log):.2f} fel i nivå)")

    models = {q: make_model(q).fit(X, y) for q in QUANTS}

    # Predict for every unconstrained edge in our network
    with open("web/data/network.geojson") as f:
        geo = json.load(f)
    measured = {f_["properties"]["id"] for f_ in geo["features"]
                if f_["properties"].get("sensor_id")}
    with open("sumo/prior_flows.json") as f:
        opposite = set(json.load(f)["edges"])

    G = load_city_graph("goteborg")
    out: dict[str, dict] = {}
    hours = np.arange(24)
    hcols = np.column_stack([np.sin(2 * np.pi * hours / 24),
                             np.cos(2 * np.pi * hours / 24)])

    n_done = 0
    for u, v, k, data in G.edges(keys=True, data=True):
        e = f"{u}_{v}_{k}"
        if e in measured or e in opposite:
            continue
        lat = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        lon = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        bearing = edge_bearing_from_graph(G, u, v, k, data)
        feats = edge_features("goteborg", lat, lon, bearing, data)
        base = np.array([[feats[c] for c in FEATURE_NAMES]] * 24)
        Xe = np.hstack([base, hcols])
        q = {qq: np.expm1(models[qq].predict(Xe)) / 4.0 for qq in QUANTS}  # veh/quarter
        lo = np.minimum.reduce(list(q.values()))
        hi = np.maximum.reduce(list(q.values()))
        mid = np.clip(q[0.5], lo, hi)

        def slots(a):
            return np.repeat(np.clip(a, 0, None), 4)[:96].round(1).tolist()

        out[e] = {"veh_q": slots(mid), "low": slots(lo), "high": slots(hi)}
        n_done += 1
        if n_done % 500 == 0:
            print(f"  {n_done} edges …")

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"edges": out,
                   "note": "Level-3 WEAK network-wide volume priors — learned "
                           "street-type levels; soft pull only, never targets."},
                  f)
    print(f"Wrote {OUT}  ({n_done} edges)")


if __name__ == "__main__":
    main()
