#!/usr/bin/env python3
"""Freeze a label-blind 10-product next-DEV coordinate repair cohort and prepare its source NLI inputs."""
from __future__ import annotations
import hashlib, json, sys
from collections import defaultdict
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import sku_directional_choice_nli_au_coordinate_v1 as adapter

BASE=ROOT/".lab-output/sku-full-condition-adapter-20261013-v2/dev"
OLD=ROOT/".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl"
SOURCE=ROOT/".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
OUT=ROOT/".lab-output/sku-next-dev-coordinate-refids-20261013-v1"

def sha(b): return hashlib.sha256(b).hexdigest()
def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def rows(name): return [json.loads(l) for l in (BASE/name).read_text(encoding="utf-8").splitlines() if l.strip()]
def write_rows(path, values):
    with path.open("xb") as f:
        for x in values: f.write(canon(x)+b"\n")
def write_json(path,x):
    with path.open("x",encoding="utf-8") as f: json.dump(x,f,ensure_ascii=False,indent=2); f.write("\n")
def main():
    if OUT.exists(): raise FileExistsError(OUT)
    OUT.mkdir(parents=True)
    policy={"schema_version":"sku-next-dev-coordinate-refids-policy-v1","dataset":"sku-next-dev-coordinate-refids-20261013-v1","selection_policy":"Exclude every product in the frozen prior DEV20 membership. Rank remaining DEV product IDs by SHA256('sku-next-dev-coordinate-refids-20261013-v1|product|'+product_id); take first 10. Within each selected product rank case IDs by SHA256('sku-next-dev-coordinate-refids-20261013-v1|case|'+case_id); take first 2. Use IDs only.","hash_domain":"product_id and case_id only","labels_read":False,"predictions_read":False,"task_details_read":False,"test_split_read":False}
    write_json(OUT/"selection-policy.json",policy)
    policy_sha=sha((OUT/"selection-policy.json").read_bytes())
    cases=rows("case-plans.jsonl"); tasks=rows("tasks.jsonl"); requests=rows("requests.jsonl"); docs=rows("documents.jsonl"); rowplans=rows("row-plans.jsonl")
    old=[json.loads(l) for l in OLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    excluded={str(x["au_product_id"]) for x in old}
    by_product=defaultdict(list)
    for c in cases: by_product[str(c["au_product_id"])].append(c)
    eligible=sorted((p for p in by_product if p not in excluded),key=lambda p:(sha(("sku-next-dev-coordinate-refids-20261013-v1|product|"+p).encode()),p))
    if len(eligible)!=87: raise ValueError(f"expected 87 remaining DEV products, found {len(eligible)}")
    selected_products=eligible[:10]
    selected_cases=[]
    for product in selected_products:
        group=sorted(by_product[product],key=lambda c:(sha(("sku-next-dev-coordinate-refids-20261013-v1|case|"+str(c["case_id"])).encode()),str(c["case_id"])))
        if len(group)<2: raise ValueError("selected product has fewer than two DEV cases")
        selected_cases.extend(group[:2])
    case_ids={x["case_id"] for x in selected_cases}; product_ids={str(x["au_product_id"]) for x in selected_cases}
    selected_tasks=[x for x in tasks if x.get("case_id") in case_ids]
    if not selected_tasks or any(not any(t.get("direction")==d for t in selected_tasks if t.get("case_id")==c) for c in case_ids for d in ("au_to_rakuten","rakuten_to_au")):
        raise ValueError("selected cases lack complete original bidirectional tasks")
    req_ids={x["request_id"] for x in selected_tasks}; selected_requests=[x for x in requests if x.get("request_id") in req_ids]
    doc_ids={x["target_document_id"] for x in selected_requests}; dossiers={x.get("dossier_id") for x in selected_tasks}
    selected_docs=[x for x in docs if x.get("document_id") in doc_ids or x.get("dossier_id") in dossiers]
    selected_rows=[x for x in rowplans if x.get("case_id") in case_ids]
    payload={"documents.jsonl":selected_docs,"requests.jsonl":selected_requests,"tasks.jsonl":selected_tasks,"row-plans.jsonl":selected_rows,"case-plans.jsonl":selected_cases}
    for name,values in payload.items(): write_rows(OUT/name,values)
    outsha={name:sha((OUT/name).read_bytes()) for name in payload}
    source_manifest={"schema_version":"sku-next-dev-coordinate-refids-input-v1","dataset":"sku-next-dev-coordinate-refids-20261013-v1","split":"dev","independent_test":False,"labels_read":False,"test_split_read":False,"model_inference_run":False,"production_adoption_allowed":False,"selected_product_count":10,"selected_case_count":20,"selected_row_count":len(selected_rows),"selected_task_count":len(selected_tasks),"selected_request_count":len(selected_requests),"selected_document_count":len(selected_docs),"outputs_sha256":outsha}
    write_json(OUT/"manifest.json",source_manifest)
    membership=[{"case_id":c["case_id"],"au_product_id":str(c["au_product_id"])} for c in selected_cases]
    write_rows(OUT/"membership.jsonl",membership)
    plan={"schema_version":"sku-next-dev-coordinate-refids-selection-v1","dataset":source_manifest["dataset"],"selection_policy_sha256":policy_sha,"selection_policy":"After freezing selection-policy.json, rank product IDs not present in the prior frozen DEV20 membership by SHA256('sku-next-dev-coordinate-refids-20261013-v1|product|'+product_id), then rank each selected product's DEV case IDs by SHA256('sku-next-dev-coordinate-refids-20261013-v1|case|'+case_id); take first 10 products and first 2 cases per product. IDs only; no labels, predictions, task details, or outcomes.","hash_domain":"IDs only","prior_dev_membership_sha256":sha(OLD.read_bytes()),"full_dev_case_count":len(cases),"full_dev_product_count":len(by_product),"excluded_prior_product_count":len(excluded),"remaining_product_count":len(eligible),"selected_products":selected_products,"selected_case_ids":[x["case_id"] for x in selected_cases],"selected_product_count":10,"selected_case_count":20,"cases_per_product":2,"membership_sha256":sha((OUT/"membership.jsonl").read_bytes()),"labels_read":False,"test_split_read":False,"predictions_read":False,"independent_test":False}
    write_json(OUT/"selection-plan.json",plan)
    result=adapter.prepare(OUT,SOURCE,OUT/"source-nli")
    print(json.dumps({"selected_products":10,"selected_cases":20,"rows":len(selected_rows),"tasks":len(selected_tasks),"requests":len(selected_requests),"documents":len(selected_docs),"source_manifest_sha256":sha((OUT/"manifest.json").read_bytes()),"selection_plan_sha256":sha((OUT/"selection-plan.json").read_bytes()),"source_nli":result},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
