"""Offline evidence verification and artifact-size planning, not model inference."""
import ast
import hashlib
import json
import platform
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261018'
start = time.perf_counter()
checked = 0
source_bytes = 0
for folder in ['primary', 'supplement', 'context']:
    for row in json.loads((OUT / folder / 'manifest.json').read_text()):
        if 'sha256' not in row:
            continue
        b = (OUT / folder / row['name']).read_bytes()
        assert len(b) == row['bytes']
        assert hashlib.sha256(b).hexdigest() == row['sha256']
        checked += 1
        source_bytes += len(b)
meta = json.loads((OUT / 'primary/smolvlm-model.json').read_text())
files = {s['rfilename']: s['size'] for s in meta['siblings']}
variants = {}
for suffix in ['', '_fp16', '_int8', '_q4', '_q4f16', '_bnb4', '_quantized', '_uint8']:
    names = ['onnx/' + part + suffix + '.onnx' for part in ['decoder_model_merged', 'embed_tokens', 'vision_encoder']]
    variants[suffix or 'fp32'] = {'files': names, 'component_bytes': [files[n] for n in names], 'total_bytes': sum(files[n] for n in names)}
family = json.loads((OUT / 'supplement/smolvlm-family.json').read_text())
result = {
    'kind': 'offline_metadata_audit_not_inference',
    'python': platform.python_version(),
    'source_documents_verified': checked,
    'source_payload_bytes': source_bytes,
    'model_id': meta['id'], 'revision': meta['sha'],
    'model_safetensors_bytes': files['model.safetensors'],
    'all_listed_files_bytes': sum(files.values()),
    'onnx_variant_plans': variants,
    'variant_plans_count': len(variants),
    'family_ids_checked': len(family),
    'family_ids': [x['id'] for x in family],
    'runtime_compatibility': 'unverified', 'peak_ram': None,
    'model_download_bytes': 0, 'inference_count': 0, 'repair_count': 0,
    'limitations': 'API listed sizes only; no graph loading, generation, tokenizer compatibility or quality measurement. Three ONNX components are a size plan, not a verified runnable package. Tokenizer/config/runtime dependencies are additional.',
}
for path in (ROOT / 'experiments/daily-tech-20261018').glob('*.py'):
    ast.parse(path.read_text())
result['wall_seconds'] = time.perf_counter() - start
(OUT / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: result[k] for k in ['source_documents_verified','source_payload_bytes','variant_plans_count','family_ids_checked','wall_seconds']}))
