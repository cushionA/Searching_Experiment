import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name


def read(file):
    return json.loads(file.read_text(encoding='utf-8-sig'))


def write(file, value):
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(directory):
    chunks = read(directory / 'fingerprint.json')
    return json.loads(''.join(chunks[key] for key in sorted(chunks, key=lambda key: int(key.removeprefix('CAMOU_CONFIG_')))))


class ProductPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = 0
        self.fields = {}

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == 'div' and 'search_container' in values.get('class', '').split():
            self.cards += 1
        if tag == 'input' and values.get('data-field') in {'HIT_COUNT', 'MAX_PAGE'}:
            number = values.get('data-value', '')
            if number.isdecimal():
                self.fields[values['data-field']] = int(number)


def product_observation(measured):
    search = measured.get('search') or {}
    if search.get('http_status') != 200:
        return None
    parser = ProductPage()
    parser.feed(search.get('dom', ''))
    displayed = re.search(r'([\d,]+)件中\s*(\d+)～(\d+)件', search.get('body_text', ''))
    observed = {'card_selector': 'div.search_container', 'card_count': parser.cards,
                'displayed_total': parser.fields.get('HIT_COUNT'),
                'maximum_page_metadata': parser.fields.get('MAX_PAGE'),
                'displayed_range': [int(displayed[2]), int(displayed[3])] if displayed else None,
                'pagination_traversed': False, 'whole_catalog_retrieved': False}
    if not parser.cards or not displayed or int(displayed[1].replace(',', '')) != observed['displayed_total']:
        raise RuntimeError('Successful search lacks corroborating product DOM: ' + measured['arm'])
    if parser.cards != int(displayed[3]) - int(displayed[2]) + 1:
        raise RuntimeError('Product card count disagrees with display range')
    return observed


def summarize(directory):
    result = []
    for measured in read(directory / 'results.json'):
        arm = directory / f"{measured['attempt']:02d}-{measured['arm']}"
        records = read(arm / 'ledger.json')['records']
        search = next((record for record in records if record['kind'] == 'document' and record['stage'] == 'search'), None)
        trace = read(arm / 'trace.json')
        events = {}
        for event in trace:
            count = events.setdefault(event['type'], {'count': 0, 'trusted': 0})
            count['count'] += 1
            count['trusted'] += event['isTrusted'] is True
        environment = measured['homepage'].get('environment') if measured['homepage'] else None
        result.append({
            'run': directory.name,
            'arm': measured['arm'],
            'homepage_status': measured['homepage'].get('http_status') if measured['homepage'] else None,
            'search_status': measured['search'].get('http_status') if measured['search'] else None,
            'search_success': measured['search_success'],
            'products': measured['products'],
            'product_dom_observation': product_observation(measured),
            'stop_reason': measured['stop_reason'],
            'verification': measured['verification'],
            'events': events,
            'search_request_headers': search.get('request_headers') if search else None,
            'search_cookie_names': search.get('cookie_names') if search else None,
            'environment': environment,
            'measurement': str(arm.relative_to(ROOT) / 'measurement.json'),
        })
    return result


def main():
    if DESTINATION.exists():
        raise RuntimeError('Destination already exists')
    rows = []
    for name in ['live', 'live-recheck', 'live-ja', 'live-dom-ja', 'live-dom-en', 'live-dom-ja-repeat']:
        if not (ROOT / name / 'results.json').exists():
            continue
        rows.extend(summarize(ROOT / name))
    original = fingerprint(ROOT / 'live')
    repeated = fingerprint(ROOT / 'live-recheck')
    regional = fingerprint(ROOT / 'live-ja')
    differences = {key: {'before': original.get(key), 'after': regional.get(key)}
                   for key in original.keys() | regional.keys() if original.get(key) != regional.get(key)}
    if original != repeated:
        raise RuntimeError('Repeat fingerprint changed')
    if set(differences) != {'timezone', 'locale:language', 'locale:region'}:
        raise RuntimeError('Unexpected regional fingerprint difference')
    if not all(row['verification']['ok'] for row in rows):
        raise RuntimeError('Measurement verification failed')
    language_comparison = {}
    if (ROOT / 'live-dom-ja/fingerprint.json').exists() and (ROOT / 'live-dom-en/fingerprint.json').exists():
        japanese_dom = fingerprint(ROOT / 'live-dom-ja')
        english_dom = fingerprint(ROOT / 'live-dom-en')
        changed = {key: {'en': english_dom.get(key), 'ja': japanese_dom.get(key)}
                   for key in english_dom.keys() | japanese_dom.keys() if english_dom.get(key) != japanese_dom.get(key)}
        if set(changed) != {'locale:language', 'locale:region'}:
            raise RuntimeError('DOM language controls changed other fingerprint fields')
        language_comparison = {'changed_keys_only': changed, 'same_timezone_config': japanese_dom['timezone'] == english_dom['timezone'],
                               'sequence': [{'run': row['run'], 'status': row['search_status']} for row in rows
                                            if row['run'] in {'live-dom-ja', 'live-dom-en', 'live-dom-ja-repeat'}]}
    summary = {
        'source_commit': '308e049668e6e3f98ab21dc22f3675eeb79de998',
        'target': 'https://joshinweb.jp/',
        'query': 'グローブ',
        'transport': 'direct browser, no proxy; TLS verification retained',
        'browser': 'Camoufox 152.0.4-beta.30 + native 4play WebExtension, headful in a dedicated Xvfb display',
        'method': 'First combine offset clicks, curved pointer paths, slow native Unicode input and a small scroll pair; remove one factor at a time only after a successful result.',
        'fresh_profiles': 'Separate profile per arm; cookies retained between homepage and search within each arm.',
        'results': rows,
        'same_fingerprint_for_initial_and_repeat': True,
        'regional_fingerprint_changes_only': differences,
        'regional_condition': read(ROOT / 'live-ja/conditions.json')['regional_condition'],
        'regional_fonts_unchanged': original['fonts'] == regional['fonts'],
        'dom_language_control': language_comparison,
        'ablation_performed': any(row['arm'] in ['center-click', 'straight-movement', 'fast-typing', 'no-scroll'] for row in rows),
        'conclusion': 'Japanese regional settings returned HTTP 200 with a verified product result DOM after all four extra interaction factors were removed. The DOM controls preserve the timezone and all other fingerprint fields while changing only locale language and region; consult their recorded JA/EN/JA status sequence. This does not expose the site-side scoring rule.',
        'font_system_observation': read(ROOT / 'system-font-observation.json'),
        'validation': {'lab_tests': '151 tests, OK, skipped=34', 'native_unicode_fixture': 'fixture-typing-check passed', 'japanese_environment_fixture': 'fixture-ja-flat passed'},
        'prior_evidence': {'path': '../joshin-search-direct-20261007-2148/summary.json', 'use': 'Earlier separate Camoufox, standard 4play and assembled comparison; not merged into the new observations.'},
        'limitations': [
            'Initial live request headers were overwritten by response headers in the recorder. Use live-recheck for header and environment claims; the initial status, bodies, events and cookie names remain available.',
            'isTrusted identifies browser-dispatched events, not proof of a human operator.',
            'Representative installed-font resolution, canvas widths and Japanese screenshots do not validate every font or glyph.',
            'Cookie names and actual transmission are recorded; values and server-side bot scores are not.',
            'Same-origin POST/XHR responses of 201 do not establish a successful anti-bot assessment.',
            'Small sequential observations do not establish causal attribution or population-level success rates.',
            'Setup and fixture failures are preserved separately and are not counted as site search failures.',
            'Native 4play does not expose response headers; request correlation uses tab, method, type, URL and arrival order.',
            'Earlier Japanese fixtures used an incorrect nested locale object. They cannot establish a Camoufox locale defect. The final regional condition uses the supported flat locale:language and locale:region keys.',
            'Evidence config.json retains legacy resource_block defaults. This driver does not install routing or resource blocking; conditions.json and browser_observation policy describe the actual run.',
        ],
        'primary_sources': [
            {'url': 'https://dom.spec.whatwg.org/#dom-event-istrusted', 'claim': 'Browser and synthetic event provenance'},
            {'url': 'https://www.w3.org/TR/fetch-metadata/#sec-fetch-user-header', 'claim': 'User-activation navigation metadata'},
            {'url': 'https://techdocs.akamai.com/cloud-security/docs/detection-methods', 'claim': 'General behavioral detection capability, not attribution of this site response'},
            {'url': 'https://camoufox.com/python/geoip/', 'claim': 'GeoIP automatic regional configuration; disabled for this test'},
            {'url': 'https://www.rfc-editor.org/rfc/rfc6265.html', 'claim': 'Cookie attributes have no standard timezone field'},
        ],
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
    host_git = '/mnt/c/Program Files/Git/cmd/git.exe'
    commit = subprocess.run([host_git, 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    if commit != summary['source_commit']:
        raise RuntimeError('Source commit changed')
    old_patch = subprocess.run([host_git, 'diff', 'HEAD', '--', paths[1]], cwd=REPO, capture_output=True, check=True).stdout
    new_patch = subprocess.run([host_git, 'diff', '--no-index', '--', '/dev/null', paths[0]], cwd=REPO, capture_output=True)
    if new_patch.returncode != 1:
        raise RuntimeError('New driver patch generation failed')
    (ROOT / 'source.patch').write_bytes(old_patch + new_patch.stdout)
    exported = ROOT / 'checkpoint-export.zip'
    result = subprocess.run(['python3', '-B', str(REPO / 'experiments/bot-diagnostics/export.py'),
                             '--run', str(ROOT), '--output', str(exported)], cwd=REPO,
                            capture_output=True, text=True, check=True)
    export_metadata = json.loads(result.stdout)
    checkpoint = ROOT / 'checkpoint.zip'
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(checkpoint, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        manifest = read_zip_json(source, 'SHA256.json')
        details = read_zip_json(source, 'CHECKPOINT.json')
        details['commit'] = commit
        for name in source.namelist():
            if name not in {'SHA256.json', 'CHECKPOINT.json'}:
                target.writestr(name, source.read(name))
        for name in paths[2:]:
            data = (REPO / name).read_bytes()
            target.writestr(name, data)
            manifest[name] = sha(data)
        details['native_4play_navigation_helpers_included'] = True
        target.writestr('SHA256.json', json.dumps(manifest, indent=2) + '\n')
        target.writestr('CHECKPOINT.json', json.dumps(details, indent=2) + '\n')
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Checkpoint CRC failed')
        for name, digest in manifest.items():
            if sha(archive.read(name)) != digest:
                raise RuntimeError('Checkpoint hash mismatch: ' + name)
        restored_verification = {}
        with tempfile.TemporaryDirectory(prefix='joshin-input-checkpoint-') as temporary:
            archive.extractall(temporary)
            restored = Path(temporary)
            for directory in details['verification']:
                checked = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/runner.mjs'),
                                          'verify', str(restored / directory)], cwd=restored,
                                         capture_output=True, text=True, check=True)
                value = json.loads(checked.stdout)
                if not value['ok']:
                    raise RuntimeError('Restored evidence verification failed: ' + directory)
                restored_verification[directory] = value
    write(ROOT / 'checkpoint.json', {'standard_export': export_metadata, 'files': len(manifest),
        'bytes': checkpoint.stat().st_size, 'sha256': sha(checkpoint.read_bytes()),
        'crc_and_manifest_verified': True, 'restored_verification': restored_verification})
    shutil.copytree(ROOT, DESTINATION, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes()) != sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checksum differs')
    print(json.dumps({'destination': str(DESTINATION), 'site_search_attempts': len(rows),
        'verified_evidence_runs': len(restored_verification), 'checkpoint_sha256': sha(checkpoint.read_bytes()),
        'ablation_performed': summary['ablation_performed']}, ensure_ascii=False))


def read_zip_json(archive, name):
    return json.loads(archive.read(name))


if __name__ == '__main__':
    main()
