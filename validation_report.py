"""One validation report per build — IMPROVEMENT_PLAN.md G3 (improvement plan 3.2).

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
from datetime import datetime, timezone
from pathlib import Path

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
    ok = (fit.get("geh_pct", 0) >= 99.0
          and not fit.get("infeasible_intervals"))
    return {
        "status": "pass" if ok else "warn",
        "geh_pct": fit.get("geh_pct"),
        "infeasible_intervals": fit.get("infeasible_intervals"),
        "vehicles": fit.get("vehicles"),
        "gate": "GEH<5 på ≥99% av sensorintervall, 0 olösliga kvartar",
    }


def _structure_section(meta: dict | None) -> dict:
    cs = (meta or {}).get("calibrated_structure")
    if not cs:
        return {"status": "missing", "reason": "calibrated_structure saknas"}
    flags = cs.get("structure_flags", [])
    prox = cs.get("dest_sensor_proximity", {})
    onward = cs.get("onward_after_last_sensor", {})
    tl = cs.get("trip_length_fit", {})
    return {
        "status": "pass" if not flags else "warn",
        "flags": flags,
        "dest_within_200m_pct": prox.get("pct_within"),
        "dest_baseline_pct": prox.get("baseline_pct_within"),
        "trip_length_shares": tl.get("shares"),
        "trip_length_l1_vs_rvu": tl.get("l1_distance"),
        "onward_after_sensor_median_m": onward.get("median_m"),
        "onward_under_200m_pct": onward.get("pct_under_200m"),
        "gate": "kalibrerad struktur får inte driva >2.5x från poolen "
                "(destinationer nära sensor, längdintervall, ärendelängder)",
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
    return {
        # Purpose labels are diagnostic only while any selected route lacks
        # matching source provenance. Do not let a perfect GEH fit imply
        # that purpose-specific behaviour is validated.
        "status": "warn" if ordering_flag or incompatible else "pass",
        "purpose_counts": agents.get("purpose_counts"),
        "purpose_length_km": lengths,
        "ordering_violated": ordering_flag,
        "purpose_incompatible_quarters_by_variant": compatibility,
        "purpose_claims_allowed": incompatible == 0,
        "gate": "fritid ska ha längst medianresa (Trafikanalys RVU Tabell 3); "
                "exakt ärende×tid-mix per kvart; varje tilldelad rutt "
                "måste ha kompatibel proveniens",
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
        "gate": "alla fordon insatta, <2% ofullbordade, "
                "teleporteringar under tröskel, per frö",
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


def assemble() -> dict:
    meta = _load(SUMO_DIR / "demand_meta.json")
    baseline = _load(WEB_DATA / "scenarios" / "baseline.json")
    loso = _load(WEB_DATA / "loso_report.json")

    sections = {
        "counts_fit": _counts_section(meta),
        "structure": _structure_section(meta),
        "purposes": _purpose_section(meta),
        "simulation": _simulation_section(baseline),
        "held_out": _held_out_section(loso),
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
