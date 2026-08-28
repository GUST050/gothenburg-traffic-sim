"""Shared test-only twin of the registered-outcome-publishing path.

Production's registered-outcome-publishing functions
(`tools.cost_ordered_benchmark._run_registered` / `_execute_and_publish`)
accept no `isolate_arms` argument at all — both always isolate each arm in
its own process, by construction, so nothing reachable from `main()` or a
plain import can ever publish a preregistered outcome without process
isolation (see cost-order v5's cross-arm timeout leak, which is exactly the
failure mode that guarantee closes).

Several test modules still need to exercise that same load/verify/execute/
publish sequence with an in-process, SUMO-free `build_arm` fake, which a
real isolated subprocess would never see. This module is that seam: a
deliberate, test-only duplicate that lives outside `tools/` and is never
imported by production code, so it cannot become a back door for a real
benchmark run to skip isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import tools.cost_ordered_benchmark as bench


def run_registered_unisolated_for_tests(
    *, registration_path, runs_root, release_root, workspace_root, data_root,
    out, overwrite, stdout, fault_injection, allow_drift,
) -> int:
    registration = json.loads(
        Path(registration_path).read_text(encoding="utf-8"))
    schema = str(registration.get("schema"))
    assert schema in bench.SUPPORTED_REGISTRATION_SCHEMAS
    assert registration.get("selected_case") is not None

    drift = bench.verify_bindings(registration, runs_root)
    if drift and not allow_drift:
        raise SystemExit(
            "the registration's bound inputs no longer describe this tree:\n"
            "  - " + "\n  - ".join(drift))

    declared_outcome = registration.get("outcome_record")
    if declared_outcome and bench._relative(Path(out).resolve()) != (
            declared_outcome):
        raise SystemExit(
            f"the registration names {declared_outcome} as its outcome but "
            f"--out is {bench._relative(Path(out).resolve())}.")

    try:
        executed = bench.run_benchmark(
            registration,
            runs_root=runs_root,
            release_root=release_root,
            workspace_root=workspace_root,
            data_root=data_root,
            fault_injection=fault_injection,
            isolate_arms=False,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        comparison = {
            "execution_error": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "binding_drift_accepted": list(drift),
            "workspace_root": str(Path(workspace_root).resolve()),
        }
        outcome = bench.build_outcome(
            registration, comparison, status="failed_execution",
            registration_path=registration_path)
        if stdout:
            print(json.dumps(outcome, indent=1, sort_keys=True))
        else:
            bench._write(out, outcome, overwrite=overwrite)
        return 5
    comparison = dict(executed["comparison"])
    if drift:
        comparison["binding_drift_accepted"] = list(drift)
    outcome = bench.build_outcome(
        registration, comparison, status="measured",
        registration_path=registration_path)
    if stdout:
        print(json.dumps(outcome, indent=1, sort_keys=True))
        return 0 if outcome["gates"]["passed"] else 4
    bench._write(out, outcome, overwrite=overwrite)
    return 0 if outcome["gates"]["passed"] else 4
