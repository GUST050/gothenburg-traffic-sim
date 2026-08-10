"""PR C: streaming closure ledgers, their integrity contract and integration.

Two questions run through this file:

  * does the streaming path produce EXACTLY what the materialising v1 path
    produced (same parents, same order, same unit IDs, same relationships), and
  * does a damaged or half-written ledger ever get used as if it were evidence.

The second matters more than it looks. The ledgers are the only record of what
a multi-month search enumerated; a truncated file that still parses would
silently shrink the search and nothing downstream would notice.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import weakref
from datetime import date, timedelta
from pathlib import Path

import pytest

from traffic_sim.core.closure_calendar import (
    generate_closure_schedules,
    iter_closure_schedules,
)
from traffic_sim.core.contracts import (
    ClosureInterval,
    ClosureSchedule,
    ClosureSearchSpec,
    DailyTimeBand,
    _content_key,
)
from traffic_sim.simulation import closure_ledgers
from traffic_sim.simulation.closure_ledgers import (
    LEDGER_SCHEMA,
    LEDGER_VERSION,
    MANIFEST_NAME,
    PARENTS_LEDGER,
    PARENT_UNITS_LEDGER,
    TEMP_SUFFIX,
    UNITS_LEDGER,
    LedgerCorrupt,
    LedgerError,
    LedgerIncomplete,
    ParentLedgerIndex,
    read_manifest,
    verify_ledgers,
    write_ledgers,
)
from traffic_sim.simulation.closure_preflight import preflight
from traffic_sim.simulation.independent_daily import (
    IndependentDailyRunner,
    daily_unit_records,
    decompose_schedules,
)
from traffic_sim.simulation.monthly_search import (
    LEDGER_ARTIFACT_KIND,
    LEDGER_DIRNAME,
    LEDGER_MANIFEST_ARTIFACT,
    MATERIALISED_SHORTLIST_LIMIT,
    SCHEMA_VERSION,
    run_monthly_search,
)
from traffic_sim.simulation.search_workspace import (
    load_search_workspace,
    open_search_workspace,
)

from tests.test_independent_daily import FakeDailyRunner


ROOT = Path(__file__).resolve().parents[1]


def _spec(**overrides) -> ClosureSearchSpec:
    """An independent-daily search small enough to enumerate by hand."""
    values = {
        "search_id": "ledger-test",
        "directed_edges": ("a_b_0",),
        "demand_build_id": "forecast-2027",
        "source": "forecast",
        "permitted_date_start": "2027-01-04",
        "permitted_date_end": "2027-01-15",
        "required_work_minutes": 8 * 60,
        "max_consecutive_start_days": 3,
        "permitted_daily_band": DailyTimeBand("06:00", "18:00"),
        "allowed_weekdays": (0, 1, 2, 3, 4),
        "interday_policy": "independent_daily_reset_v1",
        "work_allocation_policy": "exact_equal_daily_v1",
        "period_comparison_policy": "rolling_period_v1",
    }
    values.update(overrides)
    return ClosureSearchSpec(**values)


def _write(directory: Path, spec: ClosureSearchSpec, **kwargs):
    return write_ledgers(
        directory,
        spec,
        iter_closure_schedules(spec),
        unit_records=daily_unit_records,
        provenance={"search_id": spec.search_id},
        **kwargs,
    )


def _retune_manifest(directory: Path, mutate) -> None:
    """Rewrite the manifest with a recomputed content key.

    Without recomputing the key, every tampering test would only prove that
    the key check works. Re-keying forces each check (rows, bytes, digest) to
    be exercised on its own.
    """
    path = directory / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    payload.pop("content_key", None)
    payload["content_key"] = closure_ledgers.content_key(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


# --------------------------------------------------------------------------
# Exactness: the streaming ledgers must reproduce v1 to the byte.
# --------------------------------------------------------------------------


def test_ledgers_reproduce_the_v1_decomposition_exactly(tmp_path):
    spec = _spec()
    expected = generate_closure_schedules(spec)
    units, parents = decompose_schedules(spec, expected)

    manifest = _write(tmp_path / "ledgers", spec)
    directory = tmp_path / "ledgers"

    assert manifest.parent_count == len(expected)
    assert manifest.unique_unit_count == len(units)
    assert manifest.parent_unit_edge_count == sum(
        len(value) for value in parents.values()
    )

    streamed = list(closure_ledgers.iter_parent_schedules(directory))
    assert streamed == list(expected)
    assert [item.schedule_id for item in streamed] == [
        item.schedule_id for item in expected
    ]
    # Byte equality, not object equality: the ledger row IS the serialisation
    # any later reader will parse.
    assert [json.loads(line) for line in
            (directory / PARENTS_LEDGER).read_text().splitlines()] == [
        item.to_dict() for item in expected
    ]

    streamed_units = list(closure_ledgers.iter_unit_rows(directory))
    assert {row["unit_id"] for row in streamed_units} == {
        item.unit_id for item in units
    }
    by_id = {item.unit_id: item for item in units}
    for row in streamed_units:
        assert row["identity"] == dict(by_id[row["unit_id"]].identity)
        assert row["schedule"] == by_id[row["unit_id"]].schedule.to_dict()

    relationships = {
        row["parent_schedule_id"]: tuple(row["unit_ids"])
        for row in closure_ledgers.iter_parent_unit_rows(directory)
    }
    assert relationships == parents


def test_unit_rows_carry_no_reverse_parent_graph(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)

    rows = list(closure_ledgers.iter_unit_rows(tmp_path))
    assert rows
    for row in rows:
        assert "parent_schedule_ids" not in row
        assert set(row) == {"unit_id", "identity", "schedule"}

    # The forward relationship still exists — exactly once, in its own ledger.
    shared = [
        row for row in closure_ledgers.iter_parent_unit_rows(tmp_path)
    ]
    seen: dict[str, int] = {}
    for row in shared:
        for unit_id in row["unit_ids"]:
            seen[unit_id] = seen.get(unit_id, 0) + 1
    assert max(seen.values()) > 1, "fixture must share units between parents"


def test_preflight_counts_equal_the_streamed_ledger_counts(tmp_path):
    spec = _spec()
    manifest = _write(tmp_path, spec)
    report = preflight(spec)

    assert report.parent_schedule_count == manifest.parent_count
    assert report.unique_daily_unit_count == manifest.unique_unit_count


def test_ledger_bytes_do_not_depend_on_hash_randomisation(tmp_path):
    """Set/dict iteration order must not reach the published bytes.

    Run in real child interpreters with different PYTHONHASHSEED values: an
    in-process test cannot change string hashing after start-up, so it could
    not detect the fault it is checking for.
    """
    spec = _spec()
    program = (
        "import json,sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "from traffic_sim.core.contracts import ClosureSearchSpec;"
        "from traffic_sim.core.closure_calendar import iter_closure_schedules;"
        "from traffic_sim.simulation.closure_ledgers import write_ledgers;"
        "from traffic_sim.simulation.independent_daily import "
        "daily_unit_records;"
        "spec=ClosureSearchSpec.from_dict(json.loads(sys.argv[2]));"
        "m=write_ledgers(sys.argv[3], spec, iter_closure_schedules(spec),"
        " unit_records=daily_unit_records, provenance={'k': 1});"
        "print(json.dumps({name: item.sha256 for name, item"
        " in m.files.items()}))"
    )
    digests = []
    for index, seed in enumerate(("0", "1", "987654321")):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        target = tmp_path / f"seed-{index}"
        completed = subprocess.run(
            [sys.executable, "-c", program, str(ROOT),
             json.dumps(spec.to_dict()), str(target)],
            capture_output=True, text=True, check=True, env=environment,
            cwd=ROOT,
        )
        digests.append(json.loads(completed.stdout.strip().splitlines()[-1]))

    assert digests[0] == digests[1] == digests[2]


# --------------------------------------------------------------------------
# Publication and the two failure modes.
# --------------------------------------------------------------------------


def test_publication_leaves_no_temporary_file_behind(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)

    assert sorted(item.name for item in tmp_path.iterdir()) == sorted([
        MANIFEST_NAME, PARENTS_LEDGER, PARENT_UNITS_LEDGER, UNITS_LEDGER,
    ])
    assert not list(tmp_path.glob(f"*{TEMP_SUFFIX}"))


def test_interrupted_write_publishes_nothing_and_is_never_resumed(tmp_path):
    spec = _spec()

    def explode(spec_, parent):
        records = daily_unit_records(spec_, parent)
        explode.seen += 1
        if explode.seen > 2:
            raise KeyboardInterrupt("simulated interruption")
        return records

    explode.seen = 0

    with pytest.raises(KeyboardInterrupt):
        write_ledgers(
            tmp_path, spec, iter_closure_schedules(spec),
            unit_records=explode, provenance={"k": 1},
        )

    # No manifest, no published ledger, and no half-written temporary file.
    assert read_manifest(tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(LedgerIncomplete):
        verify_ledgers(tmp_path)


def test_an_interrupted_rewrite_withdraws_the_old_completion_signal(tmp_path):
    """A build in progress must claim nothing, even over a finished set."""
    spec = _spec()
    _write(tmp_path, spec)
    assert read_manifest(tmp_path) is not None

    def explode(spec_, parent):
        explode.seen += 1
        if explode.seen > 2:
            raise KeyboardInterrupt("simulated interruption")
        return daily_unit_records(spec_, parent)

    explode.seen = 0
    with pytest.raises(KeyboardInterrupt):
        write_ledgers(tmp_path, spec, iter_closure_schedules(spec),
                      unit_records=explode, provenance={"k": 1})

    assert read_manifest(tmp_path) is None
    with pytest.raises(LedgerIncomplete):
        verify_ledgers(tmp_path)
    assert not list(tmp_path.glob(f"*{TEMP_SUFFIX}"))


def test_missing_manifest_fails_closed_even_with_complete_ledgers(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)
    (tmp_path / MANIFEST_NAME).unlink()

    assert read_manifest(tmp_path) is None
    with pytest.raises(LedgerIncomplete, match="never"):
        verify_ledgers(tmp_path)


def test_missing_ledger_under_a_manifest_is_corruption_not_incompleteness(
    tmp_path,
):
    spec = _spec()
    _write(tmp_path, spec)
    (tmp_path / UNITS_LEDGER).unlink()

    with pytest.raises(LedgerCorrupt, match="missing"):
        verify_ledgers(tmp_path)


def test_truncated_ledger_fails_closed(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)
    path = tmp_path / PARENTS_LEDGER
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")

    with pytest.raises(LedgerCorrupt, match="bytes"):
        verify_ledgers(tmp_path)


def test_row_count_mismatch_fails_closed(tmp_path):
    spec = _spec()
    manifest = _write(tmp_path, spec)

    _retune_manifest(
        tmp_path,
        lambda payload: payload["files"][PARENTS_LEDGER].__setitem__(
            "rows", manifest.parent_count - 1),
    )

    with pytest.raises(LedgerCorrupt, match="rows"):
        verify_ledgers(tmp_path)


def test_digest_mismatch_of_equal_length_content_fails_closed(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)
    path = tmp_path / PARENTS_LEDGER
    raw = path.read_bytes()
    # Same length, different content: only the digest can catch this.
    swapped = raw.replace(b"closure-", b"closurE-", 1)
    assert len(swapped) == len(raw) and swapped != raw
    path.write_bytes(swapped)

    with pytest.raises(LedgerCorrupt, match="digest"):
        verify_ledgers(tmp_path)


def test_malformed_ndjson_row_fails_closed_on_read(tmp_path):
    (tmp_path / PARENT_UNITS_LEDGER).write_text(
        '{"parent_schedule_id":"a","unit_ids":["u"]}\nnot json\n',
        encoding="utf-8")

    rows = closure_ledgers.iter_parent_unit_rows(tmp_path)
    assert next(rows)["parent_schedule_id"] == "a"
    with pytest.raises(LedgerCorrupt, match="not valid JSON"):
        next(rows)


def test_parent_unit_row_without_its_relationship_fails_closed(tmp_path):
    (tmp_path / PARENT_UNITS_LEDGER).write_text(
        '{"parent_schedule_id":"a"}\n', encoding="utf-8")

    with pytest.raises(LedgerCorrupt, match="parent_schedule_id and unit_ids"):
        list(closure_ledgers.iter_parent_unit_rows(tmp_path))


def test_manifest_body_tampering_fails_its_own_content_key(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)
    path = tmp_path / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["counts"]["parent_schedules"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LedgerCorrupt, match="content key"):
        verify_ledgers(tmp_path)


def test_ledgers_belonging_to_another_search_fail_closed(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)

    with pytest.raises(LedgerCorrupt, match="another search"):
        verify_ledgers(tmp_path, expected_search_content_key="0" * 20)


def test_unsupported_ledger_schema_fails_closed(tmp_path):
    spec = _spec()
    _write(tmp_path, spec)
    _retune_manifest(tmp_path, lambda payload: payload.__setitem__(
        "version", LEDGER_VERSION + 1))

    with pytest.raises(LedgerCorrupt, match=LEDGER_SCHEMA):
        verify_ledgers(tmp_path)


def test_schedules_from_another_search_are_refused_while_writing(tmp_path):
    spec = _spec()
    # The search_id is a label and does not enter the content key; a different
    # INTENT does.
    other = generate_closure_schedules(
        _spec(search_id="somewhere-else", required_work_minutes=4 * 60))

    with pytest.raises(LedgerError, match="does not belong"):
        write_ledgers(tmp_path, spec, other, unit_records=daily_unit_records,
                      provenance={"k": 1})
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# Reading without materialising.
# --------------------------------------------------------------------------


def test_parent_index_reads_any_parent_without_holding_them(tmp_path):
    spec = _spec()
    expected = generate_closure_schedules(spec)
    _write(tmp_path, spec)

    index = ParentLedgerIndex(tmp_path)
    assert len(index) == len(expected)
    assert index.ids() == tuple(item.schedule_id for item in expected)
    assert expected[0].schedule_id in index
    assert "closure-nope" not in index
    assert index[expected[-1].schedule_id] == expected[-1]
    with pytest.raises(KeyError):
        index["closure-nope"]


def _synthetic_parents(spec: ClosureSearchSpec, dates: int, starts: int,
                       sink: list):
    """Yield `dates * starts` valid one-day parents, tracked by weak reference.

    The parents are real contract objects — content-addressed IDs and all — so
    this exercises the same code an enumerated search would, at a size no
    fixture could reach honestly.

    Weak references are the measurement: after the writer returns, any parent
    it retained is still reachable through them.  "The object graph was not
    materialised" then becomes a fact about the objects rather than an
    assertion about peak memory, which varies by machine and interpreter.
    """
    first = date(2027, 1, 4)
    for day in range(dates):
        work_date = (first + timedelta(days=day)).isoformat()
        for slot in range(starts):
            start_minutes = slot * 15
            daily_start = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
            end_minutes = start_minutes + 480
            daily_end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
            interval = ClosureInterval(
                work_date=work_date,
                start_time=f"{work_date}T{daily_start}:00",
                end_time=f"{work_date}T{daily_end}:00",
            )
            identity = {
                "search_content_key": spec.content_key,
                "first_work_date": work_date,
                "day_count": 1,
                "daily_start": daily_start,
                "daily_end": daily_end,
                "scheduled_work_minutes": 480,
                "allocation_policy": "exact_equal_daily_v1",
                "interval_durations_minutes": [480],
                "work_dates": [work_date],
            }
            schedule = ClosureSchedule(
                schedule_id="closure-" + _content_key(identity),
                search_content_key=spec.content_key,
                first_work_date=work_date,
                day_count=1,
                daily_start=daily_start,
                daily_end=daily_end,
                required_work_minutes=480,
                scheduled_work_minutes=480,
                actual_closed_minutes=480,
                rounding_overshoot_minutes=0,
                intervals=(interval,),
                allocation_policy="exact_equal_daily_v1",
            )
            sink.append(weakref.ref(schedule))
            yield schedule


def test_hundred_thousand_parent_search_never_builds_the_object_graph(tmp_path):
    spec = _spec()
    references: list = []
    dates, starts = 2000, 50
    count = dates * starts
    assert count == 100_000

    manifest = write_ledgers(
        tmp_path, spec, _synthetic_parents(spec, dates, starts, references),
        unit_records=daily_unit_records, provenance={"k": 1},
    )

    assert manifest.parent_count == count
    assert manifest.unique_unit_count == count
    assert manifest.parent_unit_edge_count == count
    row = next(closure_ledgers.iter_unit_rows(tmp_path))
    assert "parent_schedule_ids" not in row

    alive = [item for item in references if item() is not None]
    assert len(references) == count
    assert len(alive) <= 2, (
        f"{len(alive)} parent schedules are still reachable after the write; "
        "the streaming path must not retain the population")


# --------------------------------------------------------------------------
# Monthly-search integration.
# --------------------------------------------------------------------------


def _search_spec(**overrides) -> ClosureSearchSpec:
    values = {
        "search_id": "ledger-integration",
        "permitted_date_start": "2027-01-04",
        "permitted_date_end": "2027-01-08",
        "required_work_minutes": 8 * 60,
        "max_consecutive_start_days": 2,
        "permitted_daily_band": DailyTimeBand("06:00", "18:00"),
        "work_allocation_policy": "exact_equal_daily_v1",
    }
    values.update(overrides)
    return _spec(**values)


def _screen_builder(spec: ClosureSearchSpec, limit: int | None = None):
    def build(path):
        ids = [
            item.schedule_id for item in generate_closure_schedules(spec)
        ][:limit]
        return {
            "schema_version": 1,
            "kind": "monthly_closure_proxy_screening",
            "proxy_version": "independent_daily_exhaustive_sumo_v1",
            "search": spec.to_dict(),
            "candidate_count": len(ids),
            "scoreable_candidate_count": len(ids),
            "unavailable_candidates": [],
            "ranked_candidates": [],
            "shortlist": {
                "version": "independent_daily_exhaustive_sumo_v1",
                "selection_complete": True,
                "entries": [
                    {"schedule_id": value, "selection_reasons": ["exhaustive"],
                     "proxy_rank": None}
                    for value in ids
                ],
            },
        }

    return build


def _run(spec, root, runner=None, **kwargs):
    from tests.test_monthly_search import _policy

    runner = runner or IndependentDailyRunner(
        spec,
        daily_runner=FakeDailyRunner(),
        cache_root=root / "daily-cache",
    )
    return run_monthly_search(
        spec,
        _policy(),
        runner=runner,
        screen_builder=_screen_builder(spec, **kwargs),
        root=root / "search",
    )


def test_new_workspace_publishes_streaming_ledgers_and_declares_the_schema(
    tmp_path,
):
    spec = _search_spec()
    _run(spec, tmp_path)

    workspace = load_search_workspace(tmp_path / "search" / spec.search_id)
    records = [
        item for item in workspace.manifest["artifacts"]
        if item["kind"] == LEDGER_ARTIFACT_KIND
    ]
    assert len(records) == 1
    provenance = records[0]["provenance"]
    assert provenance["ledger_schema"] == LEDGER_SCHEMA
    assert provenance["ledger_version"] == LEDGER_VERSION
    assert provenance["ledger_directory"] == LEDGER_DIRNAME
    assert records[0]["path"].endswith(LEDGER_MANIFEST_ARTIFACT)

    directory = workspace.directory / LEDGER_DIRNAME
    manifest = verify_ledgers(
        directory, expected_search_content_key=spec.content_key)
    assert manifest.parent_count == len(generate_closure_schedules(spec))
    assert provenance["ledger_manifest_content_key"] == manifest.key
    assert not list(directory.glob(f"*{TEMP_SUFFIX}"))
    # The pre-PR-C whole-population artifact is not written any more.
    assert not (workspace.artifacts_dir / "candidate-ledger.json").exists()


def test_independent_runner_is_prepared_from_the_ledgers(tmp_path):
    spec = _search_spec()
    child = FakeDailyRunner()
    runner = IndependentDailyRunner(
        spec, daily_runner=child, cache_root=tmp_path / "daily-cache")
    calls: list = []
    original = runner.prepare_from_ledgers

    def record(directory, ids):
        calls.append((Path(directory), tuple(ids)))
        return original(directory, ids)

    runner.prepare_from_ledgers = record

    def refuse(_schedules):
        raise AssertionError("materialising prepare must not be used")

    runner.prepare = refuse
    _run(spec, tmp_path, runner=runner)

    assert len(calls) == 1
    assert calls[0][0].name == LEDGER_DIRNAME
    assert calls[0][1] == tuple(
        item.schedule_id for item in generate_closure_schedules(spec)
    )


def test_restart_reuses_the_published_ledgers_without_re_enumerating(tmp_path):
    spec = _search_spec()
    first = _run(spec, tmp_path)

    directory = tmp_path / "search" / spec.search_id / LEDGER_DIRNAME
    before = {
        name: (directory / name).read_bytes()
        for name in (PARENTS_LEDGER, UNITS_LEDGER, PARENT_UNITS_LEDGER,
                     MANIFEST_NAME)
    }
    second = _run(spec, tmp_path)
    after = {name: (directory / name).read_bytes() for name in before}

    assert second == first
    assert after == before
    workspace = load_search_workspace(tmp_path / "search" / spec.search_id)
    kinds = [item["kind"] for item in workspace.manifest["artifacts"]]
    assert kinds.count(LEDGER_ARTIFACT_KIND) == 1


def test_published_ledger_corruption_fails_closed_on_restart(tmp_path):
    spec = _search_spec()
    _run(spec, tmp_path)

    directory = tmp_path / "search" / spec.search_id / LEDGER_DIRNAME
    path = directory / PARENTS_LEDGER
    path.write_bytes(path.read_bytes().replace(b"closure-", b"closurE-", 1))

    with pytest.raises(LedgerCorrupt):
        _run(spec, tmp_path)


def test_unpublished_ledger_directory_is_rebuilt_not_trusted(tmp_path):
    """A build area is rebuilt; only publication freezes it.

    The distinction is the whole point of publishing the manifest last: before
    the workspace records it, nothing has claimed the directory is finished.
    """
    spec = _search_spec()
    workspace, _ = open_search_workspace(spec, root=tmp_path / "search")
    directory = workspace.directory / LEDGER_DIRNAME
    directory.mkdir()
    for name in (PARENTS_LEDGER, UNITS_LEDGER, PARENT_UNITS_LEDGER):
        (directory / name).write_text("junk\n", encoding="utf-8")
    (directory / (PARENTS_LEDGER + TEMP_SUFFIX)).write_text(
        "older junk\n", encoding="utf-8")

    _run(spec, tmp_path)

    manifest = verify_ledgers(
        directory, expected_search_content_key=spec.content_key)
    assert manifest.parent_count == len(generate_closure_schedules(spec))
    assert not list(directory.glob(f"*{TEMP_SUFFIX}"))


def test_pre_pr_c_workspace_ledger_is_still_read_and_resumed(tmp_path):
    """An old workspace keeps its materialised ledger and never gets a new one."""
    spec = _search_spec()
    workspace, _ = open_search_workspace(spec, root=tmp_path / "search")
    schedules = generate_closure_schedules(spec)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "closure_schedule_ledger",
        "search_content_key": spec.content_key,
        "candidate_count": len(schedules),
        "schedules": [item.to_dict() for item in schedules],
    }
    scratch = workspace.scratch_dir / "candidate-ledger.json"
    scratch.write_text(json.dumps(payload, indent=2, sort_keys=True),
                       encoding="utf-8")
    workspace.publish_artifact(
        scratch, "candidate-ledger.json",
        kind="closure_schedule_ledger",
        provenance={"search_content_key": spec.content_key,
                    "candidate_count": len(schedules)},
    )
    scratch.unlink()

    result = _run(spec, tmp_path)

    assert result["status"] in {"unique_winner", "inconclusive", "no_viable"}
    reloaded = load_search_workspace(tmp_path / "search" / spec.search_id)
    kinds = [item["kind"] for item in reloaded.manifest["artifacts"]]
    assert kinds.count("closure_schedule_ledger") == 1
    assert LEDGER_ARTIFACT_KIND not in kinds
    assert not (reloaded.directory / LEDGER_DIRNAME).exists()


def test_v1_daily_unit_cache_entries_are_reused_by_the_streaming_path(tmp_path):
    """The streaming unit IDs must hit cache entries written by v1.

    `decompose_schedules` and the ledger writer both go through
    `daily_unit_records`; if that ever forks into two implementations, a
    streamed search would silently re-simulate everything it had already run.
    """
    spec = _search_spec()
    cache = tmp_path / "daily-cache"

    v1_child = FakeDailyRunner()
    v1_runner = IndependentDailyRunner(
        spec, daily_runner=v1_child, cache_root=cache)
    v1_runner.prepare(generate_closure_schedules(spec))
    targets = {"q10": 1, "q50": 1, "q90": 1}
    for schedule in generate_closure_schedules(spec):
        v1_runner.run_candidate(schedule, target_repetitions=targets,
                                existing=None, stage="pilot")
    assert v1_child.calls

    _write(tmp_path / "ledgers", spec)
    streamed_child = FakeDailyRunner()
    streamed = IndependentDailyRunner(
        spec, daily_runner=streamed_child, cache_root=cache)
    streamed.prepare_from_ledgers(
        tmp_path / "ledgers",
        [item.schedule_id for item in generate_closure_schedules(spec)],
    )
    for schedule in generate_closure_schedules(spec):
        streamed.run_candidate(schedule, target_repetitions=targets,
                               existing=None, stage="pilot")

    assert streamed_child.calls == [], (
        "the streaming path re-simulated units the v1 path had already cached")


def test_large_shortlist_without_a_streaming_backend_is_refused(tmp_path):
    from traffic_sim.simulation.monthly_search import _prepare_shortlist

    class OldRunner:
        def __init__(self):
            self.prepared = None

        def prepare(self, schedules):
            self.prepared = tuple(schedules)

    spec = _search_spec()
    directory = tmp_path / "ledgers"
    _write(directory, spec)
    from traffic_sim.simulation.monthly_search import _StreamingCandidates

    candidates = _StreamingCandidates(
        directory, verify_ledgers(directory))
    ids = list(candidates)
    runner = OldRunner()

    _prepare_shortlist(runner, candidates, ids)
    assert runner.prepared is not None and len(runner.prepared) == len(ids)

    with pytest.raises(ValueError, match="materialising compatibility limit"):
        _prepare_shortlist(
            runner, candidates, ids * (MATERIALISED_SHORTLIST_LIMIT + 1))


def test_membership_never_parses_a_row(tmp_path):
    """Screening asks "do you know this ID" once per shortlisted schedule.

    `Mapping.__contains__` would answer by calling `__getitem__`, so on an
    exhaustive search the membership check alone would parse every parent —
    the population the streaming path exists not to hold.
    """
    from traffic_sim.simulation.monthly_search import _StreamingCandidates

    spec = _search_spec()
    directory = tmp_path / "ledgers"
    _write(directory, spec)
    candidates = _StreamingCandidates(directory, verify_ledgers(directory))
    known = next(iter(candidates))

    reads: list[str] = []
    original = type(candidates).__getitem__
    try:
        type(candidates).__getitem__ = lambda self, key: (
            reads.append(key) or original(self, key))
        assert known in candidates
        assert "closure-nope" not in candidates
    finally:
        type(candidates).__getitem__ = original
    assert reads == []


def test_a_pre_pr_c_workspace_is_exempt_from_the_materialising_limit(tmp_path):
    """An old workspace already holds every parent; refusing costs nothing.

    The limit exists to stop a STREAMING search from quietly reverting to the
    object graph. A v1 ledger has no streaming representation to revert from.
    """
    from traffic_sim.simulation.monthly_search import (
        _MaterialisedCandidates,
        _prepare_shortlist,
    )

    class OldRunner:
        def __init__(self):
            self.prepared = None

        def prepare(self, schedules):
            self.prepared = tuple(schedules)

    spec = _search_spec()
    schedules = generate_closure_schedules(spec)
    candidates = _MaterialisedCandidates(schedules)
    assert candidates.ledger_directory is None
    ids = [item.schedule_id for item in schedules]
    oversized = ids * (MATERIALISED_SHORTLIST_LIMIT + 1)

    runner = OldRunner()
    _prepare_shortlist(runner, candidates, oversized)
    assert len(runner.prepared) == len(oversized)
