"""
Build a SUMO network from the exact OSM graph snapshot (web/data/graph.graphml).

Run:
  python3 build_sumo_net.py          # or: make sumo-net

Why plain XML instead of importing OSM into netconvert directly:
  SUMO edge IDs become IDENTICAL to our edge IDs ("u_v_k") — the same ID space
  as network.geojson and flows.json. Scenario output maps back to the web map
  with zero geometry matching, and closing edge "60786979_3575001205_0" in
  SUMO closes exactly the edge the user clicked.

The FULL downloaded graph is used (~2 250 edges), not the 400 m display clip —
rerouting around closures needs the real alternative streets. The web app
simply ignores edges it has no geometry for.

Coordinates are projected WGS84 → SWEREF99 12 00 (EPSG:3007, metric) for SUMO.

Writes:
  sumo/plain.nod.xml, sumo/plain.edg.xml   — intermediate plain XML
  sumo/net.net.xml                          — the SUMO network (via netconvert)
"""

from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path
from xml.sax.saxutils import quoteattr

from sumo_runtime import sumo_home
from sumo_network_metadata import write_metadata

GRAPHML_PATH = Path("web/data/graph.graphml")
SUMO_DIR     = Path("sumo")

# Fallbacks when OSM tags are missing.  Speeds in km/h.
DEFAULT_SPEED_KMH = {
    "motorway": 100, "motorway_link": 60,
    "trunk": 80,     "trunk_link": 50,
    "primary": 60,   "primary_link": 50,
    "secondary": 50, "secondary_link": 50,
    "tertiary": 50,  "tertiary_link": 50,
    "residential": 30, "living_street": 20, "unclassified": 40,
}
DEFAULT_LANES = {
    "motorway": 2, "trunk": 2, "primary": 2,
    "secondary": 1, "tertiary": 1,
}

_SPEED_RE = re.compile(
    r"(?<!\d)(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>km/?h|kph|kmh|mph|m/s|knots?)?",
    re.IGNORECASE,
)
_SPEED_UNIT_TO_KMH = {
    "km/h": 1.0, "kmh": 1.0, "kph": 1.0,
    "mph": 1.609344, "m/s": 3.6,
    "knot": 1.852, "knots": 1.852,
}
_MIN_SPEED_KMH = 5.0
_MAX_SPEED_KMH = 130.0


def _tag_values(value: object) -> list[object]:
    """Return OSMnx scalar/list tags as a deterministic list."""
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def _parse_numeric_tag(value: object) -> int | None:
    """Parse the first positive integer in an OSM numeric tag."""
    for item in _tag_values(value):
        match = re.search(r"(?<!\d)(\d+)", str(item))
        if match:
            parsed = int(match.group(1))
            if parsed > 0:
                return parsed
    return None


def _is_oneway(value: object) -> bool:
    """Recognise the OSM yes/no forms, including list-valued OSMnx tags."""
    values = {str(item).strip().lower() for item in _tag_values(value)}
    return bool(values & {"yes", "true", "1", "-1"})


def _speed_kmh(value: object) -> float | None:
    """Parse one OSM maxspeed value without accepting implausible speeds.

    OSM permits semicolon-separated and conditional values.  A directed SUMO
    edge cannot safely represent every conditional lane value, so the first
    explicit value is used and the condition is deliberately ignored.  The
    old implementation concatenated digits (``30;50`` -> 3050), which could
    create effectively teleporting vehicles after a network rebuild.
    """
    for item in _tag_values(value):
        text = str(item).strip()
        if not text:
            continue
        match = _SPEED_RE.search(text.replace(",", "."))
        if not match:
            continue
        number = float(match.group("value"))
        unit = (match.group("unit") or "km/h").lower().replace(" ", "")
        factor = _SPEED_UNIT_TO_KMH.get(unit)
        if factor is None:
            continue
        kmh = number * factor
        if _MIN_SPEED_KMH <= kmh <= _MAX_SPEED_KMH:
            return kmh
    return None


def scalar(val: object) -> object:
    return val[0] if isinstance(val, list) else val


def parse_speed_ms(data: dict) -> float:
    """maxspeed tag (km/h) → m/s, falling back on highway-type defaults."""
    parsed = _speed_kmh(data.get("maxspeed"))
    if parsed is not None:
        return parsed / 3.6
    hw = str(scalar(data.get("highway", "")) or "")
    return DEFAULT_SPEED_KMH.get(hw, 50) / 3.6


def parse_lanes(data: dict) -> int:
    """Per-direction lane count. OSM 'lanes' counts BOTH directions on
    two-way ways, so halve it unless the way is one-way.  Explicit directed
    lane tags are preferred when a total is absent; when both directions are
    present and the edge orientation is unavailable, the aggregate `lanes`
    tag remains the conservative source instead of copying one direction to
    both SUMO edges."""
    raw = data.get("lanes")
    hw  = str(scalar(data.get("highway", "")) or "")
    total = _parse_numeric_tag(raw)
    oneway = _is_oneway(data.get("oneway"))
    if total is None:
        directed = _parse_numeric_tag(data.get("lanes:forward"))
        if directed is None:
            directed = _parse_numeric_tag(data.get("lanes:backward"))
        if directed is not None:
            return min(directed, 4)
    if total is not None:
        lanes = total if oneway else max(1, total // 2)
        return min(lanes, 4)
    return DEFAULT_LANES.get(hw, 1)


def main() -> None:
    # Network construction is the only path that needs these heavyweight
    # dependencies.  Keep them out of scenario/demand import paths.
    import osmnx as ox
    from pyproj import Transformer
    from network_audit import write_audit

    SUMO_DIR.mkdir(exist_ok=True)

    print(f"Loading {GRAPHML_PATH} …")
    G = ox.load_graphml(GRAPHML_PATH)
    print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    t = Transformer.from_crs("EPSG:4326", "EPSG:3007", always_xy=True)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    nod_path = SUMO_DIR / "plain.nod.xml"
    with open(nod_path, "w") as f:
        f.write("<nodes>\n")
        for n, d in G.nodes(data=True):
            x, y = t.transform(d["x"], d["y"])
            f.write(f'  <node id="{n}" x="{x:.2f}" y="{y:.2f}"/>\n')
        f.write("</nodes>\n")

    # ── Edges ──────────────────────────────────────────────────────────────────
    edg_path  = SUMO_DIR / "plain.edg.xml"
    n_written = 0
    n_selfloop = 0
    with open(edg_path, "w") as f:
        f.write("<edges>\n")
        for u, v, k, data in G.edges(keys=True, data=True):
            if u == v:            # simplified roundabout remnants — netconvert
                n_selfloop += 1   # rejects zero-length self-loops
                continue
            edge_id = f"{u}_{v}_{k}"
            speed   = parse_speed_ms(data)
            lanes   = parse_lanes(data)

            if "geometry" in data:
                pts = [t.transform(lon, lat) for lon, lat in data["geometry"].coords]
            else:
                pts = [
                    t.transform(G.nodes[u]["x"], G.nodes[u]["y"]),
                    t.transform(G.nodes[v]["x"], G.nodes[v]["y"]),
                ]
            shape = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)

            f.write(
                f'  <edge id={quoteattr(edge_id)} from="{u}" to="{v}" '
                f'numLanes="{lanes}" speed="{speed:.2f}" shape="{shape}"/>\n'
            )
            n_written += 1
        f.write("</edges>\n")
    print(f"  Wrote {n_written} edges ({n_selfloop} self-loops skipped)")

    # ── netconvert ─────────────────────────────────────────────────────────────
    home = sumo_home()
    net_path = SUMO_DIR / "net.net.xml"
    cmd = [
        str(home / "bin" / "netconvert"),
        "-n", str(nod_path),
        "-e", str(edg_path),
        "-o", str(net_path),
        "--tls.guess", "true",          # OSM signal tags are lost in graphml
        "--geometry.remove", "false",   # keep shapes 1:1 with the map
        "--no-warnings", "true",
    ]
    print("Running netconvert …")
    res = subprocess.run(cmd, capture_output=True, text=True,
                         env={"SUMO_HOME": str(home), "PATH": "/usr/bin:/bin"})
    if res.returncode != 0:
        print(res.stderr[-3000:])
        sys.exit("netconvert failed")

    size_kb = net_path.stat().st_size / 1024
    print(f"Wrote {net_path}  ({size_kb:.0f} KB)")
    metadata_path = SUMO_DIR / "network_metadata.json"
    metadata = write_metadata(net_path, metadata_path)
    print(f"Wrote {metadata_path}  ({len(metadata['edges'])} indexed edges)")
    audit_path = SUMO_DIR / "network_audit.json"
    audit = write_audit(G, net_path, audit_path)
    print(f"Wrote {audit_path}  ({len(audit['edges'])} audited edges)")
    print("SUMO edge IDs are identical to network.geojson/flows.json edge IDs.")


if __name__ == "__main__":
    main()
