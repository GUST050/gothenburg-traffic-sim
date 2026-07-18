#!/usr/bin/env python3
"""Production-scale save/load equivalence proof on the frozen Gothenburg run.

This validation is isolated in a temporary directory and never changes the
active demand, scenario set, release pointer, or persistent caches.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_scenario import run_sumo
from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.simulation.warm_state_cache import (
    certify_warm_state_equivalence,
    create_baseline_evidence_identity,
    create_warm_state_identity,
    restore_baseline_evidence,
    restore_warm_state,
    store_baseline_evidence,
    store_warm_state,
)


FROZEN = ROOT / "runs/releases/golden-2025-09-16-7day-v1/multi_day"
WARMUP_END_S = 3600
ANALYSIS_END_S = 7200


def _required(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(path)
    return path


def _edge_additional(path: Path, output: Path) -> None:
    path.write_text(
        "<additional>\n"
        f'  <edgeData id="decision" file="{output.name}" period="900" '
        f'begin="{WARMUP_END_S}" end="{ANALYSIS_END_S}" '
        'excludeEmpty="false"/>\n'
        "</additional>\n",
        encoding="utf-8",
    )


def _metrics(path: Path) -> dict:
    intervals = []
    totals = {"entered": 0, "left": 0, "time_loss_s": 0.0}
    edge_ids: set[str] = set()
    for interval in ET.parse(path).getroot().iter("interval"):
        rows = []
        for edge in interval.findall("edge"):
            row = {
                "id": edge.get("id"),
                "entered": int(float(edge.get("entered", "0"))),
                "left": int(float(edge.get("left", "0"))),
                "time_loss_s": float(edge.get("timeLoss", "0")),
                "travel_time_s": float(edge.get("traveltime", "0")),
            }
            rows.append(row)
            edge_ids.add(row["id"])
            totals["entered"] += row["entered"]
            totals["left"] += row["left"]
            totals["time_loss_s"] += row["time_loss_s"]
        intervals.append({
            "begin_s": int(float(interval.get("begin"))),
            "end_s": int(float(interval.get("end"))),
            "edges": rows,
        })
    if not intervals or not edge_ids:
        raise RuntimeError("production edgeData contained no decision evidence")
    return {
        "intervals": intervals,
        "totals": totals,
        "edge_ids": sorted(edge_ids),
    }


def _difference_summary(left: dict, right: dict) -> dict:
    fields = ("entered", "left", "time_loss_s", "travel_time_s")
    summary = {
        field: {
            "changed_values": 0,
            "maximum_absolute_delta": 0.0,
            "sum_signed_delta": 0.0,
        }
        for field in fields
    }
    if len(left["intervals"]) != len(right["intervals"]):
        raise RuntimeError("equivalence outputs have different interval counts")
    for left_interval, right_interval in zip(
        left["intervals"], right["intervals"]
    ):
        if (
            left_interval["begin_s"] != right_interval["begin_s"]
            or left_interval["end_s"] != right_interval["end_s"]
            or len(left_interval["edges"]) != len(right_interval["edges"])
        ):
            raise RuntimeError("equivalence interval structure differs")
        for left_edge, right_edge in zip(
            left_interval["edges"], right_interval["edges"]
        ):
            if left_edge["id"] != right_edge["id"]:
                raise RuntimeError("equivalence edge ordering differs")
            for field in fields:
                delta = right_edge[field] - left_edge[field]
                record = summary[field]
                if delta:
                    record["changed_values"] += 1
                record["maximum_absolute_delta"] = max(
                    record["maximum_absolute_delta"], abs(delta))
                record["sum_signed_delta"] += delta
    for record in summary.values():
        record["maximum_absolute_delta"] = round(
            record["maximum_absolute_delta"], 12)
        record["sum_signed_delta"] = round(record["sum_signed_delta"], 12)
    return summary


def _run_branch(
    directory: Path,
    *,
    network: Path,
    route: Path,
    state: Path | None,
) -> tuple[dict, float]:
    directory.mkdir()
    edge_output = directory / "edge.xml"
    edge_additional = directory / "edge.add.xml"
    _edge_additional(edge_additional, edge_output)
    started = time.monotonic()
    run_sumo(
        1000,
        route,
        [edge_additional],
        ANALYSIS_END_S,
        sumo_home(),
        micro=False,
        begin_s=WARMUP_END_S if state is not None else 0,
        net_path=network,
        flush_s=3600,
        load_state_path=state,
        work_dir=directory,
    )
    return _metrics(edge_output), time.monotonic() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release-dir", type=Path, default=FROZEN)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    release = args.release_dir.resolve()
    network = _required(release / "net.net.xml")
    route = _required(release / "calibrated.rou.xml")
    meta = json.loads(_required(release / "demand_meta.json").read_text())
    demand_build_id = str(meta.get("build_id") or meta.get("demand_build_key"))
    if not demand_build_id or demand_build_id == "None":
        raise ValueError("frozen demand metadata lacks a build identity")

    with tempfile.TemporaryDirectory(
        prefix="gothenburg-warm-state-proof-"
    ) as raw:
        work = Path(raw)
        uninterrupted, uninterrupted_s = _run_branch(
            work / "uninterrupted",
            network=network,
            route=route,
            state=None,
        )

        warm_dir = work / "warm"
        warm_dir.mkdir()
        state = warm_dir / "state.xml.gz"
        started = time.monotonic()
        run_sumo(
            1000,
            route,
            [],
            WARMUP_END_S,
            sumo_home(),
            micro=False,
            begin_s=0,
            net_path=network,
            flush_s=1,
            save_state_path=state,
            save_state_time_s=WARMUP_END_S,
            work_dir=warm_dir,
        )
        warm_s = time.monotonic() - started
        state_bytes = _required(state).stat().st_size

        branched, branched_s = _run_branch(
            work / "branched",
            network=network,
            route=route,
            state=state,
        )
        identity = create_warm_state_identity(
            demand_build_id=demand_build_id,
            demand_variant="q50",
            seed=1000,
            simulation_mode="meso",
            warmup_end_s=WARMUP_END_S,
            network_path=network,
            route_path=route,
            source_files={
                "equivalence_validator": Path(__file__),
            },
            sumo_home=sumo_home(),
        )
        certificate = certify_warm_state_equivalence(
            identity, uninterrupted, branched)
        if certificate.status != "pass":
            raise RuntimeError(
                f"fresh save/load mismatch: {certificate.mismatch}")

        cache = work / "warm-cache"
        store_warm_state(cache, identity, state, certificate)
        restored_state = work / "restored" / "state.xml.gz"
        lookup = restore_warm_state(cache, identity, restored_state)
        if not lookup.hit:
            raise RuntimeError(f"warm-state restore failed: {lookup.reason}")
        cached, cached_s = _run_branch(
            work / "cached",
            network=network,
            route=route,
            state=restored_state,
        )
        cached_certificate = certify_warm_state_equivalence(
            identity, uninterrupted, cached)
        if cached_certificate.status != "pass":
            raise RuntimeError(
                f"cached save/load mismatch: {cached_certificate.mismatch}")

        baseline_identity = create_baseline_evidence_identity(
            identity,
            analysis_start_s=WARMUP_END_S,
            analysis_end_s=ANALYSIS_END_S,
            affected_edges=tuple(uninterrupted["edge_ids"]),
            objective_profile="robust_time_loss",
        )
        baseline_root = work / "baseline-cache"
        store_baseline_evidence(
            baseline_root, baseline_identity, uninterrupted, certificate)
        baseline = restore_baseline_evidence(
            baseline_root, baseline_identity)
        if not baseline.hit or baseline.metrics != uninterrupted:
            raise RuntimeError(
                f"baseline evidence restore failed: {baseline.reason}")

        report = {
            "schema_version": 1,
            "status": "pass",
            "purpose": "production_scale_warm_state_equivalence",
            "release": release.name,
            "demand_build_id": demand_build_id,
            "warm_state_key": identity.content_key,
            "baseline_evidence_key": baseline_identity.content_key,
            "sumo_version": identity.sumo_version.splitlines()[0],
            "platform": identity.platform_id,
            "warmup_end_s": WARMUP_END_S,
            "analysis_end_s": ANALYSIS_END_S,
            "state_bytes": state_bytes,
            "wall_s": {
                "uninterrupted": round(uninterrupted_s, 3),
                "warm_state_build": round(warm_s, 3),
                "fresh_branch": round(branched_s, 3),
                "cached_branch": round(cached_s, 3),
            },
            "decision_metrics": uninterrupted["totals"],
            "fresh_difference": _difference_summary(
                uninterrupted, branched),
            "cached_difference": _difference_summary(
                uninterrupted, cached),
            "intervals": len(uninterrupted["intervals"]),
            "edges": len(uninterrupted["edge_ids"]),
            "fresh_equivalence": certificate.to_dict(),
            "cached_equivalence": cached_certificate.to_dict(),
            "cache_restore": lookup.reason,
            "baseline_restore": baseline.reason,
        }
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
