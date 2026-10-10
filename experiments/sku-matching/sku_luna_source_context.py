"""Resolve isolated SKU source strings to their cited, hash-verified raw context."""
from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


def _norm(value: str) -> str:
    return " ".join(value.split())


def _json_path(doc, path: str):
    if path in ("", "$", None):
        return doc
    if not isinstance(path, str) or not path.startswith("$"):
        raise ValueError("unsupported JSON path")
    tokens = re.findall(r"\.([^\.\[\]]+)|\[(\d+)\]", path[1:])
    if "".join(f".{key}" if key else f"[{index}]" for key, index in tokens) != path[1:]:
        raise ValueError("malformed JSON path")
    cur = doc
    for key, index in tokens:
        cur = cur[key] if key else cur[int(index)]
    return cur


class _Page(HTMLParser):
    BLOCK = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "dt", "dd", "center", "blockquote", "main", "header", "footer", "address", "pre", "caption", "figure", "figcaption"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.blocks = []
        self.anchors = []
        self.visible = []
        self.hidden = 0
        self.next_id = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hide = tag in {"script", "style", "noscript", "template"} or "hidden" in attrs or attrs.get("aria-hidden") == "true" or "display:none" in attrs.get("style", "").replace(" ", "").lower()
        if tag == "br":
            if not hide: self._append(" ")
            return
        if tag in self.VOID:
            return
        if hide: self.hidden += 1
        if tag in self.BLOCK: self._append(" ")
        self.next_id += 1
        ancestors = [node["_id"] for node in self.stack if node["tag"] in self.BLOCK]
        self.stack.append({"tag": tag, "pieces": [], "href": attrs.get("href"), "hidden": hide,
                           "_id": self.next_id, "block_ancestors": ancestors})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def _append(self, data):
        if self.hidden: return
        in_anchor = any(node["tag"] == "a" for node in self.stack)
        for node in self.stack:
            if node["tag"] in self.BLOCK or node["tag"] == "a":
                node["pieces"].append((data, in_anchor))

    def handle_data(self, data):
        if not self.hidden: self.visible.append(data)
        self._append(data)

    def handle_endtag(self, tag):
        # Tolerate malformed HTML by unwinding through the matching open tag.
        ix = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if ix is None: return
        closed = self.stack[ix:]
        del self.stack[ix:]
        for node in reversed(closed):
            if node["hidden"]: self.hidden = max(0, self.hidden - 1)
            if node["tag"] == "a":
                self.anchors.append((node.get("href"), _norm("".join(piece for piece, _ in node.get("pieces", [])))))
            if node["tag"] in self.BLOCK:
                text = _norm("".join(piece for piece, _ in node["pieces"]))
                plain = _norm("".join(piece for piece, linked in node["pieces"] if not linked))
                links = _norm("".join(piece for piece, linked in node["pieces"] if linked))
                if text: self.blocks.append({"tag": node["tag"], "text": text, "plain": plain, "links": links,
                                             "_id": node["_id"], "block_ancestors": node["block_ancestors"]})
        if tag in self.BLOCK: self._append(" ")


def _safe_member(name: str) -> str:
    name = name.replace("\\", "/")
    normalized = posixpath.normpath(name)
    if name.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError("unsafe ZIP member path")
    return normalized


def _raw(zf, ref):
    if not isinstance(ref, dict): return None
    filename = ref.get("raw_file")
    expected = ref.get("sha256")
    if not filename or not expected: return None
    try:
        data = zf.read(_safe_member(str(filename)))
    except (KeyError, ValueError): return None
    if hashlib.sha256(data).hexdigest().lower() != str(expected).lower(): return None
    return data


def _references(source):
    refs = source.get("source", source.get("provenance"))
    return refs if isinstance(refs, list) else [refs]


def _resolve_ref(zf, ref, product_id, source_text):
    data = _raw(zf, ref)
    if data is None: return [], "unresolved", []
    loc = ref.get("locator", {})
    try:
        raw_text = data.decode("utf-8")
        line = loc.get("line") if isinstance(loc, dict) else None
        if line is not None:
            lines = raw_text.splitlines()
            doc = json.loads(lines[int(line) - 1])
        else:
            doc = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError, TypeError, ValueError): return [], "unresolved", []
    path = loc.get("json_path") or loc.get("path") if isinstance(loc, dict) else loc
    if not path: path = ref.get("json_path")
    try: value = _json_path(doc, path) if path else doc
    except (KeyError, IndexError, TypeError, ValueError): return [], "unresolved", []
    if not isinstance(value, str):
        # JSONL and array locators may point at a containing record; no guessing.
        return [], "unresolved", []
    parser = _Page()
    parser.feed(value)
    parser.close()
    wanted = _norm(str(source_text))
    # Match only blocks containing the exact isolated text. Prefer the enclosing
    # table row over its individual cells so labels remain adjacent to values.
    matches = [b for b in parser.blocks if wanted and wanted in b["text"]]
    rows = [b for b in matches if b["tag"] == "tr"]
    selected = rows or [b for b in matches if not any(b["_id"] in other["block_ancestors"] for other in matches)]
    if not selected:
        # Plain title and description fields have no HTML block structure.
        if parser.blocks:
            return [], "unresolved", []
        visible = _norm("".join(parser.visible))
        if not wanted or wanted not in visible:
            return [], "unresolved", []
        plain = visible
        for _href, anchor in parser.anchors:
            if anchor: plain = plain.replace(anchor, " ")
        selected = [{"tag": "text", "text": visible, "plain": _norm(plain), "links": " ".join(a for _, a in parser.anchors)}]
    contexts = []
    headings = [b for b in parser.blocks if b["tag"] in {"h1", "h2", "h3", "h4", "h5", "h6"}]
    for block in selected[:4]:
        tag, text = block["tag"], block["text"]
        label = f"{tag}: {text}" if tag in {"tr", "th", "td", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"} else text
        if label not in contexts: contexts.append(label)
        if tag == "tr":
            # Preserve the nearest preceding section heading with table values.
            prior = [h for h in headings if parser.blocks.index(h) < parser.blocks.index(block)]
            if prior:
                heading = f"{prior[-1]['tag']}: {prior[-1]['text']}"
                if heading not in contexts: contexts.insert(0, heading)
    # Determine whether this exact phrase occurs in plain text, linked text, or both.
    plain_occurs = any(wanted in b["plain"] for b in selected)
    linked_occurs = any(wanted in b["links"] for b in selected)
    item_ids, ambiguous_link = set(), False
    if linked_occurs:
        for target, visible_anchor in parser.anchors:
            if not (wanted in visible_anchor or visible_anchor in wanted): continue
            absolute = urljoin("https://wowma.jp/", html.unescape(target))
            parsed = urlparse(absolute)
            m = re.match(r"^/item/([^/?#]+)(?:/|$)", parsed.path)
            if m and parsed.netloc.lower() in {"wowma.jp", "www.wowma.jp"}: item_ids.add(m.group(1))
            elif parsed.scheme in {"http", "https"} and parsed.netloc: ambiguous_link = True
    if plain_occurs and linked_occurs: scope = "ambiguous"
    elif len(item_ids) > 1: scope = "ambiguous"
    elif item_ids: scope = "fixed_product" if item_ids == {str(product_id)} else "other_product"
    elif linked_occurs and ambiguous_link: scope = "ambiguous"
    elif linked_occurs: scope = "ambiguous"
    elif plain_occurs: scope = "fixed_product"
    else: return [], "unresolved", []
    locator = {"raw_file": ref["raw_file"], "sha256": ref["sha256"], "json_path": path}
    if isinstance(loc, dict) and "line" in loc: locator["line"] = loc["line"]
    if "start" in ref: locator["start"] = ref["start"]
    if "end" in ref: locator["end"] = ref["end"]
    return contexts, scope, [locator]


def build_source_contexts(case: dict, zip_path: Path) -> dict[str, dict]:
    """Build source-ID keyed contexts, using only hash-verified cited ZIP members."""
    out = {}
    with zipfile.ZipFile(zip_path) as zf:
        for source in case.get("sources", []):
            sid = str(source["source_id"])
            refs = _references(source)
            all_contexts, scopes, provenance = [], [], []
            for ref in refs:
                ctx, scope, locs = _resolve_ref(zf, ref, case.get("au_product_id"), source.get("text", ""))
                scopes.append(scope); provenance.extend(locs)
                for text in ctx:
                    if text not in all_contexts: all_contexts.append(text)
            if not refs: scope = "unresolved"
            elif "unresolved" in scopes: scope = "unresolved"
            elif "ambiguous" in scopes or ("fixed_product" in scopes and "other_product" in scopes): scope = "ambiguous"
            elif "other_product" in scopes: scope = "other_product"
            else: scope = "fixed_product"
            out[sid] = {"source_id": sid, "kind": source.get("kind"), "text": source.get("text", ""),
                        "scope": scope, "contexts": all_contexts,
                        "provenance": source.get("source", source.get("provenance")),
                        "context_provenance": provenance}
    return out
