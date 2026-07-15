"""Versioned contracts shared by normal, closure, and signal studies.

The simulation tools historically accepted overlapping sets of CLI flags.  A
validated :class:`ScenarioSpec` gives them one exact definition of date,
closures, seeds, variants, and analysis windows.  This module intentionally
uses only the Python standard library so every boundary can validate a spec
before loading SUMO or the demand/model stack.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
_EDGE_ID = re.compile(r"^[^\s/\\]+$")
_MODES = frozenset({"meso", "micro"})
_VARIANTS = frozenset({"q10", "q50", "q90", "edge_shares"})


def _parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty ISO datetime")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        # Existing flow artifacts use local, timezone-less ISO epochs.  Keep
        # accepting them, but require every field in a spec to use the same
        # convention so comparisons remain deterministic.
        return parsed
    return parsed


def _ordered(start: str, end: str, label: str) -> None:
    _same_timezone_style([start, end], label)
    if _parse_time(start, f"{label}.start") >= _parse_time(end, f"{label}.end"):
        raise ValueError(f"{label}.start must be before {label}.end")


def _same_timezone_style(values: list[str], label: str) -> None:
    styles = {bool(_parse_time(value, label).tzinfo) for value in values}
    if len(styles) > 1:
        raise ValueError(f"{label} mixes timezone-aware and timezone-less datetimes")


def _edge(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or not _EDGE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty edge ID without path separators")
    return value


def _string_list(values: Any, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a list of strings")
    try:
        result = tuple(str(value) for value in values)
    except TypeError as exc:
        raise ValueError(f"{label} must be a list of strings") from exc
    if any(not value or not value.strip() for value in result):
        raise ValueError(f"{label} cannot contain empty strings")
    return result


@dataclass(frozen=True)
class AnalysisWindow:
    """Warm-up, measured interval, and drain for bounded studies."""

    warmup_s: int
    measure_start: str
    measure_end: str
    drain_s: int

    def __post_init__(self) -> None:
        if isinstance(self.warmup_s, bool) or self.warmup_s < 0:
            raise ValueError("analysis_window.warmup_s must be non-negative")
        if isinstance(self.drain_s, bool) or self.drain_s < 0:
            raise ValueError("analysis_window.drain_s must be non-negative")
        _ordered(self.measure_start, self.measure_end, "analysis_window")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AnalysisWindow":
        return cls(
            warmup_s=int(raw["warmup_s"]),
            measure_start=str(raw["measure_start"]),
            measure_end=str(raw["measure_end"]),
            drain_s=int(raw["drain_s"]),
        )


@dataclass(frozen=True)
class ClosureSpec:
    """One directed edge closure with an explicit active interval."""

    edge_id: str
    start_time: str
    end_time: str
    closure_type: str = "full"
    permitted_access_exceptions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _edge(self.edge_id, "closure.edge_id")
        _ordered(self.start_time, self.end_time, "closure")
        if not isinstance(self.closure_type, str) or not self.closure_type.strip():
            raise ValueError("closure.closure_type must be non-empty")
        object.__setattr__(
            self, "permitted_access_exceptions",
            _string_list(self.permitted_access_exceptions,
                         "closure.permitted_access_exceptions"),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ClosureSpec":
        # Accept the old API's ``begin``/``end`` names during migration.
        return cls(
            edge_id=_edge(str(raw.get("edge_id", "")), "closure.edge_id"),
            start_time=str(raw.get("start_time", raw.get("begin", ""))),
            end_time=str(raw.get("end_time", raw.get("end", ""))),
            closure_type=str(raw.get("closure_type", "full")),
            permitted_access_exceptions=tuple(
                raw.get("permitted_access_exceptions", raw.get("exceptions", ()))
            ),
        )


@dataclass(frozen=True)
class ScenarioSpec:
    """Exact inputs shared by normal, closure, and signal study tools."""

    scenario_id: str
    demand_build_id: str
    network_build_id: str
    start_time: str
    end_time: str
    closures: tuple[ClosureSpec, ...] = field(default_factory=tuple)
    simulation_mode: str = "meso"
    seed_set: tuple[int, ...] = field(default_factory=lambda: (1000, 1001, 1002))
    demand_variant_mapping: tuple[tuple[int, str], ...] = field(
        default_factory=lambda: ((1000, "q50"), (1001, "q10"), (1002, "q90"))
    )
    objective_profile: str = "default"
    signal_plan_id: str | None = None
    analysis_window: AnalysisWindow | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        for field_name in ("demand_build_id", "network_build_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        _ordered(self.start_time, self.end_time, "scenario")
        _same_timezone_style([self.start_time, self.end_time], "scenario")
        if self.simulation_mode not in _MODES:
            raise ValueError(f"simulation_mode must be one of {sorted(_MODES)}")
        if not self.seed_set or any(
            isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
            for seed in self.seed_set
        ):
            raise ValueError("seed_set must contain unique non-negative integers")
        if len(set(self.seed_set)) != len(self.seed_set):
            raise ValueError("seed_set must contain unique values")
        mapping = tuple((int(seed), str(variant))
                        for seed, variant in self.demand_variant_mapping)
        if {seed for seed, _ in mapping} != set(self.seed_set):
            raise ValueError("demand_variant_mapping must cover every seed exactly once")
        if len(mapping) != len(set(seed for seed, _ in mapping)):
            raise ValueError("demand_variant_mapping contains duplicate seeds")
        if any(variant not in _VARIANTS for _, variant in mapping):
            raise ValueError(f"demand variants must be one of {sorted(_VARIANTS)}")
        if not isinstance(self.objective_profile, str) or not self.objective_profile.strip():
            raise ValueError("objective_profile must be non-empty")
        scenario_start = _parse_time(self.start_time, "scenario.start")
        scenario_end = _parse_time(self.end_time, "scenario.end")
        for closure in self.closures:
            closure_start = _parse_time(closure.start_time, "closure.start")
            closure_end = _parse_time(closure.end_time, "closure.end")
            if closure_start < scenario_start or closure_end > scenario_end:
                raise ValueError(
                    f"closure {closure.edge_id} must fit within the scenario window"
                )
        if self.analysis_window is not None:
            _same_timezone_style(
                [self.start_time, self.end_time,
                 self.analysis_window.measure_start,
                 self.analysis_window.measure_end],
                "scenario.analysis_window",
            )
            measure_start = _parse_time(self.analysis_window.measure_start,
                                        "analysis_window.measure_start")
            measure_end = _parse_time(self.analysis_window.measure_end,
                                      "analysis_window.measure_end")
            if measure_start < scenario_start or measure_end > scenario_end:
                raise ValueError("analysis_window must fit within the scenario window")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScenarioSpec":
        mapping = raw.get("demand_variant_mapping")
        if isinstance(mapping, Mapping):
            mapping = tuple((int(seed), str(variant))
                            for seed, variant in mapping.items())
        elif mapping is None:
            mapping = ((1000, "q50"), (1001, "q10"), (1002, "q90"))
        else:
            mapping = tuple((int(seed), str(variant)) for seed, variant in mapping)
        return cls(
            scenario_id=str(raw.get("scenario_id", "")),
            demand_build_id=str(raw.get("demand_build_id", "")),
            network_build_id=str(raw.get("network_build_id", "")),
            start_time=str(raw.get("start_time", "")),
            end_time=str(raw.get("end_time", "")),
            closures=tuple(ClosureSpec.from_dict(item)
                           for item in raw.get("closures", ())),
            simulation_mode=str(raw.get("simulation_mode", "meso")),
            seed_set=tuple(int(seed) for seed in raw.get(
                "seed_set", (seed for seed, _ in mapping))),
            demand_variant_mapping=mapping,
            objective_profile=str(raw.get("objective_profile", "default")),
            signal_plan_id=(str(raw["signal_plan_id"])
                            if raw.get("signal_plan_id") is not None else None),
            analysis_window=(AnalysisWindow.from_dict(raw["analysis_window"])
                             if raw.get("analysis_window") is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = SCHEMA_VERSION
        result["demand_variant_mapping"] = {
            str(seed): variant for seed, variant in self.demand_variant_mapping
        }
        return result


@dataclass(frozen=True)
class DecisionResult:
    """Common result envelope for closure and signal decisions."""

    result_id: str
    scenario_id: str
    status: str
    provenance: str
    alternatives: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    objective_metrics: Mapping[str, Any] = field(default_factory=dict)
    uncertainty: Mapping[str, Any] = field(default_factory=dict)
    gates: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.result_id or not self.scenario_id:
            raise ValueError("result_id and scenario_id must be non-empty")
        if self.status not in {"recommended", "screening_only", "no_valid_option",
                               "inconclusive", "failed"}:
            raise ValueError("unsupported decision result status")
        if not self.provenance:
            raise ValueError("provenance must be explicit")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = SCHEMA_VERSION
        return result


def load_scenario_spec(path: Path) -> ScenarioSpec:
    """Load and validate one spec before a simulation process is started."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported ScenarioSpec schema_version")
    return ScenarioSpec.from_dict(raw)


def write_scenario_spec(path: Path, spec: ScenarioSpec) -> None:
    """Write a validated spec atomically so readers never see partial JSON."""
    spec = ScenarioSpec.from_dict(spec.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
