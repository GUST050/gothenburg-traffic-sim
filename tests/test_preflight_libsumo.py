"""The libsumo preflight must diagnose, not merely fail to import.

Three different environment faults all raise ModuleNotFoundError, and only one
of them is "libsumo is unavailable". Conflating them is how a blocker gets
recorded with the wrong fix next to it — which is worse than no record, because
someone will follow it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.preflight_libsumo as preflight

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "validation" / "libsumo_preflight_v1.json"


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


class TestItChangesNothing:
    def test_the_record_says_so_and_means_it(self, record):
        assert record["read_only"] is True
        assert record["installed_anything"] is False

    def test_probing_an_extra_path_does_not_mutate_this_interpreter(self):
        import sys

        before = list(sys.path)
        preflight._importable("sumolib", extra_path=str(ROOT))
        assert sys.path == before

    def test_it_refuses_to_overwrite_without_being_asked(self, tmp_path):
        destination = tmp_path / "preflight.json"
        preflight.main(["--out", str(destination)])
        with pytest.raises(SystemExit, match="--overwrite"):
            preflight.main(["--out", str(destination)])


class TestItSeparatesTheThreeFaults:
    def test_an_unset_sumo_home_is_not_reported_as_a_missing_module(
            self, record):
        """`sumolib` imports only with SUMO's tools on the path. That is a
        path fact, not an absence — and the record must show both probes."""
        sumolib = record["modules"]["sumolib"]
        if not record["sumo_home"]["tools_dir_exists"]:
            pytest.skip("no SUMO tools directory in this environment")
        assert sumolib["with_sumo_tools_on_path"] is not None
        if sumolib["with_sumo_tools_on_path"]["importable"]:
            assert sumolib["with_sumo_tools_on_path"]["origin"]

    def test_the_cpp_library_is_never_mistaken_for_the_python_binding(
            self, record):
        distribution = record["distribution"]
        if not distribution["libsumo_cpp_library"]:
            pytest.skip("this distribution ships no libsumo C++ library")
        assert distribution["libsumo_python_binding"] == [] or (
            record["verdict"] == "available")
        if not distribution["libsumo_python_binding"]:
            assert record["verdict"] == "blocked_missing_python_binding"
            assert "does NOT make libsumo importable" in record["blocker"]

    def test_a_verdict_always_carries_its_blocker(self, record):
        if record["verdict"] == "available":
            assert record["blocker"] is None
        else:
            assert record["blocker"]

    def test_the_sumo_binaries_are_reported_independently_of_the_binding(
            self, record):
        """SUMO can be fully working while libsumo is unavailable."""
        assert set(record["binaries"]) == set(preflight.BINARIES)
        for name, item in record["binaries"].items():
            if item["path"]:
                assert item["version"], name


class TestItLicensesNothing:
    def test_it_opens_no_gate_and_activates_no_policy(self, record):
        assert record["claim_boundary"]["opens_no_gate"] is True
        assert record["claim_boundary"]["activates_policy_v3"] is False
        assert record["release_evidence"] is False

    def test_a_missing_speed_path_never_justifies_more_workers(self, record):
        assert record["consequences"]["seed_worker_budget_unchanged"] is True
        assert record["consequences"]["pr_g_persistent_sumo"] in (
            "blocked", "attemptable")

    def test_it_recomputes_from_the_tool(self, record):
        rebuilt = preflight.build_record()
        assert rebuilt["content_key"] == record["content_key"], (
            "the preflight must be reproducible on the machine it describes")

    def test_exporting_the_sumo_home_it_already_resolves_changes_nothing(
            self, monkeypatch, record):
        """Found by a full-suite run: a neighbouring test exported SUMO_HOME.

        Pointing the variable at the root the project resolves anyway describes
        the SAME installation, so the record must not change. Echoing the
        variable inside the keyed payload made it change, which meant the same
        machine in the same state failed to reproduce its own preflight
        depending on which test ran first. It is reported under `environment`,
        which the key excludes.
        """
        resolved = record["sumo_home"]["resolved_by_project"]
        if not resolved:
            pytest.skip("no SUMO installation to point at")
        monkeypatch.setenv("SUMO_HOME", resolved)
        rebuilt = preflight.build_record()
        assert rebuilt["content_key"] == record["content_key"]
        assert rebuilt["environment"]["SUMO_HOME"] == resolved
