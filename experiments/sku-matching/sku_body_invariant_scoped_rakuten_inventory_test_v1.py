#!/usr/bin/env python3
"""Scoped source-only Rakuten inventory adapter for indexed TEST20.

This adapter reuses the frozen R8/R9 body and response protocol while replacing
only Rakuten's broad variant-marker inventory reader with exact native
item-page-app-data SKU arrays. Hooks are temporary and restored in ``finally``.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import prepare_indexed_test_native_source_v1 as INDEXED  # noqa: E402
import sku_body_invariant_native_rakuten_source_v1 as NATIVE  # noqa: E402

BASE = NATIVE.BASE
BASE_TARGET_INVENTORY = BASE._target_sku_inventory
PROTOCOL = "sku-body-invariant-scoped-rakuten-inventory-test-v1"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-final-body-indexed-test-20261013-v1/scoped-native-source-v1"
HOOK_LOCK = threading.RLock()
INVENTORY_AUDIT: dict[str, dict[str, Any]] = {}


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _span_id(span: dict[str, Any]) -> str:
    return BASE.NLI._span_id(span)


def _json_leaf_lexemes(text: str) -> tuple[Any, dict[tuple[Any, ...], tuple[int, int, Any]]]:
    """Parse duplicate-free JSON and retain exact spans for every scalar leaf."""
    decoder = json.JSONDecoder()
    leaves: dict[tuple[Any, ...], tuple[int, int, Any]] = {}

    def skip(pos: int) -> int:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos

    def parse(pos: int, path: tuple[Any, ...]) -> tuple[Any, int]:
        pos = skip(pos)
        if pos >= len(text):
            raise ValueError("truncated native application-data JSON")
        if text[pos] == "{":
            obj: dict[str, Any] = {}
            pos = skip(pos + 1)
            if pos < len(text) and text[pos] == "}":
                return obj, pos + 1
            while True:
                pos = skip(pos)
                key, key_end = decoder.raw_decode(text, pos)
                if not isinstance(key, str) or key in obj:
                    raise ValueError("native application-data object key malformed or duplicated")
                pos = skip(key_end)
                if pos >= len(text) or text[pos] != ":":
                    raise ValueError("native application-data object lacks colon")
                child, pos = parse(pos + 1, path + (key,))
                obj[key] = child
                pos = skip(pos)
                if pos < len(text) and text[pos] == "}":
                    return obj, pos + 1
                if pos >= len(text) or text[pos] != ",":
                    raise ValueError("native application-data object delimiter malformed")
                pos += 1
        if text[pos] == "[":
            array: list[Any] = []
            pos = skip(pos + 1)
            if pos < len(text) and text[pos] == "]":
                return array, pos + 1
            while True:
                child, pos = parse(pos, path + (len(array),))
                array.append(child)
                pos = skip(pos)
                if pos < len(text) and text[pos] == "]":
                    return array, pos + 1
                if pos >= len(text) or text[pos] != ",":
                    raise ValueError("native application-data array delimiter malformed")
                pos += 1
        value, end = decoder.raw_decode(text, pos)
        leaves[path] = (pos, end, value)
        return value, end

    value, end = parse(0, ())
    if text[skip(end):].strip():
        raise ValueError("trailing material after native application-data JSON")
    return value, leaves


def _field_span(raw_file: str, digest: str, encoding: str, raw_html: str,
                script: dict[str, Any], json_path: tuple[Any, ...],
                lexeme: tuple[int, int, Any], kind: str) -> dict[str, Any]:
    start, end, decoded = lexeme
    ref = NATIVE._raw_html_ref(raw_file, digest, encoding, raw_html,
        script["content_start"] + start, script["content_start"] + end,
        {"path": script["attrs"].get("id"), "script_index": script["index"],
         "script_locator": "script#item-page-app-data[type=application/json]",
         "json_path": "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in json_path)})
    # The span quote is the exact raw JSON string lexeme, including JSON escapes.
    ref["decoded_value_sha256"] = _sha_bytes(str(decoded).encode("utf-8"))
    ref["source_path"] = list(json_path)
    ref["evidence_id"] = kind + ":" + _span_id(ref)
    return ref


def _unknown(doc: dict[str, Any], reason: str) -> dict[str, Any]:
    row = {"document_id": doc.get("document_id"),
           "document_sha256": doc.get("document_sha256"),
           "side": doc.get("side"), "status": reason,
           "axis_count": 0, "native_row_count": 0,
           "semantic_assessment_performed": False}
    INVENTORY_AUDIT[str(doc.get("document_sha256", id(doc)))] = row
    return {"status": "unknown_native_target_sku_inventory",
            "document_sha256": doc.get("document_sha256"), "axes": [],
            "all_option_states": [], "source_row_lexeme_refs": [],
            "source_parent_lexeme_refs": [],
            "row_completeness_basis": reason}


def _native_parent(candidate: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(candidate, dict):
        raise ValueError("native itemInfoSku parent is not an object")
    pc = candidate.get("pcFields")
    selectors = candidate.get("variantSelectors")
    rows = candidate.get("sku")
    if not isinstance(pc, dict) or not isinstance(selectors, list) or not selectors:
        raise ValueError("native selector family is incomplete")
    if not isinstance(rows, list) or not rows:
        raise ValueError("native actual SKU row array is empty")

    axis_defs: list[dict[str, Any]] = []
    for selector_index, selector in enumerate(selectors):
        if not isinstance(selector, dict):
            raise ValueError("native selector definition is malformed")
        key, label, values = selector.get("key"), selector.get("label"), selector.get("values")
        if (not isinstance(key, str) or not key or not isinstance(label, str) or not label
                or not isinstance(values, list) or not values):
            raise ValueError("native selector key, label, or family is incomplete")
        members = []
        seen: set[str] = set()
        for member_index, member in enumerate(values):
            value = member.get("value") if isinstance(member, dict) else None
            if not isinstance(value, str) or not value or value in seen:
                raise ValueError("native selector values are empty or duplicated")
            seen.add(value)
            members.append({"index": member_index, "value": value,
                "source_path": ["api", "data", "itemInfoSku", "variantSelectors",
                    selector_index, "values", member_index, "value"]})
        axis_defs.append({"axis_index": selector_index, "axis_key": key,
            "axis_name": label, "members": members,
            "source_path": ["api", "data", "itemInfoSku", "variantSelectors", selector_index]})

    rows_by_id: dict[str, list[str]] = {}
    parsed_rows: list[dict[str, Any]] = []
    observed = [set() for _ in axis_defs]
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("native SKU array contains a non-object entry")
        variant_id, coordinates = row.get("variantId"), row.get("selectorValues")
        if not isinstance(variant_id, str) or not variant_id:
            raise ValueError("native SKU row lacks a nonempty variantId")
        if variant_id in rows_by_id:
            raise ValueError("native SKU array contains duplicate variantId")
        if not isinstance(coordinates, list) or len(coordinates) != len(axis_defs):
            raise ValueError("native SKU row does not specify every selector coordinate")
        if any(not isinstance(v, str) or not v for v in coordinates):
            raise ValueError("native SKU selector coordinate is empty or non-string")
        for index, value in enumerate(coordinates):
            if value not in {m["value"] for m in axis_defs[index]["members"]}:
                raise ValueError("native SKU row coordinate is outside declared family")
            observed[index].add(value)
        rows_by_id[variant_id] = list(coordinates)
        parsed_rows.append({"variant_id": variant_id, "coordinates": list(coordinates),
            "source_path": ["api", "data", "itemInfoSku", "sku", row_index],
            "variant_id_path": ["api", "data", "itemInfoSku", "sku", row_index, "variantId"],
            "coordinate_paths": [["api", "data", "itemInfoSku", "sku", row_index,
                                  "selectorValues", axis_index]
                                 for axis_index in range(len(coordinates))]})
    for index, axis in enumerate(axis_defs):
        if observed[index] != {m["value"] for m in axis["members"]}:
            raise ValueError("native actual SKU rows omit a declared family member")

    identity = {"itemId": candidate.get("itemId"), "shopId": candidate.get("shopId"),
        "manageNumber": candidate.get("manageNumber"), "itemNumber": pc.get("itemNumber"),
        "variantSelectors": selectors}
    if any(identity[k] is None or identity[k] == "" for k in
           ("itemId", "shopId", "manageNumber", "itemNumber")):
        raise ValueError("native item parent identity is incomplete")
    return identity, axis_defs, parsed_rows


def _scoped_rakuten_inventory(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    """Rebuild only actual main-parent Rakuten rows from mirrored appdata."""
    if doc.get("side") != "rakuten":
        return BASE_TARGET_INVENTORY(store, doc)
    try:
        raw_sources: dict[tuple[str, str], set[str]] = {}
        for ref in NATIVE._source_refs(doc):
            raw_file, digest = ref.get("raw_file"), ref.get("sha256")
            loc = ref.get("locator") or {}
            encoding = loc.get("encoding") if isinstance(loc, dict) else None
            if isinstance(raw_file, str) and isinstance(digest, str):
                key = (raw_file.replace("\\", "/"), digest)
                raw_sources.setdefault(key, set())
                if isinstance(encoding, str) and encoding:
                    raw_sources[key].add(encoding)
        html_sources = [(name, digest, next(iter(encs)) if encs else "EUC-JP")
                        for (name, digest), encs in raw_sources.items() if not name.endswith(".json")]
        if any(len(encs) > 1 for (name, _), encs in raw_sources.items() if not name.endswith(".json")):
            return _unknown(doc, "ambiguous_source_encoding")
        if len(html_sources) != 1:
            return _unknown(doc, "no_unique_captured_html_source")
        raw_file, digest, encoding = html_sources[0]
        store.raw(raw_file, digest)
        raw_html = store.text(raw_file, encoding)
        scripts = [s for s in NATIVE._scripts(raw_html)
            if str(s["attrs"].get("id", "")).lower() == "item-page-app-data"
            and str(s["attrs"].get("type", "")).lower().split(";")[0].strip() == "application/json"]
        if len(scripts) != 1:
            return _unknown(doc, "no_unique_native_appdata_script")
        script = scripts[0]
        appdata, lexemes = _json_leaf_lexemes(script["content"])
        if json.loads(script["content"]) != appdata:
            return _unknown(doc, "application_json_parser_disagreement")
        api_path = ("api", "data", "itemInfoSku")
        mirror_path = ("newApi", "itemInfoSku")
        api_obj, mirror_obj = NATIVE._at(appdata, api_path), NATIVE._at(appdata, mirror_path)
        api_identity, api_axes, api_rows = _native_parent(api_obj)
        mirror_identity, mirror_axes, mirror_rows = _native_parent(mirror_obj)
        if api_identity != mirror_identity or api_axes != mirror_axes or api_rows != mirror_rows:
            return _unknown(doc, "native_mirror_identity_family_or_row_mismatch")

        def span_at(path: list[Any], category: str) -> dict[str, Any]:
            key = tuple(path)
            if key not in lexemes:
                raise ValueError("native source scalar lacks exact JSON lexeme: " + repr(key))
            return _field_span(raw_file, digest, encoding, raw_html, script, key,
                               lexemes[key], category)

        axes = []
        parent_refs = []
        for axis_i, axis in enumerate(api_axes):
            axis_path = [*api_path, "variantSelectors", axis_i]
            key_span = span_at([*axis_path, "key"], "native-axis-key")
            label_span = span_at([*axis_path, "label"], "native-axis-label")
            members = []
            for member in axis["members"]:
                member_path = member["source_path"]
                span = span_at(member_path, "native-axis-value")
                members.append({"index": member["index"], "value": member["value"],
                    "evidence_id": span["evidence_id"], "span": span})
                parent_refs.append(span)
            axes.append({"axis_name": axis["axis_name"], "axis_key": axis["axis_key"],
                "axis_index": axis_i, "axis_name_span": label_span,
                "axis_key_span": key_span, "members": members})
            parent_refs.extend([key_span, label_span])

        states, row_refs = [], []
        for state_i, row in enumerate(api_rows):
            vid_span = span_at(row["variant_id_path"], "native-variant-id")
            values = []
            member_ids = []
            row_coordinate_spans = []
            for axis_i, value in enumerate(row["coordinates"]):
                value_span = span_at(row["coordinate_paths"][axis_i], "native-row-coordinate")
                axis = axes[axis_i]
                member = next(m for m in axis["members"] if m["value"] == value)
                values.append({"axis_index": axis_i, "axis_key": axis["axis_key"],
                    "axis_name": axis["axis_name"], "value": value,
                    "member_evidence_id": member["evidence_id"],
                    "selected_value_evidence_id": value_span["evidence_id"]})
                member_ids.append(member["evidence_id"])
                row_coordinate_spans.append(value_span)
                parent_refs.append(value_span)
            row_refs.append({"variant_id": row["variant_id"], "variant_id_span": vid_span,
                "coordinate_spans": row_coordinate_spans,
                "variant_id_evidence_id": vid_span["evidence_id"]})
            parent_refs.append(vid_span)
            states.append({"state_index": state_i, "values": values,
                "member_evidence_ids": member_ids,
                "row_ref_id": "target-native-row:" + doc["document_sha256"][:16] + ":" + row["variant_id"],
                "variant_id": row["variant_id"],
                "variant_id_evidence_id": vid_span["evidence_id"],
                "coordinate_evidence_ids": [s["evidence_id"] for s in row_coordinate_spans]})

        # Bind product identity fields and the complete parent JSON lexemes too.
        identity_paths = [(*api_path, "itemId"), (*api_path, "shopId"),
            (*api_path, "manageNumber"), (*api_path, "pcFields", "itemNumber")]
        for path in identity_paths:
            parent_refs.append(span_at(list(path), "native-item-identity"))
        # Objects have no single string lexeme. Record exact raw source as the
        # enclosing script plus the exact string-leaf refs above.
        script_ref = NATIVE._raw_html_ref(raw_file, digest, encoding, raw_html,
            script["content_start"], script["content_end"],
            {"path": script["attrs"].get("id"), "script_index": script["index"],
             "script_locator": "script#item-page-app-data[type=application/json]",
             "json_path": "$", "source_object_path": "item-page-app-data"})
        mirror_desc_path = [*mirror_path, "pcFields", "productDescription"]
        main_desc_path = [*api_path, "pcFields", "productDescription"]
        for path in (main_desc_path, mirror_desc_path):
            leaf = lexemes.get(tuple(path))
            if leaf is not None:
                parent_refs.append(span_at(list(path), "native-product-description"))

        identity_hash = BASE.sha_bytes(BASE.canon({"identity": api_identity,
            "axes": [[a["axis_key"], a["axis_name"], [m["value"] for m in a["members"]]] for a in api_axes],
            "rows": [[r["variant_id"], r["coordinates"]] for r in api_rows]}))
        for ref in parent_refs:
            if not BASE.NLI.SOURCES.verify_span(store, ref):
                return _unknown(doc, "native_inventory_raw_span_mismatch")
        if not BASE.NLI.SOURCES.verify_span(store, script_ref):
            return _unknown(doc, "native_script_raw_span_mismatch")
        audit = {"document_id": doc.get("document_id"),
            "document_sha256": doc.get("document_sha256"), "side": "rakuten",
            "status": "verified_native_inventory", "raw_file": raw_file,
            "source_sha256": digest, "script_index": script["index"],
            "main_parent_path": "$.api.data.itemInfoSku", "mirror_parent_path": "$.newApi.itemInfoSku",
            "identity_and_full_rows_sha256": identity_hash, "axis_count": len(axes),
            "native_row_count": len(states), "semantic_assessment_performed": False}
        INVENTORY_AUDIT[str(doc["document_sha256"])] = audit
        return {"status": "verified_native_axes", "document_sha256": doc["document_sha256"],
            "raw_file": raw_file, "source_sha256": digest, "axes": axes,
            "all_option_states": states, "native_row_refs": [],
            "source_row_lexeme_refs": row_refs,
            "source_parent_lexeme_refs": parent_refs + [script_ref],
            "row_completeness_basis": "exact main and mirrored native itemInfoSku selector families and ordered variantId coordinate maps; every actual row retained; no Cartesian expansion"}
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError,
            UnicodeDecodeError, OSError) as exc:
        return _unknown(doc, "native_inventory_structure_invalid:" + type(exc).__name__ + ":" + str(exc))


@contextmanager
def _installed_inventory_hook():
    with HOOK_LOCK:
        old = BASE._target_sku_inventory
        BASE._target_sku_inventory = _scoped_rakuten_inventory
        try:
            yield
        finally:
            BASE._target_sku_inventory = old


def prepare(input_dir: Path, source_root: Path, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    INVENTORY_AUDIT.clear()
    old_manifest = BASE._manifest
    BASE._manifest = INDEXED.manifest
    try:
        with _installed_inventory_hook():
            report = NATIVE.prepare(Path(input_dir), Path(source_root), output_dir)
    finally:
        BASE._manifest = old_manifest
    docs = BASE.read_rows(Path(input_dir) / "documents.jsonl")
    audit_rows = []
    for doc in docs:
        if doc.get("side") != "rakuten":
            continue
        audit_rows.append(dict(INVENTORY_AUDIT.get(str(doc.get("document_sha256")), {
            "document_id": doc.get("document_id"),
            "document_sha256": doc.get("document_sha256"), "side": "rakuten",
            "status": "inventory_extractor_not_called", "axis_count": 0,
            "native_row_count": 0, "semantic_assessment_performed": False})))
    audit_path = output_dir / "scoped-rakuten-inventory-audit.jsonl"
    BASE.write_rows(audit_path, audit_rows)
    adapter_path = Path(__file__).resolve()
    copied = output_dir / "scoped-rakuten-inventory-adapter.py"
    with copied.open("xb") as stream:
        stream.write(adapter_path.read_bytes())
    report["schema_version"] = PROTOCOL
    report["dataset"] = "sku-next-body-indexed-test-20261013-v1"
    report["split"] = "test"
    report["independent_test"] = True
    report["test_split_read"] = True
    report["source_input_manifest_sha256"] = BASE.sha_file(Path(input_dir) / "manifest.json")
    report["scoped_inventory_module"] = copied.name
    report["scoped_inventory_module_sha256"] = BASE.sha_file(copied)
    report["scoped_rakuten_inventory_status_counts"] = {
        status: sum(row.get("status") == status for row in audit_rows)
        for status in sorted({row.get("status") for row in audit_rows})}
    report["scoped_rakuten_inventory_audit"] = audit_path.name
    report["artifact_sha256"][copied.name] = BASE.sha_file(copied)
    report["artifact_sha256"][audit_path.name] = BASE.sha_file(audit_path)
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def validate_source_response(response: dict[str, Any], pair: dict[str, Any], payload: dict[str, Any],
        source_body: dict[str, Any], source_root: Path) -> dict[str, Any]:
    with _installed_inventory_hook():
        result = NATIVE.validate_source_response(response, pair, payload, source_body, source_root)
        binding = source_body.get("source_document_binding") or {}
        source_doc = {"document_id": source_body.get("document_id"),
            "document_sha256": source_body.get("document_sha256"), "side": source_body.get("side"),
            "title": {"source_refs": binding.get("title_refs", [])},
            "description": {"segments": [{"source_refs": [r]}
                for r in binding.get("description_refs", [])]}}
        store = BASE.NLI.SOURCES.RawStore(Path(source_root))
        rebuilt = _scoped_rakuten_inventory(store, source_doc)
    if payload.get("context", {}).get("target_native_sku_inventory") != BASE._public_inventory(rebuilt):
        raise ValueError("scoped native target inventory failed source rederivation")
    for ref in rebuilt.get("source_parent_lexeme_refs", []):
        if not BASE.NLI.SOURCES.verify_span(store, ref):
            raise ValueError("scoped native parent lexeme failed raw source verification")
    for row in rebuilt.get("source_row_lexeme_refs", []):
        if not BASE.NLI.SOURCES.verify_span(store, row["variant_id_span"]):
            raise ValueError("scoped native variantId failed raw source verification")
        for span in row["coordinate_spans"]:
            if not BASE.NLI.SOURCES.verify_span(store, span):
                raise ValueError("scoped native selector coordinate failed raw source verification")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.source_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
