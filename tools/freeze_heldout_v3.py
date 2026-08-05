#!/usr/bin/env python3
"""Freeze the held-out v3 monthly-proxy validation manifest.

Why a v3 at all (owner decision, 2026-07-22): held-out v3-on-v2-cases passed
every defined check, but on a case set that could not have failed the ranking
part of them. Re-measured with the new spread metric, six of its seven
ranking cases had an objective spread of EXACTLY 0.0 s and the seventh 71 s
inside a 300 s indifference band — so "the proxy shortlisted a practical
winner" was true no matter what the proxy did. This set fixes that, and the
gate now says so out loud:

* ranking cases are built on edges whose closure is MEASURED (tools/
  pilot_heldout_v3_candidates.py, before freezing, without ever consulting
  the proxy) to move the objective by more than the practical-equivalence
  band between two of the schedules the case will contain;
* they vary the DATE across a five-day envelope rather than the hour within
  one day, because the pilot showed the hour axis cannot separate an
  eligible 4 h closure on this network while the calendar axis can;
* the manifest declares `minimum_discriminating_case_fraction` and
  `discriminating_practical_winner_recall_minimum`, so a set that turns out
  degenerate FAILS instead of passing vacuously.

Stratum coverage still has to be complete, and the strata that carry no
traffic (residential, unclassified) cannot discriminate by construction —
those cases are kept, marked, and deliberately not counted on for the
discrimination fraction.

Edges stay disjoint from v1 and v2. Run once; the output is committed and
never regenerated after outcomes exist.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.heldout_v3_selection import (  # noqa: E402
    input_hashes, load_candidates, rank_for,
)
from traffic_sim.core.contracts import DemandBuildSpec  # noqa: E402
from traffic_sim.simulation.proxy_validation import (  # noqa: E402
    validate_validation_manifest,
)

# Every pilot round contributes; a case may only take an edge some round
# actually confirmed. Later files win when an edge appears twice.
PILOTS = (Path("runs") / "heldout-v3-pilot" / "probes.json",
          Path("runs") / "heldout-v3-pilot-round2" / "probes.json",
          Path("runs") / "heldout-v3-pilot-daytype" / "probes.json")
V1_MANIFEST = Path("validation/monthly_proxy_manifest.json")
V2_MANIFEST = Path("validation/monthly_proxy_manifest_v2.json")
POLICY = Path("validation/monthly_search_policy_v1.json")
OUT = Path("validation/monthly_proxy_manifest_v3.json")
SELECTION_RECORD = Path("validation/heldout_v3_selection.json")

# (case_id, road_class, band, topology, discriminating,
#  (date_start, date_end, max_consecutive_days),
#  (earliest_start, latest_end), work_minutes, resolution_minutes,
#  day_type, duration, (demand_start, demand_days))
#
# The ranking cases vary the DATE, not the hour, and that is a measured
# choice rather than a preference. Probing 25 edges through the campaign's
# own machinery (runs/heldout-v3-pilot*) showed that for a 4 h closure the
# hour axis cannot produce a ranking case on this network: the five edges
# that stayed eligible at both a 07:00 and a 12:00 start spread only
# 5.8-88 s, inside the 300 s indifference band, while every edge that spread
# more was disqualified at one of the two starts for teleports or truncated
# vehicles. The calendar axis does work - the same closure on Thursday,
# Saturday and Sunday inside one 5-day envelope stayed eligible on all three
# and spread 509 s - because weekend demand is genuinely lower, not because
# the network broke. So ranking cases hold the hour band narrow and let the
# permitted date range span day types; hour-band cases remain as strata
# fillers, where their near-tied objectives are honestly what they are.
CASE_TEMPLATES = [
    # Ranking cases: one 5-day envelope (Thu-Mon, two weekend days), a narrow
    # daily band, one closure per day. Schedules differ by DATE.
    ("v3-secondary-daytype-4h", "secondary", None, None,
     True, ("2027-07-15", "2027-07-19", 1), ("08:00", "14:00"), 240, 120,
     "mixed_weekday_weekend", "single_4h", ("2027-07-15", 5)),
    ("v3-tertiary-daytype-4h", "tertiary", None, None,
     True, ("2027-07-15", "2027-07-19", 1), ("08:00", "14:00"), 240, 120,
     "mixed_weekday_weekend", "single_4h", ("2027-07-15", 5)),
    ("v3-secondary-daytype-8h", "secondary", None, None,
     True, ("2027-07-15", "2027-07-19", 1), ("07:00", "17:00"), 480, 120,
     "mixed_weekday_weekend", "single_8h", ("2027-07-15", 5)),
    ("v3-tertiary-daytype-8h", "tertiary", None, None,
     True, ("2027-07-15", "2027-07-19", 1), ("07:00", "17:00"), 480, 120,
     "mixed_weekday_weekend", "single_8h", ("2027-07-15", 5)),
    ("v3-unclassified-daytype-4h", "unclassified", None, None,
     True, ("2027-07-15", "2027-07-19", 1), ("08:00", "14:00"), 240, 120,
     "mixed_weekday_weekend", "single_4h", ("2027-07-15", 5)),
    # Strata fillers: single dates and hour bands, kept honest about being
    # near-tied rather than dressed up as ranking evidence.
    ("v3-primary-far-weekday-4h", "primary", "far_over_750m", None,
     False, ("2027-09-15", "2027-09-15", 1), ("06:00", "14:00"), 240, 120,
     "weekday", "single_4h", ("2027-09-15", 1)),
    ("v3-secondary-near-weekend-8h", "secondary", "near_0_250m", None,
     False, ("2027-07-17", "2027-07-17", 1), ("06:00", "18:00"), 480, 120,
     "weekend", "single_8h", ("2027-07-17", 1)),
    ("v3-tertiary-medium-holiday-4h", "tertiary", "medium_250_750m", None,
     False, ("2027-12-24", "2027-12-24", 1), ("06:00", "20:00"), 240, 120,
     "holiday", "single_4h", ("2027-12-24", 1)),
    ("v3-residential-near-weekday-8h", "residential", "near_0_250m",
     "dense_alternatives", False, ("2027-09-15", "2027-09-15", 1),
     ("06:00", "20:00"), 480, 120, "weekday", "single_8h", ("2027-09-15", 1)),
    ("v3-residential-far-weekday-4h", "residential", "far_over_750m",
     "single_detour", False, ("2027-09-15", "2027-09-15", 1),
     ("06:00", "20:00"), 240, 120, "weekday", "single_4h", ("2027-09-15", 1)),
    ("v3-unclassified-medium-weekend-4h", "unclassified", "medium_250_750m",
     "multiple_detours", False, ("2027-07-17", "2027-07-17", 1),
     ("06:00", "20:00"), 240, 120, "weekend", "single_4h", ("2027-07-17", 1)),
    ("v3-tertiary-medium-mixed-24h", "tertiary", "medium_250_750m", None,
     False, ("2027-07-15", "2027-07-17", 3), ("06:00", "18:00"), 1440, 120,
     "mixed_weekday_weekend", "recurring_24h_total", ("2027-07-15", 3)),
    ("v3-secondary-far-mixed-40h", "secondary", "far_over_750m", None,
     False, ("2027-07-15", "2027-07-19", 5), ("06:00", "18:00"), 2400, 120,
     "mixed_weekday_weekend", "recurring_40h_total", ("2027-07-15", 5)),
]

# Fraction of RANKING cases that must genuinely separate start times, and how
# often the shortlist must hold a practical winner among exactly those.
# Five of the thirteen templates above are the pilot-confirmed ones; the
# floor is set below that so a case that degenerates on the campaign's own
# demand does not automatically fail the set, but a mostly-degenerate set does.
MINIMUM_DISCRIMINATING_CASE_FRACTION = 0.4
DISCRIMINATING_PRACTICAL_WINNER_RECALL_MINIMUM = 0.9


def _pilot_edges() -> dict[str, list[dict]]:
    """Probed edges that separated start times, best structural load first."""
    available = [path for path in PILOTS if path.is_file()]
    if not available:
        raise SystemExit(
            "no pilot probes found — run tools/pilot_heldout_v3_candidates.py "
            "first; a discriminating case set may not be guessed at")
    records: dict[str, dict] = {}
    for path in available:
        probes = json.loads(path.read_text())
        for edge_id, record in probes["candidates"].items():
            records[edge_id] = {**record, "edge_id": edge_id,
                                "pilot": str(path)}
    by_class: dict[str, list[dict]] = {}
    for edge_id, record in sorted(records.items()):
        if not record.get("discriminating"):
            continue
        by_class.setdefault(record["road_class"], []).append(record)
    for rows in by_class.values():
        rows.sort(key=lambda row: (-row["probe_spread_s"], row["edge_id"]))
    return by_class


def main() -> None:
    if OUT.exists():
        raise SystemExit(
            f"{OUT} already exists — a frozen held-out manifest is never "
            "regenerated; delete it manually only if no outcomes exist yet")
    v1 = validate_validation_manifest(json.loads(V1_MANIFEST.read_text()))
    v2 = validate_validation_manifest(json.loads(V2_MANIFEST.read_text()))
    practical_equivalence_s = float(json.loads(POLICY.read_text())
                                    ["finalist"]["practical_equivalence_s"])
    rows = load_candidates()
    by_edge = {row["edge_id"]: row for row in rows}
    discriminating_pool = _pilot_edges()
    used: set[str] = set()
    cases = []
    selection = []

    for (case_id, road_class, band, topology, discriminating, dates,
         band_times, work_minutes, resolution, day_type, duration,
         demand) in CASE_TEMPLATES:
        if discriminating:
            pool = [row for row in discriminating_pool.get(road_class, [])
                    if row["edge_id"] not in used
                    and (band is None
                         or row["sensor_distance_band"] == band)]
            if not pool:
                raise SystemExit(
                    f"{case_id}: no pilot-confirmed discriminating "
                    f"{road_class}/{band} edge left — widen the pilot rather "
                    "than weakening the case")
            chosen = pool[0]
            evidence = {
                "source": "pilot_probe",
                "probe_spread_s": chosen["probe_spread_s"],
                "structural_peak_quarter_load":
                    chosen["structural_peak_quarter_load"],
            }
        else:
            pool = rank_for(rows, road_class=road_class, band=band,
                            topology=topology, discriminating=False,
                            exclude=used)
            if not pool:
                raise SystemExit(
                    f"{case_id}: no unused {road_class}/{band}/{topology} edge")
            chosen = pool[0]
            evidence = {
                "source": "structural_only",
                "structural_peak_quarter_load":
                    chosen["structural_peak_quarter_load"],
            }
        edge_id = chosen["edge_id"]
        used.add(edge_id)
        structural = by_edge[edge_id]
        demand_spec = DemandBuildSpec(
            start_date=demand[0], source="forecast", days=demand[1],
            purpose="closure_envelope" if demand[1] > 1 else "standard")
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
                "resolution_minutes": resolution,
                "permitted_daily_band": {
                    "earliest_start": band_times[0],
                    "latest_end": band_times[1],
                },
            },
            "strata": {
                "road_class": road_class,
                "sensor_distance_band": structural["sensor_distance_band"],
                "topology": structural["topology"],
                "duration": duration,
                "day_type": day_type,
            },
        })
        selection.append({
            "case_id": case_id,
            "edge_id": edge_id,
            "expected_discriminating": discriminating,
            "evidence": evidence,
            "structural": structural,
        })
        print(f"{case_id}: {edge_id} {structural['sensor_distance_band']}/"
              f"{structural['topology']} {evidence}")

    manifest_raw = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_manifest",
        "proxy_version": v2["proxy_version"],
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_before_outcomes": True,
        "minimum_cases": len(CASE_TEMPLATES),
        "minimum_spearman_case_fraction": 0.5,
        "minimum_ranking_case_fraction": 0.5,
        "gate": {
            "practical_equivalence_s": practical_equivalence_s,
            "practical_winner_recall_minimum": 0.90,
            "p90_normalized_shortlist_regret_maximum": 0.10,
            "failure_disqualification_recall_minimum": 0.60,
            "minimum_discriminating_case_fraction":
                MINIMUM_DISCRIMINATING_CASE_FRACTION,
            "discriminating_practical_winner_recall_minimum":
                DISCRIMINATING_PRACTICAL_WINNER_RECALL_MINIMUM,
        },
        "required_strata": v1["required_strata"],
        "cases": cases,
    }
    normalized = validate_validation_manifest(manifest_raw)
    v1_edges = {edge for case in v1["cases"] for edge in case["directed_edges"]}
    v2_edges = {edge for case in v2["cases"] for edge in case["directed_edges"]}
    if used & (v1_edges | v2_edges):
        raise SystemExit("v3 edges must be disjoint from v1 and v2")
    OUT.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    SELECTION_RECORD.write_text(json.dumps({
        "schema_version": 1,
        "kind": "heldout_v3_selection",
        "manifest_content_key": normalized["content_key"],
        "frozen_at": normalized["frozen_at"],
        "selection_inputs": input_hashes(),
        "pilots": [str(path) for path in PILOTS if path.is_file()],
        "cases": selection,
    }, indent=2, sort_keys=True) + "\n")
    total = sum(len(case["schedule_ids"]) for case in normalized["cases"])
    expected = sum(1 for row in selection if row["expected_discriminating"])
    print(f"\nfroze {OUT} content_key={normalized['content_key']}")
    print(f"{len(normalized['cases'])} cases, {total} exhaustive schedules, "
          f"{expected} pilot-confirmed discriminating, "
          f"practical_equivalence_s={practical_equivalence_s}")


if __name__ == "__main__":
    main()
