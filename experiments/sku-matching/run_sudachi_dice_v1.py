#!/usr/bin/env python3
"""Frozen Dice-ranking and Core/Full normalization diagnostic; no new SKU rules."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import time
import unicodedata

from library_train_dictionary_v1 import dictionary_request_state
from run_installed_library_selection_v1 import catalogs
from run_library_axis_domains_v1 import assemble_mode

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / '.lab-output/sku-dictionary-half-20261015-v1'
DEFAULT = ROOT / '.lab-output/sku-sudachi-dice-20261017-v1'


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def lines(p):
    with Path(p).open(encoding='utf-8') as f:
        return [json.loads(s) for s in f if s.strip()]


def write(p, value):
    with Path(p).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')


def write_lines(p, values):
    with Path(p).open('x', encoding='utf-8') as f:
        for v in values:
            f.write(json.dumps(v, ensure_ascii=False, sort_keys=True) + '\n')


def normalize(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s).lower())


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s} if s else set()


def dice(a, b):
    a, b = bigrams(a), bigrams(b)
    return 2 * len(a & b) / (len(a) + len(b)) if a or b else 0.0


def protected(s):
    return (any(c.isnumeric() for c in s + unicodedata.normalize('NFKC', s))
            or (s.isascii() and any(c.isalnum() for c in s)))


def ranks(query, rows, view):
    def text(axes):
        return '|'.join(view[a['value']] for a in axes)
    q = text(query)
    scored = [(dice(q, text(row['axes'])), i, row['row_key']) for i, row in enumerate(rows)]
    scored.sort(key=lambda v: (-v[0], v[1]))
    return [{'row_key': k, 'score': s, 'source_order': i} for s, i, k in scored]


def verify_old():
    freeze = read(OLD / 'eval-v1/EVAL_OUTPUT_FREEZE.json')
    for p, expected in freeze['input_pins'].items():
        if sha(p) != expected:
            raise ValueError('old source changed: ' + p)
    for name, expected in freeze['code_pins'].items():
        if sha(Path(__file__).with_name(name)) != expected:
            raise ValueError('old policy changed: ' + name)
    for name, item in freeze['output_files'].items():
        if sha(OLD / 'eval-v1' / name) != item['sha256']:
            raise ValueError('old prediction changed: ' + name)
    return freeze


def run(lab):
    plan = read(lab / 'PLAN.json')
    plan_pin, code_pin = sha(lab / 'PLAN.json'), sha(__file__)
    verify_old()
    if sha(OLD / 'eval-v1/EVAL_OUTPUT_FREEZE.json') != plan['old_eval_freeze_sha256']:
        raise ValueError('wrong fixed population')
    inputs = [lab / 'PLAN.json', OLD / 'eval-v1/EVAL_OUTPUT_FREEZE.json',
              OLD / 'dictionaries-v1/dict2.json']
    for name, expected in plan['source_native_sha256'].items():
        p = OLD / 'split-v1/EVALtest/inputs' / name
        if sha(p) != expected:
            raise ValueError('wrong native source')
        inputs.append(p)
    requests, documents, row_plans, case_plans = [
        lines(OLD / 'eval-v1' / name) for name in
        ('eval-requests.jsonl', 'eval-documents.jsonl', 'eval-row-plans.jsonl', 'eval-case-plans.jsonl')]
    cases = lines(OLD / 'split-v1/EVALtest/inputs/cases.jsonl')
    products = {p['dossier_id']: p for p in lines(OLD / 'split-v1/EVALtest/inputs/products.jsonl')}
    old_states = {r['request_id']: r['state'] for r in
                  lines(OLD / 'eval-v1/exact_current_dictionary2-requests.jsonl')}
    raw = {}
    for source in read(OLD / 'CORPUS_MANIFEST.json')['sources']:
        p = ROOT / source['views']
        for k, v in read(p)['views']['raw'].items():
            if k in raw and raw[k] != v:
                raise ValueError('price-free view disagreement')
            raw[k] = v
        inputs.append(p)
    values = {a['value'] for c in cases for a in c['rakuten_selected']['axes']}
    values.update(a['value'] for p in products.values() for r in p['au_rows'] for a in r['axes'])
    values.update(v for r in requests for v in r['option_values'])
    values.update(a['value'] for r in requests for a in r['target_selected_axes'])
    if not values.issubset(raw):
        raise ValueError('missing frozen price-free value')
    raw = {v: raw[v] for v in sorted(values)}
    views = {'nfkc_lower': {v: normalize(raw[v]) for v in raw}}
    guarded, observations, timings = {}, {}, {}
    from sudachipy import dictionary, tokenizer
    started = time.perf_counter()
    for edition, item in plan['dictionary_pins'].items():
        path = Path(item['path'])
        if sha(path) != item['sha256']:
            raise ValueError('dictionary binary mismatch')
        inputs.append(path)
        init = time.perf_counter()
        dictionary_object = dictionary.Dictionary(dict=str(path))
        tok = dictionary_object.tokenizer()
        timings[edition + '_init_seconds'] = time.perf_counter() - init
        for split in 'ABC':
            name = edition + '_' + split
            t = time.perf_counter()
            mode = getattr(tokenizer.Tokenizer.SplitMode, split)
            observations[name], views[name], guarded[name] = {}, {}, {}
            for v, price_free in raw.items():
                ms = tok.tokenize(price_free, mode)
                normalized = ''.join(m.normalized_form() for m in ms)
                observations[name][v] = {
                    'price_free': price_free, 'normalized': normalized,
                    'tokens': [{'surface': m.surface(), 'normalized': m.normalized_form(),
                                'oov': m.is_oov(), 'begin': m.begin(), 'end': m.end()} for m in ms]}
                views[name][v] = normalize(normalized)
                guarded[name][v] = price_free if protected(price_free) else normalized
            timings[name + '_tokenize_seconds'] = time.perf_counter() - t
        del tok, dictionary_object
    output = lab / 'run-v1'
    output.mkdir(exist_ok=False)
    write(output / 'observations.json', observations)
    write(output / 'views.json', {'ranking': views, 'guarded_gate': guarded, 'raw': raw})
    write(output / 'tokenizer-metrics.json', {
        name: {'unique_values': len(obs),
               'tokens': sum(len(x['tokens']) for x in obs.values()),
               'oov_tokens': sum(m['oov'] for x in obs.values() for m in x['tokens']),
               'values_with_oov': sum(any(m['oov'] for m in x['tokens']) for x in obs.values()),
               'changed_values_vs_nfkc': sum(views[name][v] != views['nfkc_lower'][v] for v in obs),
               'core_full_different_values': sum(views['core_' + name[-1]][v] != views['full_' + name[-1]][v] for v in obs)}
        for name, obs in observations.items()})
    rank_records = []
    for case in cases:
        candidates = products[case['dossier_id']]['au_rows']
        if not candidates:
            raise ValueError('case without candidates')
        for name in plan['ranking_views']:
            rank_records.append({'case_id': case['case_id'], 'view': name,
                                 'candidates': ranks(case['rakuten_selected']['axes'], candidates, views[name])})
    write_lines(output / 'rankings.jsonl', rank_records)
    docs, catalog = catalogs(requests, documents)
    frozen_dictionary = read(OLD / 'dictionaries-v1/dict2.json')
    for name, view in guarded.items():
        t = time.perf_counter()
        predictions = []
        states = dict(old_states)
        for req in requests:
            rid = req['request_id']
            if states[rid] == 'unknown':
                state, reason, trace = dictionary_request_state(req, docs, catalog, frozen_dictionary, view)
                states[rid] = state
                predictions.append({'request_id': rid, 'state': state, 'base_state': 'unknown',
                                    'reason': reason, 'trace': trace})
            else:
                predictions.append({'request_id': rid, 'state': states[rid], 'base_state': states[rid],
                                    'reason': 'immutable_existing_pipeline'})
        rows, decisions = assemble_mode(row_plans, requests, states)
        if any(states[rid] != old for rid, old in old_states.items() if old != 'unknown'):
            raise ValueError('existing known state overwritten')
        if [(r['case_id'], r['au_row_key']) for r in rows] != [(r['case_id'], r['au_row_key']) for r in row_plans]:
            raise ValueError('original candidate order or coverage changed')
        if {r['case_id'] for r in decisions} != {r['case_id'] for r in cases}:
            raise ValueError('original case coverage changed')
        write_lines(output / (name + '-requests.jsonl'), predictions)
        write_lines(output / (name + '-rows.jsonl'), rows)
        write_lines(output / (name + '-cases.jsonl'), decisions)
        timings[name + '_gate_seconds'] = time.perf_counter() - t
    # JS parity is on every observed ranking string, all pair scores for a small
    # deterministic synthetic corpus, and Unicode edge examples. No npm package.
    parity_inputs = sorted(set(raw.values()) | {o['normalized'] for rows in observations.values() for o in rows.values()})
    parity_inputs += ['青', '', 'a', 'aa', 'aaaa', 'abab', '😀青', 'Ａ Ｂ', 'a\u0085b', 'a\u001cb', 'ß', 'İ']
    js = """let s='';for await(const c of process.stdin)s+=c;
const norm=s=>s.normalize('NFKC').toLowerCase().replace(/[\\p{White_Space}\\u001c-\\u001f]/gu,'');
const grams=s=>{const a=Array.from(s);return new Set(a.length>1?a.slice(1).map((_,i)=>a[i]+a[i+1]):a)};
const dice=(a,b)=>{a=grams(a);b=grams(b);return a.size+b.size?2*[...a].filter(x=>b.has(x)).length/(a.size+b.size):0};
const v=JSON.parse(s);console.log(JSON.stringify({normalized:v.map(norm),scores:v.slice(-12).flatMap(a=>v.slice(-12).map(b=>dice(norm(a),norm(b))))}));"""
    node = subprocess.run(['node', '--input-type=module', '-e', js], input=json.dumps(parity_inputs),
                          text=True, capture_output=True, check=True)
    observed = json.loads(node.stdout)
    expected = {'normalized': [normalize(v) for v in parity_inputs],
                'scores': [dice(normalize(a), normalize(b)) for a in parity_inputs[-12:] for b in parity_inputs[-12:]]}
    if observed != expected:
        raise ValueError('Python/Node parity failed')
    write(output / 'node-parity.json', {'passed': True, 'strings': len(parity_inputs), 'score_pairs': 144,
                                       'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                                       'python_unicode_version': unicodedata.unidata_version,
                                       'scope': 'observed strings plus examples, not all Unicode inputs'})
    write(output / 'timings.json', {'phases': timings, 'processing_seconds': time.perf_counter() - started})
    pins = {str(p): sha(p) for p in inputs}
    if sha(lab / 'PLAN.json') != plan_pin or sha(__file__) != code_pin:
        raise ValueError('experiment changed during prediction')
    freeze = {'schema': 'sku-sudachi-dice-freeze-v1', 'plan_sha256': sha(lab / 'PLAN.json'),
              'input_pins': pins, 'code_pin': sha(__file__),
              'output_pins': {p.name: sha(p) for p in output.iterdir() if p.is_file()},
              'cases': len(cases), 'products': len(products), 'candidate_rows': len(row_plans),
              'bindings': sum(len(r['bindings']) for r in row_plans), 'rank_views': plan['ranking_views'],
              'new_reference_reads': False, 'historical_previously_evaluated_data': True,
              'SudachiPy_version': importlib.metadata.version('SudachiPy'), 'new_model_calls': 0}
    write(output / 'FREEZE.json', freeze)
    print(json.dumps({'phase': 'run', 'output': str(output), 'cases': len(cases), 'unique_values': len(raw)}))


def grade(lab):
    from grade_library_axis_domains_v1 import pruning
    from grade_final_test_subset_v1 import canonical_reference
    output = lab / 'run-v1'
    freeze = read(output / 'FREEZE.json')
    if sha(__file__) != freeze['code_pin'] or sha(lab / 'PLAN.json') != freeze['plan_sha256']:
        raise ValueError('frozen experiment changed')
    for p, expected in freeze['input_pins'].items():
        if sha(p) != expected:
            raise ValueError('input changed: ' + p)
    for name, expected in freeze['output_pins'].items():
        if sha(output / name) != expected:
            raise ValueError('prediction changed: ' + name)
    # Selected historical references are opened only after all prediction checks.
    refs_path = OLD / 'grade-v1/selected_eval_references.jsonl'
    refs = {r['case_id']: r['reference'] for r in lines(refs_path)}
    base_rows = lines(OLD / 'eval-v1/exact_current_dictionary2-rows.jsonl')
    base_cases = lines(OLD / 'eval-v1/exact_current_dictionary2-cases.jsonl')
    base_rowmap = {(r['case_id'], r['au_row_key']): r['state'] for r in base_rows}
    rank_counts = {name: Counter() for name in freeze['rank_views']}
    failures = []
    for rec in lines(output / 'rankings.jsonl'):
        cid, name, candidates = rec['case_id'], rec['view'], rec['candidates']
        label, allowed = canonical_reference(refs[cid])
        kept = [r for r in candidates if base_rowmap[cid, r['row_key']] != 'conflict']
        for scope, rows in [('all', candidates), ('after_conflict_guard', kept)]:
            count = rank_counts[name]
            if label == 'matched':
                count[scope + '_matched_cases'] += 1
                top = rows[0]['score'] if rows else None
                tied = [r for r in rows if r['score'] == top]
                first = bool(rows and rows[0]['row_key'] in allowed)
                count[scope + '_stable_top1_correct'] += first
                count[scope + '_top_tie_contains_correct'] += any(r['row_key'] in allowed for r in tied)
                count[scope + '_unique_top1_correct'] += len(tied) == 1 and first
                count[scope + '_top3_contains_correct'] += any(r['row_key'] in allowed for r in rows[:3])
                count[scope + '_top1_tied_cases'] += len(tied) > 1
                if not first:
                    failures.append({'case_id': cid, 'view': name, 'scope': scope,
                                     'top': rows[:3], 'allowed': allowed})
            elif label == 'unmatched':
                count[scope + '_unmatched_cases'] += 1
                count[scope + '_unmatched_cases_with_candidate'] += bool(rows)
                count[scope + '_unmatched_top_score_one'] += bool(rows and rows[0]['score'] == 1)
            else:
                count[scope + '_reference_unknown_cases'] += 1
    requests = lines(OLD / 'eval-v1/eval-requests.jsonl')
    plans = lines(OLD / 'eval-v1/eval-row-plans.jsonl')
    gate_scores = {}
    for name in ['base_dictionary2', *[v for v in freeze['rank_views'] if v != 'nfkc_lower']]:
        if name == 'base_dictionary2':
            rows, decisions = base_rows, base_cases
            reqs = lines(OLD / 'eval-v1/exact_current_dictionary2-requests.jsonl')
        else:
            rows, decisions, reqs = [lines(output / (name + '-' + k + '.jsonl')) for k in ('rows', 'cases', 'requests')]
        states = {r['request_id']: r['state'] for r in reqs}
        rowstates = {(r['case_id'], r['au_row_key']): r['state'] for r in rows}
        by_case = {c['case_id']: c for c in decisions}
        c = Counter()
        errors = []
        for d in decisions:
            cid, disposition = d['case_id'], d['disposition']
            label, allowed = canonical_reference(refs[cid])
            c[disposition] += 1
            if disposition == 'accept':
                tag = 'correct_accept' if label == 'matched' and d['au_row_key'] in allowed else 'wrong_accept' if label in ('matched', 'unmatched') else 'unknown_accept'
                c[tag] += 1
                if tag == 'wrong_accept':
                    errors.append(cid)
            elif disposition == 'reject':
                tag = 'correct_reject' if label == 'unmatched' else 'wrong_reject' if label == 'matched' else 'unknown_reject'
                c[tag] += 1
                if tag == 'wrong_reject':
                    errors.append(cid)
        remaining = {b['request_id'] for row in plans if by_case[row['case_id']]['disposition'] == 'review'
                     and rowstates[row['case_id'], row['au_row_key']] != 'conflict'
                     for b in row['bindings'] if states[b['request_id']] == 'unknown'}
        fields = ('direction', 'axis_name', 'option_values', 'target_document_id', 'target_selected_axes')
        contexts = {json.dumps({k: r[k] for k in fields}, sort_keys=True, ensure_ascii=False)
                    for r in requests if r['request_id'] in remaining}
        gate_scores[name] = {'counts': dict(c), 'error_case_ids': errors,
                             'pruning': pruning(rows, refs), 'residual_AI_contexts': len(contexts)}
    result = {'schema': 'sku-sudachi-dice-grade-v1', 'ranking': {k: dict(v) for k, v in rank_counts.items()},
              'gate': gate_scores, 'freeze_sha256': sha(output / 'FREEZE.json'),
              'selected_reference_sha256': sha(refs_path), 'reference_counts': dict(Counter(canonical_reference(r)[0] for r in refs.values())),
              'independent_test': False, 'external_80_case_claim_reproduced': False,
              'external_80_csv_missing': True, 'post_grade_tuning': False,
              'reference_kind': 'saved Claude machine annotations; not human gold'}
    write(lab / 'GRADE.json', result)
    write_lines(lab / 'ranking-failures.jsonl', failures)
    print(json.dumps({'phase': 'grade', 'ranking': result['ranking'], 'gate_counts': {n: v['counts'] for n, v in gate_scores.items()}}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['run', 'grade'])
    p.add_argument('--lab', type=Path, default=DEFAULT)
    args = p.parse_args()
    (run if args.phase == 'run' else grade)(args.lab)
