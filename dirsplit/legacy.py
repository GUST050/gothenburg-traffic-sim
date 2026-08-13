"""Read-only adapter for the legacy q10/q50/q90 direction-split archive.

Plan invariant 8: old q-archives are read through an adapter, are never
migrated in place, and never become release evidence for the new policy
automatically. This module is therefore strictly read-only — it opens
`sumo/direction_split.json`, reports what is in it, and returns v2 objects
that are honest about the archive's status. It never writes.

Two things it deliberately refuses to do:

**It does not turn q10/q90 into probabilistic cases.** They arrive as
``kind="stress"`` with no weight, because their nominal 80% coverage has never
been validated out of sample. `schema.DemandCase` makes a weighted stress case
unrepresentable, so this is enforced rather than merely intended.

**It does not silently repair the pair-sum defect it finds.**

────────────────────────────────────────────────────────────────────────────
THE PAIR-SUM DEFECT, found 2026-08-13 while writing this adapter

`dirsplit/predict.py` writes the three share maps like this (lines 205-211):

    edge_shares      : e0 -> s50   e1 -> 1 - s50
    edge_shares_q10  : e0 -> s10   e1 -> 1 - s90
    edge_shares_q90  : e0 -> s90   e1 -> 1 - s10

The q50 pair sums to 1 by construction. The other two do not:

    q10 pair sum = s10 + (1 - s90) = 1 - (s90 - s10)
    q90 pair sum = s90 + (1 - s10) = 1 + (s90 - s10)

A directed pair's shares split ONE measured two-way total, so they are an
identity, not a fitted quantity: they must sum to 1. Taking the low end of
e0's interval *and* the low end of e1's interval double-counts the same
uncertainty and produces an infeasible world.

The consequence is not cosmetic. At a genuinely two-way sensor — sensor 107
is the only one in the current registry — `demand/intake.py:build_targets`
multiplies the measured two-way count by each edge's share to form the
**level-1 targets**. With the tracked median interval width of 0.107 that
means the q10 arm asks the calibrator to hit targets summing to ~89.3% of the
measured total, and the q90 arm ~110.7%. Roughly a tenth of the measured
traffic is deleted in one arm and invented in the other.

This is the mechanism behind the observation recorded in the plan's audit
table that "ytterfallen ändrar också nätets totala belastning, inte bara
riktningen" — the tracked route files hold 19,845 / 20,836 / 21,749 vehicles,
a 9.6% spread that matches the 10.7% interval width. The plan measured the
symptom; this is the cause.

It is also a live violation of plan invariant 1 ("two directions sum to 1.0 in
every slot and scenario before rounding") by the currently shipped artifact.

`load_legacy_archive` therefore reports every violating slot in
``pair_sum_defects`` and leaves the numbers exactly as found. Repair belongs
to the v2 generator (step 4), which builds coherent cases from residual
blocks; silently renormalising here would erase the evidence that the old
arm was infeasible.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .schema import (PAIR_SUM_TOLERANCE, SLOT_COUNT, Applicability,
                     CentralProfile, DemandCase, SchemaError, content_digest,
                     make_case_id)

LEGACY_PATH = Path("sumo/direction_split.json")

#: The legacy seed→variant mapping, kept here so contract tests can pin the
#: behaviour that step 6 replaces. Reproduces
#: `run_scenario.seed_variant_plan`'s `variants[index % len(variants)]` for
#: the default three seeds, and `contracts.ScenarioSpec`'s default
#: `demand_variant_mapping`.
LEGACY_SEED_VARIANTS: tuple[tuple[int, str], ...] = (
    (1000, "q50"), (1001, "q10"), (1002, "q90"),
)

#: Legacy key → the variant name used everywhere else in the tree.
LEGACY_KEYS: Mapping[str, str] = {
    "edge_shares": "q50",
    "edge_shares_q10": "q10",
    "edge_shares_q90": "q90",
}


@dataclass(frozen=True)
class PairSumDefect:
    """One directed pair whose shares do not sum to 1 in some slot."""

    variant: str
    edge_a: str
    edge_b: str
    slot: int
    pair_sum: float

    @property
    def deficit(self) -> float:
        """Signed distance from the identity. Negative = traffic deleted."""
        return self.pair_sum - 1.0


@dataclass(frozen=True)
class LegacyArchive:
    """Everything the old file says, plus what is wrong with it."""

    path: str
    digest: str
    sensors: tuple[str, ...]
    central: CentralProfile | None
    stress_cases: tuple[DemandCase, ...]
    applicability: tuple[Applicability, ...]
    pair_sum_defects: tuple[PairSumDefect, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_pair_sum_valid(self) -> bool:
        return not self.pair_sum_defects

    def defect_summary(self) -> dict[str, Any]:
        """Compact, quotable summary of the defect for reports and tests."""
        by_variant: dict[str, dict[str, float]] = {}
        for defect in self.pair_sum_defects:
            bucket = by_variant.setdefault(
                defect.variant,
                {"slots": 0, "worst_abs_deficit": 0.0, "mean_deficit": 0.0},
            )
            bucket["slots"] += 1
            bucket["mean_deficit"] += defect.deficit
            bucket["worst_abs_deficit"] = max(
                bucket["worst_abs_deficit"], abs(defect.deficit)
            )
        for bucket in by_variant.values():
            if bucket["slots"]:
                bucket["mean_deficit"] /= bucket["slots"]
        return by_variant


def _pairs_from_block(block: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """A sensor block names exactly its two directed edges."""
    shares = block.get("edge_shares") or {}
    edges = tuple(sorted(shares))
    if len(edges) != 2:
        raise SchemaError(
            f"legacy sensor block must hold exactly 2 directed edges, "
            f"got {len(edges)}: {edges}"
        )
    return ((edges[0], edges[1]),)


def _collect_defects(
    variant: str,
    shares: Mapping[str, list[float]],
    pairs: tuple[tuple[str, str], ...],
) -> list[PairSumDefect]:
    defects: list[PairSumDefect] = []
    for a, b in pairs:
        if a not in shares or b not in shares:
            continue
        for slot in range(min(SLOT_COUNT, len(shares[a]), len(shares[b]))):
            total = float(shares[a][slot]) + float(shares[b][slot])
            if abs(total - 1.0) > PAIR_SUM_TOLERANCE:
                defects.append(
                    PairSumDefect(variant, a, b, slot, round(total, 12))
                )
    return defects


def _renormalised(shares: Mapping[str, list[float]],
                  pairs: tuple[tuple[str, str], ...]) -> dict[str, tuple[float, ...]]:
    """Pair-normalise so the result can be represented as a DemandCase.

    This exists ONLY so the defective legacy arm can still be *described* by
    the v2 types, which refuse an infeasible pair. The defect itself is
    reported separately and unmodified in `LegacyArchive.pair_sum_defects`;
    this normalisation is never written back to the archive, and the case it
    produces is a stress case with no probability weight.
    """
    out: dict[str, tuple[float, ...]] = {}
    for a, b in pairs:
        series_a = [float(x) for x in shares[a][:SLOT_COUNT]]
        series_b = [float(x) for x in shares[b][:SLOT_COUNT]]
        norm_a: list[float] = []
        norm_b: list[float] = []
        for x, y in zip(series_a, series_b):
            total = x + y
            if total <= 0:
                norm_a.append(0.5)
                norm_b.append(0.5)
            else:
                norm_a.append(x / total)
                norm_b.append(y / total)
        out[a] = tuple(norm_a)
        out[b] = tuple(norm_b)
    return out


def load_legacy_archive(path: Path | str = LEGACY_PATH) -> LegacyArchive | None:
    """Read the legacy archive. Returns None when it does not exist.

    Never writes, never repairs in place. `sumo/` is a gitignored build
    intermediate, so a fresh clone legitimately has no archive at all — that
    is a None, not an error.
    """
    archive_path = Path(path)
    if not archive_path.exists():
        return None
    raw_text = archive_path.read_text()
    data = json.loads(raw_text)

    central_shares: dict[str, tuple[float, ...]] = {}
    all_pairs: list[tuple[str, str]] = []
    per_variant: dict[str, dict[str, list[float]]] = {
        name: {} for name in LEGACY_KEYS.values()
    }
    defects: list[PairSumDefect] = []
    applicability: list[Applicability] = []

    for sensor_id, block in sorted(data.items()):
        pairs = _pairs_from_block(block)
        all_pairs.extend(pairs)
        for legacy_key, variant in LEGACY_KEYS.items():
            shares = block.get(legacy_key)
            if not shares:
                continue
            per_variant[variant].update(
                {k: [float(x) for x in v] for k, v in shares.items()}
            )
            defects.extend(_collect_defects(variant, shares, pairs))
        for edge_id in sorted(block.get("edge_shares") or {}):
            status = (block.get("coverage_status") or {}).get(edge_id, "?")
            applicability.append(
                Applicability(
                    edge_id=edge_id,
                    # The legacy file cannot distinguish these; a two-way
                    # total is not a directional measurement.
                    measurement_level="transfer_prior",
                    static_domain=("extrapolation"
                                   if str(status).upper().startswith("EXTRA")
                                   else "good"),
                    # The legacy model trained weekdays 06-20 only and
                    # predicted all 24 hours with is_weekend=0.
                    temporal_support="extrapolation",
                    feature_compatibility="limited",
                    effective_sample_size=0.0,
                    # No coverage was ever measured for this archive.
                    calibration_support="unavailable",
                    detail={"legacy_coverage_status": status,
                            "sensor_id": sensor_id},
                )
            )

    pairs_tuple = tuple(all_pairs)
    central: CentralProfile | None = None
    if per_variant["q50"]:
        central_shares = {
            k: tuple(v[:SLOT_COUNT]) for k, v in per_variant["q50"].items()
        }
        central = CentralProfile(
            model_id="legacy_dirsplit_q50",
            day_type="weekday",
            shares=central_shares,
            edge_pairs=pairs_tuple,
        )

    stress: list[DemandCase] = []
    for variant in ("q10", "q90"):
        shares = per_variant[variant]
        if not shares:
            continue
        normalised = _renormalised(shares, pairs_tuple)
        case_id = make_case_id(
            generator=f"legacy_{variant}",
            day_type="weekday",
            shares=normalised,
            provenance={"legacy_variant": variant},
        )
        stress.append(
            DemandCase(
                case_id=case_id,
                kind="stress",                       # never probabilistic
                day_type="weekday",
                shares=normalised,
                edge_pairs=pairs_tuple,
                generator=f"legacy_marginal_{variant}",
                provenance={
                    "legacy_variant": variant,
                    "pair_normalised": True,
                    "reason": (
                        "legacy marginal arm violates the pair-sum identity; "
                        "normalised only so it can be represented, defect "
                        "reported separately and archive left unmodified"
                    ),
                },
            )
        )

    return LegacyArchive(
        path=str(archive_path),
        digest=content_digest(raw_text),
        sensors=tuple(sorted(data)),
        central=central,
        stress_cases=tuple(stress),
        applicability=tuple(applicability),
        pair_sum_defects=tuple(defects),
        raw=data,
    )


def legacy_variant_for_seed(seed: int) -> str | None:
    """What the OLD contract would have inferred from a seed alone.

    Kept only so tests can pin the behaviour step 6 removes. New code must
    never call this: in v2 a seed carries no demand information whatsoever,
    which is why `schema.SimulationMember` requires both coordinates.
    """
    for known_seed, variant in LEGACY_SEED_VARIANTS:
        if known_seed == seed:
            return variant
    return None


__all__ = [
    "LEGACY_PATH", "LEGACY_KEYS", "LEGACY_SEED_VARIANTS",
    "PairSumDefect", "LegacyArchive", "load_legacy_archive",
    "legacy_variant_for_seed",
]
