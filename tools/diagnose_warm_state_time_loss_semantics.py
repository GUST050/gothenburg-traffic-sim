"""Measure what SUMO's saved state does to a vehicle's `timeLoss` accumulator.

WHY THIS EXISTS. The v3 warm-state design (LUNA-WARM-08 revision 1) rests on one
unverified assumption: that after `--load-state`, a vehicle's resumed tripinfo
reports only the time loss accrued AFTER the snapshot, so its pre-boundary delay
must be carried separately in a per-vehicle ledger and added back.

LUNA-WARM-07's measured gap is CONSISTENT with that assumption but does not
prove it. If the saved state actually preserves the accumulator, the resumed
tripinfo already carries the full value, no ledger offset should ever be added,
and the v3 accounting is wrong in an interesting way. That is a cheap question to
answer with one controlled run, and an expensive one to keep guessing at.

WHAT THIS IS NOT. One synthetic vehicle on one edge is a MECHANISM probe, not
equivalence evidence, not performance evidence, and not grounds for adoption.
It establishes what SUMO does to one accumulator under one controlled fixture.

Approved as a one-time non-campaign diagnostic (LUNA-WARM-08 revision 2). It
runs exactly three SUMO processes: one uninterrupted cold run, one prefix run
that saves state, one resumed run. It touches no `runs/` path, no archived
demand, and no cache.

Usage:
  python3 tools/diagnose_warm_state_time_loss_semantics.py --preflight
  python3 tools/diagnose_warm_state_time_loss_semantics.py --execute \\
      --network sumo/net.net.xml \\
      --output-root validation/warm_state_time_loss_semantics_v1_outcome
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]

DIAGNOSTIC_ID = "warm_state_time_loss_semantics_v2"
SCHEMA = "warm_state_time_loss_semantics_v2"

# ── The frozen fixture contract ──────────────────────────────────────────────
# Every value that changes what is measured lives here and is hashed into the
# outcome, so a result can never be read as describing a fixture it did not run.
APPROVED_NETWORK = "sumo/net.net.xml"
APPROVED_OUTPUT_ROOT = "validation/warm_state_time_loss_semantics_v2_outcome"

VEHICLE_ID = "warm-probe"
STEP_LENGTH_S = 1.0
# The snapshot instant. Chosen so the probe is unambiguously MID-TRIP: it has
# accrued delay, and it is nowhere near arriving.
SAVE_TIME_S = 20.0
# Held well below every candidate edge's limit, so time loss accrues steadily
# and positively rather than depending on interactions with other traffic —
# there is no other traffic.
CONTROL_SPEED_MPS = 2.0
# Long enough that the probe is still driving at SAVE_TIME_S with a wide margin,
# and short enough to finish quickly.
MIN_EDGE_LENGTH_M = 200.0
# Generous relative to a single-vehicle run; a hang must fail, not wait forever.
RUN_TIMEOUT_S = 300
# The probe must still be mid-trip at the snapshot for the question to mean
# anything. Enforced, not assumed.
MIN_BOUNDARY_TIME_LOSS_S = 0.5

# Classification tolerance. This is a MEASUREMENT classification, not the
# campaign's equivalence contract: it decides which of three mechanisms the
# numbers indicate. It never relaxes any production comparison.
CLASSIFY_EPSILON_S = 0.05

FULL_ACCUMULATOR_PRESERVED = "full_accumulator_preserved"
POST_LOAD_SEGMENT_ONLY = "post_load_segment_only"
INCONCLUSIVE = "inconclusive"

MEMBER_ORDER = ("contract.json", "probe.rou.xml", "cold_tripinfo.xml",
                "resumed_tripinfo.xml", "prefix_state.xml.gz", "result.json")

_TRIPINFO_RE = re.compile(r'<tripinfo\b[^>]*\bid="([^"]*)"[^>]*\btimeLoss="([^"]*)"')


class DiagnosticError(SystemExit):
    """Fail closed with an explicit reason. Never used to skip a check."""


# ── Pure helpers ─────────────────────────────────────────────────────────────

def canonical_bytes(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def canonical_key(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_probe_edge(network_path) -> dict:
    """Deterministically choose ONE passenger-capable non-internal edge.

    The rule is lexicographic on edge id among every edge that satisfies the
    fixture's requirements, so the same network always yields the same probe and
    the choice cannot drift with parser or filesystem ordering. Returns the
    decision AND the candidate count, because "we picked the only option" and
    "we picked one of 737" are different claims about determinism.
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
        if float(length.group(1)) < MIN_EDGE_LENGTH_M:
            continue
        if float(speed.group(1)) <= CONTROL_SPEED_MPS:
            continue
        candidates.append((identifier.group(1), float(length.group(1)),
                           float(speed.group(1))))
    if not candidates:
        raise DiagnosticError(
            f"no passenger-capable non-internal edge of at least "
            f"{MIN_EDGE_LENGTH_M} m with a limit above {CONTROL_SPEED_MPS} m/s "
            f"exists in {network_path}; the fixture cannot be constructed")
    candidates.sort(key=lambda item: item[0])
    edge_id, length_m, speed_mps = candidates[0]
    # The probe must still be driving at the snapshot, by construction.
    expected_travel_s = length_m / CONTROL_SPEED_MPS
    if expected_travel_s <= SAVE_TIME_S * 2:
        raise DiagnosticError(
            f"probe edge {edge_id} takes only {expected_travel_s:.1f} s at "
            f"{CONTROL_SPEED_MPS} m/s; too close to the {SAVE_TIME_S} s "
            f"snapshot to guarantee a mid-trip boundary")
    return {
        "edge_id": edge_id,
        "length_m": round(length_m, 3),
        "speed_limit_mps": round(speed_mps, 3),
        "expected_travel_s": round(expected_travel_s, 3),
        "selection_rule": ("lexicographically smallest non-internal "
                           "passenger-capable edge of at least "
                           f"{MIN_EDGE_LENGTH_M} m with a limit above "
                           f"{CONTROL_SPEED_MPS} m/s"),
        "candidate_count": len(candidates),
    }


def build_route_xml(edge_id: str) -> str:
    """The minimal synthetic route: one vehicle, one edge, departing at zero."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<routes>\n'
        f'    <vehicle id="{VEHICLE_ID}" depart="0.00">\n'
        f'        <route edges="{edge_id}"/>\n'
        '    </vehicle>\n'
        '</routes>\n'
    )


def build_command(*, sumo_binary, network_path, route_path, tripinfo_path,
                  end_s, begin_s=0.0, load_state_path=None) -> list[str]:
    """The argv for one arm. Pure: builds nothing on disk and runs nothing.

    All three arms share network, route, seed, mode, step length and output
    precision. NOTHING here sets `--precision`: the arms must differ only in
    where they begin and whether they load a state, or the comparison would be
    measuring the flag instead of the accumulator.
    """
    cmd = [
        str(sumo_binary),
        "--net-file", str(Path(network_path).resolve()),
        "--route-files", str(Path(route_path).resolve()),
        "--begin", f"{float(begin_s):.2f}",
        "--end", f"{float(end_s):.2f}",
        "--step-length", f"{STEP_LENGTH_S:.2f}",
        "--seed", "1000",
        "--tripinfo-output", str(Path(tripinfo_path).resolve()),
        # Completed trips only, matching production's prefix semantics.
        "--tripinfo-output.write-unfinished", "false",
        "--no-step-log", "true",
        "--no-warnings", "true",
        # Determinism: no rerouting, no teleporting, no random departures.
        "--time-to-teleport", "-1",
        "--random", "false",
    ]
    if load_state_path is not None:
        cmd += ["--load-state", str(Path(load_state_path).resolve())]
    return cmd


def parse_tripinfo_time_loss(path) -> dict[str, float]:
    """`{vehicle_id: timeLoss}`, refusing anything malformed or duplicated."""
    out: dict[str, float] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if re.match(r'<tripinfo(?![\w-])', stripped) is None:
            continue
        match = _TRIPINFO_RE.search(stripped)
        if match is None:
            raise DiagnosticError(f"tripinfo record lacks id/timeLoss in {path}")
        vehicle, raw = match.group(1), match.group(2)
        if vehicle in out:
            raise DiagnosticError(f"duplicate tripinfo id {vehicle!r} in {path}")
        try:
            out[vehicle] = float(raw)
        except ValueError:
            raise DiagnosticError(f"non-numeric timeLoss {raw!r} in {path}")
    return out


def classify(observations: Mapping[str, Any],
             *, epsilon: float = CLASSIFY_EPSILON_S) -> dict:
    """Decide which mechanism the measurements indicate, or refuse to decide.

    Two independent signals are required to agree before a mechanism is named:
      - what the RESTORED simulation reports for the probe immediately after
        loading (the accumulator itself), and
      - what the resumed run's tripinfo reports at arrival, compared against the
        uninterrupted run's own final value.
    If they disagree, the answer is `inconclusive` and the reasons say why. A
    diagnostic that always produces a verdict is not measuring anything.
    """
    boundary = observations["prefix_boundary_time_loss_s"]
    post_load = observations["post_load_time_loss_s"]
    cold_final = observations["cold_final_tripinfo_time_loss_s"]
    resumed_final = observations["resumed_final_tripinfo_time_loss_s"]

    reasons: list[str] = []
    preserved_accumulator = abs(post_load - boundary) <= epsilon
    reset_accumulator = abs(post_load) <= epsilon
    preserved_final = abs(resumed_final - cold_final) <= epsilon
    segment_final = abs(resumed_final - (cold_final - boundary)) <= epsilon

    reasons.append(
        f"post-load accumulator {post_load} vs boundary {boundary}: "
        f"{'preserved' if preserved_accumulator else 'not preserved'}"
        f"{', and is zero' if reset_accumulator else ''}")
    reasons.append(
        f"resumed final {resumed_final} vs cold final {cold_final}: "
        f"{'equal' if preserved_final else 'different'}; vs cold final minus "
        f"boundary {round(cold_final - boundary, 6)}: "
        f"{'equal' if segment_final else 'different'}")

    if preserved_accumulator and preserved_final and not segment_final:
        classification = FULL_ACCUMULATOR_PRESERVED
        reasons.append(
            "the saved state carried the accumulator, so resumed tripinfo "
            "already reports the whole trip's time loss; adding a ledger offset "
            "on top of it would DOUBLE COUNT the pre-boundary delay")
    elif reset_accumulator and segment_final and not preserved_final:
        classification = POST_LOAD_SEGMENT_ONLY
        reasons.append(
            "the restored vehicle starts from zero, so resumed tripinfo reports "
            "only post-boundary time loss; the pre-boundary delay must be "
            "carried separately, which is what v3 assumes")
    else:
        classification = INCONCLUSIVE
        reasons.append(
            "the two signals do not agree on a single mechanism; this fixture "
            "does not settle the question and no accounting change should be "
            "based on it")
    return {"classification": classification, "reasons": reasons,
            "epsilon_s": epsilon,
            "signals": {
                "post_load_equals_boundary": preserved_accumulator,
                "post_load_is_zero": reset_accumulator,
                "resumed_final_equals_cold_final": preserved_final,
                "resumed_final_equals_cold_minus_boundary": segment_final,
            }}


def member_manifest(root, members=MEMBER_ORDER) -> dict:
    """Digest every written member, in a fixed order, refusing a missing one."""
    root = Path(root)
    manifest = {}
    for name in members:
        path = root / name
        if not path.is_file():
            raise DiagnosticError(f"outcome member is missing: {name}")
        manifest[name] = {"sha256": sha256_file(path),
                          "bytes": path.stat().st_size}
    return manifest


def build_contract(*, network_sha256, sumo_version, probe, source_fingerprints,
                   command_shapes) -> dict:
    """Everything that decides what gets measured, hashed into one object."""
    contract = {
        "schema": SCHEMA,
        "diagnostic_id": DIAGNOSTIC_ID,
        "kind": "non_campaign_mechanism_diagnostic",
        "question": ("does SUMO's saved state preserve a vehicle's timeLoss "
                     "accumulator across --load-state, or does the resumed run "
                     "report only post-boundary time loss?"),
        "why": ("the v3 warm-state accounting assumes the latter and adds a "
                "per-vehicle ledger offset; if the former is true that offset "
                "double counts and the design is wrong"),
        "claim_scope": ("one synthetic vehicle on one edge under one controlled "
                        "speed schedule; a MECHANISM observation only. It is "
                        "not equivalence evidence, not performance evidence, "
                        "and not grounds for adoption or release"),
        "fixture": {
            "vehicle_id": VEHICLE_ID,
            "step_length_s": STEP_LENGTH_S,
            "save_time_s": SAVE_TIME_S,
            "control_speed_mps": CONTROL_SPEED_MPS,
            "control_schedule": ("absolute-time: the probe's speed is held at "
                                 "control_speed_mps at every step in every arm, "
                                 "so the arms differ only in where they begin "
                                 "and whether they load a state"),
            "min_boundary_time_loss_s": MIN_BOUNDARY_TIME_LOSS_S,
            "run_timeout_s": RUN_TIMEOUT_S,
            "output_precision": "SUMO default in every arm; never overridden",
        },
        "probe_edge": dict(probe),
        "network": {"path": APPROVED_NETWORK, "sha256": network_sha256},
        "sumo_version": sumo_version,
        "command_shapes": command_shapes,
        "source_fingerprints": dict(source_fingerprints),
        "classification_vocabulary": [FULL_ACCUMULATOR_PRESERVED,
                                      POST_LOAD_SEGMENT_ONLY, INCONCLUSIVE],
    }
    contract["content_key"] = canonical_key(contract)
    return contract


# ── Injected process/TraCI seams ─────────────────────────────────────────────

class SumoRunner:
    """Owns SUMO processes and TraCI connections for this diagnostic.

    Separate from production's `WarmPrefixController` on purpose: this revision
    may not edit production warm-state code, and a diagnostic that shared it
    could not be trusted to leave it untouched.

    `traci` and `subprocess` are imported lazily inside the methods, so
    importing this module — which the process-free tests do — starts nothing.
    """

    def __init__(self, *, traci_module=None, launcher=None, port=None,
                 sumo_binary=None):
        self._traci_module = traci_module
        self._launcher = launcher
        self._port = port
        self._sumo_binary = sumo_binary

    # -- lazily resolved dependencies --

    def traci(self):
        if self._traci_module is not None:
            return self._traci_module
        tools = self.sumo_home() / "tools"
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        import traci                                   # noqa: PLC0415 — lazy
        return traci

    def sumo_home(self) -> Path:
        if os.environ.get("SUMO_HOME"):
            return Path(os.environ["SUMO_HOME"])
        import importlib.util                          # noqa: PLC0415 — lazy
        spec = importlib.util.find_spec("sumo")
        if spec is None or not spec.origin:
            raise DiagnosticError(
                "cannot locate SUMO: set SUMO_HOME or install eclipse-sumo")
        return Path(spec.origin).parent

    def sumo_binary(self) -> Path:
        if self._sumo_binary is not None:
            return Path(self._sumo_binary)
        candidate = self.sumo_home() / "bin" / "sumo"
        if not candidate.is_file():
            raise DiagnosticError(f"SUMO executable not found: {candidate}")
        return candidate

    def sumo_version(self) -> str:
        import subprocess                              # noqa: PLC0415 — lazy
        result = subprocess.run([str(self.sumo_binary()), "--version"],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise DiagnosticError(
                f"sumo --version failed with {result.returncode}")
        return result.stdout.splitlines()[0].strip()

    def _free_port(self) -> int:
        if self._port is not None:
            return self._port
        import socket                                  # noqa: PLC0415 — lazy
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def _launch(self, cmd, *, cwd, port, stderr_path):
        if self._launcher is not None:
            return self._launcher(cmd, cwd=cwd, port=port,
                                  stderr_path=stderr_path)
        import subprocess                              # noqa: PLC0415 — lazy
        # stderr to a FILE, never a pipe. An unread pipe deadlocks a process
        # that writes more than the buffer holds, and a deadlock here would
        # consume the single approved execution.
        handle = open(stderr_path, "wb")
        try:
            return subprocess.Popen(
                [*cmd, "--remote-port", str(port)], cwd=str(cwd),
                stdout=subprocess.DEVNULL, stderr=handle)
        finally:
            handle.close()

    @staticmethod
    def _stderr_tail(stderr_path, limit=1500) -> str:
        path = Path(stderr_path)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]

    def _reap(self, process, *, stderr_path, timeout=60) -> tuple:
        """Wait for the process, killing it if it will not exit, and REPORT the
        code. Never swallows it: a nonzero exit is the difference between a run
        that finished and a run that merely stopped producing output.
        """
        killed = False
        try:
            code = process.wait(timeout=timeout)
        except Exception:
            process.kill()
            code = process.wait()
            killed = True
        if code is None:
            code = getattr(process, "returncode", None)
        return code, killed, self._stderr_tail(stderr_path)

    # -- the three arms --

    def run_arm(self, *, cmd, cwd, label, capture_at_s=None,
                save_state_path=None, capture_after_load=False):
        """Drive one arm to completion under TraCI, holding the probe's speed.

        Returns the observations this arm can make. The process is always
        reaped, so SUMO writes its tripinfo exactly as a batch run would and is
        never left orphaned.
        """
        import time                                   # noqa: PLC0415 — lazy
        traci = self.traci()
        port = self._free_port()
        stderr_path = Path(cwd) / f"{label}_stderr.log"
        process = self._launch(cmd, cwd=cwd, port=port, stderr_path=stderr_path)
        observed: dict[str, Any] = {"label": label, "capture_at_s": capture_at_s}
        deadline = time.monotonic() + RUN_TIMEOUT_S
        try:
            # A SUMO that rejected its own command line exits immediately, and
            # connecting to a dead process is what turns a bad argv into a hang
            # instead of an error. Check first, and surface its stderr.
            for _ in range(20):
                if process.poll() is None:
                    break
                time.sleep(0.05)
            if process.poll() is not None:
                raise DiagnosticError(
                    f"{label}: sumo exited with {process.poll()} before TraCI "
                    f"could connect: {self._stderr_tail(stderr_path)}")
            traci.init(port)
            if capture_after_load:
                # BEFORE any stepping: the question is what the restored state
                # itself says, not what one more step of simulation produces.
                observed["post_load_time_s"] = float(traci.simulation.getTime())
                observed["post_load_present"] = (
                    VEHICLE_ID in traci.vehicle.getIDList())
                observed["post_load_time_loss_s"] = (
                    float(traci.vehicle.getTimeLoss(VEHICLE_ID))
                    if observed["post_load_present"] else None)
            last_seen_time_loss = None
            captured = None
            while traci.simulation.getMinExpectedNumber() > 0:
                if time.monotonic() > deadline:
                    raise DiagnosticError(
                        f"arm exceeded the {RUN_TIMEOUT_S}s timeout at "
                        f"simulation time {traci.simulation.getTime()}")
                present = VEHICLE_ID in traci.vehicle.getIDList()
                if present:
                    # Absolute-time schedule: identical in every arm.
                    traci.vehicle.setSpeed(VEHICLE_ID, CONTROL_SPEED_MPS)
                    last_seen_time_loss = float(
                        traci.vehicle.getTimeLoss(VEHICLE_ID))
                now = float(traci.simulation.getTime())
                if capture_at_s is not None and captured is None \
                        and now == float(capture_at_s):
                    if not present:
                        raise DiagnosticError(
                            f"{VEHICLE_ID} is not active at the capture step "
                            f"{capture_at_s}; the fixture is invalid")
                    captured = last_seen_time_loss
                    observed["capture_time_s"] = now
                    observed["captured_time_loss_s"] = captured
                    if save_state_path is not None:
                        # SAME connection, SAME step as the capture.
                        traci.simulation.saveState(str(save_state_path))
                        observed["saved_at_s"] = now
                        break
                traci.simulationStep()
            observed["last_seen_time_loss_s"] = last_seen_time_loss
            observed["final_time_s"] = float(traci.simulation.getTime())
        finally:
            try:
                traci.close()
            except Exception:
                # Already-dead connection: the process cleanup below still runs,
                # and the original failure is the one worth reporting.
                pass
            code, killed, stderr = self._reap(process, stderr_path=stderr_path)
            observed["return_code"] = code
            observed["killed_during_cleanup"] = killed
            observed["stderr_tail"] = stderr
        # ONLY reached when the body above completed normally, so a failure
        # inside it propagates untouched rather than being masked by an
        # exit-code complaint about a process we already knew had failed.
        if code != 0:
            raise DiagnosticError(
                f"{label}: sumo exited with return code {code!r} after a "
                f"normal arm completion; a run that stops producing output is "
                f"not a run that finished. stderr: {stderr}")
        if killed:
            raise DiagnosticError(
                f"{label}: sumo had to be killed during cleanup, so its "
                f"outputs cannot be trusted")
        return observed


# ── Preflight and execution ──────────────────────────────────────────────────

def preflight(*, network, output_root, runner) -> dict:
    """Everything that must hold BEFORE anything is created or executed."""
    network = Path(network)
    if str(network) != APPROVED_NETWORK:
        raise DiagnosticError(
            f"only the approved tracked network is authorized: "
            f"{APPROVED_NETWORK}, got {network}")
    if network.is_symlink() or not network.is_file():
        raise DiagnosticError(f"network must be a regular file: {network}")
    if str(output_root) != APPROVED_OUTPUT_ROOT:
        raise DiagnosticError(
            f"only the approved outcome root is authorized: "
            f"{APPROVED_OUTPUT_ROOT}, got {output_root}")
    root = ROOT / output_root
    if root.exists() or root.is_symlink():
        raise DiagnosticError(
            f"outcome root already exists and is never reused, inspected or "
            f"overwritten: {root}")
    probe = select_probe_edge(network)
    return {
        "network_sha256": sha256_file(network),
        "sumo_binary": str(runner.sumo_binary()),
        "sumo_version": runner.sumo_version(),
        "traci_importable": runner.traci() is not None,
        "outcome_root_absent": True,
        "probe_edge": probe,
    }


def source_fingerprints() -> dict:
    return {name: sha256_file(ROOT / name) for name in sorted([
        "tools/diagnose_warm_state_time_loss_semantics.py",
    ])}


def execute(*, network, output_root, runner, workspace=None) -> dict:
    """Run the three approved arms and write the canonical outcome once."""
    checks = preflight(network=network, output_root=output_root, runner=runner)
    probe = checks["probe_edge"]
    root = ROOT / output_root

    owned_workspace = workspace is None
    workspace = Path(workspace or tempfile.mkdtemp(prefix="warm-semantics-"))
    try:
        route = workspace / "probe.rou.xml"
        route.write_text(build_route_xml(probe["edge_id"]), encoding="utf-8")
        cold_tripinfo = workspace / "cold_tripinfo.xml"
        resumed_tripinfo = workspace / "resumed_tripinfo.xml"
        state = workspace / "prefix_state.xml.gz"
        end_s = probe["expected_travel_s"] * 3 + 60

        binary = runner.sumo_binary()
        cold_cmd = build_command(
            sumo_binary=binary, network_path=network, route_path=route,
            tripinfo_path=cold_tripinfo, end_s=end_s)
        prefix_cmd = build_command(
            sumo_binary=binary, network_path=network, route_path=route,
            tripinfo_path=workspace / "prefix_tripinfo.xml", end_s=end_s)
        resumed_cmd = build_command(
            sumo_binary=binary, network_path=network, route_path=route,
            tripinfo_path=resumed_tripinfo, end_s=end_s,
            begin_s=SAVE_TIME_S, load_state_path=state)

        contract = build_contract(
            network_sha256=checks["network_sha256"],
            sumo_version=checks["sumo_version"], probe=probe,
            source_fingerprints=source_fingerprints(),
            command_shapes={
                "cold": cold_cmd[1:], "prefix": prefix_cmd[1:],
                "resumed": resumed_cmd[1:],
            })

        # ARM 1: uninterrupted.
        cold = runner.run_arm(cmd=cold_cmd, cwd=workspace, label="cold",
                              capture_at_s=SAVE_TIME_S)
        # ARM 2: prefix, capturing and saving at the same step.
        prefix = runner.run_arm(cmd=prefix_cmd, cwd=workspace, label="prefix",
                                capture_at_s=SAVE_TIME_S,
                                save_state_path=state)
        if not state.is_file():
            raise DiagnosticError("the prefix arm wrote no state file")
        # ARM 3: resumed.
        resumed = runner.run_arm(cmd=resumed_cmd, cwd=workspace,
                                 label="resumed", capture_after_load=True)

        cold_trips = parse_tripinfo_time_loss(cold_tripinfo)
        resumed_trips = parse_tripinfo_time_loss(resumed_tripinfo)
        for label, trips in (("cold", cold_trips), ("resumed", resumed_trips)):
            if VEHICLE_ID not in trips:
                raise DiagnosticError(
                    f"the {label} arm produced no tripinfo record for "
                    f"{VEHICLE_ID}; the fixture did not complete")

        boundary = prefix["captured_time_loss_s"]
        if boundary is None or boundary < MIN_BOUNDARY_TIME_LOSS_S:
            raise DiagnosticError(
                f"boundary time loss {boundary} is below the required "
                f"{MIN_BOUNDARY_TIME_LOSS_S} s; the probe was not measurably "
                f"delayed before the snapshot, so the comparison is vacuous")

        observations = {
            "cold_boundary_time_loss_s": cold["captured_time_loss_s"],
            "cold_final_tripinfo_time_loss_s": cold_trips[VEHICLE_ID],
            "prefix_boundary_time_loss_s": boundary,
            "post_load_time_loss_s": resumed["post_load_time_loss_s"],
            "post_load_present": resumed["post_load_present"],
            "post_load_time_s": resumed["post_load_time_s"],
            "resumed_final_tripinfo_time_loss_s": resumed_trips[VEHICLE_ID],
            "saved_at_s": prefix["saved_at_s"],
            "cold_final_time_s": cold["final_time_s"],
            "resumed_final_time_s": resumed["final_time_s"],
        }
        if observations["post_load_time_loss_s"] is None:
            raise DiagnosticError(
                "the probe is absent immediately after loading the state; the "
                "saved state does not contain it and the question is moot")

        # EVERY arm must have been observed exiting cleanly. run_arm already
        # refuses a nonzero code, so this is the belt-and-braces check that the
        # canonical result cannot be written without three EXPLICIT zeros —
        # v1's evidence was rejected precisely because the codes were never
        # looked at, and an unchecked exit is indistinguishable from a clean one.
        return_codes = {"cold": cold.get("return_code"),
                        "prefix": prefix.get("return_code"),
                        "resumed": resumed.get("return_code")}
        for arm, code in sorted(return_codes.items()):
            if code != 0:
                raise DiagnosticError(
                    f"{arm} arm return code is {code!r}, not an observed 0; the "
                    f"canonical result requires three explicit zero exits")

        verdict = classify(observations)
        result = {
            "schema": SCHEMA,
            "diagnostic_id": DIAGNOSTIC_ID,
            "contract_content_key": contract["content_key"],
            "preflight": {k: v for k, v in checks.items()
                          if k != "probe_edge"},
            "return_codes": return_codes,
            "observations": observations,
            "arms": {"cold": cold, "prefix": prefix, "resumed": resumed},
            "verdict": verdict,
            "limitations": [
                "one synthetic vehicle on one edge with no interacting traffic",
                "one SUMO version and one platform",
                "a mechanism observation, NOT equivalence or performance "
                "evidence and not grounds for adoption",
                "does not by itself select the follow-on accounting design",
            ],
        }
        result["content_key"] = canonical_key(result)

        # Publish all-or-nothing into the exact approved root.
        staged = {
            "contract.json": json.dumps(contract, indent=2, sort_keys=True) + "\n",
            "result.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
        }
        root.mkdir(parents=True, exist_ok=False)
        for name, text in sorted(staged.items()):
            (root / name).write_text(text, encoding="utf-8")
        for name, source in (("probe.rou.xml", route),
                             ("cold_tripinfo.xml", cold_tripinfo),
                             ("resumed_tripinfo.xml", resumed_tripinfo),
                             ("prefix_state.xml.gz", state)):
            shutil.copyfile(source, root / name)
        manifest = {"schema": SCHEMA, "diagnostic_id": DIAGNOSTIC_ID,
                    "members": member_manifest(root)}
        manifest["content_key"] = canonical_key(manifest)
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        # VERIFY what was written before reporting success.
        stored_contract = json.loads((root / "contract.json").read_text())
        stored_result = json.loads((root / "result.json").read_text())
        if canonical_key(stored_contract) != contract["content_key"]:
            raise DiagnosticError("stored contract key does not recompute")
        if canonical_key(stored_result) != result["content_key"]:
            raise DiagnosticError("stored result key does not recompute")
        for name, entry in manifest["members"].items():
            if sha256_file(root / name) != entry["sha256"]:
                raise DiagnosticError(f"member digest mismatch after write: {name}")
        if stored_result.get("return_codes") != {"cold": 0, "prefix": 0,
                                                 "resumed": 0}:
            raise DiagnosticError(
                "the stored result does not record three observed zero exits")
        stored_sources = stored_contract["source_fingerprints"]
        if stored_sources != source_fingerprints():
            raise DiagnosticError(
                "the tool changed between contract capture and verification")
        return result
    finally:
        if owned_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--network", default=APPROVED_NETWORK)
    parser.add_argument("--output-root", default=APPROVED_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    if args.preflight == args.execute:
        raise DiagnosticError("pass exactly one of --preflight or --execute")

    runner = SumoRunner()
    if args.preflight:
        checks = preflight(network=args.network, output_root=args.output_root,
                           runner=runner)
        print(json.dumps(checks, indent=2, sort_keys=True))
        return 0

    result = execute(network=args.network, output_root=args.output_root,
                     runner=runner)
    print("classification:", result["verdict"]["classification"])
    for reason in result["verdict"]["reasons"]:
        print("  -", reason)
    print("content key:", result["content_key"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
