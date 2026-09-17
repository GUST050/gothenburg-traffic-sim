#!/usr/bin/env python3
"""Decision record for a future full WindowCostIndex build; runs nothing.

Reads the bounded nonzero canary and the earlier WCI evidence from disk,
verifies every content key, separates what is measured from what is modelled,
and derives a clearly labelled 30-build-key model of the raw phase and of the
peak memory footprint. It starts no index build, no oracle, no SUMO process
and no demand build. The output is append-only diagnostic evidence
(``release_evidence: false``) and never a WCI runtime.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.benchmarks import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_full_build_decision_v2"
BUILD_KEYS = 30
DAILY_UNITS = 1950
GIB = 1024 ** 3
#: The month ledger the index must beat (profile wall_time_s).
LEDGER_BASELINE_S = 7320.348
#: Half of this machine's 24 GiB: room for the OS and the file cache.
FOOTPRINT_LIMIT_BYTES = 12 * GIB
SWAP_GROWTH_LIMIT_BYTES = 1 * GIB
VARIANT_FILES = ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                 "calibrated_v2.rou.xml")
PRODUCTION_SOURCES = (
    "tools/build_window_cost_index.py",
    "tools/closure_effect_eligibility.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/window_cost_index.py",
)


def _load(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A published record, refused unless its content key describes it."""
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items()
            if key != "content_key"}
    if common.digest(body) != record.get("content_key"):
        raise SystemExit(f"{path}: content key does not describe the record")
    return record, {"path": str(path), "sha256": common.file_sha256(path),
                    "content_key": record["content_key"]}


def _fit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    """Least-squares (intercept, slope)."""
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
             / sum((x - mean_x) ** 2 for x in xs))
    return mean_y - slope * mean_x, slope


def _increments(ys: Sequence[float]) -> List[float]:
    return [later - earlier for earlier, later in zip(ys, ys[1:])]


def _span(intercept: float, slopes: Sequence[float],
          n: int) -> Dict[str, float]:
    values = [intercept + slope * n for slope in slopes]
    return {"low": min(values), "high": max(values)}


def _run_phases(canary: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Per key count: production, measurement-only and process overhead."""
    rows = []
    for run in canary["runs"]:
        phases = run["phases"]
        end = run["samples"][-1]
        measured = sum(item["wall_s"] for item in phases.values())
        rows.append({
            "build_keys": len(run["build_keys"]),
            "daily_units": run["daily_units"],
            "bound_inputs_s": phases["bound_inputs"]["wall_s"],
            "raw_index_records_s": phases["raw_index_records"]["wall_s"],
            "measurement_only_prepass_s": (
                phases["filter_prepass_diagnostic"]["wall_s"]),
            "process_overhead_s": end["wall_s"] - measured,
            "process_wall_s": end["wall_s"],
            "production_timings_s": run["production_timings"],
            "lifetime_max_phys_footprint_bytes": (
                end["lifetime_max_phys_footprint_bytes"]),
            "max_rss_bytes": end["max_rss_bytes"],
            "counters": run["counters"],
            "affected_vehicles": run["affected_vehicles_total"],
        })
    return rows


def _share_of_month(canary, inventory, edge) -> Dict[str, Any]:
    """How much of the month the canary's build keys carry."""
    archives = inventory["inventory"]["archives"]
    keys = canary["runs"][-1]["build_keys"]
    route_bytes = {
        key: sum((Path(item["archive"]) / name).stat().st_size
                 for name in VARIANT_FILES)
        for key, item in archives.items()}
    table = inventory["inventory"]["crossings"][edge]
    crossings = {key: sum(table[variant].get(key, 0) for variant in table)
                 for key in archives}
    sampled_bytes = sum(route_bytes[key] for key in keys)
    sampled_crossings = sum(crossings[key] for key in keys)
    return {
        "archives": len(archives),
        "sampled_keys": keys,
        "route_bytes_month": sum(route_bytes.values()),
        "route_bytes_sampled": sampled_bytes,
        "crossings_month": sum(crossings.values()),
        "crossings_sampled": sampled_crossings,
        "scale_by_route_bytes": sum(route_bytes.values()) / sampled_bytes,
        "scale_by_crossings": sum(crossings.values()) / sampled_crossings,
    }


def _raw_model(rows, resolution, share) -> Dict[str, Any]:
    counts = [row["build_keys"] for row in rows]
    raw = [row["raw_index_records_s"] for row in rows]
    intercept, slope = _fit(counts, raw)
    linear = _span(intercept, [slope] + _increments(raw), BUILD_KEYS)
    index_s = [item["wall_s"] for item in resolution["index_builds"]]
    index_median = sorted(index_s)[len(index_s) // 2]
    per_key = resolution["per_key"]
    validation_month = sum(item["median_wall_s"]
                           for item in per_key.values())
    validation_sampled = sum(per_key[key]["median_wall_s"]
                             for key in share["sampled_keys"])
    compute_sampled = raw[-1] - index_median - validation_sampled
    component = [index_median + validation_month + compute_sampled * scale
                 for scale in (share["scale_by_route_bytes"],
                               share["scale_by_crossings"])]
    low = min(linear["low"], min(component))
    high = max(linear["high"], max(component))
    return {
        "kind": "modelled",
        "label": ("modelled raw phase (_raw_index_records) for 30 build "
                  "keys; NOT a full WindowCostIndex time"),
        "assumptions": [
            "the archive index is built once over the 30 archive "
            "directories, as the canary counters show at 1-3 keys",
            "validation is paid once per build key (median of three fresh "
            "processes per key, measured for all 30 keys)",
            "parse, grouping and window aggregation scale with the sampled "
            "archives' share of route bytes or of crossing vehicles",
            "no memory pressure: the modelled retain footprint below makes "
            "this assumption doubtful on a 24 GiB machine",
            "warm file cache, as in every input measurement",
        ],
        "linear_in_build_keys": {
            "intercept_s": intercept, "slope_s_per_key": slope,
            "increments_s": _increments(raw),
            "predicted_s": intercept + slope * BUILD_KEYS,
            "span_over_increments_s": linear,
        },
        "component": {
            "archive_index_build_s": {"median": index_median,
                                      "runs": index_s,
                                      "kind": "measured at full scale"},
            "validation_month_s": {"value": validation_month,
                                   "kind": "measured for all 30 keys"},
            "validation_sampled_s": validation_sampled,
            "compute_sampled_s": compute_sampled,
            "predicted_by_route_bytes_s": component[0],
            "predicted_by_crossings_s": component[1],
        },
        "range_s": {"low": low, "high": high},
        "range_min": {"low": low / 60, "high": high / 60},
    }


def _memory_model(rows, retain, machine_bytes) -> Dict[str, Any]:
    counts = [row["build_keys"] for row in rows]
    footprint = [row["lifetime_max_phys_footprint_bytes"] for row in rows]
    rss = [row["max_rss_bytes"] for row in rows]
    intercept, slope = _fit(counts, footprint)
    rss_intercept, rss_slope = _fit(counts, rss)
    stream = next(item for item in retain["summaries"]
                  if item["mode"] == "stream")
    loads = stream["footprint_after_each_load_mb"]
    stream_high_mb = loads[-1] + max(_increments(loads)) * (
        BUILD_KEYS - len(loads))
    return {
        "kind": "modelled",
        "machine_memory_bytes": machine_bytes,
        "retain_path": {
            "basis": "canary lifetime max phys footprint at 1-3 build keys",
            "slope_bytes_per_key": slope,
            "predicted_bytes": intercept + slope * BUILD_KEYS,
            "span_bytes": _span(intercept,
                                [slope] + _increments(footprint),
                                BUILD_KEYS),
            "predicted_max_rss_bytes": (rss_intercept
                                        + rss_slope * BUILD_KEYS),
            "failed_build_peak_footprint_bytes": (
                retain["failed_build_reference"][
                    "peak_memory_footprint_bytes"]),
            "earlier_projection_bytes": (
                retain["footprint_projection"]["projected_bytes"]),
            "production_loop_retains_all_archives": True,
        },
        "stream_path": {
            "basis": ("diagnostic stream loop at 3 archives (exact against "
                      "retain-3); growth per archive extrapolated with the "
                      "largest observed step"),
            "footprint_after_each_load_mb": loads,
            "predicted_high_bytes": stream_high_mb * 1024 * 1024,
            "is_production": False,
        },
    }


def _replication(canary, replication, rows) -> Optional[Dict[str, Any]]:
    """The same canary on unchanged production code, run again."""
    if replication is None:
        return None
    again = _run_phases(replication)
    counts = [row["build_keys"] for row in again]
    raw = [row["raw_index_records_s"] for row in again]
    intercept, slope = _fit(counts, raw)
    cpu_ratio = [
        _cpu(second, "raw_index_records") / _cpu(first, "raw_index_records")
        for first, second in zip(canary["runs"], replication["runs"])]
    return {
        "kind": "measured replication; outputs compared exactly",
        "identical_outputs": all(
            first[field] == second[field]
            for first, second in zip(canary["runs"], replication["runs"])
            for field in ("unit_digests", "unit_costs", "records_digest")
        ) and (canary["oracle"]["oracle_unit_digests"]
               == replication["oracle"]["oracle_unit_digests"]),
        "raw_index_records_s": raw,
        "wall_ratio_to_canary": [
            second["raw_index_records_s"] / first["raw_index_records_s"]
            for first, second in zip(rows, again)],
        "cpu_ratio_to_canary": cpu_ratio,
        "reading": ("CPU time moved with wall time in every phase, including "
                    "the measurement-only prepass, so the difference is the "
                    "machine's performance state, which no input records; a "
                    "budget must assume the slower state"),
        "linear_in_build_keys": {
            "intercept_s": intercept, "slope_s_per_key": slope,
            "predicted_s": intercept + slope * BUILD_KEYS,
            "span_over_increments_s": _span(
                intercept, [slope] + _increments(raw), BUILD_KEYS),
        },
        "rows": again,
    }


def _cpu(run: Mapping[str, Any], phase: str) -> float:
    item = run["phases"][phase]
    return item["user_s"] + item["sys_s"]


def _oracle(canary, replication) -> Dict[str, Any]:
    walls = [canary["oracle"]["wall_s"]]
    if replication is not None:
        walls.append(replication["oracle"]["wall_s"])
    units = canary["oracle"]["daily_units"]
    return {
        "kind": "measured at one scale only",
        "daily_units": units,
        "wall_s": walls,
        "per_unit_s": [wall / units for wall in walls],
        "separation": ("measurement overhead of the canary; not part of the "
                       "production raw phase"),
        "month": {
            "status": "unknown",
            "reason": ("only one scale (195 units) is measured, so no growth "
                       "law is supported. A full build does not recompute "
                       "this oracle: it reads the bound daily-cost cache of "
                       "the month ledger, and no such cache exists for an "
                       "effect-eligible edge"),
            "naive_per_unit_extrapolation_s": {
                "low": min(walls) / units * DAILY_UNITS,
                "high": max(walls) / units * DAILY_UNITS,
                "use": "orientation only; not a model",
            },
        },
    }


def _comparison(resolution, raw_model) -> Dict[str, Any]:
    old = resolution["model"]["old"]["wall"]
    new = resolution["model"]["new"]["wall"]
    observed = resolution["comparison_with_old_run"]["observed"]
    return {
        "archive_resolution": {
            "comparable": True,
            "old_modelled_s": old["total_s"],
            "old_index_part_s": old["index_part_s"],
            "new_measured_s": new["total_s"],
            "new_index_part_s": new["index_part_s"],
        },
        "raw_phase_vs_failed_run": {
            "comparable": "same function, different edge and code",
            "old_observed_preparation_s": (
                observed["raw_index_records_preparation_s"]),
            "old_real_s": observed["real_s"],
            "new_modelled_s": raw_model["range_s"],
            "caveat": ("the failed run priced an edge with no crossings, so "
                       "its window aggregation cost almost nothing; the new "
                       "model prices a carried edge"),
        },
        "ledger_baseline": {
            "baseline_s": LEDGER_BASELINE_S,
            "comparable_with": "a full end-to-end build only",
            "raw_model_share": {
                "low": raw_model["range_s"]["low"] / LEDGER_BASELINE_S,
                "high": raw_model["range_s"]["high"] / LEDGER_BASELINE_S},
            "caveat": ("the baseline priced the zero edge and includes "
                       "1,075.8 s of observer overhead; the raw phase is "
                       "only one part of a build, so no benefit is claimed"),
        },
    }


def _budget(raw_model, memory) -> Dict[str, Any]:
    high = raw_model["range_s"]["high"]
    return {
        "raw_phase_soft_s": math.ceil(1.5 * high / 10) * 10,
        "raw_phase_hard_s": math.ceil(2.5 * high / 10) * 10,
        "whole_build_hard_s": LEDGER_BASELINE_S,
        "whole_build_reason": ("past the ledger baseline a cold benefit is "
                               "impossible, so the build answers nothing"),
        "footprint_limit_bytes": FOOTPRINT_LIMIT_BYTES,
        "swap_growth_limit_bytes": SWAP_GROWTH_LIMIT_BYTES,
        "retain_path_fits": (memory["retain_path"]["span_bytes"]["low"]
                             <= FOOTPRINT_LIMIT_BYTES),
        "stream_path_fits": (memory["stream_path"]["predicted_high_bytes"]
                             <= FOOTPRINT_LIMIT_BYTES),
    }


STOP_CONDITIONS = (
    ("profile_not_effect_eligible",
     "the bound profile spec is not effect-eligible under "
     "closure_effect_eligibility_v1, or its inventory evidence drifted"),
    ("input_drift",
     "any bound source, profile, manifest, archive, catalog or metadata hash "
     "differs from its binding"),
    ("footprint_over_limit",
     "sampled lifetime max phys footprint exceeds footprint_limit_bytes"),
    ("swap_growth_over_limit",
     "system swap use grows by more than swap_growth_limit_bytes"),
    ("raw_phase_over_hard_budget",
     "_raw_index_records wall time exceeds raw_phase_hard_s"),
    ("whole_build_over_baseline",
     "total wall time exceeds the 7,320.348 s ledger baseline"),
    ("per_unit_resolution_returned",
     "archive_index_build > 1, archive_validate > 30 or "
     "archive_json_read > 30 + 4 * 30"),
    ("population_mismatch",
     "daily units != 1,950, variant records != 5,850 or parents != 1,690"),
    ("oracle_incomplete_or_different",
     "any bound daily-cost record is missing or differs field by field"),
    ("zero_priced_month",
     "the raw phase finds no affected vehicle: a zero month is no decision "
     "evidence"),
    ("forbidden_side_effect",
     "a SUMO process starts or a demand archive is created"),
)


#: What the future full-build supervisor must read; design only, not built.
SUPERVISOR_DESIGN = (
    {"metric": "raw_phase_wall_s", "source": "phase timer around "
     "_raw_index_records", "soft": 1090, "hard": 1820,
     "action": "warn at soft, stop at hard"},
    {"metric": "whole_build_wall_s", "source": "process start to publish",
     "hard": LEDGER_BASELINE_S, "action": "stop"},
    {"metric": "memory_bytes", "source": "max of process-tree RSS and "
     "lifetime max physical footprint (proc_pid_rusage)",
     "hard": FOOTPRINT_LIMIT_BYTES, "action": "stop"},
    {"metric": "swap_growth_bytes", "source": "system vm.swapusage used, "
     "relative to the start", "hard": SWAP_GROWTH_LIMIT_BYTES,
     "action": "stop"},
    {"metric": "telemetry_available", "source": "every sample above",
     "hard": "a sample that cannot be read", "action": "stop (fail closed)"},
    {"metric": "archive_index_build", "source": "io_phases counter",
     "expected": 1, "action": "stop on any other value"},
    {"metric": "archive_validate", "source": "io_phases counter",
     "expected": "one per build key (30)", "action": "stop above"},
    {"metric": "variant_parses_per_file", "source": "parse counter",
     "expected": 1, "action": "stop above"},
    {"metric": "population", "source": "raw phase and ledger",
     "expected": {"daily_units": 1950, "variant_records": 5850,
                  "parents": 1690}, "action": "stop on mismatch"},
    {"metric": "sumo_processes", "source": "process census before, during "
     "and after", "expected": 0, "action": "stop"},
    {"metric": "affected_vehicles_total", "source": "raw phase records",
     "expected": "> 0", "action": "stop: a zero month is no evidence"},
    {"metric": "oracle_identity", "source": "compare_oracle",
     "expected": "complete and field-identical", "action": "stop"},
    {"metric": "provider_identity", "source": "provider identities vs the "
     "bound daily-cost cache identities", "expected": "identical",
     "action": "stop"},
    {"metric": "ledger_identity", "source": "compare_decision_ledgers",
     "expected": "identical", "action": "publish NOT_ADOPTED"},
    {"metric": "input_drift", "source": "bound source, profile, manifest, "
     "archive, catalog and effect-inventory hashes", "expected": "unchanged",
     "action": "stop"},
)


def _stream_section(stream_ab) -> Optional[Dict[str, Any]]:
    """The measured streaming raw loop, when its A/B evidence is given."""
    if stream_ab is None:
        return None
    record, ref = stream_ab
    analysis = record["analysis"]
    return {
        "evidence": ref,
        "status": record["status"],
        "production_loop": ("stream_one_archive_at_a_time"
                            if record["status"] == "PASS" else "retain"),
        "footprint_bytes": analysis["footprint_bytes"],
        "footprint_reduction_vs_retain": (
            analysis["footprint_reduction_vs_retain"]),
        "month_footprint_model": analysis["month_footprint_model"],
        "raw_wall_s": analysis["raw_wall_s"],
        "time": analysis["time"],
        "gates": analysis["gates"],
    }


def _answers(rows, memory, budget, profile_edges, inventory,
             share) -> Dict[str, Any]:
    counters = [row["counters"] for row in rows]
    verdicts = {row["edge_id"]: row for row in inventory["rows"]}
    rejected = {edge: verdicts.get(edge, {}).get("reason_codes")
                for edge in profile_edges}
    return {
        "1_is_the_8h41m_defect_gone": {
            "answer": "yes, for its cause",
            "evidence": {
                "units": [row["daily_units"] for row in rows],
                "archive_index_build": [item["archive_index_build"]
                                        for item in counters],
                "archive_validate": [item["archive_validate"]
                                     for item in counters],
                "archive_json_read": [item["archive_json_read"]
                                      for item in counters],
            },
            "reading": ("one index build and one validation per build key at "
                        "65, 130 and 195 units, where the failed run rebuilt "
                        "the index for each of 1,950 units"),
        },
        "2_measured_directly": [
            "_bound_inputs (independent of key count)",
            "archive index build over all 30 archive directories",
            "validation of all 30 build keys",
            "raw phase and exactness at 1, 2 and 3 build keys "
            f"(1/{share['scale_by_route_bytes']:.3f} of the month's route "
            "bytes)",
            "independent oracle for 195 units",
            "retain and stream footprints at 1-3 archives",
        ],
        "3_modelled_or_unmeasured": {
            "modelled": ["raw phase compute for 30 archives",
                         "peak footprint for 30 build keys"],
            "unmeasured": [
                "month ledger and daily-cost cache for an effect-eligible "
                "edge (the full build's oracle input)",
                "index write and load_index at full scale",
                "indexed adoption ledger over 1,690 parents",
                "compare_decision_ledgers on a nonzero month",
                "behaviour under memory pressure",
            ],
        },
        "4_can_retain_reach_19_gb": {
            "answer": "yes",
            "predicted_bytes": memory["retain_path"]["predicted_bytes"],
            "span_bytes": memory["retain_path"]["span_bytes"],
            "reading": ("the production loop still parses and indexes every "
                        "archive before the unit loop; only the diagnostic "
                        "stream loop stays small"),
        },
        "5_would_a_full_build_be_decision_useful_now": {
            "answer": "no",
            "profile_edges_rejected": rejected,
            "reasons": [
                "the only month profile binds an edge that "
                "closure_effect_eligibility_v1 rejects; it would price zero "
                "again",
                "its timing would not represent a carried edge, whose window "
                "aggregation dominates the canary's raw phase",
                "the retain footprint is modelled above the memory limit",
            ],
        },
        "6_budget": budget,
        "7_stop_conditions": [{"id": key, "condition": text}
                              for key, text in STOP_CONDITIONS],
    }


def _profile_edges(profile: Mapping[str, Any]) -> List[str]:
    path = Path(profile["bound_spec"]["path"])
    spec = json.loads(path.read_text(encoding="utf-8"))
    return list(spec.get("spec", spec)["directed_edges"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--canary", type=Path, required=True)
    parser.add_argument("--replication", type=Path)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--resolution-model", type=Path, required=True)
    parser.add_argument("--retain-stream", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--stream-ab", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    canary, canary_ref = _load(args.canary)
    replication, replication_ref = (_load(args.replication)
                                    if args.replication else (None, None))
    inventory, inventory_ref = _load(args.inventory)
    resolution, resolution_ref = _load(args.resolution_model)
    retain, retain_ref = _load(args.retain_stream)
    if canary.get("status") != "PASS" or (
            replication is not None
            and replication.get("status") != "PASS"):
        raise SystemExit("a decision needs a passing nonzero canary")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))

    rows = _run_phases(canary)
    edge = canary["canary_spec"]["directed_edges"][0]
    share = _share_of_month(canary, inventory, edge)
    raw_model = _raw_model(rows, resolution, share)
    memory = _memory_model(rows, retain,
                           canary["runtime"]["hw_memsize_bytes"])
    budget = _budget(raw_model, memory)
    repeat = _replication(canary, replication, rows)
    if repeat is not None:
        if not repeat["identical_outputs"]:
            raise SystemExit("the replication priced the canary differently")
        low = min(raw_model["range_s"]["low"],
                  repeat["linear_in_build_keys"]["span_over_increments_s"][
                      "low"])
        raw_model["range_over_machine_states_s"] = {
            "low": low, "high": raw_model["range_s"]["high"],
            "note": ("the replication ran about twice as fast on identical "
                     "code and inputs; the component model uses index and "
                     "validation times measured in the slower state")}
    stream = _stream_section(_load(args.stream_ab) if args.stream_ab
                             else None)
    if stream is not None and stream["status"] == "PASS":
        budget["production_raw_loop"] = stream["production_loop"]
        budget["stream_path_fits"] = stream["gates"][
            "month_model_under_limit_with_margin"]
    record = {
        "schema": SCHEMA,
        "release_evidence": False,
        "decision": "DO_NOT_START_FULL_BUILD",
        "claim_boundary": ("diagnostic decision record; nothing here is a "
                           "full WindowCostIndex time or an adoption"),
        "driver": common.driver_binding(Path(__file__)),
        "production_sources": common.source_bindings(PRODUCTION_SOURCES),
        "git": common.git_state(),
        "inputs": {
            "canary": canary_ref,
            "replication_canary": replication_ref,
            "inventory": inventory_ref,
            "archive_index_resolution_model": resolution_ref,
            "retain_stream_memory": retain_ref,
            "stream_ab": stream["evidence"] if stream else None,
            "profile": {"path": str(args.profile),
                        "sha256": common.file_sha256(args.profile),
                        "content_key": profile.get("content_key"),
                        "bound_spec": profile["bound_spec"]},
        },
        "canary_edge": edge,
        "phases_by_key_count": rows,
        "share_of_month": share,
        "raw_phase_model": raw_model,
        "replication": repeat,
        "memory_model": memory,
        "streaming_raw_loop": stream,
        "supervisor_design": {
            "status": "design only; not implemented",
            "metrics": list(SUPERVISOR_DESIGN),
        },
        "oracle": _oracle(canary, replication),
        "comparison": _comparison(resolution, raw_model),
        "answers": _answers(rows, memory, budget, _profile_edges(profile),
                            inventory, share),
        "prerequisites_before_a_full_build": [
            "a month case frozen under closure_effect_eligibility_v1, or an "
            "explicit decision to adopt the canary spec",
            "a month ledger and daily-cost cache for that case (the bound "
            "oracle and the baseline the build must beat), under its own "
            "decision and budget",
            {"requirement": "a production raw loop whose modelled "
             "footprint fits footprint_limit_bytes, proven exact against "
             "the retain loop",
             "met": bool(stream and stream["status"] == "PASS")},
            "a supervisor that reads every supervisor_design metric and "
            "enforces every stop condition",
        ],
    }
    if stream is not None and stream["status"] == "PASS":
        answers = record["answers"]
        answers["4_can_retain_reach_19_gb"].update({
            "production_loop_now": stream["production_loop"],
            "stream_month_high_bytes": stream["month_footprint_model"][
                "stream_high_at_30_bytes"],
            "reading": ("the retain loop still would; production now streams "
                        "one archive at a time and is modelled far below "
                        "the limit"),
        })
        reasons = answers["5_would_a_full_build_be_decision_useful_now"][
            "reasons"]
        reasons[:] = [reason for reason in reasons
                      if "retain footprint" not in reason] + [
            "no supervisor enforces the stop conditions yet"]
    record["output_sha256"] = common.digest({
        key: record[key] for key in (
            "decision", "phases_by_key_count", "share_of_month",
            "raw_phase_model", "replication", "memory_model",
            "streaming_raw_loop", "supervisor_design", "oracle",
            "comparison", "answers")})
    published = common.publish(args.out, record)
    print(json.dumps({
        "decision": published["decision"],
        "raw_phase_range_s": raw_model["range_s"],
        "raw_phase_over_machine_states_s": raw_model.get(
            "range_over_machine_states_s"),
        "retain_footprint_gib": {
            key: value / GIB for key, value in
            memory["retain_path"]["span_bytes"].items()},
        "budget": budget,
        "content_key": published["content_key"],
        "output_sha256": published["output_sha256"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
