import os,sys,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/workspace/evening-radar/experiments/research-repro-radar');import judge_preflight
class Gate(unittest.TestCase):
 def test_missing_probe_fails_without_cli(self):
  with patch.dict(os.environ,{'RADAR_LLM':'native'},clear=True),patch.object(judge_preflight.llm,'preflight',side_effect=AssertionError('CLI called')):
   with self.assertRaises(SystemExit):judge_preflight.require_judge()
 def test_valid_probe_no_cli(self):
  with patch.dict(os.environ,{'RADAR_LLM':'native','RADAR_NATIVE_PROBE_REQUEST':'/workspace/radar-private/20261014/probe.request.json','RADAR_NATIVE_PROBE_RESPONSE':'/workspace/radar-private/20261014/probe.response.json'},clear=True),patch.object(judge_preflight.llm,'preflight',side_effect=AssertionError('CLI called')):judge_preflight.require_judge()
 def test_wrong_identity_fails(self):
  with tempfile.TemporaryDirectory() as d:
   res=json.loads(Path('/workspace/radar-private/20261014/probe.response.json').read_text());res['task_sha256']='0'*64;p=Path(d)/'bad.json';p.write_text(json.dumps(res))
   with patch.dict(os.environ,{'RADAR_LLM':'native','RADAR_NATIVE_PROBE_REQUEST':'/workspace/radar-private/20261014/probe.request.json','RADAR_NATIVE_PROBE_RESPONSE':str(p)},clear=True),patch.object(judge_preflight.llm,'preflight',side_effect=AssertionError('CLI called')):
    with self.assertRaises(SystemExit):judge_preflight.require_judge()
unittest.main()
