"""Confidence-stage validation report — IMPROVEMENT_PLAN.md G3.

Assembles every gate the pipeline already computes into a single
machine-readable + UI-displayable file, so "is this build trustworthy?"
is answerable without reading terminal output:

  - counts_fit      — PFE GEH / infeasible intervals (demand_meta.pfe_fit)
  - structure       — destination realism, trip lengths, onward-after-
                      sensor, drift flags (demand_meta.calibrated_structure)
  - purposes        — calibrated purpose mix + per-purpose lengths +
                      RVU ordering check (agent_demand, purpose_length_km)
  - simulation      — per-seed health + flags (baseline scenario payload)
  - held_out        — LOSO ratios per station (web/data/loso_report.json)
  - temporal_holdout — frozen-release LOSO on a distinct historical date
  - verdict         — pass / warn per section + overall, with reasons

Each section carries a `status`: "pass", "warn" (published but flagged),
or "missing" (input artifact absent — absence is stated, never silently
skipped). Written to web/data/validation.json (the UI fetches it
statically) by `make demand` and refreshed after scenario rebuilds.

Design rule (§4A step 4 / improvement plan 3.3): thresholds live in ONE
place — this module reuses the pipeline's own gate outputs (flags already
computed at build time) rather than re-deriving them with potentially
diverging logic. The only judgment made here is aggregation.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.sensor_fit import (assess_exact_output_fit,
                                               assess_output_fit)

SUMO_DIR = Path("sumo")
WEB_DATA = Path("web/data")
OUT_PATH = WEB_DATA / "validation.json"

SCHEMA_VERSION = 1


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _counts_section(meta: dict | None) -> dict:
    if not meta or "pfe_fit" not in meta:
        return {"status": "missing", "reason": "demand_meta.json/pfe_fit saknas"}
    fit = meta["pfe_fit"]
    constraints = fit.get("integer_sensor_constraints")
    exact = fit.get("integer_sensor_exact")
    max_abs_error = fit.get("integer_sensor_max_abs_error")
    exact_ok = (
        isinstance(constraints, int) and constraints > 0
        and isinstance(exact, int) and exact == constraints
        and isinstance(max_abs_error, (int, float))
        and not isinstance(max_abs_error, bool)
        and math.isfinite(max_abs_error)
        and max_abs_error == 0
    )
    ok = (fit.get("geh_pct", 0) >= 99.0
          and not fit.get("infeasible_intervals")
          and exact_ok)
    return {
        "status": "pass" if ok else "warn",
        "geh_pct": fit.get("geh_pct"),
        "infeasible_intervals": fit.get("infeasible_intervals"),
        "vehicles": fit.get("vehicles"),
        "integer_sensor_constraints": constraints,
        "integer_sensor_exact": exact,
        "integer_sensor_exact_pct": fit.get("integer_sensor_exact_pct"),
        "integer_sensor_max_abs_error": max_abs_error,
        "integer_sensor_target_rule": fit.get("integer_sensor_target_rule"),
        "gate": "exakt heltalsmatchning på varje riktad sensor × 15 minuter, "
                "GEH<5 på ≥99% av sensorintervall och 0 olösliga kvartar",
    }


def _structure_section(meta: dict | None) -> dict:
    cs = (meta or {}).get("calibrated_structure")
    if not cs:
        return {"status": "missing", "reason": "calibrated_structure saknas"}
    flags = cs.get("structure_flags", [])
    prox = cs.get("dest_sensor_proximity", {})
    onward = cs.get("onward_after_last_sensor", {})
    tl = cs.get("trip_length_fit", {})
    l1_distance = tl.get("l1_distance")
    maximum_l1 = tl.get("maximum_l1_distance")
    l1_gate_defined = (
        isinstance(maximum_l1, (int, float))
        and not isinstance(maximum_l1, bool)
        and math.isfinite(maximum_l1)
        and maximum_l1 >= 0
    )
    l1_gate_passed = (
        l1_gate_defined
        and isinstance(l1_distance, (int, float))
        and not isinstance(l1_distance, bool)
        and math.isfinite(l1_distance)
        and l1_distance <= maximum_l1
    )
    reasons = []
    if not l1_gate_defined:
        reasons.append(
            "trip-length L1 publiceras men saknar fryst acceptansgräns")
    elif not l1_gate_passed:
        reasons.append(
            f"trip-length L1 {l1_distance} överstiger gränsen {maximum_l1}")
    if flags:
        reasons.extend(str(flag) for flag in flags)
    return {
        "status": "pass" if not reasons else "warn",
        "flags": flags,
        "dest_within_200m_pct": prox.get("pct_within"),
        "dest_baseline_pct": prox.get("baseline_pct_within"),
        "trip_length_shares": tl.get("shares"),
        "trip_length_l1_vs_rvu": l1_distance,
        "trip_length_l1_maximum": maximum_l1,
        "trip_length_l1_gate_defined": l1_gate_defined,
        "trip_length_l1_gate_passed": l1_gate_passed,
        "onward_after_sensor_median_m": onward.get("median_m"),
        "onward_under_200m_pct": onward.get("pct_under_200m"),
        "gate": "kalibrerad struktur får inte driva >2.5x från poolen; "
                "trip-length L1 måste dessutom ha och klara en fryst absolut "
                "gräns mot den deklarerade externa målfördelningen",
        **({"reason": "; ".join(reasons)} if reasons else {}),
    }


def _purpose_section(meta: dict | None) -> dict:
    agents = (meta or {}).get("agent_demand")
    if not agents:
        return {"status": "missing", "reason": "agent_demand saknas"}
    cs = (meta or {}).get("calibrated_structure", {})
    lengths = cs.get("purpose_length_km", {})
    ordering_flag = any("purpose_length_ordering" in f
                        for f in cs.get("structure_flags", []))
    variant_fit = (meta or {}).get("pfe_fit_variants", {})
    compatibility = {
        name: report.get("purpose_incompatible_quarters")
        for name, report in variant_fit.items()
        if isinstance(report, dict)
        and "purpose_incompatible_quarters" in report
    }
    incompatible = max((value for value in compatibility.values()
                        if isinstance(value, (int, float))), default=0)
    replacements = {
        name: report.get("purpose_replaced_routes")
        for name, report in variant_fit.items()
        if isinstance(report, dict)
        and "purpose_replaced_routes" in report
    }
    mix_relaxed = {
        name: report.get("purpose_mix_relaxed_quarters")
        for name, report in variant_fit.items()
        if isinstance(report, dict)
        and "purpose_mix_relaxed_quarters" in report
    }
    mix_reallocated = {
        name: report.get("purpose_mix_reallocation_vehicles")
        for name, report in variant_fit.items()
        if isinstance(report, dict)
        and "purpose_mix_reallocation_vehicles" in report
    }
    relaxed_mix_count = max((value for value in mix_relaxed.values()
                             if isinstance(value, (int, float))), default=0)
    return {
        # Source compatibility and the exact generated purpose margin are
        # different claims. A counts-first fallback can retain each agent's
        # real OD/purpose provenance while honestly reporting that the
        # aggregate behavioural prior could not coexist with hard sensors.
        "status": "warn" if ordering_flag or incompatible or relaxed_mix_count else "pass",
        "purpose_counts": agents.get("purpose_counts"),
        "purpose_length_km": lengths,
        "ordering_violated": ordering_flag,
        "purpose_incompatible_quarters_by_variant": compatibility,
        "purpose_replaced_routes_by_variant": replacements,
        "purpose_mix_relaxed_quarters_by_variant": mix_relaxed,
        "purpose_mix_reallocation_vehicles_by_variant": mix_reallocated,
        "purpose_claims_allowed": incompatible == 0,
        "purpose_mix_matches_generated_prior": relaxed_mix_count == 0,
        "gate": "fritid ska ha längst medianresa (Trafikanalys RVU Tabell 3); "
                "exakt ärende×tid-mix per kvart när den är förenlig med "
                "sensorerna; varje tilldelad rutt måste ha kompatibel "
                "proveniens",
    }


def _simulation_section(baseline: dict | None) -> dict:
    if not baseline:
        return {"status": "missing", "reason": "baseline.json saknas"}
    health = baseline.get("seed_health")
    flags = baseline.get("seed_health_flags")
    if health is None:
        return {"status": "missing",
                "reason": "baslinjen byggdes före hälsomätningen (E3)"}
    return {
        "status": "pass" if not flags else "warn",
        "flags": flags or [],
        "seeds": [{
            "seed": h.get("seed"),
            "inserted": h.get("inserted"),
            "loaded": h.get("loaded"),
            "unfinished": (h.get("running_at_end", 0) or 0)
                          + (h.get("waiting_at_end", 0) or 0),
            "teleports": h.get("teleports"),
        } for h in health],
        "gate": "SUMO accepterar hela den redan kalibrerade ruttfilen, "
                "<2% ofullbordade och teleporteringar under tröskel, per frö",
    }


def _sensor_output_section(meta: dict | None,
                           baseline: dict | None) -> dict:
    """Report fit against SUMO edgeData, not only PFE's pre-SUMO targets."""
    audit = (baseline or {}).get("sensor_audit") if baseline else None
    output_fit = audit.get("output_fit") if isinstance(audit, dict) else None
    if not isinstance(output_fit, dict):
        return {"status": "missing",
                "reason": "baseline saknar slutlig SUMO sensor-output-fit"}
    assessment = assess_output_fit(
        audit, n_intervals=int((baseline or {}).get("n_quarters", 0) or 0),
        days=int((meta or {}).get("days", 1) or 1))
    ensemble = assessment["directions"] or output_fit.get("ensemble") or {}
    station = assessment["stations"] or output_fit.get("station_ensemble") or {}
    raw = output_fit.get("uses_raw_ensemble_mean") is True
    result = {
        "status": "pass" if not assessment["errors"] else "warn",
        "contract": output_fit.get("contract"),
        "uses_raw_ensemble_mean": raw,
        "aggregation_minutes": output_fit.get("aggregation_minutes", 15),
        "edge_quarters": ensemble.get("edge_quarters"),
        "geh_lt_5_pct": ensemble.get("geh_lt_5_pct"),
        "mean_abs_error": ensemble.get("mean_abs_error"),
        "max_abs_error": ensemble.get("max_abs_error"),
        "station_edge_quarters": station.get("edge_quarters"),
        "station_max_abs_error": station.get("max_abs_error"),
        "gate": "rå SUMO edgeData måste täcka varje fryst sensor-target och ha GEH<5",
    }
    if assessment["errors"]:
        result["reason"] = "; ".join(assessment["errors"][:3])
    return result


def _exact_sensor_output_section(baseline: dict | None) -> dict:
    """Expose the strict, non-mutating 15-minute SUMO passage test."""
    audit = (baseline or {}).get("sensor_audit") if baseline else None
    exact = audit.get("exact_output_fit") if isinstance(audit, dict) else None
    if not isinstance(exact, dict):
        return {
            "status": "missing",
            "reason": "baseline saknar exakt 15-minuters sensor-test",
        }
    assessment = assess_exact_output_fit(
        audit, n_intervals=int((baseline or {}).get("n_quarters", 0) or 0))
    ensemble = assessment.get("ensemble") or {}
    representative = assessment.get("representative") or {}
    per_seed = assessment.get("per_seed") or []
    result = {
        "status": "pass" if not assessment["errors"] else "warn",
        "contract": exact.get("contract"),
        "aggregation_minutes": 15,
        "target_rule": exact.get("target_rule"),
        "constraints": ensemble.get("constraints"),
        "exact": ensemble.get("exact"),
        "exact_pct": ensemble.get("exact_pct"),
        "max_abs_error": ensemble.get("max_abs_error"),
        "sum_abs_error": ensemble.get("sum_abs_error"),
        "mismatch_count": len(assessment.get("ensemble_mismatches") or []),
        "representative_exact": representative.get("exact"),
        "representative_constraints": representative.get("constraints"),
        "per_seed": per_seed,
        "gate": "rå SUMO-passage måste matcha varje riktad sensor × "
                "15-minuters heltalsmål exakt; testet ändrar inga fordon",
    }
    if assessment["errors"]:
        result["reason"] = "; ".join(assessment["errors"][:5])
    return result


def _multi_day_section(meta: dict | None, baseline: dict | None) -> dict:
    """Expose continuity evidence without treating a one-day run as proof."""
    days = int((meta or {}).get("days", 1) or 1)
    if days <= 1:
        return {"status": "pass", "days": 1, "not_applicable": True,
                "gate": "flerdagsgrind gäller endast kontinuerliga dagar"}
    evidence = (baseline or {}).get("multi_day_validation") if baseline else None
    if not isinstance(evidence, dict):
        return {"status": "missing", "days": days,
                "reason": "baseline saknar periodisk SUMO-sammanfattning för flera dagar"}
    from traffic_sim.simulation.multiday import validate as validate_multiday
    boundaries = meta.get("day_boundaries_s") or [day * 86400
                                                   for day in range(days + 1)]
    errors = validate_multiday(evidence, days=days,
                               day_boundaries_s=boundaries)
    per_day = evidence.get("per_day") or []
    return {
        "status": "pass" if not errors else "warn",
        "days": days,
        "complete": evidence.get("complete") is True,
        "errors": errors,
        "per_day": per_day,
        "gate": "varje kalenderdag måste ha komplett summary-bevis; "
                "aggregat räcker inte för flerdagspublicering",
    }


def _held_out_section(loso: dict | None) -> dict:
    if not loso or not loso.get("stations"):
        return {"status": "missing", "reason": "loso_report.json saknas — "
                "kör validate_sim.py"}
    ratios = {}
    for sid, st in sorted(loso["stations"].items()):
        for edge, ed in st.get("edges", {}).items():
            ratios[f"{sid}:{edge}"] = ed.get("ratio")
    vals = sorted(v for v in ratios.values() if v is not None)
    return {
        # LOSO is characterization, not a pass/fail gate: with 6 stations
        # in 2 clusters some folds are informationally isolated by
        # geometry (documented in IMPROVEMENT_PLAN.md: 1076's 0.05 is honest
        # parsimony). Displayed, never blocking.
        "status": "info",
        "window": loso.get("window"),
        "ratios": ratios,
        "median_ratio": vals[len(vals) // 2] if vals else None,
        "note": "utelämnad-station-återskapande; karakterisering, inte grind "
                "— extrema veck kan spegla sensorns informationsisolering, "
                "inte modellfel",
    }


def _temporal_holdout_section(report: dict | None,
                              meta: dict | None) -> dict:
    if not report or not report.get("stations"):
        return {
            "status": "missing",
            "reason": "temporal_holdout_report.json saknas — kör "
                      "validate_sim.py --holdout-date YYYY-MM-DD",
        }
    contract = report.get("comparison_contract") or {}
    expected_reference = (meta or {}).get("epoch_sim")
    expected_through = ((meta or {}).get("build_options") or {}).get(
        "through_share_target")
    stale_reasons = []
    checks = (
        ("candidate pool",
         contract.get("candidate_pool_sha256"),
         sha256_file(SUMO_DIR / "candidates.rou.xml")),
        ("network",
         contract.get("network_sha256"),
         sha256_file(SUMO_DIR / "net.net.xml")),
        ("reference window",
         contract.get("reference_window_start"),
         expected_reference),
        ("through-share target",
         contract.get("through_share_target"),
         expected_through),
    )
    for label, recorded, current in checks:
        if recorded is None or current is None or str(recorded) != str(current):
            stale_reasons.append(
                f"{label}: report={recorded!r}, current={current!r}")
    if contract.get("source") != (meta or {}).get("source"):
        stale_reasons.append(
            f"source: report={contract.get('source')!r}, "
            f"current={(meta or {}).get('source')!r}")
    if stale_reasons:
        return {
            "status": "missing",
            "reason": "temporal holdout är inaktuellt för nuvarande release: "
                      + "; ".join(stale_reasons),
        }
    ratios = {}
    observed = {}
    for sid, station in sorted(report["stations"].items()):
        for edge, evidence in station.get("edges", {}).items():
            key = f"{sid}:{edge}"
            ratios[key] = evidence.get("ratio")
            observed[key] = evidence.get("observed_quarters")
    values = sorted(value for value in ratios.values() if value is not None)
    return {
        # Like spatial LOSO, temporal LOSO characterizes independent
        # recovery rather than imposing a tuned pass threshold on seven
        # clustered directed edges. Its presence is nevertheless explicit,
        # so a release cannot claim temporal evidence from the reference-day
        # LOSO alone.
        "status": "info",
        "protocol": contract.get("protocol"),
        "reference_window": {
            "start": contract.get("reference_window_start"),
            "end": contract.get("reference_window_end"),
        },
        "evaluation_window": {
            "start": contract.get("window_start"),
            "end": contract.get("window_end"),
        },
        "minimum_coverage": contract.get("minimum_temporal_coverage"),
        "observed_quarters": observed,
        "ratios": ratios,
        "median_ratio": values[len(values) // 2] if values else None,
        "note": "fryst kandidatpool/nät/priorer utvärderade på en annan "
                "historisk dag; utelämnad stations egna värden används "
                "endast för jämförelse",
    }


def assemble() -> dict:
    meta = _load(SUMO_DIR / "demand_meta.json")
    baseline = _load(WEB_DATA / "scenarios" / "baseline.json")
    loso = _load(WEB_DATA / "loso_report.json")
    temporal = _load(WEB_DATA / "temporal_holdout_report.json")

    sections = {
        "counts_fit": _counts_section(meta),
        "structure": _structure_section(meta),
        "purposes": _purpose_section(meta),
        "simulation": _simulation_section(baseline),
        "sensor_output": _sensor_output_section(meta, baseline),
        "sensor_output_exact": _exact_sensor_output_section(baseline),
        "multi_day": _multi_day_section(meta, baseline),
        "held_out": _held_out_section(loso),
        "temporal_holdout": _temporal_holdout_section(temporal, meta),
    }
    gated = [s for s in sections.values() if s["status"] in ("pass", "warn")]
    overall = ("warn" if any(s["status"] == "warn" for s in gated)
               else "pass" if gated else "missing")
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "demand_window": ((meta or {}).get("date")
                          or (meta or {}).get("start_date")),
        "demand_source": (meta or {}).get("source"),
        # Phase 6 acceptance gate: the validation panel must reflect the
        # ACTIVE study's build, not merely the latest demand build.  The
        # report is one global artifact describing whichever demand was
        # calibrated when it ran, so it has to STATE that identity —
        # otherwise a reader looking at a scenario from another build sees
        # a green shield that validates something else entirely.  The web
        # app compares this against the active scenario's own build id.
        "demand_build_id": (meta or {}).get("build_id"),
        "overall": overall,
        "sections": sections,
    }
    return report


def write_report() -> dict:
    report = assemble()
    tmp = OUT_PATH.with_name(OUT_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(report, f, indent=1)
    tmp.replace(OUT_PATH)
    n_warn = sum(1 for s in report["sections"].values()
                 if s["status"] == "warn")
    n_missing = sum(1 for s in report["sections"].values()
                    if s["status"] == "missing")
    print(f"Validering: {report['overall'].upper()} "
          f"({n_warn} varning(ar), {n_missing} saknad(e) sektion(er)) "
          f"→ {OUT_PATH}")
    return report


if __name__ == "__main__":
    write_report()
