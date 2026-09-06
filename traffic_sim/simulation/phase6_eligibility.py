"""Shared, fail-closed Phase 6 eligibility rules."""
from __future__ import annotations

from typing import Any, Mapping


DECISION_POPULATION_GATES = (
    "semantic_comparison_complete",
    "candidate_costs_field_identical",
    "hard_failures_identical",
    "health_classifications_identical",
    "timeout_outcomes_identical",
    "terminal_status_identical",
    "selected_ids_identical",
    "execution_contract_valid",
    "final_decision_identical",
    "restart_equivalent",
    "restart_cursor_identical",
    "restart_evidence_identical",
    "restart_attempt_identity_identical",
    "both_stop_proofs_valid",
    "stop_proof_valid",
    "cache_hits_consistent",
    "daily_results_cache_events_valid",
    "exact_attempt_population_check",
    "active_elapsed_basis_consistent",
    "resource_measurements_complete",
)


def decision_population_complete(
    comparison: Mapping[str, Any], caps: Mapping[str, Any]
) -> bool:
    """Re-derive a bounded population independently of its summary flag."""
    for name in DECISION_POPULATION_GATES:
        value = comparison.get(name)
        if name == "exact_attempt_population_check":
            if not isinstance(value, Mapping) or value.get("valid") is not True:
                return False
        elif value is not True:
            return False
    fixture = comparison.get("fixture_application") or {}
    if (fixture.get("applied") is not True
            or fixture.get("arm_inputs_identical") is not True
            or fixture.get("restart_cancel_observed") is not True
            or fixture.get("no_detour_pre_sumo_gate") is not True):
        return False
    cancellation = comparison.get("cancellation") or {}
    if (cancellation.get("performed") is not True
            or cancellation.get("called") is not True
            or cancellation.get("queued_work_cancelled") is not True
            or cancellation.get("no_later_starter") is not True):
        return False
    try:
        rss_cap = int(caps["peak_rss_bytes"])
        active_cap = float(caps["active_seconds"])
        disk_cap = int(caps["disk_growth_bytes"])
    except (KeyError, TypeError, ValueError):
        return False
    rss = comparison.get("peak_rss_bytes")
    active = comparison.get("active_elapsed_s")
    if (not isinstance(rss, Mapping)
            or set(rss) != {"cost_ordered", "ordered_exhaustive"}
            or not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and 0 <= int(value) <= rss_cap for value in rss.values())
            or not isinstance(active, Mapping)
            or set(active) != {"cost_ordered", "ordered_exhaustive"}
            or not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and 0 <= float(value) <= active_cap
                       for value in active.values())):
        return False
    disk = comparison.get("disk_growth_bytes")
    per_arm_disk = comparison.get("disk_growth_bytes_by_arm")
    return bool(
        isinstance(disk, (int, float)) and not isinstance(disk, bool)
        and 0 <= int(disk) <= disk_cap
        and isinstance(per_arm_disk, Mapping)
        and set(per_arm_disk) == {"cost_ordered", "ordered_exhaustive"}
        and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= int(value) <= disk_cap
                for value in per_arm_disk.values())
    )


def phase3_outcome_population_eligible(
    outcome: Mapping[str, Any], registration: Mapping[str, Any]
) -> bool:
    """Admit PASS or only a complete performance-only inconclusive outcome."""
    if (outcome.get("schema") != "subhour_cost_ordered_bounded_outcome_v1"
            or outcome.get("kind") != "subhour_bounded_sumo_outcome"
            or outcome.get("release_evidence") is not False
            or outcome.get("status") not in {
                "PASS", "INCONCLUSIVE_PERFORMANCE_GATE"}):
        return False
    selection = outcome.get("selection")
    selected_ids = selection.get("selected_ids") if isinstance(
        selection, Mapping) else None
    registered_selection = registration.get("selection")
    registered_ids = registered_selection.get("selected_ids") if isinstance(
        registered_selection, Mapping) else None
    registered_cases = registration.get("selected_cases")
    cases = outcome.get("case_results")
    gate_s = outcome.get("gate_s")
    caps = registration.get("caps")
    if (not isinstance(selected_ids, list) or not selected_ids
            or selected_ids != registered_ids
            or len(set(selected_ids)) != len(selected_ids)
            or not isinstance(registered_cases, list)
            or not isinstance(cases, list) or len(cases) != len(selected_ids)
            or not isinstance(caps, Mapping)
            or outcome.get("decision_population_complete") is not True
            or not isinstance(gate_s, Mapping)
            or gate_s.get("population_complete") is not True
            or not isinstance(gate_s.get("variants"), Mapping)
            or set(gate_s["variants"]) != {"q10", "q50", "q90"}):
        return False
    expected_pairs = [
        (item.get("case_id"), item.get("search_content_key"))
        for item in registered_cases if isinstance(item, Mapping)]
    actual_pairs = [
        (item.get("case_id"), item.get("search_content_key"))
        for item in cases if isinstance(item, Mapping)]
    if len(expected_pairs) != len(registered_cases) or actual_pairs != expected_pairs:
        return False
    for case in cases:
        comparison = case.get("comparison") if isinstance(case, Mapping) else None
        if (not isinstance(comparison, Mapping)
                or case.get("decision_population_complete") is not True
                or not decision_population_complete(comparison, caps)):
            return False
        if outcome["status"] == "PASS" and case.get("gates_passed") is not True:
            return False
    return True


def phase6_prerequisites_allow(
    statuses: Mapping[str, str], *, phase3_population_eligible: bool,
    phase_d_pass: bool,
) -> bool:
    """Exact predicate shared by controller and full-month registration."""
    return bool(
        phase_d_pass
        and all(statuses.get(name) == "PASS"
                for name in ("phase_0", "phase_1", "phase_2", "phase_4"))
        and statuses.get("phase_5") in {"PASS", "NOT_TRIGGERED"}
        and statuses.get("review") == "PASS"
        and (statuses.get("phase_3") == "PASS"
             or (statuses.get("phase_3") == "INCONCLUSIVE"
                 and phase3_population_eligible))
    )
