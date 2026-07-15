"""Lightweight runtime helpers shared by SUMO execution entry points.

Keep this module free of network-building dependencies.  Scenario and demand
commands only need to locate the eclipse-sumo installation; importing the
network builder would otherwise load OSMnx and PyProj before any work starts.
"""

from __future__ import annotations

from pathlib import Path


def sumo_home() -> Path:
    """Locate the eclipse-sumo pip package (binaries in bin/)."""
    import sumo  # pip install eclipse-sumo

    return Path(sumo.__file__).parent
