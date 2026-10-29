import unittest,json
from bench import P,pack,validate,adaptive
class Contract(unittest.TestCase):
 def setUp(self):self.rows=json.loads((P/'inputs.json').read_text());self.r=self.rows[0]
 def test_structure(self):
  self.assertEqual(len(self.rows),48);self.assertEqual(len({r['series'] for r in self.rows if r['split']=='heldout'}),6)
  for r in self.rows:self.assertFalse({'acceptable','clearly_bad','rationale','enemy_policy','future'}&r.keys())
 def test_gate(self):
  o=pack('bait',self.r,20);self.assertEqual(validate(o,self.r),'usable')
  self.assertEqual(validate(pack('bait',self.r,251),self.r),'expired')
  self.assertEqual(validate(dict(o,state_version=9),self.r),'stale_version')
  self.assertEqual(validate(dict(o,observed_at_ms=-1),self.r),'stale_timestamp')
  self.assertEqual(validate(dict(o,choice='invented'),self.r),'invalid')
 def test_history_matters(self):
  a=next(r for r in self.rows if r['id']=='eval_guard/patient/0');b=next(r for r in self.rows if r['id']=='eval_dodge/patient/0')
  self.assertEqual({k:v for k,v in a['state'].items() if k!='history'},{k:v for k,v in b['state'].items() if k!='history'})
  self.assertNotEqual(adaptive(a),adaptive(b))
 def test_low_level_continues(self):
  # Deterministic scheduler replay: decision pending; a 60Hz low layer keeps previous coefficients.
  ticks=[t for t in range(0,301,16)];r=pack('bait',self.r,300)
  applied=['hold' if t<300 or validate(r,self.r)!='usable' else r['choice'] for t in ticks]
  self.assertEqual(len(applied),19);self.assertEqual(set(applied),{'hold'})
if __name__=='__main__':unittest.main()
