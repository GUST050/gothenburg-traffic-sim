"""The v5 held-out design is frozen BEFORE outcomes and disjoint from v1-v4.

Process-free: reads only tracked pre-outcome artifacts. No outcome, report or
run root is opened, and no v5 gate record or adoption certificate exists.
"""
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.proxy_validation import (
    validate_validation_manifest, validation_manifest_content_key)

VALIDATION = Path("validation")
POLICY = VALIDATION / "monthly_proxy_policy_v5.json"
SELECTION = VALIDATION / "heldout_v5_selection.json"
MANIFEST = VALIDATION / "monthly_proxy_manifest_v5.json"


def _load(path):
    return json.loads(path.read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class TestFrozenBeforeOutcomes:

    def test_manifest_declares_pre_outcome_freeze(self):
        man = _load(MANIFEST)
        assert man["frozen_before_outcomes"] is True
        assert man["outcomes_present_at_freeze"] is False
        assert man["outcomes_path"] is None
        assert man["status"] == "frozen_pre_outcome_design"

    def test_no_outcome_derived_field_anywhere(self):
        """A pre-outcome design may not carry a MEASURED value.

        Checks exact keys, not substrings: threshold names legitimately contain
        words like "winner_recall" while carrying no measurement.
        """
        banned_keys = {"gate_status", "gate_checks", "metrics", "completed_cases",
                       "case_reports", "gate_record", "outcomes"}

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in banned_keys, key
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
        assert sel["selection_method"] == "deterministic_structural_pre_outcome"
        assert sel["status"] == "selection_frozen_pre_outcome"

    def test_no_v5_gate_record_or_certificate_exists(self):
        assert not (VALIDATION / "monthly_gate_record.json").exists()
        assert not (VALIDATION / "monthly_gate_adoption_certificate.json").exists()


class TestCanonicalIdentity:

    @pytest.mark.parametrize("path", [POLICY, SELECTION])
    def test_content_key_recomputes(self, path):
        payload = _load(path)
        assert payload["content_key"] == _canonical_key(payload)

    def test_manifest_key_uses_the_production_scheme(self):
        """The manifest key is the validator's normalized hash, not a generic one."""
        man = _load(MANIFEST)
        assert validation_manifest_content_key(man) == man["content_key"]

    def test_keys_are_unique_across_the_three_artifacts(self):
        keys = [_load(p)["content_key"] for p in (POLICY, SELECTION, MANIFEST)]
        assert len(set(keys)) == 3

    def test_manifest_binds_policy_and_selection_keys(self):
        man, pol, sel = _load(MANIFEST), _load(POLICY), _load(SELECTION)
        assert man["policy_content_key"] == pol["content_key"]
        assert man["selection_content_key"] == sel["content_key"]

    def test_production_validator_accepts_the_manifest(self):
        man = _load(MANIFEST)
        validate_validation_manifest(man)
        assert validation_manifest_content_key(man) == man["content_key"]

    def test_v5_key_differs_from_v4(self):
        v4 = _load(VALIDATION / "monthly_proxy_manifest_v4.json")
        assert _load(MANIFEST)["content_key"] != v4["content_key"]


class TestShapeAndStrata:

    def test_exactly_five_cases_and_seventy_five_schedules(self):
        man = _load(MANIFEST)
        assert len(man["cases"]) == 5
        assert man["minimum_cases"] == 5
        per_case = [len(c["schedule_ids"]) for c in man["cases"]]
        assert per_case == [15] * 5
        assert sum(per_case) == 75

    def test_schedule_ids_are_distinct_within_and_across_cases(self):
        man = _load(MANIFEST)
        allids = [s for c in man["cases"] for s in c["schedule_ids"]]
        assert len(allids) == len(set(allids)) == 75

    def test_case_ids_distinct_and_one_edge_each(self):
        man = _load(MANIFEST)
        assert len({c["case_id"] for c in man["cases"]}) == 5
        assert all(len(c["directed_edges"]) == 1 for c in man["cases"])

    def test_declared_strata_cover_every_case(self):
        man = _load(MANIFEST)
        required = man["required_strata"]
        for case in man["cases"]:
            for axis, allowed in required.items():
                assert case["strata"][axis] in allowed, (case["case_id"], axis)

    def test_gate_semantics_are_unchanged_from_v4(self):
        """Hardening adoption must not move the bar the evidence must clear."""
        v4 = _load(VALIDATION / "monthly_proxy_manifest_v4.json")
        assert _load(MANIFEST)["gate"] == v4["gate"]


class TestDisjointness:

    def _prior_edges(self):
        """The EXPLICIT v1-v4 historical inputs v5 actually froze against.

        This deliberately does NOT glob. A glob over `monthly_proxy_manifest*`
        absorbs every campaign frozen later — v6 today, v7 tomorrow — and would
        silently restate v5's historical boundary as something it never claimed.
        The sources are read from v5's own recorded `prior_sources`, so the
        frozen claim is checked against the frozen inputs.
        """
        edges = set()
        for name in _load(SELECTION)["disjointness"]["prior_sources"]:
            payload = _load(VALIDATION / name)
            for case in payload.get("cases", []):
                edges.update(case.get("directed_edges", []))
                edges.update((case.get("closure_search_spec") or {})
                             .get("directed_edges", []))
            for entry in payload.get("selected_edges", []):
                if entry.get("edge_id"):
                    edges.add(entry["edge_id"])
        return edges

    def test_prior_sources_are_historical_only(self):
        """v5's boundary may never absorb itself or any later campaign."""
        sources = _load(SELECTION)["disjointness"]["prior_sources"]
        assert sources, "the frozen boundary must name its inputs"
        for name in sources:
            assert "v5" not in name and "v6" not in name, name
            assert (VALIDATION / name).is_file(), name

    def test_v5_edges_are_disjoint_from_every_v1_v4_heldout_edge(self):
        prior = self._prior_edges()
        assert prior, "prior held-out edges must be discoverable"
        v5 = {e for c in _load(MANIFEST)["cases"] for e in c["directed_edges"]}
        assert len(v5) == 5
        assert not (v5 & prior), sorted(v5 & prior)

    def test_selection_and_manifest_agree_on_the_edges(self):
        sel = {e["edge_id"] for e in _load(SELECTION)["selected_edges"]}
        man = {e for c in _load(MANIFEST)["cases"] for e in c["directed_edges"]}
        assert sel == man

    def test_recorded_disjointness_claim_is_true(self):
        sel = _load(SELECTION)
        assert sel["disjointness"]["intersection_with_v1_v4"] == []
        assert sel["disjointness"]["prior_heldout_edge_count"] == len(self._prior_edges())


class TestSourceFingerprints:

    def test_fingerprints_cover_every_executable_gate_and_selection_input(self):
        man = _load(MANIFEST)
        fp = man["source_fingerprints"]
        for required in ("traffic_sim/simulation/heldout_gate.py",
                         "traffic_sim/simulation/monthly_search.py",
                         "traffic_sim/simulation/monthly_proxy.py",
                         "traffic_sim/simulation/proxy_validation.py",
                         "traffic_sim/core/closure_calendar.py",
                         "run_monthly_proxy_validation.py",
                         "validation/monthly_proxy_policy_v5.json",
                         "validation/heldout_v5_selection.json"):
            assert required in fp, required

    def test_recorded_fingerprints_are_frozen_not_refreshed(self):
        """v5's recorded values are HISTORY and must never be re-synced.

        Pinned literally: if a later campaign re-froze v5 to make its suite
        pass, these fail. That is the point — the drift below is precisely what
        keeps a spent campaign unadoptable.
        """
        fp = _load(MANIFEST)["source_fingerprints"]
        assert fp["traffic_sim/simulation/monthly_search.py"] == "6da01267588d592d101895bc532d9a4903eaa6017027d7e4bd8e2b932c648f65"
        assert fp["validation/heldout_v5_selection.json"] == "a10c31da667ec85c74cb3f85d8edcd33921d2eb1c3f3501d9d807ac70876b52c"
        assert fp["validation/monthly_proxy_policy_v5.json"] == "2e4b16f3109d51a40b52fd3f46743f7cce5eca69df8bc3f5233b00d2be5c6f94"

    # Every source v5 bound that has since changed, with WHY. The list grows as
    # later campaigns legitimately edit shared code; it must never SHRINK,
    # because each entry is a reason v5 can no longer be adopted.
    EXPECTED_DRIFT = {
        # Full-day independent scheduling changed the shared calendar contract.
        "traffic_sim/core/closure_calendar.py",
        # LUNA-V6-02: the frozen campaign identity moved v5 -> v6.
        "traffic_sim/simulation/monthly_search.py",
        # LUNA-V6-04: the runner gained v6 exact-demand binding, and later v7,
        # v8 and v9 as the objective migrated to vehicle-hours.
        "run_monthly_proxy_validation.py",
        # 2026-08-05: the gate schema took the tolerance-field migration, so a
        # gate names the UNIT of the objective it bounds (seconds or
        # vehicle-hours) instead of assuming seconds.
        "traffic_sim/simulation/proxy_validation.py",
    }

    def test_the_spent_campaign_no_longer_matches_the_live_tree(self):
        """Exactly the enforcement drift that closes v5, named explicitly."""
        drifted = {path for path, digest
                   in _load(MANIFEST)["source_fingerprints"].items()
                   if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest}
        assert drifted == self.EXPECTED_DRIFT, drifted

    def test_the_drift_is_specific_not_a_blanket_mismatch(self):
        """Every other bound source still matches, so v5's record stayed intact."""
        for path, digest in _load(MANIFEST)["source_fingerprints"].items():
            if path in self.EXPECTED_DRIFT:
                continue
            live = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            assert live == digest, path

    def test_the_hardened_gate_module_is_bound(self):
        fp = _load(MANIFEST)["source_fingerprints"]
        assert fp["traffic_sim/simulation/heldout_gate.py"] == hashlib.sha256(
            Path("traffic_sim/simulation/heldout_gate.py").read_bytes()).hexdigest()


class TestFreezeIsReproducible:
    """The helper must reproduce the frozen bytes WITHOUT rewriting the tree.

    Sol's point: re-running the writer and then comparing is self-fulfilling.
    This recomposes in memory and compares against the artifacts on disk.
    """

    def _rebuild(self):
        import sys
        sys.path.insert(0, "tools")
        from freeze_heldout_v5 import build_artifacts
        return build_artifacts()

    def test_the_policy_still_recomposes_byte_for_byte(self):
        """v5's policy has no cross-campaign inputs, so it is still stable."""
        rebuilt = self._rebuild()
        relative = "validation/monthly_proxy_policy_v5.json"
        assert Path(relative).read_text() == rebuilt[relative]

    def test_recomposition_of_the_spent_package_no_longer_matches(self):
        """Drift invalidates REUSE without touching a single frozen byte.

        v5's builder derives its historical boundary and source fingerprints
        from the live tree, which now contains v6. Recomposing therefore yields
        different bytes — proof that the spent package cannot be regenerated and
        silently reused. The frozen files themselves are unchanged, which
        `test_recomposition_did_not_modify_the_tree` asserts directly.
        """
        rebuilt = self._rebuild()
        for relative in ("validation/heldout_v5_selection.json",
                         "validation/monthly_proxy_manifest_v5.json"):
            assert Path(relative).read_text() != rebuilt[relative], (
                f"{relative} still recomposes identically; a spent campaign "
                f"must not be reproducible against a changed tree")

    def test_recomposition_did_not_modify_the_tree(self):
        before = {p: Path(p).read_bytes() for p in (
            "validation/monthly_proxy_policy_v5.json",
            "validation/heldout_v5_selection.json",
            "validation/monthly_proxy_manifest_v5.json")}
        self._rebuild()
        for path, raw in before.items():
            assert Path(path).read_bytes() == raw, path


class TestPhysicalIndependence:
    """Five ids must be five INDEPENDENT road cases, not one street twice."""

    def _edges(self):
        return _load(SELECTION)["selected_edges"]

    def test_no_reverse_direction_pair(self):
        pairs = {(e["structural"]["junction_u"], e["structural"]["junction_v"])
                 for e in self._edges()}
        for u, v in pairs:
            assert (v, u) not in pairs, f"reverse pair {u}/{v}"

    def test_no_two_cases_share_a_junction(self):
        nodes = [n for e in self._edges()
                 for n in (e["structural"]["junction_u"],
                           e["structural"]["junction_v"])]
        assert len(nodes) == len(set(nodes)), "cases share a junction"

    def test_no_pathological_tiny_edges(self):
        rule = _load(SELECTION)["physical_independence"]
        for e in self._edges():
            assert e["structural"]["length_m"] >= rule["min_edge_length_m"]

    def test_recorded_independence_claim_is_true(self):
        rule = _load(SELECTION)["physical_independence"]
        assert rule["shared_junctions"] == 0
        assert rule["reverse_pairs"] == 0
