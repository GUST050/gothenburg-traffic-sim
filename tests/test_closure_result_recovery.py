"""A finished road-closure job has to reach the operator, not just the disk.

Every road-closing endpoint is start-plus-poll: the job outlives the request
that began it and the tab that watched it.  The server therefore keeps its
terminal state in memory — but the load-time reattach path read one back only
for a monthly search this server had NOT run (``server_tracked === false``).
Every other finished job was dropped without a word.

Measured on the operator's machine 2026-09-10: /api/monthly_search/status held
``status: "done"`` with a complete result for ui-monthly-97e768 that the UI had
never shown.  A finished ``simulate`` job had the same hole — its scenario sat
published in index.json while the map still showed the previous study.  That is
CLAUDE.md's 2026-07-06 recalibration incident in three more endpoints, and it
gets the same fix, including the sessionStorage scoping that incident's own
follow-up had to add.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"

KINDS = ("simulate", "suggest", "monthly")


def _source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _poller(source: str) -> str:
    start = source.index("async function runRoadClosureOperation(")
    return source[start:source.index("async function activateClosedScenario(", start)]


def _no_active_branch(source: str) -> str:
    """The load-time branch that runs when no job is still in flight."""
    start = source.index("const active = states.find(([, state]) =>")
    return source[start:source.index("const [kind, state] = active;", start)]


def _recovery_block(source: str) -> str:
    branch = _no_active_branch(source)
    start = branch.index("const finished = states.find")
    return branch[start:branch.index("const completedExternal =", start)]


def _dispatcher(source: str) -> str:
    start = source.index("async function recoverFinishedRoadClosureJob(")
    return source[start:source.index("// Recover whichever road-closing operation", start)]


class TestPendingJobMarker:
    def test_the_marker_is_session_scoped_like_the_recalibration_one(self):
        source = _source()
        assert "function pendingJobKey(kind) { return `pendingClosureJob:${kind}`; }" in source
        assert "sessionStorage.setItem(pendingJobKey(kind)" in source
        assert "sessionStorage.removeItem(pendingJobKey(kind))" in source

    def test_each_operation_gets_its_own_marker(self):
        """One shared key would let a finished monthly search be recovered as
        a closure simulation, or hide one behind the other."""
        source = _source()
        assert "`pendingClosureJob:${kind}`" in source

    def test_an_unavailable_session_store_degrades_instead_of_throwing(self):
        source = _source()
        remember = source[source.index("function rememberPendingJob("):]
        remember = remember[:remember.index("function forgetPendingJob(")]
        assert "try {" in remember and "catch (_)" in remember

    def test_a_missing_identity_never_writes_a_marker(self):
        """A marker with no identity would match the next finished job of that
        kind, whoever started it."""
        source = _source()
        remember = source[source.index("function rememberPendingJob("):]
        remember = remember[:remember.index("function forgetPendingJob(")]
        assert "if (!id) return;" in remember

    def test_the_match_requires_an_identity_on_both_sides(self):
        source = _source()
        matches = source[source.index("function pendingJobMatches("):]
        matches = matches[:matches.index("\n        }\n", matches.index("return"))]
        assert "ROAD_CLOSURE_OPERATIONS[kind]?.stateIdentity(state)" in matches
        assert "!!pending && !!id && pending.id === id" in matches


class TestJobIdentity:
    @pytest.mark.parametrize("kind", KINDS)
    def test_every_operation_declares_both_ends_of_its_identity(self, kind):
        source = _source()
        table = source[source.index("const ROAD_CLOSURE_OPERATIONS = {"):]
        table = table[:table.index("\n        };")]
        entry = table[table.index(f"{kind}: {{"):]
        entry = entry[:entry.index("},")]
        assert "requestIdentity:" in entry, kind
        assert "stateIdentity:" in entry, kind

    def test_the_monthly_identity_reads_the_field_the_server_echoes(self):
        """Measured live: /api/monthly_search/status carries search_id at the
        top level, with the spec's copy as the fallback."""
        source = _source()
        assert "state => state?.search_id" in source
        assert "state?.closure_search_spec?.search_id" in source

    def test_the_scenario_identity_is_shared_by_the_two_that_send_one(self):
        source = _source()
        assert "const scenarioSpecIdentity = payload => payload?.scenario_spec?.scenario_id" in source


class TestMarkerLifecycle:
    def test_the_marker_is_written_before_the_start_response_is_read(self):
        """A dropped 202 is exactly the case the marker recovers, so it cannot
        wait for the response to come back."""
        poller = _poller(_source())
        assert (poller.index("rememberPendingJob(kind, operation.requestIdentity(requestBody))")
                < poller.index("const response = await fetch(operation.startUrl"))

    def test_the_marker_covers_every_kind_not_just_the_simulation(self):
        poller = _poller(_source())
        assert "kind === 'simulate'" not in poller
        assert "rememberPendingJob(kind," in poller
        assert "forgetPendingJob(kind)" in poller

    def test_a_tab_that_saw_the_outcome_clears_the_marker(self):
        poller = _poller(_source())
        assert poller.index("forgetPendingJob(kind)") < poller.index("return status;")


class TestFinishedJobRecovery:
    def test_a_finished_job_of_any_kind_is_recovered_on_load(self):
        block = _recovery_block(_source())
        for status in ("'done'", "'error'", "'cancelled'"):
            assert f"state.status === {status}" in block, status
        assert "recoverFinishedRoadClosureJob(kind, state)" in block

    def test_the_recovery_never_fires_for_a_visitor_who_started_nothing(self):
        """A status endpoint stays "done" forever — it holds the last completed
        job, not this tab's.  Applying it unconditionally would drop every
        future fresh page load into a stale result, the bug the equivalent
        recalibration recovery already had to fix once."""
        block = _recovery_block(_source())
        find = block[:block.index("if (finished) {")]
        assert "pendingJobMatches(kind, state)" in find

    def test_the_recovery_consumes_the_marker_exactly_once(self):
        block = _recovery_block(_source())
        assert block.count("forgetPendingJob(kind)") == 1
        assert block.index("forgetPendingJob(kind)") < block.index(
            "recoverFinishedRoadClosureJob(kind, state)")

    def test_an_externally_launched_search_is_still_recovered_separately(self):
        """The adopted-search branch answers a different question — a job this
        tab never started — and must survive this one."""
        branch = _no_active_branch(_source())
        assert "const completedExternal = states.find" in branch
        assert branch.index("const finished = states.find") < branch.index(
            "const completedExternal = states.find")


class TestRecoveryDispatch:
    def test_a_failure_or_cancellation_is_announced_rather_than_dropped(self):
        dispatcher = _dispatcher(_source())
        head = dispatcher[:dispatcher.index("await openWorkspace('closure');")]
        assert "state.status !== 'done'" in head
        assert "state.status === 'cancelled' ? 'cancelled' : 'error'" in head
        assert "state.error" in head

    @pytest.mark.parametrize("kind,label", [
        ("simulate", "Avstängningssimuleringen"),
        ("suggest", "Tidsoptimeringen"),
        ("monthly", "Sökningen efter arbetsperiod"),
    ])
    def test_every_kind_is_named_the_way_its_live_path_names_it(self, kind, label):
        source = _source()
        labels = source[source.index("const FINISHED_JOB_LABELS = {"):]
        labels = labels[:labels.index("};")]
        assert f"{kind}: '{label}'" in labels

    def test_each_kind_lands_where_its_live_poll_path_lands(self):
        dispatcher = _dispatcher(_source())
        assert "activateClosedScenario(state)" in dispatcher
        assert "renderSuggestResults(state.result)" in dispatcher
        assert "renderMonthlyResults(state.result)" in dispatcher
        assert "restoreMonthlySearchSpec(state.closure_search_spec)" in dispatcher

    def test_a_recovered_search_restores_its_own_form_and_edges(self):
        dispatcher = _dispatcher(_source())
        assert "state.closure_search_spec?.directed_edges" in dispatcher
        assert "lastMonthlySpec = state.closure_search_spec || lastMonthlySpec" in dispatcher
        assert "monthlyJobServerTracked = state.server_tracked !== false" in dispatcher

    def test_a_done_state_without_a_result_still_says_so(self):
        """A restarted server answers "done" for a job whose result object is
        gone.  Rendering undefined would throw into the catch with a
        TypeError; naming it keeps the banner readable."""
        dispatcher = _dispatcher(_source())
        assert dispatcher.count("resultatet finns inte kvar i serverns minne") == 2

    def test_a_result_that_can_no_longer_be_shown_is_announced_too(self):
        dispatcher = _dispatcher(_source())
        tail = dispatcher[dispatcher.index("} catch (e) {"):]
        assert "announceStudyOutcome(FINISHED_JOB_LABELS[kind], 'error'" in tail

    def test_the_map_is_made_visible_before_anything_is_rendered_into_it(self):
        """openWorkspace hides the "Välj en arbetsyta" landing card; without it
        the recovered result renders underneath it."""
        dispatcher = _dispatcher(_source())
        opened = dispatcher.index("await openWorkspace('closure');")
        for call in ("activateClosedScenario(state)", "renderSuggestResults(",
                     "renderMonthlyResults("):
            assert opened < dispatcher.index(call), call
