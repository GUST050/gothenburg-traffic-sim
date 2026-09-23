"""The diagnostic follows each daily unit's frozen archive and cost policy."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from tools import measure_closure_delay_distribution as diagnostic
from traffic_sim.core.contracts import ClosureSearchSpec, DailyTimeBand


def test_multiday_legacy_profile_uses_each_archive_and_q50_samples(monkeypatch):
    spec = ClosureSearchSpec(
        search_id="diagnostic",
        directed_edges=("edge",),
        demand_build_id="frozen-release",
        source="forecast",
        permitted_date_start="2027-07-15",
        permitted_date_end="2027-07-16",
        required_work_minutes=120,
        max_consecutive_start_days=2,
        permitted_daily_band=DailyTimeBand("06:00", "18:00"),
        objective_profile="displaced_vehicles_and_detour_v1",
        interday_policy="independent_daily_reset_v1",
    )
    units = [SimpleNamespace(first_work_date=day) for day in
             ("2027-07-15", "2027-07-16")]
    parent = SimpleNamespace(
        schedule_id="parent", search_content_key=spec.content_key,
        first_work_date=units[0].first_work_date, day_count=2)
    archives = [Path("/tmp/archive-one"), Path("/tmp/archive-two")]
    used_archives = []
    monkeypatch.setattr(diagnostic, "load_search_result", lambda _path: {
        "closure_search_spec": spec.to_dict(),
        "objective_method": "closure_cost_v1",
    })
    monkeypatch.setattr(diagnostic, "load_archive_index", lambda *a, **k: object())
    monkeypatch.setattr(diagnostic, "resolve_archive",
                        lambda _spec, unit, _index, **_kw:
                        archives[units.index(unit)])
    monkeypatch.setattr(diagnostic, "_unit_schedules", lambda *_args: units)
    monkeypatch.setattr(diagnostic, "_published_hours", lambda *_args: 4.0)
    monkeypatch.setattr(diagnostic, "NetworkCostModel",
                        lambda: SimpleNamespace(network_sha256="net-sha"))
    monkeypatch.setattr(diagnostic.ArchiveInputs, "from_archive",
                        classmethod(lambda _cls, _path: object()))

    def fake_report(**_kwargs):
        return {}

    monkeypatch.setattr(diagnostic.disruption_module, "_report", fake_report)

    class Pricer:
        def __init__(self, seconds):
            self.seconds = seconds

        def path_cost(self, _a, _b, banned):
            return (self.seconds if banned else 0.0, 0.0)

    class Provider:
        def __init__(self, _spec, *, archive, **_kwargs):
            self.archive = archive
            used_archives.append(archive)

        def disruption(self, _unit):
            records = []
            movement = (("A", "B"), ("A", "B"), frozenset({"edge"}),
                        None, 0.0, 0.0)
            for variant, seconds in (("q10", 1800), ("q50", 3600),
                                     ("q90", 7200)):
                diagnostic.disruption_module._report(
                    pricer=Pricer(seconds), od_counts=Counter({movement: 1}))
                records.append({
                    "demand_variant": variant,
                    "added_vehicle_hours": seconds / 3600,
                    "added_metres_total": 0.0,
                    "vehicles_affected": 1,
                    "vehicles_no_detour": 0,
                })
            return tuple(records)

    monkeypatch.setattr(diagnostic, "ArchiveDisruptionProvider", Provider)
    measured = diagnostic.measure(Path("/tmp/workspace"), parent,
                                  Path("/tmp/runs"))

    assert used_archives == archives
    assert measured["demand_archives"] == list(map(str, archives))
    assert measured["demand_variant"] == "q50"
    assert measured["added_vehicle_hours"] == 2.0
    assert measured["ranked_added_vehicle_hours"] == 4.0
    assert measured["published_added_vehicle_hours"] == 4.0
