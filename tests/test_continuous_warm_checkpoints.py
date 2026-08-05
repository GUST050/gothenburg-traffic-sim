"""Many warm-state snapshots from ONE simulation run.

WHY THIS EXISTS (2026-08-05). The annual warm bank rebuilt every checkpoint in
its own chained SUMO process: measured 1.09 s/step lower bound against 104.6 s
for a whole day, while one continuous run dropping all 96 checkpoints cost
5.04 s — indistinguishable from the 5.06 s the same run costs saving nothing.
Periodic checkpointing is free; rebuilding the prefix is what costs.

The risk this file guards is narrow and specific: SUMO pairs
``--save-state.times`` with ``--save-state.files`` POSITIONALLY, so a repeated
time or a repeated path silently yields fewer states than were requested. That
surfaces much later as a missing checkpoint in the bank, far from its cause.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traffic_sim.simulation.warm_state_cache import (
    STATE_PRECISION, save_state_arguments, save_states_arguments)


def _flag(args, name):
    return args[args.index(name) + 1]


class TestSingleSnapshotIsUnchanged:
    """The existing warm bank must not be reinterpreted by this change."""

    def test_one_checkpoint_matches_the_old_single_form_exactly(self, tmp_path):
        path = tmp_path / "state.xml.gz"
        assert save_states_arguments([(24_300, path)]) == save_state_arguments(
            path, warmup_end_s=24_300)

    def test_precision_and_rng_capture_are_carried_through(self, tmp_path):
        args = save_states_arguments([(900, tmp_path / "a.xml.gz")])
        assert _flag(args, "--save-state.rng") == "true"
        assert _flag(args, "--save-state.precision") == str(STATE_PRECISION)


class TestManySnapshots:
    def test_times_and_files_pair_positionally(self, tmp_path):
        points = [(900, tmp_path / "a.xml.gz"),
                  (1800, tmp_path / "b.xml.gz"),
                  (2700, tmp_path / "c.xml.gz")]
        args = save_states_arguments(points)
        assert _flag(args, "--save-state.times") == "900,1800,2700"
        files = _flag(args, "--save-state.files").split(",")
        assert files == [str(p.resolve()) for _t, p in points]

    def test_a_whole_day_of_quarter_hour_checkpoints(self, tmp_path):
        points = [(q * 900, tmp_path / f"s{q}.xml.gz") for q in range(1, 97)]
        args = save_states_arguments(points)
        assert len(_flag(args, "--save-state.times").split(",")) == 96
        assert len(_flag(args, "--save-state.files").split(",")) == 96

    def test_paths_are_absolute_because_sumo_runs_in_its_own_cwd(self, tmp_path):
        args = save_states_arguments([(900, Path("relative.xml.gz"))])
        assert Path(_flag(args, "--save-state.files")).is_absolute()


class TestRefusals:
    def test_repeated_time_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="strictly increasing"):
            save_states_arguments([(900, tmp_path / "a"), (900, tmp_path / "b")])

    def test_out_of_order_time_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="strictly increasing"):
            save_states_arguments([(1800, tmp_path / "a"), (900, tmp_path / "b")])

    def test_duplicate_path_is_refused(self, tmp_path):
        """Two times writing one path leaves only the later state."""
        same = tmp_path / "a.xml.gz"
        with pytest.raises(ValueError, match="duplicate checkpoint path"):
            save_states_arguments([(900, same), (1800, same)])

    def test_non_positive_and_non_integer_times_are_refused(self, tmp_path):
        for bad in (0, -900, True, 900.0):
            with pytest.raises(ValueError, match="positive integers"):
                save_states_arguments([(bad, tmp_path / "a")])

    def test_empty_is_refused(self):
        with pytest.raises(ValueError, match="at least one checkpoint"):
            save_states_arguments([])


class TestInvocationWiring:
    """build_sumo_invocation must validate the whole plan before SUMO starts."""

    def _invoke(self, tmp_path, **kwargs):
        import run_scenario
        return run_scenario.build_sumo_invocation(
            seed=1000, route_path=tmp_path / "r.rou.xml", add_paths=[],
            duration_s=86_400, home=Path("/nonexistent-sumo-home"),
            net_path=tmp_path / "net.net.xml", work_dir=tmp_path, **kwargs)

    def test_checkpoints_reach_the_command_line(self, tmp_path):
        points = [(900, tmp_path / "a.xml.gz"), (1800, tmp_path / "b.xml.gz")]
        cmd, metric_paths, _cwd = self._invoke(
            tmp_path, save_state_checkpoints=points)
        assert _flag(cmd, "--save-state.times") == "900,1800"
        assert metric_paths["states"] == tuple(
            (t, Path(p)) for t, p in points)

    def test_a_checkpoint_at_or_past_the_exclusive_end_is_refused(self, tmp_path):
        """SUMO never writes a state requested exactly at --end, so a plan
        that asks for one would silently come back short."""
        with pytest.raises(ValueError, match="exclusive simulation end"):
            self._invoke(tmp_path, save_state_checkpoints=[
                (900, tmp_path / "a"), (86_400 + 3600, tmp_path / "b")])

    def test_mixing_the_two_forms_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="never both"):
            self._invoke(tmp_path, save_state_path=tmp_path / "one",
                         save_state_time_s=900,
                         save_state_checkpoints=[(1800, tmp_path / "b")])

    def test_the_single_form_still_reports_its_state_path(self, tmp_path):
        _cmd, metric_paths, _cwd = self._invoke(
            tmp_path, save_state_path=tmp_path / "one.xml.gz",
            save_state_time_s=900)
        assert metric_paths["state"] == tmp_path / "one.xml.gz"
        assert "states" not in metric_paths
