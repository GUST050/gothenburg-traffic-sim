"""Production resolver for the opt-in WindowCostIndex: load, else build, else fall back.

``traffic_sim.simulation.window_cost_index`` already gives a fail-closed,
atomic, identity-bound index primitive (``load_index``/``write_index``), and
``IndependentDailyCostSource`` already accepts one. What was missing for
production use is the seam ABOVE those: given a profile and a registration,
find (or build) the ONE index that matches their exact identity, without two
processes building the same thing twice, and without ever treating a
missing or stale index as a hit.

This module never computes a cost itself and never weakens
``load_index``'s own checks. It only decides WHERE to look, whether to
build, and what to do when nothing usable exists.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.simulation.window_cost_index import (
    WindowCostIndex,
    WindowCostIndexError,
    load_index,
)
from traffic_sim.storage.singleflight import content_key_lock

ROOT = Path(__file__).resolve().parents[2]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def identity_key(profile_content_key: str, registration_content_key: str) -> str:
    """A short, filesystem-safe key naming ONE (profile, registration) pair.

    Deliberately derived from the INPUTS, not the built index's own
    ``bound_identity`` -- that identity does not exist until an index has
    already been built once, so keying on it would make the very first
    build for a new month unable to name its own output directory.
    """
    return _digest({"profile_content_key": str(profile_content_key),
                    "registration_content_key": str(registration_content_key)}
                   )[:32]


def canonical_index_path(index_root: Path, key: str) -> Path:
    """Content-addressed location: sharded so one directory never floods."""
    return Path(index_root) / key[:2] / key / "window-cost-index.json"


class WindowCostIndexResolveResult:
    """What the resolver decided, and why -- always constructible, never raises."""

    def __init__(self, *, index: WindowCostIndex | None, source: str,
                reason: str | None, index_path: Path | None,
                build_attempted: bool, build_outcome: Mapping[str, Any] | None,
                lock_acquired: bool | None) -> None:
        self.index = index
        self.source = source  # "warm_hit" | "cold_build" | "fallback"
        self.reason = reason
        self.index_path = index_path
        self.build_attempted = build_attempted
        self.build_outcome = build_outcome
        self.lock_acquired = lock_acquired

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reason": self.reason,
            "index_path": str(self.index_path) if self.index_path else None,
            "build_attempted": self.build_attempted,
            "build_outcome": dict(self.build_outcome) if self.build_outcome else None,
            "lock_acquired": self.lock_acquired,
            "used_index": self.index is not None,
        }


def resolve_window_cost_index(
    *,
    profile_path: Path,
    registration_path: Path,
    index_root: Path,
    expected_identity: Mapping[str, Any] | None,
    expected_daily_units: int,
    expected_variant_records: int,
    auto_build: bool = False,
    evidence_id: str | None = None,
    lock_timeout_s: float = 600.0,
    guarded_build_script: Path | None = None,
) -> WindowCostIndexResolveResult:
    """Load a matching index if one exists; optionally build one; else fall back.

    Never raises. A caller that wants strict all-or-nothing behavior should
    check ``result.index is None`` and act on ``result.reason`` itself --
    keeping the fail-closed identity checks here and the "is a fallback
    acceptable right now" policy decision with the caller, which is the only
    place that knows whether this run is producing release evidence.
    """
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    registration = json.loads(Path(registration_path).read_text(encoding="utf-8"))
    profile_content_key = str(profile.get("content_key", ""))
    registration_content_key = str(registration.get("content_key", ""))
    if not profile_content_key or not registration_content_key:
        return WindowCostIndexResolveResult(
            index=None, source="fallback",
            reason="profile or registration has no content_key to key on",
            index_path=None, build_attempted=False, build_outcome=None,
            lock_acquired=None)

    key = identity_key(profile_content_key, registration_content_key)
    index_path = canonical_index_path(Path(index_root), key)

    def _try_load() -> WindowCostIndex | None:
        if not index_path.is_file():
            return None
        try:
            return load_index(
                index_path,
                expected_identity=expected_identity,
                expected_daily_units=expected_daily_units,
                expected_variant_records=expected_variant_records,
            )
        except WindowCostIndexError:
            return None

    warm = _try_load()
    if warm is not None:
        return WindowCostIndexResolveResult(
            index=warm, source="warm_hit", reason=None, index_path=index_path,
            build_attempted=False, build_outcome=None, lock_acquired=None)

    if not auto_build:
        return WindowCostIndexResolveResult(
            index=None, source="fallback",
            reason="no matching index and auto-build is not enabled",
            index_path=index_path, build_attempted=False, build_outcome=None,
            lock_acquired=None)

    # Single-flight: only one process builds this exact identity. A process
    # that cannot get the lock within the timeout falls back rather than
    # duplicating an expensive build or blocking the search indefinitely.
    lock_root = Path(index_root) / "locks"
    try:
        with content_key_lock(lock_root, key, timeout_s=lock_timeout_s):
            # Re-check: the lock holder that just finished may have already
            # built exactly what this process was about to build.
            warm = _try_load()
            if warm is not None:
                return WindowCostIndexResolveResult(
                    index=warm, source="warm_hit", reason=None,
                    index_path=index_path, build_attempted=False,
                    build_outcome=None, lock_acquired=True)
            outcome = _run_guarded_build(
                profile_path=profile_path, registration_path=registration_path,
                index_path=index_path, evidence_id=evidence_id or f"auto-{key}",
                guarded_build_script=guarded_build_script)
            built = _try_load()
            if built is not None:
                return WindowCostIndexResolveResult(
                    index=built, source="cold_build", reason=None,
                    index_path=index_path, build_attempted=True,
                    build_outcome=outcome, lock_acquired=True)
            return WindowCostIndexResolveResult(
                index=None, source="fallback",
                reason=f"guarded build did not produce a loadable index: "
                       f"{outcome.get('state')}",
                index_path=index_path, build_attempted=True,
                build_outcome=outcome, lock_acquired=True)
    except TimeoutError as error:
        return WindowCostIndexResolveResult(
            index=None, source="fallback", reason=str(error),
            index_path=index_path, build_attempted=False, build_outcome=None,
            lock_acquired=False)


def _run_guarded_build(
    *, profile_path: Path, registration_path: Path, index_path: Path,
    evidence_id: str, guarded_build_script: Path | None,
) -> dict[str, Any]:
    """Invoke the existing guarded builder as a subprocess and report back.

    Reuses ALL of its existing safety supervision (limits, drift checks,
    atomic publish, fail-closed stop) rather than re-implementing any of it
    here. A failure here is reported, never raised: the caller decides
    whether a failed auto-build should fall back or propagate.
    """
    script = guarded_build_script or (ROOT / "tools" / "guarded_wci_build.py")
    index_out = index_path.parent
    status_dir = index_out / "status"
    evidence_out = index_out / "guarded-build-evidence.json"
    build_evidence_out = index_out / "phase5-build-evidence.json"
    index_out.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(script), "--mode", "build",
        "--profile", str(profile_path),
        "--status-dir", str(status_dir),
        "--evidence-out", str(evidence_out),
        "--registration", str(registration_path),
        "--index-out", str(index_out),
        "--build-evidence-out", str(build_evidence_out),
        "--evidence-id", evidence_id,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    outcome: dict[str, Any] = {
        "returncode": result.returncode,
        "command": command,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    if evidence_out.is_file():
        try:
            published = json.loads(evidence_out.read_text(encoding="utf-8"))
            outcome["state"] = (published.get("outcome") or {}).get("state")
            outcome["evidence_path"] = str(evidence_out)
        except (OSError, ValueError):
            outcome["state"] = "unreadable_evidence"
    else:
        outcome["state"] = "no_evidence_published"
    return outcome
