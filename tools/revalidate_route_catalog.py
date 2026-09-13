#!/usr/bin/env python3
"""Renew byte-identical catalogs without repeating the historical 30-pair study.

The full qualification remains the trust anchor. Renewal is restricted to
orchestrator-only source drift, identical inputs/config and all four outputs.
No current end-to-end speed estimate is inherited from the old campaign.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.demand import route_catalog as catalog
from traffic_sim.demand.build_lock import demand_build_lock
from traffic_sim.demand.catalog_revalidation import SCOPE, equivalent_entries, renewed_config
from tools.explain_catalog_fallback import explain

ROOT=Path(__file__).resolve().parents[1]


def bound(path: Path) -> dict:
    return {'path':str(path.resolve().relative_to(ROOT)), 'sha256':sha256_file(path)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,
                        default=Path('validation')/f'catalog_revalidation_{time.time_ns()}.json',
                        help='New evidence file (default: unique timestamp under validation/)')
    parser.add_argument(
        '--catalog-build', type=Path,
        help='Existing fresh build report; otherwise build missing catalogs')
    parser.add_argument(
        '--execute', action='store_true',
        help='Install renewal after all checks; otherwise only create evidence')
    args=parser.parse_args()
    with demand_build_lock():
        started=time.perf_counter()
        adopted=catalog.adopted_catalog_config()
        if adopted is None:
            parser.error('valid original full qualification is required')
        payload=json.loads(catalog.ADOPTION_PATH.read_text())
        payload.pop('equivalence',None)
        # Validate the original record, including every linked evidence file,
        # before freezing it as the renewed record's trust anchor.
        args.out.resolve().relative_to(ROOT)
        if args.out.exists():
            parser.error('--out must be a new evidence file')
        args.out.parent.mkdir(parents=True,exist_ok=True)
        base=args.out.with_suffix('.base.json')
        if base.exists():
            parser.error('base evidence already exists; use a new --out')
        base.write_text(json.dumps(payload,indent=2)+'\n')
        build_path=args.catalog_build or args.out.with_suffix('.build.json')
        if args.catalog_build is None:
            sizes=set(payload['catalog_selected_n_total'].values())
            if len(sizes)!=1:
                parser.error('unequal pool sizes require an explicit --catalog-build report')
            with args.out.with_suffix('.build.log').open('w') as log:
                subprocess.run([sys.executable,'tools/build_route_catalog.py','--execute',
                    '--n-total',str(next(iter(sizes))),'--report',str(build_path)],
                    cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1200)
        build=json.loads(build_path.read_text())
        if set(build['results'])!={'weekday','weekend'}:
            raise ValueError('fresh build must cover both catalogs')
        current=explain(catalog.DEFAULT_ROOT,catalog.ADOPTION_PATH)
        proofs={}
        for pool in ('weekday','weekend'):
            key=build['results'][pool]['key']
            if key!=current['pools'][pool]['current_key']:
                raise ValueError('build report does not describe current inputs')
            size=payload['catalog_selected_n_total'][pool]
            if build['results'][pool]['n_total']!=size:
                raise ValueError('catalog size changed; full qualification required')
            proofs[pool]=equivalent_entries(catalog.DEFAULT_ROOT,pool,
                    payload['catalog_keys'][pool],key,size)
        certificate={'schema_version':1,'scope':SCOPE,'base_adoption':bound(base),
            'catalog_build':bound(build_path),'pools':proofs,
            'elapsed_s':round(time.perf_counter()-started,3),
            'performance_claim': (
                'No new end-to-end speed claim; original campaign remains '
                'historical.')}
        args.out.write_text(json.dumps(certificate,indent=2)+'\n')
        payload['equivalence']=bound(args.out)
        renewed_config(payload,catalog.DEFAULT_ROOT,ROOT)
        if args.execute:
            temporary=catalog.ADOPTION_PATH.with_suffix('.renewal.tmp')
            temporary.write_text(json.dumps(payload,indent=2)+'\n')
            if catalog.adopted_catalog_config(path=temporary) is None:
                raise RuntimeError('staged renewal failed verification')
            os.replace(temporary,catalog.ADOPTION_PATH)
        print(json.dumps({'status':'renewed' if args.execute else 'validated',
            'elapsed_s':certificate['elapsed_s'],'scope':SCOPE,'report':str(args.out)}))


if __name__=='__main__':
    main()
