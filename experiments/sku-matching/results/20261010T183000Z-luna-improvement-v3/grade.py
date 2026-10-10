#!/usr/bin/env python3
"""Grade frozen paired predictions outside the checkout, without inference."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from math import comb
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
EXP = REPO / "experiments/sku-matching"
sys.path.insert(0, str(EXP))
import evaluate_frozen_sku_accuracy as scorer


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidates(cases):
    return {c["case_id"]: {r["row_key"] for r in c["au_rows"]} for c in cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    args = parser.parse_args()
    external = args.evaluation_dir.resolve()
    assert REPO not in external.parents and external != REPO
    assert not external.exists(), "use a new external evaluation directory"
    frozen = read(HERE / "frozen-protocol.json")
    for file, expected in frozen["code_sha256"].items():
        assert sha(EXP / file) == expected, f"changed frozen code: {file}"
    assert sha(HERE / "selection.json") == frozen["selection_sha256"]
    old50 = EXP / "results/20261010T174640Z-luna-expanded50"
    spec = importlib.util.spec_from_file_location("previous_frozen_grader", old50 / "grade.py")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    inventory_file = EXP / "results/20261010T171543Z-labeled-sku-accuracy/source-inventory.json"
    payloads, records = loader.load_label_bytes(read(inventory_file), REPO)
    labels = [json.loads(line) for data in payloads.values() for line in data.decode().splitlines() if line.strip()]
    assert len(labels) == 1439
    inputs = read(HERE / "baseline/inputs.json")
    assert inputs == read(HERE / "improved/inputs.json") and len(inputs) == 50
    old18 = EXP / "results/20261010T160225Z-luna-task-improvement"
    old_inputs = read(old18 / "r02-reference-development/inputs.json") + read(old18 / "r03-reference-holdout/inputs.json") + read(old50 / "inputs.json")
    all_inputs = old_inputs + inputs
    assert len({c["case_id"] for c in all_inputs}) == 118
    assert len({(c["rakuten_sku_key"], c["au_product_id"]) for c in all_inputs}) == 118
    previous = read(old18 / "summary.json")["cases"] + read(old50 / "reviewed-summary.json")["cases"]
    final = {arm: read(HERE / arm / "reviewed-summary.json")["cases"] for arm in ("baseline", "improved")}
    assert all([c["case_id"] for c in final[arm]] == [c["case_id"] for c in inputs] for arm in final)
    sources = [inventory_file, HERE / "frozen-protocol.json", HERE / "selection.json", old50 / "grade.py", Path(__file__)]
    sources += [EXP / file for file in frozen["code_sha256"]]
    sources += [HERE / arm / file for arm in final for file in ("inputs.json", "manifest.json", "reviewed-summary.json", "final-reviews.json", "links.jsonl")]
    sources += [old18 / "summary.json", old50 / "reviewed-summary.json"]
    before = {str(path): sha(path) for path in sources}
    scored = {arm: scorer.score(pred, labels, candidates(inputs)) for arm, pred in final.items()}
    for arm in final:
        ev = read(HERE / arm / "evaluation.json")["cases"]
        raw = [{"case_id": r["case_id"], "decision": "adopt" if r["decision"] == "candidate" else r["decision"], "row_key": r["candidate_row_key"] if r["decision"] == "candidate" else None} for r in ev]
        scored[arm + "_matching"] = scorer.score(raw, labels, candidates(inputs))
    scored["cumulative_118"] = scorer.score(previous + final["improved"], labels, candidates(all_inputs))
    bmap, imap = ({r["case_id"]: r for r in scored[a]["cases"]} for a in ("baseline", "improved"))
    paired = []
    for c in inputs:
        cid = c["case_id"]
        b, i = bmap[cid], imap[cid]
        paired.append({"case_id": cid, "baseline_correct": b["correct"], "improved_correct": i["correct"],
                       "baseline_decision": b["prediction_decision"], "improved_decision": i["prediction_decision"],
                       "label_decision": b["label_decision"]})
    gains = sum(r["baseline_correct"] is False and r["improved_correct"] is True for r in paired)
    losses = sum(r["baseline_correct"] is True and r["improved_correct"] is False for r in paired)
    n = gains + losses
    exact_p = min(1.0, 2 * sum(comb(n, k) for k in range(min(gains, losses) + 1)) / 2**n) if n else 1.0
    assert before == {str(path): sha(path) for path in sources}, "frozen sources changed"
    external.mkdir()
    (external / "labels").mkdir()
    for tag, data in payloads.items():
        (external / "labels" / (tag + "-labels.jsonl")).write_bytes(data)
    for tag, score in scored.items():
        destination = external / tag
        destination.mkdir()
        save(destination / "summary.json", {"metrics": score["metrics"], "case_count": len(score["cases"]), "human_verified": False})
        (destination / "case-results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in score["cases"]), encoding="utf-8")
    comparison = {"metrics": {tag: s["metrics"] for tag, s in scored.items()},
                  "paired": {"gains": gains, "losses": losses, "known_count": sum(r["baseline_correct"] is not None for r in paired),
                             "mcnemar_exact_two_sided_p_exploratory": exact_p, "cases": paired},
                  "human_verified": False, "remaining_unprocessed_label_count": 1439 - 118,
                  "limitations": ["existing Luna machine labels", "same-source fresh SKU variants", "single trial per arm", "SKU cases correlated by AU product; exploratory p assumes independent pairs"]}
    save(external / "comparisons.json", comparison)
    save(external / "grading-source-manifest.json", {"createdUTC": datetime.now(timezone.utc).isoformat(), "labels": records,
         "all_source_sha256_pre": before, "all_source_sha256_post": {str(path): sha(path) for path in sources},
         "source_hashes_unchanged": True, "new_inference_calls": 0, "human_verified": False})
    saved = HERE / "grading/comparisons.json"
    if saved.exists():
        assert read(saved) == comparison, "reproduction differs"
    print(json.dumps({tag: s["metrics"]["known_case_accuracy"] for tag, s in scored.items()}))


if __name__ == "__main__":
    main()
