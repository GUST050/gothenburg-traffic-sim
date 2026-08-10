"""Frozen scaling baseline for the rolling closure search.

Step 0 / PR A of `docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`:
make performance and semantics comparable BEFORE the code changes that follow.

WHAT IT MEASURES, SEPARATELY.  The plan is explicit that the phases must not be
pooled, because the optimisations that follow attack different ones:

    calendar_generation        materialising every parent ClosureSchedule
    daily_unit_decomposition   splitting parents into content-addressed units
    preflight                  the exact counter that replaces both, read-only
    deterministic_cost         closure_cost_v1 from routes and network
    cache_lookup               probing content-addressed daily cache entries
    sumo_wall_time             one daily unit's simulation
    peak_rss / artifact_bytes  memory and serialized size of the above

A phase whose inputs are absent is recorded as ``unmeasured`` WITH THE REASON.
It is never zero-filled and never estimated: this record's whole purpose is to
be the thing later work is compared against, and a fabricated baseline would
make a future change look like an improvement it did not earn.

DIAGNOSTIC, NOT RELEASE EVIDENCE.  The record says so in its own body, and it
opens no gate. It ranks nothing, verifies no closure and licenses no claim.

ISOLATION.  Every byte this tool writes while measuring goes into a private
temporary root that is removed afterwards. It never writes into `runs/`,
`sumo/`, `web/data/` or any campaign root, and it refuses to overwrite an
existing baseline — a frozen reference a flag can silently replace is not one.

REPRODUCIBILITY.  Timings are not reproducible; identities are. The record is
content-addressed over its INPUTS — cases, tool, sources, network, routes,
policies, interpreter and SUMO — so two runs on the same tree carry the same
``input_content_key`` and are therefore comparable, while their measured
numbers legitimately differ.

Run:  python3 tools/benchmark_closure_search_scaling.py [--repeats 5] [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.closure_calendar import generate_closure_schedules  # noqa: E402
from traffic_sim.core.contracts import ClosureSearchSpec                  # noqa: E402
from traffic_sim.simulation.closure_preflight import preflight            # noqa: E402
from traffic_sim.simulation.independent_daily import decompose_schedules  # noqa: E402

DEFAULT_OUT = ROOT / "validation" / "closure_search_scaling_baseline_v1.json"
SCHEMA = "closure_search_scaling_baseline_v1"

#: Minimum repetitions for a pure-performance phase, per the plan.
MINIMUM_REPEATS = 5

#: Sources whose bytes change what these numbers mean. Bound by digest so a
#: later comparison cannot silently be against different code.
BOUND_SOURCES = (
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/closure_preflight.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_search.py",
    "traffic_sim/simulation/envelope.py",
    "traffic_sim/simulation/closure_ranking.py",
    "traffic_sim/simulation/closure_teleport.py",
    "traffic_sim/simulation/closure_survivability.py",
    "run_monthly_closure_search.py",
    "tools/benchmark_closure_search_scaling.py",
)

#: Policy artifacts the search reads. Absent ones are recorded as absent.
BOUND_POLICIES = (
    "validation/monthly_search_policy_v1.json",
    "validation/monthly_search_policy_v2.json",
)

#: Demand route files the deterministic cost phase needs.
ROUTE_FILES = (
    "sumo/calibrated.rou.xml",
    "sumo/calibrated_v1.rou.xml",
    "sumo/calibrated_v2.rou.xml",
)

NETWORK_FILE = "sumo/net.net.xml"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _content_key(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _six_month_case(hours: int, case_id: str, note: str) -> dict[str, Any]:
    """One of the plan's two documented six-month cases, verbatim."""
    return {
        "case_id": case_id,
        "kind": "six_month_rolling",
        "note": note,
        "spec": {
            "search_id": case_id,
            "directed_edges": ["60786979_3575001205_0"],
            "demand_build_id": "scaling-baseline",
            "permitted_date_start": "2027-01-01",
            "permitted_date_end": "2027-06-30",
            "required_work_minutes": hours * 60,
            "max_consecutive_start_days": 90,
            "permitted_daily_band": {"earliest_start": "06:00",
                                     "latest_end": "18:00"},
            "source": "forecast",
            "allowed_weekdays": [0, 1, 2, 3, 4],
            "interday_policy": "independent_daily_reset_v1",
            "work_allocation_policy": "exact_equal_daily_v1",
            "period_comparison_policy": "rolling_period_v1",
            "objective_profile": "displaced_vehicles_and_detour_v1",
        },
    }


def _brute_force_case(case_id: str, *, days: int, work_minutes: int,
                      max_days: int, weekdays: Sequence[int],
                      blackouts: Sequence[str] = (),
                      note: str = "") -> dict[str, Any]:
    """A small case whose exhaustive enumeration is cheap enough to trust.

    These exist so the record carries SEMANTIC agreement, not only timing: for
    each one the benchmark asserts the preflight's counts equal the generator's
    and stores both. A speed-up that changed a count would be caught here.
    """
    from datetime import date, timedelta

    start = date(2027, 4, 5)
    return {
        "case_id": case_id,
        "kind": "small_brute_force",
        "note": note,
        "spec": {
            "search_id": case_id,
            "directed_edges": ["60786979_3575001205_0"],
            "demand_build_id": "scaling-baseline",
            "permitted_date_start": start.isoformat(),
            "permitted_date_end": (start + timedelta(days=days)).isoformat(),
            "required_work_minutes": work_minutes,
            "max_consecutive_start_days": max_days,
            "permitted_daily_band": {"earliest_start": "06:00",
                                     "latest_end": "18:00"},
            "source": "forecast",
            "allowed_weekdays": list(weekdays),
            "blackout_dates": list(blackouts),
            "interday_policy": "independent_daily_reset_v1",
            "work_allocation_policy": "exact_equal_daily_v1",
            "objective_profile": "displaced_vehicles_and_detour_v1",
        },
    }


def benchmark_cases() -> tuple[dict[str, Any], ...]:
    """The frozen case set: the plan's two six-month cases plus brute force."""
    return (
        _six_month_case(
            720, "six-month-720h",
            "plan section 2, runnable under the 10,000-unit cap"),
        _six_month_case(
            360, "six-month-360h",
            "plan section 2, valid but refused by the 10,000-unit cap"),
        _brute_force_case(
            "brute-force-single-day", days=6, work_minutes=480, max_days=1,
            weekdays=(0, 1, 2, 3, 4),
            note="one workday, weekdays only"),
        _brute_force_case(
            "brute-force-multi-day", days=13, work_minutes=480, max_days=4,
            weekdays=(0, 1, 2, 3, 4),
            note="rolling period crossing a weekend"),
        _brute_force_case(
            "brute-force-blackout", days=13, work_minutes=480, max_days=3,
            weekdays=(0, 1, 2, 3, 4, 5, 6),
            blackouts=("2027-04-09", "2027-04-14"),
            note="blackouts split the eligible run"),
        _brute_force_case(
            "brute-force-dst", days=10, work_minutes=480, max_days=3,
            weekdays=(0, 1, 2, 3, 4, 5, 6),
            note="spans the autumn DST transition date"),
    )


def _measure(label: str, call: Callable[[], Any], *, repeats: int
             ) -> tuple[dict[str, Any], Any]:
    """Time one phase `repeats` times and report median/p95 plus peak memory.

    The LAST result is returned so the caller can chain phases without paying
    for another run, and so recorded counts come from a measured invocation
    rather than a separate one that might differ.
    """
    durations: list[float] = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        durations.append(time.perf_counter() - started)
    tracemalloc.start()
    result = call()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ordered = sorted(durations)
    index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return {
        "phase": label,
        "status": "measured",
        "repeats": repeats,
        "median_s": round(statistics.median(ordered), 6),
        "p95_s": round(ordered[index], 6),
        "min_s": round(ordered[0], 6),
        "max_s": round(ordered[-1], 6),
        "peak_traced_bytes": int(peak),
    }, result


def _unmeasured(label: str, reason: str) -> dict[str, Any]:
    """A phase whose inputs are absent. Never zero-filled."""
    return {"phase": label, "status": "unmeasured", "reason": reason}


def _cache_probe_phase(unit_keys: Sequence[str], root: Path, *, repeats: int
                       ) -> dict[str, Any]:
    """Probe content-addressed cache paths inside an ISOLATED root.

    Half the keys are seeded so the measurement covers both a present and an
    absent entry; the real cache is never read or written.
    """
    for index, key in enumerate(unit_keys):
        if index % 2:
            continue
        entry = root / key[:2] / f"{key}.json"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("{}", encoding="utf-8")

    def probe() -> int:
        return sum(
            1 for key in unit_keys
            if (root / key[:2] / f"{key}.json").is_file()
        )

    record, hits = _measure("cache_lookup", probe, repeats=repeats)
    record["probed_entries"] = len(unit_keys)
    record["present_entries"] = hits
    record["basis"] = (
        "existence probe of the content-addressed daily cache path, run "
        "against a private temporary root seeded with half the entries")
    return record


#: Child program for per-case peak RSS. A resident-memory figure is only
#: meaningful in a process that did nothing else: the parent's peak is the
#: cumulative high-water mark of every case it has already run, which would
#: read as if one case needed all of it. PR C's exit gate is stated per case
#: ("the 720 h case under 64 MiB"), so the baseline has to measure per case.
_RSS_CHILD = """
import json, resource, sys
sys.path.insert(0, __ROOT__)

def peak_rss_bytes():
    # /proc VmHWM, NOT getrusage. Linux carries `ru_maxrss` across fork+exec
    # (it lives on signal_struct, which survives the mm swap), so a child
    # launched from a large parent reports the PARENT's high-water mark. That
    # is not a subtle inaccuracy: measured here, every case inherited 997 MiB
    # from the benchmark parent and the per-case figures were meaningless.
    # VmHWM comes from the new mm_struct and starts fresh at exec.
    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

from traffic_sim.core.contracts import ClosureSearchSpec
spec = ClosureSearchSpec.from_dict(json.loads(sys.argv[2]))
mode = sys.argv[1]
if mode == "materialize":
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    from traffic_sim.simulation.independent_daily import decompose_schedules
    schedules = generate_closure_schedules(spec)
    units, parents = decompose_schedules(spec, schedules)
    kept = (len(schedules), len(units), len(parents))
else:
    from traffic_sim.simulation.closure_preflight import preflight
    report = preflight(spec)
    kept = (report.parent_schedule_count, report.unique_daily_unit_count, 0)
print(json.dumps({"counts": kept, "peak_rss_bytes": peak_rss_bytes()}))
"""


def _isolated_peak_rss(spec_payload: Mapping[str, Any], mode: str
                       ) -> dict[str, Any]:
    """Peak RSS of one case in a fresh interpreter, or an honest failure."""
    # Simple placeholder substitution: the child source contains JSON
    # braces, so str.format would try to read them as fields.
    program = _RSS_CHILD.replace("__ROOT__", repr(str(ROOT)))
    try:
        completed = subprocess.run(
            [sys.executable, "-c", program, mode, _canonical(spec_payload)],
            capture_output=True, text=True, timeout=1800, check=False, cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "unmeasured", "reason": f"child failed: {error}"}
    if completed.returncode != 0:
        return {"status": "unmeasured",
                "reason": f"child exited {completed.returncode}: "
                          f"{completed.stderr.strip()[-200:]}"}
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "unmeasured", "reason": "child produced no record"}
    return {
        "status": "measured",
        "peak_rss_bytes": int(payload["peak_rss_bytes"]),
        "basis": ("VmHWM of a fresh interpreter that imported the modules and "
                  "ran only this case; getrusage is deliberately not used "
                  "because Linux carries ru_maxrss across fork+exec"),
    }


def _process_peak_rss_bytes() -> int:
    """This process's own high-water mark, from /proc when available."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _sumo_version() -> str | None:
    binary = shutil.which("sumo")
    if binary is None:
        return None
    try:
        completed = subprocess.run([binary, "--version"], capture_output=True,
                                   text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    first = completed.stdout.strip().splitlines()
    return first[0] if first else None


def _identities() -> dict[str, Any]:
    routes = {name: _sha256(ROOT / name) for name in ROUTE_FILES}
    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
        },
        "sumo_version": _sumo_version(),
        "network": {
            "path": NETWORK_FILE,
            "sha256": _sha256(ROOT / NETWORK_FILE),
        },
        "routes": routes,
        "policies": {
            name: _sha256(ROOT / name) for name in BOUND_POLICIES
        },
        "sources": {
            name: _sha256(ROOT / name) for name in BOUND_SOURCES
        },
    }


def _artifact_bytes(schedules: Sequence[Any], units: Sequence[Any]) -> dict[str, int]:
    """Serialized size of what the current path materializes.

    This is the quantity PR C's streaming ledgers must reduce, so it belongs in
    the baseline even though nothing is written to disk here.
    """
    return {
        "parent_schedules_json_bytes": len(
            _canonical([item.to_dict() for item in schedules]).encode("utf-8")),
        "daily_units_json_bytes": len(
            _canonical([item.to_dict() for item in units]).encode("utf-8")),
    }


def run_case(case: Mapping[str, Any], *, repeats: int, scratch: Path,
             identities: Mapping[str, Any]) -> dict[str, Any]:
    """Measure every phase this environment can measure, for one case."""
    spec = ClosureSearchSpec.from_dict(dict(case["spec"]))
    phases: list[dict[str, Any]] = []

    calendar_record, schedules = _measure(
        "calendar_generation", lambda: generate_closure_schedules(spec),
        repeats=repeats)
    calendar_record["parent_schedule_count"] = len(schedules)
    phases.append(calendar_record)

    decompose_record, decomposed = _measure(
        "daily_unit_decomposition",
        lambda: decompose_schedules(spec, schedules), repeats=repeats)
    units, parents = decomposed
    decompose_record["unique_daily_unit_count"] = len(units)
    phases.append(decompose_record)

    preflight_record, report = _measure(
        "preflight", lambda: preflight(spec), repeats=repeats)
    preflight_record["parent_schedule_count"] = report.parent_schedule_count
    preflight_record["unique_daily_unit_count"] = report.unique_daily_unit_count
    phases.append(preflight_record)

    # SEMANTIC agreement, recorded beside the timings. A later change that made
    # a phase faster while changing a count would fail here, not silently pass
    # as an improvement.
    agreement = {
        "parent_schedule_count_matches":
            report.parent_schedule_count == len(schedules),
        "unique_daily_unit_count_matches":
            report.unique_daily_unit_count == len(units),
        "generator_parent_schedule_count": len(schedules),
        "generator_unique_daily_unit_count": len(units),
        "preflight_parent_schedule_count": report.parent_schedule_count,
        "preflight_unique_daily_unit_count": report.unique_daily_unit_count,
    }

    case_scratch = scratch / str(case["case_id"])
    case_scratch.mkdir(parents=True, exist_ok=True)
    unit_keys = [
        hashlib.sha256(_canonical(unit.identity).encode("utf-8")).hexdigest()
        for unit in units
    ]
    if unit_keys:
        phases.append(_cache_probe_phase(unit_keys, case_scratch,
                                         repeats=repeats))
    else:
        phases.append(_unmeasured(
            "cache_lookup", "this case enumerates no daily unit to probe"))

    missing_routes = sorted(
        name for name, digest in identities["routes"].items() if digest is None)
    if missing_routes:
        reason = (
            "calibrated demand routes are absent "
            f"({', '.join(missing_routes)}); closure_cost_v1 is computed from "
            "the q10/q50/q90 route files and the network, so it cannot be "
            "measured without them")
        phases.append(_unmeasured("deterministic_cost", reason))
        phases.append(_unmeasured("sumo_wall_time", reason))
    else:
        phases.append(_unmeasured(
            "deterministic_cost",
            "route files are present but this baseline tool does not yet "
            "invoke the disruption provider; PR D extracts it as a "
            "process-free provider and this phase is measured then"))
        phases.append(_unmeasured(
            "sumo_wall_time",
            "measuring one daily unit's SUMO wall time creates simulation "
            "outcomes; it is deliberately excluded from this read-only "
            "baseline and belongs to the PR G worker benchmark"))

    return {
        "case_id": case["case_id"],
        "kind": case["kind"],
        "note": case["note"],
        "case_content_key": _content_key(case["spec"]),
        "spec": dict(case["spec"]),
        "counts": {
            "eligible_workday_count": report.eligible_workday_count,
            "valid_workday_counts": list(report.valid_workday_counts),
            "parent_schedule_count": len(schedules),
            "parent_schedules_by_workday_count": {
                str(key): value for key, value
                in sorted(report.parent_schedules_by_workday_count.items())},
            "unique_daily_unit_count": len(units),
            "parent_to_unit_edges": sum(len(ids) for ids in parents.values()),
            "units_outside_demand_year": report.units_outside_demand_year,
            "size_class": report.size_class,
        },
        "semantic_agreement": agreement,
        "artifact_bytes": _artifact_bytes(schedules, units),
        "peak_rss": {
            # The two paths PR C will change, each measured alone so the "under
            # 64 MiB" gate has something per-case to be compared against.
            "materialize_calendar_and_units":
                _isolated_peak_rss(case["spec"], "materialize"),
            "preflight_only": _isolated_peak_rss(case["spec"], "preflight"),
        },
        "phases": phases,
    }


def build_record(*, repeats: int) -> dict[str, Any]:
    if repeats < MINIMUM_REPEATS:
        raise SystemExit(
            f"--repeats must be at least {MINIMUM_REPEATS}; the plan requires "
            f"every pure performance case to run at least that many times")
    identities = _identities()
    cases = benchmark_cases()
    results = []
    with tempfile.TemporaryDirectory(prefix="closure-scaling-baseline-") as tmp:
        scratch = Path(tmp)
        for case in cases:
            print(f"  {case['case_id']} …", flush=True)
            results.append(run_case(case, repeats=repeats, scratch=scratch,
                                    identities=identities))
    # Cumulative across every case this process ran — explicitly NOT a
    # per-case figure; those live under each case's "peak_rss".
    peak_rss_bytes = _process_peak_rss_bytes()

    record = {
        "schema": SCHEMA,
        "kind": "closure_search_scaling_baseline",
        "stage": "closure_search_scaling_plan_step_0",
        "evidence_class": "diagnostic_baseline",
        "claim_boundary": {
            "release_evidence": False,
            "opens_no_gate": True,
            "reason": (
                "a performance reference frozen before the scaling changes; it "
                "ranks no closure, verifies no schedule and licenses no claim"),
        },
        "measured_at": time.strftime("%Y-%m-%d"),
        "repeats": repeats,
        "identities": identities,
        "process_peak_rss_bytes": int(peak_rss_bytes),
        "process_peak_rss_note": (
            "cumulative high-water mark of the whole benchmark process across "
            "every case; per-case figures are under cases[].peak_rss"),
        "isolation": {
            "scratch_root": "private temporary directory, removed after the run",
            "wrote_into_evidence_roots": False,
            "roots_never_touched": ["runs/", "sumo/", "web/data/",
                                    "validation/ (except this record)"],
        },
        "cases": results,
    }
    # Content-addressed over INPUTS only: timings legitimately differ between
    # runs, identities must not. Two records sharing this key are comparable.
    record["input_content_key"] = _content_key({
        "schema": SCHEMA,
        "identities": identities,
        "cases": [case["spec"] for case in cases],
        "repeats": repeats,
    })
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=MINIMUM_REPEATS,
                        help=f"repetitions per phase (minimum {MINIMUM_REPEATS})")
    parser.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    parser.add_argument("--stdout", action="store_true",
                        help="print the record instead of writing it")
    args = parser.parse_args(argv)

    # Destination checked BEFORE any measurement. Refusing after a six-month
    # case has already run wastes minutes to reach a decision that was
    # knowable at once, and leaves the user staring at a progress log that
    # ends in an error. (It also made one test take 650 of the file's 660
    # seconds, which is how the fail-late ordering was noticed.)
    out = Path(args.out)
    if not args.stdout and out.exists():
        raise SystemExit(
            f"refusing to overwrite the frozen baseline at {out}; remove it "
            f"deliberately to re-freeze")

    record = build_record(repeats=args.repeats)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.stdout:
        print(text)
        return 0

    if out.exists():
        raise SystemExit(
            f"a baseline appeared at {out} while measuring; refusing to "
            f"overwrite it")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"\nwrote {out}")
    print("input content key:", record["input_content_key"])
    for case in record["cases"]:
        counts = case["counts"]
        timings = {phase["phase"]: phase for phase in case["phases"]}
        calendar = timings["calendar_generation"]
        pre = timings["preflight"]
        print(f"  {case['case_id']:26s} parents={counts['parent_schedule_count']:6d} "
              f"units={counts['unique_daily_unit_count']:6d} "
              f"calendar_p95={calendar['p95_s']:.3f}s "
              f"preflight_p95={pre['p95_s']:.4f}s "
              f"agree={case['semantic_agreement']['parent_schedule_count_matches']}"
              f"/{case['semantic_agreement']['unique_daily_unit_count_matches']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
