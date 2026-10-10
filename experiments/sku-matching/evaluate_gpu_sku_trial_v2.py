#!/usr/bin/env python3
"""Verify and locally evaluate the frozen label-blind GPU SKU smoke artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INPUT_ROOT = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v8/dataset-upload"
SOURCE_INPUTS = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/inputs.jsonl"
TASKS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10/tasks.jsonl"
LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
CPU_PREDICTIONS = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10/hybrid/predictions.jsonl"
DOSSIERS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3/dossiers"
MODEL = {"name": "Qwen/Qwen3.5-4B", "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "load": "nf4"}
FROZEN_INPUT_SHA256 = "6e2a3057dca06e50e1c0c0f421fb9ce234e6964b2f47f668661e664abe1af23d"
FROZEN_CONFIG_SHA256 = "447136ea832e9ba9425ea7e916e64a5a3c6b640f006d6226f23e624a3d89e4e0"
FROZEN_SOURCE_CASES_SHA256 = "a91fca45c574f4d531eca2dc450e81a9adc44fa38c934955109831cf3147e18f"
FROZEN_INPUT_MANIFEST_SHA256 = "adcd042c8acd595bc540565542d46cbb244cdae16f9d8d96ecdbc13da63a9188"
FROZEN_RUNNER_SHA256 = "c01220ed9fbb84b6b8ad40b6a1563f89ad873f868eb664b1e04731ffc401e11e"
STATUSES = {"ok", "invalid_output", "invalid_input_over_limit", "error", "oom"}
DECISIONS = {"matched", "unmatched", "review"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{n}: expected JSON object")
                result.append(row)
    return result


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ids(rows: list[dict], source: str) -> set[str]:
    vals = [x.get("case_id") for x in rows]
    if any(not isinstance(x, str) or not x for x in vals) or len(vals) != len(set(vals)):
        raise ValueError(f"{source} has missing or duplicate case IDs")
    return set(vals)


def model_slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def legacy():
    """Reuse only metric/guard functions; v2 input and outputs are verified here."""
    path = HERE / "evaluate_gpu_sku_trial.py"
    spec = importlib.util.spec_from_file_location("gpu_sku_trial_metrics", path)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load frozen GPU metric implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_json_path(obj: Any, path: str) -> Any:
    if not path.startswith("$."):
        raise ValueError(f"Unsupported source JSON path: {path}")
    cur = obj
    for part in path[2:].split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ValueError(f"Unresolvable source JSON path: {path}")
        cur = cur[part]
    return cur


def _source_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def _raw_title(side: str, source_ref: dict[str, Any], dossier: dict[str, Any]) -> str:
    product_key = "au_product" if side == "au" else "rakuten_product"
    product = dossier[product_key]
    expected = product.get("title_evidence_raw", {}).get("text")
    if not isinstance(expected, str) or not expected:
        raise ValueError(f"Missing raw {side} title evidence")
    source = product.get("source", {})
    raw_path = _source_path(str(source_ref.get("raw_file", "")))
    if not raw_path.is_file() or sha256(raw_path) != source_ref.get("raw_sha256"):
        raise ValueError(f"Raw {side} title source missing or hash mismatch: {raw_path}")
    if (source_ref.get("raw_sha256") != source.get("sha256")
            or _source_path(str(source_ref.get("raw_file", ""))) != _source_path(str(source.get("raw_file", "")))):
        raise ValueError(f"Raw {side} title reference differs from frozen dossier")
    raw_bytes = raw_path.read_bytes()
    if side == "au":
        value = _resolve_json_path(json.loads(raw_bytes), str(source_ref.get("raw_json_path", "")))
    else:
        if str(source_ref.get("use")) != "title_evidence_raw":
            raise ValueError("Rakuten title source is not explicitly marked title_evidence_raw")
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import fetch_rakuten
        page, _ = fetch_rakuten.parse_page({"url": str(source_ref.get("page_url") or ""),
            "requested_url": str(source_ref.get("page_url") or ""), "retrieved_at_utc": "",
            "status": 200, "raw_file": str(raw_path), "content_type": "text/html"}, raw_bytes)
        value = page.get("title")
    if value != expected or source_ref.get("quote_verbatim") is not True:
        raise ValueError(f"Raw {side} title leaf does not match frozen raw-title evidence")
    return expected


def verify_source_bundle(inputs: Path, bundle: Path, source_inputs: Path, dossiers_dir: Path) -> tuple[list[dict], list[dict], dict]:
    """Verify label-blind evidence IDs against input, dossier and hash-checked source leaves."""
    manifest = read_json(bundle / "manifest.json")
    if manifest.get("labels_read") is not False or manifest.get("label_blind") is not True:
        raise ValueError("Input manifest does not certify label-blind preparation")
    config_sha = sha256(bundle / "config.json")
    inputs_sha = sha256(inputs)
    source_cases_path = bundle / "source-cases.jsonl"
    source_cases_sha = sha256(source_cases_path)
    if (manifest.get("config_sha256") != config_sha or manifest.get("inputs_sha256") != inputs_sha
            or manifest.get("source_cases_sha256") != source_cases_sha
            or manifest.get("case_count") != len(read_jsonl(inputs))
            or inputs_sha != FROZEN_INPUT_SHA256 or config_sha != FROZEN_CONFIG_SHA256
            or source_cases_sha != FROZEN_SOURCE_CASES_SHA256
            or sha256(bundle / "manifest.json") != FROZEN_INPUT_MANIFEST_SHA256):
        raise ValueError("Input/config/source-cases digest or count mismatch")
    config = read_json(bundle / "config.json")
    if (config.get("models") != [MODEL] or config.get("max_input_tokens") != 12000
            or config.get("max_new_tokens") != 256 or config.get("attention_implementation") != "sdpa"
            or config.get("logits_to_keep") != 1 or config.get("enable_thinking") is not False
            or config.get("reason_char_range_japanese") != [1, 80] or config.get("runner_version") != "v8"):
        raise ValueError("Config must pin the approved 4B NF4 smoke settings")
    if manifest.get("source_sha256", {}).get("v2_inputs") != sha256(source_inputs):
        raise ValueError("Frozen v2 source inputs do not match source manifest")
    products_path = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v10/products.jsonl"
    products_sha = manifest.get("source_sha256", {}).get("v10_products")
    if not products_path.is_file() or not products_sha or sha256(products_path) != products_sha:
        raise ValueError("Frozen v10 product source file hash mismatch")

    cases = read_jsonl(inputs)
    source_cases = read_jsonl(source_cases_path)
    if ids(cases, "inputs") != ids(source_cases, "source cases"):
        raise ValueError("Input/source-case ID coverage differs")
    old_cases = read_jsonl(source_inputs)
    old_by_id = {x["case_id"]: x for x in old_cases}
    source_by_id = {x["case_id"]: x for x in source_cases}
    expected_digests = manifest.get("source_sha256", {}).get("v3_dossiers", {})
    if not isinstance(expected_digests, dict):
        raise ValueError("Manifest lacks per-case v3 dossier SHA-256 values")
    raw_hashes: dict[Path, str] = {}
    checked_dossier_descriptions: set[str] = set()
    for case in cases:
        cid = case["case_id"]
        if cid not in old_by_id or cid not in source_by_id:
            raise ValueError(f"Case outside frozen source cohort: {cid}")
        src = source_by_id[cid]
        old = old_by_id[cid]
        if (case.get("rakuten", {}).get("sku") != old.get("rakuten", {}).get("sku")
                or src.get("rakuten_selected_sku") != case.get("rakuten", {}).get("sku")):
            raise ValueError(f"Selected Rakuten SKU differs from frozen source for {cid}")
        dossier_id = src.get("dossier_id")
        dossier_path = dossiers_dir / f"{dossier_id}.json"
        expected_dossier_sha = expected_digests.get(cid)
        if (not dossier_path.is_file() or not expected_dossier_sha
                or sha256(dossier_path) != expected_dossier_sha):
            raise ValueError(f"Dossier is missing or does not match manifest for {cid}")
        dossier = read_json(dossier_path)
        if dossier.get("dossier_id") != dossier_id or not src.get("description_source_complete"):
            raise ValueError(f"Dossier identity/description completeness mismatch for {cid}")
        if (src.get("rakuten_title_raw") != dossier.get("rakuten_product", {}).get("title_evidence_raw", {}).get("text")
                or src.get("au_title_raw") != dossier.get("au_product", {}).get("title_evidence_raw", {}).get("text")):
            raise ValueError(f"Frozen raw titles differ from dossier evidence for {cid}")
        if dossier_id not in checked_dossier_descriptions:
            # Re-run the deterministic extraction over the hash-verified source files.
            # Extracted descriptions remain marked as derived text, never raw HTML quotations.
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import build_luna_annotation_inputs as source_builder
            rk_desc_ref = dossier["rakuten_product"]["description"]["source"]
            au_desc_ref = dossier["au_product"]["description"]["source"]
            rk_raw = _source_path(str(rk_desc_ref["raw_file"]))
            au_raw = _source_path(str(au_desc_ref["raw_file"]))
            rk_extracted = source_builder.extract_rakuten_description(rk_raw, rk_desc_ref["sha256"])
            au_extracted = source_builder.extract_au_description(au_raw, au_desc_ref["sha256"])
            if (rk_extracted.get("individual_description_excerpt")
                    != dossier["rakuten_product"]["description"].get("individual_description_excerpt")
                    or au_extracted.get("blocks") != dossier["au_product"]["description"].get("blocks")):
                raise ValueError(f"Dossier derived descriptions do not re-resolve from raw source for {dossier_id}")
            checked_dossier_descriptions.add(dossier_id)

        au_rows = case.get("au", {}).get("sku_rows")
        source_rows = src.get("au_rows")
        if not isinstance(au_rows, list) or not au_rows or not isinstance(source_rows, list):
            raise ValueError(f"Missing fixed AU SKU rows for {cid}")
        if (len(au_rows) != len(source_rows)
                or len({x.get("row_key") for x in au_rows}) != len(au_rows)
                or len({x.get("alias") for x in au_rows}) != len(au_rows)
                or any((a.get("alias") != f"a{i:03d}" or a.get("row_key") != s.get("row_key")
                        or a.get("sku") != s.get("sku"))
                       for i, (a, s) in enumerate(zip(au_rows, source_rows, strict=True)))):
            raise ValueError(f"AU alias mapping is not bijective to the full ordered pool for {cid}")
        reg = case.get("evidence_registry")
        source_reg = src.get("source_evidence_registry")
        if not isinstance(reg, list) or reg != source_reg:
            raise ValueError(f"Prompt evidence registry differs from separately frozen source registry for {cid}")
        reg_by_id = {e.get("id"): e for e in reg if isinstance(e, dict)}
        if len(reg_by_id) != len(reg):
            raise ValueError(f"Evidence IDs missing or duplicated for {cid}")
        record_list = manifest.get("records", [])
        manifest_records = {x.get("case_id"): x for x in record_list if isinstance(x, dict)}
        if len(manifest_records) != len(record_list) or set(manifest_records) != ids(cases, "inputs"):
            raise ValueError("Manifest records have missing or duplicate case IDs")
        declared = manifest_records.get(cid)
        if (not declared or declared.get("dossier_id") != dossier_id
                or declared.get("au_rows") != len(au_rows) or declared.get("evidence_count") != len(reg)):
            raise ValueError(f"Manifest row/evidence counts mismatch for {cid}")
        for side in ("au", "rakuten"):
            records = [e for e in reg if e.get("side") == side]
            texts = case.get("source_texts", {}).get(side)
            if not isinstance(texts, list) or texts != [e.get("quote") for e in records]:
                raise ValueError(f"{side} prompt source text differs from registry for {cid}")
        if (reg_by_id.get("rt", {}).get("quote") != src["rakuten_title_raw"]
                or reg_by_id.get("rt", {}).get("side") != "rakuten"
                or reg_by_id.get("rt", {}).get("field") != "title"
                or reg_by_id.get("rt", {}).get("scope") != "product_title"
                or reg_by_id.get("rt", {}).get("source_ref", {}).get("use") != "title_evidence_raw"):
            raise ValueError(f"Rakuten raw-title evidence missing for {cid}")
        if (reg_by_id.get("at", {}).get("quote") != src["au_title_raw"]
                or reg_by_id.get("at", {}).get("side") != "au"
                or reg_by_id.get("at", {}).get("field") != "title"
                or reg_by_id.get("at", {}).get("scope") != "fixed_page_title"):
            raise ValueError(f"AU raw title evidence missing for {cid}")
        rs_ref = reg_by_id.get("rs", {}).get("source_ref", {})
        if (reg_by_id.get("rs", {}).get("quote") != case["rakuten"]["sku"]
                or reg_by_id.get("rs", {}).get("side") != "rakuten"
                or reg_by_id.get("rs", {}).get("field") != "selected_sku"
                or reg_by_id.get("rs", {}).get("scope") != "selected_option"
                or rs_ref.get("derived") is not True
                or rs_ref.get("input_file_sha256") != sha256(source_inputs)
                or rs_ref.get("input_case_id") != cid):
            raise ValueError(f"Selected SKU evidence mismatch for {cid}")
        _raw_title("rakuten", reg_by_id["rt"]["source_ref"], dossier)
        _raw_title("au", reg_by_id["at"]["source_ref"], dossier)

        excerpt = dossier.get("rakuten_product", {}).get("description", {}).get("individual_description_excerpt", "")
        rd_lines = [line for line in str(excerpt).splitlines() if line.strip()]
        au_blocks = dossier.get("au_product", {}).get("description", {}).get("blocks", [])
        expected_ids = {"rt", "at", "rs"} | {f"rd{i:04d}" for i in range(1, len(rd_lines)+1)} | {f"ad{i:04d}" for i in range(1, len(au_blocks)+1)} | {f"a{i:03d}" for i in range(len(au_rows))}
        if set(reg_by_id) != expected_ids:
            raise ValueError(f"Evidence registry IDs do not match full source scopes for {cid}")
        for i, line in enumerate(rd_lines, 1):
            e = reg_by_id[f"rd{i:04d}"]
            ref = e.get("source_ref", {})
            desc_ref = dossier["rakuten_product"]["description"]["source"]
            if (e.get("quote") != line or e.get("scope") != "individual_description_excerpt"
                    or ref.get("source_line") != i or ref.get("raw_sha256") != desc_ref.get("sha256")
                    or ref.get("raw_file") != desc_ref.get("raw_file")
                    or "not raw HTML" not in str(ref.get("transformation", ""))):
                raise ValueError(f"Rakuten derived description line/source provenance mismatch for {cid}:{i}")
        for i, block in enumerate(au_blocks, 1):
            e = reg_by_id[f"ad{i:04d}"]
            ref = e.get("source_ref", {})
            desc = dossier["au_product"]["description"]
            if (e.get("quote") != block.get("text") or e.get("scope") != block.get("scope")
                    or ref.get("source_line") != block.get("source_line")
                    or ref.get("block_index") != i-1 or ref.get("raw_json_path") != block.get("source_field")
                    or ref.get("raw_sha256") != desc.get("source", {}).get("sha256")
                    or ref.get("raw_file") != desc.get("source", {}).get("raw_file")
                    or "not raw HTML" not in str(ref.get("transformation", ""))):
                raise ValueError(f"AU description block/source provenance mismatch for {cid}:{i}")
        for i, row in enumerate(au_rows):
            e = reg_by_id[f"a{i:03d}"]
            ref = e.get("source_ref", {})
            if (e.get("quote") != row.get("sku") or e.get("row_key") != row.get("row_key")
                    or e.get("side") != "au" or e.get("field") != "selected_option"
                    or e.get("scope") != "sku_row" or ref.get("derived") is not True
                    or ref.get("input_file_sha256") != sha256(source_inputs)
                    or ref.get("input_case_id") != cid or ref.get("row_key") != row.get("row_key")):
                raise ValueError(f"AU row evidence alias/source mismatch for {cid}:{i}")
        # Verify every original capture hash, but distinguish source capture bytes from extracted text quotes.
        for e in reg:
            ref = e.get("source_ref", {})
            raw = ref.get("raw_file")
            raw_sha = ref.get("raw_sha256")
            if raw:
                path = _source_path(str(raw))
                if not path.is_file() or not isinstance(raw_sha, str) or sha256(path) != raw_sha:
                    raise ValueError(f"Evidence capture missing or hash mismatch for {cid}: {path}")
                raw_hashes[path] = raw_sha
    return cases, source_cases, {"manifest": manifest, "manifest_sha256": sha256(bundle / "manifest.json"),
        "config": config, "config_sha256": config_sha,
        "source_cases_sha256": source_cases_sha, "raw_source_file_count": len(raw_hashes),
        "source_input_sha256": sha256(source_inputs), "dossier_count": len(expected_digests)}


def validate_actual_nf4(summary: dict[str, Any]) -> None:
    model_pin = summary.get("model")
    if model_pin is None and all(k in summary for k in ("revision", "load")):
        model_pin = {"name": summary.get("model_name"), "revision": summary.get("revision"), "load": summary.get("load")}
    expected = {"model": MODEL,
        "config_name": MODEL["name"], "config_revision": MODEL["revision"],
        "runtime_quantization": "nf4", "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True}
    if model_pin != MODEL or any(summary.get(k) != v for k, v in expected.items() if k != "model"):
        raise ValueError("Runtime model pin or NF4 settings mismatch")
    if (summary.get("nf4_linear4bit_module_count", 0) <= 0
            or summary.get("nf4_quantized_linear4bit_module_count") != summary.get("nf4_linear4bit_module_count")
            or summary.get("base_logical_parameter_count", 0) <= 0
            or summary.get("nf4_logical_parameter_count", 0) <= 0
            or not 0 < summary.get("nf4_coverage_ratio", 0) <= 1):
        raise ValueError("Runtime did not confirm complete actual NF4 Linear4bit coverage")
    config = summary.get("hf_quantization_config")
    if not isinstance(config, dict) or any(config.get(k) != v for k, v in {
            "load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True}.items()):
        raise ValueError("Transformers config does not confirm NF4 double quantization")
    devices = summary.get("parameter_devices")
    if not isinstance(devices, list) or not devices or any(not str(x).startswith("cuda:") for x in devices):
        raise ValueError("Model parameters are not entirely on CUDA devices")
    if summary.get("is_loaded_in_4bit") is not True:
        raise ValueError("Transformers did not certify 4-bit model load")
    if summary.get("attention_implementation") != "sdpa" or summary.get("logits_to_keep_supported") is not True:
        raise ValueError("Runtime attention/logits configuration differs from frozen config")
    device_map = summary.get("hf_device_map")
    if not isinstance(device_map, dict) or not device_map or any(
            str(value).casefold() in {"cpu", "disk"} or str(value).startswith("cpu")
            for value in device_map.values()):
        raise ValueError("HF device map is absent or includes CPU/disk offload")


def validate_prediction_record(case: dict, record: dict) -> dict:
    """Reparse raw assistant JSON and independently resolve evidence/alias IDs."""
    if record.get("status") not in STATUSES:
        raise ValueError(f"Unknown GPU row status for {case['case_id']}: {record.get('status')}")
    for key in ("case_id",):
        if record.get(key) != case.get(key):
            raise ValueError(f"GPU prediction {key} mismatch")
    if record.get("status") != "ok":
        return {"status": record["status"], "prediction": {"decision": "review", "au_row_key": None}}
    raw = record.get("raw_output")
    if not isinstance(raw, str):
        raise ValueError(f"Successful row lacks raw model JSON for {case['case_id']}")
    p = json.loads(raw)
    if not isinstance(p, dict) or set(p) != {"decision", "au_row_alias", "evidence_ids", "reason"}:
        raise ValueError(f"Raw assistant schema invalid for {case['case_id']}")
    decision, alias, evidence_ids, reason = p["decision"], p["au_row_alias"], p["evidence_ids"], p["reason"]
    if not isinstance(decision, str) or decision not in DECISIONS or not isinstance(reason, str) or not reason.strip() or len(reason) > 80:
        raise ValueError(f"Raw decision/reason invalid for {case['case_id']}")
    pool = case["au"]["sku_rows"]
    alias_map = {x["alias"]: x["row_key"] for x in pool}
    if (len(alias_map) != len(pool) or (decision == "matched" and (not isinstance(alias, str) or alias not in alias_map))
            or (decision != "matched" and alias is not None)):
        raise ValueError(f"Raw AU alias invalid for {case['case_id']}")
    if (not isinstance(evidence_ids, list) or not evidence_ids or len(evidence_ids) > 4
            or any(not isinstance(x, str) for x in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids)):
        raise ValueError(f"Raw evidence IDs invalid for {case['case_id']}")
    registry = {e["id"]: e for e in case["evidence_registry"]}
    if len(registry) != len(case["evidence_registry"]) or any(i not in registry for i in evidence_ids):
        raise ValueError(f"Raw evidence ID does not resolve for {case['case_id']}")
    expected_evidence = []
    for evidence_id in evidence_ids:
        source = registry[evidence_id]
        expected = {k: source[k] for k in ("side", "field", "scope", "source_ref", "quote", "row_key") if k in source}
        expected["evidence_id"] = evidence_id
        if source["quote"] not in case["source_texts"].get(source["side"], []):
            raise ValueError(f"Evidence ID quote absent from current prompt scope for {case['case_id']}")
        if "row_key" in expected and expected["row_key"] not in alias_map.values():
            raise ValueError(f"Evidence ID row key is outside full fixed pool for {case['case_id']}")
        expected_evidence.append(expected)
    sides = {e["side"] for e in expected_evidence}
    if decision in {"matched", "unmatched"} and not {"rakuten", "au"}.issubset(sides):
        raise ValueError(f"Decisive answer lacks evidence from both sides for {case['case_id']}")
    if decision in {"matched", "unmatched"}:
        forbidden_scope_terms = ("series", "sibling", "navigation", "related", "recommendation", "other_product", "other_page")
        if any(any(term in str(e.get("scope", "")).casefold() for term in forbidden_scope_terms)
               for e in expected_evidence):
            raise ValueError(f"Decisive answer cites non-authoritative page context for {case['case_id']}")
        if "rs" not in evidence_ids:
            raise ValueError(f"Decisive answer must cite selected Rakuten SKU evidence for {case['case_id']}")
        if decision == "matched":
            selected_idx = next(i for i, row in enumerate(pool) if row["alias"] == alias)
            selected_evidence_id = f"a{selected_idx:03d}"
            if selected_evidence_id not in evidence_ids or registry[selected_evidence_id].get("row_key") != alias_map[alias]:
                raise ValueError(f"Matched answer must cite the exact selected AU row evidence for {case['case_id']}")
        elif not any(i.startswith("a") and registry[i].get("side") == "au"
                     and registry[i].get("row_key") in alias_map.values() for i in evidence_ids):
            raise ValueError(f"Unmatched answer must cite an explicit AU candidate-row conflict for {case['case_id']}")
    expected_parsed = {"decision": decision, "au_row_key": alias_map[alias] if decision == "matched" else None,
        "reason": reason, "evidence": expected_evidence}
    if record.get("parsed") != expected_parsed or record.get("resolved_evidence") != expected_evidence:
        raise ValueError(f"Host-resolved prediction/evidence differs from independent resolver for {case['case_id']}")
    return {"status": "ok", "prediction": {"decision": decision, "au_row_key": expected_parsed["au_row_key"]}}


def validate_artifacts(cases: list[dict], input_dir: Path, artifacts: Path) -> tuple[list[dict], dict, dict, str]:
    config = read_json(input_dir / "config.json")
    source_manifest = read_json(input_dir / "manifest.json")
    input_sha, config_sha = sha256(input_dir / "inputs.jsonl"), sha256(input_dir / "config.json")
    if (config.get("models") != [MODEL] or source_manifest.get("config_sha256") != config_sha
            or config.get("max_input_tokens") != 12000 or config.get("max_new_tokens") != 256
            or config.get("attention_implementation") != "sdpa" or config.get("logits_to_keep") != 1
            or config.get("enable_thinking") is not False):
        raise ValueError("Uploaded config does not match pinned 4B NF4 model/config hash")
    run_manifest_path = artifacts / "run-manifest.json"
    run_manifest = read_json(run_manifest_path)
    run_sha = sha256(run_manifest_path)
    runner_path = input_dir / "kaggle_gpu_sku_runner_v2.py"
    expected_runner_sha = sha256(runner_path)
    if (run_manifest.get("schema_version") != "sku-gpu-v8-run-v1"
            or run_manifest.get("model") != MODEL or run_manifest.get("input_sha256") != input_sha
            or run_manifest.get("config_sha256") != config_sha or run_manifest.get("input_count") != len(cases)
            or run_manifest.get("label_blind") is not True
            or run_manifest.get("labels_used") is not False
            or run_manifest.get("input_manifest_sha256") != sha256(input_dir / "manifest.json")
            or run_manifest.get("runner_sha256") != expected_runner_sha
            or expected_runner_sha != FROZEN_RUNNER_SHA256
            or expected_runner_sha != sha256(HERE / "kaggle_gpu_sku_runner_v2.py")):
        raise ValueError("Run manifest does not match frozen inputs/config/model")
    inventory = run_manifest.get("files_sha256")
    pred_name = "predictions-qwen3-5-4b-nf4-v8.jsonl"
    required = {"runtime.json", "summary.json", pred_name}
    if not isinstance(inventory, dict) or not required.issubset(inventory):
        raise ValueError("Run manifest artifact inventory incomplete")
    actual_files = {p.name for p in artifacts.iterdir() if p.is_file() and p.name != "run-manifest.json"}
    if actual_files != set(inventory):
        raise ValueError("Artifact directory has missing/unmanifested files")
    for name, expected in inventory.items():
        if not (artifacts / name).is_file() or sha256(artifacts / name) != expected:
            raise ValueError(f"Artifact SHA mismatch: {name}")
    runtime, summary = read_json(artifacts / "runtime.json"), read_json(artifacts / "summary.json")
    if (summary.get("inputs_sha256") != input_sha or summary.get("config_sha256") != config_sha
            or summary.get("labels_used") is not False or summary.get("label_blind") is not True
            or summary.get("partial") is not False or summary.get("status") != "complete"
            or summary.get("completed") != len(cases) or summary.get("input_count") != len(cases)
            or summary.get("input_manifest_sha256") != run_manifest.get("input_manifest_sha256")
            or summary.get("runner_sha256") != expected_runner_sha):
        raise ValueError("Run is partial/incomplete or input/config does not match")
    if run_manifest.get("status") != summary.get("status") or run_manifest.get("completed") != len(cases):
        raise ValueError("Run manifest terminal state differs from summary")
    validate_actual_nf4(summary)
    if not runtime.get("gpu_available") or runtime.get("cuda_device_count", 0) <= 0 or not runtime.get("devices"):
        raise ValueError("Runtime does not confirm an actual CUDA device")
    model = read_jsonl(artifacts / pred_name)
    if [r.get("case_id") for r in model] != [c["case_id"] for c in cases]:
        raise ValueError("Prediction rows do not match all frozen inputs in order")
    for case, record in zip(cases, model, strict=True):
        validate_prediction_record(case, record)
    status_counts = Counter(r["status"] for r in model)
    generated_count = status_counts["ok"] + status_counts["invalid_output"]
    expected_counts = {"inference_count": generated_count, "valid_prediction_count": status_counts["ok"],
        "invalid_prediction_count": status_counts["invalid_output"], "oom_count": status_counts["oom"]}
    if any(summary.get(k) != v or run_manifest.get(k) != v for k, v in expected_counts.items()):
        raise ValueError("Summary/run-manifest technical counts differ from prediction rows")
    prompt_pairs = [f"{r['case_id']}:{r['prompt_sha256']}" for r in model if isinstance(r.get("prompt_sha256"), str)]
    prompt_sha = hashlib.sha256("\n".join(prompt_pairs).encode("utf-8")).hexdigest()
    if (summary.get("prompt_sha256") != prompt_sha or run_manifest.get("prompt_sha256") != prompt_sha
            or any(r.get("status") == "ok" and not isinstance(r.get("prompt_sha256"), str) for r in model)
            or any(not re.fullmatch(r"[0-9a-f]{64}", x.split(":", 1)[1]) for x in prompt_pairs)):
        raise ValueError("Aggregate prompt hash does not match ordered per-record prompt hashes")
    if summary.get("prediction_file_sha256") != sha256(artifacts / pred_name):
        raise ValueError("Summary prediction file SHA does not match output")
    return model, run_manifest, {"config_sha256": config_sha, "runtime": runtime, "summary": summary,
        "source_manifest_sha256": sha256(input_dir / "manifest.json"), "input_sha256": input_sha,
        "prediction_filename": pred_name, "artifact_inventory": inventory,
        "runner_sha256": expected_runner_sha, "prompt_sha256": prompt_sha,
        "status_counts": dict(status_counts), "generation_count": generated_count}, run_sha


def latency_memory_stats(records: list[dict]) -> dict[str, Any]:
    stats = legacy().latency_stats(records)
    per_device: list[int] = []
    for row in records:
        values = row.get("peak_allocated_bytes_by_device")
        if isinstance(values, list):
            if len(per_device) < len(values):
                per_device.extend([0] * (len(values) - len(per_device)))
            for i, value in enumerate(values):
                if isinstance(value, (int, float)) and value >= 0:
                    per_device[i] = max(per_device[i], int(value))
    stats.pop("peak_vram_bytes_max", None)  # v8 records per-device CUDA peaks under a different field.
    stats["peak_allocated_bytes_by_device_max_over_run"] = per_device
    stats["peak_allocated_bytes_total_max_over_run"] = sum(per_device) if per_device else None
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", type=Path, default=INPUT_ROOT / "inputs.jsonl")
    ap.add_argument("--input-dir", type=Path, default=INPUT_ROOT)
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

    cases, source_cases, source_info = verify_source_bundle(args.inputs, args.input_dir, args.source_inputs, args.dossiers_dir)
    gpu_rows, run_manifest, provenance, run_sha = validate_artifacts(cases, args.input_dir, args.artifacts_dir)
    n = len(cases)
    if n != 196 and not 12 <= n <= 24:
        raise ValueError(f"Only the planned 12-24 row smoke or frozen 196-case sample is scoreable; got {n}")
    is_full = n == 196

    # Prediction bundle, source registry, all model/runtime/output hashes and exact case order are frozen before gold is opened.
    labels_sha_before = sha256(args.labels)
    task_sha = sha256(args.tasks)
    cpu_sha = sha256(args.cpu_predictions)
    tasks_full = read_jsonl(args.tasks)
    tasks_full_ids = ids(tasks_full, "full tasks")
    tasks = legacy().select_tasks(cases, tasks_full)
    task_by_id = {t["case_id"]: t for t in tasks_full}
    for case, task in zip(cases, tasks, strict=True):
        rows = case["au"]["sku_rows"]
        candidates = task.get("au_candidates", [])
        if [x["row_key"] for x in rows] != [x["row_key"] for x in candidates]:
            raise ValueError(f"Fixed AU pool differs from full task pool for {case['case_id']}")
        if case.get("dossier_id") != task.get("dossier_id") or case.get("split") != task.get("split"):
            raise ValueError(f"Task/input identity mismatch for {case['case_id']}")
    cpu_full = read_jsonl(args.cpu_predictions)
    if ids(cpu_full, "CPU predictions") != tasks_full_ids:
        raise ValueError("CPU baseline must cover exact full task ID set")
    cpu = legacy().adapt_cpu_predictions(cpu_full, cases, task_by_id)

    # Only now is machine gold opened. It must cover the complete task source, including cases outside a smoke subset.
    gold = legacy().gold_join(tasks_full, args.labels, ids(cases, "selected GPU inputs"))
    args.output.mkdir(parents=True, exist_ok=False)
    metrics = legacy()
    gpu_by_id = {r["case_id"]: r for r in gpu_rows}
    # Keep raw status for audit; normalize only OOM for the legacy status counter.
    scoring_rows = [{**r, "status": "error" if r["status"] == "oom" else r["status"]} for r in gpu_rows]
    scored, raw_metrics = metrics.score_rows(tasks, gold, scoring_rows)
    guarded_records = [{**gpu_by_id[t["case_id"]],
        "status": "error" if gpu_by_id[t["case_id"]]["status"] == "oom" else gpu_by_id[t["case_id"]]["status"],
        "parsed": metrics.constrained_prediction(t, gpu_by_id[t["case_id"]])}
        for t in tasks]
    guarded_scored, guarded_metrics = metrics.score_rows(tasks, gold, guarded_records)
    cpu_scored, cpu_metrics = metrics.score_rows(tasks, gold, cpu)
    gold_review_accepts = {
        "raw": [r["case_id"] for r in scored if r["gold_decision"] == "review" and r["prediction"]["decision"] == "matched"],
        "guard": [r["case_id"] for r in guarded_scored if r["gold_decision"] == "review" and r["prediction"]["decision"] == "matched"]}
    opposite_lace = {
        "raw": metrics.opposite_lace_accepts(tasks, scored),
        "guard": metrics.opposite_lace_accepts(tasks, guarded_scored)}
    # Technical success is reported independently from fallback accuracy/coverage.
    status_counts = Counter(r["status"] for r in gpu_rows)
    generation_count = status_counts["ok"] + status_counts["invalid_output"]
    validation_errors = Counter(r.get("validation_error") for r in gpu_rows if r["status"] == "invalid_output")
    oom_rows = [r for r in gpu_rows if r["status"] == "oom"]
    tech = {"case_count": n, "status_counts": dict(status_counts),
        "generation_completed_count": generation_count,
        "generation_completion_rate": generation_count / n if n else None,
        "parse_valid_prediction_count": status_counts["ok"],
        "parse_valid_rate": status_counts["ok"] / n if n else None,
        "invalid_output_count": status_counts["invalid_output"],
        "invalid_input_over_limit_count": status_counts["invalid_input_over_limit"],
        "oom_count": status_counts["oom"],
        "error_count": status_counts["error"],
        "invalid_output_validation_errors": dict(validation_errors),
        "oom_cases": [{"case_id": r["case_id"], "input_tokens": r.get("input_tokens")} for r in oom_rows],
        "conservative_review_fallback_rows": status_counts["invalid_output"] + status_counts["invalid_input_over_limit"] + status_counts["error"] + status_counts["oom"]}
    model_name = model_slug(MODEL["name"])
    all_metrics = {model_name: {"technical_success": tech,
        "raw_model_alone_with_review_fallback": {"overall": raw_metrics, **metrics.strata_metrics(tasks, scored)},
        "conservative_fixed_pool_guard": {"overall": guarded_metrics, **metrics.strata_metrics(tasks, guarded_scored)},
        "cpu_baseline": {"overall": cpu_metrics, **metrics.strata_metrics(tasks, cpu_scored)},
        "latency_tokens_memory": latency_memory_stats(gpu_rows)}}

    for mode, score_rows in (("raw", scored), ("guard", guarded_scored)):
        with (args.output / f"predictions-scored-{model_name}-{mode}.jsonl").open("x", encoding="utf-8") as f:
            for row, original_case in zip(score_rows, cases, strict=True):
                f.write(json.dumps({**row, "input_record": original_case}, ensure_ascii=False) + "\n")
    with (args.output / "predictions-scored-cpu.jsonl").open("x", encoding="utf-8") as f:
        for row in cpu_scored:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    csv_path = args.output / "metrics.csv"
    with csv_path.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "mode", "stratum", "metric", "value"]); writer.writeheader()
        for mode, data in (("raw_model_alone_with_review_fallback", {"overall": raw_metrics, **metrics.strata_metrics(tasks, scored)}),
                           ("conservative_fixed_pool_guard", {"overall": guarded_metrics, **metrics.strata_metrics(tasks, guarded_scored)}),
                           ("cpu_baseline", {"overall": cpu_metrics, **metrics.strata_metrics(tasks, cpu_scored)})):
            report_model = model_name if mode != "cpu_baseline" else "cpu_baseline"
            for metric, value in data["overall"].items():
                if metric != "confusion_matrix":
                    writer.writerow({"model": report_model, "mode": mode, "stratum": "all", "metric": metric, "value": value})
            for bucket in ("strata", "per_dossier"):
                for stratum, sm in data.get(bucket, {}).items():
                    for metric, value in sm.items():
                        if metric != "confusion_matrix":
                            writer.writerow({"model": report_model, "mode": mode, "stratum": f"{bucket}:{stratum}", "metric": metric, "value": value})
            for metric, value in data.get("dossier_macro", {}).items():
                writer.writerow({"model": report_model, "mode": mode, "stratum": "dossier_macro", "metric": metric, "value": value})

    if (sha256(args.labels) != labels_sha_before or sha256(args.tasks) != task_sha
            or sha256(args.cpu_predictions) != cpu_sha or sha256(args.artifacts_dir / "run-manifest.json") != run_sha):
        raise ValueError("Frozen labels/tasks/CPU/artifact manifest changed during evaluation")
    category_counts = Counter(c.get("source_category", "unknown") for c in cases)
    if "unknown" in category_counts:
        raise ValueError("Input sample lacks normalized source_category")
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "case_count": n,
        "sample_class": "full_frozen_196_case_sample" if is_full else "label_blind_smoke_subset_not_full_trial",
        "sample_category_counts": dict(category_counts), "model_configuration": MODEL,
        "gpu_run_status": "complete", "technical_success": tech,
        "label_source": "machine labels; human unverified",
        "test_status": "reused test-label cases; not a fresh holdout",
        "accuracy_interpretation": "No parsed-valid GPU predictions means raw model decision quality is not estimable; fallback metrics and the CPU comparator are smoke diagnostics only.",
        "metrics": all_metrics, "inputs_sha256": provenance["input_sha256"],
        "selected_sample_audit": {"gold_review_cases_accepted_as_matched": gold_review_accepts,
            "opposite_lace_accepted_cases_from_explicit_v10_attrs": opposite_lace},
        "input_manifest_sha256": source_info["manifest_sha256"],
        "source_cases_sha256": source_info["source_cases_sha256"],
        "source_provenance": source_info, "run_manifest_sha256": run_sha,
        "artifact_sha256": provenance["artifact_inventory"],
        "tasks_sha256": task_sha, "labels_sha256": labels_sha_before,
        "cpu_predictions_sha256": cpu_sha,
        "safety_checks": "selected-sample checks only; conservative fixed-pool gate is not a source-gate precision claim",
        "runtime_scope": "Kaggle only; no GCP speed/cost estimate"}
    write_json(args.output / "metrics.json", report)
    summary_text = [f"Qwen3.5-4B NF4 GPU SKU evaluation: {n} cases ({report['sample_class']})",
        f"Generation completed: {tech['generation_completed_count']}/{n} ({tech['generation_completion_rate']:.3f}); schema-valid predictions: {tech['parse_valid_prediction_count']}/{n} ({tech['parse_valid_rate']:.3f}); raw status counts {dict(status_counts)}.",
        f"Invalid output reasons: {dict(validation_errors)}. OOM cases and input tokens: {[(r['case_id'], r.get('input_tokens')) for r in oom_rows]}.",
        "No parsed-valid GPU predictions means decision quality cannot be estimated; the CPU comparison is a 14-case smoke diagnostic only.",
        "Invalid/error/over-limit rows remain in the denominator and fall back to review.",
        "Labels are machine annotated and human unverified; test labels were reused, so this is not a fresh holdout.",
        "Runtime, latency and memory describe Kaggle execution only; no GCP cost or speed model."]
    for mode, result in (("raw", raw_metrics), ("guard", guarded_metrics), ("CPU", cpu_metrics)):
        summary_text.append(f"{mode}: accepted precision incl. wrong rows={result['accepted_precision_including_wrong_rows']}; matched recall={result['matched_recall']}; automatic coverage={result['automatic_decision_coverage']}; false rejects={result['false_reject_matched_to_unmatched']}; review deferrals of matches={result['deferred_matched_to_review']}")
    (args.output / "report.txt").write_text("\n".join(summary_text)+"\n", encoding="utf-8")
    outputs = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in sorted(args.output.iterdir()) if p.is_file()}
    write_json(args.output / "manifest.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluator_sha256": sha256(Path(__file__)), "input_sha256": provenance["input_sha256"],
        "tasks_sha256": task_sha, "labels_sha256": labels_sha_before, "cpu_predictions_sha256": cpu_sha,
        "gpu_run_manifest_sha256": run_sha, "outputs": outputs})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
