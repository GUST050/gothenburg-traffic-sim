#!/usr/bin/env python3
"""Merge two calibrated one-day SUMO route files for the B0 measurement.

This is deliberately isolated from the production demand/scenario pipeline.
Day 1 is copied unchanged; every day-2 vehicle gets a unique ``d2_`` ID and
its departure is shifted by 86,400 seconds.  It also writes the matching
192-quarter edgeData additional file.
"""

from __future__ import annotations

import argparse
import copy
import xml.etree.ElementTree as ET
from pathlib import Path


DAY_S = 86_400
TOTAL_S = 2 * DAY_S


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day1", type=Path, required=True)
    p.add_argument("--day2", type=Path, required=True)
    p.add_argument("--routes-out", type=Path, required=True)
    p.add_argument("--additional-out", type=Path, required=True)
    p.add_argument("--edgedata-out", type=Path, required=True)
    return p.parse_args()


def merge(day1: Path, day2: Path, routes_out: Path) -> tuple[int, int]:
    first = ET.parse(day1)
    second = ET.parse(day2)
    root = first.getroot()
    if root.tag != "routes" or second.getroot().tag != "routes":
        raise ValueError("both inputs must have a <routes> root")

    # Prefix *both* days.  The calibrated files currently happen to use the
    # same candidate IDs, so leaving day 1 untouched would still collide with
    # an unprefixed input in a later extension of this probe.
    d1 = 0
    for child in root:
        if child.tag == "vehicle":
            child.set("id", "d1_" + child.attrib["id"])
            d1 += 1
    d2 = 0
    for child in second.getroot():
        if child.tag != "vehicle":
            continue  # vTypes/routes are already present from day 1.
        vehicle = copy.deepcopy(child)
        vehicle.set("id", "d2_" + vehicle.attrib["id"])
        vehicle.set("depart", f"{float(vehicle.attrib['depart']) + DAY_S:g}")
        root.append(vehicle)
        d2 += 1

    routes_out.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(first, space="  ")
    first.write(routes_out, encoding="utf-8", xml_declaration=True)
    return d1, d2


def write_additional(path: Path, edgedata_out: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("additional")
    ET.SubElement(root, "edgeData", {
        "id": "b0_48h",
        "file": edgedata_out.name,
        "period": "900",
        "begin": "0",
        "end": str(TOTAL_S),
        "excludeEmpty": "true",
    })
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    args = arguments()
    d1, d2 = merge(args.day1, args.day2, args.routes_out)
    write_additional(args.additional_out, args.edgedata_out)
    print(f"Merged {d1} day-1 + {d2} day-2 vehicles = {d1 + d2}")
    print(f"Routes: {args.routes_out}")
    print(f"edgeData config: {args.additional_out} (192 × 900 s)")


if __name__ == "__main__":
    main()
