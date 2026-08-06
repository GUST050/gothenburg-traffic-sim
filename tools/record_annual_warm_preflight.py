#!/usr/bin/env python3
"""Record or validate the environment preflight for annual population."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.plan_annual_warming import build_default_plan  # noqa: E402
from tools.populate_annual_warming import (  # noqa: E402
    _root_for,
    production_preflight,
    required_free_bytes,
)


OUTPUT = Path("validation/annual_warm_preflight_v1.json")


def _key(payload: Mapping[str, Any]) -> str:
    body = {name: value for name, value in payload.items() if name != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_report(report: Mapping[str, Any], plan: Mapping[str, Any]) -> dict:
    required = {
        "schema", "status", "recorded_date", "plan_content_key",
        "sumo_version", "sumo_home", "traci", "network_sha256",
        "reference_edge", "state_workers", "approved_state_workers",
        "localhost_ipv4_tcp_bind", "minimum_free_bytes", "measured_free_bytes",
        "disk_gate_passed", "pending_units", "keep_demand_archives",
        "content_key",
    }
    if not isinstance(report, Mapping) or set(report) != required:
        raise ValueError("annual warm preflight report fields differ")
    if report["schema"] != "annual_warm_preflight_v1" \
            or report["status"] != "pass" \
            or report["plan_content_key"] != plan["content_key"]:
        raise ValueError("annual warm preflight report identity differs")
    # The date is provenance, not a gate: it records WHEN this environment was
    # measured. It used to be the frozen literal "2026-08-04", written by
    # --write and required by --verify, so a record made on any other day
    # certified itself as having been taken on 2026-08-04. In a repository
    # whose whole discipline is seals refusing to certify stale evidence, a
    # self-falsifying timestamp is the one field that must not be frozen.
    recorded_date = report["recorded_date"]
    try:
        date.fromisoformat(str(recorded_date))
    except (TypeError, ValueError):
        raise ValueError("annual warm preflight date is not an ISO date")
    # The worker count is a real gate, but it is not the constant 3. Record
    # whatever was measured and require only that the benchmark approves it,
    # which is the same rule production_preflight() enforces at run time.
    state_workers = report["state_workers"]
    approved = report["approved_state_workers"]
    if isinstance(state_workers, bool) or not isinstance(state_workers, int) \
            or state_workers < 1:
        raise ValueError("annual warm preflight state workers are malformed")
    if isinstance(approved, bool) or not isinstance(approved, int) \
            or approved < state_workers:
        raise ValueError(
            "annual warm preflight records more workers than are approved")
    if report["localhost_ipv4_tcp_bind"] is not True:
        raise ValueError("annual warm preflight localhost bind did not pass")
    # The gate is derived from the work the invocation could select, so the
    # recorded minimum is recomputed here rather than compared to a constant.
    # A record claiming a smaller requirement than its own pending count
    # implies is therefore rejected, not trusted.
    if isinstance(report["keep_demand_archives"], bool) is False:
        raise ValueError("annual warm preflight archive policy is malformed")
    expected_minimum = required_free_bytes(
        plan,
        pending_units=report["pending_units"],
        keep_demand_archives=report["keep_demand_archives"],
    )
    if report["minimum_free_bytes"] != expected_minimum \
            or report["measured_free_bytes"] < expected_minimum \
            or report["disk_gate_passed"] is not True:
        raise ValueError("annual warm preflight disk gate did not pass")
    if report["network_sha256"] != plan["input_fingerprints"]["network"]["sha256"]:
        raise ValueError("annual warm preflight network differs")
    traci = report["traci"]
    if not isinstance(traci, Mapping) or traci.get("api_complete") is not True:
        raise ValueError("annual warm preflight TraCI API differs")
    if report["content_key"] != _key(report):
        raise ValueError("annual warm preflight content key differs")
    return json.loads(json.dumps(report))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--state-workers", type=int, default=3,
        help=(
            "Concurrent SUMO state workers to record. Must not exceed "
            "approved_seed_workers(); production_preflight() enforces that. "
            "Was hardcoded to 3, which made a record for any other count "
            "impossible to produce even after the benchmark approved it."
        ),
    )
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    plan = build_default_plan()
    if args.write:
        raw = production_preflight(
            plan, _root_for(plan, None), state_workers=args.state_workers)
        report: dict[str, Any] = {
            "schema": "annual_warm_preflight_v1",
            "status": "pass",
            "recorded_date": date.today().isoformat(),
            "plan_content_key": plan["content_key"],
            "sumo_version": raw["sumo_version"],
            "sumo_home": raw["sumo_home"],
            "traci": raw["traci"],
            "network_sha256": raw["network_sha256"],
            "reference_edge": raw["reference_edge"],
            "state_workers": raw["state_workers"],
            "approved_state_workers": raw["approved_state_workers"],
            "localhost_ipv4_tcp_bind": True,
            "minimum_free_bytes": raw["minimum_free_bytes"],
            "measured_free_bytes": raw["free_bytes"],
            "disk_gate_passed": raw["free_bytes"] >= raw["minimum_free_bytes"],
            "pending_units": raw["pending_units"],
            "keep_demand_archives": raw["keep_demand_archives"],
        }
        report["content_key"] = _key(report)
        validate_report(report, plan)
        _atomic(output, report)
    else:
        report = validate_report(
            json.loads(output.read_text(encoding="utf-8")), plan
        )
    print(json.dumps({
        "content_key": report["content_key"],
        "plan_content_key": report["plan_content_key"],
        "status": report["status"],
        "approved_state_workers": report["approved_state_workers"],
        "measured_free_bytes": report["measured_free_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
