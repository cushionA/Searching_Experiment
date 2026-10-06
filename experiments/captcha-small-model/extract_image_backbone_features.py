#!/usr/bin/env python3
"""Extract pinned ImageNet image-backbone features for the frozen linear head.

This program loads no text encoder, prompts, CAPTCHA labels, or recognition
decoder. It restores a strict ImageNet classification checkpoint, removes its
classifier only at feature extraction time, and writes normalized pooled image
features using the cache schema consumed by fit_image_head.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from PIL import Image


MODEL_SPECS: dict[str, dict[str, Any]] = {
    'EfficientFormerV2-S2': {
        'timm_name': 'efficientformerv2_s2', 'tag': 'snap_dist_in1k',
        'hf_repo': 'timm/efficientformerv2_s2.snap_dist_in1k',
        'revision': '1c56a76355000c79568559d34ba3fa24416b5107',
        'filename': 'model.safetensors', 'sha256': 'c46c28768173ee0e518b7cd94af1e832cb88d2115068a81386c08650cc22a24f',
        'bytes': 51442016, 'head': 'ImageNet-1K distillation classifier',
    },
    'EfficientFormerV2-L': {
        'timm_name': 'efficientformerv2_l', 'tag': 'snap_dist_in1k',
        'hf_repo': 'timm/efficientformerv2_l.snap_dist_in1k',
        'revision': '092a1c107ec0fdc2207a470f81c696022f17a42e',
        'filename': 'model.safetensors', 'sha256': 'a92de90055b116562f85322e596494829e54d86c56471652e3c07ed39957c92a',
        'bytes': 106226760, 'head': 'ImageNet-1K distillation classifier',
    },
    'EfficientFormer-L3': {
        'timm_name': 'efficientformer_l3', 'tag': 'snap_dist_in1k',
        'hf_repo': 'timm/efficientformer_l3.snap_dist_in1k',
        'revision': 'ca929ef48f056232098ad9226a69e4c29a00cc46',
        'filename': 'model.safetensors', 'sha256': 'ab0c5bfd7c1863e6b7625a08615d0ea88b6acb978d30fdd0fbb24e2e12da3e2a',
        'bytes': 125981408, 'head': 'ImageNet-1K distillation classifier',
    },
    'MobileOne-S4': {
        'timm_name': 'mobileone_s4', 'tag': 'apple_in1k',
        'hf_repo': 'timm/mobileone_s4.apple_in1k',
        'revision': 'f4dfe4c260ea8d7d07ac6c06042fe254efdd329a',
        'filename': 'model.safetensors', 'sha256': 'bb333a71202569a31d5d338eda74dd20e59cc5105c9126af109c30f7813c0440',
        'bytes': 60377552, 'head': 'ImageNet-1K classifier; standard timm mobileone_s4 with train-time branches',
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_images(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Return unique-byte images in the same stable sample-then-board order."""
    first_by_sha: dict[str, dict[str, str]] = {}
    for sample in manifest.get('samples', []):
        path, digest = sample.get('path'), sample.get('sha256')
        if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Invalid sample image identity: {sample.get('id')}")
        first_by_sha.setdefault(digest.lower(), {'sha256': digest.lower(), 'path': path})
    for case in manifest.get('cases', []):
        paths, hashes = case.get('tile_paths'), case.get('tile_sha256')
        if not isinstance(paths, list) or not isinstance(hashes, list) or len(paths) != len(hashes):
            raise ValueError(f"Invalid tile image identities: {case.get('id')}")
        for path, digest in zip(paths, hashes, strict=True):
            if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"Invalid tile image identity: {case.get('id')}")
            first_by_sha.setdefault(digest.lower(), {'sha256': digest.lower(), 'path': path})
    return list(first_by_sha.values())


def resolve_local_images(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    rows = manifest_images(manifest)
    root = manifest_path.parent
    for row in rows:
        path = root / row['path']
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual != row['sha256']:
            raise ValueError(f"Image byte SHA-256 mismatch for {row['path']}: {actual}")
        row['absolute_path'] = str(path.resolve())
    return rows


def pinned_checkpoint(spec: dict[str, Any], cache: Path) -> Path:
    """Download a single pinned safetensors file atomically and verify metadata."""
    import urllib.request

    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / f"{spec['timm_name']}-{spec['revision'][:12]}.safetensors"
    if destination.exists():
        if destination.stat().st_size != spec['bytes'] or sha256_file(destination) != spec['sha256']:
            raise ValueError(f'Cached checkpoint failed pinned size/SHA-256: {destination}')
        return destination
    url = (f"https://huggingface.co/{spec['hf_repo']}/resolve/"
           f"{spec['revision']}/{spec['filename']}")
    partial = destination.with_suffix('.partial')
    deadline = time.monotonic() + 900
    transferred = 0
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'captcha-small-model-feature-extractor/1'})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise TimeoutError('Checkpoint download exceeded 15 minutes')
                transferred += len(chunk)
                if transferred > spec['bytes']:
                    raise ValueError('Checkpoint is larger than its pinned metadata size')
                output.write(chunk)
        if transferred != spec['bytes'] or sha256_file(partial) != spec['sha256']:
            raise ValueError('Downloaded checkpoint size or SHA-256 differs from pinned metadata')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def resolve_pretrained_cfg(model_name: str):
    """Instantiate exact timm architecture and reject every missing/unexpected key."""
    import timm
    spec = MODEL_SPECS[model_name]
    available = set(timm.list_models(pretrained=False))
    if spec['timm_name'] not in available:
        raise RuntimeError(f"Installed timm {timm.__version__} lacks {spec['timm_name']}")
    pretrained_cfg = timm.get_pretrained_cfg(spec['timm_name'])
    if pretrained_cfg is None or pretrained_cfg.tag != spec['tag']:
        raise RuntimeError(f"Unexpected timm pretrained tag for {model_name}: {getattr(pretrained_cfg, 'tag', None)}")
    if pretrained_cfg.hf_hub_id != spec['hf_repo']:
        raise RuntimeError(f"Unexpected timm Hub mapping for {model_name}: {pretrained_cfg.hf_hub_id}")
    return pretrained_cfg


def create_strict_model(model_name: str, checkpoint_path: Path):
    """Instantiate exact timm architecture and reject every missing/unexpected key."""
    import timm
    from safetensors.torch import load_file

    spec = MODEL_SPECS[model_name]
    pretrained_cfg = resolve_pretrained_cfg(model_name)
    model = timm.create_model(spec['timm_name'], pretrained=False, num_classes=1000,
                              pretrained_cfg=pretrained_cfg)
    state = load_file(str(checkpoint_path), device='cpu')
    result = model.load_state_dict(state, strict=True)
    if result.missing_keys or result.unexpected_keys:
        raise RuntimeError(f"Non-strict checkpoint load is forbidden: {result}")
    model.eval()
    return model, pretrained_cfg


def extract_one(model_name: str, rows: list[dict[str, str]], args) -> dict[str, Any]:
    import torch
    from safetensors.torch import save_file
    from timm.data import create_transform, resolve_model_data_config

    spec = MODEL_SPECS[model_name]
    checkpoint = pinned_checkpoint(spec, args.model_dir)
    model, cfg = create_strict_model(model_name, checkpoint)
    device = args.device
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA was requested but is unavailable')
    model.to(device)
    data_cfg = resolve_model_data_config(model)
    transform = create_transform(**data_cfg, is_training=False)
    outputs = []
    started = time.perf_counter()
    for start in range(0, len(rows), args.batch_size):
        batch_rows = rows[start:start + args.batch_size]
        batch = torch.stack([
            transform(Image.open(row['absolute_path']).convert('RGB')) for row in batch_rows
        ]).to(device)
        with torch.inference_mode():
            # timm's standardized pre-logit head removes the trained classifier.
            features = model.forward_head(model.forward_features(batch), pre_logits=True)
            if features.ndim > 2:
                features = torch.flatten(features, 1)
            features = torch.nn.functional.normalize(features.float(), p=2, dim=1)
        if features.ndim != 2 or not torch.isfinite(features).all().item():
            raise RuntimeError(f'{model_name}: non-finite or non-vector image features')
        outputs.append(features.cpu())
        print(json.dumps({'model': model_name, 'encoded': min(start + len(batch_rows), len(rows)),
                          'total': len(rows)}, ensure_ascii=False), flush=True)
    embeddings = torch.cat(outputs, dim=0).contiguous()
    if embeddings.shape[0] != len(rows):
        raise RuntimeError('Feature rows do not match unique manifest images')
    elapsed = time.perf_counter() - started
    output_dir = args.output_dir
    features_path = output_dir / f"{model_name}.features.safetensors"
    index_path = output_dir / f"{model_name}.features.index.json"
    if features_path.exists() or index_path.exists():
        raise FileExistsError(f'Feature outputs already exist for {model_name}')
    save_file({'embeddings': embeddings}, str(features_path), metadata={
        'model': model_name, 'normalization': 'L2 normalized float32 image features',
        'encoder_source': 'pinned ImageNet-1K classification checkpoint',
    })
    feature_index = {
        'schema_version': 1,
        'model': model_name,
        'architecture': spec['timm_name'],
        'timm_tag': spec['tag'],
        'input_manifest_sha256': args.manifest_sha256,
        'features_filename': features_path.name,
        'features_sha256': sha256_file(features_path),
        'image_sha256': [row['sha256'] for row in rows],
        'relative_paths': [row['path'] for row in rows],
        'shape': list(embeddings.shape),
        'tensor': 'embeddings',
        'dtype': 'float32',
        'normalization': 'L2 normalized after float32 conversion',
        'benchmark_evaluator_sha256': sha256_file(Path(__file__)),
        'weight_provenance': {
            'hub_repo': spec['hf_repo'], 'revision': spec['revision'],
            'filename': spec['filename'], 'sha256': spec['sha256'], 'bytes': spec['bytes'],
            'metadata_url': f"https://huggingface.co/api/models/{spec['hf_repo']}?blobs=true",
            'local_checkpoint_sha256': sha256_file(checkpoint),
            'load_mode': 'safetensors.torch.load_file; timm load_state_dict(strict=True)',
            'classification_head': spec['head'],
        },
        'timm_pretrained_config': {
            'tag': cfg.tag, 'hf_hub_id': cfg.hf_hub_id, 'num_classes': cfg.num_classes,
            'classifier': list(cfg.classifier) if isinstance(cfg.classifier, tuple) else cfg.classifier,
        },
        'preprocessing': {
            'source': 'timm.resolve_model_data_config + timm.create_transform(is_training=False)',
            **{key: list(value) if isinstance(value, tuple) else value for key, value in data_cfg.items()},
        },
        'feature_extraction': 'model.forward_head(model.forward_features(batch), pre_logits=True)',
        'input_identity_claim': 'byte-SHA unique image paths; identity derives from restored public manifest only',
        'images_encoded': len(rows),
        'elapsed_seconds': elapsed,
        'device': device,
        'torch_version': torch.__version__,
        'timm_version': __import__('timm').__version__,
    }
    index_path.write_text(json.dumps(feature_index, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return {'model': model_name, 'features': str(features_path), 'index': str(index_path),
            'shape': list(embeddings.shape), 'features_sha256': feature_index['features_sha256'],
            'index_sha256': sha256_file(index_path), 'elapsed_seconds': elapsed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, default=Path('.lab-output/model-cache/image-backbones'))
    parser.add_argument('--models', default=','.join(MODEL_SPECS),
                        help='Comma-separated names from: ' + ', '.join(MODEL_SPECS))
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument('--batch-size', type=int, default=64)
    args = parser.parse_args()
    names = [name.strip() for name in args.models.split(',') if name.strip()]
    if not names or len(names) != len(set(names)) or any(name not in MODEL_SPECS for name in names):
        parser.error('models must be unique names from: ' + ', '.join(MODEL_SPECS))
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    raw = args.manifest.read_bytes()
    args.manifest_sha256 = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        parser.error('manifest root must be an object')
    rows = resolve_local_images(args.manifest, manifest)
    if not rows:
        parser.error('manifest contains no images')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    results = []
    for model_name in names:
        results.append(extract_one(model_name, rows, args))
    run = {
        'method': 'frozen ImageNet image backbones; classifier weights used only as pretrained encoder parameters, pooled image vectors feed a separately trained masked-BCE linear head',
        'models': results,
        'input_manifest_sha256': args.manifest_sha256,
        'unique_manifest_images': len(rows),
        'source_label_taxonomy': 'No text prompts, class strings, labels, OCR targets, or board answers are used during feature extraction.',
        'identity_limit': 'This validates restored manifest bytes, not identity with an unavailable historical per-image manifest.',
        'warning': 'Public source annotations and held-out splits are exploratory; they do not establish CAPTCHA service behavior.',
    }
    (args.output_dir / 'run.json').write_text(json.dumps(run, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(run, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
