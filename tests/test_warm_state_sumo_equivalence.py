import xml.etree.ElementTree as ET
from pathlib import Path

from run_scenario import run_sumo
from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.simulation.warm_state_cache import (
    certify_warm_state_equivalence,
    create_baseline_evidence_identity,
    create_warm_state_identity,
    restore_baseline_evidence,
    restore_warm_state,
    store_baseline_evidence,
    store_warm_state,
)


ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / "tools" / "c1_temporary_closure_probe" / "net.net.xml"
WARMUP_END_S = 60
MEASURE_END_S = 240


def _write_routes(path):
    vehicles = "\n".join(
        f'  <vehicle id="v{depart}" type="car" depart="{depart}">'
        '<route edges="direct_in closed direct_out"/></vehicle>'
        for depart in range(0, 200, 10)
    )
    path.write_text(
        '<routes>\n'
        '  <vType id="car" accel="2.6" decel="4.5" length="5" '
        'maxSpeed="10" speedDev="0.1"/>\n'
        f"{vehicles}\n"
        "</routes>\n"
    )


def _write_closure(path):
    path.write_text(
        "<additional>\n"
        '  <rerouter id="closure" edges="direct_in">\n'
        '    <interval begin="90" end="150">\n'
        '      <closingReroute id="closed" disallow="all"/>\n'
        "    </interval>\n"
        "  </rerouter>\n"
        "</additional>\n"
    )


def _write_edge_data(path, output):
    path.write_text(
        "<additional>\n"
        f'  <edgeData id="decision" file="{output.name}" period="15" '
        f'begin="{WARMUP_END_S}" end="{MEASURE_END_S}" '
        'excludeEmpty="false"/>\n'
        "</additional>\n"
    )


def _decision_metrics(path):
    intervals = []
    total_time_loss = 0.0
    total_entered = 0
    closed_during_closure = 0
    for interval in ET.parse(path).getroot().iter("interval"):
        begin = int(float(interval.get("begin")))
        end = int(float(interval.get("end")))
        rows = []
        for edge in interval.findall("edge"):
            row = {
                "id": edge.get("id"),
                "entered": int(float(edge.get("entered", "0"))),
                "left": int(float(edge.get("left", "0"))),
                "time_loss_s": float(edge.get("timeLoss", "0")),
                "travel_time_s": float(edge.get("traveltime", "0")),
            }
            rows.append(row)
            total_time_loss += row["time_loss_s"]
            total_entered += row["entered"]
            if row["id"] == "closed" and begin < 150 and end > 90:
                closed_during_closure += row["entered"]
        intervals.append({"begin_s": begin, "end_s": end, "edges": rows})
    return {
        "intervals": intervals,
        "total_time_loss_s": total_time_loss,
        "total_entered": total_entered,
        "active_closure_edge_entries": closed_during_closure,
    }


def _run_condition(work, route, closure, state=None):
    work.mkdir()
    edge_output = work / "edge.xml"
    edge_additional = work / "edge.add.xml"
    statistics = work / "statistics.xml"
    _write_edge_data(edge_additional, edge_output)
    run_sumo(
        1000,
        route,
        [closure, edge_additional],
        MEASURE_END_S,
        sumo_home(),
        micro=False,
        begin_s=WARMUP_END_S if state is not None else 0,
        net_path=NET,
        flush_s=60,
        stats_path=statistics,
        load_state_path=state,
        work_dir=work,
    )
    return _decision_metrics(edge_output)


def test_real_sumo_save_load_and_cache_are_decision_equivalent(tmp_path):
    route = tmp_path / "demand.rou.xml"
    closure = tmp_path / "closure.add.xml"
    _write_routes(route)
    _write_closure(closure)

    uninterrupted = _run_condition(
        tmp_path / "uninterrupted", route, closure)

    warm = tmp_path / "warm"
    warm.mkdir()
    state = warm / "state.xml.gz"
    run_sumo(
        1000,
        route,
        [],
        WARMUP_END_S,
        sumo_home(),
        micro=False,
        begin_s=0,
        net_path=NET,
        # SUMO's --end is exclusive, so saving at t=60 requires the runner
        # to advance one more second even though the cached state remains t=60.
        flush_s=1,
        save_state_path=state,
        save_state_time_s=WARMUP_END_S,
        work_dir=warm,
    )
    branched = _run_condition(
        tmp_path / "branched", route, closure, state=state)

    identity = create_warm_state_identity(
        demand_build_id="test-demand",
        demand_variant="q50",
        seed=1000,
        simulation_mode="meso",
        warmup_end_s=WARMUP_END_S,
        network_path=NET,
        route_path=route,
        source_files={
            "equivalence_test": Path(__file__),
        },
        sumo_home=sumo_home(),
    )
    certificate = certify_warm_state_equivalence(
        identity, uninterrupted, branched)

    assert certificate.status == "pass", certificate.mismatch
    assert uninterrupted["active_closure_edge_entries"] == 0

    cache = tmp_path / "cache"
    store_warm_state(cache, identity, state, certificate)
    restored = tmp_path / "restored" / "state.xml.gz"
    lookup = restore_warm_state(cache, identity, restored)
    assert lookup.hit is True

    cached_branch = _run_condition(
        tmp_path / "cached-branch", route, closure, state=restored)
    cached_certificate = certify_warm_state_equivalence(
        identity, uninterrupted, cached_branch)
    assert cached_certificate.status == "pass", cached_certificate.mismatch

    baseline_identity = create_baseline_evidence_identity(
        identity,
        analysis_start_s=WARMUP_END_S,
        analysis_end_s=MEASURE_END_S,
        affected_edges=("closed", "detour_1", "detour_2"),
        objective_profile="robust_time_loss",
    )
    baseline_cache = tmp_path / "baseline-cache"
    store_baseline_evidence(
        baseline_cache, baseline_identity, uninterrupted, certificate)
    baseline_lookup = restore_baseline_evidence(
        baseline_cache, baseline_identity)
    assert baseline_lookup.hit is True
    assert baseline_lookup.metrics == uninterrupted
