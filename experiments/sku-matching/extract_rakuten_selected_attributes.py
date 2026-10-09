"""Read selected Rakuten SKU attributes from captured embedded app data."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
import re
from pathlib import Path
from typing import Any


_UNIT = re.compile(r"^(mm|cm|m|kg|g|個|枚|段|層)$", re.I)
_PAIR = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>mm|cm|m)?\s*[×xX＊*]\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>mm|cm|m)", re.I)
_ROLE_PAIR = re.compile(r"幅\s*(?P<w>\d+(?:\.\d+)?)\s*(?P<uw>mm|cm|m)?\s*[×xX＊*]\s*(?:丈|長さ)\s*(?P<l>\d+(?:\.\d+)?)\s*(?P<ul>mm|cm|m)", re.I)
_DIMENSION_TITLE = {
    "本体横幅": "width_cm", "本体幅": "width_cm", "横幅": "width_cm", "幅": "width_cm",
    "本体奥行": "depth_cm", "奥行": "depth_cm", "奥行き": "depth_cm",
    "本体高さ": "height_cm", "高さ": "height_cm", "本体長さ": "length_cm", "長さ": "length_cm",
    "本体厚さ": "thickness_cm", "厚さ": "thickness_cm", "厚み": "thickness_cm",
}
_FIELD_TITLE = {
    "代表カラー": "color", "カラー": "color", "色": "color",
    "生地": "fabric", "素材": "material", "材質": "material",
    "段数": "tier_count", "セット数": "bundle_count", "枚数": "piece_count",
    "数量": "selected_quantity",
}
_COMMERCIAL = re.compile(r"価格|金額|送料|在庫|配送|発送|納期|クーポン|ポイント", re.I)


def _cm(value: str, unit: str) -> int | float:
    n = float(value)
    unit = unit.lower()
    n = n / 10 if unit == "mm" else n * 100 if unit == "m" else n
    return int(n) if n.is_integer() else n


@lru_cache(maxsize=64)
def _read_raw_item(raw_file: str, expected_sha: str | None, encoding: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not raw_file:
        return None, {"status": "missing_source_file"}
    path = Path(raw_file)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        raw = path.read_bytes()
    except OSError:
        return None, {"status": "source_file_unavailable", "raw_file": raw_file}
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and actual_sha != expected_sha:
        return None, {"status": "source_hash_mismatch", "raw_file": raw_file,
                      "expected_sha256": expected_sha, "actual_sha256": actual_sha}
    try:
        html = raw.decode(encoding, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    marker = '<script type="application/json" id="item-page-app-data">'
    pos = html.find(marker)
    start = html.find("{", pos + len(marker)) if pos >= 0 else -1
    end = html.find("</script>", start) if start >= 0 else -1
    if start < 0 or end < 0:
        return None, {"status": "embedded_app_data_missing", "raw_file": raw_file,
                      "sha256": actual_sha}
    try:
        data = json.loads(html[start:end].strip())
    except json.JSONDecodeError:
        return None, {"status": "embedded_app_data_invalid", "raw_file": raw_file,
                      "sha256": actual_sha}
    sku_data = (((data.get("api") or {}).get("data") or {}).get("itemInfoSku") or {})
    return sku_data, {"status": "ok", "raw_file": raw_file, "sha256": actual_sha,
                      "json_path": "api.data.itemInfoSku.sku"}


def _raw_item(dossier: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    product = dossier.get("rakuten_product") or {}
    desc = product.get("description") or {}
    src = desc.get("source") or product.get("source") or {}
    return _read_raw_item(str(src.get("raw_file") or ""), src.get("sha256"),
                          str(src.get("encoding") or "utf-8"))


def _normalize_attribute(title: str, value: Any, unit: Any) -> tuple[str | None, Any, dict[str, Any] | None]:
    title = str(title or "").strip()
    raw_value = str(value if value is not None else "").strip()
    unit_text = str(unit or "").strip()
    field = _FIELD_TITLE.get(title)
    dimension_field = _DIMENSION_TITLE.get(title)
    pair = _PAIR.search(raw_value)
    if pair:
        unit_a = pair.group("ua") or pair.group("ub")
        pair_fact = {"size_pair_cm": [_cm(pair.group("a"), unit_a),
                                      _cm(pair.group("b"), pair.group("ub"))],
                     "size_pair_role": ["first_size_axis", "second_size_axis"]}
    else:
        pair_fact = None
    if dimension_field and unit_text.lower() in {"cm", "mm", "m"}:
        try:
            return dimension_field, _cm(raw_value, unit_text), None
        except ValueError:
            pass
    if title in {"サイズ", "寸法", "商品サイズ"}:
        role_pair = _ROLE_PAIR.search(raw_value)
        if role_pair:
            uw = role_pair.group("uw") or role_pair.group("ul")
            return "size", raw_value, {"width_cm": _cm(role_pair.group("w"), uw),
                                        "length_cm": _cm(role_pair.group("l"), role_pair.group("ul"))}
        return "size", raw_value, pair_fact
    if field:
        return field, raw_value, pair_fact
    if _UNIT.fullmatch(unit_text) and raw_value:
        return None, {"raw_value": raw_value, "raw_unit": unit_text}, pair_fact
    return None, raw_value or None, pair_fact


def extract_selected_attributes(case: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    """Extract SKU row's embedded attributes, preserving unknowns and citations.

    The case's source_row_index selects the embedded SKU. Axis values are
    verified against that row before its attribute list is emitted. No title,
    label, price, or availability field is used.
    """
    rk = case.get("rakuten") or {}
    row_ref = rk.get("source") or {}
    raw_sku_data, source_ref = _raw_item(dossier)
    empty = {"case_id": case.get("case_id"), "status": source_ref["status"],
             "selected_attributes": [], "page_dimensions": {}}
    if raw_sku_data is None:
        return {**empty, "source_ref": source_ref}
    index = row_ref.get("source_row_index")
    skus = raw_sku_data.get("sku") or []
    if type(index) is not int or index < 0 or index >= len(skus):
        return {**empty, "status": "source_row_index_out_of_range",
                "source_ref": {**source_ref, "source_row_index": index,
                               "sku_row_count": len(skus)}}
    sku = skus[index]
    selectors = raw_sku_data.get("variantSelectors") or []
    expected = []
    for j, selected in enumerate(rk.get("option_values") or []):
        expected.append((selected.get("axis_key") or selected.get("axis_name"), selected.get("value")))
    actual = []
    for j, value in enumerate(sku.get("selectorValues") or []):
        axis = selectors[j] if j < len(selectors) else {}
        actual.append((axis.get("key") or axis.get("name"), value))
    if expected != actual:
        return {**empty, "status": "selected_axis_mismatch",
                "source_ref": {**source_ref, "source_row_index": index,
                               "case_axes": expected, "embedded_axes": actual}}

    attrs = []
    page_dims: dict[str, int | float] = {}
    raw_attrs = sku.get("attributes") or []
    for attr_index, attr in enumerate(raw_attrs):
        title, value, unit = attr.get("title"), attr.get("value"), attr.get("unit")
        if _COMMERCIAL.search(str(title or "")):
            continue
        field, normalized, extra = _normalize_attribute(str(title or ""), value, unit)
        ref = {"raw_file": source_ref.get("raw_file"), "sha256": source_ref.get("sha256"),
               "json_path": f"api.data.itemInfoSku.sku[{index}].attributes[{attr_index}]",
               "source_row_index": index, "quote": f"{title}: {value}" + (f" {unit}" if unit else "")}
        attrs.append({"field": field, "raw_title": title, "raw_value": value,
                      "raw_unit": unit, "value": normalized,
                      "status": "known" if field else "unknown_unparsed_attribute",
                      "source_ref": ref, **({"parsed_dimensions": extra} if extra else {})})
        if field in {"width_cm", "depth_cm", "height_cm", "length_cm", "thickness_cm"}:
            page_dims[field] = normalized
    return {"case_id": case.get("case_id"), "status": "ok",
            "selected_attributes": attrs,
            "page_dimensions": page_dims,
            "source_ref": {**source_ref, "source_row_index": index,
                           "sku_id": sku.get("merchantDefinedSkuId") or sku.get("variantId")}}
