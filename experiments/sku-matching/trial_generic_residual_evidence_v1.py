#!/usr/bin/env python3
"""Compare general classifiers on all fixed-AU windows, without gold labels.

This diagnoses the residual stage only, not whole-SKU correctness. Conditions
are opaque whole axis values; scope and condition relation are separate calls.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import shutil
import time
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
THRESHOLD = 0.90
QUESTION = "前提が正しいとき、仮説との論理的な関係を判定してください。前提から分からない情報を補わないでください。"
CHOICES = [
    {"label": "entailment", "description": "含意：前提から仮説が正しいと必ず言える"},
    {"label": "contradiction", "description": "矛盾：前提から仮説が誤りだと必ず言える"},
    {"label": "neutral", "description": "中立：前提だけでは仮説が正しいとも誤りとも判断できない"}]
RELATIONS = {"entailment": "support", "contradiction": "conflict", "neutral": "unknown"}


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path: Path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path: Path, rows):
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def requests_from(tasks, documents):
    by_dossier = {doc["dossier_id"]: doc for doc in documents}
    requests = []
    for task in tasks:
        document = by_dossier[task["dossier_id"]]
        selected_row = [{"axis_name": x["axis_name"], "value": x["value"]}
                        for x in task["selected_au_row"]["axes"]]
        for field in ("title_document", "description_document"):
            for window in document[field]["windows"]:
                # Requested Rakuten value is never inserted into the AU premise.
                premise = json.dumps({"選択中のAU行": selected_row, "AUページの引用": window["text"]}, ensure_ascii=False)
                hypotheses = {
                    "condition": f"このAU行の「{task['axis_name']}」は「{task['selected_value']}」です。",
                    "scope": "引用に書かれた仕様は、選択中のAU行に適用されます。"}
                for purpose, hypothesis in hypotheses.items():
                    requests.append({
                        "id": f"{task['task_id']}:{window['window_id']}:{purpose}",
                        "question": QUESTION,
                        "state": json.dumps({"前提": premise, "仮説": hypothesis}, ensure_ascii=False),
                        "choices": CHOICES, "premise": premise, "hypothesis": hypothesis,
                        "task_id": task["task_id"], "case_id": task["case_id"],
                        "au_row_key": task["au_row_key"], "condition_id": task["condition_id"],
                        "purpose": purpose, "window": window, "document_field": field,
                        "fixed_au_product_ref": task["fixed_au_product_ref"]})
    return requests


def run(input_dir: Path, output_dir: Path, backend: str, threads: int, batch_size: int,
        cache_records: Path | None = None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    tasks = read(input_dir / "tasks.jsonl")
    documents = read(input_dir / "documents.jsonl")
    requests = requests_from(tasks, documents)
    output_dir.mkdir(parents=True, exist_ok=False)
    write(output_dir / "requests.jsonl", requests)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    code_dir = output_dir / "code"
    code_dir.mkdir()
    code_paths = [Path(__file__), HERE / ("trial_generic_model_jev_v1.py" if backend == "jev" else "trial_generic_model_nli_v1.py")]
    if backend != "jev":
        code_paths.append(HERE / "trial_cpu_requirement_relations_v1.py")
    for path in code_paths:
        shutil.copyfile(path, code_dir / path.name)
    cache = {record["cache_key"]: record for record in read(cache_records)} if cache_records is not None else None
    if cache is not None and backend == "gliner":
        raise ValueError("GLiNER caching is not implemented")
    freeze = {"labels_read": False, "whole_sku_evaluation": False,
              "input_sha256": {name: sha(input_dir / name) for name in ("tasks.jsonl", "documents.jsonl", "manifest.json")},
              "request_sha256": sha(output_dir / "requests.jsonl"), "code_sha256": sha(Path(__file__)),
              "code_snapshot_sha256": {path.name: sha(code_dir / path.name) for path in code_paths},
              "cache_ref": {"path": str(cache_records), "sha256": sha(cache_records)} if cache_records is not None else None,
              "backend": backend, "threshold": THRESHOLD, "all_windows_retained": True,
              "hypothesis_rendering": "opaque axis and full selected value; no condition-type templates",
              "scope_task": "generic NLI transfer; unvalidated until evidence audit"}
    (output_dir / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    started = time.perf_counter()
    if backend == "jev":
        result = load_module("trial_generic_model_jev_v1").run_choices(requests, threads=threads, batch_size=batch_size, cache=cache)
        records = result["records"]
        for record in records:
            label = record.get("argmax_label")
            confidence = record.get("argmax_probability")
            record["relation"] = RELATIONS.get(label, "unknown") if confidence is not None and confidence >= THRESHOLD else "unknown"
    elif backend == "nli":
        result = load_module("trial_generic_model_nli_v1").run_pairs(requests, threads=threads, batch_size=batch_size, cache=cache)
        records = result["records"]
    else:
        # Reuse only the frozen public loader's generic relation mode; no
        # legacy validator, condition decomposition, or component schema runs.
        module = load_module("trial_cpu_requirement_relations_v1")
        inference_tasks = [{"task_id": r["id"], "case_id": r["case_id"],
                            "au_row_key": r["au_row_key"],
                            "requirement": {"raw_condition": r["hypothesis"]},
                            "hypothesis": r["hypothesis"],
                            "evidence": {"evidence_id": r["id"], "quote": r["premise"], "span": {"quote": r["premise"]}},
                            "source_scope": {"kind": "caller_bound_window"}} for r in requests]
        raw, pins, runtime = module.run_gliner(inference_tasks, module.DEFAULT_GLINER,
                                               threads, batch_size, "relation", lambda *_: None)
        records = []
        for request, prediction in zip(requests, raw, strict=True):
            confidence = prediction.get("predicted_confidence")
            records.append({**request, "prediction": prediction,
                            "relation": prediction.get("predicted_class", "unknown")
                            if confidence is not None and confidence >= THRESHOLD else "unknown"})
        result = {"pins": pins, "runtime": runtime}
    write(output_dir / "predictions.jsonl", records)
    by_task_window = {}
    for request, record in zip(requests, records, strict=True):
        pair = by_task_window.setdefault((request["task_id"], request["window"]["window_id"]), {})
        pair[request["purpose"]] = record["relation"]
    diagnostic = []
    for task in tasks:
        windows = [pair for (task_id, _), pair in by_task_window.items() if task_id == task["task_id"]]
        applicable = [pair["condition"] for pair in windows if pair["scope"] == "support"]
        state = "conflict" if "conflict" in applicable else "support" if "support" in applicable else "unknown"
        diagnostic.append({"task_id": task["task_id"], "case_id": task["case_id"],
                           "au_row_key": task["au_row_key"], "condition_id": task["condition_id"],
                           "condition_text": task["condition_text"], "diagnostic_relation": state,
                           "window_count": len(windows), "applicable_window_count": len(applicable),
                           "applicable_relations": dict(Counter(applicable)), "sku_adoption": "not_decided"})
    write(output_dir / "condition-diagnostics.jsonl", diagnostic)
    summary = {**freeze, "pins": result["pins"], "runtime": result["runtime"],
               "elapsed_seconds": time.perf_counter() - started,
               "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               "task_count": len(tasks), "request_count": len(requests),
               "by_purpose": {purpose: dict(Counter(rec["relation"] for req, rec in zip(requests, records, strict=True)
                                                  if req["purpose"] == purpose)) for purpose in ("condition", "scope")},
               "diagnostic_relations": dict(Counter(row["diagnostic_relation"] for row in diagnostic)),
               "output_sha256": {name: sha(output_dir / name) for name in ("predictions.jsonl", "condition-diagnostics.jsonl")}}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("backend", "task_count", "request_count", "by_purpose", "diagnostic_relations", "elapsed_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-residual-evidence-20261010-v2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=("jev", "nli", "gliner"), default="jev")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--cache-records", type=Path)
    run(**vars(parser.parse_args()))
