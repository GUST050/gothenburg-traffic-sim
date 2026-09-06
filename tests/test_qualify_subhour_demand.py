"""Real (unmocked) coverage of the Phase D qualified-demand producer/validator.

Code review finding: `tools/qualify_subhour_demand.py` had no tests at all.
These exercise the real multi-step producer path -- a genuine strict-catalog
support audit against a real tiny SUMO net, and a genuine
`validate_calibrated_provenance` round trip -- rather than mocking the two
production boundaries the tool composes, matching the same standard already
applied to `TestMixedCatalogRealIntegration` in test_build_sumo_demand.py.
"""
import json
import xml.etree.ElementTree as ET

import pytest

import build_sumo_demand as bsd
import tools.qualify_subhour_demand as qsd
from tests.test_build_sumo_demand import (
    _write_mixed_pool_source,
    _write_mixed_test_net,
)
from traffic_sim.demand.sensor_route_contract import load_network_contract


def _catalog_sources(tmp_path, edges, net_path, network_sha256):
    sources = {}
    for pool, prefix in (("weekday", "wd"), ("weekend", "we")):
        rou = tmp_path / f"{pool}.rou.xml"
        meta = tmp_path / f"{pool}.meta.json"
        _write_mixed_pool_source(
            rou, meta, edges, net_path, network_sha256,
            ordinary_id=f"{prefix}_ordinary", basis_id=f"{prefix}_basis")
        sources[pool] = (rou, meta)
    return sources


def _variant_archive(sumo_dir, suffix, *, vehicle_id, targets_ok=True,
                      relaxation_ok=True, sensor_edge="B"):
    """One q10/q50/q90 candidate+calibrated+agents fixture, mirroring
    tests/test_demand_provenance.py's minimal `_artifacts` shape."""
    candidate_routes = sumo_dir / "candidates.rou.xml"
    candidate_metadata = sumo_dir / "candidates.meta.json"
    if not candidate_routes.is_file():
        candidate_routes.write_text(
            '<routes><vehicle id="c0" depart="7.5">'
            f'<route edges="A {sensor_edge} C"/></vehicle></routes>')
        candidate_metadata.write_text(json.dumps({
            "schema_version": 1,
            "candidates": {
                "c0": {"purpose": "arbete",
                       "origin_edge": "A", "destination_edge": "C"},
            },
        }))
    calibrated_routes = sumo_dir / f"calibrated{suffix}.rou.xml"
    calibrated_agents = sumo_dir / f"calibrated{suffix}.agents.json"
    calibrated_routes.write_text(
        f'<routes><vehicle id="{vehicle_id}" depart="12.5">'
        f'<route edges="A {sensor_edge} C"/></vehicle></routes>')
    calibrated_agents.write_text(json.dumps({
        "schema_version": 1,
        "agents": [{
            "vehicle_id": vehicle_id,
            "candidate_id": "c0",
            "purpose": "arbete",
            "origin_edge": "A",
            "destination_edge": "C",
            "purpose_route_compatible": True,
            "departure_s": 12.5,
        }],
    }))
    return {
        "targets": {sensor_edge: [1]} if targets_ok else {},
        "relaxation_summary": (
            {"rung": "clean"} if relaxation_ok else "not-a-dict"),
    }


def _build_sumo_dir(tmp_path, sensor_edge="B"):
    """A real (non-catalog) three-variant demand build dir."""
    sumo_dir = tmp_path / "sumo_build"
    sumo_dir.mkdir()
    entries = {
        "": ("q50", "edge_shares"),
        "_v1": ("q10", "edge_shares_q10"),
        "_v2": ("q90", "edge_shares_q90"),
    }
    sensor_targets = {"variants": {}}
    pfe_fit_variants = {}
    for suffix, (label, target_key) in entries.items():
        record = _variant_archive(
            sumo_dir, suffix, vehicle_id=f"pfe_{label}", sensor_edge=sensor_edge)
        sensor_targets["variants"][target_key] = record["targets"]
        pfe_fit_variants[target_key] = {
            "relaxation_summary": record["relaxation_summary"]}
    demand_meta = {
        "demand_variant_contract": {
            "schema_version": 1,
            "mode": "direction_stress",
            "variants": [
                {"name": "q50", "target_key": "edge_shares",
                 "route_file": "calibrated.rou.xml"},
                {"name": "q10", "target_key": "edge_shares_q10",
                 "route_file": "calibrated_v1.rou.xml"},
                {"name": "q90", "target_key": "edge_shares_q90",
                 "route_file": "calibrated_v2.rou.xml"},
            ],
        },
        "sensor_targets": sensor_targets,
        "pfe_fit_variants": pfe_fit_variants,
    }
    (sumo_dir / "demand_meta.json").write_text(json.dumps(demand_meta))
    return sumo_dir


def _approval(source_digest="a" * 64):
    return {
        "status": "CODE_APPROVED", "source_digest": source_digest,
        "source_manifest_sha256": "b" * 64, "checks_sha256": "c" * 64,
        "checks_status": "PASS", "impact_inventory_sha256": "d" * 64,
        "phase_prerequisites": {
            "phase_0": qsd.PHASE_PREREQUISITE_CONTRACT["phase_0"],
            "phase_1": qsd.PHASE_PREREQUISITE_CONTRACT["phase_1"],
            "phase_2": {"status": "PASS", "required_tests": ["deterministic"]},
        },
    }


def _archive_inputs(sumo_dir):
    key = "build-key"
    record = {
        "build_key": key, "archive": str(sumo_dir.resolve()),
        "demand_build_spec": {"source": "fixture"},
        "archive_manifest_sha256": "e" * 64,
        "archive_content_key": "f" * 64,
    }
    return {key: (sumo_dir, record)}


def _bindings():
    adoption = {"path": "/adoption.json", "sha256": "1" * 64,
                "catalog_keys": {"weekday": "wd-key", "weekend": "we-key"}}
    catalogs = {
        pool: {"catalog_key": key, "manifest_sha256": "2" * 64,
               "routes_sha256": "3" * 64, "metadata_sha256": "4" * 64}
        for pool, key in adoption["catalog_keys"].items()
    }
    return adoption, catalogs


class TestAuditSharedSupport:
    def test_requires_exactly_weekday_and_weekend(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        _write_mixed_test_net(net_path)
        with pytest.raises(qsd.QualificationError, match="weekday and weekend"):
            qsd.audit_shared_support(
                net_path, {"1_2_0"}, min_per_sensor=1,
                catalogs={"weekday": (net_path, net_path)})

    def test_requires_at_least_one_measured_sensor(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        _write_mixed_test_net(net_path)
        with pytest.raises(qsd.QualificationError, match="no measured sensors"):
            qsd.audit_shared_support(
                net_path, [], min_per_sensor=1,
                catalogs={"weekday": (net_path, net_path),
                          "weekend": (net_path, net_path)})

    def test_real_pools_pass_a_real_shared_support_audit(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        edges = _write_mixed_test_net(net_path)
        _, _, network_sha256 = load_network_contract(net_path)
        sources = _catalog_sources(tmp_path, edges, net_path, network_sha256)
        out_rou = {}
        out_meta = {}
        for pool, (rou, meta) in sources.items():
            out_rou[pool] = tmp_path / f"catalog_{pool}.rou.xml"
            out_meta[pool] = tmp_path / f"catalog_{pool}.meta.json"
            bsd.prepare_mixed_catalog_candidates(
                {pool: (rou, meta)}, out_rou[pool], out_meta[pool],
                {edges["target"]}, min_per_sensor=1, net_path=net_path,
                cache_root=tmp_path / f"cache_{pool}")

        report = qsd.audit_shared_support(
            net_path, {edges["target"]}, min_per_sensor=1,
            catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                      "weekend": (out_rou["weekend"], out_meta["weekend"])})

        assert set(report) == {"weekday", "weekend"}
        assert report["weekday"]["status"] == "pass"
        assert report["weekend"]["status"] == "pass"
        assert report["weekday"]["vehicles"] >= 1

    def test_an_under_floor_pool_is_recorded_as_a_failure_not_raised(
            self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        edges = _write_mixed_test_net(net_path)
        _, _, network_sha256 = load_network_contract(net_path)
        sources = _catalog_sources(tmp_path, edges, net_path, network_sha256)
        out_rou = {}
        out_meta = {}
        for pool, (rou, meta) in sources.items():
            out_rou[pool] = tmp_path / f"catalog_{pool}.rou.xml"
            out_meta[pool] = tmp_path / f"catalog_{pool}.meta.json"
            bsd.prepare_mixed_catalog_candidates(
                {pool: (rou, meta)}, out_rou[pool], out_meta[pool],
                {edges["target"]}, min_per_sensor=1, net_path=net_path,
                cache_root=tmp_path / f"cache_{pool}")

        # A floor the two pooled ordinary candidates cannot possibly satisfy.
        report = qsd.audit_shared_support(
            net_path, {edges["target"]}, min_per_sensor=50,
            catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                      "weekend": (out_rou["weekend"], out_meta["weekend"])})

        assert report["weekday"]["status"] == "fail"
        assert "reason" in report["weekday"]


class TestResolveVariantContract:
    def test_rejects_a_non_direction_stress_build(self):
        with pytest.raises(qsd.QualificationError, match="direction-stress"):
            qsd.resolve_variant_contract({"demand_variant_contract": {
                "mode": "q50_only"}})

    def test_rejects_missing_variant(self):
        with pytest.raises(qsd.QualificationError, match="all three"):
            qsd.resolve_variant_contract({"demand_variant_contract": {
                "mode": "direction_stress",
                "variants": [{"name": "q50", "target_key": "edge_shares",
                               "route_file": "calibrated.rou.xml"}],
            }})

    def test_resolves_a_real_contract(self, tmp_path):
        sumo_dir = _build_sumo_dir(tmp_path)
        demand_meta = json.loads((sumo_dir / "demand_meta.json").read_text())
        resolved = qsd.resolve_variant_contract(demand_meta)
        assert set(resolved) == {"q10", "q50", "q90"}
        assert resolved["q50"]["route_file"] == "calibrated.rou.xml"


class TestValidateVariantArchive:
    def test_real_round_trip(self, tmp_path):
        sumo_dir = _build_sumo_dir(tmp_path)
        demand_meta = json.loads((sumo_dir / "demand_meta.json").read_text())

        result = qsd.validate_variant_archive(
            "q50", sumo_dir=sumo_dir,
            candidate_routes=sumo_dir / "candidates.rou.xml",
            candidate_metadata=sumo_dir / "candidates.meta.json",
            demand_meta=demand_meta, target_key="edge_shares",
            route_file="calibrated.rou.xml", measured_edges=["B"])

        assert result["vehicles"] == 1
        assert result["provenance_status"] == "pass"
        assert set(result["content_digests"]) == {
            "candidate_routes", "candidate_metadata",
            "calibrated_routes", "calibrated_agents"}

    def test_missing_archive_file_fails_closed(self, tmp_path):
        sumo_dir = _build_sumo_dir(tmp_path)
        demand_meta = json.loads((sumo_dir / "demand_meta.json").read_text())
        (sumo_dir / "calibrated.rou.xml").unlink()

        with pytest.raises(qsd.QualificationError, match="missing"):
            qsd.validate_variant_archive(
                "q50", sumo_dir=sumo_dir,
                candidate_routes=sumo_dir / "candidates.rou.xml",
                candidate_metadata=sumo_dir / "candidates.meta.json",
                demand_meta=demand_meta, target_key="edge_shares",
                route_file="calibrated.rou.xml", measured_edges=["B"])

    def test_missing_targets_fails_closed(self, tmp_path):
        sumo_dir = _build_sumo_dir(tmp_path)
        demand_meta = json.loads((sumo_dir / "demand_meta.json").read_text())
        demand_meta["sensor_targets"]["variants"]["edge_shares"] = {}

        with pytest.raises(qsd.QualificationError, match="exact published targets"):
            qsd.validate_variant_archive(
                "q50", sumo_dir=sumo_dir,
                candidate_routes=sumo_dir / "candidates.rou.xml",
                candidate_metadata=sumo_dir / "candidates.meta.json",
                demand_meta=demand_meta, target_key="edge_shares",
                route_file="calibrated.rou.xml", measured_edges=["B"])

    def test_a_tampered_route_breaks_real_provenance(self, tmp_path):
        """Not a mocked rejection: the underlying real
        `validate_calibrated_provenance` call is what actually catches this."""
        sumo_dir = _build_sumo_dir(tmp_path)
        demand_meta = json.loads((sumo_dir / "demand_meta.json").read_text())
        tree = ET.parse(sumo_dir / "calibrated.rou.xml")
        for vehicle in tree.getroot().findall("vehicle"):
            vehicle.find("route").set("edges", "A B C D")
        tree.write(sumo_dir / "calibrated.rou.xml")

        with pytest.raises(ValueError, match="differs from"):
            qsd.validate_variant_archive(
                "q50", sumo_dir=sumo_dir,
                candidate_routes=sumo_dir / "candidates.rou.xml",
                candidate_metadata=sumo_dir / "candidates.meta.json",
                demand_meta=demand_meta, target_key="edge_shares",
                route_file="calibrated.rou.xml", measured_edges=["B"])


class TestBuildManifestAndWrite:
    def _catalogs(self, tmp_path):
        net_path = tmp_path / "net.net.xml"
        edges = _write_mixed_test_net(net_path)
        _, _, network_sha256 = load_network_contract(net_path)
        sources = _catalog_sources(tmp_path, edges, net_path, network_sha256)
        out_rou = {}
        out_meta = {}
        for pool, (rou, meta) in sources.items():
            out_rou[pool] = tmp_path / f"catalog_{pool}.rou.xml"
            out_meta[pool] = tmp_path / f"catalog_{pool}.meta.json"
            bsd.prepare_mixed_catalog_candidates(
                {pool: (rou, meta)}, out_rou[pool], out_meta[pool],
                {edges["target"]}, min_per_sensor=1, net_path=net_path,
                cache_root=tmp_path / f"cache_{pool}")
        return net_path, edges, out_rou, out_meta

    def test_end_to_end_manifest_passes_and_is_self_digested(self, tmp_path):
        net_path, edges, out_rou, out_meta = self._catalogs(tmp_path)
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])
        adoption, bindings = _bindings()

        manifest = qsd.build_manifest(
            evidence_id="phase-d-test", code_approval=_approval(), net_path=net_path,
            measured_edges=[edges["target"]], min_per_sensor=1,
            adoption=adoption, catalog_bindings=bindings,
            catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                      "weekend": (out_rou["weekend"], out_meta["weekend"])},
            archives=_archive_inputs(sumo_dir))

        assert manifest["status"] == "PASS"
        assert manifest["support_audit_pass"] is True
        assert set(manifest["archives"]["build-key"]["variants"]) == {
            "q10", "q50", "q90"}
        assert manifest["schema"] == qsd.MANIFEST_SCHEMA
        # content_key excludes itself and is reproducible from the rest.
        body = {k: v for k, v in manifest.items() if k != "content_key"}
        assert manifest["content_key"] == qsd._digest_payload(body)

    def test_under_floor_support_yields_inconclusive_not_a_raise(
            self, tmp_path):
        net_path, edges, out_rou, out_meta = self._catalogs(tmp_path)
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])
        adoption, bindings = _bindings()

        manifest = qsd.build_manifest(
            evidence_id="phase-d-test", code_approval=_approval(), net_path=net_path,
            measured_edges=[edges["target"]], min_per_sensor=50,
            adoption=adoption, catalog_bindings=bindings,
            catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                      "weekend": (out_rou["weekend"], out_meta["weekend"])},
            archives=_archive_inputs(sumo_dir))

        assert manifest["status"] == "INCONCLUSIVE_SENSOR_SHORTEST_SUPPORT"
        assert manifest["support_audit_pass"] is False

    def test_rejects_wrong_catalog_key_set(self, tmp_path):
        net_path, edges, out_rou, out_meta = self._catalogs(tmp_path)
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])

        adoption, bindings = _bindings()
        adoption["catalog_keys"] = {"weekday": "wd-key"}
        with pytest.raises(qsd.QualificationError, match="weekday and weekend"):
            qsd.build_manifest(
                evidence_id="phase-d-test", code_approval=_approval(), net_path=net_path,
                measured_edges=[edges["target"]], min_per_sensor=1,
                adoption=adoption, catalog_bindings=bindings,
                catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                          "weekend": (out_rou["weekend"], out_meta["weekend"])},
                archives=_archive_inputs(sumo_dir))

    def test_rejects_missing_source_digest(self, tmp_path):
        net_path, edges, out_rou, out_meta = self._catalogs(tmp_path)
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])

        adoption, bindings = _bindings()
        with pytest.raises(qsd.QualificationError, match="source digest"):
            qsd.build_manifest(
                evidence_id="phase-d-test", code_approval=_approval(""), net_path=net_path,
                measured_edges=[edges["target"]], min_per_sensor=1,
                adoption=adoption, catalog_bindings=bindings,
                catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                          "weekend": (out_rou["weekend"], out_meta["weekend"])},
                archives=_archive_inputs(sumo_dir))

    def test_write_manifest_is_no_clobber(self, tmp_path):
        net_path, edges, out_rou, out_meta = self._catalogs(tmp_path)
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])
        adoption, bindings = _bindings()
        manifest = qsd.build_manifest(
            evidence_id="phase-d-test", code_approval=_approval(), net_path=net_path,
            measured_edges=[edges["target"]], min_per_sensor=1,
            adoption=adoption, catalog_bindings=bindings,
            catalogs={"weekday": (out_rou["weekday"], out_meta["weekday"]),
                      "weekend": (out_rou["weekend"], out_meta["weekend"])},
            archives=_archive_inputs(sumo_dir))
        output = tmp_path / "manifest.json"
        qsd.write_manifest(output, manifest)
        original_bytes = output.read_bytes()

        other = dict(manifest)
        other["source_digest"] = "t" * 64
        with pytest.raises(FileExistsError):
            qsd.write_manifest(output, other)
        assert output.read_bytes() == original_bytes


class TestMainCli:
    def test_main_writes_a_passing_manifest_and_returns_zero(
            self, tmp_path, capsys, monkeypatch):
        net_path = tmp_path / "net.net.xml"
        edges = _write_mixed_test_net(net_path)
        _, _, network_sha256 = load_network_contract(net_path)
        sources = _catalog_sources(tmp_path, edges, net_path, network_sha256)
        out_rou = {}
        out_meta = {}
        for pool, (rou, meta) in sources.items():
            out_rou[pool] = tmp_path / f"catalog_{pool}.rou.xml"
            out_meta[pool] = tmp_path / f"catalog_{pool}.meta.json"
            bsd.prepare_mixed_catalog_candidates(
                {pool: (rou, meta)}, out_rou[pool], out_meta[pool],
                {edges["target"]}, min_per_sensor=1, net_path=net_path,
                cache_root=tmp_path / f"cache_{pool}")
        sumo_dir = _build_sumo_dir(tmp_path, edges["target"])
        output = tmp_path / "manifest.json"
        source = tmp_path / "source.json"
        checks = tmp_path / "checks.json"
        impact = tmp_path / "impact.md"
        search = tmp_path / "search.json"
        for path in (source, checks, impact, search):
            path.write_text("{}")
        adoption, bindings = _bindings()
        monkeypatch.setattr(qsd, "validate_code_approval", lambda *_: _approval())
        monkeypatch.setattr(
            qsd, "validate_adoption_and_catalogs", lambda *_: (adoption, bindings))
        monkeypatch.setattr(qsd, "derive_required_demand_specs", lambda *_: {"build-key": object()})
        monkeypatch.setattr(
            qsd, "build_fresh_archives", lambda *_: _archive_inputs(sumo_dir))

        code = qsd.main([
            "--net-path", str(net_path),
            "--sensor", edges["target"],
            "--min-per-sensor", "1",
            "--evidence-id", "phase-d-cli",
            "--source-manifest", str(source),
            "--checks", str(checks),
            "--impact-inventory", str(impact),
            "--weekday-routes", str(out_rou["weekday"]),
            "--weekday-metadata", str(out_meta["weekday"]),
            "--weekend-routes", str(out_rou["weekend"]),
            "--weekend-metadata", str(out_meta["weekend"]),
            "--search-spec", str(search),
            "--fresh-runs-root", str(tmp_path / "fresh-runs"),
            "--output", str(output),
        ])

        assert code == 0
        assert output.is_file()
        manifest = json.loads(output.read_text())
        assert manifest["status"] == "PASS"
        printed = json.loads(capsys.readouterr().out)
        assert printed["status"] == "PASS"
        assert printed["content_key"] == manifest["content_key"]
