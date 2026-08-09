"""Small contract tests for the paired picker diagnostic runner."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parent.parent / "tools/run_loso_picker_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("run_loso_picker_diagnostics", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_station_result_keeps_ratio_and_diagnostic_hash():
    report = {"stations": {"107": {
        "edges": {"station_total": {
            "ratio": 1.2, "geh_ok_pct": 50.0,
            "measured_total": 10, "simulated_total": 12}},
        "held_edge_assignment_ceiling": {"missing_edges": []},
        "picker_diagnostics": {"sha256": "abc"},
    }}}

    assert runner.station_result(report, "107") == {
        "evaluation": "station_total",
        "ratio": 1.2,
        "geh_ok_pct": 50.0,
        "measured_total": 10,
        "simulated_total": 12,
        "held_edge_assignment_ceiling": {"missing_edges": []},
        "picker_diagnostics_sha256": "abc",
    }


def test_station_result_supports_small_publication_treatment_run():
    report = {"stations": {"134": {
        "edges": {"edge": {
            "ratio": 1.1, "geh_ok_pct": 75.0,
            "measured_total": 10, "simulated_total": 11}},
        "held_edge_assignment_ceiling": {
            "missing_edges": [], "integer_publication_enforced": True},
    }}}

    result = runner.station_result(report, "134")

    assert "picker_diagnostics_sha256" not in result
    assert result["held_edge_assignment_ceiling"][
        "integer_publication_enforced"] is True
