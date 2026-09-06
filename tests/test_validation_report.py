"""G3 assembled validation report (IMPROVEMENT_PLAN.md; improvement plan 3.2)."""
import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import validation_report as vr
from traffic_sim.confidence import trip_length_gate as gate


def test_web_panel_exposes_the_exact_sumo_passage_test():
    source = (Path(__file__).parent.parent / "web" / "app.js").read_text()
    assert "sensor_output_exact: 'Exakt SUMO-passagetest'" in source
    assert "sensor×kvartar" in source
    assert "accepterade" in source


def test_web_hides_internal_seed_and_describes_demand_truthfully():
    root = Path(__file__).parent.parent
    source = (root / "web" / "app.js").read_text()
    html = (root / "web" / "index.html").read_text()

    assert "representativ körning (frö" not in source
    assert "visad körning ${variant}" not in source
    assert "AUDIT_VARIANT_LABEL" not in source
    # The 🚗 vehicle-animation toggle was deliberately removed: vehicles ARE
    # the Simulering view, and offering a switch implied the conveyor-dot
    # illustration was an equally valid picture of the same run. Pinned as an
    # absence so it cannot quietly return; the mode switch drives
    # Render.setVehicleMode instead.
    assert "🚗 Fordonsanimation" not in source
    assert "🚗 Fordonsanimation" not in html
    assert "Render.setVehicleMode(true)" in source
    assert "modellerat reseärende:" in source
    assert "geografiskt flöde:" in source
    assert "Sensorindatan anger antal passager" in source
    assert "inte reseärende" in source
    assert "Modellerade reseärenden & längder" in source
    assert "Mål visad" not in html
    assert "SUMO visad" not in html
    assert "Mål, enskild" in html
    assert "SUMO, enskild" in html


def test_the_map_separates_unsimulated_roads_from_empty_ones():
    """Two different zeroes must not share one appearance.

    Under the baseline rule only sensor-crossing paths carry traffic, so most
    inner-city streets have no simulated flow at any quarter — 5 643 of 7 147
    edges on the baseline measured 2026-09-06. They were drawn with the same
    solid grey as a street the simulation DOES cover that happens to be empty
    at this quarter, which invites reading a modelling boundary as a quiet
    road. pytest cannot execute the renderer, so the invariant is pinned at
    source level, the way this file already pins other UI copy.
    """
    root = Path(__file__).parent.parent
    render = (root / "web" / "render.js").read_text()
    html = (root / "web" / "index.html").read_text()

    assert "UNSIMULATED_STYLE" in render
    # The renderer must ASK the provider, not compute the answer from
    # maxFlow(): a DeltaProvider delegates maxFlow to its closure arm, so a
    # street the closure empties reports 0 and the largest reduction the map
    # can show would be hidden. tests/js/provider_coverage.test.js executes
    # that case; this only pins that the renderer still delegates.
    assert "_provider.carriesNoTraffic(edgeId)" in render
    assert "maxFlow" not in render.split("function carriesNoTraffic")[1][:600]
    # Scoped to scenario views: in Historisk/Prognos a zero is a measurement.
    assert "_provider.isScenario || _provider.isDelta" in render
    # Claim only what is observed: no route-coverage metadata exists per edge,
    # so the copy may not assert WHY the street is empty.
    assert "Ingen trafik i detta scenario" in render
    assert "sensorkorsande" not in render

    # The comparison must decide BEFORE the plain-flow shortcuts, or a closure
    # that empties a street completely (count === 0) is drawn neutral grey —
    # the largest reduction the map can show, rendered as no change.
    assert render.index("applyDeltaStyle(e, edgeId, qi)") \
        < render.index("if (count === null)")


def test_the_render_comment_states_the_deployed_sigma():
    """The comment named 127.5 m, which matches no deployment; the shipped
    network.geojson is 119.5 m. A wrong number in a comment about how the
    confidence gradient is drawn is how the next reader mis-describes it."""
    render = (Path(__file__).parent.parent / "web" / "render.js").read_text()

    assert "127.5 m" not in render
    assert "119.5 m" in render


def test_ui_demand_copy_matches_the_sensor_input_schema():
    root = Path(__file__).parent.parent
    input_readme = (root / "data_in" / "README.md").read_text()
    source = (root / "web" / "app.js").read_text()

    for field in ("Mätplats", "Datum", "Kvart", "Antal passager"):
        assert field in input_readme
    assert "antal passager per mätplats, datum" in source
    assert "15-minutersintervall" in source


def test_recalibration_fetch_error_names_the_live_localhost_address():
    source = (Path(__file__).parent.parent / "web" / "app.js").read_text()

    assert "failed to fetch|load failed|networkerror" in source
    assert "ingen kontakt med API-servern" in source
    assert "Öppna http://localhost:8000/ och ladda om sidan" in source


def _write_inputs(tmp_path, monkeypatch, *, geh=100.0, infeasible=0,
                  integer_constraints=672, integer_exact=672,
                  integer_max_abs_error=0.0,
                  structure_flags=(), seed_flags=(), with_baseline=True,
                  with_loso=True, with_temporal=True, purpose_incompatible=None,
                  purpose_mix_relaxed=None):
    sumo = tmp_path / "sumo"
    sumo.mkdir()
    web = tmp_path / "web" / "data"
    (web / "scenarios").mkdir(parents=True)
    meta = {
        "date": "2025-09-16", "source": "historical",
        "build_id": "buildid0123456789ab",
        "epoch_sim": "2025-09-16T00:00:00",
        "build_options": {"through_share_target": 0.25},
        "pfe_fit": {
            "geh_pct": geh,
            "infeasible_intervals": infeasible,
            "vehicles": 21600,
            "integer_sensor_constraints": integer_constraints,
            "integer_sensor_exact": integer_exact,
            "integer_sensor_exact_pct": round(
                100 * integer_exact / max(1, integer_constraints), 6),
            "integer_sensor_max_abs_error": integer_max_abs_error,
            "integer_sensor_sum_abs_error": integer_max_abs_error,
            "integer_sensor_target_rule": "int(round(target))",
        },
        "agent_demand": {"purpose_counts": {"arbete": 5410, "through": 9806}},
        "calibrated_structure": {
            "structure_flags": list(structure_flags),
            "dest_sensor_proximity": {"pct_within": 7.5,
                                      "baseline_pct_within": 1.9},
            # Production never writes maximum_l1_distance; the frozen project
            # limit in traffic_sim.confidence.trip_length_gate owns it.
            "trip_length_fit": {"shares": [0.02, 0.73, 0.25],
                                "l1_distance": 0.1204},
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


class TestAssemble:
    def test_healthy_build_passes_overall(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        r = vr.assemble()
        assert r["overall"] == "pass"
        assert {s["status"] for n, s in r["sections"].items()
                if n not in {
                    "held_out", "temporal_holdout", "sensor_output",
                    "sensor_output_exact",
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

    def _structure_with_l1(self, tmp_path, monkeypatch, **fit):
        _write_inputs(tmp_path, monkeypatch)
        meta_path = vr.SUMO_DIR / "demand_meta.json"
        meta = json.loads(meta_path.read_text())
        meta["calibrated_structure"]["trip_length_fit"].update(fit)
        meta_path.write_text(json.dumps(meta))
        return vr.assemble()["sections"]["structure"]

    def test_trip_length_l1_uses_the_frozen_limit_when_none_is_declared(
            self, tmp_path, monkeypatch):
        """A build that declares nothing is still judged.

        Before 2026-08-26 no production code wrote maximum_l1_distance at
        all, so this gate was permanently undefined and every build reported
        overall "warn" whatever its data — burying the real structure flags
        published beside it.
        """
        section = self._structure_with_l1(tmp_path, monkeypatch)

        assert section["trip_length_l1_gate_defined"] is True
        assert section["trip_length_l1_maximum"] == gate.MAXIMUM_TRIP_LENGTH_L1
        assert section["trip_length_l1_gate_passed"] is True
        assert section["trip_length_l1_gate_source"] == "frozen_project_limit_v1"

    def test_trip_length_l1_over_the_frozen_limit_warns(
            self, tmp_path, monkeypatch):
        section = self._structure_with_l1(
            tmp_path, monkeypatch,
            l1_distance=gate.MAXIMUM_TRIP_LENGTH_L1 + 0.05)

        assert section["status"] == "warn"
        assert section["trip_length_l1_gate_passed"] is False
        assert "överstiger den frysta gränsen" in section["reason"]

    def test_a_build_cannot_declare_a_looser_limit_than_the_project(
            self, tmp_path, monkeypatch):
        """The limit that judges an artifact must not travel inside it."""
        section = self._structure_with_l1(
            tmp_path, monkeypatch,
            l1_distance=0.45, maximum_l1_distance=0.9)

        assert section["trip_length_l1_maximum"] == gate.MAXIMUM_TRIP_LENGTH_L1
        assert section["trip_length_l1_gate_passed"] is False

    def test_a_build_may_hold_itself_to_a_stricter_limit(
            self, tmp_path, monkeypatch):
        section = self._structure_with_l1(
            tmp_path, monkeypatch,
            l1_distance=0.1204, maximum_l1_distance=0.05)

        assert section["trip_length_l1_maximum"] == 0.05
        assert section["trip_length_l1_gate_passed"] is False

    def test_a_perfect_zero_l1_passes_rather_than_reading_as_missing(
            self, tmp_path, monkeypatch):
        """0.0 is the best possible fit, not an absent measurement."""
        section = self._structure_with_l1(
            tmp_path, monkeypatch, l1_distance=0.0)

        assert section["trip_length_l1_gate_passed"] is True
        assert "saknas" not in section.get("reason", "")

    def test_a_missing_l1_is_reported_rather_than_silently_passed(
            self, tmp_path, monkeypatch):
        section = self._structure_with_l1(
            tmp_path, monkeypatch, l1_distance=None)

        assert section["status"] == "warn"
        assert section["trip_length_l1_gate_passed"] is False
        assert "saknas" in section["reason"]

    def test_geh_collapse_warns(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch, geh=71.0)
        assert vr.assemble()["sections"]["counts_fit"]["status"] == "warn"

    def test_one_non_exact_sensor_quarter_warns(self, tmp_path, monkeypatch):
        _write_inputs(
            tmp_path, monkeypatch, integer_exact=671,
            integer_max_abs_error=1.0,
        )
        section = vr.assemble()["sections"]["counts_fit"]
        assert section["status"] == "warn"
        assert section["integer_sensor_exact"] == 671
        assert section["integer_sensor_constraints"] == 672

    def test_missing_exact_sensor_evidence_warns(self, tmp_path, monkeypatch):
        _write_inputs(tmp_path, monkeypatch)
        meta_path = vr.SUMO_DIR / "demand_meta.json"
        meta = json.loads(meta_path.read_text())
        for field in tuple(meta["pfe_fit"]):
            if field.startswith("integer_sensor_"):
                del meta["pfe_fit"][field]
        meta_path.write_text(json.dumps(meta))

        assert vr.assemble()["sections"]["counts_fit"]["status"] == "warn"

    @staticmethod
    def _exact_baseline(raw):
        from traffic_sim.simulation.sensor_fit import build_exact_output_fit

        row = {
            "sensor_id": "s", "edge_id": "e",
            "target_mean": [10.0, 11.0],
            "simulated_mean_raw": list(raw),
            "target_representative": [10.0, 11.0],
            "simulated_representative_raw": list(raw),
            "seed_runs": [{
                "seed": 1000, "variant": "edge_shares",
                "target": [10.0, 11.0], "simulated_raw": list(raw),
            }],
        }
        audit = {"directions": [row]}
        audit["exact_output_fit"] = build_exact_output_fit(
            [row], n_intervals=2, uses_raw_ensemble_mean=True)
        return {"n_quarters": 2, "sensor_audit": audit}

    def test_exact_sumo_passage_section_passes_only_zero_residual(self):
        passed = vr._exact_sensor_output_section(
            self._exact_baseline([10.0, 11.0]))
        warned = vr._exact_sensor_output_section(
            self._exact_baseline([10.0, 12.0]))

        assert passed["status"] == "pass"
        assert passed["exact"] == passed["constraints"] == 2
        assert warned["status"] == "warn"
        assert warned["mismatch_count"] == 1

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
