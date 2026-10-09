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
DEFAULT_INPUTS = ROOT / '.lab-output/sku-real-luna-annotation-inputs-20261010-v3'
DEFAULT_LABELS = ROOT / '.lab-output/sku-real-luna-labels-20261010-v3'
DEFAULT_AUDIT = ROOT / '.lab-output/sku-real-luna-label-audit-20261010-final-v2'
DEFAULT_OUTPUT = ROOT / '.lab-output/sku-luna-review-delivery-20261010-v2'
AU_ARRAYS = ROOT / '.lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.open(encoding='utf-8') if line.strip()]


def write_rows(path, rows):
    with path.open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


def export(output, inputs=DEFAULT_INPUTS, labels_dir=DEFAULT_LABELS, audit_dir=DEFAULT_AUDIT):
    if output.exists():
        raise FileExistsError(output)
    inputs, labels_dir, audit_dir = (Path(x).resolve() for x in (inputs, labels_dir, audit_dir))
    output = Path(output).resolve()
    array_path = AU_ARRAYS
    labels_manifest = json.loads((labels_dir / 'manifest.json').read_text())
    input_manifest = json.loads((inputs / 'manifest.json').read_text())
    audit = json.loads((audit_dir / 'audit.json').read_text())
    if input_manifest.get('schema_version') != 'luna-sku-annotation-manifest-v3':
        raise ValueError('Expected corrected v3 annotation inputs')
    protocol = labels_manifest.get('annotation_protocol', {})
    if (protocol.get('model') != 'gpt-6-luna' or protocol.get('human_verified') is not False
            or protocol.get('independent_external_gold') is not False or protocol.get('revision_is_gold') is not False
            or protocol.get('synthetic_data_included') is not False or protocol.get('existing_model_predictions_used') is not False):
        raise ValueError('Corrected label protocol must remain machine-only and non-gold')
    if labels_manifest.get('case_count') != 1383 or labels_manifest.get('input_manifest_sha256') != sha(inputs / 'manifest.json'):
        raise ValueError('Corrected labels are not bound to these annotation inputs')
    if audit.get('input_manifest_sha256') != sha(inputs / 'manifest.json') or audit.get('errors'):
        raise ValueError('Only labels with a passed source audit may be exported')
    if audit.get('case_count') != 1383 or audit.get('label_count') != 1383 or audit.get('decision_counts') != labels_manifest.get('decision_counts'):
        raise ValueError('Audit summary does not match corrected labels manifest')
    for name, expected in labels_manifest['output_sha256'].items():
        if sha(labels_dir / name) != expected:
            raise ValueError(f'Frozen label source changed: {name}')
    for name, expected in labels_manifest.get('supplemental_output_sha256', {}).items():
        if sha(labels_dir / name) != expected:
            raise ValueError(f'Frozen supplemental label source changed: {name}')
    for name, expected in input_manifest['output_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Frozen input changed: {name}')
    for name, expected in audit.get('input_files_sha256', {}).items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Audited input changed: {name}')
    label_path = labels_dir / 'labels.jsonl'
    audited_label_hash = next((value for key, value in audit.get('label_files_sha256', {}).items()
                               if Path(key).resolve() == label_path.resolve()), None)
    if audited_label_hash != sha(label_path):
        raise ValueError('Audit source labels do not match corrected label file')
    source_labels = labels_manifest.get('source_labels', {})
    prior_labels_path = Path(source_labels.get('path', ''))
    if not prior_labels_path.is_absolute():
        prior_labels_path = ROOT / prior_labels_path
    prior_labels_manifest = prior_labels_path.parent / 'manifest.json'
    if (not prior_labels_path.is_file() or sha(prior_labels_path) != source_labels.get('sha256')
            or not prior_labels_manifest.is_file() or sha(prior_labels_manifest) != source_labels.get('manifest_sha256')):
        raise ValueError('Prior decision source does not match corrected labels ledger')
    cases = read_rows(inputs / 'cases.jsonl')
    labels = {row['case_id']: row for row in read_rows(label_path)}
    core_labels = {row['case_id']: row for row in read_rows(labels_dir / 'labels-core-attributes.jsonl')}
    prior_labels = {row['case_id']: row for row in read_rows(prior_labels_path)}
    correction_rows = read_rows(labels_dir / 'corrections-ledger.jsonl')
    corrections_by_case = {}
    for row in correction_rows:
        corrections_by_case.setdefault(row['case_id'], []).append(row)
    eligibility = {row['case_id']: row for row in read_rows(inputs / 'eligibility.jsonl')}
    au_eligibility = {row['au_product_id']: row for row in read_rows(inputs / 'au_eligibility.jsonl')}
    dossiers = {path.stem: json.loads(path.read_text()) for path in (inputs / 'dossiers').glob('*.json')}
    for name, expected in input_manifest['dossier_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Dossier changed: {name}')
    used_ids = {d['au_product']['product_id'] for d in dossiers.values()}
    au_arrays = [row for row in read_rows(array_path) if row['au_product_id'] in used_ids]
    case_ids = {row['case_id'] for row in cases}
    if (len(cases) != 1383 or len(labels) != len(cases) or len(core_labels) != len(cases)
            or len(prior_labels) != len(cases) or set(labels) != case_ids or set(core_labels) != case_ids
            or set(prior_labels) != case_ids or len(au_arrays) != 29):
        raise ValueError('Unexpected review dataset dimensions')
    if labels_manifest.get('decision_counts') != {'matched': 435, 'unmatched': 911, 'review': 37}:
        raise ValueError('Unexpected full-identity decision counts')
    if labels_manifest.get('core_attributes_scope', {}).get('decision_counts') != {'matched': 462, 'unmatched': 911, 'review': 10}:
        raise ValueError('Unexpected limited-core-attributes decision counts')
    hold_rows = [row for row in correction_rows if row.get('action') == 'hold_for_external_audit_review']
    finding_counts = {}
    for row in hold_rows:
        finding_counts[row.get('finding_id')] = finding_counts.get(row.get('finding_id'), 0) + 1
    if len(hold_rows) != 35 or finding_counts != {'F02': 4, 'F03': 27, 'F01': 4}:
        raise ValueError(f'Unexpected audit hold ledger: {finding_counts}')
    for case in cases:
        dossier = dossiers.get(case['dossier_id'])
        if dossier is None:
            raise ValueError(f"Missing dossier for {case['case_id']}")
        candidate_keys = {row['row_key'] for row in dossier['au_rows']}
        for label_set, label_name in ((labels, 'full'), (core_labels, 'core')):
            label = label_set[case['case_id']]
            keys = label.get('matching_au_row_keys')
            if label.get('decision') not in {'matched', 'unmatched', 'review'} or not isinstance(keys, list):
                raise ValueError(f'Invalid {label_name} label shape for {case["case_id"]}')
            if (label['decision'] == 'matched') != bool(keys) or set(keys) - candidate_keys:
                raise ValueError(f'Invalid {label_name} row-key linkage for {case["case_id"]}')
    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / 'au_products_and_skus.jsonl', au_arrays)
    records, core_records = [], []
    raw_refs = {}
    for case in cases:
        dossier = dossiers[case['dossier_id']]
        au = dossier['au_product']
        annotation = labels[case['case_id']]
        core_annotation = core_labels[case['case_id']]
        previous_annotation = prior_labels[case['case_id']]
        case_corrections = corrections_by_case.get(case['case_id'], [])
        audit_entries = [row for row in case_corrections if row.get('action') == 'hold_for_external_audit_review']
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
            'audit_finding_ids': sorted({row.get('finding_id') for row in audit_entries if row.get('finding_id')}),
            'audit_source_refs': sorted({e['raw_file'] for row in audit_entries for e in row.get('verified_evidence', []) if e.get('raw_file')}),
            'revision_actions': sorted({row['action'] for row in case_corrections}),
            'previous_decision': previous_annotation['decision'],
            'matching_fields_new_unknown': '',
            'data_origin': 'observed real SKU records; no synthetic variants',
        }
        records.append(record)
        core_matched = [candidate_by_key[key] for key in core_annotation['matching_au_row_keys']]
        core_record = dict(record)
        core_record.update({
            'decision': core_annotation['decision'],
            '判定': {'matched': '一致', 'unmatched': '不一致', 'review': '保留'}[core_annotation['decision']],
            'matched_au_sku': ' || '.join(' / '.join(f"{x['axis_name_raw']}={x['value_raw']}" for x in row['axes_raw'])
                                          for row in core_matched),
            'matching_au_row_keys': core_annotation['matching_au_row_keys'],
            'rationale': core_annotation['rationale'],
            'evidence': core_annotation['evidence'],
            'annotation_origin': 'gpt-6-luna limited five-attribute equality labels, human unverified; not independent product-identity gold',
        })
        core_records.append(core_record)
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
    if len(raw_refs) != 75:
        raise ValueError(f'Expected 75 deduplicated raw source files, found {len(raw_refs)}')
    write_rows(output / 'review_skus.jsonl', records)
    write_rows(output / 'review_skus_core_attributes.jsonl', core_records)
    csv_columns = ['case_id', '判定', 'decision', 'au_product_id', 'au_url', 'au_title',
                   'au_product_price_jpy', 'au_all_sku_count', 'matched_au_sku',
                   'rakuten_url', 'rakuten_title', 'rakuten_sku', 'rakuten_price_jpy',
                   'rationale', 'evidence', 'matching_au_row_keys', 'rakuten_sku_record_key',
                   'rakuten_source_row_index', 'group_id', 'split',
                   'rakuten_availability_raw', 'matched_au_stock_raw', 'annotation_origin',
                   'audit_finding_ids', 'audit_source_refs', 'revision_actions', 'previous_decision',
                   'matching_fields_new_unknown', 'data_origin']
    for filename, rows in (('review_skus.csv', records), ('review_skus_core_attributes.csv', core_records)):
        with (output / filename).open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=csv_columns, extrasaction='ignore')
            writer.writeheader()
            for record in rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in record.items()})
    for filename, rows in (('review_skus.csv', records), ('review_skus_core_attributes.csv', core_records)):
        with (output / filename).open(encoding='utf-8-sig', newline='') as stream:
            exported_rows = list(csv.DictReader(stream))
        if len(exported_rows) != len(rows) or [r['case_id'] for r in exported_rows] != [r['case_id'] for r in rows]:
            raise RuntimeError(f'CSV row linkage validation failed: {filename}')
        for exported, source in zip(exported_rows, rows, strict=True):
            if json.loads(exported['matching_au_row_keys']) != source['matching_au_row_keys']:
                raise RuntimeError(f'CSV matching-row linkage changed: {filename}:{source["case_id"]}')
    previous_ledger = [{"case_id": case_id, "previous_decision": row["decision"],
                        "previous_matching_au_row_keys": row["matching_au_row_keys"],
                        "source_labels_sha256": source_labels["sha256"], "human_verified": False}
                       for case_id, row in sorted(prior_labels.items())]
    write_rows(output / 'prior_decision_ledger.jsonl', previous_ledger)
    readme = f'''確認用SKUデータ

review_skus.csv: Excelで確認する表。1行=固定au商品に対する実楽天SKU1件。
review_skus.jsonl: 同じ1,383行を機械で読む表。
review_skus_core_attributes.csv / .jsonl: 限定5属性の一致ラベル。付属品数などを対象外にした範囲限定であり、商品同一性goldではない。
au_products_and_skus.jsonl: 29固定au商品と各商品の全実SKU配列。
prior_decision_ledger.jsonl: 以前の機械判定を別添。現在のレビュー判定と混ぜない。

主判定は外部監査提案を反映したLunaの機械注釈。一致435 / 不一致911 / 保留37。人手確認・独立gold確認は未実施。
限定5属性は一致462 / 不一致911 / 保留10。F03の付属品数差はこのscopeでは対象外。
監査提案により保留へ変更した35件はF01=4、F02=4、F03=27。CSVに監査ID、出典、変更action、以前のdecisionを記録。
F06の層数表記など、未解決リスク54件は追加注記の対象であり判定を変更していない。
同じ楽天SKUを別の固定au商品で判定するため、楽天の一意な原SKUは644件。
auの商品価格はページ単位、楽天価格は各SKU固有価格。
在庫と同一性判定は別。売切SKUも原文保持しており、本表は最終出力CSVではない。
別au URLへの振り分けは行っていない。
原文の選択肢と価格を使った実データだけ。合成SKUや合成の不一致例は含めない。
Luna判定・引用・対応row keyは取得原文と別フィールドに保存している。
human_verified=false。監査修正は確認済みgoldではない。

review-tables.zip: 表、v3注釈入力・ラベル・監査・訂正台帳・再現コード。
review-with-evidence.zip: 上記に{len(raw_refs)}原本ファイル（au item/options、楽天HTML）を追加。
ZIPはリポジトリ基準の相対構造を保持する。説明内に元workspaceの絶対パスがある
場合は、展開先の同じ.lab-output相対パスへ読み替える。
'''
    (output / 'README.txt').write_text(readme, encoding='utf-8')
    sources = list(inputs.rglob('*'))
    sources = [p for p in sources if p.is_file()]
    sources += [labels_dir / name for name in labels_manifest['output_sha256']]
    sources += [labels_dir / name for name in labels_manifest.get('supplemental_output_sha256', {})]
    sources += [labels_dir / 'manifest.json']
    sources += [p for p in audit_dir.iterdir() if p.is_file()]
    sources += [ROOT / 'experiments/sku-matching' / name for name in
                ('export_luna_review_data.py', 'build_luna_annotation_inputs.py',
                 'revise_labels_from_external_audit.py', 'annotate_luna_curtains.py',
                 'annotate_luna_blankets.py', 'annotate_luna_other.py', 'merge_luna_labels.py',
                 'audit_luna_sku_labels.py', 'fetch_rakuten.py')]
    sources += [output / name for name in (
        'review_skus.csv', 'review_skus.jsonl', 'review_skus_core_attributes.csv',
        'review_skus_core_attributes.jsonl', 'prior_decision_ledger.jsonl',
        'au_products_and_skus.jsonl', 'README.txt')]
    sources = sorted(set(sources))
    source_snapshot = {path: sha(path) for path in set(sources) | set(raw_refs)}
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
                      'decisions': labels_manifest['decision_counts'],
                      'core_attributes_decisions': labels_manifest['core_attributes_scope']['decision_counts'],
                      'synthetic_data_count': 0, 'human_verified': False,
                      'independent_external_gold': False, 'revision_is_gold': False,
                      'payloads': entries}
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
    if any(sha(path) != digest for path, digest in source_snapshot.items()):
        raise RuntimeError('An export source changed during packaging')
    result = {'case_count': len(records), 'unique_rakuten_skus': len({r['rakuten_sku_record_key'] for r in records}),
              'au_products': len(au_arrays), 'decisions': labels_manifest['decision_counts'],
              'core_attributes_decisions': labels_manifest['core_attributes_scope']['decision_counts'],
              'audit_hold_count': len(hold_rows), 'raw_evidence_files': len(raw_refs),
              'input_manifest_sha256': sha(inputs / 'manifest.json'),
              'labels_sha256': sha(label_path), 'audit_sha256': sha(audit_dir / 'audit.json'),
              'csv': {'path': str((output / 'review_skus.csv').relative_to(ROOT)),
                      'bytes': (output / 'review_skus.csv').stat().st_size,
                      'sha256': sha(output / 'review_skus.csv'), 'rows': len(records)},
              'core_attributes_csv': {'path': str((output / 'review_skus_core_attributes.csv').relative_to(ROOT)),
                                      'bytes': (output / 'review_skus_core_attributes.csv').stat().st_size,
                                      'sha256': sha(output / 'review_skus_core_attributes.csv'), 'rows': len(core_records)},
              'archives': deliveries}
    with (output / 'delivery-summary.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs-dir', type=Path, default=DEFAULT_INPUTS)
    parser.add_argument('--labels-dir', type=Path, default=DEFAULT_LABELS)
    parser.add_argument('--audit-dir', type=Path, default=DEFAULT_AUDIT)
    parser.add_argument('--output', '--output-dir', dest='output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(export(args.output, args.inputs_dir, args.labels_dir, args.audit_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
