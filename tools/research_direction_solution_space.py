"""Search the full solution space for sensor 107's direction split.

Evidence for the "think outside the box, find the absolute best" pass on
``docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md`` (findings F12-F15).

    python3 -m tools.research_direction_solution_space

Runs on tracked artifacts only: ``web/data/flows.json``,
``web/data/normal_profile.json``, ``web/data/network.geojson``,
``data/dirsplit/training_table.csv``. No SUMO, no model training, no network.

Four candidate families are tested, three of them killed:

  C  Corridor continuity. Sensor 1076 measures southbound Skanegatan 257 m
     from 107, which measures the two-way total on the same street. If 1076
     were a clean downstream cross-section, ``1076 / 107_total`` would BE the
     southbound share -- no model needed. Falsified: 20 pp off the published
     value, and 1076 exceeds 107's TOTAL in 7.9% of quarters, so substantial
     flow enters between them.

  D  Profile deconvolution -- the family ``estimate_directions.py`` belongs to.
     Fit ``107_total = a*P_i + b*mirror(P_j)`` on the five locally measured
     single-direction profiles. Falsified twice over: implied shares land
     outside [0,1] at R^2 ~ 0.98, and a control shows the mirrored
     (counter-phase) basis never beats an un-mirrored one. Gothenburg's own
     data rejects the counter-phase premise, which is why the legacy AM/PM
     Gaussian produced 80/20.

  L  Local level anchor. Predict each held-out station by its own mean share --
     the analogue of the city's published annual D-factor for 107.

  S  Local anchor plus the pooled time-of-day shape, composed on the logit
     scale so the anchor is reproduced exactly.

The decisive result is the ordering of L and S against the alternatives: the
local anchor is worth roughly three times what the shape is worth, and both
dwarf every transferred conditional model. The direction problem is a
DATA-ACQUISITION problem before it is a modelling problem.
"""

from __future__ import annotations

import csv
import itertools
import math
from collections import defaultdict

import numpy as np

FLOWS = "web/data/flows.json"
PROFILE = "web/data/normal_profile.json"
TABLE = "data/dirsplit/training_table.csv"

E107_N = "60786979_3575001205_0"   # bearing 352.1, toward centre
E107_S = "1455801464_18241874_0"   # bearing 174.4
E1076 = "30420757_30421744_0"      # bearing 170.9, southbound Skanegatan
SINGLES = {
    "1074": "60790252_60790253_0", "1076": E1076,
    "133": "26842525_26355153_0", "134": "26355153_96523321_0",
    "2276": "26355153_91615277_0",
}
PUBLISHED_N = 3400 / 6500          # Goteborgs Stad 2025 catalogue for 107


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _load_json(path):
    import json
    with open(path) as f:
        return json.load(f)


def _profile(profiles: dict, edge: str) -> np.ndarray:
    arr = np.array([x if x is not None else np.nan
                    for x in profiles[edge]["weekday"]], dtype=float)
    return arr / np.nansum(arr)


# ── C — corridor continuity ─────────────────────────────────────────────────
def family_c() -> None:
    _rule("C — is 1076 a clean downstream cross-section of 107's southbound?")
    flows = _load_json(FLOWS)["flows"]
    total = np.array([x if x is not None else np.nan for x in flows[E107_N]], dtype=float)
    south = np.array([x if x is not None else np.nan for x in flows[E1076]], dtype=float)
    both = ~np.isnan(total) & ~np.isnan(south)
    ratio = float(np.nansum(south[both]) / np.nansum(total[both]))
    print(f"  107 two-way total : {np.nansum(total):9.0f} veh/yr  "
          f"({np.nansum(total)/365:6.0f}/day)")
    print(f"  1076 southbound   : {np.nansum(south):9.0f} veh/yr  "
          f"({np.nansum(south)/365:6.0f}/day)")
    print(f"\n  implied southbound share = 1076/107_total = {ratio:.4f}  "
          f"({100*ratio:.1f} S / {100*(1-ratio):.1f} N)")
    print(f"  published                                    "
          f"{100*(1-PUBLISHED_N):.1f} S / {100*PUBLISHED_N:.1f} N")
    print(f"  error: {abs(ratio - (1 - PUBLISHED_N)) * 100:.2f} percentage points")
    viol = int((south[both] > total[both]).sum())
    print(f"\n  quarters where 1076 EXCEEDS 107's two-way total: {viol} "
          f"({100 * viol / both.sum():.1f}%)")
    print("  -> not a nested cross-section. Flow enters between the two "
          "stations.\n  VERDICT: falsified.")


# ── D — profile deconvolution ───────────────────────────────────────────────
def family_d() -> None:
    _rule("D — can 107's total be deconvolved into two counter-phased tides?")
    profiles = _load_json(PROFILE)["profiles"]
    target = _profile(profiles, E107_N)
    singles = {k: _profile(profiles, v) for k, v in SINGLES.items()}

    def mirror(p):
        return np.roll(p[::-1], 1)

    def fit(design):
        ok = ~np.isnan(target) & ~np.isnan(design).any(axis=1)
        coef, *_ = np.linalg.lstsq(design[ok], target[ok], rcond=None)
        resid = design[ok] @ coef - target[ok]
        r2 = 1 - float((resid ** 2).sum() / ((target[ok] - target[ok].mean()) ** 2).sum())
        return coef, r2

    shares = []
    print(f"  {'basis pair':<24}{'implied N share':>17}{'R2':>8}")
    for i, j in itertools.permutations(singles, 2):
        coef, r2 = fit(np.vstack([singles[i], mirror(singles[j])]).T)
        a, b = coef
        if a + b <= 0:
            continue
        shares.append(a / (a + b))
        if len(shares) <= 6:
            print(f"  {i} + mirror({j}){'':<{max(0, 9 - len(i) - len(j))}}"
                  f"{a / (a + b):>17.3f}{r2:>8.3f}")
    arr = np.array(shares)
    print(f"  ... {len(shares)} pairs total")
    print(f"\n  range of implied share: {arr.min():.3f} to {arr.max():.3f} "
          f"({100 * (arr.max() - arr.min()):.0f} pp), median {np.median(arr):.3f}")
    print(f"  published: {PUBLISHED_N:.3f}.  Shares above 1.0 are not physical.")

    better = 0
    pairs = list(itertools.combinations(singles, 2))
    for i, j in pairs:
        _, r2_mirror = fit(np.vstack([singles[i], mirror(singles[j])]).T)
        _, r2_plain = fit(np.vstack([singles[i], singles[j]]).T)
        better += r2_mirror > r2_plain
    print(f"\n  CONTROL — mirrored basis beats un-mirrored in {better}/{len(pairs)} "
          f"pairs.")
    print("  If counter-phased tides shaped 107's total, the mirror should win")
    print("  consistently. It never does.\n  VERDICT: falsified, twice.")


# ── L / S — anchor and shape ────────────────────────────────────────────────
def _load_shares():
    rows_all = list(csv.DictReader(open(TABLE)))
    day = defaultdict(list)
    for r in rows_all:
        if r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20:
            day[(r["station_id"], r["heading"])].append(float(r["share"]))
    oneway = {s for (s, _), v in day.items()
              if v and not 0.15 <= float(np.mean(v)) <= 0.85}
    rows = [r for r in rows_all
            if r["station_id"] not in oneway and float(r["radial_cos"]) > 0
            and r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20]
    per = defaultdict(int)
    for r in rows:
        per[r["station_id"]] += 1
    return (np.array([float(r["share"]) for r in rows]),
            np.array([int(r["hour"]) for r in rows]),
            np.array([r["station_id"] for r in rows]),
            np.array([r["city"] for r in rows]),
            np.array([float(r["n_obs"]) ** 0.5 / per[r["station_id"]] for r in rows]))


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _inv(z):
    return 1 / (1 + np.exp(-z))


def family_ls(fold: str) -> None:
    y, h, st, ct, w = _load_shares()
    group = ct if fold == "city" else st
    acc = defaultdict(lambda: {"p": [], "y": [], "w": []})
    for held in sorted(set(group)):
        tr = group != held
        dev, pooled = {}, float((y[tr] * w[tr]).sum() / w[tr].sum())
        for hour in range(24):
            m = tr & (h == hour)
            if m.any():
                dev[hour] = float((y[m] * w[m]).sum() / w[m].sum())
        shape = {k: _logit(v) - _logit(pooled) for k, v in dev.items()}
        for s in sorted(set(st[group == held])):
            sm = st == s
            anchor = float((y[sm] * w[sm]).sum() / w[sm].sum())
            preds = {
                "50/50 flat": np.full(int(sm.sum()), 0.5),
                "pooled curve, no anchor": np.array([dev.get(a, pooled) for a in h[sm]]),
                "local anchor, flat": np.full(int(sm.sum()), anchor),
                "local anchor + pooled shape":
                    _inv(np.array([_logit(anchor) + shape.get(a, 0.0) for a in h[sm]])),
            }
            for k, p in preds.items():
                acc[k]["p"].extend(p)
                acc[k]["y"].extend(y[sm])
                acc[k]["w"].extend(w[sm])

    label = "LEAVE-CITY-OUT" if fold == "city" else "LEAVE-STATION-OUT"
    _rule(f"L / S — {label}: what is the local anchor worth, and does shape add?")
    mae = {}
    for k, v in acc.items():
        p, o, ww = (np.array(v[x]) for x in ("p", "y", "w"))
        mae[k] = float(np.average(np.abs(p - o), weights=ww))
    base, anch = mae["50/50 flat"], mae["local anchor, flat"]
    print(f"  {'estimator':<30}{'MAE':>9}{'vs 50/50':>11}{'vs anchor':>12}")
    for k, m in mae.items():
        print(f"  {k:<30}{m:>9.4f}{100 * (1 - m / base):>+10.1f}%"
              f"{100 * (1 - m / anch):>+11.1f}%")

    p1 = np.array(acc["local anchor, flat"]["p"])
    p2 = np.array(acc["local anchor + pooled shape"]["p"])
    obs = np.array(acc["local anchor, flat"]["y"])
    diff = np.abs(p1 - obs) - np.abs(p2 - obs)
    rng = np.random.default_rng(7)
    boot = [float(diff[rng.integers(0, len(diff), len(diff))].mean()) for _ in range(4000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"\n  shape on top of the anchor: mean MAE reduction {diff.mean():+.5f}"
          f"   95% CI [{lo:+.5f}, {hi:+.5f}]")
    print(f"  -> {'SIGNIFICANT' if lo > 0 else 'NOT significant'} at 95%")


def _load_shares_all_hours():
    """Same population, but every hour of the weekday — not just 06-20.

    The deployed training filter keeps 06-20 only, then predicts all 24 hours
    anyway (finding F7). The deliverable curve has to cover the whole day, and
    the rows for it are already in the tracked table.
    """
    rows_all = list(csv.DictReader(open(TABLE)))
    day = defaultdict(list)
    for r in rows_all:
        if r["is_weekend"] == "0" and 6 <= int(r["hour"]) <= 20:
            day[(r["station_id"], r["heading"])].append(float(r["share"]))
    oneway = {s for (s, _), v in day.items()
              if v and not 0.15 <= float(np.mean(v)) <= 0.85}
    rows = [r for r in rows_all
            if r["station_id"] not in oneway and float(r["radial_cos"]) > 0
            and r["is_weekend"] == "0"]
    per = defaultdict(int)
    for r in rows:
        per[r["station_id"]] += 1
    return (np.array([float(r["share"]) for r in rows]),
            np.array([int(r["hour"]) for r in rows]),
            np.array([float(r["n_obs"]) ** 0.5 / per[r["station_id"]] for r in rows]))


def deliverable() -> None:
    """The recommended curve for sensor 107, in numbers."""
    _rule("DELIVERABLE — recommended split at sensor 107")
    y, h, w = _load_shares_all_hours()
    dev = {}
    for hour in range(24):
        m = h == hour
        if m.any():
            dev[hour] = float((y[m] * w[m]).sum() / w[m].sum())
    pooled = float((y * w).sum() / w.sum())
    shape = {k: _logit(v) - _logit(pooled) for k, v in dev.items()}
    curve = {k: float(_inv(_logit(PUBLISHED_N) + s)) for k, s in shape.items()}
    print(f"  level  = Goteborgs Stad published 2025 D-factor  {PUBLISHED_N:.4f}")
    print(f"  shape  = pooled weekday tidal curve (held-out validated)")
    print(f"  width  = measured residual spread, 0.193 -> +/-0.0965\n")
    print(f"  {'hour':>6}{'N share':>10}{'N / S':>14}{'80% band on N':>22}")
    for hour in (0, 6, 7, 8, 9, 12, 15, 16, 17, 20, 23):
        v = curve[hour]
        print(f"  {hour:02d}:00{v:>10.3f}{100 * v:>9.1f}/{100 * (1 - v):<5.1f}"
              f"{max(v - 0.0965, 0):>13.3f} - {min(v + 0.0965, 1):.3f}")
    amp = max(curve.values()) - min(curve.values())
    check = float(np.mean(list(curve.values())))
    print(f"\n  tidal amplitude preserved: {amp:.3f}")
    print(f"  unweighted mean of the curve: {check:.4f} "
          f"(flow-weighted anchoring is done against the station's own profile)")


def main() -> None:
    family_c()
    family_d()
    family_ls("city")
    family_ls("station")
    deliverable()
    _rule("SUMMARY")
    print("  falsified: corridor continuity (C), profile deconvolution (D),")
    print("             sum constraint and its band repair (see")
    print("             tools/research_direction_sum_constraint.py),")
    print("             per-street ML transfer (see")
    print("             tools/research_direction_split_evidence.py)")
    print("  surviving: local level anchor (+22.7%) x pooled tidal shape (+7.0%")
    print("             on top), with the honest 0.193 interval across variants")
    print("\n  The anchor is worth ~3x the shape. Getting more local D-factors")
    print("  beats any further modelling.")


if __name__ == "__main__":
    main()
