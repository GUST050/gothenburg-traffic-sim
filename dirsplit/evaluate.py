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
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from .config import DATA_DIR
from .dataset import OUT_PATH as TABLE_PATH
from .dataset import PROFILE_FEATURES
from .features import FEATURE_NAMES

REPORT_PATH = DATA_DIR / "gate_m_report.json"

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

    oneway_stations = deployed_oneway_stations(raw) if drop_oneway else set()

    has_dates = "local_date" in (raw[0] if raw else {})

    rc_index = FEATURE_NAMES.index("radial_cos")
    rows: list[Row] = []
    for record in raw:
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
            mean_total=_float(record.get("mean_total_veh_h"), 1.0),
            block=block,
            features=features,
            profile=tuple(_float(record.get(c)) for c in PROFILE_FEATURES),
            day_block_id=(f"{record['city']}|{local_date}" if local_date
                          else None),
            local_date=local_date,
        ))

    trainable = [r for r in rows if in_deployed_training_window(r)]
    return rows, {
        "source": str(path),
        "raw_rows": len(raw),
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
    """

    name = "similarity_weighted_lgbm"
    complexity = 3

    def __init__(self, n_estimators: int = 400):
        self.n_estimators = n_estimators
        self._model = None
        self._mu = None
        self._sd = None
        self._shrinkage = 1.0

    def _matrix(self, rows):
        out = np.zeros((len(rows), len(FEATURE_NAMES)
                        + len(PROFILE_FEATURES) + 3), dtype=float)
        for index, row in enumerate(rows):
            base = len(FEATURE_NAMES)
            out[index, :base] = row.features
            out[index, base:base + len(PROFILE_FEATURES)] = row.profile
            tail = base + len(PROFILE_FEATURES)
            out[index, tail] = math.sin(2 * math.pi * row.hour / 24)
            out[index, tail + 1] = math.cos(2 * math.pi * row.hour / 24)
            out[index, tail + 2] = float(row.is_weekend)
        return out

    def _new_model(self):
        import lightgbm as lgb

        return lgb.LGBMRegressor(
            n_estimators=self.n_estimators, learning_rate=0.05, max_depth=5,
            num_leaves=31, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=20, random_state=42, n_jobs=-1, verbose=-1)

    def _weights(self, rows, matrix, centre_z):
        """Deployment's own sample weight: evidence x station x similarity."""
        static = matrix[:, :len(FEATURE_NAMES)]
        standardised = (static - self._mu) / self._sd
        spread = np.sqrt(
            ((standardised - standardised.mean(axis=0)) ** 2).sum(axis=1))
        bandwidth = float(np.median(spread)) or 1.0
        distances = np.sqrt(((standardised - centre_z) ** 2).sum(axis=1))
        similarity = np.exp(-0.5 * (distances / bandwidth) ** 2)

        counts: dict[str, int] = {}
        for row in rows:
            counts[row.station_id] = counts.get(row.station_id, 0) + 1
        evidence = np.array([max(r.n_obs, 1.0) ** 0.5 for r in rows],
                            dtype=float)
        station = np.array([1.0 / counts[r.station_id] for r in rows],
                           dtype=float)
        return evidence * station * similarity

    def _fit_shrinkage(self, rows, centre_z) -> float:
        """Fit lambda the way deployment does, but INSIDE the training fold.

        `train.py` regresses observed deviation from 0.5 on predicted
        deviation over pooled leave-city-out predictions. Reproduced here by
        a nested leave-city-out over the TRAINING side only, so no held-out
        row of the outer fold contributes. Skipping it would evaluate a
        model with roughly four times the deviation the project actually
        ships.
        """
        cities = sorted({r.city for r in rows})
        if len(cities) < 2:
            return 1.0
        predicted: list[float] = []
        observed: list[float] = []
        for held in cities:
            inner_train = [r for r in rows if r.city != held]
            inner_test = [r for r in rows if r.city == held]
            if len(inner_train) < 30 or not inner_test:
                continue
            matrix = self._matrix(inner_train)
            model = self._new_model()
            model.fit(matrix, np.array([r.share for r in inner_train]),
                      sample_weight=self._weights(inner_train, matrix, centre_z))
            values = np.clip(model.predict(self._matrix(inner_test)), 0.1, 0.9)
            predicted.extend((values - 0.5).tolist())
            observed.extend([r.share - 0.5 for r in inner_test])
        if not predicted:
            return 1.0
        dp = np.array(predicted)
        dy = np.array(observed)
        return float(np.clip((dp @ dy) / max(dp @ dp, 1e-12), 0.0, 1.0))

    def fit(self, rows, target_features=None):
        # Deployment trains on weekday 06-20 only. Rows outside that window
        # stay in the EVALUATION set so the report can show what happens
        # where the model is used but was never trained.
        trainable = [r for r in rows if in_deployed_training_window(r)]
        if len(trainable) < 30:
            self._model = None
            return self
        matrix = self._matrix(trainable)
        target = np.array([r.share for r in trainable], dtype=float)

        static = matrix[:, :len(FEATURE_NAMES)]
        self._mu = static.mean(axis=0)
        self._sd = static.std(axis=0)
        self._sd[self._sd < 1e-9] = 1.0

        # Centre the kernel on the TARGET, as deployment does. Falling back
        # to the training centroid only when no target is supplied keeps the
        # candidate usable standalone; ``score`` always supplies one.
        if target_features is not None and len(target_features):
            centre_z = ((np.asarray(target_features, dtype=float).mean(axis=0)
                         - self._mu) / self._sd)
        else:
            centre_z = ((static - self._mu) / self._sd).mean(axis=0)

        self._shrinkage = self._fit_shrinkage(trainable, centre_z)
        self._model = self._new_model()
        self._model.fit(matrix, target,
                        sample_weight=self._weights(trainable, matrix, centre_z))
        return self

    def predict(self, rows):
        if self._model is None:
            return np.full(len(rows), 0.5, dtype=float)
        raw = np.clip(self._model.predict(self._matrix(rows)), 0.1, 0.9)
        # Deployment ships 0.5 + lambda * (pred - 0.5); evaluating the raw
        # prediction would score a model nobody runs.
        return np.clip(0.5 + self._shrinkage * (raw - 0.5), 0.1, 0.9)


def default_candidates() -> list[Candidate]:
    return [Constant5050(), ShrunkDFactor(), BetaBinomialDFactor(),
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
                         folds: Sequence[Fold]) -> HeldOut:
    """Fit per fold on the training side only, then pool held-out rows.

    The held-out rows' STATIC FEATURES are handed to ``fit`` as
    ``target_features`` so a locally weighted candidate is centred where the
    deployment centres it. Their LABELS are not passed and are used only
    afterwards, to score.
    """
    import copy

    predicted: list[float] = []
    actual: list[float] = []
    blocks: list[str] = []
    groups: list[tuple[str, ...]] = []

    for fold in folds:
        target_features = np.array([r.features for r in fold.test],
                                   dtype=float)
        fitted = copy.deepcopy(candidate).fit(
            list(fold.train), target_features=target_features)
        predicted.extend(np.asarray(
            fitted.predict(list(fold.test)), dtype=float).tolist())
        actual.extend(r.share for r in fold.test)
        blocks.extend(r.block for r in fold.test)
        groups.extend(groups_of(r) for r in fold.test)

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
    rows, meta = load_rows(path)
    candidates = default_candidates()

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
        per_kind = {kind: held_out_predictions(candidate, folds)
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
    return {
        "protocol": "dirsplit_gate_m_v2",
        "selection_rule": SELECTION_RULE,
        "selection_rule_text": SELECTION_RULE_TEXT,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "primary_groups": list(PRIMARY_GROUPS),
        "required_fold_kinds": list(REQUIRED_FOLD_KINDS),
        "release_evidence": False,
        "input": meta,
        "temporal_support": temporal_support(rows),
        "fold_kinds": sorted(folds_by_kind),
        "blocked_date_available": bool(date_folds),
        "reports": reports,
        "pairwise_comparisons": comparisons,
        **decision,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=TABLE_PATH)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

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
