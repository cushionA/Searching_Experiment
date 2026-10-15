#!/usr/bin/env python3
"""Measure helper workloads from already frozen catalog/ranking outputs."""
from collections import Counter, defaultdict
import json
from pathlib import Path

import grade_final_test_subset_v1 as G
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE,V3_ARCHIVE
from run_installed_library_selection_v1 import read,sha,write

ROOT=Path(__file__).resolve().parents[2]
LAB=ROOT/'.lab-output/sku-installed-library-matching-20261015-v1'
SOURCE=ROOT/'.lab-output/sku-classical-parser-20261015-v1/population-inputs'
POP=ROOT/'.lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs'


def main():
    final=LAB/'price-free';selected=final/'selection-v1';ranked=final/'ranking-v1'
    sf=json.loads((selected/'OUTPUT_FREEZE.json').read_text())
    rf=json.loads((ranked/'OUTPUT_FREEZE.json').read_text())
    for n,pin in sf['output_pins'].items():
        if sha(selected/n)!=pin:raise ValueError('selected output changed')
    for n,pin in rf['output_files'].items():
        if sha(ranked/n)!=pin['sha256']:raise ValueError('rank output changed')
    requests,rows,cases=read(SOURCE/'requests.jsonl'),read(SOURCE/'row-plans.jsonl'),read(POP/'cases.jsonl')
    keys=('direction','axis_name','option_values','target_document_id','target_selected_axes')
    def context(r):return json.dumps({k:r[k] for k in keys},ensure_ascii=False,sort_keys=True)
    all_contexts={context(r) for r in requests}
    members=[{'case_id':c['case_id'],'source_version':'v2' if '/eval-v2/' in c['source_record']['file'].replace('\\','/') else 'v3'} for c in cases]
    refs=G.read_label_archives(V2_ARCHIVE,V3_ARCHIVE,members,SOURCE/'row-plans.jsonl')
    state_by_mode={};decisions_by_mode={};workloads={}
    for mode in sf['counts']:
        states={r['request_id']:r['state'] for r in read(selected/f'{mode}-requests.jsonl')}
        row_states={(r['case_id'],r['au_row_key']):r['state'] for r in read(selected/f'{mode}-rows.jsonl')}
        decisions={r['case_id']:r['disposition'] for r in read(selected/f'{mode}-cases.jsonl')}
        remaining_ids={b['request_id'] for row in rows if decisions[row['case_id']]=='review'
            and row_states[row['case_id'],row['au_row_key']]!='conflict'
            for b in row['bindings'] if states[b['request_id']]=='unknown'}
        residual_contexts={context(r) for r in requests if r['request_id'] in remaining_ids}
        workloads[mode]={'all_case_row_candidates':len(rows),
            'contradicted_case_row_candidates':sum(s=='conflict' for s in row_states.values()),
            'remaining_case_row_candidates':sum(s!='conflict' for s in row_states.values()),
            'review_cases':sum(s=='review' for s in decisions.values()),
            'case_row_candidates_in_review_cases':sum(s!='conflict' and decisions[cid]=='review' for (cid,_),s in row_states.items()),
            'all_contexts':len(all_contexts),'residual_contexts':len(residual_contexts),
            'context_reduction_from_all_ai_fraction':1-len(residual_contexts)/len(all_contexts),
            'all_option_comparisons':sum(len(json.loads(c)['option_values']) for c in all_contexts),
            'residual_option_comparisons':sum(len(json.loads(c)['option_values']) for c in residual_contexts),
            'is_eligible_workload_estimate_not_measured_tokens_cost_or_existing_hybrid_reduction':True}
        state_by_mode[mode]=row_states;decisions_by_mode[mode]=decisions
    metrics=defaultdict(Counter);wrong_candidates=defaultdict(list)
    for r in read(ranked/'rankings.jsonl'):
        mode,name=r['view'],r['view']+'/'+r['scorer']
        cid=r['case_id'];label,allowed=G.canonical_reference(refs[cid])
        candidates=[c for c in r['candidates'] if state_by_mode[mode][cid,c['row_key']]!='conflict']
        c=metrics[name]
        c['all_remaining_rows']+=len(candidates)
        c['first3_remaining_rows']+=min(3,len(candidates))
        if decisions_by_mode[mode][cid]=='review':
            c['review_remaining_rows']+=len(candidates)
            c['review_first3_rows']+=min(3,len(candidates))
        if label=='matched':
            c['positive_cases']+=1
            hit=any(v['row_key'] in allowed for v in candidates[:3])
            c['positive_first3_remaining_contains_match']+=int(hit)
            if not hit:wrong_candidates[name].append(cid)
            if decisions_by_mode[mode][cid]=='review':
                c['review_positive_cases']+=1
                c['review_positive_first3_contains_match']+=int(hit)
            c['all_reference_rows_in_remaining_first3']+=int(set(allowed).issubset({v['row_key'] for v in candidates[:3]}))
        # Forced single-row ranking is a deliberately unsafe diagnostic, never a decision.
        if label in ('matched','unmatched') and r['candidates'][0]['tie_size']==1:
            c['known_unique_top1_cases']+=1
            if label=='matched' and r['candidates'][0]['row_key'] in allowed:c['known_unique_top1_correct']+=1
            elif label=='unmatched':c['negative_cases_with_unique_top1']+=1
    data={'schema':'installed-library-helper-workload-v1','independent_test':False,
        'grader_output_sha256':sha(final/'grade.json'),'model_inference_count':0,
        'custom_sku_grammar_used':False,'custom_alias_dictionary_used':False,
        'catalog_workloads':workloads,
        'guarded_ranking':{k:{'counts':dict(v),'positive_first3_missed_case_ids':wrong_candidates[k],
            'first3_is_initial_reading_order_not_authorized_permanent_candidate_pruning':True,
            'unknown_competitors_must_still_be_checked_for_exclusive_final_matching':True}
            for k,v in sorted(metrics.items())}}
    write(final/'HELPER_ANALYSIS.json',data)
    print(json.dumps({'spacy_catalog':workloads['spacy_norm'],
        'spacy_token_set_guard':data['guarded_ranking']['spacy_norm/token_set_ratio']},ensure_ascii=False))


if __name__=='__main__':main()
