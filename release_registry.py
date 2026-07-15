"""Compatibility import for :mod:`traffic_sim.ops.releases`."""

import sys as _sys
from traffic_sim.ops import releases as _implementation

_sys.modules[__name__] = _implementation
