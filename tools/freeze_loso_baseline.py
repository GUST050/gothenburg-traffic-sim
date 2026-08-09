#!/usr/bin/env python3
"""Freeze a corrected multi-seed LOSO/TAG sweep as tracked baseline evidence.

Work package 0 of ``docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md``.

The sweep itself lives under the Git-ignored ``runs/`` tree, so a later actor
cannot re-derive the reported numbers from the repository alone.  This tool
reads one complete sweep root, re-checks its acceptance gate, and writes a
small tracked summary that binds the numbers to the exact manifest, inputs,
seeds and candidate pools that produced them.

The summary is DIAGNOSTIC evidence about held-out recovery.  It is not a
release artifact, not a calibration fit, and not a TAG certification: TAG
expects independent validation counts and far more observations than six
stations on one day can supply.

Every reported field is labelled with one of the plan's evidence classes
(``measurement``, ``derived_exact``, ``diagnostic``, ``model_assumption``,
``decision``) so a hypothesis can never be read as an observation.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.fingerprint import sha256_file  # noqa: E402

SCHEMA_VERSION = 1
KIND = "corrected_loso_sweep_baseline"
EXPECTED_PROTOCOL = "loso_pfe_meso_v5"
EXPECTED_KIND = "corrected_loso_multi_seed_sweep"
MINIMUM_SEEDS = 6
TAG_GUIDELINE_PCT = 85.0
DEFAULT_MANIFEST = ROOT / "runs/loso-corrected-v2-20260808/manifest.json"
DEFAULT_OUTPUT = ROOT / "validation/corrected_loso_sweep_v2_summary.json"
V6_REQUIRED_RUN_ARTIFACTS = {
    "sumo/demand_meta.json",
    "sumo/candidates.meta.json",
    "sumo/assignment_priors.json",
    "sumo/prior_flows.json",
    "sumo/direction_split.json",
    "web/data/observability.json",
    "loso_report.json",
    "tag_report.json",
}
ARTIFACT_BOUND_PROTOCOLS = {"loso_pfe_meso_v6", "loso_pfe_meso_v7"}

FIELD_SEMANTICS = {
    "measured_daily_total": "measurement",
    "simulated_daily_total": "diagnostic",
    "daily_ratio": "diagnostic",
    "tag_satisfactory_share_pct": "diagnostic",
    "geh_ok_pct": "diagnostic",
    "candidate_pool_sha256": "derived_exact",
    "inputs_sha256": "derived_exact",
    "manifest_sha256": "derived_exact",
    "assignment_ceiling_complete": "derived_exact",
    "seeds": "derived_exact",
    "tag_guideline_pct": "model_assumption",
    "verdict": "decision",
    "interpretation": "decision",
}


def _median(values: list[float]) -> float:
    return round(float(statistics.median(values)), 4)


def check_gate(manifest: dict, tag_reports: dict[int, dict],
               input_drift: list[str],
               expected_protocol: str = EXPECTED_PROTOCOL) -> list[str]:
    """Return the acceptance-gate failures for a sweep, newline-free.

    Pure function over already-loaded evidence so the contract can be tested
    against synthetic manifests without running a 2 h sweep.  An empty list
    means the sweep satisfies section 6 of the improvement plan.
    """
    failures: list[str] = []
    if manifest.get("kind") != EXPECTED_KIND:
        failures.append(
            f"manifest kind is {manifest.get('kind')!r}, expected {EXPECTED_KIND!r}")
    if manifest.get("status") != "complete":
        failures.append(
            f"source manifest status is {manifest.get('status')!r}, not 'complete'")

    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        failures.append("source manifest carries no runs")
        return failures

    declared = manifest.get("seeds")
    if isinstance(declared, list) and len(declared) != len(runs):
        failures.append(
            f"manifest declares {len(declared)} seeds but stores {len(runs)} runs")

    seeds = [run.get("seed") for run in runs]
    if len(set(seeds)) != len(seeds):
        failures.append("sweep seeds are not unique")
    if len(seeds) < MINIMUM_SEEDS:
        failures.append(
            f"sweep has {len(seeds)} seeds, fewer than the required {MINIMUM_SEEDS}")

    protocols = {run.get("protocol") for run in runs}
    if protocols != {expected_protocol}:
        failures.append(
            "per-seed protocols differ or are wrong: "
            + ", ".join(sorted(str(protocol) for protocol in protocols)))

    pools = [run.get("candidate_pool_sha256") for run in runs]
    if any(pool is None for pool in pools):
        failures.append("a run is missing its candidate-pool hash")
    elif len(set(pools)) != len(pools):
        failures.append(
            "candidate-pool hashes repeat: the seeds are not distinct "
            "route-support realizations")

    for run in runs:
        seed = run.get("seed")
        if expected_protocol in ARTIFACT_BOUND_PROTOCOLS:
            artifacts = run.get("artifacts_sha256")
            if not isinstance(artifacts, dict) \
                    or set(artifacts) != V6_REQUIRED_RUN_ARTIFACTS \
                    or any(not value for value in artifacts.values()):
                failures.append(
                    f"seed {seed} lacks the complete artifact-bound run hash set")
            if not run.get("demand_build_id"):
                failures.append(f"seed {seed} lacks its demand build id")
        if not run.get("all_held_edges_received_assignment_ceiling"):
            failures.append(f"seed {seed} has a held edge without an assignment ceiling")
        for edge in run.get("edges", []):
            if not edge.get("assignment_ceiling"):
                failures.append(
                    f"seed {seed} station {edge.get('station')} component edge "
                    "missing an assignment ceiling")

    for run in runs:
        seed = run.get("seed")
        tag = tag_reports.get(seed)
        if tag is None:
            failures.append(f"seed {seed} has no TAG report")
            continue
        criterion = tag.get("criteria", {}).get("either_flow_difference_or_geh", {})
        if criterion.get("share_pct") != run.get("tag_satisfactory_share_pct"):
            failures.append(
                f"seed {seed} TAG share disagrees with its manifest summary")
        if criterion.get("cases_total") != run.get("tag_total_cases"):
            failures.append(
                f"seed {seed} TAG case count disagrees with its manifest summary")

    if input_drift:
        failures.append(
            "bound inputs no longer match the manifest: " + ", ".join(input_drift))
    return failures


def _station_rows(runs: list[dict], tag_reports: dict[int, dict]) -> list[dict]:
    """Aggregate per-station evidence across seeds.

    Station 107 is one physical two-way count location assembled from two
    directed edges; it is aggregated exactly once, under the ``station_total``
    evaluation key its LOSO fold publishes.
    """
    by_station: dict[str, dict] = {}
    for run in runs:
        seed = run["seed"]
        tag_by_edge = {
            (row["station"], row["edge"]): row
            for row in tag_reports.get(seed, {}).get("by_edge", [])
        }
        for edge in run["edges"]:
            station = edge["station"]
            record = by_station.setdefault(station, {
                "station": station,
                "measurement_semantics": edge["measurement_semantics"],
                "evaluation_key": edge["evaluation"],
                "component_edges": list(edge["component_edges"]),
                "daily_ratio_by_seed": {},
                "geh_ok_pct_by_seed": {},
                "tag_satisfactory_share_pct_by_seed": {},
            })
            record["daily_ratio_by_seed"][str(seed)] = edge["ratio"]
            record["geh_ok_pct_by_seed"][str(seed)] = edge["geh_ok_pct"]
            tag_row = tag_by_edge.get((station, edge["evaluation"]))
            if tag_row is not None:
                record["tag_satisfactory_share_pct_by_seed"][str(seed)] = \
                    tag_row["share_pct"]

    rows = []
    for station, record in sorted(by_station.items()):
        ratios = list(record["daily_ratio_by_seed"].values())
        geh = list(record["geh_ok_pct_by_seed"].values())
        tag = list(record["tag_satisfactory_share_pct_by_seed"].values())
        record["daily_ratio_median"] = _median(ratios)
        record["daily_ratio_min"] = round(min(ratios), 4)
        record["daily_ratio_max"] = round(max(ratios), 4)
        record["geh_ok_pct_median"] = _median(geh)
        record["tag_satisfactory_share_pct_median"] = _median(tag) if tag else None
        rows.append(record)
    return rows


def build_summary(manifest: dict, manifest_path: Path, manifest_sha256: str,
                  tag_reports: dict[int, dict], input_drift: list[str],
                  git_identity: dict,
                  expected_protocol: str = EXPECTED_PROTOCOL) -> dict:
    """Assemble the tracked baseline record from one complete sweep."""
    failures = check_gate(
        manifest, tag_reports, input_drift,
        expected_protocol=expected_protocol)
    if failures:
        raise ValueError("; ".join(failures))

    runs = manifest["runs"]
    shares = [run["tag_satisfactory_share_pct"] for run in runs]
    stations = _station_rows(runs, tag_reports)
    seeds_meeting_guideline = sum(
        share > TAG_GUIDELINE_PCT for share in shares)
    all_seeds_pass = seeds_meeting_guideline == len(runs)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "frozen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "plan": "docs/plans/LOSO_POOL_PICKER_IMPROVEMENT_PLAN_2026-08-08.md",
        "evidence_class": "diagnostic",
        "source": {
            # runs/ is Git-ignored: the sweep root is diagnostic evidence, not
            # a release artifact, so the numbers are frozen here instead.
            "manifest_path": str(manifest_path.relative_to(ROOT)),
            "manifest_sha256": manifest_sha256,
            "manifest_status": manifest["status"],
            "manifest_tracked_in_git": False,
            "date": manifest["date"],
            "git_head": manifest["git_head"],
            "inputs_sha256": dict(manifest["inputs_sha256"]),
            "inputs_verified_against_worktree": not input_drift,
        },
        "git_identity_at_freeze": git_identity,
        "protocol": {
            "name": expected_protocol,
            "simulation_mode": "meso",
            "evaluation": "leave-one-station-out, same-window",
            "held_station_rule": (
                "held sensor removed before structural assignment loading; "
                + ("all edges covered by its measurements or derived priors "
                   "receive the normal unmeasured-edge ceiling; "
                   if expected_protocol in ARTIFACT_BOUND_PROTOCOLS else
                   "held component edges receive the normal unmeasured-edge "
                   "ceiling; ")
                +
                "measurement-derived bounds, direction priors and corridor "
                "priors involving the held station are excluded"),
            "two_way_total_rule": (
                "a two-way-total station is compared once, raw total against "
                "the sum of the simulated directions"),
            "incomplete_hours": "excluded, never converted to zero",
        },
        "seeds": [run["seed"] for run in runs],
        "candidate_pool_sha256_by_seed": {
            str(run["seed"]): run["candidate_pool_sha256"] for run in runs},
        **({
            "demand_build_id_by_seed": {
                str(run["seed"]): run["demand_build_id"] for run in runs},
            "artifacts_sha256_by_seed": {
                str(run["seed"]): dict(run["artifacts_sha256"])
                for run in runs},
        } if expected_protocol in ARTIFACT_BOUND_PROTOCOLS else {}),
        "tag": {
            "standard": "UK DfT TAG Unit M3.1, Table 2",
            "criterion": "either flow-difference band or GEH < 5",
            "tag_guideline_pct": TAG_GUIDELINE_PCT,
            "formal_certification": False,
            "per_seed": [
                {
                    "seed": run["seed"],
                    "satisfactory_cases": run["tag_satisfactory_cases"],
                    "total_cases": run["tag_total_cases"],
                    "satisfactory_share_pct": run["tag_satisfactory_share_pct"],
                    "verdict": run["tag_verdict"],
                }
                for run in runs
            ],
            "share_pct_median": _median(shares),
            "share_pct_min": round(min(shares), 4),
            "share_pct_max": round(max(shares), 4),
            "seeds_meeting_guideline": seeds_meeting_guideline,
        },
        "stations": stations,
        "assignment_ceiling_complete": all(
            run["all_held_edges_received_assignment_ceiling"] for run in runs),
        "field_semantics": dict(FIELD_SEMANTICS),
        "verdict": "pass" if all_seeds_pass else "fail",
        "interpretation": (
            "Every draw meets the strict TAG guideline of more than 85% "
            "satisfactory hourly cases."
            if all_seeds_pass else
            "At least one draw is below the strict TAG guideline of more than "
            "85% satisfactory hourly cases. Inspect the per-station and "
            "per-seed evidence; no single seed may be used to compare "
            "pipeline versions."),
        "limitations": [
            "Diagnostic evidence, not a release artifact and not a TAG "
            "certification; TAG expects independent validation counts and "
            "many more observations than six stations on one day.",
            "Six paired draws bound seed sensitivity but are too few for a "
            "strong asymptotic statistical claim.",
            "Held-out recovery is measured on 2025-09-16 only; temporal "
            "transfer to an untouched date is a separate, unfinished step.",
        ],
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def _git_identity() -> dict:
    def _git(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                    text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    head = _git("rev-parse", "HEAD")
    diff = _git("diff", "HEAD")
    status = _git("status", "--porcelain")
    import hashlib
    return {
        "head": head.strip() if head else None,
        "worktree_dirty": bool(status and status.strip()),
        "diff_sha256": hashlib.sha256((diff or "").encode()).hexdigest(),
    }


def load_tag_reports(manifest: dict, run_root: Path) -> dict[int, dict]:
    reports: dict[int, dict] = {}
    for run in manifest.get("runs", []):
        seed = run.get("seed")
        path = run_root / f"seed-{seed}" / "tag_report.json"
        if path.is_file():
            reports[seed] = json.loads(path.read_text())
    return reports


def input_drift(manifest: dict) -> list[str]:
    """Return bound inputs whose current bytes differ from the sweep's."""
    drift = []
    for name, expected in sorted(manifest.get("inputs_sha256", {}).items()):
        actual = sha256_file(ROOT / name)
        if actual != expected:
            drift.append(name if actual else f"{name} (missing)")
    return drift


def stored_run_artifact_drift(manifest: dict, run_root: Path) -> list[str]:
    """Verify the per-seed report bytes retained inside a v6 evidence root."""
    drift = []
    for run in manifest.get("runs", []):
        expected = run.get("artifacts_sha256") or {}
        seed = run.get("seed")
        for name in ("loso_report.json", "tag_report.json"):
            actual = sha256_file(run_root / f"seed-{seed}" / name)
            if actual != expected.get(name):
                drift.append(f"seed-{seed}/{name}")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-protocol", default=EXPECTED_PROTOCOL,
                        help="exact per-seed protocol to freeze; use a new "
                             "output path for a new protocol")
    parser.add_argument("--check", action="store_true",
                        help="verify the gate and the tracked summary without "
                             "rewriting it")
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() \
        else ROOT / args.manifest
    if not manifest_path.is_file():
        print(f"no sweep manifest at {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())
    tag_reports = load_tag_reports(manifest, manifest_path.parent)
    drift = input_drift(manifest)
    if args.expected_protocol in ARTIFACT_BOUND_PROTOCOLS:
        drift.extend(stored_run_artifact_drift(
            manifest, manifest_path.parent))

    try:
        summary = build_summary(
            manifest, manifest_path, sha256_file(manifest_path), tag_reports,
            drift, _git_identity(), expected_protocol=args.expected_protocol)
    except ValueError as error:
        print(f"acceptance gate FAILED: {error}")
        return 1

    output = args.output if args.output.is_absolute() else ROOT / args.output
    if args.check:
        if not output.is_file():
            print(f"no frozen baseline at {output}")
            return 1
        stored = json.loads(output.read_text())
        volatile = {"frozen_at", "git_identity_at_freeze"}
        differing = [
            key for key in set(stored) | set(summary)
            if key not in volatile and stored.get(key) != summary.get(key)
        ]
        if differing:
            print("frozen baseline no longer matches its source: "
                  + ", ".join(sorted(differing)))
            return 1
        print(f"frozen baseline matches {manifest_path.name}")
        return 0

    if output.exists():
        print(f"refusing to overwrite frozen baseline: {output}")
        return 1
    if args.expected_protocol != EXPECTED_PROTOCOL \
            and output == DEFAULT_OUTPUT:
        print("a new protocol requires a new --output path")
        return 1

    _write_json(output, summary)
    tag = summary["tag"]
    print(f"froze {len(summary['seeds'])} seeds -> {output}")
    print(f"  TAG satisfactory share: median {tag['share_pct_median']}%, "
          f"range {tag['share_pct_min']}–{tag['share_pct_max']}%, "
          f"{tag['seeds_meeting_guideline']}/{len(summary['seeds'])} above the "
          f"{TAG_GUIDELINE_PCT:.0f}% guideline")
    for station in summary["stations"]:
        print(f"  station {station['station']:<5} median ratio "
              f"{station['daily_ratio_median']:.3f}  "
              f"({station['daily_ratio_min']:.3f}–"
              f"{station['daily_ratio_max']:.3f})  median TAG "
              f"{station['tag_satisfactory_share_pct_median']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
