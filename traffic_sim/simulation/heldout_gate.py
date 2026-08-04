"""Two-artifact held-out gate adoption (LUNA-V5-01).

WHY THIS EXISTS.  The v4 adoption attempt copied a passing gate record to a
tracked path and let the loader validate the record's *own* fields.  That is
self-certifying: the record asserts which campaign it belongs to, and any
byte-level edit to a metric, threshold or claim flag still produced a record
that validated against itself.  Adoption now requires TWO artifacts:

  1. the gate record produced by the frozen evaluator, and
  2. a post-review ADOPTION CERTIFICATE that independently binds that record's
     exact bytes (SHA-256 + byte length) together with the frozen manifest
     identity and the bounded claim scope the reviewer approved.

Neither artifact can be changed without the other detecting it: editing any
byte of the record breaks the certificate's hash/length binding, and editing
the certificate breaks its own canonical content key.  Absence, alteration,
an earlier campaign, an incomplete run or an unknown field in either artifact
fails CLOSED — the loader returns ``None`` and the product stays in
bounded-exhaustive mode.

This module runs no process and reads no campaign outcome.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CERTIFICATE_SCHEMA_VERSION = 1
CERTIFICATE_KIND = "monthly_gate_adoption_certificate"
GATE_RECORD_KIND = "monthly_proxy_validation_gate_record"
GATE_RECORD_SCHEMA_VERSION = 1

# Repository root: every fingerprinted source must resolve inside it.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Campaigns whose evidence may never be adopted, whatever a certificate says.
# v4's audit passed, but its evidence was produced under the old
# self-certifying loader; the hardened enforcement source no longer matches the
# fingerprint v4 froze, so adopting it would launder a stale licence.
REJECTED_CAMPAIGNS = {
    "v4": ("v4 adoption was rejected for whole-record integrity (LUNA-V4-04 "
           "concluded rejected); its evidence predates the hardened loader"),
}

# Gate checks/metrics the practical-winner branch always emits, plus the
# additive discrimination pair a manifest opts into via its gate block.
_BASE_CHECKS = frozenset({
    "practical_winner_recall", "p90_normalized_shortlist_regret",
    "failure_disqualification_recall", "ranking_case_coverage",
    "all_shortlists_contain_eligible_candidate"})
_DISCRIMINATING_CHECKS = frozenset({
    "discriminating_case_coverage", "discriminating_practical_winner_recall"})
# The EXACT metric set `evaluate_validation_set` emits. Several are nullable
# by construction, so the type rule allows None but the consistency rules below
# refuse a None that a passing check depends on.
PRODUCTION_METRICS = frozenset({
    "winner_recall", "practical_winner_recall",
    "p90_normalized_shortlist_regret", "median_spearman",
    "spearman_case_fraction", "ranking_case_fraction",
    "discriminating_case_fraction", "discriminating_practical_winner_recall",
    "median_spearman_discriminating", "median_objective_spread_s",
    "failure_disqualification_recall", "total_disqualified_schedules"})

# Metrics the evaluator ALWAYS emits as a number; None means the record was not
# produced by it. The rest are nullable by construction.
NON_NULLABLE_METRICS = frozenset({
    "winner_recall", "spearman_case_fraction", "ranking_case_fraction",
    "discriminating_case_fraction", "total_disqualified_schedules"})

# Exact value domains. Every numeric value must additionally be FINITE: NaN and
# infinity are not producible by the evaluator and are not valid JSON numbers.
_UNIT_INTERVAL_METRICS = frozenset({
    "winner_recall", "practical_winner_recall", "spearman_case_fraction",
    "ranking_case_fraction", "discriminating_case_fraction",
    "discriminating_practical_winner_recall",
    "failure_disqualification_recall"})
_CORRELATION_METRICS = frozenset({
    "median_spearman", "median_spearman_discriminating"})
_NONNEGATIVE_METRICS = frozenset({
    "p90_normalized_shortlist_regret", "median_objective_spread_s"})
_COUNT_METRICS = frozenset({"total_disqualified_schedules"})


def _metric_value_ok(name: str, value: Any) -> bool:
    """Enforce the producer's value domain for one metric."""
    if value is None:
        return name not in NON_NULLABLE_METRICS
    if isinstance(value, bool):
        return False
    if name in _COUNT_METRICS:
        # A count is a nonnegative INTEGER; 0.0 is not producible and would
        # otherwise trigger the vacuous failure-recall pass.
        return isinstance(value, int) and value >= 0
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if name in _UNIT_INTERVAL_METRICS:
        return 0.0 <= value <= 1.0
    if name in _CORRELATION_METRICS:
        return -1.0 <= value <= 1.0
    if name in _NONNEGATIVE_METRICS:
        return value >= 0.0
    return True
_DEFINITION_FIELDS = ("practical_winner_definition", "regret_definition",
                      "failure_recall_definition")

# Exact key set of an adoption certificate. Missing or unknown keys are fatal:
# a certificate that may carry arbitrary extra fields is not a binding.
CERTIFICATE_FIELDS = frozenset({
    "schema_version",
    "kind",
    "gate_record_sha256",
    "gate_record_bytes",
    "manifest_path",
    "manifest_content_key",
    "campaign_version",
    "required_cases",
    "proxy_version",
    "shortlist_version",
    "shortlist_policy_content_key",
    "claim_scope",
    "content_key",
})

# Exact key set of a gate record, as produced by
# run_monthly_proxy_validation.gate_record_for (report minus case_reports plus
# the campaign identity fields).
GATE_RECORD_FIELDS = frozenset({
    "kind", "schema_version", "heldout_set", "manifest_content_key",
    "required_cases", "completed_cases", "proxy_version", "shortlist_version",
    "shortlist_policy_content_key", "gate_status", "ui_exposure_allowed",
    "global_best_claim_allowed", "case_count", "gate_checks", "metrics",
    "thresholds", "practical_winner_definition", "regret_definition",
    "failure_recall_definition",
})

# The only claim scope an adoption certificate may authorise.
BOUNDED_CLAIM_SCOPE = (
    "sumo_verified_schedules_within_the_enumerated_search_space"
)


def canonical_content_key(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON of ``payload`` minus ``content_key``."""
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _regular_file_bytes(path: Path) -> bytes | None:
    """Read a REGULAR, non-symlink file, or return None.

    A symlink could point the adoption seam at an unreviewed artifact, so the
    link itself is refused rather than followed.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _source_fingerprints_match(manifest: Mapping[str, Any]) -> bool:
    """Every fingerprint the manifest froze must still match the live file.

    Without this the loader validated only the manifest's SHAPE, so a
    certificate-bound record from a campaign whose enforcement source has since
    drifted would still be adopted — laundering a stale licence.
    """
    recorded = manifest.get("source_fingerprints")
    if not isinstance(recorded, dict) or not recorded:
        return False
    for relative, expected in recorded.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            return False
        path = (_REPO_ROOT / relative).resolve()
        try:
            path.relative_to(_REPO_ROOT)          # repository-confined
        except ValueError:
            return False
        raw = _regular_file_bytes(_REPO_ROOT / relative)
        if raw is None or hashlib.sha256(raw).hexdigest() != expected:
            return False
    return True


def _validate_certificate(cert: Any) -> dict[str, Any] | None:
    if not isinstance(cert, dict) or set(cert) != CERTIFICATE_FIELDS:
        return None
    if (cert["schema_version"] != CERTIFICATE_SCHEMA_VERSION
            or cert["kind"] != CERTIFICATE_KIND):
        return None
    if canonical_content_key(cert) != cert["content_key"]:
        return None
    if not (isinstance(cert["gate_record_sha256"], str)
            and len(cert["gate_record_sha256"]) == 64
            and set(cert["gate_record_sha256"]) <= set("0123456789abcdef")):
        return None
    size = cert["gate_record_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    required = cert["required_cases"]
    if isinstance(required, bool) or not isinstance(required, int) or required <= 0:
        return None
    for field in ("manifest_path", "manifest_content_key", "campaign_version",
                  "proxy_version", "shortlist_version",
                  "shortlist_policy_content_key"):
        if not isinstance(cert[field], str) or not cert[field]:
            return None
    if cert["claim_scope"] != BOUNDED_CLAIM_SCOPE:
        return None
    return cert


def _validate_gate_record(record: Any, cert: Mapping[str, Any],
                          manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(record, dict) or set(record) != GATE_RECORD_FIELDS:
        return None
    if record["kind"] != GATE_RECORD_KIND or record["gate_status"] != "pass":
        return None
    if record["schema_version"] != GATE_RECORD_SCHEMA_VERSION:
        return None
    for field in _DEFINITION_FIELDS:
        if not isinstance(record[field], str) or not record[field].strip():
            return None
    if (record["ui_exposure_allowed"] is not True
            or record["global_best_claim_allowed"] is not True):
        return None
    # Identity must agree with BOTH the certificate and the frozen manifest.
    if (record["heldout_set"] != cert["campaign_version"]
            or record["heldout_set"] != manifest.get("campaign_version")):
        return None
    if (record["manifest_content_key"] != cert["manifest_content_key"]
            or record["manifest_content_key"] != manifest.get("content_key")):
        return None
    for field in ("proxy_version", "shortlist_version",
                  "shortlist_policy_content_key"):
        if record[field] != cert[field] or record[field] != manifest.get(field):
            return None
    # Case completeness: every frozen case must have completed.
    frozen_cases = len(manifest.get("cases") or ())
    if (record["required_cases"] != cert["required_cases"]
            or record["required_cases"] != frozen_cases
            or record["completed_cases"] != frozen_cases
            or record["case_count"] != frozen_cases):
        return None
    # Thresholds must be exactly what the production evaluator emits: the
    # frozen manifest gate PLUS the manifest's ranking-coverage minimum. The
    # earlier equality-with-gate rule was wrong and made a real
    # `gate_record_for()` record impossible to adopt.
    expected_thresholds = {
        **(manifest.get("gate") or {}),
        "minimum_ranking_case_fraction":
            manifest.get("minimum_ranking_case_fraction"),
    }
    if record["thresholds"] != expected_thresholds:
        return None
    # The check and metric key sets are EXACT, derived from the frozen gate:
    # a record carrying one unrelated passing check and an invented metric is
    # not evidence that the frozen gate was met.
    gate = manifest.get("gate") or {}
    discriminating = ("minimum_discriminating_case_fraction" in gate
                      or "discriminating_practical_winner_recall_minimum" in gate)
    want_checks = set(_BASE_CHECKS) | (
        set(_DISCRIMINATING_CHECKS) if discriminating else set())

    checks = record["gate_checks"]
    if not isinstance(checks, dict) or set(checks) != want_checks:
        return None
    if any(value is not True for value in checks.values()):
        return None

    # The metric set is EXACT, not a subset: an invented extra metric means the
    # record was not produced by the frozen evaluator.
    metrics = record["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != PRODUCTION_METRICS:
        return None
    for name, value in metrics.items():
        if not _metric_value_ok(name, value):
            return None

    # RECOMPUTE every numerically-derivable check from the metrics and
    # thresholds it claims to summarise. All-true checks were previously taken
    # on trust, so a record failing every numeric gate still adopted.
    th = expected_thresholds

    def _ge(metric, minimum):
        value = metrics.get(metric)
        return value is not None and minimum is not None and value >= minimum

    def _le(metric, maximum):
        value = metrics.get(metric)
        return value is not None and maximum is not None and value <= maximum

    recomputed = {
        "practical_winner_recall":
            _ge("practical_winner_recall", th.get("practical_winner_recall_minimum")),
        "p90_normalized_shortlist_regret":
            _le("p90_normalized_shortlist_regret",
                th.get("p90_normalized_shortlist_regret_maximum")),
        "failure_disqualification_recall": (
            metrics.get("total_disqualified_schedules") == 0
            or _ge("failure_disqualification_recall",
                   th.get("failure_disqualification_recall_minimum"))),
        "ranking_case_coverage":
            _ge("ranking_case_fraction", th.get("minimum_ranking_case_fraction")),
    }
    if discriminating:
        recomputed["discriminating_case_coverage"] = _ge(
            "discriminating_case_fraction",
            th.get("minimum_discriminating_case_fraction"))
        recomputed["discriminating_practical_winner_recall"] = _ge(
            "discriminating_practical_winner_recall",
            th.get("discriminating_practical_winner_recall_minimum"))
    for name, value in recomputed.items():
        if value is not True or checks.get(name) is not True:
            return None
    return record


def load_adopted_gate(gate_path: Path,
                      certificate_path: Path) -> dict[str, Any] | None:
    """Return the adopted gate record, or None (fail closed).

    Both artifacts must be present, regular, mutually consistent and consistent
    with the frozen manifest the certificate names.
    """
    from traffic_sim.simulation.proxy_validation import (  # noqa: PLC0415
        validate_validation_manifest, validation_manifest_content_key)

    cert_bytes = _regular_file_bytes(Path(certificate_path))
    gate_bytes = _regular_file_bytes(Path(gate_path))
    if cert_bytes is None or gate_bytes is None:
        return None
    try:
        cert = json.loads(cert_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    cert = _validate_certificate(cert)
    if cert is None:
        return None

    # The certificate binds the EXACT record bytes.
    if len(gate_bytes) != cert["gate_record_bytes"]:
        return None
    if hashlib.sha256(gate_bytes).hexdigest() != cert["gate_record_sha256"]:
        return None

    manifest_bytes = _regular_file_bytes(Path(cert["manifest_path"]))
    if manifest_bytes is None:
        return None
    try:
        manifest = json.loads(manifest_bytes)
        validate_validation_manifest(manifest)
    except Exception:                                        # noqa: BLE001
        return None
    if validation_manifest_content_key(manifest) != cert["manifest_content_key"]:
        return None
    # A rejected campaign can never be adopted, however well-formed the pair.
    if (manifest.get("campaign_version") in REJECTED_CAMPAIGNS
            or cert["campaign_version"] in REJECTED_CAMPAIGNS):
        return None
    # Every frozen source fingerprint must still match the live tree.
    if not _source_fingerprints_match(manifest):
        return None

    try:
        record = json.loads(gate_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    return _validate_gate_record(record, cert, manifest)
