"""Bind a station's published, period-aggregate direction split to the
estimated per-slot profile — without inventing per-slot measurements.

Why this module exists
----------------------
Sensor 107 is the only station whose two-way total is split into TWO level-1
targets, so it is the only place where the direction model directly decides
what the calibration must reproduce.  Goteborgs Stad publishes a per-direction
mean daily volume for exactly that station (3 400 N / 3 100 S in 2025, roughly
52/48).  That is local evidence about an aggregate, and local evidence outranks
a model transferred from Norwegian streets for the same aggregate quantity.

What it deliberately does NOT do
--------------------------------
A yearly D-factor says nothing about 07:15 on a Tuesday.  Anchoring therefore
never replaces the per-slot profile: the estimated profile keeps its entire
time-of-day SHAPE and is only re-levelled, by one constant shift in log-odds
space, until the declared period reproduces the published share.  The per-slot
values stay estimates and must keep being labelled as such; only their
period mean is measurement-backed.

Mechanics
---------
``share -> logit(share) + delta -> sigmoid`` is monotone in ``delta`` and stays
inside (0, 1), so the anchoring shift is found by bisection on the period mean
and can never produce an invalid share.  The complement direction is shifted by
``-delta``, which is exactly complement-consistent — ``sigmoid(logit(1-x) -
delta) == 1 - sigmoid(logit(x) + delta)`` — so every structural relation the
split file already holds (pair sums, mirrored quantiles) survives untouched.

The period mean is weighted by the MEASURED two-way total in each slot, because
a D-factor is a volume ratio: a slot carrying 200 vehicles must count for more
than a slot carrying 5.  The weights come from the measured reference year, not
from whichever source (historical/forecast) is being simulated — the anchor is
structural evidence about the street, in the same spirit as
``STRUCTURAL_REFERENCE_DATE`` in the demand builder.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from traffic_sim.intake.sensors import DirectionalReference

# The split artifact clamps shares to this range; anchoring must not smuggle a
# value past the clamp the producer declared.
DEFAULT_CLAMP = (0.1, 0.9)
SHARE_KEY_PREFIX = "edge_shares"
_MAX_SHIFT = 20.0            # log-odds; far beyond any real D-factor correction
_BISECTION_STEPS = 200       # ~1e-58 resolution on the shift; cost is trivial


@dataclass(frozen=True)
class AnchorOutcome:
    """What anchoring did to one station, in auditable terms."""

    sensor_id: str
    status: str
    reference_edge: str
    target_share: float | None = None
    prior_share: float | None = None
    achieved_share: float | None = None
    delta_logit: float | None = None
    weight_source: str | None = None
    weighted_slots: int = 0
    tolerance: float | None = None
    period: Mapping[str, str] = field(default_factory=dict)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "sensor_id": self.sensor_id,
            "status": self.status,
            "reference_edge": self.reference_edge,
            "target_share": self.target_share,
            "prior_share": self.prior_share,
            "achieved_share": self.achieved_share,
            "delta_logit": self.delta_logit,
            "weight_source": self.weight_source,
            "weighted_slots": self.weighted_slots,
            "tolerance": self.tolerance,
            "period": dict(self.period),
            # Load-bearing label: the anchor constrains a period mean only.
            "per_slot_semantics": "estimated",
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def logit(share: float) -> float:
    share = min(max(float(share), 1e-9), 1 - 1e-9)
    return math.log(share / (1 - share))


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


def shift_share(share: float, delta: float,
                clamp: tuple[float, float] = DEFAULT_CLAMP) -> float:
    """One share, moved by ``delta`` in log-odds and re-clamped."""
    lo, hi = clamp
    return min(max(sigmoid(logit(share) + delta), lo), hi)


def weighted_mean_share(shares: Sequence[float], weights: Sequence[float],
                        delta: float = 0.0,
                        clamp: tuple[float, float] = DEFAULT_CLAMP) -> float:
    """Flow-weighted period mean of a per-slot profile after a log-odds shift."""
    total_weight = float(sum(weights))
    if total_weight <= 0:
        raise ValueError("cannot take a weighted mean with zero total weight")
    return sum(weight * shift_share(share, delta, clamp)
               for share, weight in zip(shares, weights)) / total_weight


def solve_odds_shift(shares: Sequence[float], weights: Sequence[float],
                     target: float,
                     clamp: tuple[float, float] = DEFAULT_CLAMP) -> float:
    """The log-odds shift whose weighted period mean equals ``target``.

    Monotone in ``delta``, so bisection is exact to machine precision and needs
    no starting guess.  A target the clamp cannot reach saturates at the
    bracket end; the caller compares the achieved mean against the reference's
    declared tolerance rather than trusting the shift blindly.
    """
    if not 0 < target < 1:
        raise ValueError(f"target share must lie in (0, 1), got {target}")
    low, high = -_MAX_SHIFT, _MAX_SHIFT
    if weighted_mean_share(shares, weights, low, clamp) >= target:
        return low
    if weighted_mean_share(shares, weights, high, clamp) <= target:
        return high
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2
        if weighted_mean_share(shares, weights, middle, clamp) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def period_slot_weights(
    flows: Mapping[str, Sequence[float | None]],
    edge_ids: Iterable[str],
    *,
    epoch: datetime,
    interval_minutes: int,
    period_start: str,
    period_end_exclusive: str,
    slots_per_day: int,
) -> list[float]:
    """Measured two-way volume per time-of-day slot inside the period.

    A station delivered as a two-way ``Total`` carries the SAME value on both
    directed edges (CLAUDE.md's reporting trap).  Counting both would double
    every weight — harmless for a ratio, but wrong the moment a genuinely
    directional delivery arrives, so identical series are counted once and
    differing series are summed.
    """
    series = [list(flows.get(edge_id) or []) for edge_id in edge_ids]
    series = [values for values in series if values]
    if not series:
        return [0.0] * slots_per_day
    duplicate_total = len(series) > 1 and all(
        values == series[0] for values in series[1:])
    start = datetime.fromisoformat(period_start)
    end = datetime.fromisoformat(period_end_exclusive)
    weights = [0.0] * slots_per_day
    step = timedelta(minutes=interval_minutes)
    for index in range(max(len(values) for values in series)):
        stamp = epoch + index * step
        if not start <= stamp < end:
            continue
        values = [float(v[index]) for v in series
                  if index < len(v) and v[index] is not None]
        if not values:
            continue
        total = values[0] if duplicate_total else sum(values)
        weights[index % slots_per_day] += total
    return weights


def _share_maps(entry: Mapping[str, Any]) -> dict[str, dict[str, list[float]]]:
    return {key: value for key, value in entry.items()
            if key.startswith(SHARE_KEY_PREFIX) and isinstance(value, dict)}


def anchor_split_entry(
    entry: Mapping[str, Any],
    reference: DirectionalReference,
    weights: Sequence[float],
    *,
    sensor_id: str,
    weight_source: str,
    clamp: tuple[float, float] = DEFAULT_CLAMP,
) -> tuple[dict[str, Any], AnchorOutcome]:
    """Re-level one station's split so the declared period reproduces its share."""
    entry = json.loads(json.dumps(entry))          # never mutate the caller's data
    reference_edge = reference.reference_edge_id
    other_edge = next(edge for edge in reference.edge_ids if edge != reference_edge)
    maps = _share_maps(entry)
    central = maps.get(SHARE_KEY_PREFIX)
    if central is None or reference_edge not in central:
        if central is not None and other_edge in central:
            # Half a pair is an impossible pairing, not a missing estimate.
            raise ValueError(
                f"sensor {sensor_id}: direction split contains {other_edge} but not "
                f"the reference edge {reference_edge}; the registry and the split "
                f"artifact disagree about this station's directed edges")
        return entry, AnchorOutcome(
            sensor_id=sensor_id, status="skipped_no_estimate",
            reference_edge=reference_edge,
            detail="station has no direction estimate to anchor")
    profile = [float(value) for value in central[reference_edge]]
    if len(weights) != len(profile):
        raise ValueError(
            f"sensor {sensor_id}: {len(weights)} period weights for a "
            f"{len(profile)}-slot direction profile")
    target = reference.share(reference_edge)
    period = {"label": reference.period_label, "start": reference.period_start,
              "end_exclusive": reference.period_end_exclusive}
    if sum(weights) <= 0:
        raise ValueError(
            f"sensor {sensor_id}: no measured volume inside the declared anchor "
            f"period {reference.period_start}..{reference.period_end_exclusive}")

    prior = weighted_mean_share(profile, weights, 0.0, clamp)
    delta = solve_odds_shift(profile, weights, target, clamp)
    for share_map in maps.values():
        for edge_id, values in share_map.items():
            if edge_id not in (reference_edge, other_edge):
                # Only this station's own pair is anchored. An unrelated edge
                # sharing the entry keeps its estimate untouched rather than
                # being shifted by a reference that says nothing about it.
                continue
            # The complement direction moves by -delta, which keeps every
            # mirrored-quantile relation in the artifact exactly intact.
            sign = 1.0 if edge_id == reference_edge else -1.0
            share_map[edge_id] = [shift_share(value, sign * delta, clamp)
                                  for value in values]
    achieved = weighted_mean_share(
        [float(v) for v in maps[SHARE_KEY_PREFIX][reference_edge]],
        weights, 0.0, clamp)
    status = ("anchored" if abs(achieved - target) <= reference.tolerance
              else "anchor_out_of_tolerance")
    entry["directional_anchor"] = {
        "reference_edge": reference_edge,
        "target_share": target,
        "achieved_share": achieved,
        "delta_logit": delta,
        "period": period,
        "weighting": reference.weighting,
        "weight_source": weight_source,
        "source": dict(reference.source),
        "status": status,
        "note": ("Period mean anchored to the published local D-factor; the "
                 "per-slot values remain ESTIMATED and are not level-1 "
                 "directional measurements."),
    }
    return entry, AnchorOutcome(
        sensor_id=sensor_id, status=status, reference_edge=reference_edge,
        target_share=target, prior_share=prior, achieved_share=achieved,
        delta_logit=delta, weight_source=weight_source,
        weighted_slots=sum(1 for weight in weights if weight > 0),
        tolerance=reference.tolerance, period=period)


def anchor_split_payload(
    payload: Mapping[str, Any],
    references: Mapping[str, DirectionalReference],
    *,
    flows: Mapping[str, Sequence[float | None]] | None = None,
    epoch: datetime | None = None,
    interval_minutes: int = 15,
    clamp: tuple[float, float] = DEFAULT_CLAMP,
) -> tuple[dict[str, Any], list[AnchorOutcome]]:
    """Anchor every station in a direction-split payload that has local evidence.

    Stations without a verified ``directional_reference`` — which today is all
    five single-direction stations — pass through byte-identically.
    """
    anchored = json.loads(json.dumps(dict(payload)))
    outcomes: list[AnchorOutcome] = []
    for sensor_id, reference in sorted(references.items()):
        entry = anchored.get(sensor_id)
        if not isinstance(entry, Mapping):
            outcomes.append(AnchorOutcome(
                sensor_id=sensor_id, status="skipped_no_estimate",
                reference_edge=reference.reference_edge_id,
                detail="station absent from the direction-split artifact"))
            continue
        central = _share_maps(entry).get(SHARE_KEY_PREFIX) or {}
        slots_per_day = len(central.get(reference.reference_edge_id) or []) or 96
        weight_source = reference.weighting
        if reference.weighting == "measured_two_way_total_per_slot" and flows:
            if epoch is None:
                raise ValueError(
                    "measured anchor weighting needs the measurement epoch")
            weights = period_slot_weights(
                flows, reference.edge_ids, epoch=epoch,
                interval_minutes=interval_minutes,
                period_start=reference.period_start,
                period_end_exclusive=reference.period_end_exclusive,
                slots_per_day=slots_per_day)
            if sum(weights) <= 0:
                # The declared period is not covered by the measurement file we
                # have.  Falling back to an unweighted period mean still honours
                # the local evidence; pretending it was flow-weighted would not.
                weights = [1.0] * slots_per_day
                weight_source = "uniform_fallback_no_measured_period"
        else:
            weights = [1.0] * slots_per_day
            if reference.weighting == "measured_two_way_total_per_slot":
                weight_source = "uniform_fallback_no_measurements"
        entry, outcome = anchor_split_entry(
            entry, reference, weights, sensor_id=sensor_id,
            weight_source=weight_source, clamp=clamp)
        anchored[sensor_id] = entry
        outcomes.append(outcome)
    return anchored, outcomes


def load_measurements(path: Path) -> tuple[dict[str, list[float | None]], datetime, int]:
    """(flows, epoch, interval_minutes) from a flows artifact."""
    with open(path) as handle:
        data = json.load(handle)
    epoch = datetime.fromisoformat(str(data["epoch"]))
    return data["flows"], epoch, int(data.get("interval_minutes", 15))
