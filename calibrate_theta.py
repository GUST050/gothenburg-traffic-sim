"""
θ calibration for the OD generator — a bounded grid search, not full SPSA.

Run once (result is frozen into build_sumo_demand.py's defaults):
  python3 calibrate_theta.py

HONEST SCOPE: full simulation-based calibration (SPSA/metamodel methods —
see ARCHITECTURE.md references) optimises many parameters against a
simulation-in-the-loop objective, at the cost of hundreds of expensive
evaluations. That is not proportionate here: we have exactly two free
parameters (through-fraction, gravity-km) after the population/POI/RVU
grounding fixed everything else. A 3×3 grid (9 evaluations) is the
proportionate, transparent version of the same idea, and is documented as
such rather than dressed up as more than it is.

Each evaluation: generate candidates(θ) → PFE calibrate on a fast morning
window (06-10, not the full day — this is the cheap inner-loop signal) →
score = GEH<5 percentage, tie-broken by fewer infeasible intervals. The
WINNING θ is then validated properly with the full whole-day LOSO
(validate_sim.py) — that expensive step runs ONCE, not 9 times.

Writes sumo/theta_search.json (all 9 results, for the record).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

THROUGH_FRACTIONS = (0.3, 0.5, 0.7)
GRAVITY_KMS       = (1.0, 1.8, 2.6)


def run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return res.stdout + res.stderr


def main() -> None:
    results = []
    for tf in THROUGH_FRACTIONS:
        for gk in GRAVITY_KMS:
            print(f"\n=== θ: through_fraction={tf} gravity_km={gk} ===")
            out = run([sys.executable, "build_candidates.py",
                      "--through-fraction", str(tf), "--gravity-km", str(gk),
                      "--n-total", "6000", "--n-sensor-via", "400",
                      "--out-suffix", "_theta"])
            print(out[-400:])

            Path("sumo/candidates.rou.xml").write_bytes(
                Path("sumo/candidates_theta.rou.xml").read_bytes())

            out = run([sys.executable, "build_sumo_demand.py",
                      "--begin", "06:00", "--end", "10:00",
                      "--engine", "pfe"])
            geh_lines = [l for l in out.splitlines() if "PFE edge_shares " in l]
            print(geh_lines[0] if geh_lines else out[-600:])

            geh_pct, infeasible = None, None
            for l in out.splitlines():
                if "PFE edge_shares " in l and "GEH<5" in l:
                    geh_pct = float(l.split("GEH<5:")[1].split("%")[0].strip())
                    infeasible = int(l.split("infeasible intervals:")[1]
                                     .split(")")[0].strip())
                    break
            results.append({"through_fraction": tf, "gravity_km": gk,
                            "geh_pct": geh_pct, "infeasible": infeasible})

    results.sort(key=lambda r: (-(r["geh_pct"] or 0), r["infeasible"] or 99))
    with open("sumo/theta_search.json", "w") as f:
        json.dump(results, f, indent=1)

    print("\n=== Ranking ===")
    for r in results:
        print(f"  tf={r['through_fraction']}  gk={r['gravity_km']}  "
              f"GEH<5={r['geh_pct']}%  infeasible={r['infeasible']}")
    best = results[0]
    print(f"\nBest θ: through_fraction={best['through_fraction']} "
          f"gravity_km={best['gravity_km']}")


if __name__ == "__main__":
    main()
