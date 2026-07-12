"""
signal_closure_combine.py — PLAN.md Phase D4: closure + signals combined.

Gustav's own framing of the deliverable: "when a road closes, how should the
lights adapt". Two passes, MICRO throughout (see below for why not meso):

  Pass 1  closure active, BASELINE synthetic signals (the deployed
          net.net.xml's untouched netconvert --tls.guess programs), window-
          bounded (same D1-D3 discipline: flush_s=0, begin_s/end_s from
          --window-start/--window-end) — collect disruption metrics AND the
          actually-driven post-closure routes from every evaluated seed
          (--vehroute-output).
  extract the actually-driven route per vehicle out of Pass 1's vehroute file
          (verified 2026-07-11 against 139 real rerouted vehicles, zero
          exceptions: for a rerouted vehicle the LAST <route> child of
          <routeDistribution> is the one with real exitTimes and no
          replacedOnEdge/reason markers — earlier entries are superseded
          plans kept only for audit, probability="0").
  optimize run D2's tlsCycleAdaptation.py / tlsCoordinator.py against those
          EXTRACTED routes, not the original pre-closure demand — the whole
          point of D4 is adapting signal timing to where traffic ACTUALLY
          goes once the road is closed, which is only visible after the
          runtime rerouter has acted.
  Pass 2  same closure, same window, same seeds, but with the NEWLY
          optimized signal-timing additional files applied — collect
          metrics + vehroute again.
  compare Pass 1 vs Pass 2 disruption metrics (closure_metrics.compare_metrics)
          AND route-choice stability between the two passes (PLAN.md's own
          instruction: "measure, decide" whether one iteration already
          settles route choice — this script always runs exactly two passes
          and reports the stability number; it does not loop until
          convergence).

MICRO, not meso, for both passes: PLAN.md literally says "run closure
(meso)", but D3 already established (measured, not assumed) that meso does
not execute signal programs meaningfully — and this entire phase's point is
signal-timing quality, so extracting routes from a meso run and then judging
signal quality on them would mix two regimes D3 showed disagree.

HONESTY: tls_provenance="synthetic" and D2's CAVEAT apply unchanged — every
number here is against a SYNTHETIC 90s-cycle guess, not Gothenburg's real
signal plans (PLAN.md D6, not done).

Usage:
  python3 signal_closure_combine.py --close EDGE_ID [EDGE_ID ...]
      [--window-start 07:00] [--window-end 09:00] [--seeds 3] [--out PATH]
      [--keep-scratch]
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import closure_metrics as cm
import run_scenario as rs
from signal_lab import (TLS_PROVENANCE, net_fingerprint, sumo_version,
                        tls_plan_diff, tls_timing_schedule, window_offsets_s)
from signal_optimize import (CAVEAT, paired_comparison, relative_pct, run_condition,
                             run_tls_coordinator, run_tls_cycle_adaptation)
from signal_regulation import enforce_timing_minimums


def merge_vehroute_outputs(paths: list[Path], out_path: Path) -> int:
    """Merge per-seed vehroute files while making vehicle IDs globally unique.

    Each demand variant starts vehicle IDs at the same PFE prefix. SUMO's TLS
    tools require unique IDs in their input route file, so a raw concatenation
    would silently collide. Preserve every vehicle's routeDistribution and
    prefix its ID with the run index; ``extract_final_routes`` then turns this
    exact ensemble into the route file used for timing adaptation.
    """
    out_root = ET.Element("routes")
    n = 0
    for run_index, path in enumerate(paths):
        root = ET.parse(path).getroot()
        for vehicle in root.findall("vehicle"):
            copied = copy.deepcopy(vehicle)
            vehicle_id = copied.get("id")
            if not vehicle_id:
                continue
            copied.set("id", f"run{run_index}_{vehicle_id}")
            out_root.append(copied)
            n += 1
    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return n


def extract_final_routes(vehroute_path: Path, out_path: Path) -> int:
    """The ACTUALLY-driven post-closure route per vehicle, in the plain SUMO
    route-file shape tlsCycleAdaptation.py/tlsCoordinator.py read (each
    <vehicle> with one direct <route edges="..."/> child — sumolib's
    getEdges() takes veh.route[0].edges for a non-string route, so this is
    all either tool needs; the omitted `type` attribute defaults every
    vehicle to sumolib's pce=1.0 car weighting, matching the demand's own
    overwhelmingly-car composition).

    Verified 2026-07-11 against 139 real rerouted vehicles, zero exceptions:
    when a vehicle's plan has a <routeDistribution>, the LAST <route> child
    is the one actually driven (real exitTimes, no replacedOnEdge/reason
    markers); a vehicle with no routeDistribution drove its one and only
    planned route unchanged. Returns the number of vehicles written.
    """
    root = ET.parse(vehroute_path).getroot()
    out_root = ET.Element("routes")
    n = 0
    for veh in root.findall("vehicle"):
        route_dist = veh.find("routeDistribution")
        final_route = route_dist.findall("route")[-1] if route_dist is not None \
            else veh.find("route")
        if final_route is None or not final_route.get("edges"):
            continue
        out_veh = ET.SubElement(out_root, "vehicle",
                                {"id": veh.get("id"), "depart": veh.get("depart")})
        ET.SubElement(out_veh, "route", {"edges": final_route.get("edges")})
        n += 1
    ET.ElementTree(out_root).write(out_path, xml_declaration=True, encoding="UTF-8")
    return n


def route_stability(vr1_path: Path, vr2_path: Path) -> dict:
    """Fraction of vehicles present in BOTH passes whose actually-driven edge
    sequence is IDENTICAL before vs after the signal-timing change.

    Both passes share the same seed and the same (pre-truncated) demand
    variant for the vehroute-captured seed — only the signal-timing
    additional files differ between them — so vehicle IDs are expected to
    line up almost completely; a route differs only where the changed
    signal timing itself altered a live rerouting decision. This is
    PLAN.md's "measure, decide" check: a high fraction_identical means one
    optimization pass already settled route choice; a low one means the
    optimized signals shifted routing enough that a second
    closure->extract->optimize iteration could see a materially different
    picture (not run automatically here — this only measures and reports)."""
    def load_routes(path: Path) -> dict[str, str]:
        root = ET.parse(path).getroot()
        out = {}
        for veh in root.findall("vehicle"):
            rd = veh.find("routeDistribution")
            route = rd.findall("route")[-1] if rd is not None else veh.find("route")
            if route is not None and route.get("edges"):
                out[veh.get("id")] = route.get("edges")
        return out

    r1, r2 = load_routes(vr1_path), load_routes(vr2_path)
    common = set(r1) & set(r2)
    if not common:
        return {"n_common_vehicles": 0, "n_identical_routes": 0,
                "fraction_identical": None,
                "note": "no vehicle IDs shared between the two passes' "
                        "vehroute-captured seed — route stability not "
                        "measurable this way"}
    identical = sum(1 for vid in common if r1[vid] == r2[vid])
    return {"n_common_vehicles": len(common), "n_identical_routes": identical,
            "fraction_identical": round(identical / len(common), 3)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--close", dest="edge", nargs="+", required=True,
                   metavar="EDGE_ID", help="One or more edge IDs to close.")
    p.add_argument("--window-start", default="07:00", metavar="HH:MM")
    p.add_argument("--window-end", default="09:00", metavar="HH:MM")
    p.add_argument("--seeds", type=int, default=3,
                   help="Fixed Monte Carlo seeds, 1000..1000+seeds-1 (default 3).")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: "
                        "sumo/signal_closure_combine_<edges>.json).")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Keep intermediate route/vehroute/signal files in "
                        "sumo/ instead of deleting them at the end.")
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

    prior, names = rs.load_geojson_meta()
    for e in args.edge:
        if e not in prior:
            sys.exit(f"--close {e}: not an edge in network.geojson")

    variants = rs.demand_variants(meta)
    demand_sig = rs.demand_signature(meta)
    net_fp = net_fingerprint(rs.NET_PATH)
    window_key = f"{args.window_start.replace(':', '')}_{args.window_end.replace(':', '')}"
    close_key = "+".join(args.edge)[:60]
    label = f"{close_key}_{window_key}_{demand_sig}_{net_fp}"
    streets = sorted({names.get(e, e) for e in args.edge})

    print(f"Signal-closure combine: closing {', '.join(streets)}, "
         f"{args.window_start}-{args.window_end}, {args.seeds} seed(s), MICRO")

    closures = [{"edge_id": e, "begin_s": begin_s, "end_s": end_s} for e in args.edge]
    adj = rs.build_edge_graph(set(args.edge))
    freeflow = rs.edge_freeflow_times()
    rerouter_edges = rs.edges_near(args.edge, rs.REROUTER_RADIUS_M)

    scratch: list[Path] = []
    try:
        closure_add_path = rs.SUMO_DIR / f"d4_closure_{label}.add.xml"
        rs.write_closure_additional(closure_add_path, closures, rerouter_edges)
        scratch.append(closure_add_path)

        truncated_variants = []
        n_trunc_total = n_drop_total = 0
        for vp in variants:
            fp = rs.SUMO_DIR / f"{vp.stem}_d4_{label}.rou.xml"
            t, d = rs.truncate_stranded_vehicles(vp, args.edge, fp, adj,
                                                 closures=closures, edge_travel_s=freeflow)
            n_trunc_total += t
            n_drop_total += d
            truncated_variants.append(fp)
            scratch.append(fp)
        if n_trunc_total or n_drop_total:
            print(f"  {n_trunc_total} truncated, {n_drop_total} dropped "
                 "(no detour around this closure at all)")

        print("  Pass 1: closure + BASELINE synthetic signals …")
        vr1_paths = [rs.SUMO_DIR / f"d4_vehroute1_{label}_seed{1000 + s}.xml"
                     for s in range(args.seeds)]
        vr1_merged = rs.SUMO_DIR / f"d4_vehroute1_{label}_all.xml"
        scratch += vr1_paths + [vr1_merged]
        t0 = time.time()
        pass1_metrics, pass1_runs = run_condition(
            net_path=rs.NET_PATH, add_paths=[closure_add_path],
            variants=truncated_variants, seeds=args.seeds,
            begin_s=begin_s, end_s=end_s, home=home, micro=True,
            vehroute_outputs=vr1_paths, run_label=f"d4_pass1_{label}",
            closed_edges=args.edge)
        print(f"    {time.time() - t0:.0f}s: timeLoss={pass1_metrics.total_time_loss_s:.0f}s, "
             f"{pass1_metrics.trip_count} trips, {pass1_metrics.teleport_total} teleports")

        extracted_path = rs.SUMO_DIR / f"d4_extracted_routes_{label}.rou.xml"
        scratch.append(extracted_path)
        n_vehroute = merge_vehroute_outputs(vr1_paths, vr1_merged)
        n_extracted = extract_final_routes(vr1_merged, extracted_path)
        print(f"  extracted {n_extracted}/{n_vehroute} actually-driven post-closure "
              "route(s) across all evaluated seeds")
        if n_extracted == 0:
            sys.exit("no vehicles extracted from Pass 1's vehroute-output — "
                     "cannot optimize signals against zero routes")

        print("  optimizing signals against the extracted post-closure routes …")
        adapted_raw_path = rs.SUMO_DIR / f"d4_tls_adapted_raw_{label}.add.xml"
        adapted_path = rs.SUMO_DIR / f"d4_tls_adapted_safe_{label}.add.xml"
        run_tls_cycle_adaptation(home, extracted_path, begin_s, adapted_raw_path)
        enforce_timing_minimums(rs.NET_PATH, adapted_raw_path, adapted_path)
        coordinated_path = rs.SUMO_DIR / f"d4_tls_coordinated_{label}.add.xml"
        run_tls_coordinator(home, extracted_path, adapted_path, coordinated_path)
        scratch += [adapted_raw_path, adapted_path, coordinated_path]

        print("  Pass 2: closure + OPTIMIZED signals …")
        vr2_paths = [rs.SUMO_DIR / f"d4_vehroute2_{label}_seed{1000 + s}.xml"
                     for s in range(args.seeds)]
        vr2_merged = rs.SUMO_DIR / f"d4_vehroute2_{label}_all.xml"
        scratch += vr2_paths + [vr2_merged]
        t0 = time.time()
        pass2_metrics, pass2_runs = run_condition(
            net_path=rs.NET_PATH,
            add_paths=[closure_add_path, adapted_path, coordinated_path],
            variants=truncated_variants, seeds=args.seeds,
            begin_s=begin_s, end_s=end_s, home=home, micro=True,
            vehroute_outputs=vr2_paths, run_label=f"d4_pass2_{label}",
            closed_edges=args.edge)
        print(f"    {time.time() - t0:.0f}s: timeLoss={pass2_metrics.total_time_loss_s:.0f}s, "
             f"{pass2_metrics.trip_count} trips, {pass2_metrics.teleport_total} teleports")

        comparison = cm.compare_metrics(pass1_metrics, pass2_metrics)
        merge_vehroute_outputs(vr2_paths, vr2_merged)
        stability = route_stability(vr1_merged, vr2_merged)
        plan_diff = tls_plan_diff(rs.NET_PATH, adapted_path, coordinated_path)
        timing_schedule = tls_timing_schedule(adapted_path, coordinated_path, rs.NET_PATH)
        rel_pct = relative_pct(pass1_metrics.total_time_loss_s, pass2_metrics.total_time_loss_s)
        pass_paired = paired_comparison(pass1_runs, pass2_runs)
        flag = " DISQUALIFIED" if comparison.candidate_disqualified else ""
        # Same fix as signal_optimize.py (review 2026-07-12): the paired
        # comparison was computed and stored but never printed or checked
        # against the aggregate delta it exists to cross-validate.
        sign_disagrees = (
            comparison.delta_time_loss_s != 0 and pass_paired["median_time_loss_delta_s"] != 0 and
            (comparison.delta_time_loss_s > 0) != (pass_paired["median_time_loss_delta_s"] > 0)
        )
        warn = "  ⚠ aggregate/paired-median DISAGREE in sign" if sign_disagrees else ""
        print(f"  Δ timeLoss={comparison.delta_time_loss_s:+.0f}s "
             f"({rel_pct if rel_pct is not None else 'n/a'}%){flag} "
             f"[paired median Δ={pass_paired['median_time_loss_delta_s']:+.0f}s, "
             f"stddev={pass_paired['stddev_time_loss_delta_s']:.0f}s over "
             f"{pass_paired['n_pairs']} pairs]{warn}")
        if stability["fraction_identical"] is not None:
            print(f"  route stability: {stability['fraction_identical']:.1%} of "
                 f"{stability['n_common_vehicles']} common vehicles drove an "
                 "identical route in both passes")

        result = {
            "method": "PLAN.md Phase D4: closure + signal-timing adaptation, "
                     "two-pass (baseline signals -> extract actually-driven "
                     "post-closure routes from every evaluated --vehroute-output -> optimize "
                     "tlsCycleAdaptation/tlsCoordinator against those routes "
                     "-> re-evaluate under the same closure). Always exactly "
                     "two passes; route_stability is reported so a reader can "
                     "decide whether a further iteration would matter, per "
                     "PLAN.md's 'measure, decide' instruction — this script "
                     "does not loop.",
            "closed_edges": args.edge, "streets": streets,
            "window_start": args.window_start, "window_end": args.window_end,
            "begin_s": begin_s, "end_s": end_s, "seeds": args.seeds,
            "demand_signature": demand_sig, "net_fingerprint": net_fp,
            "sumo_version": sumo_version(home), "tls_provenance": TLS_PROVENANCE,
            "caveat": CAVEAT,
            "truncated_vehicles": n_trunc_total, "dropped_vehicles": n_drop_total,
            "n_extracted_routes": n_extracted,
            "pass1_baseline_signals": {
                "metrics": dataclasses.asdict(pass1_metrics),
                "per_seed_time_loss_s": [run.metrics.total_time_loss_s for run in pass1_runs],
                "runs": [{"seed": run.seed, "demand_variant": run.demand_variant,
                          "metrics": dataclasses.asdict(run.metrics)} for run in pass1_runs],
                "disqualified": cm.is_disqualified(pass1_metrics),
            },
            "pass2_optimized_signals": {
                "metrics": dataclasses.asdict(pass2_metrics),
                "per_seed_time_loss_s": [run.metrics.total_time_loss_s for run in pass2_runs],
                "runs": [{"seed": run.seed, "demand_variant": run.demand_variant,
                          "metrics": dataclasses.asdict(run.metrics)} for run in pass2_runs],
                "disqualified": cm.is_disqualified(pass2_metrics),
            },
            "comparison": {**dataclasses.asdict(comparison),
                          "relative_time_loss_pct": rel_pct},
            "paired_comparison": pass_paired,
            "route_stability": stability,
            "tls_plan_diff": plan_diff,
            "tls_timing_schedule": timing_schedule,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        out_path = args.out or rs.SUMO_DIR / f"signal_closure_combine_{close_key}.json"
        rs.atomic_write_json(out_path, result, indent=2)
        print(f"Wrote {out_path}")
    finally:
        if args.keep_scratch:
            print(f"  kept {len(scratch)} intermediate file(s) in {rs.SUMO_DIR} (--keep-scratch)")
        else:
            removed = 0
            for p in scratch:
                try:
                    p.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            if removed:
                print(f"  cleaned up {removed} intermediate file(s)")


if __name__ == "__main__":
    main()
