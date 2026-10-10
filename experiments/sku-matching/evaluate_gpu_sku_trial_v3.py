#!/usr/bin/env python3
"""Verify and evaluate the v9 two-prompt, 14-case GPU smoke modes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INPUT_ROOT = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v9-smoke-prepared"
UPLOAD_ROOT = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v9/dataset-upload"
V8_INPUT_ROOT = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v8/dataset-upload"
SOURCE_INPUTS = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/inputs.jsonl"
TASKS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10/tasks.jsonl"
LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
CPU_PREDICTIONS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10/hybrid/predictions.jsonl"
DOSSIERS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3/dossiers"
EXPECTED_INPUT_SHA256 = "6e2a3057dca06e50e1c0c0f421fb9ce234e6964b2f47f668661e664abe1af23d"
EXPECTED_SOURCE_CASES_SHA256 = "a91fca45c574f4d531eca2dc450e81a9adc44fa38c934955109831cf3147e18f"
EXPECTED_CONFIG_SHA256 = "3452f5bf2f85873ba4e6d88c3fb0f891c8b49dff7bb64a9fadf89a456bb63214"
MODEL = {"name": "Qwen/Qwen3.5-4B", "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "load": "nf4"}
PREFILL_METHOD = "Qwen3.5 wrapper cached chunked prefill plus one-token greedy decode"
DECODE_STRATEGY = "greedy_one_token_until_eos_or_limit"
STATUSES = {"ok", "invalid_output", "invalid_input_over_limit", "error", "oom"}
DECISIONS = {"matched", "unmatched", "review"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_no}: expected object")
                rows.append(row)
    return rows


def _load_evaluator_v2():
    path = HERE / "evaluate_gpu_sku_trial_v2.py"
    spec = importlib.util.spec_from_file_location("gpu_sku_evaluator_v2_for_v3", path)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load source-provenance and metric helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy():
    return _load_evaluator_v2().legacy()


def verify_v9_inputs(input_dir: Path, source_inputs: Path = SOURCE_INPUTS,
                     dossiers_dir: Path = DOSSIERS,
                     uploaded_input_dir: Path | None = UPLOAD_ROOT) -> tuple[list[dict], dict[str, Any]]:
    """Revalidate the original v8 source bundle, then require v9 byte-identical sample copies."""
    v2 = _load_evaluator_v2()
    old_cases, old_source_cases, old_info = v2.verify_source_bundle(
        V8_INPUT_ROOT / "inputs.jsonl", V8_INPUT_ROOT, source_inputs, dossiers_dir)
    input_path = input_dir / "inputs.jsonl"
    source_cases_path = input_dir / "source-cases.jsonl"
    manifest = read_json(input_dir / "manifest.json")
    config_path = input_dir / "config.json"
    input_sha, source_cases_sha, config_sha = sha256(input_path), sha256(source_cases_path), sha256(config_path)
    if (input_sha != EXPECTED_INPUT_SHA256 or source_cases_sha != EXPECTED_SOURCE_CASES_SHA256
            or config_sha != EXPECTED_CONFIG_SHA256):
        raise ValueError("v9 smoke inputs/source cases differ from frozen 14-case v8 bundle")
    if input_path.read_bytes() != (V8_INPUT_ROOT / "inputs.jsonl").read_bytes():
        raise ValueError("v9 smoke input bytes differ from frozen v8 sample")
    if source_cases_path.read_bytes() != (V8_INPUT_ROOT / "source-cases.jsonl").read_bytes():
        raise ValueError("v9 source-case bytes differ from frozen v8 sample")
    if (manifest.get("label_blind") is not True or manifest.get("labels_used") is not False
            or manifest.get("parent_bundle") != "sku-kaggle-gpu-20261010-v8"
            or manifest.get("parent_manifest_sha256") != v2.FROZEN_INPUT_MANIFEST_SHA256
            or manifest.get("inputs_sha256") != input_sha
            or manifest.get("source_cases_sha256") != source_cases_sha
            or manifest.get("config_sha256") != config_sha
            or manifest.get("case_count") != 14 or manifest.get("inference_count") != 28
            or manifest.get("full_au_pool_retained") is not True):
        raise ValueError("v9 smoke manifest is not label-blind or its content hashes differ")
    config = read_json(config_path)
    if (config.get("models") != [MODEL] or config.get("modes") != ["strict", "simple"]
            or config.get("mode_order") != "strict_then_simple_per_case"
            or config.get("max_input_tokens") != 12000 or config.get("max_new_tokens") != 256
            or config.get("do_sample") is not False or config.get("decode_strategy") != DECODE_STRATEGY
            or config.get("attention_implementation") != "sdpa" or config.get("logits_to_keep") != 1
            or config.get("enable_thinking") is not False or config.get("prefill_chunk_size") != 512
            or config.get("prefill_method") != PREFILL_METHOD
            or config.get("case_count") != 14 or config.get("inference_count") != 28
            or config.get("diagnostic_only") is not True
            or config.get("parent_input_sha256") != EXPECTED_INPUT_SHA256
            or config.get("parent_source_cases_sha256") != EXPECTED_SOURCE_CASES_SHA256):
        raise ValueError("v9 config must pin frozen 14-case 4B NF4 strict/simple chunked-prefill smoke")
    cases, source_cases = read_jsonl(input_path), read_jsonl(source_cases_path)
    if [c.get("case_id") for c in cases] != [c.get("case_id") for c in old_cases]:
        raise ValueError("v9 case IDs/order differ from source-verified v8 sample")
    if cases != old_cases or source_cases != old_source_cases:
        raise ValueError("v9 parsed inputs/source cases differ from v8 source-verified copies")
    if len(cases) != 14:
        raise ValueError(f"v9 smoke must contain all 14 frozen cases, got {len(cases)}")
    pool_sizes = [len(c.get("au", {}).get("sku_rows", [])) for c in cases]
    if (manifest.get("record_order") != [c["case_id"] for c in cases]
            or manifest.get("pool_sizes") != pool_sizes
            or manifest.get("au_row_count") != sum(pool_sizes)):
        raise ValueError("v9 manifest row order/full-pool counts differ from frozen input bytes")
    parent_manifest = read_json(V8_INPUT_ROOT / "manifest.json")
    if manifest.get("source_sha256") != parent_manifest.get("source_sha256"):
        raise ValueError("v9 source digest references differ from frozen v8 source manifest")
    runner_path = HERE / "kaggle_gpu_sku_runner_v3.py"
    if manifest.get("runner_sha256") != sha256(runner_path):
        raise ValueError("v9 prepared manifest does not pin the current runner source hash")
    if uploaded_input_dir is not None:
        upload_names = ("inputs.jsonl", "source-cases.jsonl", "config.json", "manifest.json")
        for name in upload_names:
            prepared = input_dir / name
            uploaded = uploaded_input_dir / name
            if not prepared.is_file() or not uploaded.is_file() or sha256(prepared) != sha256(uploaded):
                raise ValueError(f"v9 uploaded snapshot differs from prepared bundle: {name}")
        if sha256(uploaded_input_dir / "kaggle_gpu_sku_runner_v3.py") != sha256(runner_path):
            raise ValueError("v9 uploaded runner snapshot differs from frozen runner source")
    return cases, {"manifest": manifest, "manifest_sha256": sha256(input_dir / "manifest.json"),
        "config": config, "config_sha256": config_sha, "input_sha256": input_sha,
        "source_cases_sha256": source_cases_sha, "v8_source_verification": old_info}


def validate_simple_record(case: dict, record: dict) -> dict:
    """Validate simple-mode output syntax and input row mapping; this is not evidence entailment."""
    cid = case["case_id"]
    if record.get("case_id") != cid or record.get("status") not in STATUSES:
        raise ValueError(f"Simple row identity/status invalid: {cid}")
    if record["status"] != "ok":
        return {"status": record["status"], "prediction": {"decision": "review", "au_row_key": None}}
    raw = record.get("raw_output")
    if not isinstance(raw, str):
        raise ValueError(f"Simple successful row lacks raw output: {cid}")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != {"decision", "au_row_alias", "reason"}:
        raise ValueError(f"Simple raw schema mismatch: {cid}")
    decision, alias, reason = obj["decision"], obj["au_row_alias"], obj["reason"]
    if decision not in DECISIONS or not isinstance(reason, str) or not reason.strip() or len(reason) > 80:
        raise ValueError(f"Simple decision/reason invalid: {cid}")
    pool = case.get("au", {}).get("sku_rows", [])
    aliases = {r.get("alias"): r.get("row_key") for r in pool}
    if len(aliases) != len(pool):
        raise ValueError(f"Input AU aliases are not unique: {cid}")
    if ((decision == "matched" and (not isinstance(alias, str) or alias not in aliases))
            or (decision != "matched" and alias is not None)):
        raise ValueError(f"Simple decision has an invalid AU alias: {cid}")
    pred = {"decision": decision, "au_row_key": aliases[alias] if decision == "matched" else None}
    expected_inputrefs = ({"kind": "inputrefs_not_entailment_evidence", "row_key": pred["au_row_key"],
        "sku": next(r["sku"] for r in pool if r["alias"] == alias)} if decision == "matched" else None)
    expected_parsed = {"decision": decision, "au_row_key": pred["au_row_key"], "reason": reason,
        "evidence": [], "inputrefs": expected_inputrefs}
    if record.get("parsed") != expected_parsed or record.get("resolved_evidence") is not None:
        raise ValueError(f"Simple host row mapping differs from raw alias or implies evidence: {cid}")
    return {"status": "ok", "prediction": pred, "reason": reason,
        "evidence_semantics": "input_row_mapping_only_not_model_entailment_evidence"}


def validate_strict_record(case: dict, record: dict) -> dict:
    """Independently resolve strict-mode model evidence IDs; review may cite no cards."""
    cid = case["case_id"]
    if record.get("case_id") != cid or record.get("status") not in STATUSES:
        raise ValueError(f"Strict row identity/status invalid: {cid}")
    if record["status"] != "ok":
        return {"status": record["status"], "prediction": {"decision": "review", "au_row_key": None},
            "evidence_semantics": "unavailable_status_review_fallback"}
    raw = record.get("raw_output")
    if not isinstance(raw, str):
        raise ValueError(f"Strict successful row lacks raw output: {cid}")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or set(obj) != {"decision", "au_row_alias", "reason", "evidence_ids"}:
        raise ValueError(f"Strict raw schema mismatch: {cid}")
    decision, alias, reason, eids = (obj[k] for k in ("decision", "au_row_alias", "reason", "evidence_ids"))
    if decision not in DECISIONS or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 80:
        raise ValueError(f"Strict decision/reason invalid: {cid}")
    pool = case.get("au", {}).get("sku_rows", [])
    aliases = {r.get("alias"): r.get("row_key") for r in pool}
    if (len(aliases) != len(pool)
            or (decision == "matched" and (not isinstance(alias, str) or alias not in aliases))
            or (decision != "matched" and alias is not None)):
        raise ValueError(f"Strict AU alias invalid: {cid}")
    registry_list = case.get("evidence_registry", [])
    registry = {e.get("id"): e for e in registry_list if isinstance(e, dict)}
    if (len(registry) != len(registry_list) or not isinstance(eids, list) or len(eids) > 4
            or any(not isinstance(x, str) for x in eids) or len(set(eids)) != len(eids)
            or any(x not in registry for x in eids)):
        raise ValueError(f"Strict evidence IDs invalid or unresolved: {cid}")
    expected_evidence = []
    for eid in eids:
        card = registry[eid]
        side = card.get("side")
        if side not in {"au", "rakuten"} or card.get("quote") not in case.get("source_texts", {}).get(side, []):
            raise ValueError(f"Strict evidence does not resolve inside input source scope: {cid}:{eid}")
        item = {k: card[k] for k in ("evidence_id", "side", "field", "scope", "source_ref", "quote", "row_key") if k in card}
        item["evidence_id"] = eid
        if "row_key" in item and item["row_key"] not in aliases.values():
            raise ValueError(f"Strict evidence row key outside fixed pool: {cid}:{eid}")
        expected_evidence.append(item)
    if decision in {"matched", "unmatched"}:
        if "rs" not in eids:
            raise ValueError(f"Strict decisive decision lacks selected Rakuten SKU evidence: {cid}")
        row_citations = [eid for eid in eids if eid in aliases]
        if decision == "matched" and (alias not in row_citations or any(x != alias for x in row_citations)):
            raise ValueError(f"Strict matched decision lacks only the chosen AU row evidence: {cid}")
        if decision == "unmatched" and not row_citations:
            raise ValueError(f"Strict unmatched decision lacks an AU conflict row: {cid}")
        if not {registry[x].get("side") for x in eids}.issuperset({"au", "rakuten"}):
            raise ValueError(f"Strict decisive evidence does not cover both source sides: {cid}")
        if any(any(term in str(registry[x].get("scope", "")).casefold().replace("-", "_")
                       for term in ("series", "sibling", "navigation", "related", "category", "recommendation")) for x in eids):
            raise ValueError(f"Strict decision relies on non-item page scope: {cid}")
    expected_parsed = {"decision": decision, "au_row_key": aliases[alias] if decision == "matched" else None,
        "reason": reason, "evidence": expected_evidence}
    if record.get("parsed") != expected_parsed or record.get("resolved_evidence") != expected_evidence:
        raise ValueError(f"Strict host-resolved evidence differs from independent resolution: {cid}")
    return {"status": "ok", "prediction": {"decision": decision, "au_row_key": expected_parsed["au_row_key"]},
        "evidence_semantics": "model_emitted_evidence_ids_independently_resolved"}


def validate_v9_actual_runtime(summary: dict, runtime: dict) -> None:
    if summary.get("model") != MODEL:
        raise ValueError("v9 summary model/revision/load differs from pinned 4B model")
    expected = {"runtime_quantization": "nf4", "is_loaded_in_4bit": True,
        "config_name": MODEL["name"], "config_revision": MODEL["revision"],
        "attention_implementation": "sdpa",
        "prefill_method": PREFILL_METHOD, "prefill_chunk_size": 512,
        "logits_to_keep": 1}
    if any(summary.get(k) != v for k, v in expected.items()):
        raise ValueError("v9 runtime model/NF4/SDPA/chunked-prefill metadata mismatch")
    if (summary.get("nf4_linear4bit_module_count", 0) <= 0
            or summary.get("nf4_quantized_linear4bit_module_count") != summary.get("nf4_linear4bit_module_count")
            or summary.get("nf4_logical_parameter_count", 0) <= 0
            or summary.get("base_logical_parameter_count", 0) <= 0
            or not 0 < summary.get("nf4_coverage_ratio", 0) <= 1):
        raise ValueError("v9 runtime lacks complete actual NF4 module coverage")
    q = summary.get("hf_quantization_config")
    if not isinstance(q, dict) or any(q.get(k) != v for k, v in {
            "load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True}.items()):
        raise ValueError("v9 Transformers quantization config does not verify NF4 double quantization")
    devs = summary.get("parameter_devices")
    dmap = summary.get("hf_device_map")
    if not isinstance(devs, list) or not devs or any(not str(x).startswith("cuda:") for x in devs):
        raise ValueError("v9 model parameters are not wholly CUDA-resident")
    if not isinstance(dmap, dict) or not dmap or any(
            str(v).casefold() in {"cpu", "disk"} or str(v).startswith("cpu") for v in dmap.values()):
        raise ValueError("v9 device map is absent or offloads to CPU/disk")
    if (runtime.get("gpu_available") is not True or runtime.get("cuda_device_count", 0) <= 0
            or not runtime.get("devices") or runtime.get("prefill_method") != expected["prefill_method"]
            or runtime.get("prefill_chunk_size") != 512
            or runtime.get("decode_strategy") != DECODE_STRATEGY or runtime.get("max_new_tokens") != 256
            or runtime.get("runtime_quantization") != "nf4"
            or runtime.get("is_loaded_in_4bit") is not True
            or runtime.get("attention_implementation") != "sdpa"
            or runtime.get("parameter_devices") != devs or runtime.get("hf_device_map") != dmap
            or runtime.get("hf_quantization_config") != q
            or runtime.get("nf4_linear4bit_module_count") != summary.get("nf4_linear4bit_module_count")
            or runtime.get("nf4_quantized_linear4bit_module_count") != summary.get("nf4_quantized_linear4bit_module_count")
            or runtime.get("nf4_coverage_ratio") != summary.get("nf4_coverage_ratio")):
        raise ValueError("v9 runtime.json does not independently confirm CUDA/NF4/prefill execution")


def _record_sha(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_v9_artifacts(cases: list[dict], input_dir: Path, info: dict[str, Any],
                          artifacts: Path) -> tuple[dict[str, list[dict]], dict, dict, str]:
    manifest = info["manifest"]
    config = info["config"]
    runner_path = HERE / "kaggle_gpu_sku_runner_v3.py"
    if not runner_path.is_file():
        raise ValueError("v3 runner source is not available for source-hash verification")
    runner_sha = sha256(runner_path)
    run_path = artifacts / "run-manifest.json"
    run_manifest = read_json(run_path)
    run_sha = sha256(run_path)
    expected_files = {
        "strict": "predictions-strict-qwen3-5-4b-nf4-v9.jsonl",
        "simple": "predictions-simple-qwen3-5-4b-nf4-v9.jsonl"}
    expected_inventory = {"runtime.json", "summary.json", *expected_files.values()}
    inventory = run_manifest.get("files_sha256")
    if (run_manifest.get("schema_version") != "sku-gpu-v9-two-mode-run-v1"
            or run_manifest.get("model") != MODEL or run_manifest.get("modes") != ["strict", "simple"]
            or run_manifest.get("label_blind") is not True or run_manifest.get("labels_used") is not False
            or run_manifest.get("input_sha256") != info["input_sha256"]
            or run_manifest.get("source_cases_sha256") != info["source_cases_sha256"]
            or run_manifest.get("config_sha256") != info["config_sha256"]
            or run_manifest.get("input_manifest_sha256") != info["manifest_sha256"]
            or run_manifest.get("runner_sha256") != runner_sha
            or run_manifest.get("input_count") != len(cases)
            or run_manifest.get("expected_inference_count") != len(cases) * 2
            or run_manifest.get("prefill_method") != config.get("prefill_method")
            or run_manifest.get("prefill_chunk_size") != 512
            or run_manifest.get("decode_strategy") != config.get("decode_strategy")
            or run_manifest.get("max_new_tokens") != config.get("max_new_tokens")):
        raise ValueError("v9 run manifest is not linked to the frozen two-mode inputs/config/runner")
    if not isinstance(inventory, dict) or set(inventory) != expected_inventory:
        raise ValueError("v9 artifact inventory does not contain exactly the two outputs and runtime/summary")
    actual_files = {p.name for p in artifacts.iterdir() if p.is_file() and p.name != "run-manifest.json"}
    if actual_files != set(inventory):
        raise ValueError("v9 artifact directory has missing or unmanifested files")
    for name, digest in inventory.items():
        if sha256(artifacts / name) != digest:
            raise ValueError(f"v9 artifact SHA mismatch: {name}")
    runtime, summary = read_json(artifacts / "runtime.json"), read_json(artifacts / "summary.json")
    if (summary.get("status") != "complete" or summary.get("partial") is not False
            or summary.get("label_blind") is not True or summary.get("labels_used") is not False
            or summary.get("input_count") != 14 or summary.get("expected_inference_count") != 28
            or summary.get("completed_records") != 28
            or summary.get("input_sha256") != info["input_sha256"]
            or summary.get("source_cases_sha256") != info["source_cases_sha256"]
            or summary.get("config_sha256") != info["config_sha256"]
            or summary.get("input_manifest_sha256") != info["manifest_sha256"]
            or summary.get("runner_sha256") != runner_sha
            or summary.get("prefill_method") != config.get("prefill_method")
            or summary.get("prefill_chunk_size") != 512):
        raise ValueError("v9 summary is incomplete or unlinked from frozen source/runner")
    validate_v9_actual_runtime(summary, runtime)
    if run_manifest.get("status") != "complete" or run_manifest.get("partial") is not False:
        raise ValueError("v9 run manifest does not declare a complete, nonpartial diagnostic")

    rows_by_mode = {}
    by_case_mode = {}
    counters = Counter()
    for mode, name in expected_files.items():
        rows = read_jsonl(artifacts / name)
        if len(rows) != len(cases):
            raise ValueError(f"{mode} output has {len(rows)} records, expected 14")
        for i, (case, row) in enumerate(zip(cases, rows, strict=True)):
            if (row.get("case_id") != case["case_id"] or row.get("index") != i
                    or row.get("mode") != mode or row.get("status") not in STATUSES):
                raise ValueError(f"{mode} identity/order/status mismatch at row {i}")
            if row.get("max_input_tokens") != config.get("max_input_tokens") or row.get("max_new_tokens") != config.get("max_new_tokens"):
                raise ValueError(f"{mode} generation budget differs from frozen config: {case['case_id']}")
            ids_sha = row.get("input_token_ids_sha256")
            if row.get("input_tokens") is not None and (not isinstance(ids_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", ids_sha)):
                raise ValueError(f"{mode} input token IDs lack a recorded SHA-256: {case['case_id']}")
            raw = row.get("raw_output")
            if isinstance(raw, str):
                if row.get("output_sha256") != hashlib.sha256(raw.encode()).hexdigest():
                    raise ValueError(f"{mode} raw output digest mismatch: {case['case_id']}")
            elif row.get("output_sha256") is not None:
                raise ValueError(f"{mode} empty raw output has a nonempty output digest: {case['case_id']}")
            if row.get("record_sha256") is not None and row.get("record_sha256") != _record_sha(row):
                raise ValueError(f"v3 canonical row digest mismatch: {mode}:{case['case_id']}")
            if row["status"] in {"ok", "invalid_output", "invalid_input_over_limit"} and not row.get("record_sha256"):
                raise ValueError(f"{mode} canonical row digest mismatch: {case['case_id']}")
            if row["status"] in {"ok", "invalid_output"}:
                peak = row.get("peak_allocated_bytes_by_device")
                if (not isinstance(peak, list) or len(peak) != runtime.get("cuda_device_count")
                        or any(not isinstance(x, int) or x < 0 for x in peak)
                        or not isinstance(row.get("latency_seconds"), (int, float))
                        or row.get("latency_seconds") < 0):
                    raise ValueError(f"{mode} successful generation lacks per-device peak/latency metrics: {case['case_id']}")
            checked = validate_strict_record(case, row) if mode == "strict" else validate_simple_record(case, row)
            row["_evaluator_check"] = checked
            by_case_mode[(case["case_id"], mode)] = row
            counters[(mode, row["status"])] += 1
        rows_by_mode[mode] = rows
    prompt_pairs = []
    for case in cases:
        for mode in ("strict", "simple"):
            row = by_case_mode[(case["case_id"], mode)]
            digest = row.get("prompt_sha256")
            if isinstance(digest, str):
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("Malformed per-record prompt SHA")
                prompt_pairs.append(f"{case['case_id']}:{mode}:{digest}")
    prompt_sha = hashlib.sha256("\n".join(prompt_pairs).encode()).hexdigest()
    if summary.get("prompt_sha256") != prompt_sha or run_manifest.get("prompt_sha256") != prompt_sha:
        raise ValueError("Aggregate prompt hash does not match per-case strict/simple prompt order")
    if (summary.get("prediction_files_sha256") != {
            mode: sha256(artifacts / name) for mode, name in expected_files.items()}):
        raise ValueError("Summary prediction file hashes do not match strict/simple artifact bytes")

    generated = sum(1 for rows in rows_by_mode.values() for row in rows if row["status"] in {"ok", "invalid_output"})
    valid = sum(1 for rows in rows_by_mode.values() for row in rows if row["status"] == "ok")
    invalid = sum(1 for rows in rows_by_mode.values() for row in rows if row["status"] in {"invalid_output", "invalid_input_over_limit"})
    oom = sum(1 for rows in rows_by_mode.values() for row in rows if row["status"] == "oom")
    errors = sum(1 for rows in rows_by_mode.values() for row in rows if row["status"] == "error")
    expected_counts = {"inference_count": generated, "valid_prediction_count": valid,
        "invalid_prediction_count": invalid, "oom_count": oom, "error_count": errors}
    if any(summary.get(k) != v or run_manifest.get(k) != v for k, v in expected_counts.items()):
        raise ValueError("v9 summary/run-manifest generation/status counts differ from mode rows")
    pairs_generated = sum(all(by_case_mode[(c["case_id"], m)]["status"] in {"ok", "invalid_output"}
        for m in ("strict", "simple")) for c in cases)
    if summary.get("completed_pairs") != pairs_generated or run_manifest.get("completed_pairs") != pairs_generated:
        raise ValueError("v9 completed pair count differs from strict/simple generation-complete pairs")
    if run_manifest.get("completed_records") != 28 or summary.get("completed_records") != 28:
        raise ValueError("v9 did not preserve all 28 mode records")
    provenance = {"artifact_inventory": inventory, "runner_sha256": runner_sha,
        "prompt_sha256": prompt_sha, "run_manifest_sha256": run_sha, "runtime": runtime,
        "summary": summary, "mode_status_counts": {m: dict(Counter(r["status"] for r in rows))
            for m, rows in rows_by_mode.items()}, "generated_count": generated}
    return rows_by_mode, run_manifest, provenance, run_sha


def as_scoring_rows(cases: list[dict], rows: list[dict], mode: str) -> list[dict]:
    result = []
    for case, row in zip(cases, rows, strict=True):
        check = row["_evaluator_check"]
        normalized = {**row, "parsed": {"decision": check["prediction"]["decision"],
            "au_row_key": check["prediction"]["au_row_key"]} if row["status"] == "ok" else None}
        normalized["evaluation_mode"] = mode
        normalized["evidence_semantics"] = check.get("evidence_semantics", "unavailable_status_review_fallback")
        result.append(normalized)
    return result


def audit_case_ids(scored: list[dict]) -> dict[str, list[str]]:
    return {
        "wrong_au_row_accepts": [r["case_id"] for r in scored if r["prediction"]["decision"] == "matched"
            and r["gold_decision"] == "matched" and r["gold_row_keys"]
            and r["prediction"].get("au_row_key") not in r["gold_row_keys"]],
        "false_accept_known_negative": [r["case_id"] for r in scored if r["gold_decision"] == "unmatched"
            and r["prediction"]["decision"] == "matched"],
        "gold_review_accepts": [r["case_id"] for r in scored if r["gold_decision"] == "review"
            and r["prediction"]["decision"] == "matched"],
        "false_unmatched_deletions": [r["case_id"] for r in scored if r["gold_decision"] == "matched"
            and r["prediction"]["decision"] == "unmatched"],
        "matched_deferred_to_review": [r["case_id"] for r in scored if r["gold_decision"] == "matched"
            and r["prediction"]["decision"] == "review"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, default=INPUT_ROOT)
    ap.add_argument("--uploaded-input-dir", type=Path, default=UPLOAD_ROOT)
    ap.add_argument("--artifacts-dir", type=Path, required=True)
    ap.add_argument("--source-inputs", type=Path, default=SOURCE_INPUTS)
    ap.add_argument("--tasks", type=Path, default=TASKS)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--cpu-predictions", type=Path, default=CPU_PREDICTIONS)
    ap.add_argument("--dossiers-dir", type=Path, default=DOSSIERS)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"Refusing existing evaluation directory: {args.output}")

    cases, info = verify_v9_inputs(args.input_dir, args.source_inputs, args.dossiers_dir,
                                   args.uploaded_input_dir)
    rows_by_mode, run_manifest, provenance, run_sha = validate_v9_artifacts(cases, args.input_dir, info, args.artifacts_dir)
    metrics = legacy()
    task_sha, cpu_sha = sha256(args.tasks), sha256(args.cpu_predictions)
    tasks_full = read_jsonl(args.tasks)
    tasks = metrics.select_tasks(cases, tasks_full)
    tasks_full_ids = {t["case_id"] for t in tasks_full}
    task_by_id = {t["case_id"]: t for t in tasks_full}
    for case, task in zip(cases, tasks, strict=True):
        if case.get("dossier_id") != task.get("dossier_id") or case.get("split") != task.get("split"):
            raise ValueError(f"Frozen input/task identity differs: {case['case_id']}")
        if [x.get("row_key") for x in case["au"]["sku_rows"]] != [x.get("row_key") for x in task.get("au_candidates", [])]:
            raise ValueError(f"Full ordered AU pool differs from task source: {case['case_id']}")
    cpu_full = read_jsonl(args.cpu_predictions)
    if {x.get("case_id") for x in cpu_full} != tasks_full_ids or len(cpu_full) != len(tasks_full):
        raise ValueError("CPU baseline does not cover the complete task source")
    cpu = metrics.adapt_cpu_predictions(cpu_full, cases, task_by_id)

    # Label access is intentionally last: source, 14 input rows, all 28 raw mode rows,
    # their hashes, runtime and the complete metric comparison inputs are already fixed.
    labels_sha = sha256(args.labels)
    gold = metrics.gold_join(tasks_full, args.labels, {c["case_id"] for c in cases})
    args.output.mkdir(parents=True, exist_ok=False)
    results: dict[str, Any] = {}
    scored_by_name: dict[str, list[dict]] = {}
    for mode in ("strict", "simple"):
        raw_records = as_scoring_rows(cases, rows_by_mode[mode], mode)
        raw_scored, raw_metrics = metrics.score_rows(tasks, gold, raw_records)
        guard_records = [{**r, "parsed": metrics.constrained_prediction(t, r)}
            for t, r in zip(tasks, raw_records, strict=True)]
        guard_scored, guard_metrics = metrics.score_rows(tasks, gold, guard_records)
        status = Counter(r["status"] for r in rows_by_mode[mode])
        tech = {"case_count": len(cases), "status_counts": dict(status),
            "valid_model_output_count": status["ok"],
            "generation_completed_count": status["ok"] + status["invalid_output"],
            "invalid_or_unavailable_count": len(cases) - status["ok"],
            "review_fallback_count": len(cases) - status["ok"],
            "inputrefs_are_entailment_evidence": False if mode == "simple" else None}
        results[mode] = {"technical": tech,
            "raw_model_alone_with_review_fallback": {"overall": raw_metrics, **metrics.strata_metrics(tasks, raw_scored)},
            "conservative_fixed_pool_guard": {"overall": guard_metrics, **metrics.strata_metrics(tasks, guard_scored)},
            "audit_case_ids": {"raw": audit_case_ids(raw_scored), "guard": audit_case_ids(guard_scored)},
            "opposite_lace_accepted_cases": {"raw": metrics.opposite_lace_accepts(tasks, raw_scored),
                "guard": metrics.opposite_lace_accepts(tasks, guard_scored)}}
        scored_by_name[mode] = raw_scored
        for suffix, scored in (("raw", raw_scored), ("guard", guard_scored)):
            with (args.output / f"predictions-scored-{mode}-{suffix}.jsonl").open("x", encoding="utf-8") as f:
                for item, case in zip(scored, cases, strict=True):
                    f.write(json.dumps({**item, "input_record": case}, ensure_ascii=False) + "\n")

    cpu_scored, cpu_metrics = metrics.score_rows(tasks, gold, cpu)
    results["cpu_baseline"] = {"overall": cpu_metrics, **metrics.strata_metrics(tasks, cpu_scored),
        "audit_case_ids": audit_case_ids(cpu_scored),
        "opposite_lace_accepted_cases": metrics.opposite_lace_accepts(tasks, cpu_scored)}
    with (args.output / "predictions-scored-cpu.jsonl").open("x", encoding="utf-8") as f:
        for item in cpu_scored:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    category_counts = Counter(c.get("source_category", "unknown") for c in cases)
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "case_count": 14,
        "sample_class": "same_14_case_smoke_as_v8_not_full_trial", "sample_category_counts": dict(category_counts),
        "model_configuration": MODEL, "prefill": {"method": info["config"].get("prefill_method"),
            "chunk_size": info["config"].get("prefill_chunk_size")},
        "gpu_run_status": "complete", "mode_metrics": results,
        "label_source": "machine labels; human unverified", "test_status": "reused development labels; not a fresh holdout",
        "simple_mode_limit": "Host inputrefs map a model-selected alias to a row key/SKU only. They are not model citations, entailment evidence, or production proof.",
        "runtime_scope": "Kaggle diagnostic only; no GCP speed/cost claim",
        "input_sha256": info["input_sha256"], "source_cases_sha256": info["source_cases_sha256"],
        "input_manifest_sha256": info["manifest_sha256"], "config_sha256": info["config_sha256"],
        "runner_sha256": provenance["runner_sha256"], "prompt_sha256": provenance["prompt_sha256"],
        "artifact_sha256": provenance["artifact_inventory"], "run_manifest_sha256": run_sha,
        "cpu_predictions_sha256": cpu_sha, "tasks_sha256": task_sha, "labels_sha256": labels_sha,
        "runtime": provenance["runtime"], "summary": provenance["summary"]}
    with (args.output / "metrics.csv").open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "mode", "guard", "stratum", "metric", "value"])
        writer.writeheader()
        for model_name, obj in results.items():
            modes = [("raw", obj["raw_model_alone_with_review_fallback"]),
                ("guard", obj["conservative_fixed_pool_guard"])] if model_name in {"strict", "simple"} else [("cpu", obj)]
            for mode, data in modes:
                for metric, value in data["overall"].items():
                    if metric != "confusion_matrix": writer.writerow({"model": model_name, "mode": mode,
                        "guard": mode == "guard", "stratum": "all", "metric": metric, "value": value})
                for bucket in ("strata", "per_dossier"):
                    for stratum, values in data.get(bucket, {}).items():
                        for metric, value in values.items():
                            if metric != "confusion_matrix": writer.writerow({"model": model_name, "mode": mode,
                                "guard": mode == "guard", "stratum": f"{bucket}:{stratum}", "metric": metric, "value": value})
                for metric, value in data.get("dossier_macro", {}).items():
                    writer.writerow({"model": model_name, "mode": mode, "guard": mode == "guard",
                        "stratum": "dossier_macro", "metric": metric, "value": value})
    if (sha256(args.tasks) != task_sha or sha256(args.cpu_predictions) != cpu_sha
            or sha256(args.labels) != labels_sha or sha256(args.artifacts_dir / "run-manifest.json") != run_sha):
        raise ValueError("Frozen label/task/CPU/runner artifacts changed during scoring")
    with (args.output / "metrics.json").open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2); f.write("\n")
    lines = ["Qwen3.5-4B NF4 v9: 14-case diagnostic smoke; same sample as v8, not the 196-case trial.",
        "Strict mode requires model-emitted exact evidence IDs. Simple mode records host input references only; those are not model evidence or production proof.",
        "Labels are reused machine annotations, human unverified; this is a development comparison, not a fresh holdout."]
    for mode in ("strict", "simple"):
        status = results[mode]["technical"]["status_counts"]
        lines.append(f"{mode}: status={status}; raw metrics={results[mode]['raw_model_alone_with_review_fallback']['overall']}; guard metrics={results[mode]['conservative_fixed_pool_guard']['overall']}")
        lines.append(f"{mode} audits: {json.dumps(results[mode]['audit_case_ids'], ensure_ascii=False)}; opposite-lace={results[mode]['opposite_lace_accepted_cases']}")
    lines.append(f"CPU same-14 diagnostic: {results['cpu_baseline']['overall']}")
    lines.append("No GCP speed or cost estimate is made.")
    (args.output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    output_files = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
        for p in sorted(args.output.iterdir()) if p.is_file()}
    with (args.output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump({"created_at_utc": datetime.now(timezone.utc).isoformat(),
            "evaluator_sha256": sha256(Path(__file__)), "input_sha256": info["input_sha256"],
            "source_cases_sha256": info["source_cases_sha256"], "config_sha256": info["config_sha256"],
            "runner_sha256": provenance["runner_sha256"], "run_manifest_sha256": run_sha,
            "tasks_sha256": task_sha, "cpu_predictions_sha256": cpu_sha, "labels_sha256": labels_sha,
            "outputs": output_files}, f, ensure_ascii=False, indent=2); f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
