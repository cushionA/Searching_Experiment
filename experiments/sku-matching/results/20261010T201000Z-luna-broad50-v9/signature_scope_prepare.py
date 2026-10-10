"""Annotate exact current-row signature evidence before any V9 signature calls."""
import importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
DIR=HERE/'inference'
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core

def sha(p): return core.sha(Path(p).read_bytes())
def main():
    spec=importlib.util.spec_from_file_location('v9_frozen_run',HERE/'run.py')
    run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)
    run.verify_protocol()
    if (HERE/'signature-freeze.json').exists():raise RuntimeError('signature stage already frozen')
    ev=core.evaluate(DIR);entries=core.prepare_signature(DIR,'signature-v3')
    for e in entries:
        rp=DIR/e['request'];req=core.read(rp);text=req['body']['contents'][0]['parts'][0]['text']
        prefix,raw=text.split('INPUT=',1);payload=json.loads(raw)
        for condition in payload['current_au_conditions']:
            condition.update({'scope':'fixed_product','kind':'sku_condition','current_row':True})
            payload['au_sources'][condition['source_id']]={
                'scope':'fixed_product','kind':'sku_condition','current_row':True,
                'axis':condition['axis'],'text':condition['value'],'contexts':[]}
        rule='現在AU行のA根拠は固定商品のその行に限定されたsku_conditionである。ラベル付きの現在行寸法が対象部位を直接示す場合は、その値を根拠に照合する。説明文の別サイズや他行から補完しない。固定AUタイトルがカーテンの場合だけ、同じ部位の幅/横幅は本体横幅、丈/高さは本体高さと対応できる。袖丈/股下/収納/別部位は混ぜず、明示矛盾を並べ替えで覆さない。A根拠で不足する対象は従来のfixed_product原文と部位・軸を照合し、確認不能ならunknown。'
        req['body']['contents'][0]['parts'][0]['text']=prefix+rule+'\nINPUT='+json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        arr=req['body']['generationConfig']['responseSchema']['properties']['checks']
        arr.update({'minItems':len(e['targets']),'maxItems':len(e['targets'])})
        core.save(rp,req);e['request_sha256']=sha(rp);e['task_version']='v9-current-row-scope-explicit-signature'
    core.save(DIR/'signature-v3-manifest.json',{'schema':'selected-dimensions-verification-v3','entries':entries})
    calls=[e for e in entries if e.get('inference_required',True)]
    core.save(HERE/'signature-dispatch-manifest.json',{'planned_calls':len(calls),'labels_read':False,'entries':[{**e,'request':'inference/'+e['request'],'answer':'inference/'+e['answer']} for e in calls]})
    paths=[HERE/'signature_scope_prepare.py',HERE/'frozen-protocol.json',DIR/'evaluation.json',DIR/'signature-v3-manifest.json',HERE/'signature-dispatch-manifest.json']+[DIR/e['request'] for e in entries]
    core.save(HERE/'signature-freeze.json',{'createdUTC':datetime.now(timezone.utc).isoformat(),'labels_read':False,'prior_same_task_gold_observed':False,'sha256':{str(p.relative_to(EXP)):sha(p) for p in paths}})
    print(json.dumps({'counts':ev['counts'],'dimension_tasks':len(calls),'local_NA':len(entries)-len(calls)}))
if __name__=='__main__':main()
