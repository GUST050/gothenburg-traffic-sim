"""Step 0 contract tests: the v2 vocabulary and the legacy adapter.

These pin the plan's invariants as executable rules. They are deliberately
dependency-free (no numpy, LightGBM, osmnx or SUMO) so they stay in the fast
tier and can run on a clean checkout.
"""

from __future__ import annotations

import json

import pytest

from dirsplit import legacy, schema


# ── helpers ───────────────────────────────────────────────────────────────
def flat(value: float) -> list[float]:
    return [value] * schema.SLOT_COUNT


def pair_shares(a_value: float, edge_a: str = "A", edge_b: str = "B"):
    return {edge_a: flat(a_value), edge_b: flat(1.0 - a_value)}


PAIRS = (("A", "B"),)


def make_case(kind="probabilistic", weight=1.0, value=0.5, generator="test"):
    shares = pair_shares(value)
    return schema.DemandCase(
        case_id=schema.make_case_id(
            generator=generator, day_type="weekday", shares=shares
        ),
        kind=kind,
        day_type="weekday",
        shares=shares,
        edge_pairs=PAIRS,
        generator=generator,
        weight=weight,
    )


# ── plan invariant 1: directed pairs sum to 1 ─────────────────────────────
class TestPairSumIdentity:
    def test_a_valid_pair_is_accepted(self):
        profile = schema.CentralProfile(
            model_id="m", day_type="weekday",
            shares=pair_shares(0.52), edge_pairs=PAIRS,
        )
        assert profile.share_at("A", 0) == pytest.approx(0.52)
        assert profile.share_at("B", 0) == pytest.approx(0.48)

    def test_a_pair_that_does_not_sum_to_one_is_refused(self):
        bad = {"A": flat(0.44), "B": flat(0.45)}     # sums to 0.89
        with pytest.raises(schema.SchemaError, match="must sum to 1.0"):
            schema.CentralProfile(
                model_id="m", day_type="weekday",
                shares=bad, edge_pairs=PAIRS,
            )

    def test_a_demand_case_enforces_the_same_identity(self):
        bad = {"A": flat(0.55), "B": flat(0.56)}     # sums to 1.11
        with pytest.raises(schema.SchemaError, match="must sum to 1.0"):
            schema.DemandCase(
                case_id="dc_x", kind="stress", day_type="weekday",
                shares=bad, edge_pairs=PAIRS, generator="test",
            )

    def test_an_edge_cannot_appear_in_two_pairs(self):
        shares = {"A": flat(0.5), "B": flat(0.5), "C": flat(0.5)}
        with pytest.raises(schema.SchemaError, match="more than one pair"):
            schema.CentralProfile(
                model_id="m", day_type="weekday", shares=shares,
                edge_pairs=(("A", "B"), ("A", "C")),
            )


# ── plan invariant 5: a stress case never carries probability mass ────────
class TestStressCasesHaveNoWeight:
    def test_a_stress_case_with_a_weight_is_refused(self):
        with pytest.raises(schema.SchemaError, match="no probability mass"):
            make_case(kind="stress", weight=0.25)

    def test_a_stress_case_without_a_weight_is_fine(self):
        case = make_case(kind="stress", weight=None)
        assert case.weight is None
        assert not case.is_probabilistic

    def test_a_probabilistic_case_requires_a_weight(self):
        with pytest.raises(schema.SchemaError, match="needs a weight"):
            make_case(kind="probabilistic", weight=None)

    def test_serialised_stress_case_has_no_weight_key_at_all(self):
        # A null weight could be read as "zero" by a lenient consumer; the
        # key must be absent instead.
        case = make_case(kind="stress", weight=None)
        payload = schema._case_json(case)
        assert "weight" not in payload

    def test_ensemble_refuses_a_stress_case_among_demand_cases(self):
        stress = make_case(kind="stress", weight=None, value=0.4)
        with pytest.raises(schema.SchemaError, match="must all be probabilistic"):
            schema.DirectionEnsemble(
                model_digest="m", training_data_digest="t",
                calibration_digest="c", central_profiles=(),
                marginal_intervals=None, demand_cases=(stress,),
            )

    def test_probabilistic_weights_must_sum_to_one(self):
        a = make_case(weight=0.5, value=0.48, generator="a")
        b = make_case(weight=0.4, value=0.52, generator="b")
        with pytest.raises(schema.SchemaError, match="weights sum to"):
            schema.DirectionEnsemble(
                model_digest="m", training_data_digest="t",
                calibration_digest="c", central_profiles=(),
                marginal_intervals=None, demand_cases=(a, b),
            )


# ── plan step 0 acceptance: a seed cannot imply a demand case ─────────────
class TestSeedCannotImplyACase:
    def test_simulation_member_requires_both_coordinates(self):
        with pytest.raises(TypeError):
            schema.SimulationMember(simulation_seed=1000)   # type: ignore[call-arg]

    def test_seed_list_takes_no_variant_argument(self):
        seeds = schema.seed_list(1000, 4)
        assert seeds == (1000, 1001, 1002, 1003)

    def test_matched_pair_shares_path_and_seed(self):
        base, cand = schema.matched_pair("sp_abc", 1000)
        assert base.scenario_path_id == cand.scenario_path_id == "sp_abc"
        assert base.simulation_seed == cand.simulation_seed == 1000
        assert base.arm == "baseline" and cand.arm == "candidate"

    def test_the_same_seed_can_carry_any_case(self):
        # The defining property: identical seed, different demand worlds.
        one = schema.SimulationMember("sp_one", 1000)
        two = schema.SimulationMember("sp_two", 1000)
        assert one.simulation_seed == two.simulation_seed
        assert one.member_id != two.member_id

    def test_case_ids_are_content_addressed_not_ordinal(self):
        case = make_case()
        assert case.case_id.startswith("dc_")
        # Nothing about a case id can be derived from a seed number.
        assert "1000" not in case.case_id or len(case.case_id) > 8

    def test_identical_content_yields_identical_case_id(self):
        assert make_case().case_id == make_case().case_id

    def test_different_shares_yield_different_case_id(self):
        assert make_case(value=0.5).case_id != make_case(value=0.51).case_id

    def test_legacy_seed_mapping_is_pinned_for_comparison(self):
        # Pins what step 6 removes, so a regression is visible.
        assert legacy.legacy_variant_for_seed(1000) == "q50"
        assert legacy.legacy_variant_for_seed(1001) == "q10"
        assert legacy.legacy_variant_for_seed(1002) == "q90"
        assert legacy.legacy_variant_for_seed(1003) is None


# ── calibration labels ────────────────────────────────────────────────────
class TestIntervalCannotClaimUnmeasuredCoverage:
    def build(self, **kwargs):
        base = dict(
            lower={"A": flat(0.44), "B": flat(0.45)},
            upper={"A": flat(0.55), "B": flat(0.56)},
            nominal_coverage=0.8,
        )
        base.update(kwargs)
        return schema.MarginalInterval(**base)

    def test_default_status_is_stress_only(self):
        assert self.build().calibration_status == "stress_only"

    def test_calibrated_requires_a_measured_coverage(self):
        with pytest.raises(schema.SchemaError, match="requires a measured"):
            self.build(calibration_status="calibrated")

    def test_calibrated_with_measurement_is_allowed(self):
        interval = self.build(calibration_status="calibrated",
                              empirical_coverage=0.79)
        assert interval.is_probability_statement

    def test_uncalibrated_label_does_not_say_eighty_percent(self):
        label = self.build().label()
        assert "80" not in label
        assert "stress" in label.lower()

    def test_crossing_interval_is_refused(self):
        with pytest.raises(schema.SchemaError, match="interval crossing"):
            self.build(lower={"A": flat(0.6), "B": flat(0.6)},
                       upper={"A": flat(0.4), "B": flat(0.4)})


# ── applicability: weak evidence widens, only errors fail closed ──────────
class TestApplicabilityDoesNotBanRoads:
    def build(self, **kwargs):
        base = dict(
            edge_id="A", measurement_level="transfer_prior",
            static_domain="good", temporal_support="good",
            feature_compatibility="good", effective_sample_size=10.0,
            calibration_support="good",
        )
        base.update(kwargs)
        return schema.Applicability(**base)

    def test_good_evidence_permits_normal_claims(self):
        assert self.build().claim_strength() == "normal"
        assert not self.build().blocks_publication

    def test_limited_evidence_widens_but_does_not_block(self):
        weak = self.build(temporal_support="limited")
        assert weak.claim_strength() == "widened"
        assert not weak.blocks_publication

    def test_extrapolation_falls_back_but_does_not_block(self):
        far = self.build(static_domain="extrapolation")
        assert far.claim_strength() == "conservative_fallback"
        assert not far.blocks_publication

    def test_only_an_input_error_fails_closed(self):
        broken = self.build(feature_compatibility="unavailable")
        assert broken.blocks_publication
        assert broken.claim_strength() == "unavailable"

    def test_grade_is_the_worst_dimension(self):
        mixed = self.build(static_domain="good", temporal_support="limited",
                           calibration_support="extrapolation")
        assert mixed.grade() == "extrapolation"


# ── scenario paths: one id across a whole horizon ─────────────────────────
class TestScenarioPath:
    def test_path_binds_dates_to_cases_in_order(self):
        path = schema.ScenarioPath(
            path_id="sp_x", case_ids=("dc_a", "dc_b"),
            calendar_dates=("2027-05-03", "2027-05-04"),
            generator_version="blocked_residual_path_v1",
        )
        assert path.horizon_days == 2
        assert path.case_for_date("2027-05-04") == "dc_b"

    def test_dates_must_be_ascending(self):
        with pytest.raises(schema.SchemaError, match="ascending"):
            schema.ScenarioPath(
                path_id="sp_x", case_ids=("dc_a", "dc_b"),
                calendar_dates=("2027-05-04", "2027-05-03"),
                generator_version="v1",
            )

    def test_lengths_must_match(self):
        with pytest.raises(schema.SchemaError, match="same length"):
            schema.ScenarioPath(
                path_id="sp_x", case_ids=("dc_a",),
                calendar_dates=("2027-05-03", "2027-05-04"),
                generator_version="v1",
            )

    def test_ensemble_refuses_a_path_naming_an_unknown_case(self):
        case = make_case()
        path = schema.ScenarioPath(
            path_id="sp_x", case_ids=("dc_missing",),
            calendar_dates=("2027-05-03",), generator_version="v1",
        )
        with pytest.raises(schema.SchemaError, match="unknown cases"):
            schema.DirectionEnsemble(
                model_digest="m", training_data_digest="t",
                calibration_digest="c", central_profiles=(),
                marginal_intervals=None, demand_cases=(case,),
                scenario_paths=(path,),
            )


# ── the legacy pair-sum defect ────────────────────────────────────────────
class TestLegacyPairSumDefect:
    """The defect found on 2026-08-13, pinned so it cannot return silently.

    `dirsplit/predict.py` writes q10 as (e0=s10, e1=1-s90) and q90 as
    (e0=s90, e1=1-s10). Neither sums to 1, so at a two-way sensor the level-1
    targets lose or gain the interval width in total volume.
    """

    @staticmethod
    def legacy_block(s10, s50, s90, e0="A", e1="B"):
        return {
            "107": {
                "edge_shares": {e0: flat(s50), e1: flat(1 - s50)},
                "edge_shares_q10": {e0: flat(s10), e1: flat(1 - s90)},
                "edge_shares_q90": {e0: flat(s90), e1: flat(1 - s10)},
                "coverage_status": {e0: "ok", e1: "ok"},
            }
        }

    def write(self, tmp_path, payload):
        path = tmp_path / "direction_split.json"
        path.write_text(json.dumps(payload))
        return path

    def test_the_q50_arm_satisfies_the_identity(self, tmp_path):
        path = self.write(tmp_path, self.legacy_block(0.44, 0.50, 0.55))
        archive = legacy.load_legacy_archive(path)
        assert archive is not None
        assert not [d for d in archive.pair_sum_defects if d.variant == "q50"]

    def test_the_q10_and_q90_arms_violate_it(self, tmp_path):
        path = self.write(tmp_path, self.legacy_block(0.44, 0.50, 0.55))
        archive = legacy.load_legacy_archive(path)
        assert archive is not None
        assert not archive.is_pair_sum_valid
        summary = archive.defect_summary()
        assert set(summary) == {"q10", "q90"}
        # width = 0.55 - 0.44 = 0.11 -> q10 sums to 0.89, q90 to 1.11
        assert summary["q10"]["mean_deficit"] == pytest.approx(-0.11, abs=1e-9)
        assert summary["q90"]["mean_deficit"] == pytest.approx(+0.11, abs=1e-9)
        assert summary["q10"]["slots"] == schema.SLOT_COUNT

    def test_the_deficit_equals_the_interval_width(self, tmp_path):
        for s10, s90 in ((0.45, 0.55), (0.40, 0.62), (0.49, 0.51)):
            path = self.write(tmp_path, self.legacy_block(s10, 0.50, s90))
            archive = legacy.load_legacy_archive(path)
            assert archive is not None
            summary = archive.defect_summary()
            assert summary["q10"]["mean_deficit"] == pytest.approx(
                -(s90 - s10), abs=1e-9
            )

    def test_a_degenerate_interval_has_no_defect(self, tmp_path):
        # s10 == s90 is the only case where the old construction is valid.
        path = self.write(tmp_path, self.legacy_block(0.5, 0.5, 0.5))
        archive = legacy.load_legacy_archive(path)
        assert archive is not None
        assert archive.is_pair_sum_valid

    def test_legacy_arms_load_as_stress_cases_without_weight(self, tmp_path):
        path = self.write(tmp_path, self.legacy_block(0.44, 0.50, 0.55))
        archive = legacy.load_legacy_archive(path)
        assert archive is not None
        assert len(archive.stress_cases) == 2
        for case in archive.stress_cases:
            assert case.kind == "stress"
            assert case.weight is None

    def test_the_adapter_never_writes_to_the_archive(self, tmp_path):
        payload = self.legacy_block(0.44, 0.50, 0.55)
        path = self.write(tmp_path, payload)
        before = path.read_bytes()
        legacy.load_legacy_archive(path)
        assert path.read_bytes() == before

    def test_a_missing_archive_is_none_not_an_error(self, tmp_path):
        assert legacy.load_legacy_archive(tmp_path / "nope.json") is None

    def test_legacy_applicability_is_honest_about_its_own_limits(self, tmp_path):
        path = self.write(tmp_path, self.legacy_block(0.44, 0.50, 0.55))
        archive = legacy.load_legacy_archive(path)
        assert archive is not None
        for entry in archive.applicability:
            # trained weekdays 06-20, predicted all 24h with is_weekend=0
            assert entry.temporal_support == "extrapolation"
            # no coverage was ever measured for this archive
            assert entry.calibration_support == "unavailable"
            # a two-way total is not a directional measurement
            assert entry.measurement_level == "transfer_prior"


# ── serialisation ─────────────────────────────────────────────────────────
class TestEnsembleSerialisation:
    def test_round_trips_to_the_documented_shape(self):
        case = make_case(weight=1.0)
        profile = schema.CentralProfile(
            model_id="m", day_type="weekday",
            shares=pair_shares(0.5), edge_pairs=PAIRS,
        )
        ensemble = schema.DirectionEnsemble(
            model_digest="md", training_data_digest="td",
            calibration_digest="cd", central_profiles=(profile,),
            marginal_intervals=None, demand_cases=(case,),
        )
        payload = ensemble.to_json()
        assert payload["schema_version"] == "direction_ensemble_v2"
        assert payload["slot_minutes"] == 15
        assert payload["marginal_intervals"]["calibration_status"] == "unavailable"
        assert payload["demand_cases"][0]["weight"] == 1.0
        # serialisable without custom encoders
        json.dumps(payload)
