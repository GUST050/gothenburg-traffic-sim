import gzip
import hashlib

import pytest

from tools.compact_transformed_routes import compact_store


def _legacy_route(store, contents):
    digest = hashlib.sha256(contents).hexdigest()
    path = store / digest[:2] / f"{digest}.rou.xml"
    path.parent.mkdir(parents=True)
    path.write_bytes(contents)
    return digest, path


def test_inventory_is_read_only(tmp_path):
    contents = b"<routes/>\n" * 100
    _digest, source = _legacy_route(tmp_path, contents)

    totals = compact_store(tmp_path, execute=False)

    assert totals["legacy_files"] == 1
    assert totals["legacy_bytes"] == len(contents)
    assert source.read_bytes() == contents
    assert not list(tmp_path.rglob("*.gz"))


def test_compaction_verifies_then_removes_legacy_route(tmp_path):
    contents = b'<vehicle id="v"><route edges="a b"/></vehicle>\n' * 1000
    digest, source = _legacy_route(tmp_path, contents)

    totals = compact_store(tmp_path, execute=True)

    compressed = source.with_name(source.name + ".gz")
    assert not source.exists()
    assert compressed.is_file()
    with gzip.open(compressed, "rb") as handle:
        assert handle.read() == contents
    assert totals["compressed_files_written"] == 1
    assert totals["legacy_files_removed"] == 1
    assert totals["bytes_reclaimed"] == len(contents) - compressed.stat().st_size
    assert compressed.name == f"{digest}.rou.xml.gz"


def test_compaction_fails_closed_on_misnamed_route(tmp_path):
    _digest, source = _legacy_route(tmp_path, b"<routes/>\n")
    source.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="does not hash"):
        compact_store(tmp_path, execute=True)

    assert source.read_bytes() == b"tampered"
    assert not list(tmp_path.rglob("*.gz"))
