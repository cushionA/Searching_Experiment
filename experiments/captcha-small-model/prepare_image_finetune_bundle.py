#!/usr/bin/env python3
"""Bundle verified public evaluation images and frozen grouped split metadata.

Only inference/training image references, labels, split assignments and bounded
public provenance are included. Source archives and environment credentials are
never inputs to this packager.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import prepare_public_bundle as packager


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prefix', default='grouped')
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    if not args.prefix or Path(args.prefix).name != args.prefix or args.prefix in ('.', '..'):
        raise ValueError('prefix must be one filename component')
    parent_hash = sha(manifest)
    root = manifest.parent
    split_report = root / f'{args.prefix}-splits.json'
    splits = json.loads(split_report.read_text())
    if splits['parent_manifest_sha256'] != parent_hash:
        raise ValueError('Split parent-manifest hash mismatch')
    extra = {split_report.name: split_report.read_bytes()}
    for fold, identity in splits['split_manifests'].items():
        path = root / identity['path']
        if path.parent != root or sha(path) != identity['sha256']:
            raise ValueError(f'Invalid split manifest: {fold}')
        doc = json.loads(path.read_text())
        if doc['split']['parent_manifest_sha256'] != parent_hash or doc['split']['fold'] != fold:
            raise ValueError(f'Split manifest identity mismatch: {fold}')
        extra[path.name] = path.read_bytes()
    restoration_path = root / 'restoration.json'
    if restoration_path.exists():
        restored = json.loads(restoration_path.read_text())
        if restored['manifest_sha256'] != parent_hash:
            raise ValueError('Restoration parent-manifest hash mismatch')
        provenance = {key: restored[key] for key in ('manifest_sha256', 'counts', 'matches_historical_counts',
                      'historical_per_image_identity', 'historical_role', 'collector_sha256', 'restorer_sha256')}
        provenance['sources'] = [{key: source[key] for key in ('repo', 'commit', 'git_tree_sha', 'tree_input_sha256')}
                                | {'archive': {key: source['archive'][key] for key in ('url', 'bytes', 'sha256')}}
                                for source in restored['sources']]
        extra['source_provenance.json'] = (json.dumps(provenance, indent=2) + '\n').encode()
        # Exact filenames are derived from the two fixed public repos, never env.
        for source in provenance['sources']:
            for name in ('README.md', 'LICENSE'):
                path = root / 'source_metadata' / f"{source['repo'].replace('/', '_')}-{name}"
                if path.exists():
                    extra[f'source_metadata/{path.name}'] = path.read_bytes()
    sys.argv = [str(Path(packager.__file__)), '--manifest', str(manifest), '--output', str(args.output)]
    packager.main()
    with zipfile.ZipFile(args.output, 'a', compression=zipfile.ZIP_STORED) as bundle:
        for name, raw in sorted(extra.items()):
            bundle.writestr(name, raw)
    receipt_path = args.output.with_suffix('.verification.json')
    receipt = json.loads(receipt_path.read_text())
    receipt.update({'bundle_sha256': sha(args.output), 'bundle_bytes': args.output.stat().st_size,
                    'frozen_split_report_sha256': sha(split_report),
                    'extra_files_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(extra.items())},
                    'includes_source_archives': False, 'includes_credentials': False,
                    'contains_frozen_train_validation_test_splits': True})
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2), flush=True)


if __name__ == '__main__':
    main()
