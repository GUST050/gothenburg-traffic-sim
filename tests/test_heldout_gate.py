"""Process-free proof that gate adoption needs BOTH artifacts and fails closed.

No SUMO, no socket, no campaign outcome: every fixture is synthetic and written
under pytest's tmp_path. The point of these tests is that neither artifact can
be changed without the other detecting it.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from traffic_sim.simulation.heldout_gate import (
    BOUNDED_CLAIM_SCOPE, CERTIFICATE_FIELDS, CERTIFICATE_KIND,
    CERTIFICATE_SCHEMA_VERSION, GATE_RECORD_FIELDS, GATE_RECORD_KIND,
    canonical_content_key, load_adopted_gate)

# v6 is the latest frozen campaign, but its source fingerprints are now stale.
# Synthetic compatibility fixtures bind its immutable schema while the
# autouse fixture below bypasses ONLY the live-source precondition. Dedicated
# source-enforcement tests do not use that bypass. Nothing here adopts anything
# for real: v6 has no gate record or certificate and all pairs live in tmp_path.
#
# The two earlier campaigns are both unadoptable, for DIFFERENT reasons, and each
# gets an explicit negative test below:
#   v4 — listed in REJECTED_CAMPAIGNS, refused by identity;
#   v5 — spent, and its frozen enforcement fingerprints no longer match the live
#        tree since the frozen identity moved to v6. That drift is the mechanism
#        that keeps a spent campaign unadoptable, so it must never be re-synced.
MANIFEST = Path("validation/monthly_proxy_manifest_v6.json")
REJECTED_MANIFEST = Path("validation/monthly_proxy_manifest_v4.json")
SPENT_MANIFEST = Path("validation/monthly_proxy_manifest_v5.json")


@pytest.fixture(autouse=True)
def _separate_loader_contract_from_live_campaign(monkeypatch, request):
    """Keep synthetic negative probes meaningful after the campaign drifts.

    Without this seam every malformed synthetic pair returns ``None`` at the
    earlier source-fingerprint check and stops testing the mutation named by
    the test. The live-source class is deliberately excluded and exercises the
    real fail-closed precondition.
    """
    if request.cls is TestLiveSourceFingerprintEnforcement:
        return
    import traffic_sim.simulation.heldout_gate as heldout_gate
    monkeypatch.setattr(
        heldout_gate, "_source_fingerprints_match", lambda _manifest: True
    )


def _manifest():
    return json.loads(MANIFEST.read_text())


def _gate_record(man, **overrides):
    """A record shaped exactly as gate_record_for would emit for `man`."""
    record = {
        "kind": GATE_RECORD_KIND,
        "schema_version": 1,
        "heldout_set": man["campaign_version"],
        "manifest_content_key": man["content_key"],
        "required_cases": len(man["cases"]),
        "completed_cases": len(man["cases"]),
        "case_count": len(man["cases"]),
        "proxy_version": man["proxy_version"],
        "shortlist_version": man["shortlist_version"],
        "shortlist_policy_content_key": man["shortlist_policy_content_key"],
        "gate_status": "pass",
        "ui_exposure_allowed": True,
        "global_best_claim_allowed": True,
        "gate_checks": {"practical_winner_recall": True,
                        "p90_normalized_shortlist_regret": True,
                        "failure_disqualification_recall": True,
                        "ranking_case_coverage": True,
                        "all_shortlists_contain_eligible_candidate": True,
                        "discriminating_case_coverage": True,
                        "discriminating_practical_winner_recall": True},
        # EXACTLY the metric set evaluate_validation_set emits.
        "metrics": {"winner_recall": 1.0,
                    "practical_winner_recall": 1.0,
                    "p90_normalized_shortlist_regret": 0.0,
                    "median_spearman": -0.371429,
                    "spearman_case_fraction": 1.0,
                    "ranking_case_fraction": 1.0,
                    "discriminating_case_fraction": 0.6,
                    "discriminating_practical_winner_recall": 1.0,
                    "median_spearman_discriminating": -0.637363,
                    "median_objective_spread_s": 436.1,
                    "failure_disqualification_recall": 0.681944,
                    "total_disqualified_schedules": 25},
        # Production thresholds = manifest gate + the ranking-coverage minimum.
        "thresholds": {**man["gate"],
                       "minimum_ranking_case_fraction":
                           man["minimum_ranking_case_fraction"]},
        "practical_winner_definition": "within the practical equivalence band",
        "regret_definition": "normalised shortlist regret",
        "failure_recall_definition": "disqualifications caught",
    }
    record.update(overrides)
    return record


def _certificate(man, gate_bytes, **overrides):
    cert = {
        "schema_version": CERTIFICATE_SCHEMA_VERSION,
        "kind": CERTIFICATE_KIND,
        "gate_record_sha256": hashlib.sha256(gate_bytes).hexdigest(),
        "gate_record_bytes": len(gate_bytes),
        "manifest_path": str(MANIFEST),
        "manifest_content_key": man["content_key"],
        "campaign_version": man["campaign_version"],
        "required_cases": len(man["cases"]),
        "proxy_version": man["proxy_version"],
        "shortlist_version": man["shortlist_version"],
        "shortlist_policy_content_key": man["shortlist_policy_content_key"],
        "claim_scope": BOUNDED_CLAIM_SCOPE,
    }
    cert.update(overrides)
    cert["content_key"] = overrides.get("content_key",
                                        canonical_content_key(cert))
    return cert


def _write_pair(tmp_path, record=None, cert_overrides=None, cert=None):
    man = _manifest()
    record = record if record is not None else _gate_record(man)
    gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
    gp = tmp_path / "gate.json"
    gp.write_bytes(gate_bytes)
    certificate = cert if cert is not None else _certificate(
        man, gate_bytes, **(cert_overrides or {}))
    cp = tmp_path / "cert.json"
    cp.write_text(json.dumps(certificate, indent=2, sort_keys=True))
    return gp, cp


class TestHappyPath:

    def test_both_valid_artifacts_adopt(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        record = load_adopted_gate(gp, cp)
        assert record is not None
        assert record["gate_status"] == "pass"
        assert record["heldout_set"] == _manifest()["campaign_version"]

    def test_certificate_key_set_is_exact(self):
        man = _manifest()
        cert = _certificate(man, b"{}")
        assert set(cert) == CERTIFICATE_FIELDS

    def test_gate_record_key_set_is_exact(self):
        assert set(_gate_record(_manifest())) == GATE_RECORD_FIELDS


class TestMissingArtifact:

    def test_absent_gate_record_fails_closed(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        gp.unlink()
        assert load_adopted_gate(gp, cp) is None

    def test_absent_certificate_fails_closed(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        cp.unlink()
        assert load_adopted_gate(gp, cp) is None

    def test_record_alone_never_adopts(self, tmp_path):
        """The v4 failure mode: a passing record with no post-review binding."""
        gp, cp = _write_pair(tmp_path)
        cp.unlink()
        assert json.loads(gp.read_text())["gate_status"] == "pass"
        assert load_adopted_gate(gp, cp) is None

    def test_symlinked_artifact_is_refused_not_followed(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        real = tmp_path / "real.json"
        real.write_bytes(gp.read_bytes())
        gp.unlink()
        os.symlink(real, gp)
        assert load_adopted_gate(gp, cp) is None


class TestRecordMutationBreaksTheBinding:
    """ANY byte changed in the record must fail against its unchanged certificate."""

    @pytest.mark.parametrize("overrides, why", [
        ({"metrics": {"winner_recall": 1.0, "practical_winner_recall": 0.5,
                      "p90_normalized_shortlist_regret": 0.0,
                      "median_spearman": 0.1, "spearman_case_fraction": 1.0,
                      "ranking_case_fraction": 1.0,
                      "discriminating_case_fraction": 0.6,
                      "discriminating_practical_winner_recall": 1.0,
                      "median_spearman_discriminating": 0.1,
                      "median_objective_spread_s": 1.0,
                      "failure_disqualification_recall": 0.7,
                      "total_disqualified_schedules": 25}}, "a changed metric"),
        ({"gate_checks": {"practical_winner_recall": False,
                          "p90_normalized_shortlist_regret": True,
                          "failure_disqualification_recall": True,
                          "ranking_case_coverage": True,
                          "all_shortlists_contain_eligible_candidate": True,
                          "discriminating_case_coverage": True,
                          "discriminating_practical_winner_recall": True}},
         "a flipped check"),
        ({"gate_status": "fail"}, "a changed status"),
        ({"ui_exposure_allowed": False}, "withheld UI exposure"),
        ({"global_best_claim_allowed": False}, "withheld global-best claim"),
        ({"completed_cases": 4}, "an incomplete run"),
        ({"required_cases": 4}, "a shrunken requirement"),
        ({"case_count": 4}, "a mismatched case count"),
        ({"heldout_set": "v2"}, "an earlier campaign"),
        ({"manifest_content_key": "0" * 64}, "a different manifest"),
        ({"shortlist_version": "stratified_shortlist_v2"}, "an older shortlist"),
        ({"proxy_version": "monthly_proxy_v0"}, "a different proxy"),
    ])
    def test_mutated_record_fails_against_unchanged_certificate(
            self, tmp_path, overrides, why):
        man = _manifest()
        good = _gate_record(man)
        good_bytes = json.dumps(good, indent=2, sort_keys=True).encode()
        cert = _certificate(man, good_bytes)          # binds the GOOD bytes
        mutated = _gate_record(man, **overrides)
        gp = tmp_path / "gate.json"
        gp.write_bytes(json.dumps(mutated, indent=2, sort_keys=True).encode())
        cp = tmp_path / "cert.json"
        cp.write_text(json.dumps(cert, indent=2, sort_keys=True))
        assert load_adopted_gate(gp, cp) is None, why

    def test_relaxed_thresholds_are_refused(self, tmp_path):
        man = _manifest()
        relaxed = dict(man["gate"])
        relaxed["practical_winner_recall_minimum"] = 0.1
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, thresholds=relaxed))
        assert load_adopted_gate(gp, cp) is None

    def test_unknown_field_in_record_is_refused(self, tmp_path):
        man = _manifest()
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, smuggled=True))
        assert load_adopted_gate(gp, cp) is None

    def test_single_byte_edit_is_detected(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        raw = gp.read_bytes()
        gp.write_bytes(raw + b" ")             # whitespace only; still valid JSON
        assert load_adopted_gate(gp, cp) is None

    def test_malformed_record_json_fails_closed(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        gp.write_bytes(b"not json")
        assert load_adopted_gate(gp, cp) is None


class TestCertificateMutationFailsClosed:

    @pytest.mark.parametrize("overrides, why", [
        ({"campaign_version": "v2"}, "a repointed campaign"),
        ({"manifest_content_key": "0" * 64}, "a repointed manifest key"),
        ({"manifest_path": "validation/monthly_proxy_manifest_v2.json"},
         "a repointed manifest path"),
        ({"required_cases": 4}, "an incomplete requirement"),
        ({"claim_scope": "anything_goes"}, "a broadened claim scope"),
        ({"gate_record_bytes": 999999}, "a wrong byte length"),
        ({"gate_record_sha256": "0" * 64}, "a wrong record hash"),
        ({"shortlist_policy_content_key": "deadbeef"}, "a policy change"),
    ])
    def test_mutated_certificate_fails_closed(self, tmp_path, overrides, why):
        gp, cp = _write_pair(tmp_path, cert_overrides=overrides)
        assert load_adopted_gate(gp, cp) is None, why

    def test_certificate_content_key_must_recompute(self, tmp_path):
        man = _manifest()
        record = _gate_record(man)
        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        cert = _certificate(man, gate_bytes)
        cert["required_cases"] = 4               # edited AFTER the key was set
        gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
        cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))
        assert load_adopted_gate(gp, cp) is None

    def test_unknown_or_missing_certificate_field_is_refused(self, tmp_path):
        man = _manifest()
        record = _gate_record(man)
        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        for mutate in (lambda c: c.update(surprise=1), lambda c: c.pop("claim_scope")):
            cert = _certificate(man, gate_bytes)
            mutate(cert)
            cert["content_key"] = canonical_content_key(cert)
            gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
            cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))
            assert load_adopted_gate(gp, cp) is None

    def test_malformed_certificate_json_fails_closed(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        cp.write_text("not json")
        assert load_adopted_gate(gp, cp) is None


class TestDefaultProductPathIsClosed:

    def test_no_default_artifacts_means_no_gate(self):
        from traffic_sim.simulation.monthly_search import (
            HELDOUT_GATE_CERTIFICATE, HELDOUT_GATE_RECORD,
            load_passing_heldout_gate)
        assert not HELDOUT_GATE_RECORD.exists()
        assert not HELDOUT_GATE_CERTIFICATE.exists()
        assert load_passing_heldout_gate() is None


# ── Adversarial probes for the LUNA-V5-01 hardening (Sol review 2026-07-27) ──
class TestStrictRecordSchema:
    """The probes Sol used: a fabricated record must not be accepted."""

    def test_schema_version_999_is_refused(self, tmp_path):
        man = _manifest()
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, schema_version=999))
        assert load_adopted_gate(gp, cp) is None

    def test_one_unrelated_passing_check_is_refused(self, tmp_path):
        """A minimal check set is not evidence the frozen gate was met."""
        man = _manifest()
        gp, cp = _write_pair(
            tmp_path, record=_gate_record(man, gate_checks={"something_true": True}))
        assert load_adopted_gate(gp, cp) is None

    def test_missing_required_check_is_refused(self, tmp_path):
        man = _manifest()
        checks = dict(_gate_record(man)["gate_checks"])
        checks.pop("failure_disqualification_recall")
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, gate_checks=checks))
        assert load_adopted_gate(gp, cp) is None

    def test_extra_unknown_check_is_refused(self, tmp_path):
        man = _manifest()
        checks = dict(_gate_record(man)["gate_checks"], invented_check=True)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, gate_checks=checks))
        assert load_adopted_gate(gp, cp) is None

    def test_invented_metric_without_required_metrics_is_refused(self, tmp_path):
        man = _manifest()
        gp, cp = _write_pair(
            tmp_path, record=_gate_record(man, metrics={"invented": 1.0}))
        assert load_adopted_gate(gp, cp) is None

    @pytest.mark.parametrize("value", ["1.0", True, None, [1.0]])
    def test_non_numeric_metric_is_refused(self, tmp_path, value):
        man = _manifest()
        metrics = dict(_gate_record(man)["metrics"], practical_winner_recall=value)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None

    @pytest.mark.parametrize("field", ["practical_winner_definition",
                                       "regret_definition",
                                       "failure_recall_definition"])
    def test_blank_definition_is_refused(self, tmp_path, field):
        man = _manifest()
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, **{field: "   "}))
        assert load_adopted_gate(gp, cp) is None


class TestLiveSourceFingerprintEnforcement:

    def test_rejected_campaign_v4_never_adopts(self, tmp_path):
        """Even a perfectly formed pair cannot adopt a rejected campaign."""
        man = json.loads(REJECTED_MANIFEST.read_text())
        record = _gate_record(man)
        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        cert = _certificate(man, gate_bytes, manifest_path=str(REJECTED_MANIFEST))
        gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
        cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))
        assert load_adopted_gate(gp, cp) is None

    def test_drifted_source_fingerprint_fails_closed(self, tmp_path):
        """A manifest whose frozen source no longer matches the tree is refused."""
        from traffic_sim.simulation.proxy_validation import (
            validation_manifest_content_key)
        man = _manifest()
        drifted = json.loads(json.dumps(man))
        first = sorted(drifted["source_fingerprints"])[0]
        drifted["source_fingerprints"][first] = "0" * 64
        drifted.pop("content_key", None)
        drifted["content_key"] = validation_manifest_content_key(drifted)
        mpath = tmp_path / "drifted_manifest.json"
        mpath.write_text(json.dumps(drifted, indent=2, sort_keys=True))
        record = _gate_record(drifted)
        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        cert = _certificate(drifted, gate_bytes, manifest_path=str(mpath))
        gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
        cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))
        assert load_adopted_gate(gp, cp) is None

    def test_latest_v6_fingerprints_are_stale_and_remain_frozen(self):
        """The live product must refuse v6 rather than re-label old evidence.

        The set is pinned EXACTLY, not asserted non-empty: it is a canary, and
        a loose check would stop reporting which sources moved. It therefore
        grows as the tree moves, and each addition is a deliberate edit.

        Grew 2026-08-05 when the ranking objective migrated to vehicle-hours:
        proxy_validation gained the tolerance-field migration, and the campaign
        runner gained v9 in EXACT_DEMAND_BINDING_CAMPAIGNS. More drift still
        means v6 stays refused, which is the property under test.
        """
        import hashlib as _h
        drifted = {
            rel for rel, digest in _manifest()["source_fingerprints"].items()
            if _h.sha256(Path(rel).read_bytes()).hexdigest() != digest
        }
        assert drifted == {
            "run_monthly_proxy_validation.py",
            "traffic_sim/core/closure_calendar.py",
            "traffic_sim/simulation/monthly_search.py",
            "traffic_sim/simulation/proxy_validation.py",
        }

    def test_spent_campaign_v5_cannot_adopt(self, tmp_path):
        """A spent campaign stays spent: its frozen fingerprints have drifted.

        v5 is not in REJECTED_CAMPAIGNS, so this is refused purely by live
        fingerprint enforcement — the same mechanism that would catch any
        attempt to reuse old evidence against a changed tree.
        """
        man = json.loads(SPENT_MANIFEST.read_text())
        record = _gate_record(man)
        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        cert = _certificate(man, gate_bytes, manifest_path=str(SPENT_MANIFEST))
        gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
        cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))
        assert load_adopted_gate(gp, cp) is None

    def test_v5_is_unadoptable_because_of_real_drift_not_rejection(self):
        """Name the mechanism, so a future re-sync would break this test."""
        import hashlib as _h
        import traffic_sim.simulation.heldout_gate as hg
        assert "v5" not in hg.REJECTED_CAMPAIGNS      # not refused by identity
        man = json.loads(SPENT_MANIFEST.read_text())
        drifted = [rel for rel, digest in man["source_fingerprints"].items()
                   if _h.sha256(Path(rel).read_bytes()).hexdigest() != digest]
        assert drifted, ("v5's fingerprints match the live tree again; if they "
                         "were re-synced, a spent campaign became adoptable")
        assert "traffic_sim/simulation/monthly_search.py" in drifted

    def test_no_gate_record_or_certificate_exists_for_any_campaign(self):
        """These tests prove adoption LOGIC; they never create real evidence."""
        assert not Path("validation/monthly_gate_record.json").exists()
        assert not Path("validation/monthly_gate_adoption_certificate.json").exists()


# ── Production compatibility + check/metric/threshold consistency ────────────
class TestProductionRecordCompatibility:
    """A record shaped exactly as `gate_record_for()` emits MUST adopt.

    The first hardening pass demanded `thresholds == manifest["gate"]`, but the
    evaluator also emits `minimum_ranking_case_fraction`, so no real record
    could ever have been adopted.
    """

    def test_production_shaped_record_adopts(self, tmp_path):
        gp, cp = _write_pair(tmp_path)
        record = load_adopted_gate(gp, cp)
        assert record is not None
        man = _manifest()
        assert record["thresholds"] == {
            **man["gate"],
            "minimum_ranking_case_fraction": man["minimum_ranking_case_fraction"]}

    def test_thresholds_missing_the_ranking_minimum_are_refused(self, tmp_path):
        man = _manifest()
        gp, cp = _write_pair(tmp_path,
                             record=_gate_record(man, thresholds=dict(man["gate"])))
        assert load_adopted_gate(gp, cp) is None

    def test_exact_production_metric_set_is_required(self, tmp_path):
        man = _manifest()
        extra = dict(_gate_record(man)["metrics"], invented_extra_metric=1.0)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=extra))
        assert load_adopted_gate(gp, cp) is None

    def test_missing_production_metric_is_refused(self, tmp_path):
        man = _manifest()
        metrics = dict(_gate_record(man)["metrics"])
        metrics.pop("median_spearman")
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None

    def test_nullable_diagnostic_metric_is_allowed(self, tmp_path):
        """median_spearman is nullable and is NOT gated; None must still adopt."""
        man = _manifest()
        metrics = dict(_gate_record(man)["metrics"], median_spearman=None,
                       median_spearman_discriminating=None)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is not None


class TestChecksAreRecomputedNotTrusted:
    """All-true checks are not evidence; they must follow from the numbers."""

    def _failing_metrics(self, man, **over):
        metrics = dict(_gate_record(man)["metrics"])
        metrics.update(over)
        return metrics

    def test_record_failing_every_numeric_gate_is_refused(self, tmp_path):
        man = _manifest()
        metrics = self._failing_metrics(
            man, practical_winner_recall=0.0,
            p90_normalized_shortlist_regret=0.99,
            failure_disqualification_recall=0.0,
            ranking_case_fraction=0.0,
            discriminating_case_fraction=0.0,
            discriminating_practical_winner_recall=0.0,
            total_disqualified_schedules=25)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None

    @pytest.mark.parametrize("over, why", [
        ({"practical_winner_recall": 0.1}, "recall below its minimum"),
        ({"p90_normalized_shortlist_regret": 0.99}, "regret above its maximum"),
        ({"failure_disqualification_recall": 0.0,
          "total_disqualified_schedules": 25}, "failure recall below minimum"),
        ({"ranking_case_fraction": 0.0}, "ranking coverage below minimum"),
        ({"discriminating_case_fraction": 0.0}, "discriminating coverage below minimum"),
        ({"discriminating_practical_winner_recall": 0.0},
         "discriminating recall below minimum"),
    ])
    def test_each_numeric_gate_is_independently_recomputed(
            self, tmp_path, over, why):
        man = _manifest()
        gp, cp = _write_pair(
            tmp_path, record=_gate_record(man, metrics=self._failing_metrics(man, **over)))
        assert load_adopted_gate(gp, cp) is None, why

    def test_vacuous_failure_recall_is_allowed_when_nothing_was_disqualified(
            self, tmp_path):
        """Production treats zero disqualifications as a vacuous pass."""
        man = _manifest()
        metrics = self._failing_metrics(
            man, failure_disqualification_recall=None,
            total_disqualified_schedules=0)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is not None

    def test_none_metric_backing_a_passing_check_is_refused(self, tmp_path):
        man = _manifest()
        metrics = self._failing_metrics(man, practical_winner_recall=None)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None


# ── Producer value domains (Sol edge probes 2026-07-27) ─────────────────────
class TestProducerValueDomains:

    @pytest.mark.parametrize("name, value", [
        ("total_disqualified_schedules", 0.0),      # float count
        ("total_disqualified_schedules", -1),       # negative count
        ("total_disqualified_schedules", None),     # never nullable
        ("winner_recall", None),                    # never nullable
        ("spearman_case_fraction", None),           # never nullable
        ("median_spearman", float("nan")),
        ("median_spearman", float("inf")),
        ("median_objective_spread_s", float("-inf")),
        ("practical_winner_recall", 1.5),           # outside [0, 1]
        ("failure_disqualification_recall", -0.1),
        ("median_spearman", 1.5),                   # outside [-1, 1]
        ("p90_normalized_shortlist_regret", -0.1),  # negative
    ])
    def test_impossible_producer_values_fail_closed(self, tmp_path, name, value):
        man = _manifest()
        metrics = dict(_gate_record(man)["metrics"])
        metrics[name] = value
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None, f"{name}={value!r}"

    def test_float_zero_count_cannot_buy_a_vacuous_failure_recall_pass(
            self, tmp_path):
        """0.0 is not producible; it must not unlock the vacuous pass."""
        man = _manifest()
        metrics = dict(_gate_record(man)["metrics"],
                       failure_disqualification_recall=0.0,
                       total_disqualified_schedules=0.0)
        gp, cp = _write_pair(tmp_path, record=_gate_record(man, metrics=metrics))
        assert load_adopted_gate(gp, cp) is None


class TestAdoptionContractStaysInSync:
    """The machine-readable contract must not drift from the loader."""

    CONTRACT = Path("validation/monthly_gate_adoption_contract_v1.json")

    def _contract(self):
        return json.loads(self.CONTRACT.read_text())

    def test_contract_content_key_recomputes(self):
        c = self._contract()
        assert canonical_content_key(c) == c["content_key"]

    def test_declared_field_sets_match_the_module(self):
        import traffic_sim.simulation.heldout_gate as hg
        c = self._contract()
        assert c["artifacts"]["gate_record"]["exact_fields"] == sorted(hg.GATE_RECORD_FIELDS)
        assert c["artifacts"]["adoption_certificate"]["exact_fields"] == sorted(hg.CERTIFICATE_FIELDS)

    def test_declared_value_schema_matches_the_module(self):
        import traffic_sim.simulation.heldout_gate as hg
        v = self._contract()["gate_record_value_schema"]
        assert v["exact_metric_keys"] == sorted(hg.PRODUCTION_METRICS)
        assert v["non_nullable_metrics"] == sorted(hg.NON_NULLABLE_METRICS)
        assert v["nonnegative_integer_counts"] == sorted(hg._COUNT_METRICS)
        assert v["all_numeric_values_must_be_finite"] is True
        assert v["schema_version"] == hg.GATE_RECORD_SCHEMA_VERSION

    def test_contract_states_the_production_threshold_rule(self):
        rule = self._contract()["thresholds_rule"]
        assert "minimum_ranking_case_fraction" in rule
        assert "metrics need merely be non-empty" not in rule

    def test_contract_lists_the_rejected_campaigns(self):
        import traffic_sim.simulation.heldout_gate as hg
        assert self._contract()["rejected_campaigns"] == sorted(hg.REJECTED_CAMPAIGNS)


class TestRealEvaluatorToLoader:
    """Bind the CONSUMER to the actual PRODUCER, end to end, in memory.

    Builds synthetic outcomes, runs the real `evaluate_validation_set`, the real
    `gate_record_for`, mints a certificate, and requires the loader to adopt.
    No SUMO, no outcome root, no campaign.
    """

    def _passing_outcomes(self, man):
        cases = []
        for case in man["cases"]:
            ids = case["schedule_ids"]
            candidates = []
            # A case only counts as DISCRIMINATING when its objective spread
            # exceeds the 300 s practical-equivalence band, so the cases the
            # manifest pre-registers as discriminating are given a real spread.
            step = 100.0 if case["expected_discriminating"] else 10.0
            for index, sid in enumerate(ids):
                failing = index == len(ids) - 1
                candidates.append({
                    "schedule_id": sid,
                    "eligible": not failing,
                    "sumo_objective": None if failing else float(index) * step,
                    "proxy_rank": index,
                    "shortlisted": index < 2,
                    "proxy_failure_flag": failing,
                })
            cases.append({
                "case_id": case["case_id"],
                "exhaustive": True,
                "provenance": {
                    # valid lowercase-hex digests: the validator checks form
                    "demand_sha256": "d" * 64, "network_sha256": "e" * 64,
                    "proxy_input_sha256": "f" * 64,
                    "matched_baseline_id": "b" * 32,
                    "seed_set": [1000, 1001, 1002],
                    "simulation_mode": "meso",
                    "sumo_version": "Eclipse SUMO sumo 1.27.1",
                },
                "candidates": candidates,
            })
        return {
            "schema_version": 1,
            "kind": "monthly_proxy_validation_outcomes",
            "proxy_version": man["proxy_version"],
            "shortlist_version": man["shortlist_version"],
            "shortlist_policy_content_key": man["shortlist_policy_content_key"],
            "manifest_content_key": man["content_key"],
            "cases": cases,
        }

    def test_real_producer_record_is_adopted_by_the_loader(self, tmp_path):
        import importlib.util
        import sys as _sys
        from traffic_sim.simulation.proxy_validation import evaluate_validation_set

        spec = importlib.util.spec_from_file_location(
            "rmpv_gate", "run_monthly_proxy_validation.py")
        rmpv = importlib.util.module_from_spec(spec)
        _sys.modules["rmpv_gate"] = rmpv
        spec.loader.exec_module(rmpv)

        man = _manifest()
        report = evaluate_validation_set(man, self._passing_outcomes(man))
        assert report["gate_status"] == "pass", (
            "synthetic outcomes must exercise the producer, not skip past it: "
            f"{report['gate_checks']}")

        record = rmpv.gate_record_for(report, man, len(man["cases"]))
        assert record is not None, "producer refused its own passing report"

        gate_bytes = json.dumps(record, indent=2, sort_keys=True).encode()
        gp = tmp_path / "gate.json"; gp.write_bytes(gate_bytes)
        cert = _certificate(man, gate_bytes)
        cp = tmp_path / "cert.json"; cp.write_text(json.dumps(cert))

        adopted = load_adopted_gate(gp, cp)
        assert adopted is not None, (
            "the loader rejected a record the real producer emitted; "
            f"keys={sorted(record)} thresholds={record.get('thresholds')}")
        assert adopted["gate_status"] == "pass"
