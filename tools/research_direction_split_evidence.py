"""Evidence for the 2026-08-13 dirsplit plan review.

Reproduces every measured number in
``docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md``.

Runs entirely on TRACKED artifacts — ``data/dirsplit/training_table.csv``,
``data/dirsplit/coverage_report.json``, ``web/data/flows.json``,
``web/data/network.geojson`` — plus ``sumo/direction_split.json`` when it has
been regenerated (``make dirsplit-predict``; the file is gitignored). Nothing
here fetches data or trains a deployed model, so it is safe to re-run.

    python3 -m tools.research_direction_split_evidence

Sections, in the order the review presents them:

  A  filter chain from the collected table to what train.py actually uses
  B  variance decomposition: station level vs time-of-day shape
  C  leave-city-out / leave-station-out tournament against 50/50
  D  the pooled tidal curve, weekday and weekend
  E  out-of-sample coverage of the nominal 80% q10-q90 interval
  F  what the deployed artifact applies, in vehicles, at sensor 107
  G  level-vs-shape decomposition and the local anchor at sensor 107

The one-way screen, the toward-centre orientation, the station-normalised
weights and the LightGBM hyperparameters are copied from ``dirsplit/train.py``
so the comparison is against the deployed architecture rather than a strawman.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

TABLE = Path("data/dirsplit/training_table.csv")
COVERAGE = Path("data/dirsplit/coverage_report.json")
FLOWS = Path("web/data/flows.json")
GEO = Path("web/data/network.geojson")
SPLIT = Path("sumo/direction_split.json")

# Same feature blocks as dirsplit/train.py.
FEATURE_NAMES = [
    "hw_rank", "speed_kmh", "lanes", "oneway", "dist_centre_km", "radial_cos",
    "res_behind_km", "res_ahead_km", "res_asym", "major_behind_km",
    "major_ahead_km", "major_asym",
]
PROFILE_FEATURES = ["am_pm_ratio", "peak_hour_am", "peak_hour_pm", "weekend_ratio"]

# Sensor 107 — the only station with two approved edges, hence the only one
# whose two-way total is split into two level-1 targets by build_targets().
S107_TOWARD_CENTRE = "60786979_3575001205_0"   # bearing 352.1 deg, northbound
S107_AWAY = "1455801464_18241874_0"            # bearing 174.4 deg, southbound
# Goteborgs Stad trafikmangder catalogue, 2025: N 3400 / S 3100 of 6500.
CITY_ANCHOR_N = 3400.0
CITY_ANCHOR_TOTAL = 6500.0

TARGET_CENTRE = (57.7068, 11.9668)   # Brunnsparken, as in dirsplit/config.py


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def load_rows() -> list[dict]:
    with open(TABLE) as f:
        return list(csv.DictReader(f))


def oneway_stations(rows: list[dict]) -> set[str]:
    """dirsplit/train.py's one-way screen, verbatim in effect."""
    day: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20:
            day[(r["station_id"], r["heading"])].append(float(r["share"]))
    return {sid for (sid, _), sh in day.items()
            if sh and not 0.15 <= float(np.mean(sh)) <= 0.85}


def station_weights(rows: list[dict]) -> np.ndarray:
    """sqrt(n_obs), then normalised so every station carries equal influence."""
    per_station: dict[str, int] = defaultdict(int)
    for r in rows:
        per_station[r["station_id"]] += 1
    return np.array([float(r["n_obs"]) ** 0.5 / per_station[r["station_id"]]
                     for r in rows])


# ── A — filter chain ────────────────────────────────────────────────────────
def section_a(rows: list[dict], oneway: set[str]) -> None:
    _rule("A — what the collected table holds vs what train.py uses")
    steps = [
        ("collected table", lambda r: True),
        ("weekday only", lambda r: r["is_weekend"] == "0"),
        ("+ hours 06-20", lambda r: r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20),
        ("+ two-way only", lambda r: r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20
         and r["station_id"] not in oneway),
        ("+ toward-centre", lambda r: r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20
         and r["station_id"] not in oneway and float(r["radial_cos"]) > 0),
    ]
    print(f"one-way-ish stations screened out: {len(oneway)}")
    for name, keep in steps:
        sub = [r for r in rows if keep(r)]
        n_stations = len({r["station_id"] for r in sub})
        print(f"  {name:<20} {len(sub):>6} rows   {n_stations:>4} stations")


# ── B — variance decomposition ──────────────────────────────────────────────
def section_b(rows: list[dict], oneway: set[str]) -> None:
    _rule("B — is the direction split a street property or a time-of-day property?")
    tw = [r for r in rows
          if r["station_id"] not in oneway and float(r["radial_cos"]) > 0]
    share = np.array([float(r["share"]) for r in tw])
    print(f"two-way toward-centre rows, all hours and day types: {len(tw)} "
          f"({len({r['station_id'] for r in tw})} stations)")
    print(f"|share - 0.5|: mean {np.mean(np.abs(share - 0.5)):.4f}  "
          f"median {np.median(np.abs(share - 0.5)):.4f}  "
          f"p90 {np.percentile(np.abs(share - 0.5), 90):.4f}  "
          f"max {np.max(np.abs(share - 0.5)):.4f}")

    by_station: dict[str, list[float]] = defaultdict(list)
    for r in tw:
        by_station[r["station_id"]].append(float(r["share"]))
    between = float(np.var([np.mean(v) for v in by_station.values()]))
    within = float(np.mean([np.var(v) for v in by_station.values() if len(v) > 1]))
    print(f"\n  between-station variance (the station's own D-factor level): "
          f"{between:.5f}  sd {math.sqrt(between):.4f}")
    print(f"  within-station variance  (hour and day-type shape):          "
          f"{within:.5f}  sd {math.sqrt(within):.4f}")
    print(f"  time-of-day share of total variation: {within / (within + between):.1%}")

    am: dict[str, list[float]] = defaultdict(list)
    pm: dict[str, list[float]] = defaultdict(list)
    for r in tw:
        if r["is_weekend"] != "0":
            continue
        h = int(r["hour"])
        if h in (7, 8):
            am[r["station_id"]].append(float(r["share"]))
        if h in (16, 17):
            pm[r["station_id"]].append(float(r["share"]))
    common = sorted(set(am) & set(pm))
    d = np.array([np.mean(am[k]) - np.mean(pm[k]) for k in common])
    print(f"\n  tidal signal (AM 07-08 minus PM 16-17 toward-centre share), "
          f"{len(common)} stations:")
    print(f"    mean {d.mean():+.4f}  median {np.median(d):+.4f}  sd {d.std():.4f}")
    print(f"    stations with the classic AM-inbound sign: "
          f"{(d > 0).sum()}/{len(d)} = {(d > 0).mean():.1%}")


# ── C — tournament ──────────────────────────────────────────────────────────
def _pooled_hour_curve(y, h, w, train, shrink_k=5.0):
    """Global D-factor curve by hour, partially pooled toward 0.5.

    No street features at all. The pooling weight n/(n+k) is what keeps a
    thin hour from asserting a large deviation on little evidence.
    """
    table: dict[int, float] = {}
    for hour in range(24):
        m = train & (h == hour)
        if not m.any():
            continue
        ww = y[m], w[m]
        vals, wts = ww
        n_eff = wts.sum() / wts.mean() if wts.mean() > 0 else 0.0
        mean = float((vals * wts).sum() / wts.sum())
        table[hour] = 0.5 + (n_eff / (n_eff + shrink_k)) * (mean - 0.5)
    grand = float((y[train] * w[train]).sum() / w[train].sum())
    return lambda hq: np.array([table.get(a, grand) for a in hq])


def _lgbm(X, y, w, train, test, extra_weight=None):
    import lightgbm as lgb
    model = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.05, max_depth=5, num_leaves=31,
        subsample=0.8, colsample_bytree=0.8, min_child_samples=20,
        random_state=42, n_jobs=-1, verbose=-1)
    sw = w if extra_weight is None else w * extra_weight
    model.fit(X[train], y[train], sample_weight=sw[train])
    return np.clip(model.predict(X[test]), 0.0, 1.0)


def _shrink_lambda(pred, obs):
    dp, dy = pred - 0.5, obs - 0.5
    return float(np.clip((dp @ dy) / max(dp @ dp, 1e-12), 0.0, 1.0))


def _target_centroid_z(mu, sd):
    with open(COVERAGE) as f:
        cov = json.load(f)
    by_sensor: dict[str, list[np.ndarray]] = defaultdict(list)
    for e in cov["edges"]:
        by_sensor[e["sensor"]].append(
            np.array([e["features"][c] for c in FEATURE_NAMES]))
    rc = FEATURE_NAMES.index("radial_cos")
    vecs = [max(v, key=lambda a: a[rc]) for v in by_sensor.values()]
    return (np.mean(vecs, axis=0) - mu) / sd


def section_c(rows: list[dict], oneway: set[str]) -> None:
    _rule("C — leave-city-out and leave-station-out tournament")
    sub = [r for r in rows
           if r["station_id"] not in oneway and float(r["radial_cos"]) > 0
           and r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20]
    y = np.array([float(r["share"]) for r in sub])
    h = np.array([int(r["hour"]) for r in sub])
    st = np.array([r["station_id"] for r in sub])
    ct = np.array([r["city"] for r in sub])
    w = station_weights(sub)
    Xs = np.array([[float(r[c]) for c in FEATURE_NAMES] for r in sub])
    X = np.hstack([
        Xs,
        np.array([[float(r[c]) for c in PROFILE_FEATURES] for r in sub]),
        np.c_[np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24), np.zeros(len(h))],
    ])
    mu, sd = Xs.mean(axis=0), Xs.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xz = (Xs - mu) / sd
    bandwidth = float(np.median(np.sqrt(((Xz - Xz.mean(axis=0)) ** 2).sum(axis=1))))
    tgt_z = _target_centroid_z(mu, sd)
    sim = np.exp(-0.5 * (np.sqrt(((Xz - tgt_z) ** 2).sum(axis=1)) / bandwidth) ** 2)
    domain = np.sqrt(((Xz - tgt_z) ** 2).sum(axis=1))
    domain = domain <= np.median(domain)

    def run(label, folds):
        acc: dict[str, dict[str, list]] = defaultdict(
            lambda: {"p": [], "y": [], "w": []})
        for train, test in folds:
            if not test.any() or not train.any():
                continue
            preds = {
                "50/50": np.full(int(test.sum()), 0.5),
                "pooled_hour_curve": _pooled_hour_curve(y, h, w, train)(h[test]),
                "lgbm_plain": _lgbm(X, y, w, train, test),
                "lgbm_simweighted": _lgbm(X, y, w, train, test, extra_weight=sim),
            }
            for k, v in preds.items():
                acc[k]["p"].extend(v)
                acc[k]["y"].extend(y[test])
                acc[k]["w"].extend(w[test])
        print(f"\n  {label}")
        print(f"  {'model':<20}{'MAE':>9}{'vs 50/50':>10}"
              f"{'MAE shrunk':>12}{'lambda':>8}{'vs 50/50':>10}")
        base = None
        for k, v in acc.items():
            p, o, ww = (np.array(v[key]) for key in ("p", "y", "w"))
            mae = float(np.average(np.abs(p - o), weights=ww))
            lam = _shrink_lambda(p, o)
            maes = float(np.average(np.abs(0.5 + lam * (p - 0.5) - o), weights=ww))
            base = mae if base is None else base
            print(f"  {k:<20}{mae:>9.4f}{100 * (1 - mae / base):>+9.1f}%"
                  f"{maes:>12.4f}{lam:>8.3f}{100 * (1 - maes / base):>+9.1f}%")

    cities = sorted(set(ct))
    run("leave-city-out, all two-way stations",
        [(ct != c, ct == c) for c in cities])
    run("leave-city-out, Gothenburg-like domain subset",
        [(ct != c, (ct == c) & domain) for c in cities])
    run(f"leave-station-out ({len(set(st))} folds)",
        [(st != s, st == s) for s in sorted(set(st))])


# ── D — the pooled curve ────────────────────────────────────────────────────
def pooled_curve(rows: list[dict], oneway: set[str], is_weekend: str) -> np.ndarray:
    """96-slot toward-centre share curve for one day type."""
    sub = [r for r in rows
           if r["station_id"] not in oneway and float(r["radial_cos"]) > 0
           and r["is_weekend"] == is_weekend]
    table: dict[int, float] = {}
    for hour in range(24):
        v = [(float(r["share"]), float(r["n_obs"]) ** 0.5)
             for r in sub if int(r["hour"]) == hour]
        if v:
            a = np.array([x for x, _ in v])
            ww = np.array([x for _, x in v])
            table[hour] = float((a * ww).sum() / ww.sum())
    grand = float(np.mean(list(table.values())))
    return np.array([table.get(i // 4, grand) for i in range(96)])


def section_d(rows: list[dict], oneway: set[str]) -> tuple[np.ndarray, np.ndarray]:
    _rule("D — the pooled tidal curve")
    wd, we = pooled_curve(rows, oneway, "0"), pooled_curve(rows, oneway, "1")
    print("  hour   weekday   weekend")
    for hour in range(24):
        i = hour * 4
        bar = int(round((wd[i] - 0.5) * 300))
        art = ("#" * -bar).rjust(16) + "|" + "#" * max(bar, 0)
        print(f"  {hour:>4}    {wd[i]:.3f}     {we[i]:.3f}   {art}")
    print(f"\n  weekday peak-to-peak {wd.max() - wd.min():.3f}   "
          f"weekend peak-to-peak {we.max() - we.min():.3f}")
    return wd, we


# ── E — interval coverage ───────────────────────────────────────────────────
def section_e(rows: list[dict], oneway: set[str]) -> None:
    _rule("E — actual out-of-sample coverage of the nominal 80% interval")
    import lightgbm as lgb
    sub = [r for r in rows
           if r["station_id"] not in oneway and float(r["radial_cos"]) > 0
           and r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20]
    y = np.array([float(r["share"]) for r in sub])
    h = np.array([int(r["hour"]) for r in sub])
    ct = np.array([r["city"] for r in sub])
    w = station_weights(sub)
    Xs = np.array([[float(r[c]) for c in FEATURE_NAMES] for r in sub])
    X = np.hstack([
        Xs,
        np.array([[float(r[c]) for c in PROFILE_FEATURES] for r in sub]),
        np.c_[np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24), np.zeros(len(h))],
    ])
    mu, sd = Xs.mean(axis=0), Xs.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xz = (Xs - mu) / sd
    bandwidth = float(np.median(np.sqrt(((Xz - Xz.mean(axis=0)) ** 2).sum(axis=1))))
    tgt_z = _target_centroid_z(mu, sd)
    sim = np.exp(-0.5 * (np.sqrt(((Xz - tgt_z) ** 2).sum(axis=1)) / bandwidth) ** 2)

    def q(alpha, train, test):
        m = lgb.LGBMRegressor(
            objective="quantile", alpha=alpha, n_estimators=400,
            learning_rate=0.05, max_depth=5, num_leaves=31, subsample=0.8,
            colsample_bytree=0.8, min_child_samples=20, random_state=42,
            n_jobs=-1, verbose=-1)
        m.fit(X[train], y[train], sample_weight=(w * sim)[train])
        return np.clip(m.predict(X[test]), 0.0, 1.0)

    lo_a, mid_a, hi_a, obs_a, w_a = [], [], [], [], []
    for c in sorted(set(ct)):
        train, test = ct != c, ct == c
        a, b, d = q(0.1, train, test), q(0.5, train, test), q(0.9, train, test)
        lo = np.minimum.reduce([a, b, d])
        hi = np.maximum.reduce([a, b, d])
        lo_a.extend(lo)
        hi_a.extend(hi)
        mid_a.extend(np.clip(b, lo, hi))
        obs_a.extend(y[test])
        w_a.extend(w[test])
    lo, mid, hi, obs, ww = (np.array(v) for v in (lo_a, mid_a, hi_a, obs_a, w_a))

    def report(tag, lo, hi):
        inside = (obs >= lo) & (obs <= hi)
        alpha = 0.2
        score = ((hi - lo) + 2 / alpha * (lo - obs) * (obs < lo)
                 + 2 / alpha * (obs - hi) * (obs > hi))
        print(f"  {tag:<36} coverage {inside.mean():.1%} "
              f"(weighted {float(np.average(inside, weights=ww)):.1%})  "
              f"mean width {float(np.average(hi - lo, weights=ww)):.3f}  "
              f"interval score {float(np.average(score, weights=ww)):.4f}")

    print("  nominal coverage is 80%\n")
    report("raw quantile models", lo, hi)
    lam = _shrink_lambda(mid, obs)
    shift = (0.5 + lam * (mid - 0.5)) - mid
    report(f"after deployed shrinkage (l={lam:.2f})",
           np.clip(lo + shift, 0.1, 0.9), np.clip(hi + shift, 0.1, 0.9))

    curve = {}
    for hour in range(24):
        m = h == hour
        if m.any():
            curve[hour] = float((y[m] * w[m]).sum() / w[m].sum())
    residual = y - np.array([curve[a] for a in h])
    q10e, q90e = np.percentile(residual, 10), np.percentile(residual, 90)
    print(f"\n  empirical residual spread around the pooled curve: "
          f"q10 {q10e:+.3f}  q90 {q90e:+.3f}")
    print(f"  an honest 80% interval is therefore ~{q90e - q10e:.3f} wide, "
          f"against the deployed {float(np.average(hi - lo, weights=ww)):.3f}")
    print("  NOTE: rows are already ~8-day means, so single-day coverage is "
          "WORSE than this. 47% is an upper bound.")


# ── F/G — sensor 107 ────────────────────────────────────────────────────────
def _bearing(c0, c1) -> float:
    (lo1, la1), (lo2, la2) = c0, c1
    dlon = math.radians(lo2 - lo1)
    y = math.sin(dlon) * math.cos(math.radians(la2))
    x = (math.cos(math.radians(la1)) * math.sin(math.radians(la2))
         - math.sin(math.radians(la1)) * math.cos(math.radians(la2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def section_f_g(wd: np.ndarray, we: np.ndarray) -> None:
    _rule("F — geometry and vehicle-level impact at sensor 107")
    with open(GEO) as f:
        geo = json.load(f)
    for feat in geo["features"]:
        p = feat["properties"]
        if p.get("id") in (S107_TOWARD_CENTRE, S107_AWAY):
            c = feat["geometry"]["coordinates"]
            b = _bearing(c[0], c[-1])
            compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((b + 22.5) % 360 // 45)]
            print(f"  {p['id']}  {p.get('name')}  bearing {b:6.1f} ({compass})")
    print(f"  -> the city catalogue's N row is the toward-centre carriageway "
          f"({S107_TOWARD_CENTRE}).")

    with open(FLOWS) as f:
        flows = json.load(f)
    total = np.array([x if x is not None else np.nan
                      for x in flows["flows"][S107_TOWARD_CENTRE]], dtype=float)
    day_index = (date(2025, 9, 16) - date(2025, 1, 1)).days
    day = total[day_index * 96:(day_index + 1) * 96]
    print(f"\n  2025-09-16 two-way total: {np.nansum(day):.0f} veh/day, "
          f"peak quarter {np.nanmax(day):.0f}")

    deployed = None
    if SPLIT.exists():
        with open(SPLIT) as f:
            deployed = np.array(json.load(f)["107"]["edge_shares"][S107_TOWARD_CENTRE])

    am = slice(28, 36)   # 07:00-09:00
    two_way_am = float(np.nansum(day[am]))
    print(f"\n  AM peak 07:00-09:00, two-way {two_way_am:.0f} veh. Toward-centre share:")
    cases = [("50/50", np.full(96, 0.5)), ("pooled tidal curve", wd)]
    if deployed is not None:
        cases.insert(1, ("deployed dirsplit q50", deployed))
    for name, s in cases:
        v = float(np.nansum(day[am] * s[am]))
        print(f"    {name:<24}{v:>7.0f} veh   ({v - two_way_am / 2:+.0f} vs 50/50)")
    if deployed is None:
        print("    (deployed q50 unavailable — run `make dirsplit-predict` first)")

    _rule("G — level vs shape, and the local anchor")
    d0 = date(2025, 1, 1)

    def flow_weighted(curve_wd, curve_we) -> float:
        num = den = 0.0
        for di in range(365):
            chunk = total[di * 96:(di + 1) * 96]
            if chunk.size < 96 or np.all(np.isnan(chunk)):
                continue
            c = curve_we if (d0 + timedelta(days=di)).weekday() >= 5 else curve_wd
            m = ~np.isnan(chunk)
            num += float(np.nansum(chunk[m] * c[m]))
            den += float(np.nansum(chunk[m]))
        return num / den

    anchor = CITY_ANCHOR_N / CITY_ANCHOR_TOTAL
    print(f"  Goteborgs Stad published 2025 D-factor: {anchor:.4f} "
          f"({100 * anchor:.1f}/{100 * (1 - anchor):.1f})")
    print(f"  50/50 baseline error:              "
          f"{abs(0.5 - anchor) * 100:.2f} pp")
    print(f"  pooled tidal curve, flow-weighted: {flow_weighted(wd, we):.4f}   "
          f"error {abs(flow_weighted(wd, we) - anchor) * 100:.2f} pp")
    if deployed is not None:
        dep = flow_weighted(deployed, deployed)
        print(f"  deployed dirsplit q50:             {dep:.4f}   "
              f"error {abs(dep - anchor) * 100:.2f} pp")
    print("  -> no transferred model recovers the LEVEL. Only local data does.")

    def logit(p):
        return np.log(p / (1 - p))

    def inv(z):
        return 1 / (1 + np.exp(-z))

    lo, hi = -2.0, 2.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if flow_weighted(inv(logit(wd) + mid), inv(logit(we) + mid)) < anchor:
            lo = mid
        else:
            hi = mid
    off = (lo + hi) / 2
    awd, awe = inv(logit(wd) + off), inv(logit(we) + off)
    print(f"\n  single logit offset that reproduces the published anchor: {off:+.4f}")
    print(f"  flow-weighted mean after anchoring: {flow_weighted(awd, awe):.4f} "
          f"(target {anchor:.4f})")
    print(f"  tidal amplitude preserved: {awd.max() - awd.min():.3f} "
          f"(unanchored {wd.max() - wd.min():.3f})")
    print("\n  anchored weekday curve at sensor 107 (N/S):")
    for hour in (0, 6, 7, 8, 9, 12, 15, 16, 17, 20, 23):
        i = hour * 4
        print(f"    {hour:>2}:00   {100 * awd[i]:.1f}/{100 * (1 - awd[i]):.1f}")


def main() -> None:
    rows = load_rows()
    oneway = oneway_stations(rows)
    section_a(rows, oneway)
    section_b(rows, oneway)
    section_c(rows, oneway)
    wd, we = section_d(rows, oneway)
    section_e(rows, oneway)
    section_f_g(wd, we)


if __name__ == "__main__":
    main()
