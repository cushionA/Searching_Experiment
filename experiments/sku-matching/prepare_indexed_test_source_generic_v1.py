#!/usr/bin/env python3
"""Freeze a label-blind TEST cohort and repair generic option vectors from exact raw families."""
from __future__ import annotations
import hashlib, importlib.util, json, sys
from collections import defaultdict, Counter
from pathlib import Path

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
import argparse
_parser=argparse.ArgumentParser()
_parser.add_argument("--config",required=True)
_args=_parser.parse_args()
CONFIG_PATH=Path(_args.config).resolve()
CONFIG=json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
BASE=Path(CONFIG["base"]).resolve(); SOURCE=Path(CONFIG["source_root"]).resolve(); OUT=Path(CONFIG["output"]).resolve(); SEED=CONFIG["dataset"]

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

def capture_pins(records):
    found={}
    def walk(x):
        if isinstance(x,dict):
            name=x.get("raw_file"); digest=x.get("sha256")
            if isinstance(name,str) and isinstance(digest,str) and len(digest)==64:
                found.setdefault(name,set()).add(digest)
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    for record in records: walk(record)
    pins=[]
    for name,digests in sorted(found.items()):
        if len(digests)!=1: raise ValueError("raw source path has conflicting pinned SHA values")
        p=(SOURCE/name).resolve()
        if not p.is_file(): raise FileNotFoundError("referenced raw source capture missing: "+name)
        actual=sha(p.read_bytes())
        expected=next(iter(digests))
        if actual!=expected: raise ValueError("raw source capture SHA mismatch: "+name)
        pins.append({"path":name,"bytes":p.stat().st_size,"sha256":actual})
    return {"schema_version":"sku-indexed-test-raw-source-capture-inventory-v1","capture_count":len(pins),"captures":pins}

def main():
    allowed={"selection-policy.json","selection-plan.json","membership.jsonl","source-builder-config.json","identity-plan-manifest.json"}
    if OUT.exists() and any(p.name not in allowed for p in OUT.iterdir()): raise FileExistsError("source output directory contains unexpected artifacts")
    OUT.mkdir(parents=True, exist_ok=True)
    plan=json.loads((OUT/"selection-plan.json").read_text(encoding="utf-8"))
    if plan.get("dataset") != SEED or plan.get("selected_product_count") != CONFIG["selected_product_count"] or plan.get("selected_case_count") != CONFIG["selected_case_count"]:
        raise ValueError("selection plan does not match configured configured TEST identity cohort")
    cases=rows(BASE/"case-plans.jsonl"); byprod=defaultdict(list)
    for c in cases: byprod[str(c["au_product_id"])].append(c)
    picked=[]
    for item in rows(OUT/"membership.jsonl"):
        matches=[c for c in byprod.get(str(item["au_product_id"]),[]) if c["case_id"]==item["case_id"]]
        if len(matches)!=1: raise ValueError("selected identity missing or duplicated in base DEV inventory")
        picked.extend(matches)
    cids={c["case_id"] for c in picked}
    if len(cids)!=CONFIG["selected_case_count"] or len({str(c["au_product_id"]) for c in picked})!=CONFIG["selected_product_count"]: raise ValueError("selection membership count mismatch")
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
    membership=[{"case_id":c["case_id"],"au_product_id":str(c["au_product_id"])} for c in picked]
    if {(str(x["case_id"]),str(x["au_product_id"])) for x in rows(OUT/"membership.jsonl")} != {(str(x["case_id"]),str(x["au_product_id"])) for x in membership}: raise ValueError("frozen identity membership does not match selected source cases")
    source_selection={"schema_version":"sku-next-body-indexed-test-source-selection-v1","dataset":SEED,"selection_policy_sha256":sha((OUT/"selection-policy.json").read_bytes()),"identity_plan_sha256":sha((OUT/"selection-plan.json").read_bytes()),"prior_membership_sha256":CONFIG["prior_membership_sha256"],"selected_products":sorted({str(c["au_product_id"]) for c in picked}),"selected_case_ids":[c["case_id"] for c in picked],"selected_product_count":CONFIG["selected_product_count"],"selected_case_count":CONFIG["selected_case_count"],"cases_per_product":CONFIG["cases_per_product"],"labels_read":False,"task_details_read":False,"predictions_read":False,"test_split_read":True,"independent_test":True,"split":"test","identity_plan_manifest_sha256":CONFIG["identity_plan_manifest_sha256"],"selected_membership_sha256":CONFIG["selected_membership_sha256"],"identity_bridge_sha256":CONFIG["identity_bridge_sha256"]}
    wj(OUT/"source-selection-receipt.json",source_selection)
    capture_inventory=capture_pins([docs,tasks,audit])
    wj(OUT/"raw-source-capture-inventory.json",capture_inventory)
    names=["raw-source-capture-inventory.json","documents.jsonl","requests.jsonl","tasks.jsonl","row-plans.jsonl","case-plans.jsonl","baseline-model-inputs.jsonl","request-model-input-map.jsonl","task-request-family-audit.jsonl","original-requests-shadow.jsonl","membership.jsonl","source-selection-receipt.json"]
    manifest={"schema_version":"sku-next-body-indexed-test-source-v1","dataset":SEED,"split":"test","independent_test":True,"selected_product_count":CONFIG["selected_product_count"],"selected_case_count":CONFIG["selected_case_count"],"row_count":len(rowplans),"task_count":len(tasks),"derived_request_count":len(derived),"document_count":len(docs),"request_family_status_counts":dict(Counter(a["status"] for a in audit)),"outputs_sha256":{n:sha((OUT/n).read_bytes()) for n in names},"selection_policy_sha256":sha((OUT/"selection-policy.json").read_bytes()),"selection_plan_sha256":sha((OUT/"selection-plan.json").read_bytes()),"source_full_adapter_manifest_sha256":sha((BASE/"manifest.json").read_bytes()),"source_bundle_inventory_sha256":sha((SOURCE.parent/"inventory.json").read_bytes()),"raw_source_capture_inventory_sha256":sha((OUT/"raw-source-capture-inventory.json").read_bytes()),"builder_sha256":sha(Path(__file__).read_bytes()),"labels_read":False,"predictions_read":False,"model_inference_run":False,"scores_created":False,"test_split_read":True,"identity_plan_manifest_sha256":CONFIG["identity_plan_manifest_sha256"],"selected_membership_sha256":CONFIG["selected_membership_sha256"],"identity_bridge_sha256":CONFIG["identity_bridge_sha256"],"production_adoption_allowed":False,"original_task_case_row_condition_ids_retained":True,"request_links_rebound_consistently":True}
    wj(OUT/"manifest.json",manifest)
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
