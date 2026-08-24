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

    def test_an_observed_external_result_is_restored_without_a_running_job(self):
        source = APP_JS.read_text(encoding="utf-8")
        assert "const completedExternal = states.find" in source
        assert "state.server_tracked === false && state.result" in source
        completed = source[source.index("if (completedExternal) {"):]
        completed = completed[:completed.index("const paused =")]
        assert "monthlyJobRunning = false" in completed
        assert "restoreMonthlySearchSpec(state.closure_search_spec)" in completed
        assert "state.closure_search_spec?.directed_edges" in completed
        assert "renderMonthlyResults(state.result)" in completed

    def test_polling_has_bounded_failures_and_exponential_backoff(self):
        source = APP_JS.read_text(encoding="utf-8")
        poller = source[source.index(
            "async function runRoadClosureOperation("):]
        poller = poller[:poller.index("async function activateClosedScenario(")]
        assert "maxConsecutivePollFailures = 5" in poller
        assert "2 ** consecutivePollFailures" in poller
        assert "consecutivePollFailures = 0" in poller
        assert "servern svarar inte" in poller
