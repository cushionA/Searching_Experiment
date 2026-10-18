#!/usr/bin/env python3
"""Package captured, source-complete natural product pairs; never run a model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import shutil

import audit_product_sku_sources_v1 as AUDIT
import sku_spec_table_first_v1 as TABLE
from build_scoped_html_blocks_v1 import _DOM, _descendants, _text

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / '.lab-output/sku-dictionary-half-20261015-v1/split-v1'
DEFAULT = ROOT / '.lab-output/sku-product-volume-normal-20261018-v1/bundle'
VERSION = 'sku-product-volume-input-v1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, obj):
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def jsonl(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')


def body(side, product, raw_ref, output):
    link_roots = []
    if side == 'au':
        raw = json.loads((output / raw_ref['blob_path']).read_text())
        # Use the captured API's primary description fields. The video
        # rendition remains available in the bundled original JSON.
        fields = [key for key in ['itemComment', 'detailComment', 'extraItemComment']
                  if isinstance(raw.get('itemInfo', {}).get(key), str)]
        locators = ['$.itemInfo.'+key for key in fields]
    else:
        locators = [{'kind': 'html_text', 'encoding': product.get('encoding', 'EUC-JP')}]
    refs = [{'raw_file': raw_ref['blob_path'], 'sha256': raw_ref['sha256'], 'locator': loc} for loc in locators]
    # Omit description.text: an empty text value would suppress raw fallback.
    doc = {'side': side, 'description': {'segments': [{'source_refs': refs}]}}
    result = TABLE.select_document(doc, output)
    selected = result['text']
    if side == 'au' and result['mode'] == 'full_body_fallback':
        tree = _DOM(selected).finish()
        selected = _text(tree.root)
        link_roots = [tree.root]
    elif side == 'rakuten' and result['mode'] == 'full_body_fallback':
        html,_ = TABLE._decode_rakuten_html((output/raw_ref['blob_path']).read_bytes(),locators[0])
        tree = _DOM(html).finish()
        link_roots = [n for n in tree.nodes if n.tag == 'span' and 'item_desc' in n.attrs.get('class','').split()]
    links = []
    if result['mode'] == 'spec_table':
        for table in result.get('tables', []):
            for row in table.get('rows', []):
                for cell in row.get('cells', []):
                    links.extend({'text':x['text'],'href':x['href']} for x in cell.get('links', []))
    else:
        for node in link_roots:
            links.extend({'text':_text(a),'href':a.attrs.get('href','')} for a in _descendants(node,{'a'}))
    links = list({(x['text'],x['href']):x for x in links}.values())
    mode = result['mode'] if selected.strip() else 'unavailable'
    lines = [{'evidence_id': f'{side}-description-{i:04d}', 'text': line}
             for i, line in enumerate(selected.splitlines(), 1)]
    return ({'mode': mode, 'text': selected, 'text_sha256': sha(selected.encode()), 'lines': lines, 'links': links},
            {'mode': mode, 'policy': result['policy'], 'raw_sources': result['raw_sources'],
             'table_source_refs': [t['source_ref'] for t in result.get('tables', [])],
             'rendering': 'existing table selector; AU fallback rendered with existing HTMLParser; selected-scope anchor text and href retained'})


def summary(values):
    a = sorted(values)
    return {'min': a[0], 'median': (a[(len(a)-1)//2] + a[len(a)//2])/2,
            'p90_nearest_rank': a[max(0, (9*len(a)+9)//10-1)], 'max': a[-1], 'sum': sum(a)}


def build(output):
    output.mkdir(parents=True, exist_ok=False)
    for name in ['inputs', 'packets', 'sources', 'source_records', 'schemas', 'scripts']:
        (output / name).mkdir()
    products_out, provenance, selected_products, selected_cases, exclusions = [], [], [], [], []
    aliases, input_pins, sizes = {}, [], []
    for split in ['TRAINdict', 'EVALtest']:
        source = INPUTS / split / 'inputs'
        for name in ['products', 'cases']:
            path = source / f'{name}.jsonl'
            input_pins.append({'path': str(path.relative_to(ROOT)), 'sha256': sha(path.read_bytes())})
        products, cases = AUDIT.jsonl(source / 'products.jsonl'), AUDIT.jsonl(source / 'cases.jsonl')
        grouped = defaultdict(list)
        for case in cases:
            grouped[case['dossier_id']].append(case)
        for product in products:
            pid = product['dossier_id']
            rows = sorted(grouped[pid], key=lambda c: (c['rakuten_selected']['source_row_index'], c['case_id']))
            rak_audit, au_audit = AUDIT.audit_rakuten(product, rows), AUDIT.audit_au(product)
            complete = (rak_audit['sha256_match'] and au_audit['sha256_match']
                        and not rak_audit['additional_native_variants']
                        and not rak_audit['case_ids_axes_mismatch']
                        and not rak_audit['case_ids_variant_absent']
                        and rak_audit['native_variant_ids'] == len(rows)
                        and not rak_audit['variant_id_selector_value_conflicts']
                        and au_audit['all_native_cells_match_provided_rows'])
            if not complete:
                exclusions.append({'dossier_id': pid, 'split': split, 'rakuten': rak_audit, 'au': au_audit})
                continue
            refs = {}
            for side in ['rakuten', 'au']:
                p = product[f'{side}_product']
                original = p['raw_file'].replace('\\', '/')
                raw_path = ROOT / original
                raw = raw_path.read_bytes()
                digest = sha(raw)
                assert digest == p['sha256']
                blob = f'sources/{digest}{raw_path.suffix.lower()}'
                if not (output / blob).exists():
                    with (output / blob).open('xb') as f:
                        f.write(raw)
                refs[side] = {'sha256': digest, 'blob_path': blob, 'original_path': original}
                aliases[original] = refs[side]
            rp, ap = product['rakuten_product'], product['au_product']
            rbody, rbody_proof = body('rakuten', rp, refs['rakuten'], output)
            abody, abody_proof = body('au', ap, refs['au'], output)
            raxes = [{'axis_index': i, 'name': a.get('label') or a.get('key') or '',
                      'values': a.get('values', [])} for i, a in enumerate(rp['selector_families'])]
            rskus = [{'sku_key': c['rakuten_selected']['source_row_key'],
                      'variant_id': c['rakuten_selected']['variant_id'],
                      'native_row_index': c['rakuten_selected']['source_row_index'],
                      'conditions': [{'axis_index': i, 'name': a['axis_label'], 'value': a['value']}
                                     for i, a in enumerate(c['rakuten_selected']['axes'])],
                      'auxiliary_attributes': [{'name': a['title'], 'value': a['value'], 'unit': a.get('unit')}
                                               for a in c['rakuten_selected'].get('variant_attributes', [])]}
                     for c in rows]
            askus = [{'sku_key': a['row_key'], 'native_row_index': a['row_index'], 'native_column_index': a['column_index'],
                      'conditions': [{'axis_index': i, 'name': x['axis_name'], 'value': x['value']}
                                     for i, x in enumerate(a['axes'])]}
                     for a in sorted(product['au_rows'], key=lambda a: (a['row_index'], a['column_index']))]
            aaxes = []
            for row in askus:
                for axis in row['conditions']:
                    while len(aaxes) <= axis['axis_index']:
                        aaxes.append({'axis_index': len(aaxes), 'name': axis['name'], 'values': []})
                    dest = aaxes[axis['axis_index']]
                    assert dest['name'] == axis['name']
                    if axis['value'] not in dest['values']:
                        dest['values'].append(axis['value'])
            au_info = json.loads((output / refs['au']['blob_path']).read_text())['itemInfo']
            unselected = [x['text'] for x in product.get('au_purchase_option_lines', []) if isinstance(x.get('text'), str)]
            record = {'schema_version': VERSION, 'product_pair_id': pid,
                      'rakuten': {'title': rp['title'], 'url': rp['url'], 'sku_axes': raxes,
                                  'skus': rskus, 'description': rbody},
                      'au': {'product_id': str(ap['product_id']), 'title': ap['title'],
                             'url': f'https://wowma.jp/item/{ap["product_id"]}', 'sku_axes': aaxes,
                             'skus': askus, 'description': abody,
                             'non_sku_options': {'has_paid_options': au_info.get('hasPaidOptions'),
                                                 'has_free_options': au_info.get('hasFreeOptions'),
                                                 'observed_unselected_text': unselected,
                                                 'complete_value_lists': None, 'current_selected_values': None}}}
            products_out.append(record)
            selected_products.append({'original_split': split, 'product': product})
            selected_cases.extend({'original_split': split, 'case': c} for c in rows)
            provenance.append({'product_pair_id': pid, 'original_split': split,
                               'collection_bucket': Path(refs['rakuten']['original_path']).parts[2],
                               'pair_identity_status': product.get('pair_status') or rows[0].get('pair_status', 'unverified'),
                               'pair_identity_human_verified': False,
                               'case_id_by_rakuten_sku_key': {c['rakuten_selected']['source_row_key']: c['case_id'] for c in rows},
                               'raw_sources': refs, 'description_selection': {'rakuten': rbody_proof, 'au': abody_proof},
                               'native_coverage': {'rakuten': rak_audit, 'au': au_audit}})
    products_out.sort(key=lambda p: p['product_pair_id'])
    provenance.sort(key=lambda p: p['product_pair_id'])
    assert len(products_out) == 152 and sum(len(p['rakuten']['skus']) for p in products_out) == 1934
    assert sum(len(p['au']['skus']) for p in products_out) == 1600
    assert len({p['product_pair_id'] for p in products_out}) == len(products_out)
    for p in products_out:
        dump(output / 'packets' / (p['product_pair_id']+'.json'), p)
        sizes.append(len(json.dumps(p, ensure_ascii=False, separators=(',', ':')).encode()))
    jsonl(output / 'inputs/products.jsonl', products_out)
    jsonl(output / 'source_records/provenance.jsonl', provenance)
    jsonl(output / 'source_records/original_products.jsonl', selected_products)
    jsonl(output / 'source_records/original_cases.jsonl', selected_cases)
    dump(output / 'source_records/raw_aliases.json', aliases)
    dump(output / 'source_records/input_pins.json', input_pins)
    dump(output / 'source_records/exclusions.json', exclusions)
    largest = sorted(products_out, key=lambda p: (len(p['rakuten']['skus'])*len(p['au']['skus']), p['product_pair_id']), reverse=True)
    hash_order = sorted(products_out, key=lambda p: sha(('smoke-v1:'+p['product_pair_id']).encode()))
    dump(output / 'subsets.json', {'smoke_10': [p['product_pair_id'] for p in hash_order[:10]],
                                 'normal_50': [p['product_pair_id'] for p in hash_order[:50]],
                                 'all_152': [p['product_pair_id'] for p in products_out],
                                 'largest_20': [p['product_pair_id'] for p in largest[:20]]})
    nr = [len(p['rakuten']['skus']) for p in products_out]
    na = [len(p['au']['skus']) for p in products_out]
    profile = {'schema': VERSION, 'product_pairs': len(products_out),
               'rakuten_SKUs': sum(nr), 'au_SKUs': sum(na),
               'candidate_row_pairs': sum(a*b for a,b in zip(nr,na)),
               'rakuten_SKUs_per_product': summary(nr), 'au_SKUs_per_product': summary(na),
               'input_bytes_per_product': summary(sizes),
               'original_split_counts': dict(Counter(x['original_split'] for x in provenance)),
               'collection_bucket_not_product_category_counts': dict(Counter(x['collection_bucket'] for x in provenance)),
               'description_modes': {side: dict(Counter(p[side]['description']['mode'] for p in products_out)) for side in ['rakuten','au']},
               'unequal_axis_count_products': sum(len(p['rakuten']['sku_axes']) != len(p['au']['sku_axes']) for p in products_out),
               'raw_blob_count': len(list((output/'sources').iterdir())),
               'labels_included': False, 'model_predictions_included': False, 'synthetic_SKUs': 0,
               'completeness': 'every distinct captured native Rakuten variant and every AU stockList dictionary cell, including out-of-stock rows',
               'independent_accuracy_benchmark': False, 'current_website_state_guaranteed': False,
               'models_executed': 0, 'new_network_fetches': 0}
    dump(output / 'PROFILE.json', profile)
    with (output / 'products.csv').open('x', newline='', encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['product_pair_id','rakuten_title','au_title','rakuten_SKUs','au_SKUs','candidate_row_pairs','rakuten_description_mode','au_description_mode'])
        for p in products_out:
            r,a=p['rakuten'],p['au'];w.writerow([p['product_pair_id'],r['title'],a['title'],len(r['skus']),len(a['skus']),len(r['skus'])*len(a['skus']),r['description']['mode'],a['description']['mode']])
    for name in ['build_product_volume_normal_v1.py','verify_product_volume_normal_v1.py','audit_product_sku_sources_v1.py','sku_spec_table_first_v1.py','build_scoped_html_blocks_v1.py']:
        shutil.copyfile(Path(__file__).parent/name, output/'scripts'/name)
    return profile


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=DEFAULT)
    print(json.dumps(build(p.parse_args().output),ensure_ascii=False,indent=2))
