#!/usr/bin/env python3
"""Phase D producer/validator: one qualified-demand manifest.

`tools/ai_flow.py`'s Phase 0-2 derivation used to return an unconditional
PASS with no evidence behind it (found by code review). Phases 0-2 are now
mechanically derived from exactly one bound manifest this tool produces --
see `_derive_phase012_status` in `tools/ai_flow.py`. This module is that
manifest's ONLY producer and its own independent validator, so a report can
never claim Phase 0-2 passed without a real, re-checked artifact behind it.

The manifest binds, in one place:
  * the CODE_APPROVED frozen source digest;
  * the current SUMO network identity and the strict sensor-route contract's
    policy version (`traffic_sim.demand.sensor_route_contract`);
  * the adopted weekday/weekend strict route-catalog runtime keys;
  * a freshly re-run shared sensor-route support-floor audit against BOTH
    catalogs (`build_sumo_demand.validate_mixed_catalog_candidates` --
    the same independent, live-network recomputation used to admit a
    candidate at all, not a trusted summary);
  * the frozen 30-date/65-window/five-consecutive-day search contract and the
    unchanged `closure_cost_v1` policy identity this plan may never loosen;
  * the demand build's declared variants (q50 for new monthly builds;
    explicit historical q10/q50/q90 contracts remain readable), independently
    re-proved via `traffic_sim.demand.provenance.validate_calibrated_provenance`
    (every emitted vehicle resolves one-for-one to a qualified candidate and
    its exact route), read against the SAME `demand_meta.json` a
    `build_sumo_demand.py` run publishes -- its own
    `demand_variant_contract` resolves each variant's target key and route
    file rather than this tool guessing filename suffixes -- plus that
    variant's exact rounded per-quarter targets and PFE relaxation-ladder
    rung, and every artifact's content digest.

Fails CLOSED: any missing, malformed, or contract-violating input raises
`QualificationError` (a producer-input defect) or is folded into
`support_audit_pass: False` / `status:
"INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT"` (a genuine scientific outcome) --
never a silent partial manifest.

No-clobber: `write_manifest` refuses to overwrite an existing file, matching
the append-only evidence contract every other Phase 3-5 producer already
follows.

Archive input is explicit: `--fresh-runs-root` builds into a previously absent
root; `--existing-runs-root` performs no build and accepts exactly one fully
validated existing archive per required build key.  Both paths still require
the protected source bytes executing this tool to match CODE_APPROVED.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Sequence

from demand.day_library import DayIdentity, DayLibrary, assemble_window
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand.provenance import validate_calibrated_provenance
from traffic_sim.demand import route_catalog
from traffic_sim.demand.sensor_route_contract import (
    POLICY_VERSION as SENSOR_ROUTE_POLICY_VERSION,
    load_network_contract,
)
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec, DemandBuildSpec
from traffic_sim.simulation.independent_daily import daily_unit_records
from traffic_sim.simulation.monthly_demand import (
    MonthlyDemandResolverRunner,
    _archives_for_build_key,
    build_demand_archive,
    find_demand_archives,
    qualified_manifest_archive_mismatch,
    validate_demand_archive,
    validate_qualified_demand_manifest_shape,
)
from tools import profile_passage_replay

MANIFEST_SCHEMA = "subhour_qualified_demand_manifest_v1"
MANIFEST_KIND = "subhour_qualified_demand_manifest"

# Non-negotiable per the sub-hour closure plan: this tool may never author a
# manifest that claims a different search shape or a changed closure-cost
# policy identity, no matter what its inputs say.
FROZEN_SEARCH_CONTRACT: dict[str, object] = {
    "dates": 30,
    "windows": 65,
    "consecutive_days": 5,
    "closure_cost_policy": "closure_cost_v1",
}
PHASE_PREREQUISITE_CONTRACT = {
    "phase_0": {
        **FROZEN_SEARCH_CONTRACT,
        "q_variants": ["q50"],
        "work_budget_seconds": 3300,
        "publication_budget_seconds": 300,
        "fresh_roots": True,
        "tie_finalist_rules_unchanged": True,
        "timeouts_capacity_terminals_bound": True,
    },
    "phase_1": {
        "shared_kernel": "run_cost_ordered_execution",
        "only_allowed_difference": "disable_early_stop",
        "cost_ordered": {"disable_early_stop": False},
        "ordered_exhaustive": {"disable_early_stop": True},
        "shared_ledger_order_verifier_attempt_health_reconciliation_cursor": True,
    },
}
REQUIRED_VARIANTS = ("q10", "q50", "q90")
REQUIRED_CATALOG_POOLS = ("weekday", "weekend")


class QualificationError(ValueError):
    """A Phase D input is missing, malformed, or fails a fail-closed check."""


class SensorShortestSupportInconclusive(QualificationError):
    """The strict route/count representation is scientifically infeasible."""


def _source_manifest(root: Path, patterns: Sequence[str]) -> dict:
    """Build the controller's canonical protected-source manifest."""
    from tools.ai_flow import source_manifest
    return source_manifest(Path(root), patterns)


def _require_current_source(source: Mapping, root: Path,
                            patterns: Sequence[str]) -> None:
    """Reject an approval frozen for source bytes other than those executing."""
    current = _source_manifest(Path(root), patterns)
    if current.get("digest") == source.get("digest"):
        return
    before = {
        item.get("path"): item.get("sha256")
        for item in source.get("files", ()) if isinstance(item, Mapping)
    }
    after = {
        item.get("path"): item.get("sha256")
        for item in current.get("files", ()) if isinstance(item, Mapping)
    }
    changed = sorted(
        path for path in set(before) | set(after)
        if before.get(path) != after.get(path))
    raise QualificationError(
        "protected source changed after CODE_APPROVED: "
        f"{changed[:20]}{' ...' if len(changed) > 20 else ''}")


def _digest_payload(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise QualificationError(f"{label} is not a SHA-256 digest")
    return value


def _load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError(f"{label} is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{label} is not an object: {path}")
    return value


def validate_code_approval(
    source_manifest_path: Path,
    checks_path: Path,
    impact_inventory_path: Path,
    *,
    source_root: Path | None = None,
    source_patterns: Sequence[str] | None = None,
) -> dict:
    """Bind Phase D to persisted controller-owned CODE_APPROVED inputs."""
    source = _load_json_object(source_manifest_path, "CODE_APPROVED source manifest")
    digest = _require_sha256(source.get("digest"), "source manifest digest")
    files = source.get("files")
    if source.get("schema_version") != 1 or not isinstance(files, list):
        raise QualificationError("CODE_APPROVED source manifest is malformed")
    canonical_files = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical_files).hexdigest() != digest:
        raise QualificationError("CODE_APPROVED source manifest digest is invalid")
    root = (Path(source_root).resolve() if source_root is not None
            else Path(__file__).resolve().parents[1])
    if source_patterns is None:
        from tools.ai_flow import FlowError, load_config
        try:
            config = load_config(
                root / ".ai-flow/config.complete-subhour.toml", root)
        except (OSError, ValueError, FlowError) as error:
            raise QualificationError(
                "cannot load the protected-source policy") from error
        if config.evidence_policy is None:
            raise QualificationError("source policy lacks evidence settings")
        source_patterns = config.evidence_policy.source_globs
    _require_current_source(source, root, source_patterns)
    checks = _load_json_object(checks_path, "source-bound checks")
    results = checks.get("results")
    if (checks.get("status") != "PASS" or checks.get("source_digest") != digest
            or not isinstance(results, list) or not results
            or any(not isinstance(item, Mapping) or item.get("returncode") != 0
                   for item in results)):
        raise QualificationError("checks are not complete PASS results for approved source")
    impact_digest = sha256_file(Path(impact_inventory_path))
    _require_sha256(impact_digest, "impact inventory digest")
    if checks.get("impact_inventory_sha256") != impact_digest:
        raise QualificationError("checks are not bound to the final impact inventory")
    prerequisites = checks.get("phase_prerequisites")
    if not isinstance(prerequisites, Mapping):
        raise QualificationError("checks lack mechanical Phase 0-2 prerequisites")
    for phase, expected in PHASE_PREREQUISITE_CONTRACT.items():
        if prerequisites.get(phase) != expected:
            raise QualificationError(f"checks have an invalid {phase} contract")
    phase2 = prerequisites.get("phase_2")
    if (not isinstance(phase2, Mapping) or phase2.get("status") != "PASS"
            or not isinstance(phase2.get("required_tests"), list)
            or not phase2["required_tests"]
            or any(not isinstance(item, str) or not item for item in phase2["required_tests"])):
        raise QualificationError("checks lack the completed deterministic Phase 2 suite")
    return {
        "status": "CODE_APPROVED",
        "source_digest": digest,
        "source_manifest_sha256": sha256_file(Path(source_manifest_path)),
        "checks_sha256": sha256_file(Path(checks_path)),
        "checks_status": "PASS",
        "impact_inventory_sha256": impact_digest,
        "phase_prerequisites": dict(prerequisites),
    }


def validate_adoption_and_catalogs(adoption_path: Path, catalog_root: Path,
                                   catalogs: Mapping[str, tuple[Path, Path]]) -> tuple[dict, dict]:
    adoption = route_catalog.adopted_catalog_config(
        Path(adoption_path), root=Path(catalog_root))
    if adoption is None:
        raise QualificationError("runtime route-catalog adoption is not valid")
    keys = dict(adoption["catalog_keys"])
    if set(catalogs) != set(REQUIRED_CATALOG_POOLS):
        raise QualificationError("catalog inputs require weekday and weekend")
    bindings = {}
    for pool, key in sorted(keys.items()):
        entry = Path(catalog_root) / key
        manifest_path = entry / "manifest.json"
        manifest = _load_json_object(manifest_path, f"{pool} catalog manifest")
        routes_path, metadata_path = map(Path, catalogs[pool])
        expected_routes = (entry / "catalog.rou.xml").resolve()
        expected_metadata = (entry / "catalog.meta.json").resolve()
        if routes_path.resolve() != expected_routes or metadata_path.resolve() != expected_metadata:
            raise QualificationError(f"{pool} catalog paths do not name adopted artifacts")
        if not route_catalog.catalog_entry_matches(
                Path(catalog_root), pool=pool, key=key,
                n_total=int(adoption["catalog_selected_n_total"][pool])):
            raise QualificationError(f"{pool} adopted catalog entry failed validation")
        bindings[pool] = {
            "catalog_key": key,
            "manifest_sha256": sha256_file(manifest_path),
            "routes_sha256": sha256_file(routes_path),
            "metadata_sha256": sha256_file(metadata_path),
        }
    return ({"path": str(Path(adoption_path).resolve()),
             "sha256": sha256_file(Path(adoption_path)),
             "catalog_keys": keys}, bindings)


def _require_passage_proof(variant: str, proof: Mapping | None,
                           expected: Mapping[str, Sequence[int]]) -> Mapping:
    """Require a replayed, target-bound sensor-entry-quarter proof.

    Route XML stores departures, not the predicted time at which each vehicle
    enters a downstream sensor edge. Counting a route in its departure quarter
    is therefore a different measurement and was the defect that rejected the
    September qualification campaign. The only accepted replacement is a
    replay of the same entry-offset system that calibration solved.
    """
    if not isinstance(proof, Mapping) or proof.get("exact") is not True \
            or proof.get("basis") != "sensor_edge_entry_quarter" \
            or type(proof.get("replayed_days")) is not int \
            or proof["replayed_days"] < 1:
        raise QualificationError(
            f"{variant} passage proof is missing, incomplete, or uses the wrong basis")
    if proof.get("targets_digest") != _digest_payload(expected):
        raise QualificationError(f"{variant} passage proof target digest differs")
    return proof


_VARIANT_DAY_FILES = {
    "q50": ("calibrated.rou.xml", "calibrated.agents.json", "fit.json"),
    "q10": ("calibrated_v1.rou.xml", "calibrated_v1.agents.json", "fit_v1.json"),
    "q90": ("calibrated_v2.rou.xml", "calibrated_v2.agents.json", "fit_v2.json"),
}


def _file_snapshot(paths: Sequence[Path]) -> dict[str, str]:
    """Content snapshot, including compressed bytes; never stat-authorized."""
    try:
        return {str(path): sha256_file(path) for path in paths}
    except OSError as error:
        raise QualificationError(
            f"evidence is unreadable or changed during qualification: {error}") from error


def _require_snapshot(snapshot: Mapping[str, str]) -> None:
    if _file_snapshot([Path(path) for path in snapshot]) != snapshot:
        raise QualificationError("evidence changed during qualification")


def _passage_snapshot(root: Path) -> dict[str, str]:
    paths = [root / "report.json", root / "result.json"]
    for name in (profile_passage_replay.REQUIRED_INPUTS
                 + profile_passage_replay.REQUIRED_EVIDENCE
                 + (profile_passage_replay.REPLAY_CONTRACT,)):
        path = root / name
        paths.append(path if path.is_file() else path.with_name(path.name + ".gz"))
    return _file_snapshot(paths)


def _verified_day_directories(demand_meta: Mapping, root: Path,
                              memo: dict[Path, dict]) -> list[Path]:
    diagnostics = demand_meta.get("day_library_diagnostics")
    days = demand_meta.get("days")
    if type(days) is not int or days < 1 or not isinstance(diagnostics, list) \
            or len(diagnostics) != days:
        raise QualificationError("demand metadata lacks complete day-library diagnostics")
    library = DayLibrary(Path(root))
    result = []
    for item in diagnostics:
        if not isinstance(item, Mapping):
            raise QualificationError("day-library diagnostic is malformed")
        date, key = item.get("date"), item.get("expected_key")
        if not isinstance(date, str) or not isinstance(key, str):
            raise QualificationError("day-library diagnostic lacks date or key")
        directory = (Path(root) / date / key).resolve()
        if directory in memo:
            _require_snapshot(memo[directory]["snapshot"])
        else:
            manifest_path = directory / "manifest.json"
            snapshot = _file_snapshot([manifest_path])
            raw = _load_json_object(manifest_path, "day-library manifest")
            identity_record = raw.get("identity")
            if not isinstance(identity_record, Mapping):
                raise QualificationError("day-library manifest lacks identity")
            try:
                identity = DayIdentity(
                    date=identity_record["date"], source=identity_record["source"],
                    pool_composition=tuple(identity_record["pool_composition"]),
                    inputs=identity_record["inputs"],
                    source_hashes=identity_record["source_hashes"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise QualificationError("day-library identity is malformed") from error
            lookup = library.lookup(identity)
            if identity.date != date or identity.key != key or lookup.outcome != "hit" \
                    or lookup.manifest is None:
                raise QualificationError(
                    f"day-library entry is absent or unverified: {directory}")
            snapshot.update({
                str(directory / name): record["sha256"]
                for name, record in lookup.manifest["artifacts"].items()
            })
            _require_snapshot(snapshot)
            memo[directory] = {"manifest": lookup.manifest, "snapshot": snapshot}
        result.append(directory)
    return result


def _decompressed_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as error:
        raise QualificationError(f"day artifact is unreadable: {path}") from error
    return digest.hexdigest()


def _seed_replay_cache(evidence_root: Path, source_cache: Path,
                       owned_cache: Path) -> None:
    """Copy candidate cache entries; replay still validates every solution."""
    for state_path in sorted(Path(evidence_root).glob("solver-*/state.json")):
        state = _load_json_object(state_path, "passage solver state")
        key = _require_sha256(state.get("key"), "passage solver state key")
        source = Path(source_cache) / key / "result.json"
        if not source.is_file():
            continue
        destination = Path(owned_cache) / key / "result.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)


def _replay_day_passage(
    evidence_root: Path,
    expected: Mapping[str, Sequence[int]],
    *,
    candidate_pool: Path,
    replay_root: Path,
    solver_cache_root: Path,
    replay_memo: dict[tuple[Path, str], dict],
) -> dict:
    evidence_root = Path(evidence_root).resolve()
    candidate_pool = Path(candidate_pool).resolve()
    memo_key = (evidence_root, sha256_file(candidate_pool))
    snapshot = _passage_snapshot(evidence_root)
    if memo_key in replay_memo and snapshot != replay_memo[memo_key]["snapshot"]:
        raise QualificationError("passage evidence changed during qualification")
    if memo_key not in replay_memo:
        owned_cache = replay_root / "solver-cache"
        _seed_replay_cache(evidence_root, solver_cache_root, owned_cache)
        output = replay_root / f"replay-{len(replay_memo):04d}"
        try:
            report = profile_passage_replay.replay(
                evidence_root, output, pool=candidate_pool,
                allow_compressed=True,
                _solver_cache_dir=owned_cache)
        except (OSError, ValueError, profile_passage_replay.ReplayRefused) as error:
            raise QualificationError(
                f"passage evidence replay failed: {evidence_root}: {error}") from error
        proof = report.get("predicted_sensor_incidence")
        if report.get("selection_reproduced", {}).get("state") != "identical":
            raise QualificationError(
                f"passage evidence did not reproduce exactly: {evidence_root}")
        proof = dict(_require_passage_proof("day", proof, expected))
        _require_sha256(proof.get("digest"), "passage projection digest")
        if _passage_snapshot(evidence_root) != snapshot:
            raise QualificationError("passage evidence changed during qualification")
        replay_memo[memo_key] = {"proof": proof, "snapshot": snapshot}
    proof = replay_memo[memo_key]["proof"]
    _require_passage_proof("day", proof, expected)
    return proof


def _reproduce_assembled_variant(
    archive: Path,
    day_directories: Sequence[Path],
    route_name: str,
    agent_name: str,
    work: Path,
) -> None:
    route_out, agents_out = work / route_name, work / agent_name
    assemble_window(day_directories, route_out, agents_out, (route_name, agent_name))
    for reproduced, published in (
            (route_out, archive / route_name), (agents_out, archive / agent_name)):
        if sha256_file(reproduced) != sha256_file(published):
            raise QualificationError(
                f"assembled archive differs from verified day artifacts: {published}")


def validate_archive_passage_evidence(
    variant: str,
    *,
    archive: Path,
    demand_meta: Mapping,
    expected: Mapping[str, Sequence[int]],
    day_library_root: Path,
    solver_cache_root: Path,
    work_root: Path,
    day_memo: dict[Path, dict],
    replay_memo: dict[tuple[Path, str], dict],
) -> dict:
    """Re-prove one assembled variant from content-bound per-day evidence."""
    if variant not in _VARIANT_DAY_FILES:
        raise QualificationError(f"unknown demand variant: {variant!r}")
    route_name, agent_name, fit_name = _VARIANT_DAY_FILES[variant]
    day_directories = _verified_day_directories(
        demand_meta, day_library_root, day_memo)
    if any(len(values) % len(day_directories) for values in expected.values()):
        raise QualificationError("archive target horizon is not divisible into days")
    quarters_per_day = len(next(iter(expected.values()))) // len(day_directories)
    candidate_pool = Path(archive) / "candidates.rou.xml"
    candidate_metadata = Path(archive) / "candidates.meta.json"
    candidate_binding = {
        "candidate_pool": sha256_file(candidate_pool),
        "candidate_metadata": sha256_file(candidate_metadata),
    }
    evidence_digests = []
    for index, directory in enumerate(day_directories):
        identity = day_memo[directory]["manifest"].get("identity")
        inputs = identity.get("inputs") if isinstance(identity, Mapping) else None
        if not isinstance(inputs, Mapping) or any(
                inputs.get(name) != digest
                for name, digest in candidate_binding.items()):
            raise QualificationError(
                f"{variant} day identity differs from the archive candidate pool")
        fit = _load_json_object(directory / fit_name, f"{variant} day fit")
        passage = fit.get("passage_calibration")
        if not isinstance(passage, Mapping):
            raise QualificationError(f"{variant} day fit lacks passage calibration")
        evidence_directory = passage.get("evidence_directory")
        if not isinstance(evidence_directory, str) or not evidence_directory:
            raise QualificationError(
                f"{variant} day fit lacks an evidence directory")
        evidence_root = Path(evidence_directory)
        if not evidence_root.is_absolute():
            raise QualificationError(
                f"{variant} passage evidence directory is not absolute")
        saved_result = _load_json_object(
            evidence_root / "result.json", f"{variant} passage result")
        if saved_result != passage:
            raise QualificationError(
                f"{variant} day fit differs from its saved passage result")
        stored_route = directory / (route_name + ".gz")
        stored_agents = directory / (agent_name + ".gz")
        if not stored_route.is_file():
            stored_route = directory / route_name
        if not stored_agents.is_file():
            stored_agents = directory / agent_name
        if _decompressed_sha256(stored_route) != passage.get("routes_sha256") \
                or _decompressed_sha256(stored_agents) != passage.get("agents_sha256"):
            raise QualificationError(
                f"{variant} day artifacts differ from passage output hashes")
        start = index * quarters_per_day
        day_expected = {
            edge: list(values[start:start + quarters_per_day])
            for edge, values in expected.items()
        }
        replayed = _replay_day_passage(
            evidence_root, day_expected,
            candidate_pool=candidate_pool,
            replay_root=work_root,
            solver_cache_root=solver_cache_root, replay_memo=replay_memo)
        evidence_digests.append(replayed["digest"])
    archive_key = hashlib.sha256(str(archive).encode()).hexdigest()[:12]
    assembly = work_root / f"assembly-{archive_key}-{variant}"
    assembly.mkdir(parents=True, exist_ok=False)
    _reproduce_assembled_variant(
        Path(archive), day_directories, route_name, agent_name, assembly)
    return {
        "basis": "sensor_edge_entry_quarter",
        "exact": True,
        "targets_digest": _digest_payload(expected),
        "replayed_days": len(day_directories),
        "evidence_digests": evidence_digests,
        "assembly_reproduced": True,
    }


def audit_shared_support(
    net_path: Path,
    measured_edges: Sequence[str],
    *,
    min_per_sensor: int,
    catalogs: Mapping[str, tuple[Path, Path]],
) -> dict[str, dict]:
    """Independently re-run the strict support-floor audit per catalog pool.

    Reuses `build_sumo_demand.validate_mixed_catalog_candidates` -- the same
    live-network recomputation (route legality, exact cost, sensor detour,
    support floor) that admits a candidate into production at all -- rather
    than trusting any prior qualification report's summary. A pool that
    fails does not abort the whole audit; it is recorded so the caller can
    publish a precise fail-closed terminal.
    """
    import build_sumo_demand as bsd

    if set(catalogs) != set(REQUIRED_CATALOG_POOLS):
        raise QualificationError(
            "shared support audit requires exactly the weekday and weekend "
            "catalogs")
    required = {str(edge) for edge in measured_edges}
    if not required:
        raise QualificationError("shared support audit has no measured sensors")
    report: dict[str, dict] = {}
    for pool, (routes_path, metadata_path) in sorted(catalogs.items()):
        try:
            result = bsd.validate_mixed_catalog_candidates(
                Path(routes_path), Path(metadata_path), required,
                min_per_sensor=min_per_sensor, net_path=Path(net_path))
        except ValueError as error:
            report[pool] = {"status": "fail", "reason": str(error)}
        else:
            report[pool] = {"status": "pass", **result}
    return report


def resolve_variant_contract(demand_meta: Mapping) -> dict[str, dict]:
    """Return each variant's target key and route file from the build's OWN
    published `demand_variant_contract` (the same manifest
    `build_sumo_demand.direction_variant_manifest` writes into
    `demand_meta.json`), instead of guessing route-file suffixes here."""
    contract = demand_meta.get("demand_variant_contract")
    if not isinstance(contract, Mapping) or contract.get("mode") not in {
            "q50_only", "direction_stress"}:
        raise QualificationError(
            "demand metadata is not a q10/q50/q90 direction-stress build")
    entries = contract.get("variants")
    if not isinstance(entries, list):
        raise QualificationError("demand metadata direction-stress/q50 variant contract is malformed")
    expected = {"q50"} if contract["mode"] == "q50_only" else set(REQUIRED_VARIANTS)
    resolved: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise QualificationError("demand metadata variant contract is malformed")
        name = entry.get("name")
        target_key = entry.get("target_key")
        route_file = entry.get("route_file")
        if not isinstance(name, str) or not isinstance(target_key, str) \
                or not isinstance(route_file, str):
            raise QualificationError("demand metadata variant entry is malformed")
        if name not in expected or name in resolved:
            raise QualificationError("demand metadata has extra or duplicate variants")
        suffix = {"q50": "", "q10": "_v1", "q90": "_v2"}[name]
        expected_target = "edge_shares" + ("" if name == "q50" else f"_{name}")
        if route_file != f"calibrated{suffix}.rou.xml" or target_key != expected_target:
            raise QualificationError("demand metadata variant mapping is inconsistent")
        resolved[name] = {"target_key": target_key, "route_file": route_file}
    if set(resolved) != expected:
        raise QualificationError(
            "demand metadata variant contract lacks all three q10/q50/q90 "
            "variants")
    return resolved


def validate_variant_archive(
    variant: str,
    *,
    sumo_dir: Path,
    candidate_routes: Path,
    candidate_metadata: Path,
    demand_meta: Mapping,
    measured_edges: Sequence[str],
    target_key: str,
    route_file: str,
    day_library_root: Path | None = None,
    solver_cache_root: Path | None = None,
    passage_work_root: Path | None = None,
    day_memo: dict[Path, dict] | None = None,
    replay_memo: dict[tuple[Path, str], dict] | None = None,
) -> dict:
    """Independently re-prove one q10/q50/q90 demand variant.

    `validate_calibrated_provenance` re-derives, from the actual XML/JSON
    bytes, that every emitted vehicle resolves one-for-one to a qualified
    candidate and its exact route -- it is not summarised from the archive's
    own claims. The variant's exact rounded per-quarter targets and PFE
    relaxation-ladder rung are read from the SAME `demand_meta.json` this
    build published, keyed by its own `target_key`, and bound by content
    digest so a later swap is detectable.
    """
    if variant not in REQUIRED_VARIANTS:
        raise QualificationError(f"unknown demand variant: {variant!r}")
    sumo_dir = Path(sumo_dir)
    candidate_routes = Path(candidate_routes)
    candidate_metadata = Path(candidate_metadata)
    calibrated_routes = sumo_dir / route_file
    calibrated_agents = calibrated_routes.with_name(
        calibrated_routes.name.replace(".rou.xml", ".agents.json"))
    for path in (candidate_routes, candidate_metadata, calibrated_routes,
                 calibrated_agents):
        if not path.is_file():
            raise QualificationError(f"{variant} demand archive is missing {path}")

    provenance = validate_calibrated_provenance(
        candidate_routes, candidate_metadata,
        [(calibrated_routes, calibrated_agents)])

    sensor_targets = demand_meta.get("sensor_targets") or {}
    targets = (sensor_targets.get("variants") or {}).get(target_key)
    if not isinstance(targets, dict) or not targets:
        raise QualificationError(
            f"{variant} demand metadata lacks exact published targets for "
            f"{target_key!r}")
    fit_variants = demand_meta.get("pfe_fit_variants") or {}
    fit = fit_variants.get(target_key)
    if not isinstance(fit, dict):
        raise QualificationError(
            f"{variant} demand metadata lacks a PFE fit summary for "
            f"{target_key!r}")
    relaxation_summary = fit.get("relaxation_summary")
    if not isinstance(relaxation_summary, dict):
        raise QualificationError(
            f"{variant} demand metadata lacks a relaxation summary for "
            f"{target_key!r}")

    measured = set(map(str, measured_edges))
    if set(targets) != measured:
        raise QualificationError(
            f"{variant} exact targets do not cover exactly the measured sensors")
    lengths = {len(values) for values in targets.values()
               if isinstance(values, list)}
    if len(lengths) != 1 or any(not isinstance(values, list) for values in targets.values()):
        raise QualificationError(f"{variant} exact targets have inconsistent horizons")
    quarters = next(iter(lengths))
    if quarters < 1:
        raise QualificationError(f"{variant} exact targets are empty")
    expected = {}
    for edge, values in sorted(targets.items()):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value)) or value < 0 for value in values):
            raise QualificationError(f"{variant} exact targets contain invalid values")
        expected[edge] = [int(round(float(value))) for value in values]
    if any(value is None for value in (
            day_library_root, solver_cache_root, passage_work_root,
            day_memo, replay_memo)):
        raise QualificationError(f"{variant} passage proof context is missing")
    proof = validate_archive_passage_evidence(
        variant, archive=sumo_dir, demand_meta=demand_meta, expected=expected,
        day_library_root=day_library_root,
        solver_cache_root=solver_cache_root,
        work_root=passage_work_root,
        day_memo=day_memo, replay_memo=replay_memo)
    _require_passage_proof(variant, proof, expected)

    return {
        "variant": variant,
        "target_key": target_key,
        "route_file": route_file,
        "vehicles": provenance["vehicles"],
        "candidate_records": provenance["candidate_records"],
        "provenance_status": provenance["status"],
        "targets_digest": _digest_payload(targets),
        "sensor_incidence_digest": proof["targets_digest"],
        "sensor_incidence_exact": True,
        "sensor_incidence_records": sum(len(values) for values in expected.values()),
        "passage_proof": proof,
        "relaxation_summary": relaxation_summary,
        "content_digests": {
            "candidate_routes": sha256_file(candidate_routes),
            "candidate_metadata": sha256_file(candidate_metadata),
            "calibrated_routes": sha256_file(calibrated_routes),
            "calibrated_agents": sha256_file(calibrated_agents),
        },
    }


def build_manifest(
    *,
    evidence_id: str,
    code_approval: Mapping,
    net_path: Path,
    measured_edges: Sequence[str],
    min_per_sensor: int,
    adoption: Mapping,
    catalog_bindings: Mapping[str, Mapping],
    catalogs: Mapping[str, tuple[Path, Path]],
    archives: Mapping[str, tuple[Path, Mapping]],
    day_library_root: Path = Path("runs/demand-days"),
    solver_cache_root: Path = Path("runs/passage-solver-cache"),
) -> dict:
    """Assemble the single Phase D qualified-demand manifest.

    A missing/malformed producer input (no archive, no candidate pool, not a
    declared-variant build) raises `QualificationError` -- there is nothing
    scientific to report. A genuine support-floor shortfall does not raise:
    it is folded into `support_audit_pass: False` and `status:
    "INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT"`, matching the plan's declared
    fail-closed semantic terminal.
    """
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise QualificationError("qualified-demand manifest needs a fresh evidence ID")
    source_digest = _require_sha256(
        code_approval.get("source_digest") if isinstance(code_approval, Mapping) else None,
        "approved source digest")
    if code_approval.get("status") != "CODE_APPROVED" \
            or code_approval.get("checks_status") != "PASS":
        raise QualificationError("qualified-demand manifest needs CODE_APPROVED checks")
    adopted_catalog_keys = adoption.get("catalog_keys") if isinstance(adoption, Mapping) else None
    if not isinstance(adopted_catalog_keys, Mapping) \
            or set(adopted_catalog_keys) != set(REQUIRED_CATALOG_POOLS):
        raise QualificationError("adoption binding lacks weekday and weekend keys")
    if not archives:
        raise QualificationError("qualified-demand manifest needs qualified archives")

    _, _, network_sha256 = load_network_contract(Path(net_path))

    support_audit = audit_shared_support(
        Path(net_path), measured_edges, min_per_sensor=min_per_sensor,
        catalogs=catalogs)
    support_audit_pass = all(
        report.get("status") == "pass" for report in support_audit.values())

    archive_inventory = {}
    day_memo: dict[Path, dict] = {}
    replay_memo: dict[tuple[Path, str], dict] = {}
    with tempfile.TemporaryDirectory(prefix="qualified-passage-replay-") as temporary:
        passage_work_root = Path(temporary)
        for build_key, (archive_path, validated_record) in sorted(archives.items()):
            archive_path = Path(archive_path).resolve()
            if validated_record.get("build_key") != build_key \
                    or Path(str(validated_record.get("archive", ""))).resolve() != archive_path:
                raise QualificationError(
                    "validated archive record does not match its build key/path")
            demand_meta_path = archive_path / "demand_meta.json"
            demand_meta = _load_json_object(demand_meta_path, "demand metadata")
            variant_contract = resolve_variant_contract(demand_meta)
            variants = {
                variant: validate_variant_archive(
                    variant, sumo_dir=archive_path,
                    candidate_routes=archive_path / "candidates.rou.xml",
                    candidate_metadata=archive_path / "candidates.meta.json",
                    demand_meta=demand_meta, measured_edges=measured_edges,
                    day_library_root=day_library_root,
                    solver_cache_root=solver_cache_root,
                    passage_work_root=passage_work_root,
                    day_memo=day_memo, replay_memo=replay_memo, **entry)
                for variant, entry in sorted(variant_contract.items())
            }
            archive_inventory[build_key] = {
                "build_key": build_key,
                "archive": str(archive_path),
                "demand_build_spec": dict(validated_record["demand_build_spec"]),
                "archive_manifest_sha256": validated_record["archive_manifest_sha256"],
                "archive_content_key": validated_record["archive_content_key"],
                "demand_meta_sha256": sha256_file(demand_meta_path),
                "variants": variants,
            }

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "kind": MANIFEST_KIND,
        "evidence_id": evidence_id,
        "release_evidence": False,
        "status": "PASS" if support_audit_pass
                   else "INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT",
        "code_approved": True,
        "source_digest": source_digest,
        "code_approval": dict(code_approval),
        "network_sha256": network_sha256,
        "sensor_route_policy_version": SENSOR_ROUTE_POLICY_VERSION,
        "adopted_catalog_keys": dict(sorted(adopted_catalog_keys.items())),
        "adoption": dict(adoption),
        "catalogs": {key: dict(value) for key, value in sorted(catalog_bindings.items())},
        "search_contract": dict(FROZEN_SEARCH_CONTRACT),
        "support_audit_pass": support_audit_pass,
        "support_audit": support_audit,
        "archives": archive_inventory,
    }
    if not support_audit_pass:
        # A support-floor terminal cannot authorize any archive even when the
        # already-built bytes were individually valid.  Preserve the audit,
        # but expose an empty consumable inventory.
        manifest["archives"] = {}
        manifest["terminal"] = {
            "stage": "shared_sensor_support_audit",
            "code": "strict_sensor_support_below_floor",
            "error_type": "SensorShortestSupportInconclusive",
            "message": "weekday/weekend strict sensor support did not meet the frozen floor",
        }
    manifest["content_key"] = _digest_payload(manifest)
    try:
        validate_qualified_demand_manifest_shape(manifest)
    except ValueError as error:
        raise QualificationError(str(error)) from error
    return manifest


def build_inconclusive_manifest(
    *, evidence_id: str, code_approval: Mapping, net_path: Path,
    measured_edges: Sequence[str], min_per_sensor: int, adoption: Mapping,
    catalog_bindings: Mapping[str, Mapping],
    catalogs: Mapping[str, tuple[Path, Path]], error: QualificationError,
) -> dict:
    """Publish a precise Phase D terminal instead of crashing mid-producer.

    This is available only after CODE_APPROVED and catalog bindings have been
    validated.  It deliberately contains no qualified archives, so no
    downstream resolver can mistake a partially validated build for input.
    """
    source_digest = _require_sha256(code_approval.get("source_digest"),
                                    "approved source digest")
    _, _, network_sha256 = load_network_contract(Path(net_path))
    support_audit = audit_shared_support(
        Path(net_path), measured_edges, min_per_sensor=min_per_sensor,
        catalogs=catalogs)
    support_audit_pass = all(
        report.get("status") == "pass" for report in support_audit.values())
    scientific = isinstance(error, SensorShortestSupportInconclusive)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "kind": MANIFEST_KIND,
        "evidence_id": evidence_id,
        "release_evidence": False,
        "status": ("INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT" if scientific
                   else "INCONCLUSIVE_DEMAND_QUALIFICATION"),
        "code_approved": True,
        "source_digest": source_digest,
        "code_approval": dict(code_approval),
        "network_sha256": network_sha256,
        "sensor_route_policy_version": SENSOR_ROUTE_POLICY_VERSION,
        "adopted_catalog_keys": dict(sorted(adoption["catalog_keys"].items())),
        "adoption": dict(adoption),
        "catalogs": {
            key: dict(value) for key, value in sorted(catalog_bindings.items())
        },
        "search_contract": dict(FROZEN_SEARCH_CONTRACT),
        "support_audit_pass": support_audit_pass,
        "support_audit": support_audit,
        "archives": {},
        "terminal": {
            "stage": "variant_archive_qualification",
            "code": ("exact_sensor_incidence_infeasible" if scientific
                     else "demand_qualification_failed"),
            "error_type": type(error).__name__,
            "message": str(error),
        },
    }
    manifest["content_key"] = _digest_payload(manifest)
    try:
        validate_qualified_demand_manifest_shape(manifest)
    except ValueError as shape_error:
        raise QualificationError(str(shape_error)) from shape_error
    return manifest


def derive_required_demand_specs(search_spec_paths: Sequence[Path]) -> dict[str, DemandBuildSpec]:
    """Mechanically derive every archive contract consumed by frozen searches."""
    required = {}
    if not search_spec_paths:
        raise QualificationError("Phase D requires frozen search specifications")
    for path in search_spec_paths:
        spec = ClosureSearchSpec.from_dict(_load_json_object(path, "search specification"))
        resolver = MonthlyDemandResolverRunner(
            spec, baseline_trip_duration_p99_s=3600,
            study_provenance_key="phase-d-qualified-demand",
            build_missing=False)
        for parent in iter_closure_schedules(spec):
            for _unit_id, _identity, build_schedule in daily_unit_records(spec, parent):
                demand = resolver._required(build_schedule())
                required[demand.build_key] = demand
    if not required:
        raise QualificationError("frozen search specifications derive no demand archives")
    return dict(sorted(required.items()))


def build_fresh_archives(required: Mapping[str, DemandBuildSpec], fresh_runs_root: Path,
                         *, builder=build_demand_archive) -> dict[str, tuple[Path, Mapping]]:
    """Run one serialized writer per spec and independently validate fresh output."""
    root = Path(fresh_runs_root).resolve()
    if root.exists():
        raise QualificationError(f"fresh demand root already exists: {root}")
    root.mkdir(parents=True)
    result = {}
    for key, spec in sorted(required.items()):
        builder(spec, runs_root=root)
        matches = find_demand_archives(root, spec)
        if len(matches) != 1:
            raise QualificationError(
                f"fresh demand build {key} produced {len(matches)} valid archives")
        record = validate_demand_archive(Path(matches[0]["archive"]), spec)
        result[key] = (Path(record["archive"]), record)
    return result


def load_existing_archives(
    required: Mapping[str, DemandBuildSpec],
    runs_root: Path,
    *,
    finder=find_demand_archives,
    archive_indexer=_archives_for_build_key,
) -> dict[str, tuple[Path, Mapping]]:
    """Load one already-built, fully validated archive per required key.

    This is the no-rebuild counterpart to ``build_fresh_archives``.  The
    archive index is local to this call and is derived from metadata content;
    each candidate still passes ``find_demand_archives``' full content checks.
    Missing or duplicate matches fail closed instead of selecting by recency.
    """
    root = Path(runs_root).resolve()
    if not root.is_dir():
        raise QualificationError(f"existing demand root is absent: {root}")
    archive_index = archive_indexer(root)
    result = {}
    for key, spec in sorted(required.items()):
        matches = finder(root, spec, _archive_index=archive_index)
        if len(matches) != 1:
            raise QualificationError(
                f"existing demand build {key} produced {len(matches)} valid archives")
        record = matches[0]
        archive = Path(str(record.get("archive", ""))).resolve()
        try:
            archive.relative_to(root)
        except ValueError as error:
            raise QualificationError(
                f"existing demand archive escapes its root: {archive}") from error
        result[key] = (archive, record)
    return result


def write_manifest(output_path: Path, manifest: Mapping) -> Path:
    """No-clobber write: refuses to overwrite an existing manifest.

    Every other Phase 3-5 producer artifact is append-only; a Phase D
    manifest that could be silently overwritten would let a later run
    invalidate an already-reviewed checkpoint's evidence in place.
    """
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(
            "qualified-demand manifest already exists (append-only evidence "
            f"cannot be overwritten): {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return output_path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net-path", required=True, type=Path)
    parser.add_argument("--sensor", action="append", required=True, dest="sensors",
                        help="measured edge id; repeat for every sensor")
    parser.add_argument("--min-per-sensor", type=int, required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--checks", required=True, type=Path)
    parser.add_argument("--impact-inventory", required=True, type=Path)
    parser.add_argument("--adoption", type=Path,
                        default=Path("sumo/route_catalog_adoption.json"))
    parser.add_argument("--catalog-root", type=Path,
                        default=Path("sumo/route_catalog"))
    parser.add_argument("--weekday-routes", required=True, type=Path)
    parser.add_argument("--weekday-metadata", required=True, type=Path)
    parser.add_argument("--weekend-routes", required=True, type=Path)
    parser.add_argument("--weekend-metadata", required=True, type=Path)
    parser.add_argument("--search-spec", action="append", required=True, type=Path)
    archive_source = parser.add_mutually_exclusive_group(required=True)
    archive_source.add_argument("--fresh-runs-root", type=Path)
    archive_source.add_argument(
        "--existing-runs-root", type=Path,
        help="reuse exactly one fully validated existing archive per build key")
    parser.add_argument("--day-library-root", type=Path,
                        default=Path("runs/demand-days"))
    parser.add_argument("--passage-solver-cache-root", type=Path,
                        default=Path("runs/passage-solver-cache"))
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def require_consumable_archives(
    manifest: Mapping,
    archives: Mapping[str, tuple[Path, Mapping]],
    *,
    net_path: Path,
) -> None:
    """Refuse PASS for an archive the manifest's own consumers would skip.

    Every resolver selects archives through
    ``qualified_manifest_archive_mismatch``, which silently excludes a
    mismatch.  A manifest this producer certifies must therefore pass that
    exact check for every archive it lists; otherwise the first consumer
    fails in preparation with "no archive", long after qualification.
    """
    for build_key, (_archive, record) in sorted(archives.items()):
        reason = qualified_manifest_archive_mismatch(
            record, manifest, net_path=Path(net_path))
        if reason is not None:
            raise QualificationError(
                f"qualified archive {build_key} is not consumable: {reason}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    approval = validate_code_approval(
        args.source_manifest, args.checks, args.impact_inventory)
    catalogs = {
        "weekday": (args.weekday_routes, args.weekday_metadata),
        "weekend": (args.weekend_routes, args.weekend_metadata),
    }
    adoption, catalog_bindings = validate_adoption_and_catalogs(
        args.adoption, args.catalog_root, catalogs)
    required = derive_required_demand_specs(args.search_spec)
    archives = (
        build_fresh_archives(required, args.fresh_runs_root)
        if args.fresh_runs_root is not None
        else load_existing_archives(required, args.existing_runs_root)
    )
    try:
        manifest = build_manifest(
            evidence_id=args.evidence_id,
            code_approval=approval,
            net_path=args.net_path,
            measured_edges=args.sensors,
            min_per_sensor=args.min_per_sensor,
            adoption=adoption, catalog_bindings=catalog_bindings,
            catalogs=catalogs, archives=archives,
            day_library_root=args.day_library_root,
            solver_cache_root=args.passage_solver_cache_root,
        )
        if manifest["status"] == "PASS":
            require_consumable_archives(
                manifest, archives, net_path=args.net_path)
    except QualificationError as error:
        manifest = build_inconclusive_manifest(
            evidence_id=args.evidence_id, code_approval=approval,
            net_path=args.net_path, measured_edges=args.sensors,
            min_per_sensor=args.min_per_sensor, adoption=adoption,
            catalog_bindings=catalog_bindings, catalogs=catalogs, error=error)
    write_manifest(args.output, manifest)
    print(json.dumps({
        "status": manifest["status"],
        "support_audit_pass": manifest["support_audit_pass"],
        "output": str(args.output),
        "content_key": manifest["content_key"],
    }, indent=1))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
