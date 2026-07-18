"""
suggest_closure_time.py — "when is it least disruptive to close road X?"

IMPROVEMENT_PLAN.md Phase C4. Offline/batch two-stage search over the CURRENTLY
CALIBRATED demand period (whatever build_sumo_demand.py + run_scenario.py's
sumo/demand_meta.json already covers — a day or a week; this tool does not
build new demand). Standalone: does not touch web/data/scenarios or serve.py.

Method:
  1. PROXY stage (seconds, no simulation): every hourly-aligned candidate
     window of the requested duration is scored from ONE baseline SUMO run's
     edgeData — closed-edge flow and nearby-corridor flow during the window,
     both LOWER is better. The proxy is a RANKING ONLY (Borda-combined rank
     positions, no fabricated "predicted delay" number) — see IMPROVEMENT_PLAN.md C4.
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
from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses
import json
import math
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from scipy import stats as scipy_stats

from traffic_sim.simulation import metrics as cm
import run_scenario as rs
from traffic_sim.core.contracts import load_scenario_spec

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
    # `null` means MISSING, never a known zero (CLAUDE.md's contract, applies
    # everywhere flows are read — a real bug review 2026-07-11 caught this
    # function silently coercing null to 0.0, which would score an unknown
    # edge as ideally low-traffic and could select it into the proxy top-k).
    # Coerced to NaN instead; proxy_scores() below excludes windows whose
    # closed edge has no real data rather than ranking them as "best".
    return {e: np.array([np.nan if v is None else float(v) for v in arr])
            for e, arr in baseline["flows"].items()}


# ── Window generation ────────────────────────────────────────────────────

def generate_windows(duration_s: int, total_duration_s: int,
                     slide_s: int) -> list[tuple[int, int]]:
    """Every window of length duration_s, sliding by slide_s, fully inside
    [0, total_duration_s]. E.g. duration_s=6h, total=7d, slide=1h gives 163
    windows — the exact number IMPROVEMENT_PLAN.md's C4 spec quotes as its example."""
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


def aligned_quarters(hours: float, parameter: str) -> int:
    """Convert a positive hour duration to exact 15-minute buckets.

    Proxy flow is available only in complete 15-minute intervals. Rounding a
    request such as 1.1h to seconds and treating each partly-overlapped bucket
    equally produces an unlabelled, biased duration. Reject it at the shared
    CLI boundary instead.
    """
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError(f"{parameter} must be a finite value > 0")
    quarters = hours * 4
    rounded = round(quarters)
    if not math.isclose(quarters, rounded, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{parameter} must be an exact multiple of 0.25 hours")
    if rounded < 1:
        raise ValueError(f"{parameter} must be at least one quarter (0.25h)")
    return int(rounded)


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
                 n_intervals: int) -> tuple[list[dict], int]:
    """Per window: mean flow on the closed edge(s) and mean flow on the
    nearby (non-closed) corridor, both from the baseline SUMO run's edgeData.
    LOWER is better for both — no combined number with invented units is
    produced here; ranking happens in rank_candidates().

    A window whose closed edge(s) have NO real (non-null) data anywhere in
    the window is EXCLUDED from the candidate list entirely, not scored as
    "0 flow = best" — found in review 2026-07-11: an earlier version
    coerced missing flow to 0.0, which would rank an edge nobody has real
    data for as the ideal time to close it, exactly backwards from what
    missing data should mean. Corridor coverage is a weaker, supporting
    signal — a window with some missing corridor edges still gets scored
    from whichever corridor edges DO have data (nanmean), and only drops
    the corridor signal entirely (falls back to closed-edge-only ranking
    for that one window) if literally none of the corridor edges have any
    data in the window.

    Returns (scored_windows, n_excluded_for_missing_data)."""
    corridor = [e for e in corridor_edges if e not in close_edges]
    missing_default = np.full(n_intervals, np.nan)
    out = []
    excluded = 0
    for begin_s, end_s in windows:
        qs = list(window_quarters(begin_s, end_s, n_intervals))
        if not qs:
            excluded += 1
            continue
        closed_vals = [baseline_flows.get(e, missing_default)[qs] for e in close_edges]
        closed_concat = np.concatenate(closed_vals) if closed_vals else np.array([])
        if closed_concat.size == 0 or np.all(np.isnan(closed_concat)):
            excluded += 1   # no real data for the edge(s) being closed — not scoreable
            continue
        closed_flow = float(np.nanmean(closed_concat))
        corridor_flow = None
        if corridor:
            corridor_vals = [baseline_flows.get(e, missing_default)[qs] for e in corridor]
            corridor_concat = np.concatenate(corridor_vals)
            if not np.all(np.isnan(corridor_concat)):
                corridor_flow = float(np.nanmean(corridor_concat))
        out.append({
            "begin_s": begin_s, "end_s": end_s,
            "closed_edge_flow": closed_flow,
            "corridor_flow": corridor_flow,
        })
    return out, excluded


def rank_candidates(scored: list[dict]) -> list[dict]:
    """Borda-style combined rank: average of the two ascending rank
    positions (closed-edge flow, corridor flow), lower average = better.
    Deliberately NOT a weighted sum of flows — that would look like a
    physical quantity (IMPROVEMENT_PLAN.md: 'never show it as predicted delay
    minutes'); a rank position carries no such implication.

    Uses scipy's 'average' tie-handling (fractional ranks for equal
    values), NOT a plain stable sort: with a stable sort, two windows tied
    on corridor_flow get arbitrarily different integer ranks (whichever
    came first in window order), which then leaks a spurious index-order
    bias into the combined rank even when corridor_flow was actually
    UNINFORMATIVE for the comparison. Caught by
    TestProxyScoresAndRanking.test_lower_flow_window_scores_better, which
    used a corridor flow constant across windows specifically to isolate
    this.

    corridor_flow can now be missing on a PER-WINDOW basis (found in review
    2026-07-11: some corridor edges near the closure can have real null
    coverage in some windows even when others don't) — a window without any
    corridor signal falls back to closed-edge-only ranking for itself
    specifically, rather than assuming corridor availability is an
    all-or-nothing property of the whole run."""
    n = len(scored)
    if n == 0:
        return []
    closed_vals = np.array([s["closed_edge_flow"] for s in scored])
    rank_closed = scipy_stats.rankdata(closed_vals, method="average") - 1

    has_corridor_idx = [i for i, s in enumerate(scored) if s["corridor_flow"] is not None]
    rank_corridor = np.full(n, np.nan)
    if has_corridor_idx:
        corridor_vals = np.array([scored[i]["corridor_flow"] for i in has_corridor_idx])
        sub_ranks = scipy_stats.rankdata(corridor_vals, method="average") - 1
        # Rescale the subset's ranks onto the SAME [0, n-1] range rank_closed
        # uses, so a window missing corridor data doesn't skew the mixed
        # average just because fewer windows were available to rank against.
        if len(has_corridor_idx) > 1:
            sub_ranks = sub_ranks / (len(has_corridor_idx) - 1) * (n - 1)
        else:
            sub_ranks = np.array([(n - 1) / 2])   # one point -> neutral middle
        for pos, i in enumerate(has_corridor_idx):
            rank_corridor[i] = sub_ranks[pos]

    combined = np.where(np.isnan(rank_corridor), rank_closed,
                        (rank_closed + rank_corridor) / 2)
    out = [
        {**s, "rank_closed_edge": float(rank_closed[i]),
         "rank_corridor": None if np.isnan(rank_corridor[i]) else float(rank_corridor[i]),
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
                     scratch: list[Path],
                     rerouter_edges: list[str] | None = None,
                     work_dir: Path | None = None,
                     seed_workers: int = 1,
                     seed_start: int = 1000,
                     variant_labels: Sequence[str] | None = None,
                     replication_records: list[dict[str, Any]] | None = None,
                     ) -> tuple[cm.DisruptionMetrics, int, int, list[float]]:
    """Run `seeds` Monte Carlo replications of one candidate (or the
    baseline, when close_edges is empty) and aggregate their disruption
    metrics. Mirrors run_scenario.main()'s truncate-once-per-variant,
    reuse-across-seeds pattern exactly, but WITHOUT writing anything into
    web/data/scenarios — this tool only ever produces a results JSON
    (IMPROVEMENT_PLAN.md C4: 'reproducible without the web'); promoting a chosen window
    into a real scenario is a separate, later run_scenario.py invocation.

    Every intermediate file this call writes into sumo/ is appended to
    `scratch` so the caller can delete them all at the end — searching a
    handful of windows otherwise leaves dozens of route/edgeData/tripinfo
    files behind per run with no natural expiry (sumo/ is gitignored, but
    unbounded either way).

    Returns the RAW per-seed total_time_loss_s values alongside the
    aggregated metrics — C5's UI wants to show a median + seed interval
    (IMPROVEMENT_PLAN.md's own words), which the mean alone throws away."""
    run_variants = variants
    # Per-VARIANT truncated/dropped counts — truncation is deterministic
    # given (variant, closure), independent of a seed's random number, so
    # it only needs computing once per variant. Keyed by run_variants'
    # index so each seed (which picks run_variants[s % len(run_variants)])
    # can look up the count for the SPECIFIC variant it actually ran,
    # instead of a global sum across every variant. Found in review
    # 2026-07-11: the earlier version summed truncated/dropped across ALL
    # variants and reported that combined total identically for every
    # seed, even though each seed only ever ran ONE of them — a seed
    # running the untruncated-by-much q50 variant was reported as if it
    # also carried q10/q90's truncation, overstating affected vehicles by
    # roughly the variant count and making it impossible to tell whether
    # the demand realization a seed actually used was itself truncated.
    per_variant_trunc: list[tuple[int, int]] = [(0, 0)] * len(variants)
    closure_add: list[Path] = []
    if seed_workers < 1:
        raise ValueError("seed_workers must be >= 1")
    if (
        isinstance(seed_start, bool)
        or not isinstance(seed_start, int)
        or seed_start < 0
    ):
        raise ValueError("seed_start must be a non-negative integer")
    if not variants:
        raise ValueError("simulate_closure requires at least one demand variant")
    if variant_labels is not None:
        labels = tuple(str(label) for label in variant_labels)
        if (
            len(labels) != len(variants)
            or len(set(labels)) != len(labels)
            or any(not label for label in labels)
        ):
            raise ValueError(
                "variant_labels must uniquely identify every demand variant"
            )
    elif replication_records is not None:
        raise ValueError(
            "replication_records require explicit variant_labels"
        )
    else:
        labels = tuple(f"variant_{index}" for index in range(len(variants)))
    base_dir = Path(work_dir) if work_dir is not None else rs.SUMO_DIR
    if work_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=False)
    if close_edges:
        assert closures is not None and adj is not None and freeflow is not None
        cpath = base_dir / f"{SCT_PREFIX}closure_{name}.add.xml"
        selected_rerouter_edges = (rerouter_edges if rerouter_edges is not None
                                   else rs.edges_near(close_edges,
                                                      rs.REROUTER_RADIUS_M))
        rs.write_closure_additional(cpath, closures, selected_rerouter_edges)
        closure_add = [cpath]
        scratch.append(cpath)
        filtered = []
        for i, vp in enumerate(variants):
            fp = base_dir / f"{vp.stem}_{SCT_PREFIX}{name}.rou.xml"
            t, d = rs.truncate_stranded_vehicles(
                vp, close_edges, fp, adj, closures=closures,
                edge_travel_s=freeflow)
            per_variant_trunc[i] = (t, d)
            filtered.append(fp)
            scratch.append(fp)
        run_variants = filtered

    jobs = []
    for s in range(seeds):
        seed = seed_start + s
        variant_idx = s % len(run_variants)
        route_path = run_variants[variant_idx]
        seed_truncated, seed_dropped = per_variant_trunc[variant_idx]
        seed_dir = base_dir / f"seed-{seed}" if work_dir is not None else base_dir
        if work_dir is not None:
            seed_dir.mkdir(parents=True, exist_ok=False)
        ed_file = seed_dir / f"{SCT_PREFIX}ed_{name}_{seed}.xml"
        add_path = seed_dir / f"{SCT_PREFIX}add_{name}_{seed}.add.xml"
        rs.write_edgedata_additional(add_path, ed_file, duration_s)
        scratch += [ed_file, add_path]
        jobs.append({"seed": seed, "route_path": route_path,
                     "variant_idx": variant_idx, "seed_dir": seed_dir,
                     "demand_variant": labels[variant_idx],
                     "ed_file": ed_file, "add_path": add_path,
                     "seed_truncated": seed_truncated,
                     "seed_dropped": seed_dropped})

    def run_one(job: dict) -> tuple[int, int, str, cm.DisruptionMetrics, list[Path]]:
        metric_paths = rs.run_sumo(
            job["seed"], job["route_path"], [job["add_path"]] + closure_add,
            duration_s, home, micro=micro, metrics=True,
            **({"work_dir": job["seed_dir"]} if work_dir is not None else {}))
        active_throughput = None
        if closures and job["ed_file"].exists():
            seed_flows = rs.parse_edgedata(job["ed_file"], n_intervals)
            active_throughput = cm.active_closure_throughput(seed_flows, closures)
        metrics = cm.build_metrics(
            metric_paths["tripinfo"], metric_paths["statistics"],
            truncated_unreachable=job["seed_truncated"],
            dropped_unreachable=job["seed_dropped"],
            summary_path=metric_paths["summary"],
            closed_edge_throughput=active_throughput)
        return (
            job["seed"],
            job["variant_idx"],
            job["demand_variant"],
            metrics,
            list(metric_paths.values()),
        )

    if seed_workers == 1 or len(jobs) == 1:
        completed = [run_one(job) for job in jobs]
    else:
        executor = ThreadPoolExecutor(max_workers=min(seed_workers, len(jobs)),
                                      thread_name_prefix="closure-seed")
        futures = [executor.submit(run_one, job) for job in jobs]
        try:
            completed = [future.result() for future in as_completed(futures)]
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
    completed.sort(key=lambda item: item[0])
    per_seed_metrics = [item[3] for item in completed]
    for item in completed:
        scratch.extend(item[4])
    if replication_records is not None:
        replication_records.extend(
            {
                "seed": seed,
                "variant_index": variant_idx,
                "demand_variant": demand_variant,
                "total_time_loss_s": metrics.total_time_loss_s,
                "truncated_unreachable": metrics.truncated_unreachable,
                "dropped_unreachable": metrics.dropped_unreachable,
                "teleport_total": metrics.teleport_total,
                "unfinished_trips": metrics.unfinished_trips,
                "unfinished_waiting_trips": metrics.unfinished_waiting_trips,
                "running_at_end": metrics.running_at_end,
                "waiting_at_end": metrics.waiting_at_end,
            }
            for seed, variant_idx, demand_variant, metrics, _ in completed
        )
    per_seed_time_loss = [m.total_time_loss_s for m in per_seed_metrics]
    # Candidate-level totals: sum over the DISTINCT variants actually used
    # (not over seeds — repeat seeds on the same variant don't add new
    # truncation, since it's deterministic per variant) so a caller asking
    # "how many vehicles were affected simulating this candidate" gets the
    # real total across the demand realizations that were actually run,
    # not an undercount (old first-seed-only) or overcount (old global sum
    # applied per seed).
    used_variant_idxs = {s % len(run_variants) for s in range(seeds)}
    n_truncated = sum(per_variant_trunc[i][0] for i in used_variant_idxs)
    n_dropped = sum(per_variant_trunc[i][1] for i in used_variant_idxs)
    return (aggregate_seed_metrics(per_seed_metrics), n_truncated, n_dropped,
           per_seed_time_loss)


def recommendation_status(correlation: dict | None) -> str:
    """A structural (not just prose) status field so a caller can gate on
    it directly instead of parsing correlation['interpretation'] text.
    NEVER returns "validated" — even a strong correlation here is from a
    small, non-random, selection-biased sample (proxy top-k + one low-
    traffic control + worst-case controls, not a stratified/held-out
    design), which is real screening evidence, not a statistical
    validation. Added 2026-07-11 per external review section 4's critique
    that this project's own honesty rule ("never claim more than
    measured") wasn't yet enforced as a checkable field here the way
    tls_provenance/recommendation_allowed are for D1-D3."""
    if correlation is None:
        return "insufficient_evidence"
    return ("screening_only_correlated" if correlation["spearman_rho"] > 0.3
           else "screening_only_weak_correlation")


def closure_feasibility(candidate: cm.DisruptionMetrics,
                        baseline: cm.DisruptionMetrics,
                        *, detour: dict | None = None) -> dict:
    """Build the hard/diagnostic gates used by closure-time ranking.

    A low delay is not a valid recommendation if it came from losing access,
    truncating trips, or an unhealthy SUMO run.  Queue is deliberately a
    *measured diagnostic* rather than an invented threshold: when either
    summary lacks the queue proxy, the candidate cannot support a trusted
    recommendation and is marked ``queue_unmeasured``.
    """
    hard_failures = list(cm.disqualification_reasons(candidate))
    if candidate.truncated_unreachable:
        hard_failures.append("truncated_unreachable_vehicles")
    if candidate.loaded > 0:
        unfinished = candidate.unfinished_trips / candidate.loaded
        if unfinished > rs.HEALTH_UNFINISHED_MAX_SHARE:
            hard_failures.append("unfinished_vehicle_share")
    detour_status = "not_supplied"
    if detour is not None:
        score = detour.get("score")
        if score is None or score <= 0:
            # The topology screen crosses every neighbouring predecessor and
            # successor. It does not know which pairs any seeded trip uses,
            # so it is useful evidence but cannot disqualify a scenario by
            # itself. Actual affected-demand loss is already a hard gate via
            # truncated_unreachable/dropped_unreachable metrics above.
            detour_status = "topology_no_confirmed_detour"
        elif score < 1.0:
            detour_status = "topology_partial_detour"
        else:
            detour_status = "all_predecessor_successor_pairs_reachable"

    queue_available = (candidate.max_queue_vehicles is not None and
                       baseline.max_queue_vehicles is not None)
    queue = {
        "status": "measured" if queue_available else "unmeasured",
        "candidate_max": candidate.max_queue_vehicles,
        "baseline_max": baseline.max_queue_vehicles,
        "delta": (candidate.max_queue_vehicles - baseline.max_queue_vehicles
                   if queue_available else None),
    }
    if not queue_available:
        hard_failures.append("queue_proxy_unmeasured")
    return {
        "eligible": not hard_failures,
        "hard_failures": sorted(set(hard_failures)),
        "detour": detour_status,
        "queue": queue,
        "paired_delta_time_loss": None,
    }


def delta_time_loss_interval(candidate_per_seed: list[float],
                             baseline_per_seed: list[float]) -> dict:
    """Median + [min, max] of paired per-seed Δ time loss.

    Baseline and candidate simulations use the same deterministic seed-to-
    variant mapping, so comparing each candidate seed with the corresponding
    baseline seed removes Monte Carlo noise instead of treating the baseline
    mean as if it were observed for every candidate.
    """
    if not candidate_per_seed or len(candidate_per_seed) != len(baseline_per_seed):
        raise ValueError(
            "paired candidate and baseline seed results must have equal non-zero length")
    deltas = sorted(c - b for c, b in zip(candidate_per_seed, baseline_per_seed))
    n = len(deltas)
    median = deltas[n // 2] if n % 2 else (deltas[n // 2 - 1] + deltas[n // 2]) / 2
    return {"median_s": median, "min_s": deltas[0], "max_s": deltas[-1], "n_seeds": n}


def aggregate_seed_metrics(per_seed: list[cm.DisruptionMetrics]) -> cm.DisruptionMetrics:
    """Mean across seeds for volume-like fields (each seed is a full,
    independent replication of the same demand — summing would just count
    the same vehicles `seeds` times); SUM for teleports (a teleport in ANY
    seed is a real integrity problem, not something to average away).

    truncated_unreachable/dropped_unreachable now vary per seed (fixed in
    review 2026-07-11 — each seed correctly carries only ITS OWN variant's
    count, see simulate_closure). Uses MAX, not mean, for the same reason
    teleports use SUM: a seed with dropped_unreachable=0 averaging against
    a seed with dropped_unreachable=1 must not round down to 0 and hide a
    real dropped vehicle from is_disqualified()'s check. MAX (not SUM)
    because a variant sampled by more than one seed (seeds > len(variants))
    would otherwise have its fixed, deterministic count added again for
    every repeat — not a real second occurrence."""
    n = len(per_seed)
    mean = lambda f: sum(getattr(m, f) for m in per_seed) / n
    teleport_reasons: dict[str, int] = {}
    for m in per_seed:
        for k, v in m.teleport_reasons.items():
            teleport_reasons[k] = teleport_reasons.get(k, 0) + v
    queues = [m.max_queue_vehicles for m in per_seed if m.max_queue_vehicles is not None]
    throughputs = [m.closed_edge_throughput for m in per_seed
                   if m.closed_edge_throughput is not None]
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
        truncated_unreachable=max(m.truncated_unreachable for m in per_seed),
        dropped_unreachable=max(m.dropped_unreachable for m in per_seed),
        max_queue_vehicles=max(queues) if queues else None,
        closed_edge_throughput=sum(throughputs) if throughputs else None,
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
    p.add_argument("--exhaustive", action="store_true",
                   help="Simulate every feasible window. This is the only mode that "
                        "can support a global best-window claim; it may be much slower.")
    p.add_argument("--extra-bad", type=int, default=2,
                   help="How many proxy-worst windows to simulate as negative "
                        "controls (default 2).")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo seeds per simulated candidate (default 3).")
    p.add_argument("--seed-workers", type=int, default=1,
                   help="Concurrent SUMO seeds per candidate (default 1; benchmark first).")
    p.add_argument("--micro", action="store_true",
                   help="Use microscopic simulation (default: mesoscopic).")
    p.add_argument("--scenario-spec", default=None, metavar="PATH",
                   help="Load a validated base ScenarioSpec. Its demand/network, "
                        "seed set and simulation mode become authoritative for "
                        "every candidate; the search adds candidate closures.")
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
    if args.seed_workers < 1:
        sys.exit("--seed-workers must be >= 1")
    home = rs.sumo_home()
    rs.SUMO_DIR.mkdir(parents=True, exist_ok=True)

    with open(rs.SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    total_duration_s = n_intervals * 900

    base_spec = None
    if args.scenario_spec:
        try:
            base_spec = load_scenario_spec(Path(args.scenario_spec))
            rs.validate_scenario_spec(
                base_spec, meta=meta, duration_s=total_duration_s,
                network_path=rs.NET_PATH)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"invalid base ScenarioSpec: {exc}")
        if base_spec.closures:
            sys.exit("closure-time search ScenarioSpec must be a base study without closures")
        args.seeds = len(base_spec.seed_set)
        args.micro = base_spec.simulation_mode == "micro"

    prior, names = rs.load_geojson_meta()
    for e in args.edge:
        if e not in prior:
            sys.exit(f"--edge {e}: not an edge in network.geojson")

    try:
        duration_s = aligned_quarters(args.duration_hours, "--duration-hours") * 900
        slide_s = aligned_quarters(args.slide_hours, "--slide-hours") * 900
    except ValueError as exc:
        sys.exit(str(exc))
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

    variants = rs.demand_variants(meta)
    adj = rs.build_edge_graph(set(args.edge))
    freeflow = rs.edge_freeflow_times()
    rerouter_edges = rs.edges_near(args.edge, rs.REROUTER_RADIUS_M)

    # ── Proxy stage: genuinely "seconds, no simulation" — reuses the
    # already-built baseline scenario's flows. ──────────────────────────
    demand_sig = rs.demand_signature(meta)
    baseline_flows = load_baseline_flows(demand_sig, n_intervals)
    scored, n_excluded = proxy_scores(windows, args.edge, rerouter_edges,
                                      baseline_flows, n_intervals)
    if n_excluded:
        print(f"  {n_excluded}/{len(windows)} candidate windows excluded — "
             "no real (non-null) flow data for the closed edge(s) in that window")
    if not scored:
        sys.exit("every candidate window was excluded for missing data — "
                 "cannot suggest a closure time")
    ranked = rank_candidates(scored)

    # ── ONE real baseline metrics run, shared by every simulated
    # candidate's Δ comparison (unavoidable — Δ timeLoss needs a real
    # simulated baseline; the scenario JSON only has flows, not timeLoss). ─
    scratch: list[Path] = []
    batch_workspace = Path(tempfile.mkdtemp(prefix=".suggest_closure_",
                                             dir=str(rs.SUMO_DIR)))
    completed_ok = False
    try:
        print("  running baseline (metrics) …")
        t0 = time.time()
        baseline_metrics, _, _, baseline_per_seed = simulate_closure(
            name="baseline", closures=None, close_edges=[], variants=variants,
            seeds=args.seeds, n_intervals=n_intervals, duration_s=total_duration_s,
            home=home, micro=args.micro, adj=None, freeflow=None, scratch=scratch,
            rerouter_edges=rerouter_edges,
            work_dir=batch_workspace / "baseline",
            seed_workers=args.seed_workers)
        print(f"  baseline done ({time.time() - t0:.0f}s): "
             f"timeLoss={baseline_metrics.total_time_loss_s:.0f}s, "
             f"{baseline_metrics.trip_count} trips")

        # ── Simulate stage ───────────────────────────────────────────────
        to_simulate = (ranked if args.exhaustive else
                       select_candidates(ranked, args.top_k, args.extra_bad))
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
                adj=adj, freeflow=freeflow, scratch=scratch,
                rerouter_edges=rerouter_edges,
                work_dir=batch_workspace / name,
                seed_workers=args.seed_workers)
            comparison = cm.compare_metrics(baseline_metrics, metrics)
            interval = delta_time_loss_interval(candidate_per_seed, baseline_per_seed)
            feasibility = closure_feasibility(
                metrics, baseline_metrics, detour=diagnostic)
            feasibility["paired_delta_time_loss"] = interval
            elapsed = time.time() - t0
            print(f"    [{i+1}/{len(to_simulate)}] proxy_rank={w['proxy_rank']} "
                 f"begin={w['begin_s']}s  ΔtimeLoss median={interval['median_s']:+.0f}s "
                 f"[{interval['min_s']:+.0f}, {interval['max_s']:+.0f}]"
                 f"{'  DISQUALIFIED: ' + ','.join(feasibility['hard_failures']) if not feasibility['eligible'] else ''}"
                 f"  ({elapsed:.0f}s)")
            simulated.append({
                "window": w, "metrics": dataclasses.asdict(metrics),
                "comparison": dataclasses.asdict(comparison),
                "delta_time_loss_interval": interval,
                "feasibility": feasibility,
                "truncated_vehicles": n_trunc, "dropped_vehicles": n_drop,
            })

        # ── Validate the proxy ───────────────────────────────────────────
        eligible = [s for s in simulated if s["feasibility"]["eligible"]]
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
            "method": ("IMPROVEMENT_PLAN.md Phase C4: exhaustive feasible windows, "
                       "Spearman-validated"
                       if args.exhaustive else
                       "IMPROVEMENT_PLAN.md Phase C4: proxy-ranked hourly windows, "
                       "top-k + controls simulated, Spearman-validated"),
            "edges": args.edge, "streets": streets,
            "duration_hours": args.duration_hours, "slide_hours": args.slide_hours,
            "total_duration_s": total_duration_s, "n_candidate_windows": len(windows),
            "n_windows_excluded_missing_data": n_excluded,
            "top_k": args.top_k, "extra_bad": args.extra_bad, "seeds": args.seeds,
            "evaluation_mode": "exhaustive" if args.exhaustive else "proxy_subset",
            "micro": args.micro,
            "demand_signature": rs.demand_signature(meta),
            "epoch_sim": meta["epoch_sim"],
            "base_scenario_spec": (base_spec.to_dict() if base_spec is not None else None),
            "detour_availability": diagnostic,
            "baseline_metrics": dataclasses.asdict(baseline_metrics),
            "baseline_per_seed_time_loss_s": baseline_per_seed,
            "proxy_candidates": ranked,
            "simulated": simulated,
            "validation": {
                "correlation": correlation,
                "simulated_best_in_proxy_top_k": best_in_topk if best else None,
                "simulated_best_begin_s": best["window"]["begin_s"] if best else None,
                "recommendation_status": recommendation_status(correlation),
                "eligible_candidates": len(eligible),
                "disqualified_candidates": len(simulated) - len(eligible),
            },
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        out_path = args.out or rs.SUMO_DIR / f"suggest_closure_{'+'.join(args.edge)[:60]}.json"
        rs.atomic_write_json(out_path, result, indent=2)
        print(f"Wrote {out_path}")
        completed_ok = True
    finally:
        # ALWAYS runs, even on a SUMO timeout (sys.exit inside run_sumo) or
        # any other exception mid-search — a search that dies partway
        # through used to leave every route/edgeData/tripinfo file written
        # so far behind with no cleanup at all, since the old cleanup block
        # only ran after a full successful result write. Found in external
        # review 2026-07-11 (NEW_CHANGES_REVIEW section 6.3).
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
            if completed_ok:
                shutil.rmtree(batch_workspace, ignore_errors=True)
        if not completed_ok or args.keep_scratch:
            print(f"  kept isolated workspace: {batch_workspace}")


if __name__ == "__main__":
    main()
