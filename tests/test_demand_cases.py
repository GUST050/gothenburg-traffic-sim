"""Step 5 acceptance: case-addressed demand, lazily built, content-keyed.

Plan acceptance for step 5:
  * central-only v2 reproduces the legacy q50 within a documented tolerance,
    or reports every intentional difference;
  * a multi-month search does not build every scenario's route files before
    the shortlist exists;
  * the cache id binds date, case, model, calibration, edge pair and route
    source.
"""

from __future__ import annotations

import pytest

from demand import cases as dc
from demand.intake import build_targets
from dirsplit import schema


SLOTS = schema.SLOT_COUNT


def flat(value):
    return [value] * SLOTS


def ensemble_payload(central=0.52, case_shares=(0.48, 0.56)):
    demand_cases = []
    weight = 1.0 / len(case_shares)
    for index, value in enumerate(case_shares):
        is_last = index == len(case_shares) - 1
        demand_cases.append({
            "case_id": f"dc_case{index}",
            "kind": "probabilistic",
            "weight": (1.0 - weight * (len(case_shares) - 1)) if is_last
            else weight,
            "edge_shares": {"A": flat(value), "B": flat(1 - value)},
        })
    return {
        "central_profile": {
            "weekday": {"shares": {"A": flat(central), "B": flat(1 - central)}}
        },
        "demand_cases": demand_cases,
        "stress_cases": [{
            "case_id": "dc_stress",
            "kind": "stress",
            "edge_shares": {"A": flat(0.35), "B": flat(0.65)},
        }],
    }


# ── resolution ────────────────────────────────────────────────────────────
class TestCaseResolution:
    def test_the_central_case_reads_the_central_profile(self):
        shares = dc.shares_for_case(ensemble_payload(), dc.CENTRAL_CASE_ID)
        assert shares["A"][0] == pytest.approx(0.52)
        assert shares["B"][0] == pytest.approx(0.48)

    def test_a_probabilistic_case_resolves_by_id(self):
        shares = dc.shares_for_case(ensemble_payload(), "dc_case0")
        assert shares["A"][0] == pytest.approx(0.48)

    def test_a_stress_case_resolves_too(self):
        shares = dc.shares_for_case(ensemble_payload(), "dc_stress")
        assert shares["A"][0] == pytest.approx(0.35)

    def test_an_unknown_case_is_refused(self):
        with pytest.raises(dc.CaseError, match="unknown demand case"):
            dc.shares_for_case(ensemble_payload(), "dc_nope")

    def test_a_wrong_slot_count_is_refused(self):
        payload = ensemble_payload()
        payload["demand_cases"][0]["edge_shares"]["A"] = [0.5] * 12
        with pytest.raises(dc.CaseError, match="expected 96"):
            dc.shares_for_case(payload, "dc_case0")

    def test_central_and_stress_have_no_probability_weight(self):
        payload = ensemble_payload()
        assert dc.case_weight(payload, dc.CENTRAL_CASE_ID) is None
        assert dc.case_weight(payload, "dc_stress") is None
        assert dc.case_weight(payload, "dc_case0") == pytest.approx(0.5)


# ── the legacy comparison ─────────────────────────────────────────────────
class TestCentralOnlyReproducesLegacy:
    FLOWS = {"A": [100.0] * 96, "B": [100.0] * 96}
    SENSORS = {"107": ["A", "B"]}

    def test_supplying_shares_matches_supplying_the_same_shares_by_key(self,
                                                                       monkeypatch):
        """The new `shares=` seam changes nothing when given the same numbers."""
        import demand.intake as intake

        legacy = {"A": flat(0.52), "B": flat(0.48)}
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": legacy)
        via_key = build_targets(self.FLOWS, self.SENSORS, 0, 4)
        via_shares = build_targets(self.FLOWS, self.SENSORS, 0, 4,
                                   shares=legacy)
        assert via_key == via_shares

    def test_the_legacy_path_is_untouched_when_shares_is_none(self,
                                                              monkeypatch):
        import demand.intake as intake
        calls = []

        def fake(key="edge_shares"):
            calls.append(key)
            return {"A": flat(0.5), "B": flat(0.5)}

        monkeypatch.setattr(intake, "load_direction_split", fake)
        build_targets(self.FLOWS, self.SENSORS, 0, 1)
        assert calls == ["edge_shares"]

    def test_supplying_shares_skips_the_legacy_file_entirely(self,
                                                             monkeypatch):
        import demand.intake as intake

        def explode(key="edge_shares"):
            raise AssertionError("legacy split file must not be read")

        monkeypatch.setattr(intake, "load_direction_split", explode)
        targets = build_targets(self.FLOWS, self.SENSORS, 0, 1,
                                shares={"A": flat(0.6), "B": flat(0.4)})
        assert targets[0]["A"] == pytest.approx(60.0)
        assert targets[0]["B"] == pytest.approx(40.0)

    def test_a_pair_exact_case_conserves_the_measured_total(self):
        """The property the legacy q10/q90 arms broke."""
        targets = build_targets(self.FLOWS, self.SENSORS, 0, 1,
                                shares={"A": flat(0.62), "B": flat(0.38)})
        assert targets[0]["A"] + targets[0]["B"] == pytest.approx(100.0)

    def test_a_legacy_style_unnormalised_pair_loses_volume(self):
        """Documents what the old construction did, so it stays visible."""
        targets = build_targets(self.FLOWS, self.SENSORS, 0, 1,
                                shares={"A": flat(0.44), "B": flat(0.45)})
        total = targets[0]["A"] + targets[0]["B"]
        assert total == pytest.approx(89.0)
        assert total < 100.0


# ── cache identity ────────────────────────────────────────────────────────
class TestCacheKeyBindsEverything:
    BASE = dict(
        date="2027-05-03", source="forecast", case_id="dc_case0",
        model_digest="m1", calibration_digest="c1",
        edge_pairs=[("A", "B")], route_source_digest="r1",
    )

    def key(self, **overrides):
        payload = dict(self.BASE)
        payload.update(overrides)
        return dc.demand_case_cache_key(**payload)

    def test_identical_inputs_give_an_identical_key(self):
        assert self.key() == self.key()

    @pytest.mark.parametrize("field,value", [
        ("date", "2027-05-04"),
        ("source", "historical"),
        ("case_id", "dc_case1"),
        ("model_digest", "m2"),
        ("calibration_digest", "c2"),
        ("route_source_digest", "r2"),
        ("window", "06:00-10:00"),
    ])
    def test_changing_any_component_changes_the_key(self, field, value):
        assert self.key(**{field: value}) != self.key()

    def test_changing_the_edge_pairs_changes_the_key(self):
        assert self.key(edge_pairs=[("A", "C")]) != self.key()

    def test_pair_order_does_not_change_the_key(self):
        """Registry insertion order must not produce a different artifact."""
        assert self.key(edge_pairs=[("B", "A")]) == self.key()

    def test_multiple_pairs_are_order_independent(self):
        one = self.key(edge_pairs=[("A", "B"), ("C", "D")])
        two = self.key(edge_pairs=[("C", "D"), ("B", "A")])
        assert one == two


# ── laziness ──────────────────────────────────────────────────────────────
class TestLaziness:
    def handles(self, builder, include_stress=False):
        return dc.plan_cases(
            ensemble_payload(), date="2027-05-03", source="forecast",
            model_digest="m", calibration_digest="c",
            edge_pairs=[("A", "B")], route_source_digest="r",
            builder=builder, include_stress=include_stress,
        )

    def test_planning_builds_nothing(self):
        built = []
        handles = self.handles(lambda h: built.append(h.case_id))
        assert len(handles) == 3            # central + two probabilistic
        assert built == []
        assert not any(h.is_built for h in handles)

    def test_only_a_resolved_handle_is_built(self):
        built = []

        def builder(handle):
            built.append(handle.case_id)
            from pathlib import Path
            return Path(f"/tmp/{handle.case_id}.rou.xml")

        handles = self.handles(builder)
        handles[1].resolve()
        assert built == [handles[1].case_id]
        assert handles[1].is_built
        assert not handles[0].is_built

    def test_resolving_twice_builds_once(self):
        built = []

        def builder(handle):
            built.append(handle.case_id)
            from pathlib import Path
            return Path("/tmp/x")

        handles = self.handles(builder)
        handles[0].resolve()
        handles[0].resolve()
        assert len(built) == 1

    def test_a_shortlist_pays_only_for_its_members(self):
        """The multi-month property: enumerate many, build few."""
        built = []

        def builder(handle):
            built.append(handle.case_id)
            from pathlib import Path
            return Path("/tmp/x")

        many = []
        for day in range(1, 31):
            many += dc.plan_cases(
                ensemble_payload(), date=f"2027-05-{day:02d}",
                source="forecast", model_digest="m", calibration_digest="c",
                edge_pairs=[("A", "B")], route_source_digest="r",
                builder=builder,
            )
        assert len(many) == 90
        assert built == []
        for handle in many[:3]:
            handle.resolve()
        assert len(built) == 3

    def test_every_handle_has_a_distinct_key_per_date(self):
        keys = set()
        for day in (1, 2, 3):
            for handle in dc.plan_cases(
                ensemble_payload(), date=f"2027-05-{day:02d}",
                source="forecast", model_digest="m", calibration_digest="c",
                edge_pairs=[("A", "B")], route_source_digest="r",
                builder=lambda h: None,
            ):
                keys.add(handle.cache_key)
        assert len(keys) == 9

    def test_stress_cases_are_excluded_unless_requested(self):
        assert len(self.handles(lambda h: None)) == 3
        assert len(self.handles(lambda h: None, include_stress=True)) == 4

    def test_a_handle_produces_targets_through_the_unchanged_intake(self):
        handles = self.handles(lambda h: None)
        central = handles[0]
        targets = central.targets({"A": [100.0] * 96, "B": [100.0] * 96},
                                  {"107": ["A", "B"]}, 0, 1)
        assert targets[0]["A"] + targets[0]["B"] == pytest.approx(100.0)


# ── reduction ─────────────────────────────────────────────────────────────
class TestWeightedReduction:
    def handles(self, include_stress=True):
        return dc.plan_cases(
            ensemble_payload(), date="2027-05-03", source="forecast",
            model_digest="m", calibration_digest="c",
            edge_pairs=[("A", "B")], route_source_digest="r",
            builder=lambda h: None, include_stress=include_stress,
        )

    def test_it_averages_probabilistic_cases_only(self):
        handles = self.handles()
        values = {h.case_id: 10.0 for h in handles}
        values[[h for h in handles if h.is_stress][0].case_id] = 1000.0
        assert dc.weighted_reduce(values, handles) == pytest.approx(10.0)

    def test_the_central_case_is_not_part_of_the_expectation(self):
        handles = self.handles()
        values = {h.case_id: 10.0 for h in handles}
        values[dc.CENTRAL_CASE_ID] = 1000.0
        assert dc.weighted_reduce(values, handles) == pytest.approx(10.0)

    def test_a_missing_probabilistic_value_is_refused(self):
        handles = self.handles()
        values = {h.case_id: 10.0 for h in handles}
        probabilistic = [h for h in handles
                         if h.weight is not None and not h.is_stress]
        values.pop(probabilistic[0].case_id)
        with pytest.raises(dc.CaseError, match="silently re-normalise"):
            dc.weighted_reduce(values, handles)

    def test_weights_are_respected(self):
        handles = self.handles(include_stress=False)
        probabilistic = [h for h in handles if h.weight is not None]
        values = {probabilistic[0].case_id: 0.0,
                  probabilistic[1].case_id: 10.0}
        for handle in handles:
            values.setdefault(handle.case_id, 0.0)
        assert dc.weighted_reduce(values, handles) == pytest.approx(5.0)
