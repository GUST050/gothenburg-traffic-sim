"""Entry-level cover for the independent-vs-continuous measurement tool.

The tool had no test at all, and that is exactly how it broke: commit 6d735cc
made ``IndependentDailyRunner.cache_root`` a mandatory keyword-only argument
and this tool's construction site was not updated, so its ENTIRE independent
arm raised ``TypeError`` on the first candidate. Nothing noticed, because the
suite never executed the tool and ``tools/`` was outside the lint target.

These tests therefore run the real code path rather than inspecting it: the
construction is reached with the tool's own frozen registration and its own
argument list, so a signature change on either side fails here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _tool():
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "measure_independent_vs_continuous",
        ROOT / "tools" / "measure_independent_vs_continuous.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Sentinel(Exception):
    """Raised in place of the search, once construction has been reached."""


def _independent_case(registration):
    for case in registration["cases"]:
        spec = case.get("independent_spec") or {}
        if spec.get("interday_policy") == "independent_daily_reset_v1":
            return case
    pytest.skip("the frozen registration declares no independent case")


def test_the_frozen_registration_still_rebuilds_its_specs():
    """The tool refuses a drifted registration; prove it does not refuse this
    one, so a failure below is about the tool and not about its input."""
    tool = _tool()
    registration = tool.load_registration()
    case = _independent_case(registration)

    spec = tool._spec_from_case(case, "independent_spec")

    assert spec.interday_policy == "independent_daily_reset_v1"


def test_running_an_independent_arm_constructs_its_daily_runner(
        tmp_path, monkeypatch):
    """The regression itself: reach the construction and get past it.

    Before the fix this raised TypeError (missing 'cache_root') instead of the
    sentinel, so the assertion below is what pins the call site to the live
    signature. The real IndependentDailyRunner is used deliberately — stubbing
    it would test the stub's signature, which is the one thing that cannot
    drift.
    """
    import traffic_sim.simulation.monthly_demand as monthly_demand
    import traffic_sim.simulation.monthly_search as monthly_search

    tool = _tool()
    registration = tool.load_registration()
    case = _independent_case(registration)

    class _Resolver:
        """Stands in for the demand resolver, which would build real demand."""

        canonical_observations = ()

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(monthly_demand, "MonthlyDemandResolverRunner",
                        _Resolver)

    def _no_search(*args, **kwargs):
        raise _Sentinel

    monkeypatch.setattr(monthly_search, "run_monthly_search", _no_search)

    with pytest.raises(_Sentinel):
        tool._run_arm(
            case, "independent",
            policy=_policy_for(tool),
            runs_root=tmp_path / "runs",
            release_root=tmp_path / "release",
            workspace_root=tmp_path / "work")


def test_the_two_arms_never_share_a_daily_result_cache(tmp_path, monkeypatch):
    """Cache isolation is part of the measurement, not an implementation
    detail: a cache both arms could read would let one arm be answered by the
    other's results, and the comparison would be partly of itself."""
    import traffic_sim.simulation.independent_daily as independent_daily
    import traffic_sim.simulation.monthly_demand as monthly_demand
    import traffic_sim.simulation.monthly_search as monthly_search

    tool = _tool()
    case = _independent_case(tool.load_registration())
    seen = []

    real_runner = independent_daily.IndependentDailyRunner

    class _Spy(real_runner):
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("cache_root"))
            super().__init__(*args, **kwargs)

    class _Resolver:
        canonical_observations = ()

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(independent_daily, "IndependentDailyRunner", _Spy)
    monkeypatch.setattr(monthly_demand, "MonthlyDemandResolverRunner",
                        _Resolver)
    monkeypatch.setattr(monthly_search, "run_monthly_search",
                        lambda *a, **k: (_ for _ in ()).throw(_Sentinel))

    work = tmp_path / "work"
    with pytest.raises(_Sentinel):
        tool._run_arm(case, "independent", policy=_policy_for(tool),
                      runs_root=tmp_path / "runs",
                      release_root=tmp_path / "release", workspace_root=work)

    assert len(seen) == 1
    cache_root = Path(seen[0])
    assert cache_root.name.startswith("independent")
    # The arm's own run root is workspace_root / arm; the cache must not sit
    # inside it, or clearing one would silently clear the other.
    assert not str(cache_root).startswith(str(work / "independent") + "/")


def _policy_for(tool):
    """The tool's own frozen policy, loaded the way measure_case loads it."""
    import json

    from traffic_sim.simulation.monthly_search import MonthlySearchPolicy

    return MonthlySearchPolicy.from_dict(
        json.loads(Path(tool.DEFAULT_POLICY).read_text(encoding="utf-8")))
