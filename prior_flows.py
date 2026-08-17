"""
Level-3 priors: learned flow estimates for the UNMEASURED opposite directions
at single-direction stations.

Run after build_data + dirsplit predict:
  python3 prior_flows.py [--date 2025-09-16]

For each single-direction sensor (1074 V, 1076 S, 133 V, 134 SO, 2276 V) the
opposite carriageway is unmeasured. The deployed direction profile gives the
measured direction's share s(t) of the two-way total; the prior for the
opposite is then

    prior_opp(t) = measured(t) · (1 − s(t)) / s(t)     [q10/q90 band likewise]

This is explicitly a PRIOR (level 3): Agent C pulls toward it softly, inside
the level-2 bounds, and it can never override a measurement.

ONE direction estimate, read not recomputed
-------------------------------------------
Until 2026-08-16 this module loaded the LightGBM package itself and re-ran the
prediction, complete with its own re-orientation and shrinkage arithmetic. That
made a second, parallel implementation of the direction split — and after the
central model changed it would have kept serving the retired one. It now reads
the SAME artifact the demand builder reads, through the same loader, so the
published local D-factor anchor (traffic_sim/intake/direction_anchor.py) is
respected here too and there is exactly one direction estimate in the program.

The pairing comes from the validated sensor registry rather than from a fresh
OSM lookup, for the same reason it does in demand/priors.py: 1076's opposite
side sits 82.5 m away, just outside build_data's 80 m snap rule, and the
reviewed record is the authority on which edge that is.

Writes sumo/prior_flows.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from demand.intake import load_direction_split

GEO_PATH        = Path("web/data/network.geojson")
FLOWS_PATH      = Path("web/data/flows.json")
SENSOR_REGISTRY = Path("data_in/sensors.json")
OUT_PATH        = Path("sumo/prior_flows.json")


def single_direction_pairs(registry_path: Path = SENSOR_REGISTRY
                           ) -> list[tuple[str, str, str]]:
    """[(sensor_id, measured_edge, opposite_edge)] for every one-way-measured
    station. A two-way station measures its total and has no unmeasured twin."""
    payload = json.loads(Path(registry_path).read_text())
    rows = payload if isinstance(payload, list) else payload.get("sensors", [])
    pairs = []
    for row in rows:
        measured = list(row.get("approved_edge_ids") or ())
        opposite = (row.get("opposite_direction") or {}).get("edge_id")
        if len(measured) != 1 or not opposite:
            continue
        pairs.append((str(row["sensor_id"]), measured[0], str(opposite)))
    return pairs


def opposite_series(measured: np.ndarray, share: np.ndarray) -> list[float | None]:
    """measured · (1 − s) / s, with missing quarters staying missing."""
    with np.errstate(divide="ignore", invalid="ignore"):
        values = measured * (1 - share) / share
    return [None if not np.isfinite(value) else round(float(value), 1)
            for value in values]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", default="2025-09-16")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args(argv)

    with open(FLOWS_PATH) as f:
        flows = json.load(f)["flows"]
    # Each edge's OWN share at each quantile: the artifact already stores the
    # per-edge value, so no re-orientation is needed here. For any edge,
    # edge_shares_q10 is that edge's LOW share and edge_shares_q90 its HIGH one.
    shares = {key: load_direction_split(key)
              for key in ("edge_shares", "edge_shares_q10", "edge_shares_q90")}
    if not shares["edge_shares"]:
        raise SystemExit("no sumo/direction_split.json — run `make demand` or "
                         "`python3 -m dirsplit.predict` first")

    epoch = pd.Timestamp("2025-01-01")
    q0 = int((pd.Timestamp(args.date) - epoch) / pd.Timedelta(minutes=15))

    result: dict[str, dict] = {}
    print(f"{'Sensor':<7} {'mätt kant → motriktning':<58} "
          f"{'andel kl 08 (q10–q90)'}")
    for sensor_id, measured_edge, opposite_edge in single_direction_pairs():
        profile = shares["edge_shares"].get(measured_edge)
        if profile is None:
            print(f"{sensor_id:<7} {measured_edge}: no direction estimate — skipped")
            continue
        s50 = np.array(profile, dtype=float)
        s10 = np.array(shares["edge_shares_q10"].get(measured_edge, profile),
                       dtype=float)
        s90 = np.array(shares["edge_shares_q90"].get(measured_edge, profile),
                       dtype=float)
        measured = np.array([value if value is not None else np.nan
                             for value in flows[measured_edge][q0:q0 + 96]],
                            dtype=float)
        result[opposite_edge] = {
            "sensor": sensor_id, "measured_edge": measured_edge,
            "prior": opposite_series(measured, s50),
            # A LARGER measured share leaves LESS for the other side, so the
            # high share produces the low band.
            "prior_low": opposite_series(measured, s90),
            "prior_high": opposite_series(measured, s10),
            "provenance": ("level-3 prior from the deployed direction profile "
                           "(sumo/direction_split.json, with any published "
                           "local period anchor applied) on the measured twin"),
        }
        print(f"{sensor_id:<7} {measured_edge} → {opposite_edge:<24} "
              f"{1 - s50[32]:.2f} ({1 - s90[32]:.2f}–{1 - s10[32]:.2f})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"date": args.date, "n_quarters": 96, "edges": result}, f)
    print(f"Wrote {args.out}  ({len(result)} opposite-direction priors)")


if __name__ == "__main__":
    main()
