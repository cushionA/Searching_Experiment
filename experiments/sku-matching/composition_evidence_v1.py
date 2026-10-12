#!/usr/bin/env python3
"""Pure input builders for whole-SKU composition evidence experiments.

This module deliberately does not load models, normalize source text, or infer
whether a description block applies to a selected SKU.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


CONTENT_VERSION = "composition-evidence-v1/whole-sku-spec+set-contents+quantity+accessories"

# Kept byte-for-byte equivalent in behavior to
# trial_keyword_filtered_product_v1.KEYWORD_HEADING / is_explicit_keyword_block
# to keep this input-builder module pure and independent of that runner.
_KEYWORD_HEADING = re.compile(
    r"^\s*(?:▼\s*)?(?:(?:【\s*)(?:検索\s*(?:ワード|キーワード)|関連キーワード|キーワード)"
    r"(?:\s*】).*|(?:検索\s*(?:ワード|キーワード)|関連キーワード|キーワード)"
    r"(?:\s*[:：].*)?)\s*$"
)
_ANCHOR_TAG = re.compile(r"<\s*a\b", re.IGNORECASE)


def _is_explicit_keyword_block(block: dict[str, Any]) -> bool:
    text = block.get("text")
    if not isinstance(text, str):
        return False
    heading = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return bool(heading and _KEYWORD_HEADING.fullmatch(heading))


def exclusion_reason(block: dict[str, Any]) -> str | None:
    """Return why a block cannot be used, or ``None`` when it is eligible.

    Table context is checked even when the current row has no link of its own:
    an anchor in the source-bound table fragment makes every row unscoped.
    """
    if not isinstance(block, dict):
        return "invalid_block"
    if _is_explicit_keyword_block(block):
        return "explicit_keyword_block"
    links = block.get("links")
    if links:
        return "block_has_links"

    context = block.get("context")
    table = context.get("table") if isinstance(context, dict) else None
    if block.get("kind") == "table_row" and not isinstance(table, dict):
        return "unbound_table_context"
    if isinstance(table, dict):
        source_ref = table.get("source_ref")
        if not isinstance(source_ref, dict) or not isinstance(source_ref.get("quote"), str):
            return "unbound_table_context"
        if _ANCHOR_TAG.search(source_ref["quote"]):
            return "linked_table_context"

    support_text = block.get("support_text")
    quote_refs = block.get("quote_refs")
    if not isinstance(support_text, str) or not support_text.strip():
        return "missing_support_text"
    if not isinstance(quote_refs, list) or not quote_refs:
        return "missing_quote_refs"
    return None


def _validate_axis_options(axis_name: str, option_values: list[str]) -> None:
    if not isinstance(axis_name, str) or not axis_name:
        raise ValueError("axis_name must be a nonempty raw string")
    if not isinstance(option_values, list) or not option_values:
        raise ValueError("option_values must be a nonempty ordered list")
    if any(not isinstance(value, str) or value == "" for value in option_values):
        raise ValueError("option_values must preserve raw string values")
    if len(set(option_values)) != len(option_values):
        raise ValueError("option_values must be unique raw labels")


def _selected_axes_prefix(target_selected_axes_text: str | None) -> str:
    if target_selected_axes_text is None:
        return ""
    if not isinstance(target_selected_axes_text, str):
        raise ValueError("target_selected_axes_text must be a raw string or None")
    return "対象SKUの選択軸：\n" + target_selected_axes_text + "\n"


def query_for(axis_name: str, option_values: list[str],
              target_selected_axes_text: str | None = None) -> str:
    """Build a whole-SKU composition query without a desired selected value."""
    _validate_axis_options(axis_name, option_values)
    options = "\n".join(option_values)
    return (
        _selected_axes_prefix(target_selected_axes_text)
        + "購入対象の仕様・セット内容・数量・付属品を調べます。\n"
        + "元の確認軸：" + axis_name + "\n"
        + "元の選択肢（原文・順序保持）：\n" + options
    )


def choice_request(request_id: str, axis_name: str, option_values: list[str],
                   unknown_label: str, block: dict[str, Any],
                   target_selected_axes_text: str | None = None,
                   metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one JEV choice request from a raw source block and full option set."""
    _validate_axis_options(axis_name, option_values)
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a nonempty string")
    if not isinstance(unknown_label, str) or not unknown_label:
        raise ValueError("unknown_label must be a nonempty raw string")
    if unknown_label in option_values:
        raise ValueError("unknown_label must not collide with an original option")
    if not isinstance(block, dict) or not isinstance(block.get("model_passage"), str):
        raise ValueError("block requires raw model_passage")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be a mapping")

    choices = [{
        "label": value,
        "description": f"購入対象の〈{axis_name}〉は〈{value}〉である。",
    } for value in option_values]
    choices.append({
        "label": unknown_label,
        "description": "この記載から購入対象の構成を特定できない。",
    })
    state = (
        _selected_axes_prefix(target_selected_axes_text)
        + "この商品の記載された仕様・セット内容：\n"
        + block["model_passage"]
    )
    request = deepcopy(metadata) if metadata is not None else {}
    request.update({
        "id": request_id,
        "question": f"購入対象の仕様・セット内容について、〈{axis_name}〉はどの選択肢に該当しますか。",
        "state": state,
        "choices": choices,
        "quote_refs": deepcopy(block.get("quote_refs", [])),
        "content_version": CONTENT_VERSION,
    })
    return request
