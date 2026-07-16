"""Generate an evidence-bound sensor contribution or placement report.

Examples:
  python3 sensor_contribution.py --sensor 107 --before-los o.json \
      --after-los n.json --before-network old.geojson \
      --after-network web/data/network.geojson --out report.json
  python3 sensor_contribution.py --placements --out placements.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from traffic_sim.intake.contribution import build_contribution, rank_placements


def _read(path: Path) -> dict:
    with open(path) as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sensor")
    parser.add_argument("--before-los", type=Path)
    parser.add_argument("--after-los", type=Path)
    parser.add_argument("--before-network", type=Path)
    parser.add_argument("--after-network", type=Path, default=Path("web/data/network.geojson"))
    parser.add_argument("--flows", type=Path, default=Path("web/data/flows.json"))
    parser.add_argument("--placements", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.placements:
        report = {"schema_version": 1, "kind": "sensor_placement_screen",
                  "candidates": rank_placements(_read(args.after_network), top_n=args.top)}
    else:
        required = (args.sensor, args.before_los, args.after_los,
                    args.before_network)
        if any(value is None for value in required):
            parser.error("sensor contribution requires --sensor, both LOSO and both network artifacts")
        flow_doc = _read(args.flows)
        report = build_contribution(
            sensor_id=str(args.sensor), registry=_read(Path("data_in/sensors.json")),
            network_before=_read(args.before_network), network_after=_read(args.after_network),
            loso_before=_read(args.before_los), loso_after=_read(args.after_los),
            flows_after=flow_doc.get("flows", flow_doc),
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()

