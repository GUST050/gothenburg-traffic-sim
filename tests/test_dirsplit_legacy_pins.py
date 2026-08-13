"""Step 0 acceptance: pin the CURRENT production behaviour before step 6.

The plan's step 0 asks for contract tests pinning today's
`direction_split.json` shape, the seed↔variant mapping and the legacy ranking,
so that when step 6 makes `demand_case_id` and `simulation_seed` orthogonal,
every place that used to conflate them shows up as a deliberate diff rather
than as silent drift.

These tests assert what the code does TODAY. They are expected to be updated
by step 6 — that update is the evidence the separation happened. They are not
assertions that today's behaviour is correct; two of them pin behaviour this
plan exists to remove.
"""

from __future__ import annotations

import pytest

from dirsplit import legacy, schema
from traffic_sim.core.contracts import ScenarioSpec
from traffic_sim.simulation.monthly_search import DEMAND_VARIANTS, canonical_seed


class TestLegacySeedVariantCoupling:
    """Today a seed IMPLIES a demand variant. Step 6 removes this."""

    def test_demand_variants_are_the_three_quantiles(self):
        assert DEMAND_VARIANTS == ("q10", "q50", "q90")

    def test_canonical_seed_interleaves_variant_into_the_seed_number(self):
        # The coupling in its clearest form: the variant is an *addend* of
        # the seed, so the two cannot be varied independently.
        assert canonical_seed("q10", 0) == 1000
        assert canonical_seed("q50", 0) == 1001
        assert canonical_seed("q90", 0) == 1002
        assert canonical_seed("q10", 1) == 1003

    def test_scenario_spec_default_maps_seeds_to_variants(self):
        spec = ScenarioSpec(
            scenario_id="s", demand_build_id="d", network_build_id="n",
            start_time="2027-05-03T00:00:00", end_time="2027-05-04T00:00:00",
        )
        assert spec.seed_set == (1000, 1001, 1002)
        assert spec.demand_variant_mapping == (
            (1000, "q50"), (1001, "q10"), (1002, "q90"),
        )

    def test_the_default_gives_exactly_one_seed_per_variant(self):
        """Zero replication within a variant — the confound this plan names.

        With three seeds spread across three variants, the published
        `confidence = spatial_prior * exp(-CV)` computes its CV over three
        observations drawn from three DIFFERENT demand worlds, so simulator
        noise and direction uncertainty cannot be separated.
        """
        spec = ScenarioSpec(
            scenario_id="s", demand_build_id="d", network_build_id="n",
            start_time="2027-05-03T00:00:00", end_time="2027-05-04T00:00:00",
        )
        variants = [variant for _seed, variant in spec.demand_variant_mapping]
        assert sorted(variants) == ["q10", "q50", "q90"]
        assert len(variants) == len(set(variants)) == len(spec.seed_set)

    def test_legacy_adapter_reproduces_the_same_mapping(self):
        for seed, variant in legacy.LEGACY_SEED_VARIANTS:
            assert legacy.legacy_variant_for_seed(seed) == variant


class TestV2RemovesTheCoupling:
    """The same three questions, asked of the v2 vocabulary."""

    def test_a_seed_alone_names_no_demand_case(self):
        # There is no v2 function taking a seed and returning a case.
        assert not hasattr(schema, "variant_for_seed")
        assert not hasattr(schema, "canonical_seed")

    def test_four_seeds_can_run_the_same_case(self):
        seeds = schema.seed_list(1000, 4)
        members = [schema.SimulationMember("sp_one", s) for s in seeds]
        assert len({m.scenario_path_id for m in members}) == 1
        assert len({m.simulation_seed for m in members}) == 4

    def test_one_seed_can_run_four_cases(self):
        paths = ["sp_a", "sp_b", "sp_c", "sp_d"]
        members = [schema.SimulationMember(p, 1000) for p in paths]
        assert len({m.simulation_seed for m in members}) == 1
        assert len({m.scenario_path_id for m in members}) == 4

    def test_baseline_and_candidate_share_the_random_draw(self):
        base, cand = schema.matched_pair("sp_a", 1000)
        assert (base.scenario_path_id, base.simulation_seed) == \
               (cand.scenario_path_id, cand.simulation_seed)


class TestLegacyArchiveShapeIsPinned:
    """The keys the old `direction_split.json` must keep while it is read."""

    def test_the_three_legacy_keys_are_known(self):
        assert legacy.LEGACY_KEYS == {
            "edge_shares": "q50",
            "edge_shares_q10": "q10",
            "edge_shares_q90": "q90",
        }

    def test_a_block_must_name_exactly_two_directed_edges(self):
        with pytest.raises(schema.SchemaError, match="exactly 2 directed edges"):
            legacy._pairs_from_block({"edge_shares": {"A": [0.5] * 96}})

    def test_the_real_archive_is_checked_when_present(self):
        """If a build intermediate exists, hold it to the identity.

        `sumo/` is gitignored, so this is a no-op on a clean checkout. When a
        developer HAS built demand, this test reports the pair-sum defect on
        the real artifact rather than only on a synthetic fixture.
        """
        archive = legacy.load_legacy_archive()
        if archive is None:
            pytest.skip("no sumo/direction_split.json in this checkout")
        summary = archive.defect_summary()
        # Document, don't assert absence: the current writer produces the
        # defect by construction, so this records its size for the report.
        assert isinstance(summary, dict)
        for variant, stats in summary.items():
            assert variant in ("q10", "q90"), (
                f"q50 must satisfy the pair-sum identity, but {variant} "
                f"reported {stats}"
            )
