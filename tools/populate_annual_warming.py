#!/usr/bin/env python3
"""Preflight, initialize, resume, or inspect 2027 annual warm population.

The default actions are read-only.  ``--initialize`` creates only a canonical
plan/progress root.  ``--execute`` is the expensive path: it resolves or builds
one exact daily demand archive at a time and launches isolated worker
interpreters for candidate-free SUMO/TraCI prefix snapshots.  No artifact is
marked succeeded until compressed storage, restore, and semantic validation all
pass.  This tool never activates the resulting bank in the product.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.plan_annual_warming import (  # noqa: E402
    DEFAULT_OUTPUT as DEFAULT_PLAN,
    build_default_plan,
)
from traffic_sim.core.contracts import (  # noqa: E402
    DemandBuildSpec,
)
from traffic_sim.core.fingerprint import sha256_file, sumo_version  # noqa: E402
from traffic_sim.simulation.annual_warm_plan import (  # noqa: E402
    iter_work_units,
    load_annual_warm_plan,
    verify_fingerprint_inputs,
)
from traffic_sim.simulation.annual_warm_progress import (  # noqa: E402
    AnnualWarmProgressStore,
)
from traffic_sim.simulation.annual_warm_population import (  # noqa: E402
    AnnualWarmPlanContext,
    annual_unit_context,
    build_annual_prefix_runner,
    build_unit_metadata,
    validate_restored_unit_payload,
    validate_unit_artifact,
)
from traffic_sim.simulation.annual_warm_store import (  # noqa: E402
    AnnualWarmArtifactStore,
)


DEFAULT_ROOT_PARENT = Path("runs") / "annual-warm-2027"
PROGRESS_FILENAME = "progress.sqlite3"
PLAN_FILENAME = "plan.json"
RUNTIME_FREE_RESERVE_BYTES = 8 * 1024**3
# A flat whole-year threshold refused every bounded run for work it would never
# do, and it was sized around retaining all 367 three-day demand archives at
# once.  Retention was the dominant term, not the artifacts: 367 x 326 MiB is
# ~117 GiB, against ~42 GiB of stores.  Archives are now pruned once their
# chains are durably complete -- the artifact store already binds the route,
# meta, spec and manifest it needs -- and the requirement is derived from the
# units this invocation can actually select.
#
# Measured on the real 96-link q10 pilot chain
# (runs/annual-warm-pilots/84f4728e-e10c-q10) and a canonical three-day archive:
#   unsealed working blobs     40.4 MiB per 96-link chain -> 432 KiB per unit
#   sealed chain container      3.79 MiB per 96-link chain
#   three-day demand archive  326    MiB
# Per-unit storage is charged at the UNSEALED rate so the gate stays valid for
# the window before a chain seals, then real usage falls below it as chains seal.
PER_UNIT_STORE_BYTES = 432 * 1024
DEMAND_ARCHIVE_BYTES = 326 * 1024**2
# Only the archive being worked and the one resolved next are ever live together.
CONCURRENT_DEMAND_ARCHIVES = 2
# Staging, SQLite WAL growth, SUMO scratch and the sealing workspace.
FIXED_OVERHEAD_BYTES = 4 * 1024**3
# A run spanning days over 104,685 units will meet transient SUMO/TraCI startup
# failures. One was observed in production: a worker's SUMO did not accept its
# TraCI socket ("Could not connect in 61 tries") while its two siblings started
# normally on the same demand build, 38 units into that build's chain. Aborting
# the whole population for that discards every completed chain.
#
# The retry is deliberately narrow. Only a failure to establish the process or
# its control socket qualifies; every validation, provenance, artifact and
# semantic failure keeps aborting immediately, because those indicate the bank
# would be wrong rather than merely delayed. Retries are logged, never silent.
TRANSIENT_UNIT_RETRY_LIMIT = 3


def _is_transient_launch_failure(error: BaseException) -> bool:
    """Whether a unit failed to START rather than failed to be CORRECT."""
    if isinstance(error, (ConnectionRefusedError, ConnectionResetError)):
        return True
    name = type(error).__name__
    text = str(error).lower()
    if "traci" in name.lower() and (
        "connect" in text or "closed" in text or "socket" in text
    ):
        return True
    return False


def required_free_bytes(
    plan: Mapping[str, Any],
    *,
    pending_units: int,
    keep_demand_archives: bool,
) -> int:
    """Disk this invocation needs, derived from the work it can select."""
    if isinstance(pending_units, bool) or not isinstance(pending_units, int) \
            or pending_units < 0:
        raise ValueError("pending_units must be a non-negative integer")
    archives = (
        len(plan["demand_build_specs"]) if keep_demand_archives
        else CONCURRENT_DEMAND_ARCHIVES
    )
    return (
        pending_units * PER_UNIT_STORE_BYTES
        + archives * DEMAND_ARCHIVE_BYTES
        + FIXED_OVERHEAD_BYTES
        + RUNTIME_FREE_RESERVE_BYTES
    )
VARIANT_FILENAMES = {
    "q10": "calibrated_v1.rou.xml",
    "q50": "calibrated.rou.xml",
    "q90": "calibrated_v2.rou.xml",
}
_POOL_PLAN: dict[str, Any] | None = None
_POOL_PLAN_CONTEXT: AnnualWarmPlanContext | None = None
_POOL_ROOT: Path | None = None
_POOL_REFERENCE_EDGE: str | None = None
_POOL_RUNNER_KEY: tuple[str, str, str] | None = None
_POOL_RUNNER: Any | None = None


def _pooled_runner_factory(spec, **kwargs):
    """Reuse one immutable archived-demand runner per worker and build."""
    global _POOL_RUNNER_KEY, _POOL_RUNNER
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    required = kwargs.get("expected_demand_spec")
    archive = Path(kwargs["archive"]).resolve()
    key = (
        str(required.build_key), str(archive), str(spec.content_key)
    )
    if _POOL_RUNNER is not None and _POOL_RUNNER_KEY == key:
        return _POOL_RUNNER
    cleanup = getattr(_POOL_RUNNER, "cleanup", None)
    if callable(cleanup):
        cleanup()
    _POOL_RUNNER = ArchivedDemandSumoRunner(spec, **kwargs)
    _POOL_RUNNER_KEY = key
    return _POOL_RUNNER


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--plan-key")
    parser.add_argument("--state-workers", type=int, default=3)
    parser.add_argument("--max-units", type=int)
    parser.add_argument(
        "--demand-build-key",
        help="Optional exact daily build filter for a bounded pilot.",
    )
    parser.add_argument(
        "--variant", choices=tuple(VARIANT_FILENAMES),
        help="Optional q10/q50/q90 chain filter for a bounded pilot.",
    )
    parser.add_argument(
        "--keep-demand-archives", action="store_true",
        help=(
            "Retain every three-day demand archive after its chains complete. "
            "Retention is what made the whole-year run need ~117 GiB of "
            "archives; the artifact store already binds every member it needs."
        ),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--initialize", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--execute", action="store_true")
    return parser


def _current_plan(path: Path) -> dict[str, Any]:
    path = path if path.is_absolute() else ROOT / path
    stored = load_annual_warm_plan(path)
    expected = build_default_plan()
    if stored != expected:
        raise ValueError("annual plan differs from current bound sources")
    return stored


def _root_for(plan: Mapping[str, Any], requested: Path | None) -> Path:
    if requested is None:
        return ROOT / DEFAULT_ROOT_PARENT / str(plan["content_key"])
    return requested if requested.is_absolute() else ROOT / requested


def _require_plan_key(plan: Mapping[str, Any], supplied: str | None) -> None:
    if supplied != plan["content_key"]:
        raise ValueError(
            "mutation requires --plan-key equal to the current annual plan: "
            + str(plan["content_key"])
        )


def _verify_plan_source_seal(plan: Mapping[str, Any]) -> None:
    """Recheck bound inputs between demand groups during a long execution."""
    verify_fingerprint_inputs(plan["input_fingerprints"], base=ROOT)


def _reference_edge(network: Path) -> str:
    network = Path(network)
    for _, element in ET.iterparse(network, events=("end",)):
        if element.tag == "edge":
            edge_id = element.get("id")
            function = element.get("function")
            element.clear()
            if edge_id and not edge_id.startswith(":") and function != "internal":
                return edge_id
        element.clear()
    raise ValueError("active SUMO network has no ordinary edge")


def _localhost_preflight() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
        if address[0] != "127.0.0.1" or not int(address[1]):
            raise ValueError("localhost bind returned an invalid address")
    finally:
        probe.close()


def _free_bytes_without_create(root: Path) -> int:
    disk_probe = Path(root).parent
    while not disk_probe.exists():
        if disk_probe == disk_probe.parent:
            raise ValueError("annual root has no existing filesystem ancestor")
        disk_probe = disk_probe.parent
    return shutil.disk_usage(disk_probe).free


def _prune_demand_archive(
    archive: Path, demand_key: str, progress: AnnualWarmProgressStore
) -> bool:
    """Delete a three-day archive once every unit that needs it has succeeded.

    Retaining all 367 archives was ~117 GiB, far more than the artifact store
    itself. Nothing needs them afterwards: ``pack_artifact`` copies the route,
    demand meta, build spec and manifest into content-addressed blobs, and the
    group loop only resolves an archive when that build still has selectable
    units. Refusing to prune while any unit for the build is unfinished is what
    keeps a resumed or ``--variant``-filtered run correct.
    """
    if progress.selectable(demand_build_key=demand_key):
        return False
    archive = Path(archive)
    # Only ever remove a canonical demand archive inside this repository's runs
    # directory, and never traverse a symlink out of it.
    runs_root = (ROOT / "runs").resolve()
    resolved = archive.resolve()
    if resolved.parent != runs_root or not resolved.name.startswith("demand-"):
        return False
    if archive.is_symlink() or not resolved.is_dir():
        return False
    shutil.rmtree(resolved)
    return True


def _runtime_disk_guard(root: Path) -> None:
    free = _free_bytes_without_create(root)
    if free < RUNTIME_FREE_RESERVE_BYTES:
        raise RuntimeError(
            "annual warming stopped before disk exhaustion: "
            f"{free} bytes free, {RUNTIME_FREE_RESERVE_BYTES} required"
        )


def _verify_initialized_root(
    plan: Mapping[str, Any], root: Path
) -> AnnualWarmProgressStore:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"annual root is not a regular directory: {root}")
    plan_path = root / PLAN_FILENAME
    if plan_path.is_symlink() or not plan_path.is_file():
        raise ValueError("annual root plan is not a regular file")
    stored_plan = load_annual_warm_plan(plan_path)
    if stored_plan != plan:
        raise ValueError("annual root plan differs from the current plan")
    for name in ("store", ".staging"):
        child = root / name
        if child.is_symlink() or not child.is_dir():
            raise ValueError(f"annual root {name} is not a regular directory")
    progress = AnnualWarmProgressStore(root / PROGRESS_FILENAME, plan)
    progress.verify()
    return progress


def _pending_unit_count(plan: Mapping[str, Any], root: Path) -> int:
    """Units still to do, or the whole plan when no root exists yet."""
    try:
        progress = _verify_initialized_root(plan, root)
    except (ValueError, FileNotFoundError):
        return len(tuple(iter_work_units(plan)))
    summary = progress.summary()
    return int(summary["total"]) - int(summary["succeeded"])


def production_preflight(
    plan: Mapping[str, Any],
    root: Path,
    *,
    state_workers: int,
    keep_demand_archives: bool = False,
) -> dict[str, Any]:
    if isinstance(state_workers, bool) or not isinstance(state_workers, int) \
            or state_workers < 1:
        raise ValueError("state_workers must be a positive integer")
    from traffic_sim.simulation.monthly_sumo import approved_seed_workers
    from traffic_sim.simulation.runtime import (
        resolve_traci,
        sumo_home,
        validate_traci_api,
    )

    approved = approved_seed_workers()
    if state_workers > approved:
        raise ValueError(
            f"state_workers={state_workers} exceeds approved isolated SUMO "
            f"worker count {approved}"
        )
    home = sumo_home()
    current_sumo_version = sumo_version(home)
    runtime = plan["runtime_identity"]
    if (
        runtime.get("sumo_version") != current_sumo_version
        or runtime.get("platform") != platform.platform()
    ):
        raise ValueError("annual plan runtime identity differs from this host")
    traci = validate_traci_api(resolve_traci(home))
    _localhost_preflight()
    network = ROOT / "sumo/net.net.xml"
    network_record = plan["input_fingerprints"].get("network")
    if not isinstance(network_record, Mapping) \
            or sha256_file(network) != network_record.get("sha256"):
        raise ValueError("annual plan network fingerprint differs")
    edge = _reference_edge(network)
    free = _free_bytes_without_create(root)
    pending = _pending_unit_count(plan, root)
    minimum_free_bytes = required_free_bytes(
        plan,
        pending_units=pending,
        keep_demand_archives=keep_demand_archives,
    )
    if free < minimum_free_bytes:
        raise ValueError(
            f"annual warming requires at least {minimum_free_bytes} free bytes; "
            f"only {free} are available"
        )
    return {
        "plan_content_key": plan["content_key"],
        "sumo_home": str(home),
        "sumo_version": current_sumo_version,
        "traci": traci,
        "network_sha256": network_record["sha256"],
        "reference_edge": edge,
        "state_workers": state_workers,
        "approved_state_workers": approved,
        "pending_units": pending,
        "keep_demand_archives": keep_demand_archives,
        "free_bytes": free,
        "minimum_free_bytes": minimum_free_bytes,
    }


def initialize_root(plan: Mapping[str, Any], root: Path) -> dict[str, Any]:
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"annual root is not a regular directory: {root}")
        return _verify_initialized_root(plan, root).summary()
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=root.name + ".", dir=root.parent))
    try:
        from traffic_sim.simulation.annual_warm_plan import write_annual_warm_plan
        write_annual_warm_plan(staging / PLAN_FILENAME, plan)
        (staging / "store").mkdir()
        (staging / ".staging").mkdir()
        progress = AnnualWarmProgressStore(staging / PROGRESS_FILENAME, plan)
        progress.initialize()
        os.replace(staging, root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _verify_initialized_root(plan, root).summary()


def _resolve_demand_archive(required: DemandBuildSpec) -> dict[str, Any]:
    from traffic_sim.simulation.monthly_demand import (
        LIVE_DEMAND_RELEASE_PRODUCTS,
        build_demand_archive,
        demand_build_lock,
        find_demand_archives,
        restore_live_demand_release,
        snapshot_live_demand_release,
    )

    matches = find_demand_archives(ROOT / "runs", required)
    if matches:
        return matches[0]
    with demand_build_lock():
        # Recheck after waiting: another monthly/annual process may have built
        # the exact content while this process was blocked on the live release.
        matches = find_demand_archives(ROOT / "runs", required)
        if matches:
            return matches[0]
        snapshot = snapshot_live_demand_release(
            root=ROOT, products=LIVE_DEMAND_RELEASE_PRODUCTS
        )
        try:
            build_demand_archive(required)
        finally:
            restore_live_demand_release(snapshot)
        matches = find_demand_archives(ROOT / "runs", required)
        if not matches:
            raise RuntimeError(
                "demand build completed without a valid archive: "
                + required.build_key
            )
        return matches[0]


def _worker_artifact(
    plan: Mapping[str, Any],
    *,
    root: Path,
    unit_id: str,
    archive: Path,
    reference_edge: str,
    unit_hint: Mapping[str, Any] | None = None,
    predecessor_unit: Mapping[str, Any] | None = None,
    plan_context: AnnualWarmPlanContext | None = None,
    validated_archive_record: Mapping[str, Any] | None = None,
    runner_factory=None,
    controller_factory=None,
) -> dict[str, Any]:
    from traffic_sim.simulation.monthly_demand import validate_demand_archive
    from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence

    indexed = plan_context or AnnualWarmPlanContext(plan)
    context = annual_unit_context(
        plan, unit_id, unit_hint=unit_hint, plan_context=indexed
    )
    unit = context["unit"]
    request = context["request"]
    required = DemandBuildSpec.from_dict(context["demand_build_spec"])
    archive_record = (
        validate_demand_archive(archive, required)
        if validated_archive_record is None
        else dict(validated_archive_record)
    )
    predecessor_unit_id = (None if predecessor_unit is None
                           else str(predecessor_unit["unit_id"]))
    metadata = build_unit_metadata(
        plan, unit_id, demand_archive=archive_record, unit_hint=unit_hint,
        predecessor_unit_id=predecessor_unit_id,
        plan_context=indexed,
    )
    runner = build_annual_prefix_runner(
        plan, request=request, required=required, archive=archive,
        reference_edge=reference_edge, runner_factory=runner_factory,
        controller_factory=controller_factory,
    )
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=unit_id + ".", dir=staging_parent
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        result = None
        if predecessor_unit is not None:
            predecessor_hint = {
                key: predecessor_unit[key] for key in (
                    "request_id", "demand_build_key", "checkpoint_s",
                    "seed", "variant")
            }
            predecessor_context = annual_unit_context(
                plan, predecessor_unit_id, unit_hint=predecessor_hint,
                plan_context=indexed)
            predecessor = predecessor_context["unit"]
            if (
                predecessor["demand_build_key"] != unit["demand_build_key"]
                or predecessor["variant"] != unit["variant"]
                or predecessor["seed"] != unit["seed"]
                or predecessor["checkpoint_s"] >= unit["checkpoint_s"]
            ):
                raise ValueError("annual warm predecessor is not chain-compatible")
            store = AnnualWarmArtifactStore(root / "store")
            if not store.artifact_exists(predecessor_unit_id):
                raise RuntimeError(
                    "annual warm predecessor artifact is unavailable: "
                    + predecessor_unit_id)
            predecessor_root = workspace / "predecessor"
            predecessor_artifact, restored_predecessor = (
                store.restore_artifact_subset(
                    predecessor_unit_id, predecessor_root,
                    ("state.xml.gz", "prefix_evidence.json"),
                )
            )
            validate_unit_artifact(
                plan, predecessor_unit_id, predecessor_artifact,
                plan_context=indexed)
            predecessor_evidence = parse_prefix_evidence(
                json.loads(restored_predecessor[
                    "prefix_evidence.json"].read_text()),
                expected_warm_point_s=predecessor["checkpoint_s"],
            )
            result = runner.extend_warm_state(
                variant=unit["variant"], seed=unit["seed"],
                warm_point_s=unit["checkpoint_s"], workspace=workspace,
                previous_state_path=restored_predecessor["state.xml.gz"],
                previous_prefix_evidence=predecessor_evidence,
                schedule_id=request["request_id"],
            )
            if result is None:
                raise RuntimeError("production prefix extension declined")
        else:
            result = runner.bootstrap_warm_state(
                variant=unit["variant"],
                seed=unit["seed"],
                warm_point_s=unit["checkpoint_s"],
                workspace=workspace,
                schedule_id=request["request_id"],
            )
        if result is None:
            raise RuntimeError("production prefix bootstrap declined")
        evidence = parse_prefix_evidence(
            result["prefix_evidence"],
            expected_warm_point_s=unit["checkpoint_s"],
        )
        evidence_path = workspace / "prefix_evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        members = {
            "state.xml.gz": Path(result["state_path"]),
            "prefix_evidence.json": evidence_path,
            "route.rou.xml": archive / VARIANT_FILENAMES[unit["variant"]],
            "demand_meta.json": archive / "demand_meta.json",
            "demand_build_spec.json": archive / "demand_build_spec.json",
            "demand_manifest.json": archive / "manifest.json",
        }
        store = AnnualWarmArtifactStore(root / "store")
        artifact = store.pack_artifact(
            plan_content_key=plan["content_key"],
            unit_id=unit_id,
            members=members,
            metadata=metadata,
        )
        restored_root = workspace / "restored"
        _stored_manifest, restored = store.restore_artifact_subset(
            unit_id, restored_root,
            ("state.xml.gz", "prefix_evidence.json", "demand_build_spec.json"),
        )
        validate_restored_unit_payload(
            plan, unit_id, restored, unit_hint=unit_hint,
            plan_context=indexed,
        )
        return validate_unit_artifact(
            plan, unit_id, artifact, plan_context=indexed
        )


def _pool_initialize(
    plan_path: Path, root: Path, plan_key: str, reference_edge: str
) -> None:
    """Bind immutable execution inputs once per spawn-isolated worker."""
    global _POOL_PLAN, _POOL_PLAN_CONTEXT, _POOL_ROOT, _POOL_REFERENCE_EDGE
    global _POOL_RUNNER_KEY, _POOL_RUNNER
    plan = _current_plan(plan_path)
    _require_plan_key(plan, plan_key)
    _POOL_PLAN = plan
    _POOL_PLAN_CONTEXT = AnnualWarmPlanContext(plan)
    _POOL_ROOT = Path(root)
    _POOL_REFERENCE_EDGE = reference_edge
    _POOL_RUNNER_KEY = None
    _POOL_RUNNER = None


def _pool_worker(
    unit: Mapping[str, Any], archive: Path, archive_record: Mapping[str, Any]
) -> dict[str, Any]:
    if (_POOL_PLAN is None or _POOL_PLAN_CONTEXT is None
            or _POOL_ROOT is None or _POOL_REFERENCE_EDGE is None):
        raise RuntimeError("annual persistent worker was not initialized")
    hint = {
        key: unit[key] for key in (
            "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
        )
    }
    return _worker_artifact(
        _POOL_PLAN,
        root=_POOL_ROOT,
        unit_id=str(unit["unit_id"]),
        archive=Path(archive),
        reference_edge=_POOL_REFERENCE_EDGE,
        unit_hint=hint,
        predecessor_unit=unit.get("_predecessor_unit"),
        plan_context=_POOL_PLAN_CONTEXT,
        runner_factory=_pooled_runner_factory,
        validated_archive_record=archive_record,
    )


def execute_population(
    plan: Mapping[str, Any],
    *,
    plan_path: Path,
    root: Path,
    state_workers: int,
    max_units: int | None,
    reference_edge: str,
    demand_build_key: str | None = None,
    variant: str | None = None,
    keep_demand_archives: bool = False,
    worker_function=None,
) -> dict[str, Any]:
    if isinstance(state_workers, bool) or not isinstance(state_workers, int) \
            or state_workers < 1:
        raise ValueError("state_workers must be a positive integer")
    progress = _verify_initialized_root(plan, root)
    indexed = AnnualWarmPlanContext(plan)
    if variant is not None and variant not in VARIANT_FILENAMES:
        raise ValueError("annual population variant is unsupported")
    selected = progress.selectable(
        limit=None if variant is not None else max_units,
        demand_build_key=demand_build_key,
    )
    if variant is not None:
        selected = [unit for unit in selected if unit["variant"] == variant]
        if max_units is not None:
            selected = selected[:max_units]
    if (demand_build_key is not None or variant is not None) and not selected:
        raise ValueError(
            "no unfinished annual units match the requested pilot filters"
        )
    predecessors: dict[str, dict[str, Any] | None] = {}
    previous_by_chain: dict[tuple[str, int, str], dict[str, Any]] = {}
    for planned_unit in iter_work_units(plan):
        chain = (
            planned_unit["demand_build_key"],
            planned_unit["seed"],
            planned_unit["variant"],
        )
        predecessors[planned_unit["unit_id"]] = previous_by_chain.get(chain)
        previous_by_chain[chain] = dict(planned_unit)

    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for unit in selected:
        annotated = dict(unit)
        predecessor = predecessors[unit["unit_id"]]
        if predecessor is not None:
            annotated["_predecessor_unit"] = predecessor
        groups.setdefault(unit["demand_build_key"], []).append(annotated)
    demands = {
        item["build_key"]: DemandBuildSpec.from_dict(item)
        for item in plan["demand_build_specs"]
    }
    store = AnnualWarmArtifactStore(root / "store")
    if worker_function is None:
        executor_context = ProcessPoolExecutor(
            max_workers=state_workers,
            mp_context=get_context("spawn"),
            initializer=_pool_initialize,
            initargs=(plan_path, root, plan["content_key"], reference_edge),
        )
    else:
        executor_context = ThreadPoolExecutor(max_workers=state_workers)
    with executor_context as executor:
        for demand_key, units in groups.items():
            # The job may run for days. Refuse to cross a source/input edit and
            # silently build a mixed bank; already-running workers retain the
            # prior imported code until this group ends, then this seal aborts.
            _verify_plan_source_seal(plan)
            remaining = []
            for unit in units:
                if not store.artifact_exists(unit["unit_id"]):
                    remaining.append(unit)
                    continue
                artifact = store.load_artifact(unit["unit_id"])
                validate_unit_artifact(
                    plan, unit["unit_id"], artifact, plan_context=indexed
                )
                with tempfile.TemporaryDirectory(
                    prefix=unit["unit_id"] + ".orphan-check.",
                    dir=root / ".staging",
                ) as raw_restore:
                    restored = store.restore_artifact(
                        unit["unit_id"], Path(raw_restore)
                    )
                    validate_restored_unit_payload(
                        plan, unit["unit_id"], restored,
                        unit_hint={
                            key: unit[key] for key in (
                                "request_id", "demand_build_key", "checkpoint_s",
                                "seed", "variant",
                            )
                        },
                        plan_context=indexed,
                    )
                if unit["state"] != "running":
                    progress.mark_running(unit["unit_id"])
                progress.mark_succeeded(unit["unit_id"], artifact["content_key"])
            if not remaining:
                continue
            _runtime_disk_guard(root)
            archive_record = _resolve_demand_archive(demands[demand_key])
            archive = Path(archive_record["archive"])
            while remaining:
                # Never submit a unit until its exact predecessor is durable.
                # Fixed-size slicing is unsafe after a partial failure: once
                # successful siblings disappear from the selectable rows, two
                # dependent checkpoints from one chain can land in one batch.
                ready = []
                for unit in remaining:
                    predecessor = unit.get("_predecessor_unit")
                    if predecessor is None or store.artifact_exists(
                        predecessor["unit_id"]
                    ):
                        ready.append(unit)
                        if len(ready) == state_workers:
                            break
                if not ready:
                    blocked = ", ".join(
                        str(unit["unit_id"]) for unit in remaining[:state_workers]
                    )
                    raise RuntimeError(
                        "annual population has no dependency-ready units: "
                        + blocked
                    )
                batch = ready
                _runtime_disk_guard(root)
                started = progress.mark_running_many(
                    [unit["unit_id"] for unit in batch]
                )
                failures = []
                retryable = []
                succeeded = {}
                failed = {}
                if worker_function is None:
                    futures = {
                        unit["unit_id"]: executor.submit(
                            _pool_worker, unit, archive, archive_record
                        )
                        for unit in batch
                    }
                else:
                    futures = {
                        unit["unit_id"]: executor.submit(
                            worker_function, unit, archive
                        )
                        for unit in batch
                    }
                for unit in batch:
                    unit_id = unit["unit_id"]
                    try:
                        worker_artifact = futures[unit_id].result()
                        artifact = store.load_artifact_manifest(unit_id)
                        if worker_artifact != artifact:
                            raise ValueError(
                                "worker artifact differs from published manifest"
                            )
                        validate_unit_artifact(
                            plan, unit_id, artifact, plan_context=indexed
                        )
                        succeeded[unit_id] = artifact["content_key"]
                    except Exception as error:
                        message = f"{type(error).__name__}: {error}"[:1000]
                        failed[unit_id] = message
                        attempts = int(started[unit_id]["attempts"])
                        if _is_transient_launch_failure(error) \
                                and attempts < TRANSIENT_UNIT_RETRY_LIMIT:
                            retryable.append(unit_id)
                            print(
                                f"  transient start failure, retrying "
                                f"{unit_id} (attempt {attempts} of "
                                f"{TRANSIENT_UNIT_RETRY_LIMIT}): {message}",
                                flush=True,
                            )
                        else:
                            failures.append(unit_id)
                progress.finish_batch(succeeded=succeeded, failed=failed)
                if failures:
                    raise RuntimeError(
                        "annual population stopped after failed batch: "
                        + ", ".join(failures)
                    )
                # A retried unit stays selectable, so it must NOT be removed
                # from `remaining`; the dependency scan re-admits it next pass.
                completed = {
                    unit["unit_id"] for unit in batch
                } - set(retryable)
                remaining = [
                    unit for unit in remaining
                    if unit["unit_id"] not in completed
                ]
            if not keep_demand_archives:
                _prune_demand_archive(archive, demand_key, progress)
    return progress.summary()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan_path = args.plan if args.plan.is_absolute() else ROOT / args.plan
    plan = _current_plan(args.plan)
    root = _root_for(plan, args.root)

    if args.max_units is not None and args.max_units < 1:
        raise SystemExit("--max-units must be positive")
    if args.preflight:
        print(json.dumps(
            production_preflight(
                plan, root, state_workers=args.state_workers,
                keep_demand_archives=args.keep_demand_archives,
            ),
            sort_keys=True,
        ))
        return 0
    if args.status:
        progress = _verify_initialized_root(plan, root)
        print(json.dumps(progress.summary(), sort_keys=True))
        return 0

    _require_plan_key(plan, args.plan_key)
    preflight = production_preflight(
        plan, root, state_workers=args.state_workers,
        keep_demand_archives=args.keep_demand_archives,
    )
    if args.initialize:
        summary = initialize_root(plan, root)
        print(json.dumps({
            **summary,
            "root": str(root),
            "preflight": preflight,
        }, sort_keys=True))
        return 0

    if not root.is_dir():
        raise SystemExit("annual root is not initialized; run --initialize first")
    lock_path = root / ".population.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise SystemExit("annual population lock is not a regular file")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("another annual population process owns the root") from exc
        summary = execute_population(
            plan,
            plan_path=plan_path,
            root=root,
            state_workers=args.state_workers,
            max_units=args.max_units,
            reference_edge=preflight["reference_edge"],
            demand_build_key=args.demand_build_key,
            variant=args.variant,
            keep_demand_archives=args.keep_demand_archives,
        )
    print(json.dumps({**summary, "root": str(root)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
