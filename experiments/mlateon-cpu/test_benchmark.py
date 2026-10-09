import importlib.util
import math
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('benchmark',Path(__file__).with_name('benchmark.py'))
b=importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

class MetricsTests(unittest.TestCase):
    def test_known_rank_two(self):
        m=b.ranking_metrics(['wrong','right'],{'right':1})
        self.assertAlmostEqual(m['ndcg@10'],1/math.log2(3))
        self.assertEqual(m['mrr@10'],.5)
        self.assertEqual(m['hit@1'],0)

    def test_cutoff(self):
        m=b.ranking_metrics([str(i) for i in range(11)],{'10':1})
        self.assertEqual(m['mrr@10'],0)
        self.assertEqual(m['ndcg@10'],0)

    def test_graded_ideal(self):
        self.assertEqual(b.ranking_metrics(['a','b'],{'a':3,'b':1})['ndcg@10'],1)

    def test_bm25_rare_term(self):
        m=b.BM25(['猫は動物','猫は動物','象は動物'])
        scores=m.score('象')
        # unigram query has no matching bigrams: documented limitation, all tied.
        self.assertEqual(scores,[0,0,0])
        self.assertGreater(m.score('象は')[2],m.score('象は')[0])

    def test_prefix_after_bos_and_no_silent_truncation(self):
        class Tokenizer:
            def encode(self,text):
                return type('Encoding',(),{'ids':[2,100,101,1]})()
        self.assertEqual(b.token_ids(Tokenizer(),'text',256000),[2,256000,100,101,1])
        with self.assertRaises(ValueError):b.token_ids(Tokenizer(),'text',256000,max_length=4)

if __name__=='__main__':unittest.main()
