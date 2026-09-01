"""Content-addressed, day-type route catalogs.

The catalog is deliberately a small storage layer, not a second demand
model.  ``build_candidates.py --catalog-mode`` produces one routed support
set for a structural day type; this module fingerprints that set's inputs,
publishes it atomically, and can combine the weekday/weekend artifacts for a
mixed calendar window.  Calendar-day departure profiles remain the PFE
margin/assignment concern.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Callable, Mapping
import xml.etree.ElementTree as ET

from traffic_sim.core.fingerprint import fingerprint_files, sha256_file
from traffic_sim.demand.sensor_route_contract import (
    POLICY_VERSION as SENSOR_ROUTE_POLICY_VERSION,
    proof_error as sensor_route_proof_error,
)
from traffic_sim.storage.singleflight import content_key_lock


SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("sumo") / "route_catalog"
DEFAULT_INITIAL_N_TOTAL = 6000
ADOPTION_PATH = Path("sumo") / "route_catalog_adoption.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("catalog.rou.xml", "catalog.meta.json", "catalog.validation.json",
           "catalog.template.json")
_DATE_CONFIG_KEYS = {"date", "start_date", "real_day_shape", "day_blocks"}

# WHICH SOURCE BYTES CAN CHANGE A STORED CATALOG ENTRY (narrowed 2026-08-26).
#
# A catalog entry holds exactly ONE artifact: the routed candidate pool that
# ``build_candidates.py --catalog-mode`` writes.  Only code that pool
# generation actually executes can change those bytes.  The LEGACY candidate
# cache stores the same artifact from the same generator and has always been
# keyed on precisely this set of labels; the catalog additionally piled the
# whole 31-entry demand source inventory on top.  That asymmetry was the bug:
#
#   * measured, 6 of the 31 inventory entries are reachable from
#     build_candidates.py's import closure and 25 are not.  The 25 include
#     pfe, pfe_kernel, demand/calibration.py, demand/publication.py,
#     demand/structure.py and this module's own qualification helper - all of
#     which run AFTER the pool exists and cannot alter a single routed edge;
#   * the consequence was not theoretical.  Commit c653b24, whose entire
#     purpose was to HARDEN the catalog's qualification evidence, changed
#     pfe.py, route_catalog.py and catalog_qualification.py and thereby
#     invalidated the adopted catalog - sending production back to the slower
#     legacy builder for a change that could not affect the pool;
#   * binding this module into the identity is also self-referential: every
#     edit to the storage layer invalidated everything the storage layer
#     held.  Stored bytes are already protected by the per-artifact digests
#     in each entry's manifest, which is the check that actually detects a
#     corrupted or substituted entry.
#
# Narrowing does NOT weaken the guarantee that matters.  A real change to
# pool generation still invalidates the catalog through build_candidates.py
# itself, and through sumo/assignment_priors.json, which is a hashed catalog
# INPUT and is regenerated whenever build_candidates.py changes.
CATALOG_SOURCE_LABELS = frozenset({
    "build_candidates",
    "build_sumo_demand",
    "build_data",
    "dirsplit_geo",
    "endpoint_locations",
    "candidate_cache",
    "sensor_registry_loader",
    "direction_anchor",
    "pipeline_fingerprint",
    "sensor_route_contract",
    "closure_disruption",
    "network_metadata",
})


class CatalogSupportError(ValueError):
    """A valid attempt needs a larger route pool for sensor support."""


def catalog_entry_matches(root: Path, *, pool: str, key: str,
                          n_total: int) -> bool:
    """Verify one immutable catalog entry against its adopted identity."""
    try:
        manifest = json.loads((_entry(root, key) / "manifest.json").read_text())
        identity = manifest.get("identity")
        outputs = manifest.get("outputs")
        if (manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("catalog_key") != key
                or not isinstance(identity, dict)
                or identity.get("pool_key") != pool
                or int((identity.get("config") or {}).get("n_total")) != n_total
                or not isinstance(outputs, dict)
                or set(outputs) != set(OUTPUTS)):
            return False
        stored = {label: _entry(root, key) / label for label in OUTPUTS}
        for label, artifact in stored.items():
            record = outputs.get(label)
            if (not isinstance(record, dict)
                    or sha256_file(artifact) != record.get("sha256")):
                return False
        min_per_sensor = int(
            (identity.get("config") or {}).get("min_per_sensor", 1))
        validate_catalog_artifacts(stored, min_per_sensor=min_per_sensor)
        return True
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError,
            json.JSONDecodeError):
        return False


def adopted_catalog_config(
    path: Path = ADOPTION_PATH,
    *,
    root: Path = DEFAULT_ROOT,
) -> dict | None:
    """Return a fully verified adoption record, or ``None`` fail-closed.

    Adoption is an executable provenance contract, not merely a switch.  The
    record must bind both evidence files, the selected sizes and the exact
    immutable catalog entries that are present on this machine.
    """
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    def valid_sha256(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))

    keys = payload.get("catalog_keys") if isinstance(payload, dict) else None
    sizes = (payload.get("catalog_selected_n_total")
             if isinstance(payload, dict) else None)
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != 3
            or payload.get("status") != "adopt"
            or not valid_sha256(payload.get("qualification_sha256"))
            or not valid_sha256(payload.get("catalog_build_sha256"))
            or not isinstance(keys, dict)
            or set(keys) != {"weekday", "weekend"}
            or not isinstance(sizes, dict)
            or set(sizes) != {"weekday", "weekend"}
            or any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 1
                   for value in sizes.values())
            or any(not isinstance(key, str) or len(key) != 32
                   or any(char not in "0123456789abcdef" for char in key)
                   for key in keys.values())):
        return None
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return None
    loaded = {}
    for label in ("qualification", "catalog_build"):
        record = evidence.get(label)
        relative = record.get("path") if isinstance(record, dict) else None
        expected = record.get("sha256") if isinstance(record, dict) else None
        if (not isinstance(relative, str) or not relative
                or Path(relative).is_absolute()
                or not valid_sha256(expected)):
            return None
        evidence_path = (PROJECT_ROOT / relative).resolve()
        try:
            evidence_path.relative_to(PROJECT_ROOT.resolve())
            if sha256_file(evidence_path) != expected:
                return None
            loaded[label] = json.loads(evidence_path.read_text())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None
    if (evidence["qualification"]["sha256"]
            != payload["qualification_sha256"]
            or evidence["catalog_build"]["sha256"]
            != payload["catalog_build_sha256"]):
        return None
    qualification = loaded["qualification"]
    build = loaded["catalog_build"]
    gates = qualification.get("gates") if isinstance(qualification, dict) else None
    binding = (qualification.get("evidence_binding")
               if isinstance(qualification, dict) else None)
    results = build.get("results") if isinstance(build, dict) else None
    if (not isinstance(qualification, dict)
            or qualification.get("verdict") != "adopt"
            or not isinstance(gates, dict) or not gates
            or not all(value is True for value in gates.values())
            or not isinstance(binding, dict)
            or binding.get("catalog_build_sha256")
               != payload["catalog_build_sha256"]
            or binding.get("catalog_keys") != keys
            or binding.get("catalog_selected_n_total") != sizes
            or not isinstance(results, dict)
            or set(results) != {"weekday", "weekend"}):
        return None
    linked_payloads = {}
    for linked_path_key, linked_hash_key in (
            ("trials_path", "trials_sha256"),
            ("suite_gates_path", "suite_gates_sha256")):
        relative = binding.get(linked_path_key)
        expected = binding.get(linked_hash_key)
        if (not isinstance(relative, str) or not relative
                or Path(relative).is_absolute()
                or not valid_sha256(expected)):
            return None
        linked = (PROJECT_ROOT / relative).resolve()
        try:
            linked.relative_to(PROJECT_ROOT.resolve())
            if sha256_file(linked) != expected:
                return None
            linked_payloads[linked_path_key] = json.loads(linked.read_text())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None
    suite_payload = linked_payloads["suite_gates_path"]
    suite_records = (suite_payload.get("gates")
                     if isinstance(suite_payload, dict) else None)
    qualified_suite = qualification.get("suite_hard_gates")
    if (not isinstance(suite_payload, dict)
            or suite_payload.get("schema_version") != 2
            or not isinstance(suite_records, dict)
            or not isinstance(qualified_suite, dict)
            or set(suite_records) != set(qualified_suite)
            or any(not isinstance(value, bool)
                   for value in qualified_suite.values())
            or any(not isinstance(suite_records[gate], dict)
                   or (suite_records[gate].get("status") == "pass")
                      != qualified_suite[gate]
                   for gate in qualified_suite)):
        return None
    for pool, record in results.items():
        if (not isinstance(record, dict)
                or record.get("key") != keys[pool]
                or record.get("n_total") != sizes[pool]):
            return None
    for pool, key in keys.items():
        if not catalog_entry_matches(
                root, pool=pool, key=key, n_total=sizes[pool]):
            return None
    return dict(payload)


def configured_candidate_source(path: Path = ADOPTION_PATH,
                                *, root: Path = DEFAULT_ROOT) -> str:
    """Return the verified default; any provenance failure stays legacy."""
    return "catalog" if adopted_catalog_config(path, root=root) else "legacy"


def _stable_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _flow_edge_set_fingerprint(path: Path) -> dict:
    """Fingerprint sensor-edge membership without daily flow magnitudes."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("source-flow payload must be an object")
    flows = payload.get("flows")
    if isinstance(flows, dict):
        edges = sorted(str(edge) for edge in flows)
    elif isinstance(payload.get("edges"), list):
        # Small synthetic identity fixtures may carry only the projection.
        edges = sorted(str(edge) for edge in payload["edges"])
    else:
        raise ValueError("source-flow payload has no edge mapping")
    if not edges or len(edges) != len(set(edges)):
        raise ValueError("source-flow edge projection is empty or duplicated")
    canonical = _stable_json(edges)
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "edges": len(edges),
    }


def catalog_identity_payload(
    config: Mapping[str, object],
    inputs: Mapping[str, Path],
    source_files: Mapping[str, Path],
    *,
    pool_key: str,
) -> dict:
    """Derive a catalog identity from the candidate identity subtractively.

    Only date-dependent demand shape inputs are removed.  Network, routing,
    generator code, sensor registry, route-count and quality gates remain in
    the identity, so a stale catalog cannot silently stand in for a changed
    structural build.
    """
    if pool_key not in {"weekday", "weekend"}:
        raise ValueError("pool_key must be weekday or weekend")
    kept_config = {
        str(k): v for k, v in config.items()
        if str(k) not in _DATE_CONFIG_KEYS
    }
    kept_inputs = {
        str(k): Path(v) for k, v in inputs.items()
        if str(k) not in {
            "real_day_shape", "day_blocks", "source_flows",
            "source_flow_edge_set",
        }
    }
    # The structural edge set is deliberately retained even when daily flow
    # magnitudes are removed.  It prevents a catalog built for a different
    # sensor/network projection from being reused.
    edge_set = None
    if "source_flow_edge_set" in inputs:
        edge_set = _flow_edge_set_fingerprint(
            Path(inputs["source_flow_edge_set"]))
    # Fail closed in BOTH directions.  A missing label would silently drop a
    # generator input from the identity; an unexpected one is how the whole
    # demand inventory crept in.  Either way the caller must decide
    # deliberately rather than have the key quietly change meaning.
    provided = set(source_files)
    missing = CATALOG_SOURCE_LABELS - provided
    unexpected = provided - CATALOG_SOURCE_LABELS
    if missing or unexpected:
        raise ValueError(
            "catalog source files must match the declared contract exactly; "
            f"missing={sorted(missing)} unexpected={sorted(unexpected)}")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "pool_key": pool_key,
        "config": kept_config,
        "inputs": fingerprint_files(kept_inputs),
        "source_files": fingerprint_files(source_files),
    }
    if edge_set is not None:
        payload["inputs"]["source_flow_edge_set"] = edge_set
    return payload


def catalog_key(config: Mapping[str, object], inputs: Mapping[str, Path],
                source_files: Mapping[str, Path], *, pool_key: str) -> str:
    payload = catalog_identity_payload(config, inputs, source_files,
                                       pool_key=pool_key)
    return hashlib.sha256(_stable_json(payload)).hexdigest()[:32]


def _entry(root: Path, key: str) -> Path:
    if not isinstance(key, str) or len(key) != 32 or any(
            c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid route catalog key")
    return Path(root) / key


def _manifest_for(key: str, identity: Mapping[str, object],
                  outputs: Mapping[str, Path]) -> dict:
    records = {}
    for label, path in outputs.items():
        digest = sha256_file(Path(path))
        if digest is None:
            raise FileNotFoundError(path)
        records[label] = {"sha256": digest, "bytes": Path(path).stat().st_size}
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_key": key,
        "identity": identity,
        "outputs": records,
    }


def catalog_size_attempts(start: int, *, attempts: int = 3,
                          growth: float = 1.5) -> tuple[int, ...]:
    """Return a bounded, deterministic support-sizing ladder."""
    if start < 1 or attempts < 1 or not math.isfinite(growth) or growth <= 1:
        raise ValueError("catalog sizing requires positive start/attempts and growth > 1")
    values = [int(start)]
    while len(values) < attempts:
        values.append(max(values[-1] + 1, int(math.ceil(values[-1] * growth))))
    return tuple(values)


def validate_catalog_artifacts(outputs: Mapping[str, Path],
                               *, min_per_sensor: int) -> dict:
    """Fail closed on malformed route, metadata or sensor-support artifacts."""
    if set(outputs) != set(OUTPUTS):
        raise ValueError("route catalog output set is incomplete")
    route_root = ET.parse(outputs["catalog.rou.xml"]).getroot()
    meta = json.loads(Path(outputs["catalog.meta.json"]).read_text())
    coverage = json.loads(Path(outputs["catalog.validation.json"]).read_text())
    template = json.loads(Path(outputs["catalog.template.json"]).read_text())
    if not isinstance(meta, dict) or not isinstance(meta.get("candidates"), dict):
        raise ValueError("route catalog metadata candidates must be an object")
    if not isinstance(meta.get("location_pools", {}), dict):
        raise ValueError("route catalog location_pools must be an object")
    if not isinstance(coverage, dict) or not coverage:
        raise ValueError("route catalog sensor coverage must be a non-empty object")
    contract = meta.get("sensor_route_contract")
    if (not isinstance(contract, dict)
            or contract.get("policy_version") != SENSOR_ROUTE_POLICY_VERSION):
        raise ValueError("route catalog lacks the strict sensor-route contract")
    if (not isinstance(template, dict)
            or template.get("schema_version") != 1
            or not isinstance(template.get("templates"), int)
            or template.get("templates", 0) < 1
            or not isinstance(template.get("semantic_sha256"), str)
            or len(template["semantic_sha256"]) != 64):
        raise ValueError("route catalog canonical-template report is invalid")
    vehicle_ids = []
    route_by_id: dict[str, list[str]] = {}
    for vehicle in route_root.findall("vehicle"):
        vehicle_id = vehicle.get("id")
        route = vehicle.find("route")
        if (not vehicle_id or route is None
                or not (route.get("edges") or "").split()):
            raise ValueError("route catalog contains a malformed vehicle")
        vehicle_ids.append(vehicle_id)
        route_by_id[vehicle_id] = (route.get("edges") or "").split()
    if not vehicle_ids or len(vehicle_ids) != len(set(vehicle_ids)):
        raise ValueError("route catalog vehicle ids are empty or duplicated")
    missing_meta = sorted(set(vehicle_ids) - set(meta["candidates"]))
    if missing_meta:
        raise ValueError(f"route catalog metadata is missing {len(missing_meta)} vehicles")
    sensor_edges = set(str(edge) for edge in coverage)
    invalid_contracts = []
    for vehicle_id in vehicle_ids:
        record = meta["candidates"].get(vehicle_id)
        error = sensor_route_proof_error(
            route_by_id[vehicle_id],
            record.get("sensor_route_contract") if isinstance(record, dict) else None,
            sensor_edges)
        if error is not None:
            invalid_contracts.append((vehicle_id, error))
    if invalid_contracts:
        vehicle_id, error = invalid_contracts[0]
        raise ValueError(
            "route catalog candidate violates the strict sensor-route "
            f"contract: {vehicle_id}:{error}")
    floor = max(1, int(min_per_sensor))
    weak = {}
    for edge, record in sorted(coverage.items()):
        if not isinstance(record, dict):
            raise ValueError(f"route catalog coverage record {edge!r} is not an object")
        unique_routes = record.get("unique_routes")
        if not isinstance(unique_routes, int) or unique_routes < 0:
            raise ValueError(f"route catalog coverage record {edge!r} is invalid")
        if unique_routes < floor:
            weak[str(edge)] = unique_routes
    if weak:
        raise CatalogSupportError(
            f"route catalog sensor support below {floor}: {weak}")
    return {
        "vehicles": len(vehicle_ids),
        "sensors": len(coverage),
        "min_unique_routes": min(
            int(record["unique_routes"]) for record in coverage.values()),
    }


def restore_catalog(root: Path, key: str, outputs: Mapping[str, Path],
                    *, expected_identity: Mapping[str, object] | None = None) -> bool:
    """Verify and atomically restore a complete catalog; return cache-hit."""
    entry = _entry(root, key)
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
        if (manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("catalog_key") != key):
            return False
        if expected_identity is not None and manifest.get("identity") != expected_identity:
            return False
        stored = manifest.get("outputs") or {}
        if set(stored) != set(outputs):
            return False
        for label in outputs:
            source = entry / label
            if sha256_file(source) != stored[label].get("sha256"):
                return False
        identity_config = manifest.get("identity", {}).get("config", {})
        min_per_sensor = int(identity_config.get("min_per_sensor", 1))
        validate_catalog_artifacts(
            {label: entry / label for label in outputs},
            min_per_sensor=min_per_sensor)
        for label, destination in outputs.items():
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".catalog.tmp")
            shutil.copy2(entry / label, temporary)
            os.replace(temporary, destination)
        return True
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, ET.ParseError,
            json.JSONDecodeError):
        return False


def publish_catalog(root: Path, key: str, outputs: Mapping[str, Path],
                    identity: Mapping[str, object]) -> None:
    """Publish immutable catalog bytes and a manifest atomically."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entry = _entry(root, key)
    with tempfile.TemporaryDirectory(prefix=f".{key}.", dir=str(root)) as tmp:
        staging = Path(tmp)
        for label, source in outputs.items():
            source = Path(source)
            if sha256_file(source) is None:
                raise FileNotFoundError(source)
            shutil.copy2(source, staging / label)
        (staging / "manifest.json").write_text(json.dumps(
            _manifest_for(key, identity, outputs), indent=1, sort_keys=True))
        quarantine = None
        if entry.exists():
            # A hash/parse-invalid immutable entry is a cache miss. Preserve
            # it for diagnosis, then atomically install the rebuilt entry;
            # os.replace cannot replace a non-empty directory directly.
            quarantine = root / f".{key}.invalid.{uuid.uuid4().hex}"
            os.replace(entry, quarantine)
        try:
            os.replace(staging, entry)
        except BaseException:
            if (quarantine is not None and quarantine.exists()
                    and not entry.exists()):
                os.replace(quarantine, entry)
            raise


def ensure_catalog(
    root: Path,
    key: str,
    identity: Mapping[str, object],
    outputs: Mapping[str, Path],
    builder: Callable[[Path], Mapping[str, Path]],
    *,
    lock_timeout_s: float = 600.0,
) -> bool:
    """Restore or single-flight-build a catalog.

    ``builder`` receives an empty temporary directory and returns the three
    output paths to publish.  A failed builder never leaves a partial entry.
    """
    if restore_catalog(root, key, outputs, expected_identity=identity):
        return True
    with content_key_lock(Path(root), key, timeout_s=lock_timeout_s):
        if restore_catalog(root, key, outputs, expected_identity=identity):
            return True
        with tempfile.TemporaryDirectory(prefix="catalog-build-") as tmp:
            built = {str(k): Path(v) for k, v in builder(Path(tmp)).items()}
            if set(built) != set(outputs):
                raise ValueError("catalog builder returned an incomplete output set")
            for label in outputs:
                if sha256_file(built[label]) is None:
                    raise FileNotFoundError(built[label])
            publish_catalog(root, key, built, identity)
        return restore_catalog(root, key, outputs, expected_identity=identity)


def candidate_catalog_command(
    *,
    output_dir: Path,
    pool_key: str,
    n_total: int,
    through_fraction: float,
    gravity_km: float,
    gravity_alpha: float,
    cross_fraction: float,
    assignment_priors: Path,
    seed: int,
    min_per_sensor: int = 50,
    python: str = sys.executable,
    script: Path = Path("build_candidates.py"),
    probe_date: str | None = None,
) -> list[str]:
    """One shared command contract for the server path and thin CLI."""
    command = [
        python, str(script), "--catalog-mode", "--catalog-pool-key", pool_key,
        "--through-fraction", str(through_fraction),
        "--gravity-km", str(gravity_km),
        "--gravity-alpha", str(gravity_alpha),
        "--cross-fraction", str(cross_fraction),
        "--assignment-priors", str(assignment_priors),
        "--n-total", str(n_total), "--min-per-sensor", str(min_per_sensor),
        "--seed", str(seed), "--out-dir", str(output_dir),
    ]
    if probe_date is not None:
        command.extend(["--date", str(probe_date)])
    return command


def ensure_sized_catalog(
    *,
    root: Path,
    pool_key: str,
    base_config: Mapping[str, object],
    inputs: Mapping[str, Path],
    source_files: Mapping[str, Path],
    destinations: Mapping[str, Path],
    command_for: Callable[[int, Path], list[str]],
    start_n_total: int,
    min_per_sensor: int,
    attempts: int = 3,
    growth: float = 1.5,
    timeout_s: float = 1200.0,
) -> dict:
    """Restore or build the smallest passing catalog in a bounded ladder."""
    attempt_records = []
    last_error = None
    for n_total in catalog_size_attempts(
            start_n_total, attempts=attempts, growth=growth):
        config = dict(base_config, n_total=n_total,
                      min_per_sensor=int(min_per_sensor))
        identity = catalog_identity_payload(
            config, inputs, source_files, pool_key=pool_key)
        key = catalog_key(config, inputs, source_files, pool_key=pool_key)
        if restore_catalog(root, key, destinations, expected_identity=identity):
            return {
                "key": key, "n_total": n_total, "cache_event": "hit",
                "attempts": attempt_records + [{
                    "n_total": n_total, "status": "restored"}],
            }

        def builder(work: Path) -> Mapping[str, Path]:
            producer = work / "producer"
            producer.mkdir()
            result = subprocess.run(
                command_for(n_total, producer), capture_output=True, text=True,
                timeout=timeout_s)
            built = {
                "catalog.rou.xml": producer / "candidates.rou.xml",
                "catalog.meta.json": producer / "candidates.meta.json",
                "catalog.validation.json": producer / "sensor_coverage_report.json",
                "catalog.template.json": producer / "canonical_template_report.json",
            }
            if result.returncode != 0:
                if all(Path(path).is_file() for path in built.values()):
                    # A normal under-support exit writes complete diagnostics;
                    # distinguish that one bounded sizing condition from an
                    # unrelated router/runtime/build failure.
                    validate_catalog_artifacts(
                        built, min_per_sensor=min_per_sensor)
                raise RuntimeError(
                    (result.stderr or result.stdout or "catalog builder failed")[-4000:])
            validate_catalog_artifacts(
                built, min_per_sensor=min_per_sensor)
            return built

        try:
            ready = ensure_catalog(root, key, identity, destinations, builder,
                                   lock_timeout_s=timeout_s)
        except CatalogSupportError as exc:
            last_error = exc
            attempt_records.append({
                "n_total": n_total, "status": "failed",
                "error": str(exc)[-1000:],
            })
            continue
        if not ready:
            raise RuntimeError(
                f"route catalog {key} failed verification after publication")
        attempt_records.append({"n_total": n_total, "status": "built"})
        return {
            "key": key, "n_total": n_total, "cache_event": "miss",
            "attempts": attempt_records,
        }
    raise RuntimeError(
        "no catalog size passed the bounded support ladder: "
        + json.dumps(attempt_records, sort_keys=True)) from last_error


def combine_catalogs(
    sources: Mapping[str, tuple[Path, Path]],
    out_rou: Path,
    out_meta: Path,
    *,
    pool_order: tuple[str, ...] = ("weekday", "weekend"),
) -> dict:
    """Merge day-type route XML and metadata without ID or pool collisions."""
    root = ET.Element("routes", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:noNamespaceSchemaLocation":
            "http://sumo.dlr.de/xsd/routes_file.xsd",
    })
    merged = {"schema_version": 3, "location_pools": {}, "candidates": {}}
    merged_contract = None
    qualified = 0
    count = 0
    for pool in pool_order:
        if pool not in sources:
            continue
        rou_path, meta_path = sources[pool]
        tree = ET.parse(rou_path)
        meta = json.loads(Path(meta_path).read_text())
        if not isinstance(meta, dict):
            raise ValueError(f"catalog metadata for {pool} is not an object")
        contract = meta.get("sensor_route_contract")
        if (not isinstance(contract, dict)
                or contract.get("policy_version") != SENSOR_ROUTE_POLICY_VERSION):
            raise ValueError(
                f"catalog metadata for {pool} lacks strict sensor routes")
        # `qualified_candidates` counts what THIS pool qualified; it is a
        # per-pool statistic, not a term of the contract. Comparing it would
        # refuse every merge whose pools happen to qualify different totals,
        # which is the normal case (measured: 434 weekday vs 435 weekend).
        # Only the policy terms have to agree.
        policy = {key: value for key, value in contract.items()
                  if key != "qualified_candidates"}
        qualified += int(contract.get("qualified_candidates") or 0)
        if merged_contract is None:
            merged_contract = policy
        elif policy != merged_contract:
            raise ValueError("catalog sensor-route contracts disagree")
        prefix = f"{pool}__"
        for vehicle in tree.getroot().findall("vehicle"):
            old_id = vehicle.get("id")
            if not old_id:
                raise ValueError(f"catalog vehicle in {pool} has no id")
            new_id = prefix + old_id
            vehicle.set("id", new_id)
            root.append(vehicle)
            record = (meta.get("candidates") or {}).get(old_id)
            if record is None:
                raise ValueError(f"catalog metadata is missing candidate {old_id}")
            if not isinstance(record, dict):
                raise ValueError(
                    f"catalog metadata candidate {old_id} is not an object")
            merged_record = dict(record)
            tour_id = merged_record.get("tour_id")
            if tour_id is not None:
                if not isinstance(tour_id, str) or not tour_id:
                    raise ValueError(
                        f"catalog metadata candidate {old_id} has invalid tour_id")
                merged_record["tour_id"] = prefix + tour_id
            merged["candidates"][new_id] = merged_record
            count += 1
        for key, value in (meta.get("location_pools") or {}).items():
            previous = merged["location_pools"].get(key)
            if previous is not None and previous != value:
                raise ValueError(f"conflicting location pool definition: {key}")
            merged["location_pools"][key] = value
    if count == 0:
        raise ValueError("cannot combine an empty route catalog")
    if merged_contract is not None:
        merged_contract = dict(merged_contract)
        merged_contract["qualified_candidates"] = qualified
    merged["sensor_route_contract"] = merged_contract
    out_rou = Path(out_rou)
    out_meta = Path(out_meta)
    out_rou.parent.mkdir(parents=True, exist_ok=True)
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_rou, encoding="utf-8", xml_declaration=False)
    out_meta.write_text(json.dumps(merged, separators=(",", ":"), sort_keys=True))
    return {"vehicles": count, "location_pools": len(merged["location_pools"])}
