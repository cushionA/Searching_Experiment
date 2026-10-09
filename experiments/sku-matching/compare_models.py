"""Fixed diagnostic CPU comparison; no threshold fitting or held-out claims."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import platform
import resource
import time

from match_skus import canonical_key, conflicts, model_text, normalize_sku

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODELS = {
    "minilm": ("embeddings", "EmbeddingModel", "sku-matching-model"),
    "bekko": ("backend_bekko", "Model", "sku-bekko-model"),
    "granite": ("backend_granite", "Model", "sku-granite-model"),
    "ruri": ("backend_ruri", "Model", "sku-ruri-model"),
    "reranker": ("backend_reranker", "Model", "sku-reranker-model"),
}
DECISIONS = ("matched", "unmatched", "review")


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def stats(values):
    import numpy as np
    if not len(values):
        return {"count": 0}
    return {"count": len(values), "min": float(np.min(values)),
            "p50": float(np.median(values)), "p95": float(np.percentile(values, 95)),
            "max": float(np.max(values))}


def provided_attribute_guard(a, b):
    """Reference guard uses supplied attributes, not model-extracted evidence."""
    a, b = normalize_sku(a), normalize_sku(b)
    different = conflicts(a, b)
    if different:
        return "unmatched", different
    missing = sorted(set(a["missing"] + b["missing"]))
    issues = sorted(set(a["issues"] + b["issues"]))
    if missing or issues:
        return "review", missing + issues
    return "matched", []


def classification_metrics(expected, predicted):
    if len(expected) != len(predicted):
        raise ValueError("Expected and predicted lengths differ")
    matrix = {e: {p: 0 for p in DECISIONS} for e in DECISIONS}
    for e, p in zip(expected, predicted, strict=True):
        matrix[e][p] += 1
    tp = matrix["matched"]["matched"]
    fp = matrix["unmatched"]["matched"]
    unknown_accepted = matrix["review"]["matched"]
    positives = sum(matrix["matched"].values())
    negatives = sum(matrix["unmatched"].values())
    unknown = sum(matrix["review"].values())
    accepted = tp + fp + unknown_accepted
    reviews = sum(row["review"] for row in matrix.values())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / positives if positives else None
    return {"confusion_matrix": matrix, "known_case_precision": precision,
            "known_case_recall": recall,
            "known_case_f1": 2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall else None,
            "verified_different_fpr": fp / negatives if negatives else None,
            "review_false_accept_rate": unknown_accepted / unknown if unknown else None,
            "accepted_count": accepted, "accepted_review_cases": unknown_accepted,
            "confirmed_same_fraction_of_all_accepted": tp / accepted if accepted else None,
            "review_rate": reviews / len(expected) if expected else None,
            "decision_coverage": (len(expected) - reviews) / len(expected) if expected else None}


def ranking_metrics(ranks, present_queries, absent_queries):
    """A retrieval miss is None and stays in the present-query denominator."""
    if len(ranks) != present_queries:
        raise ValueError("Rank count differs from present queries")
    return {"present_queries": present_queries, "absent_queries": absent_queries,
            "recall": {f"at_{k}": sum(r is not None and r <= k for r in ranks) / present_queries
                       if present_queries else None for k in (1, 5, 10)},
            "mrr": sum(1 / r for r in ranks if r is not None) / present_queries
            if present_queries else None}


def encode_distinct(model, texts, batch_size):
    """Every backend receives exactly the same unique strings, once per call."""
    import numpy as np
    unique = list(dict.fromkeys(texts))
    vectors = model.encode(unique, batch_size=batch_size)
    lookup = {text: i for i, text in enumerate(unique)}
    return vectors[np.asarray([lookup[t] for t in texts])]


def json_write(path, value):
    with path.open("x", encoding="utf-8") as target:
        target.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def jsonl_write(path, values):
    with path.open("x", encoding="utf-8") as target:
        for value in values:
            target.write(json.dumps(value, ensure_ascii=False) + "\n")


def evaluate(model, cases, mode, batch, reranker, output):
    import numpy as np
    texts = [model_text(c[s], mode) for c in cases for s in ("au", "rakuten")]
    start = time.perf_counter()
    if reranker:
        pairs = list(zip(texts[1::2], texts[0::2]))  # Rakuten query, au candidate
        scores = model.score_pairs(pairs, batch_size=batch)
        lengths = model.token_lengths_pairs(pairs)
    else:
        vectors = encode_distinct(model, texts, batch)
        scores = np.sum(vectors[0::2] * vectors[1::2], axis=1)
        lengths = model.token_lengths(texts)
    elapsed = time.perf_counter() - start
    expected = [c["expected_decision"] for c in cases]
    guards = [provided_attribute_guard(c["au"], c["rakuten"]) for c in cases]
    rows = [{"case_id": c["case_id"], "scenario": c["scenario"],
             "expected_decision": expected[i], "score": float(scores[i]),
             "provided_attribute_guard": guards[i][0], "guard_reasons": guards[i][1],
             "au_input": texts[2*i], "rakuten_input": texts[2*i+1]}
            for i, c in enumerate(cases)]
    jsonl_write(output / f"controls-{mode}.jsonl", rows)
    thresholds = (-5, -2, 0, 2, 5) if reranker else (0.7, 0.8, 0.85, 0.9, 0.95, 0.99)
    grid = []
    for threshold in thresholds:
        raw = ["matched" if s >= threshold else "unmatched" for s in scores]
        hybrid = [g if g != "matched" else p for (g, _), p in zip(guards, raw, strict=True)]
        grid.append({"fixed_threshold_for_inspection_only": threshold,
                     "model_only": classification_metrics(expected, raw),
                     "model_plus_provided_attribute_guard": classification_metrics(expected, hybrid)})
    max_length = model.MAX_LENGTH
    return {"mode": mode, "cases": len(cases), "target_counts": dict(Counter(expected)),
            "input_texts": len(texts), "distinct_texts": len(set(texts)),
            "seconds_including_token_length_audit": elapsed,
            "token_lengths": stats(lengths),
            "over_token_limit_count": int(np.sum(lengths > max_length)),
            "positive_scores": stats(scores[np.asarray(expected) == "matched"]),
            "negative_scores": stats(scores[np.asarray(expected) == "unmatched"]),
            "review_scores": stats(scores[np.asarray(expected) == "review"]),
            "by_scenario": {s: stats([float(scores[i]) for i,c in enumerate(cases) if c["scenario"] == s])
                            for s in sorted({c["scenario"] for c in cases})},
            "provided_attribute_guard_only": classification_metrics(expected, [g for g,_ in guards]),
            "threshold_grid": grid}


def rank_real(model, pair, path, name, mode, batch, reranker, retrieval_dir, output):
    import numpy as np
    au, rakuten = pair["au"], pair["rakuten"]
    au_norm = [normalize_sku(s) for s in au]
    r_norm = [normalize_sku(s) for s in rakuten]
    index = {}
    for i, s in enumerate(au_norm):
        if not s["missing"] and not s["issues"]:
            index.setdefault(canonical_key(s), []).append(i)
    answers = []
    for s in r_norm:
        hits = index.get(canonical_key(s), []) if not s["missing"] and not s["issues"] else []
        if len(hits) > 1:
            raise ValueError("Ambiguous attribute reference")
        answers.append(hits[0] if hits else None)
    texts_a, texts_r = [model_text(s, mode) for s in au], [model_text(s, mode) for s in rakuten]
    started = time.perf_counter()
    retrieval_sha = None
    if reranker:
        source_path = retrieval_dir / f"ranking-{name}-{mode}.json"
        baseline = json.loads(source_path.read_text())
        retrieval_sha = sha(source_path)
        if baseline["input_sha256"] != sha(path) or len(baseline["rows"]) != len(rakuten):
            raise ValueError("Retrieval candidates do not match the fixed dataset")
        candidate_sets = []
        for q, row in enumerate(baseline["rows"]):
            if row["query_sku_id"] != rakuten[q]["sku_id"] or row["answer_index"] != answers[q]:
                raise ValueError("Retrieval query/reference differs")
            candidate_sets.append([c["index"] for c in row["candidates"]])
        pairs = [(texts_r[q], texts_a[a]) for q, candidates in enumerate(candidate_sets) for a in candidates]
        flat = model.score_pairs(pairs, batch_size=batch)
        score_rows, offset = [], 0
        for candidates in candidate_sets:
            score_rows.append(flat[offset:offset + len(candidates)])
            offset += len(candidates)
    else:
        vectors = encode_distinct(model, texts_a + texts_r, batch)
        similarities = vectors[len(au):] @ vectors[:len(au)].T
        candidate_sets = [list(range(len(au))) for _ in rakuten]
        score_rows = similarities
    rows, ranks, absent_scores = [], [], []
    for q, (candidates, scores) in enumerate(zip(candidate_sets, score_rows, strict=True)):
        order = np.argsort(-scores, kind="stable")
        ordered = [candidates[int(i)] for i in order]
        answer = answers[q]
        rank = ordered.index(answer) + 1 if answer in ordered else None
        if answer is not None:
            ranks.append(rank)
        else:
            absent_scores.append(float(scores[int(order[0])]))
        rows.append({"query_index": q, "query_sku_id": rakuten[q]["sku_id"],
                     "answer_index": answer, "answer_rank": rank,
                     "candidates": [{"index": candidates[int(i)], "product_id": au[candidates[int(i)]]["product_id"],
                                     "sku_id": au[candidates[int(i)]]["sku_id"], "score": float(scores[int(i)])}
                                    for i in order[:10]]})
    elapsed = time.perf_counter() - started
    json_write(output / f"ranking-{name}-{mode}.json",
               {"dataset": name, "mode": mode, "input_sha256": sha(path), "rows": rows})
    return {"dataset": name, "mode": mode, "au_skus": len(au), "rakuten_queries": len(rakuten),
            "reference_basis": "provided canonical attribute correspondence within supplied family; stock and price ignored",
            "full_pair_count": len(au)*len(rakuten),
            "scored_pair_count": sum(len(s) for s in candidate_sets),
            "retrieval_candidate_source_sha256": retrieval_sha,
            **ranking_metrics(ranks, len(ranks), len(absent_scores)),
            "absent_query_top_score": stats(absent_scores), "seconds": elapsed}


def latency_probe(model, texts, batch, reranker, candidate_texts=None):
    # Warmup has disjoint inputs. Timed probes remain unique even with a cache.
    values = [f"{texts[i % len(texts)]} [CPU計測{i:03d}]" for i in range(64)]
    warm = ["ウォームアップ カーテン 幅100×丈80cm / ベージュ"]
    if reranker:
        model.score_pairs([(warm[0], warm[0])], batch_size=1)
    else:
        model.encode(warm, batch_size=1)
    times = []
    for i in range(0, len(values), batch):
        chunk = values[i:i+batch]
        started = time.perf_counter()
        if reranker:
            model.score_pairs([(t, candidate_texts[(i+j) % len(candidate_texts)])
                               for j,t in enumerate(chunk)], batch_size=batch)
        else:
            model.encode(chunk, batch_size=batch)
        times.append(time.perf_counter() - started)
    return {"fresh_items": len(values), "calls": len(times), "call_latency_seconds": stats(times),
            "seconds_per_item": sum(times)/len(values), "items_per_second": len(values)/sum(times),
            "warmup_items_excluded": 1, "tokenizer_included": True,
            "probe_basis": "real title+SKU with unique synthetic timing suffix; not accuracy data"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--batch-size", type=int, choices=(1, 8), default=8)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--evaluation", type=Path, default=ROOT/".lab-output/sku-synthetic-controls-20261009/evaluation.json")
    parser.add_argument("--retrieval-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("threads must be positive")
    reranker = args.model == "reranker"
    if reranker and not args.retrieval_dir:
        parser.error("reranker requires fixed baseline --retrieval-dir")
    args.output.mkdir(parents=True, exist_ok=False)
    module, cls, model_dir = MODELS[args.model]
    manifest_path = HERE/"model-manifest.json" if args.model == "minilm" else HERE/"manifests"/f"{args.model}.json"
    cases = json.loads(args.evaluation.read_text())["cases"]
    pairs = [(name, HERE/"results"/f"real-{name}-pair.jsonl") for name in ("original", "sibling")]
    source_hashes = {"evaluation": sha(args.evaluation), **{name: sha(p) for name,p in pairs}}
    started = time.perf_counter()
    model = getattr(importlib.import_module(module), cls)(args.model_dir or ROOT/".deps"/model_dir, threads=args.threads)
    load_seconds = time.perf_counter() - started
    print(f"{args.model} batch={args.batch_size} loaded in {load_seconds:.2f}s", flush=True)
    first_pair = json.loads(pairs[0][1].read_text())
    probes = [model_text(s) for s in (first_pair["rakuten"] if reranker
                                    else first_pair["au"] + first_pair["rakuten"])]
    probe_candidates = [model_text(s) for s in first_pair["au"]] if reranker else None
    result = {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
              "model_key": args.model, "manifest": json.loads(manifest_path.read_text()),
              "score_kind": "raw_logit" if reranker else "cosine",
              "platform": platform.platform(), "python": platform.python_version(),
              "threads": args.threads, "batch_size": args.batch_size,
              "cold_load_seconds_including_import_and_hash_check": load_seconds,
              "source_sha256": source_hashes,
              "limitations": ["1000 synthetic controls derive from one observed family; no independent held-out accuracy",
                              "real ranking reference is attribute correspondence, not independently verified physical identity",
                              "provided-attribute guard is a reference control, not extraction-model performance",
                              "threshold grid is fixed for diagnosis; no production threshold selected",
                              "embedding inputs explicitly deduplicated; caches and reused data are not fresh inference"],
              "latency_probe": latency_probe(model, probes, args.batch_size, reranker, probe_candidates)}
    result["runtime_versions"] = {}
    for package in ("numpy", "onnxruntime", "tokenizers", "torch", "transformers"):
        try:
            result["runtime_versions"][package] = version(package)
        except PackageNotFoundError:
            pass
    result["controls"], result["rankings"] = [], []
    for mode in ("sku", "title-sku"):
        result["controls"].append(evaluate(model, cases, mode, args.batch_size, reranker, args.output))
        for name,path in pairs:
            result["rankings"].append(rank_real(model, json.loads(path.read_text()), path, name,
                                               mode, args.batch_size, reranker, args.retrieval_dir, args.output))
        print(f"{args.model} {mode} done", flush=True)
    result["process_peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    json_write(args.output/"summary.json", result)
    print(json.dumps({"output": str(args.output), "rss_mib": result["process_peak_rss_mib"],
                      "latency": result["latency_probe"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
