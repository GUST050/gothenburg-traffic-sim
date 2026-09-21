"""Narrow evidence reuse for byte-identical, previously qualified catalogs.

This is artifact-equivalence renewal, never a new end-to-end speed claim.
Changed networks, generation logic, configuration or outputs need qualification.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand import route_catalog as catalog

SCOPE = 'identical_catalog_artifacts_after_orchestrator_change_v1'


def equivalent_entries(root: Path, pool: str, old_key: str, new_key: str, size: int) -> dict:
    records=[]
    for key in (old_key,new_key):
        if not catalog.catalog_entry_matches(root,pool=pool,key=key,n_total=size):
            raise ValueError(f'invalid catalog entry: {pool}/{key}')
        record=json.loads((root/key/'manifest.json').read_text())
        expected=hashlib.sha256(catalog._stable_json(record['identity'])).hexdigest()[:32]
        if expected!=key:
            raise ValueError('catalog identity does not hash to its key')
        records.append(record)
    before,after=records[0],records[1]
    left,right=before['identity'],after['identity']
    if {k:v for k,v in left.items() if k!='source_files'} != {
            k:v for k,v in right.items() if k!='source_files'}:
        raise ValueError('catalog inputs/configuration changed; full qualification required')
    old_sources,new_sources=left['source_files'],right['source_files']
    changed=sorted(k for k in old_sources.keys()|new_sources.keys()
                   if old_sources.get(k)!=new_sources.get(k))
    if set(changed)-{'build_sumo_demand'}:
        raise ValueError('catalog generator changed; full qualification required')
    if any(before['outputs'][name]['sha256']!=after['outputs'][name]['sha256']
           for name in catalog.OUTPUTS):
        raise ValueError('catalog output changed; full qualification required')
    return {'old_key':old_key,'new_key':new_key,'changed_sources':changed,
            'outputs_identical':True,
            'old_manifest_sha256':sha256_file(root/old_key/'manifest.json'),
            'new_manifest_sha256':sha256_file(root/new_key/'manifest.json'),
            'output_sha256':{n:after['outputs'][n]['sha256'] for n in catalog.OUTPUTS}}


def load_bound(record: dict, project: Path) -> dict:
    relative=record.get('path');expected=record.get('sha256')
    if not isinstance(relative,str) or Path(relative).is_absolute():
        raise ValueError('evidence path must be project-relative')
    path=(project/relative).resolve()
    path.relative_to(project.resolve())
    if not expected or sha256_file(path)!=expected:
        raise ValueError('revalidation evidence hash mismatch')
    return json.loads(path.read_text())


def renewed_config(payload: dict, root: Path, project: Path) -> dict:
    """Called only after the complete original schema-3 adoption gate passes."""
    certificate=load_bound(payload['equivalence'],project)
    if certificate.get('schema_version')!=1 or certificate.get('scope')!=SCOPE:
        raise ValueError('unsupported catalog revalidation contract')
    base=load_bound(certificate['base_adoption'],project)
    if base!={k:v for k,v in payload.items() if k!='equivalence'}:
        raise ValueError('revalidation is bound to a different original qualification')
    build=load_bound(certificate['catalog_build'],project)
    proofs=certificate['pools']
    if set(proofs)!={'weekday','weekend'} or set(build['results'])!=set(proofs):
        raise ValueError('revalidation must cover both pools')
    keys={}
    for pool,proof in proofs.items():
        old=payload['catalog_keys'][pool];new=build['results'][pool]['key']
        size=payload['catalog_selected_n_total'][pool]
        if build['results'][pool]['n_total']!=size:
            raise ValueError('revalidation changed catalog size')
        actual=equivalent_entries(root,pool,old,new,size)
        if actual!=proof:
            raise ValueError('catalog revalidation proof changed')
        keys[pool]=new
    return dict(payload,catalog_keys=keys,validation_scope=SCOPE)
