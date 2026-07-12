"""
signal_lab.py — PLAN.md Phase D1: reproducible signal-timing experiment
harness + baseline.

Runs the CURRENTLY calibrated demand through MICROSCOPIC simulation over a
bounded time-of-day window, with a fixed set of seeds, and records
closure_metrics.py's disruption scorecard per run into a results JSON with
enough provenance (CLI args, network fingerprint, demand signature, SUMO
version) to reproduce or compare runs later — this is exactly what the
deleted ad hoc sumo/*tls_verify* artifacts (PLAN.md Phase A3) lacked.

MICRO, not meso: mesoscopic simulation does not execute signal programs at
all (CLAUDE.md, measured 2026-07-06 — meso delivery is identical whether
junction control is on or off). Signal-timing experiments are therefore a
separate, bounded-window, async analysis; meso stays the engine for
interactive closures and demand recalibration.

PROVENANCE: every net.net.xml traffic light is a netconvert --tls.guess
synthetic 90 s-cycle default (CLAUDE.md, measured 2026-07-09), not a real
Gothenburg signal plan. Every result this script writes carries
tls_provenance="synthetic" explicitly, so nothing downstream can misread
"optimized against the model's default" as "better than Gothenburg's real
signals today" (PLAN.md Phase D's own stated honesty requirement). This
becomes "city-configured" only once PLAN.md D6 imports real plans.

Usage:
  python3 signal_lab.py [--window-start 07:00] [--window-end 09:00]
      [--seeds 3] [--out PATH]

Requires sumo/demand_meta.json + sumo/calibrated*.rou.xml (run
build_sumo_demand.py first) and sumo/net.net.xml (build_sumo_net.py).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

import closure_metrics as cm
import run_scenario as rs
from suggest_closure_time import aggregate_seed_metrics

TLS_PROVENANCE = "synthetic"   # see module docstring; flips to
                               # "city-configured" only when D6 lands.


def window_offsets_s(epoch_sim: str, window_start: str, window_end: str) -> tuple[int, int]:
    """HH:MM wall-clock times on the SAME calendar date as epoch_sim, as
    offsets in seconds from epoch_sim — the same convention
    structured_closures() uses for --closure begin/end, so a window means
    the same thing here as everywhere else in this project. epoch_sim is
    assumed tz-naive local wall time (demand_meta.json's actual contract,
    verified against real output — no producer ever appends 'Z'); like
    structured_closures(), this does not special-case a tz-aware epoch."""
    epoch_ts = pd.Timestamp(epoch_sim)
    date_str = epoch_ts.strftime("%Y-%m-%d")
    start_ts = pd.Timestamp(f"{date_str}T{window_start}")
    end_ts = pd.Timestamp(f"{date_str}T{window_end}")
    if end_ts <= start_ts:
        raise ValueError(f"--window-end {window_end} must be after "
                         f"--window-start {window_start}")
    begin_s = int((start_ts - epoch_ts).total_seconds())
    end_s = int((end_ts - epoch_ts).total_seconds())
    return begin_s, end_s


def net_fingerprint(net_path: Path) -> str:
    """sha1 of the network file's bytes — changes whenever TLS programs,
    connections, or geometry change, independent of demand."""
    return hashlib.sha1(net_path.read_bytes()).hexdigest()[:12]


def sumo_version(home: Path) -> str:
    try:
        res = subprocess.run([str(home / "bin" / "sumo"), "--version"],
                             capture_output=True, text=True, timeout=30)
        first_line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else ""
        return first_line or "unknown"
    except (subprocess.SubprocessError, OSError, IndexError):
        return "unknown"


def merge_route_files(paths: list[Path], out_path: Path, prefix: str,
                      require_nonempty: bool = False) -> int:
    """Merge per-source SUMO route/vehroute files into one, making vehicle
    IDs globally unique by prefixing each with its source's index in
    ``paths``. Shared by signal_optimize.py's merge_demand_variants (the
    q10/q50/q90 direction-split demand variants reuse vehicle IDs) and
    signal_closure_combine.py's merge_vehroute_outputs (per-seed vehroute
    files reuse the same PFE-prefixed IDs too) — found near-duplicated,
    independently, in review 2026-07-12: both parse each file, deep-copy
    every <vehicle>, prefix its id, and write one merged <routes> root,
    differing only in the prefix string.

    require_nonempty (default False, matching merge_vehroute_outputs'
    prior behaviour): merge_demand_variants' caller has nothing else
    checking for zero vehicles, so it opts into ValueError immediately;
    signal_closure_combine.py's caller has its own later, more specific
    check (n_extracted == 0, after route extraction) that gives a clearer
    error message, so it leaves this False."""
    out_root = ET.Element("routes")
    count = 0
    for index, path in enumerate(paths):
        root = ET.parse(path).getroot()
        for vehicle in root.findall("vehicle"):
            copied = copy.deepcopy(vehicle)
            vehicle_id = copied.get("id")
            if not vehicle_id:
                continue
            copied.set("id", f"{prefix}{index}_{vehicle_id}")
            out_root.append(copied)
            count += 1
    if require_nonempty and not count:
        raise ValueError("cannot merge an empty set of route files")
    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return count


def _parse_tl_logics(path: Path) -> dict[str, dict]:
    """id -> {offset_s, [phase_durations_s, cycle_s]} from a net.net.xml or
    additional-file <tlLogic>. tlsCoordinator.py's own output ONLY ever
    writes offset (no <phase> children) — those entries carry offset_s
    alone, so callers merge them onto a cycle-bearing entry rather than
    treating them as a second, competing cycle source."""
    out: dict[str, dict] = {}
    for tl in ET.parse(path).getroot().iter("tlLogic"):
        phases = tl.findall("phase")
        entry: dict = {"offset_s": float(tl.get("offset", 0))}
        if phases:
            durations = [float(p.get("duration")) for p in phases]
            entry["phase_durations_s"] = durations
            entry["cycle_s"] = sum(durations)
        out[tl.get("id")] = entry
    return out


def tls_plan_diff(baseline_net_path: Path, adapted_path: Path,
                  coordinated_path: Path | None = None) -> list[dict]:
    """Per-junction signal-plan diff: baseline (the deployed net.net.xml's
    netconvert --tls.guess synthetic 90 s uniform cycle) vs the optimized
    program (tlsCycleAdaptation.py's per-junction cycle/green-split
    recalculation, with tlsCoordinator.py's offset applied on top when
    given — coordinator only ever writes offset, so its file is merged
    onto the matching adapted entry rather than parsed as a second,
    independent program).

    Pure XML parsing/diffing, no SUMO invocation — shared by D2's
    signal_optimize.py and D4's signal_closure_combine.py so a UI showing
    "how did the lights change" never has two independently-derived
    answers. Sorted by tls_id for a stable, diffable/testable order.

    max_split_change_pct is only computed when both programs have the SAME
    number of phases (the ordinary case — tlsCycleAdaptation.py rescales
    durations, it does not add or remove phases) and neither cycle is
    zero; otherwise it is reported as None rather than compared against a
    phase list of different meaning at the same index."""
    baseline = _parse_tl_logics(baseline_net_path)
    adapted = _parse_tl_logics(adapted_path)
    if coordinated_path is not None:
        coordinated = _parse_tl_logics(coordinated_path)
        for tls_id, c in coordinated.items():
            if tls_id in adapted:
                adapted[tls_id]["offset_s"] = c["offset_s"]

    diffs = []
    for tls_id, base in baseline.items():
        opt = adapted.get(tls_id)
        if opt is None or "cycle_s" not in base or "cycle_s" not in opt:
            continue
        base_cycle, opt_cycle = base["cycle_s"], opt["cycle_s"]
        cycle_delta_pct = (round(100 * (opt_cycle - base_cycle) / base_cycle, 1)
                           if base_cycle else None)
        max_split_change_pct = None
        base_phases, opt_phases = base["phase_durations_s"], opt["phase_durations_s"]
        if len(base_phases) == len(opt_phases) and base_cycle and opt_cycle:
            base_splits = [d / base_cycle for d in base_phases]
            opt_splits = [d / opt_cycle for d in opt_phases]
            max_split_change_pct = round(
                100 * max(abs(o - b) for o, b in zip(opt_splits, base_splits)), 1)
        diffs.append({
            "tls_id": tls_id,
            "cycle_before_s": base_cycle, "cycle_after_s": opt_cycle,
            "cycle_delta_pct": cycle_delta_pct,
            "offset_before_s": base["offset_s"], "offset_after_s": opt["offset_s"],
            "n_phases_before": len(base_phases), "n_phases_after": len(opt_phases),
            "max_split_change_pct": max_split_change_pct,
        })
    diffs.sort(key=lambda d: d["tls_id"])
    return diffs


def tls_timing_schedule(adapted_path: Path,
                        coordinated_path: Path | None = None,
                        net_path: Path | None = None) -> list[dict]:
    """Return green/red/yellow totals for each modeled TLS link/light."""
    programs = _parse_tl_logics(adapted_path)
    if coordinated_path is not None:
        for tls_id, coordinated in _parse_tl_logics(coordinated_path).items():
            if tls_id in programs:
                programs[tls_id]["offset_s"] = coordinated["offset_s"]

    movements = {}
    if net_path is not None:
        for connection in ET.parse(net_path).getroot().findall("connection"):
            tls_id, link_index = connection.get("tl"), connection.get("linkIndex")
            if tls_id and link_index is not None:
                movements[(tls_id, int(link_index))] = {
                    "from_edge": connection.get("from"),
                    "to_edge": connection.get("to"),
                }
    rows = []
    root = ET.parse(adapted_path).getroot()
    for tl in root.iter("tlLogic"):
        tls_id = tl.get("id")
        phases = [(float(phase.get("duration")), phase.get("state"))
                  for phase in tl.findall("phase")]
        if not phases:
            continue
        cycle_s = sum(duration for duration, _ in phases)
        all_red_s = sum(duration for duration, state in phases if set(state) == {"r"})
        for link_index in range(len(phases[0][1])):
            totals = {"green_s": 0.0, "yellow_s": 0.0,
                      "redyellow_s": 0.0, "red_s": 0.0}
            for duration, state in phases:
                signal = state[link_index]
                if signal in "Gg":
                    totals["green_s"] += duration
                elif signal == "y":
                    totals["yellow_s"] += duration
                elif signal == "u":
                    totals["redyellow_s"] += duration
                elif signal == "r":
                    totals["red_s"] += duration
            rows.append({
                "tls_id": tls_id, "link_index": link_index,
                "cycle_s": round(cycle_s, 1),
                "offset_s": round(programs.get(tls_id, {}).get("offset_s", 0.0), 1),
                "all_red_s": round(all_red_s, 1),
                **movements.get((tls_id, link_index), {}),
                **{key: round(value, 1) for key, value in totals.items()},
            })
    return sorted(rows, key=lambda row: (row["tls_id"], row["link_index"]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--window-start", default="07:00", metavar="HH:MM",
                   help="Window start, wall-clock on the calibrated day (default 07:00).")
    p.add_argument("--window-end", default="09:00", metavar="HH:MM",
                   help="Window end, wall-clock on the calibrated day (default 09:00).")
    p.add_argument("--seeds", type=int, default=3,
                   help="Fixed Monte Carlo seeds, 1000..1000+seeds-1 (default 3).")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: sumo/signal_lab_<window>.json).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.seeds < 1:
        sys.exit("--seeds must be >= 1")
    home = rs.sumo_home()
    rs.SUMO_DIR.mkdir(parents=True, exist_ok=True)

    with open(rs.SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    total_duration_s = meta["n_intervals"] * 900

    try:
        begin_s, end_s = window_offsets_s(meta["epoch_sim"], args.window_start,
                                          args.window_end)
    except ValueError as exc:
        sys.exit(str(exc))
    if not (0 <= begin_s < end_s <= total_duration_s):
        sys.exit(f"window {args.window_start}-{args.window_end} falls outside "
                 f"the calibrated demand period (0-{total_duration_s / 3600:.1f}h)")

    variants = rs.demand_variants(meta)
    print(f"Signal lab: {args.window_start}-{args.window_end} window "
         f"({(end_s - begin_s) / 60:.0f} min), {args.seeds} seed(s), MICRO, "
         f"tls_provenance={TLS_PROVENANCE}")

    per_seed = []
    t_total = time.time()
    for s in range(args.seeds):
        seed = 1000 + s
        route_path = variants[s % len(variants)]
        t0 = time.time()
        # flush_s=0: this IS a bounded time-of-day window experiment — the
        # default 3600s meso flush would let vehicles depart up to an hour
        # PAST the requested window and still count toward it (verified
        # empirically 2026-07-11: 55% contamination at the default window).
        # A vehicle that departed in-window but hasn't finished by end_s is
        # honestly tracked via unfinished_trips, not silently padded in or
        # dropped.
        metric_paths = rs.run_sumo(seed, route_path, [], end_s, home,
                                   micro=True, metrics=True, begin_s=begin_s,
                                   flush_s=0)
        elapsed = time.time() - t0
        metrics = cm.build_metrics(metric_paths["tripinfo"], metric_paths["statistics"],
                                   summary_path=metric_paths["summary"])
        print(f"  seed {seed} ({route_path.name}): {elapsed:.0f}s, "
             f"timeLoss={metrics.total_time_loss_s:.0f}s, "
             f"{metrics.trip_count} trips, {metrics.teleport_total} teleports")
        per_seed.append({"seed": seed, "route_file": route_path.name,
                         "elapsed_s": round(elapsed, 1),
                         "metrics": dataclasses.asdict(metrics)})
    total_elapsed = time.time() - t_total

    aggregate = aggregate_seed_metrics(
        [cm.DisruptionMetrics(**p["metrics"]) for p in per_seed])

    result = {
        "method": "PLAN.md Phase D1: micro signal-timing experiment harness",
        "window_start": args.window_start, "window_end": args.window_end,
        "begin_s": begin_s, "end_s": end_s,
        "seeds": [p["seed"] for p in per_seed],
        "tls_provenance": TLS_PROVENANCE,
        # Structural (not just prose) enforcement of the module docstring's
        # own honesty rule: as long as TLS_PROVENANCE is "synthetic" (the
        # only value that exists until PLAN.md D6 imports real plans), no
        # caller may present this as an operational recommendation. Added
        # 2026-07-11 per external review section 5.1's suggestion to make
        # this a checkable field, not just a caveat string a UI could
        # forget to read.
        "recommendation_allowed": TLS_PROVENANCE != "synthetic",
        "net_fingerprint": net_fingerprint(rs.NET_PATH),
        "demand_signature": rs.demand_signature(meta),
        "sumo_version": sumo_version(home),
        "command": sys.argv,
        "per_seed": per_seed,
        "aggregate": dataclasses.asdict(aggregate),
        "elapsed_s": round(total_elapsed, 1),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_path = args.out or (
        rs.SUMO_DIR /
        f"signal_lab_{args.window_start.replace(':', '')}"
        f"_{args.window_end.replace(':', '')}.json")
    rs.atomic_write_json(out_path, result, indent=2)
    print(f"Wrote {out_path}  ({total_elapsed:.0f}s total)")


if __name__ == "__main__":
    main()
