"""
Where should a two-way station's intraday SHAPE come from — a foreign group of
continuous counters, or the nearest directional counter on its own corridor?

The deployed split takes its LEVEL from sensor 107's published D-factor and its
SHAPE from 81 Norwegian stations pooled into one hour x day-type curve. That is
the Traffic Monitoring Guide's standard construction: temporal patterns come
from continuous counters, grouped, and are applied to sites that only carry a
short or bidirectional count. Project-level forecasting guidance offers a second
construction for the same problem — take the pattern from a nearby permanent
counter instead of a group average — and Gothenburg has one: sensor 1076
measures southbound Skanegatan 239.9 m from sensor 107's southbound edge at a
bearing delta of 3.5 degrees.

``tools/measure_local_direction_evidence.py`` already killed the naive version
(1076's ratio against 107's two-way total carries a 21.77 pp location bias, so
it is not a direction share). This measures the surviving version: a donor's
NORMALISED shape, re-levelled to the target's own anchor, exactly as the
deployed loader re-levels the group curve.

It cannot be measured at 107 — no Gothenburg station observes a direction share
— so it is measured where the truth exists. Norwegian stations stand in: each
target station's own flow-weighted mean plays the published D-factor, its hourly
shares are the truth, and three candidates are scored against it.

    B0  flat anchor            the level alone, no shape
    B1  group shape            the deployed pooled curve, re-levelled
    L1  donor shape            the nearest ALIGNED station's own curve, re-levelled

A donor must be a different station in the same city whose heading points the
same way as the target's (bearing delta within tolerance) and which lies within
the distance band being reported. The 107/1076 geometry sits in the tightest
band, so that band is the one that answers Gothenburg's question; the wider
bands show how fast the advantage decays with distance.

Diagnostic only: ``release_evidence: false``, no preregistration, no gate.

Usage:
  python3 -m tools.measure_donor_shape_transfer
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from dirsplit.benchmark import (BOOTSTRAP_SEED, MIN_IMPROVEMENT_PCT,
                                MIN_INDEPENDENT_GROUPS, Observation,
                                ShrunkDFactor, bootstrap_interval,
                                leave_city_out, load_legacy,
                                orient_toward_centre, restrict_to_weekdays,
                                screen_two_way_stations)
from traffic_sim.intake.direction_anchor import (DEFAULT_CLAMP, shift_share,
                                                 solve_odds_shift)

STATIONS_PATH = Path("data/dirsplit/stations_matched.json")
OUT_PATH = Path("validation/donor_shape_transfer_v1.json")
SCHEMA_VERSION = "donor_shape_transfer_v1"

#: A curve needs this many distinct hours before it can be a donor or a target.
MIN_HOURS = 12
#: Two headings count as the same direction of travel within this angle. The
#: Gothenburg pair sits at 3.5 degrees; 30 degrees keeps the sample usable
#: without admitting cross-streets.
MAX_BEARING_DELTA_DEG = 30.0
#: Reported distance bands. 300 m is the band containing the 239.9 m the real
#: donor stands at.
DISTANCE_BANDS_M = (300.0, 500.0, 1000.0, 1500.0)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def bearing_delta_deg(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def road_number(road_ref: str | None) -> str | None:
    """The road identity inside a Norwegian road reference ('FV582 S1D1 …')."""
    if not road_ref:
        return None
    head = str(road_ref).strip().split()
    return head[0] if head else None


def load_stations(path: Path = STATIONS_PATH) -> dict[str, dict]:
    payload = json.loads(Path(path).read_text())
    return {station["id"]: station for station in payload if station.get("matched")}


def curves(observations: Sequence[Observation]) -> dict[tuple[str, str], dict]:
    """{(station, heading): hourly truth, weights, anchor} for usable curves."""
    grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for obs in observations:
        grouped[(obs.station_id, obs.heading)].append(obs)
    result: dict[tuple[str, str], dict] = {}
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda obs: obs.hour)
        if len({obs.hour for obs in rows}) < MIN_HOURS:
            continue
        weights = [obs.weight for obs in rows]
        if sum(weights) <= 0:
            continue
        truth = [obs.share for obs in rows]
        anchor = sum(w * t for t, w in zip(truth, weights)) / sum(weights)
        if not 0 < anchor < 1:
            continue
        result[key] = {"rows": rows, "hours": [obs.hour for obs in rows],
                       "truth": truth, "weights": weights, "anchor": anchor,
                       "city": rows[0].city,
                       # The pooled model predicts the TOWARD-CENTRE share, so a
                       # curve's orientation decides whether the group curve
                       # applies directly or as its complement — the same
                       # convention dirsplit/predict.py uses on a sensor pair.
                       "toward_centre": rows[0].features.get("radial_cos", 0.0) > 0}
    return result


def weighted_mae(truth: Sequence[float], prediction: Sequence[float],
                 weights: Sequence[float]) -> float:
    total = float(sum(weights))
    return sum(w * abs(t - p) for t, p, w in zip(truth, prediction, weights)) / total


def relevel(shape_by_hour: dict[int, float], hours: Sequence[int],
            weights: Sequence[float], anchor: float,
            clamp: tuple[float, float] = DEFAULT_CLAMP) -> list[float] | None:
    """Re-level a donor/group curve onto a target's anchor, as deployment does."""
    raw = [shape_by_hour.get(hour) for hour in hours]
    if any(value is None for value in raw):
        return None
    raw = [float(value) for value in raw]
    delta = solve_odds_shift(raw, weights, anchor, clamp)
    return [shift_share(value, delta, clamp) for value in raw]


def donor_candidates(target_key: tuple[str, str], available: dict[tuple[str, str], dict],
                     stations: dict[str, dict]) -> list[dict]:
    """Aligned donors for one target curve, nearest first."""
    target_id, target_heading = target_key
    target = stations.get(target_id)
    if target is None:
        return []
    target_bearing = (target.get("heading_bearings") or {}).get(target_heading)
    if target_bearing is None:
        return []
    found = []
    for (donor_id, donor_heading), donor_curve in available.items():
        if donor_id == target_id:
            continue                       # a station may never be its own donor
        donor = stations.get(donor_id)
        if donor is None or donor["city"] != target["city"]:
            continue
        donor_bearing = (donor.get("heading_bearings") or {}).get(donor_heading)
        if donor_bearing is None:
            continue
        delta = bearing_delta_deg(target_bearing, donor_bearing)
        if delta > MAX_BEARING_DELTA_DEG:
            continue
        distance = haversine_m(target["lat"], target["lon"],
                               donor["lat"], donor["lon"])
        found.append({
            "donor_key": (donor_id, donor_heading),
            "distance_m": distance,
            "bearing_delta_deg": delta,
            "same_road": (road_number(target.get("road_ref"))
                          == road_number(donor.get("road_ref"))
                          and road_number(target.get("road_ref")) is not None),
        })
    return sorted(found, key=lambda item: item["distance_m"])


def score_band(available: dict[tuple[str, str], dict], stations: dict[str, dict],
               group_curve: dict[str, dict[int, float]], *, limit_m: float,
               same_road_only: bool) -> dict[str, Any]:
    """Score B0/B1/L1 for every target that has a donor inside the band."""
    records: list[dict[str, Any]] = []
    # A and B within the band are each other's nearest donor, so scoring both
    # counts ONE piece of evidence twice and narrows the bootstrap that judges
    # it. Each unordered station pair contributes once, nearest first.
    seen_pairs: set[frozenset[str]] = set()
    for target_key, target in sorted(available.items()):
        if not target["toward_centre"]:
            # One target per station: the two headings are mirror images, so
            # scoring both would double-count every station.
            continue
        donors = [d for d in donor_candidates(target_key, available, stations)
                  if d["distance_m"] <= limit_m
                  and (d["same_road"] or not same_road_only)]
        if not donors:
            continue
        donor = donors[0]
        pair = frozenset({target_key[0], donor["donor_key"][0]})
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        donor_curve = available[donor["donor_key"]]
        donor_shape = {obs.hour: obs.share for obs in donor_curve["rows"]}
        group = group_curve.get(target["city"])
        if group is None:
            continue

        hours, weights = target["hours"], target["weights"]
        anchor, truth = target["anchor"], target["truth"]
        local = relevel(donor_shape, hours, weights, anchor)
        pooled = relevel(group, hours, weights, anchor)
        if local is None or pooled is None:
            continue
        flat = [anchor] * len(hours)
        records.append({
            "target": f"{target_key[0]}:{target_key[1]}",
            "donor": f"{donor['donor_key'][0]}:{donor['donor_key'][1]}",
            "city": target["city"],
            "distance_m": round(donor["distance_m"], 1),
            "bearing_delta_deg": round(donor["bearing_delta_deg"], 1),
            "same_road": donor["same_road"],
            "hours": len(hours),
            "mae_b0_flat_anchor": weighted_mae(truth, flat, weights),
            "mae_b1_group_shape": weighted_mae(truth, pooled, weights),
            "mae_l1_donor_shape": weighted_mae(truth, local, weights),
        })

    if not records:
        return {"n_targets": 0, "note": "no target had an aligned donor in this band"}

    def mean(field: str) -> float:
        return sum(record[field] for record in records) / len(records)

    b0, b1, l1 = (mean("mae_b0_flat_anchor"), mean("mae_b1_group_shape"),
                  mean("mae_l1_donor_shape"))
    # Paired per-target differences: donor minus group, and donor minus flat.
    against_group = [record["mae_l1_donor_shape"] - record["mae_b1_group_shape"]
                     for record in records]
    against_flat = [record["mae_l1_donor_shape"] - record["mae_b0_flat_anchor"]
                    for record in records]
    ci_group = bootstrap_interval(against_group, seed=BOOTSTRAP_SEED)
    ci_flat = bootstrap_interval(against_flat, seed=BOOTSTRAP_SEED)
    improvement = 100 * (1 - l1 / b1) if b1 > 0 else 0.0
    return {
        "n_targets": len(records),
        "n_same_road": sum(1 for record in records if record["same_road"]),
        "median_distance_m": sorted(record["distance_m"] for record in records)[
            len(records) // 2],
        "mae_b0_flat_anchor": round(b0, 6),
        "mae_b1_group_shape": round(b1, 6),
        "mae_l1_donor_shape": round(l1, 6),
        "donor_vs_group_pct": round(improvement, 3),
        "donor_vs_group_ci": [round(value, 8) for value in ci_group] if ci_group else None,
        "donor_vs_flat_pct": round(100 * (1 - l1 / b0), 3) if b0 > 0 else 0.0,
        "donor_vs_flat_ci": [round(value, 8) for value in ci_flat] if ci_flat else None,
        # A bootstrap over a handful of pairs can print a tight interval from
        # almost no evidence, so robustness also requires the frozen minimum
        # number of independent groups that Gate M uses.
        "material": improvement >= MIN_IMPROVEMENT_PCT,
        "robust": bool(ci_group and ci_group[1] < 0
                       and len(records) >= MIN_INDEPENDENT_GROUPS),
        "sufficient_sample": len(records) >= MIN_INDEPENDENT_GROUPS,
        "min_independent_pairs": MIN_INDEPENDENT_GROUPS,
        "targets_where_donor_beat_group": sum(1 for value in against_group if value < 0),
        "targets_where_donor_lost_to_group": sum(1 for value in against_group if value > 0),
        "records": records,
    }


def measure(table_path: Path | None = None) -> dict[str, Any]:
    observations = load_legacy(table_path) if table_path else load_legacy()
    observations, _one_way = screen_two_way_stations(observations)
    observations = restrict_to_weekdays(observations)
    available = curves(observations)
    stations = load_stations()

    # The group curve is the deployed model, fitted leave-city-out so it never
    # sees the target city. The donor is a same-city station, which is the real
    # asymmetry at 107: the corridor neighbour exists, the foreign group is a
    # different kind of evidence.
    # The pooled model is a TOWARD-CENTRE D-factor: fitted on the unoriented
    # population its mirrored headings cancel exactly and it collapses to a flat
    # 0.5, which would hand the donor a win it did not earn. Fit it exactly as
    # the deployed model is fitted — on the oriented rows — and evaluate it only
    # on toward-centre targets.
    oriented = orient_toward_centre(observations)
    group_curve: dict[str, dict[int, float]] = {}
    for _name, train_index, test_index in leave_city_out(oriented):
        held = oriented[test_index[0]].city
        model = ShrunkDFactor().fit([oriented[i] for i in train_index])
        probe = [oriented[i] for i in test_index]
        group_curve[held] = {}
        for obs, prediction in zip(probe, model.predict(probe)):
            group_curve[held].setdefault(obs.hour, float(prediction))

    bands = {}
    for limit in DISTANCE_BANDS_M:
        bands[f"within_{int(limit)}m"] = score_band(
            available, stations, group_curve, limit_m=limit, same_road_only=False)
    bands["same_road_within_1500m"] = score_band(
        available, stations, group_curve, limit_m=1500.0, same_road_only=True)

    verdicts = []
    for name, band in bands.items():
        if not band.get("n_targets"):
            verdicts.append(f"{name}: no scorable target")
            continue
        if not band.get("sufficient_sample"):
            verdicts.append(
                f"{name}: INSUFFICIENT — {band['n_targets']} independent pair(s), "
                f"below the frozen minimum of {MIN_INDEPENDENT_GROUPS}; the "
                f"{band['donor_vs_group_pct']:+.1f}% is not decidable")
        elif band["material"] and band["robust"]:
            verdicts.append(f"{name}: DONOR beats the group (material and robust)")
        elif band["robust"]:
            verdicts.append(f"{name}: donor detectably better but below "
                            f"{MIN_IMPROVEMENT_PCT}%")
        elif band["donor_vs_group_pct"] < 0:
            verdicts.append(f"{name}: GROUP wins ({band['donor_vs_group_pct']:.1f}%)")
        else:
            verdicts.append(f"{name}: no distinguishable difference")

    return {
        "schema_version": SCHEMA_VERSION,
        "measured_at": datetime.now().date().isoformat(),
        "release_evidence": False,
        "question": ("With the level known locally, is a nearby aligned counter's "
                     "own shape a better source than the pooled group curve?"),
        "candidates": {
            "B0": "flat: the target's own flow-weighted anchor in every hour",
            "B1": "group shape: the deployed pooled curve, re-levelled to that anchor",
            "L1": "donor shape: the nearest aligned station's own curve, re-levelled",
        },
        "geometry_rule": {
            "max_bearing_delta_deg": MAX_BEARING_DELTA_DEG,
            "min_hours": MIN_HOURS,
            "gothenburg_reference": {
                "target": "107 southbound", "donor": "1076 southbound",
                "distance_m": 239.9, "bearing_delta_deg": 3.5,
                "band": "within_300m",
            },
        },
        "bands": bands,
        "verdicts": verdicts,
        "limitations": [
            "Diagnostic only, no preregistration: no gate may be decided here.",
            "Norwegian stations stand in for Gothenburg; whether a Gothenburg "
            "street behaves like them is exactly what no local measurement can "
            "confirm.",
            "The anchor is the target's own flow-weighted mean, so every "
            "candidate carries the same level and only shape is scored.",
            "The aggregate has no day blocks, so future-day transfer of either "
            "shape source is unmeasured.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)

    report = measure()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")

    print(f"{'band':<24} {'n':>4} {'B0 flat':>9} {'B1 group':>9} {'L1 donor':>9} "
          f"{'donor vs group':>15}")
    for name, band in report["bands"].items():
        if not band.get("n_targets"):
            print(f"{name:<24} {'—':>4}  no aligned donor")
            continue
        interval = band["donor_vs_group_ci"]
        print(f"{name:<24} {band['n_targets']:>4} {band['mae_b0_flat_anchor']:>9.5f} "
              f"{band['mae_b1_group_shape']:>9.5f} {band['mae_l1_donor_shape']:>9.5f} "
              f"{band['donor_vs_group_pct']:>+14.1f}%"
              + (f"  CI[{interval[0]:+.5f}, {interval[1]:+.5f}]" if interval else ""))
    print()
    for verdict in report["verdicts"]:
        print(f"  {verdict}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
