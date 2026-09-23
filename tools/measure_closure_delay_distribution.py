"""Per-vehicle added-delay distribution for chosen closure candidates.

WHY THIS EXISTS.  `disruption._report` builds one added-seconds value per
affected vehicle, sums it into `added_vehicle_hours`, and drops the list.
The total answers "how expensive is this closure"; it cannot answer "how
many drivers lose how long", which is what a reader looking at two
candidates actually wants to compare.

HOW IT STAYS HONEST.  It does not reimplement the costing. It runs the
production `ArchiveDisruptionProvider` with the cache disabled and observes
`_report`'s own inputs, recomputing each movement's added seconds with the
same pricer, the same floor-at-zero rule and the same multiplicity. The
recomputed q50 total is compared against the report the production code
returned. The variant records are then reduced with the search's recorded
objective and compared against the published cost ledger. For legacy
three-variant searches, the q50 histogram and worst-variant ranked total
are explicitly separate quantities. Any disagreement is fatal.

Diagnostic only: `release_evidence` is false and no gate reads it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.contracts import (  # noqa: E402
    ClosureSchedule,
    ClosureSearchSpec,
)
from traffic_sim.simulation import closure_ledgers  # noqa: E402
from traffic_sim.simulation import disruption as disruption_module  # noqa: E402
from traffic_sim.simulation.deterministic_disruption import (  # noqa: E402
    ArchiveDisruptionProvider,
    ArchiveInputs,
    NetworkCostModel,
    sum_daily_disruption,
)
from traffic_sim.simulation.closure_ranking import (  # noqa: E402
    LEGACY_WORST_COST_OBJECTIVE,
    reduce_variant_cost,
)
from traffic_sim.analysis.closure_delay_run import (  # noqa: E402
    load_archive_index,
    load_search_result,
    resolve_archive,
)

SCHEMA = "closure_delay_distribution_v1"
#: Tolerance when comparing a recomputed total against the production
#: report. Both are sums of the same floats in the same order, so the only
#: expected difference is the report's own 4-decimal rounding.
HOURS_TOLERANCE = 5e-5


def _capture_report(store: list[list[float]]):
    """Wrap `_report` so each movement's added seconds is recorded.

    The wrapper recomputes with the SAME pricer calls and the SAME rule as
    the function it wraps, then delegates so the caller still receives the
    production report unchanged.
    """
    original = disruption_module._report

    def spy(**kwargs: Any) -> dict:
        pricer = kwargs["pricer"]
        od_counts: Counter = kwargs["od_counts"]
        seconds_for_variant: list[float] = []
        for movement, multiplicity in od_counts.items():
            (baseline_pair, detour_pair, banned, _relocation,
             endpoint_seconds, _endpoint_metres) = movement
            baseline = pricer.path_cost(*baseline_pair, frozenset())
            if baseline is None:
                continue
            detour = pricer.path_cost(*detour_pair, banned)
            if detour is None:
                continue
            seconds = max(detour[0] - baseline[0] + endpoint_seconds, 0.0)
            seconds_for_variant.extend([seconds] * multiplicity)
        store.append(seconds_for_variant)
        return original(**kwargs)

    return original, spy


def _unit_schedules(
    workspace: Path, parent_id: str,
) -> list[ClosureSchedule]:
    units = {row["unit_id"]: row for row in
             closure_ledgers.iter_unit_rows(workspace / "ledgers")}
    order: Sequence[str] = ()
    for row in closure_ledgers.iter_parent_unit_rows(workspace / "ledgers"):
        if row["parent_schedule_id"] == parent_id:
            order = row["unit_ids"]
            break
    if not order:
        raise SystemExit(f"no daily units recorded for {parent_id}")
    return [ClosureSchedule.from_dict(units[uid]["schedule"]) for uid in order]


def _published_hours(workspace: Path, parent_id: str) -> float | None:
    ledger = workspace / "artifacts" / "cost-ledger.json"
    if not ledger.is_file():
        return None
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    for entry in payload.get("costs", ()):
        if entry.get("candidate_id") == parent_id:
            return float(entry["cost"]["added_vehicle_hours"])
    return None


def measure(
    workspace: Path, parent: ClosureSchedule, runs_root: Path,
) -> dict[str, Any]:
    result = load_search_result(workspace)
    spec = ClosureSearchSpec.from_dict(result["closure_search_spec"])
    if parent.search_content_key != spec.content_key:
        raise SystemExit("candidate does not belong to the verified search")
    objective_method = str(result.get("objective_method") or
        (result.get("policy") or {}).get("objective_method") or
        LEGACY_WORST_COST_OBJECTIVE)
    index = load_archive_index(
        spec, release_root=runs_root / "monthly-demand-releases",
        runs_root=runs_root)
    network = NetworkCostModel()

    captured: list[list[float]] = []
    seconds_by_variant: dict[str, list[float]] = {}
    daily_records: list[Sequence[Mapping[str, Any]]] = []
    providers: dict[Path, ArchiveDisruptionProvider] = {}
    archives: list[str] = []
    per_day: list[dict[str, Any]] = []
    original, spy = _capture_report(captured)
    disruption_module._report = spy
    try:
        for unit in _unit_schedules(workspace, parent.schedule_id):
            archive = resolve_archive(
                spec, unit, index, runs_root=runs_root,
                network_sha256=network.network_sha256)
            archives.append(str(archive))
            provider = providers.get(archive)
            if provider is None:
                provider = ArchiveDisruptionProvider(
                    spec, archive=archive, network=network, cache=None,
                    inputs=ArchiveInputs.from_archive(archive),
                    reuse_parsed_routes=True)
                providers[archive] = provider
            before = len(captured)
            records = provider.disruption(unit)
            batches = captured[before:]
            if len(batches) != len(records):
                raise SystemExit(
                    f"{unit.first_work_date}: per-variant sample capture is incomplete")
            daily_records.append(records)
            for record, samples in zip(records, batches):
                variant = str(record["demand_variant"])
                observed = sum(samples) / 3600.0
                reported = float(record["added_vehicle_hours"])
                if abs(observed - reported) > HOURS_TOLERANCE:
                    raise SystemExit(
                        f"{unit.first_work_date} {variant}: observed "
                        f"{observed:.6f} h does not reproduce the "
                        f"reported {reported:.6f} h")
                seconds_by_variant.setdefault(variant, []).extend(samples)
            q50_record = next((record for record in records
                               if record["demand_variant"] == "q50"), None)
            if q50_record is None:
                raise SystemExit(f"{unit.first_work_date}: q50 demand is missing")
            per_day.append({
                "work_date": unit.first_work_date,
                "demand_variant": "q50",
                "added_vehicle_hours": q50_record["added_vehicle_hours"],
                "vehicles_affected": q50_record["vehicles_affected"],
                "vehicles_no_detour": q50_record["vehicles_no_detour"],
            })
    finally:
        disruption_module._report = original

    combined = sum_daily_disruption(daily_records)
    ranked_cost = reduce_variant_cost(parent.schedule_id, combined,
                                      objective_method)
    seconds = seconds_by_variant["q50"]
    total_hours = sum(seconds) / 3600.0
    q50_report = next(record for record in combined
                      if record["demand_variant"] == "q50")
    if abs(total_hours - float(q50_report["added_vehicle_hours"])) > (
            HOURS_TOLERANCE * len(daily_records)):
        raise SystemExit("q50 samples do not reproduce the aggregated q50 cost")
    published = _published_hours(workspace, parent.schedule_id)
    if published is None:
        raise SystemExit("candidate has no published cost-ledger entry")
    if abs(ranked_cost.added_vehicle_hours - published) > HOURS_TOLERANCE:
        raise SystemExit(
            f"{parent.schedule_id}: reduced {ranked_cost.added_vehicle_hours:.4f} h does not "
            f"match the published {published:.4f} h")
    ordered = sorted(seconds)
    count = len(ordered)

    def quantile(q: float) -> float:
        return round(ordered[min(count - 1, int(q * count))], 2) if count else 0.0

    return {
        "schedule_id": parent.schedule_id,
        "first_work_date": parent.first_work_date,
        "day_count": parent.day_count,
        "demand_variant": "q50",
        "demand_archives": archives,
        "objective_method": objective_method,
        "per_day": per_day,
        "vehicles_with_added_delay": count,
        "added_vehicle_hours": round(total_hours, 4),
        "ranked_added_vehicle_hours": ranked_cost.added_vehicle_hours,
        "published_added_vehicle_hours": published,
        "seconds": {
            "min": round(ordered[0], 2) if count else 0.0,
            "p25": quantile(0.25),
            "median": quantile(0.50),
            "p75": quantile(0.75),
            "p90": quantile(0.90),
            "p99": quantile(0.99),
            "max": round(ordered[-1], 2) if count else 0.0,
            "mean": round(sum(ordered) / count, 2) if count else 0.0,
        },
        "samples": ordered,
    }


def histogram(samples: Sequence[float], width: float,
              top: float) -> list[dict[str, Any]]:
    bins: Counter = Counter()
    for value in samples:
        index = min(int(value // width), int(top // width))
        bins[index] += 1
    rows = []
    last = int(top // width)
    for index in range(last + 1):
        lower = index * width
        rows.append({
            "from_seconds": round(lower, 2),
            "to_seconds": None if index == last else round(lower + width, 2),
            "vehicles": bins.get(index, 0),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--start-date", action="append", required=True,
                        help="candidate first work date; repeatable")
    parser.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--bin-seconds", type=float, default=5.0)
    parser.add_argument("--top-seconds", type=float, default=60.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    parents = {p.first_work_date: p for p in
               closure_ledgers.iter_parent_schedules(args.workspace / "ledgers")}
    results = []
    for start in args.start_date:
        parent = parents.get(start)
        if parent is None:
            raise SystemExit(f"no candidate starts on {start}")
        print(f"pricing {start} ({parent.schedule_id}) ...", flush=True)
        measured = measure(args.workspace, parent, args.runs_root)
        measured["histogram"] = histogram(
            measured["samples"], args.bin_seconds, args.top_seconds)
        del measured["samples"]
        results.append(measured)
        print(f"  {measured['vehicles_with_added_delay']} fordon, "
              f"{measured['added_vehicle_hours']} h, "
              f"median {measured['seconds']['median']} s", flush=True)

    payload = {
        "schema": SCHEMA,
        "release_evidence": False,
        "bin_seconds": args.bin_seconds,
        "top_seconds": args.top_seconds,
        "basis": "production ArchiveDisruptionProvider, cache disabled; "
                 "q50 per-movement seconds recomputed with the same pricer "
                 "and checked against the q50 report; all variant reports "
                 "are reduced with the search objective and checked against "
                 "the published cost ledger",
        "workspace": str(args.workspace),
        "candidates": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
