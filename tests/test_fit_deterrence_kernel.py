"""Selection rule for the deterrence-kernel sweep.

Found 2026-08-05: the near-tie window was an ABSOLUTE +0.02 on L1. That was
declared when L1 was scored against raw RVU and spanned 0.74-0.88, where
0.02 is ~2.5% relatively — a genuine tie. Once the target became
availability-corrected, L1 spanned ~0.027-0.174 and the same 0.02 meant
"within 74% relatively", promoting a fit 1.7x worse than the best over the
best one. An absolute epsilon does not survive a rescaling of the metric it
is applied to.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.fit_deterrence_kernel import NEAR_TIE_REL_TOL, select_winner


def _row(alpha, beta, l1, near):
    return {"alpha": alpha, "beta": beta, "l1": l1,
            "dest_near_sensor_pct": near, "shares": [0.0, 1.0, 0.0]}


class TestNearTieWindowIsScaleFree:
    def test_a_clearly_worse_fit_does_not_win_on_the_rescaled_metric(self):
        """The real 2026-08-05 grid: 0.0271 is the best, 0.0465 is 1.7x
        worse. An absolute +0.02 window called those tied."""
        results = [
            _row(1.5, 1.8, 0.0271, 3.3),
            _row(3.0, 1.3, 0.0465, 2.9),   # better proximity, much worse L1
            _row(1.5, 1.3, 0.0475, 3.5),
        ]
        best, winner, near_ties = select_winner(results)
        assert (best["alpha"], best["beta"]) == (1.5, 1.8)
        assert (winner["alpha"], winner["beta"]) == (1.5, 1.8), (
            "a fit 1.7x worse must not win on a proximity tiebreak")
        assert len(near_ties) == 1

        legacy = [r for r in results if r["l1"] <= best["l1"] + 0.02]
        legacy_winner = min(legacy, key=lambda r: r["dest_near_sensor_pct"])
        assert (legacy_winner["alpha"], legacy_winner["beta"]) == (3.0, 1.3), (
            "guard the premise: the old absolute window really did pick the "
            "worse fit, so this test is protecting against a real regression")

    def test_proximity_still_breaks_a_genuine_tie(self):
        """The rule is unchanged for actual ties — only the definition of
        'tie' is now scale-free."""
        results = [
            _row(1.5, 1.8, 0.0271, 3.3),
            _row(3.0, 1.3, 0.0271 * (1 + NEAR_TIE_REL_TOL / 2), 2.4),
        ]
        _best, winner, near_ties = select_winner(results)
        assert len(near_ties) == 2
        assert (winner["alpha"], winner["beta"]) == (3.0, 1.3)

    def test_the_window_is_relative_not_absolute(self):
        """Same relative spread at two different scales must select the
        same way — that is exactly what the absolute epsilon failed to do."""
        for scale in (1.0, 30.0):
            results = [
                _row(1.5, 1.8, 0.03 * scale, 3.3),
                _row(3.0, 1.3, 0.03 * scale * 1.5, 2.0),
            ]
            _best, winner, near_ties = select_winner(results)
            assert len(near_ties) == 1, f"scale {scale} changed the tie set"
            assert (winner["alpha"], winner["beta"]) == (1.5, 1.8)
