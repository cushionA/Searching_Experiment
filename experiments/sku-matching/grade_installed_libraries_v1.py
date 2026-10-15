#!/usr/bin/env python3
"""Grade frozen library trials using the unchanged original DEV references."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import grade_final_test_subset_v1 as G
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE,V3_ARCHIVE
from replay_dev_nli_policies_v1 import pruning_metrics
from run_installed_library_selection_v1 import sha,read,write

ROOT=Path(__file__).resolve().parents[2]
CASE_SHA='06898908758f6a6dfe3446ee63c7688c0e68d4928dde7dbfe05a821e2ef63a09'
PRODUCT_SHA='75353ed9c40333bef064109d8c99a0baf72c707564724ca1bd9481ccae539926'


def grade(experiment,source,population):
    selected=experiment/'selection-v1';ranked=experiment/'ranking-v1'
    sf=json.loads((selected/'OUTPUT_FREEZE.json').read_text())
    rf=json.loads((ranked/'OUTPUT_FREEZE.json').read_text())
    ri=json.loads((ranked/'INPUT_FREEZE.json').read_text())
    for name,pin in sf['input_pins'].items():
        if sha(source/name)!=pin:raise ValueError('selected input changed')
    for name,pin in sf['output_pins'].items():
        if sha(selected/name)!=pin:raise ValueError('selected output changed')
    for name,pin in rf['output_files'].items():
        if sha(ranked/name)!=pin['sha256']:raise ValueError('ranking output changed')
    if sha(Path(sf['view_path']))!=sf['view_sha256']:raise ValueError('view changed')
    if sha(Path(__file__).with_name('run_installed_library_selection_v1.py'))!=sf['code_sha256']:
        raise ValueError('selected code changed')
    for key,name in (('ranking_code','run_installed_library_ranking_v1.py'),('views_code','installed_library_views_v1.py')):
        if sha(Path(__file__).with_name(name))!=ri['source_sha256'][key]:raise ValueError('library code changed')
    if (sha(population/'cases.jsonl'),sha(population/'products.jsonl'))!=(CASE_SHA,PRODUCT_SHA):
        raise ValueError('original DEV membership differs')
    cases,products=read(population/'cases.jsonl'),read(population/'products.jsonl')
    case_index=G.unique_index(cases,'case_id','DEV cases');product_index=G.unique_index(products,'dossier_id','DEV products')
    members=[]
    for c in cases:
        path=c['source_record']['file'].replace('\\','/')
        version='v2' if '/eval-v2/' in path else 'v3' if '/eval-v3/' in path else None
        if version is None:raise ValueError('foreign source version')
        members.append({'case_id':c['case_id'],'source_version':version,'split':version})
    if (len(cases),len(products))!=(1370,107):raise ValueError('full DEV required')
    rank_rows=read(ranked/'rankings.jsonl');grouped=defaultdict(list)
    for r in rank_rows:
        c=case_index[r['case_id']]
        native=product_index[c['dossier_id']]['au_rows']
        if {v['row_key'] for v in r['candidates']}!={v['row_key'] for v in native} or len(r['candidates'])!=len(native):
            raise ValueError('ranking removed/duplicated original candidates')
        if any(v['raw_axis_values']!=[{'axis_name':a['axis_name'],'value':a['value']} for a in native[v['source_order']]['axes']]
                for v in r['candidates']):raise ValueError('ranking altered original axes')
        if any(k in r for k in ('decision','label','state','matched')):raise ValueError('ranker emitted decisions')
        grouped[r['view']+'/'+r['scorer']].append(r)
    expected_variants={m+'/'+s for m in ri['view_modes'] for s in ri['scorers']}
    if set(grouped)!=expected_variants or any(len(g)!=1370 or len({r['case_id'] for r in g})!=1370 for g in grouped.values()):
        raise ValueError('complete predeclared variant membership required')
    # No reference archive is opened before all library outputs and rowsets pass.
    refs=G.read_label_archives(V2_ARCHIVE,V3_ARCHIVE,members,source/'row-plans.jsonl')
    result={'schema':'installed-library-dev-grade-v1','independent_test':False,
        'reference_kind':'Claude Haiku5.5 machine annotation, not human gold',
        'library_rules_fitted_on_labels':False,'custom_parser_used':False,'custom_alias_dictionary_used':False,
        'selection_output_freeze_sha256':sha(selected/'OUTPUT_FREEZE.json'),
        'ranking_output_freeze_sha256':sha(ranked/'OUTPUT_FREEZE.json'),
        'grader_code_sha256':sha(Path(__file__)),'selection':{},'ranking':{}}
    for mode in sf['counts']:
        decisions=read(selected/f'{mode}-cases.jsonl')
        known_accept=[];correct=[];unknown_accept=[];reject_correct=[];reject_wrong=[];reject_unknown=[]
        for d in decisions:
            label,keys=G.canonical_reference(refs[d['case_id']])
            if d['disposition']=='accept':
                if label not in ('matched','unmatched'):unknown_accept.append(d['case_id'])
                else:
                    known_accept.append(d['case_id'])
                    if d['au_row_key'] in (keys or []):correct.append(d['case_id'])
            if d['disposition']=='reject':
                (reject_correct if label=='unmatched' else reject_wrong if label=='matched' else reject_unknown).append(d['case_id'])
        result['selection'][mode]={'binary_score':G.score_variant(members,decisions,refs),
            'counts':sf['counts'][mode],
            'accepted_known':len(known_accept),'correct_accepted_known':len(correct),
            'wrong_accepted_known_case_ids':sorted(set(known_accept)-set(correct)),
            'accepted_unknown_reference_case_ids':unknown_accept,
            'precision_among_known_accepts':len(correct)/len(known_accept) if known_accept else None,
            'correct_explicit_reject_count':len(reject_correct),'wrong_explicit_reject_case_ids':reject_wrong,
            'unknown_reference_explicit_reject_case_ids':reject_unknown,
            'pruning':pruning_metrics(read(selected/f'{mode}-rows.jsonl'),refs)}
    for name,group in sorted(grouped.items()):
        counts=Counter();missed=[];top1_wrong=[];ambiguous=[]
        for r in group:
            label,keys=G.canonical_reference(refs[r['case_id']])
            if label not in ('matched','unmatched'):
                counts['reference_unknown']+=1;continue
            if label=='unmatched':
                counts['negative_cases_offered_candidates']+=1;continue
            if not keys:raise ValueError('matching row set missing')
            counts['positive_cases']+=1
            native_keys=set(keys)
            top=[c for c in r['candidates'] if c['rank']==1]
            if len(top)==1:
                counts['unique_top1_positive_cases']+=1
                counts['unique_top1_correct']+=int(top[0]['row_key'] in native_keys)
            else:ambiguous.append(r['case_id'])
            hit1=r['candidates'][0]['row_key'] in native_keys
            counts['first_position_correct']+=int(hit1)
            if not hit1:top1_wrong.append(r['case_id'])
            counts['any_tied_top1_correct']+=int(any(c['row_key'] in native_keys for c in top))
            hit3=any(c['row_key'] in native_keys for c in r['candidates'][:3])
            counts['first3_positions_contain_matching_row']+=int(hit3)
            if not hit3:missed.append(r['case_id'])
            counts['all_reference_rows_in_first3_positions']+=int(native_keys.issubset({c['row_key'] for c in r['candidates'][:3]}))
            counts['tie_inclusive_rank3_contains_matching_row']+=int(any(c['rank']<=3 and c['row_key'] in native_keys for c in r['candidates']))
        result['ranking'][name]={'counts':dict(counts),
            'top1_position_hit_rate':counts['first_position_correct']/counts['positive_cases'],
            'top3_position_hit_rate':counts['first3_positions_contain_matching_row']/counts['positive_cases'],
            'first3_missing_matching_row_case_ids':missed,'top1_position_wrong_case_ids':top1_wrong,
            'positive_top1_tied_case_ids':ambiguous,'final_decisions_emitted':False}
    write(experiment/'grade.json',result)
    print(json.dumps({'selection':{m:{'correct_accept':v['correct_accepted_known'],
        'wrong_accept':len(v['wrong_accepted_known_case_ids']),'correct_reject':v['correct_explicit_reject_count'],
        'wrong_reject':len(v['wrong_explicit_reject_case_ids'])} for m,v in result['selection'].items()},
        'ranking':{m:{'top1':v['top1_position_hit_rate'],'top3':v['top3_position_hit_rate'],
        'unique_top1_correct':v['counts']['unique_top1_correct']} for m,v in result['ranking'].items()}},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--experiment',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--population',type=Path,required=True)
    grade(**vars(p.parse_args()))
