"""
Estimate a time-of-day directional split for the "Total" sensors.

Run after build_features.py (needs normal_profile.json):
  python3 estimate_directions.py

The problem: five sensors deliver the SUM of both directions, and direction
is not recoverable from the data (no directional re-export is coming). But a
daily total profile is the sum of two directional profiles, and urban
commuter traffic has a well-known structure: a morning peak in one direction
(toward the centre / workplaces) and an afternoon peak in the other.

Model (unsupervised decomposition, least squares over a parameter grid):

    T(t) = base + A * gauss(t; muA, sigA) + B * gauss(t; muB, sigB)

  - base    — flat off-peak traffic, assumed symmetric (split 50/50)
  - AM term — assigned to the directed edge whose bearing points toward
              the city centre (Brunnsparken) — a PRIOR, not a measurement
  - PM term — assigned to the opposite edge

    split_toward_centre(t) = (base/2 + A*gA(t)) / T_fit(t),  clamped [0.2, 0.8]

If the fit is poor (R² < 0.5) the sensor falls back to a flat 50/50 split.
This is an ESTIMATE and is labelled as such everywhere downstream.

Writes:
  sumo/direction_split.json — {sensor_id: {edge_shares: {edge_id: [96 floats]},
                               r2, mu_am, mu_pm, edge_toward_centre}}
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

PROFILE_PATH = Path("web/data/normal_profile.json")
GEO_PATH     = Path("web/data/network.geojson")
OUT_PATH     = Path("sumo/direction_split.json")

# Brunnsparken — the de-facto centre traffic flows toward in the AM peak
CENTRE_LAT, CENTRE_LON = 57.7068, 11.9668

SPLIT_MIN, SPLIT_MAX = 0.2, 0.8
R2_FALLBACK = 0.5

HOURS = np.arange(96) / 4.0   # slot index → hour of day


def gauss(h: np.ndarray, mu: float, sig: float) -> np.ndarray:
    return np.exp(-0.5 * ((h - mu) / sig) ** 2)


def fit_am_pm(profile: np.ndarray) -> tuple[dict, float]:
    """
    Grid search (mu, sigma) for AM and PM components; solve base/A/B by
    non-negative least squares (lstsq + clip). Returns (params, r2).
    """
    best: dict | None = None
    best_sse = np.inf
    tss = float(((profile - profile.mean()) ** 2).sum())

    for mu_am in np.arange(6.5, 10.01, 0.5):
        for mu_pm in np.arange(14.0, 19.01, 0.5):
            for sig in (0.75, 1.0, 1.5, 2.0):
                X = np.column_stack([
                    np.ones(96),
                    gauss(HOURS, mu_am, sig),
                    gauss(HOURS, mu_pm, sig),
                ])
                coef, *_ = np.linalg.lstsq(X, profile, rcond=None)
                coef = np.clip(coef, 0, None)   # base, A, B all non-negative
                sse = float(((X @ coef - profile) ** 2).sum())
                if sse < best_sse:
                    best_sse = sse
                    best = {"base": coef[0], "A": coef[1], "B": coef[2],
                            "mu_am": mu_am, "mu_pm": mu_pm, "sig": sig}

    r2 = 1 - best_sse / tss if tss > 0 else 0.0
    return best, r2


def edge_bearing_deg(coords: list) -> float:
    """Bearing of a LineString (first→last point), cos-lat corrected. 0=N, 90=E."""
    (lon1, lat1), (lon2, lat2) = coords[0], coords[-1]
    dlat = lat2 - lat1
    dlon = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2(dlon, dlat)) % 360


def bearing_to_centre_deg(coords: list) -> float:
    mid_lon = (coords[0][0] + coords[-1][0]) / 2
    mid_lat = (coords[0][1] + coords[-1][1]) / 2
    dlat = CENTRE_LAT - mid_lat
    dlon = (CENTRE_LON - mid_lon) * math.cos(math.radians((CENTRE_LAT + mid_lat) / 2))
    return math.degrees(math.atan2(dlon, dlat)) % 360


def ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def main() -> None:
    OUT_PATH.parent.mkdir(exist_ok=True)

    with open(GEO_PATH) as f:
        geo = json.load(f)
    with open(PROFILE_PATH) as f:
        profiles = json.load(f)["profiles"]

    # sensor → [(edge_id, coords)], Total sensors only (level == "Total")
    sensors: dict[str, list] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("sensor_id") and p.get("level") == "Total":
            sensors.setdefault(str(p["sensor_id"]), []).append(
                (p["id"], feat["geometry"]["coordinates"])
            )

    result: dict[str, dict] = {}
    print(f"{'Sensor':<8} {'R²':>5}  {'AM-topp':>8} {'PM-topp':>8}  mot centrum")
    for sid, edges in sorted(sensors.items()):
        if len(edges) != 2:
            print(f"{sid:<8} skipped — {len(edges)} edges (expected 2)")
            continue

        # September weekday profile (same array on both edges — it's the sum)
        prof_raw = profiles.get(edges[0][0], {}).get("weekday")
        if not prof_raw:
            print(f"{sid:<8} skipped — no weekday profile")
            continue
        profile = np.array([v if v is not None else np.nan for v in prof_raw])
        # fill occasional gaps by interpolation
        idx = np.arange(96)
        mask = ~np.isnan(profile)
        profile = np.interp(idx, idx[mask], profile[mask])

        params, r2 = fit_am_pm(profile)

        # Which directed edge points toward the centre?
        diffs = [ang_diff(edge_bearing_deg(c), bearing_to_centre_deg(c))
                 for _, c in edges]
        toward_i  = int(np.argmin(diffs))
        toward_id = edges[toward_i][0]
        other_id  = edges[1 - toward_i][0]

        if r2 < R2_FALLBACK:
            split = np.full(96, 0.5)
            print(f"{sid:<8} {r2:>5.2f}  — weak fit, falling back to 50/50")
        else:
            dir_c = params["base"] / 2 + params["A"] * gauss(HOURS, params["mu_am"], params["sig"])
            dir_o = params["base"] / 2 + params["B"] * gauss(HOURS, params["mu_pm"], params["sig"])
            split = np.clip(dir_c / np.maximum(dir_c + dir_o, 1e-9),
                            SPLIT_MIN, SPLIT_MAX)
            print(f"{sid:<8} {r2:>5.2f}  {params['mu_am']:>7.1f}h {params['mu_pm']:>7.1f}h  "
                  f"{toward_id}  (split 07:30={split[30]:.2f}, 16:30={split[66]:.2f})")

        result[sid] = {
            "r2": round(float(r2), 3),
            "mu_am": params["mu_am"], "mu_pm": params["mu_pm"],
            "edge_toward_centre": toward_id,
            "edge_shares": {
                toward_id: [round(float(s), 3) for s in split],
                other_id:  [round(float(1 - s), 3) for s in split],
            },
            "note": "ESTIMATED time-of-day split (AM/PM decomposition + "
                    "toward-centre prior) — direction is not measured.",
        }

    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=1)
    print(f"\nWrote {OUT_PATH}  ({len(result)} Total sensors)")


if __name__ == "__main__":
    main()
