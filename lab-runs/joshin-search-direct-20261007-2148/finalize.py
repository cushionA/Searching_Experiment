import hashlib
import json
import subprocess
from pathlib import Path


base = Path(__file__).resolve().parent
repo = base.parents[1]
arms = []
verification = {}
for mode, label in [('camoufox', 'Camoufox'), ('standard', '4play'), ('assembled', 'Camoufox + 4play')]:
    run = base / mode
    measurement = json.loads((run / 'measurement.json').read_text())
    conditions = json.loads((run / 'conditions.json').read_text())
    runtime = json.loads((run / 'runtime.json').read_text())
    ledger = json.loads((run / 'ledger.json').read_text())
    result = subprocess.run(['node', str(repo / 'experiments/bot-diagnostics/runner.mjs'), 'verify', str(run)],
                            capture_output=True, text=True, check=True)
    verified = json.loads(result.stdout)
    assert verified['ok']
    assert conditions['proxy_configured'] is False
    assert not any(conditions['proxy_environment_present'].values())
    assert runtime['proxy_ca_trusted'] is False
    assert measurement['homepage']['http_status'] == 200
    assert measurement['search_pages'][0]['http_status'] == 403
    assert measurement['stop_reason'] == 'http_403_on_first_search_page'
    assert measurement['extracted_products'] is None
    assert measurement['pagination_clicks'] == 0
    assert [item['status'] for item in ledger['records']] == [200, 403]
    submission = json.loads((run / 'submission.json').read_text())
    assert submission['input_value'] == 'グローブ'
    assert submission['icon_count'] == 1
    verification[mode] = verified
    arms.append({'mode': mode, 'label': label, 'browser_control': conditions['browser_control'],
                 'browser_version': runtime['version'], 'homepage_status': 200, 'search_status': 403,
                 'result_list_obtained': False, 'product_total': None, 'product_names_obtained': 0,
                 'pagination_clicks': 0, 'last_catalog_page': None,
                 'search_url': measurement['search_pages'][0]['url'],
                 'search_document_title': measurement['search_pages'][0]['title'],
                 'started_at': conditions['started_at'], 'finished_at': measurement['finished_at'],
                 'verification_ok': verified['ok'], 'run': mode})

fixtures = {}
for mode in ['4play', 'assembled']:
    fixture = json.loads((base / f'fixture-{mode}/summary.json').read_text())
    assert fixture['ok'] and all(fixture['assertions'].values())
    fixtures[mode] = {'ok': True, 'assertions': fixture['assertions']}

source_manifest = {str(item.relative_to(base / 'replay-source')): hashlib.sha256(item.read_bytes()).hexdigest()
                   for item in sorted((base / 'replay-source').rglob('*')) if item.is_file()}
(base / 'source-manifest.json').write_text(json.dumps(source_manifest, indent=2) + '\n')
(base / 'verification-all.json').write_text(json.dumps(verification, indent=2) + '\n')
summary = {'schema': 1, 'source_commit': conditions['source_commit'], 'source_local_changes': ['experiments/bot-diagnostics/joshin-product-search.mjs'],
           'source_patch': 'source.patch', 'query': 'グローブ', 'proxy_configured': False,
           'proxy_environment_cleared': True, 'tls_verification_enabled': True,
           'headful': True, 'display': ':0', 'concurrency': 'three sequential runs, fresh profiles, same-tab search submission',
           'homepage_settle_ms': 6000, 'actual_exit_ip_measured': False,
           'camoufox_fingerprints_reused_between_arms': False,
           'authorization': 'mainフェッチ後、camoufox・4play・アセンブル版のジョーシン検索テストをプロキシなしで続行するというユーザー指示。',
           'arms': arms, 'fixtures': fixtures,
           'tests': {'node': {'total': 33, 'passed': 33, 'failed': 0}, 'lab': {'total': 151, 'passed': 117, 'skipped': 34, 'failed': 0}},
           'count_semantics': '検索結果DOMは取得できず、商品総数は不明。取得商品名0件は一致商品0件を意味しない。',
           'previous_cloud_result': {'report': 'reports/2026-10/joshin-glove-search.md', 'proxy': 'Cloud proxy',
                                     'standard': [200, 403], 'assembled': [200, 403], 'adopted_as_current_measurement': False},
           'limits': ['各方式1試行。拒否の内部原因とWAFスコアは未確定。',
                      'Camoufoxと通常Firefoxはブラウザ版・fingerprintが異なり、制御方式単独の因果比較ではない。',
                      '4playの応答ヘッダーとスクリーンショットは未取得。',
                      '依存パッケージと実ブラウザbinaryはチェックポイントに含まない。'],
           'environment_incident': {'first_fixture_attempt': 'WSL restarted before fixture evidence was copied; empty log retained.',
                                    'first_log': 'validation/fixture-interrupted-4play.log',
                                    'completed_evidence': 'WSL persistent filesystem, then copied to Windows workspace'},
           'continuation_run': str(base), 'replay_sources': 'replay-source', 'checkpoint': 'checkpoint.zip'}
(base / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'arms': arms, 'verified': list(verification), 'replay_source_files': len(source_manifest)}, ensure_ascii=False))
