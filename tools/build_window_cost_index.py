"""Build and prove the conditional Phase 5 window-cost index.

The index is built from the bound raw demand archives, not from the
deterministic-cost cache.  The cache is opened only afterwards as an
independent exact oracle.  This distinction is material: copying cached
answers and comparing them with themselves is not an indexed computation.
The command never opens SUMO outcomes, and adoption is reported only after
all three variants for all 1,950 daily units compare field-for-field.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# Make direct invocation independent of the caller's working directory, like
# the other repository tools.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation import deterministic_disruption
from traffic_sim.simulation.deterministic_disruption import (
    ArchiveDisruptionProvider,
    DailyCostCache,
    NetworkCostModel,
    VARIANT_FILENAMES,
    closure_seconds,
)
from traffic_sim.simulation.disruption import (
    build_parsed_window_cost_index,
    parse_route_vehicles,
)
from traffic_sim.simulation.independent_daily import daily_unit_records
from traffic_sim.simulation.monthly_demand import (
    MonthlyDemandResolverRunner,
    _archives_for_build_key,
    find_demand_archives,
    validate_qualified_demand_manifest_shape,
)
from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    load_index,
    publish_new_file,
    write_index,
)
from traffic_sim.simulation.cost_ordered_execution import (
    ParentCost,
    build_cost_ledger,
)
from traffic_sim.simulation.deterministic_disruption import (
    parent_closure_cost,
    sum_daily_disruption,
)
from tools.profile_monthly_cost_ledger import (
    producer_runtime_manifest,
    producer_source_manifest,
)

EXPECTED_DAILY_UNITS = 1950
EXPECTED_VARIANT_RECORDS = 5850
EXPECTED_PARENTS = 1690


class _IndexedLedgerSource:
    """Run the complete parent ledger through a loaded index.

    This is intentionally separate from ``_raw_index_records``: the latter
    builds daily records, while this source measures the actual adoption path
    of every parent lookup, aggregation and deterministic sort.
    """

    def __init__(self, spec: ClosureSearchSpec, index: WindowCostIndex):
        self.spec = spec
        self.index = index
        self.lookups = 0
        # Distinct units, because `lookups` counts parent-to-unit
        # relationships: the same daily unit is priced inside every parent
        # whose window contains it.  Comparing lookups against the 5,850
        # variant records confuses two different quantities.
        self.units: set[str] = set()
        self._identity = dict(index.bound_identity.get("provider_identity", {}))

    def identity(self) -> Mapping[str, Any]:
        return dict(self._identity)

    def parent_cost(self, parent: ClosureSchedule) -> ParentCost:
        daily_records = []
        unit_ids = []
        # `daily_unit_records` yields (unit_id, identity, build_schedule): the
        # middle item is the unit's identity mapping, NOT a schedule, and the
        # schedule itself is deferred behind the callable.  Reading the
        # identity as a schedule crashed the whole-month adoption replay
        # (2026-09-16) after the oracle had already been proved and the index
        # written, leaving ranking, winner and stop proof unmeasured.
        for unit_id, identity, build_schedule in daily_unit_records(
                self.spec, parent):
            if not isinstance(identity, Mapping) or not identity:
                raise WindowCostIndexError(
                    f"daily unit {unit_id} has no identity mapping")
            schedule = build_schedule()
            # `lookup` fails closed when the identity stored with the record
            # is not the schedule just built for this unit, so the built
            # schedule -- not a remembered one -- decides what may be read.
            row = self.index.lookup(str(unit_id), schedule.schedule_id)
            self.lookups += 1
            self.units.add(str(unit_id))
            daily_records.append(tuple(dict(item) for item in row))
            unit_ids.append(str(unit_id))
        return ParentCost(
            candidate_id=parent.schedule_id,
            cost=parent_closure_cost(parent.schedule_id, daily_records),
            per_variant=sum_daily_disruption(daily_records),
            daily_unit_ids=tuple(unit_ids),
        )


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()


def _publish(path: Path, record: Mapping[str, Any]) -> None:
    """Append-only, atomic evidence publication."""
    publish_new_file(
        path,
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        label="Phase 5 evidence")


def _validate_profile_binding(profile: Mapping[str, Any],
                              profile_path: Path) -> None:
    """Reject a profile whose envelope or bound spec was changed in place.

    Phase 5 is allowed to consume an inconclusive Phase 4 profile when its
    population, timing and no-SUMO gates are complete, but that exception does
    not make the profile an untrusted input.  The profile content key and the
    bound spec digest are therefore checked before any cache or route input is
    opened.
    """
    if not isinstance(profile, Mapping) or profile.get("content_key") != _digest(
            {key: value for key, value in profile.items()
             if key != "content_key"}):
        raise WindowCostIndexError(
            f"Phase 4 profile content key mismatch: {profile_path}")
    bindings = profile.get("bindings") or {}
    bound_spec = profile.get("bound_spec")
    if not isinstance(bound_spec, Mapping):
        bound_spec = bindings.get("bound_spec")
    if not isinstance(bound_spec, Mapping) or not bound_spec.get("path"):
        raise WindowCostIndexError("profile has no bound search spec")
    spec_path = Path(str(bound_spec["path"])).resolve()
    expected_spec_digest = bindings.get("bound_spec_sha256")
    if not isinstance(expected_spec_digest, str) or not expected_spec_digest:
        raise WindowCostIndexError("profile has no bound search spec digest")
    if not spec_path.is_file() or sha256_file(spec_path) != expected_spec_digest:
        raise WindowCostIndexError("bound search spec drifted")
    source_manifest = bindings.get("producer_source_manifest")
    if not isinstance(source_manifest, Mapping) or not source_manifest:
        raise WindowCostIndexError(
            "Phase 4 profile has no complete producer source manifest")
    if dict(source_manifest) != producer_source_manifest():
        raise WindowCostIndexError("Phase 4 producer source manifest drifted")
    runtime_manifest = bindings.get("producer_runtime_manifest")
    if not isinstance(runtime_manifest, Mapping) \
            or dict(runtime_manifest) != producer_runtime_manifest():
        raise WindowCostIndexError("Phase 4 producer runtime manifest drifted")
    policy = bindings.get("policy")
    if not isinstance(policy, Mapping) or not policy.get("path"):
        raise WindowCostIndexError("Phase 4 profile has no bound policy")
    policy_path = Path(str(policy["path"])).resolve()
    if not policy_path.is_file() or sha256_file(policy_path) != policy.get(
            "sha256"):
        raise WindowCostIndexError("Phase 4 policy drifted")


@dataclass(frozen=True)
class _ArchiveDescriptor:
    """What the raw phase keeps about one build key before opening it."""

    build_key: str
    archive: Path
    content_key: str | None
    output_sha256: Mapping[str, str]
    unit_ids: tuple[str, ...]


def _phase_error(build_key: str, phase: str,
                 error: Exception) -> WindowCostIndexError:
    """Name the build key and phase; the original error stays the cause."""
    return WindowCostIndexError(
        f"build key {build_key}: {phase} failed: "
        f"{type(error).__name__}: {error}")


def _resolve_units(
    spec: ClosureSearchSpec,
    *,
    runs_root: Path,
    qualified_demand_manifest: Mapping[str, Any],
) -> tuple[dict[str, tuple[dict[str, Any], ClosureSchedule, Path]],
           tuple[_ArchiveDescriptor, ...]]:
    """Every daily unit and one validated descriptor per build key.

    Shared by the streaming production loop and the retain test oracle, so
    both price exactly the same population against the same proofs.
    """
    resolver = MonthlyDemandResolverRunner(
        spec,
        runs_root=Path(runs_root),
        build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=qualified_demand_manifest,
    )
    # ONE content-derived archive index for the whole operation.  Each
    # `find_demand_archives` call used to rebuild it from metadata content, so
    # a 1,950-unit month re-read every archive's large `demand_meta.json` 1,950
    # times; sampling the 2026-09-16 attempt showed its 31,271 s preparation
    # going into exactly that JSON parsing.  Sharing the index weakens no
    # proof: each DISTINCT demand contract below still runs the full
    # `validate_demand_archive` content check and the qualified-manifest
    # match, and nothing here is keyed on a path, a size or an mtime.
    archive_index = _archives_for_build_key(Path(runs_root))
    # Pass 1: every distinct daily unit and its built schedule.
    collected: dict[str, tuple[dict[str, Any], ClosureSchedule]] = {}
    for parent in iter_closure_schedules(spec):
        for unit_id, identity, build_schedule in daily_unit_records(spec, parent):
            schedule = build_schedule()
            existing = collected.get(unit_id)
            if existing is not None:
                if existing[0] != identity or existing[1].to_dict() != schedule.to_dict():
                    raise WindowCostIndexError(
                        f"daily unit identity collision for {unit_id}")
                continue
            collected[unit_id] = (identity, schedule)

    # Pass 2: the demand contract each unit needs, deduplicated by build key.
    # A month's 1,950 units resolve to 30 distinct build keys, so validating
    # per unit re-proved the same 30 archives 1,950 times.
    required_by_unit: dict[str, Any] = {}
    required_by_key: dict[str, Any] = {}
    for unit_id, (_identity, schedule) in collected.items():
        required = resolver._required(schedule)
        required_by_unit[unit_id] = required
        required_by_key.setdefault(required.build_key, required)

    # Pass 3: resolve and FULLY validate each distinct build key exactly once,
    # in build-key order, keeping only a small frozen descriptor.  Nothing
    # here is keyed on a path, size or mtime, and no state survives the call.
    descriptors: list[_ArchiveDescriptor] = []
    archive_of: dict[str, Path] = {}
    for build_key, required in sorted(required_by_key.items()):
        try:
            matches = find_demand_archives(
                Path(runs_root), required,
                qualified_manifest=qualified_demand_manifest,
                _archive_index=archive_index)
        except Exception as error:
            raise _phase_error(build_key, "validate", error) from error
        if not matches:
            raise WindowCostIndexError(
                f"no immutable demand archive for build key {build_key}")
        match = matches[0]
        archive_of[build_key] = Path(match["archive"]).resolve()
        descriptors.append(_ArchiveDescriptor(
            build_key=build_key,
            archive=archive_of[build_key],
            content_key=match.get("archive_content_key"),
            output_sha256={
                str(item["name"]): str(item["sha256"])
                for item in match.get("outputs") or ()},
            unit_ids=tuple(sorted(
                unit_id for unit_id, item in required_by_unit.items()
                if item.build_key == build_key)),
        ))

    # Pass 4: bind every unit to the archive its build key already proved.
    units = {
        unit_id: (identity, schedule,
                  archive_of[required_by_unit[unit_id].build_key])
        for unit_id, (identity, schedule) in collected.items()
    }
    if len(units) != EXPECTED_DAILY_UNITS:
        raise WindowCostIndexError(
            f"raw input population has {len(units)} units, expected "
            f"{EXPECTED_DAILY_UNITS}")
    return units, tuple(descriptors)


def _timings() -> dict[str, float]:
    return {
        "xml_parse": 0.0,
        "route_vehicle_grouping": 0.0,
        "shortest_path_detour": 0.0,
        "window_aggregation": 0.0,
    }


def _measurement(indexed, provider_identities, timings, *, loop,
                 variant_indexes, bindings=None) -> dict[str, Any]:
    return {
        **(bindings or {}),
        "daily_units": len(indexed),
        "daily_variant_records": len(indexed) * 3,
        "timings": timings,
        "raw_input_algorithm": "ArchiveDisruptionProvider(cache=None)",
        "raw_input_strategy": (
            "parse_each_archive_variant_once; precompute crossing events and "
            "unique-OD detours; aggregate each daily window by lookup"),
        "raw_loop": loop,
        "structural_reuse": {
            "archive_variant_indexes": variant_indexes,
            "window_queries": len(indexed) * 3,
            "reused_route_vehicle_grouping": True,
            "reused_unique_route_detours": True,
        },
        "provider_identities": provider_identities,
    }


def _unit_records(spec, provider, indexes, schedule, timings):
    """One daily unit's three variant records, from the archive's indexes."""
    if indexes is None:
        # Compatibility branch for the deliberately tiny provider test
        # doubles.  It is not reachable for the real CLI.
        return tuple(provider.disruption(schedule))
    inputs = provider.inputs
    closures = closure_seconds(
        spec, schedule, epoch=inputs.epoch, duration_s=inputs.duration_s)
    return tuple({
        "demand_variant": variant,
        **indexes[variant].disruption(
            closures,
            timing=lambda phase, elapsed: timings.__setitem__(
                phase, timings.get(phase, 0.0) + float(elapsed)),
        )
    } for variant in ("q10", "q50", "q90"))


def _oracle_record(oracle_cache, provider, schedule, unit_id):
    expected = oracle_cache.load(provider.cache_identity(schedule))
    if expected is None:
        raise WindowCostIndexError(
            f"independent deterministic oracle is missing for {unit_id}")
    return {"schedule_id": schedule.schedule_id,
            "records": [dict(item) for item in expected]}


RAW_INPUT_FILES = ("demand_meta.json", *sorted(VARIANT_FILENAMES.values()))


def _archive_binding(descriptor: _ArchiveDescriptor) -> dict[str, Any]:
    """The validated hashes of exactly the files the raw phase reads."""
    return {"archive": str(descriptor.archive),
            "content_key": descriptor.content_key,
            "files": {name: descriptor.output_sha256.get(name)
                      for name in RAW_INPUT_FILES}}


def _verify_raw_inputs_unchanged(measurement: Mapping[str, Any]) -> None:
    """Re-prove, from content, every input the raw phase priced.

    Called immediately before the index and the evidence are published:
    the costing sources must be the ones the operation started with, and
    every archive file it read must still hash to its validated digest.
    """
    sources = dict(deterministic_disruption.costing_source_identity())
    if sources != dict(measurement.get("costing_sources") or {}):
        raise WindowCostIndexError(
            "costing source changed before publication")
    bindings = measurement.get("archive_bindings")
    if not isinstance(bindings, Mapping) or not bindings:
        raise WindowCostIndexError(
            "the raw phase recorded no archive bindings to re-prove")
    for build_key, binding in sorted(bindings.items()):
        archive = Path(binding["archive"])
        drifted = sorted(
            name for name, digest in binding["files"].items()
            if not digest or sha256_file(archive / name) != digest)
        if drifted:
            raise WindowCostIndexError(
                f"build key {build_key}: {drifted} changed before "
                "publication")


def _verify_before_publication(profile_path: Path,
                               bound: Mapping[str, Any],
                               raw_measurement: Mapping[str, Any],
                               raw_input_sources: Mapping[str, str]) -> None:
    """Every binding the build started from must still hold."""
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    _validate_profile_binding(profile, Path(profile_path))
    if profile.get("content_key") != bound["profile"].get("content_key"):
        raise WindowCostIndexError("Phase 4 profile changed before publication")
    qualified = bound["qualified_ref"]
    if sha256_file(Path(str(qualified["path"])).resolve()) != qualified[
            "sha256"]:
        raise WindowCostIndexError(
            "qualified-demand manifest changed before publication")
    if _raw_input_sources() != dict(raw_input_sources):
        raise WindowCostIndexError("index builder source changed before "
                                   "publication")
    _verify_raw_inputs_unchanged(raw_measurement)


def _raw_input_sources() -> dict[str, Any]:
    return {relative: sha256_file(ROOT / relative)
            for relative in ("tools/build_window_cost_index.py",
                             "traffic_sim/simulation/window_cost_index.py")}


def _verify_opened(descriptor: _ArchiveDescriptor, provider,
                   sources: Mapping[str, str]) -> None:
    """The opened archive is the validated one, under unchanged sources."""
    inputs = provider.inputs
    expected = {"demand_meta.json": inputs.demand_meta_sha256}
    expected.update({inputs.variant_paths[variant].name: digest
                     for variant, digest in inputs.variant_sha256.items()})
    drifted = sorted(name for name, digest in expected.items()
                     if descriptor.output_sha256.get(name) != digest)
    if drifted:
        raise WindowCostIndexError(
            f"archive changed after validation: {drifted}")
    if dict(provider.identity()["costing_sources"]) != dict(sources):
        raise WindowCostIndexError(
            "costing source changed during the raw phase")


def _archive_indexes(spec, provider, network, timings):
    """Parse each variant once and keep only its window-cost index."""
    indexes = {}
    for variant in ("q10", "q50", "q90"):
        parsed = parse_route_vehicles(
            provider.inputs.variant_paths[variant],
            timing=lambda phase, elapsed: timings.__setitem__(
                phase, timings.get(phase, 0.0) + float(elapsed)))
        indexes[variant] = build_parsed_window_cost_index(
            parsed, set(spec.directed_edges),
            network.edge_time, network.edge_len,
            adjacency=network.adjacency,
            destination_access=network.destination_access,
            timing=lambda phase, elapsed: timings.__setitem__(
                phase, timings.get(phase, 0.0) + float(elapsed)),
        )
        del parsed
    return indexes


def _stream_archive(spec, descriptor, units, *, network, sources,
                    oracle_cache, timings, out) -> int:
    """Price every unit of one archive; nothing large outlives the call.

    Returns the number of variant indexes built. ``out`` receives only the
    final small records: indexed rows, oracle rows and provider identities.
    """
    phase = "open"
    provider = indexes = None
    try:
        provider = ArchiveDisruptionProvider(
            spec, archive=descriptor.archive, network=network, cache=None)
        if getattr(provider, "inputs", None) is not None:
            _verify_opened(descriptor, provider, sources)
            phase = "parse"
            indexes = _archive_indexes(spec, provider, network, timings)
        phase = "compute"
        for unit_id in descriptor.unit_ids:
            _identity, schedule, _archive = units[unit_id]
            provider_identity = getattr(provider, "identity", None)
            if callable(provider_identity):
                out["identities"][unit_id] = dict(provider_identity())
            out["indexed"][unit_id] = {
                "schedule_id": schedule.schedule_id,
                "records": [dict(item) for item in _unit_records(
                    spec, provider, indexes, schedule, timings)],
            }
            out["oracle"][unit_id] = _oracle_record(
                oracle_cache, provider, schedule, unit_id)
            if indexes is None:
                for name, elapsed in provider.timing_snapshot().items():
                    if name in timings:
                        timings[name] += float(elapsed)
        return len(indexes or ())
    except Exception as error:
        raise _phase_error(descriptor.build_key, phase, error) from error
    finally:
        # Release the archive even when it failed: a traceback keeps this
        # frame alive, and the next operation must not inherit its routes.
        del provider, indexes


def _raw_index_records(
    spec: ClosureSearchSpec,
    *,
    runs_root: Path,
    oracle_cache: DailyCostCache,
    qualified_demand_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Compute index records from route XML and return a separate oracle.

    Streams one archive at a time. Every build key is resolved and fully
    validated once into a small descriptor first; then each archive is
    opened, its three variants parsed once, all of its daily units priced,
    and the archive released before the next one is opened. Only the final
    records, oracle rows and provider identities are kept, and they are
    returned in unit-id order, so the result never depends on how build keys
    arrive or how long an archive took.

    ``ArchiveDisruptionProvider`` is deliberately constructed with
    ``cache=None``.  The oracle is read only after each unit's computation
    and is used solely for the field-by-field comparison.  Keeping these maps
    separate prevents a warmed cache lookup from becoming the implementation
    under test.  An opened archive must still hash to what its validation
    proved, and the costing sources must not change during the operation.
    """
    units, descriptors = _resolve_units(
        spec, runs_root=runs_root,
        qualified_demand_manifest=qualified_demand_manifest)
    network = NetworkCostModel()
    sources = dict(deterministic_disruption.costing_source_identity())
    timings = _timings()
    out: dict[str, dict[str, Any]] = {"indexed": {}, "oracle": {},
                                      "identities": {}}
    variant_indexes = 0
    for descriptor in descriptors:
        variant_indexes += _stream_archive(
            spec, descriptor, units, network=network, sources=sources,
            oracle_cache=oracle_cache, timings=timings, out=out)
    order = sorted(units)
    indexed = {unit_id: out["indexed"][unit_id] for unit_id in order}
    oracle = {unit_id: out["oracle"][unit_id] for unit_id in order}
    identities = {unit_id: out["identities"][unit_id] for unit_id in order
                  if unit_id in out["identities"]}
    return indexed, oracle, _measurement(
        indexed, identities, timings,
        loop="stream_one_archive_at_a_time",
        variant_indexes=variant_indexes,
        bindings={
            "costing_sources": sources,
            "archive_bindings": {descriptor.build_key:
                                 _archive_binding(descriptor)
                                 for descriptor in descriptors},
        })


def _retain_index_records(
    spec: ClosureSearchSpec,
    *,
    runs_root: Path,
    oracle_cache: DailyCostCache,
    qualified_demand_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """TEST ORACLE ONLY: the f5608a7 loop that retains every archive.

    It parses and indexes every archive before pricing any unit, which is
    modelled at about 20 GB for the month.  Production uses the streaming
    `_raw_index_records`; this stays so tests can prove the two identical.
    """
    units, _descriptors = _resolve_units(
        spec, runs_root=runs_root,
        qualified_demand_manifest=qualified_demand_manifest)
    network = NetworkCostModel()
    indexed: dict[str, dict[str, Any]] = {}
    oracle: dict[str, dict[str, Any]] = {}
    provider_identities: dict[str, dict[str, Any]] = {}
    timings = _timings()
    parsed_by_archive: dict[Path, dict[str, tuple[Any, ...]]] = {}
    provider_by_archive: dict[Path, Any] = {}
    index_by_archive: dict[Path, dict[str, Any]] = {}
    for _unit_id, (_identity, _schedule, archive) in sorted(units.items()):
        if archive in parsed_by_archive:
            continue
        provider = ArchiveDisruptionProvider(
            spec, archive=archive, network=network, cache=None)
        provider_by_archive[archive] = provider
        inputs = getattr(provider, "inputs", None)
        if inputs is None:
            parsed_by_archive[archive] = {}
            continue
        parsed_by_archive[archive] = {
            variant: parse_route_vehicles(
                path, timing=lambda phase, elapsed: timings.__setitem__(
                    phase, timings.get(phase, 0.0) + float(elapsed)))
            for variant, path in inputs.variant_paths.items()
        }
        index_by_archive[archive] = {
            variant: build_parsed_window_cost_index(
                parsed_by_archive[archive][variant], set(spec.directed_edges),
                network.edge_time, network.edge_len,
                adjacency=network.adjacency,
                destination_access=network.destination_access,
                timing=lambda phase, elapsed: timings.__setitem__(
                    phase, timings.get(phase, 0.0) + float(elapsed)),
            )
            for variant in ("q10", "q50", "q90")
        }
    for unit_id in sorted(units):
        _identity, schedule, archive = units[unit_id]
        provider = provider_by_archive[archive]
        provider_identity = getattr(provider, "identity", None)
        if callable(provider_identity):
            provider_identities[unit_id] = dict(provider_identity())
        indexed[unit_id] = {
            "schedule_id": schedule.schedule_id,
            "records": [dict(item) for item in _unit_records(
                spec, provider, index_by_archive.get(archive), schedule,
                timings)],
        }
        oracle[unit_id] = _oracle_record(
            oracle_cache, provider, schedule, unit_id)
        if not parsed_by_archive[archive]:
            for phase, elapsed in provider.timing_snapshot().items():
                if phase in timings:
                    timings[phase] += float(elapsed)
    return indexed, oracle, _measurement(
        indexed, provider_identities, timings,
        loop="retain_all_archives",
        variant_indexes=sum(len(value) for value in index_by_archive.values()))


def _bound_inputs(profile_path: Path) -> dict[str, Any]:
    """Every Phase 4 binding a Phase 5 run must re-prove before it reads data.

    Factored out so the build path and the resume path validate through ONE
    implementation: a second copy is how a resume ends up trusting something
    the builder would have refused.
    """
    profile_path = Path(profile_path).resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    _validate_profile_binding(profile, profile_path)
    qualified_ref = profile.get("qualified_demand_manifest")
    if (not isinstance(qualified_ref, Mapping)
            or set(qualified_ref) != {"path", "sha256", "content_key", "evidence_id"}):
        raise WindowCostIndexError(
            "Phase 5 profile lacks a complete qualified-demand manifest binding")
    qualified_path = Path(str(qualified_ref["path"])).resolve()
    if (not qualified_path.is_file()
            or sha256_file(qualified_path) != qualified_ref["sha256"]):
        raise WindowCostIndexError("qualified-demand manifest bytes drifted")
    qualified_manifest = json.loads(qualified_path.read_text(encoding="utf-8"))
    try:
        validate_qualified_demand_manifest_shape(qualified_manifest)
    except ValueError as error:
        raise WindowCostIndexError(str(error)) from error
    if (qualified_manifest.get("status") != "PASS"
            or qualified_manifest.get("content_key") != qualified_ref["content_key"]
            or qualified_manifest.get("evidence_id") != qualified_ref["evidence_id"]):
        raise WindowCostIndexError(
            "Phase 5 qualified-demand manifest binding is not passing")
    if profile.get("phase_5_decision") != "TRIGGERED":
        raise WindowCostIndexError("Phase 5 index is not allowed before trigger")
    if not (profile.get("population_complete")
            and profile.get("phase_timing_complete")
            and profile.get("sumo_zero_launch_gate")):
        raise WindowCostIndexError(
            "Phase 5 requires complete population, timing and zero-SUMO proof")
    bound_spec = profile.get("bound_spec")
    if not isinstance(bound_spec, Mapping):
        bound_spec = (profile.get("bindings") or {}).get("bound_spec")
    spec = ClosureSearchSpec.from_dict(json.loads(
        Path(str(bound_spec["path"])).read_text(encoding="utf-8")))
    if spec.content_key != bound_spec.get("search_content_key"):
        raise WindowCostIndexError("profile search spec content key drifted")
    ledger_path = profile_path.parent / "cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger.get("content_key") != profile.get("ledger_content_key"):
        raise WindowCostIndexError("Phase 4 ledger content key drifted")
    parent_unit_ids = {
        str(unit_id)
        for parent in ledger.get("costs", ())
        for unit_id in parent.get("daily_unit_ids", ())
    }
    if len(parent_unit_ids) != EXPECTED_DAILY_UNITS:
        raise WindowCostIndexError("ledger does not contain 1,950 unique units")
    cache_root = Path(str(profile.get("cache", {}).get("root", "")))
    if not cache_root.is_dir():
        raise WindowCostIndexError(f"daily-cost cache is missing: {cache_root}")
    if parent_unit_ids != {
        str(unit_id)
        for parent in ledger.get("costs", ())
        for unit_id in parent.get("daily_unit_ids", ())
    }:
        raise WindowCostIndexError("ledger unit population changed while reading")
    runs_root = Path(str(profile.get("runs_root", ROOT / "runs")))
    parents = tuple(iter_closure_schedules(spec))
    if len(parents) != EXPECTED_PARENTS:
        raise WindowCostIndexError(
            f"bound spec has {len(parents)} parents, expected {EXPECTED_PARENTS}")
    baseline_time_s = float(profile.get("wall_time_s", 0.0))
    if baseline_time_s <= 0:
        raise WindowCostIndexError(
            "Phase 4 profile has no positive cold wall-time baseline")
    return {
        "profile": profile,
        "profile_path": profile_path,
        "qualified_ref": dict(qualified_ref),
        "qualified_manifest": qualified_manifest,
        "bound_spec": dict(bound_spec),
        "spec": spec,
        "ledger": ledger,
        "parent_unit_ids": parent_unit_ids,
        "cache_root": cache_root,
        "runs_root": runs_root,
        "parents": parents,
        "baseline_time_s": baseline_time_s,
    }


def build_from_profile(
    profile_path: Path,
    *,
    index_out: Path,
    evidence_out: Path,
    evidence_id: str,
) -> dict[str, Any]:
    bound = _bound_inputs(profile_path)
    profile = bound["profile"]
    profile_path = bound["profile_path"]
    qualified_ref = bound["qualified_ref"]
    qualified_manifest = bound["qualified_manifest"]
    bound_spec = bound["bound_spec"]
    spec = bound["spec"]
    ledger = bound["ledger"]
    parent_unit_ids = bound["parent_unit_ids"]
    cache_root = bound["cache_root"]
    runs_root = bound["runs_root"]
    parents = bound["parents"]
    baseline_time_s = bound["baseline_time_s"]
    raw_input_sources = _raw_input_sources()
    started = time.perf_counter()
    records, oracle_records, raw_measurement = _raw_index_records(
        spec, runs_root=runs_root, oracle_cache=DailyCostCache(cache_root),
        qualified_demand_manifest=qualified_manifest)
    preparation_time_s = time.perf_counter() - started
    if set(records) != parent_unit_ids:
        raise WindowCostIndexError("raw input population does not match ledger units")
    bound_identity = {
        "schema": "subhour-phase5-window-cost-index-bound-v1",
        "search_content_key": str(bound_spec["search_content_key"]),
        "ledger_content_key": str(profile.get("ledger_content_key", "")),
        "provider_identity": ledger.get("provider_identity", {}),
        "source_profile_content_key": str(profile.get("content_key", "")),
        "qualified_demand_manifest": dict(qualified_ref),
        "policy_content_key": str(
            (profile.get("bindings") or {}).get("policy", {}).get(
                "content_key", "")),
        "producer_source_manifest": dict(
            (profile.get("bindings") or {}).get(
                "producer_source_manifest", {})),
        "producer_runtime_manifest": dict(
            (profile.get("bindings") or {}).get(
                "producer_runtime_manifest", {})),
        "raw_input_algorithm": raw_measurement["raw_input_algorithm"],
        "raw_input_sources": dict(raw_input_sources),
        "provider_identities": raw_measurement["provider_identities"],
    }
    index = WindowCostIndex(
        bound_identity=bound_identity,
        records=records,
        preparation_time_s=preparation_time_s,
    )
    oracle = index.compare_oracle(oracle_records)
    if oracle["indexed_variant_records"] != EXPECTED_VARIANT_RECORDS \
            or not oracle["oracle_complete"] or not oracle["field_identical"]:
        raise WindowCostIndexError("Phase 5 oracle is not complete and identical")
    index_out = Path(index_out)
    if index_out.exists() and any(index_out.iterdir()):
        raise FileExistsError(f"index output root must be fresh: {index_out}")
    index_out.mkdir(parents=True, exist_ok=True)
    index_path = index_out / "window-cost-index.json"
    _verify_before_publication(profile_path, bound, raw_measurement,
                               raw_input_sources)
    persistence_started = time.perf_counter()
    write_index(index_path, index)
    persisted = load_index(
        index_path,
        expected_identity=bound_identity,
        expected_daily_units=EXPECTED_DAILY_UNITS,
        expected_variant_records=EXPECTED_VARIANT_RECORDS,
    )
    persistence_load_time_s = time.perf_counter() - persistence_started
    indexed_source = _IndexedLedgerSource(spec, persisted)
    indexed_started = time.perf_counter()
    indexed_ledger = build_cost_ledger(spec, parents, indexed_source)
    indexed_ledger_time_s = time.perf_counter() - indexed_started
    # Lookups count parent-to-daily-unit relationships (1,690 parents x 5
    # days), which is not the 5,850 variant-record count the old check used.
    expected_lookups = sum(
        len(item.get("daily_unit_ids", ())) for item in ledger.get("costs", ()))
    if len(indexed_ledger.costs) != EXPECTED_PARENTS \
            or len(indexed_source.units) != EXPECTED_DAILY_UNITS \
            or indexed_source.lookups != expected_lookups:
        raise WindowCostIndexError(
            f"indexed adoption ledger covered {len(indexed_ledger.costs)} "
            f"parents, {len(indexed_source.units)} distinct daily units and "
            f"{indexed_source.lookups} lookups; expected {EXPECTED_PARENTS}, "
            f"{EXPECTED_DAILY_UNITS} and {expected_lookups}")
    indexed_total_time_s = (
        preparation_time_s + persistence_load_time_s + indexed_ledger_time_s)
    cold_benefit_s = baseline_time_s - indexed_total_time_s
    # A measured negative result is evidence, not an error. After an
    # expensive, correct run the finding is published append-only instead of
    # being thrown away; it never becomes PASS and never adopts the index.
    benefit_proven = bool(cold_benefit_s > 0)
    # The freshly built index must also reproduce the baseline ledger's
    # decision-bearing content, compared exactly as the resume path compares
    # it. A faster index that prices a different month is not an improvement,
    # so a difference can never be published as PASS.
    indexed_dict = indexed_ledger.to_dict()
    ledger_identical, ledger_comparison = compare_decision_ledgers(
        indexed_dict, ledger)
    evidence = {
        "schema": "subhour_phase5_window_cost_index_evidence_v1",
        "kind": "subhour_phase5_window_cost_index",
        "phase": 5,
        "release_evidence": False,
        "status": ("PASS" if benefit_proven and ledger_identical
                   else "NOT_ADOPTED"),
        "adopted": False,
        "ledger_identical": ledger_identical,
        "ledger_comparison": ledger_comparison,
        "baseline_ledger_content_key": ledger.get("content_key"),
        "evidence_id": evidence_id,
        "source_profile": str(profile_path),
        "source_profile_content_key": profile.get("content_key"),
        "bound_identity": bound_identity,
        "index_content_key": index.content_key,
        "preparation_time_s": preparation_time_s,
        "baseline_cold_wall_time_s": baseline_time_s,
        "cold_index_end_to_end_time_s": indexed_total_time_s,
        "cold_benefit_s": cold_benefit_s,
        "cold_benefit_proven": benefit_proven,
        "indexed_adoption": {
            "parent_schedules": len(indexed_ledger.costs),
            "daily_variant_lookups": indexed_source.lookups,
            "persistence_load_time_s": persistence_load_time_s,
            "ledger_execution_time_s": indexed_ledger_time_s,
            "ledger_content_key": indexed_ledger.to_dict()["content_key"],
            "complete": True,
        },
        "raw_measurement": raw_measurement,
        "oracle_source": "bound deterministic daily-cost cache, read after raw computation",
        "population": {
            "daily_units": len(records),
            "daily_variant_records": len(records) * 3,
            "parent_schedules": len(indexed_ledger.costs),
        },
        "oracle": oracle,
        "fresh_index_root": str(index_out.resolve()),
    }
    evidence["content_key"] = _digest(evidence)
    _verify_before_publication(profile_path, bound, raw_measurement,
                               raw_input_sources)
    _publish(evidence_out, evidence)
    return evidence


DECISION_LEDGER_FIELDS = ("candidate_id", "cost", "daily_unit_ids",
                          "per_variant")
CACHE_LEDGER_FIELDS = ("cache_hits",)


def compare_decision_ledgers(
    indexed: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Compare what the decision is made from, in the ledger's own order.

    A ledger's content key also digests diagnostic cache counters, and an
    indexed source legitimately reports different ones.  Comparing content
    keys would therefore fail a correct index and pass nothing useful, so the
    contract is the DECISION-bearing content -- candidate_id, cost,
    per_variant and daily_unit_ids -- with cache and measurement fields
    reported separately instead of being folded into the verdict.

    Both Phase 5 paths call this one implementation: a second copy is how the
    build path and the resume path start answering different questions.
    """

    def _project(costs, fields):
        return [{field: dict(item).get(field) for field in fields}
                for item in costs]

    indexed_costs = indexed.get("costs", ())
    baseline_costs = baseline.get("costs", ())
    identical = (_project(indexed_costs, DECISION_LEDGER_FIELDS)
                 == _project(baseline_costs, DECISION_LEDGER_FIELDS))
    comparison = {
        "compared_fields": sorted(DECISION_LEDGER_FIELDS),
        "decision_fields_identical": identical,
        "cache_fields_identical": (
            _project(indexed_costs, CACHE_LEDGER_FIELDS)
            == _project(baseline_costs, CACHE_LEDGER_FIELDS)),
        "cache_fields_may_differ": sorted(CACHE_LEDGER_FIELDS),
        "baseline_parents": len(baseline_costs),
        "indexed_parents": len(indexed_costs),
        "basis": ("candidate_id, cost, per_variant and daily_unit_ids in the "
                  "ledger's own order; cache counters describe the source, "
                  "not the price"),
    }
    return identical, comparison


class _ResumeOracleProviders:
    """Bound-oracle identities for an already-written index.

    Reading the oracle needs each daily unit's `cache_identity`, which folds
    in the unit identity, so one provider per unit is required.  The archive's
    verified `ArchiveInputs` are built once per ARCHIVE and passed in, so a
    resume pays archive hashing 30 times rather than 1,950, and no identity
    arithmetic is duplicated here.
    """

    def __init__(self, spec: ClosureSearchSpec, *, runs_root: Path,
                 qualified_demand_manifest: Mapping[str, Any]) -> None:
        self.spec = spec
        self.runs_root = Path(runs_root)
        self.qualified_demand_manifest = qualified_demand_manifest
        self.resolver = MonthlyDemandResolverRunner(
            spec, runs_root=self.runs_root, build_missing=False,
            baseline_trip_duration_p99_s=3600,
            study_provenance_key="subhour-phase5-resume-oracle",
            qualified_demand_manifest=qualified_demand_manifest)
        self.archive_index = _archives_for_build_key(self.runs_root)
        self._inputs: dict[Path, Any] = {}
        # Operation-local and content-derived: one full validation per
        # distinct demand contract, not one per daily unit. A month has 30
        # build keys behind 1,950 units, so the per-unit form re-proved the
        # same 30 archives 1,950 times. Nothing here outlives this object and
        # nothing is keyed on a path, size or mtime.
        self._archive_by_build_key: dict[str, Path] = {}
        self.archives: set[Path] = set()

    def _archive_for(self, schedule: ClosureSchedule) -> Path:
        required = self.resolver._required(schedule)
        cached = self._archive_by_build_key.get(required.build_key)
        if cached is not None:
            return cached
        matches = find_demand_archives(
            self.runs_root, required,
            qualified_manifest=self.qualified_demand_manifest,
            _archive_index=self.archive_index)
        if not matches:
            raise WindowCostIndexError(
                f"no qualified demand archive for build key {required.build_key}")
        archive = Path(matches[0]["archive"]).resolve()
        self._archive_by_build_key[required.build_key] = archive
        return archive

    def identity_for(self, unit_id: str, identity: Mapping[str, Any],
                     schedule: ClosureSchedule) -> Mapping[str, Any]:
        from traffic_sim.simulation.deterministic_disruption import ArchiveInputs

        archive = self._archive_for(schedule)
        self.archives.add(archive)
        inputs = self._inputs.get(archive)
        if inputs is None:
            inputs = ArchiveInputs.from_archive(archive)
            self._inputs[archive] = inputs
        if not isinstance(identity, Mapping) or not identity:
            raise WindowCostIndexError(
                f"daily unit {unit_id} has no identity mapping")
        # The oracle key is the WRITER's identity, not a new one. Phase 4 wrote
        # these entries from providers carrying no unit identity -- verified
        # directly against the bound cache: including `daily_unit` addresses a
        # key nobody ever wrote and reports the oracle as missing. `load`
        # still re-verifies that the stored identity is the one asked for.
        provider = ArchiveDisruptionProvider(
            self.spec, archive=archive, network=None, cache=None,
            inputs=inputs)
        return provider.cache_identity(schedule)

    def provider_identity_for(self, unit_id: str, identity: Mapping[str, Any],
                              schedule: ClosureSchedule) -> Mapping[str, Any]:
        """The provider identity this unit reconstructs from current inputs.

        Compared against the identity stored in the index, so a resume proves
        that the archive, network and costing sources behind every unit are
        still the ones the index was built from -- a matching key SET says
        nothing about that.
        """
        from traffic_sim.simulation.deterministic_disruption import ArchiveInputs

        archive = self._archive_for(schedule)
        inputs = self._inputs.get(archive)
        if inputs is None:
            inputs = ArchiveInputs.from_archive(archive)
            self._inputs[archive] = inputs
        provider = ArchiveDisruptionProvider(
            self.spec, archive=archive, network=None, cache=None,
            inputs=inputs)
        return dict(provider.identity())


def prove_existing_index(
    profile_path: Path,
    *,
    index_path: Path,
    evidence_out: Path,
    evidence_id: str,
    oracle_providers=None,
) -> dict[str, Any]:
    """Prove an already-written index instead of rebuilding it for hours.

    The 2026-09-16 attempt wrote a complete index and proved its oracle, then
    crashed in the adoption replay.  Rebuilding it costs 8 h 41 m, so this
    path consumes the written index -- but only after re-proving every
    binding the builder would have required, and it keeps the index's OWN
    stored preparation time in the cold end-to-end total.  A cheap replay may
    not rewrite history so that WindowCostIndex looks fast.
    """
    bound = _bound_inputs(profile_path)
    profile = bound["profile"]
    spec = bound["spec"]
    ledger = bound["ledger"]
    parents = bound["parents"]
    parent_unit_ids = bound["parent_unit_ids"]
    baseline_time_s = bound["baseline_time_s"]

    index_path = Path(index_path).resolve()
    persisted = load_index(
        index_path,
        expected_daily_units=EXPECTED_DAILY_UNITS,
        expected_variant_records=EXPECTED_VARIANT_RECORDS,
    )
    identity = dict(persisted.bound_identity)
    bindings = profile.get("bindings") or {}
    expected_identity = {
        "search_content_key": str(bound["bound_spec"]["search_content_key"]),
        "ledger_content_key": str(profile.get("ledger_content_key", "")),
        "source_profile_content_key": str(profile.get("content_key", "")),
        "qualified_demand_manifest": dict(bound["qualified_ref"]),
        "policy_content_key": str(
            (bindings.get("policy") or {}).get("content_key", "")),
        "producer_source_manifest": dict(
            bindings.get("producer_source_manifest") or {}),
        "producer_runtime_manifest": dict(
            bindings.get("producer_runtime_manifest") or {}),
    }
    for field, expected in expected_identity.items():
        if identity.get(field) != expected:
            raise WindowCostIndexError(
                f"window cost index {field} does not match this profile")
    if set(persisted.records) != parent_unit_ids:
        raise WindowCostIndexError(
            "window cost index population does not match the ledger units")
    provider_identities = identity.get("provider_identities")
    if not isinstance(provider_identities, Mapping) \
            or set(provider_identities) != parent_unit_ids:
        raise WindowCostIndexError(
            "window cost index lacks a provider identity for every daily unit")
    # Honest, not hidden: this index was written by earlier builder bytes.
    stored_sources = dict(identity.get("raw_input_sources") or {})
    current_sources = {
        relative: sha256_file(ROOT / relative)
        for relative in stored_sources
    }
    builder_source_drift = {
        relative: {"index": stored_sources[relative],
                   "current": current_sources.get(relative)}
        for relative in sorted(stored_sources)
        if stored_sources[relative] != current_sources.get(relative)
    }

    providers = oracle_providers if oracle_providers is not None else \
        _ResumeOracleProviders(
            spec, runs_root=bound["runs_root"],
            qualified_demand_manifest=bound["qualified_manifest"])
    cache = DailyCostCache(bound["cache_root"])
    oracle_started = time.perf_counter()
    oracle_records: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for parent in parents:
        for unit_id, unit_identity, build_schedule in daily_unit_records(
                spec, parent):
            unit_id = str(unit_id)
            if unit_id in seen:
                continue
            seen.add(unit_id)
            schedule = build_schedule()
            # Prove the identity, not merely its presence: the archive,
            # network and costing sources reconstructed for this unit today
            # must be exactly the ones the index recorded, or the oracle
            # record below describes a different world.
            reconstructed = providers.provider_identity_for(
                unit_id, unit_identity, schedule)
            if dict(reconstructed) != dict(provider_identities[unit_id]):
                raise WindowCostIndexError(
                    f"provider identity for unit {unit_id} differs from the "
                    "identity stored in the window cost index")
            stored = cache.load(providers.identity_for(
                unit_id, unit_identity, schedule))
            if stored is None:
                raise WindowCostIndexError(
                    f"bound deterministic oracle is missing unit {unit_id}")
            oracle_records[unit_id] = {
                "schedule_id": schedule.schedule_id,
                "records": [dict(item) for item in stored],
            }
    oracle_read_time_s = time.perf_counter() - oracle_started
    oracle = persisted.compare_oracle(oracle_records)
    if oracle["indexed_variant_records"] != EXPECTED_VARIANT_RECORDS \
            or not oracle["oracle_complete"] or not oracle["field_identical"]:
        raise WindowCostIndexError("resumed index is not oracle-identical")

    indexed_source = _IndexedLedgerSource(spec, persisted)
    replay_started = time.perf_counter()
    indexed_ledger = build_cost_ledger(spec, parents, indexed_source)
    replay_time_s = time.perf_counter() - replay_started
    expected_lookups = sum(
        len(item.get("daily_unit_ids", ())) for item in ledger.get("costs", ()))
    if len(indexed_ledger.costs) != EXPECTED_PARENTS \
            or len(indexed_source.units) != EXPECTED_DAILY_UNITS \
            or indexed_source.lookups != expected_lookups:
        raise WindowCostIndexError(
            f"resumed adoption ledger covered {len(indexed_ledger.costs)} "
            f"parents, {len(indexed_source.units)} distinct daily units and "
            f"{indexed_source.lookups} lookups; expected {EXPECTED_PARENTS}, "
            f"{EXPECTED_DAILY_UNITS} and {expected_lookups}")
    indexed_dict = indexed_ledger.to_dict()

    ledger_identical, ledger_comparison = compare_decision_ledgers(
        indexed_dict, ledger)

    stored_preparation_time_s = float(persisted.preparation_time_s)
    cold_total_s = stored_preparation_time_s + oracle_read_time_s + replay_time_s
    cold_benefit_s = baseline_time_s - cold_total_s
    # Source drift is disqualifying, not merely reportable: an index written
    # by different builder bytes may not be adopted on the strength of a
    # replay performed by today's code.
    adopted = bool(cold_benefit_s > 0 and ledger_identical
                   and not builder_source_drift)
    record = {
        "schema": "subhour_phase5_window_cost_index_resume_v1",
        "kind": "subhour_phase5_window_cost_index_resume",
        "phase": 5,
        "release_evidence": False,
        "status": "ADOPTABLE" if adopted else "NOT_ADOPTED",
        "adopted": False,
        "rebuilt": False,
        "evidence_id": evidence_id,
        "source_profile": str(bound["profile_path"]),
        "source_profile_content_key": profile.get("content_key"),
        "index_path": str(index_path),
        "index_content_key": persisted.content_key,
        "index_sha256": sha256_file(index_path),
        "bound_identity_verified": sorted(expected_identity) + [
            "provider_identities", "daily_unit_population"],
        "builder_source_drift": builder_source_drift,
        "population": {
            "daily_units": len(persisted.records),
            "daily_variant_records": len(persisted.records) * 3,
            "parent_schedules": len(indexed_ledger.costs),
        },
        "adoption": {
            "parent_schedules": len(indexed_ledger.costs),
            "daily_unit_lookups": indexed_source.lookups,
            "distinct_daily_units": len(indexed_source.units),
            "lookup_basis": ("one lookup per parent-to-daily-unit "
                             "relationship, not per variant record"),
        },
        "oracle": oracle,
        "oracle_source": "bound deterministic daily-cost cache, re-read per unit",
        "ledger_identical": ledger_identical,
        "ledger_comparison": ledger_comparison,
        "baseline_ledger_content_key": ledger.get("content_key"),
        "indexed_ledger_content_key": indexed_dict["content_key"],
        "stored_preparation_time_s": stored_preparation_time_s,
        "resume_oracle_read_time_s": oracle_read_time_s,
        "resume_adoption_replay_time_s": replay_time_s,
        "baseline_cold_wall_time_s": baseline_time_s,
        "cold_index_end_to_end_time_s": cold_total_s,
        "cold_benefit_s": cold_benefit_s,
        "cold_benefit_proven": bool(cold_benefit_s > 0),
        "accounting": (
            "the index's own stored preparation time is included: a resume "
            "replay measures adoption, never the cost of producing the index"),
    }
    record["content_key"] = _digest(record)
    _publish(Path(evidence_out), record)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--index-out", type=Path)
    parser.add_argument("--resume-index", type=Path,
                        help="prove this already-written index instead of "
                             "rebuilding it")
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args(argv)
    if (args.index_out is None) == (args.resume_index is None):
        parser.error("pass exactly one of --index-out or --resume-index")
    if args.resume_index is not None:
        result = prove_existing_index(
            args.profile, index_path=args.resume_index,
            evidence_out=args.evidence_out, evidence_id=args.evidence_id)
        print(f"proved Phase 5 index {result['index_content_key']}: "
              f"{result['status']} (benefit {result['cold_benefit_s']:.3f}s)")
        return 0 if result["status"] == "ADOPTABLE" else 1
    result = build_from_profile(
        args.profile, index_out=args.index_out, evidence_out=args.evidence_out,
        evidence_id=args.evidence_id)
    print(f"wrote Phase 5 {result['evidence_id']} ({result['index_content_key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
