#!/usr/bin/env python3
"""Offline, hash-checked restoration; never overwrite differing source bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, size: int, digest: str) -> None:
    if path.is_symlink() or path.stat().st_size != size or sha(path) != digest:
        raise ValueError('artifact pin mismatch: ' + str(path))


def safe_target(destination: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:
        raise ValueError('unsafe archive path')
    target = destination.joinpath(*p.parts)
    if not target.resolve().is_relative_to(destination):
        raise ValueError('archive target escapes destination')
    # Existing symlinks are not restored through, including links inside root.
    if any(q.is_symlink() for q in [target, *target.parents] if q != destination.parent):
        raise ValueError('symlink in restore path: ' + str(target))
    return target


def copy_exclusive(source, target: Path, size: int, digest: str) -> bool:
    if target.exists():
        verify(target, size, digest)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as output:
        shutil.copyfileobj(source, output)
    verify(target, size, digest)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--include-handoff', action='store_true',
                        help='also restore the final handoff ZIP, using its tracked SHA-256 receipt')
    parser.add_argument('--plan', type=Path,
                        default=ROOT / 'experiments/sku-matching/results/20261014-sku-session-restore-plan-v1.json')
    args = parser.parse_args()
    if args.destination.is_symlink():
        raise ValueError('destination must not be a symlink')
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    if args.include_handoff:
        name = '20261014-sku-session-handoff-final-v1.zip'
        path = ROOT / 'experiments/sku-matching/results' / name
        digest = path.with_suffix('.zip.sha256').read_text(encoding='utf-8').split()[0]
        plan['archives'].append({'name': name, 'path': str(path.relative_to(ROOT)),
                                 'bytes': path.stat().st_size, 'sha256': digest})
    counts = {'archives': 0, 'created': 0, 'identical': 0}
    for entry in plan['archives']:
        if 'parts' in entry:
            archive = safe_target(destination, '.lab-output/_restore-archives/' + entry['name'])
            if not archive.exists():
                archive.parent.mkdir(parents=True, exist_ok=True)
                with archive.open('xb') as output:
                    for part in entry['parts']:
                        path = ROOT / part['path']
                        verify(path, part['bytes'], part['sha256'])
                        with path.open('rb') as source:
                            shutil.copyfileobj(source, output)
            else:
                for part in entry['parts']:
                    verify(ROOT / part['path'], part['bytes'], part['sha256'])
        else:
            archive = ROOT / entry['path']
        verify(archive, entry['bytes'], entry['sha256'])
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError('ZIP CRC mismatch: ' + str(archive))
            if len(z.namelist()) != len(set(z.namelist())):
                raise ValueError('duplicate ZIP member paths')
            for item in z.infolist():
                if item.is_dir():
                    continue
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('ZIP symlink rejected')
                if item.filename == 'CHECKPOINT_MANIFEST.json':
                    name = '.lab-output/_restore-manifests/' + entry['name'] + '.json'
                elif item.filename.startswith('.lab-output/'):
                    name = item.filename
                else:
                    # Archived code/instructions are historical snapshots.
                    name = '.lab-output/_restore-snapshots/' + entry['name'] + '/' + item.filename
                # Validate original spelling as well as the rewritten target.
                safe_target(destination, item.filename)
                target = safe_target(destination, name)
                with z.open(item) as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                with z.open(item) as source:
                    created = copy_exclusive(source, target, item.file_size, digest)
                counts['created' if created else 'identical'] += 1
        counts['archives'] += 1
    # This audited current helper reconstructs the frozen physical capture layout.
    relative = 'experiments/sku-matching/restore_volume_source_checkpoint_v1.py'
    helper = ROOT / relative
    target = safe_target(destination, relative)
    digest = plan['physical_capture_helper_sha256']
    verify(helper, plan['physical_capture_helper_bytes'], digest)
    with helper.open('rb') as source:
        copy_exclusive(source, target, helper.stat().st_size, digest)
    print(json.dumps({'destination': str(destination), **counts,
                      'network_or_model_execution': False,
                      'next_command': ['python3', '-B', str(target)]}))


if __name__ == '__main__':
    main()
