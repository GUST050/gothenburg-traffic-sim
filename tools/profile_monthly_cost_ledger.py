"""Profile the exact SUMO-free monthly cost ledger.

The profiler measures only deterministic pricing.  It never starts SUMO and
refuses to claim a full-month profile unless the registered population is
exactly 1,950 daily units x 3 variants and 1,690 parent candidates.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# A profiler is also a real command-line entry point, not only an imported
# test helper.  Make direct execution from any working directory resolve the
# checkout's packages exactly as the other tools do.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.cost_ordered_execution import build_cost_ledger
from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
from tools.product_arm import ProcessCensusUnavailable, ProcessTreeRSSSampler

EXPECTED_DAILY_UNITS = 1950
EXPECTED_VARIANTS = 3
EXPECTED_PARENTS = 1690
PROFILE_SCHEMA = "monthly_cost_ledger_profile_v1"

# A Phase 4 profile is evidence produced by this complete import/runtime
# surface, not just by the four numeric costing files.  Keep this list explicit
# and content-addressed so Phase 5 can reject a profile before opening its
# cache or route archives when the producer changes.
PROFILE_SOURCE_FILES = (
    "tools/profile_monthly_cost_ledger.py",
    "tools/product_arm.py",
    "tools/process_census.py",
    "run_scenario.py",
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/simulation/cost_ordered_execution.py",
    "traffic_sim/simulation/deterministic_disruption.py",
    "traffic_sim/simulation/disruption.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_demand.py",
    "traffic_sim/simulation/metadata.py",
)


def producer_source_manifest() -> dict[str, str]:
    return {path: sha256_file(ROOT / path) for path in PROFILE_SOURCE_FILES}


def producer_runtime_manifest() -> dict[str, str]:
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "profile_schema": PROFILE_SCHEMA,
    }


def capture_producer_binding(
    *,
    bound_spec: Mapping[str, Any] | None = None,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    """Bind the complete Phase 4 producer before expensive work starts."""
    spec = dict(bound_spec) if isinstance(bound_spec, Mapping) else None
    spec_digest = None
    if spec is not None and spec.get("path"):
        spec_digest = sha256_file(Path(str(spec["path"])))
    policy = None
    if policy_path is not None:
        path = Path(policy_path).resolve()
        policy = {
            "path": str(path),
            "sha256": sha256_file(path),
            "content_key": MonthlySearchPolicy.from_dict(json.loads(
                path.read_text(encoding="utf-8"))).content_key,
        }
    return {
        "bound_spec": spec,
        "bound_spec_sha256": spec_digest,
        "producer_source_manifest": producer_source_manifest(),
        "producer_runtime_manifest": producer_runtime_manifest(),
        "policy": policy,
    }


def validate_producer_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of *binding* only if it still describes live bytes.

    A two-hour profile must not hash its sources only after it finishes: a
    concurrent fixer could then make old measurements appear to have been
    produced by new code.  This check runs before measurement, after it, and
    immediately before append-only publication.
    """
    if not isinstance(binding, Mapping):
        raise RuntimeError("Phase 4 producer binding is missing")
    sources = binding.get("producer_source_manifest")
    if not isinstance(sources, Mapping) or dict(sources) != producer_source_manifest():
        raise RuntimeError("Phase 4 producer sources changed during profiling")
    runtime = binding.get("producer_runtime_manifest")
    if not isinstance(runtime, Mapping) or dict(runtime) != producer_runtime_manifest():
        raise RuntimeError("Phase 4 producer runtime changed during profiling")
    bound_spec = binding.get("bound_spec")
    expected_spec_digest = binding.get("bound_spec_sha256")
    if bound_spec is not None:
        if not isinstance(bound_spec, Mapping) or not bound_spec.get("path") \
                or not isinstance(expected_spec_digest, str):
            raise RuntimeError("Phase 4 bound spec binding is incomplete")
        path = Path(str(bound_spec["path"]))
        if not path.is_file() or sha256_file(path) != expected_spec_digest:
            raise RuntimeError("Phase 4 bound spec changed during profiling")
    policy = binding.get("policy")
    if policy is not None:
        if not isinstance(policy, Mapping) or not policy.get("path"):
            raise RuntimeError("Phase 4 policy binding is incomplete")
        path = Path(str(policy["path"]))
        if not path.is_file() or sha256_file(path) != policy.get("sha256"):
            raise RuntimeError("Phase 4 policy changed during profiling")
        content_key = MonthlySearchPolicy.from_dict(json.loads(
            path.read_text(encoding="utf-8"))).content_key
        if content_key != policy.get("content_key"):
            raise RuntimeError("Phase 4 policy content key changed")
    return copy.deepcopy(dict(binding))


def _content_key(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        {key: value for key, value in payload.items() if key != "content_key"},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _publish_evidence(path: Path, record: Mapping[str, Any]) -> None:
    path = Path(path)
    bindings = record.get("bindings") if isinstance(record, Mapping) else None
    validate_producer_binding(
        bindings if isinstance(bindings, Mapping) else {})
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o644)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and no-clobber: unlike replace(),
        # it can never overwrite an append-only evidence path in a race.
        os.link(temporary, path)
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite profile evidence: {path}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _require_fresh_root(path: Path, label: str) -> Path:
    path = Path(path).resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"profile {label} root must be fresh: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value) * (1 if os.uname().sysname == "Darwin" else 1024)


def profile_ledger(
    spec: Any,
    parents: Sequence[Any],
    source: Any,
    *,
    output_root: Path,
    expected_daily_units: int = EXPECTED_DAILY_UNITS,
    expected_variants: int = EXPECTED_VARIANTS,
    expected_parents: int = EXPECTED_PARENTS,
    cache_root: Path | None = None,
    release_root: Path | None = None,
    sumo_start_probe: Callable[[], int] | None = None,
    daily_results_root: Path | None = None,
    release_root_prepared: bool = False,
    evidence_id: str | None = None,
    bound_spec: Mapping[str, Any] | None = None,
    sumo_start_before: int | None = None,
    runs_root: Path | None = None,
    policy_path: Path | None = None,
    producer_binding: Mapping[str, Any] | None = None,
    qualified_demand_manifest: Mapping[str, Any] | None = None,
    qualified_demand_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Run and account for a cold deterministic ledger profile."""
    from traffic_sim.simulation.monthly_demand import validate_qualified_demand_manifest_shape
    if qualified_demand_manifest is None:
        raise ValueError("Phase 4 requires a qualified-demand manifest")
    validate_qualified_demand_manifest_shape(qualified_demand_manifest)
    if qualified_demand_manifest.get("status") != "PASS":
        raise ValueError("Phase 4 requires a passing qualified-demand manifest")
    binding_snapshot = validate_producer_binding(
        producer_binding
        if producer_binding is not None
        else capture_producer_binding(
            bound_spec=bound_spec, policy_path=policy_path))
    output_root = Path(output_root)
    output_root = _require_fresh_root(output_root, "output")
    fresh_roots = {"output": output_root}
    if cache_root is not None:
        fresh_roots["cache"] = _require_fresh_root(cache_root, "cache")
    if release_root is not None:
        fresh_roots["release"] = (
            Path(release_root).resolve() if release_root_prepared
            else _require_fresh_root(release_root, "release"))
    if daily_results_root is not None:
        fresh_roots["daily_results"] = _require_fresh_root(
            daily_results_root, "daily-results")
    roots = list(fresh_roots.values())
    if any(left == right or left in right.parents or right in left.parents
           for index, left in enumerate(roots)
           for right in roots[index + 1:]):
        raise ValueError("profile fresh roots must not overlap")
    if len(parents) != expected_parents:
        raise ValueError(
            f"expected {expected_parents} parents, got {len(parents)}")
    # The cold profile is normally one process, but the contract measures the
    # whole live process tree so a future parser/route worker cannot disappear
    # from the resource record.  RSS completeness is an independent resource
    # gate: an unavailable census must not prevent the deterministic ledger
    # and its Phase 5 timing trigger from being measured.
    rss_sampler = None
    rss_error = None
    try:
        rss_sampler = ProcessTreeRSSSampler(os.getpgrp(), interval_s=0.05).start()
    except ProcessCensusUnavailable as error:
        rss_error = str(error)
    started = time.perf_counter()
    phase_started = started
    progress: list[dict[str, Any]] = []

    def on_progress(phase: str, completed: int, total: int,
                    detail: Mapping[str, Any]) -> None:
        progress.append({
            "phase": phase,
            "completed": completed,
            "total": total,
            "elapsed_s": time.perf_counter() - phase_started,
            "detail": dict(detail),
        })

    sumo_before = sumo_start_before
    sumo_probe_error = None
    if sumo_before is not None and (
            isinstance(sumo_before, bool) or sumo_before < 0):
        raise ValueError("SUMO launch baseline must be a non-negative integer")
    if sumo_before is None and sumo_start_probe is not None:
        try:
            sumo_before = int(sumo_start_probe())
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            sumo_probe_error = str(error)
    try:
        ledger = build_cost_ledger(spec, parents, source, progress=on_progress)
    finally:
        if rss_sampler is None:
            peak_rss_bytes = None
        else:
            try:
                peak_rss_bytes = rss_sampler.stop()
            except ProcessCensusUnavailable as error:
                peak_rss_bytes = None
                rss_error = str(error)
    elapsed = time.perf_counter() - started
    # ``computed_units`` counts parent-to-unit lookups (the same five daily
    # units occur in overlapping parents).  The population gate is the unique
    # daily-unit ledger, so derive it from the bound IDs instead of mistaking
    # 1,690 x 5 lookups for 1,950 units.
    daily_units = len({
        unit_id
        for parent in ledger.costs
        for unit_id in parent.daily_unit_ids
    })
    population_snapshot = getattr(source, "population_snapshot", None)
    observed_population = (dict(population_snapshot())
                           if callable(population_snapshot) else {})
    observed_daily_units = int(observed_population.get(
        "daily_units", daily_units))
    observed_variant_records = observed_population.get("daily_variant_records")
    sumo_after = None
    if sumo_start_probe is not None and sumo_probe_error is None:
        try:
            sumo_after = int(sumo_start_probe())
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            sumo_probe_error = str(error)
    # Keep the legacy boolean shape for callers, but make the accompanying
    # observation explicit: an uninstrumented fake is not evidence and cannot
    # make a real profile PASS.
    sumo_started = False
    if sumo_before is not None and sumo_after is not None:
        if sumo_after < sumo_before:
            sumo_probe_error = "SUMO launch counter moved backwards"
        else:
            # A cold profile is valid only when the runner-owned launch
            # counter was zero on entry and stayed zero.  Treat a non-zero
            # inherited baseline as contaminated too; otherwise preparation
            # could have launched SUMO before the profiler started measuring.
            sumo_started = sumo_before > 0 or (sumo_after - sumo_before) > 0
    if expected_variants != 3:
        raise ValueError("the exact profile contract requires three variants")
    ledger_path = output_root / "cost-ledger.json"
    ledger_path.write_text(json.dumps(ledger.to_dict(), indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    # All three roots started empty in the CLI.  Account for their complete
    # growth, including resolver/setup files, rather than reporting only the
    # published output directory and hiding cache or release writes.
    size = sum(_tree_size(path) for path in fresh_roots.values())
    # The provider reports exclusive timers at the actual seams: route XML
    # parsing/grouping, shortest-path pricing and report aggregation.  Parent
    # aggregation is timed by IndependentDailyCostSource; the final sort is
    # measured here against the exact ledger population.  Missing telemetry is
    # a failed profile, never a reason to copy the enclosing elapsed time into
    # several buckets.
    source_timing = getattr(source, "timing_snapshot", None)
    measured = dict(source_timing()) if callable(source_timing) else {}
    cache_snapshot = (dict(source.cache_snapshot())
                      if callable(getattr(source, "cache_snapshot", None))
                      else {})
    lookup_count = int(sum(len(item.daily_unit_ids)
                           for item in ledger.costs))
    memory_hits = int(cache_snapshot.get(
        "memory_cache_hits", ledger.cache_hits))
    # Do not turn absent telemetry into a fabricated cold-miss count.  The
    # real deterministic source exposes this field; a legacy test double
    # remains explicitly incomplete instead of producing the contradictory
    # old shape (8,450 lookups, 0 hits, 1,950 misses).
    memory_misses = int(cache_snapshot.get(
        "memory_cache_misses", ledger.cache_misses))
    disk_hits = int(cache_snapshot.get(
        "disk_cache_hits", ledger.disk_cache_hits))
    disk_misses = int(cache_snapshot.get(
        "disk_cache_misses", ledger.disk_cache_misses))
    disk_lookups = int(cache_snapshot.get(
        "disk_cache_lookups", disk_hits + disk_misses))
    cache_accounting_complete = (
        lookup_count == memory_hits + memory_misses
        and disk_lookups == disk_hits + disk_misses
        and memory_hits >= 0 and memory_misses >= 0
        and disk_hits >= 0 and disk_misses >= 0
    )
    required_phases = (
        "xml_parse", "route_vehicle_grouping", "shortest_path_detour",
        "window_aggregation", "parent_aggregation_sorting")
    phase_timing_complete = all(phase in measured for phase in required_phases)
    if not phase_timing_complete:
        # Small unit-test/fake sources predate the telemetry protocol. Keep
        # their contract useful without presenting a fabricated five-way
        # decomposition as a real Phase 4 measurement.
        measured = {phase: 0.0 for phase in required_phases}
        measured["parent_aggregation_sorting"] = elapsed
    sort_started = time.perf_counter()
    sorted(ledger.costs, key=lambda item: item.cost.sort_key)
    measured["parent_aggregation_sorting"] += time.perf_counter() - sort_started
    variant_population_complete = (
        observed_variant_records is not None
        and int(observed_variant_records)
        == expected_daily_units * expected_variants
    )
    reported_variant_records = (
        int(observed_variant_records)
        if observed_variant_records is not None
        else daily_units * expected_variants
    )
    population_complete = (
        observed_daily_units == expected_daily_units
        and variant_population_complete
        and len(ledger.costs) == expected_parents
    )
    phases = {
        phase: {"wall_time_s": round(max(0.0, float(measured[phase])), 6),
                "measured_by": "exclusive provider or ledger timer"}
        for phase in required_phases
    }
    resource_complete = peak_rss_bytes is not None
    sumo_observation_complete = (
        sumo_start_probe is not None and sumo_probe_error is None
        and sumo_before == 0 and sumo_after == 0
        and sumo_started is False
    )
    # Keep the observed launch population separate from the boolean gate.  A
    # non-zero delta is a valid numeric measurement even though it makes the
    # cold profile INCONCLUSIVE; collapsing it to the legacy zero default
    # would let the final report claim that no SUMO started.
    sumo_attempts = (
        None if (sumo_before is None or sumo_after is None
                 or sumo_probe_error is not None)
        else int(sumo_after - sumo_before))
    # Re-read every bound byte after the measurement.  Drift invalidates the
    # attempt before profile.json or validation evidence can be published.
    binding_snapshot = validate_producer_binding(binding_snapshot)
    qualified_binding = {
        "evidence_id": qualified_demand_manifest["evidence_id"],
        "content_key": qualified_demand_manifest["content_key"],
    }
    if qualified_demand_manifest_path is not None:
        qualified_path = Path(qualified_demand_manifest_path).resolve()
        if not qualified_path.is_file():
            raise ValueError("qualified-demand manifest binding path is missing")
        loaded_qualified = json.loads(qualified_path.read_text(encoding="utf-8"))
        if loaded_qualified != dict(qualified_demand_manifest):
            raise ValueError("qualified-demand manifest binding bytes drifted")
        qualified_binding.update({
            "path": str(qualified_path),
            "sha256": sha256_file(qualified_path),
        })
    record = {
        "schema": PROFILE_SCHEMA,
        "kind": "monthly_cost_ledger_profile",
        "release_evidence": False,
        "qualified_demand_manifest": qualified_binding,
        "cold": True,
        "execution_started": True,
        "sumo_started": sumo_started,
        "sumo_zero_launch_gate": sumo_observation_complete
        and sumo_started is False,
        "sumo_start_observation": {
            "before": sumo_before,
            "after": sumo_after,
            "delta": (None if sumo_before is None or sumo_after is None
                      else sumo_after - sumo_before),
            "measured": sumo_observation_complete,
            "error": sumo_probe_error,
        },
        "sumo_attempts": sumo_attempts,
        "population": {
            "daily_units": observed_daily_units,
            "variants_per_daily_unit": expected_variants,
            "daily_variant_records": reported_variant_records,
            "parents": len(parents),
        },
        "population_complete": population_complete,
        "variant_population_complete": variant_population_complete,
        "phases": phases,
        "progress_events": progress,
        "cache": {
            "root": str(fresh_roots.get("cache", "")),
            "lookups": lookup_count,
            "hits": memory_hits,
            "misses": memory_misses,
            "memory_cache_hits": memory_hits,
            "memory_cache_misses": memory_misses,
            "disk_cache_lookups": disk_lookups,
            "disk_cache_hits": disk_hits,
            "disk_cache_misses": disk_misses,
            "unique_unit_misses": int(daily_units),
            "accounting": "parent-to-daily-unit lookups are memory hits or misses; disk lookups are disk hits or misses",
            "accounting_consistent": cache_accounting_complete,
        },
        "wall_time_s": elapsed,
        "peak_rss_bytes": peak_rss_bytes,
        "process_tree_rss_complete": resource_complete,
        "process_tree_rss_error": rss_error,
        "disk_growth_bytes": size,
        "fresh_roots": {name: str(path) for name, path in fresh_roots.items()},
        "ledger_content_key": ledger.to_dict()["content_key"],
        "runs_root": str(Path(runs_root).resolve() if runs_root is not None
                          else getattr(source, "runs_root", ROOT / "runs")),
        "bindings": {
            "search_content_key": str(getattr(spec, "content_key", "")),
            "bound_spec": binding_snapshot["bound_spec"],
            "bound_spec_sha256": binding_snapshot["bound_spec_sha256"],
            "provider_identity": dict(ledger.provider_identity),
            "costing_sources": dict(
                ledger.provider_identity.get("costing_sources", {})
                if isinstance(ledger.provider_identity, Mapping) else {}
            ),
            "producer_source_manifest": binding_snapshot[
                "producer_source_manifest"],
            "producer_runtime_manifest": binding_snapshot[
                "producer_runtime_manifest"],
            "policy": binding_snapshot["policy"],
        },
        # The Phase 5 timing trigger is deliberately independent of the
        # process-tree RSS completeness gate.  A sandbox may deny `ps` while
        # still producing a complete, trustworthy SUMO-free ledger timing.
        # The profile itself remains INCONCLUSIVE until RSS is complete.
        "phase_5_window_cost_index_needed": (
            bool(elapsed > 600 or elapsed > 0.20 * (55 * 60))
            if population_complete and phase_timing_complete
            and sumo_observation_complete else None),
        "phase_5_decision": (
            "NOT_TRIGGERED"
            if population_complete and phase_timing_complete
            and sumo_observation_complete
            and not bool(elapsed > 600 or elapsed > 0.20 * (55 * 60))
            else "TRIGGERED"
            if population_complete and phase_timing_complete
            and sumo_observation_complete
            else None),
        "phase_timing_complete": phase_timing_complete,
        "status": ("PASS" if population_complete and phase_timing_complete
                    and cache_accounting_complete
                    and resource_complete and sumo_observation_complete
                    and sumo_started is False
                    else "INCONCLUSIVE"),
        "reason": (None if (population_complete and phase_timing_complete
                            and cache_accounting_complete
                            and resource_complete and sumo_observation_complete)
                   else "complete 1950/5850/1690 actual population, exclusive "
                   "phase telemetry, measured no-SUMO evidence and a trusted "
                   "process-tree RSS census are required before the Phase 5 "
                   "trigger"),
    }
    if evidence_id is not None:
        record["evidence_id"] = str(evidence_id)
    if bound_spec is not None:
        record["bound_spec"] = dict(bound_spec)
    record["content_key"] = _content_key(record)
    (output_root / "profile.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spec", type=Path,
                        help="bound ClosureSearchSpec JSON (required for a real run)")
    parser.add_argument("--runs-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "runs")
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--evidence-out", type=Path,
                        help="append-only validation record for this profile")
    parser.add_argument("--evidence-id", default=None)
    parser.add_argument("--qualified-demand-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.spec is None:
        raise SystemExit("--spec is required for a real cold profile")
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from tools import product_arm
    from traffic_sim.simulation.monthly_demand import validate_qualified_demand_manifest_shape

    qualified_manifest = json.loads(
        args.qualified_demand_manifest.read_text(encoding="utf-8"))
    validate_qualified_demand_manifest_shape(qualified_manifest)

    spec = ClosureSearchSpec.from_dict(
        json.loads(args.spec.read_text(encoding="utf-8")))
    parents = tuple(iter_closure_schedules(spec))
    if len(parents) != EXPECTED_PARENTS:
        raise SystemExit(
            f"bound spec must enumerate {EXPECTED_PARENTS} parents, got {len(parents)}")
    output_root = Path(args.out)
    bound_spec = {
        "path": str(Path(args.spec).resolve()),
        "search_id": spec.search_id,
        "search_content_key": spec.content_key,
    }
    policy_path = ROOT / "validation" / "monthly_search_policy_v3.json"
    producer_binding = capture_producer_binding(
        bound_spec=bound_spec, policy_path=policy_path)
    # Keep the measured output directory empty until the profile itself is
    # published.  The demand release and deterministic cache are separate
    # fresh siblings so their setup cannot make the output-root freshness
    # check ambiguous.
    cache_root = output_root.parent / (output_root.name + "-daily-cost-cache")
    release_root = (Path(args.release_root) if args.release_root is not None
                    else output_root.parent / (output_root.name + "-releases"))
    _require_fresh_root(cache_root, "cache")
    _require_fresh_root(release_root, "release")
    _require_fresh_root(output_root, "output")
    daily_results_root = output_root.parent / (output_root.name + "-daily-results")
    _require_fresh_root(daily_results_root, "daily-results")
    runner, _screen_builder, source = product_arm.build_arm(
        spec, cost_ordered=True, runs_root=args.runs_root,
        release_root=release_root, daily_cost_cache=cache_root,
        daily_results_cache_root=daily_results_root,
        study_provenance_key="subhour-cold-ledger-profile",
        objective_method="closure_cost_v1", seed_workers=1, daily_workers=1,
        max_active_sumo_slots=1,
        qualified_demand_manifest=qualified_manifest)
    try:
        def sumo_start_probe() -> int:
            snapshot = getattr(runner, "timing_snapshot", None)
            if not callable(snapshot):
                raise RuntimeError("runner lacks measured SUMO launch telemetry")
            raw = snapshot()
            if not isinstance(raw, Mapping):
                raise RuntimeError("runner SUMO launch telemetry is invalid")
            records = raw.get("exact_launch_records")
            if not isinstance(records, list):
                raise RuntimeError("runner lacks exact SUMO launch records")
            return len(records)

        # Bind the no-SUMO baseline before preparation.  The old path only
        # probed after ``runner.prepare`` and therefore could not distinguish
        # a profile that had already launched SUMO during setup.
        sumo_before_prepare = sumo_start_probe()
        if sumo_before_prepare != 0:
            raise RuntimeError("cold profile runner already has SUMO launches")
        runner.prepare(parents)
        sumo_after_prepare = sumo_start_probe()
        if sumo_after_prepare != 0:
            raise RuntimeError(
                "cold profile preparation launched SUMO; refusing profile")

        record = profile_ledger(spec, parents, source, output_root=output_root,
                                cache_root=cache_root,
                                release_root=release_root,
                                sumo_start_probe=sumo_start_probe,
                                daily_results_root=daily_results_root,
                                release_root_prepared=True,
                                evidence_id=args.evidence_id,
                                bound_spec=bound_spec,
                                sumo_start_before=sumo_after_prepare,
                                runs_root=args.runs_root,
                                policy_path=policy_path,
                                producer_binding=producer_binding,
                                qualified_demand_manifest=qualified_manifest,
                                qualified_demand_manifest_path=(
                                    args.qualified_demand_manifest))
        (output_root / "profile.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if args.evidence_out is not None:
            _publish_evidence(args.evidence_out, record)
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    print(f"wrote {output_root / 'profile.json'} ({record['wall_time_s']:.3f}s)")
    return 0


if __name__ == "__main__":
    main()
