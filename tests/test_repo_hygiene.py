"""Contract tests for the repository's large-artifact policy."""

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "tools" / "repo_hygiene_allowlist.json"


def test_repository_hygiene_check_passes_for_current_tree():
    completed = subprocess.run(
        [sys.executable, "tools/check_repo_hygiene.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "3 pinned large files" in completed.stdout


def test_every_large_file_exception_has_reviewer_context():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy["schema_version"] == 1
    assert policy["max_tracked_file_bytes"] == 5 * 1024 * 1024
    for relative, record in policy["legacy_large_files"].items():
        assert not Path(relative).is_absolute()
        assert record["bytes"] >= policy["max_tracked_file_bytes"]
        assert len(record["sha256"]) == 64
        assert len(record["reason"]) >= 20


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _run_check(root: Path, policy: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "check_repo_hygiene.py"),
            "--root",
            str(root),
            "--policy",
            str(policy),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_new_large_file_and_allowlisted_drift_fail_closed(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    pinned = repository / "pinned.bin"
    pinned.write_bytes(b"pinned-content")
    _git(repository, "add", "pinned.bin")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_tracked_file_bytes": 10,
                "legacy_large_files": {
                    "pinned.bin": {
                        "bytes": pinned.stat().st_size,
                        "sha256": hashlib.sha256(pinned.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    )
    assert _run_check(repository, policy).returncode == 0

    pinned.write_bytes(b"small")
    drift = _run_check(repository, policy)
    assert drift.returncode == 1
    assert "allowlisted artifact size drift" in drift.stderr

    pinned.write_bytes(b"pinned-content")
    extra = repository / "new.bin"
    extra.write_bytes(b"unapproved-large")
    new_file = _run_check(repository, policy)
    assert new_file.returncode == 1
    assert "new large file: new.bin" in new_file.stderr
