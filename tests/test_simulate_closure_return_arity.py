"""Regression for the arity bug fixed 2026-09-03.

`suggest_closure_time.simulate_closure` grew a 5th return value
(`n_rerouted`, added alongside the rerouted-around-closure accounting) but
its two callers in `run_monthly_proxy_validation.py` (`_baseline` and
`_run_case`) still unpacked only 4 values. That is a `ValueError: too many
values to unpack` on the very first real baseline or candidate SUMO run —
pylint's `unbalanced-tuple-unpacking` caught it as a *possible* mismatch,
but nothing previously turned that into a hard, always-run regression.

Two layers:
  * a structural AST check that the unpacking arity at every call site in
    `run_monthly_proxy_validation.py` matches the live arity of
    `simulate_closure`'s own `return (...)` statement, so this cannot drift
    silently again regardless of which side changes next; and
  * a behavioural check that `_baseline` actually accepts the real 5-tuple
    shape end to end (SUMO itself mocked out, cache path exercised for real).
"""
import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "run_monthly_proxy_validation.py"
LEGACY_PATH = ROOT / "suggest_closure_time.py"


def _runner():
    if "rmpv_arity_runner" in sys.modules:
        return sys.modules["rmpv_arity_runner"]
    spec = importlib.util.spec_from_file_location(
        "rmpv_arity_runner", str(RUNNER_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules["rmpv_arity_runner"] = module
    spec.loader.exec_module(module)
    return module


def _simulate_closure_return_arity() -> int:
    """The number of values `suggest_closure_time.simulate_closure` returns,
    read straight from its source `return (...)` statement."""
    tree = ast.parse(LEGACY_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "simulate_closure":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Tuple):
                    return len(inner.value.elts)
            raise AssertionError("simulate_closure has no tuple return")
    raise AssertionError("simulate_closure not found")


def _unpacking_targets_for_simulate_closure_calls() -> list[tuple[int, int]]:
    """(line, target_count) for every `... = legacy.simulate_closure(...)` in
    the runner, whether or not it is preceded by a starred/plain tuple."""
    tree = ast.parse(RUNNER_PATH.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        is_simulate_closure = (
            isinstance(func, ast.Attribute)
            and func.attr == "simulate_closure"
            and isinstance(func.value, ast.Name)
            and func.value.id == "legacy"
        )
        if not is_simulate_closure:
            continue
        assert len(node.targets) == 1
        target = node.targets[0]
        assert isinstance(target, ast.Tuple), "expected tuple-unpacking target"
        found.append((node.lineno, len(target.elts)))
    return found


class TestReturnArityMatchesEveryCaller:

    def test_simulate_closure_currently_returns_five_values(self):
        # Pinned so a change to either side of the contract is a deliberate,
        # reviewed edit to this test, not a silent drift.
        assert _simulate_closure_return_arity() == 5

    def test_every_call_site_unpacks_the_live_arity(self):
        arity = _simulate_closure_return_arity()
        sites = _unpacking_targets_for_simulate_closure_calls()
        assert sites, "expected at least one legacy.simulate_closure(...) call site"
        mismatched = [(line, count) for line, count in sites if count != arity]
        assert not mismatched, (
            f"call site(s) unpack {mismatched} values but "
            f"simulate_closure returns {arity}: {mismatched}"
        )

    def test_there_are_exactly_the_two_known_call_sites(self):
        # Guards against a new call site being added without this test
        # noticing it exists at all (the arity check above only runs over
        # whatever ast.walk finds).
        sites = _unpacking_targets_for_simulate_closure_calls()
        assert len(sites) == 2


def _synthetic_archive(root, name="runs/demand-arity-fixture"):
    archive = root / name
    archive.mkdir(parents=True)
    meta = {
        "demand_build_key": "k" * 16,
        "build_id": "b" * 20,
        "epoch_sim": "2027-07-15T00:00:00",
        "n_intervals": 96,
        "n_variants": 1,
    }
    (archive / "demand_meta.json").write_text(json.dumps(meta, sort_keys=True))
    return archive, meta


class TestBaselineAcceptsTheRealFiveTupleShape:
    """Exercises the actual `_baseline` code path (cache miss then cache
    hit) with a fake `simulate_closure` shaped exactly like production's
    real return, rather than mocking the unpacking away."""

    def _metrics(self, runner):
        return runner.closure_metrics.DisruptionMetrics(
            total_time_loss_s=12.5, trip_count=3, unfinished_trips=0,
            unfinished_waiting_trips=0, teleport_total=0, teleport_reasons={},
            loaded=3, inserted=3, running_at_end=0, waiting_at_end=0,
        )

    def test_a_cache_miss_calls_and_unpacks_without_raising(
            self, tmp_path, monkeypatch):
        runner = _runner()
        archive, meta = _synthetic_archive(tmp_path)
        metrics = self._metrics(runner)
        calls = []

        def fake_simulate_closure(**kwargs):
            calls.append(kwargs)
            # The real production shape: (metrics, n_truncated, n_dropped,
            # per_seed_time_loss, n_rerouted).
            return metrics, 0, 0, [10.0, 12.0, 15.0], 2

        monkeypatch.setattr(runner.legacy, "simulate_closure", fake_simulate_closure)
        monkeypatch.setattr(runner, "sumo_version", lambda *_a, **_k: "1.2.3")
        monkeypatch.setattr(runner, "sha256_file", lambda *_a, **_k: "f" * 64)

        run_root = tmp_path / "run_root"
        (run_root / "baselines").mkdir(parents=True)

        result = runner._baseline(
            demand_key="k" * 16,
            archive=archive,
            metadata=meta,
            variants=[],
            run_root=run_root,
            sumo_home=tmp_path,
            seed_workers=1,
        )
        assert len(calls) == 1
        got_metrics, per_seed, cache_key, demand_digest = result
        assert got_metrics == metrics
        assert per_seed == [10.0, 12.0, 15.0]
        assert isinstance(cache_key, str) and cache_key
        assert isinstance(demand_digest, str) and demand_digest

    def test_a_cache_hit_never_calls_simulate_closure_again(
            self, tmp_path, monkeypatch):
        runner = _runner()
        archive, meta = _synthetic_archive(tmp_path)
        metrics = self._metrics(runner)
        calls = []

        def fake_simulate_closure(**kwargs):
            calls.append(kwargs)
            return metrics, 0, 0, [10.0, 12.0, 15.0], 2

        monkeypatch.setattr(runner.legacy, "simulate_closure", fake_simulate_closure)
        monkeypatch.setattr(runner, "sumo_version", lambda *_a, **_k: "1.2.3")
        monkeypatch.setattr(runner, "sha256_file", lambda *_a, **_k: "f" * 64)

        run_root = tmp_path / "run_root"
        (run_root / "baselines").mkdir(parents=True)
        kwargs = dict(
            demand_key="k" * 16, archive=archive, metadata=meta, variants=[],
            run_root=run_root, sumo_home=tmp_path, seed_workers=1,
        )
        first = runner._baseline(**kwargs)
        second = runner._baseline(**kwargs)
        assert len(calls) == 1, "cache hit must not re-invoke simulate_closure"
        assert first[2] == second[2]
        assert first[1] == second[1]
