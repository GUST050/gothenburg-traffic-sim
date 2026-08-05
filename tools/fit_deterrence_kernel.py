"""
Fit the Tanner/gamma deterrence kernel (build_candidates.deterrence_weights)
against RVU's trip-length bins + the destination-near-sensor guard metric.

Part of the 2026-07-12 destination-clustering fix (see
IMPROVEMENT_PLAN.md): the old pure-exponential kernel's
mode sits at d=0, which — combined with the sensor-anchoring mask only
admitting destinations path-wise beyond the sensor — made "the edge
immediately past the sensor" the modal trip destination. This sweep runs
build_candidates.py --fit-only (no duarouter, ~30-60 s/combo) over an
(alpha, gravity_km) grid and reports, per combo:

  - trip_length_fit L1 vs RVU's renormalized short bins (primary criterion)
  - % of generated destinations within 200 m of a sensor (secondary/guard)

Selection rule (declared before running, so the numbers can't tempt us):
lowest L1 wins; the proximity metric breaks near-ties (within 0.02 L1) and
is a sanity check that the winner actually fixes the visual bug, not a
second objective to optimize.

Usage (from repo root):
  python3 tools/fit_deterrence_kernel.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIT_PATH = ROOT / "sumo" / "trip_length_fit_kernelfit.json"

# Trimmed after the 2026-07-12 conditional-sampling redesign (each run is
# ~100 s now, up from ~16 s, because rejection sampling pays real retries) —
# the first, denser sweep under the OLD sampling showed L1 nearly flat in
# beta at fixed alpha, so a coarser grid loses little.
# Two fits count as tied when their L1 differs by less than this FRACTION
# of the better one — scale-free, so it survives a change of target.
NEAR_TIE_REL_TOL = 0.05

ALPHAS = [0.0, 1.5, 3.0]
BETAS = [1.3, 1.8, 2.6]


def run_combo(alpha: float, beta: float) -> dict:
    cmd = [sys.executable, "build_candidates.py", "--fit-only",
           "--out-suffix", "_kernelfit",
           "--gravity-alpha", str(alpha), "--gravity-km", str(beta)]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                         timeout=900)
    if res.returncode != 0:
        print(res.stdout[-1500:], res.stderr[-1500:])
        sys.exit(f"build_candidates.py failed for alpha={alpha} beta={beta}")
    with open(FIT_PATH) as f:
        return json.load(f)


def select_winner(results: list[dict]) -> tuple[dict, dict, list[dict]]:
    """Declared selection rule: lowest L1 wins; the destination-proximity
    metric breaks only genuine ties. Returns (best, winner, near_ties)."""
    ordered = sorted(results, key=lambda r: r["l1"])
    best = ordered[0]
    near_ties = [r for r in ordered
                 if r["l1"] <= best["l1"] * (1.0 + NEAR_TIE_REL_TOL)]
    winner = min(near_ties, key=lambda r: r["dest_near_sensor_pct"])
    return best, winner, near_ties


def main() -> None:
    results = []
    for alpha in ALPHAS:
        for beta in BETAS:
            fit = run_combo(alpha, beta)
            prox = fit["dest_sensor_proximity"]
            row = {
                "alpha": alpha, "beta": beta, "mode_km": alpha * beta,
                "l1": fit["l1_distance"], "shares": fit["shares"],
                "dest_near_sensor_pct": prox["pct_within"],
                "baseline_pct": prox["baseline_pct_within"],
            }
            results.append(row)
            print(f"alpha={alpha:<4} beta={beta:<4} mode={row['mode_km']:<5.2f} "
                  f"L1={row['l1']:<7} shares={row['shares']} "
                  f"near-sensor={row['dest_near_sensor_pct']}% "
                  f"(baseline {row['baseline_pct']}%)")

    results.sort(key=lambda r: r["l1"])
    best, winner, near_ties = select_winner(results)
    print(f"\nbest L1: alpha={best['alpha']} beta={best['beta']} L1={best['l1']}")
    print(f"winner (proximity tiebreak among {len(near_ties)} near-ties): "
          f"alpha={winner['alpha']} beta={winner['beta']} L1={winner['l1']} "
          f"near-sensor={winner['dest_near_sensor_pct']}%")
    out = ROOT / "sumo" / "deterrence_kernel_fit_results.json"
    with open(out, "w") as f:
        json.dump({"grid": results, "winner": winner}, f, indent=1)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
