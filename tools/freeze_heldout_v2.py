#!/usr/bin/env python3
"""Freeze the held-out v2 monthly-proxy validation manifest (untouched cases).

Held-out v2 contract (IMPROVEMENT_PLAN.md, step-4 recovery decision frozen
2026-07-19): a NEW set of cases whose edges are disjoint from every v1 case
edge, gated on practical-winner recall (with the golden policy's frozen
300 s practical-equivalence tolerance), p90 normalized shortlist regret and
failure/disqualification recall.  Spearman and strict exact-tie recall stay
as reported diagnostics.  The gate thresholds live INSIDE the manifest and
are therefore part of its content key: provably frozen before outcomes.

Edge selection is deterministic (stable SHA-256 ordering per case) over
structural features computed from the tracked network:

- road_class: the edge's OSM ``highway`` value;
- sensor_distance_band: ``dist_sensor_m`` (<250 near, <750 medium, else far);
- topology: local street density — count of network edges whose midpoint
  lies within 250 m of the edge midpoint (calibrated on the tracked
  network's tertiles: <45 single_detour, 45-75 multiple_detours,
  >75 dense_alternatives).  This is a structural proxy, declared as such.

Run once; the output is committed and never regenerated after outcomes.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from traffic_sim.core.contracts import DemandBuildSpec  # noqa: E402
from traffic_sim.simulation.proxy_validation import (  # noqa: E402
    validate_validation_manifest,
)

NETWORK = Path("web/data/network.geojson")
V1_MANIFEST = Path("validation/monthly_proxy_manifest.json")
POLICY = Path("validation/monthly_search_policy_v1.json")
OUT = Path("validation/monthly_proxy_manifest_v2.json")

DENSITY_RADIUS_M = 250.0
SINGLE_DETOUR_BELOW = 45
DENSE_ALTERNATIVES_ABOVE = 75
MINIMUM_EDGE_LENGTH_M = 60.0

# (case_id, road_class, band, topology_target_or_None,
#  dates (start, end, max_consecutive_days), band times, work minutes,
#  day_type, duration_label, demand spec)
CASE_TEMPLATES = [
    ("v2-primary-far-weekday-4h", "primary", "far", None,
     ("2027-02-11", "2027-02-11", 1), ("06:00", "12:00"), 240,
     "weekday", "single_4h", ("2027-02-11", 1)),
    ("v2-secondary-near-weekday-8h", "secondary", "near", None,
     ("2027-02-26", "2027-02-26", 1), ("06:00", "16:00"), 480,
     "weekday", "single_8h", ("2027-02-26", 1)),
    ("v2-secondary-medium-weekend-4h", "secondary", "medium", None,
     ("2027-07-17", "2027-07-17", 1), ("10:00", "16:00"), 240,
     "weekend", "single_4h", ("2027-07-17", 1)),
    ("v2-tertiary-near-weekday-4h", "tertiary", "near", None,
     ("2027-07-22", "2027-07-22", 1), ("06:00", "12:00"), 240,
     "weekday", "single_4h", ("2027-07-22", 1)),
    ("v2-tertiary-far-weekday-8h", "tertiary", "far", "single_detour",
     ("2027-09-15", "2027-09-15", 1), ("06:00", "16:00"), 480,
     "weekday", "single_8h", ("2027-09-15", 1)),
    ("v2-residential-near-weekday-8h", "residential", "near",
     "dense_alternatives",
     ("2027-10-20", "2027-10-20", 1), ("06:00", "16:00"), 480,
     "weekday", "single_8h", ("2027-10-20", 1)),
    ("v2-residential-medium-weekday-4h", "residential", "medium",
     "multiple_detours",
     ("2027-11-18", "2027-11-18", 1), ("12:00", "18:00"), 240,
     "weekday", "single_4h", ("2027-11-18", 1)),
    ("v2-residential-far-holiday-4h", "residential", "far", "single_detour",
     ("2027-12-24", "2027-12-24", 1), ("08:00", "14:00"), 240,
     "holiday", "single_4h", ("2027-12-24", 1)),
    ("v2-unclassified-medium-weekday-4h", "unclassified", "medium",
     "multiple_detours",
     ("2027-07-15", "2027-07-15", 1), ("12:00", "18:00"), 240,
     "weekday", "single_4h", ("2027-07-15", 1)),
    ("v2-unclassified-far-weekend-8h", "unclassified", "far",
     "dense_alternatives",
     ("2027-07-17", "2027-07-17", 1), ("08:00", "18:00"), 480,
     "weekend", "single_8h", ("2027-07-17", 1)),
    ("v2-tertiary-medium-mixed-24h", "tertiary", "medium", None,
     ("2027-07-15", "2027-07-17", 3), ("06:00", "16:00"), 1440,
     "mixed_weekday_weekend", "recurring_24h_total", ("2027-07-15", 3)),
    ("v2-residential-far-mixed-40h", "residential", "far", None,
     ("2027-07-15", "2027-07-19", 5), ("08:00", "17:00"), 2400,
     "mixed_weekday_weekend", "recurring_40h_total", ("2027-07-15", 5)),
]


def _midpoint(feature):
    coords = feature["geometry"]["coordinates"]
    return coords[len(coords) // 2]


def _distance_m(a, b):
    return math.hypot(
        (a[0] - b[0]) * math.cos(math.radians(57.7)) * 111320.0,
        (a[1] - b[1]) * 111320.0,
    )


def main() -> None:
    if OUT.exists():
        raise SystemExit(
            f"{OUT} already exists — a frozen held-out manifest is never "
            "regenerated; delete it manually only if no outcomes exist yet"
        )
    geo = json.loads(NETWORK.read_text())
    edges = [
        feature for feature in geo["features"]
        if feature["geometry"]["type"] == "LineString"
    ]
    v1 = validate_validation_manifest(json.loads(V1_MANIFEST.read_text()))
    v1_edges = {
        edge for case in v1["cases"] for edge in case["directed_edges"]
    }
    policy = json.loads(POLICY.read_text())
    practical_equivalence_s = float(
        policy["finalist"]["practical_equivalence_s"]
    )

    midpoints = [(f["properties"]["id"], _midpoint(f)) for f in edges]

    def density(mid) -> int:
        return sum(
            1 for _, other in midpoints
            if _distance_m(mid, other) < DENSITY_RADIUS_M
        )

    def topology_label(mid) -> str:
        count = density(mid)
        if count < SINGLE_DETOUR_BELOW:
            return "single_detour"
        if count > DENSE_ALTERNATIVES_ABOVE:
            return "dense_alternatives"
        return "multiple_detours"

    def candidates(road_class: str, band: str) -> list[dict]:
        rows = []
        for feature in edges:
            properties = feature["properties"]
            highway = properties.get("highway")
            if isinstance(highway, list):
                highway = highway[0]
            if highway != road_class:
                continue
            if properties["id"] in v1_edges or properties.get("sensor_id"):
                continue
            if (properties.get("length_m") or 0) < MINIMUM_EDGE_LENGTH_M:
                continue
            distance = properties.get("dist_sensor_m")
            edge_band = (
                "near" if distance < 250
                else "medium" if distance < 750
                else "far"
            )
            if edge_band != band:
                continue
            rows.append({
                "id": properties["id"],
                "mid": _midpoint(feature),
            })
        return rows

    used_edges: set[str] = set()
    cases = []
    for (case_id, road_class, band, topology_target, dates, band_times,
         work_minutes, day_type, duration, demand) in CASE_TEMPLATES:
        pool = candidates(road_class, band)
        pool.sort(key=lambda row: hashlib.sha256(
            f"{case_id}:{row['id']}".encode()).hexdigest())
        chosen = None
        for row in pool:
            if row["id"] in used_edges:
                continue
            label = topology_label(row["mid"])
            if topology_target is not None and label != topology_target:
                continue
            chosen = (row["id"], label)
            break
        if chosen is None:
            raise SystemExit(
                f"{case_id}: no unused {road_class}/{band} edge with "
                f"topology {topology_target}"
            )
        edge_id, topology = chosen
        used_edges.add(edge_id)
        demand_spec = DemandBuildSpec(
            start_date=demand[0],
            source="forecast",
            days=demand[1],
            purpose="closure_envelope" if demand[1] > 1 else "standard",
        )
        band_map = {
            "near": "near_0_250m",
            "medium": "medium_250_750m",
            "far": "far_over_750m",
        }
        cases.append({
            "case_id": case_id,
            "closure_search_spec": {
                "schema_version": 1,
                "kind": "closure_search",
                "search_id": case_id,
                "directed_edges": [edge_id],
                "demand_build_id": demand_spec.build_key,
                "source": "forecast",
                "permitted_date_start": dates[0],
                "permitted_date_end": dates[1],
                "required_work_minutes": work_minutes,
                "max_consecutive_start_days": dates[2],
                "permitted_daily_band": {
                    "earliest_start": band_times[0],
                    "latest_end": band_times[1],
                },
            },
            "strata": {
                "road_class": road_class,
                "sensor_distance_band": band_map[band],
                "topology": topology,
                "duration": duration,
                "day_type": day_type,
            },
        })
        print(f"{case_id}: edge {edge_id} topology={topology} "
              f"demand={demand_spec.build_key}")

    manifest_raw = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_manifest",
        "proxy_version": "monthly_proxy_v1",
        "frozen_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "frozen_before_outcomes": True,
        "minimum_cases": 12,
        "minimum_spearman_case_fraction": 0.5,
        "minimum_ranking_case_fraction": 0.5,
        "gate": {
            "practical_equivalence_s": practical_equivalence_s,
            "practical_winner_recall_minimum": 0.90,
            "p90_normalized_shortlist_regret_maximum": 0.10,
            "failure_disqualification_recall_minimum": 0.60,
        },
        "required_strata": v1["required_strata"],
        "cases": cases,
    }
    normalized = validate_validation_manifest(manifest_raw)
    OUT.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    total = sum(len(case["schedule_ids"]) for case in normalized["cases"])
    print(f"\nfroze {OUT} content_key={normalized['content_key']}")
    print(f"{len(normalized['cases'])} cases, {total} exact schedules, "
          f"practical_equivalence_s={practical_equivalence_s}")


if __name__ == "__main__":
    main()
