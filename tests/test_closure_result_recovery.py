"""A finished closure has to reach the operator, not just the disk.

/api/close is start-plus-poll: the job outlives the request that began it and
the tab that watched it.  The server therefore keeps its terminal state in
memory — but for a `simulate` job nothing ever read it back, so a reload, a
closed tab, or an operator who thought the run had hung left a published,
selectable scenario sitting in index.json while the map still showed the
previous study and the UI said nothing at all.  That is CLAUDE.md's 2026-07-06
recalibration incident in a second endpoint, and the same fix: recover the
outcome on load, scoped to the tab that actually started it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"


def _source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _reattach_no_active_branch(source: str) -> str:
    """The load-time branch that runs when no job is still in flight."""
    start = source.index("const active = states.find(([, state]) =>")
    end = source.index("const [kind, state] = active;", start)
    return source[start:end]


class TestPendingClosureMarker:
    def test_the_marker_is_session_scoped_like_the_recalibration_one(self):
        source = _source()
        assert "const CLOSURE_SESSION_KEY = 'pendingClosure'" in source
        assert "sessionStorage.setItem(CLOSURE_SESSION_KEY" in source
        assert "sessionStorage.removeItem(CLOSURE_SESSION_KEY)" in source

    def test_an_unavailable_session_store_degrades_instead_of_throwing(self):
        source = _source()
        remember = source[source.index("function rememberPendingClosure("):]
        remember = remember[:remember.index("function forgetPendingClosure(")]
        assert "try {" in remember and "catch (_)" in remember

    def test_the_marker_is_the_scenario_id_the_server_echoes_back(self):
        source = _source()
        matches = source[source.index("function pendingClosureMatches("):]
        matches = matches[:matches.index("\n        }\n", matches.index("return"))]
        assert "state?.scenario_spec?.scenario_id" in matches
        assert "pending.scenario_id === id" in matches

    def test_the_marker_is_written_before_the_start_response_is_read(self):
        source = _source()
        poller = source[source.index("async function runRoadClosureOperation("):]
        poller = poller[:poller.index("async function activateClosedScenario(")]
        remember = poller.index("rememberPendingClosure(")
        # A dropped 202 is exactly the case the marker recovers, so it cannot
        # wait for the response to come back.
        assert remember < poller.index("const response = await fetch(operation.startUrl")
        assert "kind === 'simulate'" in poller[:remember]

    def test_only_a_simulate_job_writes_or_clears_the_marker(self):
        source = _source()
        poller = source[source.index("async function runRoadClosureOperation("):]
        poller = poller[:poller.index("async function activateClosedScenario(")]
        for call in ("rememberPendingClosure(", "forgetPendingClosure()"):
            index = poller.index(call)
            assert "kind === 'simulate'" in poller[max(0, index - 200):index], call

    def test_a_tab_that_saw_the_outcome_clears_the_marker(self):
        source = _source()
        poller = source[source.index("async function runRoadClosureOperation("):]
        poller = poller[:poller.index("async function activateClosedScenario(")]
        assert poller.index("forgetPendingClosure()") < poller.index("return status;")


class TestFinishedClosureRecovery:
    def test_a_finished_closure_is_recovered_on_load(self):
        branch = _reattach_no_active_branch(_source())
        assert "const finishedClosure = states.find" in branch
        assert "kind === 'simulate'" in branch
        for status in ("'done'", "'error'", "'cancelled'"):
            assert f"state.status === {status}" in branch, status
        assert "activateClosedScenario(state)" in branch

    def test_the_recovery_never_fires_for_a_visitor_who_started_nothing(self):
        """/api/close/status stays "done" forever — it is the last completed
        job, not this tab's.  Applying it unconditionally would drop every
        future fresh page load into a stale closure, the bug the equivalent
        recalibration recovery already had to fix once."""
        branch = _reattach_no_active_branch(_source())
        find = branch[branch.index("const finishedClosure = states.find"):]
        find = find[:find.index("if (finishedClosure) {")]
        assert "pendingClosureMatches(state)" in find

    def test_a_recovered_failure_is_announced_rather_than_dropped(self):
        branch = _reattach_no_active_branch(_source())
        block = branch[branch.index("if (finishedClosure) {"):]
        block = block[:block.index("const completedExternal =")]
        assert "announceStudyOutcome(" in block
        assert "state.status === 'cancelled' ? 'cancelled' : 'error'" in block
        assert "state.error" in block

    def test_a_recovered_scenario_that_no_longer_loads_still_says_so(self):
        """A recalibration landing afterwards wipes stale scenario files, so
        the manifest entry can outlive its data.  Silence is the one outcome
        this whole branch exists to prevent."""
        branch = _reattach_no_active_branch(_source())
        block = branch[branch.index("if (finishedClosure) {"):]
        block = block[:block.index("const completedExternal =")]
        done = block[block.index("if (state.status === 'done') {"):]
        assert "catch (e)" in done
        assert "announceStudyOutcome('Avstängningssimuleringen', 'error'" in done

    def test_the_recovery_consumes_the_marker_exactly_once(self):
        branch = _reattach_no_active_branch(_source())
        block = branch[branch.index("if (finishedClosure) {"):]
        block = block[:block.index("const completedExternal =")]
        assert block.count("forgetPendingClosure()") == 1
        assert block.index("forgetPendingClosure()") < block.index("if (state.status === 'done')")

    def test_the_map_is_made_visible_before_the_scenario_is_activated(self):
        """openWorkspace hides the "Välj en arbetsyta" landing card; without
        it the recovered scenario renders underneath it."""
        branch = _reattach_no_active_branch(_source())
        block = branch[branch.index("if (finishedClosure) {"):]
        block = block[:block.index("const completedExternal =")]
        assert block.index("openWorkspace('closure')") < block.index("activateClosedScenario(state)")
