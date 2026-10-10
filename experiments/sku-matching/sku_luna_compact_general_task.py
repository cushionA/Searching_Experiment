"""General lossless compact-context wrapper for shaped SKU requests."""
from __future__ import annotations

import copy
import json

import sku_luna_closed_contents_task as closed
import sku_luna_output_shape as shape

COMPACT_GENERAL_VERSION = "selected-sku-scoped-bidirectional-v3-general-compact-context"
COMPACT_INSTRUCTIONS = """context_idsはcontext_blocksのキーを参照し、参照先の文字列をすべて完全な原文contextとして扱う。内容の省略・要約・連結はしない。
同じsource_idとAU rowのscopeを維持し、楽天条件・選択済み属性・全AU行・全check_targetsをそのまま照合する。OUTPUT_SHAPEに従いrowsは入力順、楽天/AU checksは指定個数だけ返す。各checkはstatusとsource_idsのみ。
row_indexは入力照合用のヒントであり、出力に含めない。元の数量・型・ブランド等すべての制約を維持する。選択肢の代替値を推測せず、根拠が不十分ならunknownとする。
bounded_contentsがある場合は、scope付き固定AU商品の「内容」原文HTML行と直後のsectionラベルであり、一般的な閉じた商品contents一覧かを根拠から判断する。明示された完全な一覧と数量対応が構成品の有無を示す場合がある。単なる記載欠落や不完全・範囲不明の表はunknownとし、結果を強制しない。異なるsourceやscopeを混ぜない。
厳密にresponseSchemaどおりのJSONを返す。rootにrowsのみ、各rowにrakuten_checksとau_checksのみ、各checkにstatusとsource_idsのみを含める。行番号・row_indexは出力しない。
COMPACT_CONTEXT="""


def _contents_source_status(case, contexts):
    source_map = closed._sources(contexts)
    titles = [s for s in source_map.values() if s.get("kind") == "title" and s.get("scope") == "fixed_product"]
    if not titles or not any("カーテン" in str(source.get("text", "")) for source in titles):
        return None, "fixed_product_title_not_curtain"
    contents = [s for s in source_map.values() if s.get("kind") == "description"
                and s.get("scope") == "fixed_product" and str(s.get("text", "")).strip() == "内容"]
    if len(contents) != 1:
        return None, "fixed_product_contents_label_not_unique"
    try:
        block = closed._raw_contents_block(case, contexts)
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, "bounded_contents_unavailable:" + type(exc).__name__
    return {"source_id": block["source_id"], "scope": block["scope"], "kind": block["kind"],
            "html_row": block["raw_bounded_html_row"],
            "next_section_label": block["next_section_label"]}, "available"


def compact_request(case, contexts):
    """Return a normal v3-shaped request with losslessly deduplicated contexts."""
    request = copy.deepcopy(shape.output_shape_request(case, contexts))
    text = request["body"]["contents"][0]["parts"][0]["text"]
    marker = "INPUT="
    pos = text.find(marker)
    if pos < 0:
        raise ValueError("v3 prompt is missing INPUT marker")
    input_start = pos + len(marker)
    prefix, payload = text[:pos], json.loads(text[input_start:])
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("v3 INPUT sources must be an object")

    blocks, ids_by_text = {}, {}
    for source in sources.values():
        original = source.pop("contexts", None)
        if not isinstance(original, list) or not all(isinstance(value, str) for value in original):
            raise ValueError("each v3 source must have a string contexts list")
        refs = []
        for value in original:
            if value not in ids_by_text:
                block_id = f"C{len(blocks)}"
                ids_by_text[value] = block_id
                blocks[block_id] = value
            refs.append(ids_by_text[value])
        source["context_ids"] = refs
    payload["context_blocks"] = blocks

    bounded, reason = _contents_source_status(case, contexts)
    if bounded is not None:
        payload["bounded_contents"] = bounded
    # Keep this audit metadata outside request.body; providers receive only the
    # original body contents and response schema.
    request["local_metadata"] = {"bounded_context_available": bounded is not None,
                                  "bounded_context_reason": reason}
    request["body"]["contents"][0]["parts"][0]["text"] = (
        prefix + COMPACT_INSTRUCTIONS + "\nINPUT=" +
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return request
