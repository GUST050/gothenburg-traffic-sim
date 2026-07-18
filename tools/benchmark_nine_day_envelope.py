#!/usr/bin/env python3
"""Isolated nine-day SUMO resource proof for closure simulation envelopes.

The frozen seven-day q50 route release is copied to a temporary directory.
Its first two days are appended with unique IDs and departures shifted by
seven days, producing a deterministic nine-day load without recalibrating or
touching the active release.  This is a resource/continuity benchmark only;
it is not a new calibrated golden demand release.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import resource
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.runtime import sumo_home


FROZEN = ROOT / "runs/releases/golden-2025-09-16-7day-v1/multi_day"
DAY_S = 86_400
SOURCE_DAYS = 7
EXTRA_DAYS = 2
TOTAL_DAYS = SOURCE_DAYS + EXTRA_DAYS
TOTAL_S = TOTAL_DAYS * DAY_S
_ID = re.compile(r'\bid="([^"]+)"')
_DEPART = re.compile(r'\bdepart="([^"]+)"')


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _nonempty(path: Path) -> int:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"missing benchmark output: {path}")
    return path.stat().st_size


def extend_routes(source: Path, destination: Path) -> tuple[int, int]:
    """Copy seven days and append shifted copies of source days one and two."""
    source_count = extra_count = 0
    with source.open(encoding="utf-8") as input_stream, \
            destination.open("w", encoding="utf-8") as output_stream:
        for line in input_stream:
            if line.strip() == "</routes>":
                continue
            output_stream.write(line)
            if "<vehicle " in line:
                source_count += 1

        with source.open(encoding="utf-8") as second_pass:
            for line in second_pass:
                if "<vehicle " not in line:
                    continue
                depart_match, id_match = _DEPART.search(line), _ID.search(line)
                if depart_match is None or id_match is None:
                    raise RuntimeError("vehicle line lacks id or depart")
                depart = float(depart_match.group(1))
                if depart >= EXTRA_DAYS * DAY_S:
                    continue
                shifted = depart + SOURCE_DAYS * DAY_S
                line = (
                    line[:id_match.start(1)]
                    + f"env{SOURCE_DAYS + 1 + int(depart // DAY_S)}_"
                    + line[id_match.start(1):]
                )
                depart_match = _DEPART.search(line)
                line = (
                    line[:depart_match.start(1)]
                    + f"{shifted:g}"
                    + line[depart_match.end(1):]
                )
                output_stream.write(line)
                extra_count += 1
        output_stream.write("</routes>\n")
    return source_count, extra_count


def _statistics(path: Path) -> dict:
    root = ET.parse(path).getroot()
    vehicles = root.find("vehicles")
    teleports = root.find("teleports")
    return {
        "loaded": int(float(vehicles.get("loaded", "0"))),
        "inserted": int(float(vehicles.get("inserted", "0"))),
        "running": int(float(vehicles.get("running", "0"))),
        "waiting": int(float(vehicles.get("waiting", "0"))),
        "teleports": int(float(
            teleports.get("total", "0") if teleports is not None else 0)),
    }


def _boundaries(path: Path) -> list[dict]:
    steps = [
        {
            "time_s": float(step.get("time")),
            "loaded": int(float(step.get("loaded", "0"))),
            "inserted": int(float(step.get("inserted", "0"))),
            "running": int(float(step.get("running", "0"))),
            "waiting": int(float(step.get("waiting", "0"))),
            "teleports": int(float(step.get("teleports", "0"))),
        }
        for step in ET.parse(path).getroot().iter("step")
    ]
    result = []
    for day in range(TOTAL_DAYS + 1):
        boundary = day * DAY_S
        eligible = [row for row in steps if row["time_s"] <= boundary + 1e-6]
        if not eligible:
            raise RuntimeError(f"summary lacks boundary evidence at {boundary}")
        row = eligible[-1]
        result.append({
            "day_boundary": day,
            **row,
            "lag_s": round(boundary - row["time_s"], 3),
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    source = FROZEN / "calibrated.rou.xml"
    network = FROZEN / "net.net.xml"
    _nonempty(source)
    _nonempty(network)

    with tempfile.TemporaryDirectory(prefix="closure-envelope-9day-") as raw:
        work = Path(raw)
        routes = work / "nine_day.rou.xml"
        summary = work / "summary.xml"
        statistics = work / "statistics.xml"
        source_count, extra_count = extend_routes(source, routes)
        expected = source_count + extra_count
        binary = sumo_home() / "bin" / "sumo"
        command = [
            str(binary),
            "--mesosim", "true",
            "--meso-junction-control", "true",
            "--meso-junction-control.limited", "true",
            "-n", str(network.resolve()),
            "-r", str(routes.resolve()),
            "--seed", "1000",
            "--begin", "0",
            "--end", str(TOTAL_S + 3600),
            "--summary-output", str(summary.resolve()),
            "--summary-output.period", "900",
            "--statistic-output", str(statistics.resolve()),
            "--ignore-route-errors", "true",
            "--no-step-log", "true",
            "--no-warnings", "true",
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=work,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            raise RuntimeError(
                f"nine-day SUMO failed: {completed.stderr[-2000:]}")
        stats = _statistics(statistics)
        boundaries = _boundaries(summary)
        peak = _rss_bytes()
        healthy = (
            stats["loaded"] == expected
            and stats["inserted"] == expected
            and stats["running"] == 0
            and stats["waiting"] == 0
            and stats["teleports"] == 0
            and all(
                math.isfinite(row["lag_s"]) and 0 <= row["lag_s"] <= 900
                for row in boundaries
            )
        )
        report = {
            "schema_version": 1,
            "status": "pass" if healthy else "fail",
            "purpose": "nine_day_closure_envelope_resource_proof",
            "calibration_claim": "none; days 8–9 duplicate frozen q50 days 1–2",
            "days": TOTAL_DAYS,
            "duration_s": TOTAL_S,
            "source_vehicles": source_count,
            "appended_vehicles": extra_count,
            "total_vehicles": expected,
            "wall_s": round(elapsed, 3),
            "maximum_resident_set_bytes": peak,
            "route_bytes": _nonempty(routes),
            "summary_bytes": _nonempty(summary),
            "statistics_bytes": _nonempty(statistics),
            "statistics": stats,
            "boundaries": boundaries,
            "sumo_version": subprocess.run(
                [str(binary), "--version"],
                capture_output=True, text=True, timeout=20, check=False,
            ).stdout.splitlines()[0],
        }
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        if not healthy:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
