"""Does the transferred intraday SHAPE earn its place, once the LEVEL is already
known locally?

The question this answers
------------------------
The deployed split is two separate claims bolted together: sensor 107's
published 2025 D-factor fixes the LEVEL, and a model fitted on 81 Norwegian
stations supplies the intraday SHAPE.  Gate M scored the shape model against
50/50 — but that is the wrong baseline for the deployed system, because the
deployed system already knows the level.  The honest comparison is:

    B0  flat: predict the station's own anchor in every hour, no shape at all
    B1  anchored shape: the transferred profile, re-levelled by one log-odds
        shift so its flow-weighted period mean reproduces that same anchor
    B1u unanchored shape: the raw model output, for reference only

If B0 wins or ties, the Norwegian shape is buying nothing that the local
aggregate did not already give us, and the plan's Exit A — flat anchor — is the
honest choice.  This is exactly step 1 of the daily-split plan's Gate D
("Jämför B2 mot B1 på maskerade stationer där en periodankare beräknas från
träningsdelen"), run at the period grain the deployed anchor actually uses.

Why this is measurable at all
-----------------------------
Sensor 107 delivers only a two-way total, so Gothenburg has no per-slot
direction ground truth.  The Norwegian stations DO measure both directions, so
each one can play the part of a TOT sensor: its own flow-weighted mean is the
"published D-factor" it would have, and its per-hour shares are the truth the
prediction is scored against.  The shape model never sees the held-out
station or city; only the LEVEL is taken from the test station, which mirrors
exactly what is known at 107.

That asymmetry is the point, not a leak: locally we know the aggregate and not
the profile.  A run that also hid the level would answer Gate M's question
again instead of this one.

Usage
-----
    python3 -m tools.measure_anchored_shape_value
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dirsplit.benchmark import (BOOTSTRAP_CONFIDENCE, BOOTSTRAP_SAMPLES,
                                BOOTSTRAP_SEED, MIN_IMPROVEMENT_PCT,
                                Observation, ShrunkDFactor, bootstrap_interval,
                                leave_city_out, leave_station_out, load_legacy,
                                orient_toward_centre, restrict_to_weekdays,
                                screen_two_way_stations)
from traffic_sim.intake.direction_anchor import (DEFAULT_CLAMP, shift_share,
                                                 solve_odds_shift,
                                                 weighted_mean_share)

OUT_PATH = Path("validation/anchored_shape_value_v1.json")
SCHEMA_VERSION = "anchored_shape_value_v1"

#: A held-out station needs at least this many distinct hours before its own
#: flow-weighted mean is a usable stand-in for a published D-factor.
MIN_HOURS_PER_STATION = 12


def weighted_mae(truth: Sequence[float], prediction: Sequence[float],
                 weights: Sequence[float]) -> float:
    """Flow-weighted mean absolute error in direction share."""
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("cannot weight an error by zero total flow")
    return sum(w * abs(t - p) for t, p, w in zip(truth, prediction, weights)) / total


def evaluate_station(
    observations: Sequence[Observation],
    model: ShrunkDFactor,
    *,
    clamp: tuple[float, float] = DEFAULT_CLAMP,
) -> dict[str, Any] | None:
    """Score B0, B1 and B1u for one held-out station against its own truth."""
    rows = sorted(observations, key=lambda obs: obs.hour)
    if len({obs.hour for obs in rows} ) < MIN_HOURS_PER_STATION:
        return None
    truth = [obs.share for obs in rows]
    weights = [obs.weight for obs in rows]
    if sum(weights) <= 0:
        return None

    # The station's own flow-weighted mean plays the part of the published
    # D-factor: at 107 this number is measured and the profile is not.
    anchor = sum(w * t for t, w in zip(truth, weights)) / sum(weights)
    if not 0 < anchor < 1:
        return None

    raw = [float(value) for value in model.predict(rows)]
    # Re-level the transferred shape exactly as the deployed loader does.
    delta = solve_odds_shift(raw, weights, anchor, clamp)
    anchored = [shift_share(value, delta, clamp) for value in raw]

    flat = [anchor] * len(rows)
    achieved = weighted_mean_share(raw, weights, delta, clamp)
    return {
        "station_key": rows[0].station_key,
        "city": rows[0].city,
        "hours": len(rows),
        "anchor": round(anchor, 6),
        "anchor_achieved_by_shift": round(achieved, 6),
        "delta_logit": round(delta, 6),
        "truth_span_pp": round((max(truth) - min(truth)) * 100, 2),
        "anchored_shape_span_pp": round((max(anchored) - min(anchored)) * 100, 2),
        "mae_b0_flat_anchor": weighted_mae(truth, flat, weights),
        "mae_b1_anchored_shape": weighted_mae(truth, anchored, weights),
        "mae_b1u_unanchored_shape": weighted_mae(truth, raw, weights),
    }


def run_family(
    observations: Sequence[Observation],
    folds: Sequence[tuple[str, Sequence[int], Sequence[int]]],
) -> dict[str, Any]:
    """One fold family: fit per fold, score every held-out station."""
    stations: list[dict[str, Any]] = []
    for _name, train_index, test_index in folds:
        model = ShrunkDFactor().fit([observations[i] for i in train_index])
        grouped: dict[str, list[Observation]] = defaultdict(list)
        for index in test_index:
            grouped[observations[index].station_key].append(observations[index])
        for _key, rows in sorted(grouped.items()):
            scored = evaluate_station(rows, model)
            if scored is not None:
                stations.append(scored)

    if not stations:
        return {"stations": [], "n_stations": 0,
                "note": "no held-out station met the minimum hour count"}

    def pooled(field: str) -> float:
        return statistics.fmean(entry[field] for entry in stations)

    flat_mae = pooled("mae_b0_flat_anchor")
    shaped_mae = pooled("mae_b1_anchored_shape")
    # Paired per-station difference: negative means the shape helped.
    differences = [entry["mae_b1_anchored_shape"] - entry["mae_b0_flat_anchor"]
                   for entry in stations]
    interval = bootstrap_interval(differences)
    improvement = ((flat_mae - shaped_mae) / flat_mae * 100) if flat_mae else 0.0
    wins = sum(1 for value in differences if value < 0)
    return {
        "n_stations": len(stations),
        "mae_b0_flat_anchor": round(flat_mae, 6),
        "mae_b1_anchored_shape": round(shaped_mae, 6),
        "mae_b1u_unanchored_shape": round(pooled("mae_b1u_unanchored_shape"), 6),
        "shape_improvement_pct": round(improvement, 3),
        "paired_difference_mean": round(statistics.fmean(differences), 8),
        "bootstrap_ci": (None if interval is None
                         else [round(value, 8) for value in interval]),
        "stations_where_shape_helped": wins,
        "stations_where_shape_hurt": len(differences) - wins,
        "material": bool(improvement >= MIN_IMPROVEMENT_PCT),
        "robust": bool(interval is not None and interval[1] < 0),
        "mean_truth_span_pp": round(pooled("truth_span_pp"), 2),
        "mean_anchored_shape_span_pp": round(pooled("anchored_shape_span_pp"), 2),
        "stations": stations,
    }


def measure(table_path: Path | None = None) -> dict[str, Any]:
    observations = orient_toward_centre(
        load_legacy(table_path) if table_path else load_legacy())
    observations, one_way = screen_two_way_stations(observations)
    observations = restrict_to_weekdays(observations)

    families: dict[str, Any] = {}
    builders: Mapping[str, Callable[..., Any]] = {
        "leave_city_out": leave_city_out,
        "leave_station_out": leave_station_out,
    }
    for name, builder in builders.items():
        families[name] = run_family(observations, builder(observations))

    verdicts = []
    for name, family in families.items():
        if not family.get("n_stations"):
            verdicts.append(f"{name}: no scorable stations")
            continue
        if family["material"] and family["robust"]:
            verdicts.append(f"{name}: SHAPE (material and robust)")
        elif family["robust"]:
            verdicts.append(
                f"{name}: shape is detectable but below the {MIN_IMPROVEMENT_PCT}% bar")
        else:
            verdicts.append(f"{name}: FLAT (shape not distinguishable)")

    return {
        "schema_version": SCHEMA_VERSION,
        "measured_at": datetime.now().date().isoformat(),
        "release_evidence": False,
        "question": ("Once the LEVEL is known locally, does the transferred "
                     "intraday shape beat a flat anchor?"),
        "candidates": {
            "B0": "flat: the station's own flow-weighted anchor in every hour",
            "B1": ("anchored shape: transferred profile re-levelled by one "
                   "log-odds shift to reproduce that anchor"),
            "B1u": "unanchored shape: raw model output, reference only",
        },
        "population": {
            "table": "data/dirsplit/training_table.csv (tracked aggregate)",
            "hours": "weekday",
            "n_observations": len(observations),
            "one_way_station_directions_dropped": len(one_way),
            "min_hours_per_station": MIN_HOURS_PER_STATION,
        },
        "rule": {
            "min_improvement_pct": MIN_IMPROVEMENT_PCT,
            "bootstrap_confidence": BOOTSTRAP_CONFIDENCE,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "note": ("Reuses Gate M's frozen thresholds so the two studies are "
                     "read on the same ruler. This study is NOT Gate M and NOT "
                     "Gate D; it is a diagnostic on the tracked aggregate."),
        },
        "families": families,
        "verdicts": verdicts,
        "limitations": [
            "Diagnostic only, no preregistration: no gate may be decided here.",
            "The aggregate has no day blocks, so future-day transfer of the "
            "shape is still unmeasured — the same gap that keeps Gate M "
            "INCONCLUSIVE.",
            "The anchor is taken from the held-out station's own flow-weighted "
            "mean. That mirrors what is known at sensor 107, where the level is "
            "published and the profile is not; it is deliberate asymmetry, not "
            "shape leakage.",
            "Norwegian station hours are the truth here. Whether a Gothenburg "
            "street's profile resembles them is exactly what no local "
            "measurement can currently confirm.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--table", type=Path, default=None)
    args = parser.parse_args(argv)

    record = measure(args.table)
    print(f"population: {record['population']['n_observations']} weekday rows")
    for name, family in record["families"].items():
        if not family.get("n_stations"):
            print(f"\n{name}: {family.get('note')}")
            continue
        print(f"\n=== {name} ({family['n_stations']} held-out stations) ===")
        print(f"  B0  flat anchor        MAE {family['mae_b0_flat_anchor']:.6f}")
        print(f"  B1  anchored shape     MAE {family['mae_b1_anchored_shape']:.6f}")
        print(f"  B1u unanchored shape   MAE {family['mae_b1u_unanchored_shape']:.6f}")
        print(f"  shape improvement      {family['shape_improvement_pct']:+.2f}%"
              f"   material={family['material']}  robust={family['robust']}")
        print(f"  bootstrap CI           {family['bootstrap_ci']}")
        print(f"  helped/hurt            {family['stations_where_shape_helped']}"
              f"/{family['stations_where_shape_hurt']}")
        print(f"  true intraday span     {family['mean_truth_span_pp']} pp"
              f"   vs shape's {family['mean_anchored_shape_span_pp']} pp")
    print()
    for verdict in record["verdicts"]:
        print(f"  {verdict}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
