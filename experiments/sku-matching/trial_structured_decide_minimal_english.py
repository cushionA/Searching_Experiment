#!/usr/bin/env python3
"""Single-form English-label ablation using minimal structured SKU attributes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
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
BASE_SCRIPT = HERE / "trial_structured_decide.py"
TASKS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v4/tasks.jsonl"
CANDIDATES = ROOT / ".lab-output/sku-structured-task-trials-20261010-v4/candidates.jsonl"
INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
LABELS_PATH = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
OUTPUT = ROOT / ".lab-output/sku-structured-decide-minimal-20261010-v1"
MODEL_DIR = ROOT / ".deps/sku-gliner-model"
REPO = "fastino/GLiNER2.5-multi-Decide"
REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
LABELS = [
    "same selected product configuration",
    "different selected product configuration",
    "insufficient or conflicting information",
]
LABEL_TO_DECISION = dict(zip(LABELS, ("matched", "unmatched", "review"), strict=True))
DECISIONS = {v: k for k, v in LABEL_TO_DECISION.items()}
PINNED_INPUTS = {
    "tasks": "25ee47b500a73f25851857452a56fa73854b1758e6628676fa536369e365aa50",
    "candidates": "31023b39b3dfbfa39924a571b057919ac79b221bec4c0040dc0088b569d1d24d",
    "cases": "4e891fcea8e309e4965c0880da0ca129d5733a889644d090d9ee688723c8e3a1",
    "labels": "a461717495fea768d03e79d5d884c32e3a2eee4da6a7167795a1c57fcf38ea28",
}

spec = importlib.util.spec_from_file_location("structured_decide_base", BASE_SCRIPT)
assert spec and spec.loader
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def _short_attrs(value: Any) -> dict[str, str]:
    """Serialize canonical scalar/list values, omitting metadata and citations."""
    return base._attrs(value, "english")


def _english_field(key: Any) -> str:
    return base.EN_FIELDS.get(str(key), str(key))


def _conflicts(*sources: Any) -> list[str]:
    rows = []
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "unknown_field")
            vals = item.get("values")
            if isinstance(vals, list):
                val = " / ".join(str(v) for v in vals)
            elif vals is None:
                val = "unknown"
            else:
                val = str(vals)
            rows.append(f"{field}: {val}")
    return rows


def build_minimal_english(task: dict[str, Any], candidate_key: str) -> str:
    """Only paired selected attributes plus explicit unknown/conflict facts."""
    rakuten = task.get("rakuten")
    candidates = task.get("au_candidates")
    if not isinstance(rakuten, dict) or not isinstance(candidates, list):
        raise ValueError(f"{task.get('case_id')}: malformed structured task")
    candidate = next((c for c in candidates if c.get("row_key") == candidate_key), None)
    if candidate is None:
        raise ValueError(f"{task.get('case_id')}: top candidate key absent from task")
    left = _short_attrs(rakuten.get("attrs"))
    right = _short_attrs(candidate.get("attrs"))
    left_unknown = rakuten.get("unknown_fields", [])
    right_unknown = candidate.get("unknown_fields", [])
    for key in left_unknown if isinstance(left_unknown, list) else []:
        left.setdefault(_english_field(key), "unknown")
    for key in right_unknown if isinstance(right_unknown, list) else []:
        right.setdefault(_english_field(key), "unknown")
    page = task.get("page_context") or {}
    page_attrs = _short_attrs(page.get("attrs") if isinstance(page, dict) else {})
    page_unknown = page.get("unknown_fields", []) if isinstance(page, dict) else []
    for key in page_unknown if isinstance(page_unknown, list) else []:
        page_attrs.setdefault(_english_field(key), "unknown")
    for key, value in page_attrs.items():
        right.setdefault(key, value)
    keys = sorted(set(left) | set(right))
    if not keys:
        keys = sorted(set(left_unknown if isinstance(left_unknown, list) else []) |
                      set(right_unknown if isinstance(right_unknown, list) else []))
    left_text = "; ".join(f"{k}={left.get(k, 'unknown')}" for k in keys) or "unknown"
    right_text = "; ".join(f"{k}={right.get(k, 'unknown')}" for k in keys) or "unknown"
    conflict_facts = _conflicts(
        rakuten.get("source_conflicts"), candidate.get("source_conflicts"),
        page.get("internal_source_conflicts") if isinstance(page, dict) else None,
    )
    conflict_text = "; ".join(conflict_facts) if conflict_facts else "none"
    text = (f"Rakuten selected attributes: {left_text}\n"
            f"Fixed AU selected attributes: {right_text}\n"
            f"Unknown or conflicting source facts: {conflict_text}")
    folded = text.casefold()
    if any(term in folded for term in ("http://", "https://", "price", "stock", "inventory", "availability")):
        raise ValueError("forbidden metadata leaked into minimal English input")
    return text


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = {"tasks": args.tasks, "candidates": args.candidates,
             "cases": args.inputs_dir / "cases.jsonl", "labels": args.labels}
    pre_hashes = {k: sha256(v) for k, v in paths.items()}
    if pre_hashes != PINNED_INPUTS:
        raise ValueError(f"Frozen v4 inputs changed: {pre_hashes}")
    tasks = base.unique_by_case(base.read_jsonl(args.tasks), str(args.tasks))
    candidate_rows = base.unique_by_case(base.read_jsonl(args.candidates), str(args.candidates))
    sample, sampling = base.sample_ids(args.inputs_dir)
    previous_summary = json.loads((ROOT / ".lab-output/sku-structured-task-trials-20261010-v4/decide/summary.json").read_text())
    expected_ids = previous_summary["sampling"]["selected_case_ids"]
    ids = [r["case_id"] for r in sample]
    if ids != expected_ids or len(ids) != 166:
        raise ValueError("Case IDs differ from the completed v4 run")
    if not set(ids) <= tasks.keys() or not set(ids) <= candidate_rows.keys():
        raise ValueError("Frozen task/candidate files do not cover the stable sample")
    selected = {r["case_id"]: r for r in sample}
    pairs = []
    for cid in ids:
        task, choice = tasks[cid], candidate_rows[cid]
        pairs.append({"case_id": cid,
                      "split": selected[cid].get("split", selected[cid].get("shard_id")),
                      "top_row_key": choice.get("top_row_key"),
                      "input": build_minimal_english(task, choice.get("top_row_key"))})

    args.output.mkdir(parents=True, exist_ok=False)
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
    source_manifest = json.loads((args.model_dir / "source-manifest.json").read_text())
    if source_manifest.get("repo") != REPO or source_manifest.get("revision") != REVISION:
        raise RuntimeError("Local GLiNER snapshot manifest differs from pinned revision")

    from gliner2 import AutoExtractor
    load_start = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(args.model_dir), local_files_only=True)
    model.eval()
    load_seconds = time.perf_counter() - load_start
    start = time.perf_counter()
    texts = [p["input"] for p in pairs]
    raw_outputs = model.batch_classify_text(texts, {"decision": LABELS}, batch_size=8,
                                            include_confidence=True)
    inference_seconds = time.perf_counter() - start
    if len(raw_outputs) != len(pairs):
        raise RuntimeError("GLiNER returned an unexpected number of predictions")
    rows = []
    for pair, output in zip(pairs, raw_outputs, strict=True):
        dec = output.get("decision") if isinstance(output, dict) else output
        label = dec.get("label") if isinstance(dec, dict) else dec
        if label not in LABEL_TO_DECISION:
            raise ValueError(f"Unexpected label for {pair['case_id']}: {label!r}")
        decision = LABEL_TO_DECISION[label]
        gate, reasons = base.deterministic_gate(
            tasks[pair["case_id"]], pair["top_row_key"], decision)
        rows.append({"case_id": pair["case_id"], "split": pair["split"],
                     "top_row_key": pair["top_row_key"], "model_input": pair["input"],
                     "prediction_label": label, "prediction_decision": decision,
                     "deterministic_gate_decision": gate, "deterministic_gate_reasons": reasons,
                     "confidence_raw": dec.get("confidence") if isinstance(dec, dict) else None,
                     "raw_output": output})
    # Durable predictions precede opening gold labels.
    raw_path = args.output / "raw_predictions.json"
    _write_json(raw_path, rows)
    labels = base.load_labels(args.labels)
    if not set(ids) <= labels.keys():
        raise ValueError("Labels do not cover the fixed sample")
    gold = [labels[cid]["decision"] for cid in ids]
    hits = [pair["top_row_key"] in labels[pair["case_id"]]["matching_au_row_keys"] for pair in pairs]
    raw_pred = [row["prediction_decision"] for row in rows]
    gate_pred = [row["deterministic_gate_decision"] for row in rows]
    result_metrics = {
        "raw_model": base.metrics(gold, raw_pred, hits),
        "model_plus_deterministic_gate": base.metrics(gold, gate_pred, hits),
        "by_split": {},
    }
    for split in ("dev", "test"):
        ix = [i for i, row in enumerate(rows) if row["split"] == split]
        result_metrics["by_split"][split] = {
            "raw_model": base.metrics([gold[i] for i in ix], [raw_pred[i] for i in ix], [hits[i] for i in ix]),
            "model_plus_deterministic_gate": base.metrics([gold[i] for i in ix], [gate_pred[i] for i in ix], [hits[i] for i in ix]),
        }
    dev = result_metrics["by_split"]["dev"]["raw_model"]
    eligible = (dev["correct_selection_precision"] is not None
                and dev["correct_selection_precision"] >= 0.99
                and dev["accepted_review_count"] == 0 and dev["accepted_prediction_count"] > 0)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True,
                                              trust_remote_code=False, use_fast=True)
    encoded = tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)
    lengths = [len(v) for v in encoded["input_ids"]]
    post_hashes = {k: sha256(v) for k, v in paths.items()}
    if post_hashes != pre_hashes:
        raise RuntimeError(f"Frozen inputs changed during run: pre={pre_hashes}, post={post_hashes}")
    versions = {}
    for pkg in ("gliner2", "torch", "transformers", "tokenizers", "safetensors"):
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": REPO, "revision": REVISION, "model_files_pinned_sha256": model_files,
        "task": "minimal English feature input with English class labels",
        "labels": LABELS,
        "label_status": "machine-annotated, human-unreviewed",
        "threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
        "batch_size": 8, "load_seconds": load_seconds,
        "inference_timing": {"seconds": inference_seconds, "cases": len(rows),
                              "seconds_per_case": inference_seconds / len(rows)},
        "runtime_versions": versions, "python": platform.python_version(), "platform": platform.platform(),
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "sampling": sampling, "same_case_ids_as_v4_run": True,
        "input_sha256": {"pre_run": pre_hashes, "post_run": post_hashes,
                         "unchanged_during_run": pre_hashes == post_hashes},
        "raw_predictions_path": raw_path.name, "raw_predictions_sha256": sha256(raw_path),
        "token_lengths": {"max": max(lengths, default=0),
                          "mean": sum(lengths) / len(lengths) if lengths else None,
                          "per_case": lengths},
        "input_contract": {
            "includes": ["Rakuten selected canonical attributes", "fixed AU selected canonical attributes",
                         "explicit unknown fields", "source-conflict field/value facts"],
            "excludes": ["class labels and label wording in input", "raw SKU strings", "evidence quotes", "source refs",
                         "gold labels", "price", "stock", "availability", "URLs", "sibling routing", "strata"],
            "prompt_instruction": "none; only paired attributes and unknown/conflict facts",
        },
        "metrics": result_metrics,
        "dev_selection": {"criterion": "raw model: precision >= .99, zero accepted gold-review cases; maximize E2E recall",
                          "eligible": eligible, "selected": "minimal_english" if eligible else None,
                          "test_used_for_selection": False},
        "errors": [],
        "interpretation_limits": ["same stable 166-case sample and fixed candidate policy as v4",
                                  "existing test cases have been inspected; test is not a fresh holdout",
                                  "Luna labels are unreviewed machine annotations"],
    }
    _write_json(args.output / "summary.json", result)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks", type=Path, default=TASKS)
    p.add_argument("--candidates", type=Path, default=CANDIDATES)
    p.add_argument("--inputs-dir", type=Path, default=INPUTS)
    p.add_argument("--labels", type=Path, default=LABELS_PATH)
    p.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    p.add_argument("--output", type=Path, default=OUTPUT)
    args = p.parse_args()
    if args.output.exists():
        p.error(f"output already exists: {args.output}")
    result = run(args)
    print(json.dumps({"output": str(args.output), "cases": result["sampling"]["selected_case_count"],
                      "metrics": result["metrics"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
