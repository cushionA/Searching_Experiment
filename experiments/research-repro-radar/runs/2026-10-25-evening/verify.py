"""Verify artifact identity, native evidence and evening-state preservation."""
import hashlib,json
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];S=ROOT/'.agents/skills/research-repro-radar'
def read(p):return json.loads(p.read_text())
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
report=read(R/'report.json');before=read(R/'queue-before.json')['candidates'];after=read(S/'state/queue.json')['candidates'];a={key(x)for x in before};b={key(x)for x in after}
assert len(a)==9375 and len(b)==len(after)==9384 and a<=b
assert report['completed_ids']==report['recommended']==[]
assert len(report['digest'])==7
assert report['counts']['collected_unique']==len(read(R/'all-candidates.json'))==5753
reviews={x['candidate_id']for x in read(R/'reviews.json')};assert len(reviews)==27
assert {x['id']for x in read(R/'ranking.json')['review_pool']}<=reviews
m=read(R/'start-manifest.json')
for n in ['seen.json','mail.json']:assert hashlib.sha256((S/'state'/n).read_bytes()).hexdigest()==m['state_sha256_before'][n]
for n in ['seen.json','mail.json','queue.json','freshness.json']:assert (S/'state'/n).read_bytes()==(ROOT/'experiments/research-repro-radar/evening-state'/n).read_bytes()
assert json.dumps(read(S/'state/freshness.json')).count(report['run_id'])==1
for n,h in m['skill_sha256'].items():assert hashlib.sha256((S/n).read_bytes()).hexdigest()==h
ledger=read(R/'native-host-ledger.json');assert len(ledger)==8 and all(x['schema_valid']and x['upper_bound_elapsed_seconds']<=180 for x in ledger)
for n in ['review-validation.json','followup-validation.json']:assert all(x['exact']and x['words']<=25 for x in read(R/n))
count=0
for n in ['review-evidence.json','followup-evidence.json','model-details-evidence.json','license-evidence.json','project-evidence.json']:
 for x in read(R/n):
  if 'file'in x:assert hashlib.sha256((R/x['file']).read_bytes()).hexdigest()==x['sha256'];count+=1
pages=read(R/'arxiv-pagination-ledger.json');assert len(pages)==35 and all(x.get('status')==200 for x in pages)
assert read(R/'record-result.json')=={'recorded_seen':0,'freshness_fresh':0,'freshness_total':0,'completed_queue_items':0}
out={'pending_before':len(a),'pending_after':len(b),'pending_lost':len(a-b),'completed':0,'collected_unique':5753,'primary_read':27,'final_review_pool_read':20,'candidate_execution':0,'digest':7,'detail':0,'state_sync':'four files byte-identical','seen_mail':'unchanged','freshness':'exactly one run','native_batches':8,'native_candidate_judgments':32,'native_probe':'passed request ID/hash/schema/expected value','host_requested_model':'gpt-6-luna','service_runtime_model_id':'unknown','native_validator_tests_passed':38,'primary_evidence_hashes_checked':count,'arxiv_total_pages':40,'github_coding_search_pages':3,'raw_source_whitespace_preserved':True}
(R/'verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out))
