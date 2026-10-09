#!/usr/bin/env python3
"""Run a frozen-sample GLiNER Decide trial on structured SKU facts.

The task and candidate files contain no gold labels. Raw predictions are
persisted before labels are opened and joined for diagnostic metrics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_TASKS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v3/tasks.jsonl"
DEFAULT_CANDIDATES = ROOT / ".lab-output/sku-structured-task-trials-20261010-v3/candidates.jsonl"
DEFAULT_INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-structured-task-trials-20261010-v3/gliner-decide"
MODEL_DIR = ROOT / ".deps/sku-gliner-model"
REPO = "fastino/GLiNER2.5-multi-Decide"
REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
LABELS = ["同じ選択SKU仕様", "異なる選択SKU仕様", "情報不足で要確認"]
DECISIONS = {"matched": LABELS[0], "unmatched": LABELS[1], "review": LABELS[2]}
LABEL_DECISIONS = {v: k for k, v in DECISIONS.items()}
EN_FIELDS = {
    "width_cm": "width_cm", "height_cm": "height_cm", "size": "size",
    "color": "color", "colour": "color", "lace": "lace", "pieces": "pieces",
    "material": "material", "model": "model", "series": "series",
    "pattern": "pattern", "type": "type", "quantity": "quantity",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{n}: expected object")
                rows.append(row)
    return rows


def unique_by_case(rows: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"{source}: missing case_id")
        if cid in result:
            raise ValueError(f"{source}: duplicate case_id {cid}")
        result[cid] = row
    return result


def sample_ids(inputs_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reuse the pre-label stable hash sample from the established evaluator."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from evaluate_luna_decide import read_jsonl as read_old_jsonl, select_sample

    cases_path = inputs_dir / "cases.jsonl"
    cases = read_old_jsonl(cases_path)
    dossiers = {}
    for path in (inputs_dir / "dossiers").glob("*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        dossiers[item["dossier_id"]] = item
    selected = select_sample(cases, dossiers)
    tests = sum(c.get("split", c.get("shard_id")) == "test" for c in selected)
    if len(selected) != 166 or tests != 62:
        raise RuntimeError(f"Frozen stable sample changed: {len(selected)} cases, {tests} test cases")
    meta = {
        "case_count": len(cases), "selected_case_count": len(selected),
        "case_id_hash": "sha256(case_id UTF-8)",
        "selection_independent_of_labels_and_predictions": True,
        "selected_test_case_count": tests,
        "selected_case_ids": [r["case_id"] for r in selected],
    }
    return selected, meta


def _attrs(value: Any, label_mode: str) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("attrs must be an object")
    result = {}
    for key in sorted(value):
        # These fields are decision-irrelevant or forbidden, regardless of source.
        low = str(key).casefold()
        if any(term in low for term in ("price", "stock", "inventory", "availability", "url", "href")):
            continue
        val = value[key]
        if val is None or isinstance(val, dict):
            continue
        name = EN_FIELDS.get(str(key), str(key)) if label_mode == "english" else str(key)
        if isinstance(val, list) and all(not isinstance(x, (dict, list)) for x in val):
            if not val:
                continue
            result[name] = "×".join(str(x) for x in val)
        elif isinstance(val, bool):
            result[name] = ("true" if val else "false") if label_mode == "english" else ("あり" if val else "なし")
        else:
            result[name] = str(val).strip()
    return result


def _mark_unknown(attrs: dict[str, str], value: Any, label_mode: str) -> None:
    if not isinstance(value, list):
        return
    for raw in value:
        key = str(raw)
        key = EN_FIELDS.get(key, key) if label_mode == "english" else key
        attrs.setdefault(key, "unknown" if label_mode == "english" else "不明")


def _conflict_summary(*sources: Any, english: bool) -> str:
    """Render only conflict field/value pairs; never serialize citations or refs."""
    facts = []
    for source in sources:
        if not isinstance(source, list):
            continue
        for row in source:
            if not isinstance(row, dict):
                continue
            field = str(row.get("field") or "unknown_field")
            values = row.get("values")
            if isinstance(values, list):
                val = " / ".join(str(v) for v in values)
            elif values is None:
                val = "unknown"
            else:
                val = str(values)
            facts.append(f"{field}={val}")
    if not facts:
        return "none" if english else "なし"
    return "; ".join(facts)


def _render_attrs(attrs: dict[str, str], *, english: bool, keys: set[str] | None = None) -> str:
    keys = keys if keys is not None else set(attrs)
    if not keys:
        return "属性: 不明" if not english else "attributes: unknown"
    labels_ja = {
        "width_cm": "幅", "height_cm": "丈", "size": "サイズ", "color": "色",
        "colour": "色", "lace": "レース", "pieces": "枚数", "material": "素材",
        "model": "型番", "series": "シリーズ", "pattern": "柄", "type": "種類",
        "quantity": "数量",
    }
    if english:
        return "; ".join(f"{k}={attrs.get(k, 'unknown')}" for k in sorted(keys))
    return " / ".join(f"{labels_ja.get(k, k)}={attrs.get(k, '不明')}" for k in sorted(keys))


def build_input(task: dict[str, Any], candidate_key: str, form: str) -> str:
    """Build short input from selected facts; never include evidence or routing data."""
    rakuten = task.get("rakuten")
    candidates = task.get("au_candidates")
    if not isinstance(rakuten, dict) or not isinstance(candidates, list):
        raise ValueError(f"{task.get('case_id')}: malformed structured task")
    candidate = next((x for x in candidates if x.get("row_key") == candidate_key), None)
    if candidate is None:
        raise ValueError(f"{task.get('case_id')}: top row key absent from AU candidates")
    english = form == "english"
    rk_attrs = _attrs(rakuten.get("attrs"), form)
    au_attrs = _attrs(candidate.get("attrs"), form)
    _mark_unknown(rk_attrs, rakuten.get("unknown_fields"), form)
    _mark_unknown(au_attrs, candidate.get("unknown_fields"), form)
    # Page context can supply inherited product facts only where the selected SKU
    # record does not state a value. It is explicitly labeled as page context.
    page = task.get("page_context") or {}
    page_attrs = _attrs(page.get("attrs") if isinstance(page, dict) else {}, form)
    _mark_unknown(page_attrs, page.get("unknown_fields") if isinstance(page, dict) else [], form)
    for k, v in page_attrs.items():
        au_attrs.setdefault(k, v)
    all_keys = set(rk_attrs) | set(au_attrs)
    rk_sku = str(rakuten.get("raw_sku") or "").strip()
    au_sku = str(candidate.get("raw_sku") or "").strip()
    page_category = au_attrs.get("category", "")
    conflicts = _conflict_summary(
        rakuten.get("source_conflicts"), candidate.get("source_conflicts"),
        page.get("internal_source_conflicts") if isinstance(page, dict) else None,
        english=english,
    )
    if english:
        lines = ["Compare the selected SKU specifications for the same product.",
                 f"Rakuten selected SKU: {rk_sku or 'unknown'} | {_render_attrs(rk_attrs, english=True, keys=all_keys)}",
                 f"Fixed AU page candidate SKU: {au_sku or 'unknown'} | {_render_attrs(au_attrs, english=True, keys=all_keys)}",
                 f"Fixed AU page category: {page_category or 'unknown'}",
                 f"Source-internal conflicts: {conflicts}",
                 "Use only explicit matching or conflicting facts. Missing facts do not count as agreement.",
                 "Choose matched for same selected specification, unmatched for a clear conflict, review when evidence is insufficient."]
        lines[-1] += " A conflict internal to one source must be review."
    else:
        lines = ["選択されたSKUの仕様が同じかを判定してください。",
                 f"Rakuten選択SKU属性: {_render_attrs(rk_attrs, english=False, keys=all_keys)}",
                 f"固定AUページ候補SKU属性: {_render_attrs(au_attrs, english=False, keys=all_keys)}",
                 f"固定AUページ分類: {page_category or '不明'}",
                 f"情報源内の矛盾: {conflicts}",
                 "明示された一致または矛盾だけを使います。不明な項目は一致の根拠にしません。",
                 "同じ選択仕様なら同じ選択SKU仕様、明確な矛盾なら異なる選択SKU仕様、根拠不足なら情報不足で要確認を選んでください。"]
        lines[-1] += " 同じ情報源の中で値が矛盾している場合も要確認です。"
    text = "\n".join(lines)
    forbidden = ("http://", "https://", "price", "stock", "inventory", "availability")
    if any(term in text.casefold() for term in forbidden):
        raise ValueError("forbidden field leaked into classifier input")
    return text


def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    from evaluate_luna_decide import _label_decision
    rows = unique_by_case(read_jsonl(path), str(path))
    return {cid: {"decision": _label_decision(row),
                  "matching_au_row_keys": row.get("matching_au_row_keys", [])}
            for cid, row in rows.items()}


def metrics(gold: list[str], pred: list[str], row_hits: list[bool]) -> dict[str, Any]:
    matrix = {d: {g: 0 for g in DECISIONS} for d in DECISIONS}
    for g, p in zip(gold, pred, strict=True):
        matrix[g][p] += 1
    known = [i for i, g in enumerate(gold) if g != "review"]
    gold_matched = [i for i, g in enumerate(gold) if g == "matched"]
    accepted = [i for i, p in enumerate(pred) if p == "matched"]
    correct_selection = [i for i in gold_matched if row_hits[i] and pred[i] == "matched"]
    return {
        "confusion_matrix": matrix,
        "known_label_accuracy_excluding_review": (
            sum(pred[i] == gold[i] for i in known) / len(known) if known else None),
        "known_case_count": len(known),
        "review_gold_count": len(gold) - len(known),
        "accepted_review_count": sum(g == "review" and p == "matched" for g, p in zip(gold, pred, strict=True)),
        "gold_matched_count": len(gold_matched),
        "retrieval_top1_row_hit_count_on_gold_matched": sum(row_hits[i] for i in gold_matched),
        "retrieval_top1_row_recall_on_gold_matched": (
            sum(row_hits[i] for i in gold_matched) / len(gold_matched) if gold_matched else None),
        "correct_end_to_end_same_sku_count": len(correct_selection),
        "end_to_end_same_sku_recall": len(correct_selection) / len(gold_matched) if gold_matched else None,
        "accepted_prediction_count": len(accepted),
        "correct_selection_precision": len(correct_selection) / len(accepted) if accepted else None,
        "accepted_wrong_candidate_count": sum(i not in correct_selection for i in accepted),
    }


def deterministic_gate(task: dict[str, Any], candidate_key: str, prediction: str) -> tuple[str, list[str]]:
    """Conservative final gate: source conflict/missing values cannot be accepted."""
    rakuten = task["rakuten"]
    candidate = next(c for c in task["au_candidates"] if c.get("row_key") == candidate_key)
    a, b = _attrs(rakuten.get("attrs"), "ja"), _attrs(candidate.get("attrs"), "ja")
    _mark_unknown(a, rakuten.get("unknown_fields"), "ja")
    _mark_unknown(b, candidate.get("unknown_fields"), "ja")
    page = task.get("page_context") or {}
    page_attrs = _attrs(page.get("attrs") if isinstance(page, dict) else {}, "ja")
    _mark_unknown(page_attrs, page.get("unknown_fields") if isinstance(page, dict) else [], "ja")
    for key, value in page_attrs.items():
        b.setdefault(key, value)
    conflicts = _conflict_summary(rakuten.get("source_conflicts"), candidate.get("source_conflicts"),
                                  page.get("internal_source_conflicts") if isinstance(page, dict) else None,
                                  english=False)
    if conflicts != "なし":
        return "review", ["within_source_conflict"]
    keys = set(a) | set(b)
    if not keys:
        return "review", ["no_comparable_fields"]
    unknown = sorted(k for k in keys if k not in a or k not in b or a.get(k) in {"不明", ""} or b.get(k) in {"不明", ""})
    different = sorted(k for k in keys if k in a and k in b and a[k] not in {"不明", ""} and b[k] not in {"不明", ""} and a[k] != b[k])
    if different:
        return "unmatched", ["contradiction:" + ",".join(different)]
    if unknown:
        return "review", ["missing_or_unknown:" + ",".join(unknown)]
    return prediction, ["all_compared_fields_equal"]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_error(outdir: Path, phase: str, exc: BaseException) -> None:
    errors_path = outdir / "errors.json"
    previous = json.loads(errors_path.read_text(encoding="utf-8")) if errors_path.exists() else []
    previous.append({"phase": phase, "error_type": type(exc).__name__, "error": str(exc)})
    _write_json(errors_path, previous)


def run(args: argparse.Namespace) -> dict[str, Any]:
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1",
                      TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    if torch.cuda.is_available():
        raise RuntimeError("CUDA available; refusing GPU inference")
    from try_gliner import FILES
    model_files = {}
    for name, expected in FILES.items():
        path = args.model_dir / name
        digest = sha256(path)
        if path.stat().st_size != expected["size_bytes"] or digest != expected["sha256"]:
            raise RuntimeError(f"Pinned GLiNER checkpoint verification failed: {name}")
        model_files[name] = {"size_bytes": path.stat().st_size, "sha256": digest}
    source_manifest = json.loads((args.model_dir / "source-manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("repo") != REPO or source_manifest.get("revision") != REVISION:
        raise RuntimeError("Local GLiNER snapshot manifest differs from pinned revision")

    tasks = unique_by_case(read_jsonl(args.tasks), str(args.tasks))
    candidates = unique_by_case(read_jsonl(args.candidates), str(args.candidates))
    sample, sampling = sample_ids(args.inputs_dir)
    ids = [r["case_id"] for r in sample]
    if not set(ids) <= tasks.keys() or not set(ids) <= candidates.keys():
        raise ValueError("task or candidate file does not cover all fixed sampled case IDs")
    pairs = []
    sample_by_id = {r["case_id"]: r for r in sample}
    for cid in ids:
        task, choice = tasks[cid], candidates[cid]
        if not str(task.get("task_version", "")).startswith("structured-sku-task-v"):
            raise ValueError(f"Unexpected task version for {cid}")
        pairs.append({"case_id": cid, "split": sample_by_id[cid].get("split", sample_by_id[cid].get("shard_id")),
                      "top_row_key": choice.get("top_row_key"),
                      "ja": build_input(task, choice.get("top_row_key"), "ja"),
                      "english": build_input(task, choice.get("top_row_key"), "english")})

    args.output.mkdir(parents=True, exist_ok=False)
    load_start = time.perf_counter()
    from gliner2 import AutoExtractor
    try:
        model = AutoExtractor.from_pretrained(str(args.model_dir), local_files_only=True)
    except Exception as exc:
        _record_error(args.output, "model_load", exc)
        raise
    model.eval()
    load_seconds = time.perf_counter() - load_start
    raw_predictions: dict[str, list[dict[str, Any]]] = {}
    timings = {}
    errors = []
    for form in ("ja", "english"):
        start = time.perf_counter()
        texts = [p[form] for p in pairs]
        try:
            outputs = model.batch_classify_text(texts, {"decision": LABELS},
                                                batch_size=args.batch_size,
                                                include_confidence=True)
        except Exception as exc:
            errors.append({"form": form, "error_type": type(exc).__name__, "error": str(exc)})
            _write_json(args.output / "raw_predictions.json", raw_predictions)
            _record_error(args.output, f"inference_{form}", exc)
            raise
        elapsed = time.perf_counter() - start
        if len(outputs) != len(pairs):
            raise RuntimeError(f"unexpected output count for {form}: {len(outputs)}")
        rows = []
        for pair, text, output in zip(pairs, texts, outputs, strict=True):
            d = output.get("decision") if isinstance(output, dict) else output
            label = d.get("label") if isinstance(d, dict) else d
            if label not in LABEL_DECISIONS:
                raise ValueError(f"unexpected output label for {pair['case_id']}: {label!r}")
            raw_decision = LABEL_DECISIONS[label]
            gated, gate_reasons = deterministic_gate(tasks[pair["case_id"]],
                                                    pair["top_row_key"], raw_decision)
            rows.append({"case_id": pair["case_id"], "split": pair["split"],
                         "top_row_key": pair["top_row_key"], "model_input": text,
                         "prediction": label, "prediction_decision": raw_decision,
                         "deterministic_gate_decision": gated,
                         "deterministic_gate_reasons": gate_reasons,
                         "confidence_raw": d.get("confidence") if isinstance(d, dict) else None,
                         "raw_output": output})
        raw_predictions[form] = rows
        _write_json(args.output / "raw_predictions.json", raw_predictions)
        timings[form] = {"seconds": elapsed, "cases": len(rows), "batch_size": args.batch_size,
                         "seconds_per_case": elapsed / len(rows) if rows else None}

    # Persist raw predictions first. Labels are opened only after this durable write.
    raw_path = args.output / "raw_predictions.json"
    _write_json(raw_path, {"ja": raw_predictions["ja"], "english": raw_predictions["english"]})
    labels = load_labels(args.labels)
    if not set(ids) <= labels.keys():
        raise ValueError("labels do not cover sampled case IDs")
    gold = [labels[cid]["decision"] for cid in ids]
    row_hits = [pair["top_row_key"] in labels[pair["case_id"]]["matching_au_row_keys"]
                for pair in pairs]
    metric_results = {}
    for form, rows in raw_predictions.items():
        raw_metric = metrics(gold, [r["prediction_decision"] for r in rows], row_hits)
        gate_metric = metrics(gold, [r["deterministic_gate_decision"] for r in rows], row_hits)
        split_metrics = {}
        for split in ("dev", "test"):
            indices = [i for i, row in enumerate(rows) if row["split"] == split]
            split_metrics[split] = {
                "raw_model": metrics([gold[i] for i in indices],
                                      [rows[i]["prediction_decision"] for i in indices],
                                      [row_hits[i] for i in indices]),
                "model_plus_deterministic_gate": metrics(
                    [gold[i] for i in indices], [rows[i]["deterministic_gate_decision"] for i in indices],
                    [row_hits[i] for i in indices]),
            }
        metric_results[form] = {"raw_model": raw_metric,
                                "model_plus_deterministic_gate": gate_metric,
                                "by_split": split_metrics}
    eligible_forms = []
    for form, value in metric_results.items():
        dev = value["by_split"]["dev"]["raw_model"]
        precision = dev["correct_selection_precision"]
        if (precision is not None and precision >= 0.99
                and dev["accepted_review_count"] == 0 and dev["accepted_prediction_count"] > 0):
            eligible_forms.append((dev["end_to_end_same_sku_recall"] or 0.0,
                                   dev["known_label_accuracy_excluding_review"] or 0.0, form))
    dev_choice = max(eligible_forms)[2] if eligible_forms else None
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True,
                                              trust_remote_code=False, use_fast=True)
    token_lengths = {}
    for form in ("ja", "english"):
        encoded = tokenizer([p[form] for p in pairs], add_special_tokens=True,
                            truncation=False, padding=False)
        lengths = [len(x) for x in encoded["input_ids"]]
        token_lengths[form] = {"max": max(lengths, default=0),
                               "mean": sum(lengths) / len(lengths) if lengths else None,
                               "per_case": lengths}
    versions = {}
    for pkg in ("gliner2", "torch", "transformers", "tokenizers", "safetensors"):
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": REPO, "revision": REVISION, "model_files_pinned_sha256": model_files,
        "source_manifest_sha256": sha256(args.model_dir / "source-manifest.json"),
        "task_version": "structured-sku-task-v2+", "label_status": "machine-annotated, human-unreviewed",
        "threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
        "batch_size": args.batch_size, "load_seconds": load_seconds, "inference_timing": timings,
        "runtime_versions": versions, "platform": platform.platform(),
        "python": platform.python_version(), "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "sampling": sampling,
        "inputs_sha256": {"tasks": sha256(args.tasks), "candidates": sha256(args.candidates),
                          "cases": sha256(args.inputs_dir / "cases.jsonl"), "labels": sha256(args.labels)},
        "input_design": {
            "candidate_policy": "one fixed AU top1 row per Rakuten SKU case, selected upstream from the frozen AU page pool",
            "forms": {"ja": "Japanese canonical attribute labels and structured facts, without raw SKU strings",
                      "english": "English canonical field names and the same facts, plus raw SKU strings"},
            "uses_only": ["Rakuten raw SKU string", "Rakuten selected-SKU attrs", "fixed AU candidate raw SKU string", "AU selected-SKU attrs", "inherited AU page attrs"],
            "excluded": ["gold labels and matching row keys", "evidence quotations", "source refs", "unknown metadata", "strata", "sibling page URLs/routing", "price", "stock", "availability"],
            "unknown_policy": "unknown attributes are explicit as unknown and never interpreted as agreement",
            "page_context_policy": "page attrs are inherited only for fields absent from the selected AU SKU attrs",
        },
        "raw_predictions_path": raw_path.name, "raw_predictions_sha256": sha256(raw_path),
        "token_lengths": token_lengths, "metrics": metric_results,
        "dev_form_selection": {
            "criterion": "raw model on dev only: pair precision >= 0.99 and zero accepted gold-review cases; maximize end-to-end same-SKU recall, then known-label accuracy",
            "eligible_forms": [item[2] for item in eligible_forms],
            "selected_form": dev_choice,
            "status": "selected_on_dev" if dev_choice else "no_form_met_precision_and_review_guard",
            "test_used_for_selection": False,
        },
        "errors": errors,
        "interpretation_limits": ["166-case stable diagnostic sample, not a full-set estimate",
                                  "existing test cases have already been inspected; test is frozen-configuration diagnosis, not fresh holdout",
                                  "Luna labels are unreviewed machine annotations"],
    }
    _write_json(args.output / "summary.json", result)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p.add_argument("--inputs-dir", type=Path, default=DEFAULT_INPUTS)
    p.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    p.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--batch-size", type=int, default=8)
    args = p.parse_args()
    if args.batch_size != 8:
        p.error("this trial is pinned to batch_size=8")
    if args.output.exists():
        p.error(f"output already exists: {args.output}")
    result = run(args)
    print(json.dumps({"output": str(args.output), "sampled_cases": result["sampling"]["selected_case_count"],
                      "metrics": result["metrics"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
