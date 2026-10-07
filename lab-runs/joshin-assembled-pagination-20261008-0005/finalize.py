import csv
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DESTINATION = Path('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs') / ROOT.name
SOURCE_COMMIT = '308e049668e6e3f98ab21dc22f3675eeb79de998'
FINGERPRINT_SHA256 = 'c3edfe8106e7a1e93e905ef3de654bb6262dcb55ba9e95d9f8d84ed06b81fb3f'
FIXTURE_EXPECTATIONS = {
    'fixture-complete-recheck-final': {'pages':3,'occurrences':6,'unique':6,'clicks':2,'complete':True,
        'stop':'last_page_reached_and_reported_total_matched','statuses':[200,200,200]},
    'fixture-denied': {'pages':1,'occurrences':2,'unique':2,'clicks':1,'complete':False,
        'stop':'http_403_on_page_2','statuses':[200,403]},
    'fixture-no-progress': {'pages':1,'occurrences':2,'unique':2,'clicks':1,'complete':False,
        'stop':'page_number_did_not_advance','statuses':[200,200]},
    'fixture-duplicate': {'pages':3,'occurrences':6,'unique':5,'clicks':2,'complete':True,
        'stop':'last_page_reached_and_reported_total_matched','statuses':[200,200,200]},
}
SOURCE_PATHS = [
    'experiments/bot-diagnostics/joshin-product-search.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.mjs',
    'experiments/bot-diagnostics/joshin-product-extraction.test.mjs',
    'experiments/bot-diagnostics/evidence.mjs',
    'experiments/bot-diagnostics/runtime.mjs',
    'experiments/bot-diagnostics/camoufox-fourplay-runtime.mjs',
    'experiments/bot-diagnostics/fourplay-native-runtime.mjs',
]
CJS_PATHS = ['experiments/fourget-selfhost/fourplay/tab-navigation.cjs',
             'experiments/fourget-selfhost/fourplay/navigation-gate.cjs']


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
    return round((last-first).total_seconds(), 3) if first and last else None


def source_commit():
    return subprocess.run(['/mnt/c/Program Files/Git/cmd/git.exe','rev-parse','HEAD'],cwd=REPO,capture_output=True,text=True,check=True).stdout.strip()


def page_statuses(measurement):
    return [page.get('http_status') for page in measurement.get('search_pages', [])]


def session_observation(directory, measurement):
    ledger = read(directory / 'ledger.json')
    documents = [record for record in ledger.get('records', []) if record.get('kind') == 'document'
                 and (record.get('stage') == 'homepage' or str(record.get('stage', '')).startswith('search-page-'))]
    tab_ids = {record.get('tab_id') for record in documents}
    containers = {record.get('container') for record in documents}
    search_records = [record for record in documents if str(record.get('stage', '')).startswith('search-page-')]
    cookie_sets = {tuple(record.get('cookie_names') or []) for record in search_records}
    same_tab_container = len(documents) > 0 and len(tab_ids) == 1 and None not in tab_ids and len(containers) == 1 and None not in containers
    browser_cookie_continuity = bool(search_records) and len(cookie_sets) == 1 and bool(next(iter(cookie_sets)))
    server_file = ROOT / f'{directory.name}-server/requests.json'
    server = read(server_file) if server_file.is_file() else []
    server_search = [request for request in server if request.get('path') == '/srhzs.html']
    server_cookie_continuity = bool(server_search) and all(request.get('query_matches') is True
        and request.get('session_cookie_present') is True for request in server_search)
    if directory.name.startswith('fixture-'):
        maintained = server_cookie_continuity and same_tab_container
    else:
        maintained = browser_cookie_continuity and same_tab_container
    return {'maintained':maintained,'same_tab_container':same_tab_container,
        'tab_ids':sorted(str(value) for value in tab_ids if value is not None),
        'containers':sorted(str(value) for value in containers if value is not None),
        'search_cookie_names':sorted(next(iter(cookie_sets))) if len(cookie_sets) == 1 else [],
        'fixture_server_search_requests':len(server_search) if directory.name.startswith('fixture-') else None,
        'fixture_query_and_session_cookie_all_true':server_cookie_continuity if directory.name.startswith('fixture-') else None}


def summarize_run(name, fixture=False, validate_fixture=True):
    directory = ROOT / name
    measurement = read(directory / 'measurement.json')
    conditions = read(directory / 'conditions.json')
    product_file = directory / 'product-names.json'
    products = read(product_file) if product_file.is_file() else []
    csv_path = directory / 'product-names.csv'
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != len(products):
        raise RuntimeError('CSV/JSON product row mismatch: ' + name)
    for csv_row, product in zip(csv_rows, products):
        if csv_row.get('product_name') != product.get('product_name') or csv_row.get('product_url') != product.get('product_url'):
            raise RuntimeError('CSV/JSON product content mismatch: ' + name)
        if product.get('price_text') is not None and not isinstance(product.get('price_text'), str):
            raise RuntimeError('Price text is not a string: ' + name)
    occurrence_count = measurement.get('total_product_occurrences')
    unique_count = measurement.get('unique_product_urls')
    duplicate_urls = measurement.get('duplicate_product_urls')
    if occurrence_count is not None and occurrence_count != len(products):
        raise RuntimeError('Measurement/product occurrence count mismatch: ' + name)
    if unique_count is not None and unique_count != len({product.get('product_url') for product in products}):
        raise RuntimeError('Measurement/product unique URL mismatch: ' + name)
    rows = measurement.get('search_pages', [])
    statuses = page_statuses(measurement)
    if fixture and validate_fixture:
        expected = FIXTURE_EXPECTATIONS[name]
        observed = {'pages':measurement.get('successful_result_pages'),'occurrences':occurrence_count,
            'unique':unique_count,'clicks':measurement.get('pagination_clicks'),
            'complete':measurement.get('pagination_complete'),'stop':measurement.get('stop_reason'),'statuses':statuses}
        if observed != expected:
            raise RuntimeError('Fixture expectation mismatch for ' + name + ': ' + json.dumps(observed,ensure_ascii=False))
    session = session_observation(directory, measurement)
    if fixture and validate_fixture and not session['maintained']:
        raise RuntimeError('Fixture query/session marker or tab/container continuity failed: ' + name)
    if not fixture and not session['same_tab_container']:
        raise RuntimeError('Live document tab/container continuity failed')
    started = conditions.get('started_at')
    finished = measurement.get('finished_at')
    first_search = rows[0].get('document_observed_at') if rows else None
    last_search = rows[-1].get('document_observed_at') if rows else None
    pages = [{key:page.get(key) for key in ['attempted_page','expected_page','http_status','page_no','current_page',
        'max_page','displayed_total','displayed_range','extracted_count','extraction_error','next_available',
        'document_observed_at']} for page in rows]
    return {'run':name,'run_type':'fixture' if fixture else 'live','homepage_status':(measurement.get('homepage') or {}).get('http_status'),
        'page_statuses':statuses,'pages':pages,'successful_result_pages':measurement.get('successful_result_pages'),
        'pagination_clicks':measurement.get('pagination_clicks'),'pagination_actions':measurement.get('pagination_actions',[]),
        'observed_max_page':measurement.get('max_page'),'reported_catalog_total':measurement.get('catalog_total_reported'),
        'total_product_occurrences':occurrence_count,'unique_product_urls':unique_count,
        'duplicate_product_urls':duplicate_urls,'pagination_complete':measurement.get('pagination_complete'),
        'stop_reason':measurement.get('stop_reason'),'session':session,
        'elapsed_seconds':elapsed_seconds(started,finished),'pagination_elapsed_seconds':elapsed_seconds(first_search,last_search),
        'fingerprint_sha256':conditions.get('fingerprint_sha256'),'regional_condition':conditions.get('regional_condition'),
        'products_file':'product-names.json','csv_file':'product-names.csv'}


def verify_fixture_server_requests(name):
    file = ROOT / f'{name}-server/requests.json'
    requests = read(file)
    search = [request for request in requests if request.get('path') == '/srhzs.html']
    if not search or any(request.get('query_matches') is not True or request.get('session_cookie_present') is not True for request in search):
        raise RuntimeError('Fixture server did not confirm query and session cookie on every search request: ' + name)
    return {'search_requests':len(search),'query_matches_all':True,'session_cookie_present_all':True,
        'statuses':[request.get('status') for request in search]}


def read_zip_json(archive, name):
    return json.loads(archive.read(name))


def main():
    if DESTINATION.exists() and any(DESTINATION.iterdir()):
        raise RuntimeError('Delivery destination exists and is not empty')
    if source_commit() != SOURCE_COMMIT:
        raise RuntimeError('Source commit changed')
    fixture_results = [summarize_run(name,True) for name in FIXTURE_EXPECTATIONS]
    for name in FIXTURE_EXPECTATIONS:
        verify_fixture_server_requests(name)
    historical = []
    for name,note in [('fixture-complete','初回fixtureのextraction_error記録不具合を含む旧試行。'),
        ('fixture-complete-recheck','baseURI境界修正前の旧recheck。最終確認ではなく履歴として保持。')]:
        entry = summarize_run(name,True,False)
        entry['adopted_as_primary'] = False
        entry['note'] = note
        historical.append(entry)
    live_dir = ROOT / 'live'
    live_conditions = read(live_dir / 'conditions.json')
    live_runtime = read(live_dir / 'runtime.json')
    live = summarize_run('live',False)
    if live_conditions.get('mode') != 'assembled-ja' or live_conditions.get('proxy_configured') is not False:
        raise RuntimeError('Live run is not the requested direct assembled Japanese condition')
    if live_conditions.get('tls_verification') != 'enabled':
        raise RuntimeError('TLS verification is not enabled in live conditions')
    if live_conditions.get('fingerprint_sha256') != FINGERPRINT_SHA256 \
        or live_conditions.get('regional_condition',{}).get('config_sha256') != FINGERPRINT_SHA256 \
        or live_runtime.get('config_sha256') != FINGERPRINT_SHA256:
        raise RuntimeError('Live Camoufox fingerprint metadata mismatch')
    fingerprint_chunks = read(live_dir / 'fingerprint.json')
    config_parts = [value for key,value in sorted(fingerprint_chunks.items(),key=lambda pair:int(pair[0].removeprefix('CAMOU_CONFIG_')))
        if key.startswith('CAMOU_CONFIG_')]
    fingerprint_config = json.loads(''.join(config_parts))
    config_hash = sha(json.dumps(fingerprint_config,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    if config_hash != FINGERPRINT_SHA256:
        raise RuntimeError('Serialized fingerprint config hash mismatch')
    live['fingerprint_config_hash_recomputed'] = config_hash
    live['request_tls_verification'] = live_conditions.get('tls_verification')
    live['proxy_configured'] = live_conditions.get('proxy_configured')
    statuses = live['page_statuses']
    last_attempted = live['pages'][-1] if live.get('pages') else {}
    report = [
        '# Joshin 検索ページ送り診断',
        '',
        '検索語は「グローブ」。Camoufoxとnative 4playを同じprofile/tabで使い、観測済みのページ送りanchorをクリックし、DOMの`MAX_PAGE`に従ってページ送りを実行しました。ライブはproxyなし、日本語/JST fingerprint、TLS検証有効です。',
        '',
        '## ライブ観測',
        '',
        f"検索ページHTTP status: {', '.join(str(value) for value in statuses) if statuses else '未確認'}。",
        f"成功ページ数: {live.get('successful_result_pages')}。最後の試行ページ: {last_attempted.get('attempted_page')} (HTTP {last_attempted.get('http_status')}, 表示ページ {last_attempted.get('current_page')})。pagination_complete={live.get('pagination_complete')}。DOMのMAX_PAGE={live.get('observed_max_page')}、終了状態=`{live.get('stop_reason')}`。",
        f"商品行: {live.get('total_product_occurrences') if live.get('total_product_occurrences') is not None else '未確定'}、ユニークURL: {live.get('unique_product_urls') if live.get('unique_product_urls') is not None else '未確定'}、ページ送りクリック: {live.get('pagination_clicks')}。経過: {live.get('elapsed_seconds')}秒。",
        '',
        'ページ別status、範囲、件数、時刻は`summary.json`に保存しました。Cookie値は保存していません。tab/containerとrequest cookie名の観測は同じブラウザ文脈の継続を示しますが、cookie値やサーバ側session内容の一致までは確認していません。',
        'ページ送りは観測済みanchorへのelement.click()によるDOM操作で、click eventのisTrustedはfalseです。人間の操作との同等性や今後拒否されないことは示しません。操作間の人工delayは追加せず、homepageの6秒待機とdocument response/complete待ちを使いました。',
        '',
        '## Fixture確認',
        '',
        '| 条件 | HTTP | 成功ページ | 商品行/unique | click | 終了 |',
        '|---|---|---:|---:|---:|---|',
    ]
    for result in fixture_results:
        report.append(f"| {result['run']} | {','.join(map(str,result['page_statuses']))} | {result['successful_result_pages']} | {result['total_product_occurrences']}/{result['unique_product_urls']} | {result['pagination_clicks']} | `{result['stop_reason']}` |")
    report.extend(['','JANを示す確定フィールドは観測されていません。URL末尾13桁は`product_code_candidate`として保持し、JANとは断定していません。価格は元の文字列でCSV/JSONに保存しました。',
        '', '`fixture-complete-recheck-final`を最終fixtureとして採用しました。旧`fixture-complete`と旧`fixture-complete-recheck`は履歴としてexportに保持し、最終判定には使いません。', ''])
    (ROOT / 'report.md').write_text('\n'.join(report),encoding='utf-8')
    summary = {'source_commit':SOURCE_COMMIT,'target':'https://joshinweb.jp/','query':'グローブ',
        'browser':'Camoufox + native 4play WebExtension','live_condition':'assembled-ja, direct/no proxy, TLS verification enabled',
        'fingerprint_sha256':FINGERPRINT_SHA256,'regional_condition':live_conditions.get('regional_condition'),
        'live':live,'live_observed_completion':{'successful_result_pages':live.get('successful_result_pages'),
            'last_attempted_page':last_attempted.get('attempted_page'),'last_attempted_page_status':last_attempted.get('http_status'),
            'last_displayed_page':last_attempted.get('current_page'),'pagination_complete':live.get('pagination_complete'),
            'observed_max_page':live.get('observed_max_page'),'stop_reason':live.get('stop_reason')},
        'fixtures':fixture_results,'historical_fixtures':historical,
        'fixture_server_checks':{name:verify_fixture_server_requests(name) for name in FIXTURE_EXPECTATIONS},
        'reported_results':'403/challenge/no-progress are retained as stop outcomes and are never converted to zero products.',
        'pagination_action':'Observed anchor element.click() DOM action; click isTrusted=false.',
        'cookie_observation':'Cookie values were not stored. Request cookie names and common tab/container are evidence of browser-context continuity only; they do not prove cookie-value or server-side session equivalence.',
        'artificial_delay':'No artificial inter-click delay; the existing homepage settle and document response/complete waits remain.',
        'product_identifier_note':'No observed JAN field. A 13-digit URL suffix is recorded only as product_code_candidate.',
        'products':['product-names.json','product-names.csv'],'report':'report.md',
        'previous_direct_search_evidence':{'saved_path':'../joshin-input-diagnostics-20261007-2215/live',
            'runtime_path':'/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-input-20261007-2215/live',
            'use':'Original fingerprint source; prior search evidence remains a separate run.'}}
    write(ROOT / 'summary.json',summary)
    replay = ROOT / 'replay-source'
    for name in SOURCE_PATHS:
        target = replay / name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(REPO / name,target)
    for name in CJS_PATHS:
        target = replay / name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(REPO / name,target)
    source_patch = subprocess.run(['/mnt/c/Program Files/Git/cmd/git.exe','diff','HEAD','--',*SOURCE_PATHS],cwd=REPO,capture_output=True,check=True).stdout
    (ROOT / 'source.patch').write_bytes(source_patch)
    exported = ROOT / 'checkpoint-export.zip'
    export_result = subprocess.run(['python3','-B',str(REPO / 'experiments/bot-diagnostics/export.py'),
        '--run',str(ROOT),'--output',str(exported)],cwd=REPO,capture_output=True,text=True,check=True)
    export_metadata = json.loads(export_result.stdout)
    checkpoint = ROOT / 'checkpoint.zip'
    with zipfile.ZipFile(exported) as source, zipfile.ZipFile(checkpoint,'w',compression=zipfile.ZIP_DEFLATED) as target:
        manifest = read_zip_json(source,'SHA256.json')
        details = read_zip_json(source,'CHECKPOINT.json')
        details['commit'] = SOURCE_COMMIT
        for name in source.namelist():
            if name not in {'SHA256.json','CHECKPOINT.json'}:
                target.writestr(name,source.read(name))
        for name in CJS_PATHS:
            data=(REPO / name).read_bytes()
            target.writestr(name,data)
            manifest[name]=sha(data)
        details['native_4play_navigation_helpers_included']=True
        target.writestr('SHA256.json',json.dumps(manifest,indent=2)+'\n')
        target.writestr('CHECKPOINT.json',json.dumps(details,indent=2)+'\n')
    with zipfile.ZipFile(checkpoint) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Checkpoint CRC verification failed')
        for name,digest in manifest.items():
            if sha(archive.read(name))!=digest:
                raise RuntimeError('Checkpoint SHA256 mismatch: '+name)
        restored_verification={}
        with tempfile.TemporaryDirectory(prefix='joshin-pagination-checkpoint-') as temporary:
            archive.extractall(temporary)
            restored=Path(temporary)
            for directory in details['verification']:
                checked=subprocess.run(['node',str(restored / 'experiments/bot-diagnostics/runner.mjs'),
                    'verify',str(restored / directory)],cwd=restored,capture_output=True,text=True,check=True)
                value=json.loads(checked.stdout)
                if not value['ok']:
                    raise RuntimeError('Restored evidence verification failed: '+directory)
                restored_verification[directory]=value
    checkpoint_metadata={'standard_export':export_metadata,'files':len(manifest),'bytes':checkpoint.stat().st_size,
        'sha256':sha(checkpoint.read_bytes()),'crc_and_manifest_verified':True,'restored_verification':restored_verification}
    write(ROOT / 'checkpoint.json',checkpoint_metadata)
    shutil.copytree(ROOT,DESTINATION,dirs_exist_ok=DESTINATION.exists(),ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if sha((DESTINATION / 'checkpoint.zip').read_bytes())!=sha(checkpoint.read_bytes()):
        raise RuntimeError('Destination checksum differs')
    print(json.dumps({'destination':str(DESTINATION),'fixture_runs':len(fixture_results),'live_pages':len(live.get('pages',[])),
        'verified_evidence_records':len(restored_verification),'checkpoint_sha256':checkpoint_metadata['sha256']},ensure_ascii=False))


if __name__ == '__main__':
    main()