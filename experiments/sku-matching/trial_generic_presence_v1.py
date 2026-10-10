"""Compare generic NLI formulations for complementary accessory claims.

The existing quote questions supply natural-language claims, not component IDs.
This is a relation-task diagnostic, not a new SKU accuracy evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from trial_generic_model_nli_v1 import run_pairs

ROOT = Path(__file__).resolve().parents[2]
THRESHOLD = 0.90
TEMPLATES = {
    "attached": ("この商品には{noun}が付いている。", "この商品には{noun}が付いていない。"),
    "included": ("この商品には{noun}が含まれる。", "この商品には{noun}が含まれない。"),
    "slot": ("この商品の{noun}はありです。", "この商品の{noun}はなしです。"),
    "exists": ("この商品には{noun}がある。", "この商品には{noun}がない。"),
    "with_without": ("この商品は{noun}付きだ。", "この商品は{noun}なしだ。"),
    "accessory": ("この商品に{noun}は付属する。", "この商品に{noun}は付属しない。"),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def rows(path, values):
    Path(path).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in values))


def complementary_relation(positive, negative, threshold=THRESHOLD):
    """The same aggregation applies to every component; no product rules."""
    present = min(positive["support"], negative["conflict"])
    absent = min(positive["conflict"], negative["support"])
    if present >= threshold and absent < threshold:
        return "present"
    if absent >= threshold and present < threshold:
        return "absent"
    return "unknown"


def prepare(tasks, cases, products):
    pairs = []
    by_case = {c["case_id"]: c for c in cases}
    by_product = {p["dossier_id"]: p for p in products}
    for task in tasks:
        # This decodes the prior experiment's generated sentence contract only.
        # It does not extract components from product data or maintain an ontology.
        match = re.fullmatch(r"この商品には(.+?)が付いて(?:いる|いない)。", task["hypothesis"])
        if not match:
            raise ValueError("caller must supply complementary natural-language claims")
        noun = match[1]
        evidence = task["evidence"]
        if evidence["quote"] != evidence["span"]["quote"]:
            raise ValueError("literal quote binding differs")
        case = by_case[task["case_id"]]
        product = by_product[case["dossier_id"]]
        candidates = {r["row_key"]: r for r in product["au"]["rows"]}
        candidate = candidates[task["au_row_key"]]
        selections = " / ".join(f'{a["axis_name"]}：{a["value"]}' for a in candidate["axes"])
        contexts = {
            "quote": evidence["quote"],
            "quote_sentence": f'この商品は「{evidence["quote"]}」です。',
            "selected_row": f'商品名：{product["au"]["title"]}\n選択仕様：{selections}\n記載：{evidence["quote"]}',
        }
        for context, premise in contexts.items():
            for template, claims in TEMPLATES.items():
                for polarity, claim in zip(("positive", "negative"), claims):
                    pairs.append({
                        "id": f'{task["task_id"]}:{context}:{template}:{polarity}',
                        "task_id": task["task_id"], "context": context, "template": template,
                        "polarity": polarity, "premise": premise, "hypothesis": claim.format(noun=noun),
                        "quote": evidence, "source_scope": task["source_scope"],
                        "case_id": task["case_id"], "au_row_key": task["au_row_key"],
                        "selected_row": candidate,
                        "applicability_status": "not_independently_verified_by_this_trial",
                    })
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / ".lab-output/sku-cpu-requirement-tasks-20261010-v3/tasks.jsonl")
    parser.add_argument("--inputs", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    pairs = prepare(read(args.tasks), read(args.inputs / "cases.jsonl"), read(args.inputs / "products.jsonl"))
    args.output.mkdir(parents=True)
    rows(args.output / "pairs.jsonl", pairs)
    frozen = {"source_question_sha256": sha(args.tasks),
              "inputs_sha256": {n: sha(args.inputs / n) for n in ("cases.jsonl", "products.jsonl", "manifest.json")},
              "code_sha256": {n: sha(Path(__file__).with_name(n)) for n in (Path(__file__).name, "trial_generic_model_nli_v1.py")},
              "pairs_sha256": sha(args.output / "pairs.jsonl"), "templates": TEMPLATES,
              "threshold": THRESHOLD, "labels_read": False,
              "synthetic_product_records": False,
              "contrastive_hypotheses": True,
              "evaluation_kind": "reused_real_quote_diagnostic_not_SKU_accuracy",
              "case_ids": sorted({p["case_id"] for p in pairs}),
              "pairs": len(pairs), "unique_model_inputs": len({(p["premise"], p["hypothesis"]) for p in pairs}),
              "scope_model": "Claude responsibility; no row adoption in this experiment"}
    save(args.output / "freeze.json", frozen)
    if not args.run:
        print(json.dumps({k: frozen[k] for k in ("pairs", "unique_model_inputs", "evaluation_kind")}))
        return
    result = run_pairs(pairs, threads=args.threads, batch_size=args.batch_size)
    rows(args.output / "model-records.jsonl", result["records"])
    save(args.output / "model-pin.json", {"pins": result["pins"], "runtime": result["runtime"]})
    records = {r["id"]: r for r in result["records"]}
    proposals = []
    for pair in pairs:
        if pair["polarity"] != "positive":
            continue
        negative_id = pair["id"].rsplit(":", 1)[0] + ":negative"
        pos, neg = records[pair["id"]], records[negative_id]
        positive = pos.get("probabilities") or {k: 0.0 for k in ("support", "conflict", "unknown")}
        negative = neg.get("probabilities") or {k: 0.0 for k in ("support", "conflict", "unknown")}
        proposals.append({"task_id": pair["task_id"], "context": pair["context"],
                          "template": pair["template"], "relation": complementary_relation(positive, negative),
                          "positive_probabilities": positive, "negative_probabilities": negative,
                          "au_row_key": pair["au_row_key"],
                          "applicability_status": pair["applicability_status"]})
    rows(args.output / "proposals.jsonl", proposals)
    counts = {}
    for context in ("quote", "quote_sentence", "selected_row"):
        for template in TEMPLATES:
            counts[f"{context}:{template}"] = dict(Counter(p["relation"] for p in proposals if p["context"] == context and p["template"] == template))
    summary = {"freeze_sha256": sha(args.output / "freeze.json"), "counts": counts,
               "labels_read": False, "scope_verified": False, "SKU_decisions_generated": False}
    save(args.output / "summary.json", summary)
    save(args.output / "manifest.json", {"files": {p.name: sha(p) for p in args.output.iterdir() if p.is_file()}})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
