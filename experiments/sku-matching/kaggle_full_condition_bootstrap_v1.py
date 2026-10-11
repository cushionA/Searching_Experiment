"""Kaggle bootstrap for a pinned, private, real-data condition choice test."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

# The upload builder replaces this literal after freezing the private dataset.
BUNDLE_MANIFEST_SHA256 = 'REPLACE_WITH_FROZEN_MANIFEST_SHA256'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    started = time.perf_counter()
    print(json.dumps({'stage': 'start', 'python': sys.version}), flush=True)
    if sys.version_info < (3, 12):
        raise RuntimeError('Python 3.12+ is required')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                    'transformers==5.17.0', 'tokenizers==0.23.3',
                    'fugashi==1.5.2', 'unidic-lite==1.0.8',
                    'huggingface-hub==1.33.0', 'safetensors==0.8.0'], check=True)
    print(json.dumps({'stage': 'dependencies-ready'}), flush=True)
    task_dir = Path(tempfile.mkdtemp(prefix='sku-full-choice-'))
    bundle = task_dir / 'payload'
    manifests = list(Path('/kaggle/input').rglob('BUNDLE_MANIFEST.json'))
    archive_verified = False
    if len(manifests) == 1:
        source = manifests[0].parent
        if sha(manifests[0]) != BUNDLE_MANIFEST_SHA256:
            raise ValueError('mounted bundle manifest SHA differs')
        metadata = json.loads(manifests[0].read_text())
        bundle.mkdir()
        for rel in [*metadata['files'], 'BUNDLE_MANIFEST.json']:
            path = Path(rel)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('unsafe bundle path')
            destination = bundle / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, destination)
    else:
        archives = list(Path('/kaggle/input').rglob('sku-full-input.zip'))
        if len(archives) != 1:
            raise ValueError('expected one private input bundle')
        with zipfile.ZipFile(archives[0]) as z:
            for name in z.namelist():
                path = Path(name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('unsafe archive path')
            if z.testzip() is not None:
                raise ValueError('input archive CRC failure')
            z.extractall(bundle)
        archive_verified = True
    if sha(bundle / 'BUNDLE_MANIFEST.json') != BUNDLE_MANIFEST_SHA256:
        raise ValueError('input bundle manifest SHA differs')
    sys.path.insert(0, str(bundle / 'code'))
    import kaggle_full_condition_choices_v1 as driver
    verified = driver.verify_bundle(bundle)
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('Kaggle job has no CUDA GPU')
    print(json.dumps({'stage': 'input-ready', 'bundle': verified,
                      'gpu': torch.cuda.get_device_name(0)}, ensure_ascii=False), flush=True)
    output = Path('/kaggle/working/sku-run-results')
    output.mkdir(exist_ok=False)
    driver_path = bundle / 'code/kaggle_full_condition_choices_v1.py'
    for tag in ['full', 'novel']:
        command = [sys.executable, '-B', str(driver_path), '--bundle-dir', str(bundle),
                   '--input-dir', str(bundle / 'input' / tag), '--output-dir', str(output / tag),
                   '--weights-dir', str(task_dir / 'models'),
                   '--jev-provenance', str(bundle / 'provenance/jev-provenance.json')]
        if tag == 'novel':
            command.append('--no-download')
        print(json.dumps({'stage': 'inference-start', 'cohort': tag}), flush=True)
        subprocess.run(command, check=True)
    metadata = json.loads((bundle / 'BUNDLE_MANIFEST.json').read_text())
    files = {str(p.relative_to(output)): {'size_bytes': p.stat().st_size, 'sha256': sha(p)}
             for p in sorted(output.rglob('*')) if p.is_file()}
    receipt = {'schema_version': 'kaggle-full-condition-completion-v1',
               'input_bundle_manifest_sha256': BUNDLE_MANIFEST_SHA256,
               'input_archive_crc_verified': archive_verified,
               'input_payload_sha256_verified': True, 'labels_included': False,
               'synthetic_data': False, 'gpu': torch.cuda.get_device_name(0),
               'torch_version': torch.__version__, 'cohorts': metadata['cohorts'],
               'elapsed_seconds': time.perf_counter() - started, 'files': files}
    (output / 'COMPLETION.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    archive = Path('/kaggle/working/sku-full-condition-results.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file():
                z.write(p, str(p.relative_to(output)))
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('result archive CRC failure')
    result = {'artifact_sha256': sha(archive), 'artifact_bytes': archive.stat().st_size,
              'payload_file_count': len(files), 'cohorts': metadata['cohorts'],
              'gpu': receipt['gpu'], 'elapsed_seconds': receipt['elapsed_seconds']}
    Path('/kaggle/working/sku-full-condition-result-receipt.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    # All newly generated results are now preserved in the CRC-verified artifact.
    shutil.rmtree(output)
    print(json.dumps({'stage': 'complete', **result}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
