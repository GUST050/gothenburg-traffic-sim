"""
Applicability-domain check: do our Gothenburg sensor edges lie inside the
training stations' feature distribution?

Usage:
  python3 -m dirsplit.coverage

For every matched Norwegian station (both directions) and every Gothenburg
sensor edge, compute the shared feature vector, z-standardise on the training
set, and measure each target edge's mean distance to its KNN_K nearest
training points. Reported as a percentile of the training set's own
leave-one-out kNN distances:
   ≤ 90th percentile — inside the cloud, transfer is defensible
   > 90th            — extrapolation, flag it (prediction gets a warning)

Automatically covers any NEW sensors added to network.geojson.

Writes data/dirsplit/coverage_report.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import osmnx as ox

from .config import COVERAGE_REPORT, KNN_K, STATIONS_OK
from .features import (FEATURE_NAMES, edge_bearing_from_graph, edge_features,
                       load_city_graph)

GEO_PATH = Path("web/data/network.geojson")


def training_matrix() -> tuple[np.ndarray, list[dict]]:
    with open(STATIONS_OK) as f:
        stations = [s for s in json.load(f) if s["matched"]]
    rows, meta = [], []
    for st in stations:
        G = load_city_graph(st["city"])
        u, v, k = map(int, st["osm_edge"].split("_"))
        data = G.get_edge_data(u, v, k)
        for heading, bearing in st["heading_bearings"].items():
            f = edge_features(st["city"], st["lat"], st["lon"], bearing, data)
            rows.append([f[n] for n in FEATURE_NAMES])
            meta.append({"station": st["id"], "city": st["city"],
                         "heading": heading})
    return np.array(rows, dtype=float), meta


def target_matrix() -> tuple[np.ndarray, list[dict]]:
    """Feature vectors for every sensor edge in network.geojson — new sensors
    are included automatically."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    G = load_city_graph("goteborg")
    rows, meta = [], []
    for feat in geo["features"]:
        p = feat["properties"]
        if not p.get("sensor_id"):
            continue
        u, v, k = map(int, p["id"].split("_"))
        data = G.get_edge_data(u, v, k)
        coords = feat["geometry"]["coordinates"]
        lat = (coords[0][1] + coords[-1][1]) / 2
        lon = (coords[0][0] + coords[-1][0]) / 2
        bearing = edge_bearing_from_graph(G, u, v, k, data)
        f = edge_features("goteborg", lat, lon, bearing, data)
        rows.append([f[n] for n in FEATURE_NAMES])
        meta.append({"edge": p["id"], "sensor": p["sensor_id"],
                     "name": p.get("name"), "features": f})
    return np.array(rows, dtype=float), meta


def knn_mean_dist(X_ref: np.ndarray, X_query: np.ndarray, k: int,
                  skip_self: bool) -> np.ndarray:
    """Mean Euclidean distance to the k nearest reference points."""
    out = np.empty(len(X_query))
    for i, q in enumerate(X_query):
        d = np.sqrt(((X_ref - q) ** 2).sum(axis=1))
        d.sort()
        out[i] = d[1:k + 1].mean() if skip_self else d[:k].mean()
    return out


def main() -> None:
    X_train, train_meta = training_matrix()
    X_tgt, tgt_meta = target_matrix()
    print(f"Training points: {len(X_train)} directed station-directions, "
          f"targets: {len(X_tgt)} sensor edges")

    mu, sd = X_train.mean(axis=0), X_train.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Zt = (X_train - mu) / sd
    Zq = (X_tgt - mu) / sd

    ref_d = knn_mean_dist(Zt, Zt, KNN_K, skip_self=True)   # training self-distances
    tgt_d = knn_mean_dist(Zt, Zq, KNN_K, skip_self=False)

    report = {"knn_k": KNN_K, "n_train": len(X_train),
              "feature_names": FEATURE_NAMES, "edges": []}
    print(f"\n{'Sensor':<7} {'Kant':<26} {'kNN-dist':>9} {'percentil':>10}  status")
    for meta, d in zip(tgt_meta, tgt_d):
        pctl = float((ref_d < d).mean() * 100)
        status = "OK" if pctl <= 90 else "EXTRAPOLATION"
        print(f"{meta['sensor']:<7} {meta['edge']:<26} {d:>9.2f} {pctl:>9.0f}%  {status}")
        report["edges"].append({**{k: v for k, v in meta.items() if k != 'features'},
                                "knn_dist": round(float(d), 3),
                                "train_percentile": round(pctl, 1),
                                "status": status,
                                "features": meta["features"]})

    with open(COVERAGE_REPORT, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {COVERAGE_REPORT}")


if __name__ == "__main__":
    main()
