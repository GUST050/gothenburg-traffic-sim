"""
Fetch real population data for the OD generator's home-end masses.

Run once (results are committed under data_in/deso/, re-run only if the
inner-city bbox changes):
  python3 fetch_deso.py

Source: SCB (Statistics Sweden) open data —
  - DeSO 2025 boundaries: SCB's open WFS (geodata.scb.se/geoserver/stat),
    layer stat:DeSO_2025, CC0.
  - Population per DeSO: PXWeb API table BE0101Y/FolkmDesoAldKon. The
    newest DeSO-2025 boundary version is only back-filled through 2023 in
    this table (2024/2025 queries return 0 — a publication lag, not zero
    population) — so 2023 is the correct "latest available" year, not a
    fallback.

Known gap, documented honestly: 13 of 129 inner-city DeSO codes are new
2025-boundary splits with no data in this table yet (checked against the
table's own valid-region list). Those 13 zones get NO home mass until SCB
publishes their series — a small, bounded, disclosed limitation, not a
silent guess.

Writes:
  data_in/deso/deso_goteborg.geojson   — Gothenburg's 320 DeSO polygons
  data_in/deso/population_2023.json    — {desokod: population} for the
                                          116 inner-city zones with data
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from shapely.geometry import box, shape

from build_data import INNER_CITY_BBOX

OUT_DIR = Path("data_in/deso")
WFS_URL = ("https://geodata.scb.se/geoserver/stat/ows?service=WFS&version=2.0.0"
           "&request=GetFeature&typeNames=stat:DeSO_2025"
           "&outputFormat=application/json&srsName=EPSG:4326")
PXWEB_URL = ("https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101Y/"
             "FolkmDesoAldKon")
GOTEBORG_KOMMUNKOD = "1480"
POP_YEAR = "2023"   # newest year back-filled for DeSO-2025 boundaries


def fetch_json(url: str, data: bytes | None = None) -> dict:
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "gothenburg-traffic-sim (student project)"})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read())


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching DeSO 2025 boundaries (SCB WFS) …")
    all_deso = fetch_json(WFS_URL)
    goteborg = [f for f in all_deso["features"]
                if f["properties"]["kommunkod"] == GOTEBORG_KOMMUNKOD]
    print(f"  {len(goteborg)} DeSO in Göteborg kommun "
          f"(of {len(all_deso['features'])} in Sweden)")
    with open(OUT_DIR / "deso_goteborg.geojson", "w") as f:
        json.dump({"type": "FeatureCollection", "features": goteborg}, f)

    s, w, n, e = INNER_CITY_BBOX
    bbox = box(w, s, e, n)
    inner = [f["properties"]["desokod"] for f in goteborg
             if shape(f["geometry"]).intersects(bbox)]
    print(f"  {len(inner)} DeSO intersect the inner-city canvas")

    print(f"Checking which codes have data in FolkmDesoAldKon …")
    meta = fetch_json(PXWEB_URL)
    region_var = next(v for v in meta["variables"] if v["code"] == "Region")
    valid_codes = {v for v in region_var["values"] if not v.endswith("_DeSO2025")}
    ok = [c for c in inner if c in valid_codes]
    missing = [c for c in inner if c not in valid_codes]
    print(f"  {len(ok)}/{len(inner)} have data; {len(missing)} are new "
          f"2025-boundary splits with no series yet: {missing}")

    print(f"Fetching population, year {POP_YEAR} …")
    query = {
        "query": [
            {"code": "Region", "selection": {"filter": "item", "values": ok}},
            {"code": "Alder", "selection": {"filter": "item", "values": ["totalt"]}},
            {"code": "Kon", "selection": {"filter": "item", "values": ["1+2"]}},
            {"code": "ContentsCode",
             "selection": {"filter": "item", "values": ["000007Y7"]}},
            {"code": "Tid", "selection": {"filter": "item", "values": [POP_YEAR]}},
        ],
        "response": {"format": "json"},
    }
    result = fetch_json(PXWEB_URL, json.dumps(query).encode())
    pop = {row["key"][0]: int(row["values"][0]) for row in result["data"]}

    out = {
        "year": POP_YEAR,
        "source": "SCB BE0101Y/FolkmDesoAldKon via PXWeb API",
        "n_zones_with_data": len(pop),
        "n_zones_missing": len(missing),
        "missing_codes": missing,
        "population": pop,
    }
    with open(OUT_DIR / "population_2023.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"Wrote population_2023.json  "
          f"(total {sum(pop.values()):,} residents, {len(pop)} zones)")


if __name__ == "__main__":
    main()
