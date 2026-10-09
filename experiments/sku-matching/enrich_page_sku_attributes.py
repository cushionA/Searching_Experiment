"""Resolve selected SKU fields against explicit, scoped page specifications.

This module is deliberately source-only. A title may act only as a narrow
selector for an explicit body crosswalk; title text never supplies a fact.
Prices, stock, labels, and predictions are not inspected. Unresolved or
conflicting page facts are returned as review metadata; input is never changed.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any


_AU_SCOPES = {"product_page_extra_comment", "product_page_comment", "product_page_detail_comment"}
_SECTION_HEADINGS = {
    "内容": "contents", "セット内容": "contents", "付属品": "accessories",
    "サイズ": "dimensions", "寸法": "dimensions", "寸法(約)": "dimensions",
    "サイズ(約)": "dimensions", "サイズ/重さ": "dimensions", "サイズ・重さ": "dimensions",
    "カラー": "colors", "色": "colors", "特徴": "features", "機能": "features",
    "タイプ": "variants", "種類": "variants", "商品タイプ": "variants",
    "生地": "materials", "素材": "materials", "材質": "materials",
    "段数": "variants", "選べる段数": "variants", "重量": "ignore",
    "重量(約)": "ignore", "重さ": "ignore", "固定用スナップ": "ignore", "仕上げ加工": "ignore",
    "耐荷重": "ignore", "保証期間": "ignore", "注意事項": "ignore",
    "備考": "ignore", "配送について": "ignore", "送料": "ignore",
}
_ROLE_HEADINGS = {
    "本体": "body", "本体サイズ": "body", "収納サイズ": "folded",
    "使用時": "body", "使用時サイズ": "body", "使用時(約)": "body",
    "折りたたみサイズ": "folded", "折畳みサイズ": "folded",
    "折り畳み時": "folded", "折りたたみ時": "folded",
    "天板": "tabletop", "天板サイズ": "tabletop", "座面": "seat",
    "座面サイズ": "seat", "バスケット": "basket", "バスケット外寸": "basket",
    "キャリー": "carrier", "パネル": "panel", "ドアパーツ": "door",
    "マットレス": "mattress", "敷きパッド": "mattress",
}
_ROLE_FIELDS = {
    "body": "body_dimensions_cm", "folded": "folded_dimensions_cm",
    "tabletop": "tabletop_size_cm", "seat": "seat_size_cm",
    "basket": "basket_dimensions_cm", "carrier": "carrier_dimensions_cm",
    "panel": "panel_size_cm", "door": "door_size_cm",
    "mattress": "size_cm",
}
_AXIS_ALIASES = {
    "段数": "tier_count", "段": "tier_count", "階数": "tier_count",
    "直径": "diameter_cm", "円形サイズ": "diameter_cm",
    "幅": "width_cm", "横幅": "width_cm", "高さ": "height_cm",
    "奥行": "depth_cm", "奥行き": "depth_cm", "長さ": "length_cm",
    "厚さ": "thickness_cm", "厚み": "thickness_cm",
    "生地": "fabric_variant", "素材": "material_variant",
    "材質": "material_variant", "タイプ": "product_variant",
    "種類": "product_variant", "商品タイプ": "product_variant",
}
_UNIT = r"(?:mm|cm|m)"
_NUM = r"\d+(?:\.\d+)?"
_SIZED_PRODUCT_PREFIX = (
    "敷きパッド", "敷パッド", "マットレス", "毛布", "掛け布団カバー",
    "布団カバー", "ブランケット",
)
_TITLE_SIZE_TOKENS = (
    "セミシングル", "セミダブル", "シングル", "ダブル", "クイーン", "キング",
    "クォーター", "ハーフ", "XS", "SS", "SD", "S", "D", "Q", "K",
)


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s　]", "", _norm(value)).casefold()


def _to_cm(number: str, unit: str) -> float:
    value = float(number)
    unit = unit.lower()
    return value / 10.0 if unit == "mm" else value * 100.0 if unit == "m" else value


def _source_rows(product: dict[str, Any], side: str) -> list[tuple[str, dict[str, Any]]]:
    desc = product.get("description") or {}
    source = desc.get("source") or product.get("source") or {}
    raw_file, sha = source.get("raw_file"), source.get("sha256")
    if side == "au":
        rows = []
        for index, block in enumerate(desc.get("blocks", [])):
            if block.get("scope") not in _AU_SCOPES:
                continue
            ref = {"raw_file": raw_file, "sha256": sha,
                   "json_path": block.get("source_field", "description.blocks"),
                   "block_index": index}
            for line_no, line in enumerate(str(block.get("text", "")).splitlines(), 1):
                line = line.strip()
                if line:
                    rows.append((line, {**ref, "line": line_no}))
        return rows
    excerpt = str(desc.get("individual_description_excerpt") or "")
    ref = {"raw_file": raw_file, "sha256": sha,
           "json_path": (source.get("json_path") or "description.individual_description_excerpt")}
    rows = []
    reviews = False
    for line_no, line in enumerate(excerpt.splitlines(), 1):
        line = line.strip()
        if any(marker in line for marker in ("この商品を購入された方のレビュー", "このショップの人気商品ランキング")):
            reviews = True
        if line and not reviews:
            rows.append((line, {**ref, "line": line_no}))
    return rows


def _selector_key(name: str) -> str | None:
    clean = _norm(name)
    if clean.startswith("axis:"):
        clean = clean[5:]
    if "段" in clean and any(token in clean for token in ("段数", "段")):
        return "tier_count"
    if "レース" in clean:
        return None
    if "カラー" in clean or clean in {"色", "color", "colour"}:
        return "color"
    if clean in {"サイズ", "寸法", "size"}:
        return "size"
    if "タイプ" in clean or clean in {"種類", "商品タイプ"}:
        return "product_variant"
    for alias, key in _AXIS_ALIASES.items():
        if clean == alias or (len(alias) > 1 and alias in clean):
            return key
    if clean in {"枚数", "個数", "数量", "セット数"}:
        return "bundle_count"
    if clean in {"オプション", "option"}:
        return "bundle_option"
    if clean in {"生地", "素材", "材質"}:
        return "fabric_variant" if clean == "生地" else "material_variant"
    return None


def _number_token(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", _norm(value))
    return float(match.group()) if match else None


def _canonical_selection(entity: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return safe label-based selector roles and original selected attrs."""
    original = dict(entity.get("attrs") or {})
    selected: dict[str, Any] = {}
    replacements: dict[str, str] = {}
    for raw_key, value in original.items():
        if value is None:
            continue
        key = _selector_key(str(raw_key))
        if key is None:
            continue
        if key == "tier_count":
            number = _number_token(value)
            if number is not None:
                selected[key] = int(number) if number.is_integer() else number
                replacements[raw_key] = key
        elif key.endswith("_cm"):
            number = _number_token(value)
            if number is not None:
                unit = re.search(r"(mm|cm|m)\b", _norm(value), re.I)
                if unit:
                    selected[key] = _to_cm(str(number), unit.group(1))
                    replacements[raw_key] = key
        elif key == "bundle_count":
            number = _number_token(value)
            if number is not None:
                selected[key] = int(number) if number.is_integer() else number
                replacements[raw_key] = key
        elif key == "size":
            selected[key] = _norm(value)
            diameter = re.search(r"(?:直径|径)\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)", _norm(value), re.I)
            if not diameter:
                diameter = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm|m)\s*\((?:直径|径)\)", _norm(value), re.I)
            if diameter:
                selected["diameter_cm"] = _to_cm(diameter.group(1), diameter.group(2))
                replacements[raw_key] = "diameter_cm"
                selected.pop("size", None)
                continue
            nums = re.findall(r"\d+(?:\.\d+)?", _norm(value))
            unit_matches = re.findall(r"mm|cm|m", _norm(value), re.I)
            if len(nums) >= 2 and len(unit_matches) <= 2:
                default_unit = unit_matches[-1] if unit_matches else "cm"
                first_unit = unit_matches[0] if len(unit_matches) == 2 else default_unit
                second_unit = unit_matches[1] if len(unit_matches) == 2 else default_unit
                selected["size_cm"] = [_to_cm(nums[0], first_unit), _to_cm(nums[1], second_unit)]
                replacements[raw_key] = "size_cm"
        elif key in {"product_variant", "fabric_variant", "material_variant", "bundle_option", "color"}:
            selected[key] = _norm(value)
    return selected, {"raw_attrs": original, "replaced_fields": replacements}


def _title_size_selector(product: dict[str, Any], side: str) -> dict[str, Any] | None:
    """Use one selected size immediately after a source title's product noun.

    The title is only a selector. Dimensions still need an explicit body
    crosswalk before they enter canonical identity attributes.
    """
    # Rakuten titles describe the whole SKU pool and commonly list multiple
    # named sizes. A first title token is not its selected SKU's fixed size.
    if side != "au":
        return None
    title = _norm(product.get("title_raw", ""))
    if not title:
        return None
    prefix = "(?:" + "|".join(re.escape(x) for x in sorted(_SIZED_PRODUCT_PREFIX, key=len, reverse=True)) + ")"
    token = "(?:" + "|".join(re.escape(x) for x in sorted(_TITLE_SIZE_TOKENS, key=len, reverse=True)) + ")"
    match = re.search(rf"({prefix})\s+({token})(?![A-Za-z])", title, re.I)
    if not match:
        return None
    return {"value": match.group(2), "quote": match.group(0),
            "source_ref": {"raw_file": (product.get("source") or {}).get("raw_file"),
                           "sha256": (product.get("source") or {}).get("sha256"),
                           "json_path": "$.itemInfo.itemTitle", "side": side}}


def _page_size_selector(product: dict[str, Any], side: str) -> dict[str, Any] | None:
    if side != "au":
        return None
    found = []
    for line, ref in _source_rows(product, side):
        match = re.fullmatch(r"こちらのページは\s*[【\[]?([^】\]]+?)[】\]]?\s*です[。.]?", _norm(line))
        if match:
            value = re.sub(r"サイズ$", "", match.group(1)).strip()
            if re.fullmatch(r"[A-Za-z]{1,3}|シングル|セミシングル|セミダブル|ダブル|クイーン|キング|ハーフ|クォーター", value, re.I):
                found.append({"value": value, "quote": line, "source_ref": ref})
    return found[0] if len({f["value"] for f in found}) == 1 else None


def _condition_aliases(text: str) -> set[str]:
    value = _compact(text)
    aliases = {value}
    # A source-local parenthetical relation such as S(シングル) is explicit.
    parens = re.findall(r"([^()]+)\(([^()]+)\)", _norm(text))
    for left, right in parens:
        aliases.add(_compact(left))
        aliases.add(_compact(right))
    for suffix in ("タイプ", "サイズ", "選択の場合", "選択時"):
        if value.endswith(_compact(suffix)):
            aliases.add(value[:-len(_compact(suffix))])
    return {x for x in aliases if x}


def _selection_matches(condition: str | None, selected: dict[str, Any]) -> bool | None:
    if condition is None:
        return True
    condition = _norm(condition).strip("【】[]")
    compact = _compact(condition)
    values = {_compact(v) for v in selected.values() if isinstance(v, (str, int, float))}
    aliases = _condition_aliases(condition)
    if values & aliases:
        return True
    set_match = re.fullmatch(r"(\d+)枚セット", condition)
    if set_match and "bundle_count" in selected:
        return float(set_match.group(1)) == float(selected["bundle_count"])
    tier_match = re.fullmatch(r"(\d+)段(?:タイプ)?", condition)
    if tier_match and "tier_count" in selected:
        return float(tier_match.group(1)) == float(selected["tier_count"])
    # Width/height/diameter option labels can select a measurement only when
    # both sides expose the same explicitly named role and numeric value.
    dim = re.fullmatch(r"(幅|横幅|高さ|奥行き?|直径|径)(\d+(?:\.\d+)?)(mm|cm|m)?", condition)
    if dim:
        role, num, unit = dim.groups()
        key = {"幅": "width_cm", "横幅": "width_cm", "高さ": "height_cm",
               "奥行": "depth_cm", "奥行き": "depth_cm", "直径": "diameter_cm", "径": "diameter_cm"}[role]
        if key in selected:
            target = _to_cm(num, unit or "cm")
            return selected[key] == target
        # A size selector may crosswalk to a role only through the source's
        # explicit condition label; a scalar size is not assumed to be height.
        if "size" in selected:
            raw_size = _compact(selected["size"])
            return raw_size == _compact(f"{num}{unit or 'cm'}")
    number = _number_token(condition)
    if number is not None:
        for key, value in selected.items():
            if key.endswith("_cm") and value == number:
                return True
    return None


def _parse_dims(line: str, section: str, role: str | None) -> list[dict[str, Any]]:
    text = _norm(line)
    found: dict[str, float] = {}
    patterns = [
        ("width_cm", r"(?:横幅|(?<!縦)幅|横)\s*({_NUM})\s*({_UNIT})?"),
        ("depth_cm", r"奥行(?:き)?\s*({_NUM})\s*({_UNIT})?"),
        ("length_cm", r"(?:長さ|丈|縦幅|縦)\s*({_NUM})\s*({_UNIT})?"),
        ("height_cm", r"高さ\s*({_NUM})\s*({_UNIT})?"),
        ("thickness_cm", r"(?:厚さ|厚み)\s*({_NUM})\s*({_UNIT})?"),
        ("diameter_cm", r"(?:直径|径)\s*({_NUM})\s*({_UNIT})?"),
    ]
    for key, pattern in patterns:
        match = re.search(pattern.format(_NUM=_NUM, _UNIT=_UNIT), text, re.I)
        if match:
            unit = match.group(2) or "cm"
            found[key] = _to_cm(match.group(1), unit)
    # A small number of source tables omit labels but explicitly place two
    # values in a size field. Preserve their order as width x length.
    if not found and section == "dimensions":
        dims = re.search(rf"({_NUM})\s*({_UNIT})?\s*[×x＊]\s*({_NUM})\s*({_UNIT})?", text, re.I)
        if dims:
            unit1, unit2 = dims.group(2) or "cm", dims.group(4) or dims.group(2) or "cm"
            vals = [_to_cm(dims.group(1), unit1), _to_cm(dims.group(3), unit2)]
            role_key = _ROLE_FIELDS.get(role or "")
            if role_key:
                return [{"field": role_key, "value": vals}]
            return [{"field": "size_cm", "value": vals}]
    results = []
    if all(k in found for k in ("width_cm", "depth_cm", "height_cm")):
        field = _ROLE_FIELDS.get(role or "", "page_dimensions_cm")
        vals = [found["width_cm"], found["depth_cm"], found["height_cm"]]
        results.append({"field": field, "value": vals})
    elif role in {"tabletop", "seat", "panel", "door", "mattress"}:
        pair = None
        if "width_cm" in found and "depth_cm" in found:
            pair = [found["width_cm"], found["depth_cm"]]
        elif "width_cm" in found and "length_cm" in found:
            pair = [found["width_cm"], found["length_cm"]]
        if pair:
            results.append({"field": _ROLE_FIELDS[role], "value": pair})
    else:
        if "width_cm" in found and "length_cm" in found:
            results.append({"field": "size_cm", "value": [found["width_cm"], found["length_cm"]]})
        elif "width_cm" in found and "depth_cm" in found and "thickness_cm" in found and section == "dimensions":
            results.append({"field": "size_cm", "value": [found["width_cm"], found["depth_cm"]]})
            results.append({"field": "thickness_cm", "value": found["thickness_cm"]})
        elif "diameter_cm" in found:
            results.append({"field": "diameter_cm", "value": found["diameter_cm"]})
        # Keep labelled scalar dimensions as their own roles.
        for key, val in found.items():
            if key not in {"width_cm", "length_cm", "depth_cm", "height_cm", "diameter_cm", "thickness_cm"}:
                continue
            if key == "thickness_cm":
                results.append({"field": key, "value": val})
            elif key in {"width_cm", "depth_cm", "height_cm", "length_cm"} and not any(r["field"] in {"size_cm", "page_dimensions_cm", *_ROLE_FIELDS.values()} for r in results):
                results.append({"field": key, "value": val})
    if len(found) and not results:
        for key, val in found.items():
            results.append({"field": key, "value": val})
    return results


def _parse_contents(line: str) -> tuple[str, int | float, str] | None:
    text = _norm(line)
    match = re.search(r"(.+?)\s*[×x＊*]\s*(\d+(?:\.\d+)?)\s*(枚|個|本|点|袋|セット)", text, re.I)
    if not match:
        match = re.search(r"(.+?)\s*(\d+(?:\.\d+)?)\s*(枚|個|本|点|袋|セット)\s*$", text, re.I)
    if not match:
        return None
    name = match.group(1).strip(" ・:：")
    # Keep semantic roles such as panel/door explicit while removing a source
    # selection's material/color prefix from a generic component name.
    for suffix in ("パネル", "ドアパーツ", "ジョイントパーツ", "滑り止め", "結束バンド", "木製ハンマー"):
        if name.endswith(suffix):
            name = suffix
            break
    return name, float(match.group(2)), match.group(3)


def _page_facts(product: dict[str, Any], side: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _source_rows(product, side)
    section: str | None = None
    role: str | None = None
    condition: str | None = None
    observations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    previous_field = None
    skip_search_keywords = False
    for line, ref in rows:
        current_field = ref.get("json_path")
        if current_field != previous_field:
            section, role, condition = None, None, None
            skip_search_keywords = False
            previous_field = current_field
        source_quote = line
        compact = re.sub(r"\s+", "", _norm(line)).strip("：:")
        if any(marker in compact for marker in ("検索キーワード", "検索ワード", "検索用キーワード")):
            skip_search_keywords = True
        if skip_search_keywords:
            continue
        if compact in {"商 品 詳 細", "商品詳細", "商品仕様", "仕様"}:
            section, role, condition = None, None, None
            continue
        if compact in _SECTION_HEADINGS:
            section, role, condition = _SECTION_HEADINGS[compact], None, None
            continue
        if section == "dimensions" and (compact in _TITLE_SIZE_TOKENS or compact in {"QT", "H", "XS"}):
            condition = compact
            continue
        if section is None and re.fullmatch(r"(こちらのページは)?(.+?)(です|となります)", compact):
            # Explicit fixed-page variant/dimension statements are usable even
            # when they precede a detail heading; broad prose and titles are not.
            if compact.startswith("こちらのページは"):
                condition = None
                observations.extend(_observations_from_line(line, section, role, condition, ref))
            continue
        bracket = re.fullmatch(r"【(.+?)】", compact)
        if bracket:
            token = bracket.group(1)
            if section in {"dimensions", "contents", "accessories", "variants", "materials", "features"}:
                if token in _ROLE_HEADINGS:
                    role = _ROLE_HEADINGS[token]
                else:
                    condition = token
                    set_count = re.fullmatch(r"(\d+)枚セット", token)
                    if set_count:
                        observations.append({"field": "bundle_count", "value": int(set_count.group(1)),
                                             "condition": token,
                                             "citation": {"quote": line, "source_ref": ref}})
            continue
        bracketed_row = re.match(r"^【([^】]+)】\s*(.+)$", _norm(line))
        if bracketed_row and section in {"dimensions", "contents", "accessories", "variants", "materials", "features"}:
            condition = bracketed_row.group(1)
            line = bracketed_row.group(2).strip()
        # Japanese product tables commonly write conditions inline, e.g.
        # “4段：（約）幅40×奥行84×高さ124cm”. This condition is source text.
        inline = re.match(r"^([^：:]{1,24})[：:](.+)$", _norm(line))
        parse_line = line
        if inline and (section in {"dimensions", "contents", "accessories", "variants", "materials", "features"}
                       or re.search(rf"(?:幅|長さ|奥行|高さ|直径|厚さ|\d+\s*{_UNIT})", inline.group(2), re.I)):
            prefix = inline.group(1).strip()
            if _looks_like_selector(prefix) or (section == "dimensions" and re.fullmatch(r"[A-Za-zァ-ヶー一-龥()]{1,24}", prefix)):
                if prefix in _ROLE_HEADINGS:
                    role = _ROLE_HEADINGS[prefix]
                else:
                    condition = prefix
                parse_line = inline.group(2).strip()
        citation = {"quote": source_quote, "source_ref": ref}
        for observation in _observations_from_line(parse_line, section, role, condition, ref):
            observation["citation"] = citation
            observations.append(observation)
        component = _parse_contents(parse_line) if section in {"contents", "accessories"} else None
        if component:
            name, count, unit = component
            observations.append({"field": "bundle_components", "value": {"name": name, "count": count, "unit": unit},
                                 "condition": condition, "citation": citation, "complete_scope": section == "contents"})
        # A same-page scope statement explicitly binds a size and quantity.
        page_stmt = re.search(r"こちらのページは\s*(\d+(?:\.\d+)?)\s*[×x＊]\s*(\d+(?:\.\d+)?)\s*cm\s*[:：]?\s*(\d+)\s*枚(?:セット|set)", _norm(line), re.I)
        if page_stmt:
            observations.append({"field": "panel_size_cm", "value": [float(page_stmt.group(1)), float(page_stmt.group(2))],
                                 "condition": None, "citation": citation})
            observations.append({"field": "bundle_count", "value": int(page_stmt.group(3)),
                                 "condition": None, "citation": citation})
        if section == "features":
            tier = re.fullmatch(r"[・\s]*(\d+)段(?:タイプ)?", _norm(parse_line))
            if tier:
                observations.append({"field": "tier_count", "value": int(tier.group(1)),
                                     "condition": condition, "citation": citation})
    return observations, conflicts


def _looks_like_selector(text: str) -> bool:
    value = _norm(text)
    if value in _TITLE_SIZE_TOKENS or value in {"QT", "H", "XS"}:
        return True
    return bool(re.fullmatch(r"(?:\d+(?:\.\d+)?\s*(?:段|cm|mm|枚セット|個セット)|[A-Za-z]{1,3}(?:\([^)]*\))?|[^:]{1,16}(?:タイプ|サイズ|生地|セット))", value, re.I))


def _observations_from_line(line: str, section: str | None, role: str | None,
                            condition: str | None, ref: dict[str, Any]) -> list[dict[str, Any]]:
    text = _norm(line)
    citation = {"quote": line, "source_ref": ref}
    out: list[dict[str, Any]] = []
    if section == "variants" and re.fullmatch(r"[^/、,：:]{1,32}タイプ", text):
        out.append({"field": "product_variant", "value": text, "condition": condition, "citation": citation})
    if section == "contents":
        # A construction quantity (two layers) is distinct from the one sold
        # item. The source explicitly names the sold product before this phrase.
        named = re.fullmatch(r"([^\d×x]+?)\s*\d+枚合わせ\s*(?:[A-Za-z]{1,3}|シングル|セミシングル|セミダブル|ダブル|クイーン|キング|ハーフ|クォーター)?\s*[×x]\s*1", text)
        if named:
            out.append({"field": "product_variant", "value": named.group(1).strip(),
                        "condition": condition, "citation": citation})
            layers = re.search(r"(\d+)枚合わせ", text)
            out.append({"field": "construction_layers", "value": int(layers.group(1)),
                        "condition": condition, "citation": citation})
    if role is None:
        for token, value in (("折りたたみ", "folded"), ("折り畳み", "folded"), ("収納", "folded"),
                             ("天板", "tabletop"), ("座面", "seat"), ("バスケット", "basket"),
                             ("キャリー", "carrier"), ("パネル", "panel"), ("ドアパーツ", "door"),
                             ("本体", "body"), ("マットレス", "mattress"), ("敷きパッド", "mattress")):
            if token in text:
                role = value
                break
    if section == "dimensions" or text.startswith("こちらのページは"):
        for item in _parse_dims(text, section or "", role):
            out.append({**item, "condition": condition, "citation": citation})
    # Explicit named size-to-measure relation, e.g. “140×205cm（ダブルサイズ）”.
    if section == "dimensions":
        match = re.search(rf"({_NUM})\s*cm\s*[×x＊]\s*({_NUM})\s*cm\s*[（(]([^()（）]+サイズ)[）)]", text, re.I)
        if match:
            out.append({"field": "size_cm", "value": [float(match.group(1)), float(match.group(2))],
                        "condition": match.group(3), "citation": citation})
    if section == "dimensions" and role is None:
        # Named-size alias rows like S(シングル): ... are usable only because
        # both aliases occur in the same source row.
        alias = re.match(r"^([^：:]+)[：:](.+)$", text)
        if alias and _looks_like_selector(alias.group(1)):
            for item in _parse_dims(alias.group(2), section, role):
                item["condition"] = alias.group(1)
                item["citation"] = citation
                out.append(item)
    # Explicit source options for integer product configurations.
    tier = re.fullmatch(r"\s*(\d+)段(?:タイプ)?\s*", text)
    if tier and section in {"variants", "features", "dimensions"}:
        out.append({"field": "tier_count", "value": int(tier.group(1)), "condition": condition, "citation": citation})
    return out


def _dimension_signature(value: Any) -> tuple[float, ...] | None:
    if isinstance(value, (list, tuple)) and all(isinstance(x, (int, float)) for x in value):
        return tuple(float(x) for x in value)
    return None


def _selected_matches_observation(field: str, value: Any, selected: dict[str, Any], obs: dict[str, Any]) -> bool:
    condition = obs.get("condition")
    match = _selection_matches(condition, selected)
    if match is True:
        return True
    if match is False:
        return False
    if field == "size_cm" and ("size" in selected or "title_size" in selected):
        # Match numeric sizes only when the full ordered pair is the same.
        raw_size = selected.get("size", selected.get("title_size", ""))
        nums = re.findall(r"\d+(?:\.\d+)?", _norm(raw_size))
        signature = _dimension_signature(value)
        if len(nums) >= 2 and signature and tuple(float(x) for x in nums[:2]) == signature[:2]:
            return True
        if condition and _condition_aliases(condition) & _condition_aliases(str(raw_size)):
            return True
        return False
    if field == "diameter_cm" and "size" in selected:
        number = _number_token(selected["size"])
        if number is not None and number == value:
            return True
    return condition is None


def _merge_observations(entity: dict[str, Any], observations: list[dict[str, Any]],
                        selected: dict[str, Any], identity: dict[str, Any]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        grouped.setdefault(obs["field"], []).append(obs)
    attrs = identity["attrs"]
    evidence = identity["evidence"]
    raw_attrs = identity["raw_attrs"]
    # If the title's one type-adjacent size and the selected SKU size disagree,
    # require an explicit source-local alias relation before resolving either.
    selected_size = selected.get("size")
    title_size = selected.get("title_size")
    if selected_size is not None and title_size is not None and _compact(selected_size) != _compact(title_size):
        aligned = any(obs.get("field") == "size_cm" and obs.get("condition")
                      and _condition_aliases(obs["condition"]) >= {_compact(selected_size), _compact(title_size)}
                      for obs in observations)
        if not aligned:
            identity["source_conflicts"].append({"field": "size_selection", "values": [selected_size, title_size],
                                                   "evidence": [identity["title_selector"]] if identity.get("title_selector") else []})
            identity["needs_review"] = True
    resolved_raw_keys: dict[str, str] = {}
    for key in raw_attrs:
        semantic = _selector_key(key)
        if semantic and semantic != key:
            resolved_raw_keys[key] = semantic
    for field, rows in grouped.items():
        eligible = []
        for row in rows:
            status = _selection_matches(row.get("condition"), selected)
            if field == "size_cm" and ("size" in selected or "title_size" in selected):
                if _selected_matches_observation(field, row.get("value"), selected, row):
                    eligible.append(row)
                continue
            if status is True:
                eligible.append(row)
            elif status is None and _selected_matches_observation(field, row.get("value"), selected, row):
                eligible.append(row)
            elif row.get("condition") is None:
                eligible.append(row)
        if not eligible:
            continue
        # A selected scalar measurement can resolve an otherwise unscoped
        # alternatives table when its exact value occurs. A single conflicting
        # statement is still a conflict, never overridden by this rule.
        if field in selected and field.endswith("_cm"):
            unscoped = [r for r in eligible if r.get("condition") is None]
            values = {repr(r["value"]) for r in unscoped}
            if len(values) > 1 and any(r["value"] == selected[field] for r in unscoped):
                eligible = [r for r in eligible if r.get("condition") is not None or r["value"] == selected[field]]
        # Component lists are an ordered set of explicitly listed pieces.
        if field == "bundle_components":
            complete = [r for r in eligible if r.get("complete_scope")]
            rows_for_set = complete or eligible
            comps = sorted({(r["value"]["name"], r["value"]["count"], r["value"]["unit"]) for r in rows_for_set})
            value = [{"name": n, "count": c, "unit": u} for n, c, u in comps]
            selected_variant = any(r.get("condition") for r in rows_for_set)
            # If the source contains multiple bundle branches but selection did
            # not choose one, keep it unresolved rather than mixing all pieces.
            all_components = grouped[field]
            conditions = {r.get("condition") for r in all_components if r.get("condition")}
            if len(conditions) > 1 and not selected_variant:
                identity["unknown_fields"].append(field)
                identity["needs_review"] = True
                continue
        else:
            by_value: dict[str, list[dict[str, Any]]] = {}
            for row in eligible:
                token = repr(row["value"])
                by_value.setdefault(token, []).append(row)
            if len(by_value) > 1:
                distinct_conditions = {r.get("condition") for r in rows if r.get("condition")}
                if (field in {"size_cm", "thickness_cm"} and identity.get("side") == "rakuten"
                        and identity.get("named_selection") and len(distinct_conditions) > 1
                        and all(r.get("condition") is None for r in eligible)):
                    evidence.setdefault("unselected_background_alternatives", []).extend(r["citation"] for r in eligible)
                    continue
                identity["source_conflicts"].append({"field": field, "values": [r["value"] for r in eligible],
                                                       "evidence": [r["citation"] for r in eligible]})
                identity["needs_review"] = True
                continue
            value = eligible[0]["value"]
        existing = attrs.get(field)
        if existing is not None and existing != value:
            identity["source_conflicts"].append({"field": field, "values": [existing, value],
                                                   "evidence": [*evidence.get(field, []), *(r["citation"] for r in eligible)]})
            identity["needs_review"] = True
            continue
        attrs[field] = value
        evidence.setdefault(field, []).extend(r["citation"] for r in eligible)
        # Exact source binding may replace an opaque selector with a canonical
        # fact while preserving the raw selected value in raw_attrs.
        if field == "size_cm" and ("size" in selected or "title_size" in selected):
            attrs.pop("size", None)
            if "size" in raw_attrs:
                resolved_raw_keys["size"] = "named_size" if identity.get("named_selection") else field
            if "title_size" in selected:
                evidence.setdefault("size_title_selector", []).append(identity["title_selector"])
        elif field in {"tier_count", "width_cm", "height_cm", "depth_cm", "length_cm", "diameter_cm", "thickness_cm", "bundle_count"}:
            for raw_key in list(attrs):
                if _selector_key(raw_key) == field and raw_key != field:
                    attrs.pop(raw_key, None)
                    resolved_raw_keys[raw_key] = field
    identity["replaced_fields"].update(resolved_raw_keys)
    identity["unknown_fields"] = sorted(set(identity["unknown_fields"]) - set(resolved_raw_keys))


def _identity(entity: dict[str, Any], product: dict[str, Any], side: str,
              source_size_aliases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    selected, metadata = _canonical_selection(entity)
    title_selector = _page_size_selector(product, side) or _title_size_selector(product, side)
    if title_selector:
        selected["title_size"] = title_selector["value"]
    used_aliases = []
    size_values = {_compact(selected[k]) for k in ("size", "title_size") if k in selected}
    for relation in source_size_aliases or []:
        if size_values & set(relation["aliases"]):
            for alias in relation["aliases"]:
                selected[f"source_size_alias_{len(selected)}"] = alias
            used_aliases.append(relation["citation"])
    attrs = dict(entity.get("attrs") or {})
    evidence = deepcopy(entity.get("evidence") or {})
    unknown = list(entity.get("unknown_fields") or [])
    conflicts = deepcopy(entity.get("source_conflicts") or [])
    identity = {"attrs": attrs, "raw_attrs": metadata["raw_attrs"],
                "evidence": evidence, "unknown_fields": unknown,
                "source_conflicts": conflicts, "needs_review": bool(conflicts),
                "replaced_fields": metadata["replaced_fields"],
                "title_selector": ({"field": "size", "value": title_selector["value"],
                                    "quote": title_selector["quote"],
                                    "source_ref": title_selector["source_ref"]}
                                   if title_selector else None)}
    if used_aliases:
        evidence["source_size_aliases"] = used_aliases
    identity["side"] = side
    raw_named = selected.get("size")
    named_selection = bool(raw_named in _TITLE_SIZE_TOKENS)
    identity["named_selection"] = named_selection
    dimension_role = re.match(r"([^\d()]+)\(\d+(?:\.\d+)?[×x]", str(metadata["raw_attrs"].get("size", "")))
    identity["additional_required_fields"] = []
    if dimension_role and dimension_role.group(1).strip() not in _TITLE_SIZE_TOKENS:
        attrs["dimension_role"] = dimension_role.group(1).strip()
        identity["additional_required_fields"].append("dimension_role")
        evidence["dimension_role"] = deepcopy(evidence.get("size", []))
    nominal = raw_named if named_selection else (title_selector["value"] if title_selector else None)
    if nominal is not None:
        canonical = _norm(nominal)
        for relation in source_size_aliases or []:
            if _compact(canonical) in relation["aliases"]:
                japanese = [a for a in relation["aliases"] if re.fullmatch(r"[ァ-ヶー一-龥]+", a)]
                if len(japanese) == 1:
                    canonical = japanese[0]
        attrs["named_size"] = canonical
        evidence["named_size"] = ([{"quote": f"サイズ={raw_named}", "source_ref": (evidence.get("size") or [{}])[0].get("source_ref")}]
                                  if named_selection else [identity["title_selector"]])
        if named_selection:
            attrs.pop("size", None)
            identity["replaced_fields"]["size"] = "named_size"
    for raw_key, canonical in metadata["replaced_fields"].items():
        if canonical == "height_cm" and _selector_key(raw_key) == "height_cm":
            # A bare height selector can mean platform, tabletop, or seat
            # height. Keep it unresolved unless a scoped source fact binds it.
            continue
        if canonical in selected and raw_key in attrs:
            attrs[canonical] = selected[canonical]
            evidence.setdefault(canonical, []).extend(evidence.get(raw_key, []))
            attrs.pop(raw_key, None)
    if "size_cm" in selected and "size" in attrs:
        attrs["size_cm"] = selected["size_cm"]
        evidence.setdefault("size_cm", []).extend(evidence.get("size", []))
        attrs.pop("size", None)
        identity["replaced_fields"]["size"] = "size_cm"
    observations, parse_conflicts = _page_facts(product, side)
    identity["source_conflicts"].extend(parse_conflicts)
    _merge_observations(entity, observations, selected, identity)
    # Expose narrowly scoped resolutions of broad dimension conflicts for the
    # caller to merge. Keep the original conflict record for auditability.
    role_fields = {"body_dimensions_cm", "folded_dimensions_cm", "tabletop_size_cm",
                   "seat_size_cm", "basket_dimensions_cm", "carrier_dimensions_cm",
                   "panel_size_cm", "door_size_cm", "size_cm"}
    identity["resolved_conflicts"] = []
    for conflict in conflicts:
        if conflict.get("field") != "page_dimensions_cm":
            continue
        values = conflict.get("values") or []
        signatures = {_dimension_signature(v) for v in values}
        signatures.discard(None)
        matches = {}
        for signature in signatures:
            role_matches = []
            for field in role_fields:
                citations = identity["evidence"].get(field, [])
                value = identity["attrs"].get(field)
                if (_dimension_signature(value) == signature and citations
                        and not any(c.get("field") == field for c in identity["source_conflicts"])):
                    role_matches.append((field, citations, value))
            if len(role_matches) != 1:
                matches = {}
                break
            matches[signature] = role_matches[0]
        if signatures and len(matches) == len(signatures):
            resolved_as = sorted({match[0] for match in matches.values()})
            identity["resolved_conflicts"].append({"field": "page_dimensions_cm",
                "resolved_as": resolved_as, "values": [list(signature) for signature in sorted(signatures)],
                "evidence": [citation for match in matches.values() for citation in match[1]]})
    # Preserve any selector whose role could not be resolved from its label or
    # from a unique source binding. Do not silently drop unknown axes.
    for raw_key, value in metadata["raw_attrs"].items():
        if raw_key.startswith("axis:") and raw_key not in identity["replaced_fields"]:
            if raw_key not in identity["unknown_fields"]:
                identity["unknown_fields"].append(raw_key)
    if ("size" in selected or "title_size" in selected) and "size_cm" not in identity["attrs"]:
        identity["unknown_fields"].append("size_cm")
    identity["unknown_fields"] = sorted(set(identity["unknown_fields"]))
    identity["needs_review"] = bool(identity["needs_review"] or identity["unknown_fields"])
    return identity


def enrich_task(task: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied task with source-resolved selected identity attrs.

    The original task fields and raw SKU values are retained. Each entity gets
    a ``resolved_identity`` object whose attributes contain only explicit
    selected axes plus uniquely resolved page facts. Conflicts and unresolved
    fields remain review signals.
    """
    result = deepcopy(task)
    rak_product = dossier.get("rakuten_product") or {}
    au_product = dossier.get("au_product") or {}
    # Product pairs are established upstream. Reuse only explicit alias
    # relations in either matched page, e.g. D(ダブル), never a guessed glossary.
    size_aliases = []
    for side, product in (("rakuten", rak_product), ("au", au_product)):
        for fact in _page_facts(product, side)[0]:
            condition = fact.get("condition")
            if fact["field"] == "size_cm" and condition and re.fullmatch(r"[A-Za-z]{1,3}\([^()]+\)", _norm(condition)):
                size_aliases.append({"aliases": sorted(_condition_aliases(condition)), "citation": fact["citation"]})
    query = result.get("rakuten")
    if isinstance(query, dict):
        query["resolved_identity"] = _identity(query, rak_product, "rakuten", size_aliases)
    for candidate in result.get("au_candidates", []):
        if isinstance(candidate, dict):
            candidate["resolved_identity"] = _identity(candidate, au_product, "au", size_aliases)
    return result
