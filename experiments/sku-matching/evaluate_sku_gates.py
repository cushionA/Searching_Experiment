"""Score frozen SKU-gate predictions against the machine diagnostic labels.

Runs only after `run_sku_gates.py predict`: it re-checks the freeze hashes and
the prediction manifest before opening labels. The labels are Luna machine
annotations (435 matched / 911 unmatched / 37 review), not human-verified, and
there is no independent holdout. Gold review rows stay in every denominator:
an accept of a gold-review row is counted as unconfirmed/unsafe, never as a
true positive.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sku_gates as runner  # noqa: E402
import sku_gate_sources as src  # noqa: E402

ROOT = runner.ROOT
LABELS = ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
BASELINE = ".lab-output/sku-structured-task-trials-20261010-v10/hybrid/predictions.jsonl"
COHORT = ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/inputs.jsonl"
CURTAIN_RAKUTEN = ("/weimall/ct0/", "/weimall/ctk/")


def ratio(num, den):
    return None if not den else num / den


def score(rows: list[tuple[dict, dict]]) -> dict:
    """rows: (gold, prediction) pairs. prediction has decision/top_row_key."""
    n = len(rows)
    gold = Counter(g["decision"] for g, _ in rows)
    pred = Counter(p["decision"] for _, p in rows)
    accepts = [(g, p) for g, p in rows if p["decision"] == "matched"]
    tp = sum(g["decision"] == "matched" and p["top_row_key"] in g["matching_au_row_keys"] for g, p in accepts)
    wrong_row = sum(g["decision"] == "matched" and p["top_row_key"] not in g["matching_au_row_keys"] for g, p in accepts)
    false_accept = sum(g["decision"] == "unmatched" for g, _ in accepts)
    unsafe = sum(g["decision"] == "review" for g, _ in accepts)
    known_accepts = [x for x in accepts if x[0]["decision"] != "review"]
    deletes = [(g, p) for g, p in rows if p["decision"] == "unmatched"]
    false_delete = sum(g["decision"] == "matched" for g, _ in deletes)
    delete_on_review = sum(g["decision"] == "review" for g, _ in deletes)
    true_delete = sum(g["decision"] == "unmatched" for g, _ in deletes)
    review = pred["review"]
    gold_review_rows = [(g, p) for g, p in rows if g["decision"] == "review"]
    return {
        "n": n, "gold": dict(gold), "pred": dict(pred),
        "accepts": len(accepts), "confirmed_true_accepts": tp, "wrong_row_accepts": wrong_row,
        "false_accepts_gold_unmatched": false_accept, "unsafe_accepts_gold_review": unsafe,
        "conservative_accepted_precision": ratio(tp, len(accepts)),
        "known_only_accepted_precision": ratio(tp, len(known_accepts)),
        "unsafe_accept_rate": ratio(unsafe, len(accepts)),
        "matched_recall_row_correct": ratio(tp, gold["matched"]),
        "unmatched_predictions": len(deletes), "true_unmatched": true_delete,
        "false_deletes_gold_matched": false_delete, "unmatched_on_gold_review": delete_on_review,
        "conservative_unmatched_precision": ratio(true_delete, len(deletes)),
        "false_delete_rate_of_gold_matched": ratio(false_delete, gold["matched"]),
        "unmatched_recall": ratio(true_delete, gold["unmatched"]),
        "review_rate": ratio(review, n), "coverage_auto_decision_rate": ratio(n - review, n),
        "gold_review_handling": dict(Counter(p["decision"] for _, p in gold_review_rows)),
    }


def macro(rows_by_product: dict[str, list]) -> dict:
    """Unweighted mean over fixed pairs; a metric is averaged only where defined."""
    per = {k: score(v) for k, v in rows_by_product.items()}
    out = {"products": len(per)}
    for key in ("conservative_accepted_precision", "known_only_accepted_precision", "matched_recall_row_correct",
                "conservative_unmatched_precision", "false_delete_rate_of_gold_matched", "review_rate",
                "coverage_auto_decision_rate", "unsafe_accept_rate"):
        vals = [m[key] for m in per.values() if m[key] is not None]
        out[key] = sum(vals) / len(vals) if vals else None
        out[key + "_defined_products"] = len(vals)
    return out


def load_predictions(out: Path, manifest: dict) -> dict[str, dict]:
    systems = {}
    for name, meta in manifest["files"].items():
        path = out / name
        if src.sha256_file(path) != meta["sha256"]:
            raise RuntimeError(f"Prediction file changed: {name}")
        stem = Path(name).stem  # A-full
        systems[stem] = {r["case_id"]: r for r in src.read_jsonl(path)}
    return systems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=runner.DEFAULT_OUT)
    parser.add_argument("--eval-dir", default="evaluation")
    args = parser.parse_args()
    out = ROOT / args.out
    freeze = runner.check_freeze(out)
    pmanifest = json.loads((out / "predictions" / "manifest.json").read_text(encoding="utf-8"))
    if pmanifest["freeze_sha256"] != src.sha256_file(out / "freeze.json"):
        raise RuntimeError("Predictions were not produced under this freeze")
    systems = load_predictions(out, pmanifest)
    # Only now are labels opened.
    labels_sha = src.sha256_file(ROOT / LABELS)
    labels = {r["case_id"]: r for r in src.read_jsonl(ROOT / LABELS)}
    cases = {r["case_id"]: r for r in src.read_jsonl(out / "inputs" / "cases.jsonl")}
    if set(labels) != set(cases):
        raise RuntimeError("Label/case sets differ")
    baseline = {r["case_id"]: r for r in src.read_jsonl(ROOT / BASELINE)}
    systems = {"baseline_v10_hybrid": baseline, **systems}
    cohort_rows = src.read_jsonl(ROOT / COHORT)
    cohort = {r["case_id"] for r in cohort_rows}
    curtain = {cid for cid, c in cases.items() if any(k in c["rakuten_selected"]["url"] for k in CURTAIN_RAKUTEN)}
    subsets = {"all": set(cases), "cohort196": cohort, "curtain": curtain, "non_curtain": set(cases) - curtain,
               "cohort196_curtain": cohort & curtain, "cohort196_non_curtain": cohort - curtain,
               "split_dev": {k for k, c in cases.items() if c["split"] == "dev"},
               "split_test": {k for k, c in cases.items() if c["split"] == "test"}}
    results = {}
    for system, preds in systems.items():
        if set(preds) != set(cases):
            raise RuntimeError(f"{system}: case set differs")
        results[system] = {}
        for subset, ids in subsets.items():
            rows = [(labels[c], preds[c]) for c in sorted(ids)]
            by_product = defaultdict(list)
            for c in sorted(ids):
                by_product[cases[c]["dossier_id"]].append((labels[c], preds[c]))
            results[system][subset] = {"row": score(rows), "product_macro": macro(by_product)}
    # Per-example audit of gold-review rows (kept inside the benchmark).
    review_audit = []
    for cid in sorted(c for c, g in labels.items() if g["decision"] == "review"):
        review_audit.append({"case_id": cid, "dossier_id": cases[cid]["dossier_id"],
                             "selected": " / ".join(f"{a['axis_label']}={a['value']}" for a in cases[cid]["rakuten_selected"]["axes"]),
                             "gold_rationale": labels[cid].get("rationale"),
                             "baseline": baseline[cid]["decision"],
                             "A_full": systems["A-full"][cid]["decision"], "B_full": systems["B-full"][cid]["decision"],
                             "A_reason": systems["A-full"][cid]["reason"], "B_reason": systems["B-full"][cid]["reason"]})
    disagreements = []
    for cid in sorted(cases):
        g = labels[cid]
        for system in ("A-full", "B-full"):
            p = systems[system][cid]
            wrong = (p["decision"] == "matched" and (g["decision"] != "matched" or p["top_row_key"] not in g["matching_au_row_keys"])) \
                or (p["decision"] == "unmatched" and g["decision"] == "matched")
            if wrong:
                disagreements.append({"system": system, "case_id": cid, "dossier_id": cases[cid]["dossier_id"],
                                      "selected": " / ".join(f"{a['axis_label']}={a['value']}" for a in cases[cid]["rakuten_selected"]["axes"]),
                                      "pred": p["decision"], "pred_row": p["top_row_key"], "reason": p["reason"],
                                      "gold": g["decision"], "gold_rows": g["matching_au_row_keys"],
                                      "gold_rationale": g.get("rationale")})
    eval_dir = out / args.eval_dir
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
               "freeze_sha256": src.sha256_file(out / "freeze.json"), "freeze_note": freeze.get("note"),
               "prediction_manifest_sha256": src.sha256_file(out / "predictions" / "manifest.json"),
               "evaluator_sha256": src.sha256_file(Path(__file__)),
               "labels": {"path": LABELS, "sha256": labels_sha, "human_verified": False,
                          "status": "Luna machine-labelled reused development diagnostic; no independent holdout",
                          "counts": dict(Counter(g["decision"] for g in labels.values()))},
               "baseline": {"path": BASELINE, "sha256": src.sha256_file(ROOT / BASELINE)},
               "cohort": {"path": COHORT, "sha256": src.sha256_file(ROOT / COHORT), "cases": len(cohort),
                          "curtain": len(cohort & curtain)},
               "definitions": {
                   "confirmed_true_accept": "pred matched, gold matched, top_row_key in gold matching_au_row_keys",
                   "conservative_accepted_precision": "confirmed true accepts / all predicted accepts (gold review accepts count as unconfirmed)",
                   "known_only_accepted_precision": "auxiliary: confirmed true accepts / accepts whose gold is not review",
                   "false_delete": "pred unmatched while gold matched",
                   "product_macro": "unweighted mean over the 29 fixed pairs where the metric is defined"},
               "results": results, "gold_review_audit": review_audit}
    runner.write_new(eval_dir / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    runner.write_jsonl_new(eval_dir / "disagreements.jsonl", disagreements)
    for system in ("baseline_v10_hybrid", "A-full", "B-full"):
        r = results[system]["all"]["row"]
        print(system, {k: r[k] for k in ("pred", "confirmed_true_accepts", "false_accepts_gold_unmatched",
                                         "unsafe_accepts_gold_review", "wrong_row_accepts", "false_deletes_gold_matched",
                                         "conservative_accepted_precision", "matched_recall_row_correct", "review_rate")})


if __name__ == "__main__":
    main()
