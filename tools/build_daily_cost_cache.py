#!/usr/bin/env python3
"""Build the independent daily-cost cache for a frozen month case.

One build key at a time, resumable, and never through the WindowCostIndex.
Each daily unit is priced by the production per-file path
(``ArchiveDisruptionProvider.disruption`` with a ``DailyCostCache``), which
parses the archive's route files itself; nothing from an index is read, so
the result stays an independent oracle for that index.

A build key is a batch:

1. the registration, the archive's four files and the catalog bindings are
   re-proved from content;
2. every daily unit of that build key is computed;
3. every stored record is reloaded and compared with what was computed;
4. only then is the batch marker published atomically and no-clobber. A build
   key counts as complete only when its marker verifies, so an interrupted
   batch is a miss and is recomputed.

Unit records are content-addressed, so recomputing an interrupted batch
rewrites identical bytes. One flock per build key keeps two processes from
publishing the same batch. There is no global cache and no trust in a path,
a size or an mtime: every reuse is content-bound.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
from traffic_sim.core.closure_calendar import (  # noqa: E402
    iter_closure_schedules,
)
from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402
from traffic_sim.simulation.deterministic_disruption import (  # noqa: E402
    ArchiveDisruptionProvider,
    DailyCostCache,
    NetworkCostModel,
    costing_source_identity,
)
from traffic_sim.simulation.independent_daily import (  # noqa: E402
    daily_unit_records,
)
from traffic_sim.simulation.monthly_demand import (  # noqa: E402
    MonthlyDemandResolverRunner,
)
from traffic_sim.simulation.window_cost_index import (  # noqa: E402
    publish_new_file,
)

BATCH_SCHEMA = "daily_cost_cache_batch_v1"
STATUS_SCHEMA = "daily_cost_cache_status_v1"
TOOL_SOURCES = ("tools/build_daily_cost_cache.py",)
PRODUCTION_CACHE_MARKER = "profile-daily-cost-cache"


def _digest(payload: Any) -> str:
    from tools.closure_effect_eligibility import _digest as digest

    return digest(payload)


def _body(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items()
            if key != "content_key"}


def _usage() -> Dict[str, float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"user_s": usage.ru_utime, "sys_s": usage.ru_stime,
            "max_rss_bytes": int(usage.ru_maxrss)}


def _swap_used_bytes() -> Optional[int]:
    try:
        text = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                              capture_output=True, text=True,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"used\s*=\s*([0-9.]+)M", text)
    return int(float(match.group(1)) * 1024 * 1024) if match else None


def _footprint_bytes() -> Optional[int]:
    """This process' lifetime peak physical footprint, when macOS offers it."""
    benchmarks = str(ROOT / "validation" / "benchmarks")
    if benchmarks not in sys.path:
        sys.path.append(benchmarks)
    try:
        import wci_diag_common as common  # noqa: PLC0415

        return int(common.process_memory()[
            "lifetime_max_phys_footprint_bytes"])
    except (ImportError, OSError, RuntimeError):
        return None


@dataclasses.dataclass(frozen=True)
class BatchIdentity:
    """Everything a stored batch is bound to; any change invalidates it."""

    registration_content_key: str
    policy: str
    chosen_edge: str
    spec_content_key: str
    build_key: str
    archive: str
    archive_content_key: str
    archive_files: Mapping[str, str]
    catalogs: Mapping[str, Any]
    costing_sources: Mapping[str, str]
    tool_sources: Mapping[str, str]
    #: The network prices every unit and enters the cache key through the
    #: provider identity. A batch bound without it stays "complete" after a
    #: network change while every reader misses it, so it is bound here too.
    network: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(dataclasses.asdict(self), default=str))


def identity_of(registration: Mapping[str, Any], build_key: str, *,
                network: Any = None) -> BatchIdentity:
    from tools import freeze_wci_month_case as month

    archive = registration["archives"][build_key]
    model = network if network is not None else NetworkCostModel()
    return BatchIdentity(
        network=dict(model.identity()),
        registration_content_key=registration["content_key"],
        policy=registration["case_selection_policy"],
        chosen_edge=registration["chosen_edge"],
        spec_content_key=registration["spec_content_key"],
        build_key=build_key,
        archive=archive["archive"],
        archive_content_key=archive["archive_content_key"],
        archive_files=month.drift_archives(registration)[build_key]["files"],
        catalogs={pool: {"catalog_key": item["catalog_key"],
                         "routes_sha256": item["routes_sha256"],
                         "metadata_sha256": item["metadata_sha256"],
                         "path": item["path"]}
                  for pool, item in registration["catalogs"].items()},
        costing_sources=costing_source_identity(),
        tool_sources={name: sha256_file(ROOT / name)
                      for name in TOOL_SOURCES})


def content_drift(identity: BatchIdentity, *, network: Any = None
                  ) -> List[str]:
    """Re-prove from bytes what this batch is bound to."""
    problems = []
    model = network if network is not None else NetworkCostModel()
    if dict(model.identity()) != dict(identity.network):
        problems.append("network changed")
    archive = Path(identity.archive)
    for name, digest in sorted(identity.archive_files.items()):
        if sha256_file(archive / name) != digest:
            problems.append(f"archive file changed: {name}")
    for pool, item in sorted(identity.catalogs.items()):
        routes = Path(item["path"])
        if sha256_file(routes) != item["routes_sha256"]:
            problems.append(f"catalog changed: {pool}")
        if sha256_file(routes.with_name("catalog.meta.json")) != item[
                "metadata_sha256"]:
            problems.append(f"catalog metadata changed: {pool}")
    if dict(costing_source_identity()) != dict(identity.costing_sources):
        problems.append("costing source changed")
    if {name: sha256_file(ROOT / name) for name in TOOL_SOURCES} != dict(
            identity.tool_sources):
        problems.append("cache builder source changed")
    return problems


def units_of(spec: ClosureSearchSpec, runs_root: Path,
             qualified_manifest: Mapping[str, Any]) -> Dict[str, List[Any]]:
    """Every daily unit of the month, grouped by demand build key."""
    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=Path(runs_root), build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="daily-cost-cache-builder",
        qualified_demand_manifest=qualified_manifest)
    units: Dict[str, List[Any]] = {}
    seen = set()
    for parent in iter_closure_schedules(spec):
        for unit_id, identity, build in daily_unit_records(spec, parent):
            unit_id = str(unit_id)
            if unit_id in seen:
                continue
            seen.add(unit_id)
            schedule = build()
            units.setdefault(resolver._required(schedule).build_key,
                             []).append((unit_id, identity, schedule))
    return units


def marker_path(cache_root: Path, build_key: str) -> Path:
    return Path(cache_root) / "batches" / f"{build_key}.json"


def verify_batch(cache_root: Path, identity: BatchIdentity,
                 units: Sequence[Any], *, network: Any = None) -> List[str]:
    """Problems that make a stored batch unusable; empty means reuse it."""
    path = marker_path(cache_root, identity.build_key)
    if not path.is_file():
        return ["no batch marker"]
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"unreadable batch marker: {error}"]
    if marker.get("schema") != BATCH_SCHEMA:
        return ["unexpected batch schema"]
    if _digest(_body(marker)) != marker.get("content_key"):
        return ["batch marker content key does not describe it"]
    if marker.get("identity") != identity.to_dict():
        return ["batch identity changed"]
    problems = content_drift(identity, network=network)
    if problems:
        return problems
    stored = {item["unit_id"]: item for item in marker["units"]}
    if sorted(stored) != sorted(str(unit) for unit, _identity, _s in units):
        return ["batch covers other daily units"]
    cache = DailyCostCache(Path(cache_root))
    for unit_id, item in sorted(stored.items()):
        record = cache.path_for(item["cache_key"])
        if not record.is_file():
            return [f"missing cached unit: {unit_id}"]
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return [f"unreadable cached unit {unit_id}: {error}"]
        if (payload.get("key") != item["cache_key"]
                or _digest(payload.get("disruption")) != item["digest"]):
            return [f"cached unit does not match the batch: {unit_id}"]
    return []


def _compute_batch(spec, identity: BatchIdentity, units, cache_root: Path,
                   network, on_unit=None) -> Dict[str, Any]:
    """Price every unit of one build key through the per-file oracle."""
    cache = DailyCostCache(Path(cache_root))
    provider = ArchiveDisruptionProvider(
        spec, archive=Path(identity.archive), network=network, cache=cache)
    computed = {}
    for unit_id, _unit_identity, schedule in units:
        records = provider.disruption(schedule)
        computed[str(unit_id)] = {
            "unit_id": str(unit_id),
            "schedule_id": schedule.schedule_id,
            "cache_key": cache.key(provider.cache_identity(schedule)),
            "digest": _digest([dict(item) for item in records]),
        }
        if on_unit is not None:
            on_unit(str(unit_id), len(computed))
    # Reload every record through a fresh cache and compare, so a batch is
    # published only when what is on disk is what was computed.
    fresh = DailyCostCache(Path(cache_root))
    mismatches = []
    for unit_id, _unit_identity, schedule in units:
        stored = fresh.load(provider.cache_identity(schedule))
        if stored is None or _digest([dict(item) for item in stored]) != \
                computed[str(unit_id)]["digest"]:
            mismatches.append(str(unit_id))
    provider_identity = dict(provider.identity())
    del provider
    return {"units": [computed[key] for key in sorted(computed)],
            "mismatches": mismatches,
            "provider_identity_digest": _digest(provider_identity)}


def build_batch(registration: Mapping[str, Any], build_key: str, *,
                cache_root: Path, units: Sequence[Any], network=None,
                on_unit=None) -> Dict[str, Any]:
    """Compute, verify and publish one build key's batch."""
    model = network if network is not None else NetworkCostModel()
    identity = identity_of(registration, build_key, network=model)
    drift = content_drift(identity, network=model)
    if drift:
        raise SystemExit(f"build key {build_key}: {drift}")
    spec = ClosureSearchSpec.from_dict(registration["spec"])
    started = time.perf_counter()
    before = _usage()
    result = _compute_batch(spec, identity, units, cache_root, model, on_unit)
    if result["mismatches"]:
        raise SystemExit(f"build key {build_key}: stored units do not match "
                         f"the computed ones: {result['mismatches']}")
    # The route files are parsed once and the vehicles reused for every unit,
    # so nothing re-reads those bytes during the batch. Re-prove them here:
    # a batch whose inputs moved under it must never reach publication.
    drift = content_drift(identity, network=model)
    if drift:
        raise SystemExit(f"build key {build_key}: inputs changed while it "
                         f"was being built: {drift}")
    after = _usage()
    marker = {
        "schema": BATCH_SCHEMA,
        "release_evidence": False,
        "build_key": build_key,
        "identity": identity.to_dict(),
        "units": result["units"],
        "daily_units": len(result["units"]),
        "provider_identity_digest": result["provider_identity_digest"],
        "timing": {"wall_s": time.perf_counter() - started,
                   "user_s": after["user_s"] - before["user_s"],
                   "sys_s": after["sys_s"] - before["sys_s"]},
        "peak": {"max_rss_bytes": after["max_rss_bytes"],
                 "lifetime_max_phys_footprint_bytes": _footprint_bytes()},
    }
    marker["content_key"] = _digest(_body(marker))
    publish_new_file(marker_path(cache_root, build_key),
                     (json.dumps(marker, indent=1, sort_keys=True)
                      + "\n").encode("utf-8"),
                     label=f"daily-cost batch {build_key}")
    return marker


class BatchLock:
    """One exclusive publisher per build key, enforced by the kernel."""

    def __init__(self, cache_root: Path, build_key: str) -> None:
        self.path = Path(cache_root) / "locks" / f"{build_key}.lock"
        self._handle = None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # pylint: disable=consider-using-with
        self._handle = open(self.path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._handle.close()
            self._handle = None
            return False
        self._handle.write(f"{os.getpid()}\n")
        self._handle.flush()
        return True

    def __exit__(self, *_exception) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class Status:
    """Atomic run status; the last line of a run that stops for any reason."""

    def __init__(self, path: Path, *, requested: Sequence[str],
                 cache_root: Path, registration: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.started = time.perf_counter()
        self.state: Dict[str, Any] = {
            "schema": STATUS_SCHEMA, "state": "starting", "sequence": 0,
            "cache_root": str(cache_root),
            "registration_content_key": registration["content_key"],
            "chosen_edge": registration["chosen_edge"],
            "policy": registration["case_selection_policy"],
            "requested_build_keys": list(requested),
            "completed_build_keys": [], "failed_build_keys": [],
            "reused_build_keys": [], "current_build_key": None,
            "phase": None, "cache_hits": 0, "cache_misses": 0,
            "daily_units_done": 0, "daily_units_expected": 0,
            "recompute_reasons": {},
            "wall_s": 0.0, "user_s": 0.0, "sys_s": 0.0,
            "peak": {"max_rss_bytes": 0,
                     "lifetime_max_phys_footprint_bytes": None},
            "swap": {"before_bytes": _swap_used_bytes(), "now_bytes": None,
                     "growth_bytes": None},
            "identity": None, "output_content_keys": {},
            "stop_reason": None, "last_heartbeat_s": 0.0,
        }
        self.write()

    def update(self, **fields: Any) -> None:
        self.state.update(fields)
        self.write()

    def write(self) -> None:
        usage = _usage()
        swap_now = _swap_used_bytes()
        before = self.state["swap"]["before_bytes"]
        elapsed = round(time.perf_counter() - self.started, 3)
        self.state.update(sequence=self.state["sequence"] + 1,
                          wall_s=elapsed, user_s=usage["user_s"],
                          sys_s=usage["sys_s"], last_heartbeat_s=elapsed)
        self.state["peak"] = {
            "max_rss_bytes": max(self.state["peak"]["max_rss_bytes"],
                                 usage["max_rss_bytes"]),
            "lifetime_max_phys_footprint_bytes": _footprint_bytes()}
        self.state["swap"].update(
            now_bytes=swap_now,
            growth_bytes=(None if swap_now is None or before is None
                          else swap_now - before))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=1, sort_keys=True,
                      default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


def run(registration: Mapping[str, Any], *, build_keys: Sequence[str],
        cache_root: Path, runs_root: Path, qualified_manifest,
        status: Status, network=None) -> Dict[str, Any]:
    """Build every requested build key, reusing only what verifies."""
    spec = ClosureSearchSpec.from_dict(registration["spec"])
    grouped = units_of(spec, runs_root, qualified_manifest)
    # One network for the whole run: it is 16 MB of XML, and rebuilding it
    # per build key would cost more than the batches it guards.
    model = network if network is not None else NetworkCostModel()
    status.update(state="running", daily_units_expected=sum(
        len(grouped[key]) for key in build_keys))
    results: Dict[str, Any] = {}
    for build_key in build_keys:
        units = grouped[build_key]
        identity = identity_of(registration, build_key, network=model)
        status.update(current_build_key=build_key, phase="verify_existing",
                      identity=identity.to_dict())
        problems = verify_batch(cache_root, identity, units, network=model)
        if not problems:
            status.state["reused_build_keys"].append(build_key)
            status.update(cache_hits=status.state["cache_hits"] + len(units),
                          daily_units_done=(status.state["daily_units_done"]
                                            + len(units)),
                          phase="reused")
            results[build_key] = {"reused": True, "daily_units": len(units)}
            continue
        status.state["recompute_reasons"][build_key] = problems
        status.update(phase="locking",
                      cache_misses=status.state["cache_misses"] + len(units))
        with BatchLock(cache_root, build_key) as acquired:
            if not acquired:
                status.state["failed_build_keys"].append(build_key)
                status.update(phase="locked_by_another_process")
                results[build_key] = {"reused": False, "busy": True}
                continue
            status.update(phase="computing")
            total = len(units)

            def progress(_unit_id, done, total=total):
                status.update(phase=f"computing {done}/{total}",
                              daily_units_done=(
                                  status.state["daily_units_done"] + 1))

            marker = build_batch(registration, build_key,
                                 cache_root=cache_root, units=units,
                                 network=model, on_unit=progress)
        status.state["completed_build_keys"].append(build_key)
        status.state["output_content_keys"][build_key] = marker["content_key"]
        status.update(phase="published")
        results[build_key] = {"reused": False, "daily_units": len(units),
                              "marker": marker}
    status.update(state="done", current_build_key=None, phase=None)
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--build-key", action="append", required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--allow-production-cache", action="store_true")
    args = parser.parse_args(argv)

    from tools import build_window_cost_index as builder
    from tools import freeze_wci_month_case as month

    if (PRODUCTION_CACHE_MARKER in str(args.cache_root)
            and not args.allow_production_cache):
        raise SystemExit("refusing to write to the production daily-cost "
                         "cache without --allow-production-cache")
    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    problems = month.verify_registration(registration)
    if problems:
        raise SystemExit("the month registration does not verify: "
                         f"{problems}")
    unknown = [key for key in args.build_key
               if key not in registration["archives"]]
    if unknown:
        raise SystemExit(f"unknown build keys: {unknown}")
    bound = builder._bound_inputs(args.profile)
    status = Status(args.status, requested=args.build_key,
                    cache_root=args.cache_root, registration=registration)
    try:
        results = run(registration, build_keys=args.build_key,
                      cache_root=args.cache_root,
                      runs_root=bound["runs_root"],
                      qualified_manifest=bound["qualified_manifest"],
                      status=status)
    except BaseException as error:
        status.update(state="failed",
                      stop_reason=f"{type(error).__name__}: {error}")
        raise
    print(json.dumps({
        "cache_root": str(args.cache_root),
        "build_keys": args.build_key,
        "reused": [key for key, item in results.items()
                   if item.get("reused")],
        "built": [key for key, item in results.items()
                  if not item.get("reused") and not item.get("busy")],
        "daily_units": {key: item.get("daily_units")
                        for key, item in results.items()},
        "content_keys": status.state["output_content_keys"],
        "status": str(args.status),
        "peak": status.state["peak"],
        "wall_s": status.state["wall_s"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
