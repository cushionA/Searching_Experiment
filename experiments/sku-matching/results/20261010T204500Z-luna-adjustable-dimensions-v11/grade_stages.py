"""Diagnostic stage scores; these hypothetical candidates are never exported links."""
import argparse,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
REPO=EXP.parents[1]
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core
import evaluate_frozen_sku_accuracy as scorer

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--labels-evaluation-dir',type=Path,required=True);ap.add_argument('--output-dir',type=Path,required=True);args=ap.parse_args()
    root=args.labels_evaluation_dir.resolve();out=args.output_dir.resolve()
    assert REPO not in root.parents and root!=REPO and REPO not in out.parents and not out.exists()
    freeze=core.read(HERE/'grading-freeze.json')
    def verify():
        for rel,h in freeze['sha256'].items():assert core.sha((EXP/rel).read_bytes())==h,rel
    verify()
    labels=[json.loads(line) for p in (root/'labels').glob('*.jsonl') for line in p.read_text().splitlines() if line.strip()]
    assert len(labels)==1439
    old=EXP/'results/20261010T201000Z-luna-broad50-v9/inference';cases=core.read(HERE/'derived/inputs.json');keys={c['case_id']:{r['row_key'] for r in c['au_rows']} for c in cases}
    ev=core.read(old/'evaluation.json')['cases'];sigs={'dimensions_v9':core.read(old/'signature-v3-evaluation.json'),'dimensions_v11':core.read(HERE/'derived/signature-v3-evaluation.json')}
    outputs={}
    for tag,sig in [('matching_v9',None),*sigs.items()]:
        rows=[]
        for p in ev:
            candidate=p['decision']=='candidate';ok=candidate and (sig is None or sig[p['case_id']]['passed'])
            decision='adopt' if ok else 'pending' if candidate else 'not_evaluated' if not p['validation_ok'] else p['decision']
            rows.append({'case_id':p['case_id'],'decision':decision,'row_key':p['candidate_row_key'] if ok else None})
        score=scorer.score(rows,labels,keys);outputs[tag]={'metrics':score['metrics'],'hypothetical_stage_only':True,'not_exported_as_links':True}
    out.mkdir();core.save(out/'stage-metrics.json',outputs);verify();print(json.dumps({k:v['metrics']['known_case_accuracy'] for k,v in outputs.items()}))
if __name__=='__main__':main()
