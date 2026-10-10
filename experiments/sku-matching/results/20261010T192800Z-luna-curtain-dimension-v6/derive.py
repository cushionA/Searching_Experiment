"""Revalidate frozen v5 signatures with the narrow curtain 丈 alias.

No matching or dimension inference is repeated. Fresh semantic reviews cover
only the six cases whose dimension gate changes; original v5 files stay intact.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
OLD = EXP / "results/20261010T185500Z-luna-multirow62"
DIR = HERE / "derived"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_curtain_dimension_alias as alias


def frozen_sources():
    protocol = core.read(OLD / "frozen-protocol.json")
    paths = [OLD / "frozen-protocol.json"]
    paths += [EXP / f for f in protocol["code_sha256"]]
    paths += [OLD / f for f in protocol["artifact_scripts_sha256"]]
    paths += list((OLD / "inference").rglob("*.json"))
    paths += [OLD / "inference/links.jsonl", EXP / "sku_luna_curtain_dimension_alias.py", HERE / "derive.py"]
    return {str(p.relative_to(EXP)): core.sha(p.read_bytes()) for p in paths}


def install_guard():
    cases = {c["case_id"]: c for c in core.read(DIR / "inputs.json")}
    contexts = core.read(DIR / "source-contexts.json")
    registry = {}
    for entry in core.read(DIR / "signature-v3-manifest.json")["entries"]:
        case = cases[entry["case_id"]]
        row = next(r for r in case["au_rows"] if r["row_key"] == entry["row_key"])
        source_map = core.smoke.row_sources(case, row)
        identity = core.canonical([entry["targets"], source_map, row["conditions"]])
        assert identity not in registry
        contextual = copy.deepcopy(source_map)
        for sid, context in contexts[case["case_id"]].items():
            contextual[sid].update({k: context[k] for k in ("kind", "scope")})
        registry[identity] = (case, contextual)

    def scoped_guard(targets, answer, sources, conditions):
        identity = core.canonical([targets, sources, conditions])
        case, contextual = registry[identity]
        return alias.guard_curtain_dimensions(case, targets, answer, contextual, conditions)

    core.guard_dimensions = scoped_guard


def prepare():
    assert not DIR.exists()
    before = frozen_sources()
    ignore = shutil.ignore_patterns("review-batches", "review-batch-answers", "final-reviews.json", "reviewed-summary.json", "links.jsonl")
    shutil.copytree(OLD / "inference", DIR, ignore=ignore)
    install_guard()
    old_signatures = core.read(OLD / "inference/signature-v3-evaluation.json")
    signatures = core.signature_results(DIR, "signature-v3")
    changed = [cid for cid in signatures if signatures[cid]["passed"] != old_signatures[cid]["passed"]]
    assert len(changed) == 6 and all(signatures[cid]["passed"] for cid in changed)
    assert sum(s["passed"] for s in signatures.values()) == 13
    requests = {c["case_id"]: (batch, c) for batch in [core.read(OLD / e["request"]) for e in core.read(OLD / "review-dispatch-manifest.json")["entries"]] for c in batch["cases"]}
    entries = []
    (HERE / "review-requests").mkdir()
    (HERE / "review-answers").mkdir()
    for index, cid in enumerate(changed, 1):
        batch, item = requests[cid]
        item = copy.deepcopy(item)
        item["dimension_verification"] = {k: signatures[cid].get(k) for k in ("passed", "applicable", "validation_ok", "guard_issues", "expanded_checks")}
        request = {"instructions": batch["instructions"] + "\nこのrequestは1caseだけである。提示された現在のprediction/dimension_verificationと原文を確認し、object配列1件を返す。過去の判定は与えられていない。", "cases": [item]}
        path = HERE / f"review-requests/{index:02}.json"
        core.save(path, request)
        entries.append({"case_id": cid, "case_ids": [cid], "request": f"review-requests/{index:02}.json", "answer": f"review-answers/{index:02}.json", "request_sha256": core.sha(path.read_bytes())})
    assert before == frozen_sources()
    core.save(HERE / "review-dispatch-manifest.json", {"planned_calls": len(entries), "case_coverage": len(entries), "labels_read": False, "entries": entries})
    core.save(HERE / "frozen-protocol.json", {"createdUTC": datetime.now(timezone.utc).isoformat(), "method": "v6 curtain-specific local dimension alias; frozen v5 matching/signature answers reused; isolated semantic review of changed six gates", "new_matching_calls": 0, "new_dimension_calls": 0, "new_semantic_review_calls": 6, "changed_case_ids": changed, "source_sha256": before, "review_requests_sha256": {e["request"]: e["request_sha256"] for e in entries}, "human_verified": False, "modelVersion": None, "usage": None})
    print(json.dumps({"changed_gates": len(changed), "dimensions_passed": 13, "reviews": len(entries)}))


def collect():
    protocol = core.read(HERE / "frozen-protocol.json")
    assert protocol["source_sha256"] == frozen_sources()
    install_guard()
    entries = core.read(HERE / "review-dispatch-manifest.json")["entries"]
    reviews = core.read(OLD / "inference/final-reviews.json")
    replacements = {}
    cases = {c["case_id"]: c for c in core.read(DIR / "inputs.json")}
    contexts = core.read(DIR / "source-contexts.json")
    ev = {c["case_id"]: c for c in core.read(DIR / "evaluation.json")["cases"]}
    signatures = core.signature_results(DIR, "signature-v3")
    for entry in entries:
        assert core.sha((HERE / entry["request"]).read_bytes()) == entry["request_sha256"]
        answer = core.read(HERE / entry["answer"])
        assert isinstance(answer, list) and [r["case_id"] for r in answer] == entry["case_ids"]
        review = answer[0]
        cid = review["case_id"]
        assert set(review) == {"case_id", "verdict", "row_key", "reason", "review_evidence_ids"}
        assert review["verdict"] in {"confirm", "reject", "unresolved"} and review["reason"]
        ids = review["review_evidence_ids"]
        assert isinstance(ids, list) and len(ids) == len(set(ids)) and all(isinstance(s, str) for s in ids)
        allowed = {s["source_id"] for s in cases[cid]["sources"]} | {c["condition_id"] for row in cases[cid]["au_rows"] for c in row["conditions"]}
        assert set(ids) <= allowed and all(contexts[cid][s]["scope"] == "fixed_product" for s in ids if s.startswith("S"))
        if review["verdict"] == "confirm":
            assert ids and ev[cid]["decision"] == "candidate" and signatures[cid]["passed"]
            assert review["row_key"] == signatures[cid]["row_key"] == ev[cid]["candidate_row_key"]
            assert signatures[cid]["candidate_answer_sha256"] == ev[cid]["answer_sha256"]
            row = next(r for r in cases[cid]["au_rows"] if r["row_key"] == review["row_key"])
            assert all(s in {c["condition_id"] for c in row["conditions"]} for s in ids if s.startswith("A:"))
        else:
            assert review["row_key"] is None
            if review["verdict"] == "reject":
                assert ids
        replacements[cid] = review
    assert set(replacements) == set(protocol["changed_case_ids"])
    core.save(DIR / "final-reviews.json", [replacements.get(r["case_id"], r) for r in reviews])
    summary = core.export_links(DIR, DIR / "final-reviews.json", "signature-v3")
    assert protocol["source_sha256"] == frozen_sources()
    print(json.dumps(summary["counts"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "collect"))
    args = parser.parse_args()
    prepare() if args.command == "prepare" else collect()
