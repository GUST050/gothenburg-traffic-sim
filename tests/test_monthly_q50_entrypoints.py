"""The user-facing monthly defaults explicitly select the q50-only contract."""
import json
from pathlib import Path

import run_monthly_closure_search as cli
from traffic_sim.simulation.monthly_search import MonthlySearchPolicy


def test_cli_defaults_to_q50_without_claiming_release_approval(monkeypatch):
    monkeypatch.setattr("sys.argv", ["monthly", "--spec", "spec.json",
                                    "--baseline-trip-duration-p99-s", "3600"])
    args = cli.parse_args()
    policy = MonthlySearchPolicy.from_dict(json.loads(args.policy.read_text()))
    assert policy.pilot.variants == policy.finalist.variants == ("q50",)
    assert policy.status == "provisional"
    legacy = MonthlySearchPolicy.from_dict(json.loads(
        (Path(__file__).resolve().parents[1] / "validation" /
         "monthly_search_policy_v1.json").read_text()))
    assert policy.content_key != legacy.content_key
    assert set(legacy.pilot.variants) == {"q10", "q50", "q90"}


def test_cli_can_explicitly_replay_a_historical_policy(monkeypatch):
    monkeypatch.setattr("sys.argv", ["monthly", "--spec", "spec.json",
                                    "--policy", "historical.json",
                                    "--baseline-trip-duration-p99-s", "3600"])
    assert cli.parse_args().policy == Path("historical.json")
