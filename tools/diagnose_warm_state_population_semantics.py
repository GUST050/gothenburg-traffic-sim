"""Find the third cause of the warm/cold objective residual, at population scale.

WHY THIS EXISTS. LUNA-WARM-16 ran the v9 paired campaign once. Warming executed
for the first time — all three identities reached `warm_completed` — and the
objective still differed from cold:

    q10 -7.73 s,  q50 -80.62 s,  q90 -138.97 s

Those are BIT-IDENTICAL to the residual v2 recorded before
`--save-state.rng true` and `--save-state.precision 16` were ever applied. The
state-serialization hypothesis is therefore refuted exactly as v9 pre-registered
it: those settings changed the residual by zero. A third cause remains, and three
properties of it are already measured and constrain any explanation:

  * the sign is always NEGATIVE — the warm arm reports LESS delay than cold;
  * the magnitude SCALES with demand (q10 < q50 < q90), which points at a
    per-vehicle effect summed over a population, not a one-off boundary event;
  * the relative size is tiny (0.001%-0.022%), which is the order of magnitude
    of rounding `timeLoss` to `TRIPINFO_PRECISION` decimals across ~86 000
    vehicles.

LUNA-WARM-08's diagnostic answered a different question with ONE vehicle, and
answered it correctly: the saved state preserves the accumulator. One vehicle
cannot exhibit a population-scaled residual at all, so it could not have seen
this. This diagnostic reproduces the same cold/prefix/resume boundary with MANY
vehicles spanning the three cohorts that a boundary actually creates, and adds
HIGH-PRECISION control arms — because the one measurement that separates
"reporting quantization" from "real save/load drift" is whether the deltas
survive when the output precision is raised.

WHAT THIS IS NOT. A synthetic fixture on one edge is a MECHANISM probe. It is
not equivalence evidence, not performance evidence, not adoption evidence, and
not grounds to enable warming. It establishes what the split does to a
population of accumulators under one controlled fixture, and nothing else.

FROZEN, UNAPPROVED, UNEXECUTED. Freezing the contract grants no permission to
preflight, import TraCI, run SUMO or create the outcome root. Any execution
requires a new Sol task/revision and exact user approval.

Usage:
  python3 tools/diagnose_warm_state_population_semantics.py --freeze
  python3 tools/diagnose_warm_state_population_semantics.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIAGNOSTIC_ID = "warm_state_population_semantics_v2"
SCHEMA = "warm_state_population_semantics_v2"
CONTRACT_PATH = "validation/warm_state_population_semantics_v2_contract.json"

# v1 is SPENT. Its one approved execution died on the first arm because its
# text-scanning parser matched SUMO's `<tripinfo-output …/>` configuration
# header. Its bytes are preserved exactly; the failure is history, not a bug to
# be edited away.
V1_CONTRACT_PATH = "validation/warm_state_population_semantics_v1_contract.json"
V1_CONTRACT_SHA256 = (
    "c02a9e64391b04430b13eb68630519cefe27056cdd6b1da01b1ef42334a539b2")

APPROVED_NETWORK = "sumo/net.net.xml"
# Where a LATER approved execution would write. Recorded so the contract names
# its own outcome location; this tool never creates or touches it.
APPROVED_OUTPUT_ROOT = "validation/warm_state_population_semantics_v2_outcome"

STATUS_FROZEN = "frozen_unapproved_unexecuted"

# ── Fixture geometry ─────────────────────────────────────────────────────────
STEP_LENGTH_S = 1.0
SEED = 1000
# The snapshot instant. Late enough that the fast cohort has finished and the
# slow cohort is unambiguously mid-trip.
SAVE_TIME_S = 30.0
END_TIME_S = 400.0
# SUMO's `--end` is EXCLUSIVE, and a state requested exactly at `--end` is never
# written. Production says so directly (monthly_sumo.py: "--end is exclusive and
# requires save_state_time_s < end, so flush_s=1 is the smallest legal value
# that still writes the state") and this diagnostic must obey the same rule, or
# the prefix arm would produce no snapshot at all and the experiment could not
# run. One step past the boundary, exactly as production does it.
PREFIX_FLUSH_S = 1.0

# The edge must be long enough that the slow cohort is still driving at the
# boundary, and short enough that the fast cohort has finished before it. That
# is a BAND, not a minimum — a lower bound alone selected a 1.6 km edge on which
# no cohort separation is possible.
MIN_EDGE_LENGTH_M = 200.0
MAX_EDGE_LENGTH_M = 400.0
# Two speeds on one edge give three cohorts without needing three routes: fast
# vehicles finish before the boundary, slow ones are still driving across it.
FAST_SPEED_MPS = 20.0
SLOW_SPEED_MPS = 2.0

# Population sizes. `active_at_boundary` is deliberately the largest cohort:
# a residual that scales with the number of vehicles IN FLIGHT at the boundary
# is precisely the shape the v9 campaign measured, and a fixture with one or two
# such vehicles could not distinguish it from noise.
COHORT_COMPLETED_BEFORE = 8
COHORT_ACTIVE_AT = 24
COHORT_DEPART_AFTER = 8

DEPART_INTERVAL_S = 1.0
DEPART_AFTER_START_S = 40.0

COHORTS = ("completed_before_boundary", "active_at_boundary",
           "depart_after_boundary")

# ── Precision arms ───────────────────────────────────────────────────────────
# Production reports `timeLoss` at this many decimals; the control arms raise it.
# Imported, never duplicated: if production's reporting precision changes, this
# diagnostic's contract must change with it.
from traffic_sim.simulation.warm_state_boundary import (          # noqa: E402
    TRIPINFO_PRECISION, snapshot_settings_arguments)

CONTROL_PRECISION = 12

# Production runs MESOSCOPIC with limited junction control (run_scenario.py). A
# fixture that omitted these would measure a different simulator core than the
# one whose residual is under investigation, so parity is part of the contract.
MESO_ARGUMENTS = ("--mesosim", "true",
                  "--meso-junction-control", "true",
                  "--meso-junction-control.limited", "true")

# Full tripinfo record, not just the objective field. A partition or cohort
# error shows up in departure and arrival long before it shows up in timeLoss.
TRIPINFO_FIELDS = ("depart", "arrival", "duration", "routeLength",
                   "waitingTime", "waitingCount", "timeLoss")

# Classification tolerances.
#
# One value rounded to `TRIPINFO_PRECISION` decimals can move by at most half an
# ulp. Summed over N vehicles the worst case is N * half_ulp, so a residual that
# fits inside that envelope is fully explained by reporting quantization and
# needs no other mechanism. Anything outside it is not.
QUANT_HALF_ULP_S = 0.5 * (10 ** -TRIPINFO_PRECISION)
# The observed delta is a DIFFERENCE OF TWO ROUNDED VALUES — the split report and
# the cold report are each rounded independently — so one vehicle can contribute
# up to TWO half-ULPs, not one. The conservative per-vehicle bound is therefore a
# full ULP. Using half of that would call a legitimately-explained residual
# unexplained, which is the wrong direction to be wrong in for a diagnostic whose
# job is to avoid inventing a mechanism.
QUANT_PER_VEHICLE_BOUND_S = 2 * QUANT_HALF_ULP_S
# What counts as "still there" once precision is raised. Far below the
# production ulp, far above float noise on values of order 1e2-1e3 s.
DRIFT_EPSILON_S = 1e-6

# Runner limits, frozen so an approved execution cannot quietly widen them.
RUN_TIMEOUT_S = 600
TRACI_RETRIES = 40

PARTITION_ERROR = "population_partition_error"
OUTPUT_QUANTIZATION = "output_quantization"
SAVE_LOAD_DRIFT = "save_load_integration_drift"
EXACT_AGREEMENT = "exact_agreement"
INCONCLUSIVE = "inconclusive"

CLASSIFICATIONS = (PARTITION_ERROR, OUTPUT_QUANTIZATION, SAVE_LOAD_DRIFT,
                   EXACT_AGREEMENT, INCONCLUSIVE)

ARMS = ("cold", "prefix", "resumed",
        "cold_control", "prefix_control", "resumed_control")

MEMBER_ORDER = ("contract.json", "population.rou.xml",
                "cold_tripinfo.xml", "prefix_tripinfo.xml",
                "resumed_tripinfo.xml", "cold_control_tripinfo.xml",
                "prefix_control_tripinfo.xml", "resumed_control_tripinfo.xml",
                "observations.json", "verdict.json")

# Production sources whose bytes change what this diagnostic is ABOUT. If the
# metric producers or the boundary combiner change, a stored verdict stops
# describing the live system and must not be reusable.
BOUND_SOURCES = (
    "traffic_sim/simulation/metrics.py",
    "traffic_sim/simulation/warm_state_boundary.py",
    "traffic_sim/simulation/monthly_warm_state.py",
    "traffic_sim/simulation/monthly_sumo.py",
    "run_scenario.py",
    "tools/diagnose_warm_state_population_semantics.py",
    "tests/test_warm_state_population_semantics.py",
)


class DiagnosticError(SystemExit):
    """Fail closed with an explicit reason."""


def canonical_bytes(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def canonical_key(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── Fixture ──────────────────────────────────────────────────────────────────
def select_probe_edge(network_path) -> dict:
    """Deterministically choose ONE passenger-capable non-internal edge.

    Lexicographically smallest qualifying edge id, so the same network always
    yields the same fixture and the choice cannot drift with parser or
    filesystem ordering. The candidate count is reported too, because "the only
    option" and "one of many" are different determinism claims.
    """
    text = Path(network_path).read_text(encoding="utf-8")
    candidates = []
    for match in re.finditer(r'<edge\b([^>]*)>(.*?)</edge>', text, re.S):
        attrs, body = match.group(1), match.group(2)
        if 'function="internal"' in attrs:
            continue
        identifier = re.search(r'id="([^"]+)"', attrs)
        lane = re.search(r'<lane\b[^>]*/>', body)
        if not identifier or not lane:
            continue
        lane = lane.group(0)
        length = re.search(r'length="([\d.]+)"', lane)
        speed = re.search(r'speed="([\d.]+)"', lane)
        if not (length and speed):
            continue
        disallow = re.search(r'disallow="([^"]*)"', lane)
        allow = re.search(r'allow="([^"]*)"', lane)
        if disallow and "passenger" in disallow.group(1).split():
            continue
        if allow and "passenger" not in allow.group(1).split():
            continue
        if not (MIN_EDGE_LENGTH_M <= float(length.group(1)) <= MAX_EDGE_LENGTH_M):
            continue
        if float(speed.group(1)) <= FAST_SPEED_MPS:
            continue
        candidates.append((identifier.group(1), float(length.group(1)),
                           float(speed.group(1))))
    if not candidates:
        raise DiagnosticError(
            f"no passenger-capable non-internal edge between "
            f"{MIN_EDGE_LENGTH_M} m and {MAX_EDGE_LENGTH_M} m with a limit "
            f"above {FAST_SPEED_MPS} m/s exists in {network_path}; the "
            f"fixture cannot be constructed")
    candidates.sort(key=lambda item: item[0])
    edge_id, length_m, speed_mps = candidates[0]

    fast_travel_s = length_m / FAST_SPEED_MPS
    slow_travel_s = length_m / SLOW_SPEED_MPS
    # The cohorts must be cohorts BY CONSTRUCTION, not by luck.
    last_fast_depart = (COHORT_COMPLETED_BEFORE - 1) * DEPART_INTERVAL_S
    if last_fast_depart + fast_travel_s >= SAVE_TIME_S:
        raise DiagnosticError(
            f"fast cohort would still be driving at the {SAVE_TIME_S} s "
            f"boundary ({last_fast_depart + fast_travel_s:.1f} s); it must "
            f"complete before it")
    if slow_travel_s <= SAVE_TIME_S:
        raise DiagnosticError(
            f"slow cohort finishes in {slow_travel_s:.1f} s, before the "
            f"{SAVE_TIME_S} s boundary; no vehicle would be active across it")
    last_slow_depart = (COHORT_ACTIVE_AT - 1) * DEPART_INTERVAL_S
    if last_slow_depart >= SAVE_TIME_S:
        raise DiagnosticError(
            f"slow cohort's last departure {last_slow_depart:.1f} s is not "
            f"before the {SAVE_TIME_S} s boundary")
    if last_slow_depart + slow_travel_s >= END_TIME_S:
        raise DiagnosticError(
            f"slow cohort would not finish before {END_TIME_S} s; unfinished "
            f"trips are excluded from tripinfo and the partition would break")
    return {
        "edge_id": edge_id,
        "length_m": round(length_m, 3),
        "speed_limit_mps": round(speed_mps, 3),
        "fast_travel_s": round(fast_travel_s, 3),
        "slow_travel_s": round(slow_travel_s, 3),
        "selection_rule": ("lexicographically smallest non-internal "
                           "passenger-capable edge between "
                           f"{MIN_EDGE_LENGTH_M} m and {MAX_EDGE_LENGTH_M} m "
                           f"with a limit above {FAST_SPEED_MPS} m/s"),
        "candidate_count": len(candidates),
    }


def build_population() -> list[dict]:
    """The frozen vehicle roster: identity, cohort, departure, speed.

    Deterministic and total — every vehicle belongs to exactly one cohort, and
    the cohort is a property of the fixture rather than something inferred from
    a result afterwards. Inferring it afterwards is how a partition error hides.
    """
    roster = []
    for index in range(COHORT_COMPLETED_BEFORE):
        roster.append({"id": f"pre-{index:03d}",
                       "cohort": "completed_before_boundary",
                       "depart_s": round(index * DEPART_INTERVAL_S, 2),
                       "max_speed_mps": FAST_SPEED_MPS})
    for index in range(COHORT_ACTIVE_AT):
        roster.append({"id": f"act-{index:03d}",
                       "cohort": "active_at_boundary",
                       "depart_s": round(index * DEPART_INTERVAL_S, 2),
                       "max_speed_mps": SLOW_SPEED_MPS})
    for index in range(COHORT_DEPART_AFTER):
        roster.append({"id": f"post-{index:03d}",
                       "cohort": "depart_after_boundary",
                       "depart_s": round(
                           DEPART_AFTER_START_S + index * DEPART_INTERVAL_S, 2),
                       "max_speed_mps": FAST_SPEED_MPS})
    return roster


def cohort_index(population: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {v["id"]: v["cohort"] for v in population}


def build_route_xml(edge_id: str, population: Sequence[Mapping[str, Any]]) -> str:
    """The synthetic route file. Two vTypes, one edge, frozen departures."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<routes>',
             f'    <vType id="fast" maxSpeed="{FAST_SPEED_MPS:.2f}"/>',
             f'    <vType id="slow" maxSpeed="{SLOW_SPEED_MPS:.2f}"/>']
    for vehicle in population:
        kind = "fast" if vehicle["max_speed_mps"] == FAST_SPEED_MPS else "slow"
        lines.append(
            f'    <vehicle id="{vehicle["id"]}" type="{kind}" '
            f'depart="{vehicle["depart_s"]:.2f}">')
        lines.append(f'        <route edges="{edge_id}"/>')
        lines.append('    </vehicle>')
    lines += ['</routes>', '']
    return "\n".join(lines)


# ── Command shapes ───────────────────────────────────────────────────────────
def build_command(*, sumo_binary, network_path, route_path, tripinfo_path,
                  begin_s=0.0, end_s=END_TIME_S, save_state_at=None,
                  controller_owned=False, remote_port=None,
                  load_state_path=None, precision=None,
                  write_unfinished=True) -> list[str]:
    """The argv for one arm. Pure: builds nothing on disk and runs nothing.

    Mirrors production semantics deliberately, not incidentally:

    * MESOSCOPIC with limited junction control, because that is the simulator
      core whose residual is under investigation;
    * `--tripinfo-output.write-unfinished` TRUE by default and false only for a
      prefix arm — production's exact rule. A cold or resumed arm that silently
      dropped an unfinished vehicle would break the partition invisibly instead
      of failing loudly, which is the one failure mode this diagnostic must not
      have;
    * a save instant strictly BEFORE `--end`, because SUMO's end is exclusive
      and a state requested at it is never written;
    * for the controller-owned prefix arms, `--remote-port` so SUMO actually
      opens a TraCI server, and snapshot SETTINGS without CLI save scheduling,
      because the controller writes the state exactly once.

    Arms may differ ONLY in begin, end, save/load state, precision, output path
    and the prefix-only unfinished rule. Anything else would mean the comparison
    measured a flag rather than the boundary.
    """
    if controller_owned:
        if remote_port is None:
            raise DiagnosticError(
                "a controller-owned arm needs a --remote-port; without it SUMO "
                "opens no TraCI server and the connection can never be made")
        if save_state_at is None:
            raise DiagnosticError(
                "a controller-owned arm must declare its boundary instant")
    if save_state_at is not None:
        if float(save_state_at) >= float(end_s):
            raise DiagnosticError(
                f"save instant {save_state_at} is not strictly before --end "
                f"{end_s}; SUMO's end is exclusive and the state would never "
                f"be written")
    cmd = [
        str(sumo_binary),
        "--net-file", str(network_path),
        "--route-files", str(route_path),
        *MESO_ARGUMENTS,
        "--begin", f"{float(begin_s):.2f}",
        "--end", f"{float(end_s):.2f}",
        "--step-length", f"{STEP_LENGTH_S:.2f}",
        "--seed", str(SEED),
        "--tripinfo-output", str(tripinfo_path),
        "--tripinfo-output.write-unfinished",
        "true" if write_unfinished else "false",
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--time-to-teleport", "-1",
        "--random", "false",
    ]
    if precision is not None:
        cmd += ["--precision", str(int(precision))]
    if controller_owned:
        # Snapshot SETTINGS only — never `--save-state.times/.files`. The state
        # is written ONCE, by the connected controller at the boundary. CLI
        # scheduling would save a second time at the same instant and, worse,
        # would let the batch own a lifecycle the controller is supposed to own.
        # This mirrors production, whose prefix command carries the settings and
        # no schedule.
        cmd += [*snapshot_settings_arguments(),
                "--remote-port", str(remote_port)]
    if load_state_path is not None:
        cmd += ["--load-state", str(load_state_path)]
    return cmd


def arm_specifications() -> dict[str, dict]:
    """What each arm is for, frozen so a result cannot be re-labelled later.

    The prefix arms end ONE STEP past the boundary (production's `flush_s=1`),
    which is the smallest legal window that still writes the snapshot, and they
    are the only arms that exclude unfinished trips — a vehicle still driving at
    the boundary continues in the resumed arm, so counting it in both would
    count one vehicle twice.
    """
    prefix_end = SAVE_TIME_S + PREFIX_FLUSH_S
    return {
        "cold": {
            "role": "uninterrupted reference at production precision",
            "begin_s": 0.0, "end_s": END_TIME_S,
            "saves_state": False, "loads_state": False,
            "write_unfinished": True, "precision": None},
        "prefix": {
            "role": "runs to the boundary and captures the snapshot",
            "begin_s": 0.0, "end_s": prefix_end,
            "saves_state": True, "loads_state": False,
            "write_unfinished": False, "precision": None},
        "resumed": {
            "role": "loads the snapshot and finishes the run",
            "begin_s": SAVE_TIME_S, "end_s": END_TIME_S,
            "saves_state": False, "loads_state": True,
            "write_unfinished": True, "precision": None},
        "cold_control": {
            "role": ("the cold arm at raised precision; isolates reporting "
                     "quantization from a real difference"),
            "begin_s": 0.0, "end_s": END_TIME_S,
            "saves_state": False, "loads_state": False,
            "write_unfinished": True, "precision": CONTROL_PRECISION},
        "prefix_control": {
            "role": "prefix at raised precision, feeding resumed_control",
            "begin_s": 0.0, "end_s": prefix_end,
            "saves_state": True, "loads_state": False,
            "write_unfinished": False, "precision": CONTROL_PRECISION},
        "resumed_control": {
            "role": ("the resumed arm at raised precision; if deltas survive "
                     "here the cause is not rounding"),
            "begin_s": SAVE_TIME_S, "end_s": END_TIME_S,
            "saves_state": False, "loads_state": True,
            "write_unfinished": True, "precision": CONTROL_PRECISION},
    }


# Symbolic paths, so the frozen argv is an EXACT executable shape rather than
# prose about one. A later approved run substitutes real paths and must produce
# argv identical to this modulo these placeholders.
PLACEHOLDERS = {
    "sumo_binary": "<sumo>", "network_path": "<net.net.xml>",
    "route_path": "<population.rou.xml>", "load_state_path": "<state.xml.gz>",
    # Allocated per run by binding to port 0, exactly as the production
    # controller does, so two runs can never collide on a fixed port.
    "remote_port": "<free-port>",
}


def frozen_command(arm: str) -> list[str]:
    """The exact argv for one arm, with symbolic paths. Pure."""
    specs = arm_specifications()
    if arm not in specs:
        raise DiagnosticError(f"unknown arm {arm!r}")
    spec = specs[arm]
    return build_command(
        sumo_binary=PLACEHOLDERS["sumo_binary"],
        network_path=PLACEHOLDERS["network_path"],
        route_path=PLACEHOLDERS["route_path"],
        tripinfo_path=f"<{arm}_tripinfo.xml>",
        begin_s=spec["begin_s"], end_s=spec["end_s"],
        save_state_at=SAVE_TIME_S if spec["saves_state"] else None,
        controller_owned=spec["saves_state"],
        remote_port=(PLACEHOLDERS["remote_port"] if spec["saves_state"]
                     else None),
        load_state_path=(PLACEHOLDERS["load_state_path"]
                         if spec["loads_state"] else None),
        precision=spec["precision"],
        write_unfinished=spec["write_unfinished"])


def frozen_commands() -> dict[str, list[str]]:
    return {arm: frozen_command(arm) for arm in ARMS}


# ── Parsing ──────────────────────────────────────────────────────────────────
def parse_tripinfo(path) -> dict[str, dict]:
    """`{vehicle_id: {field: value}}` over the FULL record, failing closed.

    Parsed as XML, selecting elements whose tag is EXACTLY `tripinfo`.

    v1 scanned for the text `<tripinfo\b` and was spent by it. SUMO writes a
    configuration header as an XML COMMENT listing the options it ran with, and
    that header contains `<tripinfo-output value="..."/>` and
    `<tripinfo-output.write-unfinished value="true"/>`. A word boundary matches
    between "tripinfo" and "-", so both option tags scanned as records, neither
    had an `id`, and the run died on its very first arm. A text scan cannot see
    that those bytes are inside a comment, and cannot tell an element from a
    prefix of one; a parser does both for free.
    """
    import xml.etree.ElementTree as ET                  # noqa: PLC0415 — lazy
    try:
        tree = ET.parse(str(path))
    except ET.ParseError as error:
        raise DiagnosticError(f"{path}: malformed XML: {error}")

    out: dict[str, dict] = {}
    for element in tree.getroot().iter():
        # EXACT tag equality. `tripinfo-output` is a different element, and a
        # comment is not an element at all, so both disappear here.
        if element.tag != "tripinfo":
            continue
        identifier = element.get("id")
        if identifier is None:
            raise DiagnosticError(f"{path}: a tripinfo element has no id")
        if identifier in out:
            raise DiagnosticError(
                f"{path}: duplicate tripinfo record for {identifier!r}")
        record: dict[str, Any] = {"id": identifier}
        for field in TRIPINFO_FIELDS:
            raw = element.get(field)
            if raw is None:
                raise DiagnosticError(
                    f"{path}: {identifier!r} has no {field!r}; the full "
                    f"tripinfo record is required, not just the objective")
            try:
                value = float(raw)
            except ValueError:
                raise DiagnosticError(
                    f"{path}: unparsable {field}={raw!r} for {identifier!r}")
            if value != value or value in (float("inf"), float("-inf")):
                raise DiagnosticError(
                    f"{path}: non-finite {field} for {identifier!r}")
            record[field] = value
        out[identifier] = record
    if not out:
        raise DiagnosticError(f"{path}: no tripinfo records")
    return out


def observed_cohort(record: Mapping[str, Any],
                    boundary_s: float = SAVE_TIME_S) -> str:
    """Which cohort a vehicle ACTUALLY landed in, from its own tripinfo.

    Declared cohorts are the fixture's INTENT. Under insertion queuing and
    car-following the intent can be wrong — a vehicle may be held at insertion
    and still be driving at a boundary it was meant to clear. Ideal
    length-over-speed arithmetic cannot see that; departure and arrival can.
    Membership is therefore RE-DERIVED from observation and compared with the
    intent, never assumed to match it.
    """
    depart = float(record["depart"])
    arrival = float(record["arrival"])
    if depart >= boundary_s:
        return "depart_after_boundary"
    if arrival < boundary_s:
        return "completed_before_boundary"
    return "active_at_boundary"


def observed_cohorts(records: Mapping[str, Mapping[str, Any]],
                     boundary_s: float = SAVE_TIME_S) -> dict[str, str]:
    return {k: observed_cohort(v, boundary_s) for k, v in records.items()}


# ── Comparison ───────────────────────────────────────────────────────────────
def compare_population(*, cold, prefix, resumed, declared_cohorts,
                       boundary_s=SAVE_TIME_S) -> dict:
    """Per-vehicle cold versus split over FULL records, partition checked FIRST.

    Under preserved-accumulator accounting each vehicle completes in exactly one
    split arm, so the split's vehicle set must partition the cold set exactly. A
    vehicle missing, duplicated across arms or unknown to the fixture is a
    partition error — and delta arithmetic over a broken partition means
    nothing, which is why it is checked before any total.

    Cohort membership is taken from OBSERVATION (`observed_cohort`) and
    compared against the fixture's declared intent. Disagreements are recorded,
    not silently resolved in favour of either.

    Totals are RECOMPUTED here from the per-vehicle records. An aggregate that
    arrives pre-summed cannot be audited.
    """
    overlap = sorted(set(prefix) & set(resumed))
    split = dict(prefix)
    split.update(resumed)
    missing = sorted(set(cold) - set(split))
    extra = sorted(set(split) - set(cold))
    unknown = sorted((set(cold) | set(split)) - set(declared_cohorts))

    observed = observed_cohorts(cold, boundary_s)

    # A union-preserving SWAP is not a partition. Two vehicles can trade arms
    # and leave every set identity intact while both are attributed to the wrong
    # side of the boundary — which is exactly the accounting error this
    # diagnostic exists to detect, so membership is enforced, not just counted.
    # A vehicle that COMPLETED before the boundary can only be reported by the
    # prefix; anything still driving at it, or departing after it, can only be
    # reported by the resumed arm.
    misplaced = []
    for identifier in sorted(set(cold) & (set(prefix) | set(resumed))):
        cohort = observed.get(identifier)
        actual = "prefix" if identifier in prefix else "resumed"
        expected = ("prefix" if cohort == "completed_before_boundary"
                    else "resumed")
        if cohort is not None and actual != expected:
            misplaced.append({"id": identifier, "observed_cohort": cohort,
                              "reported_by": actual, "expected_arm": expected})
    disagreements = [
        {"id": k, "declared": declared_cohorts.get(k), "observed": observed[k]}
        for k in sorted(observed)
        if declared_cohorts.get(k) != observed[k]]

    per_vehicle = []
    for identifier in sorted(set(cold) & set(split)):
        cold_record = cold[identifier]
        split_record = split[identifier]
        entry = {
            "id": identifier,
            "declared_cohort": declared_cohorts.get(identifier),
            "observed_cohort": observed.get(identifier),
            "arm": "prefix" if identifier in prefix else "resumed",
        }
        for field in TRIPINFO_FIELDS:
            entry[f"cold_{field}"] = cold_record[field]
            entry[f"split_{field}"] = split_record[field]
        entry["cold_time_loss_s"] = cold_record["timeLoss"]
        entry["split_time_loss_s"] = split_record["timeLoss"]
        entry["delta_s"] = split_record["timeLoss"] - cold_record["timeLoss"]
        per_vehicle.append(entry)

    cold_total = sum(cold[i]["timeLoss"] for i in sorted(cold))
    split_total = sum(split[i]["timeLoss"] for i in sorted(split))

    by_cohort: dict[str, dict] = {}
    for record in per_vehicle:
        bucket = by_cohort.setdefault(
            record["observed_cohort"] or "unknown",
            {"count": 0, "delta_total_s": 0.0, "max_abs_delta_s": 0.0})
        bucket["count"] += 1
        bucket["delta_total_s"] += record["delta_s"]
        bucket["max_abs_delta_s"] = max(bucket["max_abs_delta_s"],
                                        abs(record["delta_s"]))

    observed_counts = {c: sum(1 for v in observed.values() if v == c)
                       for c in COHORTS}
    return {
        "partition_ok": not (missing or extra or overlap or unknown
                             or misplaced),
        "misplaced_vehicles": misplaced,
        "missing_from_split": missing,
        "extra_in_split": extra,
        "duplicated_across_arms": overlap,
        "unknown_to_fixture": unknown,
        "cohort_disagreements": disagreements,
        "observed_cohort_counts": observed_counts,
        "all_cohorts_populated": all(observed_counts[c] > 0 for c in COHORTS),
        "vehicle_count": len(per_vehicle),
        "per_vehicle": per_vehicle,
        "cold_total_s": cold_total,
        "split_total_s": split_total,
        "total_delta_s": split_total - cold_total,
        "max_abs_delta_s": max((abs(r["delta_s"]) for r in per_vehicle),
                               default=0.0),
        "by_cohort": {k: v for k, v in sorted(by_cohort.items())},
    }


# ── Classification ───────────────────────────────────────────────────────────
def classify(production: Mapping[str, Any],
             control: Mapping[str, Any]) -> dict:
    """One mutually exclusive verdict, fail-closed by default.

    The decision tree, in order, because earlier questions invalidate later ones:

      1. Does the split partition the cold population exactly, in BOTH the
         production and control observations, with every cohort actually
         populated? If not, nothing downstream is interpretable.
      2. Do BOTH the production and the control show zero delta? Only then is
         there nothing to explain. Checking production alone was wrong:
         two-decimal reporting can round a real difference away, so "exact at
         production precision" while the control still disagrees is precisely
         the quantization case, not agreement.
      3. Do the deltas VANISH at raised precision, and does the production
         residual fit the vehicle-count-scaled rounding envelope? Then reporting
         quantization explains it completely.
      4. Do the deltas SURVIVE at raised precision AND exceed that envelope?
         Then rounding cannot explain it and the save/load integration differs.
      5. Anything else is INCONCLUSIVE, deliberately: "some of both" and
         "neither cleanly" are real outcomes and must not be rounded into
         whichever hypothesis was expected.
    """
    for name, block in (("production", production), ("control", control)):
        for field in ("partition_ok", "all_cohorts_populated", "total_delta_s",
                      "max_abs_delta_s", "vehicle_count"):
            if field not in block:
                raise DiagnosticError(
                    f"{name} observations are missing {field!r}")

    if not production["partition_ok"] or not control["partition_ok"]:
        return {
            "classification": PARTITION_ERROR,
            "why": ("the split arms do not partition the cold population "
                    "exactly, so per-vehicle deltas are not interpretable"),
            "recommendation": None,
        }
    if not production["all_cohorts_populated"] or \
            not control["all_cohorts_populated"]:
        return {
            "classification": PARTITION_ERROR,
            "why": ("at least one boundary cohort is empty in the OBSERVED "
                    "records, so the fixture did not exercise the boundary it "
                    "claims to and no verdict about it is interpretable"),
            "recommendation": None,
        }

    count = int(production["vehicle_count"])
    envelope_s = count * QUANT_PER_VEHICLE_BOUND_S
    production_residual = abs(float(production["total_delta_s"]))
    production_max = abs(float(production["max_abs_delta_s"]))
    control_residual = abs(float(control["total_delta_s"]))
    control_max = abs(float(control["max_abs_delta_s"]))

    control_clean = control_max <= DRIFT_EPSILON_S

    if production_residual == 0.0 and production_max == 0.0 and control_clean:
        return {
            "classification": EXACT_AGREEMENT,
            "why": ("every vehicle's split time loss equals its cold value at "
                    "production precision AND the raised-precision control "
                    "shows no surviving delta; there is no residual"),
            "recommendation": None,
        }

    if control_clean and production_residual <= envelope_s:
        return {
            "classification": OUTPUT_QUANTIZATION,
            "why": (f"deltas vanish at {CONTROL_PRECISION}-decimal precision "
                    f"and the production residual {production_residual:.6f} s "
                    f"fits inside the {count} x 2 half-ULP rounding envelope "
                    f"{envelope_s:.6f} s"),
            "recommendation": (
                "compare warm and cold objectives on values that have not been "
                "round-tripped through two-decimal tripinfo reporting, or "
                "compare with a tolerance derived from the reporting precision "
                "and the vehicle count. Do NOT 'fix' the simulation: on this "
                "evidence the underlying accumulators agree"),
        }
    if not control_clean and production_residual > envelope_s:
        return {
            "classification": SAVE_LOAD_DRIFT,
            "why": (f"deltas survive at {CONTROL_PRECISION}-decimal precision "
                    f"(max {control_max:.9f} s) and the production residual "
                    f"{production_residual:.6f} s exceeds the rounding "
                    f"envelope {envelope_s:.6f} s; rounding cannot explain it"),
            "recommendation": (
                "treat the save/load boundary as genuinely lossy for this "
                "fixture and locate the mechanism before any equivalence claim; "
                "warming must stay off"),
        }
    return {
        "classification": INCONCLUSIVE,
        "why": (f"production residual {production_residual:.6f} s, rounding "
                f"envelope {envelope_s:.6f} s, control residual "
                f"{control_residual:.6f} s, control max delta "
                f"{control_max:.9f} s: the observations do not uniquely "
                f"establish quantization or save/load drift"),
        "recommendation": None,
    }


# ── Approval-gated execution / result pipeline ───────────────────────────────
# COMPLETE, so a later approved run needs no source change — enabling it by
# editing this file would change a bound source and invalidate the very key that
# was approved. GATED, so it cannot run without the exact frozen contract key.
# Simulator imports are LAZY and happen only after the gate, so importing this
# module still touches nothing.
# Two mutually exclusive terminal schemas. A run ends as exactly one of them,
# and a failure artifact can never carry a verdict.
RESULT_SCHEMA = "warm_state_population_semantics_result_v2"
FAILURE_SCHEMA = "warm_state_population_semantics_failure_v2"

FAILURE_MEMBERS = ("contract.json", "population.rou.xml", "command_ledger.json",
                   "completed_arms.json", "error.json")

# WHERE a run died. Three distinct places, because each implies a different set
# of evidence and collapsing any two produces artifacts that contradict
# themselves.
#
#   arm_setup — preparing an arm (allocating its port, building its command).
#               Earlier arms are complete; THIS arm has no command, no raw file,
#               no exit and no boundary block, because it never started.
#   arm       — inside a running arm. It has a command but no parse.
#   post_arm  — every arm finished; the failure came later (comparison, result
#               validation, publication) and names no arm at all.
PHASE_ARM_SETUP = "arm_setup"
PHASE_ARM = "arm"
PHASE_POST_ARM = "post_arm"
FAILURE_PHASES = (PHASE_ARM_SETUP, PHASE_ARM, PHASE_POST_ARM)

REQUIRED_RESULT_KEYS = (
    "schema", "diagnostic_id", "contract_content_key", "arm_exit_status",
    "arm_records", "boundary_facts", "production", "control", "verdict")


class ApprovalRequired(DiagnosticError):
    """Raised when execution is attempted without the exact frozen key."""


def frozen_contract_key() -> str:
    path = ROOT / CONTRACT_PATH
    if not path.is_file():
        raise DiagnosticError(
            f"the frozen contract is missing: {CONTRACT_PATH}; nothing can be "
            f"approved against a contract that does not exist")
    return json.loads(path.read_text())["content_key"]


def require_approval(token) -> str:
    """The one gate. Execution needs the exact key of the frozen contract.

    A token bound to the CONTENT KEY means an approval can only ever authorize
    the exact fixture, commands and classifier someone reviewed; changing any of
    them changes the key and voids the approval by construction.
    """
    if token is None:
        raise ApprovalRequired(
            "this diagnostic is frozen, unapproved and unexecuted; execution "
            "requires a new Sol task/revision and exact user approval")
    stored = frozen_contract_key()
    if token != stored:
        raise ApprovalRequired(
            "approval token does not match the frozen contract key")
    return stored


def run_arm(*, cmd, cwd, timeout_s=RUN_TIMEOUT_S):
    """Run one batch arm. Imports the process module LAZILY, after the gate."""
    import subprocess                                   # noqa: PLC0415 — lazy
    completed = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                               text=True, timeout=timeout_s)
    return completed.returncode


def free_port() -> int:
    """A port nobody else holds, allocated the way production does it.

    Binding to port 0 lets the OS pick a free one; a fixed constant would make
    two concurrent runs collide and fail for a reason that has nothing to do
    with the experiment.
    """
    import socket                                       # noqa: PLC0415 — lazy
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def run_prefix_atomic(*, cmd, cwd, home, boundary_s, state_path, port,
                      timeout_s=RUN_TIMEOUT_S):
    """Capture, save and STOP at exactly one instant, like production does.

    This is the correction Sol's review forced, and it matters. A batch prefix
    that saves at the boundary but keeps simulating — even for a single step —
    can complete a vehicle in that step. That vehicle then appears in the prefix
    tripinfo AND is still present in the state resumed from the boundary, so it
    is counted twice and the partition silently breaks.

    Production does not rely on the exclusive `--end` for atomicity; it hands the
    command to a boundary controller that steps to the instant, saves, and closes
    in the same process. This does the same: `--end` stays legal (strictly after
    the save instant, or SUMO would never write the state at all), but the run is
    terminated AT the boundary by the controller rather than allowed to run on.
    """
    from traffic_sim.simulation.runtime import (        # noqa: PLC0415 — lazy
        resolve_traci, validate_traci_api)
    traci = resolve_traci(home)
    validate_traci_api(traci)
    import subprocess                                   # noqa: PLC0415 — lazy

    process = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        traci.init(port=int(port), numRetries=TRACI_RETRIES)
        while traci.simulation.getTime() < float(boundary_s):
            traci.simulationStep()
        active = list(traci.vehicle.getIDList())
        traci.simulation.saveState(str(state_path))
        captured_at = float(traci.simulation.getTime())
    finally:
        try:
            traci.close()
        except Exception:                               # noqa: BLE001
            pass
        try:
            process.wait(timeout=timeout_s)
        except Exception:                               # noqa: BLE001
            # Kill AND reap. Leaving a killed process unwaited leaves the
            # return code None, and the previous version mapped None to zero —
            # so a killed, incomplete arm satisfied the all-arms-exited-zero
            # gate. A killed arm is a failed arm and must say so.
            process.kill()
            try:
                process.wait(timeout=timeout_s)
            except Exception:                           # noqa: BLE001
                pass
    if process.returncode is None:
        raise DiagnosticError(
            "the prefix process never reported an exit status; a killed or "
            "unreaped arm must not be recorded as a normal exit")
    return {"captured_at_s": captured_at,
            "active_vehicle_ids": sorted(active),
            "active_vehicle_count": len(active),
            "returncode": int(process.returncode)}


def validate_boundary_facts(facts, cold_records) -> dict:
    """The snapshot must be EVIDENCED, not asserted.

    The previous revision discarded what the controller saw: a fake runner
    reporting zero vehicles in flight and writing no state still produced a
    published `exact_agreement`. A boundary nobody can show did not happen.

    Three things must hold for each controller-owned arm: the capture happened
    at the contracted instant; the vehicles the controller saw in flight are
    exactly those the cold records say were straddling the boundary; and a state
    file actually exists.
    """
    expected_active = sorted(
        k for k, v in observed_cohorts(cold_records).items()
        if v == "active_at_boundary")
    for arm in sorted(a for a in ARMS if arm_specifications()[a]["saves_state"]):
        block = facts.get(arm)
        if not isinstance(block, Mapping):
            raise DiagnosticError(f"no boundary facts recorded for {arm}")
        for field in ("captured_at_s", "active_vehicle_ids",
                      "active_vehicle_count", "state_sha256"):
            if field not in block:
                raise DiagnosticError(f"{arm} boundary facts lack {field!r}")
        if float(block["captured_at_s"]) != float(SAVE_TIME_S):
            raise DiagnosticError(
                f"{arm} captured at {block['captured_at_s']} s, not the "
                f"contracted boundary {SAVE_TIME_S} s")
        observed_ids = sorted(block["active_vehicle_ids"])
        if observed_ids != expected_active:
            raise DiagnosticError(
                f"{arm} saw {len(observed_ids)} vehicles in flight at the "
                f"boundary but the cold records show {len(expected_active)}; "
                f"the snapshot does not describe this population")
        if int(block["active_vehicle_count"]) != len(observed_ids):
            raise DiagnosticError(
                f"{arm} active count disagrees with its own id list")
        if not block["state_sha256"]:
            raise DiagnosticError(
                f"{arm} wrote no state file; there is nothing to resume from")
    return dict(facts)


def build_verdict(*, production, control, contract_content_key,
                  arm_exit_status, arm_records=None, boundary_facts=None) -> dict:
    """Assemble the terminal result from two observation blocks. Pure."""
    return {
        "schema": RESULT_SCHEMA,
        "diagnostic_id": DIAGNOSTIC_ID,
        "contract_content_key": contract_content_key,
        "arm_exit_status": dict(arm_exit_status),
        "arm_records": dict(arm_records or {}),
        "boundary_facts": dict(boundary_facts or {}),
        "production": production,
        "control": control,
        "verdict": classify(production, control),
    }


def validate_result(result: Mapping[str, Any],
                    contract_content_key=None) -> dict:
    """Refuse a result that does not carry — and RECOMPUTE to — what it claims.

    The verdict is re-derived from the observations and compared. A stored
    verdict is a conclusion, and a conclusion that is merely transcribed can be
    edited; one that must survive recomputation cannot. Sol's review proved the
    earlier version accepted a forged `save_load_integration_drift`.
    """
    for key in REQUIRED_RESULT_KEYS:
        if key not in result:
            raise DiagnosticError(f"result is missing {key!r}")
    if result["schema"] != RESULT_SCHEMA:
        raise DiagnosticError(
            f"result schema {result['schema']!r} is not {RESULT_SCHEMA!r}")
    if result["diagnostic_id"] != DIAGNOSTIC_ID:
        raise DiagnosticError(
            f"result identity {result['diagnostic_id']!r} is not "
            f"{DIAGNOSTIC_ID!r}")
    expected_key = (contract_content_key if contract_content_key is not None
                    else frozen_contract_key())
    if result["contract_content_key"] != expected_key:
        raise DiagnosticError(
            "result does not name the frozen contract it claims to describe")

    exits = result["arm_exit_status"]
    if not isinstance(exits, Mapping) or set(exits) != set(ARMS):
        raise DiagnosticError(
            f"every one of the {len(ARMS)} arms must record an exit status; "
            f"got {sorted(exits) if isinstance(exits, Mapping) else exits!r}")
    nonzero = sorted(k for k, v in exits.items() if v != 0)
    if nonzero:
        raise DiagnosticError(f"arms exited non-zero: {nonzero}")

    for block in ("production", "control"):
        observations = result[block]
        if not observations.get("per_vehicle"):
            raise DiagnosticError(
                f"{block} carries no per-vehicle records; an aggregate-only "
                f"claim is not auditable")
        recomputed = sum(r["split_time_loss_s"] - r["cold_time_loss_s"]
                         for r in observations["per_vehicle"])
        if abs(recomputed - float(observations["total_delta_s"])) > 1e-9:
            raise DiagnosticError(
                f"{block} total does not recompute from its per-vehicle "
                f"records ({recomputed} != {observations['total_delta_s']})")

    # Stored comparisons are re-derived from the RAW per-arm records whenever
    # they are present. Recomputing only the totals left the partition, cohort
    # and membership findings trusted — and those are the findings that decide
    # whether the deltas mean anything at all.
    raw = result["arm_records"]
    if not isinstance(raw, Mapping) or set(raw) != set(ARMS):
        raise DiagnosticError(
            f"arm_records must cover exactly the {len(ARMS)} arms; got "
            f"{sorted(raw) if isinstance(raw, Mapping) else raw!r}. An empty "
            f"map previously skipped reconstruction entirely")
    declared = cohort_index(build_population())
    for block, arms in (("production", ("cold", "prefix", "resumed")),
                        ("control", ("cold_control", "prefix_control",
                                     "resumed_control"))):
        if not all(arm in raw for arm in arms):
            raise DiagnosticError(
                f"{block} cannot be reconstructed: raw records missing for "
                f"{sorted(set(arms) - set(raw))}")
        rebuilt = compare_population(
            cold=raw[arms[0]], prefix=raw[arms[1]], resumed=raw[arms[2]],
            declared_cohorts=declared)
        # WHOLE-object equality, not a chosen subset. Comparing selected
        # summary fields leaves everything unlisted trusted, and the point
        # of reconstruction is that nothing is.
        stored = json.loads(json.dumps(result[block]))
        if json.loads(json.dumps(rebuilt)) != stored:
            differing = sorted(
                k for k in set(rebuilt) | set(stored)
                if json.loads(json.dumps(rebuilt.get(k)))
                != stored.get(k))
            raise DiagnosticError(
                f"{block} does not reconstruct from the raw arm records; "
                f"differing fields: {differing}")

    validate_boundary_facts(result["boundary_facts"],
                            result["arm_records"]["cold"])

    recomputed_verdict = classify(result["production"], result["control"])
    stored = result["verdict"]
    if stored.get("classification") != recomputed_verdict["classification"]:
        raise DiagnosticError(
            f"stored verdict {stored.get('classification')!r} does not "
            f"recompute from the observations "
            f"(recomputed {recomputed_verdict['classification']!r})")
    if stored.get("recommendation") != recomputed_verdict["recommendation"]:
        raise DiagnosticError(
            "stored recommendation does not recompute from the observations")
    return dict(result)


def verify_live_inputs() -> dict:
    """Prove the tree still IS what the frozen contract describes.

    Runs BEFORE any simulator import: an environment that has drifted must be
    refused while this is still a pure program. Checking afterwards would mean
    a process had already started against inputs nobody approved.
    """
    contract = json.loads((ROOT / CONTRACT_PATH).read_text())
    if canonical_key(contract) != contract["content_key"]:
        raise DiagnosticError("the frozen contract does not recompute")
    drifted = sorted(
        name for name, digest in contract["source_fingerprints"].items()
        if sha256_file(ROOT / name) != digest)
    if drifted:
        raise DiagnosticError(
            f"bound sources drifted since the contract was frozen: {drifted}")
    live_network = sha256_file(ROOT / APPROVED_NETWORK)
    if live_network != contract["network_requirement"]["sha256"]:
        raise DiagnosticError(
            f"the live network no longer matches the frozen contract "
            f"({live_network} != {contract['network_requirement']['sha256']})")
    return contract


def publish_failure(root, *, members: Mapping[str, bytes]) -> dict:
    """Preserve what a FAILED run knew, before the workspace disappears.

    v1's one approved execution failed inside a `TemporaryDirectory`, so the
    single file that would have explained it was deleted as the exception
    unwound — the diagnostic could not diagnose itself. The failure path now
    preserves more carefully than the success path, because the failure path is
    when the evidence is actually needed.
    """
    missing = [m for m in FAILURE_MEMBERS if m not in members]
    if missing:
        raise DiagnosticError(
            f"failure artifact is missing required members: {missing}")
    return _publish(root, members, required=FAILURE_MEMBERS)


def publish_outcome(root, members: Mapping[str, bytes]) -> dict:
    """All-or-nothing, no-clobber write of the SUCCESS artifact."""
    missing = [m for m in MEMBER_ORDER if m not in members and m != "members.json"]
    if missing:
        raise DiagnosticError(f"outcome is missing contracted members: {missing}")
    return _publish(root, members, required=MEMBER_ORDER)


def _publish(root, members: Mapping[str, bytes], *, required) -> dict:
    """Write every member into STAGING, verify it there, and rename LAST.

    The rename is the commit, and nothing may fail after it. An earlier version
    renamed first and verified after: a failing check left a success-shaped root
    on disk, and the outer handler then saw `root.exists()` and skipped failure
    preservation entirely — a failed run wearing a successful run's clothes.

    Members are BYTES. Reading a possibly-invalid file as text with
    `errors="replace"` and writing it back silently rewrites exactly the bytes a
    parser failure exists to explain.
    """
    root = Path(root)
    if root.exists():
        raise DiagnosticError(f"refusing to reuse an existing root: {root}")
    staging = root.with_name(root.name + ".partial")
    if staging.exists():
        raise DiagnosticError(f"refusing to reuse staging path: {staging}")
    staging.mkdir(parents=True)
    digests = {}
    try:
        ordered = [n for n in required if n in members]
        ordered += [n for n in sorted(members) if n not in required]
        for name in ordered:
            payload = members[name]
            if not isinstance(payload, (bytes, bytearray)):
                raise DiagnosticError(
                    f"member {name} must be bytes; a text round-trip would "
                    f"rewrite the evidence")
            target = staging / name
            target.write_bytes(bytes(payload))
            digests[name] = sha256_file(target)
        manifest = json.dumps(digests, indent=2, sort_keys=True) + "\n"
        (staging / "members.json").write_bytes(manifest.encode("utf-8"))

        for name, digest in digests.items():
            if sha256_file(staging / name) != digest:
                raise DiagnosticError(
                    f"staged member {name} does not match its digest")
        absent = [m for m in required if not (staging / m).is_file()]
        if absent:
            raise DiagnosticError(f"staged artifact is missing {absent}")
        stray = sorted(q.name for q in staging.iterdir()
                       if q.name != "members.json" and q.name not in digests)
        if stray:
            raise DiagnosticError(f"staged artifact has unlisted members: {stray}")

        staging.rename(root)               # the commit; nothing fails after it
    except BaseException:
        import shutil                                   # noqa: PLC0415 — lazy
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return digests


def normalize_argv(frozen: Sequence[str], actual: Sequence[str]) -> None:
    """Compare a recorded argv against its frozen shape.

    The frozen command carries `<placeholder>` tokens where a run substitutes a
    real path or an allocated port. Every OTHER token must match exactly and the
    lengths must agree, so a ledger cannot claim a command the contract never
    describes — `{"cold": "not-an-argv"}` previously validated.
    """
    if not isinstance(actual, list) or not all(
            isinstance(token, str) for token in actual):
        raise DiagnosticError(
            f"a ledger entry must be a list of argv strings, got "
            f"{type(actual).__name__}")
    if len(actual) != len(frozen):
        raise DiagnosticError(
            f"ledger argv has {len(actual)} tokens but the frozen command has "
            f"{len(frozen)}")
    for index, (expected, found) in enumerate(zip(frozen, actual)):
        placeholder = expected.startswith("<") and expected.endswith(">")
        if placeholder:
            if not found:
                raise DiagnosticError(
                    f"ledger argv token {index} is empty where the frozen "
                    f"command expects a substituted value")
            continue
        if expected != found:
            raise DiagnosticError(
                f"ledger argv token {index} is {found!r}, but the frozen "
                f"command has {expected!r}")


def validate_boundary_block(arm: str, block: Mapping[str, Any], *,
                            require_contracted_instant: bool = True) -> None:
    """The shape and internal consistency of one recorded snapshot.

    `require_contracted_instant` separates two different questions. On the
    SUCCESS path a capture at the wrong instant is invalid evidence and must be
    refused. On a FAILURE artifact it is the observation that EXPLAINS the
    failure, and refusing it would mean rejecting an artifact for faithfully
    recording why the run died — so there only the shape is enforced.
    """
    for field in ("captured_at_s", "active_vehicle_ids", "active_vehicle_count",
                  "state_sha256"):
        if field not in block:
            raise DiagnosticError(f"{arm} boundary facts lack {field!r}")
    try:
        captured = float(block["captured_at_s"])
    except (TypeError, ValueError):
        raise DiagnosticError(
            f"{arm} captured_at_s is not a number: {block['captured_at_s']!r}")
    if require_contracted_instant and captured != float(SAVE_TIME_S):
        raise DiagnosticError(
            f"{arm} captured at {block['captured_at_s']} s, not the contracted "
            f"boundary {SAVE_TIME_S} s")
    ids = block["active_vehicle_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise DiagnosticError(f"{arm} active_vehicle_ids must be a list of ids")
    if len(set(ids)) != len(ids):
        raise DiagnosticError(f"{arm} active_vehicle_ids contains duplicates")
    if int(block["active_vehicle_count"]) != len(ids):
        raise DiagnosticError(
            f"{arm} active count {block['active_vehicle_count']} disagrees "
            f"with its own {len(ids)} ids")
    digest = block["state_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        raise DiagnosticError(
            f"{arm} state_sha256 is not a sha256 digest: {digest!r}")


def failure_member_allowlist() -> set:
    """Exactly what a failure artifact may contain — DERIVED, not read.

    Computed from the contract's own member list and declared arms, so adding a
    digest cannot legitimise a file the contract never permitted.
    """
    return ({"members.json"} | set(FAILURE_MEMBERS)
            | {f"{arm}_tripinfo.xml" for arm in ARMS})


def validate_failure_artifact(root, *, contract_content_key=None) -> dict:
    """Refuse a failure artifact that does not carry — and recompute to — its
    own claims. A preserved failure nobody can trust is not preservation."""
    root = Path(root)
    if not root.is_dir():
        raise DiagnosticError(f"no failure artifact at {root}")
    manifest_path = root / "members.json"
    if not manifest_path.is_file():
        raise DiagnosticError("failure artifact has no digest manifest")
    digests = json.loads(manifest_path.read_text(encoding="utf-8"))

    missing = [m for m in FAILURE_MEMBERS if not (root / m).is_file()]
    if missing:
        raise DiagnosticError(f"failure artifact is missing {missing}")

    # The permitted set is DERIVED from the contract, never read from the
    # manifest. The previous version defined "unlisted" by the very file an
    # attacker would edit, so a `smuggled.json` passed simply by having its
    # digest added.
    allowed = failure_member_allowlist()
    present = {q.name for q in root.iterdir()}
    forbidden = sorted(present - allowed)
    if forbidden:
        raise DiagnosticError(
            f"failure artifact contains members the contract does not permit: "
            f"{forbidden}")
    signed = sorted(set(digests) - allowed)
    if signed:
        raise DiagnosticError(
            f"the digest manifest lists members the contract does not permit: "
            f"{signed}")
    unhashed = sorted(present - set(digests) - {"members.json"})
    if unhashed:
        raise DiagnosticError(f"members present but unhashed: {unhashed}")
    for name, digest in digests.items():
        if not (root / name).is_file():
            raise DiagnosticError(f"manifest names a missing member: {name}")
        if sha256_file(root / name) != digest:
            raise DiagnosticError(f"member {name} does not match its digest")

    result_members = sorted(present & {"verdict.json", "observations.json"})
    if result_members:
        raise DiagnosticError(
            f"a failure artifact must carry no result members: {result_members}")

    # The embedded contract must BE the frozen contract, recomputed — not merely
    # a well-formed JSON object with a fresh digest. `contract.json = {}`
    # previously validated.
    embedded = json.loads((root / "contract.json").read_text(encoding="utf-8"))
    if not isinstance(embedded, dict) or "content_key" not in embedded:
        raise DiagnosticError("the embedded contract is not a contract")
    if canonical_key(embedded) != embedded["content_key"]:
        raise DiagnosticError("the embedded contract does not recompute")
    expected_key = (contract_content_key if contract_content_key is not None
                    else frozen_contract_key())
    if embedded["content_key"] != expected_key:
        raise DiagnosticError(
            f"the embedded contract is not the one this artifact claims "
            f"({embedded['content_key']} != {expected_key})")

    # The fixture is DETERMINISTIC, so it can be regenerated and compared. A
    # forged route file previously validated.
    edge = embedded["fixture"]["probe_edge"]["edge_id"]
    expected_route = build_route_xml(edge, build_population())
    actual_route = (root / "population.rou.xml").read_text(encoding="utf-8")
    if actual_route != expected_route:
        raise DiagnosticError(
            "the preserved route file is not the contract's deterministic "
            "fixture")

    error = json.loads((root / "error.json").read_text(encoding="utf-8"))
    if error.get("schema") != FAILURE_SCHEMA:
        raise DiagnosticError(
            f"failure schema {error.get('schema')!r} is not {FAILURE_SCHEMA!r}")
    if error.get("diagnostic_id") != DIAGNOSTIC_ID:
        raise DiagnosticError("failure artifact has the wrong diagnostic id")
    expected = (contract_content_key if contract_content_key is not None
                else frozen_contract_key())
    if error.get("contract_content_key") != expected:
        raise DiagnosticError(
            "failure artifact does not name the frozen contract it describes")
    for field in ("error_type", "error_message"):
        if not error.get(field):
            raise DiagnosticError(f"failure artifact lacks {field!r}")
    phase = error.get("failure_phase")
    if phase not in FAILURE_PHASES:
        raise DiagnosticError(
            f"failure_phase {phase!r} is not one of {list(FAILURE_PHASES)}")
    if phase in (PHASE_ARM, PHASE_ARM_SETUP) and not error.get("failing_arm"):
        raise DiagnosticError(f"a {phase} failure must name its failing arm")
    if phase == PHASE_POST_ARM and error.get("failing_arm") is not None:
        raise DiagnosticError(
            "a post-arm failure must name NO failing arm; every arm completed")

    ledger = json.loads((root / "command_ledger.json").read_text(encoding="utf-8"))
    completed = json.loads(
        (root / "completed_arms.json").read_text(encoding="utf-8"))
    specs = arm_specifications()
    failing = error.get("failing_arm")
    if phase in (PHASE_ARM, PHASE_ARM_SETUP) and failing not in ARMS:
        raise DiagnosticError(f"failing arm {failing!r} is not a declared arm")

    parsed = list(completed.get("parsed_arms", []))
    exits = completed.get("arm_exit_status", {})
    boundary = completed.get("boundary_facts", {})
    for label, names in (("ledger", ledger), ("parsed_arms", parsed),
                         ("arm_exit_status", exits),
                         ("boundary_facts", boundary)):
        undeclared = sorted(n for n in names if n not in ARMS)
        if undeclared:
            raise DiagnosticError(f"{label} names undeclared arms: {undeclared}")

    if phase == PHASE_ARM_SETUP:
        # The arm was being PREPARED: it has no command, no parse, no exit, no
        # raw file and no boundary block, because it never started.
        index = ARMS.index(failing)
        for label, names in (("ledger", ledger), ("parsed_arms", parsed),
                             ("arm_exit_status", exits),
                             ("boundary_facts", boundary)):
            if failing in names:
                raise DiagnosticError(
                    f"{label} contains {failing!r}, but the run failed while "
                    f"that arm was still being prepared")
        if (root / f"{failing}_tripinfo.xml").is_file():
            raise DiagnosticError(
                f"{failing}_tripinfo.xml exists, but that arm never started")
        for arm in ARMS[index + 1:]:
            for label, names in (("ledger", ledger), ("parsed_arms", parsed),
                                 ("arm_exit_status", exits),
                                 ("boundary_facts", boundary)):
                if arm in names:
                    raise DiagnosticError(
                        f"{label} contains {arm!r}, which runs after the arm "
                        f"being prepared and cannot have executed")
        for arm in ARMS[:index]:
            if arm not in ledger or arm not in parsed:
                raise DiagnosticError(
                    f"arm {arm!r} precedes the setup failure and must be "
                    f"complete")
    elif phase == PHASE_ARM:
        if failing in parsed:
            raise DiagnosticError(
                f"the failing arm {failing!r} is also listed as parsed")
        if failing not in ledger:
            raise DiagnosticError(
                f"the failing arm {failing!r} has no command in the ledger")

        # ORDERING: arms run in the declared order, so everything before the
        # failing arm must have been attempted and everything after it cannot.
        index = ARMS.index(failing)
        for arm in ARMS[index + 1:]:
            for label, names in (("ledger", ledger), ("parsed_arms", parsed),
                                 ("arm_exit_status", exits),
                                 ("boundary_facts", boundary)):
                if arm in names:
                    raise DiagnosticError(
                        f"{label} contains {arm!r}, which runs after the "
                        f"failing arm {failing!r} and cannot have executed")
        for arm in ARMS[:index]:
            if arm not in ledger:
                raise DiagnosticError(
                    f"arm {arm!r} precedes the failure but has no command")
            if arm not in parsed:
                raise DiagnosticError(
                    f"arm {arm!r} precedes the failure but was never parsed")
    else:
        # POST-ARM: every arm ran to completion, so the evidence must be whole.
        index = len(ARMS) - 1
        for arm in ARMS:
            if arm not in ledger:
                raise DiagnosticError(
                    f"a post-arm failure requires every command; {arm!r} is "
                    f"missing")
            if arm not in parsed:
                raise DiagnosticError(
                    f"a post-arm failure requires every arm parsed; {arm!r} is "
                    f"missing")
            if exits.get(arm) != 0:
                raise DiagnosticError(
                    f"a post-arm failure requires every arm to have exited "
                    f"zero; {arm!r} recorded {exits.get(arm)!r}")
            if not (root / f"{arm}_tripinfo.xml").is_file():
                raise DiagnosticError(
                    f"a post-arm failure requires every raw file; "
                    f"{arm}_tripinfo.xml is missing")
        for arm in ARMS:
            if specs[arm]["saves_state"] and arm not in boundary:
                raise DiagnosticError(
                    f"a post-arm failure requires boundary facts for the "
                    f"controller arm {arm!r}")

    # A parsed arm must have exited cleanly and left its raw file behind.
    for arm in parsed:
        if arm not in exits:
            raise DiagnosticError(f"parsed arm {arm!r} recorded no exit status")
        if exits[arm] != 0:
            raise DiagnosticError(
                f"parsed arm {arm!r} recorded a non-zero exit {exits[arm]!r}")
        if not (root / f"{arm}_tripinfo.xml").is_file():
            raise DiagnosticError(
                f"parsed arm {arm!r} left no preserved raw file")

    # A COMPLETED controller arm must have kept its boundary facts. Losing them
    # would discard the snapshot evidence for an arm that demonstrably ran.
    for arm in parsed:
        if specs[arm]["saves_state"] and arm not in boundary:
            raise DiagnosticError(
                f"controller arm {arm!r} parsed but kept no boundary facts")

    # RAW FILES are bound to the attempted prefix: only arms up to and including
    # the failing one can have produced output. A `resumed_tripinfo.xml` beside
    # a failing `cold` describes a run that did not happen.
    attempted = set(ARMS[:index + 1] if phase != PHASE_ARM_SETUP
                    else ARMS[:index])
    for arm in ARMS:
        raw = root / f"{arm}_tripinfo.xml"
        if raw.is_file() and arm not in attempted:
            raise DiagnosticError(
                f"{arm}_tripinfo.xml exists although {arm!r} runs after the "
                f"failing arm {failing!r} and cannot have produced output")

    # Every recorded command must match the frozen shape for that arm.
    for arm, argv in ledger.items():
        normalize_argv(frozen_command(arm), argv)

    # Boundary facts belong only to controller-owned arms that actually ran, and
    # each block must be internally consistent.
    for arm, block in boundary.items():
        if not specs[arm]["saves_state"]:
            raise DiagnosticError(
                f"boundary facts recorded for {arm!r}, which owns no snapshot")
        if arm not in ledger:
            raise DiagnosticError(
                f"boundary facts recorded for {arm!r}, which never ran")
        if not isinstance(block, Mapping):
            raise DiagnosticError(f"{arm} boundary facts are not a record")
        # SHAPE only: a failure artifact legitimately records the wrong capture
        # instant when that is what went wrong.
        validate_boundary_block(arm, block, require_contracted_instant=False)
    return {"error": error, "ledger": ledger, "completed": completed,
            "digests": digests}


def execute(*, approval_token=None, home=None, workspace=None,
            runner=None, port_allocator=None):
    """The complete runner. Refuses without the exact frozen contract key.

    Ends as exactly ONE terminal artifact in the bound root: a success outcome,
    or — once arm execution has begun — a failure artifact that preserves the
    offending bytes. An exception after the first arm starts can never become a
    success, and it is never swallowed: the artifact is written and the original
    error is re-raised.
    """
    key = require_approval(approval_token)
    contract = verify_live_inputs()          # still pure, still no simulator
    # The content key binds ONE root. Accepting a caller-supplied path would let
    # a valid token authorize writes outside the boundary the user approved, so
    # the destination is not an argument at all.
    root = ROOT / APPROVED_OUTPUT_ROOT
    if root.exists():
        raise DiagnosticError(f"refusing to reuse an existing root: {root}")
    # The STAGING path must be clear too, and it must be checked HERE — before
    # any simulator import or arm execution. A stale `<root>.partial` left the
    # run to start, fail, and then find preservation refused, so nothing
    # terminal existed at all: the one outcome this design must never have.
    staging = root.with_name(root.name + ".partial")
    if staging.exists():
        raise DiagnosticError(
            f"refusing to start: a stale staging path exists at {staging}; "
            f"preservation would be impossible after the first arm ran")

    from traffic_sim.simulation.runtime import sumo_home  # noqa: PLC0415 — lazy
    import tempfile                                       # noqa: PLC0415 — lazy

    active_home = Path(home) if home else sumo_home()
    binary = active_home / "bin" / "sumo"
    network = ROOT / APPROVED_NETWORK
    population = build_population()
    specs = arm_specifications()
    contract_text_ = json.dumps(contract, indent=2, sort_keys=True) + "\n"

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(workspace) if workspace else Path(scratch)
        work.mkdir(parents=True, exist_ok=True)
        route = work / "population.rou.xml"
        route.write_text(build_route_xml(
            select_probe_edge(network)["edge_id"], population))

        exits: dict[str, int] = {}
        records: dict[str, dict] = {}
        facts: dict[str, dict] = {}
        ledger: dict[str, list] = {}
        started = False
        # Both tracked explicitly, never inferred. `setup_arm` is the arm being
        # PREPARED; `active_arm` is the arm actually RUNNING. A failure between
        # them — a port allocation that throws, say — belongs to neither the
        # completed arms nor a running one, and had no representation before.
        setup_arm = None
        active_arm = None
        try:
            for arm in ARMS:
                setup_arm = arm
                started = True
                spec = specs[arm]
                tripinfo = work / f"{arm}_tripinfo.xml"
                state = work / ("state_control.xml.gz"
                                if arm.endswith("_control") else "state.xml.gz")
                port = ((port_allocator or free_port)()
                        if spec["saves_state"] else None)
                cmd = build_command(
                    sumo_binary=binary, network_path=network, route_path=route,
                    tripinfo_path=tripinfo, begin_s=spec["begin_s"],
                    end_s=spec["end_s"],
                    save_state_at=SAVE_TIME_S if spec["saves_state"] else None,
                    controller_owned=spec["saves_state"], remote_port=port,
                    load_state_path=state if spec["loads_state"] else None,
                    precision=spec["precision"],
                    write_unfinished=spec["write_unfinished"])
                ledger[arm] = [str(token) for token in cmd]
                # Setup is complete and execution begins HERE.
                setup_arm = None
                active_arm = arm
                if spec["saves_state"]:
                    outcome = (runner or run_prefix_atomic)(
                        cmd=cmd, cwd=work, home=active_home,
                        boundary_s=SAVE_TIME_S, state_path=state, port=port)
                    exits[arm] = outcome["returncode"]
                    # Checked IMMEDIATELY, before any later arm runs. A failing
                    # arm that did not stop the run left the remaining five to
                    # execute against a broken predecessor, and the artifact
                    # then blamed whichever arm happened to be last.
                    if exits[arm] != 0:
                        raise DiagnosticError(
                            f"{arm} exited {exits[arm]}; no later arm may run "
                            f"against a failed predecessor")
                    if not Path(state).is_file():
                        raise DiagnosticError(
                            f"{arm} reported success but wrote no state at "
                            f"{state}; there would be nothing to resume from")
                    facts[arm] = {
                        "captured_at_s": outcome["captured_at_s"],
                        "active_vehicle_ids": sorted(
                            outcome.get("active_vehicle_ids", [])),
                        "active_vehicle_count": outcome["active_vehicle_count"],
                        "state_sha256": sha256_file(state),
                    }
                else:
                    exits[arm] = (runner or run_arm)(cmd=cmd, cwd=work)
                    if exits[arm] != 0:
                        raise DiagnosticError(
                            f"{arm} exited {exits[arm]}; no later arm may run "
                            f"against a failed predecessor")
                records[arm] = parse_tripinfo(tripinfo)
                # This arm is COMPLETE. Clearing the marker is what keeps a
                # later, post-arm failure from being blamed on it — the previous
                # revision left it set and produced artifacts naming the last
                # arm as failing while also listing it as parsed, a shape its
                # own validator rejected.
                active_arm = None

            declared = cohort_index(population)
            production = compare_population(
                cold=records["cold"], prefix=records["prefix"],
                resumed=records["resumed"], declared_cohorts=declared)
            control = compare_population(
                cold=records["cold_control"], prefix=records["prefix_control"],
                resumed=records["resumed_control"], declared_cohorts=declared)
            result = validate_result(
                build_verdict(production=production, control=control,
                              contract_content_key=key, arm_exit_status=exits,
                              arm_records=records, boundary_facts=facts),
                contract_content_key=key)
            members = {
                "contract.json": contract_text_.encode("utf-8"),
                "population.rou.xml": route.read_bytes(),
                "observations.json": (json.dumps(
                    {"production": production, "control": control},
                    indent=2, sort_keys=True) + "\n").encode("utf-8"),
                "verdict.json": (json.dumps(
                    result, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            }
            for arm in ARMS:
                members[f"{arm}_tripinfo.xml"] = (
                    work / f"{arm}_tripinfo.xml").read_bytes()
            # Everything is verified in staging; the rename is the commit, so
            # there is nothing left to check afterwards that could fail and
            # strand a success-shaped root.
            published = publish_outcome(root, members)
            result["published_members"] = published
            result["output_root"] = str(root)
            return result

        except BaseException as error:
            if not started or root.exists():
                # Nothing ran, or a terminal artifact already exists. Either way
                # the failure stands on its own and must not be papered over.
                raise
            members = {
                "contract.json": contract_text_.encode("utf-8"),
                "population.rou.xml": (route.read_bytes() if route.is_file()
                                       else b""),
                "command_ledger.json": (json.dumps(
                    ledger, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                "completed_arms.json": (json.dumps(
                    {"arm_exit_status": exits, "boundary_facts": facts,
                     "parsed_arms": sorted(records)},
                    indent=2, sort_keys=True) + "\n").encode("utf-8"),
                "error.json": (json.dumps(
                    {"schema": FAILURE_SCHEMA,
                     "diagnostic_id": DIAGNOSTIC_ID,
                     "contract_content_key": key,
                     # Two distinct phases, never conflated. A failure INSIDE an
                     # arm names that arm; a failure after every arm completed —
                     # comparison, result validation, publication — names NO arm,
                     # because inventing one produced a self-contradictory
                     # artifact that this tool's own validator refused.
                     "failure_phase": (PHASE_ARM if active_arm
                                       else PHASE_ARM_SETUP if setup_arm
                                       else PHASE_POST_ARM),
                     "failing_arm": active_arm or setup_arm,
                     "error_type": type(error).__name__,
                     "error_message": str(error)},
                    indent=2, sort_keys=True) + "\n").encode("utf-8"),
            }
            # Every raw arm file that exists, INCLUDING the one that broke the
            # parser — as BYTES. This is what v1 destroyed, and a text
            # round-trip with errors="replace" would rewrite exactly the invalid
            # or differently-encoded bytes a parser failure exists to explain.
            for arm in ARMS:
                candidate = work / f"{arm}_tripinfo.xml"
                if candidate.is_file():
                    members[f"{arm}_tripinfo.xml"] = candidate.read_bytes()
            try:
                publish_failure(root, members=members)
            except BaseException as preservation_error:
                # The original failure still wins, but a preservation failure is
                # itself evidence and must never be silent — swallowing it would
                # recreate exactly the blindness v1 died of.
                try:
                    error.preservation_error = (
                        f"{type(preservation_error).__name__}: "
                        f"{preservation_error}")
                except Exception:                       # noqa: BLE001
                    pass
                print(f"WARNING: failure preservation failed: "
                      f"{type(preservation_error).__name__}: "
                      f"{preservation_error}", file=sys.stderr)
            raise


# ── Contract ─────────────────────────────────────────────────────────────────
def source_fingerprints() -> dict:
    return {name: sha256_file(ROOT / name) for name in sorted(BOUND_SOURCES)}


def build_contract() -> dict:
    network = ROOT / APPROVED_NETWORK
    if not network.is_file():
        raise DiagnosticError(f"the tracked network is missing: {network}")
    probe = select_probe_edge(network)
    population = build_population()
    specs = arm_specifications()

    contract = {
        "schema": SCHEMA,
        "diagnostic_id": DIAGNOSTIC_ID,
        "status": STATUS_FROZEN,
        "question": (
            "the v9 campaign's warm-versus-cold objective residual is "
            "bit-identical to v2's (-7.73/-80.62/-138.97 s), always negative, "
            "and scales with demand. Is that a population partition error, "
            "two-decimal output quantization, or real save/load integration "
            "drift?"),
        "v1_disposition": {
            "campaign": "warm_state_population_semantics_v1",
            "contract_sha256": V1_CONTRACT_SHA256,
            "status": "spent_failed",
            "executed": "once, under LUNA-WARM-18, with exact user approval",
            "failure": ("cold_tripinfo.xml: a tripinfo record has no id — "
                        "raised on the FIRST arm, before any boundary capture, "
                        "control arm or publication"),
            "cause": ("its parser scanned for the text `<tripinfo\\b`. SUMO "
                      "writes a configuration header as an XML COMMENT "
                      "containing `<tripinfo-output value=.../>` and "
                      "`<tripinfo-output.write-unfinished value=.../>`; a word "
                      "boundary matches between `tripinfo` and `-`, so both "
                      "option tags scanned as records and neither had an id"),
            "second_defect": ("the run happened inside a TemporaryDirectory and "
                              "preserved nothing on failure, so the offending "
                              "file was destroyed as the exception unwound and "
                              "the diagnostic could not diagnose itself"),
            "bytes_preserved": True,
        },
        "parser_contract": {
            "method": "XML element parsing",
            "selection": ("elements whose tag is EXACTLY `tripinfo`; a comment "
                          "is not an element and `tripinfo-output` is a "
                          "different tag, so both disappear without a special "
                          "case"),
            "rejects": ["malformed XML", "an element with no id",
                        "duplicate ids", "a record missing any required field",
                        "non-numeric values", "non-finite values",
                        "a file with no tripinfo elements"],
            "why_not_a_text_scan": (
                "a text scan cannot tell an element from a prefix of one, and "
                "cannot see that bytes sit inside a comment. That cost v1 its "
                "single approved execution"),
        },
        "terminal_schemas": {
            "success": RESULT_SCHEMA,
            "failure": FAILURE_SCHEMA,
            "mutually_exclusive": True,
            "success_members": list(MEMBER_ORDER),
            "failure_members": list(FAILURE_MEMBERS),
            "failure_rule": (
                "once arm execution begins, an exception publishes ONE failure "
                "artifact preserving the contract, fixture, command ledger, "
                "completed arm exits and boundary facts, the error type, "
                "message and failing arm, and EVERY raw arm file that exists "
                "including the one that broke the parser — then re-raises. A "
                "failure artifact never carries a verdict, and preservation "
                "never masks the original error"),
            "no_root_before_arms": (
                "approval, live-byte verification and root-absence all run "
                "before the first arm, so a preflight failure creates nothing"),
            "byte_exactness_rule": (
                "preserved members are written as BYTES. Reading a possibly "
                "invalid file as text with errors=replace and writing it back "
                "rewrites exactly the bytes a parser failure exists to explain"),
            "commit_rule": (
                "every member is written and verified in a staging directory "
                "and the rename is the LAST operation. Renaming first and "
                "verifying after left a success-shaped root when a check "
                "failed, and the failure handler then skipped preservation "
                "because the root already existed"),
            "preservation_failure_rule": (
                "if preservation itself fails the ORIGINAL error still "
                "propagates, but the preservation failure is reported rather "
                "than swallowed"),
            "member_allowlist_rule": (
                "the permitted member set is DERIVED from this contract's "
                "member list and declared arms, never read from the artifact's "
                "own digest manifest; adding a digest cannot legitimise a file "
                "the contract never permitted"),
            "arm_relationship_rules": (
                "every named arm must be declared; arms run in the declared "
                "order, so arms after the failing one cannot appear and arms "
                "before it must have run and parsed; a parsed arm must record "
                "a zero exit and have left its raw file; boundary facts belong "
                "only to controller-owned arms that ran"),
            "staging_precheck_rule": (
                "the staging path is checked for staleness BEFORE any simulator "
                "import or arm execution: a stale one would let a run start, "
                "fail, and then be unable to preserve anything at all"),
            "failure_validator": (
                "validate_failure_artifact recomputes the member set, digest "
                "coverage, embedded key/schema/identity and ledger/"
                "completed-arm/failing-arm consistency, and refuses any result "
                "member or unlisted file"),
            "failure_phase_rule": (
                "a failure records WHERE it happened: `arm` names the arm it "
                "died inside, `post_arm` names NO arm because every arm "
                "completed and the failure came later (comparison, result "
                "validation, publication). Conflating them produced artifacts "
                "naming the last arm as failing while also listing it as "
                "parsed — a shape this tool's own validator refuses. A third "
                "phase, `arm_setup`, covers a failure while an arm is being "
                "PREPARED (port allocation, command construction): earlier arms "
                "are complete and that arm has no command, parse, exit, raw "
                "file or boundary block at all"),
            "arm_exit_check_rule": (
                "each arm's return code is checked IMMEDIATELY, before any "
                "later arm runs; no arm may execute against a failed "
                "predecessor"),
            "boundary_evidence_scope_rule": (
                "on the success path a capture at the wrong instant is invalid "
                "evidence and is refused; on a failure artifact it is the "
                "observation that EXPLAINS the failure, so only the block's "
                "shape and internal consistency are enforced there"),
            "phase_evidence_rule": (
                "an arm-phase failure requires exactly the attempted prefix; a "
                "post-arm failure requires all six commands, parses, zero "
                "exits, raw files and controller boundary blocks; an "
                "arm_setup failure requires every earlier arm complete and NO "
                "evidence at all for the arm being prepared. Any completed "
                "controller arm must retain its boundary facts"),
            "failing_arm_rule": (
                "the failing arm is tracked explicitly as execution proceeds, "
                "never inferred from which records happen to be absent"),
            "cli_rule": ("exactly one of --freeze, --verify or --execute; "
                         "--execute requires the exact content key and a "
                         "terminal failure exits nonzero"),
        },
        "refuted_hypothesis": (
            "state serialization: v9 applied --save-state.rng true and "
            "--save-state.precision 16 and the residual did not move by a "
            "single cent, so those settings are not the cause"),
        "why_the_v2_diagnostic_could_not_answer_it": (
            "it probed ONE vehicle. A residual that scales with the number of "
            "vehicles crossing the boundary cannot appear in a one-vehicle "
            "fixture, so its correct finding — the accumulator is preserved — "
            "left this question untouched"),
        "network_requirement": {
            "path": APPROVED_NETWORK,
            "sha256": sha256_file(network),
        },
        "fixture": {
            "probe_edge": probe,
            "step_length_s": STEP_LENGTH_S,
            "seed": SEED,
            "save_time_s": SAVE_TIME_S,
            "end_time_s": END_TIME_S,
            "fast_speed_mps": FAST_SPEED_MPS,
            "slow_speed_mps": SLOW_SPEED_MPS,
            "cohort_sizes": {
                "completed_before_boundary": COHORT_COMPLETED_BEFORE,
                "active_at_boundary": COHORT_ACTIVE_AT,
                "depart_after_boundary": COHORT_DEPART_AFTER,
            },
            "population": population,
            "invariants": [
                "every vehicle belongs to exactly one cohort, assigned by the "
                "fixture rather than inferred from a result",
                "the fast cohort completes strictly before the boundary",
                "the slow cohort departs before and is still driving at it",
                "the post cohort departs strictly after it",
                "every vehicle completes before the end instant, so no trip is "
                "excluded as unfinished",
            ],
        },
        "arms": {
            name: dict(spec, precision=(TRIPINFO_PRECISION
                                        if spec["precision"] is None
                                        else spec["precision"]))
            for name, spec in sorted(specs.items())
        },
        "command_contract": {
            "shared": ("network, route file, seed, MESOSCOPIC core with limited "
                       "junction control matching production, step length, "
                       "teleporting disabled, randomness disabled"),
            "permitted_differences": ["begin", "end", "save-state", "load-state",
                                      "precision", "tripinfo output path",
                                      "prefix-only write-unfinished"],
            "exact_argv": frozen_commands(),
            "argv_placeholders": dict(sorted(PLACEHOLDERS.items())),
            "meso_arguments": list(MESO_ARGUMENTS),
            "prefix_flush_s": PREFIX_FLUSH_S,
            "prefix_atomicity_rule": (
                "the prefix arm is terminated AT the boundary by a controller "
                "that steps, saves and closes in one process — never by "
                "letting the batch run on to its exclusive end. A vehicle that "
                "completed in the extra step would appear in the prefix "
                "tripinfo AND remain in the state resumed from the boundary, "
                "counting it twice and breaking the partition silently"),
            "run_timeout_s": RUN_TIMEOUT_S,
            "remote_port_rule": (
                "controller-owned prefix arms carry --remote-port, allocated "
                "per run by binding to port 0 as production does. Without it "
                "SUMO opens no TraCI server and the connection can never be "
                "made; a fixed port would make concurrent runs collide"),
            "end_is_exclusive_rule": (
                "SUMO's --end is exclusive and a state requested exactly at it "
                "is never written, so every prefix arm ends at save_time + "
                "prefix_flush_s; this mirrors production's flush_s=1"),
            "write_unfinished_rule": (
                "true on every arm except the prefix arms, exactly as "
                "production does it: a vehicle still driving at the snapshot "
                "continues in the resumed arm, and counting it in both would "
                "count one vehicle twice — while dropping unfinished trips "
                "from a cold or resumed arm would break the partition silently "
                "instead of failing loudly"),
            "snapshot_arguments": snapshot_settings_arguments(),
            "snapshot_arguments_source": (
                "derived from the production cache constants via "
                "warm_state_boundary.snapshot_settings_arguments(); never "
                "duplicated as literals here"),
        },
        "result_contract": {
            "required_exit_status": 0,
            "required_arms": list(ARMS),
            "member_order": list(MEMBER_ORDER),
            "partition_rule": (
                "the prefix and resumed vehicle sets must be disjoint and "
                "their union must equal the cold set exactly; checked before "
                "any delta arithmetic"),
            "totals_rule": (
                "cold and split totals are RECOMPUTED from the per-vehicle "
                "records; an aggregate supplied without them is rejected"),
            "tripinfo_fields": list(TRIPINFO_FIELDS),
            "per_vehicle_fields": (
                ["id", "declared_cohort", "observed_cohort", "arm",
                 "cold_time_loss_s", "split_time_loss_s", "delta_s"]
                + [f"cold_{f}" for f in TRIPINFO_FIELDS]
                + [f"split_{f}" for f in TRIPINFO_FIELDS]),
            "cohort_rule": (
                "membership is OBSERVED from each vehicle's own depart/arrival "
                "against the boundary, never inferred from ideal "
                "length-over-speed arithmetic, which cannot see insertion "
                "queuing or car-following. The fixture's declared cohort is "
                "recorded as intent and any disagreement is reported, not "
                "silently resolved"),
            "arm_membership_rule": (
                "a vehicle that COMPLETED before the boundary may only be "
                "reported by the prefix; anything still driving at it or "
                "departing after it may only be reported by the resumed arm. "
                "Set identities alone are not enough — two vehicles can trade "
                "arms and preserve every union while both are attributed to "
                "the wrong side"),
            "reconstruction_rule": (
                "every stored comparison is RECONSTRUCTED from the raw per-arm "
                "tripinfo records — partition, cohort counts, misplaced "
                "vehicles and totals — not merely re-summed. Recomputing only "
                "the totals left the findings that decide whether the deltas "
                "mean anything at all in the trusted column"),
            "raw_records_required": True,
            "raw_records_completeness_rule": (
                "arm_records must cover exactly all six arms and the whole "
                "rebuilt comparison must equal the stored one; an empty map "
                "previously skipped reconstruction entirely, and comparing a "
                "chosen subset of fields left the rest trusted"),
            "boundary_evidence_rule": (
                "each controller-owned arm must record the capture instant, "
                "the exact vehicle ids seen in flight (which must equal the "
                "cold records' active-at-boundary cohort), their count, and a "
                "digest of a state file that exists. A boundary nobody can "
                "show did not happen"),
            "exit_status_rule": (
                "a killed or unreaped process must never be recorded as a "
                "normal exit; a missing return code is an error, not a zero"),
            "verdict_recomputation_rule": (
                "a stored verdict must RECOMPUTE from its own observations; a "
                "transcribed conclusion can be edited, a recomputed one cannot"),
            "arm_exit_rule": (
                f"all {len(ARMS)} arms must record exit status zero"),
            "cohort_population_rule": (
                "all three observed cohorts must be non-empty or the fixture "
                "did not exercise the boundary it claims to and the verdict is "
                "a partition error"),
            "result_schema": RESULT_SCHEMA,
            "required_result_keys": list(REQUIRED_RESULT_KEYS),
        },
        "classification_contract": {
            "classifications": list(CLASSIFICATIONS),
            "mutually_exclusive": True,
            "default": INCONCLUSIVE,
            "production_precision": TRIPINFO_PRECISION,
            "control_precision": CONTROL_PRECISION,
            "quantization_half_ulp_s": QUANT_HALF_ULP_S,
            "quantization_per_vehicle_bound_s": QUANT_PER_VEHICLE_BOUND_S,
            "quantization_bound_rationale": (
                "the observed delta is a difference of TWO independently "
                "rounded reports, so one vehicle can contribute two half-ULPs; "
                "the conservative per-vehicle bound is a full ULP"),
            "drift_epsilon_s": DRIFT_EPSILON_S,
            "exact_agreement_rule": (
                "requires zero delta in the production AND the raised-precision "
                "control. Production alone is not sufficient: two-decimal "
                "reporting can round a real difference away, which is the "
                "quantization case rather than agreement"),
            "rule": (
                "partition and cohort population first; then exact agreement; "
                "then quantization only "
                "if deltas vanish at control precision AND the production "
                "residual fits the vehicle-count-scaled rounding envelope; then "
                "drift only if deltas survive control precision AND the "
                "residual exceeds that envelope; otherwise inconclusive"),
            "recommendation_rule": (
                "a correction is recommended only when the observations "
                "uniquely establish its mechanism"),
        },
        "claim_scope": (
            "a synthetic single-edge mechanism probe. It is NOT equivalence "
            "evidence, NOT performance evidence, and NOT grounds for adoption, "
            "release or enabling warming. Product-default warming stays OFF "
            "whatever this returns"),
        "execution_authority": (
            "NONE from this contract. The runner is COMPLETE and GATED: it "
            "requires an approval token equal to this manifest's content_key, "
            "so an approval can only ever authorize the exact fixture, "
            "commands and classifier reviewed here — changing any of them "
            "changes the key and voids it. No approval is stored. Execution "
            "needs a new Sol task/revision and exact user approval; simulator "
            "imports are lazy and happen only after that gate"),
        "output_root": APPROVED_OUTPUT_ROOT,
        "publication_contract": {
            "members": list(MEMBER_ORDER),
            "digest_manifest": "members.json",
            "rule": ("the outcome root must be ABSENT and is written "
                     "all-or-nothing via a staging directory; the digest "
                     "manifest is written last, so a root without one is "
                     "visibly incomplete. A half-written root indexed by a "
                     "content key would otherwise read as evidence"),
            "preflight_rule": ("the frozen contract, every bound source and "
                               "the network are verified BEFORE any simulator "
                               "import, while the program is still pure"),
            "root_is_not_an_argument": (
                "the destination is derived from this contract, never supplied "
                "by a caller: the content key binds ONE root, so a valid "
                "approval token must not be able to authorize writes anywhere "
                "else"),
            "pre_commit_verification": (
                "every member is written into staging, re-hashed FROM DISK and "
                "the member list re-checked THERE; only then is the staging "
                "directory renamed into place. Nothing is verified after the "
                "rename because nothing may fail after it — an earlier "
                "revision verified afterwards, and a failing check left a "
                "success-shaped root that made failure preservation impossible"),
            "verification_recomputes": (
                "a stored artifact is checked by recomputing what it should "
                "contain: the embedded contract's own key, the deterministic "
                "fixture, each arm's frozen command shape, and the raw files "
                "the attempted prefix could have produced"),
        },
        "source_fingerprints": source_fingerprints(),
    }
    contract["content_key"] = canonical_key(contract)
    return contract


def contract_text() -> str:
    return json.dumps(build_contract(), indent=2, sort_keys=True) + "\n"


def freeze(path=None) -> str:
    """Write the contract once; refuse to clobber an existing one."""
    target = Path(path) if path else ROOT / CONTRACT_PATH
    if target.exists():
        raise DiagnosticError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contract_text(), encoding="utf-8")
    return json.loads(target.read_text())["content_key"]


def verify(path=None) -> bool:
    target = Path(path) if path else ROOT / CONTRACT_PATH
    if not target.is_file():
        raise DiagnosticError(f"the frozen contract is missing: {target}")
    stored = target.read_text(encoding="utf-8")
    if stored != contract_text():
        return False
    payload = json.loads(stored)
    return canonical_key(payload) == payload["content_key"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true",
                        help="publish the contract; refuses to overwrite")
    parser.add_argument("--verify", action="store_true",
                        help="recompose and compare; never writes")
    parser.add_argument("--execute", metavar="APPROVAL_TOKEN",
                        help=("run the diagnostic once; requires the exact "
                              "frozen contract key"))
    args = parser.parse_args(argv)
    chosen = [bool(args.freeze), bool(args.verify), args.execute is not None]
    if sum(chosen) != 1:
        raise DiagnosticError(
            "pass exactly one of --freeze, --verify or --execute")
    if args.freeze:
        key = freeze()
        print(f"{CONTRACT_PATH}: {key}")
        print(f"status: {STATUS_FROZEN} — execution requires a new Sol "
              f"task/revision and exact user approval")
        return 0
    if args.verify:
        ok = verify()
        print("reproduces byte-for-byte:", ok)
        return 0 if ok else 1
    # Gated execution. The token is checked inside `execute()` BEFORE any
    # simulator import, and a terminal failure must exit NONZERO — a diagnostic
    # that fails quietly is worse than one that does not run.
    try:
        result = execute(approval_token=args.execute)
    except DiagnosticError as error:
        print(f"diagnostic failed: {error}")
        return 1
    print("classification:", result["verdict"]["classification"])
    print("output root:", result["output_root"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
