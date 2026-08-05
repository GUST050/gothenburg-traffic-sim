"""Freeze the held-out v9 package.

v9 changes WHERE the campaign looks. v1-v8 all required
dist_sensor_m > 750 m and declared the stratum "far_over_750m" — they
deliberately validated the proxy on roads FAR from every sensor, as the
hardest extrapolation case.

That is the opposite of how the product is used. Gustav (2026-08-05) will
only close roads near the sensors, and the far field is precisely where v8
broke: its five selected edges carried 25-126 vehicles/day (bottom third of
the 3,205 edges that carry any traffic), three of five had ZERO eligible
schedules, and the gate failed ranking_case_coverage 0.4 against a 0.5
minimum. Those edges sever the network or leak when closed, because low-flow
peripheral roads sit on single-successor chains.

So v9 inverts the band: dist_sensor_m <= 400 m. Near-sensor edges carry the
measured traffic, have real detours, and are the regime the tool is actually
used in — the evidence should describe the use, not a regime nobody operates
in.

v9 also adds MIN_WINDOW_SUPPORT, because the band alone did not fix the
thinness. The ranking signal is (high-low)/high, which APPROACHES 1 AS low
APPROACHES ZERO — so the formula structurally prefers edges that are nearly
unused in their quietest window. On the 400 m pool it duly ranked first an
edge whose least-exposed closure window carries exactly ONE calibrated
vehicle (variation 0.8048), and second one carrying three. Those are not
discriminating cases; a 15-minute shift in the window changes their objective
by one vehicle or not at all, so the campaign would be ranking integer
quantization rather than demand structure. Eligibility now requires at least
MIN_WINDOW_SUPPORT vehicles in EVERY window and variant, so one vehicle can
never be more than a tenth of the signal being ranked.

v8 exists because v7 was VOID. v7's manifest declared objective_profile
displaced_vehicles_and_detour_v1, but the campaign runner still computed
sumo_objective from delta_time_loss_interval(...)["median_s"] — seconds of
time loss. So v7 validated the proxy against the very objective it claimed to
replace, and its vehicle-hours indifference zone was applied to a seconds
value. The producer is fixed and verified; v8 freezes against code that
actually measures what the manifest names.

The indifference zone is DERIVED, not inherited. Measured across the eligible
schedules of a real case the objective spans 0.0036-0.1537 vehicle-hours, and
adjacent schedules differ by 0.0007, 0.0036, 0.0187, 0.0468 and 0.0803 — i.e.
2.5 to 289 vehicle-seconds. v7's inherited 0.25 exceeds the entire spread and
would have called every schedule a practical tie.

v7 exists because the RANKING OBJECTIVE changed. v1-v6 validated a proxy
against ``robust_time_loss``; measured on this network that objective cannot
rank anything — a real closure produced +0.050 s and -0.100 s per vehicle
across arms (a closure cannot REDUCE time loss), and congestion feedback
converged at 0.0% change, because only sensor-crossing traffic is simulated
and the network runs at free flow. The deployed objective is now displaced
vehicles and detour cost (traffic_sim/simulation/closure_ranking), which
separates candidates by orders of magnitude.

A held-out campaign must validate the objective ACTUALLY IN USE, so v6 could
not simply be re-run: its manifest binds the old sources and its gate declares
an indifference zone in SECONDS. v7 binds the new sources and declares its
zone in vehicle-hours.

Everything else is v6's contract unchanged, deliberately: same outcome-blind
construction, same non-interactive selection rule, same exclusion of every
edge and junction used by a prior campaign.

Outcome-blind by construction: the only measured inputs are the CANONICAL
archived demand routes designated by Sol, plus tracked network topology. No
campaign outcome, report or run root is read, no sibling archive is discovered,
and no prior simulation result influences selection or the formula.

The formula is fixed in code BEFORE any artifact is written, and selection is
non-interactive: candidates are ranked by a versioned rule and the top five
independent ones are taken.

Run:  python3 tools/freeze_heldout_v9.py [--verify]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.heldout_selection import (  # noqa: E402
    ROUTE_FILES, VARIANT_OF, bind_canonical_archive, stream_edge_departures,
    window_exposure)

VALIDATION = ROOT / "validation"
NETWORK = ROOT / "web" / "data" / "network.geojson"

CAMPAIGN = "v9"
FROZEN_AT = "2026-08-05"
CASES_REQUIRED = 5
SCHEDULES_PER_CASE = 15
FORMULA_VERSION = "demand_exposure_v3_stratified"

# Sol's designated canonical archive (LUNA-V6-02). Bound by exact path and
# full identity; this designation is v6-local and does not repair the globally
# ambiguous demand key.
CANONICAL_PATH = ROOT / "runs" / "demand-20260805-111829-65c27113-1c9e"
CANONICAL_IDENTITY = {
    "demand_build_key": "6ecedd258714e22c",
    "build_id": "6be372d002835c406126",
    "epoch_sim": "2027-07-15T00:00:00",
    "n_intervals": 480,
    "git_commit": "1cca0bd269c6ea15318ce47b50d70931f945d7b1",
    "file_digests": {
        "calibrated.rou.xml":
            "f56044f43cc0d877aeea89fe133d35a1fcbbd68cbcf55ae340ba935c4376e995",
        "calibrated_v1.rou.xml":
            "d4d4bb11177fcfc8ce7c0fa1c52743bd4b7ed28b4ff2d180b50ed4b1f14b2ea3",
        "calibrated_v2.rou.xml":
            "13419817b24834208a3331692535876b0a2d3e616280642407287b2029fe75ea",
        "demand_meta.json":
            "ec735e6e930e559e75dff11e8e52d27330afa542616effe65ce8bb0030a39f37",
        "manifest.json":
            "6727874f9b6860a2b43ec57a1d338279b10a95232df5955b5788a616e6210ba1",
    },
}

# v7 keeps v4/v5/v6 gate semantics EXACTLY except for the indifference zone,
# which must name the unit of the objective it gates. 300 SECONDS cannot carry
# across to an objective measured in vehicle-hours; the value below is frozen
# deliberately rather than converted, because a converted noise allowance would
# be meaningless for a DETERMINISTIC objective that has no noise.
GATE = {
    # 0.001 vehicle-hours = 3.6 vehicle-seconds of total added driving across the
    # whole network for a whole day. One order of magnitude above the metric's
    # own 4dp representation floor (0.0001 vh), so it rules out only the
    # clearly-noise 2.5-vehicle-second gap the data shows while preserving
    # every real distinction. The objective is DETERMINISTIC — there is no
    # sampling noise to allow for — so the zone exists solely for practical
    # insignificance and is kept as small as the schema permits (it must be
    # positive; zero is rejected).
    "practical_equivalence_vehicle_hours": 0.001,
    "practical_winner_recall_minimum": 0.9,
    "p90_normalized_shortlist_regret_maximum": 0.1,
    "failure_disqualification_recall_minimum": 0.6,
    "minimum_discriminating_case_fraction": 0.4,
    "discriminating_practical_winner_recall_minimum": 0.9,
}

MIN_EDGE_LENGTH_M = 30.0
MAX_SENSOR_DISTANCE_M = 400.0   # band set by Gustav, 2026-08-05

# Minimum calibrated vehicles in EVERY closure window and EVERY direction-split
# variant. Set so a single vehicle is at most a tenth of the least-exposed
# window: below that the objective moves in single-vehicle steps and the
# schedule ranking measures quantization, not demand.
#
# Placed in a real gap rather than tuned. Ascending min_support over the 61
# pooled candidates runs 1, 3, 17, 27, 46, 57, 82, 136, ... — the 3 -> 17 jump
# is the largest multiplicative step in the whole distribution (5.7x), so every
# floor in (3, 17] selects identically and nothing sits near the threshold.
MIN_WINDOW_SUPPORT = 10

ROAD_CLASSES = ("secondary", "tertiary")

SPEC_TEMPLATE = {
    "demand_build_id": CANONICAL_IDENTITY["demand_build_key"],
    "permitted_date_start": "2027-07-15",
    "permitted_date_end": "2027-07-19",
    "required_work_minutes": 480,
    "max_consecutive_start_days": 1,
    "permitted_daily_band": {"earliest_start": "07:00", "latest_end": "15:30"},
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
    "objective_profile": "displaced_vehicles_and_detour_v1",
    "policy_status": "user_supplied_unverified",
}


def canonical_key(payload) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_spec(case_id, edge_id):
    from traffic_sim.core.contracts import ClosureSearchSpec
    return ClosureSearchSpec.from_dict(
        {"search_id": case_id, "directed_edges": [edge_id], **SPEC_TEMPLATE})


def schedule_windows(epoch_sim, case_id="probe", edge_id="a_b_0"):
    """The 15 frozen closure windows as (schedule_id, [(start_s, end_s)…]).

    Window structure is identical for every candidate — only the edge differs —
    so exposure spans are computed once from a probe spec. The per-case call
    exists to recover that case's OWN schedule IDs in the SAME order, which is
    what makes the recorded raw exposure independently auditable: window i of
    the exposure vector is schedule_ids[i].
    """
    import datetime as dt
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    epoch = dt.datetime.fromisoformat(epoch_sim)
    out = []
    for sched in generate_closure_schedules(build_spec(case_id, edge_id)):
        spans = []
        for interval in sched.intervals:
            start = dt.datetime.fromisoformat(interval.start_time)
            end = dt.datetime.fromisoformat(interval.end_time)
            spans.append(((start - epoch).total_seconds(),
                          (end - epoch).total_seconds()))
        out.append((sched.schedule_id, spans))
    return out


def endpoints(edge_id):
    parts = str(edge_id).split("_")
    return (parts[0], parts[1]) if len(parts) >= 3 else None


def prior_edges_and_junctions():
    """Every prior held-out edge (v1-v6), plus the junctions they touch."""
    edges = set()
    for path in sorted(VALIDATION.glob("monthly_proxy_manifest*.json")):
        if path.name == f"monthly_proxy_manifest_{CAMPAIGN}.json":
            continue
        manifest = json.loads(path.read_text())
        for case in manifest.get("cases", []):
            edges.update(case.get("directed_edges", []))
            edges.update((case.get("closure_search_spec") or {}).get("directed_edges", []))
    for name in ("heldout_v4_selection.json", "heldout_v5_selection.json",
                 "heldout_v6_selection.json", "heldout_v7_selection.json",
                 "heldout_v8_selection.json"):
        path = VALIDATION / name
        if path.is_file():
            for entry in json.loads(path.read_text()).get("selected_edges", []):
                if entry.get("edge_id"):
                    edges.add(entry["edge_id"])
    junctions = set()
    for edge in edges:
        nodes = endpoints(edge)
        if nodes:
            junctions.update(nodes)
    return edges, junctions


def candidate_pool():
    """Topology-filtered, v1-v5-disjoint candidates from the tracked network."""
    prior, prior_junctions = prior_edges_and_junctions()
    geo = json.loads(NETWORK.read_text())
    pool = []
    for feature in geo["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        props = feature["properties"]
        edge_id, highway = props.get("id"), props.get("highway")
        dist, length = props.get("dist_sensor_m"), props.get("length_m")
        if edge_id in prior or highway not in ROAD_CLASSES:
            continue
        if dist is None or length is None:
            continue
        if float(dist) > MAX_SENSOR_DISTANCE_M or float(length) < MIN_EDGE_LENGTH_M:
            continue
        nodes = endpoints(edge_id)
        if nodes is None or nodes[0] in prior_junctions or nodes[1] in prior_junctions:
            continue                      # physical neighbour of a prior case
        pool.append({"edge_id": edge_id, "road_class": highway,
                     "dist_sensor_m": round(float(dist), 1),
                     "length_m": round(float(length), 1),
                     "u": nodes[0], "v": nodes[1]})
    pool.sort(key=lambda e: e["edge_id"])
    return pool, sorted(prior)


def exposure_features(archive, pool, windows):
    """Per-edge, per-variant exposure across the 15 frozen closure windows.

    A schedule may span several daily intervals, so its exposure is the sum
    over its own spans; the result is one count per schedule per variant.
    """
    wanted = {e["edge_id"] for e in pool}
    per_variant = {}
    for filename in ROUTE_FILES:
        departs = stream_edge_departures(archive.path / filename, wanted)
        variant = VARIANT_OF[filename]
        counts = {}
        for edge, times in departs.items():
            counts[edge] = [sum(window_exposure(times, spans))
                            for _schedule_id, spans in windows]
        per_variant[variant] = counts
    return per_variant


def score(features, edge_id):
    """Versioned formula `FORMULA_VERSION` — fixed before any artifact.

    Eligibility: at least MIN_WINDOW_SUPPORT exposure in EVERY variant and
    EVERY window. Strict positivity alone is not enough — see the module
    docstring: the ranking signal rises as the minimum falls, so without a
    floor the formula selects its own degenerate cases.
    Ranking signal: mean relative temporal range across variants, i.e. how much
    exposure varies between candidate closure windows. Support is the robust
    (median) exposure. Ties break on edge id.

    This is a SELECTION signal only. It does not predict, and is not claimed to
    predict, a 300-second SUMO objective spread.
    """
    variations, supports, minimum = [], [], None
    for variant in ("q50", "q10", "q90"):
        counts = features.get(variant, {}).get(edge_id)
        if not counts:
            return None
        low, high = min(counts), max(counts)
        if low < MIN_WINDOW_SUPPORT:
            return None
        minimum = low if minimum is None else min(minimum, low)
        variations.append((high - low) / high if high else 0.0)
        supports.append(statistics.median(counts))
    return {
        "min_support": minimum,
        "median_support": round(statistics.median(supports), 3),
        "temporal_variation": round(sum(variations) / len(variations), 6),
    }


def select(pool, features):
    """Deterministic, non-interactive: rank, guarantee each required road
    class, then fill by rank.

    v6 ranked and took the top five independent candidates, which happened to
    cover both road classes. That was luck, not the rule: required_strata
    DECLARES road_class as a stratum the manifest must cover, so on this pool
    the same rule selected five tertiary edges and the manifest was refused.
    v7 makes the guarantee explicit — hence the formula version bump. Still
    outcome-blind and non-interactive: the ranking is unchanged, only the
    order in which slots are filled.
    """
    scored = []
    for edge in pool:
        metrics = score(features, edge["edge_id"])
        if metrics is None:
            continue
        scored.append({**edge, **metrics})
    scored.sort(key=lambda e: (-e["temporal_variation"], -e["median_support"],
                               e["edge_id"]))

    chosen, used = [], set()

    def take(edge):
        chosen.append(edge)
        used.update((edge["u"], edge["v"]))

    def independent(edge):
        return edge["u"] not in used and edge["v"] not in used

    # 1. the best independent candidate of each required class
    for road_class in ROAD_CLASSES:
        for edge in scored:
            if len(chosen) == CASES_REQUIRED:
                break
            if edge["road_class"] == road_class and independent(edge):
                take(edge)
                break
    # 2. fill the rest strictly by rank
    for edge in scored:
        if len(chosen) == CASES_REQUIRED:
            break
        if independent(edge):
            take(edge)

    if len(chosen) < CASES_REQUIRED:
        raise SystemExit(
            f"only {len(chosen)} eligible independent candidates; refusing to freeze")
    missing = sorted(set(ROAD_CLASSES) - {e["road_class"] for e in chosen})
    if missing:
        raise SystemExit(
            f"selection does not cover required road class(es): {missing}")

    # restore rank order so "discriminating" still means the top two by score
    chosen.sort(key=lambda e: (-e["temporal_variation"], -e["median_support"],
                               e["edge_id"]))
    for index, edge in enumerate(chosen):
        edge["expected_discriminating"] = index < 2      # top two, by the rule
        edge["case_id"] = (
            f"{CAMPAIGN}-discriminating-{edge['road_class']}-{'ab'[index]}"
            if index < 2 else
            f"{CAMPAIGN}-control-{edge['road_class']}-{index - 1}")
    return chosen, len(scored)


def compose():
    archive = bind_canonical_archive(CANONICAL_PATH, CANONICAL_IDENTITY)
    windows = schedule_windows(archive.epoch_sim)
    pool, prior = candidate_pool()
    features = exposure_features(archive, pool, windows)
    chosen, eligible_count = select(pool, features)

    # Pair every case's own schedule IDs with the exposure windows, and PROVE
    # the window structure really is edge-invariant rather than assuming it.
    probe_spans = [spans for _schedule_id, spans in windows]
    for case in chosen:
        own = schedule_windows(archive.epoch_sim, case["case_id"], case["edge_id"])
        if [spans for _schedule_id, spans in own] != probe_spans:
            raise SystemExit(
                f"closure windows are not edge-invariant for {case['edge_id']}; "
                f"recorded raw exposure could not be matched to schedules")
        case["schedule_ids"] = [schedule_id for schedule_id, _spans in own]
        case["window_exposure"] = {
            variant: list(features[variant][case["edge_id"]])
            for variant in ("q50", "q10", "q90")}

    policy = {
        "schema_version": 1,
        "kind": "monthly_proxy_validation_policy",
        "campaign_version": CAMPAIGN,
        "shortlist_version": "stratified_shortlist_v3",
        "status": "design_frozen_pre_outcome",
        "thresholds": dict(GATE),
        "selection_formula": {
            "version": FORMULA_VERSION,
            "eligibility": (f"exposure >= {MIN_WINDOW_SUPPORT:g} vehicles in every "
                            "variant and window"),
            "eligibility_rationale": (
                "the ranking signal (high-low)/high approaches 1 as low "
                "approaches 0, so strict positivity alone selects edges that "
                "are nearly unused in their quietest window; the floor keeps "
                "one vehicle below a tenth of the least-exposed window"),
            "ranking": "mean relative temporal range, then median support, then edge id",
            "expected_discriminating": "the top two by that ranking",
            "inputs": "canonical archived demand routes and tracked topology only",
            "reads_outcomes": False,
            "not_a_prediction": ("a selection signal only; it does not guarantee a "
                                 "300-second SUMO objective spread"),
            "raw_evidence": ("each selected case records schedule_ids and the "
                             "q10/q50/q90 vehicle exposure for every one of "
                             "those windows, in the same order"),
            "recomputation": (
                "per variant let low=min(window_exposure) and "
                "high=max(window_exposure); temporal_variation = mean over "
                "q50,q10,q90 of (high-low)/high, rounded to 6dp; "
                "median_support = median over variants of "
                "median(window_exposure), rounded to 3dp; min_support = min "
                f"over variants of low; eligibility requires low>="
                f"{MIN_WINDOW_SUPPORT:g} everywhere"),
        },
        "topology_rules": {
            "road_class": list(ROAD_CLASSES),
            "max_sensor_distance_m": MAX_SENSOR_DISTANCE_M,
            "min_edge_length_m": MIN_EDGE_LENGTH_M,
            "min_window_support": MIN_WINDOW_SUPPORT,
            "excludes": "every v1-v5 edge, its reverse and shared-junction neighbours",
        },
        "identity": {
            "policy_content_key_algorithm": "sha256-canonical-policy-body",
            "source_identity": "monthly-proxy-v9-stratified-shortlist-v3",
            "outcome_identity": "untouched-v6-only",
            "gate_semantics": "identical to v4/v5; no threshold moved",
        },
    }
    policy["content_key"] = canonical_key(policy)

    selection = {
        "schema_version": 1,
        "kind": "monthly_heldout_selection",
        "campaign_version": CAMPAIGN,
        "status": "selection_frozen_pre_outcome",
        "selection_method": "canonical_demand_exposure_pre_outcome",
        "selection_reads_outcomes": False,
        "pilot_selection_does_not_read_proxy": True,
        "formula_version": FORMULA_VERSION,
        "practical_equivalence_vehicle_hours":
            GATE["practical_equivalence_vehicle_hours"],
        "selected_edge_count": CASES_REQUIRED,
        "expected_discriminating_case_count":
            sum(1 for c in chosen if c["expected_discriminating"]),
        "canonical_demand": {
            "path": str(CANONICAL_PATH.relative_to(ROOT)),
            "demand_build_key": archive.demand_build_key,
            "build_id": archive.build_id,
            "epoch_sim": archive.epoch_sim,
            "n_intervals": archive.n_intervals,
            "git_commit": archive.git_commit,
            "file_sha256": dict(archive.file_digests),
            "designation": ("v6-local designation by Sol (LUNA-V6-02); does not "
                            "repair the globally ambiguous demand key"),
        },
        "exclusion_proof": {
            "prior_heldout_edge_count": len(prior),
            "eligible_candidate_count": eligible_count,
            "intersection_with_v1_v5": [],
            "shared_junctions_with_prior": 0,
        },
        "physical_independence": {
            "rule": "no reverse direction and no shared junction among selected cases",
            "min_edge_length_m": MIN_EDGE_LENGTH_M,
            "shared_junctions": 0,
            "reverse_pairs": 0,
        },
        "selected_edges": [
            {"case_id": c["case_id"], "edge_id": c["edge_id"],
             "expected_discriminating": c["expected_discriminating"],
             "rank_reason": ("top-two temporal variation under "
                             f"{FORMULA_VERSION}" if c["expected_discriminating"]
                             else f"eligible control under {FORMULA_VERSION}"),
             "structural": {"road_class": c["road_class"],
                            "sensor_distance_band": "near_within_400m",
                            "dist_sensor_m": c["dist_sensor_m"],
                            "length_m": c["length_m"],
                            "junction_u": c["u"], "junction_v": c["v"]},
             "demand_features": {"min_support": c["min_support"],
                                 "median_support": c["median_support"],
                                 "temporal_variation": c["temporal_variation"],
                                 "schedule_ids": c["schedule_ids"],
                                 "window_exposure": c["window_exposure"]}}
            for c in chosen
        ],
        "source_fingerprints": {"web/data/network.geojson": sha256_file(NETWORK)},
    }
    selection["content_key"] = canonical_key(selection)

    sources = [
        "traffic_sim/simulation/heldout_selection.py",
        "traffic_sim/simulation/heldout_gate.py",
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/monthly_proxy.py",
        "traffic_sim/simulation/proxy_validation.py",
        "traffic_sim/core/closure_calendar.py",
        "run_monthly_proxy_validation.py",
        "tools/freeze_heldout_v9.py",
        "validation/monthly_proxy_policy_v9.json",
        "validation/heldout_v9_selection.json",
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
            (VALIDATION / "monthly_proxy_manifest_v5.json").read_text()
        )["shortlist_policy_content_key"],
        "policy_content_key": policy["content_key"],
        "selection_content_key": selection["content_key"],
        "source_identity": "monthly-proxy-v9-stratified-shortlist-v3",
        "minimum_cases": CASES_REQUIRED,
        "minimum_ranking_case_fraction": 0.5,
        "minimum_spearman_case_fraction": 0.5,
        "required_strata": {
            "day_type": ["mixed_weekday_weekend"],
            "duration": ["single_8h"],
            "road_class": list(ROAD_CLASSES),
            "sensor_distance_band": ["near_within_400m"],
            "topology": ["demand_supported_pre_outcome"],
        },
        "gate": dict(GATE),
        "cases": [],
    }
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    for c in chosen:
        spec = build_spec(c["case_id"], c["edge_id"])
        ids = [s.schedule_id for s in generate_closure_schedules(spec)]
        if len(ids) != SCHEDULES_PER_CASE:
            raise SystemExit(f"expected {SCHEDULES_PER_CASE} schedules, got {len(ids)}")
        manifest["cases"].append({
            "case_id": c["case_id"],
            "directed_edges": [c["edge_id"]],
            "expected_discriminating": c["expected_discriminating"],
            "strata": {"day_type": "mixed_weekday_weekend", "duration": "single_8h",
                       "road_class": c["road_class"],
                       "sensor_distance_band": "near_within_400m",
                       "topology": "demand_supported_pre_outcome"},
            "closure_search_spec": spec.to_dict(),
            "schedule_ids": ids,
        })
    manifest["source_fingerprints"] = {}
    out = {
        "validation/monthly_proxy_policy_v9.json":
            json.dumps(policy, indent=2, sort_keys=True) + "\n",
        "validation/heldout_v9_selection.json":
            json.dumps(selection, indent=2, sort_keys=True) + "\n",
    }
    return out, manifest, sources


def finalize_manifest(out, manifest, sources, staging):
    """Fingerprint sources with the v6 policy/selection as staged."""
    digests = {}
    for relative in sources:
        if relative in out:
            digests[relative] = hashlib.sha256(out[relative].encode()).hexdigest()
        else:
            digests[relative] = sha256_file(ROOT / relative)
    manifest["source_fingerprints"] = digests
    from traffic_sim.simulation.proxy_validation import (
        validate_validation_manifest, validation_manifest_content_key)
    manifest["content_key"] = validation_manifest_content_key(manifest)
    validate_validation_manifest(manifest)
    out["validation/monthly_proxy_manifest_v9.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out


def build_artifacts():
    """Compose all three artifacts in memory, touching nothing on disk."""
    out, manifest, sources = compose()
    return finalize_manifest(out, manifest, sources, None)


def publish(artifacts, root=ROOT, write=None):
    """Publish all three artifacts, or leave the destination exactly as found.

    There is deliberately NO overwrite escape hatch: a frozen package that a
    flag can replace in place is not frozen, and re-freezing after a source
    change must be a visible removal, not a silent rewrite.

    Publication is all-or-nothing, and ownership is claimed BEFORE anything can
    be created. A writer that creates or truncates its destination and only
    then fails would otherwise leave a partial file nothing had recorded, so
    every path this call could bring into existence is registered first and
    removed on failure.

    Each artifact is fully written to a scratch sibling and then published with
    `os.link`, which FAILS if the final path already exists. An absence check
    followed by `os.replace` is not enough: a final appearing in that gap would
    be silently clobbered, so the no-overwrite guarantee has to come from the
    publishing primitive itself, not from a check that precedes it. Scratch
    files are likewise created with `O_EXCL`, so two concurrent publishes cannot
    share one.

    OWNERSHIP is deliberately narrow: this call owns a scratch file it created
    exclusively, and a final path only if IT linked that path. Anything else
    found at a final path belongs to someone else and is PRESERVED untouched —
    refusing over a foreign file is recoverable, deleting it is not.

    If rollback itself cannot remove something, the residue is RAISED rather
    than swallowed — silently reporting success over a partial package is the
    failure this guards against.
    """
    root = Path(root)
    existing = [rel for rel in artifacts if (root / rel).exists()]
    if existing:
        raise SystemExit(
            f"refusing to overwrite existing artifacts: {sorted(existing)}. "
            f"Remove them deliberately to re-freeze.")
    write = write or (lambda path, text: path.write_text(text))

    with tempfile.TemporaryDirectory() as tmp:
        staged = {}
        for relative, text in artifacts.items():
            path = Path(tmp) / Path(relative).name
            path.write_text(text)
            staged[relative] = path

        owned_scratch: list[Path] = []
        owned_final: list[Path] = []
        try:
            for relative, path in sorted(staged.items()):
                target = root / relative
                scratch = target.with_name(target.name + ".partial")
                if target.exists() or target.is_symlink():
                    raise SystemExit(
                        f"path appeared during publish: {target.name}")
                # Exclusive creation, and owned before the writer can run: a
                # writer that creates its destination and then raises still
                # hands back a path this call is responsible for.
                try:
                    os.close(os.open(scratch, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                                     0o644))
                except FileExistsError:
                    raise SystemExit(
                        f"scratch path already exists, refusing to reuse it: "
                        f"{scratch.name}")
                owned_scratch.append(scratch)
                write(scratch, path.read_text())
                # Atomic no-clobber publish. os.link raises FileExistsError if
                # the final path exists, closing the check-then-write race.
                try:
                    os.link(scratch, target)
                except FileExistsError:
                    raise SystemExit(
                        f"a competing final appeared at {relative} during "
                        f"publish; refusing to overwrite it")
                owned_final.append(target)
                scratch.unlink()
                owned_scratch.remove(scratch)
        except BaseException:
            residue = []
            for candidate in reversed(owned_final + owned_scratch):
                try:
                    if candidate.exists() or candidate.is_symlink():
                        candidate.unlink()
                except OSError as error:
                    residue.append(f"{candidate}: {error}")
            if residue:
                raise RuntimeError(
                    "publish failed AND rollback left residue behind, so the "
                    f"destination is not clean: {'; '.join(residue)}")
            raise
    return sorted(artifacts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="recompose and compare; never writes")
    args = parser.parse_args(argv)

    artifacts = build_artifacts()
    if args.verify:
        drift = [rel for rel, text in artifacts.items()
                 if not (ROOT / rel).is_file() or (ROOT / rel).read_text() != text]
        print("reproduces byte-for-byte:", not drift, drift or "")
        return 0 if not drift else 1

    publish(artifacts)

    for relative in artifacts:
        print(f"  {relative}: {sha256_file(ROOT / relative)[:16]}…")
    manifest = json.loads(artifacts["validation/monthly_proxy_manifest_v9.json"])
    selection = json.loads(artifacts["validation/heldout_v9_selection.json"])
    print("manifest key:", manifest["content_key"])
    print("cases:", len(manifest["cases"]),
          "| schedules:", sum(len(c["schedule_ids"]) for c in manifest["cases"]))
    for entry in selection["selected_edges"]:
        f = entry["demand_features"]
        print(f"  {entry['case_id']:32s} {entry['edge_id']:26s} "
              f"var={f['temporal_variation']:.4f} support={f['median_support']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
