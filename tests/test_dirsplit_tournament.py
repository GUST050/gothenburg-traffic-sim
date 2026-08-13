"""Step 2 acceptance: identical folds, no leakage, deterministic selection.

Plan acceptance for step 2:
  * 50/50, shrunk D-factor, LightGBM and the count model get exactly the same
    folds;
  * the report shows uncertainty on the DIFFERENCE, not just a point MAE;
  * the simplest statistically defensible model is chosen deterministically
    by a pre-registered rule.
"""

from __future__ import annotations

import numpy as np
import pytest

from dirsplit import evaluate as ev
from dirsplit import models as md
from dirsplit.dataset_schema import make_row


def rows_for(city="oslo", station="S1", dates=("2025-09-01", "2025-09-02"),
             hours=range(6, 21), share=0.5, total=500.0, radial=0.9,
             noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for date in dates:
        for hour in hours:
            value = float(np.clip(share + rng.normal(0, noise), 0.05, 0.95))
            toward = round(total * value)
            out.append(make_row(
                station_id=station, city=city, heading="C",
                timestamp=f"{date}T{hour:02d}:00:00",
                toward_count=toward, away_count=total - toward,
                coverage_pct=100.0,
                features={"radial_cos": radial, "hw_rank": 2.0,
                          "speed_kmh": 50.0, "lanes": 2.0},
                profile={"am_pm_ratio": 1.0},
            ))
    return out


# ── folds ─────────────────────────────────────────────────────────────────
class TestFoldsAreLeakageFree:
    def test_blocked_date_never_shares_a_day_block(self):
        rows = []
        for i, date in enumerate(["2025-09-0%d" % d for d in range(1, 9)]):
            rows += rows_for(dates=(date,), station=f"S{i%3}", seed=i)
        folds = ev.blocked_date(rows, n_blocks=4)
        assert folds
        for fold in folds:
            train_blocks = {r.day_block_id for r in fold.train}
            test_blocks = {r.day_block_id for r in fold.test}
            assert not (train_blocks & test_blocks)

    def test_a_fold_that_leaks_blocks_is_refused(self):
        rows = rows_for()
        with pytest.raises(ValueError, match="both sides"):
            ev.Fold("bad", "blocked_date", tuple(rows), tuple(rows))

    def test_leave_city_out_holds_out_a_whole_city(self):
        rows = rows_for(city="oslo", station="A") + \
               rows_for(city="bergen", station="B")
        folds = ev.leave_city_out(rows)
        assert len(folds) == 2
        for fold in folds:
            assert not ({r.city for r in fold.train} &
                        {r.city for r in fold.test})

    def test_leave_station_out_keeps_the_city_in_training(self):
        rows = (rows_for(city="oslo", station="A")
                + rows_for(city="oslo", station="B"))
        folds = ev.leave_station_out(rows)
        assert len(folds) == 2
        for fold in folds:
            assert "oslo" in {r.city for r in fold.train}
            assert not ({r.station_id for r in fold.train} &
                        {r.station_id for r in fold.test})

    def test_every_candidate_sees_the_identical_folds(self):
        rows = (rows_for(city="oslo", station="A")
                + rows_for(city="bergen", station="B"))
        folds = ev.leave_city_out(rows)
        signature = [(f.name, len(f.train), len(f.test)) for f in folds]
        # Building again must be deterministic and identical, which is what
        # lets four models be compared rather than four experiments.
        assert [(f.name, len(f.train), len(f.test))
                for f in ev.leave_city_out(rows)] == signature


class TestNoLeakageIntoFittedQuantities:
    def test_the_model_instance_is_not_mutated_across_folds(self):
        rows = (rows_for(city="oslo", station="A", share=0.7, noise=0.02)
                + rows_for(city="bergen", station="B", share=0.3, noise=0.02))
        model = md.ShrunkDFactor()
        folds = ev.leave_city_out(rows)
        ev.run_fold(model, folds[0])
        # run_fold deep-copies, so the caller's model is still unfitted.
        assert model._cell_mean == {}

    def test_shrinkage_is_fit_per_fold_not_globally(self):
        rows = (rows_for(city="oslo", station="A", share=0.7, noise=0.02, seed=1)
                + rows_for(city="bergen", station="B", share=0.3, noise=0.02, seed=2))
        folds = ev.leave_city_out(rows)
        a = md.ShrunkDFactor().fit(folds[0].train)
        b = md.ShrunkDFactor().fit(folds[1].train)
        # Different training sides must produce different fitted constants.
        assert a._cell_mean != b._cell_mean


# ── models ────────────────────────────────────────────────────────────────
class TestCandidates:
    def test_constant_predicts_a_half_everywhere(self):
        rows = rows_for(share=0.8)
        pred = md.Constant5050().fit(rows).predict(rows)
        assert np.allclose(pred.central, 0.5)

    def test_shrunk_dfactor_pulls_toward_a_half(self):
        rows = rows_for(share=0.75, noise=0.03, dates=tuple(
            f"2025-09-{d:02d}" for d in range(1, 11)), seed=3)
        fitted = md.ShrunkDFactor().fit(rows)
        pred = fitted.predict(rows)
        observed = md.shares(rows).mean()
        # Shrunk estimate lies between 0.5 and the observed mean.
        assert 0.5 < pred.central.mean() < observed + 1e-9

    def test_thin_evidence_shrinks_harder_than_thick(self):
        thin = rows_for(share=0.75, noise=0.03,
                        dates=("2025-09-01", "2025-09-02"), seed=4)
        thick = rows_for(share=0.75, noise=0.03,
                         dates=tuple(f"2025-09-{d:02d}"
                                     for d in range(1, 21)), seed=4)
        thin_pred = md.ShrunkDFactor().fit(thin).predict(thin).central.mean()
        thick_pred = md.ShrunkDFactor().fit(thick).predict(thick).central.mean()
        assert abs(thin_pred - 0.5) <= abs(thick_pred - 0.5) + 1e-9

    def test_beta_binomial_estimates_overdispersion(self):
        # Spread far wider than binomial at n=500 -> rho must exceed zero.
        rows = rows_for(share=0.5, noise=0.08, total=500.0,
                        dates=tuple(f"2025-09-{d:02d}"
                                    for d in range(1, 16)), seed=5)
        fitted = md.BetaBinomialDFactor().fit(rows)
        assert fitted._rho > 0

    def test_beta_binomial_interval_widens_with_overdispersion(self):
        tight = rows_for(share=0.5, noise=0.001, total=500.0,
                         dates=tuple(f"2025-09-{d:02d}"
                                     for d in range(1, 16)), seed=6)
        loose = rows_for(share=0.5, noise=0.08, total=500.0,
                         dates=tuple(f"2025-09-{d:02d}"
                                     for d in range(1, 16)), seed=6)
        t = md.BetaBinomialDFactor().fit(tight).predict(tight)
        l = md.BetaBinomialDFactor().fit(loose).predict(loose)
        assert np.mean(l.upper - l.lower) > np.mean(t.upper - t.lower)

    def test_lgbm_returns_non_crossing_intervals(self):
        rows = rows_for(share=0.6, noise=0.05,
                        dates=tuple(f"2025-09-{d:02d}"
                                    for d in range(1, 11)), seed=7)
        fitted = md.SimilarityWeightedLGBM(
            ["radial_cos", "hw_rank", "speed_kmh", "lanes"]).fit(rows)
        pred = fitted.predict(rows)
        assert pred.has_interval
        assert np.all(pred.lower <= pred.upper)

    def test_all_candidates_share_one_interface(self):
        rows = rows_for(noise=0.02, dates=tuple(
            f"2025-09-{d:02d}" for d in range(1, 11)), seed=8)
        for model in md.default_candidates(["radial_cos", "hw_rank"]):
            pred = model.fit(rows).predict(rows)
            assert len(pred.central) == len(rows)
            assert np.all((pred.central >= 0) & (pred.central <= 1))


# ── scores ────────────────────────────────────────────────────────────────
class TestProperScores:
    def test_pinball_is_minimised_at_the_true_quantile(self):
        rng = np.random.default_rng(0)
        actual = rng.normal(0.5, 0.1, 5000)
        true_q10 = float(np.quantile(actual, 0.1))
        losses = {
            offset: ev.pinball_loss(actual, np.full_like(actual,
                                                         true_q10 + offset), 0.1)
            for offset in (-0.05, 0.0, 0.05)
        }
        assert losses[0.0] == min(losses.values())

    def test_interval_score_punishes_a_narrow_wrong_interval(self):
        actual = np.array([0.5] * 100)
        wide = ev.interval_score(actual, np.full(100, 0.4),
                                 np.full(100, 0.6), alpha=0.2)
        narrow_wrong = ev.interval_score(actual, np.full(100, 0.1),
                                         np.full(100, 0.2), alpha=0.2)
        assert narrow_wrong > wide

    def test_coverage_is_the_fraction_inside(self):
        actual = np.array([0.1, 0.5, 0.9])
        lower = np.full(3, 0.2)
        upper = np.full(3, 0.8)
        assert ev.empirical_coverage(actual, lower, upper) == pytest.approx(1/3)

    def test_paired_ci_reports_uncertainty_on_the_difference(self):
        rng = np.random.default_rng(1)
        a = rng.normal(0.10, 0.01, 200)
        b = rng.normal(0.12, 0.01, 200)
        blocks = [f"b{i//10}" for i in range(200)]
        lo, hi = ev.paired_difference_ci(a, b, blocks)
        assert lo < 0 and hi < 0            # a is genuinely better

    def test_paired_ci_straddles_zero_when_models_tie(self):
        rng = np.random.default_rng(2)
        a = rng.normal(0.10, 0.02, 200)
        b = rng.normal(0.10, 0.02, 200)
        blocks = [f"b{i//10}" for i in range(200)]
        lo, hi = ev.paired_difference_ci(a, b, blocks)
        assert lo < 0 < hi

    def test_block_bootstrap_is_wider_than_a_row_bootstrap_would_be(self):
        """Within-day correlation must not be resampled away."""
        rng = np.random.default_rng(3)
        block_effect = rng.normal(0, 0.05, 20)
        a = np.concatenate([np.full(10, e) for e in block_effect])
        b = np.zeros_like(a)
        blocked = [f"b{i//10}" for i in range(len(a))]
        unblocked = [f"r{i}" for i in range(len(a))]
        lo_b, hi_b = ev.paired_difference_ci(a, b, blocked)
        lo_r, hi_r = ev.paired_difference_ci(a, b, unblocked)
        assert (hi_b - lo_b) > (hi_r - lo_r)


# ── selection rule ────────────────────────────────────────────────────────
class TestSelectionRule:
    def build_report(self, *, wins=(), loses=(), kinds=("leave_city_out",)):
        report: dict[str, dict] = {}
        for kind in kinds:
            groups = {}
            for group in ev.PRIMARY_GROUPS:
                if group in wins:
                    groups[group] = {"beats_baseline": True,
                                     "loses_to_baseline": False}
                elif group in loses:
                    groups[group] = {"beats_baseline": False,
                                     "loses_to_baseline": True}
                else:
                    groups[group] = {"beats_baseline": False,
                                     "loses_to_baseline": False}
            report[kind] = {"groups": groups}
        return report

    def candidates(self):
        return [md.Constant5050(), md.ShrunkDFactor(),
                md.BetaBinomialDFactor(),
                md.SimilarityWeightedLGBM(["radial_cos"])]

    def test_baseline_ships_when_nothing_wins(self):
        reports = {m.name: self.build_report() for m in self.candidates()[1:]}
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "constant_5050"

    def test_a_model_that_loses_anywhere_is_not_promoted(self):
        reports = {
            "shrunk_dfactor": self.build_report(wins=("all",),
                                                loses=("weekday_peak",)),
        }
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "constant_5050"
        assert not chosen.detail["shrunk_dfactor"]["promoted"]

    def test_a_model_that_wins_somewhere_and_loses_nowhere_is_promoted(self):
        reports = {"shrunk_dfactor": self.build_report(wins=("all",))}
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "shrunk_dfactor"

    def test_the_simplest_winner_is_kept_when_a_complex_one_ties(self):
        reports = {
            "shrunk_dfactor": self.build_report(wins=("all",)),
            "similarity_weighted_lgbm": self.build_report(),   # ties
        }
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "shrunk_dfactor"

    def test_a_complex_model_must_beat_not_merely_match(self):
        reports = {"similarity_weighted_lgbm": self.build_report()}
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "constant_5050"

    def test_selection_is_deterministic(self):
        reports = {"shrunk_dfactor": self.build_report(wins=("all",))}
        first = ev.select_model(reports, self.candidates())
        second = ev.select_model(reports, self.candidates())
        assert first.winner == second.winner
        assert first.rule == second.rule == ev.SELECTION_RULE

    def test_unmeasured_groups_count_as_not_winning(self):
        reports = {"shrunk_dfactor": {"leave_city_out": {"groups": {}}}}
        chosen = ev.select_model(reports, self.candidates())
        assert chosen.winner == "constant_5050"


# ── end to end ────────────────────────────────────────────────────────────
class TestTournamentEndToEnd:
    def test_it_runs_and_reports_every_candidate(self):
        rows = []
        for index, city in enumerate(("oslo", "bergen", "trondheim")):
            for station in ("A", "B"):
                rows += rows_for(
                    city=city, station=f"{city}-{station}",
                    dates=tuple(f"2025-09-{d:02d}" for d in range(1, 7)),
                    share=0.5, noise=0.03, seed=index,
                )
        report = ev.run_tournament(
            rows, md.default_candidates(["radial_cos", "hw_rank"]))
        assert report["selection_rule"] == "simplest_defensible_v1"
        assert set(report["reports"]) <= {
            "constant_5050", "shrunk_dfactor", "beta_binomial_dfactor",
            "similarity_weighted_lgbm",
        }
        assert report["selection"]["winner"] in report["selection"]["considered"]

    def test_pure_noise_data_selects_the_baseline(self):
        """The honest outcome when there is no direction signal to learn."""
        rows = []
        for index, city in enumerate(("oslo", "bergen", "trondheim")):
            rows += rows_for(
                city=city, station=f"{city}-A",
                dates=tuple(f"2025-09-{d:02d}" for d in range(1, 9)),
                share=0.5, noise=0.05, seed=100 + index,
            )
        report = ev.run_tournament(
            rows, md.default_candidates(["radial_cos", "hw_rank"]))
        assert report["selection"]["winner"] == "constant_5050"

    def test_the_report_carries_difference_uncertainty_not_only_point_mae(self):
        rows = []
        for index, city in enumerate(("oslo", "bergen")):
            rows += rows_for(city=city, station=f"{city}-A",
                             dates=tuple(f"2025-09-{d:02d}"
                                         for d in range(1, 7)),
                             share=0.5, noise=0.03, seed=index)
        report = ev.run_tournament(rows, [md.Constant5050(), md.ShrunkDFactor()])
        stats = report["reports"]["shrunk_dfactor"]["leave_city_out"]["groups"]["all"]
        assert "paired_diff_ci95" in stats
        assert "mae" in stats and "mae_5050" in stats
        assert len(stats["paired_diff_ci95"]) == 2
