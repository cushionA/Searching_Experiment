#!/usr/bin/env python3
"""Apply the user's exact four-field NEXT_DATA description collector.

Description text and a source-bound title are new inputs; original SKU facts,
whole options, and native inventory stay fixed. Exact JavaScript replay binds
normalized quotes to pinned raw captures. Frozen semantic validation is reused.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping
from urllib.parse import urlsplit

import sku_body_invariant_native_rakuten_source_v1 as NATIVE
import sku_nextdata_table_view_v1 as TABLE
import sku_body_invariant_scoped_rakuten_inventory_test_v1 as SCOPED

BODY = NATIVE.BASE
PROTOCOL = "sku-live-nextdata-api-leaf-equivalence-v1"
ITEM_PATH = "$.props.pageProps.initialState.itemDetails.itemPageInfo.itemInfo"
EXTRA_PATH = ITEM_PATH + ".extraItemComment"
NATIVE_FIELDS = ("itemName", "itemTitle", "itemComment", "detailComment",
                 "extraItemComment", "extraItemCommentWithVideo", "specNames",
                 "specValues", "skuInfo.header", "skuInfo.footer")
DEFAULT_LIVE_ROOT = TABLE.ROOT / ".lab-output/sku-nextdata-table-20261013-v1/live-nextdata-captures-v1"
EXTRACTOR = Path(__file__).with_name("collect_au_description_v1.cjs")
CHEERIO_ROOT = TABLE.ROOT / ".deps/sku-nextdata-cheerio"
DESCRIPTION_PROTOCOL = "sku-user-cheerio-native-description-source-v1"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read(root: Path, rel: str, expected: str) -> bytes:
    root = Path(root).resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("capture path escapes source root")
    raw = path.read_bytes()
    if _sha(raw) != expected:
        raise ValueError("capture SHA-256 mismatch: " + rel)
    return raw


@lru_cache(maxsize=128)
def _parse_live(page: bytes, script: bytes) -> dict[str, Any]:
    scripts = [s for s in NATIVE._scripts(page.decode("utf-8"))
               if s["attrs"].get("id") == "__NEXT_DATA__"]
    if len(scripts) != 1 or scripts[0]["content"].encode("utf-8") != script:
        raise ValueError("NEXT_DATA raw script does not rederive from captured page")
    parsed, _ = NATIVE._json_lexemes(script.decode("utf-8"))
    if parsed != json.loads(script) or not isinstance(parsed, dict):
        raise ValueError("NEXT_DATA JSON parsing disagreement")
    return parsed


def _leaf(obj: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    try:
        return True, BODY.NLI.SOURCES.resolve_json_path(obj, path)
    except (KeyError, IndexError, TypeError):
        return False, None


def _manifest(live_manifest: Path | Mapping[str, Any], live_root: Path | None):
    if isinstance(live_manifest, Mapping):
        obj = deepcopy(dict(live_manifest))
        root = Path(live_root or DEFAULT_LIVE_ROOT).resolve()
        digest = _sha(BODY.canon(obj))
    else:
        path = Path(live_manifest).resolve()
        if path.is_dir():
            path /= "capture-inventory.json"
        raw = path.read_bytes()
        obj, digest = json.loads(raw), _sha(raw)
        root = Path(live_root or path.parent).resolve()
    if not isinstance(obj, dict) or not isinstance(obj.get("product_records"), list):
        raise ValueError("live capture manifest lacks product_records")
    records = {}
    for record in obj["product_records"]:
        key = (record["logical_au_raw_file"], record["logical_au_raw_capture_sha256"])
        if key in records:
            raise ValueError("duplicate old-source identity in live capture manifest")
        records[key] = record
    return root, digest, records


def _verify_document(source_body: Mapping[str, Any], records: Mapping[Any, Any],
                     source_root: Path, live_root: Path) -> dict[str, Any]:
    refs = TABLE._source_refs(source_body)
    if len(refs) != 1:
        raise ValueError("AU body lacks one original raw source identity")
    ref = refs[0]
    key = (ref["raw_file"], ref["sha256"])
    if key not in records:
        raise ValueError("AU product lacks an independently captured NEXT_DATA record")
    record = records[key]
    product_id = str(record["product_id"])
    if (record.get("status") != 200 or record.get("nextdata_status") != "present"
            or record.get("json_path") != EXTRA_PATH):
        raise ValueError("live capture does not supply the requested NEXT_DATA field")
    for key_url in ("requested_url", "final_url"):
        url = urlsplit(record[key_url])
        if url.hostname != "wowma.jp" or url.path != "/item/" + product_id:
            raise ValueError("live capture URL does not bind the requested product")
    old = json.loads(_read(source_root, ref["raw_file"], ref["sha256"]))
    if str((old.get("itemInfo") or {}).get("lotNumber")) != product_id:
        raise ValueError("original API lotNumber does not bind the live product")
    page = _read(live_root, record["page_raw_file"], record["page_sha256"])
    script = _read(live_root, record["nextdata_raw_file"], record["nextdata_raw_script_sha256"])
    live = _parse_live(page, script)
    item = BODY.NLI.SOURCES.resolve_json_path(live, ITEM_PATH)
    if (not isinstance(item, dict) or str(item.get("lotNumber")) != product_id
            or str((live.get("query") or {}).get("item_id")) != product_id):
        raise ValueError("NEXT_DATA query/lotNumber does not bind the requested product")
    old_leaf = _leaf(old, TABLE.EXTRA_PATH)
    live_leaf = _leaf(live, EXTRA_PATH)
    if BODY.canon(old_leaf) != BODY.canon(live_leaf):
        raise ValueError("live NEXT_DATA extraItemComment differs from original API field")
    present, value = live_leaf
    if present and not isinstance(value, str):
        raise ValueError("NEXT_DATA extraItemComment is not a string leaf")
    digest = _sha(value.encode("utf-8")) if present else None
    if (record.get("field_status") != ("resolved" if present else "missing")
            or record.get("value_sha256") != digest
            or record.get("value_chars") != (len(value) if present else 0)):
        raise ValueError("NEXT_DATA field manifest does not rederive")

    # The existing parser also verifies original API source spans. Equal field
    # bytes imply identical DOM/table scopes; all nested table nodes are kept.
    tables = TABLE.table_html_audit(source_body, source_root)
    saved = record.get("tables")
    if (not isinstance(saved, list) or record.get("table_count") != tables["table_count"]
            or len(saved) != tables["table_count"]):
        raise ValueError("NEXT_DATA complete table inventory differs from equal API field")
    for actual, saved_table in zip(tables["tables"], saved):
        for name in ("table_ordinal", "dom_path", "start", "end", "html_sha256", "html_chars"):
            if actual[name] != saved_table.get(name):
                raise ValueError("NEXT_DATA table scope/hash does not rederive")
        table_ref = saved_table.get("source_ref") or {}
        expected_ref = {"raw_file": record["nextdata_raw_file"],
                        "sha256": record["nextdata_raw_script_sha256"],
                        "json_path": EXTRA_PATH, "decoded_value_sha256": digest,
                        "start": actual["start"], "end": actual["end"],
                        "quote_sha256": actual["html_sha256"], "dom_path": actual["dom_path"]}
        if any(table_ref.get(k) != v for k, v in expected_ref.items()):
            raise ValueError("NEXT_DATA table source identity/span does not rederive")
        rel = (Path(record["nextdata_raw_file"]).parent / table_ref["quote_file"]).as_posix()
        if _read(live_root, rel, actual["html_sha256"]) != value[actual["start"]:actual["end"]].encode("utf-8"):
            raise ValueError("saved NEXT_DATA table is not the original leaf slice")

    comparisons = {field: BODY.canon(_leaf(old, "$.itemInfo." + field)) ==
                   BODY.canon(_leaf(live, ITEM_PATH + "." + field)) for field in NATIVE_FIELDS}
    body_leaves_equal = True
    for block in source_body.get("blocks", []):
        path = (block.get("source_ref", {}).get("locator") or {}).get("json_path", "")
        if not path.startswith("$.itemInfo."):
            raise ValueError("AU body block is outside native itemInfo")
        body_leaves_equal &= BODY.canon(_leaf(old, path)) == BODY.canon(
            _leaf(live, ITEM_PATH + path[len("$.itemInfo"):]))
    allowed_ids = set(TABLE.table_block_ids(source_body, source_root))
    allowed_blocks = [{"block_id": block["block_id"],
                       "support_text_sha256": _sha(block["support_text"].encode("utf-8"))}
                      for block in source_body.get("blocks", []) if block["block_id"] in allowed_ids]
    return {"document_sha256": source_body["document_sha256"], "product_id": product_id,
            "original_api_raw_file": ref["raw_file"], "original_api_raw_sha256": ref["sha256"],
            "page_raw_file": record["page_raw_file"], "page_sha256": record["page_sha256"],
            "nextdata_raw_file": record["nextdata_raw_file"],
            "nextdata_raw_script_sha256": record["nextdata_raw_script_sha256"],
            "field_json_path": EXTRA_PATH, "field_present": present,
            "field_sha256": digest, "field_chars": len(value) if present else 0,
            "table_count": tables["table_count"], "extra_field_exactly_equal": True,
            "allowed_table_blocks": allowed_blocks,
            "native_field_exact_equality": comparisons,
            "all_original_body_leaves_exactly_equal": body_leaves_equal,
            "old_sku_snapshot_with_later_live_capture": True,
            "citation_policy": "original API spans reused only after exact live NEXT_DATA decoded-field equality; independent live page/script/table pins retained",
            "semantic_truth_verified": False}


def verify_nextdata_equivalence(source_bodies_by_sha: Mapping[str, Mapping[str, Any]],
                               live_manifest: Path | Mapping[str, Any],
                               source_root: Path = TABLE.DEFAULT_SOURCE_ROOT,
                               live_root: Path | None = None) -> dict[str, Any]:
    """Verify every supplied AU source against pinned actual NEXT_DATA captures."""
    root, manifest_sha, records = _manifest(live_manifest, live_root)
    docs = {}
    for digest, body in source_bodies_by_sha.items():
        if body.get("document_sha256") != digest:
            raise ValueError("source body map key/document SHA mismatch")
        if body.get("side") == "au":
            docs[digest] = _verify_document(body, records, Path(source_root), root)
    return {"schema_version": PROTOCOL, "live_root": str(root),
            "live_capture_manifest_sha256": manifest_sha, "au_document_count": len(docs), "product_count": len(docs),
            "exact_equal_field_count": sum(p["field_present"] for p in docs.values()),
            "both_missing_field_count": sum(not p["field_present"] for p in docs.values()), "mismatch_count": 0,
            "documents": docs, "model_inference_run": False,
            "labels_read": False, "semantic_truth_verified": False}


def _extractor_pins() -> dict[str, str]:
    package = json.loads((CHEERIO_ROOT / "node_modules/cheerio/package.json").read_bytes())
    if package.get("version") != "1.1.2":
        raise ValueError("description extractor requires pinned Cheerio 1.1.2")
    return {"extractor_sha256": _sha(EXTRACTOR.read_bytes()), "cheerio_version": "1.1.2",
            "dependency_lock_sha256": _sha((CHEERIO_ROOT / "package-lock.json").read_bytes())}


@lru_cache(maxsize=128)
def _collect_description(page: bytes, product_id: str, pins_json: bytes) -> dict[str, Any]:
    # Cache keys contain the independently verified original page bytes and all
    # transformation pins. Semantic model output is never produced here.
    result = subprocess.run(["node", str(EXTRACTOR)], input=BODY.canon({
        "html": page.decode("utf-8"), "expectedProductId": product_id}),
        capture_output=True, check=True)
    obj = json.loads(result.stdout)
    if not isinstance(obj, dict) or obj.get("productId") != product_id:
        raise ValueError("exact user description extractor returned a different product")
    return obj


def _capture_page(source_body: Mapping[str, Any], records: Mapping[Any, Any],
                  source_root: Path, live_root: Path):
    refs = TABLE._source_refs(source_body)
    if len(refs) != 1:
        raise ValueError("AU body lacks one original source binding")
    ref = refs[0]
    record = records.get((ref["raw_file"], ref["sha256"]))
    if not isinstance(record, Mapping):
        raise ValueError("AU body has no exact independently captured NEXT_DATA record")
    product_id = str(record["product_id"])
    for key in ("requested_url", "final_url"):
        if key == "final_url" and not record.get(key):
            continue
        url = urlsplit(record[key])
        if url.hostname != "wowma.jp" or url.path != "/item/" + product_id:
            raise ValueError("live page URL does not match the bound product")
    old = json.loads(_read(source_root, ref["raw_file"], ref["sha256"]))
    if str((old.get("itemInfo") or {}).get("lotNumber")) != product_id:
        raise ValueError("original API lotNumber does not bind the live product")
    if not record.get("page_raw_file"):
        return record, None
    page = _read(live_root, record["page_raw_file"], record["page_sha256"])
    return record, page


def _capture(source_body: Mapping[str, Any], records: Mapping[Any, Any],
             source_root: Path, live_root: Path):
    record, page = _capture_page(source_body, records, source_root, live_root)
    product_id = str(record["product_id"])
    if record.get("status") != 200 or record.get("nextdata_status") != "present" or page is None:
        raise ValueError("live body capture is unavailable; no live provenance can be claimed")
    raw_script = _read(live_root, record["nextdata_raw_file"], record["nextdata_raw_script_sha256"])
    parsed = _parse_live(page, raw_script)
    item = BODY.NLI.SOURCES.resolve_json_path(parsed, ITEM_PATH)
    if (not isinstance(item, dict) or str(item.get("lotNumber")) != product_id
            or str((parsed.get("query") or {}).get("item_id")) != product_id):
        raise ValueError("live query/lotNumber identity differs from original product binding")
    return record, page, parsed


def _description_body(source_body: Mapping[str, Any], records: Mapping[Any, Any],
                      source_root: Path, live_root: Path, pins: Mapping[str, str]):
    record, page = _capture_page(source_body, records, source_root, live_root)
    if page is None or record.get("status") != 200:
        collected = {"productId": str(record["product_id"]), "status": "failure", "sections": [],
                     "missingJsonPaths": [], "reason": "LIVE_CAPTURE_UNAVAILABLE"}
    else:
        collected = deepcopy(_collect_description(page, str(record["product_id"]), BODY.canon(pins)))
    doc_sha = source_body["document_sha256"]
    raw_file, raw_sha = record.get("nextdata_raw_file"), record.get("nextdata_raw_script_sha256")
    store = BODY.NLI.SOURCES.RawStore(live_root)
    blocks, sections = [], []
    has_text = collected.get("status") == "collected" and any(
        section.get("text") for section in collected.get("sections", []))
    if has_text or collected.get("sections"):
        _, _, parsed = _capture(source_body, records, source_root, live_root)
        store.raw(raw_file, raw_sha)
    elif raw_file and raw_sha:
        # Failure fallback creates no new evidence, while available raw script
        # bytes still retain their independent capture hash.
        _read(live_root, raw_file, raw_sha)

    def block(text, field, refs, transformation):
        identity = {"document_sha256": doc_sha, "raw_script_sha256": raw_sha,
                    "field": field, "source_refs": refs, "transformation": transformation,
                    "support_text_sha256": _sha(text.encode("utf-8"))}
        return {"block_id": "user-description:" + _sha(BODY.canon(identity)), "field": field,
                "passage": "", "support_text": text, "document_sha256": doc_sha,
                "source_ref": refs[0], "all_source_refs": refs,
                "transformation": transformation, "links_present": False}

    if has_text:
        title = collected.get("productTitle")
        if isinstance(title, str) and title:
            ref = BODY.NLI.SOURCES.json_leaf_span(store, raw_file, ITEM_PATH + ".itemTitle", title)
            if not ref or not BODY.NLI.SOURCES.verify_span(store, ref):
                raise ValueError("native product title is not source bound")
            blocks.append(block(title, "native_product_name", [ref], {"kind": "literal_json_leaf"}))
    for index, section in enumerate(collected["sections"]):
        refs, fields = [], []
        for path in section["jsonPaths"]:
            present, value = _leaf(parsed, path)
            if not present or not isinstance(value, str):
                raise ValueError("description section does not bind an actual JSON string leaf")
            ref = BODY.NLI.SOURCES.json_leaf_span(store, raw_file, path, value)
            if not ref or not BODY.NLI.SOURCES.verify_span(store, ref):
                raise ValueError("description HTML leaf source verification failed")
            refs.append(ref)
            fields.append({"json_path": path, "raw_html_sha256": _sha(value.encode("utf-8")),
                           "raw_html_chars": len(value)})
        transformation = {"kind": "exact_user_cheerio_descriptionText", **pins,
                          "section_index": index, "section_sha256": _sha(BODY.canon(section))}
        sections.append({"section_index": index, "source_fields": fields,
                         "json_paths": list(section["jsonPaths"]), "images": deepcopy(section["images"]),
                         "text_sha256": _sha(section["text"].encode("utf-8")),
                         "text_chars": len(section["text"]), "table_count": section["tableCount"],
                         "image_count": len(section["images"]), "media_only": section["mediaOnly"],
                         "images_sha256": _sha(BODY.canon(section["images"]))})
        if has_text and section["text"]:
            made = block(section["text"], "native_description", refs, transformation)
            # The exact user algorithm retains visible anchor text. It is
            # not certified as a product attribute by this source adapter.
            html = BODY.NLI.SOURCES.resolve_json_path(parsed, section["jsonPaths"][0])
            made["links_present"] = any(node.tag == "a" for node in TABLE.HTML_BLOCKS._DOM(html).finish().nodes)
            blocks.append(made)
    body = {"document_sha256": doc_sha, "status": "eligible_blocks" if blocks else "no_eligible_native_blocks",
            "blocks": blocks}
    proof = {"schema_version": DESCRIPTION_PROTOCOL, "document_sha256": doc_sha,
             "product_id": str(record["product_id"]), "status": collected["status"],
             "fallback_original_full": not has_text, "failure_reason": collected.get("reason"),
             "original_api_raw_sha256": TABLE._source_refs(source_body)[0]["sha256"],
             "page_sha256": record.get("page_sha256"), "nextdata_raw_script_sha256": raw_sha,
             "field_json_paths": [ITEM_PATH + "." + f for f in ("extraItemComment", "extraItemCommentWithVideo", "itemComment", "detailComment")],
             "missing_json_paths": collected["missingJsonPaths"], "sections": sections, **pins,
             "normalized_sections_sha256": _sha(BODY.canon(collected["sections"])),
             "source_bound_title_added": bool(blocks and blocks[0]["field"] == "native_product_name"),
             "blocks": [{"block_id": b["block_id"], "field": b["field"],
                         "support_text_sha256": _sha(b["support_text"].encode("utf-8"))} for b in blocks],
             "old_sku_snapshot_with_later_live_capture": True,
             "images_are_metadata_only": True, "semantic_truth_verified": False}
    proof["normalized_candidate_body_view_sha256"] = _sha(BODY.canon(_public_description(body, True)))
    public_body = _public_description(body, True) if has_text else _original_public_body(source_body)
    proof["body_view_sha256"] = _sha(BODY.canon(public_body))
    if not has_text:
        proof["blocks"] = [{"block_id": b["block_id"], "field": b["field"],
                            "support_text_sha256": _sha(b["support_text"].encode("utf-8"))}
                           for b in public_body["blocks"]]
    return body, proof


def _public_description(body: Mapping[str, Any], target: bool) -> dict[str, Any]:
    blocks = []
    for i, b in enumerate(body["blocks"]):
        public = {"block_id": b["block_id"], "field": b["field"], "passage": b["passage"],
                  "support_text": b["support_text"], "source_sha256": b["source_ref"]["sha256"]}
        if target:
            public.update({"body_index": i, "source_alias": f"body-source:{body['document_sha256'][:16]}:description:{i}",
                           "links_present": b["links_present"]})
        else:
            public.update({"evidence_id": "body:" + b["block_id"], "document_sha256": body["document_sha256"]})
        blocks.append(public)
    return {"document_sha256": body["document_sha256"], "status": body["status"], "blocks": blocks}


def _original_public_body(body: Mapping[str, Any]) -> dict[str, Any]:
    if "original_public_target_body" in body:
        return deepcopy(body["original_public_target_body"])
    doc_sha = body["document_sha256"]
    return {"document_sha256": doc_sha, "status": body["status"], "blocks": [
        {"body_index": i, "block_id": b["block_id"], "field": b["field"], "passage": b["passage"],
         "support_text": b["support_text"], "source_sha256": b["source_ref"]["sha256"],
         "source_alias": f"body-source:{doc_sha[:16]}:{i}", "links_present": b.get("links_present", False)}
        for i, b in enumerate(body["blocks"])]}


def verify_description_sourceproof(source_bodies_by_sha: Mapping[str, Mapping[str, Any]],
                                   live_manifest: Path | Mapping[str, Any],
                                   source_root: Path = TABLE.DEFAULT_SOURCE_ROOT,
                                   live_root: Path | None = None) -> dict[str, Any]:
    root, manifest_sha, records = _manifest(live_manifest, live_root)
    pins = _extractor_pins()
    docs = {}
    for doc_sha, original in source_bodies_by_sha.items():
        if original.get("document_sha256") != doc_sha:
            raise ValueError("source body map key/document SHA mismatch")
        if original.get("side") == "au":
            _, docs[doc_sha] = _description_body(original, records, Path(source_root), root, pins)
    status_counts = {status: sum(p["status"] == status for p in docs.values())
                     for status in ("collected", "failure")}
    return {"schema_version": DESCRIPTION_PROTOCOL, "au_document_count": len(docs), "product_count": len(docs),
            "variant": "cleaned_description_fields_with_full_fallback", "collector_status_counts": status_counts,
            "live_capture_manifest_sha256": manifest_sha, "documents": docs, **pins,
            "description_collected_count": sum(not p["fallback_original_full"] for p in docs.values()),
            "fallback_count": sum(p["fallback_original_full"] for p in docs.values()),
            "model_inference_run": False, "labels_read": False, "semantic_truth_verified": False}


def _non_body_sha(payload: Mapping[str, Any]) -> str:
    value = deepcopy(dict(payload))
    value.pop("indexed_transport", None)
    value["context"].pop("eligible_target_body", None)
    value["context"].pop("eligible_source_bodies", None)
    return _sha(BODY.canon(value))


def _body_envelopes(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = payload["context"]
    return {"eligible_source_bodies": context["eligible_source_bodies"],
            "eligible_target_body": context["eligible_target_body"]}


def build_live_payload_views(original_payload: Mapping[str, Any],
                             source_bodies_by_sha: Mapping[str, Mapping[str, Any]],
                             live_manifest: Path | Mapping[str, Any],
                             source_root: Path = TABLE.DEFAULT_SOURCE_ROOT,
                             live_root: Path | None = None):
    """Keep the old full control and apply the user's exact four-field collector.

    A collection with no description text returns the entire original payload
    unchanged. Images and media-only sections never become body evidence text.
    """
    root, manifest_sha, records = _manifest(live_manifest, live_root)
    pins = _extractor_pins()
    original = deepcopy(dict(original_payload))
    described = deepcopy(original)
    public = list(original["context"]["eligible_source_bodies"]) + [original["context"]["eligible_target_body"]]
    au, live_bodies, proofs = {}, {}, {}
    for public_body in public:
        doc_sha = public_body["document_sha256"]
        body = source_bodies_by_sha.get(doc_sha)
        if not isinstance(body, Mapping) or body.get("document_sha256") != doc_sha:
            raise ValueError("payload lacks an exact original source document")
        if body.get("side") != "au" or doc_sha in au:
            continue
        refs = TABLE._source_refs(body)
        au[doc_sha] = {"document_sha256": doc_sha, "document_id": body.get("document_id"), "side": "au",
                       "source_document_binding": {"description_refs": refs}}
        live_bodies[doc_sha], proofs[doc_sha] = _description_body(body, records, Path(source_root), root, pins)
        if proofs[doc_sha]["fallback_original_full"]:
            au[doc_sha]["original_public_target_body"] = _original_public_body(body)
    fallback = any(proof["fallback_original_full"] for proof in proofs.values())
    if not fallback:
        described["context"]["eligible_source_bodies"] = [
            _public_description(live_bodies[b["document_sha256"]], False) if b["document_sha256"] in live_bodies else deepcopy(b)
            for b in original["context"]["eligible_source_bodies"]]
        target = original["context"]["eligible_target_body"]
        if target["document_sha256"] in live_bodies:
            described["context"]["eligible_target_body"] = _public_description(live_bodies[target["document_sha256"]], True)
    if isinstance(live_manifest, Mapping):
        manifest_binding = {"inline_manifest": deepcopy(dict(live_manifest))}
    else:
        path = Path(live_manifest).resolve()
        if path.is_dir():
            path /= "capture-inventory.json"
        manifest_binding = {"capture_manifest_path": str(path)}
    context = {"schema_version": DESCRIPTION_PROTOCOL, "live_root": str(root), **manifest_binding,
               "capture_manifest_sha256": manifest_sha, **pins, "au_source_bindings": au,
               "documents": proofs, "fallback_original_full": fallback,
               "original_non_body_sha256": _non_body_sha(original),
               "original_body_envelopes_sha256": _sha(BODY.canon(_body_envelopes(original))),
               "description_body_envelopes_sha256": _sha(BODY.canon(_body_envelopes(described))),
               "table_target_index_map": [], "body_view_kind": "exact_user_four_field_description"}
    audit = {"schema_version": DESCRIPTION_PROTOCOL, "documents": proofs,
             "table_count": sum(section["table_count"] for proof in proofs.values() for section in proof["sections"]),
             "fallback_original_full": fallback, "source_bound_title_addition": any(p["source_bound_title_added"] for p in proofs.values()),
             "full_control": "unchanged original full input", "view": "exact user four-field Cheerio description",
             "images_are_metadata_only": True, "semantic_core_changed": False, "model_inference_run": False}
    return original, described, context, audit


def validate_live_source_response(response: dict[str, Any], pair: dict[str, Any], payload: dict[str, Any],
                                  original_source_body: dict[str, Any], source_root: Path,
                                  validation_context: Mapping[str, Any], variant: str = "description") -> dict[str, Any]:
    """Verify transformed body origins and unchanged original SKU primitives.

    This establishes source provenance only. Every semantic field remains the
    model's unchanged output and is validated by the frozen BODY v3 validator.
    """
    if variant != "description":
        raise ValueError("live source validator requires the description variant")
    structural = BODY.validate_literal_response(response, pair, payload)
    if _non_body_sha(payload) != validation_context["original_non_body_sha256"]:
        raise ValueError("description adapter changed original facts/options/native SKU context")
    manifest = validation_context.get("inline_manifest") or validation_context.get("capture_manifest_path")
    root, manifest_sha, records = _manifest(manifest, Path(validation_context["live_root"]))
    if manifest_sha != validation_context["capture_manifest_sha256"]:
        raise ValueError("live capture manifest changed after input freeze")
    pins = _extractor_pins()
    if any(validation_context.get(key) != value for key, value in pins.items()):
        raise ValueError("description extraction implementation changed after input freeze")
    fresh = {}
    for doc_sha, old_binding in validation_context["au_source_bindings"].items():
        body, proof = _description_body(old_binding, records, Path(source_root), root, pins)
        if proof != validation_context["documents"][doc_sha]:
            raise ValueError("normalized description sourceproof failed exact raw-source rederivation")
        fresh[doc_sha] = body
    fallback = any(p["fallback_original_full"] for p in validation_context["documents"].values())
    if fallback != validation_context["fallback_original_full"]:
        raise ValueError("description fallback flag does not rederive")
    envelope_sha = _sha(BODY.canon(_body_envelopes(payload)))
    expected_sha = validation_context["original_body_envelopes_sha256"] if fallback else validation_context["description_body_envelopes_sha256"]
    if envelope_sha != expected_sha:
        raise ValueError("submitted description body inventory differs from the frozen input")
    if fallback:
        return SCOPED.validate_source_response(response, pair, payload, original_source_body, source_root)
    for public in payload["context"]["eligible_source_bodies"]:
        doc_sha = public["document_sha256"]
        if doc_sha in fresh and public != _public_description(fresh[doc_sha], False):
            raise ValueError("source description body differs from exact collector replay")
    target_sha = payload["context"]["target_document_sha256"]
    if original_source_body.get("document_sha256") != target_sha:
        raise ValueError("original SKU source document does not bind target identity")
    if original_source_body.get("side") != "au":
        return SCOPED.validate_source_response(response, pair, payload, original_source_body, source_root)
    if target_sha not in fresh or payload["context"]["eligible_target_body"] != _public_description(fresh[target_sha], True):
        raise ValueError("target description body differs from exact collector replay")
    old_refs = TABLE._source_refs(original_source_body)
    if old_refs != TABLE._source_refs(validation_context["au_source_bindings"][target_sha]):
        raise ValueError("new body does not preserve the original SKU source binding")
    binding = original_source_body.get("source_document_binding") or {}
    source_doc = {"document_id": original_source_body.get("document_id"), "document_sha256": target_sha,
                  "side": original_source_body["side"], "title": {"source_refs": binding.get("title_refs", [])},
                  "description": {"segments": [{"source_refs": [ref]} for ref in binding.get("description_refs", [])]}}
    store = BODY.NLI.SOURCES.RawStore(Path(source_root))
    with SCOPED._installed_inventory_hook():
        inventory = BODY._target_sku_inventory(store, source_doc)
    if payload["context"]["target_native_sku_inventory"] != BODY._public_inventory(inventory):
        raise ValueError("native target SKU inventory differs from original raw-source rederivation")
    for row_ref in inventory.get("native_row_refs", []):
        store.raw(row_ref["raw_file"], row_ref["sha256"])
        if not isinstance(BODY.NLI.SOURCES.resolve_json_path(store.json(row_ref["raw_file"]), row_ref["json_path"]), dict):
            raise ValueError("native original SKU row reference does not resolve")
    members = payload["context"].get("hypothesis", {}).get("family_members", [])
    if len(members) != len(pair["option_values"]):
        raise ValueError("original complete source family is missing")
    for i, member in enumerate(members):
        if (member.get("whole_option") != pair["option_values"][i]
                or member.get("evidence_id") != pair["candidate_vector"][i]["option_evidence_id"]
                or not BODY.NLI.SOURCES.verify_span(store, member.get("span", {}))):
            raise ValueError("original whole-option source span failed verification")
    for fact in payload["context"]["target_selected_facts"]:
        for key in ("axis_name_span", "value_span"):
            if not BODY.NLI.SOURCES.verify_span(store, fact[key]):
                raise ValueError("original selected target fact failed raw-source verification")
    citations = []
    for decision in response["decisions"]:
        for quote in decision["body_quote_refs"]:
            block = fresh[target_sha]["blocks"][quote["body_index"]]
            citations.append({"body_index": quote["body_index"], "block_id": block["block_id"],
                              "document_sha256": target_sha, "source_refs": deepcopy(block["all_source_refs"]),
                              "transformation": deepcopy(block["transformation"]),
                              "support_text_quote": quote["quote"], "support_text_start": quote["start"],
                              "support_text_end": quote["end"], "normalized_text_sha256": _sha(block["support_text"].encode("utf-8"))})
    return {**structural, "source_citations_verified": True, "source_truth_verified": False,
            "sourceproof_certified": False, "semantic_truth_verified": False, "citations": citations,
            "description_sourceproof_protocol": DESCRIPTION_PROTOCOL}
