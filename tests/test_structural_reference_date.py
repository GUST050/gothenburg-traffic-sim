"""One canonical structural date across contracts and entry points."""

import build_sumo_demand
import screen_monthly_closures
import serve
from traffic_sim.core import contracts


def test_every_entry_point_uses_the_contract_reference_date():
    reference_date = contracts.STRUCTURAL_REFERENCE_DATE
    assert build_sumo_demand.STRUCTURAL_REFERENCE_DATE == reference_date
    assert serve.STRUCTURAL_REFERENCE_DATE == reference_date
    assert screen_monthly_closures.STRUCTURAL_REFERENCE_DATE == (
        f"{reference_date}T00:00:00")
