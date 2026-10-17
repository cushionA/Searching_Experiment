#!/usr/bin/env python3
"""Private Kaggle CPU diagnostic: frozen lookup, Dice ranking and original gate.

Scoring references stay on the host. Replayed cases measure load, not accuracy.
Original graph/phrase condition states are inputs, not recomputed in this timer.
"""
from __future__ import annotations
import argparse
import ast
from collections import defaultdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time
import types
import typing


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def frozen_functions(folder):
    """Execute only named function ASTs, unchanged from hash-pinned snapshots."""
    import re
    import unicodedata
    ns = {'Any': typing.Any, 'Mapping': typing.Mapping, 'defaultdict': defaultdict,
          're': re, 'unicodedata': unicodedata}
    wanted = {
        'replay_dev_nli_policies_v1.py': ['row_state', 'assemble'],
        'run_installed_library_selection_v1.py': ['catalogs', 'selected_state'],
        'library_train_dictionary_v1.py': ['_valid_string', '_has_numeric', '_side_for_input',
            '_group_maps', '_mapped_value', 'dictionary_request_state'],
        'run_library_axis_domains_v1.py': ['assemble_mode'],
        'run_sudachi_dice_v1.py': ['normalize', 'bigrams', 'dice', 'protected', 'ranks'],
    }
    for name, names in wanted.items():
        tree = ast.parse((folder / name).read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        if {n.name for n in nodes} != set(names):
            raise ValueError('missing original function')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), name, 'exec'), ns)
    if 'run_installed_library_selection_v1' not in sys.modules:
        module = types.ModuleType('run_installed_library_selection_v1')
        module.selected_state = ns['selected_state']
        sys.modules[module.__name__] = module
    return ns


def write_gzip(path, records):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n')


def main(folder, output, payload_raw_sha=None):
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((folder / 'MANIFEST.json').read_text())
    payload_path = folder / 'payload.json.gz'
    expanded_payload = not payload_path.is_file()
    if expanded_payload:
        payload_path = folder / 'payload.json'
        if not payload_raw_sha or sha(payload_path) != payload_raw_sha:
            raise ValueError('expanded payload does not match original raw SHA256')
    for name, pin in manifest['files'].items():
        if name == 'payload.json.gz' and expanded_payload:
            continue
        if sha(folder / name) != pin:
            raise ValueError('input pin mismatch: ' + name)
    ns = frozen_functions(folder)
    opener = open if expanded_payload else gzip.open
    with opener(payload_path, 'rt', encoding='utf-8') as f:
        data = json.load(f)
    plan = data['plan']
    cases, rows, requests = data['cases'], data['rows'], data['requests']
    if (len(cases), len(data['products']), len(rows), sum(len(r['bindings']) for r in rows)) != (2662, 213, 62502, 267334):
        raise ValueError('wrong complete population')
    raw = data['raw_view']
    values = sorted({v for r in requests for v in r['option_values']} |
                    {a['value'] for r in requests for a in r['target_selected_axes']} |
                    {a['value'] for c in cases for a in c['axes']} |
                    {a['value'] for p in data['products'] for r in p['au_rows'] for a in r['axes']})
    normalize, ranks = ns['normalize'], ns['ranks']
    views = {'nfkc_lower': {v: normalize(raw[v]) for v in values}}
    guarded, lexical, dictionary_info = {}, {}, {}
    from sudachipy import dictionary, tokenizer
    for edition in ['core', 'full']:
        package = __import__('sudachidict_' + edition)
        path = Path(package.__file__).parent / 'resources/system.dic'
        if sha(path) != manifest['dictionary_binary_sha256'][edition]:
            raise ValueError('dictionary binary differs')
        t = time.perf_counter()
        obj = dictionary.Dictionary(dict=str(path))
        tok = obj.tokenizer()
        dictionary_info[edition] = {'bytes': path.stat().st_size, 'sha256': sha(path),
                                    'load_seconds': time.perf_counter() - t}
        for mode in 'ABC':
            name = edition + '_' + mode
            t = time.perf_counter()
            views[name], guarded[name] = {}, {}
            total = oov = 0
            for v in values:
                ms = tok.tokenize(raw[v], getattr(tokenizer.Tokenizer.SplitMode, mode))
                result = ''.join(m.normalized_form() for m in ms)
                total += len(ms)
                oov += sum(m.is_oov() for m in ms)
                views[name][v] = normalize(result)
                guarded[name][v] = raw[v] if ns['protected'](raw[v]) else result
            lexical[name] = {'tokens': total, 'oov_tokens': oov,
                             'unique_values': len(values), 'seconds': time.perf_counter() - t}
        del tok, obj
    docs, catalog = ns['catalogs'](requests, data['documents'])
    per_catalog = {p['dossier_id']: {k: v for k, v in catalog.items() if k[0] == p['dossier_id']}
                   for p in data['products']}
    req_by_id = {r['request_id']: r for r in requests}
    per_rows = defaultdict(list)
    for r in rows:
        per_rows[r['case_id']].append(r)
    products = {p['dossier_id']: p for p in data['products']}
    infer = ns['dictionary_request_state']
    base = data['base_states']
    states = {'exact': data['exact_states'], 'current': base, 'dictionary2': dict(base)}
    for req in requests:
        rid = req['request_id']
        if states['dictionary2'][rid] == 'unknown':
            d = docs[req['target_document_id']]['dossier_id']
            states['dictionary2'][rid] = infer(req, docs, per_catalog[d], data['dictionary'], raw)[0]
    for name, view in guarded.items():
        states[name] = dict(states['dictionary2'])
        for req in requests:
            rid = req['request_id']
            if states[name][rid] == 'unknown':
                d = docs[req['target_document_id']]['dossier_id']
                states[name][rid] = infer(req, docs, per_catalog[d], data['dictionary'], view)[0]
    decisions = {}
    for name, s in states.items():
        rr, cc = ns['assemble_mode'](rows, requests, s)
        decisions[name] = {c['case_id']: c for c in cc}
        write_gzip(output / (name + '-rows.jsonl.gz'), rr)
        write_gzip(output / (name + '-cases.jsonl.gz'), cc)
    write_gzip(output / 'rankings.jsonl.gz', (
        {'case_id': c['case_id'], 'split': c['split'], 'view': name,
         'candidates': ranks(c['axes'], products[c['dossier_id']]['au_rows'], view)}
        for c in cases for name, view in views.items()))
    # Warm single-worker load. Original text-normalization tables and graph
    # states are cached; dictionary lookup, all Dice scores and gate run again.
    per_product = defaultdict(list)
    for c in cases:
        per_product[c['dossier_id']].append(c)
    load_results = []
    for target in [1000, 10000, plan['warm_target_cases']]:
        for name in plan['warm_load_modes']:
            started, cpu = time.perf_counter(), time.process_time()
            count = rowcount = bindings = differences = 0
            digest = hashlib.sha256()
            while count < target:
                for dossier in sorted(per_product):
                    for case in per_product[dossier]:
                        cid = case['case_id']
                        case_rows = per_rows[cid]
                        ids = {b['request_id'] for r in case_rows for b in r['bindings']}
                        current = {rid: base[rid] for rid in ids}
                        for rid in ids:
                            req = req_by_id[rid]
                            if current[rid] == 'unknown':
                                current[rid] = infer(req, docs, per_catalog[dossier], data['dictionary'], raw)[0]
                            if name != 'nfkc_lower' and current[rid] == 'unknown':
                                current[rid] = infer(req, docs, per_catalog[dossier], data['dictionary'], guarded[name])[0]
                        ranked = ranks(case['axes'], products[dossier]['au_rows'], views[name])
                        rr, cc = ns['assemble_mode'](case_rows, [], current)
                        expected_name = 'dictionary2' if name == 'nfkc_lower' else name
                        differences += cc[0] != decisions[expected_name][cid]
                        rowmap = {r['au_row_key']: r['state'] for r in rr}
                        filtered = [r for r in ranked if rowmap[r['row_key']] != 'conflict']
                        digest.update(json.dumps([cid, cc[0]['disposition'], filtered[0]['row_key'] if filtered else None]).encode())
                        count += 1
                        rowcount += len(case_rows)
                        bindings += sum(len(r['bindings']) for r in case_rows)
                    if count >= target:
                        break
            elapsed = time.perf_counter() - started
            record = {'mode': name, 'target': target, 'cases_including_repeats': count,
                      'candidate_rows': rowcount, 'condition_bindings': bindings,
                      'wall_seconds': elapsed, 'cpu_seconds': time.process_time() - cpu,
                      'cases_per_second': count / elapsed, 'decision_differences': differences,
                      'checksum': digest.hexdigest(), 'worker_count': 1}
            if differences:
                raise ValueError('replay gate decision changed')
            load_results.append(record)
            print(json.dumps({'load': record}), flush=True)
    stats = {'schema': 'sku-dice-volume-benchmark-v1', 'load': load_results,
             'lexical': lexical, 'dictionaries': dictionary_info, 'new_model_calls': 0,
             'cpu_only': True, 'available_cpus': os.cpu_count(),
             'peak_process_RSS_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
             'normalization_table_cached': True, 'similarity_score_cache': False,
             'graph_phrase_states_from_frozen_source': True,
             'replays_are_not_accuracy_samples': True}
    (output / 'benchmark.json').write_text(json.dumps(stats, indent=2) + '\n')
    receipt = {'schema': 'sku-dice-volume-remote-freeze-v1',
               'input_manifest_sha256': sha(folder / 'MANIFEST.json'),
               'plan': plan, 'worker_code_sha256': sha(__file__),
               'outputs': {p.name: sha(p) for p in output.iterdir() if p.is_file()},
               'compressed_payload_sha256_checked': not expanded_payload,
               'expanded_payload_sha256_checked': payload_raw_sha if expanded_payload else None,
               'reference_data_read': False, 'cases': len(cases), 'products': len(products)}
    (output / 'FREEZE.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'finished': True, 'output': str(output)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--payload-raw-sha')
    args = p.parse_args()
    main(args.input, args.output, args.payload_raw_sha)
