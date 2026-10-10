#!/usr/bin/env python3
"""Build label-free raw-text SKU model inputs from the frozen integrated bundle."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from typing import Any
from functools import lru_cache

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-integrated-inputs-20261010-v1/inputs"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class VisibleText(HTMLParser):
    """HTML text extraction with generic script/style suppression and no content filters."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.iframe_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "iframe":
            self.iframe_urls.extend(value for key, value in attrs if key.lower() == "src" and value)
        if tag.lower() in {"script", "style"}:
            self.hidden += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr", "td", "th", "section", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif tag.lower() in {"p", "div", "li", "tr", "section", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def parse_html(raw: bytes, encoding: str) -> tuple[list[str], list[str]]:
    parser = VisibleText()
    parser.feed(raw.decode(encoding, errors="replace"))
    # Only whitespace-only fragments are discarded; all visible text, including warnings,
    # purchase prompts, navigation, and labels, is retained as derived text.
    lines = [line.strip() for line in "".join(parser.parts).splitlines() if line.strip()]
    return lines, parser.iframe_urls


def html_lines(raw: bytes, encoding: str) -> list[str]:
    return parse_html(raw, encoding)[0]


def source_ref(path: str, sha256: str, locator: str) -> dict[str, str]:
    return {"raw_file": path, "sha256": sha256, "locator": locator}


def au_raw_fields(root: Path, info: dict[str, Any], product: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rel = product["raw_file"]
    path = root / rel
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    expected = product.get("sha256")
    if expected and sha != expected:
        raise ValueError(f"SHA-256 mismatch for {rel}")
    obj = json.loads(raw)
    raw_info = obj.get("itemInfo", {})
    result = []
    iframe_urls: list[str] = []
    # These are source-schema fields. No prefiltered description lines/scopes are read.
    for key in ("itemComment", "extraItemComment", "extraItemCommentWithVideo", "detailComment", "detailLinkName"):
        value = raw_info.get(key)
        if not isinstance(value, str) or not value:
            continue
        lines, urls = parse_html(value.encode("utf-8"), "utf-8")
        iframe_urls.extend(urls)
        for line in lines:
            result.append({"text": line, "text_kind": "derived_visible_html_text",
                           "source_field": f"$.itemInfo.{key}",
                           "source_ref": source_ref(rel, sha, f"$.itemInfo.{key}")})
    return dedupe_text_records(result), list(dict.fromkeys(iframe_urls))


def dedupe_text_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge identical extracted lines while keeping every source field reference."""
    merged: dict[str, dict[str, Any]] = {}
    for row in records:
        text = row["text"]
        if text not in merged:
            merged[text] = {key: value for key, value in row.items() if key not in ("source_ref", "source_field")}
            merged[text]["source_refs"] = []
            merged[text]["source_fields"] = []
        target = merged[text]
        ref = row.get("source_ref")
        field = row.get("source_field")
        if ref and ref not in target["source_refs"]:
            target["source_refs"].append(ref)
        if field and field not in target["source_fields"]:
            target["source_fields"].append(field)
    return list(merged.values())


@lru_cache(maxsize=64)
def verified_json_leaf(raw_path: str, sha256: str, line_no: int, json_path: str) -> str:
    """Resolve and verify a cited JSON/JSONL leaf before retaining it as literal text."""
    path = Path(raw_path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError(f"SHA-256 mismatch for source span {raw_path}")
    lines = raw.splitlines()
    if not 1 <= line_no <= len(lines):
        raise ValueError(f"invalid source line {line_no} in {raw_path}")
    value: Any = json.loads(lines[line_no - 1])
    tokens = re.findall(r"(?:^|\.)([^.\[\]]+)|\[(\d+)\]", json_path[2:])
    for name, index in tokens:
        value = value[int(index)] if index else value[name]
    if not isinstance(value, str):
        raise ValueError("source span does not resolve to a string")
    return value


def valid_purchase_option(root: Path, item: dict[str, Any]) -> bool:
    span = item.get("span") or {}
    quote = span.get("quote")
    text = item.get("text")
    ref = span.get("raw_file")
    locator = span.get("locator") or {}
    start, end = span.get("start"), span.get("end")
    if not isinstance(quote, str) or text != quote or not ref or locator.get("kind") != "jsonl_leaf":
        return False
    if not isinstance(start, int) or not isinstance(end, int) or end - start != len(quote):
        return False
    try:
        leaf = verified_json_leaf(str(root / ref), span["sha256"], int(locator["line"]), locator["json_path"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return leaf[start:end] == quote


def rak_raw_lines(root: Path, product: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rel = product["raw_file"]
    path = root / rel
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if product.get("sha256") and sha != product["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {rel}")
    encoding = product.get("encoding", "utf-8")
    ref = source_ref(rel, sha, "whole_document_visible_text")
    lines, iframe_urls = parse_html(raw, encoding)
    return dedupe_text_records([{"text": line, "text_kind": "derived_visible_html_text", "source_ref": ref}
                                for line in lines]), iframe_urls


def make_product(root: Path, source: dict[str, Any]) -> dict[str, Any]:
    au, rak = source["au_product"], source["rakuten_product"]
    # Preserve complete source arrays and their literal leaf spans as-is.
    rows = source["au_rows"]
    purchase = [{"text": x["text"], "text_kind": "literal_source_value",
                 "source_ref": x.get("span")} for x in source.get("au_purchase_option_lines", [])]
    purchase = [x for x, original in zip(purchase, source.get("au_purchase_option_lines", []))
                if valid_purchase_option(root, original)]
    rak_descriptions, rak_iframes = rak_raw_lines(root, rak)
    au_descriptions, au_iframes = au_raw_fields(root, au, au)
    return {
        "dossier_id": source["dossier_id"],
        "au": {"product_id": au["product_id"], "title": au.get("title"),
               "title_source": au.get("title_span"), "axis_names": au.get("axis_names"),
               "rows": rows, "descriptions": au_descriptions,
               "iframe_urls": au_iframes, "purchase_options": purchase},
        "rakuten": {"url": rak.get("url"), "title": rak.get("title"),
                    "title_source": rak.get("title_span"),
                    "descriptions": rak_descriptions, "iframe_urls": rak_iframes},
    }


def make_case(case: dict[str, Any], cohort: str) -> dict[str, Any]:
    chosen = case["rakuten_selected"]
    return {"case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "group_id": case.get("group_id"), "cohort": cohort,
            "au_product_id": case["au_product_id"],
            "rakuten": {"url": chosen.get("url"), "variant_id": chosen.get("variant_id"),
                        "source_sku_key": chosen.get("source_sku_key"),
                        "sku_record_key": chosen.get("sku_record_key"),
                        "axes": chosen.get("axes", []),
                        "variant_attributes": chosen.get("variant_attributes", [])}}


def cohort_lookup(manifest: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("legacy", "family", "novel"):
        for case_id in manifest.get("bundle_inputs", {}).get(name, {}).get("case_ids", []):
            result[case_id] = name
    return result


def diverse(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin groups for a deterministic, label-free diagnostic subset."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault(str(case.get("group_id", "")), []).append(case)
    out = []
    while len(out) < limit:
        advanced = False
        for items in groups.values():
            if items and len(out) < limit:
                out.append(items.pop(0)); advanced = True
        if not advanced:
            break
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def prepare(input_dir: Path, output_dir: Path, max_cases: int | None = None,
            cohort: str = "all") -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    input_cases = read_jsonl(input_dir / "cases.jsonl")
    input_products = read_jsonl(input_dir / "products.jsonl")
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    cohorts = cohort_lookup(manifest)
    dossier_au: dict[str, str] = {}
    for c in input_cases:
        dossier = str(c["dossier_id"])
        au_id = str(c["au_product_id"])
        if dossier in dossier_au and dossier_au[dossier] != au_id:
            raise ValueError(f"inconsistent AU product ID for dossier {dossier}")
        dossier_au[dossier] = au_id
    raw_hashes = manifest.get("source_raw_sha256", {})
    for product in input_products:
        expected = dossier_au.get(str(product["dossier_id"]))
        if expected is None or str(product["au_product"]["product_id"]) != expected:
            raise ValueError(f"fixed AU product ID mismatch for dossier {product['dossier_id']}")
        for source in (product["au_product"], product["rakuten_product"]):
            if raw_hashes.get(source["raw_file"]) != source.get("sha256"):
                raise ValueError(f"input manifest raw hash mismatch for {source['raw_file']}")
        for option in product.get("au_purchase_option_lines", []):
            span = option.get("span") or {}
            if span.get("raw_file") and raw_hashes.get(span["raw_file"]) not in (None, span.get("sha256")):
                raise ValueError(f"input manifest purchase-option hash mismatch for {span['raw_file']}")
    cases = [c for c in input_cases if cohort == "all" or cohorts.get(c["case_id"]) == cohort]
    case_rows = [make_case(c, cohorts.get(c["case_id"], "unknown")) for c in cases]
    if max_cases is not None:
        if max_cases < 1:
            raise ValueError("--max-cases must be positive")
        case_rows = diverse(case_rows, max_cases)
    included = {c["case_id"] for c in case_rows}
    by_dossier: dict[str, dict[str, Any]] = {}
    for c in input_cases:
        if c["case_id"] in included:
            by_dossier[c["dossier_id"]] = c
    products = [p for p in input_products if p["dossier_id"] in by_dossier]
    product_rows = [make_product(ROOT, p) for p in products]
    output_dir.mkdir(parents=True)
    write_jsonl(output_dir / "cases.jsonl", case_rows)
    write_jsonl(output_dir / "products.jsonl", product_rows)
    code = Path(__file__)
    out = {"schema_version": "generic-model-sku-input-v1", "input_dir": str(input_dir),
           "input_sha256": {n: digest(input_dir / n) for n in ("cases.jsonl", "products.jsonl", "manifest.json")},
           "source_raw_sha256": manifest.get("source_raw_sha256", {}),
           "code_sha256": digest(code), "output_sha256": {n: digest(output_dir / n) for n in ("cases.jsonl", "products.jsonl")},
           "case_count": len(case_rows), "product_count": len(product_rows),
           "cohort_filter": cohort, "max_cases": max_cases,
           "labels_read": False, "synthetic": False,
           "description_provenance": "AU itemInfo.itemComment, extraItemComment, extraItemCommentWithVideo, detailComment, and detailLinkName; Rakuten whole-document visible HTML text; exact duplicate extracted lines merge while retaining all source refs.",
           "external_iframe_content_included": False,
           "external_iframe_urls_retained": True,
           "sku_scope": "one Rakuten selected SKU per case against all fixed AU rows"}
    (output_dir / "manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--cohort", choices=("all", "legacy", "family", "novel"), default="all")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.input_dir, args.output_dir, args.max_cases, args.cohort)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: result[k] for k in ("case_count", "product_count", "cohort_filter", "max_cases", "output_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
