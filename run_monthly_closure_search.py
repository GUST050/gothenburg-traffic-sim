#!/usr/bin/env python3
"""Run or resume a robust recurring closure search with archived SUMO demand."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import (
    load_closure_search_spec,
)
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.workspace import WorkspaceLock
from traffic_sim.simulation.monthly_search import (
    ActiveBudgetExceeded,
    ActiveTimeController,
    MonthlySearchPolicy,
    run_monthly_search,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    build_simulation_envelope,
)
from traffic_sim.simulation.independent_daily import (
    INDEPENDENT_DAILY_ENVELOPE_POLICY,
    IndependentDailyRunner,
    IsolatedDailySumoRunner,
)
from traffic_sim.simulation.closure_preflight import (
    ClosureSearchPreflight,
    UnsupportedPreflightSpec,
    preflight,
)
# The seed-worker budget is a JSON reader, and it is consulted before any
# simulation exists. Importing it from `monthly_sumo` pulled in run_scenario
# (pandas) and suggest_closure_time (SciPy) — about 110 MiB — on every run,
# including one the exact preflight then refuses.
from traffic_sim.simulation import unit_budget
from traffic_sim.simulation.unit_budget import DailyUnitBudget
from traffic_sim.simulation.seed_worker_budget import (
    SEED_WORKER_BENCHMARK_RECORD,
    approved_seed_workers,
)
from traffic_sim.simulation.search_workspace import DEFAULT_ROOT


# `-i` alone holds only the IDLE-sleep assertion. A multi-hour search also
# has to survive the disk idle-sleep path (`-m`) and the plain system-sleep
# path (`-s`, honoured only on AC power), so assert all three rather than
# discovering at hour six that one of them was never held. `-d` is
# deliberately NOT asserted: keeping the panel lit burns power and buys no
# compute, and the display sleeping has never paused a unit.
KEEP_AWAKE_FLAGS = ("-i", "-m", "-s")
PHASE6_HARD_STOP_S = 55 * 60
PHASE6_PUBLICATION_RESERVE_S = 5 * 60
PHASE6_REGISTRATION_SCHEMA = "subhour_full_month_registration_v1"
PHASE_STATUS_SCHEMA = "subhour_phase_status_v1"
PHASE_REVIEW_SCHEMA = "subhour_phase_review_v1"
PHASE_CHECKPOINT_KIND = "ai_flow_phase_3_5_checkpoint"
PHASE_INDEPENDENT_REVIEW_KIND = "ai_flow_phase_3_5_independent_review"
_PHASE_STATUS_NAMES = frozenset(
    {"phase_0", "phase_1", "phase_2", "phase_3", "phase_4", "phase_5"}
)
_LINEAGE_DIGEST_FIELDS = (
    "runtime_digest", "policy_digest",
)


def _phase_artifact_status(name: str, references: Sequence[Mapping[str, Any]],
                           artifact_path: Path) -> str:
    """Derive a Phase 3--5 terminal from its actual producer artifact.

    A status envelope is only an index of evidence.  Its producer cannot
    turn an inconclusive outcome into PASS by supplying a different status or
    by hashing arbitrary source/input/runtime/policy files.
    """
    values: list[dict[str, Any]] = []
    for reference in references:
        path = _resolve_phase_reference(reference.get("path"), artifact_path)
        if not path.is_file():
            continue
        try:
            value = _read(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            values.append(value)

    if name == "phase_3":
        candidates = [value for value in values
                      if value.get("schema") == "subhour_cost_ordered_bounded_outcome_v1"
                      and value.get("kind") == "subhour_bounded_sumo_outcome"]
        if len(candidates) != 1:
            raise ValueError(
                "Phase 6 prerequisite phase_3 must reference exactly one actual evidence bounded outcome")
        value = candidates[0]
        if value.get("status") == "PASS":
            cases = value.get("case_results")
            gate_s = value.get("gate_s")
            if (not isinstance(cases, list) or not cases
                    or not all(isinstance(item, Mapping)
                               and item.get("gates_passed") is True
                               for item in cases)
                    or not isinstance(gate_s, Mapping)
                    or gate_s.get("population_complete") is not True):
                raise ValueError("Phase 3 PASS is not backed by complete paired gates")
            return "PASS"
        if isinstance(value.get("status"), str) \
                and value["status"].startswith("INCONCLUSIVE"):
            return "INCONCLUSIVE"
        raise ValueError("Phase 3 outcome has no legal terminal status")

    if name == "phase_4":
        candidates = [value for value in values
                      if value.get("schema") == "monthly_cost_ledger_profile_v1"
                      and value.get("kind") in {"monthly_cost_ledger_profile",
                                                 "monthly_cost_ledger_profile_outcome"}]
        if len(candidates) != 1:
            raise ValueError(
                "Phase 6 prerequisite phase_4 must reference exactly one actual evidence ledger profile")
        value = candidates[0]
        population = value.get("population") or {}
        complete = (
            value.get("population_complete") is True
            and value.get("phase_timing_complete") is True
            and value.get("sumo_zero_launch_gate") is True
            and int(population.get("daily_units", 0)) == 1950
            and int(population.get("daily_variant_records", 0)) == 5850
            and int(population.get("parent_schedules", population.get("parents", 0))) == 1690
        )
        if value.get("status") == "PASS" and complete:
            return "PASS"
        if isinstance(value.get("status"), str) \
                and value["status"].startswith("INCONCLUSIVE"):
            return "INCONCLUSIVE"
        raise ValueError("Phase 4 profile has no legal terminal status")

    if name == "phase_5":
        profiles = [value for value in values
                    if value.get("schema") == "monthly_cost_ledger_profile_v1"
                    and value.get("kind") in {"monthly_cost_ledger_profile",
                                               "monthly_cost_ledger_profile_outcome"}]
        if len(profiles) != 1:
            raise ValueError("Phase 5 must reference exactly one Phase 4 profile")
        profile = profiles[0]
        indexes = [value for value in values
                   if value.get("schema") == "subhour_phase5_window_cost_index_evidence_v1"
                   and value.get("kind") == "subhour_phase5_window_cost_index"]
        decision = profile.get("phase_5_decision")
        population = profile.get("population") or {}
        profile_complete = (
            profile.get("status") == "PASS"
            and profile.get("population_complete") is True
            and profile.get("phase_timing_complete") is True
            and profile.get("sumo_zero_launch_gate") is True
            and int(population.get("daily_units", 0)) == 1950
            and int(population.get("daily_variant_records", 0)) == 5850
            and int(population.get("parent_schedules", population.get(
                "parents", 0))) == 1690
        )
        if decision == "NOT_TRIGGERED":
            if indexes or not profile_complete:
                raise ValueError(
                    "Phase 5 NOT_TRIGGERED requires a complete PASS profile "
                    "and no index")
            return "NOT_TRIGGERED"
        if decision == "TRIGGERED" and len(indexes) == 1 \
                and indexes[0].get("status") == "PASS" and profile_complete:
            oracle = indexes[0].get("oracle") or {}
            if oracle.get("field_identical") is not True \
                    or oracle.get("oracle_complete") is not True:
                raise ValueError("Phase 5 PASS lacks a complete field-identical oracle")
            return "PASS"
        raise ValueError("Phase 5 trigger and index evidence do not form a legal terminal")

    # Phases 0--2 are closed by the preregistered plan and are not allowed to
    # be changed through this producer-owned status envelope.
    if name in {"phase_0", "phase_1", "phase_2"}:
        return "PASS"
    raise ValueError(f"unsupported phase status derivation: {name}")


def _on_ac_power() -> bool | None:
    """Return True on AC, False on battery, None when it cannot be read.

    This is not decoration.  `caffeinate -s` is documented as valid ONLY on
    AC power, so on battery the strongest assertion this process can make is
    silently weaker than the flags suggest.  A long search deserves to be
    told that at start rather than to be found asleep.
    """
    executable = shutil.which("pmset")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "-g", "batt"], capture_output=True, text=True,
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.lower()
    if "'ac power'" in text or "drawing from 'ac power'" in text:
        return True
    if "'battery power'" in text:
        return False
    return None


def _start_macos_keep_awake() -> subprocess.Popen[bytes] | None:
    """Hold idle/disk/system sleep assertions while this monthly CLI is alive.

    HONEST LIMIT, stated here because no flag can remove it: caffeinate
    asserts against SOFTWARE sleep paths only.  Closing the lid of a Mac
    laptop triggers clamshell sleep in the power-management layer below any
    assertion this process can hold, unless the machine is on AC power with
    an external display attached.  The operator instruction "keep it plugged
    in and the lid open" is therefore part of the contract, not a
    superstition, and the warning below says so at the one moment it can
    still be acted on.
    """
    if sys.platform != "darwin":
        return None
    executable = shutil.which("caffeinate")
    if executable is None:
        print(
            "warning: caffeinate is unavailable; this long search cannot "
            "prevent laptop sleep",
            file=sys.stderr,
        )
        return None
    try:
        process = subprocess.Popen(
            [executable, *KEEP_AWAKE_FLAGS, "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(
            f"warning: could not start caffeinate ({exc}); this long search "
            "cannot prevent laptop sleep",
            file=sys.stderr,
        )
        return None
    print(
        f"keep-awake: {executable} {' '.join(KEEP_AWAKE_FLAGS)} "
        f"-w {os.getpid()}",
        file=sys.stderr,
    )
    on_ac = _on_ac_power()
    if on_ac is False:
        print(
            "warning: this machine is on BATTERY power; caffeinate -s is "
            "honoured only on AC, so system sleep can still interrupt this "
            "search. Connect the charger.",
            file=sys.stderr,
        )
    elif on_ac is None:
        print(
            "warning: power source could not be read; connect the charger "
            "before leaving this search unattended",
            file=sys.stderr,
        )
    print(
        "note: closing the laptop lid suspends the machine below any "
        "caffeinate assertion. Leave the lid OPEN for the whole run.",
        file=sys.stderr,
    )
    return process


def _stop_macos_keep_awake(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _simulation_backends():
    """Import the SUMO-side stack, lazily.

    `monthly_sumo` and `monthly_demand` reach run_scenario and
    suggest_closure_time, whose module-scope numpy/pandas/SciPy imports cost
    ~110 MiB. Nothing in argument validation, the exact preflight or the
    streaming enumeration needs any of it, so a search that is refused before
    it starts must not pay for it. Imported here, in one place, so the cost is
    incurred exactly once and exactly where a simulation becomes real.
    """
    from traffic_sim.simulation.monthly_demand import (
        MonthlyDemandResolverRunner,
        recover_live_demand_release,
    )
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    return (ArchivedDemandSumoRunner, MonthlyDemandResolverRunner,
            recover_live_demand_release)


def _proxy_screen_builder(path, **kwargs):
    """Frozen-campaign proxy screening, imported on use.

    `screen_monthly_closures` is only needed by --screening-mode=proxy.
    """
    from screen_monthly_closures import build_screening_artifact
    from traffic_sim.simulation.monthly_proxy import (
        HELD_OUT_VALIDATED_SHORTLIST_POLICY,
    )

    return build_screening_artifact(
        path,
        policy=HELD_OUT_VALIDATED_SHORTLIST_POLICY,
        **kwargs,
    )


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _full_digest(payload: Mapping[str, Any]) -> str:
    """Use the full content key required by the Gate S source contract."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _resolve_phase_reference(raw_path: Any, artifact_path: Path) -> Path:
    reference = Path(str(raw_path))
    return reference.resolve() if reference.is_absolute() else (
        artifact_path.parent / reference
    ).resolve()


def _validate_phase_artifact(
    artifact: Mapping[str, Any], *, name: str, artifact_path: Path,
    binding: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a producer-owned phase envelope, never caller metadata."""
    if name == "review":
        return _validate_independent_review_artifact(
            artifact, artifact_path=artifact_path, binding=binding or {}
        )
    expected_kind = "subhour_phase_review" if name == "review" else "subhour_phase_status"
    expected_schema = PHASE_REVIEW_SCHEMA if name == "review" else PHASE_STATUS_SCHEMA
    if artifact.get("schema") != expected_schema or artifact.get("kind") != expected_kind:
        raise ValueError(f"Phase 6 prerequisite {name} has an invalid schema or kind")
    if artifact.get("release_evidence") is not False:
        raise ValueError(f"Phase 6 prerequisite {name} has an invalid claim boundary")
    if name == "review":
        if artifact.get("phase") != "review":
            raise ValueError("Phase 6 review artifact has an invalid phase")
    elif artifact.get("phase") != name:
        raise ValueError(f"Phase 6 prerequisite {name} has an invalid phase")
    allowed = {"PASS", "INCONCLUSIVE"} if name in {"phase_3", "phase_4"} \
        else {"PASS", "NOT_TRIGGERED"} if name == "phase_5" else {"PASS"}
    if artifact.get("status") not in allowed:
        raise ValueError(f"Phase 6 prerequisite {name} has an invalid terminal status")
    body = {key: value for key, value in artifact.items() if key != "content_key"}
    if not isinstance(artifact.get("content_key"), str) \
            or artifact.get("content_key") != _digest(body):
        raise ValueError(f"Phase 6 prerequisite {name} content key is invalid")
    lineage = artifact.get("lineage")
    if not isinstance(lineage, Mapping):
        raise ValueError(f"Phase 6 prerequisite {name} lacks producer lineage")
    lineage = dict(lineage)
    required = {"source_digests", "input_digests", *_LINEAGE_DIGEST_FIELDS}
    if not required <= set(lineage):
        raise ValueError(f"Phase 6 prerequisite {name} lineage is incomplete")
    for field in _LINEAGE_DIGEST_FIELDS:
        if not _valid_sha256(lineage.get(field)):
            raise ValueError(f"Phase 6 prerequisite {name} has invalid {field}")
    for field in ("source_digests", "input_digests"):
        values = lineage.get(field)
        if not isinstance(values, Mapping) or not values \
                or any(not isinstance(key, str) or not _valid_sha256(value)
                       for key, value in values.items()):
            raise ValueError(f"Phase 6 prerequisite {name} has invalid {field}")
    references = artifact.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError(f"Phase 6 prerequisite {name} lacks referenced bytes")
    normalized_references: list[dict[str, Any]] = []
    referenced_digests: set[str] = set()
    for reference in references:
        if not isinstance(reference, Mapping) or not isinstance(reference.get("path"), str):
            raise ValueError(f"Phase 6 prerequisite {name} has an invalid byte reference")
        path = _resolve_phase_reference(reference["path"], artifact_path)
        if not path.is_file():
            raise ValueError(f"Phase 6 prerequisite {name} references a missing file")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if reference.get("sha256") != actual_sha256:
            raise ValueError(f"Phase 6 prerequisite {name} referenced bytes drifted")
        normalized = {"path": str(path), "sha256": actual_sha256}
        referenced_digests.add(actual_sha256)
        if "content_key" in reference:
            if not isinstance(reference.get("content_key"), str):
                raise ValueError(f"Phase 6 prerequisite {name} has an invalid content reference")
            try:
                referenced = _read(path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"Phase 6 prerequisite {name} content reference is not JSON"
                ) from error
            if referenced.get("content_key") != reference["content_key"]:
                raise ValueError(f"Phase 6 prerequisite {name} content reference drifted")
            normalized["content_key"] = reference["content_key"]
        normalized_references.append(normalized)
    lineage_digests = {
        *lineage["source_digests"].values(),
        *lineage["input_digests"].values(),
        lineage["runtime_digest"],
        lineage["policy_digest"],
    }
    if not lineage_digests <= referenced_digests:
        raise ValueError(
            f"Phase 6 prerequisite {name} lineage is not bound to referenced bytes"
        )
    derived = _phase_artifact_status(name, normalized_references, artifact_path)
    if artifact.get("status") != derived:
        raise ValueError(
            f"Phase 6 prerequisite {name} status is not derived from its actual evidence"
        )
    return lineage, {"content_key": artifact["content_key"], "references": normalized_references}


def _validate_independent_review_artifact(
    artifact: Mapping[str, Any], *, artifact_path: Path,
    binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the actual ai-flow checkpoint review, including its inputs.

    Phase 6 must consume the artifact emitted by ``tools.ai_flow``.  A
    producer in this module is deliberately not allowed to manufacture a
    replacement PASS envelope from caller-supplied lineage.
    """
    if (artifact.get("schema_version") != 1
            or artifact.get("kind") != PHASE_INDEPENDENT_REVIEW_KIND
            or artifact.get("status") != "PASS"):
        raise ValueError(
            "Phase 6 prerequisite review must be the actual ai-flow checkpoint review"
        )
    if not isinstance(artifact.get("reviewer_invocation"), str) \
            or not artifact["reviewer_invocation"]:
        raise ValueError("Phase 6 independent review lacks reviewer provenance")
    body = {key: value for key, value in artifact.items() if key != "content_digest"}
    if not _valid_sha256(artifact.get("content_digest")) \
            or artifact.get("content_digest") != _full_digest(body):
        raise ValueError("Phase 6 independent review content binding is invalid")

    def validate_bound_json(raw: Any, label: str) -> tuple[Path, dict[str, Any], str]:
        if not isinstance(raw, Mapping):
            raise ValueError(f"Phase 6 independent review lacks {label} binding")
        path = Path(str(raw.get("path", ""))).resolve()
        if not path.is_file():
            raise ValueError(f"Phase 6 independent review {label} is missing")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if raw.get("sha256") != actual_sha256:
            raise ValueError(f"Phase 6 independent review {label} bytes drifted")
        try:
            value = _read(path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Phase 6 independent review {label} is not valid JSON"
            ) from error
        return path, value, actual_sha256

    checkpoint_path, checkpoint, checkpoint_sha256 = validate_bound_json(
        binding.get("checkpoint"), "checkpoint"
    )
    if (checkpoint.get("schema_version") != 1
            or checkpoint.get("kind") != PHASE_CHECKPOINT_KIND
            or checkpoint.get("status") != "PENDING_INDEPENDENT_REVIEW"):
        raise ValueError("Phase 6 independent review checkpoint is invalid")
    checkpoint_body = {
        key: value for key, value in checkpoint.items() if key != "content_digest"
    }
    if (not _valid_sha256(checkpoint.get("content_digest"))
            or checkpoint.get("content_digest") != _full_digest(checkpoint_body)):
        raise ValueError("Phase 6 independent review checkpoint content binding is invalid")

    response_path, response, response_sha256 = validate_bound_json(
        binding.get("review_response"), "review response"
    )
    if response.get("status") != "APPROVED" or response.get("findings"):
        raise ValueError("Phase 6 independent review response is not an approval")
    if artifact.get("review_response_digest") != _full_digest(response):
        raise ValueError("Phase 6 independent review response is not bound")
    if artifact.get("checkpoint_content_digest") != checkpoint.get("content_digest"):
        raise ValueError("Phase 6 independent review is bound to a different checkpoint")
    if artifact.get("source_digest") != checkpoint.get("source_digest") \
            or artifact.get("artifact_inventory_digest") != checkpoint.get(
                "artifact_inventory_digest"
            ) \
            or artifact.get("lineage_digest") != checkpoint.get("lineage_digest"):
        raise ValueError("Phase 6 independent review lineage is not bound to its checkpoint")

    normalized = {
        "content_digest": artifact["content_digest"],
        "checkpoint": {
            "path": str(checkpoint_path), "sha256": checkpoint_sha256,
            "content_digest": checkpoint["content_digest"],
        },
        "review_response": {
            "path": str(response_path), "sha256": response_sha256,
            "digest": artifact["review_response_digest"],
        },
    }
    return {}, normalized


def _require_phase6_green_prerequisites(statuses: Mapping[str, str]) -> None:
    """Require the reviewed Phase 0--5 gate state before full-month work.

    Phase 3/4 may legitimately finish inconclusive, but that terminal result
    is not an authorization to start the conditional full-month experiment.
    Keep this check next to both registration construction and verification so
    neither caller-supplied status maps nor a tampered registration can bypass
    the same gate.
    """
    required_pass = ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4")
    if any(statuses.get(name) != "PASS" for name in required_pass):
        raise ValueError(
            "Phase 6 requires PASS for Phase 0-4; inconclusive bounded "
            "evidence is not an execution authorization"
        )
    if statuses.get("phase_5") not in {"PASS", "NOT_TRIGGERED"}:
        raise ValueError(
            "Phase 6 requires Phase 5 PASS or NOT_TRIGGERED"
        )
    if statuses.get("review") != "PASS":
        raise ValueError("Phase 6 requires an independent review PASS")


def build_phase_status_artifact(
    *, phase: str, status: str, evidence_id: str,
    lineage: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the canonical producer-owned Phase 0--5 status envelope."""
    if phase not in _PHASE_STATUS_NAMES:
        raise ValueError(f"unsupported phase status name: {phase}")
    payload = {
        "schema": PHASE_STATUS_SCHEMA,
        "kind": "subhour_phase_status",
        "phase": phase,
        "status": status,
        "evidence_id": str(evidence_id),
        "release_evidence": False,
        "lineage": dict(lineage),
        "references": [dict(item) for item in references],
    }
    body = dict(payload)
    payload["content_key"] = _digest(body)
    return payload


def build_phase_review_artifact(
    *, lineage: Mapping[str, Any], references: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject the removed self-attested Phase 6 review constructor."""
    raise ValueError(
        "self-attested Phase 6 review envelopes are prohibited; consume the "
        "actual ai_flow_phase_3_5_independent_review artifact"
    )


def build_phase6_registration(
    spec: Any, policy: MonthlySearchPolicy, *, evidence_id: str,
    prerequisites: Mapping[str, Any], output_root: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Bind a full-month run before it can start."""
    required = ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4",
                "phase_5", "review")
    bindings: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    lineage: dict[str, Any] | None = None
    for name in required:
        item = prerequisites.get(name)
        if not isinstance(item, Mapping):
            raise ValueError(
                f"Phase 6 prerequisite {name} must bind an artifact path, "
                "bytes digest, status and lineage")
        path = Path(str(item.get("path", ""))).resolve()
        if not path.is_file():
            raise ValueError(f"Phase 6 prerequisite {name} artifact is missing")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item.get("sha256"):
            raise ValueError(f"Phase 6 prerequisite {name} bytes drifted")
        artifact = _read(path)
        artifact_lineage, normalized = _validate_phase_artifact(
            artifact, name=name, artifact_path=path, binding=item
        )
        if name != "review":
            if lineage is None:
                lineage = artifact_lineage
            elif artifact_lineage != lineage:
                raise ValueError("Phase 6 prerequisite lineage is inconsistent")
        bindings[name] = {
            "path": str(path), "sha256": digest,
            "status": artifact["status"],
            **({
                "content_key": normalized["content_key"],
                "lineage": artifact_lineage,
                "references": normalized["references"],
            } if name != "review" else {
                "content_digest": normalized["content_digest"],
                "checkpoint": normalized["checkpoint"],
                "review_response": normalized["review_response"],
            }),
        }
        statuses[name] = str(artifact["status"])
    _require_phase6_green_prerequisites(statuses)
    if lineage is None:
        raise ValueError("Phase 6 prerequisites have no shared lineage")
    if not {"source_digests", "input_digests", "runtime_digest",
            "policy_digest"} <= set(lineage):
        raise ValueError(
            "Phase 6 lineage lacks source/input/runtime/policy digests")
    output_root = Path(output_root).resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("Phase 6 output root is not fresh")
    payload = {
        "schema": PHASE6_REGISTRATION_SCHEMA,
        "kind": "subhour_full_month_registration",
        "evidence_id": str(evidence_id),
        "release_evidence": False,
        "search": {
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "policy_content_key": policy.content_key,
            "objective": policy.objective_method,
        },
        "prerequisites": statuses,
        "prerequisite_artifacts": bindings,
        "lineage": lineage,
        "budget": {
            "active_hard_stop_s": PHASE6_HARD_STOP_S,
            "publication_reserve_s": PHASE6_PUBLICATION_RESERVE_S,
            "no_new_starters_after_hard_stop": True,
            "exhaustive_fallback": False,
        },
        "fresh_output_root": str(output_root),
        "fresh_workspace_root": str(Path(workspace_root or output_root).resolve()),
        "claim_boundary": "diagnostic_only_no_product_activation",
    }
    payload["content_key"] = _digest({
        key: value for key, value in payload.items() if key != "content_key"
    })
    return payload


def verify_phase6_registration(
    registration: Mapping[str, Any], spec: Any, policy: MonthlySearchPolicy,
    *, actual_workspace_root: Path, actual_output_root: Path | None = None,
) -> None:
    """Verify the complete Phase 6 binding before any full-month work starts."""
    body = {key: value for key, value in registration.items()
            if key != "content_key"}
    if registration.get("schema") != PHASE6_REGISTRATION_SCHEMA \
            or registration.get("content_key") != _digest(body):
        raise ValueError("Phase 6 registration content binding is invalid")
    bound = registration.get("search") or {}
    if (bound.get("search_id") != spec.search_id
            or bound.get("search_content_key") != spec.content_key
            or bound.get("policy_content_key") != policy.content_key):
        raise ValueError("Phase 6 registration input binding drift")
    prerequisites = registration.get("prerequisites") or {}
    required = ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4",
                "phase_5", "review")
    artifacts = registration.get("prerequisite_artifacts") or {}
    if any(name not in artifacts for name in required):
        raise ValueError("Phase 6 prerequisite artifact bindings are incomplete")
    shared_lineage = registration.get("lineage")
    if not isinstance(shared_lineage, Mapping):
        raise ValueError("Phase 6 registration has no shared input lineage")
    required_lineage = {"source_digests", "input_digests", "runtime_digest",
                        "policy_digest"}
    if not required_lineage <= set(shared_lineage):
        raise ValueError(
            "Phase 6 lineage lacks source/input/runtime/policy digests")
    for name in required:
        binding = artifacts[name]
        path = Path(str(binding.get("path", ""))).resolve()
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != binding.get("sha256"):
            raise ValueError(f"Phase 6 prerequisite {name} bytes drifted")
        artifact = _read(path)
        artifact_lineage, normalized = _validate_phase_artifact(
            artifact, name=name, artifact_path=path, binding=binding
        )
        if name == "review":
            if (prerequisites.get(name) != artifact.get("status")
                    or binding.get("status") != artifact.get("status")
                    or binding.get("content_digest") != normalized["content_digest"]
                    or binding.get("checkpoint") != normalized["checkpoint"]
                    or binding.get("review_response") != normalized["review_response"]):
                raise ValueError(f"Phase 6 prerequisite {name} is not bound and green")
            continue
        if (prerequisites.get(name) != artifact.get("status")
                or binding.get("status") != artifact.get("status")
                or artifact_lineage != dict(shared_lineage)
                or binding.get("lineage") != artifact_lineage
                or binding.get("references") != normalized["references"]):
            raise ValueError(f"Phase 6 prerequisite {name} is not bound and green")
        if binding.get("content_key") != normalized["content_key"]:
            raise ValueError(f"Phase 6 prerequisite {name} content binding drifted")
    _require_phase6_green_prerequisites(
        {name: str(prerequisites.get(name)) for name in required}
    )
    budget = registration.get("budget") or {}
    if (budget.get("active_hard_stop_s") != PHASE6_HARD_STOP_S
            or budget.get("publication_reserve_s") != PHASE6_PUBLICATION_RESERVE_S
            or budget.get("no_new_starters_after_hard_stop") is not True
            or budget.get("exhaustive_fallback") is not False):
        raise ValueError("Phase 6 budget binding is invalid")
    actual_workspace_root = Path(actual_workspace_root).resolve()
    bound_workspace = Path(registration.get("fresh_workspace_root", "")).resolve()
    if bound_workspace != actual_workspace_root:
        raise ValueError("Phase 6 workspace root is not the registered root")
    bound_output = Path(registration.get("fresh_output_root", "")).resolve()
    if actual_output_root is not None and bound_output != Path(actual_output_root).resolve():
        raise ValueError("Phase 6 output root is not the registered root")
    for label, path in (("workspace", bound_workspace), ("output", bound_output)):
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"Phase 6 {label} root is not fresh")


RECOVERED_PUBLICATION_STATUS = "INCONCLUSIVE_PUBLICATION_UNVERIFIED"


def append_only_receipt_path(path: Path) -> Path:
    """Return the one receipt path bound to an append-only outcome path."""
    path = Path(path)
    return path.with_name("." + path.name + ".receipt.json")


def recover_append_only_publication(path: Path) -> dict[str, Any] | None:
    """Complete a publication interrupted between its two commit points.

    ``write_append_only_json`` must link the complete outcome bytes before it
    can hash them into the mandatory receipt.  Process death inside that
    window used to leave an immutable outcome that every terminal validator
    rejects and that no retry could repair, because the destination already
    existed.

    Recovery publishes the missing receipt for the bytes that are actually on
    disk.  It never rewrites the outcome and never invents the commit timing
    that died with the process: the recovered receipt carries a null commit
    time, ``within_deadline`` false and a non-promotable authoritative
    terminal, so a recovered READY payload can only ever be reported as
    INCONCLUSIVE.
    """
    path = Path(path)
    receipt_path = append_only_receipt_path(path)
    if not path.is_file() or receipt_path.exists():
        return None
    raw = path.read_bytes()
    try:
        committed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"committed append-only outcome is not readable JSON: {path}"
        ) from error
    if not isinstance(committed, Mapping):
        raise ValueError(
            f"committed append-only outcome is not a JSON object: {path}")
    status = str(committed.get("status"))
    receipt = {
        "schema": "append_only_publication_receipt_v1",
        "path": str(path.resolve()),
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "status": status,
        "authoritative_status": (
            RECOVERED_PUBLICATION_STATUS if status == "READY" else status),
        "committed_elapsed_s": None,
        "publication_deadline_s": None,
        "within_deadline": False,
        "recovered": True,
        "recovery_reason": (
            "publication was interrupted between the outcome commit and its "
            "receipt; the commit time was never observed"),
    }
    receipt["content_key"] = _digest(receipt)
    _publish_receipt_no_clobber(receipt_path, receipt)
    return receipt


def write_append_only_json(
    path: Path, payload: Mapping[str, Any], *,
    controller: ActiveTimeController | None = None,
) -> dict[str, Any]:
    """Publish one complete append-only JSON object without clobbering.

    A direct ``write_text`` can leave a truncated terminal outcome after an
    interruption.  A same-directory temporary plus hard link makes the
    destination appear only after the bytes are complete and refuses a second
    publisher.
    """
    path = Path(path)
    if path.exists():
        # The destination is immutable, so a second publisher is always
        # refused.  A crash between the outcome commit and its receipt is a
        # different fact: the evidence is durable but unvalidatable.  Publish
        # the recovery receipt for the committed bytes, then still refuse.
        recovered = recover_append_only_publication(path)
        if recovered is not None:
            raise FileExistsError(
                f"refusing to overwrite append-only manifest: {path}; "
                "published the recovery receipt for its already committed "
                f"bytes at {append_only_receipt_path(path)}")
        raise FileExistsError(f"refusing to overwrite append-only manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # A READY terminal may enter the append-only publication reserve only
    # while the registered deadline still holds.  The check is deliberately
    # before the hard-link commit: if serialization/fsync consumed the
    # remaining reserve, no misleading READY artifact is published.
    if (controller is not None and payload.get("status") == "READY"):
        controller.checkpoint("publish", publication=True)
    temporary: Path | None = None
    staging: Path | None = None
    # Keep the receipt beside the terminal but outside the configured
    # validation globs; it is a bound publication fact, not a second evidence
    # registration or outcome generation.
    receipt_path = append_only_receipt_path(path)
    if receipt_path.exists():
        raise FileExistsError(
            f"refusing to overwrite append-only publication receipt: {receipt_path}")
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if (controller is not None and payload.get("status") == "READY"):
            controller.checkpoint("publish", publication=True)
        # Use a two-link commit.  The first link makes complete bytes durable
        # without exposing the canonical terminal path; the second link is
        # the authoritative destination commit.  This lets a post-commit
        # clock check fail closed when a deliberately delayed destination
        # commit crosses the publication reserve.
        # The staging link must never be a fixed name.  A process killed
        # after the first link would otherwise leave that name behind forever
        # and every later retry would fail on it, permanently blocking a
        # publication that had not even committed.  A per-attempt name makes
        # an orphan inert: it is outside every evidence glob and is only ever
        # reclaimed by the attempt that created it.
        staging = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.committed.tmp")
        os.link(temporary, staging)
        if (controller is not None and payload.get("status") == "READY"):
            controller.checkpoint("publish", publication=True)
        os.link(staging, path)
        committed_elapsed_s = (
            round(float(controller.elapsed_s), 6)
            if controller is not None else None)
        deadline_s = (controller.publication_deadline_s
                      if controller is not None else None)
        within_deadline = (
            deadline_s is None or committed_elapsed_s <= deadline_s)
        authoritative_status = str(payload.get("status"))
        if (payload.get("status") == "READY" and not within_deadline):
            # The destination bytes are immutable once linked.  The receipt
            # therefore carries the authoritative terminal for the rare race
            # where the final link itself crosses the publication deadline.
            authoritative_status = "INCONCLUSIVE_BUDGET_EXHAUSTED"
        receipt = {
            "schema": "append_only_publication_receipt_v1",
            "path": str(path.resolve()),
            "payload_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "status": str(payload.get("status")),
            "authoritative_status": authoritative_status,
            "committed_elapsed_s": committed_elapsed_s,
            "publication_deadline_s": deadline_s,
            "within_deadline": bool(within_deadline),
        }
        receipt["content_key"] = _digest(receipt)
        # The receipt is written even for an overrun.  It is the authoritative
        # post-commit fact used by final reporting; the READY payload itself
        # is never treated as promotable when this flag is false.
        _publish_receipt_no_clobber(receipt_path, receipt)
        if (controller is not None and payload.get("status") == "READY"
                and not within_deadline):
            raise ActiveBudgetExceeded(
                "final append-only publication crossed the registered deadline")
        return receipt
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite append-only manifest: {path}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if staging is not None:
            staging.unlink(missing_ok=True)


def _publish_receipt_no_clobber(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a complete post-commit receipt without clobbering.

    The authoritative receipt is itself append-only.  It must therefore use
    the same complete-bytes-then-link pattern as the outcome: ``O_EXCL`` on a
    final path does not prevent an interruption after creation from leaving a
    truncated file that blocks every later validator.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode(
        "utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=path.name + ".",
                suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        # Persist the directory entry when the platform supports opening a
        # directory for fsync.  The no-clobber link remains the commit point.
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _phase6_search_telemetry(
    search_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Extract producer telemetry without accepting controller-made values."""
    if not isinstance(search_result, Mapping):
        return {
            "sumo_attempts": None,
            "peak_rss_bytes": None,
            "disk_growth_bytes": None,
        }
    raw = search_result.get("execution_telemetry")
    telemetry = dict(raw) if isinstance(raw, Mapping) else {}
    launch = telemetry.get("exact_launch_telemetry")
    attempts = telemetry.get("sumo_attempts")
    if attempts is None and isinstance(launch, Mapping):
        attempts = sum(
            int(counts.get("attempts", 0))
            for counts in launch.values()
            if isinstance(counts, Mapping)
        )
    if attempts is None:
        attempts = search_result.get("sumo_attempts")
    peak = telemetry.get("peak_rss_bytes")
    if peak is None:
        peak = telemetry.get("process_tree_peak_rss_bytes")
    if peak is None:
        peak = search_result.get("peak_rss_bytes")
    disk = telemetry.get("disk_growth_bytes")
    if disk is None:
        disk = search_result.get("disk_growth_bytes")
    return {
        "sumo_attempts": attempts,
        "peak_rss_bytes": peak,
        "disk_growth_bytes": disk,
    }


def _phase6_runtime_telemetry(
    runner: Any, output_root: Path, initial_disk_bytes: int,
    *, process_tree_peak_rss_bytes: int | None = None,
    process_tree_rss_error: str | None = None,
) -> dict[str, Any]:
    """Capture producer measurements while the full-month runner is live."""
    snapshot_fn = getattr(runner, "timing_snapshot", None)
    snapshot = snapshot_fn() if callable(snapshot_fn) else {}
    snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    launch = snapshot.get("exact_launch_telemetry")
    attempts = None
    if isinstance(launch, Mapping):
        attempts = sum(
            int(counts.get("attempts", 0))
            for counts in launch.values()
            if isinstance(counts, Mapping)
        )
    # RUSAGE_SELF/RUSAGE_CHILDREN is not simultaneous process-tree RSS and is
    # therefore not an admissible substitute.  Phase 6 must either carry the
    # peak from the live sampler or remain explicitly resource-incomplete.
    peak = process_tree_peak_rss_bytes
    rss_error = process_tree_rss_error
    if peak is None:
        if rss_error is None:
            rss_error = "no live process-tree RSS sampler completed"
    disk = max(0, _tree_size(Path(output_root)) - int(initial_disk_bytes))
    return {
        "sumo_attempts": attempts,
        "peak_rss_bytes": int(peak) if isinstance(peak, int) else None,
        "disk_growth_bytes": int(disk),
        "disk_roots": [str(Path(output_root).resolve())],
        "process_tree_rss_complete": isinstance(peak, int),
        "process_tree_rss_error": rss_error,
        "source": "phase6_runner_timing_and_process_telemetry_v1",
    }


def _tree_size(path: Path) -> int:
    if not Path(path).exists():
        return 0
    return sum(item.stat().st_size for item in Path(path).rglob("*")
               if item.is_file())


def _phase6_terminal_status(
    search_result: Mapping[str, Any], *, work_stopped_elapsed_s: float,
    publication_elapsed_s: float, process_tree_rss_error: str | None = None,
) -> str:
    """Classify READY using both the work stop and publication deadlines."""
    if process_tree_rss_error:
        # A sampler failure after work has started invalidates any peak it
        # collected.  Preserve the executed telemetry in the ordinary
        # receipt-bound INCONCLUSIVE terminal; never publish READY with a
        # partial census or invent a zero RSS value.
        return "INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE"
    if (
        search_result.get("status") == "unique_winner"
        and work_stopped_elapsed_s <= PHASE6_HARD_STOP_S
        and publication_elapsed_s <= (
            PHASE6_HARD_STOP_S + PHASE6_PUBLICATION_RESERVE_S)
    ):
        return "READY"
    return "INCONCLUSIVE"


def phase6_outcome(*, registration: Mapping[str, Any], status: str,
                   controller: ActiveTimeController,
                   detail: str | None = None,
                   search_result: Mapping[str, Any] | None = None,
                   new_starters_after_hard_stop: int | None = None,
                   work_stopped_elapsed_s: float | None = None,
                   publication_elapsed_s: float | None = None,
                   telemetry: Mapping[str, Any] | None = None,
                   publication_receipt_path: Path | None = None,
                   publication_outcome_path: Path | None = None) -> dict[str, Any]:
    """Create a bounded outcome without manufacturing a winner."""
    execution = (search_result or {}).get("cost_ordered_execution") or {}
    stop_proof = execution.get("stop_proof")
    work_stopped = (work_stopped_elapsed_s
                    if work_stopped_elapsed_s is not None
                    else controller.elapsed_s)
    publication_elapsed = (publication_elapsed_s
                           if publication_elapsed_s is not None
                           else controller.elapsed_s)
    ready_proof_valid = bool(
        status == "READY"
        and isinstance(search_result, Mapping)
        and search_result.get("status") == "unique_winner"
        and execution.get("terminal_status") in (None, "")
        and isinstance(stop_proof, Mapping)
        and stop_proof.get("valid_for_ready") is True
        and (search_result.get("claim_boundary") or {}).get(
            "global_best_claim_allowed") is True
        and work_stopped <= PHASE6_HARD_STOP_S
        and publication_elapsed <= (
            PHASE6_HARD_STOP_S + PHASE6_PUBLICATION_RESERVE_S)
    )
    if status == "READY" and not ready_proof_valid:
        status = "INCONCLUSIVE_NO_READY_PROOF"
    outcome = {
        "schema": "subhour_full_month_outcome_v1",
        "kind": "subhour_full_month_outcome",
        "release_evidence": False,
        "evidence_id": registration.get("evidence_id"),
        "status": status,
        "registration": {
            "evidence_id": registration.get("evidence_id"),
            "content_key": registration.get("content_key"),
        },
        "active_elapsed_s": round(controller.elapsed_s, 6),
        "active_elapsed_basis": "monotonic_awake_controller_v1",
        "eta_checkpoints": list(controller.eta_checkpoints),
        "new_starters_after_hard_stop": new_starters_after_hard_stop,
        "new_starters_after_hard_stop_measured": (
            new_starters_after_hard_stop is not None),
        "work_stopped_elapsed_s": round(float(work_stopped), 6),
        "publication_elapsed_s": round(float(publication_elapsed), 6),
        "publication_deadline_s": (
            PHASE6_HARD_STOP_S + PHASE6_PUBLICATION_RESERVE_S),
        "ready_proof_valid": ready_proof_valid,
        "cost_ordered_stop_proof": dict(stop_proof) if isinstance(stop_proof, Mapping) else None,
        "detail": detail,
        "claim_boundary": "no_READY_proof_no_product_activation",
    }
    if publication_receipt_path is not None:
        outcome["publication_receipt_path"] = str(
            Path(publication_receipt_path).resolve())
    if publication_outcome_path is not None:
        outcome["publication_outcome_path"] = str(
            Path(publication_outcome_path).resolve())
    measurements = dict(telemetry) if isinstance(telemetry, Mapping) else {}
    if not measurements:
        measurements = _phase6_search_telemetry(search_result)
    for key in ("sumo_attempts", "peak_rss_bytes", "disk_growth_bytes"):
        outcome[key] = measurements.get(key)
    if isinstance(measurements.get("execution_started"), bool):
        outcome["execution_started"] = measurements["execution_started"]
    outcome["telemetry"] = {
        key: outcome[key] for key in (
            "sumo_attempts", "peak_rss_bytes", "disk_growth_bytes"
        )
    }
    for key in ("disk_roots", "process_tree_rss_complete",
                "process_tree_rss_error"):
        outcome["telemetry"][key] = measurements.get(key)
    outcome["telemetry"]["active_elapsed_s"] = round(
        float(controller.elapsed_s), 6)
    outcome["budget_telemetry"] = {
        "active_elapsed_s": round(float(controller.elapsed_s), 6),
        "work_stopped_elapsed_s": round(float(work_stopped), 6),
        "publication_elapsed_s": round(float(publication_elapsed), 6),
        "new_starters_after_hard_stop": new_starters_after_hard_stop,
        "starter_events": list(controller.starter_events),
        "cancel_requests": int(controller.cancel_requests),
        "stop_new_starters": bool(controller.stop_new_starters),
        "sumo_attempts": outcome["sumo_attempts"],
        "peak_rss_bytes": outcome["peak_rss_bytes"],
        "disk_growth_bytes": outcome["disk_growth_bytes"],
        "disk_roots": outcome["telemetry"].get("disk_roots"),
        "process_tree_rss_complete": outcome["telemetry"].get(
            "process_tree_rss_complete"),
        "process_tree_rss_error": outcome["telemetry"].get(
            "process_tree_rss_error"),
    }
    if "execution_started" in outcome:
        outcome["budget_telemetry"]["execution_started"] = outcome[
            "execution_started"]
    if isinstance(search_result, Mapping):
        pilot = search_result.get("pilot_selection")
        selected_ids = []
        if isinstance(search_result.get("selection"), Mapping):
            selected_ids = list(search_result["selection"].get("selected_ids", []))
        if not selected_ids and isinstance(pilot, Mapping):
            selected_ids = list(pilot.get("selected_ids", []))
        if not selected_ids:
            selected_ids = [
                item.get("schedule_id") for item in search_result.get(
                    "shortlisted_schedules", []
                ) if isinstance(item, Mapping) and item.get("schedule_id")
            ]
        selection = dict(search_result.get("selection") or {})
        selection["selected_ids"] = selected_ids
        outcome["selection"] = selection
        outcome["case_results"] = list(search_result.get("case_results") or [])
        outcome["gate_s"] = dict(search_result.get("gate_s") or {
            "population_complete": False,
            "variants": {},
            "reason": "full-month outcome did not publish a complete Gate S population",
        })
    else:
        outcome.update({
            "selection": {"selected_ids": []},
            "case_results": [],
            "gate_s": {
                "population_complete": False,
                "variants": {},
                "reason": "full-month execution did not produce a Gate S population",
            },
        })
    body = dict(outcome)
    outcome["content_key"] = _full_digest(body)
    return outcome


def _bounded_exhaustive_builder(
    spec_path: Path,
    *,
    maximum_candidates: int,
    proxy_version: str = "bounded_exhaustive_sumo_v1",
) -> dict[str, Any]:
    spec = load_closure_search_spec(spec_path)
    # PR C: stream and stop at the cap.  Materialising first and checking the
    # length afterwards paid the full memory cost of exactly the searches the
    # cap exists to refuse.
    schedule_ids: list[str] = []
    for schedule in iter_closure_schedules(spec):
        schedule_ids.append(schedule.schedule_id)
        if len(schedule_ids) > maximum_candidates:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{maximum_candidates} candidates, above the explicit cap "
                f"{maximum_candidates}"
            )
    return _exhaustive_payload(
        spec_path,
        spec=spec,
        schedule_ids=schedule_ids,
        maximum_candidates=maximum_candidates,
        proxy_version=proxy_version,
    )


def _exhaustive_payload(
    spec_path: Path,
    *,
    spec,
    schedule_ids: Sequence[str],
    maximum_candidates: int,
    proxy_version: str,
) -> dict[str, Any]:
    if len(schedule_ids) > maximum_candidates:
        raise ValueError(
            f"bounded exhaustive screening generated {len(schedule_ids)} "
            f"candidates, above the explicit cap {maximum_candidates}"
        )
    return {
        "schema_version": 1,
        "kind": "monthly_closure_proxy_screening",
        "proxy_version": proxy_version,
        "search": spec.to_dict(),
        "claim_boundary": {
            "evidence_level": "no_proxy_bounded_exhaustive",
            "global_best_claim_allowed": False,
            "ui_exposure_allowed": False,
            "reason": "golden/diagnostic bounded exhaustive SUMO screening",
        },
        "candidate_count": len(schedule_ids),
        "scoreable_candidate_count": 0,
        "unavailable_candidates": [],
        "ranked_candidates": [],
        "shortlist": {
            "version": proxy_version,
            "selection_complete": True,
            "entries": [
                {
                    "schedule_id": schedule_id,
                    "selection_reasons": ["bounded_exhaustive"],
                    "proxy_rank": None,
                }
                for schedule_id in schedule_ids
            ],
        },
        "input_fingerprints": {
            "closure_search_spec": {
                "path": str(Path(spec_path).resolve()),
                "sha256": sha256_file(Path(spec_path)),
            }
        },
    }


def _independent_exhaustive_builder(
    spec_path: Path,
    *,
    maximum_candidates: int,
    maximum_daily_units: int,
    baseline_trip_duration_p99_s: int,
    preflight_report: ClosureSearchPreflight | None = None,
    budget: "DailyUnitBudget | None" = None,
    resume_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stream exact independent enumeration in transactional parent pages.

    A declared budget limits NEW unique units in this invocation. State from a
    previous invocation is cumulative, but the per-leg counter starts at zero.
    A parent is classified and committed only after every one of its units has
    been envelope-checked. If it would cross the leg budget, none of that
    parent's state is retained and the cursor remains on the previous complete
    parent. Without a budget the legacy hard cap is unchanged.
    """
    spec = load_closure_search_spec(spec_path)
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "independent exhaustive screening requires the independent policy"
        )
    if (
        budget is not None
        and maximum_candidates != budget.maximum_parent_schedules
    ):
        raise ValueError(
            "independent exhaustive parent cap differs from the declared "
            "budget"
        )
    parent_limit = (
        budget.maximum_parent_schedules
        if budget is not None else maximum_candidates
    )
    effective_total_limit = (
        budget.maximum_total_daily_units
        if budget is not None else maximum_daily_units
    )
    report = preflight_report or _independent_exhaustive_preflight(
        spec,
        budget=budget,
        maximum_candidates=parent_limit,
        maximum_daily_units=effective_total_limit,
        baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
    )
    if report is not None:
        if report.search_content_key != spec.content_key:
            raise ValueError(
                "independent exhaustive preflight belongs to another search")
        if (report.parent_schedule_limit != parent_limit
                or report.daily_unit_limit != effective_total_limit):
            raise ValueError(
                "independent exhaustive preflight uses different resource caps")
    from traffic_sim.simulation.independent_daily import daily_unit_records

    source_year = 2027 if spec.source == "forecast" else 2025
    source_start = date(source_year, 1, 1)
    source_end = date(source_year + 1, 1, 1)

    unavailable_units: dict[str, str] = {}
    evaluated_units: set[str] = set()
    unavailable_candidates: list[dict[str, Any]] = []
    eligible_ids: list[str] = []
    candidate_count = 0
    last_complete_parent: str | None = None
    resume_after: str | None = None
    resumed = True
    if resume_state is not None:
        if not isinstance(resume_state, Mapping):
            raise ValueError(
                "independent screening checkpoint must be an object")
        if resume_state.get("schema") != (
                "independent_daily_enumeration_checkpoint_v1"):
            raise ValueError("independent screening checkpoint schema is invalid")
        if resume_state.get("search_content_key") != spec.content_key:
            raise ValueError(
                "independent screening checkpoint belongs to another search")
        if budget is None or resume_state.get(
                "budget_content_key") != budget.content_key:
            raise ValueError("independent screening checkpoint budget differs")
        resume_after_raw = resume_state.get("resume_after_parent_id")
        if not isinstance(resume_after_raw, str) or not resume_after_raw:
            raise ValueError("independent screening checkpoint has no resume cursor")
        resume_after = resume_after_raw
        resumed = False
        raw_units = resume_state.get("evaluated_unit_ids")
        raw_eligible = resume_state.get("eligible_schedule_ids")
        raw_unavailable_units = resume_state.get("unavailable_units")
        raw_unavailable_candidates = resume_state.get("unavailable_candidates")
        if not isinstance(raw_units, list) or any(
                not isinstance(item, str) or not item for item in raw_units):
            raise ValueError("checkpoint evaluated units are invalid")
        if len(raw_units) != len(set(raw_units)):
            raise ValueError("checkpoint repeats an evaluated unit")
        if not isinstance(raw_eligible, list) or any(
                not isinstance(item, str) or not item for item in raw_eligible):
            raise ValueError("checkpoint eligible schedules are invalid")
        if len(raw_eligible) != len(set(raw_eligible)):
            raise ValueError("checkpoint repeats an eligible schedule")
        if not isinstance(raw_unavailable_units, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in raw_unavailable_units.items()):
            raise ValueError("checkpoint unavailable units are invalid")
        if not isinstance(raw_unavailable_candidates, list) or any(
                not isinstance(item, Mapping)
                for item in raw_unavailable_candidates):
            raise ValueError("checkpoint unavailable candidates are invalid")
        evaluated_units.update(raw_units)
        unavailable_units.update({
            str(key): str(value)
            for key, value in raw_unavailable_units.items()
        })
        if not set(unavailable_units).issubset(evaluated_units):
            raise ValueError("checkpoint has an unavailable unevaluated unit")
        eligible_ids.extend(raw_eligible)
        unavailable_candidates.extend(
            dict(item) for item in raw_unavailable_candidates)
        candidate_count = int(resume_state.get("parent_schedules", -1))
        if candidate_count < 1:
            raise ValueError("checkpoint parent count is invalid")
        unavailable_ids = [
            str(item.get("schedule_id", ""))
            for item in unavailable_candidates
        ]
        classified_ids = [*eligible_ids, *unavailable_ids]
        if (
            any(not item for item in unavailable_ids)
            or len(classified_ids) != candidate_count
            or len(classified_ids) != len(set(classified_ids))
        ):
            raise ValueError("checkpoint parent classification is inconsistent")
        last_complete_parent = resume_after

    leg_new_units = 0
    prefix_units: set[str] = set()
    prefix_schedule_ids: list[str] = []
    budget_stop: dict[str, Any] | None = None
    for schedule in iter_closure_schedules(spec):
        if resume_after and not resumed:
            records = list(daily_unit_records(spec, schedule))
            unit_ids = [unit_id for unit_id, _identity, _build in records]
            if len(unit_ids) != len(set(unit_ids)):
                raise ValueError("independent parent repeats a daily unit")
            prefix_units.update(unit_ids)
            prefix_schedule_ids.append(schedule.schedule_id)
            if schedule.schedule_id == resume_after:
                unavailable_ids = [
                    str(item["schedule_id"])
                    for item in unavailable_candidates
                ]
                if prefix_units != evaluated_units:
                    raise ValueError("checkpoint evaluated-unit prefix differs")
                if set(prefix_schedule_ids) != set(
                        [*eligible_ids, *unavailable_ids]):
                    raise ValueError("checkpoint parent prefix differs")
                resumed = True
            continue
        next_parent_count = candidate_count + 1
        if next_parent_count > parent_limit:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{parent_limit} parent schedules, above the explicit "
                f"cap {parent_limit}"
            )
        records = list(daily_unit_records(spec, schedule))
        parent_units = [unit_id for unit_id, _identity, _build in records]
        if len(parent_units) != len(set(parent_units)):
            raise ValueError("independent parent repeats a daily unit")
        new_records = [
            (unit_id, build)
            for unit_id, _identity, build in records
            if unit_id not in evaluated_units
        ]
        parent_new_count = len(new_records)
        if budget is not None:
            crossed = unit_budget.exceeded(
                budget,
                new_daily_units=leg_new_units + parent_new_count,
                total_daily_units=len(evaluated_units) + parent_new_count,
            )
            if crossed == "maximum_total_daily_units":
                raise ValueError(
                    "independent exhaustive screening exceeds the declared "
                    f"total daily-unit safety limit "
                    f"{budget.maximum_total_daily_units}"
                )
            if crossed == "maximum_daily_units":
                if leg_new_units == 0:
                    raise ValueError(
                        f"one parent requires {parent_new_count} new daily "
                        f"units, above the per-invocation budget "
                        f"{budget.maximum_daily_units}"
                    )
                budget_stop = {
                    "crossed": crossed,
                    "abandoned_parent_id": schedule.schedule_id,
                }
                break
        elif len(evaluated_units) + parent_new_count > maximum_daily_units:
            raise ValueError(
                f"independent exhaustive screening generated more than "
                f"{maximum_daily_units} unique daily SUMO units, above "
                f"the explicit cap {maximum_daily_units}"
            )
        parent_unavailable: dict[str, str] = {}
        for unit_id, build in new_records:
            daily = build()
            try:
                envelope = build_simulation_envelope(
                    spec,
                    daily,
                    baseline_trip_duration_p99_s=(
                        baseline_trip_duration_p99_s
                    ),
                    policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
                )
            except ValueError as exc:
                parent_unavailable[unit_id] = str(exc)
                continue
            envelope_start = date.fromisoformat(envelope.scenario_start[:10])
            envelope_end = date.fromisoformat(envelope.scenario_end[:10])
            if envelope_start < source_start or envelope_end > source_end:
                parent_unavailable[unit_id] = (
                    "exact warm-up/recovery envelope lies outside the "
                    f"downloaded {source_year} {spec.source} demand year"
                )
        # Commit the parent atomically only after all new units were checked.
        evaluated_units.update(unit_id for unit_id, _build in new_records)
        unavailable_units.update(parent_unavailable)
        leg_new_units += parent_new_count
        failed = [
            (unit_id, unavailable_units[unit_id])
            for unit_id in parent_units
            if unit_id in unavailable_units
        ]
        if failed:
            unavailable_candidates.append({
                "schedule_id": schedule.schedule_id,
                "evidence": {
                    "reason": "independent daily envelope unavailable",
                    "daily_units": [
                        {"unit_id": unit_id, "reason": reason}
                        for unit_id, reason in failed
                    ],
                },
                "coverage": None,
            })
        else:
            eligible_ids.append(schedule.schedule_id)
        candidate_count = next_parent_count
        last_complete_parent = schedule.schedule_id
    if resume_after and not resumed:
        raise ValueError("checkpoint resume cursor is absent from the search")
    if budget_stop is not None:
        if last_complete_parent is None:
            raise ValueError("budget cannot pause before one complete parent")
        state = unit_budget.BudgetState(
            daily_units=len(evaluated_units),
            leg_daily_units=leg_new_units,
            parent_schedules=candidate_count,
            status=unit_budget.INCOMPLETE_STATUS,
            stopped_by=str(budget_stop["crossed"]),
            resume_after_parent_id=last_complete_parent,
        )
        checkpoint_state = {
            "schema": "independent_daily_enumeration_checkpoint_v1",
            "search_content_key": spec.content_key,
            "budget_content_key": budget.content_key,
            "resume_after_parent_id": last_complete_parent,
            "evaluated_unit_ids": sorted(evaluated_units),
            "unavailable_units": dict(sorted(unavailable_units.items())),
            "eligible_schedule_ids": list(eligible_ids),
            "unavailable_candidates": unavailable_candidates,
            "parent_schedules": candidate_count,
        }
        resume_token = _digest({
            "checkpoint": checkpoint_state,
            "budget": budget.to_dict(),
        })
        return {
            "schema_version": 1,
            "kind": "monthly_closure_screening_checkpoint",
            "search": spec.to_dict(),
            "status": "paused",
            "exhaustive": False,
            "resume_token": resume_token,
            "budget": budget.to_dict(),
            "budget_state": state.to_dict(),
            "budget_message": unit_budget.describe(state, budget),
            "checkpoint_state": checkpoint_state,
            "abandoned_parent_id": budget_stop["abandoned_parent_id"],
        }
    if budget is None and report is not None and (
        report.parent_schedule_count != candidate_count
        or report.unique_daily_unit_count != len(evaluated_units)
    ):
        raise ValueError(
            "independent exhaustive preflight disagrees with streamed "
            "enumeration")
    if not eligible_ids:
        raise ValueError(
            "independent exhaustive screening has no schedules whose exact "
            "warm-up and recovery fit the downloaded demand year"
        )

    payload = _exhaustive_payload(
        spec_path,
        spec=spec,
        schedule_ids=eligible_ids,
        maximum_candidates=maximum_candidates,
        proxy_version="independent_daily_exhaustive_sumo_v1",
    )
    if budget is not None:
        state = unit_budget.BudgetState(
            daily_units=len(evaluated_units),
            leg_daily_units=leg_new_units,
            parent_schedules=candidate_count,
            status=unit_budget.COMPLETE_STATUS)
        payload["budget_state"] = state.to_dict()
        payload["budget"] = budget.to_dict()
        payload["exhaustive"] = True
        payload["budget_message"] = unit_budget.describe(state, budget)
    payload["candidate_count"] = candidate_count
    payload["scoreable_candidate_count"] = len(eligible_ids)
    payload["unavailable_candidates"] = unavailable_candidates
    payload["independent_daily_execution"] = {
        "interday_policy": spec.interday_policy,
        "work_allocation_policy": spec.work_allocation_policy,
        "unique_daily_unit_count": len(evaluated_units),
        "executable_daily_unit_count": (
            len(evaluated_units) - len(unavailable_units)
        ),
        "unavailable_daily_unit_count": len(unavailable_units),
        "maximum_daily_units": maximum_daily_units,
    }
    return payload


class _IndependentExhaustiveScreenBuilder:
    """Callable adapter exposing explicit checkpoint continuation."""

    def __init__(
        self,
        *,
        maximum_candidates: int,
        maximum_daily_units: int,
        baseline_trip_duration_p99_s: int,
        preflight_report: ClosureSearchPreflight | None,
        budget: DailyUnitBudget | None,
    ) -> None:
        self.maximum_candidates = maximum_candidates
        self.maximum_daily_units = maximum_daily_units
        self.baseline_trip_duration_p99_s = baseline_trip_duration_p99_s
        self.preflight_report = preflight_report
        self.budget = budget

    def _build(
        self,
        path: Path,
        resume_state: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return _independent_exhaustive_builder(
            path,
            maximum_candidates=self.maximum_candidates,
            maximum_daily_units=self.maximum_daily_units,
            baseline_trip_duration_p99_s=(
                self.baseline_trip_duration_p99_s),
            preflight_report=self.preflight_report,
            budget=self.budget,
            resume_state=resume_state,
        )

    def __call__(self, path: Path) -> dict[str, Any]:
        return self._build(path, None)

    def resume(
        self,
        path: Path,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        state = checkpoint.get("checkpoint_state")
        if not isinstance(state, Mapping):
            raise ValueError("screening checkpoint has no resumable state")
        return self._build(path, state)


def _independent_exhaustive_preflight(
    spec,
    *,
    budget: "DailyUnitBudget | None" = None,
    maximum_candidates: int,
    maximum_daily_units: int,
    baseline_trip_duration_p99_s: int,
) -> ClosureSearchPreflight | None:
    """Fail an over-budget independent search before ledger publication.

    The exact PR-B preflight counts without constructing schedules.  Running
    it before the monthly search workspace is opened prevents an over-budget
    search from writing a potentially large candidate ledger only to discover
    the unchanged parent/unit cap during screening.
    """
    if spec.interday_policy != "independent_daily_reset_v1":
        raise ValueError(
            "independent exhaustive screening requires the independent policy"
        )
    if (
        budget is not None
        and maximum_candidates != budget.maximum_parent_schedules
    ):
        raise ValueError(
            "independent exhaustive parent cap differs from the declared "
            "budget"
        )
    parent_limit = (
        budget.maximum_parent_schedules
        if budget is not None else maximum_candidates
    )
    try:
        report = preflight(
            spec,
            baseline_trip_duration_p99_s=baseline_trip_duration_p99_s,
            envelope_policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
            parent_schedule_limit=parent_limit,
            daily_unit_limit=maximum_daily_units,
        )
    except UnsupportedPreflightSpec:
        # The streaming path still supports legacy allocation policies whose
        # exact closed-form preflight is deliberately unavailable.  Preserve
        # that compatibility and enforce both caps while streaming below;
        # supported policies get the earlier, allocation-free refusal.
        return None
    if report.parent_schedule_count > parent_limit:
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.parent_schedule_count} parent schedules, above the "
            f"explicit cap {parent_limit}"
        )
    if report.unique_daily_unit_count > maximum_daily_units and budget is None:
        # Without a declared budget this stays fail-closed, exactly as before.
        # WITH one, refusing here would defeat the point: the budget's contract
        # is to PAUSE mid-enumeration with a resumable cursor, and a search
        # refused before it starts can never pause. The streaming loop enforces
        # it instead.
        raise ValueError(
            f"independent exhaustive preflight counted "
            f"{report.unique_daily_unit_count} unique daily SUMO units, "
            f"above the explicit cap {maximum_daily_units}"
        )
    if report.parent_schedule_count == 0:
        raise ValueError("independent exhaustive search has no legal schedules")
    return report


def _cost_source_for(spec, runner, args=None, *, daily_cost_cache=None,
                     window_cost_index=None):
    """Build the deterministic cost source for real cost-first execution.

    Takes either the parsed CLI ``args`` or an explicit ``daily_cost_cache``,
    so a benchmark can build the SAME cost source the product uses without
    fabricating an argparse namespace. One implementation, two callers.

    Prices a parent by pricing its daily units through the SAME calibrated
    archives the simulation would use, resolved by the demand resolver. No SUMO
    process is started to do it.
    """
    from traffic_sim.simulation.cost_ordered_execution import (
        IndependentDailyCostSource,
    )
    from traffic_sim.simulation.deterministic_disruption import (
        DailyCostCache,
        NetworkCostModel,
    )

    daily_runner = getattr(runner, "daily_runner", None)
    units_for = getattr(runner, "daily_units_for", None)
    if daily_runner is None or units_for is None:
        raise ValueError(
            "cost-ordered execution requires the independent daily runner; "
            "it prices a parent from its daily units")
    resolver = getattr(daily_runner, "deterministic_disruption_provider", None)
    if resolver is None:
        # IsolatedDailySumoRunner wraps the resolver for parallel execution;
        # the prices come from the resolver underneath it, never from a worker.
        inner = getattr(daily_runner, "runner", None)
        resolver = getattr(inner, "deterministic_disruption_provider", None)
    if resolver is None:
        raise ValueError(
            "cost-ordered execution needs a demand resolver that can produce a "
            "process-free disruption provider")

    # One network model for the whole search: the adjacency and free-flow
    # tables are per-network, and rebuilding them per candidate would cost more
    # than the simulations being avoided.
    network = NetworkCostModel()
    if daily_cost_cache is None:
        if args is None:
            raise ValueError(
                "a cost source needs a daily cost cache location")
        daily_cost_cache = args.daily_cost_cache
    cache = DailyCostCache(Path(daily_cost_cache))
    return IndependentDailyCostSource(
        spec,
        daily_units_for=units_for,
        provider_for=lambda unit_schedule: resolver(
            unit_schedule, cache=cache, network=network),
        cache=cache,
        window_cost_index=window_cost_index,
    )


def _publish_cost_ordered_shadow(spec, policy, *, root) -> dict:
    """Replay PR E's cost-ordered scan over a finished run, in shadow mode.

    Writes `cost-ordered-shadow.json` beside the search workspace — outside
    `artifacts/`, because it is a DIAGNOSTIC replay of a completed run, not one
    of the run's immutable artifacts, and a succeeded workspace is closed to
    publication anyway.

    Nothing about the run's result, ranking, finalists or claim boundary
    depends on this file.
    """
    from traffic_sim.simulation.cost_ordered_search import (
        shadow_from_pilot_selection,
    )
    from traffic_sim.simulation.search_workspace import load_search_workspace

    directory = Path(root) / spec.search_id
    workspace = load_search_workspace(directory, verify=False)
    records = [
        record for record in workspace.manifest.get("artifacts", ())
        if record.get("kind") == "monthly_pilot_selection"
    ]
    if len(records) != 1:
        raise ValueError(
            "cost-ordered shadow needs exactly one published pilot selection")
    payload = json.loads(
        (directory / str(records[0]["path"])).read_text(encoding="utf-8"))
    comparison = shadow_from_pilot_selection(
        payload,
        policy.pilot,
        search_content_key=spec.content_key,
        provider_identity={
            "schema": "deterministic_closure_disruption_v1",
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        },
        practical_equivalence_vehicle_hours=(
            policy.finalist.practical_equivalence_vehicle_hours
        ),
    )
    destination = directory / "cost-ordered-shadow.json"
    destination.write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return comparison


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--demand-archive",
        type=Path,
        help=(
            "Compatibility mode: one succeeded immutable demand archive "
            "covering every shortlist envelope. Omit to resolve/build and "
            "freeze one exact archive per shortlist envelope automatically."
        ),
    )
    parser.add_argument(
        "--demand-runs-root",
        type=Path,
        default=Path("runs"),
        help="Run registry searched for exact succeeded demand archives.",
    )
    parser.add_argument(
        "--demand-release-root",
        type=Path,
        default=Path("runs") / "monthly-demand-releases",
        help="Immutable envelope-to-archive release manifests.",
    )
    parser.add_argument(
        "--no-build-missing-demand",
        action="store_true",
        help="Fail instead of automatically calibrating missing envelopes.",
    )
    parser.add_argument(
        "--baseline-trip-duration-p99-s",
        type=int,
        required=True,
        help="Frozen baseline trip-duration p99 used to derive warm-up.",
    )
    parser.add_argument(
        "--screening-mode",
        choices=("proxy", "bounded-exhaustive", "independent-exhaustive",
                 "independent-cost-ordered-exact"),
        default="proxy",
        help=(
            "independent-cost-ordered-exact runs the SAME exhaustive "
            "screening and additionally replays the PR E cost-ordered scan "
            "against the run's own evidence, publishing a shadow comparison. "
            "It is SHADOW MODE: it changes no ranking, no finalist set and no "
            "claim, and it is not activated until the equivalence gate has "
            "passed on a named benchmark."
        ),
    )
    parser.add_argument(
        "--bounded-exhaustive-cap",
        type=int,
        default=12,
        help="Hard candidate cap when --screening-mode=bounded-exhaustive.",
    )
    parser.add_argument(
        "--independent-exhaustive-candidate-cap",
        type=int,
        default=100_000,
        help="Hard parent-schedule cap for exact independent daily search.",
    )
    parser.add_argument(
        "--independent-exhaustive-daily-cap",
        type=int,
        default=10_000,
        help="Hard unique daily SUMO-unit cap for exact independent search.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline-cache", type=Path)
    parser.add_argument(
        "--daily-result-cache",
        type=Path,
        default=Path("runs") / "closure-search-daily-results",
        help=(
            "Content-addressed independent-day evidence cache used when the "
            "search spec selects independent_daily_reset_v1."
        ),
    )
    parser.add_argument(
        "--daily-cost-cache",
        type=Path,
        default=Path("runs") / "closure-search-daily-costs",
        help=(
            "Content-addressed DETERMINISTIC daily cost cache used by "
            "--screening-mode=independent-cost-ordered-exact. Separate from "
            "--daily-result-cache because a price and a simulation outcome "
            "have different identities: a price binds routes, network and "
            "costing sources, an outcome also binds the SUMO runtime."
        ),
    )
    parser.add_argument(
        "--window-cost-index", type=Path, default=None,
        help=(
            "Opt-in complete Phase 5 WindowCostIndex JSON. The default is "
            "off; the index is accepted only after current source, runtime, "
            "policy and resolver-input identity checks."
        ),
    )
    parser.add_argument("--seed-workers", type=int, default=1)
    parser.add_argument(
        "--daily-workers",
        type=int,
        default=3,
        help=(
            "Isolated SUMO/TraCI worker interpreters for independent daily "
            "units. Ignored by continuous searches."
        ),
    )
    parser.add_argument(
        "--max-active-sumo-slots",
        type=int,
        default=8,
        help=(
            "One declared outer/inner SUMO process budget. The product of "
            "--daily-workers and --seed-workers may not exceed this value "
            "(default 8; evidence budget, not a speed claim)."
        ),
    )
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--warm-execution",
        dest="warm_execution",
        action="store_true",
        help=(
            "Use the validated SUMO warm-state path. Cache misses bootstrap "
            "a provisional prefix; any unusable warm attempt falls back to "
            "the unchanged cold path (default)."
        ),
    )
    execution.add_argument(
        "--cold-execution",
        dest="warm_execution",
        action="store_false",
        help="Disable warm-state execution and run every observation cold.",
    )
    parser.set_defaults(warm_execution=True)
    parser.add_argument("--daily-unit-budget", type=int, default=None,
                        help="Maximum NEW unique daily units enumerated in "
                             "one invocation. Crossing it pauses at a complete "
                             "parent and the counter resets on resume. Without "
                             "this flag the legacy hard cap applies unchanged.")
    parser.add_argument(
        "--daily-unit-total-cap",
        type=int,
        default=unit_budget.DEFAULT_TOTAL_DAILY_UNIT_LIMIT,
        help="Hard cumulative daily-unit safety cap for the complete search.",
    )
    parser.add_argument("--workspace-wait-s", type=float, default=3600.0,
                        help="Seconds to wait for the shared demand "
                             "workspace (a horizon pre-warm or the web "
                             "app may hold it) before giving up.")
    parser.add_argument(
        "--phase6-registration", type=Path,
        help="Required bound registration when this invocation is a Phase 6 "
             "full-month run; incomplete prerequisites are NOT_ALLOWED.")
    parser.add_argument(
        "--phase6-outcome", type=Path,
        help="Fresh append-only Phase 6 outcome path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.baseline_trip_duration_p99_s <= 0:
        raise SystemExit("--baseline-trip-duration-p99-s must be positive")
    if args.bounded_exhaustive_cap <= 0:
        raise SystemExit("--bounded-exhaustive-cap must be positive")
    if (
        args.independent_exhaustive_candidate_cap <= 0
        or args.independent_exhaustive_daily_cap <= 0
    ):
        raise SystemExit("independent exhaustive caps must be positive")
    if args.seed_workers < 1:
        raise SystemExit("--seed-workers must be at least 1")
    if args.daily_workers < 1:
        raise SystemExit("--daily-workers must be at least 1")
    if args.warm_execution and args.seed_workers != 1:
        raise SystemExit(
            "warm execution currently requires --seed-workers 1 because the "
            "production TraCI controller owns one active connection; use "
            "--cold-execution for parallel seed workers")
    max_active_slots = args.max_active_sumo_slots
    if max_active_slots < 1:
        raise SystemExit("--max-active-sumo-slots must be at least 1")
    if args.daily_workers * args.seed_workers > max_active_slots:
        raise SystemExit(
            "daily-workers * seed-workers exceeds the declared "
            "--max-active-sumo-slots process budget"
        )
    if args.seed_workers > 1 and args.daily_workers > 1:
        raise SystemExit(
            "parallel seed workers and parallel daily workers cannot yet be "
            "combined: isolated daily children currently own fresh SUMO "
            "processes and shared baseline-cache publication has not passed "
            "the nested-concurrency equivalence gate"
        )
    if args.daily_unit_budget is not None and args.daily_unit_budget < 1:
        raise SystemExit("--daily-unit-budget must be positive")
    if (
        args.daily_unit_budget is not None
        and args.screening_mode not in (
            "independent-exhaustive",
            "independent-cost-ordered-exact",
        )
    ):
        raise SystemExit(
            "--daily-unit-budget requires --screening-mode="
            "independent-exhaustive or independent-cost-ordered-exact"
        )
    if args.daily_unit_total_cap < 1:
        raise SystemExit("--daily-unit-total-cap must be positive")
    approved = approved_seed_workers()
    if args.seed_workers > approved and os.environ.get(
            "MONTHLY_SEED_WORKER_BENCHMARK") == "1":
        # The benchmark that produces the approval must be able to exercise
        # the unapproved path exactly once, on purpose, and say so out loud.
        print(
            f"benchmark mode: running {args.seed_workers} SUMO workers "
            f"without an approval record (approved: {approved})",
            file=sys.stderr,
        )
        approved = args.seed_workers
    if args.seed_workers > approved:
        raise SystemExit(
            f"--seed-workers {args.seed_workers} exceeds the {approved} "
            "approved by the recorded resource benchmark "
            f"({SEED_WORKER_BENCHMARK_RECORD}). Run "
            "benchmark_seed_workers.py to establish identical evidence and a "
            "measured peak RSS first; parallel SUMO stays closed until then."
        )
    if args.daily_workers > approved:
        raise SystemExit(
            f"--daily-workers {args.daily_workers} exceeds the {approved} "
            "approved isolated SUMO worker count"
        )
    # The Phase 6 clock belongs only to a verified, registered full-month
    # execution.  Screening and legacy monthly modes retain their established
    # timeout/execution behavior; passing a controller to them would silently
    # impose the Phase 6 deadline on runs that never opted into that contract.
    active_controller: ActiveTimeController | None = None
    phase6_registration = None
    phase6_initial_disk_bytes = 0
    phase6_telemetry: dict[str, Any] | None = None
    # Read the inputs and SIZE the search before anything expensive exists.
    # The exact preflight is read-only calendar arithmetic: it needs no demand
    # workspace, no network identity and no simulation stack. Running it here
    # means an over-budget search — the late surprise the scaling plan set out
    # to remove — is refused in about 22 MiB, instead of after ~110 MiB of
    # numeric imports and a wait for the shared demand lock.
    try:
        spec = load_closure_search_spec(args.spec)
        policy = MonthlySearchPolicy.from_dict(_read(args.policy))
        if args.window_cost_index is not None \
                and args.screening_mode != "independent-cost-ordered-exact":
            raise ValueError(
                "--window-cost-index requires independent cost-ordered mode")
        if args.screening_mode == "independent-cost-ordered-exact" \
                and getattr(args, "phase6_registration", None) is None:
            raise ValueError(
                "independent cost-ordered full-month runs require a bound "
                "--phase6-registration; missing manifest is NOT_ALLOWED")
        if args.screening_mode == "independent-cost-ordered-exact":
            phase6_registration = _read(args.phase6_registration)
            verify_phase6_registration(
                phase6_registration, spec, policy,
                actual_workspace_root=args.root,
                actual_output_root=args.root,
            )
            # One clock starts before preflight so the bound covers preflight,
            # demand locking, ledger, pilot, finalists and publication as one
            # active budget, but only after the Phase 6 registration has been
            # verified against this exact invocation.
            active_controller = ActiveTimeController(
                hard_stop_s=PHASE6_HARD_STOP_S,
                publication_reserve_s=PHASE6_PUBLICATION_RESERVE_S,
            )
            phase6_initial_disk_bytes = _tree_size(args.root)
        independent_preflight = (
            _independent_exhaustive_preflight(
                spec,
                maximum_candidates=(
                    args.independent_exhaustive_candidate_cap
                ),
                # The preflight REPORTS against the effective limit. With a
                # declared budget that limit is the budget, so a search the
                # budget admits is not refused before it starts by the very cap
                # the budget replaces.
                maximum_daily_units=(
                    args.daily_unit_total_cap
                    if args.daily_unit_budget is not None
                    else args.independent_exhaustive_daily_cap),
                baseline_trip_duration_p99_s=(
                    args.baseline_trip_duration_p99_s
                ),
            )
            if args.screening_mode in {"independent-exhaustive",
                                       "independent-cost-ordered-exact"}
            else None
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc

    runner = None
    work_stopped_elapsed_s: float | None = None
    phase6_rss_sampler = None
    phase6_rss_peak: int | None = None
    phase6_rss_error: str | None = None

    def stop_phase6_rss_sampler() -> None:
        """Finish the live process-tree census exactly once."""
        nonlocal phase6_rss_sampler, phase6_rss_peak, phase6_rss_error
        sampler, phase6_rss_sampler = phase6_rss_sampler, None
        if sampler is None:
            return
        try:
            phase6_rss_peak = int(sampler.stop())
        except Exception as error:  # fail closed in the terminal telemetry
            phase6_rss_error = str(error)

    def publish_phase6_terminal(
        error: BaseException, *, status: str = "INCONCLUSIVE_BUDGET_EXHAUSTED",
    ) -> None:
        """Publish a receipt-bound terminal before Phase 6 can start work.

        Admission failures (budget or trusted process census) happen before
        any starter is allowed to run.  They therefore publish an explicit
        zero-attempt terminal with numeric resource fields instead of
        allowing the final report to infer a value from a missing sampler.
        """
        if phase6_registration is None:
            raise SystemExit(str(error)) from error
        stop_phase6_rss_sampler()
        outcome_path = getattr(args, "phase6_outcome", None)
        phase6_telemetry = _phase6_runtime_telemetry(
            runner, args.root, phase6_initial_disk_bytes,
            process_tree_peak_rss_bytes=phase6_rss_peak,
            process_tree_rss_error=phase6_rss_error)
        # No runner was allowed to start when admission failed at the shared
        # workspace boundary.  Publish explicit zeros, never missing values.
        phase6_telemetry.update({
            "sumo_attempts": 0,
            "peak_rss_bytes": 0,
            "disk_growth_bytes": 0,
            "disk_roots": [str(Path(args.root).resolve())],
            "execution_started": False,
            "process_tree_rss_complete": False,
        })
        active_controller.stop_new_starters = True
        work_stopped_elapsed_s = active_controller.mark_work_stopped()
        phase6_telemetry["active_elapsed_s"] = active_controller.elapsed_s
        phase6_telemetry["work_stopped_elapsed_s"] = work_stopped_elapsed_s
        phase6_telemetry["starter_events"] = list(
            active_controller.starter_events)
        phase6_telemetry["cancel_requests"] = active_controller.cancel_requests
        phase6_telemetry["stop_new_starters"] = active_controller.stop_new_starters
        result = phase6_outcome(
            registration=phase6_registration,
            status=status,
            controller=active_controller,
            detail=str(error),
            new_starters_after_hard_stop=sum(
                bool(item.get("after_hard_stop"))
                for item in active_controller.starter_events),
            work_stopped_elapsed_s=work_stopped_elapsed_s,
            telemetry=phase6_telemetry,
            publication_receipt_path=(
                append_only_receipt_path(outcome_path)
                if outcome_path is not None else None),
            publication_outcome_path=outcome_path,
        )
        if outcome_path is None:
            raise SystemExit(
                "Phase 6 terminal requires --phase6-outcome for the "
                "append-only outcome") from error
        write_append_only_json(
            outcome_path, result, controller=active_controller)
        print(json.dumps(result, sort_keys=True))
    # A search owns the shared demand workspace for hours: it rebuilds
    # envelopes into sumo/ and snapshots the live release around them. The
    # web app and a horizon pre-warm run take the same lock, so this waits
    # for whichever of them is mid-build instead of interleaving files with
    # it - and says whose job it is waiting for.
    workspace = WorkspaceLock(f"run_monthly_closure_search {os.getpid()}")
    try:
        if active_controller is not None:
            active_controller.checkpoint("workspace")
            remaining = max(
                0.0, active_controller.hard_deadline_s
                - active_controller.elapsed_s)
            wait_s = min(float(args.workspace_wait_s), remaining)
        else:
            wait_s = float(args.workspace_wait_s)
        acquired = workspace.acquire(timeout=wait_s, poll_s=10.0)
        if not acquired:
            # The metadata file is not authoritative.  Re-probe the kernel
            # flock and take a just-freed lock if the bounded wait raced with
            # its release; otherwise route exhaustion through the producer's
            # normal terminal path.
            if workspace.holder() is None:
                acquired = workspace.acquire(timeout=0.0)
            if not acquired:
                holder = workspace.holder()
                if active_controller is not None:
                    active_controller.stop_new_starters = True
                    raise ActiveBudgetExceeded(
                        "shared demand workspace remained held until the "
                        f"registered active deadline ({holder or 'holder unknown'})")
                raise SystemExit(
                    f"demand workspace busy: {workspace.holder_description()}; "
                    "wait for it, stop it, or raise --workspace-wait-s")
    except ActiveBudgetExceeded as exc:
        publish_phase6_terminal(exc)
        workspace.release()
        return
    if active_controller is not None:
        # Process-tree RSS and reap evidence are trust prerequisites, not
        # optional diagnostics.  Probe before keep-awake/backend setup and
        # before any Phase 6 starter can be admitted.
        from tools.process_census import (
            ProcessCensusUnavailable, process_group_snapshot,
        )
        try:
            process_group_snapshot()
        except ProcessCensusUnavailable as exc:
            phase6_rss_error = str(exc)
            active_controller.stop_new_starters = True
            publish_phase6_terminal(
                exc, status="INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE")
            workspace.release()
            return
    keep_awake_process = _start_macos_keep_awake()
    try:
        # Only a run that owns the workspace may load or mutate the demand
        # backend. A refused lock therefore stays on the same light import
        # path as a refused preflight, and recovery cannot race a live owner.
        (ArchivedDemandSumoRunner, MonthlyDemandResolverRunner,
         recover_live_demand_release) = _simulation_backends()
        recovered = recover_live_demand_release()
        if recovered is not None:
            print(
                "restored the live demand release left behind by a killed "
                f"run ({len(recovered.get('entries', []))} products, "
                f"{len(recovered.get('trees', []))} directories)",
                file=sys.stderr,
            )
        study_key = _digest({
            "kind": "monthly_closure_search_study",
            "search_content_key": spec.content_key,
            "policy_content_key": policy.content_key,
            "demand_release_id": spec.demand_build_id,
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        })
        runner_options = {
            "baseline_trip_duration_p99_s": (
                args.baseline_trip_duration_p99_s
            ),
            "study_provenance_key": study_key,
            "seed_workers": args.seed_workers,
            "include_disruption": (
                policy.objective_method == "closure_cost_v1"
            ),
        }
        if args.warm_execution:
            # Construction is process-free. TraCI is resolved and SUMO starts
            # only if an eligible observation actually enters the warm path.
            # A cache miss bootstraps a provisional prefix. The runner retains
            # its fail-closed cold fallback if that bootstrap or any later
            # warm-path step is unusable.
            from traffic_sim.simulation.warm_state_boundary import (
                WarmPrefixController,
            )
            runner_options.update({
                "warm_execution": True,
                "boundary_controller": WarmPrefixController(),
            })
        if args.baseline_cache is not None:
            runner_options["cache_root"] = args.baseline_cache
        if (
            spec.interday_policy == "independent_daily_reset_v1"
            and args.demand_archive is not None
        ):
            raise ValueError(
                "independent daily searches require exact per-date demand "
                "resolution; --demand-archive compatibility mode is not valid"
            )
        if args.demand_archive is not None:
            runner = ArchivedDemandSumoRunner(
                spec,
                archive=args.demand_archive,
                **runner_options,
            )
        else:
            resolved_runner = MonthlyDemandResolverRunner(
                spec,
                runs_root=args.demand_runs_root,
                release_root=args.demand_release_root,
                build_missing=not args.no_build_missing_demand,
                envelope_policy=(
                    INDEPENDENT_DAILY_ENVELOPE_POLICY
                    if spec.interday_policy == "independent_daily_reset_v1"
                    else EnvelopePolicy()
                ),
                **runner_options,
            )
            if spec.interday_policy == "independent_daily_reset_v1":
                daily_runner = (
                    IsolatedDailySumoRunner(
                        resolved_runner,
                        unit_workers=args.daily_workers,
                    )
                    if args.daily_workers > 1
                    else resolved_runner
                )
                runner = IndependentDailyRunner(
                    spec,
                    daily_runner=daily_runner,
                    cache_root=args.daily_result_cache,
                )
            else:
                runner = resolved_runner
        if args.screening_mode == "proxy":
            # Screen with EXACTLY the frozen v4 campaign policy. Until the
            # fresh gate passes, the new shortlist remains release-blocked;
            # road_domain_status matches the
            # validation runner (in_domain); per-worksite coverage scoring
            # is a future refinement the gate did not cover.
            screen_builder = lambda path: _proxy_screen_builder(
                path,
                road_domain_status="in_domain",
            )
        elif args.screening_mode == "bounded-exhaustive":
            screen_builder = lambda path: _bounded_exhaustive_builder(
                path,
                maximum_candidates=args.bounded_exhaustive_cap,
            )
        else:
            if spec.interday_policy != "independent_daily_reset_v1":
                raise ValueError(
                    f"--screening-mode={args.screening_mode} requires an "
                    "independent daily search spec"
                )
            declared_budget = (
                DailyUnitBudget(
                    maximum_daily_units=args.daily_unit_budget,
                    maximum_total_daily_units=args.daily_unit_total_cap,
                    maximum_parent_schedules=(
                        args.independent_exhaustive_candidate_cap))
                if args.daily_unit_budget is not None else None)
            screen_builder = _IndependentExhaustiveScreenBuilder(
                maximum_candidates=(
                    args.independent_exhaustive_candidate_cap
                ),
                maximum_daily_units=args.independent_exhaustive_daily_cap,
                baseline_trip_duration_p99_s=(
                    args.baseline_trip_duration_p99_s
                ),
                preflight_report=independent_preflight,
                budget=declared_budget,
            )
        cost_source = None
        window_cost_index = None
        if args.screening_mode == "independent-cost-ordered-exact":
            # REAL cost-first execution: candidates are priced from the
            # calibrated routes before anything is simulated, and SUMO runs
            # only for the ones the ordering boundary requires. The exhaustive
            # mode remains the untouched reference.
            if args.window_cost_index is not None:
                from traffic_sim.simulation.window_cost_index import load_index
                from tools.profile_monthly_cost_ledger import (
                    producer_runtime_manifest,
                    producer_source_manifest,
                )
                window_cost_index = load_index(
                    args.window_cost_index,
                    expected_daily_units=1950,
                    expected_variant_records=5850,
                )
                bound_identity = window_cost_index.bound_identity
                if (bound_identity.get("search_content_key")
                        != spec.content_key
                        or bound_identity.get("policy_content_key")
                        != policy.content_key
                        or bound_identity.get("producer_source_manifest")
                        != producer_source_manifest()
                        or bound_identity.get("producer_runtime_manifest")
                        != producer_runtime_manifest()):
                    raise ValueError(
                        "window cost index source/input/policy identity is stale")
            cost_source = _cost_source_for(
                spec, runner, args, window_cost_index=window_cost_index)
        if active_controller is not None:
            # The monthly runner's child processes are in this process group.
            # Sample while the producer is live, before runner cleanup reaps
            # the children; ru_maxrss is intentionally not used as a fallback.
            from tools.product_arm import ProcessTreeRSSSampler
            try:
                phase6_rss_sampler = ProcessTreeRSSSampler(
                    os.getpgrp(), interval_s=0.05).start()
            except Exception as error:
                # A sampler that cannot establish a trusted census is a
                # terminal admission failure.  Do not start the monthly
                # search with an unmeasured process tree.
                phase6_rss_error = str(error)
                active_controller.stop_new_starters = True
                publish_phase6_terminal(
                    error, status="INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE")
                return
        result = run_monthly_search(
            spec,
            policy,
            runner=runner,
            screen_builder=screen_builder,
            root=args.root,
            cost_source=cost_source,
            active_controller=active_controller,
        )
        if active_controller is not None:
            # Capture the end of new work before cleanup and append-only
            # publication.  The publication reserve is measured separately.
            work_stopped_elapsed_s = (
                active_controller.work_stopped_elapsed_s
                if active_controller.work_stopped_elapsed_s is not None
                else active_controller.mark_work_stopped())
            stop_phase6_rss_sampler()
            phase6_telemetry = _phase6_runtime_telemetry(
                runner, args.root, phase6_initial_disk_bytes,
                process_tree_peak_rss_bytes=phase6_rss_peak,
                process_tree_rss_error=phase6_rss_error)
            phase6_telemetry["active_elapsed_s"] = active_controller.elapsed_s
            phase6_telemetry["work_stopped_elapsed_s"] = work_stopped_elapsed_s
        if cost_source is not None:
            print(
                "cost-ordered execution: "
                f"priced {getattr(cost_source, 'computed_units', 0)} daily "
                f"units, {getattr(cost_source, 'cache_hits', 0)} cache hits",
                file=sys.stderr,
            )
    except ActiveBudgetExceeded as exc:
        if phase6_registration is None:
            raise SystemExit(str(exc)) from exc
        stop_phase6_rss_sampler()
        outcome_path = getattr(args, "phase6_outcome", None)
        phase6_telemetry = _phase6_runtime_telemetry(
            runner, args.root, phase6_initial_disk_bytes,
            process_tree_peak_rss_bytes=phase6_rss_peak,
            process_tree_rss_error=phase6_rss_error)
        work_stopped_elapsed_s = active_controller.mark_work_stopped()
        phase6_telemetry["active_elapsed_s"] = active_controller.elapsed_s
        phase6_telemetry["work_stopped_elapsed_s"] = work_stopped_elapsed_s
        phase6_telemetry["starter_events"] = list(
            active_controller.starter_events)
        phase6_telemetry["cancel_requests"] = active_controller.cancel_requests
        phase6_telemetry["stop_new_starters"] = active_controller.stop_new_starters
        result = phase6_outcome(
            registration=phase6_registration,
            status=("INCONCLUSIVE_PROCESS_CENSUS_UNAVAILABLE"
                    if phase6_rss_error
                    else "INCONCLUSIVE_BUDGET_EXHAUSTED"),
            controller=active_controller,
            detail=(f"{exc}; process-tree census unavailable: {phase6_rss_error}"
                    if phase6_rss_error else str(exc)),
            new_starters_after_hard_stop=sum(
                bool(item.get("after_hard_stop"))
                for item in active_controller.starter_events),
            work_stopped_elapsed_s=work_stopped_elapsed_s,
            telemetry=phase6_telemetry,
            publication_receipt_path=(
                append_only_receipt_path(outcome_path)
                if outcome_path is not None else None),
            publication_outcome_path=outcome_path,
        )
        if outcome_path is None:
            raise SystemExit(
                "Phase 6 budget exhausted; pass --phase6-outcome for the "
                "append-only outcome") from exc
        write_append_only_json(
            outcome_path, result, controller=active_controller)
        print(json.dumps(result, sort_keys=True))
        return
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        stop_phase6_rss_sampler()
        cleanup = getattr(runner, "cleanup", None)
        try:
            if callable(cleanup):
                cleanup()
        finally:
            try:
                _stop_macos_keep_awake(keep_awake_process)
            finally:
                workspace.release()

    if result.get("status") == "paused":
        print(json.dumps(result, sort_keys=True))
        return
    if phase6_registration is not None:
        outcome_path = getattr(args, "phase6_outcome", None)
        if outcome_path is None:
            raise SystemExit(
                "Phase 6 requires --phase6-outcome for append-only publication")
        terminal_status = _phase6_terminal_status(
            result,
            work_stopped_elapsed_s=(
                work_stopped_elapsed_s
                if work_stopped_elapsed_s is not None
                else active_controller.work_stopped_elapsed_s
                if active_controller.work_stopped_elapsed_s is not None
                else active_controller.elapsed_s),
            publication_elapsed_s=active_controller.elapsed_s,
            process_tree_rss_error=phase6_rss_error,
        )
        terminal = phase6_outcome(
            registration=phase6_registration,
            status=terminal_status,
            controller=active_controller,
            detail=("monthly search completed within the registered hard stop"
                    if terminal_status == "READY" else
                    "monthly search completed without a READY proof"),
            search_result=result,
            new_starters_after_hard_stop=sum(
                bool(item.get("after_hard_stop"))
                for item in active_controller.starter_events),
            work_stopped_elapsed_s=(
                work_stopped_elapsed_s
                if work_stopped_elapsed_s is not None
                else active_controller.elapsed_s),
            publication_elapsed_s=active_controller.elapsed_s,
            telemetry=phase6_telemetry,
            publication_receipt_path=append_only_receipt_path(outcome_path),
            publication_outcome_path=outcome_path,
        )
        try:
            write_append_only_json(
                outcome_path, terminal, controller=active_controller)
        except ActiveBudgetExceeded:
            # If the destination link already committed, its append-only
            # receipt is the authoritative budget terminal and cannot be
            # overwritten.  If the deadline was detected before that link,
            # publish the registered budget terminal through the controller so
            # its receipt has a numeric commit time.
            terminal = phase6_outcome(
                registration=phase6_registration,
                status="INCONCLUSIVE_BUDGET_EXHAUSTED",
                controller=active_controller,
                detail="final Phase 6 outcome publication crossed 60 minutes",
                search_result=result,
                new_starters_after_hard_stop=sum(
                    bool(item.get("after_hard_stop"))
                    for item in active_controller.starter_events),
                work_stopped_elapsed_s=(
                    work_stopped_elapsed_s
                    if work_stopped_elapsed_s is not None
                    else active_controller.work_stopped_elapsed_s),
                publication_elapsed_s=active_controller.elapsed_s,
                telemetry=phase6_telemetry,
                publication_receipt_path=append_only_receipt_path(outcome_path),
                publication_outcome_path=outcome_path,
            )
            # A post-commit deadline overrun leaves the canonical bytes and
            # authoritative receipt in place.  A pre-commit crossing has no
            # destination yet and can safely publish the fallback once.
            if not outcome_path.exists():
                write_append_only_json(
                    outcome_path, terminal, controller=active_controller)
    boundary = result.get("claim_boundary", {})
    print(
        f"Monthly closure search {spec.search_id}: {result['status']}; "
        f"winner={result.get('winner_id')}; "
        f"scope={boundary.get('best_result_scope')}; "
        f"global_best_claim_allowed={boundary.get('global_best_claim_allowed')}; "
        f"ui_exposure_allowed={boundary.get('ui_exposure_allowed')}"
    )


if __name__ == "__main__":
    main()
