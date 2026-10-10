"""Frozen source-driven V8 matching repair, dimension verification and isolated review."""
from __future__ import annotations
import argparse, copy, json, shutil, sys, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / "results/20261010T185500Z-luna-multirow62/inference"
V6 = EXP / "results/20261010T192800Z-luna-curtain-dimension-v6"
V6DIR = V6 / "derived"
SIGDIR = EXP / "results/20261010T193100Z-luna-curtain-facet-v7/derived-v2"
BASE = HERE / "derived"
DERIVED = HERE / "repaired"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_curtain_task as facet_task
import sku_luna_curtain_dimension_alias as alias
import sku_luna_compact_context_task as compact
import sku_luna_closed_contents_task as closed


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


def norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).lower()


def selected_dimensions(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)×(\d+(?:\.\d+)?)cm", norm(value))
    return tuple(match.groups()) if match else None


def row_dimensions(value):
    match = re.fullmatch(r"幅(\d+(?:\.\d+)?)×丈(\d+(?:\.\d+)?)cm\(\d+枚(?:組)?\)", norm(value))
    return tuple(match.groups()) if match else None


def repair_prepare():
    if (HERE / "matching-repair-freeze.json").exists(): raise RuntimeError("matching repair already frozen")
    cases = core.read(BASE / "inputs.json")
    contexts = core.read(BASE / "source-contexts.json")
    evalmap = {c["case_id"]: c for c in core.read(BASE / "evaluation.json")["cases"]}
    covered = {}
    for ent in core.read(HERE / "facet-dispatch-manifest.json")["entries"]:
        checks = core.read(HERE / ent["answer"])["checks"]
        for cid in ent["covered_case_ids"]: covered[cid] = checks
    entries = []
    (HERE / "repair-matching-requests").mkdir(exist_ok=False)
    (HERE / "repair-matching-answers").mkdir(exist_ok=False)
    for case in cases:
        cid = case["case_id"]
        rvalues = {c["axis"]: c["value"] for c in case["rakuten_conditions"]}
        dims = selected_dimensions(rvalues["サイズ"])
        compatible = []
        for i, row in enumerate(case["au_rows"]):
            values = {c["axis"]: c["value"] for c in row["conditions"]}
            if (dims and row_dimensions(values["サイズ"]) == dims
                and norm(values["カラー"]) == norm(rvalues["カラー"])
                and covered[cid][i]["status"] == "support"):
                compatible.append(row["row_key"])
        if len(compatible) != 1 or evalmap[cid]["decision"] == "candidate": continue
        req = compact.compact_request(case, contexts[cid])
        index = len(entries) + 1
        rp = HERE / f"repair-matching-requests/{index:02}.json"
        core.save(rp, req)
        entries.append({"case_id": cid, "request": str(rp.relative_to(HERE)),
                        "answer": f"repair-matching-answers/{index:02}.json",
                        "request_sha256": hashfile(rp), "prior_decision": evalmap[cid]["decision"],
                        "source_compatible_row_key": compatible[0],
                        "selection_reason": "unique exact selected color/width/length row with supported independent lace facet; existing machine noncandidate"})
    paths = [HERE / "repair.py", EXP / "sku_luna_compact_context_task.py", EXP / "sku_luna_closed_contents_task.py",
             HERE / "assemble-freeze.json", HERE / "frozen-protocol.json", BASE / "manifest.json", BASE / "evaluation.json",
             BASE / "inputs.json", BASE / "source-contexts.json", SIGDIR / "signature-v3-manifest.json", SIGDIR / "signature-v3-evaluation.json"]
    paths += [BASE / ent["answer"] for ent in core.read(BASE / "manifest.json")["entries"]]
    paths += [HERE / ent["answer"] for ent in core.read(HERE / "facet-dispatch-manifest.json")["entries"]]
    core.save(HERE / "matching-dispatch-manifest-v2.json", {"planned_calls": len(entries), "labels_read": False, "entries": entries})
    freeze_once(HERE / "matching-repair-freeze.json", {"createdUTC": utc(), "labels_read": False,
      "selection_uses": "selected source metadata and frozen machine outputs only; no gold labels/verdicts",
      "source_sha256": {str(p.resolve().relative_to(EXP.resolve())): hashfile(p) for p in paths},
      "request_sha256": {e["request"]: e["request_sha256"] for e in entries}})
    print(json.dumps({"new_matching_tasks": len(entries), "case_ids": [e["case_id"] for e in entries]}))


def repair_collect():
    supplement = core.read(HERE / "matching-repair-supplement-freeze.json")
    for rel, expected in supplement["sha256"].items():
        if hashfile(EXP / rel) != expected: raise ValueError(f"supplement changed: {rel}")
    freeze = core.read(HERE / "matching-repair-freeze.json")
    for rel, expected in freeze["source_sha256"].items():
        if hashfile(EXP / rel) != expected: raise ValueError(f"source changed: {rel}")
    entries = core.read(HERE / "matching-dispatch-manifest-v2.json")["entries"]
    for ent in entries:
        if hashfile(HERE / ent["request"]) != ent["request_sha256"]: raise ValueError("request changed")
        if not (HERE / ent["answer"]).exists(): raise FileNotFoundError(HERE / ent["answer"])
    if DERIVED.exists(): raise RuntimeError("refusing overwrite repaired round")
    ignore = shutil.ignore_patterns("evaluation.json", "final-reviews.json", "reviewed-summary.json", "links.jsonl", "signature-v3-*", "review-batches", "review-batch-answers")
    shutil.copytree(BASE, DERIVED, ignore=ignore)
    manifest = core.read(DERIVED / "manifest.json")
    mindex = {e["case_id"]: e for e in manifest["entries"]}
    for ent in entries:
        m = mindex[ent["case_id"]]
        prior_sha = hashfile(DERIVED / m["answer"])
        (DERIVED / m["request"]).write_bytes((HERE / ent["request"]).read_bytes())
        (DERIVED / m["answer"]).write_bytes((HERE / ent["answer"]).read_bytes())
        m["request_sha256"] = ent["request_sha256"]
        m["output_origin"] = {"method": "fresh whole matching with lossless compact context and bounded contents; raw response unchanged",
           "raw_response_sha256": hashfile(DERIVED / m["answer"]), "prior_composed_answer_sha256": prior_sha,
           "matching_repair_freeze_sha256": hashfile(HERE / "matching-repair-freeze.json")}
    manifest["task_version"] = "v8-bounded-contents-compact-repaired"
    manifest["new_whole_matching_calls"] = len(entries)
    core.save(DERIVED / "manifest.json", manifest)
    ev = core.evaluate(DERIVED)
    generated = core.prepare_signature(DERIVED, "signature-v3")
    old_entries = {e["case_id"]: e for e in core.read(SIGDIR / "signature-v3-manifest.json")["entries"]}
    old_pass = core.read(SIGDIR / "signature-v3-evaluation.json")
    evmap = {e["case_id"]: e for e in ev["cases"]}
    lineage = []
    for ent in generated:
        cid = ent["case_id"]
        old = old_entries.get(cid)
        ent["candidate_answer_sha256"] = evmap[cid]["answer_sha256"]
        reused = bool(old and old_pass[cid]["passed"] and old["row_key"] == ent["row_key"] and old["targets"] == ent["targets"])
        if reused:
            old_req, old_ans = SIGDIR / old["request"], SIGDIR / old["answer"]
            (DERIVED / ent["request"]).write_bytes(old_req.read_bytes())
            (DERIVED / ent["answer"]).write_bytes(old_ans.read_bytes())
            ent["request_sha256"] = hashfile(DERIVED / ent["request"])
            ent["reused_from"] = {"round": "v7", "request_sha256": hashfile(old_req), "answer_sha256": hashfile(old_ans),
                "old_candidate_answer_sha256": old.get("candidate_answer_sha256"), "new_candidate_answer_sha256": ent["candidate_answer_sha256"]}
        else:
            rp = DERIVED / ent["request"]
            req = core.read(rp)
            text = req["body"]["contents"][0]["parts"][0]["text"]
            rule = "現行の固定AUカーテンの同じ部位で『幅…×丈…cm』等の幅/丈明記があれば、幅は本体横幅、丈は本体高さに対応する。袖丈・股下・収納寸法・別部位/別商品は使わず、元の単位表記を保つ。明示された幅違いは順序変更で覆さない。直接の本文根拠がなければunknownのままとする。"
            req["body"]["contents"][0]["parts"][0]["text"] = text.replace("INPUT=", rule + "\nINPUT=", 1)
            core.save(rp, req)
            ent["request_sha256"] = hashfile(rp)
            ent["task_version"] = "v8-curtain-width-length"
        lineage.append({"case_id": cid, "row_key": ent["row_key"], "reused_passed_signature": reused,
                        "candidate_answer_sha256": ent["candidate_answer_sha256"], "request_sha256": ent["request_sha256"]})
    core.save(DERIVED / "signature-v3-manifest.json", {"schema": "selected-dimensions-verification-v3", "entries": generated})
    calls = [e for e in generated if not e.get("reused_from") and e.get("inference_required", True)]
    core.save(HERE / "repaired-signature-dispatch-manifest.json", {"planned_calls": len(calls), "labels_read": False,
        "entries": [{**e, "request": "repaired/" + e["request"], "answer": "repaired/" + e["answer"]} for e in calls]})
    core.save(HERE / "lineage.json", {"case_count": 62, "new_distinct_cases": 0,
        "new_facet_tasks": 2, "reused_product_facet_answers": 2, "new_whole_matching_tasks": len(entries),
        "new_dimension_tasks": len(calls), "reused_passed_signature_count": len(generated) - len(calls),
        "method": "bounded closed-contents faceting, source-selected compact wholematching repair, narrow scoped dimensions, isolated reviews",
        "signatures": lineage, "createdUTC": utc()})
    core.save(HERE / "matching-repair-results.json", {"createdUTC": utc(), "entries": [{**e, "answer_sha256": hashfile(HERE / e["answer"]),
         "validation_ok": evmap[e["case_id"]]["validation_ok"], "decision": evmap[e["case_id"]]["decision"]} for e in entries]})
    print(json.dumps({"counts": ev["counts"], "new_dimension_tasks": len(calls), "reused_signature_count": len(generated)-len(calls)}))


def reviews_prepare():
    if not DERIVED.exists(): raise RuntimeError("run assemble first")
    signature_entries = core.read(DERIVED / "signature-v3-manifest.json")["entries"]
    if any(not (DERIVED / e["answer"]).exists() for e in signature_entries): raise RuntimeError("signature answers must be complete first")
    freeze_path = HERE / "repaired-reviews-freeze.json"
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
        bounded = closed._raw_contents_block(cases[cid], contexts[cid])
        item["bounded_contents"] = {"source_id": bounded["source_id"], "scope": bounded["scope"], "kind": bounded["kind"],
            "html_row": bounded["raw_bounded_html_row"], "next_section_label": bounded["next_section_label"]}
        # Remove only batch cardinality/output boilerplate; preserve substantive reviewer policy.
        instruction_lines = [line for line in old_instructions.splitlines() if not ("このbatchの全" in line and "case" in line)]
        instruction = "\n".join(instruction_lines)
        instruction += "\n" + facet_task.SINGLE_CASE_REVIEW_ADDITION
        instruction += "\nAU全行・選択済み楽天条件・固定AU本文を直接確認してください。候補かつdimension gate passedの場合だけconfirm可能です。確認不能はunresolved、全候補で明示矛盾ならreject。"
        instruction += "\nbounded_contentsは固定商品の内容HTML行全体と直後のsection。幅別の内容一覧・数量・SKUの枚組が整合し一覧が完結するなら、記載された同梱物だけを判断する根拠になる。単なる断片に記載がない場合は不存在の根拠にならない。全AU行・条件と原文scopeを維持する。"
        instruction += "\n出力はJSON object配列ちょうど1件。各objectのキーはcase_id, verdict, row_key, reason, review_evidence_ids。"
        req = {"instructions": instruction, "cases": [item]}
        path = HERE / f"review-requests/{i:02}.json"; core.save(path, req)
        entries.append({"case_id": cid, "case_ids": [cid], "request": f"review-requests/{i:02}.json", "answer": f"review-answers/{i:02}.json", "request_sha256": hashfile(path)})
    core.save(HERE / "repaired-review-dispatch-manifest.json", {"planned_calls": len(entries), "case_coverage": len(entries), "labels_read": False, "entries": entries})
    freeze_once(freeze_path, {"createdUTC": utc(), "prepare_sha256": hashfile(HERE / "repair_v2.py"),
                              "assemble_freeze_sha256": hashfile(HERE / "matching-repair-freeze.json"),
                              "derived_evaluation_sha256": hashfile(DERIVED / "evaluation.json"),
                              "signature_evaluation_sha256": hashfile(DERIVED / "signature-v3-evaluation.json"),
                              "review_requests_sha256": {e["request"]: e["request_sha256"] for e in entries},
                              "coverage": len(entries), "labels_read": False})
    print(json.dumps({"reviews": len(entries)}))


def reviews_collect():
    if not (HERE / "repaired-reviews-freeze.json").exists(): raise RuntimeError("review freeze missing")
    freeze = core.read(HERE / "repaired-reviews-freeze.json")
    if freeze["prepare_sha256"] != hashfile(HERE / "repair_v2.py"): raise ValueError("driver changed after reviews freeze")
    install_guard()
    entries = core.read(HERE / "repaired-review-dispatch-manifest.json")["entries"]
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
    (HERE / "links-v8.jsonl").write_bytes((DERIVED / "links.jsonl").read_bytes())
    print(json.dumps(summary.get("counts", {})))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("command", choices=("repair-prepare", "repair-collect", "reviews-prepare", "reviews-collect")); args = ap.parse_args()
    {"repair-prepare": repair_prepare, "repair-collect": repair_collect, "reviews-prepare": reviews_prepare, "reviews-collect": reviews_collect}[args.command]()
if __name__ == "__main__": main()
