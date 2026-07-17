"""G3 assembled validation report (IMPROVEMENT_PLAN.md; improvement plan 3.2)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import validation_report as vr


def _write_inputs(tmp_path, monkeypatch, *, geh=100.0, infeasible=0,
                  structure_flags=(), seed_flags=(), with_baseline=True,
                  with_loso=True, purpose_incompatible=None,
                  purpose_mix_relaxed=None):
    sumo = tmp_path / "sumo"
    sumo.mkdir()
    web = tmp_path / "web" / "data"
    (web / "scenarios").mkdir(parents=True)
    meta = {
        "date": "2025-09-16", "source": "historical",
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
    if with_baseline:
        (web / "scenarios" / "baseline.json").write_text(json.dumps({
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
    monkeypatch.setattr(vr, "SUMO_DIR", sumo)
    monkeypatch.setattr(vr, "WEB_DATA", web)
    monkeypatch.setattr(vr, "OUT_PATH", web / "validation.json")


class TestAssemble:
    def test_healthy_build_passes_overall(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        r = vr.assemble()
        assert r["overall"] == "pass"
        assert {s["status"] for n, s in r["sections"].items()
                if n not in {"held_out", "sensor_output"}} == {"pass"}
        assert r["sections"]["held_out"]["status"] == "info"
        assert r["sections"]["held_out"]["median_ratio"] == 0.78

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
                      with_loso=False)
        r = vr.assemble()
        assert r["sections"]["simulation"]["status"] == "missing"
        assert r["sections"]["held_out"]["status"] == "missing"
        assert "saknas" in r["sections"]["held_out"]["reason"]
        # missing never blocks: the present sections still gate overall
        assert r["overall"] == "pass"

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
