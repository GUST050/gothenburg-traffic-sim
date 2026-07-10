"""
Run a SUMO scenario (baseline or road closure) and export flows for the web app.

Run after build_sumo_demand.py:
  python3 run_scenario.py                                    # baseline
  python3 run_scenario.py --close 60786979_3575001205_0      # whole-run closure
  python3 run_scenario.py --closure '{"edge_id":"60786979_3575001205_0","begin":"2025-09-16T08:00:00","end":"2025-09-16T10:00:00"}'

Method:
  - Simulates the calibrated demand (sumo/calibrated.rou.xml) N times with
    different random seeds (Monte Carlo).
  - --close <edgeId> adds a whole-run rerouter closure. --closure JSON adds
    one time-windowed closure without parallel CLI argument lists;
    SUMO reroutes vehicles around it by construction.
  - 15-min per-edge flows ("entered" vehicle counts) are averaged over seeds
    and written in the flows.json format with the SAME edge IDs as the map.

Per-edge confidence (0-1), written into the scenario file:
    confidence = spatial_prior × exp(-CV)
  where spatial_prior comes from network.geojson (distance to nearest sensor)
  and CV is the mean coefficient of variation across Monte Carlo seeds.
  Far from sensors AND unstable across seeds → low confidence.

Writes:
  web/data/scenarios/<name>.json   — flows + confidence for the web app
  web/data/scenarios/index.json    — manifest the web app lists scenarios from
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from build_sumo_net import sumo_home

SUMO_DIR  = Path("sumo")
NET_PATH  = SUMO_DIR / "net.net.xml"
GEO_PATH  = Path("web/data/network.geojson")
OUT_DIR   = Path("web/data/scenarios")

# A whole-day meso run normally takes ~10-15 s (measured: 3-seed whole-day
# closure ~35 s total). This is a safety net, not a normal-path limit: without
# it, a hung sumo process has no bound and — if this script's own PARENT
# process (e.g. serve.py's outer subprocess.run) times out and kills THIS
# process first — becomes permanently orphaned, since a timeout can only ever
# kill its own direct child. Found in review 2026-07-07.
SUMO_TIMEOUT_S = 300


def demand_signature(meta: dict) -> str:
    """Stable fingerprint for the demand that produced a scenario.

    Scenario files are only meaningful for the calibrated demand currently
    represented by sumo/demand_meta.json. If someone runs build_sumo_demand.py
    manually, old closure scenarios from the previous date/window otherwise
    remain in index.json and look selectable even though they were generated
    from different routes. The signature keeps the manifest scoped to one
    demand build.
    """
    keys = ("date", "source", "begin", "end", "n_intervals", "epoch_sim", "n_variants")
    payload = {k: meta.get(k) for k in keys}
    # B1 metadata is additive for one-day demand. Preserve that established
    # signature exactly so current scenarios remain valid; multi-day demand
    # is distinguishable by its explicit range contract.
    if meta.get("days", 1) > 1:
        for key in ("start_date", "days", "end_date_exclusive",
                    "day_boundaries_s", "day_kinds"):
            payload[key] = meta.get(key)
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def demand_window_label(meta: dict) -> str:
    """Human-readable date range for scenario/index display.

    demand_metadata() (build_sumo_demand.py) only populates date/begin/end
    for single-day demand; multi-day demand carries start_date/
    end_date_exclusive/days instead. Reading meta['date'] unconditionally
    crashes with KeyError on multi-day demand.
    """
    if "date" in meta:
        return f"{meta['date']} {meta['begin']}–{meta['end']}"
    return (f"{meta['start_date']} → {meta['end_date_exclusive']} "
            f"({meta['days']} days)")


def index_for_current_demand(index: dict, signature: str) -> dict:
    """Drop manifest entries generated from another demand calibration."""
    scenarios = [
        s for s in index.get("scenarios", [])
        if s.get("demand_signature") == signature
    ]
    return {"demand_signature": signature, "scenarios": scenarios}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--close", nargs="*", default=[], metavar="EDGE_ID",
                   help="Edge(s) to close (must exist in network.geojson). "
                        "Omit for baseline.")
    p.add_argument("--closure", action="append", default=[], metavar="JSON",
                   help='Time-windowed closure, repeatable: '
                        "'{\"edge_id\":\"EDGE\",\"begin\":\"ISO\",\"end\":\"ISO\"}'. "
                        "--close remains the backwards-compatible whole-run form.")
    p.add_argument("--name",  default=None,
                   help="Scenario name (default: 'baseline' or 'close_<edge…>')")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo runs (default 3)")
    p.add_argument("--micro", action="store_true",
                   help="Use microscopic simulation (default: mesoscopic — "
                        "~20x faster, adequate for 15-min edge flows)")
    p.add_argument("--no-trajectories", action="store_true",
                   help="Skip the per-vehicle trajectory export (one extra "
                        "seed-1000 run with vehroute exit-times)")
    return p.parse_args()


def structured_closures(raw: list[str], whole_edges: list[str], epoch: str,
                        duration_s: int) -> list[dict]:
    """Parse CLI closures into the single internal time-window contract.

    The legacy --close form is deliberately represented as the old 0 through
    duration+flush interval, so all existing whole-day behaviour is retained.
    """
    if raw and whole_edges:
        raise ValueError("use either legacy --close or structured --closure, not both")
    closures = [
        {"edge_id": edge, "begin_s": 0, "end_s": duration_s + 3600}
        for edge in whole_edges
    ]
    epoch_ts = pd.Timestamp(epoch)
    for value in raw:
        try:
            item = json.loads(value)
            edge = item["edge_id"]
            begin = pd.Timestamp(item["begin"])
            end = pd.Timestamp(item["end"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("--closure must be JSON with edge_id, begin and end ISO values") from exc
        if begin.tzinfo is not None:
            begin = begin.tz_convert(None)
        if end.tzinfo is not None:
            end = end.tz_convert(None)
        begin_s = (begin - epoch_ts).total_seconds()
        end_s = (end - epoch_ts).total_seconds()
        if not (0 <= begin_s < end_s <= duration_s + 3600):
            raise ValueError("--closure window must be within the simulated run and have begin < end")
        closures.append({"edge_id": edge, "begin_s": int(begin_s), "end_s": int(end_s)})
    return closures


def edge_freeflow_times() -> dict[str, float]:
    """Free-flow seconds per net edge for the conservative window prefilter."""
    times = {}
    for edge in ET.parse(NET_PATH).getroot().findall("edge"):
        # SUMO stores these on the first lane in normal net.net.xml files;
        # accept edge-level attributes too for small synthetic fixtures.
        lane = edge.find("lane")
        length = float(edge.get("length") or (lane.get("length") if lane is not None else 0) or 0)
        speed = float(edge.get("speed") or (lane.get("speed") if lane is not None else 0) or 0)
        if length > 0 and speed > 0:
            times[edge.get("id")] = length / speed
    return times


def load_geojson_meta() -> tuple[dict[str, float], dict[str, str]]:
    """{edge_id: spatial confidence prior}, {edge_id: street name}."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    prior: dict[str, float] = {}
    names: dict[str, str] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        prior[p["id"]] = p.get("confidence") or 0.5
        names[p["id"]] = p.get("name") or p["id"]
    return prior, names


def write_edgedata_additional(path: Path, edgedata_file: Path,
                              duration_s: int) -> None:
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <edgeData id="ed" file="{edgedata_file.name}" period="900" '
                f'begin="0" end="{duration_s}" excludeEmpty="true"/>\n')
        f.write("</additional>\n")


REROUTER_RADIUS_M = 400


def edges_near(close_edges: list[str], radius_m: float) -> list[str]:
    """Edges whose midpoint lies within radius_m of a closed edge's midpoint.

    The rerouter is attached to THESE edges only. Attaching it to all 2 251
    edges makes every vehicle re-check its route on every edge entry —
    measured cost: 11.5 min per whole-day seed vs ~1 min with a local
    rerouter. Locality is also behaviourally reasonable: drivers divert when
    they encounter the closure area, not telepathically at departure.
    """
    import math
    import xml.etree.ElementTree as ET
    mids: dict[str, tuple[float, float]] = {}
    for e in ET.parse(SUMO_DIR / "plain.edg.xml").getroot().findall("edge"):
        pts = [tuple(map(float, p.split(","))) for p in e.get("shape").split()]
        mids[e.get("id")] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))
    centres = [mids[ce] for ce in close_edges if ce in mids]
    out = set(close_edges)
    for eid, (x, y) in mids.items():
        if any(math.hypot(x - cx, y - cy) <= radius_m for cx, cy in centres):
            out.add(eid)
    return sorted(out)


def write_closure_additional(path: Path, closures: list[dict],
                             all_edges: list[str]) -> None:
    """One shared closure file per scenario. Vehicles whose remaining route
    uses a closed edge recompute when they enter a rerouter edge (the
    closure's neighbourhood)."""
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <rerouter id="closure" edges="{" ".join(all_edges)}">\n')
        for closure in closures:
            f.write(f'    <interval begin="{closure["begin_s"]}" end="{closure["end_s"]}">\n')
            f.write(f'      <closingReroute id="{closure["edge_id"]}" disallow="all"/>\n')
            f.write("    </interval>\n")
        f.write("  </rerouter>\n")
        f.write("</additional>\n")


def build_edge_graph(banned: set[str]) -> dict[str, list[str]]:
    """Directed edge->edge adjacency from net.net.xml's <connection> elements,
    with `banned` edges (the closure) removed from both ends — the same
    graph SUMO's own router uses to find a path, minus the closed edges."""
    adj: dict[str, list[str]] = {}
    for c in ET.parse(NET_PATH).getroot().findall("connection"):
        frm, to = c.get("from"), c.get("to")
        if frm in banned or to in banned:
            continue
        adj.setdefault(frm, []).append(to)
    return adj


def reachable(adj: dict[str, list[str]], start: str, goal: str,
             banned: set[str]) -> bool:
    # start==goal is only trivially "reachable" if that edge itself isn't
    # the one being closed — a single-edge trip THROUGH the closed edge
    # (origin==destination==closed edge) has nowhere to go, not nowhere
    # to detour.
    if start in banned or goal in banned:
        return False
    if start == goal:
        return True
    seen = {start}
    stack = [start]
    while stack:
        for nxt in adj.get(stack.pop(), ()):
            if nxt == goal:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def truncate_stranded_vehicles(route_path: Path, close_edges: list[str],
                               out_path: Path, adj: dict[str, list[str]],
                               closures: list[dict] | None = None,
                               edge_travel_s: dict[str, float] | None = None) -> tuple[int, int]:
    """Shorten (don't delete) vehicles whose route has no detour at all.

    FOUND 2026-07-09 (Gustav asked for the closure-leak finding from the
    demand rebuild's Codex review to actually get fixed): SUMO's runtime
    <rerouter>/closingReroute (write_closure_additional) reroutes ~99.4% of
    affected vehicles fine, but for an origin/destination pair with NO
    detour around the closure at all (confirmed directly: duarouter, even
    given the same closure additional file and even replanning the trip
    from scratch, still routes through the "closed" edge — rerouters are a
    RUNTIME sumo concept, invisible to the offline router — and a plain
    Dijkstra over net.net.xml's <connection> graph with the closed edges
    removed returns no path either, independent confirmation), the live
    rerouter can't find an alternative and the vehicle just sits stuck on
    the rerouter edge until sumo's end-of-run stuck-vehicle cleanup
    forcibly TELEPORTS it past the closure — which then shows up in the
    exported flows/trajectory as if it had legitimately driven the closed
    edge.

    FIRST FIX (same day) just deleted these vehicles outright — WRONG
    (Gustav, correctly: people whose actual destination is now unreachable
    by car still drive most of the way and park short of the closure,
    walking the rest — dropping the whole vehicle erases its real traffic
    contribution on every OTHER edge of its route too, not just the
    closed one, understating flow on the approach streets). Fixed:
    TRUNCATE the route at the last edge reachable before the closure
    (i.e. right before the first closed edge encountered) instead of
    removing the vehicle — it still drives and is counted on everything
    up to that point, it just "arrives" there instead of continuing.
    Only actually dropped if the closed edge is the very FIRST edge of
    the route (nothing to truncate to — no partial trip is possible).

    SECOND FIX (same day, Codex review): the reachability check used to
    test origin→destination, as a proxy for "will the live rerouter
    detour this one fine" — WRONG for two reasons Codex caught: (a) two
    vehicles with the same origin/destination can be on different
    candidate routes, so one can already be committed to a branch its
    ORIGIN could have avoided but IT no longer can; (b) with multiple
    `--close` edges, truncating at the first one encountered ignores
    whether a LATER closure on the same route is what actually kills the
    detour. Both are the same underlying bug: origin isn't where the live
    rerouter re-plans FROM — it re-plans from wherever the vehicle
    currently is when it hits the closure. Fixed: check reachability from
    the edge immediately BEFORE the first closed edge in THIS vehicle's
    own route (not from edges[0]) to the final destination — this is
    exactly what sumo's rerouter itself computes, and since `reachable()`
    does a full graph search (not just along the original candidate
    route) with ALL close_edges removed at once, it already correctly
    accounts for every other closure on the route too, not just the
    first.

    For a time window, only a vehicle estimated to arrive at its closed edge
    during that window is considered. Its arrival is depart plus free-flow
    travel time over the preceding route. A no-detour vehicle is retained if
    the remaining closure wait is safely below SUMO's 300 s teleport limit;
    otherwise it is truncated exactly as in the whole-run case.  This does
    not use periodic mode-8 routing: C1 showed that it changes route choice.

    `adj` is built ONCE by the caller (build_edge_graph(closed)) and
    reused across every demand variant file for this closure — it only
    depends on close_edges, not on which route file is being filtered.
    """
    closed = set(close_edges)
    # None deliberately selects the legacy branch below.  The eleven
    # whole-duration regression tests exercise that branch unchanged.
    windowed = closures is not None
    edge_travel_s = edge_travel_s or {}
    tree = ET.parse(route_path)
    root = tree.getroot()
    affected = [v for v in root.findall("vehicle")
                if closed & set(v.find("route").get("edges").split())]
    if not affected:
        tree.write(out_path, xml_declaration=True, encoding="UTF-8")
        return 0, 0
    cache: dict[tuple[str, str], bool] = {}
    n_truncated = 0
    n_dropped = 0
    for v in affected:
        route_el = v.find("route")
        edges = route_el.get("edges").split()
        candidates = [(idx, closure) for idx, edge in enumerate(edges)
                      for closure in (closures or []) if edge == closure["edge_id"]]
        if windowed:
            depart = float(v.get("depart") or 0)
            elapsed = 0.0
            active: list[tuple[int, dict, float]] = []
            for idx, edge in enumerate(edges):
                for ci, closure in candidates:
                    if ci == idx:
                        arrival = depart + elapsed
                        # Free-flow is an optimistic arrival estimate.  Only
                        # classify arrivals inside the stated window; this is
                        # intentionally conservative about the wait threshold.
                        if closure["begin_s"] <= arrival < closure["end_s"]:
                            active.append((idx, closure, arrival))
                elapsed += edge_travel_s.get(edge, 0.0)
            if not active:
                continue
            i, closure, arrival = min(active, key=lambda x: x[0])
            # C1 observed the default teleport at 301 s. Keep only waits
            # strictly below 300 s, leaving one second of headroom.
            if closure["end_s"] - arrival >= 300:
                pass
            else:
                continue
        else:
            i = next(idx for idx, e in enumerate(edges) if e in closed)
        if i == 0:
            root.remove(v)
            n_dropped += 1
            continue
        key = (edges[i - 1], edges[-1])
        ok = cache.get(key)
        if ok is None:
            ok = cache[key] = reachable(adj, *key, closed)
        if ok:
            continue   # the live rerouter will detour this one fine
        route_el.set("edges", " ".join(edges[:i]))
        n_truncated += 1
    tree.write(out_path, xml_declaration=True, encoding="UTF-8")
    return n_truncated, n_dropped


def demand_variants() -> list[Path]:
    """Calibrated route sets — q50 plus (if built) the q10/q90 direction-
    split variants. Monte Carlo seeds are spread over them so the seed
    spread — and the confidence — includes direction uncertainty."""
    paths = [SUMO_DIR / "calibrated.rou.xml"]
    for suffix in ("_v1", "_v2"):
        p = SUMO_DIR / f"calibrated{suffix}.rou.xml"
        if p.exists():
            paths.append(p)
    return paths


def run_sumo(seed: int, route_path: Path, add_paths: list[Path],
             duration_s: int, home: Path, micro: bool = False,
             metrics: bool = False) -> dict[str, Path] | None:
    # cwd=SUMO_DIR so the edgeData output file (relative in the additional
    # file) lands in sumo/ — inputs must therefore be absolute paths.
    # Mesoscopic by default: our product is 15-min edge flows, which does not
    # need microscopic car-following — meso is ~20x faster (whole-day seed:
    # minutes → seconds), which is what makes interactive closures possible.
    cmd = [
        str(home / "bin" / "sumo"),
        # meso junction control: LIMITED mode only, not fully off. Full
        # control models signal delays from GUESSED (not real) timing plans
        # and measurably throttles below the flows reality (107-N delivery
        # 0.57 with plain junction-control, 0.92 without — both re-verified
        # 2026-07-06, along with --tls.default-type actuated and
        # --meso-tls-penalty, neither of which changed the result: SUMO's
        # mesoscopic engine doesn't model actuated programs at all, and the
        # penalty had no measurable effect either). --meso-junction-control
        # .limited only engages control at junctions actually approaching
        # saturation ("this prevents faulty traffic lights from hindering
        # flow in low-traffic situations" — SUMO docs) — re-measured
        # delivery identical to fully-off (0.822 mean either way) at today's
        # demand, so it costs nothing, while still being ready to model a
        # signal's real capacity limit if a scenario ever pushes a junction
        # into genuine saturation (e.g. a closure that concentrates flow).
        # Re-enable plain (unlimited) junction control when the city
        # provides real signal timings.
        *([] if micro else ["--mesosim", "true",
                            "--meso-junction-control", "true",
                            "--meso-junction-control.limited", "true"]),
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        "-a", ",".join(str(p.resolve()) for p in add_paths),
        "--seed", str(seed),
        "--begin", "0",
        # generous flush: meso insertion queues delay departures (measured
        # ~170 s avg backlog); a short flush silently drops the tail
        "--end", str(duration_s + 3600),
        "--no-step-log", "true",
        "--no-warnings", "true",
        # Vehicles whose destination IS the closed edge have no valid route —
        # drop them instead of aborting (standard for closure studies).
        "--ignore-route-errors", "true",
    ]
    metric_paths = None
    if metrics:
        # Deliberately opt-in: interactive closures only need edgeData and
        # retain their current fast command path. The stem makes per-seed,
        # per-demand-variant output names deterministic for batch callers.
        stem = f"metrics_{route_path.stem}_{seed}"
        metric_paths = {
            "tripinfo": SUMO_DIR / f"{stem}_tripinfo.xml",
            "statistics": SUMO_DIR / f"{stem}_statistics.xml",
            "summary": SUMO_DIR / f"{stem}_summary.xml",
        }
        cmd.extend([
            "--tripinfo-output", str(metric_paths["tripinfo"].resolve()),
            "--tripinfo-output.write-unfinished", "true",
            "--statistic-output", str(metric_paths["statistics"].resolve()),
            # Summary's waiting count is supporting diagnostics, not a queue
            # metric; SUMO's queue-output remains experimental.
            "--summary-output", str(metric_paths["summary"].resolve()),
            "--summary-output.period", "900",
        ])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        sys.exit(f"sumo timed out after {SUMO_TIMEOUT_S}s (seed {seed})")
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"sumo failed (seed {seed})")
    return metric_paths


def export_trajectories(name: str, route_path: Path, closure_add: list[Path],
                        duration_s: int, home: Path,
                        web_edges: set[str], micro: bool = False) -> str | None:
    """One extra SUMO run with vehroute exit-times → a compact per-vehicle
    edge-timeline the web animates: EVERY DOT IS A REAL SIMULATED VEHICLE
    with its origin, destination, route and congestion-accurate timing.

    Format (indices into a shared edge list, times in whole seconds):
      {"edges": [...], "vehicles": [{"d": depart, "e": [i...], "x": [t...]}]}
    """
    vr_file = SUMO_DIR / f"vehroutes_{name}.xml"
    cmd = [
        str(home / "bin" / "sumo"),
        *([] if micro else ["--mesosim", "true",
                            "--meso-junction-control", "true",
                            "--meso-junction-control.limited", "true"]),
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        *(("-a", ",".join(str(p.resolve()) for p in closure_add))
          if closure_add else ()),
        "--vehroute-output", vr_file.name,
        "--vehroute-output.exit-times", "true",
        "--begin", "0", "--end", str(duration_s + 3600),
        "--no-step-log", "true", "--no-warnings", "true",
        "--ignore-route-errors", "true", "--seed", "1000",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"sumo (trajectory export) timed out after {SUMO_TIMEOUT_S}s")
        return None
    if res.returncode != 0:
        print(res.stderr[-800:])
        return None

    edge_index: dict[str, int] = {}
    vehicles = []
    for veh in ET.parse(vr_file).getroot().iter("vehicle"):
        route = veh.find("route")
        if route is None or not route.get("exitTimes"):
            continue
        edges = route.get("edges").split()
        exits = [int(float(t)) for t in route.get("exitTimes").split()]
        if len(edges) != len(exits):
            continue
        # keep only edges the map can draw (all net edges are in the geojson,
        # but guard against internal/unknown ids)
        idxs = []
        for e in edges:
            if e not in edge_index:
                if e not in web_edges:
                    idxs = None
                    break
                edge_index[e] = len(edge_index)
            idxs.append(edge_index[e])
        if not idxs:
            continue
        vehicles.append({"d": int(float(veh.get("depart"))),
                         "e": idxs, "x": exits})

    vehicles.sort(key=lambda v: v["d"])
    inv = [None] * len(edge_index)
    for e, i in edge_index.items():
        inv[i] = e
    traj_name = f"{name}_traj.json"
    with open(OUT_DIR / traj_name, "w") as f:
        json.dump({"edges": inv, "vehicles": vehicles}, f,
                  separators=(",", ":"))
    size_mb = (OUT_DIR / traj_name).stat().st_size / 1e6
    print(f"  trajectories: {len(vehicles)} vehicles → {traj_name} "
          f"({size_mb:.1f} MB)")
    return traj_name


def parse_edgedata(path: Path, n_intervals: int) -> dict[str, np.ndarray]:
    """{edge_id: array of 'entered' counts per 15-min interval}."""
    flows: dict[str, np.ndarray] = {}
    root = ET.parse(path).getroot()
    for interval in root.findall("interval"):
        i = int(float(interval.get("begin")) // 900)
        if i >= n_intervals:
            continue
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            entered = float(edge.get("entered") or 0)
            if eid not in flows:
                flows[eid] = np.zeros(n_intervals)
            flows[eid][i] = entered
    return flows


def main() -> None:
    args = parse_args()
    home = sumo_home()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    duration_s  = n_intervals * 900
    sig = demand_signature(meta)

    try:
        closures = structured_closures(args.closure, args.close,
                                       meta["epoch_sim"], duration_s)
    except ValueError as exc:
        sys.exit(str(exc))
    close_edges = list(dict.fromkeys(c["edge_id"] for c in closures))

    prior, names = load_geojson_meta()
    for ce in close_edges:
        if ce not in prior:
            sys.exit(f"closure {ce}: not an edge in network.geojson")

    if args.name:
        name = args.name
    elif close_edges:
        name = "close_" + "+".join(close_edges)
        if args.closure:
            # Two windows on the same edge are distinct scenarios; unlike the
            # legacy whole-run name, include their structured identity.
            window_hash = hashlib.sha1(
                json.dumps(closures, sort_keys=True).encode()).hexdigest()[:8]
            name = f"{name}_{window_hash}"
        if len(name) > 80:   # many edges → keep the filename sane
            name = f"close_{len(close_edges)}edges_" + \
                   hashlib.sha1("+".join(close_edges).encode()).hexdigest()[:8]
    else:
        name = "baseline"

    if close_edges:
        streets = sorted({names[ce] for ce in close_edges})
        label = "Avstängning: " + ", ".join(streets)
    else:
        label = "Baslinje (ingen avstängning)"

    # Edge list for the rerouter (net edge IDs = plain edge IDs, no internals)
    net_edges = [e.get("id") for e in ET.parse(NET_PATH).getroot().findall("edge")
                 if not e.get("function")]

    print(f"Scenario '{name}'  ({label})  —  {args.seeds} seeds × {n_intervals} × 15 min")

    variants = demand_variants()
    if len(variants) > 1:
        print(f"  {len(variants)} demand variants (q50 + direction-split bounds)")

    closure_add: list[Path] = []
    if close_edges:
        rerouter_edges = edges_near(close_edges, REROUTER_RADIUS_M)
        print(f"  rerouter on {len(rerouter_edges)} edges within "
              f"{REROUTER_RADIUS_M} m of the closure")
        cpath = SUMO_DIR / f"closure_{name}.add.xml"
        write_closure_additional(cpath, closures, rerouter_edges)
        closure_add = [cpath]

        # Vehicles with no detour around the closure at all can't be fixed
        # by the runtime rerouter above (see truncate_stranded_vehicles) —
        # shortened/dropped here so they never get simulated past the
        # closure, instead of relying on sumo's stuck-vehicle teleport to
        # hide them after the fact.
        adj = build_edge_graph(set(close_edges))
        freeflow = edge_freeflow_times()
        filtered_variants = []
        n_truncated = n_dropped = 0
        for vp in variants:
            fp = SUMO_DIR / f"{vp.stem}_{name}.rou.xml"
            t, d = truncate_stranded_vehicles(
                vp, close_edges, fp, adj,
                # Preserve the exact tested function path for legacy --close.
                closures=closures if args.closure else None,
                edge_travel_s=freeflow)
            n_truncated += t
            n_dropped += d
            filtered_variants.append(fp)
        if n_truncated:
            print(f"  truncated {n_truncated} vehicle(s) with no detour around "
                  f"the closure to end just short of it (parked there instead "
                  f"of continuing) rather than sitting stuck and getting "
                  f"teleported through it at end of run")
        if n_dropped:
            print(f"  dropped {n_dropped} vehicle(s) that couldn't even "
                  f"depart with the closure in place")
        variants = filtered_variants

    per_seed: list[dict[str, np.ndarray]] = []
    for s in range(args.seeds):
        seed = 1000 + s
        route_path = variants[s % len(variants)]
        ed_file  = SUMO_DIR / f"edgedata_{name}_{seed}.xml"
        add_path = SUMO_DIR / f"additional_{name}_{seed}.add.xml"
        write_edgedata_additional(add_path, ed_file, duration_s)
        run_sumo(seed, route_path, [add_path] + closure_add, duration_s, home,
                 micro=args.micro)
        per_seed.append(parse_edgedata(ed_file, n_intervals))
        print(f"  seed {seed} ({route_path.name}): "
              f"{len(per_seed[-1])} edges with traffic")

    # ── Aggregate: mean flows + Monte Carlo confidence ─────────────────────────
    web_edges = set(prior)   # only edges the map can draw
    all_ids   = set().union(*per_seed) & web_edges

    flows_out: dict[str, list[int]] = {}
    conf_out:  dict[str, float] = {}
    for eid in sorted(all_ids):
        stack = np.stack([ps.get(eid, np.zeros(n_intervals)) for ps in per_seed])
        mean  = stack.mean(axis=0)
        flows_out[eid] = [int(round(v)) for v in mean]

        busy = mean > 2           # CV is meaningless for near-zero flows
        cv   = float((stack.std(axis=0)[busy] / mean[busy]).mean()) if busy.any() else 0.0
        conf_out[eid] = round(prior[eid] * float(np.exp(-cv)), 3)

    window_label = demand_window_label(meta)

    traj_name = None
    if not args.no_trajectories:
        traj_name = export_trajectories(name, variants[0], closure_add,
                                        duration_s, home, web_edges,
                                        micro=args.micro)

    payload = {
        "epoch":            meta["epoch_sim"],
        "interval_minutes": 15,
        "n_quarters":       n_intervals,
        "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
        "trajectories":     traj_name,
        "scenario": {
            "name": name, "label": label,
            "closed_edges": close_edges,
            "closures": closures,
            "window": window_label,
            "source": meta.get("source", "historical"),
            **({"date": meta["date"], "begin": meta["begin"], "end": meta["end"]}
               if "date" in meta else
               {"start_date": meta["start_date"],
                "end_date_exclusive": meta["end_date_exclusive"],
                "days": meta["days"]}),
            "seeds": args.seeds,
            "demand_signature": sig,
        },
        "flows":      flows_out,
        "confidence": conf_out,
    }
    out_path = OUT_DIR / f"{name}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote {out_path}  ({len(flows_out)} edges)")

    # ── Manifest ───────────────────────────────────────────────────────────────
    index_path = OUT_DIR / "index.json"
    index = json.load(open(index_path)) if index_path.exists() else {"scenarios": []}
    index = index_for_current_demand(index, sig)
    index["scenarios"] = [s for s in index["scenarios"] if s["name"] != name]
    src_tag = " · Prognos" if meta.get("source") == "forecast" else ""
    index["scenarios"].append({
        "name": name, "label": label, "file": f"{name}.json",
        "closed_edges": close_edges,
        "closures": closures,
        "demand_signature": sig,
        "window": f"{window_label}{src_tag}",
    })
    index["scenarios"].sort(key=lambda s: s["name"])
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Updated {index_path}  ({len(index['scenarios'])} scenarios)")


if __name__ == "__main__":
    main()
