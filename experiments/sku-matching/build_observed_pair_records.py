#!/usr/bin/env python3
"""Build evidence-backed AU product ↔ Rakuten product/SKU records offline.

This exports observations and review candidates. It does not make a general
product identity prediction, and it never edits source collection files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from urllib.parse import urlparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AU = Path(".lab-output/sku-real-au-20261010")
DEFAULT_RAKUTEN = Path(".lab-output/sku-real-rakuten-20261010-normalized-v2")
DEFAULT_OUT = Path(".lab-output/sku-observed-product-pairs-20261010-v2")
INPUT_SNAPSHOT_HASHES: dict[str, str] = {}
SHA256_CACHE: dict[str, str] = {}
AU_RAW_ITEM_CACHE: dict[str, dict[str, Any]] = {}
AU_RAW_TEXT_CACHE: dict[str, str] = {}
AU_ASSET_TOKEN_CACHE: dict[str, frozenset[str]] = {}

# Supplied source-mapping references. These are provenance hints, not an
# independent gold set. The current new AU collection contains the first two.
KNOWN_SOURCE_MAPS: dict[str, dict[str, Any]] = {
    "704502086": {
        "manage_number": "ct0",
        "basis": "known source mapping; AU item description references ct0 rank asset and explicitly lists drape+lace contents",
        "au_evidence_path": "$.itemInfo.extraItemComment",
        "raku_evidence_path": "products.jsonl[manage_number=ct0]",
        "expected_lace": "あり",
    },
    "704500131": {
        "manage_number": "ct0",
        "basis": "known source mapping; AU item description cross-links the 4-piece ct0 item and explicitly lists drape-only contents",
        "au_evidence_path": "$.itemInfo.extraItemComment",
        "raku_evidence_path": "products.jsonl[manage_number=ct0]",
        "expected_lace": "なし",
    },
    # Curated after checking AU item-description image/model paths against the
    # Rakuten managed item plus item type and listed specifications.
    "662585498": {"manage_number": "faa006", "basis": "AU itemimg/FAA006 product images + steel rack/80cm/2Way/5-shelf specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FAA006/faa006-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=faa006]"},
    "469850986": {"manage_number": "faa006", "basis": "AU itemimg/FAA006 product images + steel rack/80cm/5-shelf specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FAA006/faa006-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=faa006]"},
    "337595272": {"manage_number": "feb002", "basis": "AU FEB002 model images + folding high-rebound mattress/190N/10cm/single specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (TOP/feb002-4.jpg; itemimg/FEB/feb002_*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=feb002]"},
    "700957229": {"manage_number": "feb002", "basis": "AU FEB002 model image + folding high-rebound mattress/190N/10cm/king specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEB/feb002008-z2-1.jpg)", "raku_evidence_path": "products.jsonl[manage_number=feb002]"},
    "704282431": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size/color specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "704280046": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size/color specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "704285114": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size/color specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "763158008": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002qt-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "704284693": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size/color specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002sd*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "765496131": {"manage_number": "fek002", "basis": "AU FEK002 product-specific blanket images + blanket type/size specs agree with Rakuten product family", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEK/fek002-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fek002]"},
    "509691375": {"manage_number": "fgc005", "basis": "AU FGC005 product-specific reclining chair images + 4Way/reclining/24cm chair specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FGC/005/fgc005-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fgc005]"},
    "688640726": {"manage_number": "fgc010", "basis": "AU FGC010 product-specific rotating reclining-chair images + rotating chair specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FGC/fgc010-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fgc010]"},
    "597651839": {"manage_number": "stp01", "basis": "AU STP01 product-specific step-ladder images + folding step-ladder specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/STP/stp01_*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=stp01]"},
    "666720279": {"manage_number": "faa005", "basis": "AU FAA005 product-specific metal-rack images + width50cm/3-tier steel rack specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/faa005_*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=faa005]"},
    "309329265": {"manage_number": "a19e", "basis": "AU A19E product-specific car-wash step-platform images + aluminum folding platform specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/A19/a19e_*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=a19e]"},
    "653618158": {"manage_number": "pc013", "basis": "AU PC013 product-specific pet fence images + 50x50cm/13-panel fence specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/PC/pc013_*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=pc013]"},
    "483077897": {"manage_number": "fef008", "basis": "AU FEF008 product-specific heated bed-pad images + double bed-pad specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEF/fef008-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fef008]"},
    "753670518": {"manage_number": "fel001", "basis": "AU FEL001 product-specific duvet-cover images + single blanket/cover size specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEL/fel001-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fel001]"},
    "703280256": {"manage_number": "fel001", "basis": "AU FEL001 product-specific duvet-cover images + double blanket/cover size specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEL/fel001-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fel001]"},
    "703279419": {"manage_number": "fel001", "basis": "AU FEL001 product-specific duvet-cover images + semi-double blanket/cover size specs agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/FEL/fel001-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fel001]"},
    "524251610": {"manage_number": "gad100", "basis": "AU description includes GAD100 product asset + toy storage/4-tier/gray specs agree with Rakuten product variant", "au_evidence_path": "$.itemInfo.extraItemComment (TOP/gad100.jpg)", "raku_evidence_path": "products.jsonl[manage_number=gad100]"},
    "695392874": {"manage_number": "fia6080", "basis": "AU Fia60 product image + round dining table/60cm specs agree with Rakuten 60cm SKU", "au_evidence_path": "$.itemInfo.extraItemComment (TOP/fia60*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fia6080]"},
    "695394169": {"manage_number": "fia6080", "basis": "AU Fia80 product image + round dining table/80cm specs agree with Rakuten 80cm SKU", "au_evidence_path": "$.itemInfo.extraItemComment (TOP/fia80*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=fia6080]"},
    "537246358": {"manage_number": "pt0033", "basis": "AU PT0033 product-specific pet-cart images + 3Way/7-color attributes agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/PT0033/pt0033-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=pt0033]"},
    "772334211": {"manage_number": "pt0038", "basis": "AU PT0038 product-specific premium pet-cart images + 2026 premium/3Way attributes agree with Rakuten product", "au_evidence_path": "$.itemInfo.extraItemComment (itemimg/PT/pt0038-*.jpg)", "raku_evidence_path": "products.jsonl[manage_number=pt0038]"},
    # Supplied known source mappings for the two later curtain IDs.
    "778818828": {"manage_number": "ctk", "basis": "known source mapping supplied with original AU/Rakuten collection evidence", "au_evidence_path": "source API/item description and options", "raku_evidence_path": "products.jsonl[manage_number=ctk]", "expected_lace": "なし"},
    "778809347": {"manage_number": "ctk", "basis": "known source mapping supplied with original AU/Rakuten collection evidence", "au_evidence_path": "source API/item description and options", "raku_evidence_path": "products.jsonl[manage_number=ctk]", "expected_lace": "あり"},
}

REVIEW_TERMS: dict[str, tuple[str, ...]] = {
    "ctk": ("カーテン",), "faa006": ("スチールラック",), "feb002": ("マットレス",),
    "faa005": ("スチールラック",), "fek002": ("毛布",), "fef004": ("毛布",),
    "fef008": ("敷きパッド",), "fel001": ("布団カバー", "毛布"),
    "fgc005": ("座椅子",), "fgc010": ("座椅子",), "jm60b": ("ジョイントマット",),
    "stp01": ("脚立", "踏み台"), "a19e": ("洗車台", "脚立"), "fia6080": ("丸テーブル", "テーブル"),
    "pc013": ("ペットフェンス",),
    "pt0033": ("ペットカート",), "pt0038": ("ペットカート",),
    "pt0017a": ("ペットケージ", "犬 ケージ"), "rsb130": ("ソファーベッド",),
}
PRODUCT_TYPE_TERMS = tuple(sorted({
    term for terms in REVIEW_TERMS.values() for term in terms
} | {
    "マットレス", "毛布", "座椅子", "カーテン", "ジョイントマット", "踏み台",
    "脚立", "スチールラック", "丸テーブル", "ペットカート", "ペットフェンス",
    "敷きパッド", "布団カバー", "ソファーベッド", "ペットケージ", "ベッド",
    "テーブル", "収納ラック", "収納", "カーペット", "ラグ", "クッション",
}, key=lambda x: (-len(x), x)))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_cached(path: Path) -> str:
    key = str(path)
    if key not in SHA256_CACHE:
        SHA256_CACHE[key] = sha256(path)
    return SHA256_CACHE[key]


def cached_au_raw_item(path: Path) -> dict[str, Any]:
    key = str(path)
    if key not in AU_RAW_ITEM_CACHE:
        AU_RAW_ITEM_CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    return AU_RAW_ITEM_CACHE[key]


def cached_au_raw_text(path: Path) -> str:
    key = str(path)
    if key not in AU_RAW_TEXT_CACHE:
        AU_RAW_TEXT_CACHE[key] = path.read_text(encoding="utf-8")
    return AU_RAW_TEXT_CACHE[key]


def cached_au_asset_tokens(path: Path) -> frozenset[str]:
    key = str(path)
    if key not in AU_ASSET_TOKEN_CACHE:
        text = cached_au_raw_text(path)
        paths = re.findall(r"(?:TOP|itemimg)/[^\"'<>\s]+", text, flags=re.I)
        tokens: set[str] = set()
        for asset_path in paths:
            for segment in asset_path.casefold().split("/"):
                tokens.update(x for x in re.split(r"[^a-z0-9]+", segment) if x)
        AU_ASSET_TOKEN_CACHE[key] = frozenset(tokens)
    return AU_ASSET_TOKEN_CACHE[key]


def au_raw_item_path(product: dict[str, Any], fallback_root: Path) -> Path:
    item_id = str(product.get("item_id"))
    root = Path(product.get("_source_dir") or fallback_root)
    candidates = (root / f"{item_id}-item.json", root / "raw" / f"{item_id}-item.json")
    return next((p for p in candidates if p.is_file()), candidates[0])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    payload = path.read_bytes()
    INPUT_SNAPSHOT_HASHES[str(path)] = hashlib.sha256(payload).hexdigest()
    for n, line in enumerate(payload.decode("utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            row["_source_line"] = n
            row["_source_file"] = str(path)
            row["_source_dir"] = str(path.parent)
            out.append(row)
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def product_id(p: dict[str, Any], side: str) -> str:
    return str(p.get("item_id") if side == "au" else p.get("manage_number"))


def option_map(raw: Any) -> dict[str, Any]:
    """Read axis values for comparisons while leaving source values untouched."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        result: dict[str, Any] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = entry.get("axis_key") or entry.get("axis_name")
            if key is not None:
                result[str(key)] = entry.get("value")
        return result
    return {}


def axis_values_match(axis_key: str, left: Any, right: Any) -> bool:
    if "カラー" in axis_key or axis_key in ("色", "色選択"):
        return normalize_color(left) == normalize_color(right)
    if "サイズ" in axis_key or "幅" in axis_key or "丈" in axis_key:
        left_dim, right_dim = normalize_dimension(left), normalize_dimension(right)
        if left_dim and right_dim:
            return left_dim == right_dim
    return str(left) == str(right)


def compare_explicit_sku_axes(raku_options: dict[str, Any], au_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Diagnostic only: compare only axis keys explicitly present on both sources."""
    normalized_au_rows = []
    for row in au_rows:
        options = {}
        if row.get("row_option_name"):
            options[str(row["row_option_name"])] = row.get("row_option_value")
        if row.get("column_option_name"):
            options[str(row["column_option_name"])] = row.get("column_option_value")
        normalized_au_rows.append((row, options))
    shared_axes = sorted(set(raku_options) & {axis for _, options in normalized_au_rows for axis in options})
    if not shared_axes:
        return {"comparison_status": "not_evaluated", "shared_axis_keys": [], "matching_au_rows": [], "matching_au_row_count": None, "meaning": "source axis keys do not intersect; no generic row/column assumption applied"}
    matches = []
    for source_row, options in normalized_au_rows:
        if all(axis_values_match(axis, raku_options[axis], options[axis]) for axis in shared_axes):
            matches.append({"line": source_row.get("_source_line"), "row_index": source_row.get("row_index"), "column_index": source_row.get("column_index")})
    return {
        "comparison_status": "matched" if matches else "no_matching_AU_rows",
        "shared_axis_keys": shared_axes,
        "matching_au_rows": matches,
        "matching_au_row_count": len(matches),
        "meaning": "SKU attribute overlap diagnostic only; not product or SKU identity acceptance",
    }


def axes_for_product(p: dict[str, Any], side: str) -> list[dict[str, Any]]:
    if side == "raku":
        return p.get("axes") or []
    sku = p.get("sku") or {}
    names = sku.get("option_name") or {}
    result = []
    for axis_key, values_key in (("row", "row_names"), ("column", "column_names")):
        name = names.get(axis_key)
        values = sku.get(values_key) or []
        if name and values:
            result.append({"key": name, "values": values})
    return result


def normalize_dimension(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).replace("幅", "").replace("丈", "").replace("cm", "")
    s = re.sub(r"\s+", "", s).replace("×", "x").replace("✕", "x").replace("＊", "x")
    m = re.search(r"(\d+)x(\d+)", s)
    return f"{m.group(1)}x{m.group(2)}" if m else None


def normalize_color(value: Any) -> str:
    return re.sub(r"[\s（）()【】]", "", str(value or "")).replace("色", "")


def find_axis_value(axes: list[dict[str, Any]], value: str, mode: str) -> str | None:
    for axis in axes:
        for option in axis.get("values", []):
            if mode == "dimension" and normalize_dimension(option) == normalize_dimension(value):
                return str(option)
            if mode == "color" and normalize_color(option) == normalize_color(value):
                return str(option)
    return None


def au_rows_from_roots(au_roots: list[Path]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    products_by_id: dict[str, dict[str, Any]] = {}
    sku_rows_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # The first directory is primary; reference snapshots fill only missing IDs.
    for root in au_roots:
        for product in read_jsonl(root / "products.jsonl"):
            products_by_id.setdefault(str(product.get("item_id")), product)
        for row in read_jsonl(root / "skus.jsonl"):
            sku_rows_by_id[str(row.get("item_id"))].append(row)
    primary_ids = {str(p.get("item_id")) for p in read_jsonl(au_roots[0] / "products.jsonl")}
    for aid in primary_ids:
        sku_rows_by_id[aid] = [r for r in sku_rows_by_id[aid] if Path(r["_source_dir"]) == au_roots[0]]
    products = list(products_by_id.values())
    sku_rows = [row for rows in sku_rows_by_id.values() for row in rows]
    skus_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sku_rows:
        skus_by_id[str(row.get("item_id"))].append(row)
    return products, skus_by_id


def au_rows_from_root(au_root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    return au_rows_from_roots([au_root])


def rake_rows_from_roots(roots: list[Path]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    # Support one table pair at each root or several immutable batch pairs.
    products_by_manage: dict[str, dict[str, Any]] = {}
    skus_by_manage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for root in roots:
        if (root / "products.jsonl").is_file() and (root / "skus.jsonl").is_file():
            # A direct table pair is an explicit snapshot; sibling/child
            # collection experiments are not implicitly merged into it.
            table_dirs = [root]
        else:
            table_dirs = sorted({p.parent for p in root.rglob("products.jsonl") if (p.parent / "skus.jsonl").is_file()})
        if not table_dirs:
            raise SystemExit(f"no products.jsonl + skus.jsonl table pair found under {root}")
        for table_dir in table_dirs:
            for row in read_jsonl(table_dir / "products.jsonl"):
                products_by_manage.setdefault(str(row.get("manage_number")), row)
            for row in read_jsonl(table_dir / "skus.jsonl"):
                skus_by_manage[str(row.get("manage_number"))].append(row)
    return list(products_by_manage.values()), skus_by_manage


def rakuten_source_dir(product: dict[str, Any], fallback: Path) -> Path:
    return Path(product.get("_source_dir") or fallback)


def attr_signature(p: dict[str, Any]) -> dict[str, set[str]]:
    axes = axes_for_product(p, "au" if "item_title" in p else "raku")
    sizes: set[str] = set()
    colors: set[str] = set()
    lace: set[str] = set()
    for axis in axes:
        key = str(axis.get("key", ""))
        for raw_value in axis.get("values", []):
            value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
            if "サイズ" in key or "size" in key.lower():
                d = normalize_dimension(value)
                if d:
                    sizes.add(d)
            if "カラー" in key or "color" in key.lower():
                colors.add(normalize_color(value))
            if "レース" in key:
                lace.add(str(value))
    return {"sizes": sizes, "colors": colors, "lace": lace}


def source_paths(au_root: Path, raku_root: Path, au: dict[str, Any], raku: dict[str, Any]) -> dict[str, Any]:
    product_root = Path(au.get("_source_dir") or au_root)
    au_item_raw = au_raw_item_path(au, au_root)
    return {
        "au_product_jsonl": {"file": au.get("_source_file", str(product_root / "products.jsonl")), "line": au.get("_source_line")},
        "au_sku_jsonl": {"file": str(product_root / "skus.jsonl")},
        "au_raw_item": {
            "file": str(au_item_raw),
            "sha256": sha256_cached(au_item_raw) if au_item_raw.exists() else None,
            "json_path": "$.itemInfo.extraItemComment",
        },
        "rakuten_product_jsonl": {"file": raku.get("_source_file", str(rakuten_source_dir(raku, raku_root) / "products.jsonl")), "line": raku.get("_source_line")},
        "rakuten_raw": {"file": raku.get("raw_file"), "sha256": raku.get("sha256")},
    }


def au_item_evidence_excerpt(au_root: Path, au_id: str, manage_number: str | None = None) -> list[str]:
    path = au_raw_item_path({"item_id": au_id, "_source_dir": str(au_root)}, au_root)
    if not path.exists():
        return []
    item = cached_au_raw_item(path).get("itemInfo", {})
    html = str(item.get("extraItemComment", ""))
    out: list[str] = []
    patterns = [r"(?:banner/ranking/|itemimg/CT/)(?:ct0|ctk)[^\"'<> ]*"]
    if manage_number:
        patterns.append(r"(?:TOP|itemimg)/[^\"'<>\s]*" + re.escape(manage_number) + r"[^\"'<>\s]*")
    found_paths = []
    for pattern in patterns:
        found_paths.extend(re.findall(pattern, html, flags=re.I))
    for code_path in found_paths:
        if code_path not in out:
            out.append(code_path)
    for href, label in re.findall(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        if "/item/" in href and re.search(r"カーテン|4枚|2枚|レース", label):
            label = re.sub(r"<[^>]+>", " ", label)
            label = re.sub(r"\s+", " ", label).strip()
            out.append(f"description item link: {href} ({label[:100]})")
    content_match = re.search(r"<b>内容</b>\s*</td>(.*?)</tr>", html, flags=re.S)
    if content_match:
        content = content_match.group(1)
        content = re.sub(r"<br\s*/?>", " / ", content, flags=re.I)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip(" /\r\n\t")
        if content:
            out.append("内容 table: " + content[:700])
    return out


def candidate_reasons(au: dict[str, Any], raku: dict[str, Any], au_root: Path) -> list[str]:
    title = str(au.get("item_title", ""))
    rtitle = str(raku.get("title", ""))
    mid = str(raku.get("manage_number"))
    category = next((term for term in REVIEW_TERMS.get(mid, ()) if term in title and term in rtitle), None)
    if category is None:
        # Preserve product-code references in related-item blocks as review-only
        # evidence; never let these promote an identity mapping.
        raw_path = au_raw_item_path(au, au_root)
        if raw_path.exists():
            if mid.casefold() in cached_au_asset_tokens(raw_path):
                return [f"observed AU description asset filename contains Rakuten management token {mid}; context may be a related-item block", f"AU/Rakuten title category conflict: {title[:55]} <> {rtitle[:55]}"]
        return []
    a = attr_signature(au)
    r = attr_signature(raku)
    overlaps = []
    if a["sizes"] & r["sizes"]:
        overlaps.append(f"size_overlap={len(a['sizes'] & r['sizes'])}")
    if a["colors"] & r["colors"]:
        overlaps.append(f"color_overlap={len(a['colors'] & r['colors'])}")
    reasons = [f"observed category keyword overlap: {category}", *overlaps]
    if not overlaps:
        reasons.append("no shared normalized size/color axis value observed; retain for manual review only")
    if a["lace"] and r["lace"] and not (a["lace"] & r["lace"]):
        reasons.append("observed structural conflict: lace axis values do not intersect")
    layer_a = re.search(r"(\d+)層", title)
    layer_r = re.search(r"(\d+)層", rtitle)
    if layer_a and layer_r and layer_a.group(1) != layer_r.group(1):
        reasons.append(f"observed structural conflict: AU {layer_a.group(1)}層 vs Rakuten {layer_r.group(1)}層")
    heat_a = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*℃", title)
    heat_r = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*℃", rtitle)
    if heat_a and heat_r and heat_a.group(1) != heat_r.group(1):
        reasons.append(f"observed thermal-claim conflict: AU +{heat_a.group(1)}℃ vs Rakuten +{heat_r.group(1)}℃")
    return reasons


def au_lace_expectation(au_id: str, au_root: Path, product: dict[str, Any]) -> tuple[str | None, str | None]:
    # Restrict parsing to source-mapped products and exact item-description evidence.
    raw_path = au_raw_item_path(product, au_root)
    if not raw_path.exists():
        return None, None
    raw = cached_au_raw_item(raw_path)
    desc = str(raw.get("itemInfo", {}).get("extraItemComment", ""))
    # Content table provides an explicit quantity for lace curtains on the 4-set;
    # the 2-set source explicitly enumerates drapes but no lace curtains.
    content_match = re.search(r"<b>内容</b>\s*</td>(.*?)</tr>", desc, flags=re.S)
    content = content_match.group(1) if content_match else ""
    if re.search(r"レースカーテン\s*\d+枚", content):
        return "あり", "$.itemInfo.extraItemComment (内容 table: レースカーテン枚数)"
    if re.search(r"遮光カーテン\s*\d+枚", content) and "レースカーテン" not in content:
        return "なし", "$.itemInfo.extraItemComment (内容 table: drape-only quantities)"
    return None, None


def primary_image_family_evidence(
    au: dict[str, Any], raku: dict[str, Any], au_root: Path,
) -> dict[str, Any] | None:
    """Return conservative, inspectable family evidence from AU's first image.

    This requires the main image basename to contain the exact Rakuten manage
    token, an AU shop name matching the Rakuten store URL slug, and a shared
    explicit product-type term. It remains a review mapping, not SKU identity.
    """
    path = au_raw_item_path(au, au_root)
    if not path.is_file():
        return None
    item = cached_au_raw_item(path).get("itemInfo", {})
    images = item.get("productImageUrls") or []
    if not images:
        return None
    main_url = str(images[0])
    basename = urlparse(main_url).path.rsplit("/", 1)[-1].casefold()
    manage = str(raku.get("manage_number") or "").casefold()
    if not manage or not re.search(r"(?<![a-z0-9])" + re.escape(manage) + r"(?:[-_.]|$)", basename):
        return None
    path_parts = [x for x in urlparse(str(raku.get("source_url") or "")).path.split("/") if x]
    raku_shop_slug = path_parts[0].casefold() if len(path_parts) >= 2 else ""
    au_shop = re.sub(r"[^a-z0-9]", "", str(au.get("shop_name") or "").casefold())
    if not raku_shop_slug or au_shop != re.sub(r"[^a-z0-9]", "", raku_shop_slug):
        return None
    au_title, raku_title = str(au.get("item_title", "")), str(raku.get("title", ""))
    manage_terms = REVIEW_TERMS.get(str(raku.get("manage_number")), ())
    shared_type = next((term for term in (*manage_terms, *PRODUCT_TYPE_TERMS) if term in au_title and term in raku_title), None)
    if not shared_type:
        return None
    au_sig, raku_sig = attr_signature(au), attr_signature(raku)
    for axis in ("sizes", "colors", "lace"):
        if au_sig[axis] and raku_sig[axis] and not (au_sig[axis] & raku_sig[axis]):
            return None
    layer_a = re.search(r"(\d+)層", au_title)
    layer_r = re.search(r"(\d+)層", raku_title)
    if layer_a and layer_r and layer_a.group(1) != layer_r.group(1):
        return None
    heat_a = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*℃", au_title)
    heat_r = re.search(r"\+\s*(\d+(?:\.\d+)?)\s*℃", raku_title)
    if heat_a and heat_r and heat_a.group(1) != heat_r.group(1):
        return None
    return {
        "mapping_basis": "unique Rakuten manage_number token in AU primary image basename + matching AU shop name/Rakuten store slug + shared product-type term; family mapping for review, not SKU identity",
        "primary_image_url": main_url,
        "primary_image_json_path": "$.itemInfo.productImageUrls[0]",
        "primary_image_raw_file": str(path),
        "primary_image_raw_file_sha256": sha256_cached(path),
        "primary_image_basename": basename,
        "manage_number_token": manage,
        "au_shop_name": au.get("shop_name"),
        "rakuten_store_slug": raku_shop_slug,
        "shared_product_type_term": shared_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--au-dir", type=Path, action="append", help="repeat for ordered snapshots; first directory wins duplicate AU IDs")
    parser.add_argument("--rakuten-dir", type=Path, action="append", help="repeat for ordered snapshots/batch roots; first product record per manage_number wins")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    au_roots = args.au_dir or [DEFAULT_AU]
    raku_roots = args.rakuten_dir or [DEFAULT_RAKUTEN]
    au_root, raku_root, out = au_roots[0], raku_roots[0], args.output_dir
    errors: list[dict[str, Any]] = []
    sku_exclusions: list[dict[str, Any]] = []
    for base in au_roots:
        for name in ("products.jsonl", "skus.jsonl"):
            if not (base / name).exists():
                raise SystemExit(f"missing required input: {base / name}")
    for base in raku_roots:
        if not base.exists():
            raise SystemExit(f"missing required input directory: {base}")
    # Each run is an immutable snapshot. Refuse to replace any prior output.
    out.mkdir(parents=True, exist_ok=False)
    au_products, au_skus = au_rows_from_roots(au_roots)
    raku_products, raku_skus = rake_rows_from_roots(raku_roots)
    raku_by_manage = {str(p.get("manage_number")): p for p in raku_products}
    pair_rows: list[dict[str, Any]] = []
    expanded: list[dict[str, Any]] = []
    candidate_expanded: list[dict[str, Any]] = []
    au_array_rows: list[dict[str, Any]] = []
    au_array_ref_by_id: dict[str, dict[str, Any]] = {}
    paired_ids: set[tuple[str, str]] = set()

    # Store each full observed AU SKU array once and reference it from each
    # Rakuten SKU row instead of copying that potentially large array N times.
    for au in au_products:
        aid = str(au.get("item_id"))
        sku_path = Path(au.get("_source_dir") or au_root) / "skus.jsonl"
        cell_rows = []
        sku_strings = []
        for cell in au_skus.get(aid, []):
            axes = []
            for name_key, value_key in (("row_option_name", "row_option_value"), ("column_option_name", "column_option_value")):
                name, value = cell.get(name_key), cell.get(value_key)
                if name is not None or value is not None:
                    axes.append({"axis_name_raw": name, "value_raw": value})
            label = " / ".join(f"{x['axis_name_raw']}={x['value_raw']}" for x in axes if x["axis_name_raw"] is not None and x["value_raw"] is not None)
            sku_strings.append(label)
            cell_rows.append({
                "source_grain": {"file": cell.get("_source_file", str(sku_path)), "line": cell.get("_source_line"), "sku_id": cell.get("sku_id"), "row_index": cell.get("row_index"), "column_index": cell.get("column_index")},
                "axes_raw": axes,
                "stock_raw": cell.get("stock"),
                "source_record_raw": clean(cell),
            })
        item_raw = au_raw_item_path(au, au_root)
        array_row = {
            "au_product_id": aid,
            "au_product_title_raw": au.get("item_title"),
            "au_product_price_jpy_once": au.get("current_price"),
            "au_price_grain": "product-level current_price; not repeated as a per-SKU value",
            "au_product_options_raw": {"free_options": au.get("free_options"), "paid_options": au.get("paid_options")},
            "au_sku_cells_count": len(cell_rows),
            "au_sku_cells_raw": cell_rows,
            "sku_string_array_projection": sku_strings,
            "projection_rule": "concatenate only observed option_name=value pairs in source row/column order; missing labels remain missing; no canonical attributes inferred",
            "provenance": {
                "product_jsonl": {"file": au.get("_source_file"), "line": au.get("_source_line")},
                "sku_jsonl": {"file": str(sku_path), "sha256": sha256_cached(sku_path), "source_rows": len(cell_rows)},
                "item_api": au.get("item_api"), "options_api": au.get("options_api"),
                "raw_item_json": {"file": str(item_raw), "sha256": sha256_cached(item_raw) if item_raw.is_file() else None},
            },
        }
        row_hash = hashlib.sha256(json.dumps(array_row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        au_array_ref_by_id[aid] = {"file": "au_product_sku_arrays.jsonl", "line": len(au_array_rows) + 1, "record_sha256": row_hash}
        au_array_rows.append(array_row)

    # Preserve explicit source-mapping pairs when both endpoints were observed.
    for au in au_products:
        aid = str(au.get("item_id"))
        mapping = KNOWN_SOURCE_MAPS.get(aid)
        if mapping:
            raku = raku_by_manage.get(str(mapping["manage_number"]))
            if raku is not None:
                pair_rows.append({
                    "pair_id": f"au:{aid}|raku:{mapping['manage_number']}",
                    "au_product_id": aid,
                    "au_product_title": au.get("item_title"),
                    "rakuten_manage_number": mapping["manage_number"],
                    "rakuten_url": raku.get("source_url"),
                    "rakuten_title": raku.get("title"),
                    "pair_status": "confirmed_source_mapping",
                    "is_independent_gold": False,
                    "mapping_basis": mapping["basis"],
                    "expected_lace_configuration": mapping.get("expected_lace"),
                    "evidence": source_paths(au_root, raku_root, au, raku) | {
                        "au_json_path": mapping["au_evidence_path"],
                        "rakuten_json_path": mapping["raku_evidence_path"],
                        "evidence_text": au_item_evidence_excerpt(Path(au.get("_source_dir") or au_root), aid, mapping["manage_number"]) + [
                            "Rakuten title: " + str(raku.get("title", "")),
                            "Rakuten axes: " + json.dumps(raku.get("axes", []), ensure_ascii=False),
                        ],
                    },
                    "product_axes_au": au.get("sku", {}),
                    "product_axes_rakuten": raku.get("axes", []),
                    "rakuten_product_sku_count": raku.get("sku_count"),
                })
                paired_ids.add((aid, str(mapping["manage_number"])))
            else:
                errors.append({"kind": "source_mapped_rakuten_missing", "au_product_id": aid, "manage_number": mapping["manage_number"]})

    # Add explicit main-image family evidence as a separate review class. A
    # unique exact management token in the first AU product image is required;
    # related-image blocks never contribute to this mapping class.
    for au in au_products:
        aid = str(au.get("item_id"))
        matches = []
        for raku in raku_products:
            mid = str(raku.get("manage_number"))
            if (aid, mid) in paired_ids:
                continue
            evidence = primary_image_family_evidence(au, raku, au_root)
            if evidence:
                matches.append((raku, evidence))
        if len(matches) != 1:
            continue
        raku, family_evidence = matches[0]
        mid = str(raku.get("manage_number"))
        pair_rows.append({
            "pair_id": f"au:{aid}|raku:{mid}",
            "au_product_id": aid,
            "au_product_title": au.get("item_title"),
            "rakuten_manage_number": mid,
            "rakuten_url": raku.get("source_url"),
            "rakuten_title": raku.get("title"),
            "pair_status": "evidence_backed_primary_image_family_mapping",
            "is_independent_gold": False,
            "mapping_basis": family_evidence["mapping_basis"],
            "candidate_evidence": [
                "unique exact Rakuten manage token observed in AU primary product image basename",
                f"same-shop check: AU {family_evidence['au_shop_name']} matches Rakuten store slug {family_evidence['rakuten_store_slug']}",
                f"product-type term shared in both titles: {family_evidence['shared_product_type_term']}",
            ],
            "primary_image_evidence": family_evidence,
            "evidence": source_paths(au_root, raku_root, au, raku) | {
                "au_json_path": "$.itemInfo.productImageUrls[0]",
                "evidence_text": [family_evidence["primary_image_url"], "Rakuten title: " + str(raku.get("title", ""))],
            },
            "product_axes_au": au.get("sku", {}),
            "product_axes_rakuten": raku.get("axes", []),
            "rakuten_product_sku_count": raku.get("sku_count"),
        })
        paired_ids.add((aid, mid))

    # Keep attribute-supported near candidates for human review. They are never
    # promoted to confirmed merely because their titles look similar.
    for au in au_products:
        aid = str(au.get("item_id"))
        for raku in raku_products:
            mid = str(raku.get("manage_number"))
            if (aid, mid) in paired_ids:
                continue
            reasons = candidate_reasons(au, raku, au_root)
            if not reasons:
                continue
            pair_rows.append({
                "pair_id": f"au:{aid}|raku:{mid}",
                "au_product_id": aid,
                "au_product_title": au.get("item_title"),
                "rakuten_manage_number": mid,
                "rakuten_url": raku.get("source_url"),
                "rakuten_title": raku.get("title"),
                "pair_status": "candidate_review",
                "is_independent_gold": False,
                "mapping_basis": "attribute-overlap review candidate only; no identity assertion",
                "candidate_evidence": reasons,
                "evidence": source_paths(au_root, raku_root, au, raku) | {
                    "evidence_text": au_item_evidence_excerpt(Path(au.get("_source_dir") or au_root), aid, mid) + ["AU title: " + str(au.get("item_title", "")), "Rakuten title: " + str(raku.get("title", "")), *reasons],
                },
                "product_axes_au": au.get("sku", {}),
                "product_axes_rakuten": raku.get("axes", []),
                "rakuten_product_sku_count": raku.get("sku_count"),
            })

    by_au = defaultdict(list)
    for row in pair_rows:
        by_au[row["au_product_id"]].append(row)
    for au in au_products:
        aid = str(au.get("item_id"))
        rows = au_skus.get(aid, [])
        if not au.get("is_sku_product"):
            sku_exclusions.append({"record_status": "excluded_au_product_without_sku", "side": "au", "au_product_id": aid, "reason": "source product reports is_sku_product=false", "product_jsonl": {"file": au.get("_source_file"), "line": au.get("_source_line")}})
        elif not rows:
            sku_exclusions.append({"record_status": "excluded_au_zero_sku_rows", "side": "au", "au_product_id": aid, "reason": "no rows in source skus.jsonl", "product_jsonl": {"file": au.get("_source_file"), "line": au.get("_source_line")}})
        if not by_au[aid]:
            pair_rows.append({
                "pair_id": f"au:{aid}|raku:null", "au_product_id": aid,
                "au_product_title": au.get("item_title"), "rakuten_manage_number": None,
                "rakuten_url": None, "rakuten_title": None, "pair_status": "unmatched",
                "is_independent_gold": False, "mapping_basis": "no source-supported Rakuten product observed",
                "reason": "no confirmed source mapping or attribute-supported review candidate",
                "evidence": {"au_product_jsonl": {"file": au.get("_source_file", str(au_root / "products.jsonl")), "line": au.get("_source_line")}},
            })

    for raku in raku_products:
        mid = str(raku.get("manage_number"))
        sku_rows = raku_skus.get(mid, [])
        if int(raku.get("sku_count") or 0) == 0 or not sku_rows:
            has_au_pair = any(str(x.get("rakuten_manage_number")) == mid and x.get("au_product_id") is not None for x in pair_rows)
            if not has_au_pair:
                pair_rows.append({
                    "pair_id": f"au:null|raku:{mid}", "au_product_id": None,
                    "au_product_title": None, "rakuten_manage_number": mid,
                    "rakuten_url": raku.get("source_url"), "rakuten_title": raku.get("title"),
                    "pair_status": "rakuten_zero_sku_unmatched", "is_independent_gold": False,
                    "mapping_basis": "observed Rakuten product has no SKU rows and no AU-side product pair was established",
                    "record_status": "excluded_no_rakuten_sku_rows",
                    "reason": f"no source SKU rows; observed sku_count={raku.get('sku_count')}",
                    "evidence": {"rakuten_product_jsonl": {"file": raku.get("_source_file", str(rakuten_source_dir(raku, raku_root) / "products.jsonl")), "line": raku.get("_source_line")}, "raw_file": raku.get("raw_file"), "sha256": raku.get("sha256")},
                    "rakuten_product_sku_count": raku.get("sku_count"),
                })
                if int(raku.get("sku_count") or 0) > 0:
                    errors.append({"kind": "rakuten_sku_rows_missing", "rakuten_manage_number": mid, "source_sku_count": raku.get("sku_count"), "reason": "product reports a positive SKU count but source skus.jsonl has no rows"})
                    pair_rows[-1]["record_status"] = "source_error_sku_rows_missing"
                else:
                    sku_exclusions.append({"record_status": "excluded_rakuten_zero_sku_rows", "side": "rakuten", "rakuten_manage_number": mid, "reason": f"no source SKU rows; observed sku_count={raku.get('sku_count')}; no synthetic SKU record emitted", "rakuten_product_jsonl": {"file": raku.get("_source_file"), "line": raku.get("_source_line")}, "raw_file": raku.get("raw_file"), "raw_sha256": raku.get("sha256")})

    # Expand each Rakuten SKU from each confirmed product pair. Every original
    # SKU survives, including naturally mismatching structure values.
    au_by_id = {str(p.get("item_id")): p for p in au_products}
    pair_au_table_cache: dict[str, tuple[list[dict[str, Any]], str]] = {}
    for pair in pair_rows:
        if pair.get("pair_status") not in ("confirmed_source_mapping", "candidate_review", "evidence_backed_primary_image_family_mapping"):
            continue
        aid = str(pair["au_product_id"])
        mid = str(pair["rakuten_manage_number"])
        au = au_by_id[aid]
        raku = raku_by_manage[mid]
        au_table = au_skus.get(aid, [])
        r_skus = raku_skus.get(mid, [])
        expected_lace, lace_evidence = au_lace_expectation(aid, au_root, au)
        if aid not in pair_au_table_cache:
            au_sku_path = Path(au.get("_source_dir") or au_root) / "skus.jsonl"
            pair_au_table_cache[aid] = (au_table, sha256_cached(au_sku_path))
        _, au_sku_hash = pair_au_table_cache[aid]
        pair_is_confirmed = pair.get("pair_status") == "confirmed_source_mapping"
        for rsku in r_skus:
            opts_raw = rsku.get("option_values")
            opts = option_map(opts_raw)
            sku_axis_diagnostic = compare_explicit_sku_axes(opts, au_table)
            r_lace = next((str(v) for k, v in opts.items() if "レース" in str(k)), None)
            composition_compatible = None if expected_lace is None or r_lace is None else expected_lace == r_lace
            expanded_row = {
                "source_record_type": "rakuten_sku_expanded_from_confirmed_product_pair" if pair_is_confirmed else ("rakuten_sku_expanded_from_primary_image_family_mapping_review" if pair.get("pair_status") == "evidence_backed_primary_image_family_mapping" else "rakuten_sku_expanded_from_candidate_review_pair"),
                "pair_id": pair["pair_id"], "pair_status": pair["pair_status"],
                "is_independent_gold": False,
                "au_product_ref": {
                    "item_id": aid, "title": au.get("item_title"),
                    "source_file": au.get("_source_file", str(au_root / "products.jsonl")), "source_line": au.get("_source_line"),
                    "product_snapshot_sha256": sha256_cached(Path(au.get("_source_file", str(au_root / "products.jsonl")))),
                    "inherited_product_attributes": {
                        "title": "products.jsonl:item_title", "current_price": "products.jsonl:current_price",
                        "shop": "products.jsonl:shop_name", "options": "products.jsonl:free_options,paid_options",
                        "axes": "products.jsonl:sku.option_name,row_names,column_names",
                    },
                },
                "au_sku_table_ref": {
                    "file": str(Path(au.get("_source_dir") or au_root) / "skus.jsonl"), "sha256": au_sku_hash,
                    "item_id": aid, "record_count": len(au_table),
                    "array_ref": au_array_ref_by_id.get(aid),
                    "sku_attribute_comparison": sku_axis_diagnostic,
                    "match_rule": "diagnostic only; compare explicitly shared source axis keys, otherwise not_evaluated",
                },
                "au_product_price_jpy": au.get("current_price"),
                "au_price_grain": "product-level current_price; no AU per-SKU price in source SKU rows",
                "rakuten_sku": clean(rsku),
                "rakuten_sku_source": {
                    "file": rsku.get("_source_file", str(rakuten_source_dir(raku, raku_root) / "skus.jsonl")), "line": rsku.get("_source_line"),
                    "source_row_index": rsku.get("source_row_index"),
                    "sku_record_key": rsku.get("sku_record_key"),
                    "variant_id": rsku.get("variant_id"),
                    "sha256": sha256_cached(Path(rsku.get("_source_file", str(rakuten_source_dir(raku, raku_root) / "skus.jsonl")))),
                    "dedup_applied": False,
                    "row_grain": "one source JSONL row; source_url/raw SHA + source_row_index (or source line) + variant_id where available",
                },
                "rakuten_product_ref": {"manage_number": mid, "url": raku.get("source_url"), "title": raku.get("title"), "raw_file": raku.get("raw_file"), "sha256": raku.get("sha256")},
                "configuration": {
                    "au_expected_lace": expected_lace, "au_configuration_evidence": lace_evidence,
                    "rakuten_lace_option_value": r_lace,
                    "composition_compatible": composition_compatible,
                    "sku_decision": "pending",
                    "preclusion_reason": (f"observed AU fixed composition expects lace={expected_lace}; Rakuten SKU option is lace={r_lace}" if composition_compatible is False else None),
                },
                "raw_rakuten_option_values": opts_raw,
                "raku_title_raw": raku.get("title"),
                "raku_axes_raw": raku.get("axes"),
                "raku_price_raw": rsku.get("price_raw_as_in_source"),
                "raku_price_jpy": rsku.get("price_jpy"),
                "raku_availability_raw": {k: rsku.get(k) for k in ("availability_from_embedded_quantity", "availability_evidence", "visible_availability", "inventory_quantity_from_embedded_json", "hidden_sku")},
                "au_product_options_raw": {"free_options": au.get("free_options"), "paid_options": au.get("paid_options")},
                "provenance": pair.get("evidence"),
            }
            (expanded if pair_is_confirmed else candidate_expanded).append(expanded_row)
        if not r_skus:
            source_sku_count = int(raku.get("sku_count") or 0)
            if source_sku_count == 0:
                sku_exclusions.append({"record_status": "excluded_rakuten_zero_sku_rows", "side": "rakuten", "pair_id": pair["pair_id"], "pair_status": pair.get("pair_status"), "au_product_id": aid, "rakuten_manage_number": mid, "reason": f"no source SKU rows; observed sku_count={raku.get('sku_count')}; no synthetic SKU record emitted", "rakuten_product_jsonl": {"file": raku.get("_source_file"), "line": raku.get("_source_line")}, "raw_file": raku.get("raw_file"), "raw_sha256": raku.get("sha256")})
                pair["sku_record_status"] = "excluded_no_rakuten_sku_rows"
                pair["sku_exclusion_reason"] = "observed sku_count=0; product pair retained without SKU expansion"
            else:
                errors.append({"kind": "rakuten_sku_rows_missing", "au_product_id": aid, "manage_number": mid, "source_sku_count": raku.get("sku_count"), "reason": "product reports a positive SKU count but source skus.jsonl has no rows"})

    pair_rows.sort(key=lambda x: (str(x.get("au_product_id")), x.get("pair_status", ""), str(x.get("rakuten_manage_number"))))
    expanded.sort(key=lambda x: (x["au_product_ref"]["item_id"], str(x["rakuten_sku"].get("sku_id")), x["rakuten_sku_source"]["line"] or 0))
    candidate_expanded.sort(key=lambda x: (x["au_product_ref"]["item_id"], str(x["rakuten_sku"].get("sku_id")), x["rakuten_sku_source"]["line"] or 0))
    write_jsonl(out / "observed_product_pairs.jsonl", pair_rows)
    write_jsonl(out / "au_product_sku_arrays.jsonl", au_array_rows)
    write_jsonl(out / "rakuten_sku_expanded.jsonl", expanded)
    write_jsonl(out / "candidate_sku_review.jsonl", candidate_expanded)
    write_jsonl(out / "sku_exclusions.jsonl", sku_exclusions)
    write_jsonl(out / "errors.jsonl", errors)

    # Human-readable table, retaining the same source and evidence classifications.
    with (out / "observed_product_pairs.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["pair_id", "au_product_id", "au_product_title", "rakuten_manage_number", "rakuten_url", "rakuten_title", "pair_status", "is_independent_gold", "mapping_basis", "candidate_evidence", "expected_lace_configuration", "reason"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in pair_rows:
            cooked = dict(row)
            if isinstance(cooked.get("candidate_evidence"), list):
                cooked["candidate_evidence"] = " | ".join(cooked["candidate_evidence"])
            w.writerow(cooked)

    sku_by_composition = Counter(str(x["configuration"]["composition_compatible"]) for x in expanded + candidate_expanded)
    sku_by_pair_status = Counter(str(x["pair_status"]) for x in expanded + candidate_expanded)
    sku_ids_by_product: dict[str, Counter[str]] = defaultdict(Counter)
    for mid, rows in raku_skus.items():
        for row in rows:
            if row.get("sku_id") is not None:
                sku_ids_by_product[mid][str(row["sku_id"])] += 1
    duplicate_id_groups = sum(1 for counts in sku_ids_by_product.values() for count in counts.values() if count > 1)
    duplicate_id_rows = sum(count - 1 for counts in sku_ids_by_product.values() for count in counts.values() if count > 1)
    table_inputs = sorted(Path(p) for p in INPUT_SNAPSHOT_HASHES)
    input_post_hashes = {str(p): sha256(p) for p in table_inputs}
    input_snapshot_stable = all(INPUT_SNAPSHOT_HASHES.get(str(p)) == input_post_hashes[str(p)] for p in table_inputs)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "observed product mapping candidates and Rakuten SKU expansion; not general identity accuracy evaluation",
        "source_data_origin": "observed AU item/options API snapshots and Rakuten item page/SKU snapshots",
        "synthetic_data_rows": 0,
        "independent_gold_rows": 0,
        "input_snapshot_stable": input_snapshot_stable,
        "au_product_count": len(au_products), "rakuten_product_count": len(raku_products),
        "au_sku_source_rows": sum(map(len, au_skus.values())), "rakuten_sku_source_rows": sum(map(len, raku_skus.values())),
        "rakuten_duplicate_sku_id_groups": duplicate_id_groups,
        "rakuten_duplicate_sku_id_extra_rows": duplicate_id_rows,
        "rakuten_sku_dedup_applied": False,
        "pair_rows_by_status": dict(Counter(x.get("pair_status") for x in pair_rows)),
        "expanded_rakuten_sku_records": len(expanded),
        "candidate_sku_review_records": len(candidate_expanded),
        "au_product_sku_array_records": len(au_array_rows),
        "expanded_by_pair_status": dict(sku_by_pair_status),
        "composition_compatible_counts": dict(sku_by_composition),
        "sku_decision": "pending for every expanded row; this artifact is source material, not a final CSV selection",
        "errors": len(errors),
        "errors_by_kind": dict(Counter(x.get("kind") for x in errors)),
        "sku_exclusion_records": len(sku_exclusions),
        "sku_exclusions_by_status": dict(Counter(x.get("record_status") for x in sku_exclusions)),
        "coverage_note": "Only confirmed_source_mapping pairs enter rakuten_sku_expanded.jsonl. candidate_review and evidence_backed_primary_image_family_mapping SKU rows are isolated in candidate_sku_review.jsonl and do not assert SKU identity. Natural zero-SKU or non-SKU cases are listed in sku_exclusions.jsonl, with no fabricated SKU rows. Complete AU SKU arrays are stored once per product and referenced from expansions.",
        "inputs": {"au_dirs": [str(x) for x in au_roots], "rakuten_dirs": [str(x) for x in raku_roots]},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inputs = sorted(Path(p) for p in INPUT_SNAPSHOT_HASHES)
    # Include each AU raw item JSON used for source image/spec evidence,
    # including nested raw/ layouts in additional snapshots.
    raw_paths = {au_raw_item_path(au, au_root) for au in au_products}
    inputs.extend(sorted(p for p in raw_paths if p.is_file()))
    manifest = {
        "generated_at_utc": summary["generated_at_utc"],
        "builder": "experiments/sku-matching/build_observed_pair_records.py",
        "builder_sha256": sha256(Path(__file__)),
        "inputs": [{"path": str(p), "sha256": INPUT_SNAPSHOT_HASHES.get(str(p), sha256(p)), "bytes": p.stat().st_size} for p in inputs if p.is_file()],
        "input_table_pre_post_sha256": {str(p): {"pre_build": INPUT_SNAPSHOT_HASHES.get(str(p)), "post_build": input_post_hashes[str(p)]} for p in table_inputs},
        "input_snapshot_stable": input_snapshot_stable,
        "observed_raw_source_records": {
            "au_items": [{"item_id": p.get("item_id"), "item_api_sha256": (p.get("item_api") or {}).get("sha256"), "options_api_sha256": (p.get("options_api") or {}).get("sha256")} for p in au_products],
            "rakuten_items": [{"manage_number": p.get("manage_number"), "source_url": p.get("source_url"), "raw_file": p.get("raw_file"), "raw_response_sha256": p.get("sha256")} for p in raku_products],
        },
        "outputs": {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "manifest.json"},
        "synthetic_data_included": False,
        "known_source_mappings_are_independent_gold": False,
        "rakuten_sku_identity_grain": "source JSONL row; do not assume merchant sku_id unique within item; use source_row_index/sku_record_key/variant_id and raw source hashes when present",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
