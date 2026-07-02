"""
Predict the direction split for the Gothenburg sensor edges and write
sumo/direction_split.json — SAME format as estimate_directions.py, so
build_sumo_demand.py consumes it unchanged.

Usage (after train.py):
  python3 -m dirsplit.predict

Per Total sensor (pair of directed edges):
  1. compute the shared edge features for both directions (features.py),
  2. profile-shape features from the sensor's own September profile
     (normal_profile.json — the total, both directions, same semantics as
     the training stations' totals),
  3. model predicts a share per hour for EACH direction independently;
     the pair is then normalised (s1/(s1+s2)) so shares sum to 1,
  4. hourly → 96 quarter slots by linear interpolation,
  5. clamped to [0.1, 0.9] — the model must not zero out a direction.

Every prediction is labelled ESTIMATED, with the coverage status from
coverage_report.json attached. New sensors in network.geojson are handled
automatically.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

from .config import COVERAGE_REPORT, DATA_DIR
from .features import edge_bearing_from_graph, edge_features, load_city_graph
from .train import FEATURE_NAMES, MODEL_PATH, PROFILE_FEATURES

GEO_PATH     = Path("web/data/network.geojson")
PROFILE_PATH = Path("web/data/normal_profile.json")
OUT_PATH     = Path("sumo/direction_split.json")

CLAMP = (0.1, 0.9)


def sensor_profile_features(profiles: dict, edge_id: str) -> dict[str, float]:
    """Same shape statistics as dataset.profile_features, but computed from
    the September normal profile (96 slots → hourly)."""
    p = profiles.get(edge_id)
    if not p or not p.get("weekday"):
        return {"am_pm_ratio": 1.0, "peak_hour_am": 8.0,
                "peak_hour_pm": 16.0, "weekend_ratio": 0.3}

    def hourly(arr):
        out = {}
        for h in range(24):
            vals = [v for v in arr[h * 4:(h + 1) * 4] if v is not None]
            out[h] = sum(vals) / len(vals) * 4 if vals else 0.0
        return out

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
    """24 hourly values → 96 quarter slots, linear interpolation, wrapping."""
    slots = np.arange(96) / 4.0                      # slot centre in hours
    xs = np.arange(25, dtype=float)                  # 0..24 (wrap point)
    ys = np.append(hourly, hourly[0])
    return np.interp(slots, xs, ys).tolist()


def main() -> None:
    with open(MODEL_PATH, "rb") as f:
        pkg = pickle.load(f)
    model, input_cols = pkg["model"], pkg["input_cols"]

    with open(GEO_PATH) as f:
        geo = json.load(f)
    with open(PROFILE_PATH) as f:
        profiles = json.load(f)["profiles"]
    coverage = {}
    if COVERAGE_REPORT.exists():
        with open(COVERAGE_REPORT) as f:
            coverage = {e["edge"]: e["status"]
                        for e in json.load(f)["edges"]}

    G = load_city_graph("goteborg")

    # Group directed edges per sensor (Total = 2 edges, S = 1)
    sensors: dict[str, list[dict]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id") and p.get("level") == "Total":
            sensors.setdefault(str(p["sensor_id"]), []).append(p)

    def predict_hours(props: dict, prof: dict) -> np.ndarray:
        u, v, k = map(int, props["id"].split("_"))
        data = G.get_edge_data(u, v, k)
        coords_bearing = edge_bearing_from_graph(G, u, v, k, data)
        coords = None  # midpoint from geojson geometry below
        feats = edge_features(
            "goteborg",
            props["_mid_lat"], props["_mid_lon"], coords_bearing, data,
        )
        rows = []
        for h in range(24):
            rows.append([feats[c] for c in FEATURE_NAMES]
                        + [prof[c] for c in PROFILE_FEATURES]
                        + [np.sin(2 * np.pi * h / 24),
                           np.cos(2 * np.pi * h / 24), 0.0])  # weekday
        return np.clip(model.predict(np.array(rows)), 0.0, 1.0)

    # attach midpoints from geojson geometry
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id"):
            c = feat["geometry"]["coordinates"]
            p["_mid_lat"] = (c[0][1] + c[-1][1]) / 2
            p["_mid_lon"] = (c[0][0] + c[-1][0]) / 2

    result: dict[str, dict] = {}
    print(f"{'Sensor':<8} {'kl 07':>6} {'kl 08':>6} {'kl 16':>6} {'kl 17':>6}   riktad kant (andel = denna kant)")
    for sid, pair in sorted(sensors.items()):
        if len(pair) != 2:
            print(f"{sid:<8} skipped — {len(pair)} edges")
            continue
        prof = sensor_profile_features(profiles, pair[0]["id"])
        s0 = predict_hours(pair[0], prof)
        s1 = predict_hours(pair[1], prof)
        share0 = np.clip(s0 / np.maximum(s0 + s1, 1e-9), *CLAMP)  # normalise pair

        slots0 = hourly_to_slots(share0)
        result[sid] = {
            "method": "dirsplit-lightgbm (trained on Norwegian directional counts)",
            "coverage_status": {p["id"]: coverage.get(p["id"], "?") for p in pair},
            "edge_shares": {
                pair[0]["id"]: [round(s, 3) for s in slots0],
                pair[1]["id"]: [round(1 - s, 3) for s in slots0],
            },
            "note": "ESTIMATED by a model trained on cities with measured "
                    "directions — direction is not measured in Gothenburg.",
        }
        print(f"{sid:<8} {share0[7]:>6.2f} {share0[8]:>6.2f} {share0[16]:>6.2f} "
              f"{share0[17]:>6.2f}   {pair[0]['id']}")

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=1)
    print(f"Wrote {OUT_PATH}  ({len(result)} Total sensors)")


if __name__ == "__main__":
    main()
