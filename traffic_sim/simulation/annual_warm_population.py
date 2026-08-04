"""Fail-closed coordination helpers for annual warm-state population."""

from __future__ import annotations

import hashlib
import gzip
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
    DemandBuildSpec,
)
from traffic_sim.simulation.annual_warm_plan import (
    _work_unit_from_checked,
    annual_work_unit,
    update_ledger_unit,
    validate_annual_warm_ledger,
    validate_annual_warm_plan,
)
from traffic_sim.simulation.annual_warm_store import ARTIFACT_SCHEMA


UNIT_METADATA_SCHEMA = "annual_warm_unit_metadata_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARCHIVE_OUTPUTS = (
    "demand_meta.json",
    "demand_build_spec.json",
    "candidates.rou.xml",
    "candidates.meta.json",
    "calibrated.rou.xml",
    "calibrated.agents.json",
    "calibrated_v1.rou.xml",
    "calibrated_v1.agents.json",
    "calibrated_v2.rou.xml",
    "calibrated_v2.agents.json",
)
_VARIANT_ROUTE = {
    "q10": "calibrated_v1.rou.xml",
    "q50": "calibrated.rou.xml",
    "q90": "calibrated_v2.rou.xml",
}


class AnnualWarmPlanContext:
    """Validate and index one immutable annual plan once per executor.

    The full plan contains roughly 35,000 checkpoint requests. Revalidating it
    and rebuilding request dictionaries for every one of 104,685 units turns a
    constant-time lookup into hours of pure Python overhead. This context owns
    a validated defensive copy and exposes only small copied unit records.
    """

    def __init__(self, plan: Mapping[str, Any]) -> None:
        checked = validate_annual_warm_plan(plan)
        self.plan = checked
        self.requests = {
            request["request_id"]: request
            for request in checked["checkpoint_requests"]
        }
        self.demands = {
            item["build_key"]: item for item in checked["demand_build_specs"]
        }
        self.candidates = {
            (item["seed"], item["variant"]): item
            for item in checked["seed_variants"]
        }
        self.previous_request = {}
        requests_by_demand: dict[str, list[Mapping[str, Any]]] = {}
        for request in checked["checkpoint_requests"]:
            requests_by_demand.setdefault(
                request["demand_build_key"], []
            ).append(request)
        for requests in requests_by_demand.values():
            previous = None
            for request in sorted(requests, key=lambda item: item["checkpoint_s"]):
                self.previous_request[request["request_id"]] = previous
                previous = request

    def unit_context(
        self, unit_id: str, *, unit_hint: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if unit_hint is None:
            raise ValueError(
                "indexed annual unit lookup requires an exact unit_hint"
            )
        hint = dict(unit_hint)
        required_hint = {
            "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
        }
        if set(hint) != required_hint:
            raise ValueError("indexed annual unit hint fields differ")
        request = self.requests.get(hint.get("request_id"))
        mapping = self.candidates.get((
            hint.get("seed"), hint.get("variant")
        ))
        if request is None or mapping is None:
            raise ValueError(f"annual warm unit is not in the plan: {unit_id}")
        if (
            hint["demand_build_key"] != request["demand_build_key"]
            or hint["checkpoint_s"] != request["checkpoint_s"]
        ):
            raise ValueError(f"annual warm unit is not in the plan: {unit_id}")
        candidate = _work_unit_from_checked(self.plan, request, mapping)
        if candidate["unit_id"] != unit_id:
            raise ValueError(f"annual warm unit is not in the plan: {unit_id}")
        unit = {
            key: value for key, value in candidate.items()
            if key not in {"state", "attempts", "artifact_content_key", "error"}
        }
        return {
            "unit": json.loads(json.dumps(unit)),
            "request": json.loads(json.dumps(request)),
            "demand_build_spec": json.loads(json.dumps(
                self.demands[unit["demand_build_key"]]
            )),
        }

    def predecessor_unit_id(self, unit: Mapping[str, Any]) -> str | None:
        previous = self.previous_request.get(unit["request_id"])
        if previous is None:
            return None
        predecessor = _work_unit_from_checked(
            self.plan, previous,
            self.candidates[(unit["seed"], unit["variant"])],
        )
        return str(predecessor["unit_id"])


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _validated_archive_record(
    archive: Mapping[str, Any], expected_spec: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the complete immutable-demand identity carried by a unit."""
    required = {
        "run_id", "archive", "finished_at", "demand_build_spec",
        "archive_manifest_sha256", "outputs", "archive_content_key",
    }
    if not isinstance(archive, Mapping) or set(archive) != required:
        raise ValueError("demand archive validation record is malformed")
    checked = json.loads(json.dumps(
        archive, sort_keys=True, separators=(",", ":"), allow_nan=False
    ))
    if checked["demand_build_spec"] != expected_spec:
        raise ValueError("demand archive does not match annual unit demand")
    for field in ("run_id", "archive", "finished_at"):
        if not isinstance(checked[field], str) or not checked[field].strip():
            raise ValueError(f"demand archive {field} is missing")
    if not isinstance(checked["archive_manifest_sha256"], str) \
            or not _HEX64.fullmatch(checked["archive_manifest_sha256"]):
        raise ValueError("demand archive manifest identity is invalid")
    outputs = checked["outputs"]
    if not isinstance(outputs, list) or len(outputs) != len(_ARCHIVE_OUTPUTS):
        raise ValueError("demand archive output inventory is incomplete")
    by_name: dict[str, dict[str, Any]] = {}
    for record in outputs:
        if not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}:
            raise ValueError("demand archive output record is malformed")
        name = record["name"]
        size = record["bytes"]
        digest = record["sha256"]
        if name in by_name or name not in _ARCHIVE_OUTPUTS:
            raise ValueError("demand archive output inventory differs")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("demand archive output size is invalid")
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ValueError("demand archive output digest is invalid")
        by_name[name] = record
    if set(by_name) != set(_ARCHIVE_OUTPUTS):
        raise ValueError("demand archive output inventory differs")
    if checked["archive_content_key"] != _canonical_digest(outputs):
        raise ValueError("demand archive content key does not recompute")
    return checked


def annual_unit_context(
    plan: Mapping[str, Any], unit_id: str,
    *,
    unit_hint: Mapping[str, Any] | None = None,
    plan_context: AnnualWarmPlanContext | None = None,
) -> dict[str, Any]:
    if plan_context is not None:
        if plan.get("content_key") != plan_context.plan["content_key"]:
            raise ValueError("annual plan context belongs to another plan")
        return plan_context.unit_context(unit_id, unit_hint=unit_hint)
    checked = validate_annual_warm_plan(plan)
    requests = {
        request["request_id"]: request
        for request in checked["checkpoint_requests"]
    }
    demands = {
        item["build_key"]: item for item in checked["demand_build_specs"]
    }
    candidates = checked["seed_variants"]
    if unit_hint is None:
        # Compatibility/read-only path for tests and artifact inspection.
        hints = (
            {
                "request_id": request["request_id"],
                "demand_build_key": request["demand_build_key"],
                "checkpoint_s": request["checkpoint_s"],
                "seed": mapping["seed"],
                "variant": mapping["variant"],
            }
            for request in checked["checkpoint_requests"]
            for mapping in candidates
        )
    else:
        hints = (dict(unit_hint),)
    unit = None
    for hint in hints:
        request = requests.get(hint.get("request_id"))
        if request is None:
            continue
        if not any(
            hint.get("seed") == item["seed"]
            and hint.get("variant") == item["variant"]
            for item in candidates
        ):
            continue
        candidate = annual_work_unit(checked, request, hint)
        if candidate["unit_id"] == unit_id:
            unit = {
                key: value for key, value in candidate.items()
                if key not in {
                    "state", "attempts", "artifact_content_key", "error"
                }
            }
            break
    if unit is None:
        raise ValueError(f"annual warm unit is not in the plan: {unit_id}")
    return {
        "unit": unit,
        "request": requests[unit["request_id"]],
        "demand_build_spec": demands[unit["demand_build_key"]],
    }


def build_unit_metadata(
    plan: Mapping[str, Any],
    unit_id: str,
    *,
    demand_archive: Mapping[str, Any],
    unit_hint: Mapping[str, Any] | None = None,
    predecessor_unit_id: str | None = None,
    plan_context: AnnualWarmPlanContext | None = None,
) -> dict[str, Any]:
    indexed = plan_context or AnnualWarmPlanContext(plan)
    context = annual_unit_context(
        plan, unit_id, unit_hint=unit_hint, plan_context=indexed
    )
    expected_predecessor = indexed.predecessor_unit_id(context["unit"])
    if predecessor_unit_id != expected_predecessor:
        raise ValueError(
            "annual warm predecessor does not match the exact plan chain"
        )
    archive = _validated_archive_record(
        demand_archive, context["demand_build_spec"]
    )
    return {
        "schema": UNIT_METADATA_SCHEMA,
        **context["unit"],
        "request": context["request"],
        "demand_build_spec": context["demand_build_spec"],
        "demand_archive": archive,
        "population": {
            "method": ("bootstrap_from_zero" if predecessor_unit_id is None
                       else "extend_predecessor"),
            "predecessor_unit_id": predecessor_unit_id,
        },
    }


def build_annual_prefix_runner(
    plan: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    required: DemandBuildSpec,
    archive: Path,
    reference_edge: str,
    runner_factory=None,
    controller_factory=None,
):
    """Construct the exact candidate-free runner used by annual population.

    The isolated chain audit calls this same constructor so its independent
    cold comparisons cannot drift from production command semantics.
    """
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
    from traffic_sim.simulation.warm_state_boundary import WarmPrefixController

    coverage = plan["coverage_spec"]
    spec = ClosureSearchSpec(
        search_id=f"annual-prefix-{request['work_date']}",
        directed_edges=(reference_edge,),
        demand_build_id=required.build_key,
        permitted_date_start=request["work_date"],
        permitted_date_end=request["work_date"],
        required_work_minutes=coverage["resolution_minutes"],
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand(
            coverage["earliest_start"], coverage["latest_end"]
        ),
        source=coverage["source"],
        timezone=coverage["timezone"],
        dst_policy=coverage["dst_policy"],
        allowed_weekdays=(0, 1, 2, 3, 4, 5, 6),
        blackout_dates=(),
        resolution_minutes=coverage["resolution_minutes"],
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )
    runner_type = ArchivedDemandSumoRunner if runner_factory is None \
        else runner_factory
    controller_type = WarmPrefixController if controller_factory is None \
        else controller_factory
    return runner_type(
        spec,
        archive=Path(archive),
        baseline_trip_duration_p99_s=coverage["baseline_trip_duration_p99_s"],
        study_provenance_key=plan["content_key"],
        expected_demand_spec=required,
        warm_execution=True,
        boundary_controller=controller_type(),
        seed_workers=1,
    )


def validate_unit_artifact(
    plan: Mapping[str, Any],
    unit_id: str,
    artifact: Mapping[str, Any],
    *,
    plan_context: AnnualWarmPlanContext | None = None,
) -> dict[str, Any]:
    if not isinstance(artifact, Mapping) or artifact.get("schema") != ARTIFACT_SCHEMA:
        raise ValueError("annual warm artifact schema is unsupported")
    indexed = plan_context or AnnualWarmPlanContext(plan)
    checked_plan = indexed.plan
    if artifact.get("plan_content_key") != checked_plan["content_key"] \
            or artifact.get("unit_id") != unit_id:
        raise ValueError("annual warm artifact belongs to another plan or unit")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("schema") != UNIT_METADATA_SCHEMA:
        raise ValueError("annual warm artifact unit metadata is malformed")
    hint = {
        key: metadata.get(key)
        for key in (
            "request_id", "demand_build_key", "checkpoint_s", "seed", "variant"
        )
    }
    context = annual_unit_context(
        checked_plan, unit_id, unit_hint=hint, plan_context=indexed
    )
    for field, expected in context["unit"].items():
        if metadata.get(field) != expected:
            raise ValueError(f"annual warm artifact metadata differs on {field}")
    if metadata.get("request") != context["request"] \
            or metadata.get("demand_build_spec") != context["demand_build_spec"]:
        raise ValueError("annual warm artifact request or demand contract differs")
    population = metadata.get("population")
    if not isinstance(population, Mapping) or set(population) != {
        "method", "predecessor_unit_id"
    }:
        raise ValueError("annual warm artifact population provenance differs")
    predecessor = population["predecessor_unit_id"]
    expected_predecessor = indexed.predecessor_unit_id(context["unit"])
    expected_method = ("bootstrap_from_zero" if expected_predecessor is None
                       else "extend_predecessor")
    if (
        predecessor != expected_predecessor
        or population["method"] != expected_method
    ):
        raise ValueError("annual warm artifact population provenance differs")
    DemandBuildSpec.from_dict(metadata["demand_build_spec"])
    try:
        archive = _validated_archive_record(
            metadata.get("demand_archive"), context["demand_build_spec"]
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("annual warm artifact archive provenance differs") from exc
    members = artifact.get("members")
    if not isinstance(members, Mapping):
        raise ValueError("annual warm artifact members are malformed")
    outputs = {record["name"]: record for record in archive["outputs"]}
    bindings = {
        "route.rou.xml": _VARIANT_ROUTE[context["unit"]["variant"]],
        "demand_meta.json": "demand_meta.json",
        "demand_build_spec.json": "demand_build_spec.json",
    }
    for member_name, output_name in bindings.items():
        member = members.get(member_name)
        output = outputs[output_name]
        if not isinstance(member, Mapping) or (
            member.get("content_sha256") != output["sha256"]
            or member.get("content_bytes") != output["bytes"]
        ):
            raise ValueError(
                f"annual warm artifact {member_name} differs from demand archive"
            )
    manifest_member = members.get("demand_manifest.json")
    if not isinstance(manifest_member, Mapping) or (
        manifest_member.get("content_sha256")
        != archive["archive_manifest_sha256"]
    ):
        raise ValueError("annual warm artifact demand manifest differs from archive")
    content_key = artifact.get("content_key")
    if not isinstance(content_key, str) or not _HEX64.fullmatch(content_key):
        raise ValueError("annual warm artifact lacks a content key")
    return json.loads(json.dumps(artifact))


def validate_restored_unit_payload(
    plan: Mapping[str, Any],
    unit_id: str,
    restored: Mapping[str, Path],
    *,
    unit_hint: Mapping[str, Any],
    plan_context: AnnualWarmPlanContext | None = None,
) -> dict[str, Any]:
    """Validate semantic member contents, not only their stored hashes."""
    from traffic_sim.simulation.monthly_warm_state import parse_prefix_evidence

    indexed = plan_context or AnnualWarmPlanContext(plan)
    required = {
        "state.xml.gz", "prefix_evidence.json", "demand_build_spec.json"
    }
    if not isinstance(restored, Mapping) or not required <= set(restored):
        raise ValueError("restored annual artifact member set is incomplete")
    paths = {name: Path(path) for name, path in restored.items()}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ValueError("restored annual artifact contains a non-regular member")

    try:
        evidence_raw = json.loads(paths["prefix_evidence.json"].read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("restored annual prefix evidence is unreadable") from exc
    matching = indexed.unit_context(
        unit_id, unit_hint=unit_hint
    )["unit"]
    try:
        evidence = parse_prefix_evidence(
            evidence_raw, expected_warm_point_s=matching["checkpoint_s"]
        )
    except Exception as exc:
        raise ValueError("restored annual prefix evidence is invalid") from exc
    restored_spec = DemandBuildSpec.from_dict(json.loads(
        paths["demand_build_spec.json"].read_text()
    ))
    expected_spec = indexed.demands[matching["demand_build_key"]]
    if restored_spec != DemandBuildSpec.from_dict(expected_spec):
        raise ValueError("restored annual demand contract differs")

    try:
        with gzip.open(paths["state.xml.gz"], "rb") as handle:
            _event, root = next(ET.iterparse(handle, events=("start",)))
    except (OSError, StopIteration, ET.ParseError) as exc:
        raise ValueError("restored annual SUMO state is unreadable") from exc
    if root.tag != "snapshot" or root.get("type") != indexed.plan[
        "runtime_identity"
    ]["simulation_mode"]:
        raise ValueError("restored annual SUMO state mode differs")
    try:
        state_time = float(root.get("time", "nan"))
    except ValueError as exc:
        raise ValueError("restored annual SUMO state time is invalid") from exc
    if not math.isfinite(state_time) or state_time != matching["checkpoint_s"]:
        raise ValueError("restored annual SUMO state time differs")
    state_version = root.get("version")
    runtime_version = indexed.plan["runtime_identity"]["sumo_version"]
    if (
        not isinstance(state_version, str) or not state_version
        or f"sumo {state_version}" not in runtime_version.lower()
    ):
        raise ValueError("restored annual SUMO state version differs")
    return evidence


def record_unit_success(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    unit_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    checked = validate_unit_artifact(plan, unit_id, artifact)
    return update_ledger_unit(
        ledger,
        plan,
        unit_id=unit_id,
        state="succeeded",
        artifact_content_key=checked["content_key"],
    )


def selectable_units(
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    checked = validate_annual_warm_ledger(ledger, plan)
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("population limit must be a positive integer or None")
    selected = [
        dict(unit) for unit in checked["work_units"]
        if unit["state"] != "succeeded"
    ]
    return tuple(selected if limit is None else selected[:limit])


def population_summary(
    ledger: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_annual_warm_ledger(ledger, plan)
    states = Counter(unit["state"] for unit in checked["work_units"])
    total = len(checked["work_units"])
    return {
        "plan_content_key": checked["plan_content_key"],
        "ledger_content_key": checked["content_key"],
        "ledger_revision": checked["revision"],
        "total": total,
        "pending": states["pending"],
        "running": states["running"],
        "succeeded": states["succeeded"],
        "failed": states["failed"],
        "complete": states["succeeded"] == total,
    }
