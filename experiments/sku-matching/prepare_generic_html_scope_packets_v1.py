#!/usr/bin/env python3
"""Freeze generic HTML structure from SHA-verified, local AU JSON leaves.

No fetching, condition parsing, normalization, content ranking, or inference.
Offsets refer to Unicode characters in a JSON-decoded source field, never file
bytes. HTML text and attributes are derived; raw_html is the literal field slice.
The tree follows HTMLParser events, not a browser DOM or CSS visibility model.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-generic-residual-evidence-20261010-v2"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-generic-html-scope-packets-20261010-v1"
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
ROLES = {**{f"h{i}": "heading" for i in range(1, 7)}, "a": "link", "img": "image",
         "ul": "list", "ol": "list", "li": "list_item", "table": "table",
         "tr": "table_row", "td": "table_cell", "th": "table_cell", "p": "paragraph"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def json_leaf(value: Any, path: str) -> str:
    if not path.startswith("$"):
        raise ValueError(f"unsupported JSON path: {path}")
    cursor = 1
    while cursor < len(path):
        match = re.match(r'\.([^.[\]]+)|\[(\d+)\]|\[("(?:\\.|[^"\\])*")\]', path[cursor:])
        if not match:
            raise ValueError(f"unsupported JSON path: {path}")
        key = match[1] if match[1] is not None else int(match[2]) if match[2] is not None else json.loads(match[3])
        value = value[key]
        cursor += len(match[0])
    if not isinstance(value, str):
        raise ValueError(f"source JSON leaf must be a string: {path}")
    return value


class ScopeParser(HTMLParser):
    def __init__(self, raw: str, field_id: str, source_ref: dict[str, Any]):
        super().__init__(convert_charrefs=False)
        self.raw, self.field_id, self.source_ref = raw, field_id, source_ref
        self.lines = [0] + [m.end() for m in re.finditer("\n", raw)]
        self.tokens: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.stack: list[dict[str, Any]] = []
        self.last_heading: str | None = None
        self.new_block("#field", 0, [], "field")

    def position(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def span(self, start: int, end: int) -> dict[str, Any]:
        return {"start": start, "end": end, "offset_unit": "unicode_characters",
                "offset_space": self.field_id, "offset_basis": "json_decoded_source_field_string",
                "raw_html": self.raw[start:end], "source_locator": self.source_ref["locator"],
                "source_ref": self.source_ref}

    def context(self) -> dict[str, Any]:
        context = {"ancestor_block_ids": [b["block_id"] for b in self.stack],
                   "preceding_heading_block_id": self.last_heading}
        for role in ("heading", "list", "list_item", "table", "table_row", "table_cell", "link"):
            context[role + "_block_ids"] = [b["block_id"] for b in self.stack if b["role"] == role]
        return context

    def token(self, kind: str, start: int, end: int, text: str = "", **extra: Any) -> None:
        visible = kind in ("data", "entity", "charref") and not any(b["tag"] in ("script", "style") for b in self.stack)
        token = {"token_id": f"{self.field_id}:t{len(self.tokens)}", "kind": kind,
                 "source_html_span": self.span(start, end), "text": text,
                 "text_kind": "derived_decoded_html_text" if visible else "non_visible_or_structural_token",
                 "included_in_visible_text": visible, "context": self.context(), **extra}
        self.tokens.append(token)
        if visible:
            for block in self.stack:
                block["text"] += text

    def new_block(self, tag: str, start: int, attrs: list[tuple[str, str | None]], role: str | None = None) -> dict[str, Any]:
        block = {"block_id": f"{self.field_id}:b{len(self.blocks)}", "tag": tag,
                 "role": role or ROLES.get(tag, "element"), "start": start,
                 "context": self.context(), "parent_block_id": self.stack[-1]["block_id"] if self.stack else None,
                 "child_block_ids": [], "attributes": [{"name": name, "value": value} for name, value in attrs],
                 "text": "", "text_kind": "derived_decoded_html_text"}
        if self.stack:
            self.stack[-1]["child_block_ids"].append(block["block_id"])
        self.blocks.append(block)
        self.stack.append(block)
        return block

    def close_block(self, end: int, closure: str) -> None:
        block = self.stack.pop()
        block["source_html_span"] = self.span(block.pop("start"), end)
        block["closure"] = closure
        if block["role"] == "heading":
            self.last_heading = block["block_id"]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self.position()
        end = start + len(self.get_starttag_text())
        self.new_block(tag, start, attrs)
        self.token("start_tag", start, end, tag=tag)
        block = self.stack[-1]
        block["derived_attributes"] = [{"name": name, "text": value,
             "text_kind": "derived_decoded_html_attribute", "source_html_span": self.span(start, end),
             "span_scope": "whole_start_tag_not_attribute_value"} for name, value in attrs
             if value is not None and (tag == "a" and name == "href" or tag == "img" and name == "alt")]
        if tag in VOID:
            self.close_block(end, "void_element")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.close_block(self.position() + len(self.get_starttag_text()), "self_closing_tag")

    def handle_endtag(self, tag: str) -> None:
        start = self.position()
        end = self.raw.index(">", start) + 1
        self.token("end_tag", start, end, tag=tag)
        match = next((i for i in range(len(self.stack) - 1, 0, -1) if self.stack[i]["tag"] == tag), None)
        if match is not None:
            while len(self.stack) - 1 > match:
                self.close_block(start, "ancestor_end_tag")
            self.close_block(end, "explicit_end_tag")

    def handle_data(self, data: str) -> None:
        start = self.position()
        self.token("data", start, start + len(data), data)

    def reference(self, kind: str, name: str) -> None:
        start = self.position()
        length = len(name) + (2 if kind == "charref" else 1)
        end = start + length + int(self.raw[start + length:start + length + 1] == ";")
        self.token(kind, start, end, unescape(self.raw[start:end]))

    def handle_entityref(self, name: str) -> None:
        self.reference("entity", name)

    def handle_charref(self, name: str) -> None:
        self.reference("charref", name)

    def structural(self, kind: str, suffix: str) -> None:
        start = self.position()
        found = self.raw.find(suffix, start)
        self.token(kind, start, len(self.raw) if found < 0 else found + len(suffix))

    def handle_comment(self, data: str) -> None:
        self.structural("comment", "-->")

    def handle_decl(self, decl: str) -> None:
        self.structural("declaration", ">")

    def handle_pi(self, data: str) -> None:
        self.structural("processing_instruction", ">")

    def unknown_decl(self, data: str) -> None:
        self.structural("unknown_declaration", "]>")

    def finish(self) -> dict[str, Any]:
        self.feed(self.raw)
        self.close()
        while self.stack:
            self.close_block(len(self.raw), "end_of_field")
        # Preserve any syntax not exposed by HTMLParser without inventing text.
        cursor, gaps = 0, []
        for token in sorted(self.tokens, key=lambda t: t["source_html_span"]["start"]):
            span = token["source_html_span"]
            if span["start"] > cursor:
                gaps.append((cursor, span["start"]))
            cursor = max(cursor, span["end"])
        if cursor < len(self.raw):
            gaps.append((cursor, len(self.raw)))
        for start, end in gaps:
            self.token("unparsed_raw", start, end)
        self.tokens.sort(key=lambda t: t["source_html_span"]["start"])
        return {"raw_html": self.raw, "text": self.blocks[0]["text"],
                "text_kind": "derived_decoded_html_text", "tokens": self.tokens, "blocks": self.blocks,
                "coverage": {"source_field_char_count": len(self.raw), "raw_coverage_complete": True,
                             "unparsed_raw_char_count": sum(end - start for start, end in gaps),
                             "all_parser_visible_text_tokens_retained": True,
                             "visibility_basis": "HTMLParser data/entities except script/style; no CSS evaluation"}}


def prepare(input_dir: Path, output_dir: Path, source_root: Path = ROOT) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    inputs = [input_dir / "documents.jsonl"] + [input_dir / name for name in ("tasks.jsonl", "manifest.json") if (input_dir / name).is_file()]
    input_sha = {str(path.resolve()): digest(path) for path in inputs}
    document_lines = [(line, json.loads(text)) for line, text in
                      enumerate(inputs[0].read_text(encoding="utf-8").splitlines(), 1) if text.strip()]
    by_id = {}
    sources: dict[str, tuple[str, Any]] = {}
    packets = []
    for line, document in document_lines:
        identity, au = document["document_id"], document["au"]
        if identity in by_id or str(document["au_product_id"]) != str(au["product_id"]):
            raise ValueError("duplicate document or fixed AU product identity mismatch")
        by_id[identity] = document
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for index, description in enumerate(au.get("descriptions", [])):
            refs = list(description.get("source_refs", [])) + ([description["source_ref"]] if description.get("source_ref") else [])
            if not refs:
                raise ValueError("description has no source JSON leaf reference")
            for ref in refs:
                locator = ref["locator"]
                json_path = locator["json_path"] if isinstance(locator, dict) else locator
                key = (ref["raw_file"], ref["sha256"], json_path)
                group = groups.setdefault(key, {"source_ref": copy.deepcopy(ref), "old_description_text_refs": []})
                group["old_description_text_refs"].append({"description_index": index,
                    "json_path": f"$.au.descriptions[{index}].text", "text": description["text"],
                    "original_source_refs": copy.deepcopy(refs)})
        if au.get("title_source"):
            ref = au["title_source"]
            locator = ref["locator"]
            json_path = locator["json_path"] if isinstance(locator, dict) else locator
            groups[(ref["raw_file"], ref["sha256"], json_path)] = {"source_ref": copy.deepcopy(ref),
                "old_description_text_refs": [], "plain_text_title": True}
        fields = []
        for number, ((raw_file, expected_sha, json_path), group) in enumerate(groups.items()):
            path = (source_root / raw_file).resolve()
            if str(path) not in sources:
                observed_sha = digest(path)
                if observed_sha != expected_sha:
                    raise ValueError(f"raw source SHA-256 mismatch: {raw_file}")
                sources[str(path)] = (observed_sha, json.loads(path.read_text(encoding="utf-8")))
            if sources[str(path)][0] != expected_sha:
                raise ValueError(f"conflicting expected source SHA-256: {raw_file}")
            raw = json_leaf(sources[str(path)][1], json_path)
            field_id = f"{identity}:html-field:{number}"
            source_ref = {"raw_file": raw_file, "sha256": expected_sha,
                          "locator": {"kind": "json_leaf", "json_path": json_path}}
            if group.get("plain_text_title"):
                parser = ScopeParser(raw, field_id, source_ref)
                parser.token("data", 0, len(raw), raw)
                parser.close_block(len(raw), "plain_json_leaf")
                structural = {"raw_html": raw, "text": raw, "text_kind": "plain_json_leaf_text",
                              "tokens": parser.tokens, "blocks": parser.blocks,
                              "coverage": {"source_field_char_count": len(raw), "raw_coverage_complete": True,
                                           "unparsed_raw_char_count": 0, "all_parser_visible_text_tokens_retained": True}}
            else:
                structural = ScopeParser(raw, field_id, source_ref).finish()
            for block in structural["blocks"]:
                block["original_source_refs"] = [copy.deepcopy(group["source_ref"])]
            field = {"field_id": field_id, "source_ref": source_ref,
                           "original_source_ref": group["source_ref"],
                           "field_kind": "plain_title" if group.get("plain_text_title") else "description_source_field",
                           "old_description_text_refs": group["old_description_text_refs"], **structural}
            if group.get("plain_text_title"):
                field["old_title_text_ref"] = {"json_path": "$.au.title", "text": au["title"],
                                              "original_source_ref": group["source_ref"]}
            fields.append(field)
        packets.append({"schema_version": "generic-html-scope-packet-v1", "document_id": identity,
            "dossier_id": document["dossier_id"], "au_product_id": document["au_product_id"],
            "source_document_ref": {"path": str(inputs[0].resolve()), "sha256": input_sha[str(inputs[0].resolve())], "line": line},
            "fixed_au_product_ref": {"document_id": identity, "dossier_id": document["dossier_id"], "product_id": au["product_id"]},
            "fields": fields, "coverage": {"all_description_source_fields_retained": True,
            "input_description_count": len(au.get("descriptions", [])), "field_count": len(fields)}})
    task_path = input_dir / "tasks.jsonl"
    for task in read_jsonl(task_path) if task_path.is_file() else []:
        document = by_id[task["fixed_au_product_ref"]["document_id"]]
        ref = task["fixed_au_product_ref"]
        rows = {row["row_key"]: row for row in document["au"]["rows"]}
        if (str(task["au_product_id"]) != str(document["au_product_id"]) or
                str(ref["product_id"]) != str(document["au_product_id"]) or
                task["dossier_id"] != document["dossier_id"] or ref["dossier_id"] != document["dossier_id"] or
                task["selected_au_row"] != rows[task["au_row_key"]]):
            raise ValueError("selected task AU identity or full row mismatch")
    for path in inputs:
        if digest(path) != input_sha[str(path.resolve())]:
            raise RuntimeError("frozen input changed during preparation")
    for path, (expected, _) in sources.items():
        if digest(Path(path)) != expected:
            raise RuntimeError("raw source changed during preparation")
    manifest = {"schema_version": "generic-html-scope-packets-v1", "input_sha256": input_sha,
        "source_raw_sha256": {path: item[0] for path, item in sources.items()}, "code_sha256": digest(Path(__file__)),
        "product_count": len(packets), "source_file_count": len(sources),
        "field_count": sum(len(p["fields"]) for p in packets),
        "block_count": sum(len(f["blocks"]) for p in packets for f in p["fields"]),
        "token_count": sum(len(f["tokens"]) for p in packets for f in p["fields"]),
        "raw_coverage_complete": all(f["coverage"]["raw_coverage_complete"] for p in packets for f in p["fields"]),
        "unparsed_raw_char_count": sum(f["coverage"]["unparsed_raw_char_count"] for p in packets for f in p["fields"]),
        "offset_basis": "Unicode characters in JSON-decoded source field; not raw-file bytes",
        "scope_basis": "HTMLParser event nesting, not browser DOM/CSS rendering",
        "labels_read": False, "inference_run": False, "external_fetches": 0,
        "content_filters": False, "condition_normalization": False,
        "original_documents_and_tasks": "byte-identical copies; packets are separate prototype evidence"}
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("documents.jsonl", "tasks.jsonl"):
        if (input_dir / name).is_file():
            with (output_dir / name).open("xb") as stream:
                stream.write((input_dir / name).read_bytes())
    with (output_dir / "packets.jsonl").open("x", encoding="utf-8") as stream:
        for packet in packets:
            stream.write(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest["output_sha256"] = {path.name: digest(path) for path in output_dir.iterdir()}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        manifest = prepare(**vars(args))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: manifest[key] for key in ("product_count", "source_file_count", "field_count", "block_count", "token_count", "raw_coverage_complete", "unparsed_raw_char_count", "output_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
