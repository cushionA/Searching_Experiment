"""Rerank Bekko's frozen top ten using short structured SKU attributes.

Inference is deliberately separated from label evaluation: all raw rank
predictions are written before labels are opened and joined for metrics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MODEL = ROOT / ".deps/sku-reranker-model"
DEFAULT_EVALUATOR = "evaluate_structured_skus"
BATCH_SIZE = 8
THREADS = 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            rows.append(value)
    return rows


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("rows") if isinstance(value, dict) else value
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{path}: expected a JSON array or object with a rows array")
    return rows


def top10_entries(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Read canonical top-ten row key/score variants without re-ranking them."""
    entries = row.get("top10")
    if entries is None:
        entries = row.get("top10row_key/score")
    if entries is None:
        keys = row.get("top10row_key") or row.get("top10_row_keys") or []
        scores = row.get("top10score") or row.get("top10_scores") or []
        if isinstance(keys, list):
            entries = [
                {"row_key": key, "score": scores[index] if index < len(scores) else None}
                for index, key in enumerate(keys)
            ]
    if not isinstance(entries, list):
        raise ValueError(f"Prediction row {row.get('case_id')!r} has no top-ten list")
    result = []
    seen = set()
    for item in entries:
        if isinstance(item, dict):
            key = item.get("row_key") or item.get("top_row_key")
            score = item.get("score")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key, score = item
        else:
            raise ValueError(f"Malformed top-ten entry in case {row.get('case_id')!r}: {item!r}")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({"row_key": str(key), "bekko_score": score})
    if len(result) > 10:
        raise ValueError(f"Prediction row {row.get('case_id')!r} contains more than ten unique candidates")
    return result


def _canonical_task_entity(task: dict[str, Any]) -> dict[str, Any]:
    """Use the selected Rakuten SKU only; candidate page context is inherited in AU attrs."""
    return task["rakuten"]


def get_evaluator(module_name: str):
    module = importlib.import_module(module_name)
    feature_text = getattr(module, "feature_text", None)
    gate = getattr(module, "gate", None)
    if not callable(feature_text) or not callable(gate):
        raise TypeError(f"{module_name} must provide callable feature_text(entity, mode='canonical') and gate(task,candidate)")
    return feature_text, gate


def _gate_result(value: Any) -> tuple[str, list[str], dict[str, Any]]:
    if isinstance(value, tuple) and len(value) == 2:
        decision, reasons = value
        details = {}
    elif isinstance(value, dict):
        decision = value.get("decision") or value.get("status")
        details = {key: value.get(key) for key in (
            "conflicting_fields", "missing_fields", "unresolved_spelling_fields", "unresolved_fields")
                   if value.get(key)}
        reasons = [f"{key}:{','.join(map(str, values))}" for key, values in details.items()]
    else:
        raise TypeError("gate(task,candidate) must return (decision,reasons) or a decision dict")
    if isinstance(reasons, str):
        reasons = [reasons]
    decision = str(decision)
    if decision not in {"matched", "unmatched", "review"}:
        raise ValueError(f"gate returned unsupported decision {decision!r}")
    return decision, [str(reason) for reason in reasons], details


def build_pairs(
    tasks: list[dict[str, Any]], ranking_rows: list[dict[str, Any]], feature_text: Callable[..., str]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], dict[tuple[str, str], int]]:
    """Return task plans, deduplicated text pairs and an address-to-pair index."""
    tasks_by_id = {row["case_id"]: row for row in tasks}
    rankings_by_id = {row["case_id"]: row for row in ranking_rows}
    if len(tasks_by_id) != len(tasks) or len(rankings_by_id) != len(ranking_rows):
        raise ValueError("Duplicate case_id in task or Bekko ranking inputs")
    if tasks_by_id.keys() != rankings_by_id.keys():
        missing = sorted(tasks_by_id.keys() - rankings_by_id.keys())[:5]
        extra = sorted(rankings_by_id.keys() - tasks_by_id.keys())[:5]
        raise ValueError(f"Task/ranking case_id mismatch; missing rankings={missing}, extra={extra}")

    unique_pairs: list[tuple[str, str]] = []
    pair_index: dict[tuple[str, str], int] = {}
    plans = []
    for task in tasks:
        case_id = task["case_id"]
        query_entity = _canonical_task_entity(task)
        query_text = feature_text(query_entity, mode="canonical")
        if not isinstance(query_text, str):
            raise TypeError("feature_text must return a string")
        candidates_by_key = {item.get("row_key"): item for item in task.get("au_candidates", [])}
        selected = []
        for entry in top10_entries(rankings_by_id[case_id]):
            candidate = candidates_by_key.get(entry["row_key"])
            if candidate is None:
                raise ValueError(f"Bekko candidate {entry['row_key']!r} is absent in task {case_id}")
            candidate_text = feature_text(candidate, mode="canonical")
            if not isinstance(candidate_text, str):
                raise TypeError("feature_text must return a string")
            pair = (query_text, candidate_text)
            if pair not in pair_index:
                pair_index[pair] = len(unique_pairs)
                unique_pairs.append(pair)
            selected.append({**entry, "candidate": candidate,
                             "pair_index": pair_index[pair],
                             "input_sha256": hashlib.sha256(
                                 (query_text + "\0" + candidate_text).encode("utf-8")).hexdigest()})
        plans.append({"task": task, "bekko_top10": selected})
    return plans, unique_pairs, pair_index


def raw_rank_predictions(
    model: Any, tasks: list[dict[str, Any]], ranking_rows: list[dict[str, Any]],
    feature_text: Callable[..., str], gate: Callable[..., Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plans, pairs, _ = build_pairs(tasks, ranking_rows, feature_text)
    lengths = [int(value) for value in model.token_lengths_pairs(pairs)] if pairs else []
    scores = [float(value) for value in model.score_pairs(pairs, batch_size=BATCH_SIZE)] if pairs else []
    if len(scores) != len(pairs) or len(lengths) != len(pairs):
        raise RuntimeError("Reranker returned a score/token-length count different from unique inputs")
    raw = []
    for plan in plans:
        task = plan["task"]
        ranked = []
        for candidate in plan["bekko_top10"]:
            gate_decision, gate_reasons, gate_details = _gate_result(gate(task, candidate["candidate"]))
            ranked.append({
                "row_key": candidate["row_key"],
                "bekko_score": candidate["bekko_score"],
                "reranker_score": scores[candidate["pair_index"]],
                "reranker_score_kind": "raw_relevance_logit",
                "input_sha256": candidate["input_sha256"],
                "token_count_untruncated": int(lengths[candidate["pair_index"]]),
                "was_truncated": int(lengths[candidate["pair_index"]]) > int(model.max_length),
                "gate_decision": gate_decision,
                "gate_reasons": gate_reasons,
                "gate_details": gate_details,
            })
        ranked.sort(key=lambda item: (-item["reranker_score"],
                                      -(item["bekko_score"] if isinstance(item["bekko_score"], (int, float)) else float("-inf")),
                                      item["row_key"]))
        top = ranked[0] if ranked else None
        raw.append({"case_id": task["case_id"], "split": task.get("split"),
                    "group_id": task.get("group_id"), "dossier_id": task.get("dossier_id"),
                    "top_row_key": top["row_key"] if top else None,
                    "top10": ranked, "strict_gate_decision": top["gate_decision"] if top else "review",
                    "strict_gate_reasons": top["gate_reasons"] if top else ["no_candidate_in_bekko_top10"],
                    "score_kind": "raw_relevance_logit_not_identity_probability"})
    return raw, {"unique_pair_count": len(pairs), "input_pair_count": sum(len(p["bekko_top10"]) for p in plans),
                 "token_count_untruncated": lengths, "truncated_pair_count": sum(x > int(model.max_length) for x in lengths),
                 "max_length": int(model.max_length)}


def label_metrics(raw: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Join labels only after raw output has been serialized."""
    labels_by_id = {row["case_id"]: row for row in labels}
    raw_by_id = {row["case_id"]: row for row in raw}
    if len(labels_by_id) != len(labels) or labels_by_id.keys() != raw_by_id.keys():
        raise ValueError("Label case_id set must exactly match raw reranker predictions")
    retrieval_denominator = 0
    bekko_recall = 0
    rerank_recall = 0
    strict_tp = strict_fp = strict_fn = strict_tn = 0
    split_metrics: dict[str, dict[str, int]] = {}
    for case_id, label in labels_by_id.items():
        decision = label.get("decision")
        gold = set(label.get("matching_au_row_keys") or [])
        row = raw_by_id[case_id]
        top10 = row["top10"]
        top10_keys = [item["row_key"] for item in top10]
        top_key = row.get("top_row_key")
        if decision == "matched":
            retrieval_denominator += 1
            bekko_recall += int(bool(gold.intersection(top10_keys)))
            rerank_recall += int(top_key in gold)
        pred = row["strict_gate_decision"]
        # The strict identity success includes selecting the correct AU SKU.
        actual_match = decision == "matched"
        predicted_match = pred == "matched" and top_key in gold
        if actual_match and predicted_match:
            strict_tp += 1
        elif pred == "matched":
            # A matched gate on the wrong AU SKU is a false acceptance even
            # when the Rakuten case itself is labeled matched.
            strict_fp += 1
            if actual_match:
                strict_fn += 1
        elif actual_match:
            strict_fn += 1
        else:
            strict_tn += 1
        split = str(row.get("split") or "unknown")
        bucket = split_metrics.setdefault(split, {"cases": 0, "matched_gold": 0,
                                                   "bekko_recall_at_10_hits": 0,
                                                   "reranker_top1_hits": 0})
        bucket["cases"] += 1
        if decision == "matched":
            bucket["matched_gold"] += 1
            bucket["bekko_recall_at_10_hits"] += int(bool(gold.intersection(top10_keys)))
            bucket["reranker_top1_hits"] += int(top_key in gold)
    return {
        "label_source": "old Luna labels; machine-annotated and human-unverified",
        "test_is_reused_diagnostic_only": True,
        "candidate_recall_at_10_before": {
            "hits": bekko_recall, "denominator_matched_labels": retrieval_denominator,
            "value": bekko_recall / retrieval_denominator if retrieval_denominator else None,
        },
        "reranker_top1_after": {
            "hits": rerank_recall, "denominator_matched_labels": retrieval_denominator,
            "value": rerank_recall / retrieval_denominator if retrieval_denominator else None,
        },
        "strict_gate_end_to_end": {
            "correct_match_and_row": strict_tp,
            "false_accept": strict_fp,
            "missed_match_or_review_or_wrong_row": strict_fn,
            "correct_nonmatch_or_review": strict_tn,
            "precision": strict_tp / (strict_tp + strict_fp) if strict_tp + strict_fp else None,
            "recall": strict_tp / (strict_tp + strict_fn) if strict_tp + strict_fn else None,
        },
        "by_split": split_metrics,
    }


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    task_path, candidate_path, label_path = Path(args.tasks), Path(args.candidates), Path(args.labels)
    output = Path(args.output)
    evaluator_name = args.evaluator_module
    if output.exists():
        raise FileExistsError(f"Refusing to reuse a trial output directory: {output}")
    for path in (task_path, candidate_path, label_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    input_pre = {"tasks": sha256(task_path), "candidates": sha256(candidate_path), "labels": sha256(label_path)}
    tasks = read_jsonl(task_path)
    rankings = read_json_rows(candidate_path)
    sys.path.insert(0, str(HERE))
    feature_text, gate = get_evaluator(evaluator_name)
    from backend_reranker import Model
    model = Model(args.model_dir, threads=THREADS)
    raw, inference = raw_rank_predictions(model, tasks, rankings, feature_text, gate)

    # Persist all model/gate output before loading any label values.
    raw_path = output / "predictions-structured-reranker.jsonl"
    atomic_jsonl(raw_path, raw)

    labels = read_jsonl(label_path)
    metrics = label_metrics(raw, labels)
    model_manifest_path = HERE / "manifests/reranker.json"
    evaluator_path = Path(importlib.import_module(evaluator_name).__file__)
    source_hashes = {"tasks_pre": input_pre["tasks"], "candidates_pre": input_pre["candidates"],
                     "labels_pre": input_pre["labels"], "evaluator": sha256(evaluator_path),
                     "reranker_backend": sha256(HERE / "backend_reranker.py"),
                     "model_manifest": sha256(model_manifest_path),
                     "trial_script": sha256(Path(__file__))}
    input_post = {"tasks": sha256(task_path), "candidates": sha256(candidate_path), "labels": sha256(label_path)}
    if input_pre != input_post:
        raise RuntimeError("One or more frozen input files changed during the run")
    manifest = {
        "schema_version": "structured-sku-reranker-trial-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model.metadata, "model_dir": str(Path(args.model_dir).resolve()),
        "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                    "onnxruntime": _package_version("onnxruntime"),
                    "tokenizers": _package_version("tokenizers"), "threads": THREADS,
                    "batch_size": BATCH_SIZE, "execution": "serial CPUExecutionProvider"},
        "inputs": {"task_count": len(tasks), "ranking_count": len(rankings),
                   "label_count": len(labels), "input_sha256_pre": input_pre,
                   "input_sha256_post": input_post, "sha256": source_hashes},
        "predictions": {"path": raw_path.name, "count": len(raw),
                        "sha256": sha256(raw_path), "case_key_membership_exact": True,
                        "top10_candidate_key_membership_validated": True},
        "input_policy": {"features": "short canonical structured attributes; feature_text(..., mode='canonical')",
                         "candidate_scope": "Bekko canonical top ten only",
                         "excluded": ["siblings", "price", "stock", "source URL", "label", "evidence quotations"]},
        "inference": inference,
        "label_metrics": metrics,
    }
    atomic_json(output / "manifest.json", manifest)
    atomic_json(output / "metrics.json", metrics)
    return manifest


def _package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path,
                        help="Bekko canonical predictions JSON (path supplied by caller)")
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--evaluator-module", default=DEFAULT_EVALUATOR)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"output": str(args.output), "prediction_count": result["predictions"]["count"],
                      "metrics": result["label_metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
