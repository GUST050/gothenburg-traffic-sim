"""Versioned streaming ledgers for one closure search's enumeration.

PR C of `docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`.

WHY.  The v1 enumerate phase builds every parent `ClosureSchedule` into one
tuple, decomposes them into daily units, and stores on EVERY unit the list of
parents that reference it. Measured on the frozen PR A baseline, the six-month
360-hour case costs 489.9 MiB before any candidate is looked at, and the
720-hour case 175.5 MiB. Neither number is about the problem; both are about
holding the whole object graph at once.

WHAT REPLACES IT.  Three NDJSON ledgers written in one streaming pass:

    parents.ndjson       one canonical row per parent schedule
    units.ndjson         one canonical row per UNIQUE daily unit
    parent_units.ndjson  one row per parent: its ordered daily-unit IDs

The reverse unit -> parents graph is gone. It was only ever the inverse of
`parent_units.ndjson`, and materialising it cost memory proportional to the
product of parents and days rather than to either. The only index kept in
memory while writing is a set of unit IDs already emitted, which is the
smallest thing that can answer "have I written this unit yet".

PUBLICATION IS ATOMIC AND ORDERED.  Every ledger is written to a temporary
file in the SAME directory, flushed, fsynced, then `os.replace`d into place;
the directory itself is fsynced so the rename survives a crash. The manifest
is published LAST, so its presence is the signal that every ledger before it
is complete. An interrupted run therefore leaves ledgers without a manifest —
which reads as "not built", not as "built and broken". A REWRITE withdraws the
previous manifest before it starts, so the same is true there: while a build is
in progress the directory claims nothing at all.

THE TWO FAILURE MODES ARE DELIBERATELY DIFFERENT.

  * NO MANIFEST -> `LedgerIncomplete`. Nothing was ever declared finished, so
    rebuilding is safe and correct.
  * MANIFEST PRESENT BUT A LEDGER DOES NOT MATCH IT -> `LedgerCorrupt`, and
    the caller must stop. A completed ledger that no longer matches its own
    digest is not a rebuildable scratch file; silently regenerating it would
    destroy the evidence that something damaged a frozen artifact.

Reading is likewise streaming. `ParentLedgerIndex` keeps byte offsets rather
than objects, so a caller can fetch any parent by ID without holding them all:
about 100 bytes per parent instead of a full schedule with its intervals.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from traffic_sim.core.contracts import ClosureSchedule, ClosureSearchSpec

#: Versioned contract name. Any change to a row's shape or to the publication
#: protocol must bump this; a workspace records the version it was built with.
LEDGER_SCHEMA = "closure_search_ledgers_v1"
LEDGER_VERSION = 1

PARENTS_LEDGER = "parents.ndjson"
UNITS_LEDGER = "units.ndjson"
PARENT_UNITS_LEDGER = "parent_units.ndjson"
MANIFEST_NAME = "ledgers.manifest.json"

LEDGER_NAMES = (PARENTS_LEDGER, UNITS_LEDGER, PARENT_UNITS_LEDGER)

#: Suffix for in-progress files. Named so a human reading the directory can
#: tell instantly which files are not yet evidence.
TEMP_SUFFIX = ".partial"


class LedgerError(Exception):
    """Base class for every ledger fault."""


class LedgerIncomplete(LedgerError):
    """No manifest: the ledgers were never declared finished.

    Distinct from corruption on purpose — this state is safely rebuildable.
    """


class LedgerCorrupt(LedgerError):
    """A completed ledger no longer matches the manifest that froze it.

    Never rebuilt automatically. Regenerating would erase the only evidence
    that a frozen artifact was damaged.
    """


def canonical_row(payload: Mapping[str, Any]) -> str:
    """One deterministic NDJSON row.

    ``sort_keys`` and the tight separators make the bytes a function of the
    content alone, so a digest means something and two runs of the same
    enumeration produce identical files.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True) + "\n"


def content_key(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"),
                   allow_nan=False, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class LedgerFile:
    """One published ledger's frozen identity."""

    name: str
    rows: int
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "rows": self.rows, "bytes": self.bytes,
                "sha256": self.sha256}


@dataclass(frozen=True)
class LedgerManifest:
    """The record whose presence declares the ledgers complete."""

    schema: str
    version: int
    search_content_key: str
    provenance: Mapping[str, Any]
    files: Mapping[str, LedgerFile]
    parent_count: int
    unique_unit_count: int
    parent_unit_edge_count: int
    status: str
    key: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": self.schema,
            "version": self.version,
            "search_content_key": self.search_content_key,
            "provenance": dict(self.provenance),
            "files": {name: item.to_dict()
                      for name, item in sorted(self.files.items())},
            "counts": {
                "parent_schedules": self.parent_count,
                "unique_daily_units": self.unique_unit_count,
                "parent_unit_edges": self.parent_unit_edge_count,
            },
            "status": self.status,
        }
        payload["content_key"] = self.key
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LedgerManifest":
        if not isinstance(raw, Mapping):
            raise LedgerCorrupt("ledger manifest must be an object")
        try:
            files = {
                str(name): LedgerFile(
                    name=str(item["name"]), rows=int(item["rows"]),
                    bytes=int(item["bytes"]), sha256=str(item["sha256"]))
                for name, item in (raw.get("files") or {}).items()
            }
            counts = raw.get("counts") or {}
            manifest = cls(
                schema=str(raw.get("schema")),
                version=int(raw.get("version", -1)),
                search_content_key=str(raw.get("search_content_key")),
                provenance=dict(raw.get("provenance") or {}),
                files=files,
                parent_count=int(counts["parent_schedules"]),
                unique_unit_count=int(counts["unique_daily_units"]),
                parent_unit_edge_count=int(counts["parent_unit_edges"]),
                status=str(raw.get("status")),
                key=str(raw.get("content_key")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerCorrupt(f"ledger manifest is malformed: {error}") from error
        if manifest.key != content_key(manifest.to_dict()):
            raise LedgerCorrupt("ledger manifest content key does not match its body")
        return manifest


class _LedgerWriter:
    """Append canonical rows to one temporary file, tracking size and digest.

    Digest and byte count are accumulated AS THE ROWS ARE WRITTEN rather than
    by re-reading the finished file: re-reading would compute the digest of
    whatever is on disk at that moment, which is exactly what a verifier is
    supposed to catch independently.
    """

    def __init__(self, directory: Path, name: str) -> None:
        self.name = name
        self.final = directory / name
        self.temporary = directory / (name + TEMP_SUFFIX)
        self._handle = self.temporary.open("w", encoding="utf-8", newline="")
        self._digest = hashlib.sha256()
        self.rows = 0
        self.bytes = 0

    def write(self, payload: Mapping[str, Any]) -> None:
        row = canonical_row(payload)
        encoded = row.encode("utf-8")
        self._handle.write(row)
        self._digest.update(encoded)
        self.rows += 1
        self.bytes += len(encoded)

    def seal(self) -> LedgerFile:
        """Flush, fsync and atomically publish this ledger."""
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        os.replace(self.temporary, self.final)
        return LedgerFile(name=self.name, rows=self.rows, bytes=self.bytes,
                          sha256=self._digest.hexdigest())

    def abort(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass
        self.temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Make a rename durable. Without this a crash can lose the link itself."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_ledgers(
    directory: Path,
    spec: ClosureSearchSpec,
    schedules: Iterable[ClosureSchedule],
    *,
    unit_records: Callable[
        [ClosureSearchSpec, ClosureSchedule],
        Sequence[tuple[str, Mapping[str, Any], Callable[[], ClosureSchedule]]]],
    provenance: Mapping[str, Any],
) -> LedgerManifest:
    """Stream one enumeration into three ledgers and publish a manifest.

    `schedules` is consumed lazily and never collected: this is the whole
    point. `unit_records` is injected so the identity computation stays in
    `independent_daily`, where the v1 path also uses it — one implementation,
    so a streaming unit ID can never differ from a cached one.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*LEDGER_NAMES, MANIFEST_NAME):
        (directory / (name + TEMP_SUFFIX)).unlink(missing_ok=True)
    # Withdraw any previous completion signal BEFORE writing. While a build is
    # in progress the directory must claim nothing: an interrupted REWRITE then
    # reads as "never finished" and is safely rebuilt, instead of leaving an
    # older manifest describing files this run may already have replaced.
    (directory / MANIFEST_NAME).unlink(missing_ok=True)

    parents = _LedgerWriter(directory, PARENTS_LEDGER)
    units = _LedgerWriter(directory, UNITS_LEDGER)
    relationships = _LedgerWriter(directory, PARENT_UNITS_LEDGER)
    # The ONLY in-memory index during the write. Not the reverse graph: just
    # "which unit IDs have already been emitted", so each unique unit is
    # written exactly once.
    seen_units: set[str] = set()
    edges = 0
    try:
        for parent in schedules:
            if parent.search_content_key != spec.content_key:
                raise LedgerError(
                    "schedule does not belong to this search spec")
            parents.write(parent.to_dict())
            ordered_unit_ids: list[str] = []
            for unit_id, identity, build in unit_records(spec, parent):
                ordered_unit_ids.append(unit_id)
                if unit_id not in seen_units:
                    seen_units.add(unit_id)
                    # `build()` only here: a parent yields one record per
                    # interval, but only a new UNIQUE unit needs a schedule
                    # object, and constructing one is ~8x the cost of its
                    # identity.
                    units.write({
                        "unit_id": unit_id,
                        "identity": dict(identity),
                        "schedule": build().to_dict(),
                    })
            if len(set(ordered_unit_ids)) != len(ordered_unit_ids):
                raise LedgerError(
                    f"parent {parent.schedule_id} repeats a daily unit")
            edges += len(ordered_unit_ids)
            relationships.write({
                "parent_schedule_id": parent.schedule_id,
                "unit_ids": ordered_unit_ids,
            })
        sealed = {}
        for writer in (parents, units, relationships):
            record = writer.seal()
            sealed[record.name] = record
    except BaseException:
        for writer in (parents, units, relationships):
            writer.abort()
        raise
    _fsync_directory(directory)

    manifest = LedgerManifest(
        schema=LEDGER_SCHEMA,
        version=LEDGER_VERSION,
        search_content_key=spec.content_key,
        provenance=dict(provenance),
        files=sealed,
        parent_count=sealed[PARENTS_LEDGER].rows,
        unique_unit_count=sealed[UNITS_LEDGER].rows,
        parent_unit_edge_count=edges,
        status="complete",
        key="",
    )
    body = manifest.to_dict()
    body.pop("content_key", None)
    key = content_key(body)
    manifest = LedgerManifest(**{**manifest.__dict__, "key": key})

    # LAST, and atomically. Its presence is the completion signal, so it must
    # never appear before the ledgers it describes are fully on disk.
    temporary = directory / (MANIFEST_NAME + TEMP_SUFFIX)
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, directory / MANIFEST_NAME)
    _fsync_directory(directory)
    return manifest


def read_manifest(directory: Path) -> LedgerManifest | None:
    """Return the manifest, or None when the ledgers were never completed."""
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LedgerCorrupt(f"ledger manifest is unreadable: {error}") from error
    return LedgerManifest.from_dict(raw)


def verify_ledgers(directory: Path,
                   *, expected_search_content_key: str | None = None
                   ) -> LedgerManifest:
    """Fail closed unless every ledger matches the manifest that froze it.

    Size AND digest AND row count are all checked. Size alone would miss a
    same-length corruption; the digest alone would not localise a truncation;
    the row count is what a caller actually iterates, so a file whose bytes
    match but whose rows do not would silently shorten a search.
    """
    directory = Path(directory)
    manifest = read_manifest(directory)
    if manifest is None:
        raise LedgerIncomplete(
            f"no ledger manifest in {directory}; the enumeration was never "
            f"declared complete")
    if manifest.schema != LEDGER_SCHEMA or manifest.version != LEDGER_VERSION:
        raise LedgerCorrupt(
            f"ledger schema {manifest.schema!r} v{manifest.version} is not "
            f"{LEDGER_SCHEMA!r} v{LEDGER_VERSION}")
    if manifest.status != "complete":
        raise LedgerCorrupt(f"ledger manifest status is {manifest.status!r}")
    if (expected_search_content_key is not None
            and manifest.search_content_key != expected_search_content_key):
        raise LedgerCorrupt("ledger manifest belongs to another search")
    if set(manifest.files) != set(LEDGER_NAMES):
        raise LedgerCorrupt(
            f"ledger manifest must describe exactly {sorted(LEDGER_NAMES)}")

    for name in LEDGER_NAMES:
        record = manifest.files[name]
        path = directory / name
        if not path.is_file():
            raise LedgerCorrupt(f"completed ledger is missing: {name}")
        size = path.stat().st_size
        if size != record.bytes:
            raise LedgerCorrupt(
                f"ledger {name} is {size} bytes, manifest froze {record.bytes}")
        digest = hashlib.sha256()
        rows = 0
        with path.open("rb") as handle:
            for line in handle:
                digest.update(line)
                if line.strip():
                    rows += 1
        if digest.hexdigest() != record.sha256:
            raise LedgerCorrupt(f"ledger {name} digest does not match the manifest")
        if rows != record.rows:
            raise LedgerCorrupt(
                f"ledger {name} has {rows} rows, manifest froze {record.rows}")
    return manifest


def _iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as error:
                raise LedgerCorrupt(
                    f"{path.name} line {number} is not valid JSON: {error}"
                ) from error
            if not isinstance(row, dict):
                raise LedgerCorrupt(
                    f"{path.name} line {number} is not a JSON object")
            yield row


def iter_parent_schedules(directory: Path) -> Iterator[ClosureSchedule]:
    """Stream parents in the exact order the enumeration produced them."""
    for row in _iter_rows(Path(directory) / PARENTS_LEDGER):
        yield ClosureSchedule.from_dict(row)


def iter_unit_rows(directory: Path) -> Iterator[dict[str, Any]]:
    """Stream unique daily units. Rows carry NO parent list, by design."""
    yield from _iter_rows(Path(directory) / UNITS_LEDGER)


def iter_parent_unit_rows(directory: Path) -> Iterator[dict[str, Any]]:
    """Stream each parent's ordered unit IDs — the forward relationship."""
    for row in _iter_rows(Path(directory) / PARENT_UNITS_LEDGER):
        if "parent_schedule_id" not in row or not isinstance(
                row.get("unit_ids"), list):
            raise LedgerCorrupt(
                "parent-unit row must carry parent_schedule_id and unit_ids")
        yield row


class ParentLedgerIndex:
    """Random access to parents by ID, holding offsets instead of objects.

    A search still needs "give me the schedule for candidate X" during pilot
    and finalist phases. Keeping the parents in a dict costs the very memory
    PR C removes, so the index stores a byte offset per ID — roughly a hundred
    bytes each instead of a schedule and all its intervals — and parses one
    row on demand.
    """

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / PARENTS_LEDGER
        self._offsets: dict[str, int] = {}
        offset = 0
        with self.path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    schedule_id = row.get("schedule_id")
                    if not isinstance(schedule_id, str) or not schedule_id:
                        raise LedgerCorrupt(
                            "parent ledger row has no schedule_id")
                    if schedule_id in self._offsets:
                        raise LedgerCorrupt(
                            f"parent ledger repeats {schedule_id}")
                    self._offsets[schedule_id] = offset
                offset += len(line)

    def __len__(self) -> int:
        return len(self._offsets)

    def __contains__(self, schedule_id: object) -> bool:
        return schedule_id in self._offsets

    def ids(self) -> tuple[str, ...]:
        """Schedule IDs in ledger order, which is the enumeration order."""
        return tuple(self._offsets)

    def __getitem__(self, schedule_id: str) -> ClosureSchedule:
        try:
            offset = self._offsets[schedule_id]
        except KeyError:
            raise KeyError(schedule_id) from None
        with self.path.open("rb") as handle:
            handle.seek(offset)
            line = handle.readline()
        return ClosureSchedule.from_dict(json.loads(line))
