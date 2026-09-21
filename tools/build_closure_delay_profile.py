#!/usr/bin/env python3
"""Publish the added-travel-time distribution for a finished closure search.

The monthly search ranks candidates on added vehicle-hours, which says how
much delay a closure creates but not how it is shared out. This replays the
best candidates' deterministic detour cost over the same demand archives and
writes the per-vehicle distribution to `delay-profile.json` in the workspace,
where `serve.py` picks it up and the Simulering panel draws it.

    python3 tools/build_closure_delay_profile.py            # newest search
    python3 tools/build_closure_delay_profile.py --search-id abc123 --top 3

Nothing is simulated and nothing in the workspace's evidence is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.analysis.closure_delay_run import (  # noqa: E402
    DEFAULT_TOP_N,
    build_delay_profile,
    write_delay_profile,
)
from traffic_sim.analysis.delay_profile import (  # noqa: E402
    DelayProfileError,
)
from traffic_sim.simulation.search_workspace import DEFAULT_ROOT  # noqa: E402


def newest_succeeded_workspace(root: Path) -> Path | None:
    """The most recently finished search, by the same rule serve.py uses."""
    candidates: list[tuple[str, Path]] = []
    for directory in sorted(Path(root).glob("*")):
        if not directory.is_dir():
            continue
        try:
            manifest = json.loads(
                (directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("status") != "succeeded":
            continue
        candidates.append((str(manifest.get("finished_at") or ""), directory))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    return candidates[0][1]


def print_summary(payload: dict) -> None:
    labels = [str(item["label"]) for item in payload["bins"]]
    for candidate in payload["candidates"]:
        span = (f"{candidate['first_work_date']}"
                if candidate["day_count"] == 1
                else f"{candidate['first_work_date']} … "
                     f"{candidate.get('period_end')}")
        print(f"\n#{candidate['rank']}  {span}  "
              f"{candidate['daily_start']}–{candidate['daily_end']}  "
              f"({candidate['day_count']} arbetsdag(ar))")
        cost = candidate["closure_cost"]
        print(f"    rankad kostnad: {cost['added_vehicle_hours']} fordonstimmar, "
              f"{cost['vehicles_affected']} berörda fordon "
              f"(verifierad: {candidate['cost_verification']['matches']})")
        q50 = candidate["variants"]["q50"]
        for label, count in zip(labels, q50["vehicles"]):
            if count:
                print(f"      {label:>12}  {count:>7}")
        if q50["vehicles_no_detour"]:
            print(f"      {'ingen väg':>12}  {q50['vehicles_no_detour']:>7}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-id", help="Workspace directory name.")
    parser.add_argument("--workspace", type=Path,
                        help="Explicit workspace path; overrides --search-id.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help=f"Search workspace root (default {DEFAULT_ROOT}).")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help="How many of the best candidates to profile.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print the payload instead of writing it.")
    args = parser.parse_args(argv)

    if args.workspace is not None:
        workspace = args.workspace
    elif args.search_id:
        workspace = Path(args.root) / args.search_id
    else:
        found = newest_succeeded_workspace(Path(args.root))
        if found is None:
            parser.error(
                f"no succeeded search workspace under {args.root} — run a "
                f"monthly closure search first, or pass --workspace")
        workspace = found

    def progress(step: int, total: int, label: str) -> None:
        print(f"  [{step}/{total}] {label}", flush=True)

    print(f"Arbetsyta: {workspace}")
    try:
        payload = build_delay_profile(
            workspace, top_n=args.top, progress=progress)
    except DelayProfileError as error:
        print(f"delay profile unavailable: {error}", file=sys.stderr)
        return 2

    if args.stdout:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        destination = write_delay_profile(workspace, payload)
        print(f"\nSkrev {destination}")
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
