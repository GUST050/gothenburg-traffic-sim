#!/usr/bin/env python3
"""Item 1, Stage 3 — the isolated day-reuse policy experiment.

A calibrated day's identity carries ``pool_composition``: the set of day-type
geometry pools the window it was calibrated inside draws from. The PFE solves
every quarter over the WHOLE shape pool, so a Thursday calibrated in a
weekday-only window saw a smaller variable set than the same Thursday in a
window that also contained a Saturday. Those are two entries, and that is why
some of the frozen June repetitions are repetitions at all.

Two cheaper identities would collapse them:

``canonical_union``
    every day is calibrated inside the full ordered ``POOL_KEYS``;
``day_type_local``
    every day is calibrated inside its own pool key alone.

**Either is a performance fix only if it produces the same day.** This tool
measures that and nothing else. It builds the two control dates under each
candidate composition in an isolated working copy and compares the result
against BOTH context controls already in the day library. If the outputs
differ the policy is a MODEL CHANGE and is rejected for Item 1; describing it
as a safe speed-up would be wrong.

What this tool deliberately does not do
---------------------------------------
* It never activates a policy. A pass is a candidate for the small
  overlapping-window check, not a deployment.
* It never writes to ``sumo/``, ``runs/demand-days`` or the published
  scenarios. Cold builds run in a copied tree with their own library root.
* It never edits a file in ``demand_source_paths``. Doing so would change the
  demand source fingerprint and invalidate every warm day it is measuring —
  which is exactly why Stage 2 waits.
* It produces diagnostic evidence only (``release_evidence: false``).

Four ways this measurement could be got wrong quietly, each closed here:

* **gzip container bytes are not the day.** Stored route/agent artifacts are
  gzipped and gzip records an mtime, so two identical days never hash equal.
  Every comparison decompresses first and canonicalises JSON.
* **wall time and RSS must never decide equivalence.** They are reported,
  and they are structurally excluded from the verdict.
* **different source bytes are a confound, not a composition effect.** A pair
  whose ``source_hashes`` disagree is inconclusive, never "different".
* **matching only its own composition is not a pass.** A policy must
  reproduce BOTH context controls, on BOTH dates.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demand import day_library as dl                          # noqa: E402
from demand.intake import POOL_KEYS, pool_key_for             # noqa: E402
from traffic_sim.demand.source_identity import (              # noqa: E402
    demand_source_fingerprints, demand_source_paths)

# ── frozen scope, declared in source so it cannot travel in the artifact ────

#: Two dates, two candidate compositions each. Four cold days, well inside
#: the eight the plan allows.
CONTROL_DATES = ("2027-06-03", "2027-06-25")
CANDIDATE_POLICIES = ("canonical_union", "day_type_local")
CONTROL_ARMS = ("pure", "mixed")

MAX_COLD_CALIBRATIONS = 8
MAX_CALIBRATION_SECONDS = 30 * 60

#: The direction arms a full calibration publishes. Only this exact set is a
#: full three-variant day; a q50-only entry is a different identity.
VARIANT_SUFFIXES = ("", "_v1", "_v2")

DAY_PROVENANCE_NAME = "provenance.json"

#: What decides equivalence. Nothing else may.
EQUIVALENCE_DIMENSIONS = (
    "route_departures",
    "route_edges",
    "agent_records",
    "population",
    "integer_sensor_targets",
    "publication_health",
    "structure_guards",
    "route_provenance",
    "dynamic_passage_fit",
)

#: Absent from every fit in a repository that has no dynamic-passage stage;
#: absent on both sides is recorded as unevaluated, never as agreement.
OPTIONAL_DIMENSIONS = ("dynamic_passage_fit",)
REQUIRED_DIMENSIONS = tuple(
    d for d in EQUIVALENCE_DIMENSIONS if d not in OPTIONAL_DIMENSIONS)

#: A difference here is a safety regression and stops the run at once
#: (Stage 3 item 6). A difference only in departures or edges is the
#: experiment's answer, not a reason to abandon it.
REGRESSION_DIMENSIONS = (
    "route_provenance",
    "integer_sensor_targets",
    "population",
    "publication_health",
    "structure_guards",
)

#: Measured and published, but structurally barred from the verdict.
PERFORMANCE_FIELDS = (
    "total_calibration_s",
    "dynamic_passage_s",
    "peak_rss_bytes",
)

REPORTED_ONLY_FIELDS = (
    "identity_inputs",
    "source_hashes",
    "candidate_count",
    "candidate_semantic_hash",
    "pfe_shape_variables",
)


class ExperimentRefused(Exception):
    """The experiment declines to proceed, with a named reason."""


class BudgetExceeded(ExperimentRefused):
    """The frozen calibration budget would be exceeded."""


# ── canonical hashing ───────────────────────────────────────────────────────

def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ── Stage 3 step 1: can this experiment live in tools/ and tests/ at all ────

def experiment_source_files() -> tuple[Path, ...]:
    """Every file this experiment adds to the repository."""
    return (
        ROOT / "tools" / "experiment_day_reuse_policy.py",
        ROOT / "tests" / "test_experiment_day_reuse_policy.py",
    )


def fingerprint_neutrality(root: Path = ROOT) -> dict[str, Any]:
    """Prove the experiment cannot invalidate the day library it measures.

    A calibrated day binds the sha256 of the whole canonical demand source
    inventory. Anything the experiment adds must therefore sit outside that
    inventory, or every warm day in the library stops matching the moment
    this file is written.
    """
    inventory = demand_source_paths(Path(root))
    resolved = {path.resolve() for path in inventory.values()}
    inside = []
    listed = []
    for path in experiment_source_files():
        try:
            relative = str(Path(path).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            relative = str(path)
        listed.append(relative)
        if Path(path).resolve() in resolved:
            inside.append(relative)
    return {
        "check": "experiment files outside demand_source_paths",
        "inventory_size": len(inventory),
        "experiment_files": listed,
        "inside_inventory": inside,
        "fingerprint_neutral": not inside,
    }


_SUMO_DIR_BINDING = re.compile(r'^SUMO_DIR\s*=\s*Path\(\s*["\']sumo["\']\s*\)',
                               re.MULTILINE)


def sumo_dir_binding_sites(root: Path = ROOT) -> list[str]:
    """Inventory files that hardcode the shared ``sumo/`` output directory.

    This is why a cold build for the experiment needs an isolated WORKING
    COPY rather than a flag: the output directory is a module constant in
    files the day identity hashes, so redirecting it by editing source would
    invalidate every warm day — the precise cost Stage 2 is deferred to
    avoid.
    """
    root = Path(root).resolve()
    sites = []
    for path in demand_source_paths(root).values():
        if path.suffix != ".py" or not path.is_file():
            continue
        if _SUMO_DIR_BINDING.search(path.read_text(encoding="utf-8")):
            sites.append(str(path.resolve().relative_to(root)))
    return sorted(sites)


def check_output_path(
    path: Path,
    *,
    day_library_root: Path | None = None,
    sumo_dir: Path | None = None,
    allow_overwrite: bool = False,
) -> Path:
    """Refuse an output that would land in, or overwrite, protected state."""
    target = Path(path).resolve()
    library = Path(day_library_root
                   if day_library_root is not None
                   else ROOT / dl.DEFAULT_ROOT).resolve()
    sumo = Path(sumo_dir if sumo_dir is not None else ROOT / "sumo").resolve()
    if target == library or library in target.parents:
        raise ExperimentRefused(
            f"refusing to write inside the day library: {target}")
    if target == sumo or sumo in target.parents:
        raise ExperimentRefused(f"refusing to write inside sumo/: {target}")
    if target.exists() and not allow_overwrite:
        raise ExperimentRefused(
            f"artifact already exists and would be overwritten: {target}")
    return target


# ── the three compositions ──────────────────────────────────────────────────

def policy_composition(policy: str, date: str) -> tuple[str, ...]:
    """The pool composition a named policy would use for one date."""
    if policy == "canonical_union":
        return tuple(sorted(POOL_KEYS))
    if policy == "day_type_local":
        import pandas as pd
        return (pool_key_for(pd.Timestamp(date)),)
    if policy == "context_control":
        raise ExperimentRefused(
            "context_control is a property of the control entry the day was "
            "calibrated in, not of the date; read it from the control entry")
    raise ExperimentRefused(f"unknown policy: {policy!r}")


def planned_cold_calibrations() -> int:
    """Two control dates times two candidate compositions."""
    return len(CONTROL_DATES) * len(CANDIDATE_POLICIES)


# ── controls ────────────────────────────────────────────────────────────────

_REQUIRED_MANIFEST_FIELDS = ("schema_version", "kind", "key", "identity",
                             "artifacts")
_REQUIRED_IDENTITY_FIELDS = ("date", "source", "pool_composition", "inputs",
                             "source_hashes")


def _load_manifest(entry: Path) -> dict[str, Any]:
    manifest_path = Path(entry) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExperimentRefused(
            f"unreadable manifest at {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ExperimentRefused(f"manifest is not an object: {manifest_path}")
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing:
        raise ExperimentRefused(
            f"structurally invalid manifest {manifest_path}: missing "
            f"{', '.join(missing)}")
    identity = manifest["identity"]
    if not isinstance(identity, dict):
        raise ExperimentRefused(
            f"structurally invalid manifest {manifest_path}: identity")
    missing = [f for f in _REQUIRED_IDENTITY_FIELDS if f not in identity]
    if missing:
        raise ExperimentRefused(
            f"structurally invalid manifest {manifest_path}: identity is "
            f"missing {', '.join(missing)}")
    if not isinstance(manifest["artifacts"], dict) or not manifest["artifacts"]:
        raise ExperimentRefused(
            f"structurally invalid manifest {manifest_path}: artifacts")
    return manifest


def full_calibrations(library_root: Path, date: str) -> list[tuple[Path, dict]]:
    """Every stored FULL three-variant calibration of one date.

    Fails closed on a structurally invalid manifest rather than skipping it:
    an entry the experiment cannot read is not an entry it may ignore. A
    q50-only subset entry is skipped by design — only the exact
    three-variant set is a full calibration.
    """
    day_dir = Path(library_root) / date
    if not day_dir.is_dir():
        raise ExperimentRefused(f"no stored days for {date} under {library_root}")
    entries: list[tuple[Path, dict]] = []
    for entry in sorted(p for p in day_dir.iterdir() if p.is_dir()):
        if entry.name.endswith((".staging", ".replaced")):
            continue
        if not (entry / "manifest.json").is_file():
            continue
        manifest = _load_manifest(entry)
        identity = manifest["identity"]
        if identity.get("date") != date:
            raise ExperimentRefused(
                f"entry {entry} is stored under {date} but claims "
                f"{identity.get('date')}")
        names = {name.split(".gz")[0] for name in manifest["artifacts"]}
        if not all(f"calibrated{s}.rou.xml" in names for s in VARIANT_SUFFIXES):
            continue
        entries.append((entry, manifest))
    return entries


def find_built_day(library_root: Path, date: str) -> Path:
    """The one day a scratch library holds after a single isolated build.

    ``discover_controls`` cannot serve here: it requires BOTH arms, and a
    scratch library has exactly the arm just built.
    """
    entries = full_calibrations(library_root, date)
    if not entries:
        raise ExperimentRefused(
            f"no full calibration stored for {date} under {library_root}")
    if len(entries) > 1:
        raise ExperimentRefused(
            f"ambiguous scratch library: {len(entries)} full calibrations "
            f"for {date} under {library_root}")
    return entries[0][0]


def discover_controls(library_root: Path, date: str) -> dict[str, Path]:
    """The pure and mixed context controls stored for one date."""
    found: dict[str, list[Path]] = {arm: [] for arm in CONTROL_ARMS}
    for entry, manifest in full_calibrations(library_root, date):
        composition = tuple(manifest["identity"].get("pool_composition") or ())
        found["pure" if len(composition) == 1 else "mixed"].append(entry)
    for arm in CONTROL_ARMS:
        if not found[arm]:
            raise ExperimentRefused(
                f"{date} has no {arm} pool_composition control in "
                f"{library_root}; the experiment needs both")
        if len(found[arm]) > 1:
            raise ExperimentRefused(
                f"{date} has {len(found[arm])} {arm} controls "
                f"({', '.join(p.name[:12] for p in found[arm])}); the "
                "experiment needs one unambiguous control per arm")
    return {arm: found[arm][0] for arm in CONTROL_ARMS}


# ── reading a stored day ────────────────────────────────────────────────────

_ROUTE_EDGES = re.compile(r'<route edges="(?P<edges>[^"]*)"')


def _read_artifact_bytes(entry: Path, name: str) -> bytes | None:
    path = dl._day_file(Path(entry), name)
    if not path.is_file():
        return None
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def _parse_route(plain: bytes) -> tuple[list, list, int]:
    departures, edges = [], []
    for line in plain.decode("utf-8").splitlines():
        match = dl.VEHICLE_LINE.match(line)
        if match is None:
            continue
        route = _ROUTE_EDGES.search(line)
        departures.append([match.group("id"), match.group("depart")])
        edges.append([match.group("id"),
                      route.group("edges") if route else None])
    return departures, edges, len(departures)


def _publication_health(report: Mapping[str, Any]) -> dict[str, Any]:
    """The exact fields the real publication gate reads, plus its verdict.

    The verdict is taken from ``demand.calibration`` rather than restated, so
    this tool cannot drift into a second, kinder gate. The raw fields fully
    determine it, so the comparison stays valid even where the import fails.
    """
    fields = {
        "infeasible_intervals": report.get("infeasible_intervals"),
        "geh_pct": report.get("geh_pct"),
        "integer_sensor_constraints": report.get("integer_sensor_constraints"),
        "integer_sensor_exact": report.get("integer_sensor_exact"),
        "integer_sensor_max_abs_error": report.get(
            "integer_sensor_max_abs_error"),
        "bound_violations": report.get("bound_violations"),
        "unserviceable_edges": report.get("unserviceable_edges"),
    }
    try:
        from demand.calibration import _report_is_publishable
        fields["publishable"] = bool(_report_is_publishable(dict(report)))
    except Exception as exc:                       # pragma: no cover - env
        fields["publishable"] = None
        fields["publishable_unavailable"] = type(exc).__name__
    return fields


def read_day_record(
    entry: Path,
    performance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Every dimension Stage 3 reports, read out of one stored day."""
    entry = Path(entry)
    manifest = _load_manifest(entry)
    identity = manifest["identity"]
    provenance_raw = _read_artifact_bytes(entry, DAY_PROVENANCE_NAME)
    provenance = json.loads(provenance_raw) if provenance_raw else None

    variants: dict[str, Any] = {}
    for suffix in VARIANT_SUFFIXES:
        route_plain = _read_artifact_bytes(entry, f"calibrated{suffix}.rou.xml")
        if route_plain is None:
            continue
        departures, edges, vehicles = _parse_route(route_plain)
        agents_plain = _read_artifact_bytes(
            entry, f"calibrated{suffix}.agents.json")
        agents = json.loads(agents_plain) if agents_plain else None
        fit_plain = _read_artifact_bytes(entry, f"fit{suffix}.json")
        report = json.loads(fit_plain) if fit_plain else {}
        variants[suffix] = {
            "route_uncompressed_sha256": sha256_bytes(route_plain),
            "vehicles": vehicles,
            "departures_digest": digest(departures),
            "routes_digest": digest(edges),
            "agent_records": len(agents["agents"]) if agents else None,
            "agents_canonical_sha256": digest(agents) if agents else None,
            "fit_canonical_sha256": digest(report) if report else None,
            "population": report.get("vehicles", vehicles),
            "integer_sensor": {k: v for k, v in sorted(report.items())
                               if k.startswith("integer_sensor")},
            "publication_health": _publication_health(report),
            "structure_guards": {
                "purpose_allocation_summary":
                    report.get("purpose_allocation_summary"),
                "purpose_allocation": report.get("purpose_allocation"),
                "relaxed_bound_violations":
                    report.get("relaxed_bound_violations"),
                "relaxation_summary": report.get("relaxation_summary"),
            },
            "dynamic_passage_fit": report.get("dynamic_passage"),
        }

    measured = dict.fromkeys(PERFORMANCE_FIELDS)
    measured.update({k: v for k, v in (performance or {}).items()
                     if k in PERFORMANCE_FIELDS})
    candidate_records = (provenance or {}).get("candidate_records")
    return {
        "entry": str(entry),
        "key": manifest["key"],
        "identity": identity,
        "identity_inputs": identity.get("inputs", {}),
        "source_hashes": identity.get("source_hashes", {}),
        "stored_artifacts": manifest["artifacts"],
        "candidate_semantic_hash": identity.get("inputs", {}).get(
            "candidate_pool"),
        "candidate_metadata_hash": identity.get("inputs", {}).get(
            "candidate_metadata"),
        "candidate_count": candidate_records,
        # Every candidate in the day's pool is one PFE shape variable, so the
        # proven candidate-record count IS the shape-variable count.
        "pfe_shape_variables": candidate_records,
        "variants": variants,
        "route_provenance": provenance,
        "performance": measured,
    }


# ── comparison ──────────────────────────────────────────────────────────────

def _dimension_values(record: Mapping[str, Any], dimension: str) -> dict:
    variants = record.get("variants", {})
    if dimension == "route_provenance":
        return {"_": record.get("route_provenance")}
    per_variant = {
        "route_departures": "departures_digest",
        "route_edges": "routes_digest",
        "population": "population",
        "integer_sensor_targets": "integer_sensor",
        "publication_health": "publication_health",
        "structure_guards": "structure_guards",
        "dynamic_passage_fit": "dynamic_passage_fit",
    }
    if dimension == "agent_records":
        return {s: [v.get("agent_records"), v.get("agents_canonical_sha256")]
                for s, v in variants.items()}
    key = per_variant[dimension]
    return {s: v.get(key) for s, v in variants.items()}


def _all_absent(values: Mapping[str, Any]) -> bool:
    return all(value is None for value in values.values())


def compare_records(
    control: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Is the candidate day the SAME DAY as the control?

    Compared on decompressed bytes and canonical JSON only. Wall time and
    peak RSS are reported and cannot reach the verdict; the pool composition
    is the variable under test and is reported, never compared.
    """
    confounds = []
    if control.get("source_hashes") != candidate.get("source_hashes"):
        confounds.append("source_hashes")
    if control["identity"].get("date") != candidate["identity"].get("date"):
        confounds.append("date")
    if control["identity"].get("source") != candidate["identity"].get("source"):
        confounds.append("source")

    mismatched, unevaluated = [], []
    details: dict[str, Any] = {}
    for dimension in EQUIVALENCE_DIMENSIONS:
        left = _dimension_values(control, dimension)
        right = _dimension_values(candidate, dimension)
        if _all_absent(left) and _all_absent(right):
            unevaluated.append(dimension)
            details[dimension] = {"status": "absent_on_both"}
            continue
        if canonical_json(left) == canonical_json(right):
            details[dimension] = {"status": "equal"}
            continue
        mismatched.append(dimension)
        details[dimension] = {"status": "differs",
                              "control": left, "candidate": right}

    reported = {
        "pool_composition": {
            "control": list(control["identity"].get("pool_composition") or ()),
            "candidate": list(
                candidate["identity"].get("pool_composition") or ())},
        "candidate_pool_hash": {
            "control": control.get("candidate_semantic_hash"),
            "candidate": candidate.get("candidate_semantic_hash")},
        "candidate_metadata_hash": {
            "control": control.get("candidate_metadata_hash"),
            "candidate": candidate.get("candidate_metadata_hash")},
        "candidate_count": {"control": control.get("candidate_count"),
                            "candidate": candidate.get("candidate_count")},
        "pfe_shape_variables": {
            "control": control.get("pfe_shape_variables"),
            "candidate": candidate.get("pfe_shape_variables")},
        "identity_inputs": {"control": control.get("identity_inputs"),
                            "candidate": candidate.get("identity_inputs")},
        "source_hashes_equal": not bool(
            set(confounds) & {"source_hashes"}),
        "stored_artifact_bytes": {
            "control": control.get("stored_artifacts"),
            "candidate": candidate.get("stored_artifacts"),
            "note": "gzip container bytes; never used for equivalence"},
        "performance": {"control": control.get("performance"),
                        "candidate": candidate.get("performance")},
    }

    blocked = confounds or [d for d in unevaluated if d in REQUIRED_DIMENSIONS]
    if blocked:
        status, equivalent = "inconclusive", None
    else:
        status, equivalent = "measured", not mismatched
    return {
        "status": status,
        "equivalent": equivalent,
        "mismatched_dimensions": mismatched,
        "unevaluated_dimensions": unevaluated,
        "regression_dimensions": [d for d in mismatched
                                  if d in REGRESSION_DIMENSIONS],
        "confounds": confounds,
        "dimensions": details,
        "reported": reported,
    }


# ── budget ──────────────────────────────────────────────────────────────────

class Budget:
    """The frozen Stage 3 spend, enforced rather than intended."""

    def __init__(self, max_calibrations: int = MAX_COLD_CALIBRATIONS,
                 max_seconds: float = MAX_CALIBRATION_SECONDS) -> None:
        self.max_calibrations = max_calibrations
        self.max_seconds = max_seconds
        self.calibrations = 0
        self.seconds = 0.0

    def check(self) -> None:
        if self.calibrations >= self.max_calibrations:
            raise BudgetExceeded(
                f"{self.calibrations} cold calibrations already spent of "
                f"{self.max_calibrations} allowed calibrations")
        if self.seconds > self.max_seconds:
            raise BudgetExceeded(
                f"{self.seconds:.1f} calibration seconds already spent of "
                f"{self.max_seconds:.0f} allowed seconds")

    def record(self, seconds: float) -> None:
        self.calibrations += 1
        self.seconds += float(seconds)

    def report(self) -> dict[str, Any]:
        return {
            "max_cold_calibrations": self.max_calibrations,
            "max_calibration_seconds": self.max_seconds,
            "cold_calibrations": self.calibrations,
            "calibration_seconds": round(self.seconds, 3),
        }


# ── running the comparisons ─────────────────────────────────────────────────

def run_comparisons(
    jobs: Sequence[tuple[str, str, Any]],
    *,
    compare_fn: Callable[[Any, Any], Mapping[str, Any]],
    control_fn: Callable[[Any], Any],
) -> dict[str, Any]:
    """Compare each built day against its control, stopping on a regression.

    ``jobs`` are ``(date, control_arm, built_day)``. A regression in
    provenance, exact sensor targets, population, health or structure guards
    ends the run at once: those are safety properties, and continuing would
    spend budget on a build already known to be unsound.
    """
    comparisons: list[dict[str, Any]] = []
    stopped_on = None
    for date, arm, built in jobs:
        verdict = dict(compare_fn(control_fn((date, arm)), built))
        verdict.update({"date": date, "control": arm})
        comparisons.append(verdict)
        if [d for d in verdict.get("mismatched_dimensions", ())
                if d in REGRESSION_DIMENSIONS]:
            stopped_on = verdict
            break
    return {
        "comparisons": comparisons,
        "stopped_early": stopped_on is not None,
        "stopped_on": stopped_on,
    }


# ── the decision rule ───────────────────────────────────────────────────────

def policy_verdict(comparisons: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """A policy passes only by reproducing BOTH controls on BOTH dates."""
    seen = list(comparisons)
    covered = {(c.get("date"), c.get("control")) for c in seen}
    required = {(date, arm) for date in CONTROL_DATES for arm in CONTROL_ARMS}
    missing = sorted(required - covered)

    if missing:
        decision = "inconclusive"
        statement = (
            "not measured on every control: missing "
            + ", ".join(f"{d} {a}" for d, a in missing))
    elif any(c.get("equivalent") is None for c in seen):
        decision = "inconclusive"
        statement = ("at least one comparison was confounded or could not be "
                     "evaluated; composition is not isolated")
    elif all(c.get("equivalent") is True for c in seen):
        decision = "candidate_for_next_check"
        statement = ("the same built day matched both context controls on "
                     "both dates; a possible performance improvement, not yet "
                     "a policy")
    else:
        differing = sorted({d for c in seen
                            for d in c.get("mismatched_dimensions", ())})
        decision = "rejected_as_model_change"
        statement = (
            "the built day differs from a context control on "
            + ", ".join(differing)
            + "; this is a model change and not a safe performance fix")
    return {
        "decision": decision,
        "statement": statement,
        "comparisons": seen,
        "missing_coverage": [list(m) for m in missing],
        # Even a pass activates nothing: the plan requires the small
        # overlapping-window check first.
        "activate_in_production": False,
        "next_check": ("overlapping_window_check"
                       if decision == "candidate_for_next_check" else None),
    }


MEASURED_POLICY_DECISIONS = ("candidate_for_next_check",
                             "rejected_as_model_change")


def experiment_decision(
    policies: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """What Stage 3 concludes across both candidate policies.

    A policy the run never reached — because it stopped at a regression, or
    because the budget ran out — is NOT a rejection. Concluding "keep the
    current identity" from a policy that was never measured would present an
    abandoned run as a result.
    """
    unmeasured = sorted(
        name for name in CANDIDATE_POLICIES
        if policies.get(name, {}).get("decision")
        not in MEASURED_POLICY_DECISIONS
        and policies.get(name, {}).get("decision") != "inconclusive")
    passing = sorted(name for name, verdict in policies.items()
                     if verdict.get("decision") == "candidate_for_next_check")
    unresolved = sorted(name for name, verdict in policies.items()
                        if verdict.get("decision") == "inconclusive")
    if not passing and unmeasured:
        return {
            "decision": "inconclusive",
            "passing_policies": [],
            "inconclusive_policies": unresolved,
            "unmeasured_policies": unmeasured,
            "statement": (
                "not every candidate policy was measured ("
                + ", ".join(unmeasured)
                + "); the current composition-aware identity stands, but "
                  "this run does not settle the question"),
            "activate_in_production": False,
        }
    if passing:
        return {
            "decision": "candidate_for_overlapping_window_check",
            "passing_policies": passing,
            "inconclusive_policies": unresolved,
            "unmeasured_policies": unmeasured,
            "statement": (
                "exactly equivalent in isolation for "
                + ", ".join(passing)
                + "; not activated. The small overlapping-window check the "
                  "plan requires runs next, inside the same budget."),
            "activate_in_production": False,
        }
    if unresolved and len(unresolved) == len(policies):
        return {
            "decision": "inconclusive",
            "passing_policies": [],
            "inconclusive_policies": unresolved,
            "unmeasured_policies": unmeasured,
            "statement": ("no policy was measured cleanly; the current "
                          "composition-aware identity stands unchanged"),
            "activate_in_production": False,
        }
    return {
        "decision": "keep_composition_aware_identity",
        "passing_policies": [],
        "inconclusive_policies": unresolved,
        "unmeasured_policies": unmeasured,
        "statement": (
            "no candidate policy is exactly equivalent, so the current "
            "composition-aware identity is kept and the repeated day builds "
            "it causes are semantically necessary under the current model"),
        "activate_in_production": False,
    }


# ── the artifact ────────────────────────────────────────────────────────────

def build_artifact(
    *,
    root: Path,
    controls: Mapping[str, Mapping[str, Path]],
    policies: Mapping[str, Any],
    decision: Mapping[str, Any],
    budget: Budget,
    preflight: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Bind tool, code, inputs and controls by hash into one diagnostic file."""
    fingerprints = demand_source_fingerprints(Path(root))
    bound_controls = {
        date: {
            arm: {
                "path": str(Path(entry)),
                "key": _load_manifest(Path(entry))["key"],
                "manifest_sha256": sha256_file(Path(entry) / "manifest.json"),
                "pool_composition": _load_manifest(Path(entry))["identity"][
                    "pool_composition"],
            }
            for arm, entry in arms.items()
        }
        for date, arms in controls.items()
    }
    return {
        "schema_version": 1,
        "artifact": "day_reuse_policy_experiment_v1",
        "release_evidence": False,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "question": (
            "Does replacing the composition-aware day identity with "
            "canonical_union or day_type_local produce the SAME calibrated "
            "day as both context controls?"),
        "decision_rule": {
            "pass": ("the same built day matches both context controls "
                     "exactly, on both control dates"),
            "fail": ("any output difference makes the policy a model change, "
                     "not a safe performance fix"),
            "none_pass": ("keep the composition-aware identity and record the "
                          "repeated builds as semantically necessary"),
            "even_on_pass": ("no production activation; run the small "
                             "overlapping-window check first"),
        },
        "control_dates": list(CONTROL_DATES),
        "candidate_policies": list(CANDIDATE_POLICIES),
        "reported_dimensions": sorted(
            set(EQUIVALENCE_DIMENSIONS) | set(PERFORMANCE_FIELDS)
            | set(REPORTED_ONLY_FIELDS)),
        "equivalence_dimensions": list(EQUIVALENCE_DIMENSIONS),
        "performance_fields_excluded_from_verdict": list(PERFORMANCE_FIELDS),
        "preflight": dict(preflight),
        "budget": budget.report(),
        "policies": dict(policies),
        "decision": dict(decision),
        "notes": list(notes),
        "binding": {
            "tool_sha256": sha256_file(
                Path(root) / "tools" / "experiment_day_reuse_policy.py"),
            "test_sha256": sha256_file(
                Path(root) / "tests" / "test_experiment_day_reuse_policy.py"),
            "demand_source_fingerprint": digest(fingerprints),
            "demand_source_files": {name: record.get("sha256")
                                    for name, record in
                                    sorted(fingerprints.items())},
            "controls": bound_controls,
        },
    }


def write_artifact(payload: Mapping[str, Any], path: Path, *,
                   allow_overwrite: bool = False,
                   day_library_root: Path | None = None,
                   sumo_dir: Path | None = None) -> Path:
    target = check_output_path(path, day_library_root=day_library_root,
                               sumo_dir=sumo_dir,
                               allow_overwrite=allow_overwrite)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".tmp")
    staged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    os.replace(staged, target)
    return target


# ── the isolated cold build ─────────────────────────────────────────────────
#
# NOT EXERCISED BY THE TEST SUITE: it needs SUMO, the scientific stack and a
# warm day library, none of which a unit test may assume. What IS tested is
# that the plan never targets the real sumo/ or the real day library.

_RUNNER = '''\
"""Run one whole-day demand build with the pool composition forced.

Written into the isolated workspace, never into the repository: patching a
module attribute changes no source bytes, so the day identity this build
produces is exactly the identity the policy would produce in production.
"""
import json, resource, sys, time

TIMING_PATH = sys.argv[0] + ".timing.json"
FORCED = tuple(json.loads(sys.argv[1]))
BUILD_ARGV = json.loads(sys.argv[2])

sys.path.insert(0, ".")
import build_sumo_demand

build_sumo_demand.window_pool_composition = lambda start, days: FORCED
sys.argv = ["build_sumo_demand.py"] + BUILD_ARGV
started = time.time()
build_sumo_demand.main()

# The PFE solves in a fork pool, so the parent's own peak is not the run's
# peak. ru_maxrss is KiB on Linux and bytes on macOS.
scale = 1 if sys.platform == "darwin" else 1024
peak = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
           resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * scale
with open(TIMING_PATH, "w") as handle:
    json.dump({"total_calibration_s": round(time.time() - started, 3),
               "peak_rss_bytes": peak}, handle)
'''


def isolated_build_plan(
    *,
    root: Path,
    workspace: Path,
    date: str,
    composition: Sequence[str],
    source: str = "forecast",
    label: str | None = None,
) -> dict[str, Any]:
    """Everything one isolated cold build needs, plus its isolation proof.

    ``label`` separates the scratch library of one build from the next, so
    the entry a build stores can be located unambiguously afterwards.
    """
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    tree = workspace / "tree"
    library = workspace / "day-library" / (label or date)
    argv = ["--date", date, "--days", "1", "--source", source,
            "--start", "00:00", "--end", "24:00",
            "--direction-stress-variants",
            "--day-library-root", str(library)]
    return {
        "date": date,
        "composition": list(composition),
        "tree": str(tree),
        "day_library_root": str(library),
        "argv": argv,
        "runner": str(tree / "_forced_composition_run.py"),
        "guards": {
            "writes_real_sumo_dir": False,
            "writes_real_day_library": False,
            "real_sumo_dir": str(root / "sumo"),
            "real_day_library": str(root / dl.DEFAULT_ROOT),
            "edits_demand_sources": False,
        },
    }


def prepare_workspace(root: Path, workspace: Path) -> Path:
    """Copy the repository into a scratch tree with its own sumo/ and runs/.

    A byte-identical copy keeps every demand source hash identical, so the
    identity a build computes here is the identity production would compute.
    """
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    tree = workspace / "tree"
    if tree.exists():
        raise ExperimentRefused(f"workspace tree already exists: {tree}")
    real_sumo = root / "sumo"
    shutil.copytree(
        root, tree,
        # sumo/ is copied separately below so its absence is a named
        # refusal rather than a build failure ten minutes later; runs/ is
        # the day library itself and must never be reachable from here.
        ignore=shutil.ignore_patterns(".git", "runs", "sumo", "__pycache__",
                                      "*.pyc", "plots", "validation"),
        symlinks=True)
    if not real_sumo.is_dir():
        raise ExperimentRefused(
            "no sumo/ prerequisites to copy; run `make sumo-net` and a demand "
            "build before the experiment")
    shutil.copytree(real_sumo, tree / "sumo", dirs_exist_ok=True,
                    symlinks=True)
    (tree / "_forced_composition_run.py").write_text(_RUNNER,
                                                     encoding="utf-8")
    return tree


def run_isolated_build(plan: Mapping[str, Any], *, budget: Budget,
                       timeout_s: float | None = None) -> dict[str, Any]:
    """Execute one planned cold build, charging it to the budget."""
    budget.check()
    remaining = budget.max_seconds - budget.seconds
    started = time.time()
    completed = subprocess.run(
        [sys.executable, plan["runner"],
         json.dumps(list(plan["composition"])), json.dumps(plan["argv"])],
        cwd=plan["tree"], capture_output=True, text=True,
        timeout=timeout_s if timeout_s is not None else remaining)
    elapsed = time.time() - started
    budget.record(elapsed)
    if completed.returncode != 0:
        raise ExperimentRefused(
            f"cold build for {plan['date']} failed "
            f"({completed.returncode}): {completed.stderr[-2000:]}")
    timing_path = Path(plan["runner"] + ".timing.json")
    timing = (json.loads(timing_path.read_text())
              if timing_path.is_file() else {})
    timing.setdefault("total_calibration_s", round(elapsed, 3))
    return {"performance": timing, "day_library_root": plan["day_library_root"]}


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--day-library-root", type=Path,
                        default=ROOT / dl.DEFAULT_ROOT)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "validation"
                        / "day_reuse_policy_experiment_v1.json")
    parser.add_argument("--workspace", type=Path, default=None,
                        help="scratch directory for the isolated tree")
    parser.add_argument("--source", default="forecast")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--preflight-only", action="store_true",
                        help="run Stage 3 step 1 and stop")
    args = parser.parse_args(argv)

    preflight = fingerprint_neutrality(ROOT)
    preflight["sumo_dir_binding_sites"] = sumo_dir_binding_sites(ROOT)
    preflight["isolation_route"] = (
        "isolated working copy; SUMO_DIR is a module constant in inventory "
        "files, so no flag can redirect it without invalidating warm days")
    preflight["planned_cold_calibrations"] = planned_cold_calibrations()
    print(json.dumps(preflight, indent=2, sort_keys=True))
    if not preflight["fingerprint_neutral"]:
        raise ExperimentRefused(
            "the experiment's own files are inside demand_source_paths; "
            "running it would invalidate the day library it measures")
    if args.preflight_only:
        return 0

    check_output_path(args.output, day_library_root=args.day_library_root,
                      sumo_dir=ROOT / "sumo",
                      allow_overwrite=args.allow_overwrite)
    controls = {date: discover_controls(args.day_library_root, date)
                for date in CONTROL_DATES}
    control_records = {(date, arm): read_day_record(entry)
                       for date, arms in controls.items()
                       for arm, entry in arms.items()}

    workspace = Path(args.workspace or (ROOT.parent
                                        / f".day-reuse-exp-{os.getpid()}"))
    budget = Budget()
    policies: dict[str, Any] = {name: {"decision": "not_measured",
                                       "statement": "the run did not reach "
                                                    "this policy"}
                                for name in CANDIDATE_POLICIES}
    notes: list[str] = []
    try:
        # ONE copied tree serves every build: the composition is forced at
        # runtime, so nothing on disk differs between policies. Each build
        # gets its own scratch library root so the entry it stores is
        # unambiguous.
        prepare_workspace(ROOT, workspace)
        stop = False
        for policy in CANDIDATE_POLICIES:
            if stop:
                notes.append(
                    f"{policy}: not measured, the run stopped at a regression")
                break
            jobs = []
            for date in CONTROL_DATES:
                plan = isolated_build_plan(
                    root=ROOT, workspace=workspace, date=date,
                    composition=policy_composition(policy, date),
                    source=args.source, label=f"{policy}-{date}")
                built = run_isolated_build(plan, budget=budget)
                record = read_day_record(
                    find_built_day(Path(built["day_library_root"]), date),
                    performance=built["performance"])
                # One built day, compared against BOTH context controls.
                jobs += [(date, arm, record) for arm in CONTROL_ARMS]
            outcome = run_comparisons(
                jobs, compare_fn=compare_records,
                control_fn=lambda key: control_records[key])
            policies[policy] = policy_verdict(outcome["comparisons"])
            policies[policy]["run"] = {
                "stopped_early": outcome["stopped_early"]}
            if outcome["stopped_early"]:
                notes.append(f"{policy}: stopped at the first regression in "
                             + ", ".join(outcome["stopped_on"]
                                         ["regression_dimensions"]))
                stop = True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    payload = build_artifact(root=ROOT, controls=controls, policies=policies,
                             decision=experiment_decision(policies),
                             budget=budget, preflight=preflight, notes=notes)
    written = write_artifact(payload, args.output,
                             allow_overwrite=args.allow_overwrite,
                             day_library_root=args.day_library_root,
                             sumo_dir=ROOT / "sumo")
    print(f"wrote {written}")
    print(json.dumps(payload["decision"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentRefused as refusal:
        print(f"experiment refused: {refusal}", file=sys.stderr)
        raise SystemExit(2)
