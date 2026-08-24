"""Pruning the pool cache must remove only what no key can name again."""

import json
import os
import time

import pytest

from tools import prune_candidate_cache as prune


def _entry(root, name, digest, age_days):
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "candidates.rou.xml").write_text("routes")
    (entry / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "outputs": {"candidates.rou.xml": {"sha256": digest, "bytes": 6}},
    }))
    stamp = time.time() - age_days * 86400
    for path in (entry / "candidates.rou.xml", entry / "manifest.json", entry):
        os.utime(path, (stamp, stamp))
    return entry


def _day(library_root, date, key, source_hashes, pool_digest):
    directory = library_root / date / key
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({
        "identity": {
            "date": date,
            "inputs": {"candidate_pool": pool_digest},
            "source_hashes": source_hashes,
        },
    }))


def _run(monkeypatch, cache_root, library_root, argv=()):
    monkeypatch.setattr(
        prune, "parse_args",
        lambda: __import__("argparse").Namespace(
            yes="--yes" in argv, keep_recent_hours=24.0,
            cache_root=cache_root, library_root=library_root))
    return prune.main()


def test_reports_without_deleting_and_deletes_only_with_yes(
        tmp_path, monkeypatch, capsys):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    old = _entry(cache_root, "a" * 24, "digest-old", age_days=30)
    monkeypatch.setattr(prune, "newest_key_input_mtime",
                        lambda root: (time.time() - 86400, "build_candidates.py"))
    monkeypatch.setattr(prune, "pools_named_by_live_days", lambda root: set())

    assert _run(monkeypatch, cache_root, tmp_path / "days") == 0
    assert old.is_dir(), "a report must not delete anything"
    assert "rerun with --yes" in capsys.readouterr().out

    assert _run(monkeypatch, cache_root, tmp_path / "days", ("--yes",)) == 0
    assert not old.exists()


def test_entry_named_by_a_live_day_survives_the_timestamp_rule(
        tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    protected = _entry(cache_root, "b" * 24, "digest-live", age_days=30)
    doomed = _entry(cache_root, "c" * 24, "digest-dead", age_days=30)
    monkeypatch.setattr(prune, "newest_key_input_mtime",
                        lambda root: (time.time() - 86400, "pfe.py"))
    monkeypatch.setattr(prune, "pools_named_by_live_days",
                        lambda root: {"digest-live"})

    assert _run(monkeypatch, cache_root, tmp_path / "days", ("--yes",)) == 0
    assert protected.is_dir()
    assert not doomed.exists()


def test_recent_entries_are_never_touched(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    fresh = _entry(cache_root, "d" * 24, "digest-fresh", age_days=0)
    monkeypatch.setattr(prune, "newest_key_input_mtime",
                        lambda root: (time.time(), "build_candidates.py"))
    monkeypatch.setattr(prune, "pools_named_by_live_days", lambda root: set())

    assert _run(monkeypatch, cache_root, tmp_path / "days", ("--yes",)) == 0
    assert fresh.is_dir()


def test_unreadable_key_inputs_refuse_to_prune(tmp_path, monkeypatch, capsys):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    entry = _entry(cache_root, "e" * 24, "digest", age_days=30)
    monkeypatch.setattr(prune, "newest_key_input_mtime", lambda root: (0.0, "x"))

    assert _run(monkeypatch, cache_root, tmp_path / "days", ("--yes",)) == 1
    assert entry.is_dir()
    assert "refusing to guess" in capsys.readouterr().out


def test_entry_vanishing_during_scan_is_ignored(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    vanished = _entry(cache_root, "f" * 24, "digest", age_days=30)
    monkeypatch.setattr(prune, "newest_key_input_mtime",
                        lambda root: (time.time() - 86400, "pfe.py"))
    monkeypatch.setattr(prune, "pools_named_by_live_days", lambda root: set())
    original_stat = type(vanished).stat

    def racing_stat(path, *args, **kwargs):
        if path == vanished:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(vanished), "stat", racing_stat)
    assert _run(monkeypatch, cache_root, tmp_path / "days") == 0


def test_live_day_digests_come_from_the_builder_inventory(tmp_path):
    import build_sumo_demand

    current = build_sumo_demand.demand_day_source_hashes()
    library_root = tmp_path / "days"
    _day(library_root, "2027-05-25", "k1", current, "pool-live")
    _day(library_root, "2027-05-26", "k2", {"pfe": "superseded"}, "pool-dead")

    assert prune.pools_named_by_live_days(library_root) == {"pool-live"}


@pytest.mark.parametrize("name", prune.KEY_SOURCES)
def test_key_sources_exist(name):
    assert (prune.ROOT / name).is_file(), (
        f"{name} is hashed into a pool key but is not in the tree; the "
        "prune rule would silently ignore its changes")
