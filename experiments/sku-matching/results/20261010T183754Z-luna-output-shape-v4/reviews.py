#!/usr/bin/env python3
"""Prepare lossless review batches, then validate original reviewer answers."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_task_policy as policy


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    entries = []
    # Reuse the exact v2 review instruction; only the improved arm gets v3 text.
    old = EXP / "results/20261010T174640Z-luna-expanded50/review-requests/01.json"
    base_instruction = read(old)["instructions"]
    for arm, stage in (("baseline", "signature-v3"), ("improved", "signature-v3")):
        directory = HERE / arm
        cases = read(directory / "inputs.json")
        predictions = {r["case_id"]: r for r in read(directory / "evaluation.json")["cases"]}
        signatures = read(directory / (stage + "-evaluation.json"))
        contexts = read(directory / "source-contexts.json")
        (directory / "review-batches").mkdir()
        (directory / "review-batch-answers").mkdir()
        payloads = []
        instruction = base_instruction + policy.REVIEW_V3_ADDITION
        instruction += "\nこのbatchの全10caseをレビューし、指定case_id順にobject配列を返す。別caseの証拠は流用しない。"
        for case in cases:
            cid = case["case_id"]
            blocks, index, sources = {}, {}, {}
            for sid, source in contexts[cid].items():
                refs = []
                for text in source["contexts"]:
                    if text not in index:
                        block = f"B{len(blocks)}"
                        blocks[block], index[text] = text, block
                    refs.append(index[text])
                assert [blocks[x] for x in refs] == source["contexts"]
                sources[sid] = {k: source[k] for k in ("kind", "text", "scope")}
                sources[sid]["context_ids"] = refs
            payloads.append({
                "case_id": cid,
                "rakuten_selected_conditions": [{"axis": c["axis"], "value": c["value"]} for c in case["rakuten_conditions"]],
                "rakuten_selected_attributes": [{k: a.get(k) for k in ("axis", "value", "unit")} for a in case["selected_attributes"]],
                "au_rows": [{"row_key": r["row_key"], "conditions": [{"source_id": c["condition_id"], "axis": c["axis"], "value": c["value"]} for c in r["conditions"]]} for r in case["au_rows"]],
                "au_sources": sources, "au_context_blocks": blocks,
                "prediction": {k: predictions[cid].get(k) for k in ("decision", "candidate_row_key", "validation_ok", "validation_reason", "guard_issues")},
                "dimension_verification": {k: signatures.get(cid, {}).get(k) for k in ("passed", "applicable", "validation_ok", "guard_issues", "expanded_checks")},
            })
        for batch, start in enumerate(range(0, len(payloads), 10), 1):
            subset = payloads[start:start + 10]
            path = directory / f"review-batches/{batch:02}.json"
            path.write_text(json.dumps({"instructions": instruction, "cases": subset}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            entries.append({"case_id": f"{arm}-review-{batch:02}", "case_ids": [c["case_id"] for c in subset], "arm": arm,
                            "request": f"{arm}/review-batches/{batch:02}.json", "answer": f"{arm}/review-batch-answers/{batch:02}.json", "request_sha256": digest(path)})
    save(HERE / "review-dispatch-manifest.json", {"planned_calls": len(entries), "case_coverage_per_arm": 20, "labels_read": False, "entries": entries})
    print(json.dumps({"prepared_batches": len(entries)}))


def collect():
    manifest = read(HERE / "review-dispatch-manifest.json")
    all_reviews = {"baseline": [], "improved": []}
    for entry in manifest["entries"]:
        assert digest(HERE / entry["request"]) == entry["request_sha256"], "review request changed"
        answers = read(HERE / entry["answer"])
        assert isinstance(answers, list) and [a["case_id"] for a in answers] == entry["case_ids"], "coverage/order"
        all_reviews[entry["arm"]].extend(answers)
    result = {}
    for arm, stage in (("baseline", "signature-v3"), ("improved", "signature-v3")):
        directory = HERE / arm
        cases = {c["case_id"]: c for c in read(directory / "inputs.json")}
        ev = {c["case_id"]: c for c in read(directory / "evaluation.json")["cases"]}
        contexts = read(directory / "source-contexts.json")
        dimensions = read(directory / (stage + "-evaluation.json"))
        reviews = all_reviews[arm]
        assert len(reviews) == len({r["case_id"] for r in reviews}) == 20
        for review in reviews:
            assert set(review) == {"case_id", "verdict", "row_key", "reason", "review_evidence_ids"}
            cid = review["case_id"]
            case = cases[cid]
            assert review["verdict"] in {"confirm", "reject", "unresolved"}
            assert isinstance(review["reason"], str) and review["reason"]
            ids = review["review_evidence_ids"]
            assert isinstance(ids, list) and len(ids) == len(set(ids)) and all(isinstance(x, str) for x in ids)
            allowed = {s["source_id"] for s in case["sources"]}
            allowed.update(c["condition_id"] for row in case["au_rows"] for c in row["conditions"])
            assert set(ids) <= allowed, "foreign evidence ID"
            assert all(contexts[cid][s]["scope"] == "fixed_product" for s in ids if s.startswith("S")), "foreign source scope"
            if review["verdict"] == "confirm":
                assert ids and ev[cid]["decision"] == "candidate" and dimensions[cid]["passed"]
                assert review["row_key"] == ev[cid]["candidate_row_key"]
                row = next(r for r in case["au_rows"] if r["row_key"] == review["row_key"])
                row_ids = {c["condition_id"] for c in row["conditions"]}
                assert all(s in row_ids for s in ids if s.startswith("A:")), "other-row confirmation"
            else:
                assert review["row_key"] is None
        save(directory / "final-reviews.json", reviews)
        result[arm] = core.export_links(directory, directory / "final-reviews.json", stage)["counts"]
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "collect"))
    args = parser.parse_args()
    prepare() if args.command == "prepare" else collect()
