"""Repair citation units and explicitly adjustable height gates without using gold."""
import argparse, copy, importlib.util, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
OLD=EXP/'results/20261010T201000Z-luna-broad50-v9'
BASE=OLD/'inference'
DIR=HERE/'derived'
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core
import sku_luna_adjustable_dimension_task as task

def sha(p): return core.sha(Path(p).read_bytes())
def utc(): return datetime.now(timezone.utc).isoformat()
def freeze(path,paths,**notes):
    if path.exists():raise RuntimeError(f'freeze exists: {path}')
    core.save(path,{'createdUTC':utc(),'sha256':{str(p.relative_to(EXP)):sha(p) for p in paths},**notes})
def verify(path):
    for rel,expected in core.read(path)['sha256'].items():
        if sha(EXP/rel)!=expected:raise ValueError(f'frozen artifact changed: {rel}')
def prepare():
    oldsig=core.read(BASE/'signature-v3-evaluation.json');m=core.read(BASE/'signature-v3-manifest.json')
    cases={c['case_id']:c for c in core.read(BASE/'inputs.json')};contexts=core.read(BASE/'source-contexts.json')
    selected={}
    for e in m['entries']:
        cid=e['case_id'];s=oldsig[cid]
        if s['passed'] or not s['validation_ok']:continue
        c=cases[cid];r=next(r for r in c['au_rows'] if r['row_key']==e['row_key']);sources=core.smoke.row_sources(c,r)
        for sid,ctx in contexts[cid].items():
            if sid in sources:sources[sid].update({k:ctx[k] for k in ('kind','scope')})
        aliases=task.adjustable_dimension_alias_audit(c,e['targets'],s['answer'],sources,r['conditions'],contexts[cid])
        if aliases or any(i['error']=='dimension_not_proven_by_cited_quote' for i in s['guard_issues']):
            selected[cid]={'prior_guard_issues':s['guard_issues'],'explicit_adjustable_aliases':aliases,'selection':'failed local evidence guard with unsupported citation or explicit discrete same-product height settings; gold/reviews not read'}
    if not selected:raise ValueError('no eligible source/guard repair')
    shutil.copytree(BASE,DIR,ignore=shutil.ignore_patterns('signature-v3-evaluation.json','final-reviews.json','reviewed-summary.json','links.jsonl'))
    calls=[];lineage=[]
    for e in m['entries']:
        cid=e['case_id'];rp=DIR/e['request'];ap=DIR/e['answer']
        old={'request_sha256':sha(BASE/e['request']),'answer_sha256':sha(BASE/e['answer']),'candidate_answer_sha256':e['candidate_answer_sha256']}
        if cid in selected:
            core.save(rp,task.compact_request(core.read(rp)));ap.unlink();e['request_sha256']=sha(rp);e['task_version']='v11-unit-citation-discrete-adjustable-dimensions'
            calls.append({**e,'request':'derived/'+e['request'],'answer':'derived/'+e['answer']})
        else:e['reused_from']={'round':'v9',**old,'same_inputs_contexts_row_targets_matching_answer':True}
        lineage.append({'case_id':cid,'prior':old,'fresh_dimension_task':cid in selected})
    core.save(DIR/'signature-v3-manifest.json',m)
    core.save(HERE/'selection.json',{'createdUTC':utc(),'labels_read':False,'review_answers_read':False,'selected':selected})
    core.save(HERE/'signature-dispatch-manifest.json',{'planned_calls':len(calls),'entries':calls})
    core.save(HERE/'lineage.json',{'new_distinct_cases':0,'paired_cases':len(cases),'fresh_dimension_tasks':len(calls),'reused_signatures':len(m['entries'])-len(calls),'entries':lineage})
    paths=[HERE/'run.py',HERE/'selection.json',HERE/'signature-dispatch-manifest.json',HERE/'lineage.json',OLD/'frozen-protocol.json',OLD/'signature-freeze.json',BASE/'signature-v3-evaluation.json',EXP/'sku_luna_adjustable_dimension_task.py']+[p for p in DIR.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    freeze(HERE/'frozen-protocol.json',paths,labels_read=False,development_reuse=True)
    print(json.dumps({'selected_dimension_tasks':len(calls),'paired_cases':len(cases)}))

def install_guard():
    cases={c['case_id']:c for c in core.read(DIR/'inputs.json')};contexts=core.read(DIR/'source-contexts.json');registry={}
    for e in core.read(DIR/'signature-v3-manifest.json')['entries']:
        c=cases[e['case_id']];r=next(r for r in c['au_rows'] if r['row_key']==e['row_key']);sources=core.smoke.row_sources(c,r);identity=core.canonical([e['targets'],sources,r['conditions']]);scoped=copy.deepcopy(sources)
        for sid,ctx in contexts[c['case_id']].items():
            if sid in scoped:scoped[sid].update({k:ctx[k] for k in ('kind','scope')})
        registry[identity]=(c,scoped,contexts[c['case_id']])
    def guard(targets,answer,sources,conditions):
        c,scoped,ctx=registry[core.canonical([targets,sources,conditions])]
        return task.guard_adjustable_dimensions(c,targets,answer,scoped,conditions,ctx)
    core.guard_dimensions=guard

def reviews_prepare():
    verify(HERE/'frozen-protocol.json');install_guard();sig=core.signature_results(DIR,'signature-v3')
    changed=set(core.read(HERE/'selection.json')['selected']);cases={c['case_id']:c for c in core.read(DIR/'inputs.json')}
    if not (BASE/'reviewed-summary.json').exists():raise RuntimeError('V9 review collection must finish first')
    old_entries=core.read(OLD/'review-dispatch-manifest.json')['entries']
    (HERE/'review-requests').mkdir(exist_ok=False);(HERE/'review-answers').mkdir(exist_ok=False);entries=[]
    for i,old in enumerate(old_entries,1):
        cid=old['case_id'];rp=HERE/f'review-requests/{i:02}.json';ap=HERE/f'review-answers/{i:02}.json';e={'case_id':cid,'case_ids':[cid],'request':str(rp.relative_to(HERE)),'answer':str(ap.relative_to(HERE)),'inference_required':cid in changed}
        if cid in changed:
            req=core.read(OLD/old['request']);item=req['cases'][0];s=sig[cid];row=next(r for r in cases[cid]['au_rows'] if r['row_key']==s['row_key'])
            def ref(sid):return row['conditions'][int(sid[1:])]['condition_id'] if sid.startswith('A') and sid[1:].isdigit() else sid
            item['dimension_verification']={k:s.get(k) for k in ('passed','applicable','validation_ok','guard_issues')}
            item['dimension_verification']['expanded_checks']=[{'target':{k:ch['target'].get(k) for k in ('axis','value','unit')},'status':ch['status'],'evidence':[{'source_id':ref(x['source_id']),'quote':x['quote']} for x in ch['evidence']]} for ch in s['expanded_checks']]
            req['instructions']+='\n単位を直接示す原文引用を確認する。離散的な同じ商品の高さ設定は、現在行が別固定高さSKUを選ばず、原文が高さ段階調節を明記する場合だけ設定集合として比較可能。原文の単なる範囲/別商品/別部位を許容値と推測しない。'
            core.save(rp,req)
        else:
            rp.write_bytes((OLD/old['request']).read_bytes());ap.write_bytes((OLD/old['answer']).read_bytes());e['reused_from']={'round':'v9','request_sha256':sha(rp),'answer_sha256':sha(ap),'same_inputs_contexts_prediction_matching_answer_signature_gate':True}
        e['request_sha256']=sha(rp);entries.append(e)
    core.save(HERE/'review-dispatch-manifest.json',{'case_coverage':len(entries),'entries':entries})
    fresh=[e for e in entries if e['inference_required']];core.save(HERE/'new-review-dispatch-manifest.json',{'planned_calls':len(fresh),'entries':fresh})
    paths=[HERE/'run.py',DIR/'signature-v3-evaluation.json',HERE/'review-dispatch-manifest.json']+[HERE/e['request'] for e in entries]+[HERE/e['answer'] for e in entries if not e['inference_required']]+[DIR/e['answer'] for e in core.read(DIR/'signature-v3-manifest.json')['entries']]
    freeze(HERE/'review-freeze.json',paths,fresh_reviews=len(fresh),reused_reviews=len(entries)-len(fresh),labels_read=False)
    print(json.dumps({'dimension_passed':sum(s['passed'] for s in sig.values()),'fresh_reviews':len(fresh),'reused_reviews':len(entries)-len(fresh)}))

def reviews_collect():
    verify(HERE/'frozen-protocol.json');verify(HERE/'review-freeze.json');install_guard()
    spec=importlib.util.spec_from_file_location('v9_collect',OLD/'run.py');run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)
    run.HERE=HERE;run.DIR=DIR;run.verify_protocol=lambda:verify(HERE/'frozen-protocol.json');run.install_guard=install_guard
    # Its unchanged validator expects run_sha256 in review-freeze. Preserve our
    # full freeze and provide a separate compatibility view through core.read.
    original_read=core.read
    def read(path):
        value=original_read(path)
        if Path(path)==HERE/'review-freeze.json':value={**value,'run_sha256':sha(HERE/'run.py')}
        return value
    core.read=read
    try:run.reviews_collect()
    finally:core.read=original_read
    (HERE/'links-v11.jsonl').write_bytes((DIR/'links.jsonl').read_bytes())

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','reviews-prepare','reviews-collect'))
    {'prepare':prepare,'reviews-prepare':reviews_prepare,'reviews-collect':reviews_collect}[ap.parse_args().command]()
