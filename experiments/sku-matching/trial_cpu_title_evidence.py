#!/usr/bin/env python3
"""CPU-only GLiNER title evidence extraction for fixed AU product pages.

This is a title-span discovery sidecar, not SKU matching or a quality evaluation.
It preserves raw extraction candidates and never resolves a page title to a
selected-option fact without source evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v9/products.jsonl"
DEFAULT_RAW_TITLE_SOURCE = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-cpu-title-evidence-20261010-v2"
MODEL_DIR = ROOT / ".deps/sku-gliner-extract-model"
MODEL_REPO = "fastino/gliner2.5-multi-v1"
MODEL_REVISION = "cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3"
THREADS = 2
BATCH_SIZE = 8
EXPECTED_RECORDS = 29

ENTITY_DESCRIPTIONS = {
    "ja": {
        "lace_inclusion": "商品タイトル中で、レースカーテンが主カーテン等に含まれることを明示する連続した原文。『レースカーテンセット』『レース付き』等。単なる生地名・商品種類の『レース』『ミラーレース』だけでは含有としない。条件句やサイズ・色の列挙があれば、その条件を含む最小限の原文を抽出し、選択SKUへの適用を推定しない。",
        "lace_exclusion": "商品タイトル中で、レースが含まれない、別売り、ドレープのみ等を明示する連続した原文。否定を推定せず、明示的な否定表現だけを抽出する。条件句があれば含める。",
        "package_count": "セット内容の枚数・個数・組数を明示する原文スパン。異なる数量や複数サイズ・選択肢の列挙もそのまま候補として抽出し、どれが選択SKUかは判断しない。",
        "package_type": "カーテンセット、ドレープ、レース、単品、組などセット構成や商品区分を表す原文スパン。商品タイトルの記述をそのまま抽出し、構成を推定しない。",
        "conditional_qualifier": "タイトルに書かれた選択条件、適用条件、サイズ・色・仕様の条件やシリーズ内選択を示す原文。条件が存在しない場合は抽出しない。",
    },
    "en": {
        "lace_inclusion": "An exact title span explicitly saying that a lace curtain is included, such as lace-curtain set or lace included. A fabric/type mention alone is not inclusion. Preserve attached conditions; do not infer applicability to a selected SKU.",
        "lace_exclusion": "An exact title span explicitly saying lace is not included, sold separately, or only the drape is included. Never infer negation from missing words. Preserve conditions.",
        "package_count": "An exact title span stating a package quantity. Preserve multiple quantities and alternatives without deciding which one applies to a selected SKU.",
        "package_type": "An exact title span for set composition or product type, such as curtain set, drape, lace, single item, or group. Do not infer composition.",
        "conditional_qualifier": "An exact title span expressing a selection or applicability condition, series option, size, color, or specification condition.",
    },
}

CONDITION_MARKERS = (
    "選べる", "選択", "選択時", "選んだ場合", "場合", "カラー", "サイズ", "仕様", "タイプ", "のみ", "別売り", "別売",
)

# Deliberately simple literal baseline: these are candidate spans, not labels.
BASELINE_PATTERNS = {
    "lace_inclusion": [
        ("lace_set", re.compile(r"レースカーテンセット|レースカーテン付き|レース付き|レース付")),
    ],
    "lace_exclusion": [
        ("lace_absent", re.compile(r"レース(?:カーテン)?(?:は)?(?:なし|無し|別売り|別売)|ドレープのみ|レース別売り")),
    ],
    "package_count": [
        ("quantity_unit", re.compile(r"(?<!\d)\d+(?:枚|個|組|点|本)(?:セット|組)?")),
    ],
    "package_type": [
        ("package_word", re.compile(r"(?:レースカーテン|ドレープ|カーテン)?(?:\d+枚)?(?:セット|組|単品)")),
    ],
    "conditional_qualifier": [
        ("selection_condition", re.compile(r".{0,12}(?:選べる|選択時|選んだ場合|の場合|のみ|別売り).{0,12}")),
    ],
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def package_versions() -> dict[str, str | None]:
    versions = {}
    for name in ("torch", "transformers", "tokenizers", "gliner2", "safetensors", "huggingface-hub"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def read_raw_title_sources(path: Path, wanted_ids: set[str]) -> tuple[dict[str, dict[str, Any]], str]:
    source_hash = sha256_file(path)
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_index, line in enumerate(stream, 1):
            obj = json.loads(line)
            product_id = str(obj.get("au_product_id", ""))
            if product_id not in wanted_ids:
                continue
            if product_id in selected:
                raise ValueError(f"duplicate raw AU title source for product_id={product_id}")
            title = obj.get("au_product_title_raw")
            if not isinstance(title, str) or not title:
                raise ValueError(f"raw AU title source missing for product_id={product_id}")
            selected[product_id] = {"title": title, "line_index": line_index}
    if set(selected) != wanted_ids:
        raise ValueError(f"raw AU title source mapping incomplete: found={len(selected)} expected={len(wanted_ids)}")
    return selected, source_hash


def read_products(path: Path, raw_title_source_path: Path = DEFAULT_RAW_TITLE_SOURCE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    products: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            title = obj.get("title_raw")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"{path}:{line_no}: missing title_raw")
            # Copy only title and source identity. Description blocks, option rows,
            # and any unrelated fields are intentionally excluded from inference.
            product_id = str(obj.get("product_id", ""))
            if not product_id:
                raise ValueError(f"{path}:{line_no}: missing product_id")
            products.append({
                "dossier_id": obj.get("dossier_id"),
                "product_id": product_id,
                "title_raw": title,
                "prepared_input_line_index": line_no,
                "source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
            })
    ids = [item.get("dossier_id") for item in products]
    if len(products) != EXPECTED_RECORDS:
        raise ValueError(f"expected exactly {EXPECTED_RECORDS} source-backed AU titles, got {len(products)}")
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("dossier_id values must be unique nonempty strings")
    raw_sources, raw_file_sha = read_raw_title_sources(path=raw_title_source_path,
        wanted_ids={item["product_id"] for item in products})
    prepared_file_sha = sha256_file(path)
    for item in products:
        raw = raw_sources[item["product_id"]]
        raw_title = raw["title"]
        exact = item["title_raw"] == raw_title
        item["raw_source_title"] = raw_title
        item["raw_source_title_sha256"] = hashlib.sha256(raw_title.encode("utf-8")).hexdigest()
        item["title_provenance"] = {
            "status": "verbatim_source_leaf" if exact else "derived_semantic_line_not_verbatim",
            "model_input_ref": {"file": str(path), "file_sha256": prepared_file_sha,
                                "line_index": item["prepared_input_line_index"], "json_path": "$.title_raw"},
            "raw_source_ref": {"file": str(raw_title_source_path), "file_sha256": raw_file_sha,
                               "line_index": raw["line_index"], "product_id": item["product_id"],
                               "json_path": "$.au_product_title_raw"},
            "model_input_equals_raw_source_leaf": exact,
        }
    return products, {"prepared_products_sha256": prepared_file_sha,
                      "raw_title_source_sha256": raw_file_sha}


def literal_baseline(title: str, raw_source_title: str | None = None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for field, patterns in BASELINE_PATTERNS.items():
        hits = []
        for pattern_id, pattern in patterns:
            for match in pattern.finditer(title):
                hits.append({"pattern_id": pattern_id, "text": match.group(0),
                             "start": match.start(), "end": match.end(),
                             "quote": title[match.start():match.end()],
                             "source_leaf_quote_valid": (title[match.start():match.end()] in raw_source_title
                                                          if raw_source_title is not None else None)})
        result[field] = hits
    return result


def normalize_span_candidates(title: str, raw_output: dict[str, Any],
                              raw_source_title: str | None = None) -> dict[str, list[dict[str, Any]]]:
    entities = raw_output.get("entities", {}) if isinstance(raw_output, dict) else {}
    if not isinstance(entities, dict):
        entities = {}
    result: dict[str, list[dict[str, Any]]] = {}
    for field in ENTITY_DESCRIPTIONS["ja"]:
        entries = entities.get(field, []) or []
        if not isinstance(entries, list):
            entries = [entries]
        spans = []
        for entry in entries:
            if isinstance(entry, dict):
                text = entry.get("text")
                start, end = entry.get("start"), entry.get("end")
                confidence = entry.get("confidence")
            else:
                text = str(entry)
                start = title.find(text) if text else -1
                end = start + len(text) if start >= 0 else None
                confidence = None
            valid = (isinstance(text, str) and bool(text) and isinstance(start, int)
                     and isinstance(end, int) and 0 <= start < end <= len(title)
                     and title[start:end] == text)
            source_quote_valid = (title[start:end] in raw_source_title
                                  if valid and raw_source_title is not None else None)
            if not valid:
                quote_provenance = "invalid_span_no_quote"
            elif raw_source_title is None:
                quote_provenance = "input_title_only_unverified"
            else:
                quote_provenance = "verbatim_source_leaf" if source_quote_valid else "derived_input_title_only"
            spans.append({"text": text, "start": start, "end": end,
                          "confidence": confidence, "literal_span_valid": valid,
                          "quote": title[start:end] if valid else None,
                          "source_leaf_quote_valid": source_quote_valid,
                          "quote_provenance": quote_provenance,
                          "scope": "title_only_selected_sku_applicability_unknown"})
        result[field] = spans
    return result


def has_condition_marker(title: str) -> bool:
    return any(marker in title for marker in CONDITION_MARKERS)


def evidence_card(title: str, model_spans: dict[str, list[dict[str, Any]]],
                  baseline: dict[str, list[dict[str, Any]]], source_title_sha256: str) -> dict[str, Any]:
    positives = model_spans["lace_inclusion"] + baseline["lace_inclusion"]
    negatives = model_spans["lace_exclusion"] + baseline["lace_exclusion"]
    conflict = bool(positives and negatives)
    conditional = has_condition_marker(title)
    if conflict or conditional:
        lace_status = "review"
    else:
        # The title's applicability to any selected SKU is not known here.
        lace_status = "unknown"
    quantity_mentions = model_spans["package_count"] + baseline["package_count"]
    type_mentions = model_spans["package_type"] + baseline["package_type"]
    composition_review = len({str(span.get("text")) for span in quantity_mentions}) > 1 or conditional
    return {
        "scope": "title_only_selected_sku_applicability_unknown",
        "source_title_sha256": source_title_sha256,
        "lace": {"status": lace_status, "conflict_candidates_present": conflict,
                 "conditional_markers_present": conditional,
                 "inclusion_candidates": positives, "exclusion_candidates": negatives},
        "package_composition": {"status": "review" if composition_review else "unknown",
                                "quantity_mentions": quantity_mentions, "type_mentions": type_mentions,
                                "conditional_markers_present": conditional},
        "interpretation_limit": "Candidate spans are title evidence only; they do not establish selected-SKU inclusion, exclusion, quantity, or package composition.",
    }


def build_schema(model: Any) -> Any:
    return model.create_schema().entities(ENTITY_DESCRIPTIONS["ja"])


def build_outputs(products: list[dict[str, Any]], raw_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(products) != len(raw_outputs):
        raise ValueError("one raw extractor result is required for each title")
    records = []
    for product, raw in zip(products, raw_outputs, strict=True):
        title = product["title_raw"]
        raw_source_title = product["raw_source_title"]
        spans = normalize_span_candidates(title, raw, raw_source_title)
        baseline = literal_baseline(title, raw_source_title)
        records.append({
            **product,
            "source_field": "title_raw",
            "raw_extractor_output": raw,
            "extractor_spans": spans,
            "literal_pattern_baseline": baseline,
            "evidence_card": evidence_card(title, spans, baseline, product["raw_source_title_sha256"]),
        })
    return records


def model_fingerprint(verified: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"repo_id": MODEL_REPO, "revision": MODEL_REVISION,
            "verified_files": verified,
            "parameter_count": None}


def load_cpu_model(torch: Any, model_dir: Path = MODEL_DIR) -> Any:
    # Set offline controls before loading any Hugging Face component. This call
    # only uses the hash-verified local snapshot; it cannot download weights.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from gliner2 import AutoExtractor

    return AutoExtractor.from_pretrained(str(model_dir), local_files_only=True, map_location="cpu")


def prepare_artifacts(input_path: Path, output_dir: Path, script_path: Path,
                      model_dir: Path = MODEL_DIR,
                      raw_title_source_path: Path = DEFAULT_RAW_TITLE_SOURCE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    products, source_hashes = read_products(input_path, raw_title_source_path)
    input_sha = sha256_file(input_path)
    # Reuse the project's pinned model file list and strict SHA/size verifier.
    backend_path = Path(__file__).resolve().with_name("backend_gliner_extract.py")
    if str(backend_path.parent) not in sys.path:
        sys.path.insert(0, str(backend_path.parent))
    import backend_gliner_extract

    verified = backend_gliner_extract.verify_snapshot(model_dir)
    schema = {"fields": ENTITY_DESCRIPTIONS["ja"],
              "scope_policy": "title-only; selected-SKU applicability remains unknown",
              "provenance_policy": "model spans are quoted from prepared $.title_raw and checked independently against $.au_product_title_raw; derived semantic titles are never presented as verbatim raw source quotes",
              "negation_policy": "absence of a phrase never implies exclusion",
              "conflict_policy": "both inclusion and exclusion candidates require review",
              "condition_policy": "title selection/variant/condition markers require review",
              "pattern_baseline": {field: [{"pattern_id": name, "regex": regex.pattern}
                                            for name, regex in values]
                                   for field, values in BASELINE_PATTERNS.items()}}
    (output_dir / "inputs-title-only.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in products), encoding="utf-8")
    (output_dir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "status": "prepared_before_inference",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": "title-only literal condition evidence extraction; no selected-SKU resolution",
        "input_path": str(input_path), "input_sha256": input_sha,
        "source_hashes": source_hashes,
        "input_count": len(products),
        "input_snapshot_sha256": sha256_file(output_dir / "inputs-title-only.jsonl"),
        "schema_sha256": sha256_file(output_dir / "schema.json"),
        "code_path": str(script_path), "code_sha256": sha256_file(script_path),
        "model": model_fingerprint(verified),
        "model_manifest_sha256": canonical_sha256(verified),
        "threads_requested": THREADS, "batch_size": BATCH_SIZE,
        "labels_used": False, "gpu_used": False, "network_downloads": False,
    }
    (output_dir / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return products, manifest


def run(input_path: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT,
        model_dir: Path = MODEL_DIR, threads: int = THREADS, batch_size: int = BATCH_SIZE,
        raw_title_source_path: Path = DEFAULT_RAW_TITLE_SOURCE) -> dict[str, Any]:
    if threads != THREADS:
        raise ValueError(f"CPU comparison is pinned to {THREADS} intra-op threads")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    products, manifest = prepare_artifacts(input_path, output_dir, Path(__file__), model_dir, raw_title_source_path)
    started = time.monotonic()
    try:
        import torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        model = load_cpu_model(torch, model_dir)
        if hasattr(model, "eval"):
            model.eval()
        if hasattr(model, "to"):
            model.to("cpu")
        parameter_devices = sorted({str(parameter.device) for parameter in model.parameters()}) if hasattr(model, "parameters") else ["cpu"]
        if not parameter_devices or any(not device.startswith("cpu") for device in parameter_devices):
            raise RuntimeError(f"CPU-only placement required, got parameter devices {parameter_devices}")
        schema = build_schema(model)
        raw_outputs = model.batch_extract(
            [product["title_raw"] for product in products], schema,
            batch_size=batch_size, include_spans=True, include_confidence=True,
        )
        records = build_outputs(products, raw_outputs)
        output_path = output_dir / "title-evidence.jsonl"
        with output_path.open("w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
        param_count = sum(int(parameter.numel()) for parameter in model.parameters()) if hasattr(model, "parameters") else None
        runtime = {
            "status": "complete", "elapsed_seconds": time.monotonic() - started,
            "input_count": len(products), "output_count": len(records),
            "package_versions": package_versions(),
            "torch_threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
            "device": "cpu", "parameter_devices": parameter_devices,
            "parameter_count": param_count,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "cuda_used": False, "labels_used": False, "network_downloads": False,
            "output_sha256": sha256_file(output_path),
            "raw_extractor_output_retained": True,
            "literal_span_validation": "quote is populated only when title[start:end] exactly equals extracted text",
        }
        (output_dir / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.update({"status": "complete", "finished_at": datetime.now(timezone.utc).isoformat(),
                         "runtime_sha256": sha256_file(output_dir / "runtime.json"),
                         "output_sha256": runtime["output_sha256"],
                         "output_count": len(records), "parameter_count": param_count})
        (output_dir / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    except Exception as exc:
        manifest.update({"status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(),
                         "elapsed_seconds": time.monotonic() - started,
                         "error": f"{type(exc).__name__}: {exc}", "labels_used": False, "gpu_used": False})
        (output_dir / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--raw-title-source", type=Path, default=DEFAULT_RAW_TITLE_SOURCE)
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output, args.model_dir, args.threads, args.batch_size, args.raw_title_source),
                     ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
