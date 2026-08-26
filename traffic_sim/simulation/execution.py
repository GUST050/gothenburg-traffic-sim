"""Typed process boundary for one independent SUMO seed execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SeedRunPlan:
    """All inputs one seed worker may read; no publication authority."""

    seed: int
    route_path: Path
    demand_variant: str | None
    add_paths: tuple[Path, ...]
    duration_s: int
    home: Path
    micro: bool
    stats_file: Path
    summary_file: Path | None
    days: int
    day_boundaries_s: tuple[int, ...]
    edge_file: Path
    closure_edges: tuple[str, ...]
    n_intervals: int
    vehroute_output: Path | None
    vehroute_write_unfinished: bool
    work_dir: Path
    timing: bool = False
    suppress_warnings: bool = True
    time_to_teleport_s: int | None = None
    rerouting_threads: int | None = None
    routing_algorithm: str | None = None

    def __post_init__(self) -> None:
        for name in ("seed", "duration_s", "days", "n_intervals"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SeedRunPlan":
        """Compatibility adapter for focused tests and external callers."""
        return cls(
            seed=raw["seed"],
            route_path=Path(raw["route_path"]),
            demand_variant=raw.get("demand_variant"),
            add_paths=tuple(Path(path) for path in raw["add_paths"]),
            duration_s=raw["duration_s"],
            home=Path(raw["home"]),
            micro=bool(raw["micro"]),
            stats_file=Path(raw["stats_file"]),
            summary_file=(Path(raw["summary_file"])
                          if raw.get("summary_file") is not None else None),
            days=raw.get("days", 1),
            day_boundaries_s=tuple(raw.get("day_boundaries_s", ())),
            edge_file=Path(raw["edge_file"]),
            closure_edges=tuple(raw.get("closure_edges", ())),
            n_intervals=raw["n_intervals"],
            vehroute_output=(Path(raw["vehroute_output"])
                             if raw.get("vehroute_output") is not None else None),
            vehroute_write_unfinished=bool(
                raw.get("vehroute_write_unfinished", False)),
            work_dir=Path(raw["work_dir"]),
            timing=bool(raw.get("timing", False)),
            suppress_warnings=bool(raw.get("suppress_warnings", True)),
            time_to_teleport_s=raw.get("time_to_teleport_s"),
            rerouting_threads=raw.get("rerouting_threads"),
            routing_algorithm=raw.get("routing_algorithm"),
        )
