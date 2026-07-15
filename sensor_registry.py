"""Compatibility import for :mod:`traffic_sim.intake.sensors`."""

import sys as _sys
from traffic_sim.intake import sensors as _implementation

_sys.modules[__name__] = _implementation
