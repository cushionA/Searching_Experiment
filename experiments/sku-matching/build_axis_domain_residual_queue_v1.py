#!/usr/bin/env python3
"""Save a label-blind advisory residual queue for the predeclared main variant."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.lab-output/sku-classical-parser-20261015-v1/population-inputs'
FIELDS = ('direction', 'axis_name', 'option_values', 'target_document_id', 'target_selected_axes')
MODE = 'mutual_unique_raw_components'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def read(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(s) for s in f if s.strip()]


def rows(path, records):
    with path.open('x', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def build(run, output):
    run, output = run.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    freeze = json.loads((run / 'OUTPUT_FREEZE.json').read_text())
    paths = [SOURCE / n for n in ('requests.jsonl', 'row-plans.jsonl', 'case-plans.jsonl')]
    selected = [run / f'{MODE}-{kind}.jsonl' for kind in ('requests', 'rows', 'cases')]
    pins = {}
    for p in paths + selected:
        expected = (freeze['input_pins'][str(p.relative_to(ROOT))] if p in paths
                    else freeze['output_pins'][p.name])
        observed = sha(p)
        if observed != expected:
            raise ValueError(f'Frozen source/state changed: {p}')
        pins[str(p.relative_to(ROOT))] = observed
    requests, original_rows, cases = (read(p) for p in paths)
    states = {r['request_id']: r['state'] for r in read(selected[0])}
    row_states = {(r['case_id'], r['au_row_key']): r['state'] for r in read(selected[1])}
    decisions = {r['case_id']: r for r in read(selected[2])}
    request_by_id = {r['request_id']: r for r in requests}
    contexts, case_contexts, grouped_rows = {}, defaultdict(set), defaultdict(list)
    for r in original_rows:
        cid, key = r['case_id'], r['au_row_key']
        refs = []
        if decisions[cid]['disposition'] == 'review' and row_states[cid, key] != 'conflict':
            for b in r['bindings']:
                rid = b['request_id']
                if states[rid] != 'unknown':
                    continue
                req = request_by_id[rid]
                context = {field: req[field] for field in FIELDS}
                identity = json.dumps(context, ensure_ascii=False, sort_keys=True)
                context_id = hashlib.sha256(identity.encode()).hexdigest()
                entry = contexts.setdefault(context_id, {'context_id': context_id, 'context': context,
                                                         'requests': {}, 'candidate_refs': []})
                entry['requests'][rid] = req
                entry['candidate_refs'].append({'case_id': cid, 'au_row_key': key,
                                                'task_id': b['task_id'], 'request_id': rid})
                case_contexts[cid].add(context_id)
                refs.append(context_id)
        grouped_rows[cid].append({'au_row_key': key, 'hypothetical_state': row_states[cid, key],
                                 'all_original_bindings': r['bindings'],
                                 'residual_context_ids': sorted(set(refs))})
    output.mkdir(parents=True)
    rows(output / 'case-queue.jsonl', ({'case_id': c['case_id'],
         'hypothetical_disposition': decisions[c['case_id']]['disposition'],
         'production_adoption_allowed': False, 'all_original_candidates': grouped_rows[c['case_id']],
         'residual_context_ids': sorted(case_contexts[c['case_id']]),
         'unknown_competitors_must_not_certify_exclusive_matching': True} for c in cases))
    rows(output / 'residual-contexts.jsonl', ({'context_id': entry['context_id'],
         'context': entry['context'], 'original_requests': list(entry['requests'].values()),
         'candidate_refs': entry['candidate_refs'], 'production_adoption_allowed': False}
         for _, entry in sorted(contexts.items())))
    manifest = {'schema': 'axis-domain-advisory-residual-queue-v1', 'variant': MODE,
                'input_pins': pins, 'builder_code_sha256': sha(Path(__file__)),
                'case_count': len(cases), 'original_row_count': len(original_rows),
                'original_binding_count': sum(len(r['bindings']) for r in original_rows),
                'residual_context_count': len(contexts),
                'all_original_candidates_and_conditions_retained': True,
                'labels_or_grade_read': False, 'model_calls': 0, 'production_adoption_allowed': False,
                'output_pins': {p.name: sha(p) for p in output.iterdir() if p.is_file()}}
    with (output / 'MANIFEST.json').open('x', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({k: v for k, v in manifest.items() if k.endswith('_count')}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    build(**vars(p.parse_args()))
