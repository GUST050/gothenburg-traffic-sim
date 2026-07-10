#!/usr/bin/env python3
"""Run one B0 command in its own process group with an explicit timeout."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=int, required=True, help="seconds")
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    if not args.command or args.command[0] != "--":
        p.error("pass the command after --")
    command = args.command[1:]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with args.log.open("w") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            elapsed = time.monotonic() - started
            print(f"TIMEOUT after {elapsed:.1f}s; process group terminated", file=sys.stderr)
            sys.exit(124)
    elapsed = time.monotonic() - started
    print(f"exit={code}; elapsed={elapsed:.3f}s; log={args.log}")
    sys.exit(code)


if __name__ == "__main__":
    main()
