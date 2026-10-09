"""Selected SKU attributes with cited fixed-page sales conditions.

No source ID rules, prices, inventory, annotation labels, or prior predictions.
The contents-list completeness assumption is explicit. Missing title words
never establish an absent component.
"""
from __future__ import annotations
from functools import lru_cache
import json
import re
import unicodedata

TASK_VERSION = "structured-sku-task-v2"
_HEADINGS = {"内容": "contents", "セット内容": "contents", "付属品": "accessories",
             "サイズ": "dimensions", "サイズ(約)": "dimensions", "寸法": "dimensions",
             "カラー": "colors", "色": "colors", "材質": "material", "素材": "material",
             "特徴": "features", "重量": "weight", "耐荷重": "load", "保証期間": "ignore",
             "注意事項": "notes", "備考": "notes", "配送について": "ignore"}


def _norm(value):
    return unicodedata.normalize("NFKC", str(value)).strip()


def _size(value):
    text = re.sub(r"\s+", "", _norm(value)).replace("x", "×").replace("X", "×").replace("＊", "×")
    text = re.sub(r"\(約\)|\(\d+枚(?:組|セット)?\)", "", text)
    text = re.sub(r"幅|丈|奥行き?|高さ|横幅|縦幅|厚さ", "", text)
    return re.sub(r"cm(?=×)", "", text, flags=re.I)


def _axis_key(name):
    name = _norm(name)
    if "レース" in name and ("枚数" in name or "個数" in name): return "lace_count"
    if "ドレープ" in name and "枚数" in name: return "drape_count"
    if "レース" in name: return "lace"
    if "カラー" in name or name in {"色", "color", "colour"}: return "color"
    if name in {"サイズ", "寸法", "size"}: return "size"
    if name in {"タイプ", "種類", "商品タイプ"}: return "product_variant"
    if name in {"容量", "内容量"}: return "product_capacity"
    return "axis:" + (name or "unlabeled")


def _value(key, value):
    value = _norm(value)
    if key == "lace":
        if value in {"なし", "無", "不要", "含まない", "付属しない"}: return False
        if value in {"あり", "有", "付属", "付き", "含む"}: return True
        return None
    if key == "size": return _size(value)
    if key == "color": return re.sub(r"\((?:[12]級)?遮光(?:カーテン)?\)(?:≪NEW≫)?$", "", value)
    return value or None


def _axes(pairs, source):
    attrs, evidence, unknown, unresolved = {}, {}, [], []
    for name, raw in pairs:
        if not str(raw).strip(): continue
        key, value = _axis_key(name), _value(_axis_key(name), raw)
        if key in attrs and attrs[key] != value: unknown.append(key); value = None
        attrs[key] = value
        evidence.setdefault(key, []).append({"quote": f"{name}={raw}", "source_ref": source})
        if key == "size":
            panels = re.search(r"\((\d+)枚(?:組|セット)?\)", _norm(raw))
            if panels:
                attrs["panel_count"] = int(panels.group(1))
                evidence["panel_count"] = list(evidence[key])
        if value is None: unknown.append(key)
        if key.startswith("axis:"): unresolved.append(key)
    return {"attrs": attrs, "facts": dict(attrs), "selected_fields": sorted(attrs), "evidence": evidence,
            "unknown_fields": sorted(set(unknown)), "unresolved_axes": sorted(set(unresolved))}


def _description_lines(product, side):
    desc = product.get("description", {})
    source = desc.get("source", product.get("source", {}))
    if side == "au":
        rows = []
        for i, block in enumerate(desc.get("blocks", [])):
            scope = block.get("scope", "")
            if any(x in scope for x in ("series", "sibling", "navigation", "related")): continue
            ref = {"raw_file": source.get("raw_file"), "sha256": source.get("sha256"),
                   "json_path": block.get("source_field"), "block_index": i}
            rows.extend((line.strip(), ref) for line in block.get("text", "").splitlines() if line.strip())
        return rows
    ref = {"raw_file": source.get("raw_file"), "sha256": source.get("sha256"), "json_path": "Rakuten item_desc text"}
    return [(line.strip(), {**ref, "line": i + 1}) for i, line in enumerate(
        desc.get("individual_description_excerpt", "").splitlines()) if line.strip()]


@lru_cache(maxsize=64)
def _page_cached(product_json, side):
    lines = _description_lines(json.loads(product_json), side)
    section, condition = None, None
    contents, dimensions, conflicts = [], [], []
    for raw, ref in lines:
        line = _norm(raw)
        compact = re.sub(r"\s+", "", line).strip(":")
        if compact in _HEADINGS: section, condition = _HEADINGS[compact], None; continue
        if compact in {"商品詳細", "商品仕様", "仕様"}: section, condition = None, None; continue
        heading = re.fullmatch(r"【(.+)】", compact)
        if heading: condition = heading.group(1); continue
        citation = {"quote": raw, "source_ref": ref}
        if section in {"contents", "accessories"}:
            contents.append({"condition": condition, "line": line, "citation": citation,
                             "complete_contents_scope": section == "contents"})
        if section == "dimensions":
            match = re.search(r"幅\s*(\d+(?:\.\d+)?)\s*[×x]\s*奥行(?:き)?\s*(\d+(?:\.\d+)?)\s*[×x]\s*高さ\s*(\d+(?:\.\d+)?)\s*cm", line)
            if match: dimensions.append({"condition": condition, "values": [float(x) for x in match.groups()], "citation": citation})
    handles, exceptions = [], []
    for raw, ref in lines:
        material = re.search(r"持ち手[:：]\s*([^。、!！\n]+)", _norm(raw))
        prose = re.search(r"(ポリエステル|プラスチック(?:メッキ)?)製持ち手", _norm(raw))
        if material or prose: handles.append(((material or prose).group(1).strip(), {"quote": raw, "source_ref": ref}))
        exception = re.search(r"高さ(\d+(?:\.\d+)?)cmの([^、。]+?)には持ち手がありません", _norm(raw))
        if exception:
            exceptions.append({"conditions": {"axis:高さ": exception.group(1) + "cm", "color": exception.group(2)},
                               "field": "handle_included", "value": False,
                               "citation": {"quote": raw, "source_ref": ref}})
    if len({x[0] for x in handles}) > 1:
        conflicts.append({"field": "handle_material", "values": sorted({x[0] for x in handles}), "evidence": [x[1] for x in handles]})
    return {"contents": contents, "dimensions": dimensions, "internal_conflicts": conflicts,
            "conditional_exceptions": exceptions}


def _condition_matches(condition, attrs):
    if condition is None: return True
    width = re.fullmatch(r"幅(\d+(?:\.\d+)?)cm", condition)
    if width:
        selected = re.match(r"(\d+(?:\.\d+)?)×", str(attrs.get("size", "")))
        return selected is not None and float(selected.group(1)) == float(width.group(1))
    variant = str(attrs.get("product_variant", ""))
    return bool(variant and condition.replace("タイプ", "") == variant.replace("タイプ", ""))


def _inherit(entity, page, side):
    attrs, ev = entity["attrs"], entity["evidence"]
    contents = [x for x in page["contents"] if _condition_matches(x["condition"], attrs)]
    if "lace" in attrs or any("カーテン" in x["line"] for x in contents):
        attrs["category"] = "curtain"
        for row in contents:
            line = row["line"]
            if re.search(r"レース(?:カーテン)?(?:なし|を含まない|付属しない)", line):
                attrs.setdefault("lace", False)
                attrs.setdefault("lace_count", 0)
                ev.setdefault("lace", []).append(row["citation"])
                ev.setdefault("lace_count", []).append(row["citation"])
            conditional_lace = "レース" in line and any(w in line for w in ("選択の場合", "選択時", "選択の方"))
            if conditional_lace and attrs.get("lace") is None: continue
            for pattern, key in ((r"レースカーテン\s*(\d+)\s*(?:枚|個)", "lace_count"),
                                 (r"^(?:遮光|ドレープ)?カーテン\s*(\d+)\s*枚", "drape_count"),
                                 (r"(?:カーテン)?フック\s*(\d+)\s*個", "hook_count"),
                                 (r"タッセル\s*(\d+)\s*(?:枚|個)", "tassel_count")):
                match = re.search(pattern, line)
                if match:
                    count = int(match.group(1))
                    if key == "lace_count" and conditional_lace and attrs.get("lace") is False: count = 0
                    if key == "lace_count" and not conditional_lace and attrs.get("lace") is False and count > 0:
                        entity.setdefault("source_conflicts", []).append({"field": "lace_inclusion", "values": [False, count]})
                    attrs[key] = count; ev.setdefault(key, []).append(row["citation"])
        if "lace_count" in attrs and attrs.get("lace") is None:
            attrs["lace"] = attrs["lace_count"] > 0; ev["lace"] = list(ev["lace_count"])
        if attrs.get("lace") is None and contents and attrs.get("drape_count") is not None:
            complete = [x for x in contents if x["complete_contents_scope"]]
            if complete and not any("レース" in x["line"] for x in complete):
                attrs["lace"], attrs["lace_count"] = False, 0
                ev["lace"] = [x["citation"] for x in complete]; ev["lace_count"] = list(ev["lace"])
                entity["contents_list_assumption"] = "explicit contents list treated as exhaustive; not absence of title keyword"
        if attrs.get("lace") is False: attrs.setdefault("lace_count", 0)
        if attrs.get("drape_count") is not None and attrs.get("lace_count") is not None:
            total = attrs["drape_count"] + attrs["lace_count"]
            if attrs.get("panel_count") is not None and attrs["panel_count"] != total:
                entity.setdefault("source_conflicts", []).append({"field": "panel_count", "values": [attrs["panel_count"], total]})
            else:
                attrs["panel_count"] = total
                ev["panel_count"] = ev.get("drape_count", []) + ev.get("lace_count", [])
        for key in ("size", "color", "lace", "drape_count", "lace_count"): attrs.setdefault(key, None)
    eligible_dims = [d for d in page["dimensions"] if _condition_matches(d["condition"], attrs)]
    unique_dims = {tuple(d["values"]) for d in eligible_dims}
    if len(unique_dims) == 1:
        attrs["page_dimensions_cm"] = list(next(iter(unique_dims))); ev["page_dimensions_cm"] = [d["citation"] for d in eligible_dims]
    elif len(unique_dims) > 1:
        entity.setdefault("source_conflicts", []).append({"field": "page_dimensions_cm", "values": [list(x) for x in sorted(unique_dims)]})
    if page["internal_conflicts"]: entity.setdefault("source_conflicts", []).extend(page["internal_conflicts"])
    for exception in page["conditional_exceptions"]:
        if all(attrs.get(k) == v for k, v in exception["conditions"].items()):
            attrs[exception["field"]] = exception["value"]
            ev.setdefault(exception["field"], []).append(exception["citation"])
    entity["unknown_fields"] = sorted(set(entity["unknown_fields"]) | {k for k, v in attrs.items() if v is None})
    entity["facts"] = dict(attrs)
    return entity


def build_task(case_ready, dossier):
    original = case_ready.get("rakuten", {})
    labels = {x.get("key"): x.get("label") or x.get("name") or x.get("key") for x in original.get("axes_labels", [])}
    pairs = [(x.get("axis_name") or labels.get(x.get("axis_key")) or x.get("axis_key"), x.get("value", "")) for x in original.get("option_values", [])]
    if not pairs: pairs = [x.split("=", 1) for x in case_ready.get("query_sku", "").split(" / ") if "=" in x]
    rak_product, au_product = dossier.get("rakuten_product", {}), dossier.get("au_product", {})
    rak_page = _page_cached(json.dumps(rak_product, ensure_ascii=False, sort_keys=True), "rakuten")
    au_page = _page_cached(json.dumps(au_product, ensure_ascii=False, sort_keys=True), "au")
    query = _axes(pairs, original.get("source", rak_product.get("source", {})))
    query["raw_sku"] = case_ready.get("query_sku", ""); _inherit(query, rak_page, "rakuten")
    rows_by_key = {r["row_key"]: r for r in dossier.get("au_rows", [])}
    candidates = []
    for row in case_ready.get("candidates", []):
        source_row = rows_by_key.get(row["row_key"], {})
        pairs = [(a.get("axis_name_raw", ""), a.get("value_raw", "")) for a in source_row.get("axes_raw", [])]
        if not pairs: pairs = [x.split("=", 1) for x in row["text_sku"].split(" / ") if "=" in x]
        candidate = _axes(pairs, {"json_path": row["row_key"], "raw_file": "au_product_sku_arrays.jsonl"})
        candidate.update(row_key=row["row_key"], raw_sku=row["text_sku"]); _inherit(candidate, au_page, "au")
        candidates.append(candidate)
    strata = ["fixed_au_product", "all_actual_sku_candidates"]
    if query["attrs"].get("category") == "curtain":
        strata.append("curtain")
        width = re.match(r"(\d+)×", str(query["attrs"].get("size", "")))
        if width: strata.append("curtain_width_" + width.group(1))
        strata.append("rakuten_lace_" + str(query["attrs"].get("lace")).lower())
    else: strata.append("non_curtain")
    if set(query["attrs"]) != set(candidates[0]["attrs"]): strata.append("asymmetric_attributes")
    if query.get("source_conflicts") or any(c.get("source_conflicts") for c in candidates): strata.append("internal_source_conflict")
    return {"case_id": case_ready["case_id"], "task_version": TASK_VERSION, "rakuten": query, "au_candidates": candidates,
            "page_context": {"category": query["attrs"].get("category"), "internal_source_conflicts": au_page["internal_conflicts"]}, "strata": strata}
