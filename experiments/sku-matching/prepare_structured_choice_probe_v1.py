#!/usr/bin/env python3
"""Prepare label-blind AU option-choice probes, with and without HTML context.

This is rendering, not scope proof or a production decision. Visible text is
HTMLParser-derived, not CSS visibility. No ranking, filtering or truncation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

UNKNOWN = "このAU行に該当する選択肢は本文から確定できない"
ARMS = ("natural_flat", "natural_structure")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def index_unique(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {key}")
    return result


def field_windows(field: dict[str, Any], char_budget: int, overlap: int) -> list[dict[str, Any]]:
    """Keep every visible character; references retain whole raw token spans.

    Decoded-token offsets are separate from source HTML offsets, since an entity
    can have different decoded/raw lengths. No false per-character HTML mapping.
    """
    text, token_ranges = "", []
    for token in field["tokens"]:
        span = token["source_html_span"]
        if field["raw_html"][span["start"]:span["end"]] != span["raw_html"]:
            raise ValueError("packet token raw span mismatch")
        if token["included_in_visible_text"]:
            start = len(text)
            text += token["text"]
            token_ranges.append((start, len(text), token))
    if text != field["text"]:
        raise ValueError("packet visible tokens do not reproduce field text")
    windows, start = [], 0
    while start < len(text):
        end = min(len(text), start + char_budget)
        refs = []
        for low, high, token in token_ranges:
            if low < end and high > start:
                left, right = max(low, start), min(high, end)
                refs.append({"token_id": token["token_id"], "visible_char_start": left,
                    "visible_char_end": right, "decoded_token_start": left - low,
                    "decoded_token_end": right - low, "text": text[left:right],
                    "source_html_span": deepcopy(token["source_html_span"]),
                    "source_span_scope": "whole_token_not_decoded_substring", "context": deepcopy(token["context"])})
        windows.append({"field_id": field["field_id"], "field_kind": field["field_kind"],
            "source_ref": deepcopy(field["source_ref"]), "visible_char_start": start,
            "visible_char_end": end, "quote": text[start:end], "token_refs": refs})
        if end == len(text):
            break
        start = end - overlap
    return windows


def structural_context(field: dict[str, Any], window: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = index_unique(field["blocks"], "block_id")
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for token in window["token_refs"]:
        context = token["context"]
        groups = (("preceding_heading", [context.get("preceding_heading_block_id")]),
                  ("heading", context.get("heading_block_ids", [])),
                  ("parent_link", context.get("link_block_ids", [])),
                  ("table_row", context.get("table_row_block_ids", [])))
        for kind, ids in groups:
            for identity in ids:
                if identity is None or (kind, identity) in chosen:
                    continue
                block = blocks[identity]
                value = {"kind": kind, "block_id": identity, "text": block["text"],
                         "source_html_span": deepcopy(block["source_html_span"])}
                if kind == "parent_link":
                    value["hrefs"] = [a["value"] for a in block["attributes"] if a["name"] == "href"]
                chosen[(kind, identity)] = value
    return list(chosen.values())


def state_for(title: str, row: dict[str, Any], window: dict[str, Any], context: list[dict[str, Any]]) -> str:
    lines = ["現在のAU商品名：" + title, "現在のAU選択行："]
    lines.extend(f"{axis['axis_name']}：{axis['value']}" for axis in row["axes"])
    lines.append("引用本文：" + window["quote"])
    for item in context:
        if item["kind"] == "parent_link":
            lines.extend("親リンク先：" + (href or "") for href in item["hrefs"])
        else:
            label = "表の行" if item["kind"] == "table_row" else "直前見出し" if item["kind"] == "preceding_heading" else "親見出し"
            lines.append(label + "：" + item["text"])
    return "\n".join(lines)


def prepare(input_dir: Path, packet_dir: Path, output_dir: Path,
            char_budget: int = 220, overlap: int = 40) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    if char_budget <= 0 or not 0 <= overlap < char_budget:
        raise ValueError("require 0 <= overlap < positive char_budget")
    paths = [input_dir / "tasks.jsonl", input_dir / "documents.jsonl", packet_dir / "packets.jsonl"]
    paths += [p / "manifest.json" for p in (input_dir, packet_dir) if (p / "manifest.json").is_file()]
    hashes = {str(p.resolve()): digest(p) for p in paths}
    tasks = read_jsonl(paths[0])
    index_unique(tasks, "task_id")
    documents = index_unique(read_jsonl(paths[1]), "document_id")
    packets = index_unique(read_jsonl(paths[2]), "document_id")
    windows_by_document, coverage, requests = {}, [], []
    for document_id, document in documents.items():
        packet, au = packets[document_id], document["au"]
        expected = {"document_id": document_id, "dossier_id": document["dossier_id"], "product_id": au["product_id"]}
        if (str(document["au_product_id"]) != str(au["product_id"]) or
                packet["fixed_au_product_ref"] != expected or
                packet["dossier_id"] != document["dossier_id"] or
                str(packet["au_product_id"]) != str(au["product_id"])):
            raise ValueError("packet/document fixed AU identity mismatch")
        fields = packet["fields"]
        index_unique(fields, "field_id")
        title_fields = [f for f in fields if f["field_kind"] == "plain_title"]
        if len(title_fields) != 1 or not au["title"] or title_fields[0]["text"] != au["title"]:
            raise ValueError("packet title does not match current AU title")
        windows_by_document[document_id] = []
        for field in fields:
            if (not field["coverage"]["raw_coverage_complete"] or
                    field["coverage"]["source_field_char_count"] != len(field["raw_html"])):
                raise ValueError("packet raw field coverage incomplete")
            budget = max(1, len(field["text"])) if field["field_kind"] == "plain_title" else char_budget
            windows = field_windows(field, budget, 0 if field["field_kind"] == "plain_title" else overlap)
            cursor = 0
            for window in windows:
                if window["visible_char_start"] > cursor:
                    raise ValueError("visible coverage gap")
                cursor = max(cursor, window["visible_char_end"])
                window["structural_context"] = structural_context(field, window)
            if cursor != len(field["text"]):
                raise ValueError("visible coverage incomplete")
            coverage.append({"document_id": document_id, "field_id": field["field_id"],
                "source_ref": field["source_ref"], "raw_html_char_count": len(field["raw_html"]),
                "visible_char_count": len(field["text"]), "window_count": len(windows),
                "raw_packet_coverage_complete": True, "visible_window_coverage_complete": True})
            windows_by_document[document_id].extend(windows)
    for task in tasks:
        ref = task["fixed_au_product_ref"]
        document = documents[ref["document_id"]]
        rows = index_unique(document["au"]["rows"], "row_key")
        if (ref != {"document_id": document["document_id"], "dossier_id": document["dossier_id"], "product_id": document["au"]["product_id"]} or
                task["dossier_id"] != document["dossier_id"] or
                str(task["au_product_id"]) != str(document["au_product_id"]) or
                task["selected_au_row"] != rows[task["au_row_key"]] or
                task.get("direction", "rakuten_to_au") != "rakuten_to_au"):
            raise ValueError("task selected fixed AU identity/full row mismatch")
        options = task["option_values"]
        if not isinstance(options, list) or not options or any(not isinstance(v, str) for v in options):
            raise ValueError("task option_values must be a nonempty raw string list")
        choices = [{"label": f"option:{i}", "description": value} for i, value in enumerate(options)]
        choices.append({"label": "unknown", "description": UNKNOWN})
        question = f"このAU商品の選択中の行について、「{task['axis_name']}」の値はどれですか？引用が他の商品/行だけの説明なら確定しない。"
        for number, window in enumerate(windows_by_document[document["document_id"]]):
            for arm in ARMS:
                context = window["structural_context"] if arm == "natural_structure" else []
                requests.append({"id": f"{task['task_id']}:{arm}:window:{number}", "question": question,
                    "state": state_for(document["au"]["title"], task["selected_au_row"], window, context),
                    "choices": deepcopy(choices), "provenance": {"arm": arm, "task_id": task["task_id"],
                        "case_id": task["case_id"], "condition_id": task["condition_id"],
                        "au_row_key": task["au_row_key"], "selected_value": task["selected_value"],
                        "source_sku_key": task["source_sku_key"], "axis_name": task["axis_name"],
                        "option_values": deepcopy(options), "fixed_au_product_ref": deepcopy(ref),
                        "selected_au_row": deepcopy(task["selected_au_row"]), "window_index": number,
                        "window": deepcopy(window), "scope_proven": False}})
    if any(digest(p) != hashes[str(p.resolve())] for p in paths):
        raise RuntimeError("input changed during preparation")
    manifest = {"schema_version": "structured-choice-probe-v1", "input_sha256": hashes,
        "code_sha256": digest(Path(__file__)), "task_count": len(tasks), "document_count": len(documents),
        "request_count": len(requests), "arms": list(ARMS), "char_budget": char_budget, "overlap": overlap,
        "field_coverage": coverage, "all_visible_field_characters_covered": True,
        "all_raw_fields_retained_in_packets": True, "real_only": True, "synthetic_sku_generation": False,
        "labels_read": False, "desired_rakuten_value_injected_into_question_or_state": False,
        "inference_run": False, "scope_not_proven": True, "production": False,
        "duplicates_retained": True, "structural_context_truncated": False,
        "content_filters": False, "window_ranking": False, "external_fetches": 0,
        "backend_limit_policy": "run_choices rejects too-long inputs without truncation; no scope proof"}
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for request in requests:
            stream.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest["output_sha256"] = {"requests.jsonl": digest(output_dir / "requests.jsonl")}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-dir", "packet-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--char-budget", type=int, default=220)
    parser.add_argument("--overlap", type=int, default=40)
    try:
        result = prepare(**vars(parser.parse_args(argv)))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: result[k] for k in ("task_count", "document_count", "request_count", "output_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
