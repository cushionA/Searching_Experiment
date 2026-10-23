"""Read-only public-source acquisition and static audit; never import candidate code.

Python 3.12 stdlib only. Responses go to an explicitly selected scratch directory.
No model weights, dependency installation, credentials, or model execution.
"""
import argparse
import ast
import datetime
import hashlib
import json
from pathlib import Path
import time
import urllib.request

CODE = 'b240a0da39a7287a41f81e82bddccd868ef4386d'
MODEL = 'a15cfd6d4db69640985a8c55844aa2e27b615796'
BASE = f'https://raw.githubusercontent.com/cxcscmu/ReScraper/{CODE}/'
URLS = {
    'repo.json': 'https://api.github.com/repos/cxcscmu/ReScraper',
    'tree.json': f'https://api.github.com/repos/cxcscmu/ReScraper/git/trees/{CODE}?recursive=1',
    'model.json': f'https://huggingface.co/api/models/cx-cmu/ReScraper/revision/{MODEL}?blobs=true',
    'model-card.txt': f'https://huggingface.co/cx-cmu/ReScraper/resolve/{MODEL}/README.md',
    'config.json': f'https://huggingface.co/cx-cmu/ReScraper/resolve/{MODEL}/models/ReScraper/config.json',
    'paper-abs.html': 'https://arxiv.org/abs/2609.34287',
    'paper.html': 'https://arxiv.org/html/2609.34287v1',
    'host-abs.html': 'https://arxiv.org/abs/2610.13321v1',
    'host-paper.html': 'https://arxiv.org/html/2610.13321v1',
}
for name, source in {
    'readme.txt': 'README.md', 'license.txt': 'LICENSE',
    'inference-readme.txt': 'inference/README.md',
    'ops.txt': 'lib/rescraper_ops.py', 'render.txt': 'lib/render.py',
    'infer.txt': 'inference/infer_pool.py', 'requirements.txt': 'requirements/refiner.txt',
}.items():
    URLS[name] = BASE + source


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--offline', action='store_true')
    a = p.parse_args()
    a.scratch.mkdir(parents=True, exist_ok=True)
    a.output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, url in URLS.items():
        start = time.perf_counter()
        row = {'name': name, 'url': url, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            if a.offline:
                data = (a.scratch / name).read_bytes()
            else:
                req = urllib.request.Request(url, headers={'User-Agent': 'SearchingExperiment-static-audit/1.0'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = response.read(4_000_001)
                    row['status'] = response.status
                if len(data) > 4_000_000:
                    raise ValueError('metadata response exceeds 4MB cap')
                (a.scratch / name).write_bytes(data)
            row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), error=None)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        row['seconds'] = round(time.perf_counter() - start, 6)
        manifest.append(row)
    (a.output / ('offline-manifest.json' if a.offline else 'manifest.json')).write_text(json.dumps(manifest, indent=2)+'\n')
    if any(r['error'] for r in manifest):
        raise SystemExit('Source fetch/read failed; audit not completed')
    read = lambda name: (a.scratch / name).read_text()
    model = json.loads(read('model.json'))
    repo = json.loads(read('repo.json'))
    tree = json.loads(read('tree.json'))
    ops = ast.parse(read('ops.txt'))
    functions = {n.name: n for n in ops.body if isinstance(n, ast.FunctionDef)}
    fallback = functions['_apply_edit_body'].body[-1]
    paths = [s['rfilename'] for s in model['siblings']]
    weights = [s for s in model['siblings'] if s['rfilename'].endswith('.safetensors')]
    results = {
        'kind': 'static_source_and_distribution_audit_not_inference',
        'code_revision': CODE, 'model_revision': MODEL,
        'repo': {k: repo.get(k) for k in ['pushed_at', 'stargazers_count', 'forks_count', 'license']},
        'tree_truncated': tree['truncated'],
        'model_files': paths,
        'safetensors_bytes': sum(s['size'] for s in weights),
        'safetensors': weights,
        'root_config_present': 'config.json' in paths,
        'nested_config_present': 'models/ReScraper/config.json' in paths,
        'config_architectures': json.loads(read('config.json')).get('architectures'),
        'edit_fallback_ast': ast.dump(fallback, include_attributes=False),
        'edit_fallback_is_raw_text_false': ast.unparse(fallback) == 'return (rest.strip(), False)',
        'tolerant_default_disabled_source': 'os.environ.get("OPS_TOLERANT", "") == "1"' in read('ops.txt'),
        'empty_main_html_skip_source': 'if not (r1.get("main_html") or "").strip(): S["no_main_html"] += 1; continue' in read('infer.txt'),
        'prompt_limit_source': 'MAX_LEN = 32768; MAX_NEW = 3072; MAX_PROMPT = MAX_LEN - MAX_NEW' in read('infer.txt'),
        'acquisition_success': len(manifest),
        'acquisition_bytes': sum(r['bytes'] for r in manifest),
        'acquisition_seconds': round(sum(r['seconds'] for r in manifest), 6),
        'candidate_executions': 0, 'model_download_bytes': 0, 'repairs': 0,
    }
    assert model['sha'] == MODEL
    assert not results['tree_truncated']
    assert results['edit_fallback_is_raw_text_false']
    assert results['empty_main_html_skip_source']
    assert results['prompt_limit_source']
    (a.output / ('offline-audit.json' if a.offline else 'audit.json')).write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps({k: results[k] for k in ['acquisition_success','acquisition_bytes','safetensors_bytes','edit_fallback_is_raw_text_false','candidate_executions']}))


if __name__ == '__main__':
    main()
