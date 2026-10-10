"""V7 additive assembly and single-case review preparation/collection."""
from __future__ import annotations
import argparse, copy, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / "results/20261010T185500Z-luna-multirow62/inference"
V6 = EXP / "results/20261010T192800Z-luna-curtain-dimension-v6"
V6DIR = V6 / "derived"
DERIVED = HERE / "derived"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_curtain_task as facet_task
import sku_luna_curtain_dimension_alias as alias


def utc(): return datetime.now(timezone.utc).isoformat()
def hashfile(p): return core.sha(Path(p).read_bytes())
def freeze_once(path, data):
    if path.exists(): raise RuntimeError(f"freeze exists: {path}")
    core.save(path, data)


def source_freeze():
    """Capture inputs/code/request lineage and all delivered facet artifact hashes."""
    protocol = core.read(HERE / "frozen-protocol.json")
    paths = [HERE / "frozen-protocol.json", HERE / "facet_prepare.py", HERE / "facet-dispatch-manifest.json",
             EXP / "sku_luna_curtain_task.py", EXP / "sku_luna_curtain_dimension_alias.py",
             EXP / "trial_luna_sku_matching.py", OLD / "manifest.json", OLD / "inputs.json", OLD / "source-contexts.json",
             OLD / "links.jsonl", V6DIR / "signature-v3-evaluation.json", V6DIR / "signature-v3-manifest.json",
             V6 / "frozen-protocol.json"]
    paths += [OLD / e["request"] for e in core.read(OLD / "manifest.json")["entries"]]
    paths += [OLD / e["answer"] for e in core.read(OLD / "manifest.json")["entries"]]
    paths += [OLD / e["request"] for e in core.read(OLD / "signature-v3-manifest.json")["entries"]] if (OLD / "signature-v3-manifest.json").exists() else []
    paths += [OLD / e["answer"] for e in core.read(OLD / "signature-v3-manifest.json")["entries"]] if (OLD / "signature-v3-manifest.json").exists() else []
    # The V7 initial protocol is owned by the parent; ensure its listed hashes still hold.
    for rel, expected in protocol.get("code_sha256", {}).items():
        p = EXP / rel
        if not p.exists() or hashfile(p) != expected: raise ValueError(f"frozen code changed: {rel}")
    for rel, expected in protocol["source_sha256"].items():
        if hashfile(EXP / rel) != expected: raise ValueError(f"initial source changed: {rel}")
    for entry in core.read(HERE / "facet-dispatch-manifest.json")["entries"]:
        paths += [HERE / entry["request"]]
        ap = HERE / entry["answer"]
        if not ap.exists(): raise FileNotFoundError(ap)
        paths += [ap]
    return {str(p.resolve().relative_to(EXP.resolve())): hashfile(p) for p in paths}


def install_guard(round_dir=DERIVED):
    cases = {c["case_id"]: c for c in core.read(round_dir / "inputs.json")}
    contexts = core.read(round_dir / "source-contexts.json")
    registry = {}
    for entry in core.read(round_dir / "signature-v3-manifest.json")["entries"]:
        case = cases[entry["case_id"]]
        row = next(r for r in case["au_rows"] if r["row_key"] == entry["row_key"])
        source_map = core.smoke.row_sources(case, row)
        identity = core.canonical([entry["targets"], source_map, row["conditions"]])
        if identity in registry: raise ValueError("duplicate signature registry identity")
        contextual = copy.deepcopy(source_map)
        for sid, context in contexts[case["case_id"]].items():
            if sid in contextual:
                contextual[sid].update({k: context[k] for k in ("kind", "scope")})
        registry[identity] = (case, contextual)
    def scoped_guard(targets, answer, sources, conditions):
        identity = core.canonical([targets, sources, conditions])
        case, contextual = registry[identity]
        return alias.guard_curtain_dimensions(case, targets, answer, contextual, conditions)
    core.guard_dimensions = scoped_guard


def assemble():
    freeze = HERE / "assemble-freeze.json"
    if freeze.exists(): raise RuntimeError("assembly already frozen")
    frozen = source_freeze()
    # Freeze the implementation and all inference inputs before derived data is assembled.
    freeze_once(freeze, {"createdUTC": utc(), "prepare_sha256": hashfile(HERE / "prepare.py"),
                         "source_sha256": frozen,
                         "facet_answers": {e["answer"]: hashfile(HERE / e["answer"])
                                           for e in core.read(HERE / "facet-dispatch-manifest.json")["entries"]}})
    # Verify every request SHA and every shared facet response before making a derived copy.
    fmanifest = core.read(HERE / "facet-dispatch-manifest.json")
    if fmanifest.get("planned_calls") != 4 or len(fmanifest.get("entries", [])) != 4: raise ValueError("facet manifest must contain four calls")
    facet_answers = {}
    for e in fmanifest["entries"]:
        rp, ap = HERE / e["request"], HERE / e["answer"]
        if hashfile(rp) != e["request_sha256"]: raise ValueError("facet request hash mismatch")
        ans = core.read(ap)
        req = core.read(rp)
        payload = json.loads(req["body"]["contents"][0]["parts"][0]["text"].split("INPUT=", 1)[1])
        ok, why = facet_task.validate_curtain_lace_answer({"request": req, "row_count": e["row_count"]}, ans)
        if not ok: raise ValueError(f"facet answer invalid: {e['request_key']}:{why}")
        facet_answers[e["request_key"]] = (ans, hashfile(ap), payload)
    if DERIVED.exists(): raise RuntimeError("derived output exists; refusing overwrite")
    ignore = shutil.ignore_patterns("evaluation.json", "final-reviews.json", "reviewed-summary.json", "links.jsonl",
                                    "signature-v3-*", "review-batches", "review-batch-answers")
    shutil.copytree(V6DIR, DERIVED, ignore=ignore)
    # Old answers and request/input metadata are copied unchanged except the one facet axis.
    case_by_id = {c["case_id"]: c for c in core.read(DERIVED / "inputs.json")}
    old_manifest = core.read(OLD / "manifest.json")
    covered = {}
    for e in fmanifest["entries"]:
        ans, ah, payload = facet_answers[e["request_key"]]
        for cid in e["covered_case_ids"]:
            if cid in covered: raise ValueError("overlapping facet coverage")
            covered[cid] = e
            case = case_by_id[cid]
            entry = {**e, "request": e["request"], "row_count": e["row_count"]}
            # Resolve the canonical answer path through the frozen original manifest.
            mentry = next(x for x in old_manifest["entries"] if x["case_id"] == cid)
            base_path = OLD / mentry["answer"]
            original = core.read(base_path)
            old_copy = core.read(DERIVED / mentry["answer"])
            if core.canonical(original) != core.canonical(old_copy): raise ValueError("V6 copy differs from frozen raw matching answer")
            expanded = facet_task.expand_curtain_lace_answer(case, original, entry, ans)
            out = DERIVED / mentry["answer"]
            core.save(out, expanded)
            # Verify only the Rakuten lace status/source fields changed in each row.
            ix = next(i for i, c in enumerate(case["rakuten_conditions"]) if c.get("axis") == "レースカーテン")
            for before, after in zip(original["rows"], expanded["rows"], strict=True):
                if set(before) != set(after) or len(before["rakuten_checks"]) != len(after["rakuten_checks"]): raise ValueError("row shape changed")
                for j, (bc, ac) in enumerate(zip(before["rakuten_checks"], after["rakuten_checks"], strict=True)):
                    if j != ix and core.canonical(bc) != core.canonical(ac): raise ValueError("non-lace Rakuten check changed")
                if core.canonical(before["au_checks"]) != core.canonical(after["au_checks"]): raise ValueError("AU checks changed")
    if set(covered) != set(case_by_id) or len(case_by_id) != 62: raise ValueError("facet case coverage incomplete")
    derived_manifest = core.read(DERIVED / "manifest.json")
    derived_manifest["task_version"] = "v7-factorized-lace-derived"
    derived_manifest["new_whole_matching_calls"] = 0
    for entry in derived_manifest["entries"]:
        fe = covered[entry["case_id"]]
        entry["output_origin"] = {"method": "frozen original answer with only Rakuten lace check replaced locally",
                                  "original_answer_sha256": hashfile(OLD / entry["answer"]),
                                  "facet_request_sha256": fe["request_sha256"],
                                  "facet_answer_sha256": facet_answers[fe["request_key"]][1],
                                  "derived_answer_sha256": hashfile(DERIVED / entry["answer"])}
    core.save(DERIVED / "manifest.json", derived_manifest)
    ev = core.evaluate(DERIVED)
    old_sig_eval = core.read(V6DIR / "signature-v3-evaluation.json")
    old_sig_manifest = core.read(V6DIR / "signature-v3-manifest.json")
    generated = core.prepare_signature(DERIVED, "signature-v3")
    current_eval = {x["case_id"]: x for x in ev["cases"]}
    old_entries = {e["case_id"]: e for e in old_sig_manifest["entries"]}
    new_entries = {e["case_id"]: e for e in generated}
    old_pass = {cid: result for cid, result in old_sig_eval.items() if result.get("passed") is True}
    lineage = []
    for cid, ent in new_entries.items():
        res = current_eval[cid]
        ent["candidate_answer_sha256"] = res["answer_sha256"]
        old = old_entries.get(cid)
        reused = bool(old and cid in old_pass and old.get("row_key") == ent["row_key"] and res["decision"] == "candidate")
        if reused:
            # Preserve original passed signature request and answer bytes exactly at V7 paths.
            old_req = V6DIR / old["request"]
            old_ans = V6DIR / old["answer"]
            new_req, new_ans = DERIVED / ent["request"], DERIVED / ent["answer"]
            new_req.write_bytes(old_req.read_bytes()); new_ans.write_bytes(old_ans.read_bytes())
            ent["request_sha256"] = hashfile(new_req)
            ent["reused_from"] = {"round": "v6", "request_sha256": hashfile(old_req), "answer_sha256": hashfile(old_ans),
                                  "old_candidate_answer_sha256": old.get("candidate_answer_sha256"),
                                  "new_candidate_answer_sha256": res["answer_sha256"]}
        else:
            p = DERIVED / ent["request"]
            req = core.read(p)
            text = req["body"]["contents"][0]["parts"][0]["text"]
            rule = "現行の固定AUカーテンの同じ部位で『幅…×丈…cm』等の幅/丈明記があれば、幅は本体横幅、丈は本体高さに対応する。袖丈・股下・収納寸法・別部位/別商品は使わず、元の単位表記を保つ。明示された幅違いは順序変更で覆さない。直接の本文根拠がなければunknownのままとする。"
            if "INPUT=" not in text: raise ValueError("signature request missing INPUT boundary")
            text = text.replace("INPUT=", rule + "\nINPUT=", 1)
            req["body"]["contents"][0]["parts"][0]["text"] = text
            core.save(p, req); ent["request_sha256"] = hashfile(p)
            ent["task_version"] = "v7-curtain-width-length"
        lineage.append({"case_id": cid, "row_key": ent["row_key"], "reused_v6_passed_signature": reused,
                        "request": ent["request"], "answer": ent["answer"],
                        "candidate_answer_sha256": res["answer_sha256"], "request_sha256": ent["request_sha256"]})
    core.save(DERIVED / "signature-v3-manifest.json", {"schema": "selected-dimensions-verification-v3", "entries": generated})
    # Only genuinely new model calls enter dispatch manifest.
    calls = [e for e in generated if not e.get("reused_from") and e.get("inference_required", True)]
    core.save(HERE / "signature-dispatch-manifest.json", {"planned_calls": len(calls), "labels_read": False,
              "entries": [{**e, "request": "derived/" + e["request"], "answer": "derived/" + e["answer"]} for e in calls]})
    core.save(HERE / "lineage.json", {"method": "four shared product/value facet calls composed with frozen raw matching answers; no new full matching calls",
              "new_whole_matching_calls": 0, "facet_call_count": 4, "case_count": 62,
              "facets": [{"request": e["request"], "answer": e["answer"], "request_sha256": e["request_sha256"], "answer_sha256": facet_answers[e["request_key"]][1], "covered_case_ids": e["covered_case_ids"]} for e in fmanifest["entries"]],
              "signatures": lineage, "createdUTC": utc()})
    frozen_after = source_freeze()
    if frozen_after != frozen: raise ValueError("source artifacts changed during assembly")
    print(json.dumps({"cases": len(case_by_id), "facet_calls": 4, "signature_calls": len(calls), "candidates": ev["counts"].get("candidate", 0)}))


def reviews_prepare():
    if not DERIVED.exists(): raise RuntimeError("run assemble first")
    signature_entries = core.read(DERIVED / "signature-v3-manifest.json")["entries"]
    if any(not (DERIVED / e["answer"]).exists() for e in signature_entries): raise RuntimeError("signature answers must be complete first")
    freeze_path = HERE / "reviews-freeze.json"
    if freeze_path.exists(): raise RuntimeError("review freeze already exists")
    install_guard()
    ev = core.evaluate(DERIVED)
    sig = core.signature_results(DERIVED, "signature-v3")
    cases = {c["case_id"]: c for c in core.read(DERIVED / "inputs.json")}
    contexts = core.read(DERIVED / "source-contexts.json")
    rows = {}
    # Source review requests only; do not load source review answers or verdicts.
    for ent in core.read(OLD.parent / "review-dispatch-manifest.json")["entries"]:
        batch = core.read(OLD.parent / ent["request"])
        for item in batch["cases"]: rows[item["case_id"]] = (batch["instructions"], item)
    if set(rows) != set(cases): raise ValueError("original review request coverage mismatch")
    (HERE / "review-requests").mkdir(exist_ok=False); (HERE / "review-answers").mkdir(exist_ok=False)
    evmap = {x["case_id"]: x for x in ev["cases"]}
    entries = []
    for i, cid in enumerate(cases, 1):
        old_instructions, item = rows[cid]
        item = copy.deepcopy(item)
        prediction = {"decision": evmap[cid]["decision"], "candidate_row_key": evmap[cid]["candidate_row_key"]}
        item["prediction"] = prediction
        item["dimension_verification"] = {k: sig[cid].get(k) for k in ("passed", "applicable", "validation_ok", "guard_issues", "expanded_checks")} if cid in sig else {"passed": False, "applicable": False, "validation_ok": True, "guard_issues": [], "expanded_checks": []}
        trace = []
        for row in evmap[cid]["expanded_response"]["rows"]:
            trace.append({"row_key": row["row_key"], **{side: [{"condition_id": check["condition_id"], "status": check["status"], "source_ids": [e["source_id"] for e in check["evidence"]]} for check in row[side]] for side in ("rakuten_checks", "au_checks")}})
        item["matching_evidence_trace"] = trace
        # Remove only batch cardinality/output boilerplate; preserve substantive reviewer policy.
        instruction_lines = [line for line in old_instructions.splitlines() if not ("このbatchの全" in line and "case" in line)]
        instruction = "\n".join(instruction_lines)
        instruction += "\n" + facet_task.SINGLE_CASE_REVIEW_ADDITION
        instruction += "\nAU全行・選択済み楽天条件・固定AU本文を直接確認してください。候補かつdimension gate passedの場合だけconfirm可能です。確認不能はunresolved、全候補で明示矛盾ならreject。"
        instruction += "\n出力はJSON object配列ちょうど1件。各objectのキーはcase_id, verdict, row_key, reason, review_evidence_ids。"
        req = {"instructions": instruction, "cases": [item]}
        path = HERE / f"review-requests/{i:02}.json"; core.save(path, req)
        entries.append({"case_id": cid, "case_ids": [cid], "request": f"review-requests/{i:02}.json", "answer": f"review-answers/{i:02}.json", "request_sha256": hashfile(path)})
    core.save(HERE / "review-dispatch-manifest.json", {"planned_calls": len(entries), "case_coverage": len(entries), "labels_read": False, "entries": entries})
    freeze_once(freeze_path, {"createdUTC": utc(), "prepare_sha256": hashfile(HERE / "prepare.py"),
                              "assemble_freeze_sha256": hashfile(HERE / "assemble-freeze.json"),
                              "derived_evaluation_sha256": hashfile(DERIVED / "evaluation.json"),
                              "signature_evaluation_sha256": hashfile(DERIVED / "signature-v3-evaluation.json"),
                              "review_requests_sha256": {e["request"]: e["request_sha256"] for e in entries},
                              "coverage": len(entries), "labels_read": False})
    print(json.dumps({"reviews": len(entries)}))


def reviews_collect():
    if not (HERE / "reviews-freeze.json").exists(): raise RuntimeError("review freeze missing")
    freeze = core.read(HERE / "reviews-freeze.json")
    if freeze["prepare_sha256"] != hashfile(HERE / "prepare.py"): raise ValueError("driver changed after reviews freeze")
    install_guard()
    entries = core.read(HERE / "review-dispatch-manifest.json")["entries"]
    cases = {c["case_id"]: c for c in core.read(DERIVED / "inputs.json")}
    contexts = core.read(DERIVED / "source-contexts.json")
    ev = {x["case_id"]: x for x in core.evaluate(DERIVED)["cases"]}
    sig = core.signature_results(DERIVED, "signature-v3")
    reviews = {}
    for e in entries:
        rp, ap = HERE / e["request"], HERE / e["answer"]
        if hashfile(rp) != e["request_sha256"]: raise ValueError("review request changed")
        arr = core.read(ap)
        if not isinstance(arr, list) or len(arr) != 1 or arr[0].get("case_id") != e["case_id"]: raise ValueError("review must be one exact case")
        r = arr[0]; cid = e["case_id"]
        if set(r) != {"case_id", "verdict", "row_key", "reason", "review_evidence_ids"}: raise ValueError("review fields invalid")
        if r["verdict"] not in {"confirm", "reject", "unresolved"} or not isinstance(r["reason"], str) or not r["reason"]: raise ValueError("review decision invalid")
        ids = r["review_evidence_ids"]
        if not isinstance(ids, list) or len(set(ids)) != len(ids): raise ValueError("review evidence invalid")
        case = cases[cid]
        allowed = {s["source_id"] for s in case["sources"]} | {c["condition_id"] for row in case["au_rows"] for c in row["conditions"]}
        if not set(ids) <= allowed: raise ValueError("review evidence outside case sources/conditions")
        if any(contexts[cid][sid]["scope"] != "fixed_product" for sid in ids if sid.startswith("S")): raise ValueError("non-fixed source cited")
        if r["verdict"] == "confirm":
            if not ids or ev[cid]["decision"] != "candidate" or cid not in sig or not sig[cid]["passed"]: raise ValueError("confirm gate failed")
            if r["row_key"] != ev[cid]["candidate_row_key"] or r["row_key"] != sig[cid]["row_key"]: raise ValueError("confirm row mismatch")
            if sig[cid]["candidate_answer_sha256"] != ev[cid]["answer_sha256"]: raise ValueError("confirm answer hash mismatch")
            row = next(x for x in case["au_rows"] if x["row_key"] == r["row_key"])
            if any(s.startswith("A") and s not in {c["condition_id"] for c in row["conditions"]} for s in ids): raise ValueError("condition evidence not scoped to row")
        else:
            if r["row_key"] is not None: raise ValueError("non-confirm row_key must be null")
            if r["verdict"] == "reject" and not ids: raise ValueError("reject requires evidence")
        reviews[cid] = r
    if set(reviews) != set(cases): raise ValueError("review coverage incomplete")
    core.save(DERIVED / "final-reviews.json", [reviews[cid] for cid in cases])
    summary = core.export_links(DERIVED, DERIVED / "final-reviews.json", "signature-v3")
    summary["method_lineage"] = core.read(HERE / "lineage.json")
    core.save(DERIVED / "reviewed-summary.json", summary)
    (HERE / "links-v7.jsonl").write_bytes((DERIVED / "links.jsonl").read_bytes())
    print(json.dumps(summary.get("counts", {})))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("command", choices=("prepare", "assemble", "reviews-prepare", "reviews-collect")); args = ap.parse_args()
    if args.command == "prepare": raise SystemExit("Facet preparation is owned by facet_prepare.py")
    {"assemble": assemble, "reviews-prepare": reviews_prepare, "reviews-collect": reviews_collect}[args.command]()
if __name__ == "__main__": main()
