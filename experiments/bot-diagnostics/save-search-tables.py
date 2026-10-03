#!/usr/bin/env python3
"""Save a stable search-target catalog and a new table snapshot, without network access."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / 'experiments/bot-diagnostics/search-targets.json'
TABLE_ROOT = REPO / 'docs/search-services'
JST = timezone(timedelta(hours=9))


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def catalog_ids(catalog):
    ids = [s['id'] for s in catalog['sites']]
    if len(ids) != len(set(ids)) or len(ids) != catalog['service_count']:
        raise ValueError('Catalog count/IDs do not match')
    return set(ids)


def save_catalog(path, output):
    catalog = read_json(path)
    catalog_ids(catalog)
    destinations = [output / 'targets.md', output / 'targets.csv']
    if any(p.exists() for p in destinations):
        raise FileExistsError('Existing catalog tables must not be silently overwritten')
    output.mkdir(parents=True, exist_ok=True)
    header = ['ID', '分類', 'サービス', '調査対象URL', '検索方式', '検索ルート', '備考']
    kinds = {'search': 'Web検索', 'metasearch': 'メタ検索', 'selfhosted': '自ノード',
             'instance_directory': '公開インスタンス一覧', 'answer_engine': 'AI回答',
             'api': '検索API', 'computational_answer': '計算回答',
             'academic_directory': '学術検索（ルート未設定）', 'academic_search': '学術検索',
             'visual_search': '画像による検索', 'image_search': '写真検索'}
    rows = []
    for s in catalog['sites']:
        route = ' / '.join(s['search_url_templates']) or '未設定'
        alternate = ' '.join('別段階観測: ' + r['url_template'] + '（' + r['relation'] + '）'
                             for r in s['alternate_routes'])
        rows.append([s['id'], s['category'], s['name'], s['home_url'],
                     kinds.get(s['service_kind'], s['service_kind']), route,
                     s['route_notes'] + (' ' + alternate if alternate else '')])
    with destinations[1].open('x', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    lines = ['|' + '|'.join(header) + '|', '|' + '|'.join(['---'] * len(header)) + '|']
    for row in rows:
        cells = [str(c).replace('|', '/').replace('\n', ' ') for c in row]
        cells[3] = f'[{row[3]}]({row[3]})'
        cells[5] = f'`{cells[5]}`' if cells[5] != '未設定' else cells[5]
        lines.append('|' + '|'.join(cells) + '|')
    with destinations[0].open('x', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return {'catalog_table': str(destinations[0]), 'services': len(rows)}


def result_table(report):
    lines = report.read_text(encoding='utf-8').splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith('|サービス|分類|robots / home')]
    if len(starts) != 1:
        raise ValueError('Expected exactly one individual-results table')
    table = []
    for line in lines[starts[0]:]:
        if not line.startswith('|'):
            break
        table.append(line)
    if len(table) < 3:
        raise ValueError('Results table is empty')
    columns = [c.strip() for c in table[0].strip('|').split('|')]
    if len(columns) != 9:
        raise ValueError('Expected the existing nine-column table')
    rows = [[c.strip() for c in line.strip('|').split('|')] for line in table[2:]]
    if any(len(row) != len(columns) for row in rows):
        raise ValueError('Result table column count differs')
    ids = [re.search(r'\(`([a-z0-9-]+)`\)', row[0]) for row in rows]
    if not all(ids):
        raise ValueError('Each result row must retain its service ID')
    return table, columns, rows, [m.group(1) for m in ids]


def relocate_links(line, source_directory, destination_directory):
    def replace(match):
        label, target = match.groups()
        if target.startswith('#') or urlsplit(target).scheme:
            return match.group(0)
        source = Path(target)
        if not source.is_absolute():
            source = (source_directory / source).resolve()
        return f'[{label}]({os.path.relpath(source, destination_directory)})'
    return re.sub(r'\[([^\]]*)\]\(([^)]+)\)', replace, line)


def validate_report_rows(rows, table_ids, services):
    """Reject a report from another run even when it has the same service IDs."""
    by_id = {s['id']: s for s in services}
    for row, service_id in zip(rows, table_ids):
        service = by_id[service_id]
        query = f"{service['query_status']} {service['query_outcome']}".strip() or '未観測'
        if row[3] != query:
            raise ValueError(f'Report query outcome differs from summary: {service_id}')
        counts = re.search(r'候補(\d+)件・公式(\d+)件', row[6])
        expected = (len(service['result_candidates']), service['waseda_domain_count'])
        if not counts or tuple(map(int, counts.groups())) != expected:
            raise ValueError(f'Report result counts differ from summary: {service_id}')
        label = service['quality_assessment'].replace('|', '/').replace('\n', ' ')
        if not row[6].startswith(label + ' / '):
            raise ValueError(f'Report assessment differs from summary: {service_id}')
        if service.get('source_url') and service['source_url'] not in re.findall(r'\]\(([^)]+)\)', row[8]):
            raise ValueError(f'Report source URL differs from summary: {service_id}')
        if service.get('dom_sha256') and service['dom_sha256'][:12] not in row[8]:
            raise ValueError(f'Report DOM evidence differs from summary: {service_id}')


def save_snapshot(args):
    catalog = read_json(args.catalog)
    expected_ids = catalog_ids(catalog)
    summary = read_json(args.summary)
    conditions = read_json(args.conditions)
    if conditions.get('query') != summary['query']:
        raise ValueError('Conditions query differs from observed summary query')
    for key in ('headful', 'tools', 'execution_policy'):
        if key not in conditions:
            raise ValueError(f'Missing execution condition: {key}')
    services = summary['services']
    observed_ids = [s['id'] for s in services]
    table, columns, rows, table_ids = result_table(args.report)
    if set(observed_ids) != expected_ids or len(observed_ids) != len(expected_ids):
        raise ValueError('Summary must contain every catalog ID exactly once')
    if set(table_ids) != expected_ids or len(table_ids) != len(expected_ids):
        raise ValueError('Report must contain every catalog ID exactly once')
    validate_report_rows(rows, table_ids, services)
    now = datetime.now(timezone.utc)
    run_id = args.run_id or now.astimezone(JST).strftime('%Y%m%d-%H%M%S-%f')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', run_id):
        raise ValueError('Invalid run ID')
    output = args.output_root / run_id
    if output.exists():
        raise FileExistsError('Existing verification snapshot cannot be overwritten; use a new run ID')
    metadata = {
        'schema': 1, 'run_id': run_id, 'saved_at_utc': now.isoformat(),
        'saved_at_jst': now.astimezone(JST).isoformat(), 'conditions': conditions,
        'catalog': {'id': catalog['catalog_id'], 'revision': catalog['revision'],
                    'sha256': digest(args.catalog), 'snapshot': 'targets.json'},
        'sources': {label: {'path': str(path.resolve()), 'sha256': digest(path)}
                    for label, path in (('summary', args.summary), ('report', args.report),
                                        ('conditions', args.conditions))},
        'counts': {'targets': len(services), 'search_responses_confirmed': sum(s['query_confirmed'] for s in services),
                   'browser_rechecks': sum(s['browser_observation_done'] for s in services)},
        'table_columns': columns,
        'observation_lineage': [
            {k: s.get(k) for k in ('id', 'execution_policy', 'browser_observation_done',
                                  'source_url', 'final_url', 'observed_role', 'run_group',
                                  'dom_sha256', 'screenshot', 'result_extraction_method',
                                  'external_evidence_source', 'external_reproduced_in_this_run')}
            for s in services],
        'preservation': 'New directory per verification. Table contains historical home/robots/CDN fields as labelled; adopted observations and external reports remain distinct.'}
    output.mkdir(parents=True, exist_ok=False)
    rendered = [relocate_links(line, args.report.resolve().parent, output.resolve()) for line in table]
    rendered_rows = [[c.strip() for c in line.strip('|').split('|')] for line in rendered[2:]]
    with (output / 'results.md').open('x', encoding='utf-8') as f:
        f.write('\n'.join(rendered) + '\n')
    with (output / 'results.csv').open('x', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rendered_rows)
    # Keep the supplied summary and catalog bytes intact for reproducibility.
    for source, name in ((args.summary, 'results.json'), (args.catalog, 'targets.json')):
        with (output / name).open('xb') as f:
            f.write(source.read_bytes())
    write_json(output / 'conditions.json', metadata)
    hashes = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    write_json(output / 'SHA256.json', hashes)
    return {'snapshot': str(output), 'table': str(output / 'results.md'),
            'services': len(services), 'columns': len(columns), 'run_id': run_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    catalog = subs.add_parser('catalog', help='Write the stable 50-service target tables')
    catalog.add_argument('--catalog', type=Path, default=CATALOG)
    catalog.add_argument('--output', type=Path, default=TABLE_ROOT)
    snapshot = subs.add_parser('snapshot', help='Create one new verification snapshot')
    snapshot.add_argument('--catalog', type=Path, default=CATALOG)
    snapshot.add_argument('--summary', type=Path, required=True)
    snapshot.add_argument('--report', type=Path, required=True)
    snapshot.add_argument('--conditions', type=Path, required=True)
    snapshot.add_argument('--run-id')
    snapshot.add_argument('--output-root', type=Path, default=TABLE_ROOT / 'results')
    args = parser.parse_args()
    result = save_catalog(args.catalog, args.output) if args.command == 'catalog' else save_snapshot(args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
