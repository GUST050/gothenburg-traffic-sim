"""
Train the direction-split model: similarity-weighted, per-sensor, quantile.

Usage (after dataset.py):
  python3 -m dirsplit.train

Three ideas on top of plain LightGBM:

1. SIMILARITY WEIGHTING — every training row is weighted by how much its
   street resembles OUR sensor edges (Gaussian kernel on standardized static
   features, bandwidth = median distance). Motorways still contribute a
   little; central mixed-use streets dominate. This is classic covariate-
   shift correction: train where you predict.

2. PER-SENSOR MODELS — each Gothenburg sensor pair gets its own model,
   weighted toward ITS feature vector ("each road trained for itself").
   New sensors in network.geojson are picked up automatically on retrain.

3. QUANTILE REGRESSION (q10/q50/q90) — q50 is the normal trained point
   estimate; q10/q90 bound uncertainty and can be built as explicit variants.

Validation: leave-city-out as before, but the headline metric is computed
on the DOMAIN SUBSET — held-out stations whose features resemble our sensor
edges (distance below the median) — because that is the population we
actually predict. Each domain-subset station is predicted by a model
locally weighted toward that station, mirroring exactly what we do to
Gothenburg.

Writes:
  data/dirsplit/model.pkl          — {sensor_id: {q10,q50,q90}} + global fallback
  data/dirsplit/train_report.json  — overall + domain-subset MAE per city
"""

from __future__ import annotations

import csv
import json
import math
import pickle
import warnings

import numpy as np

from .config import DATA_DIR
from .dataset import OUT_PATH as TABLE_PATH
from .dataset import PROFILE_FEATURES
from .features import FEATURE_NAMES

MODEL_PATH  = DATA_DIR / "model.pkl"
REPORT_PATH = DATA_DIR / "train_report.json"

USE_PROFILE_FEATURES = False
INPUT_COLS = FEATURE_NAMES + ["hour_sin", "hour_cos", "is_weekend"]
QUANTILES   = (0.1, 0.5, 0.9)
N_STATIC    = len(FEATURE_NAMES)
TRAIN_PROTOCOL = "dirsplit_train_v2"
MIN_BANDWIDTH = 1e-6
MAX_KERNEL_EXPONENT = 700.0


def calibrate_shrinkage(predicted, observed) -> tuple[float, dict[str, float]]:
    """Fit a finite shrinkage coefficient and held-out diagnostics.

    Dataset v2 is much larger than the aggregate table used by the original
    trainer. The previous BLAS ``@`` reduction emitted overflow/invalid
    warnings during the first real v2 retrain. Accumulate explicitly in
    float64, reject non-finite inputs, and refuse a non-finite coefficient.
    """
    predicted = np.asarray(predicted, dtype=np.float64)
    observed = np.asarray(observed, dtype=np.float64)
    if predicted.shape != observed.shape or predicted.size == 0:
        raise ValueError("shrinkage needs equally shaped non-empty arrays")
    if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(observed)):
        raise ValueError("shrinkage inputs must be finite")
    dp = predicted - 0.5
    dy = observed - 0.5
    denominator = float(np.sum(dp * dp, dtype=np.float64))
    if not math.isfinite(denominator):
        raise ValueError("shrinkage denominator is not finite")
    if denominator < 1e-9:
        lam = 1.0
    else:
        numerator = float(np.sum(dp * dy, dtype=np.float64))
        if not math.isfinite(numerator):
            raise ValueError("shrinkage numerator is not finite")
        lam = float(np.clip(numerator / denominator, 0.0, 1.0))
    metrics = {
        "lambda": round(lam, 3),
        "pooled_mae_raw": round(float(np.mean(np.abs(dp - dy))), 4),
        "pooled_mae_shrunk": round(
            float(np.mean(np.abs(lam * dp - dy))), 4),
        "pooled_mae_5050": round(float(np.mean(np.abs(dy))), 4),
    }
    return lam, metrics


def validate_model_package(package: dict, *,
                           report_path=REPORT_PATH,
                           table_path=TABLE_PATH) -> dict:
    """Bind a loaded model to its report and live raw-data provenance."""
    from .evaluate import content_digest, validate_dataset_provenance
    if package.get("protocol") != TRAIN_PROTOCOL:
        raise ValueError("direction model uses an unsupported train protocol")
    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("direction model has no readable training report") \
            from error
    recorded_key = report.pop("content_key", None)
    if not recorded_key or recorded_key != content_digest(report):
        raise ValueError("direction training report content key is invalid")
    if package.get("training_report_content_key") != recorded_key:
        raise ValueError("direction model and training report do not match")
    live_dataset = validate_dataset_provenance(table_path)
    if package.get("training_dataset") != live_dataset:
        raise ValueError("direction model was not trained on the live dataset")
    return {"training_report_content_key": recorded_key,
            "dataset": live_dataset}


def load_table():
    """Returns (X, y, cities, station_keys, w_obs). Weekday 06–20 rows from
    two-way streets only (one-way-ish stations screened out)."""
    with open(TABLE_PATH) as f:
        rows = list(csv.DictReader(f))

    # v2 retains missing/low-coverage cells as explicit audit rows. They are
    # not labels. Legacy tables have no ``usable`` column and keep the old
    # path; malformed shares fail closed here instead of reaching LightGBM.
    usable = []
    for row in rows:
        if row.get("usable", "1") != "1":
            continue
        try:
            share = float(row["share"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 < share < 1.0 and np.isfinite(share):
            usable.append(row)

    from collections import defaultdict
    day_shares = defaultdict(list)
    for r in usable:
        if r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20:
            day_shares[(r["station_id"], r["heading"])].append(float(r["share"]))
    oneway = {sid for (sid, _), sh in day_shares.items()
              if sh and not 0.15 <= np.mean(sh) <= 0.85}

    rc_i = FEATURE_NAMES.index("radial_cos")
    X, y, cities, keys, w = [], [], [], [], []
    for r in usable:
        if (r["station_id"] in oneway or r["is_weekend"] != "0"
                or not 6 <= int(r["hour"]) <= 20):
            continue
        # ONE row per station-hour: keep only the toward-centre direction.
        # The two directions are mirrored duplicates (share, 1-share) —
        # keeping both double-counts every station with perfectly
        # anticorrelated labels and biases weights and importances.
        if float(r["radial_cos"]) <= 0:
            continue
        h = float(r["hour"])
        X.append([float(r[c]) for c in FEATURE_NAMES]
                 + [np.sin(2 * np.pi * h / 24),
                    np.cos(2 * np.pi * h / 24), 0.0])
        y.append(float(r["share"]))
        cities.append(r["city"])
        keys.append((r["station_id"], r["heading"]))
        # On dataset v2 one row is a real station-date-hour count pair, so
        # its binomial evidence is total_count. The legacy aggregate has no
        # such field and retains sqrt(number of contributing observations).
        evidence = float(r.get("total_count") or r["n_obs"])
        w.append(max(evidence, 1.0) ** 0.5)

    # Station-level weight normalisation: hourly rows within a station are
    # strongly correlated, not independent — give every STATION (not every
    # row) roughly equal total influence.
    from collections import Counter
    n_per_station = Counter(k[0] for k in keys)
    w = [wi / n_per_station[k[0]] for wi, k in zip(w, keys)]

    print(f"Filter: {len(oneway)} one-way-ish stations dropped, {len(y)} rows kept "
          f"(toward-centre direction only, {len(n_per_station)} stations)")
    return np.array(X), np.array(y), cities, keys, np.array(w)


def target_static_features() -> dict[str, np.ndarray]:
    """{sensor_id: static feature vector} for every sensor. For a two-way
    ("Total") sensor's pair, use the TOWARD-CENTRE edge's own row — NOT the
    mean of both directed edges. radial_cos (the model's key feature, per
    features.py) and the asymmetry/ahead-behind features flip sign between
    a pair's two directions; averaging them doesn't produce a direction-
    agnostic descriptor, it cancels radial_cos toward ~0 (verified: sensor
    107's pair is +0.61/-0.59, averaging to 0.01 instead of the real ~0.6
    toward-centre alignment). This is the same toward-centre convention
    predict.py and prior_flows.py use when orienting a pair before
    prediction. Imported lazily to avoid loading OSM graphs on import."""
    from .coverage import target_matrix
    X_tgt, meta = target_matrix()
    rc_i = FEATURE_NAMES.index("radial_cos")
    by_sensor: dict[str, list[np.ndarray]] = {}
    for row, m in zip(X_tgt, meta):
        by_sensor.setdefault(m["sensor"], []).append(row)
    return {sid: max(rows, key=lambda r: r[rc_i]) for sid, rows in by_sensor.items()}


def kernel_weights(X_static_z: np.ndarray, centre_z: np.ndarray,
                   bandwidth: float) -> np.ndarray:
    if not math.isfinite(bandwidth) or bandwidth < MIN_BANDWIDTH:
        raise ValueError("similarity bandwidth must be finite and positive")
    d = np.sqrt(((X_static_z - centre_z) ** 2).sum(axis=1))
    exponent = np.clip(0.5 * (d / bandwidth) ** 2,
                       0.0, MAX_KERNEL_EXPONENT)
    weights = np.exp(-exponent)
    if not np.all(np.isfinite(weights)) or weights.sum() <= 0:
        raise ValueError("similarity weights are not finite and usable")
    return weights


def predict_model(model, matrix: np.ndarray) -> np.ndarray:
    """Predict with the trainer's fixed NumPy column order, without noise."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="X does not have valid feature names, but .* was fitted "
                    "with feature names",
            category=UserWarning,
            module="sklearn.utils.validation")
        values = np.asarray(model.predict(matrix), dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("direction model produced non-finite predictions")
    return values


def make_model(alpha: float | None = None):
    import lightgbm as lgb
    kw = dict(n_estimators=400, learning_rate=0.05, max_depth=5, num_leaves=31,
              subsample=0.8, colsample_bytree=0.8, min_child_samples=20,
              random_state=42, n_jobs=-1, verbose=-1)
    if alpha is not None:
        kw.update(objective="quantile", alpha=alpha)
    return lgb.LGBMRegressor(**kw)


def main() -> None:
    # A deployable model must be traceable to the exact date-level table and
    # all raw volume files. Gate M already validates this chain; training now
    # applies the same fail-closed contract instead of accepting any CSV that
    # happens to have compatible columns.
    from .evaluate import (content_digest, provenance_digests,
                           validate_dataset_provenance)
    dataset_binding = validate_dataset_provenance(TABLE_PATH)
    source_binding = provenance_digests(TABLE_PATH)
    X, y, cities, keys, w_obs = load_table()
    Xs = X[:, :N_STATIC]                       # static street features
    mu, sd = Xs.mean(axis=0), Xs.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs_z = (Xs - mu) / sd

    # Median pairwise-ish bandwidth: distances to the overall centroid
    d0 = np.sqrt(((Xs_z - Xs_z.mean(axis=0)) ** 2).sum(axis=1))
    bandwidth = float(np.median(d0))
    print(f"{len(y)} rows, bandwidth={bandwidth:.2f}")

    report: dict = {
        "protocol": TRAIN_PROTOCOL,
        "n_rows": len(y),
        "bandwidth": round(bandwidth, 3),
        "input_cols": INPUT_COLS,
        "use_profile_features": USE_PROFILE_FEATURES,
        "dataset": dataset_binding,
        "provenance": source_binding,
        "cities": {},
    }

    # ── Leave-city-out, locally weighted per held-out station ─────────────────
    # Domain subset: station-directions closer than the median distance to the
    # GOTHENBURG target centroid — the population we actually care about.
    targets = target_static_features()
    tgt_centroid_z = (np.mean(list(targets.values()), axis=0) - mu) / sd
    d_tgt = np.sqrt(((Xs_z - tgt_centroid_z) ** 2).sum(axis=1))
    domain_row = d_tgt <= np.median(d_tgt)
    print(f"Domain subset: {int(domain_row.sum())} rows "
          f"({len({k for k, m in zip(keys, domain_row) if m})} station-directions)")

    city_arr = np.array(cities)
    pooled_pred, pooled_y = [], []   # domain-subset LCO pairs → shrinkage λ
    for held in sorted(set(cities)):
        te_all  = city_arr == held
        tr      = ~te_all
        te_dom  = te_all & domain_row

        # overall: one similarity-weighted model toward the Gothenburg centroid
        w_sim = kernel_weights(Xs_z, tgt_centroid_z, bandwidth)
        mdl = make_model()
        mdl.fit(X[tr], y[tr], sample_weight=(w_obs * w_sim)[tr])
        pred_all = np.clip(predict_model(mdl, X[te_all]), 0, 1)
        mae_all  = float(np.mean(np.abs(pred_all - y[te_all])))
        base_all = float(np.mean(np.abs(0.5 - y[te_all])))

        # domain subset: each held-out station predicted by a model locally
        # weighted toward THAT station — mirrors the Gothenburg deployment
        maes, bases = [], []
        held_stations = sorted({k for k, m in zip(keys, te_dom) if m})
        for st_key in held_stations:
            te_st = np.array([k == st_key for k in keys]) & te_dom
            centre = Xs_z[te_st].mean(axis=0)
            w_loc = kernel_weights(Xs_z, centre, bandwidth)
            m_loc = make_model()
            m_loc.fit(X[tr], y[tr], sample_weight=(w_obs * w_loc)[tr])
            p = np.clip(predict_model(m_loc, X[te_st]), 0, 1)
            pooled_pred.extend(p.tolist())
            pooled_y.extend(y[te_st].tolist())
            maes.append(float(np.mean(np.abs(p - y[te_st]))))
            bases.append(float(np.mean(np.abs(0.5 - y[te_st]))))
        mae_dom  = float(np.mean(maes)) if maes else float("nan")
        base_dom = float(np.mean(bases)) if bases else float("nan")

        report["cities"][held] = {
            "mae_all": round(mae_all, 4), "base_all": round(base_all, 4),
            "impr_all_pct": round(100 * (1 - mae_all / base_all), 1),
            "n_domain_stations": len(held_stations),
            "mae_domain": round(mae_dom, 4), "base_domain": round(base_dom, 4),
            "impr_domain_pct": (round(100 * (1 - mae_dom / base_dom), 1)
                                if base_dom > 0 else None),
        }
        r = report["cities"][held]
        print(f"  {held:<10} alla: MAE {mae_all:.4f} ({r['impr_all_pct']:+.1f}%)"
              f"   domän ({len(held_stations)} st): MAE {mae_dom:.4f} "
              f"({r['impr_domain_pct']:+.1f}%)")

    # ── Shrinkage calibration (James-Stein flavour) ────────────────────────────
    # Regress observed deviation from 0.5 on predicted deviation (through the
    # origin) over ALL pooled held-out domain predictions. λ<1 means raw
    # predictions overshoot; deployment uses 0.5 + λ·(pred−0.5), which is the
    # MSE-optimal linear correction given the validation evidence. λ≈0 would
    # honestly collapse the model to 50/50.
    lam, shrinkage_metrics = calibrate_shrinkage(pooled_pred, pooled_y)
    report["shrinkage"] = shrinkage_metrics
    print(f"\nShrinkage λ = {lam:.3f}   pooled domain-MAE: "
          f"rå {shrinkage_metrics['pooled_mae_raw']:.4f} → krympt "
          f"{shrinkage_metrics['pooled_mae_shrunk']:.4f}  "
          f"(50/50: {shrinkage_metrics['pooled_mae_5050']:.4f})")

    # ── Deploy: per-sensor locally weighted quantile models ────────────────────
    print("\nTraining per-sensor quantile models …")
    sensors_pkg: dict[str, dict] = {}
    for sid, feat_vec in sorted(targets.items()):
        centre_z = (feat_vec - mu) / sd
        w_loc = kernel_weights(Xs_z, centre_z, bandwidth)
        sw = w_obs * w_loc
        qmodels = {}
        for q in QUANTILES:
            m = make_model(alpha=q)
            m.fit(X, y, sample_weight=sw)
            qmodels[f"q{int(q * 100)}"] = m
        sensors_pkg[sid] = qmodels
        print(f"  sensor {sid}: eff. träningsvikt {sw.sum():.0f} "
              f"(av {w_obs.sum():.0f})")

    report["content_key"] = content_digest(report)
    package = {
        "protocol": TRAIN_PROTOCOL,
        "sensors": sensors_pkg,
        "input_cols": INPUT_COLS,
        "use_profile_features": USE_PROFILE_FEATURES,
        "quantiles": [f"q{int(q*100)}" for q in QUANTILES],
        "scaler": {"mu": mu.tolist(), "sd": sd.tolist()},
        "bandwidth": bandwidth,
        "shrinkage_lambda": lam,
        "orientation": "toward_centre",
        "training_report_content_key": report["content_key"],
        "training_dataset": dataset_binding,
    }
    model_tmp = MODEL_PATH.with_suffix(MODEL_PATH.suffix + ".tmp")
    report_tmp = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    with open(model_tmp, "wb") as f:
        pickle.dump(package, f)
    report_tmp.write_text(json.dumps(report, indent=1) + "\n")
    model_tmp.replace(MODEL_PATH)
    report_tmp.replace(REPORT_PATH)
    print(f"Wrote {MODEL_PATH} + {REPORT_PATH}")


if __name__ == "__main__":
    main()
