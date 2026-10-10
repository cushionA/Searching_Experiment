"""Conservative evidence handling for discrete adjustable dimensions."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

import sku_luna_dimension_guard as strict_guard

NUMBER = r"\d+(?:\.\d+)?"
DISCRETE_HEIGHT = re.compile(
    rf"(?:約\s*)?幅\s*({NUMBER})\s*[×xX*]\s*奥行(?:き)?\s*({NUMBER})\s*[×xX*]\s*高さ\s*({NUMBER})\s*/\s*({NUMBER})\s*(mm|cm|m)(?![a-z])"
)
ADJUSTMENT = re.compile(r"高さ.{0,12}(?:2|二)段階.{0,8}調節|高さ\s*\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*(?:mm|cm|m)の2段階")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

ADJUSTABLE_INSTRUCTIONS = """寸法根拠は、単位が原文で直接明記されたsourceを引用する。タイトルに単位がない場合、単位と部位が明示された同じscope=fixed_productの説明sourceを引用する。現在行以外の選択肢、関連商品、他scopeを使わない。
固定商品の同じ部位について、離散的な高さ設定と調節可能であることが同じ原文contextに明記され、SKU条件が別の固定高さを選択していない場合に限り、target高さをその設定値集合と照合できる。連続範囲や似た数値から推測しない。本体の異なるサイズや部位を混ぜない。元のchecks schemaと対象数を維持し、checks各要素はstatusとsource_idsのみ。
context_idsはcontext_blocksのキーを参照し、全原文context文字列を完全に復元して読む。内容の省略・要約・連結をしない。
"""


def _normalized(value):
    return unicodedata.normalize("NFKC", str(value)).replace("㎝", "cm").replace("㎜", "mm")


def _records(case):
    sources = case.get("sources", [])
    if isinstance(sources, dict):
        return {str(sid): item for sid, item in sources.items() if isinstance(item, dict)}
    if isinstance(sources, list):
        return {str(item.get("source_id")): item for item in sources
                if isinstance(item, dict) and item.get("source_id") is not None}
    return {}


def compact_request(request):
    """Deep-copy a full signature request and losslessly deduplicate source contexts."""
    compacted = copy.deepcopy(request)
    try:
        text = compacted["body"]["contents"][0]["parts"][0]["text"]
    except (KeyError, TypeError) as exc:
        raise ValueError("signature request has no first user text part") from exc
    marker = "INPUT="
    pos = text.find(marker)
    if pos < 0:
        raise ValueError("signature request is missing INPUT marker")
    payload = json.loads(text[pos + len(marker):])
    sources = payload.get("au_sources")
    if not isinstance(sources, dict):
        raise ValueError("signature INPUT au_sources must be an object")
    blocks, ids_by_text = {}, {}
    for source in sources.values():
        if not isinstance(source, dict):
            raise ValueError("signature source must be an object")
        original = source.pop("contexts", None)
        if not isinstance(original, list) or not all(isinstance(value, str) for value in original):
            raise ValueError("signature source contexts must be a string array")
        refs = []
        for value in original:
            if value not in ids_by_text:
                block_id = f"C{len(blocks)}"
                ids_by_text[value] = block_id
                blocks[block_id] = value
            refs.append(ids_by_text[value])
        source["context_ids"] = refs
    payload["context_blocks"] = blocks
    compacted["body"]["contents"][0]["parts"][0]["text"] = (
        text[:pos] + ADJUSTABLE_INSTRUCTIONS + marker +
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return compacted


def _current_row_matches(case, row_conditions):
    if not isinstance(row_conditions, list):
        return False
    return any(isinstance(row, dict) and row.get("conditions") == row_conditions
               for row in case.get("au_rows", []))


def _ref_pairs(value):
    refs = value if isinstance(value, list) else [value]
    pairs = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        raw, digest = ref.get("raw_file"), ref.get("sha256")
        if isinstance(raw, str) and raw and isinstance(digest, str) and SHA256.fullmatch(digest):
            pairs.add((raw, digest))
    return pairs


def _is_fixed_adjustable_source(case, sid, source_map, contexts):
    record = _records(case).get(str(sid))
    mapped = source_map.get(str(sid)) if isinstance(source_map, dict) else None
    context = contexts.get(str(sid)) if isinstance(contexts, dict) else None
    if not all(isinstance(x, dict) for x in (record, mapped, context)):
        return None
    quote = mapped.get("quote")
    if not isinstance(quote, str) or not quote:
        return None
    if record.get("kind") != "description" or record.get("text") != quote:
        return None
    if context.get("kind") != "description" or context.get("scope") != "fixed_product" or context.get("text") != quote:
        return None
    record_refs = record.get("source", record.get("provenance"))
    if mapped.get("provenance") != record_refs:
        return None
    source_pairs = _ref_pairs(record_refs)
    context_pairs = _ref_pairs(context.get("provenance"))
    extracted_pairs = _ref_pairs(context.get("context_provenance"))
    if (not source_pairs or not source_pairs.intersection(context_pairs) or
            not source_pairs.intersection(extracted_pairs)):
        return None
    context_texts = context.get("contexts")
    if not isinstance(context_texts, list) or not context_texts:
        return None
    quote_norm = _normalized(quote)
    candidates = []
    for text in context_texts:
        if not isinstance(text, str):
            continue
        normalized = _normalized(text)
        if quote_norm not in normalized or not ADJUSTMENT.search(normalized):
            continue
        if any(marker in quote_norm for marker in ("座面", "収納", "折りたたみ", "折り畳み", "別商品")):
            continue
        match = DISCRETE_HEIGHT.search(quote_norm)
        if not match:
            continue
        context_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        candidates.append((match, text, context_hash, source_pairs.intersection(context_pairs)))
    return candidates[0] if len(candidates) == 1 else None


def _row_selects_fixed_height(row_conditions):
    for condition in row_conditions:
        if not isinstance(condition, dict):
            continue
        axis, value = _normalized(condition.get("axis", "")), _normalized(condition.get("value", ""))
        if ("高さ" in axis or "height" in axis.lower()) and re.search(NUMBER, value):
            return True
        if re.search(rf"高さ\s*{NUMBER}\s*(?:mm|cm|m)?", value):
            return True
    return False


def _eligible_alias(case, target, check, source_map, row_conditions, contexts):
    if strict_guard.axis_kind(target.get("axis", "")) != "height":
        return None
    target_axis = _normalized(target.get("axis", ""))
    if any(part in target_axis for part in ("座面", "収納", "折りたたみ", "折り畳み")):
        return None
    if _row_selects_fixed_height(row_conditions):
        return None
    try:
        expected = Decimal(_normalized(target["value"]).strip()) * strict_guard.SCALE[target["unit"]]
    except (KeyError, InvalidOperation, TypeError):
        return None
    if not isinstance(check, dict) or check.get("status") != "support":
        return None
    matches = []
    for sid in check.get("source_ids", []):
        if not str(sid).startswith("S"):
            continue
        verified = _is_fixed_adjustable_source(case, sid, source_map, contexts)
        if verified is None:
            continue
        parsed, context_text, context_hash, provenance_pairs = verified
        height_values = [Decimal(parsed.group(3)) * strict_guard.SCALE[parsed.group(5)],
                         Decimal(parsed.group(4)) * strict_guard.SCALE[parsed.group(5)]]
        if expected not in height_values:
            continue
        chosen = parsed.group(3) if expected == height_values[0] else parsed.group(4)
        original_quote = source_map[sid]["quote"]
        replacement = parsed.group(0).replace(f"{parsed.group(3)}/{parsed.group(4)}", chosen, 1)
        proxy_quote = original_quote.replace(parsed.group(0), replacement, 1)
        if proxy_quote == original_quote:
            continue
        matches.append({"source_id": str(sid), "original_quote": original_quote,
                        "proxy_quote": proxy_quote, "context_sha256": context_hash,
                        "source_context_sha256_pairs": sorted([list(x) for x in provenance_pairs]),
                        "settings": [parsed.group(3), parsed.group(4)], "unit": parsed.group(5),
                        "target": {k: target.get(k) for k in ("axis", "value", "unit")}})
    return matches[0] if len(matches) == 1 else None


def _build_aliases(case, targets, answer, source_map, row_conditions, contexts):
    if not _current_row_matches(case, row_conditions) or not isinstance(answer, dict):
        return copy.deepcopy(source_map), []
    temporary = copy.deepcopy(source_map)
    checks = answer.get("checks")
    if not isinstance(checks, list) or len(checks) != len(targets):
        return temporary, []
    audit = []
    for index, (target, check) in enumerate(zip(targets, checks, strict=True)):
        alias = _eligible_alias(case, target, check, source_map, row_conditions, contexts)
        if alias is None:
            continue
        temporary[alias["source_id"]]["quote"] = alias["proxy_quote"]
        audit.append({"target_index": index, **alias,
                      "reason": "verified_same_part_discrete_adjustable_height_setting"})
    return temporary, audit


def adjustable_dimension_alias_audit(case, targets, answer, source_map, row_conditions, contexts):
    """Return audit records for only source/context-bound discrete aliases."""
    _, audit = _build_aliases(case, targets, answer, source_map, row_conditions, contexts)
    return audit


def guard_adjustable_dimensions(case, targets, answer, source_map, row_conditions, contexts):
    """Run the frozen strict guard with narrowly verified temporary quote aliases."""
    temporary, audit = _build_aliases(case, targets, answer, source_map, row_conditions, contexts)
    guarded, issues = strict_guard.guard_dimensions(targets, answer, temporary, row_conditions)
    return guarded, issues
