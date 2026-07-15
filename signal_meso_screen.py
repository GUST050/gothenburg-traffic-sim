"""
signal_meso_screen.py — IMPROVEMENT_PLAN.md Phase D3: is meso screening feasible?

Meso is ~20x faster than micro (measured elsewhere in this project) but
does not execute signal programs in its default (junction-control-off)
mode at all. This project's OWN deployed meso invocation already runs with
`--meso-junction-control true --meso-junction-control.limited true` (full
static-TLS behaviour, but only engaged at junctions the model judges close
to saturation). The open question this script answers: does THAT existing,
already-deployed meso configuration rank a set of alternative signal-timing
conditions in roughly the same order as real micro simulation, well enough
to use meso as a cheap pre-filter before spending micro time on the
survivors? Or is meso's TLS approximation too different from micro's to
trust for that purpose, meaning any real signal-timing search has to pay
micro's cost for every candidate?

METHOD: reuses IMPROVEMENT_PLAN.md D2's shared condition setup (signal_optimize.py) —
baseline / adapted / adapted_coordinated / actuated / delay_based — and runs
each condition BOTH in micro (real ground truth) and meso (the cheap
screen), over the SAME window and seeds. Spearman correlation between the
two condition rankings (by delta time loss vs baseline) answers the
question directly; both directions are a valid, complete D3 outcome
(IMPROVEMENT_PLAN.md's own words: "record either way").

An UNDOCUMENTED per-TLS `<param key="meso.tls.control" value="true"/>`
escape hatch was also investigated (see IMPROVEMENT_PLAN.md's D3 findings) — SUMO
1.27.1 accepts it silently (no warning either way), but no independent
confirmation of its real effect could be found in the locally available
SUMO installation (no bundled source/docs reference it), so this script
does NOT rely on it; the network-wide --meso-junction-control(.limited)
mechanism this project already deploys is used instead, since its
behaviour IS independently verified (CLAUDE.md, measured 2026-07-06).

Also reports the network's own short-approach-edge geometry near TLS
junctions (IMPROVEMENT_PLAN.md: "SUMO warns about short (<15 m) approach edges in
meso TLS — check ours") as a plain measurement, since SUMO did not
actually emit that specific warning text under this project's real
invocation despite the underlying short-edge condition being present.

Usage:
  python3 signal_meso_screen.py [--window-start 07:00] [--window-end 09:00]
      [--seeds 3] [--out PATH]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from scipy import stats as scipy_stats

import closure_metrics as cm
import run_scenario as rs
from signal_lab import TLS_PROVENANCE, net_fingerprint, sumo_version, window_offsets_s
from signal_optimize import (build_signal_conditions, condition_net_fingerprints,
                             condition_non_tls_fingerprints,
                             relative_pct, run_condition, signal_artifact_label)

SHORT_APPROACH_THRESHOLD_M = 15.0


def short_tls_approaches(net_path: Path) -> dict:
    """Every non-internal edge feeding a TLS-controlled junction, whose
    lane length is under SHORT_APPROACH_THRESHOLD_M — a plain geometric
    measurement, independent of whether SUMO's own runtime happens to
    print a warning about it (it did not, under this project's real
    invocation; see the module docstring)."""
    root = ET.parse(net_path).getroot()
    tls_ids = {tl.get("id") for tl in root.findall("tlLogic")}
    short = []
    total_approaches = 0
    for e in root.findall("edge"):
        if e.get("function") or e.get("to") not in tls_ids:
            continue
        total_approaches += 1
        lane = e.find("lane")
        length = float(lane.get("length")) if lane is not None else None
        if length is not None and length < SHORT_APPROACH_THRESHOLD_M:
            short.append({"edge": e.get("id"), "length_m": length})
    short.sort(key=lambda s: s["length_m"])
    return {"threshold_m": SHORT_APPROACH_THRESHOLD_M,
           "total_tls_approach_edges": total_approaches,
           "short_count": len(short),
           "short_pct": round(100 * len(short) / total_approaches, 1) if total_approaches else None,
           "examples": short[:10]}


def condition_correlation(names: list[str], deltas: dict
                          ) -> tuple[list[str], list[str], float | None, float | None]:
    """Spearman correlation between micro's and meso's condition rankings.

    Correlates on the RAW delta_time_loss_s values, NOT on order.index()-
    derived positions (fixed in review 2026-07-11, self-review): scipy's
    spearmanr already rank-transforms its input with correct tie handling,
    so feeding it .index() results double-ranks and silently discards any
    real tie between two conditions' actual time-loss values — every
    .index()-based "rank" list is a clean 0..N-1 permutation by
    construction, whether or not the underlying values actually differ.
    That construction also broke the degenerate-input guard: checking
    len(set(index-derived ranks)) is always == len(names) for any list
    with more than one entry, so the "cannot compute correlation" branch
    could never fire even when the underlying delta_time_loss_s values
    were genuinely all identical (e.g. an upstream metrics failure
    returning the same number for every condition). Checking the RAW
    values' distinctness instead makes the guard actually guard something.

    Returns (micro_order, meso_order, spearman_rho, p_value); rho/p_value
    are None when either side's values are degenerate (all equal)."""
    micro_vals = [deltas["micro"][n]["delta_time_loss_s"] for n in names]
    meso_vals = [deltas["meso"][n]["delta_time_loss_s"] for n in names]
    micro_order = sorted(names, key=lambda n: deltas["micro"][n]["delta_time_loss_s"])
    meso_order = sorted(names, key=lambda n: deltas["meso"][n]["delta_time_loss_s"])
    if len(set(micro_vals)) > 1 and len(set(meso_vals)) > 1:
        rho, pval = scipy_stats.spearmanr(micro_vals, meso_vals)
        return micro_order, meso_order, float(rho), float(pval)
    return micro_order, meso_order, None, None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--window-start", default="07:00", metavar="HH:MM")
    p.add_argument("--window-end", default="09:00", metavar="HH:MM")
    p.add_argument("--seeds", type=int, default=3,
                   help="Fixed Monte Carlo seeds, 1000..1000+seeds-1 (default 3).")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: sumo/signal_meso_screen_<window>.json).")
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
    demand_sig = rs.demand_signature(meta)
    baseline_net_fp = net_fingerprint(rs.NET_PATH)
    label = signal_artifact_label(args.window_start, args.window_end,
                                  demand_sig, baseline_net_fp)
    print(f"Signal meso screen: {args.window_start}-{args.window_end} window, "
         f"{args.seeds} seed(s) — micro ground truth vs meso screen")

    approach_report = short_tls_approaches(rs.NET_PATH)
    print(f"  {approach_report['short_count']}/{approach_report['total_tls_approach_edges']} "
         f"({approach_report['short_pct']}%) TLS approach edges are under "
         f"{SHORT_APPROACH_THRESHOLD_M} m")

    print("  building/reusing tlsCycleAdaptation + tlsCoordinator outputs …")
    conditions = build_signal_conditions(home, variants, begin_s, end_s, label)
    net_fps = condition_net_fingerprints(conditions)
    non_tls_net_fps = condition_non_tls_fingerprints(conditions)

    per_condition = {}
    t_total = time.time()
    for name, cfg in conditions.items():
        row = {}
        for mode, is_micro in (("micro", True), ("meso", False)):
            t0 = time.time()
            metrics, per_seed_runs = run_condition(
                net_path=cfg["net_path"], add_paths=cfg["add_paths"], variants=variants,
                seeds=args.seeds, begin_s=begin_s, end_s=end_s, home=home, micro=is_micro,
                run_label=f"d3_{name}_{mode}")
            elapsed = time.time() - t0
            print(f"  [{name}/{mode}] {elapsed:.0f}s: "
                 f"timeLoss={metrics.total_time_loss_s:.0f}s, "
                 f"{metrics.teleport_total} teleports")
            row[mode] = {"metrics": dataclasses.asdict(metrics),
                        "per_seed_time_loss_s": [run.metrics.total_time_loss_s
                                                  for run in per_seed_runs],
                        "runs": [{"seed": run.seed,
                                  "demand_variant": run.demand_variant,
                                  "metrics": dataclasses.asdict(run.metrics)}
                                 for run in per_seed_runs],
                        "elapsed_s": round(elapsed, 1)}
        per_condition[name] = row
    total_elapsed = time.time() - t_total

    # ── Δ time loss vs baseline, in EACH mode separately ─────────────────
    deltas = {"micro": {}, "meso": {}}
    for mode in ("micro", "meso"):
        baseline_metrics = cm.DisruptionMetrics(**per_condition["baseline"][mode]["metrics"])
        for name in conditions:
            if name == "baseline":
                continue
            candidate_metrics = cm.DisruptionMetrics(**per_condition[name][mode]["metrics"])
            comparison = cm.compare_metrics(baseline_metrics, candidate_metrics)
            deltas[mode][name] = {
                "delta_time_loss_s": comparison.delta_time_loss_s,
                "relative_pct": relative_pct(baseline_metrics.total_time_loss_s,
                                             candidate_metrics.total_time_loss_s),
                "disqualified": comparison.candidate_disqualified,
            }

    # ── Does meso's ranking correlate with micro's ground truth? ────────
    names = [n for n in conditions if n != "baseline"]
    micro_order, meso_order, rho, pval = condition_correlation(names, deltas)
    correlates = rho is not None and rho > 0.5
    print(f"  micro ranking (best->worst): {micro_order}")
    print(f"  meso  ranking (best->worst): {meso_order}")
    if rho is not None:
        print(f"  Spearman rho={rho:.2f} (p={pval:.3f}) -> "
             f"{'meso screening correlates well enough to use for candidate filtering' if correlates else 'meso does NOT correlate reliably — micro-only, keep windows short'}")
    else:
        print("  degenerate ranking (too few distinct values) — cannot compute correlation")

    result = {
        "method": "IMPROVEMENT_PLAN.md Phase D3: meso screening feasibility vs D1/D2 micro ground truth",
        "window_start": args.window_start, "window_end": args.window_end,
        "begin_s": begin_s, "end_s": end_s, "seeds": args.seeds,
        "short_tls_approach_edges": approach_report,
        "meso_tls_control_param_investigated": {
            "param": 'meso.tls.control="true" (per-tlLogic <param>)',
            "finding": ("SUMO 1.27.1 accepts this param silently (no warning either "
                       "way) when added to a <tlLogic> element loaded via -a, but no "
                       "independent confirmation of its semantic effect was found in "
                       "the locally available SUMO installation (no bundled source or "
                       "docs reference the key). NOT relied upon here — this script "
                       "uses the network-wide --meso-junction-control(.limited) "
                       "mechanism this project already deploys and has independently "
                       "verified instead."),
        },
        "tls_provenance": TLS_PROVENANCE,
        "recommendation_allowed": TLS_PROVENANCE != "synthetic",
        "net_fingerprint": baseline_net_fp,
        "net_fingerprints_by_condition": net_fps,
        "non_tls_network_fingerprints_by_condition": non_tls_net_fps,
        "demand_signature": demand_sig,
        "sumo_version": sumo_version(home),
        "command": sys.argv,
        "conditions": per_condition,
        "deltas_vs_baseline": deltas,
        "correlation": {
            "micro_ranking_best_to_worst": micro_order,
            "meso_ranking_best_to_worst": meso_order,
            "spearman_rho": rho, "p_value": pval,
            "meso_screening_recommended": correlates,
        },
        "elapsed_s": round(total_elapsed, 1),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_path = args.out or rs.SUMO_DIR / f"signal_meso_screen_{label}.json"
    rs.atomic_write_json(out_path, result, indent=2)
    print(f"Wrote {out_path}  ({total_elapsed:.0f}s total)")


if __name__ == "__main__":
    main()
