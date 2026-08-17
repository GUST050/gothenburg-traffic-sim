"""
Configuration for the direction-split model subsystem (dirsplit).

The system trains on cities where hourly directional counts are OPEN data
(Norway via Statens vegvesen's trafikkdata API) and predicts the time-of-day
direction split for edges where only two-way totals exist (our Gothenburg
sensors — and any future sensors: add them to network.geojson and they are
picked up automatically, nothing here is sensor-specific).

Feature computation is ONE code path for training stations and target edges
(dirsplit/features.py) — that symmetry is what makes the transfer honest.
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR     = Path("data/dirsplit")
# Raw hourly volumes are no longer fetched by this repository (the acquisition
# client was removed 2026-08-16 with the LightGBM model it fed). The tracked
# aggregate table remains the deployed profile's evidence base; drop raw
# per-station CSVs here by hand if the v2 table is ever to be rebuilt.
VOLUMES_DIR  = DATA_DIR / "volumes"
STATIONS_OK  = DATA_DIR / "stations_matched.json"
COVERAGE_REPORT = DATA_DIR / "coverage_report.json"

# Norwegian training cities: centre point + bounding box (south, west, north, east)
# + the county the API is filtered on. Add a city → it joins the training set.
CITIES: dict[str, dict] = {
    "trondheim": {
        "county": 50,
        "centre": (63.4305, 10.3951),          # Torvet
        "bbox":   (63.35, 10.25, 63.46, 10.55),
    },
    "oslo": {
        "county": 3,
        "centre": (59.9139, 10.7522),          # Karl Johan/Sentrum
        "bbox":   (59.85, 10.60, 59.98, 10.95),
    },
    "bergen": {
        "county": 46,
        "centre": (60.3929, 5.3241),           # Torgallmenningen
        "bbox":   (60.28, 5.20, 60.45, 5.45),
    },
    "stavanger": {
        "county": 11,
        "centre": (58.9700, 5.7331),
        "bbox":   (58.90, 5.60, 59.00, 5.80),
    },
}

# Target city (prediction side). Centre = Brunnsparken, consistent with
# the direction-split subsystem throughout.
TARGET_CENTRE = (57.7068, 11.9668)

# Feature parameters
LANDUSE_RADIUS_M = 1000    # half-disc radius for behind/ahead asymmetry
KNN_K            = 10      # applicability-domain neighbours

# Fallback speeds (km/h) by OSM highway class — same spirit as build_sumo_net
DEFAULT_SPEED_KMH = {
    "motorway": 100, "motorway_link": 60, "trunk": 80, "trunk_link": 50,
    "primary": 60, "primary_link": 50, "secondary": 50, "secondary_link": 50,
    "tertiary": 50, "tertiary_link": 50, "residential": 30,
    "living_street": 20, "unclassified": 40,
}

# Ordinal encoding of road class (major → minor); unseen classes → 3
HIGHWAY_RANK = {
    "motorway": 0, "motorway_link": 0, "trunk": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2, "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4, "unclassified": 5,
    "residential": 6, "living_street": 7,
}
