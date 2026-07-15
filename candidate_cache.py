"""Compatibility import for :mod:`traffic_sim.demand.cache`."""

import sys as _sys
from traffic_sim.demand import cache as _implementation

_sys.modules[__name__] = _implementation
