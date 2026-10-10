#!/usr/bin/env python3
"""Freeze every fixed-AU text window for externally supplied residual conditions.

No condition/category parsing, candidate filtering, evidence ranking, or inference.
Offsets locate Unicode characters in frozen derived text, never in raw HTML/API.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-generic-residual-evidence-20261010-v1"
REQUIRED = ("case_id", "au_row_key", "condition_id", "axis_name", "selected_value", "option_values")
OPTIONAL = ("condition_text", "raw_condition", "source_refs")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def indexed(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        identity = str(row[key])
        if identity in result:
            raise ValueError(f"duplicate {key}: {identity}")
        result[identity] = row
    return result


def make_windows(text: str, document_id: str, char_budget: int, overlap_chars: int) -> list[dict[str, Any]]:
    if char_budget < 1 or not 0 <= overlap_chars < char_budget:
        raise ValueError("require char_budget > overlap_chars >= 0")
    windows = []
    start = 0
    while start < len(text):
        end = min(start + char_budget, len(text))
        windows.append({"window_id": f"{document_id}:w{len(windows)}", "text": text[start:end],
                        "derived_text_start": start, "derived_text_end": end,
                        "offset_unit": "unicode_characters", "offset_space": document_id,
                        "overlap_with_previous_chars": max(0, windows[-1]["derived_text_end"] - start) if windows else 0})
        if end == len(text):
            break
        start = end - overlap_chars
    return windows


def make_document(product: dict[str, Any], input_ref: dict[str, Any],
                  char_budget: int, overlap_chars: int) -> dict[str, Any]:
    au = copy.deepcopy(product["au"])
    doc_id = f"fixed-au:{product['dossier_id']}:{au['product_id']}"
    description_id, title_id = doc_id + ":description", doc_id + ":title"
    parts, segments = [], []
    cursor = 0
    for index, record in enumerate(au.get("descriptions", [])):
        text = record["text"]
        if not isinstance(text, str):
            raise ValueError("AU description text must be a string")
        if index:
            cursor += 1
        refs = copy.deepcopy(record.get("source_refs", []))
        if record.get("source_ref") is not None:
            refs.append(copy.deepcopy(record["source_ref"]))
        segments.append({"description_index": index, "derived_text_start": cursor,
                         "derived_text_end": cursor + len(text), "source_refs": refs,
                         "input_ref": {**input_ref, "json_path": f"$.au.descriptions[{index}].text"}})
        parts.append(text)
        cursor += len(text)
    text = "\n".join(parts)
    windows = make_windows(text, description_id, char_budget, overlap_chars)
    for window in windows:
        window["segment_refs"] = [segment["description_index"] for segment in segments
                                  if segment["derived_text_start"] < window["derived_text_end"]
                                  and segment["derived_text_end"] > window["derived_text_start"]]
    title = au.get("title") or ""
    if not isinstance(title, str):
        raise ValueError("AU title must be a string")
    return {"document_id": doc_id, "dossier_id": product["dossier_id"],
            "au_product_id": au["product_id"], "input_ref": input_ref, "au": au,
            "title_document": {"document_id": title_id, "text": title,
                               "source_refs": [copy.deepcopy(au["title_source"])] if au.get("title_source") else [],
                               "windows": make_windows(title, title_id, char_budget, overlap_chars),
                               "independent_title_document": True,
                               "coverage": {"start": 0, "end": len(title), "complete": True}},
            "description_document": {"document_id": description_id, "text": text,
                                     "text_kind": "joined_frozen_derived_visible_text", "join_separator": "\n",
                                     "segments": segments, "windows": windows,
                                     "coverage": {"start": 0, "end": len(text), "complete": True}}}


def validate(cases: list[dict[str, Any]], products: list[dict[str, Any]],
             cards: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_case, by_product = indexed(cases, "case_id"), indexed(products, "dossier_id")
    rows_by_dossier = {}
    for product in products:
        rows_by_dossier[str(product["dossier_id"])] = indexed(product["au"]["rows"], "row_key")
    for case in cases:
        product = by_product[str(case["dossier_id"])]
        if str(case["au_product_id"]) != str(product["au"]["product_id"]):
            raise ValueError(f"fixed AU product ID mismatch: {case['case_id']}")
    seen = set()
    for card in cards:
        missing = [key for key in REQUIRED if key not in card]
        if missing:
            raise ValueError(f"residual card missing fields: {missing}")
        if not isinstance(card["axis_name"], str) or not isinstance(card["selected_value"], str) or not card["selected_value"]:
            raise ValueError("axis_name must be a string; selected_value must be a nonempty string")
        if not isinstance(card["option_values"], list) or any(not isinstance(value, str) for value in card["option_values"]):
            raise ValueError("option_values must be a complete array of strings")
        case = by_case[str(card["case_id"])]
        if str(card["au_row_key"]) not in rows_by_dossier[str(case["dossier_id"])]:
            raise ValueError(f"AU row is outside the fixed case product: {card['au_row_key']}")
        if "au_product_id" in card and str(card["au_product_id"]) != str(case["au_product_id"]):
            raise ValueError("residual card fixed AU product ID mismatch")
        if "source_sku_key" in card and card["source_sku_key"] != case.get("rakuten", {}).get("source_sku_key"):
            raise ValueError("residual card source SKU key mismatch")
        identity = tuple(str(card[key]) for key in ("case_id", "au_row_key", "condition_id"))
        if identity in seen:
            raise ValueError(f"duplicate residual condition: {identity}")
        seen.add(identity)
    return by_case, by_product


def prepare(input_dir: Path, residual_cards: Path, output_dir: Path,
            char_budget: int = 300, overlap_chars: int = 75,
            max_cards: int | None = None, count_only: bool = False) -> dict[str, Any]:
    if output_dir.exists() and not count_only:
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    if max_cards is not None and max_cards < 1:
        raise ValueError("max_cards must be positive")
    make_windows("", "validation", char_budget, overlap_chars)
    paths = [input_dir / "cases.jsonl", input_dir / "products.jsonl", residual_cards]
    manifest_path = input_dir / "manifest.json"
    hash_paths = paths + ([manifest_path] if manifest_path.is_file() else [])
    source_pre = {str(path.resolve()): digest(path) for path in hash_paths}
    cases, products, cards = [read_jsonl(path) for path in paths]
    source_lines = {str(path.resolve()): [number for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]
                    for path in (paths[1], residual_cards)}
    input_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    by_case, _ = validate(cases, products, cards)
    total_cards = len(cards)
    if max_cards is not None:
        cards = cards[:max_cards]
    selected_dossiers = {str(by_case[str(card["case_id"])]["dossier_id"]) for card in cards}
    product_path = paths[1]
    documents = [make_document(product, {"path": str(product_path.resolve()),
                                        "sha256": source_pre[str(product_path.resolve())], "line": source_lines[str(product_path.resolve())][index]},
                               char_budget, overlap_chars)
                 for index, product in enumerate(products) if str(product["dossier_id"]) in selected_dossiers]
    by_document = indexed(documents, "dossier_id")
    windows_by_dossier = {str(document["dossier_id"]): [window["window_id"]
                           for field in ("title_document", "description_document") for window in document[field]["windows"]]
                           for document in documents}
    rows_by_dossier = {str(document["dossier_id"]): indexed(document["au"]["rows"], "row_key") for document in documents}
    tasks = []
    for index, card in enumerate([] if count_only else cards):
        case = by_case[str(card["case_id"])]
        document = by_document[str(case["dossier_id"])]
        row = rows_by_dossier[str(case["dossier_id"])][str(card["au_row_key"])]
        task = {key: copy.deepcopy(card[key]) for key in REQUIRED + OPTIONAL if key in card}
        task.update({"task_id": f"residual:{index}", "residual_card": copy.deepcopy(card),
                     "direction": card.get("direction", "rakuten_to_au"),
                     "condition_text": card.get("condition_text", f"{card['axis_name']}：{card['selected_value']}"),
                     "source_sku_key": case.get("rakuten", {}).get("source_sku_key"),
                     "dossier_id": case["dossier_id"], "au_product_id": case["au_product_id"],
                     "fixed_au_product_ref": {"document_id": document["document_id"],
                                               "dossier_id": case["dossier_id"], "product_id": case["au_product_id"]},
                     "selected_au_row": copy.deepcopy(row), "rakuten_selected": copy.deepcopy(case.get("rakuten", {})),
                     "window_refs": windows_by_dossier[str(case["dossier_id"])],
                     "all_windows_required": True, "applicability_required": True,
                     "residual_card_ref": {"path": str(residual_cards.resolve()),
                                           "sha256": source_pre[str(residual_cards.resolve())], "line": source_lines[str(residual_cards.resolve())][index]}})
        tasks.append(task)
    source_post = {str(path.resolve()): digest(path) for path in hash_paths}
    if source_post != source_pre:
        raise RuntimeError("input files changed during preparation")
    manifest = {"schema_version": "generic-residual-evidence-v1", "input_sha256": source_pre,
                "input_manifest_ref": {"path": str(manifest_path.resolve()), "sha256": source_pre.get(str(manifest_path.resolve()))},
                "source_raw_sha256": input_manifest.get("source_raw_sha256", {}),
                "code_sha256": digest(Path(__file__)), "labels_read": False, "inference_run": False,
                "card_count": len(cards), "input_card_count": total_cards, "task_count": len(cards),
                "case_count": len({str(card["case_id"]) for card in cards}), "product_count": len(documents),
                "window_count": sum(len(refs) for refs in windows_by_dossier.values()),
                "condition_window_pair_count": sum(len(windows_by_dossier[str(by_case[str(card["case_id"])]["dossier_id"])]) for card in cards),
                "char_budget": char_budget, "overlap_chars": overlap_chars, "max_cards": max_cards,
                "description_coverage": "complete; all windows retained without ranking or content filters",
                "offset_provenance": "derived frozen Unicode text only; source_refs are not raw character spans"}
    if count_only:
        return manifest
    output_dir.mkdir(parents=True, exist_ok=False)
    write_jsonl(output_dir / "documents.jsonl", documents)
    write_jsonl(output_dir / "tasks.jsonl", tasks)
    manifest["output_sha256"] = {name: digest(output_dir / name) for name in ("documents.jsonl", "tasks.jsonl")}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--residual-cards", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--char-budget", type=int, default=300)
    parser.add_argument("--overlap-chars", type=int, default=75)
    parser.add_argument("--max-cards", type=int)
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(**vars(args))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    fields = ("input_card_count", "card_count", "task_count", "case_count", "product_count", "window_count",
              "condition_window_pair_count", "char_budget", "overlap_chars", "max_cards", "output_sha256")
    print(json.dumps({key: result[key] for key in fields if key in result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
