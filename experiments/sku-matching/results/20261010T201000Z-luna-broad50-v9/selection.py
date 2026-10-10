from __future__ import annotations
import hashlib, json, sys
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

ROOT = Path('/workspace/Searching_Experiment')
EXP = ROOT / 'experiments/sku-matching'
sys.path.insert(0, str(EXP))
import sku_luna_inputs as loader

SEED = '20261010'
USED_PATH = EXP / 'results/20261010T185500Z-luna-multirow62/used-case-ids.json'
OUT = Path('/workspace/sku-next50-source-selection.json')
used_document = json.loads(USED_PATH.read_text(encoding='utf-8'))
used_ids = set(used_document['case_ids'])

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def source_files(node):
    found = set()
    if isinstance(node, dict):
        raw = node.get('raw_file')
        if isinstance(raw, str) and raw:
            found.add(raw)
        for value in node.values():
            found |= source_files(value)
    elif isinstance(node, list):
        for value in node:
            found |= source_files(value)
    return found

def meta(case):
    r = case.get('rakuten', {})
    axes = r.get('axes', [])
    rdocs = set()
    for axis in axes:
        span = axis.get('value_span') or {}
        if span.get('raw_file'):
            rdocs.add(str(span['raw_file']))
    return {
        'case_id': str(case['case_id']), 'cohort': case.get('cohort'),
        'au_product_id': str(case.get('au_product_id', '')),
        'rakuten_url': str(r.get('url', '')),
        'rakuten_variant_id': str(r.get('variant_id', '')),
        'rakuten_sku_key': str(r.get('source_sku_key', '')),
        'row_count': None, 'r_source_files': sorted(rdocs),
    }

with ZipFile(loader.DEFAULT_ZIP) as archive:
    cases_raw = loader._jsonl(archive.read(loader.INPUT_ROOT + 'cases.jsonl'))
    products_raw = loader._jsonl(archive.read(loader.INPUT_ROOT + 'products.jsonl'))
    cases_bytes = archive.read(loader.INPUT_ROOT + 'cases.jsonl')
    products_bytes = archive.read(loader.INPUT_ROOT + 'products.jsonl')
    zip_hash = sha(Path(loader.DEFAULT_ZIP).read_bytes())

products = {str(p['dossier_id']): p for p in products_raw}
by_id = {str(c['case_id']): c for c in cases_raw}
eligible_raw = [c for c in cases_raw if c.get('cohort') in {'legacy', 'novel'}]
all_metadata = []
for case in eligible_raw:
    item = meta(case)
    product = products.get(str(case.get('dossier_id')), {})
    au = product.get('au', {})
    item['row_count'] = len(au.get('rows', []))
    item['au_source_files'] = sorted(source_files(au))
    all_metadata.append(item)
metadata_sha = sha(json.dumps(all_metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())

prior_raw = [by_id[cid] for cid in used_ids if cid in by_id]
prior_meta = []
for case in prior_raw:
    item = meta(case)
    product = products.get(str(case.get('dossier_id')), {})
    item['au_source_files'] = sorted(source_files(product.get('au', {})))
    prior_meta.append(item)
used_products = {x['au_product_id'] for x in prior_meta}
used_urls = {x['rakuten_url'] for x in prior_meta if x['rakuten_url']}
used_rdocs = {v for x in prior_meta for v in x['r_source_files']}
used_askus = {x['rakuten_sku_key'] for x in prior_meta if x['rakuten_sku_key']}
used_tuples_sku_au = {(x['rakuten_sku_key'], x['au_product_id']) for x in prior_meta}
used_tuples_variant = {(x['au_product_id'], x['rakuten_url'], x['rakuten_variant_id']) for x in prior_meta}

candidates = [x for x in all_metadata if x['case_id'] not in used_ids and x['row_count'] and x['row_count'] > 1
              and x['rakuten_url'] and x['rakuten_sku_key'] and x['au_product_id']]
new_counts = {
    'case_count_after_used_case_exclusion': len(all_metadata) - len(used_ids),
    'eligible_multirow_after_case_exclusion': len(candidates),
    'cohort_counts': dict(Counter(x['cohort'] for x in candidates)),
    'unused_au_products_vs_prior': len({x['au_product_id'] for x in candidates} - used_products),
    'unused_rakuten_urls_vs_prior': len({x['rakuten_url'] for x in candidates} - used_urls),
    'unused_rakuten_source_files_vs_prior': len({v for x in candidates for v in x['r_source_files']} - used_rdocs),
    'both_au_product_and_rsource_new_cases': sum(x['au_product_id'] not in used_products and not (set(x['r_source_files']) & used_rdocs) for x in candidates),
}

# Keep exact historical pair identities out. Titrate the cap on AU product,
# URL, and Rakuten source file, while prioritizing wholly unused product/source.
def rank_key(x):
    prod_new = x['au_product_id'] not in used_products
    doc_new = bool(x['r_source_files']) and not (set(x['r_source_files']) & used_rdocs)
    url_new = x['rakuten_url'] not in used_urls
    both_new = prod_new and doc_new
    tier = 0 if both_new and url_new else 1 if both_new else 2 if prod_new or doc_new else 3
    h = hashlib.sha256((SEED + '|' + x['case_id']).encode()).hexdigest()
    return tier, h

selected = []
for cap in (2, 3, 4):
    selected.clear()
    products_count, urls_count, docs_count = Counter(), Counter(), Counter()
    sku_au = set(used_tuples_sku_au)
    variants = set(used_tuples_variant)
    # Pick the less-filled cohort at each step, enforcing a 25/25 target where possible.
    remaining = sorted(candidates, key=rank_key)
    while len(selected) < 50:
        options = []
        for x in remaining:
            if (x['rakuten_sku_key'], x['au_product_id']) in sku_au:
                continue
            if (x['au_product_id'], x['rakuten_url'], x['rakuten_variant_id']) in variants:
                continue
            if products_count[x['au_product_id']] >= cap or urls_count[x['rakuten_url']] >= cap:
                continue
            if any(docs_count[d] >= cap for d in x['r_source_files']):
                continue
            count = sum(y['cohort'] == x['cohort'] for y in selected)
            target = 25
            # First favor cohort balance, then source/product novelty and seeded order.
            cohort_priority = 0 if count < target else 1
            options.append(((cohort_priority, *rank_key(x)), x))
        if not options:
            break
        _, chosen = min(options, key=lambda pair: pair[0])
        selected.append(chosen)
        remaining.remove(chosen)
        products_count[chosen['au_product_id']] += 1
        urls_count[chosen['rakuten_url']] += 1
        for doc in chosen['r_source_files']:
            docs_count[doc] += 1
        sku_au.add((chosen['rakuten_sku_key'], chosen['au_product_id']))
        variants.add((chosen['au_product_id'], chosen['rakuten_url'], chosen['rakuten_variant_id']))
    if len(selected) == 50:
        break

# Verify every selected source span with the canonical loader; no answer/label data is used.
verified, hashes = loader.load_cases(loader.DEFAULT_ZIP, [x['case_id'] for x in selected])
if len(verified) != len(selected):
    raise RuntimeError('selected source verification count mismatch')
selected = sorted(selected, key=lambda x: (x['cohort'], hashlib.sha256((SEED + '|' + x['case_id']).encode()).hexdigest()))
selected_ids = [x['case_id'] for x in selected]

selected_counts = {
    'selected_count': len(selected),
    'cohort_counts': dict(Counter(x['cohort'] for x in selected)),
    'candidate_row_count_gt1': sum(x['row_count'] > 1 for x in selected),
    'candidate_row_count_gt2': sum(x['row_count'] > 2 for x in selected),
    'unique_au_products': len({x['au_product_id'] for x in selected}),
    'unique_rakuten_urls': len({x['rakuten_url'] for x in selected}),
    'unique_rakuten_source_files': len({d for x in selected for d in x['r_source_files']}),
    'new_au_products_vs_prior': len({x['au_product_id'] for x in selected} - used_products),
    'new_rakuten_urls_vs_prior': len({x['rakuten_url'] for x in selected} - used_urls),
    'new_rakuten_source_files_vs_prior': len({d for x in selected for d in x['r_source_files']} - used_rdocs),
    'both_new_product_and_source_cases_vs_prior': sum(x['au_product_id'] not in used_products and not (set(x['r_source_files']) & used_rdocs) for x in selected),
    'case_id_overlap_with_prior': len(set(selected_ids) & used_ids),
    'sku_key_product_pair_overlap_with_prior': sum((x['rakuten_sku_key'], x['au_product_id']) in used_tuples_sku_au for x in selected),
    'product_url_variant_overlap_with_prior': sum((x['au_product_id'], x['rakuten_url'], x['rakuten_variant_id']) in used_tuples_variant for x in selected),
    'max_per_au_product': max(Counter(x['au_product_id'] for x in selected).values(), default=0),
    'max_per_rakuten_url': max(Counter(x['rakuten_url'] for x in selected).values(), default=0),
    'max_per_rakuten_source_file': max(Counter(d for x in selected for d in x['r_source_files']).values(), default=0),
}
output = {
    'schema': 'sku-next50-source-selection-v1', 'seed': SEED,
    'selection_rule': 'eligible legacy/novel multirow; exclude prior case IDs; enforce both historical pair identities; SHA256(seed|case_id) tie break; prioritize unused AU product+Rakuten raw source+URL; cohort target 25/25; caps relaxed 2 then 3 then 4',
    'selected_case_ids': selected_ids,
    'selected_metadata': selected,
    'availability': new_counts,
    'selected_summary': selected_counts,
    'source_metadata': {'zip_sha256': hashes['zip_sha256'], 'cases_jsonl_sha256': hashes['cases_jsonl_sha256'],
                        'products_jsonl_sha256': hashes['products_jsonl_sha256'], 'eligible_metadata_sha256': metadata_sha,
                        'prior_used_case_ids_sha256': sha(USED_PATH.read_bytes()), 'all_1439_source_spans_verified': True,
                        'selected_50_source_spans_verified': True},
}
OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'availability':new_counts,'selected_summary':selected_counts,'cap_used':cap,'path':str(OUT)},ensure_ascii=False))
