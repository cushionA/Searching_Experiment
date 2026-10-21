"""Audit native evidence and prepare an evening-only report; never execute candidates."""
import copy, datetime as dt, hashlib, json, re, sys
from pathlib import Path

R = Path(__file__).parent
ROOT = R.parents[3]
S = ROOT / '.agents/skills/research-repro-radar'
P = Path('/workspace/scratch/radar-20261021')
sys.path.insert(0, str(S / 'scripts'))
import native_judge

def read(p):
    return json.loads(p.read_text())

def write(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def key(row):
    return str(row.get('doi') or row.get('arxiv_id') or row.get('id') or row['url']).casefold()

def audit(name, task):
    rp, ap = P / (name + '.request.json'), P / (name + '.response.json')
    req, res = read(rp), read(ap)
    result = native_judge.validate_response(req, res)
    assert result['verdict'] is not None, result
    elapsed = ap.stat().st_mtime - rp.stat().st_mtime
    assert 0 <= elapsed <= 180, elapsed
    quotes = []
    for verdict, payload in zip(result['verdict']['candidates'], json.loads(req['payload'])):
        assert verdict['candidate_id'] == payload['candidate']['id']
        for e in verdict['evidence']:
            exact = any(e['source_url'] == source['url'] and re.sub(r'\s+', ' ', e['quote']) in re.sub(r'\s+', ' ', source['text']) for source in payload['sources'])
            quotes.append({'candidate': verdict['candidate_id'], 'url': e['source_url'], 'exact_match': exact, 'words': len(e['quote'].split())})
    ledger = {'task': task, 'backend': 'native', 'requested_model': 'gpt-6-luna', 'runtime_model': 'unknown', 'request_id': req['request_id'], 'task_sha256': req['task_sha256'], 'request_sha256': hashlib.sha256(rp.read_bytes()).hexdigest(), 'response_sha256': hashlib.sha256(ap.read_bytes()).hexdigest(), 'started_at': dt.datetime.fromtimestamp(rp.stat().st_mtime, dt.timezone.utc).isoformat(), 'finished_at': dt.datetime.fromtimestamp(ap.stat().st_mtime, dt.timezone.utc).isoformat(), 'elapsed_seconds': elapsed, 'schema_validation': result['diagnostics'], 'repairs': 0, 'quotes': quotes}
    return result['verdict']['candidates'], ledger

ledger = read(R / 'native-host-ledger.json')[:1]
initial = []
for i in range(6):
    rows, entry = audit(f'batch-{i}', f'/root/review{i}')
    initial.extend(rows)
    ledger.append(entry)
write(R / 'reviews.json', initial)
updates = []
for name, task in [('harness', '/root/harness_update'), ('models', '/root/models_update')]:
    rows, entry = audit(name, task)
    assert all(q['exact_match'] and q['words'] <= 25 for q in entry['quotes']), entry['quotes']
    updates.extend(rows)
    ledger.append(entry)
write(R / 'updated-reviews.json', updates)
write(R / 'native-host-ledger.json', ledger)
by = {x['candidate_id']: x for x in initial + updates}
targets = {x['id']: x for x in read(R / 'review-targets.json')}
h = by['2610.14224']
assert h['decision'] == 'test' and h['recommended_category'] == 'deep_dive_value'
recommended = [{'id': '2610.14224', 'title': 'Harness²: Recursive Agent Harnessing for an Open World', 'url': 'https://arxiv.org/abs/2610.14224', 'published_at': '2026-10-09', 'category': 'deep_dive_value', 'assessment': 'A', 'verification_level': 'reading', 'reason': h['reason'], 'next_check': h['next_check'], 'unconfirmed': h['unconfirmed'], 'independent_signals': [{'url': 'https://huggingface.co/papers/2610.14224', 'kind': 'HF trending', 'rank': 1}, {'url': 'https://github.com/google-research/harness2/issues/121', 'kind': 'third-party implementation investigation; not independent performance reproduction'}]}]
digest_ids = ['2610.26754', 'hf:qwen/qwen2.5-coder-1.5b-instruct', '2610.27327', '2610.24648', '2610.26825', '2610.22200', '2610.23839', '2610.24649', '2601.22709', '2607.25180']
digest = []
for ident in digest_ids:
    x = by[ident]
    digest.append({'id': ident, 'title': targets[ident]['title'], 'url': targets[ident]['url'], 'assessment': {'test': 'A', 'defer': 'B', 'reject': 'C'}[x['decision']], 'why_picked': x['reason'], 'deferred_reason': ' / '.join(x['unconfirmed']), 'verification_level': 'reading'})

# Carry every unfinished record, including execution-ineligible and ranking-excluded records.
# Detailed reading is tracked in seen, but unexecuted research remains in queue per user instruction.
previous = read(ROOT / 'experiments/research-repro-radar/evening-state/queue.json')
current = read(S / 'state/queue.json')
rows = read(R / 'all-candidates.json')
seen = read(S / 'state/seen.json')['reported']
pending = {key(x): x for x in previous['candidates']}
for x in current['candidates'] + rows:
    ident = key(x)
    if ident in seen and ident not in pending:
        continue
    if ident not in pending:
        y = dict(x)
        y.setdefault('carried_over_since', '2026-10-21')
        y.setdefault('carry_count', 1)
        y['pending_reason'] = 'No candidate execution or evidence-confirmed rejection completed.'
        pending[ident] = y
completed = ['2610.24649']
assert by[completed[0]]['decision'] == 'reject'
for ident in completed:
    pending.pop(ident, None)
current['candidates'] = list(pending.values())
write(S / 'state/queue.json', current)
write(R / 'unfinished-before-record.json', current)
ranking = read(R / 'ranking.json')
report = {'run_id': '2026-10-21-evening-native', 'date': '2026-10-21', 'status': 'reading_completed_no_candidate_execution', 'recommended': recommended, 'digest': digest, 'completed_ids': completed, 'counts': {'collected_unique': len(rows), 'ranking_input_with_carry': ranking['counts']['input'], 'primary_read_review_pool': 20, 'primary_read_total': len(targets), 'candidate_code_executions': 0, 'candidate_model_inferences': 0, 'native_initial_candidates_schema_valid': len(initial), 'native_updated_candidates_schema_valid': len(updates), 'pending_after_record_expected': len(pending)}, 'budget': {'money_jpy': 0, 'candidate_cpu_seconds': 0, 'candidate_weights_bytes': 0, 'gpu_seconds': 0, 'candidate_repairs': 0, 'native_repairs': 0, 'requested_start': '2026-10-21T09:01:00Z', 'deadline': '2026-10-21T09:31:00Z'}, 'runtime_model': 'unknown', 'limitations': ['OpenReview discovery: five HTTP 403 responses; arXiv search API: five HTTP 429 responses. Primary paper pages and HTML were readable.', 'API responses/query ceilings are not exhaustive coverage of public research. All returned candidates were retained and scored.', 'Five initial quote/URL mismatches are not accepted as evidence. Updated source-backed judgments supersede affected MemT/GRACE/Bekko/Qwen/TokenGate dispositions; raw initial judgments are retained for audit.', 'No credential-isolated candidate runtime verified; existing isolation pause is preserved without duplicate approval requests. No GPU fallback, unknown candidate code, training, credential changes, CLI LLM, email, cron, merge or deploy.', 'Detailed reading is recorded in seen/freshness; Harness² remains in unfinished queue until empirical work or source-supported rejection completes.']}
write(R / 'report.json', report)
print(json.dumps(report['counts']))
