"""Q50 monthly builds must carry a distinct identity and no stress work."""
from dataclasses import replace
import json

import pytest

from traffic_sim.core.contracts import DemandBuildSpec
from tools import qualify_subhour_demand as qsd


def test_variant_mode_separates_build_identity_and_roundtrips():
    legacy = DemandBuildSpec("2027-12-01", purpose="closure_envelope")
    central = replace(legacy, variant_mode="q50_only")
    stress = replace(legacy, variant_mode="direction_stress")
    assert len({legacy.build_key, central.build_key, stress.build_key}) == 3
    assert "variant_mode" not in legacy.to_dict()
    assert DemandBuildSpec.from_dict(central.to_dict()) == central


def test_qualifier_resolves_q50_without_fabricating_stress():
    metadata = {"demand_variant_contract": {
        "mode": "q50_only", "variants": [{"name": "q50",
        "target_key": "edge_shares", "route_file": "calibrated.rou.xml"}]}}
    assert qsd.resolve_variant_contract(metadata) == {
        "q50": {"target_key": "edge_shares", "route_file": "calibrated.rou.xml"}}


def test_qualifier_rejects_extra_stress_under_q50_identity():
    metadata = {"demand_variant_contract": {
        "mode": "q50_only", "variants": [{"name": name,
        "target_key": "edge_shares", "route_file": "calibrated.rou.xml"}
        for name in ("q50", "q10")]}}
    with pytest.raises(qsd.QualificationError):
        qsd.resolve_variant_contract(metadata)


def test_builder_refuses_stress_flag_for_q50_spec(tmp_path, monkeypatch):
    import build_sumo_demand as builder
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(DemandBuildSpec(
        "2027-12-01", purpose="closure_envelope", variant_mode="q50_only"
    ).to_dict()))
    monkeypatch.setattr("sys.argv", ["build_sumo_demand.py", "--demand-spec",
                                   str(path), "--direction-stress-variants"])
    with pytest.raises(SystemExit):
        builder.parse_args()


def test_q50_archive_validates_with_no_stress_files(tmp_path):
    from tests.test_monthly_demand import _archive
    from traffic_sim.simulation.monthly_demand import validate_demand_archive
    spec = DemandBuildSpec("2027-12-01", purpose="closure_envelope",
                           variant_mode="q50_only")
    archive = _archive(tmp_path, spec, "central", finished_at="2027-01-01T00:00:00Z")
    for suffix in ("_v1", "_v2"):
        for extension in ("rou.xml", "agents.json"):
            (archive / f"calibrated{suffix}.{extension}").unlink()
    record = validate_demand_archive(archive, spec)
    assert {item["name"] for item in record["outputs"]} >= {"calibrated.rou.xml"}
    assert not any("_v1" in item["name"] or "_v2" in item["name"]
                   for item in record["outputs"])


def test_q50_archive_cannot_reuse_legacy_identity(tmp_path):
    from tests.test_monthly_demand import _archive
    from traffic_sim.simulation.monthly_demand import validate_demand_archive
    legacy = DemandBuildSpec("2027-12-01", purpose="closure_envelope")
    archive = _archive(tmp_path, legacy, "stress", finished_at="2027-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="another build contract"):
        validate_demand_archive(archive, replace(legacy, variant_mode="q50_only"))
