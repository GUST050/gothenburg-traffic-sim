import json
import hashlib
from pathlib import Path

import pytest

from run_monthly_closure_search import _bounded_exhaustive_builder
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    DailyTimeBand,
    load_closure_search_spec,
)
from traffic_sim.simulation.finalist_decision import (
    CandidateEvidence,
    FinalistPolicy,
    PairedObservation,
)
from traffic_sim.simulation.monthly_search import (
    MonthlySearchPolicy,
    canonical_seed,
    run_monthly_search,
)
from traffic_sim.simulation.pilot_selection import PilotPolicy
from traffic_sim.simulation.search_workspace import load_search_workspace


def _spec(search_id="monthly-resume"):
    return ClosureSearchSpec(
        search_id=search_id,
        directed_edges=("edge-a",),
        demand_build_id="forecast-release",
        source="forecast",
        permitted_date_start="2027-07-05",
        permitted_date_end="2027-07-06",
        required_work_minutes=60,
        max_consecutive_start_days=1,
        permitted_daily_band=DailyTimeBand("08:00", "10:00"),
    )


def _policy(status="provisional"):
    return MonthlySearchPolicy(
        policy_id="monthly-policy-v1",
        benchmark_id="golden-monthly-smoke-v1",
        status=status,
        pilot=PilotPolicy(
            retention_band_s=1000.0,
            repetitions_per_variant=1,
            minimum_finalists=2,
            maximum_finalists=4,
        ),
        finalist=FinalistPolicy(
            absolute_precision_floor_s=10.0,
            practical_equivalence_s=5.0,
            initial_repetitions=4,
            max_repetitions=5,
        ),
    )


def _screen_builder(spec_path):
    spec = load_closure_search_spec(spec_path)
    schedules = generate_closure_schedules(spec)
    entries = [
        {"schedule_id": schedule.schedule_id, "selection_reasons": ["test"]}
        for schedule in schedules[:2]
    ]
    return {
        "schema_version": 1,
        "kind": "monthly_closure_proxy_screening",
        "proxy_version": "test-proxy",
        "search": spec.to_dict(),
        "candidate_count": len(schedules),
        "scoreable_candidate_count": len(schedules),
        "ranked_candidates": [],
        "shortlist": {"entries": entries},
    }


class FakeRunner:
    def __init__(
        self,
        *,
        fail_once_candidate=None,
        noncanonical=False,
        identity="fake-v1",
    ):
        self.calls = []
        self.fail_once_candidate = fail_once_candidate
        self.failed = False
        self.noncanonical = noncanonical
        self.identity = identity

    def provenance(self):
        return {
            "kind": "fake_monthly_backend",
            "simulation_mode": "meso",
            "identity": self.identity,
        }

    def run_candidate(
        self,
        schedule,
        *,
        target_repetitions,
        existing,
        stage,
    ):
        self.calls.append((schedule.schedule_id, stage, dict(target_repetitions)))
        if (
            self.fail_once_candidate == schedule.schedule_id
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError("transient SUMO interruption")
        deltas = {
            "08:00": 10.0,
            "08:15": 100.0,
        }
        delta = deltas.get(schedule.daily_start, 500.0)
        day_number = int(schedule.first_work_date[-2:])
        baseline = 1000.0 + day_number
        baseline_id = f"baseline-{schedule.first_work_date}"
        observations = []
        for variant in ("q10", "q50", "q90"):
            for repetition in range(target_repetitions[variant]):
                seed = canonical_seed(variant, repetition)
                if self.noncanonical and not observations:
                    seed += 100
                observations.append(
                    PairedObservation(
                        candidate_id=schedule.schedule_id,
                        demand_variant=variant,
                        seed=seed,
                        baseline_time_loss_s=baseline,
                        candidate_time_loss_s=baseline + delta,
                        matched_baseline_id=baseline_id,
                        provenance_key="monthly-study-provenance",
                    )
                )
        return CandidateEvidence(
            candidate_id=schedule.schedule_id,
            observations=tuple(observations),
        )


class PreparingRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.prepared = None

    def prepare(self, schedules):
        self.prepared = tuple(schedule.schedule_id for schedule in schedules)

    def provenance(self):
        assert self.prepared is not None
        return {
            **super().provenance(),
            "prepared_schedule_ids": list(self.prepared),
        }


def test_policy_round_trips_with_stable_content_key():
    policy = _policy()
    loaded = MonthlySearchPolicy.from_dict(policy.to_dict())
    assert loaded.content_key == policy.content_key
    assert loaded.pilot.variants == ("q10", "q50", "q90")
    assert loaded.finalist.variants == ("q10", "q50", "q90")


def test_backend_prepares_only_screened_shortlist_before_provenance(tmp_path):
    runner = PreparingRunner()
    result = run_monthly_search(
        _spec("monthly-prepare-order"),
        _policy(),
        runner=runner,
        screen_builder=_screen_builder,
        root=tmp_path,
    )
    assert runner.prepared is not None
    assert len(runner.prepared) == 2
    assert result["simulation_backend"]["prepared_schedule_ids"] == list(
        runner.prepared
    )


def test_bounded_exhaustive_result_is_ui_exposable_and_gate_aware(
        tmp_path, monkeypatch):
    """No proxy is involved in bounded-exhaustive screening: every ranked
    candidate carries real SUMO evidence, so the result may reach the UI
    with the restricted wording.  The global-best claim follows the
    tracked held-out gate record: absent/failed record → withheld."""
    import traffic_sim.simulation.monthly_search as monthly_search

    def exhaustive_builder(spec_path):
        payload = _screen_builder(spec_path)
        payload["proxy_version"] = "bounded_exhaustive_sumo_v1"
        return payload

    monkeypatch.setattr(
        monthly_search, "HELDOUT_GATE_RECORD", tmp_path / "missing.json")
    result = run_monthly_search(
        _spec("monthly-exhaustive-claims"),
        _policy(),
        runner=FakeRunner(),
        screen_builder=exhaustive_builder,
        root=tmp_path / "no-gate",
    )
    boundary = result["claim_boundary"]
    assert result["status"] == "unique_winner"
    assert boundary["ui_exposure_allowed"] is True
    assert boundary["global_best_claim_allowed"] is False
    assert boundary["best_result_scope"] == "sumo_verified_bounded_exhaustive"

    passing = tmp_path / "gate.json"
    passing.write_text(json.dumps({
        "kind": "monthly_proxy_validation_gate_record",
        "heldout_set": "v2",
        "proxy_version": "monthly_proxy_v1",
        "manifest_content_key": "m" * 64,
        "gate_status": "pass",
        "ui_exposure_allowed": True,
        "global_best_claim_allowed": True,
    }))
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_RECORD", passing)
    released = run_monthly_search(
        _spec("monthly-exhaustive-released"),
        _policy(),
        runner=FakeRunner(),
        screen_builder=exhaustive_builder,
        root=tmp_path / "with-gate",
    )
    boundary = released["claim_boundary"]
    assert boundary["ui_exposure_allowed"] is True
    assert boundary["global_best_claim_allowed"] is True
    assert boundary["heldout_gate_record"]["gate_status"] == "pass"


def test_validated_proxy_screening_is_released_but_others_stay_closed(
        tmp_path, monkeypatch):
    import traffic_sim.simulation.monthly_search as monthly_search

    passing = tmp_path / "gate.json"
    passing.write_text(json.dumps({
        "kind": "monthly_proxy_validation_gate_record",
        "heldout_set": "v2",
        "proxy_version": "monthly_proxy_v1",
        "manifest_content_key": "m" * 64,
        "gate_status": "pass",
        "ui_exposure_allowed": True,
        "global_best_claim_allowed": True,
    }))
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_RECORD", passing)

    def proxy_builder(version):
        def build(spec_path):
            payload = _screen_builder(spec_path)
            payload["proxy_version"] = version
            return payload
        return build

    covered = run_monthly_search(
        _spec("monthly-proxy-released"),
        _policy(),
        runner=FakeRunner(),
        screen_builder=proxy_builder("monthly_proxy_v1"),
        root=tmp_path / "covered",
    )
    assert covered["claim_boundary"]["ui_exposure_allowed"] is True
    assert covered["claim_boundary"]["global_best_claim_allowed"] is True
    assert covered["claim_boundary"]["best_result_scope"] == (
        "sumo_verified_monthly_shortlist_heldout_validated")

    uncovered = run_monthly_search(
        _spec("monthly-proxy-uncovered"),
        _policy(),
        runner=FakeRunner(),
        screen_builder=proxy_builder("some_other_proxy_v9"),
        root=tmp_path / "uncovered",
    )
    assert uncovered["claim_boundary"]["ui_exposure_allowed"] is False
    assert uncovered["claim_boundary"]["global_best_claim_allowed"] is False


def test_failed_or_malformed_gate_record_fails_closed(tmp_path):
    from traffic_sim.simulation.monthly_search import load_passing_heldout_gate
    missing = load_passing_heldout_gate(tmp_path / "none.json")
    assert missing is None
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({
        "kind": "monthly_proxy_validation_gate_record",
        "proxy_version": "monthly_proxy_v1",
        "gate_status": "fail",
        "ui_exposure_allowed": False,
        "global_best_claim_allowed": False,
    }))
    assert load_passing_heldout_gate(failed) is None
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not json")
    assert load_passing_heldout_gate(malformed) is None


def test_tracked_v2_gate_record_is_a_valid_passing_release_record():
    from traffic_sim.simulation.monthly_search import load_passing_heldout_gate
    record = load_passing_heldout_gate()
    assert record is not None
    assert record["heldout_set"] == "v2"
    assert record["proxy_version"] == "monthly_proxy_v1"
    metrics = record["metrics"]
    thresholds = record["thresholds"]
    assert metrics["practical_winner_recall"] >= (
        thresholds["practical_winner_recall_minimum"])
    assert metrics["p90_normalized_shortlist_regret"] <= (
        thresholds["p90_normalized_shortlist_regret_maximum"])
    assert metrics["failure_disqualification_recall"] >= (
        thresholds["failure_disqualification_recall_minimum"])
    assert record["completed_cases"] == record["required_cases"] == 12


def test_runs_full_resumable_pipeline_and_remains_fail_closed(tmp_path):
    runner = FakeRunner()
    result = run_monthly_search(
        _spec(),
        _policy(),
        runner=runner,
        screen_builder=_screen_builder,
        root=tmp_path,
    )

    assert result["status"] == "unique_winner"
    assert result["closure_search_spec"] == _spec().to_dict()
    assert result["selected_schedules"][0]["daily_start"] == "08:00"
    assert result["claim_boundary"]["global_best_claim_allowed"] is False
    assert result["claim_boundary"]["ui_exposure_allowed"] is False
    assert result["screening"]["shortlist_count"] == 2

    workspace = load_search_workspace(tmp_path / _spec().search_id)
    assert workspace.status == "succeeded"
    kinds = [item["kind"] for item in workspace.manifest["artifacts"]]
    assert kinds.count("monthly_pilot_candidate") == 2
    assert kinds.count("monthly_finalist_candidate") == 2
    assert kinds.count("monthly_closure_search_result") == 1

    no_work = FakeRunner()
    same = run_monthly_search(
        _spec(),
        _policy(),
        runner=no_work,
        screen_builder=lambda _: pytest.fail("screening reran"),
        root=tmp_path,
    )
    assert same == result
    assert no_work.calls == []
    with pytest.raises(ValueError, match="backend provenance differs"):
        run_monthly_search(
            _spec(),
            _policy(),
            runner=FakeRunner(identity="different-archive"),
            screen_builder=lambda _: pytest.fail("screening reran"),
            root=tmp_path,
        )


def test_restart_skips_immutable_completed_candidate(tmp_path):
    schedules = generate_closure_schedules(_spec())
    first_two = schedules[:2]
    runner = FakeRunner(fail_once_candidate=first_two[1].schedule_id)

    with pytest.raises(RuntimeError, match="transient"):
        run_monthly_search(
            _spec(),
            _policy(),
            runner=runner,
            screen_builder=_screen_builder,
            root=tmp_path,
        )

    workspace = load_search_workspace(tmp_path / _spec().search_id)
    assert workspace.status == "running"
    assert workspace.manifest["progress"]["phase"] == "pilot"
    assert "transient" in workspace.manifest["progress"]["last_error"]
    first_pilot_calls = [
        call for call in runner.calls
        if call[0] == first_two[0].schedule_id and call[1] == "pilot"
    ]
    assert len(first_pilot_calls) == 1

    result = run_monthly_search(
        _spec(),
        _policy(),
        runner=runner,
        screen_builder=lambda _: pytest.fail("screening reran"),
        root=tmp_path,
    )
    assert result["status"] == "unique_winner"
    first_pilot_calls = [
        call for call in runner.calls
        if call[0] == first_two[0].schedule_id and call[1] == "pilot"
    ]
    assert len(first_pilot_calls) == 1


def test_backend_noncanonical_seed_fails_without_finishing_workspace(tmp_path):
    with pytest.raises(ValueError, match="non-canonical"):
        run_monthly_search(
            _spec(),
            _policy(),
            runner=FakeRunner(noncanonical=True),
            screen_builder=_screen_builder,
            root=tmp_path,
        )
    workspace = load_search_workspace(tmp_path / _spec().search_id)
    assert workspace.status == "running"
    assert "non-canonical" in workspace.manifest["progress"]["last_error"]


def test_policy_change_cannot_reinterpret_running_workspace(tmp_path):
    schedules = generate_closure_schedules(_spec())
    runner = FakeRunner(fail_once_candidate=schedules[1].schedule_id)
    with pytest.raises(RuntimeError):
        run_monthly_search(
            _spec(),
            _policy(),
            runner=runner,
            screen_builder=_screen_builder,
            root=tmp_path,
        )

    changed = MonthlySearchPolicy(
        policy_id="monthly-policy-v2",
        benchmark_id="other-benchmark",
        status="provisional",
        pilot=_policy().pilot,
        finalist=_policy().finalist,
    )
    with pytest.raises(ValueError, match="policy differs"):
        run_monthly_search(
            _spec(),
            changed,
            runner=runner,
            screen_builder=_screen_builder,
            root=tmp_path,
        )

    manifest = json.loads(
        (tmp_path / _spec().search_id / "manifest.json").read_text()
    )
    assert manifest["status"] == "running"


def test_bounded_exhaustive_screening_has_a_hard_cap(tmp_path):
    from traffic_sim.core.contracts import write_closure_search_spec

    path = tmp_path / "search.json"
    write_closure_search_spec(path, _spec())
    schedules = generate_closure_schedules(_spec())
    artifact = _bounded_exhaustive_builder(
        path,
        maximum_candidates=len(schedules),
    )
    assert artifact["shortlist"]["selection_complete"] is True
    assert len(artifact["shortlist"]["entries"]) == len(schedules)
    assert artifact["claim_boundary"]["global_best_claim_allowed"] is False

    with pytest.raises(ValueError, match="above the explicit cap"):
        _bounded_exhaustive_builder(
            path,
            maximum_candidates=len(schedules) - 1,
        )


def test_invalid_screening_is_not_published_as_immutable_evidence(tmp_path):
    with pytest.raises(ValueError, match="shortlist is empty"):
        run_monthly_search(
            _spec(),
            _policy(),
            runner=FakeRunner(),
            screen_builder=lambda _: {
                "kind": "monthly_closure_proxy_screening",
                "search": _spec().to_dict(),
                "shortlist": {"entries": []},
            },
            root=tmp_path,
        )
    workspace = load_search_workspace(tmp_path / _spec().search_id)
    assert not any(
        record["kind"] == "monthly_proxy_screening"
        for record in workspace.manifest["artifacts"]
    )


def test_tracked_golden_monthly_search_passes_but_keeps_release_gate_closed():
    record = json.loads(
        Path("validation/golden_monthly_search_v1.json").read_text()
    )
    assert record["status"] == "passing"
    assert record["result"]["status"] == "unique_winner"
    assert record["result"]["precision_met"] is True
    assert record["result"]["variant_repetitions"] == {
        "q10": 4,
        "q50": 5,
        "q90": 7,
    }
    assert record["resume_validation"]["status"] == "passing"
    assert record["claim_boundary"]["global_best_claim_allowed"] is False
    assert record["claim_boundary"]["ui_exposure_allowed"] is False
    for label, path in (
        (
            "search_spec_sha256",
            Path("validation/golden_monthly_search_spec_v6.json"),
        ),
        (
            "policy_sha256",
            Path("validation/monthly_search_policy_v1.json"),
        ),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            record["provenance"][label]
        )
    policy = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v1.json").read_text()
    ))
    assert policy.content_key == record["policy"]["content_key"]
