"""Compatibility import for :mod:`traffic_sim.simulation.runtime`."""

import sys as _sys
from traffic_sim.simulation import runtime as _implementation

_sys.modules[__name__] = _implementation
