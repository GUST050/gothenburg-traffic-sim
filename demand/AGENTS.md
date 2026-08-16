# Demand orchestration guidance

- This package owns orchestration and data contracts; numerical PFE code lives
  under `traffic_sim/demand/`.
- Keep intake, calibration, publication and feedback boundaries explicit.
- Preserve exact measured totals, missing-value semantics and day/window
  identity through every transformation.
- New sensor behavior must be registry- or policy-driven and reusable.
- Run `make test-demand` after changes.
