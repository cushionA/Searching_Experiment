"""Preserve invalid new chunks and reuse the prior raw case when coverage fails."""
import copy, importlib.util
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v13_frozen_run',HERE/'run.py')
run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)
core=run.core

def main():
    run.verify(HERE/'frozen-protocol.json')
    entries=core.read(HERE/'matching-dispatch-manifest.json')['entries']
    oldmanifest={e['case_id']:e for e in core.read(run.BASE/'manifest.json')['entries']}
    selected=core.read(HERE/'selection.json')['selected']
    paths=[HERE/'collect_v2.py',HERE/'frozen-protocol.json',run.BASE/'manifest.json',run.BASE/'inputs.json',run.BASE/'source-contexts.json']+[HERE/e['answer'] for e in entries]+[run.BASE/oldmanifest[cid][k] for cid in selected for k in ('request','answer')]
    run.freeze(HERE/'matching-collector-freeze.json',paths,policy='all new chunks must validate for the complete case; otherwise preserve failed artifacts and byte reuse the complete prior raw request/answer for that case; no repeat inference')
    cases={c['case_id']:c for c in core.read(run.DIR/'inputs.json')};groups={cid:[] for cid in selected}
    for e in entries:
        if run.sha(HERE/e['request'])!=e['request_sha256']:raise ValueError('request changed')
        c=copy.deepcopy(cases[e['original_case_id']]);c['au_rows']=c['au_rows'][e['row_start']:e['row_start']+e['row_count']]
        raw=core.read(HERE/e['answer']);ok,why=core.smoke.validate(c,raw);groups[c['case_id']].append((e,raw,ok,why))
    (run.DIR/'matching-fallback-requests').mkdir();(run.DIR/'matching-fallback-answers').mkdir()
    manifest=core.read(run.DIR/'manifest.json');fallback=[];fresh_valid=0
    for me in manifest['entries']:
        cid=me['case_id']
        if cid not in groups:continue
        parts=sorted(groups[cid],key=lambda p:p[0]['row_start']);start=0
        for e,raw,ok,why in parts:
            if e['row_start']!=start:raise ValueError('coverage gap/overlap')
            start+=e['row_count']
        if start!=len(cases[cid]['au_rows']):raise ValueError('coverage missing')
        trace=[{**e,'answer_sha256':run.sha(HERE/e['answer']),'validation_ok':ok,'validation_reason':why} for e,raw,ok,why in parts]
        if all(ok for _,_,ok,_ in parts):
            dest=run.DIR/me['answer']
            if dest.exists():raise ValueError('refusing overwrite')
            core.save(dest,{'rows':[row for _,raw,_,_ in parts for row in raw['rows']]});fresh_valid+=1
            me['output_origin']={'method':'all original rows once; input-order unchanged raw chunk concatenation','chunks':trace,'composed_answer_sha256':run.sha(dest)}
        else:
            old=oldmanifest[cid];i=len(fallback)+1;rp=run.DIR/f'matching-fallback-requests/{i:02}.json';ap=run.DIR/f'matching-fallback-answers/{i:02}.json'
            rp.write_bytes((run.BASE/old['request']).read_bytes());ap.write_bytes((run.BASE/old['answer']).read_bytes())
            me['request']=str(rp.relative_to(run.DIR));me['answer']=str(ap.relative_to(run.DIR));me['request_sha256']=run.sha(rp)
            me['output_origin']={'method':'prior V12 complete request/answer exact raw reuse after invalid new chunk; new task not counted successful','round':'v12','request_sha256':run.sha(rp),'answer_sha256':run.sha(ap),'failed_new_chunks':trace,'same_inputs_contexts_all_rows':True}
            fallback.append({'case_id':cid,'new_attempt_success':False,'failed_chunks':[t for t in trace if not t['validation_ok']],'prior_raw_request_sha256':run.sha(rp),'prior_raw_answer_sha256':run.sha(ap)})
    core.save(run.DIR/'manifest.json',manifest);ev=core.evaluate(run.DIR)
    core.save(HERE/'matching-chunk-evaluation.json',{'task_count':len(entries),'validation_pass':sum(ok for parts in groups.values() for _,_,ok,_ in parts),'fresh_case_count':len(groups),'fresh_complete_valid_cases':fresh_valid,'prior_raw_fallback_cases':fallback,'all_cases_structurally_valid':all(c['validation_ok'] for c in ev['cases']),'counts':ev['counts']})
    print({'chunk_pass':sum(ok for parts in groups.values() for _,_,ok,_ in parts),'chunk_total':len(entries),'fresh_cases_valid':fresh_valid,'fallback_cases':len(fallback),'counts':ev['counts']})
if __name__=='__main__':main()
