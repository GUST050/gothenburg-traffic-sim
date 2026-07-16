"""
Build network.geojson and flows.json from raw Göteborg sensor CSVs.

Usage:
  python3 build_data.py \
    --data_dir ~/Downloads/"Data till Chalmers_20260618" \
    --coords   ~/Downloads/Mätpunkter_koordinater.csv

Outputs (default web/data/):
  network.geojson  — road graph (LineStrings) with sensor metadata
  flows.json       — { epoch, interval_minutes, flows: { edge_id: [count|null, ...] } }

The flowAt(edgeId, quarterIndex) contract is the only interface the web
renderer and future AI models use.  Do not change the output format without
updating provider.js and any downstream model code.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import warnings
from collections import defaultdict
from pathlib import Path

import osmnx as ox
import pandas as pd
from pyproj import Transformer

from traffic_sim.intake.sensors import load_registry

warnings.filterwarnings("ignore", message=".*OpenSSL.*", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ── Constants ──────────────────────────────────────────────────────────────────

EPOCH          = pd.Timestamp("2025-01-01T00:00:00")
INTERVAL       = pd.Timedelta(minutes=15)
N_QUARTERS     = 365 * 96   # 35 040 — 2025 is not a leap year
SNAP_WARN_M    = 100         # print a warning if snap midpoint is this far from sensor
CARRIAGEWAY_M  = 40          # max distance to search for the opposite carriageway

# Inner-city canvas (south, west, north, east) — decided 2026-07-05.
# Covers Vallgraven→Gårda west–east and the river→Krokslätt north–south.
INNER_CITY_BBOX = (57.665, 11.920, 57.722, 12.020)

# Simulation confidence: confidence = exp(-d² / 2σ²) where d = distance from
# the edge midpoint to the NEAREST sensor.  1.0 at a sensor, ~0.6 at σ metres,
# ~0.1 at 2σ.  Written to network.geojson so the web app and future
# ScenarioProvider can show how trustworthy a simulated flow is per edge.
#
# σ is derived from the leakage-free LOSO report when available:
#   web/data/loso_report.json gives held-out GEH<5 accuracy at the measured
#   sensor edges, and the previous network.geojson gives the measured-edge
#   positions used to compute each held-out station's distance to the nearest
#   remaining station.  Each (distance, accuracy) pair implies one σ via
#   accuracy = exp(-d² / 2σ²); the deployed σ is the robust median of those
#   implied values.
#
# Honest limitation: all current LOSO pairs are near-field checks inside the
# two dense sensor clusters (roughly <300 m from the nearest remaining sensor).
# Edges kilometres away are still extrapolation; they are now anchored in real
# held-out validation instead of this old guessed fallback.
CONF_SIGMA_FALLBACK_M = 250.0
CONF_SIGMA_CLIP       = (0.02, 0.98)

# ALL drivable roads inside the clip radius are kept as background edges —
# residential, living_street etc. included (network_type="drive" already
# excludes footways and parking aisles). The area is small enough (two
# clusters × clip_radius) that edge count stays manageable.

# ── Required CSV columns (after name normalisation) ────────────────────────────

REQUIRED_COLS = {"matplats", "level", "datum", "tid", "count"}


# ── Pure geometry helpers ──────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R  = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    a  = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
          + math.cos(p1) * math.cos(p2)
          * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def bearing_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing in radians from (lat1,lon1) → (lat2,lon2). North = 0, East = π/2.

    Longitude degrees are compressed by cos(lat) (~0.53 at Gothenburg's 57.7°N);
    without the correction all bearings skew toward north–south and the angle
    tolerance in find_antiparallel_edge is applied unevenly.
    """
    dlat = lat2 - lat1
    dlon = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.atan2(dlon, dlat)


def eid(u: int, v: int, k: int = 0) -> str:
    return f"{u}_{v}_{k}"


def geojson_edge_midpoint(feature: dict) -> tuple[float, float]:
    """Return (lat, lon) midpoint from a network.geojson LineString feature."""
    coords = feature["geometry"]["coordinates"]
    if not coords:
        raise ValueError("LineString has no coordinates")
    lon1, lat1 = coords[0]
    lon2, lat2 = coords[-1]
    return (lat1 + lat2) / 2, (lon1 + lon2) / 2


def empirical_conf_sigma_m(out_dir: Path) -> tuple[float, str]:
    """Fit the confidence distance-decay sigma from LOSO, or return fallback.

    The fit intentionally stays simple and robust for the tiny validation set:
    every held-out measured edge contributes one implied sigma, and the median
    is used instead of least squares so one bad fold cannot dominate the map.
    """
    loso_path = out_dir / "loso_report.json"
    network_path = out_dir / "network.geojson"
    if not loso_path.exists():
        return CONF_SIGMA_FALLBACK_M, (
            f"fallback guessed sigma {CONF_SIGMA_FALLBACK_M:.1f} m "
            f"(missing {loso_path})"
        )
    if not network_path.exists():
        return CONF_SIGMA_FALLBACK_M, (
            f"fallback guessed sigma {CONF_SIGMA_FALLBACK_M:.1f} m "
            f"(missing previous {network_path})"
        )

    try:
        loso = json.loads(loso_path.read_text())
        network = json.loads(network_path.read_text())
        features_by_id = {
            f["properties"]["id"]: f
            for f in network.get("features", [])
            if f.get("geometry", {}).get("type") == "LineString"
        }

        stations = loso["stations"]
        station_pos: dict[str, tuple[float, float]] = {}
        for sid, station in stations.items():
            pts = []
            for edge_id in station["edges"]:
                pts.append(geojson_edge_midpoint(features_by_id[edge_id]))
            station_pos[sid] = (
                sum(lat for lat, _ in pts) / len(pts),
                sum(lon for _, lon in pts) / len(pts),
            )

        implied_sigmas: list[float] = []
        for sid, station in stations.items():
            if len(station_pos) < 2:
                continue
            nearest_remaining_m = min(
                haversine_m(*station_pos[sid], *pos)
                for other_sid, pos in station_pos.items()
                if other_sid != sid
            )
            for edge in station["edges"].values():
                accuracy = max(
                    CONF_SIGMA_CLIP[0],
                    min(CONF_SIGMA_CLIP[1], edge["geh_ok_pct"] / 100),
                )
                implied_sigmas.append(
                    nearest_remaining_m / math.sqrt(-2 * math.log(accuracy))
                )

        if not implied_sigmas:
            raise ValueError("LOSO report did not yield any confidence points")
        sigma = statistics.median(implied_sigmas)
        return sigma, (
            f"empirical LOSO sigma {sigma:.1f} m from {len(implied_sigmas)} "
            f"near-field held-out edge points ({loso_path})"
        )
    except Exception as exc:
        return CONF_SIGMA_FALLBACK_M, (
            f"fallback guessed sigma {CONF_SIGMA_FALLBACK_M:.1f} m "
            f"(could not fit from LOSO: {exc})"
        )


# ── Sensor registry ───────────────────────────────────────────────────────────
# Direction semantics are maintained in data_in/sensors.json.  Keep these
# compatibility names for callers/tests that import build_data, but derive
# them from the validated registry so adding a station never requires a
# source-code edit.
SENSOR_REGISTRY_PATH = Path("data_in/sensors.json")
_SENSOR_REGISTRY = load_registry(SENSOR_REGISTRY_PATH)
SENSOR_MEASURED_DIRECTION: dict[str, str] = {
    sensor_id: record.level
    for sensor_id, record in _SENSOR_REGISTRY.records.items()
}
MANUAL_SNAPS: dict[str, str] = _SENSOR_REGISTRY.manual_snaps()

# Compass letter → travel bearing. The snapped directed edge MUST point this
# way, or the counts end up on the opposite direction of travel.
COMPASS_BEARING_DEG = {
    "N": 0, "NO": 45, "O": 90, "Ö": 90, "SO": 135,
    "S": 180, "SV": 225, "V": 270, "NV": 315,
}


def edge_bearing_deg(G, u: int, v: int) -> float:
    """Travel bearing of directed edge u→v in degrees (0=N, 90=E)."""
    return math.degrees(bearing_rad(
        G.nodes[u]["y"], G.nodes[u]["x"], G.nodes[v]["y"], G.nodes[v]["x"]
    )) % 360


def ang_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def true_snap_dist_m(G, u: int, v: int, k: int, lat: float, lon: float) -> float:
    """Distance from a point to the edge GEOMETRY — never the midpoint, which
    wrongly reports ~100 m for a sensor sitting ON a long edge (1074 taught
    us this: 1 m true distance, 130 m midpoint distance)."""
    from dirsplit.geo import point_to_polyline_m
    d = G.get_edge_data(u, v, k)
    if "geometry" in d:
        coords = list(d["geometry"].coords)
    else:
        coords = [(G.nodes[u]["x"], G.nodes[u]["y"]),
                  (G.nodes[v]["x"], G.nodes[v]["y"])]
    return point_to_polyline_m(lat, lon, coords)


def scalar(val: object) -> object:
    """Return val[0] if val is a list (OSM name/highway can be lists)."""
    return val[0] if isinstance(val, list) else val


# ── OSM graph helpers ──────────────────────────────────────────────────────────

def edge_midpoint(G, u: int, v: int, k: int) -> tuple[float, float]:
    """Return (lat, lon) of the midpoint of edge u→v (key k).

    Uses the average of the two endpoint nodes rather than the vertex at the
    index midpoint of the stored geometry.  Long OSM edges can have many
    intermediate vertices unevenly distributed along the road — picking the
    middle vertex by index can land far from the true geographic centre,
    which breaks the 40 m distance check in find_antiparallel_edge.
    """
    return (
        (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2,
        (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2,
    )


def find_antiparallel_edge(
    G,
    u: int, v: int, k: int,
    sensor_lat: float, sensor_lon: float,
    max_dist_m: float = CARRIAGEWAY_M,
    max_angle_deg: float = 30,
) -> tuple[int, int, int] | None:
    """
    Find the nearest OSM edge within max_dist_m that runs in roughly the
    opposite direction to u→v.

    This handles divided carriageways where the two roads are separate OSM
    edges (different node IDs) rather than a simple G.has_edge(v, u) reverse.

    Returns (u2, v2, k2) or None if nothing qualifies.
    """
    u_lat, u_lon = G.nodes[u]["y"], G.nodes[u]["x"]
    v_lat, v_lon = G.nodes[v]["y"], G.nodes[v]["x"]
    target = bearing_rad(u_lat, u_lon, v_lat, v_lon) + math.pi

    best: tuple[int, int, int] | None = None
    best_dist = max_dist_m

    for eu, ev, ek in G.edges(keys=True):
        if (eu, ev, ek) == (u, v, k):
            continue
        mlat, mlon = edge_midpoint(G, eu, ev, ek)
        dist = haversine_m(sensor_lat, sensor_lon, mlat, mlon)
        if dist >= best_dist:
            continue
        eu_lat, eu_lon = G.nodes[eu]["y"], G.nodes[eu]["x"]
        ev_lat, ev_lon = G.nodes[ev]["y"], G.nodes[ev]["x"]
        b    = bearing_rad(eu_lat, eu_lon, ev_lat, ev_lon)
        diff = abs(b - target) % (2 * math.pi)
        if diff > math.pi:
            diff = 2 * math.pi - diff
        if math.degrees(diff) <= max_angle_deg:
            best_dist = dist
            best = (eu, ev, ek)

    return best


def _find_second_edge(
    G,
    u: int, v: int, k: int,
    sensor_id: str,
    sensor_lat: float, sensor_lon: float,
) -> str:
    """
    For a Total sensor on edge u→v, return the edge_id of the opposite direction.

    Tries in order:
      1. G.has_edge(v, u)         — simple two-way road (single OSM way)
      2. find_antiparallel_edge   — divided road (two separate OSM carriageways)

    Raises ValueError if neither finds a candidate, so the build fails loudly
    instead of silently producing wrong output.
    """
    if G.has_edge(v, u):
        return eid(v, u, min(G[v][u].keys()))

    opp = find_antiparallel_edge(G, u, v, k, sensor_lat, sensor_lon)
    if opp is not None:
        return eid(*opp)

    raise ValueError(
        f"Sensor {sensor_id} (Total) on edge {eid(u, v, k)}: no reverse or "
        f"antiparallel OSM edge found within {CARRIAGEWAY_M} m.\n"
        f"If OSM has the road wrongly marked one-way, add to MANUAL_SNAPS.\n"
        f"If the road is genuinely one-way, the sensor level may be wrong."
    )


def _bidir_edges(edge_sensor: dict[str, str]) -> set[str]:
    """
    Return the set of edge IDs that belong to a bidirectional sensor pair.
    Any sensor with ≥ 2 edges tagged is bidirectional — works for simple
    two-way roads, divided carriageways, and any future geometry.
    """
    sensor_edges: dict[str, list[str]] = defaultdict(list)
    for edge_id, sid in edge_sensor.items():
        sensor_edges[sid].append(edge_id)
    result: set[str] = set()
    for eids in sensor_edges.values():
        if len(eids) >= 2:
            result.update(eids)
    return result


# ── Data loading ───────────────────────────────────────────────────────────────

def load_raw(data_dir: Path, coords_path: Path | None = None) -> pd.DataFrame:
    """Load and normalise all sensor CSV files in data_dir.

    coords_path is excluded explicitly (by resolved path, not just filename
    pattern) — data_in/'s drop-folder workflow puts the coordinates CSV in
    the SAME directory as the sensor CSVs, and this glob would otherwise
    concatenate it in too. It has no level/datum/tid/count columns, so its
    rows join in with those fields null; the REQUIRED_COLS check only
    verifies column presence across the whole concatenated frame, not
    per-row completeness, so this silently corrupted sensor_level for any
    sensor not already covered by the validated registry. Found in a bug
    review 2026-07-10, independently verified."""
    frames = []
    coords_resolved = coords_path.expanduser().resolve() if coords_path else None
    for path in sorted(data_dir.glob("*.csv")):
        if coords_resolved is not None and path.resolve() == coords_resolved:
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")
    raw = pd.concat(frames, ignore_index=True)

    col_map: dict[str, str] = {}
    for c in raw.columns:
        lc = c.lower().replace(" ", "").replace("_", "")
        if "matplats" in lc or "mätplats" in lc:
            col_map[c] = "matplats"
        elif lc == "level":
            col_map[c] = "level"
        elif "datum" in lc or lc == "date":
            col_map[c] = "datum"
        elif "kvart" in lc or "tid" in lc or "time" in lc:
            col_map[c] = "tid"
        elif "antal" in lc or "count" in lc or "flöde" in lc or "flow" in lc:
            col_map[c] = "count"
    raw = raw.rename(columns=col_map)

    missing = REQUIRED_COLS - set(raw.columns)
    if missing:
        raise ValueError(
            f"CSV missing required columns after normalisation: {missing}\n"
            f"Found: {list(raw.columns)}"
        )

    raw["matplats"] = raw["matplats"].astype(str).str.strip()
    raw["level"]    = raw["level"].astype(str).str.strip()
    raw["count"]    = pd.to_numeric(raw["count"], errors="coerce")
    raw["ts"] = pd.to_datetime(
        raw["datum"].astype(str).str.strip().str[:10]
        + " "
        + raw["tid"].astype(str).str.strip(),
        dayfirst=False,
        errors="coerce",
    )

    bad = raw["ts"].isna().sum()
    if bad:
        print(f"  ⚠  {bad:,} rows with unparseable timestamps — skipped")

    return raw


def load_coords(coords_path: Path) -> dict[str, tuple[float, float]]:
    """Return {matplats: (lat_wgs84, lon_wgs84)} converted from SWEREF99 12 00."""
    df = pd.read_csv(coords_path, encoding="utf-8-sig", sep=None, engine="python")
    df.columns = df.columns.str.strip()

    col_map: dict[str, str] = {}
    for c in df.columns:
        lc = c.lower().replace(" ", "").replace("_", "")
        if "matplats" in lc or "mätplats" in lc:
            col_map[c] = "matplats"
        elif "latx" in lc:
            col_map[c] = "LatX"
        elif "longy" in lc:
            col_map[c] = "LongY"
    df = df.rename(columns=col_map)

    for need in ("matplats", "LatX", "LongY"):
        if need not in df.columns:
            raise ValueError(
                f"Coordinates file missing column {need!r}. Found: {list(df.columns)}"
            )

    df["matplats"] = df["matplats"].astype(str).str.strip()

    # Source columns are misleadingly named: LongY = easting (x), LatX = northing (y)
    t = Transformer.from_crs("EPSG:3007", "EPSG:4326", always_xy=True)
    lons, lats = t.transform(df["LongY"].to_numpy(), df["LatX"].to_numpy())

    return {
        mp: (float(lat), float(lon))
        for mp, lat, lon in zip(df["matplats"], lats, lons)
    }


# ── Quarter index ──────────────────────────────────────────────────────────────

def build_qi_series(ts_series: pd.Series) -> pd.Series:
    """Vectorised: timestamp → quarter index (0 … N_QUARTERS-1), None if out of range."""
    delta = (ts_series - EPOCH) / INTERVAL
    valid = delta.notna() & (delta >= 0) & (delta < N_QUARTERS)
    result = pd.Series([None] * len(ts_series), dtype=object, index=ts_series.index)
    result[valid] = delta[valid].round().astype(int)
    return result


# ── Snapping ───────────────────────────────────────────────────────────────────

def _parse_eid(edge_id: str) -> tuple[int, int, int]:
    u, v, k = edge_id.split("_", 2)
    return int(u), int(v), int(k)


def snap_sensors(
    G,
    sensors: list[str],
    lats: list[float],
    lons: list[float],
    sensor_level: dict[str, str],
) -> dict[str, str]:
    """
    Return edge_sensor: { edge_id → sensor_id }.

    Sensor label is ground truth about road geometry:
      "Total" → two-way traffic at this point — both directed edges are tagged.
                First tries the OSM reverse; falls back to finding the other
                carriageway within CARRIAGEWAY_M metres; fails loudly if neither works.
      "S"     → single direction — one edge only.
      MANUAL_SNAPS → use when auto-snap chooses the wrong road entirely.
    """
    auto_snapped = ox.nearest_edges(G, X=lons, Y=lats)
    edge_sensor: dict[str, str] = {}

    for i, sensor_id in enumerate(sensors):
        level = sensor_level.get(sensor_id, "?")
        snap  = MANUAL_SNAPS.get(sensor_id)

        if snap is not None:
            if snap in edge_sensor:
                print(f"  ⚠  {sensor_id}: MANUAL snap {snap} already claimed by {edge_sensor[snap]}")
            edge_sensor[snap] = sensor_id
            print(f"  {sensor_id}: MANUAL   {snap}  ({level})")
            if level == "Total":
                u_s, v_s, k_s = _parse_eid(snap)
                rev = _find_second_edge(
                    G, u_s, v_s, k_s,
                    sensor_id, lats[i], lons[i],
                )
                edge_sensor[rev] = sensor_id
                print(f"      ↔  {rev}")
            continue

        u, v, k = auto_snapped[i]

        # Direction-aware snap for single-direction sensors: if the sensor's
        # compass letter points opposite to the snapped edge, take the
        # reverse/antiparallel edge instead.
        want = COMPASS_BEARING_DEG.get(level)
        if want is not None:
            have = edge_bearing_deg(G, u, v)
            if ang_diff_deg(have, want) > 90:
                # Wider search than for Total pairs: divided carriageways can
                # sit further apart than CARRIAGEWAY_M (Skånegatan: 52 m).
                if G.has_edge(v, u):
                    rev = (v, u, min(G[v][u].keys()))
                else:
                    rev = find_antiparallel_edge(
                        G, u, v, k, lats[i], lons[i],
                        max_dist_m=2 * CARRIAGEWAY_M,
                    )
                if rev is None:
                    print(f"  ⚠  {sensor_id}: level '{level}' opposes snapped edge "
                          f"({have:.0f}°) but no opposite edge found — keeping snap, CHECK MANUALLY")
                else:
                    u, v, k = rev
                    print(f"  {sensor_id}: level '{level}' ({want}°) opposes snapped "
                          f"edge ({have:.0f}°) — using opposite {eid(u, v, k)}")

        edge_id  = eid(u, v, k)
        if edge_id in edge_sensor:
            print(f"  ⚠  {sensor_id}: auto-snap {edge_id} already claimed by {edge_sensor[edge_id]}")
        edge_sensor[edge_id] = sensor_id

        dist = true_snap_dist_m(G, u, v, k, lats[i], lons[i])
        warn = f"  ⚠ {dist:.0f}m — check snap" if dist > SNAP_WARN_M else ""
        print(f"  {sensor_id}: auto     {edge_id}  ({level})  {dist:.0f}m{warn}")

        if level == "Total":
            rev = _find_second_edge(G, u, v, k, sensor_id, lats[i], lons[i])
            edge_sensor[rev] = sensor_id
            rev_dist = true_snap_dist_m(G, *_parse_eid(rev), lats[i], lons[i])
            print(f"      ↔  {rev}  ({rev_dist:.0f}m)")

    return edge_sensor


# ── Flow arrays ────────────────────────────────────────────────────────────────

def build_flows(
    raw: pd.DataFrame,
    edge_sensor: dict[str, str],
) -> dict[str, list]:
    """
    Return { edge_id: [count|null, ...] } with N_QUARTERS slots.

    Quarter indices computed once per column (vectorised), then scattered
    per sensor group — O(n_rows + n_edges) not O(n_rows × n_edges).
    """
    flows: dict[str, list] = {e: [None] * N_QUARTERS for e in edge_sensor}

    sensor_to_edges: dict[str, list[str]] = defaultdict(list)
    for edge_id, sid in edge_sensor.items():
        sensor_to_edges[sid].append(edge_id)

    raw = raw.copy()
    raw["qi"] = build_qi_series(raw["ts"])
    valid = raw[raw["qi"].notna()].copy()
    valid["qi"] = valid["qi"].astype(int)

    dups = valid.groupby(["matplats", "qi"]).size()
    dups = dups[dups > 1]
    if not dups.empty:
        print(f"  ⚠  {len(dups):,} duplicate sensor+quarter slots — last value wins:")
        for (sid, qi), n in dups.items():
            print(f"       sensor {sid}  qi={qi}  ({n} rows)")
    valid = valid.sort_values("ts").drop_duplicates(subset=["matplats", "qi"], keep="last")

    for sensor_id, group in valid.groupby("matplats"):
        edge_ids = sensor_to_edges.get(str(sensor_id), [])
        if not edge_ids:
            continue
        for row in group.itertuples(index=False):
            if pd.isnull(row.count):
                val = None
            elif int(row.count) < 0:
                print(f"  ⚠  sensor {sensor_id} qi={row.qi}: negative count {row.count} — treated as missing")
                val = None
            else:
                val = int(row.count)
            for edge_id in edge_ids:
                flows[edge_id][row.qi] = val

    return flows


# ── Argument parsing ───────────────────────────────────────────────────────────

DROP_DIR = Path("data_in")   # drop new quarterly CSV deliveries here
DEFAULT_DATA_DIR = "~/Downloads/Data till Chalmers_20260618"
DEFAULT_COORDS   = "~/Downloads/Mätpunkter_koordinater.csv"


def discover_data_dir() -> str:
    """data_in/ wins when it contains sensor CSVs — the drop-folder workflow."""
    if DROP_DIR.is_dir() and any(
        p for p in DROP_DIR.glob("*.csv") if "koordinat" not in p.name.lower()
    ):
        return str(DROP_DIR)
    return DEFAULT_DATA_DIR


def discover_coords() -> str:
    if DROP_DIR.is_dir():
        hits = [p for p in DROP_DIR.glob("*.csv") if "koordinat" in p.name.lower()]
        if hits:
            return str(hits[0])
    return DEFAULT_COORDS


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data_dir",    default=None,
                   help="Directory containing sensor CSV files "
                        "(default: data_in/ if it has CSVs, else the original delivery)")
    p.add_argument("--coords",      default=None,
                   help="Sensor coordinates CSV, SWEREF99 12 00 / EPSG:3007 "
                        "(default: *koordinat*.csv in data_in/, else the original)")
    p.add_argument("--registry",    default=str(SENSOR_REGISTRY_PATH),
                   help="Validated sensor registry JSON (default: data_in/sensors.json)")
    p.add_argument("--out_dir",     default="web/data",
                   help="Output directory (default: web/data)")
    p.add_argument("--clip_radius", type=float, default=0,
                   help="Drop non-sensor OSM edges further than this from the "
                        "nearest sensor; 0 = no clip (default — the whole "
                        "inner-city canvas is displayed, the confidence "
                        "gradient communicates trust instead of hiding roads)")
    return p.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global MANUAL_SNAPS
    args        = parse_args()
    data_dir    = Path(args.data_dir or discover_data_dir()).expanduser()
    coords_path = Path(args.coords or discover_coords()).expanduser()
    print(f"Data: {data_dir}\nCoords: {coords_path}")
    out_dir     = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conf_sigma_m, conf_sigma_source = empirical_conf_sigma_m(out_dir)
    print(f"Confidence: {conf_sigma_source}")

    ox.settings.use_cache    = True
    ox.settings.cache_folder = str(Path("cache"))
    ox.settings.log_console  = False

    # ── Load sensor data ───────────────────────────────────────────────────────
    print("Loading CSVs …")
    raw = load_raw(data_dir, coords_path)
    print(f"  {len(raw):,} rows  |  sensors: {sorted(raw['matplats'].unique())}")

    print("Loading coordinates …")
    coords = load_coords(coords_path)
    sensor_ids = sorted(str(sensor_id) for sensor_id in raw["matplats"].unique())
    registry = load_registry(Path(args.registry), coordinates=coords)
    registry.validate_data_sensors(
        sensor_ids,
        require_coordinates=True,
        study_start=raw["ts"].min().date().isoformat(),
        study_end=raw["ts"].max().date().isoformat(),
    )
    MANUAL_SNAPS = registry.manual_snaps()
    sensors = [sensor_id for sensor_id in sensor_ids if sensor_id in coords]
    if not sensors:
        raise ValueError("No sensors found in both data and coordinates files.")
    sensor_level = registry.levels_for(sensors)
    print(f"  {len(sensors)} matched: {sensors}")

    level_inconsistent = raw.groupby("matplats")["level"].nunique()
    level_inconsistent = level_inconsistent[level_inconsistent > 1]
    if not level_inconsistent.empty:
        print(f"  ⚠  Inconsistent level values: {level_inconsistent.to_dict()}")

    lats = [coords[s][0] for s in sensors]
    lons = [coords[s][1] for s in sensors]
    print(f"  {len(sensors)} matched: {sensors}")

    # ── Download OSM graph — GOTHENBURG INNER CITY ─────────────────────────────
    # Decided 2026-07-05: the canvas is the whole inner city (Vallgraven,
    # Haga/Linné, Vasastaden, Heden, Gårda, Johanneberg/Landala, Korsvägen–
    # Scandinavium), bounded by the river in the north; bridges and the big
    # approaches become through-traffic gates. Accuracy is a GRADIENT: hard
    # near sensors, prior-driven elsewhere — the confidence map says which.
    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)
    s, w, n, e = INNER_CITY_BBOX
    print(f"OSM graph  inner-city bbox {INNER_CITY_BBOX}  (cached in cache/) …")
    G = ox.graph_from_bbox(
        bbox=(w, s, e, n),          # osmnx 2.x: (left, bottom, right, top)
        network_type="drive",
        simplify=True,
    )
    print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # Exact graph snapshot for Phase 3: the SUMO network and any demand-
    # calibration code must start from THIS graph (same node/edge IDs as
    # network.geojson), not a fresh OSM download that may have changed.
    graphml_path = out_dir / "graph.graphml"
    ox.save_graphml(G, graphml_path)
    print(f"Wrote {graphml_path}  ({graphml_path.stat().st_size / 1024:.0f} KB)")

    # ── Snap and build flows ───────────────────────────────────────────────────
    print("Snapping sensors …")
    edge_sensor = snap_sensors(G, sensors, lats, lons, sensor_level)
    bidir       = _bidir_edges(edge_sensor)

    # The registry stores reviewed snaps from this exact graph snapshot.  Do
    # not allow a changed OSM graph or a direction mistake to silently move a
    # count constraint to another edge.
    resolved_edges: dict[str, list[str]] = defaultdict(list)
    resolved_distances: dict[str, list[float]] = defaultdict(list)
    for edge_id, sensor_id in edge_sensor.items():
        resolved_edges[sensor_id].append(edge_id)
        u, v, k = _parse_eid(edge_id)
        resolved_distances[sensor_id].append(
            true_snap_dist_m(G, u, v, k, coords[sensor_id][0], coords[sensor_id][1])
        )
    registry.validate_resolved_edges(
        resolved_edges,
        resolved_distances_m=resolved_distances,
        sensor_ids=sensors,
    )

    print("Building flow arrays …")
    flows = build_flows(raw, edge_sensor)

    print("  Completeness:")
    for edge_id in sorted(flows):
        arr = flows[edge_id]
        pct = 100 * sum(v is not None for v in arr) / N_QUARTERS
        print(f"    {edge_id:<40s}  sensor={edge_sensor[edge_id]}  {pct:.1f}%")

    # ── Write flows.json ───────────────────────────────────────────────────────
    flows_path = out_dir / "flows.json"
    with open(flows_path, "w") as f:
        json.dump(
            {
                "epoch":            "2025-01-01T00:00:00",
                "interval_minutes": 15,
                "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
                "flows":            flows,
            },
            f, separators=(",", ":"),
        )
    print(f"Wrote {flows_path}  ({flows_path.stat().st_size / 1024:.0f} KB)")

    # ── Write network.geojson ──────────────────────────────────────────────────
    features: list[dict] = []
    clip_r = args.clip_radius

    for u, v, k, data in G.edges(keys=True, data=True):
        edge_id   = eid(u, v, k)
        sensor_id = edge_sensor.get(edge_id)

        # Distance from edge midpoint to the NEAREST sensor drives both the
        # scope clip (two small areas, one per cluster) and the confidence.
        mlat, mlon = edge_midpoint(G, u, v, k)
        d_sensor = min(
            haversine_m(la, lo, mlat, mlon) for la, lo in zip(lats, lons)
        )

        if sensor_id is None:
            if clip_r and d_sensor > clip_r:
                continue

        coords_geom = (
            list(data["geometry"].coords)
            if "geometry" in data
            else [(G.nodes[u]["x"], G.nodes[u]["y"]),
                  (G.nodes[v]["x"], G.nodes[v]["y"])]
        )

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords_geom},
            "properties": {
                "id":            edge_id,
                "sensor_id":     sensor_id,
                "level":         sensor_level.get(sensor_id) if sensor_id else None,
                "bidirectional": sensor_id is not None and edge_id in bidir,
                "name":          scalar(data.get("name"))    or None,
                "highway":       scalar(data.get("highway")) or None,
                "length_m":      round(data.get("length", 0), 1),
                "dist_sensor_m": round(d_sensor, 1),
                "confidence":    round(
                    math.exp(-(d_sensor ** 2) / (2 * conf_sigma_m ** 2)), 3
                ),
            },
        })

    geo_path = out_dir / "network.geojson"
    with open(geo_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features},
                  f, separators=(",", ":"))

    n_sensor = sum(1 for f in features if f["properties"]["sensor_id"])
    print(f"Wrote {geo_path}  ({len(features)} edges, {n_sensor} with sensors)")


if __name__ == "__main__":
    main()
