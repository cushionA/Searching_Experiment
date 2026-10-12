#!/usr/bin/env python3
"""Prepare source-only exact literal-family proofs for the frozen coordinate-repair DEV20 cohort."""
from __future__ import annotations
from collections import Counter
import hashlib, importlib.util, json, os, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import prepare_dev_literal_family_proofs_v1 as dev_helper

COHORT=ROOT/".lab-output/sku-next-dev-coordinate-refids-20261013-v1"
SOURCE_ROOT=ROOT/".lab-output/sku-full-condition-adapter-20261013-v2/dev"
SOURCE_BUNDLE=ROOT/".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
OUT=ROOT/".lab-output/sku-next-dev-coordinate-literal-proofs-20261013-v1"
PROOF_ADAPTER=HERE/"sku_literal_family_proof_coordinate_adapter_v1.py"
BUILDER=HERE/"build_sku_literal_family_proofs_v1.py"

def sha_file(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def read_json(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def read_rows(p): return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
def write_json(p,x):
    with Path(p).open("x",encoding="utf-8") as f: json.dump(x,f,ensure_ascii=False,sort_keys=True,indent=2); f.write("\n")
def write_rows(p,xs):
    with Path(p).open("xb") as f:
        for x in xs: f.write(canonical(x)+b"\n")
def indexed(xs,key,label):
    out={}
    for x in xs:
        v=x.get(key)
        if not isinstance(v,str) or not v or v in out: raise ValueError("invalid or duplicate "+label)
        out[v]=x
    return out

def load_modules():
    import sku_literal_family_proof_v1 as frozen
    import sku_literal_family_proof_coordinate_adapter_v1 as adapter
    bs=importlib.util.spec_from_file_location("sku_coordinate_literal_builder_v1",BUILDER)
    if bs is None or bs.loader is None: raise RuntimeError("cannot load unchanged literal builder")
    builder=importlib.util.module_from_spec(bs);sys.modules[bs.name]=builder;bs.loader.exec_module(builder)
    from types import SimpleNamespace
    builder.proof=SimpleNamespace(PROOF_VERSION=frozen.PROOF_VERSION,build_proofs=adapter.build_proofs,sources=frozen.sources)
    return adapter,builder

def verify_task_families(outroot, proof, source_bundle):
    inputs=outroot/"inputs"; tasks=read_rows(inputs/"tasks.jsonl")
    cases=indexed((x["record"] for x in read_rows(inputs/"source-cases.jsonl")),"case_id","case")
    products=indexed((x["record"] for x in read_rows(inputs/"source-products.jsonl")),"dossier_id","product")
    pins=read_json(inputs/"source-input-manifests.json")
    store=proof.sources.RawStore(source_bundle); receipts=[]; rawfiles={}
    for task in tasks:
        row={"task_id":task.get("task_id"),"case_id":task.get("case_id"),"au_row_key":task.get("au_row_key"),"direction":task.get("direction"),"request_id":task.get("request_id"),"status":"unknown"}
        try:
            case=cases[task["case_id"]];product=products[task["dossier_id"]]
            if (case.get("dossier_id")!=task.get("dossier_id") or product.get("dossier_id")!=task.get("dossier_id")
                or str(case.get("au_product_id"))!=str(task.get("au_product_id"))
                or str((product.get("au_product") or {}).get("product_id"))!=str(task.get("au_product_id"))):
                raise ValueError("fixed case/product binding differs")
            prov=task.get("provenance") or {};version=prov.get("source_version")
            if version not in pins or prov.get("case_source_sha256")!=pins[version].get("cases_sha256") or prov.get("product_source_sha256")!=pins[version].get("products_sha256"):
                raise ValueError("original source input SHA pin differs")
            ref=task.get("raw_condition_ref") or {};spans=[]
            if task.get("direction")=="rakuten_to_au":
                selected=case.get("rakuten_selected") or {}
                axis=proof._one(selected.get("axes") or [],lambda a:a.get("axis_key")==ref.get("axis_key"),"exact selected Rakuten API-coordinate axis")
                if axis.get("axis_label")!=ref.get("axis_name"): raise ValueError("Rakuten exact source coordinate caption differs")
                if axis.get("value")!=ref.get("value") or proof._normalized_span(axis.get("value_span"))!=proof._normalized_span(ref.get("value_span")):
                    raise ValueError("Rakuten task selected source differs")
                family,family_spans,label_span,selected_span=proof._rakuten_family(store,selected,axis)
                proof._verified_span(store,ref.get("axis_label_span"),ref["axis_name"],"Rakuten task axis label")
                if proof._normalized_span(ref.get("axis_label_span"))!=proof._normalized_span(label_span): raise ValueError("Rakuten axis label source differs")
                sourcefile=selected["raw_file"]
            elif task.get("direction")=="au_to_rakuten":
                path=(ref.get("value_span") or {}).get("locator",{}).get("json_path","")
                aurow=proof._one(product.get("au_rows") or [],lambda r:r.get("row_key")==task.get("au_row_key"),"exact AU row")
                import re
                axis_match=re.fullmatch(r"au-axis-index:(0|[1-9][0-9]*)",str(task.get("axis_key","")))
                if axis_match is None: raise ValueError("task lacks exact AU API-coordinate index")
                axis_index=int(axis_match.group(1));au_axes=aurow.get("axes") or []
                if axis_index>=len(au_axes): raise ValueError("AU API-coordinate index is outside source row")
                axis=au_axes[axis_index]
                if axis.get("axis_name")!=ref.get("axis_name") or axis.get("value")!=ref.get("value") or proof._normalized_span(axis.get("value_span"))!=proof._normalized_span(ref.get("value_span")):
                    raise ValueError("AU source axis/value differs from exact task coordinate")
                family,family_spans,label_span,selected_span=proof._au_family(store,product,ref["axis_name"],ref["value"],ref["value_span"])
                if proof._normalized_span(axis.get("axis_name_span"))!=proof._normalized_span(label_span): raise ValueError("AU axis label source differs")
                sourcefile=(product.get("au_product") or {})["raw_file"]
            else: raise ValueError("unsupported task direction")
            if ref.get("value") not in family: raise ValueError("selected value absent from complete source family")
            spans=[proof._normalized_span(label_span),proof._normalized_span(selected_span),*[proof._normalized_span(s) for s in family_spans]]
            row.update({"status":"verified_complete_ordered_raw_family","raw_file":sourcefile,"selected_value":ref["value"],"axis_name":ref["axis_name"],"ordered_family":family,"axis_label_span":spans[0],"selected_value_span":spans[1],"family_value_span_count":len(family_spans),"raw_span_receipts":spans,"source_pins":{"source_version":version,"case_source_sha256":prov["case_source_sha256"],"product_source_sha256":prov["product_source_sha256"]},"reason":"exact_source_value_and_complete_family_spans_verified"})
            for span in spans:
                path,digest=span.get("raw_file"),span.get("sha256")
                if path and digest:
                    if store.sha(path)!=digest: raise ValueError("raw family receipt hash differs")
                    rawfiles[path]=digest
        except (KeyError,TypeError,ValueError,OSError,UnicodeDecodeError) as exc: row["reason"]=str(exc)
        receipts.append(row)
    write_rows(outroot/"task-source-verification.jsonl",receipts)
    return {"task_source_verification_count":len(receipts),"task_source_verification_status_counts":dict(Counter(x["status"] for x in receipts)),"task_source_verification_raw_file_count":len(rawfiles)}

def prepare_inputs(cohort,outroot):
    manifest=read_json(cohort/"manifest.json")
    if (manifest.get("dataset")!="sku-next-dev-coordinate-refids-20261013-v1" or manifest.get("independent_test") is not False or manifest.get("labels_read") is not False or manifest.get("test_split_read") is not False or manifest.get("production_adoption_allowed") is not False): raise ValueError("input is not label-blind next DEV cohort")
    for name,digest in manifest.get("outputs_sha256",{}).items():
        if sha_file(cohort/name)!=digest: raise ValueError("cohort input SHA differs: "+name)
    cases=indexed(read_rows(cohort/"case-plans.jsonl"),"case_id","case")
    rows=read_rows(cohort/"row-plans.jsonl");tasks=indexed(read_rows(cohort/"tasks.jsonl"),"task_id","task");requests=indexed(read_rows(cohort/"requests.jsonl"),"request_id","request")
    for task in tasks.values():
        if task.get("request_id") not in requests or task.get("case_id") not in cases: raise ValueError("task request/case link missing")
    if len(cases)!=20 or len({str(x["au_product_id"]) for x in cases.values()})!=10 or len(rows)!=320 or len(tasks)!=1220: raise ValueError("frozen DEV20 scope count mismatch")
    source_manifest=read_json(SOURCE_ROOT/"manifest.json")
    source_cases,source_products,pins,selected_manifest=dev_helper.load_original_records(SOURCE_BUNDLE,source_manifest,cases)
    outroot.mkdir(parents=True); inputs=outroot/"inputs";inputs.mkdir()
    dev_helper.write_rows(inputs/"tasks.jsonl",(tasks[k] for k in sorted(tasks)))
    dev_helper.write_rows(inputs/"source-cases.jsonl",(source_cases[k] for k in sorted(source_cases)))
    dev_helper.write_rows(inputs/"source-products.jsonl",(source_products[k] for k in sorted(source_products)))
    dev_helper.write_rows(inputs/"row-plans.jsonl",sorted(rows,key=lambda r:(r["case_id"],r["au_row_key"])))
    dev_helper.write_rows(inputs/"selected-source-manifest.jsonl",sorted(selected_manifest,key=lambda x:x["case_id"]))
    dev_helper.write_json(inputs/"source-input-manifests.json",pins)
    names=("tasks.jsonl","source-cases.jsonl","source-products.jsonl","row-plans.jsonl","selected-source-manifest.jsonl","source-input-manifests.json")
    output_sha={name:sha_file(inputs/name) for name in names}
    source_only={"schema_version":"sku-literal-family-next-dev-coordinate-input-v1","dataset":manifest["dataset"],"source_cohort_manifest_sha256":sha_file(cohort/"manifest.json"),"selection_policy_sha256":read_json(cohort/"selection-plan.json")["selection_policy_sha256"],"original_proof_code_sha256":sha_file(HERE/"sku_literal_family_proof_v1.py"),"coordinate_proof_adapter_sha256":sha_file(PROOF_ADAPTER),"builder_code_sha256":sha_file(BUILDER),"source_full_condition_manifest_sha256":sha_file(SOURCE_ROOT/"manifest.json"),"source_bundle_inventory_sha256":sha_file(SOURCE_BUNDLE.parent/"inventory.json"),"source_bundle_index_sha256":sha_file(SOURCE_BUNDLE.parent/"source-index.json"),"selected_case_count":20,"selected_product_count":10,"row_plan_count":320,"task_count":1220,"request_count":len(requests),"source_input_pins":pins,"output_sha256":output_sha,"labels_read":False,"test_split_read":False,"predictions_read":False,"model_inference_run":False,"classification_or_scoring_run":False,"production_adoption_allowed":False}
    dev_helper.write_json(inputs/"manifest.json",source_only)
    return {"input_dir":inputs,"selected_cases":cases,"selected_rows":rows,"selected_tasks":tasks,"selected_requests":requests,"source_cases":source_cases,"source_products":source_products,"source_pins":pins}

def run():
    if OUT.exists(): raise FileExistsError(OUT)
    prepared=prepare_inputs(COHORT,OUT)
    proof,builder=load_modules()
    bundle=OUT/"proof-bundle"
    proof_manifest=builder.build(OUT,SOURCE_BUNDLE,bundle)
    task_verification=verify_task_families(OUT,proof,SOURCE_BUNDLE)
    prepared["task_source_receipts"]=read_rows(OUT/"task-source-verification.jsonl")
    states=dev_helper.aggregate_request_statuses(OUT,prepared,bundle/"proofs.jsonl")
    output_manifest={"schema_version":"sku-next-dev-coordinate-literal-proof-v1","phase":"source_only_dev20_literal_family_proofs_complete","dataset":"sku-next-dev-coordinate-refids-20261013-v1","input_manifest_sha256":sha_file(OUT/"inputs/manifest.json"),"selection_plan_sha256":sha_file(COHORT/"selection-plan.json"),"source_full_condition_manifest_sha256":sha_file(SOURCE_ROOT/"manifest.json"),"source_bundle_manifest_sha256":sha_file(SOURCE_BUNDLE.parent/"inventory.json"),"proof_bundle_manifest_sha256":sha_file(bundle/"manifest.json"),"proofs_sha256":sha_file(bundle/"proofs.jsonl"),"input_product_count":10,"input_case_count":20,"input_row_count":320,"input_task_count":1220,"input_request_count":len(prepared["selected_requests"]),"literal_proof_status_counts":proof_manifest["proof_status_counts"],**task_verification,**states,"labels_read":False,"test_split_read":False,"predictions_read":False,"scores_read":False,"model_inference_run":False,"classification_or_scoring_run":False,"whole_gate_evaluation_run":False,"production_adoption_allowed":False,"all_original_tasks_retained":True,"all_original_rows_retained":True,"frozen_literal_semantics":"unchanged exact equality of selected whole value within identical complete ordered source families; no normalization, sorting, partial match, or semantic inference","source_coordinate_adapter":"AU JSONPath-derived row/column and Rakuten exact label/value locator indexes; compare source spans, SHA, selector key, caption and complete ordered family; missing/malformed coordinate stays unknown","base_literal_proof_code_changed":False,"nli_or_classifier_ran":False,"source_code_pins":{"wrapper":sha_file(Path(__file__)),"coordinate_adapter":sha_file(PROOF_ADAPTER),"unchanged_proof":sha_file(HERE/"sku_literal_family_proof_v1.py"),"unchanged_builder":sha_file(BUILDER),"source_nli_v2_freeze":sha_file(COHORT/"source-nli-v2"/"freeze.json")}}
    write_json(OUT/"manifest.json",output_manifest)
    print(json.dumps(output_manifest,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=="__main__": run()
