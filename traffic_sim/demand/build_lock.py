"""Inter-process ownership for builds that mutate shared live demand files."""

from __future__ import annotations

import contextvars
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "runs" / ".demand-build.lock"
PARENT_LOCK_ENV = "GS_PROJECT_DEMAND_BUILD_LOCK_HELD_BY_PARENT"
_DEPTH = contextvars.ContextVar("demand_build_lock_depth", default=0)


@contextmanager
def demand_build_lock():
    """Serialize all shared-output demand builds, reentrantly per context."""
    depth = _DEPTH.get()
    if depth:
        token = _DEPTH.set(depth + 1)
        try:
            yield
        finally:
            _DEPTH.reset(token)
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        token = _DEPTH.set(1)
        try:
            yield
        finally:
            _DEPTH.reset(token)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def child_environment() -> dict[str, str]:
    """Return an environment proving the caller retains the build lock."""
    if _DEPTH.get() < 1:
        raise RuntimeError("cannot delegate a demand build without owning its lock")
    environment = dict(os.environ)
    environment[PARENT_LOCK_ENV] = "1"
    return environment


def parent_holds_lock() -> bool:
    """Whether this process was launched by an owner retaining the file lock."""
    return os.environ.get(PARENT_LOCK_ENV) == "1"
