"""Process-free tests for the archived-demand resolver (LUNA-V6-01).

Synthetic fixtures only: no SUMO, no campaign outcome, no run root. Every
archive here is written under pytest's tmp_path.
"""
import hashlib
import json
from pathlib import Path

import pytest

from traffic_sim.simulation.heldout_selection import (
    DemandArchiveError, ROUTE_FILES, resolve_demand_archive)

KEY = "abc123demandkey0"
_OMIT = object()          # sentinel: write no git_dirty field at all


def _archive(root, name, *, key=KEY, build_id="b0", routes=("A", "B", "C"),
             intervals=480, status="succeeded", missing=(), kind="demand",
             manifest_extra=None, omit_status=False):
    d = root / name
    d.mkdir(parents=True)
    for filename, body in zip(ROUTE_FILES, routes):
        if filename in missing:
            continue
        (d / filename).write_text(body)
    if "demand_meta.json" not in missing:
        (d / "demand_meta.json").write_text(json.dumps({
            "demand_build_key": key, "build_id": build_id,
            "epoch_sim": "2027-07-15T00:00:00", "n_intervals": intervals}))
    if "manifest.json" not in missing:
        manifest = {"kind": kind}
        if not omit_status:
            manifest["status"] = status
        manifest.update(manifest_extra or {})
        (d / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    return d


class TestResolvesExactlyOne:

    def test_single_archive_resolves(self, tmp_path):
        _archive(tmp_path, "demand-1")
        got = resolve_demand_archive(KEY, tmp_path)
        assert got.path.name == "demand-1"
        assert got.demand_build_key == KEY and got.build_id == "b0"
        assert set(got.route_digests) == set(ROUTE_FILES)
        assert got.duplicates == ()

    def test_byte_identical_duplicates_resolve_deterministically(self, tmp_path):
        """Identical copies are resolvable; the rule is the smallest name."""
        _archive(tmp_path, "demand-b", build_id="x")
        _archive(tmp_path, "demand-a", build_id="x")
        first = resolve_demand_archive(KEY, tmp_path)
        second = resolve_demand_archive(KEY, tmp_path)
        assert first.path.name == "demand-a" == second.path.name
        assert first.duplicates == ("demand-b",)
        assert first.route_digests == second.route_digests

    def test_records_exact_input_digests(self, tmp_path):
        d = _archive(tmp_path, "demand-1")
        got = resolve_demand_archive(KEY, tmp_path)
        for name, digest in got.route_digests.items():
            assert digest == hashlib.sha256((d / name).read_bytes()).hexdigest()
        assert got.meta_digest == hashlib.sha256(
            (d / "demand_meta.json").read_bytes()).hexdigest()


class TestFailsClosed:

    def test_divergent_route_contents_are_refused(self, tmp_path):
        """The real defect: same key, different route bytes."""
        _archive(tmp_path, "demand-1", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-2", routes=("A", "B", "DIFFERENT"))
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS"):
            resolve_demand_archive(KEY, tmp_path)

    def test_divergence_is_refused_even_when_one_group_is_larger(self, tmp_path):
        """A majority does not settle it; the key must determine the inputs."""
        _archive(tmp_path, "demand-1", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-2", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-3", routes=("X", "B", "C"))
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS"):
            resolve_demand_archive(KEY, tmp_path)

    def test_absent_key_is_refused(self, tmp_path):
        _archive(tmp_path, "demand-1", key="someotherkey")
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    @pytest.mark.parametrize("missing", [
        ("calibrated.rou.xml",), ("calibrated_v1.rou.xml",),
        ("calibrated_v2.rou.xml",), ("manifest.json",), ("demand_meta.json",)])
    def test_incomplete_archive_is_not_a_candidate(self, tmp_path, missing):
        _archive(tmp_path, "demand-1", missing=missing)
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    def test_unsuccessful_build_is_not_a_candidate(self, tmp_path):
        _archive(tmp_path, "demand-1", status="failed")
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    def test_non_demand_manifest_is_not_a_candidate(self, tmp_path):
        _archive(tmp_path, "demand-1", kind="campaign")
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    def test_short_horizon_is_refused(self, tmp_path):
        _archive(tmp_path, "demand-1", intervals=96)
        with pytest.raises(DemandArchiveError, match="intervals"):
            resolve_demand_archive(KEY, tmp_path, required_intervals=480)

    def test_malformed_metadata_is_not_a_candidate(self, tmp_path):
        d = _archive(tmp_path, "demand-1")
        (d / "demand_meta.json").write_text("not json")
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)


class TestAmbiguityShapeIsRefused:
    """The blocker's SHAPE, reproduced synthetically under tmp_path.

    An earlier version of this test called the generic resolver on the live
    `runs/` tree, which discovers and hashes sibling demand archives. This
    revision binds one exact archive and permits no other `runs/` member, so
    the shape is proven from fixtures instead. Nothing outside tmp_path is
    read.
    """

    def test_three_distinct_groups_are_refused_and_counted(self, tmp_path):
        """Routes alone would say two groups; every required file says three."""
        _archive(tmp_path, "demand-1", build_id="b1", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-2", build_id="b2", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-3", build_id="b3", routes=("X", "B", "C"))
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS") as excinfo:
            resolve_demand_archive(KEY, tmp_path, required_intervals=480)
        message = str(excinfo.value)
        assert "3 distinct input contents" in message
        assert "across 3 successful archives" in message
        assert "would be reproducible" in message

    def test_route_only_identity_would_have_under_counted(self, tmp_path):
        """Guards the fix itself: metadata divergence must not collapse away."""
        _archive(tmp_path, "demand-1", build_id="b1", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-2", build_id="b2", routes=("A", "B", "C"))
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS") as excinfo:
            resolve_demand_archive(KEY, tmp_path, required_intervals=480)
        assert "2 distinct input contents" in str(excinfo.value)

    def test_the_resolver_is_never_pointed_at_the_live_tree(self):
        """No test in this module may resolve against the real `runs/` root.

        The needles are assembled at runtime so this guard cannot match its
        own source text.
        """
        source = Path(__file__).read_text()
        live_root = "Path(" + '"runs"' + ")"
        live_key = "2ac04275" + "daabe93c"
        assert live_root not in source, "a test targets the live runs/ tree"
        assert live_key not in source, "a test resolves the live demand key"


class TestReviewRegressions:
    """Sol review 2026-07-27: two P1 defects in the first resolver."""

    def test_absent_status_is_not_success(self, tmp_path):
        """[P1] An absent status carries no success provenance."""
        _archive(tmp_path, "demand-1", omit_status=True)
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    @pytest.mark.parametrize("status", ["failed", "running", "cancelled", ""])
    def test_only_succeeded_counts(self, tmp_path, status):
        _archive(tmp_path, "demand-1", status=status)
        with pytest.raises(DemandArchiveError, match="no successful"):
            resolve_demand_archive(KEY, tmp_path)

    def test_same_routes_but_divergent_metadata_is_not_a_duplicate(self, tmp_path):
        """[P1] The live July 21/22 case: identical routes, different build_id."""
        _archive(tmp_path, "demand-1", build_id="b1", routes=("A", "B", "C"))
        _archive(tmp_path, "demand-2", build_id="b2", routes=("A", "B", "C"))
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS"):
            resolve_demand_archive(KEY, tmp_path)

    def test_same_routes_but_divergent_manifest_is_not_a_duplicate(self, tmp_path):
        _archive(tmp_path, "demand-1", routes=("A", "B", "C"),
                 manifest_extra={"run_id": "one"})
        _archive(tmp_path, "demand-2", routes=("A", "B", "C"),
                 manifest_extra={"run_id": "two"})
        with pytest.raises(DemandArchiveError, match="AMBIGUOUS"):
            resolve_demand_archive(KEY, tmp_path)

    def test_resolved_result_records_every_required_input_digest(self, tmp_path):
        d = _archive(tmp_path, "demand-1")
        got = resolve_demand_archive(KEY, tmp_path)
        from traffic_sim.simulation.heldout_selection import REQUIRED_FILES
        assert set(got.input_digests) == set(REQUIRED_FILES)
        for name, digest in got.input_digests.items():
            assert digest == hashlib.sha256((d / name).read_bytes()).hexdigest()

    def test_fully_identical_archives_are_still_duplicates(self, tmp_path):
        """The fix must not make genuine duplicates unresolvable."""
        _archive(tmp_path, "demand-b")
        _archive(tmp_path, "demand-a")
        got = resolve_demand_archive(KEY, tmp_path)
        assert got.path.name == "demand-a" and got.duplicates == ("demand-b",)


# ── Exact-path canonical binding (LUNA-V6-02) ───────────────────────────────
class TestExactPathBinding:
    """Binding is by exact path and full identity; it never discovers siblings."""

    IDENT_BASE = {
        "demand_build_key": KEY, "build_id": "b0",
        "epoch_sim": "2027-07-15T00:00:00", "n_intervals": 480,
        "git_commit": "c" * 40,
    }

    def _bound(self, tmp_path, name="demand-canon", **kw):
        import hashlib as _h
        from traffic_sim.simulation.heldout_selection import REQUIRED_FILES
        extra = {"git_commit": kw.pop("git_commit", "c" * 40)}
        dirty = kw.pop("git_dirty", False)
        if dirty is not _OMIT:                      # _OMIT => field absent
            extra["git_dirty"] = dirty
        d = _archive(tmp_path, name, manifest_extra=extra, **kw)
        ident = dict(self.IDENT_BASE)
        ident["file_digests"] = {
            n: _h.sha256((d / n).read_bytes()).hexdigest() for n in REQUIRED_FILES}
        return d, ident

    def test_exact_path_binds(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import bind_canonical_archive
        d, ident = self._bound(tmp_path)
        got = bind_canonical_archive(d, ident)
        assert got.path == d and got.build_id == "b0"
        assert len(got.file_digests) == 5

    def test_binding_ignores_siblings_entirely(self, tmp_path):
        """A divergent sibling must not affect an exact-path binding."""
        from traffic_sim.simulation.heldout_selection import bind_canonical_archive
        d, ident = self._bound(tmp_path)
        _archive(tmp_path, "demand-other", routes=("X", "Y", "Z"))
        assert bind_canonical_archive(d, ident).path == d

    def test_wrong_path_is_refused(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        _d, ident = self._bound(tmp_path)
        with pytest.raises(CanonicalBindingError, match="not a real directory"):
            bind_canonical_archive(tmp_path / "absent", ident)

    def test_symlinked_archive_is_refused(self, tmp_path):
        import os
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path)
        link = tmp_path / "link"
        os.symlink(d, link)
        with pytest.raises(CanonicalBindingError, match="not a real directory"):
            bind_canonical_archive(link, ident)

    def test_symlinked_member_file_is_refused(self, tmp_path):
        import os
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path)
        target = d / "calibrated.rou.xml"
        payload = target.read_bytes()
        outside = tmp_path / "outside.xml"
        outside.write_bytes(payload)
        target.unlink()
        os.symlink(outside, target)
        with pytest.raises(CanonicalBindingError, match="non-regular"):
            bind_canonical_archive(d, ident)

    def test_missing_member_is_refused(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path)
        (d / "calibrated_v1.rou.xml").unlink()
        with pytest.raises(CanonicalBindingError, match="missing or non-regular"):
            bind_canonical_archive(d, ident)

    def test_digest_mismatch_is_refused(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path)
        ident["file_digests"]["calibrated.rou.xml"] = "0" * 64
        with pytest.raises(CanonicalBindingError, match="digest mismatch"):
            bind_canonical_archive(d, ident)

    @pytest.mark.parametrize("field, value", [
        ("demand_build_key", "otherkey"), ("build_id", "other"),
        ("epoch_sim", "2020-01-01T00:00:00"), ("n_intervals", 96),
        ("git_commit", "d" * 40)])
    def test_identity_mismatch_is_refused(self, tmp_path, field, value):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path)
        ident[field] = value
        with pytest.raises(CanonicalBindingError, match="identity mismatch"):
            bind_canonical_archive(d, ident)

    @pytest.mark.parametrize("kw", [{"status": "failed"}, {"kind": "campaign"},
                                    {"git_dirty": True}])
    def test_unsuccessful_or_dirty_build_is_refused(self, tmp_path, kw):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path, **kw)
        with pytest.raises(CanonicalBindingError, match="identity mismatch"):
            bind_canonical_archive(d, ident)

    def test_absent_git_dirty_is_refused(self, tmp_path):
        """[P1] A missing field carries no clean-tree provenance."""
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path, git_dirty=_OMIT)
        assert "git_dirty" not in json.loads((d / "manifest.json").read_text())
        with pytest.raises(CanonicalBindingError, match="affirmatively as false"):
            bind_canonical_archive(d, ident)

    @pytest.mark.parametrize("value", [0, "", None, "false", [], {}])
    def test_falsey_git_dirty_is_not_clean(self, tmp_path, value):
        """Only the boolean False proves a clean tree; 0/"" must not pass."""
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, bind_canonical_archive)
        d, ident = self._bound(tmp_path, git_dirty=value)
        with pytest.raises(CanonicalBindingError, match="affirmatively as false"):
            bind_canonical_archive(d, ident)

    def test_affirmative_false_still_binds(self, tmp_path):
        """The stricter rule must not reject the real canonical archive shape."""
        from traffic_sim.simulation.heldout_selection import bind_canonical_archive
        d, ident = self._bound(tmp_path, git_dirty=False)
        assert bind_canonical_archive(d, ident).path == d


class TestStreamingExposure:

    def _routes(self, tmp_path, vehicles):
        p = tmp_path / "r.rou.xml"
        lines = ["<routes>"]
        for depart, edges in vehicles:
            lines.append(f'<vehicle id="v{depart}" depart="{depart}">'
                         f'<route edges="{" ".join(edges)}"/></vehicle>')
        lines.append("</routes>")
        p.write_text("\n".join(lines))
        return p

    def test_collects_only_requested_edges(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import stream_edge_departures
        p = self._routes(tmp_path, [(10.0, ["a_b_0", "b_c_0"]), (20.0, ["x_y_0"])])
        got = stream_edge_departures(p, {"a_b_0"})
        assert got == {"a_b_0": [10.0]}

    def test_multiple_departures_are_sorted(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import stream_edge_departures
        p = self._routes(tmp_path, [(30.0, ["a_b_0"]), (10.0, ["a_b_0"])])
        assert stream_edge_departures(p, {"a_b_0"}) == {"a_b_0": [10.0, 30.0]}

    def test_unparsable_vehicle_is_refused_not_skipped(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, stream_edge_departures)
        p = tmp_path / "r.rou.xml"
        p.write_text('<routes>\n<vehicle id="v1"><route edges="a_b_0"/></vehicle>\n</routes>')
        with pytest.raises(CanonicalBindingError, match="unparsable"):
            stream_edge_departures(p, {"a_b_0"})

    def test_empty_route_file_is_refused(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import (
            CanonicalBindingError, stream_edge_departures)
        p = tmp_path / "r.rou.xml"
        p.write_text("<routes>\n</routes>")
        with pytest.raises(CanonicalBindingError, match="no vehicles"):
            stream_edge_departures(p, {"a_b_0"})

    def test_window_boundaries_are_half_open(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import window_exposure
        departs = [0.0, 10.0, 20.0, 30.0]
        assert window_exposure(departs, [(0.0, 20.0)]) == [2]     # 0 and 10
        assert window_exposure(departs, [(20.0, 40.0)]) == [2]    # 20 and 30
        assert window_exposure(departs, [(5.0, 5.0)]) == [0]

    def test_exposure_is_per_window_in_order(self, tmp_path):
        from traffic_sim.simulation.heldout_selection import window_exposure
        assert window_exposure([1.0, 2.0, 50.0],
                               [(0.0, 10.0), (10.0, 100.0)]) == [2, 1]
