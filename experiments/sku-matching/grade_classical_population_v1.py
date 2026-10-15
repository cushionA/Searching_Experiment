#!/usr/bin/env python3
"""Grade only the original frozen DEV population, after predictions freeze."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

import grade_final_test_subset_v1 as G
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE
from replay_dev_nli_policies_v1 import pruning_metrics
from run_classical_sku_v1 import read, sha, write, VARIANTS

CASE_SHA = '06898908758f6a6dfe3446ee63c7688c0e68d4928dde7dbfe05a821e2ef63a09'
PRODUCT_SHA = '75353ed9c40333bef064109d8c99a0baf72c707564724ca1bd9481ccae539926'


def grade(source, output, population):
    freeze = json.loads((output/'OUTPUT_FREEZE.json').read_text())
    if freeze['source_path'] != str(source.resolve()) or freeze['labels_read'] is not False:
        raise ValueError('frozen label-free source differs')
    for name, pin in freeze['input_pins'].items():
        if sha(source/name) != pin:
            raise ValueError('source changed after predictions freeze')
    for name, pin in freeze['output_pins'].items():
        if sha(output/name) != pin:
            raise ValueError('output changed after predictions freeze')
    for name, pin in freeze['code_pins'].items():
        if sha(Path(__file__).with_name(name)) != pin:
            raise ValueError('executed code changed')
    if sha(population/'cases.jsonl') != CASE_SHA or sha(population/'products.jsonl') != PRODUCT_SHA:
        raise ValueError('original DEV population identity differs')
    native_cases, native_products = read(population/'cases.jsonl'), read(population/'products.jsonl')
    if (len(native_cases), len(native_products)) != (1370,107):
        raise ValueError('full original DEV population required')
    products = G.unique_index(native_products, 'dossier_id', 'original DEV products')
    plans = G.unique_index(read(source/'case-plans.jsonl'), 'case_id', 'prepared DEV plans')
    expected = G.unique_index(native_cases, 'case_id', 'original DEV cases')
    if set(plans) != set(expected):
        raise ValueError('exact original DEV membership not retained')
    membership = []
    for cid, case in expected.items():
        native_keys = [r['row_key'] for r in products[case['dossier_id']]['au_rows']]
        if plans[cid]['row_keys'] != native_keys:
            raise ValueError('original AU row array changed')
        version = ('v2' if '/eval-v2/' in case['source_record']['file'].replace('\\','/') else
                   'v3' if '/eval-v3/' in case['source_record']['file'].replace('\\','/') else None)
        if version is None or plans[cid]['source_version'] != version:
            raise ValueError('case source version differs')
        membership.append({'case_id':cid, 'source_version':version, 'split':version})
    if Counter(m['source_version'] for m in membership) != {'v2':1295, 'v3':75}:
        raise ValueError('original version counts differ')
    refs = G.read_label_archives(V2_ARCHIVE,V3_ARCHIVE,membership,source/'row-plans.jsonl')
    result = {'schema':'classical-sku-full-dev-grade-v1','independent_test':False,
        'reference_kind':'Claude Haiku5.5 machine annotation, not human gold',
        'original_dev_case_sha256':CASE_SHA,'original_dev_product_sha256':PRODUCT_SHA,
        'output_freeze_sha256':sha(output/'OUTPUT_FREEZE.json'),
        'grader_code_sha256':sha(Path(__file__)),'case_count':1370,'variants':{}}
    variants = tuple(freeze['variants'])
    if set(variants) not in (set(VARIANTS), set(VARIANTS) | {'axis_alias'}):
        raise ValueError('unexpected variant inventory')
    for mode in variants:
        predictions = read(output/f'{mode}-cases.jsonl')
        accepted = [p for p in predictions if p['decision']=='accept']
        known = [p for p in accepted if G.canonical_reference(refs[p['case_id']])[0] in ('matched','unmatched')]
        correct = [p for p in known if p['au_row_key'] in (G.canonical_reference(refs[p['case_id']])[1] or [])]
        wrong = [p for p in known if p not in correct]
        result['variants'][mode] = {'score':G.score_variant(membership,predictions,refs),
            'pruning':pruning_metrics(read(output/f'{mode}-rows.jsonl'),refs),
            'accepted_all':len(accepted),'accepted_known':len(known),'correct_accepted_known':len(correct),
            'precision_among_known_accepts':len(correct)/len(known) if known else None,
            'false_match_rate_among_known_accepts':len(wrong)/len(known) if known else None,
            'wrong_accepted_case_ids':[p['case_id'] for p in wrong],
            'accepted_unknown_reference_case_ids':[p['case_id'] for p in accepted if p not in known]}
    write(output/'grade.json',result)
    print(json.dumps({m:{k:v for k,v in r.items() if k not in ('score','pruning','wrong_accepted_case_ids','accepted_unknown_reference_case_ids')} |
        {'metrics':r['score']['metrics'],'matching_rows_marked_conflict':r['pruning']['matching_rows_marked_conflict']}
        for m,r in result['variants'].items()},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--population',type=Path,required=True)
    grade(**vars(p.parse_args()))
