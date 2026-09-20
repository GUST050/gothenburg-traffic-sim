"""Re-planning policy for vehicles a closure catches mid-trip.

WHY THIS EXISTS.  SUMO's `<rerouter>`/`closingReroute` offers a new route
only to a vehicle that ENTERS one of the rerouter's edges while the closure
is active, and this project attaches the rerouter to the closed edges plus
everything within `run_scenario.REROUTER_RADIUS_M` (400 m).  Two populations
are therefore never told:

  * a vehicle that entered its last rerouter edge moments BEFORE the
    closure opened, and
  * a vehicle queued in spillback OUTSIDE the ring — which, under a closure
    severe enough to matter, is most of them.

They keep their calibrated route, cannot enter the sealed edge, and — with
teleporting disabled (`closure_teleport`) — simply wait at the barrier.

MEASURED (2026-09-20, SUMO 1.27.1, mesoscopic, production flags):

  * uncongested corridor, one vehicle committed 16 s before the closure
    opened with a legal turn at the very next junction: it waited **3 589 s**
    of timeLoss.  With the device it took that turn, timeLoss **29 s**;
  * saturated corridor, 1 800 veh/h through a one-lane bottleneck, 1 h
    closure: **37 of 3 600** vehicles diverted with the rerouter alone,
    against **2 267 of 3 600** with the device.  The event-driven rerouter
    is starved exactly when the closure bites hardest, because the queue
    never reaches the edges that would trigger it.

That second number is why this is OFF BY DEFAULT.  It does not merely tidy
up an odd individual trajectory; it changes what a closure COSTS, and on
that probe it changed the sign of the measured effect.  Adopting it is a
decision to be taken against a measurement on the real network, not a
default to be slipped in.

WHY IT IS PER VEHICLE.  C1 tested the rerouting device by giving it to
EVERY vehicle and found it changes route choice, which would break the
calibrated demand the whole pipeline rests on.  The device is granted here
only to vehicles whose own route uses a closed edge during the closure
window — exactly the population `run_scenario.truncate_stranded_vehicles`
already identifies — so every other vehicle's route is untouched.

WHY A THRESHOLD AND NOT FROZEN WEIGHTS.  An adapting router re-plans
because of CONGESTION as well as closures, which is the confound C1 found.
Freezing the edge weights looks like the answer and is NOT: measured,
`--device.rerouting.adaptation-weight 1` and
`--device.rerouting.adaptation-interval 0` each silently disable the
re-planning altogether (the committed vehicle waits its full 3 589 s), so a
run configured that way would report the policy as enabled while doing
nothing.  The working guard is `--device.rerouting.threshold.factor`: a
closed edge makes the current route impossible, so no threshold can block
that diversion, while ordinary congestion never clears the bar.  Measured
on the saturated corridor with NO closure: 0 of 3 600 equipped vehicles
diverted and the total time loss was IDENTICAL to a run without the device.

NOT `--device.rerouting.mode 8`.  SUMO's own help reads "8 ignores
temporary blockages" — it would route THROUGH the closure, the exact
opposite of this policy.  C1's mode-8 note is about that flag, not this one.
"""

from __future__ import annotations

from typing import Any

#: Version tag written into every provenance record this module produces.
POLICY_VERSION = "closure_rerouting_v1"

#: Route-file parameter that forces the rerouting device onto ONE vehicle.
DEVICE_PARAM = "has.rerouting.device"

#: Default re-planning interval, in seconds, when the policy is enabled.
#: A committed driver is modelled as noticing the barrier within a minute,
#: which sits below `closure_teleport.MAX_CLOSURE_WAIT_S` (300 s) — the
#: point at which this project already models a driver as giving up. The
#: two must stay consistent: re-planning later than giving up would mean a
#: driver who abandons the trip before considering the turn.
DEFAULT_PERIOD_S = 60

#: How much better a new route must be before an equipped vehicle switches.
#: This is the congestion guard, and it is deliberately far above any
#: plausible congestion ratio: only an IMPOSSIBLE current route — a closed
#: edge — clears it. Measured inert on a saturated corridor with no closure
#: (0 diversions, identical total time loss) and fully permissive on a
#: closed one.
THRESHOLD_FACTOR = 10.0


class ClosureReroutingPolicyError(ValueError):
    """Raised when a re-planning period is not usable as a SUMO option."""


def normalize_period(value: Any) -> int:
    """Return `value` as a positive integer second count, or raise.

    Zero and negatives are refused rather than silently reinterpreted:
    SUMO reads a non-positive period as "never", which is the disabled
    policy wearing an enabled label — the same failure mode the frozen
    weights above turned out to have.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClosureReroutingPolicyError(
            f"rerouting period must be an int, got {value!r}")
    if value <= 0:
        raise ClosureReroutingPolicyError(
            f"rerouting period must be a positive number of seconds, got {value}")
    return value


def sumo_arguments(period_s: int | None) -> list[str]:
    """SUMO options for the policy, or nothing at all when it is off.

    ``None`` emits NO options, so a run that does not opt in keeps a
    byte-identical command line — the same contract
    `closure_teleport.sumo_arguments` keeps.
    """
    if period_s is None:
        return []
    period_s = normalize_period(period_s)
    return [
        "--device.rerouting.period", str(period_s),
        "--device.rerouting.threshold.factor", str(THRESHOLD_FACTOR),
    ]


def policy_label(period_s: int | None) -> str:
    """Short, stable label naming how re-planning was configured."""
    if period_s is None:
        return "disabled"
    return f"committed_only_{normalize_period(period_s)}s"


def policy_record(period_s: int | None) -> dict:
    """Provenance to publish beside any metric this policy can move.

    `equipped_population` is stated because the whole safety argument is
    that the device is NOT global: a reader must be able to tell this run
    from the one C1 rejected.
    """
    return {
        "policy_version": POLICY_VERSION,
        "label": policy_label(period_s),
        "period_s": period_s,
        "threshold_factor": None if period_s is None else THRESHOLD_FACTOR,
        "equipped_population": (
            None if period_s is None
            else "vehicles whose own route uses a closed edge during the "
                 "closure window and that keep a detour"),
        "changes_measured_closure_cost": period_s is not None,
    }
