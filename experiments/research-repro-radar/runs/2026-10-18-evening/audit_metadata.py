"""Offline metadata calculations, not candidate inference or benchmarks."""
import datetime as dt,hashlib,json,time
from pathlib import Path
start=time.monotonic();R=Path(__file__).parent;root=R.parents[3]
ev=json.loads((R/'review-evidence.json').read_text());models={};files={}
for e in ev:
 if 'error'in e:continue
 p=R/e['file'];b=p.read_bytes();assert hashlib.sha256(b).hexdigest()==e['sha256'];assert len(b)==e['bytes'];files[e['file']]=len(b)
 if e['kind'].startswith('model-') and e['kind']!='model-search':
  d=json.loads(e['text']);weights=[{'name':x['rfilename'],'bytes':x.get('size')}for x in d.get('siblings',[])if x['rfilename'].endswith(('.safetensors','.onnx','.bin'))]
  models[d['id']]={'requested_url':e['url'],'revision':d.get('sha'),'created_at':d.get('createdAt'),'last_modified':d.get('lastModified'),'license':d.get('cardData',{}).get('license'),'likes':d.get('likes'),'downloads':d.get('downloads'),'weights':weights,'size_semantics':'listed artifact sizes, not necessarily combined required download and never RAM'}
edge8=models['Edge0/Edge0-8B-A1B-preview'];edge35=models['Edge0/Edge0-35B-A3B-preview']
result={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'models':models,'calculations':{'edge8_all_safetensors_bytes':sum(x['bytes']for x in edge8['weights']if x['name'].endswith('.safetensors')),'edge35_all_safetensors_bytes':sum(x['bytes']for x in edge35['weights']if x['name'].endswith('.safetensors')),'agent_reflex_8b_safetensors_bytes':sum(x['bytes']for x in models['dongguanting/Agent-Reflex-8B']['weights']if x['name'].endswith('.safetensors'))},'primary_files':len(files),'primary_payload_bytes':sum(files.values()),'candidate_code_executions':0,'model_inferences':0,'candidate_repairs_this_run':0,'candidate_weights_downloaded_bytes':0,'candidate_dependencies_downloaded_bytes':0,'gpu_seconds':0,'money_jpy':0,'performance_measurements':{'quality':None,'cpu_seconds_per_item':None,'peak_ram_bytes':None,'throughput_items_per_minute':None,'ten_thousand_items_cpu_hours':None},'metadata_audit_wall_seconds':time.monotonic()-start,'limitation':'Audit time measures metadata hashing/aggregation only; no runtime-quality-performance conclusion.'}
B=root/'benchmarks/research-repro-radar/2026-10-18-evening';B.mkdir(parents=True,exist_ok=True);(B/'metadata-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print({k:v for k,v in result.items()if k!='models'})
