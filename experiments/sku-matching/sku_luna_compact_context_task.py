"""Lossless source-context deduplication for v3-shaped SKU requests."""
from __future__ import annotations

import copy
import json

import sku_luna_closed_contents_task as closed
import sku_luna_output_shape as shape

COMPACT_VERSION = "selected-sku-scoped-bidirectional-v3-compact-context"
COMPACT_INSTRUCTIONS = """context_idsはcontext_blocksのキーを参照し、参照先の文字列をすべて完全な原文contextとして扱う。内容の省略・要約・連結はしない。
同じsource_idとAU rowのscopeを維持し、楽天条件・選択済み属性・全AU行・全check_targetsをそのまま照合する。OUTPUT_SHAPEに従ってrowsは入力順、楽天/AU checksは指定個数だけ返す。各checkはstatusとsource_idsのみ。
row_indexは入力照合用のヒントであり、出力に含めない。すべての元のquantity、type、brand等の制約を維持する。
bounded_contentsは固定商品の「内容」HTML行と直後のsectionラベルを示す。明示された完全な幅別contents一覧と数量対応が同梱物の不存在を裏付ける場合がある。単なる記載欠落や不完全・範囲不明の表はunknownとする。結果を強制せず、異なるsource/scopeを混ぜない。
COMPACT_CONTEXT="""


def _input_location(text):
    marker = "INPUT="
    pos = text.find(marker)
    if pos < 0:
        raise ValueError("v3 prompt is missing INPUT marker")
    return pos + len(marker)


def compact_request(case, contexts):
    """Wrap output-shaped v3 request, deduplicating contexts without content loss."""
    request = copy.deepcopy(shape.output_shape_request(case, contexts))
    text = request["body"]["contents"][0]["parts"][0]["text"]
    input_start = _input_location(text)
    prefix, serialized = text[:input_start - len("INPUT=" )], text[input_start:]
    payload = json.loads(serialized)
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("v3 INPUT sources must be an object")

    context_blocks = {}
    block_by_text = {}
    for source in sources.values():
        original = source.pop("contexts", None)
        if not isinstance(original, list) or not all(isinstance(item, str) for item in original):
            raise ValueError("each v3 source must have a string contexts list")
        refs = []
        for value in original:
            if value not in block_by_text:
                block_id = f"C{len(context_blocks)}"
                block_by_text[value] = block_id
                context_blocks[block_id] = value
            refs.append(block_by_text[value])
        source["context_ids"] = refs

    bounded = closed._raw_contents_block(case, contexts)
    payload["context_blocks"] = context_blocks
    payload["bounded_contents"] = {
        "source_id": bounded["source_id"],
        "scope": bounded["scope"],
        "kind": bounded["kind"],
        "html_row": bounded["raw_bounded_html_row"],
        "next_section_label": bounded["next_section_label"],
    }
    request["body"]["contents"][0]["parts"][0]["text"] = (
        prefix + COMPACT_INSTRUCTIONS + "\nINPUT=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    return request
