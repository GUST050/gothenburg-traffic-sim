"""Exact edge support carried by calibrated SUMO route artifacts.

A closure can only change a scenario when at least one published demand route
uses the closed edge.  Spatial proximity to a sensor is not evidence of that
support, so callers use this module both to fail closed before a closure run and
to suppress confidence for structurally unsupported map edges.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence
import xml.etree.ElementTree as ET


def _artifact_identity(path: Path) -> tuple[str, int, int]:
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return str(resolved), int(stat.st_size), int(stat.st_mtime_ns)


@lru_cache(maxsize=32)
def _route_edges_cached(identity: tuple[str, int, int]) -> frozenset[str]:
    path = Path(identity[0])
    edges: set[str] = set()
    # iterparse releases completed elements as it goes; annual route files can
    # be large and do not need to become a full in-memory XML tree merely to
    # establish their support boundary.
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag == "route":
            edges.update((element.get("edges") or "").split())
        element.clear()
    return frozenset(edges)


def route_edges(path: Path) -> frozenset[str]:
    """Return every edge used by one route artifact, cache-invalidated by stat."""
    return _route_edges_cached(_artifact_identity(Path(path)))


def combined_route_edges(paths: Sequence[Path]) -> frozenset[str]:
    """Return the union of exact route support across demand variants."""
    combined: set[str] = set()
    for path in paths:
        combined.update(route_edges(Path(path)))
    return frozenset(combined)


def unsupported_edges(edges: Iterable[str], paths: Sequence[Path]) -> tuple[str, ...]:
    """Return requested edges absent from every supplied demand variant."""
    supported = combined_route_edges(paths)
    return tuple(sorted({str(edge) for edge in edges} - supported))
