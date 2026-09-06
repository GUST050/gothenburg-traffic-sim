"""User-visible start position for saved scenario playback."""

from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).parent.parent
PROVIDER_JS = ROOT / "web" / "provider.js"


def scenario_initial_qi(provider: dict) -> int:
    """Evaluate the browser helper against a complete controlled provider."""
    script = f"""
const fs = require('fs');
const vm = require('vm');
const context = {{ console }};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync({json.dumps(str(PROVIDER_JS))}, 'utf8') +
    {json.dumps(chr(10) + 'this.scenarioInitialQI = scenarioInitialQI;')},
  context
);
process.stdout.write(String(context.scenarioInitialQI({json.dumps(provider)})));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout)


def test_baseline_opens_at_its_busiest_departure_quarter():
    provider = {
        "intervalMinutes": 15,
        "numQuarters": 4,
        "closures": [],
        "agentDemand": {
            "purpose_counts_by_quarter": [
                {"arbete": 2},
                {"arbete": 3, "service": 4},
                {"arbete": 1, "service": 2},
                {},
            ],
        },
    }

    assert scenario_initial_qi(provider) == 1


def test_closure_opens_when_the_first_closure_begins():
    provider = {
        "intervalMinutes": 15,
        "numQuarters": 96,
        "closures": [
            {"edge_id": "later", "begin_s": 32400, "end_s": 36000},
            {"edge_id": "first", "begin_s": 28800, "end_s": 30600},
        ],
        "agentDemand": {"purpose_counts_by_quarter": [{"arbete": 99}]},
    }

    assert scenario_initial_qi(provider) == 32


def test_old_scenario_without_demand_or_closure_still_opens_at_start():
    assert scenario_initial_qi({"intervalMinutes": 15, "numQuarters": 96}) == 0


def test_refresh_discards_both_the_old_scenario_and_its_trajectory():
    """A rebuilt day must not pair new sensor flows with old moving cars."""
    script = f"""
const fs = require('fs');
const vm = require('vm');
const context = {{ console }};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync({json.dumps(str(PROVIDER_JS))}, 'utf8') + `
    const providers = {{
      'baseline.json': {{ trajectories: 'baseline_traj.json', generation: 'old' }},
      'close.json': {{ trajectories: 'close_traj.json', generation: 'keep' }},
    }};
    const trajectories = {{
      'baseline_traj.json': {{ generation: 'old' }},
      'close_traj.json': {{ generation: 'keep' }},
    }};
    const invalidate = typeof invalidateScenarioAssetCache === 'function'
      ? invalidateScenarioAssetCache : () => {{}};
    invalidate('baseline.json', providers, trajectories);
    this.cacheResult = {{ providers, trajectories }};
  `,
  context
);
process.stdout.write(JSON.stringify(context.cacheResult));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "providers": {
            "close.json": {
                "trajectories": "close_traj.json",
                "generation": "keep",
            },
        },
        "trajectories": {
            "close_traj.json": {"generation": "keep"},
        },
    }
