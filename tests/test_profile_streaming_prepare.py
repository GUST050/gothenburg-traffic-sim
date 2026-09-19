"""The profile prepares from published ledgers, never from a parent graph.

`tools/profile_monthly_cost_ledger.py` used to call `runner.prepare(parents)`,
which rebuilds the unit set AND the reverse unit->parent graph in memory for
the whole month. Measured 2026-09-18, that held about 2.40 MB of Python heap
per daily unit and stopped the approved rebuild at 4.13 GiB without pricing a
single one of the 1,950 units.

The seam to use already exists and is already tested at the ledger layer
(`tests/test_closure_ledgers.py`): `IndependentDailyRunner.prepare_from_ledgers`
reads `units.ndjson` and `parent_units.ndjson` and keeps only the forward
parent->units mapping. What is NOT tested anywhere is the profile tool's own
use of it, which is what this file pins:

  * that the profile takes the streaming path and cannot silently fall back,
  * that the streamed population is byte-for-byte the v1 decomposition, and
  * that a damaged, incomplete or foreign ledger set refuses to run.

The population tests use the REAL frozen spec because its 1,690 parents and
1,950 units are the contract under discussion; they touch no archive and
start no process, since enumeration and unit identity are pure computation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_independent_daily import FakeDailyRunner
from traffic_sim.core import closure_calendar
from traffic_sim.core.closure_calendar import iter_closure_schedules
from traffic_sim.core.contracts import ClosureSearchSpec
from traffic_sim.simulation import closure_ledgers
from traffic_sim.simulation.closure_ledgers import (
    MANIFEST_NAME,
    PARENT_UNITS_LEDGER,
    UNITS_LEDGER,
    LedgerCorrupt,
    LedgerError,
    verify_ledgers,
)
from traffic_sim.simulation.independent_daily import (
    IndependentDailyRunner,
    decompose_schedules,
)
from tools import profile_monthly_cost_ledger as profiler

FROZEN_SPEC = Path("validation/subhour_monthly_search_profile_spec_v1.json")
EXPECTED_PARENTS = 1690
EXPECTED_UNITS = 1950


def _frozen_spec() -> ClosureSearchSpec:
    return ClosureSearchSpec.from_dict(
        json.loads(FROZEN_SPEC.read_text(encoding="utf-8")))


def _small_spec(**overrides) -> ClosureSearchSpec:
    """A handful of parents, same interday policy as the month."""
    from tests.test_closure_ledgers import _search_spec

    return _search_spec(**overrides)


def _ledgers(directory: Path, spec: ClosureSearchSpec):
    return profiler.prepared_ledgers(directory, spec)


# -- the profile takes the streaming seam ---------------------------------


class _StreamingRunner:
    def __init__(self):
        self.streamed = None
        self.materialised = None

    def prepare_from_ledgers(self, directory, parent_ids):
        self.streamed = (Path(directory), tuple(parent_ids))

    def prepare(self, schedules):
        self.materialised = tuple(schedules)


class _LegacyRunner:
    def __init__(self):
        self.materialised = None

    def prepare(self, schedules):
        self.materialised = tuple(schedules)


def test_the_profile_prepares_from_ledgers_not_from_parents(tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)
    parent_ids = tuple(
        item.schedule_id for item in iter_closure_schedules(spec))
    runner = _StreamingRunner()

    profiler.prepare_runner_from_ledgers(runner, directory, parent_ids)

    assert runner.streamed == (directory, parent_ids)
    assert runner.materialised is None, (
        "the profile must not also materialise the parent graph")


def test_a_runner_without_the_seam_is_refused_for_a_month_sized_shortlist(
        tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)
    runner = _LegacyRunner()
    parent_ids = tuple(f"parent-{n}" for n in range(EXPECTED_PARENTS))

    with pytest.raises(ValueError) as caught:
        profiler.prepare_runner_from_ledgers(runner, directory, parent_ids)

    assert "prepare_from_ledgers" in str(caught.value)
    assert runner.materialised is None, (
        "a refusal must not have materialised anything first")


def test_the_refusal_names_the_limit_it_enforces(tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)
    runner = _LegacyRunner()
    over = profiler.MATERIALISED_PARENT_LIMIT + 1

    with pytest.raises(ValueError) as caught:
        profiler.prepare_runner_from_ledgers(
            runner, directory, tuple(f"p-{n}" for n in range(over)))

    assert str(profiler.MATERIALISED_PARENT_LIMIT) in str(caught.value)


# -- the streamed population IS the v1 decomposition ----------------------


def test_a_month_sized_profile_record_must_bind_its_ledgers(tmp_path):
    """The evidence, not just the control flow, has to show it streamed."""
    from tests.test_subhour_cost_ordered_contracts import (
        _qualified_demand_manifest,
    )

    with pytest.raises(ValueError) as caught:
        profiler.profile_ledger(
            _frozen_spec(), (), object(), output_root=tmp_path / "out",
            expected_parents=EXPECTED_PARENTS,
            qualified_demand_manifest=_qualified_demand_manifest(),
            closure_ledger_binding=None)

    assert "bind the closure" in str(caught.value)


def test_the_frozen_spec_streams_1690_parents_and_1950_units(tmp_path):
    spec = _frozen_spec()
    directory = tmp_path / "ledgers"

    manifest = _ledgers(directory, spec)

    assert manifest.parent_count == EXPECTED_PARENTS
    assert manifest.unique_unit_count == EXPECTED_UNITS
    assert manifest.search_content_key == spec.content_key
    assert manifest.status == "complete"


def test_streaming_reproduces_the_materialised_decomposition_exactly(
        tmp_path):
    spec = _frozen_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)

    parents = tuple(iter_closure_schedules(spec))
    units, relationships = decompose_schedules(spec, parents)

    streamed_units = {
        str(row["unit_id"]): row
        for row in closure_ledgers.iter_unit_rows(directory)}
    streamed_edges = {
        str(row["parent_schedule_id"]): tuple(
            str(value) for value in row["unit_ids"])
        for row in closure_ledgers.iter_parent_unit_rows(directory)}

    assert set(streamed_units) == {item.unit_id for item in units}
    assert streamed_edges == relationships
    for item in units:
        row = streamed_units[item.unit_id]
        assert row["schedule"] == item.schedule.to_dict()
        assert row["identity"] == item.identity


def test_the_unit_ledger_carries_no_reverse_parent_graph(tmp_path):
    spec = _frozen_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)

    for row in closure_ledgers.iter_unit_rows(directory):
        assert "parents" not in row
        assert "parent_schedule_ids" not in row


# -- semantics the demand release and the caches depend on ----------------


def _prepared_pair(tmp_path, spec):
    """The same shortlist prepared both ways, with recording child runners."""
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)
    parents = tuple(iter_closure_schedules(spec))
    parent_ids = tuple(item.schedule_id for item in parents)

    materialised_child = FakeDailyRunner()
    materialised = IndependentDailyRunner(
        spec, daily_runner=materialised_child,
        cache_root=tmp_path / "cache-v1")
    materialised.prepare(parents)

    streamed_child = FakeDailyRunner()
    streamed = IndependentDailyRunner(
        spec, daily_runner=streamed_child,
        cache_root=tmp_path / "cache-stream")
    profiler.prepare_runner_from_ledgers(streamed, directory, parent_ids)
    return materialised, materialised_child, streamed, streamed_child, parents


def test_both_paths_hand_the_daily_runner_the_same_ordered_schedules(
        tmp_path):
    """The demand release request embeds this ORDER, so it must not move."""
    spec = _small_spec()
    _v1, v1_child, _stream, stream_child, _parents = _prepared_pair(
        tmp_path, spec)

    assert [item.to_dict() for item in stream_child.prepared] == [
        item.to_dict() for item in v1_child.prepared]


def test_daily_units_for_is_identical_for_every_parent(tmp_path):
    spec = _small_spec()
    v1, _a, stream, _b, parents = _prepared_pair(tmp_path, spec)

    for parent in parents:
        expected = v1.daily_units_for(parent)
        actual = stream.daily_units_for(parent)
        assert [unit_id for unit_id, _ in actual] == [
            unit_id for unit_id, _ in expected]
        assert [schedule.to_dict() for _, schedule in actual] == [
            schedule.to_dict() for _, schedule in expected]


def test_the_whole_provenance_record_is_identical_on_both_paths(tmp_path):
    """`provenance()` carries the daily and per-unit backend digests, which
    the daily-unit cache path is derived from."""
    spec = _small_spec()
    v1, _a, stream, _b, _parents = _prepared_pair(tmp_path, spec)

    assert dict(stream.provenance()) == dict(v1.provenance())


# -- refusals -------------------------------------------------------------


def test_ledgers_for_another_search_are_refused(tmp_path):
    """The guard is the CONTENT key, not the label.

    `search_id` is deliberately not part of `ClosureSearchSpec.content_key`,
    so renaming a search may reuse its enumeration. Changing what the search
    actually enumerates must not.
    """
    directory = tmp_path / "ledgers"
    base = _small_spec()
    _ledgers(directory, base)
    other = _small_spec(required_work_minutes=7 * 60)
    assert other.content_key != base.content_key

    with pytest.raises(LedgerError):
        _ledgers(directory, other)


def test_ledgers_enumerated_by_other_producer_sources_are_refused(tmp_path):
    """The spec key says nothing about the code that computed unit IDs.

    A run that publishes ledgers and then fails leaves them behind. If
    `daily_unit_records` or `closure_calendar` changes before the retry, the
    population is unchanged — same 1,690 parents, same row counts — while the
    identities are not, so only the producer binding can catch it.
    """
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)

    manifest_path = directory / MANIFEST_NAME
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = dict(raw["provenance"]["producer_source_manifest"])
    assert sources, "the manifest must record which sources enumerated it"
    key = sorted(sources)[0]
    sources[key] = "0" * 64
    raw["provenance"]["producer_source_manifest"] = sources
    raw["content_key"] = closure_ledgers.content_key(
        {name: value for name, value in raw.items()
         if name != "content_key"})
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(LedgerCorrupt) as caught:
        _ledgers(directory, spec)
    assert "producer sources" in str(caught.value)


def test_a_published_ledger_set_that_was_damaged_is_refused(tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    _ledgers(directory, spec)
    path = directory / UNITS_LEDGER
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(LedgerCorrupt):
        _ledgers(directory, spec)


def test_a_missing_manifest_is_rebuilt_rather_than_trusted(tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    first = _ledgers(directory, spec)
    (directory / MANIFEST_NAME).unlink()

    second = _ledgers(directory, spec)

    assert second.key == first.key, (
        "an unpublished build area is rebuilt from the same enumeration")


def test_a_complete_ledger_set_is_reused_byte_for_byte(tmp_path):
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    first = _ledgers(directory, spec)
    before = {
        name: (directory / name).read_bytes()
        for name in (UNITS_LEDGER, PARENT_UNITS_LEDGER, MANIFEST_NAME)}

    second = _ledgers(directory, spec)

    assert second.key == first.key
    assert {name: (directory / name).read_bytes() for name in before} == before


def test_an_interrupted_write_publishes_no_manifest(tmp_path, monkeypatch):
    """The interrupt must land DURING the write, not after it.

    Wrapping `write_ledgers` and raising once it returns tests nothing: the
    manifest is already published by then, so the assertion degrades to
    're-verifying a complete set'. Interrupt the enumeration instead, which
    is where a real Ctrl-C or a failing spec lands.
    """
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    real_iter = closure_calendar.iter_closure_schedules

    def stop_partway(bound_spec):
        for index, parent in enumerate(real_iter(bound_spec)):
            if index >= 3:
                raise KeyboardInterrupt("interrupted mid-enumeration")
            yield parent

    monkeypatch.setattr(closure_calendar, "iter_closure_schedules",
                        stop_partway)

    with pytest.raises(KeyboardInterrupt):
        _ledgers(directory, spec)
    monkeypatch.undo()

    assert not (directory / MANIFEST_NAME).is_file(), (
        "an interrupted enumeration must declare nothing complete")
    with pytest.raises(LedgerError):
        verify_ledgers(directory,
                       expected_search_content_key=spec.content_key)


def test_an_interrupted_write_is_rebuilt_not_resumed(tmp_path, monkeypatch):
    """What survives an interrupt is a build area, and is rebuilt whole."""
    spec = _small_spec()
    directory = tmp_path / "ledgers"
    real_iter = closure_calendar.iter_closure_schedules

    def stop_partway(bound_spec):
        for index, parent in enumerate(real_iter(bound_spec)):
            if index >= 3:
                raise KeyboardInterrupt("interrupted mid-enumeration")
            yield parent

    monkeypatch.setattr(closure_calendar, "iter_closure_schedules",
                        stop_partway)
    with pytest.raises(KeyboardInterrupt):
        _ledgers(directory, spec)
    monkeypatch.undo()

    manifest = _ledgers(directory, spec)

    assert manifest.parent_count == len(
        tuple(iter_closure_schedules(spec)))
    assert manifest.status == "complete"


# -- the profile stays process-free ---------------------------------------


def test_preparing_from_ledgers_starts_no_process(tmp_path):
    directory = tmp_path / "ledgers"
    script = (
        "import json,sys;sys.path.insert(0,'.');"
        "from pathlib import Path;"
        "from traffic_sim.core.contracts import ClosureSearchSpec;"
        "from tools import profile_monthly_cost_ledger as p;"
        f"spec=ClosureSearchSpec.from_dict(json.loads("
        f"Path({str(FROZEN_SPEC)!r}).read_text()));"
        f"m=p.prepared_ledgers(Path({str(directory)!r}), spec);"
        "print(m.parent_count, m.unique_unit_count)"
    )
    before = subprocess.run(
        ["pgrep", "-c", "-f", "sumo"], capture_output=True, text=True,
        check=False).stdout.strip()
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        check=False, cwd=Path.cwd())
    after = subprocess.run(
        ["pgrep", "-c", "-f", "sumo"], capture_output=True, text=True,
        check=False).stdout.strip()

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [str(EXPECTED_PARENTS),
                                     str(EXPECTED_UNITS)]
    assert before == after, "no SUMO process may appear while streaming"
