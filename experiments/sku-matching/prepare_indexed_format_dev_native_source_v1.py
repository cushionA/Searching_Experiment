#!/usr/bin/env python3
"""Runtime route-DEV identity bridge into the unchanged R9-v3 source-only adapter."""
from __future__ import annotations
import hashlib, importlib.util, json, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import sku_body_invariant_native_rakuten_source_v1 as NATIVE
BODY=NATIVE.BASE

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8').splitlines() if x.strip()]
def manifest(input_dir: Path):
    m=json.loads((input_dir/'manifest.json').read_text(encoding='utf-8'))
    req={'schema_version':'sku-next-body-indexed-dev-source-v1','dataset':'sku-next-body-indexed-format-dev-20261013-v1','split':'dev','independent_test':False,'selected_product_count':2,'selected_case_count':4,'labels_read':False,'predictions_read':False,'model_inference_run':False,'scores_created':False,'test_split_read':False,'production_adoption_allowed':False}
    if any(m.get(k)!=v for k,v in req.items()): raise ValueError('indexed format DEV source scope/blindness guard failed')
    for name,d in m['outputs_sha256'].items():
        if sha(input_dir/name)!=d: raise ValueError('indexed DEV source output SHA mismatch: '+name)
    if sha(input_dir/'selection-policy.json')!=m['selection_policy_sha256'] or sha(input_dir/'selection-plan.json')!=m['selection_plan_sha256']: raise ValueError('identity plan hash mismatch')
    for key,file in [('row_count','row-plans.jsonl'),('task_count','tasks.jsonl'),('derived_request_count','requests.jsonl'),('document_count','documents.jsonl')]:
        if len(rows(input_dir/file))!=m[key]: raise ValueError('cohort manifest population mismatch: '+key)
    m['selected_row_count']=m['row_count'];m['selected_task_count']=m['task_count']
    return m

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=ROOT/'.lab-output/sku-next-body-indexed-format-dev-20261013-v1/prepared-source-v1');p.add_argument('--source-root',type=Path,default=ROOT/'.lab-output/sku-full-evaluation-prepare-20261013-v1/source');p.add_argument('--output',type=Path,default=ROOT/'.lab-output/sku-next-body-indexed-format-dev-20261013-v1/scoped-native-source-v1');a=p.parse_args()
    original=BODY._manifest
    BODY._manifest=manifest
    try: out=NATIVE.prepare(a.input,a.source_root,a.output)
    finally: BODY._manifest=original
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
