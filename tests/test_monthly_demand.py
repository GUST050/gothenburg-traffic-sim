import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
)
from traffic_sim.simulation.finalist_decision import CandidateEvidence
from traffic_sim.simulation.monthly_demand import (
    MonthlyDemandResolverRunner,
    find_demand_archives,
    validate_demand_archive,
)
from traffic_sim.simulation.independent_daily import (
    INDEPENDENT_DAILY_ENVELOPE_POLICY,
    decompose_schedules,
)
from traffic_sim.demand.source_identity import demand_source_paths
import traffic_sim.simulation.monthly_demand as monthly_demand


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _spec(*, end_date="2027-07-22", latest_end="07:00"):
    return ClosureSearchSpec(
        search_id="multi-envelope",
        directed_edges=("edge-a",),
        demand_build_id="forecast-july-release-v1",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end=end_date,
        allowed_weekdays=(3,),
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("06:00", latest_end),
    )


def _archive(root, required, name, *, finished_at):
    archive = root / name
    archive.mkdir(parents=True)
    files = {
        "demand_build_spec.json": json.dumps(required.to_dict()),
        "candidates.rou.xml": (
            "<routes><vehicle id='candidate' depart='0'>"
            "<route edges='edge-a'/></vehicle></routes>"),
        "candidates.meta.json": json.dumps({
            "schema_version": 1,
            "candidates": {"candidate": {}},
        }),
        "calibrated.rou.xml": (
            "<routes><vehicle id='q50' depart='0'>"
            "<route edges='edge-a'/></vehicle></routes>"),
        "calibrated.agents.json": json.dumps({"schema_version": 1}),
        "calibrated_v1.rou.xml": (
            "<routes><vehicle id='q10' depart='0'>"
            "<route edges='edge-a'/></vehicle></routes>"),
        "calibrated_v1.agents.json": json.dumps({"schema_version": 1}),
        "calibrated_v2.rou.xml": (
            "<routes><vehicle id='q90' depart='0'>"
            "<route edges='edge-a'/></vehicle></routes>"),
        "calibrated_v2.agents.json": json.dumps({"schema_version": 1}),
    }
    for filename, content in files.items():
        (archive / filename).write_text(content)
    labels = {
        "candidates.rou.xml": "candidate_routes",
        "candidates.meta.json": "candidate_metadata",
        "calibrated.rou.xml": "calibrated_q50",
        "calibrated.agents.json": "calibrated_q50_agents",
        "calibrated_v1.rou.xml": "calibrated_v1",
        "calibrated_v1.agents.json": "calibrated_v1_agents",
        "calibrated_v2.rou.xml": "calibrated_v2",
        "calibrated_v2.agents.json": "calibrated_v2_agents",
    }
    metadata = {
        "demand_build_key": required.build_key,
        "demand_spec": required.to_dict(),
        "epoch_sim": f"{required.start_date}T00:00:00",
        "n_intervals": required.days * 96,
        "n_variants": 3,
        "candidate_provenance": {"schema_version": 1, "status": "pass"},
        "edge_support_augmentation": {
            "schema_version": 1,
            "status": "pass",
            "variants": {
                key: {"status": "pass", "required_edges": 1}
                for key in ("edge_shares", "edge_shares_q10", "edge_shares_q90")
            },
        },
        "build_fingerprint": {
            "schema_version": 1,
            "source_files": monthly_demand.demand_source_fingerprints(
                monthly_demand._PROJECT_ROOT),
            "python": monthly_demand._current_demand_runtime()[0],
            "sumo_version": monthly_demand._current_demand_runtime()[1],
            "artifacts": {
                label: {
                    "bytes": (archive / filename).stat().st_size,
                    "sha256": _sha(archive / filename),
                }
                for filename, label in labels.items()
            },
        },
    }
    files["demand_meta.json"] = json.dumps(metadata)
    (archive / "demand_meta.json").write_text(files["demand_meta.json"])
    manifest = {
        "schema_version": 1,
        "run_id": name,
        "kind": "demand",
        "status": "succeeded",
        "finished_at": finished_at,
        "outputs": [
            {
                "name": filename,
                "bytes": (archive / filename).stat().st_size,
                "sha256": _sha(archive / filename),
            }
            for filename in files
        ],
    }
    (archive / "manifest.json").write_text(json.dumps(manifest))
    return archive


class FakeChildRunner:
    created = []

    def __init__(self, spec, **options):
        self.spec = spec
        self.options = options
        self.archive = Path(options["archive"])
        self.expected = options["expected_demand_spec"]
        self.archive_digest = f"digest-{self.expected.build_key}"
        self.matched_baseline_id = f"baseline-{self.expected.build_key}"
        self.calls = []
        self.created.append(self)

    def provenance(self):
        return {
            "source_files": [{"label": "source", "sha256": "abc"}],
            "source_digest": "source-digest",
            "simulation_source_digest": "simulation-source-digest",
            "sumo_version": "SUMO 1.27.1",
            "platform": "test",
            "simulation_mode": "meso",
            "metric_schema": "closure_decision_metrics_v1",
            "demand_build_id": self.expected.build_key,
            "archive_digest": self.archive_digest,
            "matched_baseline_id": self.matched_baseline_id,
            "archive_inputs": [{"label": self.archive.name}],
        }

    def run_candidate(self, schedule, **options):
        self.calls.append(schedule.schedule_id)
        return CandidateEvidence(candidate_id=schedule.schedule_id)


def _required_for(runner, schedules):
    return {runner._required(schedule).build_key: runner._required(schedule)
            for schedule in schedules}


def test_demand_builder_is_independent_of_process_working_directory(monkeypatch, tmp_path):
    required = MonthlyDemandResolverRunner(
        _spec(end_date="2027-07-15"),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
    )._required(generate_closure_schedules(_spec(end_date="2027-07-15"))[0])
    seen = {}

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return Completed()

    monkeypatch.setattr(monthly_demand.subprocess, "run", fake_run)
    monthly_demand.build_demand_archive(required)
    assert seen["cwd"] == monthly_demand._PROJECT_ROOT
    assert seen["env"]["GS_PROJECT_DEMAND_BUILD_LOCK_HELD_BY_PARENT"] == "1"


def test_archive_validation_checks_contract_and_manifest_hashes(tmp_path):
    schedules = generate_closure_schedules(_spec(end_date="2027-07-15"))
    resolver = MonthlyDemandResolverRunner(
        _spec(end_date="2027-07-15"),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    required = resolver._required(schedules[0])
    archive = _archive(
        tmp_path, required, "demand-older", finished_at="2027-01-01T00:00:00Z")
    record = validate_demand_archive(archive, required)
    assert record["demand_build_spec"]["purpose"] == "closure_envelope"

    (archive / "calibrated_v1.rou.xml").write_text("<tampered/>")
    with pytest.raises(ValueError, match="changed"):
        validate_demand_archive(archive, required)


def test_archive_validation_rejects_another_demand_source_identity(tmp_path):
    schedules = generate_closure_schedules(_spec(end_date="2027-07-15"))
    resolver = MonthlyDemandResolverRunner(
        _spec(end_date="2027-07-15"),
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    required = resolver._required(schedules[0])
    archive = _archive(
        tmp_path, required, "demand-stale-source",
        finished_at="2027-01-01T00:00:00Z")
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["build_fingerprint"]["source_files"]["pfe"]["sha256"] = "0" * 64
    (archive / "demand_meta.json").write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="different source code"):
        validate_demand_archive(archive, required)


def test_demand_source_identity_covers_every_demand_module():
    paths = {
        path.resolve()
        for path in demand_source_paths(monthly_demand._PROJECT_ROOT).values()
    }
    expected = {
        path.resolve()
        for package in ("demand", "traffic_sim/demand")
        for path in (monthly_demand._PROJECT_ROOT / package).glob("*.py")
    }
    assert expected <= paths


def test_multi_envelope_resolution_is_frozen_and_routes_by_date(tmp_path):
    FakeChildRunner.created = []
    spec = _spec()
    schedules = generate_closure_schedules(spec)
    assert [item.intervals[0].start_time[:10] for item in schedules] == [
        "2027-07-15", "2027-07-22"]
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    required = _required_for(resolver, schedules)
    older = {}
    for index, item in enumerate(required.values()):
        older[item.build_key] = _archive(
            tmp_path,
            item,
            f"demand-old-{index}",
            finished_at=f"2027-01-0{index + 1}T00:00:00Z",
        )

    resolver.prepare(schedules)
    provenance = resolver.provenance()
    assert provenance["kind"] == "multi_envelope_monthly_sumo_backend"
    assert len(provenance["envelope_backends"]) == 2
    for schedule in schedules:
        resolver.run_candidate(
            schedule,
            target_repetitions={"q10": 0, "q50": 0, "q90": 0},
            existing=None,
            stage="pilot",
        )
    assert sorted(
        child.expected.start_date for child in FakeChildRunner.created
    ) == ["2027-07-15", "2027-07-22"]
    assert all(len(child.calls) == 1 for child in FakeChildRunner.created)

    # A newer matching run appears, but the immutable release must retain the
    # archives selected before any simulation evidence was written.
    first_required = next(iter(required.values()))
    _archive(
        tmp_path,
        first_required,
        "demand-newer",
        finished_at="2027-12-31T00:00:00Z",
    )
    FakeChildRunner.created = []
    resumed = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    resumed.prepare(schedules)
    selected = {
        item["demand_build_spec"]["build_key"]: Path(item["archive"])
        for item in resumed.provenance()["demand_release"]["entries"]
    }
    assert selected[first_required.build_key] == older[first_required.build_key]


def test_equal_envelopes_share_one_archive_and_one_child(tmp_path):
    FakeChildRunner.created = []
    spec = _spec(end_date="2027-07-15", latest_end="08:00")
    schedules = generate_closure_schedules(spec)
    assert len(schedules) == 5
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    required = resolver._required(schedules[0])
    _archive(
        tmp_path, required, "demand-shared",
        finished_at="2027-01-01T00:00:00Z")
    resolver.prepare(schedules)
    assert len(FakeChildRunner.created) == 1


def test_independent_daily_units_keep_exact_dates_and_full_recovery(tmp_path):
    FakeChildRunner.created = []
    spec = ClosureSearchSpec(
        search_id="independent-demand",
        directed_edges=("edge-a",),
        demand_build_id="forecast-daily-release-v1",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-16",
        allowed_weekdays=(3, 4),
        required_work_minutes=8 * 60,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("15:00", "22:00"),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )
    parent = next(
        item for item in generate_closure_schedules(spec)
        if item.day_count == 2 and item.daily_start == "15:00"
    )
    units, _ = decompose_schedules(spec, (parent,))
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
        envelope_policy=INDEPENDENT_DAILY_ENVELOPE_POLICY,
    )
    required = _required_for(resolver, [item.schedule for item in units])
    assert len(required) == 2
    assert all(item.days == 3 for item in required.values())
    for index, item in enumerate(required.values()):
        _archive(
            tmp_path,
            item,
            f"demand-independent-{index}",
            finished_at=f"2027-01-0{index + 1}T00:00:00Z",
        )

    resolver.prepare([item.schedule for item in units])

    assert sorted(
        child.expected.start_date for child in FakeChildRunner.created
    ) == ["2027-07-14", "2027-07-15"]


def test_independent_windows_on_one_date_share_the_canonical_three_day_archive():
    spec = ClosureSearchSpec(
        search_id="independent-full-day",
        directed_edges=("edge-a",),
        demand_build_id="forecast-daily-release-v2",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-15",
        allowed_weekdays=(3,),
        required_work_minutes=15,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("00:00", "24:00"),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="study",
    )
    schedules = generate_closure_schedules(spec)
    required = {resolver._required(item) for item in schedules}
    assert len(required) == 1
    only = required.pop()
    assert (only.start_date, only.days) == ("2027-07-14", 3)


def test_independent_source_year_boundary_stays_fail_closed():
    spec = ClosureSearchSpec(
        search_id="independent-year-boundary",
        directed_edges=("edge-a",),
        demand_build_id="forecast-daily-release-v3",
        source="forecast",
        permitted_date_start="2027-01-01",
        permitted_date_end="2027-01-01",
        allowed_weekdays=(4,),
        required_work_minutes=15,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("00:00", "02:00"),
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_balanced_daily_v1",
    )
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=3600,
        study_provenance_key="study",
    )
    schedules = generate_closure_schedules(spec)
    early = next(item for item in schedules if item.daily_start == "00:00")
    late = next(item for item in schedules if item.daily_start == "01:15")
    with pytest.raises(ValueError, match="outside the downloaded 2027"):
        resolver._required(early)
    assert resolver._required(late).start_date == "2027-01-01"


def test_missing_archive_is_built_once_then_resolved(tmp_path):
    FakeChildRunner.created = []
    spec = _spec(end_date="2027-07-15")
    schedules = generate_closure_schedules(spec)
    built = []

    def builder(required):
        built.append(required.build_key)
        _archive(
            tmp_path,
            required,
            "demand-built",
            finished_at="2027-01-01T00:00:00Z",
        )

    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        demand_builder=builder,
        runner_factory=FakeChildRunner,
    )
    resolver.prepare(schedules)
    assert len(built) == 1
    assert len(find_demand_archives(
        tmp_path, resolver._required(schedules[0]))) == 1


def _live_release(root):
    """Create a live release product tree with sentinel bytes.

    One product (calibrated_v2.agents.json) is deliberately left missing so
    the restore path proves absence is preserved too."""
    contents = {}
    for relative in monthly_demand.LIVE_DEMAND_RELEASE_PRODUCTS:
        if relative.name == "calibrated_v2.agents.json":
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"live:{relative.name}")
        contents[str(relative)] = target.read_bytes()
    return contents


def _scribbling_builder(runs_root, live_root, *, fail=False):
    """Fake demand builder with the real one's side effect: it writes the
    envelope's demand THROUGH every live release path."""
    def builder(required):
        for relative in monthly_demand.LIVE_DEMAND_RELEASE_PRODUCTS:
            target = live_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"envelope:{required.build_key}")
        if fail:
            raise RuntimeError("demand build failed after touching live state")
        _archive(
            runs_root,
            required,
            f"demand-built-{required.build_key}",
            finished_at="2027-01-01T00:00:00Z",
        )
    return builder


def test_envelope_build_restores_live_release_products(tmp_path):
    FakeChildRunner.created = []
    spec = _spec(end_date="2027-07-15")
    schedules = generate_closure_schedules(spec)
    live_root = tmp_path / "live"
    before = _live_release(live_root)

    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        demand_builder=_scribbling_builder(tmp_path, live_root),
        runner_factory=FakeChildRunner,
        live_release_root=live_root,
    )
    resolver.prepare(schedules)

    for relative in monthly_demand.LIVE_DEMAND_RELEASE_PRODUCTS:
        target = live_root / relative
        if str(relative) in before:
            assert target.read_bytes() == before[str(relative)], relative
        else:
            assert not target.exists(), (
                f"missing live product must stay missing: {relative}")


def test_failed_envelope_build_still_restores_live_release(tmp_path):
    FakeChildRunner.created = []
    spec = _spec(end_date="2027-07-15")
    schedules = generate_closure_schedules(spec)
    live_root = tmp_path / "live"
    before = _live_release(live_root)

    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        demand_builder=_scribbling_builder(tmp_path, live_root, fail=True),
        runner_factory=FakeChildRunner,
        live_release_root=live_root,
    )
    with pytest.raises(RuntimeError, match="after touching live state"):
        resolver.prepare(schedules)

    for relative in monthly_demand.LIVE_DEMAND_RELEASE_PRODUCTS:
        target = live_root / relative
        if str(relative) in before:
            assert target.read_bytes() == before[str(relative)], relative
        else:
            assert not target.exists(), relative


def test_missing_archive_fails_closed_when_build_is_disabled(tmp_path):
    spec = _spec(end_date="2027-07-15")
    schedules = generate_closure_schedules(spec)
    resolver = MonthlyDemandResolverRunner(
        spec,
        baseline_trip_duration_p99_s=1800,
        study_provenance_key="study",
        runs_root=tmp_path,
        release_root=tmp_path / "releases",
        build_missing=False,
        runner_factory=FakeChildRunner,
    )
    with pytest.raises(FileNotFoundError, match="no succeeded immutable"):
        resolver.prepare(schedules)
