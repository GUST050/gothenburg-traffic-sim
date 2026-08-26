"""A three-variant day must also answer the map's q50-only request.

A closure search calibrates q10/q50/q90; the map asks for q50 alone. Those
are different day identities, so before this the same calendar day was
calibrated twice — once for the search, once for the map. The median arm is
shareable because it is solved first and frozen before the stress arms run,
and that the BYTES agree was measured on a real date rather than argued:
validation/q50_subset_identity_v1.json.

What is worth a permanent test here is the wiring that makes the stored day
findable, because every part of it is a silent failure when it drifts: the
arm picked, the identity a q50-only build computes, and the artifact names
that build later looks for.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_sumo_demand as bsd
from demand.day_library import DayIdentity, DayLibrary

RECORD = Path("validation/q50_subset_identity_v1.json")


class TestTheSharedArm:
    def test_the_median_arm_is_exactly_what_a_q50_only_build_publishes(self):
        # The property the subset identity rests on: restricting a stress
        # build's contract to its median arm reproduces the q50-only
        # contract, entry for entry.
        assert [bsd.median_variant(bsd.direction_variants(True))] == \
            bsd.direction_variants(False)

    def test_a_single_variant_contract_is_its_own_median(self):
        assert bsd.median_variant(bsd.direction_variants(False)) == \
            ("", "edge_shares")

    def test_a_contract_without_a_median_arm_is_reported_not_guessed(self):
        assert bsd.median_variant([("_v1", "edge_shares_q10"),
                                   ("_v2", "edge_shares_q90")]) is None

    def test_the_median_arm_writes_the_unsuffixed_artifact_names(self):
        # The subset entry is stored under these names because that is what a
        # q50-only window looks for when it assembles. A suffix here would
        # store a day no map request can read.
        suffix, _key = bsd.median_variant(bsd.direction_variants(True))
        assert f"calibrated{suffix}.rou.xml" == "calibrated.rou.xml"
        assert f"calibrated{suffix}.agents.json" == "calibrated.agents.json"
        assert f"fit{suffix}.json" == "fit.json"


class TestTheIdentityBridge:
    """Only the variant axis is bridged; everything else still separates."""

    @staticmethod
    def _identity(variants, **overrides):
        inputs = {
            "constraints": "c0",
            "purpose_shares": "p0",
            "through_share_target": 0.25,
            "candidate_pool": "pool",
            "candidate_metadata": "meta",
            "edge_geometry": "geo",
            "variants": variants,
            "picker_runtime": {"numpy": "2.0.2"},
        }
        inputs.update(overrides.pop("inputs", {}))
        return DayIdentity(
            date=overrides.pop("date", "2027-05-26"),
            source=overrides.pop("source", "forecast"),
            pool_composition=overrides.pop("pool_composition", ("weekday",)),
            inputs=inputs,
            source_hashes={"pfe": "abc"},
        )

    def test_the_variant_list_alone_changes_the_key(self):
        stress = self._identity(["edge_shares", "edge_shares_q10",
                                 "edge_shares_q90"])
        median = self._identity(["edge_shares"])
        assert stress.key != median.key

    def test_pool_composition_still_separates_a_shared_arm(self):
        # A date calibrated beside a weekend saw a bigger shape pool. That is
        # a different, equally correct result and must never be served for a
        # single-day request, variant subset or not.
        alone = self._identity(["edge_shares"])
        mixed = self._identity(["edge_shares"],
                               pool_composition=("weekday", "weekend"))
        assert alone.key != mixed.key

    def test_the_median_constraints_are_what_the_subset_is_keyed_on(self):
        # Restricting the constraints digest is the whole mechanism: a subset
        # identity that kept the three-arm digest could never equal the one a
        # q50-only build computes.
        three_arm = self._identity(["edge_shares"], inputs={"constraints": "c3"})
        one_arm = self._identity(["edge_shares"], inputs={"constraints": "c1"})
        assert three_arm.key != one_arm.key


class TestTheStoredEvidence:
    """The sharing is only allowed because a measurement says the bytes agree."""

    def test_the_identity_record_exists_and_passes(self):
        record = json.loads(RECORD.read_text())
        assert record["result"] == "PASS"
        assert record["subset_key_equals_genuine_key"] is True
        assert record["identity_recorded_equals_genuine_identity"] is True

    def test_the_record_claims_identity_for_the_published_demand(self):
        identical = json.loads(RECORD.read_text())["artifacts_identical"]
        for artifact in ("calibrated.rou.xml", "calibrated.agents.json",
                         "provenance.json"):
            assert identical[artifact] is True, artifact

    def test_the_record_does_not_overclaim_the_fit_report(self):
        # fit.json carries wall-clock timings that differ between ANY two
        # runs. The record must say so rather than assert byte identity it
        # cannot have.
        record = json.loads(RECORD.read_text())
        assert record["artifacts_identical"]["fit.json"] != True  # noqa: E712
        assert "timings_s" in record["fit_timings_note"]

    def test_the_end_to_end_leg_measured_a_real_hit(self):
        leg = json.loads(RECORD.read_text())["end_to_end"]
        assert leg["published_demand_matches_genuine_q50_build"] is True
        assert leg["warm_q50_from_subset_s"] < leg["cold_q50_only_s"]


class TestTheLibraryContract:
    """What the subset entry must satisfy to be usable at all."""

    def test_an_entry_is_found_by_key_and_verified_by_bytes(self, tmp_path):
        library = DayLibrary(tmp_path)
        identity = TestTheIdentityBridge._identity(["edge_shares"])
        route = tmp_path / "calibrated.rou.xml"
        route.write_text("<routes/>")
        library.put(identity, {"calibrated.rou.xml": route}, fit={"vehicles": 1})

        assert library.get(identity) is not None
        stored = library.path_for(identity) / "calibrated.rou.xml.gz"
        stored.write_bytes(b"tampered")
        assert library.get(identity) is None, (
            "an altered entry must read as absent, not as a day")

    def test_a_subset_entry_is_not_returned_for_the_stress_identity(self,
                                                                   tmp_path):
        library = DayLibrary(tmp_path)
        median = TestTheIdentityBridge._identity(["edge_shares"])
        route = tmp_path / "calibrated.rou.xml"
        route.write_text("<routes/>")
        library.put(median, {"calibrated.rou.xml": route}, fit={})

        stress = TestTheIdentityBridge._identity(
            ["edge_shares", "edge_shares_q10", "edge_shares_q90"])
        assert library.get(stress) is None, (
            "sharing is one-way: a q50 day cannot answer a three-arm request")


@pytest.mark.parametrize("name", ["calibrated.rou.xml", "calibrated.agents.json",
                                  "fit.json"])
def test_the_subset_stores_the_names_the_assembler_reads(name):
    source = Path("build_sumo_demand.py").read_text()
    block = source.split("def _store_q50_subset")[1].split("def _calibrate_one_day")[0]
    assert f'"{name}"' in block, (
        f"{name} is what a q50-only window assembles from; the subset entry "
        "must store it under exactly that name")
