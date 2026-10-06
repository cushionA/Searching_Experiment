#!/usr/bin/env python3
"""Build a pinned Kaggle notebook for ImageNet-backbone feature extraction and heads."""
from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import extract_image_backbone_features as extractor


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/captcha-small-model'
PUBLIC_BUILDER = EXPERIMENT / 'build_public_eval_notebook.py'
DEFAULT_MODELS = tuple(extractor.MODEL_SPECS)
SCRIPT_NAMES = ('extract_image_backbone_features.py', 'fit_image_head.py', 'subprocess_runner.py')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_public_builder():
    spec = importlib.util.spec_from_file_location('build_public_eval_notebook', PUBLIC_BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load public notebook builder: {PUBLIC_BUILDER}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def public_template(manifest: Path, dataset_ref: str, bundle: Path, models: list[str]) -> dict:
    """Use the public builder's verified ZIP/fold setup as a notebook template."""
    module = load_public_builder()
    with tempfile.TemporaryDirectory(prefix='image-backbone-template-') as tmp:
        temp_output = Path(tmp) / 'template.ipynb'
        old_argv = sys.argv
        try:
            sys.argv = [str(PUBLIC_BUILDER), '--manifest', str(manifest),
                        '--dataset-ref', dataset_ref, '--output', str(temp_output),
                        '--bundle', str(bundle), '--models', 'TinyCLIP-ViT-40M-32-Text-19M',
                        '--save-features']
            module.main()
        finally:
            sys.argv = old_argv
        return json.loads(temp_output.read_text(encoding='utf-8'))


def setup_payload(setup_source: str) -> str:
    module = ast.parse(setup_source)
    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'payload' for t in node.targets):
            call = node.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != 'loads':
                continue
            gzip_call = call.args[0]
            b64_call = gzip_call.args[0]
            return ast.literal_eval(b64_call.args[0])
    raise ValueError('Could not locate embedded public-builder setup payload')


def notebook_cell(kind: str, source: str) -> dict:
    digest = hashlib.sha256((kind + source).encode()).hexdigest()[:12]
    result = {'id': digest, 'cell_type': kind, 'metadata': {}, 'source': source.splitlines(keepends=True)}
    if kind == 'code':
        result.update({'execution_count': None, 'outputs': []})
    return result


def make_run_cell(models: list[str], payload: dict) -> str:
    model_literals = repr(models)
    pin_literals = repr(payload['model_pins'])
    return f'''import json, pathlib, sys

work = pathlib.Path("/kaggle/working")
root = pathlib.Path("/tmp/public-eval-input")
scripts = pathlib.Path("/tmp/public-eval-scripts")
sys.path.insert(0, str(scripts))
from subprocess_runner import run_logged_process
manifest = root / "manifest.json"
model_dir = pathlib.Path("/tmp/image-backbone-models")
summary_path = work / "public-eval-summary.json"
models = {model_literals}
model_pins = {pin_literals}
summary = {{"status": "running", "feature_extractors": {{}}, "heads": {{}}, "logs": {{}},
            "manifest_sha256": {payload['manifest_sha256']!r},
            "bundle_sha256": {payload['bundle_sha256']!r},
            "split_manifests_sha256": {payload['split_manifests_sha256']!r},
            "script_sha256": {payload['script_sha256']!r}, "model_pins": model_pins}}
model_dir.mkdir()

def save_summary():
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")

def run_one(key, command, timeout):
    log = work / ("image-backbone-" + key + ".log.txt")
    try:
        result = run_logged_process(command, log, timeout)
    except Exception as exc:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"{{type(exc).__name__}}: {{exc}}\\n")
        result = {{"returncode": None, "timed_out": False,
                  "launch_error": f"{{type(exc).__name__}}: {{exc}}"}}
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:] if log.exists() else []
    result["log"] = str(log)
    result["log_tail"] = "\\n".join(tail)
    return result

try:
    import torch
    summary["runtime"] = {{"torch": torch.__version__, "cuda_available": torch.cuda.is_available(),
                         "cuda_version": torch.version.cuda, "device_count": torch.cuda.device_count()}}
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this notebook is configured for GPU feature extraction")
except Exception as exc:
    summary["environment_error"] = f"{{type(exc).__name__}}: {{exc}}"
    summary["status"] = "error"
    summary["failures"] = {{"environment": summary["environment_error"]}}
    save_summary()
    raise

save_summary()
for name in models:
    key = name.lower().replace("-", "_")
    feature_dir = work / ("public-eval-result-" + key)
    command = [sys.executable, str(scripts / "extract_image_backbone_features.py"),
               "--manifest", str(manifest), "--output-dir", str(feature_dir),
               "--model-dir", str(model_dir), "--models", name,
               "--device", "cuda", "--batch-size", "64"]
    status = run_one(key, command, 1200)
    summary["feature_extractors"][name] = status
    summary["logs"]["feature-" + name] = status.get("log")
    save_summary()

    if status.get("returncode") == 0 and not status.get("timed_out"):
        head_dir = work / ("public-eval-head-" + key)
        head_command = [sys.executable, str(scripts / "fit_image_head.py"),
            "--manifest", str(manifest),
            "--features", str(feature_dir / (name + ".features.safetensors")),
            "--feature-index", str(feature_dir / (name + ".features.index.json")),
            "--train-split", str(root / "grouped-train.json"),
            "--validation-split", str(root / "grouped-validation.json"),
            "--test-split", str(root / "grouped-test.json"),
            "--output", str(head_dir), "--epochs", "100", "--patience", "10", "--threads", "2"]
        head_status = run_one("head-" + key, head_command, 180)
    else:
        head_status = {{"returncode": None, "timed_out": False, "skipped": True,
                       "reason": "Feature extractor failed; no verified feature cache"}}
    summary["heads"][name] = head_status
    summary["logs"]["head-" + name] = head_status.get("log")
    save_summary()

failures = {{}}
for name, status in summary["feature_extractors"].items():
    if status.get("returncode") != 0 or status.get("timed_out") or status.get("skipped"):
        failures["features-" + name] = status
for name, status in summary["heads"].items():
    if status.get("returncode") != 0 or status.get("timed_out") or status.get("skipped"):
        failures["head-" + name] = status
summary["failures"] = failures
summary["status"] = "error" if failures else "success"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
if failures:
    raise RuntimeError(f"Some image-backbone runs failed; see {{summary_path}}")
'''


def build(args) -> dict:
    if args.output.exists():
        raise FileExistsError(args.output)
    models = args.models.split(',')
    if not models or len(models) != len(set(models)) or any(x not in DEFAULT_MODELS for x in models):
        raise ValueError('models must be unique names from: ' + ', '.join(DEFAULT_MODELS))
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError('Manifest root must be an object')

    # This calls the existing public builder unchanged. It validates the ZIP
    # inventory, manifest bytes, image references, fold bytes, and index hashes.
    template = public_template(args.manifest, args.dataset_ref, args.bundle, models)
    builder = load_public_builder()
    refs = builder.image_references(manifest)
    folds = builder.split_assets(args.manifest, manifest_bytes)
    bundle_sha = builder.validate_bundle(args.bundle, manifest_bytes, refs, folds)

    scripts = {name: (EXPERIMENT / name).read_text(encoding='utf-8') for name in SCRIPT_NAMES}
    script_hashes = {name: sha256(raw.encode('utf-8')) for name, raw in scripts.items()}
    pins = {name: {key: spec[key] for key in ('timm_name', 'tag', 'hf_repo', 'revision', 'filename', 'sha256', 'bytes')}
            for name, spec in extractor.MODEL_SPECS.items()}
    payload = {
        'scripts': scripts, 'script_sha256': script_hashes,
        'manifest_sha256': sha256(manifest_bytes), 'bundle_sha256': bundle_sha,
        'dataset_ref': args.dataset_ref,
        'split_manifests_sha256': {name: sha256(raw) for name, raw in folds.items()},
        'save_features_and_fit_heads': True, 'model_pins': pins,
    }
    payload_raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    packed = base64.b64encode(gzip.compress(payload_raw, compresslevel=9, mtime=0)).decode('ascii')
    if len(packed.encode('ascii')) > 1024 * 1024:
        raise ValueError(f'Compressed notebook payload exceeds 1 MiB: {len(packed)} bytes')

    setup_cell = template['cells'][2]
    setup_source = ''.join(setup_cell['source'])
    old_packed = setup_payload(setup_source)
    setup_source = setup_source.replace(repr(old_packed), repr(packed), 1)
    template_keys = ['tinyclip_vit_40m_32_text_19m']
    wanted_keys = [name.lower().replace('-', '_') for name in models]
    setup_source = setup_source.replace(f'model_keys = {template_keys!r}', f'model_keys = {wanted_keys!r}')
    setup_source = setup_source.replace(
        '(work / "environment.json").write_text',
        'env["embedded_script_sha256"] = payload["script_sha256"]\nenv["image_backbone_weight_pins"] = payload["model_pins"]\n'
        'env["image_backbone_models"] = ' + repr(models) + '\n'
        '(work / "environment.json").write_text', 1)
    template['cells'][0] = notebook_cell('markdown', f'''# Frozen ImageNet image-backbone comparison

Extract normalized pre-logit image features for **{', '.join(models)}**, then fit an independent 16-logit linear head on the fixed grouped train/validation/test partitions. Each encoder is frozen. These are ImageNet-1K image classifiers; this notebook performs no zero-shot text scoring, prompt synthesis, OCR decoding, or CAPTCHA-trained recognition.

The input archive SHA-256 is `{bundle_sha}` and the public manifest SHA-256 is `{payload['manifest_sha256']}`. The notebook verifies the mounted archive, referenced images, and all grouped split files before extraction. The restored source snapshot does not establish identity with any unavailable historical per-image manifest, and these exploratory holdouts come from previously used public images.

Each backbone runs in a separate subprocess with a 1,200-second timeout. A failed extraction is recorded and the remaining backbones continue; successful feature caches are passed to `fit_image_head.py` with 100 epochs maximum, patience 10, CPU and two threads, with a 180-second timeout. Board decisions use a fixed sigmoid threshold of 0.5. Runtime, weight revisions/hashes, script hashes, split hashes, status summaries, features, and head outputs are collected under `/kaggle/working`.
''')
    template['cells'][1] = notebook_cell('code', '# Keep Kaggle\'s preinstalled torch, CUDA, and Triton unchanged.\n%pip install --no-deps timm==1.0.30 safetensors==0.8.0\n')
    template['cells'][2] = notebook_cell('code', setup_source)
    template['cells'][3] = notebook_cell('code', make_run_cell(models, payload))
    template['cells'][4] = notebook_cell('code', '''import hashlib, json, pathlib
work = pathlib.Path("/kaggle/working")
summary = json.loads((work / "public-eval-summary.json").read_text(encoding="utf-8"))
files = []
for path in sorted(work.rglob("*")):
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": str(path), "bytes": path.stat().st_size, "sha256": digest})
print(json.dumps({"status": summary["status"], "models": summary["feature_extractors"],
                  "heads": summary["heads"], "files": files}, ensure_ascii=False, indent=2))
''')

    for item in template['cells']:
        if item['cell_type'] == 'code':
            source = ''.join(item['source'])
            source = '\n'.join(line for line in source.splitlines() if not line.startswith('%pip '))
            ast.parse(source)
    embedded = json.loads(gzip.decompress(base64.b64decode(setup_payload(setup_source))))
    if embedded['script_sha256'] != script_hashes or embedded['split_manifests_sha256'] != payload['split_manifests_sha256']:
        raise ValueError('Notebook payload hashes do not match build inputs')
    for name, content in embedded['scripts'].items():
        if sha256(content.encode()) != embedded['script_sha256'][name]:
            raise ValueError(f'Embedded script hash mismatch: {name}')
    notebook_bytes = (json.dumps(template, ensure_ascii=False, indent=1) + '\n').encode()
    if len(notebook_bytes) >= 1024 * 1024:
        raise ValueError(f'Notebook source exceeds 1 MiB: {len(notebook_bytes)}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(notebook_bytes)
    receipt = {
        'notebook_path': str(args.output), 'notebook_bytes': len(notebook_bytes),
        'notebook_sha256': sha256(notebook_bytes), 'manifest_sha256': payload['manifest_sha256'],
        'bundle_sha256': bundle_sha, 'dataset_ref': args.dataset_ref,
        'split_manifests_sha256': payload['split_manifests_sha256'],
        'script_sha256': script_hashes, 'model_pins': pins, 'models': models,
        'validation': 'All code cells AST-parsed after removing the package-install magic; existing public builder verified ZIP safety, exact manifest/fold bytes, image references, and split index hashes; embedded code hashes were rechecked.',
    }
    receipt_path = args.output.parent / 'build-receipt.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--dataset-ref', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', default=','.join(DEFAULT_MODELS))
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
