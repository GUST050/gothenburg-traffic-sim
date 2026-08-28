"""PR E: cost-ordered verification must be the exhaustive answer, cheaper.

Every test here is a differential test against the reference path. The claim
being defended is narrow and total: for the same candidates, costs, policy and
hard-failure outcomes, the cost-ordered scan produces the SAME pilot status and
the SAME `selected_ids` as verifying everything — while simulating fewer
candidates.

The failure mode this guards against is quiet truncation. A cutoff that used
`<` instead of `<=`, or that stopped before `minimum_finalists` viable
candidates existed, would still look like a working search and would still
return finalists; it would just return the wrong ones, sometimes.
"""

from __future__ import annotations

import itertools
import json
import random

import pytest

from traffic_sim.simulation.closure_ranking import ClosureCost
from traffic_sim.simulation.cost_ordered_search import (
    CostOrderedCandidate,
    CostOrderedState,
    NO_DETOUR_FAILURE,
    compare_with_exhaustive,
    identity_key,
    plan_order,
    replay_shadow,
    run_cost_ordered_search,
)
from traffic_sim.simulation.finalist_decision import (
    RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
    TIMEOUT_IDENTITY_SCHEMA,
    CandidateEvidence,
    PairedObservation,
    TimeoutIdentity,
)
from traffic_sim.simulation.pilot_selection import (
    PilotPolicy,
    select_pilot_finalists,
)

PROVIDER_IDENTITY = {"schema": "deterministic_closure_disruption_v1",
                     "network": {"sha256": "net"}}
SEARCH_KEY = "0123456789abcdef0123"


def _policy(minimum=2, maximum=6, repetitions=1) -> PilotPolicy:
    return PilotPolicy(
        retention_band_s=300.0,
        repetitions_per_variant=repetitions,
        minimum_finalists=minimum,
        maximum_finalists=maximum,
    )


def _variant_records(hours, *, metres=None, affected=None, no_detour=0):
    """Three variant records whose field-wise worst is exactly `hours`."""
    return tuple({
        "demand_variant": variant,
        "vehicles_affected": int(affected if affected is not None
                                 else round(hours * 10)),
        "vehicles_considered": 1000,
        "vehicles_no_detour": int(no_detour),
        "added_vehicle_hours": float(hours),
        "added_metres_total": float(metres if metres is not None
                                    else hours * 100.0),
    } for variant in ("q10", "q50", "q90"))


def _candidate(index, hours, *, metres=None, affected=None, no_detour=0):
    candidate_id = f"closure-{index:04d}"
    records = _variant_records(hours, metres=metres, affected=affected,
                               no_detour=no_detour)
    cost = ClosureCost(
        candidate_id=candidate_id,
        added_vehicle_hours=float(hours),
        added_metres_total=float(records[0]["added_metres_total"]),
        vehicles_affected=int(records[0]["vehicles_affected"]),
        vehicles_no_detour=int(no_detour),
    )
    return CostOrderedCandidate(candidate_id=candidate_id, cost=cost,
                                disruption=records)


def _evidence(candidate, policy, *, failures=()):
    """Complete matched pilot evidence for one candidate."""
    observations = []
    if not failures:
        for variant in policy.variants:
            for repetition in range(policy.repetitions_per_variant):
                seed = 1000 + repetition * 3 + ("q10", "q50", "q90").index(variant)
                observations.append(PairedObservation(
                    candidate_id=candidate.candidate_id,
                    demand_variant=variant,
                    seed=seed,
                    baseline_time_loss_s=100.0,
                    candidate_time_loss_s=100.0 + candidate.cost.added_vehicle_hours,
                    matched_baseline_id="baseline",
                    provenance_key="provenance",
                ))
    return CandidateEvidence(
        candidate_id=candidate.candidate_id,
        observations=tuple(observations),
        hard_failures=tuple(failures),
        disruption=tuple(candidate.disruption),
    )


def _run(candidates, policy, *, failures=(), band=0.0, state=None,
         verified_evidence=None, stop_after=None, disable_early_stop=False,
         timeouts=()):
    """Cost-ordered scan with a recording verifier."""
    failed = set(failures)
    timed_out = set(timeouts)
    verified: list[str] = []

    def verify(candidate_id):
        if stop_after is not None and len(verified) >= stop_after:
            raise _Interrupted(tuple(verified))
        verified.append(candidate_id)
        candidate = next(item for item in candidates
                         if item.candidate_id == candidate_id)
        if candidate_id in timed_out:
            identity = TimeoutIdentity(
                schema=TIMEOUT_IDENTITY_SCHEMA,
                candidate_id=candidate_id,
                work_date="2027-09-16",
                search_content_key=SEARCH_KEY,
                variant="q50",
                seed=1000,
                attempt=1,
                threshold_s=300.0,
                retry_protocol=RETRY_PROTOCOL_SINGLE_ATTEMPT_FIXED_THRESHOLD,
                search_provenance_key="provenance",
            )
            return CandidateEvidence(
                candidate_id=candidate_id,
                observations=(),
                hard_failures=(
                    f"q50:1000:attempt1:threshold300s:{candidate_id}",),
                disruption=tuple(candidate.disruption),
                timeout_undecided=(identity,),
            )
        return _evidence(
            candidate, policy,
            failures=("hard_gate",) if candidate_id in failed else (),
        )

    result = run_cost_ordered_search(
        candidates, policy,
        verify=verify,
        search_content_key=SEARCH_KEY,
        provider_identity=PROVIDER_IDENTITY,
        practical_equivalence_vehicle_hours=band,
        state=state,
        verified_evidence=verified_evidence,
        disable_early_stop=disable_early_stop,
    )
    return result, verified


class _Interrupted(Exception):
    def __init__(self, verified):
        super().__init__("interrupted")
        self.verified = verified


def _exhaustive(candidates, policy, *, failures=(), band=0.0):
    """The reference: verify everything, then select."""
    failed = set(failures)
    evidence = []
    for candidate in candidates:
        if candidate.cost.vehicles_no_detour > 0:
            # The reference path simulates these too and lets the selector
            # demote them; it never sees a deterministic pre-refusal.
            evidence.append(_evidence(candidate, policy))
            continue
        evidence.append(_evidence(
            candidate, policy,
            failures=("hard_gate",) if candidate.candidate_id in failed else (),
        ))
    return select_pilot_finalists(
        evidence, policy,
        ranking_objective="closure_cost_v1",
        practical_equivalence_vehicle_hours=band,
    )


# --------------------------------------------------------------------------
# Ordering and the stop rule.
# --------------------------------------------------------------------------


class TestOrderAndStop:
    def test_it_orders_by_the_unchanged_sort_key(self):
        candidates = [
            _candidate(1, 5.0), _candidate(2, 1.0), _candidate(3, 3.0),
        ]
        ordered, refused = plan_order(candidates)
        assert [cost.candidate_id for cost in ordered] == [
            "closure-0002", "closure-0003", "closure-0001"]
        assert refused == ()

    def test_no_detour_candidates_never_reach_verification(self):
        candidates = [
            _candidate(1, 1.0),
            _candidate(2, 0.1, no_detour=4),
            _candidate(3, 2.0),
        ]
        result, verified = _run(candidates, _policy(minimum=2))
        assert "closure-0002" not in verified
        assert [cost.candidate_id for cost in result.disqualified] == [
            "closure-0002"]

    def test_it_stops_once_the_band_is_exhausted(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 11)]
        result, verified = _run(candidates, _policy(minimum=2), band=0.0)

        # Cutoff is the 2nd viable candidate's 2.0; nothing above it qualifies.
        assert result.state.cutoff == 2.0
        assert verified == ["closure-0001", "closure-0002"]
        assert result.stop_proof["stop_reason"] == "band_exhausted"
        assert result.stop_proof["first_unexamined_added_vehicle_hours"] == 3.0

    def test_a_candidate_exactly_on_the_boundary_is_verified(self):
        """`<=`, not `<`. A tie on the boundary belongs inside it."""
        candidates = [
            _candidate(1, 1.0), _candidate(2, 2.0),
            _candidate(3, 2.5), _candidate(4, 2.5001),
        ]
        result, verified = _run(candidates, _policy(minimum=2), band=0.5)

        assert result.state.cutoff == 2.0
        assert "closure-0003" in verified, "2.5 == cutoff + band is inside"
        assert "closure-0004" not in verified
        assert set(result.selection.selected_ids) == {
            "closure-0001", "closure-0002", "closure-0003"}

    def test_hard_failures_do_not_stop_the_scan(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 7)]
        result, verified = _run(candidates, _policy(minimum=2),
                                failures={"closure-0001", "closure-0002"})
        assert verified[:4] == ["closure-0001", "closure-0002",
                                "closure-0003", "closure-0004"]
        assert set(result.selection.selected_ids) == {
            "closure-0003", "closure-0004"}

    def test_too_few_viable_candidates_exhausts_the_space(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        result, verified = _run(candidates, _policy(minimum=9, maximum=12),
                                failures={"closure-0004", "closure-0005"})
        assert len(verified) == 5
        assert result.stop_proof["stop_reason"] == "search_space_exhausted"

    def test_the_stop_proof_states_the_band_and_the_next_candidate(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        result, _ = _run(candidates, _policy(minimum=2), band=0.25)
        proof = result.stop_proof
        assert proof["cutoff_added_vehicle_hours"] == 2.0
        assert proof["selection_band_added_vehicle_hours"] == 2.25
        assert proof["first_unexamined_candidate_id"] == "closure-0003"
        assert proof["unexamined"] == 3
        assert "cheaper candidate was" in proof["argument"]
        json.dumps(proof)  # machine-readable


class TestDisableEarlyStop:
    """The ordered-exhaustive reference arm: same order, no early stop."""

    def test_it_verifies_every_candidate_in_the_same_cost_order(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 11)]
        result, verified = _run(candidates, _policy(minimum=2), band=0.0,
                                disable_early_stop=True)
        assert verified == [f"closure-{index:04d}" for index in range(1, 11)]
        assert result.stop_proof["stop_reason"] == "search_space_exhausted"
        assert result.stop_proof["unexamined"] == 0
        assert result.stop_proof["disable_early_stop"] is True

    def test_it_reaches_the_same_selection_as_the_bounded_scan(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 11)]
        bounded, bounded_verified = _run(candidates, _policy(minimum=2),
                                         band=0.0)
        exhaustive, exhaustive_verified = _run(
            candidates, _policy(minimum=2), band=0.0,
            disable_early_stop=True)
        assert len(bounded_verified) < len(exhaustive_verified)
        assert (bounded.selection.status, bounded.selection.selected_ids) == (
            exhaustive.selection.status, exhaustive.selection.selected_ids)

    def test_a_disabled_and_a_normal_scan_do_not_share_an_identity(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 4)]
        bounded, _ = _run(candidates, _policy(minimum=2), band=0.0)
        exhaustive, _ = _run(candidates, _policy(minimum=2), band=0.0,
                             disable_early_stop=True)
        assert bounded.state.identity_key != exhaustive.state.identity_key

    def test_a_normal_state_cannot_be_resumed_with_stopping_disabled(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 5)]
        bounded, _ = _run(candidates, _policy(minimum=2), band=0.0)
        with pytest.raises(ValueError, match="policy, cost ledger or"):
            _run(candidates, _policy(minimum=2), band=0.0,
                state=bounded.state, disable_early_stop=True)


class TestUndecidedTimeoutBlocksEarlyStop:
    """cost-order-v5's failure mode: a timeout must never justify a stop."""

    def test_a_timeout_inside_the_band_forces_full_exhaustion(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 8)]
        result, verified = _run(candidates, _policy(minimum=2), band=0.0,
                                timeouts={"closure-0002"})
        # Without the timeout this would stop at closure-0002 (cutoff 2.0,
        # band_exhausted at closure-0003). The undecided evidence must push
        # it all the way to exhaustion instead.
        assert verified == [f"closure-{index:04d}" for index in range(1, 8)]
        assert result.stop_proof["stop_reason"] == "search_space_exhausted"
        assert result.stop_proof["undecided_candidate_ids"] == [
            "closure-0002"]
        assert result.selection.status == "inconclusive"

    def test_a_timeout_never_reached_does_not_taint_a_clean_stop(self):
        """A candidate beyond the band is never examined either way — a

        timeout that would only occur past the natural stop point cannot
        possibly be seen, so the scan still stops cleanly there.
        """
        candidates = [_candidate(index, float(index))
                      for index in range(1, 8)]
        result, verified = _run(candidates, _policy(minimum=2), band=0.0,
                                timeouts={"closure-0005"})
        assert verified == ["closure-0001", "closure-0002"]
        assert result.stop_proof["stop_reason"] == "band_exhausted"
        assert result.stop_proof["undecided_candidate_ids"] == []

    def test_no_timeout_still_stops_normally(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 8)]
        result, verified = _run(candidates, _policy(minimum=2), band=0.0)
        assert result.stop_proof["stop_reason"] == "band_exhausted"
        assert result.stop_proof["undecided_candidate_ids"] == []
        assert len(verified) < 7


# --------------------------------------------------------------------------
# Differential equivalence with the exhaustive path.
# --------------------------------------------------------------------------


class TestDifferentialEquivalence:
    @pytest.mark.parametrize("hours", [
        (1.0, 2.0, 3.0, 4.0),
        (1.0, 1.0, 1.0, 1.0),
        (5.0, 4.0, 3.0, 2.0, 1.0),
        (0.0, 0.0, 1.0),
        (2.5,),
    ])
    def test_small_calendars_agree(self, hours):
        candidates = [_candidate(index, value)
                      for index, value in enumerate(hours, start=1)]
        policy = _policy(minimum=2, maximum=10)
        result, _ = _run(candidates, policy, band=0.5)
        reference = _exhaustive(candidates, policy, band=0.5)

        comparison = compare_with_exhaustive(result, reference)
        assert comparison["equivalent"] is True, comparison

    def test_primary_and_secondary_ties_agree(self):
        """Equal hours, then equal metres, then the candidate-ID tie-break."""
        candidates = [
            _candidate(1, 2.0, metres=100.0, affected=5),
            _candidate(2, 2.0, metres=100.0, affected=5),
            _candidate(3, 2.0, metres=50.0, affected=5),
            _candidate(4, 2.0, metres=100.0, affected=1),
        ]
        policy = _policy(minimum=2, maximum=10)
        result, _ = _run(candidates, policy)
        reference = _exhaustive(candidates, policy)

        assert compare_with_exhaustive(result, reference)["equivalent"] is True
        ordered = [cost.candidate_id for cost in result.ordered_costs]
        assert ordered == ["closure-0003", "closure-0004", "closure-0001",
                           "closure-0002"]

    def test_capacity_exceeded_is_preserved(self):
        candidates = [_candidate(index, 1.0) for index in range(1, 8)]
        policy = _policy(minimum=2, maximum=3)
        result, _ = _run(candidates, policy)
        reference = _exhaustive(candidates, policy)

        assert result.selection.status == "capacity_exceeded"
        assert result.selection.selected_ids == ()
        assert compare_with_exhaustive(result, reference)["equivalent"] is True

    def test_every_candidate_failing_gives_no_viable(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 5)]
        policy = _policy(minimum=2)
        failures = {item.candidate_id for item in candidates}
        result, verified = _run(candidates, policy, failures=failures)
        reference = _exhaustive(candidates, policy, failures=failures)

        assert result.selection.status == "no_viable"
        assert len(verified) == 4, "it must exhaust the space before giving up"
        assert compare_with_exhaustive(result, reference)["equivalent"] is True

    def test_every_candidate_no_detour_gives_no_viable(self):
        candidates = [_candidate(index, float(index), no_detour=1)
                      for index in range(1, 4)]
        policy = _policy(minimum=2)
        result, verified = _run(candidates, policy)
        reference = _exhaustive(candidates, policy)

        assert verified == [], "a no-detour candidate must never reach SUMO"
        assert result.selection.status == "no_viable"
        assert reference.status == "no_viable"
        assert compare_with_exhaustive(result, reference)["equivalent"] is True

    @pytest.mark.parametrize("seed", range(24))
    def test_randomised_hard_failure_masks_agree(self, seed):
        """Random costs, ties, no-detour and hard-failure masks."""
        rng = random.Random(seed)
        size = rng.randint(1, 9)
        candidates = []
        for index in range(1, size + 1):
            hours = rng.choice([0.0, 0.5, 1.0, 1.0, 2.0, 2.5, 3.0])
            no_detour = 1 if rng.random() < 0.15 else 0
            candidates.append(_candidate(
                index, hours,
                metres=rng.choice([10.0, 20.0]),
                affected=rng.choice([1, 2]),
                no_detour=no_detour,
            ))
        failures = {
            item.candidate_id for item in candidates
            if rng.random() < 0.3 and item.cost.vehicles_no_detour == 0
        }
        policy = _policy(minimum=rng.randint(1, 3),
                         maximum=rng.randint(3, 12))
        band = rng.choice([0.0, 0.25, 1.0])

        result, verified = _run(candidates, policy, failures=failures,
                                band=band)
        reference = _exhaustive(candidates, policy, failures=failures,
                                band=band)
        comparison = compare_with_exhaustive(result, reference)
        assert comparison["equivalent"] is True, (
            f"seed={seed} {comparison}")
        assert comparison["sumo_verifications_saved"] >= 0

    def test_it_actually_saves_work_on_a_broad_search(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 51)]
        policy = _policy(minimum=3, maximum=20)
        result, verified = _run(candidates, policy, band=1.0)
        reference = _exhaustive(candidates, policy, band=1.0)

        assert compare_with_exhaustive(result, reference)["equivalent"] is True
        assert len(verified) == 4
        assert result.stop_proof["unexamined"] == 46


# --------------------------------------------------------------------------
# Restart.
# --------------------------------------------------------------------------


class TestRestart:
    def _interrupted(self, candidates, policy, *, band, after):
        """Run until `after` verifications, then hand back a partial state."""
        verified: list[str] = []
        evidence: dict[str, CandidateEvidence] = {}

        def verify(candidate_id):
            if len(verified) >= after:
                raise _Interrupted(tuple(verified))
            verified.append(candidate_id)
            candidate = next(item for item in candidates
                             if item.candidate_id == candidate_id)
            evidence[candidate_id] = _evidence(candidate, policy)
            return evidence[candidate_id]

        with pytest.raises(_Interrupted):
            run_cost_ordered_search(
                candidates, policy, verify=verify,
                search_content_key=SEARCH_KEY,
                provider_identity=PROVIDER_IDENTITY,
                practical_equivalence_vehicle_hours=band,
            )
        ordered, _refused = plan_order(candidates)
        key = identity_key(
            search_content_key=SEARCH_KEY,
            policy=policy,
            practical_equivalence_vehicle_hours=band,
            provider_identity=PROVIDER_IDENTITY,
            ordered_costs=ordered,
        )
        state = CostOrderedState(
            identity_key=key,
            order=tuple(cost.candidate_id for cost in ordered),
            cursor=len(verified),
            verified=tuple(verified),
            viable=tuple(verified),
        )
        return state, evidence

    @pytest.mark.parametrize("after", [1, 2, 3])
    def test_restart_continues_at_the_first_unverified_candidate(self, after):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 8)]
        policy = _policy(minimum=3, maximum=10)
        state, evidence = self._interrupted(candidates, policy, band=1.0,
                                            after=after)
        assert json.loads(json.dumps(state.to_dict()))

        resumed, verified = _run(candidates, policy, band=1.0,
                                 state=CostOrderedState.from_dict(
                                     state.to_dict()),
                                 verified_evidence=evidence)
        fresh, _ = _run(candidates, policy, band=1.0)
        reference = _exhaustive(candidates, policy, band=1.0)

        assert verified == list(fresh.state.verified)[after:]
        assert resumed.selection.selected_ids == fresh.selection.selected_ids
        assert compare_with_exhaustive(resumed, reference)["equivalent"] is True

    def test_restarting_a_finished_scan_verifies_nothing_more(self):
        """Re-running with the completed state is a no-op, not a repeat."""
        candidates = [_candidate(index, float(index))
                      for index in range(1, 8)]
        policy = _policy(minimum=2, maximum=10)
        first, verified = _run(candidates, policy, band=0.0)
        assert verified == ["closure-0001", "closure-0002"]

        evidence = {item.candidate_id: item for item in first.evidence
                    if item.candidate_id in first.state.verified}
        resumed, again = _run(candidates, policy, band=0.0,
                              state=first.state,
                              verified_evidence=evidence)

        assert again == [], "the cutoff was already earned"
        assert resumed.stop_proof["stop_reason"] == "band_exhausted"
        assert resumed.state.cutoff == 2.0
        assert resumed.selection.selected_ids == first.selection.selected_ids

    def test_a_changed_policy_invalidates_the_resume(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        policy = _policy(minimum=2)
        state, evidence = self._interrupted(candidates, policy, band=0.0,
                                            after=1)
        with pytest.raises(ValueError, match="resume is invalid"):
            _run(candidates, _policy(minimum=3), band=0.0, state=state,
                 verified_evidence=evidence)

    def test_a_changed_cost_ledger_invalidates_the_resume(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        policy = _policy(minimum=2)
        state, evidence = self._interrupted(candidates, policy, band=0.0,
                                            after=1)
        moved = [_candidate(1, 9.9)] + candidates[1:]
        with pytest.raises(ValueError, match="resume is invalid"):
            _run(moved, policy, band=0.0, state=state,
                 verified_evidence=evidence)

    def test_a_changed_provider_identity_invalidates_the_resume(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        policy = _policy(minimum=2)
        state, evidence = self._interrupted(candidates, policy, band=0.0,
                                            after=1)

        def verify(candidate_id):  # pragma: no cover - must not be reached
            raise AssertionError("a stale resume must not verify anything")

        with pytest.raises(ValueError, match="resume is invalid"):
            run_cost_ordered_search(
                candidates, policy, verify=verify,
                search_content_key=SEARCH_KEY,
                provider_identity={"schema": "other"},
                practical_equivalence_vehicle_hours=0.0,
                state=state,
                verified_evidence=evidence,
            )

    def test_a_resume_without_its_evidence_fails_closed(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 6)]
        policy = _policy(minimum=2)
        state, _evidence = self._interrupted(candidates, policy, band=0.0,
                                             after=1)
        with pytest.raises(ValueError, match="missing evidence"):
            _run(candidates, policy, band=0.0, state=state,
                 verified_evidence={})


class TestStateContract:
    def test_a_malformed_state_is_refused(self):
        with pytest.raises(ValueError, match="schema is unsupported"):
            CostOrderedState.from_dict({"schema": "other", "version": 1})

    def test_a_cursor_outside_the_order_is_refused(self):
        with pytest.raises(ValueError, match="outside its order"):
            CostOrderedState.from_dict({
                "schema": "cost_ordered_closure_search_v1", "version": 1,
                "identity_key": "k", "order": ["a"], "cursor": 5,
            })

    def test_marking_an_unverified_candidate_viable_is_refused(self):
        with pytest.raises(ValueError, match="unverified"):
            CostOrderedState.from_dict({
                "schema": "cost_ordered_closure_search_v1", "version": 1,
                "identity_key": "k", "order": ["a", "b"], "cursor": 1,
                "verified": ["a"], "viable": ["b"],
            })

    @pytest.mark.parametrize("override, message", [
        ({"cursor": 2, "verified": ["a"]}, "verified prefix"),
        ({"cursor": 1, "verified": ["b"]}, "verified prefix"),
        ({"cursor": 2, "verified": ["a", "a"]}, "repeats"),
        ({"cursor": 2, "verified": ["a", "b"], "viable": ["a", "a"]},
         "repeats"),
        ({"cursor": 2, "verified": ["a", "b"], "viable": ["b", "a"]},
         "verified order"),
    ])
    def test_a_resume_must_be_the_exact_verified_prefix(
            self, override, message):
        raw = {
            "schema": "cost_ordered_closure_search_v1", "version": 1,
            "identity_key": "k", "order": ["a", "b"], "cursor": 0,
            "verified": [], "viable": [],
        }
        raw.update(override)
        with pytest.raises(ValueError, match=message):
            CostOrderedState.from_dict(raw)

    def test_a_direct_state_cannot_bypass_resume_validation(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 4)]
        policy = _policy(minimum=1)
        fresh, _verified = _run(candidates, policy)
        forged = CostOrderedState(
            identity_key=fresh.state.identity_key,
            order=fresh.state.order,
            cursor=2,
            verified=(fresh.state.order[0],),
            viable=(fresh.state.order[0],),
        )
        with pytest.raises(ValueError, match="verified prefix"):
            _run(candidates, policy, state=forged, verified_evidence={
                fresh.state.order[0]: fresh.evidence[0],
            })

    def test_resume_viability_must_match_the_persisted_evidence(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 4)]
        policy = _policy(minimum=1)
        fresh, _verified = _run(candidates, policy)
        candidate_id = fresh.state.verified[0]
        ineligible = CandidateEvidence(
            candidate_id=candidate_id,
            observations=(),
            hard_failures=("simulator_failed",),
            disruption=fresh.evidence[0].disruption,
        )

        with pytest.raises(ValueError, match="viable set"):
            _run(candidates, policy, state=fresh.state,
                 verified_evidence={candidate_id: ineligible})

    def test_it_round_trips_through_json(self):
        state = CostOrderedState(
            identity_key="k", order=("a", "b"), cursor=1,
            verified=("a",), viable=("a",), cutoff=1.5,
            stop_reason="band_exhausted",
        )
        assert CostOrderedState.from_dict(
            json.loads(json.dumps(state.to_dict()))) == state


class TestDisqualifiedEvidenceShape:
    def test_a_refused_candidate_keeps_its_deterministic_evidence(self):
        candidates = [_candidate(1, 1.0), _candidate(2, 0.5, no_detour=2)]
        result, _ = _run(candidates, _policy(minimum=1))
        refused = next(item for item in result.evidence
                       if item.candidate_id == "closure-0002")
        assert refused.hard_failures == (NO_DETOUR_FAILURE,)
        assert len(refused.disruption) == 3
        assert refused.observations == ()


class TestShadowReplay:
    """Shadow mode: same answer from a subset of an exhaustive run's evidence."""

    def _exhaustive_evidence(self, candidates, policy, failures=()):
        failed = set(failures)
        evidence = {}
        for candidate in candidates:
            if candidate.cost.vehicles_no_detour > 0:
                evidence[candidate.candidate_id] = _evidence(candidate, policy)
                continue
            evidence[candidate.candidate_id] = _evidence(
                candidate, policy,
                failures=("hard_gate",)
                if candidate.candidate_id in failed else (),
            )
        return evidence

    def test_the_replay_matches_and_uses_only_a_subset(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 21)]
        policy = _policy(minimum=3, maximum=10)
        evidence = self._exhaustive_evidence(candidates, policy)

        result = replay_shadow(
            candidates, policy, evidence,
            search_content_key=SEARCH_KEY,
            provider_identity=PROVIDER_IDENTITY,
            practical_equivalence_vehicle_hours=0.5,
        )
        reference = _exhaustive(candidates, policy, band=0.5)
        comparison = compare_with_exhaustive(result, reference)

        assert comparison["equivalent"] is True
        assert set(result.state.verified) < set(evidence)
        assert comparison["sumo_verifications_saved"] > 0

    @pytest.mark.parametrize("seed", range(12))
    def test_randomised_replays_agree(self, seed):
        rng = random.Random(1000 + seed)
        size = rng.randint(2, 12)
        candidates = [
            _candidate(index, rng.choice([0.5, 1.0, 1.0, 2.0, 3.0]),
                       metres=rng.choice([10.0, 40.0]),
                       no_detour=1 if rng.random() < 0.1 else 0)
            for index in range(1, size + 1)
        ]
        failures = {
            item.candidate_id for item in candidates
            if rng.random() < 0.25 and item.cost.vehicles_no_detour == 0
        }
        policy = _policy(minimum=rng.randint(1, 3), maximum=rng.randint(4, 14))
        band = rng.choice([0.0, 0.5])
        evidence = self._exhaustive_evidence(candidates, policy, failures)

        result = replay_shadow(
            candidates, policy, evidence,
            search_content_key=SEARCH_KEY,
            provider_identity=PROVIDER_IDENTITY,
            practical_equivalence_vehicle_hours=band,
        )
        reference = _exhaustive(candidates, policy, failures=failures,
                                band=band)
        assert compare_with_exhaustive(result, reference)["equivalent"] is True

    def test_a_replay_that_needs_unknown_evidence_fails_loudly(self):
        candidates = [_candidate(index, float(index))
                      for index in range(1, 5)]
        policy = _policy(minimum=2)
        evidence = self._exhaustive_evidence(candidates, policy)
        evidence.pop("closure-0001")

        with pytest.raises(ValueError, match="does not have"):
            replay_shadow(
                candidates, policy, evidence,
                search_content_key=SEARCH_KEY,
                provider_identity=PROVIDER_IDENTITY,
            )


class TestShadowFromPilotSelection:
    """The replay a finished run can produce without simulating anything."""

    def _payload(self, statistics, *, status="ready", selected=()):
        return {
            "status": status,
            "selected_ids": list(selected),
            "candidates": statistics,
        }

    def _statistic(self, index, hours, *, eligible=True, failures=()):
        return {
            "candidate_id": f"closure-{index:04d}",
            "eligible": eligible,
            "hard_failures": list(failures),
            "complete": eligible,
            "worst_variant_delta_s": 10.0 + hours,
            "closure_cost": {
                "candidate_id": f"closure-{index:04d}",
                "added_vehicle_hours": float(hours),
                "added_metres_total": float(hours * 100.0),
                "vehicles_affected": int(hours * 10),
                "vehicles_no_detour": 0,
            },
        }

    def test_it_reproduces_the_recorded_selection(self):
        from traffic_sim.simulation.cost_ordered_search import (
            shadow_from_pilot_selection,
        )

        policy = _policy(minimum=2, maximum=10)
        statistics = [self._statistic(index, float(index))
                      for index in range(1, 8)]
        payload = self._payload(
            statistics, selected=["closure-0001", "closure-0002"])

        comparison = shadow_from_pilot_selection(
            payload, policy,
            search_content_key=SEARCH_KEY,
            provider_identity=PROVIDER_IDENTITY,
            practical_equivalence_vehicle_hours=0.0,
        )

        assert comparison["equivalent"] is True
        assert comparison["matches_recorded_run"] is True
        assert comparison["evidence_class"] == "diagnostic_shadow_replay"
        assert comparison["release_evidence"] is False
        assert comparison["sumo_verifications_saved"] == 5
        assert "Not a re-simulation" in comparison["replay_basis"]

    def test_a_candidate_without_a_recorded_cost_fails_closed(self):
        from traffic_sim.simulation.cost_ordered_search import (
            shadow_from_pilot_selection,
        )

        payload = self._payload([{"candidate_id": "closure-0001",
                                  "eligible": True, "hard_failures": []}])
        with pytest.raises(ValueError, match="recorded closure cost"):
            shadow_from_pilot_selection(
                payload, _policy(),
                search_content_key=SEARCH_KEY,
                provider_identity=PROVIDER_IDENTITY,
            )
