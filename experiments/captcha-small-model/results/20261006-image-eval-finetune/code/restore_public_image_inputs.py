#!/usr/bin/env python3
"""Restore fixed public image inputs offline, retaining the historical collector.

Tree/archive acquisition is deliberately separate. The supplied immutable Git
snapshots and archive receipts are verified before extraction. This runner uses
collect_public_samples' original manifest construction without resolving HEAD.
It does not claim identity with an unavailable historical per-image manifest.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import collect_public_samples as collector

SOURCES = {
    'orlov-ai/hcaptcha-dataset': {
        'slug': 'hcaptcha', 'commit': 'a1b180f9091719517d8890c33ab8b4d5df38ac10',
        'tree': '77d61b41e6c51897b427eea3dfb9be527ce93b95',
    },
    'ssivakorn/reCAPTCHA-study': {
        'slug': 'recaptcha', 'commit': 'efb3595c33780abf9d326c729973e72d165366c2',
        'tree': '4a001ca38d2512b31a66c77f5d4f05a66792a203',
    },
}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def validate_source(source: str, metadata_dir: Path) -> tuple[dict, dict, Path]:
    expected = SOURCES[source]
    slug = expected['slug']
    tree_path = metadata_dir / f'{slug}-tree.json'
    tree = collector.read_tree(tree_path)
    if (tree.get('repo'), tree.get('commit_sha'), tree.get('sha'), tree.get('git_tree_sha')) != (
        source, expected['commit'], expected['tree'], expected['tree']
    ):
        raise ValueError(f'Wrong pinned source snapshot: {source}')
    if tree.get('truncated') or not isinstance(tree.get('tree'), list):
        raise ValueError(f'Incomplete source snapshot: {source}')
    receipt = read(metadata_dir / f'{slug}-archive.json')
    archive = Path(receipt['path'])
    wanted_url = f"https://codeload.github.com/{source}/tar.gz/{expected['commit']}"
    if (receipt.get('repo'), receipt.get('commit_sha'), receipt.get('url'), receipt.get('complete')) != (
        source, expected['commit'], wanted_url, True
    ):
        raise ValueError(f'Invalid archive receipt: {source}')
    if archive.stat().st_size != receipt['bytes'] or collector.sha256_file(archive) != receipt['sha256']:
        raise ValueError(f'Archive receipt hash mismatch: {source}')
    return tree, receipt, tree_path


def offline_snapshot(source: str, snapshot: dict, original: Path, destination: Path) -> tuple[dict, dict]:
    """Adapter for the unmodified collector: validate immutable pins, never HEAD."""
    expected = SOURCES[source]
    if snapshot.get('commit_sha') != expected['commit'] or snapshot.get('sha') != expected['tree']:
        raise ValueError(f'Unexpected collector snapshot: {source}')
    filename = 'reCAPTCHA-study-tree.json' if source.startswith('ssivakorn/') else 'hcaptcha-dataset-tree.json'
    saved = destination / filename
    shutil.copyfile(original, saved)
    return snapshot, {
        'repo': source, 'commit_sha': expected['commit'], 'git_tree_sha': expected['tree'],
        'resolved_from': 'fixed historical commit/tree snapshot; offline, no HEAD resolution',
        'verified_tree_entry_count': len(snapshot['tree']),
        'input_snapshot_sha256': collector.sha256_file(original),
        'saved_snapshot_path': saved.name, 'saved_snapshot_sha256': collector.sha256_file(saved),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata_dir = args.source_metadata.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    # Verify both complete inputs before creating any output.
    checked = {source: validate_source(source, metadata_dir) for source in SOURCES}
    (output / 'source_metadata').mkdir(parents=True)
    metadata = {
        'run_id': output.name, 'created_utc': '2026-10-05',
        'restoration': 'fixed public source Git revisions; original per-image manifest unavailable',
        'download_policy': 'offline; externally fetched pinned archives only', 'sources': [],
    }
    for source, (tree, receipt, tree_path) in checked.items():
        slug = SOURCES[source]['slug']
        extracted = collector.safe_tar_extract(Path(receipt['path']), output / 'data' / slug,
                                               source, SOURCES[source]['commit'], tree)
        metadata['sources'].append({
            'repo': source, 'commit': SOURCES[source]['commit'], 'git_tree_sha': SOURCES[source]['tree'],
            'tree_url': tree.get('url'), 'tree_input_sha256': collector.sha256_file(tree_path),
            'archive': receipt, 'extract': {k: v for k, v in extracted.items() if k != 'files'},
            'extracted_files': extracted['files'],
        })
        for name in ('README.md', 'LICENSE'):
            asset = output / 'data' / slug / name
            if asset.exists():
                shutil.copyfile(asset, output / 'source_metadata' / f"{source.replace('/', '_')}-{name}")
        print(json.dumps({'source': source, 'extracted_files': extracted['files_extracted'],
                          'extracted_bytes': extracted['bytes_extracted']}), flush=True)
    (output / 'source_metadata' / 'source_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    # The collector's CLI runs in this isolated process. Keep all original label,
    # ordering, decoded-pixel deduplication, class and prompt logic unchanged.
    collector.resolve_snapshot = offline_snapshot
    sys.argv = [str(Path(collector.__file__)), '--run', str(output), '--manifests-only',
                '--hcaptcha-tree', str(checked['orlov-ai/hcaptcha-dataset'][2]),
                '--recaptcha-tree', str(checked['ssivakorn/reCAPTCHA-study'][2])]
    collector.main()
    full_path = output / 'public_full.json'
    full = read(full_path)
    counts = {'samples': len(full['samples']), 'boards': len(full['cases']),
              'source_totals': full['source_totals'], 'deduplication': full['deduplication']}
    restoration = {
        'manifest_sha256': collector.sha256_file(full_path), 'counts': counts,
        'matches_historical_counts': (counts['samples'], counts['boards']) == (4068, 1000),
        'historical_per_image_identity': 'unverified: original input manifest and raw predictions unavailable',
        'historical_role': 'exploratory model-selection benchmark; repeatedly used for candidate selection',
        'collector_sha256': collector.sha256_file(Path(collector.__file__)),
        'restorer_sha256': collector.sha256_file(Path(__file__)),
        'sources': [{k: item[k] for k in ('repo', 'commit', 'git_tree_sha', 'tree_input_sha256', 'archive')}
                    for item in metadata['sources']],
    }
    (output / 'restoration.json').write_text(json.dumps(restoration, indent=2) + '\n')
    print(json.dumps(restoration, indent=2), flush=True)


if __name__ == '__main__':
    main()
