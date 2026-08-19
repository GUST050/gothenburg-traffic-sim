import hashlib
import json
import shutil
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


GENERATION = {
    "build_candidates": {"sha256": "a" * 64, "bytes": 1},
    "pfe": {"sha256": "b" * 64, "bytes": 1},
    "build_sumo_demand": {"sha256": "c" * 64, "bytes": 1},
}


def _archive(root, required, name, *, finished_at, generation=None):
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
        # demand_meta.json is deliberately NOT written here: the `metadata`
        # dict below is the authoritative one and overwrites it, so a second
        # copy at this point would read as live while never surviving.
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
            # Real source hashes by default, so the reuse-validation tests see
            # a truthful archive. A caller that passes `generation` is testing
            # the generation-mixing guard and must be able to override exactly
            # the GENERATION_SOURCE_FILES entries -- this dict is written over
            # demand_meta.json below, so injecting it anywhere else is lost.
            "source_files": {
                **monthly_demand.demand_source_fingerprints(
                    monthly_demand._PROJECT_ROOT),
                **(dict(generation) if generation is not None else {}),
            },
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
    assert "--direction-stress-variants" in seen["command"]


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


def _two_required(tmp_path):
    resolver = MonthlyDemandResolverRunner(
        _spec(), baseline_trip_duration_p99_s=1800,
        study_provenance_key="study", runs_root=tmp_path / "runs",
        release_root=tmp_path / "releases", build_missing=False)
    schedules = generate_closure_schedules(_spec())
    required = _required_for(resolver, schedules)
    keys = sorted(required)
    if len(keys) < 2:
        pytest.skip("needs at least two distinct envelopes")
    return [(key, required[key]) for key in keys[:2]]


def test_release_mixing_demand_generations_is_refused(tmp_path, monkeypatch):
    """A search must not compare envelopes built by different generators.

    Every envelope build is a fresh subprocess importing whatever code is on
    disk, so a long search that straddles a generator change silently ends up
    with archives from both sides. That is not a paired comparison, and it is
    invisible in every downstream artifact unless something checks.
    """
    entries = {}
    for index, (key, required) in enumerate(_two_required(tmp_path)):
        generation = dict(GENERATION)
        if index:
            generation["build_candidates"] = {"sha256": "z" * 64, "bytes": 1}
        archive = _archive(
            tmp_path, required, f"archive-{index}",
            finished_at="2026-07-21T00:00:00Z", generation=generation)
        entries[key] = {"archive": str(archive)}

    with pytest.raises(ValueError, match="different candidate/solver"):
        monthly_demand._require_one_demand_generation(entries)


def test_release_of_one_generation_is_accepted(tmp_path):
    entries = {
        key: {"archive": str(_archive(
            tmp_path, required, f"same-{index}",
            finished_at="2026-07-21T00:00:00Z"))}
        for index, (key, required) in enumerate(_two_required(tmp_path))
    }
    monthly_demand._require_one_demand_generation(entries)


def test_archive_without_generator_hashes_is_refused(tmp_path):
    key, required = _two_required(tmp_path)[0]
    archive = _archive(tmp_path, required, "unfingerprinted",
                       finished_at="2026-07-21T00:00:00Z",
                       generation={"build_candidates": {"sha256": "", "bytes": 0}})
    with pytest.raises(ValueError, match="generator source hashes"):
        monthly_demand._require_one_demand_generation(
            {key: {"archive": str(archive)}})


class TestLiveReleaseKillSafety:
    """The restore lives in a finally block, which a kill skips. A marker on
    disk is what lets the next run put the deployed release back."""

    def _live(self, tmp_path):
        root = tmp_path / "box"
        (root / "sumo").mkdir(parents=True)
        (root / "web" / "data" / "scenarios").mkdir(parents=True)
        (root / "sumo" / "calibrated.rou.xml").write_text("live routes")
        (root / "web" / "data" / "scenarios" / "baseline.json").write_text("live")
        return root

    def _snapshot(self, tmp_path, root):
        return monthly_demand.snapshot_live_demand_release(
            root=root,
            products=(Path("sumo") / "calibrated.rou.xml",),
            directories=(Path("web") / "data" / "scenarios",),
            marker=tmp_path / "marker.json")

    def test_a_killed_run_is_recovered_from_the_marker(self, tmp_path):
        root = self._live(tmp_path)
        self._snapshot(tmp_path, root)   # deliberately never restored
        (root / "sumo" / "calibrated.rou.xml").write_text("envelope routes")
        (root / "web" / "data" / "scenarios" / "baseline.json").unlink()

        recovered = monthly_demand.recover_live_demand_release(
            tmp_path / "marker.json")

        assert recovered is not None
        assert (root / "sumo" / "calibrated.rou.xml").read_text() == "live routes"
        assert (root / "web" / "data" / "scenarios"
                / "baseline.json").read_text() == "live"
        assert not (tmp_path / "marker.json").exists()

    def test_scenario_directory_contents_are_restored_exactly(self, tmp_path):
        root = self._live(tmp_path)
        snapshot = self._snapshot(tmp_path, root)
        # A build clears stale scenarios and writes its own.
        (root / "web" / "data" / "scenarios" / "baseline.json").unlink()
        (root / "web" / "data" / "scenarios" / "intruder.json").write_text("new")

        monthly_demand.restore_live_demand_release(snapshot)

        scenarios = sorted(
            path.name for path in (root / "web" / "data" / "scenarios").iterdir())
        assert scenarios == ["baseline.json"]

    def test_a_completed_restore_leaves_no_marker(self, tmp_path):
        root = self._live(tmp_path)
        snapshot = self._snapshot(tmp_path, root)
        monthly_demand.restore_live_demand_release(snapshot)
        assert not (tmp_path / "marker.json").exists()
        assert monthly_demand.recover_live_demand_release(
            tmp_path / "marker.json") is None

    def test_an_unreadable_marker_is_refused_not_guessed(self, tmp_path):
        marker = tmp_path / "marker.json"
        marker.write_text(json.dumps({"kind": "something_else"}))
        with pytest.raises(ValueError, match="unreadable live release"):
            monthly_demand.recover_live_demand_release(marker)

    def test_a_marker_whose_snapshot_is_gone_is_dropped(self, tmp_path):
        root = self._live(tmp_path)
        snapshot = self._snapshot(tmp_path, root)
        shutil.rmtree(snapshot["directory"])
        assert monthly_demand.recover_live_demand_release(
            tmp_path / "marker.json") is None
        assert not (tmp_path / "marker.json").exists()


_ARCHIVE_LABELS = {
    "candidates.rou.xml": "candidate_routes",
    "candidates.meta.json": "candidate_metadata",
    "calibrated.rou.xml": "calibrated_q50",
    "calibrated.agents.json": "calibrated_q50_agents",
    "calibrated_v1.rou.xml": "calibrated_v1",
    "calibrated_v1.agents.json": "calibrated_v1_agents",
    "calibrated_v2.rou.xml": "calibrated_v2",
    "calibrated_v2.agents.json": "calibrated_v2_agents",
}


def _rewrite_metadata(archive, metadata):
    """Write demand_meta.json and re-bind every artifact and manifest hash.

    The archive seals its artifacts twice over -- once in the build
    fingerprint, once in the run manifest -- so a test that edits a route file
    has to re-seal both or it is testing the tamper detector instead of the
    contract it means to exercise.
    """
    metadata["build_fingerprint"]["artifacts"] = {
        label: {"bytes": (archive / filename).stat().st_size,
                "sha256": _sha(archive / filename)}
        for filename, label in _ARCHIVE_LABELS.items()
    }
    (archive / "demand_meta.json").write_text(json.dumps(metadata))
    manifest = json.loads((archive / "manifest.json").read_text())
    manifest["outputs"] = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha(path)}
        for path in sorted(archive.iterdir())
        if path.name != "manifest.json"
    ]
    (archive / "manifest.json").write_text(json.dumps(manifest))

# ── Baseline-rule edge support (2026-08-06) ──────────────────────────────────
# The annual warming, having cleared two earlier faults, failed here: the
# archive contract still demanded the full-edge support augmentation that the
# baseline rule ("only what is measured is simulated") deliberately deleted on
# 2026-08-05, so no archive built under the current rule could ever validate.

def _baseline_rule_archive(tmp_path, name, *, mode="single_pool",
                           calibrated_edges="edge-a"):
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
    archive = _archive(tmp_path, required, name,
                       finished_at="2027-01-01T00:00:00Z")
    for variant in ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                    "calibrated_v2.rou.xml"):
        (archive / variant).write_text(
            f"<routes><vehicle id='v' depart='0'>"
            f"<route edges='{calibrated_edges}'/></vehicle></routes>")
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["edge_support_augmentation"] = {
        "schema_version": 1, "status": "disabled_baseline_rule", "variants": {}}
    metadata["candidate_provenance"] = {
        "schema_version": 1, "status": "pass", "mode": mode}
    _rewrite_metadata(archive, metadata)
    return archive, required


def test_archive_accepts_the_baseline_rule_edge_support_state(tmp_path):
    archive, required = _baseline_rule_archive(tmp_path, "demand-baseline")

    record = validate_demand_archive(archive, required)

    assert record["demand_build_spec"]["purpose"] == "closure_envelope"


def test_archive_rejects_a_route_outside_its_candidate_pool(tmp_path):
    """Containment is the half of the old contract that still binds."""
    archive, required = _baseline_rule_archive(
        tmp_path, "demand-stray", calibrated_edges="edge-a edge-invented")

    with pytest.raises(ValueError, match="absent from its candidate pool"):
        validate_demand_archive(archive, required)


def test_day_assembled_archive_is_not_held_to_a_single_days_pool(tmp_path):
    """The archive keeps only the LAST day's pool, so containment cannot apply.

    Every earlier day's routes legitimately use edges that pool never had --
    52 of them on the first annual envelope. The per-day provenance proofs
    cover those vehicles instead, and they are strictly stronger: they name
    the candidate, not merely the edge set.
    """
    archive, required = _baseline_rule_archive(
        tmp_path, "demand-assembled", mode="assembled_day_library",
        calibrated_edges="edge-a edge-from-another-day")

    record = validate_demand_archive(archive, required)

    assert record["archive"].endswith("demand-assembled")


def test_archive_rejects_an_unknown_edge_support_status(tmp_path):
    archive, required = _baseline_rule_archive(tmp_path, "demand-unknown")
    metadata = json.loads((archive / "demand_meta.json").read_text())
    metadata["edge_support_augmentation"]["status"] = "something_new"
    _rewrite_metadata(archive, metadata)

    with pytest.raises(ValueError, match="unknown edge-support status"):
        validate_demand_archive(archive, required)
