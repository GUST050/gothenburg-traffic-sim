"""Deterministic, process-free freeze of the v5 monthly held-out design.

Selection reads ONLY tracked pre-outcome inputs — `web/data/network.geojson`
for structure and the tracked v1-v4 manifests/selection for disjointness. It
never reads a campaign report, outcome or run root, so no outcome knowledge can
leak into which edges are held out.

Run:  python3 tools/freeze_heldout_v5.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NETWORK = ROOT / "web" / "data" / "network.geojson"
VALIDATION = ROOT / "validation"

CAMPAIGN = "v5"
FROZEN_AT = "2026-07-27"
CASES_REQUIRED = 5
SCHEDULES_PER_CASE = 15
# Physical independence: five IDs must be five independent road cases.
MIN_EDGE_LENGTH_M = 30.0        # reject pathological stubs

# Already-archived demand envelope; this task generates no demand.
DEMAND_BUILD_ID = "2ac04275daabe93c"

# v5 keeps v4's gate semantics EXACTLY: hardening adoption must not move the
# bar the evidence has to clear.
GATE = {
    "practical_equivalence_s": 300.0,
    "practical_winner_recall_minimum": 0.9,
    "p90_normalized_shortlist_regret_maximum": 0.1,
    "failure_disqualification_recall_minimum": 0.6,
    "minimum_discriminating_case_fraction": 0.4,
    "discriminating_practical_winner_recall_minimum": 0.9,
}


def canonical_key(payload) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prior_heldout_edges() -> set[str]:
    """Every directed edge used by any v1-v4 release or diagnostic held-out set."""
    edges: set[str] = set()
    for path in sorted(VALIDATION.glob("monthly_proxy_manifest*.json")):
        # Never let THIS campaign's own output feed back into its selection:
        # a re-freeze would otherwise exclude the edges it just chose and
        # silently pick a different set.
        if path.name == f"monthly_proxy_manifest_{CAMPAIGN}.json":
            continue
        manifest = json.loads(path.read_text())
        for case in manifest.get("cases", []):
            edges.update(case.get("directed_edges", []))
            spec = case.get("closure_search_spec") or {}
            edges.update(spec.get("directed_edges", []))
    selection = VALIDATION / "heldout_v4_selection.json"
    if selection.is_file():
        for entry in json.loads(selection.read_text()).get("selected_edges", []):
            if entry.get("edge_id"):
                edges.add(entry["edge_id"])
    return edges


def _endpoints(edge_id: str) -> tuple[str, str] | None:
    """Split a `u_v_k` directed edge id into its junction endpoints."""
    parts = str(edge_id).split("_")
    if len(parts) < 3:
        return None
    return parts[0], parts[1]


def candidate_pool(excluded: set[str]) -> list[dict]:
    """Structural candidates from the tracked network, pre-outcome only."""
    geo = json.loads(NETWORK.read_text())
    pool = []
    for feature in geo["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        props = feature["properties"]
        edge_id = props.get("id")
        highway = props.get("highway")
        dist = props.get("dist_sensor_m")
        length = props.get("length_m")
        if edge_id in excluded or highway not in ("secondary", "tertiary"):
            continue
        if dist is None or length is None or dist <= 750.0:
            continue                      # far_over_750m stratum only
        if float(length) < MIN_EDGE_LENGTH_M:
            continue                      # pathological stub, not a road case
        nodes = _endpoints(edge_id)
        if nodes is None:
            continue
        pool.append({
            "edge_id": edge_id,
            "road_class": highway,
            "dist_sensor_m": round(float(dist), 1),
            "length_m": round(float(length), 1),
            "u": nodes[0],
            "v": nodes[1],
        })
    # Deterministic order: no randomness, no outcome-derived ranking.
    pool.sort(key=lambda e: (e["road_class"], -e["dist_sensor_m"], e["edge_id"]))
    return pool


def select(pool: list[dict]) -> list[dict]:
    """Two secondary + three tertiary, strictly first-by-deterministic-order.

    Mirrors v4's shape (2 expected-discriminating + 3 controls) without reading
    any outcome: 'expected_discriminating' is a PRE-registered structural
    expectation, not a measurement.
    """
    def independent(candidates, want):
        """Greedy pick that keeps every case a DISTINCT physical segment.

        Rejects the reverse direction of an already-chosen segment and any edge
        sharing a junction with one (same corridor/intersection), so five ids
        are five independent road cases rather than one street counted twice.
        """
        picked, used_nodes = [], set()
        for edge in candidates:
            if len(picked) == want:
                break
            if edge["u"] in used_nodes or edge["v"] in used_nodes:
                continue                       # reverse or shared-junction
            picked.append(edge)
            used_nodes.update((edge["u"], edge["v"]))
        return picked, used_nodes

    secondary_pool = [e for e in pool if e["road_class"] == "secondary"]
    tertiary_pool = [e for e in pool if e["road_class"] == "tertiary"]
    secondary, used = independent(secondary_pool, 2)
    tertiary, _ = independent(
        [e for e in tertiary_pool
         if e["u"] not in used and e["v"] not in used], 3)
    # The tertiary pass must also stay internally independent.
    if len(secondary) < 2 or len(tertiary) < 3:
        raise SystemExit("insufficient physically independent candidates")
    chosen = []
    for i, edge in enumerate(secondary):
        chosen.append({**edge, "case_id": f"v5-discriminating-secondary-{'ab'[i]}",
                       "expected_discriminating": True})
    labels = ("near-tie", "failure", "control")
    for i, edge in enumerate(tertiary):
        chosen.append({**edge, "case_id": f"v5-control-tertiary-{labels[i]}",
                       "expected_discriminating": False})
    nodes = [n for c in chosen for n in (c["u"], c["v"])]
    if len(nodes) != len(set(nodes)):
        raise SystemExit("selected cases share a junction")
    return chosen


def build_spec(case_id: str, edge_id: str) -> dict:
    """The frozen closure-search spec for a case.

    Mirrors v4's frozen window/date/duration design exactly; only the case id
    and directed edge differ. The demand identity is the already-archived
    envelope — this task generates no demand.
    """
    from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: PLC0415

    spec = ClosureSearchSpec.from_dict({
        "search_id": case_id,
        "directed_edges": [edge_id],
        "demand_build_id": DEMAND_BUILD_ID,
        "permitted_date_start": "2027-07-15",
        "permitted_date_end": "2027-07-19",
        "required_work_minutes": 480,
        "max_consecutive_start_days": 1,
        "permitted_daily_band": {"earliest_start": "07:00",
                                 "latest_end": "15:30"},
        "source": "forecast",
        "timezone": "Europe/Stockholm",
        "dst_policy": "exclude_transition_dates",
        "allowed_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "blackout_dates": [],
        "same_daily_window": True,
        "resolution_minutes": 15,
        "closure_type": "full",
        "duration_basis": "required_work_time",
        "work_to_closure_assumption": "one_to_one",
        "objective_profile": "robust_time_loss",
        "policy_status": "user_supplied_unverified",
    })
    return spec


def build_schedules(spec) -> list[str]:
    """Canonical schedule ids from the PRODUCTION enumerator (deterministic)."""
    from traffic_sim.core.closure_calendar import (  # noqa: PLC0415
        generate_closure_schedules)

    ids = [s.schedule_id for s in generate_closure_schedules(spec)]
    if len(ids) != SCHEDULES_PER_CASE:
        raise SystemExit(
            f"expected {SCHEDULES_PER_CASE} canonical schedules, got {len(ids)}")
    return ids


def build_artifacts() -> dict[str, str]:
    """Return {relative_path: exact file text} WITHOUT touching the tree.

    Lets a test prove the freeze is reproducible without rewriting the frozen
    artifacts (which would make the check trivially self-fulfilling).
    """
    return _compose()


def _compose() -> dict[str, str]:
    excluded = prior_heldout_edges()
    chosen = select(candidate_pool(excluded))
    assert len({c["edge_id"] for c in chosen}) == CASES_REQUIRED
    assert not ({c["edge_id"] for c in chosen} & excluded)

    policy = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_policy",
        "campaign_version": CAMPAIGN,
        "shortlist_version": "stratified_shortlist_v3",
        "status": "design_frozen_pre_outcome",
        "thresholds": dict(GATE),
        "selection_rule": {
            "source": "tracked web/data/network.geojson structural attributes only",
            "road_class": ["secondary", "tertiary"],
            "sensor_distance_band": "far_over_750m",
            "order": "road_class, then descending dist_sensor_m, then edge_id",
            "take": {"secondary": 2, "tertiary": 3},
            "reads_outcomes": False,
            "reads_proxy_scores": False,
        },
        "identity": {
            "policy_content_key_algorithm":
                "sha256-canonical-selection-rule-and-thresholds",
            "source_identity": "monthly-proxy-v5-stratified-shortlist-v3",
            "outcome_identity": "untouched-v5-only",
            "gate_semantics": "identical to v4; adoption hardening changed no threshold",
        },
    }
    policy["content_key"] = canonical_key(policy)
    out = {"validation/monthly_proxy_policy_v5.json":
           json.dumps(policy, indent=2, sort_keys=True) + "\n"}

    selection = {
        "schema_version": 1,
        "kind": "monthly_heldout_selection",
        "campaign_version": CAMPAIGN,
        "status": "selection_frozen_pre_outcome",
        "selection_method": "deterministic_structural_pre_outcome",
        "selection_reads_outcomes": False,
        "pilot_selection_does_not_read_proxy": True,
        "practical_equivalence_s": GATE["practical_equivalence_s"],
        "selected_edge_count": CASES_REQUIRED,
        "physical_independence": {
            "rule": ("no reverse direction of a chosen segment and no edge "
                     "sharing a junction with one; minimum length "
                     f"{MIN_EDGE_LENGTH_M} m"),
            "min_edge_length_m": MIN_EDGE_LENGTH_M,
            "shared_junctions": 0,
            "reverse_pairs": 0,
        },
        "expected_discriminating_case_count":
            sum(1 for c in chosen if c["expected_discriminating"]),
        "disjointness": {
            "prior_heldout_edge_count": len(excluded),
            "prior_sources": sorted(
                p.name for p in VALIDATION.glob("monthly_proxy_manifest*.json")
                if p.name != f"monthly_proxy_manifest_{CAMPAIGN}.json")
                + ["heldout_v4_selection.json"],
            "intersection_with_v1_v4": [],
        },
        "selected_edges": [
            {"case_id": c["case_id"], "edge_id": c["edge_id"],
             "expected_discriminating": c["expected_discriminating"],
             "structural": {"road_class": c["road_class"],
                            "sensor_distance_band": "far_over_750m",
                            "dist_sensor_m": c["dist_sensor_m"],
                            "length_m": c["length_m"],
                            "junction_u": c["u"], "junction_v": c["v"]}}
            for c in chosen
        ],
        "source_fingerprints": {
            "web/data/network.geojson": sha256_file(NETWORK),
        },
    }
    selection["content_key"] = canonical_key(selection)
    out["validation/heldout_v5_selection.json"] = (
        json.dumps(selection, indent=2, sort_keys=True) + "\n")

    sources = [
        "traffic_sim/simulation/heldout_gate.py",
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/monthly_proxy.py",
        "traffic_sim/simulation/proxy_validation.py",
        "traffic_sim/core/closure_calendar.py",
        "run_monthly_proxy_validation.py",
        "validation/monthly_proxy_policy_v5.json",
        "validation/heldout_v5_selection.json",
    ]
    manifest = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_manifest",
        "campaign_version": CAMPAIGN,
        "frozen_at": FROZEN_AT,
        "status": "frozen_pre_outcome_design",
        "frozen_before_outcomes": True,
        "outcomes_present_at_freeze": False,
        "outcomes_path": None,
        "proxy_version": "monthly_proxy_v1",
        "shortlist_version": "stratified_shortlist_v3",
        "shortlist_policy_content_key": json.loads(
            (VALIDATION / "monthly_proxy_manifest_v4.json").read_text()
        )["shortlist_policy_content_key"],
        "policy_content_key": policy["content_key"],
        "selection_content_key": selection["content_key"],
        "source_identity": "monthly-proxy-v5-stratified-shortlist-v3",
        "minimum_cases": CASES_REQUIRED,
        "minimum_ranking_case_fraction": 0.5,
        "minimum_spearman_case_fraction": 0.5,
        "required_strata": {
            "day_type": ["mixed_weekday_weekend"],
            "duration": ["single_8h"],
            "road_class": ["secondary", "tertiary"],
            "sensor_distance_band": ["far_over_750m"],
            "topology": ["structural_pre_outcome"],
        },
        "gate": dict(GATE),
        "cases": [
            {
                "case_id": c["case_id"],
                "directed_edges": [c["edge_id"]],
                "expected_discriminating": c["expected_discriminating"],
                "strata": {
                    "day_type": "mixed_weekday_weekend",
                    "duration": "single_8h",
                    "road_class": c["road_class"],
                    "sensor_distance_band": "far_over_750m",
                    "topology": "structural_pre_outcome",
                },
                "closure_search_spec": build_spec(
                    c["case_id"], c["edge_id"]).to_dict(),
                "schedule_ids": build_schedules(
                    build_spec(c["case_id"], c["edge_id"])),
            }
            for c in chosen
        ],
        "source_fingerprints": {s: sha256_file(ROOT / s) for s in sources},
    }
    # The manifest key MUST come from the production validator, which hashes a
    # normalized field subset — not this module's generic canonical key.
    from traffic_sim.simulation.proxy_validation import (  # noqa: PLC0415
        validate_validation_manifest, validation_manifest_content_key)
    manifest["content_key"] = validation_manifest_content_key(manifest)
    validate_validation_manifest(manifest)
    out["validation/monthly_proxy_manifest_v5.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out


def main() -> int:
    out = _compose()
    for relative, text in out.items():
        (ROOT / relative).write_text(text)
    policy = json.loads(out["validation/monthly_proxy_policy_v5.json"])
    selection = json.loads(out["validation/heldout_v5_selection.json"])
    manifest = json.loads(out["validation/monthly_proxy_manifest_v5.json"])
    print("policy   :", policy["content_key"])
    print("selection:", selection["content_key"])
    print("manifest :", manifest["content_key"])
    print("cases    :", len(manifest["cases"]),
          "| schedules:", sum(len(c["schedule_ids"]) for c in manifest["cases"]))
    print("disjoint from",
          selection["disjointness"]["prior_heldout_edge_count"],
          "prior held-out edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
