"""The ONE owner of the frozen trip-length acceptance threshold.

WHY THIS MODULE EXISTS (2026-08-26).  `traffic_sim/confidence/report.py`
has always evaluated a trip-length gate of the form "L1 against the
declared external target must be at most ``maximum_l1_distance``", and it
fails closed when that limit is absent.  Nothing in the production
pipeline ever wrote it: a repository-wide search found `maximum_l1_distance`
set only inside `tests/test_validation_report.py`.  The consequence was
silent and total — the structure section could never reach ``pass``, so
EVERY demand build reported ``overall: "warn"`` regardless of its data, and
that permanent warning buried the genuine signal it was published beside
(the under-1 km structure-cap flag).

A gate that cannot be evaluated is not a conservative gate; it is no gate,
plus noise.  The threshold is therefore declared here, once, in source, so
that:

  * it cannot be tuned per build — a limit that travels inside the artifact
    it judges is a limit the artifact can choose;
  * `report.py` keeps honouring the design rule that thresholds live in one
    place, by importing this value instead of inlining a number;
  * a producer that wants to hold ITSELF to a stricter limit still can (see
    `effective_maximum_l1`), but no producer can loosen it.

CHOOSING THE VALUE.  The bin shares are a probability distribution over the
three RVU short bins, so the L1 distance has an exact interpretation:

    L1 = 2 x total-variation distance

and the total-variation distance is the share of vehicles that would have
to MOVE to a different length bin for the calibrated population to match
the declared target.  The frozen limit below therefore states a plain,
externally meaningful requirement:

    at most 10% of calibrated vehicles may sit in the wrong RVU length bin
    relative to the availability-corrected behavioural target

which is L1 <= 0.20.  This is a declared project standard, not a fit to any
observed build, and deliberately not derived from the candidate pool: an
acceptance threshold that moves with the pool would let a worse pool excuse
a worse calibration, which is the exact failure the availability-corrected
target was introduced to avoid (see
`build_candidates.availability_corrected_rvu_target`).

WHAT IT MEASURES TODAY, recorded so the number is not mistaken for a rubber
stamp.  On the 2026-08-26 forecast builds the CANDIDATE POOL sits at
L1 ~= 0.026 (TV ~1.3%, comfortably inside), while the CALIBRATED output
sits at L1 = 0.3101 (TV 15.5%, outside).  The gate therefore separates the
generator, which reproduces the behavioural target well, from PFE's
selection, which does not — it over-selects the 5-10 km bin (21.6% against
a 6.1% target).  Builds are expected to FAIL this gate until that is
addressed.  That is the gate working, not the gate being wrong.
"""
from __future__ import annotations

import math


# At most 10% of vehicles in the wrong RVU length bin (L1 = 2 x TV).
MAXIMUM_TRIP_LENGTH_L1 = 0.20

TRIP_LENGTH_GATE_RATIONALE = (
    "hogst 10% av fordonen far ligga i fel RVU-langdklass mot den "
    "tillganglighetskorrigerade malfordelningen (L1 = 2 x total variation)"
)


def _finite_non_negative(value: object) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0.0)


def effective_maximum_l1(declared: object = None) -> float:
    """Return the limit to judge a build by.

    A build may declare its own ``maximum_l1_distance``.  It is honoured
    only when it is STRICTER than the project limit: holding yourself to a
    higher standard is a build's business, while relaxing the standard you
    are judged against is not.  Anything absent, malformed, or looser falls
    back to the frozen project limit.
    """
    if _finite_non_negative(declared):
        return min(float(declared), MAXIMUM_TRIP_LENGTH_L1)
    return MAXIMUM_TRIP_LENGTH_L1
