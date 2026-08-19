#!/usr/bin/env python3
"""Does what the map draws still agree with what the sensors say?

The published scenario carries its own ``sensor_audit`` block, but a block
the same run wrote cannot check that run. This walks the whole chain from
the raw sources instead, and only the last step is allowed to trust anything
the pipeline recorded:

  1. sensor registry + flow source  ->  per-edge Level-1 targets, recomputed
     here through demand.intake (the canonical intake, so the two-way/
     directional semantics cannot be re-derived subtly wrong)
  2. recomputed targets  ==  the targets frozen in sumo/demand_meta.json
  3. the frozen targets  vs  web/data/scenarios/<scenario>.json ``flows`` —
     the exact integers the browser colours the map with
  4. two-way stations: the sum of both directed edges the map draws  vs  the
     raw station value the source delivered

Missing is not zero: a source that did not constrain an edge-quarter leaves
None, and those pairs are excluded from every statistic rather than scored
as an observed zero.

    python3 tools/check_map_matches_sensors.py
    python3 tools/check_map_matches_sensors.py --scenario baseline --geh-max 5

Exit code 0 only if every gate passes.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLOWS = {"historical": ROOT / "web/data/flows.json",
         "forecast": ROOT / "web/data/flows_forecast.json"}
DEMAND_META = ROOT / "sumo/demand_meta.json"
SCENARIO_DIR = ROOT / "web/data/scenarios"


def geh(simulated: float, target: float) -> float:
    """The transport-standard flow comparison. 0 when both are zero."""
    denominator = simulated + target
    if denominator <= 0:
        return 0.0
    return math.sqrt(2.0 * (simulated - target) ** 2 / denominator)


def aggregate(series: list[float], per_bin: int) -> list[float]:
    return [sum(series[i:i + per_bin])
            for i in range(0, len(series) - len(series) % per_bin, per_bin)]


def summarize(pairs: list[tuple[float, float]], geh_max: float) -> dict:
    """GEH profile of (simulated, target) pairs."""
    if not pairs:
        return {"n": 0}
    values = [geh(sim, tgt) for sim, tgt in pairs]
    errors = [abs(sim - tgt) for sim, tgt in pairs]
    return {
        "n": len(pairs),
        "geh_ok_pct": 100.0 * sum(v < geh_max for v in values) / len(values),
        "max_geh": max(values),
        "mean_abs_error": sum(errors) / len(errors),
        "max_abs_error": max(errors),
        "simulated_total": sum(sim for sim, _t in pairs),
        "target_total": sum(tgt for _s, tgt in pairs),
    }


def count_trajectory_entries(traj: dict, edges: set[str],
                             n_intervals: int) -> dict[str, dict[int, int]]:
    """Per-quarter entry counts for ``edges``, re-derived from vehicle paths.

    Matches the published flow's semantics exactly: SUMO's edgeData counts a
    vehicle as ENTERED when it arrives from a previous edge, so the first
    edge of a route — where the vehicle departed — is not an entry. Counting
    it would inflate every edge people start their trips on, and the map's
    numbers would stop being comparable to the sensor targets.
    """
    index = {i: edge for i, edge in enumerate(traj["edges"]) if edge in edges}
    counts: dict[str, dict[int, int]] = {edge: {} for edge in edges}
    for vehicle in traj["vehicles"]:
        route, exits = vehicle["e"], vehicle["x"]
        for position in range(1, len(route)):
            edge = index.get(route[position])
            if edge is None or position - 1 >= len(exits):
                continue
            quarter = int(exits[position - 1]) // 900
            if 0 <= quarter < n_intervals:
                counts[edge][quarter] = counts[edge].get(quarter, 0) + 1
    return counts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default="baseline",
                        help="Published scenario name to check (default baseline).")
    parser.add_argument("--geh-max", type=float, default=5.0,
                        help="GEH a pair must stay under to count as agreeing.")
    parser.add_argument("--min-geh-ok-pct", type=float, default=100.0,
                        help="Share of pairs that must agree, per aggregation.")
    # Any archived run can be checked the same way, which is how a
    # HISTORICAL date — real measured counts rather than a forecast — gets
    # audited without republishing the live map over someone's work.
    parser.add_argument("--demand-meta", type=Path, default=DEMAND_META,
                        help="demand_meta.json to check against (default the "
                             "live sumo/ one).")
    parser.add_argument("--scenario-dir", type=Path, default=SCENARIO_DIR,
                        help="Directory holding the published scenario JSON "
                             "and index (default the live web set).")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from demand.intake import (build_targets, load_sensor_edges,
                               observed_sensor_series, target_series)

    meta = json.loads(Path(args.demand_meta).read_text())
    scenario_dir = Path(args.scenario_dir)
    scenario = json.loads((scenario_dir / f"{args.scenario}.json").read_text())
    index = json.loads((scenario_dir / "index.json").read_text())

    date = meta.get("date") or meta.get("start_date")
    source = meta["source"]
    qi_start, n_intervals = int(meta["qi_start"]), int(meta["n_intervals"])
    print(f"live demand   : {date}  source={source}  "
          f"{n_intervals} x 15 min  build_id={meta.get('build_id')}")
    print(f"scenario      : {args.scenario}  "
          f"({scenario['scenario'].get('label') or args.scenario})")

    failures: list[str] = []

    # ── The published scenario must belong to the live demand ─────────────
    entry = next((s for s in index["scenarios"]
                  if s["name"] == args.scenario), None)
    signature = (entry or {}).get("demand_signature")
    if signature != meta.get("build_id"):
        failures.append(
            f"the map shows a scenario built from demand {signature}, but "
            f"sumo/ now holds {meta.get('build_id')}")
    else:
        print(f"published set is coherent with the live demand: {signature}")

    # ── 1/2. recompute the targets and hold the build to them ─────────────
    flows = json.loads(FLOWS[source].read_text())["flows"]
    sensor_edges = load_sensor_edges()
    recomputed = target_series(
        build_targets(flows, sensor_edges, qi_start, n_intervals))
    observations = observed_sensor_series(flows, sensor_edges,
                                          qi_start, n_intervals)
    frozen = ((meta.get("sensor_targets") or {}).get("variants")
              or {}).get("edge_shares")
    if not frozen:
        failures.append("the demand build froze no edge_shares targets to check")
    else:
        mismatched = sorted(
            edge for edge in set(recomputed) | set(frozen)
            if recomputed.get(edge) != frozen.get(edge))
        if mismatched:
            # Say WHICH input moved. A changed station value and a changed
            # direction model are different findings: the first means the
            # evidence under the build changed, the second means only the
            # share this build derived from an unchanged two-way total did.
            # Collapsing them into one "targets differ" hides the one that
            # matters.
            stored_observations = meta.get("sensor_observations") or {}
            sensor_changed = sorted(
                edge for edge in mismatched
                if stored_observations.get(edge) != observations.get(edge))
            derived_changed = [edge for edge in mismatched
                               if edge not in sensor_changed]
            if sensor_changed:
                failures.append(
                    "the sensor values under this build changed since it ran: "
                    f"{sensor_changed[:3]}")
            if derived_changed:
                failures.append(
                    "the same station values now derive different directional "
                    "targets — the direction split model moved, not the "
                    f"measurement: {derived_changed[:3]}")
        else:
            print(f"targets recomputed from {FLOWS[source].name} match the "
                  f"frozen build targets exactly ({len(frozen)} edges)")

    # ── 3. what the map draws vs those targets ────────────────────────────
    drawn = scenario["flows"]
    edge_station = {edge: sensor_id
                    for sensor_id, edges in sensor_edges.items()
                    for edge in edges}
    quarter_pairs: list[tuple[float, float]] = []
    hourly_pairs: list[tuple[float, float]] = []
    per_edge: dict[str, dict] = {}
    for edge, targets in sorted(recomputed.items()):
        simulated = drawn.get(edge)
        if simulated is None:
            failures.append(f"sensor edge {edge} is missing from the map")
            continue
        pairs = [(float(simulated[i]), float(target))
                 for i, target in enumerate(targets) if target is not None]
        quarter_pairs.extend(pairs)
        per_edge[edge] = summarize(pairs, args.geh_max)
        if all(target is not None for target in targets):
            hourly_pairs.extend(zip(aggregate([float(v) for v in simulated], 4),
                                    aggregate([float(t) for t in targets], 4)))

    print("\nper directed sensor edge — the numbers the map colours, "
          "against the sensor targets")
    print(f"  {'station':>8} {'edge':38} {'n':>4} {'GEH<'+str(args.geh_max):>7}"
          f" {'maxGEH':>7} {'MAE':>7} {'sim/day':>9} {'sensor/day':>10}")
    for edge, stats in sorted(per_edge.items(),
                              key=lambda kv: edge_station.get(kv[0], "")):
        if not stats.get("n"):
            continue
        print(f"  {edge_station.get(edge, '?'):>8} {edge:38} {stats['n']:>4}"
              f" {stats['geh_ok_pct']:>6.1f}% {stats['max_geh']:>7.3f}"
              f" {stats['mean_abs_error']:>7.2f} {stats['simulated_total']:>9.0f}"
              f" {stats['target_total']:>10.0f}")

    # ── 4. two-way stations, as the physical counter reports them ─────────
    print("\nper physical station — both carriageways summed, against the raw "
          "station value")
    station_pairs: list[tuple[float, float]] = []
    for sensor_id, edges in sorted(sensor_edges.items()):
        raw = observations.get(edges[0])
        if raw is None:
            continue
        # A two-way total is delivered under both directed edges; the station
        # count is that value, not the sum of the two copies of it.
        pairs = [
            (sum(float(drawn[edge][i]) for edge in edges if edge in drawn),
             float(raw[i]))
            for i in range(n_intervals) if raw[i] is not None
        ]
        station_pairs.extend(pairs)
        stats = summarize(pairs, args.geh_max)
        if not stats.get("n"):
            continue
        print(f"  station {sensor_id:>6} ({len(edges)} edge(s)) "
              f"n={stats['n']:>3} GEH<{args.geh_max:g} {stats['geh_ok_pct']:>5.1f}%"
              f" max {stats['max_geh']:>6.3f}  map/day {stats['simulated_total']:>7.0f}"
              f"  sensor/day {stats['target_total']:>7.0f}")

    # ── 5. the layer that actually moves: the animated vehicles ───────────
    # The map draws two things from two different files. Colours and the
    # hover number come from `flows`, the 3-seed ensemble mean. The dots that
    # drive along the streets come from the trajectory sidecar, which is ONE
    # representative seed. Checking only the first would leave the layer a
    # person actually watches unverified, so re-derive the counts straight
    # from the published vehicle paths.
    trajectory_pairs: list[tuple[float, float]] = []
    traj_name = scenario.get("trajectories")
    traj_path = scenario_dir / traj_name if traj_name else None
    if traj_path and traj_path.is_file():
        traj = json.loads(traj_path.read_text())
        counts = count_trajectory_entries(traj, set(recomputed), n_intervals)
        print(f"\nanimated vehicles ({traj_name}, seed {traj.get('seed')}, "
              f"{traj.get('n_vehicles')} of {traj.get('inserted_in_run')} drawn) "
              f"— re-counted from the published paths")
        for edge, targets in sorted(recomputed.items()):
            pairs = [(float(counts[edge].get(i, 0)), float(target))
                     for i, target in enumerate(targets) if target is not None]
            trajectory_pairs.extend(pairs)
            stats = summarize(pairs, args.geh_max)
            if not stats.get("n"):
                continue
            print(f"  {edge_station.get(edge, '?'):>8} {edge:38} "
                  f"GEH<{args.geh_max:g} {stats['geh_ok_pct']:>5.1f}% "
                  f"max {stats['max_geh']:>6.3f}  drawn/day "
                  f"{stats['simulated_total']:>6.0f}  sensor/day "
                  f"{stats['target_total']:>6.0f}")
    elif traj_name:
        failures.append(f"the scenario names trajectories {traj_name}, but "
                        "the file the browser would fetch is missing")

    print()
    for label, pairs in (("15 min, directed", quarter_pairs),
                         ("15 min, animated cars", trajectory_pairs),
                         ("60 min, directed", hourly_pairs),
                         ("15 min, station total", station_pairs)):
        stats = summarize(pairs, args.geh_max)
        if not stats.get("n"):
            print(f"  {label:24s} no comparable pairs")
            continue
        ok = stats["geh_ok_pct"] >= args.min_geh_ok_pct
        print(f"  {label:24s} n={stats['n']:>5}  GEH<{args.geh_max:g}: "
              f"{stats['geh_ok_pct']:.1f}%  max {stats['max_geh']:.3f}  "
              f"MAE {stats['mean_abs_error']:.2f}  "
              f"{'OK' if ok else 'BELOW GATE'}")
        if not ok:
            failures.append(
                f"{label}: only {stats['geh_ok_pct']:.1f}% of pairs reach "
                f"GEH<{args.geh_max:g}")

    print()
    if failures:
        print("FAIL — the map and the sensors do not agree:")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("PASS — every sensor edge the map draws agrees with the sensor "
          "evidence it was calibrated to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
