"""Pre-registration and activation gates for the v2 pipeline (plan step 8).

The plan's rule: freeze the design BEFORE running, then run, then judge the
outcome against what was frozen. That ordering is the whole point — a
threshold chosen after seeing the numbers is not a gate, it is a description.

What must be frozen (plan step 8, "Frys före körning"):

* model candidates, folds, metric hierarchy;
* nominal coverage and per-group gates;
* scenario batches and the maximum compute budget;
* seed start, the adaptive repetition rule, the CI method;
* risk measures, the tie/inconclusive rule, safety gates;
* test dates, roads and closure candidates, chosen outcome-blind.

Adoption requires TWO artifacts, following the pattern this repository
already had to learn the hard way (`heldout_gate.py`, after the v4 audit
found a lone gate record is self-certifying — any byte edited inside it still
validated against itself): a REGISTRATION frozen before execution, and an
OUTCOME that names the registration's exact content key. Neither alone can
activate anything, and altering either fails closed.

This module deliberately contains no way to synthesise an outcome. Producing
one requires running the campaigns, which requires SUMO and explicit
approval; `evaluate_outcome` only judges an outcome it is handed. A missing
outcome is reported as ``unexecuted``, never as a pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from dirsplit.schema import content_digest

REGISTRATION_SCHEMA = "closure_uncertainty_shadow_registration_v1"
OUTCOME_SCHEMA = "closure_uncertainty_shadow_outcome_v1"

#: Sources whose bytes are bound into a registration. Changing any of them
#: invalidates it, because each can change what the campaign measures.
BOUND_SOURCES: tuple[str, ...] = (
    "dirsplit/schema.py",
    "dirsplit/dataset_schema.py",
    "dirsplit/models.py",
    "dirsplit/evaluate.py",
    "dirsplit/calibrate.py",
    "dirsplit/evidence.py",
    "dirsplit/scenarios.py",
    "dirsplit/scenario_evaluation.py",
    "dirsplit/ensemble.py",
    "demand/cases.py",
    "traffic_sim/core/simulation_members.py",
    "traffic_sim/simulation/uncertainty_policy.py",
    "traffic_sim/simulation/uncertainty_presentation.py",
    # The tests that give the contracts their meaning are bound too: the
    # v7 rejection in this repo's warm-state history was exactly a
    # registration whose fingerprints omitted its own regressions, so they
    # could have been weakened while its key still validated.
    "tests/test_dirsplit_schema.py",
    "tests/test_dirsplit_scenarios.py",
    "tests/test_simulation_members.py",
    "tests/test_uncertainty_policy.py",
)


class GateError(ValueError):
    """Raised when a registration or outcome violates the contract."""


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return content_digest(path.read_bytes().decode("utf-8", "replace"))


def source_fingerprints(root: Path = Path("."),
                        sources: Sequence[str] = BOUND_SOURCES
                        ) -> dict[str, str | None]:
    return {name: _file_digest(root / name) for name in sources}


@dataclass(frozen=True)
class Registration:
    """Everything frozen before a single campaign run."""

    campaign: str
    model_candidates: tuple[str, ...]
    fold_kinds: tuple[str, ...]
    metric_hierarchy: tuple[str, ...]
    nominal_coverage: float
    coverage_tolerance: float
    primary_groups: tuple[str, ...]
    scenario_batches: tuple[int, ...]
    max_compute_units: int
    seed_start: int
    min_seeds: int
    adaptive_repetition_rule: str
    ci_method: str
    bootstrap_seed: int
    risk_measures: tuple[str, ...]
    tie_rule: str
    safety_gates: tuple[str, ...]
    test_dates: tuple[str, ...]
    test_edges: tuple[str, ...]
    selection_rule: str
    outcome_blind_note: str
    source_fingerprints: Mapping[str, str | None]
    schema_version: str = REGISTRATION_SCHEMA

    def __post_init__(self) -> None:
        if not self.campaign.strip():
            raise GateError("campaign must be named")
        if not 0.0 < self.nominal_coverage < 1.0:
            raise GateError("nominal_coverage must lie in (0, 1)")
        if self.max_compute_units <= 0:
            raise GateError("max_compute_units must be positive")
        if self.min_seeds < 1:
            raise GateError("min_seeds must be at least 1")
        if not self.scenario_batches or \
                list(self.scenario_batches) != sorted(self.scenario_batches):
            raise GateError("scenario_batches must be ascending and non-empty")
        if not self.model_candidates:
            raise GateError("at least one model candidate must be registered")

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_fingerprints"] = dict(sorted(
            self.source_fingerprints.items()
        ))
        for key in ("model_candidates", "fold_kinds", "metric_hierarchy",
                    "primary_groups", "scenario_batches", "risk_measures",
                    "safety_gates", "test_dates", "test_edges"):
            payload[key] = list(getattr(self, key))
        return payload

    @property
    def content_key(self) -> str:
        return content_digest(self.to_json())


def freeze_registration(registration: Registration, path: Path) -> str:
    """Write the registration append-only. Returns its content key.

    Created with ``O_EXCL`` so the no-overwrite guarantee comes from the
    publishing primitive rather than from a check that precedes it: a file
    appearing between an existence test and the write cannot be clobbered.
    """
    payload = registration.to_json()
    payload["content_key"] = registration.content_key
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise GateError(
            f"{path} already exists. A registration is frozen evidence: "
            "publish a new version rather than editing a recorded one."
        ) from None
    with os.fdopen(handle, "w") as file:
        json.dump(payload, file, indent=1, sort_keys=True)
        file.write("\n")
    return registration.content_key


def load_registration(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def verify_registration(path: Path, root: Path = Path(".")
                        ) -> dict[str, Any]:
    """Check a registration still describes the tree it was frozen against.

    Reports rather than raises for source drift, because drift is a normal
    and expected state once development continues — what must never happen is
    a campaign being ADOPTED under a key that no longer matches. That refusal
    lives in :func:`evaluate_outcome`.
    """
    payload = load_registration(path)
    if payload is None:
        return {"present": False, "valid": False, "reason": "not found"}

    recorded_key = payload.pop("content_key", None)
    recomputed = content_digest(payload)
    payload["content_key"] = recorded_key

    live = source_fingerprints(root)
    frozen = payload.get("source_fingerprints") or {}
    drifted = sorted(
        name for name, digest in frozen.items() if live.get(name) != digest
    )
    return {
        "present": True,
        "valid": recorded_key == recomputed,
        "recorded_key": recorded_key,
        "recomputed_key": recomputed,
        "drifted_sources": drifted,
        "source_drift": bool(drifted),
        "reason": ("" if recorded_key == recomputed
                   else "the registration's own content key does not match "
                        "its body — it was edited after freezing"),
    }


@dataclass(frozen=True)
class GateOutcome:
    """The result of one executed campaign, bound to its registration."""

    campaign: str
    registration_key: str
    executed: bool
    metrics: Mapping[str, float]
    notes: str = ""
    schema_version: str = OUTCOME_SCHEMA


#: Gate thresholds. Frozen here, in code, alongside the registration that
#: names them — so a passing outcome cannot be manufactured by relaxing one
#: in a data file.
GATE_THRESHOLDS: Mapping[str, tuple[str, float]] = {
    # name: (direction, threshold). "ge" = must be >=, "le" = must be <=.
    "point_beats_baseline_pct": ("ge", 0.0),
    "interval_coverage_error": ("le", 0.05),
    "joint_variogram_gain": ("ge", 0.0),
    "scenario_stability": ("ge", 1.0),
    "seed_stability": ("ge", 1.0),
    "provenance_reproducible": ("ge", 1.0),
}


def evaluate_outcome(registration_path: Path,
                     outcome: GateOutcome | None,
                     root: Path = Path(".")) -> dict[str, Any]:
    """Judge an outcome against its frozen registration. Fail-closed.

    Four distinct refusals, none of which can be read as a pass:

    * no registration            -> ``unregistered``
    * registration edited/drifted -> ``registration_invalid``
    * no outcome                 -> ``unexecuted``
    * outcome names another key  -> ``registration_mismatch``
    """
    status = verify_registration(registration_path, root)
    if not status["present"]:
        return {"activated": False, "status": "unregistered",
                "detail": status}
    if not status["valid"]:
        return {"activated": False, "status": "registration_invalid",
                "detail": status}

    if outcome is None:
        return {
            "activated": False,
            "status": "unexecuted",
            "detail": {
                **status,
                "note": ("the campaign has not been run. Execution requires "
                         "SUMO and explicit approval; an unexecuted "
                         "registration is not a pass."),
            },
        }

    if outcome.registration_key != status["recorded_key"]:
        return {"activated": False, "status": "registration_mismatch",
                "detail": {"outcome_key": outcome.registration_key,
                           "registration_key": status["recorded_key"]}}
    if not outcome.executed:
        return {"activated": False, "status": "unexecuted",
                "detail": {"notes": outcome.notes}}
    if status["source_drift"]:
        return {
            "activated": False,
            "status": "source_drift",
            "detail": {
                **status,
                "note": ("bound sources changed since the registration was "
                         "frozen, so this outcome does not describe the "
                         "current tree"),
            },
        }

    checks: dict[str, Any] = {}
    passed = True
    for name, (direction, threshold) in sorted(GATE_THRESHOLDS.items()):
        if name not in outcome.metrics:
            checks[name] = {"ok": False, "reason": "missing"}
            passed = False
            continue
        value = float(outcome.metrics[name])
        ok = value >= threshold if direction == "ge" else value <= threshold
        checks[name] = {"ok": bool(ok), "value": value,
                        "direction": direction, "threshold": threshold}
        passed = passed and ok

    return {
        "activated": bool(passed),
        "status": "passed" if passed else "failed",
        "checks": checks,
        "detail": status,
    }


def build_default_registration(root: Path = Path(".")) -> Registration:
    """The registration this implementation is designed to be judged by."""
    from dirsplit.calibrate import COVERAGE_TOLERANCE
    from dirsplit.evaluate import (BOOTSTRAP_SEED, PRIMARY_GROUPS,
                                   SELECTION_RULE)
    from traffic_sim.simulation.uncertainty_policy import (MIN_SEEDS,
                                                           SCENARIO_BATCHES)

    return Registration(
        campaign="closure_uncertainty_shadow_v1",
        model_candidates=("constant_5050", "shrunk_dfactor",
                          "beta_binomial_dfactor", "similarity_weighted_lgbm"),
        fold_kinds=("leave_city_out", "leave_station_out", "blocked_date"),
        metric_hierarchy=("mae", "pinball", "interval_score",
                          "empirical_coverage", "variogram_score",
                          "energy_score"),
        nominal_coverage=0.8,
        coverage_tolerance=COVERAGE_TOLERANCE,
        primary_groups=tuple(PRIMARY_GROUPS),
        scenario_batches=tuple(SCENARIO_BATCHES),
        max_compute_units=10_000,
        seed_start=1000,
        min_seeds=MIN_SEEDS,
        adaptive_repetition_rule=(
            "add seeds only while the paired candidate-minus-baseline "
            "difference straddles the decision boundary; never add seeds "
            "after inspecting which candidate they would favour"
        ),
        ci_method="block bootstrap over station-days, 2000 resamples",
        bootstrap_seed=BOOTSTRAP_SEED,
        risk_measures=("weighted_expectation", "weighted_tail_q90",
                       "max_regret"),
        tie_rule=(
            "a would-be winner is downgraded to tie when expectation, tail "
            "and max_regret prefer different candidates, and to inconclusive "
            "when the ranking has not settled across two scenario batches or "
            "fewer than min_seeds seeds ran"
        ),
        safety_gates=("topological_severing", "no_detour",
                      "closure_survivability", "seed_health"),
        # Deliberately empty: outcome-blind selection of test dates, roads
        # and closure candidates has not been performed. Populating these
        # after seeing any result would break the pre-registration.
        test_dates=(),
        test_edges=(),
        selection_rule=SELECTION_RULE,
        outcome_blind_note=(
            "test_dates and test_edges are EMPTY on purpose. They must be "
            "drawn by a versioned outcome-blind rule before execution, and "
            "the registration re-frozen as v2 at that point. Filling them in "
            "after any measurement would void the pre-registration."
        ),
        source_fingerprints=source_fingerprints(root),
    )


__all__ = [
    "REGISTRATION_SCHEMA", "OUTCOME_SCHEMA", "BOUND_SOURCES",
    "GATE_THRESHOLDS", "GateError", "Registration", "GateOutcome",
    "source_fingerprints", "freeze_registration", "load_registration",
    "verify_registration", "evaluate_outcome", "build_default_registration",
]
