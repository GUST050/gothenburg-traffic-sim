"""Unit tests for Agent B (observability.py) on synthetic networks —
the real network currently has too few sensors for exact solves (0 derived,
verified), so correctness is proven here and the machinery scales with
sensor density."""

import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from observability import chain_successor, corridor_alarms, junction_fixpoint

N = 96


def series(val):
    return np.full(N, float(val))


def eid(u, v, k=0):
    return f"{u}_{v}_{k}"


class TestJunctionFixpoint:
    def make_y_junction(self):
        """A→J→B and J→C: one in, two out."""
        G = nx.MultiDiGraph()
        for u, v in [(1, 10), (10, 2), (10, 3)]:
            G.add_edge(u, v, key=0)
        return G

    def test_single_unknown_is_derived(self):
        G = self.make_y_junction()
        known = {eid(1, 10): series(100), eid(10, 2): series(60)}
        derived, alarms = junction_fixpoint(G, known, N)
        assert eid(10, 3) in derived
        np.testing.assert_allclose(derived[eid(10, 3)], 40.0)
        assert not alarms

    def test_two_unknowns_not_derived(self):
        G = self.make_y_junction()
        known = {eid(1, 10): series(100)}
        derived, _ = junction_fixpoint(G, known, N)
        assert not derived

    def test_fixpoint_cascade(self):
        """Deriving at J1 unlocks J2: 1→J1→{2, J2}, J2→{3, 4}."""
        G = nx.MultiDiGraph()
        for u, v in [(1, 10), (10, 2), (10, 20), (20, 3), (20, 4)]:
            G.add_edge(u, v, key=0)
        known = {eid(1, 10): series(100), eid(10, 2): series(30),
                 eid(20, 3): series(50)}
        derived, _ = junction_fixpoint(G, known, N)
        np.testing.assert_allclose(derived[eid(10, 20)], 70.0)   # J1 solve
        np.testing.assert_allclose(derived[eid(20, 4)], 20.0)    # unlocked J2

    def test_contradiction_raises_alarm(self):
        """More flow out than in → negative derivation must alarm."""
        G = self.make_y_junction()
        known = {eid(1, 10): series(50), eid(10, 2): series(90)}
        derived, alarms = junction_fixpoint(G, known, N)
        assert any(a["type"] == "negative_derivation" for a in alarms)
        assert np.all(derived[eid(10, 3)] >= 0)   # clipped, not negative

    def test_missing_slots_propagate_as_nan(self):
        G = self.make_y_junction()
        a = series(100); a[10] = np.nan
        known = {eid(1, 10): a, eid(10, 2): series(60)}
        derived, _ = junction_fixpoint(G, known, N)
        assert np.isnan(derived[eid(10, 3)][10])
        assert derived[eid(10, 3)][0] == pytest.approx(40.0)

    def test_boundary_stub_is_not_a_conservation_node(self):
        """A dead-end node (no outgoing edges) must never be 'solved'."""
        G = nx.MultiDiGraph()
        G.add_edge(1, 2, key=0)    # node 2 has in but no out
        known = {}
        derived, _ = junction_fixpoint(G, known, N)
        assert not derived


class TestCorridor:
    def make_chain(self):
        """m1 → c1 → c2 → m2: measured ends, unbranched middle."""
        G = nx.MultiDiGraph()
        for u, v in [(1, 2), (2, 3), (3, 4), (4, 5)]:
            G.add_edge(u, v, key=0)
        return G

    def test_chain_successor_found(self):
        G = self.make_chain()
        known = {eid(1, 2): series(100), eid(4, 5): series(100)}
        assert chain_successor(G, eid(1, 2), set(known)) == eid(4, 5)

    def test_consistent_corridor_is_ok(self):
        G = self.make_chain()
        known = {eid(1, 2): series(100), eid(4, 5): series(102)}
        alarms = corridor_alarms(G, known)
        assert len(alarms) == 1
        assert alarms[0]["severity"] == "ok"

    def test_inconsistent_corridor_alarms(self):
        G = self.make_chain()
        known = {eid(1, 2): series(100), eid(4, 5): series(300)}
        alarms = corridor_alarms(G, known)
        assert alarms[0]["severity"] == "ALARM"

    def test_branching_breaks_chain(self):
        G = self.make_chain()
        G.add_edge(3, 9, key=0)   # side street at node 3 → not a pure chain
        known = {eid(1, 2): series(100), eid(4, 5): series(300)}
        assert chain_successor(G, eid(1, 2), set(known)) is None
