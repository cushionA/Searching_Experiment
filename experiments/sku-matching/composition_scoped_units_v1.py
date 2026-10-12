#!/usr/bin/env python3
"""Create per-original-request units for scoped composition context.

Requests with an explicit target selected-axes string get a distinct paired
unit keyed by their original request ID. Requests without selected axes get a
separate global singleton with no source-request binding or desired value.
Verified typed-value-equivalence requests are excluded for unchanged handling
by the caller.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import composition_context_v1 as CONTEXT


CONTENT_VERSION = "original_request_scoped_context_v1"
PROTOCOL_VERSION = CONTENT_VERSION
UNKNOWN_LABEL = CONTEXT.UNKNOWN_LABEL
PROTOCOL = CONTEXT.PROTOCOL.replace(
    "共有ユニットでは選択中の軸情報がありません。商品共通の説明だけを判定し、特定SKUへの適用を推測しないでください。pairedユニットでは、提示された全選択軸がこの対象SKUを識別する文脈です。そのSKUについてだけ判定し、別SKUへ一般化しないでください。",
    "各ユニットは元の要求一件だけに対応し、要求間・行間で共有または再利用しません。target_selected_axes_textがあるpairedユニットでは、その要求に保存された対象SKUの選択軸だけを文脈として使い、同じ対象SKUの説明からその要求の購入構成を判定します。選択軸がないsharedユニットは一要求だけのグローバル説明です。特定の選択値やSKUへの適用を推測せず、別要求へ一般化しないでください。"
)


def build_units(requests: list[dict[str, Any]], proof_ids: set[str] | list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Build one unit per eligible original request, excluding typed proofs.

    The returned dictionary is keyed by original request ID. ``proof_ids``
    identifies requests that retain their existing typed-value-equivalence
    proof path and therefore must not be converted into model units.
    """
    if not isinstance(requests, list):
        raise ValueError("requests must be a list")
    if not isinstance(proof_ids, (set, list, tuple)) or any(
            not isinstance(value, str) or not value for value in proof_ids):
        raise ValueError("proof_ids must be a collection of nonempty request IDs")
    excluded = set(proof_ids)
    seen: set[str] = set()
    result: dict[str, dict[str, Any]] = {}
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("each original request must be a mapping")
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request requires a nonempty request_id")
        if request_id in seen:
            raise ValueError("original request IDs must be unique")
        seen.add(request_id)
        if request_id in excluded:
            continue

        axis_name = request.get("axis_name")
        options = request.get("option_values")
        document_id = request.get("target_document_id")
        if not isinstance(axis_name, str) or not axis_name:
            raise ValueError("request requires its exact axis_name")
        if (not isinstance(options, list) or not options
                or any(not isinstance(value, str) or value == "" for value in options)
                or len(set(options)) != len(options)):
            raise ValueError("request requires unique, nonempty raw option_values")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("request requires a target_document_id")

        axes = request.get("target_selected_axes_text")
        common = {
            "group_id": request_id,
            "axis_name": axis_name,
            "option_values": list(options),
            "target_document_id": document_id,
        }
        if isinstance(axes, str) and axes:
            # Keep the exact original request as provenance. CONTEXT only places
            # its allowed binding fields into the packet/model payload.
            if request.get("selected_value") not in options:
                raise ValueError("paired request selected_value must be an original raw option")
            unit = {
                **common,
                "kind": "paired",
                "target_selected_axes_text": axes,
                "source_request": deepcopy(request),
            }
            CONTEXT._validate_unit(unit)
        elif axes in (None, ""):
            # An axis-free request is still a singleton; no requested value or
            # request-level metadata is exposed through this global unit.
            unit = {
                **common,
                "kind": "shared",
                "target_selected_axes_text": None,
                "model_request": {
                    "id": request_id,
                    "choices": ([{"label": value} for value in options]
                                + [{"label": UNKNOWN_LABEL}]),
                },
            }
            CONTEXT._validate_unit(unit)
        else:
            raise ValueError("target_selected_axes_text must be a string or empty")
        result[request_id] = unit

    unknown_proofs = excluded - seen
    if unknown_proofs:
        raise ValueError("proof_ids do not match original requests")
    return result


def build_packet(unit: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a complete source-bound packet using the unchanged context logic."""
    return CONTEXT.build_packet(unit, blocks)


def model_payload(packet: dict[str, Any]) -> dict[str, Any]:
    """Build a leakage-limited payload using the original packet validator."""
    return CONTEXT.model_payload(packet)
