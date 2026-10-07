import json
import importlib.util
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

helper_spec = importlib.util.spec_from_file_location('report_helpers', Path(__file__).resolve().with_name('report-helpers.py'))
report_helpers = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(report_helpers)
DESTINATION = report_helpers.DESTINATION
MODES = report_helpers.MODES
REPO = report_helpers.REPO
ROOT = report_helpers.ROOT
fingerprint = report_helpers.fingerprint
read = report_helpers.read
sha = report_helpers.sha
summarize = report_helpers.summarize
write = report_helpers.write


def validate_run(name, fixture):
    directory = ROOT / name
    if not (directory / 'results.json').is_file():
        raise RuntimeError('Missing run output: ' + name)
    conditions = read(directory / 'conditions.json')
    rows = summarize(directory)
    measured = read(directory / 'results.json')[0]
    if conditions.get('tls_verification_enabled') is not True:
        raise RuntimeError('TLS verification is not confirmed: ' + name)
    if not measured.get('verification', {}).get('ok'):
        raise RuntimeError('Evidence verification failed: ' + name)
    if fixture:
        verification = measured.get('fixture_verification') or {}
        expected = ['http_status_200', 'query_in_get', 'typed_events_trusted', 'key_events_trusted',
                    'pointer_movement_trusted', 'click_trusted', 'same_window', 'timezone_asia_tokyo',
                    'intl_locale_ja_jp', 'navigator_language_ja_jp', 'date_offset_minus_540',
                    'jan_1_2026_offset_minus_540', 'jul_1_2026_offset_minus_540',
                    'request_accept_language_ja_jp']
        if any(verification.get(key) is not True for key in expected):
            raise RuntimeError('Fixture checks failed: ' + name)
        if conditions.get('proxy_configured') is not False:
            raise RuntimeError('Fixture unexpectedly has a proxy configured: ' + name)
    else:
        if conditions.get('proxy_configured') is not True or conditions.get('proxy_scheme') != 'http':
            raise RuntimeError('Live run lacks the configured proxy transport: ' + name)
        if conditions.get('proxy_authentication_present') is not False:
            raise RuntimeError('Unexpected proxy authentication metadata: ' + name)
        if conditions.get('transport') != 'proxy_browser':
            raise RuntimeError('Unexpected live transport metadata: ' + name)
    return {'conditions': conditions, 'rows': rows, 'measurement': measured}


def read_zip_json(archive, name):
    return json.loads(archive.read(name))


def main():
    if DESTINATION.exists() and any(DESTINATION.iterdir()):
        raise RuntimeError('Destination already exists and is not empty')
    runs = {name: validate_run(name, name.startswith('fixture-')) for name in MODES}
    commits = {runs[name]['conditions'].get('source_commit') for name in MODES}
    if commits != {'308e049668e6e3f98ab21dc22f3675eeb79de998'}:
        raise RuntimeError('Source commit differs across runs')
    assembled_fp = fingerprint(ROOT / 'live-dom-ja')
    standalone_fp = fingerprint(ROOT / 'live-camoufox-ja')
    if assembled_fp != standalone_fp:
        raise RuntimeError('Camoufox fingerprint differs between the standalone and assembled runs')
    rows = [row for name in MODES for row in runs[name]['rows']]
    live_rows = [row for row in rows if row['run'].startswith('live-')]
    summary = {
        'source_commit': next(iter(commits)),
        'target': 'https://joshinweb.jp/',
        'query': 'グローブ',
        'transport': 'Proxy-enabled browser runs used the approved loopback Squid forward; TLS verification remained enabled. Fixture traffic stayed on loopback without proxy variables.',
        'browser_conditions': {name: runs[name]['conditions'].get('browser_control') for name in MODES},
        'proxy_conditions': {name: {'configured': runs[name]['conditions'].get('proxy_configured'),
            'scheme': runs[name]['conditions'].get('proxy_scheme'),
            'authentication_present': runs[name]['conditions'].get('proxy_authentication_present'),
            'route': runs[name]['conditions'].get('transport')} for name in MODES},
        'tls_verification_enabled': all(runs[name]['conditions'].get('tls_verification_enabled') is True for name in MODES),
        'results': rows,
        'live_status_and_product_observations': [{key: row.get(key) for key in
            ['run', 'search_status', 'search_success', 'result_dom_obtained', 'product_dom_observation', 'stop_reason']}
            for row in live_rows],
        'same_camoufox_fingerprint_standalone_and_assembled': True,
        'camoufox_fingerprint_sha256': runs['live-camoufox-ja']['conditions'].get('fingerprint_sha256'),
        'fixture_checks_passed': True,
        'proxy_route_observation': read(ROOT / 'infrastructure/route-observation.json'),
        'conclusion': 'The three browser paths were evaluated with proxy transport on live requests and loopback-only fixtures. HTTP status and product DOM evidence are reported separately; a 403 is not counted as zero products.',
        'limitations': [
            'One run per browser path does not estimate repeatability or population-level success rates.',
            'A 200 response with a product DOM confirms observable search output, not the site-side scoring rule.',
            'Fixture checks validate local browser input and regional settings; they do not establish live-site behavior.',
            'Browser conditions record proxy configuration presence, scheme, and authentication presence without endpoint or credentials; infrastructure evidence separately records the user-specified loopback endpoint and VM metadata.',
            'isTrusted identifies browser-dispatched events, not proof of a human operator.',
            'Squid access logs contain no CONNECT records. Browser proxy injection and the SSH tunnel are recorded, but an independent route check for each browser was not completed.',
        ],
        'prior_evidence': {'saved_path': '../joshin-input-diagnostics-20261007-2215/live',
            'runtime_path': '/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-input-20261007-2215/live',
            'use': 'The original assembled run supplied the reusable Camoufox fingerprint. Its direct-route results remain separate from these proxy runs.'},
    }
    write(ROOT / 'summary.json', summary)
    replay = ROOT / 'replay-source'
    replay.mkdir(exist_ok=True)
    paths = ['experiments/bot-diagnostics/joshin-input-diagnostics.mjs',
             'experiments/bot-diagnostics/joshin-product-search.mjs',
             'experiments/fourget-selfhost/fourplay/tab-navigation.cjs',
             'experiments/fourget-selfhost/fourplay/navigation-gate.cjs']
    for name in paths:
        target = replay / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    proxy_notes = replay / 'docs/proxy.md'
    proxy_notes.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / 'docs/proxy.md', proxy_notes)
    host_git = '/mnt/c/Program Files/Git/cmd/git.exe'
    commit = subprocess.run([host_git, 'rev-parse', 'HEAD'], cwd=REPO,
                            capture_output=True, text=True, check=True).stdout.strip()
    if commit != summary['source_commit']:
        raise RuntimeError('Source commit changed')
    existing_patch = subprocess.run([host_git, 'diff', 'HEAD', '--', paths[1]], cwd=REPO,
                                    capture_output=True, check=True).stdout
    driver_patch = subprocess.run([host_git, 'diff', '--no-index', '--', '/dev/null', paths[0]],
                                  cwd=REPO, capture_output=True)
    if driver_patch.returncode != 1:
        raise RuntimeError('Driver patch generation failed')
    (ROOT / 'source.patch').write_bytes(existing_patch + driver_patch.stdout)
    exported = ROOT / 'checkpoint-export.zip'
    result = subprocess.run(['python3', '-B', str(REPO / 'experiments/bot-diagnostics/export.py'),
        '--run', str(ROOT), '--output', str(exported)], cwd=REPO, capture_output=True,
        text=True, check=True)
    export_metadata = json.loads(result.stdout)
    checkpoint = ROOT / 'checkpoint.zip'
    cjs_paths = paths[2:]
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(checkpoint, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        manifest = read_zip_json(source, 'SHA256.json')
        details = read_zip_json(source, 'CHECKPOINT.json')
        details['commit'] = commit
        for name in source.namelist():
            if name not in {'SHA256.json', 'CHECKPOINT.json'}:
                target.writestr(name, source.read(name))
        for name in cjs_paths:
            data = (REPO / name).read_bytes()
            target.writestr(name, data)
            manifest[name] = sha(data)
        details['native_4play_navigation_helpers_included'] = True
        target.writestr('SHA256.json', json.dumps(manifest, indent=2) + '\n')
        target.writestr('CHECKPOINT.json', json.dumps(details, indent=2) + '\n')
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Checkpoint CRC verification failed')
        for name, digest in manifest.items():
            if sha(archive.read(name)) != digest:
                raise RuntimeError('Checkpoint SHA256 mismatch: ' + name)
        restored_verification = {}
        with tempfile.TemporaryDirectory(prefix='joshin-proxy-checkpoint-') as temporary:
            archive.extractall(temporary)
            restored = Path(temporary)
            for directory in details['verification']:
                checked = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/runner.mjs',),
                    'verify', str(restored / directory)], cwd=restored, capture_output=True, text=True, check=True)
                value = json.loads(checked.stdout)
                if not value['ok']:
                    raise RuntimeError('Restored evidence verification failed: ' + directory)
                restored_verification[directory] = value
    write(ROOT / 'checkpoint.json', {'standard_export': export_metadata, 'files': len(manifest),
        'bytes': checkpoint.stat().st_size, 'sha256': sha(checkpoint.read_bytes()),
        'crc_and_manifest_verified': True, 'restored_verification': restored_verification})
    shutil.copytree(ROOT, DESTINATION, dirs_exist_ok=DESTINATION.exists(), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes()) != sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checksum differs')
    print(json.dumps({'destination': str(DESTINATION), 'site_search_attempts': len(live_rows),
        'verified_evidence_runs': len(restored_verification),
        'checkpoint_sha256': sha(checkpoint.read_bytes())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
