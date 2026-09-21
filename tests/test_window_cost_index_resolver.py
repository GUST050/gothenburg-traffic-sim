from __future__ import annotations

import json

import pytest

from traffic_sim.simulation import window_cost_index_resolver as resolver
from traffic_sim.simulation.window_cost_index import WindowCostIndex, write_index


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _profile_and_registration(tmp_path, *, profile_key="profile-a",
                              registration_key="registration-a"):
    profile_path = tmp_path / "profile.json"
    registration_path = tmp_path / "registration.json"
    _write(profile_path, {"content_key": profile_key})
    _write(registration_path, {"content_key": registration_key})
    return profile_path, registration_path


def _index(records=None):
    return WindowCostIndex(
        bound_identity={"schema": "x", "search_content_key": "s"},
        records=records or {
            "unit-a": {"schedule_id": "sched-a",
                      "records": [{"demand_variant": v} for v in
                                  ("q50",)]},
        },
    )


def test_identity_key_is_deterministic_and_order_independent():
    a = resolver.identity_key("p1", "r1")
    b = resolver.identity_key("p1", "r1")
    c = resolver.identity_key("r1", "p1")
    assert a == b
    assert a != c


def test_warm_hit_loads_an_already_built_matching_index(tmp_path):
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"
    key = resolver.identity_key("profile-a", "registration-a")
    index_path = resolver.canonical_index_path(index_root, key)
    write_index(index_path, _index())

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1,
        auto_build=False)

    assert result.source == "warm_hit"
    assert result.index is not None
    assert result.build_attempted is False


def test_missing_index_without_auto_build_falls_back(tmp_path):
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1,
        auto_build=False)

    assert result.source == "fallback"
    assert result.index is None
    assert result.build_attempted is False
    assert "auto-build is not enabled" in result.reason


def test_wrong_population_is_never_a_hit(tmp_path):
    """A completeness mismatch must fall back, never load a partial index."""
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"
    key = resolver.identity_key("profile-a", "registration-a")
    index_path = resolver.canonical_index_path(index_root, key)
    write_index(index_path, _index())

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        # Ask for a population the stored index does not have.
        expected_daily_units=2, expected_variant_records=2,
        auto_build=False)

    assert result.source == "fallback"
    assert result.index is None


def test_missing_profile_content_key_falls_back_without_touching_disk(tmp_path):
    profile_path = tmp_path / "profile.json"
    registration_path = tmp_path / "registration.json"
    _write(profile_path, {})  # no content_key
    _write(registration_path, {"content_key": "r"})

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=tmp_path / "index-root", expected_identity=None,
        expected_daily_units=1, expected_variant_records=1,
        auto_build=False)

    assert result.source == "fallback"
    assert result.index is None
    assert result.index_path is None


def test_auto_build_invokes_the_guarded_builder_once_and_loads_its_output(
        tmp_path, monkeypatch):
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"
    key = resolver.identity_key("profile-a", "registration-a")
    index_path = resolver.canonical_index_path(index_root, key)

    calls = []

    def fake_run_guarded_build(*, profile_path, registration_path, index_path,
                               evidence_id, guarded_build_script):
        calls.append(evidence_id)
        write_index(index_path, _index())
        return {"state": "passed"}

    monkeypatch.setattr(resolver, "_run_guarded_build", fake_run_guarded_build)

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1,
        auto_build=True)

    assert result.source == "cold_build"
    assert result.index is not None
    assert result.build_attempted is True
    assert result.lock_acquired is True
    assert len(calls) == 1


def test_a_second_caller_after_a_successful_build_gets_a_warm_hit_not_a_rebuild(
        tmp_path, monkeypatch):
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"

    calls = []

    def fake_run_guarded_build(*, profile_path, registration_path, index_path,
                               evidence_id, guarded_build_script):
        calls.append(evidence_id)
        write_index(index_path, _index())
        return {"state": "passed"}

    monkeypatch.setattr(resolver, "_run_guarded_build", fake_run_guarded_build)

    first = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1, auto_build=True)
    second = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1, auto_build=True)

    assert first.source == "cold_build"
    assert second.source == "warm_hit"
    assert len(calls) == 1


def test_a_failed_build_falls_back_rather_than_raising(tmp_path, monkeypatch):
    profile_path, registration_path = _profile_and_registration(tmp_path)
    index_root = tmp_path / "index-root"

    def fake_run_guarded_build(*, profile_path, registration_path, index_path,
                               evidence_id, guarded_build_script):
        # No index written: simulates a guarded build that stopped fail-closed.
        return {"state": "failed", "reason": "footprint_over_limit"}

    monkeypatch.setattr(resolver, "_run_guarded_build", fake_run_guarded_build)

    result = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=registration_path,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1, auto_build=True)

    assert result.source == "fallback"
    assert result.index is None
    assert result.build_attempted is True
    assert result.build_outcome["state"] == "failed"


def test_different_registration_never_reuses_another_edges_index(tmp_path):
    """Two different registrations (e.g. two edges) must never collide."""
    profile_path = tmp_path / "profile.json"
    reg_a = tmp_path / "reg-a.json"
    reg_b = tmp_path / "reg-b.json"
    _write(profile_path, {"content_key": "profile-x"})
    _write(reg_a, {"content_key": "edge-a"})
    _write(reg_b, {"content_key": "edge-b"})
    index_root = tmp_path / "index-root"

    key_a = resolver.identity_key("profile-x", "edge-a")
    key_b = resolver.identity_key("profile-x", "edge-b")
    assert key_a != key_b
    write_index(resolver.canonical_index_path(index_root, key_a), _index())

    result_for_b = resolver.resolve_window_cost_index(
        profile_path=profile_path, registration_path=reg_b,
        index_root=index_root, expected_identity=None,
        expected_daily_units=1, expected_variant_records=1, auto_build=False)

    assert result_for_b.source == "fallback"
    assert result_for_b.index is None
