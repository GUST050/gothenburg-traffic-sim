"""Reproducibility and fail-closed guards for the Stage 4 topology screen."""

import inspect

import pytest

from tools import screen_closure_survivability as screen


def test_missing_documented_edge_fails_the_live_self_check():
    with pytest.raises(SystemExit, match="absent"):
        screen._known_case({}, {})


def test_documented_edge_that_no_longer_severs_fails(monkeypatch):
    monkeypatch.setattr(screen.cs, "successors_cut_off",
                        lambda edge, collapsed, predecessors: ())
    with pytest.raises(SystemExit, match="cuts off nothing"):
        screen._known_case({screen.KNOWN_SEVERING_EDGE: ["next"]}, {})


def test_content_key_covers_the_report_body():
    report = {"schema_version": 1, "value": 3}
    report["content_key"] = screen._content_key(report)
    assert screen._content_key(report) == report["content_key"]
    report["value"] = 4
    assert screen._content_key(report) != report["content_key"]


def test_screen_fingerprints_its_executed_implementations():
    source = inspect.getsource(screen.screen)
    assert '"run_scenario.py"' in source
    assert '"tools/freeze_heldout_v10.py"' in source
    assert '"tools/screen_closure_survivability.py"' in source
    assert '"traffic_sim/simulation/closure_survivability.py"' in source


def test_verify_is_read_only_mode():
    args = screen.parse_args(["--verify"])
    assert args.verify is True
    assert args.stdout is False
