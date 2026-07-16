"""Bit-identity pin for the compiled IPF kernel (pfe_kernel.py).

The kernel's contract is EXACT equality with the pure-Python loop —
identical operations in identical order. These tests run both paths on
adversarial synthetic problems and require np.array_equal (no tolerance).
The full-network proof lives in tools/verify_pfe_kernel.py; the H2
benchmark fingerprint provides the third, independent net.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pfe
import pfe_kernel
from pfe import Candidate

pytestmark = pytest.mark.skipif(not pfe_kernel.NUMBA_AVAILABLE,
                                reason="numba not installed")


def _both_paths(cands, measured, bounds, priors, **kw):
    if not pfe._KERNEL_ENABLED:
        pytest.skip("kernel disabled in this environment")
    fast = pfe.solve_interval_entropy(cands, measured, bounds, priors, **kw)
    try:
        pfe._KERNEL_ENABLED = False
        pure = pfe.solve_interval_entropy(cands, measured, bounds, priors,
                                          **kw)
    finally:
        pfe._KERNEL_ENABLED = True
    return fast, pure


def _assert_identical(fast, pure):
    if pure is None or fast is None:
        assert fast is None and pure is None
        return
    assert np.array_equal(fast, pure), (
        f"kernel not bitwise equal: max abs diff "
        f"{np.max(np.abs(fast - pure)):.3e}")


def cand(*edges):
    return Candidate(depart=0.0, edges=list(edges))


class TestKernelBitIdentity:
    def test_measured_only(self):
        cands = [cand("a", "b"), cand("a"), cand("c")]
        _assert_identical(*_both_paths(cands, {"a": 100.0}, {}, {}))

    def test_measured_bounds_priors_interacting(self):
        # Overlapping constraints on shared routes — the oscillation/
        # averaging machinery does real work here.
        cands = [cand("m", "p"), cand("m", "b"), cand("b", "p"), cand("m")]
        fast, pure = _both_paths(
            cands, {"m": 60.0}, {"b": (5.0, 20.0)}, {"p": (40.0, 2.0)})
        _assert_identical(fast, pure)

    def test_groups_and_route_cost(self):
        rng = np.random.default_rng(3)
        cands = [cand("m", f"x{i}") for i in range(40)]
        cost = rng.uniform(0.1, 1.0, size=40) * pfe.EPS_PARSIMONY
        fast, pure = _both_paths(
            cands, {"m": 200.0}, {}, {},
            route_cost=cost, groups=[(list(range(10)), 0.0, 30.0)])
        _assert_identical(fast, pure)

    def test_infeasible_returns_none_on_both(self):
        cands = [cand("a")]
        fast, pure = _both_paths(cands, {"a": 100.0}, {"a": (0.0, 30.0)}, {})
        assert fast is None and pure is None

    def test_randomized_stress(self):
        # 30 random problems: mixed constraint types, ragged index sets.
        rng = np.random.default_rng(11)
        for trial in range(30):
            n = int(rng.integers(5, 60))
            edges_pool = [f"e{k}" for k in range(int(rng.integers(3, 15)))]
            cands = [cand(*rng.choice(edges_pool,
                                      size=int(rng.integers(1, 4)),
                                      replace=False))
                     for _ in range(n)]
            measured = {edges_pool[0]: float(rng.integers(10, 200))}
            bounds = ({edges_pool[1]: (0.0, float(rng.integers(5, 50)))}
                      if len(edges_pool) > 1 else {})
            priors = ({edges_pool[2]: (float(rng.integers(5, 80)), 1.0)}
                      if len(edges_pool) > 2 else {})
            fast, pure = _both_paths(cands, measured, bounds, priors)
            _assert_identical(fast, pure)
