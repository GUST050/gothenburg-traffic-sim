"""Compatibility import for :mod:`traffic_sim.core.contracts`."""

import sys as _sys
from traffic_sim.core import contracts as _implementation

_sys.modules[__name__] = _implementation
