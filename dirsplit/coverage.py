"""
Applicability: what kind of evidence stands behind each sensor edge's
direction estimate?

Usage:
  python3 -m dirsplit.coverage                  # full run (needs OSM graphs)
  python3 -m dirsplit.coverage --evidence-only  # profile from existing artifacts

The original check answered one question — do our Gothenburg sensor edges lie
inside the training stations' FEATURE distribution? For every matched Norwegian
station (both directions) and every Gothenburg sensor edge it computes the
shared feature vector, z-standardises on the training set, and measures each
target edge's mean distance to its KNN_K nearest training points, reported as a
percentile of the training set's own leave-one-out kNN distances:
   ≤ 90th percentile — inside the cloud, transfer is defensible
   > 90th            — extrapolation, flag it (prediction gets a warning)

That is necessary but far from sufficient, because static street features are
only one of the ways an estimate can be unsupported. Observability v2 therefore
reports an EVIDENCE PROFILE with separate dimensions (measurement level,
static domain, temporal support, feature compatibility, effective sample size,
calibration support, local cross-check), each of which can be strong while
another is weak.

The profile deliberately does NOT gate anything. Weak evidence widens
uncertainty and weakens the claim; it never removes a road from the network or
from a closure search, because "we know little about this street" is not the
same statement as "this street may not be studied". Only a genuinely impossible
input — a pair that cannot be resolved at all — is fail-closed, as
``unavailable``.

Automatically covers any NEW sensors added to network.geojson.

Writes data/dirsplit/coverage_report.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .config import COVERAGE_REPORT, KNN_K, STATIONS_OK
from .features import (FEATURE_NAMES, edge_bearing_from_graph, edge_features,
                       load_city_graph)

GEO_PATH = Path("web/data/network.geojson")
SENSOR_REGISTRY = Path("data_in/sensors.json")
PROFILE_PATH = Path("web/data/normal_profile.json")
BENCHMARK_REPORT = Path("validation/dirsplit_point_benchmark_v1.json")

#: Evidence classes. ``unavailable`` is the ONLY one that withholds a
#: prediction, and it means the input could not be resolved at all.
EVIDENCE_GOOD = "good"
EVIDENCE_LIMITED = "limited"
EVIDENCE_EXTRAPOLATION = "extrapolation"
EVIDENCE_UNAVAILABLE = "unavailable"


def training_matrix() -> tuple[np.ndarray, list[dict]]:
    with open(STATIONS_OK) as f:
        stations = [s for s in json.load(f) if s["matched"]]
    rows, meta = [], []
    for st in stations:
        G = load_city_graph(st["city"])
        u, v, k = map(int, st["osm_edge"].split("_"))
        data = G.get_edge_data(u, v, k)
        for heading, bearing in st["heading_bearings"].items():
            f = edge_features(st["city"], st["lat"], st["lon"], bearing, data)
            rows.append([f[n] for n in FEATURE_NAMES])
            meta.append({"station": st["id"], "city": st["city"],
                         "heading": heading})
    return np.array(rows, dtype=float), meta


def target_matrix() -> tuple[np.ndarray, list[dict]]:
    """Feature vectors for every sensor edge in network.geojson — new sensors
    are included automatically."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    G = load_city_graph("goteborg")
    rows, meta = [], []
    for feat in geo["features"]:
        p = feat["properties"]
        if not p.get("sensor_id"):
            continue
        u, v, k = map(int, p["id"].split("_"))
        data = G.get_edge_data(u, v, k)
        coords = feat["geometry"]["coordinates"]
        lat = (coords[0][1] + coords[-1][1]) / 2
        lon = (coords[0][0] + coords[-1][0]) / 2
        bearing = edge_bearing_from_graph(G, u, v, k, data)
        f = edge_features("goteborg", lat, lon, bearing, data)
        rows.append([f[n] for n in FEATURE_NAMES])
        meta.append({"edge": p["id"], "sensor": p["sensor_id"],
                     "name": p.get("name"), "features": f})
    return np.array(rows, dtype=float), meta


def knn_mean_dist(X_ref: np.ndarray, X_query: np.ndarray, k: int,
                  skip_self: bool) -> np.ndarray:
    """Mean Euclidean distance to the k nearest reference points."""
    out = np.empty(len(X_query))
    for i, q in enumerate(X_query):
        d = np.sqrt(((X_ref - q) ** 2).sum(axis=1))
        d.sort()
        out[i] = d[1:k + 1].mean() if skip_self else d[:k].mean()
    return out


def _registry_rows() -> dict[str, dict]:
    if not SENSOR_REGISTRY.exists():
        return {}
    payload = json.loads(SENSOR_REGISTRY.read_text())
    rows = payload if isinstance(payload, list) else payload.get("sensors", [])
    return {str(row["sensor_id"]): row for row in rows}


def measurement_level(record: dict | None, edge_id: str) -> dict:
    """How much of THIS edge's direction is measured rather than modelled."""
    if record is None:
        return {"level": "transfer_prior_only",
                "detail": "edge has no validated registry record"}
    approved = set(record.get("approved_edge_ids") or ())
    opposite = ((record.get("opposite_direction") or {}).get("edge_id"))
    reference = record.get("directional_reference")
    if record.get("measurement_semantics") == "two_way_total":
        level = {"level": "two_way_total_split_estimated",
                 "detail": ("the station's total is measured; how it divides "
                            "between the carriageways is estimated")}
        if reference:
            level["period_anchor"] = {
                "period": (reference.get("period") or {}).get("label"),
                "source": (reference.get("source") or {}).get("name"),
                "time_semantics": reference.get("time_semantics"),
            }
        return level
    if edge_id in approved:
        return {"level": "direction_measured",
                "detail": "this carriageway is measured; its value is level-1"}
    if opposite and edge_id == opposite:
        return {"level": "opposite_carriageway_estimated",
                "detail": ("unmeasured carriageway; enters as a level-2 bound, "
                           "never as a target")}
    return {"level": "transfer_prior_only", "detail": "edge is not in the registry"}


def temporal_support(meta_path: Path | None = None) -> dict:
    """Which hours and day types the training data actually covers.

    The deployed model is trained on weekday 06–20 only, while predictions are
    produced for all 24 hours and reused across the calendar. That gap is a
    property of the evidence and has to be stated, not smoothed over.
    """
    from .benchmark import SUPPORTED_HOURS
    from .dataset import META_PATH_V2

    meta_path = meta_path or META_PATH_V2
    support = {
        "trained_hours": [min(SUPPORTED_HOURS), max(SUPPORTED_HOURS)],
        "trained_day_types": ["weekday"],
        "predicted_hours": [0, 23],
        "predicted_day_types": ["weekday", "weekend", "holiday"],
        "status": EVIDENCE_LIMITED,
        "detail": ("weekend and 21:00–05:00 predictions are extrapolation: the "
                   "deployed training band is weekday 06–20"),
    }
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        support["raw_rows"] = meta.get("rows")
        support["labelled_rows"] = meta.get("rows_with_label")
        support["independent_day_blocks"] = meta.get("independent_day_blocks")
        support["source_resolution"] = meta.get("source_resolution")
    else:
        support["detail"] += ("; the raw table is absent, so day-level support "
                              "is unmeasured")
    return support


def feature_compatibility(edge_id: str, level: str,
                          profile_path: Path | None = None) -> dict:
    """Does this edge's profile input mean the same thing as in training?"""
    profile_path = profile_path or PROFILE_PATH
    inference_path = ("paired_total" if level == "two_way_total_split_estimated"
                      else "single_direction")
    available = False
    if profile_path.exists():
        profiles = json.loads(profile_path.read_text()).get("profiles", {})
        available = bool((profiles.get(edge_id) or {}).get("weekday"))
    return {
        "inference_path": inference_path,
        "profile_available": available,
        "status": EVIDENCE_GOOD if inference_path == "paired_total" and available
        else EVIDENCE_LIMITED,
        "detail": ("training profile features are computed from BOTH directions; "
                   "a single-direction station can only supply its own side, so "
                   "the same feature name would not mean the same thing — the "
                   "v2 table keeps the two under separate names"),
    }


def effective_sample_size(train_meta: list[dict] | None = None) -> dict:
    """Independent evidence behind the transfer, not merely row count."""
    from .dataset import META_PATH_V2

    result: dict = {"training_station_directions": len(train_meta or []) or None,
                    "independent_day_blocks": None,
                    "status": EVIDENCE_LIMITED}
    if META_PATH_V2.exists():
        meta = json.loads(META_PATH_V2.read_text())
        result["independent_day_blocks"] = meta.get("independent_day_blocks")
        result["labelled_rows"] = meta.get("rows_with_label")
    result["detail"] = ("hours within one station-day are strongly correlated; "
                        "the independent unit is a day block, not a row")
    return result


def calibration_support() -> dict:
    """What held-out validation currently says — including that it is unfinished."""
    if not BENCHMARK_REPORT.exists():
        return {"status": EVIDENCE_UNAVAILABLE,
                "interval_calibration": "unmeasured",
                "detail": ("no point benchmark has been run; the deployed "
                           "q10/q90 have never been checked for coverage")}
    report = json.loads(BENCHMARK_REPORT.read_text())
    gate = report.get("gate_m", {})
    return {
        "status": {"MODEL": EVIDENCE_GOOD, "BASELINE": EVIDENCE_GOOD}.get(
            gate.get("verdict"), EVIDENCE_LIMITED),
        "gate_m": gate.get("verdict"),
        "gate_m_reasons": gate.get("reasons", []),
        "table": report.get("table"),
        "interval_calibration": "unmeasured",
        "detail": ("point transfer is benchmarked; the q10/q90 interval's "
                   "nominal 80% coverage is still unvalidated, so it is a "
                   "stress band, not a probability statement"),
    }


def local_crosscheck(record: dict | None) -> dict:
    """A real local directional measurement beats any transferred model."""
    reference = (record or {}).get("directional_reference")
    if not reference:
        return {"available": False,
                "detail": ("no published per-direction volume for this station; "
                           "the split rests on transfer plus conservation")}
    total = sum(float(direction["value"]) for direction in reference["directions"])
    return {
        "available": True,
        "period": (reference.get("period") or {}).get("label"),
        "shares": {direction["edge_id"]: round(float(direction["value"]) / total, 4)
                   for direction in reference["directions"]},
        "time_semantics": reference.get("time_semantics"),
        "source": (reference.get("source") or {}).get("name"),
        "detail": ("period aggregate only: it anchors the period mean and "
                   "carries no per-slot authority"),
    }


def evidence_profile(edge_id: str, sensor_id: str, *, static_status: str,
                     train_meta: list[dict] | None = None) -> dict:
    """The multi-dimensional evidence behind one edge's direction estimate."""
    record = _registry_rows().get(str(sensor_id))
    level = measurement_level(record, edge_id)
    compatibility = feature_compatibility(edge_id, level["level"])
    temporal = temporal_support()
    dimensions = {
        "measurement_level": level,
        "static_domain": {"status": (EVIDENCE_GOOD if static_status == "OK"
                                     else EVIDENCE_EXTRAPOLATION),
                          "knn_status": static_status},
        "temporal_support": temporal,
        "feature_compatibility": compatibility,
        "effective_sample_size": effective_sample_size(train_meta),
        "calibration_support": calibration_support(),
        "local_crosscheck": local_crosscheck(record),
    }
    statuses = {dimensions["static_domain"]["status"],
                temporal["status"], compatibility["status"],
                dimensions["calibration_support"]["status"]}
    if EVIDENCE_EXTRAPOLATION in statuses:
        overall = EVIDENCE_EXTRAPOLATION
    elif statuses - {EVIDENCE_GOOD}:
        overall = EVIDENCE_LIMITED
    else:
        overall = EVIDENCE_GOOD
    return {
        "edge": edge_id,
        "sensor": str(sensor_id),
        "evidence": overall,
        "dimensions": dimensions,
        # The rule, restated where it is consumed: evidence strength changes
        # the width of a claim, never a street's right to be studied.
        "usage": {
            EVIDENCE_GOOD: "normal central profile",
            EVIDENCE_LIMITED: "central profile with a weaker claim and wider stated uncertainty",
            EVIDENCE_EXTRAPOLATION: "conservative fallback; report inconclusive if the choice depends on it",
            EVIDENCE_UNAVAILABLE: "fail closed: no estimate can be produced",
        }[overall],
        "excludes_road": False,
    }


def build_evidence_report(coverage_report: dict,
                          train_meta: list[dict] | None = None) -> dict:
    """Attach an evidence profile to an existing kNN coverage report."""
    edges = []
    for edge in coverage_report.get("edges", []):
        edges.append(evidence_profile(edge["edge"], edge["sensor"],
                                      static_status=edge.get("status", "?"),
                                      train_meta=train_meta))
    return {
        "schema_version": "dirsplit_observability_v2",
        "dimensions": ["measurement_level", "static_domain", "temporal_support",
                       "feature_compatibility", "effective_sample_size",
                       "calibration_support", "local_crosscheck"],
        "policy": ("Evidence strength widens or narrows a claim. It never "
                   "excludes a road from simulation or from a closure study; "
                   "only an unresolvable input is fail-closed."),
        "edges": edges,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--evidence-only", action="store_true",
                        help="Rebuild the evidence profile from the existing "
                             "coverage report without recomputing features "
                             "(no OSM graph needed).")
    args = parser.parse_args()

    if args.evidence_only:
        if not COVERAGE_REPORT.exists():
            raise SystemExit(f"{COVERAGE_REPORT} is missing — run a full "
                             f"coverage pass first")
        report = json.loads(COVERAGE_REPORT.read_text())
        report["observability"] = build_evidence_report(report)
        COVERAGE_REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=1))
        print(f"{'Sensor':<7} {'Kant':<26} {'evidens':>14}  användning")
        for edge in report["observability"]["edges"]:
            print(f"{edge['sensor']:<7} {edge['edge']:<26} "
                  f"{edge['evidence']:>14}  {edge['usage']}")
        print(f"\nWrote {COVERAGE_REPORT} (observability v2 only)")
        return

    X_train, train_meta = training_matrix()
    X_tgt, tgt_meta = target_matrix()
    print(f"Training points: {len(X_train)} directed station-directions, "
          f"targets: {len(X_tgt)} sensor edges")

    mu, sd = X_train.mean(axis=0), X_train.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Zt = (X_train - mu) / sd
    Zq = (X_tgt - mu) / sd

    ref_d = knn_mean_dist(Zt, Zt, KNN_K, skip_self=True)   # training self-distances
    tgt_d = knn_mean_dist(Zt, Zq, KNN_K, skip_self=False)

    report = {"knn_k": KNN_K, "n_train": len(X_train),
              "feature_names": FEATURE_NAMES, "edges": []}
    print(f"\n{'Sensor':<7} {'Kant':<26} {'kNN-dist':>9} {'percentil':>10}  status")
    for meta, d in zip(tgt_meta, tgt_d):
        pctl = float((ref_d < d).mean() * 100)
        status = "OK" if pctl <= 90 else "EXTRAPOLATION"
        print(f"{meta['sensor']:<7} {meta['edge']:<26} {d:>9.2f} {pctl:>9.0f}%  {status}")
        report["edges"].append({**{k: v for k, v in meta.items() if k != 'features'},
                                "knn_dist": round(float(d), 3),
                                "train_percentile": round(pctl, 1),
                                "status": status,
                                "features": meta["features"]})

    report["observability"] = build_evidence_report(report, train_meta)
    print(f"\n{'Sensor':<7} {'Kant':<26} {'evidens':>14}  användning")
    for edge in report["observability"]["edges"]:
        print(f"{edge['sensor']:<7} {edge['edge']:<26} "
              f"{edge['evidence']:>14}  {edge['usage']}")

    with open(COVERAGE_REPORT, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {COVERAGE_REPORT}")


if __name__ == "__main__":
    main()
