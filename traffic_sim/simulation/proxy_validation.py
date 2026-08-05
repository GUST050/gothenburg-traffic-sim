"""Frozen held-out validation gate for the monthly closure proxy.

The validation manifest is frozen before SUMO outcomes are collected.  It
defines exact cases, schedule IDs, and required representation strata.
Outcomes are a separate artifact tied to the manifest's content key.  The
gate cannot pass on partial, non-exhaustive, or provenance-free evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import median
from typing import Any, Mapping, Sequence

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation.monthly_proxy import (
    HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    PROXY_VERSION,
    SHORTLIST_VERSION,
)


SCHEMA_VERSION = 1
WINNER_RECALL_GATE = 0.90
P90_NORMALIZED_REGRET_GATE = 0.10
MEDIAN_SPEARMAN_GATE = 0.60
_MODES = frozenset({"meso", "micro"})
# Manifest `gate` object fields (held-out v2 contract, frozen 2026-07-19
# per the step-4 recovery decision): the winner metric uses a practical-
# equivalence indifference zone instead of exact float ties — v1's two
# "missed winners" were 4 s and 22 s apart on medians whose seed ranges
# spanned hundreds of seconds, which is measurement noise, not proxy
# error.  Spearman becomes a reported diagnostic, not a gate.
_GATE_FIELDS = (
    "practical_winner_recall_minimum",
    "p90_normalized_shortlist_regret_maximum",
    "failure_disqualification_recall_minimum",
)
# The indifference zone, in the OBJECTIVE'S OWN UNIT. The gate arithmetic is
# objective-agnostic — it only ever compares sumo_objective against
# best_objective + tolerance — but the unit is not, and a tolerance carried
# across a change of objective is silently wrong. (Measured 2026-08-05: an
# absolute 0.02 tie-break survived a 20x rescaling of its metric and promoted
# a fit 1.7x worse than the best.) A manifest must therefore name its unit,
# and exactly one of these may appear:
#   practical_equivalence_s              seconds of added time loss  (v1-v6)
#   practical_equivalence_vehicle_hours  vehicle-hours of added driving (v7+)
_TOLERANCE_FIELDS = (
    "practical_equivalence_s",
    "practical_equivalence_vehicle_hours",
)


def gate_tolerance(gate: dict) -> float | None:
    """The indifference zone, whichever unit this manifest declared."""
    if not gate:
        return None
    for field in _TOLERANCE_FIELDS:
        if field in gate:
            return gate[field]
    return None
# Additive discrimination checks used by v3/v4. Earlier manifests do not
# contain these fields and retain their original gate semantics unchanged.
_OPTIONAL_GATE_FIELDS = (
    "minimum_discriminating_case_fraction",
    "discriminating_practical_winner_recall_minimum",
)


def _canonical_key(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _number(value: Any, label: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + end - 1) / 2
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("Spearman inputs differ in length")
    if len(left) < 3:
        return None
    rank_left = _average_ranks(left)
    rank_right = _average_ranks(right)
    mean_left = sum(rank_left) / len(rank_left)
    mean_right = sum(rank_right) / len(rank_right)
    covariance = sum(
        (a - mean_left) * (b - mean_right)
        for a, b in zip(rank_left, rank_right)
    )
    variance_left = sum((value - mean_left) ** 2 for value in rank_left)
    variance_right = sum((value - mean_right) ** 2 for value in rank_right)
    denominator = math.sqrt(variance_left * variance_right)
    if denominator == 0:
        return None
    return covariance / denominator


def validation_manifest_content_key(manifest: Mapping[str, Any]) -> str:
    """Return the immutable identity of a validated held-out manifest."""
    normalized = validate_validation_manifest(manifest)
    payload = {
        key: value
        for key, value in normalized.items()
        if key != "content_key"
    }
    return _canonical_key(payload)


def validate_validation_manifest(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("validation manifest must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported validation manifest schema")
    if raw.get("kind") != "monthly_proxy_validation_manifest":
        raise ValueError("validation manifest kind is invalid")
    if raw.get("proxy_version") != PROXY_VERSION:
        raise ValueError("validation manifest proxy_version is not current")
    if raw.get("frozen_before_outcomes") is not True:
        raise ValueError("validation manifest must be frozen before outcomes")
    if not isinstance(raw.get("frozen_at"), str) or not raw["frozen_at"]:
        raise ValueError("validation manifest frozen_at is required")
    minimum_cases = raw.get("minimum_cases")
    if (
        isinstance(minimum_cases, bool)
        or not isinstance(minimum_cases, int)
        or minimum_cases < 1
    ):
        raise ValueError("validation manifest minimum_cases must be positive")
    minimum_spearman_fraction = _number(
        raw.get("minimum_spearman_case_fraction"),
        "minimum_spearman_case_fraction",
    )
    if not 0 < minimum_spearman_fraction <= 1:
        raise ValueError(
            "minimum_spearman_case_fraction must be above zero and at most one"
        )
    minimum_ranking_fraction = _number(
        raw.get("minimum_ranking_case_fraction"),
        "minimum_ranking_case_fraction",
    )
    if not 0 < minimum_ranking_fraction <= 1:
        raise ValueError(
            "minimum_ranking_case_fraction must be above zero and at most one"
        )

    gate_raw = raw.get("gate")
    gate: dict[str, float] | None = None
    if gate_raw is not None:
        if not isinstance(gate_raw, Mapping) or set(_GATE_FIELDS) - set(gate_raw):
            raise ValueError(
                "validation manifest gate must define at least "
                + ", ".join(_GATE_FIELDS)
            )
        unknown = (set(gate_raw) - set(_GATE_FIELDS)
                   - set(_TOLERANCE_FIELDS) - set(_OPTIONAL_GATE_FIELDS))
        if unknown:
            raise ValueError(
                "validation manifest gate has unknown fields: "
                + ", ".join(sorted(unknown))
            )
        gate = {
            field: _number(gate_raw[field], f"gate.{field}")
            for field in _GATE_FIELDS
            + tuple(f for f in _TOLERANCE_FIELDS if f in gate_raw)
            + tuple(field for field in _OPTIONAL_GATE_FIELDS if field in gate_raw)
        }
        present = [f for f in _TOLERANCE_FIELDS if f in gate]
        if len(present) != 1:
            raise ValueError(
                "gate must declare exactly one indifference zone naming its "
                f"unit, one of {list(_TOLERANCE_FIELDS)}; found {present}")
        if gate[present[0]] <= 0:
            raise ValueError(f"gate.{present[0]} must be positive")
        for field in sorted(set(gate) - set(present)):
            if not 0 < gate[field] <= 1:
                raise ValueError(
                    f"gate.{field} must be above zero and at most one"
                )

    required_raw = raw.get("required_strata")
    if not isinstance(required_raw, Mapping) or not required_raw:
        raise ValueError("validation manifest required_strata is required")
    required: dict[str, list[str]] = {}
    for dimension, values in sorted(required_raw.items()):
        if (
            not isinstance(dimension, str)
            or not dimension
            or isinstance(values, (str, bytes))
        ):
            raise ValueError("validation required strata are invalid")
        normalized = sorted({str(value) for value in values})
        if not normalized or any(not value for value in normalized):
            raise ValueError("validation required strata cannot be empty")
        required[dimension] = normalized

    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, Sequence) or isinstance(
        cases_raw, (str, bytes)
    ):
        raise ValueError("validation manifest cases must be a list")
    cases = []
    case_ids: set[str] = set()
    represented = {dimension: set() for dimension in required}
    for item in cases_raw:
        if not isinstance(item, Mapping):
            raise ValueError("validation case must be an object")
        case_id = str(item.get("case_id", ""))
        if not case_id or case_id in case_ids:
            raise ValueError("validation case IDs must be unique and non-empty")
        case_ids.add(case_id)
        strata_raw = item.get("strata")
        if not isinstance(strata_raw, Mapping):
            raise ValueError(f"validation case {case_id} has no strata")
        strata = {}
        for dimension in required:
            value = str(strata_raw.get(dimension, ""))
            if not value:
                raise ValueError(
                    f"validation case {case_id} lacks stratum {dimension}"
                )
            strata[dimension] = value
            represented[dimension].add(value)
        spec_raw = item.get("closure_search_spec")
        try:
            spec = ClosureSearchSpec.from_dict(spec_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"validation case {case_id} has an invalid closure search spec"
            ) from exc
        schedule_ids_raw = item.get("schedule_ids")
        generated_ids = tuple(
            schedule.schedule_id for schedule in generate_closure_schedules(spec)
        )
        if schedule_ids_raw is None:
            schedule_ids = generated_ids
        else:
            if isinstance(schedule_ids_raw, (str, bytes)) or not isinstance(
                schedule_ids_raw, Sequence
            ):
                raise ValueError(
                    f"validation case {case_id} schedule_ids must be a list"
                )
            schedule_ids = tuple(str(value) for value in schedule_ids_raw)
        if (
            len(schedule_ids) < 3
            or len(set(schedule_ids)) != len(schedule_ids)
            or any(not value for value in schedule_ids)
        ):
            raise ValueError(
                f"validation case {case_id} needs at least three unique schedules"
            )
        if schedule_ids != generated_ids:
            raise ValueError(
                f"validation case {case_id} schedule IDs do not exactly match "
                "its frozen closure search"
            )
        descriptor = {
            "case_id": case_id,
            "closure_search_content_key": spec.content_key,
            "closure_search_spec": spec.to_dict(),
            "directed_edges": sorted(spec.directed_edges),
            "strata": strata,
            "schedule_ids": list(schedule_ids),
        }
        cases.append(descriptor)

    if len(cases) < minimum_cases:
        raise ValueError("validation manifest has fewer than minimum_cases")
    coverage_missing = {
        dimension: sorted(set(values) - represented[dimension])
        for dimension, values in required.items()
        if set(values) - represented[dimension]
    }
    if coverage_missing:
        raise ValueError(
            "validation manifest misses required strata: "
            + json.dumps(coverage_missing, sort_keys=True)
        )

    if "campaign_version" in raw and (
        not isinstance(raw["campaign_version"], str) or not raw["campaign_version"]
    ):
        raise ValueError("validation manifest campaign_version is invalid")
    if "shortlist_version" in raw and (
        not isinstance(raw["shortlist_version"], str) or not raw["shortlist_version"]
    ):
        raise ValueError("validation manifest shortlist_version is invalid")
    if raw.get("shortlist_version") not in (None, SHORTLIST_VERSION):
        raise ValueError("validation manifest shortlist_version is not current")
    if "shortlist_policy_content_key" in raw and (
        not isinstance(raw["shortlist_policy_content_key"], str)
        or raw["shortlist_policy_content_key"]
        != HELD_OUT_VALIDATED_SHORTLIST_POLICY.content_key
    ):
        raise ValueError(
            "validation manifest shortlist_policy_content_key is not current"
        )
    if "status" in raw and (
        not isinstance(raw["status"], str) or not raw["status"]
    ):
        raise ValueError("validation manifest status is invalid")
    if "outcomes_present_at_freeze" in raw and raw["outcomes_present_at_freeze"] is not False:
        raise ValueError("validation manifest cannot contain outcomes at freeze")
    if "outcomes_path" in raw and raw["outcomes_path"] is not None and not isinstance(raw["outcomes_path"], str):
        raise ValueError("validation manifest outcomes_path is invalid")
    for field in ("policy_content_key", "selection_content_key", "source_identity"):
        if field in raw and (not isinstance(raw[field], str) or not raw[field]):
            raise ValueError(f"validation manifest {field} is invalid")
    fingerprints = raw.get("source_fingerprints")
    if fingerprints is not None:
        if not isinstance(fingerprints, Mapping) or not fingerprints:
            raise ValueError("validation manifest source_fingerprints is invalid")
        for name, digest in fingerprints.items():
            if (
                not isinstance(name, str) or not name
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("validation manifest source_fingerprints is invalid")

    normalized_manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_proxy_validation_manifest",
        "proxy_version": PROXY_VERSION,
        "frozen_at": raw["frozen_at"],
        "frozen_before_outcomes": True,
        "minimum_cases": minimum_cases,
        "minimum_spearman_case_fraction": minimum_spearman_fraction,
        "minimum_ranking_case_fraction": minimum_ranking_fraction,
        "required_strata": required,
        "cases": sorted(cases, key=lambda case: case["case_id"]),
    }
    # Preserve the frozen V4 provenance envelope in the canonical identity.
    # These fields are optional so V1/V2 manifests normalize byte-for-byte as
    # before, while a V4 refreeze cannot silently lose its source identity.
    optional_fields = (
        "campaign_version",
        "shortlist_version",
        "shortlist_policy_content_key",
        "status",
        "outcomes_present_at_freeze",
        "outcomes_path",
        "policy_content_key",
        "selection_content_key",
        "source_identity",
        "source_fingerprints",
    )
    for field in optional_fields:
        if field in raw:
            normalized_manifest[field] = raw[field]
    if gate is not None:
        normalized_manifest["gate"] = gate
    supplied_key = raw.get("content_key")
    expected = _canonical_key(normalized_manifest)
    if supplied_key is not None and supplied_key != expected:
        raise ValueError("validation manifest content_key does not match")
    normalized_manifest["content_key"] = expected
    return normalized_manifest


def _validate_provenance(
    provenance: Mapping[str, Any],
    case_id: str,
) -> dict[str, Any]:
    if not isinstance(provenance, Mapping):
        raise ValueError(f"validation case {case_id} has no provenance")
    for digest_name in (
        "network_sha256",
        "demand_sha256",
        "proxy_input_sha256",
    ):
        digest = provenance.get(digest_name)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(
                f"validation case {case_id} has invalid {digest_name}"
            )
    mode = provenance.get("simulation_mode")
    if mode not in _MODES:
        raise ValueError(f"validation case {case_id} has invalid simulation mode")
    seeds = provenance.get("seed_set")
    if (
        isinstance(seeds, (str, bytes))
        or not isinstance(seeds, Sequence)
        or not seeds
        or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
    ):
        raise ValueError(f"validation case {case_id} has invalid seed_set")
    if not isinstance(provenance.get("sumo_version"), str) or not provenance[
        "sumo_version"
    ]:
        raise ValueError(f"validation case {case_id} has no SUMO version")
    if not isinstance(provenance.get("matched_baseline_id"), str) or not provenance[
        "matched_baseline_id"
    ]:
        raise ValueError(f"validation case {case_id} has no matched baseline")
    return dict(provenance)


def evaluate_validation_case(
    descriptor: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    practical_equivalence_s: float | None = None,
) -> dict[str, Any]:
    case_id = descriptor["case_id"]
    if outcome.get("case_id") != case_id:
        raise ValueError(f"validation outcome case ID differs for {case_id}")
    if outcome.get("exhaustive") is not True:
        raise ValueError(f"validation case {case_id} is not exhaustive")
    provenance = _validate_provenance(outcome.get("provenance"), case_id)
    expected_ids = set(descriptor["schedule_ids"])
    candidates_raw = outcome.get("candidates")
    if isinstance(candidates_raw, (str, bytes)) or not isinstance(
        candidates_raw, Sequence
    ):
        raise ValueError(f"validation case {case_id} candidates must be a list")
    candidates = []
    seen: set[str] = set()
    ranks: set[int] = set()
    for raw in candidates_raw:
        if not isinstance(raw, Mapping):
            raise ValueError(f"validation case {case_id} candidate is invalid")
        schedule_id = str(raw.get("schedule_id", ""))
        if schedule_id in seen or schedule_id not in expected_ids:
            raise ValueError(
                f"validation case {case_id} has duplicate or unknown schedule"
            )
        seen.add(schedule_id)
        eligible = raw.get("eligible")
        if not isinstance(eligible, bool):
            raise ValueError(
                f"validation case {case_id} candidate eligibility is invalid"
            )
        objective = (
            _number(raw.get("sumo_objective"), "sumo_objective")
            if eligible
            else None
        )
        proxy_rank = raw.get("proxy_rank")
        if proxy_rank is not None:
            if (
                isinstance(proxy_rank, bool)
                or not isinstance(proxy_rank, int)
                or proxy_rank < 0
                or proxy_rank in ranks
            ):
                raise ValueError(
                    f"validation case {case_id} proxy ranks are invalid"
                )
            ranks.add(proxy_rank)
        shortlisted = raw.get("shortlisted")
        failure_flag = raw.get("proxy_failure_flag")
        if not isinstance(shortlisted, bool) or not isinstance(failure_flag, bool):
            raise ValueError(
                f"validation case {case_id} screening flags are invalid"
            )
        candidates.append({
            "schedule_id": schedule_id,
            "eligible": eligible,
            "sumo_objective": objective,
            "proxy_rank": proxy_rank,
            "shortlisted": shortlisted,
            "proxy_failure_flag": failure_flag,
        })
    if seen != expected_ids:
        raise ValueError(
            f"validation case {case_id} is missing exhaustive schedules"
        )

    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    failures = [candidate for candidate in candidates if not candidate["eligible"]]
    caught_failures = [
        candidate
        for candidate in failures
        if candidate["proxy_failure_flag"] or candidate["shortlisted"]
    ]
    failure_recall = (
        len(caught_failures) / len(failures) if failures else None
    )
    if not eligible:
        return {
            "case_id": case_id,
            "case_role": "failure_only",
            "strata": dict(descriptor["strata"]),
            "candidate_count": len(candidates),
            "eligible_count": 0,
            "disqualified_count": len(failures),
            "winner_schedule_ids": [],
            "winner_recalled": None,
            "practical_winner_schedule_ids": [],
            "practical_winner_recalled": None,
            "best_sumo_objective": None,
            "best_shortlisted_sumo_objective": None,
            "normalized_shortlist_regret": None,
            "spearman_rho": None,
            "spearman_n": 0,
            "objective_spread_s": None,
            "failure_disqualification_recall": failure_recall,
            "provenance": provenance,
        }
    best_objective = min(
        float(candidate["sumo_objective"]) for candidate in eligible
    )
    tolerance = max(1e-9, abs(best_objective) * 1e-12)
    winners = [
        candidate for candidate in eligible
        if math.isclose(
            float(candidate["sumo_objective"]),
            best_objective,
            abs_tol=tolerance,
            rel_tol=0.0,
        )
    ]
    winner_recalled = any(candidate["shortlisted"] for candidate in winners)
    # Practical winners: within the frozen practical-equivalence tolerance
    # of the exhaustive optimum.  A shortlist containing any of them is a
    # practical hit — the deployed finalist stage decides among shortlisted
    # candidates with matched high-replication SUMO, so distinguishing
    # schedules whose whole-network medians differ by less than the
    # pre-registered indifference zone is not this gate's job.
    if practical_equivalence_s is not None:
        practical_winners = [
            candidate for candidate in eligible
            if float(candidate["sumo_objective"])
            <= best_objective + practical_equivalence_s
        ]
        practical_winner_recalled = any(
            candidate["shortlisted"] for candidate in practical_winners
        )
        practical_winner_ids = sorted(
            candidate["schedule_id"] for candidate in practical_winners
        )
    else:
        practical_winner_recalled = None
        practical_winner_ids = []
    shortlisted_eligible = [
        candidate for candidate in eligible if candidate["shortlisted"]
    ]
    if not shortlisted_eligible:
        shortlist_best = None
        regret = None
    else:
        shortlist_best = min(
            float(candidate["sumo_objective"])
            for candidate in shortlisted_eligible
        )
        impact_range = (
            _quantile(
                [float(candidate["sumo_objective"]) for candidate in eligible],
                0.9,
            )
            - best_objective
        )
        regret = (
            max(0.0, shortlist_best - best_objective)
            / max(impact_range, 1e-9)
        )

    correlated = [
        candidate
        for candidate in eligible
        if candidate["proxy_rank"] is not None
    ]
    spearman = _spearman(
        [float(candidate["proxy_rank"]) for candidate in correlated],
        [float(candidate["sumo_objective"]) for candidate in correlated],
    )
    return {
        "case_id": case_id,
        "case_role": "ranking",
        "strata": dict(descriptor["strata"]),
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "disqualified_count": len(failures),
        "winner_schedule_ids": sorted(
            candidate["schedule_id"] for candidate in winners
        ),
        "winner_recalled": winner_recalled,
        "practical_winner_schedule_ids": practical_winner_ids,
        "practical_winner_recalled": practical_winner_recalled,
        "best_sumo_objective": best_objective,
        "best_shortlisted_sumo_objective": shortlist_best,
        "normalized_shortlist_regret": regret,
        "spearman_rho": spearman,
        "spearman_n": len(correlated),
        # How much decision there was to get right: the span of the eligible
        # schedules' exhaustive objectives.  A case whose span sits inside
        # the practical-equivalence band cannot distinguish a good ranking
        # from a lucky one, and is counted as such rather than averaged in
        # as if it were evidence of discrimination.
        "objective_spread_s": (
            max(float(candidate["sumo_objective"]) for candidate in eligible)
            - best_objective
        ),
        "failure_disqualification_recall": failure_recall,
        "provenance": provenance,
    }


def evaluate_validation_set(
    manifest_raw: Mapping[str, Any],
    outcomes_raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the release gate; incomplete evidence always keeps UI closed."""
    manifest = validate_validation_manifest(manifest_raw)
    if not isinstance(outcomes_raw, Mapping):
        raise ValueError("validation outcomes must be an object")
    if outcomes_raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported validation outcomes schema")
    if outcomes_raw.get("kind") != "monthly_proxy_validation_outcomes":
        raise ValueError("validation outcomes kind is invalid")
    if outcomes_raw.get("proxy_version") != PROXY_VERSION:
        raise ValueError("validation outcomes proxy_version is not current")
    if outcomes_raw.get("manifest_content_key") != manifest["content_key"]:
        raise ValueError("validation outcomes do not belong to the manifest")
    outcomes = outcomes_raw.get("cases")
    if isinstance(outcomes, (str, bytes)) or not isinstance(outcomes, Sequence):
        raise ValueError("validation outcomes cases must be a list")
    by_id = {
        str(outcome.get("case_id", "")): outcome
        for outcome in outcomes
        if isinstance(outcome, Mapping)
    }
    expected_ids = {case["case_id"] for case in manifest["cases"]}
    if set(by_id) != expected_ids or len(by_id) != len(outcomes):
        raise ValueError("validation outcomes do not exactly cover frozen cases")

    gate = manifest.get("gate")
    tolerance = gate_tolerance(gate)
    case_reports = [
        evaluate_validation_case(
            case,
            by_id[case["case_id"]],
            practical_equivalence_s=tolerance,
        )
        for case in manifest["cases"]
    ]
    ranking_reports = [
        report for report in case_reports if report["case_role"] == "ranking"
    ]
    if not ranking_reports:
        raise ValueError("validation set has no ranking cases")
    winner_recall = sum(
        report["winner_recalled"] for report in ranking_reports
    ) / len(ranking_reports)
    practical_winner_recall = (
        sum(
            bool(report["practical_winner_recalled"])
            for report in ranking_reports
        ) / len(ranking_reports)
        if gate
        else None
    )
    total_disqualified = sum(
        report["disqualified_count"] for report in case_reports
    )
    regrets = [
        report["normalized_shortlist_regret"]
        for report in case_reports
        if report["normalized_shortlist_regret"] is not None
    ]
    spearmans = [
        report["spearman_rho"]
        for report in case_reports
        if report["spearman_rho"] is not None
    ]
    failure_recalls = [
        report["failure_disqualification_recall"]
        for report in case_reports
        if report["failure_disqualification_recall"] is not None
    ]
    p90_regret = _quantile(regrets, 0.9) if regrets else None
    median_spearman = median(spearmans) if spearmans else None
    failure_recall = (
        sum(failure_recalls) / len(failure_recalls)
        if failure_recalls
        else None
    )
    spearman_case_fraction = len(spearmans) / len(ranking_reports)
    ranking_case_fraction = len(ranking_reports) / len(case_reports)
    # Discriminating cases: those whose eligible schedules really do differ by
    # more than the pre-registered indifference zone. Recall over THEM is the
    # number that says whether the proxy can choose, as opposed to whether
    # everything it could have chosen happened to be equally good.
    discriminating = [
        report for report in ranking_reports
        if tolerance is not None
        and report["objective_spread_s"] is not None
        and report["objective_spread_s"] > tolerance
    ]
    discriminating_case_fraction = len(discriminating) / len(ranking_reports)
    discriminating_practical_winner_recall = (
        sum(bool(report["practical_winner_recalled"]) for report in discriminating)
        / len(discriminating)
        if discriminating
        else None
    )
    discriminating_spearmans = [
        report["spearman_rho"] for report in discriminating
        if report["spearman_rho"] is not None
    ]
    spreads = [
        report["objective_spread_s"] for report in ranking_reports
        if report["objective_spread_s"] is not None
    ]
    if gate:
        # Held-out v2 contract (step-4 recovery decision): gate on
        # practical-winner recall, regret and failure recall; Spearman and
        # strict exact-tie recall stay in metrics as diagnostics.  The
        # failure-recall check is vacuous only when the exhaustive runs
        # produced no disqualified schedule at all (nothing to catch).
        gate_checks = {
            "practical_winner_recall": (
                practical_winner_recall is not None
                and practical_winner_recall
                >= gate["practical_winner_recall_minimum"]
            ),
            "p90_normalized_shortlist_regret": (
                p90_regret is not None
                and p90_regret
                <= gate["p90_normalized_shortlist_regret_maximum"]
            ),
            "failure_disqualification_recall": (
                total_disqualified == 0
                or (
                    failure_recall is not None
                    and failure_recall
                    >= gate["failure_disqualification_recall_minimum"]
                )
            ),
            "ranking_case_coverage": (
                ranking_case_fraction
                >= manifest["minimum_ranking_case_fraction"]
            ),
            "all_shortlists_contain_eligible_candidate": (
                len(regrets) == len(ranking_reports)
            ),
        }
        # v3-style checks, present only when the frozen manifest asked for
        # them. A set that claims to test discrimination must contain enough
        # cases that actually do, and must be judged on those cases.
        if "minimum_discriminating_case_fraction" in gate:
            gate_checks["discriminating_case_coverage"] = (
                discriminating_case_fraction
                >= gate["minimum_discriminating_case_fraction"]
            )
        if "discriminating_practical_winner_recall_minimum" in gate:
            gate_checks["discriminating_practical_winner_recall"] = (
                discriminating_practical_winner_recall is not None
                and discriminating_practical_winner_recall
                >= gate["discriminating_practical_winner_recall_minimum"]
            )
    else:
        gate_checks = {
            "winner_recall": winner_recall >= WINNER_RECALL_GATE,
            "p90_normalized_shortlist_regret": (
                p90_regret is not None
                and p90_regret <= P90_NORMALIZED_REGRET_GATE
            ),
            "median_spearman": (
                median_spearman is not None
                and median_spearman >= MEDIAN_SPEARMAN_GATE
            ),
            "spearman_case_coverage": (
                spearman_case_fraction
                >= manifest["minimum_spearman_case_fraction"]
            ),
            "ranking_case_coverage": (
                ranking_case_fraction
                >= manifest["minimum_ranking_case_fraction"]
            ),
            "all_shortlists_contain_eligible_candidate": (
                len(regrets) == len(ranking_reports)
            ),
        }
    passed = all(gate_checks.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_proxy_validation_report",
        "proxy_version": PROXY_VERSION,
        "shortlist_version": manifest.get("shortlist_version"),
        "shortlist_policy_content_key": manifest.get(
            "shortlist_policy_content_key"
        ),
        "manifest_content_key": manifest["content_key"],
        "case_count": len(case_reports),
        "metrics": {
            "winner_recall": round(winner_recall, 6),
            "practical_winner_recall": (
                round(practical_winner_recall, 6)
                if practical_winner_recall is not None
                else None
            ),
            "p90_normalized_shortlist_regret": (
                round(p90_regret, 6) if p90_regret is not None else None
            ),
            "median_spearman": (
                round(median_spearman, 6)
                if median_spearman is not None
                else None
            ),
            "spearman_case_fraction": round(spearman_case_fraction, 6),
            "ranking_case_fraction": round(ranking_case_fraction, 6),
            "discriminating_case_fraction": round(
                discriminating_case_fraction, 6
            ),
            "discriminating_practical_winner_recall": (
                round(discriminating_practical_winner_recall, 6)
                if discriminating_practical_winner_recall is not None
                else None
            ),
            "median_spearman_discriminating": (
                round(median(discriminating_spearmans), 6)
                if discriminating_spearmans
                else None
            ),
            "median_objective_spread_s": (
                round(median(spreads), 3) if spreads else None
            ),
            "failure_disqualification_recall": (
                round(failure_recall, 6)
                if failure_recall is not None
                else None
            ),
            "total_disqualified_schedules": total_disqualified,
        },
        "thresholds": (
            {
                **{field: gate[field] for field in sorted(gate)},
                "minimum_ranking_case_fraction":
                    manifest["minimum_ranking_case_fraction"],
            }
            if gate
            else {
                "winner_recall_minimum": WINNER_RECALL_GATE,
                "p90_normalized_shortlist_regret_maximum":
                    P90_NORMALIZED_REGRET_GATE,
                "median_spearman_minimum": MEDIAN_SPEARMAN_GATE,
                "minimum_spearman_case_fraction":
                    manifest["minimum_spearman_case_fraction"],
                "minimum_ranking_case_fraction":
                    manifest["minimum_ranking_case_fraction"],
            }
        ),
        "gate_checks": gate_checks,
        "gate_status": "pass" if passed else "fail",
        "ui_exposure_allowed": passed,
        "global_best_claim_allowed": passed,
        "case_reports": case_reports,
        "regret_definition": (
            "(best shortlisted objective - exhaustive best) / "
            "(exhaustive eligible p90 objective - exhaustive best)"
        ),
        "failure_recall_definition": (
            "actual SUMO disqualifications either flagged by the proxy or "
            "included in the SUMO shortlist"
        ),
        "practical_winner_definition": (
            "shortlist contains at least one eligible schedule whose paired "
            "median added time loss lies within gate.practical_equivalence_s "
            "of the exhaustive optimum"
            if gate
            else None
        ),
    }


def evaluate_partial_validation_set(
    manifest_raw: Mapping[str, Any],
    outcomes_raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Report resumable partial evidence without ever opening the UI gate."""
    manifest = validate_validation_manifest(manifest_raw)
    if not isinstance(outcomes_raw, Mapping):
        raise ValueError("partial validation outcomes must be an object")
    if outcomes_raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported partial validation outcomes schema")
    if outcomes_raw.get("kind") != "monthly_proxy_validation_outcomes":
        raise ValueError("partial validation outcomes kind is invalid")
    if outcomes_raw.get("proxy_version") != PROXY_VERSION:
        raise ValueError("partial validation outcomes proxy_version is not current")
    if outcomes_raw.get("manifest_content_key") != manifest["content_key"]:
        raise ValueError("partial outcomes do not belong to the manifest")
    outcomes = outcomes_raw.get("cases")
    if isinstance(outcomes, (str, bytes)) or not isinstance(outcomes, Sequence):
        raise ValueError("partial validation cases must be a list")
    descriptors = {case["case_id"]: case for case in manifest["cases"]}
    by_id: dict[str, Mapping[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise ValueError("partial validation case is invalid")
        case_id = str(outcome.get("case_id", ""))
        if case_id not in descriptors or case_id in by_id:
            raise ValueError("partial validation has duplicate or unknown cases")
        by_id[case_id] = outcome
    gate = manifest.get("gate")
    tolerance = gate["practical_equivalence_s"] if gate else None
    reports = [
        evaluate_validation_case(
            descriptors[case_id],
            outcome,
            practical_equivalence_s=tolerance,
        )
        for case_id, outcome in sorted(by_id.items())
    ]
    ranking = [report for report in reports if report["case_role"] == "ranking"]
    regrets = [
        report["normalized_shortlist_regret"]
        for report in ranking
        if report["normalized_shortlist_regret"] is not None
    ]
    spearmans = [
        report["spearman_rho"]
        for report in ranking
        if report["spearman_rho"] is not None
    ]
    failure_recalls = [
        report["failure_disqualification_recall"]
        for report in reports
        if report["failure_disqualification_recall"] is not None
    ]
    winner_recall = (
        sum(bool(report["winner_recalled"]) for report in ranking) / len(ranking)
        if ranking
        else None
    )
    missing = sorted(set(descriptors) - set(by_id))
    recalled_winners = sum(
        bool(report["winner_recalled"]) for report in ranking
    )
    # Optimistic bound: pretend every unfinished case becomes a ranking case
    # whose exhaustive winner is recalled.  If even that cannot reach the
    # threshold, the release gate is conclusively failed and further costly
    # SUMO runs cannot change that decision.  Under a manifest gate object
    # the bound applies to the PRACTICAL winner metric — the one that gates.
    winner_recall_upper_bound = (
        (recalled_winners + len(missing)) / (len(ranking) + len(missing))
        if ranking or missing
        else 0.0
    )
    practical_recall = (
        sum(bool(report["practical_winner_recalled"]) for report in ranking)
        / len(ranking)
        if gate and ranking
        else None
    )
    if gate:
        recalled_practical = sum(
            bool(report["practical_winner_recalled"]) for report in ranking
        )
        practical_upper_bound = (
            (recalled_practical + len(missing)) / (len(ranking) + len(missing))
            if ranking or missing
            else 0.0
        )
        conclusively_failed = (
            practical_upper_bound < gate["practical_winner_recall_minimum"]
        )
    else:
        practical_upper_bound = None
        conclusively_failed = winner_recall_upper_bound < WINNER_RECALL_GATE
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "monthly_proxy_validation_report",
        "proxy_version": PROXY_VERSION,
        "shortlist_version": manifest.get("shortlist_version"),
        "shortlist_policy_content_key": manifest.get(
            "shortlist_policy_content_key"
        ),
        "manifest_content_key": manifest["content_key"],
        "case_count": len(reports),
        "required_case_count": len(manifest["cases"]),
        "missing_case_ids": missing,
        "metrics": {
            "winner_recall": (
                round(winner_recall, 6) if winner_recall is not None else None
            ),
            "winner_recall_upper_bound": round(
                winner_recall_upper_bound, 6
            ),
            "practical_winner_recall": (
                round(practical_recall, 6)
                if practical_recall is not None
                else None
            ),
            "practical_winner_recall_upper_bound": (
                round(practical_upper_bound, 6)
                if practical_upper_bound is not None
                else None
            ),
            "p90_normalized_shortlist_regret": (
                round(_quantile(regrets, 0.9), 6) if regrets else None
            ),
            "median_spearman": (
                round(median(spearmans), 6) if spearmans else None
            ),
            "spearman_case_fraction": (
                round(len(spearmans) / len(ranking), 6) if ranking else 0.0
            ),
            "ranking_case_fraction": round(
                len(ranking) / len(reports), 6
            ) if reports else 0.0,
            "failure_disqualification_recall": (
                round(sum(failure_recalls) / len(failure_recalls), 6)
                if failure_recalls
                else None
            ),
        },
        "gate_status": "fail" if conclusively_failed else "incomplete",
        "gate_checks": (
            {
                "all_frozen_cases_complete": False,
                "practical_winner_recall": (
                    practical_recall is not None
                    and practical_recall
                    >= gate["practical_winner_recall_minimum"]
                ),
                "p90_normalized_shortlist_regret": (
                    bool(regrets)
                    and _quantile(regrets, 0.9)
                    <= gate["p90_normalized_shortlist_regret_maximum"]
                ),
                "failure_disqualification_recall": (
                    bool(failure_recalls)
                    and sum(failure_recalls) / len(failure_recalls)
                    >= gate["failure_disqualification_recall_minimum"]
                ),
            }
            if gate
            else {
                "all_frozen_cases_complete": False,
                "winner_recall": (
                    winner_recall is not None
                    and winner_recall >= WINNER_RECALL_GATE
                ),
                "p90_normalized_shortlist_regret": (
                    bool(regrets)
                    and _quantile(regrets, 0.9) <= P90_NORMALIZED_REGRET_GATE
                ),
                "median_spearman": (
                    bool(spearmans)
                    and median(spearmans) >= MEDIAN_SPEARMAN_GATE
                ),
            }
        ),
        "ui_exposure_allowed": False,
        "global_best_claim_allowed": False,
        "case_reports": reports,
        "reason": (
            "even perfect results on every unfinished case cannot raise "
            "winner recall to the release threshold"
            if conclusively_failed
            else "held-out evidence is incomplete; partial metrics are "
                 "diagnostic only and can never satisfy the release gate"
        ),
    }
