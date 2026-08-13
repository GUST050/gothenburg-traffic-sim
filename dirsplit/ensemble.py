"""Assemble and write `sumo/direction_ensemble_v2.json` (plan step 4).

The artifact binds five things that must not drift apart: the central
profile, the marginal intervals with their measured calibration status, the
probabilistic demand cases with weights, the stress cases with none, and the
applicability profiles — plus digests tying all of it to the source data,
model and calibration that produced it.

Writing is deliberately fail-closed and all-or-nothing. `DirectionEnsemble`
validates the pair-sum identity, the weight sum and the stress/probabilistic
separation on construction, so an artifact that would violate a plan
invariant cannot be serialised at all; there is no path that writes a
partially-valid file and reports success.

The writer also refuses to overwrite silently. A direction ensemble is an
input to every downstream demand build, so replacing one in place would
change what an already-published scenario meant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .schema import (Applicability, CentralProfile, DemandCase,
                     DirectionEnsemble, MarginalInterval, ScenarioPath,
                     content_digest)

DEFAULT_PATH = Path("sumo/direction_ensemble_v2.json")


def build_ensemble(
    *,
    central_profiles: Sequence[CentralProfile],
    demand_cases: Sequence[DemandCase],
    model_digest: str,
    training_data_digest: str,
    calibration_digest: str,
    marginal_intervals: MarginalInterval | None = None,
    stress_cases: Sequence[DemandCase] = (),
    scenario_paths: Sequence[ScenarioPath] = (),
    applicability: Sequence[Applicability] = (),
    path_generator: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> DirectionEnsemble:
    """Construct the ensemble, validating every plan invariant on the way."""
    return DirectionEnsemble(
        model_digest=model_digest,
        training_data_digest=training_data_digest,
        calibration_digest=calibration_digest,
        central_profiles=tuple(central_profiles),
        marginal_intervals=marginal_intervals,
        demand_cases=tuple(demand_cases),
        stress_cases=tuple(stress_cases),
        scenario_paths=tuple(scenario_paths),
        applicability=tuple(applicability),
        path_generator=dict(path_generator or {}),
        provenance=dict(provenance or {}),
    )


def write_ensemble(ensemble: DirectionEnsemble,
                   path: Path = DEFAULT_PATH,
                   *, overwrite: bool = False) -> dict[str, Any]:
    """Serialise atomically. Refuses to clobber unless explicitly told to.

    The temp-then-replace pattern means a reader never sees a half-written
    ensemble, and a failed write leaves the previous artifact intact and
    coherent with whatever scenarios already reference it.
    """
    payload = ensemble.to_json()
    payload["artifact_digest"] = content_digest(payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{path} already exists. A direction ensemble is an input to "
            "every downstream demand build, so replacing one in place would "
            "change what an already-published scenario meant. Pass "
            "overwrite=True deliberately, or write a new version."
        )
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)
    return payload


def load_ensemble(path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    """Read the artifact back, verifying its self-digest.

    A mismatched digest is a hard error rather than a warning: the whole
    point of binding the content is that a silently edited ensemble must not
    be usable.
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    recorded = payload.pop("artifact_digest", None)
    if recorded is not None:
        actual = content_digest(payload)
        if actual != recorded:
            raise ValueError(
                f"{path} digest mismatch: recorded {recorded[:12]}…, "
                f"computed {actual[:12]}… — the artifact was edited after "
                "it was written"
            )
    payload["artifact_digest"] = recorded
    return payload


def summarise(ensemble: DirectionEnsemble) -> dict[str, Any]:
    """A short human-facing summary for build logs."""
    intervals = ensemble.marginal_intervals
    return {
        "schema_version": ensemble.schema_version,
        "n_central_profiles": len(ensemble.central_profiles),
        "n_demand_cases": len(ensemble.demand_cases),
        "n_stress_cases": len(ensemble.stress_cases),
        "n_scenario_paths": len(ensemble.scenario_paths),
        "n_applicability": len(ensemble.applicability),
        "interval_status": (intervals.calibration_status if intervals
                            else "unavailable"),
        "interval_label": (intervals.label() if intervals
                           else "intervall saknas"),
        "has_calibrated_intervals": ensemble.has_calibrated_intervals,
        "claim_strengths": _claim_counts(ensemble.applicability),
    }


def _claim_counts(profiles: Iterable[Applicability]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for profile in profiles:
        key = profile.claim_strength()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["DEFAULT_PATH", "build_ensemble", "write_ensemble",
           "load_ensemble", "summarise"]
