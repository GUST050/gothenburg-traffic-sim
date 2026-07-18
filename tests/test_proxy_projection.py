from traffic_sim.simulation.proxy_projection import project_forecast_flows


def _constant(value, count=96):
    return [value] * count


def _payload(epoch, flows):
    return {
        "epoch": epoch,
        "interval_minutes": 15,
        "flows": flows,
    }


def _inputs():
    sensor_edges = {
        "total": ("s1a", "s1b"),
        "directional-2": ("s2",),
        "directional-3": ("s3",),
    }
    semantics = {
        "total": "two_way_total",
        "directional-2": "directional",
        "directional-3": "directional",
    }
    shares = {
        "s1a": _constant(0.6),
        "s1b": _constant(0.4),
    }
    reference = _payload(
        "2025-01-01T00:00:00",
        {
            "s1a": _constant(10, 365 * 96),
            "s1b": _constant(10, 365 * 96),
            "s2": _constant(10, 365 * 96),
            "s3": _constant(10, 365 * 96),
        },
    )
    forecast = _payload(
        "2027-01-01T00:00:00",
        {
            "s1a": _constant(20, 365 * 96),
            "s1b": _constant(20, 365 * 96),
            "s2": _constant(40, 365 * 96),
            "s3": _constant(60, 365 * 96),
        },
    )
    corridor = {
        "epoch": "2025-01-01T00:00:00",
        "interval_minutes": 15,
        "corridor_priors": {
            "corridor": {"prior": _constant(9, 365 * 96)}
        },
    }
    return sensor_edges, semantics, shares, reference, forecast, corridor


def _project(*, edge_ids=("s1a", "s1b", "assignment", "learned", "corridor",
                          "missing"), minimum_scale_stations=3, **overrides):
    sensor_edges, semantics, shares, reference, forecast, corridor = _inputs()
    values = {
        "projection_start": "2027-04-05T00:00:00",
        "projection_end_exclusive": "2027-04-06T00:00:00",
        "forecast_payload": forecast,
        "reference_payload": reference,
        "sensor_edges": sensor_edges,
        "sensor_semantics": semantics,
        "direction_shares": shares,
        "assignment_flows": {"assignment": _constant(5)},
        "learned_priors": {"learned": {"prior": _constant(7)}},
        "corridor_payload": corridor,
        "structural_reference_date": "2025-09-16T00:00:00",
        "minimum_scale_stations": minimum_scale_stations,
    }
    values.update(overrides)
    return project_forecast_flows(edge_ids, **values)


def test_station_median_scales_structural_sources():
    result = _project()
    # Station ratios are 2, 4, 6. Every station has equal influence, so the
    # robust scale is their median (4), not a volume-weighted aggregate.
    assert result.scale_factors[0] == 4
    assert result.scale_station_counts[0] == 3
    assert result.flows["assignment"][0] == 20
    assert result.flows["learned"][0] == 28
    assert result.flows["corridor"][0] == 36
    assert result.structural_sources == {
        "s1a": "forecast_sensor",
        "s1b": "forecast_sensor",
        "assignment": "assignment_prior",
        "learned": "learned_direction_prior",
        "corridor": "corridor_prior",
        "missing": "missing",
    }
    assert result.flows["missing"][0] is None


def test_two_way_total_is_split_once_not_doubled():
    result = _project(edge_ids=("s1a", "s1b"))
    assert result.flows["s1a"][0] == 12
    assert result.flows["s1b"][0] == 8
    assert result.flows["s1a"][0] + result.flows["s1b"][0] == 20


def test_direct_sensor_forecast_survives_when_scale_support_is_missing():
    sensor_edges, semantics, shares, reference, forecast, corridor = _inputs()
    forecast["flows"]["s2"][0:] = [None] * (365 * 96)
    forecast["flows"]["s3"][0:] = [None] * (365 * 96)
    result = _project(
        edge_ids=("s1a", "assignment"),
        forecast_payload=forecast,
        minimum_scale_stations=3,
    )
    assert result.scale_factors[0] is None
    assert result.scale_station_counts[0] == 1
    assert result.flows["s1a"][0] == 12
    assert result.flows["assignment"][0] is None
    assert result.minimum_forecast_station_coverage == 1 / 3


def test_missing_quarter_never_becomes_zero():
    sensor_edges, semantics, shares, reference, forecast, corridor = _inputs()
    requested_index = (94 * 96)  # 2027-04-05 from a Jan 1 epoch
    forecast["flows"]["s1a"][requested_index] = None
    forecast["flows"]["s1b"][requested_index] = None
    result = _project(
        edge_ids=("s1a", "assignment"),
        forecast_payload=forecast,
    )
    assert result.flows["s1a"][0] is None
    # Two other stations remain, below the required three for structural
    # scaling. Missing therefore propagates instead of becoming zero.
    assert result.flows["assignment"][0] is None


def test_invalid_two_way_duplicate_totals_fail_closed():
    sensor_edges, semantics, shares, reference, forecast, corridor = _inputs()
    requested_index = 94 * 96
    forecast["flows"]["s1b"][requested_index] = 21
    try:
        _project(forecast_payload=forecast)
    except ValueError as exc:
        assert "inconsistent duplicated totals" in str(exc)
    else:
        raise AssertionError("inconsistent two-way totals were accepted")


def test_projection_artifact_labels_claim_boundary():
    payload = _project(edge_ids=("assignment",)).to_dict()
    assert payload["kind"] == "monthly_proxy_projection"
    assert payload["n_intervals"] == 96
    assert "direct forecast only on measured sensor edges" in payload["claim"]
