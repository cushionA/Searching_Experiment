"""Source-selected repair of failed current-row dimension gates; no gold selection."""
from __future__ import annotations
import argparse, copy, importlib.util, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / 'results/20261010T195200Z-luna-closed-contents-v8'
BASE = OLD / 'repaired'
DIR = HERE / 'derived'
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_current_row_dimension_task as dimension

def utc(): return datetime.now(timezone.utc).isoformat()
def sha(p): return core.sha(Path(p).read_bytes())
def freeze(path, data):
    if path.exists(): raise RuntimeError(f'freeze exists: {path}')
    core.save(path, data)
def verify(path):
    for rel, expected in core.read(path)['sha256'].items():
        if sha(EXP / rel) != expected: raise ValueError(f'frozen artifact changed: {rel}')
def install_guard():
    spec = importlib.util.spec_from_file_location('v8_review_reuse', OLD / 'review_reuse.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.install_guard(DIR)

def prepare():
    prior = core.read(BASE / 'signature-v3-evaluation.json')
    manifest = core.read(BASE / 'signature-v3-manifest.json')
    selected = [e for e in manifest['entries'] if not prior[e['case_id']]['passed']]
    if not selected: raise ValueError('no failed dimension gates')
    # Failed gates, not labels/review verdicts, determine this development set.
    shutil.copytree(BASE, DIR, ignore=shutil.ignore_patterns('signature-v3-evaluation.json', 'final-reviews.json', 'reviewed-summary.json', 'links.jsonl'))
    cases = {c['case_id']: c for c in core.read(DIR / 'inputs.json')}
    contexts = core.read(DIR / 'source-contexts.json')
    selected_ids = {e['case_id'] for e in selected}; calls = []; lineage = []
    for entry in manifest['entries']:
        entry.pop('reused_from', None)
        rp, ap = DIR / entry['request'], DIR / entry['answer']
        old = {'request_sha256': sha(BASE / entry['request']), 'answer_sha256': sha(BASE / entry['answer']), 'candidate_answer_sha256': entry['candidate_answer_sha256']}
        if entry['case_id'] in selected_ids:
            case = cases[entry['case_id']]
            row = next(r for r in case['au_rows'] if r['row_key'] == entry['row_key'])
            targets = [{k: a.get(k) for k in ('axis', 'value', 'unit')} for a in entry['targets']]
            core.save(rp, dimension.request(case, row, targets, contexts[entry['case_id']]))
            # Copies of old responses were made by copytree; leave originals in V8,
            # and remove only the V10 copy before new inference writes it.
            ap.unlink()
            entry['request_sha256'] = sha(rp)
            entry['task_version'] = 'explicit-current-row-dimension-scope-v10'
            calls.append({**entry, 'request': 'derived/' + entry['request'], 'answer': 'derived/' + entry['answer']})
            lineage.append({'case_id': entry['case_id'], 'prior': old, 'fresh_dimension_task': True})
        else:
            entry['reused_from'] = {'round': 'v8', **old, 'same_inputs_contexts_row_targets_matching_answer': True}
            lineage.append({'case_id': entry['case_id'], 'prior': old, 'fresh_dimension_task': False})
    core.save(DIR / 'signature-v3-manifest.json', manifest)
    core.save(HERE / 'signature-dispatch-manifest.json', {'planned_calls': len(calls), 'selection': 'failed candidate dimension gates only; no gold or verdict selection', 'entries': calls})
    core.save(HERE / 'lineage.json', {'new_distinct_cases': 0, 'paired_case_count': len(cases), 'fresh_dimension_tasks': len(calls), 'reused_passed_signatures': len(manifest['entries']) - len(calls), 'entries': lineage})
    paths = [HERE / 'run.py', HERE / 'signature-dispatch-manifest.json', HERE / 'lineage.json', OLD / 'grading-freeze.json', EXP / 'sku_luna_current_row_dimension_task.py', EXP.parents[1] / 'tests/test_lab_sku_luna_current_row_dimension_task.py']
    paths += [p for p in DIR.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    freeze(HERE / 'frozen-protocol.json', {'createdUTC': utc(), 'raw_labels_read_in_this_stage': False, 'prior_development_metrics_observed': True, 'selection_uses_gold': False, 'sha256': {os.path.relpath(p, EXP): sha(p) for p in paths}})
    # Responses reused in this stage are frozen; paths for fresh responses do not
    # exist yet and are intentionally outside the initial freeze.
    print(json.dumps({'dimension_tasks': len(calls), 'reused_signature_count': len(manifest['entries']) - len(calls)}))

def reviews_prepare():
    verify(HERE / 'frozen-protocol.json')
    install_guard(); sig = core.signature_results(DIR, 'signature-v3')
    changed = {e['case_id'] for e in core.read(HERE / 'signature-dispatch-manifest.json')['entries']}
    cases = {c['case_id']: c for c in core.read(DIR / 'inputs.json')}
    old_entries = core.read(OLD / 'repaired-review-dispatch-manifest.json')['entries']
    (HERE / 'review-requests').mkdir(exist_ok=False); (HERE / 'review-answers').mkdir(exist_ok=False)
    entries = []
    for i, old in enumerate(old_entries, 1):
        cid = old['case_id']; rp = HERE / f'review-requests/{i:02}.json'; ap = HERE / f'review-answers/{i:02}.json'
        e = {'case_id': cid, 'case_ids': [cid], 'request': str(rp.relative_to(HERE)), 'answer': str(ap.relative_to(HERE)), 'inference_required': cid in changed}
        if cid in changed:
            req = core.read(OLD / old['request']); item = req['cases'][0]
            s = sig[cid]; row = next(r for r in cases[cid]['au_rows'] if r['row_key'] == s['row_key'])
            def ref(sid):
                return row['conditions'][int(sid[1:])]['condition_id'] if sid.startswith('A') and sid[1:].isdigit() else sid
            item['dimension_verification'] = {k: s.get(k) for k in ('passed', 'applicable', 'validation_ok', 'guard_issues')}
            item['dimension_verification']['expanded_checks'] = [{'target': {k: c['target'].get(k) for k in ('axis', 'value', 'unit')}, 'status': c['status'], 'evidence': [{'source_id': ref(x['source_id']), 'quote': x['quote']} for x in c['evidence']]} for c in s['expanded_checks']]
            # Generic A references in matching traces are local to each current
            # row and restored without asking the model to generate identifiers.
            rows = {r['row_key']: r for r in cases[cid]['au_rows']}
            for tr in item.get('matching_evidence_trace', []):
                for side in ('rakuten_checks', 'au_checks'):
                    for c in tr[side]:
                        c['source_ids'] = [rows[tr['row_key']]['conditions'][int(x[1:])]['condition_id'] if x.startswith('A') and x[1:].isdigit() else x for x in c['source_ids']]
            req['instructions'] += '\n新しい寸法結果も検証対象の仮説。現AU行の原文幅/丈と選択済み寸法を直接確認し、同じcaseの全条件をレビューする。'
            core.save(rp, req)
        else:
            rp.write_bytes((OLD / old['request']).read_bytes()); ap.write_bytes((OLD / old['answer']).read_bytes())
            e['reused_from'] = {'round': 'v8', 'request_sha256': sha(rp), 'answer_sha256': sha(ap), 'same_inputs_contexts_prediction_matching_answer_signature_gate': True}
        e['request_sha256'] = sha(rp); entries.append(e)
    core.save(HERE / 'review-dispatch-manifest.json', {'case_coverage': len(entries), 'entries': entries})
    fresh = [e for e in entries if e['inference_required']]
    core.save(HERE / 'new-review-dispatch-manifest.json', {'planned_calls': len(fresh), 'entries': fresh})
    paths = [HERE / 'run.py', DIR / 'signature-v3-evaluation.json', HERE / 'review-dispatch-manifest.json']
    paths += [HERE / e['request'] for e in entries] + [HERE / e['answer'] for e in entries if not e['inference_required']]
    paths += [DIR / e['answer'] for e in core.read(DIR / 'signature-v3-manifest.json')['entries']]
    freeze(HERE / 'review-freeze.json', {'createdUTC': utc(), 'fresh_tasks': len(fresh), 'reused_reviews': len(entries) - len(fresh), 'sha256': {str(p.relative_to(EXP)): sha(p) for p in paths}})
    print(json.dumps({'signature_passed': sum(s['passed'] for s in sig.values()), 'signature_total': len(sig), 'fresh_reviews': len(fresh), 'reused_reviews': len(entries) - len(fresh)}))

def reviews_collect():
    verify(HERE / 'frozen-protocol.json'); verify(HERE / 'review-freeze.json'); install_guard()
    spec = importlib.util.spec_from_file_location('v8_collect', OLD / 'review_reuse.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    # Reuse the unchanged stringent review validator with V10-specific paths.
    mod.HERE = HERE; mod.DERIVED = DIR
    mod.install_guard = install_guard
    core.save(HERE / 'repaired-reviews-freeze.json', {'prepare_sha256': sha(OLD / 'review_reuse.py'), 'reused_answer_sha256': {}, 'note': 'V10 frozen-protocol and review-freeze are verified before invoking unchanged validator'})
    (HERE / 'review_reuse.py').write_bytes((OLD / 'review_reuse.py').read_bytes())
    (HERE / 'repaired-review-dispatch-manifest.json').write_bytes((HERE / 'review-dispatch-manifest.json').read_bytes())
    mod.reviews_collect()
    (HERE / 'links-v10.jsonl').write_bytes((DIR / 'links.jsonl').read_bytes())

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('command', choices=('prepare', 'reviews-prepare', 'reviews-collect'))
    {'prepare': prepare, 'reviews-prepare': reviews_prepare, 'reviews-collect': reviews_collect}[ap.parse_args().command]()
