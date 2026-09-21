"""Closure integrity must use the absolute timestamps emitted by SUMO."""
from pathlib import Path
import numpy as np
import run_scenario as rs
from traffic_sim.simulation.metrics import active_closure_throughput


def test_trimmed_xml_keeps_absolute_quarters(tmp_path):
    path = tmp_path / 'edges.xml'
    path.write_text('<meandata><interval begin="82800" end="83700">'
                    '<edge id="closed" entered="1"/></interval>'
                    '<interval begin="86400" end="87300">'
                    '<edge id="closed" entered="0"/></interval>'
                    '<interval begin="172800" end="173700">'
                    '<edge id="closed" entered="7"/></interval></meandata>')
    flows = rs.parse_edgedata(path, 288, ('closed',))
    closure = [{'edge_id': 'closed', 'begin_s': 86400, 'end_s': 172800}]
    assert np.flatnonzero(flows['closed']).tolist() == [92, 192]
    assert active_closure_throughput(flows, closure) == 0
    # The prior production call double-shifted timestamps and counted 23:00.
    assert active_closure_throughput(flows, closure, window_begin_s=82800) == 1


def test_real_entries_inside_closure_are_still_rejected(tmp_path):
    path = tmp_path / 'edges.xml'
    path.write_text('<meandata><interval begin="86400" end="87300">'
                    '<edge id="closed" entered="2"/></interval></meandata>')
    flows = rs.parse_edgedata(path, 288, ('closed',))
    assert active_closure_throughput(flows, [{'edge_id': 'closed',
        'begin_s': 86400, 'end_s': 172800}]) == 2


def test_production_callers_do_not_shift_absolute_parser_output():
    # Pin both adapter seams: a relative-array unit test alone missed this bug.
    import ast
    for path in ('suggest_closure_time.py', 'traffic_sim/simulation/monthly_sumo.py'):
        tree = ast.parse(Path(path).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == 'active_closure_throughput']
        assert calls
        assert all(not any(k.arg == 'window_begin_s' for k in call.keywords)
                   for call in calls)
