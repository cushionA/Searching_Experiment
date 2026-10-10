"""Bind the recovered chunk ledger and inherited guard dependencies before grading."""
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--verify-only',action='store_true');args=ap.parse_args()
    freezes=[HERE/'frozen-protocol.json',HERE/'matching-collector-freeze.json',HERE/'signature-freeze.json',HERE/'review-freeze.json',EXP/'results/20261010T205900Z-luna-closed-review-v12/grading-freeze.json']
    checks={};bound={}
    for p in freezes:
        data=json.loads(p.read_text())
        for rel,h in data['sha256'].items():
            if hashlib.sha256((EXP/rel).read_bytes()).hexdigest()!=h:raise ValueError('lineage changed: '+rel)
            if rel in bound and bound[rel]!=h:raise ValueError('inconsistent lineage hash: '+rel)
            bound[rel]=h
        checks[str(p.relative_to(EXP))]=len(data['sha256'])
    required=['results/20261010T204500Z-luna-adjustable-dimensions-v11/run_v2.py','results/20261010T204500Z-luna-adjustable-dimensions-v11/run.py','results/20261010T204500Z-luna-adjustable-dimensions-v11/selection.json','sku_luna_curtain_dimension_alias.py','sku_luna_adjustable_dimension_task.py','sku_luna_dimension_guard.py','trial_luna_sku_matching.py']
    for rel in required:
        if rel not in bound:raise ValueError('unbound inherited guard dependency: '+rel)
    record={'verifiedUTC':datetime.now(timezone.utc).isoformat(),'stage':'post-review, before grading','freeze_checks':checks,'unique_bound_files':len(bound),'guard_dependencies_sha256':{r:bound[r] for r in required},'collector_ledger_and_failed_chunk_bound':True,'all_hashes_match':True,'note':'Initial V13 stage gates did not recursively verify the inherited guard code and collector freeze. This additive gate verifies all prior parent hashes plus collector, signatures and reviews before grade; no answer/source/code hash changes were found. This is not claimed to have run before the earlier inference.'}
    if not args.verify_only:
        p=HERE/'lineage-verification.json'
        if p.exists():raise ValueError('refusing overwrite verification record')
        p.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'all_hashes_match':True,'unique_bound_files':len(bound),'freeze_checks':checks}))
if __name__=='__main__':main()
