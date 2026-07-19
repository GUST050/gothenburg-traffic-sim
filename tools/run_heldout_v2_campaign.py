#!/usr/bin/env python3
"""Held-out v2 campaign driver: build missing demand, then run all cases.

Stage 1 builds every demand envelope the frozen v2 manifest needs that has
no succeeded immutable archive yet, with the live release product set
snapshotted before the first build and restored byte-for-byte afterwards
(the 2026-07-19 lesson: envelope builds write THROUGH the live paths).

Stage 2 runs the resumable exhaustive SUMO campaign
(run_monthly_proxy_validation.py) against the frozen v2 manifest.  Both
stages are safe to re-run: archives are found instead of rebuilt, and
completed cases/schedules are loaded instead of resimulated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from traffic_sim.core.contracts import DemandBuildSpec  # noqa: E402
from traffic_sim.simulation.monthly_demand import (  # noqa: E402
    build_demand_archive,
    find_demand_archives,
    restore_live_demand_release,
    snapshot_live_demand_release,
)
from traffic_sim.simulation.proxy_validation import (  # noqa: E402
    validate_validation_manifest,
)

MANIFEST = Path("validation/monthly_proxy_manifest_v2.json")
RUNS_ROOT = Path("runs")

# The three envelopes the frozen manifest needs beyond the existing
# archives.  Specs must reproduce the manifest's demand_build_id exactly —
# verified below before any build starts.
NEEDED_SPECS = [
    DemandBuildSpec(start_date="2027-12-24", source="forecast", days=1,
                    purpose="standard"),
    DemandBuildSpec(start_date="2027-07-15", source="forecast", days=3,
                    purpose="closure_envelope"),
    DemandBuildSpec(start_date="2027-07-15", source="forecast", days=5,
                    purpose="closure_envelope"),
]


def main() -> None:
    manifest = validate_validation_manifest(json.loads(MANIFEST.read_text()))
    needed_keys = {
        case["closure_search_spec"]["demand_build_id"]
        for case in manifest["cases"]
    }
    spec_by_key = {spec.build_key: spec for spec in NEEDED_SPECS}
    unknown = set(spec_by_key) - needed_keys
    if unknown:
        raise SystemExit(f"campaign specs not in the manifest: {unknown}")

    missing = [
        spec for key, spec in spec_by_key.items()
        if not find_demand_archives(RUNS_ROOT, spec)
    ]
    if missing:
        print(f"stage 1: building {len(missing)} missing demand envelope(s)",
              flush=True)
        snapshot = snapshot_live_demand_release()
        try:
            for spec in missing:
                print(f"  building {spec.build_key} ({spec.start_date}, "
                      f"{spec.days} day(s))", flush=True)
                build_demand_archive(spec)
                if not find_demand_archives(RUNS_ROOT, spec):
                    raise SystemExit(
                        f"build finished but no valid archive found for "
                        f"{spec.build_key}"
                    )
        finally:
            restore_live_demand_release(snapshot)
            print("stage 1: live release restored", flush=True)
    else:
        print("stage 1: all demand envelopes already archived", flush=True)

    print("stage 2: exhaustive SUMO campaign (resumable)", flush=True)
    completed = subprocess.run(
        [sys.executable, "run_monthly_proxy_validation.py",
         "--manifest", str(MANIFEST)],
        check=False,
    )
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
