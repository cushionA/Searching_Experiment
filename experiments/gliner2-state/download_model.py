"""Fetch a pinned public checkpoint to an external cache; record all file hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from huggingface_hub import snapshot_download

REPO = 'fastino/GLiNER2.5-multi-Decide'
REVISION = 'a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f'

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = p.parse_args()
    started = time.perf_counter()
    path = Path(snapshot_download(REPO, revision=REVISION, cache_dir=str(args.cache),
                                 allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt']))
    manifest = {'repo': REPO, 'revision': REVISION, 'download_seconds': time.perf_counter() - started, 'files': []}
    for f in sorted(path.rglob('*')):
        if not f.is_file():
            continue
        digest = hashlib.sha256()
        with f.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        manifest['files'].append({'name': f.relative_to(path).as_posix(), 'bytes': f.stat().st_size,
                                  'sha256': digest.hexdigest()})
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    print(path)

if __name__ == '__main__':
    main()
