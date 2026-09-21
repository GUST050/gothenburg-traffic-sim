"""Versioned effect eligibility for AUTOMATIC closure-case selection.

`closure_effect_eligibility_v1` filters automatically chosen WCI, benchmark
and performance cases after the unchanged structural discovery. Frozen
artifacts that name no policy replay `structural_survivability_v1`. User
closures never pass through the policy: a directed edge without traffic is a
legitimate closure whose honest answer is zero.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.closure_effect_eligibility as cee
import tools.cost_ordered_benchmark as bench
import tools.cost_ordered_benchmark_suite as suite
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand
from traffic_sim.simulation.deterministic_disruption import (
    VARIANT_FILENAMES,
    closure_seconds,
    parent_closure_cost,
)
from traffic_sim.simulation.disruption import (
    build_parsed_window_cost_index,
    parse_route_vehicles,
)
from traffic_sim.simulation.independent_daily import daily_unit_records

ROOT = Path(__file__).resolve().parents[1]
ZERO_EDGE = "26355153_26842525_0"
MEASURED_REVERSE = "26842525_26355153_0"
ALL_X = {variant: ["A X"] for variant in VARIANT_FILENAMES}


def _vehicles_xml(routes, extra=""):
    return "<routes>" + "".join(
        f'<vehicle id="v{n}" depart="{n}.00"><route edges="{route}"/>'
        "</vehicle>" for n, route in enumerate(routes)) + extra + "</routes>"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog(root: Path, key: str, routes) -> str:
    path = root / key / "catalog.rou.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_vehicles_xml(routes), encoding="utf-8")
    (path.parent / "catalog.meta.json").write_text(json.dumps({
        "candidates": {f"c{n}": {} for n in range(len(routes))}}),
        encoding="utf-8")
    return _sha(path)


def _pool(root: Path, key: str, routes) -> tuple[str, str, str]:
    """(catalog key, routes sha256, metadata sha256) as an archive binds."""
    routes_sha = _catalog(root, key, routes)
    return key, routes_sha, _sha(root / key / "catalog.meta.json")


def _archive(root: Path, name: str, *, variants, pools, extra=""):
    """``variants``: variant -> routes; ``pools``: pool -> (key, sha[, meta])."""
    archive = root / name
    archive.mkdir(parents=True, exist_ok=True)
    meta = {"demand_build_key": f"key-{name}",
            "pfe_fit_variants": {
                cee.VARIANT_FIT_KEYS[variant]: {"vehicles": len(routes)}
                for variant, routes in variants.items()}}
    if pools is not None:
        meta["candidate_catalog"] = {
            "keys": {pool: binding[0] for pool, binding in pools.items()},
            "artifacts": {pool: {"routes_sha256": binding[1],
                                 **({"metadata_sha256": binding[2]}
                                    if len(binding) > 2 else {})}
                          for pool, binding in pools.items()}}
    (archive / "demand_meta.json").write_text(json.dumps(meta),
                                              encoding="utf-8")
    hashes = {}
    for variant, routes in variants.items():
        path = archive / VARIANT_FILENAMES[variant]
        path.write_text(_vehicles_xml(routes, extra), encoding="utf-8")
        hashes[variant] = _sha(path)
    return cee.ArchiveRef(build_key=f"key-{name}", archive=archive,
                          content_key=f"content-{name}",
                          variant_sha256=hashes)


@pytest.fixture
def world(tmp_path):
    catalog_root = tmp_path / "catalog"
    pools = {"weekday": _pool(catalog_root, "pool-wd", ["A X", "A Y"]),
             "weekend": _pool(catalog_root, "pool-we", ["A X"])}
    refs = [
        _archive(tmp_path, "a1", variants=ALL_X, pools=pools),
        _archive(tmp_path, "a2", variants={"q10": ["A Y"], "q50": ["A X", "A X"],
                                           "q90": ["A X", "A X"]},
                 pools={"weekday": pools["weekday"]}),
    ]
    return SimpleNamespace(catalog_root=catalog_root, pools=pools,
                           refs=refs, root=tmp_path)


def _verdict(world, edge, refs=None, required=None, network=None):
    refs = world.refs if refs is None else refs
    inventory = cee.build_inventory([edge, "X", "Y", "Z"], refs,
                                    catalog_root=world.catalog_root)
    keys = [ref.build_key for ref in refs] if required is None else required
    return cee.assess(
        edge, inventory,
        network_edges={"A", "X", "Y", "Z"} if network is None else network,
        required_build_keys=keys,
        daily_units={key: 65 for key in keys})


def _spy_parser(monkeypatch):
    calls = []

    def spy(path, **kwargs):
        calls.append(Path(path))
        return parse_route_vehicles(path, **kwargs)

    monkeypatch.setattr(cee, "parse_route_vehicles", spy)
    return calls


class TestThePredicate:
    def test_a_carried_edge_is_eligible(self, world):
        verdict = _verdict(world, "X")
        assert verdict.effect_eligible is True
        assert verdict.reason_codes == (cee.ELIGIBLE,)
        assert verdict.summary["catalog_routes"] == {"weekday": 1,
                                                     "weekend": 1}
        assert verdict.summary["crossings"] == {"q50": 3}
        assert verdict.summary["archives_with_crossings"] == 2
        assert verdict.summary["daily_units_with_crossings"] == 130

    def test_an_unused_edge_has_no_support_and_no_crossings(self, world):
        verdict = _verdict(world, "Z")
        assert verdict.effect_eligible is False
        assert verdict.reason_codes == (cee.NO_CATALOG_ROUTE_SUPPORT,
                                        cee.NO_OBSERVED_ARCHIVE_CROSSINGS)
        details = {(r["code"], r["detail"]) for r in verdict.reasons}
        assert (cee.NO_CATALOG_ROUTE_SUPPORT, "weekend") in details
        assert (cee.NO_OBSERVED_ARCHIVE_CROSSINGS, "q50") in details

    def test_every_variant_needs_an_observed_crossing(self, world):
        verdict = _verdict(world, "Y")
        assert cee.NO_OBSERVED_ARCHIVE_CROSSINGS in verdict.reason_codes
        assert {r["detail"] for r in verdict.reasons
                if r["code"] == cee.NO_OBSERVED_ARCHIVE_CROSSINGS} == {
            "q50", "daily_units"}

    def test_a_missing_network_edge_is_named(self, world):
        verdict = _verdict(world, "X", network={"A"})
        assert verdict.reason_codes == (cee.MISSING_NETWORK_EDGE,)

    def test_a_missing_variant_is_a_rejection_not_a_zero(self, world):
        ref = _archive(world.root, "a3", variants={"q10": ["A X"]},
                       pools={"weekday": world.pools["weekday"]})
        verdict = _verdict(world, "X", refs=[ref])
        assert cee.MISSING_REQUIRED_VARIANT in verdict.reason_codes
        assert cee.NO_OBSERVED_ARCHIVE_CROSSINGS not in verdict.reason_codes

    @pytest.mark.parametrize("pools", [
        None,
        {"weekday": ("absent", "0" * 64)},
        {"weekday": ("pool-wd", "0" * 64)},
    ], ids=["unbound", "file-missing", "sha256-mismatch"])
    def test_a_required_pool_problem_is_named(self, world, pools):
        ref = _archive(world.root, "a4", variants=ALL_X, pools=pools)
        verdict = _verdict(world, "X", refs=[ref])
        assert cee.MISSING_REQUIRED_POOL in verdict.reason_codes

    def test_one_pool_name_with_two_catalogs_is_a_required_pool_problem(
            self, world):
        other = _archive(world.root, "a5", variants=ALL_X,
                         pools={"weekday": world.pools["weekend"]})
        verdict = _verdict(world, "X", refs=[world.refs[0], other])
        assert cee.MISSING_REQUIRED_POOL in verdict.reason_codes

    def test_a_required_build_key_without_archive_is_incomplete(self, world):
        verdict = _verdict(world, "X", required=["key-a1", "key-absent"])
        assert verdict.reason_codes == (cee.INCOMPLETE_ARCHIVE_COVERAGE,)

    def test_pool_names_come_from_metadata(self, world):
        ref = _archive(world.root, "a6", variants=ALL_X,
                       pools={"holiday": world.pools["weekend"]})
        assert _verdict(world, "X", refs=[ref]).summary["catalog_routes"] == {
            "holiday": 1}

    def test_the_production_parser_reads_each_file_once(self, world,
                                                         monkeypatch):
        calls = _spy_parser(monkeypatch)
        candidates = [f"E{n}" for n in range(6)] + ["X", "Y"]
        inventory = cee.build_inventory(candidates, world.refs,
                                        catalog_root=world.catalog_root)
        assert len(calls) == len(set(calls)) == 1 * 2 + 2
        assert inventory.crossings["X"]["q50"] == {"key-a1": 1, "key-a2": 2}
        for edge in candidates:
            cee.assess(edge, inventory, network_edges=set(candidates),
                       required_build_keys=["key-a1", "key-a2"],
                       daily_units={"key-a1": 1, "key-a2": 1})
        assert len(calls) == 4, "assessment must not reread any file"

    def test_named_routes_count_exactly_as_the_production_parser(
            self, world):
        named = ('<route id="r1" edges="A X"/>'
                 '<vehicle id="named" depart="9" route="r1"/>')
        ref = _archive(world.root, "a7", variants=ALL_X,
                       pools={"weekday": world.pools["weekday"]}, extra=named)
        inventory = cee.build_inventory(["X"], [ref],
                                        catalog_root=world.catalog_root)
        parsed = parse_route_vehicles(ref.archive / VARIANT_FILENAMES["q50"])
        assert inventory.crossings["X"]["q50"]["key-a7"] == sum(
            1 for vehicle in parsed if "X" in vehicle[0])

    def test_eligible_candidates_keep_the_given_stable_order(self, world):
        edges = ["Y", "X", "Z", "A"]
        inventory = cee.build_inventory(edges, world.refs,
                                        catalog_root=world.catalog_root)
        verdicts = {edge: cee.assess(
            edge, inventory, network_edges=set(edges),
            required_build_keys=["key-a1"], daily_units={"key-a1": 1})
            for edge in edges}
        items = [{"edge": edge, "key": f"k-{edge}"} for edge in edges]
        orders = set()
        for seed in range(5):
            shuffled = items[:]
            random.Random(seed).shuffle(shuffled)
            chosen = cee.eligible_in_order(
                shuffled, lambda item: verdicts[item["edge"]],
                order_key=lambda item: item["key"])
            orders.add(tuple(item["edge"] for item in chosen))
        assert orders == {("A", "X")}


class TestInventoryEvidence:
    def test_fresh_evidence_verifies(self, world):
        inventory = cee.build_inventory(["X"], world.refs,
                                        catalog_root=world.catalog_root)
        assert cee.verify_inventory(inventory.to_dict()) == []
        assert inventory.to_dict()["content_key"] == inventory.content_key

    def test_catalog_drift_invalidates_evidence(self, world):
        record = cee.build_inventory(
            ["X"], world.refs, catalog_root=world.catalog_root).to_dict()
        path = world.catalog_root / "pool-we" / "catalog.rou.xml"
        path.write_text(_vehicles_xml(["A Y"]), encoding="utf-8")
        assert any("pool-we" in problem
                   for problem in cee.verify_inventory(record))

    def test_archive_drift_invalidates_evidence(self, world):
        record = cee.build_inventory(
            ["X"], world.refs, catalog_root=world.catalog_root).to_dict()
        path = world.refs[1].archive / VARIANT_FILENAMES["q50"]
        path.write_text(_vehicles_xml(["A"]), encoding="utf-8")
        assert any("key-a2" in problem
                   for problem in cee.verify_inventory(record))

    def test_a_tampered_record_is_refused(self, world):
        record = cee.build_inventory(
            ["X"], world.refs, catalog_root=world.catalog_root).to_dict()
        record["crossings"]["X"]["q50"]["key-a1"] = 99
        assert "inventory content key mismatch" in cee.verify_inventory(record)


class TestPolicyVersions:
    def test_a_record_without_policy_replays_the_legacy_policy(self):
        assert cee.policy_of(None) == cee.LEGACY_POLICY
        assert cee.policy_of({}) == cee.LEGACY_POLICY
        assert cee.policy_of({"case_selection_policy": cee.POLICY}) == (
            cee.POLICY)
        with pytest.raises(ValueError, match="unknown"):
            cee.policy_of({"case_selection_policy": "invented_v9"})

    def test_frozen_subhour_registrations_replay_the_legacy_policy(self):
        records = sorted((ROOT / "validation").glob(
            "subhour_bounded_sumo_registration_*.json"))
        assert records
        for path in records:
            record = json.loads(path.read_text(encoding="utf-8"))
            assert cee.policy_of(record.get("selection")) == (
                cee.LEGACY_POLICY), path.name


def _spec(edge, work_date="2027-07-15"):
    return ClosureSearchSpec(
        search_id=f"case-{edge[:6]}-{work_date}",
        directed_edges=(edge,),
        demand_build_id="forecast-2027",
        source="forecast",
        permitted_date_start=work_date,
        permitted_date_end=work_date,
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "08:00"),
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1",
    )


@pytest.fixture
def subhour_world(tmp_path, monkeypatch):
    """Five edges x two periods; only ZERO_EDGE lacks an effect."""
    import tools.subhour_cost_ordered_benchmark as subhour

    edges = [ZERO_EDGE, "90000001_1_0", "90000002_1_0", "90000003_1_0",
             "90000004_1_0"]
    specs = [_spec(edge, day) for edge in edges
             for day in ("2027-07-15", "2027-08-12")]
    monkeypatch.setattr(subhour.base, "discovered_specs",
                        lambda runs_root: tuple(specs))
    monkeypatch.setattr(subhour.base, "_structural_profile", lambda spec: {
        "candidate_count": 6, "unique_daily_unit_count": 1,
        "work_dates": [spec.permitted_date_start]})
    monkeypatch.setattr(subhour, "_spec_has_metadata_archives",
                        lambda *args: True)
    monkeypatch.setattr(subhour, "_effect_archives",
                        lambda spec, runs_root, manifest: {
                            "k": {"archive": str(tmp_path)}})
    screened = []

    def screen(cases, **_kwargs):
        screened.append(len(cases))
        return {spec.content_key: {
            "effect_eligible": spec.directed_edges != (ZERO_EDGE,),
            "reason_codes": [], "edges": []} for spec, _ in cases}, None

    monkeypatch.setattr(subhour.base, "effect_screen_cases", screen)
    return SimpleNamespace(module=subhour, runs=tmp_path, screened=screened)


class TestSubhourSelection:
    def test_the_legacy_policy_reproduces_the_frozen_selection(
            self, subhour_world):
        subhour = subhour_world.module
        legacy = subhour.select_cases(subhour_world.runs,
                                      policy=cee.LEGACY_POLICY)
        assert ZERO_EDGE in legacy["distinct_edges"]
        assert subhour_world.screened == [], "legacy never screens effect"
        frozen = {"selection": {"eligible_list_digest":
                                legacy["eligible_list_digest"],
                                "selected_ids": legacy["selected_ids"]}}
        replay = subhour._recompute_selection(frozen, subhour_world.runs,
                                              None)
        assert replay["eligible_list_digest"] == (
            legacy["eligible_list_digest"])
        assert replay["selected_ids"] == legacy["selected_ids"]

    def test_the_new_policy_rejects_the_empty_direction(self,
                                                        subhour_world):
        subhour = subhour_world.module
        current = subhour.select_cases(subhour_world.runs)
        assert current["case_selection_policy"] == cee.POLICY
        assert ZERO_EDGE not in current["distinct_edges"]
        assert subhour_world.screened == [10], "one screen for every case"
        replay = subhour._recompute_selection(
            {"selection": {"case_selection_policy": cee.POLICY}},
            subhour_world.runs, None)
        assert replay["selected_ids"] == current["selected_ids"]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """One synthetic day whose routes use only the measured reverse edge."""
    catalog_root = tmp_path / "catalog"
    pool = _pool(catalog_root, "pool-wd", [f"A {MEASURED_REVERSE}"])
    runs = tmp_path / "runs"
    ref = _archive(runs, "demand-20250916-historical",
                   variants={variant: [f"A {MEASURED_REVERSE}"]
                             for variant in VARIANT_FILENAMES},
                   pools={"weekday": pool})
    meta_path = ref.archive / "demand_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({"epoch_sim": "2025-09-16T00:00:00", "n_intervals": 96,
                 "n_variants": 3,
                 "demand_spec": {"start_date": "2025-09-16",
                                 "source": "historical", "days": 1,
                                 "begin": "00:00", "end": "24:00",
                                 "structural_reference_date": "2025-09-16",
                                 "purpose": "standard"}})
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(encoding="utf-8"))
    network = frozenset(item["edge_id"]
                        for item in screen["candidate_pool"]["edges"]) | {"A"}
    monkeypatch.setattr(bench, "_effect_sources",
                        lambda data_root: (network, catalog_root))

    def resolve(spec, runs_root, cache=None):
        return {key: {"archive": str(ref.archive),
                      "routes": {variant: {"sha256": sha} for variant, sha
                                 in ref.variant_sha256.items()}}
                for key in bench._daily_units_by_build_key(spec, runs_root)}
    monkeypatch.setattr(bench, "_resolved_archives_for_spec", resolve)
    return runs


class TestCostOrderedSelection:
    def test_general_discovery_still_offers_the_zero_edge(self, library):
        assert bench.surviving_roads()[0]["edge_id"] == ZERO_EDGE
        edges = {spec.directed_edges[0]
                 for spec in bench.discovered_specs(library)}
        assert ZERO_EDGE in edges and MEASURED_REVERSE in edges

    def test_the_new_policy_never_selects_the_empty_direction(self, library):
        selection = bench.select_case(library, from_archives=True)
        assert selection["case_selection_policy"] == cee.POLICY
        assert selection["selected"]["spec"]["directed_edges"] == [
            MEASURED_REVERSE]
        zero = [item for item in selection["evaluated"]
                if item["spec"]["directed_edges"] == [ZERO_EDGE]]
        assert zero and all(not item["eligible"] for item in zero)
        assert all(cee.NO_CATALOG_ROUTE_SUPPORT in item["effect"]["reason_codes"]
                   for item in zero)

    def test_the_legacy_policy_keeps_the_structural_selection(self, library):
        selection = bench.select_case(library, from_archives=True,
                                      policy=cee.LEGACY_POLICY)
        assert selection["case_selection_policy"] == cee.LEGACY_POLICY
        assert selection["effect_inventory"] is None
        assert all(item["eligible"] == item["structurally_eligible"]
                   for item in selection["evaluated"])

    def test_the_suite_selects_only_eligible_cases(self, library):
        selection = bench.select_case(library, from_archives=True)
        chosen = suite.select_suite_cases(selection, count=1)
        assert [item["spec"]["directed_edges"] for item in chosen] == [
            [MEASURED_REVERSE]]


def _user_spec(edges):
    return ClosureSearchSpec(
        search_id="user-directed-closure",
        directed_edges=tuple(edges),
        demand_build_id="forecast-2027",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-15",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "08:00"),
        allowed_weekdays=(3,),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        objective_profile="displaced_vehicles_and_detour_v1",
    )


def _closures(spec):
    parent = next(iter(iter_closure_schedules(spec)))
    records = list(daily_unit_records(spec, parent))
    assert len(records) == 1, "a one-day spec has exactly one daily unit"
    build = records[0][2]
    return closure_seconds(spec, build(),
                           epoch=datetime(2027, 7, 14, 0, 0),
                           duration_s=3 * 86400)


class TestUserClosuresKeepTheirMeaning:
    def test_a_zero_traffic_directed_edge_is_kept_and_prices_zero(
            self, tmp_path):
        spec = ClosureSearchSpec.from_dict(_user_spec([ZERO_EDGE]).to_dict())
        assert spec.directed_edges == (ZERO_EDGE,)
        closures = _closures(spec)
        assert {item["edge_id"] for item in closures} == {ZERO_EDGE}
        routes = tmp_path / "routes.rou.xml"
        routes.write_text(_vehicles_xml([f"A {MEASURED_REVERSE}"] * 3),
                          encoding="utf-8")
        index = build_parsed_window_cost_index(
            parse_route_vehicles(routes), {ZERO_EDGE},
            {"A": 1.0, MEASURED_REVERSE: 1.0, ZERO_EDGE: 1.0},
            {"A": 10.0, MEASURED_REVERSE: 10.0, ZERO_EDGE: 10.0},
            adjacency={"A": [MEASURED_REVERSE], MEASURED_REVERSE: [ZERO_EDGE],
                       ZERO_EDGE: []})
        record = index.disruption(closures)
        assert record["vehicles_affected"] == 0
        assert record["vehicles_considered"] == 3
        cost = parent_closure_cost("user-candidate", [[
            {"demand_variant": variant, **record}
            for variant in ("q10", "q50", "q90")]])
        assert cost.vehicles_affected == 0
        assert cost.added_vehicle_hours == 0

    def test_one_listed_direction_never_closes_its_reverse(self):
        closures = _closures(_user_spec([ZERO_EDGE]))
        assert MEASURED_REVERSE not in {item["edge_id"] for item in closures}

    def test_both_directions_close_only_when_both_are_listed(self):
        closures = _closures(_user_spec([ZERO_EDGE, MEASURED_REVERSE]))
        assert {item["edge_id"] for item in closures} == {
            ZERO_EDGE, MEASURED_REVERSE}
        windows = {}
        for item in closures:
            windows.setdefault(item["edge_id"], set()).add(
                (item["begin_s"], item["end_s"]))
        assert windows[ZERO_EDGE] == windows[MEASURED_REVERSE]


# --------------------------------------------------------------------------
# Review of 10518ae..9b12e13: contracts the first delivery did not enforce.
# --------------------------------------------------------------------------

def _declare_vehicle_count(ref, variant, count):
    meta_path = ref.archive / "demand_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pfe_fit_variants"][cee.VARIANT_FIT_KEYS[variant]]["vehicles"] = count
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


class TestParserProblemsAreReasonsNeverZeros:
    def test_an_unreadable_variant_is_a_reason_not_a_crash_or_a_zero(
            self, world):
        path = world.refs[0].archive / VARIANT_FILENAMES["q50"]
        path.write_text("<routes><vehicle", encoding="utf-8")
        verdict = _verdict(world, "X")
        assert cee.MISSING_REQUIRED_VARIANT in verdict.reason_codes
        assert cee.NO_OBSERVED_ARCHIVE_CROSSINGS not in verdict.reason_codes
        assert {"code": cee.MISSING_REQUIRED_VARIANT,
                "detail": "q50@key-a1:parse_error"} in verdict.reasons

    def test_an_unreadable_catalog_is_a_pool_reason_not_missing_support(
            self, world):
        path = world.catalog_root / "pool-we" / "catalog.rou.xml"
        path.write_text("<routes><vehicle", encoding="utf-8")
        verdict = _verdict(world, "X")
        assert cee.MISSING_REQUIRED_POOL in verdict.reason_codes
        assert cee.NO_CATALOG_ROUTE_SUPPORT not in verdict.reason_codes

    def test_vehicles_the_parser_skips_are_a_reason_not_a_zero(self, world):
        # The production parser only sees routes embedded in a vehicle. A
        # vehicle referencing a named route is skipped, so its crossing would
        # otherwise read as observed zero traffic.
        named = ('<route id="r1" edges="A Z"/>'
                 '<vehicle id="named" depart="9" route="r1"/>')
        ref = _archive(world.root, "a8", variants=ALL_X,
                       pools={"weekday": world.pools["weekday"]},
                       extra=named)
        _declare_vehicle_count(ref, "q50", 2)
        verdict = _verdict(world, "Z", refs=[ref])
        assert {"code": cee.MISSING_REQUIRED_VARIANT,
                "detail": "q50@key-a8:vehicle_count_mismatch"} in (
                    verdict.reasons)
        assert cee.NO_OBSERVED_ARCHIVE_CROSSINGS not in verdict.reason_codes

    def test_an_undeclared_vehicle_count_fails_closed(self, world):
        meta_path = world.refs[0].archive / "demand_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        del meta["pfe_fit_variants"]
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        verdict = _verdict(world, "X")
        assert cee.MISSING_REQUIRED_VARIANT in verdict.reason_codes

    def test_a_catalog_count_below_its_bound_metadata_fails_closed(
            self, world):
        meta_path = world.catalog_root / "pool-we" / "catalog.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["candidates"]["extra"] = {}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        ref = _archive(world.root, "a9", variants=ALL_X, pools={
            "weekend": ("pool-we",
                        world.pools["weekend"][1], _sha(meta_path))})
        verdict = _verdict(world, "X", refs=[ref])
        assert {"code": cee.MISSING_REQUIRED_POOL,
                "detail": "weekend:route_count_mismatch"} in verdict.reasons
        assert cee.NO_CATALOG_ROUTE_SUPPORT not in verdict.reason_codes

    def test_unbound_catalog_metadata_fails_closed(self, world):
        ref = _archive(world.root, "a10", variants=ALL_X, pools={
            "weekend": ("pool-we", world.pools["weekend"][1], "0" * 64)})
        verdict = _verdict(world, "X", refs=[ref])
        assert {"code": cee.MISSING_REQUIRED_POOL,
                "detail": "weekend:metadata_sha256_mismatch"} in (
                    verdict.reasons)


class _Parsed(list):
    """A weak-referenceable stand-in for one parsed file."""


class TestOneLargeVariantInMemory:
    def test_a_parsed_file_is_released_before_the_next_is_parsed(
            self, world, monkeypatch):
        import gc
        import weakref

        alive = []

        def parser(path, **kwargs):
            gc.collect()
            leaked = [ref for ref in alive if ref() is not None]
            assert not leaked, f"{len(leaked)} parsed file(s) still held"
            parsed = _Parsed(parse_route_vehicles(path, **kwargs))
            alive.append(weakref.ref(parsed))
            return parsed

        monkeypatch.setattr(cee, "parse_route_vehicles", parser)
        cee.build_inventory(["X"], world.refs,
                            catalog_root=world.catalog_root)
        assert len(alive) == 4


class TestFixedCasesAreNotLabelledEffectGated:
    def test_the_fixed_case_path_records_the_structural_policy(
            self, tmp_path):
        selection = bench.select_case(tmp_path)
        assert selection["case_selection_policy"] == cee.LEGACY_POLICY
        assert selection["effect_rule"] is None

    def test_effect_gating_without_archives_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="archives"):
            bench.select_case(tmp_path, policy=cee.POLICY)


class TestEffectEvidenceIsBoundAndVerified:
    def test_selection_evidence_verifies_until_a_catalog_drifts(
            self, library, tmp_path):
        selection = bench.select_case(library, from_archives=True)
        evidence = selection["effect_inventory"]
        assert evidence["record"]["content_key"] == (
            evidence["inventory_content_key"])
        record = {"selection": {"case_selection_policy": cee.POLICY,
                                "effect_inventory": evidence}}
        assert cee.verify_selection_evidence(record["selection"]) == []
        assert not [problem for problem in
                    bench.verify_bindings(record, library)
                    if "effect" in problem]
        path = Path(next(iter(evidence["record"]["catalogs"].values()))[
            "path"])
        path.write_text(_vehicles_xml(["A"]), encoding="utf-8")
        assert any("effect inventory" in problem
                   for problem in bench.verify_bindings(record, library))

    def test_a_new_policy_selection_without_evidence_is_refused(self):
        assert cee.verify_selection_evidence(
            {"case_selection_policy": cee.POLICY})
        assert cee.verify_selection_evidence({}) == []

    def test_the_suite_registration_keeps_the_policy_and_screens_once(
            self, monkeypatch, tmp_path):
        calls = []
        evaluated = [
            {"search_id": f"s{n}", "search_content_key": f"{n}" * 20,
             "spec": {"directed_edges": [f"edge-{n}"]},
             "candidate_count": 13, "unique_daily_unit_count": 13,
             "work_dates": [day], "work_dates_with_calibrated_archive": [day],
             "structurally_eligible": True, "effect_eligible": True,
             "eligible": True, "resolved_archives": {}}
            for n, day in enumerate(["2027-03-22", "2027-07-15"] * 2)]
        selection = {"evaluated": evaluated, "archives_available": 2,
                     "case_selection_policy": cee.POLICY,
                     "effect_rule": bench.EFFECT_SELECTION_RULE,
                     "effect_inventory": {"inventory_content_key": "k"}}

        def select_case(runs_root, **kwargs):
            calls.append(kwargs)
            return selection

        def build_registration(runs_root, **kwargs):
            if kwargs.get("selection") is None:
                select_case(runs_root, from_archives=True)
            return {"sources": {}, "selected_case": None}

        monkeypatch.setattr(suite.base, "select_case", select_case)
        monkeypatch.setattr(suite.base, "build_registration",
                            build_registration)
        record = suite.build_registration(tmp_path, data_root=tmp_path,
                                          outcome_path=tmp_path / "o.json")
        assert len(calls) == 1, "the effect inventory is built once"
        assert calls[0].get("data_root") == tmp_path
        assert record["selection"]["case_selection_policy"] == cee.POLICY
        assert record["selection"]["effect_inventory"] == {
            "inventory_content_key": "k"}

    def test_a_policy_selection_without_eligibility_selects_nothing(self):
        item = {"search_id": "s", "search_content_key": "k" * 20,
                "spec": {"directed_edges": ["e"]}, "candidate_count": 13,
                "unique_daily_unit_count": 13, "work_dates": ["2027-03-22"],
                "structurally_eligible": True}
        assert suite.select_suite_cases(
            {"case_selection_policy": cee.POLICY, "evaluated": [item]}) == []
        assert len(suite.select_suite_cases({"evaluated": [item]})) == 1


class TestSubhourUsesTheRegisteredDataRoot:
    def test_selection_screens_against_the_given_data_root(
            self, subhour_world, monkeypatch, tmp_path):
        subhour = subhour_world.module
        seen = []
        inner = subhour.base.effect_screen_cases

        def screen(cases, **kwargs):
            seen.append(kwargs.get("data_root"))
            return inner(cases, **kwargs)

        monkeypatch.setattr(subhour.base, "effect_screen_cases", screen)
        subhour.select_cases(subhour_world.runs, data_root=tmp_path / "d")
        subhour._recompute_selection(
            {"data_root": str(tmp_path / "r"),
             "selection": {"case_selection_policy": cee.POLICY}},
            subhour_world.runs, None)
        assert seen == [tmp_path / "d", (tmp_path / "r").resolve()]
