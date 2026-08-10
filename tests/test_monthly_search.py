import dataclasses
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


@pytest.fixture(autouse=True)
def _unit_adoption_source_identity(monkeypatch):
    """Exercise adoption mechanics independently of retired tracked evidence.

    The live v6 certificate is intentionally stale after production source
    changes. These unit tests synthesize record/certificate mutations and need
    one accepted control pair; source-drift behavior is covered exhaustively in
    ``test_heldout_gate.py`` against the real fingerprint checker.
    """
    import traffic_sim.simulation.heldout_gate as heldout_gate
    monkeypatch.setattr(
        heldout_gate, "_source_fingerprints_match", lambda _manifest: True)


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


V4_MANIFEST = Path("validation/monthly_proxy_manifest_v4.json")


def _frozen_campaign_gate_record(**overrides):
    """A gate record for the frozen campaign, as the runner would write it.

    Built from the frozen manifest rather than hardcoded, so a record can only
    look valid while it names the campaign and manifest actually frozen.
    """
    manifest = json.loads(V4_MANIFEST.read_text())
    record = {
        "kind": "monthly_proxy_validation_gate_record",
        "heldout_set": manifest["campaign_version"],
        "manifest_content_key": manifest["content_key"],
        "required_cases": len(manifest["cases"]),
        "completed_cases": len(manifest["cases"]),
        "proxy_version": manifest["proxy_version"],
        "shortlist_version": manifest["shortlist_version"],
        "shortlist_policy_content_key": manifest["shortlist_policy_content_key"],
        "gate_status": "pass",
        "ui_exposure_allowed": True,
        "global_best_claim_allowed": True,
    }
    record.update(overrides)
    return record


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


class CostedFakeRunner(FakeRunner):
    def run_candidate(self, schedule, **kwargs):
        evidence = super().run_candidate(schedule, **kwargs)
        hours = 1.0 if schedule.daily_start == "08:15" else 10.0
        return CandidateEvidence(
            candidate_id=evidence.candidate_id,
            observations=evidence.observations,
            disruption=({
                "vehicles_affected": 10,
                "vehicles_no_detour": 0,
                "added_vehicle_hours": hours,
                "added_metres_total": hours * 100,
            },),
        )


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


def _write_adopted_pair(tmp_path, name="adopted", **record_overrides):
    """Write a gate record AND its binding adoption certificate.

    LUNA-V5-01: adoption needs two artifacts; a record alone never opens the
    gate. The exhaustive mutation matrix lives in tests/test_heldout_gate.py.
    """
    import hashlib
    from traffic_sim.simulation.heldout_gate import (
        BOUNDED_CLAIM_SCOPE, CERTIFICATE_KIND, CERTIFICATE_SCHEMA_VERSION,
        canonical_content_key)
    man = json.loads(Path("validation/monthly_proxy_manifest_v6.json").read_text())
    record = _frozen_campaign_gate_record(**record_overrides)
    # Rebind identity to the ADOPTABLE campaign: v4 is explicitly rejected.
    for field in ("heldout_set", "manifest_content_key", "required_cases",
                  "completed_cases", "proxy_version", "shortlist_version",
                  "shortlist_policy_content_key"):
        if field not in record_overrides:
            record[field] = {
                "heldout_set": man["campaign_version"],
                "manifest_content_key": man["content_key"],
                "required_cases": len(man["cases"]),
                "completed_cases": len(man["cases"]),
                "proxy_version": man["proxy_version"],
                "shortlist_version": man["shortlist_version"],
                "shortlist_policy_content_key": man["shortlist_policy_content_key"],
            }[field]
    record.setdefault("schema_version", 1)
    record.setdefault("case_count", len(man["cases"]))
    record.setdefault("gate_checks", {
        "practical_winner_recall": True, "p90_normalized_shortlist_regret": True,
        "failure_disqualification_recall": True, "ranking_case_coverage": True,
        "all_shortlists_contain_eligible_candidate": True,
        "discriminating_case_coverage": True,
        "discriminating_practical_winner_recall": True})
    # EXACTLY the metric set the production evaluator emits.
    record.setdefault("metrics", {
        "winner_recall": 1.0, "practical_winner_recall": 1.0,
        "p90_normalized_shortlist_regret": 0.0, "median_spearman": -0.371429,
        "spearman_case_fraction": 1.0, "ranking_case_fraction": 1.0,
        "discriminating_case_fraction": 0.6,
        "discriminating_practical_winner_recall": 1.0,
        "median_spearman_discriminating": -0.637363,
        "median_objective_spread_s": 436.1,
        "failure_disqualification_recall": 0.681944,
        "total_disqualified_schedules": 25})
    # Production thresholds = manifest gate + the ranking-coverage minimum.
    record.setdefault("thresholds", {
        **man["gate"],
        "minimum_ranking_case_fraction": man["minimum_ranking_case_fraction"]})
    record.setdefault("practical_winner_definition", "within the band")
    record.setdefault("regret_definition", "normalised regret")
    record.setdefault("failure_recall_definition", "disqualifications caught")
    gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
    gp = tmp_path / f"{name}-gate.json"
    gp.write_bytes(gate_bytes)
    cert = {
        "schema_version": CERTIFICATE_SCHEMA_VERSION,
        "kind": CERTIFICATE_KIND,
        "gate_record_sha256": hashlib.sha256(gate_bytes).hexdigest(),
        "gate_record_bytes": len(gate_bytes),
        "manifest_path": "validation/monthly_proxy_manifest_v6.json",
        "manifest_content_key": man["content_key"],
        "campaign_version": man["campaign_version"],
        "required_cases": len(man["cases"]),
        "proxy_version": man["proxy_version"],
        "shortlist_version": man["shortlist_version"],
        "shortlist_policy_content_key": man["shortlist_policy_content_key"],
        "claim_scope": BOUNDED_CLAIM_SCOPE,
    }
    cert["content_key"] = canonical_content_key(cert)
    cp = tmp_path / f"{name}-cert.json"
    cp.write_text(json.dumps(cert, indent=2, sort_keys=True))
    return gp, cp


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
    monkeypatch.setattr(
        monthly_search, "HELDOUT_GATE_CERTIFICATE", tmp_path / "missing-cert.json")
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

    passing, cert = _write_adopted_pair(tmp_path)
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_RECORD", passing)
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_CERTIFICATE", cert)
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

    passing, cert = _write_adopted_pair(tmp_path)
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_RECORD", passing)
    monkeypatch.setattr(monthly_search, "HELDOUT_GATE_CERTIFICATE", cert)

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


def test_v4_adoption_was_rejected_and_the_default_path_is_closed():
    """LUNA-V4-04 was concluded REJECTED; LUNA-V5-01 removed its candidate.

    Adoption of a lone gate record was self-certifying. The product ships with
    neither artifact, so the gate is closed by default.
    """
    from traffic_sim.simulation.monthly_search import (
        HELDOUT_GATE_CERTIFICATE, HELDOUT_GATE_RECORD, load_passing_heldout_gate)
    assert not Path("validation/monthly_proxy_v4_gate.json").exists()
    assert not HELDOUT_GATE_RECORD.exists()
    assert not HELDOUT_GATE_CERTIFICATE.exists()
    assert load_passing_heldout_gate() is None


def test_a_record_without_its_certificate_never_adopts(tmp_path):
    """The exact v4 failure mode: a passing record with no post-review binding."""
    from traffic_sim.simulation.monthly_search import load_passing_heldout_gate
    gp, cp = _write_adopted_pair(tmp_path)
    assert load_passing_heldout_gate(gp, cp) is not None
    cp.unlink()
    assert load_passing_heldout_gate(gp, cp) is None


@pytest.mark.parametrize("overrides, why", [
    ({"heldout_set": "v2"}, "an earlier campaign label"),
    ({"heldout_set": "v3"}, "a relabelled earlier campaign"),
    ({"manifest_content_key": "0" * 64}, "a different frozen manifest"),
    ({"completed_cases": 4}, "an incomplete case set"),
    ({"required_cases": 4}, "a shrunken required-case count"),
    ({"gate_status": "fail"}, "a failing gate"),
    ({"ui_exposure_allowed": False}, "withheld UI exposure"),
    ({"global_best_claim_allowed": False}, "withheld global-best claim"),
    ({"shortlist_policy_content_key": "deadbeef"}, "a shortlist policy change"),
    ({"shortlist_version": "stratified_shortlist_v2"}, "an older shortlist version"),
    ({"kind": "something_else"}, "a wrong record kind"),
])
def test_tampered_or_earlier_gate_records_fail_closed(tmp_path, overrides, why):
    """No relabelled, incomplete, downgraded or tampered record opens the gate."""
    from traffic_sim.simulation.monthly_search import load_passing_heldout_gate
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(_frozen_campaign_gate_record(**overrides)))
    assert load_passing_heldout_gate(path) is None, why


def test_corrupted_gate_bytes_fail_against_the_certificate(tmp_path):
    """Any byte edited in the record breaks its certificate binding."""
    from traffic_sim.simulation.monthly_search import load_passing_heldout_gate
    gp, cp = _write_adopted_pair(tmp_path)
    assert load_passing_heldout_gate(gp, cp) is not None
    gp.write_bytes(gp.read_bytes() + b" ")
    assert load_passing_heldout_gate(gp, cp) is None


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


def test_objective_aligned_pipeline_uses_cost_in_pilot_and_final(tmp_path):
    policy = dataclasses.replace(
        _policy(), objective_method="closure_cost_v1"
    )
    result = run_monthly_search(
        _spec("monthly-objective-aligned"),
        policy,
        runner=CostedFakeRunner(),
        screen_builder=_screen_builder,
        root=tmp_path,
    )

    assert result["status"] == "unique_winner"
    assert result["selected_schedules"][0]["daily_start"] == "08:15"
    assert result["pilot_selection"]["method"] == (
        "deterministic_closure_cost_pilot_v1"
    )
    assert result["robust_decision"]["method"] == (
        "deterministic_worst_variant_closure_cost_v1"
    )


def test_multi_month_rolling_period_result_keeps_compact_start_date_summary(
    tmp_path,
):
    spec = dataclasses.replace(
        _spec("multi-month-rolling-period"),
        permitted_date_end="2027-08-05",
        interday_policy="independent_daily_reset_v1",
        work_allocation_policy="exact_equal_daily_v1",
        period_comparison_policy="rolling_period_v1",
    )
    policy = dataclasses.replace(
        _policy(), objective_method="closure_cost_v1"
    )

    def every_start_date(spec_path):
        loaded = load_closure_search_spec(spec_path)
        schedules = generate_closure_schedules(loaded)
        entries = [
            {"schedule_id": item.schedule_id, "selection_reasons": ["test"]}
            for item in schedules
            if item.daily_start == "08:00"
        ]
        return {
            "schema_version": 1,
            "kind": "monthly_closure_proxy_screening",
            "proxy_version": "test-proxy",
            "search": loaded.to_dict(),
            "candidate_count": len(entries),
            "scoreable_candidate_count": len(entries),
            "ranked_candidates": [],
            "shortlist": {"entries": entries},
        }

    class DateCostRunner(FakeRunner):
        def run_candidate(self, schedule, **kwargs):
            evidence = super().run_candidate(schedule, **kwargs)
            hours = abs(20 - int(schedule.first_work_date[-2:])) + 1
            return CandidateEvidence(
                candidate_id=evidence.candidate_id,
                observations=evidence.observations,
                disruption=({
                    "vehicles_affected": 10,
                    "vehicles_no_detour": 0,
                    "added_vehicle_hours": hours,
                    "added_metres_total": hours * 100,
                },),
            )

    result = run_monthly_search(
        spec,
        policy,
        runner=DateCostRunner(),
        screen_builder=every_start_date,
        root=tmp_path,
    )

    comparison = result["period_comparison"]
    assert comparison["comparison_complete"] is True
    assert comparison["start_date_count"] == 32
    assert comparison["best_start_date"] == "2027-07-20"
    assert comparison["best_schedule_id"] == result["winner_id"]
    assert result["claim_boundary"]["global_best_claim_allowed"] is False


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


def test_provisional_v2_policy_binds_the_closure_cost_objective():
    v1 = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v1.json").read_text()
    ))
    v2 = MonthlySearchPolicy.from_dict(json.loads(
        Path("validation/monthly_search_policy_v2.json").read_text()
    ))

    assert v1.objective_method == "legacy_time_loss_v1"
    assert v2.objective_method == "closure_cost_v1"
    assert v2.status == "provisional"
    assert v2.content_key != v1.content_key


def test_only_the_frozen_campaign_record_opens_the_release_gate(tmp_path):
    """The relabelling hole Sol found: a v1-v3 or diagnostic record carrying
    the CURRENT shortlist identity must not license the frozen campaign."""
    from traffic_sim.simulation.monthly_search import (
        frozen_campaign_identity,
        load_passing_heldout_gate,
    )

    identity = frozen_campaign_identity()
    assert identity is not None
    frozen, frozen_cert = _write_adopted_pair(tmp_path, name="frozen")
    accepted = load_passing_heldout_gate(frozen, frozen_cert)
    assert accepted is not None
    assert accepted["heldout_set"] == identity["campaign_version"]
    assert accepted["manifest_content_key"] == identity["manifest_content_key"]

    rejected = {
        "earlier_campaign_label": {"heldout_set": "v3"},
        "another_campaigns_manifest": {"manifest_content_key": "m" * 64},
        "diagnostic_replay": {"heldout_set": "v3-replay"},
        "incomplete_run": {"completed_cases": identity["required_cases"] - 1},
        "case_count_from_another_set": {
            "required_cases": identity["required_cases"] + 1},
        "missing_manifest_identity": {"manifest_content_key": None},
        "missing_case_counts": {"completed_cases": None},
    }
    for name, override in rejected.items():
        path, cert = _write_adopted_pair(tmp_path, name=name, **override)
        assert load_passing_heldout_gate(path, cert) is None, name


def test_gate_closes_when_the_certificate_names_an_unreadable_manifest(
        tmp_path, monkeypatch):
    """The certificate names the manifest; an absent one fails closed."""
    import json as _json
    import traffic_sim.simulation.monthly_search as monthly_search

    gp, cp = _write_adopted_pair(tmp_path)
    assert monthly_search.load_passing_heldout_gate(gp, cp) is not None

    cert = _json.loads(cp.read_text())
    cert["manifest_path"] = str(tmp_path / "absent-manifest.json")
    from traffic_sim.simulation.heldout_gate import canonical_content_key
    cert["content_key"] = canonical_content_key(cert)
    cp.write_text(_json.dumps(cert))
    assert monthly_search.load_passing_heldout_gate(gp, cp) is None


def test_a_tampered_frozen_manifest_closes_the_gate(tmp_path, monkeypatch):
    """Binding is only meaningful if the manifest is verified, not read."""
    import traffic_sim.simulation.monthly_search as monthly_search

    manifest = json.loads(V4_MANIFEST.read_text())
    manifest["cases"] = manifest["cases"][:-1]        # content key no longer recomputes
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(manifest))
    monkeypatch.setattr(monthly_search, "HELDOUT_CAMPAIGN_MANIFEST", tampered)
    assert monthly_search.frozen_campaign_identity() is None


def test_gate_record_creation_refuses_records_it_cannot_bind():
    """Creation side of the same rule: no campaign label, a report from
    another manifest, or an incomplete run must not become a gate record."""
    import run_monthly_proxy_validation as runner

    manifest = json.loads(V4_MANIFEST.read_text())
    report = {
        "gate_status": "pass",
        "manifest_content_key": manifest["content_key"],
        "ui_exposure_allowed": True,
        "global_best_claim_allowed": True,
        "case_reports": [{"case_id": "dropped-from-the-record"}],
    }
    complete = len(manifest["cases"])

    record = runner.gate_record_for(report, manifest, complete)
    assert record is not None
    assert record["heldout_set"] == manifest["campaign_version"]
    assert record["manifest_content_key"] == manifest["content_key"]
    assert record["required_cases"] == record["completed_cases"] == complete
    assert "case_reports" not in record

    unlabelled = {key: value for key, value in manifest.items()
                  if key != "campaign_version"}
    assert runner.gate_record_for(report, unlabelled, complete) is None
    other_manifest_report = {**report, "manifest_content_key": "m" * 64}
    assert runner.gate_record_for(other_manifest_report, manifest,
                                  complete) is None
    assert runner.gate_record_for(report, manifest, complete - 1) is None
    assert runner.gate_record_for({**report, "gate_status": "unbound"},
                                  manifest, complete) is None
