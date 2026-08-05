"""Test-wide fixtures.

The demand workspace lock is real machine state: a horizon pre-warm or a
monthly search running beside the test suite genuinely holds it, and every
serve.py job would then be refused — 53 failures the first time this landed,
none of them about the code under test. Tests therefore get their own lock
file, and the tests that are ABOUT the lock pass an explicit path.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True, scope="session")
def _isolated_workspace_lock(tmp_path_factory):
    from traffic_sim.simulation import workspace

    original = workspace.LOCK_PATH
    workspace.LOCK_PATH = tmp_path_factory.mktemp("workspace") / "lock"
    yield
    workspace.LOCK_PATH = original
