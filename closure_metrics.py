"""Compatibility import for :mod:`traffic_sim.simulation.metrics`."""

import sys as _sys
from traffic_sim.simulation import metrics as _implementation

_sys.modules[__name__] = _implementation
