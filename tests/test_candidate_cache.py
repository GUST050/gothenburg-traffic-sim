"""Candidate cache must be exact, complete, and interruption-safe."""

import candidate_cache


def _outputs(tmp_path):
    result = {}
    for name, text in (("routes.xml", "routes"), ("meta.json", "meta")):
        path = tmp_path / name
        path.write_text(text)
        result[name] = path
    return result


def test_cache_round_trip_and_key_changes(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("v1")
    inputs = {"network": source}
    first_key = candidate_cache.cache_key({"seed": 1}, inputs,
                                          {"source": source})
    outputs = _outputs(tmp_path)
    candidate_cache.store(tmp_path / "cache", first_key, outputs)

    restored = {name: tmp_path / "restored" / name for name in outputs}
    assert candidate_cache.restore(tmp_path / "cache", first_key, restored)
    assert restored["routes.xml"].read_text() == "routes"
    source.write_text("v2")
    second_key = candidate_cache.cache_key({"seed": 1}, inputs,
                                           {"source": source})
    assert first_key != second_key


def test_partial_entry_is_a_cache_miss(tmp_path):
    source = tmp_path / "routes.xml"
    source.write_text("routes")
    key = candidate_cache.cache_key({}, {"routes": source}, {})
    entry = tmp_path / "cache" / key
    entry.mkdir(parents=True)
    (entry / "manifest.json").write_text(
        '{"schema_version": 1, "outputs": {"routes.xml": '
        '{"sha256": "wrong"}}}')
    destination = tmp_path / "out.xml"
    assert not candidate_cache.restore(
        tmp_path / "cache", key, {"routes.xml": destination})
    assert not destination.exists()
