#!/usr/bin/env python3
"""Probe raw-axis span extraction on real AU titles and selected rows.

This title-only diagnostic does not infer absence, normalize values, score labels,
or adopt a SKU. It uses caller-supplied raw axis labels, without a field taxonomy.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(input_dir: Path, output_dir: Path, threads=2, schema_style="axis"):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    writer = load("trial_structured_choice_probe_v1")
    tasks = writer.read(input_dir / "tasks.jsonl")
    documents = {r["dossier_id"]: r for r in writer.read(input_dir / "documents.jsonl")}
    requests = []
    for task in tasks:
        document = documents[task["dossier_id"]]
        row = next(r for r in document["au"]["rows"] if r["row_key"] == task["au_row_key"])
        if row != task["selected_au_row"] or task["au_product_id"] != document["au_product_id"]:
            raise ValueError("fixed AU row mismatch")
        text = "現在のAU商品名：" + document["au"]["title"] + "\n現在のAU選択行：\n"
        text += "\n".join(a["axis_name"] + "：" + a["value"] for a in row["axes"])
        description = task["axis_name"] if schema_style == "axis" else (
            f"この商品の「{task['axis_name']}」の選択値を示す原文。選択肢は"
            + "、".join("「" + value + "」" for value in task["option_values"])
            + "。選択値に対応する仕様や状態を表す原文を抽出してください。")
        requests.append({"id": task["task_id"], "text": text, "axis_name": task["axis_name"],
                         "schema_description": description,
                         "case_id": task["case_id"], "au_row_key": task["au_row_key"],
                         "selected_au_row": row, "option_values": task["option_values"],
                         "selected_value_metadata_only": task["selected_value"],
                         "source_sku_key": task["source_sku_key"], "title_source": document["au"]["title_source"],
                         "scope_proven": False})
    output_dir.mkdir(parents=True, exist_ok=False)
    writer.write(output_dir / "requests.jsonl", requests)
    code = output_dir / "code"
    code.mkdir()
    for path in (Path(__file__), HERE / "backend_gliner_extract.py", HERE / "trial_structured_choice_probe_v1.py"):
        shutil.copyfile(path, code / path.name)
    freeze = {"schema_version": "generic-raw-axis-span-probe-v1", "labels_read": False,
              "production_eligible": False, "probe_scope": "title_and_selected_au_row_only",
              "field_taxonomy": None, "schema_style": schema_style,
              "schema_description": "raw axis name; optional full raw alternatives in one generic template",
              "missing_entity_means": "unknown; never absence",
              "desired_rakuten_value_in_model_text": False, "threshold": 0.5,
              "input_sha256": {n: writer.sha(input_dir / n) for n in ("tasks.jsonl", "documents.jsonl")},
              "requests_sha256": writer.sha(output_dir / "requests.jsonl"),
              "code_sha256": {p.name: writer.sha(p) for p in code.iterdir()}}
    (output_dir / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import torch
    torch.set_num_threads(threads)
    pinned = load("backend_gliner_extract")
    verified = pinned.verify_snapshot()
    from gliner2 import AutoExtractor
    started = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(pinned.MODEL_DIR), local_files_only=True).to("cpu").eval()
    schemas = [model.create_schema().entities({r["axis_name"]: r["schema_description"]}) for r in requests]
    word_counts = [len(list(model.processor.word_splitter(r["text"], lower=True))) for r in requests]
    if any(n > 512 for n in word_counts):
        raise ValueError("title probe exceeds declared word-token bound; no truncation allowed")
    print(json.dumps({"event": "model_ready", "model": pinned.REPO, "request_count": len(requests)}), flush=True)
    raw = model.batch_extract([r["text"] for r in requests], schemas, batch_size=4,
                              include_spans=True, include_confidence=True, threshold=0.5, max_len=None)
    records = []
    for request, output, count in zip(requests, raw, word_counts, strict=True):
        for entity in output.get("entities", {}).get(request["axis_name"], []):
            if request["text"][entity["start"]:entity["end"]] != entity["text"]:
                raise ValueError("returned entity is not the literal source span")
        records.append({**request, "raw_output": output, "word_token_count": count,
                        "span_offset_basis": "composite model text; not source HTML or file bytes"})
    writer.write(output_dir / "predictions.jsonl", records)
    summary = {**freeze, "model_id": pinned.REPO, "revision": pinned.REVISION,
               "files_verified": verified, "parameter_count": sum(p.numel() for p in model.parameters()),
               "device": "cpu", "threads": threads, "elapsed_seconds": time.perf_counter() - started,
               "conditions_with_spans": sum(bool(r["raw_output"].get("entities", {}).get(r["axis_name"])) for r in records),
               "request_count": len(records), "span_matches_are_semantic_proof": False,
               "output_sha256": {n: writer.sha(output_dir / n) for n in ("requests.jsonl", "predictions.jsonl", "freeze.json")}}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("model_id", "request_count", "conditions_with_spans", "elapsed_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--schema-style", choices=("axis", "axis_options"), default="axis")
    run(**vars(parser.parse_args()))
