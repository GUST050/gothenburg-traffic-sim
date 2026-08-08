#!/usr/bin/env python3
"""Judge the simulation by the industry-standard DMRB criteria, on HELD-OUT data.

WHY THIS EXISTS
---------------
Every build already reports "100% GEH<5". That number is goodness of FIT: those
seven edges are the calibration targets, so reproducing them is what the solver
was asked to do, not evidence that the model generalises. Reporting it as
validation would be circular.

The only genuinely held-out data this project has is the leave-one-station-out
run (`validate_sim.py` -> web/data/loso_report.json): each fold recalibrates
with one station's data fully excluded -- bounds, priors, corridor priors and
the assignment-prior scale factor included -- then predicts that station.

This tool applies the UK Design Manual for Roads and Bridges acceptance
criteria, the most widely cited standard for traffic model calibration, to
those held-out predictions:

  * GEH < 5 for at least 85% of flow observations
  * link flows within +-15% for at least 85% of links
  * (the travel-time criterion is not applicable: no local travel-time data,
    and per CLAUDE.md none is coming)

HOW TO READ THE RESULT HONESTLY
-------------------------------
DMRB was written for models calibrated against MANY counts, where holding one
link out still leaves hundreds informing the model. Here, holding out 1 of 6
stations removes ~17% of every measurement the model has. That is a far harsher
test than the standard's intended use, so a DMRB failure here does not mean the
model is worse than a typical DMRB-passing model tested this way -- it means
this network is sensor-poor, which the project already states as its central
limitation.

It is still the right test to run, because it is the one an external reviewer
will reach for, and because failing it honestly is more useful than passing a
circular one.

Also note the single-draw caveat recorded in project memory: LOSO ratios move
0.929-1.537 across draws under IDENTICAL code. A single report's ratios carry
that much noise; the pass/fail counts below are more robust than the ratios.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

DEFAULT_REPORT = Path("web/data/loso_report.json")

GEH_SHARE_FLOOR = 85.0      # DMRB: GEH<5 on >=85% of observations
FLOW_TOLERANCE = 0.15       # DMRB: within +-15%
FLOW_SHARE_FLOOR = 85.0     # ...on >=85% of links


def evaluate(report: dict) -> dict:
    rows = []
    for station, record in sorted(report.get("stations", {}).items()):
        for edge, entry in record.get("edges", {}).items():
            measured = float(entry["measured_total"])
            simulated = float(entry["simulated_total"])
            rows.append({
                "station": station,
                "edge": edge,
                "measured_total": measured,
                "simulated_total": simulated,
                "ratio": entry.get("ratio"),
                "geh_ok_pct": float(entry["geh_ok_pct"]),
                "within_tolerance": (
                    abs(simulated - measured) / measured <= FLOW_TOLERANCE
                    if measured else False),
            })
    if not rows:
        raise ValueError("LOSO report contains no held-out stations")

    geh_pass = [r for r in rows if r["geh_ok_pct"] >= GEH_SHARE_FLOOR]
    flow_pass = [r for r in rows if r["within_tolerance"]]
    geh_share = 100.0 * len(geh_pass) / len(rows)
    flow_share = 100.0 * len(flow_pass) / len(rows)
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]

    return {
        "schema_version": 1,
        "standard": "DMRB (Design Manual for Roads and Bridges)",
        "evaluated_on": "leave-one-station-out held-out predictions",
        "not_a_calibration_fit": True,
        "window": report.get("window"),
        "held_out_edges": len(rows),
        "criteria": {
            "geh_lt_5_on_85pct_of_observations": {
                "edges_meeting": len(geh_pass),
                "edges_total": len(rows),
                "share_pct": round(geh_share, 1),
                "floor_pct": GEH_SHARE_FLOOR,
                "pass": geh_share >= FLOW_SHARE_FLOOR,
            },
            "flow_within_15pct": {
                "edges_meeting": len(flow_pass),
                "edges_total": len(rows),
                "share_pct": round(flow_share, 1),
                "floor_pct": FLOW_SHARE_FLOOR,
                "pass": flow_share >= FLOW_SHARE_FLOOR,
            },
            "travel_time_within_15pct": {
                "pass": None,
                "reason": "no local travel-time data; none is coming "
                          "(CLAUDE.md, decided 2026-07-20)",
            },
        },
        "ratio_summary": {
            "median": round(statistics.median(ratios), 3),
            "min": round(min(ratios), 3),
            "max": round(max(ratios), 3),
            "caveat": "single draw; LOSO ratios move 0.929-1.537 across draws "
                      "under identical code, so treat these as noisy",
        },
        "geh_share_summary": {
            "median_pct": round(statistics.median(
                [r["geh_ok_pct"] for r in rows]), 1),
            "min_pct": min(r["geh_ok_pct"] for r in rows),
            "max_pct": max(r["geh_ok_pct"] for r in rows),
        },
        "verdict": "pass" if (geh_share >= FLOW_SHARE_FLOOR
                              and flow_share >= FLOW_SHARE_FLOOR) else "fail",
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true",
                        help="emit the full record instead of the summary")
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"no LOSO report at {args.report} — run validate_sim.py first")
        return 1
    result = evaluate(json.loads(args.report.read_text()))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"DMRB validation — {result['evaluated_on']}")
    print(f"window {result['window']}   held-out edges {result['held_out_edges']}\n")
    print(f"{'station':<9}{'measured':>10}{'simulated':>11}{'ratio':>8}"
          f"{'GEH<5':>8}   flow +-15%")
    for row in result["rows"]:
        print(f"{row['station']:<9}{row['measured_total']:>10.0f}"
              f"{row['simulated_total']:>11.0f}{row['ratio']:>8.2f}"
              f"{row['geh_ok_pct']:>7.1f}%   "
              f"{'OK' if row['within_tolerance'] else 'outside'}")
    geh = result["criteria"]["geh_lt_5_on_85pct_of_observations"]
    flow = result["criteria"]["flow_within_15pct"]
    print(f"\n  GEH<5 on >={GEH_SHARE_FLOOR:.0f}% of quarters : "
          f"{geh['edges_meeting']}/{geh['edges_total']} edges "
          f"({geh['share_pct']}%)  -> {'PASS' if geh['pass'] else 'FAIL'}")
    print(f"  daily flow within +-15%          : "
          f"{flow['edges_meeting']}/{flow['edges_total']} edges "
          f"({flow['share_pct']}%)  -> {'PASS' if flow['pass'] else 'FAIL'}")
    print(f"  travel time                      : not applicable "
          f"({result['criteria']['travel_time_within_15pct']['reason']})")
    print(f"\n  VERDICT: {result['verdict'].upper()}")
    print("\n  Read this in context: DMRB assumes many counts, so holding one "
          "link out\n  leaves the model well informed. Here it removes ~17% of "
          "every measurement\n  the model has. The failure measures how "
          "sensor-poor the network is, which\n  is the project's stated central "
          "limitation -- not a defect introduced by it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
