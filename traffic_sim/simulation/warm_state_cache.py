"""Fail-closed SUMO warm-state cache for recurring closure finalists.

SUMO state files are not portable across versions or platforms, and future
departures still come from the original route file.  Cache identity therefore
covers the exact demand/network inputs, code, SUMO runtime, platform, seed and
state options.  An entry is usable only when an equivalence certificate for
that exact identity proves that uninterrupted and save/load decision metrics
match.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from traffic_sim.core.fingerprint import (
    fingerprint_files,
    git_commit,
    sha256_file,
    sumo_version,
)


SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("runs") / "closure-search-cache" / "warm-state"
DEFAULT_BASELINE_ROOT = Path("runs") / "closure-search-cache" / "baseline"
STATE_FILENAME = "state.xml.gz"
BASELINE_FILENAME = "metrics.json"
STATE_PRECISION = 16
STATE_RNG_SAVED = True
DECISION_METRIC_SCHEMA = "closure_decision_metrics_v1"
CACHE_ABSOLUTE_TOLERANCE = 0.0
CACHE_FIELD_TOLERANCES = {"travel_time_s": 1.0}
_MODES = frozenset({"meso", "micro"})


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any, *, length: int | None = None) -> str:
    result = hashlib.sha256(_canonical(value)).hexdigest()
    return result[:length] if length is not None else result


def _complete_fingerprints(
    paths: Mapping[str, Path],
    label: str,
) -> dict[str, dict[str, Any]]:
    records = fingerprint_files(paths)
    missing = [name for name, record in records.items()
               if record.get("sha256") is None]
    if missing:
        raise FileNotFoundError(
            f"{label} fingerprints are missing: {', '.join(missing)}")
    return records


@dataclass(frozen=True)
class WarmStateIdentity:
    """Every semantic and runtime input needed to interpret a SUMO state."""

    demand_build_id: str
    network_build_id: str
    demand_variant: str
    seed: int
    simulation_mode: str
    warmup_end_s: int
    inputs: Mapping[str, Mapping[str, Any]]
    source_files: Mapping[str, Mapping[str, Any]]
    git_commit: str | None
    python_version: str
    sumo_version: str
    platform_id: str
    save_state_rng: bool = STATE_RNG_SAVED
    save_state_precision: int = STATE_PRECISION

    def __post_init__(self) -> None:
        for name in ("demand_build_id", "network_build_id", "demand_variant",
                     "python_version", "sumo_version", "platform_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"warm_state_identity.{name} must be non-empty")
        if self.simulation_mode not in _MODES:
            raise ValueError(
                f"warm_state_identity.simulation_mode must be one of "
                f"{sorted(_MODES)}")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError(
                "warm_state_identity.seed must be a non-negative integer")
        if (
            isinstance(self.warmup_end_s, bool)
            or not isinstance(self.warmup_end_s, int)
            or self.warmup_end_s <= 0
        ):
            raise ValueError(
                "warm_state_identity.warmup_end_s must be a positive integer")
        if self.save_state_rng is not True:
            raise ValueError(
                "warm-state cache requires saved random-number state")
        if self.save_state_precision != STATE_PRECISION:
            raise ValueError(
                f"warm-state cache requires precision {STATE_PRECISION}")
        if self.git_commit is not None and (
            not isinstance(self.git_commit, str) or not self.git_commit.strip()
        ):
            raise ValueError(
                "warm_state_identity.git_commit must be a string or null")
        for group_name in ("inputs", "source_files"):
            group = getattr(self, group_name)
            if not isinstance(group, Mapping) or not group:
                raise ValueError(
                    f"warm_state_identity.{group_name} must be non-empty")
            for label, record in group.items():
                if (
                    not isinstance(label, str)
                    or not label
                    or not isinstance(record, Mapping)
                    or not isinstance(record.get("sha256"), str)
                    or len(record["sha256"]) != 64
                    or not isinstance(record.get("bytes"), int)
                    or record["bytes"] < 0
                ):
                    raise ValueError(
                        f"warm_state_identity.{group_name} contains "
                        "an invalid fingerprint")
            object.__setattr__(
                self,
                group_name,
                MappingProxyType({
                    label: MappingProxyType(dict(record))
                    for label, record in sorted(group.items())
                }),
            )
        network_digest = self.inputs.get("network", {}).get("sha256")
        if network_digest != self.network_build_id:
            raise ValueError(
                "warm_state_identity.network_build_id must equal the network hash")

    @property
    def content_key(self) -> str:
        return _digest(self.to_dict(), length=32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "demand_build_id": self.demand_build_id,
            "network_build_id": self.network_build_id,
            "demand_variant": self.demand_variant,
            "seed": self.seed,
            "simulation_mode": self.simulation_mode,
            "warmup_end_s": self.warmup_end_s,
            "inputs": {name: dict(record)
                       for name, record in sorted(self.inputs.items())},
            "source_files": {name: dict(record)
                             for name, record in sorted(self.source_files.items())},
            "git_commit": self.git_commit,
            "python_version": self.python_version,
            "sumo_version": self.sumo_version,
            "platform_id": self.platform_id,
            "save_state_rng": self.save_state_rng,
            "save_state_precision": self.save_state_precision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WarmStateIdentity":
        if not isinstance(raw, Mapping):
            raise ValueError("warm_state_identity must be a JSON object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported warm_state_identity schema")
        return cls(
            demand_build_id=str(raw.get("demand_build_id", "")),
            network_build_id=str(raw.get("network_build_id", "")),
            demand_variant=str(raw.get("demand_variant", "")),
            seed=raw.get("seed"),
            simulation_mode=str(raw.get("simulation_mode", "")),
            warmup_end_s=raw.get("warmup_end_s"),
            inputs=raw.get("inputs", {}),
            source_files=raw.get("source_files", {}),
            git_commit=raw.get("git_commit"),
            python_version=str(raw.get("python_version", "")),
            sumo_version=str(raw.get("sumo_version", "")),
            platform_id=str(raw.get("platform_id", "")),
            save_state_rng=raw.get("save_state_rng"),
            save_state_precision=raw.get("save_state_precision"),
        )


def create_warm_state_identity(
    *,
    demand_build_id: str,
    demand_variant: str,
    seed: int,
    simulation_mode: str,
    warmup_end_s: int,
    network_path: Path,
    route_path: Path,
    source_files: Mapping[str, Path],
    sumo_home: Path,
    baseline_additional_files: Mapping[str, Path] | None = None,
) -> WarmStateIdentity:
    """Fingerprint an exact baseline state before any cache lookup."""
    input_paths = {
        "network": Path(network_path),
        "route": Path(route_path),
        **{
            f"additional:{name}": Path(path)
            for name, path in sorted((baseline_additional_files or {}).items())
        },
    }
    inputs = _complete_fingerprints(input_paths, "warm-state input")
    repository_root = Path(__file__).resolve().parents[2]
    mandatory_sources = {
        "scenario_runner": repository_root / "run_scenario.py",
        "warm_state_cache": Path(__file__).resolve(),
    }
    overlap = set(mandatory_sources) & set(source_files)
    if overlap:
        raise ValueError(
            "warm-state source labels are reserved: "
            + ", ".join(sorted(overlap)))
    sources = _complete_fingerprints(
        {**mandatory_sources,
         **{name: Path(path) for name, path in source_files.items()}},
        "warm-state source",
    )
    version = sumo_version(Path(sumo_home))
    if version is None:
        raise ValueError("SUMO version is unavailable")
    return WarmStateIdentity(
        demand_build_id=demand_build_id,
        network_build_id=str(inputs["network"]["sha256"]),
        demand_variant=demand_variant,
        seed=seed,
        simulation_mode=simulation_mode,
        warmup_end_s=warmup_end_s,
        inputs=inputs,
        source_files=sources,
        git_commit=git_commit(),
        python_version=sys.version.split()[0],
        sumo_version=version,
        platform_id=platform.platform(),
    )


@dataclass(frozen=True)
class EquivalenceCertificate:
    identity_key: str
    status: str
    metric_schema: str
    uninterrupted_sha256: str
    branched_sha256: str
    absolute_tolerance: float
    field_tolerances: Mapping[str, float]
    max_absolute_delta: float
    mismatch: str | None = None

    def __post_init__(self) -> None:
        if (
            len(self.identity_key) != 32
            or any(char not in "0123456789abcdef" for char in self.identity_key)
        ):
            raise ValueError("equivalence identity_key is invalid")
        if self.status not in {"pass", "fail"}:
            raise ValueError("equivalence status must be pass or fail")
        if not self.metric_schema:
            raise ValueError("equivalence metric_schema must be explicit")
        if (
            isinstance(self.absolute_tolerance, bool)
            or not math.isfinite(self.absolute_tolerance)
            or self.absolute_tolerance < 0
            or isinstance(self.max_absolute_delta, bool)
            or not math.isfinite(self.max_absolute_delta)
            or self.max_absolute_delta < 0
        ):
            raise ValueError("equivalence tolerances must be finite and non-negative")
        if self.status == "pass" and self.mismatch is not None:
            raise ValueError("passing equivalence cannot contain a mismatch")
        if not isinstance(self.field_tolerances, Mapping):
            raise ValueError("equivalence field_tolerances must be an object")
        normalized_tolerances: dict[str, float] = {}
        for field_name, tolerance in self.field_tolerances.items():
            if (
                not isinstance(field_name, str)
                or not field_name
                or isinstance(tolerance, bool)
                or not math.isfinite(tolerance)
                or tolerance < 0
            ):
                raise ValueError(
                    "equivalence field tolerances must be finite non-negative "
                    "numbers keyed by field name")
            normalized_tolerances[field_name] = float(tolerance)
        object.__setattr__(
            self, "field_tolerances",
            MappingProxyType(dict(sorted(normalized_tolerances.items()))),
        )
        for name in ("uninterrupted_sha256", "branched_sha256"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"equivalence {name} is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key,
            "status": self.status,
            "metric_schema": self.metric_schema,
            "uninterrupted_sha256": self.uninterrupted_sha256,
            "branched_sha256": self.branched_sha256,
            "absolute_tolerance": self.absolute_tolerance,
            "field_tolerances": dict(self.field_tolerances),
            "max_absolute_delta": self.max_absolute_delta,
            "mismatch": self.mismatch,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EquivalenceCertificate":
        if not isinstance(raw, Mapping):
            raise ValueError("equivalence certificate must be a JSON object")
        return cls(
            identity_key=raw.get("identity_key"),
            status=raw.get("status"),
            metric_schema=raw.get("metric_schema"),
            uninterrupted_sha256=raw.get("uninterrupted_sha256"),
            branched_sha256=raw.get("branched_sha256"),
            absolute_tolerance=raw.get("absolute_tolerance"),
            field_tolerances=raw.get("field_tolerances", {}),
            max_absolute_delta=raw.get("max_absolute_delta"),
            mismatch=raw.get("mismatch"),
        )


def _compare_values(
    expected: Any,
    actual: Any,
    *,
    path: str,
    tolerance: float,
    field_tolerances: Mapping[str, float],
    field_name: str | None = None,
) -> tuple[float, str | None]:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (0.0, None) if expected is actual else (
            0.0, f"{path}: boolean mismatch")
    if (
        isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
    ):
        left, right = float(expected), float(actual)
        if not math.isfinite(left) or not math.isfinite(right):
            return 0.0, f"{path}: metric is not finite"
        delta = abs(left - right)
        allowed = field_tolerances.get(
            field_name, tolerance) if field_name is not None else tolerance
        return delta, (
            None if delta <= allowed
            else f"{path}: absolute delta {delta} exceeds {allowed}"
        )
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if set(expected) != set(actual):
            return 0.0, f"{path}: object keys differ"
        maximum = 0.0
        for key in sorted(expected):
            delta, mismatch = _compare_values(
                expected[key], actual[key],
                path=f"{path}.{key}", tolerance=tolerance,
                field_tolerances=field_tolerances,
                field_name=str(key))
            maximum = max(maximum, delta)
            if mismatch:
                return maximum, mismatch
        return maximum, None
    if (
        isinstance(expected, Sequence)
        and not isinstance(expected, (str, bytes))
        and isinstance(actual, Sequence)
        and not isinstance(actual, (str, bytes))
    ):
        if len(expected) != len(actual):
            return 0.0, f"{path}: sequence lengths differ"
        maximum = 0.0
        for index, (left, right) in enumerate(zip(expected, actual)):
            delta, mismatch = _compare_values(
                left, right, path=f"{path}[{index}]", tolerance=tolerance,
                field_tolerances=field_tolerances,
                field_name=field_name)
            maximum = max(maximum, delta)
            if mismatch:
                return maximum, mismatch
        return maximum, None
    return (0.0, None) if expected == actual else (
        0.0, f"{path}: values differ")


def compare_decision_metrics(
    identity: WarmStateIdentity,
    uninterrupted: Mapping[str, Any],
    branched: Mapping[str, Any],
    *,
    metric_schema: str = DECISION_METRIC_SCHEMA,
    absolute_tolerance: float = 1e-9,
    field_tolerances: Mapping[str, float] | None = None,
) -> EquivalenceCertificate:
    """Certify the exact metrics used for candidate ranking and gates."""
    if not isinstance(uninterrupted, Mapping) or not isinstance(branched, Mapping):
        raise ValueError("decision metrics must be JSON objects")
    if (
        isinstance(absolute_tolerance, bool)
        or not math.isfinite(absolute_tolerance)
        or absolute_tolerance < 0
    ):
        raise ValueError("absolute_tolerance must be finite and non-negative")
    tolerances = dict(field_tolerances or {})
    for field_name, tolerance in tolerances.items():
        if (
            not isinstance(field_name, str)
            or not field_name
            or isinstance(tolerance, bool)
            or not math.isfinite(tolerance)
            or tolerance < 0
        ):
            raise ValueError("field tolerances must be finite and non-negative")
    # Serialize first so unsupported or NaN values fail before comparison.
    uninterrupted_digest = _digest(uninterrupted)
    branched_digest = _digest(branched)
    maximum, mismatch = _compare_values(
        uninterrupted, branched, path="$", tolerance=absolute_tolerance,
        field_tolerances=tolerances)
    return EquivalenceCertificate(
        identity_key=identity.content_key,
        status="pass" if mismatch is None else "fail",
        metric_schema=metric_schema,
        uninterrupted_sha256=uninterrupted_digest,
        branched_sha256=branched_digest,
        absolute_tolerance=absolute_tolerance,
        field_tolerances=tolerances,
        max_absolute_delta=maximum,
        mismatch=mismatch,
    )


def certify_warm_state_equivalence(
    identity: WarmStateIdentity,
    uninterrupted: Mapping[str, Any],
    branched: Mapping[str, Any],
) -> EquivalenceCertificate:
    """Apply the fixed cache gate: exact primary metrics, 1 s travel time."""
    return compare_decision_metrics(
        identity,
        uninterrupted,
        branched,
        metric_schema=DECISION_METRIC_SCHEMA,
        absolute_tolerance=CACHE_ABSOLUTE_TOLERANCE,
        field_tolerances=CACHE_FIELD_TOLERANCES,
    )


def _uses_cache_equivalence_policy(
    certificate: EquivalenceCertificate,
) -> bool:
    return (
        certificate.metric_schema == DECISION_METRIC_SCHEMA
        and certificate.absolute_tolerance == CACHE_ABSOLUTE_TOLERANCE
        and dict(certificate.field_tolerances) == CACHE_FIELD_TOLERANCES
    )


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    key: str
    reason: str
    destination: Path | None = None


def _entry(root: Path, key: str) -> Path:
    if (
        len(key) != 32
        or any(char not in "0123456789abcdef" for char in key)
    ):
        raise ValueError("invalid warm-state cache key")
    return Path(root) / key


def _read_valid_manifest(
    entry: Path,
    identity: WarmStateIdentity,
) -> tuple[dict[str, Any] | None, str]:
    try:
        manifest = json.loads(
            (entry / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "manifest_missing_or_invalid"
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return None, "schema_mismatch"
    if manifest.get("kind") != "sumo_warm_state":
        return None, "kind_mismatch"
    if manifest.get("key") != identity.content_key:
        return None, "key_mismatch"
    try:
        stored_identity = WarmStateIdentity.from_dict(manifest.get("identity"))
    except (TypeError, ValueError):
        return None, "identity_invalid"
    if stored_identity.to_dict() != identity.to_dict():
        return None, "identity_mismatch"
    try:
        certificate = EquivalenceCertificate.from_dict(
            manifest.get("equivalence"))
    except (TypeError, ValueError):
        return None, "equivalence_invalid"
    if (
        certificate.status != "pass"
        or certificate.identity_key != identity.content_key
        or not _uses_cache_equivalence_policy(certificate)
    ):
        return None, "equivalence_not_proven"
    state_record = manifest.get("state")
    state_path = entry / STATE_FILENAME
    digest = sha256_file(state_path)
    if (
        not isinstance(state_record, dict)
        or state_record.get("file") != STATE_FILENAME
        or digest is None
        or digest != state_record.get("sha256")
        or state_path.stat().st_size != state_record.get("bytes")
    ):
        return None, "state_digest_mismatch"
    return manifest, "valid"


def store_warm_state(
    root: Path,
    identity: WarmStateIdentity,
    state_path: Path,
    equivalence: EquivalenceCertificate,
) -> Path:
    """Atomically store one state only after exact semantic equivalence."""
    if (
        equivalence.status != "pass"
        or equivalence.identity_key != identity.content_key
        or not _uses_cache_equivalence_policy(equivalence)
    ):
        raise ValueError(
            "warm state cannot be cached without matching equivalence proof")
    state_path = Path(state_path)
    if sha256_file(state_path) is None:
        raise FileNotFoundError(state_path)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entry = _entry(root, identity.content_key)
    lock_path = root / f".{identity.content_key}.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if entry.exists():
                _, reason = _read_valid_manifest(entry, identity)
                if reason != "valid":
                    raise ValueError(
                        f"existing warm-state cache entry is invalid: {reason}")
                return entry
            temporary = root / f".{identity.content_key}.tmp"
            shutil.rmtree(temporary, ignore_errors=True)
            temporary.mkdir()
            try:
                destination = temporary / STATE_FILENAME
                shutil.copy2(state_path, destination)
                digest = sha256_file(destination)
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "sumo_warm_state",
                    "key": identity.content_key,
                    "identity": identity.to_dict(),
                    "equivalence": equivalence.to_dict(),
                    "state": {
                        "file": STATE_FILENAME,
                        "bytes": destination.stat().st_size,
                        "sha256": digest,
                    },
                }
                (temporary / "manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, entry)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return entry


def restore_warm_state(
    root: Path,
    identity: WarmStateIdentity,
    destination: Path,
) -> CacheLookup:
    """Materialize a verified cache state without exposing partial output."""
    entry = _entry(root, identity.content_key)
    manifest, reason = _read_valid_manifest(entry, identity)
    if manifest is None:
        return CacheLookup(False, identity.content_key, reason)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".cache.tmp")
    try:
        shutil.copy2(entry / STATE_FILENAME, temporary)
        if sha256_file(temporary) != manifest["state"]["sha256"]:
            return CacheLookup(
                False, identity.content_key, "restored_state_digest_mismatch")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return CacheLookup(True, identity.content_key, "hit", destination)


def save_state_arguments(state_path: Path, *, warmup_end_s: int) -> list[str]:
    """SUMO flags for a deterministic high-precision warm-state snapshot."""
    if (
        isinstance(warmup_end_s, bool)
        or not isinstance(warmup_end_s, int)
        or warmup_end_s <= 0
    ):
        raise ValueError("warmup_end_s must be a positive integer")
    return [
        "--save-state.times", str(warmup_end_s),
        "--save-state.files", str(Path(state_path).resolve()),
        "--save-state.rng", "true",
        "--save-state.precision", str(STATE_PRECISION),
    ]


def load_state_arguments(state_path: Path) -> list[str]:
    """SUMO flags for loading a state whose identity was already verified."""
    state_path = Path(state_path)
    if not state_path.is_file():
        raise FileNotFoundError(state_path)
    return ["--load-state", str(state_path.resolve())]


@dataclass(frozen=True)
class BaselineEvidenceIdentity:
    """Identity of one matched no-closure decision-metric series."""

    warm_state_key: str
    analysis_start_s: int
    analysis_end_s: int
    affected_edges: tuple[str, ...]
    objective_profile: str
    metric_schema: str = DECISION_METRIC_SCHEMA

    def __post_init__(self) -> None:
        if (
            len(self.warm_state_key) != 32
            or any(char not in "0123456789abcdef"
                   for char in self.warm_state_key)
        ):
            raise ValueError("baseline_evidence.warm_state_key is invalid")
        for name in ("analysis_start_s", "analysis_end_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"baseline_evidence.{name} must be a non-negative integer")
        if self.analysis_end_s <= self.analysis_start_s:
            raise ValueError(
                "baseline_evidence analysis window must be increasing")
        try:
            edges = tuple(sorted(self.affected_edges))
        except TypeError as exc:
            raise ValueError(
                "baseline_evidence.affected_edges must contain strings") from exc
        if (
            not edges
            or any(not isinstance(edge, str) or not edge for edge in edges)
            or len(set(edges)) != len(edges)
        ):
            raise ValueError(
                "baseline_evidence.affected_edges must be unique non-empty strings")
        object.__setattr__(self, "affected_edges", edges)
        if not isinstance(self.objective_profile, str) or not self.objective_profile:
            raise ValueError(
                "baseline_evidence.objective_profile must be non-empty")
        if not isinstance(self.metric_schema, str) or not self.metric_schema:
            raise ValueError(
                "baseline_evidence.metric_schema must be non-empty")

    @property
    def content_key(self) -> str:
        return _digest(self.to_dict(), length=32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "warm_state_key": self.warm_state_key,
            "analysis_start_s": self.analysis_start_s,
            "analysis_end_s": self.analysis_end_s,
            "affected_edges": list(self.affected_edges),
            "objective_profile": self.objective_profile,
            "metric_schema": self.metric_schema,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BaselineEvidenceIdentity":
        if not isinstance(raw, Mapping):
            raise ValueError("baseline_evidence identity must be a JSON object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported baseline_evidence schema")
        return cls(
            warm_state_key=str(raw.get("warm_state_key", "")),
            analysis_start_s=raw.get("analysis_start_s"),
            analysis_end_s=raw.get("analysis_end_s"),
            affected_edges=tuple(raw.get("affected_edges", ())),
            objective_profile=str(raw.get("objective_profile", "")),
            metric_schema=str(raw.get("metric_schema", "")),
        )


def create_baseline_evidence_identity(
    warm_state: WarmStateIdentity,
    *,
    analysis_start_s: int,
    analysis_end_s: int,
    affected_edges: Sequence[str],
    objective_profile: str,
    metric_schema: str = DECISION_METRIC_SCHEMA,
) -> BaselineEvidenceIdentity:
    return BaselineEvidenceIdentity(
        warm_state_key=warm_state.content_key,
        analysis_start_s=analysis_start_s,
        analysis_end_s=analysis_end_s,
        affected_edges=tuple(affected_edges),
        objective_profile=objective_profile,
        metric_schema=metric_schema,
    )


@dataclass(frozen=True)
class BaselineLookup:
    hit: bool
    key: str
    reason: str
    metrics: Mapping[str, Any] | None = None


def _read_valid_baseline(
    entry: Path,
    identity: BaselineEvidenceIdentity,
) -> tuple[Mapping[str, Any] | None, str]:
    try:
        manifest = json.loads(
            (entry / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "manifest_missing_or_invalid"
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != "matched_baseline_evidence"
    ):
        return None, "schema_or_kind_mismatch"
    if manifest.get("key") != identity.content_key:
        return None, "key_mismatch"
    try:
        stored_identity = BaselineEvidenceIdentity.from_dict(
            manifest.get("identity"))
        certificate = EquivalenceCertificate.from_dict(
            manifest.get("warm_state_equivalence"))
    except (TypeError, ValueError):
        return None, "provenance_invalid"
    if stored_identity.to_dict() != identity.to_dict():
        return None, "identity_mismatch"
    if (
        certificate.status != "pass"
        or certificate.identity_key != identity.warm_state_key
        or certificate.metric_schema != identity.metric_schema
        or not _uses_cache_equivalence_policy(certificate)
    ):
        return None, "warm_state_equivalence_not_proven"
    metrics_path = entry / BASELINE_FILENAME
    record = manifest.get("metrics")
    digest = sha256_file(metrics_path)
    if (
        not isinstance(record, dict)
        or record.get("file") != BASELINE_FILENAME
        or digest is None
        or digest != record.get("sha256")
        or metrics_path.stat().st_size != record.get("bytes")
    ):
        return None, "metrics_digest_mismatch"
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "metrics_invalid"
    if not isinstance(metrics, dict):
        return None, "metrics_invalid"
    return metrics, "valid"


def store_baseline_evidence(
    root: Path,
    identity: BaselineEvidenceIdentity,
    metrics: Mapping[str, Any],
    warm_state_equivalence: EquivalenceCertificate,
) -> Path:
    """Atomically cache a matched baseline tied to a proven warm state."""
    if (
        warm_state_equivalence.status != "pass"
        or warm_state_equivalence.identity_key != identity.warm_state_key
        or warm_state_equivalence.metric_schema != identity.metric_schema
        or not _uses_cache_equivalence_policy(warm_state_equivalence)
    ):
        raise ValueError(
            "baseline evidence requires matching warm-state equivalence")
    if not isinstance(metrics, Mapping):
        raise ValueError("baseline metrics must be a JSON object")
    metrics_payload = _canonical(dict(metrics))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entry = _entry(root, identity.content_key)
    lock_path = root / f".{identity.content_key}.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if entry.exists():
                stored_metrics, reason = _read_valid_baseline(entry, identity)
                if reason != "valid":
                    raise ValueError(
                        f"existing baseline cache entry is invalid: {reason}")
                if _canonical(stored_metrics) != metrics_payload:
                    raise ValueError(
                        "existing baseline cache entry has a semantic mismatch")
                return entry
            temporary = root / f".{identity.content_key}.tmp"
            shutil.rmtree(temporary, ignore_errors=True)
            temporary.mkdir()
            try:
                metrics_path = temporary / BASELINE_FILENAME
                metrics_path.write_bytes(metrics_payload + b"\n")
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "matched_baseline_evidence",
                    "key": identity.content_key,
                    "identity": identity.to_dict(),
                    "warm_state_equivalence": warm_state_equivalence.to_dict(),
                    "metrics": {
                        "file": BASELINE_FILENAME,
                        "bytes": metrics_path.stat().st_size,
                        "sha256": sha256_file(metrics_path),
                    },
                }
                (temporary / "manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, entry)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return entry


def restore_baseline_evidence(
    root: Path,
    identity: BaselineEvidenceIdentity,
) -> BaselineLookup:
    metrics, reason = _read_valid_baseline(
        _entry(root, identity.content_key), identity)
    if metrics is None:
        return BaselineLookup(False, identity.content_key, reason)
    return BaselineLookup(True, identity.content_key, "hit", metrics)
