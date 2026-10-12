import hashlib,json,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];O=ROOT/'benchmarks/gliner-sku-evidence-20261012'
def read(n):return json.loads((O/n).read_text())
class ArtifactTests(unittest.TestCase):
 def test_frozen_inputs_unchanged(self):
  for name,digest in read('provenance.json')['selected_hashes'].items():self.assertEqual(hashlib.sha256((O/name).read_bytes()).hexdigest(),digest)
 def test_complete_token_budgeted_scan(self):
  ds={d['product_id']:d for d in read('input.json')};seen=[]
  for name in ['smoke','bounded']:
   p=read(name+'-plan.json');self.assertEqual(p['input_sha256'],hashlib.sha256((O/'input.json').read_bytes()).hexdigest())
   for r in p['plan']:
    seen.append(r['product_id']);text=ds[r['product_id']]['text']
    self.assertTrue(all(x['subwords']<=480 for x in r['chunks']))
    self.assertTrue(all(c.isspace() or any(x['start']<=i<x['end'] for x in r['chunks']) for i,c in enumerate(text)))
  self.assertEqual(len(seen),12);self.assertEqual(len(set(seen)),12)
 def test_original_source_coordinates(self):
  z=zipfile.ZipFile(ROOT/'experiments/sku-matching/results/20261010-claude-task-checkpoint.zip')
  for d in read('input.json'):
   for seg in d['segments']:
    raw=z.read(seg['raw_file']);self.assertEqual(hashlib.sha256(raw).hexdigest(),seg['raw_sha256'])
    obj=json.loads(raw)
    for k in seg['json_path'][2:].split('.'):obj=obj[k]
    self.assertEqual(obj[seg['raw_start']:seg['raw_end']],d['text'][seg['start']:seg['end']])
 def test_candidates_never_assert_row_support(self):
  ds={d['product_id']:d for d in read('input.json')};n=0
  for r in read('candidates.json'):
   for s in r['model_candidates']:
    n+=1;self.assertEqual(s['condition_state'],'unassessed');self.assertEqual(s['row_applicability'],'unassessed')
    self.assertEqual(ds[r['product_id']]['text'][s['start']:s['end']],s['text']);self.assertTrue(all(p['valid'] for p in s['raw_spans']))
  self.assertEqual(n,read('evaluation.json')['model_spans'])
 def test_latest_split_accounts_for_every_case(self):
  for r in read('latest-data-audit.json').values():
   self.assertEqual(sum(r['split_cases'].values()),r['cases']);self.assertEqual(set(r['source_spans']),{'valid'})
   self.assertEqual(r['source_spans']['valid'],r['source_span_count'])
 def test_report_totals_are_recomputable(self):
  r=read('evaluation.json');self.assertEqual(sum(p['model_span_count'] for p in r['per_product']),r['model_spans'])
  self.assertAlmostEqual(sum(p['model_seconds'] for p in r['per_product']),r['inference_seconds'])
  for m,scores in r['aggregate'].items():
   for k,val in scores.items():self.assertEqual(val,sum(p['measures'][m][k] for p in r['per_product']))
if __name__=='__main__':unittest.main()
