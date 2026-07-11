"""
suggest_closure_time.py — "when is it least disruptive to close road X?"

PLAN.md Phase C4. Offline/batch two-stage search over the CURRENTLY
CALIBRATED demand period (whatever build_sumo_demand.py + run_scenario.py's
sumo/demand_meta.json already covers — a day or a week; this tool does not
build new demand). Standalone: does not touch web/data/scenarios or serve.py.

Method:
  1. PROXY stage (seconds, no simulation): every hourly-aligned candidate
     window of the requested duration is scored from ONE baseline SUMO run's
     edgeData — closed-edge flow and nearby-corridor flow during the window,
     both LOWER is better. The proxy is a RANKING ONLY (Borda-combined rank
     positions, no fabricated "predicted delay" number) — see PLAN.md C4.
  2. SIMULATE stage: the proxy top-k, one "most obviously low-traffic"
     control, and the proxy's worst window(s) are each simulated as a real
     time-windowed closure (reusing run_scenario.py's own truncation/
     rerouter logic) and scored with closure_metrics.py's disqualification-
     aware scorecard against the SAME baseline run.
  3. VALIDATE: Spearman correlation between proxy rank and simulated
     Δ time loss over the simulated subset, and whether the simulated best
     landed inside the proxy top-k. Both are printed and stored — a weak
     correlation is reported, not hidden.

Detour availability (whether ANY alternative path exists around the closed
edge(s) at all) is a property of the EDGE, not the time window — topology
does not change hour to hour — so it is computed once as a diagnostic, not
used to rank windows.

Usage:
  python3 suggest_closure_time.py --edge EDGE_ID [EDGE_ID ...] \\
      --duration-hours 6 [--slide-hours 1] [--top-k 15] [--extra-bad 2] \\
      [--seeds 3] [--micro] [--out PATH] [--keep-scratch]

Requires sumo/demand_meta.json + sumo/calibrated*.rou.xml (run
build_sumo_demand.py first) and sumo/net.net.xml (build_sumo_net.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy import stats as scipy_stats

import closure_metrics as cm
import run_scenario as rs

SCT_PREFIX = "sct_"   # every scratch file this tool writes into sumo/
BASELINE_SCENARIO = rs.OUT_DIR / "baseline.json"


def load_baseline_flows(demand_sig: str, n_intervals: int) -> dict[str, np.ndarray]:
    """Genuinely 'seconds, no simulation': the proxy stage's flow numbers
    come from the ALREADY-BUILT baseline scenario (web/data/scenarios/
    baseline.json) — the same Monte-Carlo-averaged per-edge/per-quarter
    flow every other part of this project treats as ground truth — instead
    of running a throwaway extra SUMO simulation. Requires the baseline to
    match the CURRENT calibrated demand exactly (same demand_signature);
    a stale or missing baseline is a caller error, not something to guess
    around silently."""
    if not BASELINE_SCENARIO.exists():
        sys.exit(f"{BASELINE_SCENARIO} not found — run `python3 run_scenario.py` "
                 "(baseline, no closure) first")
    with open(BASELINE_SCENARIO) as f:
        baseline = json.load(f)
    got_sig = baseline["scenario"]["demand_signature"]
    if got_sig != demand_sig:
        sys.exit(f"{BASELINE_SCENARIO} was built from demand_signature "
                 f"{got_sig!r}, but the currently calibrated demand is "
                 f"{demand_sig!r} — rebuild the baseline with "
                 "`python3 run_scenario.py` before suggesting closure times")
    if baseline["n_quarters"] != n_intervals:
        sys.exit(f"{BASELINE_SCENARIO} covers {baseline['n_quarters']} quarters, "
                 f"current demand covers {n_intervals} — inconsistent state, "
                 "rebuild the baseline")
    return {e: np.array([0.0 if v is None else float(v) for v in arr])
            for e, arr in baseline["flows"].items()}


# ── Window generation ────────────────────────────────────────────────────

def generate_windows(duration_s: int, total_duration_s: int,
                     slide_s: int) -> list[tuple[int, int]]:
    """Every window of length duration_s, sliding by slide_s, fully inside
    [0, total_duration_s]. E.g. duration_s=6h, total=7d, slide=1h gives 163
    windows — the exact number PLAN.md's C4 spec quotes as its example."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if duration_s > total_duration_s:
        return []
    last_begin = total_duration_s - duration_s
    begins = range(0, last_begin + 1, slide_s)
    return [(b, b + duration_s) for b in begins]


def window_quarters(begin_s: int, end_s: int, n_intervals: int) -> range:
    """Quarter indices [lo, hi) whose 900s interval overlaps [begin_s, end_s)."""
    lo = max(0, begin_s // 900)
    hi = min(n_intervals, -(-end_s // 900))   # ceil division
    return range(lo, hi)


# ── Detour availability (per edge set, NOT per window) ──────────────────

def edge_neighbors(net_path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """{edge: predecessor edges}, {edge: successor edges} from every
    <connection>, ignoring any closure — the same source build_edge_graph
    reads, just not filtered."""
    preds: dict[str, set[str]] = {}
    succs: dict[str, set[str]] = {}
    for c in ET.parse(net_path).getroot().findall("connection"):
        frm, to = c.get("from"), c.get("to")
        succs.setdefault(frm, set()).add(to)
        preds.setdefault(to, set()).add(frm)
    return preds, succs


def detour_availability(close_edges: list[str], net_path: Path) -> dict:
    """Diagnostic, computed once: can traffic still get from an edge feeding
    INTO the closure to an edge leaving it, without using any closed edge?

    Topology-only (does not depend on the time window). A score of 0 means
    the closure has no detour at all anywhere — every affected vehicle with
    no alternative route will be truncated (run_scenario.py's existing,
    tested behaviour), which is worth knowing before searching for a "best"
    time at all.

    Deliberately does NOT call run_scenario.build_edge_graph(): that
    function hardcodes the module-level run_scenario.NET_PATH rather than
    taking a path argument, so it would silently ignore the net_path this
    function was actually given (only a real bug for tests/alternate nets —
    production always passes rs.NET_PATH — but a real bug regardless,
    caught by TestDetourAvailability). Reuses edge_neighbors()'s already-
    parsed successor map directly instead."""
    closed = set(close_edges)
    preds_map, succs_map = edge_neighbors(net_path)
    preds = {e for edge in close_edges for e in preds_map.get(edge, ())} - closed
    succs = {e for edge in close_edges for e in succs_map.get(edge, ())} - closed
    if not preds or not succs:
        return {"predecessors": sorted(preds), "successors": sorted(succs),
                "reachable_pairs": 0, "total_pairs": 0, "score": None}
    adj = {e: sorted(s for s in nxt if s not in closed)
          for e, nxt in succs_map.items() if e not in closed}
    total = 0
    reach = 0
    for p in preds:
        for s in succs:
            total += 1
            if rs.reachable(adj, p, s, closed):
                reach += 1
    return {"predecessors": sorted(preds), "successors": sorted(succs),
            "reachable_pairs": reach, "total_pairs": total,
            "score": reach / total}


# ── Proxy scoring ─────────────────────────────────────────────────────────

def proxy_scores(windows: list[tuple[int, int]], close_edges: list[str],
                 corridor_edges: list[str], baseline_flows: dict[str, np.ndarray],
                 n_intervals: int) -> list[dict]:
    """Per window: mean flow on the closed edge(s) and mean flow on the
    nearby (non-closed) corridor, both from the baseline SUMO run's edgeData.
    LOWER is better for both — no combined number with invented units is
    produced here; ranking happens in rank_candidates()."""
    corridor = [e for e in corridor_edges if e not in close_edges]
    out = []
    for begin_s, end_s in windows:
        qs = list(window_quarters(begin_s, end_s, n_intervals))
        closed_vals = [baseline_flows.get(e, np.zeros(n_intervals))[qs].mean()
                       for e in close_edges] if qs else [0.0]
        corridor_vals = [baseline_flows.get(e, np.zeros(n_intervals))[qs].mean()
                         for e in corridor] if qs and corridor else [0.0]
        out.append({
            "begin_s": begin_s, "end_s": end_s,
            "closed_edge_flow": float(np.mean(closed_vals)),
            "corridor_flow": float(np.mean(corridor_vals)) if corridor else None,
        })
    return out


def rank_candidates(scored: list[dict]) -> list[dict]:
    """Borda-style combined rank: average of the two ascending rank
    positions (closed-edge flow, corridor flow), lower average = better.
    Deliberately NOT a weighted sum of flows — that would look like a
    physical quantity (PLAN.md: 'never show it as predicted delay
    minutes'); a rank position carries no such implication.

    Uses scipy's 'average' tie-handling (fractional ranks for equal
    values), NOT a plain stable sort: with a stable sort, two windows tied
    on corridor_flow get arbitrarily different integer ranks (whichever
    came first in window order), which then leaks a spurious index-order
    bias into the combined rank even when corridor_flow was actually
    UNINFORMATIVE for the comparison. Caught by
    TestProxyScoresAndRanking.test_lower_flow_window_scores_better, which
    used a corridor flow constant across windows specifically to isolate
    this."""
    n = len(scored)
    if n == 0:
        return []
    closed_vals = np.array([s["closed_edge_flow"] for s in scored])
    rank_closed = scipy_stats.rankdata(closed_vals, method="average") - 1
    has_corridor = scored[0]["corridor_flow"] is not None
    if has_corridor:
        corridor_vals = np.array([s["corridor_flow"] for s in scored])
        rank_corridor = scipy_stats.rankdata(corridor_vals, method="average") - 1
    else:
        rank_corridor = np.zeros(n)
    combined = (rank_closed + rank_corridor) / (2 if has_corridor else 1)
    out = [
        {**s, "rank_closed_edge": float(rank_closed[i]),
         "rank_corridor": float(rank_corridor[i]) if has_corridor else None,
         "combined_rank": float(combined[i])}
        for i, s in enumerate(scored)
    ]
    out.sort(key=lambda s: s["combined_rank"])
    for proxy_rank, s in enumerate(out):
        s["proxy_rank"] = proxy_rank   # 0 = best (least loaded) by the proxy
    return out


# ── Simulation of one candidate (windowed closure or baseline) ──────────

def simulate_closure(*, name: str, closures: list[dict] | None,
                     close_edges: list[str], variants: list[Path],
                     seeds: int, n_intervals: int, duration_s: int,
                     home: Path, micro: bool,
                     adj: dict[str, list[str]] | None,
                     freeflow: dict[str, float] | None,
                     scratch: list[Path]
                     ) -> tuple[cm.DisruptionMetrics, int, int, list[float]]:
    """Run `seeds` Monte Carlo replications of one candidate (or the
    baseline, when close_edges is empty) and aggregate their disruption
    metrics. Mirrors run_scenario.main()'s truncate-once-per-variant,
    reuse-across-seeds pattern exactly, but WITHOUT writing anything into
    web/data/scenarios — this tool only ever produces a results JSON
    (PLAN.md C4: 'reproducible without the web'); promoting a chosen window
    into a real scenario is a separate, later run_scenario.py invocation.

    Every intermediate file this call writes into sumo/ is appended to
    `scratch` so the caller can delete them all at the end — searching a
    handful of windows otherwise leaves dozens of route/edgeData/tripinfo
    files behind per run with no natural expiry (sumo/ is gitignored, but
    unbounded either way).

    Returns the RAW per-seed total_time_loss_s values alongside the
    aggregated metrics — C5's UI wants to show a median + seed interval
    (PLAN.md's own words), which the mean alone throws away."""
    run_variants = variants
    n_truncated = n_dropped = 0
    closure_add: list[Path] = []
    if close_edges:
        assert closures is not None and adj is not None and freeflow is not None
        cpath = rs.SUMO_DIR / f"{SCT_PREFIX}closure_{name}.add.xml"
        rerouter_edges = rs.edges_near(close_edges, rs.REROUTER_RADIUS_M)
        rs.write_closure_additional(cpath, closures, rerouter_edges)
        closure_add = [cpath]
        scratch.append(cpath)
        filtered = []
        for vp in variants:
            fp = rs.SUMO_DIR / f"{vp.stem}_{SCT_PREFIX}{name}.rou.xml"
            t, d = rs.truncate_stranded_vehicles(
                vp, close_edges, fp, adj, closures=closures,
                edge_travel_s=freeflow)
            n_truncated += t
            n_dropped += d
            filtered.append(fp)
            scratch.append(fp)
        run_variants = filtered

    per_seed_metrics: list[cm.DisruptionMetrics] = []
    for s in range(seeds):
        seed = 1000 + s
        route_path = run_variants[s % len(run_variants)]
        ed_file = rs.SUMO_DIR / f"{SCT_PREFIX}ed_{name}_{seed}.xml"
        add_path = rs.SUMO_DIR / f"{SCT_PREFIX}add_{name}_{seed}.add.xml"
        rs.write_edgedata_additional(add_path, ed_file, duration_s)
        scratch += [ed_file, add_path]
        metric_paths = rs.run_sumo(seed, route_path, [add_path] + closure_add,
                                   duration_s, home, micro=micro, metrics=True)
        scratch.extend(metric_paths.values())
        per_seed_metrics.append(cm.build_metrics(
            metric_paths["tripinfo"], metric_paths["statistics"],
            truncated_unreachable=n_truncated, dropped_unreachable=n_dropped,
            summary_path=metric_paths["summary"]))
    per_seed_time_loss = [m.total_time_loss_s for m in per_seed_metrics]
    return (aggregate_seed_metrics(per_seed_metrics), n_truncated, n_dropped,
           per_seed_time_loss)


def delta_time_loss_interval(candidate_per_seed: list[float],
                             baseline_per_seed: list[float]) -> dict:
    """Median + [min, max] of Δ time loss across seeds, for the UI's
    'median ΔtimeLoss + seed interval' requirement (PLAN.md C5) — the mean-
    only comparison in closure_metrics.MetricComparison collapses exactly
    the spread-over-seeds information the product's honesty promise wants
    shown. Each candidate seed is compared against the SAME baseline mean
    (not seed-paired — variants cycle across seeds independently on each
    side, so there is no natural 1:1 pairing to begin with; treating both
    sides as independent-seed ensembles matches how q10/q50/q90 spread is
    already treated everywhere else in this project)."""
    baseline_mean = sum(baseline_per_seed) / len(baseline_per_seed)
    deltas = sorted(c - baseline_mean for c in candidate_per_seed)
    n = len(deltas)
    median = deltas[n // 2] if n % 2 else (deltas[n // 2 - 1] + deltas[n // 2]) / 2
    return {"median_s": median, "min_s": deltas[0], "max_s": deltas[-1], "n_seeds": n}


def aggregate_seed_metrics(per_seed: list[cm.DisruptionMetrics]) -> cm.DisruptionMetrics:
    """Mean across seeds for volume-like fields (each seed is a full,
    independent replication of the same demand — summing would just count
    the same vehicles `seeds` times); SUM for teleports (a teleport in ANY
    seed is a real integrity problem, not something to average away); MAX
    for the diagnostic queue proxy. truncated/dropped are identical across
    seeds by construction (computed once per variant, not per seed) so the
    first value is authoritative."""
    n = len(per_seed)
    mean = lambda f: sum(getattr(m, f) for m in per_seed) / n
    teleport_reasons: dict[str, int] = {}
    for m in per_seed:
        for k, v in m.teleport_reasons.items():
            teleport_reasons[k] = teleport_reasons.get(k, 0) + v
    queues = [m.max_queue_vehicles for m in per_seed if m.max_queue_vehicles is not None]
    return cm.DisruptionMetrics(
        total_time_loss_s=mean("total_time_loss_s"),
        trip_count=round(mean("trip_count")),
        unfinished_trips=round(mean("unfinished_trips")),
        unfinished_waiting_trips=round(mean("unfinished_waiting_trips")),
        teleport_total=sum(m.teleport_total for m in per_seed),
        teleport_reasons=teleport_reasons,
        loaded=round(mean("loaded")),
        inserted=round(mean("inserted")),
        running_at_end=round(mean("running_at_end")),
        waiting_at_end=round(mean("waiting_at_end")),
        truncated_unreachable=per_seed[0].truncated_unreachable,
        dropped_unreachable=per_seed[0].dropped_unreachable,
        max_queue_vehicles=max(queues) if queues else None,
        closed_edge_throughput=None,
    )


# ── Candidate selection for the simulate stage ───────────────────────────

def select_candidates(ranked: list[dict], top_k: int, extra_bad: int) -> list[dict]:
    """Proxy top-k, the single most obviously low-traffic window (by closed-
    edge flow alone, ignoring corridor — a simple sanity control against the
    combined proxy), and the worst `extra_bad` windows as negative controls.
    De-duplicated, order preserved (best first, then the low-traffic control
    if new, then the worst)."""
    chosen: dict[int, dict] = {}
    for w in ranked[:top_k]:
        chosen[w["begin_s"]] = w
    low_traffic = min(ranked, key=lambda w: w["closed_edge_flow"])
    chosen.setdefault(low_traffic["begin_s"], low_traffic)
    for w in sorted(ranked, key=lambda w: -w["combined_rank"])[:extra_bad]:
        chosen.setdefault(w["begin_s"], w)
    return sorted(chosen.values(), key=lambda w: w["proxy_rank"])


# ── CLI ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--edge", nargs="+", required=True, metavar="EDGE_ID",
                   help="Directed edge(s) making up the road to close "
                        "(e.g. both directions of a two-way street).")
    p.add_argument("--duration-hours", type=float, required=True,
                   help="How long the closure must last, in hours.")
    p.add_argument("--slide-hours", type=float, default=1.0,
                   help="Candidate windows slide by this many hours (default 1).")
    p.add_argument("--top-k", type=int, default=15,
                   help="How many proxy-best windows to actually simulate (default 15).")
    p.add_argument("--extra-bad", type=int, default=2,
                   help="How many proxy-worst windows to simulate as negative "
                        "controls (default 2).")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo seeds per simulated candidate (default 3).")
    p.add_argument("--micro", action="store_true",
                   help="Use microscopic simulation (default: mesoscopic).")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: "
                        "sumo/suggest_closure_<edges>.json).")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Keep intermediate route/edgeData/tripinfo files in "
                        "sumo/ instead of deleting them at the end (for "
                        "debugging one specific candidate).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    home = rs.sumo_home()
    rs.SUMO_DIR.mkdir(parents=True, exist_ok=True)

    with open(rs.SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    total_duration_s = n_intervals * 900

    prior, names = rs.load_geojson_meta()
    for e in args.edge:
        if e not in prior:
            sys.exit(f"--edge {e}: not an edge in network.geojson")

    duration_s = round(args.duration_hours * 3600)
    slide_s = round(args.slide_hours * 3600)
    if duration_s < 900 or slide_s < 900:
        sys.exit("--duration-hours and --slide-hours must both be at least "
                 "one quarter (0.25h) — flows are only resolved to 15-min buckets")
    windows = generate_windows(duration_s, total_duration_s, slide_s)
    if not windows:
        sys.exit(f"closure duration {args.duration_hours}h does not fit inside "
                 f"the calibrated demand period ({total_duration_s / 3600:.1f}h)")

    streets = sorted({names[e] for e in args.edge})
    print(f"Suggesting closure times for {', '.join(streets)} "
         f"({args.duration_hours}h, {len(windows)} candidate windows, "
         f"slide={args.slide_hours}h)")

    diagnostic = detour_availability(args.edge, rs.NET_PATH)
    if diagnostic["score"] is None:
        print("  WARNING: closed edge has no predecessor or successor edges "
             "at all (dead-end road) — every closure here is a full stop, "
             "not a detour question.")
    elif diagnostic["score"] < 1.0:
        print(f"  NOTE: only {diagnostic['reachable_pairs']}/{diagnostic['total_pairs']} "
             f"predecessor->successor pairs have ANY detour around this closure "
             f"(topology-only check) — expect truncated/stranded vehicles "
             f"regardless of which window is chosen.")

    variants = rs.demand_variants()
    adj = rs.build_edge_graph(set(args.edge))
    freeflow = rs.edge_freeflow_times()
    rerouter_edges = rs.edges_near(args.edge, rs.REROUTER_RADIUS_M)

    # ── Proxy stage: genuinely "seconds, no simulation" — reuses the
    # already-built baseline scenario's flows. ──────────────────────────
    demand_sig = rs.demand_signature(meta)
    baseline_flows = load_baseline_flows(demand_sig, n_intervals)
    scored = proxy_scores(windows, args.edge, rerouter_edges, baseline_flows,
                          n_intervals)
    ranked = rank_candidates(scored)

    # ── ONE real baseline metrics run, shared by every simulated
    # candidate's Δ comparison (unavoidable — Δ timeLoss needs a real
    # simulated baseline; the scenario JSON only has flows, not timeLoss). ─
    scratch: list[Path] = []
    print("  running baseline (metrics) …")
    t0 = time.time()
    baseline_metrics, _, _, baseline_per_seed = simulate_closure(
        name="baseline", closures=None, close_edges=[], variants=variants,
        seeds=args.seeds, n_intervals=n_intervals, duration_s=total_duration_s,
        home=home, micro=args.micro, adj=None, freeflow=None, scratch=scratch)
    print(f"  baseline done ({time.time() - t0:.0f}s): "
         f"timeLoss={baseline_metrics.total_time_loss_s:.0f}s, "
         f"{baseline_metrics.trip_count} trips")

    # ── Simulate stage ───────────────────────────────────────────────────
    to_simulate = select_candidates(ranked, args.top_k, args.extra_bad)
    print(f"  simulating {len(to_simulate)} of {len(windows)} candidates …")
    simulated = []
    for i, w in enumerate(to_simulate):
        name = f"w{w['begin_s']}"
        closures = [{"edge_id": e, "begin_s": w["begin_s"], "end_s": w["end_s"]}
                   for e in args.edge]
        t0 = time.time()
        metrics, n_trunc, n_drop, candidate_per_seed = simulate_closure(
            name=name, closures=closures, close_edges=args.edge,
            variants=variants, seeds=args.seeds, n_intervals=n_intervals,
            duration_s=total_duration_s, home=home, micro=args.micro,
            adj=adj, freeflow=freeflow, scratch=scratch)
        comparison = cm.compare_metrics(baseline_metrics, metrics)
        interval = delta_time_loss_interval(candidate_per_seed, baseline_per_seed)
        elapsed = time.time() - t0
        print(f"    [{i+1}/{len(to_simulate)}] proxy_rank={w['proxy_rank']} "
             f"begin={w['begin_s']}s  ΔtimeLoss median={interval['median_s']:+.0f}s "
             f"[{interval['min_s']:+.0f}, {interval['max_s']:+.0f}]"
             f"{'  DISQUALIFIED: ' + ','.join(comparison.disqualification_reasons) if comparison.candidate_disqualified else ''}"
             f"  ({elapsed:.0f}s)")
        simulated.append({
            "window": w, "metrics": dataclasses.asdict(metrics),
            "comparison": dataclasses.asdict(comparison),
            "delta_time_loss_interval": interval,
            "truncated_vehicles": n_trunc, "dropped_vehicles": n_drop,
        })

    # ── Validate the proxy ───────────────────────────────────────────────
    eligible = [s for s in simulated if not s["comparison"]["candidate_disqualified"]]
    correlation = None
    if len(eligible) >= 3:
        proxy_ranks = [s["window"]["proxy_rank"] for s in eligible]
        deltas = [s["comparison"]["delta_time_loss_s"] for s in eligible]
        rho, pval = scipy_stats.spearmanr(proxy_ranks, deltas)
        correlation = {"spearman_rho": float(rho), "p_value": float(pval),
                       "n": len(eligible),
                       "interpretation": (
                           "proxy rank and simulated delay both increase together "
                           "(as expected) — trust the ranking"
                           if rho > 0.3 else
                           "WEAK OR NO correlation between the proxy ranking and "
                           "simulated delay — do not trust the proxy ranking for "
                           "this edge/duration; widen --top-k instead")}
        print(f"  Spearman rho={rho:.2f} (p={pval:.3f}, n={len(eligible)}): "
             f"{correlation['interpretation']}")
    else:
        print("  fewer than 3 non-disqualified simulated candidates — "
             "skipping correlation check")

    best = min(eligible, key=lambda s: s["comparison"]["delta_time_loss_s"],
              default=None)
    best_in_topk = (best is not None and
                    best["window"]["proxy_rank"] < args.top_k)
    if best is not None:
        flag = "" if best_in_topk else "  (OUTSIDE proxy top-k — widen --top-k)"
        print(f"  simulated best: begin={best['window']['begin_s']}s "
             f"ΔtimeLoss={best['comparison']['delta_time_loss_s']:+.0f}s{flag}")

    result = {
        "method": "PLAN.md Phase C4: proxy-ranked hourly windows, "
                 "top-k + controls simulated, Spearman-validated",
        "edges": args.edge, "streets": streets,
        "duration_hours": args.duration_hours, "slide_hours": args.slide_hours,
        "total_duration_s": total_duration_s, "n_candidate_windows": len(windows),
        "top_k": args.top_k, "extra_bad": args.extra_bad, "seeds": args.seeds,
        "micro": args.micro,
        "demand_signature": rs.demand_signature(meta),
        "epoch_sim": meta["epoch_sim"],
        "detour_availability": diagnostic,
        "baseline_metrics": dataclasses.asdict(baseline_metrics),
        "baseline_per_seed_time_loss_s": baseline_per_seed,
        "proxy_candidates": ranked,
        "simulated": simulated,
        "validation": {
            "correlation": correlation,
            "simulated_best_in_proxy_top_k": best_in_topk if best else None,
            "simulated_best_begin_s": best["window"]["begin_s"] if best else None,
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_path = args.out or rs.SUMO_DIR / f"suggest_closure_{'+'.join(args.edge)[:60]}.json"
    rs.atomic_write_json(out_path, result, indent=2)
    print(f"Wrote {out_path}")

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
        print(f"  cleaned up {removed} intermediate file(s)")


if __name__ == "__main__":
    main()
