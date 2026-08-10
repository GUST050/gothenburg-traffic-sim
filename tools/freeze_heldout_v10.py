"""Freeze the held-out v10 package.

Stage 4 of `docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md`.

v10 IS v9 PLUS ONE CONDITION.  v9's evidence quality is the best this project
has produced — ``median_spearman`` +0.945 against −0.976 on the v3-era set, and
six of seven gate checks passed.  It failed exactly one, ``ranking_case_fraction``
0.4 against a 0.5 floor, because three of its five cases produced zero eligible
schedules.  So the shortest path to a passing campaign is to keep every part of
v9 that worked and fix only case selection.

THE ADDED CONDITION: **the edge must survive its own closure**.  v9 proved its
candidates carried demand; nothing asked whether closing them strands anyone.
`traffic_sim/simulation/closure_survivability` answers that from the tracked
network and the same canonical archive v9 bound, before any SUMO run:

  * no vehicle crossing the edge loses its destination once the edge is gone,
    measured from the edge immediately BEFORE the closure on that vehicle's own
    route — where SUMO's rerouter re-plans from; and
  * no successor edge is left with the closed edge as its only way in.

Denied departures (routes STARTING on the edge) are recorded and deliberately
do NOT disqualify.  Every street with departures has some, so gating on them
would refuse to close any real street — the mistake C1 fixed on 2026-08-06.

WHAT IS DELIBERATELY UNCHANGED FROM v9: the canonical archive and its full
identity, the 400 m band, ``MIN_WINDOW_SUPPORT``, the ranking signal, the
road-class stratum guarantee, the gate thresholds, the indifference zone, the
outcome-blind construction and the exclusion of every prior campaign's edges
and junctions.  A campaign that changed two things at once could not attribute
its result to either.

WHY THE SIMULATION SOURCES ARE BOUND TOO.  Stage 3 changed what a closure run
does: `traffic_sim/simulation/closure_teleport` disables SUMO's stuck-vehicle
relocation on the closure arm, which is what removes the only observed route to
traffic on a closed edge.  A v10 frozen against that behaviour must not be
replayable against a tree where it has changed, so both new modules are in the
fingerprinted source set.

Outcome-blind by construction: the only measured inputs are the canonical
archived demand routes and tracked network topology.  No campaign outcome,
report or run root is read, no sibling archive is discovered, and no prior
simulation result influences selection or the formula.

Run:  python3 tools/freeze_heldout_v10.py [--verify] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.closure_survivability import (  # noqa: E402
    RULE_VERSION as SURVIVABILITY_RULE_VERSION, evaluate_pool)
from traffic_sim.simulation.heldout_selection import (  # noqa: E402
    ROUTE_FILES, VARIANT_OF, bind_canonical_archive, stream_edge_departures,
    window_exposure)
from tools.freeze_heldout_v9 import (  # noqa: E402
    CANONICAL_IDENTITY, CANONICAL_PATH, GATE, MAX_SENSOR_DISTANCE_M,
    MIN_EDGE_LENGTH_M, MIN_WINDOW_SUPPORT, ROAD_CLASSES, SPEC_TEMPLATE,
    canonical_key, endpoints, publish, sha256_file)

VALIDATION = ROOT / "validation"
NETWORK = ROOT / "web" / "data" / "network.geojson"

CAMPAIGN = "v10"
FROZEN_AT = "2026-08-10"
CASES_REQUIRED = 5
SCHEDULES_PER_CASE = 15
FORMULA_VERSION = "demand_exposure_v4_survivable"

#: Stage 4's gate: enough surviving cases that ranking_case_fraction >= 0.5 is
#: ACHIEVABLE before a single SUMO run. Five are selected and the condition
#: applies to all of them, so this is a guard against a future relaxation
#: quietly re-freezing a set that cannot clear the check it exists to clear.
MIN_SURVIVING_CASES = 4


def build_spec(case_id, edge_id):
    from traffic_sim.core.contracts import ClosureSearchSpec
    return ClosureSearchSpec.from_dict(
        {"search_id": case_id, "directed_edges": [edge_id], **SPEC_TEMPLATE})


def schedule_windows(epoch_sim, case_id="probe", edge_id="a_b_0"):
    """The 15 frozen closure windows as (schedule_id, [(start_s, end_s)…])."""
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


def prior_edges_and_junctions():
    """Every prior held-out edge, plus the junctions they touch.

    v9 listed its predecessors' selection files by name. v10 GLOBS them
    instead: a hand-maintained list is one forgotten line away from re-using a
    previously validated edge, and the glob is strictly more inclusive. It is
    still deterministic — sorted, and the current campaign's own artifacts are
    excluded by name so a re-freeze after a deliberate removal reproduces.
    """
    edges = set()
    for path in sorted(VALIDATION.glob("monthly_proxy_manifest*.json")):
        if path.name == f"monthly_proxy_manifest_{CAMPAIGN}.json":
            continue
        manifest = json.loads(path.read_text())
        for case in manifest.get("cases", []):
            edges.update(case.get("directed_edges", []))
            edges.update((case.get("closure_search_spec") or {}).get("directed_edges", []))
    for path in sorted(VALIDATION.glob("heldout_v*_selection.json")):
        if path.name == f"heldout_{CAMPAIGN}_selection.json":
            continue
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
    """Topology-filtered, prior-disjoint candidates from the tracked network."""
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
    """Per-edge, per-variant exposure across the 15 frozen closure windows."""
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


def survivability(archive, pool):
    """Run the Stage 4 precondition over the whole pool, once.

    The probe uses run_scenario's OWN graph builder and reachability search, so
    the selection cannot be right about a detour the simulation then disagrees
    with. That import also fixes the network this freeze is bound to: its
    digest goes into `source_fingerprints`.
    """
    import run_scenario as rs
    if not rs.NET_PATH.is_file():
        raise SystemExit(
            f"the survivability probe needs the SUMO network at {rs.NET_PATH}, "
            f"which is absent. Build it with `make sumo-net` (deterministic "
            f"from the tracked web/data/graph.graphml) and re-run.")
    return evaluate_pool([edge["edge_id"] for edge in pool],
                         archive_path=archive.path,
                         build_adjacency=rs.build_edge_graph,
                         reachable=rs.reachable), rs.NET_PATH


def score(features, edge_id):
    """Versioned formula `FORMULA_VERSION` — fixed before any artifact.

    Identical to v9's `demand_exposure_v3_stratified` ranking. The version bump
    records that ELIGIBILITY gained a condition, not that the ranking changed;
    the survivability filter is applied in `select` so that the recorded score
    of a refused candidate stays comparable with an accepted one.
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


def select(pool, features, reports):
    """Deterministic and non-interactive: filter, rank, guarantee each road
    class, then fill by rank.

    The survivability filter runs BEFORE ranking, so a candidate that severs
    someone can never take a slot from one that does not — which is the whole
    point of moving the check ahead of the SUMO runs.
    """
    scored, refused = [], []
    for edge in pool:
        metrics = score(features, edge["edge_id"])
        if metrics is None:
            continue
        report = reports[edge["edge_id"]]
        candidate = {**edge, **metrics, "survivability": report.to_dict()}
        (scored if report.survives else refused).append(candidate)
    scored.sort(key=lambda e: (-e["temporal_variation"], -e["median_support"],
                               e["edge_id"]))
    refused.sort(key=lambda e: e["edge_id"])

    chosen, used = [], set()

    def take(edge):
        chosen.append(edge)
        used.update((edge["u"], edge["v"]))

    def independent(edge):
        return edge["u"] not in used and edge["v"] not in used

    for road_class in ROAD_CLASSES:
        for edge in scored:
            if len(chosen) == CASES_REQUIRED:
                break
            if edge["road_class"] == road_class and independent(edge):
                take(edge)
                break
    for edge in scored:
        if len(chosen) == CASES_REQUIRED:
            break
        if independent(edge):
            take(edge)

    if len(chosen) < CASES_REQUIRED:
        raise SystemExit(
            f"only {len(chosen)} eligible independent candidates survive their "
            f"own closure ({len(refused)} refused by the survivability rule); "
            f"refusing to freeze")
    missing = sorted(set(ROAD_CLASSES) - {e["road_class"] for e in chosen})
    if missing:
        raise SystemExit(
            f"selection does not cover required road class(es): {missing}")

    surviving = sum(1 for edge in chosen if edge["survivability"]["survives_own_closure"])
    if surviving < MIN_SURVIVING_CASES:
        raise SystemExit(
            f"only {surviving} of {len(chosen)} selected cases survive their own "
            f"closure; Stage 4 requires at least {MIN_SURVIVING_CASES} so "
            f"ranking_case_fraction >= 0.5 is achievable before any SUMO run")

    chosen.sort(key=lambda e: (-e["temporal_variation"], -e["median_support"],
                               e["edge_id"]))
    for index, edge in enumerate(chosen):
        edge["expected_discriminating"] = index < 2      # top two, by the rule
        edge["case_id"] = (
            f"{CAMPAIGN}-discriminating-{edge['road_class']}-{'ab'[index]}"
            if index < 2 else
            f"{CAMPAIGN}-control-{edge['road_class']}-{index - 1}")
    return chosen, len(scored), refused


def compose():
    archive = bind_canonical_archive(CANONICAL_PATH, CANONICAL_IDENTITY)
    windows = schedule_windows(archive.epoch_sim)
    pool, prior = candidate_pool()
    features = exposure_features(archive, pool, windows)
    reports, net_path = survivability(archive, pool)
    chosen, eligible_count, refused = select(pool, features, reports)

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
                            "variant and window, AND the edge survives its own "
                            f"closure under {SURVIVABILITY_RULE_VERSION}"),
            "eligibility_rationale": (
                "v9's exposure floor is unchanged and keeps the ranking signal "
                "from selecting its own degenerate cases; the survivability "
                "condition is added because v9 failed ranking_case_fraction "
                "with three of five cases producing zero eligible schedules — "
                "selection had proved the edges carried demand but never asked "
                "whether closing them strands anyone"),
            "survivability_rule": {
                "version": SURVIVABILITY_RULE_VERSION,
                "disqualifies": (
                    "any vehicle crossing the edge whose destination becomes "
                    "unreachable once the edge is removed, measured from the "
                    "edge immediately before the closure on that vehicle's own "
                    "route; or any successor left with the closed edge as its "
                    "only incoming connection"),
                "does_not_disqualify": (
                    "denied departures (routes starting on the edge). Every "
                    "street with departures has some, so gating on them would "
                    "refuse to close any real street — the mistake C1 fixed on "
                    "2026-08-06. They are recorded per candidate instead."),
                "worst_variant": "an edge that severs under ANY of q50/q10/q90 is refused",
            },
            "ranking": "mean relative temporal range, then median support, then edge id",
            "expected_discriminating": "the top two by that ranking",
            "inputs": ("canonical archived demand routes, tracked topology and "
                       "the derived SUMO connection graph only"),
            "reads_outcomes": False,
            "not_a_prediction": ("a selection signal only; it does not guarantee a "
                                 "SUMO objective spread"),
            "raw_evidence": ("each selected case records schedule_ids, the "
                             "q10/q50/q90 vehicle exposure for every one of "
                             "those windows in the same order, and its full "
                             "survivability report"),
            "recomputation": (
                "per variant let low=min(window_exposure) and "
                "high=max(window_exposure); temporal_variation = mean over "
                "q50,q10,q90 of (high-low)/high, rounded to 6dp; "
                "median_support = median over variants of "
                "median(window_exposure), rounded to 3dp; min_support = min "
                f"over variants of low; eligibility requires low>="
                f"{MIN_WINDOW_SUPPORT:g} everywhere AND "
                "survives_own_closure"),
        },
        "topology_rules": {
            "road_class": list(ROAD_CLASSES),
            "max_sensor_distance_m": MAX_SENSOR_DISTANCE_M,
            "min_edge_length_m": MIN_EDGE_LENGTH_M,
            "min_window_support": MIN_WINDOW_SUPPORT,
            "survives_own_closure": True,
            "min_surviving_cases": MIN_SURVIVING_CASES,
            "excludes": "every prior campaign edge, its reverse and shared-junction neighbours",
        },
        "identity": {
            "policy_content_key_algorithm": "sha256-canonical-policy-body",
            "source_identity": "monthly-proxy-v10-stratified-shortlist-v3",
            "outcome_identity": "untouched-v6-only",
            "gate_semantics": "identical to v4/v5/v9; no threshold moved",
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
            "designation": ("the same archive v9 bound, by exact path and full "
                            "identity; does not repair the globally ambiguous "
                            "demand key"),
        },
        "exclusion_proof": {
            "prior_heldout_edge_count": len(prior),
            "eligible_candidate_count": eligible_count,
            "refused_by_survivability_count": len(refused),
            "intersection_with_prior": [],
            "shared_junctions_with_prior": 0,
        },
        "physical_independence": {
            "rule": "no reverse direction and no shared junction among selected cases",
            "min_edge_length_m": MIN_EDGE_LENGTH_M,
            "shared_junctions": 0,
            "reverse_pairs": 0,
        },
        "survivability": {
            "rule_version": SURVIVABILITY_RULE_VERSION,
            "surviving_selected_cases":
                sum(1 for c in chosen if c["survivability"]["survives_own_closure"]),
            "min_surviving_cases": MIN_SURVIVING_CASES,
            # The refused candidates are the evidence that the condition BINDS.
            # A rule that refused nothing would be indistinguishable from no
            # rule at all, and the reader could not tell which it was.
            "refused": [
                {"edge_id": entry["edge_id"],
                 "road_class": entry["road_class"],
                 "vehicles_severed_destination":
                     entry["survivability"]["vehicles_severed_destination"],
                 "successors_cut_off": entry["survivability"]["successors_cut_off"]}
                for entry in refused],
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
                                 "window_exposure": c["window_exposure"]},
             "survivability": c["survivability"]}
            for c in chosen
        ],
        "source_fingerprints": {
            "web/data/network.geojson": sha256_file(NETWORK),
            # Derived, gitignored, and therefore fingerprinted rather than
            # trusted: `make sumo-net` reproduces it deterministically from the
            # tracked graph, so this digest is what makes the survivability
            # probe auditable after the fact.
            "sumo/net.net.xml": sha256_file(net_path),
        },
    }
    selection["content_key"] = canonical_key(selection)

    sources = [
        "traffic_sim/simulation/heldout_selection.py",
        "traffic_sim/simulation/heldout_gate.py",
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/monthly_proxy.py",
        "traffic_sim/simulation/proxy_validation.py",
        "traffic_sim/simulation/closure_survivability.py",
        "traffic_sim/simulation/closure_teleport.py",
        "traffic_sim/core/closure_calendar.py",
        "run_scenario.py",
        "run_monthly_proxy_validation.py",
        "tools/freeze_heldout_v9.py",
        "tools/freeze_heldout_v10.py",
        "validation/monthly_proxy_policy_v10.json",
        "validation/heldout_v10_selection.json",
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
        "source_identity": "monthly-proxy-v10-stratified-shortlist-v3",
        "minimum_cases": CASES_REQUIRED,
        "minimum_ranking_case_fraction": 0.5,
        "minimum_spearman_case_fraction": 0.5,
        "required_strata": {
            "day_type": ["mixed_weekday_weekend"],
            "duration": ["single_8h"],
            "road_class": list(ROAD_CLASSES),
            "sensor_distance_band": ["near_within_400m"],
            "topology": ["closure_survivable_pre_outcome"],
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
                       "topology": "closure_survivable_pre_outcome"},
            "closure_search_spec": spec.to_dict(),
            "schedule_ids": ids,
        })
    manifest["source_fingerprints"] = {}
    out = {
        f"validation/monthly_proxy_policy_{CAMPAIGN}.json":
            json.dumps(policy, indent=2, sort_keys=True) + "\n",
        f"validation/heldout_{CAMPAIGN}_selection.json":
            json.dumps(selection, indent=2, sort_keys=True) + "\n",
    }
    return out, manifest, sources


def finalize_manifest(out, manifest, sources):
    """Fingerprint sources with this campaign's policy/selection as staged."""
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
    out[f"validation/monthly_proxy_manifest_{CAMPAIGN}.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out


def build_artifacts():
    """Compose all three artifacts in memory, touching nothing on disk."""
    out, manifest, sources = compose()
    return finalize_manifest(out, manifest, sources)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true",
                        help="recompose and compare; never writes")
    parser.add_argument("--dry-run", action="store_true",
                        help="compose and report the selection; never writes")
    args = parser.parse_args(argv)

    # A missing or mismatched canonical archive is a legitimate fail-closed
    # outcome, not a crash: this tool is normally run on a machine that has the
    # gitignored runs/ tree, and a raw traceback tells the reader nothing about
    # which of the two prerequisites is absent.
    from traffic_sim.simulation.heldout_selection import CanonicalBindingError
    try:
        artifacts = build_artifacts()
    except CanonicalBindingError as error:
        raise SystemExit(
            f"the canonical demand archive could not be bound: {error}. v10 "
            f"binds the SAME archive v9 froze, by exact path and full identity; "
            f"it lives under the gitignored runs/ tree.")
    selection = json.loads(artifacts[f"validation/heldout_{CAMPAIGN}_selection.json"])

    if args.verify:
        drift = [rel for rel, text in artifacts.items()
                 if not (ROOT / rel).is_file() or (ROOT / rel).read_text() != text]
        print("reproduces byte-for-byte:", not drift, drift or "")
        return 0 if not drift else 1

    if not args.dry_run:
        publish(artifacts)
        for relative in artifacts:
            print(f"  {relative}: {sha256_file(ROOT / relative)[:16]}…")

    manifest = json.loads(
        artifacts[f"validation/monthly_proxy_manifest_{CAMPAIGN}.json"])
    print("manifest key:", manifest["content_key"])
    print("cases:", len(manifest["cases"]),
          "| schedules:", sum(len(c["schedule_ids"]) for c in manifest["cases"]))
    print("refused by survivability:",
          selection["exclusion_proof"]["refused_by_survivability_count"])
    for entry in selection["selected_edges"]:
        f = entry["demand_features"]
        s = entry["survivability"]
        print(f"  {entry['case_id']:34s} {entry['edge_id']:26s} "
              f"var={f['temporal_variation']:.4f} support={f['median_support']} "
              f"severed={s['vehicles_severed_destination']} "
              f"denied={s['vehicles_denied_departure']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
