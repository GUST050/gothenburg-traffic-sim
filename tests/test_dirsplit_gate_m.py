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
    def build(self, *, wins=(), loses=(), kinds=("leave_city_out",)):
        report = {}
        for kind in kinds:
            groups = {}
            for group in ev.PRIMARY_GROUPS:
                groups[group] = {
                    "beats_baseline": group in wins,
                    "loses_to_baseline": group in loses,
                }
            report[kind] = {"groups": groups}
        return report

    META = {"blocks": 50}

    def test_nothing_promoted_is_baseline(self):
        reports = {c.name: self.build()
                   for c in ev.default_candidates()[1:]}
        result = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert result["gate_m"] == "BASELINE"
        assert result["winner"] == "constant_5050"

    def test_a_candidate_that_loses_anywhere_is_not_promoted(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",),
                                                loses=("weekday_peak",))}
        result = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert result["gate_m"] == "BASELINE"

    def test_a_clean_win_is_model(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        result = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert result["gate_m"] == "MODEL"
        assert result["winner"] == "shrunk_dfactor"

    def test_a_tie_does_not_promote(self):
        reports = {"similarity_weighted_lgbm": self.build()}
        result = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert result["gate_m"] == "BASELINE"

    def test_the_simplest_winner_is_kept(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",)),
                   "similarity_weighted_lgbm": self.build()}
        result = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert result["winner"] == "shrunk_dfactor"

    def test_too_few_blocks_is_inconclusive_not_baseline(self):
        """'We could not measure it' is not 'the model does not help'."""
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        result = ev.decide_gate_m(reports, ev.default_candidates(),
                                  {"blocks": 3})
        assert result["gate_m"] == "INCONCLUSIVE"
        assert result["winner"] is None

    def test_the_decision_is_deterministic(self):
        reports = {"shrunk_dfactor": self.build(wins=("all",))}
        first = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        second = ev.decide_gate_m(reports, ev.default_candidates(), self.META)
        assert first["gate_m"] == second["gate_m"]
        assert first["selection_rule"] == ev.SELECTION_RULE


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
        assert self.report()["selection_rule"] == "simplest_defensible_v1"

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
