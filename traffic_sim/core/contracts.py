"""Versioned contracts shared by normal, closure, and signal studies.

The simulation tools historically accepted overlapping sets of CLI flags.  A
validated :class:`ScenarioSpec` gives them one exact definition of date,
closures, seeds, variants, and analysis windows.  This module intentionally
uses only the Python standard library so every boundary can validate a spec
before loading SUMO or the demand/model stack.
"""

from __future__ import annotations

import json
import hashlib
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
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.+-]+$")


def _date(value: str, label: str) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{label} must be a real calendar date") from exc
    return value


def _clock(value: str, label: str) -> str:
    if not isinstance(value, str) or not _CLOCK.fullmatch(value):
        raise ValueError(f"{label} must be HH:MM (24:00 is allowed only as an end)")
    return value


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


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
class DemandBuildSpec:
    """Validated identity of one calibrated demand build.

    A demand build is an input to every later SUMO scenario.  Keeping its
    calendar range, source and effective calibration window in one contract
    prevents the API, CLI and metadata from silently describing different
    datasets.  ``build_key`` is content-addressed and deliberately excludes
    itself, so it is stable across machines and archive filenames.
    """

    start_date: str
    source: str = "historical"
    days: int = 1
    begin: str = "00:00"
    end: str = "24:00"
    structural_reference_date: str = "2025-09-16"

    def __post_init__(self) -> None:
        _date(self.start_date, "demand.start_date")
        _date(self.structural_reference_date,
              "demand.structural_reference_date")
        if self.source not in {"historical", "forecast"}:
            raise ValueError("demand.source must be historical or forecast")
        if isinstance(self.days, bool) or not isinstance(self.days, int) or not (1 <= self.days <= 7):
            raise ValueError("demand.days must be an integer from 1 through 7")
        _clock(self.begin, "demand.begin")
        _clock(self.end, "demand.end")
        if _clock_minutes(self.end) <= _clock_minutes(self.begin):
            raise ValueError("demand.end must be after demand.begin")
        # Multi-day builds are always continuous full calendar days in the
        # pipeline.  Requiring the effective window here prevents a caller
        # from recording 06:00-10:00 while the builder actually uses 00:00-24:00.
        if self.days > 1 and (self.begin != "00:00" or self.end != "24:00"):
            raise ValueError("multi-day demand builds must use begin=00:00 and end=24:00")

    @property
    def build_key(self) -> str:
        payload = {
            "start_date": self.start_date,
            "source": self.source,
            "days": self.days,
            "begin": self.begin,
            "end": self.end,
            "structural_reference_date": self.structural_reference_date,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DemandBuildSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("demand_build_spec must be a JSON object")
        # ``date`` is accepted only as a migration alias for old API clients.
        start_date = raw.get("start_date", raw.get("date"))
        if start_date is None:
            raise ValueError("demand.start_date is required")
        raw_days = raw.get("days", 1)
        if isinstance(raw_days, bool):
            raise ValueError("demand.days must be an integer from 1 through 7")
        try:
            parsed_days = int(raw_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("demand.days must be an integer from 1 through 7") from exc
        spec = cls(
            start_date=str(start_date),
            source=str(raw.get("source", "historical")),
            days=parsed_days,
            begin=str(raw.get("begin", "00:00")),
            end=str(raw.get("end", "24:00")),
            structural_reference_date=str(
                raw.get("structural_reference_date", "2025-09-16")),
        )
        supplied_key = raw.get("build_key")
        if supplied_key is not None and str(supplied_key) != spec.build_key:
            raise ValueError("demand.build_key does not match the spec contents")
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "demand_build",
            "start_date": self.start_date,
            "source": self.source,
            "days": self.days,
            "begin": self.begin,
            "end": self.end,
            "structural_reference_date": self.structural_reference_date,
            "build_key": self.build_key,
        }


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


def load_demand_build_spec(path: Path) -> DemandBuildSpec:
    """Load and validate a demand contract before expensive calibration starts."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported DemandBuildSpec schema_version")
    if raw.get("kind", "demand_build") != "demand_build":
        raise ValueError("spec is not a demand_build contract")
    return DemandBuildSpec.from_dict(raw)


def write_demand_build_spec(path: Path, spec: DemandBuildSpec) -> None:
    """Write a validated demand contract atomically."""
    spec = DemandBuildSpec.from_dict(spec.to_dict())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(spec.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
