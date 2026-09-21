"""Diagnostic length-weighted path size; production PFE remains unchanged.

Run ``python3 -m traffic_sim.demand.route_regularization --help`` for a
read-only comparison. Cost differences are not held-out accuracy evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping
import xml.etree.ElementTree as ET

import numpy as np

from traffic_sim.demand.pfe import (
    Candidate, EPS_PARSIMONY, PATH_SIZE_FLOOR, path_size_weights,
)


def length_path_size_weights(shapes: list[Candidate],
                             edge_lengths: Mapping[str, float]) -> np.ndarray:
    """Compute EPS / sum((edge length / route length) / geometry count).

    Count identical route geometries once, independent of purpose. Keep the
    production floor and scale. Splitting an edge into consecutive pieces
    with equal combined length and identical route membership preserves cost.
    """
    counts: dict[str, int] = {}
    lengths: dict[str, float] = {}
    for geometry in {tuple(candidate.edges) for candidate in shapes}:
        if not geometry:
            raise ValueError("path size requires nonempty route geometries")
        for edge in set(geometry):
            try:
                length = float(edge_lengths[edge])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"missing or invalid length for edge {edge}") from exc
            if not math.isfinite(length) or length <= 0:
                raise ValueError(f"length must be finite and positive for edge {edge}")
            lengths[edge] = length
            counts[edge] = counts.get(edge, 0) + 1
    sizes = []
    for candidate in shapes:
        total = math.fsum(lengths[edge] for edge in candidate.edges)
        sizes.append(math.fsum((lengths[edge] / total) / counts[edge]
                               for edge in candidate.edges))
    return EPS_PARSIMONY / np.clip(sizes, PATH_SIZE_FLOOR, 1.0)


def compare(candidate_path: Path, network_path: Path) -> dict:
    """Bind geometry-only comparison to exact XML bytes; never run SUMO.

    The first lane supplies each edge's length, explicitly a diagnostic
    convention. Vehicle counts and purpose duplicates cannot alter the pool.
    """
    paths = [candidate_path, network_path, Path(__file__),
             Path(__file__).with_name("pfe.py")]
    identities = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in paths}
    shapes = [Candidate(0, list(edges)) for edges in sorted({
        tuple(route.get("edges", "").split())
        for route in ET.parse(candidate_path).getroot().iter("route")})]
    lengths = {}
    for edge in ET.parse(network_path).getroot().iter("edge"):
        lane = edge.find("lane")
        if lane is not None:
            lengths[edge.get("id")] = lane.get("length")
    before = path_size_weights(shapes)
    after = length_path_size_weights(shapes, lengths)
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != identities[str(path)]
           for path in paths):
        raise RuntimeError("input changed during comparison; discard result")
    ratios = after / before
    return {
        "protocol": "path_size_geometry_diagnostic_v1",
        "diagnostic_only": True, "production_policy_changed": False,
        "held_out_accuracy_measured": False, "input_sha256": identities,
        "edge_length_convention": "first SUMO lane length in metres",
        "unique_geometries": len(shapes),
        "changed_costs": int(np.count_nonzero(~np.isclose(before, after, rtol=1e-12, atol=0))),
        "cost_ratio_min_median_max": ([float(np.min(ratios)), float(np.median(ratios)),
                                       float(np.max(ratios))] if len(ratios) else []),
        "adoption_requirement": "Paired frozen LOSO and SUMO comparison with identical "
            "inputs, seeds, active sensor constraints and route provenance; no gate weakening.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compare(args.candidates, args.network), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
