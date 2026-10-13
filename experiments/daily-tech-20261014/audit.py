"""Static data/source audit, not execution of RapidOCR or browser/model inference."""
import hashlib
import json
from pathlib import Path
import platform
import resource
import time
import yaml

START = time.perf_counter()
ROOT = Path('benchmarks/daily-tech-20261014')
manifest = json.loads((ROOT / 'manifest.json').read_text())
for entry in manifest:
    data = (ROOT / entry['file']).read_bytes()
    assert len(data) == entry['bytes'], entry['file']
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['file']
models = yaml.safe_load((ROOT / 'rapidocr-models.yaml').read_text())
config = yaml.safe_load((ROOT / 'rapidocr-config.yaml').read_text())
routes = models['model_routes']['PP-OCRv6']
rows = []
for task in ['det', 'rec']:
    for size in ['tiny', 'small', 'medium']:
        for language in ['japan', 'ja', 'jp', 'en']:
            normalized = routes['aliases'].get(language, language)
            selected = [r['model_key'] for r in routes[task][size] if normalized in r['supported_langs']]
            rows.append(dict(task=task, size=size, input_language=language, normalized=normalized, model_keys=selected))
            assert bool(selected) == (size != 'tiny' or language == 'en')
license_text = (ROOT / 'rapidocr-license-notice.txt').read_text()
license_checks = []
for task in ['det', 'rec']:
    model = models['onnxruntime']['PP-OCRv6'][task][f'multi_PP-OCRv6_{task}_small']
    check = dict(task=task, registry_sha256=model['SHA256'], hash_in_license_notice=model['SHA256'] in license_text)
    assert check['hash_in_license_notice']
    license_checks.append(check)
find = (ROOT / 'find-release.txt').read_text()
assert find.index("ariaSnapshot({ mode: 'ai' })") < find.index("snapshot.split") < find.index('matchedLines.push')
mcp = json.loads((ROOT / 'mcp-package.json').read_text())
core = json.loads((ROOT / 'playwright-core-registry.json').read_text())
result = dict(
    kind='static_primary_source_audit_not_inference',
    python=platform.python_version(), pyyaml=yaml.__version__,
    verified_source_count=len(manifest), source_manifest_sha256=hashlib.sha256((ROOT/'manifest.json').read_bytes()).hexdigest(),
    rapidocr_version='3.10.0', routing_rows=rows, license_hash_checks=license_checks,
    defaults={k:{x:config[k][x] for x in ['engine_type','lang_type','model_type','ocr_version']} for k in ['Det','Rec']},
    browser_find=dict(core_git_head=core['gitHead'], full_snapshot_before_filter=True, performance_measured=False),
    node_engines=dict(mcp=mcp['engines'], dependency=core['engines']),
    candidate_inference_count=0, browser_execution_count=0,
    isolation_probe=dict(command='bwrap --unshare-all --die-with-parent --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp --clearenv /usr/bin/true',exit_code=1,stderr='bwrap: setting up uid map: Read-only file system',fallback_attempted=False),
    elapsed_seconds=round(time.perf_counter()-START,6),
    peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    errors=[], limits=['YAML and source inspection only; resolver not executed', 'No model artifacts downloaded; artifact bytes not hash-verified','No OCR accuracy, latency or RAM measurement'],
)
(ROOT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['verified_source_count','candidate_inference_count','elapsed_seconds','peak_rss_kib']},indent=2))
