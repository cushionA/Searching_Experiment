"""Raw-source spans for the fixed-pair SKU gates.

Every quote used by the gates is a literal substring of an original file whose
SHA-256 is checked on load. HTML spans are character offsets into the decoded
page. JSON spans are offsets into one resolved string leaf. Derived dossier text
is never cited as if it were the original source.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ORIGINAL_ROOT = "/workspace/Searching_Experiment"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def relative_source(path: str) -> str:
    """Map the original absolute workspace prefix to the current checkout."""
    if path.startswith(ORIGINAL_ROOT + "/"):
        return path[len(ORIGINAL_ROOT) + 1:]
    return path


_PATH_TOKEN = re.compile(r"\.([^.\[\]]+)|\[(\d+)\]")


def resolve_json_path(document, json_path: str):
    if not json_path.startswith("$"):
        raise ValueError(f"Unsupported JSONPath: {json_path}")
    node, rest = document, json_path[1:]
    for match in _PATH_TOKEN.finditer(rest):
        node = node[match.group(1)] if match.group(1) is not None else node[int(match.group(2))]
    consumed = "".join(m.group(0) for m in _PATH_TOKEN.finditer(rest))
    if consumed != rest:
        raise ValueError(f"Unsupported JSONPath: {json_path}")
    return node


class RawStore:
    """SHA-checked raw files below one checkout root."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._bytes: dict[str, bytes] = {}
        self._sha: dict[str, str] = {}

    def raw(self, rel: str, expected_sha256: str | None = None) -> bytes:
        rel = relative_source(rel)
        if rel not in self._bytes:
            self._bytes[rel] = (self.root / rel).read_bytes()
            self._sha[rel] = sha256_bytes(self._bytes[rel])
        if expected_sha256 is not None and self._sha[rel] != expected_sha256:
            raise ValueError(f"SHA-256 mismatch for {rel}")
        return self._bytes[rel]

    def sha(self, rel: str) -> str:
        self.raw(rel)
        return self._sha[relative_source(rel)]

    def text(self, rel: str, encoding: str) -> str:
        return _decode(self.raw(rel), encoding)

    def json(self, rel: str):
        return _json_cached(self.raw(rel))

    def jsonl_line(self, rel: str, line: int):
        lines = self.raw(rel).split(b"\n")
        return json.loads(lines[line - 1])


@lru_cache(maxsize=64)
def _decode(data: bytes, encoding: str) -> str:
    return data.decode(encoding)


@lru_cache(maxsize=128)
def _json_cached(data: bytes):
    return json.loads(data)


def make_span(store: RawStore, rel: str, locator: dict, start: int, end: int, quote: str) -> dict:
    span = {"raw_file": relative_source(rel), "sha256": store.sha(rel), "locator": locator,
            "start": start, "end": end, "quote": quote}
    if not verify_span(store, span):
        raise ValueError(f"Span does not resolve to its quote: {span}")
    return span


def span_container(store: RawStore, span: dict) -> str:
    locator = span["locator"]
    kind = locator["kind"]
    if kind == "html_text":
        return store.text(span["raw_file"], locator["encoding"])
    if kind == "json_leaf":
        leaf = resolve_json_path(store.json(span["raw_file"]), locator["json_path"])
    elif kind == "jsonl_leaf":
        leaf = resolve_json_path(store.jsonl_line(span["raw_file"], locator["line"]), locator["json_path"])
    else:
        raise ValueError(f"Unknown locator kind: {kind}")
    if not isinstance(leaf, str):
        raise ValueError("JSON span must resolve to a string leaf")
    return leaf


def verify_span(store: RawStore, span: dict) -> bool:
    """True only when file SHA, locator, offsets, and quote all agree."""
    try:
        if store.sha(span["raw_file"]) != span["sha256"]:
            return False
        container = span_container(store, span)
    except (KeyError, IndexError, ValueError, OSError, UnicodeDecodeError):
        return False
    start, end = span["start"], span["end"]
    return (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(container)
            and container[start:end] == span["quote"] and span["quote"] != "")


def sub_span(span: dict, offset: int, length: int) -> dict:
    """A narrower literal span inside an already verified span."""
    if offset < 0 or length <= 0 or offset + length > len(span["quote"]):
        raise ValueError("Sub-span outside parent quote")
    start = span["start"] + offset
    return {**span, "start": start, "end": start + length,
            "quote": span["quote"][offset:offset + length]}


# Rakuten page HTML -----------------------------------------------------------

def _json_string_end(text: str, start: int) -> int:
    """Index of the closing quote for a JSON string whose opening quote is at start-1."""
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i
        i += 1
    raise ValueError("Unterminated JSON string")


def rakuten_selected_values(store: RawStore, rel: str, encoding: str, variant_id: str) -> list[dict]:
    """Spans for one variant's selectorValues entries in the original page JSON."""
    text = store.text(rel, encoding)
    marker = '{"variantId":' + json.dumps(variant_id, ensure_ascii=False) + ',"selectorValues":['
    starts = [m.start() for m in re.finditer(re.escape(marker), text)]
    if not starts:
        raise ValueError(f"Variant {variant_id} is missing in {rel}")
    decoder = json.JSONDecoder()
    copies = {json.dumps(decoder.raw_decode(text, at + len(marker) - 1)[0], ensure_ascii=False) for at in starts}
    if len(copies) != 1:
        # Repeated page-data blocks must agree before any copy is cited.
        raise ValueError(f"Variant {variant_id} has conflicting copies in {rel}")
    pos = starts[0] + len(marker)
    spans = []
    while text[pos] != "]":
        if text[pos] == ",":
            pos += 1
            continue
        if text[pos] != '"':
            raise ValueError("selectorValues must contain JSON strings")
        end = _json_string_end(text, pos + 1)
        literal = text[pos + 1:end]
        locator = {"kind": "html_text", "encoding": encoding,
                   "path": f"sku[variantId={variant_id}].selectorValues[{len(spans)}]",
                   "identical_copies": len(starts)}
        if "\\" in literal:
            spans.append({"unresolved_escape": literal, "locator": locator})
        else:
            spans.append(make_span(store, rel, locator, pos + 1, end, literal))
        pos = end + 1
    return spans


def rakuten_variant_attributes(store: RawStore, rel: str, encoding: str, variant_id: str) -> list[dict]:
    """Per-SKU structured attributes (title/value/unit) of one variant, with value spans."""
    text = store.text(rel, encoding)
    marker = '{"variantId":' + json.dumps(variant_id, ensure_ascii=False) + ',"selectorValues":['
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"Variant {variant_id} is missing in {rel}")
    variant, end = json.JSONDecoder().raw_decode(text, start)
    out, pos = [], start
    for index, attr in enumerate(variant.get("attributes", [])):
        title, value = attr.get("title"), attr.get("value")
        if not isinstance(title, str) or not isinstance(value, str) or not value:
            continue
        literal = '{"title":' + json.dumps(title, ensure_ascii=False) + ',"value":' + json.dumps(value, ensure_ascii=False)
        at = text.find(literal, pos, end)
        if at < 0:
            continue
        value_start = at + len(literal) - len(json.dumps(value, ensure_ascii=False)) + 1
        locator = {"kind": "html_text", "encoding": encoding,
                   "path": f"sku[variantId={variant_id}].attributes[{index}].value"}
        span = make_span(store, rel, locator, value_start, value_start + len(value), value) \
            if json.dumps(value, ensure_ascii=False)[1:-1] == value else None
        out.append({"title": title, "value": value, "unit": attr.get("unit"), "value_span": span})
        pos = at + len(literal)
    return out


def rakuten_variant_selectors(store: RawStore, rel: str, encoding: str) -> list[dict]:
    """Axis keys/labels and allowed values from the page's variantSelectors JSON."""
    text = store.text(rel, encoding)
    marker = '"variantSelectors":'
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"variantSelectors missing in {rel}")
    decoder = json.JSONDecoder()
    selectors, _ = decoder.raw_decode(text, start + len(marker))
    out = []
    search_from = start
    for index, selector in enumerate(selectors):
        label_literal = '"label":' + json.dumps(selector.get("label", ""), ensure_ascii=False)
        at = text.find('"key":' + json.dumps(selector["key"], ensure_ascii=False), search_from)
        label_at = text.find(label_literal, at)
        label_span = None
        if at >= 0 and label_at >= 0 and selector.get("label"):
            value_start = label_at + len('"label":"')
            label_span = make_span(store, rel, {"kind": "html_text", "encoding": encoding,
                                                "path": f"variantSelectors[{index}].label"},
                                   value_start, value_start + len(selector["label"]), selector["label"])
        search_from = max(at, search_from)
        out.append({"key": selector["key"], "label": selector.get("label"),
                    "values": [v.get("value") for v in selector.get("values", [])],
                    "label_span": label_span})
    return out


def html_title_span(store: RawStore, rel: str, encoding: str, title_text: str) -> dict | None:
    text = store.text(rel, encoding)
    open_at = text.find("<title>")
    close_at = text.find("</title>", open_at)
    if open_at < 0 or close_at < 0:
        return None
    at = text.find(title_text, open_at, close_at)
    if at < 0:
        return None
    return make_span(store, rel, {"kind": "html_text", "encoding": encoding, "path": "html/head/title"},
                     at, at + len(title_text), title_text)


def locate_lines_in_text(store: RawStore, rel: str, encoding: str, lines: list[str], path: str) -> list[dict | None]:
    """Sequentially locate stripped description lines; unlocatable lines stay None."""
    text = store.text(rel, encoding)
    pos, out = 0, []
    for index, line in enumerate(lines):
        quote = line.strip()
        at = text.find(quote, pos) if quote else -1
        if at < 0:
            out.append(None)
            continue
        out.append(make_span(store, rel, {"kind": "html_text", "encoding": encoding,
                                          "path": f"{path}[line={index + 1}]"}, at, at + len(quote), quote))
        pos = at + len(quote)
    return out


# AU item JSON ------------------------------------------------------------------

def json_leaf_span(store: RawStore, rel: str, json_path: str, quote: str | None = None,
                   search_from: int = 0) -> dict | None:
    leaf = resolve_json_path(store.json(rel), json_path)
    if not isinstance(leaf, str) or leaf == "":
        return None
    quote = leaf if quote is None else quote
    at = leaf.find(quote, search_from)
    if at < 0 or quote == "":
        return None
    return make_span(store, rel, {"kind": "json_leaf", "json_path": json_path}, at, at + len(quote), quote)


def jsonl_leaf_span(store: RawStore, rel: str, line: int, json_path: str) -> dict | None:
    leaf = resolve_json_path(store.jsonl_line(rel, line), json_path)
    if not isinstance(leaf, str) or leaf == "":
        return None
    return make_span(store, rel, {"kind": "jsonl_leaf", "line": line, "json_path": json_path}, 0, len(leaf), leaf)
