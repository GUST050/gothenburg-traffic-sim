"""Shared integer semantics for optional PFE structure ceilings."""

from __future__ import annotations

import math


def integer_structure_cap(total: float, cap_share: float,
                          minimum: float = 2.0) -> int:
    """Return the integer-feasible ceiling used by solver and audit.

    A fractional ceiling cannot be imposed on integer vehicle counts.  PFE's
    bound convention treats an integer as representing the surrounding
    half-open half-vehicle cell, hence ``floor(limit + 0.5)``.  Keeping this in
    one helper prevents validation from flagging a vector the solver correctly
    considers inside the exact discrete contract.
    """
    if total < 0 or cap_share < 0 or minimum < 0:
        raise ValueError("structure-cap inputs must be non-negative")
    return int(math.floor(max(minimum, cap_share * total) + 0.5))
