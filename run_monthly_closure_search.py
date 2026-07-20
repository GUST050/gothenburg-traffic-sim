#!/usr/bin/env python3
"""Run or resume a robust recurring closure search with archived SUMO demand."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from screen_monthly_closures import build_screening_artifact
from traffic_sim.simulation.monthly_proxy import (
    HELD_OUT_VALIDATED_SHORTLIST_POLICY,
)
from traffic_sim.core.closure_calendar import generate_closure_schedules
from traffic_sim.core.contracts import (
    load_closure_search_spec,
)
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.simulation.monthly_search import (
    MonthlySearchPolicy,
    run_monthly_search,
)
from traffic_sim.simulation.monthly_demand import MonthlyDemandResolverRunner
from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner
from traffic_sim.simulation.search_workspace import DEFAULT_ROOT


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _bounded_exhaustive_builder(
    spec_path: Path,
    *,
    maximum_candidates: int,
) -> dict[str, Any]:
    spec = load_closure_search_spec(spec_path)
    schedules = generate_closure_schedules(spec)
    if len(schedules) > maximum_candidates:
        raise ValueError(
            f"bounded exhaustive screening generated {len(schedules)} "
            f"candidates, above the explicit cap {maximum_candidates}"
        )
    return {
        "schema_version": 1,
        "kind": "monthly_closure_proxy_screening",
        "proxy_version": "bounded_exhaustive_sumo_v1",
        "search": spec.to_dict(),
        "claim_boundary": {
            "evidence_level": "no_proxy_bounded_exhaustive",
            "global_best_claim_allowed": False,
            "ui_exposure_allowed": False,
            "reason": "golden/diagnostic bounded exhaustive SUMO screening",
        },
        "candidate_count": len(schedules),
        "scoreable_candidate_count": 0,
        "unavailable_candidates": [],
        "ranked_candidates": [],
        "shortlist": {
            "version": "bounded_exhaustive_sumo_v1",
            "selection_complete": True,
            "entries": [
                {
                    "schedule_id": schedule.schedule_id,
                    "selection_reasons": ["bounded_exhaustive"],
                    "proxy_rank": None,
                }
                for schedule in schedules
            ],
        },
        "input_fingerprints": {
            "closure_search_spec": {
                "path": str(Path(spec_path).resolve()),
                "sha256": sha256_file(Path(spec_path)),
            }
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--demand-archive",
        type=Path,
        help=(
            "Compatibility mode: one succeeded immutable demand archive "
            "covering every shortlist envelope. Omit to resolve/build and "
            "freeze one exact archive per shortlist envelope automatically."
        ),
    )
    parser.add_argument(
        "--demand-runs-root",
        type=Path,
        default=Path("runs"),
        help="Run registry searched for exact succeeded demand archives.",
    )
    parser.add_argument(
        "--demand-release-root",
        type=Path,
        default=Path("runs") / "monthly-demand-releases",
        help="Immutable envelope-to-archive release manifests.",
    )
    parser.add_argument(
        "--no-build-missing-demand",
        action="store_true",
        help="Fail instead of automatically calibrating missing envelopes.",
    )
    parser.add_argument(
        "--baseline-trip-duration-p99-s",
        type=int,
        required=True,
        help="Frozen baseline trip-duration p99 used to derive warm-up.",
    )
    parser.add_argument(
        "--screening-mode",
        choices=("proxy", "bounded-exhaustive"),
        default="proxy",
    )
    parser.add_argument(
        "--bounded-exhaustive-cap",
        type=int,
        default=12,
        help="Hard candidate cap when --screening-mode=bounded-exhaustive.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline-cache", type=Path)
    parser.add_argument("--seed-workers", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.baseline_trip_duration_p99_s <= 0:
        raise SystemExit("--baseline-trip-duration-p99-s must be positive")
    if args.bounded_exhaustive_cap <= 0:
        raise SystemExit("--bounded-exhaustive-cap must be positive")
    if args.seed_workers != 1:
        raise SystemExit(
            "monthly search currently requires one SUMO worker until the "
            "golden resource benchmark approves parallel execution"
        )
    try:
        spec = load_closure_search_spec(args.spec)
        policy = MonthlySearchPolicy.from_dict(_read(args.policy))
        study_key = _digest({
            "kind": "monthly_closure_search_study",
            "search_content_key": spec.content_key,
            "policy_content_key": policy.content_key,
            "demand_release_id": spec.demand_build_id,
            "network_sha256": sha256_file(Path("sumo/net.net.xml")),
        })
        runner_options = {
            "baseline_trip_duration_p99_s": (
                args.baseline_trip_duration_p99_s
            ),
            "study_provenance_key": study_key,
            "seed_workers": args.seed_workers,
        }
        if args.baseline_cache is not None:
            runner_options["cache_root"] = args.baseline_cache
        if args.demand_archive is not None:
            runner = ArchivedDemandSumoRunner(
                spec,
                archive=args.demand_archive,
                **runner_options,
            )
        else:
            runner = MonthlyDemandResolverRunner(
                spec,
                runs_root=args.demand_runs_root,
                release_root=args.demand_release_root,
                build_missing=not args.no_build_missing_demand,
                **runner_options,
            )
        if args.screening_mode == "proxy":
            # Screen with EXACTLY the policy the held-out v2 gate validated,
            # so the shortlist the operator sees is the one whose recall/
            # regret were measured. road_domain_status matches the
            # validation runner (in_domain); per-worksite coverage scoring
            # is a future refinement the gate did not cover.
            screen_builder = lambda path: build_screening_artifact(
                path,
                road_domain_status="in_domain",
                policy=HELD_OUT_VALIDATED_SHORTLIST_POLICY,
            )
        else:
            screen_builder = lambda path: _bounded_exhaustive_builder(
                path,
                maximum_candidates=args.bounded_exhaustive_cap,
            )
        result = run_monthly_search(
            spec,
            policy,
            runner=runner,
            screen_builder=screen_builder,
            root=args.root,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc

    boundary = result.get("claim_boundary", {})
    print(
        f"Monthly closure search {spec.search_id}: {result['status']}; "
        f"winner={result.get('winner_id')}; "
        f"scope={boundary.get('best_result_scope')}; "
        f"global_best_claim_allowed={boundary.get('global_best_claim_allowed')}; "
        f"ui_exposure_allowed={boundary.get('ui_exposure_allowed')}"
    )


if __name__ == "__main__":
    main()
