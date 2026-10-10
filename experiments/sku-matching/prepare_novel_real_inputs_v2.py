"""Build label-free source packets for the four novel AU/Rakuten families.

Each record is one captured Rakuten SKU against the complete fixed AU item
page pool. Exact evidence references point into the original AU JSON values or
the decoded captured Rakuten HTML; sibling/navigation links are stored in a
separate audit-only scope and never enter model inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUDIT = ROOT / ".lab-output/sku-novel-pair-source-audit-20261010-v1/report.json"
PAIR_META = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4/observed_product_pairs.jsonl"
OUT = ROOT / ".lab-output/sku-novel-real-inputs-20261010-v2"
SUPPORTED = {
    "au:724257905|raku:hg020",
    "au:764777172|raku:fca2160",
    "au:763454500|raku:mbc005",
    "au:398508173|raku:qaa0100",
}
MAX_DESCRIPTION_CHARS = 9000
LINK_TERMS = re.compile(r"こちら|コチラ|リンク|別ページ|色違い|シリーズ|ラインナップ", re.I)
SKIP_DESC = re.compile(r"検索ワード|検索キーワード|クーポン|ランキング|送料無料", re.I)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def clean_relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def raw_json_value_span(raw_text: str, value) -> dict:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    at = raw_text.find(encoded)
    if at < 0:
        raise ValueError("Could not locate serialized JSON value in original capture")
    return {"raw_char_start": at, "raw_char_end": at + len(encoded), "offset_unit": "unicode_codepoint"}


def json_property_evidence(raw_file: str, raw_bytes: bytes, raw_text: str,
                           property_name: str, json_path: str, scope: str) -> dict:
    marker = json.dumps(property_name, ensure_ascii=False) + ":"
    at = raw_text.find(marker)
    if at < 0:
        raise ValueError(f"JSON property not found: {json_path}")
    start = at + len(marker)
    while raw_text[start].isspace():
        start += 1
    _, end = json.JSONDecoder().raw_decode(raw_text[start:])
    end += start
    return {"scope": scope, "quote": raw_text[start:end],
            "source_ref": {"raw_file": raw_file, "raw_sha256": sha256(raw_bytes),
                           "json_path": json_path, "raw_char_start": start,
                           "raw_char_end": end, "offset_unit": "unicode_codepoint_in_raw_json"}}


def json_evidence(raw_file: str, raw_bytes: bytes, raw_text: str, json_path: str,
                  source_value: str, quote: str, scope: str) -> dict:
    start = source_value.find(quote)
    if start < 0:
        raise ValueError(f"Quote not found at {json_path}: {quote[:60]!r}")
    whole = raw_json_value_span(raw_text, source_value)
    return {
        "scope": scope,
        "quote": quote,
        "source_ref": {
            "raw_file": raw_file,
            "raw_sha256": sha256(raw_bytes),
            "json_path": json_path,
            "value_char_start": start,
            "value_char_end": start + len(quote),
            "value_offset_unit": "unicode_codepoint_within_json_string_value",
            **whole,
        },
    }


class SourceHTMLParser(HTMLParser):
    """Collect exact source text nodes and link start tags with char offsets."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0]
        self.line_starts.extend(i + 1 for i, c in enumerate(text) if c == "\n")
        self.anchor_depth = 0
        self.skip_depth = 0
        self.nodes: list[dict] = []
        self.links: list[dict] = []
        self.feed(text)

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        tag_source = self.get_starttag_text() or ""
        if tag.lower() == "a":
            self.anchor_depth += 1
            href = dict(attrs).get("href")
            self.links.append({"href": href, "start_tag_html": tag_source,
                               "html_char_start": start, "html_char_end": start + len(tag_source),
                               "scope": "navigation_or_sibling_link_audit_only"})
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.anchor_depth:
            self.anchor_depth -= 1
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        start = self._offset()
        if not data or self.anchor_depth or self.skip_depth:
            return
        # HTMLParser data is source-exact when character-reference conversion is disabled.
        if self.text[start:start + len(data)] != data:
            return
        quote = data.strip()
        if not quote:
            return
        left = len(data) - len(data.lstrip())
        self.nodes.append({"quote": quote, "html_char_start": start + left,
                           "html_char_end": start + left + len(quote),
                           "scope": "current_page_description"})


def decode_html(data: bytes) -> tuple[str, str]:
    # Rakuten raw captures declare their character set in the response body.
    for enc in ("euc_jp", "cp932", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", data, 0, min(len(data), 1), "no supported encoding")


def page_desc_nodes(parser: SourceHTMLParser) -> list[dict]:
    out, seen = [], set()
    for node in parser.nodes:
        q = node["quote"]
        if len(q) < 4 or SKIP_DESC.search(q) or LINK_TERMS.search(q):
            continue
        # Keep concrete descriptive text, dimensions, option values, and item specifications.
        informative = (len(q) >= 22 or re.search(r"\d|cm|kg|枚|本|段|畳|素材|材質|耐荷重|サイズ|カラー|メーカー型番", q))
        if not informative or q in seen:
            continue
        seen.add(q)
        out.append(node)
    return out


def bounded_blocks(blocks: list[dict], limit: int = MAX_DESCRIPTION_CHARS) -> tuple[list[dict], bool]:
    kept, total = [], 0
    for block in blocks:
        n = len(block["quote"])
        if total + n > limit:
            continue
        kept.append(block)
        total += n
    return kept, len(kept) != len(blocks)


def raw_html_span(raw_file: str, raw_bytes: bytes, encoding: str, decoded: str,
                  start: int, end: int, scope: str, quote: str | None = None) -> dict:
    exact = decoded[start:end]
    if quote is not None and exact != quote:
        raise ValueError(f"HTML span mismatch at {raw_file}:{start}-{end}")
    return {"scope": scope, "quote": exact,
            "source_ref": {"raw_file": raw_file, "raw_sha256": sha256(raw_bytes),
                           "encoding": encoding, "html_char_start": start,
                           "html_char_end": end, "offset_unit": "unicode_codepoint_after_decode"}}


def add_verified_span(evidence: dict) -> dict:
    """Attach RawStore-compatible exact span while retaining legacy source_ref."""
    if "verified_span" in evidence:
        return evidence
    ref = evidence["source_ref"]
    if "html_char_start" in ref:
        locator = {"kind": "html_text", "encoding": ref["encoding"]}
        start, end = ref["html_char_start"], ref["html_char_end"]
        quote = evidence["quote"]
    elif "value_char_start" in ref:
        locator = {"kind": "json_leaf", "json_path": ref["json_path"]}
        start, end = ref["value_char_start"], ref["value_char_end"]
        # Embedded AU HTML fields can have decoded text quotes whose source is
        # HTML markup. The verified quote is the exact raw substring in the leaf.
        quote = evidence["quote"]
        if ref.get("value_offset_unit") == "unicode_codepoint_within_embedded_html_string":
            raw_value = ref.get("_leaf_value")
            if raw_value is None:
                raise ValueError("Missing embedded JSON leaf for canonical span")
            quote = raw_value[start:end]
    elif "raw_char_start" in ref:
        locator = {"kind": "html_text", "encoding": "utf-8"}
        start, end, quote = ref["raw_char_start"], ref["raw_char_end"], evidence["quote"]
    else:
        return evidence
    evidence["verified_span"] = {"raw_file": ref["raw_file"],
                                  "sha256": ref.get("raw_sha256", ref.get("sha256")),
                                  "locator": locator, "start": start, "end": end,
                                  "quote": quote}
    return evidence


def locate_variant(decoded: str, variant_id: str) -> tuple[int, int, dict]:
    needle = '"variantId":' + json.dumps(variant_id, ensure_ascii=False)
    at = decoded.find(needle)
    if at < 0:
        # The source sometimes uses lowercase variant IDs even when the merchant SKU is uppercase.
        raise ValueError(f"Variant not found in Rakuten raw page: {variant_id}")
    start = decoded.rfind("{", 0, at)
    depth = 0
    in_string = False
    escaped = False
    end = None
    for i in range(start, len(decoded)):
        ch = decoded[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError(f"Unclosed variant object for {variant_id}")
    parsed = json.loads(decoded[start:end])
    if parsed.get("variantId") != variant_id:
        raise ValueError(f"Variant ID mismatch for {variant_id}")
    return start, end, parsed


def raw_object_property_span(decoded: str, obj_start: int, obj_end: int, key: str) -> tuple[int, int, object]:
    fragment = decoded[obj_start:obj_end]
    match = re.search(r'"' + re.escape(key) + r'"\s*:', fragment)
    if not match:
        raise ValueError(f"Property {key} missing in selected SKU object")
    value_start = obj_start + match.end()
    while decoded[value_start].isspace():
        value_start += 1
    value, relative_end = json.JSONDecoder().raw_decode(decoded[value_start:obj_end])
    return value_start, value_start + relative_end, value


def html_property_evidence(raw_file: str, raw_bytes: bytes, encoding: str, decoded: str,
                           obj_start: int, obj_end: int, property_path: str,
                           property_key: str, scope: str) -> dict:
    start, end, _ = raw_object_property_span(decoded, obj_start, obj_end, property_key)
    evidence = raw_html_span(raw_file, raw_bytes, encoding, decoded, start, end, scope)
    evidence["source_ref"]["embedded_json_path"] = property_path
    return evidence


def make_row_keys(item_id: str, sku_info: dict, flat_rows: list[dict]) -> list[dict]:
    rows = []
    for row in flat_rows:
        ri, ci = row["row_index"], row["column_index"]
        row_name = sku_info.get("rowNames", [""] * (ri + 1))[ri]
        col_name = sku_info.get("columnNames", [""] * (ci + 1))[ci]
        rows.append({**row, "row_key": f"au:{item_id}:{row['sku_id']}:{ri}:{ci}",
                     "row_option_name": sku_info.get("optionName", {}).get("row", ""),
                     "row_option_value": row_name,
                     "column_option_name": sku_info.get("optionName", {}).get("column", ""),
                     "column_option_value": col_name})
    return rows


def prepare(out: Path = OUT) -> dict:
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    entries = {entry["pair_id"]: entry for entry in audit["suggested_family_pairs"]}
    if not SUPPORTED <= set(entries):
        raise ValueError("Source audit does not contain the four expected supported families")
    metas = {d["pair_id"]: d for d in read_jsonl(PAIR_META) if d.get("pair_id") in SUPPORTED}
    if set(metas) != SUPPORTED:
        raise ValueError("Missing source-family metadata")

    input_rows, source_rows = [], []
    pair_summaries = []
    for pair_id in sorted(SUPPORTED):
        audit_entry, d = entries[pair_id], metas[pair_id]
        au_info = audit_entry["au"]; rak = audit_entry["rakuten"]
        au_file = ROOT / au_info["raw_item"]["file"]
        au_bytes = au_file.read_bytes()
        if sha256(au_bytes) != au_info["raw_item"]["sha256"]:
            raise ValueError(f"AU raw SHA mismatch: {au_file}")
        au_text = au_bytes.decode("utf-8")
        au_data = json.loads(au_text)
        item = au_data["itemInfo"]
        au_sku_info = item["skuInfo"]
        au_sku_table = au_info["sku_source_jsonl"]["file"]
        au_sku_rows = read_jsonl(ROOT / au_sku_table)
        au_sku_rows = [r for r in au_sku_rows if str(r.get("item_id")) == str(au_info["item_id"])]
        au_rows = make_row_keys(str(au_info["item_id"]), au_sku_info, au_sku_rows)
        if len(au_rows) != len(audit_entry["au"]["sku_rows"]):
            raise ValueError(f"AU SKU count drift for {pair_id}")

        rraw_ref = rak["raw_page"]
        rraw_path = ROOT / rraw_ref["file"]
        rraw_bytes = rraw_path.read_bytes()
        if sha256(rraw_bytes) != rraw_ref["sha256"]:
            raise ValueError(f"Rakuten raw SHA mismatch: {rraw_path}")
        rtext, rencoding = decode_html(rraw_bytes)
        parser = SourceHTMLParser(rtext)
        rdesc_nodes, rdesc_truncated = bounded_blocks(page_desc_nodes(parser))
        rdesc = [raw_html_span(rraw_ref["file"], rraw_bytes, rencoding, rtext,
                               block["html_char_start"], block["html_char_end"],
                               block["scope"], block["quote"])
                 for block in rdesc_nodes]
        au_desc, au_desc_truncated = [], False
        for field_name in ("extraItemComment", "detailComment"):
            value = item.get(field_name)
            if not isinstance(value, str) or not value:
                continue
            parsed = SourceHTMLParser(value)
            candidates = page_desc_nodes(parsed)
            for n in candidates:
                ev = json_evidence(au_info["raw_item"]["file"], au_bytes, au_text,
                                   f"$.itemInfo.{field_name}", value, n["quote"],
                                   "current_page_description")
                ev["source_ref"].update({"value_char_start": n["html_char_start"],
                                         "value_char_end": n["html_char_end"],
                                         "value_offset_unit": "unicode_codepoint_within_embedded_html_string",
                                         "_leaf_value": value})
                ev = add_verified_span(ev)
                ev["source_ref"].pop("_leaf_value", None)
                au_desc.append(ev)
        au_desc, au_desc_truncated = bounded_blocks(au_desc)

        # Current page titles must be literal spans in the original source.
        au_title_ev = json_evidence(au_info["raw_item"]["file"], au_bytes, au_text,
                                    "$.itemInfo.itemTitle", item["itemTitle"], item["itemTitle"],
                                    "current_page_title")
        rtitle = rak["title"]
        rtitle_at = rtext.find(rtitle)
        if rtitle_at < 0:
            raise ValueError(f"Rakuten title not found verbatim: {pair_id}")
        rtitle_ev = raw_html_span(rraw_ref["file"], rraw_bytes, rencoding, rtext,
                                  rtitle_at, rtitle_at + len(rtitle), "current_page_title", rtitle)
        au_sku_array_ev = json_property_evidence(au_info["raw_item"]["file"], au_bytes, au_text,
                                                 "skuInfo", "$.itemInfo.skuInfo", "au_original_sku_array")
        au_price_ev = json_property_evidence(au_info["raw_item"]["file"], au_bytes, au_text,
                                             "currentPrice", "$.itemInfo.currentPrice", "au_item_page_price")

        r_skus = rak["full_sku_rows"]
        # Exactly one output case per captured Rakuten SKU row.
        for sku_row in r_skus:
            variant_id = sku_row.get("variant_id")
            if not variant_id:
                raise ValueError(f"Missing variant_id for {pair_id}")
            obj_start, obj_end, variant_obj = locate_variant(rtext, variant_id)
            variant_index = next(i for i, row in enumerate(r_skus) if row["variant_id"] == variant_id)
            full_variant_ev = raw_html_span(rraw_ref["file"], rraw_bytes, rencoding, rtext,
                                            obj_start, obj_end, "selected_rakuten_sku_record_audit_only")
            selected_attr_evidence = [
                html_property_evidence(rraw_ref["file"], rraw_bytes, rencoding, rtext, obj_start, obj_end,
                                       f"$.embedded_sku[{variant_index}].{key}", key, scope)
                for key, scope in (("variantId", "selected_sku_variant_id"),
                                   ("merchantDefinedSkuId", "selected_sku_merchant_id"),
                                   ("selectorValues", "selected_sku_original_selector_values"),
                                   ("taxIncludedPrice", "selected_sku_price_source"))
                if key in variant_obj
            ]
            selected_attrs = []
            series_attrs = []
            attrs_start = attrs_end = None
            if "attributes" in variant_obj:
                attrs_start, attrs_end, _ = raw_object_property_span(rtext, obj_start, obj_end, "attributes")
                attr_cursor = attrs_start
                # Locate each exact source object within the original attributes JSON array.
                for attr_i, attr in enumerate(variant_obj["attributes"]):
                    encoded_attr = json.dumps(attr, ensure_ascii=False, separators=(",", ":"))
                    attr_at = rtext.find(encoded_attr, attr_cursor, attrs_end)
                    if attr_at < 0:
                        continue
                    attr_end = attr_at + len(encoded_attr)
                    ref = {"raw_file": rraw_ref["file"], "raw_sha256": sha256(rraw_bytes),
                           "encoding": rencoding, "html_char_start": attr_at,
                           "html_char_end": attr_end, "offset_unit": "unicode_codepoint_after_decode",
                           "embedded_json_path": f"$.embedded_sku[{variant_index}].attributes[{attr_i}]"}
                    evidence = {"scope": "selected_sku_attribute" if "シリーズ" not in str(attr.get("title", "")) else "series_scope_audit_only",
                                "quote": rtext[attr_at:attr_end], "source_ref": ref}
                    record = {"source_attribute": attr, "exact_source_span": evidence}
                    if "シリーズ" in str(attr.get("title", "")):
                        series_attrs.append(record)
                    else:
                        selected_attrs.append(record)
                        selected_attr_evidence.append(evidence)
                    attr_cursor = attr_end

            option_values = sku_row.get("option_values", [])
            option_string = " / ".join(f"{v.get('axis_key')}={v.get('value')}" for v in option_values)
            case_id = f"novel-{d['rakuten_manage_number']}-{variant_id}"
            if any(x["case_id"] == case_id for x in input_rows):
                raise ValueError(f"Duplicate generated case ID {case_id}")
            # Bind every selected-variant reference to exactly this generated case.
            full_variant_ev["source_ref"].update({"case_id": case_id, "input_case_id": case_id})
            full_variant_ev = add_verified_span(full_variant_ev)
            for ev in selected_attr_evidence:
                ev["source_ref"].update({"case_id": case_id, "input_case_id": case_id})
                add_verified_span(ev)
            for record in selected_attrs:
                record["exact_source_span"]["source_ref"].update({"case_id": case_id,
                                                                  "input_case_id": case_id})
                add_verified_span(record["exact_source_span"])
            # Label-blind, task-compatible shape: one source SKU and the complete AU row pool.
            display_au_rows = [{"alias": f"a{i:03d}", "row_key": r["row_key"],
                                "sku": " / ".join(filter(None, [
                                    f"{r['row_option_name']}={r['row_option_value']}" if r['row_option_name'] else "",
                                    f"{r['column_option_name']}={r['column_option_value']}" if r['column_option_name'] else ""])),
                                "source_stock": r.get("stock")}
                               for i, r in enumerate(au_rows)]
            selected_sku = {
                "source_ref": {**full_variant_ev["source_ref"],
                               "selected_variant_only": True,
                               "verified_span": full_variant_ev["verified_span"]},
                "source_sku_key": sku_row.get("source_sku_key"),
                "variant_id": variant_id,
                "merchant_defined_sku_id": sku_row.get("merchant_defined_sku_id"),
                "sku_id": sku_row.get("sku_id"),
                "option_values": option_values,
                "selector_values_raw": variant_obj.get("selectorValues"),
                "price_jpy": sku_row.get("price_jpy"),
                "price_raw_as_in_source": sku_row.get("price_raw_as_in_source"),
                "selected_sku_attribute_evidence": selected_attrs,
                "selected_sku_exact_property_spans": selected_attr_evidence,
                "series_scope_audit_only": series_attrs,
                "raw_variant_object_audit_only": full_variant_ev,
            }
            # Only selected SKU attributes with exact raw spans enter the model input.
            # Full source row/object and series attributes remain in source-cases.jsonl.
            selected_sku_input = {k: v for k, v in selected_sku.items()
                                  if k not in ("series_scope_audit_only", "raw_variant_object_audit_only")}
            evidence_registry = [
                {"id": "at", "side": "au", "field": "title", **au_title_ev},
                {"id": "rt", "side": "rakuten", "field": "title", **rtitle_ev},
                {"id": "as", "side": "au", "field": "selected_sku_array", **au_sku_array_ev},
                {"id": "ap", "side": "au", "field": "product_price", **au_price_ev},
                *[{"id": f"ad{i:04d}", "side": "au", "field": "description", **b}
                  for i, b in enumerate(au_desc)],
                *[{"id": f"rd{i:04d}", "side": "rakuten", "field": "description", **b}
                  for i, b in enumerate(rdesc)],
                *[{"id": f"rs{i:03d}", "side": "rakuten", "field": "selected_sku", **ev}
                  for i, ev in enumerate(selected_attr_evidence)],
            ]
            evidence_registry = [add_verified_span(ev) for ev in evidence_registry]
            # Keep sibling/link markup out of the actual model-facing input.
            inp = {
                "case_id": case_id,
                "dossier_id": f"novel-pair-{hashlib.sha256(pair_id.encode()).hexdigest()[:16]}",
                "family_id": pair_id,
                "split": "unassigned_private_candidate",
                "source_category": "non_curtain_novel_family_candidate",
                "rakuten": {"url": rak["url"], "title_raw": rtitle,
                            "selected_sku": selected_sku_input,
                            "sku": option_string,
                            "page_description_blocks": rdesc},
                "au": {"item_id": au_info["item_id"], "url": au_info["source_item_url"],
                       "title_raw": item["itemTitle"], "product_price": item.get("currentPrice"),
                       "product_price_grain": "fixed item/page price; not per AU color row",
                       "sku_rows": display_au_rows,
                       "original_full_sku_array": au_sku_info,
                       "original_full_sku_array_source": au_sku_array_ev["source_ref"],
                       "product_price_source": au_price_ev["source_ref"],
                       "page_description_blocks": au_desc},
                "evidence_registry": evidence_registry,
                "source_texts": {"au_title": item["itemTitle"], "rakuten_title": rtitle,
                                 "au_descriptions": [b["quote"] for b in au_desc],
                                 "rakuten_descriptions": [b["quote"] for b in rdesc]},
                "label_status": "unlabeled_private_candidate_not_gold",
            }
            source = {
            "schema_version": "sku-novel-real-source-case-v2",
                "case_id": case_id,
                "dossier_id": inp["dossier_id"],
                "family_id": pair_id,
                "pair_id": pair_id,
                "label_status": "unlabeled_private_candidate_not_gold",
                "rakuten_title_raw": rtitle,
                "rakuten_selected_sku": option_string,
                "rakuten_selected_sku_attributes": selected_sku,
                "rakuten_current_page_description_blocks": rdesc,
                "au_title_raw": item["itemTitle"],
                "au_current_page_description_blocks": au_desc,
                "au_product_price": {"value": item.get("currentPrice"), "json_path": "$.itemInfo.currentPrice",
                                      "grain": "fixed item/page price"},
                "au_rows": au_rows,
                "au_original_full_sku_info": au_sku_info,
                "au_raw_description_html_audit_only": {
                    "extraItemComment": item.get("extraItemComment", ""),
                    "detailComment": item.get("detailComment", ""),
                    "scope": "original page description fields; may contain sibling links; not injected as evidence"
                },
                "rakuten_related_link_scope_audit_only": parser.links,
                "au_related_link_scope_audit_only": [
                    {"href": link["href"], "start_tag_html": link["start_tag_html"],
                     "html_char_start_within_json_string": link["html_char_start"],
                     "html_char_end_within_json_string": link["html_char_end"],
                     "scope": "navigation_or_sibling_link_audit_only"}
                    for value in (item.get("extraItemComment", ""), item.get("detailComment", ""))
                    for link in SourceHTMLParser(value).links
                ],
                "source_evidence_registry": evidence_registry,
                "raw_source_provenance": {
                    "au_item": {"file": au_info["raw_item"]["file"], "sha256": sha256(au_bytes),
                                "title_json_path": "$.itemInfo.itemTitle",
                                "sku_array_json_path": "$.itemInfo.skuInfo",
                                "extra_comment_json_path": "$.itemInfo.extraItemComment",
                                "retrieved_item_api": item.get("itemTitle") is not None},
                    "rakuten_page": {"file": rraw_ref["file"], "sha256": sha256(rraw_bytes),
                                     "encoding": rencoding, "url": rak["url"],
                                     "selected_variant_html_char_start": obj_start,
                                     "selected_variant_html_char_end": obj_end},
                    "au_sku_table": au_info["sku_source_jsonl"],
                    "rakuten_sku_table": rak["sku_source_jsonl"],
                },
            }
            input_rows.append(inp)
            source_rows.append(source)
        pair_summaries.append({"pair_id": pair_id, "case_count": len(r_skus),
                               "au_row_count": len(au_rows), "rakuten_product_sku_count": len(r_skus),
                               "description_blocks_truncated": {"au": au_desc_truncated,
                                                                  "rakuten": rdesc_truncated}})

    out.mkdir(parents=True)
    in_text = "".join(canonical(row) + "\n" for row in input_rows)
    source_text = "".join(canonical(row) + "\n" for row in source_rows)
    (out / "inputs.jsonl").write_text(in_text, encoding="utf-8")
    (out / "source-cases.jsonl").write_text(source_text, encoding="utf-8")
    manifest = {
        "schema_version": "sku-novel-real-inputs-manifest-v2",
        "created_utc": "2026-10-10",
        "input_count": len(input_rows),
        "source_case_count": len(source_rows),
        "case_ids_in_order": [row["case_id"] for row in input_rows],
        "family_count": len(pair_summaries),
        "families": pair_summaries,
        "rakuten_sku_count_total": sum(x["rakuten_product_sku_count"] for x in pair_summaries),
        "au_row_count_total_per_case": sum(x["au_row_count"] * x["case_count"] for x in pair_summaries),
        "inputs_sha256": sha256(in_text.encode("utf-8")),
        "inputs_bytes": len(in_text.encode("utf-8")),
        "source_cases_sha256": sha256(source_text.encode("utf-8")),
        "source_cases_bytes": len(source_text.encode("utf-8")),
        "audit_report_sha256": sha256(AUDIT.read_bytes()),
        "source_metadata_sha256": sha256(PAIR_META.read_bytes()),
        "preparer_sha256": sha256(Path(__file__).read_bytes()),
        "preparer_file": clean_relative(Path(__file__)),
        "labels_read": False,
        "gold_or_holdout": False,
        "network_used": False,
        "gpu_or_model_used": False,
        "links_sibling_series_scope_in_inputs": False,
        "source_evidence_rule": "Evidence records include additive RawStore-compatible verified_span locators where the exact string leaf or raw HTML text can be resolved; object-valued JSON provenance uses exact serialized UTF-8 JSON text spans, while semantic JSONPath remains on source_ref.",
        "schema_alignment": "One row per Rakuten SKU, full AU row pool, title/description blocks, and source_evidence_registry fields aligned with prepare_gpu_condition_tasks_v1 source-cases shape; no diagnostics or labels added.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    print(json.dumps(prepare(args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
