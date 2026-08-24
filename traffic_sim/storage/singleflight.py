"""Small cross-process single-flight primitive for content-addressed work."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
import sys
import time
from typing import Iterator


_SAFE_KEY = re.compile(r"[a-zA-Z0-9._-]{1,128}\Z")


@contextmanager
def content_key_lock(
    root: Path,
    key: str,
    *,
    timeout_s: float = 600.0,
    poll_s: float = 0.25,
) -> Iterator[None]:
    """Allow one producer for ``key`` while other processes wait.

    ``flock`` is released automatically when a worker exits or is killed. The
    lock file is deliberately retained: it is a harmless inode, not evidence
    that work is still running. Contention is reported once and bounded so a
    hung producer becomes a visible job failure rather than an infinite wait.
    """
    if not isinstance(key, str) or not _SAFE_KEY.fullmatch(key):
        raise ValueError("single-flight content key is not filesystem-safe")
    if timeout_s <= 0 or poll_s <= 0:
        raise ValueError("single-flight timeout and poll interval must be positive")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f".{key}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout_s
        waiting_reported = False
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waiting_reported:
                    print(
                        f"waiting for another producer for content key {key}",
                        file=sys.stderr,
                    )
                    waiting_reported = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "timed out waiting for another producer for content "
                        f"key {key} after {timeout_s:.1f}s"
                    )
                time.sleep(min(poll_s, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
