"""Run ONE product closure-search arm, exactly as the CLI would.

A benchmark that compares exhaustive against cost-ordered execution is only
worth anything if both arms are the product. The temptation when writing such a
tool is to assemble a runner that looks like the CLI's — and then the two drift,
and the benchmark measures a lookalike.

So this module is the single place that builds an arm, and it builds it out of
`run_monthly_closure_search`'s own helpers: the same screening builders, the
same demand resolver, the same independent-daily runner, the same
`_cost_source_for`. The benchmark and the independent-vs-continuous harness both
call it. If the CLI changes how an arm is constructed, there is one place that
has to follow, not three.

WHAT IT DELIBERATELY DOES NOT DO. It never builds demand: `build_missing=False`
everywhere, because a measurement that calibrates its own inputs is measuring
itself. It takes no workspace lock either — the caller holds one for the whole
campaign, since acquiring and releasing per arm would let another writer in
between them.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.contracts import ClosureSearchSpec  # noqa: E402

#: The product CLI's own defaults, named here so an arm cannot silently differ
#: from a real run in a parameter nobody thought to pass.
BASELINE_TRIP_DURATION_P99_S = 3600
MAXIMUM_CANDIDATES = 100_000
MAXIMUM_DAILY_UNITS = 10_000


def peak_rss_bytes() -> int:
    """Peak RSS of this process AND its children.

    A SUMO campaign spends most of its memory in child processes, and
    `ru_maxrss` for children survives fork+exec on Linux, so the larger of the
    two is what the arm actually cost.
    """
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024
    return int(max(own, children)) * scale


def write_spec(spec: ClosureSearchSpec, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec.to_dict(), indent=1, sort_keys=True),
                    encoding="utf-8")
    return path


def build_arm(
    spec: ClosureSearchSpec,
    *,
    cost_ordered: bool,
    runs_root: Path,
    release_root: Path,
    daily_cost_cache: Path,
    study_provenance_key: str,
    objective_method: str,
    seed_workers: int = 1,
):
    """Return `(runner, screen_builder, cost_source)` for one arm.

    The ONLY difference between the two arms is `cost_ordered`. Everything
    else — spec, demand, policy, runner, screening — is identical by
    construction, which is what makes a difference in the outcome attributable
    to the execution order rather than to the setup.
    """
    from run_monthly_closure_search import (
        _bounded_exhaustive_builder,
        _cost_source_for,
        _independent_exhaustive_builder,
    )
    from traffic_sim.simulation.envelope import EnvelopePolicy
    from traffic_sim.simulation.independent_daily import (
        INDEPENDENT_DAILY_ENVELOPE_POLICY,
        IndependentDailyRunner,
    )
    from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner

    independent = spec.interday_policy == "independent_daily_reset_v1"
    if cost_ordered and not independent:
        raise ValueError(
            "cost-ordered execution prices a parent from its daily units; it "
            "requires the independent daily reset policy")

    resolved = MonthlyDemandResolverRunner(
        spec,
        runs_root=Path(runs_root),
        release_root=Path(release_root),
        build_missing=False,
        baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
        study_provenance_key=study_provenance_key,
        seed_workers=seed_workers,
        include_disruption=objective_method == "closure_cost_v1",
        envelope_policy=(INDEPENDENT_DAILY_ENVELOPE_POLICY if independent
                         else EnvelopePolicy()),
    )
    if independent:
        runner = IndependentDailyRunner(
            spec, daily_runner=resolved,
            cache_root=Path(daily_cost_cache).parent / "daily-results")

        def screen_builder(path):
            return _independent_exhaustive_builder(
                path,
                maximum_candidates=MAXIMUM_CANDIDATES,
                maximum_daily_units=MAXIMUM_DAILY_UNITS,
                baseline_trip_duration_p99_s=BASELINE_TRIP_DURATION_P99_S,
                preflight_report=None)
    else:
        runner = resolved

        def screen_builder(path):
            return _bounded_exhaustive_builder(
                path, maximum_candidates=MAXIMUM_CANDIDATES)

    cost_source = None
    if cost_ordered:
        cost_source = _cost_source_for(
            spec, runner, daily_cost_cache=daily_cost_cache)
    return runner, screen_builder, cost_source


def run_arm(
    spec: ClosureSearchSpec,
    policy,
    *,
    cost_ordered: bool,
    workspace_root: Path,
    runs_root: Path,
    release_root: Path,
    daily_cost_cache: Path,
    study_provenance_key: str,
    seed_workers: int = 1,
) -> dict[str, Any]:
    """Execute one arm end to end and report what it cost."""
    from traffic_sim.simulation.monthly_search import run_monthly_search

    runner, screen_builder, cost_source = build_arm(
        spec,
        cost_ordered=cost_ordered,
        runs_root=runs_root,
        release_root=release_root,
        daily_cost_cache=daily_cost_cache,
        study_provenance_key=study_provenance_key,
        objective_method=policy.objective_method,
        seed_workers=seed_workers,
    )
    started = time.monotonic()
    rss_before = peak_rss_bytes()
    try:
        result = run_monthly_search(
            spec, policy,
            runner=runner,
            screen_builder=screen_builder,
            root=Path(workspace_root),
            cost_source=cost_source,
        )
    finally:
        cleanup = getattr(runner, "cleanup", None)
        if callable(cleanup):
            cleanup()
    return {
        "arm": "cost_ordered" if cost_ordered else "exhaustive",
        "search_id": spec.search_id,
        "workspace": str(Path(workspace_root) / spec.search_id),
        "search_content_key": spec.content_key,
        "result": result,
        "wall_time_s": round(time.monotonic() - started, 3),
        # Reported as a pair: the peak BEFORE this arm is what the process had
        # already reached, so a reader can tell a genuine arm cost from a high
        # watermark the arm merely inherited.
        "peak_rss_bytes": peak_rss_bytes(),
        "peak_rss_before_bytes": rss_before,
        "daily_cost_cache_hits": int(getattr(cost_source, "cache_hits", 0)),
        "computed_daily_units": int(getattr(cost_source, "computed_units", 0)),
    }


def sumo_pilot_count(result: Mapping[str, Any]) -> int | None:
    """How many candidates the PILOT actually simulated.

    Cost-ordered runs publish it. An exhaustive run does not — its pilot count
    is the shortlist, which the screening record already states — so this
    returns None there and the caller reads the shortlist instead. Returning a
    guess would be worse than returning nothing.
    """
    execution = result.get("cost_ordered_execution")
    if not execution:
        return None
    return int(execution["cost_ordered_sumo_candidates"])
