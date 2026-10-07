import csv
import datetime as dt
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import tempfile
import urllib.parse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name
GIT = '/mnt/c/Program Files/Git/cmd/git.exe'
SOURCE_COMMIT = '308e049668e6e3f98ab21dc22f3675eeb79de998'
FIXTURES = {
    'fixture-ja-complete': {'mode': 'patchright-ja-fixture', 'pages': 3, 'rows': 6, 'unique': 6,
        'clicks': 2, 'complete': True, 'stop': 'last_page_reached_and_reported_total_matched', 'statuses': [200, 200, 200]},
    'fixture-ja-denied': {'mode': 'patchright-ja-fixture', 'pages': 1, 'rows': 2, 'unique': 2,
        'clicks': 1, 'complete': False, 'stop': 'http_403_on_page_2', 'statuses': [200, 403]},
    'fixture-en-complete': {'mode': 'patchright-en-fixture', 'pages': 1, 'rows': 2, 'unique': 2,
        'clicks': 0, 'complete': False, 'stop': 'requested_page_limit_reached', 'statuses': [200]},
}
LIVE_RUNS = {'live-ja': {'mode': 'patchright-ja', 'locale': 'ja-JP', 'limit': 3},
    'live-en': {'mode': 'patchright-en', 'locale': 'en-US', 'limit': 1}}
RUNS = [*FIXTURES, *LIVE_RUNS]
SOURCE_PATHS = [
    'experiments/bot-diagnostics/joshin-product-search.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.test.mjs',
    'experiments/bot-diagnostics/evidence.mjs',
    'experiments/bot-diagnostics/runtime.mjs',
    'experiments/bot-diagnostics/runner.mjs',
    'experiments/bot-diagnostics/observations.mjs',
    'experiments/bot-diagnostics/lightpanda-runtime.mjs',
    'experiments/bot-diagnostics/lightpanda-shims.mjs',
    'experiments/bot-diagnostics/obscura-runtime.mjs',
    'experiments/bot-diagnostics/chromium-trust.mjs',
    'experiments/bot-diagnostics/dom-stability.mjs',
    'experiments/bot-diagnostics/fourplay-runtime.mjs',
    'experiments/bot-diagnostics/fourplay-native-runtime.mjs',
    'experiments/bot-diagnostics/camoufox-runtime.mjs',
    'experiments/bot-diagnostics/camoufox-fourplay-runtime.mjs',
]
CJS_PATHS = ['experiments/fourget-selfhost/fourplay/tab-navigation.cjs',
    'experiments/fourget-selfhost/fourplay/navigation-gate.cjs']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parsed_time(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def intervals(values):
    parsed = [parsed_time(value) for value in values]
    return [(b - a).total_seconds() for a, b in zip(parsed, parsed[1:]) if a is not None and b is not None]


def interval_summary(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {'count': 0, 'mean_seconds': None, 'median_seconds': None, 'min_seconds': None,
            'max_seconds': None, 'p90_seconds': None, 'p90_method': 'linear interpolation, position=0.9*(n-1)'}
    position = 0.9 * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    p90 = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {'count': len(ordered), 'mean_seconds': round(statistics.mean(ordered), 3),
        'median_seconds': round(statistics.median(ordered), 3), 'min_seconds': round(ordered[0], 3),
        'max_seconds': round(ordered[-1], 3), 'p90_seconds': round(p90, 3),
        'p90_method': 'linear interpolation, position=0.9*(n-1)'}


def verify_run(name):
    result = subprocess.run(['node', str(REPO / 'experiments/bot-diagnostics/runner.mjs'), 'verify', str(ROOT / name)],
        cwd=REPO, capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    if value.get('ok') is not True:
        raise RuntimeError('Evidence verification failed: ' + name)
    return value


def check_products(directory, measurement):
    products = read(directory / 'product-names.json') if (directory / 'product-names.json').exists() else []
    with (directory / 'product-names.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != len(products):
        raise RuntimeError('CSV/JSON product row mismatch: ' + directory.name)
    for csv_row, product in zip(csv_rows, products):
        if csv_row.get('product_name') != product.get('product_name') or csv_row.get('product_url') != product.get('product_url'):
            raise RuntimeError('CSV/JSON product content mismatch: ' + directory.name)
    total = measurement.get('total_product_occurrences')
    unique = measurement.get('unique_product_urls')
    actual_unique = len({product.get('product_url') for product in products})
    if total is not None and total != len(products):
        raise RuntimeError('Product occurrence count mismatch: ' + directory.name)
    if unique is not None and unique != actual_unique:
        raise RuntimeError('Unique URL count mismatch: ' + directory.name)
    return products, total, unique


def environment_summary(measurement, expected_locale):
    observations = []
    if isinstance(measurement.get('homepage', {}).get('environment'), dict):
        observations.append(measurement['homepage']['environment'])
    observations.extend(page['environment'] for page in measurement.get('search_pages', [])
        if isinstance(page.get('environment'), dict))
    fields_match = lambda env: env.get('navigator_language') == expected_locale \
        and (env.get('navigator_languages') or [None])[0] == expected_locale \
        and env.get('intl_locale') == expected_locale and env.get('timezone') == 'Asia/Tokyo' \
        and isinstance(env.get('timezone_offset_minutes'), dict) \
        and all(env['timezone_offset_minutes'].get(key) == -540 for key in ['now', 'jan_1_2026', 'jul_1_2026'])
    return {'observed': bool(observations), 'pages_observed': len(observations),
        'all_match_expected_locale_and_jst': all(fields_match(value) for value in observations) if observations else None,
        'samples': [{key: value.get(key) for key in ['navigator_language', 'navigator_languages', 'intl_locale',
            'timezone', 'timezone_offset_minutes']} for value in observations]}


def document_observation(directory, measurement):
    ledger = read(directory / 'ledger.json')
    documents = [record for record in ledger.get('records', []) if record.get('kind') == 'document'
        and (record.get('stage') == 'homepage' or str(record.get('stage', '')).startswith('search-page-'))]
    search_documents = [record for record in documents if str(record.get('stage', '')).startswith('search-page-')]
    top_level = [record.get('frame_is_active_top_level') for record in documents
        if 'frame_is_active_top_level' in record]
    page_ids = {record.get('tab_id') for record in documents}
    containers = {record.get('container') for record in documents}
    origins = [page.get('document_time_origin') for page in measurement.get('search_pages', [])]
    observed_origins = [value for value in origins if isinstance(value, (int, float))]
    cookie_name_set = {name for record in documents for name in (record.get('cookie_names') or [])}
    cookie_name_set.update(name for page in [measurement.get('homepage')] + measurement.get('search_pages', [])
        if isinstance(page, dict) for name in (page.get('cookie_names') or []))
    return {'document_requests': len(documents), 'search_document_requests': len(search_documents),
        'frame_is_active_top_level_all_true': all(value is True for value in top_level) if top_level else None,
        'tab_ids_all_null': all(value is None for value in page_ids) if page_ids else None,
        'container_ids_all_null': all(value is None for value in containers) if containers else None,
        'playwright_page_model': 'one retained browser.page; request frame is checked as active top-level',
        'document_time_origin_values': origins, 'numeric_time_origin_count': len(observed_origins),
        'unique_numeric_time_origin_count': len(set(observed_origins)), 'cookie_names_only': sorted(cookie_name_set)}


def fixture_server_check(name):
    requests = read(ROOT / f'{name}-server/requests.json')
    search = [item for item in requests if item.get('path') == '/srhzs.html']
    if not search or any(item.get('query_matches') is not True or item.get('session_cookie_present') is not True for item in search):
        raise RuntimeError('Fixture server did not confirm query/session on every search request: ' + name)
    return {'search_requests': len(search), 'query_matches_all': True, 'session_cookie_present_all': True,
        'statuses': [item.get('status') for item in search]}


def summarize_run(name, fixture=False):
    directory = ROOT / name
    measurement = read(directory / 'measurement.json')
    conditions = read(directory / 'conditions.json')
    runtime = read(directory / 'runtime.json')
    products, count, unique = check_products(directory, measurement)
    statuses = [page.get('http_status') for page in measurement.get('search_pages', [])]
    action_times = [action.get('clicked_at') for action in measurement.get('pagination_actions', [])]
    ledger = read(directory / 'ledger.json')
    request_times = [record.get('started_at') for record in ledger.get('records', [])
        if record.get('kind') == 'document' and str(record.get('stage', '')).startswith('search-page-')]
    environment = environment_summary(measurement, 'ja-JP' if 'ja' in name else 'en-US')
    page_observation = document_observation(directory, measurement)
    submission = read(directory / 'submission.json')
    if fixture:
        expected = FIXTURES[name]
        actual = {'mode': measurement.get('mode'), 'successful_pages': measurement.get('successful_result_pages'),
            'rows': count, 'unique': unique, 'clicks': measurement.get('pagination_clicks'),
            'complete': measurement.get('pagination_complete'), 'stop': measurement.get('stop_reason'), 'statuses': statuses}
        wanted = {'mode': expected['mode'], 'successful_pages': expected['pages'], 'rows': expected['rows'],
            'unique': expected['unique'], 'clicks': expected['clicks'], 'complete': expected['complete'],
            'stop': expected['stop'], 'statuses': expected['statuses']}
        if actual != wanted:
            raise RuntimeError(f'Fixture result mismatch {name}: {json.dumps(actual, ensure_ascii=False)}')
        if not environment['observed'] or environment['all_match_expected_locale_and_jst'] is not True:
            raise RuntimeError('Fixture locale/timezone observation mismatch: ' + name)
        if not page_observation['frame_is_active_top_level_all_true']:
            raise RuntimeError('Fixture top-level frame observation mismatch: ' + name)
    patchright_condition = conditions.get('patchright_condition') or {}
    submission_url = urllib.parse.urlparse(submission.get('form_action', ''))
    if submission.get('input_value') != 'グローブ' or submission_url.path != '/srhzs.html' \
        or str(submission.get('form_method', '')).lower() != 'get':
        raise RuntimeError('Search submission parameters mismatch: ' + name)
    if not fixture and (submission.get('document_charset') != 'Shift_JIS' or submission.get('accept_charset') not in ('', None)):
        raise RuntimeError('Live search form charset observations mismatch: ' + name)
    if conditions.get('mode') != (FIXTURES[name]['mode'] if fixture else LIVE_RUNS[name]['mode']):
        raise RuntimeError('Unexpected browser mode: ' + name)
    if conditions.get('proxy_configured') is not False or conditions.get('tls_verification') != 'enabled':
        raise RuntimeError('Direct/TLS condition mismatch: ' + name)
    if conditions.get('browser_control') != 'Patchright + Playwright':
        raise RuntimeError('Browser is not Patchright controlled through Playwright: ' + name)
    if conditions.get('fingerprint_sha256') is not None or patchright_condition.get('camoufox_fingerprint_applied') is not False:
        raise RuntimeError('Unexpected Camoufox fingerprint condition: ' + name)
    if patchright_condition.get('timezone') != 'Asia/Tokyo' or patchright_condition.get('tls_verification') != 'enabled':
        raise RuntimeError('Patchright timezone/TLS configuration mismatch: ' + name)
    expected_locale = 'en-US' if '-en' in name else 'ja-JP'
    if patchright_condition.get('locale') != expected_locale:
        raise RuntimeError('Patchright locale configuration mismatch: ' + name)
    if runtime.get('headless') is not False or not runtime.get('display') or not runtime.get('version') \
        or 'chromium' not in str(runtime.get('executable', '')).lower():
        raise RuntimeError('Actual headful Chromium runtime metadata is incomplete: ' + name)
    if not page_observation['frame_is_active_top_level_all_true']:
        raise RuntimeError('Browser document sequence was not observed on the active top-level page: ' + name)
    if page_observation['tab_ids_all_null'] is not True or page_observation['container_ids_all_null'] is not True:
        raise RuntimeError('Patchright tab/container IDs were unexpectedly present: ' + name)
    if environment['observed'] and environment['all_match_expected_locale_and_jst'] is not True:
        raise RuntimeError('Observed locale/timezone mismatch: ' + name)
    if not fixture:
        if patchright_condition.get('requested_page_limit') != LIVE_RUNS[name]['limit']:
            raise RuntimeError('Requested page limit metadata mismatch: ' + name)
    return {'run': name, 'mode': measurement.get('mode'), 'homepage_status': (measurement.get('homepage') or {}).get('http_status'),
        'status_codes': statuses,
        'successful_result_pages': measurement.get('successful_result_pages'),
        'total_product_occurrences': count, 'unique_product_urls': unique,
        'pagination_clicks': measurement.get('pagination_clicks'),
        'catalog_total_reported': measurement.get('catalog_total_reported'), 'max_page': measurement.get('max_page'),
        'pagination_complete': measurement.get('pagination_complete'), 'stop_reason': measurement.get('stop_reason'),
        'environment': environment, 'document_observation': page_observation,
        'request_start_intervals': interval_summary(intervals(request_times)),
        'click_time_intervals': interval_summary(intervals(action_times)),
        'ledger_route_labels': sorted({record.get('route') for record in ledger.get('records', []) if record.get('route')}),
        'conditions': conditions, 'runtime': runtime,
        'submission': submission,
        'first_search_document': ({key: measurement['search_pages'][0].get(key) for key in
            ['http_status', 'title', 'text_prefix', 'document_time_origin']} if measurement.get('search_pages') else None),
        'error': measurement.get('error'), 'products_file': 'product-names.json', 'csv_file': 'product-names.csv'}


def read_zip_json(archive, name):
    return json.loads(archive.read(name))


def main():
    if DESTINATION.exists() and any(DESTINATION.iterdir()):
        raise RuntimeError('Delivery destination exists and is not empty')
    commit = subprocess.run([GIT, 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    if commit != SOURCE_COMMIT:
        raise RuntimeError('Source commit changed')
    verification = {name: verify_run(name) for name in RUNS}
    results = {name: summarize_run(name, name in FIXTURES) for name in RUNS}
    fixture_checks = {name: fixture_server_check(name) for name in FIXTURES}
    old_observations = {
        'direct_english_403': {'run': 'joshin-input-diagnostics-20261007-2215',
            'path': 'lab-runs/joshin-input-diagnostics-20261007-2215/live-dom-en/results.json',
            'observed': 'direct English-condition search returned HTTP 403'},
        'direct_japanese_200': {'run': 'joshin-input-diagnostics-20261007-2215',
            'path': 'lab-runs/joshin-input-diagnostics-20261007-2215/live-dom-ja/results.json',
            'observed': 'direct Japanese-condition search returned HTTP 200'},
        'proxy_japanese_403': {'run': 'joshin-proxy-ja-20261007-2340',
            'path': 'lab-runs/joshin-proxy-ja-20261007-2340/live-camoufox-ja/results.json',
            'observed': 'proxy Japanese-condition search returned HTTP 403'},
    }
    report = ['# Joshin Patchright smoke', '',
        'direct・headfulのPatchrightを日本語/JSTと英語/JSTで比較しました。各runは新しいbrowser sessionで、Cookie値は保存していません。', '',
        '| run | status | 成功ページ | 商品行/unique | click | complete | stop |', '|---|---|---:|---:|---:|---|---|']
    for name, result in results.items():
        row_count = result['total_product_occurrences'] if result['total_product_occurrences'] is not None else '未確定'
        unique_count = result['unique_product_urls'] if result['unique_product_urls'] is not None else '未確定'
        report.append(f"| {name} | {','.join(map(str, result['status_codes']))} | {result['successful_result_pages']} | "
            f"{row_count}/{unique_count} | {result['pagination_clicks']} | "
            f"{result['pagination_complete']} | `{result['stop_reason']}` |")
    report.extend(['', f"両liveのhomepageはHTTP 200。JA検索はHTTP 404でtitle「Joshin web | 家電とパソコンの大型専門店」、本文「サーバーが混み合っております。しばらく経ってからもう一度お試しください。」。EN検索はHTTP 403でAccess Denied/permission本文でした。両方で入力「グローブ」、action `/srhzs.html`、GET、Shift_JIS、accept_charset空を記録しました。表示文とstatusを報告し、原因は断定しません。",
        '両条件はdirect/no proxyで同じ設定を使い、IP addressは測定していません。未確定件数は0に置き換えていません。',
        '環境スナップショット、top-level frame、tab/container null、document timeOrigin、request開始間隔、実runtime詳細はsummary.jsonに保存しました。',
        '過去観測はdirect English 403／direct Japanese 200／proxy Japanese 403で、出典run名とファイルをsummaryに記録しました。この比較だけでIPだけが原因とは断定できません。',
        'live-ja/live-enはそれぞれ3ページ/1ページ上限のsmoke testです。上限到達はカタログ全件取得を意味しません。', ''])
    (ROOT / 'report.md').write_text('\n'.join(report), encoding='utf-8')
    summary = {'source_commit': SOURCE_COMMIT, 'query': 'グローブ', 'target': 'https://joshinweb.jp/',
        'browser': 'Patchright + Playwright, headful Chromium', 'condition': 'direct; TLS verification enabled; Asia/Tokyo',
        'runs': results, 'fixture_server_checks': fixture_checks, 'evidence_verification': verification,
        'historical_observations': old_observations,
        'interpretation_limit': 'Different locale, browser configuration, and prior proxy conditions do not isolate IP as the cause.',
        'network_path_limit': {'proxy_configured': False, 'public_ip_measured': False,
            'ledger_route_label_semantics': 'environment_proxy is a fixed Evidence.reserve label for non-loopback URLs, not an observed route or IP measurement.'},
        'requested_limits': {'live-ja': 3, 'live-en': 1}, 'report': 'report.md'}
    write(ROOT / 'summary.json', summary)
    for name in SOURCE_PATHS + CJS_PATHS:
        target = ROOT / 'replay-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    source_patch = subprocess.run([GIT, 'diff', 'HEAD', '--', *SOURCE_PATHS, *CJS_PATHS],
        cwd=REPO, capture_output=True, check=True).stdout
    (ROOT / 'source.patch').write_bytes(source_patch)
    export_path = ROOT / 'checkpoint-export.zip'
    export_result = subprocess.run(['python3', '-B', str(REPO / 'experiments/bot-diagnostics/export.py'),
        '--run', str(ROOT), '--output', str(export_path)], cwd=REPO, capture_output=True, text=True, check=True)
    export_metadata = json.loads(export_result.stdout)
    checkpoint = ROOT / 'checkpoint.zip'
    with zipfile.ZipFile(export_path) as source, zipfile.ZipFile(checkpoint, 'w', zipfile.ZIP_DEFLATED) as target:
        manifest = read_zip_json(source, 'SHA256.json')
        details = read_zip_json(source, 'CHECKPOINT.json')
        details['commit'] = SOURCE_COMMIT
        for name in source.namelist():
            if name not in {'SHA256.json', 'CHECKPOINT.json'}:
                target.writestr(name, source.read(name))
        for name in CJS_PATHS:
            data = (REPO / name).read_bytes()
            target.writestr(name, data)
            manifest[name] = sha(data)
        details['imported_navigation_helpers_included'] = True
        target.writestr('SHA256.json', json.dumps(manifest, indent=2) + '\n')
        target.writestr('CHECKPOINT.json', json.dumps(details, indent=2) + '\n')
    restored_checks = {}
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Checkpoint CRC verification failed')
        for name, digest in manifest.items():
            if sha(archive.read(name)) != digest:
                raise RuntimeError('Checkpoint SHA256 mismatch: ' + name)
        with tempfile.TemporaryDirectory(prefix='joshin-patchright-checkpoint-') as temporary:
            archive.extractall(temporary)
            restored = Path(temporary)
            for name in details['verification']:
                checked = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/runner.mjs'),
                    'verify', str(restored / name)], cwd=restored, capture_output=True, text=True, check=True)
                value = json.loads(checked.stdout)
                if value.get('ok') is not True:
                    raise RuntimeError('Restored evidence verification failed: ' + name)
                restored_checks[name] = value
            entry = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/joshin-product-search.mjs')],
                cwd=restored, capture_output=True, text=True)
            entry_output = entry.stdout + entry.stderr
            if entry.returncode == 0 or 'Usage:' not in entry_output or 'MODULE_NOT_FOUND' in entry_output:
                raise RuntimeError('Restored source imports failed: ' + entry_output[-2000:])
            source_import_check = {'exit_code': entry.returncode, 'usage_error_observed': True, 'module_not_found': False}
    checkpoint_info = {'standard_export': export_metadata, 'file_count': len(manifest), 'bytes': checkpoint.stat().st_size,
        'sha256': sha(checkpoint.read_bytes()), 'crc_and_manifest_verified': True,
        'restored_evidence_verification': restored_checks, 'source_import_verification': source_import_check}
    write(ROOT / 'checkpoint.json', checkpoint_info)
    shutil.copytree(ROOT, DESTINATION, dirs_exist_ok=DESTINATION.exists(), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes()) != sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checkpoint checksum mismatch')
    print(json.dumps({'destination': str(DESTINATION), 'runs': len(results), 'checkpoint_sha256': checkpoint_info['sha256']},
        ensure_ascii=False))


if __name__ == '__main__':
    main()
