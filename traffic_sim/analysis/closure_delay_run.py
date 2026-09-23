"""Build a delay profile for the best candidates of a finished search.

This is the wiring half of `delay_profile`: it finds the archives a completed
monthly closure search actually used, replays the deterministic detour cost
over them, and publishes the per-vehicle distribution beside the workspace.

THE ARCHIVES COME FROM THE SEARCH'S OWN RELEASE, not from a fresh guess. A
demand archive is selected by a `DemandBuildSpec` whose build key depends on
the simulation envelope, and the envelope depends on the warm-up basis
(`baseline_trip_duration_p99_s`) and the `EnvelopePolicy` the search ran with.
Re-deriving those from today's defaults would silently pick a different
archive whenever either changes — a curve drawn from different demand than
the ranking it illustrates. `runs/monthly-demand-releases/*.json` records the
exact mapping the search froze, including both of those inputs. This module
requires that release and verifies its pinned archive bytes before replay.

PUBLISHED AS A DIAGNOSTIC, NOT AN ARTIFACT. `delay-profile.json` is written
beside the workspace rather than into `artifacts/`, exactly like
`cost-ordered-shadow.json`: a succeeded workspace is closed to publication,
and this is a replay of a finished run rather than evidence the run produced.
Nothing about the search's result, ranking, finalists or claim boundary
depends on it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from traffic_sim.analysis.delay_profile_store import (
    MEASURE,
    PROFILE_FILENAME,
    SCHEMA_VERSION,
    profile_matches_search,
    read_delay_profile,
    read_verified_result,
)
from traffic_sim.analysis.delay_profile import (
    BINS,
    DEMAND_VARIANTS,
    DelayProfileError,
    delay_histogram,
    rank_periods,
    rank_schedules,
    sum_histograms,
    verify_against_cost,
)
from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.deterministic_disruption import (
    ArchiveInputs,
    NetworkCostModel,
    VARIANT_FILENAMES,
    closure_seconds,
)
from traffic_sim.simulation.closure_ranking import (
    CLOSURE_COST_OBJECTIVES,
    LEGACY_WORST_COST_OBJECTIVE,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
    envelope_demand_spec,
    independent_daily_demand_spec,
)
from traffic_sim.simulation.independent_daily import (
    INDEPENDENT_DAILY_ENVELOPE_POLICY,
    daily_unit_records,
)
from traffic_sim.simulation.monthly_demand import DEFAULT_RELEASE_ROOT, DEFAULT_RUNS_ROOT
from traffic_sim.simulation.search_workspace import load_search_workspace


#: How many candidates get a curve. Two is the default because a curve is
#: read by comparison — one distribution on its own has no scale — and a third
#: line on the same axis stops being legible at panel width.
DEFAULT_TOP_N = 2

#: The warm-up basis serve.py and the CLI both start searches with. Used ONLY
#: when no demand release records the value that was actually used.
FALLBACK_BASELINE_TRIP_P99_S = 3600

ProgressCallback = Callable[[int, int, str], None]


@dataclasses.dataclass(frozen=True)
class ArchiveIndex:
    """Which demand archive serves which envelope, and where that was decided."""

    by_build_key: Mapping[str, Path]
    records_by_build_key: Mapping[str, Mapping[str, Any]]
    baseline_trip_duration_p99_s: int
    envelope_policy: EnvelopePolicy
    origin: str
    release_paths: tuple[str, ...] = ()
    validated_build_keys: set[str] = dataclasses.field(
        default_factory=set, compare=False, repr=False)
    network_sha_by_build_key: dict[str, str] = dataclasses.field(
        default_factory=dict, compare=False, repr=False)

    def provenance(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "baseline_trip_duration_p99_s": self.baseline_trip_duration_p99_s,
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            "release_manifests": list(self.release_paths),
            "archive_count": len(self.by_build_key),
        }


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verify_pinned_archive(path: Path, entry: Mapping[str, Any],
                           required: Any) -> str:
    """Check the release's exact archive bytes without today's source defaults."""
    if not path.is_dir():
        raise DelayProfileError(f"the demand archive this search used is gone: {path}")
    if (entry.get("build_key") != required.build_key
            or entry.get("demand_build_spec") != required.to_dict()):
        raise DelayProfileError(f"pinned demand build contract changed: {path}")
    manifest_hash = entry.get("archive_manifest_sha256")
    outputs = entry.get("outputs")
    if (not isinstance(manifest_hash, str) or len(manifest_hash) != 64
            or not isinstance(outputs, list) or not outputs
            or entry.get("archive_content_key") != _canonical_digest(outputs)
            or sha256_file(path / "manifest.json") != manifest_hash):
        raise DelayProfileError(f"pinned demand archive identity changed: {path}")
    seen: set[str] = set()
    for output in outputs:
        if not isinstance(output, Mapping):
            raise DelayProfileError(f"pinned demand archive output is invalid: {path}")
        name = output.get("name")
        if (not isinstance(name, str) or name in {"", ".", ".."}
                or Path(name).name != name or name in seen
                or type(output.get("bytes")) is not int
                or output["bytes"] < 0
                or not isinstance(output.get("sha256"), str)):
            raise DelayProfileError(f"pinned demand archive output is invalid: {path}")
        seen.add(name)
        item = path / name
        if (not item.is_file() or item.stat().st_size != output["bytes"]
                or sha256_file(item) != output["sha256"]):
            raise DelayProfileError(f"pinned demand archive output changed: {item}")
    needed_variants = ("q50",) if required.variant_mode == "q50_only" else DEMAND_VARIANTS
    needed = {"demand_meta.json", "demand_build_spec.json"} | {
        VARIANT_FILENAMES[variant] for variant in needed_variants}
    if not needed.issubset(seen):
        raise DelayProfileError(f"pinned demand archive is incomplete: {path}")
    try:
        metadata = json.loads((path / "demand_meta.json").read_text(
            encoding="utf-8"))
        network_sha = metadata["build_fingerprint"]["artifacts"]["network"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        raise DelayProfileError(
            f"pinned demand archive lacks its network identity: {path}") from None
    if not isinstance(network_sha, str) or not network_sha:
        raise DelayProfileError(f"pinned demand archive has no network hash: {path}")
    return network_sha


def load_archive_index(
    spec: ClosureSearchSpec,
    *,
    release_root: Path = DEFAULT_RELEASE_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> ArchiveIndex:
    """Index this search's frozen demand releases, or declare a fallback."""
    release_root = Path(release_root)
    by_build_key: dict[str, Path] = {}
    records_by_build_key: dict[str, Mapping[str, Any]] = {}
    paths: list[str] = []
    settings: set[tuple[int, str]] = set()
    policy = INDEPENDENT_DAILY_ENVELOPE_POLICY
    p99 = FALLBACK_BASELINE_TRIP_P99_S

    for path in sorted(release_root.glob("*.json")) if release_root.is_dir() else ():
        try:
            release = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(release, Mapping):
            continue
        request = release.get("request")
        if (not isinstance(request, Mapping)
                or request.get("search_content_key") != spec.content_key
                or request.get("release_id") != spec.demand_build_id):
            continue
        if (release.get("kind") != "monthly_demand_release"
                or release.get("schema_version") != 1
                or request.get("kind") != "monthly_demand_release_request"
                or request.get("schema_version") != 1
                or path.stem != _canonical_digest(request)[:32]
                or release.get("content_key") != _canonical_digest({
                    key: value for key, value in release.items()
                    if key != "content_key"
                })):
            raise DelayProfileError(f"the search's demand release changed: {path}")
        try:
            release_policy = EnvelopePolicy(**dict(request["envelope_policy"]))
            release_p99 = int(request["baseline_trip_duration_p99_s"])
        except (KeyError, TypeError, ValueError):
            raise DelayProfileError(f"the search's demand release is invalid: {path}") from None
        # Two releases for one search must agree on the basis they were
        # resolved against; if they do not, the archives they pin are not
        # comparable and no single curve describes the search.
        settings.add((release_p99, json.dumps(
            dataclasses.asdict(release_policy), sort_keys=True)))
        policy, p99 = release_policy, release_p99
        entries = release.get("entries")
        if (not isinstance(entries, list) or not entries
                or any(not isinstance(entry, Mapping)
                       or not isinstance(entry.get("demand_build_spec"), Mapping)
                       for entry in entries)):
            raise DelayProfileError(f"the search's demand release has no archives: {path}")
        required_builds = request.get("required_builds")
        if (not isinstance(required_builds, list)
                or any(not isinstance(item, Mapping) for item in required_builds)
                or sorted(required_builds, key=lambda item: item.get("build_key", ""))
                != sorted((entry.get("demand_build_spec") for entry in entries),
                          key=lambda item: item.get("build_key", ""))):
            raise DelayProfileError(f"the search's demand release builds changed: {path}")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise DelayProfileError(f"the search's demand release is invalid: {path}")
            build_key = str(
                (entry.get("demand_build_spec") or {}).get("build_key", ""))
            archive = str(entry.get("archive", ""))
            if not build_key or not archive:
                raise DelayProfileError(f"the search's demand release is invalid: {path}")
            resolved = Path(archive)
            if not resolved.is_absolute():
                resolved = (path.parent / resolved).resolve()
            previous = records_by_build_key.get(build_key)
            if previous is not None and (
                    previous != entry or by_build_key[build_key] != resolved):
                raise DelayProfileError(
                    f"the search's demand releases disagree on {build_key}")
            by_build_key[build_key] = resolved
            records_by_build_key[build_key] = entry
        paths.append(str(path))

    if len(settings) > 1:
        raise DelayProfileError(
            "this search's demand releases disagree about the envelope basis; "
            "the archives they pin are not comparable")
    if not by_build_key:
        raise DelayProfileError(
            "the search's frozen demand release is missing; refusing to "
            "guess an archive for the delay diagram")
    return ArchiveIndex(
        by_build_key=by_build_key,
        records_by_build_key=records_by_build_key,
        baseline_trip_duration_p99_s=p99,
        envelope_policy=policy,
        origin="demand_release",
        release_paths=tuple(paths),
    )


def unit_schedules(
    spec: ClosureSearchSpec,
    parent: ClosureSchedule,
) -> tuple[ClosureSchedule, ...]:
    """The schedules that are actually costed, one archive each.

    Under `independent_daily_reset_v1` a parent is priced as the sum of its
    daily units — the same decomposition the search uses, through the same
    function, so a chart cannot split a schedule differently than the ranking
    did. Any other interday policy prices the parent in one piece.
    """
    if spec.interday_policy != "independent_daily_reset_v1":
        return (parent,)
    return tuple(
        build_schedule()
        for _unit_id, _identity, build_schedule
        in daily_unit_records(spec, parent)
    )


def resolve_archive(
    spec: ClosureSearchSpec,
    schedule: ClosureSchedule,
    index: ArchiveIndex,
    *,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    network_sha256: str | None = None,
) -> Path:
    """The demand archive this one schedule was costed against."""
    envelope = build_simulation_envelope(
        spec,
        schedule,
        baseline_trip_duration_p99_s=index.baseline_trip_duration_p99_s,
        policy=index.envelope_policy,
    )
    if spec.interday_policy == "independent_daily_reset_v1":
        required = independent_daily_demand_spec(spec, schedule, envelope)
    else:
        required = envelope_demand_spec(spec, envelope)

    pinned = index.by_build_key.get(required.build_key)
    if pinned is None:
        raise DelayProfileError(
            f"the search's demand release does not pin "
            f"{schedule.first_work_date} ({required.build_key})")
    if required.build_key not in index.validated_build_keys:
        index.network_sha_by_build_key[required.build_key] = _verify_pinned_archive(
            pinned, index.records_by_build_key[required.build_key], required)
        index.validated_build_keys.add(required.build_key)
    if (network_sha256 is not None
            and index.network_sha_by_build_key[required.build_key]
            != network_sha256):
        raise DelayProfileError(
            f"the current network differs from the search's demand archive: {pinned}")
    return pinned


def profile_candidate(
    spec: ClosureSearchSpec,
    parent: ClosureSchedule,
    published_cost: Mapping[str, Any],
    *,
    index: ArchiveIndex,
    network: NetworkCostModel,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    progress: ProgressCallback | None = None,
    step: int = 0,
    total_steps: int = 0,
    archive_inputs: dict[Path, ArchiveInputs] | None = None,
    demand_variants: tuple[str, ...] = DEMAND_VARIANTS,
    objective_method: str = LEGACY_WORST_COST_OBJECTIVE,
) -> tuple[dict[str, Any], int]:
    """One candidate's curve per variant, verified against its ranked cost.

    Returns the entry and the advanced progress step, so a caller can show
    one continuous count across several candidates.
    """
    schedules = unit_schedules(spec, parent)
    closed = set(spec.directed_edges)
    if not demand_variants or any(
            variant not in DEMAND_VARIANTS for variant in demand_variants):
        raise DelayProfileError(
            f"unsupported demand variants for delay profile: "
            f"{list(demand_variants)}")
    per_variant_parts: dict[str, list] = {name: [] for name in demand_variants}
    archives: list[str] = []

    # One archive commonly serves several daily units (the canonical daily
    # envelope is a previous/current/next window) and both candidates. Fixing
    # its identity once avoids re-hashing three route files per unit.
    resolved = {} if archive_inputs is None else archive_inputs
    for schedule in schedules:
        archive = resolve_archive(
            spec, schedule, index, runs_root=runs_root,
            network_sha256=network.network_sha256)
        inputs = resolved.get(archive)
        if inputs is None:
            inputs = ArchiveInputs.from_archive(archive)
            resolved[archive] = inputs
        archives.append(str(archive))
        closures = closure_seconds(
            spec, schedule, epoch=inputs.epoch, duration_s=inputs.duration_s)
        for variant in demand_variants:
            if variant not in inputs.variant_paths:
                raise DelayProfileError(
                    f"the search's archive does not contain its declared "
                    f"{variant} demand variant: {archive}")
            per_variant_parts[variant].append(delay_histogram(
                inputs.variant_paths[variant],
                closed,
                closures,
                network.edge_time,
                network.edge_len,
                adjacency=network.adjacency,
                destination_access=getattr(network, "destination_access", None),
            ))
            step += 1
            if progress is not None:
                progress(step, total_steps,
                         f"{schedule.first_work_date} {variant}")

    summed = {variant: sum_histograms(parts)
              for variant, parts in per_variant_parts.items()}
    verification = verify_against_cost(
        summed,
        published_cost,
        candidate_id=parent.schedule_id,
        objective_method=objective_method,
    )
    return {
        "schedule_id": parent.schedule_id,
        "first_work_date": parent.first_work_date,
        "day_count": parent.day_count,
        "daily_start": parent.daily_start,
        "daily_end": parent.daily_end,
        "intervals": [interval.to_dict() for interval in parent.intervals],
        "closure_cost": dict(published_cost),
        "variants": summed,
        "cost_verification": verification,
        "demand_archives": archives,
    }, step


def load_search_result(search_dir: Path) -> dict[str, Any]:
    """The verified result of a succeeded search workspace."""
    search_dir = Path(search_dir)
    workspace = load_search_workspace(search_dir, verify=True)
    records = [
        item for item in workspace.manifest.get("artifacts", ())
        if isinstance(item, Mapping)
        and item.get("kind") == "monthly_closure_search_result"
    ]
    if len(records) != 1:
        raise DelayProfileError(
            f"workspace does not hold exactly one search result: {search_dir}")
    result = json.loads(
        (search_dir / str(records[0]["path"])).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise DelayProfileError(f"search result is not an object: {search_dir}")
    return result


def select_candidates(
    result: Mapping[str, Any],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> tuple[tuple[ClosureSchedule, Mapping[str, Any], dict[str, Any]], ...]:
    """The best candidates, best first, with the label the panel shows.

    A long-range search reports PERIODS (one best schedule per rolling start
    date, listed in calendar order); a short one reports the schedules
    themselves. Both are ranked by the same cost key, so "the two best dates"
    means the same thing in either shape.
    """
    comparison = result.get("period_comparison")
    selected: list[tuple[ClosureSchedule, Mapping[str, Any], dict[str, Any]]] = []
    if isinstance(comparison, Mapping):
        for period in rank_periods(comparison.get("periods", ()))[:top_n]:
            schedule = ClosureSchedule.from_dict(period["best_schedule"])
            selected.append((schedule, period["best_cost"], {
                "start_date": period.get("start_date"),
                "period_end": period.get("period_end"),
                "workday_count": period.get("workday_count"),
                "status": period.get("status"),
                "contains_final_winner": bool(
                    period.get("contains_final_winner")),
            }))
        return tuple(selected)

    statistics = {
        str(item.get("candidate_id")): item
        for source in ("robust_decision", "pilot_selection")
        for item in (result.get(source) or {}).get("candidates", ())
        if isinstance(item, Mapping)
    }
    ranked = rank_schedules(result.get("shortlisted_schedules", ()), statistics)
    for raw_schedule, cost in ranked[:top_n]:
        schedule = ClosureSchedule.from_dict(raw_schedule)
        selected.append((schedule, cost, {
            "start_date": schedule.first_work_date,
            "period_end": schedule.intervals[-1].work_date,
            "workday_count": schedule.day_count,
            "status": ("best_period"
                       if schedule.schedule_id == result.get("winner_id")
                       else "viable"),
            "contains_final_winner":
                schedule.schedule_id == result.get("winner_id"),
        }))
    return tuple(selected)


def build_delay_profile(
    search_dir: Path,
    *,
    top_n: int = DEFAULT_TOP_N,
    release_root: Path = DEFAULT_RELEASE_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    network_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Replay the best candidates of one finished search as distributions."""
    search_dir = Path(search_dir)
    result = load_search_result(search_dir)
    verified_result = read_verified_result(search_dir)
    if verified_result is None or verified_result[0] != result:
        raise DelayProfileError("the search result bytes no longer match its workspace")
    spec = ClosureSearchSpec.from_dict(result["closure_search_spec"])
    declared = result.get("demand_variants") or DEMAND_VARIANTS
    if not isinstance(declared, (list, tuple)):
        raise DelayProfileError("search result has invalid demand_variants")
    declared_set = {str(variant) for variant in declared}
    demand_variants = tuple(
        variant for variant in DEMAND_VARIANTS if variant in declared_set)
    if declared_set != set(demand_variants) or "q50" not in demand_variants:
        raise DelayProfileError(
            f"search result declares unsupported demand variants: "
            f"{sorted(declared_set)}")
    objective_method = str(
        result.get("objective_method")
        or (result.get("policy") or {}).get("objective_method")
        or LEGACY_WORST_COST_OBJECTIVE
    )
    if objective_method not in CLOSURE_COST_OBJECTIVES:
        raise DelayProfileError(
            f"search result declares unsupported closure objective: "
            f"{objective_method}")
    candidates = select_candidates(result, top_n=top_n)
    if not candidates:
        raise DelayProfileError(
            "the search has no viable candidate to draw a distribution for")

    index = load_archive_index(
        spec, release_root=release_root, runs_root=runs_root)
    network = NetworkCostModel(network_path)

    total_steps = sum(
        len(unit_schedules(spec, schedule)) * len(demand_variants)
        for schedule, _cost, _meta in candidates)
    step = 0
    archive_inputs: dict[Path, ArchiveInputs] = {}
    entries: list[dict[str, Any]] = []
    for rank, (schedule, cost, meta) in enumerate(candidates, start=1):
        entry, step = profile_candidate(
            spec, schedule, cost,
            index=index, network=network, runs_root=runs_root,
            progress=progress, step=step, total_steps=total_steps,
            archive_inputs=archive_inputs,
            demand_variants=demand_variants,
            objective_method=objective_method)
        entry["rank"] = rank
        entry.update(meta)
        entries.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "closure_delay_profile",
        "measure": MEASURE,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "search_id": result.get("search_id"),
        "search_content_key": spec.content_key,
        "result_sha256": verified_result[1],
        "directed_edges": list(spec.directed_edges),
        "source": spec.source,
        "interday_policy": spec.interday_policy,
        "bins": [dict(item) for item in BINS],
        "variants": list(demand_variants),
        "variant_files": {
            variant: VARIANT_FILENAMES[variant]
            for variant in demand_variants
        },
        "direction_sensitivity_evaluated": (
            set(demand_variants) == set(DEMAND_VARIANTS)
            and result.get("direction_sensitivity_evaluated") is not False
        ),
        "objective_method": objective_method,
        "candidates": entries,
        "demand": index.provenance(),
        "network": {
            "path": str(network.network_path),
            "sha256": network.network_sha256,
        },
        "basis": (
            "cheapest legal free-flow path with the closure minus the cheapest "
            "without it, per affected vehicle; identical to the seconds the "
            "ranking objective sums into added_vehicle_hours"
        ),
        "not_measured": (
            "queueing and congestion delay. Simulated time loss was measured "
            "as noise around zero on this network (closure_ranking), which is "
            "why the deployed objective is this detour cost."
        ),
    }


def write_delay_profile(
    search_dir: Path,
    payload: Mapping[str, Any],
) -> Path:
    """Publish the replay beside the workspace, atomically."""
    destination = Path(search_dir) / PROFILE_FILENAME
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


__all__ = [
    "ArchiveIndex",
    "DEFAULT_TOP_N",
    "PROFILE_FILENAME",
    "build_delay_profile",
    "load_archive_index",
    "load_search_result",
    "profile_matches_search",
    "read_delay_profile",
    "resolve_archive",
    "select_candidates",
    "unit_schedules",
    "write_delay_profile",
]
