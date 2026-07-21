"""The demand day-library contract (SPEED_ARCHITECTURE_PLAN layers L1/L2).

The load-bearing claim is a single equality: a window assembled from
separately calibrated days is byte-identical to the same window calibrated
monolithically. Everything else in the library is bookkeeping around it.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from demand import day_library as dl
from traffic_sim.demand.pfe import Candidate, write_calibration_report


def _shapes():
    pools = {"home:E": [{"id": f"loc{i}", "kind": "building",
                         "p": 12.5 * i, "w": 1.0 + i} for i in range(6)]}
    out = []
    for tag, purpose in (("a", "arbete"), ("b", "fritid"), ("c", "through")):
        source = Candidate(0.0, ["O", "E", f"D{tag}"], source_id=f"src-{tag}",
                           intent={"purpose": purpose,
                                   "origin_location_pool": "home:E",
                                   "destination_location_pool": "home:E",
                                   "tour_id": f"t-{tag}", "leg": "outbound"},
                           location_pools=pools)
        out.append(Candidate(0.0, ["O", "E", f"D{tag}"], location_pools=pools,
                             source_candidates=[source]))
    return out


def _window(nq, *, day):
    """Per-quarter inputs that differ between the two days on purpose."""
    targets, solutions, mixes = [], [], []
    for q in range(nq):
        level = 4.0 + (q % 5) + (2.0 if day else 0.0)
        targets.append({"E": float(int(level))})
        share = np.array([0.5, 0.3, 0.2]) if not day else np.array([0.2, 0.5, 0.3])
        solutions.append(level * share)
        mixes.append(Counter({"arbete": 2, "fritid": 1, "through": 1}))
    return targets, solutions, mixes


def _two_day_inputs(nq_per_day):
    targets, solutions, mixes = [], [], []
    for day in (0, 1):
        day_targets, day_solutions, day_mixes = _window(nq_per_day, day=day)
        targets += day_targets
        solutions += day_solutions
        mixes += day_mixes
    return targets, solutions, mixes


def _write(out, targets, solutions, mixes, day_quarters):
    report = write_calibration_report(
        _shapes(), out, targets, solutions,
        purpose_mixes_per_q=mixes, day_quarters=day_quarters)
    agents = out.with_name(out.name.replace(".rou.xml", ".agents.json"))
    return report, out.read_bytes(), agents.read_bytes()


class TestAssemblyEqualsMonolith:
    """L2's whole justification, held as a permanent regression test."""

    NQ = 96

    def _days(self, tmp_path):
        targets, solutions, mixes = _two_day_inputs(self.NQ)
        days = []
        for day in (0, 1):
            directory = tmp_path / f"day{day}"
            directory.mkdir()
            span = slice(day * self.NQ, (day + 1) * self.NQ)
            _write(directory / "calibrated.rou.xml", targets[span],
                   solutions[span], mixes[span], self.NQ)
            days.append(directory)
        return days, (targets, solutions, mixes)

    def test_assembled_window_is_byte_identical_to_a_monolithic_build(
            self, tmp_path):
        days, (targets, solutions, mixes) = self._days(tmp_path)
        _report, mono_xml, mono_agents = _write(
            tmp_path / "monolith.rou.xml", targets, solutions, mixes, self.NQ)

        dl.assemble_window(days, tmp_path / "assembled.rou.xml",
                           tmp_path / "assembled.agents.json")

        assert (tmp_path / "assembled.rou.xml").read_bytes() == mono_xml
        assert (tmp_path / "assembled.agents.json").read_bytes() == mono_agents

    def test_assembly_reports_what_it_wrote(self, tmp_path):
        days, _inputs = self._days(tmp_path)
        summary = dl.assemble_window(days, tmp_path / "a.rou.xml",
                                     tmp_path / "a.agents.json")
        assert summary["days"] == 2
        assert summary["vehicles"] > 0

    def test_mismatched_route_and_agent_counts_are_refused(self, tmp_path):
        days, _inputs = self._days(tmp_path)
        agents_path = days[0] / "calibrated.agents.json"
        payload = json.loads(agents_path.read_text())
        payload["agents"] = payload["agents"][:-1]
        agents_path.write_text(json.dumps(payload))

        with pytest.raises(ValueError, match="agents"):
            dl.assemble_window(days, tmp_path / "b.rou.xml",
                               tmp_path / "b.agents.json")


class TestDayIdentity:
    def _identity(self, **overrides):
        base = {
            "date": "2027-03-09",
            "source": "forecast",
            "pool_composition": ("weekday",),
            "inputs": {"targets": "abc"},
            "source_hashes": {"pfe": "def"},
        }
        base.update(overrides)
        return dl.DayIdentity(**base)

    def test_key_is_stable_for_the_same_identity(self):
        assert self._identity().key == self._identity().key

    @pytest.mark.parametrize("overrides", [
        {"date": "2027-03-10"},
        {"source": "historical"},
        {"pool_composition": ("weekday", "weekend")},
        {"inputs": {"targets": "other"}},
        {"source_hashes": {"pfe": "changed"}},
    ])
    def test_every_component_changes_the_key(self, overrides):
        # Notably pool_composition: the PFE solves each quarter over the whole
        # shape pool, so the same date calibrated beside a weekend day is a
        # different (equally correct) result and must not reuse the entry.
        assert self._identity(**overrides).key != self._identity().key


class TestStoreFailsClosed:
    def _stored(self, tmp_path):
        library = dl.DayLibrary(tmp_path / "library")
        identity = dl.DayIdentity(
            date="2027-03-09", source="forecast",
            pool_composition=("weekday",), inputs={"a": 1},
            source_hashes={"pfe": "x"})
        artifact = tmp_path / "calibrated.rou.xml"
        artifact.write_text("<routes>\n</routes>\n")
        library.put(identity, {"calibrated.rou.xml": artifact},
                    fit={"geh_pct": 100.0})
        return library, identity

    def test_round_trip_returns_the_manifest(self, tmp_path):
        library, identity = self._stored(tmp_path)
        manifest = library.get(identity)
        assert manifest is not None
        assert manifest["fit"]["geh_pct"] == 100.0
        assert manifest["identity"] == identity.to_dict()

    def test_absent_entry_is_none(self, tmp_path):
        library, identity = self._stored(tmp_path)
        other = dl.DayIdentity(
            date="2027-03-10", source="forecast",
            pool_composition=("weekday",), inputs={"a": 1},
            source_hashes={"pfe": "x"})
        assert library.get(other) is None

    def test_altered_artifact_is_treated_as_absent(self, tmp_path):
        library, identity = self._stored(tmp_path)
        (library.path_for(identity) / "calibrated.rou.xml").write_text("edited")
        assert library.get(identity) is None

    def test_deleted_artifact_is_treated_as_absent(self, tmp_path):
        library, identity = self._stored(tmp_path)
        (library.path_for(identity) / "calibrated.rou.xml").unlink()
        assert library.get(identity) is None

    def test_manifest_for_a_different_identity_is_rejected(self, tmp_path):
        library, identity = self._stored(tmp_path)
        manifest_path = library.manifest_path(identity)
        manifest = json.loads(manifest_path.read_text())
        manifest["identity"]["date"] = "2027-03-10"
        manifest_path.write_text(json.dumps(manifest))
        assert library.get(identity) is None

    def test_put_replaces_an_existing_entry_atomically(self, tmp_path):
        library, identity = self._stored(tmp_path)
        replacement = tmp_path / "replacement.rou.xml"
        replacement.write_text("<routes>\n  <!-- v2 -->\n</routes>\n")
        library.put(identity, {"calibrated.rou.xml": replacement})
        manifest = library.get(identity)
        assert manifest is not None
        assert not library.path_for(identity).with_name(
            library.path_for(identity).name + ".staging").exists()
        assert (library.path_for(identity)
                / "calibrated.rou.xml").read_text().count("v2") == 1

    def test_artifact_names_cannot_escape_the_entry(self, tmp_path):
        library, identity = self._stored(tmp_path)
        artifact = tmp_path / "calibrated.rou.xml"
        with pytest.raises(ValueError, match="invalid artifact name"):
            library.put(identity, {"../escape.xml": artifact})
