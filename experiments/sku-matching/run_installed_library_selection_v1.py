#!/usr/bin/env python3
"""Compare selected catalog values using native library views, without a parser.

Only this experiment adapter is new. No SKU grammar, alias dictionary, numeric
extractor, or classifier is defined here. All original conditions pass through
the existing gate; normalized equality is an experimental hypothesis.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

from replay_dev_nli_policies_v1 import assemble


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def read(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s]


def write(path,data):
    with path.open('x',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def write_rows(path,rows):
    with path.open('x',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')


def catalogs(requests,documents):
    """Index raw, complete native option vectors already present in the input."""
    docs={d['document_id']:d for d in documents}
    if len(docs)!=len(documents):raise ValueError('duplicate document ID')
    families=defaultdict(set)
    for r in requests:
        d=docs[r['target_document_id']]
        side='rakuten' if r['direction']=='rakuten_to_au' else 'au'
        families[d['dossier_id'],side,r['axis_name']].add(tuple(r['option_values']))
    return docs,dict(families)


def selected_state(request,docs,families,view):
    """Selected-option lookup, retaining unknown axes and ambiguous catalogs."""
    name=request['axis_name']
    matches=[a for a in request['target_selected_axes'] if a['axis_name']==name]
    if not name or len(matches)!=1:return 'unknown','target_axis_missing_or_ambiguous'
    doc=docs[request['target_document_id']]
    vectors=families.get((doc['dossier_id'],doc['side'],name),set())
    if len(vectors)!=1:return 'unknown','target_catalog_missing_or_inconsistent'
    target=list(next(iter(vectors)))
    source=request['option_values']
    if (request.get('source_family_status','complete')!='complete' or not source or not target
            or any(not isinstance(v,str) or not v for v in source+target)):
        return 'unknown','incomplete_native_catalog'
    source_keys=[view[v] for v in source]
    target_keys=[view[v] for v in target]
    if len(set(source_keys))!=len(source_keys) or len(set(target_keys))!=len(target_keys):
        return 'unknown','library_view_catalog_collision'
    selected=request['selected_value']
    actual=matches[0]['value']
    if selected not in source or actual not in target:return 'unknown','selected_value_not_in_native_catalog'
    selected_key,actual_key=view[selected],view[actual]
    # A shared identifier can be looked up without equating the two entire
    # families. Different sets alone never prove a contradiction.
    if selected_key not in target_keys:return 'unknown','selected_value_has_no_catalog_counterpart'
    if selected_key==actual_key:return 'support','selected_values_equal_in_library_view'
    return 'conflict','different_native_options_under_shared_identifier'


def run(source,views_path,output):
    if output.exists():raise FileExistsError(output)
    names=('requests.jsonl','documents.jsonl','row-plans.jsonl','case-plans.jsonl','tasks.jsonl')
    pins={n:sha(source/n) for n in names}
    view_pin=sha(views_path);code_pin=sha(Path(__file__))
    requests,documents,rows,cases=(read(source/n) for n in names[:4])
    if len({r['request_id'] for r in requests})!=len(requests):raise ValueError('duplicate request ID')
    if (len(cases),len(rows),sum(len(r['bindings']) for r in rows))!=(1370,32325,132136):
        raise ValueError('full original DEV row/condition coverage required')
    by_case=defaultdict(list)
    bindings=[]
    for row in rows:
        by_case[row['case_id']].append(row['au_row_key'])
        bindings.extend(b['task_id'] for b in row['bindings'])
    if len(bindings)!=len(set(bindings)):raise ValueError('duplicate task binding')
    if any(by_case[c['case_id']]!=c['row_keys'] for c in cases):raise ValueError('original row order/coverage differs')
    payload=json.loads(views_path.read_text(encoding='utf-8'))
    views=payload['views']
    expected={v for r in requests for v in r['option_values']}|{a['value'] for r in requests for a in r['target_selected_axes']}
    if any(set(v)!=expected for v in views.values()):raise ValueError('library view input coverage differs')
    docs,families=catalogs(requests,documents)
    output.mkdir(parents=True)
    start=time.perf_counter();counts={}
    for mode,view in views.items():
        states={};results=[]
        for request in requests:
            state,reason=selected_state(request,docs,families,view)
            states[request['request_id']]=state
            results.append({'request_id':request['request_id'],'state':state,'reason':reason,
                'raw_selected_value':request['selected_value'],
                'view_selected_value':view[request['selected_value']],
                'production_adoption_allowed':False})
        row_results,decisions=assemble(rows,requests,states)
        # The existing binary gate calls all non-accepts drop. Preserve review
        # separately so absence cannot be mistaken for an explicit proof.
        row_groups=defaultdict(list)
        for row in row_results:row_groups[row['case_id']].append(row['state'])
        for d in decisions:
            d['disposition']=('accept' if d['decision']=='accept' else
                'reject' if all(s=='conflict' for s in row_groups[d['case_id']]) else 'review')
        for kind,data in (('requests',results),('rows',row_results),('cases',decisions)):
            write_rows(output/f'{mode}-{kind}.jsonl',data)
        counts[mode]={'requests':dict(Counter(states.values())),
            'rows':dict(Counter(r['state'] for r in row_results)),
            'cases':dict(Counter(d['disposition'] for d in decisions)),
            'reasons':dict(Counter(r['reason'] for r in results))}
    if sha(views_path)!=view_pin or sha(Path(__file__))!=code_pin:raise ValueError('code/views changed')
    if any(sha(source/n)!=p for n,p in pins.items()):raise ValueError('source changed')
    freeze={'schema':'installed-library-selected-output-freeze-v1','source_path':str(source.resolve()),
        'input_pins':pins,'view_path':str(views_path.resolve()),'view_sha256':view_pin,
        'code_sha256':code_pin,'gate_code_sha256':sha(Path(__file__).with_name('replay_dev_nli_policies_v1.py')),
        'labels_read':False,'independent_test':False,'source_split':'dev','counts':counts,
        'elapsed_seconds':time.perf_counter()-start,'versions':payload['versions'],
        'custom_parser_used':False,'custom_alias_dictionary_used':False,'grammar_rules_added':False,
        'learned_model_used':False,'fuzzy_score_used_for_final_state':False,
        'normalized_equality_is_semantic_proof':False,'production_adoption_allowed':False,
        'output_pins':{p.name:sha(p) for p in output.iterdir() if p.is_file()}}
    write(output/'OUTPUT_FREEZE.json',freeze)
    print(json.dumps({'elapsed_seconds':freeze['elapsed_seconds'],'counts':counts},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--views',dest='views_path',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    run(**vars(p.parse_args()))
