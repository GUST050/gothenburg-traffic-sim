"""Exact-time warm-state boundary accounting for the monthly warm path.

SUMO's mesoscopic tripinfo output reads the private
``MSDevice_Tripinfo::myMesoTimeLoss`` accumulator, but that member is omitted
from the device's save/load state. Official TraCI documentation defines
``getTimeLoss()`` as accumulated time loss, but frozen SUMO 1.27 mesoscopic
save/load diagnostics show it does not reproduce this private tripinfo member
across the boundary. The production path therefore never uses the legacy TraCI
ledger as objective evidence.

The exact active ID set is captured at the save instant. A strict parser binds
SUMO's unfinished Tripinfo values to that population, and resumed output is
joined by vehicle identity. Each whole millisecond value is normalized once
with SUMO's decimal half-up formatting rule. Python's built-in ``round`` uses
ties-to-even and was the entire v14 residual: thousands of otherwise exact
vehicles were each one cent low. Missing, duplicate, overlapping, or pre-
boundary-unknown identities fail closed.

The state is still written by the same process and connection that reached the
exact save step, with the RNG and state-precision settings the cache identity
claims. Nothing here treats unit evidence as runtime equivalence; cache
publication still requires a passing paired campaign.

Nothing here imports `traci`, `subprocess` or `socket` at module scope; the
controller resolves them lazily so process-free checks stay process-free.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.simulation.warm_state_cache import (
    STATE_PRECISION, STATE_RNG_SAVED)

SNAPSHOT_FACTS_SCHEMA = "warm_snapshot_facts_v1"

# Bounded, deliberately. v3 published a map with one entry per airborne vehicle,
# which made the canonical payload grow with traffic. These are the only facts a
# reviewer needs in order to confirm the snapshot happened where it claims.
SNAPSHOT_FACT_FIELDS = frozenset({
    "schema", "warm_point_s", "captured_at_s", "active_vehicle_count",
    "save_state_rng", "save_state_precision",
})

SNAPSHOT_RNG_FLAG = "--save-state.rng"
SNAPSHOT_PRECISION_FLAG = "--save-state.precision"
KEEP_AFTER_ARRIVAL_FLAG = "--keep-after-arrival"

# Ordinary production output reports at this precision. Warm transport uses
# more digits so two real segments can be joined before this one final round.
TRIPINFO_PRECISION = 2
WARM_OUTPUT_PRECISION = 16
# Mesoscopic ``MSDevice_Tripinfo::myMesoTimeLoss`` is a ``SUMOTime`` integer
# and is incremented through ``TIME2STEPS`` in SUMO 1.27.1. Its exact public
# value therefore has millisecond precision. Re-quantize a reconstructed value
# to that native grid before carrying it across another chained boundary;
# otherwise repeated binary-float/16-digit XML round-trips eventually flip a
# final two-decimal half-up result by one cent.
MESO_TIMELOSS_PRECISION = 3
BOUNDARY_ACTIVE_SCHEMA = "warm_boundary_active_v3"
PREFIX_ACCUMULATOR_SCHEMA = "warm_prefix_meso_accumulator_v3"
MESO_RECONCILIATION_SCHEMA = "warm_meso_reconciliation_v2"

_TRIPINFO_START_RE = re.compile(r'<tripinfo(?![\w-])')
_TRIPINFO_RE = re.compile(r'<tripinfo\b[^>]*\bid="([^"]*)"[^>]*\btimeLoss="([^"]*)"')


class BoundaryLedgerError(Exception):
    """Raised when snapshot evidence is malformed. Never raised to skip work.

    The name is unchanged because every caller and test already catches it; what
    it guards is now the snapshot, not a ledger.
    """


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def check_time_loss(value) -> float:
    """Validate a time loss without rounding it. Fail closed on bad input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BoundaryLedgerError(f"time loss must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise BoundaryLedgerError("time loss must be finite")
    if value < 0:
        raise BoundaryLedgerError(f"time loss must be non-negative, got {value}")
    return value


def normalize_time_loss(value, precision: int = TRIPINFO_PRECISION) -> float:
    """Format like SUMO's positive fixed-precision decimal output.

    Python's built-in ``round`` is ties-to-even. SUMO's output rounds positive
    half-way millisecond values up, so ``36.555`` must become ``36.56`` rather
    than ``36.55``. Applied to a WHOLE value, never to either side of the warm
    boundary.
    """
    if isinstance(precision, bool) or not isinstance(precision, int) \
            or precision < 0:
        raise BoundaryLedgerError("precision must be a non-negative integer")
    checked = check_time_loss(value)
    quantum = Decimal(1).scaleb(-precision)
    try:
        return float(Decimal(str(checked)).quantize(
            quantum, rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise BoundaryLedgerError("time loss cannot be normalized") from error


# ── The snapshot command ─────────────────────────────────────────────────────

def snapshot_settings_arguments() -> list[str]:
    """The state-serialization flags, DERIVED from the cache constants.

    Not a hand-written copy. `STATE_RNG_SAVED` and `STATE_PRECISION` are what the
    identity records, so deriving the argv from them makes the two impossible to
    disagree — which is exactly how v3 came to claim settings it never applied.
    """
    if STATE_RNG_SAVED is not True:
        raise BoundaryLedgerError(
            "the warm-state cache requires the RNG to be saved; a snapshot "
            "without it cannot reproduce the run it claims to continue")
    return [SNAPSHOT_RNG_FLAG, "true",
            SNAPSHOT_PRECISION_FLAG, str(int(STATE_PRECISION))]


def _single_option(tokens, flag: str, expected: str) -> None:
    positions = [i for i, token in enumerate(tokens) if token == flag]
    if len(positions) != 1:
        raise BoundaryLedgerError(
            f"the warm command must contain exactly one {flag}, found "
            f"{len(positions)}")
    index = positions[0]
    if index + 1 >= len(tokens) or tokens[index + 1] != expected:
        actual = None if index + 1 >= len(tokens) else tokens[index + 1]
        raise BoundaryLedgerError(
            f"{flag} must be {expected!r}, got {actual!r}")


def validate_snapshot_command(cmd) -> dict:
    """The snapshot argv must carry each setting EXACTLY once, and no more.

    Duplicates are refused as well as omissions: SUMO takes the last occurrence,
    so a duplicated flag silently lets whichever copy came last decide the
    state's fidelity. Warm reconstruction requires global precision 16 because SUMO exposes no
    tripinfo-only precision flag; the warm caller must normalize other affected
    output fields back to ordinary cold semantics.
    """
    tokens = [str(token) for token in cmd]
    facts: dict[str, Any] = {}
    for flag, label, expected in (
            (SNAPSHOT_RNG_FLAG, "save_state_rng", "true"),
            (SNAPSHOT_PRECISION_FLAG, "save_state_precision",
             str(int(STATE_PRECISION)))):
        positions = [i for i, token in enumerate(tokens) if token == flag]
        if len(positions) != 1:
            raise BoundaryLedgerError(
                f"the snapshot command must contain exactly one {flag}, found "
                f"{len(positions)}")
        index = positions[0]
        if index + 1 >= len(tokens):
            raise BoundaryLedgerError(f"{flag} has no value")
        value = tokens[index + 1]
        if value != expected:
            raise BoundaryLedgerError(
                f"{flag} is {value!r}, but the cache identity records "
                f"{expected!r}")
        facts[label] = True if expected == "true" else int(expected)
    # SUMO 1.27's mesoscopic tripinfo accumulator is private and omitted from
    # MSDevice_Tripinfo::saveState/loadState.  The prefix therefore transports
    # that value through unfinished tripinfo, and both warm halves need enough
    # output precision to be joined before the ordinary two-decimal reporting
    # round.  This is an output transport setting; production normalisation is
    # still applied once to each reconstructed whole vehicle.
    _single_option(tokens, "--precision", str(WARM_OUTPUT_PRECISION))
    _single_option(tokens, "--tripinfo-output.write-unfinished", "true")
    return facts


def validate_resumed_command(cmd, *, warm_point_s: int,
                             terminal_time_s: int,
                             require_retention: bool = False) -> int | None:
    """Validate warm transport and optional legacy diagnostic retention."""
    if isinstance(warm_point_s, bool) or not isinstance(warm_point_s, int) \
            or isinstance(terminal_time_s, bool) \
            or not isinstance(terminal_time_s, int) \
            or terminal_time_s <= warm_point_s:
        raise BoundaryLedgerError("the resumed time domain is invalid")
    tokens = [str(token) for token in cmd]
    _single_option(tokens, "--precision", str(WARM_OUTPUT_PRECISION))
    # The resumed half emits unfinished vehicles too: their post-boundary
    # segment is joined to the prefix segment by vehicle identity. Relying on
    # the command builder's default made a future explicit false value pass
    # validation and silently erase the very records the join requires.
    _single_option(tokens, "--tripinfo-output.write-unfinished", "true")
    positions = [index for index, token in enumerate(tokens)
                 if token == KEEP_AFTER_ARRIVAL_FLAG]
    if not require_retention:
        if positions:
            raise BoundaryLedgerError(
                f"the production resumed command must omit {KEEP_AFTER_ARRIVAL_FLAG}; "
                "retaining every arrived vehicle was needed only by the "
                "retired TraCI terminal-ledger diagnostic")
        return None
    if len(positions) != 1:
        raise BoundaryLedgerError(
            f"the resumed command must contain exactly one "
            f"{KEEP_AFTER_ARRIVAL_FLAG}, found {len(positions)}")
    index = positions[0]
    if index + 1 >= len(tokens):
        raise BoundaryLedgerError(f"{KEEP_AFTER_ARRIVAL_FLAG} has no value")
    try:
        retention = int(tokens[index + 1])
    except ValueError:
        raise BoundaryLedgerError(
            f"{KEEP_AFTER_ARRIVAL_FLAG} must be an integer")
    required = terminal_time_s - warm_point_s
    if retention < required:
        raise BoundaryLedgerError(
            f"{KEEP_AFTER_ARRIVAL_FLAG} {retention} does not cover the "
            f"{required}-second resumed horizon")
    return retention


def capture_boundary_active(connection, *, warm_point_s: int) -> dict:
    """Capture exact boundary membership from the state-writing connection."""
    now = connection.simulation.getTime()
    if isinstance(now, bool) or not isinstance(now, (int, float)) \
            or float(now) != float(warm_point_s):
        raise BoundaryLedgerError(
            f"boundary identities were captured at {now!r}, not "
            f"{warm_point_s}")
    raw = list(connection.vehicle.getIDList())
    if any(not isinstance(item, str) or not item for item in raw):
        raise BoundaryLedgerError("boundary identities must be non-empty strings")
    if len(raw) != len(set(raw)):
        raise BoundaryLedgerError("duplicate boundary-active vehicle id")
    record = {
        "schema": BOUNDARY_ACTIVE_SCHEMA,
        "warm_point_s": int(warm_point_s),
        "captured_at_s": float(warm_point_s),
        "vehicle_ids": sorted(raw),
    }
    record["digest"] = hashlib.sha256(_canonical(record)).hexdigest()
    return record


def validate_boundary_active(record, *, expected_warm_point_s=None) -> dict:
    required = {"schema", "warm_point_s", "captured_at_s", "vehicle_ids",
                "digest"}
    if not isinstance(record, Mapping) or set(record) != required:
        raise BoundaryLedgerError("boundary-active record field set is wrong")
    if record["schema"] != BOUNDARY_ACTIVE_SCHEMA:
        raise BoundaryLedgerError("boundary-active record schema is wrong")
    point = record["warm_point_s"]
    if isinstance(point, bool) or not isinstance(point, int) or point <= 0:
        raise BoundaryLedgerError("boundary-active warm point is invalid")
    if expected_warm_point_s is not None and point != expected_warm_point_s:
        raise BoundaryLedgerError("boundary-active warm point does not match")
    if record["captured_at_s"] != float(point):
        raise BoundaryLedgerError("boundary-active capture instant does not match")
    ids = record["vehicle_ids"]
    if not isinstance(ids, list) or ids != sorted(ids) or len(ids) != len(set(ids)) \
            or any(not isinstance(item, str) or not item for item in ids):
        raise BoundaryLedgerError("boundary-active identities are not canonical")
    body = {key: value for key, value in record.items() if key != "digest"}
    if record["digest"] != hashlib.sha256(_canonical(body)).hexdigest():
        raise BoundaryLedgerError("boundary-active digest does not match")
    return dict(record)


def _strict_tripinfo(path) -> dict[str, dict[str, float]]:
    """Read exact identity/timing/loss records; malformed XML never degrades."""
    try:
        root = ET.parse(Path(path)).getroot()
    except (OSError, ET.ParseError) as error:
        raise BoundaryLedgerError(f"tripinfo XML is unreadable: {error}")
    records: dict[str, dict[str, float]] = {}
    for element in root.iter("tripinfo"):
        identifier = element.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise BoundaryLedgerError("tripinfo record has no vehicle id")
        if identifier in records:
            raise BoundaryLedgerError(
                f"duplicate tripinfo vehicle id {identifier!r}")
        raw_loss = element.get("timeLoss")
        raw_arrival = element.get("arrival")
        raw_depart = element.get("depart")
        try:
            loss = float(raw_loss)
            arrival = float(raw_arrival)
            depart = float(raw_depart)
        except (TypeError, ValueError):
            raise BoundaryLedgerError(
                f"tripinfo record {identifier!r} lacks numeric "
                "depart/arrival/timeLoss")
        if not math.isfinite(arrival) or not math.isfinite(depart) or depart < 0:
            raise BoundaryLedgerError(
                f"tripinfo timing for {identifier!r} is invalid")
        records[identifier] = {
            "time_loss_s": check_time_loss(loss),
            "depart_s": depart,
            "arrival_s": arrival,
        }
    if not records:
        raise BoundaryLedgerError("tripinfo contains no vehicle records")
    return records


def build_prefix_accumulator(path, boundary_active, *,
                             precision: int = TRIPINFO_PRECISION) -> dict:
    """Partition high-precision prefix tripinfo and retain private meso loss.

    `write-unfinished=true` makes SUMO call MSDevice_Tripinfo::generateOutput
    for vehicles still in flight.  In meso mode that output reads the private
    `myMesoTimeLoss` member which SUMO's state serializer omits.  The active ID
    record comes from the same connection and instant that wrote the state, so
    membership is evidence rather than an arrival-time guess.
    """
    active = validate_boundary_active(boundary_active)
    active_ids = set(active["vehicle_ids"])
    records = _strict_tripinfo(path)
    seen_active = active_ids & set(records)
    if seen_active != active_ids:
        raise BoundaryLedgerError(
            "unfinished prefix tripinfo identities do not equal the captured "
            f"boundary population: missing={sorted(active_ids-seen_active)} "
            f"extra={sorted(seen_active-active_ids)}")
    finished_active = sorted(
        identifier for identifier in active_ids
        if records[identifier]["arrival_s"] >= 0)
    if finished_active:
        raise BoundaryLedgerError(
            "captured boundary-active vehicles are finalized without a step: "
            f"{finished_active}")
    late_nonactive = sorted(
        identifier for identifier, value in records.items()
        if identifier not in active_ids
        and value["arrival_s"] > active["warm_point_s"])
    if late_nonactive:
        raise BoundaryLedgerError(
            "prefix output contains post-boundary vehicles without a step: "
            f"{late_nonactive}")
    completed = {
        identifier: normalize_time_loss(value["time_loss_s"], precision)
        for identifier, value in records.items()
        if identifier not in active_ids
        and 0 <= value["arrival_s"] <= active["warm_point_s"]
    }
    entries = {
        identifier: records[identifier]["time_loss_s"]
        for identifier in sorted(active_ids)
    }
    ledger = {
        "schema": PREFIX_ACCUMULATOR_SCHEMA,
        "warm_point_s": active["warm_point_s"],
        "output_precision": WARM_OUTPUT_PRECISION,
        "active_population_digest": active["digest"],
        "entries": entries,
    }
    ledger["digest"] = hashlib.sha256(_canonical(ledger)).hexdigest()
    return {
        "completed_trips": {
            "total_time_loss_s": sum(completed.values()),
            "trip_count": len(completed),
        },
        "completed_records": dict(sorted(completed.items())),
        # JSON objects are canonicalized with sorted keys, which destroys the
        # order SUMO emitted completed tripinfo.  Keep that order separately:
        # Python's cold metric reader adds in XML order, and continuing that
        # exact accumulator is required for bit-exact cold/warm equality.
        "completed_record_order": list(completed),
        "active_accumulator": ledger,
    }


def validate_prefix_accumulator(ledger, *, expected_warm_point_s=None) -> dict:
    required = {"schema", "warm_point_s", "output_precision",
                "active_population_digest", "entries", "digest"}
    if not isinstance(ledger, Mapping) or set(ledger) != required:
        raise BoundaryLedgerError("prefix accumulator field set is wrong")
    if ledger["schema"] != PREFIX_ACCUMULATOR_SCHEMA:
        raise BoundaryLedgerError("prefix accumulator schema is wrong")
    point = ledger["warm_point_s"]
    if isinstance(point, bool) or not isinstance(point, int) or point <= 0 \
            or (expected_warm_point_s is not None
                and point != expected_warm_point_s):
        raise BoundaryLedgerError("prefix accumulator warm point is wrong")
    if ledger["output_precision"] != WARM_OUTPUT_PRECISION:
        raise BoundaryLedgerError("prefix accumulator output precision is wrong")
    if not isinstance(ledger["active_population_digest"], str) \
            or re.fullmatch(r"[0-9a-f]{64}",
                            ledger["active_population_digest"]) is None:
        raise BoundaryLedgerError("active population digest is invalid")
    entries = ledger["entries"]
    if not isinstance(entries, Mapping) or list(entries) != sorted(entries):
        raise BoundaryLedgerError("prefix accumulator entries are not canonical")
    for identifier, value in entries.items():
        if not isinstance(identifier, str) or not identifier:
            raise BoundaryLedgerError("prefix accumulator id is invalid")
        check_time_loss(value)
    # The outer ledger digest only proves self-consistency.  Reconstruct the
    # exact boundary-active record as well, otherwise an arbitrary population
    # digest plus a freshly hashed outer object would validate successfully.
    population = {
        "schema": BOUNDARY_ACTIVE_SCHEMA,
        "warm_point_s": point,
        "captured_at_s": float(point),
        "vehicle_ids": list(entries),
    }
    expected_population_digest = hashlib.sha256(
        _canonical(population)).hexdigest()
    if ledger["active_population_digest"] != expected_population_digest:
        raise BoundaryLedgerError(
            "active population digest does not match accumulator identities")
    body = {key: value for key, value in ledger.items() if key != "digest"}
    if ledger["digest"] != hashlib.sha256(_canonical(body)).hexdigest():
        raise BoundaryLedgerError("prefix accumulator digest does not match")
    return dict(ledger)


def reconcile_resumed_tripinfo(path, prefix_accumulator, *,
                               completed_vehicle_ids=(),
                               completed_prefix_total,
                               precision: int = TRIPINFO_PRECISION) -> dict:
    """Reconstruct and round each whole vehicle exactly once."""
    ledger = validate_prefix_accumulator(prefix_accumulator)
    records = _strict_tripinfo(path)
    active = set(ledger["entries"])
    completed = list(completed_vehicle_ids)
    if completed != sorted(completed) or len(completed) != len(set(completed)) \
            or any(not isinstance(item, str) or not item for item in completed):
        raise BoundaryLedgerError("completed prefix identities are not canonical")
    check_time_loss(completed_prefix_total)
    overlap = sorted(set(completed) & set(records))
    if overlap:
        raise BoundaryLedgerError(
            f"vehicles occur in both completed prefix and resumed output: {overlap}")
    missing = sorted(active - set(records))
    if missing:
        raise BoundaryLedgerError(
            f"resumed tripinfo is missing boundary vehicles: {missing}")
    wrong_active = sorted(
        identifier for identifier in active
        if records[identifier]["depart_s"] > ledger["warm_point_s"])
    if wrong_active:
        raise BoundaryLedgerError(
            "boundary-active vehicles depart after the captured boundary: "
            f"{wrong_active}")
    unknown = sorted(
        identifier for identifier, value in records.items()
        if identifier not in active
        and value["depart_s"] < ledger["warm_point_s"])
    if unknown:
        raise BoundaryLedgerError(
            "resumed tripinfo contains unknown pre-boundary vehicles: "
            f"{unknown}")
    corrected: dict[str, float] = {}
    # Preserve SUMO's XML order for the numeric accumulation. Cold production
    # uses that same order; sorting IDs here can introduce a floating-point-only
    # mismatch even when every reported two-decimal vehicle value is identical.
    for identifier, value in records.items():
        whole = value["time_loss_s"] + ledger["entries"].get(identifier, 0.0)
        corrected[identifier] = normalize_time_loss(whole, precision)
    raw_total = sum(value["time_loss_s"] for value in records.values())
    total = sum(corrected.values())
    # Continue the exact accumulator state produced by the prefix XML reader.
    # Adding two already-computed segment totals changes floating-point grouping
    # and can differ from an uninterrupted cold `sum` despite identical vehicle
    # values.  Prefix-completed records necessarily precede resumed arrivals, so
    # this is the same order the cold tripinfo parser observes.
    combined_total = sum(corrected.values(), start=float(completed_prefix_total))
    return {
        "schema": MESO_RECONCILIATION_SCHEMA,
        "active_count": len(active),
        "resumed_count": len(records),
        # ``missing`` above proves every boundary-active identity was corrected.
        # This is a reported population size, not an independent cross-check.
        "corrected_count": len(active),
        "prefix_accumulator_total_s": sum(ledger["entries"].values()),
        "raw_total_time_loss_s": raw_total,
        # Resumed records only; reconstruct_metrics adds the completed prefix.
        "corrected_total_time_loss_s": total,
        # Whole-run continued accumulator; use only for the exact split join.
        "combined_total_time_loss_s": combined_total,
        "correction_delta_s": total - raw_total,
        "trip_count": len(records),
        "tripinfo_precision": precision,
        "prefix_accumulator_digest": ledger["digest"],
        "records_digest": hashlib.sha256(_canonical(corrected)).hexdigest(),
        # Private workspace material for the validation observer. Callers must
        # remove this unbounded map from canonical split diagnostics.
        "records": corrected,
    }


# ── Snapshot facts ───────────────────────────────────────────────────────────

def capture_snapshot_facts(connection, *, warm_point_s: int,
                           save_state_rng: bool = STATE_RNG_SAVED,
                           save_state_precision: int = STATE_PRECISION) -> dict:
    """Read BOUNDED facts about the snapshot from an injected connection.

    The reported simulation time must EQUAL the warm point. A snapshot taken a
    step early or late describes a different instant than the identity claims,
    and every later comparison would then be confidently wrong, so a mismatch is
    fatal to the warm path rather than a rounding concern.

    `active_vehicle_count` is recorded for diagnosis only — a count, never a map,
    and nothing in the accounting depends on it.
    """
    if not isinstance(warm_point_s, int) or isinstance(warm_point_s, bool) \
            or warm_point_s <= 0:
        raise BoundaryLedgerError("warm_point_s must be a positive integer")

    now = connection.simulation.getTime()
    if not isinstance(now, (int, float)) or isinstance(now, bool):
        raise BoundaryLedgerError("simulation time must be a number")
    if float(now) != float(warm_point_s):
        raise BoundaryLedgerError(
            f"snapshot is at simulation time {now}, not the warm point "
            f"{warm_point_s}; it would describe a different instant")

    identifiers = list(connection.vehicle.getIDList())
    for vehicle in identifiers:
        if not isinstance(vehicle, str) or not vehicle:
            raise BoundaryLedgerError(f"invalid active vehicle id {vehicle!r}")
    if len(set(identifiers)) != len(identifiers):
        raise BoundaryLedgerError("duplicate active vehicle id at the snapshot")

    return {
        "schema": SNAPSHOT_FACTS_SCHEMA,
        "warm_point_s": warm_point_s,
        "captured_at_s": float(warm_point_s),
        "active_vehicle_count": len(identifiers),
        "save_state_rng": bool(save_state_rng),
        "save_state_precision": int(save_state_precision),
    }


def validate_snapshot_facts(facts, *, expected_warm_point_s=None) -> dict:
    """Fail closed on malformed, legacy, or wrong-instant snapshot facts."""
    if not isinstance(facts, Mapping):
        raise BoundaryLedgerError("snapshot facts must be an object")
    if facts.get("schema") != SNAPSHOT_FACTS_SCHEMA:
        raise BoundaryLedgerError(
            f"unsupported snapshot facts schema {facts.get('schema')!r}; "
            f"legacy evidence is never interpreted")
    if set(facts) != SNAPSHOT_FACT_FIELDS:
        raise BoundaryLedgerError(
            f"snapshot facts field set is wrong: missing="
            f"{sorted(SNAPSHOT_FACT_FIELDS - set(facts))} "
            f"extra={sorted(set(facts) - SNAPSHOT_FACT_FIELDS)}")

    point = facts["warm_point_s"]
    if not isinstance(point, int) or isinstance(point, bool) or point <= 0:
        raise BoundaryLedgerError("snapshot warm_point_s must be a positive int")
    if expected_warm_point_s is not None and point != expected_warm_point_s:
        raise BoundaryLedgerError(
            f"snapshot describes warm point {point}, not the expected "
            f"{expected_warm_point_s}")
    captured = facts["captured_at_s"]
    if isinstance(captured, bool) or not isinstance(captured, (int, float)) \
            or not math.isfinite(float(captured)):
        raise BoundaryLedgerError("captured_at_s must be a finite number")
    if float(captured) != float(point):
        raise BoundaryLedgerError(
            f"snapshot was captured at {captured}, not at its own warm point "
            f"{point}")
    count = facts["active_vehicle_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise BoundaryLedgerError(
            "active_vehicle_count must be a non-negative integer")

    # The settings must be the ones the cache identity records, or the state on
    # disk is not the state the key describes.
    if facts["save_state_rng"] is not True:
        raise BoundaryLedgerError(
            "the snapshot did not save the RNG; the identity records that it "
            "does, so this state cannot be trusted to continue its run")
    if facts["save_state_precision"] != int(STATE_PRECISION):
        raise BoundaryLedgerError(
            f"snapshot precision {facts['save_state_precision']} does not match "
            f"the cache constant {STATE_PRECISION}")
    return dict(facts)


def snapshot_digest(facts) -> str:
    """Canonical digest of validated snapshot facts."""
    return hashlib.sha256(_canonical(validate_snapshot_facts(facts))).hexdigest()


# ── Selective restore-boundary correction (LUNA-WARM-23) ─────────────────────
#
# WHAT LUNA-WARM-22 MEASURED, stated no wider than the evidence.
#
# The v9 campaign's residual is carried entirely by a MINORITY of the vehicles
# in flight across the warm point: 5 of 44, 10 of 50 and 12 of 51 for q10/q50/
# q90, summing to exactly -7.73, -80.62 and -138.97 s. Every other vehicle —
# more than 99.99% of the population — is byte-identical between the cold run
# and the split. Most affected vehicles lose their ENTIRE accumulated time loss
# on resume; a few lose part of it.
#
# So BOTH earlier rules are refuted:
#   * "the accumulator is always preserved" (LUNA-WARM-08's one-vehicle probe)
#     is true for most active vehicles and false for a measured minority;
#   * a blanket per-vehicle boundary offset (v3's design) would double count the
#     ~80% of active vehicles whose accumulator demonstrably survives.
#
# WHY THIS SELECTION HAPPENS IS UNKNOWN. This module does not need to know. It
# MEASURES each active vehicle's accumulated time loss at the save instant and
# again immediately after the load, and corrects only the difference it actually
# observed. An unmeasured vehicle is never corrected, and a vehicle whose value
# survived is left exactly as it is.

SAVE_LEDGER_SCHEMA = "warm_save_ledger_v1"
RESTORE_AUDIT_SCHEMA = "warm_restore_audit_v1"
FINAL_LEDGER_SCHEMA = "warm_final_ledger_v1"
RESTORE_CORRECTION_SCHEMA = "warm_restore_correction_v1"

SAVE_LEDGER_FIELDS = frozenset(
    {"schema", "warm_point_s", "captured_at_s", "entries", "digest"})
RESTORE_AUDIT_FIELDS = frozenset(
    {"schema", "warm_point_s", "saved_digest", "restored_digest", "deficits",
     "affected_count", "deficit_total_s", "preserved_count", "digest"})
FINAL_LEDGER_FIELDS = frozenset(
    {"schema", "warm_point_s", "terminal_time_s", "audit_digest", "entries",
     "digest"})


def _accumulator_map(connection, *, warm_point_s: int, label: str) -> dict:
    """`{vehicle_id: accumulated timeLoss}` for every active vehicle, at an
    instant that MUST equal the warm point.

    Read through one injected connection so the caller cannot substitute a
    different process, and refused outright if the clock disagrees — a ledger
    taken a step early or late describes a different simulation than the one it
    claims to, and would silently corrupt every deficit derived from it.
    """
    if not isinstance(warm_point_s, int) or isinstance(warm_point_s, bool) \
            or warm_point_s <= 0:
        raise BoundaryLedgerError("warm_point_s must be a positive integer")
    now = connection.simulation.getTime()
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise BoundaryLedgerError(f"{label}: simulation time must be a number")
    if float(now) != float(warm_point_s):
        raise BoundaryLedgerError(
            f"{label}: taken at simulation time {now}, not the warm point "
            f"{warm_point_s}; it would describe a different instant")

    identifiers = list(connection.vehicle.getIDList())
    entries: dict[str, float] = {}
    for vehicle in identifiers:
        if not isinstance(vehicle, str) or not vehicle:
            raise BoundaryLedgerError(f"{label}: invalid vehicle id {vehicle!r}")
        if vehicle in entries:
            raise BoundaryLedgerError(
                f"{label}: duplicate active vehicle id {vehicle!r}")
        value = connection.vehicle.getTimeLoss(vehicle)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise BoundaryLedgerError(
                f"{label}: {vehicle!r} reported a non-finite time loss {value!r}")
        if float(value) < 0.0:
            raise BoundaryLedgerError(
                f"{label}: {vehicle!r} reported a negative time loss {value!r}")
        entries[vehicle] = float(value)
    return entries


def capture_save_ledger(connection, *, warm_point_s: int) -> dict:
    """The canonical accumulator ledger at the SAVE instant.

    Taken from the same TraCI connection, the same simulation instant and the
    same process that writes the state file. Anything looser would let the
    ledger describe a run other than the one the state came from.
    """
    entries = _accumulator_map(connection, warm_point_s=warm_point_s,
                               label="save ledger")
    ledger = {
        "schema": SAVE_LEDGER_SCHEMA,
        "warm_point_s": int(warm_point_s),
        "captured_at_s": float(warm_point_s),
        "entries": dict(sorted(entries.items())),
    }
    ledger["digest"] = hashlib.sha256(_canonical(
        {k: v for k, v in ledger.items() if k != "digest"})).hexdigest()
    return ledger


def capture_restore_ledger(connection, *, warm_point_s: int) -> dict:
    """The accumulator ledger immediately AFTER the load, before any step.

    Same shape as the save ledger, and deliberately the same code path: a
    difference between the two must come from the save/load boundary, never from
    two readers disagreeing about how to read.
    """
    entries = _accumulator_map(connection, warm_point_s=warm_point_s,
                               label="restore ledger")
    ledger = {
        "schema": SAVE_LEDGER_SCHEMA,
        "warm_point_s": int(warm_point_s),
        "captured_at_s": float(warm_point_s),
        "entries": dict(sorted(entries.items())),
    }
    ledger["digest"] = hashlib.sha256(_canonical(
        {k: v for k, v in ledger.items() if k != "digest"})).hexdigest()
    return ledger


def validate_save_ledger(ledger, *, expected_warm_point_s=None) -> dict:
    """Fail closed on malformed, legacy, drifted or tampered ledgers."""
    if not isinstance(ledger, Mapping):
        raise BoundaryLedgerError("a ledger must be an object")
    if ledger.get("schema") != SAVE_LEDGER_SCHEMA:
        raise BoundaryLedgerError(
            f"unsupported ledger schema {ledger.get('schema')!r}; legacy "
            f"evidence is never interpreted")
    if set(ledger) != SAVE_LEDGER_FIELDS:
        raise BoundaryLedgerError(
            f"ledger field set is wrong: missing="
            f"{sorted(SAVE_LEDGER_FIELDS - set(ledger))} "
            f"extra={sorted(set(ledger) - SAVE_LEDGER_FIELDS)}")
    point = ledger["warm_point_s"]
    if not isinstance(point, int) or isinstance(point, bool) or point <= 0:
        raise BoundaryLedgerError("ledger warm_point_s must be a positive int")
    if expected_warm_point_s is not None and point != expected_warm_point_s:
        raise BoundaryLedgerError(
            f"ledger describes warm point {point}, not the expected "
            f"{expected_warm_point_s}")
    captured = ledger["captured_at_s"]
    if isinstance(captured, bool) or not isinstance(captured, (int, float)) \
            or float(captured) != float(point):
        raise BoundaryLedgerError(
            f"ledger captured_at_s {captured!r} is not its own warm point "
            f"{point}")
    entries = ledger["entries"]
    if not isinstance(entries, Mapping):
        raise BoundaryLedgerError("ledger entries must be an object")
    for vehicle, value in entries.items():
        if not isinstance(vehicle, str) or not vehicle:
            raise BoundaryLedgerError(f"invalid ledger vehicle id {vehicle!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)) or float(value) < 0.0:
            raise BoundaryLedgerError(
                f"ledger value for {vehicle!r} must be finite and "
                f"non-negative, got {value!r}")
    recomputed = hashlib.sha256(_canonical(
        {k: v for k, v in ledger.items() if k != "digest"})).hexdigest()
    if recomputed != ledger["digest"]:
        raise BoundaryLedgerError(
            "ledger digest does not recompute; the evidence was altered")
    return dict(ledger)


def build_restore_audit(saved, restored, *, expected_warm_point_s=None) -> dict:
    """The MEASURED deficits, and nothing inferred.

    Both ledgers must cover exactly the same vehicles at exactly the same warm
    point. A restored value ABOVE its saved value is refused rather than
    clamped: the boundary is supposed to preserve or lose accumulated delay, and
    an increase means something is wrong that a correction must not paper over.

    Only strictly positive `saved - restored` differences become deficits.
    Vehicles whose value survived are recorded as preserved and are never
    touched — that is what makes this selective rather than a blanket offset.
    """
    saved = validate_save_ledger(saved, expected_warm_point_s=expected_warm_point_s)
    restored = validate_save_ledger(
        restored, expected_warm_point_s=saved["warm_point_s"])

    saved_entries, restored_entries = saved["entries"], restored["entries"]
    missing = sorted(set(saved_entries) - set(restored_entries))
    extra = sorted(set(restored_entries) - set(saved_entries))
    if missing or extra:
        raise BoundaryLedgerError(
            f"restore ledger does not cover the same vehicles: "
            f"missing={missing[:8]} extra={extra[:8]}; a correction without "
            f"exact identity coverage would be guesswork")

    deficits: dict[str, float] = {}
    preserved = 0
    for vehicle in sorted(saved_entries):
        before, after = saved_entries[vehicle], restored_entries[vehicle]
        if after > before:
            raise BoundaryLedgerError(
                f"{vehicle!r} restored with MORE accumulated time loss "
                f"({after} > {before}); the boundary cannot create delay and a "
                f"correction must not hide it")
        deficit = before - after
        if deficit > 0.0:
            deficits[vehicle] = deficit
        else:
            preserved += 1

    audit = {
        "schema": RESTORE_AUDIT_SCHEMA,
        "warm_point_s": saved["warm_point_s"],
        "saved_digest": saved["digest"],
        "restored_digest": restored["digest"],
        "deficits": deficits,
        "affected_count": len(deficits),
        "deficit_total_s": normalize_time_loss(sum(deficits.values())),
        "preserved_count": preserved,
    }
    audit["digest"] = hashlib.sha256(_canonical(
        {k: v for k, v in audit.items() if k != "digest"})).hexdigest()
    return audit


def validate_restore_audit(audit, *, expected_warm_point_s=None) -> dict:
    """Refuse an audit that does not recompute to its own claims."""
    if not isinstance(audit, Mapping):
        raise BoundaryLedgerError("a restore audit must be an object")
    if audit.get("schema") != RESTORE_AUDIT_SCHEMA:
        raise BoundaryLedgerError(
            f"unsupported restore-audit schema {audit.get('schema')!r}; legacy "
            f"evidence is never interpreted")
    if set(audit) != RESTORE_AUDIT_FIELDS:
        raise BoundaryLedgerError(
            f"restore-audit field set is wrong: missing="
            f"{sorted(RESTORE_AUDIT_FIELDS - set(audit))} "
            f"extra={sorted(set(audit) - RESTORE_AUDIT_FIELDS)}")
    point = audit["warm_point_s"]
    if not isinstance(point, int) or isinstance(point, bool) or point <= 0:
        raise BoundaryLedgerError("audit warm_point_s must be a positive int")
    if expected_warm_point_s is not None and point != expected_warm_point_s:
        raise BoundaryLedgerError(
            f"audit describes warm point {point}, not the expected "
            f"{expected_warm_point_s}")
    deficits = audit["deficits"]
    if not isinstance(deficits, Mapping):
        raise BoundaryLedgerError("audit deficits must be an object")
    for vehicle, value in deficits.items():
        if not isinstance(vehicle, str) or not vehicle:
            raise BoundaryLedgerError(f"invalid deficit vehicle id {vehicle!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)) or float(value) <= 0.0:
            raise BoundaryLedgerError(
                f"deficit for {vehicle!r} must be finite and strictly "
                f"positive, got {value!r}")
    if audit["affected_count"] != len(deficits):
        raise BoundaryLedgerError(
            f"audit claims {audit['affected_count']} affected vehicles but "
            f"carries {len(deficits)} deficits")
    recomputed_total = normalize_time_loss(sum(deficits.values()))
    if normalize_time_loss(audit["deficit_total_s"]) != recomputed_total:
        raise BoundaryLedgerError(
            f"audit deficit total {audit['deficit_total_s']} does not recompute "
            f"from its own deficits ({recomputed_total})")
    preserved = audit["preserved_count"]
    if isinstance(preserved, bool) or not isinstance(preserved, int) \
            or preserved < 0:
        raise BoundaryLedgerError("preserved_count must be a non-negative int")
    recomputed = hashlib.sha256(_canonical(
        {k: v for k, v in audit.items() if k != "digest"})).hexdigest()
    if recomputed != audit["digest"]:
        raise BoundaryLedgerError(
            "restore-audit digest does not recompute; the evidence was altered")
    return dict(audit)


def capture_final_ledger(connection, vehicle_ids, *, warm_point_s: int,
                         terminal_time_s: int, audit_digest: str) -> dict:
    """Capture unrounded terminal accumulators for exactly the affected IDs.

    SUMO tripinfo has already rounded each value for serialization. Starting a
    correction from that file and rounding again is not equivalent to rounding
    the uninterrupted whole value once. The retained TraCI objects are the only
    source of the unrounded terminal accumulator, so every deficit-bearing ID
    must still be readable at the exact terminal instant.
    """
    if isinstance(terminal_time_s, bool) or not isinstance(terminal_time_s, int) \
            or terminal_time_s <= warm_point_s:
        raise BoundaryLedgerError(
            "terminal_time_s must be an integer after the warm point")
    now = connection.simulation.getTime()
    if isinstance(now, bool) or not isinstance(now, (int, float)) \
            or float(now) != float(terminal_time_s):
        raise BoundaryLedgerError(
            f"final ledger: simulation time {now!r} is not terminal time "
            f"{terminal_time_s}")
    if not isinstance(audit_digest, str) or len(audit_digest) != 64:
        raise BoundaryLedgerError("final ledger requires the restore-audit digest")
    requested = list(vehicle_ids)
    if any(not isinstance(vehicle, str) or not vehicle for vehicle in requested):
        raise BoundaryLedgerError("final ledger vehicle ids must be non-empty strings")
    if len(set(requested)) != len(requested):
        raise BoundaryLedgerError("final ledger vehicle ids must be unique")
    available = list(connection.vehicle.getIDList())
    if len(set(available)) != len(available):
        raise BoundaryLedgerError("final TraCI vehicle list contains duplicates")
    missing = sorted(set(requested) - set(available))
    if missing:
        raise BoundaryLedgerError(
            f"deficit vehicles are unavailable at terminal time: {missing[:8]}; "
            f"--keep-after-arrival did not retain exact coverage")
    entries = {
        vehicle: check_time_loss(connection.vehicle.getTimeLoss(vehicle))
        for vehicle in sorted(requested)
    }
    ledger = {
        "schema": FINAL_LEDGER_SCHEMA,
        "warm_point_s": int(warm_point_s),
        "terminal_time_s": int(terminal_time_s),
        "audit_digest": audit_digest,
        "entries": entries,
    }
    ledger["digest"] = hashlib.sha256(_canonical(ledger)).hexdigest()
    return ledger


def validate_final_ledger(ledger, audit, *, expected_warm_point_s=None,
                          expected_terminal_time_s=None) -> dict:
    """Validate exact final coverage and binding to one restore audit."""
    audit = validate_restore_audit(
        audit, expected_warm_point_s=expected_warm_point_s)
    if not isinstance(ledger, Mapping) or set(ledger) != FINAL_LEDGER_FIELDS:
        raise BoundaryLedgerError("final ledger field set is wrong")
    if ledger.get("schema") != FINAL_LEDGER_SCHEMA:
        raise BoundaryLedgerError(
            f"unsupported final-ledger schema {ledger.get('schema')!r}")
    point = ledger["warm_point_s"]
    terminal = ledger["terminal_time_s"]
    if point != audit["warm_point_s"]:
        raise BoundaryLedgerError("final ledger warm point does not match audit")
    if expected_terminal_time_s is not None and terminal != expected_terminal_time_s:
        raise BoundaryLedgerError(
            f"final ledger terminal time {terminal} does not match expected "
            f"{expected_terminal_time_s}")
    if isinstance(terminal, bool) or not isinstance(terminal, int) \
            or terminal <= point:
        raise BoundaryLedgerError("final ledger terminal time is invalid")
    if ledger["audit_digest"] != audit["digest"]:
        raise BoundaryLedgerError("final ledger is bound to another restore audit")
    entries = ledger["entries"]
    if not isinstance(entries, Mapping) or set(entries) != set(audit["deficits"]):
        raise BoundaryLedgerError(
            "final ledger does not cover exactly the deficit-bearing vehicles")
    for vehicle, value in entries.items():
        if not isinstance(vehicle, str) or not vehicle:
            raise BoundaryLedgerError("final ledger has an invalid vehicle id")
        check_time_loss(value)
    recomputed = hashlib.sha256(_canonical(
        {key: value for key, value in ledger.items() if key != "digest"}
    )).hexdigest()
    if recomputed != ledger["digest"]:
        raise BoundaryLedgerError(
            "final ledger digest does not recompute; the evidence was altered")
    return dict(ledger)


def apply_restore_deficits(resumed_records, final_ledger, audit, *,
                           precision: int = TRIPINFO_PRECISION,
                           expected_warm_point_s=None,
                           expected_terminal_time_s=None) -> dict:
    """Correct each affected whole value from its UNROUNDED TraCI reading.

    `resumed_records` is `{vehicle_id: time_loss}` from the resumed arm's final
    tripinfo. Every vehicle carrying a deficit must appear there — a deficit
    with no final record cannot be applied, and silently dropping it would
    reintroduce exactly the loss this exists to repair.

    The raw tripinfo map supplies identity coverage and every unaffected value.
    It is never the numeric starting point for an affected vehicle: those begin
    with the unrounded terminal ledger and are rounded once only after their
    measured deficit is added. Raw SUMO output files are never rewritten.
    """
    audit = validate_restore_audit(audit,
                                   expected_warm_point_s=expected_warm_point_s)
    final_ledger = validate_final_ledger(
        final_ledger, audit, expected_warm_point_s=expected_warm_point_s,
        expected_terminal_time_s=expected_terminal_time_s)
    if not isinstance(resumed_records, Mapping):
        raise BoundaryLedgerError("resumed records must be an object")

    deficits = audit["deficits"]
    absent = sorted(v for v in deficits if v not in resumed_records)
    if absent:
        raise BoundaryLedgerError(
            f"deficits reference vehicles with no resumed tripinfo record: "
            f"{absent[:8]}; the correction cannot be applied and the loss "
            f"cannot be silently dropped")

    corrected: dict[str, float] = {}
    for vehicle, value in resumed_records.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise BoundaryLedgerError(
                f"resumed record for {vehicle!r} is not finite: {value!r}")
        if vehicle in deficits:
            # ONE application to the UNROUNDED whole resumed accumulator,
            # normalised ONCE at the reporting precision.
            corrected[vehicle] = normalize_time_loss(
                float(final_ledger["entries"][vehicle])
                + float(deficits[vehicle]), precision)
        else:
            corrected[vehicle] = float(value)

    raw_total = sum(float(value) for value in resumed_records.values())
    corrected_total = sum(corrected.values())
    return {
        "schema": RESTORE_CORRECTION_SCHEMA,
        "records": corrected,
        "applied_count": len(deficits),
        "deficit_total_s": audit["deficit_total_s"],
        "raw_total_time_loss_s": raw_total,
        "corrected_total_time_loss_s": corrected_total,
        "correction_delta_s": corrected_total - raw_total,
        "audit_digest": audit["digest"],
        "save_ledger_digest": audit["saved_digest"],
        "restored_ledger_digest": audit["restored_digest"],
        "final_ledger_digest": final_ledger["digest"],
        "unchanged_count": len(corrected) - len(deficits),
        "tripinfo_precision": precision,
    }


# ── Preserved-accumulator aggregation ────────────────────────────────────────

def aggregate_split_time_loss(*, completed_prefix_total,
                              completed_prefix_trips, resumed_total,
                              resumed_trips,
                              continued_total=None,
                              precision: int = TRIPINFO_PRECISION) -> dict:
    """Add the two segment aggregates. NO boundary offset is ever applied.

    This is the whole objective rule under preserved-accumulator accounting. A
    vehicle that finished before the snapshot is counted once, in the prefix
    aggregate. A vehicle still driving at the snapshot is absent from the
    completed-only prefix and counted once in the resumed aggregate — with its
    FULL time loss, because the saved state preserved its accumulator. Adding a
    pre-boundary offset here, as v3 did, would count that vehicle's early delay
    twice.
    """
    if continued_total is None:
        prefix_total = normalize_time_loss(completed_prefix_total, precision)
        post_total = normalize_time_loss(resumed_total, precision)
    else:
        # These totals are still validated even though the uninterrupted result
        # comes from the continued accumulator state rather than their grouped
        # addition.
        prefix_total = check_time_loss(completed_prefix_total)
        post_total = check_time_loss(resumed_total)
    for name, value in (("completed_prefix_trips", completed_prefix_trips),
                        ("resumed_trips", resumed_trips)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BoundaryLedgerError(
                f"{name} must be a non-negative integer, got {value!r}")
    total = (normalize_time_loss(prefix_total + post_total, precision)
             if continued_total is None else check_time_loss(continued_total))
    return {
        "total_time_loss_s": total,
        "trip_count": completed_prefix_trips + resumed_trips,
        "completed_prefix_trips": completed_prefix_trips,
        "resumed_trips": resumed_trips,
        # Recorded so a reviewer can see the refuted rule is absent, rather than
        # having to prove a negative by reading the code.
        "boundary_offset_applied": False,
        "tripinfo_precision": precision,
    }


def parse_tripinfo_time_loss(path) -> dict[str, float]:
    """`{vehicle_id: timeLoss}` from a tripinfo file, keyed strictly by id.

    Retained for DIAGNOSTIC use only — production accounting reads aggregates,
    not per-vehicle maps, so nothing in the objective depends on this. A
    duplicate id is refused rather than resolved: silently keeping one of two
    records would drop real delay with no signal.
    """
    out: dict[str, float] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        # The TAG BOUNDARY matters: SUMO wraps records in a `<tripinfos>`
        # container, and a bare prefix test matches that opening tag too.
        if _TRIPINFO_START_RE.match(stripped) is None:
            continue
        match = _TRIPINFO_RE.search(stripped)
        if match is None:
            raise BoundaryLedgerError(
                f"tripinfo record lacks id/timeLoss in {Path(path).name}")
        vehicle, raw = match.group(1), match.group(2)
        if not vehicle:
            raise BoundaryLedgerError("tripinfo record has an empty vehicle id")
        if vehicle in out:
            raise BoundaryLedgerError(
                f"duplicate tripinfo vehicle id {vehicle!r} in "
                f"{Path(path).name}")
        try:
            value = float(raw)
        except ValueError:
            raise BoundaryLedgerError(
                f"tripinfo timeLoss {raw!r} for {vehicle!r} is not a number")
        out[vehicle] = check_time_loss(value)
    return out


# ── The controller ───────────────────────────────────────────────────────────

class WarmPrefixController:
    """Owns ONE SUMO process and its TraCI connection for the prefix run.

    The state and the facts about it must come from the SAME process at the SAME
    step. A batch `--save-state` run cannot provide that: once it exits there is
    nothing left to ask, and a separately-opened connection is a DIFFERENT
    simulation.

    `traci`, `subprocess` and `socket` are imported LAZILY inside the methods, so
    constructing this object starts nothing and imports no simulator — which is
    what lets the harness hold one unconditionally while process-free checks stay
    process-free.
    """

    def __init__(self, *, traci_module=None, launcher=None, port=None,
                 run_budget_s=900, clock=None, sleep=None, spawner=None):
        self._traci_module = traci_module
        self._launcher = launcher
        self._port = port
        self._run_budget_s = run_budget_s
        # Injected so the time budget is testable without waiting for it. A
        # deadline nothing can advance is a deadline nothing can prove.
        self._clock = clock
        self._sleep = sleep
        self._spawner = spawner

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        import time                                    # noqa: PLC0415 — lazy
        return time.monotonic()

    def _spawn(self, target):
        """Start the blocking phase on a worker thread.

        Injected so the wall-clock bound is testable without a real hang.
        Threads are not processes: nothing here starts a subprocess, opens a
        socket or imports a simulator.
        """
        if self._spawner is not None:
            return self._spawner(target)
        import threading                               # noqa: PLC0415 — lazy
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        return worker

    def _pause(self, seconds) -> None:
        if self._sleep is not None:
            self._sleep(seconds)
            return
        import time                                    # noqa: PLC0415 — lazy
        time.sleep(seconds)

    def _traci(self, home):
        """Resolve TraCI from EXACTLY this SUMO home.

        v6 failed with `No module named 'traci'` on every identity because this
        was a bare `import traci`: the package ships inside the installation at
        `<home>/tools/traci` and is not on `sys.path`. The shared resolver also
        PROVES the module came from that home, so a stray package elsewhere on
        the path cannot silently stand in for the SUMO we are about to run.
        """
        if self._traci_module is not None:
            return self._traci_module
        from traffic_sim.simulation.runtime import (   # noqa: PLC0415 — lazy
            resolve_traci)
        return resolve_traci(home)

    def _launch(self, cmd, *, cwd, home, port, stderr_path):
        if self._launcher is not None:
            return self._launcher(cmd, cwd=cwd, home=home, port=port,
                                  stderr_path=stderr_path)
        import subprocess                              # noqa: PLC0415 — lazy
        # stderr to a FILE, never a pipe: an unread pipe deadlocks a process that
        # writes more than the buffer holds.
        handle = open(stderr_path, "wb")
        try:
            return subprocess.Popen(
                [*cmd, "--remote-port", str(port)], cwd=str(cwd),
                env={"SUMO_HOME": str(home)},
                stdout=subprocess.DEVNULL, stderr=handle)
        finally:
            handle.close()

    def _free_port(self) -> int:
        if self._port is not None:
            return self._port
        import socket                                  # noqa: PLC0415 — lazy
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    @staticmethod
    def _stderr_tail(stderr_path, limit=1500) -> str:
        path = Path(stderr_path)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]

    def _reap(self, process, *, stderr_path, timeout=60, kill_timeout=30):
        """Wait, killing the process if it will not exit, and REPORT the code.

        BOTH waits are bounded. An unbounded second wait after `kill()` can hang
        forever on a process stuck in an uninterruptible state, which turns
        cleanup into the very hang the timeout exists to prevent.

        This never raises. It is called from a `finally`, so raising here would
        replace whatever real failure the body was already reporting; cleanup
        problems are RETURNED and surfaced only when there is no primary error.
        """
        killed = False
        cleanup_error = None
        code = None
        try:
            code = process.wait(timeout=timeout)
        except Exception:
            killed = True
            try:
                process.kill()
            except Exception as error:                 # already gone, or worse
                cleanup_error = f"kill failed: {error}"
            try:
                code = process.wait(timeout=kill_timeout)
            except Exception as error:
                cleanup_error = (f"the process did not exit within "
                                 f"{kill_timeout}s of being killed: {error}")
        if code is None:
            code = getattr(process, "returncode", None)
        try:
            stderr = self._stderr_tail(stderr_path)
        except Exception as error:                     # unreadable log
            stderr = f"<stderr unavailable: {error}>"
        return code, killed, stderr, cleanup_error

    def _traci_phase(self, *, traci, port, warm_point_s, state_path, settings,
                     deadline, step_chunk_s) -> dict:
        """Connect, advance to the warm point, capture and save. Blocking.

        Runs on a worker thread so the caller can enforce a WALL-CLOCK bound
        around the whole phase. The cooperative deadline checks below still help
        — they stop a slow-but-progressing run early with a precise message —
        but they cannot interrupt a call that has already blocked. That is the
        watchdog's job.

        `close()` HAPPENS HERE, not on the main thread. It is itself a TraCI
        operation and can block, so closing from the main thread put one call
        outside every timeout — and, if this worker had not unwound, ran it
        concurrently against the connection this thread is still using. Every
        TraCI touch belongs to one thread and one bound.
        """
        try:
            return self._connected_phase(
                traci=traci, port=port, warm_point_s=warm_point_s,
                state_path=state_path, settings=settings, deadline=deadline,
                step_chunk_s=step_chunk_s)
        finally:
            try:
                traci.close()
            except Exception:
                # An already-dead connection is expected after a failure. The
                # original error is the one worth reporting, so this never
                # raises over it.
                pass

    def _connected_phase(self, *, traci, port, warm_point_s, state_path,
                         settings, deadline, step_chunk_s) -> dict:
        """Connect, advance, capture, save. Closing is the caller's `finally`."""
        traci.init(port)
        target = float(warm_point_s)
        chunk = float(step_chunk_s)
        if chunk <= 0:
            raise BoundaryLedgerError("step_chunk_s must be positive")
        while True:
            now = float(traci.simulation.getTime())
            if now >= target:
                break
            if self._now() > deadline:
                raise BoundaryLedgerError(
                    f"the prefix run exceeded its time budget at simulation "
                    f"time {now}, short of the warm point {warm_point_s}; its "
                    f"state would describe an unknown instant")
            traci.simulationStep(min(now + chunk, target))
            if float(traci.simulation.getTime()) <= now:
                raise BoundaryLedgerError(
                    f"the simulation did not advance past {now}; it cannot "
                    f"reach the warm point {warm_point_s}")
        facts = capture_snapshot_facts(
            traci, warm_point_s=warm_point_s,
            save_state_rng=settings["save_state_rng"],
            save_state_precision=settings["save_state_precision"])
        # Membership comes from the SAME connection and instant that writes the
        # state. SUMO documents getTimeLoss as accumulated loss, but frozen 1.27
        # meso save/load diagnostics prove it is not an exact transport for the
        # private tripinfo accumulator across this boundary.
        active = capture_boundary_active(traci, warm_point_s=warm_point_s)
        traci.simulation.saveState(str(state_path))
        return {"facts": facts, "boundary_active": active}

    def _restore_phase(self, *, traci, port, warm_point_s, terminal_time_s,
                       saved_ledger, deadline) -> dict:
        """Connect to the resumed run, measure, then let it finish. Blocking.

        The measurement must happen BEFORE any resumed step. Once the simulation
        advances, an accumulator that was restored short is indistinguishable
        from one that simply accrued less delay afterwards, and the deficit
        becomes unrecoverable.

        `close()` happens in this thread's `finally`, for the same reason as the
        prefix phase: every TraCI touch belongs to one thread and one bound.
        """
        try:
            traci.init(port)
            now = float(traci.simulation.getTime())
            if now != float(warm_point_s):
                raise BoundaryLedgerError(
                    f"the resumed run reports simulation time {now}, not the "
                    f"warm point {warm_point_s}; it did not load the state this "
                    f"correction is measured against")
            audit = None
            if saved_ledger is not None:
                # Legacy diagnostic compatibility only. Production passes no
                # saved ledger because the documented TraCI value did not
                # reproduce the private tripinfo accumulator exactly through
                # the frozen SUMO 1.27 meso save/load boundary.
                restored = capture_restore_ledger(
                    traci, warm_point_s=warm_point_s)
                audit = build_restore_audit(
                    saved_ledger, restored, expected_warm_point_s=warm_point_s)
            if self._now() > deadline:
                raise BoundaryLedgerError(
                    "the resumed run exceeded its time budget before the "
                    "terminal advance")
            # ONE target-time advance. A Python/TraCI round trip per simulated
            # second would erase the speedup this path exists to provide.
            traci.simulationStep(float(terminal_time_s))
            if self._now() > deadline:
                raise BoundaryLedgerError(
                    "the resumed run exceeded its time budget during the "
                    "terminal advance")
            if audit is None:
                return {
                    "warm_point_s": warm_point_s,
                    "terminal_time_s": terminal_time_s,
                    "terminal_reached": float(traci.simulation.getTime())
                        == float(terminal_time_s),
                }
            final = capture_final_ledger(
                traci, audit["deficits"], warm_point_s=warm_point_s,
                terminal_time_s=terminal_time_s,
                audit_digest=audit["digest"])
            return {"restore_audit": audit, "final_ledger": final}
        finally:
            try:
                traci.close()
            except Exception:
                pass

    def run_resumed(self, *, cmd, cwd, home, warm_point_s, terminal_time_s,
                    saved_ledger=None,
                    connect_attempts=40, connect_poll_s=0.05,
                    kill_grace_s=30.0) -> dict:
        """Launch the unchanged post-state scenario and measure the restore.

        Same wall-clock machinery as `run_prefix`, and for the same reason:
        every TraCI call can block forever, so the phase runs on a worker thread
        whose join is the real bound, and the SUMO process is what gets killed to
        release a call that has already blocked.

        The scenario command is NOT modified here beyond the port the launcher
        appends. This measures the run the campaign would have made; changing it
        would measure something else.
        """
        validate_resumed_command(
            cmd, warm_point_s=warm_point_s,
            terminal_time_s=terminal_time_s,
            require_retention=saved_ledger is not None)
        if saved_ledger is not None:
            saved_ledger = validate_save_ledger(
                saved_ledger, expected_warm_point_s=warm_point_s)
        traci = self._traci(home)
        port = self._free_port()
        stderr_path = Path(cwd) / "resumed_stderr.log"
        process = self._launch(cmd, cwd=cwd, home=home, port=port,
                               stderr_path=stderr_path)
        deadline = self._now() + self._run_budget_s

        outcome: dict[str, Any] = {}
        timed_out = False
        try:
            for _ in range(connect_attempts):
                if process.poll() is None:
                    break
                self._pause(connect_poll_s)
            if process.poll() is not None:
                raise BoundaryLedgerError(
                    f"sumo exited with {process.poll()} before TraCI could "
                    f"connect to the resumed run: "
                    f"{self._stderr_tail(stderr_path)}")
            if self._now() > deadline:
                raise BoundaryLedgerError(
                    "the resumed run exhausted its time budget before TraCI "
                    "could be connected")

            def phase():
                try:
                    outcome["measurement"] = self._restore_phase(
                        traci=traci, port=port, warm_point_s=warm_point_s,
                        terminal_time_s=terminal_time_s,
                        saved_ledger=saved_ledger, deadline=deadline)
                except BaseException as error:         # reported, never lost
                    outcome["error"] = error

            worker = self._spawn(phase)
            worker.join(self._run_budget_s)
            if worker.is_alive():
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass
                worker.join(kill_grace_s)
            if "error" in outcome and not timed_out:
                raise outcome["error"]
            if timed_out:
                raise BoundaryLedgerError(
                    f"the resumed run exceeded its {self._run_budget_s}s budget "
                    f"inside a blocking TraCI call; the process was killed to "
                    f"release it, and its restore measurement cannot be trusted"
                    + ("" if not worker.is_alive() else
                       " (the worker did not unwind within "
                       f"{kill_grace_s}s)"))
        finally:
            code, killed, stderr, cleanup_error = self._reap(
                process, stderr_path=stderr_path)
        if cleanup_error is not None:
            raise BoundaryLedgerError(
                f"the resumed process could not be cleaned up ({cleanup_error}),"
                f" so the restore measurement cannot be trusted")
        if code != 0:
            raise BoundaryLedgerError(
                f"sumo exited with return code {code!r} after a normal resumed "
                f"run: {stderr}")
        if killed:
            raise BoundaryLedgerError(
                "sumo had to be killed during cleanup, so the restore "
                "measurement cannot be trusted")
        measurement = outcome["measurement"]
        if saved_ledger is None:
            if measurement != {
                    "warm_point_s": warm_point_s,
                    "terminal_time_s": terminal_time_s,
                    "terminal_reached": True}:
                raise BoundaryLedgerError(
                    "the resumed run did not reach its exact terminal instant")
            return measurement
        audit = validate_restore_audit(
            measurement["restore_audit"],
            expected_warm_point_s=warm_point_s)
        final = validate_final_ledger(
            measurement["final_ledger"], audit,
            expected_warm_point_s=warm_point_s,
            expected_terminal_time_s=terminal_time_s)
        return {"restore_audit": audit, "final_ledger": final}


    def run_prefix(self, *, cmd, cwd, home, warm_point_s, state_path,
                   connect_attempts=40, connect_poll_s=0.05,
                   step_chunk_s=60.0, kill_grace_s=30.0) -> dict:
        """Advance to the warm point, save the state, return bounded facts.

        The command must ALREADY carry the snapshot settings; that is validated
        here, so a caller that omitted them cannot produce a state the identity
        misdescribes.

        WALL-CLOCK BOUND. Every TraCI call here can block indefinitely —
        `init`, `getTime`, `simulationStep`, the snapshot queries and
        `saveState` — and no check placed around them can interrupt one that has
        already blocked. So the whole phase runs on a worker thread and the
        budget is enforced by joining that thread with a timeout. When it
        expires the only thing we own that can break the block is the SUMO
        PROCESS: killing it makes the pending socket call fail, which unwinds the
        worker. That is the mechanism, and it is why the process is killed before
        the thread is joined again.

        Any failure propagates: a warm prefix that cannot be captured must become
        a cold run, never a run with invented evidence.
        """
        settings = validate_snapshot_command(cmd)
        # BEFORE the launcher, the port, and any process: an unresolvable TraCI
        # must cost nothing. v6 launched SUMO three times and only then
        # discovered it could not talk to it.
        traci = self._traci(home)
        port = self._free_port()
        stderr_path = Path(cwd) / "prefix_stderr.log"
        process = self._launch(cmd, cwd=cwd, home=home, port=port,
                               stderr_path=stderr_path)
        deadline = self._now() + self._run_budget_s

        outcome: dict[str, Any] = {}
        timed_out = False
        try:
            # A SUMO that rejected its command line exits immediately, and
            # connecting to a dead process turns a bad argv into a hang.
            for _ in range(connect_attempts):
                if process.poll() is None:
                    break
                self._pause(connect_poll_s)
            if process.poll() is not None:
                raise BoundaryLedgerError(
                    f"sumo exited with {process.poll()} before TraCI could "
                    f"connect: {self._stderr_tail(stderr_path)}")

            # A cheap early-out, NOT the bound: if the budget is already gone
            # there is no point starting a phase we would immediately have to
            # kill. The wall-clock bound is the join/kill below, because that is
            # the only thing that can release a call which has already blocked.
            if self._now() > deadline:
                raise BoundaryLedgerError(
                    "the prefix run exhausted its time budget before TraCI "
                    "could be connected")

            def phase():
                try:
                    outcome["captured"] = self._traci_phase(
                        traci=traci, port=port, warm_point_s=warm_point_s,
                        state_path=state_path, settings=settings,
                        deadline=deadline, step_chunk_s=step_chunk_s)
                except BaseException as error:         # reported, never lost
                    outcome["error"] = error

            worker = self._spawn(phase)
            worker.join(self._run_budget_s)
            if worker.is_alive():
                timed_out = True
                # The blocked call cannot be cancelled; killing the process it is
                # talking to is what releases it.
                try:
                    process.kill()
                except Exception:
                    pass
                worker.join(kill_grace_s)
            if "error" in outcome and not timed_out:
                raise outcome["error"]
            if timed_out:
                raise BoundaryLedgerError(
                    f"the prefix run exceeded its {self._run_budget_s}s budget "
                    f"inside a blocking TraCI call; the process was killed to "
                    f"release it, and its state cannot be trusted"
                    + ("" if not worker.is_alive() else
                       " (the worker did not unwind within "
                       f"{kill_grace_s}s)"))
        finally:
            # The main thread owns ONLY the process, never the connection: the
            # worker closes TraCI inside the same bound. Touching the connection
            # here would put a blocking call outside every timeout and race a
            # worker that may still be using it.
            code, killed, stderr, cleanup_error = self._reap(
                process, stderr_path=stderr_path)
        # Only reached when the body completed normally, so a failure inside it
        # propagates untouched instead of being masked by an exit-code complaint
        # about a process we already knew had failed.
        if cleanup_error is not None:
            raise BoundaryLedgerError(
                f"the prefix process could not be cleaned up ({cleanup_error}), "
                f"so the snapshot cannot be trusted")
        if code != 0:
            raise BoundaryLedgerError(
                f"sumo exited with return code {code!r} after a normal prefix "
                f"completion; a run that stops producing output is not a run "
                f"that finished. stderr: {stderr}")
        if killed:
            raise BoundaryLedgerError(
                "sumo had to be killed during cleanup, so the snapshot cannot "
                "be trusted")
        captured = outcome["captured"]
        # Both bounded records are validated before leaving: snapshot settings
        # and exact population. The private accumulator is parsed only after
        # normal process exit finalizes unfinished tripinfo.
        return {
            "facts": validate_snapshot_facts(
                captured["facts"], expected_warm_point_s=warm_point_s),
            "boundary_active": validate_boundary_active(
                captured["boundary_active"],
                expected_warm_point_s=warm_point_s),
        }
