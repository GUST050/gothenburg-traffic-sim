"""Coherent whole-day demand cases from observed residual blocks (step 4).

This module is where the plan's central argument becomes code.

Calibrating each cell's marginal separately does not give you a day. Applying
the q10 of every edge and every hour simultaneously produces a world in which
every road is unusually quiet at every hour at once — which is not the 10th
percentile day, because roads and hours are correlated and a marginal
quantile carries no dependence information at all. The forecasting literature
names this exactly (Schefzik/Thorarinsdottir/Gneiting on Ensemble Copula
Coupling): after marginal calibration you still need a dependence template to
rebuild realistic space-time trajectories, and the variogram score exists
because the energy score is relatively insensitive to getting that dependence
wrong.

The first implementable template is not a parametric copula but the data
itself. Method, following the plan:

1. Fit the central model with a city or period blocked out.
2. Compute residuals on the **logit scale** for every observed
   ``(city, date, station, hour)``. Logit because a share lives in (0, 1) and
   an additive residual there cannot push a share out of range or produce the
   asymmetric distortion that adding on the raw scale would.
3. Keep each **city-date as one whole block**. That block carries the real
   within-day shape and the real between-road correlation for that day.
4. Match each Gothenburg edge to a donor station by applicability features,
   and draw all of a scenario's edges from the SAME donor city-date, so the
   cross-road structure survives the transfer.
5. Add the block, transform back, and normalise the pair **exactly**.
6. Select cases outcome-blind by stable hash.

Step 5 is also the repair for the defect recorded in `dirsplit/legacy.py`.
The old writer produced the partner direction from its own independent
quantile, so a q10 pair summed to ``1 - (s90 - s10)`` and a q90 pair to
``1 + (s90 - s10)`` — deleting or inventing roughly a tenth of the measured
two-way total at sensor 107. Here the partner is *derived* as ``1 - s``, so
the identity holds by construction and cannot drift.

Multi-day horizons reuse contiguous observed blocks first, and fall back to a
pre-registered moving-block bootstrap that preserves observed multi-day
autocorrelation. Independent day sampling is available but is labelled a
baseline, never the default, because it destroys exactly the dependence this
module exists to keep.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .dataset_schema import DirectionRow
from .models import DirectionModel, expit, logit, shares, usable
from .schema import (SLOT_COUNT, DemandCase, ScenarioPath, make_case_id,
                     make_path_id)

GENERATOR_VERSION = "blocked_residual_path_v1"

#: Independent-sampling fallback, kept only as a labelled baseline.
INDEPENDENT_GENERATOR = "independent_day_baseline_v1"


def hourly_to_slots(hourly: Sequence[float]) -> np.ndarray:
    """Interpolate 24 hourly values onto 96 quarter-hour slots.

    Matches `predict.hourly_to_slots` exactly, including the wrap back to
    hour 0, so a v2 case lands on the same grid the deployment already uses.
    """
    values = np.asarray(hourly, dtype=float)
    slots = np.arange(SLOT_COUNT) / 4.0
    xs = np.arange(25, dtype=float)
    ys = np.append(values, values[0])
    return np.interp(slots, xs, ys)


@dataclass(frozen=True)
class ResidualBlock:
    """One city-date's held-out residuals, kept whole.

    ``residuals`` maps ``(station_id, hour)`` to a logit-scale residual. The
    block is the unit of transfer: a scenario draws every edge from one
    block, so the between-road and within-day correlation observed on that
    real day is what shapes the generated day.
    """

    city: str
    date: str
    residuals: Mapping[tuple[str, int], float]

    @property
    def block_id(self) -> str:
        return f"{self.city}:{self.date}"

    @property
    def stations(self) -> tuple[str, ...]:
        return tuple(sorted({station for station, _hour in self.residuals}))

    def hourly_for(self, station_id: str) -> np.ndarray | None:
        """24 hourly residuals for one station, or None if too sparse.

        Missing hours are filled by interpolation across observed hours
        rather than with zeros: a zero is a claim that the model was exactly
        right at that hour, which is a different and stronger statement than
        "not observed".
        """
        observed = {hour: value for (station, hour), value
                    in self.residuals.items() if station == station_id}
        if len(observed) < 3:
            return None
        hours = np.array(sorted(observed), dtype=float)
        values = np.array([observed[int(h)] for h in hours], dtype=float)
        return np.interp(np.arange(24, dtype=float), hours, values,
                         left=values[0], right=values[-1])


def build_residual_library(
    rows: Sequence[DirectionRow],
    model: DirectionModel,
    fold_builder,
) -> list[ResidualBlock]:
    """Held-out logit residuals, grouped into whole city-date blocks.

    The model is refit per fold and residuals are taken only on the held-out
    side, so no block contains a residual the model had already seen. A
    library built on training residuals would understate the spread by
    exactly the amount the model overfits.
    """
    import copy

    collected: dict[tuple[str, str], dict[tuple[str, int], float]] = {}
    for fold in fold_builder(rows):
        train = usable(fold.train)
        test = usable(fold.test)
        if not train or not test:
            continue
        fitted = copy.deepcopy(model).fit(train)
        predicted = fitted.predict(test).central
        actual = shares(test)
        residual = logit(actual) - logit(predicted)
        for row, value in zip(test, residual):
            key = (row.city, row.local_date)
            collected.setdefault(key, {})[(row.station_id, row.hour)] = \
                float(value)

    return [
        ResidualBlock(city=city, date=date, residuals=residuals)
        for (city, date), residuals in sorted(collected.items())
    ]


def donor_for_edge(edge_features: Mapping[str, float],
                   donors: Mapping[str, Mapping[str, float]],
                   feature_names: Sequence[str]) -> str | None:
    """Nearest donor station in standardised applicability-feature space.

    Explicitly the SAME features the applicability profile grades on, so a
    donor cannot be "similar" by a measure the evidence profile never checks.
    """
    if not donors:
        return None
    names = list(feature_names)
    matrix = np.array(
        [[float(f.get(name, 0.0)) for name in names] for f in donors.values()],
        dtype=float,
    )
    scale = matrix.std(axis=0)
    scale[scale == 0] = 1.0
    centre = matrix.mean(axis=0)
    target = np.array([float(edge_features.get(name, 0.0)) for name in names],
                      dtype=float)
    distances = np.linalg.norm((matrix - centre) / scale
                               - (target - centre) / scale, axis=1)
    return list(donors)[int(np.argmin(distances))]


def apply_block(
    central: Mapping[str, Sequence[float]],
    edge_pairs: Sequence[tuple[str, str]],
    block: ResidualBlock,
    donor_by_edge: Mapping[str, str],
) -> dict[str, tuple[float, ...]]:
    """Add a residual block to the central profile, pair-exact.

    The partner direction is DERIVED as ``1 - s`` rather than perturbed
    independently. That is what makes the pair-sum identity hold by
    construction, and it is the specific defect this repairs: the legacy
    writer perturbed both members and produced pairs summing to 1 +/- the
    interval width.
    """
    out: dict[str, tuple[float, ...]] = {}
    for lead, partner in edge_pairs:
        base = np.asarray(central[lead], dtype=float)
        donor = donor_by_edge.get(lead)
        adjustment = np.zeros(SLOT_COUNT, dtype=float)
        if donor is not None:
            hourly = block.hourly_for(donor)
            if hourly is not None:
                adjustment = hourly_to_slots(hourly)
        shifted = expit(logit(base) + adjustment)
        shifted = np.clip(shifted, 1e-6, 1 - 1e-6)
        out[lead] = tuple(float(x) for x in shifted)
        out[partner] = tuple(float(1.0 - x) for x in shifted)
    return out


def _stable_index(seed_text: str, modulo: int) -> int:
    """Deterministic outcome-blind index from a stable hash.

    A stable hash rather than an RNG so the selection is reproducible from
    the artifact alone, and outcome-blind because the seed text names only
    the inputs — never a simulated result.
    """
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % max(modulo, 1)


def select_blocks(library: Sequence[ResidualBlock], n: int,
                  selection_key: str) -> list[ResidualBlock]:
    """Outcome-blind stratified selection across cities and dates.

    Stratifies by city first so one city cannot supply every scenario, then
    picks within each city by stable hash.
    """
    if not library or n <= 0:
        return []
    by_city: dict[str, list[ResidualBlock]] = {}
    for block in library:
        by_city.setdefault(block.city, []).append(block)
    cities = sorted(by_city)
    chosen: list[ResidualBlock] = []
    round_index = 0
    while len(chosen) < n and round_index < n + len(cities):
        for city in cities:
            if len(chosen) >= n:
                break
            pool = [b for b in by_city[city] if b not in chosen]
            if not pool:
                continue
            index = _stable_index(f"{selection_key}|{city}|{round_index}",
                                  len(pool))
            chosen.append(pool[index])
        round_index += 1
    return chosen[:n]


def generate_cases(
    central: Mapping[str, Sequence[float]],
    edge_pairs: Sequence[tuple[str, str]],
    library: Sequence[ResidualBlock],
    donor_by_edge: Mapping[str, str],
    *,
    n_cases: int,
    day_type: str = "weekday",
    selection_key: str = GENERATOR_VERSION,
) -> list[DemandCase]:
    """Probabilistic demand cases with equal weights summing to exactly 1.

    Equal weights because the blocks are selected outcome-blind from observed
    days: nothing in the selection justifies preferring one real day over
    another, and inventing unequal weights would be inventing information.

    The final weight is adjusted so the sum is exactly 1 rather than
    ``n * (1/n)``, which for n=3 is 0.9999999999999999 and would fail the
    ensemble's weight check for a reason that has nothing to do with traffic.
    """
    blocks = select_blocks(library, n_cases, selection_key)
    if not blocks:
        return []
    cases: list[DemandCase] = []
    weight = 1.0 / len(blocks)
    for index, block in enumerate(blocks):
        payload = apply_block(central, edge_pairs, block, donor_by_edge)
        is_last = index == len(blocks) - 1
        this_weight = (1.0 - weight * (len(blocks) - 1)) if is_last else weight
        provenance = {
            "donor_city": block.city,
            "donor_date": block.date,
            "donor_block_id": block.block_id,
            "donor_by_edge": dict(sorted(donor_by_edge.items())),
            "selection": "outcome_blind_stable_hash",
        }
        cases.append(DemandCase(
            case_id=make_case_id(generator=GENERATOR_VERSION,
                                 day_type=day_type, shares=payload,
                                 provenance=provenance),
            kind="probabilistic",
            day_type=day_type,
            shares=payload,
            edge_pairs=tuple(edge_pairs),
            generator=GENERATOR_VERSION,
            weight=this_weight,
            provenance=provenance,
        ))
    return cases


def contiguous_runs(library: Sequence[ResidualBlock],
                    length: int) -> list[list[ResidualBlock]]:
    """Runs of consecutive calendar dates within one city.

    Preferred over resampling for multi-day horizons because a real run of
    consecutive days carries the observed day-to-day autocorrelation, which
    no independent draw reproduces.
    """
    from datetime import date as date_cls

    by_city: dict[str, list[ResidualBlock]] = {}
    for block in library:
        by_city.setdefault(block.city, []).append(block)

    runs: list[list[ResidualBlock]] = []
    for city in sorted(by_city):
        blocks = sorted(by_city[city], key=lambda b: b.date)
        for start in range(len(blocks) - length + 1):
            window = blocks[start:start + length]
            dates = [date_cls.fromisoformat(b.date) for b in window]
            if all((dates[i + 1] - dates[i]).days == 1
                   for i in range(len(dates) - 1)):
                runs.append(window)
    return runs


def build_paths(
    cases_by_block: Mapping[str, DemandCase],
    library: Sequence[ResidualBlock],
    calendar_dates: Sequence[str],
    *,
    n_paths: int,
    selection_key: str = GENERATOR_VERSION,
) -> tuple[list[ScenarioPath], str]:
    """Scenario paths over a multi-day horizon.

    Returns the paths and the method actually used, so a consumer can tell a
    genuine contiguous run from a bootstrap fallback. The method name is not
    cosmetic: an independent-day path is a weaker claim and must be labelled
    as one.
    """
    horizon = len(calendar_dates)
    if horizon == 0 or not cases_by_block:
        return [], "none"

    runs = contiguous_runs(library, horizon) if horizon > 1 else [
        [block] for block in library
    ]
    method = "contiguous_observed_run"
    if not runs:
        # Moving-block bootstrap over whatever contiguity exists.
        runs = _moving_block_runs(library, horizon)
        method = "moving_block_bootstrap"
    if not runs:
        runs = _independent_runs(library, horizon, selection_key)
        method = INDEPENDENT_GENERATOR

    paths: list[ScenarioPath] = []
    for index in range(min(n_paths, len(runs))):
        pick = _stable_index(f"{selection_key}|path|{index}", len(runs))
        run = runs[pick]
        case_ids = []
        usable_run = True
        for block in run:
            case = cases_by_block.get(block.block_id)
            if case is None:
                usable_run = False
                break
            case_ids.append(case.case_id)
        if not usable_run:
            continue
        paths.append(ScenarioPath(
            path_id=make_path_id(case_ids=case_ids,
                                 calendar_dates=list(calendar_dates),
                                 generator_version=method),
            case_ids=tuple(case_ids),
            calendar_dates=tuple(calendar_dates),
            generator_version=method,
        ))
    return paths, method


def _moving_block_runs(library: Sequence[ResidualBlock],
                       horizon: int) -> list[list[ResidualBlock]]:
    """Overlapping runs stitched from the longest available contiguous spans."""
    best: list[list[ResidualBlock]] = []
    for length in range(horizon - 1, 1, -1):
        spans = contiguous_runs(library, length)
        if not spans:
            continue
        for span in spans:
            run = list(span)
            while len(run) < horizon:
                run.append(span[len(run) % len(span)])
            best.append(run[:horizon])
        if best:
            break
    return best


def _independent_runs(library: Sequence[ResidualBlock], horizon: int,
                      selection_key: str) -> list[list[ResidualBlock]]:
    if not library:
        return []
    runs = []
    for offset in range(min(8, len(library))):
        run = [
            library[_stable_index(f"{selection_key}|ind|{offset}|{day}",
                                  len(library))]
            for day in range(horizon)
        ]
        runs.append(run)
    return runs


__all__ = [
    "GENERATOR_VERSION", "INDEPENDENT_GENERATOR", "ResidualBlock",
    "build_residual_library", "donor_for_edge", "apply_block",
    "select_blocks", "generate_cases", "contiguous_runs", "build_paths",
    "hourly_to_slots",
]
