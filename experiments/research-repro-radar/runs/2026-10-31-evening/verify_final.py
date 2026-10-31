"""Offline integrity and carry-continuity checks; not a candidate quality benchmark."""
from pathlib import Path
import hashlib
import json
import subprocess

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[3]
STATE = ROOT / 'experiments/research-repro-radar/evening-state'

def read(path):
    return json.loads(path.read_text())

def key(row):
    return str(row.get('doi') or row.get('arxiv_id') or row.get('id') or row['url']).casefold()

def main():
    original = read(RUN/'queue-before.json')['candidates']
    current = read(STATE/'queue.json')['candidates']
    old = {key(x): x for x in original}
    new = {key(x): x for x in current}
    assert len(old) == len(original) == 9514
    assert len(new) == len(current) == 9549
    assert old.keys() <= new.keys()
    assert all(x.get('carried_over_since') == new[k].get('carried_over_since') for k, x in old.items())
    report = read(RUN/'report.json')
    assert report['completed_ids'] == report['recommended'] == []
    assert len(read(RUN/'all-candidates.json')) == 1687
    assert read(RUN/'ranking.json')['counts']['invalid'] == 0
    assert {x['id'] for x in read(RUN/'ranking.json')['review_pool']} == {x['id'] for x in read(RUN/'review-targets.json')}
    for row in read(RUN/'start-manifest.json')['files']:
        data = (ROOT/'.agents/skills/research-repro-radar'/row['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
    checked = 0
    ledgers = [read(RUN/'discovery-ledger.json')['requests'], read(RUN/'supplement-ledger.json'), read(RUN/'review-evidence.json'), read(RUN/'audit-source-ledger.json'), read(RUN/'metadata-ledger.json')]
    for rows in ledgers:
        for item in rows:
            if 'sha256' not in item:
                continue
            path = RUN/item['file'] if 'file' in item else RUN/'sources'/(item['label']+'.json')
            data = path.read_bytes()
            assert hashlib.sha256(data).hexdigest() == item['sha256'], str(path)
            assert len(data) == item['bytes']
            checked += 1
    assert not read(RUN/'audit-quote-check-repaired.json')['bad']
    assert len(read(RUN/'quote-check.json')['bad']) == 5  # retained explicitly, not treated as accepted citations
    for name in ['seen.json', 'mail.json']:
        remote = subprocess.check_output(['git','show',f'ced6335934ed9307058efef2a734b38ad0c8b4ab:experiments/research-repro-radar/evening-state/{name}'], cwd=ROOT)
        assert remote == (STATE/name).read_bytes(), name
    result = {'ok': True, 'old_pending':len(old), 'new_pending':len(new), 'old_missing':0, 'carry_date_changes':0, 'completed':0, 'source_hashes_checked':checked, 'invalid_final_candidates':0, 'unresolved_pool_citations':5, 'model_inferences':0}
    (RUN/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
