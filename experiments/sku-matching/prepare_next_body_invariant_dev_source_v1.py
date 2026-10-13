#!/usr/bin/env python3
"""Freeze a label-blind DEV cohort and repair generic option vectors from exact raw families."""
from __future__ import annotations
import hashlib, importlib.util, json, sys
from collections import defaultdict, Counter
from pathlib import Path

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
BASE=ROOT/".lab-output/sku-full-condition-adapter-20261013-v2/dev"
OLD=ROOT/".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl"
CURRENT=ROOT/".lab-output/sku-next-dev-coordinate-refids-20261013-v1/membership.jsonl"
SOURCE=ROOT/".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
OUT=ROOT/".lab-output/sku-next-body-invariant-dev-20261013-v1"
SEED="sku-next-body-invariant-dev-20261013-v1"

def sha(b): return hashlib.sha256(b).hexdigest()
def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def digest(x): return sha(canon(x))
def rows(p): return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
def wr(p,xs):
    with Path(p).open("xb") as f:
        for x in xs: f.write(canon(x)+b"\n")
def wj(p,x):
    with Path(p).open("x",encoding="utf-8") as f: json.dump(x,f,ensure_ascii=False,indent=2); f.write("\n")

def validate_native_family(family):
    source=family.get("family_source") or {}; locator=source.get("locator") or {}; values=family.get("family_values")
    if (not source.get("raw_file") or not isinstance(source.get("sha256"),str) or len(source["sha256"])!=64
            or any(c not in "0123456789abcdef" for c in source["sha256"])
            or not (locator.get("path") or locator.get("json_path") or family.get("axis_key"))
            or not isinstance(values,list) or not values or any(not isinstance(v,str) or not v for v in values)
            or len(values)!=len(set(values))):
        raise ValueError("missing or invalid exact native source family receipt")

def main():
    if OUT.exists(): raise FileExistsError(OUT)
    # Freeze policy before ranking, using IDs only.
    OUT.mkdir(parents=True)
    policy={"schema_version":"sku-next-body-invariant-dev-policy-v1","dataset":SEED,
      "selection_policy":"Exclude products in both frozen prior DEV20 and coordinate-refids DEV10 memberships. Rank remaining DEV product IDs by SHA256('"+SEED+"|product|'+product_id); take first 5. Within each selected product rank case IDs by SHA256('"+SEED+"|case|'+case_id); take first 2. IDs only.",
      "hash_domain":"product_id and case_id only","labels_read":False,"predictions_read":False,"task_details_read":False,"test_split_read":False}
    wj(OUT/"selection-policy.json",policy)
    cases=rows(BASE/"case-plans.jsonl"); byprod=defaultdict(list)
    for c in cases: byprod[str(c["au_product_id"])].append(c)
    excl={str(x["au_product_id"]) for path in (OLD,CURRENT) for x in rows(path)}
    eligible=sorted((p for p in byprod if p not in excl),key=lambda p:(sha((SEED+"|product|"+p).encode()),p))
    if len(eligible)!=77: raise ValueError("expected 77 remaining products")
    products=eligible[:5]; picked=[]
    for p in products:
        group=sorted(byprod[p],key=lambda c:(sha((SEED+"|case|"+c["case_id"]).encode()),c["case_id"]))
        if len(group)<2: raise ValueError("selected product lacks two DEV cases")
        picked += group[:2]
    cids={c["case_id"] for c in picked}
    tasks=[t for t in rows(BASE/"tasks.jsonl") if t["case_id"] in cids]
    rids={t["request_id"] for t in tasks}; oldreq={r["request_id"]:r for r in rows(BASE/"requests.jsonl") if r["request_id"] in rids}
    docs=[d for d in rows(BASE/"documents.jsonl") if d["dossier_id"] in {c["dossier_id"] for c in picked}]
    rowplans=[r for r in rows(BASE/"row-plans.jsonl") if r["case_id"] in cids]

    sys.path.insert(0,str(HERE))
    import sku_directional_choice_nli_v1 as base
    import sku_directional_choice_nli_au_coordinate_v1 as coord
    store=base.SOURCES.RawStore(SOURCE)
    derived={}; audit=[]; task_new={}
    for t in tasks:
        original=oldreq[t["request_id"]]
        try:
            fact=coord._fact_record(store,t); fam=fact["family"]; validate_native_family(fam)
            source=fam["family_source"]
            locator=fam.get("axis_key") or (fam.get("family_source",{}).get("locator") or {}).get("json_path") or (fam.get("family_source",{}).get("locator") or {}).get("path")
            coordkey={"direction":t["direction"],"task_axis_key":t["axis_key"],"source_axis_key_or_path":locator,
                      "source_locator":source.get("locator"),
                      "raw_file":source["raw_file"],"raw_sha256":source["sha256"],"ordered_native_family":fam["family_values"]}
            sem={k:original[k] for k in ("direction","axis_name","selected_value","target_document_id","target_document_sha256","target_selected_axes","target_selected_axes_text")}
            sem["option_values"]=list(fam["family_values"])
            key=digest({"source_coordinate_family":coordkey,"request_semantics":sem})
            rid="generic-description-derived:"+key
            req={"request_id":rid,"request_sha256":digest(sem),**sem,"source_family_key":digest(coordkey),"source_family_receipt":coordkey,
                 "original_request_ids":[t["request_id"]],"selected_value_is_binding_only":True,"applicability_required":True,"production_adoption_allowed":False,"strict_typed_proof":False}
            derived.setdefault(rid,req)
            if t["request_id"] not in derived[rid]["original_request_ids"]: derived[rid]["original_request_ids"].append(t["request_id"])
            newrid=rid; status="verified_complete_ordered_native_family"
            receipt={"task_id":t["task_id"],"original_request_id":t["request_id"],"derived_request_id":rid,"status":status,
                     "raw_family":coordkey,"member_spans":fam["members"]}
        except Exception as e:
            # Fail closed: unavailable coordinate facts remain unknown and never inherit pooled options.
            marker={"task_id":t["task_id"],"unknown_reason":str(e)}
            sem={k:original[k] for k in ("direction","axis_name","selected_value","target_document_id","target_document_sha256","target_selected_axes","target_selected_axes_text")}
            sem["option_values"]=[]
            key=digest({"unknown":marker,"semantics":sem}); newrid="generic-description-derived-unknown:"+key
            derived.setdefault(newrid,{"request_id":newrid,"request_sha256":digest(sem),**sem,"source_family_status":"unknown","original_request_ids":[t["request_id"]],"production_adoption_allowed":False})
            t["source_family_status"]="unknown"; task_new[t["task_id"]]=newrid
            receipt={"task_id":t["task_id"],"original_request_id":t["request_id"],"derived_request_id":newrid,"status":"unknown","reason":str(e)}
        if receipt["status"]!="unknown": task_new[t["task_id"]]=newrid
        audit.append(receipt); t["request_id"]=newrid
    # All metadata links follow the newly partitioned request IDs.
    model_inputs={}; links=[]
    for r in derived.values():
        if r.get("source_family_status")=="unknown": continue
        sem={k:r[k] for k in ("direction","axis_name","option_values","target_document_id","target_document_sha256","target_selected_axes","target_selected_axes_text")}
        mid="condition-model-input:"+digest(sem); r["model_input_id"]=mid
        model_inputs[mid]={"model_input_id":mid,**sem,"model_input_sha256":digest(sem),"selected_value_in_model_input":False,"model_input_version":"fixed-condition-whole-options-v2"}
        links.append({"request_id":r["request_id"],"model_input_id":mid})
    reqmap={r["request_id"]:r for r in derived.values()}
    for t in tasks:
        if t["request_id"] in reqmap and reqmap[t["request_id"]].get("model_input_id"): t["model_input_id"]=reqmap[t["request_id"]]["model_input_id"]
    bindings={t["task_id"]:(t["request_id"],t.get("direction"),t.get("axis_key")) for t in tasks}
    for rp in rowplans:
        for b in rp["bindings"]:
            x=bindings.get(b["task_id"])
            if not x: raise ValueError("row binding task omitted")
            b["request_id"]=x[0]
    # Preserve the pre-repair arrays in a source-only shadow for audit.
    wr(OUT/"original-requests-shadow.jsonl",oldreq.values())
    for name,vals in (("documents.jsonl",docs),("requests.jsonl",derived.values()),("tasks.jsonl",tasks),("row-plans.jsonl",rowplans),("case-plans.jsonl",picked),("baseline-model-inputs.jsonl",model_inputs.values()),("request-model-input-map.jsonl",links),("task-request-family-audit.jsonl",audit)):
        wr(OUT/name,vals)
    membership=[{"case_id":c["case_id"],"au_product_id":str(c["au_product_id"])} for c in picked]; wr(OUT/"membership.jsonl",membership)
    selected_ids={"selected_products":products,"selected_case_ids":[c["case_id"] for c in picked]}
    plan={"schema_version":"sku-next-body-invariant-dev-selection-v1","dataset":SEED,"policy_sha256":sha((OUT/"selection-policy.json").read_bytes()),"excluded_membership_sha256":[sha(OLD.read_bytes()),sha(CURRENT.read_bytes())],"remaining_product_count":len(eligible),**selected_ids,"selected_product_count":5,"selected_case_count":10,"cases_per_product":2,"labels_read":False,"task_details_read":False,"predictions_read":False,"test_split_read":False}
    wj(OUT/"selection-plan.json",plan)
    names=["documents.jsonl","requests.jsonl","tasks.jsonl","row-plans.jsonl","case-plans.jsonl","baseline-model-inputs.jsonl","request-model-input-map.jsonl","task-request-family-audit.jsonl","original-requests-shadow.jsonl","membership.jsonl"]
    manifest={"schema_version":"sku-next-body-invariant-dev-source-v1","dataset":SEED,"split":"dev","independent_test":False,"selected_product_count":5,"selected_case_count":10,"row_count":len(rowplans),"task_count":len(tasks),"derived_request_count":len(derived),"document_count":len(docs),"request_family_status_counts":dict(Counter(a["status"] for a in audit)),"outputs_sha256":{n:sha((OUT/n).read_bytes()) for n in names},"selection_policy_sha256":sha((OUT/"selection-policy.json").read_bytes()),"selection_plan_sha256":sha((OUT/"selection-plan.json").read_bytes()),"source_full_adapter_manifest_sha256":sha((BASE/"manifest.json").read_bytes()),"source_bundle_inventory_sha256":sha((SOURCE.parent/"inventory.json").read_bytes()),"builder_sha256":sha(Path(__file__).read_bytes()),"labels_read":False,"predictions_read":False,"model_inference_run":False,"scores_created":False,"test_split_read":False,"production_adoption_allowed":False,"original_task_case_row_condition_ids_retained":True,"request_links_rebound_consistently":True}
    wj(OUT/"manifest.json",manifest)
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
