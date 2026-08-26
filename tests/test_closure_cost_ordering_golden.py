"""Static contract for the current real-golden PR D/E verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# v1-v6 remain immutable historical evidence. v7 binds the additive exact
# baseline sensor-passage audit without changing the closure-cost result.
RECORD = ROOT / "validation" / "closure_cost_ordering_golden_v7.json"


def _read():
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_the_real_golden_provider_and_runner_are_field_identical():
    record = _read()
    assert record["schema"] == "closure_cost_ordering_golden_v1"
    gate = record["gates"]["pr_d_real_golden_provider_runner"]
    assert gate == {
        "field_identical": True,
        "recorded_cost_contract_identical": True,
        "sorting_order_identical": True,
        "status": "passed",
    }
    assert len(record["cases"]) == 3
    assert all(item["provider_runner_field_identical"]
               for item in record["cases"])


def test_the_named_real_benchmark_matches_exhaustive_without_overclaiming():
    record = _read()
    gate = record["gates"]["pr_e_named_real_benchmark"]
    assert gate["status"] == "passed"
    assert gate["pilot_status_identical"] is True
    assert gate["selected_ids_identical"] is True
    assert gate["sumo_verifications_saved"] == 0
    assert record["cost_ordered_comparison"]["equivalent"] is True
    assert record["gates"]["policy_v3_activation"]["status"] == "closed"
    assert record["release_evidence"] is False
    assert record["heldout_evidence"] is False
    assert record["claim_boundary"] == {
        "activates_policy_v3": False,
        "opens_global_best": False,
        "permits_ui_claim": False,
        "reason": (
            "real golden development replay; no new SUMO execution and no "
            "held-out observation"),
    }


def test_every_interpreting_source_digest_matches_this_tree():
    for name, source in _read()["sources"].items():
        path = Path(source["path"])
        if not path.is_absolute():
            path = ROOT / path
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source[
            "sha256"], name


def test_the_record_can_be_reproduced_but_not_silently_overwritten():
    source = (ROOT / "tools" / "verify_closure_cost_ordering_golden.py"
              ).read_text(encoding="utf-8")
    assert "--verify" in source
    assert "--overwrite" in source
    assert '"release_evidence": False' in source
    assert "No simulator is started" in source
