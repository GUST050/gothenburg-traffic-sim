"""
signal_optimize.py — PLAN.md Phase D2: off-the-shelf signal-timing
optimizers, evaluated via the D1 harness (signal_lab.py's machinery).

Runs SUMO's own tools against the currently calibrated demand for one time
window, then evaluates FIVE conditions with the SAME MICRO metric machinery
against the SAME baseline:

  baseline              — the deployed net.net.xml's untouched synthetic
                          90 s-cycle programs (netconvert --tls.guess).
  adapted               — tlsCycleAdaptation.py's per-intersection Webster
                          cycle/green-split recalculation for this window.
  adapted_coordinated   — the above, plus tlsCoordinator.py's green-wave
                          offset coordination on top.
  actuated              — SUMO's built-in gap-based actuated TLS type, as
                          a no-optimization-tool reference point.
  delay_based           — SUMO's built-in delay-based actuated TLS type,
                          same reference-point role.

actuated/delay_based are network-BUILD-time choices (verified: `sumo
--tls.default-type` does not exist, only `netconvert --tls.default-type`
does), so each needs its own network file — built from the SAME plain
nod/edg XML build_sumo_net.py uses, verified to produce byte-identical
edge IDs to the deployed network.

HONESTY (PLAN.md's own instruction for this step): every relative
improvement here is measured against a SYNTHETIC 90 s-cycle GUESS
(netconvert --tls.guess), not Gothenburg's real signal plans (PLAN.md D6,
not done). Expect possibly LARGE relative wins for exactly that reason —
absolute numbers are always reported alongside relative ones, and every
result row carries tls_provenance="synthetic" with an explicit caveat
string, matching signal_lab.py's (D1) same honesty field.

Usage:
  python3 signal_optimize.py [--window-start 07:00] [--window-end 09:00]
      [--seeds 3] [--out PATH]

Requires everything signal_lab.py requires, plus sumo/plain.nod.xml +
sumo/plain.edg.xml (written by build_sumo_net.py) for the actuated/
delay_based network variants.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path

import closure_metrics as cm
import run_scenario as rs
from signal_lab import net_fingerprint, sumo_version, window_offsets_s
from suggest_closure_time import aggregate_seed_metrics

CAVEAT = ("Measured against a SYNTHETIC netconvert --tls.guess baseline "
         "(90 s uniform cycle), not Gothenburg's real signal plans (PLAN.md "
         "D6, not yet imported). A large relative improvement here reflects "
         "how naive the baseline is, not necessarily real-world quality — "
         "read the absolute numbers, not just the percentages.")

BUILTIN_TLS_TYPES = ["actuated", "delay_based"]


def run_tls_cycle_adaptation(home: Path, route_path: Path, begin_s: int,
                             out_path: Path, program_id: str = "a") -> None:
    tool = home / "tools" / "tlsCycleAdaptation.py"
    cmd = [sys.executable, str(tool),
          "-n", str(rs.NET_PATH.resolve()), "-r", str(route_path.resolve()),
          "-b", str(begin_s), "-o", str(out_path.resolve()), "-p", program_id]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        print(res.stdout[-2000:], res.stderr[-2000:])
        sys.exit("tlsCycleAdaptation.py failed")


def run_tls_coordinator(home: Path, route_path: Path, adapted_path: Path,
                        out_path: Path) -> None:
    tool = home / "tools" / "tlsCoordinator.py"
    cmd = [sys.executable, str(tool),
          "-n", str(rs.NET_PATH.resolve()), "-r", str(route_path.resolve()),
          "-a", str(adapted_path.resolve()), "-o", str(out_path.resolve())]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        print(res.stdout[-2000:], res.stderr[-2000:])
        sys.exit("tlsCoordinator.py failed")


def build_alt_type_net(home: Path, tls_type: str, out_path: Path) -> None:
    """A network identical to the deployed one except every guessed TLS
    program uses `tls_type` instead of "static" — a netconvert-time choice
    (verified: no equivalent sumo runtime flag exists). Reuses the exact
    plain nod/edg inputs and flags build_sumo_net.py's own netconvert call
    uses (--tls.guess true, --geometry.remove false) so the only
    difference is --tls.default-type."""
    nod_path = rs.SUMO_DIR / "plain.nod.xml"
    edg_path = rs.SUMO_DIR / "plain.edg.xml"
    if not (nod_path.exists() and edg_path.exists()):
        sys.exit(f"{nod_path} / {edg_path} not found — run build_sumo_net.py first")
    cmd = [str(home / "bin" / "netconvert"),
          "-n", str(nod_path), "-e", str(edg_path), "-o", str(out_path.resolve()),
          "--tls.guess", "true", "--tls.default-type", tls_type,
          "--geometry.remove", "false", "--no-warnings", "true"]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         env={"SUMO_HOME": str(home), "PATH": "/usr/bin:/bin"},
                         timeout=300)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"netconvert failed for --tls.default-type {tls_type}")


def run_condition(*, net_path: Path, add_paths: list[Path], variants: list[Path],
                  seeds: int, begin_s: int, end_s: int, home: Path
                  ) -> tuple[cm.DisruptionMetrics, list[float]]:
    per_seed_metrics = []
    per_seed_time_loss = []
    for s in range(seeds):
        seed = 1000 + s
        route_path = variants[s % len(variants)]
        metric_paths = rs.run_sumo(seed, route_path, add_paths, end_s, home,
                                   micro=True, metrics=True, begin_s=begin_s,
                                   net_path=net_path)
        metrics = cm.build_metrics(metric_paths["tripinfo"], metric_paths["statistics"],
                                   summary_path=metric_paths["summary"])
        per_seed_metrics.append(metrics)
        per_seed_time_loss.append(metrics.total_time_loss_s)
    return aggregate_seed_metrics(per_seed_metrics), per_seed_time_loss


def relative_pct(baseline_val: float, candidate_val: float) -> float | None:
    if baseline_val == 0:
        return None
    return round(100 * (candidate_val - baseline_val) / baseline_val, 1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--window-start", default="07:00", metavar="HH:MM")
    p.add_argument("--window-end", default="09:00", metavar="HH:MM")
    p.add_argument("--seeds", type=int, default=3,
                   help="Fixed Monte Carlo seeds, 1000..1000+seeds-1 (default 3).")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: sumo/signal_optimize_<window>.json).")
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

    variants = rs.demand_variants()
    label = f"{args.window_start.replace(':', '')}_{args.window_end.replace(':', '')}"
    print(f"Signal optimize: {args.window_start}-{args.window_end} window, "
         f"{args.seeds} seed(s), MICRO — 5 conditions vs synthetic baseline")

    # ── Build the optimizer outputs and alt-type networks once. ──────────
    print("  running tlsCycleAdaptation.py …")
    adapted_path = rs.SUMO_DIR / f"tls_adapted_{label}.add.xml"
    run_tls_cycle_adaptation(home, variants[0], begin_s, adapted_path)

    print("  running tlsCoordinator.py …")
    coordinated_path = rs.SUMO_DIR / f"tls_coordinated_{label}.add.xml"
    run_tls_coordinator(home, variants[0], adapted_path, coordinated_path)

    alt_nets: dict[str, Path] = {}
    for tls_type in BUILTIN_TLS_TYPES:
        print(f"  building alternate network (--tls.default-type {tls_type}) …")
        net_path = rs.SUMO_DIR / f"net_{tls_type}.net.xml"
        build_alt_type_net(home, tls_type, net_path)
        alt_nets[tls_type] = net_path

    conditions = {
        "baseline": {"net_path": rs.NET_PATH, "add_paths": []},
        "adapted": {"net_path": rs.NET_PATH, "add_paths": [adapted_path]},
        "adapted_coordinated": {"net_path": rs.NET_PATH,
                                "add_paths": [adapted_path, coordinated_path]},
        "actuated": {"net_path": alt_nets["actuated"], "add_paths": []},
        "delay_based": {"net_path": alt_nets["delay_based"], "add_paths": []},
    }

    results = {}
    t_total = time.time()
    for name, cfg in conditions.items():
        print(f"  running condition '{name}' ({args.seeds} seeds) …")
        t0 = time.time()
        metrics, per_seed_time_loss = run_condition(
            net_path=cfg["net_path"], add_paths=cfg["add_paths"], variants=variants,
            seeds=args.seeds, begin_s=begin_s, end_s=end_s, home=home)
        elapsed = time.time() - t0
        print(f"    {elapsed:.0f}s: timeLoss={metrics.total_time_loss_s:.0f}s, "
             f"{metrics.trip_count} trips, {metrics.teleport_total} teleports")
        results[name] = {"metrics": dataclasses.asdict(metrics),
                         "per_seed_time_loss_s": per_seed_time_loss,
                         "elapsed_s": round(elapsed, 1)}
    total_elapsed = time.time() - t_total

    baseline_metrics = cm.DisruptionMetrics(**results["baseline"]["metrics"])
    comparisons = {}
    for name in conditions:
        if name == "baseline":
            continue
        candidate_metrics = cm.DisruptionMetrics(**results[name]["metrics"])
        comparison = cm.compare_metrics(baseline_metrics, candidate_metrics)
        comparisons[name] = {
            **dataclasses.asdict(comparison),
            "relative_time_loss_pct": relative_pct(
                baseline_metrics.total_time_loss_s, candidate_metrics.total_time_loss_s),
        }
        flag = " DISQUALIFIED" if comparison.candidate_disqualified else ""
        print(f"  {name}: Δ={comparison.delta_time_loss_s:+.0f}s "
             f"({comparisons[name]['relative_time_loss_pct']}%){flag}")

    result = {
        "method": "PLAN.md Phase D2: off-the-shelf signal-timing optimizers vs D1 baseline",
        "window_start": args.window_start, "window_end": args.window_end,
        "begin_s": begin_s, "end_s": end_s, "seeds": args.seeds,
        "tls_provenance": "synthetic", "caveat": CAVEAT,
        "net_fingerprint": net_fingerprint(rs.NET_PATH),
        "demand_signature": rs.demand_signature(meta),
        "sumo_version": sumo_version(home),
        "command": sys.argv,
        "conditions": results,
        "comparisons_vs_baseline": comparisons,
        "elapsed_s": round(total_elapsed, 1),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_path = args.out or rs.SUMO_DIR / f"signal_optimize_{label}.json"
    rs.atomic_write_json(out_path, result, indent=2)
    print(f"Wrote {out_path}  ({total_elapsed:.0f}s total)")


if __name__ == "__main__":
    main()
