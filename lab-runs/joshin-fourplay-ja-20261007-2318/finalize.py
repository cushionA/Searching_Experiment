import json
import runpy
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name
helpers = runpy.run_path(str(ROOT / 'report-helpers.py'))
read = helpers['read']
write = helpers['write']
sha = helpers['sha']


def main():
    if DESTINATION.exists():
        raise RuntimeError('Destination already exists')
    rows = helpers['summarize'](ROOT / 'live-fourplay-ja')
    fixture = read(ROOT / 'fixture-fourplay-ja-os/results.json')[0]
    if not fixture['verification']['ok'] or not all(fixture['fixture_verification'].values()):
        raise RuntimeError('Native 4play fixture failed')
    if not all(row['verification']['ok'] for row in rows):
        raise RuntimeError('Native 4play evidence failed')
    conditions = read(ROOT / 'live-fourplay-ja/conditions.json')
    measured = read(ROOT / 'live-fourplay-ja/01-dom-control/measurement.json')
    if conditions['fingerprint_sha256'] is not None or conditions['camoufox_metadata'] is not None:
        raise RuntimeError('Camoufox configuration present in native 4play')
    summary = {'source_commit': conditions['source_commit'], 'query': 'グローブ',
               'browser_control': 'Ordinary Firefox + native 4play, without Camoufox',
               'runtime': measured['runtime'], 'conditions': conditions, 'results': rows,
               'submission': 'DOM value assignment and synthetic input/change followed by DOM click; same method as the assembled and standalone Camoufox DOM language controls',
               'fixture_all_checks_passed': True,
               'previous_fixture': 'fixture-fourplay-ja failed only the Intl locale check; the OS regional locale was then installed into this run directory and forced in Firefox preferences',
               'assembled_evidence': '../joshin-input-diagnostics-20261007-2215/summary.json',
               'camoufox_evidence': '../joshin-camoufox-ja-20261007-2308/summary.json',
               'limitations': ['This is one ordinary Firefox observation, not an estimated success rate.',
                               'Product cards are verified offline from the captured DOM. Pagination was not traversed.',
                               'Firefox and Camoufox versions and installed fonts differ; this is not a one-variable comparison.',
                               'Cookie values and site-side bot scores are not recorded.',
                               'document.fonts.check and canvas widths alone do not establish which Japanese fonts are installed.',
                               'Legacy resource-block configuration is retained by the evidence framework, but this browser run loads site resources normally.']}
    write(ROOT / 'summary.json', summary)
    paths = ['experiments/bot-diagnostics/joshin-input-diagnostics.mjs',
             'experiments/fourget-selfhost/fourplay/tab-navigation.cjs',
             'experiments/fourget-selfhost/fourplay/navigation-gate.cjs']
    for name in paths:
        target = ROOT / 'replay-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    host_git = '/mnt/c/Program Files/Git/cmd/git.exe'
    commit = subprocess.run([host_git, 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    if commit != conditions['source_commit']:
        raise RuntimeError('Source commit changed')
    patch = subprocess.run([host_git, 'diff', '--no-index', '--', '/dev/null', paths[0]], cwd=REPO, capture_output=True)
    if patch.returncode != 1:
        raise RuntimeError('Driver patch failed')
    (ROOT / 'source.patch').write_bytes(patch.stdout)
    exported = ROOT / 'checkpoint-export.zip'
    result = subprocess.run(['python3', '-B', str(REPO / 'experiments/bot-diagnostics/export.py'),
                             '--run', str(ROOT), '--output', str(exported)], cwd=REPO,
                            capture_output=True, text=True, check=True)
    checkpoint = ROOT / 'checkpoint.zip'
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(checkpoint, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        manifest = json.loads(source.read('SHA256.json'))
        details = json.loads(source.read('CHECKPOINT.json'))
        details['commit'] = commit
        for name in source.namelist():
            if name not in {'SHA256.json', 'CHECKPOINT.json'}:
                target.writestr(name, source.read(name))
        for name in paths[1:]:
            data = (REPO / name).read_bytes()
            target.writestr(name, data)
            manifest[name] = sha(data)
        details['native_4play_navigation_helpers_included_for_shared_imports'] = True
        target.writestr('SHA256.json', json.dumps(manifest, indent=2) + '\n')
        target.writestr('CHECKPOINT.json', json.dumps(details, indent=2) + '\n')
    restored_verification = {}
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('CRC failed')
        if any(sha(archive.read(name)) != digest for name, digest in manifest.items()):
            raise RuntimeError('Manifest mismatch')
        with tempfile.TemporaryDirectory(prefix='joshin-fourplay-ja-') as temporary:
            archive.extractall(temporary)
            restored = Path(temporary)
            for directory in details['verification']:
                checked = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/runner.mjs'),
                                          'verify', str(restored / directory)], cwd=restored,
                                         capture_output=True, text=True, check=True)
                value = json.loads(checked.stdout)
                if not value['ok']:
                    raise RuntimeError('Restored run verification failed')
                restored_verification[directory] = value
    write(ROOT / 'checkpoint.json', {'standard_export': json.loads(result.stdout),
        'files': len(manifest), 'bytes': checkpoint.stat().st_size, 'sha256': sha(checkpoint.read_bytes()),
        'crc_and_manifest_verified': True, 'restored_verification': restored_verification})
    shutil.copytree(ROOT, DESTINATION, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes()) != sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checksum mismatch')
    print(json.dumps({'destination': str(DESTINATION), 'results': [{'status': row['search_status'],
        'products': row['product_dom_observation']} for row in rows],
        'verified_runs': len(restored_verification), 'checkpoint_sha256': sha(checkpoint.read_bytes())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
