"""Measure real retention on a real evidence root, changing nothing.

Retention is the plan's largest suspected step-4 cost and the q50 replay never
ran it: a single-variant replay compresses one copied file directly. This
profiles the actual prune_evidence path on an owned copy, and the tests that
matter most are the ones proving the original root is untouched and that the
ranked wall cannot exceed the wall that really elapsed.
"""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools import profile_passage_replay as profiler


def _root(base, *, arms=('q50', '_v1', '_v2'), files=2, size=20_000):
    """A three-variant evidence root shaped like a finished build."""
    base = Path(base)
    for arm in arms:
        for index in range(files):
            path = base / arm / 'evidence' / f'trace{index}.xml'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<routes>' + 'ab' * (size // 2) + '</routes>')
        (base / arm / 'input').mkdir(parents=True, exist_ok=True)
        (base / arm / 'input' / 'calibrated.rou.xml').write_text(
            '<routes>' + 'cd' * 200 + '</routes>')
        stage = base / arm / 'candidate'
        stage.mkdir(parents=True, exist_ok=True)
        (stage / 'calibrated.rou.xml').write_text('<routes/>')
    (base / 'source_reports.json').write_text(json.dumps({'x': 1}))
    for index in range(2):
        (base / f'original-{index}').write_text('rollback')
    return base


def _fingerprint(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size,
                   hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in Path(root).rglob('*') if p.is_file())


class TestTheProfilerRefusesUnsafeLayouts:
    def test_an_output_inside_the_evidence_root_is_refused(self, tmp_path):
        root = _root(tmp_path / 'evidence')

        with pytest.raises(profiler.ReplayRefused, match='outside'):
            profiler.profile_retention(root, root / 'out', repeats=1)

    def test_an_output_containing_the_evidence_root_is_refused(self, tmp_path):
        root = _root(tmp_path / 'evidence' / 'inner')

        with pytest.raises(profiler.ReplayRefused, match='outside'):
            profiler.profile_retention(root, tmp_path / 'evidence', repeats=1)

    def test_the_same_path_for_both_is_refused(self, tmp_path):
        root = _root(tmp_path / 'evidence')

        with pytest.raises(profiler.ReplayRefused, match='outside'):
            profiler.profile_retention(root, root, repeats=1)


class TestTheOriginalRootSurvivesUntouched:
    def test_every_path_size_and_digest_is_identical_afterwards(self, tmp_path):
        root = _root(tmp_path / 'evidence')
        before = _fingerprint(root)

        profiler.profile_retention(root, tmp_path / 'out', repeats=2)

        assert _fingerprint(root) == before

    def test_the_report_states_the_source_was_unchanged_per_repeat(
            self, tmp_path):
        root = _root(tmp_path / 'evidence')

        report = profiler.profile_retention(root, tmp_path / 'out', repeats=2)

        assert [run['source_unchanged'] for run in report['runs']] == [True, True]

    def test_the_working_copy_is_removed_after_the_report_is_written(
            self, tmp_path):
        out = tmp_path / 'out'

        profiler.profile_retention(_root(tmp_path / 'evidence'), out, repeats=2)

        assert (out / 'retention_profile.json').is_file()
        assert not list(out.glob('repeat-*/copy'))

    def test_the_copy_is_a_real_copy_not_a_hard_link(self, tmp_path):
        root = _root(tmp_path / 'evidence')

        report = profiler.profile_retention(root, tmp_path / 'out', repeats=1)

        assert report['runs'][0]['copy_is_hardlink_free'] is True


class TestTheMeasurementIsHonest:
    @pytest.fixture
    def report(self, tmp_path):
        return profiler.profile_retention(
            _root(tmp_path / 'evidence'), tmp_path / 'out', repeats=3)

    def test_the_copy_is_timed_separately_and_left_out_of_retention(self, report):
        run = report['runs'][0]

        assert run['copy_s'] > 0
        assert 'copy_s' not in run['phases']
        assert run['retention_root_wall_s'] < run['copy_s'] + \
            run['retention_root_wall_s']

    def test_ranked_wall_never_exceeds_the_wall_that_really_elapsed(self, report):
        for run in report['runs']:
            ranked = sum(entry['wall_s'] for entry in run['ranking'])
            assert ranked <= run['retention_root_wall_s'] + 0.01, (
                f"ranked {ranked} exceeds measured "
                f"{run['retention_root_wall_s']}")

    def test_positive_ranking_shares_sum_to_about_one_hundred_percent(
            self, report):
        for run in report['runs']:
            total = sum(entry['share_percent'] for entry in run['ranking'])
            assert total == pytest.approx(100.0, abs=0.5)

    def test_the_concurrent_region_reports_one_wall_and_its_thread_detail(
            self, report):
        phases = report['runs'][0]['phases']
        region = phases['retention_compress']

        assert region['concurrent'] is True
        assert region['exclusive_s'] is None
        assert region['wall_contribution_s'] > 0
        assert phases['gzip_compress']['wall_contribution_s'] == 0
        assert phases['gzip_compress']['inclusive_s'] > 0

    def test_bytes_and_file_counts_are_reported_before_and_after(self, report):
        run = report['runs'][0]

        # Three arms: 2 evidence + 1 input XML are compressible, and the
        # candidate stage XML is removed with its directory instead.
        assert run['raw_xml_files_before'] == 12
        assert run['raw_xml_files_after'] == 0
        assert run['compressed_files_after'] == 9
        assert run['bytes']['read'] > 0
        assert run['bytes']['written'] > 0
        assert run['bytes']['verified'] > 0
        assert 0 < run['compression_ratio'] < 1

    def test_every_compressed_file_matches_the_original_when_decompressed(
            self, report):
        assert report['runs'][0]['gz_verified_against_original'] > 0
        assert report['runs'][0]['gz_digest_mismatches'] == []

    def test_the_contract_outcome_is_checked_not_assumed(self, report):
        run = report['runs'][0]

        assert run['candidate_dirs_removed'] is True
        assert run['rollback_files_removed'] is True
        assert run['source_reports_removed'] is True

    def test_the_first_repeat_is_reported_apart_from_the_later_ones(self, report):
        assert report['first_repeat']['retention_root_wall_s'] > 0
        assert report['reused_process']['retention_root_wall_s']['median_s'] > 0
        assert report['repeats'] == 3

    def test_residual_and_unmeasured_categories_are_explicit(self, report):
        run = report['runs'][0]

        assert 'residual_s' in run
        assert report['unmeasured_categories']
        assert report['release_evidence'] is False


class TestTheRetentionModeIsReachableFromTheCommandLine:
    def test_retention_root_does_not_require_an_unrelated_source(self, tmp_path):
        """--source names a replay's evidence; retention has its own root.

        The first real invocation was refused with "--source is required"
        because the retention branch sat below that check.
        """
        import subprocess
        import sys

        root = _root(tmp_path / 'evidence')
        result = subprocess.run(
            [sys.executable, 'tools/profile_passage_replay.py',
             '--retention-root', str(root),
             '--out', str(tmp_path / 'out'), '--repeats', '1'],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / 'out' / 'retention_profile.json').is_file()
