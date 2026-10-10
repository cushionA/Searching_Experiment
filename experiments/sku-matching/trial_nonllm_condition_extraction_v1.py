#!/usr/bin/env python3
"""Run a frozen CPU GLiNER span-extraction trial on real Japanese SKU text.

The script reads only unannotated source text fields from the frozen input JSONL.
It stores raw extraction proposals and exact source spans; it never reads labels
or scores predictions against an answer set.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-novel-real-inputs-20261010-v2/inputs.jsonl"
DEFAULT_PACKETS = ROOT / ".lab-output/sku-gpu-condition-tasks-20261010-v2/model/packets.jsonl"
DEFAULT_MODEL = ROOT / ".deps/sku-gliner-extract-model"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-nonllm-model-trial-20261010-v2"
MODEL_ID = "fastino/gliner2.5-multi-v1"
REVISION = "cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3"
THRESHOLDS = (0.5, 0.7)  # fixed before inference; diagnostic views only
MAX_LEN = 4096

# Deliberately ask for narrow evidence spans. Presence is represented by explicit
# wording, never inferred from a product/category name or from absence of a span.
ENTITY_DESCRIPTIONS = {
    "lace_presence": "選択SKUにレースカーテンが含まれるかを明示する状態語だけ。あり、なし、付属、非付属などの原文スパン。レースカーテン等の商品種類名から推定しない。",
    "topboard": "商品の天板またはトップボードを指す原文の名称スパン。天板の有無や材質を推定しない。",
    "quantity": "選択SKUに含まれる数量、枚数、個数、組数またはセット数を明示する数値と単位の原文スパン。耐荷重、寸法、段数は含めない。",
    "tier": "選択SKUの段数、階段数または高さ調節段階数を明示する原文スパン。商品説明に列挙された別SKUの候補も原文どおり抽出し、選択値と推定しない。",
    "size": "商品の寸法またはサイズを表す数値と単位の原文スパン。選択SKUと別SKUの値が同じ説明文にあれば原文どおり抽出し、どのSKUの値か推定しない。",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            result.append(row)
    return result


def add_source(groups: dict[str, dict[str, Any]], text: Any, binding: dict[str, Any]) -> None:
    if not isinstance(text, str) or not text.strip():
        return
    item = groups.setdefault(text, {"text": text, "bindings": []})
    item["bindings"].append(binding)


def collect_inputs(rows: list[dict[str, Any]], packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect bounded title/axis/quote literals and retain every source binding."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        for side in ("au", "rakuten"):
            add_source(groups, row.get(side, {}).get("title_raw"), {
                "source_kind": "title", "case_id": case_id, "side": side,
                "source_field": f"{side}.title_raw",
            })
        selected = row.get("rakuten", {}).get("selected_sku")
        for index, option in enumerate((selected or {}).get("option_values") or []):
            if isinstance(option, dict):
                add_source(groups, option.get("value"), {
                    "source_kind": "selected_axis_value", "case_id": case_id,
                    "side": "rakuten", "source_field": f"rakuten.selected_sku.option_values[{index}].value",
                    "axis_key": option.get("axis_key"),
                })
        for row_index, sku_row in enumerate(row.get("au", {}).get("sku_rows") or []):
            sku = sku_row.get("sku")
            if not isinstance(sku, str):
                continue
            for axis_index, component in enumerate(sku.split(" / ")):
                if "=" in component:
                    axis_name, value = component.split("=", 1)
                    add_source(groups, value.strip(), {
                        "source_kind": "au_row_axis_value", "case_id": case_id,
                        "side": "au", "source_field": f"au.sku_rows[{row_index}].sku",
                        "row_key": sku_row.get("row_key"), "axis_index": axis_index,
                        "axis_name_raw": axis_name.strip(),
                    })
    for packet in packets:
        for evidence_index, evidence in enumerate(packet.get("evidence") or []):
            if not isinstance(evidence, dict):
                continue
            source_ref = evidence.get("source_ref")
            add_source(groups, evidence.get("quote"), {
                "source_kind": "packet_quote", "packet_id": packet.get("packet_id"),
                "case_id": packet.get("case_id"), "au_row_key": packet.get("au_row_key"),
                "evidence_index": evidence_index,
                "evidence_binding": evidence,
                "source_ref": source_ref,
            })
    return list(groups.values())


def normalize_raw(text: str, raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build span evidence without changing the model's entity strings or scores."""
    entities = raw.get("entities", {})
    evidence: dict[str, list[dict[str, Any]]] = {}
    for field in ENTITY_DESCRIPTIONS:
        evidence[field] = []
        for entry in entities.get(field, []) or []:
            if isinstance(entry, dict):
                span = {key: entry[key] for key in ("text", "start", "end", "confidence") if key in entry}
                if "start" in span and "end" in span:
                    span["source_text"] = text[span["start"] : span["end"]]
                evidence[field].append(span)
            else:
                value = str(entry)
                start = text.find(value)
                evidence[field].append({
                    "text": value, "start": start if start >= 0 else None,
                    "end": start + len(value) if start >= 0 else None,
                    "source_text": value if start >= 0 else None,
                })
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--packets", type=Path, default=DEFAULT_PACKETS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {args.output}")

    sys.path.insert(0, str(HERE))
    from backend_gliner_extract import GLiNERAttributeExtractor, verify_snapshot

    snapshot = verify_snapshot(args.model_dir)
    source_manifest_path = args.model_dir / "source-manifest.json"
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if manifest.get("repo") != MODEL_ID or manifest.get("revision") != REVISION:
        raise RuntimeError("Local extraction model is not the pinned model/revision")

    input_rows = read_jsonl(args.input)
    if len(input_rows) != 56:
        raise RuntimeError(f"Frozen input must have 56 real SKU rows; found {len(input_rows)}")
    packet_rows = read_jsonl(args.packets)
    if len(packet_rows) != 27 or sum(len(x.get("evidence") or []) for x in packet_rows) != 97:
        raise RuntimeError("Frozen packet input must contain 27 packets and 97 evidence quotes")
    units = collect_inputs(input_rows, packet_rows)
    if len(units) > 200:
        raise RuntimeError(f"Bounded literal set exceeds 200 unique texts: {len(units)}")
    for unit in units:
        unit["input_text_sha256"] = sha256_bytes(unit["text"].encode("utf-8"))

    start_load = time.perf_counter()
    extractor = GLiNERAttributeExtractor(args.model_dir, label_style="ja")
    schema = extractor.model.create_schema().entities(ENTITY_DESCRIPTIONS)
    load_seconds = time.perf_counter() - start_load
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True)

    outputs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    tokenized_lengths = [len(tokenizer(unit["text"], add_special_tokens=True, truncation=False)["input_ids"])
                         for unit in units]
    eligible = []
    for unit, token_count in zip(units, tokenized_lengths, strict=True):
        if token_count > MAX_LEN:
            failures.append({
                "bindings": unit["bindings"], "input_text_sha256": unit["input_text_sha256"],
                "text": unit["text"], "token_count_untruncated": token_count,
                "error_type": "InputTooLong", "error": f"{token_count} tokens exceeds configured max_len={MAX_LEN}; skipped without truncation",
            })
        else:
            eligible.append((unit, token_count))
    for offset in range(0, len(eligible), args.batch_size):
        batch_with_lengths = eligible[offset:offset + args.batch_size]
        batch = [item[0] for item in batch_with_lengths]
        batch_texts = [unit["text"] for unit in batch]
        try:
            inference_start = time.perf_counter()
            raw_results = extractor.model.batch_extract(
                batch_texts, schema, batch_size=args.batch_size, threshold=0.5,
                include_spans=True, include_confidence=True,
            )
            elapsed = time.perf_counter() - inference_start
            if len(raw_results) != len(batch):
                raise RuntimeError("model output count differs from input batch")
            for (unit, token_count), raw_result in zip(batch_with_lengths, raw_results, strict=True):
                evidence_by_field = normalize_raw(unit["text"], raw_result)
                proposed = {}
                for field in ENTITY_DESCRIPTIONS:
                    spans = evidence_by_field.get(field, [])
                    proposed[field] = {
                        "all_spans": spans,
                        "threshold_views": {
                            str(threshold): [span for span in spans
                                             if isinstance(span.get("confidence"), (int, float))
                                             and span["confidence"] >= threshold]
                            for threshold in THRESHOLDS
                        },
                    }
                outputs.append({
                    "bindings": unit["bindings"],
                    "input_text_sha256": unit["input_text_sha256"],
                    "text": unit["text"],
                    "token_count_untruncated": token_count,
                    "model_max_len_config": MAX_LEN,
                    "was_truncated_by_length_limit": token_count > MAX_LEN,
                    "batch_elapsed_seconds": elapsed,
                    "proposals": proposed,
                    "raw_model_output": raw_result,
                })
        except Exception as exc:
            for unit in batch:
                failures.append({
                    "bindings": unit["bindings"],
                    "input_text_sha256": unit["input_text_sha256"],
                    "token_count_untruncated": next(length for candidate, length in batch_with_lengths if candidate is unit),
                    "text": unit["text"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.mkdir()  # atomic refusal if another process created it meanwhile
    output_path = args.output / "proposals.jsonl"
    with output_path.open("x", encoding="utf-8") as stream:
        for row in outputs:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    errors_path = args.output / "errors.jsonl"
    with errors_path.open("x", encoding="utf-8") as stream:
        for row in failures:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    meta = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(args.input.resolve()),
        "input_sha256": sha256_file(args.input),
        "packets_path": str(args.packets.resolve()),
        "packets_sha256": sha256_file(args.packets),
        "input_row_count": len(input_rows),
        "source_text_unit_count": len(units),
        "output_count": len(outputs),
        "error_count": len(failures),
        "packet_row_count": len(packet_rows),
        "packet_quote_count_before_dedup": sum(len(x.get("evidence") or []) for x in packet_rows),
        "unique_literal_count": len(units),
        "input_scope": "56 case AU/Rakuten titles, Rakuten selected-axis values, AU per-row axis values, and 27 packet evidence quote literals; exact duplicate text deduplicated with all source bindings retained",
        "model_id": MODEL_ID,
        "revision": REVISION,
        "model_dir": str(args.model_dir.resolve()),
        "model_files": snapshot,
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "torch": importlib.metadata.version("torch"),
            "gliner2": importlib.metadata.version("gliner2"),
            "device": "cpu",
            "batch_size": args.batch_size,
            "model_load_seconds": load_seconds,
            "max_len_config": MAX_LEN,
            "encoder_max_position_embeddings": 512,
        },
        "labels": ENTITY_DESCRIPTIONS,
        "runner_path": str(Path(__file__).resolve()),
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "thresholds": list(THRESHOLDS),
        "thresholds_are_diagnostic_only": True,
        "model_entity_threshold": 0.5,
        "scoring": "No gold labels read; no accuracy or threshold tuning performed",
        "outputs": {
            "proposals.jsonl_sha256": sha256_file(output_path),
            "errors.jsonl_sha256": sha256_file(errors_path),
        },
    }
    with (args.output / "metadata.json").open("x", encoding="utf-8") as stream:
        json.dump(meta, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "texts": len(units), "success": len(outputs), "errors": len(failures), "load_seconds": load_seconds}, ensure_ascii=False))


if __name__ == "__main__":
    main()
