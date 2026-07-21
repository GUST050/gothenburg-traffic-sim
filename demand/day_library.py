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

import hashlib
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("runs") / "demand-days"
DAY_SECONDS = 86400
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

        Verification is the point: an entry whose bytes no longer match its
        manifest is treated as absent so the caller rebuilds it.
        """
        manifest_path = self.manifest_path(identity)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("kind") != "calibrated_demand_day"
            or manifest.get("key") != identity.key
            or manifest.get("identity") != identity.to_dict()
            or not isinstance(manifest.get("artifacts"), dict)
        ):
            return None
        directory = self.path_for(identity)
        for name, record in manifest["artifacts"].items():
            path = directory / name
            if not path.is_file():
                return None
            try:
                if sha256_bytes(path) != record.get("sha256"):
                    return None
                if path.stat().st_size != record.get("bytes"):
                    return None
            except (OSError, AttributeError):
                return None
        return manifest

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
        staging.mkdir(parents=True)
        records: dict[str, Any] = {}
        for name, path in sorted(artifacts.items()):
            if "/" in name or name in {".", ".."}:
                raise ValueError(f"invalid artifact name: {name!r}")
            target = staging / name
            shutil.copyfile(path, target)
            records[name] = {
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
        shutil.rmtree(directory, ignore_errors=True)
        os.replace(staging, directory)
        return manifest


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
            with open(day / names[0]) as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped in {"<routes>", "</routes>"}:
                        continue
                    out.write(_shift_route_line(line, offset_s, vehicle_id))
                    vehicle_id += 1
            day_agents = json.loads(
                (day / names[1]).read_text(encoding="utf-8")
            )["agents"]
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
    coverage_by_day: list[Any] = []

    for day_index, report in enumerate(day_reports):
        offset = day_index * quarters_per_day
        vehicles += int(report.get("vehicles", 0))
        infeasible += int(report.get("infeasible_intervals", 0))
        geh_ok += int(report.get("geh_ok", 0))
        geh_total += int(report.get("geh_total", 0))
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
        "achieved": achieved,
        "unserviceable_edges": sorted(unserviceable),
        "bound_violations": violations,
        "relaxed_bound_violations": relaxed,
        "purpose_allocation": allocation,
        "purpose_allocation_summary": merged_summary,
        "relaxation_summary": dict(relaxation),
    }
