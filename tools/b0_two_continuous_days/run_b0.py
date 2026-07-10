#!/usr/bin/env python3
"""Reproducible B0 two-continuous-days measurement.

Every demand build is deliberately synchronous.  A route snapshot is copied
only after ``subprocess.run(..., check=True)`` has returned and the just-written
``sumo/demand_meta.json`` has been checked for the requested date.  This is the
guard against the failed attempt-2 file-copy race.
"""
from __future__ import annotations

import json
import resource
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = ROOT / "sumo"
OUT = Path(__file__).resolve().parent / "results_20260710_verified"
DATES = (("day1", "2025-09-16"), ("day2", "2025-09-17"))
DAY_S = 86_400


def size(path: Path) -> int:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Expected non-empty file: {path}")
    return path.stat().st_size


def run_checked(cmd: list[str], *, timeout: int, log: Path, cwd: Path = ROOT) -> dict:
    """Block until one command ends; fail before any caller can continue."""
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    with log.open("w") as stream:
        try:
            completed = subprocess.run(
                cmd, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                check=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timed out after {timeout}s: {cmd}") from exc
    elapsed = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if completed.returncode != 0:  # check=True already covers this; keep explicit.
        raise RuntimeError(f"Non-zero return code {completed.returncode}: {cmd}")
    return {"wall_s": elapsed, "peak_rss_bytes": max(before, after)}


def build_and_snapshot(label: str, date: str) -> dict:
    log = OUT / f"build_{date}.log"
    metrics = run_checked(
        [sys.executable, "build_sumo_demand.py", "--date", date,
         "--begin", "00:00", "--end", "24:00"],
        timeout=1200, log=log,
    )
    meta_path = SUMO_DIR / "demand_meta.json"
    with meta_path.open() as stream:
        meta = json.load(stream)
    if meta.get("date") != date:
        raise RuntimeError(
            f"Demand metadata mismatch after {date}: got {meta.get('date')!r}; aborting"
        )
    snapshots = {}
    # This copy is intentionally in the same call frame, immediately after
    # successful completion and metadata validation -- never a timestamp poll.
    for suffix in ("", "_v1", "_v2"):
        source = SUMO_DIR / f"calibrated{suffix}.rou.xml"
        destination = OUT / f"{label}_{date}{suffix}.rou.xml"
        shutil.copy2(source, destination)
        snapshots[suffix or "q50"] = size(destination)
    return {"date": date, "meta": meta, "snapshots": snapshots, **metrics}


def write_additional(path: Path, edge_out: Path, duration_s: int, ident: str) -> None:
    root = ET.Element("additional")
    ET.SubElement(root, "edgeData", {
        "id": ident, "file": str(edge_out), "period": "900", "begin": "0",
        "end": str(duration_s), "excludeEmpty": "true",
    })
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def run_sumo(label: str, route: Path, duration_s: int) -> dict:
    import sumo
    home = Path(sumo.__file__).parent
    add = OUT / f"{label}.add.xml"
    edge = OUT / f"{label}_edgedata.xml"
    summary = OUT / f"{label}_summary.xml"
    tripinfo = OUT / f"{label}_tripinfo.xml"
    statistics = OUT / f"{label}_statistics.xml"
    write_additional(add, edge, duration_s, label)
    metrics = run_checked([
        str(home / "bin" / "sumo"), "--mesosim", "true",
        "--meso-junction-control", "true", "--meso-junction-control.limited", "true",
        "-n", str((SUMO_DIR / "net.net.xml").resolve()), "-r", str(route.resolve()),
        "-a", str(add.resolve()), "--seed", "1000", "--begin", "0",
        "--end", str(duration_s + 3600), "--summary-output", str(summary),
        "--summary-output.period", "900", "--tripinfo-output", str(tripinfo),
        "--tripinfo-output.write-unfinished", "true", "--statistic-output", str(statistics),
        "--ignore-route-errors", "true", "--no-step-log", "true", "--no-warnings", "true",
    ], timeout=300, log=OUT / f"{label}.log", cwd=OUT)
    for p in (add, edge, summary, tripinfo, statistics):
        size(p)
    return {"wall_s": metrics["wall_s"], "peak_rss_bytes": metrics["peak_rss_bytes"],
            "route_bytes": size(route), "edgedata_bytes": size(edge),
            "summary_bytes": size(summary), "tripinfo_bytes": size(tripinfo),
            "statistics_bytes": size(statistics), "scenario_bytes": sum(size(p) for p in (summary, tripinfo, statistics))}


def merge_routes(day1: Path, day2: Path, destination: Path) -> dict:
    first, second = ET.parse(day1), ET.parse(day2)
    root = first.getroot()
    if root.tag != "routes" or second.getroot().tag != "routes":
        raise RuntimeError("Expected two SUMO <routes> files")
    n1 = n2 = 0
    ids = set()
    for vehicle in root.findall("vehicle"):
        vehicle.set("id", "d1_" + vehicle.attrib["id"])
        ids.add(vehicle.attrib["id"]); n1 += 1
    for original in second.getroot().findall("vehicle"):
        vehicle = ET.fromstring(ET.tostring(original))
        vehicle.set("id", "d2_" + vehicle.attrib["id"])
        vehicle.set("depart", f"{float(vehicle.attrib['depart']) + DAY_S:g}")
        if vehicle.attrib["id"] in ids:
            raise RuntimeError(f"Duplicate vehicle ID after prefix: {vehicle.attrib['id']}")
        ids.add(vehicle.attrib["id"]); root.append(vehicle); n2 += 1
    ET.indent(first, space="  ")
    first.write(destination, encoding="utf-8", xml_declaration=True)
    return {"day1_vehicles": n1, "day2_vehicles": n2, "merged_vehicles": n1 + n2,
            "route_bytes": size(destination)}


def summary_at(path: Path, at_s: int) -> dict:
    for step in ET.parse(path).getroot().iter("step"):
        if abs(float(step.attrib["time"]) - at_s) < 0.01:
            return {key: int(float(step.attrib.get(key, "0"))) for key in ("running", "waiting", "teleports")}
    raise RuntimeError(f"No summary row at t={at_s} in {path}")


def edge_entered(path: Path) -> list[int]:
    values = []
    for interval in ET.parse(path).getroot().iter("interval"):
        values.append(sum(int(float(e.attrib.get("entered", "0"))) for e in interval.iter("edge")))
    return values


def trip_metrics(path: Path) -> dict:
    vehicles = list(ET.parse(path).getroot().iter("tripinfo"))
    return {"vehicles": len(vehicles), "timeLoss_s": sum(float(v.attrib.get("timeLoss", 0)) for v in vehicles)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    builds = {label: build_and_snapshot(label, date) for label, date in DATES}
    merged_route = OUT / "continuous_48h.rou.xml"
    merge = merge_routes(OUT / "day1_2025-09-16.rou.xml", OUT / "day2_2025-09-17.rou.xml", merged_route)
    if merge["merged_vehicles"] != merge["day1_vehicles"] + merge["day2_vehicles"]:
        raise RuntimeError("Merged vehicle count verification failed")
    runs = {
        "continuous_48h": run_sumo("continuous_48h", merged_route, 2 * DAY_S),
        "day1_24h": run_sumo("day1_24h", OUT / "day1_2025-09-16.rou.xml", DAY_S),
        "day2_24h": run_sumo("day2_24h", OUT / "day2_2025-09-17.rou.xml", DAY_S),
    }
    continuous_midnight = summary_at(OUT / "continuous_48h_summary.xml", DAY_S)
    separate_midnight = summary_at(OUT / "day1_24h_summary.xml", DAY_S)
    cont = edge_entered(OUT / "continuous_48h_edgedata.xml")
    d1 = edge_entered(OUT / "day1_24h_edgedata.xml")
    d2 = edge_entered(OUT / "day2_24h_edgedata.xml")
    if not (len(cont) == 192 and len(d1) == len(d2) == 96):
        raise RuntimeError(f"Wrong edgeData intervals: {len(cont)}, {len(d1)}, {len(d2)}")
    rows = []
    for qi in range(92, 101):
        chain_value = d1[qi] if qi < 96 else d2[qi - 96]
        rows.append({"quarter": qi, "continuous_entered": cont[qi], "separate_entered": chain_value,
                     "delta": cont[qi] - chain_value})
    report = {
        "builds": builds, "merge": merge, "runs": runs,
        "midnight": {"continuous": continuous_midnight, "separate_day1_end": separate_midnight},
        "near_midnight_edge_entered": rows,
        "trip_metrics": {
            "continuous": trip_metrics(OUT / "continuous_48h_tripinfo.xml"),
            "separate_day1": trip_metrics(OUT / "day1_24h_tripinfo.xml"),
            "separate_day2": trip_metrics(OUT / "day2_24h_tripinfo.xml"),
        },
    }
    report["trip_metrics"]["separate_total_timeLoss_s"] = (
        report["trip_metrics"]["separate_day1"]["timeLoss_s"] + report["trip_metrics"]["separate_day2"]["timeLoss_s"])
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
