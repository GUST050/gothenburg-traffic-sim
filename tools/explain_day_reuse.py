"""Explain why a calendar date holds more than one calibrated day.

Stage 1 of the item-1 contract in ``IMPROVEMENT_PLAN.md``. A monthly search
records one passage calibration per stored day identity, and the June job
recorded 49 of them for 31 named calendar-day units. ``DayLibrary.get``
answers only "yes" or "None", so nothing in the run says which identity field
separated two otherwise identical days -- the question had to be answered by
reading manifests afterwards, by hand.

This module does that reading once, reproducibly, and is deliberately placed
OUTSIDE ``traffic_sim.demand.source_identity.demand_source_paths``. That
inventory binds 40 files into every ``DayIdentity``; adding a diagnostic to
one of them would make every stored day unreachable, which is a real cost
(measured 2026-09-10: 296 live entries over 94 dates in an 8.6 GB store) to
pay for a question that can be answered without paying it at all.

The tool only reads. It never writes, moves, prunes or re-times anything
under the library root, and a test asserts exactly that.

The approach follows established practice for content-addressed caches:
Bazel's ``--explain``/``--verbose_explanations``, Nextflow's ``-dump-hashes``,
Gradle's task-input comparison and ccache's named outcome counters all answer
a miss by naming the key component that differed, not merely by reporting the
miss.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "day_reuse_explanation_v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = Path("runs") / "demand-days"
DEFAULT_OUTPUT = Path("validation") / "day_reuse_explanation_v1.json"

# Identity fields whose leaves are compared individually. Reporting a bare
# "inputs" would name the container, not the input that actually changed --
# the difference between "something changed" and a usable answer.
EXPANDED_FIELDS = ("inputs", "source_hashes")

# A q50-only entry is stored on purpose beside the three-variant day (see the
# _store_q50_subset docstring in build_sumo_demand.py). These two fields are
# the ONLY ones allowed to differ when linking an alias to its full entry:
# constraints is keyed by variant suffix, so it necessarily follows variants.
ALIAS_IGNORED_FIELDS = frozenset({"inputs.constraints", "inputs.variants"})

CANDIDATE_PREFIXES = ("inputs.candidate", "inputs.catalog_keys")
FULL_VARIANTS = (
    "edge_shares", "edge_shares_q10", "edge_shares_q90",
)
Q50_VARIANTS = ("edge_shares",)


def diff_identity(left: Mapping[str, Any],
                  right: Mapping[str, Any]) -> tuple[str, ...]:
    """Sorted dotted paths on which two stored identities differ."""
    paths: set[str] = set()
    for field in set(left) | set(right):
        a, b = left.get(field), right.get(field)
        if a == b:
            continue
        if field in EXPANDED_FIELDS and isinstance(a, Mapping) \
                and isinstance(b, Mapping):
            for leaf in set(a) | set(b):
                if a.get(leaf) != b.get(leaf):
                    paths.add(f"{field}.{leaf}")
            continue
        paths.add(field)
    return tuple(sorted(paths))


def classify(paths: Sequence[str]) -> str | None:
    """The single cause of a difference, by the contract's precedence.

    Order matters and is the contract's: a changed source byte means the
    older entry could never have been reused whatever else differs, and a
    variant subset is a deliberate alias rather than a repeated solve. Only
    when neither holds may a difference count as possibly avoidable.
    """
    if not paths:
        return None
    if any(p.startswith("source_hashes") for p in paths):
        return "source_change"
    if "inputs.variants" in paths:
        return "variant_subset"
    if "pool_composition" in paths:
        return "pool_composition"
    if any(p.startswith(prefix) for p in paths
           for prefix in CANDIDATE_PREFIXES):
        return "candidate_drift"
    return "other"


@dataclass(frozen=True)
class Entry:
    """One stored day: where it is, when it was written, what it is of."""

    date: str
    key: str
    written_at: float
    identity: dict
    path: str

    @property
    def variants(self) -> list:
        value = self.identity.get("inputs", {}).get("variants", [])
        return list(value) if isinstance(value, list) else []

    @property
    def is_full(self) -> bool:
        """A full calibration is the exact three-arm publication contract."""
        return tuple(self.variants) == FULL_VARIANTS

    @property
    def kind(self) -> str:
        variants = tuple(self.variants)
        if variants == FULL_VARIANTS:
            return "full"
        if variants == Q50_VARIANTS:
            return "alias"
        return "other"


@dataclass(frozen=True)
class LibraryScan:
    entries: tuple[Entry, ...]
    unreadable: tuple[dict, ...]


def parse_bound(text: str) -> float:
    """ISO 8601 to a unix instant; a naive value means local time."""
    return datetime.fromisoformat(text).timestamp()


def load_library(root: Path, *, since: float | None = None,
                 until: float | None = None) -> LibraryScan:
    """Read every manifest under ``root``; damaged ones are reported, not lost.

    Bounds are INCLUSIVE. A job's own start and end second are the natural
    way to name its window, and excluding them silently drops the first or
    last day it wrote.
    """
    entries: list[Entry] = []
    unreadable: list[dict] = []
    root = Path(root)
    if not root.is_dir():
        return LibraryScan(entries=(), unreadable=())
    for date_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for key_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            manifest = key_dir / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                written_at = manifest.stat().st_mtime
            except OSError as error:
                unreadable.append({"path": str(manifest),
                                   "error": type(error).__name__})
                continue
            if since is not None and written_at < since:
                continue
            if until is not None and written_at > until:
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                unreadable.append({"path": str(manifest),
                                   "error": type(error).__name__})
                continue
            if not isinstance(payload, dict):
                unreadable.append({"path": str(manifest),
                                   "error": "InvalidManifest"})
                continue
            if payload.get("schema_version") != 1:
                unreadable.append({"path": str(manifest),
                                   "error": "SchemaVersionMismatch"})
                continue
            identity = payload.get("identity")
            if not isinstance(identity, dict):
                unreadable.append({"path": str(manifest),
                                   "error": "MissingIdentity"})
                continue
            if payload.get("kind") != "calibrated_demand_day":
                unreadable.append({"path": str(manifest),
                                   "error": "KindMismatch"})
                continue
            if payload.get("key") != key_dir.name:
                unreadable.append({"path": str(manifest),
                                   "error": "KeyMismatch"})
                continue
            if not isinstance(payload.get("artifacts"), dict):
                unreadable.append({"path": str(manifest),
                                   "error": "InvalidArtifacts"})
                continue
            if identity.get("schema_version") != 1:
                unreadable.append({"path": str(manifest),
                                   "error": "IdentitySchemaMismatch"})
                continue
            if identity.get("date") != date_dir.name:
                unreadable.append({"path": str(manifest),
                                   "error": "IdentityDateMismatch"})
                continue
            if (
                not isinstance(identity.get("source"), str)
                or not isinstance(identity.get("pool_composition"), list)
                or not isinstance(identity.get("inputs"), dict)
                or not isinstance(identity.get("source_hashes"), dict)
            ):
                unreadable.append({"path": str(manifest),
                                   "error": "InvalidIdentity"})
                continue
            entries.append(Entry(date=date_dir.name, key=key_dir.name,
                                 written_at=written_at, identity=identity,
                                 path=str(manifest)))
    entries.sort(key=lambda entry: (entry.date, entry.key))
    unreadable.sort(key=lambda record: record["path"])
    return LibraryScan(entries=tuple(entries), unreadable=tuple(unreadable))


def _alias_target(alias: Entry, full_entries: Sequence[Entry]) -> str | None:
    """The full entry an alias is a subset of, lowest key wins ties."""
    for candidate in sorted(full_entries, key=lambda entry: entry.key):
        difference = set(diff_identity(alias.identity, candidate.identity))
        if difference <= ALIAS_IGNORED_FIELDS:
            return candidate.key
    return None


def _reusable(entry: Entry, current: Mapping[str, str] | None) -> bool | None:
    if current is None:
        return None
    return entry.identity.get("source_hashes") == dict(current)


def explain(scan: LibraryScan, *,
            current_sources: Mapping[str, str] | None = None,
            root: str | None = None,
            filters: Mapping[str, Any] | None = None) -> dict:
    """Per-date account of repeated calibrations, aliases and reusability.

    Deliberately free of a wall clock: two runs over the same tree produce
    byte-identical reports, which is what makes "did the tool change
    anything?" a checkable question.
    """
    by_date: dict[str, list[Entry]] = {}
    for entry in scan.entries:
        by_date.setdefault(entry.date, []).append(entry)

    causes: dict[str, int] = {}
    dates_report: list[dict] = []
    full_total = alias_total = other_total = unlinked = repeated_dates = repeats = 0
    reusable_entries = reusable_dates = None
    if current_sources is not None:
        reusable_entries = 0
        reusable_dates = 0

    for date in sorted(by_date):
        entries = sorted(by_date[date], key=lambda entry: entry.key)
        full = [entry for entry in entries if entry.kind == "full"]
        aliases = [entry for entry in entries if entry.kind == "alias"]
        other = [entry for entry in entries if entry.kind == "other"]
        full_total += len(full)
        alias_total += len(aliases)
        other_total += len(other)
        if len(full) > 1:
            repeated_dates += 1
            repeats += len(full) - 1

        alias_targets = {alias.key: _alias_target(alias, full)
                         for alias in aliases}
        unlinked += sum(1 for target in alias_targets.values()
                        if target is None)

        comparisons = []
        chronological = sorted(full,
                               key=lambda entry: (entry.written_at, entry.key))
        for index, compared in enumerate(chronological[1:], start=1):
            candidates = []
            for baseline in chronological[:index]:
                candidate_fields = diff_identity(
                    baseline.identity, compared.identity)
                candidates.append((len(candidate_fields),
                                   -baseline.written_at, baseline.key,
                                   baseline, candidate_fields))
            _distance, _recent, _key, baseline, fields = min(candidates)
            cause = classify(fields)
            if cause is not None:
                causes[cause] = causes.get(cause, 0) + 1
            comparisons.append({"baseline_key": baseline.key,
                                "compared_key": compared.key,
                                "cause": cause,
                                "fields": list(fields)})

        entry_records = []
        date_has_reusable = False
        for entry in entries:
            usable = _reusable(entry, current_sources)
            if usable:
                reusable_entries += 1
                date_has_reusable = True
            entry_records.append({
                "key": entry.key,
                "kind": entry.kind,
                "written_at": round(entry.written_at, 3),
                "variants": entry.variants,
                "pool_composition": list(
                    entry.identity.get("pool_composition", [])),
                "alias_of": (alias_targets[entry.key]
                             if entry.kind == "alias" else None),
                "reusable": usable,
            })
        if date_has_reusable:
            reusable_dates += 1

        dates_report.append({"date": date,
                             "full_calibrations": len(full),
                             "q50_aliases": len(aliases),
                             "other_entries": len(other),
                             "entries": entry_records,
                             "comparisons": comparisons})

    summary = {
        "entries": len(scan.entries),
        "dates": len(by_date),
        "full_calibrations": full_total,
        "q50_aliases": alias_total,
        "other_entries": other_total,
        "unlinked_aliases": unlinked,
        "repeated_dates": repeated_dates,
        "repeated_calibrations": repeats,
        "causes": causes,
        "unreadable_manifests": len(scan.unreadable),
        "reusable_entries": reusable_entries,
        "reusable_dates": reusable_dates,
        "stale_entries": (None if reusable_entries is None
                          else len(scan.entries) - reusable_entries),
    }
    return {
        "schema": SCHEMA,
        "release_evidence": False,
        "root": root,
        "filters": dict(filters or {}),
        "summary": summary,
        "unreadable": [dict(record) for record in scan.unreadable],
        "dates": dates_report,
    }


def current_source_inventory(
        root: Path | None = None) -> tuple[dict | None, str | None]:
    """The tree's own demand source hashes, or why they are unavailable.

    Anchored to this file's own repository, never to the process cwd -- the
    same rule ``build_sumo_demand._source_files`` states, and for the same
    reason: a command launched from anywhere must fingerprint one tree. Run
    as a script, ``sys.path[0]`` is ``tools/``, so the import needs the root
    added explicitly; without it the comparison silently degraded to
    "unknown" while the tool still looked like it had run.
    """
    repo_root = Path(root) if root is not None else REPO_ROOT
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from traffic_sim.demand.source_identity import (
            demand_source_fingerprints)
        records = demand_source_fingerprints(repo_root)
        return ({label: record.get("sha256")
                 for label, record in records.items()}, None)
    except Exception as error:  # inventory is diagnostic, never fatal here
        return None, type(error).__name__


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Explain repeated calendar-day calibrations (read-only).")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="day-library root to read")
    parser.add_argument("--since", default=None,
                        help="ISO 8601 lower bound on manifest write time")
    parser.add_argument("--until", default=None,
                        help="ISO 8601 upper bound on manifest write time")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="where to write the JSON report")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        args.output.resolve().relative_to(args.root.resolve())
    except ValueError:
        pass
    else:
        parser.error("--output must be outside the day-library root")

    since = parse_bound(args.since) if args.since else None
    until = parse_bound(args.until) if args.until else None
    scan = load_library(args.root, since=since, until=until)
    current, unavailable = current_source_inventory()
    report = explain(scan, current_sources=current, root=str(args.root),
                     filters={"since": args.since, "until": args.until,
                              "source_inventory_error": unavailable})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(f"{summary['full_calibrations']} full calibration(s) for "
          f"{summary['dates']} calendar date(s); "
          f"{summary['repeated_calibrations']} repeat(s) on "
          f"{summary['repeated_dates']} date(s): {summary['causes']}")
    print(f"{summary['q50_aliases']} q50 alias(es), "
          f"{summary['unlinked_aliases']} unlinked; "
          f"{summary['unreadable_manifests']} unreadable manifest(s)")
    print(f"reusable with current sources: {summary['reusable_entries']} "
          f"entry(ies) over {summary['reusable_dates']} date(s)")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
