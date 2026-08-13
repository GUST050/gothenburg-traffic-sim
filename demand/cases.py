"""Resolve a v2 demand case into demand-build inputs (plan step 5).

The old pipeline addressed direction assumptions by a *q-key* —
``"edge_shares"``, ``"edge_shares_q10"``, ``"edge_shares_q90"`` — a closed
vocabulary of three, baked into `build_sumo_demand`'s variant list, the
scenario contract's seed mapping, the cache identity and the ranking
reducers. Adding a fourth assumption meant editing all of them, and the three
that existed were welded to seed numbers.

A v2 demand case is just a complete set of per-edge, per-slot shares with an
identity. So the seam this module provides is small on purpose:

* :func:`shares_for_case` turns a case id into exactly the mapping
  `demand.intake.build_targets` already accepts, and
* :func:`demand_case_cache_key` binds a built artifact to everything that can
  change what it means.

Nothing here changes the legacy path. `build_targets` without ``shares``
behaves exactly as before, so a central-only v2 build and a legacy q50 build
can be compared directly — which is the plan's acceptance criterion for this
step.

**Laziness.** The plan requires that a multi-month search must not build
every scenario's route files before the shortlist exists. That is a property
of *when* the caller resolves a case, not of the resolution itself, so this
module exposes :class:`LazyCaseDemand` — a handle that knows its cache key
and its shares but does not touch the filesystem until ``resolve()`` is
called. A search can therefore enumerate thousands of candidate cases,
compute their keys, discard all but a shortlist, and only then pay for
route generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dirsplit.schema import SLOT_COUNT, content_digest

#: Case id used for the central (point-estimate) build. Deliberately not
#: "q50": the central profile is whatever model won the step 2 tournament,
#: which on the tracked data is `constant_5050`, not a quantile of anything.
CENTRAL_CASE_ID = "dc_central"


class CaseError(ValueError):
    """Raised when a case cannot be resolved into demand inputs."""


def shares_for_case(ensemble: Mapping[str, Any], case_id: str
                    ) -> dict[str, list[float]]:
    """Per-edge, per-slot shares for one demand case.

    Accepts the loaded `direction_ensemble_v2.json` payload. The central case
    is served from ``central_profile``; any other id is looked up among the
    probabilistic demand cases and then the stress cases, in that order.
    """
    if case_id == CENTRAL_CASE_ID:
        profiles = ensemble.get("central_profile") or {}
        merged: dict[str, list[float]] = {}
        for profile in profiles.values():
            merged.update({k: list(v) for k, v in
                           (profile.get("shares") or {}).items()})
        if not merged:
            raise CaseError("ensemble carries no central profile")
        return merged

    for bucket in ("demand_cases", "stress_cases"):
        for case in ensemble.get(bucket) or []:
            if case.get("case_id") == case_id:
                shares = {k: list(v) for k, v in
                          (case.get("edge_shares") or {}).items()}
                if not shares:
                    raise CaseError(f"case {case_id} carries no edge_shares")
                _check_slots(shares, case_id)
                return shares
    raise CaseError(f"unknown demand case {case_id!r}")


def _check_slots(shares: Mapping[str, Sequence[float]], case_id: str) -> None:
    for edge_id, series in shares.items():
        if len(series) != SLOT_COUNT:
            raise CaseError(
                f"case {case_id} edge {edge_id} has {len(series)} slots, "
                f"expected {SLOT_COUNT}"
            )


def case_weight(ensemble: Mapping[str, Any], case_id: str) -> float | None:
    """Probability weight, or None for the central case and stress cases.

    Returning None rather than 0.0 is deliberate: a stress case has no
    probability mass, and a zero would let a weighted mean silently include
    it at no cost.
    """
    if case_id == CENTRAL_CASE_ID:
        return None
    for case in ensemble.get("demand_cases") or []:
        if case.get("case_id") == case_id:
            return case.get("weight")
    for case in ensemble.get("stress_cases") or []:
        if case.get("case_id") == case_id:
            return None
    raise CaseError(f"unknown demand case {case_id!r}")


def is_stress_case(ensemble: Mapping[str, Any], case_id: str) -> bool:
    return any(case.get("case_id") == case_id
               for case in ensemble.get("stress_cases") or [])


def demand_case_cache_key(
    *,
    date: str,
    source: str,
    case_id: str,
    model_digest: str,
    calibration_digest: str,
    edge_pairs: Sequence[Sequence[str]],
    route_source_digest: str,
    window: str = "00:00-24:00",
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Content key binding everything that can change what a build means.

    The plan names the required components explicitly — date, case, model,
    calibration, edge pair and route source. Each is here because changing it
    changes the artifact:

    * ``date``/``source``/``window`` — which counts were calibrated against;
    * ``case_id`` — which direction world;
    * ``model_digest`` — which central model produced the profile the case
      perturbs (the step 2 winner can change);
    * ``calibration_digest`` — the interval calibration, which moves bounds;
    * ``edge_pairs`` — re-snapping a sensor changes which edges exist, and a
      pair set that silently differs would reuse geometry built for another
      network. Sorted, so registry insertion order cannot change the key;
    * ``route_source_digest`` — the candidate pool and router identity.
    """
    payload = {
        "date": date,
        "source": source,
        "window": window,
        "case_id": case_id,
        "model_digest": model_digest,
        "calibration_digest": calibration_digest,
        "edge_pairs": sorted(tuple(sorted(pair)) for pair in edge_pairs),
        "route_source_digest": route_source_digest,
        "extra": dict(extra or {}),
    }
    return content_digest(payload)


@dataclass
class LazyCaseDemand:
    """A demand case that has not been built yet.

    Constructing one is free: it computes an identity and holds a callable.
    Nothing reaches the filesystem until :meth:`resolve` runs. That is what
    lets a multi-month search enumerate every candidate schedule, rank them
    on a central-only screen, and pay for scenario route files only for the
    shortlist.
    """

    case_id: str
    cache_key: str
    shares: Mapping[str, Sequence[float]]
    builder: Callable[["LazyCaseDemand"], Path]
    weight: float | None = None
    is_stress: bool = False
    _resolved: Path | None = field(default=None, init=False, repr=False)

    @property
    def is_built(self) -> bool:
        return self._resolved is not None

    def resolve(self) -> Path:
        """Build (or return the already-built) route artifact."""
        if self._resolved is None:
            self._resolved = self.builder(self)
        return self._resolved

    def targets(self, flows, sensor_edges, qi_start, n_intervals):
        """Level-1 targets for this case, via the unchanged intake path."""
        from demand.intake import build_targets

        return build_targets(flows, sensor_edges, qi_start, n_intervals,
                             shares={k: list(v)
                                     for k, v in self.shares.items()})


def plan_cases(
    ensemble: Mapping[str, Any],
    *,
    date: str,
    source: str,
    model_digest: str,
    calibration_digest: str,
    edge_pairs: Sequence[Sequence[str]],
    route_source_digest: str,
    builder: Callable[[LazyCaseDemand], Path],
    include_stress: bool = False,
    window: str = "00:00-24:00",
) -> list[LazyCaseDemand]:
    """Enumerate every case as a LAZY handle, central case first.

    ``include_stress`` is False by default. Stress cases carry no probability
    weight, so including them in a run that computes a weighted expectation
    would either corrupt the expectation or require every downstream reducer
    to remember to exclude them. Opting in makes the robustness arm explicit.
    """
    case_ids = [CENTRAL_CASE_ID]
    case_ids += [c["case_id"] for c in ensemble.get("demand_cases") or []]
    if include_stress:
        case_ids += [c["case_id"] for c in ensemble.get("stress_cases") or []]

    handles: list[LazyCaseDemand] = []
    for case_id in case_ids:
        try:
            shares = shares_for_case(ensemble, case_id)
        except CaseError:
            continue
        handles.append(LazyCaseDemand(
            case_id=case_id,
            cache_key=demand_case_cache_key(
                date=date, source=source, case_id=case_id,
                model_digest=model_digest,
                calibration_digest=calibration_digest,
                edge_pairs=edge_pairs,
                route_source_digest=route_source_digest,
                window=window,
            ),
            shares=shares,
            builder=builder,
            weight=case_weight(ensemble, case_id),
            is_stress=is_stress_case(ensemble, case_id),
        ))
    return handles


def weighted_reduce(values: Mapping[str, float],
                    handles: Sequence[LazyCaseDemand]) -> float:
    """Weighted expectation over PROBABILISTIC cases only.

    Refuses to include a stress case or the central case, both of which have
    no probability mass. This is the reducer-side half of plan invariant 5:
    the schema stops a stress case from carrying a weight, and this stops a
    caller from supplying one anyway.
    """
    total = 0.0
    mass = 0.0
    for handle in handles:
        if handle.is_stress or handle.weight is None:
            continue
        if handle.case_id not in values:
            raise CaseError(
                f"no value for probabilistic case {handle.case_id}; a "
                "weighted expectation over an incomplete set would silently "
                "re-normalise and misreport the mean"
            )
        total += handle.weight * float(values[handle.case_id])
        mass += handle.weight
    if mass <= 0:
        raise CaseError("no probabilistic cases to reduce over")
    return total / mass


__all__ = [
    "CENTRAL_CASE_ID", "CaseError", "shares_for_case", "case_weight",
    "is_stress_case", "demand_case_cache_key", "LazyCaseDemand",
    "plan_cases", "weighted_reduce",
]
