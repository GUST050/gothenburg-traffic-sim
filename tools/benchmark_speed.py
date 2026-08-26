#!/usr/bin/env python3
"""Measure simulation speed without weakening any simulation contract.

This is deliberately a standalone benchmark rather than a pytest test.  It
runs the real ``run_scenario.py`` entry point in a temporary output directory,
records the exact inputs and SUMO/Python environment, and computes semantic
digests after removing only non-semantic timestamps and run paths.  The tool
can therefore compare serial and bounded-seed execution without treating a
faster but different traffic result as a win.

Examples::

    python3 tools/benchmark_speed.py --trials 3 --workers 1 2 3
    python3 tools/benchmark_speed.py --case baseline --trials 1 --workers 1 2
    python3 tools/benchmark_speed.py --reference old.json --write report.json

The default cases are the frozen plan cases: historical baseline, the known
detour closure, and a microscopic baseline smoke run.  A run can take several
minutes on a laptop, especially for micro; no command is started on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import resource
import secrets
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Launched as `python3 tools/benchmark_speed.py`, Python puts `tools/` on
# sys.path and NOT the repository root, so `load_phase_profile()`'s import of
# the production `run_scenario.validate_phase_profile` could not resolve — the
# defect that aborted the LUNA-PERF-05 campaign after its first scenario had
# already run. Under pytest the root is already importable, which is why the
# in-process tests never saw it. Make the root resolvable in every context;
# the validator itself stays the production one.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCENARIO = ROOT / "run_scenario.py"
KNOWN_CLOSURE = "26842525_26355153_0"


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(payload) -> str:
    """Hash JSON after recursively removing explicitly non-semantic fields."""
    def normalise(value):
        if isinstance(value, dict):
            return {
                key: normalise(item)
                for key, item in sorted(value.items())
                if key not in {"generated_at", "created_at", "finished_at"}
                and key not in {"path", "source_path", "workspace"}
            }
        if isinstance(value, list):
            return [normalise(item) for item in value]
        return value

    encoded = json.dumps(normalise(payload), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _command_output(command: list[str]) -> str | None:
    """stdout of a command that SUCCEEDED, or None.

    `check=False` alone is not enough: `git status --porcelain` exiting 128
    also prints nothing, so an unreadable repository would otherwise be
    recorded as a clean checkout and pass the report gate. Failure must stay
    indistinguishable from "unknown", never from "fine".
    """
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                text=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    # macOS reports bytes, Linux reports KiB.
    return value if sys.platform == "darwin" else value * 1024


def file_fingerprints() -> dict[str, str | None]:
    """Inputs that can change a scenario result, kept in every report."""
    paths = {
        "network": ROOT / "sumo/net.net.xml",
        "graph": ROOT / "web/data/graph.graphml",
        "flows": ROOT / "web/data/flows.json",
        "direction_split": ROOT / "sumo/direction_split.json",
        "demand_meta": ROOT / "sumo/demand_meta.json",
        "bounds": ROOT / "web/data/observability_bounds.json",
        "priors": ROOT / "web/data/assignment_priors.json",
        "calibrated_q50": ROOT / "sumo/calibrated.rou.xml",
        "calibrated_q10": ROOT / "sumo/calibrated_v1.rou.xml",
        "calibrated_q90": ROOT / "sumo/calibrated_v2.rou.xml",
    }
    source_names = (
        "run_scenario.py", "closure_metrics.py", "sumo_runtime.py",
        "build_sumo_demand.py", "demand/calibration.py",
    )
    paths.update({f"source:{name}": ROOT / name for name in source_names})
    return {label: sha256_file(path) for label, path in paths.items()}


def sumo_version() -> str | None:
    try:
        import sumo
        home = Path(sumo.__file__).resolve().parent
        result = subprocess.run([str(home / "bin/sumo"), "--version"],
                                capture_output=True, text=True, timeout=20,
                                check=False)
        # A nonzero exit means the command failed; its stderr is a diagnostic,
        # not a version string, and recording it would let an unattributable
        # run pass the report gate.
        if result.returncode != 0:
            return None
        return (result.stdout or result.stderr).strip() or None
    except (ImportError, OSError, subprocess.SubprocessError):
        return None


def load_phase_profile(path: Path, *, payload: dict, inputs: dict) -> dict:
    """Read, validate and BIND one run's phase sidecar to the run it claims.

    Matching a name and a seed count is not binding: a profile with the same
    scenario_id but different seeds, variants, closures, simulation mode,
    demand build, network build or source generation describes a run nobody
    asked about, and its timings would silently become the reference for an
    optimization. Every frozen identity is therefore compared against the
    scenario payload that was actually published and against this benchmark's
    own input fingerprints.
    """
    from run_scenario import validate_phase_profile

    if not path.exists():
        raise RuntimeError(f"phase timing sidecar missing: {path}")
    profile = json.loads(path.read_text())
    validate_phase_profile(profile)

    scenario = payload.get("scenario") or {}
    spec = payload.get("scenario_spec") or {}

    def closure_identity(records) -> list[tuple]:
        """Compare what identifies a closure, not its serialization.

        The ScenarioSpec carries `closure_type` and access exceptions that the
        run-level record does not; comparing raw dicts would fail every
        closure case for a formatting reason instead of an identity one.
        """
        return [(str(record.get("edge_id")), str(record.get("start_time")),
                 str(record.get("end_time")))
                for record in (records or ())]

    expected = {
        "scenario_id": spec.get("scenario_id") or scenario.get("scenario_id"),
        "simulation_mode": (spec.get("simulation_mode")
                            or scenario.get("simulation_mode")),
        "network_build_id": (spec.get("network_build_id")
                             or scenario.get("network_build_id")),
        "demand_signature": scenario.get("demand_signature"),
        "demand_build_key": scenario.get("demand_build_key"),
        "seed_set": list(spec.get("seed_set") or scenario.get("seed_set") or ()),
        "demand_variant_mapping": {
            str(seed): variant for seed, variant
            in (spec.get("demand_variant_mapping") or {}).items()},
    }
    for field, wanted in expected.items():
        if wanted in (None, "", [], {}):
            raise RuntimeError(
                f"published scenario does not carry {field}; the phase "
                "sidecar cannot be bound to it")
        if profile.get(field) != wanted:
            raise RuntimeError(
                f"phase sidecar {field} does not match the published "
                f"scenario: {profile.get(field)!r} != {wanted!r}")
    if closure_identity(profile.get("closures")) != closure_identity(
            spec.get("closures")):
        raise RuntimeError(
            "phase sidecar closures do not match the published scenario: "
            f"{closure_identity(profile.get('closures'))} != "
            f"{closure_identity(spec.get('closures'))}")

    for label, key in (("source:run_scenario.py", "run_scenario"),
                       ("network", "network"),
                       ("demand_meta", "demand_meta")):
        current = inputs.get(label)
        recorded = (profile.get("source_fingerprints") or {}).get(key)
        if current is None or recorded != current:
            raise RuntimeError(
                f"phase sidecar {key} fingerprint does not match this "
                f"benchmark's inputs: {recorded!r} != {current!r}")
    return profile


def run_case(case: str, workers: int, seeds: int, micro: bool,
             timeout_s: int, root: Path, *, rerouting_threads: int | None = None,
             routing_algorithm: str | None = None,
             minimal_edgedata: bool = False,
             full_edgedata: bool = False) -> dict:
    """Run one frozen case in a private staging directory."""
    closure = case == "closure"
    is_micro = case == "micro" or micro
    name = "baseline" if case in {"baseline", "micro"} else "close_" + KNOWN_CLOSURE
    out_dir = root / "output"
    # The phase sidecar is diagnostic metadata about WHERE the time went. It
    # is bound to the run and reported, but never enters the scenario or
    # trajectory digest, so it can neither create nor mask a result change.
    timing_path = root / "phase_profile.json"
    command = [sys.executable, str(SCENARIO), "--seeds", str(seeds),
               "--seed-workers", str(workers), "--out-dir", str(out_dir),
               "--timing-sidecar", str(timing_path)]
    if rerouting_threads is not None:
        command += ["--rerouting-threads", str(rerouting_threads)]
    if routing_algorithm is not None:
        command += ["--routing-algorithm", routing_algorithm]
    if minimal_edgedata:
        command.append("--minimal-edgedata")
    if full_edgedata:
        command.append("--full-edgedata")
    if closure:
        command += ["--close", KNOWN_CLOSURE]
    if is_micro:
        command.append("--micro")
    started = time.monotonic()
    before_rss = rss_bytes()
    completed = subprocess.run(command, cwd=ROOT, capture_output=True,
                               text=True, timeout=timeout_s, check=False)
    elapsed = time.monotonic() - started
    peak = max(before_rss, rss_bytes())
    log_path = root / "stdout.log"
    log_path.write_text(completed.stdout + "\n--- stderr ---\n" + completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case} workers={workers} failed ({completed.returncode}); "
            f"see {log_path}: {completed.stderr[-1000:]}")

    scenario_path = out_dir / f"{name}.json"
    if not scenario_path.exists():
        raise RuntimeError(f"scenario output missing: {scenario_path}")
    payload = json.loads(scenario_path.read_text())
    trajectories = payload.get("trajectories")
    trajectory_payload = None
    if trajectories:
        trajectory_path = out_dir / trajectories
        if not trajectory_path.exists():
            raise RuntimeError(f"trajectory output missing: {trajectory_path}")
        trajectory_payload = json.loads(trajectory_path.read_text())

    phase_profile = load_phase_profile(timing_path, payload=payload,
                                       inputs=file_fingerprints())
    return {
        "case": case,
        "workers": workers,
        "seeds": seeds,
        "micro": is_micro,
        "rerouting_threads": rerouting_threads,
        "routing_algorithm": routing_algorithm,
        "minimal_edgedata": minimal_edgedata,
        "full_edgedata": full_edgedata,
        "command": command,
        "returncode": completed.returncode,
        "wall_s": round(elapsed, 3),
        "peak_child_rss_bytes": peak,
        "scenario_bytes": scenario_path.stat().st_size,
        "trajectory_bytes": (out_dir / trajectories).stat().st_size
        if trajectories else 0,
        "scenario_digest": canonical_digest(payload),
        "trajectory_digest": (canonical_digest(trajectory_payload)
                               if trajectory_payload is not None else None),
        "phase_profile": phase_profile,
        "seed_health": payload.get("seed_health"),
        "closure_integrity": payload.get("scenario", {}).get("closure_integrity"),
        "log": str(log_path),
    }


HARNESS = Path(__file__).resolve()
CAMPAIGN_SCHEMA_VERSION = 1
# The ACTIVE_GOAL's validated-completion budget. Adoption gates may be tighter
# than this, never looser.
VALIDATED_COMPLETION_S = 10.0
# The p95 wall-time improvement a parallel arm must show over its serial
# reference before it is even a candidate for adoption. Pinned exactly so a
# refrozen contract cannot quietly weaken it (a 1% "improvement" is noise, not
# a speed-up worth trading review budget on).
MIN_P95_IMPROVEMENT = 0.2
# The only thing clearing the adoption gates is allowed to mean. A contract may
# not reword this into deployment/release authority: the gates are diagnostic
# evidence for CONSIDERING a faster arm, never permission to ship it.
ADOPTION_AUTHORIZES_NOTHING = (
    "nothing: clearing these gates is diagnostic evidence for considering the "
    "parallel arm only. It authorizes no deployment, production default change, "
    "release, publication, or relaxation of any existing gate.")
# What the production call path can ACTUALLY execute. `run_case()` derives its
# seeds as 1000+i with the q50/q10/q90 default mapping and closes the module
# constant for its whole run; nothing in a campaign file can change that. So a
# campaign declaring other seeds, another edge or another window would be a
# decorative contract whose timings describe a different run than it claims.
# For this frozen v1 the declaration must equal the capability exactly, and a
# materially different campaign needs a new identity and a pre-outcome
# refreeze rather than a silently-ignored field.
EXECUTABLE_CAMPAIGN = {
    "simulation_mode": "meso",
    # Ordered arms, serial first. `run_case()` passes this straight to
    # run_scenario.py's --seed-workers, so an arm is executable exactly when
    # that flag accepts it; production's own default is untouched. Arm 1 is
    # mandatory because it is the reference the parallel arm's scenario and
    # trajectory digests are compared against — without it a faster arm could
    # not be shown to be result-preserving.
    "worker_arms": [1, 3],
    "seeds": [1000, 1001, 1002],
    "demand_variant_mapping": {"1000": "q50", "1001": "q10", "1002": "q90"},
    "trials": 5,
    "warmup_trials": 0,
    "cache_substitution": False,
    "timeout_seconds": 1800,
}
EXECUTABLE_CASES = (
    {"name": "baseline_whole_day", "case": "baseline", "closed_edges": [],
     "scenario_name": "baseline"},
    {"name": "closure_whole_window", "case": "closure",
     "closed_edges": [KNOWN_CLOSURE],
     "scenario_name": "close_" + KNOWN_CLOSURE},
)
WHOLE_WINDOW = "whole_simulated_window"
# A campaign identity is single-use. Recomputing a content key proves only that
# a contract is unedited — it says nothing about whether that contract has
# already been spent, and a retired identity that still loads can be rerun,
# overwrite its own run root, or have a second report attributed to its first
# approval. The current identity is therefore named here, and every retired one
# is refused by name with the reason it was retired.
#
# The seed-parallel phase-profile campaign line is CLOSED: v6 was the final
# identity and its one approved run was a valid, result-preserving miss (the
# closure whole-window parallel arm did not reach the latency ceiling). There is
# no current campaign — `None` means nothing is executable — and no v7 or other
# identity may be run without a new, separately reviewed line.
CURRENT_CAMPAIGN_ID = None
RETIRED_CAMPAIGN_IDS = {
    "scenario_phase_profile_v1":
        "retired: its run aborted on the script-context import defect and its "
        "frozen harness fingerprint predates the fix",
    "scenario_phase_profile_v2":
        "retired: consumed by an execution whose outcomes are unratified audit "
        "history, so the identity may never be executed again",
    "scenario_phase_profile_v3":
        "retired: spent by its one approved execution on 2026-07-23; its "
        "single-worker report is diagnostic baseline evidence and the identity "
        "may never be executed again",
    "scenario_phase_profile_v4":
        "retired: spent by its one approved paired execution on 2026-07-23 — "
        "result-preserving but not adoptable, because the closure whole-window "
        "parallel arm exceeded the validated-completion ceiling; diagnostic "
        "evidence only, and the identity may never be run again",
    "scenario_phase_profile_v5":
        "retired: its approved one-shot paired identity is spent (2026-07-23) "
        "and was non-adoptable — result-preserving but the closure whole-window "
        "parallel arm stayed over the validated-completion ceiling; diagnostic "
        "evidence only, and the identity may never be run again",
    "scenario_phase_profile_v6":
        "retired: its one approved execution is spent (2026-07-23), "
        "result-preserving but non-adoptable — the closure whole-window parallel "
        "arm missed the hard validated-completion ceiling. This closed the "
        "seed-parallel campaign line; the identity grants no retry, no v7, and "
        "no adoption, release or publication authority",
}
# Provenance a campaign report must carry to be publishable at all. Each of
# these can silently come back null (SUMO not importable, git absent), and a
# report missing them cannot be attributed to a machine or a code state.
REQUIRED_REPORT_FIELDS = ("platform", "cpu_count", "python", "sumo_version",
                          "git_commit", "git_dirty", "campaign")
# The inputs that define this campaign. Verifying only the labels a contract
# happens to list would let a refrozen contract drop the harness fingerprint
# and still pass, so the set is pinned here and required exactly.
REQUIRED_FINGERPRINT_LABELS = (
    "demand_meta", "calibrated_q50", "calibrated_q10", "calibrated_q90",
    "network", "source:run_scenario.py", "harness:benchmark_speed.py")
# The result gates a paired serial/parallel campaign must keep in force. An
# adoption gate that could quietly drop one of these would let a faster arm be
# adopted on weaker evidence than the arm it replaces, which is precisely the
# trade the ACTIVE_GOAL forbids. The set is pinned and required exactly.
EXISTING_RESULT_GATES = (
    "closure_integrity", "hard_failure_refusal", "phase_profile_binding",
    "report_provenance", "seed_health", "semantic_digest_equivalence")
ADOPTION_GATE_FIELDS = {
    "serial_arm_workers": int, "parallel_arm_workers": int,
    "max_semantic_mismatches": int, "max_reference_mismatches": int,
    "max_parallel_p95_wall_seconds": float,
    "min_p95_wall_improvement_fraction": float,
    "required_existing_gates": list, "authorizes": str,
}


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= set("0123456789abcdef"))


class CampaignRefused(Exception):
    """Raised when a frozen campaign cannot be executed exactly as frozen."""


def campaign_content_key(campaign: dict) -> str:
    payload = {key: value for key, value in campaign.items()
               if key != "content_key"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def load_campaign(path: Path) -> dict:
    """Load and strictly validate a frozen phase-profile campaign.

    Every check here happens BEFORE any artifact directory or subprocess
    exists: a campaign that cannot be run exactly as frozen must fail while
    nothing has been created, not half-way through producing evidence for a
    question nobody froze.
    """
    try:
        campaign = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise CampaignRefused(f"cannot read campaign {path}: {error}") from None
    if not isinstance(campaign, dict):
        raise CampaignRefused("campaign must be a JSON object")
    if (campaign.get("schema_version") != CAMPAIGN_SCHEMA_VERSION
            or campaign.get("kind") != "scenario_phase_profile_campaign"):
        raise CampaignRefused("not a phase-profile campaign contract")
    for field in ("campaign_id", "frozen_at", "content_key"):
        if not isinstance(campaign.get(field), str) or not campaign[field]:
            raise CampaignRefused(f"campaign is missing {field}")
    # Identity before contract: a spent or unknown identity is refused here,
    # before the key is even recomputed, so no retired campaign can reach an
    # artifact directory or a subprocess on the strength of being unedited.
    campaign_id = campaign["campaign_id"]
    if campaign_id in RETIRED_CAMPAIGN_IDS:
        raise CampaignRefused(
            f"{campaign_id} is not the executable campaign — "
            f"{RETIRED_CAMPAIGN_IDS[campaign_id]}")
    if CURRENT_CAMPAIGN_ID is None:
        raise CampaignRefused(
            f"{campaign_id} is not the executable campaign — the seed-parallel "
            "phase-profile campaign line is closed and no campaign is "
            "executable; a new line needs a separate review, not a v7")
    if campaign_id != CURRENT_CAMPAIGN_ID:
        raise CampaignRefused(
            f"{campaign_id} is not the executable campaign — only "
            f"{CURRENT_CAMPAIGN_ID} may run")
    if campaign_content_key(campaign) != campaign["content_key"]:
        raise CampaignRefused(
            "campaign content key does not recompute — the frozen contract "
            "has been edited")

    execution = campaign.get("execution")
    if not isinstance(execution, dict):
        raise CampaignRefused("campaign has no execution block")
    required_execution = {
        "simulation_mode": str, "worker_arms": list, "trials": int,
        "warmup_trials": int, "timeout_seconds": int, "seeds": list,
        "demand_variant_mapping": dict, "cache_substitution": bool,
    }
    for field, kind in required_execution.items():
        value = execution.get(field)
        if not isinstance(value, kind) or (kind is bool) != isinstance(value, bool):
            raise CampaignRefused(f"campaign execution.{field} is invalid")
    if execution["simulation_mode"] != "meso":
        raise CampaignRefused("this campaign is mesoscopic by construction")
    if execution["cache_substitution"]:
        raise CampaignRefused("cache substitution is not part of this campaign")
    if execution["warmup_trials"] != 0:
        raise CampaignRefused("this campaign measures fresh trials only")
    for field in ("trials", "timeout_seconds"):
        if execution[field] < 1:
            raise CampaignRefused(f"campaign execution.{field} must be >= 1")
    arms = execution["worker_arms"]
    if (not arms or len(set(arms)) != len(arms)
            or any(isinstance(arm, bool) or not isinstance(arm, int) or arm < 1
                   for arm in arms)):
        raise CampaignRefused(
            "campaign execution.worker_arms must be unique integers >= 1")
    if arms[0] != 1:
        raise CampaignRefused(
            "campaign execution.worker_arms must start with the serial arm 1: "
            "a parallel arm is only adoptable against a serial reference")
    seeds = execution["seeds"]
    mapping = execution["demand_variant_mapping"]
    if (not seeds or len(set(seeds)) != len(seeds)
            or any(isinstance(seed, bool) or not isinstance(seed, int)
                   for seed in seeds)):
        raise CampaignRefused("campaign seeds must be unique integers")
    if {str(seed) for seed in seeds} != set(mapping) or any(
            variant not in {"q10", "q50", "q90"} for variant in mapping.values()):
        raise CampaignRefused(
            "campaign seed/demand-variant mapping is incomplete or invalid")

    for field, expected in EXECUTABLE_CAMPAIGN.items():
        if execution[field] != expected:
            raise CampaignRefused(
                f"campaign execution.{field} is {execution[field]!r}, but the "
                f"production call path executes {expected!r}; a materially "
                "different campaign needs a new identity and a pre-outcome "
                "refreeze")

    validate_adoption_gates(campaign)

    cases = campaign.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CampaignRefused("campaign has no cases")
    if len(cases) != len(EXECUTABLE_CASES):
        raise CampaignRefused(
            f"campaign declares {len(cases)} cases; this frozen version "
            f"executes exactly {len(EXECUTABLE_CASES)}")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise CampaignRefused("campaign case must be an object")
        name, kind = case.get("name"), case.get("case")
        edges = case.get("closed_edges")
        if not isinstance(name, str) or not name or name in seen:
            raise CampaignRefused("campaign case names must be unique strings")
        seen.add(name)
        if kind not in {"baseline", "closure"}:
            raise CampaignRefused(f"campaign case {name} has an invalid kind")
        if not isinstance(edges, list) or any(
                not isinstance(edge, str) or not edge for edge in edges):
            raise CampaignRefused(f"campaign case {name} has invalid closed_edges")
        if (kind == "baseline") != (edges == []):
            raise CampaignRefused(
                f"campaign case {name}: a baseline closes nothing and a "
                "closure must name its directed edges")
        if kind == "closure" and case.get("scenario_name") != "close_" + "+".join(edges):
            raise CampaignRefused(
                f"campaign case {name} scenario_name does not match its edges")

    for declared, executable in zip(cases, EXECUTABLE_CASES):
        for field in ("name", "case", "closed_edges", "scenario_name"):
            if declared.get(field) != executable[field]:
                raise CampaignRefused(
                    f"campaign case {declared.get('name')!r} {field} is "
                    f"{declared.get(field)!r}, but the production call path "
                    f"executes {executable[field]!r}; a materially different "
                    "campaign needs a new identity and a pre-outcome refreeze")
        window = declared.get("closure_window")
        if not isinstance(window, dict):
            raise CampaignRefused(
                f"campaign case {declared['name']} has no machine-readable "
                "closure window")
        duration_s = int(campaign["demand_window"]["n_intervals"]) * 900
        expected_window = {
            "kind": WHOLE_WINDOW if executable["closed_edges"] else "none",
            "start_offset_s": 0 if executable["closed_edges"] else None,
            "end_offset_s": duration_s if executable["closed_edges"] else None,
        }
        if window != expected_window:
            raise CampaignRefused(
                f"campaign case {declared['name']} closure window "
                f"{window!r} is not what `--close` executes "
                f"({expected_window!r})")

    identity = campaign.get("demand_identity")
    if not isinstance(identity, dict) or set(identity) != {
            "demand_build_key", "build_id", "n_variants"}:
        raise CampaignRefused(
            "campaign demand_identity must declare exactly demand_build_key, "
            "build_id and n_variants")
    if (not isinstance(identity["demand_build_key"], str)
            or not identity["demand_build_key"]
            or not isinstance(identity["build_id"], str)
            or not identity["build_id"]
            or isinstance(identity["n_variants"], bool)
            or not isinstance(identity["n_variants"], int)):
        raise CampaignRefused("campaign demand_identity fields are invalid")

    required_report = campaign.get("required_report_fields")
    if not isinstance(required_report, list) or set(required_report) != set(
            REQUIRED_REPORT_FIELDS):
        raise CampaignRefused(
            "campaign required_report_fields must be exactly "
            + ", ".join(sorted(REQUIRED_REPORT_FIELDS)))
    fingerprints = campaign.get("frozen_fingerprints")
    if not isinstance(fingerprints, dict):
        raise CampaignRefused("campaign has no frozen fingerprints")
    if set(fingerprints) != set(REQUIRED_FINGERPRINT_LABELS):
        missing = sorted(set(REQUIRED_FINGERPRINT_LABELS) - set(fingerprints))
        extra = sorted(set(fingerprints) - set(REQUIRED_FINGERPRINT_LABELS))
        raise CampaignRefused(
            "campaign frozen fingerprints must be exactly "
            + ", ".join(REQUIRED_FINGERPRINT_LABELS)
            + (f"; missing {missing}" if missing else "")
            + (f"; unexpected {extra}" if extra else ""))
    for label, digest in fingerprints.items():
        if not _is_sha256(digest):
            raise CampaignRefused(
                f"campaign fingerprint {label} is not a lowercase sha256 digest")
    if not isinstance(campaign.get("demand_window"), dict):
        raise CampaignRefused("campaign has no demand window")
    return campaign


def verify_campaign_inputs(campaign: dict) -> dict:
    """Refuse a campaign whose frozen inputs are no longer what is on disk.

    A stale contract is the dangerous case: it would still run, and its
    timings would silently describe a different demand, network or runner
    than the one that was frozen.
    """
    current = dict(file_fingerprints())
    current["harness:benchmark_speed.py"] = sha256_file(HARNESS)
    drift = []
    for label, frozen in campaign["frozen_fingerprints"].items():
        actual = current.get(label)
        if actual is None:
            drift.append(f"{label}: input missing on disk")
        elif actual != frozen:
            drift.append(f"{label}: {frozen[:12]}… != {actual[:12]}…")
    if drift:
        raise CampaignRefused(
            "frozen campaign inputs have drifted: " + "; ".join(drift))

    window = campaign["demand_window"]
    try:
        meta = json.loads((ROOT / "sumo" / "demand_meta.json").read_text())
    except (OSError, ValueError) as error:
        raise CampaignRefused(f"cannot read the live demand: {error}") from None
    mismatched = {field: (value, meta.get(field))
                  for field, value in window.items()
                  if meta.get(field) != value}
    identity = campaign["demand_identity"]
    mismatched.update({field: (value, meta.get(field))
                       for field, value in identity.items()
                       if meta.get(field) != value})
    if mismatched:
        raise CampaignRefused(
            "the live demand is not the frozen campaign demand: "
            + "; ".join(f"{field} frozen {frozen!r} but live {live!r}"
                        for field, (frozen, live) in sorted(mismatched.items())))
    return {"inputs": current, "demand_identity": dict(identity)}


def campaign_matrix(campaign: dict) -> list[dict]:
    """The exact executable rows the frozen contract implies, in order."""
    execution = campaign["execution"]
    rows = []
    for case in campaign["cases"]:
        for workers in execution["worker_arms"]:
            for trial in range(1, execution["trials"] + 1):
                rows.append({
                    "campaign_case": case["name"],
                    "case": case["case"],
                    "trial": trial,
                    "workers": workers,
                    "seeds": len(execution["seeds"]),
                    "seed_set": list(execution["seeds"]),
                    "demand_variant_mapping": dict(
                        execution["demand_variant_mapping"]),
                    "micro": False,
                    "timeout": execution["timeout_seconds"],
                    "closed_edges": list(case["closed_edges"]),
                    "closure_window": dict(case["closure_window"]),
                    "scenario_name": case["scenario_name"],
                })
    return rows


def validate_adoption_gates(campaign: dict) -> dict:
    """Refuse a paired campaign whose adoption gates could be met by a worse run.

    These gates decide whether a faster worker arm may ever be adopted, so a
    campaign that declares them loosely — nonzero tolerated mismatches, a
    latency ceiling above the stated goal, a token improvement threshold, or a
    shortened list of the result gates it keeps — would be a contract for
    adopting an unproven speed-up. Each bound is therefore checked against the
    strictest value the goal allows, not merely for presence.
    """
    gates = campaign.get("adoption_gates")
    if not isinstance(gates, dict):
        raise CampaignRefused("campaign has no machine-readable adoption_gates")
    for field, kind in ADOPTION_GATE_FIELDS.items():
        value = gates.get(field)
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
            gates[field] = value
        if not isinstance(value, kind) or isinstance(value, bool):
            raise CampaignRefused(f"adoption_gates.{field} is invalid")
    arms = campaign["execution"]["worker_arms"]
    if gates["serial_arm_workers"] != arms[0]:
        raise CampaignRefused(
            "adoption_gates.serial_arm_workers must be the campaign's serial arm")
    if gates["parallel_arm_workers"] not in arms[1:]:
        raise CampaignRefused(
            "adoption_gates.parallel_arm_workers must be a frozen parallel arm")
    for field in ("max_semantic_mismatches", "max_reference_mismatches"):
        if gates[field] != 0:
            raise CampaignRefused(
                f"adoption_gates.{field} must be 0: a parallel arm that changes "
                "the result is not a speed-up")
    if not 0 < gates["max_parallel_p95_wall_seconds"] <= VALIDATED_COMPLETION_S:
        raise CampaignRefused(
            "adoption_gates.max_parallel_p95_wall_seconds must be positive and "
            f"no looser than the {VALIDATED_COMPLETION_S:g}s validated-"
            "completion target")
    if gates["min_p95_wall_improvement_fraction"] != MIN_P95_IMPROVEMENT:
        raise CampaignRefused(
            "adoption_gates.min_p95_wall_improvement_fraction must be exactly "
            f"the frozen {MIN_P95_IMPROVEMENT:.0%} floor ({MIN_P95_IMPROVEMENT}); "
            "a weaker threshold would adopt noise as a speed-up")
    if gates["authorizes"] != ADOPTION_AUTHORIZES_NOTHING:
        raise CampaignRefused(
            "adoption_gates.authorizes must be the literal no-authority "
            "statement: these gates grant no deployment or release authority")
    if tuple(gates["required_existing_gates"]) != EXISTING_RESULT_GATES:
        raise CampaignRefused(
            "adoption_gates.required_existing_gates must list exactly "
            + ", ".join(EXISTING_RESULT_GATES))
    return gates


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sample")
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def evaluate_adoption_gates(report: dict, campaign: dict) -> dict:
    """Score a finished paired report against the frozen adoption gates.

    Declaring gates in a contract nobody evaluates is decoration, so this is
    the executable side of `validate_adoption_gates()`. It reads a report only;
    it runs nothing, writes nothing, and never decides deployment — `adoptable`
    means the measured evidence cleared every frozen bound, not that anything
    may ship.
    """
    gates = validate_adoption_gates(campaign)
    execution = campaign["execution"]
    serial_arm = gates["serial_arm_workers"]
    parallel_arm = gates["parallel_arm_workers"]
    canonical_seeds = sorted(execution["seeds"])
    trials = execution["trials"]
    findings, per_case = [], {}

    def flag(name):
        if name not in findings:
            findings.append(name)

    def positive_finite(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0)

    # Explicit, present mismatch lists. A MISSING list is unknown, not empty:
    # reading a dropped key as "zero mismatches" would let an incomplete report
    # pass the equivalence gate it never actually evaluated.
    for key in ("semantic_mismatches", "reference_mismatches"):
        if not isinstance(report.get(key), list):
            flag(f"missing_{key}")
    if len(report.get("semantic_mismatches") or []) > gates["max_semantic_mismatches"]:
        flag("semantic_digest_equivalence")
    if len(report.get("reference_mismatches") or []) > gates["max_reference_mismatches"]:
        flag("reference_equivalence")

    rows = report.get("cases")
    if not isinstance(rows, list) or not rows:
        flag("missing_cases")
        return {
            "campaign_id": campaign["campaign_id"],
            "content_key": campaign["content_key"],
            "per_case": {}, "failed_gates": sorted(findings),
            "adoptable": False, "authorizes": gates["authorizes"],
        }

    # The report must be EXACTLY the frozen matrix: every (case, arm, trial)
    # present, unique, and nothing extra. Baseline-only or one-trial-per-arm
    # evidence must not be adoptable just because the rows it does have look
    # healthy.
    expected = {(row["campaign_case"], row["case"], row["workers"], row["trial"])
                for row in campaign_matrix(campaign)}
    observed = [(r.get("campaign_case"), r.get("case"), r.get("workers"),
                 r.get("trial")) for r in rows]
    if len(observed) != len(set(observed)):
        flag("duplicate_rows")
    if set(observed) != expected:
        flag("matrix_incomplete")

    # Per-row completeness, fail-closed. Any row missing timing, phase binding,
    # complete canonical seed health, or (for closures) verified integrity
    # disqualifies the whole campaign.
    for row in rows:
        if row.get("returncode") != 0:
            flag("hard_failure_refusal")
        if not positive_finite(row.get("wall_s")):
            flag("invalid_wall_time")
        if not isinstance(row.get("phase_profile"), dict):
            flag("phase_profile_binding")
        health = row.get("seed_health")
        seeds = (sorted(h.get("seed") for h in health if isinstance(h, dict))
                 if isinstance(health, list) else None)
        if seeds != canonical_seeds:
            flag("seed_health")
        else:
            for h in health:
                if (h.get("collisions") or h.get("teleports")
                        or h.get("running_at_end") or h.get("waiting_at_end")
                        or not isinstance(h.get("loaded"), int)
                        or not isinstance(h.get("inserted"), int)
                        or h.get("loaded") != h.get("inserted")):
                    flag("seed_health")
        if row.get("case") == "closure" and \
                row.get("closure_integrity") != "verified_clean":
            flag("closure_integrity")

    # Independently derive paired equivalence rather than trusting the report's
    # own mismatch list: for each case/trial the parallel arm's scenario and
    # trajectory digests must be present, well-formed, and equal to the serial
    # arm's. A missing digest fails closed.
    by_pair = {}
    for row in rows:
        by_pair.setdefault((row.get("campaign_case"), row.get("trial")),
                           {})[row.get("workers")] = row
    for (_case, _trial), by_worker in by_pair.items():
        serial_row = by_worker.get(serial_arm)
        parallel_row = by_worker.get(parallel_arm)
        if not serial_row or not parallel_row:
            continue                              # matrix_incomplete covers this
        for field in ("scenario_digest", "trajectory_digest"):
            serial_v = serial_row.get(field)
            parallel_v = parallel_row.get(field)
            if not _is_sha256(serial_v) or not _is_sha256(parallel_v):
                flag("digest_missing")
            elif serial_v != parallel_v:
                flag("paired_digest_mismatch")

    for case in sorted({row.get("campaign_case") for row in rows}):
        arm_p95, complete = {}, True
        for arm in (serial_arm, parallel_arm):
            walls = [row["wall_s"] for row in rows
                     if row.get("campaign_case") == case
                     and row.get("workers") == arm
                     and positive_finite(row.get("wall_s"))]
            if len(walls) != trials:
                flag(f"missing_arm:{case}:w{arm}")
                complete = False
                break
            arm_p95[arm] = percentile(walls, 0.95)
        if not complete:
            continue
        improvement = ((arm_p95[serial_arm] - arm_p95[parallel_arm])
                       / arm_p95[serial_arm])
        entry = {
            "serial_p95_wall_s": arm_p95[serial_arm],
            "parallel_p95_wall_s": arm_p95[parallel_arm],
            "p95_wall_improvement_fraction": improvement,
            "meets_latency_ceiling": (arm_p95[parallel_arm]
                                      <= gates["max_parallel_p95_wall_seconds"]),
            "meets_improvement_floor": (
                improvement >= gates["min_p95_wall_improvement_fraction"]),
        }
        per_case[case] = entry
        if not entry["meets_latency_ceiling"]:
            flag(f"parallel_latency_ceiling:{case}")
        if not entry["meets_improvement_floor"]:
            flag(f"p95_improvement_floor:{case}")

    expected_cases = {case["name"] for case in campaign["cases"]}
    adoptable = not findings and set(per_case) == expected_cases
    return {
        "campaign_id": campaign["campaign_id"],
        "content_key": campaign["content_key"],
        "per_case": per_case,
        "failed_gates": sorted(findings),
        "adoptable": adoptable,
        "authorizes": gates["authorizes"],
    }


def validate_campaign_report(report: dict, campaign: dict) -> None:
    """Refuse to publish a campaign report that cannot be attributed.

    Presence is not enough: a report can carry a `campaign` block whose matrix
    or demand identity describes a different run, and `sumo_version()` and the
    git lookups return None when their tools are absent. Every required field
    is therefore checked for TYPE and, where it has a frozen counterpart, for
    exact equality with it.
    """
    missing = [field for field in REQUIRED_REPORT_FIELDS
               if report.get(field) in (None, "", [], {})]
    if missing:
        raise CampaignRefused(
            "campaign report is missing required provenance: "
            + ", ".join(sorted(set(missing))))

    checks = {
        "platform": lambda v: isinstance(v, str) and bool(v.strip()),
        "python": lambda v: isinstance(v, str) and bool(v.strip()),
        "sumo_version": lambda v: isinstance(v, str) and bool(v.strip()),
        "cpu_count": lambda v: isinstance(v, int) and not isinstance(v, bool)
        and v > 0,
        "git_commit": lambda v: isinstance(v, str) and len(v) == 40
        and set(v) <= set("0123456789abcdef"),
        "git_dirty": lambda v: isinstance(v, bool),
    }
    invalid = sorted(field for field, ok in checks.items()
                     if not ok(report.get(field)))
    if invalid:
        raise CampaignRefused(
            "campaign report provenance is invalid: " + ", ".join(invalid))

    bound = report.get("campaign")
    if not isinstance(bound, dict):
        raise CampaignRefused("campaign report has no campaign binding")
    for field in ("campaign_id", "content_key", "executable_matrix",
                  "demand_identity"):
        if not bound.get(field):
            raise CampaignRefused(f"campaign report is missing campaign.{field}")
    if (bound["campaign_id"] != campaign["campaign_id"]
            or bound["content_key"] != campaign["content_key"]):
        raise CampaignRefused(
            "campaign report identity does not match the frozen campaign")
    if bound["executable_matrix"] != campaign_matrix(campaign):
        raise CampaignRefused(
            "campaign report executable matrix is not the frozen campaign's")
    if bound["demand_identity"] != campaign["demand_identity"]:
        raise CampaignRefused(
            "campaign report demand identity is not the frozen campaign's")

    inputs = report.get("inputs")
    if not isinstance(inputs, dict):
        raise CampaignRefused("campaign report carries no input provenance")
    for label in REQUIRED_FINGERPRINT_LABELS:
        frozen = campaign["frozen_fingerprints"][label]
        if inputs.get(label) != frozen:
            raise CampaignRefused(
                f"campaign report input provenance is missing or drifted: {label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", action="append", choices=("baseline", "closure", "micro"),
                        help="case(s) to run; default is all three frozen cases")
    parser.add_argument("--workers", nargs="+", type=int, default=None,
                        help="seed-worker counts to compare (default: 1 2)")
    parser.add_argument("--seeds", type=int, default=None)
    parser.add_argument("--trials", type=int, default=None,
                        help="fresh trials per case/worker pair (default 1; use 3 for adoption)")
    parser.add_argument("--timeout", type=int, default=None,
                        help="per-case timeout in seconds")
    parser.add_argument("--rerouting-threads", type=int, default=None,
                        help="opt-in SUMO rerouting threads for an ad-hoc "
                             "paired benchmark")
    parser.add_argument("--routing-algorithm",
                        choices=("dijkstra", "astar", "CH", "CHWrapper"),
                        default=None,
                        help="opt-in SUMO routing algorithm for an ad-hoc "
                             "paired benchmark")
    parser.add_argument("--minimal-edgedata", action="store_true",
                        help="compatibility flag for the adopted "
                             "entered/timeLoss-only edgeData field set")
    parser.add_argument("--full-edgedata", action="store_true",
                        help="full-field rollback arm for an ad-hoc paired "
                             "benchmark")
    parser.add_argument("--campaign", type=Path, default=None,
                        help="run the exact matrix of a frozen phase-profile "
                             "campaign contract instead of the ad-hoc cases")
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate a campaign and print its executable "
                             "matrix without running anything")
    parser.add_argument("--reference", type=Path,
                        help="optional previous report; semantic mismatches are reported")
    parser.add_argument("--write", type=Path, default=None,
                        help="write the JSON report to this path")
    parser.add_argument("--artifact-dir", type=Path, default=None,
                        help="persistent directory for logs and staged outputs "
                             "(default: /private/tmp/gs-speed-<timestamp>)")
    args = parser.parse_args()
    if args.preflight_only and args.campaign is None:
        parser.error("--preflight-only applies to --campaign runs")
    if args.campaign is not None:
        # The frozen contract IS the matrix. Silently letting a flag override
        # it would produce evidence for a campaign that was never frozen.
        overrides = [name for name, value in (("--case", args.case),
                                              ("--workers", args.workers),
                                              ("--seeds", args.seeds),
                                              ("--trials", args.trials),
                                              ("--timeout", args.timeout),
                                              ("--rerouting-threads",
                                               args.rerouting_threads),
                                              ("--routing-algorithm",
                                               args.routing_algorithm),
                                              ("--minimal-edgedata",
                                               args.minimal_edgedata or None),
                                              ("--full-edgedata",
                                               args.full_edgedata or None))
                     if value is not None]
        if overrides:
            parser.error("a frozen campaign cannot be overridden by "
                         + ", ".join(overrides))
        return args
    if not args.case:
        args.case = ["baseline", "closure", "micro"]
    if args.workers is None:
        args.workers = [1, 2]
    if args.seeds is None:
        args.seeds = 3
    if args.trials is None:
        args.trials = 1
    if args.timeout is None:
        args.timeout = 1800
    if args.seeds < 1 or args.trials < 1 or any(w < 1 for w in args.workers):
        parser.error("--seeds, --trials and --workers must be >= 1")
    if args.rerouting_threads is not None and args.rerouting_threads < 1:
        parser.error("--rerouting-threads must be >= 1")
    if args.minimal_edgedata and args.full_edgedata:
        parser.error("--minimal-edgedata and --full-edgedata are mutually "
                     "exclusive")
    return args


def main() -> int:
    args = parse_args()
    campaign = matrix = verified = None
    if args.campaign is not None:
        # Everything that can refuse this run happens here, before an artifact
        # directory is created or a subprocess is spawned.
        try:
            campaign = load_campaign(args.campaign)
            verified = verify_campaign_inputs(campaign)
            matrix = campaign_matrix(campaign)
        except CampaignRefused as refusal:
            print(f"campaign refused: {refusal}", file=sys.stderr)
            return 2
        if args.preflight_only:
            print(json.dumps({
                "campaign_id": campaign["campaign_id"],
                "campaign_content_key": campaign["content_key"],
                "campaign_path": str(args.campaign),
                "frozen_inputs_verified": sorted(campaign["frozen_fingerprints"]),
                "demand_window": campaign["demand_window"],
                "demand_identity_verified": verified["demand_identity"],
                "executable_matrix": matrix,
                "runs_planned": len(matrix),
                "executed": False,
                "artifact_dir": None,
                "note": "preflight only: nothing was run and nothing was written",
            }, indent=2, sort_keys=True))
            return 0
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report = {
        "schema_version": 1,
        "started_at": started,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "git_commit": None,
        "git_dirty": None,
        "sumo_version": sumo_version(),
        "inputs": (verified["inputs"] if verified is not None
                   else file_fingerprints()),
        "cases": [],
    }
    commit = _command_output(["git", "rev-parse", "HEAD"])
    status = _command_output(["git", "status", "--porcelain"])
    report["git_commit"] = (commit or "").strip() or None
    # A clean tree is an empty stdout with exit 0; a failed command is None,
    # which the campaign report gate refuses rather than reading as clean.
    report["git_dirty"] = None if status is None else bool(status.strip())

    if campaign is not None:
        report["campaign"] = {
            "campaign_id": campaign["campaign_id"],
            "content_key": campaign["content_key"],
            "path": str(args.campaign),
            "frozen_at": campaign["frozen_at"],
            "executable_matrix": matrix,
            "demand_identity": verified["demand_identity"],
        }
        try:
            # Provenance is checked before a single case runs: a report that
            # could not be attributed must not cost hours of SUMO first.
            validate_campaign_report(report, campaign)
        except CampaignRefused as refusal:
            print(f"campaign refused: {refusal}", file=sys.stderr)
            return 2

    artifact_dir = args.artifact_dir or Path("/private/tmp") / (
        "gs-speed-" + time.strftime("%Y%m%d-%H%M%S") + "-" +
        secrets.token_hex(2))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    report["artifact_dir"] = str(artifact_dir)
    run_root = artifact_dir
    if matrix is not None:
        for row in matrix:
            trial_root = run_root / (f"{row['campaign_case']}-w{row['workers']}"
                                     f"-t{row['trial']}")
            trial_root.mkdir()
            result = run_case(row["case"], row["workers"], row["seeds"],
                              row["micro"], row["timeout"], trial_root)
            result["trial"] = row["trial"]
            result["campaign_case"] = row["campaign_case"]
            report["cases"].append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    else:
        for case in args.case:
            for workers in args.workers:
                for trial in range(args.trials):
                    trial_root = run_root / f"{case}-w{workers}-t{trial + 1}"
                    trial_root.mkdir()
                    routing_kwargs = {
                        key: value for key, value in (
                            ("rerouting_threads", args.rerouting_threads),
                            ("routing_algorithm", args.routing_algorithm),
                            ("minimal_edgedata",
                             args.minimal_edgedata if args.minimal_edgedata
                             else None),
                            ("full_edgedata",
                             args.full_edgedata if args.full_edgedata
                             else None),
                        ) if value is not None
                    }
                    result = run_case(case, workers, args.seeds, False,
                                      args.timeout, trial_root,
                                      **routing_kwargs)
                    result["trial"] = trial + 1
                    report["cases"].append(result)
                    print(json.dumps(result, sort_keys=True), flush=True)

    # A worker-count change is accepted only when it is result-preserving. The
    # report remains useful when a run differs: the non-zero return value makes
    # CI/manual adoption stop before anyone calls a faster result accurate.
    mismatches = []
    grouped: dict[str, dict[int, dict]] = {}
    for item in report["cases"]:
        grouped.setdefault(item["case"], {}).setdefault(item["trial"], {})[
            item["workers"]] = item
    for case, trials in grouped.items():
        for trial, by_worker in trials.items():
            serial = by_worker.get(1)
            if not serial:
                continue
            for workers, candidate in by_worker.items():
                if workers == 1:
                    continue
                for field in ("scenario_digest", "trajectory_digest"):
                    if candidate.get(field) != serial.get(field):
                        mismatches.append({"case": case, "trial": trial,
                                           "workers": workers, "field": field,
                                           "serial": serial.get(field),
                                           "candidate": candidate.get(field)})
    reference_mismatches = []
    if args.reference:
        reference = json.loads(args.reference.read_text())
        reference_rows = {
            (row.get("case"), row.get("workers"), row.get("trial")): row
            for row in reference.get("cases", [])
        }
        for row in report["cases"]:
            key = (row["case"], row["workers"], row["trial"])
            old = reference_rows.get(key)
            if old is None:
                reference_mismatches.append({"key": key, "reason": "missing_reference_row"})
                continue
            for field in ("scenario_digest", "trajectory_digest"):
                if row.get(field) != old.get(field):
                    reference_mismatches.append({
                        "key": key, "field": field,
                        "reference": old.get(field), "current": row.get(field),
                    })
    if campaign is not None:
        try:
            validate_campaign_report(report, campaign)
        except CampaignRefused as refusal:
            print(f"campaign report refused: {refusal}", file=sys.stderr)
            return 2
    report["semantic_mismatches"] = mismatches
    report["reference_mismatches"] = reference_mismatches
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # Bind success to the frozen adoption gates. A campaign that finished but
    # missed a case, ran an unhealthy seed, breached the latency ceiling, or
    # failed to beat its serial arm by the frozen margin is NOT an adoptable
    # result, and the exit status must say so — otherwise a green run could be
    # read as a proven speed-up it never was.
    adoption = None
    if campaign is not None:
        adoption = evaluate_adoption_gates(report, campaign)
        report["adoption"] = adoption

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"wrote {args.write}")
    summary = {"semantic_mismatches": mismatches,
               "reference_mismatches": reference_mismatches,
               "n_runs": len(report["cases"])}
    if adoption is not None:
        summary["adoptable"] = adoption["adoptable"]
        summary["failed_gates"] = adoption["failed_gates"]
    print(json.dumps(summary, indent=2))
    failed = bool(mismatches or reference_mismatches)
    if adoption is not None and not adoption["adoptable"]:
        failed = True
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
