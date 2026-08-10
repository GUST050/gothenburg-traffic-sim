"""Which edges survive their own closure? Topology only, no demand, no SUMO run.

The demand half of Stage 4's precondition needs the canonical archived routes.
The TOPOLOGY half needs nothing but the network, so it can be answered on any
machine that can run `make sumo-net` — and it answers the question that decides
whether a v10 freeze is worth attempting: are there at least four near-sensor
candidates whose closure severs nobody?

WHAT IT MEASURES.  For each edge, whether any real approach immediately before
the closure can still reach each immediate successor after the edge is removed.
That is the Skånegatan/Engelbrektsgatan shape in `CLAUDE.md` — node 3575001205
has exactly ONE incoming connection in the whole network, so closing that edge
structurally cut off everything downstream, and 39 vehicles were then
teleported through the closure because the live rerouter had nothing to offer.

WHAT IT DOES NOT MEASURE.  Whether any vehicle actually wanted to go there. An
edge that passes this screen can still strand real demand further along its
route; that is the per-vehicle half, and it needs the archive. The screen is a
NECESSARY condition, reported as one.

Reads the tracked network and the SUMO network derived from it. Runs no
simulation, writes one validation artifact, touches no release or study.

Run:
    python3 tools/screen_closure_survivability.py [--out PATH] [--stdout]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from traffic_sim.core.fingerprint import sha256_file          # noqa: E402
from traffic_sim.simulation import closure_survivability as cs  # noqa: E402

SCREEN_VERSION = 2
MEASURED_AT = "2026-08-10"
DEFAULT_OUT = ROOT / "validation" / "closure_survivability_screen_v2.json"

#: The edge `CLAUDE.md` documents as structurally severing its downstream, used
#: as a live self-check: a screen that cannot see its own motivating case is
#: inert, which is exactly what the first version of this probe was.
KNOWN_SEVERING_EDGE = "60786979_3575001205_0"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    parser.add_argument("--stdout", action="store_true",
                        help="print the report instead of writing a file")
    parser.add_argument("--verify", action="store_true",
                        help="recompute and compare byte-for-byte; never write")
    return parser.parse_args(argv)


def _content_key(payload: dict) -> str:
    body = {key: value for key, value in payload.items()
            if key != "content_key"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _known_case(collapsed, predecessors) -> tuple[str, ...]:
    """Fail closed when the live self-check is absent or no longer severs."""
    if KNOWN_SEVERING_EDGE not in collapsed:
        raise SystemExit(
            f"self-check failed: documented edge {KNOWN_SEVERING_EDGE} is "
            "absent from the collapsed network")
    cut_off = cs.successors_cut_off(
        KNOWN_SEVERING_EDGE, collapsed, predecessors)
    if not cut_off:
        raise SystemExit(
            f"self-check failed: closing {KNOWN_SEVERING_EDGE} cuts off "
            "nothing, but CLAUDE.md records it as structurally severing its "
            "downstream. The screen is not measuring what it claims to.")
    return cut_off


def screen() -> dict:
    import run_scenario as rs                                  # noqa: PLC0415
    from tools.freeze_heldout_v10 import (                     # noqa: PLC0415
        MAX_SENSOR_DISTANCE_M, MIN_EDGE_LENGTH_M, MIN_SURVIVING_CASES,
        ROAD_CLASSES, candidate_pool)

    if not rs.NET_PATH.is_file():
        raise SystemExit(
            f"the screen needs the SUMO network at {rs.NET_PATH}. Build it "
            f"with `make sumo-net` — it is derived deterministically from the "
            f"tracked web/data/graph.graphml.")

    adjacency = rs.build_edge_graph(set())
    collapsed = cs.collapse_internal(adjacency)
    predecessors = cs.real_predecessors(collapsed)

    # SELF-CHECK BEFORE ANY RESULT IS REPORTED. The first version of this probe
    # ran on the raw connection graph, where SUMO's internal junction lanes keep
    # a successor alive after its only real predecessor is removed, and it duly
    # reported zero severing edges across the whole network. Refusing to emit a
    # report unless the documented case is reproduced makes that failure loud.
    known = _known_case(collapsed, predecessors)

    network_rows = []
    for edge in sorted(collapsed):
        cut = cs.successors_cut_off(edge, collapsed, predecessors)
        if cut:
            network_rows.append({"edge_id": edge, "successors_cut_off": list(cut)})

    pool, prior = candidate_pool()
    pool_rows = []
    for entry in pool:
        cut = cs.successors_cut_off(entry["edge_id"], collapsed, predecessors)
        pool_rows.append({
            "edge_id": entry["edge_id"],
            "road_class": entry["road_class"],
            "dist_sensor_m": entry["dist_sensor_m"],
            "survives_topology": not cut,
            "successors_cut_off": list(cut),
        })
    survivors = [row for row in pool_rows if row["survives_topology"]]

    by_class = {}
    for row in pool_rows:
        bucket = by_class.setdefault(row["road_class"], {"survive": 0, "fatal": 0})
        bucket["survive" if row["survives_topology"] else "fatal"] += 1

    report = {
        "schema_version": 1,
        "kind": "closure_survivability_topology_screen",
        "screen_version": SCREEN_VERSION,
        "stage": "closure_integrity_plan_stage_4",
        "rule_version": cs.RULE_VERSION,
        "measured_at": MEASURED_AT,
        "question": ("which edges leave an immediate successor unreachable "
                     "from every real approach once they close?"),
        "self_check": {
            "edge": KNOWN_SEVERING_EDGE,
            "successors_cut_off": list(known),
            "basis": ("CLAUDE.md: node 3575001205 has exactly ONE incoming "
                      "connection in the whole network"),
        },
        "network": {
            "directed_edges": len(collapsed),
            "raw_connection_sources": len(adjacency),
            "internal_sources_collapsed": sum(
                1 for edge in adjacency if edge.startswith(":")),
            "edges_that_sever_a_successor": len(network_rows),
            "share": round(len(network_rows) / len(collapsed), 4),
        },
        "candidate_pool": {
            "rule": (f"dist_sensor_m <= {MAX_SENSOR_DISTANCE_M:g} m, "
                     f"length >= {MIN_EDGE_LENGTH_M:g} m, road class in "
                     f"{list(ROAD_CLASSES)}, excluding every prior campaign "
                     f"edge and its junction neighbours"),
            "prior_heldout_edges_excluded": len(prior),
            "size": len(pool_rows),
            "survive_topology": len(survivors),
            "fatal": len(pool_rows) - len(survivors),
            "by_road_class": by_class,
            "min_surviving_cases_required": MIN_SURVIVING_CASES,
            "gate_achievable": len(survivors) >= MIN_SURVIVING_CASES,
            "edges": pool_rows,
        },
        "limitation": (
            "TOPOLOGY ONLY, and therefore a NECESSARY condition, not a "
            "sufficient one. An edge that passes can still strand real demand "
            "further along a route; that is the per-vehicle half of the rule "
            "and it needs the canonical archived demand, which lives under the "
            "gitignored runs/ tree."),
        "sources": {
            "run_scenario.py": sha256_file(ROOT / "run_scenario.py"),
            "tools/freeze_heldout_v10.py": sha256_file(
                ROOT / "tools/freeze_heldout_v10.py"),
            "tools/screen_closure_survivability.py": sha256_file(
                Path(__file__)),
            "traffic_sim/simulation/closure_survivability.py": sha256_file(
                ROOT / "traffic_sim/simulation/closure_survivability.py"),
            "web/data/graph.graphml": sha256_file(ROOT / "web/data/graph.graphml"),
            "web/data/network.geojson": sha256_file(ROOT / "web/data/network.geojson"),
            "sumo/net.net.xml": sha256_file(rs.NET_PATH),
        },
        "severing_edges": network_rows,
    }
    report["content_key"] = _content_key(report)
    return report


def main(argv=None) -> int:
    args = parse_args(argv)
    report = screen()
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.verify:
        out = Path(args.out)
        if not out.is_file():
            raise SystemExit(f"cannot verify absent report: {out}")
        stored = json.loads(out.read_text())
        if stored.get("content_key") != _content_key(stored):
            raise SystemExit(f"stored report content key is invalid: {out}")
        if stored != report:
            raise SystemExit(f"stored report does not reproduce: {out}")
        print("reproduces byte-for-byte: True")
        return 0
    if args.stdout:
        print(text)
    else:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"refusing to overwrite {out}; remove it deliberately")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")

    net = report["network"]
    pool = report["candidate_pool"]
    print(f"network: {net['edges_that_sever_a_successor']}/{net['directed_edges']} "
          f"edges ({net['share']:.1%}) sever a successor when closed")
    print(f"pool:    {pool['survive_topology']}/{pool['size']} near-sensor "
          f"candidates survive the topology screen")
    for road_class, counts in sorted(pool["by_road_class"].items()):
        print(f"  {road_class:10s} survive {counts['survive']:3d}  "
              f"fatal {counts['fatal']:3d}")
    print(f"stage 4 gate achievable (>= {pool['min_surviving_cases_required']} "
          f"survivors): {pool['gate_achievable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
