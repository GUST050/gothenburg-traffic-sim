"""Run registry (IMPROVEMENT_PLAN.md E1) contract tests."""
import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runs as runs_mod


class TestRunRegistry:
    def _patched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path / "runs")
        return runs_mod

    def test_manifest_exists_before_any_work(self, monkeypatch, tmp_path):
        r = self._patched(monkeypatch, tmp_path).start_run(
            "demand", {"date": "2025-09-16"}, argv=["make", "demand"])
        m = json.loads((r.dir / "manifest.json").read_text())
        assert m["status"] == "running"
        assert m["kind"] == "demand"
        assert m["inputs"] == {"date": "2025-09-16"}
        assert m["argv"] == ["make", "demand"]
        assert m["run_id"].startswith("demand-")

    def test_success_flips_latest_pointer(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        r1 = mod.start_run("demand", {"date": "a"}, argv=[])
        r1.finish("succeeded")
        r2 = mod.start_run("demand", {"date": "b"}, argv=[])
        r2.finish("succeeded")
        assert mod.latest("demand")["run_id"] == r2.run_id

    def test_failure_leaves_previous_pointer_untouched(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        good = mod.start_run("demand", {"date": "a"}, argv=[])
        good.finish("succeeded")
        bad = mod.start_run("demand", {"date": "b"}, argv=[])
        bad.finish("failed", error="boom")
        assert mod.latest("demand")["run_id"] == good.run_id
        assert mod.load_manifest(bad.run_id)["error"] == "boom"
        assert mod.load_manifest(bad.run_id)["status"] == "failed"

    def test_outputs_are_copied_and_recorded(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        product = tmp_path / "calibrated.rou.xml"
        product.write_text("<routes/>")
        r = mod.start_run("demand", {}, argv=[])
        dest = r.add_output(product)
        assert dest.read_text() == "<routes/>"
        m = mod.load_manifest(r.run_id)
        assert m["outputs"][0]["name"] == "calibrated.rou.xml"
        assert m["outputs"][0]["bytes"] == len("<routes/>")
        assert m["outputs"][0]["sha256"] == hashlib.sha256(
            b"<routes/>").hexdigest()

    def test_missing_output_is_disclosed_not_fatal(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        r = mod.start_run("demand", {}, argv=[])
        assert r.add_output(tmp_path / "nope.xml") is None
        assert str(tmp_path / "nope.xml") in mod.load_manifest(
            r.run_id)["missing_outputs"]

    def test_recorded_facts_land_in_manifest(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        r = mod.start_run("scenario", {"close": ["e1"]}, argv=[])
        r.record("geh_pct", 100.0)
        assert mod.load_manifest(r.run_id)["recorded"]["geh_pct"] == 100.0

    def test_distinct_ids_for_identical_inputs(self, monkeypatch, tmp_path):
        mod = self._patched(monkeypatch, tmp_path)
        a = mod.start_run("demand", {"date": "x"}, argv=[])
        b = mod.start_run("demand", {"date": "x"}, argv=[])
        assert a.run_id != b.run_id
        assert a.manifest["input_digest"] == b.manifest["input_digest"]
