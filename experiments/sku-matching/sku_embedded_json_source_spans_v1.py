"""Candidate provenance adapter for JSON embedded in Rakuten item-page HTML.

Embedded JSON spans address decoded string leaves. Their offsets are not offsets
into the HTML or the JSON source spelling; the locator binds the leaf to the
original file SHA, strict page encoding, script id, and explicit JSONPath.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from html.parser import HTMLParser
import re
from typing import Any, Iterator

SCRIPT_ID = "item-page-app-data"
SOURCE_KIND = "embedded_json_leaf_v1"
_PATH = re.compile(r"(?:\.([A-Za-z_$][\w$]*)|\[(\d+)\])")
class _ScriptFinder(HTMLParser):
    """Collect real script elements with the target id; script data stays raw."""

    def __init__(self, target_id: str):
        super().__init__(convert_charrefs=False)
        self.target_id = target_id
        self.matches: list[tuple[list[tuple[str, str | None]], str, bool]] = []
        self.active: int | None = None
        self.data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        id_values = [value for name, value in attrs if name.lower() == "id"]
        if self.target_id not in id_values:
            return
        if self.active is not None:
            raise ValueError("nested target script")
        self.data = []
        self.matches.append((attrs, "", False))
        self.active = len(self.matches) - 1

    def handle_data(self, data: str) -> None:
        if self.active is not None:
            self.data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.active is not None:
            attrs, _, _ = self.matches[self.active]
            self.matches[self.active] = (attrs, "".join(self.data), True)
            self.active = None
            self.data = []


def _script_json(page: str, script_id: str) -> str:
    finder = _ScriptFinder(script_id)
    finder.feed(page)
    finder.close()
    if finder.active is not None or len(finder.matches) != 1:
        raise ValueError("expected exactly one target script element")
    attrs, body, closed = finder.matches[0]
    names = [name.lower() for name, _ in attrs]
    if len(names) != len(set(names)):
        raise ValueError("duplicate target script attribute")
    values = {name.lower(): value for name, value in attrs}
    if values.get("id") != script_id or values.get("type") != "application/json":
        raise ValueError("target script must have exact application/json type")
    if not closed:
        raise ValueError("unclosed target script")
    return body


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _embedded_document(store: Any, rel: str, encoding: str, script_id: str = SCRIPT_ID) -> tuple[str, Any]:
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("missing source encoding")
    page = store.text(rel, encoding)  # strict decoding through the legacy RawStore
    try:
        body = _script_json(page, script_id)
        document = json.loads(body, object_pairs_hook=_no_duplicate_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid or ambiguous embedded JSON") from exc
    return store.sha(rel), document


def _resolve(document: Any, path: str) -> Any:
    if not isinstance(path, str) or not path.startswith("$"):
        raise ValueError("explicit JSONPath required")
    rest = path[1:]
    node = document
    consumed = 0
    for m in _PATH.finditer(rest):
        if m.start() != consumed:
            raise ValueError("invalid JSONPath")
        key, index = m.groups()
        if key is not None:
            if not isinstance(node, dict) or key not in node:
                raise ValueError("JSONPath does not resolve")
            node = node[key]
        else:
            if not isinstance(node, list) or int(index) >= len(node):
                raise ValueError("JSONPath does not resolve")
            node = node[int(index)]
        consumed = m.end()
    if consumed != len(rest):
        raise ValueError("invalid JSONPath")
    return node


def span_container(store: Any, span: dict[str, Any], legacy_span_container) -> str:
    """Resolve this adapter's locator; send every old locator to the old function."""
    locator = span.get("locator", {})
    if locator.get("kind") != SOURCE_KIND:
        return legacy_span_container(store, span)
    if locator.get("script_id") != SCRIPT_ID:
        raise ValueError("unsupported embedded script id")
    sha, document = _embedded_document(store, span["raw_file"], locator.get("encoding"), locator["script_id"])
    if sha != span.get("sha256"):
        raise ValueError("embedded source SHA mismatch")
    axis_index = locator.get("axis_index")
    member_index = locator.get("member_index")
    axis_key = locator.get("axis_key")
    if type(axis_index) is not int or axis_index < 0 or type(member_index) is not int or member_index < 0:
        raise ValueError("invalid embedded source coordinate")
    if not isinstance(axis_key, str):
        raise ValueError("invalid embedded axis key")
    expected_path = (f"$.newApi.itemInfoSku.variantSelectors[{axis_index}]"
                     f".values[{member_index}].value")
    if locator.get("json_path") != expected_path:
        raise ValueError("embedded path does not match its source coordinates")
    selectors = _resolve(document, "$.newApi.itemInfoSku.variantSelectors")
    if not isinstance(selectors, list) or axis_index >= len(selectors):
        raise ValueError("embedded axis coordinate does not resolve")
    selector = selectors[axis_index]
    if not isinstance(selector, dict) or selector.get("key") != axis_key:
        raise ValueError("embedded axis key does not match its source coordinate")
    members = selector.get("values")
    if not isinstance(members, list) or member_index >= len(members):
        raise ValueError("embedded member coordinate does not resolve")
    member = members[member_index]
    if not isinstance(member, dict) or not isinstance(member.get("value"), str):
        raise ValueError("embedded member coordinate is not a string value")
    leaf = _resolve(document, expected_path)
    if leaf != member["value"]:
        raise ValueError("embedded path does not resolve to its source coordinate")
    if not isinstance(leaf, str):
        raise ValueError("embedded JSON locator must resolve to a string leaf")
    return leaf


def verify_span(store: Any, span: dict[str, Any], legacy_verify_span) -> bool:
    locator = span.get("locator", {})
    if locator.get("kind") != SOURCE_KIND:
        return legacy_verify_span(store, span)
    try:
        leaf = span_container(store, span, lambda _s, _sp: "")
        start, end = span["start"], span["end"]
        return (type(start) is int and type(end) is int and 0 <= start < end <= len(leaf)
                and span.get("quote") != "" and leaf[start:end] == span["quote"])
    except (KeyError, IndexError, TypeError, ValueError, OSError, UnicodeDecodeError):
        return False


def rakuten_selector_value_span(store: Any, rel: str, encoding: str, axis_index: int,
                                axis_key: str, value: str) -> dict[str, Any] | None:
    """Locate one exact decoded selector member by its axis coordinate and member index."""
    if type(axis_index) is not int or axis_index < 0 or not isinstance(value, str) or not value:
        return None
    try:
        sha, document = _embedded_document(store, rel, encoding)
        path = "$.newApi.itemInfoSku.variantSelectors"
        selectors = _resolve(document, path)
        if not isinstance(selectors, list) or axis_index >= len(selectors):
            return None
        selector = selectors[axis_index]
        if not isinstance(selector, dict) or selector.get("key") != axis_key:
            return None
        members = selector.get("values")
        if not isinstance(members, list):
            return None
        indexes = [i for i, member in enumerate(members)
                   if isinstance(member, dict) and member.get("value") == value]
        if len(indexes) != 1:
            return None
        member_index = indexes[0]
        json_path = f"{path}[{axis_index}].values[{member_index}].value"
        if _resolve(document, json_path) != value:
            return None
        return {"raw_file": rel, "sha256": sha,
                "locator": {"kind": SOURCE_KIND, "encoding": encoding, "script_id": SCRIPT_ID,
                            "json_path": json_path, "axis_index": axis_index,
                            "axis_key": axis_key, "member_index": member_index},
                "start": 0, "end": len(value), "quote": value}
    except (KeyError, IndexError, TypeError, ValueError, OSError, UnicodeDecodeError):
        return None


@contextmanager
def install_adapter(old_sources: Any) -> Iterator[None]:
    """Temporarily install on a separate candidate process; always restore old API."""
    old_container = old_sources.span_container
    old_selector = old_sources.rakuten_selector_value_span
    old_verify = old_sources.verify_span

    def adapted_container(store: Any, span: dict[str, Any]) -> str:
        return span_container(store, span, old_container)

    def adapted_verify(store: Any, span: dict[str, Any]) -> bool:
        return verify_span(store, span, old_verify)

    old_sources.span_container = adapted_container
    old_sources.verify_span = adapted_verify
    old_sources.rakuten_selector_value_span = rakuten_selector_value_span
    try:
        yield
    finally:
        old_sources.span_container = old_container
        old_sources.verify_span = old_verify
        old_sources.rakuten_selector_value_span = old_selector
