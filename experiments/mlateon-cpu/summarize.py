"""Recompute comparison from saved scores; does not run models or alter labels."""
import argparse
import json
from pathlib import Path
from benchmark import ranking_metrics, sha256

parser=argparse.ArgumentParser()
parser.add_argument('results',type=Path)
args=parser.parse_args()
arms={a:json.loads((args.results/f'{a}.json').read_text()) for a in ['bm25','fp32','int8','mean-ablation']}
fixture=json.loads(Path(__file__).with_name('fixture.json').read_text())
labels={q['id']:q['relevant'] for q in fixture['queries']}
assert len({v['fixture_sha256'] for v in arms.values()})==1
assert sha256(Path(__file__).with_name('fixture.json'))==arms['bm25']['fixture_sha256']
fp=arms['fp32']['predictions'];quant=arms['int8']['predictions']
assert [p['query_id'] for p in fp]==[p['query_id'] for p in quant]
deltas=[abs(x['scores_by_document'][d]-y['scores_by_document'][d]) for x,y in zip(fp,quant) for d in x['scores_by_document']]
summary={'metrics':{a:v['metrics'] for a,v in arms.items()},'fp32_int8':{'top1_agreement':sum(x['ranking'][0]==y['ranking'][0] for x,y in zip(fp,quant))/len(fp),'max_abs_score_difference':max(deltas),'mean_abs_score_difference':sum(deltas)/len(deltas),'changed_top1_queries':[x['query_id'] for x,y in zip(fp,quant) if x['ranking'][0]!=y['ranking'][0]],'median_speedup':arms['fp32']['latency_ms']['median']/arms['int8']['latency_ms']['median']}}
# Offline simulated cascade: candidate recall ceiling is separate from all-document search.
for arm in ['fp32','int8']:
    rows=[]
    for base,p in zip(arms['bm25']['predictions'],arms[arm]['predictions']):
        assert base['query_id']==p['query_id']
        candidates=base['ranking'][:10]
        ranked=sorted(candidates,key=lambda d:(-p['scores_by_document'][d],d))
        rows.append({'query_id':p['query_id'],'candidate_hit':bool(set(candidates)&set(labels[p['query_id']])),'ranking':ranked,**ranking_metrics(ranked,labels[p['query_id']])})
    summary['bm25_top10_rerank_'+arm]={'candidate_recall':sum(r['candidate_hit'] for r in rows)/len(rows),'metrics':{k:sum(r[k] for r in rows)/len(rows) for k in ['ndcg@10','mrr@10','hit@1']},'timing':'not measured; offline scores from exhaustive search','predictions':rows}
(args.results/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='metrics'},ensure_ascii=False,indent=2))
