"""Isolated diagnostics and prototypes that are not part of the production
pipeline.

Nothing under this package is imported by build_data.py, build_sumo_demand.py,
pfe.py, run_scenario.py or serve.py. A module here may read published
artifacts and existing SUMO evidence, but it never regenerates candidates,
re-solves the PFE, writes to sumo/ or web/data/, or changes any production
contract. Promoting a finding out of this package into production code is a
separate, deliberate decision — never a side effect of adding a diagnostic.
"""
