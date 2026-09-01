import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from traffic_sim.demand import route_catalog
from traffic_sim.demand.sensor_route_contract import (
    ABS_TOLERANCE_S, POLICY_VERSION, REL_TOLERANCE, route_digest)


def _strict_metadata(vehicle_id, edges, sensors, **record):
    route = list(edges)
    proof = {
        "policy_version": POLICY_VERSION,
        "pass": True,
        "network_sha256": "a" * 64,
        "route_sha256": route_digest(route),
        "origin_edge": route[0],
        "destination_edge": route[-1],
        "route_cost_s": 0.0,
        "shortest_free_cost_s": 0.0,
        "sensor_penalty_s": {sensor: 1.0 for sensor in sorted(sensors)},
        "sensor_edges": sorted(sensors),
        "absolute_tolerance_s": ABS_TOLERANCE_S,
        "relative_tolerance": REL_TOLERANCE,
    }
    return {
        "schema_version": 3,
        "location_pools": {},
        "sensor_route_contract": {
            "policy_version": POLICY_VERSION,
            "network_sha256": "a" * 64,
            "absolute_tolerance_s": ABS_TOLERANCE_S,
            "relative_tolerance": REL_TOLERANCE,
            "qualified_candidates": 1,
        },
        "candidates": {
            vehicle_id: {**record, "sensor_route_contract": proof},
        },
    }


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
    # The identity contract is exact, so a fixture must present the real
    # label set rather than a stand-in name.
    sources = {"build_candidates": source}
    for label in route_catalog.CATALOG_SOURCE_LABELS - {"build_candidates"}:
        extra = tmp_path / f"{label.replace('/', '_')}.py"
        extra.write_text(f"# {label}")
        sources[label] = extra
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
    monkeypatch.setattr(route_catalog, "PROJECT_ROOT", tmp_path)
    validation = tmp_path / "validation"
    validation.mkdir()
    keys = {"weekday": "b" * 32, "weekend": "c" * 32}
    sizes = {"weekday": 6000, "weekend": 6000}
    build = validation / "build.json"
    build.write_text(json.dumps({"results": {
        pool: {"key": keys[pool], "n_total": sizes[pool]} for pool in keys
    }}))
    trials = validation / "trials.json"
    trials.write_text(json.dumps({"trials": []}))
    suite = validation / "suite.json"
    suite_gates = {"purpose_route_compatibility": True}
    suite.write_text(json.dumps({
        "schema_version": 2,
        "gates": {
            gate: {"status": "pass", "tests": ["tests/test_pfe.py"]}
            for gate in suite_gates
        },
    }))
    qualification = validation / "qualification.json"
    qualification.write_text(json.dumps({
        "verdict": "adopt", "gates": {"hard_correctness": True},
        "suite_hard_gates": suite_gates,
        "evidence_binding": {
            "catalog_build_sha256": route_catalog.sha256_file(build),
            "catalog_keys": keys,
            "catalog_selected_n_total": sizes,
            "trials_path": "validation/trials.json",
            "trials_sha256": route_catalog.sha256_file(trials),
            "suite_gates_path": "validation/suite.json",
            "suite_gates_sha256": route_catalog.sha256_file(suite),
        },
    }))
    config.write_text(json.dumps({
        "schema_version": 3, "status": "adopt",
        "qualification_sha256": route_catalog.sha256_file(qualification),
        "catalog_build_sha256": route_catalog.sha256_file(build),
        "evidence": {
            "qualification": {
                "path": "validation/qualification.json",
                "sha256": route_catalog.sha256_file(qualification),
            },
            "catalog_build": {
                "path": "validation/build.json",
                "sha256": route_catalog.sha256_file(build),
            },
        },
        "catalog_keys": keys,
        "catalog_selected_n_total": sizes,
    }))
    assert route_catalog.configured_candidate_source(config) == "catalog"
    suite.write_text("{}")
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
            '<routes><vehicle id="t0" depart="0"><route edges="sensor"/></vehicle></routes>')
        (work / "catalog.meta.json").write_text(
            json.dumps(_strict_metadata(
                "t0", ["sensor"], {"sensor"}, purpose="through")))
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
        (output_dir / "candidates.meta.json").write_text(json.dumps(
            _strict_metadata(
                "t0", ["sensor"], {"sensor"}, purpose="through")))
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
        document = _strict_metadata(
            "t0", ["a"], {"a"}, purpose=pool, tour_id="tour-0")
        document["location_pools"] = {"home:a": [{"id": "a"}]}
        meta.write_text(json.dumps(document))
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


class TestCatalogSourceContract:
    """The catalog identity binds pool generation, and nothing downstream.

    Narrowed 2026-08-26 after commit c653b24 — whose purpose was to harden
    the catalog's qualification evidence — invalidated the adopted catalog by
    editing pfe.py, route_catalog.py and catalog_qualification.py, none of
    which pool generation executes.
    """

    def test_a_missing_generator_source_is_refused(self, tmp_path):
        config, inputs, sources = _identity_inputs(tmp_path)
        del sources["build_candidates"]
        with pytest.raises(ValueError, match="missing"):
            route_catalog.catalog_identity_payload(
                config, inputs, sources, pool_key="weekday")

    def test_an_unexpected_source_is_refused(self, tmp_path):
        """This is exactly how the whole demand inventory crept in."""
        config, inputs, sources = _identity_inputs(tmp_path)
        stray = tmp_path / "pfe.py"
        stray.write_text("# solver")
        sources["source:pfe"] = stray
        with pytest.raises(ValueError, match="unexpected"):
            route_catalog.catalog_identity_payload(
                config, inputs, sources, pool_key="weekday")

    def test_the_bound_set_still_covers_what_generation_imports(self):
        """Measured, not asserted: what build_candidates actually imports.

        If pool generation ever starts importing a module the identity does
        not bind, a stale catalog could serve a changed pool. This fails then,
        rather than after a silently wrong build.
        """
        import subprocess
        import sys

        probe = (
            "import sys, json, pathlib\n"
            "root = pathlib.Path('.').resolve()\n"
            "before = set(sys.modules)\n"
            "import build_candidates\n"
            "out = []\n"
            "for name in set(sys.modules) - before:\n"
            "    mod = sys.modules.get(name)\n"
            "    path = getattr(mod, '__file__', None)\n"
            "    if not path:\n"
            "        continue\n"
            "    p = pathlib.Path(path).resolve()\n"
            "    try:\n"
            "        out.append(str(p.relative_to(root)))\n"
            "    except ValueError:\n"
            "        pass\n"
            "print(json.dumps(sorted(out)))\n"
        )
        result = subprocess.run([sys.executable, "-c", probe],
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr[-2000:]
        imported = set(json.loads(result.stdout.strip().splitlines()[-1]))

        import build_sumo_demand

        bound = set()
        for label, path in build_sumo_demand.candidate_identity_components(
                n_total=6000, through_fraction=0.5, gravity_km=1.8,
                gravity_alpha=1.5, cross_fraction=0.3, is_weekend=False,
                start_date="2027-09-01", seed=42,
                min_per_sensor=50,
                home=build_sumo_demand.sumo_home(),
                flows_path=build_sumo_demand.FLOWS_PATH)[2].items():
            assert label in route_catalog.CATALOG_SOURCE_LABELS
            bound.add(str(Path(path)))

        # Imported by generation but deliberately NOT bound, each because it
        # is a thin locator whose behaviour is already pinned elsewhere in the
        # identity. Listed explicitly so a NEW unbound import fails this test.
        known_unbound = {
            "demand/__init__.py",
            "traffic_sim/simulation/__init__.py",
            "traffic_sim/simulation/runtime.py",
            "traffic_sim/intake/__init__.py",
            "traffic_sim/__init__.py",
            "demand/locations.py",
            "traffic_sim/intake/sensors.py",
            "build_data.py",
            "build_candidates.py",
            "dirsplit/__init__.py",
            "dirsplit/geo.py",
            "traffic_sim/core/__init__.py",
            "traffic_sim/core/fingerprint.py",
            "traffic_sim/demand/__init__.py",
        }
        escaped = imported - bound - known_unbound
        assert not escaped, (
            "pool generation imports project code the catalog identity does "
            f"not bind: {sorted(escaped)}")

    def test_downstream_stages_are_genuinely_not_imported_by_generation(self):
        """Pins the reason those 22 entries were dropped from the identity."""
        import subprocess
        import sys

        probe = (
            "import sys\n"
            "import build_candidates\n"
            "downstream = ['traffic_sim.demand.pfe', 'demand.calibration',\n"
            "              'demand.publication', 'demand.structure',\n"
            "              'traffic_sim.demand.catalog_qualification',\n"
            "              'traffic_sim.demand.route_catalog']\n"
            "print([m for m in downstream if m in sys.modules])\n"
        )
        result = subprocess.run([sys.executable, "-c", probe],
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.strip().endswith("[]"), result.stdout
