#!/usr/bin/env python3
"""Verify fixed-job outputs, freeze every DEV row, then grade original rowsets."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import zipfile

import grade_final_test_subset_v1 as GRADE
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE, validate_dev_membership
from restore_session_artifacts_20261014 import sha
from replay_dev_nli_policies_v1 import assemble, pruning_metrics
import kaggle_v7_small_nli_single_v1 as WORKER
from validate_v7_single_raw_v1 import validate_raw

VIEWS = ('title_row', 'body')


def read(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s]


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False);f.write('\n')


def write_rows(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n')


def grade(experiment, artifacts, output):
    if output.exists():raise FileExistsError(output)
    host=experiment/'host'
    freeze=json.loads((experiment/'HOST_INPUT_FREEZE.json').read_text())
    for name,digest in freeze['host_pins'].items():
        if sha(host/name)!=digest:raise ValueError('frozen host input changed: '+name)
    notebook=json.loads((artifacts/'NOTEBOOK_RECEIPT.json').read_text())
    if (notebook['kernel_ref']!=freeze['kernel_ref'] or notebook['returncode']!=0
        or notebook['bundle_manifest_sha256']!=freeze['bundle_manifest_sha256']):
        raise ValueError('fixed notebook output identity/completion differs')
    archive=artifacts/'v7-small-nli-single-results.zip'
    if sha(archive)!=notebook['result_archive_sha256'] or archive.stat().st_size!=notebook['result_archive_bytes']:
        raise ValueError('returned result archive differs from receipt')
    output.mkdir(parents=True,exist_ok=False)
    raw=output/'first-pass-raw';raw.mkdir()
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or len(z.namelist())!=len(set(z.namelist())):
            raise ValueError('result archive CRC/inventory differs')
        if set(z.namelist())!=set(notebook['files']):raise ValueError('result files differ from notebook receipt')
        for name,pin in notebook['files'].items():
            if Path(name).name!=name:raise ValueError('unsafe result member name')
            data=z.read(name)
            import hashlib
            if len(data)!=pin['bytes'] or hashlib.sha256(data).hexdigest()!=pin['sha256']:
                raise ValueError('result member pin differs')
            with (raw/name).open('xb') as f:f.write(data)
    completion=json.loads((raw/'COMPLETION.json').read_text())
    runtime=json.loads((raw/'model-ready.json').read_text())
    execution=json.loads((raw/'EXECUTION_FREEZE.json').read_text())
    if (completion['actual_loaded_models']!=['nli'] or runtime['loaded_models']!=['nli']
        or execution['bundle_manifest_sha256']!=freeze['bundle_manifest_sha256']
        or runtime['cuda_runtime']['device']!='cuda' or not runtime['cuda_runtime']['gpu_name']):
        raise ValueError('single-model CUDA execution not proven')
    model_pin=execution['model_pin']
    if (model_pin!=completion['output_model_pin'] or model_pin['model_id']!=WORKER.NLI.MODEL_ID
        or model_pin['revision']!=WORKER.NLI.MODEL_REVISION
        or model_pin['file_sha256']!=WORKER.NLI.NLI_FILE_SHA256
        or (model_pin['precision'],model_pin['device'])!=('fp32','cuda')):
        raise ValueError('fixed NLI model identity differs')
    ready_pin=runtime['nli_pin']
    for key in ('model_id','revision','precision','device','file_sha256'):
        if ready_pin[key]!=model_pin[key]:raise ValueError('loaded model pin differs: '+key)
    if ready_pin['parameter_count']!=WORKER.NLI.FP32_PARAMETER_COUNT:
        raise ValueError('loaded parameter count differs')
    gpu=runtime['cuda_runtime']
    expected_runtime={'precision':'fp32','autocast':False,'attention_implementation':'eager',
        'cuda_matmul_tf32':False,'cudnn_tf32':False,'batch_size':16,'threads':4}
    if any(gpu.get(k)!=v for k,v in expected_runtime.items()):
        raise ValueError('fixed CUDA arithmetic settings differ')
    for name,pin in completion['output_files'].items():
        p=raw/name
        if p.stat().st_size!=pin['size_bytes'] or sha(p)!=pin['sha256']:
            raise ValueError('worker completion output pin differs')
    for name,digest in execution['code_sha256'].items():
        if sha(experiment/'public-bundle/code'/name)!=digest:
            raise ValueError('executed code differs from uploaded code: '+name)
    contexts=read(experiment/'public-bundle/contexts.jsonl')
    by_context={c['context_ref']:c for c in contexts}
    if len(by_context)!=len(contexts) or len(contexts)!=freeze['context_count']:
        raise ValueError('public context vector differs')
    expected={(c['context_ref'],v,i) for c in contexts for v in VIEWS for i in range(len(c['option_values']))}
    options=read(raw/'option-states.jsonl');states={}
    raw_validation=validate_raw(contexts,read(raw/'raw-scores.jsonl'),options,completion)
    for row in options:
        key=(row['context_ref'],row['view'],row['option_index'])
        if key not in expected or key in states or row['state'] not in ('support','conflict','unknown'):
            raise ValueError('foreign/duplicate/invalid option state')
        if row['option_value']!=by_context[key[0]]['option_values'][key[2]]:
            raise ValueError('whole original option identity differs')
        states[key]=row['state']
    if set(states)!=expected or completion['option_state_count']!=len(expected):
        raise ValueError('all original options/views not complete')
    membership,cases,_=validate_dev_membership(host/'membership.jsonl',host/'case-plans.jsonl',
        host/'row-plans.jsonl',host/'selection-plan.json')
    requests=read(host/'requests.jsonl');request_index=GRADE.unique_index(requests,'request_id','requests')
    bindings=GRADE.unique_index(read(host/'request-bindings.jsonl'),'request_id','bindings')
    tasks,rows=read(host/'tasks.jsonl'),read(host/'row-plans.jsonl')
    if set(bindings)!=set(request_index):raise ValueError('request join coverage differs')
    for rid,binding in bindings.items():
        req=request_index[rid];context=by_context[binding['context_ref']]
        if context['option_values']!=req['option_values'] or context['option_values'][binding['selected_option_index']]!=req['selected_value']:
            raise ValueError('exact source request/selected-option binding differs')
    hint_index={h['task_id']:h for h in read(host/'fresh-v7-task-hints.jsonl')}
    count_summary={};predictions={}
    for view in VIEWS:
        request_states={rid:states[b['context_ref'],view,b['selected_option_index']] for rid,b in bindings.items()}
        request_outputs=[{'request_id':rid,'state':state} for rid,state in request_states.items()]
        row_outputs,decisions=assemble(rows,requests,request_states)
        predictions[view]=decisions
        strata=defaultdict(Counter)
        for task in tasks:
            hint=hint_index.get(task['task_id'],{}).get('producer_condition',{})
            strata[hint.get('status','no_v7_hint')][request_states[task['request_id']]]+=1
        count_summary[view]={'requests':dict(Counter(request_states.values())),
            'rows':dict(Counter(r['state'] for r in row_outputs)),
            'v7_condition_strata':{k:dict(v) for k,v in strata.items()}}
        for label,data in [('requests',request_outputs),('rows',row_outputs),('cases',decisions)]:
            write_rows(output/f'{view}-{label}.jsonl',data)
    outputs={p.name:sha(p) for p in output.glob('*.jsonl')}
    output_freeze={'schema':'v7-small-nli-single-output-freeze-v1','all_views_predictions_frozen':True,
        'host_input_freeze_sha256':sha(experiment/'HOST_INPUT_FREEZE.json'),
        'raw_output_files':{p.name:sha(p) for p in raw.iterdir()},'outputs':outputs,
        'case_count':len(cases),'all_rows':len(rows),'all_conditions':len(tasks),
        'scope_status':'unproved','citation_semantics_supported':False,'production_adoption_allowed':False,
        'independent_test':False,'labels_read':False,'counts':count_summary,
        'grader_code_sha256':sha(Path(__file__)),
        'raw_validator_code_sha256':sha(Path(__file__).with_name('validate_v7_single_raw_v1.py')),
        'raw_validation':raw_validation}
    write(output/'OUTPUT_FREEZE.json',output_freeze)
    for name,digest in outputs.items():
        if sha(output/name)!=digest:raise ValueError('output changed before annotation read')
    references=GRADE.read_label_archives(V2_ARCHIVE,V3_ARCHIVE,membership,host/'row-plans.jsonl')
    scoring_members=[{**m,'split':m['source_version']} for m in membership]
    result={'schema':'v7-small-nli-single-original-rowsets-grade-v1','source_split':'dev','independent_test':False,
        'reference_kind':'Claude Haiku5.5 machine annotation, not human gold','new_luna_inference':False,
        'v7_hints_used_as_proof_or_filter':False,'literal_overlay':False,'jev_prefilter':False,
        'output_freeze_sha256':sha(output/'OUTPUT_FREEZE.json'),'runtime':runtime,
        'completion':completion,'raw_validation':raw_validation,'variants':{}}
    for view in VIEWS:
        result['variants'][view]={'score':GRADE.score_variant(scoring_members,predictions[view],references),
            'pruning':pruning_metrics(read(output/f'{view}-rows.jsonl'),references),'counts':count_summary[view]}
    a={p['case_id']:p for p in predictions['title_row']};b={p['case_id']:p for p in predictions['body']}
    result['same_case_decision_changes']=[cid for cid in a if GRADE.canonical_decision(a[cid])!=GRADE.canonical_decision(b[cid])]
    write(output/'grade.json',result)
    print(json.dumps({v:{'metrics':r['score']['metrics'],'pruning':r['pruning'],'counts':r['counts']}
        for v,r in result['variants'].items()},ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--experiment',type=Path,required=True)
    p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    grade(**vars(p.parse_args()))
