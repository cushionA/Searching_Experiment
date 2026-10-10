"""Run frozen Claude SKU source gates A/B on the novel four-family source set.

This script imports gate logic from the read-only Claude share. It does not
open labels, call models, or change the baseline implementation.
"""
from __future__ import annotations

import hashlib, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / ".lab-output/sku-novel-real-inputs-20261010-v2"
OUT = ROOT / ".lab-output/sku-novel-cpu-gate-20261010-v2"
EXPECTED_INPUTS_SHA256 = "8caf26cd8e916454db3fcaf15c6c783c50320721349272b04e651b03a7bd55c5"
EXPECTED_SOURCE_CASES_SHA256 = "ae7fbb2e02bb19d09bb9745645cce199bb8c0c04ebcc71277124858b2999454e"
CLAUDE = Path("/workspace/Searching_Experiment-claude-share-20261010/experiments/sku-matching")
sys.path.insert(0, str(CLAUDE))
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402

def read(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_new(path, text):
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def jline(rows):
    return "".join(gates.canonical_json(row) + "\n" for row in rows)

def registry(case):
    return {x["id"]: x for x in case["evidence_registry"]}

def build_context(case, store):
    au, rak, reg = case["au"], case["rakuten"], registry(case)
    au_src = au["original_full_sku_array_source"]
    rel, raw = au_src["raw_file"], store.json(au_src["raw_file"])
    sku = src.resolve_json_path(raw, au_src["json_path"])
    row_name, col_name = sku["optionName"]["row"], sku["optionName"]["column"]
    rows=[]
    for r, row_value in enumerate(sku["rowNames"]):
        for c, col_value in enumerate(sku["columnNames"]):
            axes=[]
            for axis, value, path in ((row_name,row_value,f'{au_src["json_path"]}.rowNames[{r}]'),
                                      (col_name,col_value,f'{au_src["json_path"]}.columnNames[{c}]')):
                if value:
                    axes.append({"axis_name":axis,"value":value,
                                 "axis_name_span":src.json_leaf_span(store,rel,f'{au_src["json_path"]}.optionName.{"row" if axis==row_name else "column"}'),
                                 "value_span":src.json_leaf_span(store,rel,path)})
            rows.append({"row_key":f'au:{au["item_id"]}:{sku["skuId"]}:{r}:{c}',"row_index":r,"column_index":c,
                         "sku_id":sku["skuId"],"axes":axes,"array_source":{"file":rel,"line":au_src.get("line")}})
    at, rt = reg["at"]["verified_span"], reg["rt"]["verified_span"]
    rak_ref=reg["rt"]["source_ref"]
    au_rel, rak_rel = au_src["raw_file"], rak_ref["raw_file"]
    rak_sha=rak_ref["raw_sha256"]
    enc=rak_ref["encoding"]
    selectors=src.rakuten_variant_selectors(store,rak_rel,enc)
    color, families, tokens=gates.derive_vocabulary([(a["axis_name"],a["value"]) for row in rows for a in row["axes"]],selectors)
    def desc_lines(side):
        out=[]
        for b in case["evidence_registry"]:
            if b.get("side") != side or b.get("scope") != "current_page_description": continue
            sp=b.get("verified_span")
            if not sp: raise ValueError(f"Unresolved registry description span: {case['case_id']} {b['id']}")
            out.append({"text":b["quote"],"span":sp,"scope_tag":"product_page"})
        return out
    context={"dossier_id":case["dossier_id"],"pair_ref":case["family_id"],
      "au_product":{"product_id":au["item_id"],"raw_file":au_rel,"sha256":au_src["raw_sha256"],"title":au["title_raw"],"title_span":at,
                     "axis_names":{"row":row_name,"column":col_name}},"au_rows":rows,
      "rakuten_product":{"url":rak["url"],"raw_file":rak_rel,"sha256":rak_sha,"encoding":enc,"title":rak["title_raw"],"title_span":rt,
                          "selector_families":families},"color_vocab":sorted(color),"variant_tokens":list(tokens),
      "au_description_lines":desc_lines("au"),
      "rakuten_description_lines":desc_lines("rakuten"),"au_purchase_option_lines":[],
      "size_code_crosswalks":[],"excluded_non_identity_fields":list(gates.NON_IDENTITY_FIELDS)}
    return context

def build_case(case, ctx, store):
    sel=case["rakuten"]["selected_sku"]
    variant=sel["variant_id"]
    rel=sel["source_ref"]["raw_file"]
    enc=ctx["rakuten_product"]["encoding"]
    vals=src.rakuten_selected_values(store,rel,enc,variant)
    families=ctx["rakuten_product"]["selector_families"]
    axes=[]
    for i,opt in enumerate(sel["option_values"]):
        fam=families[i]
        if fam["key"] != opt["axis_key"] or vals[i].get("quote") != opt["value"]:
            raise ValueError(f"{case['case_id']}: selector order/value mismatch")
        axes.append({"axis_index":i,"axis_key":opt["axis_key"],"axis_label":fam["label"] or fam["key"],
                     "axis_label_span":fam["label_span"],"value":opt["value"],"value_span":vals[i],"family_values":fam["values"]})
    case_id="case-"+hashlib.sha1(case["case_id"].encode()).hexdigest()[:20]
    return {"schema_version":"sku-gate-case-input-v1","task_version":gates.TASK_VERSION,"case_id":case_id,
      "dossier_id":case["dossier_id"],"group_id":case["family_id"],"split":"dev","au_product_id":ctx["au_product"]["product_id"],
      "rakuten_selected":{"url":case["rakuten"]["url"],"raw_file":rel,"sha256":sel["source_ref"]["raw_sha256"],
        "variant_id":variant,"source_row_key":sel["source_sku_key"],"source_sku_key":sel["source_sku_key"],
        "sku_record_key":variant,"source_row_index":0,"axes":axes,
        "variant_attributes":src.rakuten_variant_attributes(store,rel,enc,variant)},"excluded_non_identity_fields":list(gates.NON_IDENTITY_FIELDS),
      "_novel_case_id":case["case_id"]}

def main():
    if OUT.exists(): raise FileExistsError(f"Refusing to overwrite {OUT}")
    manifest=json.loads((INPUT/"manifest.json").read_text())
    inputs=read(INPUT/"inputs.jsonl"); source_cases=read(INPUT/"source-cases.jsonl")
    if (len(inputs)!=56 or len(source_cases)!=56
        or manifest["inputs_sha256"]!=EXPECTED_INPUTS_SHA256 or sha(INPUT/"inputs.jsonl")!=EXPECTED_INPUTS_SHA256
        or manifest["source_cases_sha256"]!=EXPECTED_SOURCE_CASES_SHA256
        or sha(INPUT/"source-cases.jsonl")!=EXPECTED_SOURCE_CASES_SHA256):
        raise ValueError("Input manifest/hash/count mismatch")
    store=src.RawStore(ROOT)
    contexts={}; cases=[]
    for x in inputs:
        if x["dossier_id"] not in contexts: contexts[x["dossier_id"]]=build_context(x,store)
        cases.append(build_case(x,contexts[x["dossier_id"]],store))
    for c in contexts.values():
        for row in c["au_rows"]:
            for axis in row["axes"]:
                if not axis["axis_name_span"] or not axis["value_span"]: raise ValueError("Missing exact AU axis span")
        for line in c["au_description_lines"]+c["rakuten_description_lines"]:
            if not line["span"]: raise ValueError("Missing exact current-page description span")
    # Source inputs are frozen before any machine decisions are generated.
    source_files=sorted(store._sha)
    code_files=[CLAUDE/n for n in ("sku_gate_atoms.py","sku_gate_sources.py","sku_gates.py","run_sku_gates.py")]
    OUT.mkdir(parents=True)
    write_new(OUT/"products.jsonl",jline([contexts[k] for k in sorted(contexts)]))
    write_new(OUT/"cases.jsonl",jline([{k:v for k,v in x.items() if not k.startswith("_")} for x in cases]))
    write_new(OUT/"case-id-map.json",json.dumps({c["case_id"]:c["_novel_case_id"] for c in cases},ensure_ascii=False,indent=2)+"\n")
    freeze={"frozen_at_utc":datetime.now(timezone.utc).isoformat(),"input_manifest_sha256":sha(INPUT/"manifest.json"),
      "inputs_sha256":sha(INPUT/"inputs.jsonl"),"source_cases_sha256":sha(INPUT/"source-cases.jsonl"),
      "task_code_sha256":{str(p):sha(p) for p in code_files+[Path(__file__)]},"prepared_inputs_sha256":{"products.jsonl":sha(OUT/"products.jsonl"),"cases.jsonl":sha(OUT/"cases.jsonl"),"case-id-map.json":sha(OUT/"case-id-map.json")},
      "raw_source_sha256":store._sha,"labels_read":False,"gpu_or_model_used":False,"network_used":False,
      "methods":["A","B"],"source_configs":["full"],"fixed_au_per_case":True,"all_au_rows_retained":True,"sibling_rerouting":False}
    write_new(OUT/"freeze.json",json.dumps(freeze,ensure_ascii=False,indent=2)+"\n")
    freeze_hash=sha(OUT/"freeze.json")
    facts={k:gates.PairFacts(v) for k,v in contexts.items()}; evaluators={"A":{},"B":{}}
    results={"A":[],"B":[]}; start=time.time()
    for ci in cases:
        for method in ("A","B"):
            key=ci["dossier_id"]
            if key not in evaluators[method]: evaluators[method][key]=gates.make_evaluator(method,facts[key],"full")
            result=gates.run_method(method,{k:v for k,v in ci.items() if not k.startswith("_")},facts[key],"full",evaluators[method][key])
            results[method].append(result)
    prediction_dir=OUT/"predictions"; prediction_dir.mkdir()
    for method,rows in results.items(): write_new(prediction_dir/f"{method}-full.jsonl",jline(rows))
    # A-only omits unknown AU constraints; report it distinctly as a baseline limitation.
    counts={}
    for method,rows in results.items():
        counts[method]={}
        for r in rows: counts[method][r["decision"]]=counts[method].get(r["decision"],0)+1
    diagnostics={}
    for method, rows in results.items():
        diagnostics[method]={
          "unresolved_requirement_cases":sum(r["reason"]=="unresolved_requirement" for r in rows),
          "row_status_counts":dict(sum((__import__("collections").Counter(r["row_status_counts"]) for r in rows),__import__("collections").Counter())),
          "unknown_atom_results":sum(a.get("status")=="unknown" for r in rows for row in r.get("rows",[]) for a in row.get("atom_results",[])),
          "au_only_atom_total":sum(len(row.get("au_only_atoms",[])) for r in rows for row in r.get("rows",[]))}
    write_new(prediction_dir/"manifest.json",json.dumps({"freeze_sha256":freeze_hash,"labels_read":False,"machine_diagnostics_only":True,
      "source_config":"full","decision_counts":counts,"diagnostics":diagnostics,"elapsed_seconds":round(time.time()-start,2),"case_count":len(cases)},indent=2)+"\n")
    # Output is not a gold/holdout evaluation. Unknown-omission diagnostic is explicit.
    summary={"input_count":len(inputs),"family_count":len(contexts),"au_rows_per_case_total":sum(len(x["au"]["sku_rows"]) for x in inputs),
      "inputs_sha256":EXPECTED_INPUTS_SHA256,"source_cases_sha256":EXPECTED_SOURCE_CASES_SHA256,
      "fixed_current_page_urls":sorted({x["rakuten"]["url"] for x in inputs}),"decision_counts":counts,"diagnostics":diagnostics,
      "method_a_unknown_omission":"Method A is the unchanged structural gate. Unknown requirements do not establish positive identity; A outputs remain machine diagnostics.",
      "machine_diagnostics_only":True,"human_gold_or_holdout_claim":False,"labels_read":False,"gpu_or_model_used":False,"network_used":False,
      "known_parse_gaps":["HG020 per-bag counts (本)","FCA2160 mats枚/畳", "MBC005 top_board", "QAA0100 stepcount/width"]}
    write_new(OUT/"report.json",json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
