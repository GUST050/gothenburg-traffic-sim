#!/usr/bin/env python3
"""Run an evidence-bound multi-seed corrected LOSO sweep."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEDS = (20260807, 20260808, 20260809, 20260810, 20260811, 42)
EXPECTED_PROTOCOL = "loso_pfe_meso_v7"
RUN_ARTIFACTS = (
    "sumo/demand_meta.json",
    "sumo/candidates.meta.json",
    "sumo/assignment_priors.json",
    "sumo/prior_flows.json",
    "sumo/direction_split.json",
    "web/data/observability.json",
)
BOUND_INPUTS = (
    "tools/run_corrected_loso_sweep.py",
    "build_candidates.py",
    "build_sumo_demand.py",
    "assignment_priors.py",
    "traffic_sim/demand/pfe.py",
    "traffic_sim/confidence/loso.py",
    "tools/validate_dmrb.py",
    "sumo/net.net.xml",
    "web/data/flows.json",
    "data_in/sensors.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _run(command: list[str], log_path: Path) -> float:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n")
        log.flush()
        subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    return round(time.monotonic() - started, 3)


def _summarize(report: dict, tag: dict, seed: int, seconds: dict) -> dict:
    edge_rows = []
    for station, station_data in sorted(report["stations"].items()):
        for edge, entry in sorted(station_data["edges"].items()):
            component_edges = entry.get("component_edges", [edge])
            ceiling = station_data["held_edge_assignment_ceiling"]
            eligible_edges = ceiling["eligible_edges"]
            required_edges = ceiling.get("required_edges", component_edges)
            edge_rows.append({
                "station": station,
                "evaluation": edge,
                "measurement_semantics": entry.get(
                    "measurement_semantics", "legacy_directional"),
                "component_edges": component_edges,
                "assignment_ceiling_required_edges": required_edges,
                "ratio": entry["ratio"],
                "geh_ok_pct": entry["geh_ok_pct"],
                "assignment_ceiling": all(
                    required in eligible_edges for required in required_edges),
            })
    ratios = sorted(float(row["ratio"]) for row in edge_rows)
    criterion = tag["criteria"]["either_flow_difference_or_geh"]
    return {
        "seed": seed,
        "candidate_pool_sha256": report["comparison_contract"][
            "candidate_pool_sha256"],
        "protocol": report["comparison_contract"]["protocol"],
        "tag_satisfactory_cases": criterion["cases_meeting"],
        "tag_total_cases": criterion["cases_total"],
        "tag_satisfactory_share_pct": criterion["share_pct"],
        "tag_verdict": tag["verdict"],
        "daily_ratio_min": ratios[0],
        "daily_ratio_median": ratios[len(ratios) // 2],
        "daily_ratio_max": ratios[-1],
        "all_held_edges_received_assignment_ceiling": all(
            row["assignment_ceiling"] for row in edge_rows),
        "edges": edge_rows,
        "seconds": seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path, required=True,
        help="new evidence directory; an existing nonempty directory is refused",
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    args = parser.parse_args()
    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refusing nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    seeds = tuple(args.seeds)
    if len(seeds) != len(set(seeds)) or len(seeds) < 6:
        raise SystemExit("LOSO sweep requires at least six unique seeds")
    inputs = {name: _sha256(ROOT / name) for name in BOUND_INPUTS}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
        text=True, check=True,
    ).stdout.strip()
    manifest = {
        "schema_version": 1,
        "kind": "corrected_loso_multi_seed_sweep",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_head": head,
        "inputs_sha256": inputs,
        "date": "2025-09-16",
        "seeds": list(seeds),
        "runs": [],
    }
    _write_json(output_root / "manifest.json", manifest)

    try:
        for index, seed in enumerate(seeds):
            run_root = output_root / f"seed-{seed}"
            run_root.mkdir()
            print(f"[{index + 1}/{len(seeds)}] seed {seed}: demand build", flush=True)
            build_seconds = _run([
                sys.executable, "-u", "build_sumo_demand.py",
                "--date", "2025-09-16", "--source", "historical",
                "--begin", "00:00", "--end", "24:00",
                "--seed", str(seed), "--keep-scenarios",
            ], run_root / "build.log")
            meta = json.loads((ROOT / "sumo/demand_meta.json").read_text())
            actual_seed = meta.get("build_options", {}).get("seed")
            if actual_seed != seed:
                raise RuntimeError(
                    f"demand build seed differs: expected {seed}, got {actual_seed}")

            final_seed = index == len(seeds) - 1
            live_report = ROOT / "web/data/loso_report.json"
            report_path = live_report if final_seed else run_root / "loso_report.json"
            print(f"[{index + 1}/{len(seeds)}] seed {seed}: corrected LOSO", flush=True)
            loso_seconds = _run([
                sys.executable, "-u", "validate_sim.py",
                "--output", str(report_path),
            ], run_root / "loso.log")
            if final_seed:
                shutil.copy2(live_report, run_root / "loso_report.json")
            report = json.loads((run_root / "loso_report.json").read_text())
            if report.get("comparison_contract", {}).get("protocol") != \
                    EXPECTED_PROTOCOL:
                raise RuntimeError("corrected LOSO protocol was not published")

            tag_path = run_root / "tag_report.json"
            tag_log = run_root / "tag.log"
            started = time.monotonic()
            with tag_log.open("w", encoding="utf-8") as log:
                result = subprocess.run([
                    sys.executable, "tools/validate_dmrb.py",
                    "--report", str(run_root / "loso_report.json"), "--json",
                ], cwd=ROOT, capture_output=True, text=True, check=True)
                log.write(result.stderr)
            tag_path.write_text(result.stdout, encoding="utf-8")
            tag_seconds = round(time.monotonic() - started, 3)
            tag = json.loads(result.stdout)
            summary = _summarize(
                report, tag, seed,
                {"build": build_seconds, "loso": loso_seconds, "tag": tag_seconds},
            )
            summary["demand_build_id"] = meta.get("build_id")
            summary["artifacts_sha256"] = {
                name: _sha256(ROOT / name) for name in RUN_ARTIFACTS
            }
            summary["artifacts_sha256"].update({
                "loso_report.json": _sha256(run_root / "loso_report.json"),
                "tag_report.json": _sha256(tag_path),
            })
            _write_json(run_root / "summary.json", summary)
            manifest["runs"].append(summary)
            _write_json(output_root / "manifest.json", manifest)
            print(
                f"[{index + 1}/{len(seeds)}] seed {seed}: "
                f"TAG {summary['tag_satisfactory_share_pct']}%, daily median "
                f"{summary['daily_ratio_median']}",
                flush=True,
            )
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(output_root / "manifest.json", manifest)
        raise

    manifest["status"] = "complete"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(output_root / "manifest.json", manifest)
    print(f"complete: {output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
