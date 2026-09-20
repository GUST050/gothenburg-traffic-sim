#!/usr/bin/env python3
"""Explain an `active_closure_edge_throughput` disqualification.

The gate reports ONE number and the UI reports a generic sentence, so a
failing search says "vehicles crossed the closed edge" and nothing about
which vehicles, when, or whether SUMO had the edge shut at that moment.
There are only two ways the number can be positive, and they need opposite
fixes:

  * SUMO genuinely let a vehicle onto a sealed edge — a simulator or
    policy problem; or
  * the gate scored a bucket in which the edge was OPEN — a bookkeeping
    problem, where the window being scored is not the window that was
    simulated.

This tool decides which, from the two files the run itself wrote: the
candidate's SUMO edgeData output, and the closure additional handed to
SUMO (`run_scenario.write_closure_additional`). It reuses
`run_scenario.parse_edgedata` and
`traffic_sim.simulation.metrics.active_closure_throughput` rather than
reimplementing either — a second implementation of the measurement under
investigation could only confuse the answer.

  python3 tools/explain_closure_throughput.py \\
      --edgedata runs/<id>/.../sct_ed_candidate_1000.xml \\
      --closure-additional runs/<id>/.../sct_closure_candidate.add.xml

`--closure edge:begin:end` (repeatable) replaces the additional when only
the scored window is known, which is what a result artifact records.
"""
from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_scenario as rs
from traffic_sim.simulation import metrics as cm

INTERVAL_S = 900


# One reader, in run_scenario, so the explainer cannot disagree with the gate
# about which windows SUMO was given — which is the very question it answers.
read_closing_intervals = rs.read_closure_intervals


def parse_closure_arguments(values: list[str]) -> list[dict]:
    rows = []
    for raw in values:
        edge, begin, end = raw.rsplit(":", 2)
        rows.append({"edge_id": edge, "begin_s": int(begin), "end_s": int(end)})
    return rows


def read_raw_buckets(path: Path, edges: set[str]) -> list[dict]:
    """Every measured bucket for the closed edges, straight from the file."""
    rows = []
    for interval in ET.parse(path).getroot().iter("interval"):
        begin = float(interval.get("begin"))
        end = float(interval.get("end"))
        for edge in interval.findall("edge"):
            if edge.get("id") in edges:
                rows.append({"edge_id": edge.get("id"),
                             "begin_s": begin, "end_s": end,
                             "entered": float(edge.get("entered") or 0)})
    return rows


def scored_quarters(closures: list[dict]) -> dict[str, set[int]]:
    """The quarters the gate actually sums: fully inside a closure window."""
    scored: dict[str, set[int]] = {}
    for closure in closures:
        first = math.ceil(closure["begin_s"] / INTERVAL_S)
        last = closure["end_s"] // INTERVAL_S
        scored.setdefault(closure["edge_id"], set()).update(range(first, last))
    return scored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edgedata", type=Path, required=True)
    parser.add_argument("--closure-additional", type=Path)
    parser.add_argument("--closure", action="append", default=[],
                        metavar="EDGE:BEGIN_S:END_S")
    args = parser.parse_args(argv)
    if not args.closure_additional and not args.closure:
        parser.error("pass --closure-additional or at least one --closure")

    simulated = (read_closing_intervals(args.closure_additional)
                 if args.closure_additional else [])
    scored_closures = parse_closure_arguments(args.closure) or simulated
    edges = {row["edge_id"] for row in scored_closures} | {
        row["edge_id"] for row in simulated}

    raw = read_raw_buckets(args.edgedata, edges)
    if not raw:
        print(f"no bucket for {sorted(edges)} appears in {args.edgedata}.")
        print("With excludeEmpty=true that reads as a measured zero only if "
              "the caller zero-fills the closed edges; otherwise the gate "
              "reports None, which is 'never looked', not 'clean'.")
        return 0

    # A bucket that does not start on an absolute 15-minute boundary is
    # filed under floor(begin/900) and therefore overlaps the NEXT quarter
    # too, so a window boundary can be scored against the wrong traffic.
    unaligned = [row for row in raw if row["begin_s"] % INTERVAL_S]
    width = max(int(row["end_s"] // INTERVAL_S) for row in raw) + 1
    flows = rs.parse_edgedata(args.edgedata, width,
                              measured_empty_edges=tuple(sorted(edges)))
    total = cm.active_closure_throughput(flows, scored_closures)

    print(f"edgedata        : {args.edgedata}")
    print(f"scored windows  : " + ", ".join(
        f"{c['edge_id']} [{c['begin_s']}, {c['end_s']})" for c in scored_closures))
    if simulated:
        print(f"windows SUMO ran: " + ", ".join(
            f"{c['edge_id']} [{c['begin_s']}, {c['end_s']})" for c in simulated))
    print(f"gate total      : {total!r}  "
          f"(leak={cm.closure_edge_leaked(total)})")
    if unaligned:
        print(f"WARNING         : {len(unaligned)} bucket(s) do not start on a "
              f"15-minute boundary; every one of them is filed under the "
              f"quarter it starts in while covering part of the next.")

    scored = scored_quarters(scored_closures)
    offenders = []
    print("\nbucket                        edge            entered  scored  "
          "sealed_by_sumo")
    for row in sorted(raw, key=lambda r: (r["begin_s"], r["edge_id"])):
        if not row["entered"]:
            continue
        quarter = int(row["begin_s"] // INTERVAL_S)
        is_scored = quarter in scored.get(row["edge_id"], set())
        sealed = any(c["edge_id"] == row["edge_id"]
                     and c["begin_s"] <= row["begin_s"]
                     and row["end_s"] <= c["end_s"]
                     for c in simulated) if simulated else None
        print(f"[{row['begin_s']:>8.0f}, {row['end_s']:>8.0f})  "
              f"{row['edge_id'][:14]:<14}  {row['entered']:>7.0f}  "
              f"{str(is_scored):<6}  {sealed}")
        if is_scored and sealed is False:
            offenders.append(row)

    print()
    if not simulated:
        print("VERDICT: pass --closure-additional as well. Without the file "
              "SUMO was given, a scored bucket cannot be checked against the "
              "window that was actually simulated, which is the whole "
              "question.")
    elif offenders:
        entered = sum(row["entered"] for row in offenders)
        print(f"VERDICT: bookkeeping. {entered:.0f} vehicle(s) in "
              f"{len(offenders)} scored bucket(s) crossed while SUMO had the "
              f"edge OPEN. The scored window is wider than the simulated one; "
              f"fix the window, not the simulator.")
    elif cm.closure_edge_leaked(total):
        print("VERDICT: simulator. Every scored bucket lies inside a window "
              "SUMO had sealed, so the vehicles really did cross a closed "
              "edge. Check the teleport policy actually on the command line "
              "(`--time-to-teleport -1`) and re-read the closure additional: "
              "a sealed edge in mesoscopic SUMO admits nobody.")
    else:
        print("VERDICT: no leak in the scored window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
