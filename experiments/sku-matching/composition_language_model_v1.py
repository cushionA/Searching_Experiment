#!/usr/bin/env python3
"""Validate source-grounded composition responses from unpinned language models.

This module assembles no prompts and calls no model. Agent identity and trace
provenance belong to the dispatch runner, not to the model's self-description.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import evaluate_scoped_html_trial_v1 as GATE


CONTENT_VERSION = "composition-language-model-validator-v1"
RESPONSE_FIELDS = frozenset({"unit_id", "winning_value", "evidence_block_ids", "reason"})


def _raw_options(packet: dict[str, Any]) -> list[str]:
    values = packet.get("option_values")
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or value == "" for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("packet requires unique nonempty raw option_values")
    return values


def _packet_blocks(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = packet.get("blocks")
    if not isinstance(values, list) or not values:
        raise ValueError("packet requires source blocks")
    indexed = {}
    for block in values:
        if not isinstance(block, dict):
            raise ValueError("each packet block must be an object")
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id or block_id in indexed:
            raise ValueError("packet block IDs must be unique nonempty strings")
        if not isinstance(block.get("model_passage"), str):
            raise ValueError("packet block requires the exact model_passage string")
        indexed[block_id] = block
    return indexed


def _target_document_id(packet: dict[str, Any], request: dict[str, Any] | None,
                       blocks: dict[str, dict[str, Any]]) -> str:
    candidates = [packet.get("target_document_id")]
    if request is not None:
        candidates.append(request.get("target_document_id"))
    for block in blocks.values():
        refs = block.get("quote_refs")
        if isinstance(refs, list):
            candidates.extend(ref.get("document_id") for ref in refs if isinstance(ref, dict))
    identities = {value for value in candidates if isinstance(value, str) and value}
    if len(identities) != 1:
        raise ValueError("packet must bind exactly one current target document")
    return next(iter(identities))


def _verified_block_refs(block: dict[str, Any], document_id: str,
                         documents: dict[str, Any]) -> list[dict[str, Any]]:
    refs = block.get("quote_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("selected evidence block has no literal quote_refs")
    request = {"target_document_id": document_id}
    verified = []
    for ref in refs:
        if (not isinstance(ref, dict) or ref.get("field") != "description"
                or ref.get("document_id") != document_id
                or not GATE.verify_quote(ref, request, documents)):
            raise ValueError("selected evidence block contains an invalid current-document quote")
        verified.append(deepcopy(ref))
    return verified


def validate_response(packet: dict[str, Any], response: dict[str, Any],
                      request_or_None: dict[str, Any] | None,
                      documents: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a model's whole raw option is bound to literal evidence.

    ``None`` is an unknown response and must not cite evidence. Non-null winners
    need one or more packet block IDs, complete verified description citations,
    and, for paired units, the exact verified current selected-axis context.
    """
    if not isinstance(packet, dict) or not isinstance(response, dict):
        raise ValueError("packet and response must be mappings")
    if not isinstance(documents, dict):
        raise ValueError("documents must be indexed by document_id")
    unit_id = packet.get("unit_id")
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("packet requires a nonempty unit_id")
    if set(response) != RESPONSE_FIELDS:
        raise ValueError("response fields differ from the frozen response contract")
    if response.get("unit_id") != unit_id:
        raise ValueError("response unit_id differs from the packet")
    axis_name = packet.get("axis_name")
    if not isinstance(axis_name, str) or axis_name == "":
        raise ValueError("packet requires the exact raw axis_name")
    options = _raw_options(packet)
    kind = packet.get("kind")
    if kind not in {"shared", "paired"}:
        raise ValueError("packet kind must be shared or paired")
    if request_or_None is not None and not isinstance(request_or_None, dict):
        raise ValueError("request_or_None must be a mapping or None")
    blocks = _packet_blocks(packet)
    document_id = _target_document_id(packet, request_or_None, blocks)
    if document_id not in documents:
        raise ValueError("current target document is absent from documents")

    selected_axes = packet.get("target_selected_axes_text")
    if kind == "shared":
        if selected_axes not in (None, ""):
            raise ValueError("shared packet must not contain selected-axis text")
        if any(ref.get("field") == "selected_axes"
               for block in blocks.values()
               for ref in block.get("quote_refs", []) if isinstance(ref, dict)):
            raise ValueError("shared packet must not contain selected-axis quote refs")
    else:
        if request_or_None is None:
            raise ValueError("paired response requires its original request binding")
        if not isinstance(selected_axes, str) or not selected_axes:
            raise ValueError("paired packet requires the exact selected-axis text")
        if request_or_None.get("target_selected_axes_text") != selected_axes:
            raise ValueError("paired packet axes differ from the original request")
        if request_or_None.get("target_document_id") != document_id:
            raise ValueError("paired request target document differs from the packet")

    winning_value = response.get("winning_value")
    evidence_ids = response.get("evidence_block_ids")
    reason = response.get("reason")
    if winning_value is not None and (not isinstance(winning_value, str) or winning_value not in options):
        raise ValueError("winning_value must exactly equal one original raw option or null")
    if not isinstance(evidence_ids, list) or any(not isinstance(value, str) for value in evidence_ids):
        raise ValueError("evidence_block_ids must be a list of block ID strings")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("evidence_block_ids must not contain duplicates")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("reason must be a short nonempty string")

    if winning_value is None:
        if evidence_ids:
            raise ValueError("null winner must not cite evidence")
        return {
            "unit_id": unit_id,
            "status": "unknown",
            "winning_value": None,
            "quote_refs": [],
            "evidence_block_ids": [],
            "reason": reason,
            "source_model_response": deepcopy(response),
            "scope_status": "unproved",
            "production_adoption_allowed": False,
            "variant": CONTENT_VERSION,
        }

    if not evidence_ids:
        raise ValueError("non-null winner requires at least one evidence block")
    unknown_ids = set(evidence_ids) - set(blocks)
    if unknown_ids:
        raise ValueError("response cites a block outside the packet")

    refs: list[dict[str, Any]] = []
    seen_refs = set()
    for block_id in evidence_ids:
        for ref in _verified_block_refs(blocks[block_id], document_id, documents):
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if key not in seen_refs:
                seen_refs.add(key)
                refs.append(ref)

    if kind == "paired":
        axes_ref = {"document_id": document_id, "field": "selected_axes", "start": 0,
                    "end": len(selected_axes), "quote": selected_axes}
        if not GATE.verify_quote(axes_ref, request_or_None, documents):
            raise ValueError("paired selected-axis quote does not verify against the original request")
        refs.append(axes_ref)

    return {
        "unit_id": unit_id,
        "status": "support",
        "winning_value": winning_value,
        "quote_refs": refs,
        "evidence_block_ids": list(evidence_ids),
        "reason": reason,
        "source_model_response": deepcopy(response),
        "scope_status": "unproved",
        "production_adoption_allowed": False,
        "variant": CONTENT_VERSION,
    }
