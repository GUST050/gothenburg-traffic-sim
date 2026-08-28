"""Private worker for one isolated independent-day SUMO candidate."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DemandBuildSpec,
)
from traffic_sim.simulation.envelope import EnvelopePolicy
from traffic_sim.simulation.independent_daily import (
    _evidence_from_dict,
    _evidence_to_dict,
)
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
from traffic_sim.simulation.warm_state_boundary import WarmPrefixController


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("daily worker request must be an object")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_request(
    raw: Mapping[str, Any], *, telemetry_sidecar: Path | None = None
) -> dict[str, Any]:
    if raw.get("schema") != "independent_daily_worker_request_v1":
        raise ValueError("daily worker request schema is unsupported")
    execution = raw.get("execution")
    if not isinstance(execution, Mapping):
        raise ValueError("daily worker execution contract is missing")
    spec = ClosureSearchSpec.from_dict(execution["spec"])
    schedule = ClosureSchedule.from_dict(raw["schedule"])
    if schedule.search_content_key != spec.content_key:
        raise ValueError("daily worker schedule belongs to another search")
    warm_execution = execution.get("warm_execution") is True
    runner = ArchivedDemandSumoRunner(
        spec,
        archive=Path(str(execution["archive"])),
        baseline_trip_duration_p99_s=int(
            execution["baseline_trip_duration_p99_s"]
        ),
        study_provenance_key=str(execution["study_provenance_key"]),
        cache_root=Path(str(execution["cache_root"])),
        seed_workers=1,
        envelope_policy=EnvelopePolicy(**dict(execution["envelope_policy"])),
        expected_demand_spec=DemandBuildSpec.from_dict(
            execution["expected_demand_spec"]
        ),
        include_disruption=execution.get("include_disruption") is True,
        warm_execution=warm_execution,
        boundary_controller=(WarmPrefixController() if warm_execution else None),
        launch_sidecar_path=telemetry_sidecar,
    )
    existing_raw = raw.get("existing")
    existing = (
        None if existing_raw is None else _evidence_from_dict(existing_raw)
    )
    targets = raw.get("target_repetitions")
    if not isinstance(targets, Mapping):
        raise ValueError("daily worker targets are missing")
    evidence = runner.run_candidate(
        schedule,
        target_repetitions={key: int(value) for key, value in targets.items()},
        existing=existing,
        stage=str(raw.get("stage", "")),
    )
    # This subprocess is the ONLY place the real SUMO launch happens for an
    # isolated daily unit — `runner.launch_telemetry` is therefore the whole
    # truth about how many attempts occurred, and it dies with this process
    # unless it rides back in the result. Without this, every isolated-worker
    # run reported zero exact-launch telemetry regardless of how many SUMO
    # processes actually ran.
    return {
        "schema": "independent_daily_worker_result_v3",
        "evidence": _evidence_to_dict(evidence),
        "launch_telemetry": runner.launch_telemetry_snapshot(),
        "launch_records": runner.launch_records_snapshot(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--telemetry-sidecar", type=Path, required=False, default=None)
    args = parser.parse_args(argv)
    # The sidecar (if supplied) is written to durably, record by record, as
    # each real SUMO attempt happens inside `execute_request` — so it already
    # holds the truth about every attempt regardless of whether this call
    # returns normally, raises, or the process is killed before it can. Only
    # the final evidence/summary result depends on reaching this line.
    result = execute_request(_read(args.request), telemetry_sidecar=args.telemetry_sidecar)
    _atomic_json(args.result, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
