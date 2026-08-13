"""The applicability evidence profile (plan step 3).

`dirsplit/coverage.py` answers exactly one question — is this edge's static
feature vector inside the training cloud — and turns it into a pass/fail at
the 90th percentile. That is one axis of several, and it is not the axis that
has been failing.

The plan lists what the single kNN pass cannot see, and each becomes its own
graded dimension here:

``measurement_level``
    Are both directions measured, one, or neither? A two-way total is not a
    directional measurement, so a Gothenburg sensor delivering "Total" sits at
    ``transfer_prior`` no matter how good the model is. This is the dimension
    that makes the local D-factor at sensor 107 visible as what it is: a real
    measurement outranking any transferred prior.

``static_domain``
    The old kNN check, kept but demoted to one axis among several.

``temporal_support``
    Was this hour and day type ever observed in training? The deployed model
    trained on weekdays 06-20 and predicted all 24 hours with
    ``is_weekend=0``, so every night hour and every weekend prediction was
    silent extrapolation that no dimension of the old report could show.

``feature_compatibility``
    Do the input features mean the same thing in training and at the target?
    The profile features are computed from the SUM of both directions, which
    several Gothenburg sensors do not have — so a column with the same name
    can carry a different quantity on each side.

``effective_sample_size``
    How many independent day blocks actually support this cell, after
    weighting. A cell backed by fifteen hourly rows from one station-day has
    one day of evidence, not fifteen.

``calibration_support``
    Did the held-out calibration measure this group at all, and did it hold?

``local_crosscheck``
    Where a genuine local two-way measurement exists, how does the model
    compare against it.

The grading rule is the plan's: weak evidence WIDENS uncertainty and weakens
claims; it never bans a road. Only a genuine input error — an impossible edge
pairing, a missing feature, an incoherent registry — fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .dataset_schema import DirectionRow
from .schema import EVIDENCE_GRADES, Applicability

SCHEMA_VERSION = "dirsplit_evidence_profile_v1"

#: Independent day blocks below which a cell is thin.
THIN_BLOCKS = 5

#: Independent day blocks below which a cell is effectively unsupported.
SPARSE_BLOCKS = 2

#: kNN percentile above which the static domain is extrapolation. Matches the
#: existing `coverage.py` threshold so the axis is comparable to the old
#: report rather than silently re-tuned.
STATIC_DOMAIN_PERCENTILE = 90.0


def grade_static_domain(percentile: float | None) -> str:
    if percentile is None:
        return "unavailable"
    if percentile > STATIC_DOMAIN_PERCENTILE:
        return "extrapolation"
    if percentile > 75.0:
        return "limited"
    return "good"


def grade_temporal_support(hour: int, is_weekend: bool,
                           support: Mapping[str, Any]) -> str:
    """Was this exact (hour, day-type) cell observed in training?"""
    key = "weekend_hours" if is_weekend else "weekday_hours"
    observed = set(support.get(key) or [])
    if not observed:
        return "extrapolation"
    if hour in observed:
        return "good"
    # Adjacent hours are interpolation rather than pure extrapolation.
    if (hour - 1) in observed or (hour + 1) in observed:
        return "limited"
    return "extrapolation"


def grade_effective_sample(n_blocks: float) -> str:
    if n_blocks <= 0:
        return "unavailable"
    if n_blocks < SPARSE_BLOCKS:
        return "extrapolation"
    if n_blocks < THIN_BLOCKS:
        return "limited"
    return "good"


def grade_feature_compatibility(*, profile_from_total: bool,
                                target_has_total: bool,
                                missing_features: Sequence[str] = ()) -> str:
    """Do the features mean the same thing on both sides?

    A missing feature is an input error and fails closed. A profile feature
    derived from a two-way total, used at a target where only one direction
    is measured, is a semantic mismatch — the column name matches but the
    quantity does not — so it grades ``limited`` rather than ``good``.
    """
    if missing_features:
        return "unavailable"
    if profile_from_total and not target_has_total:
        return "limited"
    return "good"


def grade_calibration_support(coverage_report: Mapping[str, Any] | None,
                              group: str = "all") -> str:
    """Grade how well held-out calibration supports this edge.

    A MISSING calibration report grades ``extrapolation``, not
    ``unavailable``. The distinction is the plan's rule in miniature:
    ``unavailable`` is reserved for a genuine input error and fails closed,
    while "nobody has calibrated this yet" is thin evidence, which must widen
    the claim rather than remove the road from the search. Grading an
    uncalibrated edge as an input error would ban every road before the first
    calibration run — exactly the behaviour this profile replaces.
    """
    if not coverage_report:
        return "extrapolation"
    for entry in coverage_report.get("groups") or []:
        if entry.get("group") != group:
            continue
        if entry.get("n_blocks", 0) < SPARSE_BLOCKS:
            return "extrapolation"
        if entry.get("is_calibrated"):
            return "good" if entry.get("n_blocks", 0) >= THIN_BLOCKS \
                else "limited"
        return "limited"
    return "extrapolation"


@dataclass(frozen=True)
class LocalCrosscheck:
    """A genuine local directional measurement, where one exists.

    The city's own trafikmängder catalogue publishes a D-factor for sensor
    107 (N 3400 / S 3100 of 6500 in 2025 = 52/48; exactly 50/50 in 2023-24).
    That is a MEASUREMENT, and by the project's estimation hierarchy it
    outranks any transferred prior at that edge — which matters because 107
    is the only station in the registry whose split touches a level-1 target
    at all.
    """

    edge_id: str
    measured_share: float
    source: str
    year: str | None = None

    def compare(self, predicted: float) -> dict[str, Any]:
        error = abs(predicted - self.measured_share)
        baseline = abs(0.5 - self.measured_share)
        return {
            "edge_id": self.edge_id,
            "measured_share": self.measured_share,
            "predicted_share": round(float(predicted), 6),
            "abs_error": round(error, 6),
            "abs_error_5050": round(baseline, 6),
            "beats_5050": bool(error < baseline),
            "source": self.source,
            "year": self.year,
        }

    def grade(self, predicted: float) -> str:
        result = self.compare(predicted)
        return "good" if result["beats_5050"] else "limited"


def build_profile(
    edge_id: str,
    *,
    measurement_level: str,
    static_percentile: float | None,
    hour: int,
    is_weekend: bool,
    temporal_support_report: Mapping[str, Any],
    n_effective_blocks: float,
    coverage_report: Mapping[str, Any] | None = None,
    profile_from_total: bool = True,
    target_has_total: bool = True,
    missing_features: Sequence[str] = (),
    crosscheck: LocalCrosscheck | None = None,
    predicted_share: float | None = None,
    detail: Mapping[str, Any] | None = None,
) -> Applicability:
    """Assemble one edge's evidence profile from the measured inputs."""
    local = None
    if crosscheck is not None and predicted_share is not None:
        local = crosscheck.grade(predicted_share)

    return Applicability(
        edge_id=edge_id,
        measurement_level=measurement_level,
        static_domain=grade_static_domain(static_percentile),
        temporal_support=grade_temporal_support(hour, is_weekend,
                                                temporal_support_report),
        feature_compatibility=grade_feature_compatibility(
            profile_from_total=profile_from_total,
            target_has_total=target_has_total,
            missing_features=missing_features,
        ),
        effective_sample_size=float(n_effective_blocks),
        calibration_support=grade_calibration_support(coverage_report),
        local_crosscheck=local,
        detail={
            "static_percentile": static_percentile,
            "hour": hour,
            "is_weekend": bool(is_weekend),
            "effective_sample_grade": grade_effective_sample(n_effective_blocks),
            "crosscheck": (crosscheck.compare(predicted_share)
                           if crosscheck is not None
                           and predicted_share is not None else None),
            **dict(detail or {}),
        },
    )


def summarise_profiles(profiles: Iterable[Applicability]) -> dict[str, Any]:
    profiles = list(profiles)
    by_grade: dict[str, int] = {grade: 0 for grade in EVIDENCE_GRADES}
    by_claim: dict[str, int] = {}
    for profile in profiles:
        by_grade[profile.grade()] = by_grade.get(profile.grade(), 0) + 1
        claim = profile.claim_strength()
        by_claim[claim] = by_claim.get(claim, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "n_edges": len(profiles),
        "by_grade": by_grade,
        "by_claim_strength": dict(sorted(by_claim.items())),
        "blocked": [p.edge_id for p in profiles if p.blocks_publication],
        "note": (
            "A weak grade widens uncertainty and weakens the claim. It never "
            "removes a road from the search: only an input error "
            "(measurement_level/feature semantics unavailable) fails closed."
        ),
    }


def write_report(profiles: Iterable[Applicability], path: Path) -> dict[str, Any]:
    profiles = list(profiles)
    payload = {
        **summarise_profiles(profiles),
        "edges": {
            p.edge_id: {
                "measurement_level": p.measurement_level,
                "static_domain": p.static_domain,
                "temporal_support": p.temporal_support,
                "feature_compatibility": p.feature_compatibility,
                "effective_sample_size": p.effective_sample_size,
                "calibration_support": p.calibration_support,
                "local_crosscheck": p.local_crosscheck,
                "grade": p.grade(),
                "claim_strength": p.claim_strength(),
                "detail": dict(p.detail),
            }
            for p in profiles
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return payload


__all__ = [
    "SCHEMA_VERSION", "THIN_BLOCKS", "SPARSE_BLOCKS",
    "STATIC_DOMAIN_PERCENTILE", "LocalCrosscheck", "build_profile",
    "grade_static_domain", "grade_temporal_support", "grade_effective_sample",
    "grade_feature_compatibility", "grade_calibration_support",
    "summarise_profiles", "write_report",
]
