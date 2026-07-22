"""One shared demand/simulation workspace, guarded across PROCESSES.

``sumo/`` and ``web/data/`` are a single mutable workspace: every demand
build, scenario run and closure search writes the same route files, the same
scenario directory and the same live-release products. serve.py has always
serialized its own jobs with a threading lock, which is enough while the
server is the only writer — it is not, and a pre-warm run that builds a whole
horizon in the background is precisely the case that breaks the assumption.
Without a cross-process guard, a "Byt dag" or a road closure clicked in the
browser can read a half-written ``calibrated.rou.xml``, or have its scenario
output reverted by the warm run's live-release restore.

So the workspace has ONE lock, held by whichever process is writing it:

* the lock is an ``flock`` on ``runs/.demand-workspace.lock``, so it is
  released by the operating system even if the holder is SIGKILLed;
* the holder writes who it is into the file, so a refusal can say what is
  running rather than just "busy";
* nothing blocks forever by default — the web app refuses immediately with
  the holder's name, and the pre-warm run waits between builds.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

LOCK_PATH = Path("runs") / ".demand-workspace.lock"


class WorkspaceBusy(RuntimeError):
    """Raised when another process holds the demand workspace."""

    def __init__(self, holder: dict[str, Any] | None) -> None:
        self.holder = holder or {}
        super().__init__(describe_holder(self.holder))


def describe_holder(holder: dict[str, Any] | None) -> str:
    if not holder:
        return "another process is using the demand workspace"
    owner = holder.get("owner") or "unknown job"
    pid = holder.get("pid")
    since = holder.get("since")
    running = ""
    if isinstance(since, (int, float)):
        running = f", running {int(max(0.0, time.time() - since)) // 60} min"
    return f"{owner} (pid {pid}{running}) is using the demand workspace"


class WorkspaceLock:
    """Exclusive hold on the shared demand workspace.

    Usable as a context manager. ``acquire`` never blocks longer than the
    timeout it is given, and reports the current holder when it gives up.
    """

    def __init__(self, owner: str, *, path: Path = LOCK_PATH,
                 timeout: float = 0.0) -> None:
        self.owner = owner
        self.path = Path(path)
        # How long ``with lock:`` waits before refusing. Zero means "refuse
        # immediately", which is what an interactive request wants; a batch
        # job that can afford to queue passes a real wait here.
        self.timeout = timeout
        self._handle = None

    def acquire(self, *, timeout: float = 0.0,
                poll_s: float = 1.0) -> bool:
        if self._handle is not None:
            raise RuntimeError("workspace lock already held by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    return False
                time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))
                continue
            break
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"owner": self.owner, "pid": os.getpid(),
                                 "since": time.time()}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return True

    def acquire_or_raise(self, *, timeout: float = 0.0) -> "WorkspaceLock":
        if not self.acquire(timeout=timeout):
            raise WorkspaceBusy(workspace_holder(self.path))
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @property
    def held(self) -> bool:
        return self._handle is not None

    def holder(self) -> dict[str, Any] | None:
        """Who holds THIS lock's file — asked of the lock, never of a path.

        Callers that ask ``workspace_holder()`` separately can drift onto a
        different file than the lock they just failed to take, and then
        report the wrong reason; going through the object cannot.
        """
        return workspace_holder(self.path)

    def holder_description(self) -> str:
        return describe_holder(self.holder())

    def __enter__(self) -> "WorkspaceLock":
        if not self.held:
            self.acquire_or_raise(timeout=self.timeout)
        return self

    def __exit__(self, *_exception) -> None:
        self.release()


def workspace_holder(path: Path = LOCK_PATH) -> dict[str, Any] | None:
    """Who holds the workspace right now, or None if it is free.

    Probed by trying to take the lock on a separate handle: an flock is tied
    to the open file description, so this correctly reports "held" even when
    the holder is this same process (that is exactly what the web app needs
    to tell the user, and never a reason to proceed).
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        handle = open(path, "r")
    except OSError:
        return None
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                payload = json.loads(handle.read() or "{}")
            except ValueError:
                payload = {}
            return payload if isinstance(payload, dict) else {}
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return None
    finally:
        handle.close()
