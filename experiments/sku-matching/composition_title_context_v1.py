#!/usr/bin/env python3
"""Add strictly source-bound original titles as auxiliary composition evidence.

This module only builds deterministic packets and validates model replies. It
performs no inference, label access, or title-based SKU selection.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

import composition_context_v1 as CONTEXT
import composition_language_model_v1 as LM
import composition_source_blocks_v2 as SOURCES

PROTOCOL_VERSION = "composition-title-auxiliary-context-v1"
CONTENT_VERSION = PROTOCOL_VERSION
RESPONSE_FIELDS = LM.RESPONSE_FIELDS
_TITLE_GUIDANCE = """商品名は説明本文と別の補助証拠です。商品名: 複数仕様/検索語/選べる記載はselected SKU proofなし。商品名に複数仕様・検索語・選べる候補・サイズ一覧が含まれることがあり、商品名だけでは選択中SKUの証拠になりません。商品名だけを根拠に選ぶ場合も、原文が実際にこの選択構成を特定している必要があります。対応が曖昧ならnullです。商品名の記載を説明本文へ読み替えたり、選択SKUへの適用証明とみなしたりしないでください。
商品名の引用元は入力中のtitle役割ブロックだけです。説明本文はdescription役割ブロックだけから引用してください。本文と商品名の記述が異なるとき、別仕様・関連品・検索語を混ぜず、不明ならnullを返してください。"""
PROTOCOL = (CONTEXT.PROTOCOL.replace(
    "引用本文が存在することは、その記述が選択SKUに適用される証明ではありません。記述が購入対象の構成を明確に示さない限り、推測せず不明にしてください。",
    _TITLE_GUIDANCE + "\n引用本文が存在することは、その記述が選択SKUに適用される証明ではありません。記述が購入対象の構成を明確に示さない限り、推測せず不明にしてください。"
).replace("reasonは短い文字列にしてください。", "reasonは500文字以下の短い文字列にしてください."))
PROTOCOL = PROTOCOL.replace(CONTEXT.PROTOCOL_VERSION, PROTOCOL_VERSION, 1)


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _title_refs(document: dict[str, Any], store: Any) -> list[dict[str, Any]] | None:
    """Return bound current-document title quote refs, or None if any source fails."""
    if not isinstance(document, dict):
        raise ValueError("document must be a mapping")
    doc_id, doc_sha = document.get("document_id"), document.get("document_sha256")
    title = document.get("title")
    if not isinstance(doc_id, str) or not doc_id or not isinstance(doc_sha, str) or not doc_sha:
        raise ValueError("document requires frozen identity and document SHA")
    if not isinstance(title, dict) or not isinstance(title.get("text"), str) or not title["text"]:
        return None
    sources = title.get("source_refs")
    if not isinstance(sources, list) or not sources:
        return None
    refs = []
    for source in sources:
        if not isinstance(source, dict):
            return None
        locator = source.get("locator")
        if (not isinstance(locator, dict)
                or locator.get("kind") not in {"json_leaf", "jsonl_leaf", "html_text"}
                or source.get("quote") != title["text"]
                or type(source.get("start")) is not int
                or type(source.get("end")) is not int
                or source["end"] - source["start"] != len(title["text"])
                or not isinstance(source.get("raw_file"), str)
                or not isinstance(source.get("sha256"), str)):
            return None
        try:
            if not SOURCES.SOURCES.verify_span(store, source):
                return None
        except (KeyError, IndexError, OSError, UnicodeDecodeError, ValueError):
            return None
        refs.append({"document_id": doc_id, "document_sha256": doc_sha,
                     "field": "title", "start": 0,
                     "end": len(title["text"]), "quote": title["text"]})
    return refs


def build_packet(unit: dict[str, Any], bodyblocks: list[dict[str, Any]] | None,
                 document: dict[str, Any], store: Any) -> dict[str, Any] | None:
    """Build complete body context plus a separately marked verified title block.

    Body blocks pass through the frozen context builder unchanged. Invalid or
    unbound title provenance is omitted. A title-only packet is valid when its
    original title spans verify; no usable body or title returns None.
    """
    unit_id, options, doc_id, axes = CONTEXT._validate_unit(unit)
    if not isinstance(document, dict) or document.get("document_id") != doc_id:
        raise ValueError("document target differs from original unit")
    if bodyblocks is None:
        bodyblocks = []
    if not isinstance(bodyblocks, list):
        raise ValueError("bodyblocks must be a list")
    body_packet = CONTEXT.build_packet(unit, bodyblocks) if bodyblocks else None
    blocks = deepcopy(body_packet["blocks"]) if body_packet else []
    title_refs = _title_refs(document, store)
    title_text = document.get("title", {}).get("text") if isinstance(document.get("title"), dict) else None
    if title_refs is not None:
        raw_title_source_refs = deepcopy(document["title"]["source_refs"])
        title_identity = {"document_id": doc_id,
                          "document_sha256": document["document_sha256"],
                          "raw_title": title_text, "quote_refs": title_refs,
                          "raw_title_source_refs": raw_title_source_refs}
        title_id = "title:" + _canonical_sha(title_identity)
        if any(b["block_id"] == title_id for b in blocks):
            raise ValueError("title block ID collides with a body block")
        blocks.append({"block_id": title_id, "evidence_role": "title",
                       "model_passage": title_text, "quote_refs": deepcopy(title_refs),
                       "raw_title_source_refs": raw_title_source_refs})
    if not blocks:
        return None
    packet = {"unit_id": unit_id, "kind": unit["kind"], "axis_name": unit["axis_name"],
              "option_values": list(options), "target_document_id": doc_id,
              "blocks": blocks, "title_scope": "unproved_auxiliary_not_selected_sku_proof",
              "content_version": CONTENT_VERSION}
    if unit["kind"] == "paired":
        packet["target_selected_axes_text"] = axes
    packet["packet_sha256"] = _canonical_sha(packet)
    return packet


def model_payload(packet: dict[str, Any]) -> dict[str, Any]:
    """Strip locator/quote metadata while exposing each passage's evidence role."""
    if not isinstance(packet, dict) or not isinstance(packet.get("blocks"), list):
        raise ValueError("model payload requires a complete packet")
    unsigned = {k: v for k, v in packet.items() if k != "packet_sha256"}
    if packet.get("packet_sha256") != _canonical_sha(unsigned):
        raise ValueError("packet SHA does not match the frozen packet")
    if packet.get("title_scope") != "unproved_auxiliary_not_selected_sku_proof":
        raise ValueError("packet title scope is missing or changed")
    fields = ("unit_id", "kind", "axis_name", "option_values", "target_document_id",
              "title_scope", "content_version")
    payload = {k: deepcopy(packet[k]) for k in fields}
    if packet["kind"] == "paired":
        axes = packet.get("target_selected_axes_text")
        if not isinstance(axes, str) or not axes:
            raise ValueError("paired packet requires exact selected axes")
        payload["target_selected_axes_text"] = axes
    elif packet["kind"] != "shared" or "target_selected_axes_text" in packet:
        raise ValueError("shared packet cannot contain selected axes")
    payload["blocks"] = []
    seen = set()
    for block in packet["blocks"]:
        if not isinstance(block, dict):
            raise ValueError("packet block must be an object")
        bid, passage, refs = block.get("block_id"), block.get("model_passage"), block.get("quote_refs")
        role = block.get("evidence_role", "description")
        expected_fields = {"block_id", "model_passage", "quote_refs"}
        if role == "title": expected_fields.update({"evidence_role", "raw_title_source_refs"})
        if (set(block) != expected_fields or not isinstance(bid, str) or not bid
                or bid in seen or not isinstance(passage, str) or not passage
                or not isinstance(refs, list) or not refs
                or role not in {"description", "title"}):
            raise ValueError("packet block differs from the frozen body/title schema")
        if any(ref.get("field") != role for ref in refs if isinstance(ref, dict)):
            raise ValueError("block citations differ from its evidence role")
        seen.add(bid)
        payload["blocks"].append({"block_id": bid, "evidence_role": role,
                                  "model_passage": passage})
    if not payload["blocks"]:
        raise ValueError("empty packets cannot be dispatched")
    payload["model_input_sha256"] = _canonical_sha(payload)
    return payload


def _packet_blocks(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = packet.get("blocks")
    if not isinstance(values, list) or not values:
        raise ValueError("packet requires source blocks")
    result = {}
    for block in values:
        if not isinstance(block, dict):
            raise ValueError("packet block must be a mapping")
        bid = block.get("block_id")
        role = block.get("evidence_role", "description")
        if not isinstance(bid, str) or not bid or bid in result:
            raise ValueError("packet block IDs must be unique nonempty strings")
        if role not in {"description", "title"} or not isinstance(block.get("model_passage"), str):
            raise ValueError("packet block requires a valid role and exact passage")
        result[bid] = block
    return result


def validate_response(packet: dict[str, Any], response: dict[str, Any],
                      request_or_None: dict[str, Any] | None,
                      documents: dict[str, Any]) -> dict[str, Any]:
    """Validate exact old response schema and source-bound title/body citations."""
    if not isinstance(packet, dict) or not isinstance(response, dict) or not isinstance(documents, dict):
        raise ValueError("packet, response, and documents must be mappings")
    unsigned = {k: v for k, v in packet.items() if k != "packet_sha256"}
    if packet.get("packet_sha256") != _canonical_sha(unsigned):
        raise ValueError("packet SHA does not match the frozen packet")
    unit_id = packet.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id or set(response) != RESPONSE_FIELDS:
        raise ValueError("response fields differ from the exact frozen schema")
    if response.get("unit_id") != unit_id:
        raise ValueError("response unit_id differs from the packet")
    if packet.get("title_scope") != "unproved_auxiliary_not_selected_sku_proof":
        raise ValueError("packet title scope is missing or changed")
    blocks = _packet_blocks(packet)
    doc_id = packet.get("target_document_id")
    if not isinstance(doc_id, str) or doc_id not in documents:
        raise ValueError("target document is absent from documents")
    axis, options, kind = packet.get("axis_name"), packet.get("option_values"), packet.get("kind")
    if not isinstance(axis, str) or not axis or kind not in {"shared", "paired"}:
        raise ValueError("packet lacks exact original unit identity")
    if (not isinstance(options, list) or not options or len(set(options)) != len(options)
            or any(not isinstance(x, str) or not x for x in options)):
        raise ValueError("packet requires unique original raw options")
    selected_axes = packet.get("target_selected_axes_text")
    if kind == "shared":
        if selected_axes not in (None, "") or (request_or_None is not None and request_or_None.get("target_document_id") != doc_id):
            raise ValueError("shared packet cannot contain selected-axis context")
        if any(ref.get("field") == "selected_axes" for b in blocks.values() for ref in b.get("quote_refs", []) if isinstance(ref, dict)):
            raise ValueError("shared packet cannot contain selected-axis citations")
    else:
        if (not isinstance(request_or_None, dict) or request_or_None.get("target_document_id") != doc_id
                or not isinstance(selected_axes, str) or not selected_axes
                or request_or_None.get("target_selected_axes_text") != selected_axes):
            raise ValueError("paired packet axes differ from the original request")

    winner, evidence_ids, reason = response.get("winning_value"), response.get("evidence_block_ids"), response.get("reason")
    if winner is not None and (not isinstance(winner, str) or winner not in options):
        raise ValueError("winning_value must exactly equal an original raw option or null")
    if (not isinstance(evidence_ids, list) or any(not isinstance(x, str) for x in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)):
        raise ValueError("evidence_block_ids must be unique block ID strings")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("reason must be a short nonempty string")
    if winner is None:
        if evidence_ids: raise ValueError("null winner must not cite evidence")
        return {"unit_id": unit_id, "status": "unknown", "winning_value": None,
                "quote_refs": [], "evidence_block_ids": [], "reason": reason,
                "source_model_response": deepcopy(response), "scope_status": "unproved",
                "production_adoption_allowed": False, "variant": CONTENT_VERSION}
    if not evidence_ids:
        raise ValueError("non-null winner requires at least one evidence block")
    if set(evidence_ids) - set(blocks):
        raise ValueError("response cites a block outside the packet")
    refs, seen = [], set()
    for bid in evidence_ids:
        block = blocks[bid]
        role = block.get("evidence_role", "description")
        block_refs = block.get("quote_refs")
        if not isinstance(block_refs, list) or not block_refs:
            raise ValueError("selected evidence block has no literal quote refs")
        for ref in block_refs:
            if (not isinstance(ref, dict) or ref.get("field") != role
                    or ref.get("document_id") != doc_id
                    or not LM.GATE.verify_quote(ref, {"target_document_id": doc_id}, documents)):
                raise ValueError("selected evidence block has invalid current-document quote")
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                refs.append(deepcopy(ref)); seen.add(key)
    if kind == "paired":
        axes_ref = {"document_id": doc_id, "field": "selected_axes", "start": 0,
                    "end": len(selected_axes), "quote": selected_axes}
        if not LM.GATE.verify_quote(axes_ref, request_or_None, documents):
            raise ValueError("paired selected-axis quote does not verify against the original request")
        refs.append(axes_ref)
    return {"unit_id": unit_id, "status": "support", "winning_value": winner,
            "quote_refs": refs, "evidence_block_ids": list(evidence_ids),
            "reason": reason, "source_model_response": deepcopy(response),
            "scope_status": "unproved", "production_adoption_allowed": False,
            "variant": CONTENT_VERSION}
