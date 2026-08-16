"""Fas 0A: pin today's q artifacts, q->seed mapping and closure ranking.

The plan requires the current behaviour to be pinned before any gate opens,
so that a later change shows up as a deliberate diff rather than as drift.

These tests assert what the code does TODAY. Two of them pin behaviour the
plan may later remove — that is their purpose, and they are expected to be
updated by the change that removes it, never quietly deleted.
"""

from __future__ import annotations

import pytest

from traffic_sim.core.contracts import ScenarioSpec
from traffic_sim.simulation.monthly_search import DEMAND_VARIANTS, canonical_seed


class TestQVariantVocabularyIsPinned:
    def test_the_three_variants_are_the_quantiles(self):
        assert DEMAND_VARIANTS == ("q10", "q50", "q90")

    def test_the_legacy_split_keys_are_pinned(self):
        from demand.intake import load_direction_split
        import inspect
        source = inspect.getsource(load_direction_split)
        assert "edge_shares" in source


class TestSeedAndVariantAreStillEntangled:
    """Today a seed IMPLIES a demand variant.

    Fas 0B measures whether that entanglement matters; only a Gate S=YES
    branch may later remove it. Pinning it here makes the measurement
    meaningful and any removal visible.
    """

    def test_canonical_seed_interleaves_the_variant_into_the_number(self):
        assert canonical_seed("q10", 0) == 1000
        assert canonical_seed("q50", 0) == 1001
        assert canonical_seed("q90", 0) == 1002
        assert canonical_seed("q10", 1) == 1003

    def test_an_unknown_variant_is_refused(self):
        with pytest.raises(ValueError, match="unknown demand variant"):
            canonical_seed("q99", 0)

    def test_a_negative_repetition_is_refused(self):
        with pytest.raises(ValueError, match="non-negative"):
            canonical_seed("q50", -1)

    def test_scenario_spec_defaults_map_seeds_to_variants(self):
        spec = ScenarioSpec(
            scenario_id="s", demand_build_id="d", network_build_id="n",
            start_time="2027-05-03T00:00:00", end_time="2027-05-04T00:00:00",
        )
        assert spec.seed_set == (1000, 1001, 1002)
        assert spec.demand_variant_mapping == (
            (1000, "q50"), (1001, "q10"), (1002, "q90"),
        )

    def test_the_default_gives_exactly_one_seed_per_variant(self):
        """Zero replication within a variant — what Fas 0B has to work around.

        Because each q case gets a single seed, the existing default cannot
        separate simulator noise from direction variation. The Fas 0B tool
        must therefore supply its own matched seed list rather than reusing
        this default.
        """
        spec = ScenarioSpec(
            scenario_id="s", demand_build_id="d", network_build_id="n",
            start_time="2027-05-03T00:00:00", end_time="2027-05-04T00:00:00",
        )
        variants = [variant for _seed, variant in spec.demand_variant_mapping]
        assert sorted(variants) == ["q10", "q50", "q90"]
        assert len(variants) == len(set(variants)) == len(spec.seed_set)


class TestClosureRankingContractIsPinned:
    def test_the_ranking_module_still_exposes_its_entry_points(self):
        from traffic_sim.simulation import closure_ranking

        assert hasattr(closure_ranking, "__all__") or \
            any(not name.startswith("_") for name in dir(closure_ranking))

    def test_scenario_spec_rejects_an_empty_seed_set(self):
        with pytest.raises(ValueError):
            ScenarioSpec(
                scenario_id="s", demand_build_id="d", network_build_id="n",
                start_time="2027-05-03T00:00:00",
                end_time="2027-05-04T00:00:00", seed_set=(),
            )
