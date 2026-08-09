from traffic_sim.intake.contribution import build_contribution, rank_placements


def _loso(ratios, *, contract=True):
    report = {"stations": {sid: {"edges": {
        edge: {"ratio": ratio} for edge, ratio in edges.items()
    }} for sid, edges in ratios.items()}}
    if contract:
        report["comparison_contract"] = {
            "protocol": "loso_pfe_meso_v2", "source": "historical",
            "window_start": "2025-09-16T00:00:00",
            "window_end": "2025-09-17T00:00:00", "n_intervals": 96,
            "simulation_mode": "meso", "candidate_pool_sha256": "pool",
            "network_sha256": "network", "assignment_prior_enabled": True,
        }
    return report


def test_contribution_reports_holdout_change_and_confidence_gain():
    registry = {"sensors": [{"sensor_id": "new", "quality_status": "accepted",
                              "snap_status": "approved", "approved_edge_ids": ["e1"]}]}
    before_network = {"features": [{"properties": {"id": "e1", "confidence": 0.2}}]}
    after_network = {"features": [{"properties": {"id": "e1", "confidence": 0.8}}]}
    report = build_contribution(
        sensor_id="new", registry=registry,
        network_before=before_network, network_after=after_network,
        loso_before=_loso({"old": {"e": 0.5}}),
        loso_after=_loso({"old": {"e": 0.9}, "new": {"e1": 1.0}}),
        flows_after={"e1": [1, None, 3]},
    )
    assert report["outcome"] == "improved"
    assert report["confidence"]["improved_edges"] == 1
    assert report["coverage"]["e1"]["coverage_pct"] == 66.67


def test_missing_holdout_is_insufficient_not_neutral():
    report = build_contribution(
        sensor_id="new", registry={"sensors": [{"sensor_id": "new"}]},
        network_before=None, network_after=None,
        loso_before={}, loso_after={}, flows_after={},
    )
    assert report["outcome"] == "insufficient_evidence"


def test_different_loso_contract_cannot_be_reported_as_sensor_improvement():
    registry = {"sensors": [{"sensor_id": "new", "approved_edge_ids": ["e1"]}]}
    before = _loso({"old": {"e": 0.5}})
    after = _loso({"old": {"e": 0.9}})
    after["comparison_contract"]["window_start"] = "2025-09-17T00:00:00"
    network = {"features": [{"properties": {"id": "e1", "confidence": 0.2}}]}

    report = build_contribution(
        sensor_id="new", registry=registry, network_before=network,
        network_after=network, loso_before=before, loso_after=after,
        flows_after={"e1": [1]},
    )

    assert report["outcome"] == "insufficient_evidence"
    assert report["comparability"]["comparable"] is False


def test_negative_comparable_holdout_change_is_reported_as_worsened():
    registry = {"sensors": [{"sensor_id": "new", "approved_edge_ids": ["e1"]}]}
    network = {"features": [{"properties": {"id": "e1", "confidence": 0.2}}]}
    report = build_contribution(
        sensor_id="new", registry=registry, network_before=network,
        network_after=network,
        loso_before=_loso({"old": {"e": 1.0}}),
        loso_after=_loso({"old": {"e": 0.5}}),
        flows_after={"e1": [1]},
    )

    assert report["outcome"] == "worsened"


def test_new_protocol_refuses_underidentified_sensor_improvement_claim():
    registry = {"sensors": [{"sensor_id": "new", "approved_edge_ids": ["e1"]}]}
    network = {"features": [{"properties": {"id": "e1", "confidence": 0.2}}]}
    before = _loso({"old": {"e": 0.5}})
    after = _loso({"old": {"e": 0.9}, "new": {"e1": 1.0}})
    for report in (before, after):
        report["comparison_contract"]["sensor_observability_gate"] = (
            "candidate incidence rank")
    after["stations"]["new"]["observability_certificate"] = {
        "status": "underidentified",
        "onboarding_ready": False,
    }

    result = build_contribution(
        sensor_id="new", registry=registry,
        network_before=network, network_after=network,
        loso_before=before, loso_after=after, flows_after={"e1": [1]},
    )

    assert result["outcome"] == "insufficient_evidence"
    assert result["holdout"]["observability_certificate"]["status"] == (
        "underidentified")


def test_placement_screen_excludes_measured_edges_and_is_sorted():
    network = {"features": [
        {"properties": {"id": "measured", "sensor_id": "s", "confidence": 0.0,
                         "dist_sensor_m": 1000}},
        {"properties": {"id": "far", "confidence": 0.0, "dist_sensor_m": 1000}},
        {"properties": {"id": "near", "confidence": 0.5, "dist_sensor_m": 10}},
    ]}
    rows = rank_placements(network, top_n=2)
    assert [row["edge_id"] for row in rows] == ["far", "near"]
