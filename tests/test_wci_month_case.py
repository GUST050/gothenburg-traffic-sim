"""Freezing one month case: the policy selects it, and it stays provable.

The registration is built from a synthetic census whose archives and
catalogs are real small files, so selection, the requirements on the chosen
case and every drift check run against bytes rather than doubles. One test
re-proves the registration that was actually published.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.closure_effect_eligibility as cee
from tests.test_closure_effect_eligibility import (
    ALL_X,
    _archive,
    _pool,
    _vehicles_xml,
)
from tools import freeze_wci_month_case as month
from tools import guarded_wci_build as guard

EDGES = frozenset({"A", "X", "Y", "Z"})
ROOT = Path(__file__).resolve().parents[1]
#: The newest published registration; an older one binds older sources and
#: is superseded rather than re-proved.
PUBLISHED = max(
    ROOT.glob("validation/wci_month_case_registration_*.json"),
    key=lambda path: path.name, default=ROOT / "no-registration.json")


def _census(tmp_path, *, rows_edges, manifest_path):
    """A census over two small archives and two catalog pools."""
    catalog_root = tmp_path / "catalog"
    pools = {"weekday": _pool(catalog_root, "pool-wd", ["A X", "A Y"]),
             "weekend": _pool(catalog_root, "pool-we", ["A X", "A Y"])}
    both = {variant: ["A X", "A Y"] for variant in ALL_X}
    refs = [_archive(tmp_path, "a1", variants=both, pools=pools),
            _archive(tmp_path, "a2",
                     variants={"q10": ["A X", "A Y"],
                               "q50": ["A X", "A X", "A Y"],
                               "q90": ["A X", "A Y"]}, pools=pools)]
    inventory = cee.build_inventory(rows_edges, refs,
                                    catalog_root=catalog_root)
    units = {ref.build_key: 65 for ref in refs}
    verdicts = {edge: cee.assess(edge, inventory,
                                 network_edges=set(rows_edges) | {"A"},
                                 required_build_keys=sorted(
                                     ref.build_key for ref in refs),
                                 daily_units=units) for edge in rows_edges}
    record = {
        "schema": month.CENSUS_SCHEMA,
        "release_evidence": False,
        "inventory": inventory.to_dict(),
        "inventory_content_key": inventory.content_key,
        "daily_units_by_build_key": units,
        "qualified_demand_manifest": {
            "path": str(manifest_path),
            "sha256": month.sha256_file(manifest_path),
            "content_key": "q" * 64, "evidence_id": "test-manifest"},
        "production_sources": {
            name: month.sha256_file(ROOT / name)
            for name in ("tools/closure_effect_eligibility.py",)},
        "rows": [{"edge_id": edge, "structural_rank": rank,
                  "structurally_survivable": True,
                  "measurement_status": "measured",
                  "effect_eligible": verdicts[edge].effect_eligible,
                  "reason_codes": list(verdicts[edge].reason_codes)}
                 for rank, edge in enumerate(rows_edges, start=1)],
    }
    record["content_key"] = month._digest(month._body(record))
    return record


@pytest.fixture
def census(tmp_path):
    """Rank 1 has no traffic; rank 2 and 3 are eligible, rank 3 busiest."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return _census(tmp_path, rows_edges=["Z", "Y", "X"],
                   manifest_path=manifest)


class TestSelection:
    def test_the_first_eligible_in_structural_order_wins(self, census):
        derived = month.rederive(census, network_edges={"A", "X", "Y", "Z"})
        assert [row["edge_id"] for row in derived["rows"]] == ["Z", "Y", "X"]
        assert [row["edge_id"] for row in derived["eligible_in_order"]] == [
            "Y", "X"], "Z has no traffic; Y ranks before the busier X"
        assert derived["disagreements"] == []

    def test_the_busiest_edge_is_not_preferred(self, census):
        derived = month.rederive(census, network_edges={"A", "X", "Y", "Z"})
        chosen, other = derived["eligible_in_order"][:2]
        assert (chosen["summary"]["crossings"]["q50"]
                < other["summary"]["crossings"]["q50"])

    def test_census_rows_that_disagree_with_the_policy_are_refused(
            self, census, tmp_path):
        census["rows"][0]["effect_eligible"] = True
        census["content_key"] = month._digest(month._body(census))
        path = tmp_path / "census.json"
        path.write_text(json.dumps(census), encoding="utf-8")
        with pytest.raises(SystemExit, match="disagree"):
            month.build_registration(census_path=path, canary_spec_path=path,
                                     nonzero_evidence=[])

    @pytest.mark.parametrize("patch,expected", [
        ({"catalog_routes": {"weekday": 0, "weekend": 1}},
         "no route support"),
        ({"crossings": {"q10": 1, "q50": 0, "q90": 1}},
         "no crossing vehicle"),
        ({"archives_with_crossings": 1, "archives": 2}, "not every archive"),
        ({"daily_units_with_crossings": 0}, "no daily unit"),
    ])
    def test_the_chosen_case_must_show_traffic_everywhere(self, patch,
                                                          expected):
        chosen = {"effect_eligible": True, "reason_codes": ["eligible"],
                  "structurally_survivable": True,
                  "summary": {"catalog_routes": {"weekday": 1, "weekend": 1},
                              "crossings": {"q10": 1, "q50": 1, "q90": 1},
                              "archives": 2, "archives_with_crossings": 2,
                              "daily_units": 130,
                              "daily_units_with_crossings": 130}}
        chosen["summary"].update(patch)
        problems = month._chosen_requirements(chosen)
        assert any(expected in problem for problem in problems), problems

    def test_a_tampered_inventory_is_refused(self, census):
        census["inventory"]["crossings"]["X"]["q10"] = {"key-a1": 99}
        with pytest.raises(ValueError, match="content key"):
            month.inventory_of(census)


def _registration(census_record, tmp_path):
    """A registration built by hand from a synthetic census."""
    from traffic_sim.core.contracts import ClosureSearchSpec

    inventory = month.inventory_of(census_record)
    derived = month.rederive(census_record,
                             network_edges={"A", "X", "Y", "Z"})
    chosen = derived["eligible_in_order"][0]
    spec = json.loads((ROOT / month.FROZEN_ZERO_SPEC).read_text(
        encoding="utf-8"))
    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps(census_record), encoding="utf-8")
    record = {
        "schema": month.SCHEMA,
        "case_selection_policy": cee.POLICY,
        "chosen_edge": chosen["edge_id"],
        "chosen_verdict": chosen,
        "spec": {**spec, "directed_edges": [chosen["edge_id"]]},
        "candidates": [row["edge_id"] for row in derived["rows"]],
        "candidate_list_digest": month._digest(
            [row["edge_id"] for row in derived["rows"]]),
        "verdicts": derived["rows"],
        "eligible_in_order": [row["edge_id"]
                              for row in derived["eligible_in_order"]],
        "qualified_demand_manifest": census_record[
            "qualified_demand_manifest"],
        "archives": month._archive_bindings(inventory),
        "catalogs": month._catalog_bindings(inventory),
        "census": {"path": str(census_path),
                   "sha256": month.sha256_file(census_path),
                   "content_key": census_record["content_key"],
                   "inventory_content_key": census_record[
                       "inventory_content_key"]},
        "canary_spec": {"path": str(census_path),
                        "sha256": month.sha256_file(census_path)},
        "frozen_zero_spec": {
            "path": str(ROOT / month.FROZEN_ZERO_SPEC),
            "sha256": month.sha256_file(ROOT / month.FROZEN_ZERO_SPEC)},
        "source_bindings": {
            "tools/closure_effect_eligibility.py": month.sha256_file(
                ROOT / "tools/closure_effect_eligibility.py")},
    }
    record["spec_content_key"] = ClosureSearchSpec.from_dict(
        record["spec"]).content_key
    record["content_key"] = month._digest(month._body(record))
    return record


class TestVerification:
    def test_a_fresh_registration_verifies(self, census, tmp_path):
        assert month.verify_registration(
            _registration(census, tmp_path), network_edges=EDGES) == []

    def test_a_tampered_registration_is_refused(self, census, tmp_path):
        record = _registration(census, tmp_path)
        record["chosen_edge"] = "Z"
        assert "registration content key does not describe it" in \
            month.verify_registration(record)

    def test_catalog_drift_refuses_the_registration(self, census, tmp_path):
        record = _registration(census, tmp_path)
        catalog = Path(record["catalogs"]["weekday"]["path"])
        catalog.write_text(_vehicles_xml(["A Q"]), encoding="utf-8")
        problems = month.verify_registration(record, network_edges=EDGES)
        assert any("bound file changed" in problem and "catalog" in problem
                   for problem in problems), problems

    def test_archive_drift_refuses_the_registration(self, census, tmp_path):
        record = _registration(census, tmp_path)
        archive = Path(record["archives"]["key-a1"]["archive"])
        (archive / "calibrated.rou.xml").write_text(
            _vehicles_xml(["A"]), encoding="utf-8")
        assert "census inventory no longer verifies" in \
            month.verify_registration(record, network_edges=EDGES)

    def test_a_source_change_refuses_the_registration(self, census,
                                                      tmp_path):
        record = _registration(census, tmp_path)
        record["source_bindings"] = {
            "tools/closure_effect_eligibility.py": "0" * 64}
        record["content_key"] = month._digest(month._body(record))
        assert any("closure_effect_eligibility.py" in problem
                   for problem in month.verify_registration(record, network_edges=EDGES))

    def test_the_drift_bindings_name_every_archive_file(self, census,
                                                        tmp_path):
        record = _registration(census, tmp_path)
        archives = month.drift_archives(record)
        assert sorted(archives) == ["key-a1", "key-a2"]
        for binding in archives.values():
            assert sorted(binding["files"]) == [
                "calibrated.rou.xml", "demand_meta.json"]
            assert all(binding["files"].values())
        assert guard.DriftChecker(month.drift_files(record),
                                  archives).full() == []


class TestThePublishedRegistration:
    """The month case that was actually frozen, re-proved from disk."""

    @pytest.mark.skipif(not PUBLISHED.is_file(),
                        reason="no month case has been frozen here")
    def test_it_still_verifies(self):
        record = json.loads(PUBLISHED.read_text(encoding="utf-8"))
        if record.get("schema") != month.SCHEMA:
            assert month.verify_registration(record) == [
                f"unexpected schema: {record.get('schema')}"]
            return
        assert month.verify_registration(record) == []
        assert record["chosen_edge"] == record["eligible_in_order"][0]
        assert record["population"] == {
            "parents": 1690, "daily_units": 1950, "variant_records": 1950,
            "build_keys": 30, "units_per_build_key": [65]}
        assert len(record["archives"]) == 30
        assert sorted(record["catalogs"]) == ["weekday", "weekend"]
        assert all(canary["affected_vehicles_by_build_keys"]
                   for canary in record["nonzero_canaries"])


class TestProductionPreflight:
    def test_the_zero_edge_profile_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(month, "verify_registration", lambda _record: [])
        registration = tmp_path / "registration.json"
        registration.write_text(json.dumps({
            "spec_content_key": "d38038fd495414129508",
            "source_bindings": {}, "archives": {}, "catalogs": {},
            "qualified_demand_manifest": {"path": str(tmp_path / "m.json"),
                                          "sha256": "0" * 64},
            "frozen_zero_spec": {"path": str(tmp_path / "s.json"),
                                 "sha256": "0" * 64},
            "census": {"path": str(tmp_path / "c.json"), "sha256": "0" * 64},
            "canary_spec": {"path": str(tmp_path / "k.json"),
                            "sha256": "0" * 64},
        }), encoding="utf-8")
        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({
            "bound_spec": {"search_content_key": "383c9130a45370c2f296"},
            "population": {"daily_units": 1950, "parents": 1690}}),
            encoding="utf-8")

        setup = guard.production_setup(SimpleNamespace(
            registration=registration, profile=profile,
            index_out=tmp_path / "index",
            build_evidence_out=tmp_path / "build.json",
            status_dir=tmp_path / "status", evidence_id="test"))
        assert any("profile binds another spec" in problem
                   for problem in setup["problems"])
        assert setup["diagnostic"] is False
