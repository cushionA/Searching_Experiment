"""Compare SKU text vs title+SKU on labeled synthetic controls and CPU load."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import resource
import time

from match_skus import match_pair, model_text


def file_sha(path):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256")
    return digest.hexdigest()


def distributions(np, values):
    if not len(values):
        return {"count": 0}
    return {"count": len(values), "min": float(np.min(values)),
            "p50": float(np.median(values)), "p95": float(np.percentile(values, 95)),
            "max": float(np.max(values))}


def evaluate(model, cases, mode, batch_size):
    import numpy as np
    texts = [model_text(case[site], mode) for case in cases for site in ("au", "rakuten")]
    start = time.perf_counter()
    vectors = model.encode(texts, batch_size=batch_size)
    elapsed = time.perf_counter() - start
    lengths = model.token_lengths(texts)
    scores = np.sum(vectors[0::2] * vectors[1::2], axis=1)
    labels = np.array([case["expected_match"] for case in cases], dtype=bool)
    known = np.array([case.get("expected_decision") != "review" and
                      case["scenario"] != "negative_missing_lace_evidence" for case in cases])
    thresholds = []
    for threshold in (0.7, 0.8, 0.85, 0.9, 0.95):
        predicted = scores >= threshold
        tp = int(np.sum(predicted & labels & known))
        fp = int(np.sum(predicted & ~labels & known))
        fn = int(np.sum(~predicted & labels & known))
        tn = int(np.sum(~predicted & ~labels & known))
        thresholds.append({"threshold": threshold, "true_positive": tp, "false_positive": fp,
                           "false_negative": fn, "true_negative": tn,
                           "precision": tp / (tp + fp) if tp + fp else None,
                           "recall": tp / (tp + fn) if tp + fn else None})
    by_scenario = {}
    for scenario in sorted({case["scenario"] for case in cases}):
        mask = np.array([case["scenario"] == scenario for case in cases])
        by_scenario[scenario] = distributions(np, scores[mask])
    high_negatives = np.where(~labels & known)[0]
    high_negatives = sorted(high_negatives, key=lambda i: -scores[i])[:8]
    return {"mode": mode, "cases": len(cases), "records": len(texts),
            "evaluable_known_identity_cases": int(np.sum(known)),
            "incomplete_evidence_cases_excluded_from_accuracy": int(np.sum(~known)),
            "distinct_input_texts": len(set(texts)), "encode_seconds": elapsed,
            "token_length": distributions(np, lengths),
            "right_truncated_input_count": int(sum(length > model.MAX_LENGTH for length in lengths)),
            "positive_similarity": distributions(np, scores[labels & known]),
            "negative_similarity": distributions(np, scores[~labels & known]),
            "incomplete_evidence_similarity": distributions(np, scores[~known]),
            "by_scenario": by_scenario, "thresholds_for_inspection_only": thresholds,
            "highest_scoring_negatives": [{"case_id": cases[i]["case_id"], "scenario": cases[i]["scenario"],
                "similarity": float(scores[i]), "au_input": texts[2*i], "rakuten_input": texts[2*i+1]}
                for i in high_negatives]}


def ranking(model, pair, mode, batch_size):
    """One product's 306 Rakuten rows against 153 au rows, raw embedding only."""
    import numpy as np
    au, rakuten = pair["au"], pair["rakuten"]
    vectors = model.encode([model_text(s, mode) for s in au + rakuten], batch_size=batch_size)
    similarities = vectors[len(au):] @ vectors[:len(au)].T
    rows, decisions = match_pair(pair)
    expected = {(row["rakuten_product_id"], row["rakuten_sku_id"]):
                (row["au_product_id"], row["au_sku_id"]) for row in rows}
    top = np.argmax(similarities, axis=1)
    correct = 0
    positive = 0
    absent_scores = []
    for i, r in enumerate(rakuten):
        answer = expected.get((r["product_id"], r["sku_id"]))
        if answer:
            positive += 1
            candidate = au[int(top[i])]
            correct += (candidate["product_id"], candidate["sku_id"]) == answer
        else:
            absent_scores.append(float(similarities[i, top[i]]))
    return {"mode": mode, "au_skus": len(au), "rakuten_skus": len(rakuten),
            "reference": "canonical_attributes_of_synthetic_fixture",
            "positive_queries": positive, "top1_correct": int(correct),
            "top1_accuracy_for_present_skus": float(correct / positive) if positive else None,
            "absent_sku_top1_similarity": distributions(np, absent_scores)}


def workload(dataset_path, model, batch_size):
    counts = Counter()
    start = time.perf_counter()
    with dataset_path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            pair = json.loads(line)
            rows, decisions = match_pair(pair, model=model, batch_size=batch_size)
            counts["product_pairs"] += 1
            counts["input_records"] += len(pair["au"]) + len(pair["rakuten"])
            counts["matched_csv_rows"] += len(rows)
            counts.update(f"{r['site']}_{r['status']}" for r in decisions)
    elapsed = time.perf_counter() - start
    return {"counts": dict(counts), "seconds": elapsed,
            "input_records_per_second": counts["input_records"] / elapsed,
            "model_enabled": model is not None,
            "includes": "JSONL read, attribute normalization, indexing, match decisions; excludes CSV write"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--fresh-texts", type=int, default=2048)
    args = parser.parse_args()
    if args.batch_size < 1 or args.threads < 1 or args.fresh_texts < 1:
        parser.error("batch-size, threads and fresh-texts must be positive")
    if args.output.exists():
        parser.error("output already exists")
    from embeddings import EmbeddingModel
    cases = json.loads(args.evaluation.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases["cases"]
    with args.dataset.open(encoding="utf-8") as source:
        pair = json.loads(next(line for line in source if line.strip()))
    result = {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "python": platform.python_version(),
              "threads": args.threads, "batch_size": args.batch_size,
              "dataset_sha256": file_sha(args.dataset), "evaluation_sha256": file_sha(args.evaluation),
              "data_limitations": "Synthetic Rakuten labels, prices and availability. Not production accuracy. No live scraping/cart action in benchmarks.",
              "model": json.loads((Path(__file__).parent / "model-manifest.json").read_text())}
    started = time.perf_counter()
    model = EmbeddingModel(args.model_dir, threads=args.threads)
    result["model_load_seconds"] = time.perf_counter() - started
    print("model loaded", flush=True)
    result["evaluations"] = []
    result["rankings"] = []
    for mode in ("sku", "title-sku"):
        result["evaluations"].append(evaluate(model, cases, mode, args.batch_size))
        result["rankings"].append(ranking(model, pair, mode, args.batch_size))
        print(f"{mode} evaluation complete", flush=True)
    # Fresh text stress intentionally changes synthetic dimensions. It measures
    # uncached inference, not model accuracy or realistic SKU distribution.
    texts = [f"カーテン レースセット / 幅100×丈{100+i}cm(4枚組) / ベージュ"
             for i in range(args.fresh_texts)]
    start = time.perf_counter()
    model.encode(texts, batch_size=args.batch_size)
    elapsed = time.perf_counter() - start
    result["fresh_inference"] = {"text_count": len(texts), "seconds": elapsed,
                                 "texts_per_second": len(texts) / elapsed, "data": "synthetic dimensions"}
    print("fresh inference complete", flush=True)
    result["workloads"] = [workload(args.dataset, None, args.batch_size)]
    print("rules workload complete", flush=True)
    result["workloads"].append(workload(args.dataset, model, args.batch_size))
    result["process_peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    # Serialize before opening the result, so an unsupported scalar cannot leave
    # a partial artifact that looks like a finished benchmark.
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        target.write(encoded + "\n")
    print(json.dumps({"output": str(args.output), "workloads": result["workloads"],
                      "fresh_inference": result["fresh_inference"], "peak_rss_mib": result["process_peak_rss_mib"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
