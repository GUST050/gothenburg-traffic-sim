#!/usr/bin/env python3
"""Run immutable, validation-only picker diagnostics for selected LOSO folds."""

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
DEFAULT_SEEDS = (20260808, 20260811)
DEFAULT_STATIONS = ("107", "134", "2276")
BOUND_INPUTS = (
    "tools/run_loso_picker_diagnostics.py",
    "build_candidates.py",
    "build_sumo_demand.py",
    "assignment_priors.py",
    "traffic_sim/demand/pfe.py",
    "traffic_sim/confidence/loso.py",
    "traffic_sim/confidence/picker_diagnostics.py",
    "traffic_sim/confidence/controlled_rounding.py",
    "traffic_sim/confidence/route_support.py",
    "sumo/net.net.xml",
    "web/data/flows.json",
    "data_in/sensors.json",
)
LIVE_ARTIFACTS = (
    "sumo/demand_meta.json",
    "sumo/candidates.meta.json",
    "sumo/assignment_priors.json",
    "sumo/prior_flows.json",
    "sumo/direction_split.json",
    "web/data/observability.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def run_logged(command: list[str], log_path: Path) -> float:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n")
        log.flush()
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                       check=True)
    return round(time.monotonic() - started, 3)


def station_result(report: dict, station: str) -> dict:
    station_data = report["stations"][station]
    evaluation, value = next(iter(station_data["edges"].items()))
    result = {
        "evaluation": evaluation,
        "ratio": value["ratio"],
        "geh_ok_pct": value["geh_ok_pct"],
        "measured_total": value["measured_total"],
        "simulated_total": value["simulated_total"],
        "held_edge_assignment_ceiling": station_data[
            "held_edge_assignment_ceiling"],
    }
    if "picker_diagnostics" in station_data:
        result["picker_diagnostics_sha256"] = station_data[
            "picker_diagnostics"]["sha256"]
    if "controlled_integer_rounding" in station_data:
        result["controlled_integer_rounding"] = station_data[
            "controlled_integer_rounding"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    parser.add_argument("--stations", nargs="+", default=DEFAULT_STATIONS)
    parser.add_argument(
        "--publication-treatment-only", action="store_true",
        help="run the paired selected folds without another large picker trace")
    parser.add_argument(
        "--controlled-rounding-treatment", action="store_true",
        help="run the validation-only joint integer-rounding treatment")
    args = parser.parse_args()

    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"refusing nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    seeds = tuple(args.seeds)
    stations = tuple(str(station) for station in args.stations)
    if len(seeds) < 2 or len(seeds) != len(set(seeds)):
        raise SystemExit("picker diagnostic replay requires >=2 unique seeds")
    if not stations or len(stations) != len(set(stations)):
        raise SystemExit("picker diagnostic stations must be nonempty and unique")

    manifest = {
        "schema_version": 1,
        "kind": (
            "paired_loso_production_joint_projection_diagnostics"
            if args.controlled_rounding_treatment
            else "paired_loso_integer_publication_treatment"
            if args.publication_treatment_only
            else "paired_loso_picker_diagnostic_replay"),
        "evidence_class": "diagnostic_replay",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "date": "2025-09-16",
        "seeds": list(seeds),
        "stations": list(stations),
        "inputs_sha256": {
            name: sha256_file(ROOT / name) for name in BOUND_INPUTS},
        "runs": [],
    }
    write_json(output_root / "manifest.json", manifest)

    try:
        for index, seed in enumerate(seeds):
            run_root = output_root / f"seed-{seed}"
            run_root.mkdir()
            print(f"[{index + 1}/{len(seeds)}] seed {seed}: demand build", flush=True)
            build_seconds = run_logged([
                sys.executable, "-u", "build_sumo_demand.py",
                "--date", "2025-09-16", "--source", "historical",
                "--begin", "00:00", "--end", "24:00",
                "--seed", str(seed), "--keep-scenarios",
            ], run_root / "build.log")

            meta = json.loads((ROOT / "sumo/demand_meta.json").read_text())
            if meta.get("build_options", {}).get("seed") != seed:
                raise RuntimeError("demand build published the wrong seed")

            report_path = run_root / "loso_report.json"
            print(f"[{index + 1}/{len(seeds)}] seed {seed}: folds "
                  f"{','.join(stations)}", flush=True)
            loso_command = [
                sys.executable, "-u", "validate_sim.py",
                "--output", str(report_path),
                "--stations", *stations,
            ]
            if not args.publication_treatment_only:
                diagnostics_dir = run_root / "picker"
                loso_command.extend([
                    "--picker-diagnostics-dir", str(diagnostics_dir)])
            if args.controlled_rounding_treatment:
                loso_command.append("--experimental-controlled-rounding")
            loso_seconds = run_logged(loso_command, run_root / "loso.log")

            report = json.loads(report_path.read_text())
            if sorted(report["stations"]) != sorted(stations):
                raise RuntimeError("diagnostic replay station subset differs")
            diagnostics_enabled = bool(report["comparison_contract"].get(
                "picker_diagnostics_enabled"))
            if diagnostics_enabled == args.publication_treatment_only:
                raise RuntimeError("picker diagnostics mode differs from request")
            if not report["comparison_contract"].get(
                    "controlled_integer_rounding"):
                raise RuntimeError("LOSO did not use production joint projection")
            diagnostics_enabled = bool(report["comparison_contract"].get(
                "joint_integer_diagnostics_enabled"))
            if diagnostics_enabled != args.controlled_rounding_treatment:
                raise RuntimeError(
                    "joint projection diagnostics mode differs from request")

            folds = run_root / "folds"
            folds.mkdir()
            for station in stations:
                for suffix in ("rou.xml", "agents.json"):
                    source = ROOT / f"sumo/loso_{station}.{suffix}"
                    if source.is_file():
                        shutil.copy2(source, folds / source.name)
                source = ROOT / f"sumo/loso_ed_{station}.xml"
                shutil.copy2(source, folds / source.name)
            shutil.copy2(ROOT / "sumo/calibrated.rou.xml",
                         folds / "all_stations_calibrated.rou.xml")

            artifacts_dir = run_root / "artifacts"
            artifacts_dir.mkdir()
            live_hashes = {}
            for name in LIVE_ARTIFACTS:
                source = ROOT / name
                live_hashes[name] = sha256_file(source)
                shutil.copy2(source, artifacts_dir / source.name)

            archived_hashes = {
                str(path.relative_to(run_root)): sha256_file(path)
                for path in sorted(run_root.rglob("*")) if path.is_file()
                and path.name not in {"summary.json"}
            }
            summary = {
                "seed": seed,
                "demand_build_id": meta.get("build_id"),
                "candidate_pool_sha256": report["comparison_contract"][
                    "candidate_pool_sha256"],
                "seconds": {"build": build_seconds, "loso": loso_seconds},
                "stations": {
                    station: station_result(report, station)
                    for station in stations
                },
                "live_artifacts_sha256": live_hashes,
                "archived_artifacts_sha256": archived_hashes,
            }
            write_json(run_root / "summary.json", summary)
            manifest["runs"].append(summary)
            write_json(output_root / "manifest.json", manifest)
            ratios = ", ".join(
                f"{station}={summary['stations'][station]['ratio']}"
                for station in stations)
            print(f"[{index + 1}/{len(seeds)}] seed {seed}: {ratios}", flush=True)
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output_root / "manifest.json", manifest)
        raise

    manifest["status"] = "complete"
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output_root / "manifest.json", manifest)
    print(f"complete: {output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
