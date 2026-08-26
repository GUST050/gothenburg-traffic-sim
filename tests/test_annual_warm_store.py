import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.annual_warm_store import AnnualWarmArtifactStore


MEMBER_NAMES = (
    "state.xml.gz",
    "prefix_evidence.json",
    "route.rou.xml",
    "demand_meta.json",
    "demand_build_spec.json",
    "demand_manifest.json",
)


def _members(root: Path, *, marker="first"):
    root.mkdir(parents=True, exist_ok=True)
    values = {
        "state.xml.gz": gzip.compress(("state-" + marker).encode(), mtime=0),
        "prefix_evidence.json": json.dumps({"prefix": marker}).encode() * 400,
        "route.rou.xml": ("<routes>" + marker * 2000 + "</routes>").encode(),
        "demand_meta.json": json.dumps({"meta": marker}).encode(),
        "demand_build_spec.json": json.dumps({"spec": marker}).encode(),
        "demand_manifest.json": json.dumps({"manifest": marker}).encode(),
    }
    result = {}
    for name, data in values.items():
        path = root / name
        path.write_bytes(data)
        result[name] = path
    return result, values


def _pack(store, members, *, unit="annual-unit-one"):
    return store.pack_artifact(
        plan_content_key="a" * 64,
        unit_id=unit,
        members=members,
        metadata={
            "demand_build_key": "build-one",
            "checkpoint_s": 24300,
            "seed": 1000,
            "variant": "q50",
        },
    )


def test_pack_restore_is_byte_exact_and_compresses_routes(tmp_path):
    members, original = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    manifest = _pack(store, members)

    restored = store.restore_artifact("annual-unit-one", tmp_path / "restored")
    assert set(restored) == set(MEMBER_NAMES)
    assert {name: path.read_bytes() for name, path in restored.items()} == original
    assert manifest["members"]["route.rou.xml"]["encoding"] == "gzip"
    assert manifest["members"]["route.rou.xml"]["stored_bytes"] < \
        manifest["members"]["route.rou.xml"]["content_bytes"]
    assert list((store.root / ".staging").iterdir()) == []


def test_existing_shared_member_is_reused_without_recompression(
        tmp_path, monkeypatch):
    members, _original = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    first = _pack(store, members, unit="annual-unit-one")

    original_gzip_file = gzip.GzipFile

    def compression_must_not_run(*args, **kwargs):
        mode = kwargs.get("mode", args[1] if len(args) > 1 else None)
        if mode == "wb":
            raise AssertionError(
                "existing content-addressed member was recompressed")
        return original_gzip_file(*args, **kwargs)

    monkeypatch.setattr(gzip, "GzipFile", compression_must_not_run)
    second = _pack(store, members, unit="annual-unit-two")
    assert second["members"] == first["members"]


def test_existing_shared_gzip_is_revalidated_before_reuse(tmp_path):
    members, _original = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    first = _pack(store, members, unit="annual-unit-one")
    blob = store.root / first["members"]["route.rou.xml"]["blob"]
    blob.write_bytes(blob.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="gzip blob"):
        _pack(store, members, unit="annual-unit-two")


def test_identical_member_bytes_deduplicate_across_units(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    first = _pack(store, members, unit="annual-unit-one")
    second = _pack(store, members, unit="annual-unit-two")

    assert first["members"] == second["members"]
    blobs = [path for path in (tmp_path / "store" / "blobs").rglob("*")
             if path.is_file()]
    assert len(blobs) == len({
        item["blob"] for item in first["members"].values()
    })


def test_existing_unit_is_immutable_but_idempotent_pack_is_allowed(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    first = _pack(store, members)
    assert _pack(store, members) == first

    changed, _ = _members(tmp_path / "changed", marker="different")
    with pytest.raises(ValueError, match="different content"):
        _pack(store, changed)


def test_artifact_manifest_symlink_is_never_accepted(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    target = store.root / "artifacts" / "annual-unit-one.json"
    target.parent.mkdir(parents=True)
    target.symlink_to(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="symlink|regular"):
        _pack(store, members)


def test_blob_tampering_fails_before_restore(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    manifest = _pack(store, members)
    blob = store.root / manifest["members"]["route.rou.xml"]["blob"]
    blob.write_bytes(blob.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="stored bytes changed"):
        store.load_artifact("annual-unit-one")
    with pytest.raises(ValueError):
        store.restore_artifact("annual-unit-one", tmp_path / "restore")


def test_manifest_tampering_fails_even_with_recomputed_member_data(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    _pack(store, members)
    path = store.root / "artifacts" / "annual-unit-one.json"
    payload = json.loads(path.read_text())
    payload["metadata"]["seed"] = 9999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="content key differs"):
        store.load_artifact("annual-unit-one")


def test_symlink_sources_and_unknown_member_sets_fail_closed(tmp_path):
    members, _ = _members(tmp_path / "source")
    link = tmp_path / "state-link"
    link.symlink_to(members["state.xml.gz"])
    members["state.xml.gz"] = link
    store = AnnualWarmArtifactStore(tmp_path / "store")
    with pytest.raises(ValueError, match="regular non-symlink"):
        _pack(store, members)

    members, _ = _members(tmp_path / "other")
    incomplete = copy.copy(members)
    incomplete.pop("demand_manifest.json")
    with pytest.raises(ValueError, match="must be exactly"):
        _pack(store, incomplete)


def test_storage_totals_report_logical_compression(tmp_path):
    members, _ = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    _pack(store, members)
    totals = store.storage_totals("annual-unit-one")
    assert totals["content_bytes"] == sum(path.stat().st_size for path in members.values())
    assert 0 < totals["stored_ratio"] < 1
    assert totals["stored_bytes"] < totals["content_bytes"]


def test_member_records_bind_original_and_stored_sha256(tmp_path):
    members, values = _members(tmp_path / "source")
    store = AnnualWarmArtifactStore(tmp_path / "store")
    manifest = _pack(store, members)
    for name, record in manifest["members"].items():
        assert record["content_sha256"] == hashlib.sha256(values[name]).hexdigest()
        blob = store.root / record["blob"]
        assert record["stored_sha256"] == hashlib.sha256(blob.read_bytes()).hexdigest()
