"""Compatibility import for :mod:`traffic_sim.simulation.metadata`."""

import sys as _sys
from traffic_sim.simulation import metadata as _implementation

_sys.modules[__name__] = _implementation
