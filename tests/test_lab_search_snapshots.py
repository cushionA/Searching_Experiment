import csv
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'search_tables', REPO / 'experiments/bot-diagnostics/save-search-tables.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SearchSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        catalog = {'catalog_id': 'test-catalog', 'revision': 'v1', 'service_count': 1,
                   'sites': [{'id': 'example'}]}
        self.service = {'id': 'example', 'query_status': '200', 'query_outcome': 'content_observed',
                        'quality_assessment': '関連候補', 'result_candidates': [{'url': 'https://waseda.jp/'}],
                        'waseda_domain_count': 1, 'query_confirmed': True, 'browser_observation_done': True,
                        'source_url': 'https://example.com/?q=query', 'dom_sha256': 'a' * 64}
        summary = {'query': 'query', 'services': [self.service]}
        conditions = {'query': 'query', 'headful': True, 'tools': ['patchright'],
                      'execution_policy': 'browser_observation'}
        for filename, value in (('catalog.json', catalog), ('summary.json', summary), ('conditions.json', conditions)):
            (self.root / filename).write_text(json.dumps(value), encoding='utf-8')
        self.row = '|Example (`example`)|search|履歴|200 content_observed|なし|なし|関連候補 / 候補1件・公式1件|なし|[URL](https://example.com/?q=query) / DOM `aaaaaaaaaaaa…`|'
        self.write_report(self.row)
        self.args = Namespace(catalog=self.root / 'catalog.json', summary=self.root / 'summary.json',
                              report=self.root / 'report.md', conditions=self.root / 'conditions.json',
                              output_root=self.root / 'snapshots', run_id='test-run')

    def write_report(self, row):
        header = '|サービス|分類|robots / home（履歴）|最新query|CAPTCHA/拒否|CDN|品質|備考|証拠|'
        self.root.joinpath('report.md').write_text(
            '# Full report\n\n' + header + '\n|' + '|'.join(['---'] * 9) + '|\n' + row + '\n\nOther prose\n',
            encoding='utf-8')

    def test_saves_table_only_and_original_summary_catalog_bytes(self):
        saved = MODULE.save_snapshot(self.args)
        output = Path(saved['snapshot'])
        lines = (output / 'results.md').read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith('|') for line in lines))
        self.assertEqual((output / 'results.json').read_bytes(), self.args.summary.read_bytes())
        self.assertEqual((output / 'targets.json').read_bytes(), self.args.catalog.read_bytes())
        with (output / 'results.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(len(list(csv.reader(f))), 2)

    def test_existing_verification_is_not_overwritten(self):
        output = Path(MODULE.save_snapshot(self.args)['snapshot'])
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        with self.assertRaises(FileExistsError):
            MODULE.save_snapshot(self.args)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_conditions_with_different_query_fail_before_creating_snapshot(self):
        value = json.loads(self.args.conditions.read_text())
        value['query'] = 'another query'
        self.args.conditions.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'query differs'):
            MODULE.save_snapshot(self.args)
        self.assertFalse((self.args.output_root / self.args.run_id).exists())

    def test_same_ids_from_a_different_report_fail_before_creating_snapshot(self):
        changes = [('200 content_observed', '403 access_denied_observed'),
                   ('候補1件・公式1件', '候補2件・公式2件'),
                   ('aaaaaaaaaaaa', 'bbbbbbbbbbbb'),
                   ('https://example.com/?q=query', 'https://example.com/?q=other')]
        for before, after in changes:
            with self.subTest(change=after):
                self.write_report(self.row.replace(before, after))
                with self.assertRaises(ValueError):
                    MODULE.save_snapshot(self.args)
                self.assertFalse((self.args.output_root / self.args.run_id).exists())


if __name__ == '__main__':
    unittest.main()
