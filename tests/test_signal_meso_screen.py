"""
Unit tests for signal_meso_screen.py (PLAN.md Phase D3).

The heavy end (running micro AND meso for six conditions) is exercised
manually against real demand, not here — mirrors the established test
style for every other SUMO-invoking script in this project. This covers
the one pure, independently-testable piece: measuring short TLS-approach
edges from a net.net.xml, which PLAN.md explicitly asked to "check ours"
for.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import signal_meso_screen as sms


def _write_net(tmp_path, edges):
    """edges: list of (id, to_junction, length_m) tuples, all feeding a
    single TLS-controlled junction 'j'."""
    lines = ['<net>', '  <tlLogic id="j" type="static" programID="0" offset="0">',
            '    <phase duration="30" state="G"/>', '  </tlLogic>']
    for eid, to, length in edges:
        lines.append(f'  <edge id="{eid}" to="{to}">')
        lines.append(f'    <lane id="{eid}_0" length="{length}"/>')
        lines.append('  </edge>')
    # An internal edge (function set) feeding the same junction — must be
    # ignored regardless of its length, matching truncate_stranded_vehicles'
    # own established internal-edge exclusion pattern elsewhere.
    lines.append('  <edge id=":j_0" function="internal" to="j">')
    lines.append('    <lane id=":j_0_0" length="0.1"/>')
    lines.append('  </edge>')
    lines.append('</net>')
    path = tmp_path / "net.net.xml"
    path.write_text("\n".join(lines))
    return path


class TestShortTlsApproaches:
    def test_counts_edges_under_threshold(self, tmp_path):
        net = _write_net(tmp_path, [
            ("a", "j", 5.0), ("b", "j", 20.0), ("c", "j", 14.9), ("d", "j", 15.0),
        ])
        report = sms.short_tls_approaches(net)
        assert report["total_tls_approach_edges"] == 4
        assert report["short_count"] == 2   # 5.0 and 14.9 are < 15; 15.0 is not
        assert {e["edge"] for e in report["examples"]} == {"a", "c"}

    def test_internal_edges_are_excluded(self, tmp_path):
        net = _write_net(tmp_path, [("a", "j", 20.0)])
        report = sms.short_tls_approaches(net)
        # the internal edge ":j_0" (0.1 m) must NOT be counted despite
        # feeding the same TLS junction and being well under the threshold
        assert report["total_tls_approach_edges"] == 1
        assert report["short_count"] == 0

    def test_non_tls_junction_edges_are_excluded(self, tmp_path):
        lines = ['<net>',
                '  <tlLogic id="j" type="static" programID="0" offset="0">',
                '    <phase duration="30" state="G"/>', '  </tlLogic>',
                '  <edge id="a" to="j"><lane id="a_0" length="5.0"/></edge>',
                '  <edge id="b" to="not_a_tls"><lane id="b_0" length="1.0"/></edge>',
                '</net>']
        path = tmp_path / "net.net.xml"
        path.write_text("\n".join(lines))
        report = sms.short_tls_approaches(path)
        assert report["total_tls_approach_edges"] == 1
        assert report["short_count"] == 1

    def test_zero_approach_edges_reports_none_pct_not_a_crash(self, tmp_path):
        lines = ['<net>',
                '  <tlLogic id="j" type="static" programID="0" offset="0">',
                '    <phase duration="30" state="G"/>', '  </tlLogic>',
                '</net>']
        path = tmp_path / "net.net.xml"
        path.write_text("\n".join(lines))
        report = sms.short_tls_approaches(path)
        assert report["total_tls_approach_edges"] == 0
        assert report["short_pct"] is None

    def test_examples_sorted_shortest_first(self, tmp_path):
        net = _write_net(tmp_path, [("a", "j", 10.0), ("b", "j", 2.0), ("c", "j", 7.0)])
        report = sms.short_tls_approaches(net)
        assert [e["edge"] for e in report["examples"]] == ["b", "c", "a"]


def _deltas(micro_vals: dict, meso_vals: dict) -> dict:
    return {
        "micro": {n: {"delta_time_loss_s": v} for n, v in micro_vals.items()},
        "meso": {n: {"delta_time_loss_s": v} for n, v in meso_vals.items()},
    }


class TestConditionCorrelation:
    """condition_correlation (fixed 2026-07-11, self-review): the degenerate-
    input guard must inspect the RAW delta_time_loss_s values, not
    order.index()-derived positions that are always a clean 0..N-1
    permutation regardless of whether the underlying values differ at all."""

    def test_real_correlation_is_computed_for_distinct_values(self):
        names = ["a", "b", "c"]
        deltas = _deltas({"a": 100.0, "b": 200.0, "c": 300.0},
                         {"a": 150.0, "b": 250.0, "c": 350.0})
        micro_order, meso_order, rho, pval = sms.condition_correlation(names, deltas)
        assert rho == 1.0   # identical ordering on both sides
        assert micro_order == meso_order == ["a", "b", "c"]

    def test_degenerate_micro_values_are_detected_not_silently_ranked(self):
        # This is the exact bug: with the old order.index()-based ranks,
        # this identical-everywhere micro side would still produce a
        # confident-looking rho instead of being caught as degenerate.
        names = ["a", "b", "c"]
        deltas = _deltas({"a": 100.0, "b": 100.0, "c": 100.0},
                         {"a": 150.0, "b": 250.0, "c": 350.0})
        _, _, rho, pval = sms.condition_correlation(names, deltas)
        assert rho is None
        assert pval is None

    def test_degenerate_meso_values_are_detected(self):
        names = ["a", "b", "c"]
        deltas = _deltas({"a": 100.0, "b": 200.0, "c": 300.0},
                         {"a": 50.0, "b": 50.0, "c": 50.0})
        _, _, rho, pval = sms.condition_correlation(names, deltas)
        assert rho is None
        assert pval is None

    def test_negative_correlation_is_a_real_negative_rho_not_masked(self):
        names = ["a", "b", "c"]
        deltas = _deltas({"a": 100.0, "b": 200.0, "c": 300.0},
                         {"a": 300.0, "b": 200.0, "c": 100.0})
        _, _, rho, _ = sms.condition_correlation(names, deltas)
        assert rho == -1.0
