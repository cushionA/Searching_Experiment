#!/usr/bin/env python3
"""Freeze review inputs, retaining row-specific evidence proposed by matching."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
DIR = HERE / "inference"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_task_policy as policy

STAGE = "signature-v3"
COUNT = 62
TRACE_RULE = """
matching_evidence_traceはローカル復元した照合の仮説であり、正解ではない。条件ID、現在行、根拠IDを対応させて原文で再確認する。全行に直接の型・寸法・数量・材質の矛盾があればその矛盾を確認しrejectする。確認できる明示矛盾を単に『一致根拠不足』に置き換えない。ただし別商品・別部位・別条件の根拠なら追認せずunresolvedにする。confirm/rejectには必ずその判定を裏付けるreview_evidence_idsを1件以上返す。N/Aは登録寸法なしであり、物理寸法一致の証明ではない。
"""


def save(path, obj):
    core.save(path, obj)


def prepare():
    cases = core.read(DIR / "inputs.json")
    assert len(cases) == COUNT
    ev = {c["case_id"]: c for c in core.read(DIR / "evaluation.json")["cases"]}
    signatures = core.read(DIR / (STAGE + "-evaluation.json"))
    contexts = core.read(DIR / "source-contexts.json")
    old = EXP / "results/20261010T174640Z-luna-expanded50/review-requests/01.json"
    instruction = core.read(old)["instructions"] + policy.REVIEW_V3_ADDITION + TRACE_RULE
    (DIR / "review-batches").mkdir()
    (DIR / "review-batch-answers").mkdir()
    payloads = []
    for case in cases:
        cid = case["case_id"]
        blocks, index, sources = {}, {}, {}
        for sid, source in contexts[cid].items():
            refs = []
            for text in source["contexts"]:
                if text not in index:
                    bid = f"B{len(blocks)}"
                    blocks[bid], index[text] = text, bid
                refs.append(index[text])
            assert [blocks[b] for b in refs] == source["contexts"]
            sources[sid] = {k: source[k] for k in ("kind", "text", "scope")}
            sources[sid]["context_ids"] = refs
        trace = []
        for row in ev[cid].get("expanded_response", {}).get("rows", []):
            item = {"row_key": row["row_key"]}
            for side in ("rakuten_checks", "au_checks"):
                item[side] = [{"condition_id": c["condition_id"], "status": c["status"],
                               "source_ids": [e["source_id"] for e in c["evidence"]]}
                              for c in row[side]]
            trace.append(item)
        payloads.append({
            "case_id": cid,
            "rakuten_selected_conditions": [{"axis": c["axis"], "value": c["value"]} for c in case["rakuten_conditions"]],
            "rakuten_selected_attributes": [{k: a.get(k) for k in ("axis", "value", "unit")} for a in case["selected_attributes"]],
            "au_rows": [{"row_key": r["row_key"], "conditions": [{"source_id": c["condition_id"], "axis": c["axis"], "value": c["value"]} for c in r["conditions"]]} for r in case["au_rows"]],
            "au_sources": sources, "au_context_blocks": blocks,
            "prediction": {k: ev[cid].get(k) for k in ("decision", "candidate_row_key", "validation_ok", "validation_reason", "guard_issues")},
            "matching_evidence_trace": trace,
            "dimension_verification": {k: signatures.get(cid, {}).get(k) for k in ("passed", "applicable", "validation_ok", "guard_issues", "expanded_checks")},
        })
    entries = []
    for batch, start in enumerate(range(0, COUNT, 10), 1):
        subset = payloads[start:start + 10]
        request = DIR / f"review-batches/{batch:02}.json"
        request.write_text(json.dumps({"instructions": instruction + f"\nこのbatchの全{len(subset)}caseを指定case_id順にobject配列でレビューする。別caseの証拠を流用しない。", "cases": subset}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        entries.append({"case_id": f"review-{batch:02}", "case_ids": [c["case_id"] for c in subset],
                        "request": f"inference/review-batches/{batch:02}.json", "answer": f"inference/review-batch-answers/{batch:02}.json",
                        "request_sha256": core.sha(request.read_bytes())})
    save(HERE / "review-dispatch-manifest.json", {"planned_calls": len(entries), "case_coverage": COUNT, "labels_read": False,
         "review_task_version": "sku-semantic-review-v3-with-prediction-trace-v1", "entries": entries})
    print(json.dumps({"batches": len(entries), "cases": COUNT}))


def collect():
    manifest = core.read(HERE / "review-dispatch-manifest.json")
    reviews = []
    for entry in manifest["entries"]:
        assert core.sha((HERE / entry["request"]).read_bytes()) == entry["request_sha256"]
        answer = core.read(HERE / entry["answer"])
        assert isinstance(answer, list) and [c["case_id"] for c in answer] == entry["case_ids"]
        reviews.extend(answer)
    assert len(reviews) == len({r["case_id"] for r in reviews}) == COUNT
    cases = {c["case_id"]: c for c in core.read(DIR / "inputs.json")}
    contexts = core.read(DIR / "source-contexts.json")
    ev = {c["case_id"]: c for c in core.read(DIR / "evaluation.json")["cases"]}
    signatures = core.read(DIR / (STAGE + "-evaluation.json"))
    for review in reviews:
        assert set(review) == {"case_id", "verdict", "row_key", "reason", "review_evidence_ids"}
        cid = review["case_id"]
        assert review["verdict"] in {"confirm", "reject", "unresolved"}
        assert isinstance(review["reason"], str) and review["reason"]
        ids = review["review_evidence_ids"]
        assert isinstance(ids, list) and len(ids) == len(set(ids)) and all(isinstance(s, str) for s in ids)
        allowed = {s["source_id"] for s in cases[cid]["sources"]}
        allowed.update(c["condition_id"] for row in cases[cid]["au_rows"] for c in row["conditions"])
        assert set(ids) <= allowed
        assert all(contexts[cid][s]["scope"] == "fixed_product" for s in ids if s.startswith("S"))
        if review["verdict"] == "confirm":
            assert ids and ev[cid]["decision"] == "candidate" and signatures[cid]["passed"]
            assert review["row_key"] == ev[cid]["candidate_row_key"]
            assert signatures[cid]["row_key"] == ev[cid]["candidate_row_key"]
            assert signatures[cid]["candidate_answer_sha256"] == ev[cid]["answer_sha256"]
            row = next(r for r in cases[cid]["au_rows"] if r["row_key"] == review["row_key"])
            row_ids = {c["condition_id"] for c in row["conditions"]}
            assert all(s in row_ids for s in ids if s.startswith("A:"))
        else:
            assert review["row_key"] is None
            if review["verdict"] == "reject":
                assert ids, "reject requires explicit current-product evidence"
    save(DIR / "final-reviews.json", reviews)
    print(json.dumps(core.export_links(DIR, DIR / "final-reviews.json", STAGE)["counts"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "collect"))
    args = parser.parse_args()
    prepare() if args.command == "prepare" else collect()
