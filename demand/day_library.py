"""Content-addressed store of calibrated single days, and window assembly.

SPEED_ARCHITECTURE_PLAN_2026-07.md layers L1 and L2. A closure search rebuilds
the same calendar days over and over because every envelope is calibrated as
one monolithic window. Once a day's demand is a function of the day itself
(stage B seeding), it can be calibrated once, stored, and concatenated into
any window that contains it.

Two rules make that safe, and both are enforced here rather than assumed:

* **Fail closed.** An entry is used only if every artifact still hashes to
  what its manifest recorded. A missing or corrupt entry is a rebuild, never
  a silent fallback to different data.
* **Assembly is not a second implementation.** A day is stored exactly as the
  writer produced it, day-local; assembly only shifts departures by whole
  days and renumbers vehicles in order. Everything that decides WHAT is
  published happened in the writer, once.

The identity of an entry is the full fingerprint of everything that went into
it, including the source hashes of the code that produced it, so a changed
input or a changed line of solver code simply never matches an old entry.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("runs") / "demand-days"
DAY_SECONDS = 86400
# Route XML and agent JSON are highly repetitive text; stored gzipped they
# shrink ~10x, which is what makes warming a whole year ~13 GB instead of
# ~150 GB. Entries written before compression existed remain readable: every
# reader falls back to the plain name when the .gz is absent, and the
# manifest always records whichever bytes are actually on disk.
COMPRESSED_SUFFIXES = (".rou.xml", ".agents.json")
REPLACED_SUFFIX = ".replaced"


def _day_file(directory: Path, name: str) -> Path:
    """The stored path for a day artifact, whichever encoding it has."""
    compressed = Path(directory) / (name + ".gz")
    return compressed if compressed.is_file() else Path(directory) / name


def _open_day_text(directory: Path, name: str):
    """Open a stored day artifact for text reading, transparently."""
    path = _day_file(directory, name)
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path)


VEHICLE_LINE = re.compile(
    r'^(?P<head>\s*<vehicle id=")(?P<id>[^"]*)(?P<mid>" depart=")'
    r'(?P<depart>[^"]*)(?P<tail>".*)$'
)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _digest(payload: Any, *, length: int = 32) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:length]


def sha256_bytes(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DayIdentity:
    """Everything a stored day is a function of.

    ``pool_composition`` is the sorted set of day-type geometry pools present
    in the window the day is calibrated inside. It belongs in the identity
    because the PFE solves every quarter over the WHOLE shape pool: a Tuesday
    calibrated in a weekday-only window had a smaller variable set than the
    same Tuesday in a window that also contains a Saturday, so those are two
    different (both correct) results and must not share an entry.
    """

    date: str
    source: str
    pool_composition: tuple[str, ...]
    inputs: Mapping[str, Any] = field(default_factory=dict)
    source_hashes: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "date": self.date,
            "source": self.source,
            "pool_composition": list(self.pool_composition),
            "inputs": dict(self.inputs),
            "source_hashes": dict(self.source_hashes),
        }

    @property
    def key(self) -> str:
        return _digest(self.to_dict())


class LookupReason(str, Enum):
    """Why a lookup ended the way it did.

    ``get`` used to answer every one of these with the same ``None``, so a
    build could not tell an absent day from a corrupt one, nor either from a
    day that exists under a different -- equally correct -- identity. Named
    outcomes are the same device ccache uses for its per-outcome counters.
    """

    HIT = "hit"
    ENTRY_ABSENT = "entry_absent"
    # Present but unusable: parsed as something other than a manifest, or not
    # parseable at all.
    MANIFEST_UNREADABLE = "manifest_unreadable"
    SCHEMA_MISMATCH = "schema_mismatch"
    KIND_MISMATCH = "kind_mismatch"
    KEY_MISMATCH = "key_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    ARTIFACT_RECORD_INVALID = "artifact_record_invalid"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"
    ARTIFACT_SIZE_MISMATCH = "artifact_size_mismatch"
    ARTIFACT_IO_ERROR = "artifact_io_error"


class IdentityCause(str, Enum):
    """The single field class that separated two identities for one date."""

    SOURCE_CHANGE = "source_change"
    VARIANT_SUBSET = "variant_subset"
    POOL_COMPOSITION = "pool_composition"
    CANDIDATE_DRIFT = "candidate_drift"
    OTHER = "other"


# Identity fields whose leaves are compared individually. Reporting a bare
# "inputs" would name the container, not the input that actually changed.
EXPANDED_IDENTITY_FIELDS = ("inputs", "source_hashes")
CANDIDATE_FIELD_PREFIXES = ("inputs.candidate", "inputs.catalog_keys")


def diff_identity(left: Mapping[str, Any],
                  right: Mapping[str, Any]) -> tuple[str, ...]:
    """Sorted dotted paths on which two stored identities differ."""
    paths: set[str] = set()
    for name in set(left) | set(right):
        a, b = left.get(name), right.get(name)
        if a == b:
            continue
        if name in EXPANDED_IDENTITY_FIELDS and isinstance(a, Mapping) \
                and isinstance(b, Mapping):
            for leaf in set(a) | set(b):
                if a.get(leaf) != b.get(leaf):
                    paths.add(f"{name}.{leaf}")
            continue
        paths.add(name)
    return tuple(sorted(paths))


def classify_identity_difference(
        paths: Sequence[str]) -> IdentityCause | None:
    """The one cause of a difference, in the order the plan fixed.

    Order is the point. A changed source byte means the older entry could
    never have been reused whatever else differs, and a variant subset is a
    deliberate alias rather than a repeated solve. Only when neither holds
    may a difference count as possibly avoidable calendar context.
    """
    if not paths:
        return None
    if any(p.startswith("source_hashes") for p in paths):
        return IdentityCause.SOURCE_CHANGE
    if "inputs.variants" in paths:
        return IdentityCause.VARIANT_SUBSET
    if "pool_composition" in paths:
        return IdentityCause.POOL_COMPOSITION
    if any(p.startswith(prefix) for p in paths
           for prefix in CANDIDATE_FIELD_PREFIXES):
        return IdentityCause.CANDIDATE_DRIFT
    return IdentityCause.OTHER


@dataclass(frozen=True)
class DayLookup:
    """The full result of asking the library for one day."""

    manifest: dict[str, Any] | None
    outcome: Literal["hit", "miss", "rejected"]
    reason: LookupReason
    expected_key: str
    compared_key: str | None = None
    differing_fields: tuple[str, ...] = ()
    identity_cause: IdentityCause | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe record for build metadata. Never the manifest."""
        return {
            "outcome": self.outcome,
            "reason": self.reason.value,
            "expected_key": self.expected_key,
            "compared_key": self.compared_key,
            "differing_fields": list(self.differing_fields),
            "identity_cause": (None if self.identity_cause is None
                               else self.identity_cause.value),
        }


Q50_ALIAS_STATUSES = frozenset({
    "not_requested", "created", "already_present", "not_applicable"})


def _valid_identity_key(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 32
        and all(char in "0123456789abcdef" for char in value))


def valid_day_library_diagnostic(item: Any) -> bool:
    """Return whether one persisted lookup plus build action is complete.

    This schema is shared by the builder and monthly archive consumer so the
    producer cannot call a partial decision complete while the consumer later
    guesses the missing counters.
    """
    if not isinstance(item, Mapping):
        return False
    outcome = item.get("outcome")
    reason = item.get("reason")
    expected_key = item.get("expected_key")
    compared_key = item.get("compared_key")
    differing_fields = item.get("differing_fields")
    identity_cause = item.get("identity_cause")
    duration = item.get("lookup_duration_s")
    calibrated = item.get("full_calibration")
    alias_status = item.get("q50_alias_status")
    if (
        outcome not in {"hit", "miss", "rejected"}
        or reason not in {member.value for member in LookupReason}
        or not _valid_identity_key(expected_key)
        or (compared_key is not None and not _valid_identity_key(compared_key))
        or not isinstance(differing_fields, list)
        or any(not isinstance(field, str) or not field
               for field in differing_fields)
        or identity_cause not in {
            None, *(member.value for member in IdentityCause)}
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration < 0
        or type(calibrated) is not bool
        or calibrated != (outcome != "hit")
        or alias_status not in Q50_ALIAS_STATUSES
        or (outcome == "hit" and alias_status != "not_requested")
        or (outcome != "hit" and alias_status == "not_requested")
    ):
        return False
    classified = classify_identity_difference(differing_fields)
    classified_value = None if classified is None else classified.value
    if identity_cause != classified_value or (
            (compared_key is None) != (not differing_fields)):
        return False
    if outcome == "hit":
        return (reason == LookupReason.HIT.value and compared_key is None
                and not differing_fields and identity_cause is None)
    if outcome == "miss":
        return reason == LookupReason.ENTRY_ABSENT.value
    return (reason not in {
        LookupReason.HIT.value, LookupReason.ENTRY_ABSENT.value}
        and compared_key is None and not differing_fields
        and identity_cause is None)


class DayLibrary:
    """Filesystem store of calibrated day artifacts, keyed by DayIdentity."""

    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, identity: DayIdentity) -> Path:
        # Date first so a horizon is browsable and prunable by eye.
        return self.root / identity.date / identity.key

    def manifest_path(self, identity: DayIdentity) -> Path:
        return self.path_for(identity) / "manifest.json"

    def get(self, identity: DayIdentity) -> dict[str, Any] | None:
        """Return a verified entry, or None if absent, incomplete or altered.

        Unchanged for every existing caller: the verification and the
        fail-closed answer both live in ``lookup`` now, and this is its
        manifest. Nothing may be returned that ``lookup`` did not call a hit.
        """
        return self.lookup(identity).manifest

    def _miss(self, identity: DayIdentity, reason: LookupReason) -> DayLookup:
        """An absent entry, with whatever the same date can say about why."""
        compared_key, fields = self._nearest_sibling(identity)
        return DayLookup(manifest=None, outcome="miss", reason=reason,
                         expected_key=identity.key, compared_key=compared_key,
                         differing_fields=fields,
                         identity_cause=classify_identity_difference(fields))

    @staticmethod
    def _rejected(identity: DayIdentity, reason: LookupReason) -> DayLookup:
        """An entry that exists at the expected key but cannot be trusted."""
        return DayLookup(manifest=None, outcome="rejected", reason=reason,
                         expected_key=identity.key)

    def _nearest_sibling(
            self, identity: DayIdentity) -> tuple[str | None, tuple[str, ...]]:
        """The stored identity for this DATE that is closest to the one asked.

        Evidence, never a substitute: this explains why a date already in the
        store was calibrated again, and is deliberately not returned as a
        manifest. Nearest is fewest differing leaf paths, with the key as a
        deterministic tie-breaker so two runs report the same sibling.
        """
        date_directory = self.root / identity.date
        wanted = identity.to_dict()
        best: tuple[int, str, tuple[str, ...]] | None = None
        try:
            candidates = sorted(p for p in date_directory.iterdir()
                                if p.is_dir())
        except OSError:
            return None, ()
        for entry in candidates:
            if (
                entry.name == identity.key
                or entry.name.endswith(".staging")
                or entry.name.endswith(REPLACED_SUFFIX)
            ):
                continue
            try:
                manifest = json.loads(
                    (entry / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(manifest, dict):
                continue
            other = manifest.get("identity")
            if not isinstance(other, dict):
                continue
            fields = diff_identity(wanted, other)
            ranked = (len(fields), entry.name, fields)
            if best is None or ranked[:2] < best[:2]:
                best = ranked
        if best is None:
            return None, ()
        return best[1], best[2]

    def _verify(self, identity: DayIdentity, directory: Path) -> DayLookup:
        """Verify ``identity`` against one explicit on-disk directory."""
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return self._miss(identity, LookupReason.ENTRY_ABSENT)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._rejected(identity, LookupReason.MANIFEST_UNREADABLE)
        if not isinstance(manifest, dict):
            return self._rejected(identity, LookupReason.MANIFEST_UNREADABLE)
        for predicate, reason in (
            (manifest.get("schema_version") != SCHEMA_VERSION,
             LookupReason.SCHEMA_MISMATCH),
            (manifest.get("kind") != "calibrated_demand_day",
             LookupReason.KIND_MISMATCH),
            (manifest.get("key") != identity.key,
             LookupReason.KEY_MISMATCH),
            (manifest.get("identity") != identity.to_dict(),
             LookupReason.IDENTITY_MISMATCH),
            (not isinstance(manifest.get("artifacts"), Mapping),
             LookupReason.ARTIFACT_RECORD_INVALID),
        ):
            if predicate:
                return self._rejected(identity, reason)
        for name, record in manifest["artifacts"].items():
            if not isinstance(record, Mapping):
                return self._rejected(
                    identity, LookupReason.ARTIFACT_RECORD_INVALID)
            expected_digest = record.get("sha256")
            expected_size = record.get("bytes")
            if (
                not isinstance(expected_digest, str)
                or len(expected_digest) != 64
                or any(char not in "0123456789abcdef"
                       for char in expected_digest)
                or type(expected_size) is not int
                or expected_size < 0
            ):
                return self._rejected(
                    identity, LookupReason.ARTIFACT_RECORD_INVALID)
            path = directory / name
            if not path.is_file():
                return self._rejected(identity, LookupReason.ARTIFACT_MISSING)
            try:
                digest = sha256_bytes(path)
                size = path.stat().st_size
            except OSError:
                return self._rejected(identity,
                                      LookupReason.ARTIFACT_IO_ERROR)
            if digest != expected_digest:
                return self._rejected(
                    identity, LookupReason.ARTIFACT_DIGEST_MISMATCH)
            if size != expected_size:
                return self._rejected(
                    identity, LookupReason.ARTIFACT_SIZE_MISMATCH)
        return DayLookup(manifest=manifest, outcome="hit",
                         reason=LookupReason.HIT, expected_key=identity.key)

    def _reconcile_replaced(self, identity: DayIdentity) -> None:
        """Recover a complete entry left between the two atomic swap steps."""
        directory = self.path_for(identity)
        replaced = directory.with_name(directory.name + REPLACED_SUFFIX)
        if not replaced.is_dir():
            return
        if directory.is_dir():
            if self._verify(identity, directory).manifest is not None:
                shutil.rmtree(replaced, ignore_errors=True)
            return
        if self._verify(identity, replaced).manifest is None:
            return
        try:
            os.replace(replaced, directory)
        except OSError:
            # Another writer may have completed the same entry after the
            # checks above. Leave both paths untouched unless the live entry
            # is now independently verifiable.
            if directory.is_dir() \
                    and self._verify(identity, directory).manifest is not None:
                shutil.rmtree(replaced, ignore_errors=True)

    def lookup(self, identity: DayIdentity) -> DayLookup:
        """Verify one stored day and say exactly what was found.

        Verification is unchanged and still fails closed; what is new is that
        each way of failing has its own name, so a build can report why a day
        was rebuilt instead of only that it was.
        """
        self._reconcile_replaced(identity)
        return self._verify(identity, self.path_for(identity))

    def put(
        self,
        identity: DayIdentity,
        artifacts: Mapping[str, Path],
        *,
        fit: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store one calibrated day. Published atomically, last file first.

        The manifest is written only after every artifact is in place, so a
        crash mid-write leaves an entry that ``get`` rejects rather than a
        half-entry it would accept.
        """
        directory = self.path_for(identity)
        staging = directory.with_name(directory.name + ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        self._reconcile_replaced(identity)
        staging.mkdir(parents=True)
        self._sweep_abandoned_staging(directory.parent, keep=staging)
        records: dict[str, Any] = {}
        for name, path in sorted(artifacts.items()):
            if "/" in name or name in {".", ".."}:
                raise ValueError(f"invalid artifact name: {name!r}")
            if name.endswith(COMPRESSED_SUFFIXES):
                target = staging / (name + ".gz")
                with open(path, "rb") as source, gzip.open(
                        target, "wb", compresslevel=6) as sink:
                    shutil.copyfileobj(source, sink)
            else:
                target = staging / name
                shutil.copyfile(path, target)
            records[target.name] = {
                "sha256": sha256_bytes(target),
                "bytes": target.stat().st_size,
            }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "calibrated_demand_day",
            "key": identity.key,
            "identity": identity.to_dict(),
            "artifacts": records,
            "fit": dict(fit or {}),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        directory.parent.mkdir(parents=True, exist_ok=True)
        # Swap, don't delete-then-move: a reader that has just verified this
        # entry would otherwise find its files gone half-way through
        # assembling a window. Renaming the old copy aside keeps exactly one
        # complete entry visible at the path at every instant.
        replaced = directory.with_name(directory.name + ".replaced")
        shutil.rmtree(replaced, ignore_errors=True)
        if directory.exists():
            os.replace(directory, replaced)
        try:
            os.replace(staging, directory)
        except OSError:
            if replaced.exists() and not directory.exists():
                os.replace(replaced, directory)      # put the old one back
            raise
        shutil.rmtree(replaced, ignore_errors=True)
        return manifest

    # A day is stored in minutes; anything abandoned for this long belongs to
    # a build that was killed, not to one still running. Deliberately not
    # "any staging directory": two builders may legitimately be storing
    # different days under the same date at the same time.
    ABANDONED_STAGING_AGE_S = 6 * 3600

    def _sweep_abandoned_staging(self, date_directory: Path, *,
                                 keep: Path) -> None:
        """Remove staging directories left behind by killed builds.

        Nothing else ever cleans them: a kill between mkdir and the atomic
        rename leaves a directory no reader looks at and no writer revisits
        unless that exact day is rebuilt. Over a horizon-long warming run
        they simply accumulate.
        """
        cutoff = time.time() - self.ABANDONED_STAGING_AGE_S
        for candidate in Path(date_directory).glob("*.staging"):
            if candidate == keep:
                continue
            try:
                if candidate.stat().st_mtime < cutoff:
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                continue


def _shift_route_line(line: str, offset_s: float, vehicle_id: int) -> str:
    """Rewrite one day-local vehicle line into its window position.

    Only the departure time and the vehicle id move. Route edges, departPos
    and arrivalPos are the writer's output and are copied through untouched.
    """
    match = VEHICLE_LINE.match(line)
    if match is None:
        raise ValueError(f"unrecognised vehicle line: {line.rstrip()}")
    depart = float(match.group("depart")) + offset_s
    return (f"{match.group('head')}pfe{vehicle_id}{match.group('mid')}"
            f"{depart:.1f}{match.group('tail')}\n")


def assemble_window(
    days: Iterable[Path],
    route_out: Path,
    agents_out: Path,
    names: tuple[str, str] = ("calibrated.rou.xml", "calibrated.agents.json"),
) -> dict[str, Any]:
    """Concatenate day-local route/agent files into one window.

    ``days`` are the stored day directories in calendar order. Day *d* is
    shifted by ``d * 86400`` seconds and vehicles are renumbered sequentially,
    which is precisely what a monolithic writer produces for the same days —
    it emits quarters in order and numbers vehicles as it goes.
    """
    day_paths = [Path(day) for day in days]
    vehicle_id = 0
    agents: list[dict[str, Any]] = []
    route_tmp = Path(route_out).with_name(Path(route_out).name + ".tmp")
    route_tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(route_tmp, "w") as out:
        out.write("<routes>\n")
        for day_index, day in enumerate(day_paths):
            offset_s = day_index * DAY_SECONDS
            with _open_day_text(day, names[0]) as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped in {"<routes>", "</routes>"}:
                        continue
                    out.write(_shift_route_line(line, offset_s, vehicle_id))
                    vehicle_id += 1
            with _open_day_text(day, names[1]) as handle:
                day_agents = json.load(handle)["agents"]
            for agent in day_agents:
                shifted = dict(agent)
                shifted["vehicle_id"] = f"pfe{len(agents)}"
                shifted["departure_s"] = round(
                    float(agent["departure_s"]) + offset_s, 1)
                agents.append(shifted)
        out.write("</routes>\n")
    if len(agents) != vehicle_id:
        raise ValueError(
            f"assembled {vehicle_id} vehicles but {len(agents)} agents; "
            "a day's route and provenance files disagree")
    os.replace(route_tmp, route_out)

    agents_tmp = Path(agents_out).with_name(Path(agents_out).name + ".tmp")
    with open(agents_tmp, "w") as out:
        json.dump({"schema_version": 1, "agents": agents}, out,
                  separators=(",", ":"))
    os.replace(agents_tmp, agents_out)
    return {"vehicles": vehicle_id, "days": len(day_paths)}


def merge_day_reports(
    day_reports: list[Mapping[str, Any]],
    *,
    quarters_per_day: int = 96,
) -> dict[str, Any]:
    """Combine per-day fit reports into the window report the writer returns.

    Every field a calibration report carries is either a per-quarter record or
    a sum over quarters, so merging is exact rather than approximate: quarter
    indices shift by the day's offset, counts add, edge sets union. The one
    pool-level field (protected signature coverage) is a property of the
    shared candidate pool and must therefore already agree across the days.
    """
    if not day_reports:
        raise ValueError("a window needs at least one day report")
    passage_days = [report.get("passage_calibration") for report in day_reports]
    if any(passage_days) and not all(
            isinstance(record, Mapping) and record.get("status") == "validated"
            for record in passage_days):
        raise ValueError("cannot merge incomplete or mixed passage calibration evidence")
    passage_policies = {
        record.get("policy") for record in passage_days
        if isinstance(record, Mapping)
    }
    if passage_policies and (
            None in passage_policies or len(passage_policies) != 1):
        raise ValueError("cannot merge mixed passage calibration policy versions")
    days = len(day_reports)
    total_quarters = days * quarters_per_day
    achieved: dict[str, list[float]] = {}
    violations: list[dict] = []
    relaxed: list[dict] = []
    allocation: list[dict] = []
    unserviceable: set[str] = set()
    summary_counters = (
        "quarters_with_incompatible_routes", "quarters_with_relaxed_mix",
        "mix_reallocation_vehicles", "replaced_routes",
    )
    summary_maps = (
        "incompatible_routes_by_purpose", "mix_shortfall_by_purpose",
        "mix_excess_by_purpose",
    )
    merged_summary: dict[str, Any] = {name: 0 for name in summary_counters}
    merged_maps: dict[str, Counter] = {name: Counter() for name in summary_maps}
    relaxation: Counter = Counter()
    vehicles = infeasible = geh_ok = geh_total = 0
    integer_constraints = integer_exact = 0
    integer_sum_abs_error = integer_max_abs_error = 0.0
    coverage_by_day: list[Any] = []

    for day_index, report in enumerate(day_reports):
        offset = day_index * quarters_per_day
        vehicles += int(report.get("vehicles", 0))
        infeasible += int(report.get("infeasible_intervals", 0))
        geh_ok += int(report.get("geh_ok", 0))
        geh_total += int(report.get("geh_total", 0))
        integer_constraints += int(
            report.get("integer_sensor_constraints", 0) or 0)
        integer_exact += int(report.get("integer_sensor_exact", 0) or 0)
        integer_sum_abs_error += float(
            report.get("integer_sensor_sum_abs_error", 0.0) or 0.0)
        integer_max_abs_error = max(
            integer_max_abs_error,
            float(report.get("integer_sensor_max_abs_error", 0.0) or 0.0),
        )
        unserviceable.update(report.get("unserviceable_edges", ()))
        relaxation.update(report.get("relaxation_summary", {}))
        for edge, values in (report.get("achieved") or {}).items():
            if len(values) != quarters_per_day:
                raise ValueError(
                    f"day {day_index} reports {len(values)} quarters for "
                    f"{edge}, expected {quarters_per_day}")
            row = achieved.setdefault(edge, [0.0] * total_quarters)
            row[offset:offset + quarters_per_day] = [float(v) for v in values]
        for source, target in ((report.get("bound_violations", ()), violations),
                               (report.get("relaxed_bound_violations", ()),
                                relaxed),
                               (report.get("purpose_allocation", ()),
                                allocation)):
            for entry in source:
                shifted = dict(entry)
                if "quarter" in shifted:
                    shifted["quarter"] = int(shifted["quarter"]) + offset
                target.append(shifted)
        day_summary = report.get("purpose_allocation_summary") or {}
        for name in summary_counters:
            merged_summary[name] += int(day_summary.get(name, 0) or 0)
        for name in summary_maps:
            merged_maps[name].update(day_summary.get(name, {}) or {})
        coverage_by_day.append(
            day_summary.get("protected_signature_coverage"))

    for name in summary_maps:
        merged_summary[name] = dict(sorted(merged_maps[name].items()))
    present = [value for value in coverage_by_day if isinstance(value, Mapping)]
    if present:
        missing: Counter = Counter()
        for value in present:
            for purpose, count in (value.get("missing_by_purpose") or {}).items():
                missing[purpose] = max(missing[purpose], int(count))
        merged_summary["protected_signature_coverage"] = {
            "signatures": min(int(value.get("signatures", 0))
                              for value in present),
            "missing_by_purpose": dict(sorted(missing.items())),
        }
        if len(present) > 1:
            merged_summary["protected_signature_coverage_by_day"] = present
    return {
        "vehicles": vehicles,
        "infeasible_intervals": infeasible,
        "geh_ok": geh_ok,
        "geh_total": geh_total,
        "geh_pct": round(100 * geh_ok / max(1, geh_total), 1),
        "integer_sensor_constraints": integer_constraints,
        "integer_sensor_exact": integer_exact,
        "integer_sensor_exact_pct": round(
            100 * integer_exact / max(1, integer_constraints), 6),
        "integer_sensor_max_abs_error": round(integer_max_abs_error, 9),
        "integer_sensor_sum_abs_error": round(integer_sum_abs_error, 9),
        "integer_sensor_target_rule": "int(round(target))",
        "achieved": achieved,
        "unserviceable_edges": sorted(unserviceable),
        "bound_violations": violations,
        "relaxed_bound_violations": relaxed,
        "purpose_allocation": allocation,
        "purpose_allocation_summary": merged_summary,
        "relaxation_summary": dict(relaxation),
        **({"passage_calibration": {
            "policy": next(iter(passage_policies)), "status": "validated",
            "days": [report["passage_calibration"] for report in day_reports]},
            "measurement_basis": "predicted_sensor_passage_quarter"}
           if all(report.get("passage_calibration", {}).get("status") == "validated"
                  for report in day_reports) else {}),
    }
