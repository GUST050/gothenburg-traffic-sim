"""Structural realism of the calibrated demand — split from
build_sumo_demand.py 2026-07-14 (see IMPROVEMENT_PLAN.md).

Owns: the shared edge-geometry loader, the calibrated-structure metrics
and drift flags (the permanent destination-bias validation gate described in
IMPROVEMENT_PLAN.md), per-purpose length
reporting, and the PFE structure-preservation groups. Patchable module
globals (GEO_PATH, the geometry cache) live HERE — tests that monkeypatch
them must target this module.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

GEO_PATH = Path("web/data/network.geojson")

# Keep PFE's structural validation independent of build_candidates.py.  That
# module imports OSMnx/Shapely and costs several seconds to import; the PFE
# only needs this small, deterministic city-scale distance calculation.
KLAT_M = 110_540.0
KLON_M = 111_320.0 * np.cos(np.radians(57.7))
NEAR_SENSOR_RADIUS_M = 200.0
RVU_SHORT_BIN_EDGES_KM = (1.0, 5.0, 10.0)
_RVU_SHORT_RAW = (0.09, 0.31, 0.19)
RVU_SHORT_BIN_SHARES = tuple(v / sum(_RVU_SHORT_RAW) for v in _RVU_SHORT_RAW)


def gravity_distance_km(lats: np.ndarray, lons: np.ndarray,
                        lat0: float, lon0: float) -> np.ndarray:
    """Cos-corrected flat-earth distance at Gothenburg city scale."""
    return np.sqrt(((lats - lat0) * KLAT_M / 1000.0) ** 2
                   + ((lons - lon0) * KLON_M / 1000.0) ** 2)


def trip_length_fit(lengths_km: list[float]) -> dict:
    """RVU short-distance-bin fit; identical contract to candidate reporting."""
    n = len(lengths_km)
    if n == 0:
        return {
            "shares": [0.0, 0.0, 0.0],
            "counts": [0, 0, 0],
            "short_trip_count": 0,
            "l1_distance": float("inf"),
            "n": 0,
        }
    counts = [0, 0, 0]
    over_10km = 0
    for distance in lengths_km:
        if distance <= RVU_SHORT_BIN_EDGES_KM[0]:
            counts[0] += 1
        elif distance <= RVU_SHORT_BIN_EDGES_KM[1]:
            counts[1] += 1
        elif distance <= RVU_SHORT_BIN_EDGES_KM[2]:
            counts[2] += 1
        else:
            over_10km += 1
    n_short = n - over_10km
    shares = [count / n_short for count in counts] if n_short else [0.0, 0.0, 0.0]
    return {"shares": [round(value, 4) for value in shares],
            "counts": counts,
            "short_trip_count": n_short,
            "l1_distance": round(sum(abs(value - target)
                                     for value, target in zip(shares, RVU_SHORT_BIN_SHARES)), 4),
            "n": n, "over_10km_pct": round(100 * over_10km / n, 1)}


def destination_sensor_proximity(dest_edge_ids: list[str],
                                 edge_latlon: dict[str, tuple[float, float]],
                                 sensor_edge_ids: list[str],
                                 radius_m: float = NEAR_SENSOR_RADIUS_M) -> dict:
    """Destination-near-sensor diagnostic shared by pool/output reports."""
    sensor_pts = [edge_latlon[edge_id] for edge_id in sensor_edge_ids
                  if edge_id in edge_latlon]
    if not sensor_pts:
        return {"radius_m": radius_m, "pct_within": None,
                "baseline_pct_within": None, "n": len(dest_edge_ids)}
    s_lats = np.array([point[0] for point in sensor_pts])
    s_lons = np.array([point[1] for point in sensor_pts])

    def near(lat: float, lon: float) -> bool:
        return bool((gravity_distance_km(s_lats, s_lons, lat, lon) * 1000.0).min()
                    <= radius_m)

    n_near = sum(1 for edge_id in dest_edge_ids
                 if edge_id in edge_latlon and near(*edge_latlon[edge_id]))
    n_known = sum(1 for edge_id in dest_edge_ids if edge_id in edge_latlon)
    all_near = sum(1 for point in edge_latlon.values() if near(*point))
    return {"radius_m": radius_m,
            "pct_within": round(100 * n_near / n_known, 1) if n_known else None,
            "baseline_pct_within": round(100 * all_near / len(edge_latlon), 1),
            "n": n_known}

def calibrated_agent_summary(route_path: Path, n_intervals: int) -> dict | None:
    """Summarise the individual demand agents emitted beside a PFE route file.

    Older candidate pools have no purpose sidecar, so absence stays explicit
    rather than fabricating a purpose split from route geometry.
    """
    agent_path = route_path.with_name(route_path.name.replace(".rou.xml", ".agents.json"))
    if not agent_path.exists():
        return None
    with open(agent_path) as f:
        agents = json.load(f).get("agents", [])
    purposes = Counter(agent.get("purpose", "unknown") for agent in agents)
    by_quarter = [Counter() for _ in range(n_intervals)]
    origin_locations = Counter(
        agent.get("origin_location_id") for agent in agents
        if agent.get("origin_location_id") is not None)
    origin_edges = Counter(agent.get("origin_edge") for agent in agents
                          if agent.get("origin_edge") is not None)
    for agent in agents:
        quarter = int(float(agent.get("departure_s", 0)) // 900)
        if 0 <= quarter < n_intervals:
            by_quarter[quarter][agent.get("purpose", "unknown")] += 1
    result = {
        "agent_file": agent_path.name,
        "n_agents": len(agents),
        "purpose_counts": dict(sorted(purposes.items())),
        "purpose_counts_by_quarter": [dict(sorted(counts.items()))
                                      for counts in by_quarter],
    }
    if origin_locations:
        result["origin_location_distribution"] = {
            "agents_with_physical_origin": sum(origin_locations.values()),
            "unique_locations": len(origin_locations),
            "max_vehicles_at_one_location": max(origin_locations.values()),
            "max_vehicles_on_one_origin_edge": max(origin_edges.values()) if origin_edges else 0,
        }
    return result


_EDGE_GEOMETRY_CACHE: tuple | None = None


def load_edge_geometry() -> tuple[dict[str, tuple[float, float]],
                                  set[str], dict[str, float]]:
    """Parse GEO_PATH once per process: edge midpoints as (lat, lon),
    measured-edge ids, and polyline lengths in metres.

    THE shared geometry source for the destination-bias guard chain
    (_route_structure_metrics and structure_groups_for_shapes) — one parse,
    one midpoint convention, so the enforced PFE cap and the drift metric
    can never disagree about where an edge is. Cached on the file's
    (mtime, size), not unconditionally: serve.py-driven recalibrations can
    rewrite network.geojson while a process lives."""
    global _EDGE_GEOMETRY_CACHE
    st = GEO_PATH.stat()
    key = (st.st_mtime_ns, st.st_size)
    if _EDGE_GEOMETRY_CACHE is not None and _EDGE_GEOMETRY_CACHE[0] == key:
        return _EDGE_GEOMETRY_CACHE[1], _EDGE_GEOMETRY_CACHE[2], _EDGE_GEOMETRY_CACHE[3]
    with open(GEO_PATH) as f:
        geo = json.load(f)
    edge_latlon: dict[str, tuple[float, float]] = {}
    sensor_edge_ids: set[str] = set()
    edge_len_m: dict[str, float] = {}
    for feat in geo["features"]:
        if feat["geometry"]["type"] != "LineString":
            continue
        coords = feat["geometry"]["coordinates"]
        mid = coords[len(coords) // 2]
        eid = feat["properties"]["id"]
        edge_latlon[eid] = (mid[1], mid[0])          # (lat, lon)
        lats = np.array([c[1] for c in coords])
        lons = np.array([c[0] for c in coords])
        edge_len_m[eid] = float(gravity_distance_km(
            lats[1:], lons[1:], lats[:-1], lons[:-1]).sum() * 1000.0)
        if feat["properties"].get("sensor_id"):
            sensor_edge_ids.add(eid)
    _EDGE_GEOMETRY_CACHE = (key, edge_latlon, sensor_edge_ids, edge_len_m)
    return edge_latlon, sensor_edge_ids, edge_len_m


def _route_structure_metrics(route_path: Path) -> dict | None:
    """Per-vehicle structure metrics for one SUMO route file — the shared
    machinery behind calibrated_structure_report (below). Emits the
    destination-integrity guards described in IMPROVEMENT_PLAN.md:

    - trip_length_fit (straight-line O→D, RVU bins)
    - dest_sensor_proximity (share ending within 200 m of a sensor)
    - onward_after_last_sensor: how far each vehicle's route CONTINUES
      after the last measured sensor edge it crosses (polyline metres) —
      the sharpest signature of the original bug ("crosses the sensor,
      then vanishes"), sharper than nearest-sensor distance because it
      cannot be confused by a destination that happens to sit near a
      DIFFERENT sensor the route never crossed.
    - sensor_passages: how many measured edges each route crosses.

    Uses this module's lightweight metric helpers so a post-calibration
    report never has to import OSMnx/Shapely."""
    if not route_path.exists() or not GEO_PATH.exists():
        return None
    edge_latlon, sensor_edge_ids, edge_len_m = load_edge_geometry()

    lengths_km: list[float] = []
    dest_edges: list[str] = []
    onward_m: list[float] = []
    passages = Counter()
    quarter_totals = Counter()
    under_1km_by_quarter = Counter()
    n_no_sensor = 0
    for veh in ET.parse(route_path).getroot().iter("vehicle"):
        route = veh.find("route")
        if route is None or not route.get("edges"):
            continue
        edges = route.get("edges").split()
        o, d = edge_latlon.get(edges[0]), edge_latlon.get(edges[-1])
        dest_edges.append(edges[-1])
        if o is not None and d is not None:
            distance_km = float(gravity_distance_km(
                np.array([d[0]]), np.array([d[1]]), o[0], o[1])[0])
            lengths_km.append(distance_km)
            quarter = int(float(veh.get("depart", "0")) // 900)
            quarter_totals[quarter] += 1
            if distance_km <= RVU_SHORT_BIN_EDGES_KM[0]:
                under_1km_by_quarter[quarter] += 1
        n_pass = sum(1 for e in edges if e in sensor_edge_ids)
        passages[min(n_pass, 3)] += 1   # 3 == "3 or more"
        if n_pass == 0:
            n_no_sensor += 1
        else:
            last = max(k for k, e in enumerate(edges) if e in sensor_edge_ids)
            onward_m.append(sum(edge_len_m.get(e, 0.0) for e in edges[last + 1:]))

    if not dest_edges:
        return None
    onward_sorted = sorted(onward_m)
    return {
        "trip_length_fit": {
            **trip_length_fit(lengths_km),
            "quarter_totals": dict(sorted(quarter_totals.items())),
            "under_1km_by_quarter": dict(sorted(under_1km_by_quarter.items())),
        },
        "dest_sensor_proximity": destination_sensor_proximity(
            dest_edges, edge_latlon, sorted(sensor_edge_ids)),
        "onward_after_last_sensor": {
            "median_m": round(onward_sorted[len(onward_sorted) // 2], 1)
                        if onward_sorted else None,
            "pct_under_200m": round(100 * sum(1 for v in onward_m if v < 200.0)
                                    / len(onward_m), 1) if onward_m else None,
            "n_routes_without_sensor": n_no_sensor,
        },
        "sensor_passages": {("3+" if k == 3 else str(k)): passages[k]
                            for k in sorted(passages)},
    }


def purpose_lengths_km(route_path: Path) -> dict | None:
    """Straight-line O→D length per PURPOSE for the agents emitted beside a
    calibrated route file.

    The time/day-type distance behaviour is composed as
    P(length | purpose) × P(purpose | hour, day type), so purpose-
    conditional length is the piece the survey data actually pins down
    (Trafikanalys RVU 2023 Tabell 3, car: arbete 24 km < service 30 km <
    fritid 56 km — fritid LONGEST). Found 2026-07-13: the pool preserved
    that ordering but the pre-62a1584 purpose stamping inverted it in the
    calibrated output, invisibly — hence this standing metric."""
    agent_path = route_path.with_name(
        route_path.name.replace(".rou.xml", ".agents.json"))
    if not agent_path.exists() or not GEO_PATH.exists():
        return None
    edge_latlon, _sensor_ids, _len_m = load_edge_geometry()
    with open(agent_path) as f:
        agents = json.load(f).get("agents", [])
    by_p: dict[str, list[float]] = {}
    for a in agents:
        o = edge_latlon.get(a.get("origin_edge"))
        d = edge_latlon.get(a.get("destination_edge"))
        if o is None or d is None:
            continue
        km = float(gravity_distance_km(
            np.array([d[0]]), np.array([d[1]]), o[0], o[1])[0])
        by_p.setdefault(a.get("purpose", "unknown"), []).append(km)
    out = {}
    for p, lens in sorted(by_p.items()):
        lens.sort()
        out[p] = {"n": len(lens),
                  "mean_km": round(float(np.mean(lens)), 2),
                  "median_km": round(lens[len(lens) // 2], 2)}
    return out or None


# The ordering flag needs enough vehicles per purpose to mean anything.
PURPOSE_LENGTH_MIN_N = 50

# Structure-preservation slack: a structural group's calibrated share may be
# at most this multiple of the candidate pool's own share. 2.0 gives the
# solver genuine freedom (activity near sensors is real — Korsvägen etc.)
# while blocking the measured 5-10x amplification.
DEST_GROUP_CAP_MULT = 2.0
# The candidate pool is a sample from the DeSO/building/POI endpoint field.
# A count-only calibration must not amplify a single sampled access edge by
# orders of magnitude merely because it happens to close one sensor band.
# This is deliberately looser than the 2x destination guard: origin mass is
# more sensitive to directed network reachability, and the guard may never
# cost a real sensor count (the common relaxation ladder still wins).
ORIGIN_EDGE_CAP_MULT = 3.0
# Straight-line O→D length bins whose calibrated share is likewise capped
# relative to the pool — RVU's own bin edges, matching trip_length_fit.
LENGTH_BIN_EDGES_KM = (1.0, 5.0, 10.0)

# Flag threshold: calibrated may exceed the pool by at most the structure-
# cap multiple plus a 25% margin (the per-quarter caps carry a 2-vehicle
# integer floor, so mild aggregate overshoot is expected, not a defect).
STRUCTURE_FLAG_MULT = DEST_GROUP_CAP_MULT * 1.25


def calibrated_structure_report(route_path: Path,
                                pool_path: Path | None = None) -> dict | None:
    """Structure metrics of the CALIBRATED output + drift FLAGS vs the
    candidate pool it was calibrated from — the permanent validation gate
    for the 2026-07-12 destination-clustering bug (the consolidated plan's
    requirement to publish validation gates with
    every demand build ... Reject or flag a build when it has good GEH but
    fails those structural checks").

    Measured then: the candidate POOL looked acceptable while the PFE-
    calibrated output had 36.5% of vehicles ending within 200 m of a sensor
    (2.1% random-edge baseline) — calibration distorted the seed's
    structure invisibly, because the only fit ever computed ran at
    candidate-generation time. Flags compare calibrated vs pool (the seed
    structure PFE is supposed to preserve), not vs an absolute number."""
    report = _route_structure_metrics(route_path)
    if report is None:
        return None
    flags = []

    # Purpose-conditional length: the RVU distance data orders fritid
    # LONGEST (see purpose_lengths_km). The pool respects it via
    # PURPOSE_LENGTH_SCALE; an inversion in the calibrated output means the
    # calibration/purpose-allocation stage decohered P(length | purpose).
    lengths = purpose_lengths_km(route_path)
    if lengths:
        report["purpose_length_km"] = lengths
        arb, fri = lengths.get("arbete"), lengths.get("fritid")
        # MEDIAN, deliberately: the length-aware allocator (pfe.
        # allocate_interval_provenance) restores the ordering robustly
        # (measured 2026-07-13: fritid median 1.80 → 2.80 km, now above
        # arbete), but the MEAN keeps a small permanent gap because long
        # routes with arbete-only provenance rightly stay arbete —
        # relabeling them would invent leisure trips to work POIs. A flag
        # that fires on every healthy build is alarm-fatigue noise; both
        # statistics stay reported above.
        if (arb and fri and min(arb["n"], fri["n"]) >= PURPOSE_LENGTH_MIN_N
                and fri["median_km"] < arb["median_km"]):
            flags.append(
                f"purpose_length_ordering: fritid median {fri['median_km']} km"
                f" < arbete median {arb['median_km']} km — RVU (Trafikanalys "
                "Tabell 3) orders fritid longest; P(length|purpose) decohered "
                "in calibration")

    if pool_path is not None:
        pool = _route_structure_metrics(pool_path)
        if pool is not None:
            report["pool"] = pool

            def ratio_flag(name: str, calibrated_v, pool_v) -> None:
                if calibrated_v is None or pool_v is None:
                    return
                if calibrated_v > max(pool_v, 0.5) * STRUCTURE_FLAG_MULT:
                    flags.append(f"{name}: calibrated {calibrated_v} vs pool "
                                 f"{pool_v} (over {STRUCTURE_FLAG_MULT:.2f}x)")

            ratio_flag("dest_within_200m_of_sensor_pct",
                       report["dest_sensor_proximity"]["pct_within"],
                       pool["dest_sensor_proximity"]["pct_within"])
            ratio_flag("onward_under_200m_pct",
                       report["onward_after_last_sensor"]["pct_under_200m"],
                       pool["onward_after_last_sensor"]["pct_under_200m"])
            calibrated_fit = report["trip_length_fit"]
            pool_fit = pool["trip_length_fit"]
            pool_short_n = pool_fit.get("short_trip_count", 0)
            pool_share = (
                pool_fit["counts"][0] / pool_short_n
                if pool_short_n else 0.0
            )
            quarter_totals = calibrated_fit.get("quarter_totals", {})
            short_by_quarter = calibrated_fit.get(
                "under_1km_by_quarter", {})
            violations = []
            for quarter, total in quarter_totals.items():
                actual = int(short_by_quarter.get(quarter, 0))
                limit = max(2.0, DEST_GROUP_CAP_MULT * pool_share * total)
                if actual > limit + 1e-9:
                    violations.append((int(quarter), actual, limit))
            calibrated_fit["under_1km_cap_audit"] = {
                "pool_share": pool_share,
                "violating_quarters": len(violations),
                "max_excess_vehicles": round(
                    max((actual - limit for _, actual, limit in violations),
                        default=0.0),
                    3,
                ),
                "violations": [
                    {
                        "quarter": quarter,
                        "actual": actual,
                        "limit": round(limit, 3),
                        "excess": round(actual - limit, 3),
                    }
                    for quarter, actual, limit in violations
                ],
            }
            if violations:
                flags.append(
                    "trips_under_1km_cap: "
                    f"{len(violations)} quarter(s) exceed the exact "
                    f"{DEST_GROUP_CAP_MULT:.2f}x pool-share/floor-2 cap; "
                    f"maximum excess "
                    f"{max(actual - limit for _, actual, limit in violations):.2f} "
                    "vehicle(s)"
                )
    report["structure_flags"] = flags
    for flag in flags:
        print(f"  WARNING structure drift: {flag}")
    return report


def structure_groups_for_shapes(shapes) -> list[tuple[str, list[int], float]]:
    """PFE structure-preservation groups (name, member shape indices, capped
    assigned share) — see IMPROVEMENT_PLAN.md's destination-integrity rules:
    "PFE may adjust route use to satisfy sensor counts, but it may not turn
    a realistic candidate distribution into mostly sensor-adjacent
    destinations." Two families, both capped at DEST_GROUP_CAP_MULT × the
    pool's own share:

    1. near_sensor_dest — routes ENDING within 200 m of a sensor. ROOT
       CAUSE (measured 2026-07-12, after the stage-1 generation fix had
       already brought the POOL's share down to the activity-field level
       of ~2-4%): PFE still calibrated 19.4% of vehicles onto such routes
       — 87 of 948 active shapes carried 4 550 vehicles at 52 veh/shape vs
       24 for all others. A route crossing exactly one sensor and ENDING
       is a free variable for closing that sensor's hard count band
       without disturbing any other sensor's band (ruled OUT empirically:
       assignment-prior bounds — a --no-assignment-prior build still
       showed 18.3%).
    2. length_bin_* — straight-line O→D length bins (RVU's edges). Same
       under-determination family, measured after the near-sensor cap was
       already in place: the calibrated 0-1 km share was still inflated
       1.2% → 7.5% (6×) relative to the pool. The consolidated plan requires
       explicit length-bin bands.
    3. origin_edge:* — each physical endpoint access edge's selected share
       may be at most 3x its source-pool share. This is a distributional
       guard, not an arbitrary spatial exclusion: a real building beside a
       sensor remains eligible, but one sampled route cannot become hundreds
       of cars just because PFE has no other count constraint on its origin.

    Caps are ceilings only (lo=0 — a group must never force flow), applied
    per quarter with an absolute value derived from that quarter's total,
    and dropped (never the counts) if an interval can't otherwise be
    served — see solve_interval_with_relaxation's ladder, the pass-1
    fallback in _run_pfe_interval_job, and the integer-stage repair in
    write_calibration_report. Groups whose cap would be >= 100% are
    omitted (no-ops)."""
    edge_latlon: dict[str, tuple[float, float]] = {}
    sensor_pts: list[tuple[float, float]] = []
    if GEO_PATH.exists():
        edge_latlon, sensor_edge_ids, _edge_len_m = load_edge_geometry()
        sensor_pts = [edge_latlon[e] for e in sorted(sensor_edge_ids)
                      if e in edge_latlon]
    s_lats_a = np.array([p[0] for p in sensor_pts])
    s_lons_a = np.array([p[1] for p in sensor_pts])

    def near(eid: str) -> bool:
        ll = edge_latlon.get(eid)
        if ll is None or not sensor_pts:
            return False
        return bool((gravity_distance_km(s_lats_a, s_lons_a, ll[0], ll[1])
                     * 1000.0).min() <= NEAR_SENSOR_RADIUS_M)

    def od_length_km(shape) -> float | None:
        o = edge_latlon.get(shape.edges[0])
        d = edge_latlon.get(shape.edges[-1])
        if o is None or d is None:
            return None
        return float(gravity_distance_km(
            np.array([d[0]]), np.array([d[1]]), o[0], o[1])[0])

    def length_bin(km: float) -> int:
        for b, edge in enumerate(LENGTH_BIN_EDGES_KM):
            if km <= edge:
                return b
        return len(LENGTH_BIN_EDGES_KM)

    candidate_groups: dict[str, list[int]] = {}
    if sensor_pts:
        candidate_groups["near_sensor_dest"] = []
    bin_names = [f"length_bin_{lo}-{hi}km" for lo, hi in
                 zip((0,) + LENGTH_BIN_EDGES_KM, LENGTH_BIN_EDGES_KM + ("inf",))]
    if sensor_pts:
        for name in bin_names:
            candidate_groups[name] = []
    for i, shape in enumerate(shapes):
        if sensor_pts and shape.edges:
            if near(shape.edges[-1]):
                candidate_groups["near_sensor_dest"].append(i)
            km = od_length_km(shape)
            if km is not None:
                candidate_groups[bin_names[length_bin(km)]].append(i)
        # Exact route shapes have one first edge even when their provenance
        # contains multiple purpose/time candidates. It is therefore the
        # stable physical origin-access group for every source of this shape.
        if shape.edges:
            candidate_groups.setdefault(f"origin_edge:{shape.edges[0]}", []).append(i)

    n_total = sum(len(s.source_candidates) for s in shapes)
    groups: list[tuple[str, list[int], float]] = []
    origin_groups = 0
    for name, members in candidate_groups.items():
        if not members:
            continue
        n_members = sum(len(shapes[i].source_candidates) for i in members)
        # Pool share weighted by how many source candidates each shape
        # carries — the seed structure the calibration must preserve.
        pool_share = n_members / max(1, n_total)
        multiplier = (ORIGIN_EDGE_CAP_MULT if name.startswith("origin_edge:")
                      else DEST_GROUP_CAP_MULT)
        cap_share = multiplier * pool_share
        if cap_share >= 1.0:
            continue   # cap can never bind — omit the no-op
        groups.append((name, members, cap_share))
        if name.startswith("origin_edge:"):
            origin_groups += 1
        else:
            print(f"  PFE structure guard [{name}]: {len(members)} shapes, "
                  f"{100 * pool_share:.1f}% of pool candidates — assigned share "
                  f"capped at {100 * cap_share:.1f}%")
    if origin_groups:
        print(f"  PFE origin-distribution guard: {origin_groups} access-edge "
              f"groups, each capped at {ORIGIN_EDGE_CAP_MULT:.0f}x its "
              "candidate-pool share when it would otherwise be amplified")
    return groups
