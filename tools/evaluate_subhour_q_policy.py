"""Evaluate the separately preregistered q10/q50/q90 Gate S policy.

This module does not alter the monthly search.  It consumes bound bounded or
full-month evidence and applies the four-result policy mechanically.  In
particular, ``Q50_ONLY`` is never inferred from a small observed difference:
it requires zero decision regret, identical finalists and complete recall of
variant-unique decision-relevant failures.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "subhour_gate_s_registration_v2"
OUTCOME_SCHEMA = "subhour_gate_s_outcome_v2"
ROBUST_THREE_VARIANT = "ROBUST_THREE_VARIANT"
FINALIST_STRESS = "FINALIST_STRESS"
Q50_ONLY = "Q50_ONLY"
INCONCLUSIVE = "INCONCLUSIVE"
STRESS_CASES = ("q10", "q50", "q90")
GATE_EVIDENCE_SCHEMA = "subhour_gate_s_evidence_v1"
_DECISION_FIELDS = {
    "hard_failures", "viable_set", "finalists", "winner",
    "capacity_exceeded",
}


def extract_gate_s_evidence(*, source_path: Path,
                            evidence_id: str) -> dict[str, Any]:
    """Extract Gate S input from one bound current Phase 3/6 outcome.

    The extractor never fills missing decisions with neutral values.  A
    preflight, unhealthy, or otherwise incomplete source becomes an explicit
    incomplete Gate S envelope, which ``evaluate`` can publish as
    ``INCONCLUSIVE``.  Complete activation-capable evidence must carry the
    actual q10/q50/q90 source population in the outcome's ``gate_s`` field.
    """
    source_path = Path(source_path).resolve()
    if not source_path.is_file():
        raise ValueError("Gate S source outcome is missing")
    raw = source_path.read_bytes()
    source = json.loads(raw.decode("utf-8"))
    if not isinstance(source, Mapping):
        raise ValueError("Gate S source outcome must be an object")
    source_key = source.get("content_key")
    if not isinstance(source_key, str) or source_key != _digest(
            {key: value for key, value in source.items()
             if key != "content_key"}):
        raise ValueError("Gate S source outcome content key is invalid")
    source_evidence_id = source.get("evidence_id")
    registration = source.get("registration")
    if (not isinstance(source_evidence_id, str) or not source_evidence_id
            or not isinstance(registration, Mapping)
            or registration.get("evidence_id") != source_evidence_id
            or not isinstance(registration.get("content_key"), str)
            or not registration.get("content_key")):
        raise ValueError("Gate S source outcome is not bound to its registration")
    registration_path = registration.get("path")
    if not isinstance(registration_path, str) or not registration_path:
        raise ValueError("Gate S source registration path is required")
    registration_file = Path(registration_path).resolve()
    if not registration_file.is_file():
        raise ValueError("Gate S source registration is missing")
    registration_raw = registration_file.read_bytes()
    if registration.get("sha256") != hashlib.sha256(registration_raw).hexdigest():
        raise ValueError("Gate S source registration bytes drifted")
    bound_registration = json.loads(registration_raw.decode("utf-8"))
    if (not isinstance(bound_registration, Mapping)
            or bound_registration.get("schema")
            != "subhour_cost_ordered_bounded_registration_v1"
            or bound_registration.get("evidence_id") != source_evidence_id
            or bound_registration.get("content_key") != registration.get(
                "content_key")):
        raise ValueError("Gate S source registration identity does not match")
    registration_body = {
        key: value for key, value in bound_registration.items()
        if key not in {"content_key", "registered_at"}
    } if isinstance(bound_registration, Mapping) else {}
    if (not isinstance(bound_registration, Mapping)
            or bound_registration.get("content_key") != _digest(registration_body)):
        raise ValueError("Gate S source registration content key is invalid")
    selected = ((source.get("selection") or {}).get("selected_ids")
                if isinstance(source.get("selection"), Mapping) else None)
    if not isinstance(selected, list) or not selected:
        raise ValueError("Gate S source outcome has no registered case IDs")
    registered_selection = bound_registration.get("selection")
    registered_ids = (registered_selection.get("selected_ids")
                      if isinstance(registered_selection, Mapping) else None)
    registered_cases = bound_registration.get("selected_cases")
    if (not isinstance(registered_ids, list)
            or selected != registered_ids
            or not isinstance(registered_cases, list)
            or any(not isinstance(item, Mapping)
                   or not isinstance(item.get("case_id"), str)
                   or not isinstance(item.get("search_content_key"), str)
                   for item in registered_cases)):
        raise ValueError("Gate S source selection is not bound to its registration")
    cases = source.get("case_results")
    case_items = cases if isinstance(cases, list) else []
    observed_pairs = [
        (item.get("case_id"), item.get("search_content_key"))
        for item in case_items if isinstance(item, Mapping)
    ]
    registered_pairs = [
        (item["case_id"], item["search_content_key"])
        for item in registered_cases
    ]
    if observed_pairs != registered_pairs:
        raise ValueError("Gate S source case identities do not match registration")
    observed_ids = [str(item.get("case_id")) for item in case_items
                    if isinstance(item, Mapping) and item.get("case_id")]
    source_gate = source.get("gate_s")
    complete = bool(
        len(observed_ids) == len(selected)
        and len(set(observed_ids)) == len(observed_ids)
        and all(str(item.get("search_content_key")) in selected
                for item in case_items if isinstance(item, Mapping))
        and all(item.get("decision_population_complete") is True
                for item in case_items if isinstance(item, Mapping))
        and isinstance(source_gate, Mapping)
        and source_gate.get("population_complete") is True
        and set(source_gate.get("variants") or {}) == set(STRESS_CASES)
    )
    source_status = str(source.get("status") or "INCONCLUSIVE")
    # A speed/resource miss is a Phase 3 terminal, not a correctness-health
    # failure.  Once the paired decision population is complete and bound to
    # the registration, Gate S may evaluate it independently of that status.
    health_status = (
        "PASS" if complete and source_status in {
            "PASS", "INCONCLUSIVE_PERFORMANCE_GATE",
            "INCONCLUSIVE_SPEED_GATE",
        } else source_status
    )
    if health_status == "PASS" and not complete:
        health_status = "INCONCLUSIVE_INCOMPLETE_SOURCE_POPULATION"
    evidence: dict[str, Any] = {
        "schema": GATE_EVIDENCE_SCHEMA,
        "kind": "subhour_gate_s_evidence",
        "release_evidence": False,
        "evidence_id": str(evidence_id),
        "health": {
            "status": health_status,
            "detail": (None if complete else
                        "bound Phase 3/full-month outcome lacks complete "
                        "q10/q50/q90 source-derived decisions"),
        },
        "source_lineage": {
            "schema": source.get("schema"),
            "evidence_id": source_evidence_id,
            "content_key": source_key,
            "path": str(source_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "population": {
            "registered_cases": len(selected),
            "observed_cases": len(observed_ids),
            "registered_variants": list(STRESS_CASES),
            "observed_case_ids": observed_ids,
            "decision_population_complete": complete,
            "case_population_complete": complete,
        },
        "variants": {},
    }
    if complete:
        source_variants = source_gate["variants"]
        for variant in STRESS_CASES:
            item = source_variants.get(variant)
            if not isinstance(item, Mapping):
                raise ValueError("Gate S source variant is malformed")
            decision = item.get("decision")
            if (not isinstance(decision, Mapping)
                    or set(decision) != _DECISION_FIELDS
                    or not isinstance(decision.get("capacity_exceeded"), bool)):
                raise ValueError(
                    "Gate S source decision lacks the registered finalist-capacity field")
            copied = dict(item)
            copied["case_records"] = [
                {"case_id": str(case["case_id"]),
                 "source_case_sha256": _digest(dict(case))}
                 for case in case_items
            ]
            evidence["variants"][variant] = copied
    evidence["content_key"] = _digest(evidence)
    return evidence


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def build_registration(*, evidence_path: Path, evidence_sha256: str,
                       evidence_id: str = "subhour-gate-s-20260831-v2") -> dict[str, Any]:
    """Create a Gate S registration from a validated current evidence file."""
    evidence_path = Path(evidence_path).resolve()
    if not evidence_path.is_file():
        raise ValueError("Gate S evidence is missing")
    raw = evidence_path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != str(evidence_sha256):
        raise ValueError("Gate S evidence digest does not match its bytes")
    evidence = _load_gate_evidence(raw)
    lineage = evidence["source_lineage"]
    return _with_content_key({
        "schema": SCHEMA,
        "kind": "subhour_gate_s_registration",
        "evidence_id": evidence_id,
        "release_evidence": False,
        "activation": {"q50_only": False, "ui_api_change": False},
        "bound_evidence": {
            "path": str(evidence_path),
            "sha256": actual_sha256,
            "schema": evidence["schema"],
            "evidence_id": evidence["evidence_id"],
            "content_key": evidence["content_key"],
            "source_lineage": dict(lineage),
        },
        "stress_cases": list(STRESS_CASES),
        "decision_fields": [
            "hard_failures", "viable_set", "finalists", "winner",
            "capacity_exceeded",
        ],
        "policy": {
            # Keep the plan's three comparisons explicit.  The published
            # result names remain the four allowed terminal outcomes below.
            "ROBUST_FINALIST": {
                "comparison": "robust q10/q50/q90 cost and all three stress arms",
                "requires_stress_qualification": True,
            },
            "Q50_PLUS_STRESS": {
                "comparison": "q50 normal arm with q10/q90 before finalist approval",
                "requires_stress_qualification": True,
            },
            "Q50_ONLY": {
                "comparison": "q50 cost and q50 SUMO arm",
                "zero_decision_regret": True,
                "identical_finalists": True,
                "variant_unique_failure_recall": 1.0,
            },
            "ROBUST_THREE_VARIANT": "ROBUST_FINALIST comparison changes qualification",
            "FINALIST_STRESS": "Q50_PLUS_STRESS comparison changes finalists or regret",
            "INCONCLUSIVE": "missing, unhealthy, or non-comparable bound evidence",
        },
        "claim_boundary": "q50_only_inactive_without_strict_gate_and_review",
    })


def _with_content_key(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["content_key"] = _digest(result)
    return result


def verify_registration(registration: Mapping[str, Any], *, root: Path = ROOT) -> None:
    if registration.get("schema") != SCHEMA or registration.get("release_evidence") is not False:
        raise ValueError("Gate S registration is not the diagnostic v2 policy")
    body = {key: value for key, value in registration.items() if key != "content_key"}
    if registration.get("content_key") != _digest(body):
        raise ValueError("Gate S registration content key is invalid")
    if registration.get("stress_cases") != list(STRESS_CASES):
        raise ValueError("Gate S must bind q10/q50/q90")
    if registration.get("decision_fields") != [
            "hard_failures", "viable_set", "finalists", "winner",
            "capacity_exceeded"]:
        raise ValueError("Gate S registration decision schema is incomplete")
    q50 = (registration.get("policy") or {}).get("Q50_ONLY") or {}
    if q50.get("zero_decision_regret") is not True \
            or q50.get("identical_finalists") is not True \
            or float(q50.get("variant_unique_failure_recall", -1)) != 1.0:
        raise ValueError("Gate S Q50_ONLY gates are not strict")
    bound = registration.get("bound_evidence") or {}
    path = Path(bound.get("path", ""))
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        raise ValueError("bound Gate S evidence is missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != bound.get("sha256"):
        raise ValueError("bound Gate S evidence drift")
    evidence = _load_gate_evidence(path.read_bytes())
    if bound.get("schema") != evidence["schema"] \
            or bound.get("evidence_id") != evidence["evidence_id"] \
            or bound.get("content_key") != evidence["content_key"] \
            or bound.get("source_lineage") != evidence["source_lineage"]:
        raise ValueError("Gate S registration lineage does not match evidence")


def _load_gate_evidence(raw: bytes) -> dict[str, Any]:
    """Validate the complete, current Gate S input envelope."""
    try:
        evidence = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Gate S evidence is not valid JSON") from error
    if not isinstance(evidence, dict) or evidence.get("schema") != GATE_EVIDENCE_SCHEMA:
        raise ValueError("Gate S requires the current bound evidence schema")
    content_key = evidence.get("content_key")
    body = {key: value for key, value in evidence.items() if key != "content_key"}
    if not isinstance(content_key, str) or content_key != _digest(body):
        raise ValueError("Gate S evidence content key is invalid")
    lineage = evidence.get("source_lineage")
    if not isinstance(lineage, Mapping) or not {
            "schema", "evidence_id", "content_key", "path", "sha256"} <= set(lineage):
        raise ValueError("Gate S evidence lacks complete source lineage")
    source_path = Path(str(lineage["path"]))
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != lineage["sha256"]:
        raise ValueError("Gate S source evidence is missing or drifted")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(source, Mapping) or source.get("schema") != lineage["schema"] \
            or source.get("evidence_id") != lineage["evidence_id"]:
        raise ValueError("Gate S source lineage is not bound to the source bytes")
    if not isinstance(source.get("evidence_id"), str) \
            or not source.get("evidence_id"):
        raise ValueError("Gate S source outcome lacks top-level evidence identity")
    if source.get("content_key") != lineage["content_key"]:
        raise ValueError("Gate S source content key does not match lineage")
    source_cases = _bound_source_cases(source, allow_incomplete=True)
    source_gate = source.get("gate_s")
    population = evidence.get("population")
    source_selection = source.get("selection") or {}
    registered_source_count = len(source_selection.get("selected_ids", []))
    if not isinstance(population, Mapping) \
            or population.get("registered_cases") != registered_source_count \
            or population.get("registered_variants") != list(STRESS_CASES) \
            or population.get("observed_case_ids") != [
                item["case_id"] for item in source_cases]:
        raise ValueError("Gate S source does not contain the complete registered case set")
    if population.get("decision_population_complete") is not True \
            or population.get("case_population_complete") is not True:
        # Incomplete bound outcomes are valid diagnostic input.  Their only
        # legal Gate S result is INCONCLUSIVE, so no placeholder decision is
        # validated or used below.
        return evidence
    if not isinstance(source_gate, Mapping) \
            or set(source_gate.get("variants") or {}) != set(STRESS_CASES):
        raise ValueError(
            "Gate S source does not contain bound q10/q50/q90 decisions")
    variants = evidence.get("variants")
    if set(variants or {}) != set(STRESS_CASES):
        raise ValueError("Gate S evidence lacks complete q10/q50/q90 coverage")
    for variant in STRESS_CASES:
        item = variants[variant]
        decision = item.get("decision") if isinstance(item, Mapping) else None
        if (not isinstance(item, Mapping)
                or set(decision or {}) != _DECISION_FIELDS
                or not isinstance(decision.get("capacity_exceeded"), bool)):
            raise ValueError("Gate S evidence has an incomplete decision population")
        failures = item.get("decision_relevant_failures")
        if not isinstance(failures, list) or len({str(value) for value in failures}) != len(failures):
                raise ValueError("Gate S evidence has an invalid failure population")
        candidate_costs = item.get("candidate_costs")
        if not isinstance(candidate_costs, Mapping) or not candidate_costs:
            raise ValueError("Gate S evidence lacks candidate-level cost population")
        for candidate_id, cost in candidate_costs.items():
            if not isinstance(candidate_id, str) or not isinstance(cost, Mapping):
                raise ValueError("Gate S candidate cost identity is invalid")
            required_cost = {"added_vehicle_hours", "added_metres_total",
                             "vehicles_affected", "vehicles_no_detour",
                             "feasible"}
            if set(cost) != required_cost:
                raise ValueError("Gate S candidate cost fields are incomplete")
            if not isinstance(cost["feasible"], bool):
                raise ValueError("Gate S candidate feasibility is invalid")
            if any(isinstance(cost[field], bool)
                   or not isinstance(cost[field], (int, float))
                   or not math.isfinite(float(cost[field]))
                   for field in required_cost - {"feasible"}):
                raise ValueError("Gate S candidate cost is not finite")
        winner_cost = item.get("winner_cost")
        reference_cost = item.get("reference_winner_cost")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value))
               for value in (winner_cost, reference_cost)):
            raise ValueError("Gate S evidence lacks numeric regret inputs")
        source_variant = source_gate["variants"].get(variant)
        if not isinstance(source_variant, Mapping):
            raise ValueError("Gate S source variant is malformed")
        if (source_variant.get("decision") != decision
                or source_variant.get("decision_relevant_failures")
                != failures
                or source_variant.get("candidate_costs") != candidate_costs
                or source_variant.get("winner_cost") != winner_cost
                or source_variant.get("reference_winner_cost") != reference_cost):
            raise ValueError(
                "Gate S decision or failure metrics are not extracted from "
                "the bound source outcome")
        records = item.get("case_records")
        if not isinstance(records, list) or len(records) != len(source_cases):
            raise ValueError(
                "Gate S evidence lacks complete source-derived case records")
        expected_records = {
            case["case_id"]: _digest(case["record"])
            for case in source_cases}
        seen_case_ids = []
        for record in records:
            if (not isinstance(record, Mapping)
                    or set(record) != {"case_id", "source_case_sha256"}
                    or not isinstance(record["case_id"], str)
                    or record["case_id"] not in expected_records
                    or not isinstance(record["source_case_sha256"], str)
                    or record["source_case_sha256"] != expected_records[
                        record["case_id"]]):
                raise ValueError(
                    "Gate S case record is not derived from the bound source")
            seen_case_ids.append(record["case_id"])
        if seen_case_ids != [case["case_id"] for case in source_cases]:
            raise ValueError(
                "Gate S case records do not cover the bound source cases "
                "exactly once")
    return evidence


def _bound_source_cases(source: Mapping[str, Any], *,
                        allow_incomplete: bool = False) -> list[dict[str, Any]]:
    """Reconstruct the registered case population from bound source bytes."""
    selection = source.get("selection")
    selected_ids = (selection or {}).get("selected_ids") \
        if isinstance(selection, Mapping) else None
    cases = source.get("case_results")
    if not isinstance(selected_ids, list) or not selected_ids \
            or len(set(selected_ids)) != len(selected_ids) \
            or not isinstance(cases, list) \
            or (not allow_incomplete and len(cases) != len(selected_ids)) \
            or (allow_incomplete and len(cases) > len(selected_ids)):
        raise ValueError(
            "Gate S source does not contain the complete registered case set")
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("Gate S source case is malformed")
        case_id = case.get("case_id")
        content_key = case.get("search_content_key")
        if (not isinstance(case_id, str) or not case_id
                or case_id in by_id or content_key not in selected_ids):
            raise ValueError("Gate S source case identity is incomplete")
        by_id[case_id] = {
            "case_id": case_id,
            "record": dict(case),
            "search_content_key": content_key,
        }
    if not allow_incomplete and {
            item["search_content_key"] for item in by_id.values()} != set(selected_ids):
        raise ValueError("Gate S source cases do not cover selected IDs exactly")
    ordered = [next(item for item in by_id.values()
                    if item["search_content_key"] == selected_id)
               for selected_id in selected_ids
               if any(item["search_content_key"] == selected_id
                      for item in by_id.values())]
    return ordered


def _read_bound_evidence(registration: Mapping[str, Any], *, root: Path = ROOT
                         ) -> tuple[dict[str, Any], bytes]:
    """Read exactly the bytes named by the registration, never a substitute."""
    verify_registration(registration, root=root)
    path = Path((registration.get("bound_evidence") or {}).get("path", ""))
    if not path.is_absolute():
        path = Path(root) / path
    raw = path.read_bytes()
    try:
        evidence = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("bound Gate S evidence is not valid JSON") from error
    if not isinstance(evidence, dict):
        raise ValueError("bound Gate S evidence must be an object")
    return evidence, raw


def _signature(decision: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(json.dumps(decision.get(field), sort_keys=True)
                 for field in ("hard_failures", "viable_set", "finalists", "winner"))


def classify(decisions: Mapping[str, Mapping[str, Any]], *, health: Mapping[str, Any] | None = None,
             decision_regret: Mapping[str, float] | None = None,
             variant_unique_failures: Mapping[str, Any] | None = None,
             q50_failure_recall: float | None = None,
             _trusted_bound: bool = False) -> dict[str, Any]:
    """Return one of the four preregistered Gate S outcomes."""
    if not _trusted_bound:
        return {"status": INCONCLUSIVE,
                "reasons": ["Gate S metrics must be derived from bound evidence"],
                "q50_only_active": False}
    health = dict(health or {})
    # ``extract_gate_s_evidence`` has already validated the source bytes,
    # lineage, case population and q10/q50/q90 records.  A successful bounded
    # or full-month source therefore uses its published ``PASS`` status as a
    # trusted healthy status; ``ok`` remains accepted for the direct library
    # contract and older validated evidence envelopes.
    if health and health.get("status") not in {"ok", "PASS"}:
        return {"status": INCONCLUSIVE, "reasons": [str(health.get("detail") or health["status"])],
                "q50_only_active": False}
    if set(decisions) != set(STRESS_CASES) or any(
            not isinstance(decision, Mapping)
            or set(decision) != _DECISION_FIELDS
            or not isinstance(decision.get("capacity_exceeded"), bool)
            for decision in decisions.values()):
        return {"status": INCONCLUSIVE, "reasons": ["q10/q50/q90 decisions are incomplete"],
                "q50_only_active": False}
    if not isinstance(decision_regret, Mapping) \
            or set(decision_regret) != set(STRESS_CASES):
        return {"status": INCONCLUSIVE,
                "reasons": ["complete q10/q50/q90 decision regret is required"],
                "q50_only_active": False}
    # Qualification changes are checked before numeric regret because an
    # infeasible q50 winner has no finite physical regret; it is already a
    # decisive robust-stress result and must not be hidden as INCONCLUSIVE.
    failure_sets = {}
    if isinstance(variant_unique_failures, Mapping):
        for variant in STRESS_CASES:
            values = variant_unique_failures.get(variant)
            if isinstance(values, Mapping) and isinstance(values.get("all"), list):
                failure_sets[variant] = {str(item) for item in values["all"]}
    changed_qualification = any(
        decisions[variant][field] != decisions["q50"][field]
        for variant in ("q10", "q90")
        for field in ("hard_failures", "viable_set")
    ) or any(
        failure_sets.get(variant, set()) != failure_sets.get("q50", set())
        for variant in ("q10", "q90")
    ) or any(
        bool(decisions[variant].get("capacity_exceeded", False))
        != bool(decisions["q50"].get("capacity_exceeded", False))
        for variant in ("q10", "q90")
    )
    if any(bool(decision.get("capacity_exceeded", False))
           for decision in decisions.values()):
        return {
            "status": INCONCLUSIVE,
            "reasons": ["registered finalist capacity was exceeded"],
            "q50_only_active": False,
        }
    regret = {}
    undefined_regret = False
    for variant in STRESS_CASES:
        value = decision_regret[variant]
        if value is None:
            undefined_regret = True
            regret[variant] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)) or float(value) < 0:
            return {"status": INCONCLUSIVE,
                    "reasons": ["decision regret population is invalid"],
                    "q50_only_active": False}
        regret[variant] = float(value)
    if not isinstance(variant_unique_failures, Mapping) \
            or set(variant_unique_failures) != set(STRESS_CASES):
        return {"status": INCONCLUSIVE,
                "reasons": ["complete variant-unique failure populations are required"],
                "q50_only_active": False}
    all_failures: set[str] = set()
    recalled: set[str] = set()
    for variant in STRESS_CASES:
        values = variant_unique_failures[variant]
        if not isinstance(values, Mapping) \
                or set(values) != {"all", "q50_recalled"} \
                or not isinstance(values["all"], list) \
                or not isinstance(values["q50_recalled"], list):
            return {"status": INCONCLUSIVE,
                    "reasons": ["variant-unique failure population is invalid"],
                    "q50_only_active": False}
        current = {str(item) for item in values["all"]}
        current_recalled = {str(item) for item in values["q50_recalled"]}
        if not current_recalled <= current:
            return {"status": INCONCLUSIVE,
                    "reasons": ["q50 failure recall contains unknown failures"],
                    "q50_only_active": False}
        all_failures.update(current)
        recalled.update(current_recalled)
    recall = (len(recalled) / len(all_failures)) if all_failures else 1.0
    if q50_failure_recall is not None:
        if isinstance(q50_failure_recall, bool) or not isinstance(q50_failure_recall, (int, float)) \
                or not math.isfinite(float(q50_failure_recall)) \
                or float(q50_failure_recall) != recall:
            return {"status": INCONCLUSIVE,
                    "reasons": ["q50 failure recall does not match its population"],
                    "q50_only_active": False}
    if changed_qualification:
        return {"status": ROBUST_THREE_VARIANT,
                "reasons": ["ROBUST_FINALIST stress changes qualification"],
                "q50_only_active": False, "decision_regret": regret,
                "variant_unique_failure_recall": recall}
    if undefined_regret:
        return {"status": INCONCLUSIVE,
                "reasons": ["actual policy regret is unavailable"],
                "q50_only_active": False}
    if any(float(value) != 0.0 for value in regret.values()):
        return {"status": FINALIST_STRESS,
                "reasons": ["Q50_PLUS_STRESS decision regret is non-zero"],
                "q50_only_active": False, "decision_regret": regret}
    q50_finalists = decisions["q50"].get("finalists")
    identical_finalists = all(decision.get("finalists") == q50_finalists
                              for decision in decisions.values())
    if all(float(value) == 0.0 for value in regret.values()) \
            and identical_finalists and recall == 1.0:
        return {"status": Q50_ONLY,
                "reasons": ["strict Q50_ONLY gates passed"],
                "q50_only_active": True, "decision_regret": regret,
                "variant_unique_failure_recall": recall}
    # Qualification (hard failures or viable population) was handled above.
    # Any remaining difference is a finalist/winner stress result.  Keep this
    # branch explicit instead of deriving a second, incomplete set of fields:
    # the old branch referenced an undefined ``changed_robust_fields`` and
    # turned a valid zero-regret finalist change into a NameError.
    return {"status": FINALIST_STRESS,
            "reasons": ["stress evidence changes a bound decision field"],
            "q50_only_active": False, "decision_regret": regret,
            "variant_unique_failure_recall": recall}


def evaluate(registration: Mapping[str, Any], evidence: Mapping[str, Any] | None = None,
             *, root: Path = ROOT) -> dict[str, Any]:
    bound_evidence, raw = _read_bound_evidence(registration, root=root)
    if evidence is not None:
        supplied = json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
        bound_canonical = json.dumps(bound_evidence, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")
        if supplied != bound_canonical:
            raise ValueError("caller-supplied Gate S evidence is not bound to the registration")
    evidence = bound_evidence
    population = evidence.get("population") or {}
    if (population.get("decision_population_complete") is not True
            or population.get("case_population_complete") is not True):
        detail = ((evidence.get("health") or {}).get("detail")
                  or "bound Gate S source population is incomplete")
        return _with_content_key({
            "schema": OUTCOME_SCHEMA,
            "kind": "subhour_gate_s_outcome",
            "release_evidence": False,
            "registration": {"evidence_id": registration["evidence_id"],
                              "content_key": registration["content_key"]},
            "bound_evidence_sha256": hashlib.sha256(raw).hexdigest(),
            "status": INCONCLUSIVE,
            "gate": {"status": INCONCLUSIVE, "reasons": [str(detail)],
                     "q50_only_active": False},
        })
    decisions, regret, failures = _derive_gate_inputs(evidence)
    gate = classify(decisions, health=evidence.get("health"),
                    decision_regret=regret,
                    variant_unique_failures=failures,
                    q50_failure_recall=None,
                    _trusted_bound=True)
    return _with_content_key({
        "schema": OUTCOME_SCHEMA,
        "kind": "subhour_gate_s_outcome",
        "release_evidence": False,
        "registration": {"evidence_id": registration["evidence_id"],
                          "content_key": registration["content_key"]},
        "bound_evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "status": gate["status"],
        "gate": gate,
    })


def _derive_gate_inputs(evidence: Mapping[str, Any]):
    """Derive Gate S comparisons from bound candidate-level populations.

    ``reference_winner_cost`` is retained in the evidence schema for lineage,
    but is not a policy metric: comparing cost-ordered to ordered-exhaustive
    within one variant only tests execution equivalence.  Actual policy regret
    evaluates the q50-selected winner under each stress variant against that
    variant's own best feasible candidate.
    """
    variants = evidence["variants"]
    decisions = {variant: dict(variants[variant]["decision"])
                 for variant in STRESS_CASES}
    q50_winners = variants["q50"]["decision"].get("winner")
    if isinstance(q50_winners, str):
        q50_winners = [q50_winners]
    if not isinstance(q50_winners, list):
        q50_winners = []
    regret: dict[str, float | None] = {}
    for variant in STRESS_CASES:
        candidate_costs = variants[variant]["candidate_costs"]
        stress_winners = variants[variant]["decision"].get("winner")
        if isinstance(stress_winners, str):
            stress_winners = [stress_winners]
        if not isinstance(stress_winners, list):
            stress_winners = []
        if any(not isinstance(candidate_costs.get(winner), Mapping)
               or candidate_costs[winner].get("feasible") is not True
               for winner in q50_winners):
            regret[variant] = None
            continue
        def aggregate_cost(winners: list[str]) -> tuple[float, float, int] | None:
            rows = [candidate_costs.get(winner) for winner in winners]
            if (not winners or any(not isinstance(row, Mapping)
                                   or row.get("feasible") is not True
                                   for row in rows)):
                return None
            return (
                sum(float(row["added_vehicle_hours"]) for row in rows),
                sum(float(row["added_metres_total"]) for row in rows),
                sum(int(row["vehicles_affected"]) for row in rows),
            )

        q50_cost = aggregate_cost(q50_winners)
        optimal_cost = aggregate_cost(stress_winners)
        # Regret is deliberately a lexicographic decision indicator.  A zero
        # value means the complete registered closure-cost tuple is identical;
        # it is never inferred from vehicle-hours alone.  The magnitude is not
        # a weighted pseudo-currency, because the registration defines no such
        # weights.
        regret[variant] = (
            None if q50_cost is None or optimal_cost is None
            else 0.0 if q50_cost == optimal_cost else 1.0
        )
    q50_failures = {str(item) for item in
                    variants["q50"]["decision_relevant_failures"]}
    failures = {}
    for variant in STRESS_CASES:
        population = {str(item) for item in
                      variants[variant]["decision_relevant_failures"]}
        failures[variant] = {
            "all": sorted(population),
            "q50_recalled": sorted(population & q50_failures),
        }
    return decisions, regret, failures
