#!/usr/bin/env python3
"""Run the 30-pair cold legacy/catalog demand qualification campaign."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.demand.build_lock import child_environment, demand_build_lock
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand.catalog_qualification import (
    PER_TRIAL_HARD_GATES,
    SUITE_HARD_GATES,
)
from traffic_sim.simulation.monthly_demand import (
    LIVE_DEMAND_RELEASE_PRODUCTS,
    restore_live_demand_release,
    snapshot_live_demand_release,
)


FIXTURES = (
    {"name": "weekday", "date": "2027-09-08", "days": 1},
    {"name": "weekend", "date": "2027-09-11", "days": 1},
    {"name": "holiday", "date": "2027-05-13", "days": 1},
    {"name": "mixed", "date": "2027-09-10", "days": 2},
)


def load_suite_gate_record(path: Path) -> dict[str, bool]:
    """Load once-per-campaign suite evidence, failing closed on prose-only claims."""
    payload = json.loads(Path(path).read_text())
    raw = payload.get("gates") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != 2
            or payload.get("kind") != "route_catalog_suite_gate_evidence"
            or not isinstance(raw, dict)):
        raise ValueError("suite-gate record must contain a gates object")
    missing = sorted(set(SUITE_HARD_GATES) - set(raw))
    extra = sorted(set(raw) - set(SUITE_HARD_GATES))
    if missing or extra:
        raise ValueError(
            "suite-gate record does not match the suite contract; missing="
            + ",".join(missing) + " extra=" + ",".join(extra))
    result = {}
    project_root = Path(__file__).resolve().parents[1]
    for gate in SUITE_HARD_GATES:
        record = raw.get(gate)
        tests = record.get("tests") if isinstance(record, dict) else None
        if (not isinstance(record, dict)
                or record.get("status") not in {"pass", "fail"}
                or not isinstance(tests, list) or not tests
                or any(not isinstance(test, str) or not test for test in tests)):
            raise ValueError(
                f"suite gate {gate} needs status and non-empty test evidence")
        for test in tests:
            evidence_path = Path(test.split("::", 1)[0])
            resolved = (project_root / evidence_path).resolve()
            if (evidence_path.is_absolute()
                    or not resolved.is_relative_to(project_root)
                    or not resolved.is_file()):
                raise ValueError(
                    f"suite gate {gate} names missing evidence: {test}")
        result[gate] = record["status"] == "pass"
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-root", type=Path, required=True,
                        help="Verified root produced by build_route_catalog.py")
    parser.add_argument("--suite-gates", type=Path, required=True,
                        help="JSON object containing the non-timing hard gates")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=30,
                        help="Paired trials (minimum 30)")
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--n-total", type=int,
                        default=6000,
                        help="Matched candidate request for both arms")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Resume the exact existing --out campaign")
    args = parser.parse_args()
    if args.trials < 30:
        parser.error("--trials must be at least 30")
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")
    if args.n_total < 1:
        parser.error("--n-total must be positive")
    return args


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1, sort_keys=True))
    os.replace(temporary, path)


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def evaluate_hard_gates(meta: dict, validation: dict) -> dict[str, bool]:
    """Measure build-specific contracts; suite contracts are recorded once."""
    gates = {gate: False for gate in PER_TRIAL_HARD_GATES}
    fit = meta.get("pfe_fit") or {}
    provenance = meta.get("candidate_provenance") or {}
    agent_demand = meta.get("agent_demand") or {}
    structure = meta.get("calibrated_structure") or {}
    vehicles = fit.get("vehicles")
    gates["exact_sensor_targets"] = (
        int(fit.get("integer_sensor_constraints") or 0) > 0
        and fit.get("integer_sensor_exact") == fit.get("integer_sensor_constraints"))
    gates["zero_integer_residual"] = (
        float(fit.get("integer_sensor_max_abs_error") or 0.0) == 0.0
        and float(fit.get("integer_sensor_sum_abs_error") or 0.0) == 0.0)
    gates["population_contract"] = (
        isinstance(vehicles, int) and vehicles > 0
        and provenance.get("vehicles") == vehicles
        and agent_demand.get("n_agents") == vehicles
        and agent_demand.get("n_behavioural_agents") == vehicles)
    gates["sensor_anchor_contract"] = (
        (structure.get("onward_after_last_sensor") or {}).get(
            "n_routes_without_sensor") == 0)
    # A validation report's vocabulary is "pass", "warn" and "missing", and
    # `overall` is derived from those — the string "fail" is never produced
    # anywhere in traffic_sim/confidence/report.py. Comparing against it made
    # confidence_health unconditionally true and reduced candidate_structure
    # to "the metadata exists": two of the seven per-trial hard gates could
    # not fail whatever the build did. Bind each to a section that can
    # actually report a problem instead. (Found 2026-08-26.)
    sections = validation.get("sections")
    sections = sections if isinstance(sections, dict) else {}

    def section_status(name: str) -> str:
        record = sections.get(name)
        return (str(record.get("status")) if isinstance(record, dict)
                else "missing")

    gates["candidate_structure"] = (
        isinstance(structure, dict) and bool(structure)
        and section_status("structure") != "missing")
    gates["route_agent_provenance"] = (
        provenance.get("status") == "pass"
        and provenance.get("vehicles") == vehicles)
    # The confidence stage must have actually described THIS build. A report
    # missing its count-fit, structure or simulation section is not evidence
    # about the arm that produced it, however green the rest looks.
    gates["confidence_health"] = (
        str(validation.get("overall")) in {"pass", "warn"}
        and all(section_status(name) != "missing"
                for name in ("counts_fit", "structure", "simulation")))
    return gates


def run_arm(*, arm: str, fixture: dict, scratch: Path,
            catalog_root: Path, timeout_s: float,
            n_total: int) -> dict:
    candidate_cache = scratch / "candidate-cache"
    day_library = scratch / "day-library"
    trial_catalog = scratch / "route-catalog"
    if arm == "catalog":
        shutil.copytree(catalog_root, trial_catalog)
    command = [
        sys.executable, "-c",
        "import build_sumo_demand as module; module.main()",
        "--start-date", fixture["date"], "--days", str(fixture["days"]),
        "--source", "forecast", "--begin", "00:00", "--end", "24:00",
        "--candidate-source", arm, "--keep-scenarios",
        "--candidate-n-total", str(n_total),
        "--candidate-cache-root", str(candidate_cache),
        "--route-catalog-root", str(trial_catalog),
        "--day-library-root", str(day_library),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1],
        env=child_environment(), capture_output=True, text=True,
        timeout=timeout_s)
    wall_s = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"{arm} {fixture['name']} build failed: "
            + "\n--- stdout ---\n" + completed.stdout[-8000:]
            + "\n--- stderr ---\n" + completed.stderr[-4000:])
    meta = json.loads(Path("sumo/demand_meta.json").read_text())
    validation = json.loads(Path("web/data/validation.json").read_text())
    timings = meta.get("timings_s") or {}
    return {
        "wall_s": round(wall_s, 6),
        "adapter_s": float(timings.get("catalog_restore_or_build", 0.0))
                     + float(timings.get("catalog_adapter", 0.0)),
        "pfe_s": float(timings.get("pfe_variants_and_rounding", 0.0)),
        "peak_rss_bytes": _rss_bytes(),
        "hard_gates": evaluate_hard_gates(meta, validation),
        "build_id": meta.get("build_id"),
        "candidate_source": arm,
        "candidate_n_total": (meta.get("build_options") or {}).get(
            "candidate_n_total"),
        "catalog_keys": dict(
            ((meta.get("candidate_catalog") or {}).get("keys") or {})),
        "catalog_selected_n_total": dict(
            ((meta.get("candidate_catalog") or {}).get(
                "selected_n_total") or {})),
        "vehicles": (meta.get("pfe_fit") or {}).get("vehicles"),
        "pfe_shape_variables": (meta.get("pfe_fit") or {}).get(
            "pfe_shape_variables"),
        "pfe_source_candidates": (meta.get("pfe_fit") or {}).get(
            "pfe_source_candidates"),
        "validation_overall": validation.get("overall"),
    }


def main() -> int:
    args = parse_args()
    suite = load_suite_gate_record(args.suite_gates)
    campaign = {
        "schema_version": 2,
        "kind": "route_catalog_paired_trials",
        "requested_pairs": args.trials,
        "fixtures": list(FIXTURES),
        "candidate_n_total": args.n_total,
        "suite_gate_evidence": {
            "path": str(args.suite_gates),
            "sha256": sha256_file(args.suite_gates),
            "gates": suite,
        },
        "execute": bool(args.execute),
        "trials": [],
    }
    if not args.execute:
        print(json.dumps(campaign, indent=1, sort_keys=True))
        return 0
    if args.out.exists():
        if not args.resume:
            raise FileExistsError(
                f"refusing to overwrite existing campaign: {args.out}; use --resume")
        existing = json.loads(args.out.read_text())
        if (not isinstance(existing, dict)
                or existing.get("schema_version") != campaign["schema_version"]
                or existing.get("kind") != campaign["kind"]
                or existing.get("requested_pairs") != args.trials
                or existing.get("fixtures") != campaign["fixtures"]
                or existing.get("candidate_n_total") != args.n_total
                or existing.get("suite_gate_evidence")
                   != campaign["suite_gate_evidence"]
                or not isinstance(existing.get("trials"), list)
                or len(existing["trials"]) > args.trials):
            raise ValueError("existing campaign does not match this invocation")
        campaign = existing

    extra_products = tuple(LIVE_DEMAND_RELEASE_PRODUCTS) + tuple(
        Path("sumo") / f"catalog_{pool}.{suffix}"
        for pool in ("weekday", "weekend")
        for suffix in ("rou.xml", "meta.json", "validation.json", "template.json")
    ) + (Path("sumo") / "assignment_priors.json",)
    with demand_build_lock():
        snapshot = snapshot_live_demand_release(products=extra_products)
        try:
            for index in range(len(campaign["trials"]), args.trials):
                fixture = FIXTURES[index % len(FIXTURES)]
                order = (("legacy", "catalog") if index % 2 == 0
                         else ("catalog", "legacy"))
                record = {
                    "trial_id": index + 1,
                    "order": "legacy_first" if order[0] == "legacy"
                             else "catalog_first",
                    "day_class": fixture["name"],
                    "date": fixture["date"],
                    "days": fixture["days"],
                }
                for arm in order:
                    with tempfile.TemporaryDirectory(
                            prefix=f"catalog-benchmark-{index}-{arm}-") as raw:
                        record[arm] = run_arm(
                            arm=arm, fixture=fixture, scratch=Path(raw),
                            catalog_root=args.catalog_root,
                            timeout_s=args.timeout_s, n_total=args.n_total)
                campaign["trials"].append(record)
                _atomic_json(args.out, campaign)
                print(f"completed paired catalog trial {index + 1}/{args.trials}",
                      flush=True)
        finally:
            restore_live_demand_release(snapshot)
    _atomic_json(args.out, campaign)
    print(f"wrote {len(campaign['trials'])} paired trials to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
