#!/usr/bin/env python3
"""Freeze grouped exploratory train/validation/test image splits before fitting.

Every tile from a board shares a fold. Boards/samples with identical decoded
pixels are joined transitively, including repeated tiles across different boards.
The original evaluation set has already been used for model selection, so these
holdouts are never described as a fresh final test set.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
from typing import Any


class Components:
    def __init__(self, keys: list[str]):
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def grouped_rows(manifest: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows = [{'kind': 'sample', 'row': row} for row in manifest['samples']]
    rows += [{'kind': 'board', 'row': row} for row in manifest['cases']]
    keys = [item['row']['id'] for item in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('IDs must be unique across samples and boards')
    components = Components(keys)
    first_content = {}
    for item in rows:
        row = item['row']
        digests = [row['pixel_sha256']] if item['kind'] == 'sample' else row['tile_pixel_sha256']
        if not digests or any(not isinstance(x, str) or len(x) != 64 for x in digests):
            raise ValueError(f"Missing decoded-pixel grouping hashes: {row['id']}")
        raw_digests = [row.get('sha256')] if item['kind'] == 'sample' else row.get('tile_sha256', [])
        content_keys = [('decoded-pixel', digest) for digest in digests]
        content_keys += [('raw-bytes', digest) for digest in raw_digests if digest is not None]
        for content_key in content_keys:
            if content_key in first_content:
                components.join(row['id'], first_content[content_key])
            else:
                first_content[content_key] = row['id']
    grouped = defaultdict(list)
    for item in rows:
        grouped[components.find(item['row']['id'])].append(item)
    return [grouped[key] for key in sorted(grouped)]


def stratum(item: dict[str, Any]) -> str:
    row = item['row']
    label = row['label'] if item['kind'] == 'sample' else row['target']
    kind = item['kind'] if item['kind'] == 'sample' else f"board/{row['source_type']}"
    return f"{row['source']}/{kind}/{label}"


def partition(manifest: dict[str, Any], seed: int = 20261006,
              fractions: tuple[float, float, float] = (.7, .1, .2)) -> dict[str, Any]:
    if len(fractions) != 3 or any(f <= 0 for f in fractions) or abs(sum(fractions) - 1) > 1e-9:
        raise ValueError('Three positive split fractions must sum to one')
    groups = grouped_rows(manifest)
    rng = random.Random(seed)
    rng.shuffle(groups)
    # Process large components first so exact duplicates never get split to meet
    # a percentage target. Randomized ties make the frozen seed meaningful.
    groups.sort(key=len, reverse=True)
    totals = Counter(stratum(item) for group in groups for item in group)
    counts = {fold: Counter() for fold in ('train', 'validation', 'test')}
    folds = dict(zip(counts, fractions, strict=True))
    assigned = {fold: [] for fold in folds}
    group_rows = []
    for group in groups:
        contribution = Counter(stratum(item) for item in group)
        # Minimize the change in normalized squared deviation from per-stratum
        # targets. This balances both labels and source task counts without ever
        # consulting model predictions or fitting outcomes.
        def cost(fold: str) -> float:
            return sum(((counts[fold][key] + n - totals[key] * folds[fold]) ** 2
                        - (counts[fold][key] - totals[key] * folds[fold]) ** 2)
                       / max(1, totals[key]) for key, n in contribution.items())
        fold = min(folds, key=cost)
        counts[fold].update(contribution)
        assigned[fold].extend(group)
        ids = sorted(item['row']['id'] for item in group)
        group_id = hashlib.sha256('\0'.join(ids).encode()).hexdigest()
        group_rows.append({'group_id': group_id, 'fold': fold, 'sample_ids':
                           [item['row']['id'] for item in group if item['kind'] == 'sample'],
                           'board_ids': [item['row']['id'] for item in group if item['kind'] == 'board'],
                           'size': len(group)})
    outputs = {}
    for fold, items in assigned.items():
        # Retain historical source order within each fold.
        selected = {item['row']['id'] for item in items}
        outputs[fold] = {key: value for key, value in manifest.items() if key not in ('samples', 'cases')}
        outputs[fold]['samples'] = [row for row in manifest['samples'] if row['id'] in selected]
        outputs[fold]['cases'] = [row for row in manifest['cases'] if row['id'] in selected]
        outputs[fold]['split'] = {'fold': fold, 'seed': seed, 'role':
                                 'exploratory holdout from previously used model-selection images'}
    return {'manifests': outputs, 'groups': group_rows,
            'summary': {fold: {'samples': len(outputs[fold]['samples']), 'boards': len(outputs[fold]['cases']),
                               'strata': dict(counts[fold]),
                               'unsupported_strata': sorted(key for key in totals if not counts[fold][key])} for fold in folds},
            'component_count': len(groups), 'largest_component_rows': max(map(len, groups), default=0),
            'seed': seed, 'fractions': folds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--prefix', default='grouped')
    parser.add_argument('--seed', type=int, default=20261006)
    args = parser.parse_args()
    if not args.prefix or Path(args.prefix).name != args.prefix or args.prefix in ('.', '..'):
        raise ValueError('prefix must be one filename component')
    manifest_path = args.manifest.resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    targets = {fold: manifest_path.parent / f'{args.prefix}-{fold}.json'
               for fold in ('train', 'validation', 'test')}
    report_path = manifest_path.parent / f'{args.prefix}-splits.json'
    for path in (*targets.values(), report_path):
        if path.exists():
            raise FileExistsError(path)
    result = partition(manifest, args.seed)
    for fold, output in result.pop('manifests').items():
        output['split']['parent_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        targets[fold].write_text(json.dumps(output, indent=2) + '\n')
    result.update({
        'parent_manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'split_manifests': {fold: {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                            for fold, path in targets.items()},
        'group_rule': 'original board identity plus transitive identical raw-bytes or decoded-pixel duplicates across all tiles/samples',
        'same_board_tiles_share_fold': True, 'identical_decoded_pixels_share_fold': True, 'identical_raw_bytes_share_fold': True,
        'latent_original_scene_identity': 'not always supplied by sources; exact pixels and board identity only',
        'fresh_final_test': False,
        'limitation': 'all source inputs have already been used for model selection; acquire unused material for final evaluation',
    })
    report_path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'groups'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
