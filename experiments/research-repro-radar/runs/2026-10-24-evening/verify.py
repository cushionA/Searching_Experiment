"""Verify the run's evidence and persistence, not candidate reproduction."""
import hashlib
import json
from pathlib import Path
R=Path(__file__).parent
ROOT=R.parents[3]
S=Path('/workspace/radar-skills/research-repro-radar')
def read(p): return json.loads(p.read_text())
def key(x): return str(x.get('doi') or x.get('arxiv_id') or x.get('id') or x['url']).casefold()
report=read(R/'report.json')
before=read(R/'queue-before.json')['candidates']
after=read(S/'state/queue.json')['candidates']
a={key(x) for x in before};b={key(x) for x in after}
assert a<=b and len(b)==len(after)==report['counts']['pending_after_record_expected']
assert len(a)==6606 and report['completed_ids']==[] and report['recommended']==[]
assert len(report['digest'])==8
assert report['counts']['collected_unique']==len(read(R/'all-candidates.json'))==5747
reviews={x['candidate_id'] for x in read(R/'reviews.json')}
assert len(reviews)==31
assert {x['id'] for x in read(R/'ranking.json')['review_pool']}<=reviews
manifest=read(R/'start-manifest.json')
for name in ['seen.json','mail.json']:
    assert hashlib.sha256((S/'state'/name).read_bytes()).hexdigest()==manifest['state_sha256_before'][name]
for name in ['seen.json','mail.json','queue.json','freshness.json']:
    assert (S/'state'/name).read_bytes()==(ROOT/'experiments/research-repro-radar/evening-state'/name).read_bytes()
assert json.dumps(read(S/'state/freshness.json')).count(report['run_id'])==1
for p,sha in manifest['skill_sha256'].items():
    assert hashlib.sha256((S/p).read_bytes()).hexdigest()==sha
ledger=read(R/'native-host-ledger.json')
assert len(ledger)==8 and all(x['schema_valid'] and x['upper_bound_elapsed_seconds']<=180 for x in ledger)
for name in ['followup-validation.json','final-pool-validation.json']:
    assert all(x['exact'] and x['words']<=25 for x in read(R/name))
evidence_count=0
for name in ['review-evidence.json','followup-evidence.json','model-details-evidence.json','license-evidence.json','intent-evidence.json']:
    for e in read(R/name):
        if 'file' in e:
            assert hashlib.sha256((R/e['file']).read_bytes()).hexdigest()==e['sha256'],e['file']
            evidence_count+=1
pages=read(R/'arxiv-pagination-ledger.json')
assert len(pages)==35 and all(x.get('status')==200 for x in pages)
checks={'pending_before':len(a),'pending_after':len(b),'pending_lost':len(a-b),'completed':0,
 'collected_unique':5747,'primary_read':31,'final_review_pool':20,'candidate_execution':0,
 'digest':8,'detail':0,'state_sync':'four files byte-identical','seen_mail':'unchanged',
 'freshness':'exactly one run','native_batches':8,'native_candidate_judgments':35,
 'native_probe':'validated separately; host gpt-6-luna, serving ID unknown',
 'native_validator_tests':{'passed':38,'elapsed_seconds':0.433},
 'primary_evidence_hashes_checked':evidence_count,'arxiv_additional_pages':35,
 'arxiv_total_pages':40,'arxiv_total_records':3792,'diff_check':'Authored files clean; raw source whitespace retained for hash fidelity'}
(R/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(checks))
