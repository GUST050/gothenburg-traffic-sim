import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from traffic_sim.demand import route_catalog


def _identity_inputs(tmp_path):
    network = tmp_path / "network.txt"
    flows = tmp_path / "flows.json"
    source = tmp_path / "builder.py"
    network.write_text("network")
    flows.write_text('{"edges": ["sensor"]}')
    source.write_text("print('builder')")
    config = {
        "n_total": 100,
        "start_date": "2025-09-16",
        "real_day_shape": "date-specific",
        "day_blocks": "date-specific",
        "route_diversity": 2.0,
    }
    inputs = {
        "network": network,
        "source_flows": flows,
        "source_flow_edge_set": flows,
        "real_day_shape": tmp_path / "missing-shape",
        "day_blocks": tmp_path / "missing-blocks",
    }
    sources = {"builder": source}
    return config, inputs, sources


def test_catalog_identity_excludes_date_but_keeps_structure(tmp_path):
    config, inputs, sources = _identity_inputs(tmp_path)
    first = route_catalog.catalog_key(config, inputs, sources, pool_key="weekday")
    changed = dict(config, start_date="2025-09-17", real_day_shape="another")
    assert route_catalog.catalog_key(changed, inputs, sources,
                                    pool_key="weekday") == first
    changed["route_diversity"] = 2.1
    assert route_catalog.catalog_key(changed, inputs, sources,
                                    pool_key="weekday") != first
    assert route_catalog.catalog_key(config, inputs, sources,
                                    pool_key="weekend") != first


def test_catalog_identity_keeps_sensor_edges_but_not_daily_values(tmp_path):
    config, inputs, sources = _identity_inputs(tmp_path)
    flows = inputs["source_flow_edge_set"]
    flows.write_text(json.dumps({"flows": {"sensor": [1, 2, 3]}}))
    first = route_catalog.catalog_key(
        config, inputs, sources, pool_key="weekday")
    flows.write_text(json.dumps({"flows": {"sensor": [99, 0, 8]}}))
    assert route_catalog.catalog_key(
        config, inputs, sources, pool_key="weekday") == first
    flows.write_text(json.dumps({
        "flows": {"sensor": [99, 0, 8], "new_sensor": [0, 1, 0]},
    }))
    assert route_catalog.catalog_key(
        config, inputs, sources, pool_key="weekday") != first


def test_catalog_size_ladder_is_bounded_and_deterministic():
    assert route_catalog.catalog_size_attempts(100) == (100, 150, 225)
    with pytest.raises(ValueError):
        route_catalog.catalog_size_attempts(0)


def test_candidate_catalog_command_uses_isolated_output(tmp_path):
    command = route_catalog.candidate_catalog_command(
        output_dir=tmp_path, pool_key="weekend", n_total=6000,
        through_fraction=0.5, gravity_km=1.8, gravity_alpha=1.5,
        cross_fraction=0.3, assignment_priors=Path("priors.json"), seed=42)
    assert command[command.index("--out-dir") + 1] == str(tmp_path)
    assert command[command.index("--catalog-pool-key") + 1] == "weekend"
    assert command[command.index("--n-total") + 1] == "6000"


def test_adoption_default_is_fail_safe_and_explicit(tmp_path, monkeypatch):
    config = tmp_path / "adoption.json"
    assert route_catalog.configured_candidate_source(config) == "legacy"
    config.write_text("not json")
    assert route_catalog.configured_candidate_source(config) == "legacy"
    monkeypatch.setattr(route_catalog, "catalog_entry_matches", lambda *a, **k: True)
    config.write_text(json.dumps({
        "schema_version": 2, "status": "adopt",
        "qualification_sha256": "a" * 64,
        "catalog_build_sha256": "d" * 64,
        "catalog_keys": {
            "weekday": "b" * 32,
            "weekend": "c" * 32,
        },
        "catalog_selected_n_total": {"weekday": 6000, "weekend": 6000},
    }))
    assert route_catalog.configured_candidate_source(config) == "catalog"
    config.write_text(json.dumps({
        "schema_version": 2, "status": "adopt",
        "qualification_sha256": "a" * 64,
        "catalog_build_sha256": "d" * 64,
        "catalog_keys": {"weekday": "z" * 32, "weekend": "c" * 32},
        "catalog_selected_n_total": {"weekday": 6000, "weekend": 6000},
    }))
    assert route_catalog.configured_candidate_source(config) == "legacy"


def test_ensure_catalog_is_single_complete_manifest(tmp_path):
    config, inputs, sources = _identity_inputs(tmp_path)
    identity = route_catalog.catalog_identity_payload(
        config, inputs, sources, pool_key="weekday")
    key = route_catalog.catalog_key(config, inputs, sources, pool_key="weekday")
    destinations = {
        label: tmp_path / "dest" / label for label in route_catalog.OUTPUTS
    }
    calls = []

    def builder(work):
        calls.append(1)
        (work / "catalog.rou.xml").write_text(
            '<routes><vehicle id="t0" depart="0"><route edges="a"/></vehicle></routes>')
        (work / "catalog.meta.json").write_text(
            json.dumps({"schema_version": 2, "location_pools": {},
                        "candidates": {"t0": {"purpose": "through"}}}))
        (work / "catalog.validation.json").write_text(json.dumps({
            "sensor": {"total": 1, "unique_routes": 1,
                       "unique_od_pairs": 1, "cross_1": 0,
                       "cross_2": 0, "cross_3plus": 0}
        }))
        (work / "catalog.template.json").write_text(json.dumps({
            "schema_version": 1, "pool_key": "weekday", "templates": 1,
            "semantic_sha256": "a" * 64,
        }))
        return {label: work / label for label in route_catalog.OUTPUTS}

    root = tmp_path / "catalog"
    assert route_catalog.ensure_catalog(root, key, identity, destinations, builder)
    assert route_catalog.ensure_catalog(root, key, identity, destinations, builder)
    assert len(calls) == 1
    assert destinations["catalog.rou.xml"].is_file()
    manifest = json.loads((root / key / "manifest.json").read_text())
    assert manifest["catalog_key"] == key
    (root / key / "catalog.meta.json").write_text("corrupt")
    assert not route_catalog.restore_catalog(root, key, destinations,
                                             expected_identity=identity)
    assert route_catalog.ensure_catalog(
        root, key, identity, destinations, builder) is True
    assert len(calls) == 2
    assert route_catalog.restore_catalog(
        root, key, destinations, expected_identity=identity)
    assert len(list(root.glob(f".{key}.invalid.*"))) == 1


def test_sized_catalog_retries_only_sensor_support(tmp_path, monkeypatch):
    config, inputs, sources = _identity_inputs(tmp_path)
    destinations = {
        label: tmp_path / "dest" / label for label in route_catalog.OUTPUTS
    }

    def command_for(n_total, output_dir):
        return ["fake-builder", str(n_total), str(output_dir)]

    class Result:
        stderr = ""
        stdout = ""

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **_kwargs):
        n_total, output_dir = int(command[1]), Path(command[2])
        output_dir.mkdir(exist_ok=True)
        (output_dir / "candidates.rou.xml").write_text(
            '<routes><vehicle id="t0" depart="0"><route edges="sensor"/></vehicle></routes>')
        (output_dir / "candidates.meta.json").write_text(json.dumps({
            "schema_version": 2, "location_pools": {},
            "candidates": {"t0": {"purpose": "through"}},
        }))
        unique = 1 if n_total == 100 else 2
        (output_dir / "sensor_coverage_report.json").write_text(json.dumps({
            "sensor": {"unique_routes": unique, "total": unique}
        }))
        (output_dir / "canonical_template_report.json").write_text(json.dumps({
            "schema_version": 1, "pool_key": "weekday", "templates": 1,
            "semantic_sha256": "b" * 64,
        }))
        return Result(1 if unique < 2 else 0)

    monkeypatch.setattr(route_catalog.subprocess, "run", fake_run)
    result = route_catalog.ensure_sized_catalog(
        root=tmp_path / "catalog", pool_key="weekday", base_config=config,
        inputs=inputs, source_files=sources, destinations=destinations,
        command_for=command_for, start_n_total=100, min_per_sensor=2,
        attempts=2)
    assert result["n_total"] == 150
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "failed", "built"
    ]


def test_failed_builder_never_publishes_partial_entry(tmp_path):
    config, inputs, sources = _identity_inputs(tmp_path)
    identity = route_catalog.catalog_identity_payload(
        config, inputs, sources, pool_key="weekday")
    key = route_catalog.catalog_key(config, inputs, sources, pool_key="weekday")
    destinations = {
        label: tmp_path / "dest" / label for label in route_catalog.OUTPUTS
    }

    def broken(work):
        (work / "catalog.rou.xml").write_text("<routes>")
        raise RuntimeError("injected producer failure")

    with pytest.raises(RuntimeError, match="injected"):
        route_catalog.ensure_catalog(
            tmp_path / "catalog", key, identity, destinations, broken)
    assert not (tmp_path / "catalog" / key).exists()


def test_combine_catalogs_prefixes_ids_and_preserves_metadata(tmp_path):
    sources = {}
    for pool in ("weekday", "weekend"):
        rou = tmp_path / f"{pool}.rou.xml"
        meta = tmp_path / f"{pool}.meta.json"
        rou.write_text(
            '<routes><vehicle id="t0" depart="0"><route edges="a"/></vehicle></routes>')
        meta.write_text(json.dumps({
            "schema_version": 2,
            "location_pools": {"home:a": [{"id": "a"}]},
            "candidates": {"t0": {"purpose": pool, "tour_id": "tour-0"}},
        }))
        sources[pool] = (rou, meta)
    out_rou, out_meta = tmp_path / "combined.rou.xml", tmp_path / "combined.meta.json"
    report = route_catalog.combine_catalogs(sources, out_rou, out_meta)
    assert report["vehicles"] == 2
    combined = json.loads(out_meta.read_text())
    assert set(combined["candidates"]) == {"weekday__t0", "weekend__t0"}
    assert {record["tour_id"] for record in combined["candidates"].values()} == {
        "weekday__tour-0", "weekend__tour-0"
    }
    assert {v.get("id") for v in ET.parse(out_rou).getroot().findall("vehicle")} == {
        "weekday__t0", "weekend__t0"
    }
