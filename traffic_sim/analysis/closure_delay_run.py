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
exact mapping the search froze, including both of those inputs, so this module
reads them from there and only falls back to a scan when no release exists
(the compatibility `--demand-archive` path), recording which of the two it used.

PUBLISHED AS A DIAGNOSTIC, NOT AN ARTIFACT. `delay-profile.json` is written
beside the workspace rather than into `artifacts/`, exactly like
`cost-ordered-shadow.json`: a succeeded workspace is closed to publication,
and this is a replay of a finished run rather than evidence the run produced.
Nothing about the search's result, ranking, finalists or claim boundary
depends on it.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from traffic_sim.analysis.delay_profile import (
    BINS,
    DEMAND_VARIANTS,
    MEASURE,
    SCHEMA_VERSION,
    DelayProfileError,
    delay_histogram,
    rank_periods,
    rank_schedules,
    sum_histograms,
    verify_against_cost,
)
from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec
from traffic_sim.simulation.deterministic_disruption import (
    ArchiveInputs,
    NetworkCostModel,
    VARIANT_FILENAMES,
    closure_seconds,
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
from traffic_sim.simulation.monthly_demand import (
    DEFAULT_RELEASE_ROOT,
    DEFAULT_RUNS_ROOT,
    find_demand_archives,
)
from traffic_sim.simulation.search_workspace import load_search_workspace


#: Where the replay is written inside the search workspace.
PROFILE_FILENAME = "delay-profile.json"

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
    baseline_trip_duration_p99_s: int
    envelope_policy: EnvelopePolicy
    origin: str
    release_paths: tuple[str, ...] = ()

    def provenance(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "baseline_trip_duration_p99_s": self.baseline_trip_duration_p99_s,
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            "release_manifests": list(self.release_paths),
            "archive_count": len(self.by_build_key),
        }


def load_archive_index(
    spec: ClosureSearchSpec,
    *,
    release_root: Path = DEFAULT_RELEASE_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> ArchiveIndex:
    """Index this search's frozen demand releases, or declare a fallback."""
    release_root = Path(release_root)
    by_build_key: dict[str, Path] = {}
    paths: list[str] = []
    settings: set[tuple[int, str]] = set()
    policy = INDEPENDENT_DAILY_ENVELOPE_POLICY
    p99 = FALLBACK_BASELINE_TRIP_P99_S

    for path in sorted(release_root.glob("*.json")) if release_root.is_dir() else ():
        try:
            release = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        request = release.get("request")
        if (not isinstance(release, Mapping) or not isinstance(request, Mapping)
                or request.get("search_content_key") != spec.content_key):
            continue
        try:
            release_policy = EnvelopePolicy(**dict(request["envelope_policy"]))
            release_p99 = int(request["baseline_trip_duration_p99_s"])
        except (KeyError, TypeError, ValueError):
            continue
        # Two releases for one search must agree on the basis they were
        # resolved against; if they do not, the archives they pin are not
        # comparable and no single curve describes the search.
        settings.add((release_p99, json.dumps(
            dataclasses.asdict(release_policy), sort_keys=True)))
        policy, p99 = release_policy, release_p99
        for entry in release.get("entries", ()):
            if not isinstance(entry, Mapping):
                continue
            build_key = str(
                (entry.get("demand_build_spec") or {}).get("build_key", ""))
            archive = str(entry.get("archive", ""))
            if not build_key or not archive:
                continue
            resolved = Path(archive)
            if not resolved.is_absolute():
                resolved = (path.parent / resolved).resolve()
            by_build_key[build_key] = resolved
        paths.append(str(path))

    if len(settings) > 1:
        raise DelayProfileError(
            "this search's demand releases disagree about the envelope basis; "
            "the archives they pin are not comparable")
    return ArchiveIndex(
        by_build_key=by_build_key,
        baseline_trip_duration_p99_s=p99,
        envelope_policy=policy,
        origin="demand_release" if by_build_key else "archive_scan",
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
    if pinned is not None:
        if not pinned.is_dir():
            raise DelayProfileError(
                f"the demand archive this search used is gone: {pinned}")
        return pinned

    matches = find_demand_archives(Path(runs_root), required)
    if not matches:
        raise DelayProfileError(
            f"no demand archive is available for {schedule.first_work_date} "
            f"({required.build_key}); the run's archives were removed or were "
            f"built by different source code")
    return Path(str(matches[0]["archive"]))


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
) -> tuple[dict[str, Any], int]:
    """One candidate's curve per variant, verified against its ranked cost.

    Returns the entry and the advanced progress step, so a caller can show
    one continuous count across several candidates.
    """
    schedules = unit_schedules(spec, parent)
    closed = set(spec.directed_edges)
    per_variant_parts: dict[str, list] = {name: [] for name in DEMAND_VARIANTS}
    archives: list[str] = []

    # One archive commonly serves several daily units (the canonical daily
    # envelope is a previous/current/next window) and both candidates. Fixing
    # its identity once avoids re-hashing three route files per unit.
    resolved = {} if archive_inputs is None else archive_inputs
    for schedule in schedules:
        archive = resolve_archive(spec, schedule, index, runs_root=runs_root)
        inputs = resolved.get(archive)
        if inputs is None:
            inputs = ArchiveInputs.from_archive(archive)
            resolved[archive] = inputs
        archives.append(str(archive))
        closures = closure_seconds(
            spec, schedule, epoch=inputs.epoch, duration_s=inputs.duration_s)
        for variant in DEMAND_VARIANTS:
            per_variant_parts[variant].append(delay_histogram(
                inputs.variant_paths[variant],
                closed,
                closures,
                network.edge_time,
                network.edge_len,
                adjacency=network.adjacency,
            ))
            step += 1
            if progress is not None:
                progress(step, total_steps,
                         f"{schedule.first_work_date} {variant}")

    summed = {variant: sum_histograms(parts)
              for variant, parts in per_variant_parts.items()}
    verification = verify_against_cost(
        summed, published_cost, candidate_id=parent.schedule_id)
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
    spec = ClosureSearchSpec.from_dict(result["closure_search_spec"])
    candidates = select_candidates(result, top_n=top_n)
    if not candidates:
        raise DelayProfileError(
            "the search has no viable candidate to draw a distribution for")

    index = load_archive_index(
        spec, release_root=release_root, runs_root=runs_root)
    network = NetworkCostModel(network_path)

    total_steps = sum(
        len(unit_schedules(spec, schedule)) * len(DEMAND_VARIANTS)
        for schedule, _cost, _meta in candidates)
    step = 0
    archive_inputs: dict[Path, ArchiveInputs] = {}
    entries: list[dict[str, Any]] = []
    for rank, (schedule, cost, meta) in enumerate(candidates, start=1):
        entry, step = profile_candidate(
            spec, schedule, cost,
            index=index, network=network, runs_root=runs_root,
            progress=progress, step=step, total_steps=total_steps,
            archive_inputs=archive_inputs)
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
        "directed_edges": list(spec.directed_edges),
        "source": spec.source,
        "interday_policy": spec.interday_policy,
        "bins": [dict(item) for item in BINS],
        "variants": list(DEMAND_VARIANTS),
        "variant_files": dict(VARIANT_FILENAMES),
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


def read_delay_profile(search_dir: Path) -> dict[str, Any] | None:
    """A previously published replay, or None when there is none."""
    path = Path(search_dir) / PROFILE_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def profile_matches_search(
    payload: Mapping[str, Any],
    search_id: str,
    content_key: str,
) -> bool:
    """Whether a stored replay describes the search being displayed."""
    return (
        payload.get("kind") == "closure_delay_profile"
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("measure") == MEASURE
        and str(payload.get("search_id")) == str(search_id)
        and str(payload.get("search_content_key")) == str(content_key)
    )


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
