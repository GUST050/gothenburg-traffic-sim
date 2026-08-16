"""Minimal reproducer for the `threads=1` integer-repair failure.

`traffic_sim/demand/pfe.py::repair_integer_bounds` passes the undocumented
HiGHS option ``{"threads": 1}`` through SciPy's ``milp``. That call sometimes
returns ``status=4`` with ``"(HiGHS Status 0: Not Set)"`` before solving, and
the repository currently attributes this to SciPy >= 1.17 and pins
``scipy>=1.11,<1.17`` in `requirements.txt`, CI and `ARCHITECTURE.md`.

This script tests that attribution with a two-variable model, so the result
does not depend on the production problem. It varies ONE thing: whether the
``threads=1`` solve is the first HiGHS solve in the process.

Measured 2026-08-16 on scipy 1.17.1 AND scipy 1.16.3 (inside the pin), same
result on both:

    threads=1 first   -> 0 (Optimal), and every later solve also succeeds
    default first     -> 4 "(HiGHS Status 0: Not Set)", permanently

So the failure is ORDER-dependent, not version-dependent: HiGHS starts a global
scheduler on the first solve, and a later solve asking for a different thread
count in the same process is refused. A `fork` pool inherits that state from
its parent, which is how the production publisher can hit it.

This does NOT by itself justify removing the pin — it shows the pin does not
explain this failure mode. Work package A2 owns the real root-cause statement
against the production model.

Usage:
    python3 tools/reproduce_highs_thread_option.py             # both orders
    python3 tools/reproduce_highs_thread_option.py --json      # machine record
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import warnings

CHILD_SOURCE = """
import sys, warnings
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
warnings.simplefilter("ignore")

def solve(opts):
    r = milp(c=np.array([1.0, 1.0]),
             constraints=LinearConstraint(np.array([[1.0, 1.0]]), 2, 2),
             bounds=Bounds(0, 10), integrality=np.ones(2), options=opts)
    return int(r.status), str(r.message)

threads = {"time_limit": 20.0, "threads": 1}
default = {"time_limit": 20.0}
seq = [threads, default, threads] if sys.argv[1] == "threads_first" else \\
      [default, threads, threads]
import json as _json
print(_json.dumps([solve(o) for o in seq]))
"""


def run_order(order: str) -> list:
    out = subprocess.run([sys.executable, "-c", CHILD_SOURCE, order],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="emit a JSON record")
    args = p.parse_args()

    import scipy
    record = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "scipy": scipy.__version__,
        "orders": {order: run_order(order)
                   for order in ("threads_first", "default_first")},
    }
    tf = record["orders"]["threads_first"]
    df = record["orders"]["default_first"]
    record["threads_first_all_ok"] = all(s == 0 for s, _ in tf)
    record["default_first_blocks_threads"] = df[0][0] == 0 and df[1][0] != 0
    record["conclusion"] = (
        "order-dependent: the first HiGHS solve fixes the process thread count"
        if record["threads_first_all_ok"] and record["default_first_blocks_threads"]
        else "not reproduced in this environment")

    if args.json:
        print(json.dumps(record, indent=1))
        return

    print(f"python {record['python']}  scipy {record['scipy']}")
    for order, results in record["orders"].items():
        print(f"\n{order}:")
        for i, (status, message) in enumerate(results, start=1):
            print(f"  solve {i}: status={status} {message[:52]}")
    print(f"\n=> {record['conclusion']}")


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        main()
