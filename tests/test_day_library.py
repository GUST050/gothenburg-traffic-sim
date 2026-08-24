"""The demand day-library contract (SPEED_ARCHITECTURE_PLAN layers L1/L2).

The load-bearing claim is a single equality: a window assembled from
separately calibrated days is byte-identical to the same window calibrated
monolithically. Everything else in the library is bookkeeping around it.
"""
import json
import os
import time
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

    @staticmethod
    def _stored_artifact(library, identity):
        # Address the artifact by what the manifest actually recorded, so
        # these tests hold whichever encoding the store chooses.
        manifest = library.get(identity)
        assert manifest is not None
        return library.path_for(identity) / next(iter(manifest["artifacts"]))

    def test_altered_artifact_is_treated_as_absent(self, tmp_path):
        library, identity = self._stored(tmp_path)
        self._stored_artifact(library, identity).write_text("edited")
        assert library.get(identity) is None

    def test_deleted_artifact_is_treated_as_absent(self, tmp_path):
        library, identity = self._stored(tmp_path)
        self._stored_artifact(library, identity).unlink()
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
        import gzip
        with gzip.open(library.path_for(identity)
                       / "calibrated.rou.xml.gz", "rt") as handle:
            assert handle.read().count("v2") == 1

    def test_artifact_names_cannot_escape_the_entry(self, tmp_path):
        library, identity = self._stored(tmp_path)
        artifact = tmp_path / "calibrated.rou.xml"
        with pytest.raises(ValueError, match="invalid artifact name"):
            library.put(identity, {"../escape.xml": artifact})


class TestMergeDayReports:
    """Per-day fit reports must combine into exactly the window report the
    monolithic writer would have returned: every field is a per-quarter
    record or a sum over quarters."""

    def _day(self, *, quarters=4, vehicles=10, geh_ok=3, geh_total=4,
             achieved=None, **extra):
        report = {
            "vehicles": vehicles,
            "infeasible_intervals": 0,
            "geh_ok": geh_ok, "geh_total": geh_total,
            "integer_sensor_constraints": quarters,
            "integer_sensor_exact": quarters,
            "integer_sensor_exact_pct": 100.0,
            "integer_sensor_max_abs_error": 0.0,
            "integer_sensor_sum_abs_error": 0.0,
            "integer_sensor_target_rule": "int(round(target))",
            "achieved": achieved or {"E": [1.0] * quarters},
            "unserviceable_edges": [],
            "bound_violations": [],
            "relaxed_bound_violations": [],
            "purpose_allocation": [{"quarter": q, "target": {}}
                                   for q in range(quarters)],
            "purpose_allocation_summary": {
                "quarters_with_incompatible_routes": 1,
                "incompatible_routes_by_purpose": {"arbete": 2},
                "quarters_with_relaxed_mix": 0,
                "mix_shortfall_by_purpose": {},
                "mix_excess_by_purpose": {},
                "mix_reallocation_vehicles": 3,
                "replaced_routes": 1,
                "protected_signature_coverage": 0.75,
            },
            "relaxation_summary": {"clean": quarters},
        }
        report.update(extra)
        return report

    def test_counts_add_and_quarters_shift(self):
        merged = dl.merge_day_reports(
            [self._day(), self._day(vehicles=6, geh_ok=4, geh_total=4)],
            quarters_per_day=4)

        assert merged["vehicles"] == 16
        assert merged["geh_ok"] == 7 and merged["geh_total"] == 8
        assert merged["geh_pct"] == 87.5
        assert merged["integer_sensor_constraints"] == 8
        assert merged["integer_sensor_exact"] == 8
        assert merged["integer_sensor_exact_pct"] == 100.0
        assert merged["integer_sensor_max_abs_error"] == 0.0
        assert merged["achieved"]["E"] == [1.0] * 8
        assert [entry["quarter"] for entry in merged["purpose_allocation"]] == list(range(8))
        assert merged["relaxation_summary"] == {"clean": 8}
        assert merged["purpose_allocation_summary"]["replaced_routes"] == 2
        assert merged["purpose_allocation_summary"][
            "incompatible_routes_by_purpose"] == {"arbete": 4}

    def test_edges_missing_from_a_day_are_zero_not_absent(self):
        # pfe_fit_by_day indexes the window array by absolute quarter, so a
        # short row would silently shift every later day's GEH.
        merged = dl.merge_day_reports(
            [self._day(achieved={"E": [2.0] * 4}),
             self._day(achieved={"F": [5.0] * 4})],
            quarters_per_day=4)

        assert merged["achieved"]["E"] == [2.0] * 4 + [0.0] * 4
        assert merged["achieved"]["F"] == [0.0] * 4 + [5.0] * 4

    def test_violations_are_reported_at_their_window_quarter(self):
        day = self._day(bound_violations=[{"edge": "U", "quarter": 2}])
        merged = dl.merge_day_reports([self._day(), day], quarters_per_day=4)
        assert merged["bound_violations"] == [{"edge": "U", "quarter": 6}]

    def test_short_day_row_is_refused(self):
        with pytest.raises(ValueError, match="expected 4"):
            dl.merge_day_reports([self._day(achieved={"E": [1.0]})],
                                 quarters_per_day=4)

    def test_coverage_reports_the_conservative_envelope_across_days(self):
        # Days resample their own tour templates, so their pools differ
        # slightly. Coverage is a diagnostic: report the worst case seen and
        # keep the per-day values rather than inventing one pool-wide number.
        first = self._day()
        first["purpose_allocation_summary"]["protected_signature_coverage"] = {
            "signatures": 108, "missing_by_purpose": {"arbete": 31, "fritid": 47}}
        second = self._day()
        second["purpose_allocation_summary"]["protected_signature_coverage"] = {
            "signatures": 109, "missing_by_purpose": {"arbete": 28, "fritid": 51}}

        merged = dl.merge_day_reports([first, second], quarters_per_day=4)
        summary = merged["purpose_allocation_summary"]

        assert summary["protected_signature_coverage"] == {
            "signatures": 108,
            "missing_by_purpose": {"arbete": 31, "fritid": 51}}
        assert len(summary["protected_signature_coverage_by_day"]) == 2

    def test_empty_window_is_refused(self):
        with pytest.raises(ValueError, match="at least one day"):
            dl.merge_day_reports([])


class TestCompressedStorage:
    """Storage may gzip the big artifacts; nothing downstream may notice.

    The window a consumer assembles must be byte-identical whether its days
    are stored plain (pre-compression entries) or gzipped, and the manifest
    must always describe the bytes actually on disk.
    """

    def _store_day(self, tmp_path, day_dir):
        library = dl.DayLibrary(tmp_path / "library")
        identity = dl.DayIdentity(
            date="2027-03-09", source="forecast",
            pool_composition=("weekday",), inputs={"n": 1},
            source_hashes={"pfe": "x"})
        artifacts = {
            "calibrated.rou.xml": day_dir / "calibrated.rou.xml",
            "calibrated.agents.json": day_dir / "calibrated.agents.json",
            "fit.json": day_dir / "fit.json",
        }
        (day_dir / "fit.json").write_text("{\"geh_pct\": 100.0}")
        library.put(identity, artifacts)
        return library, identity

    def _calibrated_day(self, tmp_path):
        targets, solutions, mixes = _window(8, day=0)
        directory = tmp_path / "day"
        directory.mkdir()
        _write(directory / "calibrated.rou.xml", targets, solutions, mixes, 8)
        return directory

    def test_large_artifacts_are_stored_gzipped(self, tmp_path):
        day = self._calibrated_day(tmp_path)
        library, identity = self._store_day(tmp_path, day)
        entry = library.path_for(identity)
        assert (entry / "calibrated.rou.xml.gz").is_file()
        assert not (entry / "calibrated.rou.xml").exists()
        assert (entry / "fit.json").is_file()          # small files stay plain
        manifest = library.get(identity)
        assert manifest is not None
        assert "calibrated.rou.xml.gz" in manifest["artifacts"]

    def test_assembly_from_gzipped_days_is_byte_identical_to_plain(self, tmp_path):
        day = self._calibrated_day(tmp_path)
        library, identity = self._store_day(tmp_path, day)

        dl.assemble_window([day], tmp_path / "plain.rou.xml",
                           tmp_path / "plain.agents.json")
        dl.assemble_window([library.path_for(identity)],
                           tmp_path / "gz.rou.xml", tmp_path / "gz.agents.json")

        assert (tmp_path / "gz.rou.xml").read_bytes() == (
            tmp_path / "plain.rou.xml").read_bytes()
        assert (tmp_path / "gz.agents.json").read_bytes() == (
            tmp_path / "plain.agents.json").read_bytes()

    def test_tampered_gzip_entry_is_treated_as_absent(self, tmp_path):
        day = self._calibrated_day(tmp_path)
        library, identity = self._store_day(tmp_path, day)
        target = library.path_for(identity) / "calibrated.rou.xml.gz"
        target.write_bytes(target.read_bytes()[:-4] + b"XXXX")
        assert library.get(identity) is None

    def test_compression_actually_compresses(self, tmp_path):
        day = self._calibrated_day(tmp_path)
        library, identity = self._store_day(tmp_path, day)
        plain = (day / "calibrated.rou.xml").stat().st_size
        stored = (library.path_for(identity)
                  / "calibrated.rou.xml.gz").stat().st_size
        assert stored < plain / 4


class TestStorageHousekeeping:
    """A horizon-long warming run stores thousands of days; what it leaves
    behind matters as much as what it writes."""

    def _identity(self, date="2027-06-01"):
        return dl.DayIdentity(date=date, source="forecast",
                              pool_composition=("weekday",), inputs={"n": 1},
                              source_hashes={"pfe": "x"})

    def _artifacts(self, tmp_path, text="a"):
        path = tmp_path / "fit.json"
        path.write_text(f'{{"geh_pct": 100.0, "t": "{text}"}}')
        return {"fit.json": path}

    def test_staging_left_by_a_killed_build_is_swept(self, tmp_path):
        library = dl.DayLibrary(tmp_path / "library")
        identity = self._identity()
        abandoned = (library.path_for(identity).parent
                     / "deadbeef.staging")
        abandoned.mkdir(parents=True)
        (abandoned / "half-written.gz").write_bytes(b"x" * 64)
        old = time.time() - dl.DayLibrary.ABANDONED_STAGING_AGE_S - 60
        os.utime(abandoned, (old, old))

        library.put(identity, self._artifacts(tmp_path))

        assert not abandoned.exists()

    def test_a_concurrent_builds_fresh_staging_is_left_alone(self, tmp_path):
        library = dl.DayLibrary(tmp_path / "library")
        identity = self._identity()
        other = library.path_for(identity).parent / "otherkey.staging"
        other.mkdir(parents=True)

        library.put(identity, self._artifacts(tmp_path))

        assert other.is_dir()

    def test_replacing_an_entry_never_leaves_the_path_empty(self, tmp_path):
        """Re-storing a day must not delete-then-move: a reader that just
        verified the entry would find its files gone mid-assembly."""
        library = dl.DayLibrary(tmp_path / "library")
        identity = self._identity()
        library.put(identity, self._artifacts(tmp_path, "first"))
        entry = library.path_for(identity)
        seen = []

        real_replace = os.replace

        def watching_replace(src, dst):
            seen.append((entry / "manifest.json").is_file())
            return real_replace(src, dst)

        os.replace = watching_replace
        try:
            library.put(identity, self._artifacts(tmp_path, "second"))
        finally:
            os.replace = real_replace

        # The manifest is present before and after every rename step except
        # the instant the new entry lands, and is complete at the end.
        assert seen and seen[0] is True
        assert library.get(identity) is not None
        assert "second" in (entry / "fit.json").read_text()
        assert not (entry.with_name(entry.name + ".replaced")).exists()
