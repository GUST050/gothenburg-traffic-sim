"""Fast catalog renewal may only carry forward exact, already-qualified artifacts."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from traffic_sim.demand import route_catalog as catalog
from traffic_sim.demand.catalog_revalidation import equivalent_entries


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, 'catalog_entry_matches', lambda *a, **kw: True)
    outputs={name:{'sha256':hashlib.sha256(name.encode()).hexdigest()} for name in catalog.OUTPUTS}
    old={'pool_key':'weekday','config':{'n_total':6000},'inputs':{'network':{'sha256':'same'}},
        'source_files': {
            'build_sumo_demand': {'sha256': 'old'},
            'build_candidates': {'sha256': 'unchanged'}}}
    new=copy.deepcopy(old);new['source_files']['build_sumo_demand']['sha256']='new'
    keys=[]
    for identity in [old,new]:
        key=hashlib.sha256(catalog._stable_json(identity)).hexdigest()[:32]
        path=tmp_path/key;path.mkdir()
        (path/'manifest.json').write_text(json.dumps({'identity':identity,'outputs':outputs}))
        keys.append(key)
    return keys


def test_exact_artifacts_with_only_orchestrator_drift_can_renew(tmp_path, monkeypatch):
    old,new=fixture(tmp_path,monkeypatch)
    proof=equivalent_entries(tmp_path,'weekday',old,new,6000)
    assert proof['changed_sources']==['build_sumo_demand']
    assert proof['outputs_identical'] is True


@pytest.mark.parametrize('mutation',['output','network','generator','config'])
def test_material_change_requires_full_qualification(tmp_path,monkeypatch,mutation):
    old,new=fixture(tmp_path,monkeypatch)
    path=tmp_path/new/'manifest.json';record=json.loads(path.read_text())
    if mutation=='output': record['outputs']['catalog.rou.xml']['sha256']='changed'
    if mutation=='network': record['identity']['inputs']['network']['sha256']='changed'
    if mutation == 'generator':
        record['identity']['source_files']['build_candidates'][
            'sha256'] = 'changed'
    if mutation=='config':record['identity']['config']['n_total']=7000
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError): equivalent_entries(tmp_path,'weekday',old,new,6000)


def test_corrupt_catalog_never_passes_equivalence(tmp_path,monkeypatch):
    old,new=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(catalog,'catalog_entry_matches',lambda *a,**kw:False)
    with pytest.raises(ValueError): equivalent_entries(tmp_path,'weekday',old,new,6000)


def test_renewal_binds_base_build_and_each_equivalence_proof(tmp_path,monkeypatch):
    from traffic_sim.demand.catalog_revalidation import renewed_config,SCOPE
    old,new=fixture(tmp_path,monkeypatch)
    base={'catalog_keys':{'weekday':old,'weekend':old},
          'catalog_selected_n_total':{'weekday':6000,'weekend':6000}}
    build={'results':{pool:{'key':new,'n_total':6000} for pool in base['catalog_keys']}}
    def write(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value))
        return {'path':name,'sha256':catalog.sha256_file(path)}
    certificate={'schema_version':1,'scope':SCOPE,
        'base_adoption':write('base.json',base),'catalog_build':write('build.json',build),
        'pools': {
            pool: equivalent_entries(tmp_path, pool, old, new, 6000)
            for pool in base['catalog_keys']}}
    payload=dict(base,equivalence=write('certificate.json',certificate))
    assert renewed_config(payload,tmp_path,tmp_path)['catalog_keys']['weekday']==new
    (tmp_path/'base.json').write_text('{}')
    with pytest.raises(ValueError,match='hash'):
        renewed_config(payload,tmp_path,tmp_path)


def test_missing_renewal_certificate_fails_closed(tmp_path,monkeypatch):
    from traffic_sim.demand.catalog_revalidation import renewed_config
    with pytest.raises(ValueError,match='hash'):
        renewed_config({'equivalence':{'path':'missing.json','sha256':'a'*64}},tmp_path,tmp_path)
