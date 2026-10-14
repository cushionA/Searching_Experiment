import unittest
from benchmark import score, read_cases
from pathlib import Path
class ScoringTests(unittest.TestCase):
    def test_strict_json(self):
        for output in ['```json\n{"label":"billing"}\n```','{"label":"billing","extra":0}', '["billing"]']:
            self.assertFalse(score(output, {'label':'billing'})['format_ok'])
    def test_bool_not_integer(self):
        self.assertFalse(score('{"item":"A","quantity":true}',{'item':'A','quantity':1})['exact'])
    def test_partial_and_wrong_label(self):
        self.assertEqual(score('{"item":"A","quantity":9}',{'item':'A','quantity':1})['field_hits'],1)
        self.assertFalse(score('{"label":"account"}',{'label':'billing'})['exact'])
    def test_cases(self):
        cases,_=read_cases(Path(__file__).with_name('cases.jsonl'))
        self.assertEqual(len(cases),24)
if __name__=='__main__': unittest.main()
