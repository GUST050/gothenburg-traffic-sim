"""Run registry — IMPROVEMENT_PLAN.md Phase E1 (2026-07-13).

Every demand build and scenario run gets an immutable directory under
`runs/<run_id>/` with a manifest written BEFORE the work starts, so a
completed-looking artifact can never be separated from how it was made
(audit P0-1: shared mutable output dirs; the 2026-07-13 fritid diagnosis
spent an hour on artifacts that turned out to predate the code being
debugged — a manifest makes that impossible).

Design:
- `start_run(kind, inputs, argv)` writes `runs/<id>/manifest.json` with
  status "running" and returns a `Run`.
- `run.add_output(path)` copies a finished product into the run directory
  (products keep being written to their legacy shared paths too — full
  path redirection is deliberately deferred to the H1 module split; this
  registry layer must not destabilize the pipeline it audits).
- `run.finish("succeeded" | "failed", error=...)` stamps the manifest and,
  on success, atomically flips `runs/latest_<kind>.json` to point at this
  run. The pointer is the ONLY mutable file; everything in `runs/<id>/`
  is written once.
- All manifest writes are tmp+`os.replace` atomic.

run_id = <kind>-<UTC timestamp>-<input digest[:8]>-<4 hex random>, so two
runs with identical inputs are still distinct directories.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = Path("runs")

_SCHEMA_VERSION = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty() -> bool | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip()) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _input_digest(inputs: dict) -> str:
    return hashlib.sha256(
        json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


class Run:
    """One immutable run directory + its manifest."""

    def __init__(self, run_id: str, directory: Path, manifest: dict):
        self.run_id = run_id
        self.dir = directory
        self.manifest = manifest

    def _flush(self) -> None:
        _atomic_write_json(self.dir / "manifest.json", self.manifest)

    def add_output(self, source: Path, name: str | None = None) -> Path | None:
        """Copy a finished product into the run directory and record it."""
        source = Path(source)
        if not source.exists():
            self.manifest.setdefault("missing_outputs", []).append(str(source))
            self._flush()
            return None
        dest = self.dir / (name or source.name)
        shutil.copy2(source, dest)
        self.manifest.setdefault("outputs", []).append({
            "name": dest.name,
            "source_path": str(source),
            "bytes": dest.stat().st_size,
            "sha256": _sha256_file(dest),
        })
        self._flush()
        return dest

    def record(self, key: str, value) -> None:
        """Attach a JSON-serializable fact (metrics, gate results, timings)."""
        self.manifest.setdefault("recorded", {})[key] = value
        self._flush()

    def finish(self, status: str, error: str | None = None) -> None:
        assert status in ("succeeded", "failed", "cancelled"), status
        self.manifest["status"] = status
        self.manifest["finished_at"] = _now()
        if error:
            self.manifest["error"] = error
        self._flush()
        if status == "succeeded":
            _atomic_write_json(
                RUNS_DIR / f"latest_{self.manifest['kind']}.json",
                {"run_id": self.run_id, "dir": str(self.dir),
                 "finished_at": self.manifest["finished_at"]})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_run(kind: str, inputs: dict, argv: list[str] | None = None) -> Run:
    """Create runs/<id>/ and write its manifest before any work happens."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = _input_digest(inputs)
    run_id = f"{kind}-{stamp}-{digest[:8]}-{secrets.token_hex(2)}"
    directory = RUNS_DIR / run_id
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "kind": kind,
        "status": "running",
        "created_at": _now(),
        "argv": list(argv if argv is not None else sys.argv),
        "inputs": inputs,
        "input_digest": digest,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "user": getpass.getuser(),
        "pid": os.getpid(),
    }
    run = Run(run_id, directory, manifest)
    run._flush()
    return run


def latest(kind: str) -> dict | None:
    """The pointer for the last successful run of this kind, or None."""
    path = RUNS_DIR / f"latest_{kind}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def load_manifest(run_id: str) -> dict | None:
    path = RUNS_DIR / run_id / "manifest.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
