"""PR C comparison record: streaming ledgers against the frozen v1 baseline.

`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`, PR C.

WHY A SECOND ARTIFACT.  `validation/closure_search_scaling_baseline_v1.json`
is PR A's immutable historical evidence.  PR C edits `closure_calendar.py`,
`independent_daily.py` and `monthly_search.py`, so that record will report
source drift from now on — which is correct and is the point of binding source
digests.  It is NOT rewritten.  This tool writes a separate diagnostic record
that names the baseline, quotes its `input_content_key`, and states plainly
which of its numbers are comparable and which are not.

WHAT IS COMPARED, AND ON WHAT.  Each frozen case is run two ways in a FRESH
CHILD INTERPRETER — once through the v1 materialising path
(`generate_closure_schedules` + `decompose_schedules`) and once through the
streaming ledger writer.  Both are measured here, on this machine, in the same
session.  That pairing is what carries the claim: the frozen baseline was taken
on Darwin/arm64, and a resident-memory figure from one operating system is not
evidence about another.  The cross-machine gate is reported honestly as open
unless this host is the baseline's host.

SEMANTIC EQUALITY IS PART OF THE RECORD.  A memory win that changed a count, a
schedule ID or a parent-to-unit relationship is not a win.  Every case stores
whether the streamed parents, unique units and relationships match the v1
decomposition exactly, plus the ledger digests that make the answer checkable
later.

CAPS ARE NOT TOUCHED.  The 10,000-unit cap still refuses what it refused; each
case records whether it would be refused, so the 360-hour case remains visibly
over budget while no longer failing for want of memory.

ISOLATION.  Everything written while measuring goes into a private temporary
root that is removed afterwards.  The tool refuses to overwrite an existing
record unless asked, and it never runs SUMO or the deterministic-cost probe —
those belong to the frozen baseline and to PR D.

Run:  python3 tools/benchmark_closure_streaming.py [--repeats 5] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_closure_search_scaling import (   # noqa: E402
    BOUND_POLICIES,
    MINIMUM_REPEATS,
    _content_key,
    _sha256,
    benchmark_cases,
)
from traffic_sim.simulation.closure_ledgers import (   # noqa: E402
    LEDGER_SCHEMA,
    LEDGER_VERSION,
)

DEFAULT_OUT = ROOT / "validation" / "closure_search_streaming_v1.json"
SCHEMA = "closure_search_streaming_v1"

#: PR A's frozen record. Named and digested, never rewritten.
BASELINE_RECORD = ROOT / "validation" / "closure_search_scaling_baseline_v1.json"
#: The baseline's own input identity, quoted so a reader can see exactly which
#: frozen record these numbers are being set against.
BASELINE_INPUT_CONTENT_KEY = (
    "33de490e76b711207732a7405ef1235cb753118876f5a895c88959351e56f3d8")

#: The plan's PR C exit gate for the 720-hour case.
STREAMING_PEAK_RSS_GATE_BYTES = 64 * 1024 * 1024

#: The unit cap the search refuses at. PR C does not move it.
MAXIMUM_DAILY_UNITS = 10_000

#: Sources whose bytes change what these numbers mean. The streaming path adds
#: the ledger module to the baseline's set.
BOUND_SOURCES = (
    "traffic_sim/core/closure_calendar.py",
    "traffic_sim/core/contracts.py",
    "traffic_sim/simulation/closure_ledgers.py",
    "traffic_sim/simulation/closure_preflight.py",
    "traffic_sim/simulation/independent_daily.py",
    "traffic_sim/simulation/monthly_search.py",
    "run_monthly_closure_search.py",
    "tools/benchmark_closure_search_scaling.py",
    "tools/benchmark_closure_streaming.py",
)


#: The child program. Both modes run in a fresh interpreter for the same
#: reason the baseline does it: `ru_maxrss` on Linux survives fork+exec, and a
#: parent that has already run five cases would lend its high-water mark to the
#: sixth. `VmHWM` comes from the new address space and starts at exec.
_CHILD = r"""
import json, os, resource, shutil, sys, tempfile, time
sys.path.insert(0, __ROOT__)


def peak_rss_bytes():
    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


from traffic_sim.core.contracts import ClosureSearchSpec

mode = sys.argv[1]
spec = ClosureSearchSpec.from_dict(json.loads(sys.argv[2]))
repeats = int(sys.argv[3])

durations = []
payload = {}

# Imports FIRST, then the import-time high-water mark, then the work. Both
# paths reach `independent_daily`, which imports `finalist_decision`, which
# imports scipy; that fixed cost belongs to the interpreter, not to the
# enumeration, and reporting it inside the enumeration's figure would credit
# the streaming path with removing memory it never allocated.
if mode == "materialize":
    from traffic_sim.core.closure_calendar import generate_closure_schedules
    from traffic_sim.simulation.independent_daily import decompose_schedules
    import_peak = peak_rss_bytes()
    schedules = units = parents = None
    for _ in range(repeats):
        # Release the previous repetition BEFORE building the next one, or the
        # peak would show two whole object graphs and overstate what v1 costs.
        schedules = units = parents = None
        started = time.perf_counter()
        schedules = generate_closure_schedules(spec)
        units, parents = decompose_schedules(spec, schedules)
        durations.append(time.perf_counter() - started)
    payload = {
        "parent_schedule_count": len(schedules),
        "unique_daily_unit_count": len(units),
        "parent_unit_edge_count": sum(len(value) for value in parents.values()),
        "parent_schedules_json_bytes": len(json.dumps(
            [item.to_dict() for item in schedules], separators=(",", ":"))),
        # The v1 unit carries the reverse graph; its serialized size is the
        # thing the streaming unit ledger is being compared against.
        "daily_units_json_bytes": len(json.dumps(
            [item.to_dict() for item in units], separators=(",", ":"))),
        "reverse_parent_reference_count": sum(
            len(item.parent_schedule_ids) for item in units),
    }
    # Keep the last decomposition alive across the measurement so the peak
    # reflects holding the graph, which is what v1 actually does.
    payload["held"] = bool(schedules and units and parents)
elif mode == "stream":
    from traffic_sim.core.closure_calendar import iter_closure_schedules
    from traffic_sim.simulation.closure_ledgers import write_ledgers
    from traffic_sim.simulation.independent_daily import daily_unit_records
    import_peak = peak_rss_bytes()
    root = tempfile.mkdtemp(prefix="closure-streaming-")
    try:
        for index in range(repeats):
            target = os.path.join(root, "run-%d" % index)
            started = time.perf_counter()
            manifest = write_ledgers(
                target, spec, iter_closure_schedules(spec),
                unit_records=daily_unit_records,
                provenance={"benchmark": "closure_search_streaming_v1"})
            durations.append(time.perf_counter() - started)
            # Removed AFTER the timing, every repetition: the six-month cases
            # write over 100 MB of ledgers each and this runs on a fixed disk
            # allowance.
            shutil.rmtree(target, ignore_errors=True)
        payload = {
            "parent_schedule_count": manifest.parent_count,
            "unique_daily_unit_count": manifest.unique_unit_count,
            "parent_unit_edge_count": manifest.parent_unit_edge_count,
            "ledger_manifest_content_key": manifest.key,
            "ledger_files": {
                name: {"rows": item.rows, "bytes": item.bytes,
                       "sha256": item.sha256}
                for name, item in sorted(manifest.files.items())
            },
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
elif mode == "preflight":
    from traffic_sim.simulation.closure_preflight import preflight
    import_peak = peak_rss_bytes()
    for _ in range(repeats):
        started = time.perf_counter()
        report = preflight(spec)
        durations.append(time.perf_counter() - started)
    payload = {
        "parent_schedule_count": report.parent_schedule_count,
        "unique_daily_unit_count": report.unique_daily_unit_count,
    }
else:
    raise SystemExit("unknown mode: %s" % mode)

print(json.dumps({
    "durations_s": durations,
    "peak_rss_bytes": peak_rss_bytes(),
    "import_peak_rss_bytes": import_peak,
    "imported_scipy": "scipy" in sys.modules,
    "payload": payload,
}))
"""


def _statistics(durations: list[float]) -> dict[str, float]:
    import statistics as stats

    ordered = sorted(durations)
    index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return {
        "median_s": round(stats.median(ordered), 6),
        "p95_s": round(ordered[index], 6),
        "min_s": round(ordered[0], 6),
        "max_s": round(ordered[-1], 6),
    }


def _run_child(mode: str, spec_payload: Mapping[str, Any], repeats: int,
               *, timeout: int) -> dict[str, Any]:
    """One measured mode in a fresh interpreter, or an honest failure."""
    program = _CHILD.replace("__ROOT__", repr(str(ROOT)))
    argument = json.dumps(spec_payload, sort_keys=True, separators=(",", ":"))
    try:
        completed = subprocess.run(
            [sys.executable, "-c", program, mode, argument, str(repeats)],
            capture_output=True, text=True, timeout=timeout, check=False,
            cwd=ROOT)
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "unmeasured", "phase": mode,
                "reason": f"child failed: {error}"}
    if completed.returncode != 0:
        return {"status": "unmeasured", "phase": mode,
                "reason": (f"child exited {completed.returncode}: "
                           f"{completed.stderr.strip()[-300:]}")}
    try:
        raw = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "unmeasured", "phase": mode,
                "reason": "child produced no record"}
    peak = int(raw["peak_rss_bytes"])
    imports = int(raw["import_peak_rss_bytes"])
    record = {
        "phase": mode,
        "status": "measured",
        "repeats": repeats,
        "peak_rss_bytes": peak,
        "import_peak_rss_bytes": imports,
        # The figure that is actually about the enumeration. Both paths import
        # the same modules, so subtracting the identical fixed cost compares
        # the algorithms rather than the interpreter.
        "peak_rss_over_imports_bytes": max(0, peak - imports),
        "imported_scipy": bool(raw.get("imported_scipy")),
        "peak_rss_basis": (
            "high-water RSS of a fresh interpreter that imported the modules "
            "and ran only this case and mode; /proc VmHWM when available, "
            "otherwise getrusage normalized for the host"),
        **_statistics([float(value) for value in raw["durations_s"]]),
    }
    record.update(raw["payload"])
    record.pop("held", None)
    return record


def _semantic_agreement(materialize: Mapping[str, Any],
                        stream: Mapping[str, Any]) -> dict[str, Any]:
    """Did the streaming path enumerate the same search?

    Counts only — the byte-level equality of schedules, IDs, order and
    parent-to-unit relationships is pinned by the test suite
    (`tests/test_closure_ledgers.py`, `tests/test_closure_calendar.py`), which
    is where an exactness claim belongs.  A benchmark record should not be the
    only place a correctness property is checked.
    """
    if materialize.get("status") != "measured" or stream.get("status") != "measured":
        return {"status": "unavailable",
                "reason": "one of the two paths was not measured"}
    fields = ("parent_schedule_count", "unique_daily_unit_count",
              "parent_unit_edge_count")
    matches = {
        f"{name}_matches": materialize.get(name) == stream.get(name)
        for name in fields
    }
    return {
        "status": "measured",
        "materialize": {name: materialize.get(name) for name in fields},
        "stream": {name: stream.get(name) for name in fields},
        **matches,
        "all_match": all(matches.values()),
        "exactness_pinned_by": [
            "tests/test_closure_ledgers.py",
            "tests/test_closure_calendar.py",
        ],
    }


def run_case(case: Mapping[str, Any], *, repeats: int,
             timeout: int) -> dict[str, Any]:
    spec_payload = dict(case["spec"])
    materialize = _run_child("materialize", spec_payload, repeats,
                             timeout=timeout)
    stream = _run_child("stream", spec_payload, repeats, timeout=timeout)
    preflight = _run_child("preflight", spec_payload, repeats, timeout=timeout)

    units = stream.get("unique_daily_unit_count")
    if units is None:
        units = preflight.get("unique_daily_unit_count")
    record = {
        "case_id": case["case_id"],
        "kind": case["kind"],
        "note": case.get("note", ""),
        "spec": spec_payload,
        "case_content_key": _content_key(spec_payload),
        "phases": [materialize, stream, preflight],
        "semantic_agreement": _semantic_agreement(materialize, stream),
        "resource_budget": {
            "maximum_daily_units": MAXIMUM_DAILY_UNITS,
            "unique_daily_unit_count": units,
            "refused_by_unit_cap": (
                None if units is None else bool(units > MAXIMUM_DAILY_UNITS)),
            "cap_changed_by_pr_c": False,
        },
    }
    if (materialize.get("status") == "measured"
            and stream.get("status") == "measured"):
        record["peak_rss_reduction"] = {
            "materialize_bytes": materialize["peak_rss_bytes"],
            "stream_bytes": stream["peak_rss_bytes"],
            "ratio": round(
                materialize["peak_rss_bytes"]
                / max(stream["peak_rss_bytes"], 1), 3),
            "materialize_over_imports_bytes":
                materialize["peak_rss_over_imports_bytes"],
            "stream_over_imports_bytes":
                stream["peak_rss_over_imports_bytes"],
            "over_imports_ratio": round(
                materialize["peak_rss_over_imports_bytes"]
                / max(stream["peak_rss_over_imports_bytes"], 1), 3),
            "measured_on_the_same_host_in_the_same_run": True,
        }
    return record


def _baseline_binding() -> dict[str, Any]:
    """Name the frozen baseline and state what is and is not comparable."""
    baseline_digest = _sha256(BASELINE_RECORD)
    stored_key = None
    if BASELINE_RECORD.is_file():
        try:
            stored_key = json.loads(
                BASELINE_RECORD.read_text(encoding="utf-8")
            ).get("input_content_key")
        except (OSError, ValueError):
            stored_key = None
    return {
        "path": str(BASELINE_RECORD.relative_to(ROOT)),
        "sha256": baseline_digest,
        "declared_input_content_key": BASELINE_INPUT_CONTENT_KEY,
        "stored_input_content_key": stored_key,
        "input_content_key_matches": stored_key == BASELINE_INPUT_CONTENT_KEY,
        "rewritten_by_this_tool": False,
        "expected_source_drift": (
            "PR C edits closure_calendar.py, independent_daily.py and "
            "monthly_search.py, so the frozen baseline's bound source digests "
            "no longer match this tree. That drift is intended evidence, not "
            "a defect; the baseline is history and stays unmodified."),
        "external_phases_not_rerun": [
            "deterministic_cost", "sumo_wall_time",
        ],
        "external_phase_note": (
            "the baseline's external probe measured SUMO and closure_cost_v1 "
            "on its own host and demand; PR C changes neither, so it is not "
            "rerun here"),
    }


def _identities(repeats: int) -> dict[str, Any]:
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
        "ledger_contract": {
            "schema": LEDGER_SCHEMA,
            "version": LEDGER_VERSION,
        },
        "policies": {name: _sha256(ROOT / name) for name in BOUND_POLICIES},
        "sources": {name: _sha256(ROOT / name) for name in BOUND_SOURCES},
        "benchmark_tool": {
            "path": "tools/benchmark_closure_streaming.py",
            "sha256": _sha256(ROOT / "tools" / "benchmark_closure_streaming.py"),
        },
        "repeats": repeats,
    }


def _memory_gate(cases: list[Mapping[str, Any]],
                 identities: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's under-64-MiB gate for the 720-hour streaming path.

    Comparability is decided BEFORE the pass/fail is stated.  The frozen
    baseline was taken on Darwin/arm64; a Linux `VmHWM` figure is a different
    measurement of a different allocator on a different kernel.  When the hosts
    differ the measurement is still recorded — and the gate is left OPEN, with
    the reason, rather than converted into a pass it did not earn.
    """
    baseline_platform = {"system": "Darwin", "machine": "arm64"}
    host = identities["platform"]
    comparable = (host["system"] == baseline_platform["system"]
                  and host["machine"] == baseline_platform["machine"])
    target = next(
        (item for item in cases if item["case_id"] == "six-month-720h"), None)
    stream = None
    if target is not None:
        stream = next((item for item in target["phases"]
                       if item.get("phase") == "stream"
                       and item.get("status") == "measured"), None)
    if stream is None:
        return {"status": "unmeasured",
                "reason": "the 720-hour streaming phase was not measured",
                "gate_bytes": STREAMING_PEAK_RSS_GATE_BYTES}
    total_under = stream["peak_rss_bytes"] < STREAMING_PEAK_RSS_GATE_BYTES
    enumeration_under = (
        stream["peak_rss_over_imports_bytes"] < STREAMING_PEAK_RSS_GATE_BYTES)
    if total_under and comparable:
        status = "passed"
    elif total_under:
        status = "open_pending_baseline_host"
    elif enumeration_under:
        status = "open_fixed_import_cost_dominates"
    else:
        status = "failed"
    return {
        "case_id": "six-month-720h",
        "gate_bytes": STREAMING_PEAK_RSS_GATE_BYTES,
        "measured_peak_rss_bytes": stream["peak_rss_bytes"],
        "import_peak_rss_bytes": stream["import_peak_rss_bytes"],
        "measured_peak_rss_over_imports_bytes":
            stream["peak_rss_over_imports_bytes"],
        "total_under_gate": bool(total_under),
        "enumeration_under_gate": bool(enumeration_under),
        "host": dict(host),
        "baseline_host": baseline_platform,
        "comparable_to_frozen_baseline_host": bool(comparable),
        "status": status,
        "reason": {
            "passed": (
                "measured under the gate on the frozen baseline's own host"),
            "open_pending_baseline_host": (
                "measured under the gate, but on a host the frozen "
                "Darwin/arm64 baseline cannot be compared against; the "
                "hardware gate stays open for the dev machine"),
            "open_fixed_import_cost_dominates": (
                "the enumeration itself measures under the gate, but the "
                "process total does not: importing independent_daily pulls "
                "finalist_decision and therefore scipy, a fixed cost the v1 "
                "path pays identically and that this PR does not touch. The "
                "gate is stated as a process total, so it stays OPEN and must "
                "be re-measured on the frozen baseline's own host"),
            "failed": (
                "the streaming enumeration itself measured at or above the "
                "gate"),
        }[status],
    }


def build_record(*, repeats: int, timeout: int) -> dict[str, Any]:
    if repeats < MINIMUM_REPEATS:
        raise SystemExit(
            f"--repeats must be at least {MINIMUM_REPEATS}; the plan requires "
            f"every pure performance case to run at least that many times")
    identities = _identities(repeats)
    cases = []
    for case in benchmark_cases():
        print(f"  {case['case_id']} …", flush=True)
        cases.append(run_case(case, repeats=repeats, timeout=timeout))

    record = {
        "schema": SCHEMA,
        "kind": "closure_search_streaming_comparison",
        "stage": "closure_search_scaling_plan_pr_c",
        "evidence_class": "diagnostic_comparison",
        "claim_boundary": {
            "release_evidence": False,
            "opens_no_gate": True,
            "reason": (
                "a performance and semantics comparison for the streaming "
                "ledgers; it ranks no closure, verifies no schedule and "
                "licenses no claim"),
        },
        "measured_at": time.strftime("%Y-%m-%d"),
        "repeats": repeats,
        "identities": identities,
        "baseline": _baseline_binding(),
        "isolation": {
            "scratch_root": "private temporary directory inside each child, "
                            "removed after the run",
            "wrote_into_evidence_roots": False,
            "ran_sumo": False,
            "roots_never_touched": ["runs/", "sumo/", "web/data/",
                                    "validation/ (except this record)"],
        },
        "cases": cases,
    }
    record["memory_gate"] = _memory_gate(cases, identities)
    record["input_content_key"] = _content_key({
        "schema": SCHEMA,
        "identities": identities,
        "cases": [case["spec"] for case in cases],
        "baseline_input_content_key": BASELINE_INPUT_CONTENT_KEY,
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
    parser.add_argument("--timeout", type=int, default=7200,
                        help="per-child timeout in seconds")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an existing record at --out")
    args = parser.parse_args(argv)

    destination = Path(args.out)
    if not args.stdout and destination.exists() and not args.overwrite:
        raise SystemExit(
            f"{destination} already exists; pass --overwrite to replace it")

    record = build_record(repeats=args.repeats, timeout=args.timeout)
    payload = json.dumps(record, indent=1, sort_keys=True) + "\n"
    if args.stdout:
        print(payload, end="")
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(destination.parent),
            delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    # NamedTemporaryFile creates 0600; a tracked artifact has to be readable
    # like every other record in validation/.
    temporary.chmod(0o644)
    temporary.replace(destination)
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
