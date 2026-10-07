"""The frozen SUMO network is restored only when a fresh build reproduces it."""

import hashlib
import lzma
from pathlib import Path

import pytest

from tools import restore_frozen_network as frozen

HEADER = "<!-- generated on {stamp} by Eclipse SUMO netconvert 1.27.1\n"


def _net(stamp: str, body: str = '<net version="1.20"/>\n') -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n\n'
            + HEADER.format(stamp=stamp) + "-->\n\n" + body).encode()


def _archive(tmp_path, payload: bytes) -> tuple[Path, str]:
    path = tmp_path / "net.net.xml.xz"
    path.write_bytes(lzma.compress(payload))
    return path, hashlib.sha256(payload).hexdigest()


def test_restores_exact_bytes_when_build_differs_only_in_its_timestamp(tmp_path):
    frozen_bytes = _net("2026-07-17T13:43:41")
    archive, digest = _archive(tmp_path, frozen_bytes)
    built = tmp_path / "net.net.xml"
    built.write_bytes(_net("2026-10-07T20:51:55"))

    frozen.restore(archive, built, expected_sha256=digest)

    assert built.read_bytes() == frozen_bytes


def test_refuses_a_rebuild_that_drifted_from_the_frozen_network(tmp_path):
    archive, digest = _archive(tmp_path, _net("2026-07-17T13:43:41"))
    built = tmp_path / "net.net.xml"
    drifted = _net("2026-10-07T20:51:55", '<net version="1.21"/>\n')
    built.write_bytes(drifted)

    with pytest.raises(frozen.FrozenNetworkError, match="drift"):
        frozen.restore(archive, built, expected_sha256=digest)

    assert built.read_bytes() == drifted


def test_refuses_an_archive_with_the_wrong_hash(tmp_path):
    archive, _digest = _archive(tmp_path, _net("2026-07-17T13:43:41"))
    built = tmp_path / "net.net.xml"
    built.write_bytes(_net("2026-10-07T20:51:55"))

    with pytest.raises(frozen.FrozenNetworkError, match="sha256"):
        frozen.restore(archive, built, expected_sha256="0" * 64)


def test_requires_a_fresh_build_to_compare_against(tmp_path):
    archive, digest = _archive(tmp_path, _net("2026-07-17T13:43:41"))

    with pytest.raises(frozen.FrozenNetworkError, match="make sumo-net"):
        frozen.restore(archive, tmp_path / "net.net.xml", expected_sha256=digest)


def test_tracked_archive_holds_the_network_the_frozen_evidence_binds():
    payload = lzma.decompress(frozen.FROZEN_ARCHIVE.read_bytes())

    assert hashlib.sha256(payload).hexdigest() == frozen.FROZEN_NETWORK_SHA256
