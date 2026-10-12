"""Add the complete ordered source-option list to each candidate's question."""
from __future__ import annotations

import json

import compound_product_choices_v1 as COMPOUND
import product_condition_sharing_v1 as SHARING


QUESTION = (
    "AU商品の実際の仕様・セット内容に当てはまる楽天側の「{axis}」の選択肢を1つ選ぶ。"
    "価格・追加料金・在庫は仕様の根拠にせず、特定できなければ不明を選ぶ。"
    "\n元の選択肢（順序保持JSON）："
)


def make_classification_requests(groups, blocks, rankings):
    """Build compound requests with all literal options visible in every query."""
    requests = COMPOUND.make_classification_requests(groups, blocks, rankings)
    axes = {group["group_id"]: group["axis_name"] for group in groups}
    for request in requests:
        labels = request["all_original_option_values"] + [SHARING.UNKNOWN]
        request["question"] = QUESTION.format(axis=axes[request["group_id"]]) + json.dumps(labels, ensure_ascii=False)
    return requests
