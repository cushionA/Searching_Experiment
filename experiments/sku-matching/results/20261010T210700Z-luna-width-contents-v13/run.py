"""Source-selected paired repair of width-specific curtain package matching."""
import argparse, copy, importlib.util, re, shutil, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
OLD=EXP/'results/20261010T205900Z-luna-closed-review-v12'
BASE=OLD/'derived'
DIR=HERE/'derived'
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core
import sku_luna_compact_general_task as general
import sku_luna_closed_contents_task as closed
import sku_luna_current_row_dimension_task as dimensions

PACKAGE_RULE='''カーテンは現在行の幅に対応する内容表を先に確認し、主カーテン、レース、タッセル、フックを別の構成品として数える。幅100と幅150の表を取り違えない。タイトル全体の4枚等は幅別の内容を上書きしない。現在AUサイズの括弧内「枚/枚組」は内容原文から主カーテンのみか主＋レースの合計かを確認する。楽天の選択済みサイズが幅×丈のみで枚数を指定していない場合、別の幅の個数を追加制約にしない。選択済みレースあり/なしは同じ幅の固定商品同梱物と照合する。bounded_contentsの完結した全構成品列挙と次section境界を確認できる場合だけ、その一覧を不存在の根拠にも使える。単なる語の欠落や曖昧な範囲はunknownとする。検索ワードや別商品を根拠にせず、指定された全条件を個別に照合する。support/contradictionでは対応する固定商品原文または現在行条件のsource_idsを直接引用する。出力形式や行数、条件数はresponseSchemaに従う。'''

def sha(p):return core.sha(Path(p).read_bytes())
def freeze(path,paths,**notes):
    if path.exists():raise ValueError('freeze already exists')
    core.save(path,{'createdUTC':datetime.now(timezone.utc).isoformat(),'sha256':{str(p.relative_to(EXP)):sha(p) for p in paths},**notes})
def verify(path):
    for rel,h in core.read(path)['sha256'].items():
        if sha(EXP/rel)!=h:raise ValueError('frozen artifact changed: '+rel)
def norm(x):return re.sub(r'\s+','',unicodedata.normalize('NFKC',x)).lower()
def compatible_rows(case):
    rv={r['axis']:r['value'] for r in case['rakuten_conditions']}
    size=re.fullmatch(r'(\d+(?:\.\d+)?)×(\d+(?:\.\d+)?)cm',norm(rv.get('サイズ','')))
    if not size:return []
    found=[]
    for row in case['au_rows']:
        vals={c['axis']:c['value'] for c in row['conditions']}
        rs=re.fullmatch(r'幅(\d+(?:\.\d+)?)×丈(\d+(?:\.\d+)?)cm\(\d+枚(?:組)?\)',norm(vals.get('サイズ','')))
        if rs and rs.groups()==size.groups() and norm(vals.get('カラー',''))==norm(rv.get('カラー','')):found.append(row['row_key'])
    return found
def install_guard():
    p=EXP/'results/20261010T204500Z-luna-adjustable-dimensions-v11/run_v2.py'
    spec=importlib.util.spec_from_file_location('v11_composed_guard',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.run.DIR=DIR;m.install_guard()
def prepare():
    cases=core.read(BASE/'inputs.json');contexts=core.read(BASE/'source-contexts.json')
    final={c['case_id']:c for c in core.read(BASE/'reviewed-summary.json')['cases']};selected={}
    for c in cases:
        cid=c['case_id']
        if final[cid]['decision']!='pending' or not any(r['axis']=='レースカーテン' for r in c['rakuten_conditions']):continue
        rows=compatible_rows(c)
        if len(rows)!=1:continue
        block=closed._raw_contents_block(c,contexts[cid])
        selected[cid]={'source_compatible_row_key':rows[0],'bounded_contents_source_id':block['source_id'],'source_sha256':block['raw_file_sha256'],'selection':'pending with exactly one selected color/width/length row and verified bounded current-product contents; no gold selection'}
    if not selected:raise ValueError('no source-eligible repair')
    shutil.copytree(BASE,DIR,ignore=shutil.ignore_patterns('evaluation.json','signature-v3-evaluation.json','final-reviews.json','reviewed-summary.json','links.jsonl'))
    (HERE/'matching-chunk-requests').mkdir();(HERE/'matching-chunk-answers').mkdir();entries=[]
    manifest=core.read(DIR/'manifest.json')
    for c in cases:
        cid=c['case_id']
        if cid not in selected:continue
        me=next(e for e in manifest['entries'] if e['case_id']==cid)
        # The full contract is never sent; every original row is sent once in chunks.
        full=general.compact_request(c,contexts[cid]);full['body']['contents'][0]['parts'][0]['text']=full['body']['contents'][0]['parts'][0]['text'].replace('INPUT=',PACKAGE_RULE+'\nINPUT=',1)
        core.save(DIR/me['request'],full);me['request_sha256']=sha(DIR/me['request']);(DIR/me['answer']).unlink()
        for start in range(0,len(c['au_rows']),27):
            part=copy.deepcopy(c);part['au_rows']=part['au_rows'][start:start+27]
            req=general.compact_request(part,contexts[cid]);req['body']['contents'][0]['parts'][0]['text']=req['body']['contents'][0]['parts'][0]['text'].replace('INPUT=',PACKAGE_RULE+'\nINPUT=',1)
            i=len(entries)+1;rp=HERE/f'matching-chunk-requests/{i:03}.json';core.save(rp,req)
            entries.append({'case_id':f'{cid}:rows:{start}:{start+len(part["au_rows"])}','original_case_id':cid,'row_start':start,'row_count':len(part['au_rows']),'request':str(rp.relative_to(HERE)),'answer':f'matching-chunk-answers/{i:03}.json','request_sha256':sha(rp)})
    core.save(DIR/'manifest.json',manifest);shutil.copyfile(DIR/'manifest.json',HERE/'manifest-before-inference.json');shutil.copyfile(DIR/'signature-v3-manifest.json',HERE/'signature-manifest-before-inference.json')
    core.save(HERE/'selection.json',{'selected':selected,'gold_used_for_selection':False,'prior_metrics_observed':True,'new_distinct_pairs':0})
    core.save(HERE/'matching-dispatch-manifest.json',{'planned_calls':len(entries),'paired_cases':len(selected),'entries':entries})
    paths=[HERE/'run.py',HERE/'selection.json',HERE/'matching-dispatch-manifest.json',HERE/'manifest-before-inference.json',HERE/'signature-manifest-before-inference.json',OLD/'grading-freeze.json',EXP/'sku_luna_compact_general_task.py',EXP/'sku_luna_closed_contents_task.py',EXP/'sku_luna_current_row_dimension_task.py']+[p for p in DIR.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in ('manifest.json','signature-v3-manifest.json')]+[HERE/e['request'] for e in entries]
    freeze(HERE/'frozen-protocol.json',paths,development_reuse=True,fresh_matching_cases=len(selected),reused_matching_cases=len(cases)-len(selected))
    print({'matching_chunks':len(entries),'selected_cases':len(selected),'fresh_rows':sum(e['row_count'] for e in entries)})
def matching_collect():
    verify(HERE/'frozen-protocol.json');cases={c['case_id']:c for c in core.read(DIR/'inputs.json')};groups={cid:[] for cid in core.read(HERE/'selection.json')['selected']}
    for e in core.read(HERE/'matching-dispatch-manifest.json')['entries']:
        if sha(HERE/e['request'])!=e['request_sha256']:raise ValueError('chunk request changed')
        c=copy.deepcopy(cases[e['original_case_id']]);c['au_rows']=c['au_rows'][e['row_start']:e['row_start']+e['row_count']]
        raw=core.read(HERE/e['answer']);ok,why=core.smoke.validate(c,raw)
        if not ok:raise ValueError(f'chunk validation: {why}')
        groups[c['case_id']].append((e,raw))
    manifest=core.read(DIR/'manifest.json')
    for me in manifest['entries']:
        cid=me['case_id']
        if cid not in groups:continue
        parts=sorted(groups[cid],key=lambda p:p[0]['row_start']);start=0
        for e,raw in parts:
            if e['row_start']!=start:raise ValueError('gap/overlap')
            start+=e['row_count']
        if start!=len(cases[cid]['au_rows']):raise ValueError('missing rows')
        if (DIR/me['answer']).exists():raise ValueError('refusing overwrite')
        core.save(DIR/me['answer'],{'rows':[r for _,raw in parts for r in raw['rows']]})
        me['output_origin']={'method':'all original rows once; input-order unchanged raw chunk concatenation','chunks':[{**e,'answer_sha256':sha(HERE/e['answer'])} for e,_ in parts],'composed_answer_sha256':sha(DIR/me['answer'])}
    core.save(DIR/'manifest.json',manifest);ev=core.evaluate(DIR);core.save(HERE/'matching-chunk-evaluation.json',{'task_count':sum(len(p) for p in groups.values()),'validation_pass':sum(len(p) for p in groups.values()),'fresh_case_count':len(groups),'all_cases_structurally_valid':all(c['validation_ok'] for c in ev['cases']),'counts':ev['counts']})
    print(ev['counts'])
def signature_prepare():
    verify(HERE/'frozen-protocol.json');cases={c['case_id']:c for c in core.read(DIR/'inputs.json')};contexts=core.read(DIR/'source-contexts.json');ev=core.evaluate(DIR)
    m=core.read(DIR/'signature-v3-manifest.json');old={e['case_id']:e for e in m['entries']};fresh=[]
    for c in ev['cases']:
        if c['decision']!='candidate':continue
        cid=c['case_id']
        if cid in old:
            if old[cid]['candidate_answer_sha256']!=c['answer_sha256']:raise ValueError('changed old candidate needs explicit signature binding')
            continue
        case=cases[cid];row=next(r for r in case['au_rows'] if r['row_key']==c['candidate_row_key']);targets=[a for a in case['selected_attributes'] if a.get('unit') in {'mm','cm','m'}]
        i=len(m['entries'])+1;rp=DIR/f'signature-v3-requests/{i:02}.json';ap=DIR/f'signature-v3-answers/{i:02}.json'
        req=dimensions.request(case,row,[{k:a.get(k) for k in ('axis','value','unit')} for a in targets],contexts[cid]);core.save(rp,req)
        e={'case_id':cid,'row_key':row['row_key'],'targets':targets,'candidate_answer_sha256':c['answer_sha256'],'request':str(rp.relative_to(DIR)),'answer':str(ap.relative_to(DIR)),'request_sha256':sha(rp),'inference_required':bool(targets),'task_version':'v13-current-row-width-length'}
        m['entries'].append(e)
        if targets:fresh.append({**e,'request':'derived/'+e['request'],'answer':'derived/'+e['answer']})
        else:core.save(ap,{'checks':[]})
    core.save(DIR/'signature-v3-manifest.json',m);core.save(HERE/'signature-dispatch-manifest.json',{'planned_calls':len(fresh),'entries':fresh})
    paths=[HERE/'run.py',HERE/'signature-dispatch-manifest.json',DIR/'manifest.json',DIR/'signature-v3-manifest.json',EXP/'sku_luna_current_row_dimension_task.py']+[DIR/e['request'] for e in m['entries']]+[DIR/e['answer'] for e in m['entries'] if (DIR/e['answer']).exists()]+[DIR/e['answer'] for e in core.read(DIR/'manifest.json')['entries']]
    freeze(HERE/'signature-freeze.json',paths,fresh_signature_tasks=len(fresh),prior_signatures_unchanged=len(old))
    print({'fresh_signatures':len(fresh),'total':len(m['entries'])})
def reviews_prepare():
    verify(HERE/'frozen-protocol.json');verify(HERE/'signature-freeze.json');install_guard();sig=core.signature_results(DIR,'signature-v3');ev={c['case_id']:c for c in core.evaluate(DIR)['cases']};cases={c['case_id']:c for c in core.read(DIR/'inputs.json')};selected=set(core.read(HERE/'selection.json')['selected'])
    (HERE/'review-requests').mkdir();(HERE/'review-answers').mkdir();entries=[]
    for i,old in enumerate(core.read(OLD/'review-dispatch-manifest.json')['entries'],1):
        cid=old['case_id'];rp=HERE/f'review-requests/{i:02}.json';ap=HERE/f'review-answers/{i:02}.json';e={'case_id':cid,'case_ids':[cid],'request':str(rp.relative_to(HERE)),'answer':str(ap.relative_to(HERE)),'inference_required':cid in selected}
        if cid in selected:
            req=core.read(OLD/old['request']);item=req['cases'][0];req['instructions']+='\n'+PACKAGE_RULE
            item['prediction']={k:ev[cid].get(k) for k in ('decision','candidate_row_key','validation_ok','validation_reason','guard_issues')};trace=[];rowmap={r['row_key']:r for r in cases[cid]['au_rows']}
            def ref(sid,row):return row['conditions'][int(sid[1:])]['condition_id'] if sid.startswith('A') and sid[1:].isdigit() else sid
            for row in ev[cid]['expanded_response']['rows']:
                trace.append({'row_key':row['row_key'],**{side:[{'condition_id':c['condition_id'],'status':c['status'],'source_ids':[ref(x['source_id'],rowmap[row['row_key']]) for x in c['evidence']]} for c in row[side]] for side in ('rakuten_checks','au_checks')}})
            item['matching_evidence_trace']=trace;s=sig.get(cid,{})
            item['dimension_verification']={k:s.get(k) for k in ('passed','applicable','validation_ok','guard_issues')};item['dimension_verification']['expanded_checks']=[]
            for ch in s.get('expanded_checks') or []:
                row=rowmap[s['row_key']];item['dimension_verification']['expanded_checks'].append({'target':{k:ch['target'].get(k) for k in ('axis','value','unit')},'status':ch['status'],'evidence':[{'source_id':ref(x['source_id'],row),'quote':x['quote']} for x in ch['evidence']]})
            core.save(rp,req)
        else:
            rp.write_bytes((OLD/old['request']).read_bytes());ap.write_bytes((OLD/old['answer']).read_bytes());e['reused_from']={'round':'v12','request_sha256':sha(rp),'answer_sha256':sha(ap),'same_inputs_contexts_matching_signature_gates':True}
        e['request_sha256']=sha(rp);entries.append(e)
    core.save(HERE/'review-dispatch-manifest.json',{'case_coverage':len(entries),'entries':entries});fresh=[e for e in entries if e['inference_required']];core.save(HERE/'new-review-dispatch-manifest.json',{'planned_calls':len(fresh),'entries':fresh})
    paths=[HERE/'run.py',HERE/'review-dispatch-manifest.json',DIR/'signature-v3-evaluation.json']+[HERE/e['request'] for e in entries]+[HERE/e['answer'] for e in entries if not e['inference_required']]+[DIR/e['answer'] for e in core.read(DIR/'signature-v3-manifest.json')['entries']]
    freeze(HERE/'review-freeze.json',paths,fresh_reviews=len(fresh),reused_reviews=len(entries)-len(fresh));print({'fresh_reviews':len(fresh),'signature_passed':sum(s['passed'] for s in sig.values()),'signature_total':len(sig)})
def reviews_collect():
    verify(HERE/'frozen-protocol.json');verify(HERE/'signature-freeze.json');verify(HERE/'review-freeze.json')
    spec=importlib.util.spec_from_file_location('v9_validator',EXP/'results/20261010T201000Z-luna-broad50-v9/run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.HERE=HERE;m.DIR=DIR;m.verify_protocol=lambda:verify(HERE/'frozen-protocol.json');m.install_guard=install_guard
    orig=core.read
    def read(path):
        x=orig(path)
        return {**x,'run_sha256':sha(HERE/'run.py')} if Path(path)==HERE/'review-freeze.json' else x
    core.read=read
    try:m.reviews_collect()
    finally:core.read=orig
    (HERE/'links-v13.jsonl').write_bytes((DIR/'links.jsonl').read_bytes())
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','matching-collect','signature-prepare','reviews-prepare','reviews-collect'));cmd=ap.parse_args().command
    {'prepare':prepare,'matching-collect':matching_collect,'signature-prepare':signature_prepare,'reviews-prepare':reviews_prepare,'reviews-collect':reviews_collect}[cmd]()
