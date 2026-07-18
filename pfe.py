"""Compatibility import for :mod:`traffic_sim.demand.pfe`."""

import sys as _sys
from traffic_sim.demand import pfe as _implementation

_sys.modules[__name__] = _implementation
