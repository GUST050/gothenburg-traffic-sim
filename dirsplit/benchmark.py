"""
Model tournament for the direction split — Gate M of the direction plan.

The question is deliberately narrow: **is there a robust, transferable
conditional signal in the direction share at all, or is 50/50 (plus a local
period anchor where one exists) the honest central estimate?**  A complicated
model is not a product goal; the simplest candidate that survives leakage-free
held-out validation wins.

Four candidates on identical folds:

1. ``constant_5050``  — the traffic-engineering D-factor baseline. FHWA notes
   central urban streets often sit near 50/50, which makes this a real
   competitor rather than a straw man.
2. ``shrunk_dfactor`` — per hour and day type, partially pooled toward 0.5.
   Conditional, but with no street features at all.
3. ``lightgbm_similarity`` — the deployed family: gradient boosting on street
   features, similarity-weighted toward the Gothenburg target edges, with
   station-level weight normalisation.
4. ``beta_binomial``  — a count model on the raw directional counts with an
   explicit overdispersion parameter, because the label IS two counts and
   hour-to-hour variation is wider than binomial.

Three complementary fold families, all fitted strictly inside the training
fold (scaling, bandwidth, shrinkage and every hyperparameter):

* ``leave_city_out``    — geographic transfer, the deployment situation.
* ``leave_station_out`` — a new street inside a known city.
* ``blocked_date``      — future days; requires the v2 raw table, since the
  legacy aggregate has no dates left to hold out.

Uncertainty is reported as a bootstrap over INDEPENDENT GROUPS (day blocks
when available, else stations), never over correlated rows, and the decision
rule below is frozen in source before any fold is measured.

Usage:
  python3 -m dirsplit.benchmark                    # v2 table, full tournament
  python3 -m dirsplit.benchmark --table legacy     # aggregate, partial only

Writes validation/dirsplit_point_benchmark_v1.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .config import COVERAGE_REPORT
from .dataset import (OUT_PATH as LEGACY_TABLE, OUT_PATH_V2 as RAW_TABLE,
                      PROFILE_DIR_FEATURES, PROFILE_FEATURES,
                      PROFILE_TOTAL_FEATURES)
from .features import FEATURE_NAMES

REPORT_PATH = Path("validation/dirsplit_point_benchmark_v1.json")
SCHEMA_VERSION = "dirsplit_point_benchmark_v1"

GATE_BASELINE, GATE_MODEL, GATE_INCONCLUSIVE = "BASELINE", "MODEL", "INCONCLUSIVE"

# ── frozen decision rule (set before any fold was measured) ──────────────────
#: A model must beat 50/50 by at least this much of the baseline's own MAE
#: before the improvement is called material at all.
MIN_IMPROVEMENT_PCT = 5.0
#: ... and the bootstrap interval of the paired per-group difference must
#: exclude zero at this level.
BOOTSTRAP_CONFIDENCE = 0.95
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 42
#: A winning model may not be materially worse than 50/50 in any main group.
MATERIAL_HARM_PCT = 5.0
#: Fewer independent groups than this cannot support a transfer claim.
MIN_INDEPENDENT_GROUPS = 8
#: Weekday hours the deployed model is actually trained on today.  Rows outside
#: this band are reported separately: predicting them is extrapolation, and the
#: report must say so rather than average it away.
SUPPORTED_HOURS = range(6, 21)


@dataclass
class Observation:
    """One labelled direction-share observation at its native grain."""

    station_id: str
    city: str
    heading: str
    hour: int
    is_weekend: int
    share: float
    weight: float
    features: dict[str, float]
    day_block_id: str | None = None
    toward_count: float | None = None
    total_count: float | None = None

    @property
    def station_key(self) -> str:
        return f"{self.station_id}:{self.heading}"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_v2(path: Path = RAW_TABLE, *,
            profile_kind: str = "total") -> list[Observation]:
    """Labelled rows of the raw table, with one explicit feature semantics.

    ``profile_kind`` selects the inference path: ``total`` uses both-direction
    profile statistics (paired_total, the legacy meaning), ``directional`` uses
    this heading's own (single_direction, computable at a Gothenburg station
    that measures one carriageway), ``none`` drops profile features entirely.
    """
    columns = {"total": PROFILE_TOTAL_FEATURES,
               "directional": PROFILE_DIR_FEATURES,
               "none": []}[profile_kind]
    out: list[Observation] = []
    with open(path) as handle:
        for row in csv.DictReader(handle):
            if row.get("included") != "1":
                continue
            features = {name: _float(row[name]) for name in FEATURE_NAMES}
            features.update({name: _float(row[name]) for name in columns})
            out.append(Observation(
                station_id=row["station_id"], city=row["city"],
                heading=row["heading"], hour=int(row["hour"]),
                is_weekend=int(row["is_weekend"]),
                share=float(row["share"]), weight=_float(row["total_count"], 1.0),
                features=features, day_block_id=row["day_block_id"],
                toward_count=_float(row["toward_count"]),
                total_count=_float(row["total_count"])))
    return out


def load_legacy(path: Path = LEGACY_TABLE) -> list[Observation]:
    """The aggregated table: no dates, no raw counts, hence no day blocks."""
    out: list[Observation] = []
    with open(path) as handle:
        for row in csv.DictReader(handle):
            features = {name: _float(row[name]) for name in FEATURE_NAMES}
            features.update({f"profile_total_{name}": _float(row[name])
                             for name in PROFILE_FEATURES})
            out.append(Observation(
                station_id=row["station_id"], city=row["city"],
                heading=row["heading"], hour=int(row["hour"]),
                is_weekend=int(row["is_weekend"]), share=float(row["share"]),
                weight=_float(row["mean_total_veh_h"], 1.0) * _float(row["n_obs"], 1.0),
                features=features, day_block_id=None))
    return out


#: The predicted population is two-way city streets.  A station that is
#: effectively one-way (a ramp, a couplet leg) has a share pinned near 0 or 1,
#: where 50/50 is absurd and every model looks brilliant for the wrong reason —
#: the deployed trainer screens them out for exactly this reason.  The screen
#: is a POPULATION definition applied identically to every candidate before any
#: fold is drawn, never a per-model tuning knob.
ONE_WAY_LOW, ONE_WAY_HIGH = 0.15, 0.85


def screen_two_way_stations(observations: Sequence[Observation]
                            ) -> tuple[list[Observation], list[str]]:
    """Drop stations whose weekday daytime share is pinned near 0 or 1."""
    shares: dict[str, list[float]] = {}
    for obs in observations:
        if obs.is_weekend or obs.hour not in SUPPORTED_HOURS:
            continue
        shares.setdefault(obs.station_key, []).append(obs.share)
    one_way = {key for key, values in shares.items()
               if values and not ONE_WAY_LOW <= (sum(values) / len(values))
               <= ONE_WAY_HIGH}
    kept = [obs for obs in observations if obs.station_key not in one_way]
    return kept, sorted(one_way)


def restrict_to_supported_hours(observations: Sequence[Observation]
                                ) -> list[Observation]:
    """Weekday 06–20 only: the band the LightGBM model was trained on."""
    return [obs for obs in observations
            if not obs.is_weekend and obs.hour in SUPPORTED_HOURS]


def restrict_to_weekdays(observations: Sequence[Observation]
                         ) -> list[Observation]:
    """Every weekday hour — exactly the population the deployed D-factor fits."""
    return [obs for obs in observations if not obs.is_weekend]


def orient_toward_centre(observations: Sequence[Observation]) -> list[Observation]:
    """Keep one row per station-hour: the toward-centre direction.

    The two directions of a station are mirrored duplicates (s, 1-s); keeping
    both double-counts every station with perfectly anticorrelated labels.
    """
    return [obs for obs in observations if obs.features.get("radial_cos", 0.0) > 0]


# ── candidate models ─────────────────────────────────────────────────────────

class Model:
    name = "model"
    requires_counts = False

    def fit(self, train: Sequence[Observation]) -> "Model":
        raise NotImplementedError

    def predict(self, test: Sequence[Observation]) -> np.ndarray:
        raise NotImplementedError


class Constant5050(Model):
    name = "constant_5050"

    def fit(self, train):
        return self

    def predict(self, test):
        return np.full(len(test), 0.5)


class ShrunkDFactor(Model):
    """Per (hour, day type) mean deviation from 0.5, partially pooled.

    The pooling weight ``n / (n + k)`` is the standard hierarchical shrinkage
    of a group mean toward the grand mean; here the grand mean is fixed at the
    D-factor baseline, so a cell with little evidence simply stays at 50/50.
    """

    name = "shrunk_dfactor"

    def __init__(self, prior_strength: float = 25.0):
        self.prior_strength = prior_strength
        self.cells: dict[tuple[int, int], float] = {}
        self.global_deviation = 0.0

    def fit(self, train):
        by_cell: dict[tuple[int, int], list[tuple[float, float]]] = {}
        total_weight = 0.0
        total_deviation = 0.0
        for obs in train:
            by_cell.setdefault((obs.hour, obs.is_weekend), []).append(
                (obs.share - 0.5, obs.weight))
            total_weight += obs.weight
            total_deviation += obs.weight * (obs.share - 0.5)
        self.global_deviation = (total_deviation / total_weight
                                 if total_weight > 0 else 0.0)
        for cell, pairs in by_cell.items():
            n = float(len(pairs))
            weight_sum = sum(weight for _, weight in pairs) or 1.0
            mean = sum(dev * weight for dev, weight in pairs) / weight_sum
            pooled = n / (n + self.prior_strength)
            self.cells[cell] = pooled * mean + (1 - pooled) * self.global_deviation
        return self

    def predict(self, test):
        return np.clip([0.5 + self.cells.get((obs.hour, obs.is_weekend),
                                             self.global_deviation)
                        for obs in test], 0.0, 1.0)


def _design(observations: Sequence[Observation], columns: Sequence[str]
            ) -> np.ndarray:
    rows = []
    for obs in observations:
        hour_radians = 2 * math.pi * obs.hour / 24
        rows.append([obs.features.get(name, 0.0) for name in columns]
                    + [math.sin(hour_radians), math.cos(hour_radians),
                       float(obs.is_weekend)])
    return np.array(rows, dtype=float)


def _station_normalised_weights(observations: Sequence[Observation]) -> np.ndarray:
    """Every station gets equal total influence; hours within one are correlated."""
    counts: dict[str, int] = {}
    for obs in observations:
        counts[obs.station_key] = counts.get(obs.station_key, 0) + 1
    return np.array([1.0 / counts[obs.station_key] for obs in observations])


class LightGBMSimilarity(Model):
    """The deployed family: boosted trees, similarity-weighted to the target.

    One model per fold, weighted toward the Gothenburg target centroid.  The
    deployment additionally fits one model per sensor; that is a locality
    refinement of the same family and multiplies fold cost by the number of
    sensors, so the tournament evaluates the family once per fold and says so.
    """

    name = "lightgbm_similarity"

    def __init__(self, columns: Sequence[str], target_features: np.ndarray | None):
        self.columns = list(columns)
        self.target_features = target_features
        self.model = None
        self.mu = None
        self.sd = None

    def fit(self, train):
        import lightgbm as lgb

        X = _design(train, self.columns)
        y = np.array([obs.share for obs in train])
        static = X[:, :len(FEATURE_NAMES)]
        # Scaling, bandwidth and similarity weights are all fitted INSIDE the
        # training fold — nothing about the held-out rows may inform them.
        self.mu, self.sd = static.mean(axis=0), static.std(axis=0)
        self.sd[self.sd < 1e-9] = 1.0
        z = (static - self.mu) / self.sd
        weights = _station_normalised_weights(train)
        if self.target_features is not None and len(self.target_features):
            centre = ((self.target_features[:, :len(FEATURE_NAMES)].mean(axis=0)
                       - self.mu) / self.sd)
            spread = np.sqrt(((z - z.mean(axis=0)) ** 2).sum(axis=1))
            bandwidth = float(np.median(spread)) or 1.0
            distance = np.sqrt(((z - centre) ** 2).sum(axis=1))
            weights = weights * np.exp(-0.5 * (distance / bandwidth) ** 2)
        self.model = lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, max_depth=5, num_leaves=31,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=20,
            random_state=42, n_jobs=-1, verbose=-1)
        self.model.fit(X, y, sample_weight=weights)
        return self

    def predict(self, test):
        return np.clip(self.model.predict(_design(test, self.columns)), 0.0, 1.0)


class ShrunkLightGBM(Model):
    """The DEPLOYED estimator: the boosted model, then James-Stein shrinkage.

    ``predict.py`` ships ``0.5 + lambda * (prediction - 0.5)`` with a lambda
    regressed on pooled held-out predictions.  Evaluating the unshrunk family
    alone would judge something the product does not use, so the deployed form
    competes too — but its lambda is refitted here on an INNER leave-city-out
    split of the training fold only, never on the fold being scored, which is
    the leak the deployed calibration itself does not avoid.
    """

    name = "lightgbm_similarity_shrunk"

    def __init__(self, columns: Sequence[str], target_features: np.ndarray | None):
        self.columns = list(columns)
        self.target_features = target_features
        self.base = LightGBMSimilarity(columns, target_features)
        self.shrinkage = 1.0

    def fit(self, train):
        inner_groups = sorted({obs.city for obs in train})
        if len(inner_groups) < 2:
            inner_groups = sorted({obs.station_id for obs in train})
            key = lambda obs: obs.station_id            # noqa: E731 — local alias
        else:
            key = lambda obs: obs.city                  # noqa: E731
        deviations_x: list[float] = []
        deviations_y: list[float] = []
        for held in inner_groups[:4]:
            inner_train = [obs for obs in train if key(obs) != held]
            inner_test = [obs for obs in train if key(obs) == held]
            if not inner_train or not inner_test:
                continue
            inner = LightGBMSimilarity(self.columns, self.target_features)
            inner.fit(inner_train)
            predictions = inner.predict(inner_test)
            deviations_x.extend((predictions - 0.5).tolist())
            deviations_y.extend([obs.share - 0.5 for obs in inner_test])
        if deviations_x:
            x = np.array(deviations_x)
            y = np.array(deviations_y)
            self.shrinkage = float(np.clip((x @ y) / max(x @ x, 1e-12), 0.0, 1.0))
        self.base.fit(train)
        return self

    def predict(self, test):
        return np.clip(0.5 + self.shrinkage * (self.base.predict(test) - 0.5),
                       0.0, 1.0)


class BetaBinomial(Model):
    """Logit-link beta-binomial regression on the raw directional counts.

    Two counts per observation is what the source actually delivers, and the
    hour-to-hour spread is wider than a binomial allows, so the dispersion is
    estimated rather than assumed.  Fitted by maximum likelihood with
    station-normalised weights, on features standardised inside the fold.
    """

    name = "beta_binomial"
    requires_counts = True

    def __init__(self, columns: Sequence[str]):
        self.columns = list(columns)
        self.beta: np.ndarray | None = None
        self.mu = None
        self.sd = None

    def fit(self, train):
        from scipy.optimize import minimize
        from scipy.special import betaln

        X = _design(train, self.columns)
        self.mu, self.sd = X.mean(axis=0), X.std(axis=0)
        self.sd[self.sd < 1e-9] = 1.0
        Z = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        successes = np.array([obs.toward_count or 0.0 for obs in train])
        totals = np.array([obs.total_count or 0.0 for obs in train])
        weights = _station_normalised_weights(train)
        keep = totals > 0
        Z, successes, totals, weights = (Z[keep], successes[keep], totals[keep],
                                         weights[keep])
        if not len(Z):
            raise ValueError("beta-binomial needs observations with counts")

        def negative_log_likelihood(params):
            beta, log_phi = params[:-1], params[-1]
            phi = math.exp(min(max(log_phi, -6.0), 12.0))
            eta = np.clip(Z @ beta, -30, 30)
            mean = 1 / (1 + np.exp(-eta))
            a = np.clip(mean * phi, 1e-6, None)
            b = np.clip((1 - mean) * phi, 1e-6, None)
            ll = betaln(successes + a, totals - successes + b) - betaln(a, b)
            return -float(np.sum(weights * ll))

        start = np.zeros(Z.shape[1] + 1)
        start[-1] = math.log(50.0)
        result = minimize(negative_log_likelihood, start, method="L-BFGS-B",
                          options={"maxiter": 500})
        self.beta = result.x[:-1]
        return self

    def predict(self, test):
        X = _design(test, self.columns)
        Z = np.hstack([np.ones((len(X), 1)), (X - self.mu) / self.sd])
        eta = np.clip(Z @ self.beta, -30, 30)
        return 1 / (1 + np.exp(-eta))


# ── folds ────────────────────────────────────────────────────────────────────

Fold = tuple[str, list[int], list[int]]


def leave_city_out(observations: Sequence[Observation]) -> list[Fold]:
    cities = sorted({obs.city for obs in observations})
    folds = []
    for city in cities:
        test = [i for i, obs in enumerate(observations) if obs.city == city]
        train = [i for i, obs in enumerate(observations) if obs.city != city]
        if test and train:
            folds.append((f"city={city}", train, test))
    return folds


def leave_station_out(observations: Sequence[Observation], *,
                      max_folds: int = 25) -> list[Fold]:
    """Hold out whole stations (both headings) — a genuinely new street.

    Capped at ``max_folds`` deterministically ordered stations so the
    tournament stays affordable; the cap is recorded in the report.
    """
    stations = sorted({obs.station_id for obs in observations})
    step = max(1, len(stations) // max_folds)
    folds = []
    for station in stations[::step][:max_folds]:
        test = [i for i, obs in enumerate(observations) if obs.station_id == station]
        train = [i for i, obs in enumerate(observations) if obs.station_id != station]
        if test and train:
            folds.append((f"station={station}", train, test))
    return folds


def blocked_date(observations: Sequence[Observation], *, n_blocks: int = 5
                 ) -> list[Fold]:
    """Hold out contiguous calendar blocks, keeping a day whole everywhere."""
    blocks = sorted({obs.day_block_id for obs in observations
                     if obs.day_block_id})
    if len(blocks) < n_blocks:
        return []
    dates = sorted({block.split(":", 1)[1] for block in blocks})
    size = max(1, len(dates) // n_blocks)
    folds = []
    for index in range(n_blocks):
        start = index * size
        held = set(dates[start:start + size] if index < n_blocks - 1
                   else dates[start:])
        if not held:
            continue
        test = [i for i, obs in enumerate(observations)
                if obs.day_block_id and obs.day_block_id.split(":", 1)[1] in held]
        train = [i for i, obs in enumerate(observations)
                 if obs.day_block_id and obs.day_block_id.split(":", 1)[1] not in held]
        if test and train:
            folds.append((f"dates={min(held)}..{max(held)}", train, test))
    return folds


# ── metrics ──────────────────────────────────────────────────────────────────

def group_key(obs: Observation) -> str:
    """The independent unit for uncertainty: a day block, else a station."""
    return obs.day_block_id or obs.station_key


def paired_group_differences(observations: Sequence[Observation],
                             predictions: np.ndarray) -> dict[str, float]:
    """Per group, mean |model error| minus mean |50/50 error|."""
    sums: dict[str, list[float]] = {}
    for obs, prediction in zip(observations, predictions):
        entry = sums.setdefault(group_key(obs), [0.0, 0.0, 0.0])
        entry[0] += abs(prediction - obs.share)
        entry[1] += abs(0.5 - obs.share)
        entry[2] += 1
    return {key: (value[0] - value[1]) / value[2] for key, value in sums.items()}


def bootstrap_interval(values: Sequence[float], *, samples: int = BOOTSTRAP_SAMPLES,
                       confidence: float = BOOTSTRAP_CONFIDENCE,
                       seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    """Percentile bootstrap over independent groups."""
    values = list(values)
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(draw) / len(draw))
    means.sort()
    tail = (1 - confidence) / 2
    low = means[int(tail * (len(means) - 1))]
    high = means[int((1 - tail) * (len(means) - 1))]
    return low, high


def score(observations: Sequence[Observation], predictions: np.ndarray) -> dict:
    errors = np.abs(predictions - np.array([obs.share for obs in observations]))
    baseline = np.abs(0.5 - np.array([obs.share for obs in observations]))
    weights = np.array([obs.weight for obs in observations])
    weight_sum = weights.sum() or 1.0
    differences = paired_group_differences(observations, predictions)
    interval = bootstrap_interval(list(differences.values()))
    supported = np.array([obs.hour in SUPPORTED_HOURS and not obs.is_weekend
                          for obs in observations])
    return {
        "n": len(observations),
        "mae": float(errors.mean()),
        "weighted_mae": float((errors * weights).sum() / weight_sum),
        "baseline_mae": float(baseline.mean()),
        "improvement_pct": float(100 * (1 - errors.mean() / baseline.mean()))
        if baseline.mean() > 0 else 0.0,
        "n_groups": len(differences),
        "mean_group_difference": float(np.mean(list(differences.values()))),
        "bootstrap_ci": list(interval) if interval else None,
        "mae_supported_hours": float(errors[supported].mean()) if supported.any() else None,
        "mae_unsupported_hours": (float(errors[~supported].mean())
                                  if (~supported).any() else None),
        "n_unsupported_hours": int((~supported).sum()),
    }


# ── the tournament ───────────────────────────────────────────────────────────

def target_feature_matrix() -> np.ndarray | None:
    """Gothenburg sensor-edge features, for covariate-shift weighting.

    Read from the tracked coverage report when present so the tournament does
    not need an OSM graph; falls back to computing them, and to None (no
    similarity weighting, recorded as such) when neither is available.
    """
    if COVERAGE_REPORT.exists():
        report = json.loads(COVERAGE_REPORT.read_text())
        rows = [[edge["features"][name] for name in FEATURE_NAMES]
                for edge in report.get("edges", []) if edge.get("features")]
        if rows:
            return np.array(rows, dtype=float)
    try:
        from .coverage import target_matrix
        matrix, _meta = target_matrix()
        return matrix
    except Exception:                       # noqa: BLE001 — optional input
        return None


def build_models(columns: Sequence[str], target: np.ndarray | None,
                 *, with_counts: bool) -> list[Model]:
    models: list[Model] = [Constant5050(), ShrunkDFactor()]
    try:
        import lightgbm            # noqa: F401 — availability probe
        models.append(LightGBMSimilarity(columns, target))
        models.append(ShrunkLightGBM(columns, target))
    except ImportError:
        pass
    if with_counts:
        models.append(BetaBinomial(columns))
    return models


def run_family(name: str, folds: Sequence[Fold], observations: Sequence[Observation],
               models: Sequence[Model], factory: Callable[[Model], Model]) -> dict:
    """Evaluate every model on one fold family, refitting inside each fold."""
    per_model: dict[str, Any] = {}
    for template in models:
        fold_scores = []
        pooled_observations: list[Observation] = []
        pooled_predictions: list[float] = []
        for fold_name, train_index, test_index in folds:
            train = [observations[i] for i in train_index]
            test = [observations[i] for i in test_index]
            model = factory(template)
            try:
                model.fit(train)
                predictions = np.asarray(model.predict(test), dtype=float)
            except Exception as error:      # noqa: BLE001 — one model's failure
                fold_scores.append({"fold": fold_name,
                                    "error": f"{type(error).__name__}: {error}"})
                continue
            fold_scores.append({"fold": fold_name, **score(test, predictions)})
            pooled_observations.extend(test)
            pooled_predictions.extend(predictions.tolist())
        entry: dict[str, Any] = {"folds": fold_scores}
        if pooled_observations:
            entry["pooled"] = score(pooled_observations,
                                    np.array(pooled_predictions))
            entry["by_city"] = {
                city: score([obs for obs in pooled_observations if obs.city == city],
                            np.array([p for obs, p in zip(pooled_observations,
                                                          pooled_predictions)
                                      if obs.city == city]))
                for city in sorted({obs.city for obs in pooled_observations})
            }
        per_model[template.name] = entry
    return {"family": name, "n_folds": len(folds), "models": per_model}


def classify_gate_m(families: Mapping[str, Any], *,
                    day_blocks_available: bool,
                    n_independent_groups: int,
                    notes: Sequence[str] = ()) -> dict:
    """Apply the frozen Gate M rule to the measured families.

    ``BASELINE`` is a complete, successful outcome: it says a conditional mean
    has not been shown to beat 50/50, not that direction variance is zero —
    that is Gate S's separate question.
    """
    reasons = list(notes)
    if not day_blocks_available:
        reasons.append(
            "no independent day blocks: the aggregated table cannot support a "
            "blocked-date fold, so future-day transfer is unmeasured")
    if n_independent_groups < MIN_INDEPENDENT_GROUPS:
        reasons.append(
            f"only {n_independent_groups} independent groups, below the frozen "
            f"minimum of {MIN_INDEPENDENT_GROUPS}")

    summary: dict[str, Any] = {}
    for model_name in sorted({name for family in families.values()
                              for name in family["models"]}):
        if model_name == Constant5050.name:
            continue
        wins, harms, per_family = 0, [], {}
        for family_name, family in families.items():
            entry = family["models"].get(model_name, {})
            pooled = entry.get("pooled")
            if not pooled:
                continue
            interval = pooled.get("bootstrap_ci")
            material = pooled["improvement_pct"] >= MIN_IMPROVEMENT_PCT
            robust = bool(interval and interval[1] < 0)
            per_family[family_name] = {
                "improvement_pct": pooled["improvement_pct"],
                "bootstrap_ci": interval, "material": material, "robust": robust}
            if material and robust:
                wins += 1
            for city, city_score in (entry.get("by_city") or {}).items():
                if city_score["improvement_pct"] < -MATERIAL_HARM_PCT:
                    harms.append(f"{family_name}/{city}")
        summary[model_name] = {"families": per_family, "wins": wins,
                               "material_harm_groups": sorted(set(harms))}

    winners = [name for name, entry in summary.items()
               if entry["wins"] == len([f for f in families
                                        if name in families[f]["models"]])
               and entry["wins"] > 0 and not entry["material_harm_groups"]]

    if reasons:
        verdict = GATE_INCONCLUSIVE
    elif winners:
        verdict = GATE_MODEL
    else:
        verdict = GATE_BASELINE
        reasons.append(
            "no candidate beat 50/50 by at least "
            f"{MIN_IMPROVEMENT_PCT}% with a bootstrap interval excluding zero "
            "in every fold family")
    return {
        "verdict": verdict,
        "winners": sorted(winners),
        "reasons": reasons,
        "per_model": summary,
        "rule": {
            "min_improvement_pct": MIN_IMPROVEMENT_PCT,
            "bootstrap_confidence": BOOTSTRAP_CONFIDENCE,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "material_harm_pct": MATERIAL_HARM_PCT,
            "min_independent_groups": MIN_INDEPENDENT_GROUPS,
            "bootstrap_unit": "independent day block, else station-direction",
        },
    }


def tournament(observations: Sequence[Observation], *, profile_kind: str,
               table: str, hours: str = "all") -> dict:
    observations = orient_toward_centre(observations)
    observations, one_way = screen_two_way_stations(observations)
    if hours == "supported":
        observations = restrict_to_supported_hours(observations)
    elif hours == "weekday":
        observations = restrict_to_weekdays(observations)
    if not observations:
        raise ValueError("no toward-centre observations to evaluate")
    columns = list(FEATURE_NAMES)
    profile_columns = {"total": PROFILE_TOTAL_FEATURES,
                       "directional": PROFILE_DIR_FEATURES, "none": []}[profile_kind]
    columns += profile_columns
    with_counts = all(obs.total_count for obs in observations[:50])
    models = build_models(columns, target_feature_matrix(), with_counts=with_counts)

    families: dict[str, Any] = {}
    for name, folds in (("leave_city_out", leave_city_out(observations)),
                        ("leave_station_out", leave_station_out(observations)),
                        ("blocked_date", blocked_date(observations))):
        if not folds:
            continue
        families[name] = run_family(name, folds, observations, models,
                                    factory=_fresh)
    groups = {group_key(obs) for obs in observations}
    notes = []
    if not any(isinstance(model, LightGBMSimilarity) for model in models):
        notes.append("lightgbm unavailable: the deployed model family was not "
                     "evaluated, so no verdict about it is possible")
    if not with_counts:
        notes.append("raw directional counts unavailable: the count model was "
                     "not evaluated")
    gate = classify_gate_m(
        families,
        day_blocks_available="blocked_date" in families,
        n_independent_groups=len(groups),
        notes=notes)
    return {
        "schema_version": SCHEMA_VERSION,
        "release_evidence": False,
        "table": table,
        "population": {
            "definition": "two-way city streets, toward-centre direction",
            "hours": hours,
            "one_way_screen": [ONE_WAY_LOW, ONE_WAY_HIGH],
            "one_way_station_directions_dropped": len(one_way),
        },
        "profile_feature_semantics": profile_kind,
        "inference_path": {"total": "paired_total",
                           "directional": "single_direction",
                           "none": "no_profile_features"}[profile_kind],
        "n_observations": len(observations),
        "n_independent_groups": len(groups),
        "models_evaluated": [model.name for model in models],
        "families": families,
        "gate_m": gate,
    }


def _fresh(template: Model) -> Model:
    """A clean, unfitted copy — no state may cross a fold boundary."""
    if isinstance(template, ShrunkLightGBM):
        return ShrunkLightGBM(template.columns, template.target_features)
    if isinstance(template, LightGBMSimilarity):
        return LightGBMSimilarity(template.columns, template.target_features)
    if isinstance(template, BetaBinomial):
        return BetaBinomial(template.columns)
    if isinstance(template, ShrunkDFactor):
        return ShrunkDFactor(template.prior_strength)
    return Constant5050()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--table", choices=("v2", "legacy"), default="v2",
                        help="v2 = raw observations (default); legacy = the "
                             "aggregated diagnostic table, which cannot decide "
                             "Gate M because it has no day blocks.")
    parser.add_argument("--profile-features",
                        choices=("total", "directional", "none"), default="total",
                        help="Which profile-feature semantics to evaluate.")
    parser.add_argument("--hours", choices=("all", "weekday", "supported"),
                        default="all",
                        help="all = every hour the source reports (weekend and "
                             "off-hours support is then measured, not assumed); "
                             "weekday = every weekday hour, the population the "
                             "deployed D-factor profile fits; supported = "
                             "weekday 06-20, the band the LightGBM model was "
                             "trained on.")
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    if args.table == "v2":
        if not RAW_TABLE.exists():
            print(f"{RAW_TABLE} is missing — run `python3 -m dirsplit.dataset` "
                  f"after fetching volumes (`make dirsplit-volumes`).")
            return 2
        observations = load_v2(RAW_TABLE, profile_kind=args.profile_features)
    else:
        if not LEGACY_TABLE.exists():
            print(f"{LEGACY_TABLE} is missing.")
            return 2
        observations = load_legacy(LEGACY_TABLE)

    report = tournament(observations, profile_kind=args.profile_features,
                        table=args.table, hours=args.hours)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")

    print(f"{report['n_observations']} observations, "
          f"{report['n_independent_groups']} independent groups, "
          f"models: {', '.join(report['models_evaluated'])}")
    for family_name, family in report["families"].items():
        print(f"\n{family_name} ({family['n_folds']} folds)")
        for model_name, entry in family["models"].items():
            pooled = entry.get("pooled")
            if not pooled:
                print(f"  {model_name:<22} (no pooled result)")
                continue
            interval = pooled["bootstrap_ci"]
            print(f"  {model_name:<22} MAE {pooled['mae']:.4f} "
                  f"(50/50 {pooled['baseline_mae']:.4f}, "
                  f"{pooled['improvement_pct']:+.1f}%)"
                  + (f"  CI[{interval[0]:+.4f}, {interval[1]:+.4f}]"
                     if interval else ""))
    gate = report["gate_m"]
    print(f"\nGate M: {gate['verdict']}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
