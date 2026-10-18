#!/usr/bin/env python3
"""Verify a portable product-level test corpus against its captured sources."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import audit_product_sku_sources_v1 as AUDIT
import build_product_volume_normal_v1 as BUILD


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def verify(root):
    root = root.resolve()
    packets = rows(root/'inputs/products.jsonl')
    products = rows(root/'source_records/original_products.jsonl')
    cases = rows(root/'source_records/original_cases.jsonl')
    aliases = read(root/'source_records/raw_aliases.json')
    prov = {p['product_pair_id']: p for p in rows(root/'source_records/provenance.jsonl')}
    assert len({p['product_pair_id'] for p in packets}) == len(packets)
    assert len({p['product']['dossier_id'] for p in products}) == len(products)
    assert len({p['case']['case_id'] for p in cases}) == len(cases)
    assert set(prov) == {p['product_pair_id'] for p in packets}
    pp = {p['product_pair_id']: p for p in packets}
    grouped = defaultdict(list)
    for row in cases:
        grouped[row['case']['dossier_id']].append(row['case'])
    assert set(grouped) == set(pp)
    for source in aliases.values():
        path = (root/source['blob_path']).resolve()
        assert root in path.parents
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
    old_source_path = AUDIT.source_path
    AUDIT.source_path = lambda value: root/aliases[value.replace('\\','/')]['blob_path']
    sku_counts = Counter()
    try:
        for record in products:
            product=record['product'];pid=product['dossier_id'];packet=pp[pid]
            source_cases=grouped[pid]
            ra, aa = AUDIT.audit_rakuten(product,source_cases), AUDIT.audit_au(product)
            assert ra['sha256_match'] and aa['sha256_match'], pid
            assert ra['native_variant_ids']==len(source_cases) and not ra['additional_native_variants'], pid
            assert not ra['case_ids_axes_mismatch'] and not ra['case_ids_variant_absent'], pid
            assert not ra['variant_id_selector_value_conflicts'] and aa['all_native_cells_match_provided_rows'], pid
            assert packet==read(root/'packets'/(pid+'.json')), pid
            assert packet['schema_version']==BUILD.VERSION
            r=packet['rakuten'];a=packet['au']
            assert r['title']==product['rakuten_product']['title'] and r['url']==product['rakuten_product']['url']
            assert a['title']==product['au_product']['title'] and a['product_id']==str(product['au_product']['product_id'])
            src={c['rakuten_selected']['source_row_key']:c['rakuten_selected'] for c in source_cases}
            assert len(src)==len(r['skus'])==len({s['sku_key'] for s in r['skus']}), pid
            assert {s['sku_key'] for s in r['skus']}==set(src), pid
            for sku in r['skus']:
                s=src[sku['sku_key']]
                assert sku['variant_id']==s['variant_id'] and sku['native_row_index']==s['source_row_index']
                assert [(x['name'],x['value']) for x in sku['conditions']]==[(x['axis_label'],x['value']) for x in s['axes']]
                assert [x['axis_index'] for x in sku['conditions']]==list(range(len(s['axes'])))
                assert sku['auxiliary_attributes']==[{'name':x['title'],'value':x['value'],'unit':x.get('unit')} for x in s.get('variant_attributes',[])]
            assert [(x['name'],x['values']) for x in r['sku_axes']]==[(x.get('label') or x.get('key') or '',x.get('values',[])) for x in product['rakuten_product']['selector_families']]
            au_src={row['row_key']:row for row in product['au_rows']}
            assert len(a['skus'])==len(au_src)==len({s['sku_key'] for s in a['skus']}),pid
            assert {s['sku_key'] for s in a['skus']}==set(au_src)
            for sku in a['skus']:
                original=au_src[sku['sku_key']]
                assert (sku['native_row_index'],sku['native_column_index'])==(original['row_index'],original['column_index'])
                assert [(x['name'],x['value']) for x in sku['conditions']]==[(x['axis_name'],x['value']) for x in original['axes']]
            for side in ['rakuten','au']:
                original=product[f'{side}_product']; ref=prov[pid]['raw_sources'][side]
                recomputed,_=BUILD.body(side,original,ref,root)
                assert packet[side]['description']==recomputed, (pid,side,'description re-extraction')
                for sku in packet[side]['skus']:
                    assert not set(sku)&{'price','stock','prediction','expected','label','score'}
            assert a['non_sku_options']['complete_value_lists'] is None
            assert a['non_sku_options']['current_selected_values'] is None
            sku_counts.update({'rakuten':len(r['skus']),'au':len(a['skus']),'candidate_pairs':len(r['skus'])*len(a['skus'])})
    finally:
        AUDIT.source_path=old_source_path
    profile=read(root/'PROFILE.json')
    assert profile['product_pairs']==len(packets)
    assert profile['rakuten_SKUs']==sku_counts['rakuten'] and profile['au_SKUs']==sku_counts['au']
    assert profile['candidate_row_pairs']==sku_counts['candidate_pairs']
    assert profile['labels_included'] is False and profile['model_predictions_included'] is False
    for key,subset in read(root/'subsets.json').items():
        assert len(subset)==len(set(subset)) and set(subset)<=set(pp),key
    return {'passed':True,'product_pairs':len(packets),**dict(sku_counts),
            'native_source_coverage_verified':True,'all_description_re_extractions_equal':True,
            'all_source_blob_hashes_match':True,'all_alias_paths_verified':len(aliases),
            'no_synthetic_SKUs':True,'no_reference_or_prediction_fields_in_inputs':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('dataset',type=Path,nargs='?',default=Path(__file__).resolve().parents[1])
    print(json.dumps(verify(p.parse_args().dataset),ensure_ascii=False,indent=2))
