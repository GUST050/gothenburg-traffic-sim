"""Compiled IPF inner loop — the deliberate JIT escalation (2026-07-16).

Phase I (2026-07-14) measured 91-96% of demand-build time inside
solve_interval_entropy's pure-Python Gauss-Seidel loops and closed with:
"no safe lever short of a JIT dependency — a deliberate decision for that
day". Gustav asked for a severe speedup ("sekunder istället för 20
minuter"), so this is that decision, taken with the safety net the project
built for exactly this moment (the H2 benchmark fingerprint + a bitwise
comparison harness, tools/verify_pfe_kernel.py).

BIT-IDENTITY CONTRACT: this kernel performs the SAME floating-point
operations in the SAME order as the pure-Python loop in
pfe.solve_interval_entropy — sequential per-item sums in index order,
the same factor guards, the same sample-after-level-2 averaging.
IEEE-754 doubles with identical op order give identical bits, so the
kernel's output must equal the Python path EXACTLY (not approximately);
tools/verify_pfe_kernel.py asserts equality on the real network across
all 96 quarters, and tests/test_pfe_kernel.py pins it in the suite.
fastmath stays OFF — it licenses reassociation, which breaks the
contract. Any future edit to the Python loop MUST be mirrored here and
re-verified bitwise.

The kernel is optional: pfe.py falls back to the pure-Python loop when
numba is not importable, and the environment variable PFE_PURE=1 forces
the fallback (used by the verification harness to produce the reference).
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
    NUMBA_AVAILABLE = True
except ImportError:               # pragma: no cover - exercised without numba
    NUMBA_AVAILABLE = False

    def njit(*args, **kwargs):    # type: ignore[misc]
        def deco(f):
            return f
        return deco(args[0]) if args and callable(args[0]) else deco


@njit(cache=True, fastmath=False, nogil=True)
def ipf_iterations(x: np.ndarray,
                   m_indptr: np.ndarray, m_idx: np.ndarray,
                   m_target: np.ndarray,
                   b_indptr: np.ndarray, b_idx: np.ndarray,
                   b_lo: np.ndarray, b_hi: np.ndarray,
                   p_indptr: np.ndarray, p_idx: np.ndarray,
                   p_target: np.ndarray, p_weight: np.ndarray,
                   burn_in: int, max_iterations: int) -> np.ndarray:
    """The 200-iteration Gauss-Seidel core, CSR-encoded.

    Mirrors pfe.solve_interval_entropy's iteration loop line for line:
    level 1 (measured, exact-target pull), level 2 (bounds/groups,
    rescale-into-band), feasible-point sampling between level 2 and
    level 3, level 3 (priors, partial pull). Returns the burn-in-averaged
    x (or the final x when no samples were taken).
    """
    n = x.shape[0]
    x_sum = np.zeros(n, dtype=np.float64)
    n_samples = 0

    n_m = m_indptr.shape[0] - 1
    n_b = b_indptr.shape[0] - 1
    n_p = p_indptr.shape[0] - 1

    for it in range(max_iterations):
        # Level 1 — measured, hard band: always pull to the exact target.
        for i in range(n_m):
            total = 0.0
            for k in range(m_indptr[i], m_indptr[i + 1]):
                total += x[m_idx[k]]
            if total <= 0.0:
                continue
            factor = m_target[i] / total
            for k in range(m_indptr[i], m_indptr[i + 1]):
                x[m_idx[k]] *= factor

        # Level 2 — interval bounds + structure groups, hard.
        for i in range(n_b):
            total = 0.0
            for k in range(b_indptr[i], b_indptr[i + 1]):
                total += x[b_idx[k]]
            if total <= 0.0:
                continue
            if total < b_lo[i]:
                factor = b_lo[i] / total
            elif total > b_hi[i]:
                factor = b_hi[i] / total
            else:
                factor = 1.0
            if factor != 1.0:
                for k in range(b_indptr[i], b_indptr[i + 1]):
                    x[b_idx[k]] *= factor

        # Sample HERE — this point satisfies every hard constraint just
        # enforced; averaging such points stays hard-feasible even if
        # level 3 oscillates (same argument as the Python original).
        if it >= burn_in:
            for j in range(n):
                x_sum[j] += x[j]
            n_samples += 1

        # Level 3 — priors, soft partial pull.
        for i in range(n_p):
            total = 0.0
            for k in range(p_indptr[i], p_indptr[i + 1]):
                total += x[p_idx[k]]
            if total <= 0.0:
                continue
            alpha = p_weight[i] / (p_weight[i] + 1.0)
            factor = 1.0 + alpha * (p_target[i] / total - 1.0)
            if factor > 0.0 and factor != 1.0:
                for k in range(p_indptr[i], p_indptr[i + 1]):
                    x[p_idx[k]] *= factor

    if n_samples > 0:
        for j in range(n):
            x[j] = x_sum[j] / n_samples
    return x


def warmup() -> None:
    """Trigger JIT compilation on a tiny problem (no-op without numba).

    Called from pfe.prepare_calibration in the parent process so forked
    pool workers inherit the compiled function.
    """
    if not NUMBA_AVAILABLE:
        return
    one = np.array([0], dtype=np.int64)
    ptr = np.array([0, 1], dtype=np.int64)
    ipf_iterations(np.ones(1), ptr, one, np.ones(1),
                   ptr, one, np.zeros(1), np.ones(1),
                   ptr, one, np.ones(1), np.ones(1), 0, 1)


def csr_items(items: list, n_values: int) -> tuple:
    """Pack [(index_list, v1[, v2]), ...] into CSR arrays for the kernel.

    Index order inside each item is preserved exactly — summation order
    is part of the bit-identity contract.
    """
    indptr = np.zeros(len(items) + 1, dtype=np.int64)
    total_len = sum(len(item[0]) for item in items)
    idx = np.empty(total_len, dtype=np.int64)
    values = [np.empty(len(items), dtype=np.float64)
              for _ in range(n_values)]
    pos = 0
    for i, item in enumerate(items):
        js = item[0]
        idx[pos:pos + len(js)] = js
        pos += len(js)
        indptr[i + 1] = pos
        for v in range(n_values):
            values[v][i] = item[1 + v]
    return (indptr, idx, *values)
