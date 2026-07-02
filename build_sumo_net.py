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
from pathlib import Path
from xml.sax.saxutils import quoteattr

import osmnx as ox
from pyproj import Transformer

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


def sumo_home() -> Path:
    """Locate the eclipse-sumo pip package (binaries in bin/, tools in tools/)."""
    import sumo  # pip install eclipse-sumo
    return Path(sumo.__file__).parent


def scalar(val: object) -> object:
    return val[0] if isinstance(val, list) else val


def parse_speed_ms(data: dict) -> float:
    """maxspeed tag (km/h) → m/s, falling back on highway-type defaults."""
    raw = scalar(data.get("maxspeed"))
    if raw is not None:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if digits:
            return int(digits) / 3.6
    hw = str(scalar(data.get("highway", "")) or "")
    return DEFAULT_SPEED_KMH.get(hw, 50) / 3.6


def parse_lanes(data: dict) -> int:
    """Per-direction lane count. OSM 'lanes' counts BOTH directions on
    two-way ways, so halve it unless the way is one-way."""
    raw = scalar(data.get("lanes"))
    hw  = str(scalar(data.get("highway", "")) or "")
    if raw is not None:
        digits = "".join(ch for ch in str(raw).split(";")[0] if ch.isdigit())
        if digits:
            total  = int(digits)
            oneway = data.get("oneway") in (True, "True", "true", "yes")
            lanes  = total if oneway else max(1, total // 2)
            return min(lanes, 4)
    return DEFAULT_LANES.get(hw, 1)


def main() -> None:
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
    print("SUMO edge IDs are identical to network.geojson/flows.json edge IDs.")


if __name__ == "__main__":
    main()
