#!/usr/bin/env python3
"""Build complete, source-bound composition packets for a single model unit.

Packets contain every eligible description block bound to the target document.
They intentionally omit titles, labels, previous predictions, and the desired
selected value. A unit with no eligible blocks returns ``None``; callers must
record that unit as an explicit unknown without dispatching an empty prompt.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import composition_evidence_v1 as COMPOSITION


PROTOCOL_VERSION = "composition-full-context-packet-v1"
CONTENT_VERSION = PROTOCOL_VERSION
UNKNOWN_LABEL = "記載から特定できない（診断用）"
PROTOCOL = f"""{PROTOCOL_VERSION}
固定された対象文書に属する、除外条件を通過した全ての説明ブロックを読み、元の選択肢から購入対象の構成を判定してください。ブロックの省略・上位選択・切り詰めはありません。選択肢は原文の全体値・順序を保持しています。winning_valueは元の選択肢一つ、または判定不能を表すnullです。nullは選択肢ではありません。

対象は商品説明に書かれた仕様、セット内容、数量、付属品です。複数ブロックに分かれた記述を使う場合、それらが同じ購入構成を説明していることを確認してください。別の選択肢、関連商品、推奨品、画像内の品、同梱と明記されていない品の内容や数量を混ぜないでください。条件付き・曖昧・不完全な記述や、購入対象を一意に定められない場合は不明を選びます。

数字、単位、数量、寸法、セット個数、残差となる付属品・構成要素を原文の粒度で保ってください。換算、丸め、補完、正規化をせず、記載のない数字や単位を作らないでください。価格、在庫、配送・送料、画面UI上の選択状態だけから構成を推定してはいけません。他のファイル、外部サイトやWeb検索、ラベル、以前の予測・判定、採点結果は使わないでください。確率、モデルID、出典適用性を作り上げてはいけません。

共有ユニットでは選択中の軸情報がありません。商品共通の説明だけを判定し、特定SKUへの適用を推測しないでください。pairedユニットでは、提示された全選択軸がこの対象SKUを識別する文脈です。そのSKUについてだけ判定し、別SKUへ一般化しないでください。

引用本文が存在することは、その記述が選択SKUに適用される証明ではありません。記述が購入対象の構成を明確に示さない限り、推測せず不明にしてください。

応答は既存の契約に従うJSON object一つです。キーは unit_id、winning_value、evidence_block_ids、reason の4つです。unit_idは入力値をそのまま返します。winning_valueは元の選択肢のいずれか一つを完全一致で返すか、不明なら null にします。winning_valueがnullならevidence_block_idsは空配列にします。値を選ぶ場合は判断に使った入力block_idを一つ以上返します。reasonは短い文字列にしてください。追加キーや説明文をJSONの外に付けないでください。"""


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_unit(unit: dict[str, Any]) -> tuple[str, list[str], str, str | None]:
    if not isinstance(unit, dict):
        raise ValueError("unit must be a mapping")
    unit_id = unit.get("group_id")
    kind = unit.get("kind")
    axis_name = unit.get("axis_name")
    target_document_id = unit.get("target_document_id")
    options = unit.get("option_values")
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit requires its original group_id")
    if kind not in {"shared", "paired"}:
        raise ValueError("unit kind must be shared or paired")
    if not isinstance(axis_name, str) or not axis_name:
        raise ValueError("unit requires the exact raw axis_name")
    if not isinstance(target_document_id, str) or not target_document_id:
        raise ValueError("unit requires a target_document_id")
    if (not isinstance(options, list) or not options
            or any(not isinstance(value, str) or value == "" for value in options)
            or len(set(options)) != len(options)):
        raise ValueError("unit requires unique, nonempty raw option_values")

    axes = unit.get("target_selected_axes_text")
    if kind == "shared":
        if axes not in (None, ""):
            raise ValueError("shared unit must not contain selected axes")
        if unit.get("source_request") is not None:
            raise ValueError("shared unit must not be bound to one selected-value request")
        model_request = unit.get("model_request")
        if not isinstance(model_request, dict) or model_request.get("id") != unit_id:
            raise ValueError("shared unit requires its original model_request binding")
        choices = model_request.get("choices")
        if (not isinstance(choices, list)
                or [choice.get("label") for choice in choices if isinstance(choice, dict)]
                != options + [UNKNOWN_LABEL]
                or len(choices) != len(options) + 1):
            raise ValueError("shared model_request options differ from exact raw unit options")
        if unit.get("model_request", {}).get("selected_value") is not None:
            raise ValueError("shared model_request must not contain a desired selected value")
        return unit_id, options, target_document_id, None

    if not isinstance(axes, str) or not axes:
        raise ValueError("paired unit requires exact target_selected_axes_text")
    source_request = unit.get("source_request")
    if not isinstance(source_request, dict):
        raise ValueError("paired unit requires its original source_request binding")
    # These fields bind the input packet, but selected_value is deliberately
    # neither copied nor used to choose a response.
    if (source_request.get("request_id") != unit_id
            or source_request.get("target_document_id") != target_document_id
            or source_request.get("axis_name") != axis_name
            or source_request.get("option_values") != options
            or source_request.get("target_selected_axes_text") != axes):
        raise ValueError("paired unit/source_request document, axis, options, or axes differ")
    if source_request.get("selected_value") not in options:
        raise ValueError("paired source_request selected_value is not an original raw option")
    return unit_id, options, target_document_id, axes


def _eligible_blocks(target_document_id: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        raise ValueError("blocks must be a list")
    selected = []
    seen_ids: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("each source block must be a mapping")
        # The caller may pass a multi-document block inventory. Only exact
        # target-document blocks can enter this packet.
        if block.get("target_document_id") != target_document_id:
            continue
        # The frozen source-block schema calls the displayed field
        # ``support_text``; the unchanged predicate reads ``text`` for its
        # keyword-heading check. Supply that alias only in its local input.
        predicate_input = dict(block)
        predicate_input.setdefault("text", block.get("support_text"))
        reason = COMPOSITION.exclusion_reason(predicate_input)
        if reason is not None:
            continue
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("eligible source block requires a nonempty block_id")
        if block_id in seen_ids:
            raise ValueError("eligible source block IDs must be unique")
        seen_ids.add(block_id)
        passage = block.get("model_passage")
        refs = block.get("quote_refs")
        if not isinstance(passage, str):
            raise ValueError("eligible source block requires exact model_passage")
        if not isinstance(refs, list) or not refs:
            raise ValueError("eligible source block requires description quote_refs")
        for ref in refs:
            if (not isinstance(ref, dict) or ref.get("document_id") != target_document_id
                    or ref.get("field") != "description"):
                raise ValueError("eligible quote refs must be description refs for the target document")
            expected_document_sha = block.get("target_document_sha256")
            if (expected_document_sha is not None
                    and ref.get("document_sha256") != expected_document_sha):
                raise ValueError("eligible quote ref document SHA differs from its source block")
        start, end = block.get("html_start"), block.get("html_end")
        if type(start) is not int or type(end) is not int or start < 0 or end < start:
            raise ValueError("eligible source block requires ordered integer html offsets")
        selected.append({"block_id": block_id, "model_passage": passage,
                         "quote_refs": refs, "html_start": start, "html_end": end})
    selected.sort(key=lambda block: (block["html_start"], block["html_end"], block["block_id"]))
    # Offsets are ordering metadata only; the model receives only the original
    # passage and its original literal quote references.
    return [{key: block[key] for key in ("block_id", "model_passage", "quote_refs")}
            for block in selected]


def build_packet(unit: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a complete source packet, or ``None`` when no block is usable.

    ``blocks`` may contain records for multiple documents; exact target-document
    filtering happens before the unchanged composition exclusion predicate.
    No truncation, normalization, or reconstruction is performed.
    """
    unit_id, options, document_id, axes = _validate_unit(unit)
    selected = _eligible_blocks(document_id, blocks)
    if not selected:
        return None
    packet: dict[str, Any] = {
        "unit_id": unit_id,
        "kind": unit["kind"],
        "axis_name": unit["axis_name"],
        "option_values": list(options),
        "target_document_id": document_id,
        "blocks": deepcopy(selected),
    }
    if unit["kind"] == "paired":
        packet["target_selected_axes_text"] = axes
    packet["packet_sha256"] = _canonical_sha(packet)
    return packet


def model_payload(packet: dict[str, Any]) -> dict[str, Any]:
    """Return the compact inference payload while leaving source refs packet-bound.

    Quote refs remain in the full packet for source validation and receipts; the
    model sees only the literal passage text, avoiding a duplicate copy of the
    underlying document quote in its context window.
    """
    if not isinstance(packet, dict) or not isinstance(packet.get("blocks"), list):
        raise ValueError("model payload requires a complete packet")
    expected = packet.get("packet_sha256")
    unsigned = {key: value for key, value in packet.items() if key != "packet_sha256"}
    if not isinstance(expected, str) or expected != _canonical_sha(unsigned):
        raise ValueError("packet SHA does not match the frozen complete packet")
    fields = ("unit_id", "kind", "axis_name", "option_values", "target_document_id")
    payload = {field: deepcopy(packet[field]) for field in fields}
    if packet["kind"] == "paired":
        axes = packet.get("target_selected_axes_text")
        if not isinstance(axes, str) or not axes:
            raise ValueError("paired packet requires exact selected axes")
        payload["target_selected_axes_text"] = axes
    elif packet["kind"] != "shared" or "target_selected_axes_text" in packet:
        raise ValueError("shared packet cannot contain selected axes")
    payload["blocks"] = []
    for block in packet["blocks"]:
        if (not isinstance(block, dict)
                or set(block) != {"block_id", "model_passage", "quote_refs"}
                or not isinstance(block["block_id"], str)
                or not isinstance(block["model_passage"], str)
                or not isinstance(block["quote_refs"], list)):
            raise ValueError("packet block differs from the complete source block schema")
        payload["blocks"].append({"block_id": block["block_id"],
                                  "model_passage": block["model_passage"]})
    if not payload["blocks"]:
        raise ValueError("empty packets must be recorded as unknown without model dispatch")
    payload["model_input_sha256"] = _canonical_sha(payload)
    return payload
