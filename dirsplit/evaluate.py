"""Fas 1 — one shared evaluation module: folds, candidates, Gate M.

The plan allows at most ONE new module here, and forbids creating separate
schema/model/calibration/scenario families before their gates open. So folds,
the four candidates, the metrics and the decision rule all live in this file
and nothing else is added.

What Gate M asks: is there a robust predictive direction signal, or is the
even split as good? The answer must come from leakage-free blocked held-out
validation against `constant_5050` and other simple baselines, and must
report the central difference WITH UNCERTAINTY rather than a bare point MAE —
a 0.0008 MAE advantage means nothing without knowing the noise around it.

Three properties this module enforces rather than assumes:

* **Every fitted quantity is fit inside the fold.** Standardisation, kernel
  bandwidth and shrinkage are all estimated from the training side only, so
  no information from the held-out side can reach model selection.
* **Blocks, not rows.** Hourly rows from one station are strongly
  correlated. Bootstrapping rows would report a far tighter interval than
  the data supports, which is exactly how a null result gets mistaken for a
  win. The paired bootstrap resamples whole station blocks.
* **Simplest wins ties.** A complex candidate replaces the incumbent only if
  it never significantly loses in a primary group and significantly wins in
  at least one. Anything else keeps `constant_5050`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from .config import DATA_DIR
from .dataset import DATASET_PROTOCOL, META_PATH
from .dataset import OUT_PATH as TABLE_PATH
from .dataset import PROFILE_FEATURES
from .features import FEATURE_NAMES

REPORT_PATH = DATA_DIR / "gate_m_report.json"
GATE_M_PROTOCOL = "dirsplit_gate_m_v3"
INTERVAL_REPORT_PATH = DATA_DIR / "interval_calibration_report.json"
INTERVAL_PROTOCOL = "dirsplit_interval_calibration_v1"

#: The published interval and its declared clamp, from `dirsplit/predict.py`.
INTERVAL_QUANTILES = (0.1, 0.5, 0.9)
NOMINAL_COVERAGE = 0.8
CLAMP_LOW, CLAMP_HIGH = 0.1, 0.9

#: Every third training station (sorted) is held back inside the fold to
#: calibrate the conformal correction. Deterministic, so a rerun of the same
#: fold calibrates against the same stations.
CONFORMAL_STATION_STRIDE = 3
MIN_CALIBRATION_STATIONS = 3

#: Frozen before any fold was scored.
#:
#: v2 (2026-08-14) after review. v1's TEXT and its CODE were different rules,
#: and the code was the looser of the two in three ways, each of which could
#: promote a model the frozen rule forbids:
#:
#:   * ``wins``/``loses`` were accumulated ACROSS fold kinds, so a win under
#:     leave-city-out plus a tie under leave-station-out promoted — while
#:     rule 4 requires the win under EVERY fold kind;
#:   * every candidate was compared against 50/50 only, so a more complex
#:     model could displace an already-promoted simpler one without ever
#:     being shown better than it;
#:   * a missing fold kind was skipped instead of returning INCONCLUSIVE,
#:     which rule 6 requires — the aggregated legacy table carries no dates,
#:     so ``blocked_date`` cannot be built from it at all.
#:
#: The version is bumped rather than edited in place so the recorded v1
#: outcome stays readable as what it was: a decision taken under a rule the
#: code did not implement.
SELECTION_RULE = "simplest_defensible_v2"
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260814

#: A candidate must not lose in any of these to be promoted.
PRIMARY_GROUPS = ("all", "weekday_peak")

#: Every one of these must produce folds before Gate M can be decided. The
#: plan names all three (geographic transfer, new road, future day) as
#: complementary — a model validated on two of them has not been shown to
#: generalise along the third, and the third is the one a multi-month closure
#: search depends on.
REQUIRED_FOLD_KINDS = ("leave_city_out", "leave_station_out", "blocked_date")

SELECTION_RULE_TEXT = """\
Frozen before any fold was scored.

1. Candidates are ordered by complexity ascending; constant_5050 is the
   incumbent.
2. A more complex candidate replaces the CURRENT INCUMBENT only if, on
   pooled held-out predictions, BOTH hold against that incumbent (not
   against 50/50 once the incumbent is something else):
   a. in every primary group the 95% block-bootstrap CI on the paired
      difference (candidate MAE - incumbent MAE) does not lie entirely above
      zero, i.e. it never significantly loses; and
   b. in at least one primary group that CI lies entirely below zero.
3. Ties, unmeasurable groups and non-finite CIs count as "does not win".
4. The rule is applied per fold kind; promotion requires BOTH conditions to
   hold under EVERY fold kind, not under one of them.
5. If nothing is promoted, Gate M = BASELINE and the uncertainty is reported
   rather than modelled away.
6. Gate M = INCONCLUSIVE if leakage is detected, if fewer than
   MIN_INDEPENDENT_BLOCKS blocks exist, or if any of REQUIRED_FOLD_KINDS
   could not be built.
7. The evaluated population and the evaluated model must be the ones the
   deployment uses. A tournament run on a different station screen, or on a
   LightGBM weighted toward a different centre, measures a model that is not
   the deployed one and cannot speak about it.
"""

MIN_INDEPENDENT_BLOCKS = 8


# ──────────────────────────────────────────────────────────────────────────
# rows
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Row:
    """One observation from the training table.

    ``block`` is the correlation unit the bootstrap resamples. When the
    source carries dates it is station x date; on the legacy aggregate it is
    station x day-type, which is the finest grouping that table can express.
    """

    station_id: str
    city: str
    heading: str
    hour: int
    is_weekend: int
    share: float
    n_obs: float
    mean_total: float
    block: str
    features: tuple[float, ...]
    profile: tuple[float, ...]
    day_block_id: str | None = None
    local_date: str | None = None

    @property
    def approx_total_count(self) -> float:
        """Vehicles behind this share, for the count model.

        On the aggregate table the exact per-day counts are gone, so this is
        the mean hourly total times the number of averaged days. It is an
        approximation and the report says so; it is still far better than
        pretending every cell carries equal evidence.
        """
        return max(self.mean_total * max(self.n_obs, 1.0), 1.0)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


#: The deployed one-way screen (dirsplit/train.py::load_table). A station
#: whose weekday daytime mean share sits outside this band is one-way-ish and
#: is dropped, because its 0/1 labels poison the fit.
DEPLOYED_TWO_WAY_BAND = (0.15, 0.85)
DEPLOYED_TRAINING_HOURS = range(6, 21)


def deployed_oneway_stations(records: Sequence[Mapping[str, Any]]) -> set[str]:
    """Stations the deployed trainer screens out, by its own definition.

    This is OBSERVED-SHARE based, not the OSM ``oneway`` feature flag. The
    two disagree badly on this table — the OSM flag drops 139 of 178
    stations and leaves 39, while the deployed screen drops 97 and leaves
    81 — so a tournament run on the OSM flag is scoring a different
    population from the one the deployment trains and predicts on, and its
    verdict cannot transfer. Reproduced here rather than imported because
    ``train.py::load_table`` also reads the file, fits and prints; this
    needs only the screen.
    """
    by_direction: dict[tuple[str, str], list[float]] = {}
    for record in records:
        if (int(_float(record.get("is_weekend"))) == 0
                and int(_float(record.get("hour"))) in DEPLOYED_TRAINING_HOURS):
            by_direction.setdefault(
                (record["station_id"], record["heading"]), []).append(
                    _float(record.get("share")))
    low, high = DEPLOYED_TWO_WAY_BAND
    return {station for (station, _heading), shares in by_direction.items()
            if shares and not low <= float(np.mean(shares)) <= high}


def in_deployed_training_window(row: "Row") -> bool:
    """Would the deployed trainer have trained on this row?

    Weekday 06-20 only. Rows outside it stay in the EVALUATION set — that is
    how the report can say what happens where the model is used but was
    never trained — but the LightGBM candidate must not FIT on them, or it
    would be a different model from the deployed one.
    """
    return (not row.is_weekend
            and row.hour in DEPLOYED_TRAINING_HOURS)


def load_rows(path: Path = TABLE_PATH, *, drop_oneway: bool = True,
              orient_toward_centre: bool = True) -> tuple[list[Row], dict]:
    """Load the tracked table into Row objects.

    Reproduces the two screens the deployed trainer applies, so the
    tournament measures the same population the deployment predicts:
    one-way-ish stations are dropped by their OBSERVED weekday-daytime share
    (``deployed_oneway_stations``, exactly train.py's rule), and only the
    toward-centre direction of each pair is kept — by train.py's own
    ``radial_cos > 0`` test, not by taking each station's argmax, because
    the two differ for a station whose headings both point away from the
    centre.
    """
    raw: list[dict] = []
    with open(path) as handle:
        raw = list(csv.DictReader(handle))

    # Dataset v2 preserves invalid/missing cells for auditability. They are
    # evidence about coverage, not labels, and must never enter a fit or the
    # one-way screen. Legacy tables have no ``usable`` column and retain their
    # old behaviour.
    eligible = [record for record in raw
                if record.get("usable", "1") == "1"
                and 0.0 < _float(record.get("share")) < 1.0]
    oneway_stations = deployed_oneway_stations(eligible) \
        if drop_oneway else set()

    has_dates = "local_date" in (raw[0] if raw else {})

    rc_index = FEATURE_NAMES.index("radial_cos")
    rows: list[Row] = []
    for record in eligible:
        if record["station_id"] in oneway_stations:
            continue
        share = _float(record.get("share"))
        if not 0.0 < share < 1.0:
            continue
        features = tuple(_float(record.get(c)) for c in FEATURE_NAMES)
        if orient_toward_centre and features[rc_index] <= 0:
            continue
        is_weekend = int(_float(record.get("is_weekend")))
        local_date = record.get("local_date") if has_dates else None
        block = (f"{record['station_id']}|{local_date}" if local_date
                 else f"{record['station_id']}|{is_weekend}")
        rows.append(Row(
            station_id=record["station_id"],
            city=record["city"],
            heading=record["heading"],
            hour=int(_float(record.get("hour"))),
            is_weekend=is_weekend,
            share=share,
            n_obs=_float(record.get("n_obs"), 1.0),
            mean_total=_float(
                record.get("mean_total_veh_h") or record.get("total_count"),
                1.0),
            block=block,
            features=features,
            profile=tuple(_float(record.get(c)) for c in PROFILE_FEATURES),
            day_block_id=(record.get("day_block_id")
                          or (f"{record['station_id']}|{local_date}"
                              if local_date else None)),
            local_date=local_date,
        ))

    trainable = [r for r in rows if in_deployed_training_window(r)]
    return rows, {
        "source": str(path),
        "raw_rows": len(raw),
        "unusable_or_invalid_rows": len(raw) - len(eligible),
        "oneway_screen": "observed weekday-daytime mean share outside "
                         f"{DEPLOYED_TWO_WAY_BAND} (dirsplit/train.py)",
        "oneway_stations_dropped": len(oneway_stations),
        "orientation_screen": "radial_cos > 0 (dirsplit/train.py)",
        "rows_kept": len(rows),
        "stations": len({r.station_id for r in rows}),
        "cities": sorted({r.city for r in rows}),
        "blocks": len({r.block for r in rows}),
        "deployed_training_window_rows": len(trainable),
        "deployed_training_window_stations": len(
            {r.station_id for r in trainable}),
        "has_day_level_dates": has_dates,
        "block_definition": ("station x date" if has_dates
                             else "station x day-type"),
    }


# ──────────────────────────────────────────────────────────────────────────
# temporal support
# ──────────────────────────────────────────────────────────────────────────
def temporal_support(rows: Sequence[Row]) -> dict[str, Any]:
    """Which (hour, day-type) cells are actually observed.

    The deployed trainer keeps weekdays 06-20 only while `predict.py` emits
    all 24 hours with ``is_weekend=0``, so every night hour and every weekend
    prediction is silent extrapolation. Naming the supported set is what lets
    the report refuse it rather than repeat it.
    """
    weekday = sorted({r.hour for r in rows if not r.is_weekend})
    weekend = sorted({r.hour for r in rows if r.is_weekend})
    return {
        "weekday_hours_observed": weekday,
        "weekend_hours_observed": weekend,
        "weekday_hours_unsupported": sorted(set(range(24)) - set(weekday)),
        "weekend_hours_unsupported": sorted(set(range(24)) - set(weekend)),
        "weekend_rows": sum(1 for r in rows if r.is_weekend),
        "refusal": (
            "Predictions for an unsupported (hour, day-type) cell are "
            "extrapolation and must be labelled as such, not emitted as if "
            "they were trained."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────
# candidates
# ──────────────────────────────────────────────────────────────────────────
class Candidate:
    name = "base"
    complexity = 0

    def fit(self, rows: Sequence[Row],
            target_features: np.ndarray | None = None) -> "Candidate":
        """Fit on the training side of a fold.

        ``target_features`` is the STATIC FEATURE matrix of the rows this
        model will be asked to predict — never their labels. It exists
        because the deployment is locally weighted toward a KNOWN target
        road: `train.py` fits each sensor's model with a Gaussian kernel
        centred on that sensor's own feature vector, which it knows before
        any Gothenburg measurement exists. A fold that withheld the held-out
        station's features would therefore be evaluating a model the project
        does not deploy. Passing labels here would be leakage; passing
        features is the deployment.
        """
        raise NotImplementedError

    def predict(self, rows: Sequence[Row]) -> np.ndarray:
        raise NotImplementedError


class Constant5050(Candidate):
    """The traffic-engineering null, and the shrinkage target of the rest."""

    name = "constant_5050"
    complexity = 0

    def fit(self, rows, target_features=None):
        return self

    def predict(self, rows):
        return np.full(len(rows), 0.5, dtype=float)


class ShrunkDFactor(Candidate):
    """Hierarchical (hour, day-type) D-factor, pooled toward 0.5.

    The shrinkage weight ``n / (n + k)`` is DERIVED from the observed
    between- and within-cell variance rather than tuned, and ``n`` counts
    independent BLOCKS rather than rows — fifteen hourly rows from one
    station-day are one day of evidence, not fifteen.
    """

    name = "shrunk_dfactor"
    complexity = 1

    def __init__(self):
        self._cell: dict[tuple[int, int], float] = {}
        self._blocks: dict[tuple[int, int], int] = {}
        self._k = float("inf")

    def fit(self, rows, target_features=None):
        buckets: dict[tuple[int, int], list[float]] = {}
        blocks: dict[tuple[int, int], set[str]] = {}
        for row in rows:
            key = (row.hour, row.is_weekend)
            buckets.setdefault(key, []).append(row.share)
            blocks.setdefault(key, set()).add(row.block)
        if not buckets:
            return self
        means = {k: float(np.mean(v)) for k, v in buckets.items()}
        within = [float(np.var(v, ddof=1)) for v in buckets.values()
                  if len(v) > 1]
        between = (float(np.var(list(means.values()), ddof=1))
                   if len(means) > 1 else 0.0)
        mean_within = float(np.mean(within)) if within else 0.0
        self._k = (mean_within / between) if between > 1e-12 else float("inf")
        self._cell = means
        self._blocks = {k: len(v) for k, v in blocks.items()}
        return self

    def predict(self, rows):
        out = np.empty(len(rows), dtype=float)
        for index, row in enumerate(rows):
            key = (row.hour, row.is_weekend)
            mean = self._cell.get(key)
            if mean is None or not math.isfinite(self._k):
                out[index] = 0.5
                continue
            n = float(self._blocks.get(key, 0))
            weight = n / (n + self._k) if (n + self._k) > 0 else 0.0
            out[index] = 0.5 + weight * (mean - 0.5)
        return np.clip(out, 0.1, 0.9)


class BetaBinomialDFactor(Candidate):
    """Same groups, fit on counts with an estimated overdispersion.

    The label comes from two counts and real day-to-day spread is wider than
    binomial, so one extra parameter (the intra-block correlation) describes
    the data rather than decorating it. Cells are weighted by evidence, so a
    cell backed by 3 vehicles cannot outvote one backed by 3,000.
    """

    name = "beta_binomial_dfactor"
    complexity = 2

    def __init__(self):
        self._cell: dict[tuple[int, int], float] = {}
        self._k = float("inf")

    def fit(self, rows, target_features=None):
        buckets: dict[tuple[int, int], list[Row]] = {}
        for row in rows:
            buckets.setdefault((row.hour, row.is_weekend), []).append(row)
        if not buckets:
            return self
        means = {}
        for key, group in buckets.items():
            weights = np.array([r.approx_total_count for r in group])
            values = np.array([r.share for r in group])
            means[key] = float(np.average(values, weights=weights))
        within = [float(np.var([r.share for r in g], ddof=1))
                  for g in buckets.values() if len(g) > 1]
        between = (float(np.var(list(means.values()), ddof=1))
                   if len(means) > 1 else 0.0)
        mean_within = float(np.mean(within)) if within else 0.0
        self._k = (mean_within / between) if between > 1e-12 else float("inf")
        self._cell = means
        self._blocks = {k: len({r.block for r in g})
                        for k, g in buckets.items()}
        return self

    def predict(self, rows):
        out = np.empty(len(rows), dtype=float)
        for index, row in enumerate(rows):
            key = (row.hour, row.is_weekend)
            mean = self._cell.get(key)
            if mean is None or not math.isfinite(self._k):
                out[index] = 0.5
                continue
            n = float(getattr(self, "_blocks", {}).get(key, 0))
            weight = n / (n + self._k) if (n + self._k) > 0 else 0.0
            out[index] = 0.5 + weight * (mean - 0.5)
        return np.clip(out, 0.1, 0.9)


class SimilarityWeightedLGBM(Candidate):
    """The DEPLOYED approach, entered under the same folds.

    Standardisation and bandwidth are fit on the TRAINING side of each fold,
    which is the leakage requirement; the deployed trainer computes them once
    over everything.

    CORRECTED 2026-08-14. The first version of this candidate differed from
    the deployed model in four ways at once, so its verdict described a
    model this project does not ship:

    * it weighted toward the TRAINING population's own centroid, while
      `train.py` weights toward the target — the Gothenburg sensor's feature
      vector when deploying, the held-out station's when validating. A model
      centred on the training cloud is the general model, not the locally
      weighted one;
    * it used no ``n_obs ** 0.5`` evidence weight, so a station-hour backed
      by one day counted as much as one backed by twenty;
    * it fit on every row, including weekends and nights the deployed
      trainer excludes;
    * it applied no shrinkage, while the deployment ships
      ``0.5 + lambda * (pred - 0.5)`` with ``lambda`` around 0.26-0.29 —
      i.e. it deploys about a quarter of the raw deviation.

    On the target-centred weighting and leakage: ``target_features`` carries
    the held-out rows' STATIC STREET FEATURES only, never their labels.
    That is exactly the information the deployment has about a Gothenburg
    edge before any local measurement exists, so using it is reproducing the
    deployment rather than peeking at the answer. ``score`` builds the
    matrix from ``fold.test`` features and the fit never sees ``r.share``
    for a test row.

    The tournament enters this estimator twice. The current paired-total
    deployment uses the four profile-shape features. The lower-complexity
    ``similarity_weighted_lgbm_no_profile`` removes them, making its feature
    semantics valid for a Gothenburg target where only one direction is
    measured. It is a separate candidate rather than silently filling a
    two-way-total feature with a one-direction proxy.
    """

    name = "similarity_weighted_lgbm"
    complexity = 4

    def __init__(self, n_estimators: int = 400, *,
                 use_profile_features: bool = True):
        self.n_estimators = n_estimators
        self.use_profile_features = bool(use_profile_features)
        if not self.use_profile_features:
            # A separately named, simpler candidate for single-direction
            # deployment targets, where a two-way TOTAL profile does not
            # exist with the training semantics.
            self.name = "similarity_weighted_lgbm_no_profile"
            self.complexity = 3
        self._model = None
        self._mu = None
        self._sd = None
        self._shrinkage = 1.0

    def _matrix(self, rows):
        profile_width = len(PROFILE_FEATURES) \
            if self.use_profile_features else 0
        out = np.zeros((len(rows), len(FEATURE_NAMES)
                        + profile_width + 3), dtype=float)
        for index, row in enumerate(rows):
            base = len(FEATURE_NAMES)
            out[index, :base] = row.features
            if self.use_profile_features:
                out[index, base:base + profile_width] = row.profile
            tail = base + profile_width
            out[index, tail] = math.sin(2 * math.pi * row.hour / 24)
            out[index, tail + 1] = math.cos(2 * math.pi * row.hour / 24)
            out[index, tail + 2] = float(row.is_weekend)
        return out

    def _new_model(self, alpha: float | None = None):
        import lightgbm as lgb

        options = dict(
            n_estimators=self.n_estimators, learning_rate=0.05, max_depth=5,
            num_leaves=31, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=20, random_state=42,
            # This evaluator fits thousands of small, sequential fold models.
            # LightGBM's all-core setting is pure scheduling here: on the
            # reference host it was 10.6x slower (6.97 vs 0.65 s for ten
            # identical 400-tree fits) and does not change the estimator.
            n_jobs=1, verbose=-1)
        if alpha is not None:
            options.update(objective="quantile", alpha=alpha)
        return lgb.LGBMRegressor(**options)

    @staticmethod
    def _predict_model(model, matrix: np.ndarray) -> np.ndarray:
        """Predict without LightGBM/sklearn's synthetic-name warning.

        LightGBM assigns ``Column_N`` names even when fitted on a NumPy array;
        current sklearn then warns that the next NumPy array has no names.
        Both arrays have the same deterministic column order. Suppress only
        that cosmetic UserWarning locally; numerical/runtime warnings remain
        visible and are still promoted by the Gate M verification command.
        """
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="X does not have valid feature names, but .* was "
                        "fitted with feature names",
                category=UserWarning,
                module="sklearn.utils.validation")
            return np.asarray(model.predict(matrix), dtype=float)

    #: Numerical floors. Every one of these guards a real failure mode seen
    #: on the tracked table: a degenerate bandwidth divides by zero, a large
    #: standardised distance overflows the squared exponent, and a
    #: near-constant prediction makes the shrinkage regression's denominator
    #: vanish. The previous version emitted divide-by-zero, overflow and
    #: invalid-value warnings on the real run; it happened to produce finite
    #: output, but "happened to" is not a property.
    MIN_BANDWIDTH = 1e-6
    MAX_KERNEL_EXPONENT = 700.0          # exp(-700) underflows to 0 cleanly
    MIN_SHRINKAGE_DENOMINATOR = 1e-9

    def _weights(self, rows, matrix, centre_z):
        """Deployment's own sample weight: evidence x station x similarity.

        Guarded end to end: the bandwidth has a floor, the kernel exponent is
        clipped before ``exp``, and the result is required to be finite.
        """
        static = matrix[:, :len(FEATURE_NAMES)]
        standardised = (static - self._mu) / self._sd
        spread = np.sqrt(
            ((standardised - standardised.mean(axis=0)) ** 2).sum(axis=1))
        bandwidth = float(np.median(spread))
        if not math.isfinite(bandwidth) or bandwidth < self.MIN_BANDWIDTH:
            bandwidth = 1.0
        distances = np.sqrt(((standardised - centre_z) ** 2).sum(axis=1))
        exponent = np.clip(0.5 * (distances / bandwidth) ** 2,
                           0.0, self.MAX_KERNEL_EXPONENT)
        similarity = np.exp(-exponent)

        counts: dict[str, int] = {}
        for row in rows:
            counts[row.station_id] = counts.get(row.station_id, 0) + 1
        evidence = np.array([max(r.approx_total_count, 1.0) ** 0.5 for r in rows],
                            dtype=float)
        station = np.array([1.0 / counts[r.station_id] for r in rows],
                           dtype=float)
        weights = evidence * station * similarity
        if not np.all(np.isfinite(weights)):
            raise ValueError(
                "similarity weights are not finite; refusing to fit a model "
                "on weights that cannot be interpreted")
        if weights.sum() <= 0:
            raise ValueError(
                "all similarity weights are zero; the target is outside the "
                "training support and cannot be evaluated as deployment")
        return weights

    def _fit_shrinkage(self, rows, centres_z,
                       domain_stations: set[str] | None = None) -> float:
        """Fit lambda the way deployment does, but INSIDE the training fold.

        `train.py` regresses observed deviation from 0.5 on predicted
        deviation over pooled leave-city-out predictions, where each pooled
        prediction came from a model weighted toward ITS OWN held-out
        station. Reproduced here by a nested leave-city-out over the TRAINING
        side only, re-centring the kernel on each inner held-out station —
        the previous version reused the OUTER fold's single centre for every
        inner prediction, which fits lambda for a model that is not the one
        being calibrated.

        ``centres_z`` maps an inner station id to its standardised centre;
        stations absent from it are skipped rather than silently pooled.
        """
        cities = sorted({r.city for r in rows})
        if len(cities) < 2:
            return 1.0
        predicted: list[float] = []
        observed: list[float] = []
        for held in cities:
            inner_train = [r for r in rows if r.city != held]
            if len(inner_train) < 30:
                continue
            matrix = self._matrix(inner_train)
            target = np.array([r.share for r in inner_train])
            by_station: dict[str, list[Row]] = {}
            for row in rows:
                if (row.city == held
                        and (domain_stations is None
                             or row.station_id in domain_stations)):
                    by_station.setdefault(row.station_id, []).append(row)
            for station, inner_test in sorted(by_station.items()):
                centre_z = centres_z.get(station)
                if centre_z is None:
                    continue
                model = self._new_model()
                model.fit(matrix, target,
                          sample_weight=self._weights(inner_train, matrix,
                                                      centre_z))
                values = np.clip(
                    self._predict_model(model, self._matrix(inner_test)),
                    0.1, 0.9)
                predicted.extend((values - 0.5).tolist())
                observed.extend([r.share - 0.5 for r in inner_test])
        if not predicted:
            return 1.0
        dp = np.array(predicted)
        dy = np.array(observed)
        if not np.all(np.isfinite(dp)) or not np.all(np.isfinite(dy)):
            raise ValueError(
                "nested shrinkage produced non-finite held-out values; "
                "refusing to discard failed rows from the calibration")
        denominator = float(np.sum(dp * dp, dtype=np.float64))
        if not math.isfinite(denominator) or \
                denominator < self.MIN_SHRINKAGE_DENOMINATOR:
            # Predictions carry no deviation to calibrate, so there is
            # nothing to shrink toward 0.5 that is not already there.
            return 1.0
        value = float(np.sum(dp * dy, dtype=np.float64)) / denominator
        if not math.isfinite(value):
            raise ValueError("nested shrinkage coefficient is not finite")
        return float(np.clip(value, 0.0, 1.0))

    def station_centre(self, rows) -> np.ndarray:
        """Standardised kernel centre for one target station's rows."""
        static = self._matrix(rows)[:, :len(FEATURE_NAMES)]
        return ((static.mean(axis=0) - self._mu) / self._sd)

    def _prepare_training(self, rows):
        trainable = [r for r in rows if in_deployed_training_window(r)]
        if len(trainable) < 30:
            self._model = None
            return trainable, None, None
        matrix = self._matrix(trainable)
        target = np.array([r.share for r in trainable], dtype=float)
        static = matrix[:, :len(FEATURE_NAMES)]
        self._mu = static.mean(axis=0)
        self._sd = static.std(axis=0)
        self._sd[self._sd < 1e-9] = 1.0
        return trainable, matrix, target

    def _domain_stations(self, rows,
                         deployment_target_features: np.ndarray | None
                         ) -> set[str] | None:
        """Deployment's Gothenburg-domain subset, fitted inside the fold."""
        if deployment_target_features is None or not len(
                deployment_target_features):
            return None
        target = np.asarray(deployment_target_features, dtype=float)
        if target.ndim != 2 or target.shape[1] != len(FEATURE_NAMES) \
                or not np.all(np.isfinite(target)):
            raise ValueError("deployment target features are malformed")
        static = self._matrix(rows)[:, :len(FEATURE_NAMES)]
        standardised = (static - self._mu) / self._sd
        target_centre = (target.mean(axis=0) - self._mu) / self._sd
        distances = np.sqrt(((standardised - target_centre) ** 2).sum(axis=1))
        cutoff = float(np.median(distances))
        return {row.station_id for row, distance in zip(rows, distances)
                if distance <= cutoff}

    def prepare_shrinkage(
            self, rows,
            deployment_target_features: np.ndarray | None = None) -> float:
        """Fit the fold-level lambda once; it is shared by target stations."""
        trainable, _matrix, _target = self._prepare_training(rows)
        if len(trainable) < 30:
            return 1.0
        by_station: dict[str, list[Row]] = {}
        for row in trainable:
            by_station.setdefault(row.station_id, []).append(row)
        centres_z = {station: self.station_centre(group)
                     for station, group in by_station.items()}
        domain = self._domain_stations(trainable, deployment_target_features)
        return self._fit_shrinkage(trainable, centres_z, domain)

    def fit(self, rows, target_features=None, *, shrinkage_override=None,
            deployment_target_features=None):
        # Deployment trains on weekday 06-20 only. Rows outside that window
        # stay in the EVALUATION set so the report can show what happens
        # where the model is used but was never trained.
        trainable, matrix, target = self._prepare_training(rows)
        if len(trainable) < 30:
            return self
        assert matrix is not None and target is not None
        static = matrix[:, :len(FEATURE_NAMES)]

        # Centre the kernel on the TARGET, as deployment does. Falling back
        # to the training centroid only when no target is supplied keeps the
        # candidate usable standalone; ``held_out_predictions`` always
        # supplies ONE STATION's features, because deployment fits one model
        # per target sensor rather than one model per city.
        if target_features is not None and len(target_features):
            centre_z = ((np.asarray(target_features, dtype=float).mean(axis=0)
                         - self._mu) / self._sd)
        else:
            centre_z = ((static - self._mu) / self._sd).mean(axis=0)

        self._shrinkage = (float(shrinkage_override)
                           if shrinkage_override is not None else
                           self.prepare_shrinkage(
                               rows, deployment_target_features))
        # Production publishes q50 from the alpha=.5 quantile model. The
        # nested lambda is intentionally calibrated with the mean regressor,
        # matching train.py, but the final point estimate must be the model
        # predict.py actually reads.
        self._model = self._new_model(alpha=0.5)
        self._model.fit(matrix, target,
                        sample_weight=self._weights(trainable, matrix, centre_z))
        return self

    def fit_interval(self, rows, target_features=None, *,
                     shrinkage_override=None,
                     deployment_target_features=None):
        """Fit the THREE quantile models `dirsplit/predict.py` publishes.

        Same rows, same standardisation, same kernel centre, same weights and
        same shrinkage as ``fit`` — only the LightGBM objective differs per
        alpha. Anything else would measure an interval the project does not
        ship.
        """
        trainable, matrix, target = self._prepare_training(rows)
        self._interval_models = None
        if len(trainable) < 30:
            return self
        assert matrix is not None and target is not None
        static = matrix[:, :len(FEATURE_NAMES)]
        if target_features is not None and len(target_features):
            centre_z = ((np.asarray(target_features, dtype=float).mean(axis=0)
                         - self._mu) / self._sd)
        else:
            centre_z = ((static - self._mu) / self._sd).mean(axis=0)
        self._shrinkage = (float(shrinkage_override)
                           if shrinkage_override is not None else
                           self.prepare_shrinkage(
                               rows, deployment_target_features))
        weights = self._weights(trainable, matrix, centre_z)
        models = {}
        for alpha in INTERVAL_QUANTILES:
            model = self._new_model(alpha=alpha)
            model.fit(matrix, target, sample_weight=weights)
            models[alpha] = model
        self._interval_models = models
        self._model = models[0.5]
        return self

    def predict_interval(self, rows):
        """(lower, mid, upper) exactly as `dirsplit/predict.py` publishes them.

        Reproduced step for step from the deployed writer: clamp each raw
        quantile, enforce q10 <= q50 <= q90, SHIFT all three by the shrinkage
        correction (the interval keeps its raw width and is only re-centred),
        clamp again, then open to the clamp limits outside weekday 06-20.

        That last step is why coverage must be reported separately inside and
        outside support: an unsupported hour is not a model output at all, it
        is the constant [0.1, 0.9] statement of ignorance, and pooling the two
        would let 96 trivially-covered night slots hide the trained hours.
        """
        n = len(rows)
        supported = np.array(
            [in_deployed_training_window(row) for row in rows], dtype=bool)
        if not getattr(self, "_interval_models", None):
            return (np.full(n, CLAMP_LOW), np.full(n, 0.5),
                    np.full(n, CLAMP_HIGH), supported)
        matrix = self._matrix(rows)
        raw = {alpha: np.clip(self._predict_model(model, matrix),
                              CLAMP_LOW, CLAMP_HIGH)
               for alpha, model in self._interval_models.items()}
        stacked = [raw[alpha] for alpha in INTERVAL_QUANTILES]
        lower = np.minimum.reduce(stacked)
        upper = np.maximum.reduce(stacked)
        mid = np.clip(raw[0.5], lower, upper)
        shift = (0.5 + self._shrinkage * (mid - 0.5)) - mid
        lower, mid, upper = (np.clip(a + shift, CLAMP_LOW, CLAMP_HIGH)
                             for a in (lower, mid, upper))
        lower[~supported] = CLAMP_LOW
        mid[~supported] = 0.5
        upper[~supported] = CLAMP_HIGH
        return lower, mid, upper, supported

    def predict(self, rows):
        if self._model is None:
            return np.full(len(rows), 0.5, dtype=float)
        raw = np.clip(
            self._predict_model(self._model, self._matrix(rows)), 0.1, 0.9)
        # Deployment ships 0.5 + lambda * (pred - 0.5); evaluating the raw
        # prediction would score a model nobody runs.
        result = np.clip(0.5 + self._shrinkage * (raw - 0.5), 0.1, 0.9)
        # The trainer fits weekday 06-20 only. A model output elsewhere is an
        # extrapolation, not a trained split. Deployment falls back to the
        # maximum-entropy point estimate outside that support, so the
        # tournament must score the same hybrid policy.
        supported = np.array(
            [in_deployed_training_window(row) for row in rows], dtype=bool)
        result[~supported] = 0.5
        return result


def default_candidates() -> list[Candidate]:
    return [Constant5050(), ShrunkDFactor(), BetaBinomialDFactor(),
            SimilarityWeightedLGBM(use_profile_features=False),
            SimilarityWeightedLGBM()]


# ──────────────────────────────────────────────────────────────────────────
# folds
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Fold:
    name: str
    kind: str
    train: tuple[Row, ...]
    test: tuple[Row, ...]

    def __post_init__(self):
        overlap = {r.block for r in self.train} & {r.block for r in self.test}
        if overlap:
            raise ValueError(
                f"fold {self.name} leaks {len(overlap)} block(s) across the "
                "split")


def leave_city_out(rows: Sequence[Row]) -> list[Fold]:
    folds = []
    for city in sorted({r.city for r in rows}):
        train = tuple(r for r in rows if r.city != city)
        test = tuple(r for r in rows if r.city == city)
        if train and test:
            folds.append(Fold(f"city={city}", "leave_city_out", train, test))
    return folds


def leave_station_out(rows: Sequence[Row], max_folds: int = 40) -> list[Fold]:
    folds = []
    for station in sorted({r.station_id for r in rows})[:max_folds]:
        train = tuple(r for r in rows if r.station_id != station)
        test = tuple(r for r in rows if r.station_id == station)
        if train and test:
            folds.append(
                Fold(f"station={station}", "leave_station_out", train, test))
    return folds


def blocked_date(rows: Sequence[Row], n_blocks: int = 4) -> list[Fold]:
    """Only possible when the source carries dates."""
    dates = sorted({r.local_date for r in rows if r.local_date})
    if len(dates) < 2:
        return []
    size = math.ceil(len(dates) / min(n_blocks, len(dates)))
    folds = []
    for index in range(0, len(dates), size):
        held = set(dates[index:index + size])
        train = tuple(r for r in rows if r.local_date not in held)
        test = tuple(r for r in rows if r.local_date in held)
        if train and test:
            folds.append(Fold(f"dates[{index}]", "blocked_date", train, test))
    return folds


# ──────────────────────────────────────────────────────────────────────────
# metrics
# ──────────────────────────────────────────────────────────────────────────
def paired_difference_ci(candidate_err: np.ndarray, baseline_err: np.ndarray,
                         blocks: Sequence[str], confidence: float = 0.95,
                         seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """Block bootstrap CI on mean(candidate) - mean(baseline).

    Whole blocks are resampled, because rows within a block are correlated
    and a row bootstrap would report an interval far tighter than the data
    supports.
    """
    diff = candidate_err - baseline_err
    by_block: dict[str, list[float]] = {}
    for block, value in zip(blocks, diff):
        by_block.setdefault(block, []).append(float(value))
    keys = list(by_block)
    if len(keys) < 2:
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


def groups_of(row: Row) -> tuple[str, ...]:
    groups = ["all", "weekend" if row.is_weekend else "weekday"]
    if not row.is_weekend and (6 <= row.hour <= 9 or 15 <= row.hour <= 18):
        groups.append("weekday_peak")
    if row.hour < 6 or row.hour > 20:
        groups.append("off_hours")
    return tuple(groups)


def content_digest(payload: Any) -> str:
    """Stable SHA-256 over a JSON-serialisable payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def provenance_digests(table_path: Path) -> dict[str, Any]:
    """Bind a report to the data and the source code that produced it.

    Without this a Gate M report is a floating claim: it names its rule and
    its row counts, but nothing ties it to the exact table it read or the
    exact scoring code that read it, so a later table refresh or a change to
    the candidates would leave a stale report indistinguishable from a
    current one.
    """
    def digest_file(path: Path) -> str:
        try:
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError as error:
            raise FileNotFoundError(
                f"cannot provenance-bind Gate M: missing {path}") from error

    module = Path(__file__).resolve()
    meta_path = table_path.with_name(META_PATH.name)
    return {
        "training_table": str(table_path),
        "training_table_digest": digest_file(table_path),
        "evaluate_module_digest": digest_file(module),
        "trainer_module_digest": digest_file(module.parent / "train.py"),
        "dataset_module_digest": digest_file(module.parent / "dataset.py"),
        "features_module_digest": digest_file(module.parent / "features.py"),
        "dataset_meta": str(meta_path),
        "dataset_meta_digest": digest_file(meta_path),
        "note": ("digests bind this report to the exact table and scoring "
                 "code it came from; a report whose digests no longer match "
                 "the tree is stale, not current"),
    }


def validate_dataset_provenance(table_path: Path) -> dict[str, Any]:
    """Verify the v2 table all the way back to its raw-file manifest."""
    meta_path = table_path.with_name(META_PATH.name)
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Gate M requires a readable dataset-v2 manifest: {meta_path}") \
            from error
    if meta.get("protocol") != DATASET_PROTOCOL:
        raise ValueError(
            f"Gate M requires {DATASET_PROTOCOL}, got {meta.get('protocol')!r}")
    live_table = hashlib.sha256(table_path.read_bytes()).hexdigest()
    if meta.get("primary_table_sha256") != live_table:
        raise ValueError(
            "training table does not match training_table_meta.json; rebuild "
            "the dataset instead of evaluating mixed provenance")
    sources = meta.get("source_volume_files")
    if not isinstance(sources, list) or not sources:
        raise ValueError("dataset-v2 manifest binds no raw volume files")
    source_key = hashlib.sha256(json.dumps(
        sources, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if source_key != meta.get("source_content_key"):
        raise ValueError("dataset-v2 raw source content key is invalid")
    drifted = []
    for record in sources:
        try:
            live = hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest()
        except (KeyError, OSError, TypeError):
            drifted.append(str(record.get("path", "<missing path>")))
            continue
        if live != record.get("sha256"):
            drifted.append(str(record["path"]))
    if drifted:
        raise ValueError(
            "dataset-v2 raw volume provenance drifted for "
            f"{len(drifted)} file(s): {drifted[:3]}")
    return {
        "protocol": meta["protocol"],
        "manifest": str(meta_path),
        "manifest_sha256": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
        "source_content_key": source_key,
        "source_volume_files": len(sources),
        "rows": int(meta.get("rows", 0)),
        "usable_rows": int(meta.get("usable_rows", 0)),
        "day_blocks": int(meta.get("day_blocks", 0)),
    }


def deployment_target_matrix() -> np.ndarray:
    """Static features of the Gothenburg sensors deployment predicts."""
    from .train import target_static_features

    targets = target_static_features()
    if not targets:
        raise ValueError("deployment exposes no Gothenburg target features")
    matrix = np.asarray([targets[key] for key in sorted(targets)], dtype=float)
    if (matrix.ndim != 2 or matrix.shape[1] != len(FEATURE_NAMES)
            or not np.all(np.isfinite(matrix))):
        raise ValueError("deployment target feature matrix is malformed")
    return matrix


@dataclass(frozen=True)
class HeldOut:
    """One candidate's pooled held-out predictions over a set of folds.

    Kept as an object rather than collapsed straight into a report because
    the frozen rule compares a candidate against the CURRENT INCUMBENT, not
    always against 50/50. That comparison is impossible once only summary
    statistics survive, which is how the previous version ended up letting a
    complex model displace a simpler promoted one without ever being shown
    better than it.
    """

    predicted: np.ndarray
    actual: np.ndarray
    blocks: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]

    @property
    def error(self) -> np.ndarray:
        return np.abs(self.actual - self.predicted)

    def mask(self, group: str) -> np.ndarray:
        return np.array([group in g for g in self.groups], dtype=bool)


def held_out_predictions(candidate: Candidate,
                         folds: Sequence[Fold],
                         deployment_target_features: np.ndarray | None = None,
                         ) -> HeldOut:
    """Fit per fold on the training side only, then pool held-out rows.

    ONE MODEL PER HELD-OUT STATION, not one per fold. Deployment
    (`dirsplit/train.py`) trains a separate locally weighted model for each
    target sensor, centred on that sensor's own feature vector, and its own
    leave-city-out validation does the same for each held-out station. A
    leave-city-out fold that fit a single model centred on the whole city's
    mean would be evaluating a model the project does not ship — the
    difference matters most exactly where the kernel is doing work.

    The held-out station's STATIC FEATURES are handed to ``fit`` as
    ``target_features``; that is what deployment knows about a Gothenburg
    edge before any local measurement exists. Their LABELS are not passed
    and are used only afterwards, to score.

    Stations are visited in sorted order within each fold, and folds in the
    order given, so every candidate produces rows in the same order and
    ``compare`` can pair them row by row.
    """
    import copy

    predicted: list[float] = []
    actual: list[float] = []
    blocks: list[str] = []
    groups: list[tuple[str, ...]] = []

    for fold in folds:
        fold_shrinkage = None
        if isinstance(candidate, SimilarityWeightedLGBM):
            # Lambda depends on the outer training fold, not on which target
            # station is predicted. Computing it inside every station fit
            # multiplied the real tournament into thousands of redundant
            # LightGBM fits.
            fold_shrinkage = copy.deepcopy(candidate).prepare_shrinkage(
                list(fold.train), deployment_target_features)
        by_station: dict[str, list[Row]] = {}
        for row in fold.test:
            by_station.setdefault(row.station_id, []).append(row)
        for _station, rows in sorted(by_station.items()):
            target_features = np.array([r.features for r in rows], dtype=float)
            fitted = copy.deepcopy(candidate)
            if isinstance(fitted, SimilarityWeightedLGBM):
                fitted.fit(
                    list(fold.train), target_features=target_features,
                    shrinkage_override=fold_shrinkage,
                    deployment_target_features=deployment_target_features)
            else:
                fitted.fit(list(fold.train), target_features=target_features)
            predicted.extend(np.asarray(
                fitted.predict(list(rows)), dtype=float).tolist())
            actual.extend(r.share for r in rows)
            blocks.extend(r.block for r in rows)
            groups.extend(groups_of(r) for r in rows)

    return HeldOut(np.array(predicted, dtype=float),
                   np.array(actual, dtype=float),
                   tuple(blocks), tuple(groups))


def compare(candidate: HeldOut, reference: HeldOut) -> dict[str, Any]:
    """Group-wise paired comparison of two candidates' held-out errors.

    Both sides must come from the SAME folds, so the rows line up and the
    difference is paired row by row — the whole point of running every
    candidate over one fold set.
    """
    if len(candidate.error) != len(reference.error):
        raise ValueError(
            "paired comparison needs both candidates scored on the same "
            f"folds ({len(candidate.error)} vs {len(reference.error)} rows)")
    err, base = candidate.error, reference.error
    per_group: dict[str, Any] = {}
    for name in ("all", "weekday", "weekend", "weekday_peak", "off_hours"):
        mask = candidate.mask(name)
        if not mask.any():
            continue
        low, high = paired_difference_ci(
            err[mask], base[mask],
            [b for b, m in zip(candidate.blocks, mask) if m])
        base_mae = float(np.mean(base[mask]))
        per_group[name] = {
            "n": int(mask.sum()),
            "n_blocks": len({b for b, m in zip(candidate.blocks, mask) if m}),
            "mae": round(float(np.mean(err[mask])), 6),
            "mae_5050": round(base_mae, 6),
            "improvement_pct": round(
                100.0 * (base_mae - float(np.mean(err[mask]))) / base_mae, 2)
            if base_mae > 0 else 0.0,
            "paired_diff_ci95": [round(low, 6), round(high, 6)],
            "beats_baseline": bool(high < 0),
            "loses_to_baseline": bool(low > 0),
        }
    if not len(err):
        return {"n": 0, "groups": {}}
    return {
        "n": int(len(err)),
        "n_blocks": len(set(candidate.blocks)),
        "mae": round(float(np.mean(err)), 6),
        "mae_5050": round(float(np.mean(base)), 6),
        "groups": per_group,
    }


def score(candidate: Candidate, folds: Sequence[Fold],
          reference: Candidate | None = None) -> dict[str, Any]:
    """Fit per fold on the training side only, then pool held-out errors.

    ``reference`` defaults to ``constant_5050``, which is what the published
    report compares against. The decision rule uses ``compare`` directly so
    it can hold the CURRENT incumbent fixed instead.
    """
    held = held_out_predictions(candidate, folds)
    if not len(held.error):
        return {"n": 0, "groups": {}}
    baseline = held_out_predictions(reference or Constant5050(), folds)
    return compare(held, baseline)


# ──────────────────────────────────────────────────────────────────────────
# Interval calibration (2026-08-16)
#
# Gate M asks whether the POINT estimate beats 50/50. Nothing has ever asked
# what the published q10/q90 interval actually covers, which is the question
# that decides whether the split is usable as an uncertainty statement rather
# than a decoration. This section measures that on the same leakage-free
# folds, then applies split-conformal (CQR) calibration fit strictly inside
# each training fold.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class HeldOutInterval:
    """Pooled held-out intervals for one policy over a set of folds."""

    lower: np.ndarray
    mid: np.ndarray
    upper: np.ndarray
    actual: np.ndarray
    supported: np.ndarray
    stations: tuple[str, ...]
    corrections: tuple[float, ...] = ()

    @property
    def covered(self) -> np.ndarray:
        return (self.actual >= self.lower) & (self.actual <= self.upper)

    @property
    def width(self) -> np.ndarray:
        return self.upper - self.lower


def station_weighted_quantile(values: np.ndarray,
                              stations: Sequence[str],
                              level: float) -> float:
    """Empirical quantile with every station contributing equal total weight.

    Hourly rows inside one station are correlated, so a plain row quantile is
    dominated by whichever station happens to contribute most rows — the same
    reason this module bootstraps station blocks rather than rows. Equal
    station weight is the convention `train.py` already uses for fitting.
    """
    values = np.asarray(values, dtype=float)
    if not len(values):
        return float("nan")
    counts: dict[str, int] = {}
    for station in stations:
        counts[station] = counts.get(station, 0) + 1
    weights = np.array([1.0 / counts[s] for s in stations], dtype=float)
    order = np.argsort(values, kind="mergesort")
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights) / weights.sum()
    index = int(np.searchsorted(cumulative, min(level, 1.0), side="left"))
    return float(values[min(index, len(values) - 1)])


def conformal_correction(model: "SimilarityWeightedLGBM",
                         calibration_rows: Sequence[Row],
                         nominal: float = NOMINAL_COVERAGE) -> float | None:
    """Split-conformal (CQR) widening for the calibration stations.

    The score is the standard CQR residual ``max(lower - y, y - upper)``:
    negative when the row already falls inside the interval, positive by
    exactly the amount the interval was too narrow. Its ``nominal`` quantile
    is how much the interval must open — or, when negative, may close.

    Only SUPPORTED rows contribute. Outside weekday 06-20 the published
    interval is the constant [0.1, 0.9] ignorance statement, not a model
    output, so calibrating on it would measure the clamp instead of the model.
    """
    rows = [r for r in calibration_rows if in_deployed_training_window(r)]
    stations = sorted({r.station_id for r in rows})
    if len(stations) < MIN_CALIBRATION_STATIONS:
        return None
    lower, _mid, upper, supported = model.predict_interval(rows)
    actual = np.array([r.share for r in rows], dtype=float)
    scores = np.maximum(lower - actual, actual - upper)[supported]
    kept = [r.station_id for r, keep in zip(rows, supported) if keep]
    if not len(scores):
        return None
    # Finite-sample adjustment at the BLOCK level: n is the number of
    # independent calibration stations, not the number of correlated rows.
    level = min(1.0, nominal * (1.0 + 1.0 / len(stations)))
    return station_weighted_quantile(scores, kept, level)


def held_out_intervals(candidate: "SimilarityWeightedLGBM",
                       folds: Sequence[Fold],
                       deployment_target_features: np.ndarray | None = None,
                       *, conformal: bool = False) -> HeldOutInterval:
    """Pool held-out intervals fold by fold, optionally conformalized.

    Deployment parity is the same as ``held_out_predictions``: one model per
    held-out station, kernel centred on that station's static features, every
    fitted quantity estimated on the training side only.

    With ``conformal=True`` the fold's training stations are split
    deterministically into a fit set and a calibration set, the quantile
    models are fit on the FIT set alone, and the correction comes from the
    calibration set. The calibration stations therefore never train the model
    whose interval they correct, and the held-out station touches neither.
    """
    import copy

    lower_all: list[float] = []
    mid_all: list[float] = []
    upper_all: list[float] = []
    actual_all: list[float] = []
    supported_all: list[bool] = []
    stations_all: list[str] = []
    corrections: list[float] = []

    for fold in folds:
        train_rows = list(fold.train)
        calibration_rows: list[Row] = []
        if conformal:
            train_stations = sorted({r.station_id for r in train_rows})
            calibration_ids = {
                station for index, station in enumerate(train_stations)
                if index % CONFORMAL_STATION_STRIDE == 0}
            if len(calibration_ids) >= MIN_CALIBRATION_STATIONS:
                calibration_rows = [r for r in train_rows
                                    if r.station_id in calibration_ids]
                train_rows = [r for r in train_rows
                              if r.station_id not in calibration_ids]
        fold_shrinkage = copy.deepcopy(candidate).prepare_shrinkage(
            train_rows, deployment_target_features)

        by_station: dict[str, list[Row]] = {}
        for row in fold.test:
            by_station.setdefault(row.station_id, []).append(row)
        for _station, rows in sorted(by_station.items()):
            target_features = np.array([r.features for r in rows], dtype=float)
            fitted = copy.deepcopy(candidate).fit_interval(
                train_rows, target_features=target_features,
                shrinkage_override=fold_shrinkage,
                deployment_target_features=deployment_target_features)
            lower, mid, upper, supported = fitted.predict_interval(list(rows))
            if conformal and calibration_rows:
                correction = conformal_correction(fitted, calibration_rows)
                if correction is not None:
                    corrections.append(correction)
                    inside = supported
                    lower[inside] = np.clip(lower[inside] - correction,
                                            CLAMP_LOW, CLAMP_HIGH)
                    upper[inside] = np.clip(upper[inside] + correction,
                                            CLAMP_LOW, CLAMP_HIGH)
            lower_all.extend(lower.tolist())
            mid_all.extend(mid.tolist())
            upper_all.extend(upper.tolist())
            supported_all.extend(supported.tolist())
            actual_all.extend(r.share for r in rows)
            stations_all.extend(r.station_id for r in rows)

    return HeldOutInterval(
        np.array(lower_all, dtype=float), np.array(mid_all, dtype=float),
        np.array(upper_all, dtype=float), np.array(actual_all, dtype=float),
        np.array(supported_all, dtype=bool), tuple(stations_all),
        tuple(corrections))


def interval_metrics(held: HeldOutInterval) -> dict[str, Any]:
    """Coverage and width, reported separately inside and outside support.

    ``station_coverage_min`` matters as much as the pooled number: a policy
    can average 80% by covering four stations completely and one not at all,
    which is not what an 80% interval promises for the next street.
    """
    def _slice(mask: np.ndarray) -> dict[str, Any]:
        if not mask.any():
            return {"n": 0}
        covered = held.covered[mask]
        width = held.width[mask]
        stations = np.array(held.stations)[mask]
        per_station = [float(covered[stations == s].mean())
                       for s in sorted(set(stations.tolist()))]
        return {
            "n": int(mask.sum()),
            "stations": len(per_station),
            "coverage": round(float(covered.mean()), 4),
            "mean_width": round(float(width.mean()), 4),
            "median_width": round(float(np.median(width)), 4),
            "station_coverage_min": round(float(np.min(per_station)), 4),
            "station_coverage_median": round(float(np.median(per_station)), 4),
        }

    return {
        "nominal_coverage": NOMINAL_COVERAGE,
        "supported": _slice(held.supported),
        "unsupported": _slice(~held.supported),
        "all_rows": _slice(np.ones(len(held.actual), dtype=bool)),
        "conformal_corrections": {
            "n": len(held.corrections),
            "median": (round(float(np.median(held.corrections)), 4)
                       if held.corrections else None),
            "min": (round(float(np.min(held.corrections)), 4)
                    if held.corrections else None),
            "max": (round(float(np.max(held.corrections)), 4)
                    if held.corrections else None),
        },
    }


def run_interval_calibration(
        path: Path = TABLE_PATH, *, fold_kind: str = "leave_station_out",
        max_folds: int = 0) -> dict[str, Any]:
    """Measure the published interval, then the conformalized one."""
    dataset_provenance = validate_dataset_provenance(path)
    rows, meta = load_rows(path)
    deployment_targets = deployment_target_matrix()
    builders: dict[str, Callable[[Sequence[Row]], list[Fold]]] = {
        "leave_city_out": leave_city_out,
        "leave_station_out": lambda r: leave_station_out(r, 40),
        "blocked_date": blocked_date,
    }
    if fold_kind not in builders:
        raise ValueError(f"unknown fold kind {fold_kind!r}")
    folds = builders[fold_kind](rows)
    if max_folds:
        folds = folds[:max_folds]
    if not folds:
        raise ValueError(f"fold kind {fold_kind!r} produced no folds")

    candidate = SimilarityWeightedLGBM(use_profile_features=False)
    published = held_out_intervals(candidate, folds, deployment_targets)
    calibrated = held_out_intervals(candidate, folds, deployment_targets,
                                    conformal=True)
    report = {
        "protocol": INTERVAL_PROTOCOL,
        "release_evidence": False,
        "recorded_fold_kind": fold_kind,
        "folds_used": len(folds),
        "folds_available": len(builders[fold_kind](rows)),
        "candidate": candidate.name,
        "input": meta,
        "dataset": dataset_provenance,
        "provenance": provenance_digests(path),
        "published_interval": interval_metrics(published),
        "conformalized_interval": interval_metrics(calibrated),
        "interpretation": (
            "Coverage below the nominal level means the published q10/q90 is "
            "narrower than the data supports and must not be presented as an "
            "80% interval. Unsupported rows are the constant [0.1, 0.9] "
            "ignorance statement, not a model output, and are reported "
            "separately for that reason."),
    }
    report["content_key"] = content_digest(report)
    return report


# ──────────────────────────────────────────────────────────────────────────
# Gate M
# ──────────────────────────────────────────────────────────────────────────
def _inconclusive_gate_m(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "gate_m": "INCONCLUSIVE",
        "winner": None,
        "reason": reason,
        "selection_rule": SELECTION_RULE,
        "note": ("INCONCLUSIVE is not BASELINE. It says the evidence needed "
                 "to decide was not available, so the current release path "
                 "stays as legacy and only the evidence defect is repaired."),
        **extra,
    }


def decide_gate_m(reports: Mapping[str, Mapping[str, Any]],
                  candidates: Sequence[Candidate],
                  meta: Mapping[str, Any],
                  comparisons: Mapping[str, Mapping[str, Mapping[str, Any]]]
                  | None = None,
                  fold_kinds: Sequence[str] | None = None) -> dict[str, Any]:
    """Apply SELECTION_RULE_TEXT. BASELINE / MODEL / INCONCLUSIVE.

    ``comparisons[candidate][incumbent][fold_kind]`` holds the paired
    comparison of that candidate against that incumbent, in the same shape
    ``compare`` returns. Rule 2 needs it: once a simpler model has been
    promoted, a more complex one must beat THAT, not 50/50. Without it the
    function falls back to the reports' vs-50/50 entries, which is only
    valid while the incumbent is still 50/50 — so the fallback is recorded
    in the result rather than applied silently.
    """
    ordered = sorted(candidates, key=lambda c: (c.complexity, c.name))
    incumbent = ordered[0].name
    detail: dict[str, Any] = {}

    if meta.get("blocks", 0) < MIN_INDEPENDENT_BLOCKS:
        return _inconclusive_gate_m(
            f"only {meta.get('blocks')} independent blocks; "
            f"{MIN_INDEPENDENT_BLOCKS} are required before a difference can "
            "be called robust")

    # Rule 6: a fold kind that could not be built is missing evidence, not
    # evidence of no effect. The aggregated legacy table carries no dates, so
    # blocked_date cannot be built from it — which is exactly the case this
    # guard exists for.
    available = set(fold_kinds if fold_kinds is not None
                    else {kind for report in reports.values() for kind in report})
    missing = [kind for kind in REQUIRED_FOLD_KINDS if kind not in available]
    if missing:
        return _inconclusive_gate_m(
            "required fold kind(s) could not be built: "
            + ", ".join(missing)
            + ". A model validated only on the fold kinds that happened to be "
              "constructible has not been shown to generalise along the one "
              "that was not.",
            missing_fold_kinds=missing,
            available_fold_kinds=sorted(available))

    fallback_used = comparisons is None
    for candidate in ordered[1:]:
        report = reports.get(candidate.name) or {}
        if not report:
            detail[candidate.name] = {"promoted": False, "why": "no results"}
            continue

        against = (((comparisons or {}).get(candidate.name) or {})
                   .get(incumbent))
        if against is None:
            if candidate.name == incumbent or incumbent != ordered[0].name:
                # No paired comparison against the CURRENT incumbent exists,
                # and the incumbent is no longer 50/50, so the report's
                # vs-50/50 entries cannot answer rule 2.
                detail[candidate.name] = {
                    "promoted": False,
                    "why": f"no paired comparison against incumbent "
                           f"{incumbent}"}
                continue
            against = report          # incumbent is 50/50: the report is it

        notes: dict[str, str] = {}
        # Rule 4: BOTH conditions must hold under EVERY fold kind. Evaluated
        # per kind and then AND-ed, instead of pooling wins and losses across
        # kinds — a win under one fold kind and a tie under another is not a
        # win under every fold kind.
        promoted_per_kind: list[bool] = []
        for kind in sorted(REQUIRED_FOLD_KINDS):
            stats = against.get(kind)
            if not stats:
                notes[f"{kind}:*"] = "unmeasured"
                promoted_per_kind.append(False)
                continue
            wins = loses = False
            for group in PRIMARY_GROUPS:
                entry = (stats.get("groups") or {}).get(group)
                if not entry:
                    notes[f"{kind}:{group}"] = "unmeasured"
                    continue
                if entry.get("loses_to_baseline"):
                    loses = True
                    notes[f"{kind}:{group}"] = "loses"
                elif entry.get("beats_baseline"):
                    wins = True
                    notes[f"{kind}:{group}"] = "wins"
                else:
                    notes[f"{kind}:{group}"] = "tie"
            promoted_per_kind.append(wins and not loses)

        promoted = bool(promoted_per_kind) and all(promoted_per_kind)
        detail[candidate.name] = {
            "promoted": promoted,
            "compared_against": incumbent,
            "notes": notes,
        }
        if promoted:
            incumbent = candidate.name

    gate = "BASELINE" if incumbent == "constant_5050" else "MODEL"
    result = {
        "gate_m": gate,
        "winner": incumbent,
        "reason": ("no candidate won a primary group under every fold kind "
                   "without losing one" if gate == "BASELINE"
                   else f"{incumbent} wins a primary group under every fold "
                        "kind and loses none, against the incumbent it "
                        "replaced"),
        "selection_rule": SELECTION_RULE,
        "detail": detail,
    }
    if fallback_used:
        result["comparison_basis"] = (
            "constant_5050 only — no paired candidate-vs-incumbent "
            "comparisons were supplied")
    return result


def run(path: Path = TABLE_PATH) -> dict[str, Any]:
    dataset_provenance = validate_dataset_provenance(path)
    rows, meta = load_rows(path)
    candidates = default_candidates()
    deployment_targets = deployment_target_matrix()

    builders: dict[str, Callable[[Sequence[Row]], list[Fold]]] = {
        "leave_city_out": leave_city_out,
        "leave_station_out": lambda r: leave_station_out(r, 40),
    }
    date_folds = blocked_date(rows)
    if date_folds:
        builders["blocked_date"] = blocked_date

    # Every candidate is scored on ONE fold set per kind, and its pooled
    # held-out predictions are kept, so any pair can be compared row by row
    # afterwards. Re-fitting per comparison would be both slower and unable
    # to guarantee the pairing.
    folds_by_kind = {kind: build(rows) for kind, build in builders.items()}
    folds_by_kind = {kind: folds for kind, folds in folds_by_kind.items()
                     if folds}
    held: dict[str, dict[str, HeldOut]] = {}
    for candidate in candidates:
        per_kind = {kind: held_out_predictions(
                        candidate, folds, deployment_targets)
                    for kind, folds in folds_by_kind.items()}
        if per_kind:
            held[candidate.name] = per_kind

    baseline = Constant5050().name
    reports: dict[str, dict[str, Any]] = {
        name: {kind: compare(value, held[baseline][kind])
               for kind, value in per_kind.items()}
        for name, per_kind in held.items()
    }
    comparisons: dict[str, dict[str, dict[str, Any]]] = {
        name: {
            other: {kind: compare(value, held[other][kind])
                    for kind, value in per_kind.items()}
            for other in held if other != name
        }
        for name, per_kind in held.items()
    }

    decision = decide_gate_m(reports, candidates, meta,
                             comparisons=comparisons,
                             fold_kinds=sorted(folds_by_kind))
    report = {
        "protocol": GATE_M_PROTOCOL,
        "selection_rule": SELECTION_RULE,
        "selection_rule_text": SELECTION_RULE_TEXT,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "primary_groups": list(PRIMARY_GROUPS),
        "required_fold_kinds": list(REQUIRED_FOLD_KINDS),
        "release_evidence": False,
        "input": meta,
        "dataset": dataset_provenance,
        "provenance": provenance_digests(path),
        "deployment_target_features": {
            "rows": int(deployment_targets.shape[0]),
            "sha256": content_digest(deployment_targets.tolist()),
        },
        "temporal_support": temporal_support(rows),
        "fold_kinds": sorted(folds_by_kind),
        "blocked_date_available": bool(date_folds),
        "reports": reports,
        "pairwise_comparisons": comparisons,
        **decision,
    }
    # A content key over everything above, so a report cannot be edited
    # after the fact without the edit being visible, and so two reports can
    # be compared for identity in one field instead of by eye.
    report["content_key"] = content_digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=TABLE_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--intervals", action="store_true",
        help="measure published vs conformalized q10/q90 coverage instead "
             "of running the Gate M tournament")
    parser.add_argument("--fold-kind", default="leave_station_out")
    parser.add_argument(
        "--max-folds", type=int, default=0,
        help="interval mode only: stop after N folds for a fast pass; 0 "
             "runs every fold and is the only form that may be registered")
    args = parser.parse_args(argv)

    if args.intervals:
        report = run_interval_calibration(
            args.table, fold_kind=args.fold_kind, max_folds=args.max_folds)
        out = args.out or INTERVAL_REPORT_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        partial = report["folds_used"] < report["folds_available"]
        print(f"fold kind {report['recorded_fold_kind']}: "
              f"{report['folds_used']}/{report['folds_available']} folds"
              + ("  PARTIAL — diagnostic only" if partial else ""))
        for key in ("published_interval", "conformalized_interval"):
            block = report[key]["supported"]
            print(f"{key:24s} supported n={block.get('n', 0)}  "
                  f"coverage {block.get('coverage')} "
                  f"(nominal {NOMINAL_COVERAGE})  "
                  f"width {block.get('mean_width')}  "
                  f"worst station {block.get('station_coverage_min')}")
        print(f"Wrote {out}")
        return 0

    args.out = args.out or REPORT_PATH
    report = run(args.table)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")

    info = report["input"]
    print(f"rows {info['rows_kept']} from {info['stations']} stations, "
          f"{info['blocks']} blocks ({info['block_definition']})")
    print(f"fold kinds: {report['fold_kinds']}")
    if not report["blocked_date_available"]:
        print("  blocked_date NOT available: the source carries no dates")
    support = report["temporal_support"]
    print(f"weekend rows: {support['weekend_rows']}  "
          f"unsupported weekend hours: "
          f"{len(support['weekend_hours_unsupported'])}/24")
    print()
    for name, per_kind in sorted(report["reports"].items()):
        for kind, stats in sorted(per_kind.items()):
            group = (stats.get("groups") or {}).get("all", {})
            ci = group.get("paired_diff_ci95", [float("nan")] * 2)
            print(f"{name:26s} {kind:19s} MAE {stats['mae']:.4f} vs "
                  f"{stats['mae_5050']:.4f} "
                  f"({group.get('improvement_pct', 0):+.1f}%)  "
                  f"CI95 [{ci[0]:+.4f}, {ci[1]:+.4f}]")
    print(f"\nGate M = {report['gate_m']}  (winner: {report['winner']})")
    print(f"  {report['reason']}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
