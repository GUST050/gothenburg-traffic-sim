"""Step 4 acceptance: coherent days, pair-exact shares, honest path labels.

Plan acceptance for step 4:
  * one demand-case id describes the whole edge x time matrix for a day, and
    one path id binds every day of a multi-day closure;
  * reproducibility and content digests are locked;
  * marginal and joint scores are compared against simple baselines.

The pair-exactness tests are also the regression for the defect recorded in
`dirsplit/legacy.py`: the legacy writer perturbed both directions
independently, so its outer arms did not sum to 1.
"""

from __future__ import annotations

import numpy as np
import pytest

from dirsplit import scenario_evaluation as se
from dirsplit import scenarios as sc
from dirsplit import schema
from dirsplit.dataset_schema import make_row
from dirsplit.models import ShrunkDFactor


PAIRS = [("A", "B")]


def central(value=0.52):
    return {"A": [value] * schema.SLOT_COUNT,
            "B": [1.0 - value] * schema.SLOT_COUNT}


def block(city="oslo", date="2025-09-01", station="S1", amount=0.3):
    return sc.ResidualBlock(
        city=city, date=date,
        residuals={(station, hour): amount for hour in range(24)},
    )


def library(n=6, city_cycle=("oslo", "bergen")):
    out = []
    for index in range(n):
        city = city_cycle[index % len(city_cycle)]
        out.append(block(city=city,
                         date=f"2025-09-{(index // len(city_cycle)) + 1:02d}",
                         amount=0.1 * (index + 1)))
    return out


# ── the repair: pair-exactness ────────────────────────────────────────────
class TestPairExactness:
    def test_the_partner_is_derived_not_perturbed(self):
        payload = sc.apply_block(central(), PAIRS, block(), {"A": "S1"})
        for slot in range(schema.SLOT_COUNT):
            assert payload["A"][slot] + payload["B"][slot] == pytest.approx(
                1.0, abs=1e-12
            )

    def test_a_large_residual_still_sums_to_one(self):
        payload = sc.apply_block(central(), PAIRS, block(amount=3.0),
                                 {"A": "S1"})
        assert payload["A"][0] + payload["B"][0] == pytest.approx(1.0, abs=1e-12)

    def test_the_generated_case_passes_the_schema_pair_check(self):
        cases = sc.generate_cases(central(), PAIRS, library(),
                                  {"A": "S1"}, n_cases=3)
        assert len(cases) == 3
        for case in cases:                     # DemandCase validates on build
            assert case.edge_pairs == tuple(PAIRS)

    def test_the_legacy_construction_would_fail_this_check(self):
        """Regression for the defect in dirsplit/legacy.py.

        Perturbing both directions independently — the legacy q10/q90 shape —
        cannot satisfy the identity, and the schema now refuses it.
        """
        s10, s90 = 0.44, 0.55
        bad = {"A": [s10] * schema.SLOT_COUNT,
               "B": [1 - s90] * schema.SLOT_COUNT}
        with pytest.raises(schema.SchemaError, match="must sum to 1.0"):
            schema.DemandCase(
                case_id="dc_bad", kind="stress", day_type="weekday",
                shares=bad, edge_pairs=tuple(PAIRS), generator="legacy_q10",
            )


# ── whole-day coherence ───────────────────────────────────────────────────
class TestOneCaseIsOneWholeDay:
    def test_a_case_covers_every_edge_and_every_slot(self):
        cases = sc.generate_cases(central(), PAIRS, library(),
                                  {"A": "S1"}, n_cases=2)
        for case in cases:
            assert set(case.edges()) == {"A", "B"}
            for series in case.shares.values():
                assert len(series) == schema.SLOT_COUNT

    def test_all_edges_of_one_case_come_from_one_donor_day(self):
        cases = sc.generate_cases(central(), PAIRS, library(),
                                  {"A": "S1"}, n_cases=3)
        for case in cases:
            assert "donor_block_id" in case.provenance
            assert case.provenance["donor_block_id"].count(":") == 1

    def test_weights_are_probabilistic_and_sum_to_exactly_one(self):
        cases = sc.generate_cases(central(), PAIRS, library(),
                                  {"A": "S1"}, n_cases=3)
        assert all(c.is_probabilistic for c in cases)
        assert sum(c.weight for c in cases) == pytest.approx(1.0, abs=1e-12)

    def test_the_ensemble_accepts_the_generated_weights(self):
        cases = sc.generate_cases(central(), PAIRS, library(),
                                  {"A": "S1"}, n_cases=3)
        ensemble = schema.DirectionEnsemble(
            model_digest="m", training_data_digest="t",
            calibration_digest="c", central_profiles=(),
            marginal_intervals=None, demand_cases=tuple(cases),
        )
        assert len(ensemble.demand_cases) == 3


# ── determinism and outcome-blindness ─────────────────────────────────────
class TestDeterministicOutcomeBlindSelection:
    def test_the_same_inputs_give_the_same_case_ids(self):
        first = sc.generate_cases(central(), PAIRS, library(), {"A": "S1"},
                                  n_cases=3)
        second = sc.generate_cases(central(), PAIRS, library(), {"A": "S1"},
                                   n_cases=3)
        assert [c.case_id for c in first] == [c.case_id for c in second]

    def test_a_different_selection_key_changes_the_draw(self):
        a = sc.select_blocks(library(8), 3, "key-a")
        b = sc.select_blocks(library(8), 3, "key-b")
        assert [x.block_id for x in a] != [x.block_id for x in b]

    def test_selection_stratifies_across_cities(self):
        chosen = sc.select_blocks(library(8), 4, "k")
        assert len({b.city for b in chosen}) == 2

    def test_selection_never_sees_an_outcome(self):
        # The only inputs are the library and a key naming inputs.
        import inspect
        source = inspect.getsource(sc.select_blocks)
        for forbidden in ("time_loss", "objective", "winner", "result"):
            assert forbidden not in source


# ── multi-day paths ───────────────────────────────────────────────────────
class TestMultiDayPaths:
    def build_cases(self, lib):
        cases = {}
        for blk in lib:
            payload = sc.apply_block(central(), PAIRS, blk, {"A": "S1"})
            case = schema.DemandCase(
                case_id=schema.make_case_id(generator="g", day_type="weekday",
                                            shares=payload,
                                            provenance={"b": blk.block_id}),
                kind="probabilistic", day_type="weekday", shares=payload,
                edge_pairs=tuple(PAIRS), generator="g", weight=1.0,
            )
            cases[blk.block_id] = case
        return cases

    def test_a_contiguous_run_is_preferred(self):
        lib = [block(city="oslo", date=f"2025-09-{d:02d}", amount=0.1 * d)
               for d in range(1, 6)]
        paths, method = sc.build_paths(self.build_cases(lib), lib,
                                       ["2027-05-03", "2027-05-04"],
                                       n_paths=2)
        assert method == "contiguous_observed_run"
        assert paths and all(p.horizon_days == 2 for p in paths)

    def test_one_path_id_binds_every_day(self):
        lib = [block(city="oslo", date=f"2025-09-{d:02d}", amount=0.1 * d)
               for d in range(1, 6)]
        paths, _method = sc.build_paths(self.build_cases(lib), lib,
                                        ["2027-05-03", "2027-05-04",
                                         "2027-05-05"], n_paths=1)
        assert len(paths) == 1
        path = paths[0]
        assert path.horizon_days == 3
        assert path.case_for_date("2027-05-04") == path.case_ids[1]

    def test_independent_sampling_is_labelled_as_a_baseline(self):
        # No contiguous run of 3 exists across single isolated dates.
        lib = [block(city="oslo", date="2025-09-01", amount=0.1),
               block(city="bergen", date="2025-11-05", amount=0.2)]
        paths, method = sc.build_paths(self.build_cases(lib), lib,
                                       ["2027-05-03", "2027-05-04",
                                        "2027-05-05"], n_paths=1)
        assert method == sc.INDEPENDENT_GENERATOR
        assert paths and paths[0].generator_version == sc.INDEPENDENT_GENERATOR

    def test_contiguous_runs_require_consecutive_dates(self):
        lib = [block(date="2025-09-01"), block(date="2025-09-03")]
        assert sc.contiguous_runs(lib, 2) == []

    def test_runs_do_not_span_cities(self):
        lib = [block(city="oslo", date="2025-09-01"),
               block(city="bergen", date="2025-09-02")]
        assert sc.contiguous_runs(lib, 2) == []


# ── residual library ──────────────────────────────────────────────────────
class TestResidualLibrary:
    def rows(self):
        out = []
        for city, base in (("oslo", 0.55), ("bergen", 0.45)):
            for day in range(1, 6):
                for hour in range(6, 21):
                    out.append(make_row(
                        station_id=f"{city}-S", city=city, heading="C",
                        timestamp=f"2025-09-{day:02d}T{hour:02d}:00:00",
                        toward_count=round(500 * base),
                        away_count=500 - round(500 * base),
                        coverage_pct=100.0,
                        features={"radial_cos": 0.9}, profile={},
                    ))
        return out

    def test_blocks_are_whole_city_dates(self):
        from dirsplit.evaluate import leave_city_out
        lib = sc.build_residual_library(self.rows(), ShrunkDFactor(),
                                        leave_city_out)
        assert lib
        for blk in lib:
            assert blk.block_id == f"{blk.city}:{blk.date}"

    def test_residuals_are_held_out_not_in_sample(self):
        from dirsplit.evaluate import leave_city_out
        lib = sc.build_residual_library(self.rows(), ShrunkDFactor(),
                                        leave_city_out)
        # Oslo residuals come from a model fit on Bergen only, so they are
        # not zero the way an in-sample fit would make them.
        oslo = [b for b in lib if b.city == "oslo"]
        assert oslo
        values = [v for b in oslo for v in b.residuals.values()]
        assert max(abs(v) for v in values) > 1e-6

    def test_a_sparse_station_yields_no_hourly_series(self):
        sparse = sc.ResidualBlock("oslo", "2025-09-01",
                                  {("S1", 8): 0.1, ("S1", 9): 0.2})
        assert sparse.hourly_for("S1") is None

    def test_missing_hours_are_interpolated_not_zero_filled(self):
        partial = sc.ResidualBlock(
            "oslo", "2025-09-01",
            {("S1", 6): 1.0, ("S1", 12): 1.0, ("S1", 18): 1.0},
        )
        series = partial.hourly_for("S1")
        assert series is not None
        # A zero fill would leave hour 0 at 0.0; interpolation carries 1.0.
        assert series[0] == pytest.approx(1.0)


# ── joint scoring ─────────────────────────────────────────────────────────
class TestJointScores:
    def test_energy_score_is_lower_for_a_closer_ensemble(self):
        truth = np.array([0.5, 0.5, 0.5])
        close = np.array([[0.51, 0.49, 0.5], [0.49, 0.51, 0.5]])
        far = np.array([[0.9, 0.1, 0.5], [0.1, 0.9, 0.5]])
        assert se.energy_score(close, truth) < se.energy_score(far, truth)

    def test_variogram_score_detects_wrong_correlation(self):
        """Identical marginals, opposite dependence — the discriminating case."""
        rng = np.random.default_rng(0)
        n = 400
        common = rng.normal(0, 1, n)
        correlated = np.stack([common, common], axis=1)
        independent = np.stack([common, rng.normal(0, 1, n)], axis=1)
        truth = np.array([1.5, 1.5])          # a jointly-high observation
        vs_corr = se.variogram_score(correlated, truth)
        vs_indep = se.variogram_score(independent, truth)
        assert vs_corr < vs_indep

    def test_variogram_score_is_zero_for_a_perfect_match(self):
        truth = np.array([0.2, 0.8])
        ensemble = np.array([[0.2, 0.8]] * 5)
        assert se.variogram_score(ensemble, truth) == pytest.approx(0.0)

    def test_dimension_mismatch_is_refused(self):
        with pytest.raises(ValueError, match="dimension"):
            se.energy_score(np.zeros((3, 2)), np.zeros(5))

    def test_diagnostics_report_autocorrelation_and_cross_correlation(self):
        member = {"A": list(np.linspace(0.4, 0.6, 96)),
                  "B": list(np.linspace(0.6, 0.4, 96))}
        diag = se.dependence_diagnostics([member])
        assert diag.n_members == 1 and diag.n_dimensions == 2
        assert diag.lag1_autocorrelation > 0.9
        assert diag.cross_series_correlation < -0.9   # mirrored pair

    def test_baseline_comparison_names_the_discriminating_score(self):
        rng = np.random.default_rng(1)
        truth = np.array([1.0, 1.0])
        common = rng.normal(0, 1, 200)
        joint = np.stack([common, common], axis=1)
        independent = np.stack([common, rng.normal(0, 1, 200)], axis=1)
        result = se.compare_against_baseline(joint, independent, truth)
        assert result["joint_beats_baseline"]
        assert "variogram_score" in result["note"]


# ── the artifact ──────────────────────────────────────────────────────────
class TestEnsembleArtifact:
    def build(self):
        from dirsplit import ensemble as en
        cases = sc.generate_cases(central(), PAIRS, library(), {"A": "S1"},
                                  n_cases=3)
        profile = schema.CentralProfile(
            model_id="constant_5050", day_type="weekday",
            shares=central(0.5), edge_pairs=tuple(PAIRS),
        )
        return en, en.build_ensemble(
            central_profiles=[profile], demand_cases=cases,
            model_digest="m", training_data_digest="t",
            calibration_digest="c",
        )

    def test_it_writes_and_reads_back(self, tmp_path):
        en, ensemble = self.build()
        path = tmp_path / "direction_ensemble_v2.json"
        payload = en.write_ensemble(ensemble, path)
        assert payload["schema_version"] == "direction_ensemble_v2"
        loaded = en.load_ensemble(path)
        assert loaded is not None
        assert loaded["artifact_digest"] == payload["artifact_digest"]

    def test_it_refuses_to_clobber_an_existing_artifact(self, tmp_path):
        en, ensemble = self.build()
        path = tmp_path / "direction_ensemble_v2.json"
        en.write_ensemble(ensemble, path)
        with pytest.raises(FileExistsError, match="already exists"):
            en.write_ensemble(ensemble, path)

    def test_an_edited_artifact_fails_its_digest(self, tmp_path):
        import json as _json
        en, ensemble = self.build()
        path = tmp_path / "direction_ensemble_v2.json"
        en.write_ensemble(ensemble, path)
        payload = _json.loads(path.read_text())
        payload["slot_minutes"] = 15
        payload["demand_cases"][0]["weight"] = 0.99
        path.write_text(_json.dumps(payload))
        with pytest.raises(ValueError, match="digest mismatch"):
            en.load_ensemble(path)

    def test_a_missing_artifact_is_none(self, tmp_path):
        from dirsplit import ensemble as en
        assert en.load_ensemble(tmp_path / "nope.json") is None

    def test_the_summary_never_claims_uncalibrated_coverage(self):
        en, ensemble = self.build()
        summary = en.summarise(ensemble)
        assert summary["interval_status"] == "unavailable"
        assert not summary["has_calibrated_intervals"]
        assert "80" not in summary["interval_label"]
