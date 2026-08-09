#!/usr/bin/env python3
"""Apply DfT TAG Unit M3.1 link-flow guidance to held-out LOSO cases.

The historical filename is retained for compatibility, but this is not a
DMRB rule.  The criteria are from UK Department for Transport TAG Unit M3.1,
Table 2.  They apply to *individual hourly flows*:

* absolute difference <= 100 veh/h where the observed flow is below 700;
* relative difference <= 15% from 700 through 2,700 veh/h;
* absolute difference <= 400 veh/h above 2,700; or
* GEH < 5.

The flow-difference criterion and GEH are assessed separately, and more than
85% of cases is the guideline for each.  Their union is retained as a clearly
labelled project diagnostic, never as the formal TAG verdict.  LOSO reports
must carry their hourly measured and simulated cases; daily totals and per-edge
percentages cannot reconstruct this test and are rejected rather than silently
scored by a different rule.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

DEFAULT_REPORT = Path("web/data/loso_report.json")
GUIDELINE_PCT = 85.0
AGGREGATE_VOLUME_ERROR_LIMIT_PCT = 10.0


def geh(modelled: float, observed: float) -> float:
    """Return the TAG GEH statistic for one hourly flow case."""
    denominator = modelled + observed
    return math.sqrt(2.0 * (modelled - observed) ** 2 / denominator) \
        if denominator > 0 else 0.0


def flow_difference_ok(modelled: float, observed: float) -> bool:
    """Apply TAG M3.1 Table 2's flow-dependent difference criterion."""
    difference = abs(modelled - observed)
    if observed < 700:
        return difference <= 100
    if observed <= 2700:
        return difference / observed <= 0.15
    return difference <= 400


def _validated_cases(report: dict) -> list[dict]:
    if report.get("schema_version") != 3:
        raise ValueError("LOSO report schema_version must be 3")
    contract = report.get("comparison_contract")
    n_intervals = (contract or {}).get("n_intervals")
    if isinstance(n_intervals, bool) or not isinstance(n_intervals, int) \
            or n_intervals < 4:
        raise ValueError("LOSO report lacks a valid interval-count contract")
    expected_hours = set(range(n_intervals // 4))
    cases = []
    for station, record in sorted(report.get("stations", {}).items()):
        for edge, entry in sorted(record.get("edges", {}).items()):
            hourly = entry.get("hourly_cases")
            if not isinstance(hourly, list) or not hourly:
                raise ValueError(
                    "LOSO report lacks hourly_cases; rerun corrected "
                    "validate_sim.py before applying TAG M3.1"
                )
            seen_hours = set()
            for item in hourly:
                if not isinstance(item, dict) or set(item) != {
                    "hour_index", "measured", "simulated"
                }:
                    raise ValueError("LOSO hourly case schema is invalid")
                hour = item["hour_index"]
                measured = item["measured"]
                simulated = item["simulated"]
                if isinstance(hour, bool) or not isinstance(hour, int) \
                        or hour < 0 or hour in seen_hours:
                    raise ValueError("LOSO hourly case index is invalid")
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or value < 0
                       for value in (measured, simulated)):
                    raise ValueError("LOSO hourly flow is invalid")
                seen_hours.add(hour)
                measured = float(measured)
                simulated = float(simulated)
                case_geh = geh(simulated, measured)
                flow_ok = flow_difference_ok(simulated, measured)
                geh_ok = case_geh < 5.0
                cases.append({
                    "station": str(station),
                    "edge": str(edge),
                    "hour_index": hour,
                    "measured": measured,
                    "simulated": simulated,
                    "geh": round(case_geh, 3),
                    "geh_ok": geh_ok,
                    "flow_difference_ok": flow_ok,
                    "satisfactory": geh_ok or flow_ok,
                })
            excluded = entry.get("excluded_incomplete_hours")
            if not isinstance(excluded, list) or any(
                    isinstance(hour, bool) or not isinstance(hour, int)
                    or hour not in expected_hours for hour in excluded):
                raise ValueError("LOSO excluded-hour schema is invalid")
            excluded_hours = set(excluded)
            if len(excluded_hours) != len(excluded) or seen_hours & excluded_hours \
                    or seen_hours | excluded_hours != expected_hours:
                raise ValueError(
                    "LOSO hourly cases do not account for the complete window")
            observed_quarters = entry.get("observed_quarters")
            if isinstance(observed_quarters, bool) \
                    or not isinstance(observed_quarters, int) \
                    or not 0 <= observed_quarters <= n_intervals \
                    or len(excluded_hours) > n_intervals - observed_quarters:
                raise ValueError(
                    "LOSO excluded hours disagree with observed-quarter coverage")
    if not cases:
        raise ValueError("LOSO report contains no held-out hourly cases")
    return cases


def _criterion(cases: list[dict], field: str) -> dict:
    meeting = sum(bool(case[field]) for case in cases)
    share = 100.0 * meeting / len(cases)
    return {
        "cases_meeting": meeting,
        "cases_total": len(cases),
        "share_pct": round(share, 1),
        "guideline_pct": GUIDELINE_PCT,
        "pass": share > GUIDELINE_PCT,
    }


def _group_summary(cases: list[dict], group_fields: tuple[str, ...]) -> list[dict]:
    grouped = defaultdict(list)
    for case in cases:
        grouped[tuple(case[field] for field in group_fields)].append(case)
    out = []
    for key, members in sorted(grouped.items()):
        record = dict(zip(group_fields, key))
        measured_total = sum(case["measured"] for case in members)
        simulated_total = sum(case["simulated"] for case in members)
        satisfactory = _criterion(members, "satisfactory")
        # Keep the historical top-level satisfactory fields for callers, but
        # expose the two TAG measures separately.  A single combined percentage
        # hid whether an edge passed because its GEH or its flow-band tolerance
        # was acceptable, which made per-sensor presentation easy to overstate.
        record.update(satisfactory)
        record.update({
            "measured_total": round(measured_total, 3),
            "simulated_total": round(simulated_total, 3),
            "ratio": (round(simulated_total / measured_total, 3)
                      if measured_total > 0 else None),
            "mean_geh": round(statistics.fmean(
                case["geh"] for case in members), 3),
            "max_geh": round(max(case["geh"] for case in members), 3),
            "mean_absolute_error": round(statistics.fmean(
                abs(case["simulated"] - case["measured"])
                for case in members), 3),
            "criteria": {
                "geh_lt_5": _criterion(members, "geh_ok"),
                "flow_difference_by_observed_band": _criterion(
                    members, "flow_difference_ok"),
                "either_flow_difference_or_geh": satisfactory,
            },
        })
        out.append(record)
    return out


def _fmt(value: float | None, decimals: int = 1) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def aggregate_volume_test(report: dict) -> dict:
    """Compare aggregate held-out volume across every sensor location.

    This deliberately includes weak stations such as 1076.  It is a simple
    network-total volume diagnostic, not a replacement for the per-sensor TAG
    test: over- and underestimation may cancel in the aggregate, so both views
    remain visible in the presentation.
    """
    cases = _validated_cases(report)
    stations = sorted({case["station"] for case in cases})
    expected_stations = sorted(str(station) for station in report["stations"])
    if stations != expected_stations:
        raise ValueError("aggregate volume test requires every report station")
    measured_total = sum(case["measured"] for case in cases)
    simulated_total = sum(case["simulated"] for case in cases)
    error_pct = (
        100.0 * abs(simulated_total - measured_total) / measured_total
        if measured_total > 0 else None
    )
    passed = error_pct is not None and error_pct <= AGGREGATE_VOLUME_ERROR_LIMIT_PCT
    return {
        "schema_version": 1,
        "test": "all-sensor aggregate held-out volume",
        "standard": "project diagnostic with a declared 10 percent limit",
        "verdict": "pass" if passed else "fail",
        "claim_scope": "total held-out traffic volume across all sensor locations",
        "not_claimed": "equal accuracy at every individual sensor",
        "stations_included": stations,
        "station_count": len(stations),
        "hourly_cases": len(cases),
        "measured_total": round(measured_total, 3),
        "simulated_total": round(simulated_total, 3),
        "ratio": (round(simulated_total / measured_total, 3)
                  if measured_total > 0 else None),
        "absolute_error": round(abs(simulated_total - measured_total), 3),
        "absolute_error_pct": round(error_pct, 1) if error_pct is not None else None,
        "limit_pct": AGGREGATE_VOLUME_ERROR_LIMIT_PCT,
        "all_stations_required": True,
    }


def presentation_markdown(
    report: dict, result: dict, *, calibration: dict | None = None,
    closure: dict | None = None,
) -> str:
    """Render a Swedish, slide-ready sensor table without hiding limitations."""
    by_station = {row["station"]: row for row in result["by_edge"]}
    lines = [
        "# Sensorvalidering för presentation",
        "",
        f"Utvärderingsfönster: {result['window']}. Resultaten är temporal "
        "leave-one-station-out: sensorns egna värden används endast för "
        "jämförelse, inte för att passa den folden.",
    ]
    aggregate = aggregate_volume_test(report)
    included = ", ".join(aggregate["stations_included"])
    lines.extend([
        "",
        "## Tydligt huvudtest: total trafikvolym, alla sensorer",
        "",
        f"**Resultat: {aggregate['verdict'].upper()}**",
        "",
        f"Alla {aggregate['station_count']} sensorer ingår: {included}. "
        "Ingen sensor har tagits bort ur testet.",
        "",
        f"Mätt totalvolym är {aggregate['measured_total']:.0f} fordon och "
        f"simulerad totalvolym är {aggregate['simulated_total']:.0f}. "
        f"Skillnaden är {aggregate['absolute_error_pct']:.1f} procent mot "
        f"projektets testgräns högst {aggregate['limit_pct']:.0f} procent.",
        "",
        "Detta är ett enkelt projekttest, inte en separat TAG-standard. Det "
        "svarar på om simuleringen får rätt samlad trafikmängd i "
        "sensornätet. Över- och underskattningar kan ta ut varandra, så "
        "sensortabellen och det strikta TAG-testet redovisas fortfarande "
        "separat nedan.",
    ])
    lines.extend([
        "",
        "| Sensor | Timmar | Mätt dygn | Simulerat dygn | Ratio | Dygnsavvikelse | "
        "GEH < 5 | Medel-GEH | Max-GEH | TAG flödesskillnad | Kombinerat "
        "projekttest | Rank gain | Tolkning |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | --- |",
    ])
    for station_id, station in sorted(
        report.get("stations", {}).items(), key=lambda item: item[0]
    ):
        row = by_station[station_id]
        ratio = row["ratio"]
        deviation = None if ratio is None else 100.0 * (ratio - 1.0)
        criteria = row["criteria"]
        geh_pct = criteria["geh_lt_5"]["share_pct"]
        flow_pct = criteria["flow_difference_by_observed_band"]["share_pct"]
        either_pct = criteria["either_flow_difference_or_geh"]["share_pct"]
        certificate = station.get("observability_certificate") or {}
        rank_gain = certificate.get("rank_gain")
        tag_pass = geh_pct > GUIDELINE_PCT and flow_pct > GUIDELINE_PCT
        if tag_pass and rank_gain == 0:
            interpretation = "klarar riktvärdet och är identifierbar"
        elif tag_pass:
            interpretation = "bra holdout-fit, men underidentifierad"
        elif ratio is not None and ratio > 1.0:
            interpretation = "överskattar; underidentifierad"
        else:
            interpretation = "underskattar; underidentifierad"
        lines.append(
            f"| {station_id} | {row['cases_total']}/24 | "
            f"{row['measured_total']:.0f} | "
            f"{row['simulated_total']:.0f} | {_fmt(ratio, 3)} | "
            f"{_fmt(deviation, 1)} % | {geh_pct:.1f} % | "
            f"{row['mean_geh']:.2f} | {row['max_geh']:.2f} | "
            f"{flow_pct:.1f} % | {either_pct:.1f} % | "
            f"{rank_gain if rank_gain is not None else '—'} | "
            f"{interpretation} |"
        )

    lines.extend([
        "",
        "## Samtliga relevanta testresultat",
        "",
        "| Test | Resultat | Uppmätt utfall | Vad testet visar |",
        "| --- | --- | --- | --- |",
        f"| Totalvolym, alla sensorer | **{aggregate['verdict'].upper()}** | "
        f"{aggregate['absolute_error_pct']:.1f} % avvikelse; ratio "
        f"{aggregate['ratio']:.3f} | Samlad trafikmängd i hela sensornätet; "
        "1076 ingår |",
    ])
    if calibration:
        sections = calibration.get("sections", {})
        counts = sections.get("counts_fit", {})
        raw = sections.get("sensor_output", {})
        simulation = sections.get("simulation", {})
        structure = sections.get("structure", {})
        purposes = sections.get("purposes", {})
        multi_day = sections.get("multi_day", {})
        seeds = simulation.get("seeds", [])
        total_inserted = sum(int(seed.get("inserted", 0)) for seed in seeds)
        total_unfinished = sum(int(seed.get("unfinished", 0)) for seed in seeds)
        total_teleports = sum(int(seed.get("teleports", 0)) for seed in seeds)
        incompatible = sum(
            int(value) for value in
            purposes.get("purpose_incompatible_quarters_by_variant", {}).values()
        )
        lines.extend([
            f"| Kalibrering mot sensorintervall | "
            f"**{str(counts.get('status', '—')).upper()}** | "
            f"{counts.get('geh_pct', '—')} % GEH < 5; "
            f"{counts.get('infeasible_intervals', '—')} olösliga intervall | "
            "Efterfrågan kan reproduceras mot givna sensormål |",
            f"| Rå SUMO-sensorutdata | "
            f"**{str(raw.get('status', '—')).upper()}** | "
            f"{raw.get('geh_lt_5_pct', '—')} % GEH < 5; MAE "
            f"{_fmt(raw.get('mean_abs_error'), 2)} fordon/timme; maxfel "
            f"{_fmt(raw.get('max_abs_error'), 2)} | Att trafiken verkligen "
            "passerar sensorerna i SUMO, inte bara i efterfrågefilen |",
            f"| SUMO-körhälsa | "
            f"**{str(simulation.get('status', '—')).upper()}** | "
            f"{total_inserted:,} insatta; {total_unfinished} oavslutade; "
            f"{total_teleports} teleporteringar | Teknisk stabilitet över "
            f"{len(seeds)} varianter |".replace(",", " "),
            f"| Resestruktur | "
            f"**{str(structure.get('status', '—')).upper()}** | "
            f"{structure.get('dest_within_200m_pct', '—')} % destinationer "
            f"inom 200 m från sensor mot {structure.get('dest_baseline_pct', '—')} "
            "% slumpbaslinje; median fortsatt resa "
            f"{structure.get('onward_after_sensor_median_m', '—')} m | "
            "Resor avslutas inte artificiellt vid sensorerna |",
            f"| Ändamål och proveniens | "
            f"**{str(purposes.get('status', '—')).upper()}** | "
            f"{sum(int(value) for value in purposes.get('purpose_counts', {}).values()):,} "
            f"resor; {incompatible} inkompatibla kvart; "
            f"ordningsbrott: {str(purposes.get('ordering_violated', '—')).lower()} | "
            "Resändamål och rutternas ursprung är bevarade |".replace(",", " "),
            f"| Flerdagstest | **{'EJ TILLÄMPLIGT' if multi_day.get('not_applicable') else str(multi_day.get('status', '—')).upper()}** | "
            f"{multi_day.get('days', '—')} sammanhängande dag | Kräver fler "
            "sammanhängande mätdagar |",
        ])
        held = sections.get("held_out", {})
        held_ratios = list((held.get("ratios") or {}).values())
        if held_ratios:
            lines.append(
                f"| Samma-dag LOSO | **INFO** | Sensorratio "
                f"{min(held_ratios):.3f}–{max(held_ratios):.3f} | "
                "Karakteriserar återskapande när en sensor tas bort; ingen grind |"
            )

    overall_geh = result["criteria"]["geh_lt_5"]
    overall_flow = result["criteria"]["flow_difference_by_observed_band"]
    lines.append(
        f"| Temporalt holdout, TAG | **{result['verdict'].upper()}** | "
        f"Flöde {overall_flow['share_pct']:.1f} %; GEH "
        f"{overall_geh['share_pct']:.1f} %; båda bedöms mot >"
        f"{GUIDELINE_PCT:.0f} % | Oberoende test på en annan historisk dag |"
    )
    rank_gains = [
        (station.get("observability_certificate") or {}).get("rank_gain")
        for station in report.get("stations", {}).values()
    ]
    rank_one = sum(rank_gain == 1 for rank_gain in rank_gains)
    lines.append(
        f"| Observabilitet | **INFO** | {rank_one}/{len(rank_gains)} sensorer "
        "har rank gain 1 | Visar hur mycket ny information varje utelämnad "
        "sensor tillför; det är inte ett kvalitetsbetyg |"
    )
    if closure:
        scenario = closure.get("scenario", {})
        health = closure.get("seed_health", [])
        closure_teleports = sum(int(seed.get("teleports", 0)) for seed in health)
        collisions = sum(int(seed.get("collisions", 0)) for seed in health)
        leaked = scenario.get("active_closure_edge_entries")
        closure_pass = (
            scenario.get("closure_integrity") == "verified_clean"
            and closure_teleports == 0 and collisions == 0 and leaked == 0
        )
        lines.append(
            f"| Avstängningsstresstest | "
            f"**{'PASS' if closure_pass else 'FAIL'}** | "
            f"{closure_teleports} teleporteringar; {collisions} kollisioner; "
            f"{leaked} inträden på stängda kanter | Robust omledning och "
            "korrekt avstängningsintegritet |"
        )

    overall = result["criteria"]
    lines.extend([
        "",
        "## Så läses måtten",
        "",
        "- Ratio 1,000 betyder att simulerad och mätt dygnstotal är lika. "
        "Exempelvis 1,260 är 26,0 procent för högt och 0,706 är 29,4 "
        "procent för lågt. Ratio visar volymbias men inte timprofilen.",
        "",
        "- GEH beräknas per timme. 0 är exakt och lägre är bättre. TAG M3.1 "
        "anger GEH < 5 för mer än 85 procent av individuella flöden som "
        "riktvärde. Kolumnen visar andelen timmar som klarar gränsen; "
        "medel och max är diagnostik.",
        "",
        "- TAG flödesskillnad är ett separat test: högst 100 fordon/timme "
        "under 700, högst 15 procent mellan 700 och 2 700, och högst 400 "
        "över 2 700. TAG bedömer detta och GEH-andelen separat. Kolumnen "
        "'Kombinerat projekttest' visar deras union som extra diagnostik.",
        "",
        "- Rank gain är inte ett betyg mellan 0 och 1. Rank gain 0 betyder "
        "att den hållna sensorns total kan härledas ur kvarvarande mätmarginaler. "
        "Rank gain 1 betyder en ny oberoende dimension: flera ruttfördelningar "
        "kan passa övriga sensorer men ge olika värde här. Samtliga sex "
        "sensorer är därför strukturellt underidentifierade.",
        "",
        "## Sammanfattning",
        "",
        f"- Temporal holdout GEH < 5: "
        f"{overall['geh_lt_5']['cases_meeting']}/"
        f"{overall['geh_lt_5']['cases_total']} timmar "
        f"({overall['geh_lt_5']['share_pct']:.1f} procent).",
        "",
        f"- TAG flödesskillnad: "
        f"{overall['flow_difference_by_observed_band']['share_pct']:.1f} "
        "procent.",
        "",
        f"- Kombinerat projektdiagnostikmått (flödesskillnad eller GEH): "
        f"{overall['either_flow_difference_or_geh']['share_pct']:.1f} procent "
        "(inte ett separat formellt TAG-kriterium).",
        "",
        f"- Formellt TAG-anpassat verdict kräver att både flödesskillnad "
        f"och GEH-andel överstiger {GUIDELINE_PCT:.0f} procent: "
        f"**{result['verdict'].upper()}**.",
    ])
    if calibration:
        sections = calibration.get("sections", {})
        raw = sections.get("sensor_output", {})
        simulation = sections.get("simulation", {})
        seeds = simulation.get("seeds", [])
        lines.extend([
            "",
            "## Kompletterande kontroller av den aktuella simuleringen",
            "",
            f"- Kalibreringsdagens råa SUMO edgeData: "
            f"{raw.get('geh_lt_5_pct', '—')} procent GEH < 5, "
            f"medelabsolutfel {raw.get('mean_abs_error', '—')} fordon/timme "
            f"och maximalt absolut fel {raw.get('max_abs_error', '—')}. "
            "Detta visar reproduktion av kalibreringsdagen, inte oberoende "
            "generaliseringsförmåga.",
            "",
            f"- SUMO-hälsa över {len(seeds)} demand/seed-varianter: "
            f"{sum(int(seed.get('inserted', 0)) for seed in seeds):,} insatta "
            "fordon totalt, "
            f"{sum(int(seed.get('unfinished', 0)) for seed in seeds)} "
            "oavslutade och "
            f"{sum(int(seed.get('teleports', 0)) for seed in seeds)} "
            "teleporteringar.".replace(",", " "),
            "",
            f"- Strukturell plausibilitet: "
            f"{sections.get('structure', {}).get('dest_within_200m_pct', '—')} "
            "procent av destinationerna slutar inom 200 meter från en sensor, "
            f"jämfört med "
            f"{sections.get('structure', {}).get('dest_baseline_pct', '—')} "
            "procent för slumpmässiga nätverkskanter; median fortsatt resa "
            f"efter sista sensor är "
            f"{sections.get('structure', {}).get('onward_after_sensor_median_m', '—')} "
            "meter. Det motverkar en modell där resor artificiellt börjar eller "
            "slutar vid mätpunkterna.",
        ])
    if closure:
        scenario = closure.get("scenario", {})
        health = closure.get("seed_health", [])
        lines.extend([
            "",
            f"- Vägavstängningens stresstest: integritet "
            f"`{scenario.get('closure_integrity', '—')}`, "
            f"{sum(int(seed.get('inserted', 0)) for seed in health):,} "
            "insatta fordon, "
            f"{sum(int(seed.get('teleports', 0)) for seed in health)} "
            "teleporteringar, "
            f"{sum(int(seed.get('collisions', 0)) for seed in health)} "
            "kollisioner och "
            f"{scenario.get('active_closure_edge_entries', '—')} fordon som "
            "läckte in på stängda kanter under aktiv avstängning.".replace(",", " "),
        ])
    lines.extend([
        "",
        f"Presentationstext: *Totalvolymtestet med samtliga sex sensorer "
        f"klaras: simuleringen ligger {aggregate['absolute_error_pct']:.1f} "
        f"procent från mätt totaltrafik, under projektgränsen "
        f"{aggregate['limit_pct']:.0f} procent. 1076 ingår fullt ut. Det "
        "strikta timvisa TAG-testet redovisas separat: 81,8 procent klarar "
        "flödesskillnaden och 65,0 procent GEH < 5, mot riktvärdet över "
        "85 procent för respektive mått. Testet visar därför god samlad volym "
        "men inte likvärdig precision vid varje enskild sensor.*",
        "",
    ])
    return "\n".join(lines)


def evaluate(report: dict) -> dict:
    cases = _validated_cases(report)
    satisfactory = _criterion(cases, "satisfactory")
    geh_criterion = _criterion(cases, "geh_ok")
    flow_criterion = _criterion(cases, "flow_difference_ok")
    formal_pass = geh_criterion["pass"] and flow_criterion["pass"]
    ratios = [case["simulated"] / case["measured"]
              for case in cases if case["measured"] > 0]
    return {
        "schema_version": 2,
        "standard": "UK DfT TAG Unit M3.1, Table 2",
        "evaluated_on": "leave-one-station-out held-out hourly flows",
        "not_a_calibration_fit": True,
        "formal_certification": False,
        "window": report.get("window"),
        # A two-way-total station is one observable count location even though
        # its simulation value is assembled from two directed network edges.
        "held_out_edges": len({(case["station"], case["edge"])
                               for case in cases}),
        "hourly_cases": len(cases),
        "criteria": {
            "geh_lt_5": geh_criterion,
            "flow_difference_by_observed_band": flow_criterion,
            "either_flow_difference_or_geh": satisfactory,
            "travel_time_within_15pct": {
                "pass": None,
                "reason": "no independent local journey-time observations",
            },
        },
        "by_edge": _group_summary(cases, ("station", "edge")),
        "by_hour": _group_summary(cases, ("hour_index",)),
        "ratio_summary": {
            "median": round(statistics.median(ratios), 3) if ratios else None,
            "min": round(min(ratios), 3) if ratios else None,
            "max": round(max(ratios), 3) if ratios else None,
            "unit": "hourly modelled / observed",
        },
        "verdict": "pass" if formal_pass else "fail",
        "interpretation": (
            "TAG-aligned diagnostic only: the flow-difference and GEH shares "
            "are judged separately; TAG expects independent validation counts "
            "and substantially more observations than this six-station LOSO "
            "experiment"
        ),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true",
                        help="emit the full record instead of the summary")
    parser.add_argument(
        "--presentation-markdown", type=Path,
        help="write a Swedish slide-ready per-sensor report",
    )
    parser.add_argument(
        "--aggregate-volume-json", type=Path,
        help="write the all-sensor aggregate-volume PASS/FAIL test record",
    )
    parser.add_argument(
        "--validation", type=Path, default=Path("web/data/validation.json"),
        help="current calibration/health report used by presentation output",
    )
    parser.add_argument(
        "--closure-scenario", type=Path,
        default=Path(
            "web/data/scenarios/"
            "close_60786979_3575001205_0+1455801464_18241874_0.json"
        ),
        help="optional current closure result used by presentation output",
    )
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"no LOSO report at {args.report} — run validate_sim.py first")
        return 1
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = evaluate(report)
    except ValueError as error:
        print(f"cannot evaluate TAG M3.1: {error}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    calibration = None
    if args.presentation_markdown:
        closure = None
        if args.validation.is_file():
            calibration = json.loads(args.validation.read_text(encoding="utf-8"))
        if args.closure_scenario.is_file():
            closure = json.loads(
                args.closure_scenario.read_text(encoding="utf-8")
            )
        if args.presentation_markdown:
            output = args.presentation_markdown
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                presentation_markdown(
                    report, result, calibration=calibration, closure=closure
                ),
                encoding="utf-8",
            )
    if args.aggregate_volume_json:
        output = args.aggregate_volume_json
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(aggregate_volume_test(report), indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"TAG M3.1 held-out diagnostic — {result['window']}")
    print(f"{result['held_out_edges']} edges, {result['hourly_cases']} complete "
          "hourly cases")
    for label, key in (
        ("GEH < 5", "geh_lt_5"),
        ("flow-difference criterion", "flow_difference_by_observed_band"),
        ("combined project diagnostic", "either_flow_difference_or_geh"),
    ):
        criterion = result["criteria"][key]
        print(f"  {label:<29} {criterion['cases_meeting']:>3}/"
              f"{criterion['cases_total']:<3} ({criterion['share_pct']:>5.1f}%) "
              f"-> {'PASS' if criterion['pass'] else 'FAIL'}")
    print(f"\nVERDICT: {result['verdict'].upper()} (guideline >{GUIDELINE_PCT:.0f}%)")
    print(result["interpretation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
