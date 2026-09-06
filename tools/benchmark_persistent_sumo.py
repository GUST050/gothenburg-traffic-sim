#!/usr/bin/env python3
"""Persistent-SUMO closure-latency experiment harness (Phase 7 C1).

FAIL-CLOSED, NON-PRODUCTION benchmark. Measures whether a pool of reused,
TraCI-controlled ``sumo`` processes returns the frozen whole-window closure
result below the 10 s ceiling *and* faster than the current fresh-subprocess
path, while producing a SEMANTICALLY IDENTICAL result. It never changes
production, never publishes a scenario/trajectory/manifest/cache/state, and — on
import, contract validation, ``--help`` or any test — never imports
``traci``/libsumo, starts SUMO, opens a socket or creates an outcome. TraCI is
imported lazily only inside ``--execute`` after the full contract + environment
preflight passes, and before any campaign root / port / process.

Equivalence design: BOTH arms run three seeds (q50/q10/q90), parse per-seed SUMO
outputs, and assemble the query's scenario/trajectory artifacts through the
SHARED ``run_scenario.build_scenario_payload`` / ``build_trajectory_payload``
seams that production itself now uses — so a digest match means the persistent
arm's per-seed SUMO outputs equal the subprocess arm's, against the real
production artifact shape. ``run_scenario.py`` is fingerprint-bound, so any
change to those seams invalidates the contract.

Timer boundary: every timed query INCLUDES load/advance/finalize + parse +
aggregation + payload build + digest + validation for that query; only the
one-time pool warm-up is separate (``pool_warmup_queries = 0``).

The experiment is UNEXECUTED and UNAPPROVED. Running it needs a separate Sol
task and fresh exact-key user approval matching the frozen content key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUMO_DIR = ROOT / "sumo"
NET_PATH = SUMO_DIR / "net.net.xml"
# The ONLY path the CLI may run: a contract copied/renamed elsewhere is refused
# even if its content key recomputes (a moved contract is not this experiment).
CANONICAL_CONTRACT = ROOT / "validation" / "persistent_sumo_campaign_v2.json"

# ── Frozen experiment constants ──────────────────────────────────────────────
SCHEMA_VERSION = 1
KIND = "persistent_sumo_experiment"
CURRENT_EXPERIMENT_ID = "persistent_sumo_v2"
RETIRED_EXPERIMENT_IDS = {
    "persistent_sumo_v0": "retired: pre-freeze draft identity, never executable",
    "persistent_sumo_v1": (
        "retired: SPENT 2026-07-24 on a failed experiment (pool members were "
        "bootstrapped without a network, so every seed fell back to a cold "
        "child and no persistent arm existed). Its one attempt is consumed and "
        "may never be rerun; its outcome tree is preserved read-only"),
}

KNOWN_CLOSURE = "26842525_26355153_0"
BEGIN_S = 0
SIM_END_S = 90000                # meso --end = DURATION_S + 3600 flush
DURATION_S = 86400               # demand window; edgeData/spec use this
SEED_WORKERS = 3
POOL_SIZE = 3
POOL_WARMUP_QUERIES = 0
PER_QUERY_TIMEOUT_S = 600
TRIALS = 1
N_INTERVALS = 96

SEED_MEMBER_MAP = (
    {"member": 0, "seed": 1000, "variant": "q50"},
    {"member": 1, "seed": 1001, "variant": "q10"},
    {"member": 2, "seed": 1002, "variant": "q90"},
)
TRAJECTORY_SEED = SEED_MEMBER_MAP[0]["seed"]     # seed 1000 / member 0 / q50
QUERY_ORDER = ("baseline", "closure", "baseline", "closure", "baseline",
               "closure", "baseline", "closure", "baseline", "closure")

MAX_PARALLEL_P95_WALL_S = 10.0
MIN_P95_IMPROVEMENT_FRACTION = 0.04
VALIDATED_COMPLETION_S = 10.0

REQUIRED_FINGERPRINT_LABELS = (
    "harness:benchmark_persistent_sumo.py",
    "source:run_scenario.py",
    "network",
    "demand_meta",
    "calibrated_q50", "calibrated_q10", "calibrated_q90",
)
HARNESS = Path(__file__).resolve()

ADOPTION_AUTHORIZES_NOTHING = (
    "nothing: a passing run is diagnostic evidence for CONSIDERING the "
    "persistent-process arm only. It authorizes no production default change, "
    "API change, deployment, release, publication, or relaxation of any gate.")


class ContractRefused(Exception):
    """Raised when the frozen experiment contract cannot be honoured exactly."""


class ExperimentAborted(Exception):
    """Raised when a preflight or filesystem-safety guard stops an execution."""


class MemberFault(Exception):
    """A pool member errored, hit EOF, timed out or returned invalid output."""


# ── Canonical identity + digests ─────────────────────────────────────────────
def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def content_key(contract: dict) -> str:
    payload = {k: v for k, v in contract.items() if k != "content_key"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def sha256_file(path: Path) -> str | None:
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= set("0123456789abcdef"))


def file_fingerprints() -> dict[str, str | None]:
    paths = {
        "harness:benchmark_persistent_sumo.py": HARNESS,
        "source:run_scenario.py": ROOT / "run_scenario.py",
        "network": SUMO_DIR / "net.net.xml",
        "demand_meta": SUMO_DIR / "demand_meta.json",
        "calibrated_q50": SUMO_DIR / "calibrated.rou.xml",
        "calibrated_q10": SUMO_DIR / "calibrated_v1.rou.xml",
        "calibrated_q90": SUMO_DIR / "calibrated_v2.rou.xml",
    }
    return {label: sha256_file(path) for label, path in paths.items()}


def canonical_digest(payload) -> str:
    """Semantic digest: same rule as tools/benchmark_speed.canonical_digest."""
    def normalise(value):
        if isinstance(value, dict):
            return {k: normalise(v) for k, v in sorted(value.items())
                    if k not in {"generated_at", "created_at", "finished_at"}
                    and k not in {"path", "source_path", "workspace"}}
        if isinstance(value, list):
            return [normalise(v) for v in value]
        return value
    encoded = json.dumps(normalise(payload), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ContractRefused(f"duplicate key in contract JSON: {key!r}")
        seen[key] = value
    return seen


# ── Contract loading and strict validation ───────────────────────────────────
def load_contract(path: Path) -> dict:
    """Load and strictly validate the frozen experiment contract.

    Every refusal happens BEFORE a run directory, TraCI import, socket or
    process. Duplicate JSON keys are rejected during decode, not overwritten.
    """
    try:
        contract = json.loads(Path(path).read_text(),
                              object_pairs_hook=_reject_duplicate_keys)
    except ContractRefused:  # pylint: disable=try-except-raise
        # Redundant only while ContractRefused derives from Exception rather
        # than ValueError. Kept deliberately: _reject_duplicate_keys raises it
        # from INSIDE the decode, and a refusal must reach the caller as
        # itself, never re-wrapped as "cannot read contract".
        raise
    except (OSError, ValueError) as error:
        raise ContractRefused(f"cannot read contract {path}: {error}") from None
    if not isinstance(contract, dict):
        raise ContractRefused("contract must be a JSON object")
    if (contract.get("schema_version") != SCHEMA_VERSION
            or contract.get("kind") != KIND):
        raise ContractRefused("not a persistent-SUMO experiment contract")
    for field in ("experiment_id", "frozen_at", "content_key"):
        if not isinstance(contract.get(field), str) or not contract[field]:
            raise ContractRefused(f"contract is missing {field}")

    experiment_id = contract["experiment_id"]
    if experiment_id in RETIRED_EXPERIMENT_IDS:
        raise ContractRefused(
            f"{experiment_id} is not executable — "
            f"{RETIRED_EXPERIMENT_IDS[experiment_id]}")
    if experiment_id != CURRENT_EXPERIMENT_ID:
        raise ContractRefused(
            f"{experiment_id} is not the executable experiment — only "
            f"{CURRENT_EXPERIMENT_ID} may run")
    if content_key(contract) != contract["content_key"]:
        raise ContractRefused(
            "content key does not recompute — the frozen contract was edited")

    _reject_unknown_keys(contract, ALLOWED_TOP_LEVEL_KEYS, "contract")
    missing = REQUIRED_TOP_LEVEL_KEYS - set(contract)
    if missing:
        raise ContractRefused(
            "contract is missing required field(s): " + ", ".join(sorted(missing)))
    _validate_lineage(contract)
    _validate_execution(contract)
    _validate_matrix(contract)
    _validate_gates(contract)
    _validate_report_schema(contract)
    _validate_fingerprints_present(contract)
    _validate_identity_bindings(contract)
    return contract


def _validate_identity_bindings(contract: dict) -> None:
    for field in ("expected_sumo_version", "expected_platform"):
        v = contract.get(field)
        if not isinstance(v, str) or not v:
            raise ContractRefused(f"contract.{field} must be a non-empty string")
    demand = contract.get("demand_identity")
    if not isinstance(demand, dict) or set(demand) != {"build_id",
                                                       "demand_build_key"}:
        raise ContractRefused(
            "demand_identity must bind exactly build_id and demand_build_key")
    if contract.get("outcomes_present_at_freeze") is not False:
        raise ContractRefused("outcomes_present_at_freeze must be false")


REQUIRED_REPORT_PER_QUERY = ("index", "case", "parallel_wall_s",
                             "scenario_digest", "trajectory_digest",
                             "closure_integrity", "seed_health", "fallbacks",
                             "member_events")
REQUIRED_REPORT_VERDICT = ("persistent_p95_wall_s", "subprocess_p95_wall_s",
                           "p95_improvement_fraction", "member_faults",
                           "fallbacks", "failed_gates", "eligible_and_passed",
                           "authorizes")
# Every key run_experiment actually emits at the report envelope — no emitted
# key may be undeclared, or the schema would not describe the artifact.
REQUIRED_REPORT_MEMBER_EVENT = ("index", "case", "fallbacks", "events")
REQUIRED_REPORT_MEMBER_EVENT_ENTRY = ("member", "seed", "error")
ALLOWED_REPORT_SCHEMA_KEYS = frozenset({
    "per_query", "top_level", "verdict", "member_event", "member_event_entry"})
ALLOWED_LIFECYCLE_KEYS = frozenset({
    "seed_member_binding", "reference_arm", "cold_fallback", "retire_on_fault",
    "cleanup"})


def _validate_report_schema(contract: dict) -> None:
    schema = contract.get("report_schema")
    if not isinstance(schema, dict):
        raise ContractRefused("contract has no report_schema")
    _reject_unknown_keys(schema, ALLOWED_REPORT_SCHEMA_KEYS, "report_schema")
    if set(schema) != ALLOWED_REPORT_SCHEMA_KEYS:
        raise ContractRefused(
            "report_schema must bind top_level, per_query, verdict, "
            "member_event and member_event_entry")
    for field, required in (
            ("top_level", REQUIRED_REPORT_TOP_LEVEL),
            ("per_query", REQUIRED_REPORT_PER_QUERY),
            ("verdict", REQUIRED_REPORT_VERDICT),
            ("member_event", REQUIRED_REPORT_MEMBER_EVENT),
            ("member_event_entry", REQUIRED_REPORT_MEMBER_EVENT_ENTRY)):
        value = schema.get(field)
        if not isinstance(value, list) or set(value) != set(required):
            raise ContractRefused(
                f"report_schema.{field} must list exactly "
                + ", ".join(required))


ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "kind", "experiment_id", "frozen_at", "content_key",
    "purpose", "lineage", "execution", "matrix", "gates", "report_schema",
    "demand_identity", "expected_sumo_version", "expected_platform",
    "outcomes_present_at_freeze", "not_evidence_for", "frozen_fingerprints"})
ALLOWED_EXECUTION_KEYS = frozenset({
    "simulation_mode", "begin_s", "sim_end_s", "seed_workers", "pool_size",
    "pool_warmup_queries", "per_query_timeout_s", "trials", "known_closure",
    "n_intervals", "timer_semantics", "option_template",
    "persistent_arm_only_options", "shared_production_builders",
    "bootstrap_template", "lifecycle"})
ALLOWED_MATRIX_KEYS = frozenset({
    "arms", "query_order", "seed_member_map", "closure"})
ALLOWED_GATES_KEYS = frozenset({
    "max_parallel_p95_wall_s", "min_p95_improvement_fraction", "latency_sample",
    "required_seed_health", "required_closure_integrity",
    "any_fault_or_fallback_is_ineligible", "authorizes"})
ALLOWED_TIMER_KEYS = frozenset({
    "per_query_includes_finalization_reload", "per_query_includes_parse_validate",
    "per_query_includes_payload_build", "excludes_one_time_pool_warmup",
    "parallel_wall_method"})
ALLOWED_CLOSURE_KEYS = frozenset({"edge", "begin", "end"})
REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "kind", "experiment_id", "frozen_at", "content_key",
    "lineage", "execution", "matrix", "gates", "report_schema",
    "demand_identity", "outcomes_present_at_freeze", "frozen_fingerprints"})
REQUIRED_REPORT_TOP_LEVEL = ("schema_version", "experiment_id", "content_key",
                             "pool_warmup_wall_s", "pool_warmup_queries",
                             "member_faults", "member_events",
                             "persistent_queries", "subprocess_queries",
                             "verdict", "note")


def _reject_unknown_keys(obj: dict, allowed: frozenset, where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise ContractRefused(
            f"{where} has unknown field(s): {', '.join(sorted(extra))}")


# ── Exact frozen VALUES bound by the contract (not just field presence) ───────
# Every value below is the authoritative frozen text; a re-keyed mutation of any
# of them is refused. The re-freeze writes these same objects into the contract,
# so the shipped contract satisfies the bindings by construction.
OPTION_TEMPLATE = [
    "--mesosim true", "--meso-junction-control true",
    "--meso-junction-control.limited true", "-n <net>", "-r <route>",
    "-a <additionals>", "--seed <seed>", "--begin 0", "--end 90000",
    "--no-step-log true", "--no-warnings true", "--ignore-route-errors true",
    "--statistic-output <stats>",
    "--vehroute-output <vehroutes> (trajectory seed only)",
]
PERSISTENT_ARM_ONLY_OPTIONS = ["--remote-port <port>", "--num-clients 1"]
# The member bootstrap: SUMO cannot start a TraCI server without a network, so
# the v1 bare `--remote-port/--num-clients` launch died instantly. Bound exactly.
BOOTSTRAP_TEMPLATE = ["<sumo>", "-n <net>", "--remote-port <port>",
                      "--num-clients 1"]
SHARED_PRODUCTION_BUILDERS = [
    "run_scenario.build_scenario_payload",
    "run_scenario.build_trajectory_payload",
]
PARALLEL_WALL_METHOD = (
    "real concurrent span across the three slots, including assemble+digest, "
    "identical for both arms")
LIFECYCLE_TEXT = {
    "cleanup": (
        "try/finally closes sockets and terminates+reaps every member on "
        "success, failure, exception, timeout, keyboard interruption and "
        "partial startup; construction kills a spawned-but-unconnected member; "
        "graceful-close timeout kills then reaps"),
    "cold_fallback": (
        "a faulted seed/query may run once as a fresh child; any fallback "
        "makes the run ineligible"),
    "reference_arm": (
        "exactly three fresh per-seed run_sumo children once per query, never "
        "a full run_scenario.py orchestration"),
    "retire_on_fault": "retire, terminate and reap a faulted member",
    "seed_member_binding": (
        "member 0->1000/q50, 1->1001/q10, 2->1002/q90; no member crosses slots"),
}
ALLOWED_LINEAGE_KEYS = frozenset({"outcome_access", "selected_by", "supersedes"})


def _validate_lineage(contract: dict) -> None:
    lineage = contract.get("lineage")
    if not isinstance(lineage, dict):
        raise ContractRefused("contract has no lineage")
    _reject_unknown_keys(lineage, ALLOWED_LINEAGE_KEYS, "lineage")
    if set(lineage) != ALLOWED_LINEAGE_KEYS:
        raise ContractRefused(
            "lineage must bind exactly outcome_access, selected_by, supersedes")


def _validate_execution(contract: dict) -> None:
    execution = contract.get("execution")
    if not isinstance(execution, dict):
        raise ContractRefused("contract has no execution block")
    _reject_unknown_keys(execution, ALLOWED_EXECUTION_KEYS, "execution")
    expected = {
        "simulation_mode": "meso", "begin_s": BEGIN_S, "sim_end_s": SIM_END_S,
        "seed_workers": SEED_WORKERS, "pool_size": POOL_SIZE,
        "pool_warmup_queries": POOL_WARMUP_QUERIES,
        "per_query_timeout_s": PER_QUERY_TIMEOUT_S, "trials": TRIALS,
        "known_closure": KNOWN_CLOSURE, "n_intervals": N_INTERVALS,
    }
    for field, want in expected.items():
        if execution.get(field) != want:
            raise ContractRefused(
                f"execution.{field} is {execution.get(field)!r}, but this "
                f"harness executes {want!r}")
    timer = execution.get("timer_semantics")
    if not isinstance(timer, dict):
        raise ContractRefused("execution.timer_semantics missing")
    _reject_unknown_keys(timer, ALLOWED_TIMER_KEYS, "execution.timer_semantics")
    # Bind exact VALUES, not just presence: a re-keyed option template, TraCI-
    # only option pair or builder list must be refused.
    if execution.get("option_template") != OPTION_TEMPLATE:
        raise ContractRefused(
            "execution.option_template must be the exact frozen SUMO option "
            "template")
    if execution.get("bootstrap_template") != BOOTSTRAP_TEMPLATE:
        raise ContractRefused(
            "execution.bootstrap_template must be the exact frozen "
            "network-backed member bootstrap")
    if execution.get("persistent_arm_only_options") != PERSISTENT_ARM_ONLY_OPTIONS:
        raise ContractRefused(
            "execution.persistent_arm_only_options must be exactly the frozen "
            "--remote-port 0 / --num-clients 1 pair")
    if execution.get("shared_production_builders") != SHARED_PRODUCTION_BUILDERS:
        raise ContractRefused(
            "execution.shared_production_builders must bind exactly the two "
            "run_scenario payload builders")
    lifecycle = execution.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise ContractRefused("execution.lifecycle missing")
    _reject_unknown_keys(lifecycle, ALLOWED_LIFECYCLE_KEYS, "execution.lifecycle")
    if lifecycle != LIFECYCLE_TEXT:
        raise ContractRefused(
            "execution.lifecycle must be the exact frozen text for "
            "seed_member_binding, reference_arm, cold_fallback, retire_on_fault "
            "and cleanup")
    if (timer.get("per_query_includes_finalization_reload") is not True
            or timer.get("per_query_includes_parse_validate") is not True
            or timer.get("per_query_includes_payload_build") is not True
            or timer.get("excludes_one_time_pool_warmup") is not True):
        raise ContractRefused(
            "timer_semantics must include the finalization reload, parse/"
            "validate and payload build per query and exclude only one-time "
            "pool warm-up")
    if timer.get("parallel_wall_method") != PARALLEL_WALL_METHOD:
        raise ContractRefused(
            "timer_semantics.parallel_wall_method must be the exact frozen "
            "shared-method text")


def _validate_matrix(contract: dict) -> None:
    matrix = contract.get("matrix")
    if not isinstance(matrix, dict):
        raise ContractRefused("contract has no matrix")
    _reject_unknown_keys(matrix, ALLOWED_MATRIX_KEYS, "matrix")
    if list(matrix.get("query_order", [])) != list(QUERY_ORDER):
        raise ContractRefused(
            "matrix.query_order must be the exact ten-query alternating "
            "baseline/closure sequence")
    slots = matrix.get("seed_member_map")
    if [dict(s) for s in (slots or [])] != [dict(s) for s in SEED_MEMBER_MAP]:
        raise ContractRefused(
            "matrix.seed_member_map must bind member 0/1/2 to "
            "seed 1000/q50, 1001/q10, 1002/q90 exactly")
    closure = matrix.get("closure")
    if isinstance(closure, dict):
        _reject_unknown_keys(closure, ALLOWED_CLOSURE_KEYS, "matrix.closure")
    if not isinstance(closure, dict) or closure.get("edge") != KNOWN_CLOSURE \
            or closure.get("begin") != "00:00" or closure.get("end") != "24:00":
        raise ContractRefused(
            "matrix.closure must be the frozen whole-day closure on "
            f"{KNOWN_CLOSURE}")
    if matrix.get("arms") != ["arm_subprocess", "arm_persistent"]:
        raise ContractRefused(
            "matrix.arms must be [arm_subprocess, arm_persistent]")


def _validate_gates(contract: dict) -> None:
    gates = contract.get("gates")
    if not isinstance(gates, dict):
        raise ContractRefused("contract has no gates")
    _reject_unknown_keys(gates, ALLOWED_GATES_KEYS, "gates")
    ceiling = gates.get("max_parallel_p95_wall_s")
    if isinstance(ceiling, int) and not isinstance(ceiling, bool):
        ceiling = float(ceiling)
    if ceiling != MAX_PARALLEL_P95_WALL_S \
            or not 0 < ceiling <= VALIDATED_COMPLETION_S:
        raise ContractRefused(
            "gates.max_parallel_p95_wall_s must be the frozen 10.0 s ceiling")
    improvement = gates.get("min_p95_improvement_fraction")
    if isinstance(improvement, int) and not isinstance(improvement, bool):
        improvement = float(improvement)
    if improvement != MIN_P95_IMPROVEMENT_FRACTION:
        raise ContractRefused(
            "gates.min_p95_improvement_fraction must be the frozen 0.04 floor")
    if gates.get("latency_sample") != "five_persistent_closure_queries":
        raise ContractRefused(
            "gates.latency_sample must be the five persistent closure queries")
    if gates.get("required_seed_health") != {
            "collisions": 0, "teleports": 0, "running_at_end": 0,
            "waiting_at_end": 0, "loaded_equals_inserted": True}:
        raise ContractRefused(
            "gates.required_seed_health must require every health field")
    if gates.get("required_closure_integrity") != "verified_clean":
        raise ContractRefused(
            "gates.required_closure_integrity must be verified_clean")
    if gates.get("any_fault_or_fallback_is_ineligible") is not True:
        raise ContractRefused(
            "gates.any_fault_or_fallback_is_ineligible must be true")
    if gates.get("authorizes") != ADOPTION_AUTHORIZES_NOTHING:
        raise ContractRefused(
            "gates.authorizes must be the literal no-authority statement")


def _validate_fingerprints_present(contract: dict) -> None:
    frozen = contract.get("frozen_fingerprints")
    if not isinstance(frozen, dict):
        raise ContractRefused("contract has no frozen_fingerprints")
    if set(frozen) != set(REQUIRED_FINGERPRINT_LABELS):
        raise ContractRefused(
            "frozen_fingerprints must bind exactly: "
            + ", ".join(REQUIRED_FINGERPRINT_LABELS))
    for label, digest in frozen.items():
        if not _is_sha256(digest):
            raise ContractRefused(f"frozen_fingerprints.{label} is not a sha256")
    demand = contract.get("demand_identity")
    if not isinstance(demand, dict) or not all(
            isinstance(demand.get(k), str) and demand.get(k)
            for k in ("build_id", "demand_build_key")):
        raise ContractRefused("contract has an incomplete demand_identity")


# ── Environment preflight (execute path only) ────────────────────────────────
def sumo_version() -> str | None:
    try:
        import sumo                                          # noqa: PLC0415
        home = Path(sumo.__file__).resolve().parent
        result = subprocess.run([str(home / "bin/sumo"), "--version"],
                                capture_output=True, text=True, timeout=20,
                                check=False)
        if result.returncode != 0:
            return None
        return (result.stdout or result.stderr).strip() or None
    except (ImportError, OSError, subprocess.SubprocessError):
        return None


def verify_environment(contract: dict) -> dict:
    """Refuse execution on any drift. Never imports TraCI or starts SUMO."""
    live = file_fingerprints()
    frozen = contract["frozen_fingerprints"]
    drifted = [label for label in REQUIRED_FINGERPRINT_LABELS
               if live.get(label) != frozen.get(label)]
    if drifted:
        raise ExperimentAborted(
            "input fingerprints drifted from the frozen contract: "
            + ", ".join(sorted(drifted)))
    try:
        meta = json.loads((SUMO_DIR / "demand_meta.json").read_text())
    except (OSError, ValueError) as error:
        raise ExperimentAborted(f"cannot read demand identity: {error}") from None
    demand = contract["demand_identity"]
    if (str(meta.get("build_id")) != demand["build_id"]
            or str(meta.get("demand_build_key")) != demand["demand_build_key"]):
        raise ExperimentAborted("live demand identity differs from the contract")
    expected_sumo = contract.get("expected_sumo_version")
    live_sumo = sumo_version()
    if expected_sumo is not None and live_sumo != expected_sumo:
        raise ExperimentAborted(
            f"SUMO version drift: contract {expected_sumo!r}, live {live_sumo!r}")
    expected_platform = contract.get("expected_platform")
    if expected_platform is not None and platform.platform() != expected_platform:
        raise ExperimentAborted("platform differs from the frozen contract")
    return {"inputs": live, "sumo_version": live_sumo,
            "platform": platform.platform()}


# ── Authoritative SUMO argument builder (shared by both arms) ────────────────
def _seed_route(variant: str) -> Path:
    return SUMO_DIR / {"q50": "calibrated.rou.xml", "q10": "calibrated_v1.rou.xml",
                       "q90": "calibrated_v2.rou.xml"}[variant]


def build_bootstrap_args(*, sumo_bin, net_path, port) -> list[str]:
    """PURE builder for a pool member's TraCI-server bootstrap command.

    A SUMO server cannot start without a network: the v1 harness launched
    `sumo --remote-port <p> --num-clients 1` with no `-n`, so every member exited
    with "Quitting (on error)" and the whole persistent arm silently degenerated
    into cold fallbacks. The network here is only the bootstrap scaffold — each
    TIMED query still `simulation.load`s the full result-affecting argument set
    from `build_sumo_args`, so nothing about a query's inputs comes from here.
    Kept pure (no imports, no process, no socket) so it is testable directly."""
    return [str(sumo_bin),
            "-n", str(Path(net_path).resolve()),
            "--remote-port", str(port),
            "--num-clients", "1"]


def build_sumo_args(*, seed: int, route_path: Path, add_paths: list[Path],
                    edgedata_out: Path, statistics_out: Path,
                    vehroute_out: Path | None, traci_server: bool,
                    closed_edges: list[str] | None = None) -> list[str]:
    """The one command template both arms use; only the trailing TraCI-server
    pair distinguishes the persistent arm. Result-affecting flags are identical
    to run_scenario.run_sumo's, so a difference can only come from execution
    mode. Vehroute output is requested only for the trajectory seed (its path is
    None otherwise), matching run_scenario.

    ``closed_edges`` selects the Stage 3 teleport policy exactly as production
    does — the closure query must run the closure semantics, or "the persistent
    arm reproduced the real production artifact" would be a claim about a
    simulation production no longer performs."""
    args = [
        "--mesosim", "true",
        "--meso-junction-control", "true",
        "--meso-junction-control.limited", "true",
        "-n", str(NET_PATH.resolve()),
        "-r", str(Path(route_path).resolve()),
        "-a", ",".join(str(Path(p).resolve()) for p in add_paths),
        "--seed", str(seed),
        "--begin", str(BEGIN_S),
        "--end", str(SIM_END_S),
        "--no-step-log", "true",
        "--no-warnings", "true",
        "--ignore-route-errors", "true",
        "--statistic-output", str(Path(statistics_out).resolve()),
    ]
    if closed_edges:
        from traffic_sim.simulation import closure_teleport  # noqa: PLC0415
        args += closure_teleport.sumo_arguments(
            closure_teleport.CLOSURE_TIME_TO_TELEPORT_S)
    if vehroute_out is not None:
        args += ["--vehroute-output", str(Path(vehroute_out).resolve()),
                 "--vehroute-output.exit-times", "true",
                 "--vehroute-output.write-unfinished", "true"]
    if traci_server:
        args += ["--remote-port", "0", "--num-clients", "1"]
    return args


def _closure_teleport_policy() -> dict:
    """The provenance record production writes into a closure payload."""
    from traffic_sim.simulation import closure_teleport      # noqa: PLC0415
    return closure_teleport.policy_record(
        closure_teleport.CLOSURE_TIME_TO_TELEPORT_S)


# ── Query matrix ─────────────────────────────────────────────────────────────
def query_matrix(contract: dict) -> list[dict]:
    rows = []
    for index, case in enumerate(QUERY_ORDER):
        rows.append({
            "index": index, "case": case,
            "closed_edges": [KNOWN_CLOSURE] if case == "closure" else [],
            "slots": [dict(slot) for slot in SEED_MEMBER_MAP],
        })
    return rows


# ── Shared payload assembly (both arms; production seams) ─────────────────────
class ScenarioAssembler:
    """Assembles a query's scenario + trajectory digests through the SHARED
    production builders `run_scenario.build_scenario_payload` /
    `build_trajectory_payload`, fed by per-seed SUMO outputs. Both arms use ONE
    instance, so identical per-seed outputs yield identical digests, and a match
    means the persistent arm reproduced the real production artifact.

    `context` carries the metadata both arms share (web_edges, prior, meta,
    spec_for, closures). Injected as a fake in tests; built for real by
    `build_assembler` under --execute.
    """

    def __init__(self, context: dict):
        self.context = context

    def assemble(self, case: str, per_seed: list[dict]) -> dict:
        import numpy as np                                   # noqa: PLC0415
        import run_scenario as rs                            # noqa: PLC0415
        ctx = self.context
        closed_edges = [KNOWN_CLOSURE] if case == "closure" else []
        closures = ctx["closures"] if case == "closure" else []
        per_seed_flows = [{e: np.asarray(v, dtype=float)
                           for e, v in s["flows"].items()} for s in per_seed]
        flows_out, conf_out = rs.aggregate_flows(
            per_seed_flows, set(ctx["web_edges"]), ctx["prior"], N_INTERVALS)
        entries_by_seed = [s.get("active_closure_entries") for s in per_seed]
        entries = integrity = None
        if case == "closure":
            entries = rs.aggregate_active_closure_entries(entries_by_seed,
                                                          closures)
            integrity = rs.closure_integrity_status(entries, closures)
        seed_health = [s["seed_health"] for s in per_seed]
        traj_seed = next((s for s in per_seed if s["seed"] == TRAJECTORY_SEED),
                         None)
        traj = traj_seed.get("trajectory") if traj_seed else None
        traj_payload = None
        traj_name = None
        if traj is not None:
            traj_payload = rs.build_trajectory_payload(
                TRAJECTORY_SEED, traj["variant"], traj["vehicles"],
                traj["n_unfinished"], traj["inserted"], traj["sampling"],
                traj["inv"])
            traj_name = f"{ctx['name_for'](case)}_traj.json"
        spec = ctx["spec_for"](case)
        sensor_audit = ctx["sensor_audit_for"](case, per_seed, flows_out,
                                               per_seed_flows)
        payload = rs.build_scenario_payload(
            meta=ctx["meta"], n_intervals=N_INTERVALS,
            generated_at="",  # stripped by canonical_digest; constant here
            spec=spec, traj_name=traj_name, name=ctx["name_for"](case),
            label=ctx["label_for"](case), close_edges=closed_edges,
            closures=closures, window_label=ctx["window_label"],
            n_truncated=sum(s.get("truncated", 0) for s in per_seed),
            n_dropped=sum(s.get("dropped", 0) for s in per_seed),
            active_closure_entries=entries, active_entries_by_seed=entries_by_seed,
            closure_integrity=integrity, seed_count=len(per_seed),
            seed_values=[s["seed"] for s in per_seed], sig=ctx["sig"],
            seed_health=seed_health, health_flags=rs.seed_health_flags(seed_health),
            multi_day_validation=None, sensor_audit=sensor_audit,
            flows_out=flows_out, conf_out=conf_out,
            # Production attaches the teleport policy to a closure payload and
            # nothing else. Omitting it here would make the two artifacts differ
            # for a reason that has nothing to do with execution mode.
            teleport_policy=(
                _closure_teleport_policy() if closed_edges else None))
        # Production refuses to publish a baseline whose raw SUMO edgeData does
        # not fit the frozen demand targets (`baseline_output_fit_errors` raises
        # in main()). Apply the SAME gate here so neither arm can "match" on an
        # artifact production would never publish. A closure is explanatory, not
        # a calibration gate, so it is exempt exactly as production exempts it.
        fit_errors = ctx["fit_errors_for"](case, sensor_audit)
        if case == "baseline" and fit_errors:
            raise MemberFault(
                "baseline output fit failed (production would refuse to "
                "publish): " + "; ".join(fit_errors[:3]))
        return {
            "scenario_digest": canonical_digest(payload),
            "trajectory_digest": (canonical_digest(traj_payload)
                                  if traj_payload is not None else None),
            "closure_integrity": integrity,
            "seed_health": seed_health,
        }


# ── Pool lifecycle ───────────────────────────────────────────────────────────
class PoolMember:
    def __init__(self, slot: dict, connector, work_dir: Path):
        self.slot = slot
        self.seed = slot["seed"]
        self.variant = slot["variant"]
        self.connector = connector
        self.work_dir = work_dir
        self.alive = True
        self.faulted = False
        self.unreaped = False        # connector abort/close could not reap it

    def abort(self) -> None:
        self.alive = False
        self.faulted = True
        if self.connector is not None:
            try:
                self.connector.abort()
            except Exception:                              # noqa: BLE001
                self.unreaped = True   # record, never silently accept an orphan

    def close(self) -> None:
        self.alive = False
        if self.connector is not None:
            try:
                self.connector.close()
            except Exception:                              # noqa: BLE001
                self.unreaped = True


class PersistentPool:
    def __init__(self, root: Path, connector_factory, clock=time.perf_counter):
        self.root = Path(root)
        self.connector_factory = connector_factory
        self.clock = clock
        self.members: list[PoolMember] = []
        self.warmup_wall_s: float | None = None
        self.faults = 0
        self.unreaped: list[PoolMember] = []

    def _note_unreaped(self, member: PoolMember) -> None:
        if member.unreaped and member not in self.unreaped:
            self.unreaped.append(member)

    def warmup(self) -> None:
        start = self.clock()
        for slot in SEED_MEMBER_MAP:
            work_dir = self.root / f"member-{slot['member']}"
            work_dir.mkdir(parents=True, exist_ok=False)
            connector = self.connector_factory(slot, work_dir)
            self.members.append(PoolMember(slot, connector, work_dir))
        self.warmup_wall_s = self.clock() - start

    def member_for(self, slot_member: int) -> PoolMember:
        member = self.members[slot_member]
        if member.slot["member"] != slot_member:
            raise MemberFault(f"member slot mismatch: {slot_member}")
        return member

    def retire(self, member: PoolMember) -> None:
        member.faulted = True
        self.faults += 1
        member.close()
        self._note_unreaped(member)

    def shutdown(self) -> None:
        for member in self.members:
            member.close()
            self._note_unreaped(member)


def _member_query_prep_paths(work_dir: Path, seed: int, closed_edges):
    add = [work_dir / "edgedata.add.xml"]
    if closed_edges:
        add.append(work_dir / f"closure_{'_'.join(closed_edges)}.add.xml")
    return add


def _run_member_seed(member: PoolMember, query: dict, prep) -> dict:
    """One seed's full persistent work for one query (raises on fault).

    Writes this query's private additionals (edgedata + closure) via the shared
    `prep`, filters the closure demand variant, `simulation.load`s, advances,
    reads live health, finalizes (flush reload), parses per-seed outputs. Returns
    this seed's raw contribution for the assembler. No file is referenced that
    prep did not create."""
    if not member.alive or member.faulted:
        raise MemberFault(f"member {member.slot['member']} not usable")
    try:
        prepared = prep.prepare(member.work_dir, member.seed, member.variant,
                                query)
        add = _member_query_prep_paths(member.work_dir, member.seed,
                                       query["closed_edges"])
        args = build_sumo_args(
            seed=member.seed, route_path=prepared["route"], add_paths=add,
            edgedata_out=member.work_dir / "edgedata.xml",
            statistics_out=member.work_dir / "statistics.xml",
            vehroute_out=(member.work_dir / "vehroutes.xml"
                          if member.seed == TRAJECTORY_SEED else None),
            traci_server=False, closed_edges=query["closed_edges"])
        member.connector.load(args)
        member.connector.advance(SIM_END_S)
        member.connector.collect_live_health()      # diagnostic only
        member.connector.finalize()                 # recurring, TIMED flush
        out = prep.read_outputs(member.work_dir, member.seed, member.variant,
                                query)
    except MemberFault:
        raise
    except Exception as error:                              # noqa: BLE001
        raise MemberFault(str(error)) from error
    return {"member": member.slot["member"], "seed": member.seed,
            "variant": member.variant,
            "truncated": prepared["truncated"], "dropped": prepared["dropped"],
            **out}


def thread_dispatch(jobs: list, timeout_s: int, on_timeout=None) -> list:
    """Run every job() concurrently under ONE query-wide deadline; return one
    PER-JOB outcome so the caller can handle each member independently.

    Each outcome is ``{"ok": True, "value": ...}`` or
    ``{"ok": False, "error": str}``. The whole query still shares a single
    deadline: if any job is unfinished at ``timeout_s`` the members are aborted
    via ``on_timeout`` and a MemberFault is raised (a query-wide fault). A job
    that raises does NOT abort the others — it becomes a per-job ``ok: False``.
    """
    from concurrent.futures import ThreadPoolExecutor, wait  # noqa: PLC0415
    outcomes: list = [None] * len(jobs)
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {pool.submit(job): i for i, job in enumerate(jobs)}
    try:
        _done, not_done = wait(futures, timeout=timeout_s)
        if not_done:
            if on_timeout is not None:
                on_timeout()
            pool.shutdown(wait=False, cancel_futures=True)
            raise MemberFault(f"per-query deadline exceeded after {timeout_s}s")
        for future, index in futures.items():
            exc = future.exception()
            outcomes[index] = ({"ok": False, "error": str(exc)} if exc is not None
                               else {"ok": True, "value": future.result()})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return outcomes


def run_persistent_query(pool, query, assembler, prep, *,
                         dispatch=thread_dispatch, fallback_runner=None,
                         clock=time.perf_counter,
                         timeout_s=PER_QUERY_TIMEOUT_S) -> dict:
    members = [pool.member_for(slot["member"]) for slot in query["slots"]]
    jobs = [(lambda m=m: _run_member_seed(m, query, prep)) for m in members]

    def _abort():
        for member in members:
            member.abort()

    start = clock()
    try:
        outcomes = dispatch(jobs, timeout_s, _abort)
    except MemberFault as deadline:
        # Query-wide deadline: on_timeout already aborted every member. Route it
        # through the SAME recorded ineligible-fallback/event path a per-member
        # fault uses (retire+reap+event+cold fallback), so the run records
        # ineligible evidence rather than crashing the whole experiment. With no
        # cold fallback configured there is no evidence to record — re-raise.
        if fallback_runner is None:
            raise
        events, per_seed = [], []
        for slot, member in zip(query["slots"], members):
            pool.retire(member)
            events.append({"member": slot["member"], "seed": slot["seed"],
                           "error": str(deadline)})
            per_seed.append(fallback_runner(slot, query, reason=str(deadline)))
        row = _assemble_row(query, per_seed, assembler)
        row["parallel_wall_s"] = clock() - start
        row["fallbacks"] = len(per_seed)
        row["member_events"] = events
        return row
    per_seed, events, fell_back = [], [], 0
    for slot, member, outcome in zip(query["slots"], members, outcomes):
        if outcome["ok"]:
            per_seed.append(outcome["value"])
            continue
        # PER-MEMBER fault: retire ONLY this member; the others keep their work.
        pool.retire(member)
        events.append({"member": slot["member"], "seed": slot["seed"],
                       "error": outcome["error"]})
        if fallback_runner is None:
            raise MemberFault(
                f"member {slot['member']} faulted: {outcome['error']}")
        per_seed.append(fallback_runner(slot, query, reason=outcome["error"]))
        fell_back += 1
    row = _assemble_row(query, per_seed, assembler)
    row["parallel_wall_s"] = clock() - start        # includes assemble/digest
    row["fallbacks"] = fell_back
    row["member_events"] = events
    return row


def _close_process_gracefully(proc, timeout_s: int = 5) -> bool:
    """GRACEFUL close, matching the frozen `lifecycle.cleanup` text: a process
    already told to shut down gets a wait window to exit on its own; only on a
    graceful-close TIMEOUT (or any other wait error) do we kill and reap. Return
    True only when the child is proved reaped."""
    try:
        proc.wait(timeout=timeout_s)
        return True
    except Exception:                                      # noqa: BLE001
        return _reap_process(proc)


def _reap_process(proc) -> bool:
    """FORCED kill+reap of one child; return True ONLY when it is PROVED reaped
    — i.e. ``wait()`` RETURNED an exit code. A wait that raises (a timeout, or
    any other unknown-state error) is NOT proof of reap; after two forced
    kill+wait attempts still without a returned code, it is an orphan."""
    for _ in range(2):
        try:
            proc.kill()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=5)
            return True
        except Exception:                                  # noqa: BLE001
            continue        # timeout OR unknown error == unproved; try again
    return False


class ChildRegistry:
    """Query-wide abort handle for the reference arm: real per-seed children
    register their process here so a query deadline can terminate+reap them,
    mirroring the persistent pool's member abort."""

    def __init__(self):
        self.procs = []
        self.unreaped = []

    def register(self, proc):
        self.procs.append(proc)

    @staticmethod
    def _reap(proc) -> bool:
        return _reap_process(proc)

    def abort_all(self):
        # Record any child that could not be proved reaped so callers can fail
        # closed instead of leaking an orphan.
        self.unreaped = [proc for proc in self.procs if not self._reap(proc)]
        return self.unreaped


def run_reference_query(query, reference_runner, assembler, prep, *,
                        dispatch=thread_dispatch, clock=time.perf_counter,
                        timeout_s=PER_QUERY_TIMEOUT_S) -> dict:
    """Launch exactly three fresh per-seed children ONCE, concurrently, then
    assemble via the SAME shared builders — never a full run_scenario.py
    orchestration. A query deadline terminates+reaps every registered child."""
    registry = ChildRegistry()
    jobs = [(lambda slot=slot: reference_runner(slot, query, prep, registry))
            for slot in query["slots"]]
    start = clock()
    try:
        outcomes = dispatch(jobs, timeout_s, registry.abort_all)
    except BaseException as exc:
        # Deadline, worker error surfaced by dispatch, or KeyboardInterrupt:
        # terminate+reap every registered child so none outlives the query. If
        # any child cannot be proved reaped, fail closed on the orphan rather
        # than re-raising the original and leaking it.
        registry.abort_all()
        if registry.unreaped:
            raise ExperimentAborted(
                f"{len(registry.unreaped)} reference child(ren) could not be "
                "reaped on query abort — refusing to leak an orphan") from exc
        raise
    faulted = [o for o in outcomes if not o["ok"]]
    if faulted:
        registry.abort_all()
        if registry.unreaped:
            raise ExperimentAborted(
                f"{len(registry.unreaped)} reference child(ren) could not be "
                "reaped after a fault — refusing to leak an orphan")
        raise MemberFault(
            "reference arm faulted: " + "; ".join(o["error"] for o in faulted))
    per_seed = [o["value"] for o in outcomes]
    row = _assemble_row(query, per_seed, assembler)
    row["parallel_wall_s"] = clock() - start
    row["fallbacks"] = 0
    row["member_events"] = []
    return row


def _assemble_row(query, per_seed, assembler) -> dict:
    assembled = assembler.assemble(query["case"], per_seed)
    return {"index": query["index"], "case": query["case"], **assembled}


# ── Gate evaluation ──────────────────────────────────────────────────────────
def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sample")
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


_EXPECTED_SEEDS = tuple(slot["seed"] for slot in SEED_MEMBER_MAP)
_SEED_TO_QVARIANT = {slot["seed"]: slot["variant"] for slot in SEED_MEMBER_MAP}


_INT_HEALTH_FIELDS = ("loaded", "inserted", "running_at_end", "waiting_at_end",
                      "collisions", "teleports")
# The exact record `run_scenario.parse_seed_health` produces.
_EXACT_HEALTH_KEYS = frozenset({"seed", "variant", *_INT_HEALTH_FIELDS})


def _variant_family(name) -> str | None:
    """Map a seed-health `variant` (a route filename in production, e.g.
    ``calibrated_v1_close_<edge>.rou.xml``, or the bare ``q50``/``q10``/``q90``
    alias in fixtures) to its q-family, so a mislabelled seed↔variant pairing is
    caught regardless of the case-dependent route name."""
    s = str(name)
    if s in ("q50", "q10", "q90"):
        return s
    # Production's real names: `calibrated.rou.xml` and, for a closure,
    # `<stem>_close_<edges>.rou.xml` where stem = Path("calibrated.rou.xml").stem
    # = "calibrated.rou" — i.e. `calibrated.rou_close_<edge>.rou.xml`. Strip the
    # real suffix FIRST, then the closure tag, then the retained ".rou".
    if s.endswith(".rou.xml"):
        s = s[:-len(".rou.xml")]
    s = s.split("_close_", 1)[0]
    if s.endswith(".rou"):
        s = s[:-len(".rou")]
    return {"calibrated": "q50", "calibrated_v1": "q10",
            "calibrated_v2": "q90"}.get(s)


def _seed_health_ok(records: list[dict]) -> bool:
    # Exactly one healthy record per frozen seed — a short/extra/mislabelled
    # seed list is not "healthy", it is unmeasured. Every count must be a
    # non-negative integer (a None/negative is unmeasured, not zero). Each
    # record's variant must be the one frozen for its seed (member binding).
    if not isinstance(records, list) or len(records) != len(_EXPECTED_SEEDS):
        return False
    # Type-guard BEFORE comparing: a non-dict record or a non-integer seed is
    # malformed evidence that must fail this gate, never raise out of it.
    if not all(isinstance(r, dict) for r in records):
        return False
    # EXACTLY the keys production's `parse_seed_health` emits — an extra field
    # is unverified evidence smuggled into a health record.
    if not all(set(r) == _EXACT_HEALTH_KEYS for r in records):
        return False
    seeds = [r.get("seed") for r in records]
    if not all(isinstance(s, int) and not isinstance(s, bool) for s in seeds):
        return False
    if sorted(seeds) != sorted(_EXPECTED_SEEDS):
        return False
    for r in records:
        if _variant_family(r.get("variant")) != _SEED_TO_QVARIANT.get(r.get("seed")):
            return False
        for field in _INT_HEALTH_FIELDS:
            v = r.get(field)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                return False
        if (r["collisions"] or r["teleports"] or r["running_at_end"]
                or r["waiting_at_end"] or r["loaded"] != r["inserted"]):
            return False
    return True


# Every key a completed proof row carries — EXACT, not a subset: an unknown
# field or a missing fallbacks/member_events entry is a malformed row.
_EXACT_ROW_KEYS = frozenset({
    "index", "case", "parallel_wall_s", "scenario_digest", "trajectory_digest",
    "closure_integrity", "seed_health", "fallbacks", "member_events"})
_EXACT_MEMBER_EVENT_ENTRY_KEYS = frozenset(REQUIRED_REPORT_MEMBER_EVENT_ENTRY)


_MEMBER_TO_SEED = {slot["member"]: slot["seed"] for slot in SEED_MEMBER_MAP}


def _member_event_entries_ok(entries) -> bool:
    """A proof row's member_events is a list of exact {member, seed, error}
    entries; an arbitrary dict shape is malformed evidence, not an absence.
    The member/seed pair must be the frozen binding (member 0↔1000, …) — a
    cross-bound pair is corrupt evidence."""
    if not isinstance(entries, list) or len(entries) > len(SEED_MEMBER_MAP):
        return False
    seen_members = set()
    for ev in entries:
        if not isinstance(ev, dict) or set(ev) != _EXACT_MEMBER_EVENT_ENTRY_KEYS:
            return False
        member, seed = ev.get("member"), ev.get("seed")
        if isinstance(member, bool) or member not in _MEMBER_TO_SEED:
            return False
        if isinstance(seed, bool) or _MEMBER_TO_SEED[member] != seed:
            return False
        if not isinstance(ev.get("error"), str) or not ev["error"]:
            return False
        # A query has three FIXED members and each can fault/fall back at most
        # once, so a repeated member is impossible evidence.
        if member in seen_members:
            return False
        seen_members.add(member)
    return True


def _finite_wall(row: dict) -> bool:
    wall = row.get("parallel_wall_s")
    return (not isinstance(wall, bool) and isinstance(wall, (int, float))
            and math.isfinite(wall) and wall > 0)


def _row_schema_ok(row: dict, *, arm: str = "persistent") -> bool:
    if set(row) != _EXACT_ROW_KEYS:
        return False
    if not _finite_wall(row):
        return False
    if not isinstance(row.get("fallbacks"), int) or isinstance(row["fallbacks"], bool) \
            or row["fallbacks"] < 0:
        return False
    if not _member_event_entries_ok(row.get("member_events")):
        return False
    # Every recorded fault event corresponds to exactly one fallback (the run
    # loop appends an event and a fallback together); a fault event with zero
    # fallbacks — or the reverse — is inconsistent evidence.
    if len(row["member_events"]) != row["fallbacks"]:
        return False
    # The REFERENCE arm has no cold fallback at all — `run_reference_query`
    # always emits fallbacks=0 / member_events=[]. Any other value is
    # impossible evidence, not a tolerable variation.
    if arm == "reference" and (row["fallbacks"] or row["member_events"]):
        return False
    return True


def evaluate(persistent_rows, reference_rows, *, member_faults, fallbacks):
    findings: list[str] = []

    def by_index(rows):
        indexed = {}
        if not isinstance(rows, list):
            findings.append("malformed_rows")
            return indexed
        for row in rows:
            # A non-dict row, or one whose index is not a plain int, is
            # malformed evidence — record it and drop it rather than letting
            # `.get`/an unhashable key raise out of the gate.
            if not isinstance(row, dict):
                findings.append("malformed_row")
                continue
            key = row.get("index")
            # Must be a plain int IN RANGE: an out-of-range index would either
            # raise from `QUERY_ORDER[i]` during sampling, or (when negative)
            # silently wrap onto the wrong query.
            if isinstance(key, bool) or not isinstance(key, int) \
                    or not 0 <= key < len(QUERY_ORDER):
                findings.append("malformed_row")
                continue
            if key in indexed:
                findings.append("duplicate_query")
            indexed[key] = row
        return indexed

    ref = by_index(reference_rows)
    per = by_index(persistent_rows)
    expected = set(range(len(QUERY_ORDER)))
    if set(ref) != expected:
        findings.append("reference_incomplete")
    if set(per) != expected:
        findings.append("persistent_incomplete")

    for index in sorted(expected & set(per) & set(ref)):
        p, r = per[index], ref[index]
        if not _row_schema_ok(p, arm="persistent") \
                or not _row_schema_ok(r, arm="reference"):
            findings.append(f"malformed_row:{index}")
            continue
        if p.get("case") != QUERY_ORDER[index] or r.get("case") != QUERY_ORDER[index]:
            findings.append(f"cross_paired:{index}")
            continue
        # Both scenario AND trajectory digests are required for EVERY query:
        # the representative seed (1000) produces a vehroute in baseline and
        # closure alike, so a missing trajectory is missing evidence, not a
        # legitimate absence.
        for field in ("scenario_digest", "trajectory_digest"):
            pv, rv = p.get(field), r.get(field)
            if not _is_sha256(pv) or not _is_sha256(rv):
                findings.append(f"digest_missing:{field}:{index}")
            elif pv != rv:
                findings.append(f"digest_mismatch:{field}:{index}")
        if not _seed_health_ok(p.get("seed_health") or []) \
                or not _seed_health_ok(r.get("seed_health") or []):
            findings.append(f"seed_health:{index}")
        if QUERY_ORDER[index] == "closure":
            if p.get("closure_integrity") != "verified_clean" \
                    or r.get("closure_integrity") != "verified_clean":
                findings.append(f"closure_integrity:{index}")
        # A BASELINE query closes no edge, so production's assembler leaves
        # closure_integrity None. Any closure proof on a baseline row is
        # evidence for a closure that never existed.
        elif p.get("closure_integrity") is not None \
                or r.get("closure_integrity") is not None:
            findings.append(f"closure_integrity:{index}")

    if member_faults:
        findings.append("member_fault")
    if fallbacks:
        findings.append("fallback_used")

    # Per-row evidence must reconcile EXACTLY with the reported global counters
    # (`run_experiment` sums the same rows), and a recorded fault event without
    # a counted member fault is impossible.
    row_fallbacks = sum(row["fallbacks"] for row in per.values()
                        if isinstance(row.get("fallbacks"), int)
                        and not isinstance(row.get("fallbacks"), bool))
    row_events = sum(len(row["member_events"]) for row in per.values()
                     if isinstance(row.get("member_events"), list))
    if not isinstance(fallbacks, int) or isinstance(fallbacks, bool) \
            or row_fallbacks != fallbacks:
        findings.append("fallback_count_mismatch")
    if not isinstance(member_faults, int) or isinstance(member_faults, bool) \
            or member_faults < row_events:
        findings.append("member_fault_count_mismatch")

    # Only finite numeric closure walls enter the sample — a malformed/string
    # wall must fail the verdict (latency_sample_incomplete), never crash
    # percentile arithmetic with a TypeError.
    closure_walls = [per[i]["parallel_wall_s"] for i in sorted(per)
                     if QUERY_ORDER[i] == "closure" and _finite_wall(per[i])]
    ref_closure = [ref[i]["parallel_wall_s"] for i in sorted(ref)
                   if QUERY_ORDER[i] == "closure" and _finite_wall(ref[i])]
    per_p95 = ref_p95 = improvement = None
    if len(closure_walls) == 5 and len(ref_closure) == 5:
        per_p95 = percentile(closure_walls, 0.95)
        ref_p95 = percentile(ref_closure, 0.95)
        improvement = (ref_p95 - per_p95) / ref_p95 if ref_p95 else None
        if per_p95 > MAX_PARALLEL_P95_WALL_S:
            findings.append("parallel_latency_ceiling")
        if improvement is None or improvement < MIN_P95_IMPROVEMENT_FRACTION:
            findings.append("p95_improvement_floor")
    else:
        findings.append("latency_sample_incomplete")

    return {
        "persistent_p95_wall_s": per_p95, "subprocess_p95_wall_s": ref_p95,
        "p95_improvement_fraction": improvement, "member_faults": member_faults,
        "fallbacks": fallbacks, "failed_gates": sorted(set(findings)),
        "eligible_and_passed": not findings,
        "authorizes": ADOPTION_AUTHORIZES_NOTHING,
    }


# ── Orchestration ────────────────────────────────────────────────────────────
def run_experiment(contract, root, *, connector_factory, reference_runner,
                   assembler, prep, fallback_runner=None,
                   dispatch=thread_dispatch, reference_dispatch=None,
                   clock=time.perf_counter) -> dict:
    reference_dispatch = reference_dispatch or dispatch
    queries = query_matrix(contract)
    pool = PersistentPool(root, connector_factory, clock=clock)
    persistent_rows, reference_rows = [], []
    body_exc = None
    try:
        pool.warmup()
        for query in queries:
            reference_rows.append(run_reference_query(
                query, reference_runner, assembler, prep,
                dispatch=reference_dispatch, clock=clock))
            persistent_rows.append(run_persistent_query(
                pool, query, assembler, prep, dispatch=dispatch,
                fallback_runner=fallback_runner, clock=clock))
    except BaseException as exc:                            # noqa: BLE001
        body_exc = exc
    finally:
        pool.shutdown()

    # An un-reaped persistent member is terminal — surface it EVEN WHEN the body
    # is already raising (an in-flight fault must not suppress a leaked orphan),
    # chaining the original cause. Otherwise re-raise the in-flight exception.
    if pool.unreaped:
        raise ExperimentAborted(
            f"{len(pool.unreaped)} persistent member(s) could not be reaped on "
            "shutdown — refusing to leak an orphan") from body_exc
    if body_exc is not None:
        raise body_exc

    fallbacks = sum(r.get("fallbacks", 0) for r in persistent_rows)
    verdict = evaluate(persistent_rows, reference_rows,
                       member_faults=pool.faults, fallbacks=fallbacks)
    member_events = [
        {"index": r["index"], "case": r["case"], "fallbacks": r.get("fallbacks", 0),
         "events": r.get("member_events", [])}
        for r in persistent_rows if r.get("member_events") or r.get("fallbacks", 0)]
    report = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": contract["experiment_id"],
        "content_key": contract["content_key"],
        "pool_warmup_wall_s": pool.warmup_wall_s,
        "pool_warmup_queries": POOL_WARMUP_QUERIES,
        "member_faults": pool.faults,
        "member_events": member_events,
        "persistent_queries": persistent_rows,
        "subprocess_queries": reference_rows,
        "verdict": verdict,
        "note": "diagnostic performance evidence only; authorizes nothing",
    }
    (Path(root) / "persistent_sumo_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True))
    return report


# ── Filesystem safety ────────────────────────────────────────────────────────
def prepare_campaign_root(root: Path) -> Path:
    root = Path(root)
    if ".." in root.parts:
        raise ExperimentAborted("campaign root must not contain '..'")
    if root.is_symlink():
        raise ExperimentAborted(f"campaign root is a symlink: {root}")
    if root.exists():
        raise ExperimentAborted(f"campaign root already exists: {root}")
    # A symlink ANYWHERE in the ancestor chain would redirect the whole run tree
    # outside the intended location — refuse it, not just a symlinked leaf.
    absolute = root if root.is_absolute() else Path.cwd() / root
    for ancestor in absolute.parents:
        if ancestor.is_symlink():
            raise ExperimentAborted(
                f"campaign root path crosses a symlink: {ancestor}")
    resolved = root.resolve()
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


# ── CLI ──────────────────────────────────────────────────────────────────────
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Persistent-SUMO closure-latency experiment (fail-closed).")
    p.add_argument("--campaign", required=True, type=Path)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-contract-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    p.add_argument("--artifact-dir", type=Path)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if Path(args.campaign).resolve() != CANONICAL_CONTRACT.resolve():
        print("contract refused: --campaign must be the canonical "
              f"{CANONICAL_CONTRACT.name} at its frozen path, not a copy or "
              "rename", file=sys.stderr)
        return 2
    try:
        contract = load_contract(args.campaign)
    except ContractRefused as refusal:
        print(f"contract refused: {refusal}", file=sys.stderr)
        return 2
    if args.validate_contract_only:
        print(json.dumps({
            "experiment_id": contract["experiment_id"],
            "content_key": contract["content_key"],
            "queries_planned": len(QUERY_ORDER), "pool_size": POOL_SIZE,
            "executed": False, "artifact_dir": None,
            "note": "contract-only validation: nothing was run and nothing "
                    "was created",
        }, indent=2, sort_keys=True))
        return 0
    if args.artifact_dir is None:
        print("contract refused: --execute requires --artifact-dir",
              file=sys.stderr)
        return 2
    try:
        verify_environment(contract)
        _execute(contract, args.artifact_dir)
    except (ExperimentAborted, ImportError, OSError) as abort:
        print(f"experiment aborted: {abort}", file=sys.stderr)
        return 2
    return 0


def _import_traci():  # pragma: no cover - only reached under --execute
    import sumo                                              # noqa: PLC0415
    home = Path(sumo.__file__).resolve().parent
    tools_dir = home / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import traci                                             # noqa: PLC0415
    return traci


# ── Real drivers (implemented; exercised only under --execute) ───────────────
class _SharedPrep:  # pragma: no cover - only under --execute
    """Real query preparation + per-seed output parsing, reusing run_scenario's
    own writers/filters/parsers so both arms are result-faithful.

    `structured_closures` (whole-run form) supplies the `begin_s`/`end_s` the
    active-closure measurement requires; `parse_edgedata` retains the required
    measured-zero closed edges; the trajectory is read from the FILTERED route
    that was actually simulated, over the real web_edges, with production's
    vehroute/inserted reconciliation before it becomes evidence.
    """

    def __init__(self, web_edges, duration_s, epoch):
        self.web_edges = set(web_edges)
        self.duration_s = duration_s
        self.epoch = epoch
        # cache of {work_dir: filtered_route_path} so read_outputs reads the
        # SAME route prepare() selected (and reports its filename as the variant).
        self._selected_route = {}

    def measurement_closures(self, close_edges):
        """Whole-run internal closures ({edge_id, begin_s, end_s}) the
        active-closure measurement and the additional writer both require. The
        whole-edge form goes in `whole_edges` (arg 2), NOT `raw` (JSON)."""
        import run_scenario as rs                            # noqa: PLC0415
        return rs.structured_closures([], list(close_edges), self.epoch,
                                      self.duration_s)

    def prepare(self, work_dir, seed, variant, query):
        """Write additionals, filter the closure variant, and RETURN the
        (route, truncated, dropped) that production accumulates into the payload.

        The edgeData window is ``DURATION_S`` (86 400, production's demand
        window), NOT ``SIM_END_S`` (90 000, the meso-flush --end). The filtered
        route is named exactly as production: ``<stem>_<name>.rou.xml`` with
        ``name = "close_" + "+".join(close_edges)``.
        """
        import run_scenario as rs                            # noqa: PLC0415
        work_dir = Path(work_dir)
        rs.write_edgedata_additional(work_dir / "edgedata.add.xml",
                                     work_dir / "edgedata.xml", self.duration_s)
        route = _seed_route(variant)
        truncated = dropped = 0
        if query["closed_edges"]:
            close = query["closed_edges"]
            name = "close_" + "+".join(close)
            rerouter = rs.edges_near(close, rs.REROUTER_RADIUS_M)
            rs.write_closure_additional(
                work_dir / f"closure_{'_'.join(close)}.add.xml",
                self.measurement_closures(close), rerouter)
            filtered = work_dir / f"{route.stem}_{name}.rou.xml"
            adj = rs.build_edge_graph(set(close))
            truncated, dropped = rs.truncate_stranded_vehicles(
                route, close, filtered, adj)
            route = filtered
        self._selected_route[str(work_dir)] = route
        return {"route": route, "truncated": truncated, "dropped": dropped}

    def read_outputs(self, work_dir, seed, variant, query):
        import numpy as np                                   # noqa: PLC0415
        import run_scenario as rs                            # noqa: PLC0415
        import closure_metrics as cm                         # noqa: PLC0415
        work_dir = Path(work_dir)
        close = query["closed_edges"]
        route = self._selected_route.get(str(work_dir), _seed_route(variant))
        arrays = rs.parse_edgedata(work_dir / "edgedata.xml", N_INTERVALS,
                                   measured_empty_edges=close)
        flows = {e: series.tolist() for e, series in arrays.items()}
        # Production keys seed health by the ACTUAL route filename (the filtered
        # route for a closure), not the q50/q10/q90 alias.
        health = rs.parse_seed_health(work_dir / "statistics.xml", seed,
                                      route.name)
        # Per-seed active-closure throughput (production measures each seed
        # before aggregation, so a single leaking seed cannot be averaged away).
        entries = None
        if close:
            entries = cm.active_closure_throughput(
                {e: np.asarray(v, dtype=float) for e, v in flows.items()},
                self.measurement_closures(close))
        trajectory = None
        vr = work_dir / "vehroutes.xml"
        if seed == TRAJECTORY_SEED and vr.exists():
            endpoints = rs.load_agent_endpoint_positions(route)
            edge_index, vehicles, n_in_file, n_unf, sampling = \
                rs.parse_vehroute_file(vr, self.web_edges,
                                       endpoint_positions=endpoints,
                                       return_sampling=True)
            inserted = health.get("inserted") if health else None
            if inserted and n_in_file / inserted < 0.98:
                trajectory = None            # production withholds a short file
            else:
                vehicles.sort(key=lambda v: v["d"])
                inv = [None] * len(edge_index)
                for e, i in edge_index.items():
                    inv[i] = e
                trajectory = {"vehicles": vehicles, "n_unfinished": n_unf,
                              "inserted": inserted, "sampling": sampling,
                              "inv": inv, "variant": route.name}
        return {"flows": flows, "seed_health": health,
                "active_closure_entries": entries, "trajectory": trajectory}


class _TraciConnector:
    """Spawn+connect are injectable so the lifecycle (including the failed-
    connect cleanup and un-reapable close) is testable without SUMO/TraCI."""

    def __init__(self, slot, work_dir, traci_mod, port, *, spawn=None,
                 connect=None):
        self.slot = slot
        self.work_dir = Path(work_dir)
        self.traci = traci_mod
        self.port = port
        self.proc = None
        self.conn = None
        self._spawn = spawn or self._default_spawn
        self._do_connect = connect or self._default_connect
        try:
            self.proc = self._spawn()
            self.conn = self._do_connect(self.proc)
        except BaseException:
            # A spawned-but-unconnected process MUST be reaped; if it cannot be,
            # that orphan is terminal and supersedes the connect failure — never
            # discard the cleanup failure.
            if self.proc is not None and not _reap_process(self.proc):
                raise ExperimentAborted(
                    f"member {self.slot['member']} could not be reaped after a "
                    "failed connect — refusing to leak an orphan")
            raise

    def _default_spawn(self):
        # Network-backed bootstrap via the pure builder; `sumo` is imported
        # lazily here and nowhere at module load.
        import sumo                                          # noqa: PLC0415
        home = Path(sumo.__file__).resolve().parent
        args = build_bootstrap_args(sumo_bin=home / "bin/sumo",
                                    net_path=NET_PATH, port=self.port)
        return subprocess.Popen(args, cwd=str(self.work_dir),
                                start_new_session=True)

    def _default_connect(self, proc):  # pragma: no cover - only under --execute
        return self.traci.connect(port=self.port, proc=proc)

    def load(self, args):  # pragma: no cover - only under --execute
        self.conn.load(list(args))

    def advance(self, end_s):  # pragma: no cover - only under --execute
        self.conn.simulationStep(end_s)

    def collect_live_health(self):  # pragma: no cover - only under --execute
        return {"running": self.conn.simulation.getMinExpectedNumber()}

    def finalize(self):  # pragma: no cover - only under --execute
        self.conn.load(["-n", str(NET_PATH.resolve()), "--end", "1"])

    def _drop_connection(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:                              # noqa: BLE001
                pass

    def abort(self):
        """FORCED termination (fault/deadline/partial startup): kill then reap."""
        self._drop_connection()
        if self.proc is None:
            return
        if not _reap_process(self.proc):
            raise MemberFault(
                f"member {self.slot['member']} SUMO process could not be reaped")

    def close(self):
        """GRACEFUL close: drop the socket, let SUMO exit within the wait
        window, and only kill+reap if that window times out — exactly the frozen
        `lifecycle.cleanup` contract. An unprovable reap is surfaced, never
        swallowed (PoolMember/PersistentPool then fail the run closed)."""
        self._drop_connection()
        if self.proc is None:
            return
        if not _close_process_gracefully(self.proc):
            raise MemberFault(
                f"member {self.slot['member']} SUMO process could not be reaped")


def _free_port():  # pragma: no cover
    """Bind a socket to port 0, read the OS-assigned free port, release it.

    A hard-coded base port collides when two runs overlap; a freshly-allocated
    port per member gives private, collision-safe startup. There is an inherent
    TOCTOU window, but each member owns its own port and a bind failure surfaces
    as a member fault the pool retires."""
    import socket                                           # noqa: PLC0415
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def real_connector_factory(traci_mod):  # pragma: no cover
    def factory(slot, work_dir):
        return _TraciConnector(slot, work_dir, traci_mod, port=_free_port())
    return factory



def _sumo_bin():  # pragma: no cover
    import sumo                                              # noqa: PLC0415
    return str(Path(sumo.__file__).resolve().parent / "bin/sumo")


def real_reference_runner(root, *, launch=None, dir_prefix="ref"):
    """Launch exactly three fresh per-seed SUMO children once, each via the
    SHARED `build_sumo_args` builder (traci_server=False) and a registered
    subprocess so a query deadline can terminate+reap it; parse per-seed outputs.
    No run_scenario.py orchestration. `launch` is injectable for tests.
    `dir_prefix` keeps a cold fallback's child in its OWN work directory so it
    can never overwrite the reference arm's preserved evidence for that query."""
    launch = launch or _launch_child

    def runner(slot, query, prep, registry):
        wd = Path(root) / f"{dir_prefix}-q{query['index']}-seed-{slot['seed']}"
        wd.mkdir(parents=True, exist_ok=True)
        prepared = prep.prepare(wd, slot["seed"], slot["variant"], query)
        add = _member_query_prep_paths(wd, slot["seed"], query["closed_edges"])
        args = build_sumo_args(
            seed=slot["seed"], route_path=prepared["route"], add_paths=add,
            edgedata_out=wd / "edgedata.xml", statistics_out=wd / "statistics.xml",
            vehroute_out=(wd / "vehroutes.xml"
                          if slot["seed"] == TRAJECTORY_SEED else None),
            traci_server=False, closed_edges=query["closed_edges"])
        proc = launch(args, wd)
        registry.register(proc)
        rc = proc.wait(timeout=PER_QUERY_TIMEOUT_S)
        if rc != 0:
            raise MemberFault(f"reference seed {slot['seed']} exited {rc}")
        out = prep.read_outputs(wd, slot["seed"], slot["variant"], query)
        return {"member": slot["member"], "seed": slot["seed"],
                "variant": slot["variant"],
                "truncated": prepared["truncated"],
                "dropped": prepared["dropped"], **out}
    return runner


def _launch_child(args, work_dir):  # pragma: no cover
    return subprocess.Popen([_sumo_bin(), *args], cwd=str(work_dir),
                            start_new_session=True)


def real_fallback_runner(root, prep, *, launch=None):
    """A cold fallback is one fresh reference child. Its throwaway registry is
    wrapped in cleanup: on ANY failure the child is terminated+reaped, and an
    un-reapable child is surfaced as an orphan `ExperimentAborted` rather than
    discarded with the registry. `launch` is injectable for tests."""
    reference = real_reference_runner(root, launch=launch, dir_prefix="fallback")

    def runner(slot, query, reason):
        registry = ChildRegistry()
        try:
            return reference(slot, query, prep, registry)
        except BaseException as exc:
            registry.abort_all()
            if registry.unreaped:
                raise ExperimentAborted(
                    f"fallback child for seed {slot['seed']} could not be "
                    "reaped — refusing to leak an orphan") from exc
            raise
    return runner


def _real_closures():  # pragma: no cover
    """Whole-run closure with begin_s/end_s the measurement/spec need. The
    whole-edge form goes in `whole_edges` (arg 2), never `raw` (JSON)."""
    import run_scenario as rs                                # noqa: PLC0415
    meta = json.loads((SUMO_DIR / "demand_meta.json").read_text())
    return rs.structured_closures([], [KNOWN_CLOSURE], meta["epoch_sim"],
                                  DURATION_S)


def build_assembler_context():  # pragma: no cover
    import pandas as pd                                      # noqa: PLC0415
    import run_scenario as rs                                # noqa: PLC0415
    prior, names = rs.load_geojson_meta()
    meta = json.loads((SUMO_DIR / "demand_meta.json").read_text())
    epoch = meta["epoch_sim"]
    network_id = sha256_file(NET_PATH)
    sig = meta.get("demand_signature") or str(meta.get("build_id") or "")
    measurement_closures = _real_closures()
    spec_cache = {}

    def spec_for(case):
        if case not in spec_cache:
            name = ("close_" + KNOWN_CLOSURE if case == "closure" else "baseline")
            closures = (rs.contract_closures(measurement_closures, epoch,
                                             DURATION_S)
                        if case == "closure" else [])
            spec_cache[case] = rs.ScenarioSpec.from_dict({
                "scenario_id": name,
                "demand_build_id": str(meta.get("build_id") or sig),
                "network_build_id": network_id,
                "start_time": epoch,
                "end_time": (pd.Timestamp(epoch)
                             + pd.Timedelta(seconds=DURATION_S)).isoformat(),
                "closures": closures,
                "simulation_mode": "meso", "seed_set": [1000, 1001, 1002],
                "demand_variant_mapping": {"1000": "q50", "1001": "q10",
                                           "1002": "q90"}})
        return spec_cache[case]

    def sensor_audit_for(case, per_seed, flows_out, per_seed_arrays):
        # Reproduce production exactly: results carry array flows + health +
        # route_path, and raw_mean_flows is the per-sensor-edge ensemble mean.
        results = [{"seed": s["seed"], "flows": arr, "health": s["seed_health"],
                    "route_path": _seed_route(s["variant"])}
                   for s, arr in zip(per_seed, per_seed_arrays)]
        raw_mean_flows = {
            info["edge_id"]: rs._raw_mean_series(results, info["edge_id"],
                                                 N_INTERVALS)
            for info in rs.sensor_audit_edges()}
        return rs.build_sensor_audit(
            meta, results, flows_out, N_INTERVALS,
            raw_mean_flows=raw_mean_flows, representative_seed=TRAJECTORY_SEED,
            calibration_comparison=(case != "closure"))

    def label_for(case):
        if case == "closure":
            streets = sorted({names[KNOWN_CLOSURE]}) if KNOWN_CLOSURE in names \
                else [KNOWN_CLOSURE]
            return "Avstängning: " + ", ".join(streets)
        return "Baslinje (ingen avstängning)"

    def fit_errors_for(case, sensor_audit):
        closures = (rs.contract_closures(measurement_closures, epoch, DURATION_S)
                    if case == "closure" else [])
        return rs.baseline_output_fit_errors(meta, sensor_audit,
                                             n_intervals=N_INTERVALS,
                                             closures=closures)

    return {
        "web_edges": set(prior), "prior": prior, "meta": meta, "sig": sig,
        "window_label": rs.demand_window_label(meta),
        "sensor_audit_for": sensor_audit_for,
        "fit_errors_for": fit_errors_for,
        "closures": measurement_closures,
        "spec_for": spec_for,
        "name_for": lambda case: ("close_" + KNOWN_CLOSURE if case == "closure"
                                  else "baseline"),
        "label_for": label_for,
    }


def _execute(contract, artifact_dir):  # pragma: no cover
    """Run the frozen experiment once, under recorded exact-key approval.

    TraCI + all executable dependencies are imported/validated here AFTER the
    contract+environment preflight (in main) and BEFORE the campaign root, port
    or process. Reached only via --execute; tests drive `run_experiment` with
    fakes.
    """
    import run_scenario as rs                                # noqa: PLC0415
    traci_mod = _import_traci()                     # after preflight, before root
    prior, _names = rs.load_geojson_meta()
    meta = json.loads((SUMO_DIR / "demand_meta.json").read_text())
    prep = _SharedPrep(set(prior), DURATION_S, meta["epoch_sim"])
    assembler = ScenarioAssembler(build_assembler_context())
    root = prepare_campaign_root(artifact_dir)
    run_experiment(
        contract, root,
        connector_factory=real_connector_factory(traci_mod),
        reference_runner=real_reference_runner(root),
        assembler=assembler, prep=prep,
        fallback_runner=real_fallback_runner(root, prep))


if __name__ == "__main__":
    raise SystemExit(main())
