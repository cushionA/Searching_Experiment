#!/usr/bin/env python3
"""Source-only literal-family proofs and exact per-task source receipts for the body-invariant independent TEST cohort."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib, importlib.util, json, re, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
COHORT=ROOT/".lab-output/sku-final-body-indexed-test-20261013-v1/prepared-source-v1"
SOURCE=ROOT/".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
SOURCE_ROOT=ROOT/".lab-output/sku-full-condition-adapter-20261013-v2/test"
OUT=ROOT/".lab-output/sku-final-body-indexed-test-20261013-v1/literal-proofs-v1"
PROOF=HERE/"sku_literal_family_proof_v1.py"
COORD=HERE/"sku_literal_family_proof_coordinate_adapter_v1.py"
FROZEN_CODE_SHA="ef00e3"  # Full bytecode identity is recorded from the current frozen file below.

def sha_file(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def rows(p): return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
def readj(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def wr(p,xs):
    with Path(p).open("xb") as f:
        for x in xs: f.write(canon(x)+b"\n")
def wj(p,x):
    with Path(p).open("x",encoding="utf-8") as f: json.dump(x,f,ensure_ascii=False,sort_keys=True,indent=2);f.write("\n")
def indexed(xs,key):
    out={}
    for x in xs:
        k=x.get(key)
        if not isinstance(k,str) or not k or k in out: raise ValueError("missing or duplicate "+key)
        out[k]=x
    return out

def load_modules():
    sys.path.insert(0,str(HERE))
    import sku_literal_family_proof_v1 as frozen
    import sku_literal_family_proof_coordinate_adapter_v1 as coordinate
    from types import SimpleNamespace
    builder=SimpleNamespace(PROOF_VERSION=frozen.PROOF_VERSION,build_proofs=coordinate.build_proofs,sources=frozen.sources)
    return frozen,coordinate,builder

def prepare_input():
    manifest=readj(COHORT/"manifest.json")
    if (manifest.get("schema_version")!="sku-next-body-indexed-test-source-v1"
            or manifest.get("dataset")!="sku-next-body-indexed-test-20261013-v1"
            or manifest.get("independent_test") is not True or manifest.get("selected_product_count")!=20
            or manifest.get("selected_case_count")!=40 or manifest.get("row_count")!=len(rows(COHORT/"row-plans.jsonl"))
            or manifest.get("task_count")!=len(rows(COHORT/"tasks.jsonl")) or manifest.get("derived_request_count")!=len(rows(COHORT/"requests.jsonl"))
            or manifest.get("split")!="test" or manifest.get("independent_test") is not True or manifest.get("identity_plan_manifest_sha256")!="6747e6d9ac160d608f18f3ac51a3a543c06a34b8f3091736689a20dba249773d" or manifest.get("selected_membership_sha256")!="09b11b352cead06686eb5f446b4ed713d8391f08255cd2731ec8c263ddf9a4fa" or manifest.get("labels_read") is not False
            or manifest.get("model_inference_run") is not False or manifest.get("test_split_read") is not True):
        raise ValueError("new cohort manifest scope/blindness guard failed")
    for name,digest in manifest.get("outputs_sha256",{}).items():
        if sha_file(COHORT/name)!=digest: raise ValueError("new cohort file SHA differs: "+name)
    cases=indexed(rows(COHORT/"case-plans.jsonl"),"case_id")
    tasks=indexed(rows(COHORT/"tasks.jsonl"),"task_id")
    req=indexed(rows(COHORT/"requests.jsonl"),"request_id")
    rowplans=rows(COHORT/"row-plans.jsonl")
    if len(cases)!=40 or len({str(c["au_product_id"]) for c in cases.values()})!=20 or len(rowplans)!=manifest["row_count"] or len(tasks)!=manifest["task_count"] or len(req)!=manifest["derived_request_count"]:
        raise ValueError("new cohort populations differ from pinned manifest")
    for t in tasks.values():
        if t.get("case_id") not in cases or t.get("request_id") not in req: raise ValueError("task case/request binding missing")
    # Verify every row-plan binding is exactly represented by a task.
    task_bind={(t["task_id"],t["case_id"],t["au_row_key"],t["request_id"],t["direction"],t["axis_key"]) for t in tasks.values()}
    seen=set()
    for rp in rowplans:
        for b in rp.get("bindings",[]):
            item=(b["task_id"],rp["case_id"],rp["au_row_key"],b["request_id"],b["direction"],b["axis_key"])
            if item not in task_bind or item in seen: raise ValueError("row-plan binding does not uniquely match task")
            seen.add(item)
    if seen!={(x[0],x[1],x[2],x[3],x[4],x[5]) for x in task_bind}: raise ValueError("row plans omit tasks")
    # Reuse the source-only loader; it verifies original v2/v3 input SHA pins and fixed case/product identities.
    import prepare_dev_literal_family_proofs_v1 as helper
    src_manifest=readj(SOURCE_ROOT/"manifest.json")
    source_cases,source_products,pins,selected=helper.load_original_records(SOURCE,src_manifest,cases)
    OUT.mkdir(parents=True); inp=OUT/"inputs";inp.mkdir()
    wr(inp/"tasks.jsonl",(tasks[k] for k in sorted(tasks)))
    wr(inp/"source-cases.jsonl",(source_cases[k] for k in sorted(source_cases)))
    wr(inp/"source-products.jsonl",(source_products[k] for k in sorted(source_products)))
    wr(inp/"row-plans.jsonl",sorted(rowplans,key=lambda x:(x["case_id"],x["au_row_key"])))
    wr(inp/"selected-source-manifest.jsonl",sorted(selected,key=lambda x:x["case_id"]))
    wj(inp/"source-input-manifests.json",pins)
    names=("tasks.jsonl","source-cases.jsonl","source-products.jsonl","row-plans.jsonl","selected-source-manifest.jsonl","source-input-manifests.json")
    manifest={"schema_version":"sku-next-body-invariant-test-literal-proof-input-v1","dataset":manifest["dataset"],"cohort_manifest_sha256":sha_file(COHORT/"manifest.json"),"selected_case_count":len(cases),"selected_product_count":len({str(x["au_product_id"]) for x in cases.values()}),"row_plan_count":len(rowplans),"task_count":len(tasks),"request_count":len(req),"source_input_pins":pins,"output_sha256":{n:sha_file(inp/n) for n in names},"labels_read":False,"predictions_read":False,"model_inference_run":False,"test_split_read":True,"identity_plan_manifest_sha256":manifest["identity_plan_manifest_sha256"],"selected_membership_sha256":manifest["selected_membership_sha256"],"raw_source_capture_inventory_sha256":manifest.get("raw_source_capture_inventory_sha256"),"production_adoption_allowed":False}
    wj(inp/"manifest.json",manifest)
    return tasks,source_cases,source_products,pins,manifest

def verify_task(task,case,product,pins,coord,frozen,store):
    ref=task.get("raw_condition_ref") or {}; prov=task.get("provenance") or {}
    version=prov.get("source_version")
    if version not in pins or prov.get("case_source_sha256")!=pins[version]["cases_sha256"] or prov.get("product_source_sha256")!=pins[version]["products_sha256"]:
        raise ValueError("original source input SHA pin mismatch")
    if case.get("case_id")!=task.get("case_id") or case.get("dossier_id")!=task.get("dossier_id") or product.get("dossier_id")!=task.get("dossier_id"):
        raise ValueError("task case/product source binding mismatch")
    if str(case.get("au_product_id"))!=str(task.get("au_product_id")) or str((product.get("au_product") or {}).get("product_id"))!=str(task.get("au_product_id")):
        raise ValueError("task AU product identity mismatch")
    if task.get("direction")=="rakuten_to_au":
        selected=case.get("rakuten_selected") or {}; want=ref.get("axis_label_span") or {}; val=ref.get("value_span") or {}
        axes=selected.get("axes") or []
        axis=frozen._one(axes,lambda a:frozen._normalized_span(a.get("axis_label_span"))==frozen._normalized_span(want) and frozen._normalized_span(a.get("value_span"))==frozen._normalized_span(val),"exact Rakuten task source coordinate")
        if axis.get("axis_key")!=ref.get("axis_key") or axis.get("axis_label")!=ref.get("axis_name") or axis.get("value")!=ref.get("value"):
            raise ValueError("Rakuten task key/caption/value differs at exact coordinate")
        family,spans,label_span,selected_span=coord._rakuten_family(store,selected,axis)
        if frozen._normalized_span(want)!=frozen._normalized_span(label_span): raise ValueError("Rakuten task label span differs")
        raw=selected.get("raw_file")
    elif task.get("direction")=="au_to_rakuten":
        m=re.fullmatch(r"au-axis-index:(0|[1-9][0-9]*)",str(task.get("axis_key","")))
        if not m: raise ValueError("AU task lacks exact native axis index")
        aurow=frozen._one(product.get("au_rows") or [],lambda r:r.get("row_key")==task.get("au_row_key"),"exact AU source row")
        axes=aurow.get("axes") or []; idx=int(m.group(1))
        if idx>=len(axes): raise ValueError("AU task coordinate index outside source row")
        axis=axes[idx]
        if axis.get("axis_name")!=ref.get("axis_name") or axis.get("value")!=ref.get("value") or frozen._normalized_span(axis.get("value_span"))!=frozen._normalized_span(ref.get("value_span")):
            raise ValueError("AU task name/value/span differs at exact coordinate")
        family,spans,label_span,selected_span=coord._au_family(store,product,ref["axis_name"],ref["value"],ref["value_span"])
        if frozen._normalized_span(axis.get("axis_name_span"))!=frozen._normalized_span(label_span): raise ValueError("AU task axis label coordinate differs")
        raw=(product.get("au_product") or {}).get("raw_file")
    else: raise ValueError("unsupported task direction")
    if ref.get("value") not in family or len(family)!=len(set(family)): raise ValueError("selected value absent/ambiguous in complete raw family")
    receipts=[frozen._normalized_span(label_span),frozen._normalized_span(selected_span),*[frozen._normalized_span(s) for s in spans]]
    for span in receipts:
        if store.sha(span["raw_file"])!=span["sha256"]: raise ValueError("raw receipt SHA mismatch")
    return {"task_id":task["task_id"],"request_id":task["request_id"],"case_id":task["case_id"],"au_product_id":str(task["au_product_id"]),"au_row_key":task["au_row_key"],"direction":task["direction"],"native_axis_key":axis.get("axis_key"),"axis_name":ref["axis_name"],"selected_value":ref["value"],"ordered_family":family,"raw_file":raw,"raw_sha256":receipts[0]["sha256"],"raw_span_receipts":receipts,"status":"verified_complete_ordered_raw_family","reason":"exact_native_coordinate_and_all_ordered_family_spans_verified"}

def run():
    if OUT.exists(): raise FileExistsError(OUT)
    tasks,source_cases,source_products,pins,input_manifest=prepare_input()
    frozen,coord,builder=load_modules()
    cases={k:v["record"] for k,v in source_cases.items()}; products={k:v["record"] for k,v in source_products.items()}
    store=coord.sources.RawStore(SOURCE); receipts=[]
    for t in tasks.values():
        base={"task_id":t["task_id"],"request_id":t["request_id"],"case_id":t["case_id"],"au_row_key":t["au_row_key"],"direction":t["direction"],"status":"unknown"}
        try: base.update(verify_task(t,cases[t["case_id"]],products[t["dossier_id"]],pins,coord,frozen,store))
        except (KeyError,TypeError,ValueError,OSError,UnicodeDecodeError) as e: base["reason"]=str(e)
        receipts.append(base)
    if len(receipts)!=len(tasks) or len({x["task_id"] for x in receipts})!=len(tasks): raise ValueError("task verification not exactly once for task IDs")
    wr(OUT/"task-source-verification.jsonl",receipts)
    receipt_by_id={r["task_id"]:r for r in receipts}
    # Pair only a unique caption in each direction on a fixed case/row. Any duplicate or missing axis stays unknown.
    grouped=defaultdict(lambda:{"rakuten_to_au":[],"au_to_rakuten":[]})
    for t in tasks.values(): grouped[(t["case_id"],t["au_row_key"])][t["direction"]].append(t)
    proofs=[]; task_states={t:{"task_id":t,"status":"unknown","reason":"no_unique_bidirectional_axis_pair"} for t in tasks}
    for (case_id,row_key), dirs in sorted(grouped.items()):
        fc=Counter(t["raw_condition_ref"]["axis_name"] for t in dirs["rakuten_to_au"])
        rc=Counter(t["raw_condition_ref"]["axis_name"] for t in dirs["au_to_rakuten"])
        fi={t["raw_condition_ref"]["axis_name"]:t for t in dirs["rakuten_to_au"] if fc[t["raw_condition_ref"]["axis_name"]]==1}
        ri={t["raw_condition_ref"]["axis_name"]:t for t in dirs["au_to_rakuten"] if rc[t["raw_condition_ref"]["axis_name"]]==1}
        for name in sorted(set(fi)&set(ri)):
            f,r=fi[name],ri[name]
            if receipt_by_id[f["task_id"]]["status"]!="verified_complete_ordered_raw_family" or receipt_by_id[r["task_id"]]["status"]!="verified_complete_ordered_raw_family":
                continue
            proof=coord.prove_task_pair(f,r,source_cases[case_id],source_products[f["dossier_id"]],store,pins)
            proofs.append(proof)
            if proof.get("status") in {"support","conflict"} and proof.get("ordered_family")==receipt_by_id[f["task_id"]]["ordered_family"]==receipt_by_id[r["task_id"]]["ordered_family"]:
                for task,other in ((f,r),(r,f)):
                    task_states[task["task_id"]]={"task_id":task["task_id"],"request_id":task["request_id"],"case_id":case_id,"au_row_key":row_key,"axis_name":name,"counterpart_task_id":other["task_id"],"proof_pair_id":f["task_id"]+"|"+r["task_id"],"status":proof["status"],"reason":proof.get("reason"),"ordered_family":proof["ordered_family"],"receipt_status":receipt_by_id[task["task_id"]]["status"]}
            else:
                for task in (f,r): task_states[task["task_id"]]={"task_id":task["task_id"],"request_id":task["request_id"],"case_id":case_id,"au_row_key":row_key,"axis_name":name,"status":"unknown","reason":proof.get("reason","proof or family disagreement"),"receipt_status":receipt_by_id[task["task_id"]]["status"]}
    wr(OUT/"literal-pair-proofs.jsonl",proofs)
    states=[task_states[k] for k in sorted(task_states)]
    wr(OUT/"task-literal-proof-states.jsonl",states)
    allnames=("task-source-verification.jsonl","literal-pair-proofs.jsonl","task-literal-proof-states.jsonl")
    m={"schema_version":"sku-next-body-invariant-test-literal-proofs-v1","phase":"source_only_exact_literal_family_proofs_complete","dataset":"sku-next-body-indexed-test-20261013-v1","split":"test","independent_test":True,"test_split_read":True,"input_manifest_sha256":sha_file(OUT/"inputs/manifest.json"),"cohort_manifest_sha256":sha_file(COHORT/"manifest.json"),"source_full_condition_manifest_sha256":sha_file(SOURCE_ROOT/"manifest.json"),"source_bundle_inventory_sha256":sha_file(SOURCE.parent/"inventory.json"),"selected_product_count":len({str(x["au_product_id"]) for x in tasks.values()}),"selected_case_count":len({x["case_id"] for x in tasks.values()}),"row_count":len(rows(COHORT/"row-plans.jsonl")),"task_count":len(tasks),"request_count":len(rows(COHORT/"requests.jsonl")),"source_task_receipt_count":len(receipts),"source_task_receipt_status_counts":dict(Counter(x["status"] for x in receipts)),"literal_pair_proof_count":len(proofs),"literal_pair_status_counts":dict(Counter(x.get("status","unknown") for x in proofs)),"task_literal_state_count":len(states),"task_literal_state_counts":dict(Counter(x["status"] for x in states)),"all_tasks_have_exactly_one_source_receipt":len(receipts)==len(tasks) and len({x["task_id"] for x in receipts})==len(tasks),"original_task_ids_preserved":set(tasks)=={x["task_id"] for x in states},"source_code_pins":{"wrapper_sha256":sha_file(Path(__file__)),"unchanged_ef00_proof_sha256":sha_file(PROOF),"coordinate_adapter_sha256":sha_file(COORD),"frozen_proof_version":frozen.PROOF_VERSION,"builder_bytecode_source":"unchanged frozen prove_task_pair/build_proofs bytecode with coordinate adapter's private globals"},"outputs_sha256":{n:sha_file(OUT/n) for n in allnames},"labels_read":False,"predictions_read":False,"model_inference_run":False,"scores_created":False,"test_split_read":True,"identity_plan_manifest_sha256":readj(COHORT/"manifest.json")["identity_plan_manifest_sha256"],"selected_membership_sha256":readj(COHORT/"manifest.json")["selected_membership_sha256"],"raw_source_capture_inventory_sha256":readj(COHORT/"manifest.json").get("raw_source_capture_inventory_sha256"),"production_adoption_allowed":False,"literal_semantics":"exact selected value equality only within identical complete ordered raw families; duplicate captions remain unknown; no normalization or axis projection"}
    wj(OUT/"manifest.json",m)
    print(json.dumps(m,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=="__main__": run()
