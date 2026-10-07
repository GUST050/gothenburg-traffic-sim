"""The sensor refresh must prepare inputs before materializing route pools."""

from pathlib import Path
import subprocess


def test_refresh_builds_catalog_after_network_before_demand_even_with_parallel_make():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["make", "-n", "-j4", "refresh", "PYTHON=chosen-python"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    steps = result.stdout
    assert steps.index("chosen-python build_data.py") < steps.index(
        "chosen-python build_sumo_net.py")
    assert steps.index("chosen-python build_sumo_net.py") < steps.index(
        "chosen-python -m dirsplit.predict")
    assert steps.index("chosen-python -m dirsplit.predict") < steps.index(
        "chosen-python tools/build_route_catalog.py --execute")
    assert steps.index("chosen-python tools/build_route_catalog.py --execute") < steps.index(
        "chosen-python build_sumo_demand.py")
