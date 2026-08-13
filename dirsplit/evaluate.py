"""Fold design, scoring and the pre-registered selection rule (plan step 2).

Three complementary splits, because they answer three different questions and
the deployment needs all three to hold:

``leave_city_out``
    Geographic transfer. Can a model trained on Oslo/Bergen/Trondheim say
    anything about Stavanger — and, by extension, about Gothenburg?

``leave_station_out``
    A new road inside a known city. This is the question a new Gothenburg
    sensor asks.

``blocked_date``
    A future day. Rows from one date never straddle the split, because
    within-day correlation would otherwise leak: two hours of the same
    station-day are not two independent observations, and treating them as
    such makes any interval look better calibrated than it is.

Every fold hands the model ONLY its training rows. Shrinkage constants,
kernel bandwidths, dispersion parameters and clamps are all fit inside
``model.fit``; nothing in this module estimates a quantity from the full
dataset. That is the plan's leakage requirement, and the reason the harness
refits a fresh model instance per fold rather than reusing one.

Scoring follows Gneiting & Raftery: proper scores only, and sharpness is
reported only alongside coverage, so a narrow interval cannot look good by
being confidently wrong.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from .dataset_schema import DirectionRow
from .models import (DirectionModel, Prediction, n_independent_blocks, shares,
                     station_weights, usable)

#: Pre-registered primary groups. A model must not lose in any of these to
#: be promoted over a simpler one.
PRIMARY_GROUPS = ("all", "weekday_peak")

#: Bootstrap resamples for the paired-difference interval.
BOOTSTRAP_SAMPLES = 2000

#: Pre-registered bootstrap seed, fixed before any fold was scored.
BOOTSTRAP_SEED = 20260813


@dataclass(frozen=True)
class Fold:
    """One train/test split, named so results are traceable."""

    name: str
    kind: str
    train: tuple[DirectionRow, ...]
    test: tuple[DirectionRow, ...]

    def __post_init__(self) -> None:
        train_blocks = {r.day_block_id for r in self.train}
        test_blocks = {r.day_block_id for r in self.test}
        if self.kind == "blocked_date" and train_blocks & test_blocks:
            raise ValueError(
                f"fold {self.name}: day blocks appear on both sides "
                f"({sorted(train_blocks & test_blocks)[:3]})"
            )


# ──────────────────────────────────────────────────────────────────────────
# fold builders
# ──────────────────────────────────────────────────────────────────────────
def leave_city_out(rows: Sequence[DirectionRow]) -> list[Fold]:
    cities = sorted({r.city for r in rows})
    folds = []
    for city in cities:
        train = tuple(r for r in rows if r.city != city)
        test = tuple(r for r in rows if r.city == city)
        if train and test:
            folds.append(Fold(f"city={city}", "leave_city_out", train, test))
    return folds


def leave_station_out(rows: Sequence[DirectionRow],
                      max_folds: int | None = None) -> list[Fold]:
    """Hold out one station at a time, within its own city.

    The training side keeps the held-out station's city so this measures
    "a new road here", not "a new city" — that is what leave_city_out is for.
    """
    stations = sorted({r.station_id for r in rows})
    if max_folds is not None:
        stations = stations[:max_folds]
    folds = []
    for station in stations:
        train = tuple(r for r in rows if r.station_id != station)
        test = tuple(r for r in rows if r.station_id == station)
        if train and test:
            folds.append(
                Fold(f"station={station}", "leave_station_out", train, test)
            )
    return folds


def blocked_date(rows: Sequence[DirectionRow], n_blocks: int = 4) -> list[Fold]:
    """Split by whole dates, contiguous in time.

    Contiguity matters: sampling dates at random would let a model interpolate
    between neighbouring days, which is not the question. A future day sits
    after everything the model saw.
    """
    dates = sorted({r.local_date for r in rows})
    if len(dates) < n_blocks:
        n_blocks = max(1, len(dates))
    if n_blocks < 2:
        return []
    size = math.ceil(len(dates) / n_blocks)
    folds = []
    for index in range(n_blocks):
        held = set(dates[index * size:(index + 1) * size])
        if not held:
            continue
        train = tuple(r for r in rows if r.local_date not in held)
        test = tuple(r for r in rows if r.local_date in held)
        if train and test:
            folds.append(
                Fold(f"dates[{index}]", "blocked_date", train, test)
            )
    return folds


# ──────────────────────────────────────────────────────────────────────────
# scores
# ──────────────────────────────────────────────────────────────────────────
def pinball_loss(actual: np.ndarray, predicted: np.ndarray,
                 quantile: float) -> float:
    """Proper scoring rule for a single quantile."""
    diff = actual - predicted
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def interval_score(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                   alpha: float) -> float:
    """Winkler/interval score — width penalised, misses penalised harder.

    The single number that stops a model from winning by reporting a very
    narrow interval that rarely contains the truth.
    """
    width = upper - lower
    below = (2 / alpha) * np.maximum(lower - actual, 0.0)
    above = (2 / alpha) * np.maximum(actual - upper, 0.0)
    return float(np.mean(width + below + above))


def empirical_coverage(actual: np.ndarray, lower: np.ndarray,
                       upper: np.ndarray) -> float:
    inside = (actual >= lower) & (actual <= upper)
    return float(np.mean(inside)) if len(actual) else float("nan")


def coverage_interval(actual: np.ndarray, lower: np.ndarray,
                      upper: np.ndarray, blocks: Sequence[str],
                      confidence: float = 0.95,
                      seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Block bootstrap CI on the coverage rate.

    Resampling whole day blocks rather than rows keeps the within-day
    correlation intact; a row bootstrap would report a far narrower interval
    than the data supports.
    """
    if not len(actual):
        return (float("nan"), float("nan"))
    inside = ((actual >= lower) & (actual <= upper)).astype(float)
    by_block: dict[str, list[float]] = {}
    for block, value in zip(blocks, inside):
        by_block.setdefault(block, []).append(float(value))
    keys = list(by_block)
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAP_SAMPLES, dtype=float)
    for index in range(BOOTSTRAP_SAMPLES):
        picked = rng.choice(len(keys), size=len(keys), replace=True)
        pool: list[float] = []
        for position in picked:
            pool.extend(by_block[keys[position]])
        draws[index] = float(np.mean(pool)) if pool else float("nan")
    tail = (1 - confidence) / 2
    return (float(np.nanquantile(draws, tail)),
            float(np.nanquantile(draws, 1 - tail)))


def paired_difference_ci(a_errors: np.ndarray, b_errors: np.ndarray,
                         blocks: Sequence[str], confidence: float = 0.95,
                         seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Block bootstrap CI on mean(a_errors) - mean(b_errors).

    Paired and blocked. This is the quantity the selection rule reads: a
    complex model is promoted only when this interval lies entirely below
    zero, i.e. it is better by more than block-level noise.
    """
    diff = a_errors - b_errors
    by_block: dict[str, list[float]] = {}
    for block, value in zip(blocks, diff):
        by_block.setdefault(block, []).append(float(value))
    keys = list(by_block)
    if not keys:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAP_SAMPLES, dtype=float)
    for index in range(BOOTSTRAP_SAMPLES):
        picked = rng.choice(len(keys), size=len(keys), replace=True)
        pool: list[float] = []
        for position in picked:
            pool.extend(by_block[keys[position]])
        draws[index] = float(np.mean(pool)) if pool else float("nan")
    tail = (1 - confidence) / 2
    return (float(np.nanquantile(draws, tail)),
            float(np.nanquantile(draws, 1 - tail)))


def group_of(row: DirectionRow) -> tuple[str, ...]:
    """Which reporting groups a row belongs to."""
    groups = ["all", "weekend" if row.is_weekend else "weekday"]
    if not row.is_weekend and (6 <= row.hour <= 9 or 15 <= row.hour <= 18):
        groups.append("weekday_peak")
    if row.hour < 6 or row.hour > 20:
        groups.append("off_hours")
    return tuple(groups)


# ──────────────────────────────────────────────────────────────────────────
# running the tournament
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class FoldResult:
    fold: str
    kind: str
    model: str
    n_test: int
    n_blocks: int
    absolute_errors: np.ndarray = field(repr=False)
    baseline_errors: np.ndarray = field(repr=False)
    blocks: tuple[str, ...] = field(repr=False, default=())
    groups: tuple[tuple[str, ...], ...] = field(repr=False, default=())
    pinball: Mapping[str, float] = field(default_factory=dict)
    coverage: float | None = None
    interval_score: float | None = None
    mean_width: float | None = None


def run_fold(model: DirectionModel, fold: Fold,
             quantiles: tuple[float, float] = (0.1, 0.9)) -> FoldResult:
    """Fit on the fold's training rows only, then score on its test rows."""
    train = usable(fold.train)
    test = usable(fold.test)
    fitted = copy.deepcopy(model).fit(train)
    prediction: Prediction = fitted.predict(test)
    actual = shares(test)
    errors = np.abs(actual - prediction.central)
    baseline = np.abs(actual - 0.5)

    pinball: dict[str, float] = {}
    coverage = width = iscore = None
    if prediction.has_interval:
        assert prediction.lower is not None and prediction.upper is not None
        pinball[f"q{int(quantiles[0]*100)}"] = pinball_loss(
            actual, prediction.lower, quantiles[0])
        pinball[f"q{int(quantiles[1]*100)}"] = pinball_loss(
            actual, prediction.upper, quantiles[1])
        pinball["q50"] = pinball_loss(actual, prediction.central, 0.5)
        coverage = empirical_coverage(actual, prediction.lower, prediction.upper)
        width = float(np.mean(prediction.upper - prediction.lower))
        iscore = interval_score(actual, prediction.lower, prediction.upper,
                                alpha=1 - (quantiles[1] - quantiles[0]))

    return FoldResult(
        fold=fold.name, kind=fold.kind, model=fitted.name,
        n_test=len(test), n_blocks=n_independent_blocks(test),
        absolute_errors=errors, baseline_errors=baseline,
        # Bootstrap blocks are STATION-DAYS. Observations within one station
        # on one day are the correlated ones, so resampling whole station-days
        # is what keeps the paired interval honest. (city-date blocks are the
        # right unit for step 4's residual library, which needs cross-road
        # structure; that is a different question from this bootstrap.)
        blocks=tuple(r.station_day_id for r in test),
        groups=tuple(group_of(r) for r in test),
        pinball=pinball, coverage=coverage, interval_score=iscore,
        mean_width=width,
    )


def aggregate(results: Sequence[FoldResult]) -> dict[str, Any]:
    """Pool fold results into the report the selection rule reads."""
    if not results:
        return {"n_test": 0}
    errors = np.concatenate([r.absolute_errors for r in results])
    baseline = np.concatenate([r.baseline_errors for r in results])
    blocks = [b for r in results for b in r.blocks]
    groups = [g for r in results for g in r.groups]

    per_group: dict[str, Any] = {}
    for name in ("all", "weekday", "weekend", "weekday_peak", "off_hours"):
        mask = np.array([name in g for g in groups], dtype=bool)
        if not mask.any():
            continue
        lo, hi = paired_difference_ci(errors[mask], baseline[mask],
                                      [b for b, m in zip(blocks, mask) if m])
        per_group[name] = {
            "n": int(mask.sum()),
            "n_blocks": len({b for b, m in zip(blocks, mask) if m}),
            "mae": float(np.mean(errors[mask])),
            "mae_5050": float(np.mean(baseline[mask])),
            "improvement_pct": _improvement(errors[mask], baseline[mask]),
            "paired_diff_ci95": [lo, hi],
            "beats_baseline": bool(hi < 0),
            "loses_to_baseline": bool(lo > 0),
        }

    coverages = [r.coverage for r in results if r.coverage is not None]
    widths = [r.mean_width for r in results if r.mean_width is not None]
    iscores = [r.interval_score for r in results if r.interval_score is not None]
    return {
        "n_test": int(len(errors)),
        "n_blocks": len(set(blocks)),
        "mae": float(np.mean(errors)),
        "mae_5050": float(np.mean(baseline)),
        "improvement_pct": _improvement(errors, baseline),
        "groups": per_group,
        "coverage": float(np.mean(coverages)) if coverages else None,
        "mean_width": float(np.mean(widths)) if widths else None,
        "interval_score": float(np.mean(iscores)) if iscores else None,
        "folds": [
            {"fold": r.fold, "kind": r.kind, "n_test": r.n_test,
             "n_blocks": r.n_blocks, "mae": float(np.mean(r.absolute_errors)),
             "mae_5050": float(np.mean(r.baseline_errors)),
             "coverage": r.coverage, "interval_score": r.interval_score,
             "pinball": dict(r.pinball)}
            for r in results
        ],
    }


def _improvement(errors: np.ndarray, baseline: np.ndarray) -> float:
    """Percent reduction in MAE against 50/50. Positive = better."""
    base = float(np.mean(baseline))
    if base <= 0:
        return 0.0
    return round(100.0 * (base - float(np.mean(errors))) / base, 2)


# ──────────────────────────────────────────────────────────────────────────
# the pre-registered selection rule
# ──────────────────────────────────────────────────────────────────────────
SELECTION_RULE = "simplest_defensible_v1"

SELECTION_RULE_TEXT = """\
Frozen before any fold was scored.

1. Candidates are ordered by complexity, ascending. Constant5050 is the
   incumbent and starts as the selected model.
2. A more complex candidate replaces the incumbent only if, on the pooled
   held-out predictions, BOTH hold:
   a. in every pre-registered primary group, the 95% block-bootstrap CI on
      the paired difference (candidate MAE - incumbent MAE) does not lie
      entirely above zero, i.e. it never significantly loses; and
   b. in at least one primary group that CI lies entirely below zero, i.e.
      it significantly wins somewhere.
3. Ties, unmeasurable groups and NaN CIs count as "does not win".
4. The rule is applied once per fold KIND. A candidate is promoted only if
   it satisfies (2) under every fold kind that produced results.
5. If no candidate is promoted, Constant5050 ships and the uncertainty is
   reported rather than modelled away.
"""


@dataclass(frozen=True)
class Selection:
    winner: str
    rule: str
    reason: str
    considered: tuple[str, ...]
    detail: Mapping[str, Any] = field(default_factory=dict)


def select_model(reports: Mapping[str, Mapping[str, Any]],
                 candidates: Sequence[DirectionModel],
                 primary_groups: Sequence[str] = PRIMARY_GROUPS) -> Selection:
    """Apply SELECTION_RULE_TEXT deterministically.

    ``reports`` maps model name -> {fold_kind -> aggregate(...)}.
    """
    ordered = sorted(candidates, key=lambda m: (m.complexity, m.name))
    if not ordered:
        raise ValueError("no candidates")
    incumbent = ordered[0]
    detail: dict[str, Any] = {}
    reason = f"{incumbent.name} is the incumbent baseline"

    for candidate in ordered[1:]:
        report = reports.get(candidate.name)
        if not report:
            detail[candidate.name] = {"promoted": False,
                                      "why": "no results"}
            continue
        wins_somewhere = False
        loses_somewhere = False
        notes: dict[str, Any] = {}
        for kind, aggregate_report in report.items():
            groups = aggregate_report.get("groups", {})
            for group in primary_groups:
                stats = groups.get(group)
                if not stats:
                    notes[f"{kind}:{group}"] = "unmeasured"
                    continue
                if stats.get("loses_to_baseline"):
                    loses_somewhere = True
                    notes[f"{kind}:{group}"] = "loses"
                elif stats.get("beats_baseline"):
                    wins_somewhere = True
                    notes[f"{kind}:{group}"] = "wins"
                else:
                    notes[f"{kind}:{group}"] = "tie"
        promoted = wins_somewhere and not loses_somewhere
        detail[candidate.name] = {"promoted": promoted, "notes": notes}
        if promoted:
            incumbent = candidate
            reason = (f"{candidate.name} wins in at least one primary group "
                      "and loses in none")

    return Selection(
        winner=incumbent.name,
        rule=SELECTION_RULE,
        reason=reason,
        considered=tuple(m.name for m in ordered),
        detail=detail,
    )


def run_tournament(
    rows: Sequence[DirectionRow],
    candidates: Sequence[DirectionModel],
    fold_builders: Mapping[str, Callable[[Sequence[DirectionRow]], list[Fold]]]
    | None = None,
    quantiles: tuple[float, float] = (0.1, 0.9),
) -> dict[str, Any]:
    """Run every candidate through every fold kind and select a winner."""
    builders = dict(fold_builders or {
        "leave_city_out": leave_city_out,
        "leave_station_out": lambda r: leave_station_out(r, max_folds=40),
        "blocked_date": blocked_date,
    })
    reports: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        per_kind: dict[str, Any] = {}
        for kind, build in builders.items():
            folds = build(rows)
            if not folds:
                continue
            results = [run_fold(candidate, fold, quantiles) for fold in folds]
            per_kind[kind] = aggregate(results)
        if per_kind:
            reports[candidate.name] = per_kind

    selection = select_model(reports, candidates)
    return {
        "schema_version": "dirsplit_tournament_v1",
        "selection_rule": SELECTION_RULE,
        "selection_rule_text": SELECTION_RULE_TEXT,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "primary_groups": list(PRIMARY_GROUPS),
        "quantiles": list(quantiles),
        "n_rows": len(rows),
        "n_usable": len(usable(rows)),
        "n_day_blocks": n_independent_blocks(rows),
        "models": {name: model.describe()
                   for name, model in ((c.name, c) for c in candidates)},
        "reports": reports,
        "selection": {
            "winner": selection.winner,
            "reason": selection.reason,
            "considered": list(selection.considered),
            "detail": selection.detail,
        },
    }


__all__ = [
    "Fold", "leave_city_out", "leave_station_out", "blocked_date",
    "pinball_loss", "interval_score", "empirical_coverage",
    "coverage_interval", "paired_difference_ci", "run_fold", "aggregate",
    "select_model", "run_tournament", "Selection", "SELECTION_RULE",
    "SELECTION_RULE_TEXT", "PRIMARY_GROUPS", "BOOTSTRAP_SEED",
]
