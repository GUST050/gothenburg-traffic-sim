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
HARNESSES = [
    ROOT / "tests" / "js" / "controls_keyboard.test.js",
    ROOT / "tests" / "js" / "provider_coverage.test.js",
    ROOT / "tests" / "js" / "polling.test.js",
    ROOT / "tests" / "js" / "animation.test.js",
    ROOT / "tests" / "js" / "text.test.js",
    ROOT / "tests" / "js" / "monthly_validation.test.js",
    ROOT / "tests" / "js" / "delay_profile.test.js",
]

#: Skipping is right on a contributor's machine without node and wrong in CI,
#: where a skipped executable check reads exactly like a passing one. The
#: workflow sets CI=true so the skip becomes a failure there.
_NODE = shutil.which("node")
_MAY_SKIP = _NODE is None and not os.environ.get("CI")


@pytest.mark.parametrize("harness", HARNESSES)
def test_the_harness_is_present(harness):
    """A skipped node test must never be indistinguishable from a missing one."""
    assert harness.is_file()


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.skipif(_MAY_SKIP, reason="node is not installed on this machine")
def test_web_javascript_behaves(harness):
    assert _NODE is not None, (
        "node is required wherever CI is set, so these checks cannot be "
        "silently skipped in the one place their absence would not be noticed")
    result = subprocess.run(
        ["node", str(harness)], cwd=ROOT, capture_output=True, text=True,
        timeout=120, check=False)

    assert result.returncode == 0, (
        f"{harness.name} failed its executable checks:\n"
        f"{result.stdout}\n{result.stderr}")
    assert "passed" in result.stdout
