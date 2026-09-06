"""Measure Stage 3's gate: does disabling teleporting stop the closure leak?

Stage 3 of `docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md`. The gate, in the
plan's own words:

    closed-edge throughput must fall to 0 while `dropped_unreachable`/
    unfinished trips rise by no more than the number of vehicles Stage 1
    showed as genuinely detour-less. A vehicle that cannot get through must
    show up as *not arriving*, never as *driving through*.

This harness runs ONE closure TWICE against the same demand, the same seeds
and the same window — once under SUMO's default stuck-vehicle relocation and
once under `closure_teleport.CLOSURE_TIME_TO_TELEPORT_S` — and writes the
paired result to `validation/closure_teleport_policy_v1.json`.

WHY IT IS PAIRED AND NOT A SINGLE RUN. The measurement of interest is a
DIFFERENCE: throughput must fall, and the stuck population must not rise by
more than the vehicles that genuinely have nowhere to go. A single run under
the new policy would report zero teleports and zero throughput and prove
nothing at all, because both are zero by construction. The arm that still
teleports is what makes the zero mean something.

THE DETOUR-LESS BUDGET IS MEASURED, NOT ASSUMED. It comes from
`run_scenario.closure_disruption_across_variants`, which is demand-side and
congestion-independent, and specifically from `vehicles_no_detour` — the
vehicles for which no legal path exists once the edge is gone. That is exactly
the population the plan says is allowed to stop arriving.

This tool writes ONE validation artifact and nothing else. It publishes no
scenario, touches no release, and changes no active study.

Run:
    python3 tools/measure_closure_teleport_policy.py --edge <EDGE_ID> \
        --begin 25200 --end 54000 [--seeds 3] [--out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_scenario as rs                                     # noqa: E402
import suggest_closure_time as legacy                         # noqa: E402
from traffic_sim.simulation import closure_teleport as ct     # noqa: E402
from traffic_sim.core.fingerprint import sha256_file, sumo_version  # noqa: E402

DEFAULT_OUT = ROOT / "validation" / "closure_teleport_policy_v1.json"
ARM_LABELS = ("sumo_default", "policy")
SOURCE_PATHS = {
    "run_scenario.py": ROOT / "run_scenario.py",
    "suggest_closure_time.py": ROOT / "suggest_closure_time.py",
    "tools/measure_closure_teleport_policy.py": Path(__file__),
    "traffic_sim/simulation/closure_teleport.py":
        ROOT / "traffic_sim/simulation/closure_teleport.py",
    "traffic_sim/simulation/metrics.py":
        ROOT / "traffic_sim/simulation/metrics.py",
}


def _content_key(payload: dict) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def evidence_identity(home: Path, variants: list[Path]) -> dict:
    """Exact files and runtime that give the paired result its meaning."""
    inputs = {
        "sumo/demand_meta.json": rs.SUMO_DIR / "demand_meta.json",
        "sumo/net.net.xml": rs.NET_PATH,
        "sumo/plain.edg.xml": rs.SUMO_DIR / "plain.edg.xml",
        **{f"sumo/{path.name}": path for path in variants},
    }
    binary = home / "bin" / "sumo"
    records = {
        "inputs": {label: sha256_file(path)
                   for label, path in sorted(inputs.items())},
        # Absence is meaningful: run_scenario parses net.net.xml directly when
        # this optional cache is absent, and consumes it when present/valid.
        "optional_inputs": {
            "sumo/network_metadata.json": sha256_file(
                rs.SUMO_DIR / "network_metadata.json"),
        },
        "sources": {label: sha256_file(path)
                    for label, path in sorted(SOURCE_PATHS.items())},
        "sumo": {
            "version": sumo_version(home),
            "binary_sha256": sha256_file(binary),
        },
    }
    missing = [label for section in ("inputs", "sources")
               for label, digest in records[section].items()
               if digest is None]
    if records["sumo"]["binary_sha256"] is None:
        missing.append("SUMO binary")
    if missing:
        raise SystemExit(
            f"cannot bind Stage 3 evidence; missing inputs: {sorted(missing)}")
    return records


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edge", action="append", required=True,
                        metavar="EDGE_ID",
                        help="Directed edge to close. Repeat for a worksite.")
    parser.add_argument("--begin", type=int, required=True, metavar="SECONDS",
                        help="Closure start, simulation seconds from epoch.")
    parser.add_argument("--end", type=int, required=True, metavar="SECONDS",
                        help="Closure end, exclusive.")
    parser.add_argument("--seeds", type=int, default=3,
                        help="Monte Carlo replications per arm (default 3).")
    parser.add_argument("--seed-workers", type=int, default=1,
                        help="Concurrent SUMO processes within one arm.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    args = parser.parse_args(argv)
    if args.end <= args.begin:
        parser.error("--end must be greater than --begin")
    if args.seeds < 1:
        parser.error("--seeds must be >= 1")
    return args


def metrics_record(metrics) -> dict:
    """The fields the gate reads, plus the context needed to read them."""
    return {
        "total_time_loss_s": metrics.total_time_loss_s,
        "trip_count": metrics.trip_count,
        "unfinished_trips": metrics.unfinished_trips,
        "unfinished_waiting_trips": metrics.unfinished_waiting_trips,
        "teleport_total": metrics.teleport_total,
        "teleport_reasons": dict(metrics.teleport_reasons),
        "loaded": metrics.loaded,
        "inserted": metrics.inserted,
        "running_at_end": metrics.running_at_end,
        "waiting_at_end": metrics.waiting_at_end,
        "truncated_unreachable": metrics.truncated_unreachable,
        "dropped_unreachable": metrics.dropped_unreachable,
        "max_queue_vehicles": metrics.max_queue_vehicles,
        "closed_edge_throughput": metrics.closed_edge_throughput,
    }


def run_arm(*, label, time_to_teleport_s, closures, close_edges, variants,
            n_intervals, duration_s, home, adjacency, freeflow,
            rerouter_edges, seeds, seed_workers, workspace):
    """One arm: the same closure, differing only in the teleport option."""
    scratch: list[Path] = []
    replications: list[dict] = []
    started = time.time()
    # simulate_closure returns n_rerouted as a FIFTH value (commit
    # 3f20d70): a closure-avoiding detour must never be counted as lost
    # access. Unpacking four raised ValueError on every call, so this
    # arm could not run at all.
    metrics, truncated, dropped, per_seed, _rerouted = legacy.simulate_closure(
        name=f"teleport-{label}", closures=closures, close_edges=close_edges,
        variants=variants, seeds=seeds, n_intervals=n_intervals,
        duration_s=duration_s, home=home, micro=False, adj=adjacency,
        freeflow=freeflow, scratch=scratch, rerouter_edges=rerouter_edges,
        work_dir=workspace / f"arm-{label}", seed_workers=seed_workers,
        variant_labels=legacy.ROBUST_VARIANT_LABELS[:len(variants)],
        replication_records=replications,
        time_to_teleport_s=time_to_teleport_s)
    return {
        "arm": label,
        "teleport_policy": ct.policy_record(time_to_teleport_s),
        "metrics": metrics_record(metrics),
        "truncated_unreachable": truncated,
        "dropped_unreachable": dropped,
        "per_seed_time_loss_s": list(per_seed),
        "replications": replications,
        "wall_seconds": round(time.time() - started, 2),
    }, metrics


def main(argv=None) -> int:
    args = parse_args(argv)
    out_path = Path(args.out)
    if out_path.exists():
        raise SystemExit(
            f"refusing to overwrite {out_path}; remove it deliberately to "
            f"re-measure")

    home = rs.sumo_home()
    meta = json.loads((rs.SUMO_DIR / "demand_meta.json").read_text())
    n_intervals = int(meta["n_intervals"])
    duration_s = n_intervals * 900
    variants = rs.demand_variants(meta)
    identity_at_start = evidence_identity(home, variants)

    close_edges = list(dict.fromkeys(args.edge))
    closures = [{"edge_id": edge, "begin_s": args.begin, "end_s": args.end}
                for edge in close_edges]
    adjacency = rs.build_edge_graph(set(close_edges))
    freeflow = rs.edge_freeflow_times()
    rerouter_edges = rs.edges_near(close_edges, rs.REROUTER_RADIUS_M)

    # The budget the gate allows the stuck population to grow by: vehicles for
    # which no legal path exists once the edge is gone. Demand-side, so it is
    # the same number under either arm and cannot be tuned by the result.
    disruption = rs.closure_disruption_across_variants(
        variants, set(close_edges), closures, *rs.free_flow_edge_cost())
    if disruption is None:
        raise SystemExit(
            "no disruption record could be computed for this closure; without "
            "a measured detour-less count the gate has no budget and cannot "
            "be decided")
    detour_less = int(disruption["vehicles_no_detour"])

    workspace = Path(tempfile.mkdtemp(prefix=".teleport_policy_",
                                      dir=str(rs.SUMO_DIR)))
    arms: dict[str, dict] = {}
    measured = {}
    try:
        for label, policy in (("sumo_default", None),
                              ("policy", ct.CLOSURE_TIME_TO_TELEPORT_S)):
            print(f"  arm {label}: teleport policy {ct.policy_label(policy)} …",
                  flush=True)
            record, metrics = run_arm(
                label=label, time_to_teleport_s=policy, closures=closures,
                close_edges=close_edges, variants=variants,
                n_intervals=n_intervals, duration_s=duration_s, home=home,
                adjacency=adjacency, freeflow=freeflow,
                rerouter_edges=rerouter_edges, seeds=args.seeds,
                seed_workers=args.seed_workers, workspace=workspace)
            arms[label] = record
            measured[label] = metrics
            print(f"    teleports={metrics.teleport_total} "
                  f"throughput={metrics.closed_edge_throughput} "
                  f"unfinished={metrics.unfinished_trips} "
                  f"({record['wall_seconds']}s)", flush=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    gate = ct.evaluate_stage3_gate(
        before=arms["sumo_default"]["metrics"],
        after=arms["policy"]["metrics"],
        detour_less_vehicles=detour_less)

    payload = {
        "schema_version": 1,
        "kind": "closure_teleport_policy_measurement",
        "stage": "closure_integrity_plan_stage_3",
        "measured_at": time.strftime("%Y-%m-%d"),
        "closed_edges": close_edges,
        "closure_window_s": [args.begin, args.end],
        "seeds": args.seeds,
        "demand_build_key": meta.get("demand_build_key"),
        "build_id": meta.get("build_id"),
        "n_intervals": n_intervals,
        "demand_variants": [path.name for path in variants],
        "evidence_identity": identity_at_start,
        "detour_less_budget": {
            "vehicles_no_detour": detour_less,
            "vehicles_denied_departure":
                disruption.get("vehicles_denied_departure"),
            "vehicles_severed_destination":
                disruption.get("vehicles_severed_destination"),
            "basis": disruption["basis"],
        },
        "arms": [arms[label] for label in ARM_LABELS],
        "gate": gate,
        "platform": {"python": platform.python_version(),
                     "machine": platform.machine(),
                     "system": platform.system()},
        "limitation": (
            "one closure on one demand build. It decides the Stage 3 gate for "
            "that case, which is what the plan asks for, and is not evidence "
            "that every closure behaves the same way."),
    }
    identity_at_end = evidence_identity(home, variants)
    if identity_at_end != identity_at_start:
        raise SystemExit(
            "Stage 3 inputs changed during the paired run; refusing to write "
            "evidence for mixed identities")
    payload["content_key"] = _content_key(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {out_path}")
    print("gate passed:", gate["passed"])
    for check, value in sorted(gate["checks"].items()):
        print(f"  {check}: {value}")
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
