"""H2 (IMPROVEMENT_PLAN.md; audit P1-3): the PFE benchmark fingerprint must not drift.

Re-runs the seeded fixture through the deployed solve path and compares
against the checked-in baseline. A failure here means a solver/report
change ALTERED RESULTS — if intentional, review the diff and re-run
`python3 tools/pfe_benchmark.py --write-baseline`; never loosen the
tolerances to make it pass.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools import pfe_benchmark as bench


@pytest.fixture(scope="module")
def result():
    return bench.run_benchmark()


@pytest.fixture(scope="module")
def baseline():
    if not bench.BASELINE_PATH.exists():
        pytest.skip("no checked-in baseline — run tools/pfe_benchmark.py "
                    "--write-baseline")
    return json.loads(bench.BASELINE_PATH.read_text())


class TestBenchmarkFingerprint:
    def test_exact_deterministic_fields(self, result, baseline):
        # Same seed, same code ⇒ these are exactly reproducible.
        for key in ("vehicles", "geh_pct", "infeasible", "rungs",
                    "bound_violations",
                    "quarters_with_incompatible_purposes"):
            assert result[key] == baseline[key], (
                f"{key}: {result[key]} != baseline {baseline[key]} — a "
                "solver/report change altered results; review, then "
                "re-baseline deliberately if intended")

    def test_near_group_share_within_tolerance(self, result, baseline):
        # Small float/ordering jitter tolerated; a cap that stopped
        # binding would move this by far more than 2 percentage points.
        assert abs(result["near_group_share"]
                   - baseline["near_group_share"]) < 0.02

    def test_purpose_mix_within_tolerance(self, result, baseline):
        for p, share in baseline["purpose_mix"].items():
            assert abs(result["purpose_mix"].get(p, 0.0) - share) < 0.02, p

    def test_fixture_actually_exercises_the_guard(self, result):
        # Planted demand puts ~38% on the capped group; the guard must
        # hold the calibrated share clearly below that AND below the cap.
        assert result["near_group_share"] < 0.25
        assert result["geh_pct"] == 100.0
