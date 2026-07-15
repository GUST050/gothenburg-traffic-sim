"""Compatibility import for :mod:`traffic_sim.core.fingerprint`."""

import sys as _sys
from traffic_sim.core import fingerprint as _implementation

_sys.modules[__name__] = _implementation
