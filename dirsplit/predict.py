"""
Predict the direction split for the Gothenburg sensor edges and write
sumo/direction_split.json — consumed by build_sumo_demand.py.

Usage:
  python3 -m dirsplit.predict

The central profile is the hour x day-type D-factor, partially pooled toward
50/50 and using no street features at all. It is the model that won the
leakage-free tournament in dirsplit/benchmark.py: on the population it serves
it beats 50/50 leave-city-out with a bootstrap interval excluding zero, while
the boosted per-sensor family it replaced (removed 2026-08-16) was
indistinguishable from 50/50 at best and significantly worse in its raw form.
The deployed code imports that class rather than reimplementing it, so what
ships is what was measured.

Per pair: the toward-centre edge takes the profile, the other edge the
complement, so the pair always sums to 1. q10/q90 are leave-city-out RESIDUAL
quantiles of the same model — measured transfer error, not a calibrated
interval — and build_sumo_demand builds demand VARIANTS at those bounds so the
Monte Carlo scenario spread (and the confidence number on the map) includes
direction uncertainty. Hourly values become 96 slots by linear interpolation,
clamped to [0.1, 0.9].

Which side of a pair points toward the centre is computed from the published
network geometry, so this path needs no OSM download. New sensors in
network.geojson are picked up automatically.

What this file does NOT contain: a station's published local D-factor. Where
the validated registry holds a verified `directional_reference` (today sensor
107 alone), the profile written here is re-levelled at LOAD time — see
traffic_sim/intake/direction_anchor.py and demand/intake.py's
load_anchored_direction_split. Anchoring lives there, not here, so every
consumer sees the same shares. The numbers in this file are therefore the
model's own estimate, before local evidence is applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .config import COVERAGE_REPORT, TARGET_CENTRE
from .geo import bearing_deg, radial_cos

GEO_PATH     = Path("web/data/network.geojson")
# The validated registry is the source of truth for which edge is the
# opposite carriageway of a single-direction station. It is recorded there,
# verified, rather than re-derived per run: 1076's opposite side sits 82.5 m
# away, just outside build_data's 80 m snap rule, and widening that threshold
# would change every other consumer of it.
SENSOR_REGISTRY = Path("data_in/sensors.json")
OUT_PATH     = Path("sumo/direction_split.json")

CLAMP = (0.1, 0.9)


def hourly_to_slots(hourly: np.ndarray) -> list[float]:
    slots = np.arange(96) / 4.0
    xs = np.arange(25, dtype=float)
    ys = np.append(hourly, hourly[0])
    return np.interp(slots, xs, ys).tolist()


def collect_pairs(geo: dict) -> dict[str, list[dict]]:
    """{sensor_id: [edge properties, edge properties]} for every station.

    Every sensor gets BOTH directions, not only the two-way station. Until
    2026-08-06 this filtered on level == "Total", which matched sensor 107
    alone. The other five stations measure ONE carriageway, so their opposite
    side had no estimate at all and the PFE filled it from structure with
    nothing to answer to. The registry now records a verified opposite edge per
    single-direction station, and the model runs on the pair exactly as it does
    for 107.

    The opposite edge is a DIFFERENT edge with its own features, which is what
    makes this meaningful rather than circular: edge_features() measures
    radial_cos and the residential/major half-discs relative to the direction of
    travel, so reversing an edge flips radial_cos and res_asym/major_asym
    exactly and swaps behind/ahead (verified on all four node-reverse pairs). A
    model given identical features for both sides could only ever answer 50/50.
    """
    def _midpoint(props: dict, feat: dict) -> dict:
        c = feat["geometry"]["coordinates"]
        props["_mid_lat"] = (c[0][1] + c[-1][1]) / 2
        props["_mid_lon"] = (c[0][0] + c[-1][0]) / 2
        return props

    by_edge = {f["properties"]["id"]: f for f in geo["features"]
               if f["geometry"]["type"] == "LineString"}
    opposite_of = {}
    if SENSOR_REGISTRY.exists():
        registry = json.loads(SENSOR_REGISTRY.read_text())
        rows = registry if isinstance(registry, list) else registry.get(
            "sensors", registry)
        for row in rows:
            spec = row.get("opposite_direction")
            if spec and spec.get("edge_id"):
                opposite_of[str(row["sensor_id"])] = spec["edge_id"]

    sensors: dict[str, list[dict]] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        if not p.get("sensor_id"):
            continue
        sid = str(p["sensor_id"])
        if p.get("level") == "Total":
            sensors.setdefault(sid, []).append(_midpoint(p, feat))
            continue
        opposite = by_edge.get(opposite_of.get(sid, ""))
        if opposite is None:
            print(f"{sid:<8} skipped — single-direction station with no "
                  f"verified opposite_direction in the registry")
            continue
        pair = dict(opposite["properties"])
        pair["_estimated_side"] = True          # never a measured carriageway
        sensors.setdefault(sid, []).extend(
            [_midpoint(p, feat), _midpoint(pair, opposite)])
    return sensors


def geometry_orientation(props: dict, feat_geometry: list) -> float:
    """radial_cos toward the city centre, from geometry alone.

    The boosted model needs the full OSM feature vector to orient a pair, but
    the D-factor central profile needs only WHICH side points toward the
    centre. Deriving that from the published network geometry keeps the
    deployed path free of an OSM download — and it is the same quantity
    dirsplit/features.py computes, so the two agree (verified against the
    tracked coverage report for every sensor edge).
    """
    start, end = feat_geometry[0], feat_geometry[-1]
    travel = bearing_deg(start[1], start[0], end[1], end[0])
    mid_lat = (start[1] + end[1]) / 2
    mid_lon = (start[0] + end[0]) / 2
    to_centre = bearing_deg(mid_lat, mid_lon, *TARGET_CENTRE)
    return radial_cos(travel, to_centre)


def fit_dfactor_profile(table_path: Path | None = None) -> dict:
    """Fit the tournament's winning central model on the whole training table.

    This is the SAME class the benchmark evaluated — imported, never
    reimplemented — so what ships is what was measured: an hour x day-type
    D-factor, partially pooled toward 0.5, with no street features at all.
    Street-specific structure did not transfer; time-of-day structure did.

    The stress band comes from leave-city-out residuals of the same model, so
    its width is measured transfer error rather than an assumption. It is an
    empirical band across the TRAINING cities and has not been validated on
    Gothenburg, so it stays labelled uncalibrated.
    """
    from .benchmark import (ShrunkDFactor, leave_city_out, load_legacy,
                            orient_toward_centre, screen_two_way_stations)
    from .dataset import OUT_PATH as LEGACY_TABLE

    table_path = table_path or LEGACY_TABLE
    observations = orient_toward_centre(load_legacy(table_path))
    observations, one_way = screen_two_way_stations(observations)
    weekday = [obs for obs in observations if not obs.is_weekend]
    if not weekday:
        raise ValueError(f"{table_path} contains no weekday observations")

    residuals: dict[int, list[float]] = {}
    for _name, train_index, test_index in leave_city_out(weekday):
        model = ShrunkDFactor().fit([weekday[i] for i in train_index])
        held = [weekday[i] for i in test_index]
        for obs, prediction in zip(held, model.predict(held)):
            residuals.setdefault(obs.hour, []).append(obs.share - float(prediction))

    deployed = ShrunkDFactor().fit(weekday)
    hours = list(range(24))
    central = np.array([float(deployed.predict(
        [_hour_probe(hour)])[0]) for hour in hours])
    pooled = sorted(value for values in residuals.values() for value in values)
    low, high = [], []
    for hour in hours:
        sample = sorted(residuals.get(hour) or pooled)
        low.append(np.quantile(sample, 0.1) if sample else 0.0)
        high.append(np.quantile(sample, 0.9) if sample else 0.0)
    support = {hour: sum(1 for obs in weekday if obs.hour == hour)
               for hour in hours}
    return {
        "central": np.clip(central, *CLAMP),
        "q10": np.clip(central + np.array(low), *CLAMP),
        "q90": np.clip(central + np.array(high), *CLAMP),
        "provenance": {
            "model": "shrunk_dfactor",
            "description": ("hour x day-type direction share, partially pooled "
                            "toward 0.5; no street features"),
            "prior_strength": ShrunkDFactor().prior_strength,
            "training_rows": len(weekday),
            "training_stations": len({obs.station_id for obs in weekday}),
            "one_way_station_directions_dropped": len(one_way),
            "training_table": str(table_path),
            "training_table_digest": _digest(table_path),
            "hour_support": support,
            "band": "leave_city_out_residual_q10_q90",
            "band_calibration": ("uncalibrated for Gothenburg: the residuals "
                                 "are held-out across the Norwegian training "
                                 "cities, not validated on the target domain"),
        },
    }


def _hour_probe(hour: int):
    """A minimal observation the D-factor model can be asked about."""
    from .benchmark import Observation

    return Observation(station_id="target", city="goteborg", heading="toward",
                       hour=hour, is_weekend=0, share=0.5, weight=1.0,
                       features={"radial_cos": 1.0})


def _digest(path: Path) -> str | None:
    if not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def predict_dfactor(sensors: dict[str, list[dict]], geo: dict, *,
                    verbose: bool = True) -> dict[str, dict]:
    """The measured-winner central profile, applied to every sensor pair.

    Every street gets the same toward-centre curve, because that is exactly
    what the evidence supports: the tournament found the transferable signal in
    time of day, not in street features. Which SIDE of a pair is the
    toward-centre one is still per-edge, and comes from the network geometry.
    """
    profile = fit_dfactor_profile()
    geometry = {feature["properties"]["id"]: feature["geometry"]["coordinates"]
                for feature in geo["features"]
                if feature["geometry"]["type"] == "LineString"}
    coverage = _coverage_status()
    central, q10, q90 = profile["central"], profile["q10"], profile["q90"]
    s50, s10, s90 = (hourly_to_slots(a) for a in (central, q10, q90))

    result: dict[str, dict] = {}
    if verbose:
        print(f"{'Sensor':<8} {'toward-centre edge':<26} "
              f"{'kl 07':>18} {'kl 16':>18}")
    for sid, pair in sorted(sensors.items()):
        if len(pair) != 2:
            print(f"{sid:<8} skipped — {len(pair)} edges")
            continue
        oriented = sorted(
            pair, key=lambda props: geometry_orientation(
                props, geometry[props["id"]]), reverse=True)
        e0, e1 = oriented[0]["id"], oriented[1]["id"]
        result[sid] = {
            "method": "dirsplit shrunk D-factor (hour x day type, pooled "
                      "toward 50/50; no street features) — selected by the "
                      "leakage-free model tournament",
            "central_model": profile["provenance"],
            "coverage_status": {p["id"]: coverage.get(p["id"], "?")
                                for p in oriented},
            "day_type": "weekday",
            "edge_shares":     {e0: [round(x, 3) for x in s50],
                                e1: [round(1 - x, 3) for x in s50]},
            "edge_shares_q10": {e0: [round(x, 3) for x in s10],
                                e1: [round(1 - x, 3) for x in s90]},
            "edge_shares_q90": {e0: [round(x, 3) for x in s90],
                                e1: [round(1 - x, 3) for x in s10]},
            "note": "ESTIMATED. The published local D-factor is applied later, "
                    "at load time, for stations that have one (see "
                    "traffic_sim/intake/direction_anchor.py). q10/q90 are "
                    "UNCALIBRATED stress bounds from held-out transfer "
                    "residuals, not a probability statement.",
        }
        if verbose:
            print(f"{sid:<8} {e0:<26} "
                  f"{q10[7]:.2f}–{central[7]:.2f}–{q90[7]:.2f} "
                  f"{q10[16]:.2f}–{central[16]:.2f}–{q90[16]:.2f}")
    return result


def _coverage_status() -> dict[str, str]:
    if not COVERAGE_REPORT.exists():
        return {}
    with open(COVERAGE_REPORT) as f:
        return {e["edge"]: e["status"] for e in json.load(f)["edges"]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)

    with open(GEO_PATH) as f:
        geo = json.load(f)
    sensors = collect_pairs(geo)

    result = predict_dfactor(sensors, geo)

    args.out.parent.mkdir(exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1)
    print(f"Wrote {args.out}  ({len(result)} sensors, both directions, "
          f"central model: shrunk D-factor)")


if __name__ == "__main__":
    main()
