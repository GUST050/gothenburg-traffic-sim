"""SUMO backend for the resumable monthly closure-search orchestrator."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
import hashlib
import inspect
import json
import math
import os
import platform
import shutil
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import run_scenario as rs
import suggest_closure_time as legacy
from traffic_sim.core.contracts import (
    ClosureSchedule,
    ClosureSearchSpec,
    DemandBuildSpec,
)
from traffic_sim.core.fingerprint import sha256_file, sumo_version
from traffic_sim.simulation import closure_teleport
from traffic_sim.simulation import metrics as closure_metrics
# Re-exported so every existing importer of these two names is unchanged.
# They live in a dependency-free module because the closure-search CLI reads
# the seed-worker budget before any simulation stack needs to exist.
from traffic_sim.simulation.seed_worker_budget import (  # noqa: F401
    SEED_WORKER_BENCHMARK_RECORD,
    approved_seed_workers,
)
# PR D. The variant-to-route mapping and the interval-to-seconds conversion now
# live in the process-free cost module, so the pre-SUMO and post-SUMO paths
# cannot drift apart on "which file is q10" or "when is this edge shut".
from traffic_sim.simulation.deterministic_disruption import (  # noqa: F401
    VARIANT_FILENAMES,
    closure_seconds as _deterministic_closure_seconds,
)
from traffic_sim.simulation.envelope import (
    EnvelopePolicy,
    RecoveryBucket,
    build_simulation_envelope,
    evaluate_recovery,
    read_edgedata_time_loss,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    DEMAND_VARIANTS,
    PairedObservation,
)
from traffic_sim.simulation.monthly_search import canonical_seed
from traffic_sim.simulation.monthly_warm_state import (  # noqa: E402
    PREFIX_EVIDENCE_SCHEMA, audit_route_mutation, build_monthly_observation,  # noqa: E501
    build_prefix_evidence, concatenate_recovery_buckets,
    evaluate_warm_eligibility, monthly_warm_identity, parse_prefix_evidence,
    plan_warm_execution, reconstruct_metrics, route_safe_warm_point)
from traffic_sim.simulation.warm_state_cache import (  # noqa: E402
    STATE_PRECISION, STATE_RNG_SAVED, restore_warm_state)
from traffic_sim.simulation.warm_state_boundary import (  # noqa: E402
    MESO_TIMELOSS_PRECISION, SNAPSHOT_FACTS_SCHEMA, TRIPINFO_PRECISION,
    WARM_OUTPUT_PRECISION,
    aggregate_split_time_loss, build_prefix_accumulator,
    reconcile_resumed_tripinfo, snapshot_settings_arguments)
from traffic_sim.simulation.warm_state_forensics import (  # noqa: E402
    ForensicObservationError)
from traffic_sim.simulation.warm_route_windows import (  # noqa: E402
    WarmRouteWindowCache,
)


SCHEMA_VERSION = 1
DEFAULT_BASELINE_CACHE = Path("runs") / "closure-search-baselines"
WARM_POST_FLUSH_S = 3600


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_record(path: Path, *, label: str) -> dict[str, Any]:
    digest = sha256_file(path)
    if digest is None:
        raise FileNotFoundError(path)
    return {
        "label": label,
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


def _write_tripinfo_view(source: Path, destination: Path,
                         values: Mapping[str, float], *,
                         precision: int = TRIPINFO_PRECISION) -> None:
    """Write a validation-only record view with canonical whole-trip loss.

    SUMO's high-precision files are transport artifacts. The forensic observer
    must see the same per-vehicle values production reports after the warm path joins
    two halves, and the completed-prefix view must exclude active vehicles.
    """
    root = ET.parse(source).getroot()
    output = ET.Element("tripinfos")
    found: set[str] = set()
    for element in root.iter("tripinfo"):
        identifier = element.get("id")
        if identifier not in values:
            continue
        if identifier in found:
            raise ValueError(f"duplicate tripinfo id {identifier!r}")
        found.add(identifier)
        clone = ET.fromstring(ET.tostring(element, encoding="unicode"))
        clone.set("timeLoss", f"{float(values[identifier]):.{precision}f}")
        output.append(clone)
    if found != set(values):
        raise ValueError(
            f"tripinfo view population mismatch: missing={sorted(set(values)-found)}")
    ET.ElementTree(output).write(
        destination, encoding="utf-8", xml_declaration=True)


def _read_warm_edgedata_time_loss(path: Path) -> tuple[RecoveryBucket, ...]:
    """Parse precision-16 warm edgeData with ordinary cold semantics.

    SUMO's ``--precision`` is global, although warm reconstruction needs extra digits only for
    tripinfo transport.  A cold edgeData file rounds every edge value before
    :func:`read_edgedata_time_loss` sums the interval.  Repeat exactly that
    operation here so high-precision transport cannot silently change recovery
    buckets. Queue, health and closure-throughput fields are integer-valued and
    retain their existing parsers.
    """
    root = ET.parse(Path(path)).getroot()
    buckets: list[RecoveryBucket] = []
    for interval in root.iter("interval"):
        try:
            begin = float(interval.get("begin"))
            end = float(interval.get("end"))
        except (TypeError, ValueError) as error:
            raise ValueError("edgeData interval has invalid begin/end") from error
        if (not math.isfinite(begin) or not math.isfinite(end)
                or not begin.is_integer() or not end.is_integer()
                or end <= begin):
            raise ValueError("edgeData interval has invalid begin/end")
        total = 0.0
        for edge in interval.findall("edge"):
            try:
                value = float(edge.get("timeLoss", "0"))
            except (TypeError, ValueError) as error:
                raise ValueError("edgeData edge has invalid timeLoss") from error
            if not math.isfinite(value) or value < 0:
                raise ValueError("edgeData edge has invalid timeLoss")
            total += round(value, TRIPINFO_PRECISION)
        buckets.append(RecoveryBucket(int(begin), int(end), total))
    return tuple(buckets)


def _buckets_to_dict(values: tuple[RecoveryBucket, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(value) for value in values]


def _buckets_from_dict(values: Any) -> tuple[RecoveryBucket, ...]:
    if not isinstance(values, list):
        raise ValueError("baseline recovery buckets are invalid")
    return tuple(RecoveryBucket(**dict(value)) for value in values)



# ── Structured warm-attempt diagnostics (LUNA-WARM-11) ──────────────────────
# WHY THIS EXISTS. The v4 campaign (LUNA-WARM-10) failed with all three warm
# arms falling back to cold, and the immutable evidence could not say WHY: the
# runner recorded free-text reason tuples at eleven sites and the harness
# consumed none of them. A campaign that can fail eleven ways and record none of
# them forces exactly the rerun its own contract forbids.
#
# So every warm-enabled observation now finalizes exactly ONE attempt record,
# carrying its identity, ordered events with STABLE codes, and one terminal
# outcome. The codes are a closed vocabulary because a diagnostic you cannot
# match on is only slightly better than none.
WARM_ATTEMPT_SCHEMA = "monthly_warm_attempt_v1"

WARM_OUTCOME_EXECUTED = "warm_executed"
WARM_OUTCOME_FALLBACK = "cold_fallback"
WARM_OUTCOMES = frozenset({WARM_OUTCOME_EXECUTED, WARM_OUTCOME_FALLBACK})

# Terminal codes end an attempt; informational ones describe something that
# happened on the way and must stay distinguishable from the outcome — a cache
# miss is not a failure, it is the normal path into a bootstrap.
WARM_TERMINAL_CODES = frozenset({
    "ineligible", "route_audit_failed", "no_route_safe_warm_point",
    "identity_unavailable", "controller_absent", "snapshot_failed",
    "state_file_missing", "bootstrap_failed", "invoker_declined",
    "invoker_failed", "warm_evidence_invalid", "prefix_extension_failed",
    "unexpected_error",
    "warm_completed",
})
WARM_INFORMATIONAL_CODES = frozenset({
    "cache_miss", "state_restored", "bootstrap_started",
    "prefix_extension_started",
})
WARM_REASON_CODES = WARM_TERMINAL_CODES | WARM_INFORMATIONAL_CODES

# Details are diagnostic ONLY. They are bounded so a pathological error string
# cannot grow the canonical payload, and they are never parsed as authority:
# every decision is made from the code and the outcome.
WARM_DETAIL_MAX_KEYS = 6
WARM_DETAIL_MAX_CHARS = 300
# KEYS are bounded too. Limiting values and key COUNT still let one 9 000-
# character key grow the canonical record, which is the same unbounded payload
# the value bound exists to prevent — a bound with a gap is not a bound.
WARM_DETAIL_MAX_KEY_CHARS = 64


def _bounded_detail(value):
    """Coerce one detail value into a bounded, JSON-safe scalar."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)[:WARM_DETAIL_MAX_CHARS]


class WarmAttempt:
    """One warm-enabled observation's diagnostic record.

    Exactly one is finalized per warm-enabled `_run_observation` call — success
    or fallback — so an unreported fallback is impossible by construction.
    """

    def __init__(self, schedule_id: str, variant: str, seed: int):
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ValueError("warm attempt requires a schedule id")
        if not isinstance(variant, str) or not variant:
            raise ValueError("warm attempt requires a demand variant")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("warm attempt requires an integer seed")
        self.schedule_id = schedule_id
        self.demand_variant = variant
        self.seed = seed
        self.events: list[dict[str, Any]] = []
        self._finalized: dict[str, Any] | None = None

    def event(self, code: str, **details) -> None:
        """Append one ordered diagnostic event. Never raises past validation."""
        if code not in WARM_REASON_CODES:
            raise ValueError(f"unknown warm reason code {code!r}")
        if self._finalized is not None:
            raise ValueError(
                f"attempt for {self.identity()} is already finalized; a record "
                f"that keeps changing is not evidence")
        trimmed = {str(k)[:WARM_DETAIL_MAX_KEY_CHARS]: _bounded_detail(v)
                   for k, v in sorted(details.items())[:WARM_DETAIL_MAX_KEYS]}
        self.events.append({"code": code, "details": trimmed})

    def identity(self) -> tuple[str, str, int]:
        return (self.schedule_id, self.demand_variant, self.seed)

    def finalize(self, outcome: str) -> dict[str, Any]:
        """Close the record. Idempotent only in the sense that it refuses to
        happen twice — two outcomes for one attempt is a contradiction."""
        if outcome not in WARM_OUTCOMES:
            raise ValueError(f"unknown warm outcome {outcome!r}")
        if self._finalized is not None:
            raise ValueError(
                f"attempt for {self.identity()} was already finalized as "
                f"{self._finalized['outcome']!r}")
        self._finalized = {
            "schema": WARM_ATTEMPT_SCHEMA,
            "schedule_id": self.schedule_id,
            "demand_variant": self.demand_variant,
            "seed": self.seed,
            "outcome": outcome,
            "events": list(self.events),
        }
        return self._finalized

    @property
    def record(self) -> dict[str, Any] | None:
        return self._finalized


def validate_warm_attempt(attempt) -> dict[str, Any]:
    """Fail closed on a malformed attempt record."""
    if not isinstance(attempt, Mapping):
        raise ValueError("warm attempt must be an object")
    required = {"schema", "schedule_id", "demand_variant", "seed", "outcome",
                "events"}
    if set(attempt) != required:
        raise ValueError(
            f"warm attempt field set is wrong: missing="
            f"{sorted(required - set(attempt))} "
            f"extra={sorted(set(attempt) - required)}")
    if attempt["schema"] != WARM_ATTEMPT_SCHEMA:
        raise ValueError(
            f"unsupported warm attempt schema {attempt['schema']!r}")
    for field in ("schedule_id", "demand_variant"):
        if not isinstance(attempt[field], str) or not attempt[field]:
            raise ValueError(f"warm attempt {field} must be a non-empty string")
    if isinstance(attempt["seed"], bool) or not isinstance(attempt["seed"], int):
        raise ValueError("warm attempt seed must be an integer")
    if attempt["outcome"] not in WARM_OUTCOMES:
        raise ValueError(f"unknown warm outcome {attempt['outcome']!r}")
    events = attempt["events"]
    if not isinstance(events, list) or not events:
        raise ValueError(
            "a warm attempt must record at least one event; an outcome with no "
            "events explains nothing, which is the gap this record closes")
    for index, event in enumerate(events):
        if not isinstance(event, Mapping) or set(event) != {"code", "details"}:
            raise ValueError(f"warm attempt event {index} is malformed")
        if event["code"] not in WARM_REASON_CODES:
            raise ValueError(f"unknown warm reason code {event['code']!r}")
        if not isinstance(event["details"], Mapping):
            raise ValueError(f"warm attempt event {index} details must be an object")
        # READ SIDE, not just write side. The builder bounds details, but stored
        # bytes are untrusted input: a record written by anything that bypassed
        # the builder — or edited afterwards — must not be able to smuggle a
        # nested structure, a 9 000-character string or a non-finite float into
        # the canonical payload. Enforcing the declared bounds only where we
        # create data leaves the boundary facing untrusted data unguarded.
        if len(event["details"]) > WARM_DETAIL_MAX_KEYS:
            raise ValueError(
                f"warm attempt event {index} carries {len(event['details'])} "
                f"details, over the declared {WARM_DETAIL_MAX_KEYS}")
        for name, value in sorted(event["details"].items()):
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"warm attempt event {index} detail key {name!r} must be a "
                    f"non-empty string")
            if len(name) > WARM_DETAIL_MAX_KEY_CHARS:
                raise ValueError(
                    f"warm attempt event {index} detail key is {len(name)} "
                    f"characters, over the declared "
                    f"{WARM_DETAIL_MAX_KEY_CHARS}; an unbounded key grows the "
                    f"canonical record exactly like an unbounded value")
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int):
                continue
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError(
                        f"warm attempt event {index} detail {name!r} is not "
                        f"finite; it would not survive canonicalization")
                continue
            if isinstance(value, str):
                if len(value) > WARM_DETAIL_MAX_CHARS:
                    raise ValueError(
                        f"warm attempt event {index} detail {name!r} is "
                        f"{len(value)} characters, over the declared "
                        f"{WARM_DETAIL_MAX_CHARS}")
                continue
            raise ValueError(
                f"warm attempt event {index} detail {name!r} is a "
                f"{type(value).__name__}; details are bounded scalars only, so "
                f"a nested structure cannot grow the canonical payload")

    # The outcome must FOLLOW from the events, or the record contradicts itself.
    terminal = events[-1]["code"]
    if attempt["outcome"] == WARM_OUTCOME_EXECUTED:
        if terminal != "warm_completed":
            raise ValueError(
                f"attempt claims {WARM_OUTCOME_EXECUTED!r} but its last event is "
                f"{terminal!r}, not 'warm_completed'")
    elif terminal == "warm_completed":
        raise ValueError(
            f"attempt claims {WARM_OUTCOME_FALLBACK!r} but its last event says "
            f"the warm arm completed")
    elif terminal not in WARM_TERMINAL_CODES:
        raise ValueError(
            f"attempt fell back on informational event {terminal!r}; a fallback "
            f"must end on a terminal reason so the cause is recorded")
    return dict(attempt)


class ArchivedDemandSumoRunner:
    # Default boundary controller: NONE. Nothing in this module imports traci,
    # so a runner nobody wired declines to a cold run rather than guessing which
    # vehicles were in flight. The validation harness supplies a real one.
    _boundary_controller = None

    """Run exact monthly candidates against one immutable demand archive.

    One instance covers one continuous demand envelope. A future monthly
    resolver may construct several instances and route schedules by envelope;
    the orchestrator already permits their distinct matched baseline IDs.
    """

    def __init__(
        self,
        spec: ClosureSearchSpec,
        *,
        archive: Path,
        baseline_trip_duration_p99_s: int,
        study_provenance_key: str,
        cache_root: Path = DEFAULT_BASELINE_CACHE,
        seed_workers: int = 1,
        envelope_policy: EnvelopePolicy = EnvelopePolicy(),
        expected_demand_spec: DemandBuildSpec | None = None,
        include_disruption: bool = False,
        warm_execution: bool = False,
        sumo_invoker=None,
        boundary_controller=None,
        forensic_observer=None,
    ) -> None:
        self.spec = ClosureSearchSpec.from_dict(spec.to_dict())
        self.archive = Path(archive).resolve()
        self.metadata = _read(self.archive / "demand_meta.json")
        manifest_path = self.archive / "manifest.json"
        if manifest_path.is_file():
            manifest = _read(manifest_path)
            if manifest.get("status") not in {"succeeded", "validated"}:
                raise ValueError("demand archive is not succeeded/validated")
        self.expected_demand_spec = (
            DemandBuildSpec.from_dict(expected_demand_spec.to_dict())
            if expected_demand_spec is not None
            else None
        )
        self.demand_build_key = (
            self.expected_demand_spec.build_key
            if self.expected_demand_spec is not None
            else self.spec.demand_build_id
        )
        if self.metadata.get("demand_build_key") != self.demand_build_key:
            raise ValueError(
                "demand archive build key does not match expected demand"
            )
        if self.expected_demand_spec is not None:
            archived_spec_path = self.archive / "demand_build_spec.json"
            if not archived_spec_path.is_file():
                raise FileNotFoundError(archived_spec_path)
            archived_spec = DemandBuildSpec.from_dict(
                _read(archived_spec_path)
            )
            metadata_spec = DemandBuildSpec.from_dict(
                self.metadata.get("demand_spec", {})
            )
            if (
                archived_spec != self.expected_demand_spec
                or metadata_spec != self.expected_demand_spec
            ):
                raise ValueError(
                    "demand archive contract does not match expected envelope"
                )
            if (
                str(self.metadata.get("source"))
                != self.expected_demand_spec.source
                or str(self.metadata.get("epoch_sim"))
                != f"{self.expected_demand_spec.start_date}T00:00:00"
                or int(self.metadata.get("n_intervals", -1))
                != self.expected_demand_spec.days * 96
            ):
                raise ValueError(
                    "demand archive time/source metadata does not match "
                    "expected envelope"
                )
        if int(self.metadata.get("n_variants", 0)) != 3:
            raise ValueError("monthly SUMO runner requires q10/q50/q90 routes")
        self.variants = {
            variant: (self.archive / filename).resolve()
            for variant, filename in VARIANT_FILENAMES.items()
        }
        for path in self.variants.values():
            if not path.is_file():
                raise FileNotFoundError(path)
        archive_net = self.archive / "net.net.xml"
        if (
            archive_net.is_file()
            and sha256_file(archive_net) != sha256_file(rs.NET_PATH)
        ):
            raise ValueError("demand archive network differs from active SUMO net")
        # Demand archives do not carry net.net.xml, so the file check above
        # is usually vacuous — but demand_meta.json records the exact
        # network the calibration constrained against.  Simulating archived
        # routes on a different active network would silently break edge
        # identity, so this is a hard gate whenever the record exists.
        recorded_network = (
            self.metadata.get("sensor_contract") or {}
        ).get("network_sha256")
        if (
            recorded_network is not None
            and recorded_network != sha256_file(rs.NET_PATH)
        ):
            raise ValueError(
                "demand archive was calibrated against another SUMO network "
                "than the active sumo/net.net.xml"
            )
        if (
            isinstance(seed_workers, bool)
            or not isinstance(seed_workers, int)
            or seed_workers < 1
        ):
            raise ValueError("seed_workers must be a positive integer")
        if (
            isinstance(baseline_trip_duration_p99_s, bool)
            or not isinstance(baseline_trip_duration_p99_s, int)
            or baseline_trip_duration_p99_s <= 0
        ):
            raise ValueError(
                "baseline_trip_duration_p99_s must be a positive integer"
            )
        if (
            not isinstance(study_provenance_key, str)
            or not study_provenance_key.strip()
        ):
            raise ValueError("study_provenance_key must be non-empty")
        if warm_execution is not False and warm_execution is not True:
            raise ValueError("warm_execution must be a bool")
        if include_disruption is not False and include_disruption is not True:
            raise ValueError("include_disruption must be a bool")
        self.include_disruption = bool(include_disruption)
        # DEFAULT-OFF. Revision 1 exposes this only to the paired validation
        # harness; product, API and monthly-search paths never set it, so the
        # warm branch is unreachable in ordinary operation even if a cache
        # entry happens to exist.
        self.warm_execution = bool(warm_execution)
        # Injection seam: the warm arm's SUMO invocation. None means "not
        # wired", which makes the warm path decline rather than fake a run.
        self._sumo_invoker = sumo_invoker
        # Set only when supplied, so the CLASS attribute below stays the
        # single default and can be substituted wholesale by a caller that
        # owns simulator control. An unconditional assignment would shadow it
        # with None and make the seam unreachable.
        # Set only when supplied, so the CLASS attribute above remains the
        # single default and can be substituted wholesale. An unconditional
        # assignment would shadow it with None and make the seam unreachable —
        # which it silently did until the end-to-end test caught it.
        if boundary_controller is not None:
            self._boundary_controller = boundary_controller
        # Validation-only and default None. The observer receives raw tripinfo
        # while this runner still owns it; normal callers pay no parsing or
        # payload cost and retain byte-identical observations/cache members.
        self._forensic_observer = forensic_observer
        # One finalized record per warm-enabled observation. The old free-text
        # tuples were never consumed by anything, which is how LUNA-WARM-10
        # produced a failure nobody could diagnose.
        self.warm_attempts: list[dict[str, Any]] = []
        self.route_audits: list = []
        # States created this run but NOT yet cached. They are promoted only
        # after full canonical equivalence passes, so a state can never be
        # reused on the strength of an unverified run.
        self.provisional_states: list[dict] = []
        self._warm_route_window_caches: dict[str, WarmRouteWindowCache] = {}
        self._warm_route_window_directories: list[tempfile.TemporaryDirectory] = []
        self.canonical_observations: list = []
        self.baseline_trip_duration_p99_s = baseline_trip_duration_p99_s
        self.study_provenance_key = study_provenance_key
        self.cache_root = Path(cache_root)
        self.seed_workers = seed_workers
        self.envelope_policy = envelope_policy
        self.epoch = datetime.fromisoformat(str(self.metadata["epoch_sim"]))
        self.n_intervals = int(self.metadata["n_intervals"])
        self.duration_s = self.n_intervals * 900
        self.end = self.epoch + timedelta(seconds=self.duration_s)
        self.home = rs.sumo_home()
        self.close_edges = list(self.spec.directed_edges)
        self.adjacency = rs.build_edge_graph(set(self.close_edges))
        self.freeflow = rs.edge_freeflow_times()
        self._disruption_cache: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self._deterministic_provider = None
        if self.include_disruption:
            self._disruption_adjacency = rs.build_edge_graph(set())
            (
                self._disruption_edge_time,
                self._disruption_edge_len,
            ) = rs.free_flow_edge_cost()
        self.rerouter_edges = rs.edges_near(
            self.close_edges,
            rs.REROUTER_RADIUS_M,
        )
        self.detour = legacy.detour_availability(
            self.close_edges,
            rs.NET_PATH,
        )
        self.input_records = [
            _file_record(
                self.archive / "demand_meta.json",
                label="demand_meta",
            ),
            *(
                _file_record(
                    self.variants[variant],
                    label=f"demand_routes_{variant}",
                )
                for variant in DEMAND_VARIANTS
            ),
            _file_record(rs.NET_PATH, label="sumo_network"),
        ]
        self.archive_digest = _canonical_digest(self.input_records)
        self.matched_baseline_id = (
            "monthly-baseline-" + self.archive_digest[:20]
        )
        sources = [
            (
                "run_monthly_closure_search.py",
                Path("run_monthly_closure_search.py"),
            ),
            (
                "screen_monthly_closures.py",
                Path("screen_monthly_closures.py"),
            ),
            (
                "traffic_sim/core/contracts.py",
                Path("traffic_sim/core/contracts.py"),
            ),
            (
                "traffic_sim/core/closure_calendar.py",
                Path("traffic_sim/core/closure_calendar.py"),
            ),
            (
                "traffic_sim/simulation/monthly_search.py",
                Path("traffic_sim/simulation/monthly_search.py"),
            ),
            (
                "traffic_sim/simulation/monthly_sumo.py",
                Path(__file__),
            ),
            (
                "traffic_sim/simulation/closure_teleport.py",
                Path("traffic_sim/simulation/closure_teleport.py"),
            ),
            (
                "traffic_sim/simulation/monthly_demand.py",
                Path("traffic_sim/simulation/monthly_demand.py"),
            ),
            (
                "traffic_sim/simulation/monthly_proxy.py",
                Path("traffic_sim/simulation/monthly_proxy.py"),
            ),
            (
                "traffic_sim/simulation/proxy_projection.py",
                Path("traffic_sim/simulation/proxy_projection.py"),
            ),
            (
                "traffic_sim/simulation/pilot_selection.py",
                Path("traffic_sim/simulation/pilot_selection.py"),
            ),
            (
                "traffic_sim/simulation/closure_ranking.py",
                Path("traffic_sim/simulation/closure_ranking.py"),
            ),
            (
                "traffic_sim/simulation/period_comparison.py",
                Path("traffic_sim/simulation/period_comparison.py"),
            ),
            (
                "traffic_sim/simulation/finalist_decision.py",
                Path("traffic_sim/simulation/finalist_decision.py"),
            ),
            (
                "traffic_sim/simulation/search_workspace.py",
                Path("traffic_sim/simulation/search_workspace.py"),
            ),
            ("suggest_closure_time.py", Path("suggest_closure_time.py")),
            ("run_scenario.py", Path("run_scenario.py")),
            (
                "traffic_sim/simulation/envelope.py",
                Path("traffic_sim/simulation/envelope.py"),
            ),
            (
                "traffic_sim/simulation/metrics.py",
                Path("traffic_sim/simulation/metrics.py"),
            ),
        ]
        self.source_digest = _canonical_digest(
            [
                _file_record(path, label=label)
                for label, path in sources
            ]
        )
        self.source_records = [
            _file_record(path, label=label)
            for label, path in sources
        ]
        simulation_source_labels = {
            "traffic_sim/simulation/monthly_sumo.py",
            "traffic_sim/simulation/closure_teleport.py",
            "traffic_sim/simulation/monthly_demand.py",
            "suggest_closure_time.py",
            "run_scenario.py",
            "traffic_sim/simulation/envelope.py",
            "traffic_sim/simulation/metrics.py",
        }
        self.simulation_source_records = [
            record
            for record in self.source_records
            if record["label"] in simulation_source_labels
        ]
        self.simulation_source_digest = _canonical_digest(
            self.simulation_source_records
        )
        self.runtime_identity = {
            "sumo_version": str(sumo_version(self.home)),
            "platform": platform.platform(),
            "simulation_source_digest": self.simulation_source_digest,
            "simulation_mode": "meso",
            "metric_schema": "closure_decision_metrics_v1",
        }

    def provenance(self) -> Mapping[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "archived_demand_monthly_sumo_backend",
            "simulation_mode": "meso",
            "study_provenance_key": self.study_provenance_key,
            "search_content_key": self.spec.content_key,
            "demand_release_id": self.spec.demand_build_id,
            "demand_build_id": self.demand_build_key,
            "demand_build_spec": (
                self.expected_demand_spec.to_dict()
                if self.expected_demand_spec is not None
                else None
            ),
            "archive_digest": self.archive_digest,
            "matched_baseline_id": self.matched_baseline_id,
            "archive_inputs": list(self.input_records),
            "source_files": list(self.source_records),
            "source_digest": self.source_digest,
            "simulation_source_digest": self.simulation_source_digest,
            "baseline_trip_duration_p99_s": (
                self.baseline_trip_duration_p99_s
            ),
            "ranking_objective_evidence": (
                "closure_cost_v1"
                if self.include_disruption
                else "legacy_time_loss_v1"
            ),
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            **self.runtime_identity,
        }

    @property
    def execution_arm(self) -> str:
        """Which arm this runner will use. Cold unless explicitly opted in."""
        return "warm" if self.warm_execution else "cold"

    def warm_decision(self, schedule: ClosureSchedule):
        """The warm/cold decision for one schedule, with its reason.

        Always computed from the production closure seconds, so the decision a
        future warm arm makes is the one recorded here. With warm execution off
        this reports `disabled` without consulting eligibility at all.
        """
        if not self.warm_execution:
            from traffic_sim.simulation.monthly_warm_state import WarmDecision
            return WarmDecision(False, "warm_execution_disabled")
        return evaluate_warm_eligibility(
            closures=self._closure_seconds(schedule),
            scenario_start_s=0,
            simulation_mode="meso",
            duration_s=self.duration_s,
        )

    def _envelope(self, schedule: ClosureSchedule):
        envelope = build_simulation_envelope(
            self.spec,
            schedule,
            baseline_trip_duration_p99_s=(
                self.baseline_trip_duration_p99_s
            ),
            policy=self.envelope_policy,
        )
        start = datetime.fromisoformat(envelope.scenario_start)
        end = datetime.fromisoformat(envelope.scenario_end)
        if start < self.epoch or end > self.end:
            raise ValueError(
                f"demand archive does not cover envelope for "
                f"{schedule.schedule_id}: {envelope.scenario_start}--"
                f"{envelope.scenario_end}"
            )
        return envelope

    def _baseline_cache_key(
        self,
        variant: str,
        seed: int,
        *,
        n_intervals: int | None = None,
        duration_s: int | None = None,
        begin_s: int = 0,
        flush_s: int = 3600,
    ) -> str:
        n_intervals = self.n_intervals if n_intervals is None else n_intervals
        duration_s = self.duration_s if duration_s is None else duration_s
        return _canonical_digest({
            "archive_digest": self.archive_digest,
            "variant": variant,
            "seed": seed,
            "n_intervals": n_intervals,
            "duration_s": duration_s,
            "begin_s": begin_s,
            "flush_s": flush_s,
            **self.runtime_identity,
        })[:32]

    def _run_baseline(
        self,
        variant: str,
        seed: int,
        *,
        n_intervals: int | None = None,
        duration_s: int | None = None,
        begin_s: int = 0,
        flush_s: int = 3600,
    ) -> tuple[
        closure_metrics.DisruptionMetrics,
        tuple[RecoveryBucket, ...],
    ]:
        n_intervals = self.n_intervals if n_intervals is None else n_intervals
        duration_s = self.duration_s if duration_s is None else duration_s
        if begin_s < 0 or begin_s >= duration_s or begin_s % 900:
            raise ValueError("baseline begin_s must be a 15-minute-aligned range start")
        key = self._baseline_cache_key(
            variant, seed, n_intervals=n_intervals, duration_s=duration_s,
            begin_s=begin_s, flush_s=flush_s)
        path = self.cache_root / f"{key}.json"
        if path.is_file():
            cached = _read(path)
            evidence = {
                "metrics": cached.get("metrics"),
                "recovery_buckets": cached.get("recovery_buckets"),
            }
            if (
                cached.get("cache_key") != key
                or cached.get("archive_digest") != self.archive_digest
                or int(cached.get("n_intervals", -1)) != n_intervals
                or int(cached.get("duration_s", -1)) != duration_s
                or int(cached.get("begin_s", -1)) != begin_s
                or int(cached.get("flush_s", -1)) != flush_s
                or cached.get("evidence_sha256") != _canonical_digest(evidence)
            ):
                raise ValueError(f"monthly baseline cache is corrupt: {path}")
            return (
                closure_metrics.DisruptionMetrics(**cached["metrics"]),
                _buckets_from_dict(cached["recovery_buckets"]),
            )

        temporary_root = Path(tempfile.mkdtemp(
            prefix=f"monthly-base-{variant}-{seed}-",
            dir=str(self.cache_root.parent)
            if self.cache_root.parent.is_dir()
            else None,
        ))
        scratch: list[Path] = []
        try:
            metrics, _, _, _ = legacy.simulate_closure(
                name="baseline",
                closures=None,
                close_edges=[],
                variants=[self.variants[variant]],
                seeds=1,
                n_intervals=n_intervals,
                duration_s=duration_s,
                begin_s=begin_s,
                flush_s=flush_s,
                home=self.home,
                micro=False,
                adj=None,
                freeflow=None,
                scratch=scratch,
                work_dir=temporary_root / "run",
                seed_workers=1,
                seed_start=seed,
                variant_labels=[variant],
            )
            edge_data = (
                temporary_root
                / "run"
                / f"seed-{seed}"
                / f"{legacy.SCT_PREFIX}ed_baseline_{seed}.xml"
            )
            buckets = read_edgedata_time_loss(edge_data)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "kind": "monthly_matched_baseline",
                "cache_key": key,
                "archive_digest": self.archive_digest,
                "variant": variant,
                "seed": seed,
                "n_intervals": n_intervals,
                "duration_s": duration_s,
                "begin_s": begin_s,
                "flush_s": flush_s,
                **self.runtime_identity,
                "metrics": dataclasses.asdict(metrics),
                "recovery_buckets": _buckets_to_dict(buckets),
            }
            payload["evidence_sha256"] = _canonical_digest({
                "metrics": payload["metrics"],
                "recovery_buckets": payload["recovery_buckets"],
            })
            if path.exists():
                raise FileExistsError(
                    f"monthly baseline cache raced with another writer: {path}"
                )
            _atomic_json(path, payload)
            return metrics, buckets
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def _closure_seconds(
        self,
        schedule: ClosureSchedule,
    ) -> list[dict[str, Any]]:
        # ONE implementation, shared with the process-free provider (PR D).
        # A pre-SUMO cost and a post-SUMO cost that disagreed about when the
        # edge is shut would produce two different closures wearing the same
        # schedule ID, and the equivalence gate is precisely what must catch
        # that — so it must not be able to happen in the first place.
        return _deterministic_closure_seconds(
            self.spec, schedule,
            epoch=self.epoch,
            duration_s=self.duration_s,
        )

    def _cold_simulation_window(
        self, schedule: ClosureSchedule,
    ) -> tuple[int, int, int, int]:
        """Return the smallest safe cold-run horizon for one schedule.

        Canonical independent-daily archives deliberately contain a reusable
        previous/current/next-day envelope.  SUMO still used to run to the
        archive's far end for every candidate, even when the declared
        recovery envelope ended earlier.  Independent reset explicitly allows
        a reset between work days, so start at the envelope midnight and run
        only its declared recovery horizon.  Continuous searches retain
        archive-start execution because overnight carryover is part of that
        contract.  The warm path is intentionally not routed through this
        helper until its own equivalence benchmark covers the reset boundary.
        """
        envelope = self._envelope(schedule)
        end = datetime.fromisoformat(envelope.scenario_end)
        start = datetime.fromisoformat(envelope.scenario_start)
        begin_s = int((start - self.epoch).total_seconds())
        duration_s = int((end - self.epoch).total_seconds())
        if begin_s < 0 or duration_s <= begin_s or duration_s > self.duration_s:
            raise ValueError(
                f"simulation envelope end is outside demand archive for "
                f"{schedule.schedule_id}: {envelope.scenario_end}")
        if duration_s % 900:
            raise ValueError(
                f"simulation envelope end is not aligned to 15 minutes: "
                f"{envelope.scenario_end}")
        n_intervals = (duration_s - begin_s) // 900
        flush_s = 0 if begin_s else 3600
        if self.spec.interday_policy != "independent_daily_reset_v1":
            begin_s, flush_s = 0, 3600
            n_intervals = duration_s // 900
        return n_intervals, duration_s, begin_s, flush_s

    def _matched_baseline_id_for_window(
        self,
        *,
        n_intervals: int,
        duration_s: int,
        begin_s: int,
        flush_s: int,
    ) -> str:
        if (
            n_intervals == self.n_intervals
            and duration_s == self.duration_s
            and begin_s == 0
            and flush_s == 3600
        ):
            return self.matched_baseline_id
        return "monthly-baseline-" + _canonical_digest({
            "archive_digest": self.archive_digest,
            "n_intervals": n_intervals,
            "duration_s": duration_s,
            "begin_s": begin_s,
            "flush_s": flush_s,
        })[:20]

    def _run_baseline_for_window(
        self,
        variant: str,
        seed: int,
        *,
        n_intervals: int,
        duration_s: int,
        begin_s: int = 0,
        flush_s: int = 3600,
    ):
        """Call baseline with the trimmed window while preserving test seams."""
        parameters = inspect.signature(self._run_baseline).parameters
        if "duration_s" in parameters or "n_intervals" in parameters:
            return self._run_baseline(
                variant, seed, n_intervals=n_intervals, duration_s=duration_s,
                begin_s=begin_s, flush_s=flush_s)
        # Older injected probes override the two-argument seam.  They are
        # diagnostic test doubles, not production runners, and must continue
        # to be callable without weakening the production window contract.
        return self._run_baseline(variant, seed)

    def _closure_disruption(
        self,
        schedule: ClosureSchedule,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return deterministic per-variant detour evidence for a schedule."""
        if not self.include_disruption:
            return ()
        cached = self._disruption_cache.get(schedule.schedule_id)
        if cached is not None:
            return cached
        # PR D: the SAME process-free computation the pre-SUMO cost uses. The
        # plan asks for the two paths to be field-wise identical; the surest
        # way to satisfy an equivalence gate is for there to be one path.
        result = self.deterministic_disruption_provider().disruption(schedule)
        self._disruption_cache[schedule.schedule_id] = result
        return result

    def deterministic_disruption_provider(self, *, cache=None):
        """A process-free cost provider bound to THIS runner's archive.

        Reuses the network tables this runner already built, so asking for a
        deterministic cost never re-parses a 16 MB network, and never starts a
        simulation.
        """
        from traffic_sim.simulation.deterministic_disruption import (
            ArchiveDisruptionProvider,
            NetworkCostModel,
            _file_state,
        )

        if self._deterministic_provider is None or cache is not None:
            network = object.__new__(NetworkCostModel)
            network.network_path = Path(rs.NET_PATH).resolve()
            network.network_sha256 = sha256_file(rs.NET_PATH)
            network.network_metadata_path = Path(
                rs.SUMO_DIR / "network_metadata.json").resolve()
            network.network_metadata_sha256 = sha256_file(
                network.network_metadata_path)
            network._network_state = _file_state(network.network_path)
            network._metadata_state = _file_state(
                network.network_metadata_path)
            network.adjacency = self._disruption_adjacency
            network.edge_time = self._disruption_edge_time
            network.edge_len = self._disruption_edge_len
            provider = ArchiveDisruptionProvider(
                self.spec,
                archive=self.archive,
                network=network,
                cache=cache,
            )
            if cache is not None:
                return provider
            self._deterministic_provider = provider
        return self._deterministic_provider

    def warm_interpreting_sources(self) -> dict:
        """Sources whose bytes change how a warm state or prefix is READ."""
        repository = Path(__file__).resolve().parents[2]
        return {
            "monthly_sumo": Path(__file__).resolve(),
            "monthly_warm_state": (repository / "traffic_sim" / "simulation"
                                   / "monthly_warm_state.py"),
            "metrics": repository / "traffic_sim" / "simulation" / "metrics.py",
            "envelope": repository / "traffic_sim" / "simulation" / "envelope.py",
            "feasibility": repository / "suggest_closure_time.py",
            # Snapshot accounting decides how a cached prefix is turned back
            # into the objective, so its bytes are read-affecting.
            "warm_state_boundary": (repository / "traffic_sim" / "simulation"
                                    / "warm_state_boundary.py"),
            "warm_route_windows": (repository / "traffic_sim" / "simulation"
                                   / "warm_route_windows.py"),
        }

    def warm_state_root(self) -> Path:
        """Isolated per-runner warm-state root. Never a shared global path."""
        return Path(self.cache_root).parent / "warm-state"

    def cleanup(self) -> None:
        """Remove provisional warm workspaces retained during one sweep.

        Validation harnesses inspect and promote these states before calling
        cleanup. Product searches never publish provisional material and must
        not leak one temporary directory per schedule/seed after completion.
        """
        workspaces = {
            Path(entry["state_path"]).parent
            for entry in self.provisional_states
            if entry.get("state_path") is not None
        }
        for workspace in workspaces:
            shutil.rmtree(workspace, ignore_errors=True)
        self.provisional_states.clear()
        for directory in getattr(self, "_warm_route_window_directories", ()):
            directory.cleanup()
        if hasattr(self, "_warm_route_window_directories"):
            self._warm_route_window_directories.clear()
        if hasattr(self, "_warm_route_window_caches"):
            self._warm_route_window_caches.clear()

    def _extension_route_window(
        self, variant: str, begin_s: int, end_s: int, destination: Path
    ) -> Path:
        cache = self._warm_route_window_caches.get(variant)
        if cache is None:
            directory = tempfile.TemporaryDirectory(
                prefix=f"warm-route-{variant}-"
            )
            self._warm_route_window_directories.append(directory)
            cache = WarmRouteWindowCache(
                self.variants[variant], Path(directory.name), alignment_s=900
            )
            self._warm_route_window_caches[variant] = cache
        return cache.materialize(begin_s, end_s, destination)

    def _capture_forensic(self, *, schedule_id: str, variant: str, seed: int,
                          phase: str, tripinfo_path: Path,
                          metrics) -> None:
        """Invoke the opt-in observer before an owned workspace disappears."""
        if self._forensic_observer is None:
            return
        capture = getattr(self._forensic_observer, "capture", None)
        if not callable(capture):
            raise ForensicObservationError(
                "forensic observer has no callable capture method")
        capture(
            schedule_id=schedule_id, demand_variant=variant, seed=seed,
            phase=phase, tripinfo_path=Path(tripinfo_path),
            reported_total_s=metrics.total_time_loss_s,
            reported_trip_count=metrics.trip_count)

    def _capture_warm_forensic_summary(self, *, schedule_id: str,
                                       variant: str, seed: int,
                                       metrics) -> None:
        if self._forensic_observer is None:
            return
        capture = getattr(self._forensic_observer,
                          "capture_warm_summary", None)
        if not callable(capture):
            raise ForensicObservationError(
                "forensic observer has no callable capture_warm_summary method")
        capture(
            schedule_id=schedule_id, demand_variant=variant, seed=seed,
            reported_total_s=metrics.total_time_loss_s,
            reported_trip_count=metrics.trip_count)

    def bootstrap_warm_state(self, *, variant: str, seed: int,
                             warm_point_s: int, workspace: Path, attempt=None,
                             schedule_id: str | None = None):
        """Candidate-FREE prefix run: save a state and measure the prefix.

        No closure additional and the UNFILTERED archive route, so the state
        encodes nothing about the candidate it is later used for. The state is
        PROVISIONAL: it stays in the workspace and is only promoted into the
        cache once full canonical equivalence passes.
        """
        run_dir = workspace / "bootstrap"
        run_dir.mkdir(parents=True, exist_ok=True)
        state_path = workspace / "state.xml.gz"
        edge_data = run_dir / "prefix_edgedata.xml"
        add_path = run_dir / "prefix_edgedata.add.xml"
        rs.write_edgedata_additional(add_path, edge_data, warm_point_s)
        # ONE simulation step past the snapshot, not the default 3600 s meso
        # flush. With flush_s=3600 the prefix keeps simulating for another hour
        # after the state is written, so its tripinfo/statistics would overlap
        # the resumed arm and be counted twice by prefix accounting. SUMO's
        # --end is exclusive and requires save_state_time_s < end, so flush_s=1
        # is the smallest legal value that still writes the state.
        # Without a controller we cannot know which vehicles were airborne, and
        # LUNA-WARM-07 proved aggregates cannot substitute — so this is a cold
        # fallback, not a guess.
        if self._boundary_controller is None:
            if attempt is not None:
                attempt.event("controller_absent")
            return None
        # The command is BUILT here and RUN by the controller, so the ledger and
        # the saved state come from one process at one step. `--save-state` is
        # deliberately absent: the controller saves through the same connection
        # it captures from, which is the whole point.
        cmd, metric_paths, run_cwd = rs.build_sumo_invocation(
            seed, self.variants[variant], [add_path], warm_point_s, self.home,
            micro=False, metrics=True, work_dir=run_dir, flush_s=1,
            # The unfinished records are the only public output exposing
            # MSDevice_Tripinfo::myMesoTimeLoss before save/load drops it.
            tripinfo_write_unfinished=True,
            output_precision=WARM_OUTPUT_PRECISION)
        # The state-serialization settings the cache identity RECORDS must be on
        # the command that actually writes the state. They remain derived from
        # the cache constants, never restated by hand. V12 proved they do not
        # explain the residual; they remain reproducibility requirements
        # while transporting SUMO's separately omitted meso tripinfo member.
        cmd = [*cmd, *snapshot_settings_arguments()]
        run_cwd.mkdir(parents=True, exist_ok=True)
        try:
            captured = self._boundary_controller.run_prefix(
                cmd=cmd, cwd=run_cwd, home=self.home,
                warm_point_s=warm_point_s, state_path=state_path)
            snapshot_facts = captured["facts"]
            boundary_active = captured["boundary_active"]
        except Exception as error:                     # any capture failure
            if attempt is not None:
                attempt.event("snapshot_failed", error=str(error),
                              error_type=type(error).__name__)
            return None
        if not Path(state_path).is_file():
            if attempt is not None:
                attempt.event("state_file_missing", path=Path(state_path).name)
            return None
        measured = closure_metrics.build_metrics(
            metric_paths["tripinfo"], metric_paths["statistics"],
            summary_path=metric_paths.get("summary"))
        try:
            partition = build_prefix_accumulator(
                metric_paths["tripinfo"], boundary_active)
        except Exception as error:
            if attempt is not None:
                attempt.event("snapshot_failed", error=str(error),
                              error_type=type(error).__name__)
            return None
        completed_measured = dataclasses.replace(
            measured,
            total_time_loss_s=partition["completed_trips"]["total_time_loss_s"],
            trip_count=partition["completed_trips"]["trip_count"],
            unfinished_trips=0,
            unfinished_waiting_trips=0)
        if self._forensic_observer is not None:
            if not schedule_id:
                raise ForensicObservationError(
                    "forensic prefix capture requires a schedule id")
            completed_view = run_dir / "completed_prefix_tripinfo.xml"
            _write_tripinfo_view(
                metric_paths["tripinfo"], completed_view,
                partition["completed_records"])
            self._capture_forensic(
                schedule_id=schedule_id, variant=variant, seed=seed,
                phase="completed_prefix",
                tripinfo_path=completed_view, metrics=completed_measured)
        evidence = build_prefix_evidence(
            completed_trips=partition["completed_trips"],
            completed_records=partition["completed_records"],
            queue_maximum=measured.max_queue_vehicles,
            counters={"loaded": measured.loaded, "inserted": measured.inserted,
                      "teleport_total": measured.teleport_total,
                      "teleport_reasons": dict(measured.teleport_reasons or {})},
            recovery_buckets=_read_warm_edgedata_time_loss(edge_data),
            warm_point_s=warm_point_s,
            snapshot_facts=snapshot_facts,
            active_accumulator=partition["active_accumulator"],
            completed_record_order=partition["completed_record_order"])
        return {"state_path": Path(state_path), "prefix_evidence": evidence}

    def extend_warm_state(self, *, variant: str, seed: int,
                          warm_point_s: int, workspace: Path,
                          previous_state_path: Path,
                          previous_prefix_evidence: Mapping[str, Any],
                          attempt=None, schedule_id: str | None = None):
        """Advance a verified candidate-free prefix instead of replaying zero.

        SUMO omits the mesoscopic tripinfo accumulator from a saved state.  The
        previous prefix evidence carries that omitted value for every vehicle
        active at the earlier boundary.  This method joins it to the extension
        run's unfinished tripinfo, then writes a new state/evidence pair at the
        later boundary.  Any incomplete population or accounting mismatch
        declines fail-closed; callers may then use an ordinary bootstrap.
        """
        try:
            previous = parse_prefix_evidence(previous_prefix_evidence)
            previous_point_s = previous["warm_point_s"]
            if (isinstance(warm_point_s, bool)
                    or not isinstance(warm_point_s, int)
                    or warm_point_s <= previous_point_s):
                raise ValueError(
                    "extended warm point must be later than the previous point")
            previous_state_path = Path(previous_state_path)
            if previous_state_path.is_symlink() or not previous_state_path.is_file():
                raise ValueError("previous warm state is missing or not regular")
            if self._boundary_controller is None:
                if attempt is not None:
                    attempt.event("controller_absent")
                return None

            run_dir = Path(workspace) / "extension"
            run_dir.mkdir(parents=True, exist_ok=True)
            state_path = Path(workspace) / "state.xml.gz"
            edge_data = run_dir / "prefix_edgedata.xml"
            add_path = run_dir / "prefix_edgedata.add.xml"
            rs.write_edgedata_additional(
                add_path, edge_data, warm_point_s, begin_s=previous_point_s)
            extension_route = self._extension_route_window(
                variant, previous_point_s, warm_point_s,
                run_dir / "extension.rou.xml",
            )
            cmd, metric_paths, run_cwd = rs.build_sumo_invocation(
                seed, extension_route, [add_path], warm_point_s,
                self.home, micro=False, metrics=True,
                begin_s=previous_point_s, work_dir=run_dir, flush_s=1,
                load_state_path=previous_state_path,
                tripinfo_write_unfinished=True,
                output_precision=WARM_OUTPUT_PRECISION)
            cmd = [*cmd, *snapshot_settings_arguments()]
            run_cwd.mkdir(parents=True, exist_ok=True)
            captured = self._boundary_controller.run_prefix(
                cmd=cmd, cwd=run_cwd, home=self.home,
                warm_point_s=warm_point_s, state_path=state_path)
            if not state_path.is_file():
                raise ValueError("extended warm state was not written")

            measured = closure_metrics.build_metrics(
                metric_paths["tripinfo"], metric_paths["statistics"],
                summary_path=metric_paths.get("summary"))
            correction = reconcile_resumed_tripinfo(
                metric_paths["tripinfo"], previous["active_accumulator"],
                completed_vehicle_ids=sorted(previous["completed_records"]),
                completed_prefix_total=previous["completed_trips"][
                    "total_time_loss_s"],
                precision=MESO_TIMELOSS_PRECISION)
            corrected_view = run_dir / "corrected_extension_tripinfo.xml"
            _write_tripinfo_view(
                metric_paths["tripinfo"], corrected_view,
                correction["records"], precision=MESO_TIMELOSS_PRECISION)
            partition = build_prefix_accumulator(
                corrected_view, captured["boundary_active"])

            overlap = set(previous["completed_records"]) & set(
                partition["completed_records"])
            if overlap:
                raise ValueError(
                    "extended prefix repeated completed vehicle identities")
            completed_records = {
                **previous["completed_records"],
                **partition["completed_records"],
            }
            completed_order = [
                *previous["completed_record_order"],
                *partition["completed_record_order"],
            ]
            completed_total = sum(
                (completed_records[identifier]
                 for identifier in partition["completed_record_order"]),
                start=previous["completed_trips"]["total_time_loss_s"],
            )
            buckets = concatenate_recovery_buckets(
                previous["recovery_buckets"],
                _read_warm_edgedata_time_loss(edge_data),
                previous_point_s, domain_start_s=0,
                domain_end_s=warm_point_s)
            queue_values = [
                value for value in (
                    previous["queue_maximum"], measured.max_queue_vehicles)
                if value is not None
            ]
            evidence = build_prefix_evidence(
                completed_trips={
                    "total_time_loss_s": completed_total,
                    "trip_count": len(completed_records),
                },
                completed_records=completed_records,
                completed_record_order=completed_order,
                queue_maximum=max(queue_values) if queue_values else None,
                counters={
                    "loaded": measured.loaded,
                    "inserted": measured.inserted,
                    "teleport_total": measured.teleport_total,
                    "teleport_reasons": dict(
                        measured.teleport_reasons or {}),
                },
                recovery_buckets=buckets,
                warm_point_s=warm_point_s,
                snapshot_facts=captured["facts"],
                active_accumulator=partition["active_accumulator"],
            )
            return {"state_path": state_path, "prefix_evidence": evidence}
        except Exception as error:
            if attempt is not None:
                attempt.event("snapshot_failed", error=str(error),
                              error_type=type(error).__name__)
                return None
            raise

    def _default_warm_invoker(self, *, plan, schedule, variant, seed, closures,
                              workspace, prefix_evidence):
        """Post-warm phase: load the prefix state and apply the closure.

        High-precision output transports the omitted meso accumulator. Tripinfo
        is reconstructed by identity; recovery edgeData is normalized back to
        cold precision, while integer queue/health/throughput fields retain the
        ordinary production parsers.
        """
        run_dir = workspace / "post"
        run_dir.mkdir(parents=True, exist_ok=True)
        rs.write_closure_additional(plan.closure_additional, closures,
                                    self.rerouter_edges)
        truncated, dropped = rs.truncate_stranded_vehicles(
            self.variants[variant], self.close_edges, plan.filtered_route,
            self.adjacency, closures, self.freeflow)
        edge_data = run_dir / "post_edgedata.xml"
        add_path = run_dir / "post_edgedata.add.xml"
        rs.write_edgedata_additional(add_path, edge_data, self.duration_s,
                                     begin_s=plan.warm_point_s)
        if self._boundary_controller is None:
            return None
        terminal_time_s = self.duration_s + WARM_POST_FLUSH_S
        cmd, metric_paths, run_cwd = rs.build_sumo_invocation(
            seed, plan.filtered_route, [add_path, plan.closure_additional],
            self.duration_s, self.home, micro=False, metrics=True,
            begin_s=plan.warm_point_s, work_dir=run_dir,
            load_state_path=plan.state_path,
            output_precision=WARM_OUTPUT_PRECISION,
            # The warm arm must be equivalent to the cold arm it replaces, and
            # the cold arm (legacy.simulate_closure) now runs the Stage 3
            # teleport policy. Only the post-warm segment carries it, because
            # only that segment has the closure — the candidate-free prefix
            # keeps SUMO's default exactly as before.
            time_to_teleport_s=(closure_teleport.CLOSURE_TIME_TO_TELEPORT_S
                                if closures else None),
            tripinfo_write_unfinished=True)
        measurement = self._boundary_controller.run_resumed(
            cmd=cmd, cwd=run_cwd, home=self.home,
            warm_point_s=plan.warm_point_s,
            terminal_time_s=terminal_time_s)
        # MEASURE closure throughput. LUNA-WARM-05 left this unmeasured, so the
        # warm arm reported None against the cold arm's 0 — and "we did not
        # look" is not the same statement as "nothing crossed a closed edge".
        # Closed edges are zero-filled because `excludeEmpty=true` omits an edge
        # that was measured and carried nothing.
        if closures:
            if not edge_data.exists():
                # A closure with no measured post domain cannot be certified.
                return None
            seed_flows = rs.parse_edgedata(
                edge_data, self.n_intervals,
                measured_empty_edges=tuple(self.close_edges))
            active_throughput = closure_metrics.active_closure_throughput(
                seed_flows, closures)
            if active_throughput is None:
                return None
        else:
            active_throughput = None
        raw_post_metrics = closure_metrics.build_metrics(
            metric_paths["tripinfo"], metric_paths["statistics"],
            truncated_unreachable=truncated, dropped_unreachable=dropped,
            summary_path=metric_paths.get("summary"),
            closed_edge_throughput=active_throughput)
        correction = reconcile_resumed_tripinfo(
            metric_paths["tripinfo"], prefix_evidence["active_accumulator"],
            completed_vehicle_ids=sorted(prefix_evidence["completed_records"]),
            completed_prefix_total=prefix_evidence["completed_trips"][
                "total_time_loss_s"])
        post_metrics = dataclasses.replace(
            raw_post_metrics,
            total_time_loss_s=correction["corrected_total_time_loss_s"],
            trip_count=correction["trip_count"])
        corrected_view = run_dir / "corrected_resumed_tripinfo.xml"
        if self._forensic_observer is not None:
            _write_tripinfo_view(
                metric_paths["tripinfo"], corrected_view,
                correction["records"])
        self._capture_forensic(
            schedule_id=schedule.schedule_id, variant=variant, seed=seed,
            phase="resumed", tripinfo_path=(corrected_view
                                             if self._forensic_observer is not None
                                             else metric_paths["tripinfo"]),
            metrics=post_metrics)
        correction_summary = {
            key: value for key, value in correction.items() if key != "records"
        }
        return {
            "post_warm_metrics": post_metrics,
            "raw_post_warm_metrics": raw_post_metrics,
            "restore_correction": correction_summary,
            "candidate_buckets": _read_warm_edgedata_time_loss(edge_data),
        }

    def _provisional_predecessor(self, *, variant: str, seed: int,
                                 warm_point_s: int):
        """Nearest usable earlier prefix from this runner's current sweep."""
        candidates = [
            entry for entry in self.provisional_states
            if entry["variant"] == variant and entry["seed"] == seed
            and entry["identity"].warmup_end_s < warm_point_s
            and Path(entry["state_path"]).is_file()
        ]
        return max(
            candidates,
            key=lambda entry: entry["identity"].warmup_end_s,
            default=None,
        )

    def run_warm_observation(
        self,
        schedule: ClosureSchedule,
        *,
        variant: str,
        seed: int,
        envelope,
        closures,
        baseline,
        baseline_buckets,
        attempt=None,
    ):
        """Warm arm: restore or bootstrap a prefix state, then run the closure.

        Returns None for ANY reason the warm path is not usable, so the caller
        runs the unchanged cold path. A cache miss is never an error and never
        a silent degradation of evidence: the cold arm produces the same
        canonical observation.
        """
        decision = self.warm_decision(schedule)
        if not decision.eligible:
            if attempt is not None:
                attempt.event("ineligible", reason=decision.reason)
            return None
        invoker = self._sumo_invoker or self._default_warm_invoker

        # ROUTE SAFETY FIRST — before any state lookup or bootstrap. The prefix
        # is simulated from the UNFILTERED route, so a vehicle whose route the
        # closure filtering changes, and which departs before the snapshot,
        # would already have driven a route the candidate does not use. The
        # snapshot must therefore precede the earliest affected departure, and
        # that cannot be known without filtering first.
        audit_workspace = Path(tempfile.mkdtemp(
            prefix=f"warm-audit-{schedule.schedule_id[:12]}-{variant}-{seed}-"))
        try:
            filtered = audit_workspace / "filtered.rou.xml"
            rs.truncate_stranded_vehicles(
                self.variants[variant], self.close_edges, filtered,
                self.adjacency, closures, self.freeflow)
            audit = audit_route_mutation(self.variants[variant], filtered)
        except Exception as error:
            shutil.rmtree(audit_workspace, ignore_errors=True)
            if attempt is not None:
                attempt.event("route_audit_failed", error=str(error),
                              error_type=type(error).__name__)
            return None

        safe_point = route_safe_warm_point(
            audit, closure_begin_s=min(c["begin_s"] for c in closures))
        if safe_point is None:
            # No sound split exists for this candidate: run the cold path.
            if attempt is not None:
                attempt.event("no_route_safe_warm_point")
            return None
        decision = replace(decision, warm_point_s=safe_point)
        try:
            identity = monthly_warm_identity(
                demand_build_id=self.demand_build_key,
                demand_variant=variant,
                seed=seed,
                simulation_mode="meso",
                warm_point_s=decision.warm_point_s,
                network_path=rs.NET_PATH,
                unfiltered_route_path=self.variants[variant],
                # Every module that interprets the state or its prefix must be
                # part of the identity. Binding only the mandatory defaults
                # would let metric, envelope or feasibility semantics change
                # while a cached state still looked valid.
                source_files=self.warm_interpreting_sources(),
                sumo_home=self.home,
                archive_dir=self.archive,
                expected_variant_filename=VARIANT_FILENAMES[variant],
                route_audit=audit,
                audit_record_path=audit_workspace / "route_audit.json",
                snapshot_binding={
                    "snapshot_schema": SNAPSHOT_FACTS_SCHEMA,
                    "save_state_rng": STATE_RNG_SAVED,
                    "save_state_precision": STATE_PRECISION,
                },
                snapshot_record_path=audit_workspace / "snapshot_binding.json",
            )
        except Exception as error:                       # contract violation
            if attempt is not None:
                attempt.event("identity_unavailable", error=str(error),
                              error_type=type(error).__name__)
            return None
        finally:
            shutil.rmtree(audit_workspace, ignore_errors=True)
        self.route_audits.append((schedule.schedule_id, variant, seed, audit,
                                  safe_point))

        workspace = Path(tempfile.mkdtemp(
            prefix=f"warm-{schedule.schedule_id[:16]}-{variant}-{seed}-"))
        try:
            state_path = workspace / "state.xml.gz"
            lookup = restore_warm_state(self.warm_state_root(), identity, state_path)
            prefix_evidence = None
            if lookup.hit:
                prefix_evidence = self._cached_prefix_evidence(identity)
                if prefix_evidence is not None and attempt is not None:
                    # INFORMATIONAL: the state came from cache rather than a
                    # fresh bootstrap. Declaring this code without ever emitting
                    # it would have been a promise the record could not keep.
                    attempt.event("state_restored")
            if not lookup.hit or prefix_evidence is None:
                # Cache miss: extend the nearest earlier candidate-free state
                # from this sweep. The physical prefix contains no closure;
                # route safety for the NEW candidate was established above,
                # and exact evidence is reconciled at the later boundary.
                if attempt is not None:
                    # INFORMATIONAL: a miss is the normal path into bootstrap
                    # or chaining, not a failure.
                    attempt.event("cache_miss", reason=str(lookup.reason))
                predecessor = self._provisional_predecessor(
                    variant=variant, seed=seed,
                    warm_point_s=decision.warm_point_s)
                if predecessor is None:
                    if attempt is not None:
                        attempt.event("bootstrap_started")
                    bootstrap = self.bootstrap_warm_state(
                        variant=variant, seed=seed,
                        warm_point_s=decision.warm_point_s,
                        workspace=workspace,
                        # Forward the current attempt so the specific failure
                        # cause cannot collapse into a generic fallback.
                        attempt=attempt, schedule_id=schedule.schedule_id)
                else:
                    if attempt is not None:
                        attempt.event(
                            "prefix_extension_started",
                            from_warm_point_s=(
                                predecessor["identity"].warmup_end_s),
                            to_warm_point_s=decision.warm_point_s)
                    bootstrap = self.extend_warm_state(
                        variant=variant, seed=seed,
                        warm_point_s=decision.warm_point_s,
                        workspace=workspace,
                        previous_state_path=predecessor["state_path"],
                        previous_prefix_evidence=predecessor[
                            "prefix_evidence"],
                        attempt=attempt, schedule_id=schedule.schedule_id)
                if bootstrap is None:
                    if attempt is not None:
                        if predecessor is None:
                            attempt.event("bootstrap_failed")
                        else:
                            attempt.event("prefix_extension_failed")
                    return None
                state_path = bootstrap["state_path"]
                prefix_evidence = bootstrap["prefix_evidence"]
                # PROVISIONAL: kept out of the cache until equivalence passes.
                self.provisional_states.append({
                    "identity": identity,
                    "state_path": Path(state_path),
                    "prefix_evidence": prefix_evidence,
                    "schedule_id": schedule.schedule_id,
                    "variant": variant, "seed": seed,
                })
            plan = plan_warm_execution(
                warm_point_s=decision.warm_point_s,
                state_path=state_path,
                unfiltered_route=self.variants[variant],
                filtered_route=workspace / "filtered.rou.xml",
                closure_additional=workspace / "closure.add.xml",
            )
            try:
                result = invoker(plan=plan, schedule=schedule,
                                 variant=variant, seed=seed,
                                 closures=closures, workspace=workspace,
                                 prefix_evidence=prefix_evidence)
            except ForensicObservationError:
                raise
            except Exception as error:
                # Malformed resumed tripinfo, a duplicate identity, an
                # unreadable output — all mean this warm attempt is unusable,
                # which is a cold fallback with a recorded reason, never an
                # aborted observation.
                if attempt is not None:
                    attempt.event("invoker_failed", error=str(error),
                                  error_type=type(error).__name__)
                return None
            if result is None:
                if attempt is not None:
                    attempt.event("invoker_declined")
                return None
            # Account for the prefix explicitly; never omit or synthesise it.
            # The prefix comes from the bootstrap (or from the cached state's
            # recorded evidence); the invoker only observes the post-warm
            # segment. Every field crosses the boundary by an explicit rule.
            # Select by PRESENCE. `or` would silently swap an explicitly
            # supplied but empty/partial object for the bootstrap's, hiding an
            # invoker that returned malformed evidence.
            evidence = (result["prefix_evidence"]
                        if "prefix_evidence" in result else prefix_evidence)
            # Aggregate ONCE, then reuse the bounded summary for both the
            # objective and the diagnostics — so what the observation reports
            # and what the diagnostics let a reviewer recompute cannot disagree.
            #
            # INVALID WARM EVIDENCE IS A COLD FALLBACK, NOT AN ERROR. A missing
            # boundary identity, a double count, a malformed ledger or an
            # impossible reconstruction all mean this warm attempt cannot be
            # trusted — and the cold arm produces the same canonical
            # observation, so the honest response is to record why and let it
            # run. Letting the exception escape instead aborted the whole
            # observation and produced no evidence at all.
            try:
                corrected_post = result["post_warm_metrics"]
                post_metrics = (dataclasses.asdict(corrected_post)
                                if dataclasses.is_dataclass(corrected_post)
                                else dict(corrected_post))
                raw_post = result["raw_post_warm_metrics"]
                raw_post_metrics = (dataclasses.asdict(raw_post)
                                    if dataclasses.is_dataclass(raw_post)
                                    else dict(raw_post))
                correction_summary = dict(result["restore_correction"])
                # Keep the two intentionally different correction totals named
                # at the consumption boundary: the corrected resumed segment is
                # the post metric, while the continued accumulator already
                # includes completed-prefix records and is only the exact join.
                resumed_corrected_total = correction_summary[
                    "corrected_total_time_loss_s"]
                whole_run_continued_total = correction_summary[
                    "combined_total_time_loss_s"]
                if resumed_corrected_total != post_metrics["total_time_loss_s"]:
                    raise ValueError(
                        "restore correction differs from corrected post metrics")
                aggregation = aggregate_split_time_loss(
                    completed_prefix_total=evidence["completed_trips"][
                        "total_time_loss_s"],
                    completed_prefix_trips=evidence["completed_trips"][
                        "trip_count"],
                    resumed_total=post_metrics["total_time_loss_s"],
                    resumed_trips=post_metrics["trip_count"],
                    continued_total=whole_run_continued_total)
                metrics = closure_metrics.DisruptionMetrics(
                    **reconstruct_metrics(
                        evidence, result["post_warm_metrics"],
                        expected_warm_point_s=decision.warm_point_s,
                        aggregation=aggregation,
                        continued_total=whole_run_continued_total))
                self._capture_warm_forensic_summary(
                    schedule_id=schedule.schedule_id, variant=variant,
                    seed=seed, metrics=metrics)
            except ForensicObservationError:
                raise
            except Exception as error:
                if attempt is not None:
                    attempt.event("warm_evidence_invalid", error=str(error),
                                  error_type=type(error).__name__)
                return None
            # The canonical payload must keep the FULL bucket domain, so the
            # recovery verdict sees the same intervals a cold run would.
            # Everything from here to the canonical observation is derived from
            # the SAME warm evidence, so a failure anywhere in it is the same
            # class of problem: unusable warm evidence, hence a cold fallback.
            candidate_buckets = tuple(
                RecoveryBucket(**bucket) for bucket in concatenate_recovery_buckets(
                    evidence["recovery_buckets"], result["candidate_buckets"],
                    decision.warm_point_s, domain_start_s=0,
                    domain_end_s=self.duration_s))
            feasibility = legacy.closure_feasibility(
                metrics, baseline, detour=self.detour)
            failures = set(feasibility["hard_failures"])
            failures.update(
                f"baseline_{reason}"
                for reason in closure_metrics.disqualification_reasons(baseline))
            closure_end_s = int((datetime.fromisoformat(envelope.closure_end)
                                 - self.epoch).total_seconds())
            recovery_end_s = int((datetime.fromisoformat(envelope.scenario_end)
                                  - self.epoch).total_seconds())
            recovery = evaluate_recovery(
                baseline_buckets, candidate_buckets,
                closure_end_s=closure_end_s, recovery_cap_end_s=recovery_end_s,
                policy=self.envelope_policy)
            if not recovery.recovered:
                failures.add(f"recovery_{recovery.status}")
            canonical = build_monthly_observation(
                schedule_id=schedule.schedule_id, variant=variant, seed=seed,
                simulation_mode="meso",
                baseline_metrics=baseline, candidate_metrics=metrics,
                baseline_buckets=baseline_buckets,
                candidate_buckets=candidate_buckets,
                feasibility=feasibility, recovery=recovery,
                failures=sorted(failures),
                matched_baseline_id=self.matched_baseline_id,
                provenance_key=self.study_provenance_key,
                provenance=self.provenance(),
                execution_arm="warm", warm_point_s=decision.warm_point_s,
                split_diagnostics={
                    "route_audit": audit,
                    "selected_warm_point_s": decision.warm_point_s,
                    "prefix_completed_trips": dict(
                        evidence["completed_trips"]),
                    "prefix_counters": dict(evidence["counters"]),
                    "prefix_teleport_reasons": dict(evidence["teleport_reasons"]),
                    "prefix_queue_maximum": evidence["queue_maximum"],
                    "prefix_aggregation": dict(aggregation),
                    "snapshot_facts": dict(evidence["snapshot_facts"]),
                    "raw_post_metrics": raw_post_metrics,
                    "corrected_post_metrics": post_metrics,
                    "restore_correction": correction_summary,
                    "reconstructed_metrics": dataclasses.asdict(metrics),
                })
            return (
                PairedObservation(
                    candidate_id=schedule.schedule_id, demand_variant=variant,
                    seed=seed, baseline_time_loss_s=baseline.total_time_loss_s,
                    candidate_time_loss_s=metrics.total_time_loss_s,
                    matched_baseline_id=self.matched_baseline_id,
                    provenance_key=self.study_provenance_key),
                tuple(sorted(failures)),
                canonical,
            )
        finally:
            # The workspace holds a PROVISIONAL state the harness may still
            # promote, so it is retained when one was created and cleaned up
            # otherwise.
            if not any(entry["state_path"].parent == workspace
                       for entry in self.provisional_states):
                shutil.rmtree(workspace, ignore_errors=True)

    def _cached_prefix_evidence(self, identity):
        """Versioned prefix evidence from the VERIFIED cache entry, or None.

        `restore_warm_state` has already re-checked the manifest, the state
        digest and every member digest, so this cannot read a tampered or
        partially written member. A legacy `prefix_metrics.json`-only entry, an
        unknown schema or malformed content returns None — a cache MISS that
        runs the unchanged cold path. Such an entry is never repaired.
        """
        entry = (self.warm_state_root() / identity.content_key
                 / "prefix_evidence.json")
        if entry.is_symlink() or not entry.is_file():
            return None
        try:
            # Bind to the IDENTITY's warm point. Without it a member warmed to
            # a different point is not classified as an incompatible entry
            # here; it would restore, and only fail much later during
            # reconstruction. An incompatible entry must be a cache MISS.
            return parse_prefix_evidence(
                _read(entry), expected_warm_point_s=identity.warmup_end_s)
        except (OSError, ValueError, TypeError, Exception):
            return None

    def _run_observation(
        self,
        schedule: ClosureSchedule,
        *,
        variant: str,
        seed: int,
    ) -> tuple[PairedObservation, tuple[str, ...], dict[str, Any] | None]:
        envelope = self._envelope(schedule)
        if self.warm_execution:
            # Warm state snapshots are still bound to the full archive until
            # their separate equivalence gate covers a trimmed horizon.
            run_n_intervals, run_duration_s, run_begin_s, run_flush_s = (
                self.n_intervals, self.duration_s, 0, 3600)
        else:
            (
                run_n_intervals,
                run_duration_s,
                run_begin_s,
                run_flush_s,
            ) = self._cold_simulation_window(schedule)
        matched_baseline_id = self._matched_baseline_id_for_window(
            n_intervals=run_n_intervals,
            duration_s=run_duration_s,
            begin_s=run_begin_s,
            flush_s=run_flush_s,
        )
        baseline, baseline_buckets = self._run_baseline_for_window(
            variant, seed, n_intervals=run_n_intervals,
            duration_s=run_duration_s, begin_s=run_begin_s,
            flush_s=run_flush_s)
        closures = self._closure_seconds(schedule)
        # The warm arm is attempted only when explicitly enabled AND the frozen
        # eligibility rule says this schedule qualifies. Any miss, mismatch or
        # contract error below falls through to the UNCHANGED cold path.
        if self.warm_execution:
            # EXACTLY ONE attempt is finalized per warm-enabled observation —
            # success or fallback — so an unreported fallback is impossible by
            # construction. LUNA-WARM-10 failed with three silent fallbacks and
            # no way to tell which of eleven paths produced them.
            attempt = WarmAttempt(schedule.schedule_id, variant, seed)
            # THE GUARD BELONGS HERE, at the boundary that CONSUMES the warm
            # path, not only at the individual places that can fail inside it.
            # The warm arm is an optimisation over an equivalent cold arm, so
            # nothing it does may cost us an observation: an escape would abort
            # the whole identity and produce no evidence at all, which is worse
            # than simply running cold.
            try:
                warm = self.run_warm_observation(
                    schedule, variant=variant, seed=seed,
                    envelope=envelope, closures=closures,
                    baseline=baseline, baseline_buckets=baseline_buckets,
                    attempt=attempt)
            except ForensicObservationError:
                raise
            except Exception as error:
                attempt.event("unexpected_error", error=str(error),
                              error_type=type(error).__name__)
                warm = None
            if warm is not None:
                attempt.event(
                    "warm_completed",
                    warm_point_s=self.warm_decision(schedule).warm_point_s)
                self.warm_attempts.append(
                    attempt.finalize(WARM_OUTCOME_EXECUTED))
                return warm
            if not attempt.events:
                # A decline with no recorded event is exactly the silence this
                # record exists to eliminate, so it is reported as such rather
                # than left blank.
                attempt.event(
                    "unexpected_error",
                    error="the warm path declined without recording a reason")
            self.warm_attempts.append(attempt.finalize(WARM_OUTCOME_FALLBACK))
        temporary_root = Path(tempfile.mkdtemp(
            prefix=f"monthly-{schedule.schedule_id[:20]}-{variant}-{seed}-"
        ))
        scratch: list[Path] = []
        try:
            metrics, _, _, _ = legacy.simulate_closure(
                name="candidate",
                closures=closures,
                close_edges=self.close_edges,
                variants=[self.variants[variant]],
                seeds=1,
                n_intervals=run_n_intervals,
                duration_s=run_duration_s,
                begin_s=run_begin_s,
                flush_s=run_flush_s,
                home=self.home,
                micro=False,
                adj=self.adjacency,
                freeflow=self.freeflow,
                scratch=scratch,
                rerouter_edges=self.rerouter_edges,
                work_dir=temporary_root / "run",
                seed_workers=1,
                seed_start=seed,
                variant_labels=[variant],
            )
            if self._forensic_observer is not None:
                tripinfo_paths = [Path(path) for path in scratch
                                  if Path(path).name.endswith("_tripinfo.xml")]
                if len(tripinfo_paths) != 1:
                    raise ForensicObservationError(
                        "cold candidate produced "
                        f"{len(tripinfo_paths)} tripinfo paths, expected 1")
                self._capture_forensic(
                    schedule_id=schedule.schedule_id, variant=variant,
                    seed=seed, phase="cold_candidate",
                    tripinfo_path=tripinfo_paths[0], metrics=metrics)
            feasibility = legacy.closure_feasibility(
                metrics,
                baseline,
                detour=self.detour,
            )
            failures = set(feasibility["hard_failures"])
            baseline_failures = closure_metrics.disqualification_reasons(
                baseline
            )
            failures.update(
                f"baseline_{reason}" for reason in baseline_failures
            )
            candidate_edge_data = (
                temporary_root
                / "run"
                / f"seed-{seed}"
                / f"{legacy.SCT_PREFIX}ed_candidate_{seed}.xml"
            )
            candidate_buckets = read_edgedata_time_loss(candidate_edge_data)
            closure_end_s = int(
                (
                    datetime.fromisoformat(envelope.closure_end) - self.epoch
                ).total_seconds()
            )
            recovery_end_s = int(
                (
                    datetime.fromisoformat(envelope.scenario_end) - self.epoch
                ).total_seconds()
            )
            recovery = evaluate_recovery(
                baseline_buckets,
                candidate_buckets,
                closure_end_s=closure_end_s,
                recovery_cap_end_s=recovery_end_s,
                policy=self.envelope_policy,
            )
            if not recovery.recovered:
                failures.add(f"recovery_{recovery.status}")
            # ONE canonical production observation, assembled here so a future
            # warm arm compares the same object the cold arm produced rather
            # than a reduced copy of it.
            canonical_observation = build_monthly_observation(
                schedule_id=schedule.schedule_id,
                variant=variant,
                seed=seed,
                simulation_mode="meso",
                baseline_metrics=baseline,
                candidate_metrics=metrics,
                baseline_buckets=baseline_buckets,
                candidate_buckets=candidate_buckets,
                feasibility=feasibility,
                recovery=recovery,
                failures=sorted(failures),
                matched_baseline_id=matched_baseline_id,
                provenance_key=self.study_provenance_key,
                provenance=self.provenance(),
                # ALWAYS "cold" here: this is the cold simulation path, and it
                # is also where the warm arm falls back to. Labelling it from
                # the runner's setting would mark a genuinely cold result
                # "warm" (and, with no warm point, crash the builder).
                execution_arm="cold",
                warm_point_s=None,
            )
            return (
                PairedObservation(
                    candidate_id=schedule.schedule_id,
                    demand_variant=variant,
                    seed=seed,
                    baseline_time_loss_s=baseline.total_time_loss_s,
                    candidate_time_loss_s=metrics.total_time_loss_s,
                    matched_baseline_id=matched_baseline_id,
                    provenance_key=self.study_provenance_key,
                ),
                tuple(sorted(failures)),
                canonical_observation,
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def _observations_for(
        self,
        schedule: ClosureSchedule,
        pending: list[tuple[str, int]],
    ):
        """Yield this candidate's observations in canonical (variant, seed) order.

        With ``seed_workers == 1`` each run starts only when the previous one
        finished, exactly as the search has always executed. Above 1 the runs
        are started concurrently — they are independent SUMO processes with
        their own seed, their own scratch directory and (within one candidate)
        distinct matched-baseline cache keys — but they are still YIELDED in
        canonical order, one at a time.

        That ordering is what makes concurrency a pure speed change. The caller
        stops consuming at the first observation with a hard failure, so it
        sees precisely the observation set the serial loop would have produced;
        runs that the serial loop would never have reached are discarded, and
        an exception raised by such a run is discarded with it rather than
        surfacing early. Concurrency therefore costs speculative work on
        candidates that fail, never a different result.
        """
        if self.seed_workers == 1 or len(pending) <= 1:
            for variant, seed in pending:
                try:
                    observation, run_failures, canonical = self._run_observation(
                        schedule, variant=variant, seed=seed)
                except SystemExit as error:
                    message = str(error)
                    if "sumo timed out" not in message and "sumo failed" not in message:
                        raise
                    observation, canonical = None, None
                    run_failures = (
                        f"sumo_execution_failure:{variant}:{seed}:" + message,
                    )
                yield variant, seed, observation, run_failures, canonical
            return

        executor = ThreadPoolExecutor(
            max_workers=min(self.seed_workers, len(pending)),
            thread_name_prefix="monthly-seed")
        futures: list = []
        try:
            futures = [
                executor.submit(
                    self._run_observation, schedule, variant=variant, seed=seed)
                for variant, seed in pending
            ]
            for (variant, seed), future in zip(pending, futures):
                try:
                    observation, run_failures, canonical = future.result()
                except SystemExit as error:
                    message = str(error)
                    if "sumo timed out" not in message and "sumo failed" not in message:
                        raise
                    observation, canonical = None, None
                    run_failures = (
                        f"sumo_execution_failure:{variant}:{seed}:" + message,
                    )
                yield variant, seed, observation, run_failures, canonical
        finally:
            # Drop what has not started, then wait for the few still in
            # flight: a search that walked away from a failed candidate must
            # not leave stray SUMO processes competing with the next one.
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True)

    def run_candidate(
        self,
        schedule: ClosureSchedule,
        *,
        target_repetitions: Mapping[str, int],
        existing: CandidateEvidence | None,
        stage: str,
    ) -> CandidateEvidence:
        if stage not in {"pilot", "finalist"}:
            raise ValueError("monthly SUMO stage must be pilot or finalist")
        if schedule.search_content_key != self.spec.content_key:
            raise ValueError("schedule does not belong to SUMO runner search")
        observations = list(existing.observations if existing is not None else ())
        failures = set(existing.hard_failures if existing is not None else ())
        disruption = (
            existing.disruption
            if existing is not None and existing.disruption
            else self._closure_disruption(schedule)
        )
        if failures:
            return CandidateEvidence(
                candidate_id=schedule.schedule_id,
                observations=tuple(observations),
                hard_failures=tuple(sorted(failures)),
                disruption=disruption,
            )
        seen = {
            (item.demand_variant, item.seed) for item in observations
        }
        self.canonical_observations = []
        pending: list[tuple[str, int]] = []
        for variant in DEMAND_VARIANTS:
            target = target_repetitions.get(variant)
            if (
                isinstance(target, bool)
                or not isinstance(target, int)
                or target < 0
            ):
                raise ValueError("target repetitions must be non-negative integers")
            for repetition in range(target):
                seed = canonical_seed(variant, repetition)
                if (variant, seed) in seen:
                    continue
                pending.append((variant, seed))

        with closing(self._observations_for(schedule, pending)) as stream:
            for variant, seed, observation, run_failures, canonical in stream:
                if observation is not None:
                    observations.append(observation)
                if canonical is not None:
                    self.canonical_observations.append(canonical)
                seen.add((variant, seed))
                failures.update(run_failures)
                if failures:
                    break
        observations.sort(
            key=lambda item: (
                DEMAND_VARIANTS.index(item.demand_variant),
                item.seed,
            )
        )
        return CandidateEvidence(
            candidate_id=schedule.schedule_id,
            observations=tuple(observations),
            hard_failures=tuple(sorted(failures)),
            disruption=disruption,
        )
