"""Frozen broad SKU task: fresh selected-only matching, dimensions, isolated review."""
from __future__ import annotations
import argparse, copy, json, sys
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
DIR = HERE / "inference"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_inputs as inputs
import sku_luna_task_policy as policy
import sku_luna_curtain_task as facet
import sku_luna_curtain_dimension_alias as alias
import sku_luna_compact_general_task as general


def utc(): return datetime.now(timezone.utc).isoformat()
def sha(p): return core.sha(Path(p).read_bytes())
def freeze(path, data):
    if path.exists(): raise RuntimeError(f"refusing overwrite {path}")
    core.save(path, data)


def verify_protocol():
    saved = core.read(HERE / "frozen-protocol.json")
    for rel, expected in saved["sha256"].items():
        if sha(EXP / rel) != expected: raise ValueError(f"protocol changed {rel}")


def prepare():
    selection = core.read(HERE / "selection.json")
    ids = selection["selected_case_ids"]
    cases, hashes = inputs.load_cases(inputs.DEFAULT_ZIP, ids)
    if len(cases) != 50 or len(set(ids)) != 50: raise ValueError("expected fifty unique cases")
    prior_ids = set(core.read(EXP / "results/20261010T185500Z-luna-multirow62/used-case-ids.json")["case_ids"])
    if set(ids) & prior_ids: raise ValueError("repeated case")
    prior_paths=[EXP/"results"/r/"inputs.json" for r in (
       "20261010T160225Z-luna-task-improvement/r02-reference-development","20261010T160225Z-luna-task-improvement/r03-reference-holdout",
       "20261010T174640Z-luna-expanded50","20261010T183000Z-luna-improvement-v3/improved","20261010T183754Z-luna-output-shape-v4/improved","20261010T195200Z-luna-closed-contents-v8/repaired")]
    prior=[c for path in prior_paths for c in core.read(path)]
    for identity in (lambda c:(c["rakuten_sku_key"],c["au_product_id"]),lambda c:(c["au_product_id"],c["rakuten_url"],c["rakuten_variant_id"])):
        old={identity(c) for c in prior};new=[identity(c) for c in cases]
        if len(set(new))!=50 or set(new)&old:raise ValueError("historical source identity repeats")
    # All code and selected inputs are frozen before the first provider task.
    manifest = core.prepare(DIR, cases, hashes, inputs.DEFAULT_ZIP, mode="scoped-v3", selection=selection)
    contexts = core.read(DIR / "source-contexts.json")
    sizes = []
    for entry in manifest["entries"]:
        case = next(c for c in cases if c["case_id"] == entry["case_id"])
        rp = DIR / entry["request"]
        old_size = rp.stat().st_size
        request = general.compact_request(case, contexts[case["case_id"]])
        core.save(rp, request)
        entry["request_sha256"] = sha(rp)
        sizes.append({"case_id": case["case_id"], "original_scoped_bytes": old_size, "compact_bytes": rp.stat().st_size})
    manifest["task_version"] = "broad-selected-only-lossless-context-v9"
    core.save(DIR / "manifest.json", manifest)
    entries=[]
    (HERE/"matching-chunk-requests").mkdir(exist_ok=False);(HERE/"matching-chunk-answers").mkdir(exist_ok=False)
    for case in cases:
        cid=case["case_id"]
        for start in range(0,len(case["au_rows"]),27):
            chunk=copy.deepcopy(case);chunk["au_rows"]=chunk["au_rows"][start:start+27]
            request=general.compact_request(chunk,contexts[cid]);index=len(entries)+1
            rp=HERE/f"matching-chunk-requests/{index:03}.json";core.save(rp,request)
            entries.append({"case_id":f"{cid}:rows:{start}:{start+len(chunk['au_rows'])}","original_case_id":cid,
              "row_start":start,"row_count":len(chunk["au_rows"]),"request":f"matching-chunk-requests/{index:03}.json",
              "answer":f"matching-chunk-answers/{index:03}.json","request_sha256":sha(rp)})
    core.save(HERE / "matching-dispatch-manifest.json", {"planned_calls":len(entries),"distinct_case_count":50,"candidate_rows_per_task_max":27,"labels_read":False,"entries":entries})
    core.save(HERE / "request-size-audit.json", {"cases": sizes, "lossless_contexts": True})
    modules = ["trial_luna_sku_matching.py", "trial_gemini_sku_smoke_v2.py", "sku_luna_inputs.py", "sku_luna_source_context.py",
      "sku_luna_dimension_guard.py", "sku_luna_task_policy.py", "sku_luna_output_shape.py", "sku_luna_curtain_dimension_alias.py",
      "sku_luna_curtain_task.py", "sku_luna_closed_contents_task.py", "sku_luna_compact_general_task.py", "evaluate_frozen_sku_accuracy.py"]
    (HERE/"initial-matching-manifest.json").write_bytes((DIR/"manifest.json").read_bytes())
    paths = [EXP/m for m in modules]+[HERE/"run.py", HERE/"selection.json", HERE/"selection.py", HERE/"matching-dispatch-manifest.json", HERE/"initial-matching-manifest.json",DIR/"inputs.json",DIR/"source-contexts.json"]+[HERE/e["request"] for e in entries]+[DIR/e["request"] for e in manifest["entries"]]
    freeze(HERE / "frozen-protocol.json", {"createdUTC":utc(), "labels_read":False, "new_distinct_cases":50,
       "source_archive_sha256":hashes["zip_sha256"], "sha256":{str(p.relative_to(EXP)):sha(p) for p in paths}})
    print(json.dumps({"matching_tasks":len(entries),"candidate_rows":sum(len(c['au_rows']) for c in cases)}))


def matching_collect():
    verify_protocol(); cases={c["case_id"]:c for c in core.read(DIR/"inputs.json")}
    chunks=core.read(HERE/"matching-dispatch-manifest.json")["entries"]
    grouped={cid:[] for cid in cases}
    for ent in chunks:
        if sha(HERE/ent["request"])!=ent["request_sha256"]:raise ValueError("chunk request changed")
        if not (HERE/ent["answer"]).exists():raise FileNotFoundError(HERE/ent["answer"])
        c=copy.deepcopy(cases[ent["original_case_id"]]);c["au_rows"]=c["au_rows"][ent["row_start"]:ent["row_start"]+ent["row_count"]]
        raw=core.read(HERE/ent["answer"]);ok,why=core.smoke.validate(c,raw)
        grouped[c["case_id"]].append((ent,raw,ok,why))
    manifest=core.read(DIR/"manifest.json")
    for ent in manifest["entries"]:
        parts=sorted(grouped[ent["case_id"]],key=lambda x:x[0]["row_start"]);start=0
        for piece,raw,ok,why in parts:
            if piece["row_start"]!=start:raise ValueError("chunk coverage gap/overlap")
            start+=piece["row_count"]
        if start!=len(cases[ent["case_id"]]["au_rows"]):raise ValueError("chunk row coverage incomplete")
        dest=DIR/ent["answer"]
        if dest.exists():raise RuntimeError("refusing overwrite composed answer")
        if len(parts)==1:
            dest.write_bytes((HERE/parts[0][0]["answer"]).read_bytes())
        elif all(ok for _,_,ok,_ in parts):
            core.save(dest,{"rows":[row for _,raw,_,_ in parts for row in raw["rows"]]})
        else:
            core.save(dest,{"chunk_errors":[{"request":piece["request"],"answer":piece["answer"],"validation_reason":why} for piece,raw,ok,why in parts if not ok]})
        ent["output_origin"]={"method":"single raw task byte copy" if len(parts)==1 else "input-order local row concatenation from unchanged raw chunk responses",
           "all_chunks_valid":all(ok for _,_,ok,_ in parts),"composed_answer_sha256":sha(dest),"chunks":[{**piece,"answer_sha256":sha(HERE/piece["answer"]),"validation_ok":ok,"validation_reason":why} for piece,raw,ok,why in parts]}
    core.save(DIR/"manifest.json",manifest)
    # Source/intended full requests unchanged; only origin metadata was appended.
    protocol=core.read(HERE/"frozen-protocol.json")
    if str((DIR/"manifest.json").relative_to(EXP)) in protocol["sha256"]:raise ValueError("manifest unexpectedly frozen before origin composition")
    ev=core.evaluate(DIR)
    core.save(HERE/"matching-chunk-evaluation.json",{"task_count":len(chunks),"validation_pass":sum(ok for ps in grouped.values() for _,_,ok,_ in ps),"case_count":50,"row_count":sum(len(c['au_rows']) for c in cases.values()),"cases_valid":sum(e["validation_ok"] for e in ev["cases"]),"counts":ev["counts"]})
    print(json.dumps(core.read(HERE/"matching-chunk-evaluation.json")))


def signature_prepare():
    verify_protocol()
    ev = core.evaluate(DIR)
    generated = core.prepare_signature(DIR, "signature-v3")
    # Extend only explicit current-curtain width/丈 correspondence; retain all previous policies.
    for entry in generated:
        rp = DIR / entry["request"]
        req = core.read(rp)
        txt = req["body"]["contents"][0]["parts"][0]["text"]
        rule = "固定AUカーテンの同じ部位の『幅…×丈…cm』では幅=本体横幅、丈=本体高さに対応する。袖丈/股下/収納時/別商品/別部位を混ぜず、直接原文が無ければunknown。明示矛盾を数字の並べ替えで覆さない。"
        req["body"]["contents"][0]["parts"][0]["text"] = txt.replace("INPUT=",rule+"\nINPUT=",1)
        core.save(rp,req); entry["request_sha256"]=sha(rp)
    core.save(DIR/"signature-v3-manifest.json",{"schema":"selected-dimensions-verification-v3","entries":generated})
    calls=[e for e in generated if e.get("inference_required",True)]
    core.save(HERE/"signature-dispatch-manifest.json",{"planned_calls":len(calls),"labels_read":False,"entries":[{**e,"request":"inference/"+e["request"],"answer":"inference/"+e["answer"]} for e in calls]})
    freeze(HERE/"signature-freeze.json",{"createdUTC":utc(),"evaluation_sha256":sha(DIR/"evaluation.json"),"manifest_sha256":sha(DIR/"signature-v3-manifest.json"),"request_sha256":{e["request"]:e["request_sha256"] for e in generated}})
    print(json.dumps({"counts":ev["counts"],"dimension_tasks":len(calls),"local_NA":len(generated)-len(calls)}))


def install_guard():
    cases={c["case_id"]:c for c in core.read(DIR/"inputs.json")}; contexts=core.read(DIR/"source-contexts.json");registry={}
    for ent in core.read(DIR/"signature-v3-manifest.json")["entries"]:
        c=cases[ent["case_id"]];row=next(r for r in c["au_rows"] if r["row_key"]==ent["row_key"]);sources=core.smoke.row_sources(c,row)
        identity=core.canonical([ent["targets"],sources,row["conditions"]]); scoped=copy.deepcopy(sources)
        for sid,ctx in contexts[c["case_id"]].items():
            if sid in scoped:scoped[sid].update({k:ctx[k] for k in ("kind","scope")})
        if identity in registry:
            previous,old_scoped=registry[identity]
            if previous["au_product_id"]!=c["au_product_id"] or core.canonical(old_scoped)!=core.canonical(scoped):raise ValueError("ambiguous scoped signature identity")
        registry[identity]=(c,scoped)
    def guard(targets,answer,sources,conditions):
        c,scoped=registry[core.canonical([targets,sources,conditions])]
        return alias.guard_curtain_dimensions(c,targets,answer,scoped,conditions)
    core.guard_dimensions=guard


def reviews_prepare():
    verify_protocol()
    entries=core.read(DIR/"signature-v3-manifest.json")["entries"]
    if any(not (DIR/e["answer"]).exists() for e in entries):raise RuntimeError("dimension answers incomplete")
    install_guard(); ev={e["case_id"]:e for e in core.evaluate(DIR)["cases"]}; sig=core.signature_results(DIR,"signature-v3")
    cases=core.read(DIR/"inputs.json"); contexts=core.read(DIR/"source-contexts.json")
    instruction=core.read(EXP/"results/20261010T174640Z-luna-expanded50/review-requests/01.json")["instructions"]+policy.REVIEW_V3_ADDITION+facet.SINGLE_CASE_REVIEW_ADDITION
    instruction+="\nmatching_evidence_traceは仮説であり正解ではない。全AU行の選択条件・原文scopeを直接確認する。候補かつ寸法passedのみconfirm。登録寸法0のN/Aは物理的同一性の証明ではない。rejectは全行の明示矛盾を直接引用、確認不能ならunresolved。配列ちょうど1件、キーはcase_id/verdict/row_key/reason/review_evidence_ids。"
    (HERE/"review-requests").mkdir(exist_ok=False);(HERE/"review-answers").mkdir(exist_ok=False); dispatch=[]
    for i,c in enumerate(cases,1):
        cid=c["case_id"];blocks={};index={};sources={}
        for sid,s in contexts[cid].items():
            refs=[]
            for txt in s["contexts"]:
                if txt not in index:index[txt]=f"B{len(blocks)}";blocks[index[txt]]=txt
                refs.append(index[txt])
            sources[sid]={k:s[k] for k in ("kind","scope","text")};sources[sid]["context_ids"]=refs
        trace=[]
        expanded=ev[cid].get("expanded_response") or {}
        for row in expanded.get("rows",[]):
            trace.append({"row_key":row["row_key"],**{side:[{"condition_id":v["condition_id"],"status":v["status"],"source_ids":[e["source_id"] for e in v["evidence"]]} for v in row[side]] for side in ("rakuten_checks","au_checks")}})
        item={"case_id":cid,"rakuten_selected_conditions":[{"axis":r["axis"],"value":r["value"]} for r in c["rakuten_conditions"]],
          "rakuten_selected_attributes":[{k:a.get(k) for k in ("axis","value","unit")} for a in c["selected_attributes"]],
          "au_rows":[{"row_key":r["row_key"],"conditions":[{"source_id":v["condition_id"],"axis":v["axis"],"value":v["value"]} for v in r["conditions"]]} for r in c["au_rows"]],
          "au_sources":sources,"au_context_blocks":blocks,"prediction":{k:ev[cid].get(k) for k in ("decision","candidate_row_key","validation_ok","validation_reason","guard_issues")},
          "matching_evidence_trace":trace,"dimension_verification":{k:sig.get(cid,{}).get(k) for k in ("passed","applicable","validation_ok","guard_issues","expanded_checks")}}
        # Restore current-row condition references locally in review traces.
        row_by_key={r["row_key"]:r for r in c["au_rows"]}
        def scoped_ref(ref,row):
            if ref.startswith("A") and ref[1:].isdigit():
                idx=int(ref[1:])
                if idx>=len(row["conditions"]):raise ValueError("AU reference out of current row")
                return row["conditions"][idx]["condition_id"]
            return ref
        for tr in trace:
            row=row_by_key[tr["row_key"]]
            for side in ("rakuten_checks","au_checks"):
                for ck in tr[side]:ck["source_ids"]=[scoped_ref(ref,row) for ref in ck["source_ids"]]
        current=sig.get(cid,{})
        if current.get("expanded_checks") is not None:
            row=row_by_key[current["row_key"]]
            item["dimension_verification"]["expanded_checks"]=[{
              "target":{k:check["target"].get(k) for k in ("axis","value","unit")},"status":check["status"],
              "evidence":[{"source_id":scoped_ref(e["source_id"],row),"quote":e["quote"]} for e in check["evidence"]]} for check in current["expanded_checks"]]
        # Matching payload already carries bounded full contents when applicable.
        me=next(e for e in core.read(DIR/"manifest.json")["entries"] if e["case_id"]==cid)
        req=core.read(DIR/me["request"]);payload=json.loads(req["body"]["contents"][0]["parts"][0]["text"].split("INPUT=",1)[1])
        if "bounded_contents" in payload:item["bounded_contents"]=payload["bounded_contents"]
        rp=HERE/f"review-requests/{i:02}.json";core.save(rp,{"instructions":instruction,"cases":[item]})
        dispatch.append({"case_id":cid,"case_ids":[cid],"request":f"review-requests/{i:02}.json","answer":f"review-answers/{i:02}.json","request_sha256":sha(rp)})
    core.save(HERE/"review-dispatch-manifest.json",{"planned_calls":50,"case_coverage":50,"labels_read":False,"entries":dispatch})
    freeze(HERE/"review-freeze.json",{"createdUTC":utc(),"run_sha256":sha(HERE/"run.py"),"evaluation_sha256":sha(DIR/"evaluation.json"),"signature_sha256":sha(DIR/"signature-v3-evaluation.json"),"request_sha256":{e["request"]:e["request_sha256"] for e in dispatch}})
    print(json.dumps({"review_tasks":50,"signature_passed":sum(s["passed"] for s in sig.values()),"signature_total":len(sig)}))


def reviews_collect():
    verify_protocol(); install_guard();frozen=core.read(HERE/"review-freeze.json")
    if frozen["run_sha256"]!=sha(HERE/"run.py"):raise ValueError("review driver changed")
    cases={c["case_id"]:c for c in core.read(DIR/"inputs.json")};contexts=core.read(DIR/"source-contexts.json")
    ev={e["case_id"]:e for e in core.evaluate(DIR)["cases"]};sig=core.signature_results(DIR,"signature-v3");reviews=[]
    for ent in core.read(HERE/"review-dispatch-manifest.json")["entries"]:
        if sha(HERE/ent["request"])!=ent["request_sha256"]:raise ValueError("review request changed")
        arr=core.read(HERE/ent["answer"]);cid=ent["case_id"]
        if not isinstance(arr,list) or len(arr)!=1 or arr[0].get("case_id")!=cid:raise ValueError("invalid single review coverage")
        r=arr[0]
        if set(r)!={"case_id","verdict","row_key","reason","review_evidence_ids"} or r["verdict"] not in {"confirm","reject","unresolved"} or not isinstance(r["reason"],str) or not r["reason"]:raise ValueError("invalid review fields")
        ids=r["review_evidence_ids"]
        if not isinstance(ids,list) or not all(isinstance(s,str) for s in ids) or len(set(ids))!=len(ids):raise ValueError("invalid review refs")
        allowed={s["source_id"] for s in cases[cid]["sources"]}|{c["condition_id"] for row in cases[cid]["au_rows"] for c in row["conditions"]}
        if not set(ids)<=allowed or any(contexts[cid][s]["scope"]!="fixed_product" for s in ids if s.startswith("S")):raise ValueError("review source/scope mismatch")
        if r["verdict"]=="confirm":
            if not ids or ev[cid]["decision"]!="candidate" or not sig.get(cid,{}).get("passed") or r["row_key"]!=ev[cid]["candidate_row_key"] or r["row_key"]!=sig[cid]["row_key"] or sig[cid]["candidate_answer_sha256"]!=ev[cid]["answer_sha256"]:raise ValueError("confirm gates/row/hash fail")
            row=next(row for row in cases[cid]["au_rows"] if row["row_key"]==r["row_key"])
            if any(s.startswith("A:") and s not in {c["condition_id"] for c in row["conditions"]} for s in ids):raise ValueError("condition from wrong row")
        elif r["row_key"] is not None or r["verdict"]=="reject" and not ids:raise ValueError("nonconfirm evidence fail")
        reviews.append(r)
    if {r["case_id"] for r in reviews}!=set(cases):raise ValueError("review coverage mismatch")
    core.save(DIR/"final-reviews.json",reviews);summary=core.export_links(DIR,DIR/"final-reviews.json","signature-v3")
    (HERE/"links-v9.jsonl").write_bytes((DIR/"links.jsonl").read_bytes());print(json.dumps(summary["counts"]))


def main():
    ap=argparse.ArgumentParser();ap.add_argument("command",choices=("prepare","matching-collect","signature-prepare","reviews-prepare","reviews-collect"));args=ap.parse_args()
    {"prepare":prepare,"matching-collect":matching_collect,"signature-prepare":signature_prepare,"reviews-prepare":reviews_prepare,"reviews-collect":reviews_collect}[args.command]()
if __name__=="__main__":main()
