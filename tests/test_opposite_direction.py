"""Both directions at every station, not only at the two-way one.

Five of the six stations measure ONE carriageway. Until 2026-08-06 the
opposite side carried no constraint at all -- the PFE filled it from structure
with nothing to answer to, which is an assumption dressed as a result. The
direction model now predicts both sides and the opposite flow enters as a
level-2 bound the solver must satisfy.

The nastiest part of that change is guarded here: pair-normalised shares
suddenly exist for MEASURED edges too, and build_targets multiplies every
sensor edge by its share. A measured 50 would have been calibrated as 25 --
silently, at 100% GEH against the halved target.
"""
import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demand.intake import build_targets
from demand.priors import opposite_direction_bounds


class TestMeasuredValuesAreNeverSplit:
    """A single-direction station already measures one carriageway."""

    def test_a_measured_directional_count_enters_level_1_untouched(self, monkeypatch):
        import demand.intake as intake
        # A share exists for the measured edge, as it now does in production.
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": {"m": [0.5] * 96})
        targets = build_targets({"m": [50.0]}, {"1076": ["m"]}, 0, 1)
        assert targets[0]["m"] == 50.0, (
            "a directional station's value IS that direction's count; "
            "halving it would still show 100% GEH against the halved target")

    def test_a_two_way_total_is_still_split(self, monkeypatch):
        import demand.intake as intake
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": {"n": [0.6] * 96,
                                                       "s": [0.4] * 96})
        targets = build_targets({"n": [100.0], "s": [100.0]},
                                {"107": ["n", "s"]}, 0, 1)
        assert targets[0]["n"] == 60.0
        assert targets[0]["s"] == 40.0

    def test_a_two_way_pair_without_a_model_falls_back_to_even(self, monkeypatch):
        import demand.intake as intake
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": {})
        targets = build_targets({"n": [100.0], "s": [100.0]},
                                {"107": ["n", "s"]}, 0, 1)
        assert targets[0]["n"] == targets[0]["s"] == 50.0


class TestOppositeDirectionBounds:
    def _registry(self, tmp_path):
        path = tmp_path / "sensors.json"
        path.write_text(json.dumps([
            {"sensor_id": "1076", "approved_edge_ids": ["measured"],
             "opposite_direction": {"edge_id": "other"}},
            {"sensor_id": "107",
             "approved_edge_ids": ["north", "south"]},        # two-way: skipped
        ]))
        return path

    def test_the_opposite_flow_follows_from_the_measured_one(self, tmp_path, monkeypatch):
        import demand.intake as intake
        shares = {"edge_shares": {"measured": [0.5] * 96},
                  "edge_shares_q10": {"measured": [0.4] * 96},
                  "edge_shares_q90": {"measured": [0.6] * 96}}
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": shares[key])
        out = opposite_direction_bounds({"measured": [50.0]}, 1, 0,
                                        registry_path=self._registry(tmp_path))
        lo, hi = out[0]["other"]
        # A CEILING only: s=0.4 -> 50*0.6/0.4 = 75 is the most the opposite
        # side could plausibly carry. The floor is deliberately 0.
        assert lo == 0.0, (
            "a floor would force traffic onto a carriageway nothing measured; "
            "it bound at the floor in 40% of edge-quarters and was removed")
        assert round(hi, 1) == 75.0

    def test_a_two_way_station_gets_no_inferred_bound(self, tmp_path, monkeypatch):
        import demand.intake as intake
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": {"measured": [0.5] * 96})
        out = opposite_direction_bounds({"measured": [50.0], "north": [10.0]},
                                        1, 0, registry_path=self._registry(tmp_path))
        assert "north" not in out[0], "107 measures both sides; nothing to infer"

    def test_a_missing_measurement_constrains_nothing(self, tmp_path, monkeypatch):
        """None means the source did not constrain that quarter, not zero."""
        import demand.intake as intake
        shares = {"edge_shares": {"measured": [0.5] * 96},
                  "edge_shares_q10": {"measured": [0.4] * 96},
                  "edge_shares_q90": {"measured": [0.6] * 96}}
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda key="edge_shares": shares[key])
        out = opposite_direction_bounds({"measured": [None]}, 1, 0,
                                        registry_path=self._registry(tmp_path))
        assert out[0] == {}

    def test_no_registry_yields_no_bounds_rather_than_failing(self, tmp_path):
        out = opposite_direction_bounds({"m": [50.0]}, 2, 0,
                                        registry_path=tmp_path / "absent.json")
        assert out == [{}, {}]


class TestTheRegistryIsWiredUp:
    """The five verified opposite carriageways must actually be recorded."""

    def test_every_single_direction_station_has_a_verified_opposite(self):
        rows = json.loads(Path("data_in/sensors.json").read_text())
        rows = rows if isinstance(rows, list) else rows.get("sensors", rows)
        singles = [r for r in rows if len(r.get("approved_edge_ids", [])) == 1]
        assert len(singles) == 5
        for row in singles:
            spec = row.get("opposite_direction")
            assert spec and spec.get("edge_id"), row["sensor_id"]
            assert spec["measurement_status"] == "unmeasured_estimated"


class TestVariantsMoveDirectionNotVolume:
    """A demand variant may move WHERE the measured total goes, never HOW MUCH.

    The split artifact stores each edge's own MARGINAL quantile, and two
    marginals do not sum to one. Feeding them straight into build_targets made
    the q10 route file calibrate sensor 107 to 82% of its measured day total
    and the q90 file to 118% (pair sums measured at 0.59-1.41 on the deployed
    profile) — a volume stress case wearing a direction label, against the rule
    that measurements outrank every estimate.
    """

    def _targets(self, monkeypatch, shares, key="edge_shares"):
        import demand.intake as intake
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda k="edge_shares": shares)
        return intake.build_targets({"n": [100.0], "s": [100.0]},
                                    {"107": ["n", "s"]}, 0, 1, split_key=key)

    def test_a_marginal_pair_that_undershoots_still_conserves_the_total(
            self, monkeypatch):
        # Each edge's own LOW share: 0.38 + 0.27 = 0.65 of the measured total.
        targets = self._targets(monkeypatch,
                                {"n": [0.38] * 96, "s": [0.27] * 96})
        assert targets[0]["n"] + targets[0]["s"] == pytest.approx(100.0)

    def test_a_marginal_pair_that_overshoots_still_conserves_the_total(
            self, monkeypatch):
        targets = self._targets(monkeypatch,
                                {"n": [0.73] * 96, "s": [0.62] * 96})
        assert targets[0]["n"] + targets[0]["s"] == pytest.approx(100.0)

    def test_the_two_variants_land_on_opposite_sides(self, monkeypatch):
        low = self._targets(monkeypatch, {"n": [0.38] * 96, "s": [0.27] * 96})
        high = self._targets(monkeypatch, {"n": [0.73] * 96, "s": [0.62] * 96})
        assert low[0]["n"] != pytest.approx(high[0]["n"])
        assert (low[0]["n"] - 50) * (high[0]["n"] - 50) < 0

    def test_the_canonical_edge_is_stable(self):
        from demand.intake import scenario_shares
        shares = {"n": [0.6] * 96, "s": [0.3] * 96}
        first = scenario_shares(["n", "s"], shares, 0)
        second = scenario_shares(["s", "n"], shares, 0)
        assert first == second

    def test_a_single_direction_station_takes_its_whole_count(self):
        from demand.intake import scenario_shares
        assert scenario_shares(["m"], {"m": [0.42] * 96}, 0) == {"m": 1.0}

    def test_without_a_model_the_pair_splits_evenly(self):
        from demand.intake import scenario_shares
        assert scenario_shares(["n", "s"], {}, 0) == {"n": 0.5, "s": 0.5}

    def test_published_counts_match_the_calibration_targets(self, tmp_path,
                                                            monkeypatch):
        """write_counts feeds the same numbers to SUMO that the PFE aimed at."""
        import xml.etree.ElementTree as ET

        import demand.intake as intake
        import demand.publication as publication
        shares = {"n": [0.38] * 96, "s": [0.27] * 96}
        monkeypatch.setattr(intake, "load_direction_split",
                            lambda k="edge_shares": shares)
        monkeypatch.setattr(publication, "load_direction_split",
                            lambda k="edge_shares": shares)
        out = tmp_path / "counts.xml"
        publication.write_counts({"n": [100.0], "s": [100.0]},
                                 {"107": ["n", "s"]}, 0, 1, out,
                                 split_key="edge_shares_q10")
        written = {edge.get("id"): float(edge.get("count"))
                   for edge in ET.parse(out).getroot().iter("edge")}
        targets = intake.build_targets({"n": [100.0], "s": [100.0]},
                                       {"107": ["n", "s"]}, 0, 1,
                                       split_key="edge_shares_q10")
        assert sum(written.values()) == pytest.approx(100.0, abs=1.0)
        for edge, value in written.items():
            assert value == pytest.approx(targets[0][edge], abs=1.0)
