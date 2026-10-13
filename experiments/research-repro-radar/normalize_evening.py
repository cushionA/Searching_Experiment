"""Normalize retained public responses, preserving raw evidence and unknown resources."""
from pathlib import Path
import json,xml.etree.ElementTree as E
ROOT=Path(__file__).parent
RUN=ROOT/"runs/2026-10-13-evening"
rows=json.loads((RUN/"expanded-candidates.json").read_text())
RSS="{http://purl.org/rss/1.0/}";DC="{http://purl.org/dc/elements/1.1/}"
for path in sorted((RUN/"sources").glob("mdpi*.txt")):
 for item in E.fromstring(path.read_bytes()).findall(RSS+"item"):
  doi=item.findtext(DC+"identifier","").removeprefix("doi: ").strip()
  rows.append({"doi":doi,"id":doi,"title":item.findtext(DC+"title") or item.findtext(RSS+"title"),"source":"mdpi","url":item.findtext(RSS+"link"),"abstract":item.findtext(RSS+"description", ""),"published_at":item.findtext(DC+"date"),"estimated_cpu_minutes":20,"resource_status":"unknown; execution gate closed","estimate_basis":"20-minute inspection bound, not measured runtime","evidence_file":"sources/"+path.name})
for path in sorted((RUN/"sources").glob("*-new.json")):
 for item in json.loads(path.read_text()):
  mid=item["id"]
  rows.append({"id":"hf:"+mid,"title":mid,"source":"huggingface","url":"https://huggingface.co/"+mid,"abstract":" ".join(item.get("tags",[])),"published_at":item.get("createdAt"),"updated_at":item.get("lastModified"),"has_model":True,"estimated_cpu_minutes":20,"resource_status":"unknown; model listing is not a feasibility check","estimate_basis":"20-minute inspection bound, not inference estimate","likes":item.get("likes"),"downloads":item.get("downloads"),"evidence_file":"sources/"+path.name})
for item in json.loads((RUN/"primary-supplement.json").read_text())["candidates"]:
 mid="hf:"+item["url"].split("huggingface.co/",1)[1] if "huggingface.co/" in item["url"] else item["url"]
 row={"id":mid,"title":item["title"],"source":"huggingface" if mid.startswith("hf:") else "github","url":item["url"],"abstract":item["hardware"]+" "+item["limitations"],"has_model":mid.startswith("hf:"),"has_code":not mid.startswith("hf:"),"estimated_cpu_minutes":20,"resource_status":"target CPU timing/RAM unmeasured; judge gate closed","estimate_basis":"20-minute inspection bound","evidence_file":"primary-supplement.json","license_observed":item["license"]}
 rows.append(row)
# Preserve pre-deduplication records for audit.
(RUN/"normalized-before-dedup.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n")
key=lambda r:r.get("doi") or r.get("arxiv_id") or r.get("id") or r["url"]
print(json.dumps({"raw_normalized":len(rows),"unique_ids":len({key(r) for r in rows}),"source_counts":{s:sum(r["source"]==s for r in rows) for s in {r["source"] for r in rows}}}))

by={}
for row in rows:
 k=key(row)
 if k in by:by[k].update(row)
 else:by[k]=dict(row)
(RUN/"all-candidates.json").write_text(json.dumps(list(by.values()),ensure_ascii=False,indent=2)+"\n")
