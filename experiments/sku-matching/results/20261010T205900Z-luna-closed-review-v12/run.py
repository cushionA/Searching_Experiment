"""Apply the previously verified bounded contents review policy to fresh pairs."""
import argparse,copy,importlib.util,json,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
OLD=EXP/'results/20261010T204500Z-luna-adjustable-dimensions-v11'
BASE=OLD/'derived'
DIR=HERE/'derived'
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core

def sha(p):return core.sha(Path(p).read_bytes())
def verify():
    for rel,h in core.read(HERE/'frozen-protocol.json')['sha256'].items():
        if sha(EXP/rel)!=h:raise ValueError(f'changed {rel}')
def install_guard():
    spec=importlib.util.spec_from_file_location('v11_composed_guard',OLD/'run_v2.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    mod.run.DIR=DIR;mod.install_guard()
def prepare():
    cases={c['case_id']:c for c in core.read(BASE/'inputs.json')};sig=core.read(BASE/'signature-v3-evaluation.json');reviews={r['case_id']:r for r in core.read(BASE/'final-reviews.json')};old_entries=core.read(OLD/'review-dispatch-manifest.json')['entries'];selected={}
    for e in old_entries:
        cid=e['case_id'];req=core.read(OLD/e['request']);item=req['cases'][0]
        if (reviews[cid]['verdict']=='unresolved' and sig.get(cid,{}).get('passed')
            and any(c['axis']=='レースカーテン' and c['value']=='なし' for c in cases[cid]['rakuten_conditions'])
            and item.get('bounded_contents',{}).get('scope')=='fixed_product'):
            selected[cid]={'selection':'unresolved review with passed dimensions, selected lace absent, and original bounded fixed-product contents; no gold selection','row_key':sig[cid]['row_key']}
    if not selected:raise ValueError('no eligible review repair')
    shutil.copytree(BASE,DIR,ignore=shutil.ignore_patterns('final-reviews.json','reviewed-summary.json','links.jsonl'))
    (HERE/'review-requests').mkdir(exist_ok=False);(HERE/'review-answers').mkdir(exist_ok=False);entries=[]
    addition='\nbounded_contentsは固定AU商品の内容HTML行全体と直後のsectionラベル。内容一覧が幅別の全構成品と個数を列挙し、SKU行の幅・枚数と整合して完結すると原文から判断できる場合は、その一覧全体をレース同梱の有無の根拠として扱える。省略された断片や範囲不明の表、単なるタイトルの記載欠落から不存在を推測しない。全componentと現在行を直接確認し、unknownならunresolvedを維持する。総タイトルの枚数で現在幅の個数を上書きしない。肯定も否定も同じcaseの全条件を検証し、predictionと過去レビューを正解とみなさない。'
    for i,old in enumerate(old_entries,1):
        cid=old['case_id'];rp=HERE/f'review-requests/{i:02}.json';ap=HERE/f'review-answers/{i:02}.json';e={'case_id':cid,'case_ids':[cid],'request':str(rp.relative_to(HERE)),'answer':str(ap.relative_to(HERE)),'inference_required':cid in selected}
        if cid in selected:
            req=core.read(OLD/old['request']);req['instructions']+=addition;core.save(rp,req)
        else:
            rp.write_bytes((OLD/old['request']).read_bytes());ap.write_bytes((OLD/old['answer']).read_bytes());e['reused_from']={'round':'v11','request_sha256':sha(rp),'answer_sha256':sha(ap),'same_inputs_contexts_matching_prediction_signature_gate':True}
        e['request_sha256']=sha(rp);entries.append(e)
    core.save(HERE/'selection.json',{'selected':selected,'gold_used_for_selection':False,'prior_metrics_observed':True})
    core.save(HERE/'review-dispatch-manifest.json',{'case_coverage':len(entries),'entries':entries});fresh=[e for e in entries if e['inference_required']]
    core.save(HERE/'new-review-dispatch-manifest.json',{'planned_calls':len(fresh),'entries':fresh})
    paths=[HERE/'run.py',HERE/'selection.json',HERE/'review-dispatch-manifest.json',HERE/'new-review-dispatch-manifest.json',OLD/'grading-freeze.json',OLD/'run_v2.py',OLD/'guard-composition-freeze.json']+[p for p in DIR.rglob('*') if p.is_file() and '__pycache__' not in p.parts]+[HERE/e['request'] for e in entries]+[HERE/e['answer'] for e in entries if not e['inference_required']]
    core.save(HERE/'frozen-protocol.json',{'createdUTC':datetime.now(timezone.utc).isoformat(),'fresh_review_tasks':len(fresh),'reused_reviews':len(entries)-len(fresh),'matching_and_signatures_unchanged':True,'sha256':{str(p.relative_to(EXP)):sha(p) for p in paths}})
    print(json.dumps({'new_reviews':len(fresh),'reused_reviews':len(entries)-len(fresh)}))
def collect():
    verify();install_guard()
    spec=importlib.util.spec_from_file_location('v9_validator',EXP/'results/20261010T201000Z-luna-broad50-v9/run.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    mod.HERE=HERE;mod.DIR=DIR;mod.verify_protocol=verify;mod.install_guard=install_guard
    core.save(HERE/'review-freeze.json',{'run_sha256':sha(HERE/'run.py'),'note':'the complete frozen-protocol was verified before unchanged review validation'})
    mod.reviews_collect();(HERE/'links-v12.jsonl').write_bytes((DIR/'links.jsonl').read_bytes())
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','collect'));{'prepare':prepare,'collect':collect}[ap.parse_args().command]()
