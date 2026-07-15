"""Compatibility import for :mod:`traffic_sim.ops.runs`."""

import sys as _sys
from traffic_sim.ops import runs as _implementation

_sys.modules[__name__] = _implementation
