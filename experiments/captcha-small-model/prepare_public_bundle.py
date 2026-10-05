"""Package only manifest-referenced images for the offline GPU evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    refs = {s['path']: s['sha256'] for s in manifest['samples']}
    for case in manifest['cases']:
        for name, digest in zip(case['tile_paths'], case['tile_sha256'], strict=True):
            if name in refs and refs[name] != digest:
                raise ValueError(f'Conflicting image hash: {name}')
            refs[name] = digest
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', raw)
        for index, (name, digest) in enumerate(sorted(refs.items()), 1):
            rel = PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or '\\' in name:
                raise ValueError(f'Unsafe image path: {name}')
            path = args.manifest.parent / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f'Image hash mismatch: {name}')
            archive.write(path, name)
            if index % 2000 == 0:
                print(json.dumps({'verified_and_packed': index, 'total': len(refs)}), flush=True)
    h = hashlib.sha256()
    with args.output.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    verification = {'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                    'bundle_sha256': h.hexdigest(), 'bundle_bytes': args.output.stat().st_size,
                    'image_paths': len(refs), 'samples': len(manifest['samples']),
                    'boards': len(manifest['cases']), 'all_image_hashes_verified': True,
                    'includes_full_board_screenshots': False}
    args.output.with_suffix('.verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    print(json.dumps(verification), flush=True)


if __name__ == '__main__':
    main()
