"""
Predict the direction split for the Gothenburg sensor edges and write
sumo/direction_split.json — consumed by build_sumo_demand.py.

Usage (after train.py):
  python3 -m dirsplit.predict

Each Total sensor pair is predicted by ITS OWN locally weighted model
(trained toward that sensor's street characteristics) at three quantiles:
  q50 — the point estimate used as the default split
  q10/q90 — the uncertainty interval; build_sumo_demand builds demand
  VARIANTS at these bounds so the Monte Carlo scenario spread (and the
  confidence number on the map) includes direction uncertainty.

Mechanics per pair: predict quantile shares for the FIRST directed edge;
the second edge gets the complement (1 − q mirrors the quantiles). The pair
therefore always sums to 1 at every quantile. Hourly → 96 slots by linear
interpolation, clamped to [0.1, 0.9].

Predictions are ESTIMATES and labelled as such, with the coverage status
(applicability domain) attached per edge. New sensors in network.geojson
are picked up automatically after a retrain.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

from .config import COVERAGE_REPORT
from .features import edge_bearing_from_graph, edge_features, load_city_graph
from .train import FEATURE_NAMES, MODEL_PATH, PROFILE_FEATURES

GEO_PATH     = Path("web/data/network.geojson")
PROFILE_PATH = Path("web/data/normal_profile.json")
OUT_PATH     = Path("sumo/direction_split.json")

CLAMP = (0.1, 0.9)


def sensor_profile_features(profiles: dict, edge_id: str) -> dict[str, float]:
    """Same shape statistics as dataset.profile_features, from the September
    normal profile (96 slots → hourly)."""
    p = profiles.get(edge_id)
    if not p or not p.get("weekday"):
        return {"am_pm_ratio": 1.0, "peak_hour_am": 8.0,
                "peak_hour_pm": 16.0, "weekend_ratio": 0.3}

    def hourly(arr):
        return {h: (lambda v: sum(v) / len(v) * 4 if v else 0.0)
                ([x for x in arr[h * 4:(h + 1) * 4] if x is not None])
                for h in range(24)}

    wd = hourly(p["weekday"])
    we = hourly(p.get("weekend") or [None] * 96)
    am = max(range(5, 13),  key=lambda h: wd[h])
    pm = max(range(13, 21), key=lambda h: wd[h])
    am_sum = sum(wd[h] for h in range(6, 10))
    pm_sum = sum(wd[h] for h in range(15, 19))
    wd_all, we_all = sum(wd.values()), sum(we.values())
    return {
        "am_pm_ratio":   round(am_sum / pm_sum, 4) if pm_sum > 0 else 1.0,
        "peak_hour_am":  float(am),
        "peak_hour_pm":  float(pm),
        "weekend_ratio": round(we_all / wd_all, 4) if wd_all > 0 else 0.3,
    }


def hourly_to_slots(hourly: np.ndarray) -> list[float]:
    slots = np.arange(96) / 4.0
    xs = np.arange(25, dtype=float)
    ys = np.append(hourly, hourly[0])
    return np.interp(slots, xs, ys).tolist()


def main() -> None:
    with open(MODEL_PATH, "rb") as f:
        pkg = pickle.load(f)
    sensors_models = pkg["sensors"]

    with open(GEO_PATH) as f:
        geo = json.load(f)
    with open(PROFILE_PATH) as f:
        profiles = json.load(f)["profiles"]
    coverage = {}
    if COVERAGE_REPORT.exists():
        with open(COVERAGE_REPORT) as f:
            coverage = {e["edge"]: e["status"] for e in json.load(f)["edges"]}

    G = load_city_graph("goteborg")

    sensors: dict[str, list[dict]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id") and p.get("level") == "Total":
            c = feat["geometry"]["coordinates"]
            p["_mid_lat"] = (c[0][1] + c[-1][1]) / 2
            p["_mid_lon"] = (c[0][0] + c[-1][0]) / 2
            sensors.setdefault(str(p["sensor_id"]), []).append(p)

    def edge_feats(props: dict) -> dict:
        u, v, k = map(int, props["id"].split("_"))
        data = G.get_edge_data(u, v, k)
        bearing = edge_bearing_from_graph(G, u, v, k, data)
        return edge_features("goteborg", props["_mid_lat"], props["_mid_lon"],
                             bearing, data)

    def predict_hours(model, feats: dict, prof: dict) -> np.ndarray:
        rows = [[feats[c] for c in FEATURE_NAMES]
                + [prof[c] for c in PROFILE_FEATURES]
                + [np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24), 0.0]
                for h in range(24)]
        return np.clip(model.predict(np.array(rows)), 0.0, 1.0)

    result: dict[str, dict] = {}
    print(f"{'Sensor':<8} {'kl 07 (q10–q50–q90)':>22} {'kl 16 (q10–q50–q90)':>22}")
    for sid, pair in sorted(sensors.items()):
        if len(pair) != 2:
            print(f"{sid:<8} skipped — {len(pair)} edges")
            continue
        if sid not in sensors_models:
            print(f"{sid:<8} skipped — no model (retrain: make dirsplit-train)")
            continue
        prof = sensor_profile_features(profiles, pair[0]["id"])

        # The model is trained on toward-centre directions ONLY — orient the
        # pair so the toward-centre edge is predicted, the other mirrored.
        f0, f1 = edge_feats(pair[0]), edge_feats(pair[1])
        if f1["radial_cos"] > f0["radial_cos"]:
            pair = [pair[1], pair[0]]
            f0 = f1
        q = {name: np.clip(predict_hours(m, f0, prof), *CLAMP)
             for name, m in sensors_models[sid].items()}
        # quantile crossing guard: enforce q10 ≤ q50 ≤ q90 per hour
        q10 = np.minimum.reduce([q["q10"], q["q50"], q["q90"]])
        q90 = np.maximum.reduce([q["q10"], q["q50"], q["q90"]])
        q50 = np.clip(q["q50"], q10, q90)

        # Shrinkage calibration from leave-city-out validation: the interval
        # keeps its width but is re-centred on the shrunk point estimate.
        lam = pkg.get("shrinkage_lambda", 1.0)
        q50_s = 0.5 + lam * (q50 - 0.5)
        shift = q50_s - q50
        q10, q50, q90 = (np.clip(a + shift, *CLAMP) for a in (q10, q50, q90))

        e0, e1 = pair[0]["id"], pair[1]["id"]
        s10, s50, s90 = (hourly_to_slots(a) for a in (q10, q50, q90))
        result[sid] = {
            "method": "dirsplit-lightgbm per-sensor quantile "
                      "(similarity-weighted, trained on Norwegian directional counts)",
            "coverage_status": {p["id"]: coverage.get(p["id"], "?") for p in pair},
            "edge_shares":     {e0: [round(x, 3) for x in s50],
                                e1: [round(1 - x, 3) for x in s50]},
            "edge_shares_q10": {e0: [round(x, 3) for x in s10],
                                e1: [round(1 - x, 3) for x in s90]},
            "edge_shares_q90": {e0: [round(x, 3) for x in s90],
                                e1: [round(1 - x, 3) for x in s10]},
            "note": "ESTIMATED interval by a model trained on cities with "
                    "measured directions — direction is not measured in "
                    "Gothenburg. q10/q90 feed demand variants for Monte Carlo.",
        }
        print(f"{sid:<8} {q10[7]:.2f}–{q50[7]:.2f}–{q90[7]:.2f}"
              f"{'':>8}{q10[16]:.2f}–{q50[16]:.2f}–{q90[16]:.2f}")

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=1)
    print(f"Wrote {OUT_PATH}  ({len(result)} Total sensors)")


if __name__ == "__main__":
    main()
