"""Out-of-sample validation of the direction-split model's published claims.

`train.py` reports a shrunk pooled domain MAE that beats the 50/50 baseline.
That number fits the shrinkage coefficient lambda AND scores it on the same
pooled held-out pairs, so it cannot answer "does this generalize". This module
answers three questions the training report structurally cannot:

  GATE M   Nested lambda — fit lambda on three cities, score it on the fourth.
           Plus a station-level bootstrap CI, because rows within a station are
           correlated and the row count overstates the evidence.

  COVERAGE Does [q10, q90], after the crossing guard and the shrinkage
           re-centring that `predict.py` actually deploys, contain the nominal
           80% of held-out observations?

  DOMAIN   Where does each per-sensor kernel sit relative to the training
           cloud? `load_table()` keeps only toward-centre rows (radial_cos > 0)
           while `coverage.target_matrix()` returns whichever directed edge
           carries the sensor_id — for a single-direction station that can be
           the away-from-centre carriageway.

Nothing here writes to `data/dirsplit/`; the model and its training report are
inputs, not outputs. Results go to `validation/` as an append-only evidence
artifact, matching the repository's other gate records.

Usage:
    python3 -m dirsplit.validate                  # full run, writes validation/
    python3 -m dirsplit.validate --boot 0         # skip the bootstrap
    python3 -m dirsplit.validate --out /tmp/x.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# predict.py clamps every published share into this band before use.
CLAMP = (0.1, 0.9)
NOMINAL_COVERAGE = 0.80
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "validation" / "dirsplit_gate_m.json"


# ── Pure functions (numpy only — unit-testable without lightgbm/osmnx) ────────

def fit_lambda(dp: np.ndarray, dy: np.ndarray) -> float:
    """James-Stein shrinkage slope through the origin, as `train.py` fits it.

    dp = predicted deviation from 0.5, dy = observed deviation from 0.5.
    Clipped to [0, 1]: 0 collapses the model to 50/50, 1 keeps it raw.
    """
    dp = np.asarray(dp, dtype=float)
    dy = np.asarray(dy, dtype=float)
    return float(np.clip((dp @ dy) / max(dp @ dp, 1e-12), 0.0, 1.0))


def apply_deployment_interval(q10, q50, q90, lam: float,
                              clamp: tuple[float, float] = CLAMP):
    """Reproduce `predict.py`'s post-processing exactly.

    Three steps, in this order:
      1. clamp each raw quantile into the published band;
      2. crossing guard — the interval is the min/max of all three, so a
         model whose q10 exceeds its q90 cannot invert the interval;
      3. shrinkage re-centring — q50 is pulled toward 0.5 by lambda and the
         WHOLE interval is shifted by the same amount, so the width survives
         the shrinkage unchanged.

    Returns (lo, mid, hi) after the final clamp.
    """
    a10, a50, a90 = (np.clip(np.asarray(a, dtype=float), *clamp)
                     for a in (q10, q50, q90))
    lo = np.minimum.reduce([a10, a50, a90])
    hi = np.maximum.reduce([a10, a50, a90])
    mid = np.clip(a50, lo, hi)
    shift = (0.5 + lam * (mid - 0.5)) - mid
    lo, mid, hi = (np.clip(v + shift, *clamp) for v in (lo, mid, hi))
    return lo, mid, hi


def interval_coverage(lo, hi, y) -> float:
    """Fraction of observations inside the closed interval [lo, hi]."""
    lo, hi, y = (np.asarray(a, dtype=float) for a in (lo, hi, y))
    if len(y) == 0:
        return float("nan")
    return float(((y >= lo) & (y <= hi)).mean())


def nested_lambda_scores(by_city: dict[str, dict[str, np.ndarray]]) -> dict:
    """Score each city with a lambda fitted only on the OTHER cities.

    `by_city[city]` carries {"dp": ..., "dy": ...} — the held-out domain
    predictions for that city, produced by models that never saw it. The
    leakage this closes is in the SHRINKAGE step, not the model step: the
    published figure fits one scalar on all four cities and then reports its
    error on those same rows.
    """
    cities = sorted(by_city)
    if len(cities) < 2:
        raise ValueError("nested lambda needs at least two cities")

    per_city, pred, truth = [], [], []
    for held in cities:
        others = [c for c in cities if c != held]
        lam = fit_lambda(np.concatenate([by_city[c]["dp"] for c in others]),
                         np.concatenate([by_city[c]["dy"] for c in others]))
        dp, dy = by_city[held]["dp"], by_city[held]["dy"]
        mae_nested = float(np.mean(np.abs(lam * dp - dy)))
        mae_5050 = float(np.mean(np.abs(dy)))
        per_city.append({
            "city": held,
            "lambda_fitted_on_other_cities": round(lam, 3),
            "n_rows": int(len(dp)),
            "mae_nested_lambda": round(mae_nested, 4),
            "mae_5050": round(mae_5050, 4),
            "impr_pct": round(100 * (1 - mae_nested / mae_5050), 1)
            if mae_5050 > 0 else None,
        })
        pred.append(lam * dp)
        truth.append(dy)

    pred, truth = np.concatenate(pred), np.concatenate(truth)
    mae_nested = float(np.mean(np.abs(pred - truth)))
    mae_5050 = float(np.mean(np.abs(truth)))
    return {
        "per_city": per_city,
        "pooled_mae_nested": round(mae_nested, 4),
        "pooled_mae_5050": round(mae_5050, 4),
        "pooled_impr_pct": round(100 * (1 - mae_nested / mae_5050), 2)
        if mae_5050 > 0 else None,
    }


def bootstrap_nested_delta(stations: list, station_city: dict,
                           station_dp: dict, station_dy: dict,
                           n_boot: int = 2000, seed: int = 20260816) -> dict:
    """Station-level bootstrap CI for MAE(nested lambda) - MAE(50/50).

    The resampling unit is the station-direction, not the row: hourly rows
    within a station are strongly correlated, so resampling rows would
    manufacture precision the data does not have. Negative delta means the
    model beats 50/50.
    """
    rng = np.random.default_rng(seed)

    def delta(subset: list) -> float | None:
        by_city: dict[str, list] = {}
        for st in subset:
            by_city.setdefault(station_city[st], []).append(st)
        if len(by_city) < 2:
            return None
        num_shrunk = num_base = 0.0
        n = 0
        for st in subset:
            held = station_city[st]
            others = [s for c, group in by_city.items() if c != held
                      for s in group]
            lam = fit_lambda(np.concatenate([station_dp[s] for s in others]),
                             np.concatenate([station_dy[s] for s in others]))
            dp, dy = station_dp[st], station_dy[st]
            num_shrunk += float(np.abs(lam * dp - dy).sum())
            num_base += float(np.abs(dy).sum())
            n += len(dy)
        return (num_shrunk - num_base) / n if n else None

    point = delta(stations)
    draws = []
    for _ in range(n_boot):
        sample = [stations[i] for i in rng.integers(0, len(stations),
                                                    len(stations))]
        d = delta(sample)
        if d is not None:
            draws.append(d)

    out = {"n_stations": len(stations), "n_boot_effective": len(draws),
           "delta_point": round(float(point), 5) if point is not None else None}
    if draws:
        arr = np.array(draws)
        lo, hi = np.percentile(arr, [2.5, 97.5])
        out["delta_ci95"] = [round(float(lo), 5), round(float(hi), 5)]
        out["p_model_better"] = round(float((arr < 0).mean()), 3)
        out["ci_excludes_zero"] = bool(lo > 0 or hi < 0)
    return out


def summarize_orientation(target_radial_cos: dict[str, float],
                          train_radial_cos_min: float) -> dict:
    """Flag sensors whose kernel centre falls outside the training support.

    `load_table()` trains on toward-centre rows only, so the training cloud
    starts just above zero. A target vector with radial_cos below that minimum
    is an extrapolation on the feature the model leans on hardest.
    """
    flagged = [sid for sid, rc in target_radial_cos.items()
               if rc < train_radial_cos_min]
    return {
        "train_radial_cos_min": round(float(train_radial_cos_min), 3),
        "sensors": [{"sensor": sid, "target_radial_cos": round(float(rc), 3),
                     "outside_training_support": rc < train_radial_cos_min}
                    for sid, rc in sorted(target_radial_cos.items())],
        "n_outside_training_support": len(flagged),
        "sensors_outside": sorted(flagged),
    }


# ── Leave-city-out machinery (needs lightgbm + the OSM graph) ─────────────────

def _fit_predict_stations(X, y, Xs_z, w_obs, keys, train_mask, station_keys,
                          bandwidth, alpha=None):
    """Per-station locally weighted held-out predictions, as `train.py` does:
    one model per held-out station, kernel-weighted toward THAT station."""
    from .train import kernel_weights, make_model

    per_station = {}
    for st_key in station_keys:
        te = np.array([k == st_key for k in keys])
        if not te.any():
            continue
        centre = Xs_z[te].mean(axis=0)
        w_loc = kernel_weights(Xs_z, centre, bandwidth)
        model = make_model(alpha=alpha)
        model.fit(X[train_mask], y[train_mask],
                  sample_weight=(w_obs * w_loc)[train_mask])
        per_station[st_key] = (np.clip(model.predict(X[te]), 0, 1), y[te])
    return per_station


def run_validation(n_boot: int = 2000) -> dict:
    """Full measurement. Returns the evidence record."""
    from .train import (load_table, target_static_features, kernel_weights,
                        N_STATIC, QUANTILES)
    from .features import FEATURE_NAMES

    X, y, cities, keys, w_obs = load_table()
    Xs = X[:, :N_STATIC]
    mu, sd = Xs.mean(axis=0), Xs.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs_z = (Xs - mu) / sd

    d0 = np.sqrt(((Xs_z - Xs_z.mean(axis=0)) ** 2).sum(axis=1))
    bandwidth = float(np.median(d0))

    targets = target_static_features()
    tgt_centroid_z = (np.mean(list(targets.values()), axis=0) - mu) / sd
    d_tgt = np.sqrt(((Xs_z - tgt_centroid_z) ** 2).sum(axis=1))
    domain_row = d_tgt <= np.median(d_tgt)

    city_arr = np.array(cities)
    all_cities = sorted(set(cities))
    stations_by_city = {
        c: sorted({k for k, m in zip(keys, (city_arr == c) & domain_row) if m})
        for c in all_cities}

    record: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": "dirsplit_nested_lambda_and_coverage_v1",
        "n_rows": int(len(y)),
        "bandwidth": round(bandwidth, 3),
        "n_domain_rows": int(domain_row.sum()),
        "domain_stations_by_city": {c: len(s) for c, s in
                                    stations_by_city.items()},
    }

    # ── Point model: held-out domain predictions, kept per station ───────────
    station_dp, station_dy, station_city = {}, {}, {}
    by_city: dict[str, dict[str, np.ndarray]] = {}
    for held in all_cities:
        per_station = _fit_predict_stations(
            X, y, Xs_z, w_obs, keys, city_arr != held,
            stations_by_city[held], bandwidth)
        dp_parts, dy_parts = [], []
        for st_key, (pred, truth) in per_station.items():
            station_dp[st_key] = pred - 0.5
            station_dy[st_key] = truth - 0.5
            station_city[st_key] = held
            dp_parts.append(pred - 0.5)
            dy_parts.append(truth - 0.5)
        by_city[held] = {"dp": np.concatenate(dp_parts),
                         "dy": np.concatenate(dy_parts)}
        print(f"  fold {held}: {len(by_city[held]['dp'])} domain rows",
              flush=True)

    # Replication of the published (in-sample-lambda) figure, for comparison.
    dp_all = np.concatenate([by_city[c]["dp"] for c in all_cities])
    dy_all = np.concatenate([by_city[c]["dy"] for c in all_cities])
    lam_insample = fit_lambda(dp_all, dy_all)
    record["published_replication"] = {
        "lambda": round(lam_insample, 3),
        "pooled_mae_raw": round(float(np.mean(np.abs(dp_all - dy_all))), 4),
        "pooled_mae_shrunk": round(
            float(np.mean(np.abs(lam_insample * dp_all - dy_all))), 4),
        "pooled_mae_5050": round(float(np.mean(np.abs(dy_all))), 4),
        "note": "lambda is fitted on and scored against the same pooled rows",
    }

    record["gate_m_nested_lambda"] = nested_lambda_scores(by_city)
    if n_boot:
        record["gate_m_bootstrap"] = bootstrap_nested_delta(
            sorted(station_dp, key=str), station_city, station_dp, station_dy,
            n_boot=n_boot)

    # ── Interval coverage with the deployed post-processing ─────────────────
    report_path = REPO_ROOT / "data" / "dirsplit" / "train_report.json"
    lam_deployed = json.loads(report_path.read_text())["shrinkage"]["lambda"]

    per_city_cov, pooled = [], {"lo": [], "hi": [], "y": [], "raw": []}
    for held in all_cities:
        q = {}
        for alpha in QUANTILES:
            per_station = _fit_predict_stations(
                X, y, Xs_z, w_obs, keys, city_arr != held,
                stations_by_city[held], bandwidth, alpha=alpha)
            order = sorted(per_station, key=str)
            q[alpha] = np.concatenate([per_station[k][0] for k in order])
            truth = np.concatenate([per_station[k][1] for k in order])
        lo, _mid, hi = apply_deployment_interval(
            q[0.1], q[0.5], q[0.9], lam_deployed)
        lo_raw, _m, hi_raw = apply_deployment_interval(
            q[0.1], q[0.5], q[0.9], 1.0)      # lam=1 → no re-centring
        per_city_cov.append({
            "city": held, "n_rows": int(len(truth)),
            "coverage_deployed": round(interval_coverage(lo, hi, truth), 3),
            "coverage_before_shrinkage": round(
                interval_coverage(lo_raw, hi_raw, truth), 3),
            "median_width": round(float(np.median(hi - lo)), 4),
        })
        pooled["lo"].append(lo); pooled["hi"].append(hi)
        pooled["y"].append(truth)
        pooled["raw"].append((lo_raw, hi_raw))
        print(f"  coverage {held}: {per_city_cov[-1]['coverage_deployed']}",
              flush=True)

    yy = np.concatenate(pooled["y"])
    lo_all, hi_all = np.concatenate(pooled["lo"]), np.concatenate(pooled["hi"])
    lo_raw_all = np.concatenate([r[0] for r in pooled["raw"]])
    hi_raw_all = np.concatenate([r[1] for r in pooled["raw"]])
    record["coverage"] = {
        "nominal": NOMINAL_COVERAGE,
        "lambda_used": lam_deployed,
        "per_city": per_city_cov,
        "pooled_coverage_deployed": round(
            interval_coverage(lo_all, hi_all, yy), 3),
        "pooled_coverage_before_shrinkage": round(
            interval_coverage(lo_raw_all, hi_raw_all, yy), 3),
        "pooled_median_width": round(float(np.median(hi_all - lo_all)), 4),
    }

    # ── Domain/orientation diagnostic ───────────────────────────────────────
    rc_i = FEATURE_NAMES.index("radial_cos")
    orient = summarize_orientation(
        {sid: float(vec[rc_i]) for sid, vec in targets.items()},
        float(Xs[:, rc_i].min()))
    for entry in orient["sensors"]:
        centre_z = (targets[entry["sensor"]] - mu) / sd
        w = kernel_weights(Xs_z, centre_z, bandwidth)
        entry["effective_sample_size"] = round(
            float(w.sum() ** 2 / (w ** 2).sum()), 1)
        entry["effective_fraction_of_training_set"] = round(
            float(w.sum() ** 2 / (w ** 2).sum()) / len(y), 3)
    record["domain_orientation"] = orient
    return record


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--boot", type=int, default=2000,
                   help="bootstrap draws for the Gate M CI (0 disables)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    record = run_validation(n_boot=args.boot)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1) + "\n")

    nested = record["gate_m_nested_lambda"]
    cov = record["coverage"]
    print()
    print(f"published (in-sample lambda): "
          f"{record['published_replication']['pooled_mae_shrunk']} vs "
          f"{record['published_replication']['pooled_mae_5050']} for 50/50")
    print(f"GATE M (nested lambda):       {nested['pooled_mae_nested']} vs "
          f"{nested['pooled_mae_5050']} for 50/50 "
          f"({nested['pooled_impr_pct']:+.2f}%)")
    if "gate_m_bootstrap" in record:
        b = record["gate_m_bootstrap"]
        print(f"  delta {b['delta_point']:+.5f}  CI95 {b['delta_ci95']}  "
              f"P(model better) {b['p_model_better']}")
    print(f"COVERAGE: {cov['pooled_coverage_deployed']:.3f} "
          f"(nominal {cov['nominal']}), median width "
          f"{cov['pooled_median_width']}")
    print(f"orientation: {record['domain_orientation']['n_outside_training_support']}"
          f" sensor(s) outside training support")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
