"""E3 per-seed health gate tests (IMPROVEMENT_PLAN.md; improvement plan Phase 1.3-1.4).

parse_seed_health reads SUMO's --statistic-output; seed_health_flags
evaluates the gates; serve.validate_staged_scenarios refuses a staged
baseline carrying flags.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import run_scenario as rs
import serve

STATS_XML = """<statistics>
    <vehicles loaded="{loaded}" inserted="{inserted}" running="{running}"
              waiting="{waiting}"/>
    <teleports total="{teleports}" jam="0" yield="0" wrongLane="0"/>
    <safety collisions="0" emergencyStops="0"/>
</statistics>"""


def write_stats(tmp_path, loaded=1000, inserted=1000, running=0, waiting=0,
                teleports=0):
    p = tmp_path / "stats.xml"
    p.write_text(STATS_XML.format(loaded=loaded, inserted=inserted,
                                  running=running, waiting=waiting,
                                  teleports=teleports))
    return p


class TestParseSeedHealth:
    def test_reads_all_fields(self, tmp_path):
        p = write_stats(tmp_path, loaded=1200, inserted=1150, running=30,
                        waiting=20, teleports=2)
        h = rs.parse_seed_health(p, seed=1000, variant="calibrated.rou.xml")
        assert h == {"seed": 1000, "variant": "calibrated.rou.xml",
                     "loaded": 1200, "inserted": 1150, "running_at_end": 30,
                     "waiting_at_end": 20, "teleports": 2, "collisions": 0}

    def test_missing_file_returns_none(self, tmp_path):
        assert rs.parse_seed_health(tmp_path / "nope.xml", 1000, "v") is None

    def test_corrupt_xml_returns_none(self, tmp_path):
        p = tmp_path / "stats.xml"
        p.write_text("<statistics><vehicles")
        assert rs.parse_seed_health(p, 1000, "v") is None


class TestSeedHealthFlags:
    def _health(self, tmp_path, **kw):
        return rs.parse_seed_health(write_stats(tmp_path, **kw), 1000, "v")

    def test_healthy_seed_has_no_flags(self, tmp_path):
        assert rs.seed_health_flags([self._health(tmp_path)]) == []

    def test_zero_insertion_is_fatal(self, tmp_path):
        flags = rs.seed_health_flags(
            [self._health(tmp_path, inserted=0, waiting=1000)])
        assert len(flags) == 1 and "0 of 1000" in flags[0]

    def test_unfinished_share_over_threshold_flags(self, tmp_path):
        flags = rs.seed_health_flags(
            [self._health(tmp_path, running=20, waiting=15)])  # 3.5% > 2%
        assert len(flags) == 1 and "unfinished" in flags[0]

    def test_unfinished_share_under_threshold_passes(self, tmp_path):
        assert rs.seed_health_flags(
            [self._health(tmp_path, running=10, waiting=5)]) == []  # 1.5%

    def test_teleport_burst_flags(self, tmp_path):
        flags = rs.seed_health_flags(
            [self._health(tmp_path, teleports=11)])
        assert len(flags) == 1 and "teleports" in flags[0]

    def test_teleport_share_scales_with_fleet(self, tmp_path):
        # 20000 inserted: max(10, 0.5%) = 100 — 50 teleports is fine.
        assert rs.seed_health_flags(
            [self._health(tmp_path, loaded=20000, inserted=20000,
                          teleports=50)]) == []

    def test_none_records_are_tolerated(self):
        assert rs.seed_health_flags([None]) == []


class TestServeRefusesUnhealthySeeds:
    def test_staged_baseline_with_health_flags_is_refused(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "index.json").write_text(json.dumps({"scenarios": [
            {"name": "baseline", "file": "baseline.json"}]}))
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1]},
            "seed_health_flags": ["seed 1001: 900/1000 vehicles unfinished"],
        }))
        meta = tmp_path / "demand_meta.json"
        meta.write_text(json.dumps({"pfe_fit": {
            "geh_pct": 100.0, "infeasible_intervals": 0}}))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert not ok and "simuleringshälsa" in reason

    def test_staged_baseline_without_field_passes(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "index.json").write_text(json.dumps({"scenarios": [
            {"name": "baseline", "file": "baseline.json"}]}))
        (staging / "baseline.json").write_text(json.dumps({
            "flows": {"e1": [1]}}))
        meta = tmp_path / "demand_meta.json"
        meta.write_text(json.dumps({}))
        ok, reason = serve.validate_staged_scenarios(staging, meta)
        assert ok, reason
