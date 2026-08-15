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
SELECTION_RULE = "simplest_defensible_v1"
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260814

#: A candidate must not lose in any of these to be promoted.
PRIMARY_GROUPS = ("all", "weekday_peak")

SELECTION_RULE_TEXT = """\
Frozen before any fold was scored.

1. Candidates are ordered by complexity ascending; constant_5050 is the
   incumbent.
2. A more complex candidate replaces the incumbent only if, on pooled
   held-out predictions and UNDER EVERY FOLD KIND THAT RAN, all three hold:
   a. in every primary group, the 95% block-bootstrap CI on the paired
      difference (candidate MAE - 50/50 MAE) does not lie entirely above
      zero, i.e. it never significantly loses to the null;
   b. in at least one primary group under every fold kind, that CI lies
      entirely below zero, i.e. it significantly beats the null; and
   c. it also significantly beats the CURRENT INCUMBENT in at least one
      primary group under every fold kind, measured by the same paired
      block-bootstrap CI. A more complex model may not displace a simpler
      one merely by both beating 50/50.
3. Ties, unmeasurable groups and non-finite CIs count as "does not win".
4. If nothing is promoted, Gate M = BASELINE and the uncertainty is reported
   rather than modelled away.
5. Gate M = INCONCLUSIVE if leakage is detected, if fewer than
   MIN_INDEPENDENT_BLOCKS independent blocks exist, or if any REQUIRED fold
   kind could not be built. A gate that could not be measured is not a gate
   that returned a negative answer.
"""

#: Fold kinds that MUST be constructible. blocked_date needs a date-preserving
#: dataset; without it the "does this transfer to a future day" question is
#: unanswered, so the gate is INCONCLUSIVE rather than BASELINE.
REQUIRED_FOLD_KINDS = ("leave_city_out", "leave_station_out", "blocked_date")

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


#: Weekday-daytime mean share outside this band marks a station as
#: "one-way-ish". This is the DEPLOYED trainer's definition
#: (dirsplit/train.py::load_table) and is reproduced exactly, because
#: screening on the OSM `oneway` feature instead selects a different and much
#: smaller population -- 39 stations rather than 81 -- and a tournament run on
#: a different population cannot speak about the deployed model.
ONEWAY_SHARE_BAND = (0.15, 0.85)

#: The deployed trainer fits on weekday 06-20 rows only. Predictions outside
#: that band are extrapolation; the tournament still SCORES them so the gap is
#: visible, but the fact is recorded rather than left implicit.
DEPLOYED_TRAINING_BAND = {"weekday_hours": (6, 20), "weekend": False}


def _oneway_stations(raw: Sequence[Mapping[str, Any]]) -> set[str]:
    """Stations screened out by the deployed observed-share rule."""
    by_key: dict[tuple[str, str], list[float]] = {}
    for record in raw:
        if record.get("is_weekend") != "0":
            continue
        try:
            hour = int(_float(record.get("hour")))
        except (TypeError, ValueError):
            continue
        if not 6 <= hour <= 20:
            continue
        by_key.setdefault((record["station_id"], record["heading"]), []).append(
            _float(record.get("share")))
    low, high = ONEWAY_SHARE_BAND
    return {
        station for (station, _heading), shares in by_key.items()
        if shares and not low <= (sum(shares) / len(shares)) <= high
    }


def load_rows(path: Path = TABLE_PATH, *, drop_oneway: bool = True,
              orient_toward_centre: bool = True) -> tuple[list[Row], dict]:
    """Load the tracked table into Row objects.

    Reproduces the two screens the DEPLOYED trainer applies, so the
    tournament measures the population the deployment actually predicts:
    one-way-ish stations are screened out by their OBSERVED weekday-daytime
    shares (not by the OSM `oneway` feature), and only the toward-centre
    direction is kept, using the deployed `radial_cos > 0` rule rather than a
    per-station maximum.
    """
    raw: list[dict] = []
    with open(path) as handle:
        raw = list(csv.DictReader(handle))

    oneway_stations = _oneway_stations(raw) if drop_oneway else set()
    osm_oneway_stations = {
        record["station_id"] for record in raw
        if _float(record.get("oneway")) >= 0.5
    }

    has_dates = "local_date" in (raw[0] if raw else {})

    rows: list[Row] = []
    for record in raw:
        if record["station_id"] in oneway_stations:
            continue
        share = _float(record.get("share"))
        if not 0.0 < share < 1.0:
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
            features=tuple(_float(record.get(c)) for c in FEATURE_NAMES),
            profile=tuple(_float(record.get(c)) for c in PROFILE_FEATURES),
            day_block_id=(f"{record['city']}|{local_date}" if local_date
                          else None),
            local_date=local_date,
        ))

    if orient_toward_centre:
        # The deployed rule: keep the direction whose radial_cos is positive.
        rc_index = FEATURE_NAMES.index("radial_cos")
        rows = [r for r in rows if r.features[rc_index] > 0]

    return rows, {
        "source": str(path),
        "raw_rows": len(raw),
        "oneway_rule": "deployed_observed_share_band",
        "oneway_share_band": list(ONEWAY_SHARE_BAND),
        "oneway_stations_dropped": len(oneway_stations),
        "osm_oneway_stations_would_drop": len(osm_oneway_stations),
        "stations_before_screen": len({r["station_id"] for r in raw}),
        "deployed_training_band": DEPLOYED_TRAINING_BAND,
        "rows_kept": len(rows),
        "stations": len({r.station_id for r in rows}),
        "cities": sorted({r.city for r in rows}),
        "blocks": len({r.block for r in rows}),
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

    #: No entrant here is the deployed dirsplit model. Every one of them is
    #: fit on THIS table and THESE folds; the deployment is fit with a
    #: similarity kernel aimed at the Gothenburg sensor edges. A score here
    #: is evidence about the entrant, never about the deployed model.
    deployment_equivalent = False

    def fit(self, rows: Sequence[Row]) -> "Candidate":
        raise NotImplementedError

    def predict(self, rows: Sequence[Row]) -> np.ndarray:
        raise NotImplementedError


class Constant5050(Candidate):
    """The traffic-engineering null, and the shrinkage target of the rest."""

    name = "constant_5050"
    complexity = 0

    def fit(self, rows):
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

    def fit(self, rows):
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

    def fit(self, rows):
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


class LgbmReimplementation(Candidate):
    """A gradient-boosting entrant. NOT the deployed model.

    Named this way deliberately. It differs from `dirsplit/train.py` in at
    least two ways that matter, so a result here cannot be reported as a
    statement about the deployed model:

    * the similarity kernel is centred on the TRAINING population's centroid,
      while deployment centres it on the Gothenburg target (and, in the
      leave-city-out arm, on the held-out station itself);
    * standardisation and bandwidth are fit inside each fold, which is the
      leakage requirement here, while deployment computes them once over
      everything.

    It earns its place in the tournament as "does gradient boosting on these
    features beat an even split under leakage-free folds", which is a real
    question. It does not answer "is the shipped model worse than 50/50";
    answering that needs the deployed trainer itself under these folds.
    """

    name = "lgbm_reimplementation"
    complexity = 3
    deployment_equivalent = False

    def __init__(self, n_estimators: int = 400):
        self.n_estimators = n_estimators
        self._model = None
        self._mu = None
        self._sd = None

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

    def fit(self, rows):
        import lightgbm as lgb

        if len(rows) < 30:
            self._model = None
            return self
        matrix = self._matrix(rows)
        target = np.array([r.share for r in rows], dtype=float)

        static = matrix[:, :len(FEATURE_NAMES)]
        self._mu = static.mean(axis=0)
        self._sd = static.std(axis=0)
        self._sd[self._sd < 1e-9] = 1.0
        standardised = (static - self._mu) / self._sd
        centre = standardised.mean(axis=0)
        distances = np.sqrt(((standardised - centre) ** 2).sum(axis=1))
        bandwidth = float(np.median(distances)) or 1.0
        similarity = np.exp(-0.5 * (distances / bandwidth) ** 2)

        counts: dict[str, int] = {}
        for row in rows:
            counts[row.station_id] = counts.get(row.station_id, 0) + 1
        station_weight = np.array(
            [1.0 / counts[r.station_id] for r in rows], dtype=float)

        self._model = lgb.LGBMRegressor(
            n_estimators=self.n_estimators, learning_rate=0.05, max_depth=5,
            num_leaves=31, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=20, random_state=42, n_jobs=-1, verbose=-1)
        self._model.fit(matrix, target,
                        sample_weight=station_weight * similarity)
        return self

    def predict(self, rows):
        if self._model is None:
            return np.full(len(rows), 0.5, dtype=float)
        return np.clip(self._model.predict(self._matrix(rows)), 0.1, 0.9)


def default_candidates() -> list[Candidate]:
    return [Constant5050(), ShrunkDFactor(), BetaBinomialDFactor(),
            LgbmReimplementation()]


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
class Pooled:
    """Held-out errors pooled across one fold kind, in fold/test-row order.

    Rule 2c compares a candidate against the CURRENT INCUMBENT, not only
    against 50/50. That comparison has to be paired row-by-row on the same
    held-out predictions, so the raw arrays are kept rather than only the
    summary statistics that go into the report.
    """

    err: np.ndarray
    baseline: np.ndarray
    blocks: tuple[str, ...]
    groups: tuple[tuple[str, ...], ...]

    def mask_for(self, group: str) -> np.ndarray:
        return np.array([group in g for g in self.groups], dtype=bool)

    def alignment_key(self) -> tuple[str, ...]:
        """Two Pooled objects are pairable only if this matches."""
        return self.blocks


def pooled_errors(candidate: Candidate, folds: Sequence[Fold]) -> Pooled:
    """Fit per fold on the training side only, then pool held-out errors."""
    import copy

    errors: list[float] = []
    baseline: list[float] = []
    blocks: list[str] = []
    groups: list[tuple[str, ...]] = []

    for fold in folds:
        fitted = copy.deepcopy(candidate).fit(list(fold.train))
        predicted = fitted.predict(list(fold.test))
        actual = np.array([r.share for r in fold.test], dtype=float)
        errors.extend(np.abs(actual - predicted).tolist())
        baseline.extend(np.abs(actual - 0.5).tolist())
        blocks.extend(r.block for r in fold.test)
        groups.extend(groups_of(r) for r in fold.test)

    return Pooled(np.array(errors, dtype=float),
                  np.array(baseline, dtype=float),
                  tuple(blocks), tuple(groups))


def pairwise_ci(candidate: Pooled, incumbent: Pooled,
                group: str) -> tuple[float, float]:
    """95% block-bootstrap CI on (candidate MAE - incumbent MAE) in a group.

    Returns NaNs when the two pools are not row-aligned, which counts as
    "does not win" under rule 3.
    """
    if candidate.alignment_key() != incumbent.alignment_key():
        return (float("nan"), float("nan"))
    mask = candidate.mask_for(group)
    if not mask.any():
        return (float("nan"), float("nan"))
    return paired_difference_ci(
        candidate.err[mask], incumbent.err[mask],
        [b for b, m in zip(candidate.blocks, mask) if m])


def score(candidate: Candidate, folds: Sequence[Fold],
          pooled: Pooled | None = None) -> dict[str, Any]:
    """Summarise pooled held-out errors into the reportable statistics."""
    if pooled is None:
        pooled = pooled_errors(candidate, folds)

    err, base = pooled.err, pooled.baseline
    blocks, groups = pooled.blocks, pooled.groups
    if err.size == 0:
        return {"n": 0, "groups": {}}

    per_group: dict[str, Any] = {}
    for name in ("all", "weekday", "weekend", "weekday_peak", "off_hours"):
        mask = np.array([name in g for g in groups], dtype=bool)
        if not mask.any():
            continue
        low, high = paired_difference_ci(
            err[mask], base[mask], [b for b, m in zip(blocks, mask) if m])
        base_mae = float(np.mean(base[mask]))
        per_group[name] = {
            "n": int(mask.sum()),
            "n_blocks": len({b for b, m in zip(blocks, mask) if m}),
            "mae": round(float(np.mean(err[mask])), 6),
            "mae_5050": round(base_mae, 6),
            "improvement_pct": round(
                100.0 * (base_mae - float(np.mean(err[mask]))) / base_mae, 2)
            if base_mae > 0 else 0.0,
            "paired_diff_ci95": [round(low, 6), round(high, 6)],
            "beats_baseline": bool(high < 0),
            "loses_to_baseline": bool(low > 0),
        }
    return {
        "n": int(len(err)),
        "n_blocks": len(set(blocks)),
        "mae": round(float(np.mean(err)), 6),
        "mae_5050": round(float(np.mean(base)), 6),
        "groups": per_group,
    }


# ──────────────────────────────────────────────────────────────────────────
# Gate M
# ──────────────────────────────────────────────────────────────────────────
def _inconclusive_m(reason: str) -> dict[str, Any]:
    return {
        "gate_m": "INCONCLUSIVE",
        "winner": None,
        "reason": reason,
        "selection_rule": SELECTION_RULE,
    }


def decide_gate_m(reports: Mapping[str, Mapping[str, Any]],
                  candidates: Sequence[Candidate],
                  meta: Mapping[str, Any],
                  pooled: Mapping[tuple[str, str], Pooled] | None = None,
                  ) -> dict[str, Any]:
    """Apply SELECTION_RULE_TEXT. BASELINE / MODEL / INCONCLUSIVE.

    ``pooled`` maps (candidate name, fold kind) to the held-out errors that
    rule 2c needs to compare a candidate against the current incumbent. It
    is optional only so that the function stays callable on a stored report;
    without it no candidate can demonstrate 2c, so nothing is promoted.
    """
    ordered = sorted(candidates, key=lambda c: (c.complexity, c.name))
    incumbent = ordered[0].name
    detail: dict[str, Any] = {}
    pooled = pooled or {}

    if meta.get("leakage_detected"):
        return _inconclusive_m(
            "leakage was detected in the fold construction; a held-out "
            "difference measured through a leak is not evidence")

    ran_kinds = sorted({kind for report in reports.values() for kind in report})
    missing = [kind for kind in REQUIRED_FOLD_KINDS if kind not in ran_kinds]
    if missing:
        return _inconclusive_m(
            f"required fold kind(s) {', '.join(missing)} could not be built "
            f"from this table (ran: {', '.join(ran_kinds) or 'none'}); a gate "
            "that could not be measured is not a gate that answered no")

    if meta.get("blocks", 0) < MIN_INDEPENDENT_BLOCKS:
        return _inconclusive_m(
            f"only {meta.get('blocks')} independent blocks; "
            f"{MIN_INDEPENDENT_BLOCKS} are required before a difference can "
            "be called robust")

    for candidate in ordered[1:]:
        report = reports.get(candidate.name) or {}
        if not report:
            detail[candidate.name] = {"promoted": False, "why": "no results"}
            continue

        notes: dict[str, str] = {}
        loses_anywhere = False
        wins_null: dict[str, bool] = {}
        beats_incumbent: dict[str, bool] = {}

        for kind in ran_kinds:
            stats = report.get(kind)
            if stats is None:
                notes[kind] = "candidate not scored on this fold kind"
                wins_null[kind] = False
                beats_incumbent[kind] = False
                continue

            won_here = False
            beat_here = False
            for group in PRIMARY_GROUPS:
                entry = (stats.get("groups") or {}).get(group)
                if not entry:
                    notes[f"{kind}:{group}"] = "unmeasured"
                    continue
                if entry.get("loses_to_baseline"):
                    loses_anywhere = True
                    notes[f"{kind}:{group}"] = "loses to 50/50"
                    continue
                if entry.get("beats_baseline"):
                    won_here = True
                    notes[f"{kind}:{group}"] = "beats 50/50"
                else:
                    notes[f"{kind}:{group}"] = "tie with 50/50"

                mine = pooled.get((candidate.name, kind))
                theirs = pooled.get((incumbent, kind))
                if mine is None or theirs is None:
                    notes[f"{kind}:{group}:vs_{incumbent}"] = "unpairable"
                    continue
                low, high = pairwise_ci(mine, theirs, group)
                if math.isnan(low) or math.isnan(high):
                    notes[f"{kind}:{group}:vs_{incumbent}"] = "unpairable"
                elif high < 0:
                    beat_here = True
                    notes[f"{kind}:{group}:vs_{incumbent}"] = (
                        f"beats incumbent [{low:.4f}, {high:.4f}]")
                else:
                    notes[f"{kind}:{group}:vs_{incumbent}"] = (
                        f"no win over incumbent [{low:.4f}, {high:.4f}]")

            wins_null[kind] = won_here
            beats_incumbent[kind] = beat_here

        wins_everywhere = all(wins_null.get(k, False) for k in ran_kinds)
        beats_everywhere = all(beats_incumbent.get(k, False)
                               for k in ran_kinds)
        promoted = (not loses_anywhere) and wins_everywhere and beats_everywhere

        if promoted:
            why = f"beats 50/50 and {incumbent} under every fold kind"
        elif loses_anywhere:
            why = "significantly loses to 50/50 in a primary group"
        elif not wins_everywhere:
            missing_kinds = [k for k in ran_kinds if not wins_null.get(k)]
            why = ("no significant win over 50/50 under fold kind(s) "
                   + ", ".join(missing_kinds))
        else:
            missing_kinds = [k for k in ran_kinds
                             if not beats_incumbent.get(k)]
            why = (f"does not significantly beat the incumbent {incumbent} "
                   "under fold kind(s) " + ", ".join(missing_kinds))

        detail[candidate.name] = {
            "promoted": promoted,
            "compared_against_incumbent": incumbent,
            "why": why,
            "wins_over_null_by_fold_kind": wins_null,
            "beats_incumbent_by_fold_kind": beats_incumbent,
            "notes": notes,
        }
        if promoted:
            incumbent = candidate.name

    gate = "BASELINE" if incumbent == "constant_5050" else "MODEL"
    return {
        "gate_m": gate,
        "winner": incumbent,
        "reason": ("no candidate beat both 50/50 and the incumbent under "
                   "every fold kind that ran"
                   if gate == "BASELINE"
                   else f"{incumbent} beats 50/50 and every simpler incumbent "
                        "under every fold kind that ran"),
        "selection_rule": SELECTION_RULE,
        "fold_kinds_scored": ran_kinds,
        "detail": detail,
    }


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

    reports: dict[str, dict[str, Any]] = {}
    pooled: dict[tuple[str, str], Pooled] = {}
    for candidate in candidates:
        per_kind: dict[str, Any] = {}
        for kind, build in builders.items():
            folds = build(rows)
            if not folds:
                continue
            held_out = pooled_errors(candidate, folds)
            if held_out.err.size:
                pooled[(candidate.name, kind)] = held_out
            per_kind[kind] = score(candidate, folds, pooled=held_out)
        if per_kind:
            reports[candidate.name] = per_kind

    decision = decide_gate_m(reports, candidates, meta, pooled=pooled)
    return {
        "protocol": "dirsplit_gate_m_v1",
        "selection_rule": SELECTION_RULE,
        "selection_rule_text": SELECTION_RULE_TEXT,
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "primary_groups": list(PRIMARY_GROUPS),
        "release_evidence": False,
        "input": meta,
        "temporal_support": temporal_support(rows),
        "fold_kinds": sorted(builders),
        "blocked_date_available": bool(date_folds),
        "reports": reports,
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
