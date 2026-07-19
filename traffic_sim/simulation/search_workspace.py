"""Isolated, integrity-checked workspaces for recurring closure searches.

The monthly optimizer must never write candidates into the active scenario
directory while it is still working.  A search therefore owns exactly one
directory under ``runs/closure-search/<search_id>``.  Inputs and published
artifacts are immutable; only ``manifest.json`` and the disposable
``scratch/`` directory change while the search is running.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from traffic_sim.core.contracts import (
    ClosureSearchSpec,
    load_closure_search_spec,
    write_closure_search_spec,
)
from traffic_sim.core.fingerprint import git_commit, sha256_file


SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("runs") / "closure-search"
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _safe_artifact_name(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("artifact name must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("artifact name must stay inside the workspace")
    return path


def _file_record(path: Path, *, relative_to: Path) -> dict[str, Any]:
    digest = sha256_file(path)
    if digest is None:
        raise FileNotFoundError(path)
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


@dataclass
class SearchWorkspace:
    """One exclusive closure-search directory and its mutable run manifest."""

    directory: Path
    manifest: dict[str, Any]

    @property
    def spec_path(self) -> Path:
        return self.directory / "input" / "closure_search.json"

    @property
    def scratch_dir(self) -> Path:
        return self.directory / "scratch"

    @property
    def artifacts_dir(self) -> Path:
        return self.directory / "artifacts"

    @property
    def status(self) -> str:
        return str(self.manifest.get("status", ""))

    def _require_running(self) -> None:
        if self.status != "running":
            raise RuntimeError(
                f"closure-search workspace is already {self.status!r}")

    def _flush(self) -> None:
        _atomic_json(self.directory / "manifest.json", self.manifest)

    def update_progress(
        self,
        phase: str,
        *,
        completed: int = 0,
        total: int | None = None,
        error: str | None = None,
    ) -> None:
        """Persist a resumable progress pointer while the workspace runs."""
        self._require_running()
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError("workspace progress phase must be non-empty")
        if (
            isinstance(completed, bool)
            or not isinstance(completed, int)
            or completed < 0
        ):
            raise ValueError("workspace progress completed must be non-negative")
        if (
            total is not None
            and (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < completed
            )
        ):
            raise ValueError(
                "workspace progress total must be at least completed"
            )
        progress = {
            "phase": phase,
            "completed": completed,
            "total": total,
            "updated_at": _now(),
        }
        if error is not None:
            progress["last_error"] = str(error)
        self.manifest["progress"] = progress
        self._flush()

    def publish_artifact(
        self,
        source: Path,
        name: str,
        *,
        kind: str,
        provenance: Mapping[str, Any],
    ) -> Path:
        """Copy one completed artifact into immutable workspace storage."""
        self._require_running()
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("artifact kind must be non-empty")
        if not isinstance(provenance, Mapping) or not provenance:
            raise ValueError("artifact provenance must be explicit")
        # Fail before touching the destination if provenance is not
        # serializable.  A manifest must never lag behind a copied artifact.
        json.dumps(provenance, sort_keys=True)
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        relative = _safe_artifact_name(name)
        destination = self.artifacts_dir.joinpath(*relative.parts)
        if destination.exists():
            raise FileExistsError(
                f"closure-search artifact already exists: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        record = {
            **_file_record(destination, relative_to=self.directory),
            "kind": kind,
            "provenance": dict(provenance),
        }
        self.manifest.setdefault("artifacts", []).append(record)
        self._flush()
        return destination

    def finish(
        self,
        status: str,
        *,
        error: str | None = None,
        keep_scratch: bool = False,
    ) -> None:
        """Finalize the workspace without touching any active release."""
        self._require_running()
        if status not in _TERMINAL_STATUSES:
            raise ValueError(
                f"workspace status must be one of {sorted(_TERMINAL_STATUSES)}")
        if status == "succeeded":
            errors = verify_search_workspace(self)
            if errors:
                raise ValueError(
                    "cannot finish invalid closure-search workspace: "
                    + "; ".join(errors))
        # Cancellation always removes only this search's disposable scratch;
        # it must not leave a large half-run behind or touch any artifact.
        preserve = bool(status == "failed"
                        or (status == "succeeded" and keep_scratch))
        if not preserve and self.scratch_dir.exists():
            shutil.rmtree(self.scratch_dir)
        self.manifest.update({
            "status": status,
            "finished_at": _now(),
            "scratch": "preserved" if preserve else "removed",
        })
        if error:
            self.manifest["error"] = str(error)
        self._flush()


def create_search_workspace(
    spec: ClosureSearchSpec,
    *,
    root: Path = DEFAULT_ROOT,
) -> SearchWorkspace:
    """Create a new workspace; a reused search ID always fails closed."""
    spec = ClosureSearchSpec.from_dict(spec.to_dict())
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / spec.search_id
    directory.mkdir(parents=False, exist_ok=False)
    try:
        (directory / "input").mkdir()
        (directory / "artifacts").mkdir()
        (directory / "scratch").mkdir()
        write_closure_search_spec(
            directory / "input" / "closure_search.json", spec)
        spec_record = _file_record(
            directory / "input" / "closure_search.json",
            relative_to=directory,
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "closure_search_workspace",
            "search_id": spec.search_id,
            "search_content_key": spec.content_key,
            "status": "running",
            "created_at": _now(),
            "git_commit": git_commit(),
            "input": spec_record,
            "artifacts": [],
            "scratch": "active",
        }
        workspace = SearchWorkspace(directory=directory, manifest=manifest)
        workspace._flush()
        return workspace
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def load_search_workspace(
    directory: Path,
    *,
    verify: bool = True,
) -> SearchWorkspace:
    directory = Path(directory)
    try:
        manifest = json.loads(
            (directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("closure-search workspace manifest is unreadable") from exc
    workspace = SearchWorkspace(directory=directory, manifest=manifest)
    if verify:
        errors = verify_search_workspace(workspace)
        if errors:
            raise ValueError(
                "closure-search workspace integrity failed: "
                + "; ".join(errors))
    return workspace


def open_search_workspace(
    spec: ClosureSearchSpec,
    *,
    root: Path = DEFAULT_ROOT,
) -> tuple[SearchWorkspace, bool]:
    """Create or resume the exact workspace.

    Returns ``(workspace, created)``. A running workspace is resumable and a
    succeeded workspace is idempotently readable. Failed or cancelled work
    must use a new search ID so old diagnostic evidence cannot be silently
    reinterpreted.
    """
    spec = ClosureSearchSpec.from_dict(spec.to_dict())
    directory = Path(root) / spec.search_id
    if not directory.exists():
        return create_search_workspace(spec, root=root), True
    workspace = load_search_workspace(directory)
    stored = load_closure_search_spec(workspace.spec_path)
    if (
        stored.search_id != spec.search_id
        or stored.content_key != spec.content_key
    ):
        raise ValueError(
            "existing closure-search workspace belongs to different input"
        )
    if workspace.status in {"failed", "cancelled"}:
        raise RuntimeError(
            f"closure-search workspace is {workspace.status}; use a new search_id"
        )
    if workspace.status == "running" and not workspace.scratch_dir.is_dir():
        raise ValueError("running closure-search workspace has no scratch directory")
    return workspace, False


def verify_search_workspace(workspace: SearchWorkspace) -> list[str]:
    """Return integrity/provenance errors without modifying the workspace."""
    errors: list[str] = []
    manifest = workspace.manifest
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported workspace schema")
    if manifest.get("kind") != "closure_search_workspace":
        errors.append("workspace kind is invalid")
    if manifest.get("status") not in {"running", *_TERMINAL_STATUSES}:
        errors.append("workspace status is invalid")
    try:
        spec = load_closure_search_spec(workspace.spec_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"closure-search input is invalid: {exc}")
        spec = None
    if spec is not None:
        if manifest.get("search_id") != spec.search_id:
            errors.append("workspace search_id does not match its input")
        if manifest.get("search_content_key") != spec.content_key:
            errors.append("workspace search content key does not match its input")
    input_record = manifest.get("input")
    if not isinstance(input_record, dict):
        errors.append("workspace input record is missing")
    else:
        digest = sha256_file(workspace.spec_path)
        if (
            digest is None
            or input_record.get("path") != "input/closure_search.json"
            or input_record.get("sha256") != digest
            or input_record.get("bytes") != (
                workspace.spec_path.stat().st_size
                if workspace.spec_path.is_file() else None
            )
        ):
            errors.append("workspace input digest is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("workspace artifact ledger is invalid")
        artifacts = []
    seen: set[str] = set()
    for record in artifacts:
        if not isinstance(record, dict):
            errors.append("workspace artifact record is invalid")
            continue
        relative = record.get("path")
        try:
            safe = _safe_artifact_name(relative)
        except (TypeError, ValueError):
            errors.append("workspace artifact path is unsafe")
            continue
        label = safe.as_posix()
        if label in seen:
            errors.append(f"workspace artifact is duplicated: {label}")
            continue
        seen.add(label)
        path = workspace.directory.joinpath(*safe.parts)
        digest = sha256_file(path)
        if (
            digest is None
            or digest != record.get("sha256")
            or path.stat().st_size != record.get("bytes")
        ):
            errors.append(f"workspace artifact digest is invalid: {label}")
        if not record.get("kind") or not isinstance(
            record.get("provenance"), dict
        ) or not record["provenance"]:
            errors.append(f"workspace artifact provenance is missing: {label}")
    if workspace.artifacts_dir.is_dir():
        actual = {
            path.relative_to(workspace.directory).as_posix()
            for path in workspace.artifacts_dir.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        }
        for label in sorted(actual - seen):
            errors.append(f"workspace artifact is not in the ledger: {label}")
    return errors
