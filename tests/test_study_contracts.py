import json

import pytest

from study_contracts import (
    AnalysisWindow,
    ClosureSpec,
    ScenarioSpec,
    load_scenario_spec,
    write_scenario_spec,
)


def _spec(**overrides):
    values = {
        "scenario_id": "normal-2025-09-16",
        "demand_build_id": "demand-a",
        "network_build_id": "network-b",
        "start_time": "2025-09-16T00:00:00",
        "end_time": "2025-09-17T00:00:00",
    }
    values.update(overrides)
    return ScenarioSpec(**values)


def test_scenario_round_trip_and_legacy_closure_names(tmp_path):
    spec = _spec(
        closures=(ClosureSpec.from_dict({
            "edge_id": "u_v_0",
            "begin": "2025-09-16T08:00:00",
            "end": "2025-09-16T10:00:00",
        }),),
        analysis_window=AnalysisWindow(
            warmup_s=900,
            measure_start="2025-09-16T08:15:00",
            measure_end="2025-09-16T09:45:00",
            drain_s=600,
        ),
    )
    path = tmp_path / "scenario.json"
    write_scenario_spec(path, spec)
    loaded = load_scenario_spec(path)
    assert loaded == spec
    assert json.loads(path.read_text())["schema_version"] == 1


def test_closure_must_fit_scenario_window():
    with pytest.raises(ValueError, match="fit within"):
        _spec(closures=(ClosureSpec(
            "u_v_0", "2025-09-17T00:00:00", "2025-09-17T00:15:00"),))


def test_variant_mapping_must_cover_each_seed_once():
    with pytest.raises(ValueError, match="cover every seed"):
        _spec(seed_set=(1, 2), demand_variant_mapping=((1, "q50"),))


def test_mixed_timezone_styles_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        _spec(end_time="2025-09-17T00:00:00Z")


def test_signal_plan_is_optional_for_normal_scenario():
    assert _spec().signal_plan_id is None
