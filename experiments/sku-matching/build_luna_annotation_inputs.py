#!/usr/bin/env python3
"""Build blind, source-grounded Luna annotation inputs for real SKU pairs."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
SHARDS = ("shard-1", "shard-2", "shard-3")
# Catalog families that should remain together even where the captured item IDs differ.
RELATED_MANAGE_NUMBERS = (
    ("ct0", "ctk"), ("fgc005", "fgc010"), ("faa005", "faa006"),
    ("pt0033", "pt0038"), ("fel001", "fek002"),
)
FORBIDDEN_KEYS = {
    "label", "labels", "annotation", "annotations", "configuration", "expected_lace",
    "au_expected_lace", "preclusion_reason", "sku_decision", "pair_status",
    "is_independent_gold", "candidate_evidence", "mapping_basis", "known_matcher",
    "matcher_output", "model_output", "predictions", "prediction", "exact_match",
    "sku_attribute_comparison", "matching_au_rows", "matching_au_row_count",
}
TASK_SPEC = """Luna annotation task: real AU Pay Market and Rakuten SKU identity

Use cases.jsonl in the assigned shard. Resolve dossier_id through dossier_index.json and read the JSON under dossiers/. Each dossier contains the fixed AU product title, semantic product-option wording, every AU SKU row, source descriptions from the individual AU/Rakuten product pages, and provenance. `blocks`, `individual_description_excerpt`, and `purchase_options_raw` are the semantic identity input. `raw_fields`, `title_evidence_raw`, and `purchase_options_evidence_raw` are audit evidence only and must not be used as semantic input. Each case contains exactly one real Rakuten SKU option combination. Dossier hashes and their source SHA-256 values are fixed in manifest.json and the shard manifest.

Independently decide whether the Rakuten SKU represents the same specific sellable product/variant as any AU SKU row. Recheck identity from the supplied specifications and cited source text. Do not infer identity from pair IDs, file organization, prices, inventory, availability, or model/matcher outputs. Price and inventory eligibility are stored separately in eligibility.jsonl and au_eligibility.jsonl; never use them as semantic identity evidence. Image contents are not transcribed; use review when the supplied text cannot resolve the case.

Allowed decisions are matched, unmatched, and review. Keep all product specifications and exception conditions, including sentences that also contain purchase wording. Mixed specification lines remain semantic with only monetary amounts masked. Shipping, coupon, availability, purchase-CTA, review, and navigation-only lines are retained separately as nonsemantic. Full raw fields are audit evidence only. Return one JSON object per case in a separate labels.jsonl with these fields:
  case_id: exact input case_id
  decision: matched | unmatched | review
  matching_au_row_keys: list of dossier AU row_key values; required and non-empty for matched, empty for unmatched/review
  rationale: concise explanation
  evidence: list of citations, each with source (au_title, au_purchase_option, au_row, au_description, rakuten_title, rakuten_axes, rakuten_sku, or rakuten_description), source_ref (row_key or source JSON path where applicable), and a short exact quote

AU row_key format is au:{item_id}:{sku_id}:{row_index}:{column_index}. Copy an exact row_key from the dossier; do not invent one. If important evidence conflicts or cannot establish identity, select review. Do not change split, group, shard, case, or dossier assignments.

split is a frozen dev/test partition for later model evaluation. Every group remains in one split. Annotation shard_id is workload assignment and does not define the evaluation split. The curtains group (ct0/ctk) is fixed to test; the other groups were assigned before labels using stable hashing. Do not revise assignments after labels exist.
"""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"])
        if tag in {"p", "div", "li", "tr", "td", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "li", "tr", "td", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class RakutenItemDescription(HTMLParser):
    """Extract Rakuten's explicit item_desc region, excluding page chrome."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.target_depth = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        depth = len(self.stack) + 1
        classes = (attrs.get("class") or "").split()
        if tag.lower() == "span" and "item_desc" in classes and self.target_depth is None:
            self.target_depth = depth
        if self.target_depth is not None and tag.lower() in {"p", "div", "li", "tr", "td", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag.lower() not in self.VOID:
            self.stack.append(tag.lower())

    def handle_endtag(self, tag):
        tag = tag.lower()
        depth = len(self.stack)
        if self.target_depth is not None and tag in {"p", "div", "li", "tr", "td", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "span" and self.target_depth == depth:
            self.target_depth = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.target_depth is not None:
            self.parts.append(data)


def html_text(value: str, *, limit: int = 7000) -> tuple[str, list[str]]:
    parser = VisibleText()
    parser.feed(value)
    text = "\n".join(" ".join(x.split()) for x in "".join(parser.parts).splitlines() if x.strip())
    return text[:limit], parser.hrefs


_MONEY_AMOUNT = re.compile(
    r"(?:[¥￥]\s*[0-9][0-9,]*(?:\.[0-9]+)?|[0-9][0-9,]*(?:(?:万|億)[0-9,]*)?(?:\.[0-9]+)?\s*円(?![年月日型]))"
)
_COMMERCIAL_CLAUSE = re.compile(
    r"(?:(?:通常|販売|税込|参考)?価格(?:は|が|：|:)?\s*(?:[¥￥]?[0-9][0-9,]*(?:(?:万|億)[0-9,]*)?(?:円)?|(?:お問い合わせ|別途|要相談|ご確認)[^。]*)|"
    r"在庫(?:あり|なし|有|無|切れ|状況)|残り\s*[0-9０-９]+(?:個|点|枚)?|送料無料|送料\s*(?:無料|込み|込|別)|"
    r"クーポン(?:配布|利用|使用|対象|適用)|ポイント(?:還元|進呈|[0-9０-９]+倍))"
)
_SPEC_CUE = re.compile(r"サイズ|寸法|高さ|幅|奥行|長さ|カラー|色|材質|素材|持ち手|付属|内容|仕様|タイプ|型|枚|個|cm|mm", re.I)
_COMMERCIAL_ONLY = (
    ("price_policy", re.compile(r"^(?:(?:通常|販売|税込|参考)?価格)(?:について|は|が|：|:|\s|[0-9¥￥]).*$")),
    ("shipping_policy", re.compile(r"^(?:送料|配送|発送|お届け)(?:について|方法|目安|先|日|無料|料|条件|案内).*$")),
    ("coupon_policy", re.compile(r"^(?:クーポン|ポイント)(?:配布|利用|使用|進呈|還元|キャンペーン).*$")),
    ("availability_notice", re.compile(r"^(?:在庫(?:状況)?|残り\s*[0-9０-９]+(?:個|点|枚)?|売り切れ|品切れ).*$")),
    ("purchase_cta", re.compile(r"^(?:ご注文|ご購入|購入|カート)(?:はこちら|手続き|方法|に進む|に入れる|ください|願います).*$")),
    ("review_or_navigation", re.compile(r"^(?:レビュー|総合評価|商品番号|関連商品|おすすめ商品).*$")),
)


def semantic_line(line: str) -> tuple[str, str | None]:
    """Mask money locally and classify only standalone commercial notices."""
    clean = line.strip()
    for reason, pattern in _COMMERCIAL_ONLY:
        if pattern.search(clean) and not _SPEC_CUE.search(clean):
            return clean, reason
    clean = _COMMERCIAL_CLAUSE.sub("[販売案内]", clean)
    return _MONEY_AMOUNT.sub("[金額]", clean), None


def split_semantic_lines(text: str) -> tuple[list[dict], list[dict]]:
    semantic, commercial = [], []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        cleaned, reason = semantic_line(raw_line)
        entry = {"line": line_number, "text": raw_line, "semantic_text": cleaned}
        (commercial if reason else semantic).append({**entry, **({"reason": reason} if reason else {})})
    return semantic, commercial


def source_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def extract_au_description(raw_path: Path, expected_sha: str) -> dict:
    raw = raw_path.read_bytes()
    if sha256_bytes(raw) != expected_sha:
        raise ValueError(f"AU raw SHA mismatch: {raw_path}")
    record = json.loads(raw)
    info = record.get("itemInfo", {})
    blocks, raw_fields, commercial_only = [], [], []
    linked_items = set()
    for key, scope in (("extraItemComment", "product_page_extra_comment"),
                       ("itemComment", "product_page_comment"),
                       ("detailComment", "product_page_detail_comment")):
        value = info.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        raw_fields.append({"source_field": f"$.itemInfo.{key}", "raw_text": value,
                           "use": "audit_only_not_semantic_input"})
        text, hrefs = html_text(value)
        linked_items.update(re.findall(r"/item/(\d+)", " ".join(hrefs)))
        semantic, commercial = split_semantic_lines(text)
        commercial_only.extend({"source_field": f"$.itemInfo.{key}", **row} for row in commercial)
        for part in semantic:
            chunk = part["semantic_text"]
            likely_series = bool(re.search(r"ラインナップ|シリーズ|選べる|サイズ展開|カラー展開", chunk))
            blocks.append({
                "source_field": f"$.itemInfo.{key}",
                "scope": "series_or_sibling_context" if likely_series else scope,
                "text": chunk[:1800],
                "source_line": part["line"],
            })
            if sum(len(b["text"]) for b in blocks) >= 6500:
                break
    return {
        "source": {"raw_file": str(raw_path.relative_to(ROOT)), "sha256": expected_sha,
                   "json_paths": ["$.itemInfo.extraItemComment", "$.itemInfo.itemComment", "$.itemInfo.detailComment"]},
        "blocks": blocks[:80],
        "raw_fields": raw_fields,
        "commercial_only_lines": commercial_only,
        "linked_au_item_ids": sorted(linked_items),
        "extraction_note": "blocks contain semantic-clean source lines; only monetary amounts are locally masked. Commercial-only lines are separate. raw_fields preserve full source fields for audit only. Series/sibling cues are tagged where explicitly signaled.",
    }


def extract_rakuten_description(raw_path: Path, expected_sha: str) -> dict:
    raw = raw_path.read_bytes()
    if sha256_bytes(raw) != expected_sha:
        raise ValueError(f"Rakuten raw SHA mismatch: {raw_path}")
    head = raw[:2048].decode("ascii", errors="ignore")
    match = re.search(rb"charset\s*=\s*[\"']?([\w.-]+)", raw[:4096], re.I)
    encoding = match.group(1).decode("ascii", errors="ignore") if match else "utf-8"
    try:
        source = raw.decode(encoding, errors="replace")
    except LookupError:
        encoding, source = "utf-8", raw.decode("utf-8", errors="replace")
    parser = RakutenItemDescription()
    parser.feed(source)
    text = "\n".join(" ".join(x.split()) for x in "".join(parser.parts).splitlines() if x.strip())
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    raw_text = "\n".join(lines).strip()
    semantic, commercial = split_semantic_lines(raw_text)
    text = "\n".join(x["semantic_text"] for x in semantic).strip()
    return {
        "source": {"raw_file": str(raw_path.relative_to(ROOT)), "sha256": expected_sha,
                   "page_url": None, "encoding": encoding, "json_path": "Rakuten product page HTML body"},
        "individual_description_excerpt": text[:7000],
        "raw_fields": [{"source_field": "Rakuten item_desc", "raw_text": raw_text,
                        "use": "audit_only_not_semantic_input"}],
        "commercial_only_lines": commercial,
        "masked_money_count": sum("[金額]" in x["semantic_text"] for x in semantic),
        "extraction_note": "Semantic excerpt preserves specification and exception text, masking only numeric monetary amounts. Commercial-only lines are retained separately. Full raw text is audit evidence only.",
    }


def public_au_options(options_raw: dict) -> dict:
    """Keep option specifications; separate only standalone commercial notices."""
    result = {"free_options": [], "paid_options": [], "commercial_only": [], "raw_fields": []}
    for kind in result:
        if kind not in {"free_options", "paid_options"}:
            continue
        for option in options_raw.get(kind, []) or []:
            title = str(option.get("title") or "").strip()
            selections = [str(x.get("title") or "").strip() for x in option.get("freeOptionsList" if kind == "free_options" else "paidOptionsList", []) or []]
            result["raw_fields"].append({"kind": kind, "title": title, "selection_titles": selections,
                                         "use": "audit_only_not_semantic_input"})
            clean_title, title_reason = semantic_line(title)
            clean_selections = []
            for choice in selections:
                cleaned, reason = semantic_line(choice)
                if reason:
                    result["commercial_only"].append({"kind": kind, "text": choice, "reason": reason})
                else:
                    clean_selections.append(cleaned)
            if title_reason:
                result["commercial_only"].append({"kind": kind, "text": title, "reason": title_reason})
            elif clean_title or clean_selections:
                result[kind].append({"title": clean_title, "selection_titles": clean_selections})
    return result


def au_row_key(item_id: str, cell: dict) -> str:
    grain = cell.get("source_grain", {})
    return f"au:{item_id}:{grain.get('sku_id', '')}:{grain.get('row_index', '')}:{grain.get('column_index', '')}"


class UnionFind:
    def __init__(self, values):
        self.parent = {v: v for v in values}

    def find(self, value):
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def build(input_dir: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing annotation output: {output_dir}")
    au_file = input_dir / "au_product_sku_arrays.jsonl"
    rk_file = input_dir / "rakuten_sku_expanded.jsonl"
    au_rows = read_jsonl(au_file)
    rk_rows = read_jsonl(rk_file)
    source_before = {str(p.relative_to(ROOT)): sha256_file(p) for p in (au_file, rk_file)}
    au_by_id = {str(row["au_product_id"]): row for row in au_rows}
    if len(au_by_id) != len(au_rows):
        raise ValueError("Duplicate AU product IDs in source arrays")

    pairs: dict[str, list[dict]] = defaultdict(list)
    for row in rk_rows:
        pairs[row["pair_id"]].append(row)
    if len(pairs) != 29 or len(rk_rows) != 1383:
        raise ValueError(f"Unexpected source dimensions: {len(rk_rows)} cases / {len(pairs)} pairs")
    referenced_raw_paths = sorted({source_path(row["provenance"][key]["file"])
                                    for row in rk_rows for key in ("au_raw_item", "rakuten_raw")})
    raw_before = {str(path.relative_to(ROOT)): sha256_file(path) for path in referenced_raw_paths}

    # Product family and shared Rakuten page form indivisible split components.
    uf = UnionFind(pairs)
    node_pair: dict[tuple[str, str], str] = {}
    pair_manage: dict[str, str] = {}
    for pair_id, records in pairs.items():
        item_id = str(records[0]["au_product_ref"]["item_id"])
        url = records[0]["rakuten_product_ref"]["url"]
        manage_number = str(records[0]["rakuten_product_ref"]["manage_number"]).lower()
        pair_manage[pair_id] = manage_number
        for kind, key in (("au", item_id), ("raku", url), ("family_member", manage_number)):
            node = (kind, key)
            if node in node_pair:
                uf.union(pair_id, node_pair[node])
            else:
                node_pair[node] = pair_id
    for family in RELATED_MANAGE_NUMBERS:
        members = [pair for pair, manage in pair_manage.items() if manage in family]
        for pair in members[1:]:
            uf.union(members[0], pair)
    components: dict[str, list[str]] = defaultdict(list)
    for pair_id in pairs:
        components[uf.find(pair_id)].append(pair_id)
    comp_cases = {root: sum(len(pairs[p]) for p in ps) for root, ps in components.items()}
    assignment: dict[str, str] = {}
    loads = {s: 0 for s in SHARDS}
    for root in sorted(components, key=lambda r: (-comp_cases[r], min(components[r]))):
        shard = min(SHARDS, key=lambda s: (loads[s], s))
        for pair_id in components[root]:
            assignment[pair_id] = shard
        loads[shard] += comp_cases[root]
    component_id = {pair_id: "group-" + hashlib.sha256("\n".join(sorted(components[uf.find(pair_id)])).encode()).hexdigest()[:12]
                    for pair_id in pairs}
    curtains_group_ids = {component_id[pair] for pair, manage in pair_manage.items() if manage in {"ct0", "ctk"}}
    other_groups = sorted({component_id[p] for p in pairs} - curtains_group_ids,
                          key=lambda group: hashlib.sha256(group.encode()).hexdigest())
    test_target = max(3, round(len(other_groups) * 0.25))
    test_groups = curtains_group_ids | set(other_groups[:test_target])
    if len({component_id[p] for p in pairs} - test_groups) < 3:
        raise ValueError("Need at least three dev groups after reserving curtains and stable-hash test groups")
    if len(test_groups) < 3:
        raise ValueError("Need at least three test groups")

    output_dir.mkdir(parents=True)
    (output_dir / "TASK_SPEC.txt").write_text(TASK_SPEC, encoding="utf-8")
    for shard in SHARDS:
        (output_dir / shard).mkdir()
    (output_dir / "dossiers").mkdir()
    case_rows, eligibility_rows = [], []
    au_eligibility = {}
    pair_dossier_paths = {}

    for pair_id, records in sorted(pairs.items()):
        first = records[0]
        item_id = str(first["au_product_ref"]["item_id"])
        au = au_by_id.get(item_id)
        if au is None:
            raise ValueError(f"Missing AU SKU array for {item_id}")
        au_raw_ref = first["provenance"]["au_raw_item"]
        rk_raw_ref = first["provenance"]["rakuten_raw"]
        au_raw_path, rk_raw_path = source_path(au_raw_ref["file"]), source_path(rk_raw_ref["file"])
        au_description = extract_au_description(au_raw_path, au_raw_ref["sha256"])
        rk_description = extract_rakuten_description(rk_raw_path, rk_raw_ref["sha256"])
        au_options = public_au_options(au.get("au_product_options_raw", {}))
        rk_description["source"]["page_url"] = first["rakuten_product_ref"]["url"]
        pair_hash = hashlib.sha256(pair_id.encode()).hexdigest()[:16]
        dossier_id = f"pair-{pair_hash}"
        shard = assignment[pair_id]
        group = component_id[pair_id]
        public_au_rows = []
        au_stock_rows = []
        for cell in au["au_sku_cells_raw"]:
            source_record = cell.get("source_record_raw", {})
            grain = cell.get("source_grain", {})
            row_key = au_row_key(item_id, cell)
            public_au_rows.append({
                "row_key": row_key,
                "axes_raw": cell.get("axes_raw", []),
                "source_grain": {k: grain.get(k) for k in ("file", "line", "row_index", "column_index", "sku_id")},
                "source_record_ref": {"item_id": source_record.get("item_id"), "sku_id": source_record.get("sku_id"),
                                      "platform": source_record.get("platform")},
            })
            au_stock_rows.append({"row_key": row_key, "stock_raw": cell.get("stock_raw", source_record.get("stock"))})
        au_eligibility[item_id] = {"au_product_id": item_id, "au_product_price_jpy_once": au.get("au_product_price_jpy_once"),
                                   "rows": au_stock_rows}
        dossier = {
            "schema_version": "luna-sku-dossier-v3", "dossier_id": dossier_id, "group_id": group,
            "pair_ref": pair_id,
            "au_product": {"product_id": item_id,
                           "title_raw": semantic_line(str(au["au_product_title_raw"]))[0],
                           "title_evidence_raw": {"text": au["au_product_title_raw"],
                                                  "use": "audit_only_not_semantic_input"},
                           "purchase_options": {k: au_options[k] for k in ("free_options", "paid_options")},
                           "purchase_options_commercial_only": au_options["commercial_only"],
                           "purchase_options_raw": {k: au_options[k] for k in ("free_options", "paid_options")},
                           "purchase_options_evidence_raw": au_options["raw_fields"],
                           "source": {"raw_file": au_raw_ref["file"], "sha256": au_raw_ref["sha256"],
                                      "json_path": au_raw_ref.get("json_path", "$.itemInfo")},
                           "description": au_description},
            "au_rows": public_au_rows,
            "rakuten_product": {"title_raw": semantic_line(str(first["raku_title_raw"]))[0],
                                 "title_evidence_raw": {"text": first["raku_title_raw"],
                                                        "use": "audit_only_not_semantic_input"},
                                 "source": {"url": first["rakuten_product_ref"]["url"],
                                            "raw_file": rk_raw_ref["file"], "sha256": rk_raw_ref["sha256"],
                                            "json_path": first["provenance"].get("rakuten_json_path")},
                                 "description": rk_description},
            "annotation_prompt": "判定対象の商品仕様を独立に比較してください。matched / unmatched / review のいずれかを付け、matched の場合だけ対応する au_rows.row_key を返してください。販売状態は同一性の根拠にしないでください。",
        }
        dossier_path = output_dir / "dossiers" / f"{dossier_id}.json"
        dossier_path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pair_dossier_paths[pair_id] = str(dossier_path.relative_to(output_dir))

        for record in records:
            sku = record["rakuten_sku"]
            record_key = sku["sku_record_key"]
            case_id = "case-" + hashlib.sha256(f"{pair_id}\0{record_key}".encode()).hexdigest()[:20]
            case_rows.append({
                "schema_version": "luna-sku-case-v1", "case_id": case_id, "group_id": group,
                "split": "test" if group in test_groups else "dev",
                "shard_id": shard, "dossier_id": dossier_id,
                "rakuten": {"title_raw": semantic_line(str(record["raku_title_raw"]))[0],
                            "option_values": [{"axis_key": o.get("axis_key"), "axis_name": o.get("axis_name"), "value": o.get("value")}
                                              for o in sku.get("option_values", [])],
                            "axes_labels": [{"key": a.get("key"), "label": a.get("label"), "name": a.get("name")}
                                            for a in record.get("raku_axes_raw", [])],
                            "source": {"url": sku.get("source_url"), "raw_file": sku.get("raw_file"),
                                       "sha256": sku.get("sha256"), "json_path": record["provenance"].get("rakuten_json_path"),
                                       "source_sku_jsonl": {"file": record["rakuten_sku_source"].get("file"),
                                                             "line": record["rakuten_sku_source"].get("line")},
                                       "source_row_index": sku.get("source_row_index"),
                                       "sku_record_key": record_key, "source_row_key": sku.get("source_row_key"),
                                       "source_sku_key": sku.get("source_sku_key")}},
            })
            eligibility_rows.append({
                "case_id": case_id,
                "purpose": "availability/price eligibility only; exclude from semantic identity judgment",
                "rakuten": {"price_jpy": sku.get("price_jpy"), "availability": record.get("raku_availability_raw")},
                "au_stock_ref": f"au:{item_id}",
            })

    # One JSONL per shard, and one flat convenience index; each case appears in exactly one shard.
    case_by_id = {row["case_id"]: row for row in case_rows}
    dossier_sha256 = {str(path.relative_to(output_dir)): sha256_file(path)
                      for path in sorted((output_dir / "dossiers").glob("*.json"))}
    dossier_by_id = {path.stem: str(path.relative_to(output_dir))
                     for path in (output_dir / "dossiers").glob("*.json")}
    shard_manifests = {}
    for shard in SHARDS:
        rows = sorted((r for r in case_rows if r["shard_id"] == shard), key=lambda r: r["case_id"])
        payload = "".join(canonical_json(row) + "\n" for row in rows).encode()
        (output_dir / shard / "cases.jsonl").write_bytes(payload)
        shard_manifest = {"shard_id": shard, "case_count": len(rows), "group_count": len({r["group_id"] for r in rows}),
                          "case_ids": [r["case_id"] for r in rows], "cases_sha256": sha256_bytes(payload),
                          "dossiers": {dossier_by_id[d]: dossier_sha256[pair_dossier_paths[pair_id]]
                                       for pair_id, d in ((pid, f"pair-{hashlib.sha256(pid.encode()).hexdigest()[:16]}")
                                                          for pid, recs in pairs.items() if assignment[pid] == shard)}}
        (output_dir / shard / "manifest.json").write_text(json.dumps(shard_manifest, ensure_ascii=False, indent=2) + "\n")
        shard_manifests[shard] = shard_manifest
    cases_payload = "".join(canonical_json(r) + "\n" for r in sorted(case_rows, key=lambda x: x["case_id"])).encode()
    eligibility_payload = "".join(canonical_json(r) + "\n" for r in sorted(eligibility_rows, key=lambda x: x["case_id"])).encode()
    au_eligibility_payload = "".join(canonical_json(v) + "\n" for _, v in sorted(au_eligibility.items())).encode()
    (output_dir / "cases.jsonl").write_bytes(cases_payload)
    (output_dir / "eligibility.jsonl").write_bytes(eligibility_payload)
    (output_dir / "au_eligibility.jsonl").write_bytes(au_eligibility_payload)
    (output_dir / "dossier_index.json").write_text(json.dumps(pair_dossier_paths, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    group_rows = defaultdict(list)
    for pair_id, records in pairs.items():
        group_rows[component_id[pair_id]].append({
            "pair_id": pair_id,
            "manage_number": pair_manage[pair_id],
            "au_product_id": str(records[0]["au_product_ref"]["item_id"]),
            "rakuten_url": records[0]["rakuten_product_ref"]["url"],
            "case_count": len(records),
            "split": "test" if component_id[pair_id] in test_groups else "dev",
            "shard_id": assignment[pair_id],
        })
    group_manifest = {}
    for group_id, members in sorted(group_rows.items()):
        group_manifest[group_id] = {"group_id": group_id, "case_count": sum(m["case_count"] for m in members),
                                    "split": members[0]["split"], "shard_id": members[0]["shard_id"],
                                    "members": sorted(members, key=lambda m: m["pair_id"])}
    (output_dir / "group_manifest.json").write_text(json.dumps(group_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    source_after = {str(p.relative_to(ROOT)): sha256_file(p) for p in (au_file, rk_file)}
    raw_after = {str(path.relative_to(ROOT)): sha256_file(path) for path in referenced_raw_paths}
    if source_before != source_after or raw_before != raw_after:
        raise RuntimeError("Source input changed during build")
    manifest = {
        "schema_version": "luna-sku-annotation-manifest-v3", "created_by": "build_luna_annotation_inputs.py",
        "build_notes": {
            "semantic_description_policy": "Keep product specifications and exceptions; locally mask numeric monetary amounts; classify standalone commercial-only lines separately.",
            "raw_field_policy": "Full AU source description fields, Rakuten item_desc text, and AU option wording are retained as audit-only evidence.",
            "identity_assignments": "Case IDs, group IDs, splits, and shard assignments retain the deterministic v2 construction rules.",
            "synthetic_data_included": False,
        },
        "source_dimensions": {"rakuten_sku_cases": len(rk_rows), "product_pairs": len(pairs),
                               "rakuten_urls": len({r["rakuten_product_ref"]["url"] for r in rk_rows}),
                               "au_products": len({str(r["au_product_ref"]["item_id"]) for r in rk_rows})},
        "case_count": len(case_rows), "dossier_count": len(pair_dossier_paths),
        "group_count": len(components), "shard_case_counts": loads,
        "split_group_counts": {split: len({r["group_id"] for r in case_rows if r["split"] == split}) for split in ("dev", "test")},
        "split_case_counts": {split: sum(r["split"] == split for r in case_rows) for split in ("dev", "test")},
        "split_policy": {"curtains_manage_numbers_fixed_test": ["ct0", "ctk"],
                         "other_groups": "stable SHA-256 order; first ceil-like 25%, minimum 3 groups, assigned test",
                         "families_kept_together": [list(x) for x in RELATED_MANAGE_NUMBERS]},
        "shards": shard_manifests,
        "dossier_sha256": dossier_sha256,
        "source_inputs_pre_post_sha256": {"pre": source_before, "post": source_after, "unchanged": source_before == source_after},
        "raw_source_pre_post_sha256": {"pre": raw_before, "post": raw_after, "unchanged": raw_before == raw_after},
        "output_sha256": {"cases.jsonl": sha256_bytes(cases_payload), "eligibility.jsonl": sha256_bytes(eligibility_payload),
                          "au_eligibility.jsonl": sha256_bytes(au_eligibility_payload)},
        "blindness": {"label_fields_removed": True, "matcher_and_model_fields_removed": True,
                      "price_and_stock_separated": True, "synthetic_data_included": False},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    validate(output_dir)
    return manifest


def validate(output_dir: Path) -> None:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = read_jsonl(output_dir / "cases.jsonl")
    eligibility = read_jsonl(output_dir / "eligibility.jsonl")
    if len(cases) != 1383 or len(eligibility) != len(cases) or len({x["case_id"] for x in cases}) != len(cases):
        raise ValueError("Case/eligibility count or uniqueness check failed")
    if not manifest["source_inputs_pre_post_sha256"]["unchanged"] or not manifest["raw_source_pre_post_sha256"]["unchanged"]:
        raise ValueError("Input source pre/post digest mismatch")
    eligibility_ids = {x["case_id"] for x in eligibility}
    dossier_ids = {p.stem for p in (output_dir / "dossiers").glob("*.json")}
    for case in cases:
        if case["case_id"] not in eligibility_ids or case["dossier_id"] not in dossier_ids:
            raise ValueError("Dangling case reference")
        if any(key in case for key in FORBIDDEN_KEYS):
            raise ValueError("Forbidden label/prediction field in case")
        if contains_commercial_fields(case):
            raise ValueError("Price/stock leaked into annotation case")
    group_splits = defaultdict(set)
    for case in cases:
        group_splits[case["group_id"]].add(case["split"])
    if any(len(splits) != 1 for splits in group_splits.values()):
        raise ValueError("A product family group crosses dev/test")
    if len({g for g, splits in group_splits.items() if "dev" in splits}) < 3 or len({g for g, splits in group_splits.items() if "test" in splits}) < 3:
        raise ValueError("Need at least three groups per split")
    for path in list((output_dir / "dossiers").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        flat = canonical_json(value)
        if any(re.search(rf'"{re.escape(key)}"\s*:', flat) for key in FORBIDDEN_KEYS):
            raise ValueError(f"Forbidden label/prediction field in dossier {path.name}")
        if contains_commercial_fields(value):
            raise ValueError(f"Price/stock leaked into dossier {path.name}")
        if manifest["dossier_sha256"].get(str(path.relative_to(output_dir))) != sha256_file(path):
            raise ValueError(f"Dossier SHA mismatch {path.name}")
    for shard in SHARDS:
        rows = read_jsonl(output_dir / shard / "cases.jsonl")
        sm = json.loads((output_dir / shard / "manifest.json").read_text(encoding="utf-8"))
        if len(rows) != sm["case_count"] or sha256_file(output_dir / shard / "cases.jsonl") != sm["cases_sha256"]:
            raise ValueError(f"Shard manifest mismatch: {shard}")
    all_shard_ids = [row["case_id"] for s in SHARDS for row in read_jsonl(output_dir / s / "cases.jsonl")]
    if sorted(all_shard_ids) != sorted(x["case_id"] for x in cases):
        raise ValueError("Shard membership does not cover each case exactly once")


def contains_commercial_fields(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?:^|_)(?:price|stock|availability)(?:$|_)", str(key), re.I):
                return True
            if contains_commercial_fields(child):
                return True
    elif isinstance(value, list):
        return any(contains_commercial_fields(child) for child in value)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    manifest = build(args.input_dir, args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "case_count": manifest["case_count"],
                      "dossier_count": manifest["dossier_count"], "shard_case_counts": manifest["shard_case_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
