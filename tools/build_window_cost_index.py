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
from traffic_sim.simulation.deterministic_disruption import (
    ArchiveDisruptionProvider,
    DailyCostCache,
    NetworkCostModel,
    closure_seconds,
)
from traffic_sim.simulation.disruption import (
    build_parsed_window_cost_index,
    parse_route_vehicles,
)
from traffic_sim.simulation.independent_daily import daily_unit_records
from traffic_sim.simulation.monthly_demand import (
    MonthlyDemandResolverRunner,
    find_demand_archives,
    validate_qualified_demand_manifest_shape,
)
from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    load_index,
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
        self._identity = dict(index.bound_identity.get("provider_identity", {}))

    def identity(self) -> Mapping[str, Any]:
        return dict(self._identity)

    def parent_cost(self, parent: ClosureSchedule) -> ParentCost:
        daily_records = []
        unit_ids = []
        for unit_id, schedule, _build in daily_unit_records(self.spec, parent):
            row = self.index.lookup(str(unit_id), schedule.schedule_id)
            self.lookups += 1
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
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite Phase 5 evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


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


def _raw_index_records(
    spec: ClosureSearchSpec,
    *,
    runs_root: Path,
    oracle_cache: DailyCostCache,
    qualified_demand_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Compute index records from route XML and return a separate oracle.

    ``ArchiveDisruptionProvider`` is deliberately constructed with
    ``cache=None``.  It parses/group routes and performs interval aggregation
    directly from the immutable archive inputs.  The returned oracle is read
    only after that computation and is used solely for the field-by-field
    comparison.  Keeping these maps separate prevents a warmed cache lookup
    from becoming the implementation under test.
    """
    resolver = MonthlyDemandResolverRunner(
        spec,
        runs_root=Path(runs_root),
        build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-phase5-raw-index",
        qualified_demand_manifest=qualified_demand_manifest,
    )
    units: dict[str, tuple[dict[str, Any], ClosureSchedule, Path]] = {}
    for parent in iter_closure_schedules(spec):
        for unit_id, identity, build_schedule in daily_unit_records(spec, parent):
            schedule = build_schedule()
            existing = units.get(unit_id)
            if existing is not None:
                if existing[0] != identity or existing[1].to_dict() != schedule.to_dict():
                    raise WindowCostIndexError(
                        f"daily unit identity collision for {unit_id}")
                continue
            required = resolver._required(schedule)
            matches = find_demand_archives(
                Path(runs_root), required,
                qualified_manifest=qualified_demand_manifest)
            if not matches:
                raise WindowCostIndexError(
                    f"no immutable demand archive for daily unit {unit_id}")
            units[unit_id] = (identity, schedule,
                              Path(matches[0]["archive"]).resolve())

    if len(units) != EXPECTED_DAILY_UNITS:
        raise WindowCostIndexError(
            f"raw input population has {len(units)} units, expected "
            f"{EXPECTED_DAILY_UNITS}")

    network = NetworkCostModel()
    indexed: dict[str, dict[str, Any]] = {}
    oracle: dict[str, dict[str, Any]] = {}
    provider_identities: dict[str, dict[str, Any]] = {}
    timings = {
        "xml_parse": 0.0,
        "route_vehicle_grouping": 0.0,
        "shortest_path_detour": 0.0,
        "window_aggregation": 0.0,
    }
    # Parse each immutable archive/variant exactly once and build one reusable
    # exact window-cost index.  Its crossing-event and unique-OD detour tables
    # are then queried for every daily unit; no window repeats XML grouping or
    # shortest-path pricing.
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
            # Keep small injected test doubles useful.  Production providers
            # always expose ArchiveInputs and take the indexed path below.
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
        identity, schedule, archive = units[unit_id]
        provider = provider_by_archive[archive]
        provider_identity = getattr(provider, "identity", None)
        if callable(provider_identity):
            provider_identities[unit_id] = dict(provider_identity())
        if parsed_by_archive[archive]:
            inputs = provider.inputs
            closed = set(spec.directed_edges)
            closures = closure_seconds(
                spec, schedule, epoch=inputs.epoch,
                duration_s=inputs.duration_s)
            raw_records = tuple({
                "demand_variant": variant,
                **index_by_archive[archive][variant].disruption(
                    closures,
                    timing=lambda phase, elapsed: timings.__setitem__(
                        phase, timings.get(phase, 0.0) + float(elapsed)),
                )
            } for variant in ("q10", "q50", "q90"))
        else:
            # Compatibility branch for the deliberately tiny provider test
            # double above.  It is not reachable for the real CLI.
            raw_records = provider.disruption(schedule)
        indexed[unit_id] = {
            "schedule_id": schedule.schedule_id,
            "records": [dict(item) for item in raw_records],
        }
        cache_identity = provider.cache_identity(schedule)
        expected = oracle_cache.load(cache_identity)
        if expected is None:
            raise WindowCostIndexError(
                f"independent deterministic oracle is missing for {unit_id}")
        oracle[unit_id] = {
            "schedule_id": schedule.schedule_id,
            "records": [dict(item) for item in expected],
        }
        # Indexed production timings are recorded at the parse/routing seams
        # above.  Compatibility providers expose their own cumulative timers.
        if not parsed_by_archive[archive]:
            for phase, elapsed in provider.timing_snapshot().items():
                if phase in timings:
                    timings[phase] += float(elapsed)
    return indexed, oracle, {
        "daily_units": len(indexed),
        "daily_variant_records": len(indexed) * 3,
        "timings": timings,
        "raw_input_algorithm": "ArchiveDisruptionProvider(cache=None)",
        "raw_input_strategy": (
            "parse_each_archive_variant_once; precompute crossing events and "
            "unique-OD detours; aggregate each daily window by lookup"),
        "structural_reuse": {
            "archive_variant_indexes": sum(
                len(value) for value in index_by_archive.values()),
            "window_queries": len(indexed) * 3,
            "reused_route_vehicle_grouping": True,
            "reused_unique_route_detours": True,
        },
        "provider_identities": provider_identities,
    }


def build_from_profile(
    profile_path: Path,
    *,
    index_out: Path,
    evidence_out: Path,
    evidence_id: str,
) -> dict[str, Any]:
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
    if len(parents) != 1690:
        raise WindowCostIndexError(
            f"bound spec has {len(parents)} parents, expected 1690")
    started = time.perf_counter()
    records, oracle_records, raw_measurement = _raw_index_records(
        spec, runs_root=runs_root, oracle_cache=DailyCostCache(cache_root),
        qualified_demand_manifest=qualified_manifest)
    preparation_time_s = time.perf_counter() - started
    baseline_time_s = float(profile.get("wall_time_s", 0.0))
    if baseline_time_s <= 0:
        raise WindowCostIndexError(
            "Phase 4 profile has no positive cold wall-time baseline")
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
        "raw_input_sources": {
            relative: sha256_file(ROOT / relative)
            for relative in (
                "tools/build_window_cost_index.py",
                "traffic_sim/simulation/window_cost_index.py",
            )
        },
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
    if len(indexed_ledger.costs) != 1690 \
            or indexed_source.lookups != EXPECTED_VARIANT_RECORDS:
        raise WindowCostIndexError(
            "indexed adoption ledger did not cover all 1,690 parents and 5,850 lookups")
    indexed_total_time_s = (
        preparation_time_s + persistence_load_time_s + indexed_ledger_time_s)
    cold_benefit_s = baseline_time_s - indexed_total_time_s
    if cold_benefit_s <= 0:
        raise WindowCostIndexError(
            "WindowCostIndex has no measured cold end-to-end benefit")
    evidence = {
        "schema": "subhour_phase5_window_cost_index_evidence_v1",
        "kind": "subhour_phase5_window_cost_index",
        "phase": 5,
        "release_evidence": False,
        "status": "PASS",
        "evidence_id": evidence_id,
        "source_profile": str(profile_path),
        "source_profile_content_key": profile.get("content_key"),
        "bound_identity": bound_identity,
        "index_content_key": index.content_key,
        "preparation_time_s": preparation_time_s,
        "baseline_cold_wall_time_s": baseline_time_s,
        "cold_index_end_to_end_time_s": indexed_total_time_s,
        "cold_benefit_s": cold_benefit_s,
        "cold_benefit_proven": True,
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
    _publish(evidence_out, evidence)
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--index-out", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args(argv)
    result = build_from_profile(
        args.profile, index_out=args.index_out, evidence_out=args.evidence_out,
        evidence_id=args.evidence_id)
    print(f"wrote Phase 5 {result['evidence_id']} ({result['index_content_key']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
