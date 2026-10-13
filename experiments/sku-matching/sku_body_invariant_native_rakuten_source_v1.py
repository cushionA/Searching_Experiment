#!/usr/bin/env python3
"""Source-only R9 adapter for Rakuten's native productDescription field.

This new wrapper temporarily installs a strict extractor into the frozen R8
module. It adds no semantic classifier and does not alter R8 source, schema,
tests, or decision validation. Native field origins are independently bound
to the captured HTML; extracted support text is rederived from that field.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import sku_body_invariant_choice_v3 as BASE  # noqa: E402
import build_scoped_html_blocks_v1 as HTMLBLOCKS  # noqa: E402
BASE_SOURCE_BODY = BASE._source_body

PROTOCOL = "sku-body-invariant-choice-v3-native-rakuten-source-v1"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r9"
HOOK_LOCK = threading.RLock()
FIELD_BINDING_AUDIT: dict[str, dict[str, Any]] = {}


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _scripts(raw_html: str) -> list[dict[str, Any]]:
    """Return exact source coordinates for structurally parsed script elements."""
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(raw_html) if char == "\n")

    def offset(pos: tuple[int, int]) -> int:
        line, col = pos
        if line < 1 or line > len(starts):
            raise ValueError("HTML parser returned an invalid line coordinate")
        return starts[line - 1] + col

    class Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.index = 0
            self.current: dict[str, Any] | None = None
            self.results: list[dict[str, Any]] = []
            self.structural_ok = True

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "script":
                return
            if self.current is not None:
                self.structural_ok = False
            start_tag = self.get_starttag_text()
            start = offset(self.getpos())
            if start_tag is None or raw_html[start:start + len(start_tag)] != start_tag:
                self.structural_ok = False
            self.current = {"index": self.index, "attrs": dict(attrs),
                            "content_start": start + len(start_tag), "chunks": []}
            self.index += 1

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() == "script":
                self.handle_starttag(tag, attrs)
                self.handle_endtag(tag)

        def handle_data(self, data: str) -> None:
            if self.current is None:
                return
            start = offset(self.getpos())
            if raw_html[start:start + len(data)] != data:
                self.structural_ok = False
            self.current["chunks"].append((start, data))

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() != "script" or self.current is None:
                return
            end = offset(self.getpos())
            begin = self.current["content_start"]
            content = raw_html[begin:end]
            if "".join(text for _, text in self.current["chunks"]) != content:
                self.structural_ok = False
            self.results.append({**self.current, "content_end": end, "content": content})
            self.current = None

    parser = Parser()
    parser.feed(raw_html)
    parser.close()
    if parser.current is not None:
        parser.structural_ok = False
    if not parser.structural_ok:
        raise ValueError("captured script text cannot be mapped losslessly to raw HTML")
    return parser.results


def _json_lexemes(text: str) -> tuple[Any, dict[tuple[Any, ...], tuple[int, int, Any]]]:
    """Parse JSON while preserving exact source spans of every value string."""
    decoder = json.JSONDecoder()
    strings: dict[tuple[Any, ...], tuple[int, int, Any]] = {}

    def skip(pos: int) -> int:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos

    def parse(pos: int, path: tuple[Any, ...]) -> tuple[Any, int]:
        pos = skip(pos)
        if pos >= len(text):
            raise ValueError("truncated application-data JSON")
        if text[pos] == "{":
            result: dict[str, Any] = {}
            pos = skip(pos + 1)
            if pos < len(text) and text[pos] == "}":
                return result, pos + 1
            while True:
                pos = skip(pos)
                key, key_end = decoder.raw_decode(text, pos)
                if not isinstance(key, str):
                    raise ValueError("application-data object has a non-string key")
                if key in result:
                    raise ValueError("application-data object contains a duplicate key")
                pos = skip(key_end)
                if pos >= len(text) or text[pos] != ":":
                    raise ValueError("application-data object lacks colon")
                child, pos = parse(pos + 1, path + (key,))
                result[key] = child
                pos = skip(pos)
                if pos < len(text) and text[pos] == "}":
                    return result, pos + 1
                if pos >= len(text) or text[pos] != ",":
                    raise ValueError("application-data object delimiter malformed")
                pos += 1
        if text[pos] == "[":
            result_list = []
            pos = skip(pos + 1)
            if pos < len(text) and text[pos] == "]":
                return result_list, pos + 1
            while True:
                child, pos = parse(pos, path + (len(result_list),))
                result_list.append(child)
                pos = skip(pos)
                if pos < len(text) and text[pos] == "]":
                    return result_list, pos + 1
                if pos >= len(text) or text[pos] != ",":
                    raise ValueError("application-data array delimiter malformed")
                pos += 1
        value, end = decoder.raw_decode(text, pos)
        if isinstance(value, str):
            strings[path] = (pos, end, value)
        return value, end

    value, end = parse(0, ())
    if text[skip(end):].strip():
        raise ValueError("trailing material after application-data JSON")
    return value, strings


def _at(value: Any, path: tuple[str, ...]) -> Any:
    node = value
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise KeyError("missing native itemInfoSku path")
        node = node[key]
    return node


def _identity_and_inventory(candidate: Any) -> tuple[dict[str, Any], dict[str, tuple[str, ...]]]:
    if not isinstance(candidate, dict):
        raise ValueError("itemInfoSku mirror is not an object")
    pc = candidate.get("pcFields")
    selectors, rows = candidate.get("variantSelectors"), candidate.get("sku")
    if not isinstance(pc, dict) or not isinstance(pc.get("productDescription"), str) or not pc["productDescription"]:
        raise ValueError("native productDescription is absent or not a string")
    if not isinstance(selectors, list) or not selectors or not isinstance(rows, list) or not rows:
        raise ValueError("native selector/SKU family is incomplete")
    axis_values = []
    for selector in selectors:
        key, label, members = selector.get("key"), selector.get("label"), selector.get("values")
        if not isinstance(key, str) or not key or not isinstance(label, str) or not label or not isinstance(members, list) or not members:
            raise ValueError("native selector axis is malformed")
        values = [member.get("value") if isinstance(member, dict) else None for member in members]
        if any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
            raise ValueError("native selector members are malformed or duplicated")
        axis_values.append((key, label, values))
    rows_by_id: dict[str, tuple[str, ...]] = {}
    observed = [set() for _ in selectors]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("variantId"), str) or not row["variantId"]:
            raise ValueError("native SKU row identity is malformed")
        variant_id, coordinates = row["variantId"], row.get("selectorValues")
        if variant_id in rows_by_id or not isinstance(coordinates, list) or len(coordinates) != len(selectors):
            raise ValueError("native SKU rows contain duplicate IDs or incomplete selector coordinates")
        if any(not isinstance(value, str) or not value for value in coordinates):
            raise ValueError("native SKU row has empty or non-string selector coordinates")
        rows_by_id[variant_id] = tuple(coordinates)
        for index, value in enumerate(coordinates):
            if value not in axis_values[index][2]:
                raise ValueError("native SKU row has a coordinate outside its complete selector family")
            observed[index].add(value)
    if any(observed[i] != set(axis_values[i][2]) for i in range(len(axis_values))):
        raise ValueError("native SKU rows do not cover every declared selector member")
    identity = {"itemId": candidate.get("itemId"), "shopId": candidate.get("shopId"),
        "manageNumber": candidate.get("manageNumber"), "itemNumber": pc.get("itemNumber"),
        "productDescription": pc["productDescription"], "variantSelectors": selectors}
    if any(identity[key] is None or identity[key] == "" for key in ("itemId", "shopId", "manageNumber", "itemNumber")):
        raise ValueError("native item identity is incomplete")
    return identity, rows_by_id


def _raw_html_ref(raw_file: str, digest: str, encoding: str, raw_html: str,
                  start: int, end: int, locator: dict[str, Any]) -> dict[str, Any]:
    if not (0 <= start < end <= len(raw_html)):
        raise ValueError("native source lexeme offset is out of bounds")
    return {"raw_file": raw_file, "sha256": digest, "locator": {
        "kind": "html_text", "encoding": encoding, **locator},
        "start": start, "end": end, "quote": raw_html[start:end],
        "offset_unit": "unicode_code_points"}


def _source_refs(doc: dict[str, Any]) -> list[dict[str, Any]]:
    title = doc.get("title", {})
    refs = list(title.get("source_refs", [])) if isinstance(title, dict) else []
    refs.extend(ref for segment in doc.get("description", {}).get("segments", [])
                for ref in segment.get("source_refs", []))
    return refs


def native_rakuten_blocks(store: Any, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract blocks only from mirrored same-parent native SKU descriptions."""
    if doc.get("side") != "rakuten":
        return []
    audit = {"document_id": doc.get("document_id"), "document_sha256": doc.get("document_sha256"),
        "side": "rakuten", "status": "source_binding_unverified", "native_field_present": False,
        "mirrored_parent_count": 0, "native_variant_id_count": 0, "eligible_block_count": 0,
        "semantic_assessment_performed": False}
    FIELD_BINDING_AUDIT[doc.get("document_sha256", str(id(doc)))] = audit

    def abstain(status: str) -> list[dict[str, Any]]:
        audit["status"] = status
        return []

    raw_sources: dict[tuple[str, str], set[str]] = {}
    for ref in _source_refs(doc):
        raw_file, digest = ref.get("raw_file"), ref.get("sha256")
        locator = ref.get("locator") or {}
        encoding = locator.get("encoding") if isinstance(locator, dict) else None
        if isinstance(raw_file, str) and isinstance(digest, str):
            key = (raw_file.replace("\\", "/"), digest)
            raw_sources.setdefault(key, set())
            if isinstance(encoding, str) and encoding:
                raw_sources[key].add(encoding)
    html_sources = [(raw_file, digest, next(iter(encodings)) if encodings else "EUC-JP")
        for (raw_file, digest), encodings in raw_sources.items() if not raw_file.endswith(".json")]
    if any(len(encodings) > 1 for (raw_file, _), encodings in raw_sources.items() if not raw_file.endswith(".json")):
        return abstain("ambiguous_source_encoding")
    if len(html_sources) != 1:
        return abstain("no_unique_captured_html_source")
    raw_file, digest, encoding = html_sources[0]
    try:
        store.raw(raw_file, digest)
        raw_html = store.text(raw_file, encoding)
        app_scripts = [script for script in _scripts(raw_html)
            if str(script["attrs"].get("id", "")).lower() == "item-page-app-data"
            and str(script["attrs"].get("type", "")).lower().split(";")[0].strip() == "application/json"]
        if len(app_scripts) != 1:
            return abstain("no_unique_native_appdata_script")
        script = app_scripts[0]
        app_data, lexemes = _json_lexemes(script["content"])
        # Also require standard parser agreement before trusting lexical spans.
        if json.loads(script["content"]) != app_data:
            return []
        api_path = ("api", "data", "itemInfoSku")
        new_path = ("newApi", "itemInfoSku")
        api_object, new_object = _at(app_data, api_path), _at(app_data, new_path)
        for candidate in (api_object, new_object):
            pc_fields = candidate.get("pcFields") if isinstance(candidate, dict) else None
            field_value = pc_fields.get("productDescription") if isinstance(pc_fields, dict) else None
            if not isinstance(field_value, str) or not field_value:
                return abstain("native_product_description_missing")
        api_identity, api_rows = _identity_and_inventory(api_object)
        new_identity, new_rows = _identity_and_inventory(new_object)
        if api_identity != new_identity or api_rows != new_rows:
            return abstain("native_mirrored_parent_mismatch")
        api_ids = list(api_rows)
        audit["native_field_present"] = True
        audit["mirrored_parent_count"] = 2
        inventory = BASE._target_sku_inventory(store, doc)
        if inventory.get("status") != "verified_native_axes":
            return abstain("native_target_inventory_unverified")
        inventory_states = inventory.get("all_option_states", [])
        inventory_ids = [state.get("variant_id") for state in inventory_states]
        if len(inventory_ids) != len(set(inventory_ids)) or set(inventory_ids) != set(api_ids):
            return abstain("native_sku_id_set_mismatch")
        inventory_rows: dict[str, tuple[str, ...]] = {}
        for state in inventory_states:
            variant_id = state.get("variant_id")
            values = state.get("values")
            if not isinstance(values, list):
                return abstain("native_sku_coordinate_map_malformed")
            ordered = sorted(values, key=lambda item: item.get("axis_index", -1))
            if ([item.get("axis_index") for item in ordered] != list(range(len(api_identity["variantSelectors"])))
                    or any(not isinstance(item.get("value"), str) or not item["value"] for item in ordered)):
                return abstain("native_sku_coordinate_map_malformed")
            inventory_rows[variant_id] = tuple(item["value"] for item in ordered)
        if inventory_rows != api_rows:
            return abstain("native_sku_coordinate_map_mismatch")
        # Verify selector structure and full member coverage against R8's exact
        # captured selector family; axis order and duplicate captions survive.
        inv_axes = inventory.get("axes", [])
        source_axes = api_object["variantSelectors"]
        if len(inv_axes) != len(source_axes):
            return abstain("native_axis_family_mismatch")
        for i, (source_axis, inv_axis) in enumerate(zip(source_axes, inv_axes)):
            vals = [x["value"] for x in source_axis["values"]]
            if (source_axis["key"] != inv_axis.get("axis_key")
                    or source_axis["label"] != inv_axis.get("axis_name")
                    or vals != [m.get("value") for m in inv_axis.get("members", [])]):
                return abstain("native_axis_family_mismatch")
        desc_path = api_path + ("pcFields", "productDescription")
        desc = api_identity["productDescription"]
        rel_path = "$." + ".".join(desc_path)
        field_start, field_end, decoded_value = lexemes[desc_path]
        if decoded_value != desc:
            return abstain("native_field_lexeme_mismatch")
        # Both exact escaped JSON string lexemes must be present in the same
        # captured script and decode to the byte-identical productDescription.
        mirror_path = new_path + ("pcFields", "productDescription")
        mirror_start, mirror_end, mirror_value = lexemes[mirror_path]
        if mirror_value != desc:
            return abstain("native_mirror_lexeme_mismatch")
        audit.update({"status": "native_field_bound", "native_variant_id_count": len(api_ids),
            "native_field_codepoint_count": len(desc), "native_product_description_sha256": _sha_bytes(desc.encode("utf-8")),
            "script_index": script["index"], "field_json_path": "$.api.data.itemInfoSku.pcFields.productDescription",
            "source_raw_file": raw_file, "source_raw_sha256": digest,
            "mirrored_field_lexeme_count": 2,
            "variant_coordinate_map_sha256": _sha_bytes(json.dumps(
                [[variant_id, list(api_rows[variant_id])] for variant_id in sorted(api_rows)],
                ensure_ascii=False, separators=(",", ":")).encode("utf-8"))})
        field_refs = []
        for json_path, start, end in ((rel_path, field_start, field_end),
                                      ("$." + ".".join(mirror_path), mirror_start, mirror_end)):
            field_refs.append(_raw_html_ref(raw_file, digest, encoding, raw_html,
                script["content_start"] + start, script["content_start"] + end,
                {"path": script["attrs"].get("id"), "script_index": script["index"],
                 "script_locator": "script#item-page-app-data[type=application/json]",
                 "json_path": json_path, "source_object_path": ".".join(json_path.split(".")[:-1])}))
        script_ref = _raw_html_ref(raw_file, digest, encoding, raw_html,
            script["content_start"], script["content_end"],
            {"path": script["attrs"].get("id"), "script_index": script["index"],
             "script_locator": "script#item-page-app-data[type=application/json]",
             "json_path": "$", "source_object_path": "item-page-app-data"})
        html_ref = {"raw_file": raw_file, "sha256": digest,
            "json_path": rel_path, "script_index": script["index"]}
        extracted = HTMLBLOCKS.extract_blocks(desc, html_ref, title="")
        # Verify the decoded field is the exact string whose escaped source
        # lexeme and script parent are pinned above.
        HTMLBLOCKS.verify_blocks(desc, extracted)
        results = []
        for item in extracted:
            # A source block containing any linked material is excluded in full.
            if BASE._anchor_excluded(item) or not item.get("support_text", "").strip():
                continue
            local_ref = item["source_ref"]
            mapped = dict(item)
            mapped["block_id"] = "native-rakuten-description:" + _sha_bytes(
                json.dumps([doc["document_sha256"], script["index"], rel_path,
                            item["block_id"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))[:40]
            mapped["field"] = "native_rakuten_product_description"
            mapped["passage"] = item.get("text", "")
            mapped["source_ref"] = field_refs[0]
            mapped["links_present"] = bool(item.get("links"))
            mapped["native_field_binding"] = {
                "binding_version": PROTOCOL, "field_json_path": rel_path,
                "mirror_json_path": "$." + ".".join(mirror_path),
                "script_index": script["index"],
                "script_locator": "script#item-page-app-data[type=application/json]",
                "script_ref": script_ref, "mirrored_field_refs": field_refs,
                "decoded_field_sha256": _sha_bytes(desc.encode("utf-8")),
                "field_local_block_ref": local_ref,
                "field_local_block_id": item["block_id"],
                "native_identity": {key: api_identity[key] for key in
                    ("itemId", "shopId", "manageNumber", "itemNumber")},
                "native_sku_variant_ids": api_ids}
            results.append(mapped)
        audit["eligible_block_count"] = len(results)
        audit["status"] = "eligible_body_blocks" if results else "field_bound_no_unlinked_blocks"
        return results
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError,
            UnicodeDecodeError, OSError):
        # Ambiguous, incomplete, or malformed source means no eligible body
        # blocks. It is never repaired or interpreted by a Python classifier.
        return abstain("native_source_structure_invalid")


def _source_body(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    result = BASE_SOURCE_BODY(store, doc)
    if doc.get("side") == "rakuten":
        result["native_field_policy"] = (
            "Rakuten body is derived only from mirrored native item-page-app-data productDescription "
            "members inside one itemInfoSku object whose identity, selector families, and complete SKU "
            "variantId set match captured native inventory; no whole-page text is used. Raw source spans "
            "prove provenance only, while field-local HTML blocks are rederived separately.")
    return result


@contextmanager
def _installed_adapter():
    """Temporarily install this adapter without mutating R8 source bytes."""
    with HOOK_LOCK:
        old_blocks, old_body = BASE._native_rakuten_blocks, BASE._source_body
        BASE._native_rakuten_blocks, BASE._source_body = native_rakuten_blocks, _source_body
        try:
            yield
        finally:
            BASE._native_rakuten_blocks, BASE._source_body = old_blocks, old_body


def prepare(input_dir: Path = BASE.DEFAULT_INPUT, source_root: Path = BASE.DEFAULT_SOURCE,
            output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    FIELD_BINDING_AUDIT.clear()
    with _installed_adapter():
        report = BASE.prepare(input_dir, source_root, output_dir)
    raku_docs = [doc for doc in BASE.read_rows(Path(input_dir) / "documents.jsonl")
                 if doc.get("side") == "rakuten"]
    audit_rows = []
    for doc in raku_docs:
        row = FIELD_BINDING_AUDIT.get(doc.get("document_sha256"), {
            "document_id": doc.get("document_id"), "document_sha256": doc.get("document_sha256"),
            "side": "rakuten", "status": "extractor_not_called", "native_field_present": False,
            "mirrored_parent_count": 0, "native_variant_id_count": 0, "eligible_block_count": 0,
            "semantic_assessment_performed": False})
        audit_rows.append(dict(row))
    audit_path = output_dir / "native-rakuten-field-binding-audit.jsonl"
    BASE.write_rows(audit_path, audit_rows)
    adapter_path = Path(__file__).resolve()
    copied_adapter = output_dir / "native-rakuten-body-adapter.py"
    with copied_adapter.open("xb") as stream:
        stream.write(adapter_path.read_bytes())
    report["schema_version"] = PROTOCOL
    report["source_variant_module"] = copied_adapter.name
    report["source_variant_module_sha256"] = BASE.sha_file(copied_adapter)
    report["base_r8_module_sha256"] = BASE.sha_file(HERE / "sku_body_invariant_choice_v3.py")
    report["base_r8_schema_sha256"] = BASE.sha_file(HERE / "sku_body_invariant_choice_v3.schema.json")
    report["native_rakuten_body_document_count"] = sum(
        body.get("side") == "rakuten" and body.get("status") == "eligible_blocks"
        for body in BASE.read_rows(output_dir / "source-bodies.jsonl"))
    report["native_rakuten_field_binding_audit"] = audit_path.name
    report["native_rakuten_field_binding_status_counts"] = {
        status: sum(row.get("status") == status for row in audit_rows)
        for status in sorted({row.get("status") for row in audit_rows})}
    report["native_rakuten_bound_description_document_count"] = sum(
        row.get("status") in {"eligible_body_blocks", "field_bound_no_unlinked_blocks"} for row in audit_rows)
    report["artifact_sha256"][copied_adapter.name] = report["source_variant_module_sha256"]
    report["artifact_sha256"][audit_path.name] = BASE.sha_file(audit_path)
    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def validate_source_response(response: dict[str, Any], pair: dict[str, Any], payload: dict[str, Any],
        source_body: dict[str, Any], source_root: Path) -> dict[str, Any]:
    with _installed_adapter():
        result = BASE.validate_source_response(response, pair, payload, source_body, source_root)
        binding = source_body.get("source_document_binding") or {}
        title = {"source_refs": binding.get("title_refs", [])}
        desc_refs = binding.get("description_refs", [])
        source_doc = {"document_id": source_body.get("document_id"),
            "document_sha256": source_body.get("document_sha256"), "side": source_body.get("side"),
            "title": title, "description": {"segments": [{"source_refs": [ref]} for ref in desc_refs]}}
        store = BASE.NLI.SOURCES.RawStore(Path(source_root))
        rebuilt = _source_body(store, source_doc)
    if source_body.get("blocks") != rebuilt.get("blocks"):
        raise ValueError("native Rakuten productDescription block/provenance inventory failed exact rederivation")
    for block in rebuilt.get("blocks", []):
        metadata = block.get("native_field_binding")
        if block.get("field") != "native_rakuten_product_description":
            continue
        if not isinstance(metadata, dict) or metadata.get("binding_version") != PROTOCOL:
            raise ValueError("native Rakuten block lacks exact field binding metadata")
        for span in [metadata.get("script_ref"), *metadata.get("mirrored_field_refs", [])]:
            if not isinstance(span, dict) or not BASE.NLI.SOURCES.verify_span(store, span):
                raise ValueError("native Rakuten script/field raw lexeme span verification failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BASE.DEFAULT_INPUT)
    parser.add_argument("--source-root", type=Path, default=BASE.DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.source_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
