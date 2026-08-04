#!/usr/bin/env python3
"""Write or verify the default-off 2027 annual warm-coverage plan.

This tool performs no demand generation, SUMO/TraCI work, cache publication,
or product activation.  It only fingerprints tracked inputs and deterministically
projects the existing calendar/envelope contracts.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.simulation.annual_warm_plan import (  # noqa: E402
    build_annual_warm_plan,
    default_2027_coverage_spec,
    fingerprint_inputs,
    load_annual_warm_plan,
    write_annual_warm_plan,
)
from traffic_sim.core.fingerprint import sumo_version  # noqa: E402
from traffic_sim.simulation.runtime import sumo_home  # noqa: E402
from traffic_sim.demand.source_identity import demand_source_paths  # noqa: E402


DEFAULT_OUTPUT = Path("validation/annual_warm_plan_2027.json")
BOUND_INPUTS = {
    "forecast": Path("web/data/flows_forecast.json"),
    "network": Path("sumo/net.net.xml"),
    "demand_builder_source": Path("build_sumo_demand.py"),
    "demand_build_lock_source": Path("traffic_sim/demand/build_lock.py"),
    "contracts_source": Path("traffic_sim/core/contracts.py"),
    "calendar_source": Path("traffic_sim/core/closure_calendar.py"),
    "envelope_source": Path("traffic_sim/simulation/envelope.py"),
    "monthly_demand_source": Path("traffic_sim/simulation/monthly_demand.py"),
    "monthly_sumo_source": Path("traffic_sim/simulation/monthly_sumo.py"),
    "monthly_warm_state_source": Path(
        "traffic_sim/simulation/monthly_warm_state.py"
    ),
    "warm_boundary_source": Path(
        "traffic_sim/simulation/warm_state_boundary.py"
    ),
    "warm_route_windows_source": Path(
        "traffic_sim/simulation/warm_route_windows.py"
    ),
    "warm_cache_source": Path("traffic_sim/simulation/warm_state_cache.py"),
    "metrics_source": Path("traffic_sim/simulation/metrics.py"),
    "runtime_source": Path("traffic_sim/simulation/runtime.py"),
    "scenario_runner_source": Path("run_scenario.py"),
    "annual_plan_source": Path("traffic_sim/simulation/annual_warm_plan.py"),
    "annual_store_source": Path("traffic_sim/simulation/annual_warm_store.py"),
    "annual_population_source": Path(
        "traffic_sim/simulation/annual_warm_population.py"
    ),
    "annual_progress_source": Path(
        "traffic_sim/simulation/annual_warm_progress.py"
    ),
    "planner_source": Path("tools/plan_annual_warming.py"),
    "population_tool_source": Path("tools/populate_annual_warming.py"),
    "storage_pilot_tool_source": Path("tools/pilot_annual_warm_storage.py"),
}
BOUND_INPUTS.update({
    f"demand_source:{label}": path.relative_to(ROOT)
    for label, path in demand_source_paths(ROOT).items()
})


def build_default_plan() -> dict:
    home = sumo_home()
    return build_annual_warm_plan(
        default_2027_coverage_spec(),
        input_fingerprints=fingerprint_inputs(BOUND_INPUTS, base=ROOT),
        runtime_identity={
            "sumo_version": sumo_version(home),
            "platform": platform.platform(),
            "simulation_mode": "meso",
            "metric_schema": "closure_decision_metrics_v1",
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="repository-relative plan path (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = build_default_plan()
    if args.write:
        write_annual_warm_plan(output, expected)
        result = expected
    else:
        stored = load_annual_warm_plan(output)
        if stored != expected:
            raise SystemExit(
                "annual warm plan differs from current sources or coverage contract"
            )
        result = stored
    print(json.dumps({
        "content_key": result["content_key"],
        "status": result["status"],
        **result["coverage"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
