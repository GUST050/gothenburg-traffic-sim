"""Compatibility import for :mod:`traffic_sim.demand.pfe_kernel`."""

import sys as _sys
from traffic_sim.demand import pfe_kernel as _implementation

_sys.modules[__name__] = _implementation
