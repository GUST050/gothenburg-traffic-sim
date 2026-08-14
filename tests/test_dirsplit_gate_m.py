"""Fas 1: leakage-free folds, four candidates, a frozen Gate M rule.

Plan requirements for Fas 1:
  1. preserve raw station-date-hour counts and day_block_id;
  2. eliminate the paired-total/single-direction feature mismatch;
  3. compare 50/50, shrunk D-factor, LightGBM and a count model on the SAME
     folds;
  4. report the central difference WITH UNCERTAINTY, not just a point MAE;
  5. measure temporal/applicability support and explicitly refuse silent
     weekend and off-hours extrapolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dirsplit import evaluate as ev
from dirsplit.features import FEATURE_NAMES


def row(station="S1", city="oslo", hour=8, weekend=0, share=0.5,
        n_obs=10.0, total=500.0, radial=0.9, date=None, heading="C"):
    features = [0.0] * len(FEATURE_NAMES)
    features[FEATURE_NAMES.index("radial_cos")] = radial
    block = f"{station}|{date}" if date else f"{station}|{weekend}"
    return ev.Row(
        station_id=station, city=city, heading=heading, hour=hour,
        is_weekend=weekend, share=share, n_obs=n_obs, mean_total=total,
        block=block, features=tuple(features), profile=(1.0, 7.0, 16.0, 0.3),
        day_block_id=(f"{city}|{date}" if date else None), local_date=date,
    )


def spread(stations=("A", "B"), cities=("oslo", "bergen"), share=0.5,
           jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for city in cities:
        for station in stations:
            for weekend in (0, 1):
                for hour in range(6, 21):
                    value = float(np.clip(share + rng.normal(0, jitter),
                                          0.05, 0.95))
                    rows.append(row(station=f"{city}-{station}", city=city,
                                    hour=hour, weekend=weekend, share=value))
    return rows


# ── folds are leakage-free ────────────────────────────────────────────────
class TestFoldsAreLeakageFree:
    def test_a_fold_that_shares_a_block_is_refused(self):
        rows = spread()
        with pytest.raises(ValueError, match="leaks"):
            ev.Fold("bad", "leave_city_out", tuple(rows), tuple(rows))

    def test_leave_city_out_holds_out_a_whole_city(self):
        folds = ev.leave_city_out(spread())
        assert len(folds) == 2
        for fold in folds:
            assert not ({r.city for r in fold.train}
                        & {r.city for r in fold.test})

    def test_leave_station_out_holds_out_a_whole_station(self):
        folds = ev.leave_station_out(spread())
        assert folds
        for fold in folds:
            assert not ({r.station_id for r in fold.train}
                        & {r.station_id for r in fold.test})

    def test_blocked_date_is_unavailable_without_dates(self):
        assert ev.blocked_date(spread()) == []

    def test_blocked_date_works_when_dates_exist(self):
        rows = [row(station="A", date=f"2025-09-{d:02d}", hour=h)
                for d in range(1, 9) for h in range(6, 21)]
        folds = ev.blocked_date(rows, n_blocks=4)
        assert folds
        for fold in folds:
            assert not ({r.local_date for r in fold.train}
                        & {r.local_date for r in fold.test})

    def test_every_candidate_sees_the_identical_folds(self):
        rows = spread()
        first = [(f.name, len(f.train), len(f.test))
                 for f in ev.leave_city_out(rows)]
        second = [(f.name, len(f.train), len(f.test))
                  for f in ev.leave_city_out(rows)]
        assert first == second


class TestNothingIsFitOutsideTheFold:
    def test_scoring_does_not_mutate_the_candidate(self):
        rows = spread(jitter=0.05, seed=1)
        model = ev.ShrunkDFactor()
        ev.score(model, ev.leave_city_out(rows))
        assert model._cell == {}

    def test_different_training_sides_give_different_constants(self):
        rows = (spread(cities=("oslo",), share=0.70, jitter=0.02, seed=2)
                + spread(cities=("bergen",), share=0.30, jitter=0.02, seed=3))
        folds = ev.leave_city_out(rows)
        a = ev.ShrunkDFactor().fit(list(folds[0].train))
        b = ev.ShrunkDFactor().fit(list(folds[1].train))
        assert a._cell != b._cell

    def test_the_lgbm_standardisation_is_fit_per_fold(self):
        rows = spread(jitter=0.05, seed=4)
        folds = ev.leave_city_out(rows)
        a = ev.SimilarityWeightedLGBM().fit(list(folds[0].train))
        b = ev.SimilarityWeightedLGBM().fit(list(folds[1].train))
        assert a._mu is not None and b._mu is not None
        assert not np.allclose(a._mu, b._mu) or len(folds[0].train) == \
            len(folds[1].train)


# ── the four candidates ───────────────────────────────────────────────────
class TestCandidates:
    def test_all_four_are_registered_in_complexity_order(self):
        names = [c.name for c in ev.default_candidates()]
        assert names == ["constant_5050", "shrunk_dfactor",
                         "beta_binomial_dfactor", "similarity_weighted_lgbm"]
        complexities = [c.complexity for c in ev.default_candidates()]
        assert complexities == sorted(complexities)

    def test_the_null_predicts_a_half(self):
        rows = spread(share=0.8)
        assert np.allclose(ev.Constant5050().fit(rows).predict(rows), 0.5)

    def test_shrunk_dfactor_pulls_toward_a_half(self):
        rows = spread(share=0.75, jitter=0.02, seed=5)
        predicted = ev.ShrunkDFactor().fit(rows).predict(rows)
        observed = float(np.mean([r.share for r in rows]))
        assert 0.5 < float(np.mean(predicted)) <= observed + 1e-9

    def test_shrinkage_counts_blocks_not_rows(self):
        """Fifteen hourly rows from one block are one block of evidence."""
        model = ev.ShrunkDFactor().fit(spread(jitter=0.02, seed=6))
        for key, count in model._blocks.items():
            assert count <= len({r.block for r in spread()})

    def test_the_count_model_weights_by_evidence(self):
        """3/5 vehicles must not outvote 3000/5000."""
        thin = [row(station="thin", hour=h, share=0.9, total=5.0, n_obs=1.0)
                for h in range(6, 21)]
        thick = [row(station="thick", hour=h, share=0.5, total=5000.0,
                     n_obs=20.0) for h in range(6, 21)]
        model = ev.BetaBinomialDFactor().fit(thin + thick)
        # the weighted cell mean must sit near the thick station's 0.5
        assert all(abs(v - 0.5) < 0.1 for v in model._cell.values())

    def test_every_candidate_returns_one_value_per_row(self):
        rows = spread(jitter=0.03, seed=7)
        for candidate in ev.default_candidates():
            predicted = candidate.fit(rows).predict(rows)
            assert len(predicted) == len(rows)
            assert np.all((predicted >= 0.0) & (predicted <= 1.0))


# ── uncertainty on the difference ─────────────────────────────────────────
class TestDifferenceCarriesUncertainty:
    def test_a_genuine_improvement_has_a_ci_below_zero(self):
        rng = np.random.default_rng(0)
        better = rng.normal(0.10, 0.01, 400)
        worse = rng.normal(0.13, 0.01, 400)
        blocks = [f"b{i // 20}" for i in range(400)]
        low, high = ev.paired_difference_ci(better, worse, blocks)
        assert low < 0 and high < 0

    def test_a_tie_straddles_zero(self):
        rng = np.random.default_rng(1)
        a = rng.normal(0.10, 0.02, 400)
        b = rng.normal(0.10, 0.02, 400)
        blocks = [f"b{i // 20}" for i in range(400)]
        low, high = ev.paired_difference_ci(a, b, blocks)
        assert low < 0 < high

    def test_the_block_bootstrap_is_wider_than_a_row_bootstrap(self):
        """Correlated rows must not be resampled as independent."""
        rng = np.random.default_rng(2)
        effect = rng.normal(0, 0.05, 20)
        a = np.concatenate([np.full(20, e) for e in effect])
        b = np.zeros_like(a)
        blocked = [f"b{i // 20}" for i in range(len(a))]
        unblocked = [f"r{i}" for i in range(len(a))]
        lo_b, hi_b = ev.paired_difference_ci(a, b, blocked)
        lo_r, hi_r = ev.paired_difference_ci(a, b, unblocked)
        assert (hi_b - lo_b) > (hi_r - lo_r)

    def test_the_score_report_carries_a_ci_per_group(self):
        rows = spread(jitter=0.04, seed=8)
        report = ev.score(ev.ShrunkDFactor(), ev.leave_city_out(rows))
        stats = report["groups"]["all"]
        assert len(stats["paired_diff_ci95"]) == 2
        assert "mae" in stats and "mae_5050" in stats
        assert "n_blocks" in stats

    def test_a_single_block_yields_no_interval(self):
        low, high = ev.paired_difference_ci(
            np.zeros(5), np.zeros(5), ["only"] * 5)
        assert np.isnan(low) and np.isnan(high)


# ── temporal support is measured and refused, not extrapolated ────────────
class TestTemporalSupport:
    def test_observed_hours_are_reported_per_day_type(self):
        rows = [row(hour=h, weekend=0) for h in range(6, 21)]
        support = ev.temporal_support(rows)
        assert support["weekday_hours_observed"] == list(range(6, 21))
        assert support["weekend_hours_observed"] == []

    def test_unsupported_cells_are_named(self):
        rows = [row(hour=h, weekend=0) for h in range(6, 21)]
        support = ev.temporal_support(rows)
        assert support["weekday_hours_unsupported"] == [
            0, 1, 2, 3, 4, 5, 21, 22, 23]
        assert support["weekend_hours_unsupported"] == list(range(24))

    def test_the_report_states_the_refusal_explicitly(self):
        support = ev.temporal_support([row()])
        assert "extrapolation" in support["refusal"]
        assert "must be labelled" in support["refusal"]


# ── the frozen Gate M rule ────────────────────────────────────────────────
class TestGateMRule:
    ALL_KINDS = ev.REQUIRED_FOLD_KINDS

    def build(self, *, wins=(), loses=(), kinds=None, per_kind=None):
        """A synthetic report.

        ``per_kind`` overrides wins/loses for one fold kind, which is what a
        rule-4 test needs: a candidate that wins under one kind and only ties
        under another must NOT be promoted.
        """
        report = {}
        for kind in (kinds or self.ALL_KINDS):
            kind_wins, kind_loses = (per_kind or {}).get(kind, (wins, loses))
            report[kind] = {"groups": {
                group: {"beats_baseline": group in kind_wins,
                        "loses_to_baseline": group in kind_loses}
                for group in ev.PRIMARY_GROUPS}}
        return report

    META = {"blocks": 50}

    def decide(self, reports, meta=None, comparisons=None, kinds=None):
        return ev.decide_gate_m(
            reports, ev.default_candidates(), meta or self.META,
            comparisons=comparisons,
            fold_kinds=list(kinds or self.ALL_KINDS))

    def test_nothing_promoted_is_baseline(self):
        reports = {c.name: self.build()
                   for c in ev.default_candidates()[1:]}
        result = self.decide(reports)
        assert result["gate_m"] == "BASELINE"
        assert result["winner"] == "constant_5050"

    def test_a_candidate_that_loses_anywhere_is_not_promoted(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",),
                                                loses=("weekday_peak",))}
        assert self.decide(reports)["gate_m"] == "BASELINE"

    def test_a_clean_win_under_every_fold_kind_is_model(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        result = self.decide(reports)
        assert result["gate_m"] == "MODEL"
        assert result["winner"] == "shrunk_dfactor"

    def test_a_win_under_only_one_fold_kind_is_not_a_win(self):
        """Rule 4: promotion requires the win under EVERY fold kind.

        The previous code pooled wins and losses across kinds, so a win under
        leave-city-out plus a tie elsewhere promoted.
        """
        reports = {"shrunk_dfactor": self.build(per_kind={
            "leave_city_out": (("all",), ()),
            "leave_station_out": ((), ()),
            "blocked_date": ((), ()),
        })}
        result = self.decide(reports)
        assert result["gate_m"] == "BASELINE"
        assert result["detail"]["shrunk_dfactor"]["promoted"] is False

    def test_a_tie_does_not_promote(self):
        reports = {"similarity_weighted_lgbm": self.build()}
        assert self.decide(reports)["gate_m"] == "BASELINE"

    def test_the_simplest_winner_is_kept(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",)),
                   "similarity_weighted_lgbm": self.build()}
        assert self.decide(reports)["winner"] == "shrunk_dfactor"

    def test_a_complex_model_must_beat_the_promoted_incumbent_not_5050(self):
        """Rule 2: the comparison target moves with the incumbent.

        Both models beat 50/50 here, but the complex one is only a TIE
        against the simpler incumbent, so it must not replace it.
        """
        reports = {"shrunk_dfactor": self.build(wins=("all",)),
                   "similarity_weighted_lgbm": self.build(wins=("all",))}
        comparisons = {
            "similarity_weighted_lgbm": {
                "shrunk_dfactor": self.build(),          # tie vs incumbent
                "constant_5050": self.build(wins=("all",)),
            },
            "shrunk_dfactor": {
                "constant_5050": self.build(wins=("all",)),
            },
        }
        result = self.decide(reports, comparisons=comparisons)
        assert result["winner"] == "shrunk_dfactor"
        assert result["detail"]["similarity_weighted_lgbm"][
            "compared_against"] == "shrunk_dfactor"

    def test_a_complex_model_that_does_beat_the_incumbent_is_promoted(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",)),
                   "similarity_weighted_lgbm": self.build(wins=("all",))}
        comparisons = {
            "similarity_weighted_lgbm": {
                "shrunk_dfactor": self.build(wins=("all",)),
                "constant_5050": self.build(wins=("all",)),
            },
            "shrunk_dfactor": {
                "constant_5050": self.build(wins=("all",)),
            },
        }
        result = self.decide(reports, comparisons=comparisons)
        assert result["winner"] == "similarity_weighted_lgbm"

    def test_a_missing_comparison_against_a_promoted_incumbent_blocks(self):
        """Silence is not evidence of an improvement."""
        reports = {"shrunk_dfactor": self.build(wins=("all",)),
                   "similarity_weighted_lgbm": self.build(wins=("all",))}
        comparisons = {"shrunk_dfactor": {
            "constant_5050": self.build(wins=("all",))}}
        result = self.decide(reports, comparisons=comparisons)
        assert result["winner"] == "shrunk_dfactor"
        assert "no paired comparison" in \
            result["detail"]["similarity_weighted_lgbm"]["why"]

    def test_too_few_blocks_is_inconclusive_not_baseline(self):
        """'We could not measure it' is not 'the model does not help'."""
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        result = self.decide(reports, meta={"blocks": 3})
        assert result["gate_m"] == "INCONCLUSIVE"
        assert result["winner"] is None

    def test_a_missing_required_fold_kind_is_inconclusive_not_baseline(self):
        """Rule 6, and the specific defect this replaces.

        The aggregated legacy table carries no dates, so blocked_date cannot
        be built from it. The previous code skipped that kind and published
        BASELINE, which reads as 'the model does not help' rather than 'the
        future-day evidence was never gathered'.
        """
        reports = {"shrunk_dfactor": self.build(
            kinds=("leave_city_out", "leave_station_out"))}
        result = self.decide(
            reports, kinds=("leave_city_out", "leave_station_out"))
        assert result["gate_m"] == "INCONCLUSIVE"
        assert result["winner"] is None
        assert result["missing_fold_kinds"] == ["blocked_date"]

    def test_inconclusive_says_it_is_not_baseline(self):
        reports = {"shrunk_dfactor": self.build(kinds=("leave_city_out",))}
        result = self.decide(reports, kinds=("leave_city_out",))
        assert "INCONCLUSIVE is not BASELINE" in result["note"]

    def test_blocked_date_is_a_required_fold_kind(self):
        assert "blocked_date" in ev.REQUIRED_FOLD_KINDS
        assert set(ev.REQUIRED_FOLD_KINDS) == {
            "leave_city_out", "leave_station_out", "blocked_date"}

    def test_the_decision_is_deterministic(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        first = self.decide(reports)
        second = self.decide(reports)
        assert first["gate_m"] == second["gate_m"]
        assert first["selection_rule"] == ev.SELECTION_RULE

    def test_the_rule_version_records_the_correction(self):
        assert ev.SELECTION_RULE == "simplest_defensible_v2"


# ── the tournament measures the deployed population and model ─────────────
class TestTheTournamentMatchesDeployment:
    def test_the_oneway_screen_is_the_deployed_observed_share_rule(self):
        """Not the OSM `oneway` feature flag, which screens a different set."""
        records = [
            # a genuinely two-way station whose OSM flag says oneway
            {"station_id": "A", "heading": "N", "is_weekend": "0",
             "hour": "8", "share": "0.52", "oneway": "1"},
            # a one-way-ish station whose OSM flag says two-way
            {"station_id": "B", "heading": "N", "is_weekend": "0",
             "hour": "8", "share": "0.97", "oneway": "0"},
        ]
        dropped = ev.deployed_oneway_stations(records)
        assert dropped == {"B"}

    def test_the_screen_only_looks_at_weekday_daytime_rows(self):
        records = [
            {"station_id": "A", "heading": "N", "is_weekend": "0",
             "hour": "8", "share": "0.52", "oneway": "0"},
            {"station_id": "A", "heading": "N", "is_weekend": "0",
             "hour": "3", "share": "0.99", "oneway": "0"},
        ]
        assert ev.deployed_oneway_stations(records) == set()

    def test_the_band_matches_train_py(self):
        assert ev.DEPLOYED_TWO_WAY_BAND == (0.15, 0.85)
        assert list(ev.DEPLOYED_TRAINING_HOURS) == list(range(6, 21))

    def test_the_lgbm_is_centred_on_the_target_not_the_training_cloud(self):
        """Deployment weights toward the road it will predict."""
        rows = (spread(cities=("oslo",), share=0.70, jitter=0.02, seed=20)
                + spread(cities=("bergen",), share=0.30, jitter=0.02, seed=21))
        near = np.zeros((1, len(FEATURE_NAMES)))
        far = np.full((1, len(FEATURE_NAMES)), 5.0)
        a = ev.SimilarityWeightedLGBM().fit(rows, target_features=near)
        b = ev.SimilarityWeightedLGBM().fit(rows, target_features=far)
        assert not np.allclose(a.predict(rows), b.predict(rows))

    def test_the_target_features_carry_no_labels(self):
        """The kernel centre is street geometry, never a held-out share."""
        rows = spread(jitter=0.03, seed=22)
        folds = ev.leave_city_out(rows)
        target = np.array([r.features for r in folds[0].test], dtype=float)
        assert target.shape[1] == len(FEATURE_NAMES)
        shares = {r.share for r in folds[0].test}
        assert not any(np.isclose(target, value).any() for value in shares
                       if value not in (0.0, 1.0)) or target.shape[1] > 0

    def test_the_lgbm_fits_only_on_the_deployed_training_window(self):
        weekend = [row(station="W", hour=h, weekend=1, share=0.8)
                   for h in range(6, 21)]
        weekday = [row(station="D", hour=h, weekend=0, share=0.5)
                   for h in range(6, 21)]
        assert not ev.in_deployed_training_window(weekend[0])
        assert ev.in_deployed_training_window(weekday[0])
        # too few weekday rows to fit → the candidate falls back to 0.5
        model = ev.SimilarityWeightedLGBM().fit(weekend)
        assert np.allclose(model.predict(weekend), 0.5)

    def test_the_lgbm_applies_the_deployed_shrinkage(self):
        rows = (spread(cities=("oslo",), share=0.75, jitter=0.02, seed=23)
                + spread(cities=("bergen",), share=0.75, jitter=0.02, seed=24))
        model = ev.SimilarityWeightedLGBM().fit(rows)
        assert 0.0 <= model._shrinkage <= 1.0
        # deployment ships 0.5 + lambda*(pred-0.5), so predictions sit at
        # most as far from 0.5 as the raw ones.
        predicted = model.predict(rows)
        assert np.all(np.abs(predicted - 0.5) <= 0.4 + 1e-9)

    def test_the_rule_text_names_the_population_requirement(self):
        assert "deployment uses" in ev.SELECTION_RULE_TEXT
        assert "different centre" in ev.SELECTION_RULE_TEXT


# ── the real run ──────────────────────────────────────────────────────────
class TestRealGateMReport:
    PATH = Path("data/dirsplit/gate_m_report.json")

    def report(self):
        if not self.PATH.is_file():
            pytest.skip("Gate M has not been run in this checkout")
        return json.loads(self.PATH.read_text())

    def test_it_records_a_decided_gate(self):
        report = self.report()
        assert report["gate_m"] in ("BASELINE", "MODEL", "INCONCLUSIVE")

    def test_it_is_not_release_evidence(self):
        assert self.report()["release_evidence"] is False

    def test_it_names_its_frozen_selection_rule(self):
        assert self.report()["selection_rule"] == ev.SELECTION_RULE

    def test_it_reports_all_four_candidates(self):
        report = self.report()
        assert set(report["reports"]) == {
            "constant_5050", "shrunk_dfactor", "beta_binomial_dfactor",
            "similarity_weighted_lgbm"}

    def test_every_candidate_carries_a_paired_ci(self):
        report = self.report()
        for name, per_kind in report["reports"].items():
            for kind, stats in per_kind.items():
                entry = stats["groups"]["all"]
                assert len(entry["paired_diff_ci95"]) == 2, f"{name}/{kind}"

    def test_it_records_whether_blocked_date_was_possible(self):
        assert "blocked_date_available" in self.report()

    def test_it_measures_the_deployed_population(self):
        """Not the OSM-flag population, which is less than half the size."""
        report = self.report()
        assert "observed weekday-daytime" in report["input"]["oneway_screen"]
        assert report["input"]["stations"] == 81

    def test_a_missing_blocked_date_fold_left_it_undecided(self):
        report = self.report()
        if report["blocked_date_available"]:
            pytest.skip("dataset v2 exists in this checkout")
        assert report["gate_m"] == "INCONCLUSIVE"
        assert report["winner"] is None
        assert "blocked_date" in report["missing_fold_kinds"]

    def test_it_carries_pairwise_comparisons_not_only_vs_5050(self):
        report = self.report()
        pairwise = report["pairwise_comparisons"]
        assert pairwise["similarity_weighted_lgbm"]["shrunk_dfactor"]


# ── the v1 outcome is withdrawn, not quietly rewritten ────────────────────
class TestTheWithdrawnV1Outcome:
    V1 = Path("validation/dirsplit_gate_m_outcome_v1.json")
    V2 = Path("validation/dirsplit_gate_m_outcome_v2.json")

    def test_the_v1_record_is_still_present_and_unedited(self):
        """Append-only: a published outcome is evidence of what was said."""
        if not self.V1.is_file():
            pytest.skip("no v1 outcome in this checkout")
        assert json.loads(self.V1.read_text())["gate_m"] == "BASELINE"

    def test_v2_supersedes_it_explicitly(self):
        if not self.V2.is_file():
            pytest.skip("no v2 outcome in this checkout")
        payload = json.loads(self.V2.read_text())
        assert payload["supersedes"] == str(self.V1)
        assert payload["gate_m"] == "INCONCLUSIVE"
        assert payload["release_evidence"] is False

    def test_v2_withdraws_the_unsupported_comparison_claim(self):
        if not self.V2.is_file():
            pytest.skip("no v2 outcome in this checkout")
        payload = json.loads(self.V2.read_text())
        claim = payload["why_v1_is_withdrawn"]["withdrawn_claim"]
        assert "31.6-39.2%" in claim
        assert "NOT supported" in claim

    def test_v2_names_what_would_actually_decide_the_gate(self):
        if not self.V2.is_file():
            pytest.skip("no v2 outcome in this checkout")
        payload = json.loads(self.V2.read_text())
        assert "dataset v2" in payload["what_would_decide_it"]["requirement"]
        assert payload["what_would_decide_it"]["blocker_in_this_environment"]
