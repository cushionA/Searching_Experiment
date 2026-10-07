import csv
import datetime as dt
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name
GIT = '/mnt/c/Program Files/Git/cmd/git.exe'
SOURCE_COMMIT = '308e049668e6e3f98ab21dc22f3675eeb79de998'
FINGERPRINT_SHA256 = 'c3edfe8106e7a1e93e905ef3de654bb6262dcb55ba9e95d9f8d84ed06b81fb3f'
FIXTURE_EXPECTATIONS = {
    'fixture-complete-recheck': {'pages': 3, 'occurrences': 6, 'unique': 6, 'clicks': 2, 'complete': True,
        'stop': 'last_page_reached_and_reported_total_matched', 'statuses': [200, 200, 200]},
    'fixture-denied-recheck': {'pages': 1, 'occurrences': 2, 'unique': 2, 'clicks': 1, 'complete': False,
        'stop': 'http_403_on_page_2', 'statuses': [200, 403]},
    'fixture-no-progress-recheck': {'pages': 1, 'occurrences': 2, 'unique': 2, 'clicks': 1, 'complete': False,
        'stop': 'page_number_did_not_advance', 'statuses': [200, 200]},
    'fixture-duplicate-recheck': {'pages': 3, 'occurrences': 6, 'unique': 5, 'clicks': 2, 'complete': True,
        'stop': 'last_page_reached_and_reported_total_matched', 'statuses': [200, 200, 200]},
}
HISTORICAL_FIXTURES = ['fixture-complete', 'fixture-denied', 'fixture-no-progress', 'fixture-duplicate']
SOURCE_PATHS = [
    'experiments/bot-diagnostics/joshin-product-search.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.test.mjs',
    'experiments/bot-diagnostics/evidence.mjs',
    'experiments/bot-diagnostics/runtime.mjs',
    'experiments/bot-diagnostics/camoufox-runtime.mjs',
    'experiments/bot-diagnostics/camoufox-fourplay-runtime.mjs',
]
CJS_PATHS = [
    'experiments/fourget-selfhost/fourplay/tab-navigation.cjs',
    'experiments/fourget-selfhost/fourplay/navigation-gate.cjs',
]


def read(file):
    return json.loads(file.read_text(encoding='utf-8-sig'))


def write(file, value):
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def elapsed_seconds(start, end):
    first, last = timestamp(start), timestamp(end)
    return round((last - first).total_seconds(), 3) if first and last else None


def interval_summary(values):
    valid = sorted(value for value in values if value is not None)
    if not valid:
        return {'count': 0, 'mean_seconds': None, 'median_seconds': None, 'min_seconds': None,
            'max_seconds': None, 'p90_seconds': None, 'p90_method': 'linear interpolation, position=0.9*(n-1)'}
    position = 0.9 * (len(valid) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    p90 = valid[lower] + (valid[upper] - valid[lower]) * (position - lower)
    return {'count': len(valid), 'mean_seconds': round(statistics.mean(valid), 3),
        'median_seconds': round(statistics.median(valid), 3), 'min_seconds': round(valid[0], 3),
        'max_seconds': round(valid[-1], 3), 'p90_seconds': round(p90, 3),
        'p90_method': 'linear interpolation, position=0.9*(n-1)'}


def timestamp_intervals(values):
    parsed = [timestamp(value) for value in values]
    return [(current - previous).total_seconds() for previous, current in zip(parsed, parsed[1:])
        if previous is not None and current is not None]


def page_statuses(measurement):
    return [page.get('http_status') for page in measurement.get('search_pages', [])]


def environment_observation(measurement):
    pages = measurement.get('search_pages', [])
    environments = [page.get('environment') for page in pages if isinstance(page.get('environment'), dict)]
    snapshots = [page.get('snapshot') for page in pages if isinstance(page.get('snapshot'), dict)]
    values = environments or snapshots
    if not values:
        return {'observed': False, 'pages_with_environment': 0, 'all_match_ja_jst': None, 'samples': []}
    samples = []
    for value in values:
        sample = {key: value.get(key) for key in ['navigator_language', 'navigator_languages', 'intl_locale',
            'timezone', 'timezone_offset_minutes']}
        samples.append(sample)
    matched = all(sample['navigator_language'] == 'ja-JP'
        and sample['intl_locale'] == 'ja-JP' and sample['timezone'] == 'Asia/Tokyo'
        and isinstance(sample['timezone_offset_minutes'], dict)
        and all(sample['timezone_offset_minutes'].get(key) == -540 for key in ['now', 'jan_1_2026', 'jul_1_2026'])
        for sample in samples)
    return {'observed': True, 'pages_with_environment': len(samples), 'all_match_ja_jst': matched, 'samples': samples}


def session_observation(directory):
    ledger = read(directory / 'ledger.json')
    documents = [record for record in ledger.get('records', []) if record.get('kind') == 'document'
        and (record.get('stage') == 'homepage' or str(record.get('stage', '')).startswith('search-page-'))]
    search_records = [record for record in documents if str(record.get('stage', '')).startswith('search-page-')]
    cookie_sets = {tuple(record.get('cookie_names') or []) for record in search_records}
    top_level_values = [record.get('frame_is_active_top_level') for record in documents
        if 'frame_is_active_top_level' in record]
    top_level_ok = bool(top_level_values) and all(value is True for value in top_level_values)
    server_file = ROOT / f'{directory.name}-server/requests.json'
    server = read(server_file) if server_file.is_file() else []
    server_search = [request for request in server if request.get('path') == '/srhzs.html']
    server_ok = bool(server_search) and all(request.get('query_matches') is True
        and request.get('session_cookie_present') is True for request in server_search)
    if directory.name.startswith('fixture-'):
        maintained = server_ok
    else:
        maintained = bool(search_records) and top_level_ok
    return {'maintained': maintained, 'playwright_tab_container_ids': None,
        'frame_is_active_top_level_all_true': top_level_ok if top_level_values else None,
        'search_cookie_names': sorted(next(iter(cookie_sets))) if len(cookie_sets) == 1 else [],
        'fixture_server_search_requests': len(server_search) if directory.name.startswith('fixture-') else None,
        'fixture_query_and_session_cookie_all_true': server_ok if directory.name.startswith('fixture-') else None}


def summarize_run(name, fixture=False, validate_fixture=True):
    directory = ROOT / name
    measurement = read(directory / 'measurement.json')
    conditions = read(directory / 'conditions.json')
    products = read(directory / 'product-names.json') if (directory / 'product-names.json').is_file() else []
    with (directory / 'product-names.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != len(products):
        raise RuntimeError('CSV/JSON product row mismatch: ' + name)
    for csv_row, product in zip(csv_rows, products):
        if csv_row.get('product_name') != product.get('product_name') or csv_row.get('product_url') != product.get('product_url'):
            raise RuntimeError('CSV/JSON product content mismatch: ' + name)
    occurrence_count = measurement.get('total_product_occurrences')
    unique_count = measurement.get('unique_product_urls')
    if occurrence_count is not None and occurrence_count != len(products):
        raise RuntimeError('Measurement/product occurrence count mismatch: ' + name)
    if unique_count is not None and unique_count != len({product.get('product_url') for product in products}):
        raise RuntimeError('Measurement/product unique URL mismatch: ' + name)
    rows = measurement.get('search_pages', [])
    statuses = page_statuses(measurement)
    if fixture and validate_fixture:
        expected = FIXTURE_EXPECTATIONS[name]
        observed = {'pages': measurement.get('successful_result_pages'), 'occurrences': occurrence_count,
            'unique': unique_count, 'clicks': measurement.get('pagination_clicks'),
            'complete': measurement.get('pagination_complete'), 'stop': measurement.get('stop_reason'), 'statuses': statuses}
        if observed != expected:
            raise RuntimeError('Fixture expectation mismatch for ' + name + ': ' + json.dumps(observed, ensure_ascii=False))
    session = session_observation(directory)
    if fixture and validate_fixture and not session['maintained']:
        raise RuntimeError('Fixture query/session marker continuity failed: ' + name)
    if not fixture and not session['maintained']:
        raise RuntimeError('Live document sequence was not confirmed on the active top-level Playwright page')
    started = conditions.get('started_at')
    finished = measurement.get('finished_at')
    first_search = rows[0].get('document_observed_at') if rows else None
    last_search = rows[-1].get('document_observed_at') if rows else None
    actions = measurement.get('pagination_actions', [])
    by_page = {page.get('current_page') or page.get('page_no'): page.get('document_observed_at') for page in rows}
    click_from_document = [elapsed_seconds(by_page.get(action.get('from_page')), action.get('clicked_at')) for action in actions]
    click_to_click = timestamp_intervals([action.get('clicked_at') for action in actions])
    ledger = read(directory / 'ledger.json')
    request_starts = [record.get('started_at') for record in ledger.get('records', [])
        if record.get('kind') == 'document' and str(record.get('stage', '')).startswith('search-page-')]
    docs = [page.get('document_observed_at') for page in rows]
    pages = [{key: page.get(key) for key in ['attempted_page', 'expected_page', 'http_status', 'page_no', 'current_page',
        'max_page', 'displayed_total', 'displayed_range', 'extracted_count', 'extraction_error', 'next_available',
        'document_observed_at', 'environment']} for page in rows]
    return {'run': name, 'run_type': 'fixture' if fixture else 'live', 'homepage_status': (measurement.get('homepage') or {}).get('http_status'),
        'page_statuses': statuses, 'pages': pages, 'successful_result_pages': measurement.get('successful_result_pages'),
        'pagination_clicks': measurement.get('pagination_clicks'), 'pagination_actions': actions,
        'observed_max_page': measurement.get('max_page'), 'reported_catalog_total': measurement.get('catalog_total_reported'),
        'total_product_occurrences': occurrence_count, 'unique_product_urls': unique_count,
        'duplicate_product_urls': measurement.get('duplicate_product_urls'), 'pagination_complete': measurement.get('pagination_complete'),
        'stop_reason': measurement.get('stop_reason'), 'session': session, 'environment': environment_observation(measurement),
        'error': measurement.get('error'),
        'failure_class': 'runtime_or_control_error' if measurement.get('stop_reason') == 'experiment_error' else None,
        'waf_denial_observed': any(status == 403 for status in statuses),
        'elapsed_seconds': elapsed_seconds(started, finished), 'pagination_elapsed_seconds': elapsed_seconds(first_search, last_search),
        'intervals': {'request_start_to_request_start': interval_summary(timestamp_intervals(request_starts)),
            'document_observed_to_document_observed': interval_summary(timestamp_intervals(docs)),
            'document_to_click': interval_summary([value for value in click_from_document if value is not None]),
            'click_to_click': interval_summary(click_to_click)},
        'fingerprint_sha256': conditions.get('fingerprint_sha256'), 'regional_condition': conditions.get('regional_condition'),
        'products_file': 'product-names.json', 'csv_file': 'product-names.csv'}


def verify_run(name):
    result = subprocess.run(['node', str(REPO / 'experiments/bot-diagnostics/runner.mjs'), 'verify', str(ROOT / name)],
        cwd=REPO, capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    if not value.get('ok'):
        raise RuntimeError('Evidence verification failed: ' + name)
    return value


def verify_fixture_server_requests(name):
    requests = read(ROOT / f'{name}-server/requests.json')
    search = [request for request in requests if request.get('path') == '/srhzs.html']
    if not search or any(request.get('query_matches') is not True or request.get('session_cookie_present') is not True
        for request in search):
        raise RuntimeError('Fixture server did not confirm query and session cookie on every search request: ' + name)
    return {'search_requests': len(search), 'query_matches_all': True, 'session_cookie_present_all': True,
        'statuses': [request.get('status') for request in search]}


def read_zip_json(archive, name):
    return json.loads(archive.read(name))


def main():
    if DESTINATION.exists() and any(DESTINATION.iterdir()):
        raise RuntimeError('Delivery destination exists and is not empty')
    if subprocess.run([GIT, 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip() != SOURCE_COMMIT:
        raise RuntimeError('Source commit changed')
    run_names = [*FIXTURE_EXPECTATIONS, 'live-recheck', *HISTORICAL_FIXTURES, 'live']
    verification = {name: verify_run(name) for name in run_names}
    fixture_results = [summarize_run(name, True) for name in FIXTURE_EXPECTATIONS]
    server_checks = {name: verify_fixture_server_requests(name) for name in FIXTURE_EXPECTATIONS}
    historical_fixtures = [summarize_run(name, True, False) for name in HISTORICAL_FIXTURES]
    historical_live = summarize_run('live')
    live_dir = ROOT / 'live-recheck'
    live_conditions = read(live_dir / 'conditions.json')
    live_runtime = read(live_dir / 'runtime.json')
    live = summarize_run('live-recheck')
    if live_conditions.get('mode') != 'camoufox-ja' or live_conditions.get('proxy_configured') is not False:
        raise RuntimeError('Live run is not the requested direct Camoufox Japanese condition')
    if live_conditions.get('tls_verification') != 'enabled':
        raise RuntimeError('TLS verification is not enabled in live conditions')
    if live_conditions.get('browser_control') != 'Camoufox + Playwright':
        raise RuntimeError('Live browser control is not standalone Camoufox through Playwright')
    if live_runtime.get('engine') != 'firefox' or live_runtime.get('headless') is not False \
        or not live_runtime.get('display') or not live_runtime.get('camoufox_js') or not live_runtime.get('playwright_core'):
        raise RuntimeError('Live Camoufox runtime metadata is incomplete or not headful')
    if live_conditions.get('fingerprint_sha256') != FINGERPRINT_SHA256 \
        or live_conditions.get('regional_condition', {}).get('config_sha256') != FINGERPRINT_SHA256:
        raise RuntimeError('Live Camoufox fingerprint condition hash mismatch')
    runtime_hash = live_runtime.get('config_sha256')
    if runtime_hash is not None and runtime_hash != FINGERPRINT_SHA256:
        raise RuntimeError('Live Camoufox runtime fingerprint hash mismatch')
    chunks = read(live_dir / 'fingerprint.json')
    config_parts = [value for key, value in sorted(chunks.items(), key=lambda pair: int(pair[0].removeprefix('CAMOU_CONFIG_')))
        if key.startswith('CAMOU_CONFIG_')]
    config_hash = sha(json.dumps(json.loads(''.join(config_parts)), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    if config_hash != FINGERPRINT_SHA256:
        raise RuntimeError('Serialized fingerprint config hash mismatch')
    if live['environment']['observed'] and live['environment']['all_match_ja_jst'] is not True:
        raise RuntimeError('Observed browser language/timezone does not match ja-JP/JST')
    live['fingerprint_config_hash_recomputed'] = config_hash
    live['runtime'] = live_runtime
    live['request_tls_verification'] = live_conditions.get('tls_verification')
    live['proxy_configured'] = live_conditions.get('proxy_configured')
    old_measurement = read(REPO / 'lab-runs/joshin-assembled-pagination-20261008-0005/live/measurement.json')
    old_pages = old_measurement.get('search_pages', [])
    old_actions = old_measurement.get('pagination_actions', [])
    old_by_page = {page.get('current_page') or page.get('page_no'): page.get('document_observed_at') for page in old_pages}
    old_ledger = read(REPO / 'lab-runs/joshin-assembled-pagination-20261008-0005/live/ledger.json')
    old_request_starts = [record.get('started_at') for record in old_ledger.get('records', [])
        if record.get('kind') == 'document' and str(record.get('stage', '')).startswith('search-page-')]
    old_summary = {'successful_result_pages': old_measurement.get('successful_result_pages'),
        'pagination_clicks': old_measurement.get('pagination_clicks'), 'reported_catalog_total': old_measurement.get('catalog_total_reported'),
        'total_product_occurrences': old_measurement.get('total_product_occurrences'),
        'unique_product_urls': old_measurement.get('unique_product_urls'),
        'request_start_to_request_start': interval_summary(timestamp_intervals(old_request_starts)),
        'document_observed_to_document_observed': interval_summary(timestamp_intervals([page.get('document_observed_at') for page in old_pages])),
        'document_to_click': interval_summary([elapsed_seconds(old_by_page.get(action.get('from_page')), action.get('clicked_at'))
            for action in old_actions if elapsed_seconds(old_by_page.get(action.get('from_page')), action.get('clicked_at')) is not None]),
        'click_to_click': interval_summary(timestamp_intervals([action.get('clicked_at') for action in old_actions]))}
    last = live['pages'][-1] if live['pages'] else {}
    environment = live['environment']
    environment_text = (f"JA/JST実測: navigator={environment['samples'][0].get('navigator_language')}, "
        f"Intl={environment['samples'][0].get('intl_locale')}, timezone={environment['samples'][0].get('timezone')}, "
        f"offset={environment['samples'][0].get('timezone_offset_minutes', {}).get('now')}分; "
        f"観測ページ={environment['pages_with_environment']}, 全件一致={environment['all_match_ja_jst']}。"
        if environment.get('observed') and environment.get('samples') else 'JA/JST実測値は未観測。')
    request_interval_rows = []
    for label, summary in [('Camoufox', live['intervals']['request_start_to_request_start']),
        ('assembled', old_summary['request_start_to_request_start'])]:
        request_interval_rows.append(f"| {label} | {summary['count']} | {summary['mean_seconds']} | {summary['median_seconds']} | "
            f"{summary['min_seconds']} | {summary['max_seconds']} | {summary['p90_seconds']} |")
    report = ['# Joshin Camoufox pagination run', '',
        '検索語「グローブ」をCamoufox単独（Playwright制御、headful）で実行しました。条件はproxyなし、TLS検証有効、日本語/JST fingerprintです。', '',
        '## ライブ結果', '',
        f"HTTP status: {', '.join(map(str, live['page_statuses'])) if live['page_statuses'] else '未確認'}。成功ページ {live['successful_result_pages']}、最後の試行 {last.get('attempted_page')}（HTTP {last.get('http_status')}、表示 {last.get('current_page')}）、完全性 `{live['pagination_complete']}`、終了 `{live['stop_reason']}`。",
        f"DOM総数 {live['reported_catalog_total']}、商品行 {live['total_product_occurrences']}、ユニークURL {live['unique_product_urls']}、クリック {live['pagination_clicks']}。{environment_text}", '',
        f"初回liveは {historical_live['successful_result_pages']}ページ/{historical_live['total_product_occurrences']}商品行を取得後、`{historical_live['stop_reason']}`（{historical_live.get('error', {}).get('message') if historical_live.get('error') else '詳細なし'}）で終了しました。HTTP 403観測={historical_live['waf_denial_observed']}。これはruntime/control errorとして保持し、WAF拒否とは分類していません。最終結果にはlive-recheckを採用しました。", '',
        '検索request開始間隔（started_at連続差、秒）:', '',
        '| 条件 | 間隔数 | 平均 | 中央値 | 最短 | 最長 | p90 |', '|---|---:|---:|---:|---:|---:|---:|', *request_interval_rows, '',
        'p90は線形補間（position=0.9×(n−1)）。document観測差とdocument→click／click→clickの統計はsummary.jsonに記録しました。',
        '旧runの比較元: `lab-runs/joshin-assembled-pagination-20261008-0005/live/measurement.json` と `ledger.json`。',
        f"旧assembled観測は {old_summary['reported_catalog_total']}件/{old_summary['successful_result_pages']}ページ/{old_summary['pagination_clicks']}クリック。homepage settle 6秒とready polling 150msは設定上の待機で、人工クリック間delayとは別です。DOM clickの`isTrusted`はfalseです。", '',
        'この速度比較は別条件・各run一回の観測で、差の原因や今後の成功を示すものではありません。',
        'Cookie値は保存していません。Cookie名のみ記録し、Playwrightのtab/container IDは対象外です。ページ文書のtop-level frame属性はledgerの観測として記載しています。', '',
        '## Fixture確認', '', '| 条件 | HTTP | 成功ページ | 商品行/unique | click | 終了 |', '|---|---|---:|---:|---:|---|']
    for result in fixture_results:
        report.append(f"| {result['run']} | {','.join(map(str, result['page_statuses']))} | {result['successful_result_pages']} | {result['total_product_occurrences']}/{result['unique_product_urls']} | {result['pagination_clicks']} | `{result['stop_reason']}` |")
    report.extend(['', '初回4 fixtureは履歴として保持し、suffix `-recheck` の4 fixtureを最終確認に採用しました。'])
    (ROOT / 'report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    summary = {'source_commit': SOURCE_COMMIT, 'target': 'https://joshinweb.jp/', 'query': 'グローブ',
        'browser': 'Camoufox controlled by Playwright; standalone, headful',
        'live_condition': 'camoufox-ja, direct/no proxy, TLS verification enabled', 'fingerprint_sha256': FINGERPRINT_SHA256,
        'regional_condition': live_conditions.get('regional_condition'), 'live': live,
        'history_failed_runtime_attempt': historical_live,
        'previous_assembled_run': {**old_summary, 'provenance': [
            'lab-runs/joshin-assembled-pagination-20261008-0005/live/measurement.json',
            'lab-runs/joshin-assembled-pagination-20261008-0005/live/ledger.json']},
        'fixtures': fixture_results, 'historical_fixtures': historical_fixtures,
        'fixture_server_checks': server_checks,
        'evidence_verification': verification, 'cookie_observation': 'Cookie values were not stored; only observed cookie names are retained.',
        'pagination_action': 'Observed anchor element.click(); click isTrusted=false.',
        'waits': {'artificial_inter_click_delay_ms': 0, 'homepage_settle_ms': 6000, 'readiness_poll_ms': 150},
        'products': ['product-names.json', 'product-names.csv'], 'report': 'report.md'}
    write(ROOT / 'summary.json', summary)
    for name in SOURCE_PATHS:
        target = ROOT / 'replay-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    for name in CJS_PATHS:
        target = ROOT / 'replay-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    patch = subprocess.run([GIT, 'diff', 'HEAD', '--', *SOURCE_PATHS, *CJS_PATHS], cwd=REPO, capture_output=True, check=True).stdout
    (ROOT / 'source.patch').write_bytes(patch)
    exported = ROOT / 'checkpoint-export.zip'
    export_result = subprocess.run(['python3', '-B', str(REPO / 'experiments/bot-diagnostics/export.py'),
        '--run', str(ROOT), '--output', str(exported)], cwd=REPO, capture_output=True, text=True, check=True)
    export_metadata = json.loads(export_result.stdout)
    checkpoint = ROOT / 'checkpoint.zip'
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(checkpoint, 'w', compression=zipfile.ZIP_DEFLATED) as target:
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
    restored_verification = {}
    source_import_verification = None
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Checkpoint CRC verification failed')
        for name, digest in manifest.items():
            if sha(archive.read(name)) != digest:
                raise RuntimeError('Checkpoint SHA256 mismatch: ' + name)
        with tempfile.TemporaryDirectory(prefix='joshin-camoufox-checkpoint-') as temporary:
            archive.extractall(temporary)
            restored = Path(temporary)
            for name in details['verification']:
                checked = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/runner.mjs'),
                    'verify', str(restored / name)], cwd=restored, capture_output=True, text=True, check=True)
                value = json.loads(checked.stdout)
                if not value.get('ok'):
                    raise RuntimeError('Restored evidence verification failed: ' + name)
                restored_verification[name] = value
            entry = subprocess.run(['node', str(restored / 'experiments/bot-diagnostics/joshin-product-search.mjs')],
                cwd=restored, capture_output=True, text=True)
            entry_output = entry.stdout + entry.stderr
            if entry.returncode == 0 or 'Usage:' not in entry_output or 'MODULE_NOT_FOUND' in entry_output:
                raise RuntimeError('Restored search entry import verification failed: ' + entry_output[-2000:])
            source_import_verification = {'exit_code': entry.returncode, 'usage_error_observed': True,
                'module_not_found': False}
    checkpoint_metadata = {'standard_export': export_metadata, 'files': len(manifest), 'bytes': checkpoint.stat().st_size,
        'sha256': sha(checkpoint.read_bytes()), 'crc_and_manifest_verified': True,
        'restored_verification': restored_verification, 'source_import_verification': source_import_verification}
    write(ROOT / 'checkpoint.json', checkpoint_metadata)
    shutil.copytree(ROOT, DESTINATION, dirs_exist_ok=DESTINATION.exists(),
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes()) != sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checkpoint checksum differs')
    print(json.dumps({'destination': str(DESTINATION), 'fixture_runs': len(fixture_results),
        'live_pages': len(live.get('pages', [])), 'verified_evidence_records': len(restored_verification),
        'checkpoint_sha256': checkpoint_metadata['sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
