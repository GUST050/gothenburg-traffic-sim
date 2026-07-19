"""SUMO backend for the resumable monthly closure-search orchestrator."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import platform
import shutil
import tempfile
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
from traffic_sim.simulation import metrics as closure_metrics
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


SCHEMA_VERSION = 1
DEFAULT_BASELINE_CACHE = Path("runs") / "closure-search-baselines"
VARIANT_FILENAMES = {
    "q50": "calibrated.rou.xml",
    "q10": "calibrated_v1.rou.xml",
    "q90": "calibrated_v2.rou.xml",
}


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
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


def _buckets_to_dict(values: tuple[RecoveryBucket, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(value) for value in values]


def _buckets_from_dict(values: Any) -> tuple[RecoveryBucket, ...]:
    if not isinstance(values, list):
        raise ValueError("baseline recovery buckets are invalid")
    return tuple(RecoveryBucket(**dict(value)) for value in values)


class ArchivedDemandSumoRunner:
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
            "envelope_policy": dataclasses.asdict(self.envelope_policy),
            **self.runtime_identity,
        }

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

    def _baseline_cache_key(self, variant: str, seed: int) -> str:
        return _canonical_digest({
            "archive_digest": self.archive_digest,
            "variant": variant,
            "seed": seed,
            **self.runtime_identity,
        })[:32]

    def _run_baseline(
        self,
        variant: str,
        seed: int,
    ) -> tuple[
        closure_metrics.DisruptionMetrics,
        tuple[RecoveryBucket, ...],
    ]:
        key = self._baseline_cache_key(variant, seed)
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
                n_intervals=self.n_intervals,
                duration_s=self.duration_s,
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
        closures = []
        for interval in schedule.intervals:
            begin = int(
                (
                    datetime.fromisoformat(interval.start_time) - self.epoch
                ).total_seconds()
            )
            end = int(
                (
                    datetime.fromisoformat(interval.end_time) - self.epoch
                ).total_seconds()
            )
            if begin < 0 or end <= begin or end > self.duration_s:
                raise ValueError("closure interval lies outside demand archive")
            for edge in self.close_edges:
                closures.append({
                    "edge_id": edge,
                    "begin_s": begin,
                    "end_s": end,
                })
        return closures

    def _run_observation(
        self,
        schedule: ClosureSchedule,
        *,
        variant: str,
        seed: int,
    ) -> tuple[PairedObservation, tuple[str, ...]]:
        envelope = self._envelope(schedule)
        baseline, baseline_buckets = self._run_baseline(variant, seed)
        closures = self._closure_seconds(schedule)
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
                n_intervals=self.n_intervals,
                duration_s=self.duration_s,
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
            return (
                PairedObservation(
                    candidate_id=schedule.schedule_id,
                    demand_variant=variant,
                    seed=seed,
                    baseline_time_loss_s=baseline.total_time_loss_s,
                    candidate_time_loss_s=metrics.total_time_loss_s,
                    matched_baseline_id=self.matched_baseline_id,
                    provenance_key=self.study_provenance_key,
                ),
                tuple(sorted(failures)),
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

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
        if failures:
            return CandidateEvidence(
                candidate_id=schedule.schedule_id,
                observations=tuple(observations),
                hard_failures=tuple(sorted(failures)),
            )
        seen = {
            (item.demand_variant, item.seed) for item in observations
        }
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
                observation, run_failures = self._run_observation(
                    schedule,
                    variant=variant,
                    seed=seed,
                )
                observations.append(observation)
                seen.add((variant, seed))
                failures.update(run_failures)
                if failures:
                    break
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
        )
