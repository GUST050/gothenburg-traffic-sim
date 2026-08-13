"""Scoring a scenario ensemble as a joint object (plan step 4).

Marginal scores cannot tell you whether an ensemble got the *dependence*
right, and dependence is the entire reason step 4 exists. Two ensembles can
have identical per-cell marginals while one reproduces the observed
between-road and within-day structure and the other is white noise.

Three complementary instruments, each answering a different question:

``energy_score``
    The multivariate generalisation of CRPS. Proper, but — as Scheuerer &
    Hamill showed — comparatively insensitive to misspecified correlation,
    so it is reported as a check rather than as the discriminator.

``variogram_score``
    Built from pairwise differences, so it responds directly to the
    correlation structure the energy score under-weights. This is the
    discriminating score for the question step 4 asks.

``dependence_diagnostics``
    Not scores but descriptions: lag-1 autocorrelation within a day,
    cross-road correlation between edges, and the frequency of joint extreme
    hours. An ensemble can score well and still get one of these obviously
    wrong, and a reader deserves to see them separately.

Every function here compares an ensemble against observations. None of them
decides anything: the plan is explicit that a scenario ensemble is judged
through the DECISION it supports (Kaut & Wallace; Birge's value of the
stochastic solution), so these numbers feed the stability analysis rather
than replacing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


def _stack(ensemble: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(ensemble, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("ensemble must be (n_members, n_dimensions)")
    return matrix


def energy_score(ensemble: Sequence[Sequence[float]],
                 observation: Sequence[float]) -> float:
    """ES = mean||X_i - y|| - 0.5 * mean||X_i - X_j||.

    Proper for the full joint distribution. Lower is better.
    """
    members = _stack(ensemble)
    truth = np.asarray(observation, dtype=float)
    if members.shape[1] != truth.shape[0]:
        raise ValueError("observation dimension does not match the ensemble")
    n = len(members)
    if n == 0:
        return float("nan")
    first = float(np.mean(np.linalg.norm(members - truth, axis=1)))
    if n == 1:
        return first
    diffs = members[:, None, :] - members[None, :, :]
    second = float(np.mean(np.linalg.norm(diffs, axis=2)))
    return first - 0.5 * second


def variogram_score(ensemble: Sequence[Sequence[float]],
                    observation: Sequence[float],
                    order: float = 0.5,
                    weights: np.ndarray | None = None) -> float:
    """Scheuerer & Hamill's variogram score of the given order.

    Compares the ensemble's expected ``|X_i - X_j|^p`` against the
    observation's, for every pair of dimensions. Because it is built from
    pairwise differences it is sensitive to correlation structure in a way
    the energy score is not — which is exactly why the plan names it.
    """
    members = _stack(ensemble)
    truth = np.asarray(observation, dtype=float)
    d = truth.shape[0]
    if members.shape[1] != d:
        raise ValueError("observation dimension does not match the ensemble")
    if weights is None:
        weights = np.ones((d, d), dtype=float)

    obs_gamma = np.abs(truth[:, None] - truth[None, :]) ** order
    ens_gamma = np.mean(
        np.abs(members[:, :, None] - members[:, None, :]) ** order, axis=0
    )
    return float(np.sum(weights * (obs_gamma - ens_gamma) ** 2))


@dataclass(frozen=True)
class DependenceDiagnostics:
    """Descriptions of structure, reported alongside the scores."""

    lag1_autocorrelation: float
    cross_series_correlation: float
    joint_extreme_rate: float
    n_members: int
    n_dimensions: int

    def to_json(self) -> dict[str, Any]:
        return {
            "lag1_autocorrelation": _round(self.lag1_autocorrelation),
            "cross_series_correlation": _round(self.cross_series_correlation),
            "joint_extreme_rate": _round(self.joint_extreme_rate),
            "n_members": self.n_members,
            "n_dimensions": self.n_dimensions,
        }


def _round(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 6)


def lag1_autocorrelation(series: Sequence[float]) -> float:
    values = np.asarray(series, dtype=float)
    if len(values) < 3:
        return float("nan")
    a, b = values[:-1], values[1:]
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def dependence_diagnostics(
    members: Sequence[Mapping[str, Sequence[float]]],
    edge_ids: Sequence[str] | None = None,
) -> DependenceDiagnostics:
    """Within-day autocorrelation, cross-road correlation, joint extremes.

    ``members`` is a list of scenario members, each mapping edge id to its
    per-slot series. All three quantities are averaged over members, so a
    single lucky member cannot carry the diagnostic.
    """
    if not members:
        return DependenceDiagnostics(float("nan"), float("nan"),
                                     float("nan"), 0, 0)
    ids = list(edge_ids or sorted(members[0]))
    if not ids:
        return DependenceDiagnostics(float("nan"), float("nan"),
                                     float("nan"), len(members), 0)

    autocorrs: list[float] = []
    crosses: list[float] = []
    extremes: list[float] = []

    for member in members:
        series = [np.asarray(member[eid], dtype=float)
                  for eid in ids if eid in member]
        if not series:
            continue
        for one in series:
            value = lag1_autocorrelation(one)
            if np.isfinite(value):
                autocorrs.append(value)
        for i in range(len(series)):
            for j in range(i + 1, len(series)):
                a, b = series[i], series[j]
                if np.std(a) == 0 or np.std(b) == 0:
                    continue
                crosses.append(float(np.corrcoef(a, b)[0, 1]))
        if len(series) >= 2:
            stacked = np.vstack(series)
            centred = stacked - stacked.mean(axis=1, keepdims=True)
            scale = stacked.std(axis=1, keepdims=True)
            scale[scale == 0] = 1.0
            z = centred / scale
            # A slot is a joint extreme when every road is beyond 1 sd the
            # same way — the kind of day a marginal ensemble never produces.
            extremes.append(float(np.mean(np.all(z > 1.0, axis=0)
                                          | np.all(z < -1.0, axis=0))))

    return DependenceDiagnostics(
        lag1_autocorrelation=float(np.mean(autocorrs)) if autocorrs
        else float("nan"),
        cross_series_correlation=float(np.mean(crosses)) if crosses
        else float("nan"),
        joint_extreme_rate=float(np.mean(extremes)) if extremes
        else float("nan"),
        n_members=len(members),
        n_dimensions=len(ids),
    )


def compare_against_baseline(
    ensemble: Sequence[Sequence[float]],
    baseline: Sequence[Sequence[float]],
    observation: Sequence[float],
    order: float = 0.5,
) -> dict[str, Any]:
    """Score a joint ensemble against a simpler independent baseline.

    The plan requires this comparison explicitly: a residual library must
    beat at least one simpler independent-residual baseline, or it is extra
    machinery with nothing to show for it. Lower is better for both scores,
    so ``*_gain`` is positive when the joint ensemble is better.
    """
    es_joint = energy_score(ensemble, observation)
    es_base = energy_score(baseline, observation)
    vs_joint = variogram_score(ensemble, observation, order=order)
    vs_base = variogram_score(baseline, observation, order=order)
    return {
        "energy_score": _round(es_joint),
        "energy_score_baseline": _round(es_base),
        "energy_score_gain": _round(es_base - es_joint),
        "variogram_score": _round(vs_joint),
        "variogram_score_baseline": _round(vs_base),
        "variogram_score_gain": _round(vs_base - vs_joint),
        "variogram_order": order,
        "joint_beats_baseline": bool(
            np.isfinite(vs_joint) and np.isfinite(vs_base)
            and vs_joint < vs_base
        ),
        "note": (
            "variogram_score is the discriminating measure for dependence; "
            "energy_score is reported as a check because it is relatively "
            "insensitive to misspecified correlation (Scheuerer & Hamill)."
        ),
    }


__all__ = [
    "energy_score", "variogram_score", "lag1_autocorrelation",
    "dependence_diagnostics", "DependenceDiagnostics",
    "compare_against_baseline",
]
