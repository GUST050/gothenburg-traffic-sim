"""Stage 3: v2 discovers its cases from the archive library, not from guesses.

v1's cases were written by hand with date ranges that mostly named dates no
archive ever held, and it was then frozen against an EMPTY runs directory — an
environment-local artifact that can no longer be corrected without editing
history. Both mistakes are structural, and both are closed here:

  * cases are built around dates the library actually contains, and around
    roads that survive their own closure;
  * a discovery that finds nothing REFUSES to freeze, instead of writing an
    empty registration that looks like evidence.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import tools.cost_ordered_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]


def _archive(root: Path, work_date: str, *, source: str = "historical",
             days: int = 1, begin: str = "00:00", end: str = "24:00",
             variants=("q10", "q50", "q90")) -> Path:
    """A calibrated archive shaped exactly like a real one's metadata."""
    directory = root / f"demand-{work_date.replace('-', '')}-{source}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "demand_meta.json").write_text(json.dumps({
        "demand_build_key": f"key-{work_date}-{source}",
        "epoch_sim": f"{work_date}T00:00:00",
        "n_intervals": 96 * days,
        "n_variants": 3,
        "demand_spec": {
            "start_date": work_date, "source": source, "days": days,
            "begin": begin, "end": end,
            "structural_reference_date": "2025-09-16",
            "purpose": "standard" if days == 1 else "closure_envelope",
        },
    }), encoding="utf-8")
    for variant in variants:
        (directory / bench.VARIANT_FILENAMES[variant]).write_text(
            f"<routes id='{work_date}-{variant}'/>", encoding="utf-8")
    return directory


def _weekday_library(root: Path, first: str, count: int, **kwargs) -> list[str]:
    made = []
    day = date.fromisoformat(first)
    while len(made) < count:
        if day.weekday() < 5:
            _archive(root, day.isoformat(), **kwargs)
            made.append(day.isoformat())
        day += timedelta(days=1)
    return made


class TestTheCalendarComesFromTheArchives:
    def test_it_reads_whole_day_three_variant_archives(self, tmp_path):
        dates = _weekday_library(tmp_path, "2025-09-01", 4)
        calendar = bench.archive_calendar(tmp_path)
        assert sorted(calendar) == sorted(dates)
        for record in calendar.values():
            assert set(record["routes"]) == {"q10", "q50", "q90"}
            assert record["source"] == "historical"

    def test_a_two_variant_archive_is_not_in_the_calendar(self, tmp_path):
        _archive(tmp_path, "2025-09-01", variants=("q10", "q50"))
        assert bench.archive_calendar(tmp_path) == {}

    def test_a_multi_day_archive_is_not_a_daily_unit(self, tmp_path):
        _archive(tmp_path, "2025-09-01", days=7)
        assert bench.archive_calendar(tmp_path) == {}, (
            "an independent daily unit resolves to a single-day archive")

    def test_a_part_day_archive_is_refused(self, tmp_path):
        _archive(tmp_path, "2025-09-01", begin="06:00", end="12:00")
        assert bench.archive_calendar(tmp_path) == {}

    def test_sources_are_kept_apart(self, tmp_path):
        _weekday_library(tmp_path, "2025-09-01", 3)
        _weekday_library(tmp_path, "2027-01-04", 3, source="forecast")
        calendar = bench.archive_calendar(tmp_path)
        sources = {record["source"] for record in calendar.values()}
        assert sources == {"historical", "forecast"}


class TestTheRoadsAreStructurallyChosen:
    def test_only_edges_that_survive_their_own_closure_are_used(self):
        roads = bench.surviving_roads()
        assert roads
        screen = json.loads(bench.SURVIVABILITY_SCREEN.read_text(
            encoding="utf-8"))
        surviving = {item["edge_id"]
                     for item in screen["candidate_pool"]["edges"]
                     if item["survives_topology"]}
        assert {item["edge_id"] for item in roads} <= surviving

    def test_v1s_road_is_excluded_because_it_severs_a_successor(self):
        """The documented single-incoming-connection case.

        A search over an edge whose closure strands a successor produces
        degenerate candidates for reasons that have nothing to do with
        execution order — which is why v1 could not discriminate.
        """
        assert "60786979_3575001205_0" not in {
            item["edge_id"] for item in bench.surviving_roads()}

    def test_the_choice_is_deterministic(self):
        assert ([item["edge_id"] for item in bench.surviving_roads()]
                == [item["edge_id"] for item in bench.surviving_roads()])


class TestDiscoveryBuildsRunnableCases:
    def test_every_discovered_window_lies_inside_the_library(self, tmp_path):
        dates = _weekday_library(tmp_path, "2025-09-01", 6)
        specs = bench.discovered_specs(tmp_path)
        assert specs
        for spec in specs:
            assert spec.permitted_date_start in dates
            assert spec.permitted_date_end in dates

    def test_several_daily_windows_are_offered_per_date_run(self, tmp_path):
        _weekday_library(tmp_path, "2025-09-01", 4)
        bands = {(spec.permitted_daily_band.earliest_start,
                  spec.permitted_daily_band.latest_end)
                 for spec in bench.discovered_specs(tmp_path)}
        assert len(bands) == len(bench.DISCOVERY_BANDS) > 1

    def test_an_empty_library_discovers_nothing(self, tmp_path):
        assert bench.discovered_specs(tmp_path) == ()

    def test_a_single_date_is_not_a_run(self, tmp_path):
        _archive(tmp_path, "2025-09-01")
        assert bench.discovered_specs(tmp_path) == ()

    def test_discovered_cases_are_structurally_eligible(self, tmp_path):
        _weekday_library(tmp_path, "2025-09-01", 6)
        selection = bench.select_case(tmp_path, from_archives=True)
        assert selection["case_source"] == "discovered_from_archive_metadata"
        assert selection["selected"] is not None
        chosen = selection["selected"]
        assert chosen["candidate_count"] >= (
            bench.MINIMUM_STRUCTURAL_CANDIDATES)
        assert chosen["work_dates_with_calibrated_archive"] == (
            chosen["work_dates"])

    def test_selection_still_consults_no_outcome(self, tmp_path, monkeypatch):
        from traffic_sim.simulation import deterministic_disruption as dd

        def refuse(*args, **kwargs):
            raise AssertionError("discovery consulted a cost or an outcome")

        monkeypatch.setattr(dd, "parent_closure_cost", refuse)
        monkeypatch.setattr(dd.ArchiveDisruptionProvider, "disruption", refuse)
        _weekday_library(tmp_path, "2025-09-01", 5)
        assert bench.select_case(tmp_path, from_archives=True)["selected"]


class TestItRefusesToFreezeAnEmptyRegistration:
    def test_discovery_with_no_archives_refuses(self, tmp_path):
        with pytest.raises(SystemExit, match="Refusing to freeze an empty"):
            bench.main(["--preregister", "--from-archives",
                        "--runs-root", str(tmp_path),
                        "--registration", str(tmp_path / "v2.json")])
        assert not (tmp_path / "v2.json").exists(), (
            "an empty registration must not reach disk at all")

    def test_discovery_with_archives_writes_a_registration(self, tmp_path):
        library = tmp_path / "runs"
        library.mkdir()
        _weekday_library(library, "2025-09-01", 6)
        destination = tmp_path / "v2.json"
        bench.main(["--preregister", "--from-archives",
                    "--runs-root", str(library),
                    "--registration", str(destination)])
        record = json.loads(destination.read_text(encoding="utf-8"))
        assert record["status"] == "frozen_before_outcome"
        assert record["selected_case"] is not None
        assert record["selection"]["case_source"] == (
            "discovered_from_archive_metadata")
        assert record["archives"], "the chosen case must bind its archives"
        assert record["release_evidence"] is False
        assert record["claim_boundary"]["activates_policy_v3"] is False
        assert bench.verify_bindings(record, library) == []

    def test_discover_then_run_is_one_unbroken_pipeline(self, tmp_path,
                                                         monkeypatch):
        """The capstone: `--preregister --from-archives` then `--run`.

        The arms are the in-memory ones (this checkout has no SUMO demand), but
        discovery, binding verification, both arms' orchestration, the
        comparison, the gates and the outcome writer are all production. On a
        host with the calibrated library the only difference is which arms run.
        """
        import tools.product_arm as pa
        from tests.test_cost_ordered_execution import (
            FakeCostSource, FakeRunner, _ordered_prices, _screen_builder)
        from traffic_sim.core.closure_calendar import generate_closure_schedules

        library = tmp_path / "runs"
        library.mkdir()
        _weekday_library(library, "2025-09-01", 6)
        registration_path = tmp_path / "v2.json"
        bench.main(["--preregister", "--from-archives",
                    "--runs-root", str(library),
                    "--registration", str(registration_path)])
        record = json.loads(registration_path.read_text(encoding="utf-8"))

        from traffic_sim.core.contracts import ClosureSearchSpec
        spec = ClosureSearchSpec.from_dict(
            {key: value for key, value in record["selected_case"]["spec"].items()
             if key != "content_key"})
        _schedules, prices = _ordered_prices(spec)
        ids = [item.schedule_id for item in generate_closure_schedules(spec)]

        def fake_build_arm(spec_arg, *, cost_ordered, objective_method, **kw):
            return (FakeRunner(prices=prices), _screen_builder(spec_arg, ids),
                    FakeCostSource(prices) if cost_ordered else None)

        monkeypatch.setattr(pa, "build_arm", fake_build_arm)
        monkeypatch.setattr(bench.pa, "build_arm", fake_build_arm)

        outcome_path = tmp_path / "outcome-v2.json"
        code = bench.main(["--run", "--registration", str(registration_path),
                           "--runs-root", str(library),
                           "--release-root", str(tmp_path / "releases"),
                           "--workspace-root", str(tmp_path / "ws"),
                           "--out", str(outcome_path)])
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        assert code == 0, outcome["gates"]["checks"]
        assert outcome["gates"]["passed"] is True
        assert outcome["comparison"]["sumo_verifications_saved"] > 0
        assert outcome["comparison"]["ledger_costs_field_identical"] is True
        assert outcome["comparison"]["restart_equivalent"] is True
        assert outcome["claim_boundary"]["activates_policy_v3"] is False
        assert registration_path.read_text(encoding="utf-8") == json.dumps(
            record, indent=1, sort_keys=True) + "\n", (
            "the run must not have edited the registration")

    def test_the_v1_registration_is_untouched_by_any_of_this(self):
        v1 = json.loads(
            (ROOT / "validation"
             / "cost_ordered_benchmark_registration_v1.json")
            .read_text(encoding="utf-8"))
        assert v1["status"] == "blocked_no_structurally_eligible_case"
        assert v1["selection"].get("case_source") is None, (
            "v1 predates discovery and must stay exactly as it was frozen")
