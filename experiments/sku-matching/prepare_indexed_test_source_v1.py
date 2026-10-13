#!/usr/bin/env python3
"""Create a TEST identity-bound source cohort using the frozen indexed DEV derivation."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
EXPECTED_MEMBERSHIP_SHA256='09b11b352cead06686eb5f446b4ed713d8391f08255cd2731ec8c263ddf9a4fa'
DATASET='sku-next-body-indexed-test-20261013-v1'
SCHEMA='sku-body-final-test-identity-plan-v1'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8').splitlines() if x.strip()]
def write_json(p,x):
    with Path(p).open('x',encoding='utf-8') as f: json.dump(x,f,ensure_ascii=False,sort_keys=True,indent=2); f.write('\n')
def write_rows(p,xs):
    with Path(p).open('x',encoding='utf-8') as f:
        for x in xs: f.write(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')

def validate_identity_plan(m, selected, digest, expected_digest=EXPECTED_MEMBERSHIP_SHA256):
    if m.get('schema_version')!=SCHEMA or m.get('status')!='identity_only_subset_plan_no_test_evidence_read': raise ValueError('pinned TEST identity plan schema/status mismatch')
    if digest!=expected_digest or m.get('outputs_sha256',{}).get('selected-membership.jsonl')!=expected_digest: raise ValueError('pinned TEST selected-membership SHA mismatch')
    if (m.get('selected_product_count'),m.get('selected_case_count'),m.get('selection_based_only_on_identity_fields'))!=(20,40,True): raise ValueError('TEST identity plan counts/scope mismatch')
    if len(selected)!=40 or len({str(x.get('au_product_id')) for x in selected})!=20 or len({x.get('case_id') for x in selected})!=40: raise ValueError('TEST identity membership is incomplete or duplicated')
    return {str(x['case_id']):str(x['au_product_id']) for x in selected}

def prepare(config_path: Path | None):
    cfg=json.loads(config_path.read_text(encoding='utf-8')) if config_path else {}
    plan_dir=Path(cfg.get('identity_plan_dir',ROOT/'.lab-output/sku-body-final-test-identity-plan-20261013-v1')).resolve()
    out=Path(cfg.get('output',ROOT/'.lab-output/sku-final-body-indexed-test-20261013-v1/prepared-source-v1')).resolve()
    plan_manifest=plan_dir/'manifest.json'; membership_file=plan_dir/'selected-membership.jsonl'
    m=json.loads(plan_manifest.read_text(encoding='utf-8'))
    digest=sha(membership_file)
    selected=rows(membership_file)
    expected=validate_identity_plan(m,selected,digest)
    if out.exists() and any(out.iterdir()): raise FileExistsError('TEST source output already contains artifacts')
    out.mkdir(parents=True,exist_ok=True)
    clean=[{'case_id':str(x['case_id']),'au_product_id':str(x['au_product_id'])} for x in selected]
    write_rows(out/'membership.jsonl',clean)
    selection_plan={'schema_version':'sku-next-body-indexed-test-selection-plan-v1','dataset':DATASET,'selected_product_count':20,'selected_case_count':40,'cases_per_product':2,'identity_plan_manifest_sha256':sha(plan_manifest),'selected_membership_sha256':digest,'selected_membership':clean,'selection_based_only_on_identity_fields':True}
    policy={'schema_version':'sku-next-body-indexed-test-selection-policy-v1','source_identity_plan_schema':SCHEMA,'selection_method':'frozen preselected ID-only membership; no condition-text reselection','identity_plan_manifest_sha256':sha(plan_manifest),'selected_membership_sha256':digest,'selected_product_count':20,'selected_case_count':40,'labels_read':False,'predictions_read':False,'condition_text_read_for_selection':False}
    write_json(out/'selection-policy.json',policy)
    selection_plan['policy_sha256']=sha(out/'selection-policy.json')
    write_json(out/'selection-plan.json',selection_plan)
    builder_cfg={'base':str(Path(cfg.get('base',ROOT/'.lab-output/sku-full-condition-adapter-20261013-v2/test')).resolve()),'source_root':str(Path(cfg.get('source_root',ROOT/'.lab-output/sku-full-evaluation-prepare-20261013-v1/source')).resolve()),'output':str(out),'dataset':DATASET,'prior_membership_sha256':digest,'identity_plan_manifest_sha256':sha(plan_manifest),'selected_membership_sha256':digest,'selected_product_count':20,'selected_case_count':40,'cases_per_product':2,'identity_bridge_sha256':sha(Path(__file__))}
    config_out=out/'source-builder-config.json';write_json(config_out,builder_cfg)
    cmd=[sys.executable,str(HERE/'prepare_indexed_test_source_generic_v1.py'),'--config',str(config_out)]
    subprocess.run(cmd,check=True)
    # Bind source receipt to the exact identity-only plan manifest in the final artifact inventory.
    final=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    final['identity_plan_manifest_sha256']=sha(plan_manifest); final['selected_membership_sha256']=digest
    final['identity_plan_manifest_path']=str(plan_manifest.relative_to(ROOT)); final['selected_membership_path']=str(membership_file.relative_to(ROOT)); final['identity_bridge_sha256']=sha(Path(__file__))
    (out/'manifest.json').write_text(json.dumps(final,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(final,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path);a=p.parse_args();prepare(a.config)
