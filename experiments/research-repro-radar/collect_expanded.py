"""Additional official discovery feeds; never executes candidate code."""
import importlib.util,json,concurrent.futures as cf,urllib.parse
from pathlib import Path
from judge_preflight import require_judge
require_judge()
spec=importlib.util.spec_from_file_location("collector",Path(__file__).with_name("collect.py"))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
run=Path(__file__).parent/"runs/2026-10-13-evening"
c.RAW=run/"sources"
queries=["text summarization","structured extraction noisy text","quantization int4 inference","distillation encoder","cpu inference throughput"]
urls=[("expanded-"+str(i),"https://huggingface.co/api/papers/search?q="+urllib.parse.quote(q)) for i,q in enumerate(queries)]
results=list(cf.ThreadPoolExecutor(max_workers=4).map(c.fetch,urls))
records=json.loads((run/"candidates.json").read_text()); by={r["arxiv_id"]:r for r in records}
for entry,rows in results:
 for pos,row in enumerate(rows,1):
  p=row.get("paper",row);key=p.get("id")
  if not key or not p.get("title"):continue
  if key in by:by[key]["discovered_via"].append(entry["label"]);continue
  r={"id":key,"arxiv_id":key,"title":p["title"],"source":"huggingface","url":"https://arxiv.org/abs/"+key,"abstract":p.get("summary",""),"published_at":p.get("publishedAt"),"has_code":bool(p.get("githubRepo")),"github_url":p.get("githubRepo"),"has_model":False,"requires_gpu":False,"estimated_cpu_minutes":20,"resource_status":"unknown; blocked until primary resource review","estimate_basis":"bounded inspection allocation, not inference estimate","discovered_via":[entry["label"]],"evidence_file":"sources/"+entry["label"]+".json","evidence_position":pos}
  by[key]=r;records.append(r)
(run/"expanded-candidates.json").write_text(json.dumps(records,ensure_ascii=False,indent=2)+"\n")
(run/"expanded-ledger.json").write_text(json.dumps({"queries":queries,"requests":[r[0] for r in results],"unique":len(records),"pagination":"Search responses have no next Link; backend ceiling not exhaustive"},ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"unique":len(records),"requests":[r[0] for r in results]}))
