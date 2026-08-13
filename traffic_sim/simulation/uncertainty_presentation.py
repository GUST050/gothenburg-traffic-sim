"""What the UI must show about uncertainty (plan step 7, Phase D).

The plan's display rules are not decoration — each exists because the current
UI can state something it has not earned:

* the central estimate, labelled as ONE run, never as the analysis;
* the interval, labelled ``calibrated`` or ``uncalibrated stress interval``,
  never "80%" unless coverage was measured;
* how many scenario paths, demand cases and seeds actually ran;
* scenario and seed convergence;
* observability/applicability for the affected roads;
* whether the winner is robust, conditional, or inconclusive.

This module builds that payload as data, with the labels chosen here rather
than in JavaScript. Putting them in Python means the rule "an uncalibrated
interval cannot say 80%" is testable, and a UI change cannot quietly reword
it. `serve.py` and the web layer render what they are given.

The single most important property, tested below: no non-winning outcome may
produce a payload that a reader — or a caller checking only for absence of
error — can mistake for a winner. ``tie``, ``inconclusive``, ``no_viable``
and ``paused`` each get their own headline, their own explanation, and
``winner: null``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from dirsplit.schema import Applicability
from traffic_sim.simulation.uncertainty_policy import (MIN_SEEDS, PolicyResult,
                                                       POLICY_VERSION)

PRESENTATION_VERSION = "closure_uncertainty_presentation_v1"

#: Headlines per outcome. Swedish, matching the rest of the product UI.
#: None of the non-winning ones contains a candidate name.
HEADLINES: Mapping[str, str] = {
    "unique_winner": "Ett alternativ är bäst",
    "tie": "Oavgjort — alternativen kan inte skiljas åt",
    "inconclusive": "Inget svar än — underlaget räcker inte",
    "no_viable": "Inget alternativ är möjligt",
    "paused": "Pausad — körningen slutfördes inte",
}

#: Explanations, all stating what is missing rather than implying a result.
EXPLANATIONS: Mapping[str, str] = {
    "unique_winner": ("Ett alternativ är bäst på alla riskmått och "
                      "rangordningen har slutat ändra sig."),
    "tie": ("Skillnaden är mindre än osäkerheten, eller så pekar olika "
            "riskmått på olika alternativ. Ingen vinnare publiceras."),
    "inconclusive": ("Scenario- eller seed-budgeten är inte klar, eller så "
                     "har rangordningen inte stabiliserats. Ingen vinnare "
                     "publiceras."),
    "no_viable": ("Alla alternativ föll på en hård säkerhetsgrind "
                  "(framkomlighet, topologi eller hälsa)."),
    "paused": ("Körningen pausades innan budgeten var klar. Resultatet är "
               "ofullständigt och ingen vinnare publiceras."),
}


def interval_label(calibration_status: str,
                   nominal_coverage: float | None = None,
                   empirical_coverage: float | None = None) -> str:
    """The only place an interval gets a human label.

    A nominal level may be shown ONLY when coverage was measured. Everything
    else is an uncalibrated stress interval, said plainly.
    """
    if calibration_status == "calibrated" and empirical_coverage is not None:
        pct = round(100 * float(nominal_coverage or 0.0))
        measured = round(100 * float(empirical_coverage))
        return f"{pct}% intervall (uppmätt täckning {measured}%)"
    if calibration_status == "unavailable":
        return "Intervall saknas"
    return "Okalibrerat stressintervall — ingen sannolikhet påstås"


def claim_label(claim_strength: str) -> str:
    return {
        "normal": "Normal tillförlitlighet",
        "widened": "Bredare osäkerhet — tunt underlag",
        "conservative_fallback": "Extrapolation — konservativt antagande",
        "unavailable": "Otillgänglig — indatafel",
    }.get(claim_strength, claim_strength)


@dataclass(frozen=True)
class RunInventory:
    """What actually ran. Separated so the UI can show all three axes."""

    scenario_paths: int
    demand_cases: int
    seeds_per_case: int
    stress_cases: int = 0

    @property
    def seeds_sufficient(self) -> bool:
        return self.seeds_per_case >= MIN_SEEDS

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_paths": self.scenario_paths,
            "demand_cases": self.demand_cases,
            "seeds_per_case": self.seeds_per_case,
            "stress_cases": self.stress_cases,
            "seeds_sufficient": self.seeds_sufficient,
            "note": (
                "Demand-osäkerhet (scenarier) och simulatorns slump (seeds) "
                "är separata axlar. Samma seed-lista körs för varje scenario "
                "och för både bas och kandidat."
            ),
        }


def build_payload(
    result: PolicyResult,
    inventory: RunInventory,
    *,
    calibration_status: str = "stress_only",
    nominal_coverage: float | None = 0.8,
    empirical_coverage: float | None = None,
    applicability: Sequence[Applicability] = (),
    central_run_id: str | None = None,
) -> dict[str, Any]:
    """The complete uncertainty payload the UI renders."""
    outcome = result.decision.outcome
    is_winner = outcome == "unique_winner"

    roads = [
        {
            "edge_id": profile.edge_id,
            "grade": profile.grade(),
            "claim_strength": profile.claim_strength(),
            "claim_label": claim_label(profile.claim_strength()),
            "measurement_level": profile.measurement_level,
            # Never filtered out: a low-evidence road is SHOWN, flagged.
            "included_in_search": not profile.blocks_publication,
            "excluded_reason": ("indatafel" if profile.blocks_publication
                                else None),
        }
        for profile in applicability
    ]

    return {
        "presentation_version": PRESENTATION_VERSION,
        "policy_version": POLICY_VERSION,
        "outcome": outcome,
        "is_winner": is_winner,
        "winner": result.decision.winner if is_winner else None,
        "headline": HEADLINES.get(outcome, "Okänt utfall"),
        "explanation": EXPLANATIONS.get(outcome, result.decision.reason),
        "reason_detail": result.decision.reason,
        "central_run": {
            "run_id": central_run_id,
            "label": "En representativ körning",
            "note": ("Animationen visar EN körning, inte hela "
                     "osäkerhetsanalysen."),
        },
        "demand_uncertainty": {
            "interval_label": interval_label(calibration_status,
                                             nominal_coverage,
                                             empirical_coverage),
            "calibration_status": calibration_status,
            "is_probability_statement": calibration_status == "calibrated",
        },
        "seed_uncertainty": {
            "seeds_per_case": inventory.seeds_per_case,
            "sufficient": inventory.seeds_sufficient,
            "note": ("Simulatorns slump mäts med upprepade seeds inom varje "
                     "scenario, matchat mellan bas och kandidat."),
        },
        "inventory": inventory.to_json(),
        "convergence": result.stability.to_json(),
        "scores": [score.to_json() for score in result.scores],
        "agreeing_measures": dict(result.agreeing_measures),
        "roads": roads,
        "roads_excluded_for_low_evidence": 0,
    }


def assert_no_false_winner(payload: Mapping[str, Any]) -> None:
    """Guard: a non-winning payload must not read as a win.

    Called by the API before serving. It is cheap, and the failure it
    prevents — a UI showing a candidate name next to an inconclusive run —
    is the kind that gets a street closed on evidence that does not exist.
    """
    if payload.get("outcome") == "unique_winner":
        if not payload.get("winner"):
            raise ValueError("a unique_winner payload must name a winner")
        return
    if payload.get("winner") is not None:
        raise ValueError(
            f"outcome {payload.get('outcome')!r} must not name a winner"
        )
    if payload.get("is_winner"):
        raise ValueError(
            f"outcome {payload.get('outcome')!r} must not set is_winner"
        )


__all__ = [
    "PRESENTATION_VERSION", "HEADLINES", "EXPLANATIONS", "RunInventory",
    "interval_label", "claim_label", "build_payload",
    "assert_no_false_winner",
]
