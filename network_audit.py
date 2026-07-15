"""Compatibility import for :mod:`traffic_sim.simulation.network_audit`."""

import sys as _sys
from traffic_sim.simulation import network_audit as _implementation

_sys.modules[__name__] = _implementation
