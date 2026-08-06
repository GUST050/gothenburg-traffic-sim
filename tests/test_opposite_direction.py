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
        # s=0.6 -> 50*0.4/0.6 = 33.3 (low); s=0.4 -> 50*0.6/0.4 = 75 (high)
        assert round(lo, 1) == 33.3
        assert round(hi, 1) == 75.0
        assert lo < hi, "the mapping is decreasing in s; bounds must be ordered"

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
