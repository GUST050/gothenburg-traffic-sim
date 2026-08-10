"""Outcome-blind held-out selection inputs (LUNA-V6-01).

This module resolves the IMMUTABLE archived demand a held-out campaign must be
selected against. It reads no campaign outcome, report or run root, runs no
process, and never consults a simulation result.

WHY THE RESOLVER IS STRICT.  A `demand_build_key` is supposed to identify the
demand inputs. If several archives claim the same key but differ in ANY
required immutable file — routes, demand metadata or run manifest — then the
key does not determine the inputs, and any exposure computed from "the"
archive would silently depend on which copy was picked: unreproducible, and
unfalsifiable after the fact. That is refused rather than resolved by
preference, and identical routes are not enough to call two archives the same
build.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

ROUTE_FILES = ("calibrated.rou.xml", "calibrated_v1.rou.xml",
               "calibrated_v2.rou.xml")
VARIANT_OF = {"calibrated.rou.xml": "q50",
              "calibrated_v1.rou.xml": "q10",
              "calibrated_v2.rou.xml": "q90"}
REQUIRED_FILES = ROUTE_FILES + ("demand_meta.json", "manifest.json")


class DemandArchiveError(Exception):
    """Raised when the exact demand build cannot be resolved unambiguously."""


@dataclass(frozen=True)
class ResolvedArchive:
    """One unambiguously resolved archived demand build."""
    path: Path
    demand_build_key: str
    build_id: str
    epoch_sim: str
    n_intervals: int
    route_digests: dict          # filename -> sha256
    meta_digest: str
    input_digests: dict          # EVERY required immutable file -> sha256
    duplicates: tuple            # byte-identical siblings, for the record


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("demand-*") if p.is_dir())


def _claims_key(directory: Path, build_key: str) -> dict | None:
    """Return the archive's demand_meta if it claims `build_key`, else None."""
    meta_path = directory / "demand_meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or str(meta.get("demand_build_key")) != build_key:
        return None
    return meta


def _is_successful(directory: Path) -> bool:
    """A build counts only on AFFIRMATIVE recorded success.

    An absent status is not success: a legacy or truncated manifest carries no
    success provenance, and treating it as successful would admit an archive
    nothing ever vouched for.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict) or manifest.get("kind") != "demand":
        return False
    return manifest.get("status") == "succeeded"


def resolve_demand_archive(build_key: str, root: Path = Path("runs"),
                           *, required_intervals: int | None = None
                           ) -> ResolvedArchive:
    """Resolve EXACTLY one archived demand build, or fail closed.

    Rules, in order:
      1. Only successful archives whose `demand_build_key` equals `build_key`
         and that carry all required files are candidates.
      2. Candidates are grouped by the SHA-256 of EVERY required immutable
         file — the three calibrated routes, the demand metadata and the run
         manifest. More than one distinct group means the key does not
         determine the demand: refuse. Route-identical archives that differ in
         metadata or manifest identity are NOT duplicates.
      3. Only genuinely byte-identical duplicates are resolved, by one
         documented rule — the lexicographically smallest directory name — so
         resolution is deterministic and independent of filesystem order.
    """
    root = Path(root)
    candidates = []
    for directory in _candidate_dirs(root):
        meta = _claims_key(directory, build_key)
        if meta is None or not _is_successful(directory):
            continue
        if any(not (directory / name).is_file() for name in REQUIRED_FILES):
            continue
        candidates.append((directory, meta))

    if not candidates:
        raise DemandArchiveError(
            f"no successful archived demand build claims key {build_key!r} "
            f"with all of {', '.join(REQUIRED_FILES)}")

    # Duplicate identity is the digest of EVERY required immutable file, not
    # the routes alone. Two archives with identical routes but different
    # metadata/manifest identity are NOT byte-identical duplicates, and
    # silently tie-breaking between them would hide a real divergence.
    groups: dict[tuple, list] = {}
    for directory, meta in candidates:
        digests = tuple(_sha256(directory / name) for name in REQUIRED_FILES)
        groups.setdefault(digests, []).append((directory, meta))

    if len(groups) > 1:
        detail = "; ".join(
            f"{[d.name for d, _ in members]} -> routes "
            f"{'/'.join(x[:12] for x in key[:len(ROUTE_FILES)])}"
            f" meta/manifest {'/'.join(x[:12] for x in key[len(ROUTE_FILES):])}"
            for key, members in sorted(groups.items()))
        raise DemandArchiveError(
            f"demand key {build_key!r} is AMBIGUOUS: {len(groups)} distinct "
            f"input contents across {len(candidates)} successful archives "
            f"({detail}). The key does not determine the demand inputs, so no "
            f"exposure computed from it would be reproducible.")

    (digests, members), = groups.items()
    members.sort(key=lambda item: item[0].name)      # documented tie-break
    directory, meta = members[0]
    input_digests = dict(zip(REQUIRED_FILES, digests))

    n_intervals = meta.get("n_intervals")
    if required_intervals is not None and n_intervals != required_intervals:
        raise DemandArchiveError(
            f"archive {directory.name} covers {n_intervals} intervals, "
            f"required {required_intervals}")

    return ResolvedArchive(
        path=directory,
        demand_build_key=build_key,
        build_id=str(meta.get("build_id")),
        epoch_sim=str(meta.get("epoch_sim")),
        n_intervals=int(n_intervals) if n_intervals is not None else -1,
        route_digests={name: input_digests[name] for name in ROUTE_FILES},
        meta_digest=input_digests["demand_meta.json"],
        input_digests=input_digests,
        duplicates=tuple(d.name for d, _ in members[1:]),
    )


# ── Exact-path canonical binding (LUNA-V6-02) ────────────────────────────────
# Sol designated ONE archive as canonical for v6. Binding is by exact path and
# full identity: this mode never globs, never discovers a sibling, and never
# repairs the globally ambiguous demand key.

VEHICLE_RE = re.compile(r'<vehicle\b[^>]*\bdepart="([0-9.eE+-]+)"')
EDGES_RE = re.compile(r'<route\b[^>]*\bedges="([^"]*)"')
ALLOWED_TAGS = {"routes", "vehicle", "route"}


class CanonicalBindingError(Exception):
    """Raised when the exact canonical archive does not match its identity."""


@dataclass(frozen=True)
class CanonicalArchive:
    path: Path
    demand_build_key: str
    build_id: str
    epoch_sim: str
    n_intervals: int
    git_commit: str
    file_digests: dict


def bind_canonical_archive(path, identity) -> CanonicalArchive:
    """Bind ONE archive by exact path and full declared identity.

    `identity` must supply demand_build_key, build_id, epoch_sim, n_intervals,
    git_commit, git_dirty and a digest for each of REQUIRED_FILES. Every field
    is checked BEFORE any route byte is parsed. No sibling is read or listed.
    """
    directory = Path(path)
    if directory.is_symlink() or not directory.is_dir():
        raise CanonicalBindingError(f"canonical archive is not a real directory: {directory}")
    resolved_root = directory.resolve()

    digests = {}
    for name in REQUIRED_FILES:
        target = directory / name
        if target.is_symlink() or not target.is_file():
            raise CanonicalBindingError(f"missing or non-regular canonical file: {name}")
        if target.resolve().parent != resolved_root:
            raise CanonicalBindingError(f"canonical file escapes the archive: {name}")
        want = identity["file_digests"].get(name)
        got = _sha256(target)
        if want != got:
            raise CanonicalBindingError(
                f"canonical digest mismatch for {name}: expected {want}, got {got}")
        digests[name] = got

    meta = json.loads((directory / "demand_meta.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    expectations = (
        ("demand_build_key", str(meta.get("demand_build_key")), identity["demand_build_key"]),
        ("build_id", str(meta.get("build_id")), identity["build_id"]),
        ("epoch_sim", str(meta.get("epoch_sim")), identity["epoch_sim"]),
        ("n_intervals", meta.get("n_intervals"), identity["n_intervals"]),
        ("kind", manifest.get("kind"), "demand"),
        ("status", manifest.get("status"), "succeeded"),
        ("git_commit", manifest.get("git_commit"), identity["git_commit"]),
    )
    for field, got, want in expectations:
        if got != want:
            raise CanonicalBindingError(
                f"canonical identity mismatch on {field}: expected {want!r}, got {got!r}")

    # A clean tree must be asserted AFFIRMATIVELY. `bool(meta.get(...))` would
    # read an absent field as clean, admitting an archive that never recorded
    # its tree state at all — exactly the provenance the binding exists to
    # prove. Identity comparison also rejects 0/"" masquerading as False.
    dirty = manifest.get("git_dirty")
    if dirty is not False:
        raise CanonicalBindingError(
            "canonical identity mismatch on git_dirty: a clean build tree must "
            f"be recorded affirmatively as false, got {dirty!r}")

    return CanonicalArchive(
        path=directory, demand_build_key=identity["demand_build_key"],
        build_id=identity["build_id"], epoch_sim=identity["epoch_sim"],
        n_intervals=int(identity["n_intervals"]),
        git_commit=identity["git_commit"], file_digests=digests)


def stream_edge_departures(route_path, candidates):
    """Stream one route file; return {edge: [depart seconds…]} for `candidates`.

    Process-free and single-pass. Refuses an unsupported construct rather than
    silently skipping it, so an unparsed vehicle can never look like zero
    exposure.
    """
    wanted = set(candidates)
    found: dict[str, list[float]] = {}
    seen_vehicles = 0
    with open(route_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith(("<?xml", "<routes", "</routes", "<!--")):
                continue
            if not line.startswith("<vehicle"):
                tag = line[1:].split(">")[0].split()[0].lstrip("/")
                if tag not in ALLOWED_TAGS:
                    raise CanonicalBindingError(
                        f"unsupported route construct {tag!r} in {Path(route_path).name}")
                continue
            depart_match = VEHICLE_RE.search(line)
            edges_match = EDGES_RE.search(line)
            if depart_match is None or edges_match is None:
                raise CanonicalBindingError(
                    f"unparsable vehicle element in {Path(route_path).name}")
            seen_vehicles += 1
            depart = float(depart_match.group(1))
            for edge in edges_match.group(1).split():
                if edge in wanted:
                    found.setdefault(edge, []).append(depart)
    if not seen_vehicles:
        raise CanonicalBindingError(f"no vehicles parsed from {Path(route_path).name}")
    return {edge: sorted(times) for edge, times in found.items()}


def stream_closure_exposure(route_path, candidates):
    """Per candidate edge, everything a survivability check needs — in one pass.

    Returned per edge:
      ``vehicles``          routes containing the edge at all;
      ``denied_departures`` routes STARTING on it, which a full closure denies
                            outright — access the closure removes, not a
                            statement about the network's topology;
      ``resume_pairs``      ``{(edge before the closure, destination): count}``.

    The pair is deliberately the edge IMMEDIATELY BEFORE the closed one, not
    the route's origin. That is where SUMO's live rerouter re-plans from, and
    `run_scenario.truncate_stranded_vehicles` was fixed in 2026-07-09 review
    for using the origin as a proxy: two vehicles sharing an origin can be on
    different candidate routes, one already committed to a dead branch the
    other avoided. Counting pairs rather than vehicles keeps the reachability
    work proportional to distinct approaches, which is a few dozen even when
    thousands of vehicles cross the edge.

    Parses with the same strict single-pass reader as `stream_edge_departures`,
    including its refusal of unsupported constructs: a vehicle that silently
    failed to parse would look like an edge with nothing to sever.
    """
    wanted = set(candidates)
    found: dict[str, dict] = {}
    seen_vehicles = 0
    with open(route_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith(("<?xml", "<routes", "</routes", "<!--")):
                continue
            if not line.startswith("<vehicle"):
                tag = line[1:].split(">")[0].split()[0].lstrip("/")
                if tag not in ALLOWED_TAGS:
                    raise CanonicalBindingError(
                        f"unsupported route construct {tag!r} in {Path(route_path).name}")
                continue
            edges_match = EDGES_RE.search(line)
            if VEHICLE_RE.search(line) is None or edges_match is None:
                raise CanonicalBindingError(
                    f"unparsable vehicle element in {Path(route_path).name}")
            seen_vehicles += 1
            edges = edges_match.group(1).split()
            if not edges:
                continue
            # FIRST occurrence only. A route may touch the same edge twice;
            # the closure stops it at the earliest one, and counting the later
            # occurrence would price a leg the vehicle never reaches.
            for index, edge in enumerate(edges):
                if edge not in wanted:
                    continue
                record = found.setdefault(
                    edge, {"vehicles": 0, "denied_departures": 0,
                           "resume_pairs": {}})
                record["vehicles"] += 1
                if index == 0:
                    record["denied_departures"] += 1
                else:
                    key = (edges[index - 1], edges[-1])
                    record["resume_pairs"][key] = \
                        record["resume_pairs"].get(key, 0) + 1
                break
    if not seen_vehicles:
        raise CanonicalBindingError(f"no vehicles parsed from {Path(route_path).name}")
    return found


def window_exposure(departs, windows):
    """Vehicles departing inside each [start, end) window, in window order.

    A half-open interval keeps a boundary vehicle in exactly one window.
    """
    counts = []
    for start, end in windows:
        counts.append(sum(1 for t in departs if start <= t < end))
    return counts
