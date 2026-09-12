"""Source-bound adapter for dynamic passage calibration."""
import json
from pathlib import Path
import pytest
from traffic_sim.core.fingerprint import sha256_file
from tools import trial_dynamic_passage as trial


def fixture(tmp_path):
    source = tmp_path / 'source'
    inputs = source / 'input'
    inputs.mkdir(parents=True)
    (inputs / 'calibrated.rou.xml').write_text('<routes><vehicle id="v" depart="850"><route edges="o s d"/></vehicle></routes>')
    (inputs / 'calibrated.agents.json').write_text(json.dumps({'agents': [
        {'vehicle_id': 'v', 'origin_edge': 'o', 'destination_edge': 'd',
         'purpose': 'work', 'departure_s': 850}]}))
    (inputs / 'demand_meta.json').write_text(json.dumps({'n_intervals': 2,
        'sensor_targets': {'variants': {'edge_shares': {'s': [0, 1]}}}}))
    (inputs / 'net.net.xml').write_text('<net/>')
    report = {'input_sha256': {p.name: sha256_file(p) for p in inputs.iterdir()},
              'evidence_sha256': {}}
    for arm in (1000, 1001, 1002):
        path = source / 'evidence' / f'learning-0-arm-{arm}' / 'vehroute.xml'
        path.parent.mkdir(parents=True)
        path.write_text('<routes><vehicle id="v" depart="851"><route edges="o s d" exitTimes="910 920 930"/></vehicle></routes>')
        report['evidence_sha256'][str(path.relative_to(source))] = sha256_file(path)
        edge = path.with_name('edge.xml')
        edge.write_text('<meandata><interval begin="0" end="900"><edge id="s" entered="0"/></interval><interval begin="900" end="1800"><edge id="s" entered="1"/></interval></meandata>')
        report['evidence_sha256'][str(edge.relative_to(source))] = sha256_file(edge)
    (source / 'report.json').write_text(json.dumps(report))
    return source


def test_source_loader_binds_full_route_time_and_od_purpose(tmp_path):
    source = fixture(tmp_path)
    options, groups, metadata = trial.load_source(source)
    assert len(options) == 1
    assert options[0].entry_offsets_s['1000'] == (1., 60., 70.)
    assert options[0].edges == ('o', 's', 'd')
    assert groups == {options[0].group: 1}
    assert metadata['n_intervals'] == 2


def test_changed_trace_cannot_be_used_as_bound_evidence(tmp_path):
    source = fixture(tmp_path)
    path = source / 'evidence/learning-0-arm-1000/vehroute.xml'
    path.write_text(path.read_text().replace('910', '911'))
    with pytest.raises(ValueError, match='hash'):
        trial.load_source(source)


def test_route_change_rejected_even_with_new_hash(tmp_path):
    source = fixture(tmp_path)
    path = source / 'evidence/learning-0-arm-1000/vehroute.xml'
    path.write_text(path.read_text().replace('o s d', 'o other d'))
    report = json.loads((source / 'report.json').read_text())
    report['evidence_sha256'][str(path.relative_to(source))] = sha256_file(path)
    (source / 'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='route'):
        trial.load_source(source)


def test_materialization_preserves_physical_route_and_agent_attributes(tmp_path):
    source = fixture(tmp_path)
    route = source / 'input/calibrated.rou.xml'
    route.write_text(route.read_text().replace('depart="850"', 'depart="850" speedFactor="1.2" departPos="5"'))
    from traffic_sim.experimental.dynamic_assignment import RouteDeparture
    selected = [RouteDeparture('v@60', 'od', 910, ('o', 's', 'd'),
                               {'fit': (0, 60, 70)}, 0, 1)]
    output = tmp_path / 'candidate'
    trial.materialize_selection(route, source / 'input/calibrated.agents.json', selected, output)
    import xml.etree.ElementTree as ET
    vehicle = ET.parse(output / 'calibrated.rou.xml').getroot().find('vehicle')
    assert vehicle.attrib['speedFactor'] == '1.2'
    assert vehicle.attrib['departPos'] == '5'
    assert vehicle.attrib['depart'] == '910.0'
    assert vehicle.find('route').attrib['edges'] == 'o s d'
    agent = json.loads((output / 'calibrated.agents.json').read_text())['agents'][0]
    assert agent['departure_s'] == 910 and agent['purpose'] == 'work'
    assert agent['vehicle_id'] == vehicle.attrib['id']
    with pytest.raises(FileExistsError):
        trial.materialize_selection(route, source / 'input/calibrated.agents.json', selected, output)


def test_trace_projection_must_reconstruct_raw_entered_cells(tmp_path):
    source = fixture(tmp_path)
    path = source / 'evidence/learning-0-arm-1000/edge.xml'
    path.write_text(path.read_text().replace('entered="1"', 'entered="2"'))
    report = json.loads((source / 'report.json').read_text())
    report['evidence_sha256'][str(path.relative_to(source))] = sha256_file(path)
    (source / 'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='reconstruct'):
        trial.load_source(source)
