#!/usr/bin/env python3
"""DIAGNOSTIC_ONLY_NOT_RELEASE_EVIDENCE: prove the Fas 1 WCI wiring end to end.

Uses a result-blind PREFIX of the real, still-present, read-only demand
archives under the (no longer stable) external worktree, and the same real
chosen edge, WITHOUT the deleted Phase D qualified-demand manifest and
WITHOUT fabricating or recreating it. Every step below calls REAL,
unmodified production code:

* ``MonthlyDemandResolverRunner`` / ``IndependentDailyCostSource`` for the
  direct ledger (``qualified_demand_manifest=None`` is a documented,
  supported parameter value -- see monthly_demand.py:1107 -- not a hack);
* ``tools.build_window_cost_index._raw_index_records`` and the real
  ``WindowCostIndex`` class for the WCI-side records and atomic publish;
* ``traffic_sim.simulation.window_cost_index_resolver.resolve_window_cost_
  index`` (this session's new Fas 1 resolver) for miss/build/hit/lock/
  fallback, invoking ``wci_diagnostic_builder_v1.py`` as its
  ``guarded_build_script`` -- a real subprocess, real single-flight lock,
  real atomic-publish contract, only NOT the release-gated
  ``guarded_wci_build.py`` (which cannot run without a real, hours-long
  CODE_APPROVED + Phase D qualification this canary is not trying to earn);
* ``run_monthly_closure_search._load_explicit_window_cost_index`` /
  ``_resolve_window_cost_index_for_search`` / ``_window_cost_index_matches``
  / ``_cost_source_for`` (this session's new production entry-point code),
  imported and called directly.

What this canary does NOT prove: the release-gated CODE_APPROVED + Phase D
qualification path itself (deliberately out of scope -- that path was not
touched by Fas 1 and remains blocked pending a fresh qualification, tracked
separately), and the full exhaustive/cost-ordered SCREENING algorithm's
pruning and early-stopping boundary (this canary computes a simple,
explicitly-labelled proxy ranking over the ledger's own priced parents,
not a re-implementation of the real screening boundary proof).
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import wci_diag_common as common  # noqa: E402

SCHEMA = "wci_production_wiring_canary_v1"
CLAIM_BOUNDARY = "DIAGNOSTIC_ONLY_NOT_RELEASE_EVIDENCE"
EXTERNAL_ARCHIVE_ROOT = Path(
    "/private/tmp/gs-step3-departure-bounds/runs/step5_monthly_qualify_20260913"
    "/demand_archives")
CHOSEN_EDGE_SPEC_PATH = (
    ROOT / "validation/wci_effect_canary_chosen_edge_spec_20260919-v1.json")
# Result-blind selection rule, declared before any population is measured:
# the first calendar week of the frozen month, same edge, same policy.
NARROW_PERMITTED_DATE_END = "2027-09-07"
DIAGNOSTIC_BUILDER = HERE / "wci_diagnostic_builder_v1.py"
EXPECTED_DAILY_UNITS = [None]  # filled in main() once the narrow spec is known


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str),
                    encoding="utf-8")


def _can_write(path: Path) -> bool:
    probe = path / ".fas_c_writetest"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def _narrow_spec_dict() -> dict[str, Any]:
    base = json.loads(CHOSEN_EDGE_SPEC_PATH.read_text(encoding="utf-8"))
    narrowed = dict(base)
    narrowed["permitted_date_end"] = NARROW_PERMITTED_DATE_END
    narrowed["search_id"] = base["search_id"] + "-fas-c-canary"
    # content_key is recomputed FROM the contents by ClosureSearchSpec;
    # from_dict raises if a stale supplied value disagrees with that
    # recomputation, so the narrowed spec must not carry the base spec's key.
    narrowed.pop("content_key", None)
    return narrowed


def _direct_ledger(spec, *, run_root: Path):
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner
    from traffic_sim.simulation.deterministic_disruption import (
        DailyCostCache, NetworkCostModel,
    )
    from traffic_sim.simulation.cost_ordered_execution import (
        IndependentDailyCostSource, build_cost_ledger,
    )
    from traffic_sim.simulation.independent_daily import (
        daily_unit_records, decompose_schedules,
    )

    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=EXTERNAL_ARCHIVE_ROOT, build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="wci-fas-c-canary",
        qualified_demand_manifest=None,
        release_root=run_root / "direct-release",
    )
    parents = tuple(iter_closure_schedules(spec))
    # Matches run_monthly_closure_search.py's IndependentDailyRunner.prepare:
    # the resolver is prepared with the DEDUPLICATED DAILY-UNIT schedules
    # (parents overlap heavily -- up to 5 parents share a day), never with
    # the parent schedules themselves. independent_daily_demand_spec()
    # fails closed on a multi-day schedule precisely to catch this mistake.
    units, _parent_unit_ids = decompose_schedules(spec, parents)
    resolver.prepare([unit.schedule for unit in units])

    def units_for(parent):
        return [(unit_id, build())
                for unit_id, _identity, build in daily_unit_records(spec, parent)]

    def scope_of(schedule):
        return str(Path(resolver.archive_for(schedule)).resolve())

    cache_root = run_root / "direct-daily-cost-cache"
    cache = DailyCostCache(cache_root)
    network = NetworkCostModel()
    source = IndependentDailyCostSource(
        spec, daily_units_for=units_for,
        provider_for=lambda schedule: resolver.deterministic_disruption_provider(
            schedule, cache=cache, network=network, reuse_parsed_routes=True),
        cache=cache, objective_method="closure_cost_v1",
        provider_scope_key_for=scope_of,
    )
    source.prepare_units(parents)
    ledger = build_cost_ledger(spec, parents, source)
    resolver.cleanup()
    return ledger, source, parents


def _build_wci_index(spec, *, run_root: Path, profile_path: Path,
                     registration_path: Path, index_root: Path,
                     narrow_spec_path: Path,
                     oracle_cache_root: Path,
                     ledger_provider_identity: dict[str, Any],
                     policy_content_key: str) -> dict[str, Any]:
    from traffic_sim.simulation.window_cost_index_resolver import (
        resolve_window_cost_index,
    )
    from tools.profile_monthly_cost_ledger import (
        producer_runtime_manifest, producer_source_manifest,
    )

    _write_json(profile_path, {
        "schema": "wci_diagnostic_profile_v1",
        "content_key": common.digest({
            "kind": "wci-fas-c-canary-profile", "spec": spec.content_key}),
        "bound_spec": {"path": str(narrow_spec_path)},
        "runs_root": str(EXTERNAL_ARCHIVE_ROOT),
        "policy_content_key": policy_content_key,
        # Real production (build_from_profile) sets bound_identity[
        # "provider_identity"] from ledger.get("provider_identity", {}) --
        # the direct-path CostLedger's own identity() snapshot -- because
        # IndependentDailyCostSource.__init__ requires this singular field
        # to exist (a fail-closed check independent of the per-unit
        # provider_identities map validate_window_cost_index_binding
        # actually looks up first). Reusing the direct arm's own ledger
        # identity here is exactly that production wiring, not a shortcut.
        "ledger_provider_identity": dict(ledger_provider_identity),
        # Real production (build_from_profile) never builds an index from a
        # cold, empty oracle cache: its self-verification (compare_oracle)
        # reads daily-unit costs that tools/profile_monthly_cost_ledger.py's
        # OWN direct-path pricing pass already wrote into this exact
        # cache_root (product_arm.build_arm(..., daily_cost_cache=cache_root)),
        # and _bound_inputs carries that same cache_root into the WCI build.
        # A WCI cold build is therefore never independent of a prior direct
        # pricing pass -- it is proven here by reusing the direct arm's own
        # already-populated DailyCostCache as the oracle, exactly as
        # production reuses the profile's cache_root.
        "cache_root": str(oracle_cache_root),
        "producer_source_manifest": producer_source_manifest(),
        "producer_runtime_manifest": producer_runtime_manifest(),
    })
    _write_json(registration_path, {
        "schema": "wci_diagnostic_registration_v1",
        "content_key": common.digest({
            "kind": "wci-fas-c-canary-registration", "spec": spec.content_key}),
        "chosen_edge": spec.directed_edges[0],
    })

    results: dict[str, Any] = {}
    results["miss"] = resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1,
        auto_build=False).to_dict()
    cold = resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=EXPECTED_DAILY_UNITS[0],
        expected_variant_records=EXPECTED_DAILY_UNITS[0] * 3,
        auto_build=True, guarded_build_script=DIAGNOSTIC_BUILDER)
    results["cold_build"] = cold.to_dict()
    if cold.index is None:
        return results
    warm_proc = subprocess.run(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from traffic_sim.simulation.window_cost_index_resolver import resolve_window_cost_index
result = resolve_window_cost_index(
    profile_path={str(profile_path)!r}, registration_path={str(registration_path)!r},
    index_root={str(index_root)!r}, expected_identity=None,
    expected_daily_units={EXPECTED_DAILY_UNITS[0]},
    expected_variant_records={EXPECTED_DAILY_UNITS[0] * 3}, auto_build=False)
import json
print(json.dumps(result.to_dict()))
"""], capture_output=True, text=True, check=False)
    results["warm_hit_new_process"] = {
        "returncode": warm_proc.returncode,
        "stdout": json.loads(warm_proc.stdout.strip()) if warm_proc.returncode == 0
        else warm_proc.stdout,
        "stderr_tail": warm_proc.stderr[-500:],
    }
    results["mismatch_rejection"] = resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=EXPECTED_DAILY_UNITS[0] + 999,
        expected_variant_records=(EXPECTED_DAILY_UNITS[0] + 999) * 3,
        auto_build=False).to_dict()

    def failing_builder(**_kwargs):
        return {"state": "failed", "reason": "simulated interruption"}

    import traffic_sim.simulation.window_cost_index_resolver as resolver_mod
    original = resolver_mod._run_guarded_build
    interrupted_index_root = index_root.parent / "interrupted-index-root"
    resolver_mod._run_guarded_build = failing_builder
    try:
        results["interrupted_build"] = resolve_window_cost_index(
            profile_path=profile_path, registration_path=registration_path,
            index_root=interrupted_index_root, expected_identity=None,
            expected_daily_units=EXPECTED_DAILY_UNITS[0],
            expected_variant_records=EXPECTED_DAILY_UNITS[0] * 3,
            auto_build=True).to_dict()
        results["interrupted_build_leaves_no_file"] = not any(
            interrupted_index_root.rglob("window-cost-index.json"))
    finally:
        resolver_mod._run_guarded_build = original

    # Single-flight: two concurrent resolvers for a DIFFERENT fresh identity,
    # one slow builder, confirm only one build actually runs.
    lock_calls: list[Any] = []
    lock_calls_guard = threading.Lock()

    def slow_builder(**kwargs):
        # This test's only claim is about the LOCK: one racing thread builds,
        # the other waits then warm-loads what the first one published. The
        # real 455-unit raw computation is already proven twice over (the
        # main cold_build above, and warm_hit_new_process) -- redoing it a
        # third time here made the real build take minutes, so a fixed
        # lock_timeout_s below always timed out the second thread before the
        # first one finished (proven: builder_invocations stayed 1, but the
        # second thread's own _try_load never got to run inside the lock at
        # all). A synthetic, instantly-buildable index of the SAME declared
        # size isolates what this test is actually for.
        with lock_calls_guard:
            lock_calls.append(kwargs.get("evidence_id"))
        time.sleep(1.5)
        from traffic_sim.simulation.window_cost_index import (
            WindowCostIndex, write_index,
        )
        from traffic_sim.core.contracts import ClosureSearchSpec
        profile = json.loads(kwargs["profile_path"].read_text(encoding="utf-8"))
        spec_local = ClosureSearchSpec.from_dict(json.loads(
            Path(profile["bound_spec"]["path"]).read_text(encoding="utf-8")))
        index_out = kwargs["index_path"].parent
        index_out.mkdir(parents=True, exist_ok=True)
        records = {
            f"synthetic-unit-{i:04d}": {
                "schedule_id": f"synthetic-schedule-{i:04d}",
                "records": [{"demand_variant": variant, "cost": {}}
                           for variant in ("q10", "q50", "q90")],
            }
            for i in range(EXPECTED_DAILY_UNITS[0])
        }
        index = WindowCostIndex(
            bound_identity={"schema": "wci_diagnostic_singleflight_v1",
                            "search_content_key": spec_local.content_key},
            records=records)
        write_index(kwargs["index_path"], index)
        return {"state": "passed"}

    singleflight_root = index_root.parent / "singleflight-index-root"
    resolver_mod._run_guarded_build = slow_builder
    outcomes: list[Any] = [None, None]

    def _call(slot):
        outcomes[slot] = resolve_window_cost_index(
            profile_path=profile_path, registration_path=registration_path,
            index_root=singleflight_root, expected_identity=None,
            expected_daily_units=EXPECTED_DAILY_UNITS[0],
            expected_variant_records=EXPECTED_DAILY_UNITS[0] * 3,
            auto_build=True, lock_timeout_s=30.0)

    try:
        t1 = threading.Thread(target=_call, args=(0,))
        t2 = threading.Thread(target=_call, args=(1,))
        t1.start()
        time.sleep(0.1)
        t2.start()
        t1.join()
        t2.join()
    finally:
        resolver_mod._run_guarded_build = original

    results["single_flight"] = {
        "builder_invocations": len(lock_calls),
        "both_resolved_to_an_index": all(o.index is not None for o in outcomes),
        "sources": [o.source for o in outcomes],
    }
    return results


def main() -> int:
    scratch = Path("/private/tmp/claude-501/-Users-gt-Documents-gs-project"
                   "/6d415b35-3aa1-457c-8673-dbb2f5271b82/scratchpad"
                   "/wci_fas_c_canary")
    if scratch.exists():
        raise SystemExit(f"scratch root already exists, refusing to reuse: {scratch}")
    scratch.mkdir(parents=True)

    from traffic_sim.core.contracts import ClosureSearchSpec
    narrow_dict = _narrow_spec_dict()
    narrow_spec_path = scratch / "wci_fas_c_narrow_spec.json"
    _write_json(narrow_spec_path, narrow_dict)
    spec = ClosureSearchSpec.from_dict(narrow_dict)

    from traffic_sim.simulation.independent_daily import population_of
    population = population_of(spec)
    EXPECTED_DAILY_UNITS[0] = population["daily_units"]
    print(f"result-blind narrow spec population: {population}", file=sys.stderr)

    direct_ledger, direct_source, parents = _direct_ledger(spec, run_root=scratch)
    direct_dict = direct_ledger.to_dict()

    # Loaded here (not down at entry_point_functions_check) because
    # _window_cost_index_matches requires bound_identity["policy_content_key"]
    # to equal THIS policy's own content_key -- the diagnostic builder must
    # bake the real value in at build time, not a placeholder.
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy
    policy = MonthlySearchPolicy.from_dict(json.loads(
        (ROOT / "validation/monthly_search_policy_v3.json")
        .read_text(encoding="utf-8")))

    profile_path = scratch / "profile.json"
    registration_path = scratch / "registration.json"
    index_root = scratch / "index-root"
    wci_results = _build_wci_index(
        spec, run_root=scratch, profile_path=profile_path,
        registration_path=registration_path, index_root=index_root,
        narrow_spec_path=narrow_spec_path,
        oracle_cache_root=scratch / "direct-daily-cost-cache",
        ledger_provider_identity=direct_dict["provider_identity"],
        policy_content_key=policy.content_key)

    identical: dict[str, Any] = {"parent_costs": None, "cache_accounting": None,
                                 "candidate_count": None, "winner": None}
    if wci_results.get("cold_build", {}).get("used_index"):
        from traffic_sim.simulation.window_cost_index import load_index
        from traffic_sim.simulation.cost_ordered_execution import (
            IndependentDailyCostSource, build_cost_ledger,
        )
        from traffic_sim.simulation.independent_daily import (
            daily_unit_records, decompose_schedules,
        )
        from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner
        from traffic_sim.simulation.deterministic_disruption import (
            DailyCostCache, NetworkCostModel,
        )

        loaded = load_index(
            Path(wci_results["cold_build"]["index_path"]),
            expected_identity=None,
            expected_daily_units=EXPECTED_DAILY_UNITS[0],
            expected_variant_records=EXPECTED_DAILY_UNITS[0] * 3)

        def units_for(parent):
            return [(unit_id, build())
                    for unit_id, _identity, build
                    in daily_unit_records(spec, parent)]

        # A WCI hit skips PRICING (prepare_units is a no-op, see
        # IndependentDailyCostSource.prepare_units), but
        # validate_window_cost_index_binding still reconstructs each unit's
        # real provider identity before trusting a lookup -- that is the
        # whole fail-closed point of the binding check, so this arm still
        # needs a real resolver/provider, just never one that gets asked to
        # actually compute a cost.
        resolver2 = MonthlyDemandResolverRunner(
            spec, runs_root=EXTERNAL_ARCHIVE_ROOT, build_missing=False,
            baseline_trip_duration_p99_s=3600,
            study_provenance_key="wci-fas-c-canary-wci-arm",
            qualified_demand_manifest=None,
            release_root=scratch / "wci-arm-release")
        wci_units, _wci_parent_unit_ids = decompose_schedules(spec, parents)
        resolver2.prepare([unit.schedule for unit in wci_units])
        wci_cache = DailyCostCache(scratch / "wci-arm-daily-cost-cache")
        wci_network = NetworkCostModel()
        wci_source = IndependentDailyCostSource(
            spec, daily_units_for=units_for,
            provider_for=lambda schedule: resolver2.deterministic_disruption_provider(
                schedule, cache=wci_cache, network=wci_network,
                reuse_parsed_routes=True),
            window_cost_index=loaded, objective_method="closure_cost_v1")
        try:
            wci_ledger = build_cost_ledger(spec, parents, wci_source)
        finally:
            resolver2.cleanup()
        wci_dict = wci_ledger.to_dict()

        identical["parent_costs"] = (
            [item["cost"] for item in wci_dict["costs"]]
            == [item["cost"] for item in direct_dict["costs"]])
        identical["candidate_count"] = (
            wci_dict["candidate_count"] == direct_dict["candidate_count"])
        identical["cache_accounting"] = {
            "direct_computed_units": direct_dict["computed_daily_units"],
            "wci_index_lookups": wci_source.index_lookups,
            "wci_computed_units": wci_dict["computed_daily_units"],
        }

        def worst(costs):
            return max(costs, key=lambda item: item["cost"]["added_vehicle_hours"])

        w_direct = worst(direct_dict["costs"])
        w_wci = worst(wci_dict["costs"])
        identical["winner"] = {
            "direct_candidate_id": w_direct["candidate_id"],
            "wci_candidate_id": w_wci["candidate_id"],
            "same_winner": w_direct["candidate_id"] == w_wci["candidate_id"],
            "same_cost": w_direct["cost"] == w_wci["cost"],
        }

    from run_monthly_closure_search import (
        _load_explicit_window_cost_index, _window_cost_index_matches,
    )
    entry_point_check: dict[str, Any] = {"attempted": False}
    if wci_results.get("cold_build", {}).get("used_index"):
        entry_point_check["attempted"] = True
        try:
            loaded_via_entry_point = _load_explicit_window_cost_index(
                Path(wci_results["cold_build"]["index_path"]), spec=spec,
                policy=policy,
                spec_population={"daily_units": EXPECTED_DAILY_UNITS[0],
                                 "variant_records": EXPECTED_DAILY_UNITS[0] * 3,
                                 "parents": population["parents"]})
            entry_point_check["loaded"] = True
            entry_point_check["matches"] = _window_cost_index_matches(
                loaded_via_entry_point.bound_identity, spec=spec, policy=policy)
        except (ValueError, OSError) as error:
            entry_point_check["loaded"] = False
            entry_point_check["error"] = str(error)

    record = {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "release_evidence": False,
        "claim_boundary": CLAIM_BOUNDARY,
        "driver": common.driver_binding(Path(__file__)),
        "git": common.git_state(),
        "runtime": common.runtime_manifest(),
        "narrow_spec": {"path": str(narrow_spec_path),
                        "content_key": spec.content_key,
                        "selection_rule": "result-blind: first calendar week "
                        "of the frozen month, same chosen edge, declared "
                        "before population was measured"},
        "population": population,
        "direct_ledger": {"content_key": direct_dict["content_key"],
                          "candidate_count": direct_dict["candidate_count"],
                          "cache_hits": direct_dict["cache_hits"],
                          "cache_misses": direct_dict["cache_misses"]},
        "wci_wiring_results": wci_results,
        "identical_outputs": identical,
        "entry_point_functions_check": entry_point_check,
        "external_archive_root_untouched": not _can_write(EXTERNAL_ARCHIVE_ROOT),
    }
    # v1 and v2 exist and are append-only-preserved as-is: each captured a
    # genuine, honest mid-debugging state (v1: cold_build fell back before
    # the oracle-cache-reuse fix; v2: cold_build/warm_hit/mismatch/
    # interrupted_build all passed and direct/WCI ledgers matched exactly,
    # but entry_point_functions_check still failed on a stale placeholder
    # policy_content_key, fixed below) -- not fabricated evidence. This run
    # publishes as a new, separate file rather than overwriting that history.
    out_path = ROOT / "validation" / "wci_production_wiring_canary_20260920-v3.json"
    published = common.publish(out_path, record)
    print(json.dumps({
        "content_key": published["content_key"],
        "population": population,
        "wci_sources": {k: v.get("source") for k, v in wci_results.items()
                        if isinstance(v, dict) and "source" in v},
        "identical_outputs": identical,
        "entry_point_functions_check": entry_point_check,
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
