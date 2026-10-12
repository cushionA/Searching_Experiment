"""Verify real evening checkpoint in disposable copies; never acknowledge real work."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
SKILL=ROOT/'.agents/skills/research-repro-radar/scripts'
STATE=ROOT/'experiments/research-repro-radar/evening-state'
RUN=ROOT/'experiments/research-repro-radar/runs/2026-10-12-evening'

def snapshot(path):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.glob('*.json'))}

def main():
    before=snapshot(STATE)
    assert set(before)=={'seen.json','queue.json','mail.json','freshness.json'}
    original_queue=json.loads((STATE/'queue.json').read_text())['candidates']
    original_ids={x.get('arxiv_id',x.get('id')) for x in original_queue}
    results={'state_sha256':before,'pending_before':len(original_ids),'checks':{}}
    def call(name,input_file,state):
        output=subprocess.check_output([sys.executable,str(SKILL/name),str(input_file),'--state-dir',str(state),'--today','2026-10-12'],text=True)
        return json.loads(output)
    with tempfile.TemporaryDirectory(prefix='radar-restore-') as temp:
        temp=Path(temp);restored=temp/'state';shutil.copytree(STATE,restored)
        assert snapshot(restored)==before
        results['checks']['byte_identical_restore']=True
        empty=temp/'empty.json';empty.write_text('[]')
        ranked=call('rank_candidates.py',empty,restored)
        assert {x['id'] for x in ranked['ranked_candidates']}==original_ids
        results['checks']['pending_reintroduced_without_new_input']=True
        results['restored_counts']=ranked['counts']
        call('record_run.py',RUN/'report.json',restored)
        assert len(json.loads((restored/'freshness.json').read_text())['history'])==1
        assert {x['arxiv_id'] for x in json.loads((restored/'queue.json').read_text())['candidates']}==original_ids
        results['checks']['same_run_freshness_idempotent_and_pending_kept']=True
        twice=temp/'twice.json';c=json.loads((RUN/'candidates.json').read_text());twice.write_text(json.dumps(c+c))
        ranked=call('rank_candidates.py',twice,restored)
        duplicates=sum(x['reason']=='duplicate' for x in ranked['rejected'])
        assert duplicates==len(c)
        results['checks']['rediscovery_duplicates_suppressed']=True
        results['duplicate_input_records_suppressed']=duplicates
        # Synthetic fixture is separate from both real state and candidate counts.
        fixture=temp/'fixture';input_file=temp/'fixture.json'
        input_file.write_text(json.dumps([{'id':'fixture-only','title':'Fixture small model','source':'arxiv','url':'https://example.invalid/fixture','estimated_cpu_minutes':1}]))
        call('rank_candidates.py',input_file,fixture)
        report=temp/'fixture-report.json';report.write_text(json.dumps({'run_id':'fixture-only','recommended':[{'id':'fixture-only','title':'Fixture','published_at':'2026-10-12'}]}))
        call('record_run.py',report,fixture)
        reranked=call('rank_candidates.py',input_file,fixture)
        assert reranked['counts']['already_reported']==1 and reranked['counts']['queued']==0
        results['checks']['seen_suppression_and_completion_fixture_only']=True
    assert snapshot(STATE)==before
    results['checks']['canonical_state_unchanged']=True
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
