"""Offline continuation verification and derived checkpoint metadata."""
import collections
import csv
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
GROUPS = ('general', 'meta', 'ai-api', 'niche')
BASELINE = REPO / '.lab-output/search-services-20261003-robots-retry.zip'
AUTHORIZATION = '今は関係ないから全部無視でいいぞグラウンディングサーチの時だけ使う'

def read(path):
    return json.loads(path.read_text())

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def main():
    aggregate = read(BASE / 'aggregate-verification.json')
    if 'historical_verification' not in aggregate:
        aggregate = {'historical_verification': aggregate}
    checks, all_records, all_results = {}, [], []
    prefix, normal_checks = {}, {}
    with zipfile.ZipFile(BASELINE) as baseline:
        for group in GROUPS:
            root = BASE / group
            ledger, results = read(root / 'ledger.json'), read(root / 'results.json')
            arc = f'lab-runs/search-services-20261003/{group}'
            previous_ledger = json.loads(baseline.read(arc + '/ledger.json'))
            previous_results = json.loads(baseline.read(arc + '/results.json'))
            assert ledger['records'][:len(previous_ledger['records'])] == previous_ledger['records']
            assert results[:len(previous_results)] == previous_results
            assert (root / 'config.json').read_bytes() == baseline.read(arc + '/config.json')
            prefix[group] = {'records': len(previous_ledger['records']), 'results': len(previous_results), 'unchanged': True}
            verified = json.loads(subprocess.check_output(['node', str(REPO / 'experiments/bot-diagnostics/runner.mjs'), 'verify', str(root)], text=True))
            assert verified['ok']
            checks[group] = verified
            observation = read(root / 'browser-observation-results.json')
            assert all(x.get('finished_at') for x in observation)
            setup = [x for x in results[len(previous_results):] if x.get('outcome') == 'adapter_ready']
            assert setup and all(x.get('headless') is False for x in setup), (group, setup)
            forbidden = {'outside_scope', 'resource_policy', 'request_budget', 'byte_budget', 'non_get', 'robots_denied'}
            blocked = [b for x in results[len(previous_results):] for b in (x.get('blocked_requests') or [])]
            assert not any(x.get('reason') in forbidden for x in blocked), (group, blocked)
            normal = [x for x in ledger['records'] if x.get('policy') == 'browser_observation']
            assert normal and all(x['kind'] != 'robots' for x in normal)
            normal_checks[group] = {
                'finished_services': len(observation), 'adapter_setups_headful': len(setup),
                'legacy_policy_blocks': 0, 'normal_records': len(normal),
                'post_requests': sum(x.get('request_method') == 'POST' for x in normal),
                'image_requests': sum(x.get('resource_type') == 'image' for x in normal),
            }
            all_records += ledger['records']
            all_results += results
    fixtures = {}
    for group in ('robots-probe-fixture', 'browser-observation-fixture'):
        verified = json.loads(subprocess.check_output(['node', str(REPO / 'experiments/bot-diagnostics/runner.mjs'), 'verify', str(BASE / group)], text=True))
        assert verified['ok']
        fixtures[group] = verified
    summary = read(BASE / 'search-services-summary.json')['services']
    assert len(summary) == len({x['id'] for x in summary}) == 50
    with (BASE / 'search-services-summary.csv').open(encoding='utf-8-sig') as f:
        assert len(list(csv.DictReader(f))) == 50
    by_id = {x['id']: x for x in summary}
    brave = by_id['brave']
    assert brave['query_confirmed'] and brave['simple_challenge_passed']
    assert brave['query_status'] == '200' and len(brave['result_candidates']) == 10 and brave['waseda_domain_count'] == 9
    assert by_id['yandex']['query_status'] == '503' and not by_id['yandex']['query_confirmed']
    assert by_id['yandex']['visual_challenge_attempted']
    assert by_id['baidu']['query_confirmed'] and by_id['baidu']['result_candidates']
    assert all(by_id[k]['query_confirmed'] and not by_id[k]['result_candidates'] for k in ('marginalia', 'kiddle', 'wiby'))
    assert sum(x['browser_observation_done'] for x in summary) == 38
    aggregate.update({
        'ok': True, 'unique_services': 50, 'requests': len(all_records),
        'charged_body_bytes': sum(x['bytes_charged'] for x in all_records),
        'saved_results': len(all_results), 'groups': checks,
        'requests_by_policy': dict(collections.Counter(x.get('policy', 'grounding') for x in all_records)),
        'requests_status': dict(collections.Counter(str(x['status']) for x in all_records)),
        'browser_observation': {'authorization': AUTHORIZATION, 'policy': 'browser_observation',
            'legacy_limits_enforced': False, 'current_robots_stops': 0,
            'completed_rechecks': 38, 'confirmed_search_responses_including_zero': sum(x['query_confirmed'] for x in summary),
            'brave_verify_passed': True, 'yandex_visual_submission_status': 'environment_proxy_http_503_answer_unknown',
            'groups': normal_checks},
        'baseline': {'file': BASELINE.name, 'sha256': hashlib.sha256(BASELINE.read_bytes()).hexdigest(),
            'original_ledger_results_config_prefix': prefix},
        'local_fixtures': {'excluded_from_service_counts': True, 'verification': fixtures},
        'note': 'Normal browser observations append to unchanged historical ledgers. Stored response body accounting is not total transfer bytes. Old caps apply only to grounding records. HTTP/DOM evidence cannot establish sustained scraping stability.',
    })
    save(BASE / 'aggregate-verification.json', aggregate)
    scope = read(BASE / 'scope.json')
    if 'historical_initial_policy' not in scope:
        scope['historical_initial_policy'] = {k: scope.pop(k) for k in (
            'requests_per_service_limit', 'charged_body_bytes_per_service_limit',
            'aggregate_request_limit', 'aggregate_charged_body_byte_limit', 'scope_note', 'limitations')}
    continuation = {'authorization': AUTHORIZATION, 'plan': 'browser-observation-plan.json',
        'policy': 'browser_observation', 'legacy_limits_enforced': False,
        'legacy_robots_enforced': False, 'same_original_ledger': True, 'finished_services': 38,
        'route_repair_plan': 'route-repair-plan.json', 'simple_challenge_attempts': ['brave', 'yandex'],
        'visual_challenge_attempts': {'yandex': 1}}
    if not any(x.get('policy') == 'browser_observation' for x in scope.get('continuations', [])):
        scope.setdefault('continuations', []).append(continuation)
    scope.update({'execution_policy': 'browser_observation', 'legacy_constraints_apply_to': 'grounding only',
        'scope_note': 'Listed services, one query phrase each; native browser HTTP/S dependencies, CDN, images, iframe, POST, service worker and redirects allowed. No result destination pages intentionally opened, no API accounts or paid calls.',
        'limitations': ['Finite page observation time (usually six seconds after navigation)',
            'WebSocket handshake/frames not recorded', 'Login/API credentials not configured',
            'Keyword-matched candidate counts are not comparable organic rankings except specifically marked Brave/Baidu cards']})
    save(BASE / 'scope.json', scope)
    shutil.copyfile(REPO / 'docs/search-services-20261003.md', BASE / 'report.md')
    print(json.dumps({k: aggregate[k] for k in ('ok', 'requests', 'saved_results', 'charged_body_bytes', 'requests_by_policy', 'browser_observation')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
