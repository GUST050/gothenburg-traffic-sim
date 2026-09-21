"""Stage 1 of the item-1 contract: explain repeated day calibrations.

The tool under test is read-only by contract. These tests therefore pin two
different kinds of promise: that the arithmetic and the cause precedence are
right, and that running the command changes nothing under the library root.
"""
import json
import os
from pathlib import Path

import pytest

from tools.explain_day_reuse import (Entry, LibraryScan, classify,
                                     diff_identity, explain, load_library,
                                     main, parse_bound)

THREE = ["edge_shares", "edge_shares_q10", "edge_shares_q90"]
ONE = ["edge_shares"]


def _identity(date, *, variants, composition, pool="aaa", constraints="c0",
              sources=None):
    return {
        "schema_version": 1,
        "date": date,
        "source": "forecast",
        "pool_composition": list(composition),
        "inputs": {
            "variants": list(variants),
            "constraints": constraints,
            "candidate_pool": pool,
            "candidate_metadata": pool,
            "catalog_keys": {"pool": pool},
        },
        "source_hashes": dict(sources or {"pfe": "1"}),
    }


def _entry(date, key, **kwargs):
    return Entry(date=date, key=key, written_at=0.0,
                 identity=_identity(date, **kwargs),
                 path=f"runs/demand-days/{date}/{key}/manifest.json")


def _scan(*entries, unreadable=()):
    return LibraryScan(entries=tuple(entries), unreadable=tuple(unreadable))


def _write_entry(root, date, key, *, written_at=None, identity=None,
                 manifest=None, **kwargs):
    directory = Path(root) / date / key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    if manifest is None:
        manifest = {"schema_version": 1, "kind": "calibrated_demand_day",
                    "key": key,
                    "identity": identity or _identity(date, **kwargs),
                    "artifacts": {}}
    path.write_text(json.dumps(manifest) if not isinstance(manifest, str)
                    else manifest, encoding="utf-8")
    if written_at is not None:
        os.utime(path, (written_at, written_at))
    return path


# --- identity difference -------------------------------------------------

def test_diff_expands_nested_input_and_source_fields():
    a = _identity("2027-06-03", variants=THREE, composition=["weekday"],
                  pool="aaa")
    b = _identity("2027-06-03", variants=THREE,
                  composition=["weekday", "weekend"], pool="bbb",
                  sources={"pfe": "2"})

    assert diff_identity(a, b) == (
        "inputs.candidate_metadata", "inputs.candidate_pool",
        "inputs.catalog_keys", "pool_composition", "source_hashes.pfe")


def test_diff_of_identical_identities_is_empty():
    a = _identity("2027-06-03", variants=THREE, composition=["weekday"])
    assert diff_identity(a, dict(a)) == ()


# --- cause precedence ----------------------------------------------------

def test_a_source_change_outranks_every_other_difference():
    paths = ("inputs.candidate_pool", "inputs.variants",
             "pool_composition", "source_hashes.pfe")
    assert classify(paths) == "source_change"


def test_a_variant_subset_outranks_a_composition_difference():
    assert classify(("inputs.constraints", "inputs.variants",
                     "pool_composition")) == "variant_subset"


def test_composition_outranks_candidate_drift():
    assert classify(("inputs.candidate_pool", "pool_composition")) == \
        "pool_composition"


def test_candidate_drift_is_reported_when_it_is_the_only_difference():
    assert classify(("inputs.candidate_metadata",)) == "candidate_drift"


def test_an_unrecognised_field_is_other():
    assert classify(("inputs.through_share_target",)) == "other"


def test_no_difference_has_no_cause():
    assert classify(()) is None


# --- counting ------------------------------------------------------------

def test_q50_aliases_are_linked_and_excluded_from_calibration_counts():
    scan = _scan(
        _entry("2027-06-03", "k1", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "k2", variants=ONE, composition=["weekday"],
               constraints="c1"),
    )

    report = explain(scan)

    assert report["summary"]["full_calibrations"] == 1
    assert report["summary"]["q50_aliases"] == 1
    assert report["summary"]["unlinked_aliases"] == 0
    assert report["summary"]["repeated_calibrations"] == 0
    assert report["summary"]["causes"] == {}
    assert report["dates"][0]["entries"][1]["alias_of"] == "k1"


def test_an_alias_matching_two_full_entries_takes_the_lowest_key():
    scan = _scan(
        _entry("2027-06-03", "kb", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "ka", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "kz", variants=ONE, composition=["weekday"],
               constraints="c1"),
    )

    report = explain(scan)

    entries = report["dates"][0]["entries"]
    aliases = [e for e in entries if e["kind"] == "alias"]
    assert aliases[0]["alias_of"] == "ka"


def test_an_alias_that_matches_nothing_is_counted_not_dropped():
    scan = _scan(
        _entry("2027-06-03", "k1", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "k2", variants=ONE, composition=["weekend"]),
    )

    report = explain(scan)

    assert report["summary"]["unlinked_aliases"] == 1
    assert report["dates"][0]["entries"][1]["alias_of"] is None


def test_repeated_dates_report_one_comparison_per_extra_calibration():
    scan = _scan(
        _entry("2027-06-03", "k1", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "k2", variants=THREE,
               composition=["weekday", "weekend"], pool="bbb"),
        _entry("2027-06-04", "k3", variants=THREE, composition=["weekday"]),
    )

    report = explain(scan)

    assert report["summary"]["dates"] == 2
    assert report["summary"]["full_calibrations"] == 3
    assert report["summary"]["repeated_dates"] == 1
    assert report["summary"]["repeated_calibrations"] == 1
    assert report["summary"]["causes"] == {"pool_composition": 1}
    comparison = report["dates"][0]["comparisons"][0]
    assert comparison["baseline_key"] == "k1"
    assert comparison["compared_key"] == "k2"
    assert "pool_composition" in comparison["fields"]


def test_comparisons_follow_time_and_choose_the_closest_prior_identity():
    """Historical labels must not depend on lexicographic content hashes."""
    old_pure = _entry(
        "2027-06-03", "z-old-pure", variants=THREE,
        composition=["weekday"], sources={"pfe": "old"})
    new_pure = _entry(
        "2027-06-03", "a-new-pure", variants=THREE,
        composition=["weekday"], sources={"pfe": "new"})
    new_mixed = _entry(
        "2027-06-03", "b-new-mixed", variants=THREE,
        composition=["weekday", "weekend"], pool="mixed",
        sources={"pfe": "new"})
    object.__setattr__(old_pure, "written_at", 1.0)
    object.__setattr__(new_pure, "written_at", 2.0)
    object.__setattr__(new_mixed, "written_at", 3.0)

    comparisons = explain(_scan(new_mixed, old_pure, new_pure))[
        "dates"][0]["comparisons"]

    assert [(row["baseline_key"], row["compared_key"], row["cause"])
            for row in comparisons] == [
        ("z-old-pure", "a-new-pure", "source_change"),
        ("a-new-pure", "b-new-mixed", "pool_composition"),
    ]


def test_two_variants_are_neither_a_full_calibration_nor_a_q50_alias():
    partial = _entry(
        "2027-06-03", "partial", variants=THREE[:2],
        composition=["weekday"])

    report = explain(_scan(partial))

    assert report["summary"]["full_calibrations"] == 0
    assert report["summary"]["q50_aliases"] == 0
    assert report["summary"]["other_entries"] == 1
    assert report["dates"][0]["entries"][0]["kind"] == "other"


# --- ordering and determinism -------------------------------------------

def test_dates_and_entries_are_ordered_deterministically():
    scan = _scan(
        _entry("2027-06-04", "kz", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "kb", variants=THREE, composition=["weekday"]),
        _entry("2027-06-03", "ka", variants=THREE, composition=["weekday"]),
    )

    report = explain(scan)

    assert [d["date"] for d in report["dates"]] == ["2027-06-03", "2027-06-04"]
    assert [e["key"] for e in report["dates"][0]["entries"]] == ["ka", "kb"]


def test_two_runs_of_the_cli_write_byte_identical_reports(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"])
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    main(["--root", str(root), "--output", str(first)])
    main(["--root", str(root), "--output", str(second)])

    assert first.read_bytes() == second.read_bytes()


# --- damaged input -------------------------------------------------------

def test_an_unreadable_manifest_is_counted_with_its_error_class(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"])
    _write_entry(root, "2027-06-03", "broken", manifest="{not json")

    scan = load_library(root)
    report = explain(scan)

    assert report["summary"]["unreadable_manifests"] == 1
    assert report["unreadable"][0]["error"] == "JSONDecodeError"
    assert report["unreadable"][0]["path"].endswith("broken/manifest.json")
    assert report["summary"]["full_calibrations"] == 1


def test_a_manifest_without_an_identity_is_counted_not_silently_skipped(
        tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", manifest={"schema_version": 1})

    report = explain(load_library(root))

    assert report["summary"]["unreadable_manifests"] == 1
    assert report["unreadable"][0]["error"] == "MissingIdentity"


@pytest.mark.parametrize(("change", "expected_error"), [
    ({"schema_version": 2}, "SchemaVersionMismatch"),
    ({"kind": "something_else"}, "KindMismatch"),
    ({"key": "different"}, "KeyMismatch"),
    ({"artifacts": []}, "InvalidArtifacts"),
])
def test_an_incomplete_manifest_is_rejected_with_a_named_reason(
        tmp_path, change, expected_error):
    root = tmp_path / "demand-days"
    identity = _identity("2027-06-03", variants=THREE,
                         composition=["weekday"])
    manifest = {
        "schema_version": 1,
        "kind": "calibrated_demand_day",
        "key": "k1",
        "identity": identity,
        "artifacts": {},
        **change,
    }
    _write_entry(root, "2027-06-03", "k1", manifest=manifest)

    report = explain(load_library(root))

    assert report["summary"]["entries"] == 0
    assert report["summary"]["unreadable_manifests"] == 1
    assert report["unreadable"][0]["error"] == expected_error


def test_an_identity_for_a_different_date_is_rejected(tmp_path):
    root = tmp_path / "demand-days"
    identity = _identity("2027-06-04", variants=THREE,
                         composition=["weekday"])
    _write_entry(root, "2027-06-03", "k1", identity=identity)

    report = explain(load_library(root))

    assert report["summary"]["entries"] == 0
    assert report["unreadable"][0]["error"] == "IdentityDateMismatch"


# --- time bounds ---------------------------------------------------------

def test_time_bounds_include_entries_written_exactly_on_the_bound(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "early", variants=THREE,
                 composition=["weekday"], written_at=100.0)
    _write_entry(root, "2027-06-03", "onbound", variants=THREE,
                 composition=["weekday"], written_at=200.0)
    _write_entry(root, "2027-06-03", "late", variants=THREE,
                 composition=["weekday"], written_at=300.0)

    scan = load_library(root, since=200.0, until=300.0)

    assert sorted(e.key for e in scan.entries) == ["late", "onbound"]


def test_iso_bounds_with_an_offset_are_parsed_to_that_instant():
    assert parse_bound("2026-09-10T18:29:15+02:00") == 1789057755.0


# --- current source matching --------------------------------------------

def test_reusability_counts_entries_the_current_sources_can_still_match():
    scan = _scan(
        _entry("2027-06-03", "k1", variants=THREE, composition=["weekday"],
               sources={"pfe": "1"}),
        _entry("2027-06-04", "k2", variants=THREE, composition=["weekday"],
               sources={"pfe": "2"}),
    )

    report = explain(scan, current_sources={"pfe": "1"})

    assert report["summary"]["reusable_entries"] == 1
    assert report["summary"]["reusable_dates"] == 1
    assert report["summary"]["stale_entries"] == 1
    assert report["dates"][0]["entries"][0]["reusable"] is True
    assert report["dates"][1]["entries"][0]["reusable"] is False


def test_reusability_is_reported_as_unknown_without_a_source_inventory():
    scan = _scan(_entry("2027-06-03", "k1", variants=THREE,
                        composition=["weekday"]))

    report = explain(scan)

    assert report["summary"]["reusable_entries"] is None
    assert report["dates"][0]["entries"][0]["reusable"] is None


# --- the read-only promise ----------------------------------------------

def _tree_state(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime,
                   p.stat().st_size)
                  for p in Path(root).rglob("*") if p.is_file())


def test_running_the_cli_leaves_the_library_untouched(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"], written_at=200.0)
    _write_entry(root, "2027-06-03", "k2", variants=ONE,
                 composition=["weekday"], constraints="c1", written_at=200.0)
    before = _tree_state(root)

    main(["--root", str(root), "--output", str(tmp_path / "out.json")])

    assert _tree_state(root) == before


def test_cli_refuses_an_output_path_inside_the_library(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"])
    before = _tree_state(root)

    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), "--output",
              str(root / "diagnostics" / "report.json")])

    assert error.value.code == 2
    assert _tree_state(root) == before


def test_the_report_declares_its_schema_and_is_not_release_evidence(tmp_path):
    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"])
    out = tmp_path / "out.json"

    main(["--root", str(root), "--output", str(out)])
    report = json.loads(out.read_text())

    assert report["schema"] == "day_reuse_explanation_v1"
    assert report["release_evidence"] is False


# --- frozen real-data baseline ------------------------------------------

JOB_ROOT = Path(__file__).resolve().parent.parent / "runs" / "demand-days"
JOB_SINCE = "2026-09-10T18:29:15+02:00"
JOB_UNTIL = "2026-09-10T19:54:32+02:00"


@pytest.mark.skipif(not JOB_ROOT.is_dir(),
                    reason="the ui-monthly-g1f50b day library is absent here")
def test_the_recorded_job_window_reproduces_the_frozen_baseline():
    before = _tree_state(JOB_ROOT)

    scan = load_library(JOB_ROOT, since=parse_bound(JOB_SINCE),
                        until=parse_bound(JOB_UNTIL))
    report = explain(scan)

    summary = report["summary"]
    if summary["full_calibrations"] == 0:
        pytest.skip("the exact historical ui-monthly-g1f50b window is absent")
    assert summary["full_calibrations"] == 49
    assert summary["dates"] == 30
    assert summary["q50_aliases"] == 49
    assert summary["unlinked_aliases"] == 0
    assert summary["repeated_dates"] == 19
    assert summary["repeated_calibrations"] == 19
    assert summary["causes"] == {"pool_composition": 19}
    assert _tree_state(JOB_ROOT) == before


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_cli_compares_against_the_real_source_inventory(tmp_path):
    """Run as a script, not in-process.

    pytest's conftest already puts the repository root on ``sys.path``, so an
    in-process call would import the inventory even when the shipped command
    cannot. Only a subprocess reproduces the condition a user actually meets.
    """
    import subprocess
    import sys

    root = tmp_path / "demand-days"
    _write_entry(root, "2027-06-03", "k1", variants=THREE,
                 composition=["weekday"])
    out = tmp_path / "out.json"

    result = subprocess.run(
        [sys.executable, "tools/explain_day_reuse.py", "--root", str(root),
         "--output", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True)

    report = json.loads(out.read_text())
    assert report["filters"]["source_inventory_error"] is None, result.stdout
    assert isinstance(report["summary"]["reusable_entries"], int)
    assert isinstance(report["summary"]["stale_entries"], int)
