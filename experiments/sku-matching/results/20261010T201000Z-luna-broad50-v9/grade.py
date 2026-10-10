"""Score a new frozen fifty-case task and cumulative 250 pairs outside checkout."""
import argparse, json, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
REPO=EXP.parents[1]
OLD=EXP/"results/20261010T203800Z-luna-current-row-dimensions-v10"
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core
import evaluate_frozen_sku_accuracy as scorer


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evaluation-dir',type=Path,required=True);out=ap.parse_args().evaluation_dir.resolve()
    assert out!=REPO and REPO not in out.parents and not out.exists()
    freeze=core.read(HERE/'grading-freeze.json')
    def verify():
        for rel,expected in freeze['sha256'].items():assert core.sha((EXP/rel).read_bytes())==expected,rel
    verify()
    subprocess.run([sys.executable,'-B',str(OLD/'grade.py'),'--evaluation-dir',str(out)],check=True,stdout=subprocess.DEVNULL)
    labels=[json.loads(line) for file in (out/'labels').glob('*.jsonl') for line in file.read_text().splitlines() if line.strip()]
    assert len(labels)==1439
    paths=[EXP/'results'/p for p in ('20261010T160225Z-luna-task-improvement/r02-reference-development','20261010T160225Z-luna-task-improvement/r03-reference-holdout','20261010T174640Z-luna-expanded50','20261010T183000Z-luna-improvement-v3/improved','20261010T183754Z-luna-output-shape-v4/improved','20261010T203800Z-luna-current-row-dimensions-v10/derived')]
    prior_inputs=[c for p in paths for c in core.read(p/'inputs.json')];prior=[c for p in paths for c in core.read(p/'reviewed-summary.json')['cases']]
    cases=core.read(HERE/'inference/inputs.json');final=core.read(HERE/'inference/reviewed-summary.json')['cases']
    all_inputs=prior_inputs+cases
    assert len(prior)==200 and len(final)==50 and len({c['case_id'] for c in all_inputs})==250
    assert len({(c['rakuten_sku_key'],c['au_product_id']) for c in all_inputs})==250
    keys=lambda cs:{c['case_id']:{r['row_key'] for r in c['au_rows']} for c in cs}
    scored={'broad50_v9':scorer.score(final,labels,keys(cases)),'cumulative_250_v9':scorer.score(prior+final,labels,keys(all_inputs))}
    for tag,score in scored.items():
        d=out/tag;d.mkdir();core.save(d/'summary.json',{'metrics':score['metrics'],'case_count':len(score['cases']),'human_verified':False})
        (d/'case-results.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in score['cases']),encoding='utf-8')
    comparison=core.read(out/'comparisons.json');comparison['metrics'].update({tag:s['metrics'] for tag,s in scored.items()})
    comparison.update({'new_distinct_case_count':50,'remaining_unprocessed_label_count':1189,'note':'fresh50 case/SKU-AU pairs, source-only fixed selection, prior200 task versions reused only in cumulative score; group novelty reported separately; human-unverified Luna gold'})
    core.save(out/'comparisons.json',comparison);verify()
    core.save(out/'v9-source-manifest.json',{'source_hashes_unchanged':True,'sha256':freeze['sha256'],'human_verified':False})
    if (HERE/'grading/comparisons.json').exists():assert core.read(HERE/'grading/comparisons.json')==comparison
    print(json.dumps({tag:s['metrics']['known_case_accuracy'] for tag,s in scored.items()}))
if __name__=='__main__':main()
