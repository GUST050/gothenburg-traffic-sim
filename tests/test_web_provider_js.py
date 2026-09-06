"""Run the provider seam's executable JavaScript checks from the suite.

The web app has no JS test runner, and every UI invariant in this suite is
otherwise pinned by asserting on SOURCE STRINGS. That could not have caught
the defect these checks exist for: the renderer asked the right question of
the wrong provider, so the code all the string assertions looked for was
present and correct while the behaviour was wrong. Executing the real
provider.js is the only thing that distinguishes those two states.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "provider_coverage.test.js"

#: Skipping is right on a contributor's machine without node and wrong in CI,
#: where a skipped executable check reads exactly like a passing one. The
#: workflow sets CI=true so the skip becomes a failure there.
_NODE = shutil.which("node")
_MAY_SKIP = _NODE is None and not os.environ.get("CI")


def test_the_harness_is_present():
    """A skipped node test must never be indistinguishable from a missing one."""
    assert HARNESS.is_file()


@pytest.mark.skipif(_MAY_SKIP, reason="node is not installed on this machine")
def test_provider_coverage_predicate_behaves():
    assert _NODE is not None, (
        "node is required wherever CI is set, so these checks cannot be "
        "silently skipped in the one place their absence would not be noticed")
    result = subprocess.run(
        ["node", str(HARNESS)], cwd=ROOT, capture_output=True, text=True,
        timeout=120, check=False)

    assert result.returncode == 0, (
        "web/provider.js failed its executable checks:\n"
        f"{result.stdout}\n{result.stderr}")
    assert "passed" in result.stdout
