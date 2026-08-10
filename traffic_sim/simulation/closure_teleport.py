"""Teleport policy for closure runs.

Stage 3 of `docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md`.

WHY THIS EXISTS.  SUMO relocates a gridlocked vehicle *along its own route*
after `--time-to-teleport` seconds of waiting (default 300 s).  A vehicle
stuck at the mouth of a closed edge is therefore carried PAST the closure,
and the closed edge's ``edgeData`` records an ``entered`` count that
``active_closure_throughput()`` reads as a leak.

Stage 1 measured the mechanism and REVISED the plan's premise
(`validation/closure_leak_mechanism_v1.json`):

  * teleports and closed-edge throughput are NOT one event counted twice —
    5 of 75 schedules teleported without any throughput, and one directly
    observed teleport was REFUSED entry to the closed edge and landed past
    it;
  * but a teleport is a NECESSARY condition: across all 75 schedules there
    was no throughput without one (``throughput_without_teleports: 0``).

Necessity is what makes this policy work.  Removing teleporting removes the
only observed path to a closed-edge entry, while leaving every legal movement
untouched.  Stage 2 had already refuted the alternative lever: widening
`REROUTER_RADIUS_M` from 400 m to 3 000 m grows the rerouter 86x, costs no
wall time, and changes the teleport count by nothing
(`validation/rerouter_radius_sweep_v1.json`).

WHAT THE POLICY IS NOT.  It is deliberately NOT option D of the plan
("stop counting teleport-induced entries as leaks"), which was rejected
outright.  Nothing here loosens a measurement.  The stuck vehicle still
exists; it simply stops being relocated through a road that is closed, and
is counted honestly as a trip that did not arrive.  That is the plan's own
requirement in one line:

    A vehicle that cannot get through must show up as *not arriving*,
    never as *driving through*.

THE VACUOUSNESS TRAP, AND THE GUARD.  Once teleporting is disabled,
``teleport_total`` is zero BY CONSTRUCTION, so "0 teleports" stops being
evidence of a healthy run.  Reading it as evidence would be exactly the
self-deception the plan rejected.  Every consumer therefore records
``policy_record()`` beside the count, so a zero always carries how it was
obtained, and `evaluate_stage3_gate()` requires the honest replacement
evidence — bounded growth in vehicles that never arrived — rather than
accepting the zero on its own.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Version tag written into every provenance record this module produces.
POLICY_VERSION = "closure_teleport_v1"

#: SUMO's built-in default, in seconds. Recorded for provenance only; this
#: module never emits it as a flag, so a run that does not opt in keeps a
#: byte-identical command line.
SUMO_DEFAULT_TIME_TO_TELEPORT_S = 300

#: The deployed closure policy: -1 disables SUMO teleporting entirely.
#:
#: The plan proposed defaulting to "a value chosen from Stage 1's waiting-time
#: distribution". Stage 1's measurement then revised the mechanism, and that
#: revision rules a finite default out: ANY finite threshold still teleports,
#: and a teleport is the necessary condition for the leak. Only -1 can satisfy
#: Stage 3's own gate that closed-edge throughput falls to zero.
CLOSURE_TIME_TO_TELEPORT_S = -1

#: Waiting time beyond which a driver is modelled as giving up on a closure
#: rather than queueing it out. Previously an unnamed literal `300` inside
#: `run_scenario.truncate_stranded_vehicles`, where it coincided with SUMO's
#: teleport default and so read as if it were derived from it. It is not: it
#: is a behavioural assumption about drivers, and it must keep its value when
#: the teleport threshold changes. Naming it here keeps the two apart.
MAX_CLOSURE_WAIT_S = 300


class ClosureTeleportPolicyError(ValueError):
    """Raised when a teleport policy value is not usable as a SUMO option."""


def normalize_time_to_teleport(value: Any) -> int:
    """Return `value` as a SUMO-usable integer second count, or raise.

    ``-1`` (disabled) and any positive threshold are accepted. ``0`` is
    refused: SUMO reads it as "teleport immediately", which is neither the
    disabled policy nor a plausible threshold, and a caller that meant
    "disabled" and typed 0 would silently get the most aggressive relocation
    setting there is.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClosureTeleportPolicyError(
            f"time-to-teleport must be an int, got {value!r}")
    if value == 0 or value < -1:
        raise ClosureTeleportPolicyError(
            "time-to-teleport must be -1 (disabled) or a positive number of "
            f"seconds, got {value}")
    return value


def teleport_disabled(value: int | None) -> bool:
    """True when this policy prevents SUMO from relocating stuck vehicles."""
    return value is not None and normalize_time_to_teleport(value) == -1


def policy_label(value: int | None) -> str:
    """Short, stable label naming how teleporting was configured.

    ``None`` means no flag was passed, i.e. SUMO's own default applies. That
    is reported as ``sumo_default_300s`` rather than as a chosen policy, so a
    run that never opted in cannot be read as one that deliberately kept
    teleporting on.
    """
    if value is None:
        return f"sumo_default_{SUMO_DEFAULT_TIME_TO_TELEPORT_S}s"
    value = normalize_time_to_teleport(value)
    return "disabled" if value == -1 else f"threshold_{value}s"


def policy_record(value: int | None) -> dict:
    """Provenance for one run's teleport configuration.

    ``teleport_count_is_informative`` is the whole point of carrying this
    around: when it is False, a zero teleport count says nothing about the
    run's health and the honest evidence is the unfinished/stuck population
    instead.
    """
    disabled = teleport_disabled(value)
    return {
        "policy_version": POLICY_VERSION,
        "time_to_teleport_s": value,
        "label": policy_label(value),
        "teleporting_enabled": not disabled,
        "teleport_count_is_informative": not disabled,
        "basis": (
            "closure runs disable SUMO teleporting: Stage 1 measured a "
            "teleport as the necessary condition for closed-edge throughput "
            "(validation/closure_leak_mechanism_v1.json), and Stage 2 refuted "
            "rerouter reach as the lever "
            "(validation/rerouter_radius_sweep_v1.json)"
            if disabled else
            "SUMO stuck-vehicle relocation is active; a teleport may carry a "
            "vehicle past a closed edge and register as throughput"),
    }


def sumo_arguments(value: int | None) -> list[str]:
    """The argv fragment for this policy — empty when nothing was chosen.

    Emitting nothing for ``None`` is what keeps every pre-existing caller's
    command line byte-identical, which the warm/cold equivalence contract in
    `monthly_warm_state` depends on.
    """
    if value is None:
        return []
    return ["--time-to-teleport", str(normalize_time_to_teleport(value))]


def _stuck_population(metrics: Mapping[str, Any] | Any) -> int:
    """Vehicles that did not complete their trip, however they were removed.

    Summed rather than picked: with teleporting disabled the population that
    used to be relocated has to land somewhere, and it lands in exactly these
    two places — never departed at all (`dropped_unreachable`) or still on the
    network when the run ended (`unfinished_trips`).
    """
    def read(name: str) -> int:
        if isinstance(metrics, Mapping):
            if name not in metrics:
                raise ClosureTeleportPolicyError(
                    f"metrics record is missing {name!r}")
            value = metrics[name]
        else:
            value = getattr(metrics, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ClosureTeleportPolicyError(
                f"metrics field {name!r} must be a non-negative int, "
                f"got {value!r}")
        return value

    return read("unfinished_trips") + read("dropped_unreachable")


def _throughput(metrics: Mapping[str, Any] | Any) -> int | None:
    if isinstance(metrics, Mapping):
        value = metrics.get("closed_edge_throughput")
    else:
        value = getattr(metrics, "closed_edge_throughput", None)
    if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ClosureTeleportPolicyError(
            "metrics field 'closed_edge_throughput' must be None or a "
            f"non-negative int, got {value!r}")
    return value


def _teleports(metrics: Mapping[str, Any] | Any) -> int:
    if isinstance(metrics, Mapping):
        value = metrics.get("teleport_total")
    else:
        value = getattr(metrics, "teleport_total", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ClosureTeleportPolicyError(
            "metrics field 'teleport_total' must be a non-negative int, "
            f"got {value!r}")
    return value


def evaluate_stage3_gate(*, before: Mapping[str, Any] | Any,
                         after: Mapping[str, Any] | Any,
                         detour_less_vehicles: int) -> dict:
    """Decide Stage 3's gate on one paired before/after closure measurement.

    The plan states it as: closed-edge throughput must fall to 0 while
    ``dropped_unreachable``/unfinished trips rise by no more than the number
    of vehicles Stage 1 showed as genuinely detour-less.

    ``before`` is the same closure simulated under SUMO's default teleport
    behaviour, ``after`` the same closure with this module's policy applied.
    Both must come from the same demand, seed and schedule — this function
    cannot verify that and does not pretend to; it is the caller's pairing.

    FAIL-CLOSED. An unmeasured throughput (``None``) is not a pass. "We never
    looked" and "we looked and it was zero" are different statements, and
    only the second one clears a gate whose entire subject is a measurement.
    """
    if isinstance(detour_less_vehicles, bool) or \
            not isinstance(detour_less_vehicles, int) or \
            detour_less_vehicles < 0:
        raise ClosureTeleportPolicyError(
            "detour_less_vehicles must be a non-negative int, got "
            f"{detour_less_vehicles!r}")

    after_throughput = _throughput(after)
    before_throughput = _throughput(before)
    stuck_before = _stuck_population(before)
    stuck_after = _stuck_population(after)
    stuck_growth = stuck_after - stuck_before

    teleports_before = _teleports(before)
    teleports_after = _teleports(after)
    checks = {
        "baseline_throughput_measured": before_throughput is not None,
        "baseline_exhibits_leak": (
            before_throughput is not None and before_throughput > 0),
        "baseline_exhibits_teleport_mechanism": teleports_before > 0,
        "throughput_measured": after_throughput is not None,
        "throughput_is_zero": after_throughput == 0,
        "throughput_reduced_to_zero": (
            before_throughput is not None and before_throughput > 0 and
            after_throughput == 0),
        "no_teleports": teleports_after == 0,
        "stuck_growth_within_detour_less": stuck_growth <= detour_less_vehicles,
    }
    return {
        "schema_version": 1,
        "kind": "closure_teleport_stage3_gate",
        "policy_version": POLICY_VERSION,
        "checks": checks,
        "passed": all(checks.values()),
        "measured": {
            "closed_edge_throughput_before": before_throughput,
            "closed_edge_throughput_after": after_throughput,
            "teleports_before": teleports_before,
            "teleports_after": teleports_after,
            "stuck_before": stuck_before,
            "stuck_after": stuck_after,
            "stuck_growth": stuck_growth,
            "detour_less_vehicles": detour_less_vehicles,
        },
        "gate": (
            "the default arm exhibits the measured teleport/leak mechanism, "
            "closed-edge throughput then falls to zero under the policy, and "
            "the unfinished/dropped population grows by no more than the "
            "genuinely detour-less vehicle count"),
    }
