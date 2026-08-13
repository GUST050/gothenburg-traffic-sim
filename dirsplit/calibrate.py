"""Block-wise held-out interval calibration (plan step 3).

The plan's rule, from Gneiting/Balabdaoui/Raftery: maximise sharpness subject
to calibration. A narrow interval is only worth something if its frequentist
coverage actually holds, so an interval must not be labelled "80%" before its
out-of-sample coverage has been measured.

The current pipeline never measured it. `dirsplit/predict.py` writes
``edge_shares_q10``/``_q90`` from three quantile models, applies a crossing
guard, a clamp and a shrinkage shift, and ships the result — but no artifact
anywhere records what fraction of held-out observations those bounds actually
contain. Post-processing is not neutral: the clamp truncates and the shrinkage
shift moves the whole interval, so coverage has to be measured on the FINAL
numbers, after every transformation, which is what `measure_coverage` does.

Two calibration modes are provided and they are not interchangeable:

``conformal``
    Split-conformal adjustment of the interval width by a held-out residual
    quantile (Romano/Patterson/Candès). Under exchangeability this gives a
    finite-sample marginal coverage guarantee.

``none``
    Measure coverage, report it, adjust nothing.

The plan is explicit that the exchangeability premise is questionable here:
Norwegian stations, calendar days and Gothenburg edges are not obviously
exchangeable, covariate-shift results need a known density ratio, and time
series need methods that model dependence. So calibration is applied with
**blocks** (one station-day is one exchangeable unit, not one row), and the
artifact records `exchangeability_claimed=False` alongside the numbers. A
measured coverage is evidence; it is not a guarantee, and this module never
writes the word "guaranteed".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .dataset_schema import DirectionRow
from .evaluate import (BOOTSTRAP_SEED, Fold, coverage_interval,
                       empirical_coverage, interval_score)
from .models import DirectionModel, Prediction, shares, usable

SCHEMA_VERSION = "dirsplit_calibration_v2"

#: Coverage a nominal 80% interval must reach, allowing for finite-sample
#: noise, before it may be labelled calibrated. Frozen before measurement.
COVERAGE_TOLERANCE = 0.05


@dataclass(frozen=True)
class CoverageResult:
    """Measured coverage for one group of held-out predictions."""

    group: str
    n: int
    n_blocks: int
    nominal: float
    empirical: float
    ci95: tuple[float, float]
    mean_width: float
    interval_score: float

    @property
    def is_calibrated(self) -> bool:
        """Calibrated when the nominal level lies inside the measured CI.

        Deliberately a CI test rather than a point comparison: with a handful
        of blocks the point estimate is noisy, and declaring calibration from
        a lucky point value is exactly the failure this module exists to
        prevent.
        """
        low, high = self.ci95
        if not np.isfinite(low) or not np.isfinite(high):
            return False
        return low - COVERAGE_TOLERANCE <= self.nominal <= high + COVERAGE_TOLERANCE

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "n": self.n,
            "n_blocks": self.n_blocks,
            "nominal_coverage": self.nominal,
            "empirical_coverage": round(self.empirical, 6),
            "coverage_ci95": [round(self.ci95[0], 6), round(self.ci95[1], 6)],
            "mean_width": round(self.mean_width, 6),
            "interval_score": round(self.interval_score, 6),
            "is_calibrated": self.is_calibrated,
        }


def measure_coverage(
    actual: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    blocks: Sequence[str],
    *,
    group: str = "all",
    nominal: float = 0.8,
) -> CoverageResult:
    """Coverage, its block-bootstrap CI, sharpness and interval score.

    Measured on the FINAL bounds, after clamping and shrinkage, because those
    transformations change coverage and a pre-transform measurement would
    describe an interval nobody ships.
    """
    if len(actual) == 0:
        return CoverageResult(group, 0, 0, nominal, float("nan"),
                              (float("nan"), float("nan")),
                              float("nan"), float("nan"))
    return CoverageResult(
        group=group,
        n=int(len(actual)),
        n_blocks=len(set(blocks)),
        nominal=nominal,
        empirical=empirical_coverage(actual, lower, upper),
        ci95=coverage_interval(actual, lower, upper, blocks),
        mean_width=float(np.mean(upper - lower)),
        interval_score=interval_score(actual, lower, upper,
                                      alpha=1 - nominal),
    )


def conformal_offset(actual: np.ndarray, lower: np.ndarray,
                     upper: np.ndarray, blocks: Sequence[str],
                     nominal: float = 0.8) -> float:
    """Split-conformal width adjustment, computed on whole blocks.

    The conformity score is the signed distance outside the interval
    (Romano et al.'s CQR score). The offset is its ``nominal`` quantile taken
    over BLOCK-level scores, so one station-day contributes one score rather
    than fifteen correlated ones — otherwise the quantile is driven by how
    many hours a station happened to report.

    A negative offset (the interval is wider than it needs to be) is allowed:
    shrinking an over-wide interval is a sharpness gain, and refusing it would
    bias every calibration in one direction.
    """
    if len(actual) == 0:
        return 0.0
    score = np.maximum(lower - actual, actual - upper)
    by_block: dict[str, list[float]] = {}
    for block, value in zip(blocks, score):
        by_block.setdefault(block, []).append(float(value))
    block_scores = np.array([max(values) for values in by_block.values()],
                            dtype=float)
    if not len(block_scores):
        return 0.0
    n = len(block_scores)
    # Finite-sample conformal level.
    level = min(1.0, np.ceil((n + 1) * nominal) / n)
    return float(np.quantile(block_scores, level))


@dataclass
class Calibration:
    """A fitted interval calibration, applied to future predictions."""

    method: str
    offset: float
    nominal: float
    fitted_blocks: int
    exchangeability_claimed: bool = False
    notes: str = ""

    def apply(self, prediction: Prediction,
              clamp: tuple[float, float] = (0.1, 0.9)) -> Prediction:
        if prediction.lower is None or prediction.upper is None:
            return prediction
        lower = np.clip(prediction.lower - self.offset, *clamp)
        upper = np.clip(prediction.upper + self.offset, *clamp)
        lower, upper = np.minimum(lower, upper), np.maximum(lower, upper)
        return Prediction(
            central=np.clip(prediction.central, lower, upper),
            lower=lower, upper=upper,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "offset": round(self.offset, 8),
            "nominal_coverage": self.nominal,
            "fitted_blocks": self.fitted_blocks,
            "exchangeability_claimed": self.exchangeability_claimed,
            "notes": self.notes,
        }


def fit_calibration(model: DirectionModel, folds: Sequence[Fold],
                    *, nominal: float = 0.8,
                    method: str = "conformal") -> tuple[Calibration,
                                                        dict[str, Any]]:
    """Fit an interval calibration on held-out predictions only.

    The model is refit inside every fold and the conformity scores come from
    the held-out side, so no observation contributes to both the model and
    the calibration that judges it.
    """
    import copy

    actual_all: list[float] = []
    lower_all: list[float] = []
    upper_all: list[float] = []
    blocks_all: list[str] = []
    rows_all: list[DirectionRow] = []

    for fold in folds:
        train = usable(fold.train)
        test = usable(fold.test)
        if not train or not test:
            continue
        fitted = copy.deepcopy(model).fit(train)
        prediction = fitted.predict(test)
        if not prediction.has_interval:
            continue
        actual_all.extend(shares(test).tolist())
        lower_all.extend(np.asarray(prediction.lower).tolist())
        upper_all.extend(np.asarray(prediction.upper).tolist())
        blocks_all.extend(r.station_day_id for r in test)
        rows_all.extend(test)

    if not actual_all:
        return (
            Calibration(method="none", offset=0.0, nominal=nominal,
                        fitted_blocks=0,
                        notes="no interval predictions were produced"),
            {"groups": [], "n": 0},
        )

    actual = np.array(actual_all)
    lower = np.array(lower_all)
    upper = np.array(upper_all)

    offset = (conformal_offset(actual, lower, upper, blocks_all, nominal)
              if method == "conformal" else 0.0)
    calibration = Calibration(
        method=method, offset=offset, nominal=nominal,
        fitted_blocks=len(set(blocks_all)),
        exchangeability_claimed=False,
        notes=("split-conformal offset over station-day blocks; the "
               "exchangeability premise between Norwegian station-days and "
               "Gothenburg edges is NOT established, so this is a measured "
               "adjustment, not a coverage guarantee"),
    )

    adjusted_lower = np.clip(lower - offset, 0.1, 0.9)
    adjusted_upper = np.clip(upper + offset, 0.1, 0.9)
    report = coverage_report(actual, adjusted_lower, adjusted_upper,
                             blocks_all, rows_all, nominal=nominal)
    report["before_calibration"] = coverage_report(
        actual, lower, upper, blocks_all, rows_all, nominal=nominal
    )["groups"]
    return calibration, report


def coverage_report(actual: np.ndarray, lower: np.ndarray, upper: np.ndarray,
                    blocks: Sequence[str], rows: Sequence[DirectionRow],
                    *, nominal: float = 0.8) -> dict[str, Any]:
    """Coverage per pre-registered reporting group."""
    from .evaluate import group_of

    groups = [group_of(r) for r in rows]
    results: list[CoverageResult] = []
    for name in ("all", "weekday", "weekend", "weekday_peak", "off_hours"):
        mask = np.array([name in g for g in groups], dtype=bool)
        if not mask.any():
            continue
        results.append(measure_coverage(
            actual[mask], lower[mask], upper[mask],
            [b for b, m in zip(blocks, mask) if m],
            group=name, nominal=nominal,
        ))
    return {
        "schema_version": SCHEMA_VERSION,
        "nominal_coverage": nominal,
        "coverage_tolerance": COVERAGE_TOLERANCE,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "n": int(len(actual)),
        "groups": [r.to_json() for r in results],
        "all_groups_calibrated": bool(results) and all(
            r.is_calibrated for r in results
        ),
    }


def calibration_status(report: Mapping[str, Any]) -> str:
    """Map a coverage report to the schema's calibration_status.

    Deliberately strict: every reported group must be calibrated. A model
    whose interval holds on weekdays but fails at night is not a calibrated
    model — it is a model with a known failure mode, and the honest label for
    the artifact as a whole is `stress_only`.
    """
    groups = report.get("groups") or []
    if not groups:
        return "unavailable"
    return "calibrated" if all(g.get("is_calibrated") for g in groups) \
        else "stress_only"


__all__ = [
    "SCHEMA_VERSION", "COVERAGE_TOLERANCE", "CoverageResult", "Calibration",
    "measure_coverage", "conformal_offset", "fit_calibration",
    "coverage_report", "calibration_status",
]
