#!/usr/bin/env python3
"""Child side of the guarded WindowCostIndex runner: telemetry only.

The guarded runner (``tools/guarded_wci_build.py``) starts this module in its
own process group. It runs exactly one job and writes a telemetry file
atomically every second and at every phase change:

* ``build``  -- ``build_window_cost_index.build_from_profile`` for a month
  profile, publishing the index and its evidence;
* ``canary`` -- the bounded canary population for the given build keys:
  ``_raw_index_records`` against an existing oracle cache, then the per-unit
  cost and provider-identity checks. It publishes nothing.

The builder's functions are wrapped only to count and time them. Every
number the builder computes, and every file it writes, is its own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "wci_guarded_child_telemetry_v1"
HEARTBEAT_S = 1.0


class Telemetry:
    """Thread-safe telemetry state, written atomically by a heartbeat."""

    def __init__(self, path: Path, *, job: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: Dict[str, Any] = {
            "schema": SCHEMA, "job": job, "pid": os.getpid(),
            "pgid": os.getpgid(0), "sequence": 0, "phase": "starting",
            "phases": [], "counters": {"archive_index_build": 0,
                                       "archive_validate": {},
                                       "variant_parses": {}},
            "population": None, "affected_vehicles_total": None,
            "oracle": None, "provider_identity": None, "ledger": None,
            "published": None, "result": None, "error": None,
        }
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def start(self) -> "Telemetry":
        self.write()
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.write()

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            self.write()

    def phase(self, name: str) -> None:
        with self._lock:
            self._state["phase"] = name
            self._state["phases"].append(
                {"phase": name, "monotonic": time.monotonic()})
        self.write()

    def count(self, kind: str, key: str | None = None) -> None:
        with self._lock:
            counters = self._state["counters"]
            if key is None:
                counters[kind] += 1
            else:
                counters[kind][key] = counters[kind].get(key, 0) + 1

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)
        self.write()

    def write(self) -> None:
        with self._lock:
            self._state["sequence"] += 1
            self._state["written_monotonic"] = time.monotonic()
            payload = json.dumps(self._state, sort_keys=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.tmp")
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)


def _wrap(module, name: str, around: Callable) -> None:
    original = getattr(module, name)

    def wrapped(*args, **kwargs):
        return around(original, *args, **kwargs)

    setattr(module, name, wrapped)


def _records_summary(records: Mapping[str, Any],
                     identities: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "population": {"daily_units": len(records),
                       "variant_records": sum(len(unit["records"])
                                              for unit in records.values())},
        "affected_vehicles_total": sum(
            int(item.get("vehicles_affected") or 0)
            for unit in records.values() for item in unit["records"]),
        "provider_identity": {"units": len(identities),
                              "complete": set(identities) == set(records)},
    }


def instrument(builder, telemetry: Telemetry) -> None:
    """Count and time the builder's seams; never change a result."""
    def index(original, *args, **kwargs):
        telemetry.count("archive_index_build")
        return original(*args, **kwargs)

    def validate(original, runs_root, required, **kwargs):
        telemetry.count("archive_validate", str(required.build_key))
        return original(runs_root, required, **kwargs)

    def parse(original, path, **kwargs):
        telemetry.count("variant_parses", str(path))
        return original(path, **kwargs)

    def phased(name):
        def around(original, *args, **kwargs):
            telemetry.phase(name)
            return original(*args, **kwargs)
        return around

    def raw(original, *args, **kwargs):
        telemetry.phase("raw_index_records")
        records, oracle, measurement = original(*args, **kwargs)
        telemetry.update(**_records_summary(
            records, measurement.get("provider_identities") or {}))
        telemetry.phase("oracle_compare")
        return records, oracle, measurement

    _wrap(builder, "_archives_for_build_key", index)
    _wrap(builder, "find_demand_archives", validate)
    _wrap(builder, "parse_route_vehicles", parse)
    _wrap(builder, "_bound_inputs", phased("bound_inputs"))
    _wrap(builder, "_raw_index_records", raw)
    _wrap(builder, "_verify_before_publication",
          phased("verify_before_publication"))
    _wrap(builder, "write_index", phased("persist_index"))
    _wrap(builder, "build_cost_ledger", phased("indexed_ledger"))
    _wrap(builder, "_publish", phased("publish_evidence"))


def build_job(args, builder, telemetry: Telemetry) -> None:
    evidence = builder.build_from_profile(
        Path(args.profile), index_out=Path(args.index_out),
        evidence_out=Path(args.evidence_out), evidence_id=args.evidence_id)
    population = dict(evidence.get("population") or {})
    oracle = dict(evidence.get("oracle") or {})
    telemetry.update(
        population={"daily_units": population.get("daily_units"),
                    "variant_records": population.get(
                        "daily_variant_records"),
                    "parents": population.get("parent_schedules")},
        oracle={"complete": bool(oracle.get("oracle_complete")),
                "identical": bool(oracle.get("field_identical")),
                "records": oracle.get("indexed_variant_records")},
        ledger={"identical": bool(evidence.get("ledger_identical")),
                "status": evidence.get("status")},
        published={"index": str(Path(args.index_out)
                                / "window-cost-index.json"),
                   "evidence": str(args.evidence_out),
                   "evidence_content_key": evidence.get("content_key")},
        result={"status": evidence.get("status")})


def canary_job(args, builder, telemetry: Telemetry) -> None:
    """The bounded canary population; publishes nothing."""
    benchmarks = ROOT / "validation" / "benchmarks"
    if str(benchmarks) not in sys.path:
        sys.path.append(str(benchmarks))
    # pylint: disable=import-outside-toplevel
    from wci_effect_canary_v3 import _population, _unit_checks
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.deterministic_disruption import DailyCostCache

    record = json.loads(Path(args.canary_spec).read_text(encoding="utf-8"))
    spec = ClosureSearchSpec.from_dict(record["spec"])
    if spec.content_key != record["spec_content_key"]:
        raise SystemExit("canary spec content key drifted")
    bound = builder._bound_inputs(Path(args.profile))
    telemetry.phase("population")
    units, _required = _population(builder, bound, spec,
                                   set(json.loads(args.build_keys)))
    production_records = builder.daily_unit_records

    def filtered(spec_arg, parent):
        for item in production_records(spec_arg, parent):
            if str(item[0]) in units:
                yield item

    builder.daily_unit_records = filtered
    builder.EXPECTED_DAILY_UNITS = len(units)
    cache = DailyCostCache(Path(args.oracle_root))
    records, oracle, measurement = builder._raw_index_records(
        spec, runs_root=bound["runs_root"], oracle_cache=cache,
        qualified_demand_manifest=bound["qualified_manifest"])
    identities = measurement["provider_identities"]
    _costs, cost_mismatches, identity_mismatches = _unit_checks(
        records, oracle, identities, units, cache)
    comparison = builder.WindowCostIndex(
        bound_identity={"schema": SCHEMA, "spec": spec.content_key},
        records=records, preparation_time_s=0.0).compare_oracle(oracle)
    telemetry.update(
        oracle={"complete": bool(comparison["oracle_complete"]),
                "identical": bool(comparison["field_identical"])
                and not cost_mismatches,
                "records": comparison["indexed_variant_records"]},
        provider_identity={"units": len(identities),
                           "complete": set(identities) == set(records)
                           and not identity_mismatches},
        ledger={"identical": None, "status": "not_applicable"},
        result={"status": "COMPLETE"})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", choices=("build", "canary"), required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--index-out", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--evidence-id")
    parser.add_argument("--canary-spec", type=Path)
    parser.add_argument("--oracle-root", type=Path)
    parser.add_argument("--build-keys")
    args = parser.parse_args(argv)

    # pylint: disable=import-outside-toplevel
    from tools import build_window_cost_index as builder

    telemetry = Telemetry(args.telemetry, job=args.job).start()
    instrument(builder, telemetry)
    try:
        (build_job if args.job == "build" else canary_job)(
            args, builder, telemetry)
        telemetry.phase("done")
        return 0
    except BaseException as error:
        telemetry.update(error=f"{type(error).__name__}: {error}",
                         phase="failed")
        raise
    finally:
        telemetry.close()


if __name__ == "__main__":
    raise SystemExit(main())
