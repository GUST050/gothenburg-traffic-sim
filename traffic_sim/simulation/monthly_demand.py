"""Resolve and freeze one calibrated demand archive per monthly envelope.

The proxy shortlist can contain schedules on different dates.  Their SUMO
times are local to different calibrated route files, so one archive cannot
honestly serve the whole month.  This module groups schedules by the exact
``DemandBuildSpec`` derived from their simulation envelope, resolves or builds
each missing archive once, freezes the mapping in an immutable release
manifest, and routes every candidate to its matched SUMO backend.
"""

from __future__ import annotations

import dataclasses
from functools import lru_cache
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DemandBuildSpec,
    write_demand_build_spec,
)
from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.demand.build_lock import child_environment, demand_build_lock
from traffic_sim.demand.source_identity import demand_source_fingerprints
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
    envelope_demand_spec,
    independent_daily_demand_spec,
)
from traffic_sim.simulation.finalist_decision import CandidateEvidence
from traffic_sim.simulation.monthly_sumo import (
    DEFAULT_BASELINE_CACHE,
    ArchivedDemandSumoRunner,
)
from traffic_sim.demand.route_support import route_edges
from traffic_sim.simulation.runtime import sumo_home


SCHEMA_VERSION = 1
DEFAULT_RUNS_ROOT = Path("runs")
DEFAULT_RELEASE_ROOT = DEFAULT_RUNS_ROOT / "monthly-demand-releases"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_ARCHIVE_FILES = (
    "demand_meta.json",
    "demand_build_spec.json",
    "candidates.rou.xml",
    "candidates.meta.json",
    "calibrated.rou.xml",
    "calibrated.agents.json",
    "calibrated_v1.rou.xml",
    "calibrated_v1.agents.json",
    "calibrated_v2.rou.xml",
    "calibrated_v2.agents.json",
)
# The tracked demand builder writes THROUGH the live release paths (sumo/
# demand products, web/data OD export) even when it is only materializing a
# closure-envelope archive.  A monthly search must never change what the
# deployed site simulates (2026-07-19: two smoke envelope builds silently
# left the live site calibrated for 2027-07-22), so every path a consumer
# reads at scenario time is snapshotted before the first missing-envelope
# build and restored byte-for-byte afterwards, on success and on failure.
# Diagnostic intermediates (candidate pools, fit reports) are deliberately
# not restored: nothing reads them at runtime and the next tracked build
# rewrites them; the runtime contract below is what defines the release.
LIVE_DEMAND_RELEASE_PRODUCTS = (
    Path("sumo") / "demand_meta.json",
    Path("sumo") / "demand_build_spec.json",
    Path("sumo") / "calibrated.rou.xml",
    Path("sumo") / "calibrated_v1.rou.xml",
    Path("sumo") / "calibrated_v2.rou.xml",
    Path("sumo") / "calibrated.agents.json",
    Path("sumo") / "calibrated_v1.agents.json",
    Path("sumo") / "calibrated_v2.agents.json",
    Path("web") / "data" / "od_matrix.json",
    Path("web") / "data" / "od_matrix.csv",
    # Found live during the held-out v2 campaign (2026-07-20): the demand
    # builder also refreshes the assembled validation report for ITS OWN
    # build, leaving the live site's shield panel describing the wrong
    # demand window.
    Path("web") / "data" / "validation.json",
    # The candidate pool was originally excluded as a "diagnostic
    # intermediate nothing reads at runtime" — wrong: the validation
    # report hashes it to prove the frozen temporal-holdout evidence still
    # belongs to the live release.  Leaving a later envelope build's pool
    # behind makes that check report a FALSE stale, and the live pool is
    # not recoverable from any archive (found 2026-07-20, after it had
    # already happened once).
    Path("sumo") / "candidates.rou.xml",
    Path("sumo") / "candidates.meta.json",
)
# Whole DIRECTORIES the builder rewrites wholesale. A demand build clears
# stale scenarios, so an envelope build deletes the deployed site's scenario
# artifacts outright - found 2026-07-21 during the day-library proof, which
# removed four tracked scenario files and had to restore them from git. File
# entries cannot express that: what has to come back is the directory's exact
# contents, including the absence of anything the build added.
LIVE_DEMAND_RELEASE_DIRECTORIES = (
    Path("web") / "data" / "scenarios",
)
# Written beside the snapshot so a run that is KILLED (SIGKILL, a serve.py
# timeout, a crash) still leaves a recoverable pointer. Without it the
# restore lives only in a finally block, which a kill skips - exactly what
# happened when the 40h search was cancelled mid-build.
LIVE_RELEASE_SNAPSHOT_MARKER = Path("runs") / ".live-demand-release-snapshot.json"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _canonical_digest(payload: Any, *, length: int = 64) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_outputs(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = manifest.get("outputs", ())
    if not isinstance(values, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if isinstance(value, Mapping) and isinstance(value.get("name"), str):
            result[str(value["name"])] = value
    return result


@lru_cache(maxsize=1)
def _current_demand_runtime() -> tuple[str, str | None]:
    """Runtime fields recorded by ``make_fingerprint`` for reuse validation."""
    return sys.version.split()[0], sumo_version(sumo_home())
GENERATION_SOURCE_FILES = ("build_candidates", "pfe", "build_sumo_demand")


def demand_generation_of(archive: Path) -> dict[str, str]:
    """The source hashes of the code that produced one demand archive.

    A demand archive is content-addressed by its CONTRACT (dates, source,
    window), which is deliberately silent about the code that filled it. That
    is what lets a rebuild reuse an archive — and also what would let one
    search mix an envelope calibrated before a generator change with one
    calibrated after it, since every envelope build is a fresh subprocess
    importing whatever code is on disk at the time.

    Stage B changes the candidate seeding, so this is no longer hypothetical:
    a `prepare` in flight across its landing could straddle it. Reading the
    generator hashes the archive already records makes that detectable.
    """
    metadata = _read(Path(archive) / "demand_meta.json")
    source_files = (metadata.get("build_fingerprint") or {}).get(
        "source_files") or {}
    return {
        name: str((source_files.get(name) or {}).get("sha256", ""))
        for name in GENERATION_SOURCE_FILES
    }


def _require_one_demand_generation(
    entries_by_key: Mapping[str, Mapping[str, Any]],
) -> None:
    """Refuse a release whose archives were built by different generators.

    Fail closed and name the disagreement: comparing envelopes calibrated by
    two different candidate generators is not a paired comparison, and the
    difference would otherwise be invisible in every downstream artifact.
    """
    generations: dict[str, list[str]] = {}
    for key, entry in sorted(entries_by_key.items()):
        archive = Path(str(entry.get("archive", "")))
        try:
            generation = demand_generation_of(archive)
        except (OSError, ValueError):
            raise ValueError(
                f"monthly demand archive has no readable build fingerprint: "
                f"{archive}"
            ) from None
        if not all(generation.values()):
            raise ValueError(
                f"monthly demand archive does not record its generator "
                f"source hashes: {archive}"
            )
        generations.setdefault(_canonical_digest(generation, length=16),
                               []).append(key)
    if len(generations) > 1:
        groups = "; ".join(
            f"{digest}: {', '.join(keys)}"
            for digest, keys in sorted(generations.items())
        )
        raise ValueError(
            "monthly demand release mixes archives built by different "
            f"candidate/solver generations ({groups}). Rebuild the older "
            "envelopes so every candidate is compared against demand from "
            "one generation."
        )


def validate_demand_archive(
    archive: Path,
    required: DemandBuildSpec,
) -> dict[str, Any]:
    """Validate one immutable run archive against an exact envelope contract."""
    archive = Path(archive).resolve()
    manifest = _read(archive / "manifest.json")
    if manifest.get("kind") != "demand" or manifest.get("status") != "succeeded":
        raise ValueError(f"demand archive is not a succeeded demand run: {archive}")

    archived_spec = DemandBuildSpec.from_dict(
        _read(archive / "demand_build_spec.json")
    )
    metadata = _read(archive / "demand_meta.json")
    build_fingerprint = metadata.get("build_fingerprint")
    if not isinstance(build_fingerprint, Mapping) \
            or build_fingerprint.get("schema_version") != 1:
        raise ValueError(f"demand archive lacks a build fingerprint: {archive}")
    if build_fingerprint.get("source_files") != demand_source_fingerprints(
            _PROJECT_ROOT):
        raise ValueError(
            f"demand archive was produced by different source code: {archive}")
    current_python, current_sumo = _current_demand_runtime()
    if build_fingerprint.get("python") != current_python \
            or build_fingerprint.get("sumo_version") != current_sumo:
        raise ValueError(
            f"demand archive was produced by a different runtime: {archive}")
    metadata_spec = DemandBuildSpec.from_dict(metadata.get("demand_spec", {}))
    if archived_spec != required or metadata_spec != required:
        raise ValueError(f"demand archive has another build contract: {archive}")
    if metadata.get("demand_build_key") != required.build_key:
        raise ValueError(f"demand archive build key is inconsistent: {archive}")
    if str(metadata.get("epoch_sim")) != f"{required.start_date}T00:00:00":
        raise ValueError(f"demand archive epoch does not match envelope: {archive}")
    if int(metadata.get("n_intervals", -1)) != required.days * 96:
        raise ValueError(f"demand archive duration does not match envelope: {archive}")
    if int(metadata.get("n_variants", 0)) != 3:
        raise ValueError(f"demand archive lacks q10/q50/q90 variants: {archive}")

    outputs = _manifest_outputs(manifest)
    records = []
    for name in _REQUIRED_ARCHIVE_FILES:
        path = archive / name
        expected = outputs.get(name)
        digest = sha256_file(path)
        if (
            digest is None
            or expected is None
            or digest != expected.get("sha256")
            or path.stat().st_size != int(expected.get("bytes", -1))
        ):
            raise ValueError(
                f"demand archive output is missing or has changed: {path}"
            )
        records.append({
            "name": name,
            "bytes": path.stat().st_size,
            "sha256": digest,
        })
    provenance = metadata.get("candidate_provenance")
    if not isinstance(provenance, Mapping) \
            or provenance.get("schema_version") != 1 \
            or provenance.get("status") != "pass":
        raise ValueError(
            f"demand archive lacks passing candidate provenance: {archive}")
    augmentation = metadata.get("edge_support_augmentation")
    augmentation_variants = (
        augmentation.get("variants")
        if isinstance(augmentation, Mapping) else None
    )
    if not isinstance(augmentation, Mapping) \
            or augmentation.get("schema_version") != 1 \
            or not isinstance(augmentation_variants, Mapping):
        raise ValueError(
            f"demand archive lacks an edge-support record: {archive}")
    candidate_edges = route_edges(archive / "candidates.rou.xml")
    if not candidate_edges:
        raise ValueError(f"demand archive candidate pool has no edges: {archive}")
    variant_routes = {
        "edge_shares": archive / "calibrated.rou.xml",
        "edge_shares_q10": archive / "calibrated_v1.rou.xml",
        "edge_shares_q90": archive / "calibrated_v2.rou.xml",
    }
    support_status = augmentation.get("status")
    if support_status == "pass":
        # Pre-baseline-rule archives, where synthetic support vehicles were
        # added until the calibrated routes covered every pool edge. Still
        # accepted exactly as before, so old archives keep validating.
        if set(augmentation_variants) != set(variant_routes):
            raise ValueError(
                f"demand archive lacks passing full-edge support: {archive}")
        for label, support_report in augmentation_variants.items():
            if not isinstance(support_report, Mapping) \
                    or support_report.get("status") != "pass" \
                    or int(support_report.get("required_edges", 0)) \
                    != len(candidate_edges) \
                    or route_edges(variant_routes[label]) != candidate_edges:
                raise ValueError(
                    f"demand archive has invalid full-edge support for {label}: "
                    f"{archive}")
    elif support_status == "disabled_baseline_rule":
        # BASELINE RULE (2026-08-05, CLAUDE.md): "only what is measured is
        # simulated". The augmentation this branch replaces existed to force
        # calibrated routes to cover EVERY pool edge, and it did so with
        # vehicles built with forbidden_edges=measured -- traffic that by
        # construction can never cross a sensor. The rule deleted it, and
        # roughly half the inner city legitimately carries zero baseline flow
        # as a result.
        #
        # So the old equality here is not merely unmet, it is UNSATISFIABLE by
        # design: demanding it would require re-inventing exactly the synthetic
        # traffic the rule forbids. Found 2026-08-06 when the annual warming,
        # having cleared two earlier faults, failed on this one -- no archive
        # built under the current rule can ever validate.
        #
        # This is a contract UPDATE, not a relaxed gate: the direction of the
        # binding is simply reversed. Coverage (every pool edge is driven)
        # cannot hold; CONTAINMENT (no calibrated route uses an edge the pool
        # never offered) still must, and it is the half that actually protects
        # the archive -- it is what would catch routes fabricated outside the
        # published candidate pool.
        if augmentation_variants:
            raise ValueError(
                "demand archive disabled edge support but still reports "
                f"variants: {archive}")
        # A DAY-ASSEMBLED window (stage B) draws a separate candidate pool per
        # calendar day and the archive keeps only the LAST one, so containment
        # against it would fail for every earlier day's routes -- measured: 52
        # such edges on the first annual envelope. That is the same
        # namespace mistake the per-day provenance fix removed, one level up,
        # and testing it here would be just as wrong.
        #
        # Nothing is lost by not testing it: the per-day provenance proofs
        # already bind every published vehicle to a real candidate in the pool
        # that actually produced it, which is strictly STRONGER than
        # containment (it names the candidate, not merely the edge set). This
        # branch therefore checks what the archive alone can still witness.
        if provenance.get("mode") == "assembled_day_library":
            for label, route_path in sorted(variant_routes.items()):
                if not route_edges(route_path):
                    raise ValueError(
                        f"demand archive variant {label} has no routes: "
                        f"{archive}")
        else:
            for label, route_path in sorted(variant_routes.items()):
                used = route_edges(route_path)
                if not used:
                    raise ValueError(
                        f"demand archive variant {label} has no routes: "
                        f"{archive}")
                outside = used - candidate_edges
                if outside:
                    raise ValueError(
                        f"demand archive variant {label} uses {len(outside)} "
                        f"edge(s) absent from its candidate pool, e.g. "
                        f"{sorted(outside)[:3]}: {archive}")
    else:
        raise ValueError(
            f"demand archive has an unknown edge-support status "
            f"{support_status!r}: {archive}")
    fingerprints = build_fingerprint.get("artifacts") or {}
    artifact_names = {
        "candidates.rou.xml": "candidate_routes",
        "candidates.meta.json": "candidate_metadata",
        "calibrated.rou.xml": "calibrated_q50",
        "calibrated.agents.json": "calibrated_q50_agents",
        "calibrated_v1.rou.xml": "calibrated_v1",
        "calibrated_v1.agents.json": "calibrated_v1_agents",
        "calibrated_v2.rou.xml": "calibrated_v2",
        "calibrated_v2.agents.json": "calibrated_v2_agents",
    }
    by_name = {record["name"]: record for record in records}
    for filename, label in artifact_names.items():
        expected = fingerprints.get(label)
        actual = by_name[filename]
        if not isinstance(expected, Mapping) or (
            expected.get("sha256") != actual["sha256"]
            or int(expected.get("bytes", -1)) != actual["bytes"]
        ):
            raise ValueError(
                f"demand archive fingerprint does not bind {filename}: {archive}")
    return {
        "run_id": str(manifest.get("run_id", archive.name)),
        "archive": str(archive),
        "finished_at": str(manifest.get("finished_at", "")),
        "demand_build_spec": required.to_dict(),
        "archive_manifest_sha256": sha256_file(archive / "manifest.json"),
        "outputs": records,
        "archive_content_key": _canonical_digest(records),
    }


def find_demand_archives(
    runs_root: Path,
    required: DemandBuildSpec,
) -> tuple[dict[str, Any], ...]:
    """Return all valid succeeded archives for ``required``, newest first."""
    matches: list[dict[str, Any]] = []
    for archive in sorted(Path(runs_root).glob("demand-*")):
        if not archive.is_dir():
            continue
        try:
            record = validate_demand_archive(archive, required)
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            continue
        matches.append(record)
    matches.sort(
        key=lambda item: (str(item["finished_at"]), str(item["run_id"])),
        reverse=True,
    )
    return tuple(matches)


def snapshot_live_demand_release(
    *,
    root: Path = _PROJECT_ROOT,
    products: Sequence[Path] = LIVE_DEMAND_RELEASE_PRODUCTS,
    directories: Sequence[Path] = LIVE_DEMAND_RELEASE_DIRECTORIES,
    marker: Path | None = None,
) -> dict[str, Any]:
    """Copy the live release aside before an envelope build."""
    directory = Path(tempfile.mkdtemp(prefix="live-demand-release-"))
    entries: list[dict[str, Any]] = []
    trees: list[dict[str, Any]] = []
    try:
        for index, relative in enumerate(products):
            source = Path(root) / relative
            saved: str | None = None
            if source.is_file():
                saved = f"{index:03}-{source.name}"
                shutil.copy2(source, directory / saved)
            entries.append({"relative": str(relative), "saved": saved})
        for index, relative in enumerate(directories):
            source = Path(root) / relative
            saved = None
            if source.is_dir():
                saved = f"tree{index:03}-{source.name}"
                shutil.copytree(source, directory / saved)
            trees.append({"relative": str(relative), "saved": saved})
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    snapshot = {"root": Path(root), "directory": directory,
                "entries": entries, "trees": trees}
    marker_path = LIVE_RELEASE_SNAPSHOT_MARKER if marker is None else Path(marker)
    snapshot["marker"] = marker_path
    _atomic_json(marker_path, {
        "schema_version": SCHEMA_VERSION,
        "kind": "live_demand_release_snapshot",
        "root": str(Path(root)),
        "directory": str(directory),
        "entries": entries,
        "trees": trees,
    })
    return snapshot


def restore_live_demand_release(snapshot: Mapping[str, Any]) -> None:
    """Return every live release product to its snapshotted bytes.

    A product that did not exist at snapshot time is removed again: a box
    with no active release must not gain a half-labelled one as a side
    effect of a monthly search.
    """
    directory = Path(snapshot["directory"])
    root = Path(snapshot["root"])
    for tree in snapshot.get("trees", ()):
        target = root / str(tree["relative"])
        saved = tree["saved"]
        if saved is None:
            shutil.rmtree(target, ignore_errors=True)
            continue
        source = directory / str(saved)
        if not source.is_dir():
            raise FileNotFoundError(
                f"live demand release snapshot is incomplete: {source}")
        staging = target.with_name(target.name + ".restore.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging)
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    for entry in snapshot["entries"]:
        target = root / str(entry["relative"])
        saved = entry["saved"]
        if saved is None:
            target.unlink(missing_ok=True)
            continue
        source = directory / str(saved)
        if not source.is_file():
            raise FileNotFoundError(
                f"live demand release snapshot is incomplete: {source}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".restore.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    shutil.rmtree(directory, ignore_errors=True)
    marker = snapshot.get("marker")
    if marker is not None:
        Path(marker).unlink(missing_ok=True)


def recover_live_demand_release(
    marker: Path = LIVE_RELEASE_SNAPSHOT_MARKER,
) -> dict[str, Any] | None:
    """Restore a live release left behind by a killed run, if there is one.

    The normal restore runs in a finally block, which a SIGKILL - or a
    serve.py timeout that kills the job - skips entirely. The snapshot marker
    survives that, so the next run can put the deployed release back before
    doing anything else. Returns what it recovered, or None if there was
    nothing to recover.
    """
    marker = Path(marker)
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("kind") != "live_demand_release_snapshot"
        or not isinstance(record.get("entries"), list)
    ):
        # A marker we cannot read is not a licence to guess: leave the box
        # alone and say so, rather than restoring from a half-written record.
        raise ValueError(f"unreadable live release snapshot marker: {marker}")
    directory = Path(str(record["directory"]))
    if not directory.is_dir():
        # The snapshot itself is gone (temp cleaned). Nothing can be restored;
        # drop the marker so it stops claiming otherwise.
        marker.unlink(missing_ok=True)
        return None
    snapshot = {
        "root": Path(str(record["root"])),
        "directory": directory,
        "entries": record["entries"],
        "trees": record.get("trees", []),
        "marker": marker,
    }
    restore_live_demand_release(snapshot)
    return record


def build_demand_archive(required: DemandBuildSpec) -> None:
    """Run the tracked demand builder for one missing envelope."""
    with demand_build_lock():
        with tempfile.TemporaryDirectory(prefix="monthly-demand-spec-") as raw:
            spec_path = Path(raw) / f"{required.build_key}.json"
            write_demand_build_spec(spec_path, required)
            completed = subprocess.run(
                [
                    sys.executable,
                    "build_sumo_demand.py",
                    "--demand-spec",
                    str(spec_path),
                    "--keep-scenarios",
                    "--direction-stress-variants",
                ],
                check=False,
                cwd=_PROJECT_ROOT,
                env=child_environment(),
            )
    if completed.returncode != 0:
        raise RuntimeError(
            f"demand build {required.build_key} failed with exit code "
            f"{completed.returncode}"
        )


RunnerFactory = Callable[..., ArchivedDemandSumoRunner]
DemandBuilder = Callable[[DemandBuildSpec], None]


class MonthlyDemandResolverRunner:
    """Candidate runner spanning several frozen calendar-envelope archives."""

    def __init__(
        self,
        spec: ClosureSearchSpec,
        *,
        baseline_trip_duration_p99_s: int,
        study_provenance_key: str,
        runs_root: Path = DEFAULT_RUNS_ROOT,
        release_root: Path = DEFAULT_RELEASE_ROOT,
        cache_root: Path = DEFAULT_BASELINE_CACHE,
        seed_workers: int = 1,
        envelope_policy: EnvelopePolicy = EnvelopePolicy(),
        build_missing: bool = True,
        include_disruption: bool = False,
        warm_execution: bool = False,
        boundary_controller=None,
        demand_builder: DemandBuilder = build_demand_archive,
        runner_factory: RunnerFactory = ArchivedDemandSumoRunner,
        live_release_root: Path = _PROJECT_ROOT,
        live_release_products: Sequence[Path] = LIVE_DEMAND_RELEASE_PRODUCTS,
    ) -> None:
        self.spec = ClosureSearchSpec.from_dict(spec.to_dict())
        if (
            isinstance(baseline_trip_duration_p99_s, bool)
            or not isinstance(baseline_trip_duration_p99_s, int)
            or baseline_trip_duration_p99_s <= 0
        ):
            raise ValueError(
                "baseline_trip_duration_p99_s must be a positive integer"
            )
        self.baseline_trip_duration_p99_s = baseline_trip_duration_p99_s
        self.study_provenance_key = study_provenance_key
        self.runs_root = Path(runs_root)
        self.release_root = Path(release_root)
        self.cache_root = Path(cache_root)
        self.seed_workers = seed_workers
        self.envelope_policy = envelope_policy
        self.build_missing = bool(build_missing)
        if include_disruption is not False and include_disruption is not True:
            raise ValueError("include_disruption must be a bool")
        self.include_disruption = bool(include_disruption)
        if warm_execution is not False and warm_execution is not True:
            raise ValueError("warm_execution must be a bool")
        if warm_execution and boundary_controller is None:
            raise ValueError(
                "warm execution requires an explicit boundary controller")
        self.warm_execution = bool(warm_execution)
        self.boundary_controller = boundary_controller
        self.demand_builder = demand_builder
        self.runner_factory = runner_factory
        self.live_release_root = Path(live_release_root)
        self.live_release_products = tuple(live_release_products)
        self._schedule_build_keys: dict[str, str] = {}
        self._runners: dict[str, ArchivedDemandSumoRunner] = {}
        self._release: dict[str, Any] | None = None
        self._prepared_schedule_ids: tuple[str, ...] | None = None

    def _required(
        self,
        schedule: ClosureSchedule,
    ) -> DemandBuildSpec:
        envelope = build_simulation_envelope(
            self.spec,
            schedule,
            baseline_trip_duration_p99_s=self.baseline_trip_duration_p99_s,
            policy=self.envelope_policy,
        )
        if self.spec.interday_policy == "independent_daily_reset_v1":
            return independent_daily_demand_spec(
                self.spec, schedule, envelope
            )
        return envelope_demand_spec(self.spec, envelope)

    def _request(
        self,
        schedules: Sequence[ClosureSchedule],
        required_by_key: Mapping[str, DemandBuildSpec],
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "monthly_demand_release_request",
            "release_id": self.spec.demand_build_id,
            "search_content_key": self.spec.content_key,
            "shortlist_schedule_ids": [
                schedule.schedule_id for schedule in schedules
            ],
            "baseline_trip_duration_p99_s": (
                self.baseline_trip_duration_p99_s
            ),
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            "required_builds": [
                required_by_key[key].to_dict()
                for key in sorted(required_by_key)
            ],
        }

    @staticmethod
    def _release_content(payload: Mapping[str, Any]) -> str:
        return _canonical_digest({
            key: value
            for key, value in payload.items()
            if key != "content_key"
        })

    def _load_release(
        self,
        path: Path,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        release = _read(path)
        if (
            release.get("schema_version") != SCHEMA_VERSION
            or release.get("kind") != "monthly_demand_release"
            or release.get("request") != request
            or release.get("content_key") != self._release_content(release)
        ):
            raise ValueError(f"monthly demand release is invalid: {path}")
        return release

    def _resolve_new_release(
        self,
        request: Mapping[str, Any],
        required_by_key: Mapping[str, DemandBuildSpec],
    ) -> dict[str, Any]:
        entries = []
        with demand_build_lock():
            live_snapshot: dict[str, Any] | None = None
            try:
                # Recheck under the inter-process lock: another search may have
                # completed the same immutable archive while this one waited.
                for key in sorted(required_by_key):
                    required = required_by_key[key]
                    matches = find_demand_archives(self.runs_root, required)
                    if not matches and self.build_missing:
                        if live_snapshot is None:
                            live_snapshot = snapshot_live_demand_release(
                                root=self.live_release_root,
                                products=self.live_release_products,
                            )
                        self.demand_builder(required)
                        matches = find_demand_archives(self.runs_root, required)
                    if not matches:
                        raise FileNotFoundError(
                            f"no succeeded immutable demand archive for "
                            f"{required.build_key} ({required.start_date}, "
                            f"{required.days} day(s), {required.source}); run "
                            f"build_sumo_demand.py with this closure-envelope "
                            f"DemandBuildSpec or allow automatic builds"
                        )
                    entries.append(matches[0])
            finally:
                if live_snapshot is not None:
                    restore_live_demand_release(live_snapshot)
        release = {
            "schema_version": SCHEMA_VERSION,
            "kind": "monthly_demand_release",
            "request": dict(request),
            "entries": entries,
        }
        release["content_key"] = self._release_content(release)
        return release

    def prepare(self, schedules: Sequence[ClosureSchedule]) -> None:
        schedules = tuple(schedules)
        if not schedules:
            raise ValueError("monthly demand resolver needs a non-empty shortlist")
        schedule_ids = tuple(schedule.schedule_id for schedule in schedules)
        if len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("monthly demand shortlist has duplicate schedules")
        if self._prepared_schedule_ids is not None:
            if schedule_ids != self._prepared_schedule_ids:
                raise ValueError(
                    "monthly demand resolver was prepared for another shortlist"
                )
            return

        required_by_key: dict[str, DemandBuildSpec] = {}
        for schedule in schedules:
            required = self._required(schedule)
            previous = required_by_key.get(required.build_key)
            if previous is not None and previous != required:
                raise ValueError("DemandBuildSpec build-key collision")
            required_by_key[required.build_key] = required
            self._schedule_build_keys[schedule.schedule_id] = required.build_key

        request = self._request(schedules, required_by_key)
        request_key = _canonical_digest(request, length=32)
        release_path = self.release_root / f"{request_key}.json"
        if release_path.is_file():
            release = self._load_release(release_path, request)
        else:
            release = self._resolve_new_release(request, required_by_key)
            _atomic_json(release_path, release)

        entries = release.get("entries")
        if not isinstance(entries, list):
            raise ValueError("monthly demand release entries are invalid")
        by_key = {
            str(entry.get("demand_build_spec", {}).get("build_key")): entry
            for entry in entries
            if isinstance(entry, Mapping)
        }
        if set(by_key) != set(required_by_key):
            raise ValueError("monthly demand release does not cover the shortlist")
        _require_one_demand_generation(by_key)

        for key, required in required_by_key.items():
            pinned = by_key[key]
            archive = Path(str(pinned["archive"]))
            # Releases may be copied or created with relative archive paths;
            # resolve them relative to the release manifest, never the
            # process working directory.
            if not archive.is_absolute():
                archive = (release_path.parent / archive).resolve()
            current = validate_demand_archive(archive, required)
            expected_pinned = dict(pinned)
            expected_pinned["archive"] = str(archive)
            if current != expected_pinned:
                raise ValueError(
                    f"pinned monthly demand archive changed: {archive}"
                )
            runner = self.runner_factory(
                self.spec,
                archive=archive,
                baseline_trip_duration_p99_s=(
                    self.baseline_trip_duration_p99_s
                ),
                study_provenance_key=self.study_provenance_key,
                cache_root=self.cache_root,
                seed_workers=self.seed_workers,
                envelope_policy=self.envelope_policy,
                expected_demand_spec=required,
                include_disruption=self.include_disruption,
                warm_execution=self.warm_execution,
                boundary_controller=self.boundary_controller,
            )
            self._runners[key] = runner

        self._release = {
            **release,
            "manifest_path": str(release_path.resolve()),
        }
        self._prepared_schedule_ids = schedule_ids

    def provenance(self) -> Mapping[str, Any]:
        if self._release is None or not self._runners:
            raise RuntimeError(
                "monthly demand resolver must be prepared before provenance"
            )
        child = [self._runners[key].provenance() for key in sorted(self._runners)]
        common_fields = (
            "source_files",
            "source_digest",
            "simulation_source_digest",
            "sumo_version",
            "platform",
            "simulation_mode",
            "metric_schema",
        )
        for field in common_fields:
            if any(item.get(field) != child[0].get(field) for item in child[1:]):
                raise ValueError(
                    f"monthly envelope backends disagree on {field}"
                )
        ranking_objective_evidence = child[0].get(
            "ranking_objective_evidence", "legacy_time_loss_v1"
        )
        if any(
            item.get(
                "ranking_objective_evidence", "legacy_time_loss_v1"
            ) != ranking_objective_evidence
            for item in child[1:]
        ):
            raise ValueError(
                "monthly envelope backends disagree on "
                "ranking_objective_evidence"
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "multi_envelope_monthly_sumo_backend",
            "simulation_mode": "meso",
            "study_provenance_key": self.study_provenance_key,
            "search_content_key": self.spec.content_key,
            "demand_release_id": self.spec.demand_build_id,
            "demand_release": dict(self._release),
            "envelope_backends": [
                {
                    "demand_build_id": item["demand_build_id"],
                    "archive_digest": item["archive_digest"],
                    "matched_baseline_id": item["matched_baseline_id"],
                    "archive_inputs": item["archive_inputs"],
                }
                for item in child
            ],
            "baseline_trip_duration_p99_s": (
                self.baseline_trip_duration_p99_s
            ),
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            "ranking_objective_evidence": ranking_objective_evidence,
            **{
                field: child[0][field]
                for field in common_fields
            },
        }

    def candidate_provenance(
        self, schedule: ClosureSchedule
    ) -> Mapping[str, Any]:
        """Return the exact child backend identity for one prepared unit.

        Independent-day result caching uses this narrower identity so an
        unrelated change to the parent shortlist cannot invalidate exact daily
        SUMO evidence. The child record still binds demand, network, runtime,
        source and metric semantics.
        """
        if self._prepared_schedule_ids is None:
            raise RuntimeError("monthly demand resolver is not prepared")
        key = self._schedule_build_keys.get(schedule.schedule_id)
        if key is None:
            raise ValueError("candidate was not part of the frozen shortlist")
        return self._runners[key].provenance()

    def candidate_execution_contract(
        self, schedule: ClosureSchedule
    ) -> Mapping[str, Any]:
        """Describe one child so it can run in an isolated interpreter.

        This is an execution contract, not evidence. It contains only the
        already-prepared archive and the exact constructor inputs needed to
        rebuild the same :class:`ArchivedDemandSumoRunner` with a private TraCI
        connection. No demand resolution or mutable release lookup occurs in
        the worker.
        """
        if self._prepared_schedule_ids is None:
            raise RuntimeError("monthly demand resolver is not prepared")
        key = self._schedule_build_keys.get(schedule.schedule_id)
        if key is None:
            raise ValueError("candidate was not part of the frozen shortlist")
        child = self._runners[key]
        expected = child.expected_demand_spec
        if expected is None:
            raise ValueError("isolated daily execution requires exact demand")
        return {
            "spec": self.spec.to_dict(),
            "archive": str(child.archive),
            "baseline_trip_duration_p99_s": (
                child.baseline_trip_duration_p99_s
            ),
            "study_provenance_key": child.study_provenance_key,
            "cache_root": str(child.cache_root),
            "envelope_policy": dataclasses.asdict(child.envelope_policy),
            "expected_demand_spec": expected.to_dict(),
            "warm_execution": child.warm_execution,
            "include_disruption": child.include_disruption,
        }

    def cleanup(self) -> None:
        """Release provisional resources owned by all prepared children."""
        for runner in self._runners.values():
            cleanup = getattr(runner, "cleanup", None)
            if callable(cleanup):
                cleanup()

    def archive_for(self, schedule: ClosureSchedule) -> Path:
        """The immutable demand archive that covers this schedule's envelope.

        PR D. Deterministic cost needs the ROUTE FILES, not a simulator, so the
        resolver exposes the resolution result on its own. A caller can build a
        process-free provider from it without ever constructing a SUMO runner
        or opening a TraCI connection.
        """
        if self._prepared_schedule_ids is None:
            raise RuntimeError("monthly demand resolver is not prepared")
        key = self._schedule_build_keys.get(schedule.schedule_id)
        if key is None:
            raise ValueError("candidate was not part of the frozen shortlist")
        return Path(self._runners[key].archive)

    def deterministic_disruption_provider(
        self,
        schedule: ClosureSchedule,
        *,
        cache: Any = None,
        network: Any = None,
        unit_identity: Mapping[str, Any] | None = None,
    ):
        """A process-free cost provider for one schedule's archive.

        Starts no SUMO, opens no TraCI connection and reads no simulation
        outcome — it resolves the archive and delegates to
        `deterministic_disruption.ArchiveDisruptionProvider`.
        """
        from traffic_sim.simulation.deterministic_disruption import (
            ArchiveDisruptionProvider,
        )

        return ArchiveDisruptionProvider(
            self.spec,
            archive=self.archive_for(schedule),
            network=network,
            cache=cache,
            unit_identity=unit_identity,
        )

    def run_candidate(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> CandidateEvidence:
        if self._prepared_schedule_ids is None:
            raise RuntimeError("monthly demand resolver is not prepared")
        key = self._schedule_build_keys.get(schedule.schedule_id)
        if key is None:
            raise ValueError("candidate was not part of the frozen shortlist")
        return self._runners[key].run_candidate(
            schedule,
            target_repetitions=target_repetitions,
            existing=existing,
            stage=stage,
        )
