import json
from pathlib import Path

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
    DemandBuildSpec,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    PairedObservation,
)
from traffic_sim.simulation.monthly_search import canonical_seed
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
import traffic_sim.simulation.monthly_sumo as monthly_sumo


def _spec(build_key="demand-key"):
    return ClosureSearchSpec(
        search_id="sumo-backend",
        directed_edges=("edge-a",),
        demand_build_id=build_key,
        source="historical",
        permitted_date_start="2025-09-16",
        permitted_date_end="2025-09-16",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", "07:00"),
    )


def _archive(tmp_path, *, build_key="demand-key", days=1, demand_spec=None):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "manifest.json").write_text(json.dumps({"status": "succeeded"}))
    metadata = {
        "demand_build_key": build_key,
        "epoch_sim": "2025-09-16T00:00:00",
        "n_intervals": days * 96,
        "n_variants": 3,
    }
    if demand_spec is not None:
        metadata["source"] = demand_spec.source
        metadata["demand_spec"] = demand_spec.to_dict()
        (archive / "demand_build_spec.json").write_text(
            json.dumps(demand_spec.to_dict())
        )
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    for filename in (
        "calibrated.rou.xml",
        "calibrated_v1.rou.xml",
        "calibrated_v2.rou.xml",
    ):
        (archive / filename).write_text(f"<routes id='{filename}'/>")
    return archive


@pytest.fixture
def patched_runtime(tmp_path, monkeypatch):
    net = tmp_path / "net.net.xml"
    net.write_text("<net/>")
    monkeypatch.setattr(monthly_sumo.rs, "NET_PATH", net)
    monkeypatch.setattr(monthly_sumo.rs, "sumo_home", lambda: tmp_path)
    monkeypatch.setattr(
        monthly_sumo.rs,
        "build_edge_graph",
        lambda edges: {"edge-a": []},
    )
    monkeypatch.setattr(
        monthly_sumo.rs,
        "edge_freeflow_times",
        lambda: {"edge-a": 10.0},
    )
    monkeypatch.setattr(
        monthly_sumo.rs,
        "edges_near",
        lambda edges, radius: list(edges),
    )
    monkeypatch.setattr(
        monthly_sumo.legacy,
        "detour_availability",
        lambda edges, path: {"score": 1.0},
    )
    monkeypatch.setattr(monthly_sumo, "sumo_version", lambda home: "SUMO 1.27.1")
    return tmp_path


def test_archive_identity_and_envelope_are_validated(
        tmp_path, patched_runtime):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    envelope = runner._envelope(schedule)
    assert envelope.scenario_start == "2025-09-16T00:00:00"
    assert envelope.scenario_end == "2025-09-17T00:00:00"
    assert runner.matched_baseline_id.startswith("monthly-baseline-")
    provenance = runner.provenance()
    labels = {item["label"] for item in provenance["source_files"]}
    assert all(
        not Path(item["label"]).is_absolute()
        for item in provenance["source_files"]
    )
    assert {
        "run_monthly_closure_search.py",
        "traffic_sim/core/closure_calendar.py",
        "traffic_sim/simulation/monthly_search.py",
        "traffic_sim/simulation/monthly_sumo.py",
        "traffic_sim/simulation/pilot_selection.py",
        "traffic_sim/simulation/finalist_decision.py",
        "traffic_sim/simulation/search_workspace.py",
    } <= labels
    assert "git_commit_at_run" not in provenance
    assert provenance["source_digest"] != (
        provenance["simulation_source_digest"]
    )


def test_archive_calibrated_on_another_network_is_rejected(
        tmp_path, patched_runtime):
    archive = _archive(tmp_path)
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["sensor_contract"] = {"network_sha256": "0" * 64}
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="another SUMO network"):
        ArchivedDemandSumoRunner(
            _spec(),
            archive=archive,
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "cache",
        )


def test_archive_matching_active_network_is_accepted(
        tmp_path, patched_runtime):
    from traffic_sim.core.fingerprint import sha256_file
    archive = _archive(tmp_path)
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["sensor_contract"] = {
        "network_sha256": sha256_file(monthly_sumo.rs.NET_PATH)}
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=archive,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    assert runner.matched_baseline_id.startswith("monthly-baseline-")


def test_archive_with_wrong_build_key_is_rejected(tmp_path, patched_runtime):
    with pytest.raises(ValueError, match="build key"):
        ArchivedDemandSumoRunner(
            _spec(),
            archive=_archive(tmp_path, build_key="other"),
            baseline_trip_duration_p99_s=1800,
            study_provenance_key="study",
            cache_root=tmp_path / "cache",
        )


def test_exact_envelope_demand_contract_is_validated(
        tmp_path, patched_runtime):
    demand_spec = DemandBuildSpec(
        start_date="2025-09-16",
        source="historical",
        days=1,
        begin="00:00",
        end="24:00",
        purpose="closure_envelope",
    )
    runner = ArchivedDemandSumoRunner(
        _spec("release-id"),
        archive=_archive(
            tmp_path,
            build_key=demand_spec.build_key,
            demand_spec=demand_spec,
        ),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
        expected_demand_spec=demand_spec,
    )
    assert runner.provenance()["demand_release_id"] == "release-id"
    assert runner.provenance()["demand_build_id"] == demand_spec.build_key


def test_runner_adds_only_missing_canonical_repetitions(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    calls = []

    def observation(selected, *, variant, seed):
        calls.append((variant, seed))
        return (
            PairedObservation(
                candidate_id=selected.schedule_id,
                demand_variant=variant,
                seed=seed,
                baseline_time_loss_s=100.0,
                candidate_time_loss_s=110.0,
                matched_baseline_id=runner.matched_baseline_id,
                provenance_key="study",
            ),
            (),
        )

    monkeypatch.setattr(runner, "_run_observation", observation)
    pilot = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 1, "q50": 1, "q90": 1},
        existing=None,
        stage="pilot",
    )
    assert calls == [
        ("q10", canonical_seed("q10", 0)),
        ("q50", canonical_seed("q50", 0)),
        ("q90", canonical_seed("q90", 0)),
    ]

    calls.clear()
    final = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 2, "q50": 1, "q90": 2},
        existing=pilot,
        stage="finalist",
    )
    assert calls == [
        ("q10", canonical_seed("q10", 1)),
        ("q90", canonical_seed("q90", 1)),
    ]
    assert len(final.observations) == 5


def test_hard_failure_stops_additional_work(
        tmp_path, patched_runtime, monkeypatch):
    runner = ArchivedDemandSumoRunner(
        _spec(),
        archive=_archive(tmp_path),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        cache_root=tmp_path / "cache",
    )
    schedule = generate_closure_schedules(_spec())[0]
    existing = CandidateEvidence(
        candidate_id=schedule.schedule_id,
        hard_failures=("teleports",),
    )
    monkeypatch.setattr(
        runner,
        "_run_observation",
        lambda *args, **kwargs: pytest.fail("SUMO should not run"),
    )
    result = runner.run_candidate(
        schedule,
        target_repetitions={"q10": 4, "q50": 4, "q90": 4},
        existing=existing,
        stage="finalist",
    )
    assert result.hard_failures == ("teleports",)
