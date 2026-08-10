"""Re-score a stored campaign's disqualifications under the current rules.

Closes the OPEN item in `docs/OPEN_ISSUES_2026-08-06.md` §5:

    OPEN — 21 of 61 disqualified v9 schedules carried no teleports and no
    throughput. They failed on unreachability alone. C1 should have addressed
    this; it has not been re-measured against a fresh campaign.

and the unplanned finding recorded by Stage 1 in
`validation/closure_leak_mechanism_v1.json`:

    schedules disqualified with no teleport and no throughput at all. Fixing
    teleports alone cannot make these eligible; they are vehicles with no
    route, a failure mode the plan's stages 2-3 do not address.

WHAT THIS IS.  A re-score of evidence already on disk: it reads a campaign's
`outcomes.json`, takes each schedule's recorded `hard_failures`, and partitions
them by what the CURRENT rules would do with each reason —

  * access reasons (`dropped_unreachable_vehicles`,
    `truncated_unreachable_vehicles`) stopped being hard failures on
    2026-08-06 (C1, `metrics.access_impact_reasons`);
  * `teleports` cannot arise at all under the Stage 3 policy
    (`closure_teleport.CLOSURE_TIME_TO_TELEPORT_S`);
  * `active_closure_edge_throughput` had a teleport as its necessary
    condition in all 75 measured schedules, so it too is expected to go —
    EXPECTED, which is not the same as measured;
  * everything else (`queue_proxy_unmeasured`, `unfinished_vehicle_share`,
    any `baseline_*` reason) is untouched by either change and still
    disqualifies.

WHAT THIS IS NOT, and the distinction matters.  It is NOT a prediction that a
re-run campaign passes. Removing teleporting changes the simulation: vehicles
that were relocated now stay put, which changes queues, arrivals and therefore
every downstream number. The reason-level re-score can only answer "which
recorded failures are still failures under today's rules", and that is what it
reports. `projected_eligible` is labelled as a projection everywhere it
appears, and the tool refuses to emit a pass/fail verdict on the campaign gate.

Reads only. It runs no simulation, writes nothing into the campaign root, and
touches no release or active study.

Run:
    python3 tools/remeasure_closure_disqualification.py \
        --outcomes runs/closure-proxy-validation/<key>/outcomes.json \
        [--out validation/closure_disqualification_rescore_v1.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "validation" / "closure_disqualification_rescore_v1.json"

#: Reclassified as IMPACT rather than failure by C1 (2026-08-06). A schedule
#: held back only by these is eligible under today's rules.
ACCESS_REASONS = frozenset({
    "dropped_unreachable_vehicles",
    "truncated_unreachable_vehicles",
})

#: Removed by construction once SUMO cannot relocate a stuck vehicle.
TELEPORT_REASON = "teleports"

#: Removed only INDIRECTLY: Stage 1 measured a teleport as the necessary
#: condition for throughput (0 of 35 throughput schedules lacked one), so
#: removing teleports is expected to remove this too. Kept in its own bucket
#: because "expected from a necessary condition" is weaker evidence than
#: "impossible by construction", and collapsing the two would overstate the
#: projection.
THROUGHPUT_REASON = "active_closure_edge_throughput"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outcomes", required=True, metavar="PATH",
                        help="A campaign's outcomes.json (or outcomes.partial.json).")
    parser.add_argument("--out", default=str(DEFAULT_OUT), metavar="PATH")
    parser.add_argument("--stdout", action="store_true",
                        help="Print the report instead of writing a file.")
    return parser.parse_args(argv)


def classify(reasons) -> dict:
    """Bucket one schedule's recorded hard failures.

    `remaining` is the honest answer: the reasons neither change touches. A
    schedule is projected eligible only when that set is empty — never because
    the interesting reasons happened to dominate.
    """
    reasons = sorted({str(reason) for reason in reasons})
    access = [r for r in reasons if r in ACCESS_REASONS]
    teleports = [r for r in reasons if r == TELEPORT_REASON]
    throughput = [r for r in reasons if r == THROUGHPUT_REASON]
    remaining = [r for r in reasons
                 if r not in ACCESS_REASONS and r != TELEPORT_REASON
                 and r != THROUGHPUT_REASON]
    return {
        "reasons": reasons,
        "access_only_reasons": access,
        "teleport_reasons": teleports,
        "throughput_reasons": throughput,
        "reasons_still_disqualifying": remaining,
        "projected_eligible": not remaining,
        "projection_depends_on_teleport_removal": bool(teleports or throughput),
    }


def rescore(outcomes: dict) -> dict:
    """Partition every schedule of a stored campaign."""
    cases = outcomes.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SystemExit("outcomes file carries no cases")

    per_case = []
    totals = {
        "schedules": 0,
        "eligible_as_recorded": 0,
        "disqualified_as_recorded": 0,
        "access_only": 0,
        "teleport_or_throughput_only": 0,
        "access_and_teleport_only": 0,
        "still_disqualified": 0,
        "projected_eligible": 0,
    }
    for case in cases:
        candidates = case.get("candidates")
        if candidates is None:
            candidates = case.get("schedules", [])
        if isinstance(candidates, dict):
            candidates = [candidates[key] for key in sorted(candidates)]
        rows = []
        for candidate in candidates:
            recorded_eligible = bool(candidate.get("eligible"))
            verdict = classify(candidate.get("hard_failures") or [])
            totals["schedules"] += 1
            if recorded_eligible:
                totals["eligible_as_recorded"] += 1
            else:
                totals["disqualified_as_recorded"] += 1
                if verdict["projected_eligible"]:
                    if verdict["access_only_reasons"] and \
                            not verdict["projection_depends_on_teleport_removal"]:
                        totals["access_only"] += 1
                    elif verdict["access_only_reasons"]:
                        totals["access_and_teleport_only"] += 1
                    else:
                        totals["teleport_or_throughput_only"] += 1
                else:
                    totals["still_disqualified"] += 1
            if verdict["projected_eligible"]:
                totals["projected_eligible"] += 1
            rows.append({
                "schedule_id": candidate.get("schedule_id"),
                "eligible_as_recorded": recorded_eligible,
                **verdict,
            })
        # A case becomes RANKABLE when at least two schedules can be ordered;
        # one eligible schedule has nothing to be ranked against. This mirrors
        # the campaign gate's own notion and is reported per case so the
        # projection can be read where the gate actually applies.
        projected = sum(1 for row in rows if row["projected_eligible"])
        per_case.append({
            "case_id": case.get("case_id"),
            "schedules": len(rows),
            "eligible_as_recorded":
                sum(1 for row in rows if row["eligible_as_recorded"]),
            "projected_eligible": projected,
            "projected_rankable": projected >= 2,
            "candidates": rows,
        })

    rankable_recorded = sum(
        1 for case in per_case
        if case["eligible_as_recorded"] >= 2)
    rankable_projected = sum(1 for case in per_case if case["projected_rankable"])
    return {
        "cases": per_case,
        "totals": totals,
        "case_summary": {
            "cases": len(per_case),
            "rankable_as_recorded": rankable_recorded,
            "rankable_projected": rankable_projected,
            "ranking_case_fraction_as_recorded":
                round(rankable_recorded / len(per_case), 4),
            "ranking_case_fraction_projected":
                round(rankable_projected / len(per_case), 4),
        },
    }


def build_report(outcomes_path: Path) -> dict:
    outcomes = json.loads(outcomes_path.read_text())
    body = rescore(outcomes)
    return {
        "schema_version": 1,
        "kind": "closure_disqualification_rescore",
        "stage": "closure_integrity_plan_unplanned_finding",
        "measured_at": time.strftime("%Y-%m-%d"),
        "source": {
            "outcomes_path": str(outcomes_path),
            "outcomes_sha256": hashlib.sha256(
                outcomes_path.read_bytes()).hexdigest(),
            "campaign_version": outcomes.get("campaign_version"),
            "manifest_content_key": outcomes.get("manifest_content_key"),
        },
        "rules_applied": {
            "access_reasons_reclassified": sorted(ACCESS_REASONS),
            "access_basis": ("C1, 2026-08-06 — metrics.access_impact_reasons; "
                             "access a closure removes is its impact, not a "
                             "broken run"),
            "teleport_reason_removed_by_construction": TELEPORT_REASON,
            "teleport_basis": ("Stage 3 — closure runs set "
                               "--time-to-teleport -1, so SUMO cannot relocate "
                               "a stuck vehicle at all"),
            "throughput_reason_expected_to_follow": THROUGHPUT_REASON,
            "throughput_basis": ("Stage 1 measured a teleport as the NECESSARY "
                                 "condition for closed-edge throughput: 0 of "
                                 "35 throughput schedules lacked one "
                                 "(validation/closure_leak_mechanism_v1.json). "
                                 "Necessary, not proven-by-construction — "
                                 "which is why it is a separate bucket"),
        },
        "interpretation_limits": [
            "This is a RE-SCORE of stored reasons, not a re-run. Disabling "
            "teleporting changes the simulation itself, so a re-run would "
            "produce different queues, arrivals and metrics.",
            "projected_eligible answers only 'is any recorded reason still a "
            "failure under today's rules'. It is not a claim that the "
            "schedule would be eligible in a fresh campaign.",
            "No campaign gate verdict is derived here. A gate needs a campaign "
            "run against its own frozen manifest.",
        ],
        **body,
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    outcomes_path = Path(args.outcomes)
    if not outcomes_path.is_file():
        raise SystemExit(f"no such outcomes file: {outcomes_path}")
    report = build_report(outcomes_path)

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.stdout:
        print(text)
    else:
        out_path = Path(args.out)
        if out_path.exists():
            raise SystemExit(
                f"refusing to overwrite {out_path}; remove it deliberately")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
        print(f"wrote {out_path}")

    totals = report["totals"]
    summary = report["case_summary"]
    print(f"schedules: {totals['schedules']} "
          f"({totals['disqualified_as_recorded']} disqualified as recorded)")
    print(f"  access only (C1 already fixed)       : {totals['access_only']}")
    print(f"  teleport/throughput only (Stage 3)   : "
          f"{totals['teleport_or_throughput_only']}")
    print(f"  both access and teleport             : "
          f"{totals['access_and_teleport_only']}")
    print(f"  still disqualified under today's rules: "
          f"{totals['still_disqualified']}")
    print(f"rankable cases: {summary['rankable_as_recorded']} recorded -> "
          f"{summary['rankable_projected']} projected "
          f"(fraction {summary['ranking_case_fraction_as_recorded']} -> "
          f"{summary['ranking_case_fraction_projected']}, projection only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
