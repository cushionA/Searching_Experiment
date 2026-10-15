"""Read-only evidence and continuity audit for the October 15 evening run."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'experiments/research-repro-radar/runs/2026-10-15-evening'
STATE = ROOT / 'experiments/research-repro-radar/evening-state'
BASE = 'f197d9d181e59998cfb61040ed763d308e512fa5'


def load(path):
    return json.loads(path.read_text())


def key(row):
    return str(row.get('doi') or row.get('arxiv_id') or row.get('id') or row['url']).casefold()


def main():
    report = load(RUN / 'report.json')
    previous = json.loads(subprocess.check_output([
        'git', '-C', str(ROOT), 'show',
        BASE + ':experiments/research-repro-radar/evening-state/queue.json']))
    current = load(STATE / 'queue.json')['candidates']
    old = {key(x) for x in previous['candidates']}
    now = {key(x) for x in current}
    assert len(old) == 1072
    assert old <= now, 'Unfinished canonical candidates lost'
    assert len(now) == len(current) == report['counts']['pending_after_record']
    assert report['completed_ids'] == [], 'This run completed no candidate inference'
    rank = load(RUN / 'ranking-final.json')
    reviews = load(RUN / 'reviews.json')
    assert len(reviews) == len(rank['review_pool']) == 20
    assert [x['judgment']['candidate_id'] for x in reviews] == [x['id'] for x in rank['review_pool']]
    candidates = load(RUN / 'all-candidates.json')
    assert len(candidates) == len({key(x) for x in candidates}) == report['counts']['collected_unique']
    for name in ['seen.json', 'queue.json', 'mail.json', 'freshness.json']:
        assert (STATE / name).read_bytes() == (ROOT / '.agents/skills/research-repro-radar/state' / name).read_bytes()
    ledger = load(RUN / 'native-host-ledger.json')
    assert ledger['backend'] == 'native'
    assert ledger['requested_model'] == 'gpt-6-luna'
    assert ledger['actual_service_model'] == 'unknown'
    assert all(x['validation_exit'] == 0 for x in ledger['requests'])
    import sys
    sys.path.insert(0, str(ROOT / '.agents/skills/research-repro-radar/scripts'))
    import jsonschema
    schema = load(ROOT / '.agents/skills/research-repro-radar/references/native-candidate.schema.json')
    for row in reviews:
        jsonschema.validate(row['judgment'], schema)
        assert row['judgment']['verification_level'] in ['reading', 'unreviewed']
    benchmark = load(ROOT / 'benchmarks/research-repro-radar/2026-10-15-squeez-modernbert-continuation.json')
    assert hashlib.sha256((ROOT / benchmark['synthetic_input']['path']).read_bytes()).hexdigest() == benchmark['synthetic_input']['sha256']
    baselines = json.loads(subprocess.check_output([sys.executable, str(RUN / 'squeez-baselines.py')], cwd=ROOT))
    assert all(baselines[k] == benchmark['baselines'][k] for k in ['clean', 'noisy'])
    audit = load(RUN / 'parent-poc-audit.json')
    assert audit['actual_repairs'] == 2 and audit['allowed_repairs'] == 1
    assert audit['further_execution_stopped']
    manifest = load(RUN / 'evidence-manifest.json')
    for entry in manifest:
        assert hashlib.sha256((ROOT / entry['file']).read_bytes()).hexdigest() == entry['sha256'], entry['file']
    print(json.dumps({'ok': True, 'collected': len(candidates), 'reviewed': len(reviews),
                      'candidate_inferences': 0, 'pending': len(now),
                      'all_original_pending_retained': True, 'manifest_files': len(manifest),
                      'repair_cap_exceeded_and_reported': True}))


if __name__ == '__main__':
    main()
