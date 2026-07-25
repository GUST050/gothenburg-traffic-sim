"""The online-latency harness must be trustworthy before it is useful.

A benchmark that can be satisfied by a changed answer, a different endpoint,
an error swallowed as a fast response, or a report missing its provenance is
worse than no benchmark: it converts an unmeasured regression into a claimed
speed-up. These tests hold the harness to the frozen contract, and to
refusing anything the safe default mode must not touch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import benchmark_online_latency as bench   # noqa: E402

CONTRACT = bench.load_contract()


def _report(**overrides):
    """A minimal passing report, so each test can break exactly one thing."""
    provenance = {field: "value" for field in CONTRACT["required_provenance"]}
    provenance.update({
        "measurement": "cached_render",
        "endpoint_identity": "fixture:web/data/scenarios/index.json",
        "cpu_count": 10, "candidate_count": 0, "git_dirty": False,
        "seeds": [1000, 1001, 1002], "warmup_trials": 1, "measured_trials": 3,
    })
    report = {
        "schema_version": 1,
        "kind": "online_latency_benchmark_report",
        "contract_version": CONTRACT["contract_version"],
        "provenance": provenance,
        "measured": {
            "measured_trials": 3,
            "p50_seconds": 0.2, "p95_seconds": 0.4, "max_seconds": 0.5,
            "response_bytes": 1024, "errors": [],
            "semantic_digest": "d" * 64, "distinct_semantic_digests": 1,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(report.get(key), dict):
            report[key] = {**report[key], **value}
        else:
            report[key] = value
    return report


class TestContract:

    def test_the_three_measurements_are_distinct_and_frozen(self):
        budgets = {name: entry["p95_seconds_maximum"]
                   for name, entry in CONTRACT["measurements"].items()}
        assert budgets == {"cached_render": 2.0,
                           "async_acknowledgement": 1.0,
                           "validated_completion": 10.0}

    def test_validated_completion_is_not_safe_by_default(self):
        entry = CONTRACT["measurements"]["validated_completion"]
        assert entry["safe_in_default_mode"] is False
        assert entry["requires_explicit_approval"] is True


class TestPercentile:

    def test_interpolates_between_closest_ranks(self):
        values = [1.0, 2.0, 3.0, 4.0]
        assert bench.percentile(values, 0.5) == 2.5
        assert bench.percentile(values, 0.0) == 1.0
        assert bench.percentile(values, 1.0) == 4.0
        assert bench.percentile(values, 0.95) == pytest.approx(3.85)

    def test_single_sample_and_empty(self):
        assert bench.percentile([0.7], 0.95) == 0.7
        with pytest.raises(ValueError):
            bench.percentile([], 0.5)

    def test_order_does_not_matter(self):
        assert bench.percentile([3.0, 1.0, 2.0], 0.5) == 2.0


class TestSemanticDigest:
    VOLATILE = CONTRACT["semantic_digest"]["volatile_keys"]

    def test_volatile_fields_do_not_change_the_digest(self):
        base = {"generated_at": "t1", "elapsed_s": 12.5,
                "scenario": {"flows": {"a_b_0": [1, 2, 3]}}}
        later = {"generated_at": "t2", "elapsed_s": 99.9,
                 "scenario": {"flows": {"a_b_0": [1, 2, 3]}}}
        assert (bench.semantic_digest(base, self.VOLATILE)
                == bench.semantic_digest(later, self.VOLATILE))

    def test_any_real_change_does_change_it(self):
        base = {"scenario": {"flows": {"a_b_0": [1, 2, 3]}}}
        for changed in ({"scenario": {"flows": {"a_b_0": [1, 2, 4]}}},
                        {"scenario": {"flows": {"a_b_1": [1, 2, 3]}}},
                        {"scenario": {"flows": {"a_b_0": [1, 2, 3]},
                                      "status": "inconclusive"}}):
            assert (bench.semantic_digest(base, self.VOLATILE)
                    != bench.semantic_digest(changed, self.VOLATILE))

    def test_key_order_and_nesting_are_stable(self):
        left = {"b": [{"y": 2, "x": 1}], "a": 1}
        right = {"a": 1, "b": [{"x": 1, "y": 2}]}
        assert (bench.semantic_digest(left, self.VOLATILE)
                == bench.semantic_digest(right, self.VOLATILE))


class TestSafeDefaultMode:
    """The harness must refuse rather than quietly measure something unsafe."""

    def test_mutating_endpoints_are_refused(self):
        for path in ("/api/close?edges=a_b_0", "/api/recalibrate?date=2027-01-01",
                     "/api/monthly", "/api/optimize", "/api/suggest"):
            with pytest.raises(bench.BenchmarkRefused, match="mutating"):
                bench.check_safe_target(f"http://127.0.0.1:8000{path}",
                                        "cached_render", CONTRACT)

    def test_non_loopback_targets_are_refused(self):
        with pytest.raises(bench.BenchmarkRefused, match="non-loopback"):
            bench.check_safe_target("http://example.com/data/x.json",
                                    "cached_render", CONTRACT)

    def test_validated_completion_needs_explicit_approval(self):
        url = "http://127.0.0.1:8000/data/scenarios/index.json"
        with pytest.raises(bench.BenchmarkRefused, match="not safe in default"):
            bench.check_safe_target(url, "validated_completion", CONTRACT)
        bench.check_safe_target(url, "validated_completion", CONTRACT,
                                approved_by="sol+user 2026-07-22")

    def test_read_only_local_target_is_allowed(self):
        bench.check_safe_target("http://127.0.0.1:8000/data/scenarios/index.json",
                                "cached_render", CONTRACT)

    def test_unknown_measurement_is_refused(self):
        with pytest.raises(bench.BenchmarkRefused, match="unknown measurement"):
            bench.check_safe_target("http://127.0.0.1:8000/x.json",
                                    "make_it_fast", CONTRACT)


class TestMeasure:
    VOLATILE = CONTRACT["semantic_digest"]["volatile_keys"]

    def test_warmups_are_discarded_and_trials_counted(self):
        calls = []

        def sampler():
            calls.append(1)
            return json.dumps({"generated_at": len(calls), "v": 1}).encode(), 200

        result = bench.measure(sampler, warmup=2, trials=3,
                               volatile_keys=self.VOLATILE)
        assert len(calls) == 5
        assert result["measured_trials"] == 3
        assert result["distinct_semantic_digests"] == 1   # volatile-only change
        assert result["errors"] == []

    def test_http_errors_are_recorded_not_timed_away(self):
        def sampler():
            return b"{}", 503

        result = bench.measure(sampler, warmup=0, trials=2,
                               volatile_keys=self.VOLATILE)
        assert result["measured_trials"] == 0
        assert len(result["errors"]) == 2
        assert result["p95_seconds"] is None

    def test_a_changing_answer_is_visible_as_non_determinism(self):
        values = iter([1, 2, 3])

        def sampler():
            return json.dumps({"v": next(values)}).encode(), 200

        result = bench.measure(sampler, warmup=0, trials=3,
                               volatile_keys=self.VOLATILE)
        assert result["distinct_semantic_digests"] == 3
        assert result["semantic_digest"] is None

    def test_fixture_sampler_needs_no_server(self, tmp_path):
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps({"flows": {"a_b_0": [1, 2]}}))
        result = bench.measure(bench.fixture_sampler(path), warmup=1, trials=2,
                               volatile_keys=self.VOLATILE)
        assert result["measured_trials"] == 2
        assert result["response_bytes"] == len(path.read_bytes())
        assert result["errors"] == []


class TestVerdict:

    def test_within_budget_passes(self):
        assert bench.evaluate(_report(), CONTRACT)["status"] == "pass"

    def test_over_budget_fails_with_the_contract_threshold(self):
        verdict = bench.evaluate(_report(measured={"p95_seconds": 2.4, "max_seconds": 2.5}), CONTRACT)
        assert verdict["status"] == "fail"
        assert verdict["p95_seconds_maximum"] == 2.0
        assert any("exceeds" in problem for problem in verdict["problems"])

    def test_the_one_second_acknowledgement_budget_is_enforced(self):
        report = _report(provenance={"measurement": "async_acknowledgement"},
                         measured={"p95_seconds": 1.4, "max_seconds": 1.5})
        assert bench.evaluate(report, CONTRACT)["status"] == "fail"

    def test_missing_provenance_fails_even_when_fast(self):
        report = _report()
        del report["provenance"]["model_version"]
        verdict = bench.evaluate(report, CONTRACT)
        assert verdict["status"] == "fail"
        assert any("model_version" in problem for problem in verdict["problems"])

    def test_errors_or_non_determinism_fail_even_when_fast(self):
        assert bench.evaluate(_report(measured={"errors": ["HTTP 500"]}),
                              CONTRACT)["status"] == "fail"
        assert bench.evaluate(_report(measured={"distinct_semantic_digests": 2}),
                              CONTRACT)["status"] == "fail"


class TestComparison:
    """A difference in seconds only means something when both runs measured
    the same thing and got the same answer."""

    def test_identical_identity_and_answer_allows_a_speed_claim(self):
        reference = _report(measured={"p95_seconds": 0.9, "max_seconds": 1.0})
        result = bench.compare(_report(measured={"p95_seconds": 0.4}),
                               reference, CONTRACT)
        assert result["status"] == "pass"
        assert result["p95_delta_seconds"] == pytest.approx(-0.5)
        assert result["speed_claim_allowed"] is True

    def test_a_changed_answer_is_never_a_speed_improvement(self):
        reference = _report(measured={"p95_seconds": 0.9, "max_seconds": 1.0,
                                      "semantic_digest": "e" * 64})
        result = bench.compare(_report(measured={"p95_seconds": 0.1}),
                               reference, CONTRACT)
        assert result["status"] == "fail"
        assert result["speed_claim_allowed"] is False
        assert any("semantic_digest mismatch" in p for p in result["problems"])

    def test_a_different_endpoint_or_measurement_fails(self):
        other_endpoint = _report(provenance={"endpoint_identity": "fixture:other"})
        assert bench.compare(_report(), other_endpoint, CONTRACT)["status"] == "fail"
        other_measurement = _report(provenance={"measurement": "async_acknowledgement"})
        assert bench.compare(_report(), other_measurement,
                             CONTRACT)["status"] == "fail"

    def test_a_different_contract_version_fails(self):
        result = bench.compare(_report(), _report(contract_version="online_latency_v0"),
                               CONTRACT)
        assert result["status"] == "fail"
        assert any("contract_version" in p for p in result["problems"])

    def test_errors_on_either_side_fail_the_comparison(self):
        result = bench.compare(_report(measured={"errors": ["HTTP 503"]}),
                               _report(), CONTRACT)
        assert result["status"] == "fail"
        assert result["speed_claim_allowed"] is False

    def test_being_slower_is_reported_but_not_claimed(self):
        result = bench.compare(
            _report(measured={"p95_seconds": 1.5, "max_seconds": 1.6}),
            _report(measured={"p95_seconds": 0.4}), CONTRACT)
        assert result["p95_delta_seconds"] == pytest.approx(1.1)
        assert result["speed_claim_allowed"] is False


class TestCommandLine:

    def _args(self, tmp_path, **extra):
        fixture = tmp_path / "cached.json"
        fixture.write_text(json.dumps({"flows": {"a_b_0": [1, 2, 3]}}))
        argv = ["--measurement", "cached_render", "--fixture", str(fixture),
                "--cache-state", "precomputed", "--candidate-count", "0",
                "--seeds", "1000,1001,1002", "--model-version", "test_v1",
                "--warmup", "1", "--trials", "3"]
        for key, value in extra.items():
            argv += [f"--{key.replace('_', '-')}", str(value)]
        return argv

    def test_a_fixture_run_writes_a_complete_report(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        code = bench.main(self._args(tmp_path, write=out))
        capsys.readouterr()
        report = json.loads(out.read_text())
        assert code == 0
        assert report["verdict"]["status"] == "pass"
        assert not bench.missing_provenance(report, CONTRACT)
        assert report["measured"]["distinct_semantic_digests"] == 1
        assert report["contract_version"] == CONTRACT["contract_version"]

    def test_the_cli_refuses_a_mutating_target(self, tmp_path, capsys):
        code = bench.main(["--measurement", "cached_render",
                           "--target", "http://127.0.0.1:8000/api/close?edges=a",
                           "--cache-state", "warm", "--candidate-count", "1",
                           "--seeds", "1000", "--model-version", "v"])
        assert code == 2
        assert "refused" in capsys.readouterr().err

    def test_the_cli_refuses_an_unapproved_completion_benchmark(self, tmp_path,
                                                                capsys):
        argv = self._args(tmp_path)
        argv[argv.index("cached_render")] = "validated_completion"
        assert bench.main(argv) == 2
        assert "approval" in capsys.readouterr().err

    def test_comparing_against_a_reference_round_trips(self, tmp_path, capsys):
        first = tmp_path / "a.json"
        bench.main(self._args(tmp_path, write=first))
        second = tmp_path / "b.json"
        code = bench.main(self._args(tmp_path, write=second, compare=first))
        capsys.readouterr()
        comparison = json.loads(second.read_text())["comparison"]
        assert code == 0
        assert comparison["status"] == "pass"
        assert "p95_delta_seconds" in comparison


class TestReadOnlyStatusEndpoints:
    """Sol blocker 1: the substring denylist refused every real status GET,
    which is exactly what the 1 s acknowledgement budget must measure."""

    STATUS = ("/api/close/status", "/api/recalibrate/status",
              "/api/suggest_closure/status", "/api/optimize_signals/status",
              "/api/monthly_search/status", "/api/ping", "/api/jobs")
    JOB_START = ("/api/close", "/api/recalibrate", "/api/suggest_closure",
                 "/api/optimize_signals", "/api/monthly_search", "/api/cancel")

    def test_every_status_endpoint_is_measurable(self):
        for path in self.STATUS:
            bench.check_safe_target(f"http://127.0.0.1:8000{path}",
                                    "async_acknowledgement", CONTRACT)

    def test_their_job_start_paths_stay_refused(self):
        for path in self.JOB_START:
            with pytest.raises(bench.BenchmarkRefused, match="mutating"):
                bench.check_safe_target(f"http://127.0.0.1:8000{path}",
                                        "async_acknowledgement", CONTRACT)

    def test_a_status_path_with_a_query_string_is_still_exact(self):
        """Allow-listing is by exact path; the query cannot smuggle a job."""
        bench.check_safe_target(
            "http://127.0.0.1:8000/api/close/status?job=1",
            "async_acknowledgement", CONTRACT)
        with pytest.raises(bench.BenchmarkRefused, match="mutating"):
            bench.check_safe_target(
                "http://127.0.0.1:8000/api/close/status/../close",
                "async_acknowledgement", CONTRACT)

    def test_the_allow_list_is_frozen_in_the_contract_not_the_code(self):
        listed = set(CONTRACT["safe_default_mode"]["read_only_endpoints"])
        assert set(self.STATUS) == listed


class TestRedirectsAreRefused:
    """Sol blocker 2: urlopen follows redirects, and the safety check only saw
    the URL that was typed."""

    def _handler(self):
        return bench.RefuseRedirects()

    def test_a_redirect_to_a_mutating_path_is_refused(self):
        with pytest.raises(bench.BenchmarkRefused, match="redirect"):
            self._handler().redirect_request(
                None, None, 302, "Found", {},
                "http://127.0.0.1:8000/api/close?edges=a_b_0")

    def test_a_redirect_to_another_host_is_refused(self):
        with pytest.raises(bench.BenchmarkRefused, match="example.com"):
            self._handler().redirect_request(
                None, None, 301, "Moved", {}, "http://example.com/x.json")

    def test_even_a_harmless_looking_redirect_is_refused(self):
        """A benchmark that cannot name the resource it timed is not evidence."""
        with pytest.raises(bench.BenchmarkRefused):
            self._handler().redirect_request(
                None, None, 307, "Temporary Redirect", {},
                "http://127.0.0.1:8000/data/scenarios/index.json")

    def test_the_http_sampler_installs_the_refusing_handler(self):
        sampler = bench.http_sampler("http://127.0.0.1:8000/api/ping", 1.0)
        opener = sampler.__closure__[0].cell_contents
        assert any(isinstance(handler, bench.RefuseRedirects)
                   for handler in opener.handlers)


class TestMeasuredValuesMustProveTheAnswer:
    """Sol blocker 3: required keys existed, so a null digest passed."""

    def test_a_null_digest_fails_even_with_plausible_timings(self):
        report = _report(measured={"semantic_digest": None})
        problems = bench.invalid_measured(report)
        assert any("semantic_digest" in problem for problem in problems)
        assert bench.evaluate(report, CONTRACT)["status"] == "fail"

    def test_a_null_digest_on_either_side_blocks_a_speed_claim(self):
        null_digest = _report(measured={"semantic_digest": None,
                                        "p95_seconds": 0.1})
        good = _report()
        for new, reference in ((null_digest, good), (good, null_digest)):
            result = bench.compare(new, reference, CONTRACT)
            assert result["status"] == "fail"
            assert result["speed_claim_allowed"] is False
            assert "p95_delta_seconds" not in result

    def test_malformed_measured_values_are_rejected(self):
        for broken in ({"semantic_digest": "zz" * 32},
                       {"distinct_semantic_digests": 2},
                       {"measured_trials": 0},
                       {"response_bytes": 0},
                       {"p95_seconds": -1.0},
                       {"p95_seconds": float("inf")},
                       {"p50_seconds": 0.9},          # p50 > p95
                       {"errors": "none"}):
            assert bench.invalid_measured(_report(measured=broken)), broken

    def test_a_coherent_report_has_no_value_problems(self):
        assert bench.invalid_measured(_report()) == []
