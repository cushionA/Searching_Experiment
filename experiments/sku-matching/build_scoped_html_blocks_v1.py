#!/usr/bin/env python3
"""Extract source-bound AU description blocks without interpreting specifications.

Offsets address the decoded JSON string, in Unicode code points, not JSON bytes.
``text`` is rendered from visible DOM text and is deliberately *not* an exact
quote. Every block, cell, link and text run retains an exact HTML ``source_ref``.
Table rows stay intact: labels, line breaks, inline conditions and link targets
are not converted into category rules or assertions about the current SKU.
"""
from __future__ import annotations

import argparse
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re

VERSION = "scoped-html-blocks-v2-anchor-excluded"
FIELDS = ("itemComment", "extraItemComment", "extraItemCommentWithVideo")
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
INVISIBLE = frozenset({"script", "style", "template"})
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
BOUNDARIES = frozenset({"p", "div", "section", "article", "center", "ul", "ol", "li", "dl", "dt", "dd", "tr", "td", "th", "table", *HEADINGS})
BLOCK_TAGS = frozenset({"p", "div", "section", "article", "center", "li", "dt", "dd", "blockquote", "pre", *HEADINGS})


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _Node:
    def __init__(self, tag, start, end, parent=None, attrs=None, path=""):
        self.tag, self.start, self.end, self.parent = tag, start, end, parent
        self.attrs, self.path = dict(attrs or []), path
        self.children, self.counts = [], {}
        self.start_tag_end = end
        self.closure = "void" if tag in VOID else "open"


class _Text:
    def __init__(self, text, start, end, kind="text"):
        self.text, self.start, self.end, self.kind = text, start, end, kind


class _DOM(HTMLParser):
    """Small tolerant DOM with explicit/implicit source extents.

    Optional HTML row/cell/list/paragraph closing tags are handled structurally;
    unlike a browser this parser does not apply CSS or fetch embedded resources.
    """
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.line_starts = [0]
        self.line_starts.extend(match.end() for match in re.finditer("\n", raw))
        self.root = _Node("document", 0, len(raw), path="/document")
        self.stack = [self.root]
        self.nodes = []

    def char_offset(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _close_to(self, index, end, closure):
        while len(self.stack) > index:
            node = self.stack.pop()
            node.end, node.closure = end, closure

    def _optional_close(self, tag, start):
        # Stop at the containing structural barrier, so nested tables/lists do
        # not close cells/items in their parent table/list.
        targets, barriers = set(), set()
        if tag in {"td", "th"}:
            targets, barriers = {"td", "th"}, {"tr", "table"}
        elif tag == "tr":
            targets, barriers = {"tr"}, {"table"}
        elif tag == "li":
            targets, barriers = {"li"}, {"ul", "ol"}
        elif tag in {"dt", "dd"}:
            targets, barriers = {"dt", "dd"}, {"dl"}
        elif tag == "p" or tag in BOUNDARIES:
            targets, barriers = {"p"}, {"td", "th", "li", "document"}
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag in targets:
                self._close_to(index, start, "implicit_before_start_tag")
                break
            if self.stack[index].tag in barriers:
                break

    def handle_starttag(self, tag, attrs):
        start = self.char_offset()
        self._optional_close(tag, start)
        parent = self.stack[-1]
        parent.counts[tag] = parent.counts.get(tag, 0) + 1
        end = start + len(self.get_starttag_text())
        node = _Node(tag, start, end, parent, attrs,
                     f"{parent.path}/{tag}[{parent.counts[tag]}]")
        parent.children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            node = self.stack.pop()
            node.closure = "self_closing"

    def handle_endtag(self, tag):
        start = self.char_offset()
        stop = self.raw.find(">", start)
        end = len(self.raw) if stop < 0 else stop + 1
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self._close_to(index + 1, start, "implicit_before_end_tag")
                node = self.stack.pop()
                node.end, node.closure = end, "explicit_end_tag"
                break

    def handle_data(self, data):
        start = self.char_offset()
        self.stack[-1].children.append(_Text(data, start, start + len(data)))

    def _entity(self, spelling):
        start = self.char_offset()
        # HTMLParser accepts some references without ';'. Bind the actual
        # source spelling rather than inventing a terminal semicolon.
        length = len(spelling)
        if self.raw[start:start + length] != spelling:
            spelling = spelling[:-1]
            length -= 1
        self.stack[-1].children.append(_Text(unescape(spelling), start, start + length, "entity"))

    def handle_entityref(self, name):
        self._entity("&" + name + ";")

    def handle_charref(self, name):
        self._entity("&#" + name + ";")

    def finish(self):
        self.feed(self.raw)
        self.close()
        self._close_to(1, len(self.raw), "implicit_end_of_input")
        return self


def _ancestors(node):
    node = node.parent
    while node is not None:
        yield node
        node = node.parent


def _nearest(node, tags):
    return next((parent for parent in _ancestors(node) if parent.tag in tags), None)


def _visible(node):
    return node.tag not in INVISIBLE and not any(parent.tag in INVISIBLE for parent in _ancestors(node))


def _normalized(value):
    # Formatting only; no product terms, numbers or commercial lines removed.
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip()).strip()


def _tokens(node, evidence_only=False):
    if isinstance(node, _Text):
        return [(node.text, node)]
    if evidence_only and node.tag == "a":
        return [("\n", None)]
    if not _visible(node):
        return []
    if node.tag in {"br", "hr"}:
        return [("\n", None)]
    if node.tag == "img":
        alt = node.attrs.get("alt")
        return [(alt, node)] if isinstance(alt, str) and alt else []
    result = []
    for child in node.children:
        if isinstance(child, _Node) and child.tag in BOUNDARIES:
            result.append(("\n", None))
        result.extend(_tokens(child, evidence_only=evidence_only))
        if isinstance(child, _Node) and child.tag in BOUNDARIES:
            result.append(("\n", None))
    return result


def _text(node, evidence_only=False):
    return _normalized("".join(value for value, _ in _tokens(node, evidence_only=evidence_only)))


def _descendants(node, tags):
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag in tags and _visible(child):
                yield child
            yield from _descendants(child, tags)


def _source_identity(source_ref):
    if not isinstance(source_ref, dict):
        raise TypeError("source_ref must be a mapping")
    raw_file, digest = source_ref.get("raw_file"), source_ref.get("sha256")
    path = source_ref.get("json_path", source_ref.get("locator", {}).get("json_path"))
    if not all(isinstance(value, str) and value for value in (raw_file, digest, path)):
        raise ValueError("source_ref requires raw_file, sha256 and json_path/locator.json_path")
    if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
        raise ValueError("source_ref sha256 must be a lowercase hexadecimal SHA256")
    return raw_file, digest, path


def extract_blocks(raw_html: str, source_ref: dict, title: str = "") -> list[dict]:
    """Return structural blocks with exact references into one original HTML leaf.

    ``source_ref`` requires ``raw_file``, ``sha256`` (raw JSON file hash) and
    ``json_path`` or ``locator.json_path``. The caller verifies the file hash
    and resolves the JSON leaf; this function additionally binds its HTML hash.
    ``title`` is optional context only and is never marked as description evidence.
    A row is not declared applicable to the current SKU by extraction.
    """
    if not isinstance(raw_html, str) or not isinstance(title, str):
        raise TypeError("raw_html and title must be strings")
    raw_file, digest, json_path = _source_identity(source_ref)
    dom = _DOM(raw_html).finish()
    html_digest = sha(raw_html.encode("utf-8"))

    def ref(start, end, **extra):
        return {"raw_file": raw_file, "sha256": digest,
                "locator": {"kind": "json_leaf", "json_path": json_path},
                "start": start, "end": end, "quote": raw_html[start:end],
                "offset_unit": "unicode_code_points", "html_sha256": html_digest, **extra}

    def node_ref(node):
        return ref(node.start, node.end, dom_path=node.path, closure=node.closure)

    def text_runs(node):
        result = []
        for value, leaf in _tokens(node):
            if leaf is None or not value.strip():
                continue
            is_alt = isinstance(leaf, _Node)
            result.append({"text": value, "kind": "image_alt" if is_alt else leaf.kind,
                           "source_ref": ref(leaf.start, leaf.end,
                           **({"attribute": "alt", "dom_path": leaf.path} if is_alt else {}))})
        return result

    def visible_parts(node):
        # Literal pieces for callers that bind against an existing flattened
        # visible-text document. Keep internal whitespace and spelling exactly;
        # a caller must skip a piece if literal find fails, never normalize it
        # into a quote. These pieces are separate from exact raw HTML refs.
        return [line.strip() for value, leaf in _tokens(node) if leaf is not None
                for line in value.splitlines() if line.strip()]

    def evidence_text_runs(node):
        result = []
        for value, leaf in _tokens(node, evidence_only=True):
            if leaf is None or not value.strip():
                continue
            is_alt = isinstance(leaf, _Node)
            result.append({"text": value, "kind": "image_alt" if is_alt else leaf.kind,
                           "source_ref": ref(leaf.start, leaf.end,
                           **({"attribute": "alt", "dom_path": leaf.path} if is_alt else {}))})
        return result

    def evidence_visible_parts(node):
        return [line.strip() for value, leaf in _tokens(node, evidence_only=True) if leaf is not None
                for line in value.splitlines() if line.strip()]

    def links(node):
        candidates = ([node] if node.tag == "a" else []) + list(_descendants(node, {"a"}))
        return [{"href": link.attrs.get("href"), "text": _text(link),
                 "source_ref": node_ref(link), "text_runs": text_runs(link),
                 "visible_parts": visible_parts(link)} for link in candidates]

    def snippet(node, kind):
        return {"kind": kind, "tag": node.tag, "text": _text(node),
                "source_ref": node_ref(node), "links": links(node), "text_runs": text_runs(node),
                "visible_parts": visible_parts(node), "support_text": _text(node, evidence_only=True),
                "support_text_runs": evidence_text_runs(node),
                "support_visible_parts": evidence_visible_parts(node)}

    # Table ownership is based on ancestors, not on arbitrary string windows.
    tables = [node for node in dom.nodes if node.tag == "table" and _visible(node)]
    table_rows = {table.path: [node for node in dom.nodes if node.tag == "tr" and _visible(node)
                  and _nearest(node, {"table"}) is table] for table in tables}
    heading_nodes = [node for node in dom.nodes if node.tag in HEADINGS and _visible(node)
                     and _nearest(node, {"table"}) is None and _text(node)]

    def preceding_headings(node):
        stack = []
        for heading in heading_nodes:
            if heading.start >= node.start:
                break
            level = int(heading.tag[1])
            stack = [previous for previous in stack if int(previous.tag[1]) < level]
            stack.append(heading)
        return [snippet(heading, "section_heading") for heading in stack]

    def cells(row):
        return [node for node in _descendants(row, {"td", "th"}) if _nearest(node, {"tr"}) is row]

    def span_count(cell, attribute):
        value = cell.attrs.get(attribute, "1")
        return max(1, int(value)) if isinstance(value, str) and value.isdecimal() else 1

    inherited = {}
    for table in tables:
        active = []
        for row_index, row in enumerate(table_rows[table.path]):
            active = [entry for entry in active if entry["last_row_index"] >= row_index]
            inherited[row.path] = [{**snippet(entry["node"], "inherited_rowspan_cell"),
                                   "column_index": entry["column_index"],
                                   "origin_row_index": entry["origin_row_index"]} for entry in active]
            occupied = {column for entry in active for column in
                        range(entry["column_index"], entry["column_index"] + entry["column_span"])}
            column = 0
            for cell in cells(row):
                while column in occupied:
                    column += 1
                width, height = span_count(cell, "colspan"), span_count(cell, "rowspan")
                if height > 1:
                    active.append({"node": cell, "column_index": column, "column_span": width,
                                   "origin_row_index": row_index, "last_row_index": row_index + height - 1})
                column += width

    def table_context(table, row):
        headers = []
        for previous in table_rows[table.path]:
            if previous.start >= row.start:
                break
            previous_cells = cells(previous)
            if previous_cells and (all(cell.tag == "th" for cell in previous_cells)
                                   or len(previous_cells) == 1):
                if _text(previous):
                    headers.append(snippet(previous, "preceding_table_header_row"))
        captions = [snippet(caption, "table_caption") for caption in _descendants(table, {"caption"})
                    if _nearest(caption, {"table"}) is table and _text(caption)]
        siblings = table.parent.children
        position = siblings.index(table)
        nearby = {}
        for key, children in (("nearby_before", reversed(siblings[:position])),
                              ("nearby_after", siblings[position + 1:])):
            for sibling in children:
                if isinstance(sibling, _Node) and (sibling.tag == "table" or list(_descendants(sibling, {"table"}))):
                    # A neighboring table is a structural scope boundary, not
                    # a caption to import from a different product/link table.
                    break
                if isinstance(sibling, _Node) and sibling.tag not in {"table", "script", "style", "template"}:
                    if _text(sibling):
                        nearby[key] = snippet(sibling, key)
                        break
                elif isinstance(sibling, _Text) and sibling.text.strip():
                    nearby[key] = {"kind": key, "text": _normalized(sibling.text),
                                   "source_ref": ref(sibling.start, sibling.end)}
                    break
        return {"table_id": "table:" + sha((json_path + table.path + html_digest).encode())[:24],
                "source_ref": node_ref(table), "captions": captions,
                "preceding_header_rows": headers, "inherited_rowspan_cells": inherited.get(row.path, []), **nearby}

    selected = []
    # Every table row is an independent block. The whole cell content, including
    # multiple inline applicability headings, stays within that one row.
    for node in dom.nodes:
        if node.tag == "tr" and _visible(node) and (_text(node) or links(node)):
            selected.append((node, "table_row"))
        elif (node.tag in BLOCK_TAGS and _visible(node) and _nearest(node, {"table"}) is None
              and _text(node)):
            # Emit the innermost meaningful block; wrapper DIVs/CENTERs do not
            # repeat every descendant paragraph or absorb adjacent tables.
            nested = list(_descendants(node, BLOCK_TAGS | {"table"}))
            if not nested:
                selected.append((node, "heading" if node.tag in HEADINGS else
                                 "list_item" if node.tag in {"li", "dt", "dd"} else "paragraph"))

    # Loose text is common in itemComment and around images. Group only runs
    # owned by the same DOM parent; never flatten through a table/block boundary.
    covered = {id(node) for node, _ in selected}
    for parent in [dom.root, *dom.nodes]:
        if not _visible(parent) or parent.tag in INVISIBLE or _nearest(parent, {"table"}) is not None or parent.tag == "table":
            continue
        if any(id(node) in covered for node in [parent, *_ancestors(parent)]):
            continue
        group = []

        def flush():
            nonlocal group
            if group:
                # Virtual node has the exact first-to-last fragment extent, with
                # original children retained for rendering and individual runs.
                virtual = _Node("fragment", group[0].start, group[-1].end, parent,
                                path=f"{parent.path}/fragment[{group[0].start}]")
                virtual.children, virtual.closure = group, "source_fragment"
                if _text(virtual) or links(virtual):
                    selected.append((virtual, "text"))
                group = []

        for child in parent.children:
            if isinstance(child, _Text) or (isinstance(child, _Node) and child.tag not in BLOCK_TAGS | {"table", "tr", "td", "th"}):
                if isinstance(child, _Node) and any(id(descendant) in covered for descendant in _descendants(child, BLOCK_TAGS)):
                    flush()
                else:
                    group.append(child)
            else:
                flush()
        flush()

    blocks = []
    seen = set()
    for node, kind in sorted(selected, key=lambda item: (item[0].start, item[0].end, item[1])):
        identity = (node.start, node.end, kind)
        if identity in seen:
            continue
        seen.add(identity)
        block_id = "html-block:" + sha(json.dumps([raw_file, digest, json_path, html_digest, *identity],
                              ensure_ascii=False, separators=(",", ":")).encode())[:32]
        context = {"source_title_context_only": title, "headings": preceding_headings(node),
                   "ancestor_tags": [parent.tag for parent in reversed(list(_ancestors(node)))],
                   "applicability_inferred": False}
        table = _nearest(node, {"table"})
        if table is not None:
            context["table"] = table_context(table, node)
        block = {"schema_version": VERSION, "block_id": block_id, "kind": kind, "tag": node.tag,
                 "text": _text(node), "text_is_exact_quote": False, "source_ref": node_ref(node),
                 "html_start": node.start, "html_end": node.end, "text_runs": text_runs(node),
                 "visible_parts": visible_parts(node), "links": links(node),
                 "support_text": _text(node, evidence_only=True),
                 "support_text_runs": evidence_text_runs(node),
                 "support_visible_parts": evidence_visible_parts(node), "context": context}
        if kind == "table_row":
            block["cells"] = [{**snippet(cell, "table_cell"),
                               "colspan": cell.attrs.get("colspan", "1"),
                               "rowspan": cell.attrs.get("rowspan", "1"),
                               "scope": cell.attrs.get("scope")} for cell in cells(node)]
        blocks.append(block)
    verify_blocks(raw_html, blocks)
    return blocks


def verify_blocks(raw_html: str, blocks: list[dict]) -> None:
    """Raise for any broken exact HTML reference, including contextual evidence."""
    html_digest = sha(raw_html.encode("utf-8"))
    identities = set()
    source_identity = _source_identity(blocks[0]["source_ref"]) if blocks else None

    def walk(value):
        if isinstance(value, dict):
            if {"raw_file", "sha256", "locator", "start", "end", "quote", "html_sha256"} <= value.keys():
                if _source_identity(value) != source_identity:
                    raise ValueError("HTML source file/hash/JSONPath binding differs")
                start, end = value["start"], value["end"]
                if (not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(raw_html)
                        or value["quote"] != raw_html[start:end] or value["html_sha256"] != html_digest):
                    raise ValueError("HTML source quote/hash/offset binding differs")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for block in blocks:
        if block["block_id"] in identities:
            raise ValueError("duplicate HTML block identity")
        identities.add(block["block_id"])
        if (block["html_start"], block["html_end"]) != (block["source_ref"]["start"], block["source_ref"]["end"]):
            raise ValueError("block extent differs from source reference")
        walk(block)


def blocks_from_item_file(path: Path, raw_file: str | None = None) -> list[dict]:
    """Read exactly one known AU API item file, verify and bind all three fields."""
    body = path.read_bytes()
    data = json.loads(body)
    item = data["itemInfo"]
    blocks = []
    for field in FIELDS:
        raw_html = item.get(field)
        if isinstance(raw_html, str) and raw_html:
            blocks.extend(extract_blocks(raw_html, {"raw_file": raw_file or str(path),
                          "sha256": sha(body), "json_path": "$.itemInfo." + field},
                          title=item.get("itemTitle", "")))
    return blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    blocks = blocks_from_item_file(args.item_file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for block in blocks:
            handle.write(json.dumps(block, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"schema_version": VERSION, "block_count": len(blocks),
                      "output": str(args.output), "output_sha256": sha(args.output.read_bytes())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
