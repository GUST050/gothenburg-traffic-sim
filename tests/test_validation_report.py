"""G3 assembled validation report (IMPROVEMENT_PLAN.md; improvement plan 3.2)."""
import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import validation_report as vr


def _write_inputs(tmp_path, monkeypatch, *, geh=100.0, infeasible=0,
                  structure_flags=(), seed_flags=(), with_baseline=True,
                  with_loso=True, with_temporal=True, purpose_incompatible=None,
                  purpose_mix_relaxed=None,
                  baseline_build_id="buildid0123456789ab"):
    sumo = tmp_path / "sumo"
    sumo.mkdir()
    web = tmp_path / "web" / "data"
    (web / "scenarios").mkdir(parents=True)
    meta = {
        "date": "2025-09-16", "source": "historical",
        "build_id": "buildid0123456789ab",
        "epoch_sim": "2025-09-16T00:00:00",
        "build_options": {"through_share_target": 0.25},
        "pfe_fit": {"geh_pct": geh, "infeasible_intervals": infeasible,
                    "vehicles": 21600},
        "agent_demand": {"purpose_counts": {"arbete": 5410, "through": 9806}},
        "calibrated_structure": {
            "structure_flags": list(structure_flags),
            "dest_sensor_proximity": {"pct_within": 7.5,
                                      "baseline_pct_within": 1.9},
            "trip_length_fit": {"shares": [0.02, 0.73, 0.25],
                                "l1_distance": 0.4031},
            "onward_after_last_sensor": {"median_m": 2901.9,
                                         "pct_under_200m": 5.9},
            "purpose_length_km": {
                "arbete": {"n": 5410, "mean_km": 2.99, "median_km": 2.79},
                "fritid": {"n": 1597, "mean_km": 2.75, "median_km": 2.8}},
        },
    }
    if purpose_incompatible is not None or purpose_mix_relaxed is not None:
        meta["pfe_fit_variants"] = {
            "edge_shares": {}
        }
        if purpose_incompatible is not None:
            meta["pfe_fit_variants"]["edge_shares"][
                "purpose_incompatible_quarters"] = purpose_incompatible
        if purpose_mix_relaxed is not None:
            meta["pfe_fit_variants"]["edge_shares"][
                "purpose_mix_relaxed_quarters"] = purpose_mix_relaxed
    (sumo / "demand_meta.json").write_text(json.dumps(meta))
    candidate_bytes = b"<routes/>"
    network_bytes = b"<net/>"
    (sumo / "candidates.rou.xml").write_bytes(candidate_bytes)
    (sumo / "net.net.xml").write_bytes(network_bytes)
    if with_baseline:
        (web / "scenarios" / "baseline.json").write_text(json.dumps({
            "scenario": {"build_id": baseline_build_id},
            "flows": {"e": [1]},
            "seed_health": [{"seed": 1000, "loaded": 21600, "inserted": 21600,
                             "running_at_end": 0, "waiting_at_end": 0,
                             "teleports": 0}],
            "seed_health_flags": list(seed_flags),
        }))
    if with_loso:
        (web / "loso_report.json").write_text(json.dumps({
            "window": "2025-09-16",
            "stations": {"134": {"edges": {"e1": {"ratio": 0.78}}}}}))
    if with_temporal:
        (web / "temporal_holdout_report.json").write_text(json.dumps({
            "comparison_contract": {
                "protocol": "temporal_loso_pfe_meso_v1",
                "reference_window_start": "2025-09-16T00:00:00",
                "reference_window_end": "2025-09-17T00:00:00",
                "window_start": "2025-09-17T00:00:00",
                "window_end": "2025-09-18T00:00:00",
                "minimum_temporal_coverage": 0.9,
                "source": "historical",
                "candidate_pool_sha256": hashlib.sha256(
                    candidate_bytes).hexdigest(),
                "network_sha256": hashlib.sha256(network_bytes).hexdigest(),
                "through_share_target": 0.25,
            },
            "stations": {"134": {"edges": {
                "e1": {"ratio": 0.82, "observed_quarters": 96}
            }}},
        }))
    monkeypatch.setattr(vr, "SUMO_DIR", sumo)
    monkeypatch.setattr(vr, "WEB_DATA", web)
    monkeypatch.setattr(vr, "OUT_PATH", web / "validation.json")


class TestStudyIdentity:
    """Phase 6 acceptance gate: the panel must reflect the ACTIVE study's
    build. The report is one global artifact, so it has to STATE which
    build it validates — the web app refuses a pass shield when that id
    differs from the loaded scenario's."""

    def test_report_records_the_build_it_validates(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        assert vr.assemble()["demand_build_id"] == "buildid0123456789ab"

    def test_missing_build_id_is_null_not_invented(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        meta_path = vr.SUMO_DIR / "demand_meta.json"
        meta = json.loads(meta_path.read_text())
        del meta["build_id"]
        meta_path.write_text(json.dumps(meta))
        assert vr.assemble()["demand_build_id"] is None

    def test_mismatched_baseline_fails_closed(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, baseline_build_id="stale-build")

        report = vr.assemble()

        identity = report["sections"]["scenario_identity"]
        assert identity["status"] == "warn"
        assert identity["demand_build_id"] == "buildid0123456789ab"
        assert identity["baseline_demand_build_id"] == "stale-build"
        assert report["sections"]["simulation"]["status"] == "missing"
        assert report["sections"]["sensor_output"]["status"] == "missing"
        assert "inaktuell baseline" in report["sections"]["simulation"]["reason"]
        assert report["overall"] == "warn"

    def test_unidentified_baseline_fails_closed(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, baseline_build_id=None)

        report = vr.assemble()

        assert report["sections"]["scenario_identity"]["status"] == "warn"
        assert report["sections"]["simulation"]["status"] == "missing"
        assert report["overall"] == "warn"


class TestAssemble:
    def test_healthy_build_passes_overall(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        r = vr.assemble()
        assert r["overall"] == "pass"
        assert {s["status"] for n, s in r["sections"].items()
                if n not in {
                    "held_out", "temporal_holdout", "sensor_output"
                }} == {"pass"}
        assert r["sections"]["held_out"]["status"] == "info"
        assert r["sections"]["held_out"]["median_ratio"] == 0.78
        assert r["sections"]["temporal_holdout"]["status"] == "info"
        assert r["sections"]["temporal_holdout"]["median_ratio"] == 0.82
        assert r["sections"]["temporal_holdout"]["observed_quarters"] == {
            "134:e1": 96}

    def test_structure_flag_warns_overall(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch,
                      structure_flags=["trips_under_1km_pct: ..."])
        r = vr.assemble()
        assert r["sections"]["structure"]["status"] == "warn"
        assert r["overall"] == "warn"

    def test_geh_collapse_warns(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, geh=71.0)
        assert vr.assemble()["sections"]["counts_fit"]["status"] == "warn"

    def test_seed_flags_warn_simulation(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch,
                      seed_flags=["seed 1000: 900/21600 unfinished"])
        r = vr.assemble()
        assert r["sections"]["simulation"]["status"] == "warn"

    def test_purpose_ordering_flag_maps_to_purposes_section(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch,
                      structure_flags=["purpose_length_ordering: fritid ..."])
        r = vr.assemble()
        assert r["sections"]["purposes"]["ordering_violated"] is True
        assert r["sections"]["purposes"]["status"] == "warn"

    def test_purpose_incompatibility_blocks_purpose_claims(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, purpose_incompatible=4)
        section = vr.assemble()["sections"]["purposes"]
        assert section["status"] == "warn"
        assert section["purpose_claims_allowed"] is False
        assert section["purpose_incompatible_quarters_by_variant"] == {
            "edge_shares": 4
        }

    def test_relaxed_mix_warns_without_blocking_route_purpose_claims(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, purpose_mix_relaxed=3)
        section = vr.assemble()["sections"]["purposes"]
        assert section["status"] == "warn"
        assert section["purpose_claims_allowed"] is True
        assert section["purpose_mix_matches_generated_prior"] is False
        assert section["purpose_mix_relaxed_quarters_by_variant"] == {
            "edge_shares": 3
        }

    def test_missing_artifacts_are_stated_not_skipped(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, with_baseline=False,
                      with_loso=False, with_temporal=False)
        r = vr.assemble()
        assert r["sections"]["simulation"]["status"] == "missing"
        assert r["sections"]["held_out"]["status"] == "missing"
        assert r["sections"]["temporal_holdout"]["status"] == "missing"
        assert "saknas" in r["sections"]["held_out"]["reason"]
        # missing never blocks: the present sections still gate overall
        assert r["overall"] == "pass"

    def test_stale_temporal_holdout_is_not_presented_as_current_evidence(
            self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        report_path = vr.WEB_DATA / "temporal_holdout_report.json"
        report = json.loads(report_path.read_text())
        report["comparison_contract"]["candidate_pool_sha256"] = "stale"
        report_path.write_text(json.dumps(report))

        section = vr.assemble()["sections"]["temporal_holdout"]

        assert section["status"] == "missing"
        assert "inaktuellt" in section["reason"]

    def test_pre_e3_baseline_without_health_is_missing(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        base = vr.WEB_DATA / "scenarios" / "baseline.json"
        base.write_text(json.dumps({"flows": {"e": [1]}}))
        r = vr.assemble()
        assert r["sections"]["simulation"]["status"] == "missing"

    def test_write_report_is_atomic_and_valid_json(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        report = vr.write_report()
        on_disk = json.loads(vr.OUT_PATH.read_text())
        assert on_disk["overall"] == report["overall"] == "pass"
        assert on_disk["schema_version"] == vr.SCHEMA_VERSION
