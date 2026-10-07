import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import benchmark as b

HERE = Path(__file__).parent
RESULTS = HERE.parents[1] / 'benchmarks/gliner2-state-20261008'

class StateBenchmarkTest(unittest.TestCase):
    def test_fixed_fixture_balanced_disjoint(self):
        rows = b.load_fixture(HERE / 'fixture.json')
        self.assertEqual(len(rows), 96)
        for split in ('train', 'test'):
            for lang in ('en', 'ja'):
                for label in b.LABELS:
                    self.assertEqual(sum(r['split'] == split and r['lang'] == lang and r['label'] == label for r in rows), 6)
        digest = hashlib.sha256((HERE / 'fixture.json').read_bytes()).hexdigest()
        self.assertEqual(digest, (RESULTS / 'fixture-before-inference.sha256').read_text().split()[0])

    def test_reject_leakage(self):
        obj = json.loads((HERE / 'fixture.json').read_text())
        obj['rows'][-1]['text'] = obj['rows'][0]['text']
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.json'
            path.write_text(json.dumps(obj))
            with self.assertRaises(ValueError):
                b.load_fixture(path)

    def test_unknown_is_not_false_and_score_is_not_correctness(self):
        self.assertIsNone(b.typed_state('unknown', .99)['can_ship_now'])
        self.assertTrue(b.typed_state('unknown', .99)['requires_review'])
        self.assertFalse(b.typed_state('available', .99)['score_is_calibrated'])
        self.assertTrue(b.typed_state('available', .79)['requires_review'])
        self.assertFalse(b.typed_state('preorder', .99)['can_ship_now'])
        for label, score in [('invented', .5), ('available', float('nan')), ('sold_out', 1.1)]:
            with self.assertRaises(ValueError):
                b.typed_state(label, score)

    def test_conflicting_keywords_abstain(self):
        self.assertEqual(b.rule_predict('Sold out yesterday, in stock now.')[0], 'unknown')

    def test_metrics_known_confusion(self):
        rows = [dict(gold=a,predicted=z,typed=b.typed_state(z,.9),latency_ms=[1,2])
                for a,z in [('available','available'),('sold_out','available'),('preorder','preorder'),('unknown','unknown')]]
        m = b.metrics(rows)
        self.assertEqual(m['accuracy'], .75)
        self.assertAlmostEqual(m['macro_f1'], 2/3)
        self.assertEqual(m['false_available'], 1)
        self.assertEqual(m['review_gate']['selected'], 3)
        self.assertAlmostEqual(m['review_gate']['accuracy'], 2/3)

    def test_saved_predictions_recompute(self):
        gold = {r['id']: r for r in b.load_fixture(HERE / 'fixture.json') if r['split'] == 'test'}
        digest = hashlib.sha256((HERE / 'fixture.json').read_bytes()).hexdigest()
        for engine in ('rules', 'tfidf', 'gliner'):
            data = json.loads((RESULTS / f'{engine}.json').read_text())
            self.assertEqual(data['fixture_sha256'], digest)
            self.assertEqual(len(data['predictions']), len(gold))
            self.assertEqual({r['id'] for r in data['predictions']}, set(gold))
            for r in data['predictions']:
                self.assertEqual(r['gold'], gold[r['id']]['label'])
                self.assertEqual(r['typed'], b.typed_state(r['predicted'], r['score']))
                self.assertTrue(r['repeat_label_agreement'])
            for group in ('all','en','ja'):
                rows = [r for r in data['predictions'] if group == 'all' or r['lang'] == group]
                self.assertEqual(b.metrics(rows), data['summary'][group])

if __name__ == '__main__':
    unittest.main()
