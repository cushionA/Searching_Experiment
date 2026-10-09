"""Freeze independently produced Luna annotation shards without replacing sources."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_luna_sku_labels import read_jsonl, validate_label_records

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def freeze(inputs: Path, label_dir: Path) -> dict:
    final = label_dir / 'labels.jsonl'
    manifest_path = label_dir / 'manifest.json'
    if final.exists() or manifest_path.exists():
        raise FileExistsError('Frozen labels or manifest already exist')
    input_manifest_path = inputs / 'manifest.json'
    input_manifest = json.loads(input_manifest_path.read_text())
    for name, expected in input_manifest['output_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Input hash mismatch: {name}')
    for name, expected in input_manifest['dossier_sha256'].items():
        if sha(inputs / name) != expected:
            raise ValueError(f'Dossier hash mismatch: {name}')
    cases = read_jsonl(inputs / 'cases.jsonl')
    case_by_id = {case['case_id']: case for case in cases}
    shard_paths = [label_dir / f'shard-{n}-labels.jsonl' for n in (1, 2, 3)]
    review_paths = [label_dir / f'shard-{n}-review.json' for n in (1, 2, 3)]
    source_hashes = {path.name: sha(path) for path in shard_paths + review_paths}
    labels = []
    for n, path in enumerate(shard_paths, 1):
        rows = read_jsonl(path)
        actual = [row['case_id'] for row in rows]
        expected = {case['case_id'] for case in cases if case['shard_id'] == f'shard-{n}'}
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError(f'Incomplete or duplicated annotation shard: {path.name}')
        labels.extend(rows)
    if len(labels) != len(cases) or len({r['case_id'] for r in labels}) != len(cases):
        raise ValueError('Labels must cover the entire input exactly once')
    dossiers = {
        path.stem: json.loads(path.read_text())
        for path in (inputs / 'dossiers').glob('*.json')
    }
    validation = validate_label_records(
        cases, dossiers, labels,
        json.loads((inputs / 'group_manifest.json').read_text()), input_manifest)
    if validation['errors']:
        raise ValueError(f'Annotation validation failed: {validation["errors"][:3]}')
    labels.sort(key=lambda row: row['case_id'])
    payload = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')) + '\n' for row in labels).encode()
    decision_counts = Counter(row['decision'] for row in labels)
    splits = defaultdict(Counter)
    for row in labels:
        splits[case_by_id[row['case_id']]['split']][row['decision']] += 1
    protocol = {
        'model': 'gpt-6-luna',
        'kind': 'source interpretations by three Luna agents, expanded to per-SKU labels',
        'independent_luna_second_review': True,
        'human_verified': False,
        'independent_external_gold': False,
        'existing_model_predictions_used': False,
        'synthetic_data_included': False,
        'semantic_identity_separate_from_availability': True,
        'price_grain': 'AU product page price; Rakuten price belongs to each SKU record',
    }
    corrections = label_dir / 'shard-2-audit-corrections.jsonl'
    if corrections.is_file():
        source_hashes[corrections.name] = sha(corrections)
    manifest = {
        'schema_version': 'luna-sku-label-manifest-v1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'input_dir': str(inputs.resolve()),
        'input_manifest_sha256': sha(input_manifest_path),
        'cases_sha256': sha(inputs / 'cases.jsonl'),
        'case_count': len(labels),
        'decision_counts': dict(decision_counts),
        'split_decision_counts': {key: dict(value) for key, value in splits.items()},
        'family_group_count': len({case['group_id'] for case in cases}),
        'unique_rakuten_source_skus': len({case['rakuten']['source']['sku_record_key'] for case in cases}),
        'annotation_protocol': protocol,
        'output_sha256': {'labels.jsonl': hashlib.sha256(payload).hexdigest(), **source_hashes},
        'annotation_code_sha256': {
            name: sha(Path(__file__).parent / name)
            for name in ('annotate_luna_curtains.py', 'annotate_luna_blankets.py',
                         'annotate_luna_other.py', 'merge_luna_labels.py')
        },
        'operational_history': (
            'An annotator removed the shared annotation output directory before freeze. '
            'Shard 3 was regenerated from unchanged v2 sources; the missing first output '
            'hash is unavailable. Correction histories are retained in shard reviews. '
            'Raw captures and annotation input snapshots were preserved.'),
    }
    if any(sha(label_dir / name) != expected for name, expected in source_hashes.items()):
        raise RuntimeError('Annotation shard changed during merge')
    with final.open('xb') as target:
        target.write(payload)
    with manifest_path.open('x', encoding='utf-8') as target:
        target.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs-dir', type=Path, required=True)
    parser.add_argument('--label-dir', type=Path, required=True)
    args = parser.parse_args()
    manifest = freeze(args.inputs_dir, args.label_dir)
    print(json.dumps({key: manifest[key] for key in
                      ('case_count', 'decision_counts', 'split_decision_counts',
                       'unique_rakuten_source_skus')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
