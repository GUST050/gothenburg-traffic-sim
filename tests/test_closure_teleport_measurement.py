"""Identity contract for the real Stage 3 paired measurement."""

import inspect

from tools import measure_closure_teleport_policy as measure


def test_content_key_covers_the_measurement_body():
    payload = {"schema_version": 1, "gate": {"passed": True}}
    payload["content_key"] = measure._content_key(payload)
    assert measure._content_key(payload) == payload["content_key"]
    payload["gate"]["passed"] = False
    assert measure._content_key(payload) != payload["content_key"]


def test_identity_binds_every_executed_source():
    assert set(measure.SOURCE_PATHS) == {
        "run_scenario.py",
        "suggest_closure_time.py",
        "tools/measure_closure_teleport_policy.py",
        "traffic_sim/simulation/closure_teleport.py",
        "traffic_sim/simulation/metrics.py",
    }


def test_each_arm_records_seed_and_variant_pairing():
    source = inspect.getsource(measure.run_arm)
    assert "replication_records=replications" in source
    assert '"replications": replications' in source


def test_inputs_are_checked_again_before_publication():
    source = inspect.getsource(measure.main)
    assert "identity_at_start = evidence_identity" in source
    assert "identity_at_end = evidence_identity" in source
    assert "identity_at_end != identity_at_start" in source
