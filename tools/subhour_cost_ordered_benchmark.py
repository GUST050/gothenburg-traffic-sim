"""Freeze and run the Phase 3 bounded cost-ordered comparison.

The freezer deliberately has no reference to benchmark outcome files.  It
enumerates only closure specs and demand/network metadata, orders the complete
eligible list by the registered SHA-256 tuple rule, and writes a new
append-only registration.  The runner then uses the already reviewed
same-code-path comparison: both arms are cost ordered and only
``disable_early_stop`` differs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.cost_ordered_benchmark as base  # noqa: E402
from traffic_sim.core.fingerprint import sha256_file  # noqa: E402

SCHEMA = "subhour_cost_ordered_bounded_registration_v1"
OUTCOME_SCHEMA = "subhour_cost_ordered_bounded_outcome_v1"
CASE_COUNT = 8
MIN_EDGES = 4
MIN_PERIODS = 2
POLICY_PATH = "validation/monthly_search_policy_v3.json"
DEFAULT_REGISTRATION = ROOT / "validation" / (
    "subhour_bounded_sumo_registration_20260831-v13.json")
DEFAULT_OUTCOME = ROOT / "validation" / (
    "subhour_bounded_sumo_outcome_20260831-v13.json")
_ARCHIVE_INDEX_CACHE: dict[str, list[Path]] = {}
_VALID_ARCHIVE_CACHE: set[tuple[str, str]] = set()
_METADATA_ARCHIVE_KEYS: dict[str, set[str]] = {}
_ARCHIVE_MATCH_CACHE: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
_DEMAND_SOURCE_FINGERPRINTS: dict[str, Any] | None = None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _key(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _with_content_key(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an immutable outcome envelope with a self-checking digest."""
    result = copy.deepcopy(dict(payload))
    result["content_key"] = _key({
        key: value for key, value in result.items() if key != "content_key"
    })
    return result


def _publish_json_no_clobber(
        path: Path, record: Mapping[str, Any], *, label: str) -> None:
    """Atomically publish one append-only JSON artifact.

    A killed evidence process may leave workspaces incomplete, but it must
    never leave a half-written file at the canonical registration/outcome
    path.  A same-directory temporary plus hard link is atomic and, unlike
    replace(), fails if another writer already published that path.
    """
    path = Path(path)
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
        os.link(temporary, path)
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite {label}: {path}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _relative(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(Path(path).resolve())


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _registered_root_paths(record: Mapping[str, Any], data_root: Path) -> dict[str, Path]:
    roots = record.get("fresh_roots") or {}
    return {
        name: (Path(value) if Path(value).is_absolute()
               else Path(data_root) / str(value)).resolve()
        for name, value in roots.items()
    }


def _execution_marker_path(record: Mapping[str, Any], data_root: Path) -> Path:
    workspace = _registered_root_paths(record, data_root).get(
        "workspace_namespace")
    if workspace is None:
        raise ValueError("registration has no workspace root")
    return workspace / ".subhour-registration-binding.json"


def _write_execution_marker(record: Mapping[str, Any], data_root: Path) -> None:
    marker = _execution_marker_path(record, data_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    _publish_json_no_clobber(marker, {
        "schema": "subhour_execution_binding_v1",
        "evidence_id": str(record["evidence_id"]),
        "registration_content_key": str(record["content_key"]),
    }, label="execution binding")


def _verify_execution_marker(record: Mapping[str, Any], data_root: Path) -> bool:
    marker = _execution_marker_path(record, data_root)
    if not marker.is_file():
        return False
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("registered execution binding is malformed")
    expected = {
        "schema": "subhour_execution_binding_v1",
        "evidence_id": str(record["evidence_id"]),
        "registration_content_key": str(record["content_key"]),
    }
    if value != expected:
        raise ValueError("registered roots belong to another execution")
    return True


def _period(item: Mapping[str, Any]) -> str:
    spec = item["spec"]
    # The archive library contains several forecast envelopes under one
    # demand-build family.  Bind the actual calendar demand period (source +
    # year-month), not a guessed outcome label or a legacy build id.
    date_text = str(item.get("work_dates", [""])[0])
    return f"{spec.get('source')}:{date_text[:7]}"


def outcome_free_tuple(item: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """The exact tuple frozen before any benchmark outcome is read."""
    spec = item["spec"]
    band = spec["permitted_daily_band"]
    return (
        _period(item),
        str(spec["directed_edges"][0]),
        str(item["work_dates"][0]),
        f"{band['earliest_start']}-{band['latest_end']}",
        str(item["search_content_key"]),
    )


def _metadata_inventory(runs_root: Path) -> list[dict[str, Any]]:
    """Build the complete eligible inventory without opening outcome files."""
    # These helpers read demand_meta/routes and the topology survivability
    # screen only.  They do not import or glob validation/*outcome* records.
    items: list[dict[str, Any]] = []
    for spec in base.discovered_specs(Path(runs_root)):
        profile = base._structural_profile(spec)
        if int(profile["candidate_count"]) < 6:
            continue
        # Archive availability is an outcome-free input property.  Exclude a
        # metadata case that cannot be executed before hashing/selection, so
        # the frozen rule never needs an outcome-driven or post-selection
        # replacement.
        if not _spec_has_metadata_archives(spec, Path(runs_root)):
            continue
        item = {
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "spec": spec.to_dict(),
            "candidate_count": int(profile["candidate_count"]),
            "unique_daily_unit_count": int(profile["unique_daily_unit_count"]),
            "work_dates": list(profile["work_dates"]),
            "work_dates_with_metadata": list(profile["work_dates"]),
        }
        item["demand_period"] = _period(item)
        item["selection_tuple"] = list(outcome_free_tuple(item))
        item["selection_sha256"] = _key(outcome_free_tuple(item))
        items.append(item)
    return sorted(items, key=lambda item: item["selection_sha256"])


def _spec_has_metadata_archives(spec: Any, runs_root: Path) -> bool:
    """Cheap outcome-blind availability screen; full validation happens later."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation.independent_daily import daily_unit_records
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner

    index_key = str(Path(runs_root).resolve())
    available = _METADATA_ARCHIVE_KEYS.get(index_key)
    if available is None:
        global _DEMAND_SOURCE_FINGERPRINTS
        if _DEMAND_SOURCE_FINGERPRINTS is None:
            from traffic_sim.demand.source_identity import demand_source_fingerprints
            _DEMAND_SOURCE_FINGERPRINTS = demand_source_fingerprints(ROOT)
        available = set()
        for archive in sorted(Path(runs_root).glob("demand-*")):
            metadata_path = archive / "demand_meta.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                build_key = metadata.get("demand_build_key")
                fingerprint = metadata.get("build_fingerprint") or {}
                if (isinstance(build_key, str) and build_key
                        and fingerprint.get("source_files")
                        == _DEMAND_SOURCE_FINGERPRINTS
                        and int(metadata.get("n_variants", 0)) == 3):
                    available.add(build_key)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        _METADATA_ARCHIVE_KEYS[index_key] = available
    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=Path(runs_root), build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-registration-selection")
    required = set()
    for parent in iter_closure_schedules(spec):
        for _unit_id, _identity, build_schedule in daily_unit_records(spec, parent):
            required.add(resolver._required(build_schedule()).build_key)
    return required <= available


def _metadata_archives_for_spec(spec: Any, runs_root: Path) -> dict[str, dict[str, Any]] | None:
    """Resolve candidate archives from metadata without validating outcomes."""
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation.independent_daily import daily_unit_records
    from traffic_sim.simulation.monthly_demand import (
        find_demand_archives,
        MonthlyDemandResolverRunner,
    )

    resolver = MonthlyDemandResolverRunner(
        spec, runs_root=Path(runs_root), build_missing=False,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="subhour-registration-selection")
    required: dict[str, Any] = {}
    for parent in iter_closure_schedules(spec):
        for _unit_id, _identity, build_schedule in daily_unit_records(spec, parent):
            item = build_schedule()
            demand = resolver._required(item)
            required[demand.build_key] = demand
    index_key = str(Path(runs_root).resolve())
    result: dict[str, dict[str, Any]] = {}
    for key, demand in sorted(required.items()):
        # Bind a succeeded, immutable current-source archive.  A metadata-only
        # lexicographic lookup can select a stale historical directory even
        # when a fresh archive for the same build key is present.  This
        # validator reads demand/archive metadata and source fingerprints, not
        # benchmark outcomes, so selection remains outcome-blind while the
        # executable registration is self-consistent.
        match_key = (str(Path(runs_root).resolve()), demand.build_key)
        matches = _ARCHIVE_MATCH_CACHE.get(match_key)
        if matches is None:
            matches = find_demand_archives(Path(runs_root), demand)
            _ARCHIVE_MATCH_CACHE[match_key] = tuple(matches)
        if not matches:
            return None
        archive = Path(matches[0]["archive"])
        routes = {variant: archive / filename
                  for variant, filename in base.VARIANT_FILENAMES.items()}
        result[key] = {
            "archive": str(archive.resolve()),
            "epoch_sim": f"{demand.start_date}T00:00:00",
            "n_intervals": demand.days * 96,
            "demand_build_spec": demand.to_dict(),
            "routes": {variant: {"path": str(path.resolve()),
                                  "sha256": sha256_file(path)}
                       for variant, path in sorted(routes.items())},
            "demand_meta_sha256": sha256_file(archive / "demand_meta.json"),
        }
    return result


def select_cases(runs_root: Path = base.DEFAULT_RUNS_ROOT) -> dict[str, Any]:
    """Select eight cases with four edges and two demand periods."""
    eligible = _metadata_inventory(Path(runs_root))
    edges = sorted({str(item["spec"]["directed_edges"][0])
                    for item in eligible})
    periods = sorted({str(item["demand_period"]) for item in eligible})
    if len(edges) < MIN_EDGES or len(periods) < MIN_PERIODS:
        raise ValueError("metadata cannot provide four edges and two periods")
    required_edges = edges[:MIN_EDGES]
    required_periods = periods[:MIN_PERIODS]
    required_strata = {(period, edge) for period in required_periods
                       for edge in required_edges}
    chosen: list[dict[str, Any]] = []
    chosen_keys: set[str] = set()
    chosen_strata: set[tuple[str, str]] = set()
    # The eligible list is already SHA ordered.  Walk it once: this makes the
    # first member of every required stratum, and the final fill, follow the
    # same canonical order instead of introducing a second period/edge order.
    for item in eligible:
        stratum = (item["demand_period"], item["spec"]["directed_edges"][0])
        if stratum not in required_strata or stratum in chosen_strata:
            continue
        chosen.append(copy.deepcopy(item))
        chosen_keys.add(item["search_content_key"])
        chosen_strata.add(stratum)
    if chosen_strata != required_strata:
        missing = sorted(required_strata - chosen_strata)
        raise ValueError(f"missing eligible stratum(s): {missing}")
    for item in eligible:
        if len(chosen) == CASE_COUNT:
            break
        if item["search_content_key"] in chosen_keys:
            continue
        chosen.append(copy.deepcopy(item))
        chosen_keys.add(item["search_content_key"])
    if len(chosen) != CASE_COUNT:
        raise ValueError(f"only {len(chosen)} eligible cases are available")
    selected_ids = [item["search_content_key"] for item in chosen]
    return {
        "rule": (
            "eligible metadata only; sort by sha256(canonical((demand_period, "
            "directed_edge, date, window, search_content_key))) "
            "and take the first member of each lexicographically smallest "
            "four-edge x two-period stratum, then fill in that same order"),
        "reads_outcomes": False,
        "eligible": eligible,
        "eligible_list_digest": _key(eligible),
        "selected_ids": selected_ids,
        "selected": chosen,
        "distinct_edges": sorted({item["spec"]["directed_edges"][0]
                                   for item in chosen}),
        "distinct_periods": sorted({item["demand_period"] for item in chosen}),
    }


def _source_digests() -> dict[str, str]:
    # The paired arms are built by product_arm and ultimately execute the
    # complete import closure sealed by the base benchmark.  Keep the
    # subhour orchestrator itself in the seal too: changing how it binds,
    # compares, or publishes either arm changes the evidence contract.
    names = tuple(sorted(set(base.SEMANTIC_SOURCES) | {
        "tools/subhour_cost_ordered_benchmark.py",
    }))
    return {name: sha256_file(ROOT / name) for name in names}


def build_registration(
    runs_root: Path = base.DEFAULT_RUNS_ROOT,
    *,
    data_root: Path = ROOT,
    registration_path: Path = DEFAULT_REGISTRATION,
    evidence_id: str = "subhour-bounded-sumo-v1",
) -> dict[str, Any]:
    selection = select_cases(runs_root)
    selected: list[dict[str, Any]] = []
    archives: dict[str, Any] = {}
    # `select_cases` is the only selector.  Archive resolution is an input
    # binding check for those exact IDs; it is never allowed to substitute a
    # preferred period, edge, or another metadata candidate after the rule has
    # been frozen.
    selected_items = selection["selected"]
    if len(selected_items) != CASE_COUNT:
        raise ValueError("frozen selection did not produce the required cases")
    for item in selected_items:
        resolved = _metadata_archives_for_spec(
            base.ClosureSearchSpec.from_dict(item["spec"]), Path(runs_root))
        if resolved is None:
            raise ValueError(
                "the deterministic selected case has no complete metadata "
                f"archive: {item['search_content_key']}")
        archives.update(copy.deepcopy(resolved))
    for index, item in enumerate(selected_items):
        case = {key: copy.deepcopy(item[key]) for key in (
            "search_id", "search_content_key", "spec", "candidate_count",
            "unique_daily_unit_count", "work_dates", "demand_period",
            "selection_tuple", "selection_sha256")}
        case["case_id"] = f"{evidence_id}-{index + 1:02d}-{item['selection_sha256'][:12]}"
        selected.append(case)
    data_root = Path(data_root).resolve()
    roots = {
        "workspace_namespace": f"runs/{evidence_id}/workspaces",
        "daily_cost_cache": f"runs/{evidence_id}/daily-cost-cache",
        "output_namespace": f"runs/{evidence_id}/outputs",
    }
    selected_ids = [str(item["search_content_key"]) for item in selected]
    selected_edges = sorted({str(item["spec"]["directed_edges"][0])
                             for item in selected})
    selected_periods = sorted({str(item["demand_period"]) for item in selected})
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": "subhour_bounded_sumo_registration",
        "evidence_id": evidence_id,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "release_evidence": False,
        "reads_outcomes": False,
        "selection": {
            "rule": selection["rule"],
            "eligible_list_digest": selection["eligible_list_digest"],
            "eligible_count": len(selection["eligible"]),
            "selected_ids": selected_ids,
            "distinct_edges": selected_edges,
            "distinct_periods": selected_periods,
            "selected_case_count": len(selected),
        },
        "selected_cases": selected,
        "archives": archives,
        "data_root": str(data_root),
        "runs_root": str(Path(runs_root).resolve()),
        "policy": {"path": POLICY_PATH,
                   "sha256": sha256_file(ROOT / POLICY_PATH)},
        "sources": _source_digests(),
        "runtime": base.sumo_runtime_identity(data_root),
        "network": {
            "path": str(data_root / "sumo" / "net.net.xml"),
            "sha256": sha256_file(data_root / "sumo" / "net.net.xml"),
        },
        "network_metadata": {
            "path": str(data_root / "sumo" / "network_metadata.json"),
            "sha256": sha256_file(data_root / "sumo" / "network_metadata.json"),
        },
        "arms": {
            "shared_kernel": "run_cost_ordered_execution",
            "only_allowed_difference": "disable_early_stop",
            "cost_ordered": {"disable_early_stop": False},
            "ordered_exhaustive": {"disable_early_stop": True},
        },
        "fixtures": {
            "backfill": {"declared": True, "symmetric": True,
                         "basis": "fixed registered case prefix",
                         "application": "synthetic hard-failure before next starter"},
            "no_detour": {"declared": True, "symmetric": True,
                          "basis": "deterministic pre-SUMO gate",
                          "application": "identical ledger disqualification"},
            "dense_boundary": {"declared": True, "symmetric": True,
                               "basis": "fixed cost-order tuple",
                               "application": "identical adjacent-band prefix"},
            "restart_cancel": {"declared": True, "symmetric": True,
                               "application": "interrupt after first pilot and cancel queued work"},
        },
        "caps": {
            "active_seconds": 3300,
            "suite_active_seconds": 3300,
            "restart_timeout_seconds": 120,
            "attempts_per_case": 10000,
            # The per-case value is per arm.  The suite value also reserves
            # the interrupted and resumed restart probe for every case.
            "attempts_per_suite": CASE_COUNT * 4 * 10000,
            "peak_rss_bytes": 8 * 1024**3,
            "disk_growth_bytes": 20 * 1024**3,
            "suite_disk_growth_bytes": 20 * 1024**3,
            "seed_workers": 1,
            "daily_workers": 1,
            "max_active_sumo_slots": 1,
        },
        "fresh_roots": roots,
        "gates": {
            "minimum_cases": CASE_COUNT,
            "minimum_edges": MIN_EDGES,
            "minimum_periods": MIN_PERIODS,
            "minimum_exact_attempt_reduction": 0.30,
            "minimum_active_time_reduction": 0.30,
            "resource_regression_allowed": False,
        },
        "outcome_record": _relative(Path(registration_path).resolve().with_name(
            f"subhour_bounded_sumo_outcome_{evidence_id}.json")),
        "claim_boundary": "bounded diagnostic evidence; no product activation",
    }
    body = {key: value for key, value in record.items()
            if key not in {"content_key", "registered_at"}}
    record["content_key"] = _key(body)
    return record


def verify_registration(record: Mapping[str, Any], *, root: Path = ROOT,
                        require_fresh_roots: bool = True) -> None:
    if record.get("schema") != SCHEMA or record.get("reads_outcomes") is not False:
        raise ValueError("registration is not the expected outcome-blind schema")
    body = {key: value for key, value in record.items()
            if key not in {"content_key", "registered_at"}}
    if record.get("content_key") != _key(body):
        raise ValueError("registration content key does not match body")
    selection = record.get("selection") or {}
    selected = record.get("selected_cases") or []
    if len(selected) < CASE_COUNT or len(selection.get("selected_ids", [])) != len(selected):
        raise ValueError("registration does not contain the required cases")
    selected_ids = [str(item.get("search_content_key")) for item in selected]
    if selected_ids != [str(item) for item in selection["selected_ids"]] \
            or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("registration selected IDs are not canonical")
    data_root = Path(record.get("data_root", root)).resolve()
    runs_root = Path(record.get("runs_root", data_root / "runs")).resolve()
    recomputed_selection = select_cases(runs_root)
    if selected_ids != [str(item) for item in recomputed_selection["selected_ids"]]:
        raise ValueError("registration selected IDs do not follow the frozen rule")
    if selection.get("eligible_list_digest") != recomputed_selection["eligible_list_digest"]:
        raise ValueError("registration eligible-list digest drifted while building")
    if len(selection.get("distinct_edges", [])) < MIN_EDGES:
        raise ValueError("registration does not span four directed edges")
    if len(selection.get("distinct_periods", [])) < MIN_PERIODS:
        raise ValueError("registration does not span two demand periods")
    if (record.get("arms") or {}).get("only_allowed_difference") != "disable_early_stop":
        raise ValueError("registration changes more than the early-stop flag")
    if (record.get("arms") or {}).get("cost_ordered", {}).get(
            "disable_early_stop") is not False or (
            record.get("arms") or {}).get("ordered_exhaustive", {}).get(
                "disable_early_stop") is not True:
        raise ValueError("registration arm flags are not the registered pair")
    expected_rule = (
        "eligible metadata only; sort by sha256(canonical((demand_period, "
        "directed_edge, date, window, search_content_key))) and take the "
        "first member of each lexicographically smallest four-edge x two-period "
        "stratum, then fill "
        "in that same order")
    if selection.get("rule") != expected_rule:
        raise ValueError("registration selection rule is not the five-field rule")
    fixture_names = ("backfill", "no_detour", "dense_boundary", "restart_cancel")
    fixtures = record.get("fixtures") or {}
    for name in fixture_names:
        fixture = fixtures.get(name)
        if not isinstance(fixture, Mapping) or fixture.get("declared") is not True \
                or fixture.get("symmetric") is not True \
                or not fixture.get("application"):
            raise ValueError(f"fixture {name} is not declared and symmetric")
    caps = record.get("caps") or {}
    required_caps = (
        "active_seconds", "suite_active_seconds", "restart_timeout_seconds",
        "attempts_per_case", "attempts_per_suite", "peak_rss_bytes",
        "disk_growth_bytes", "suite_disk_growth_bytes",
    )
    if any(isinstance(caps.get(name), bool)
           or not isinstance(caps.get(name), (int, float))
           or float(caps[name]) <= 0 for name in required_caps):
        raise ValueError("registration resource caps are incomplete")
    if float(caps["suite_active_seconds"]) > float(caps["active_seconds"]) * len(selected):
        raise ValueError("suite active-time cap exceeds per-case bound")
    if int(caps["attempts_per_suite"]) < int(caps["attempts_per_case"]) * 4:
        raise ValueError("suite launch cap does not cover paired restart scope")
    roots = record.get("fresh_roots") or {}
    historical_root = re.compile(r"(?:^|[-_/])v[1-5](?:$|[-_/])")
    if any(historical_root.search(str(value)) for value in roots.values()):
        raise ValueError("registration reuses a historical v1-v5 root")
    if runs_root != Path(record.get("runs_root", runs_root)).resolve():
        raise ValueError("registration runs root is not canonical")
    recomputed = select_cases(runs_root)
    if recomputed["eligible_list_digest"] != selection.get("eligible_list_digest"):
        raise ValueError("eligible metadata inventory drift")
    if [str(item) for item in selection["selected_ids"]] != [
            str(item) for item in recomputed["selected_ids"]]:
        raise ValueError("selected IDs do not conform to the frozen rule")
    eligible_by_id = {
        str(item["search_content_key"]): item for item in recomputed["eligible"]}
    for item in selected:
        selected_id = str(item.get("search_content_key"))
        expected = eligible_by_id.get(selected_id)
        if expected is None:
            raise ValueError("selected case is absent from eligible inventory")
        if tuple(item.get("selection_tuple", ())) != outcome_free_tuple(expected) \
                or item.get("selection_sha256") != expected["selection_sha256"]:
            raise ValueError("selected case tuple is not outcome-free canonical data")
    expected_sources = set(base.SEMANTIC_SOURCES) | {
        "tools/subhour_cost_ordered_benchmark.py",
    }
    sources = record.get("sources") or {}
    if set(sources) != expected_sources:
        missing = sorted(expected_sources - set(sources))
        extra = sorted(set(sources) - expected_sources)
        raise ValueError(
            "registration executable source manifest is incomplete: "
            f"missing={missing}, extra={extra}")
    for relative, expected in sources.items():
        path = Path(root) / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"source drift: {relative}")
    policy = record.get("policy") or {}
    policy_path = Path(policy.get("path", ""))
    if not policy_path.is_absolute():
        policy_path = root / policy_path
    if not policy_path.is_file() or sha256_file(policy_path) != policy.get("sha256"):
        raise ValueError("policy drift")
    try:
        runtime = base.sumo_runtime_identity(data_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"runtime binding unavailable: {error}") from error
    if runtime != record.get("runtime"):
        raise ValueError("runtime binding drift")
    for bound_key in ("network", "network_metadata"):
        bound = record[bound_key]
        path = Path(bound["path"])
        if not path.is_absolute():
            path = Path(root) / path
        if not path.is_file() or sha256_file(path) != bound["sha256"]:
            raise ValueError(f"{bound_key} drift")
    for build_key, binding in (record.get("archives") or {}).items():
        archive = Path(binding.get("archive", ""))
        if not archive.is_absolute():
            archive = data_root / archive
        meta = archive / "demand_meta.json"
        if not meta.is_file() or sha256_file(meta) != binding.get(
                "demand_meta_sha256"):
            raise ValueError(f"demand archive metadata drift: {build_key}")
        for variant, route_binding in (binding.get("routes") or {}).items():
            route = Path(route_binding.get("path", ""))
            if not route.is_absolute():
                route = archive / route
            if not route.is_file() or sha256_file(route) != route_binding.get(
                    "sha256"):
                raise ValueError(
                    f"demand archive route drift: {build_key}/{variant}")
    roots = record.get("fresh_roots") or {}
    if set(roots) != {"workspace_namespace", "daily_cost_cache", "output_namespace"}:
        raise ValueError("registration fresh roots are incomplete")
    resolved_roots = []
    for name, relative in roots.items():
        path = Path(relative)
        if not path.is_absolute():
            path = data_root / path
        path = path.resolve()
        if any(path == other or other in path.parents for other in resolved_roots):
            raise ValueError(f"fresh roots overlap: {name}")
        if require_fresh_roots and path.exists() and any(path.iterdir()):
            raise ValueError(f"fresh root is not empty: {name}")
        resolved_roots.append(path)


def write_registration(path: Path, record: Mapping[str, Any]) -> None:
    _publish_json_no_clobber(
        path, record, label="frozen registration")


def _verify_registration_for_execution(
        registration: Mapping[str, Any], *, data_root: Path,
        require_fresh_roots: bool) -> None:
    """Verify immutable bindings while distinguishing start from resume.

    The compatibility fallback is only for small injected test doubles that
    still expose the old two-argument verifier.  Production always uses the
    explicit freshness flag, so populated roots are accepted only after the
    registration-owned execution marker has been checked.
    """
    try:
        verify_registration(
            registration, root=ROOT, require_fresh_roots=require_fresh_roots)
    except TypeError as error:
        if "require_fresh_roots" not in str(error):
            raise
        verify_registration(registration, root=ROOT)


def _fixture_application(registration: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bound, symmetric fixture controls for one paired case.

    The real pair is deliberately not allowed to invent fixtures from measured
    outcomes.  These controls are therefore a small immutable test matrix:
    both arms receive the same pre-SUMO disqualification/backfill and boundary
    inputs, while the restart/cancel probe interrupts the same first-pilot
    cursor in each arm.  The returned record is included in the outcome so a
    reviewer can see that the labels were actually applied, rather than merely
    declared in the registration.
    """
    from traffic_sim.core.closure_calendar import iter_closure_schedules

    fixtures = registration.get("fixtures") or {}
    names = ("backfill", "no_detour", "dense_boundary", "restart_cancel")
    applied = {}
    for name in names:
        item = fixtures.get(name)
        if not isinstance(item, Mapping) or item.get("declared") is not True \
                or item.get("symmetric") is not True:
            raise ValueError(f"fixture {name} is not applicable to both arms")
        applied[name] = {
            "application": str(item["application"]),
            "case_id": str(case["case_id"]),
            "arms": ("cost_ordered", "ordered_exhaustive"),
            "same_inputs": True,
        }
    schedules = list(iter_closure_schedules(
        base.ClosureSearchSpec.from_dict(case["spec"])))
    if len(schedules) < 4:
        raise ValueError("fixture matrix requires four deterministic candidates")
    ids = [schedule.schedule_id for schedule in schedules[:4]]
    return {
        "case_id": str(case["case_id"]),
        "arm_inputs_identical": True,
        "backfill": {"candidate_id": ids[0]},
        "no_detour": {"candidate_id": ids[1]},
        "dense_boundary": {
            "candidate_ids": ids[2:4],
            "forced_fields": {
                "added_vehicle_hours": 0.0,
                "added_metres_total": 0.0,
                "vehicles_affected": 0,
                "vehicles_no_detour": 0,
            },
        },
        "restart_cancel": {"synthetic_queued": 1},
        "declarations": applied,
    }


_DECISION_POPULATION_GATES = (
    "semantic_comparison_complete",
    "candidate_costs_field_identical",
    "hard_failures_identical",
    "health_classifications_identical",
    "timeout_outcomes_identical",
    "terminal_status_identical",
    "selected_ids_identical",
    "execution_contract_valid",
    "final_decision_identical",
    "restart_equivalent",
    "restart_cursor_identical",
    "restart_evidence_identical",
    "restart_attempt_identity_identical",
    "both_stop_proofs_valid",
    "stop_proof_valid",
    "cache_hits_consistent",
    "daily_results_cache_events_valid",
    "exact_attempt_population_check",
    "active_elapsed_basis_consistent",
    "resource_measurements_complete",
)


def _decision_population_complete(
    comparison: Mapping[str, Any], caps: Mapping[str, Any]
) -> bool:
    """Return whether paired decisions are trustworthy independent of speed.

    The 30-percent reductions are a Phase 3 performance result, not a
    prerequisite for extracting a complete q10/q50/q90 decision population.
    Resource caps and all correctness/restart/provenance checks remain hard
    requirements here; a cap violation must never become Gate S input.
    """
    required_true = (
        *_DECISION_POPULATION_GATES,
    )
    for name in required_true:
        value = comparison.get(name)
        if name == "exact_attempt_population_check":
            if not isinstance(value, Mapping) or value.get("valid") is not True:
                return False
        elif value is not True:
            return False
    fixture = comparison.get("fixture_application") or {}
    if fixture.get("applied") is not True or fixture.get(
            "arm_inputs_identical") is not True or fixture.get(
                "restart_cancel_observed") is not True or fixture.get(
                    "no_detour_pre_sumo_gate") is not True:
        return False
    cancellation = comparison.get("cancellation") or {}
    if (cancellation.get("performed") is not True
            or cancellation.get("called") is not True
            or cancellation.get("queued_work_cancelled") is not True
            or cancellation.get("no_later_starter") is not True):
        return False
    rss = comparison.get("peak_rss_bytes")
    rss_cap = int(caps["peak_rss_bytes"])
    if (not isinstance(rss, Mapping)
            or set(rss) != {"cost_ordered", "ordered_exhaustive"}
            or not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and int(value) >= 0 and int(value) <= rss_cap
                       for value in rss.values())):
        return False
    active = comparison.get("active_elapsed_s")
    active_cap = float(caps["active_seconds"])
    if (not isinstance(active, Mapping)
            or set(active) != {"cost_ordered", "ordered_exhaustive"}
            or not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and 0 <= float(value) <= active_cap
                       for value in active.values())):
        return False
    disk = comparison.get("disk_growth_bytes")
    if (not isinstance(disk, (int, float)) or isinstance(disk, bool)
            or int(disk) < 0
            or int(disk) > int(caps["disk_growth_bytes"])):
        return False
    per_arm_disk = comparison.get("disk_growth_bytes_by_arm")
    if (not isinstance(per_arm_disk, Mapping)
            or set(per_arm_disk) != {"cost_ordered", "ordered_exhaustive"}
            or not all(isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and int(value) >= 0
                       and int(value) <= int(caps["disk_growth_bytes"])
                       for value in per_arm_disk.values())):
        return False
    return True


def _resource_gates_pass(comparison: Mapping[str, Any], caps: Mapping[str, Any]) -> bool:
    """Evaluate correctness/resource gates and the registered speed gates."""
    if not _decision_population_complete(comparison, caps):
        return False
    return bool(
        comparison.get("exact_attempts_reduction_meets_30_percent") is True
        and comparison.get("awake_active_time_reduction_meets_30_percent") is True
        and comparison.get("no_resource_cap_regression") is True
    )


def _run_case(registration: Mapping[str, Any], case: Mapping[str, Any], *,
              runs_root: Path, data_root: Path, workspace_root: Path,
              counterbalance: bool = False) -> dict[str, Any]:
    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

    spec = base.ClosureSearchSpec.from_dict(case["spec"])
    policy = MonthlySearchPolicy.from_dict(json.loads(
        (ROOT / POLICY_PATH).read_text(encoding="utf-8")))
    caps = registration.get("caps") or {}
    active_seconds = float(caps.get("active_seconds", 0))
    attempts_per_case = int(caps.get("attempts_per_case", 0))
    restart_timeout = float(caps.get("restart_timeout_seconds", 0))
    if (active_seconds <= 0 or attempts_per_case <= 0
            or restart_timeout <= 0
            or active_seconds <= restart_timeout):
        raise ValueError("registration resource caps are not positive")
    case_root = Path(workspace_root) / str(case["case_id"])
    size_before = _tree_size(case_root)
    fixture_application = _fixture_application(registration, case)
    result = base.run_ordered_exhaustive_comparison(
        spec, policy, runs_root=runs_root,
        release_root=data_root / registration["fresh_roots"][
            "output_namespace"] / "releases", workspace_root=case_root,
        daily_cost_cache=data_root / registration["fresh_roots"]["daily_cost_cache"] /
        str(case["case_id"]), data_root=data_root,
        counterbalance=counterbalance, isolate_arms=True,
        # Reserve the registered restart/cancellation probe inside the
        # per-case envelope.  Each paired arm gets half of what remains, so
        # the pair plus the probe cannot silently exceed the cap.
        arm_timeout_s=(active_seconds - restart_timeout) / 2.0,
        max_verifications=None,
        max_exact_launches=attempts_per_case,
        fixture_controls=fixture_application)
    comparison = dict(result["comparison"])
    launch_counts = {
        arm_name: len((result["arms"].get(arm_name) or {}).get(
            "exact_launch_records") or [])
        for arm_name in ("cost_ordered", "ordered_exhaustive")
    }
    if any(count > attempts_per_case for count in launch_counts.values()):
        comparison["resource_cap_violation"] = {
            "scope": "per_case_exact_launch_attempts",
            "cap": attempts_per_case,
            "observed_by_arm": launch_counts,
        }
        comparison["gates_passed"] = False
        return {
            "case_id": case["case_id"], "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "comparison": comparison, "arms": result["arms"],
            "gates_passed": False,
            "terminal_status": "INCONCLUSIVE_BUDGET_EXHAUSTED",
        }
    restart = base._restart_probe(
        spec, policy, workspace_root=case_root / "restart", runs_root=runs_root,
        release_root=case_root / "restart-releases",
        daily_cost_cache=data_root / registration["fresh_roots"]["daily_cost_cache"] /
        str(case["case_id"]), reference=result["arms"]["cost_ordered"],
        fixture_controls=fixture_application,
        require_attempt_identity=True,
        max_exact_launches=attempts_per_case,
        timeout_s=restart_timeout)
    comparison["restart"] = restart
    comparison["restart_equivalent"] = bool(restart.get("equivalent"))
    comparison["restart_active_elapsed_s"] = float(
        restart.get("active_elapsed_s", 0.0) or 0.0)
    observed_fixtures = {
        str(arm): ((result["arms"].get(arm) or {}).get("fixture_telemetry") or {})
        for arm in ("cost_ordered", "ordered_exhaustive")
    }
    applied_by_arm = [set(item.get("applied", ()))
                      for item in observed_fixtures.values()]
    comparison["fixture_application"] = {
        "applied": bool(applied_by_arm) and all(
            {"backfill", "no_detour", "dense_boundary"} <= value
            for value in applied_by_arm),
        "arm_inputs_identical": fixture_application["arm_inputs_identical"],
        "registered": fixture_application["declarations"],
        "observed_by_arm": observed_fixtures,
    }
    no_detour_candidate_id = str(
        (fixture_application.get("no_detour") or {}).get("candidate_id", ""))
    no_detour_launch_counts = {}
    for arm_name in ("cost_ordered", "ordered_exhaustive"):
        records = (result["arms"].get(arm_name) or {}).get(
            "exact_launch_records")
        no_detour_launch_counts[arm_name] = (
            None if not isinstance(records, list) else sum(
                str(record.get("candidate_id")) == no_detour_candidate_id
                for record in records if isinstance(record, Mapping)))
    comparison["fixture_application"].update({
        "no_detour_candidate_id": no_detour_candidate_id,
        "no_detour_sumo_launches_by_arm": no_detour_launch_counts,
        "no_detour_pre_sumo_gate": bool(no_detour_candidate_id)
        and all(value == 0 for value in no_detour_launch_counts.values()),
    })
    comparison["fixture_application"]["restart_cancel_observed"] = all(
        bool((value.get("cancel_observed") or {}).get("called"))
        for value in observed_fixtures.values())
    comparison["cancellation"] = dict(restart.get("cancellation") or {})
    comparison["cancellation"]["performed"] = bool(restart.get("performed"))
    comparison["fixture_application"]["restart_cancel_observed"] = bool(
        comparison["cancellation"].get("called"))
    comparison["restart_cursor_identical"] = bool(
        (restart.get("cursor_identical")))
    comparison["restart_evidence_identical"] = bool(
        (restart.get("evidence_identical")))
    comparison["restart_attempt_identity_identical"] = bool(
        (restart.get("attempt_identity_identical")))
    comparison["disk_growth_bytes"] = max(0, _tree_size(case_root) - size_before)
    comparison["disk_growth_bytes_by_arm"] = dict(
        comparison.get("disk_growth_bytes_by_arm") or {})
    comparison["resource_caps_enforced"] = _resource_gates_pass(
        comparison, caps)
    comparison["decision_population_complete"] = _decision_population_complete(
        comparison, caps)
    comparison["performance_gates_passed"] = bool(
        comparison.get("exact_attempts_reduction_meets_30_percent") is True
        and comparison.get("awake_active_time_reduction_meets_30_percent") is True
        and comparison.get("no_resource_cap_regression") is True)
    comparison["gates_passed"] = (
        comparison["resource_caps_enforced"]
        and restart.get("status") != "INCONCLUSIVE_RESTART_TIMEOUT")
    return {
        "case_id": case["case_id"], "search_id": spec.search_id,
        "search_content_key": spec.content_key,
        "comparison": comparison,
        "arms": result["arms"],
        "gates_passed": comparison["gates_passed"],
        # Keep producer-level terminals explicit.  ``run_registered`` must
        # distinguish a complete decision population from the independent
        # performance gate without reaching into a mocked comparison object.
        "decision_population_complete": comparison[
            "decision_population_complete"],
        "performance_gates_passed": comparison["performance_gates_passed"],
        "terminal_status": restart.get("status"),
    }


def _variant_decisions(arm: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive one decision per demand variant from a complete arm.

    The cost ledger is the source of the deterministic q10/q50/q90 fields;
    the ordered-exhaustive arm supplies the complete candidate population and
    its published evidence supplies hard failures.  Nothing here invents a
    neutral value for a missing variant: an incomplete arm raises and the
    enclosing outcome stays inconclusive.
    """
    raw_ledger = base._raw_cost_ledger(arm)
    if not isinstance(raw_ledger, Mapping):
        raise ValueError("complete Gate S source arm has no cost ledger")
    semantic = base._candidate_semantic_evidence(arm)
    policy = base._published_search_policy(arm)
    pilot = getattr(policy, "pilot", None)
    finalist = getattr(policy, "finalist", None)
    minimum_finalists = int(getattr(pilot, "minimum_finalists", 1))
    maximum_finalists = int(getattr(pilot, "maximum_finalists", minimum_finalists))
    practical_equivalence_vehicle_hours = float(
        getattr(finalist, "practical_equivalence_vehicle_hours", 0.0)
    )
    by_variant: dict[str, list[dict[str, Any]]] = {
        variant: [] for variant in ("q10", "q50", "q90")}
    failures_by_candidate: dict[str, dict[str, set[str]]] = {}
    for candidate_id, stages in semantic.items():
        qualified = {variant: set() for variant in by_variant}
        for stage in stages.values():
            for value in stage.get("hard_failures", ()):
                failure = str(value)
                # SUMO execution failures carry the demand variant in their
                # stable identity.  Preserve that qualification; applying it
                # to all three variants would erase the stress-only failure
                # population Gate S is required to recall.
                matched = next((variant for variant in by_variant
                                if failure.startswith(
                                    f"sumo_execution_failure:{variant}:")
                                or failure.startswith(f"{variant}:")), None)
                targets = (by_variant.keys() if matched is None else (matched,))
                for variant in targets:
                    qualified[variant].add(failure)
            for record in stage.get("disruption", ()):
                if not isinstance(record, Mapping):
                    raise ValueError("Gate S disruption record is malformed")
                variant = str(record.get("demand_variant", ""))
                if variant not in qualified:
                    raise ValueError("Gate S disruption variant is invalid")
                if int(record.get("vehicles_no_detour", 0)) > 0:
                    qualified[variant].add("vehicles_no_detour")
        failures_by_candidate[str(candidate_id)] = qualified
    for item in raw_ledger.get("costs", ()):
        candidate_id = str(item.get("candidate_id", ""))
        records = item.get("per_variant")
        if not candidate_id or not isinstance(records, list):
            raise ValueError("Gate S source ledger has an incomplete candidate")
        seen: set[str] = set()
        for record in records:
            variant = str(record.get("demand_variant", ""))
            if variant not in by_variant or variant in seen:
                raise ValueError("Gate S source ledger has invalid variant coverage")
            required = {"vehicles_affected", "vehicles_no_detour",
                        "added_vehicle_hours", "added_metres_total"}
            if not required <= set(record):
                raise ValueError("Gate S source variant cost is incomplete")
            seen.add(variant)
            by_variant[variant].append({
                "candidate_id": candidate_id,
                "cost": {field: record[field] for field in required},
            })
        if seen != set(by_variant):
            raise ValueError("Gate S source ledger lacks q10/q50/q90 coverage")

    result: dict[str, dict[str, Any]] = {}
    for variant, candidates in by_variant.items():
        viable = []
        hard_failures: set[str] = set()
        decision_relevant_failures: set[str] = set()
        for item in candidates:
            candidate_id = item["candidate_id"]
            cost = item["cost"]
            failures = set(
                failures_by_candidate.get(candidate_id, {}).get(variant, set()))
            if int(cost["vehicles_no_detour"]) > 0:
                failures.add("vehicles_no_detour")
            if failures:
                hard_failures.update(failures)
                decision_relevant_failures.update(
                    f"{candidate_id}:{failure}" for failure in failures)
                continue
            viable.append((
                float(cost["added_vehicle_hours"]),
                float(cost["added_metres_total"]),
                int(cost["vehicles_affected"]),
                candidate_id,
                cost,
            ))
        # This is the registered closure_cost_v1 rule, not a vehicle-hours
        # shortcut: the band is opened by primary cost, while every retained
        # row is still ordered lexicographically by all deterministic fields.
        viable.sort(key=lambda item: item[:4])
        minimum_count = min(minimum_finalists, len(viable))
        cutoff = (viable[minimum_count - 1][0]
                  if minimum_count else None)
        finalist_rows = [
            item for item in viable
            if cutoff is not None
            and item[0] <= cutoff + practical_equivalence_vehicle_hours
        ]
        finalists = [item[3] for item in finalist_rows]
        winner = finalists[0] if finalists else None
        winner_cost = (float(finalist_rows[0][4]["added_vehicle_hours"])
                       if finalist_rows else 0.0)
        capacity_exceeded = len(finalists) > maximum_finalists
        result[variant] = {
            "decision": {
                "hard_failures": sorted(hard_failures),
                "viable_set": [item[3] for item in viable],
                "finalists": finalists,
                "winner": winner,
                "capacity_exceeded": capacity_exceeded,
            },
            "decision_relevant_failures": sorted(decision_relevant_failures),
            "winner_cost": winner_cost,
            "candidate_costs": {
                item["candidate_id"]: {
                    **{field: item["cost"][field] for field in (
                        "added_vehicle_hours", "added_metres_total",
                        "vehicles_affected", "vehicles_no_detour")},
                    "feasible": item["candidate_id"] in {
                        row[3] for row in viable},
                }
                for item in candidates
            },
        }
    return result


def _populate_gate_s(case_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build Gate S's q10/q50/q90 records from complete paired outcomes."""
    aggregate: dict[str, dict[str, Any]] = {
        variant: {
            "hard_failures": set(), "viable_set": [], "finalists": [],
            "winners": [], "decision_relevant_failures": set(),
            "winner_cost": 0.0, "reference_winner_cost": 0.0,
            "candidate_costs": {}, "capacity_exceeded": False,
        }
        for variant in ("q10", "q50", "q90")}
    for case in case_results:
        if case.get("decision_population_complete") is not True:
            raise ValueError(
                "Gate S requires every bounded case to have complete decisions")
        arms = case.get("arms") or {}
        cost_arm = arms.get("cost_ordered")
        reference_arm = arms.get("ordered_exhaustive")
        if not isinstance(cost_arm, Mapping) or not isinstance(reference_arm, Mapping):
            raise ValueError("Gate S source case lacks both paired arms")
        cost_variants = _variant_decisions(cost_arm)
        reference_variants = _variant_decisions(reference_arm)
        case_id = str(case.get("case_id", ""))
        if not case_id:
            raise ValueError("Gate S source case has no case ID")
        for variant in ("q10", "q50", "q90"):
            current = aggregate[variant]
            decision = cost_variants[variant]["decision"]
            reference = reference_variants[variant]
            prefix = f"{case_id}:"
            current["hard_failures"].update(decision["hard_failures"])
            current["viable_set"].extend(prefix + value for value in decision["viable_set"])
            current["finalists"].extend(prefix + value for value in decision["finalists"])
            if decision["winner"] is not None:
                current["winners"].append(prefix + decision["winner"])
            current["decision_relevant_failures"].update(
                prefix + value for value in
                cost_variants[variant]["decision_relevant_failures"])
            current["winner_cost"] += float(cost_variants[variant]["winner_cost"])
            current["reference_winner_cost"] += float(reference["winner_cost"])
            # Capacity is a per-case finalist decision.  Preserve the OR of
            # those decisions in the bound aggregate; dropping it here lets
            # the evaluator mistake an over-capacity population for a valid
            # Q50-only candidate set.
            current["capacity_exceeded"] = bool(
                current["capacity_exceeded"]
                or decision.get("capacity_exceeded") is True)
            current["candidate_costs"].update({
                f"{prefix}{candidate_id}": dict(cost)
                for candidate_id, cost in
                cost_variants[variant]["candidate_costs"].items()
            })
    variants = {}
    for variant, current in aggregate.items():
        winners = current.pop("winners")
        variants[variant] = {
            "decision": {
                "hard_failures": sorted(current["hard_failures"]),
                "viable_set": current["viable_set"],
                    "finalists": current["finalists"],
                    "winner": winners,
                    "capacity_exceeded": current["capacity_exceeded"],
            },
            "decision_relevant_failures": sorted(
                current["decision_relevant_failures"]),
            "winner_cost": current["winner_cost"],
            "reference_winner_cost": current["reference_winner_cost"],
            "candidate_costs": current["candidate_costs"],
        }
    return {"population_complete": True, "variants": variants,
            "source": "complete paired bounded/full-month evidence"}


def _source_drift_outcome(
    registration: Mapping[str, Any],
    *,
    registration_path: Path | None,
    completed_results: Sequence[Mapping[str, Any]],
    error: BaseException,
    data_root: Path | None = None,
    disk_baseline_bytes: int = 0,
) -> dict[str, Any]:
    """Publish no scientific claims when bound sources move during a run."""
    selected = registration.get("selection") or {}
    completed = [
        {
            "case_id": str(item.get("case_id", "")),
            "unpromoted_record_sha256": _key(dict(item)),
        }
        for item in completed_results
        if isinstance(item, Mapping)
    ]
    attempts = 0
    active_seconds = 0.0
    peaks: list[int] = []
    disk_growth_bytes = 0
    for item in completed_results:
        if not isinstance(item, Mapping):
            continue
        comparison = item.get("comparison") or {}
        measured = _case_resource_measurements(item)
        partial = item.get("phase3_telemetry")
        if isinstance(partial, Mapping):
            measured = {**measured, **{
                key: partial[key] for key in (
                    "attempts", "active_seconds", "peak_rss_bytes")
                if key in partial}}
        active_seconds += float(measured.get("active_seconds") or 0.0)
        attempts += int(measured.get("attempts") or 0)
        raw_peak = measured.get("peak_rss_bytes")
        if isinstance(raw_peak, (int, float)) and not isinstance(raw_peak, bool) and raw_peak >= 0:
            peaks.append(int(raw_peak))
        # The authoritative disk account is taken below from every registered
        # root.  Per-case comparison growth omits the shared cache and output
        # namespaces and is therefore not suitable for a drift terminal.
    bound_root = Path(data_root) if data_root is not None else Path(
        registration.get("data_root", ROOT))
    registered_roots = _registered_root_paths(registration, bound_root)
    disk_growth_bytes = max(
        0,
        sum(_tree_size(path) for path in registered_roots.values())
        - int(disk_baseline_bytes),
    )
    roots = registered_roots.values()
    resources = _suite_resource_summary(
        completed_results, attempts=attempts, active_seconds=active_seconds,
        disk_growth_bytes=disk_growth_bytes, disk_roots=roots)
    return _with_content_key({
        "schema": OUTCOME_SCHEMA,
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "evidence_id": registration["evidence_id"],
        "status": "INCONCLUSIVE_SOURCE_DRIFT",
        "decision_population_complete": False,
        "registration": _registration_reference(registration, registration_path),
        "selection": _selection_summary(registration),
        # Result objects remain in their isolated workspaces. Only digests are
        # carried here so mixed-source results cannot enter a later gate.
        "case_results": [],
        "unpromoted_execution": {
            "execution_started": bool(completed_results),
            "completed_case_count": len(completed_results),
            "completed_records": completed,
            "source_drift_error": str(error),
        },
        "gate_s": {
            "population_complete": False,
            "variants": {},
            "reason": "bound sources changed during paired execution",
        },
        "correctness_gates": {
            "status": "NOT_EVALUATED_SOURCE_DRIFT",
            "terminal_semantics_identical": None,
            "selected_ids_identical": None,
            "ledger_identical": None,
            "stop_proof_valid": None,
            "exact_attempt_reduction_meets_30_percent": None,
            "active_time_reduction_meets_30_percent": None,
        },
        "remaining_registered_cases": max(
            0, len(registration.get("selected_cases") or [])
            - len(completed_results)),
        "claim_boundary": (
            "diagnostic only; completed work is unpromoted because the "
            "registered executable source changed during measurement"),
        "suite_consumption": {
            "attempts": attempts,
            "active_seconds": active_seconds,
            "disk_growth_bytes": disk_growth_bytes,
            "execution_started": bool(completed_results),
        },
        "resources": resources,
    })


def _selection_summary(registration: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the frozen selection into every producer-owned outcome.

    Gate S consumes bounded outcomes directly.  Reconstructing this metadata
    from completed cases loses the registered population on budget, census,
    lock, and source-drift terminals, so the registration is the sole source.
    """
    selected = registration.get("selection") or {}
    if not isinstance(selected, Mapping):
        selected = {}
    selected_ids = selected.get("selected_ids", [])
    return {
        "selected_case_count": len(selected_ids)
        if isinstance(selected_ids, list) else 0,
        "eligible_count": selected.get("eligible_count"),
        "distinct_edges": len(selected.get("distinct_edges", []))
        if isinstance(selected.get("distinct_edges", []), list) else 0,
        "distinct_demand_periods": len(selected.get("distinct_periods", []))
        if isinstance(selected.get("distinct_periods", []), list) else 0,
        "selected_ids": list(selected_ids) if isinstance(selected_ids, list) else [],
    }


def _registration_reference(
    registration: Mapping[str, Any], registration_path: Path | None = None,
) -> dict[str, Any]:
    """Carry the exact registration identity into every producer outcome."""
    reference = {
        "evidence_id": registration.get("evidence_id"),
        "content_key": registration.get("content_key"),
    }
    if registration_path is not None and Path(registration_path).is_file():
        path = Path(registration_path).resolve()
        reference.update({
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return reference


def _suite_resource_summary(
    results: Sequence[Mapping[str, Any]], *, attempts: int,
    active_seconds: float, disk_growth_bytes: int,
    disk_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    """Publish additive suite telemetry and the peak case RSS explicitly."""
    peaks: list[int] = []
    rss_errors: list[str] = []
    rss_unavailable = False
    for result in results:
        comparison = result.get("comparison") or {}
        raw = comparison.get("peak_rss_bytes")
        if isinstance(raw, Mapping):
            peaks.extend(
                int(value) for value in raw.values()
                if isinstance(value, (int, float))
                and not isinstance(value, bool) and value >= 0
            )
        partial = result.get("phase3_telemetry")
        if isinstance(partial, Mapping):
            if partial.get("rss_status") == "unavailable":
                rss_unavailable = True
            if partial.get("peak_rss_error"):
                rss_unavailable = True
                rss_errors.append(str(partial["peak_rss_error"]))
            value = partial.get("peak_rss_bytes")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                peaks.append(int(value))
    if rss_unavailable:
        # A missing sample invalidates the maximum as a complete census: a
        # later report must carry an explicit unavailable shape, never a
        # partial numeric peak that could be mistaken for a bound.
        peak_rss_bytes = None
        rss_status = "unavailable"
    else:
        peak_rss_bytes = max(peaks, default=None)
        rss_status = "measured" if peaks else (
            "not_run" if not results else "unavailable")
    return {
        "attempts": int(attempts),
        "active_seconds": float(active_seconds),
        "peak_rss_bytes": peak_rss_bytes,
        "rss_status": rss_status,
        "rss_error": "; ".join(sorted(set(rss_errors))) if rss_errors else None,
        "disk_growth_bytes": int(disk_growth_bytes),
        "disk_roots": sorted(str(Path(path).resolve()) for path in disk_roots),
        "aggregation": {
            "attempts": "sum of all arm and restart attempts",
            "active_seconds": "sum of all arm and restart active time",
            "peak_rss_bytes": "maximum peak across cases and arms",
        "disk_growth_bytes": "registered non-overlapping suite roots",
        },
    }


def _case_resource_measurements(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the case's producer telemetry without inventing defaults."""
    comparison = result.get("comparison") or {}
    attempts = 0
    for arm in (result.get("arms") or {}).values():
        if isinstance(arm, Mapping) and isinstance(
                arm.get("exact_launch_records"), list):
            attempts += len(arm["exact_launch_records"])
    attempts += int((comparison.get("restart") or {}).get(
        "exact_launch_attempts", 0) or 0)
    active = comparison.get("active_elapsed_s")
    active_seconds = (
        sum(float(value) for value in active.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool))
        if isinstance(active, Mapping) else None)
    if active_seconds is not None:
        active_seconds += float(
            comparison.get("restart_active_elapsed_s", 0.0) or 0.0)
    peaks: list[int] = []
    raw_peak = comparison.get("peak_rss_bytes")
    if isinstance(raw_peak, Mapping):
        peaks.extend(int(value) for value in raw_peak.values()
                     if isinstance(value, (int, float))
                     and not isinstance(value, bool) and value >= 0)
    return {
        "attempts": attempts,
        "active_seconds": active_seconds,
        "peak_rss_bytes": max(peaks, default=None),
        "disk_growth_bytes": (
            int(comparison["disk_growth_bytes"])
            if isinstance(comparison.get("disk_growth_bytes"), (int, float))
            and not isinstance(comparison.get("disk_growth_bytes"), bool)
            else None),
    }


def _partial_case_resource_measurements(
    case_root: Path, *, started_at: float, peak_rss_bytes: int | None,
) -> dict[str, Any]:
    """Recover telemetry from arm result files after a caught execution error."""
    attempts = 0
    active_seconds = 0.0
    peaks: list[int] = []
    for path in sorted(Path(case_root).glob("**/isolated-arm-result-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        arm = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(arm, Mapping):
            continue
        records = arm.get("exact_launch_records")
        if isinstance(records, list):
            attempts += len(records)
        active = arm.get("active_elapsed_s")
        if isinstance(active, (int, float)) and not isinstance(active, bool):
            active_seconds += float(active)
        peak = arm.get("peak_rss_bytes")
        if isinstance(peak, (int, float)) and not isinstance(peak, bool) and peak >= 0:
            peaks.append(int(peak))
    # The enclosing wall-clock interval is the only trustworthy active
    # envelope when the producer did not reach a comparison object.  It is
    # deliberately not used for successful cases, which carry the runner's
    # own awake-active measurements.
    active_seconds = max(active_seconds, time.monotonic() - started_at)
    if peak_rss_bytes is not None:
        peaks.append(int(peak_rss_bytes))
    return {
        "attempts": attempts,
        "active_seconds": active_seconds,
        "peak_rss_bytes": max(peaks, default=None),
    }


def _phase3_case_telemetry(
    result: Mapping[str, Any] | None, *, case_root: Path,
    registered_roots: Mapping[str, Path], baseline_bytes: int,
    started_at: float, peak_rss_bytes: int | None,
    peak_rss_error: str | None,
) -> dict[str, Any]:
    """Centralize Phase 3 accounting around the case producer.

    ``_run_case`` may fail after one isolated arm has already published its
    launch records.  The enclosing runner, rather than the case's normal
    comparison return path, owns the resource envelope so those records,
    sampler results and all registered namespaces survive an exception or a
    later source-drift terminal.
    """
    existing = result if isinstance(result, Mapping) else {}
    measured = _case_resource_measurements(existing)
    partial = existing.get("phase3_telemetry")
    if isinstance(partial, Mapping):
        measured = {**measured, **{
            key: partial[key] for key in (
                "attempts", "active_seconds", "peak_rss_bytes")
            if key in partial}}
    if (not isinstance(result, Mapping)
            or measured.get("active_seconds") is None
            or (not measured.get("attempts") and not measured.get(
                "peak_rss_bytes"))):
        recovered = _partial_case_resource_measurements(
            case_root, started_at=started_at, peak_rss_bytes=peak_rss_bytes)
        for key in ("attempts", "active_seconds", "peak_rss_bytes"):
            if measured.get(key) is None or (
                    key in {"attempts", "peak_rss_bytes"}
                    and not measured.get(key)):
                measured[key] = recovered[key]
    if peak_rss_bytes is not None:
        measured["peak_rss_bytes"] = max(
            int(measured.get("peak_rss_bytes") or 0), int(peak_rss_bytes))
    roots_size = sum(_tree_size(path) for path in registered_roots.values())
    return {
        "attempts": int(measured.get("attempts") or 0),
        "active_seconds": max(
            float(measured.get("active_seconds") or 0.0),
            time.monotonic() - started_at if not isinstance(result, Mapping)
            else 0.0),
        "peak_rss_bytes": measured.get("peak_rss_bytes"),
        "peak_rss_error": peak_rss_error,
        "rss_status": "unavailable" if peak_rss_error else (
            "measured" if measured.get("peak_rss_bytes") is not None
            else "unavailable"),
        "disk_growth_bytes": max(0, roots_size - int(baseline_bytes)),
        "disk_roots": sorted(str(path.resolve())
                              for path in registered_roots.values()),
        "case_root": str(case_root.resolve()),
    }


def run_registered(registration: Mapping[str, Any], *, runs_root: Path,
                   data_root: Path, workspace_root: Path,
                   registration_path: Path | None = None) -> dict[str, Any]:
    data_root = Path(data_root).resolve()
    if registration_path is None:
        raise ValueError(
            "bounded execution requires the exact registration path")
    registration_path = Path(registration_path).resolve()
    if not registration_path.is_file():
        raise ValueError("bounded execution registration is missing")
    try:
        bound_registration = json.loads(
            registration_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("bounded execution registration is not valid JSON") from error
    if not isinstance(bound_registration, Mapping) \
            or dict(bound_registration) != dict(registration):
        raise ValueError(
            "bounded execution registration bytes do not match the bound object")
    registration_ref = _registration_reference(registration, registration_path)
    resumed = _verify_execution_marker(registration, data_root)
    _verify_registration_for_execution(
        registration, data_root=data_root, require_fresh_roots=not resumed)
    if not resumed:
        _write_execution_marker(registration, data_root)
    # The shared demand workspace is an external serialization boundary.  Do
    # not enter a case and then wait indefinitely for it: a registered run
    # must publish a durable terminal record when another job owns the lock.
    # This probe is fail-closed and never removes or overwrites the holder's
    # lock metadata.
    from traffic_sim.simulation.workspace import WorkspaceLock
    selected = registration.get("selection") or {}
    lock_probe = WorkspaceLock(
        f"subhour_preflight {os.getpid()}",
        path=Path(data_root).resolve() / "runs" / ".demand-workspace.lock")
    if not lock_probe.acquire(timeout=0.0):
        outcome = {
            "schema": OUTCOME_SCHEMA,
            "kind": "subhour_bounded_sumo_outcome",
            "release_evidence": False,
            "evidence_id": registration["evidence_id"],
            "status": "INCONCLUSIVE_WORKSPACE_BUSY",
            "decision_population_complete": False,
            "registration": registration_ref,
            "preflight": {
                "status": "workspace_busy",
                "execution_started": False,
                "selection_read_before_outcome": True,
                "holder": lock_probe.holder(),
                "lock_path": str(lock_probe.path.resolve()),
            },
            "selection": _selection_summary(registration),
            "case_results": [],
            "gate_s": {
                "population_complete": False,
                "variants": {},
                "reason": "bounded execution did not start",
            },
            "paired_attempts": {
                "cost_ordered": None, "ordered_exhaustive": None,
                "exact_attempts_reduction": None,
                "active_elapsed_s": None,
            },
            "restart_cancellation": {
                "status": "NOT_RUN_PRE_EXECUTION",
                "paired": None, "no_later_starter": None,
            },
            "correctness_gates": {
                "status": "NOT_EVALUATED_PRE_EXECUTION",
                "terminal_semantics_identical": None,
                "selected_ids_identical": None,
                "ledger_identical": None,
                "stop_proof_valid": None,
                "exact_attempt_reduction_meets_30_percent": None,
                "active_time_reduction_meets_30_percent": None,
            },
            "resources": {
                "peak_rss_bytes": 0, "disk_growth_bytes": 0,
                "rss_status": "not_run", "disk_status": "not_run",
                "disk_roots": sorted(str(path) for path in
                                     _registered_root_paths(
                                         registration, data_root).values()),
                "caps": dict(registration.get("caps") or {}),
            },
            "suite_consumption": {
                "attempts": 0, "active_seconds": 0.0,
                "disk_growth_bytes": 0, "execution_started": False,
            },
            "remaining_registered_cases": len(
                registration.get("selected_cases") or []),
            "claim_boundary": (
                "diagnostic only; no 30-percent, restart, or "
                "product-activation claim"),
        }
        return _with_content_key(outcome)
    # A successful probe must not retain the shared lock while the real
    # comparison arms acquire it.  Keeping this handle open would make the
    # runner wait on its own probe and turn a free workspace into a deadlock.
    lock_probe.release()
    # RSS and reap gates depend on a trusted process census.  Probe it before
    # constructing runners or starting SUMO so an unavailable census becomes
    # a cheap, append-only terminal record rather than a partial execution
    # that cannot publish trustworthy resource evidence.
    from tools.process_census import (
        ProcessCensusUnavailable, census_mechanism_names,
        process_group_snapshot)

    try:
        process_group_snapshot()
    except ProcessCensusUnavailable as error:
        selected = registration.get("selection") or {}
        outcome = {
            "schema": OUTCOME_SCHEMA,
            "kind": "subhour_bounded_sumo_outcome",
            "release_evidence": False,
            "evidence_id": registration["evidence_id"],
            "status": "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE",
            "decision_population_complete": False,
            "registration": registration_ref,
            "preflight": {
                "required_before_execution": (
                    "trusted process-tree census for RSS and reap gates"),
                "census_mechanisms_tried": census_mechanism_names(),
                "status": "unavailable",
                "error": str(error),
                "execution_started": False,
                "selection_read_before_outcome": True,
            },
            "selection": _selection_summary(registration),
            "case_results": [],
            "gate_s": {
                "population_complete": False,
                "variants": {},
                "reason": "bounded execution did not start",
            },
            "paired_attempts": {
                "cost_ordered": None, "ordered_exhaustive": None,
                "exact_attempts_reduction": None,
                "active_elapsed_s": None,
            },
            "restart_cancellation": {
                "status": "NOT_RUN_PRE_EXECUTION",
                "paired": None, "no_later_starter": None,
            },
            "correctness_gates": {
                "status": "NOT_EVALUATED_PRE_EXECUTION",
                "terminal_semantics_identical": None,
                "selected_ids_identical": None,
                "ledger_identical": None,
                "stop_proof_valid": None,
                "exact_attempt_reduction_meets_30_percent": None,
                "active_time_reduction_meets_30_percent": None,
            },
            "resources": {
                "peak_rss_bytes": 0, "disk_growth_bytes": 0,
                "rss_status": "unavailable", "disk_status": "not_run",
                "disk_roots": sorted(str(path) for path in
                                     _registered_root_paths(
                                         registration, data_root).values()),
                "caps": dict(registration.get("caps") or {}),
            },
            "suite_consumption": {
                "attempts": 0, "active_seconds": 0.0,
                "disk_growth_bytes": 0, "execution_started": False,
            },
            "remaining_registered_cases": len(
                registration.get("selected_cases") or []),
            "claim_boundary": (
                "diagnostic only; no 30-percent, restart, or "
                "product-activation claim"),
        }
        return _with_content_key(outcome)
    # The CLI argument is only a compatibility surface.  The registration's
    # fresh namespace is the authority, otherwise a caller could accidentally
    # place a new outcome under a reused workspace while still passing all
    # input-digest checks.
    workspace_root = _registered_root_paths(registration, data_root)[
        "workspace_namespace"]
    registered_roots = _registered_root_paths(registration, data_root)
    results = []
    caps = registration.get("caps") or {}
    suite_active_cap = float(caps.get(
        "suite_active_seconds", caps.get("active_seconds", 0)))
    suite_disk_cap = int(caps.get(
        "suite_disk_growth_bytes", caps.get("disk_growth_bytes", 0)))
    suite_attempt_cap = int(caps.get(
        "attempts_per_suite", caps.get("attempts_per_case", 0)))
    suite_active_used = 0.0
    # Measure the registered namespaces themselves, not just the current
    # case's arm directories.  Cache and release output are separate roots
    # and must count toward the aggregate suite disk cap as well.
    suite_disk_baseline = sum(
        _tree_size(path) for path in registered_roots.values())
    suite_disk_used = 0
    suite_attempts_used = 0
    for index, case in enumerate(registration["selected_cases"]):
        if ((suite_active_cap > 0 and suite_active_used >= suite_active_cap)
                or (suite_disk_cap > 0 and suite_disk_used >= suite_disk_cap)
                or (suite_attempt_cap > 0 and suite_attempts_used >= suite_attempt_cap)):
            return _with_content_key({
                "schema": OUTCOME_SCHEMA,
                "kind": "subhour_bounded_sumo_outcome",
                "release_evidence": False,
                "evidence_id": registration["evidence_id"],
                "status": "INCONCLUSIVE_BUDGET_EXHAUSTED",
                "decision_population_complete": False,
                "registration": registration_ref,
                "selection": _selection_summary(registration),
                "case_results": results,
                "suite_caps": {"active_seconds": suite_active_cap,
                                "disk_growth_bytes": suite_disk_cap,
                                "attempts": suite_attempt_cap},
                "suite_consumption": {"active_seconds": suite_active_used,
                                       "disk_growth_bytes": suite_disk_used,
                                       "attempts": suite_attempts_used,
                                       "execution_started": bool(results)},
                "resources": _suite_resource_summary(
                    results, attempts=suite_attempts_used,
                    active_seconds=suite_active_used,
                    disk_growth_bytes=suite_disk_used,
                    disk_roots=registered_roots.values()),
                "claim_boundary": "diagnostic only; suite budget exhausted",
            })
        # Verify immediately before every starter so a source edit between
        # cases prevents any later SUMO work from beginning.  Populated roots
        # are valid here because this is the same registration-owned run.
        try:
            _verify_registration_for_execution(
                registration, data_root=data_root, require_fresh_roots=False)
        except (OSError, ValueError) as error:
            return _source_drift_outcome(
                registration, registration_path=registration_path,
                completed_results=results, error=error,
                data_root=data_root, disk_baseline_bytes=suite_disk_baseline)
        case_started = time.monotonic()
        case_root = workspace_root / str(case["case_id"])
        case_disk_baseline = sum(
            _tree_size(path) for path in registered_roots.values())
        case_sampler = None
        case_peak_error = None
        case_peak = None
        try:
            from tools.product_arm import ProcessTreeRSSSampler
            case_sampler = ProcessTreeRSSSampler(os.getpgrp(), interval_s=0.05).start()
        except Exception as error:
            case_peak_error = str(error)
        try:
            result = _run_case(registration, case, runs_root=runs_root,
                               data_root=data_root,
                               workspace_root=workspace_root,
                               counterbalance=bool(index % 2))
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
            if case_sampler is not None:
                try:
                    case_peak = int(case_sampler.stop())
                except Exception as census_error:
                    case_peak_error = str(census_error)
                case_sampler = None
            partial = _partial_case_resource_measurements(
                workspace_root / str(case["case_id"]),
                started_at=case_started, peak_rss_bytes=case_peak)
            result = {"case_id": case["case_id"], "gates_passed": False,
                      "decision_population_complete": False,
                      "performance_gates_passed": False,
                      "error": {"type": type(error).__name__,
                                "message": str(error)},
                      "phase3_telemetry": partial}
        finally:
            if case_sampler is not None:
                try:
                    case_peak = int(case_sampler.stop())
                except Exception as error:
                    case_peak_error = str(error)
        # This envelope is deliberately outside ``_run_case``.  It is the
        # authoritative fallback for exceptions after real arm work and also
        # captures the registered cache/output namespaces, not only the case
        # directory.  A census failure invalidates the case's resource gate;
        # it must not be converted into a numeric zero or a PASS.
        live_telemetry = _phase3_case_telemetry(
            result, case_root=case_root, registered_roots=registered_roots,
            baseline_bytes=case_disk_baseline, started_at=case_started,
            peak_rss_bytes=case_peak, peak_rss_error=case_peak_error)
        result["phase3_telemetry"] = live_telemetry
        if case_peak_error is not None:
            result["gates_passed"] = False
            result["resource_census_complete"] = False
        results.append(result)
        comparison = result.get("comparison") or {}
        measured = _case_resource_measurements(result)
        partial = result.get("phase3_telemetry")
        if isinstance(partial, Mapping):
            measured = {**measured, **{
                key: partial[key] for key in (
                    "attempts", "active_seconds", "peak_rss_bytes")
                if key in partial}}
        suite_active_used += float(measured.get("active_seconds") or 0.0)
        suite_disk_used = max(
            0,
            sum(_tree_size(path) for path in registered_roots.values())
            - suite_disk_baseline,
        )
        attempts = int(measured.get("attempts") or 0)
        suite_attempts_used += attempts
        budget_terminal = (
            str(result.get("terminal_status", ""))
            == "INCONCLUSIVE_BUDGET_EXHAUSTED"
            or isinstance(comparison.get("resource_cap_violation"), Mapping)
            or "budget" in str((result.get("error") or {}).get(
                "message", "")).lower()
            or "exact sumo launch-attempt cap" in str(
                (result.get("error") or {}).get("message", "")).lower()
        )
        if (budget_terminal
                or (suite_active_cap > 0 and suite_active_used > suite_active_cap)
                or (suite_disk_cap > 0 and suite_disk_used > suite_disk_cap)
                or (suite_attempt_cap > 0 and suite_attempts_used > suite_attempt_cap)):
            result["gates_passed"] = False
            result["cap_violation"] = {
                "active_seconds": suite_active_used > suite_active_cap
                if suite_active_cap > 0 else False,
                "disk_growth_bytes": suite_disk_used > suite_disk_cap
                if suite_disk_cap > 0 else False,
                "attempts": suite_attempts_used > suite_attempt_cap
                if suite_attempt_cap > 0 else False,
            }
            return _with_content_key({
                "schema": OUTCOME_SCHEMA,
                "kind": "subhour_bounded_sumo_outcome",
                "release_evidence": False,
                "evidence_id": registration["evidence_id"],
                "status": "INCONCLUSIVE_BUDGET_EXHAUSTED",
                "decision_population_complete": False,
                "registration": registration_ref,
                "selection": _selection_summary(registration),
                "case_results": results,
                "suite_caps": {"active_seconds": suite_active_cap,
                                "disk_growth_bytes": suite_disk_cap,
                                "attempts": suite_attempt_cap},
                "suite_consumption": {"active_seconds": suite_active_used,
                                       "disk_growth_bytes": suite_disk_used,
                                       "attempts": suite_attempts_used,
                                       "execution_started": bool(results)},
                "resources": _suite_resource_summary(
                    results, attempts=suite_attempts_used,
                    active_seconds=suite_active_used,
                    disk_growth_bytes=suite_disk_used,
                    disk_roots=registered_roots.values()),
                "claim_boundary": "diagnostic only; suite resource cap exceeded",
            })
        # A result is promotable only if the same registered bytes still
        # exist after the entire case. This catches edits during a long arm.
        try:
            _verify_registration_for_execution(
                registration, data_root=data_root, require_fresh_roots=False)
        except (OSError, ValueError) as error:
            return _source_drift_outcome(
                registration, registration_path=registration_path,
                completed_results=results, error=error,
                data_root=data_root, disk_baseline_bytes=suite_disk_baseline)
    terminal_statuses = [str(item.get("terminal_status")) for item in results
                         if item.get("terminal_status")]
    passed = bool(results) and all(
        item.get("gates_passed") is True for item in results)
    decision_population_complete = bool(results) and all(
        item.get("decision_population_complete") is True for item in results)
    gate_s = _populate_gate_s(results) if decision_population_complete else {
        "population_complete": False,
        "variants": {},
        "reason": "bounded paired decision population is incomplete",
    }
    performance_miss = decision_population_complete and any(
        item.get("performance_gates_passed") is not True for item in results)
    return _with_content_key({
        "schema": OUTCOME_SCHEMA,
        "kind": "subhour_bounded_sumo_outcome",
        "release_evidence": False,
        "evidence_id": registration["evidence_id"],
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # This is the exact aggregate of the producer-level checks above.  It
        # is deliberately independent of the speed gates so a complete
        # decision population can feed Gate S after a clean performance miss.
        "decision_population_complete": decision_population_complete,
        "status": ("PASS" if passed else "INCONCLUSIVE_PERFORMANCE_GATE"
                   if performance_miss else terminal_statuses[0]
                   if terminal_statuses else "INCONCLUSIVE_BOUNDED_GATES"),
        "registration": registration_ref,
        "selection": _selection_summary(registration),
        "case_results": results,
        "suite_caps": {"active_seconds": suite_active_cap,
                        "disk_growth_bytes": suite_disk_cap,
                        "attempts": suite_attempt_cap},
        "suite_consumption": {"active_seconds": suite_active_used,
                               "disk_growth_bytes": suite_disk_used,
                               "attempts": suite_attempts_used,
                               "execution_started": bool(results)},
        "resources": _suite_resource_summary(
            results, attempts=suite_attempts_used,
            active_seconds=suite_active_used,
            disk_growth_bytes=suite_disk_used,
            disk_roots=registered_roots.values()),
        "gate_s": gate_s,
        "claim_boundary": "diagnostic only; no product activation",
    })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preregister", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--runs-root", type=Path, default=base.DEFAULT_RUNS_ROOT)
    parser.add_argument("--data-root", type=Path, default=ROOT)
    parser.add_argument("--registration", type=Path, default=DEFAULT_REGISTRATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument("--workspace-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--evidence-id", default="subhour-bounded-sumo-v1")
    args = parser.parse_args(argv)
    if args.preregister:
        record = build_registration(args.runs_root, data_root=args.data_root,
                                    registration_path=args.registration,
                                    evidence_id=args.evidence_id)
        write_registration(args.registration, record)
        print(f"wrote {args.registration} ({record['content_key']})")
        return 0
    record = json.loads(args.registration.read_text(encoding="utf-8"))
    if args.verify:
        verify_registration(record, root=args.data_root)
        print(f"verified {args.registration} ({record['content_key']})")
        return 0
    outcome = run_registered(record, runs_root=args.runs_root,
                             data_root=args.data_root,
                             workspace_root=args.workspace_root,
                             registration_path=args.registration)
    try:
        _publish_json_no_clobber(args.out, outcome, label="outcome")
    except FileExistsError as error:
        raise SystemExit(str(error)) from error
    print(f"wrote {args.out} (status={outcome['status']})")
    return 0 if outcome["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
