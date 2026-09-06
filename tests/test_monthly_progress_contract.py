"""Step 4: a long run has to say what it is doing, in one shared vocabulary.

A progress phase the UI has no label for renders as a bare "Söker", which is
worse than no progress display at all — it tells the user the search is alive
without telling them whether it is minutes or hours from finishing. So the
phase list, the search that emits it and the labels that render it are pinned
against each other here rather than being kept in sync by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traffic_sim.simulation.monthly_search import PROGRESS_PHASES
from traffic_sim.simulation.monthly_search import _runner_timing_snapshot
from traffic_sim.simulation.search_workspace import (
    create_search_workspace,
    load_search_workspace,
)

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"
INDEX_HTML = ROOT / "web" / "index.html"


def _labels() -> dict[str, str]:
    """The phase labels the UI actually ships."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("const MONTHLY_PHASE_LABELS = {")
    end = source.index("};", start)
    body = source[start + len("const MONTHLY_PHASE_LABELS = {"):end]
    labels = {}
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("//"):
            continue
        name, _, label = line.partition(":")
        labels[name.strip()] = label.strip().strip("'")
    return labels


class TestPhaseVocabulary:
    def test_the_plans_new_phases_exist(self):
        for phase in ("preflight", "cost_units", "cost_parents",
                      "health_scan", "finalists", "paused_budget"):
            assert phase in PROGRESS_PHASES, phase

    def test_every_phase_has_a_swedish_label(self):
        labels = _labels()
        missing = [phase for phase in PROGRESS_PHASES if phase not in labels]
        assert missing == [], f"the UI has no label for {missing}"

    def test_the_ui_has_no_label_for_a_phase_that_cannot_happen(self):
        labels = _labels()
        extra = [phase for phase in labels if phase not in PROGRESS_PHASES]
        assert extra == [], f"the UI labels phases the search never emits: {extra}"

    def test_the_phases_are_in_execution_order(self):
        order = list(PROGRESS_PHASES)
        assert order.index("preflight") < order.index("enumerate")
        assert order.index("enumerate") < order.index("screen")
        assert order.index("cost_units") < order.index("cost_parents")
        assert order.index("cost_parents") < order.index("pilot")
        assert order.index("pilot") < order.index("finalists")
        assert order.index("finalists") < order.index("publish")


class TestProgressDetail:
    def _spec(self):
        from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand

        return ClosureSearchSpec(
            search_id="progress-detail",
            directed_edges=("a_b_0",),
            demand_build_id="forecast-2027",
            source="forecast",
            permitted_date_start="2027-01-04",
            permitted_date_end="2027-01-08",
            required_work_minutes=8 * 60,
            max_consecutive_start_days=1,
            permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        )

    def test_detail_survives_a_manifest_round_trip(self, tmp_path):
        workspace = create_search_workspace(self._spec(), root=tmp_path)
        workspace.update_progress(
            "cost_parents",
            completed=3,
            total=10,
            detail={"costed": 3, "cost_total": 10, "cache_hits": 2,
                    "verified": 1, "cutoff": 1.25},
        )

        reloaded = load_search_workspace(workspace.directory)
        progress = reloaded.manifest["progress"]
        assert progress["phase"] == "cost_parents"
        assert progress["detail"]["cache_hits"] == 2
        assert progress["detail"]["cutoff"] == 1.25

    def test_a_non_serialisable_detail_is_refused_at_write_time(self, tmp_path):
        workspace = create_search_workspace(self._spec(), root=tmp_path)
        with pytest.raises((ValueError, TypeError)):
            workspace.update_progress("pilot", detail={"bad": {1, 2}})

    def test_detail_is_optional(self, tmp_path):
        workspace = create_search_workspace(self._spec(), root=tmp_path)
        workspace.update_progress("enumerate", completed=1, total=2)
        assert "detail" not in workspace.manifest["progress"]

    def test_a_non_object_detail_is_refused(self, tmp_path):
        workspace = create_search_workspace(self._spec(), root=tmp_path)
        with pytest.raises(ValueError, match="detail must be an object"):
            workspace.update_progress("pilot", detail=["not", "an", "object"])

    def test_optional_timing_cannot_fail_the_search(self):
        class RaisingRunner:
            @staticmethod
            def timing_snapshot():
                raise RuntimeError("diagnostic unavailable")

        class NonJsonRunner:
            @staticmethod
            def timing_snapshot():
                return {"bad": {1, 2}}

        assert _runner_timing_snapshot(RaisingRunner()) == {}
        assert _runner_timing_snapshot(NonJsonRunner()) == {}


class TestTheUiRendersTheDetail:
    def test_the_bundle_renders_every_detail_counter(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "function monthlyProgressDetail(" in source
        for token in ("kostnadsberäknade", "cacheträffar",
                      "SUMO-verifierade", "gräns", "unika dagsenheter"):
            assert token in source, token

    def test_the_detail_is_shown_beside_the_phase(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "${label}${counts}${detail}" in source

    def test_running_ui_uses_wall_time_during_backend_preparation(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "function monthlyElapsedLabel(" in source
        assert "status.wall_elapsed_s ?? status.elapsed_s" in source
        assert "${wallElapsed} totalt" in source
        assert "s aktiv tid" not in source

    def test_a_reloaded_paused_search_restores_the_exact_form(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "function restoreMonthlySearchSpec(" in source
        assert "state.status === 'paused'" in source
        assert "restoreMonthlySearchSpec(state.closure_search_spec)" in source
        assert "starta samma sökning igen" in source

    def test_an_observed_external_search_cannot_offer_server_cancel(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "let monthlyJobServerTracked = true" in source
        assert "monthlyJobServerTracked = state.server_tracked !== false" in source
        assert "monthlyJobRunning && !monthlyJobServerTracked" in source
        assert "btnMonthlyCancel.hidden = unownedMonthlyJob" in source
        assert "if (!monthlyJobServerTracked) return" in source

    def test_a_completed_result_is_restored_without_a_running_job(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "const completedMonthly = states.find" in source
        assert "state.status === 'done' && state.result" in source
        completed = source[source.index("if (completedMonthly) {"):]
        completed = completed[:completed.index("const paused =")]
        assert "monthlyJobRunning = false" in completed
        assert "monthlyJobServerTracked = state.server_tracked !== false" in completed
        assert "restoreMonthlySearchSpec(state.closure_search_spec)" in completed
        assert "state.closure_search_spec?.directed_edges" in completed
        assert "renderMonthlyResults(state.result)" in completed

    def test_period_table_separates_q50_prices_from_sumo_verification(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "Ford.timmar (q50)" in source
        assert "Beräknad med q50" in source
        assert "Bäst · SUMO-verifierad" in source
        assert "deterministic_comparison_complete" in source

    def test_period_search_has_one_unambiguous_objective(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        assert 'id="monthly-period-mode"' not in html
        assert "jämförs automatiskt" in html
        assert "objective_profile: 'closure_cost_v1'" in source
        assert "period_comparison_policy: 'rolling_period_v1'" in source
        assert "monthlyPeriodMode" not in source
        assert "monthly-tool-active" in source

    def test_period_result_is_a_full_screen_with_explicit_time_columns(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        assert "#monthly-results {" in html
        assert "position: fixed; inset: 0" in html
        assert 'id="monthly-col-start-time"' in html
        assert 'id="monthly-col-end-time"' in html
        assert 'id="monthly-results-summary"' in html
        assert 'id="monthly-results-subtitle"' in html
        assert 'id="monthly-results-notice"' in html
        assert '<details id="monthly-results-method">' in html
        assert "body.monthly-results-open #workspace-nav" in html
        assert "body.monthly-results-open #legend" in html
        assert "body.monthly-results-open .leaflet-control-container" in html
        assert html.index('<section id="monthly-results"') > html.index(
            '<div id="sliders">')
        assert "scheduleTableFields(period.best_schedule)" in source
        assert "document.body.classList.add('monthly-results-open')" in source
        assert "monthlyResultsNotice.textContent" in source
        assert "monthlyResultsMethod.open = false" in source
        assert "monthlyResults.focus()" in source

    def test_monthly_views_fit_small_screens_and_explain_whole_days(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        source = APP_JS.read_text(encoding="utf-8")
        assert 'id="monthly-full-day-value"' in html
        assert 'aria-describedby="monthly-full-day-value"' in html
        assert "monthlyFullDayValue.hidden = !whole" in source
        assert "monthlyBanner.classList.contains('show')" in source
        assert "? btnMonthlyRun : btnMonthly" in source
        assert "@media (max-width: 720px)" in html
        assert "#monthly-results-summary" in html
        assert "overflow-x: auto" in html

    def test_polling_has_bounded_failures_and_exponential_backoff(self):
        source = APP_JS.read_text(encoding="utf-8")
        poller = source[source.index(
            "async function runRoadClosureOperation("):]
        poller = poller[:poller.index("async function activateClosedScenario(")]
        assert "maxConsecutivePollFailures = 5" in poller
        assert "2 ** consecutivePollFailures" in poller
        assert "consecutivePollFailures = 0" in poller
        assert "servern svarar inte" in poller


def _job_error_catch_blocks() -> list[str]:
    """The catch blocks that end a road-closure job's status poll loop.

    Each `runRoadClosureOperation` caller converts a terminal `error` status
    into a thrown Error; the catch that receives it is the last code to run
    for that job in this tab.
    """
    source = APP_JS.read_text(encoding="utf-8")
    blocks = []
    marker = "if (status.status === 'error') throw new Error(status.error);"
    start = 0
    while True:
        hit = source.find(marker, start)
        if hit == -1:
            break
        # Road-closure jobs only. Signal optimization also polls, but its
        # failure is NOT a study outcome — the map keeps whatever scenario it
        # had either way, so announceStudyOutcome's "the map still shows the
        # previous study" wording would be actively wrong there. It needs its
        # own non-blocking panel message, which does not exist yet.
        if "runRoadClosureOperation(" not in source[max(0, hit - 1500):hit]:
            start = hit + len(marker)
            continue
        catch = source.find("} catch (e) {", hit)
        assert catch != -1, "a job error path must be caught"
        end = source.find("} finally {", catch)
        blocks.append(source[catch:end if end != -1 else catch + 600])
        start = hit + len(marker)
    return blocks


def test_a_failed_job_never_reports_through_a_blocking_alert():
    """alert() freezes the event loop, so the poll loop cannot be replaced.

    MDN documents alert() as a legacy exception to the non-blocking model: it
    holds the main thread until dismissed, starving timers and pending
    promises. Found live 2026-08-26 — a paused monthly search left this alert
    on screen and the tab still showed that failure over three hours after a
    replacement search had started and was running normally. app.js already
    owns the right mechanism (announceStudyOutcome, whose own comment says
    "a transient alert() is not a label"); this keeps every job error path on
    it.
    """
    def strip_comments(block: str) -> str:
        # A block explaining WHY it avoids alert() must not read as using it.
        return "\n".join(
            line for line in block.splitlines()
            if not line.strip().startswith("//"))

    blocks = _job_error_catch_blocks()
    assert blocks, "expected at least one road-closure job error path"
    offenders = [b for b in blocks if "alert(" in strip_comments(b)]
    assert not offenders, (
        "a job-failure path still uses a blocking alert(); use "
        f"announceStudyOutcome instead: {offenders}")
    assert all("announceStudyOutcome(" in b for b in blocks), (
        "every job-failure path must announce through the persistent banner")
