import json

import pytest

from tools import benchmark_exact_close_cache as bench


def test_target_must_be_exact_loopback_close_endpoint():
    bench._require_loopback_close("http://127.0.0.1:8000/api/close")
    for target in (
            "https://127.0.0.1:8000/api/close",
            "http://example.com/api/close",
            "http://127.0.0.1:8000/api/close?edges=a",
            "http://127.0.0.1:8000/api/cancel"):
        with pytest.raises(ValueError):
            bench._require_loopback_close(target)


def test_fixture_requires_structured_closure_and_trajectory(tmp_path):
    fixture = tmp_path / "scenario.json"
    fixture.write_text(json.dumps({"scenario_spec": {"scenario_id": "x"}}))
    with pytest.raises(ValueError, match="requires a closure"):
        bench._load_fixture(fixture)


def test_percentile_uses_linear_interpolation():
    assert bench._percentile(list(range(1, 11)), 0.95) == pytest.approx(9.55)


def test_trials_below_contract_are_refused(tmp_path):
    with pytest.raises(ValueError, match="at least 10"):
        bench.run_benchmark(
            target=bench.DEFAULT_TARGET, fixture=tmp_path / "missing.json",
            trials=9, timeout=1.0)
