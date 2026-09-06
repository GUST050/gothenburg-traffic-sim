#!/usr/bin/env python3
"""Probe candidate closure edges before freezing a held-out v3 case set.

A held-out case only tests the screening proxy if its schedules can actually
differ. Two ways a case dies before it starts, both measured here rather than
assumed:

* **Degenerate ranking.** Every schedule scores the same, so any shortlist is
  a "practical winner" — six of held-out v2's seven ranking cases were like
  this, at exactly 0.0 s spread.
* **Degenerate failure.** Every schedule is disqualified on hard integrity
  (teleports, unreachable vehicles, closure leakage), so the case carries no
  ranking evidence at all — five of v2's twelve were like this.

The probe runs the SAME machinery the campaign will (matched baseline, three
seeds, meso, the same feasibility gate) on two extreme start times: inside
the morning peak and in the evening. If a candidate's two probes are both
eligible and differ by more than the practical-equivalence band, a case built
on that edge can distinguish start times; if not, it cannot, and this is
known BEFORE the set is frozen instead of after the campaign.

This is deliberately a screen against DEGENERACY, not against the proxy: the
probes never read a proxy rank or shortlist, so which schedule the proxy
would have picked plays no part in choosing the cases it is later judged on.
Recorded in full (including rejected candidates) so the selection is auditable.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_scenario as rs                                    # noqa: E402
import suggest_closure_time as legacy                        # noqa: E402
from run_monthly_proxy_validation import (                   # noqa: E402
    _atomic_json, _baseline, _demand_archives, _read, _variants,
)
from tools.heldout_v3_selection import (                     # noqa: E402
    input_hashes, load_candidates, rank_for,
)
from traffic_sim.simulation.workspace import WorkspaceLock    # noqa: E402

DEFAULT_ROOT = Path("runs") / "heldout-v3-pilot"
# Two starts far enough apart in the demand profile to expose whether the
# objective moves with start time at all. NOT the late evening: a closure
# that is still active near the end of the simulated day leaves stuck
# vehicles no time to drain, and they are teleported at run end - measured
# in the first pilot round, where the same edge was eligible at 07:00 and
# disqualified for teleports at 19:00 with a nearly identical delay.
PROBE_WINDOWS = (("peak", 0, "07:00", 240), ("midday", 0, "12:00", 240))
PRACTICAL_EQUIVALENCE_S = 300.0


def probe_closure(*, edge_id: str, start: str, minutes: int,
                  day_offset: int = 0, archive: Path,
                  metadata: dict, variants: list[Path], baseline,
                  baseline_seeds, sumo_home: Path, run_root: Path,
                  seed_workers: int) -> dict:
    epoch = datetime.fromisoformat(str(metadata["epoch_sim"]))
    begin = (datetime.fromisoformat(f"{epoch.date()}T{start}:00")
             + timedelta(days=day_offset))
    closures = [{
        "edge_id": edge_id,
        "begin_s": int((begin - epoch).total_seconds()),
        "end_s": int((begin + timedelta(minutes=minutes) - epoch
                      ).total_seconds()),
    }]
    temporary_root = Path(tempfile.mkdtemp(prefix="v3-probe-",
                                           dir=str(run_root)))
    scratch: list[Path] = []
    try:
        # Fifth value n_rerouted (commit 3f20d70); see simulate_closure.
        # Unpacking four raised ValueError on every probe.
        metrics, truncated, dropped, per_seed, _rerouted = legacy.simulate_closure(
            name=f"probe-{start.replace(':', '')}",
            closures=closures,
            close_edges=[edge_id],
            variants=variants,
            seeds=3,
            n_intervals=int(metadata["n_intervals"]),
            duration_s=int(metadata["n_intervals"]) * 900,
            home=sumo_home,
            micro=False,
            adj=rs.build_edge_graph({edge_id}),
            freeflow=rs.edge_freeflow_times(),
            scratch=scratch,
            rerouter_edges=rs.edges_near([edge_id], rs.REROUTER_RADIUS_M),
            work_dir=temporary_root / "run",
            seed_workers=seed_workers,
        )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    interval = legacy.delta_time_loss_interval(per_seed, baseline_seeds)
    feasibility = legacy.closure_feasibility(
        metrics, baseline,
        detour=legacy.detour_availability([edge_id], rs.NET_PATH))
    return {
        "eligible": bool(feasibility["eligible"]),
        "hard_failures": list(feasibility["hard_failures"]),
        "delta_time_loss_median_s": float(interval["median_s"]),
        "delta_time_loss_min_s": float(interval["min_s"]),
        "delta_time_loss_max_s": float(interval["max_s"]),
        "truncated_unreachable": truncated,
        "dropped_unreachable": dropped,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demand-key", default="5699948becd95c03",
                        help="Archived whole-day forecast demand to probe on "
                             "(default: the 2027-09-15 weekday archive).")
    parser.add_argument("--per-class", type=int, default=3,
                        help="Candidates to probe per road class.")
    parser.add_argument("--min-detour-score", type=float, default=0.0,
                        help="Skip edges whose closure leaves less than this "
                             "share of feeding-to-leaving pairs connected "
                             "(0.5 is the practical maximum for a two-way "
                             "street, whose own opposite direction is counted "
                             "as a predecessor no vehicle may U-turn into).")
    parser.add_argument("--max-peak-load", type=float, default=None,
                        help="Skip edges busier than this at their peak "
                             "quarter: the busiest closures gridlock and are "
                             "disqualified for teleports at every start.")
    parser.add_argument("--probe", action="append", default=[],
                        metavar="LABEL:DAY:HH:MM:MINUTES",
                        help="Replace the default probe windows.")
    parser.add_argument("--road-class", action="append", default=[],
                        help="Restrict to these road classes.")
    parser.add_argument("--state-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seed-workers", type=int, default=1)
    parser.add_argument("--sumo-home", type=Path, default=None,
                        help="Defaults to the installed eclipse-sumo package.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sumo_home is None:
        args.sumo_home = rs.sumo_home()
    road_classes = args.road_class or [
        "primary", "secondary", "tertiary", "residential", "unclassified"]
    archives = _demand_archives()
    archive = archives.get(args.demand_key)
    if archive is None:
        raise SystemExit(
            f"no succeeded demand archive for {args.demand_key}; have "
            + ", ".join(sorted(archives)))
    metadata = _read(archive / "demand_meta.json")
    if int(metadata.get("n_intervals", 0)) % 96:
        raise SystemExit("probe needs a whole-day demand build")
    run_root = Path(args.state_root)
    (run_root / "baselines").mkdir(parents=True, exist_ok=True)
    results_path = run_root / "probes.json"
    results = _read(results_path) if results_path.is_file() else {
        "schema_version": 1,
        "kind": "heldout_v3_candidate_probes",
        "demand_build_key": args.demand_key,
        "demand_start_date": metadata.get("start_date"),
        "practical_equivalence_s": PRACTICAL_EQUIVALENCE_S,
        "selection_inputs": input_hashes(),
        "candidates": {},
    }
    if results.get("demand_build_key") != args.demand_key:
        raise SystemExit(f"{results_path} holds probes for a different demand "
                         "build; use a different --state-root")

    windows = PROBE_WINDOWS
    if args.probe:
        # LABEL:DAY:HH:MM:MINUTES — the day offset is what lets a probe
        # compare the SAME closure on a weekday and a Sunday inside one
        # multi-day envelope, which is the only axis on this network that
        # was found to move the objective while staying eligible.
        windows = tuple(
            (label, int(day), f"{hour}:{minute}", int(length))
            for label, day, hour, minute, length in
            (entry.split(":") for entry in args.probe))
    rows = load_candidates()
    chosen: list[dict] = []
    taken: set[str] = set()
    for road_class in road_classes:
        pool = rank_for(rows, road_class=road_class, exclude=taken)
        for row in pool:
            if len(chosen) >= args.per_class * len(road_classes):
                break
            if (args.max_peak_load is not None
                    and row["structural_peak_quarter_load"] > args.max_peak_load):
                continue
            if args.min_detour_score > 0:
                score = legacy.detour_availability(
                    [row["edge_id"]], rs.NET_PATH)["score"]
                row["detour_score"] = score
                if score is None or score < args.min_detour_score:
                    continue
            taken.add(row["edge_id"])
            chosen.append(row)
            if sum(1 for c in chosen if c["road_class"] == road_class) >= \
                    args.per_class:
                break
    print(f"probing {len(chosen)} candidate edge(s) on "
          f"{metadata.get('start_date')} ({args.demand_key})", flush=True)

    variants = _variants(archive, metadata)
    with WorkspaceLock(f"heldout_v3_pilot {archive.name}", timeout=1800):
        baseline, baseline_seeds, baseline_id, _digest = _baseline(
            demand_key=args.demand_key, archive=archive, metadata=metadata,
            variants=variants, run_root=run_root, sumo_home=args.sumo_home,
            seed_workers=args.seed_workers)
        results["matched_baseline_id"] = baseline_id
        for index, row in enumerate(chosen, 1):
            edge_id = row["edge_id"]
            record = results["candidates"].get(edge_id) or {**row, "probes": {}}
            for label, day_offset, start, minutes in windows:
                if label in record["probes"]:
                    continue
                started = time.perf_counter()
                record["probes"][label] = {
                    "start": start, "minutes": minutes,
                    "day_offset": day_offset,
                    **probe_closure(
                        edge_id=edge_id, start=start, minutes=minutes,
                        day_offset=day_offset,
                        archive=archive, metadata=metadata, variants=variants,
                        baseline=baseline, baseline_seeds=baseline_seeds,
                        sumo_home=args.sumo_home, run_root=run_root,
                        seed_workers=args.seed_workers),
                    "wall_s": round(time.perf_counter() - started, 1),
                }
                results["candidates"][edge_id] = record
                _atomic_json(results_path, results)
            probes = record["probes"]
            labels = [label for label, *_rest in windows]
            values = [probes[label]["delta_time_loss_median_s"]
                      for label in labels]
            spread = max(values) - min(values)
            record["probe_spread_s"] = round(spread, 1)
            record["discriminating"] = bool(
                all(probes[label]["eligible"] for label in labels)
                and spread > PRACTICAL_EQUIVALENCE_S)
            results["candidates"][edge_id] = record
            _atomic_json(results_path, results)
            print(f"[{index}/{len(chosen)}] {edge_id} {row['road_class']:12s} "
                  + " ".join(
                      f"{label}={'ok' if probes[label]['eligible'] else 'DQ'}"
                      for label in labels)
                  + f" spread={spread:9.1f}s "
                  f"{'DISCRIMINATING' if record['discriminating'] else ''}",
                  flush=True)

    usable = [edge for edge, record in results["candidates"].items()
              if record.get("discriminating")]
    print(f"\n{len(usable)}/{len(results['candidates'])} probed edges can "
          f"separate start times by more than {PRACTICAL_EQUIVALENCE_S:.0f} s")
    print(f"written to {results_path}")


if __name__ == "__main__":
    main()
