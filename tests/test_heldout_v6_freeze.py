"""The v6 held-out design is demand-supported, independent and pre-outcome.

Process-free: reads only tracked artifacts and the canonical demand archive's
identity. No SUMO, no campaign outcome, no run root, no sibling archive.
"""
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.proxy_validation import (
    validate_validation_manifest, validation_manifest_content_key)

VALIDATION = Path("validation")
POLICY = VALIDATION / "monthly_proxy_policy_v6.json"
SELECTION = VALIDATION / "heldout_v6_selection.json"
MANIFEST = VALIDATION / "monthly_proxy_manifest_v6.json"


def _load(path):
    return json.loads(path.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TestFrozenBeforeOutcomes:

    def test_manifest_declares_pre_outcome_freeze(self):
        man = _load(MANIFEST)
        assert man["frozen_before_outcomes"] is True
        assert man["outcomes_present_at_freeze"] is False
        assert man["outcomes_path"] is None
        assert man["status"] == "frozen_pre_outcome_design"

    def test_no_outcome_derived_key_anywhere(self):
        banned = {"gate_status", "gate_checks", "metrics", "completed_cases",
                  "case_reports", "gate_record", "outcomes"}

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in banned, key
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for path in (POLICY, SELECTION, MANIFEST):
            walk(_load(path))
        blob = json.dumps([_load(p) for p in (POLICY, SELECTION, MANIFEST)])
        for banned_path in ("outcomes.json", "report.json",
                            "runs/closure-proxy-validation"):
            assert banned_path not in blob, banned_path

    def test_selection_declares_it_read_no_outcome(self):
        sel = _load(SELECTION)
        assert sel["selection_reads_outcomes"] is False
        assert sel["pilot_selection_does_not_read_proxy"] is True
        assert sel["status"] == "selection_frozen_pre_outcome"

    def test_no_v6_gate_record_or_certificate_exists(self):
        assert not (VALIDATION / "monthly_gate_record.json").exists()
        assert not (VALIDATION / "monthly_gate_adoption_certificate.json").exists()


class TestCanonicalDemandBinding:

    def test_selection_binds_the_exact_canonical_archive(self):
        demand = _load(SELECTION)["canonical_demand"]
        assert demand["path"] == "runs/demand-20260721-222017-41bc682a-bbe1"
        assert demand["demand_build_key"] == "2ac04275daabe93c"
        assert demand["build_id"] == "42d841800726b9b911df"
        assert demand["epoch_sim"] == "2027-07-15T00:00:00"
        assert demand["n_intervals"] == 480
        assert demand["git_commit"] == "a26a068c0a54fe697aa5c97497469a71bc58c399"

    def test_all_five_demand_hashes_are_bound(self):
        digests = _load(SELECTION)["canonical_demand"]["file_sha256"]
        assert set(digests) == {
            "calibrated.rou.xml", "calibrated_v1.rou.xml", "calibrated_v2.rou.xml",
            "demand_meta.json", "manifest.json"}
        assert digests["calibrated.rou.xml"].startswith("56000bc43a8f")
        assert all(len(v) == 64 for v in digests.values())

    def test_designation_is_recorded_as_v6_local(self):
        note = _load(SELECTION)["canonical_demand"]["designation"]
        assert "v6-local" in note and "does not repair" in note

    def test_manifest_transitively_binds_demand_through_the_selection_key(self):
        man, sel = _load(MANIFEST), _load(SELECTION)
        assert man["selection_content_key"] == sel["content_key"]
        assert sel["content_key"] == _canonical_key(sel)


class TestCanonicalIdentity:

    @pytest.mark.parametrize("path", [POLICY, SELECTION])
    def test_content_key_recomputes(self, path):
        payload = _load(path)
        assert payload["content_key"] == _canonical_key(payload)

    def test_manifest_uses_the_production_key_scheme(self):
        man = _load(MANIFEST)
        assert validation_manifest_content_key(man) == man["content_key"]

    def test_production_validator_accepts_the_manifest(self):
        validate_validation_manifest(_load(MANIFEST))

    def test_keys_are_unique_and_differ_from_v5(self):
        keys = [_load(p)["content_key"] for p in (POLICY, SELECTION, MANIFEST)]
        assert len(set(keys)) == 3
        v5 = _load(VALIDATION / "monthly_proxy_manifest_v5.json")["content_key"]
        assert _load(MANIFEST)["content_key"] != v5


class TestShapeAndGate:

    def test_exactly_five_cases_and_seventy_five_unique_schedules(self):
        man = _load(MANIFEST)
        assert len(man["cases"]) == 5 and man["minimum_cases"] == 5
        assert [len(c["schedule_ids"]) for c in man["cases"]] == [15] * 5
        allids = [s for c in man["cases"] for s in c["schedule_ids"]]
        assert len(allids) == len(set(allids)) == 75

    def test_at_least_two_cases_are_expected_discriminating(self):
        man = _load(MANIFEST)
        assert sum(1 for c in man["cases"] if c["expected_discriminating"]) >= 2

    def test_gate_semantics_unchanged_from_v5(self):
        assert _load(MANIFEST)["gate"] == _load(
            VALIDATION / "monthly_proxy_manifest_v5.json")["gate"]

    def test_declared_strata_cover_every_case(self):
        man = _load(MANIFEST)
        for case in man["cases"]:
            for axis, allowed in man["required_strata"].items():
                assert case["strata"][axis] in allowed, (case["case_id"], axis)


class TestExclusionAndIndependence:

    def _prior_edges(self):
        edges = set()
        for path in sorted(VALIDATION.glob("monthly_proxy_manifest*.json")):
            if path.name == MANIFEST.name:
                continue
            for case in _load(path).get("cases", []):
                edges.update(case.get("directed_edges", []))
                edges.update((case.get("closure_search_spec") or {})
                             .get("directed_edges", []))
        for name in ("heldout_v4_selection.json", "heldout_v5_selection.json"):
            p = VALIDATION / name
            if p.is_file():
                for entry in _load(p)["selected_edges"]:
                    edges.add(entry["edge_id"])
        return edges

    def test_v6_edges_are_disjoint_from_every_v1_v5_edge(self):
        prior = self._prior_edges()
        assert prior
        v6 = {e for c in _load(MANIFEST)["cases"] for e in c["directed_edges"]}
        assert len(v6) == 5 and not (v6 & prior)

    def test_no_shared_junction_with_any_prior_case(self):
        prior_nodes = set()
        for edge in self._prior_edges():
            parts = edge.split("_")
            if len(parts) >= 3:
                prior_nodes.update((parts[0], parts[1]))
        for entry in _load(SELECTION)["selected_edges"]:
            s = entry["structural"]
            assert s["junction_u"] not in prior_nodes
            assert s["junction_v"] not in prior_nodes

    def test_selected_cases_are_pairwise_independent(self):
        nodes = [n for e in _load(SELECTION)["selected_edges"]
                 for n in (e["structural"]["junction_u"], e["structural"]["junction_v"])]
        assert len(nodes) == len(set(nodes)), "cases share a junction"

    def test_no_stub_edges(self):
        rule = _load(SELECTION)["physical_independence"]
        for entry in _load(SELECTION)["selected_edges"]:
            assert entry["structural"]["length_m"] >= rule["min_edge_length_m"]


class TestDemandSupportedSelection:

    def test_every_case_has_strictly_positive_support(self):
        for entry in _load(SELECTION)["selected_edges"]:
            features = entry["demand_features"]
            assert features["min_support"] > 0, entry["case_id"]
            assert features["median_support"] > 0

    def test_every_case_records_raw_pre_outcome_features(self):
        for entry in _load(SELECTION)["selected_edges"]:
            assert set(entry["demand_features"]) == {
                "min_support", "median_support", "temporal_variation",
                "schedule_ids", "window_exposure"}
            assert 0.0 <= entry["demand_features"]["temporal_variation"] <= 1.0

    def test_raw_counts_are_kept_for_every_variant_and_window(self):
        """[P1 evidence] Aggregates alone are not auditable."""
        for entry in _load(SELECTION)["selected_edges"]:
            features = entry["demand_features"]
            exposure = features["window_exposure"]
            assert set(exposure) == {"q10", "q50", "q90"}
            assert len(features["schedule_ids"]) == 15
            assert len(set(features["schedule_ids"])) == 15
            for variant, counts in exposure.items():
                assert len(counts) == 15, (entry["case_id"], variant)
                assert all(isinstance(c, int) and c > 0 for c in counts)

    def test_recorded_schedule_ids_are_exactly_the_manifest_schedules(self):
        """Raw window i is auditable only if it names a real frozen schedule."""
        cases = {c["case_id"]: c["schedule_ids"] for c in _load(MANIFEST)["cases"]}
        for entry in _load(SELECTION)["selected_edges"]:
            assert entry["demand_features"]["schedule_ids"] == cases[entry["case_id"]]

    def test_aggregates_recompute_from_the_raw_counts_alone(self):
        """The published recipe must reproduce every stored aggregate."""
        import statistics
        for entry in _load(SELECTION)["selected_edges"]:
            features = entry["demand_features"]
            exposure = features["window_exposure"]
            variations, supports, minimum = [], [], None
            for variant in ("q50", "q10", "q90"):
                counts = exposure[variant]
                low, high = min(counts), max(counts)
                minimum = low if minimum is None else min(minimum, low)
                variations.append((high - low) / high if high else 0.0)
                supports.append(statistics.median(counts))
            assert features["min_support"] == minimum, entry["case_id"]
            assert features["median_support"] == round(
                statistics.median(supports), 3), entry["case_id"]
            assert features["temporal_variation"] == round(
                sum(variations) / len(variations), 6), entry["case_id"]

    def test_the_frozen_ranking_recomputes_from_the_artifact_alone(self):
        """Order and discriminating labels follow from the recorded evidence."""
        entries = _load(SELECTION)["selected_edges"]
        ranked = sorted(entries, key=lambda e: (
            -e["demand_features"]["temporal_variation"],
            -e["demand_features"]["median_support"], e["edge_id"]))
        assert [e["case_id"] for e in ranked] == [e["case_id"] for e in entries]
        assert [e["expected_discriminating"] for e in ranked] == [
            True, True, False, False, False]

    def test_discriminating_cases_rank_above_the_controls(self):
        entries = _load(SELECTION)["selected_edges"]
        disc = [e["demand_features"]["temporal_variation"]
                for e in entries if e["expected_discriminating"]]
        ctrl = [e["demand_features"]["temporal_variation"]
                for e in entries if not e["expected_discriminating"]]
        assert disc and ctrl and min(disc) >= max(ctrl)

    def test_formula_is_versioned_and_disclaims_prediction(self):
        formula = _load(POLICY)["selection_formula"]
        assert formula["version"] == _load(SELECTION)["formula_version"]
        assert formula["reads_outcomes"] is False
        assert "does not guarantee" in formula["not_a_prediction"]

    def test_every_case_records_its_ranking_reason(self):
        for entry in _load(SELECTION)["selected_edges"]:
            assert entry["rank_reason"]
            assert _load(SELECTION)["formula_version"] in entry["rank_reason"]


class TestSourceFingerprintsAndReproduction:

    EXPECTED_DRIFT = {
        "traffic_sim/core/closure_calendar.py",
        "traffic_sim/simulation/monthly_search.py",
        # 2026-08-05, vehicle-hours objective migration: the runner gained v7,
        # v8 and v9 exact-demand binding and now measures the displaced-vehicle
        # objective, and the gate schema names the unit it bounds.
        "run_monthly_proxy_validation.py",
        "traffic_sim/simulation/proxy_validation.py",
    }

    def test_fingerprints_cover_the_executable_selection_path(self):
        fp = _load(MANIFEST)["source_fingerprints"]
        for required in ("traffic_sim/simulation/heldout_selection.py",
                         "traffic_sim/simulation/heldout_gate.py",
                         "traffic_sim/simulation/monthly_search.py",
                         "traffic_sim/simulation/proxy_validation.py",
                         "traffic_sim/core/closure_calendar.py",
                         "tools/freeze_heldout_v6.py",
                         "validation/monthly_proxy_policy_v6.json",
                         "validation/heldout_v6_selection.json"):
            assert required in fp, required

    def test_recorded_fingerprints_are_frozen_and_live_drift_is_explicit(self):
        fingerprints = _load(MANIFEST)["source_fingerprints"]
        drifted = {
            path for path, digest in fingerprints.items()
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
        }
        assert drifted == self.EXPECTED_DRIFT

    def test_a_source_edit_would_invalidate_the_manifest(self, tmp_path):
        """Drift detection is real, proven without touching the tree."""
        fp = dict(_load(MANIFEST)["source_fingerprints"])
        target = "traffic_sim/simulation/heldout_selection.py"
        mutated = Path(target).read_bytes() + b"\n# drift\n"
        assert hashlib.sha256(mutated).hexdigest() != fp[target]

    def test_freeze_preview_reports_drift_without_rewriting_history(self):
        import sys
        sys.path.insert(0, "tools")
        from freeze_heldout_v6 import build_artifacts
        before = {p: Path(p).read_bytes() for p in
                  (str(POLICY), str(SELECTION), str(MANIFEST))}
        generated = build_artifacts()
        assert Path(POLICY).read_text() == generated[str(POLICY)]
        assert Path(SELECTION).read_text() == generated[str(SELECTION)]
        recorded = _load(MANIFEST)
        preview = json.loads(generated[str(MANIFEST)])
        differing = {
            key for key in recorded if recorded[key] != preview[key]
        }
        assert differing == {"content_key", "source_fingerprints"}
        fingerprint_drift = {
            path for path in recorded["source_fingerprints"]
            if recorded["source_fingerprints"][path]
            != preview["source_fingerprints"][path]
        }
        assert fingerprint_drift == self.EXPECTED_DRIFT
        for path, raw in before.items():
            assert Path(path).read_bytes() == raw, path


class TestPublicationIsImmutableAndAtomic:
    """[P1 immutability] Sol review 2026-07-27."""

    def _tool(self):
        import sys
        sys.path.insert(0, "tools")
        import freeze_heldout_v6
        return freeze_heldout_v6

    def test_there_is_no_overwrite_escape_hatch(self):
        """A flag that can replace a frozen package makes it not frozen."""
        assert "--force" not in Path("tools/freeze_heldout_v6.py").read_text()
        with pytest.raises(SystemExit):
            self._tool().main(["--force"])

    def test_publish_refuses_an_occupied_destination(self, tmp_path):
        tool = self._tool()
        artifacts = {"validation/a.json": "{}\n", "validation/b.json": "{}\n"}
        (tmp_path / "validation").mkdir()
        (tmp_path / "validation" / "b.json").write_text("PRIOR")
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            tool.publish(artifacts, root=tmp_path)
        assert (tmp_path / "validation" / "b.json").read_text() == "PRIOR"
        assert not (tmp_path / "validation" / "a.json").exists()

    def test_a_failed_publish_leaves_no_partial_package(self, tmp_path):
        """Injected mid-publish failure must roll back what it already wrote."""
        tool = self._tool()
        artifacts = {f"validation/{name}.json": "{}\n"
                     for name in ("a", "b", "c")}
        (tmp_path / "validation").mkdir()
        calls = []

        def flaky(path, text):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError("disk full")
            path.write_text(text)

        with pytest.raises(OSError, match="disk full"):
            tool.publish(artifacts, root=tmp_path, write=flaky)
        assert len(calls) == 2                      # it really did fail midway
        survivors = sorted(p.name for p in (tmp_path / "validation").iterdir())
        assert survivors == [], f"partial package left behind: {survivors}"

    def test_a_writer_that_creates_its_target_then_raises_is_rolled_back(
            self, tmp_path):
        """[P1 atomicity] The path must be OWNED before the writer can create it.

        Appending to the rollback list only after the writer returns would leave
        this half-written file behind — recorded by nothing, and indistinguishable
        from a complete freeze on the next glance.
        """
        tool = self._tool()
        artifacts = {f"validation/{name}.json": "{}\n"
                     for name in ("a", "b", "c")}
        (tmp_path / "validation").mkdir()
        calls = []

        def creates_then_raises(path, text):
            calls.append(path)
            path.write_text("HALF-WRITTEN")      # the destination now EXISTS
            assert path.exists()
            if len(calls) == 2:
                raise OSError("crashed after creating the file")

        with pytest.raises(OSError, match="crashed after creating"):
            tool.publish(artifacts, root=tmp_path, write=creates_then_raises)
        assert len(calls) == 2
        residue = sorted(p.name for p in (tmp_path / "validation").iterdir())
        assert residue == [], f"partial/scratch files survived: {residue}"

    def test_a_foreign_file_at_a_final_path_is_preserved_not_deleted(
            self, tmp_path):
        """Ownership is narrow: this call owns only what it created.

        A writer that touches a final path directly produces a file this call
        cannot prove it owns. Refusing over it is recoverable; deleting someone
        else's bytes is not. Our own scratch is still cleaned up.
        """
        tool = self._tool()
        artifacts = {f"validation/{name}.json": "{}\n" for name in ("a", "b")}
        (tmp_path / "validation").mkdir()

        def hostile(path, text):
            path.write_text(text)
            final = path.with_name(path.name.replace(".partial", ""))
            final.write_text("SNEAKED IN")       # creates the FINAL path itself
            raise OSError("failed after touching the final path")

        with pytest.raises(OSError, match="after touching the final path"):
            tool.publish(artifacts, root=tmp_path, write=hostile)
        survivors = sorted(p.name for p in (tmp_path / "validation").iterdir())
        assert survivors == ["a.json"], survivors
        assert (tmp_path / "validation" / "a.json").read_text() == "SNEAKED IN"
        assert not list((tmp_path / "validation").glob("*.partial"))

    def test_a_competing_final_appearing_before_publish_is_refused(self, tmp_path):
        """[P1 no-clobber race] The gap between checking and publishing.

        `os.replace` would silently overwrite a final that appeared after the
        absence check. The competitor is created here AFTER the scratch is fully
        written and BEFORE the final publish, which is exactly that gap.
        """
        tool = self._tool()
        artifacts = {f"validation/{name}.json": "OURS\n" for name in ("a", "b", "c")}
        (tmp_path / "validation").mkdir()

        def stage_then_race(path, text):
            path.write_text(text)                # scratch fully written
            final = path.with_name(path.name.replace(".partial", ""))
            if final.name == "b.json":           # competitor wins the race
                final.write_text("COMPETING BYTES")

        with pytest.raises(SystemExit, match="competing final appeared"):
            tool.publish(artifacts, root=tmp_path, write=stage_then_race)

        validation = tmp_path / "validation"
        # the competitor's bytes are preserved EXACTLY
        assert (validation / "b.json").read_text() == "COMPETING BYTES"
        # only paths this call published are rolled back
        assert not (validation / "a.json").exists()
        assert not (validation / "c.json").exists()
        assert not list(validation.glob("*.partial"))
        assert sorted(p.name for p in validation.iterdir()) == ["b.json"]

    def test_the_publish_primitive_itself_refuses_to_clobber(self, tmp_path):
        """Guards the fix: os.replace must not come back as the final step."""
        source = Path("tools/freeze_heldout_v6.py").read_text()
        publish_body = source.split("def publish(")[1].split("\ndef ")[0]
        assert "os.link(" in publish_body
        assert "os.replace(" not in publish_body
        assert "O_EXCL" in publish_body

    def test_an_existing_scratch_path_is_not_reused(self, tmp_path):
        """Exclusive scratch creation: never adopt a leftover partial file."""
        tool = self._tool()
        artifacts = {"validation/a.json": "A\n"}
        (tmp_path / "validation").mkdir()
        stale = tmp_path / "validation" / "a.json.partial"
        stale.write_text("LEFTOVER")
        with pytest.raises(SystemExit, match="scratch path already exists"):
            tool.publish(artifacts, root=tmp_path)
        assert stale.read_text() == "LEFTOVER"
        assert not (tmp_path / "validation" / "a.json").exists()

    def test_rollback_residue_is_raised_not_swallowed(self, tmp_path, monkeypatch):
        """A rollback that cannot clean up must NOT report a quiet failure."""
        import pathlib
        tool = self._tool()
        artifacts = {f"validation/{name}.json": "{}\n" for name in ("a", "b")}
        (tmp_path / "validation").mkdir()
        real_unlink = pathlib.Path.unlink

        def stubborn(self, *args, **kwargs):
            if self.name.startswith("a."):
                raise OSError("permission denied")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "unlink", stubborn)

        def flaky(path, text):
            path.write_text(text)
            if path.name.startswith("b."):
                raise OSError("disk full")

        with pytest.raises(RuntimeError, match="rollback left residue"):
            tool.publish(artifacts, root=tmp_path, write=flaky)

    def test_the_final_path_is_never_written_in_place(self, tmp_path):
        """Finals arrive by atomic replace, so they are never half-written."""
        tool = self._tool()
        artifacts = {"validation/a.json": "A\n"}
        (tmp_path / "validation").mkdir()
        seen = []
        tool.publish(artifacts, root=tmp_path,
                     write=lambda p, t: (seen.append(p.name), p.write_text(t))[-1])
        assert seen == ["a.json.partial"], seen
        assert (tmp_path / "validation" / "a.json").read_text() == "A\n"
        assert not (tmp_path / "validation" / "a.json.partial").exists()
        assert sorted(p.name for p in (tmp_path / "validation").iterdir()) == \
            ["a.json"], "scratch link must not survive publication"

    def test_a_clean_publish_writes_every_artifact(self, tmp_path):
        tool = self._tool()
        artifacts = {"validation/a.json": "A\n", "validation/b.json": "B\n"}
        (tmp_path / "validation").mkdir()
        assert tool.publish(artifacts, root=tmp_path) == sorted(artifacts)
        for relative, text in artifacts.items():
            assert (tmp_path / relative).read_text() == text


class TestProductStaysClosed:

    def test_frozen_identity_is_v6_but_adoption_is_closed(self):
        import traffic_sim.simulation.monthly_search as ms
        assert ms.HELDOUT_CAMPAIGN_MANIFEST.name == "monthly_proxy_manifest_v6.json"
        assert ms.load_passing_heldout_gate() is None
        assert not ms.HELDOUT_GATE_RECORD.exists()
        assert not ms.HELDOUT_GATE_CERTIFICATE.exists()
