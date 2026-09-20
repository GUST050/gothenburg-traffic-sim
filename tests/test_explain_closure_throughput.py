"""The closure-throughput explainer must separate the two possible causes.

A positive `active_closure_edge_throughput` is either the simulator letting
a vehicle onto a sealed edge or the gate scoring a bucket in which the edge
was open. Those need opposite fixes, and the gate's single number cannot
tell them apart, so the explainer has to — and it has to say so in the
words the reader acts on.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import explain_closure_throughput as ect


def _edgedata(path: Path, rows) -> Path:
    body = "\n".join(
        f'  <interval begin="{begin}" end="{end}">\n'
        f'    <edge id="closed" entered="{entered}"/>\n'
        f"  </interval>"
        for begin, end, entered in rows)
    path.write_text(f"<meandata>\n{body}\n</meandata>\n")
    return path


def _closure_additional(path: Path, windows) -> Path:
    body = "\n".join(
        f'    <interval begin="{begin}" end="{end}">\n'
        f'      <closingReroute id="closed" disallow="all"/>\n'
        f"    </interval>"
        for begin, end in windows)
    path.write_text(
        f'<additional>\n  <rerouter id="closure" edges="closed">\n{body}\n'
        f"  </rerouter>\n</additional>\n")
    return path


class TestVerdict:
    def test_an_open_bucket_inside_the_scored_window_reads_as_bookkeeping(
            self, tmp_path, capsys):
        # SUMO sealed [900, 2700); the gate is asked about [900, 4500), so the
        # traffic that legitimately resumed at reopening is scored as a leak.
        edgedata = _edgedata(tmp_path / "ed.xml",
                             [(900, 1800, 0), (1800, 2700, 0),
                              (2700, 3600, 12), (3600, 4500, 9)])
        additional = _closure_additional(tmp_path / "c.add.xml", [(900, 2700)])

        ect.main(["--edgedata", str(edgedata),
                  "--closure-additional", str(additional),
                  "--closure", "closed:900:4500"])

        out = capsys.readouterr().out
        assert "VERDICT: bookkeeping" in out
        assert "21 vehicle(s) in 2 scored bucket(s)" in out

    def test_a_sealed_bucket_that_still_carried_traffic_blames_the_simulator(
            self, tmp_path, capsys):
        edgedata = _edgedata(tmp_path / "ed.xml",
                             [(900, 1800, 3), (1800, 2700, 0)])
        additional = _closure_additional(tmp_path / "c.add.xml", [(900, 2700)])

        ect.main(["--edgedata", str(edgedata),
                  "--closure-additional", str(additional)])

        out = capsys.readouterr().out
        assert "VERDICT: simulator" in out

    def test_a_clean_closure_is_reported_as_clean(self, tmp_path, capsys):
        edgedata = _edgedata(tmp_path / "ed.xml",
                             [(900, 1800, 0), (2700, 3600, 14)])
        additional = _closure_additional(tmp_path / "c.add.xml", [(900, 2700)])

        ect.main(["--edgedata", str(edgedata),
                  "--closure-additional", str(additional)])

        out = capsys.readouterr().out
        assert "VERDICT: no leak in the scored window." in out

    def test_unaligned_buckets_are_called_out(self, tmp_path, capsys):
        # A bucket starting at 1200 is filed under quarter 1 while covering
        # part of quarter 2, so a window boundary lands on the wrong traffic.
        edgedata = _edgedata(tmp_path / "ed.xml", [(1200, 2100, 5)])
        additional = _closure_additional(tmp_path / "c.add.xml", [(900, 2700)])

        ect.main(["--edgedata", str(edgedata),
                  "--closure-additional", str(additional)])

        assert "do not start on a 15-minute boundary" in capsys.readouterr().out

    def test_the_scored_window_alone_refuses_to_guess(self, tmp_path, capsys):
        edgedata = _edgedata(tmp_path / "ed.xml", [(900, 1800, 4)])

        ect.main(["--edgedata", str(edgedata), "--closure", "closed:900:2700"])

        out = capsys.readouterr().out
        assert "pass --closure-additional as well" in out

    def test_one_of_the_two_window_sources_is_required(self, tmp_path):
        edgedata = _edgedata(tmp_path / "ed.xml", [(900, 1800, 0)])
        with pytest.raises(SystemExit):
            ect.main(["--edgedata", str(edgedata)])


class TestTheUiNamesTheGateThatFired:
    """A disqualification message must not guess at the cause.

    The no_viable text listed three fixed gates ("omväg, strandade fordon,
    simuleringshälsa") whatever had actually happened, so a month that fell
    entirely on `active_closure_edge_throughput` read as if the road were
    simply impossible to close — and the real reason had to be dug out of
    result.json by hand.
    """

    APP_JS = ROOT / "web" / "app.js"

    def test_the_hardcoded_gate_guess_is_gone(self):
        source = self.APP_JS.read_text(encoding="utf-8")
        assert "omväg, strandade fordon, simuleringshälsa" not in source

    def test_the_message_is_built_from_the_reported_failures(self):
        source = self.APP_JS.read_text(encoding="utf-8")
        no_viable = source.split("result.status === 'no_viable'")[1][:1200]
        assert "hard_failures" in no_viable
        assert "GATE_LABELS" in no_viable

    def test_an_unknown_gate_code_is_shown_verbatim(self):
        # `GATE_LABELS[reason] || reason` — a gate nobody has translated yet
        # must still reach the reader, not be dropped or renamed.
        source = self.APP_JS.read_text(encoding="utf-8")
        assert "GATE_LABELS[reason] || reason" in source

    def test_the_gate_the_failing_search_hit_has_a_gloss(self):
        source = self.APP_JS.read_text(encoding="utf-8")
        assert "active_closure_edge_throughput: 'trafik på avstängd kant'" in source
