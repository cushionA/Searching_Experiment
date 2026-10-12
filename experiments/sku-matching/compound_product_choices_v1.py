"""Classification prompts that compare a product to every literal source option.

This is a pure input builder. It preserves the product model's frozen state
rendering and does not inspect labels, wanted values, selected rows, or option
semantics when constructing the prompt.
"""
from __future__ import annotations

from copy import deepcopy

import product_condition_sharing_v1 as SHARING
import trial_product_condition_models_v1 as PRODUCT


QUESTION = (
    "楽天側の「{axis}」について、次の元の選択肢から、AU商品に当てはまるものを1つ選んでください。"
    "商品名と提示された商品説明から実際の仕様・セット内容を判断してください。"
    "価格、追加料金、在庫状況は商品仕様を判断する根拠に使わないでください。"
    "根拠が不足し、どの選択肢か特定できない場合は不明を選んでください。"
)
DESCRIPTION = "このAU商品の実際の仕様・セット内容は「{axis}：{value}」に当てはまる。"
UNKNOWN_DESCRIPTION = "元のどの選択肢か、商品名と提示された説明から特定できない。"


def _source_values(group):
    values = group.get("option_values")
    choices = group.get("model_request", {}).get("choices")
    if (not isinstance(values, list) or not values or
            any(not isinstance(value, str) or not value for value in values) or
            len(values) != len(set(values))):
        raise ValueError("ordered raw source option values required")
    if not isinstance(choices, list) or [choice.get("label") for choice in choices] != values + [SHARING.UNKNOWN]:
        raise ValueError("source choices must preserve all raw options and one unknown")
    if SHARING.UNKNOWN in values:
        raise ValueError("raw source option collides with the reserved unknown label")
    return values


def make_classification_requests(groups, blocks, rankings):
    """Return the frozen PRODUCT request metadata with compound-choice wording."""
    requests = []
    for group in groups:
        values = _source_values(group)
        axis = group.get("axis_name")
        if not isinstance(axis, str) or not axis:
            raise ValueError("raw source axis required")
        ordered_ids = rankings.get(group["group_id"], {}).get("ordered_block_ids", [])[:PRODUCT.TOP_K]
        for index, block_id in enumerate(ordered_ids):
            block = blocks[block_id]
            candidates = [
                {"label": value, "description": DESCRIPTION.format(axis=axis, value=value)}
                for value in values
            ]
            candidates.append({"label": SHARING.UNKNOWN, "description": UNKNOWN_DESCRIPTION})
            requests.append({
                "id": group["group_id"] + ":block:" + str(index),
                "group_id": group["group_id"],
                "group_sha256": group["group_sha256"],
                "block_id": block_id,
                "block_rank": index + 1,
                "question": QUESTION.format(axis=axis),
                "state": PRODUCT.state_for(group, block),
                "choices": candidates,
                "unknown_control_label": SHARING.UNKNOWN,
                "all_original_option_values": deepcopy(values),
            })
    return requests
