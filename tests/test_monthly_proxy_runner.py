"""The v6 runner may only ever use its ONE frozen demand archive.

Process-free: no SUMO, no TraCI, no run root, no campaign outcome. Every
fail-closed branch is exercised against synthetic fixtures under tmp_path; the
single test that touches the real archive reads only its five canonical files,
and proves that with an audit hook.

WHY THIS EXISTS. `_demand_archives()` resolves demand by key alone, and the key
`2ac04275daabe93c` is claimed by several successful archives whose input bytes
DIFFER. Key resolution therefore silently depends on which copy happens to sort
last — unreproducible, and unfalsifiable after the run. v6 binds one exact path
plus five digests instead.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SELECTION = Path("validation/heldout_v6_selection.json")
MANIFEST = Path("validation/monthly_proxy_manifest_v6.json")
CANONICAL_FILES = ("calibrated.rou.xml", "calibrated_v1.rou.xml",
                   "calibrated_v2.rou.xml", "demand_meta.json", "manifest.json")


def _runner():
    """Load the runner by path; it is a script, not an importable package."""
    if "rmpv_runner" in sys.modules:
        return sys.modules["rmpv_runner"]
    spec = importlib.util.spec_from_file_location(
        "rmpv_runner", "run_monthly_proxy_validation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rmpv_runner"] = module
    spec.loader.exec_module(module)
    return module


def _load(path):
    return json.loads(Path(path).read_text())


def _canonical_key(payload):
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _synthetic_archive(root, name="runs/demand-synthetic", **over):
    """A complete, self-consistent archive plus a selection that binds it."""
    archive = root / name
    archive.mkdir(parents=True)
    for filename in CANONICAL_FILES[:3]:
        (archive / filename).write_text(f"<routes/><!-- {filename} -->")
    meta = {"demand_build_key": over.get("key", "k" * 16),
            "build_id": over.get("build_id", "b" * 20),
            "epoch_sim": over.get("epoch_sim", "2027-07-15T00:00:00"),
            "n_intervals": over.get("n_intervals", 480),
            "n_variants": 3}
    (archive / "demand_meta.json").write_text(json.dumps(meta, sort_keys=True))
    manifest = {"kind": "demand", "status": over.get("status", "succeeded"),
                "git_commit": over.get("git_commit", "c" * 40),
                "git_dirty": over.get("git_dirty", False)}
    (archive / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    digests = {n: hashlib.sha256((archive / n).read_bytes()).hexdigest()
               for n in CANONICAL_FILES}
    selection = {
        "campaign_version": "v6",
        "canonical_demand": {
            "path": name, "demand_build_key": meta["demand_build_key"],
            "build_id": meta["build_id"], "epoch_sim": meta["epoch_sim"],
            "n_intervals": meta["n_intervals"], "git_commit": manifest["git_commit"],
            "file_sha256": digests, "designation": "synthetic fixture"},
    }
    selection["content_key"] = _canonical_key(selection)
    return archive, selection


def _write_pair(tmp_path, selection, *, cases=None, campaign="v6"):
    spath = tmp_path / "selection.json"
    spath.write_text(json.dumps(selection, indent=2, sort_keys=True))
    key = selection.get("canonical_demand", {}).get("demand_build_key", "k" * 16)
    manifest = {
        "campaign_version": campaign,
        "selection_content_key": selection.get("content_key"),
        "cases": cases if cases is not None else [
            {"case_id": "c1", "closure_search_spec": {"demand_build_id": key}}],
    }
    return manifest, spath


class TestExactBindingSucceeds:

    def test_a_consistent_selection_and_archive_bind(self, tmp_path, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        archive, selection = _synthetic_archive(tmp_path)
        manifest, spath = _write_pair(tmp_path, selection)
        key, path = runner.bind_exact_demand(manifest, spath)
        assert key == selection["canonical_demand"]["demand_build_key"]
        assert path == archive

    def test_binding_ignores_a_divergent_sibling_entirely(self, tmp_path, monkeypatch):
        """A sibling claiming the same key must not influence an exact bind."""
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        archive, selection = _synthetic_archive(tmp_path)
        sibling, _ = _synthetic_archive(tmp_path, name="runs/demand-other")
        (sibling / "calibrated.rou.xml").write_text("DIFFERENT BYTES")
        manifest, spath = _write_pair(tmp_path, selection)
        assert runner.bind_exact_demand(manifest, spath)[1] == archive


class TestSelectionFailsClosed:

    def _bind(self, tmp_path, monkeypatch, mutate=None, **pair):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        _archive, selection = _synthetic_archive(tmp_path)
        if mutate is not None:
            mutate(selection)
        manifest, spath = _write_pair(tmp_path, selection, **pair)
        return runner, manifest, spath

    def test_missing_selection_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, _ = self._bind(tmp_path, monkeypatch)
        with pytest.raises(SystemExit, match="requires --selection"):
            runner.bind_exact_demand(manifest, None)

    def test_the_refusal_names_the_approved_command(self, tmp_path, monkeypatch):
        runner, manifest, _ = self._bind(tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as excinfo:
            runner.bind_exact_demand(manifest, None)
        assert "--manifest validation/monthly_proxy_manifest_v6.json" in str(excinfo.value)

    def test_a_directory_is_not_a_selection_artifact(self, tmp_path, monkeypatch):
        runner, manifest, _ = self._bind(tmp_path, monkeypatch)
        (tmp_path / "adir").mkdir()
        with pytest.raises(SystemExit, match="must be a regular file"):
            runner.bind_exact_demand(manifest, tmp_path / "adir")

    def test_a_symlinked_selection_is_refused(self, tmp_path, monkeypatch):
        import os
        runner, manifest, spath = self._bind(tmp_path, monkeypatch)
        link = tmp_path / "link.json"
        os.symlink(spath, link)
        with pytest.raises(SystemExit, match="must be a regular file"):
            runner.bind_exact_demand(manifest, link)

    def test_a_tampered_selection_key_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath = self._bind(tmp_path, monkeypatch)
        payload = json.loads(spath.read_text())
        payload["canonical_demand"]["build_id"] = "tampered"
        spath.write_text(json.dumps(payload))          # key no longer recomputes
        with pytest.raises(SystemExit, match="content key does not recompute"):
            runner.bind_exact_demand(manifest, spath)

    def test_a_selection_from_another_manifest_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath = self._bind(tmp_path, monkeypatch)
        manifest["selection_content_key"] = "0" * 64
        with pytest.raises(SystemExit, match="does not belong to this manifest"):
            runner.bind_exact_demand(manifest, spath)

    def test_a_campaign_mismatch_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath = self._bind(
            tmp_path, monkeypatch, mutate=lambda s: s.update(campaign_version="v5"))
        # re-key so only the campaign disagrees
        payload = json.loads(spath.read_text())
        payload["content_key"] = _canonical_key(payload)
        spath.write_text(json.dumps(payload))
        manifest["selection_content_key"] = payload["content_key"]
        with pytest.raises(SystemExit, match="does not\\s+match manifest campaign"):
            runner.bind_exact_demand(manifest, spath)

    @pytest.mark.parametrize("field", [
        "path", "demand_build_key", "build_id", "epoch_sim", "n_intervals",
        "git_commit", "file_sha256", "designation"])
    def test_an_incomplete_canonical_demand_record_is_refused(
            self, tmp_path, monkeypatch, field):
        def drop(selection):
            selection["canonical_demand"].pop(field)
        runner, manifest, spath = self._bind(tmp_path, monkeypatch, mutate=drop)
        payload = json.loads(spath.read_text())
        payload["content_key"] = _canonical_key(payload)
        spath.write_text(json.dumps(payload))
        manifest["selection_content_key"] = payload["content_key"]
        with pytest.raises(SystemExit, match="complete canonical_demand record"):
            runner.bind_exact_demand(manifest, spath)

    def test_an_absent_canonical_demand_record_is_refused(self, tmp_path, monkeypatch):
        def drop(selection):
            selection.pop("canonical_demand")
        runner, manifest, spath = self._bind(tmp_path, monkeypatch, mutate=drop)
        payload = json.loads(spath.read_text())
        payload["content_key"] = _canonical_key(payload)
        spath.write_text(json.dumps(payload))
        manifest["selection_content_key"] = payload["content_key"]
        with pytest.raises(SystemExit, match="complete canonical_demand record"):
            runner.bind_exact_demand(manifest, spath)


class TestPathConfinement:

    def _with_path(self, tmp_path, monkeypatch, recorded):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        _archive, selection = _synthetic_archive(tmp_path)
        selection["canonical_demand"]["path"] = recorded
        selection["content_key"] = _canonical_key(selection)
        manifest, spath = _write_pair(tmp_path, selection)
        manifest["selection_content_key"] = selection["content_key"]
        return runner, manifest, spath

    @pytest.mark.parametrize("recorded", [
        "/etc/passwd", "../outside/demand", "runs/../../escape",
    ])
    def test_traversal_and_absolute_paths_are_refused(
            self, tmp_path, monkeypatch, recorded):
        runner, manifest, spath = self._with_path(tmp_path, monkeypatch, recorded)
        with pytest.raises(SystemExit, match="repository-relative|escapes"):
            runner.bind_exact_demand(manifest, spath)

    def test_an_empty_path_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath = self._with_path(tmp_path, monkeypatch, "")
        with pytest.raises(SystemExit, match="non-empty string"):
            runner.bind_exact_demand(manifest, spath)

    def test_a_symlinked_archive_directory_is_refused(self, tmp_path, monkeypatch):
        import os
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        archive, selection = _synthetic_archive(tmp_path)
        link = tmp_path / "runs" / "demand-link"
        os.symlink(archive, link)
        selection["canonical_demand"]["path"] = "runs/demand-link"
        selection["content_key"] = _canonical_key(selection)
        manifest, spath = _write_pair(tmp_path, selection)
        manifest["selection_content_key"] = selection["content_key"]
        with pytest.raises(SystemExit, match="traverses a symlink"):
            runner.bind_exact_demand(manifest, spath)


class TestArchiveIdentityFailsClosed:

    def _bind_with(self, tmp_path, monkeypatch, **over):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        archive, selection = _synthetic_archive(tmp_path, **over)
        manifest, spath = _write_pair(tmp_path, selection)
        return runner, manifest, spath, archive, selection

    def test_a_changed_route_byte_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath, archive, _ = self._bind_with(tmp_path, monkeypatch)
        (archive / "calibrated_v1.rou.xml").write_text("CHANGED AFTER FREEZE")
        with pytest.raises(SystemExit, match="not bindable"):
            runner.bind_exact_demand(manifest, spath)

    def test_a_missing_canonical_file_is_refused(self, tmp_path, monkeypatch):
        runner, manifest, spath, archive, _ = self._bind_with(tmp_path, monkeypatch)
        (archive / "manifest.json").unlink()
        with pytest.raises(SystemExit, match="not bindable"):
            runner.bind_exact_demand(manifest, spath)

    @pytest.mark.parametrize("field, value", [
        ("demand_build_key", "otherkey0otherkey"), ("build_id", "different"),
        ("epoch_sim", "2020-01-01T00:00:00"), ("n_intervals", 96),
        ("git_commit", "d" * 40)])
    def test_recorded_metadata_must_match_the_archive(
            self, tmp_path, monkeypatch, field, value):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        _archive, selection = _synthetic_archive(tmp_path)
        selection["canonical_demand"][field] = value
        selection["content_key"] = _canonical_key(selection)
        manifest, spath = _write_pair(tmp_path, selection)
        manifest["selection_content_key"] = selection["content_key"]
        manifest["cases"] = [{"case_id": "c1", "closure_search_spec": {
            "demand_build_id": selection["canonical_demand"]["demand_build_key"]}}]
        with pytest.raises(SystemExit, match="not bindable"):
            runner.bind_exact_demand(manifest, spath)

    @pytest.mark.parametrize("over", [
        {"status": "failed"}, {"git_dirty": True}])
    def test_an_unsuccessful_or_dirty_build_is_refused(
            self, tmp_path, monkeypatch, over):
        runner, manifest, spath, _a, _s = self._bind_with(tmp_path, monkeypatch, **over)
        with pytest.raises(SystemExit, match="not bindable"):
            runner.bind_exact_demand(manifest, spath)


class TestCasesMustUseTheBoundDemand:

    def test_a_case_on_another_demand_is_refused(self, tmp_path, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        _archive, selection = _synthetic_archive(tmp_path)
        good = selection["canonical_demand"]["demand_build_key"]
        manifest, spath = _write_pair(tmp_path, selection, cases=[
            {"case_id": "c1", "closure_search_spec": {"demand_build_id": good}},
            {"case_id": "c2", "closure_search_spec": {"demand_build_id": "other"}}])
        with pytest.raises(SystemExit, match="do not use the bound demand"):
            runner.bind_exact_demand(manifest, spath)


class TestNoDiscoveryNoRootNoSumo:
    """Criterion 3: everything fails before discovery, run root or SUMO."""

    def test_v6_never_calls_sibling_discovery(self, tmp_path, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)

        def forbidden():
            raise AssertionError("v6 must never glob for demand archives")

        monkeypatch.setattr(runner, "_demand_archives", forbidden)
        _archive, selection = _synthetic_archive(tmp_path)
        manifest, spath = _write_pair(tmp_path, selection)
        runner.bind_exact_demand(manifest, spath)      # must not raise

    def test_binding_resolves_no_sumo_and_creates_no_run_root(
            self, tmp_path, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(runner.rs, "sumo_home", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("SUMO must not be resolved during binding")))
        made = []
        real_mkdir = Path.mkdir

        def watched(self, *args, **kwargs):
            if "closure-proxy-validation" in str(self):
                made.append(str(self))
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", watched)
        _archive, selection = _synthetic_archive(tmp_path)
        manifest, spath = _write_pair(tmp_path, selection)
        runner.bind_exact_demand(manifest, spath)
        assert made == [], made

    def test_a_failed_binding_also_creates_no_run_root(self, tmp_path, monkeypatch):
        runner = _runner()
        monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
        _archive, selection = _synthetic_archive(tmp_path)
        manifest, spath = _write_pair(tmp_path, selection)
        manifest["selection_content_key"] = "0" * 64
        with pytest.raises(SystemExit):
            runner.bind_exact_demand(manifest, spath)
        assert not (tmp_path / "runs" / "closure-proxy-validation").exists()


class TestLegacyBehaviourIsUnchanged:

    def test_non_binding_campaigns_reject_a_selection(self, tmp_path):
        runner = _runner()
        assert "v5" not in runner.EXACT_DEMAND_BINDING_CAMPAIGNS
        assert "v4" not in runner.EXACT_DEMAND_BINDING_CAMPAIGNS

    def test_only_demand_binding_campaigns_bind_exactly(self):
        """v7 joined v6 when the ranking objective changed: it binds its own
        canonical archive by exact path and full identity the same way. v10
        joined for Stage 4 of the closure-integrity plan, and DELIBERATELY
        before its freeze — the freeze fingerprints the runner, so registering
        afterwards would void the campaign. The set stays pinned so a campaign
        cannot acquire exact binding by accident — legacy v1-v5 must remain
        excluded (covered above)."""
        assert _runner().EXACT_DEMAND_BINDING_CAMPAIGNS == frozenset(
            {"v6", "v7", "v8", "v9", "v10"})

    def test_key_resolution_still_exists_for_legacy_campaigns(self):
        """Legacy is not broadened, relabeled or removed."""
        assert callable(_runner()._demand_archives)

    def test_the_approved_v6_command_shape_is_frozen(self):
        command = _runner().APPROVED_V6_COMMAND
        assert command == (
            "python3 run_monthly_proxy_validation.py "
            "--manifest validation/monthly_proxy_manifest_v6.json "
            "--selection validation/heldout_v6_selection.json "
            "--seed-workers 3")


class TestAgainstTheRealFrozenArtifacts:
    """The only tests that touch the real archive; they read five files."""

    def test_the_real_v6_selection_binds_its_real_archive(self):
        runner = _runner()
        manifest = _load(MANIFEST)
        key, path = runner.bind_exact_demand(manifest, SELECTION)
        assert key == "2ac04275daabe93c"
        assert path == Path("runs/demand-20260721-222017-41bc682a-bbe1").resolve()

    def test_binding_reads_only_the_five_canonical_files(self):
        """Discovery guard: no sibling archive, no outcome, no campaign root."""
        opened = []
        runner = _runner()

        def hook(event, args):
            if event == "open":
                opened.append(str(args[0]))

        sys.addaudithook(hook)
        runner.bind_exact_demand(_load(MANIFEST), SELECTION)
        canonical = "runs/demand-20260721-222017-41bc682a-bbe1"
        touched = {p for p in opened if "/runs/" in p or p.startswith("runs/")}
        strays = sorted(p for p in touched if canonical not in p)
        assert strays == [], strays
        assert {Path(p).name for p in touched} <= set(CANONICAL_FILES)

    def test_the_manifest_binds_the_selection_it_was_frozen_with(self):
        assert _load(MANIFEST)["selection_content_key"] == _load(SELECTION)["content_key"]

    def test_every_real_v6_case_uses_the_bound_demand(self):
        key = _load(SELECTION)["canonical_demand"]["demand_build_key"]
        for case in _load(MANIFEST)["cases"]:
            assert case["closure_search_spec"]["demand_build_id"] == key

    def test_no_outcome_gate_or_certificate_exists(self):
        assert not Path("validation/monthly_gate_record.json").exists()
        assert not Path("validation/monthly_gate_adoption_certificate.json").exists()
