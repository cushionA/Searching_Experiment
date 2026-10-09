"""Export source-only SKU records and separate Luna annotations for review."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.open(encoding='utf-8') if line.strip()]


def write_rows(path, rows):
    with path.open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


def export(output):
    if output.exists():
        raise FileExistsError(output)
    inputs = ROOT / '.lab-output/sku-real-luna-annotation-inputs-20261010-v2'
    labels_dir = ROOT / '.lab-output/sku-real-luna-labels-20261010-v1'
    audit_dir = ROOT / '.lab-output/sku-real-luna-label-audit-20261010-final-v1'
    array_path = ROOT / '.lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl'
    labels_manifest = json.loads((labels_dir / 'manifest.json').read_text())
    input_manifest = json.loads((inputs / 'manifest.json').read_text())
    audit = json.loads((audit_dir / 'audit.json').read_text())
    if audit['errors']:
        raise ValueError('Only labels with a passed source audit may be exported')
    for name, expected in labels_manifest['output_sha256'].items():
        if sha(labels_dir / name) != expected:
            raise ValueError(f'Frozen label source changed: {name}')
    for name, expected in input_manifest['output_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Frozen input changed: {name}')
    cases = read_rows(inputs / 'cases.jsonl')
    labels = {row['case_id']: row for row in read_rows(labels_dir / 'labels.jsonl')}
    eligibility = {row['case_id']: row for row in read_rows(inputs / 'eligibility.jsonl')}
    au_eligibility = {row['au_product_id']: row for row in read_rows(inputs / 'au_eligibility.jsonl')}
    dossiers = {path.stem: json.loads(path.read_text()) for path in (inputs / 'dossiers').glob('*.json')}
    for name, expected in input_manifest['dossier_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Dossier changed: {name}')
    used_ids = {d['au_product']['product_id'] for d in dossiers.values()}
    au_arrays = [row for row in read_rows(array_path) if row['au_product_id'] in used_ids]
    if len(cases) != 1383 or len(labels) != len(cases) or len(au_arrays) != 29:
        raise ValueError('Unexpected review dataset dimensions')
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / 'au_products_and_skus.jsonl', au_arrays)
    records = []
    raw_refs = {}
    for case in cases:
        dossier = dossiers[case['dossier_id']]
        au = dossier['au_product']
        annotation = labels[case['case_id']]
        candidate_by_key = {r['row_key']: r for r in dossier['au_rows']}
        matched_rows = [candidate_by_key[key] for key in annotation['matching_au_row_keys']]
        option_labels = {x['key']: x.get('label') or x.get('name') or x['key'] for x in case['rakuten']['axes_labels']}
        raku_sku = ' / '.join(f"{option_labels.get(x['axis_key'], x['axis_key'])}={x['value']}" for x in case['rakuten']['option_values'])
        au_matches = [' / '.join(f"{x['axis_name_raw']}={x['value_raw']}" for x in row['axes_raw']) for row in matched_rows]
        price = eligibility[case['case_id']]['rakuten']['price_jpy']
        if type(price) is not int:
            raise ValueError('Rakuten records require individual integer SKU prices')
        record = {
            'case_id': case['case_id'], 'pair_ref': dossier['pair_ref'],
            'group_id': case['group_id'], 'split': case['split'],
            'decision': annotation['decision'],
            '判定': {'matched': '一致', 'unmatched': '不一致', 'review': '保留'}[annotation['decision']],
            'au_product_id': au['product_id'], 'au_url': f"https://wowma.jp/item/{au['product_id']}",
            'au_title': au['title_raw'],
            'au_product_price_jpy': au_eligibility[au['product_id']]['au_product_price_jpy_once'],
            'au_all_sku_count': len(dossier['au_rows']),
            'matched_au_sku': ' || '.join(au_matches),
            'matching_au_row_keys': annotation['matching_au_row_keys'],
            'rakuten_url': case['rakuten']['source']['url'],
            'rakuten_title': case['rakuten']['title_raw'], 'rakuten_sku': raku_sku,
            'rakuten_price_jpy': price,
            'rakuten_sku_record_key': case['rakuten']['source']['sku_record_key'],
            'rakuten_source_row_index': case['rakuten']['source']['source_row_index'],
            'rationale': annotation['rationale'], 'evidence': annotation['evidence'],
            'rakuten_availability_raw': eligibility[case['case_id']]['rakuten']['availability'],
            'matched_au_stock_raw': [next(r['stock_raw'] for r in au_eligibility[au['product_id']]['rows'] if r['row_key'] == key) for key in annotation['matching_au_row_keys']],
            'annotation_origin': 'gpt-6-luna machine annotations, human unverified',
            'data_origin': 'observed real SKU records; no synthetic variants',
        }
        records.append(record)
        for source in (au['source'], case['rakuten']['source']):
            path = Path(source['raw_file'])
            path = path if path.is_absolute() else ROOT / path
            if not path.is_file() or sha(path) != source['sha256']:
                raise ValueError(f'Raw source missing or changed: {path}')
            raw_refs[path] = source['sha256']
        # Preserve actual purchase-option API bodies alongside the AU item body.
        au_raw = Path(au['source']['raw_file'])
        au_raw = au_raw if au_raw.is_absolute() else ROOT / au_raw
        options = au_raw.with_name(f"{au['product_id']}-options.json")
        if not options.is_file():
            raise ValueError(f'AU options raw missing: {options}')
        raw_refs[options] = sha(options)
    write_rows(output / 'review_skus.jsonl', records)
    csv_columns = ['case_id', '判定', 'decision', 'au_product_id', 'au_url', 'au_title',
                   'au_product_price_jpy', 'au_all_sku_count', 'matched_au_sku',
                   'rakuten_url', 'rakuten_title', 'rakuten_sku', 'rakuten_price_jpy',
                   'rationale', 'evidence', 'matching_au_row_keys', 'rakuten_sku_record_key',
                   'rakuten_source_row_index', 'group_id', 'split',
                   'rakuten_availability_raw', 'matched_au_stock_raw']
    with (output / 'review_skus.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_columns, extrasaction='ignore')
        writer.writeheader()
        for record in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in record.items()})
    readme = '''確認用SKUデータ

review_skus.csv: Excelで確認する表。1行=固定au商品に対する実楽天SKU1件。
review_skus.jsonl: 同じ1,383行を機械で読む表。
au_products_and_skus.jsonl: 29固定au商品と各商品の全実SKU配列。

判定はLunaの機械注釈。一致470 / 不一致911 / 保留2。人手確認は未実施。
同じ楽天SKUを別の固定au商品で判定するため、楽天の一意な原SKUは644件。
auの商品価格はページ単位、楽天価格は各SKU固有価格。
在庫と同一性判定は別。売切SKUも原文保持しており、本表は最終出力CSVではない。
別au URLへの振り分けは行っていない。
原文の選択肢と価格を使った実データだけ。合成SKUや合成の不一致例は含めない。
Luna判定・引用・対応row keyは取得原文と別フィールドに保存している。

review-tables.zip: 表、全注釈入力・ラベル・監査・再現コード。
review-with-evidence.zip: 上記に75原本ファイル（au item/options、楽天HTML）を追加。
ZIPはリポジトリ基準の相対構造を保持する。説明内に元workspaceの絶対パスがある
場合は、展開先の同じ.lab-output相対パスへ読み替える。
'''
    (output / 'README.txt').write_text(readme, encoding='utf-8')
    sources = list(inputs.rglob('*'))
    sources = [p for p in sources if p.is_file()]
    sources += [labels_dir / name for name in labels_manifest['output_sha256']]
    sources += [labels_dir / 'manifest.json']
    sources += [p for p in audit_dir.iterdir() if p.is_file()]
    sources += [ROOT / 'experiments/sku-matching' / name for name in
                ('export_luna_review_data.py', 'build_luna_annotation_inputs.py',
                 'annotate_luna_curtains.py', 'annotate_luna_blankets.py', 'annotate_luna_other.py',
                 'merge_luna_labels.py', 'audit_luna_sku_labels.py', 'fetch_rakuten.py')]
    sources += [output / name for name in ('review_skus.csv', 'review_skus.jsonl', 'au_products_and_skus.jsonl', 'README.txt')]
    sources = sorted(set(sources))
    deliveries = {}
    for name, paths in (('review-tables.zip', sources), ('review-with-evidence.zip', sorted(set(sources) | set(raw_refs)))):
        archive = output / name
        entries = []
        for path in paths:
            if not path.is_relative_to(ROOT) or any('sku-synthetic' in part or 'sku-fixed-product-' in part for part in path.parts):
                raise ValueError(f'Forbidden source path: {path}')
            entries.append({'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': sha(path)})
        provenance = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
                      'case_count': 1383, 'unique_rakuten_skus': 644, 'au_products': 29,
                      'synthetic_data_count': 0, 'human_verified': False, 'payloads': entries}
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path, entry in zip(paths, entries, strict=True):
                bundle.write(path, entry['path'])
                if sha(path) != entry['sha256']:
                    raise RuntimeError(f'Source changed during export: {path}')
            bundle.writestr('REVIEW_MANIFEST.json', json.dumps(provenance, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError('ZIP CRC verification failed')
            for entry in entries:
                data = bundle.read(entry['path'])
                if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise RuntimeError('Archived payload differs from source')
        deliveries[name] = {'bytes': archive.stat().st_size, 'sha256': sha(archive), 'payload_count': len(entries)}
    result = {'case_count': 1383, 'decisions': labels_manifest['decision_counts'],
              'raw_evidence_files': len(raw_refs), 'archives': deliveries}
    with (output / 'delivery-summary.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.output_dir.resolve()), ensure_ascii=False))


if __name__ == '__main__':
    main()
