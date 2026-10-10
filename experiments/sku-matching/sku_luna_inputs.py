"""Load label-free Rakuten→AU SKU inputs and choose a deterministic holdout.

Only the frozen generic-input checkpoint is read. Source quotations are
resolved from raw members in that same ZIP and are accepted only when both the
source digest and decoded slice match the recorded span.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from zipfile import ZipFile


INPUT_ROOT = ".lab-output/sku-generic-model-inputs-20261010-v2/"
DEFAULT_ZIP = Path(__file__).resolve().parent / "results/20261010-sku-generic-presence-checkpoint.zip"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


class _ArchiveSources:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        self.cache: dict[str, bytes] = {}

    def raw(self, rel: str, sha256: str) -> bytes:
        if rel not in self.cache:
            try:
                data = self.archive.read(rel)
            except KeyError as exc:
                raise ValueError(f"missing cited source member: {rel}") from exc
            actual = _digest(data)
            if actual != sha256:
                raise ValueError(f"source hash mismatch: {rel}")
            self.cache[rel] = data
        elif _digest(self.cache[rel]) != sha256:
            raise ValueError(f"conflicting cited hashes: {rel}")
        return self.cache[rel]

    @staticmethod
    def _resolve_json_path(node: Any, path: str) -> Any:
        if not path.startswith("$"):
            raise ValueError("unsupported JSON path")
        tokens = re.findall(r"\.([^\.\[\]]+)|\[(\d+)\]", path[1:])
        if "".join((f".{a}" if a else f"[{b}]") for a, b in tokens) != path[1:]:
            raise ValueError("malformed JSON path")
        for key, index in tokens:
            node = node[key] if key else node[int(index)]
        return node

    def span_text(self, span: dict[str, Any]) -> str:
        rel, sha = span["raw_file"], span["sha256"]
        locator = span["locator"]
        raw = self.raw(rel, sha)
        kind = locator["kind"]
        if kind == "html_text":
            return raw.decode(locator["encoding"])
        if kind == "json_leaf":
            doc = json.loads(raw)
        elif kind == "jsonl_leaf":
            lines = raw.splitlines()
            line = int(locator["line"])
            if not 1 <= line <= len(lines):
                raise ValueError("JSONL source line out of range")
            doc = json.loads(lines[line - 1])
        else:
            raise ValueError(f"unsupported source locator kind: {kind}")
        value = self._resolve_json_path(doc, locator["json_path"])
        if not isinstance(value, str):
            raise ValueError("cited JSON leaf is not text")
        return value

    def verified(self, span: Any, expected_quote: Any = None) -> bool:
        if not isinstance(span, dict):
            return False
        try:
            text = self.span_text(span)
            start, end = span["start"], span["end"]
            quote = span["quote"]
            return (isinstance(start, int) and isinstance(end, int) and
                    0 <= start < end <= len(text) and text[start:end] == quote and
                    (expected_quote is None or expected_quote == quote))
        except (KeyError, TypeError, ValueError, OSError, UnicodeDecodeError,
                json.JSONDecodeError):
            return False


def _provenance(span: Any) -> Any:
    if not isinstance(span, dict):
        return span
    return {k: span[k] for k in ("quote", "raw_file", "sha256", "start", "end", "locator") if k in span}


def _make_cases(archive: ZipFile, case_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cases_bytes = archive.read(INPUT_ROOT + "cases.jsonl")
    products_bytes = archive.read(INPUT_ROOT + "products.jsonl")
    cases_all, products_all = _jsonl(cases_bytes), _jsonl(products_bytes)
    by_case = {row["case_id"]: row for row in cases_all}
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("duplicate requested case IDs")
    missing = [case_id for case_id in case_ids if case_id not in by_case]
    if missing:
        raise ValueError("checkpoint is missing requested case IDs: " + ", ".join(missing))
    by_dossier = {row["dossier_id"]: row for row in products_all}
    sources = _ArchiveSources(archive)
    result: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = by_case[case_id]
        product = by_dossier.get(case["dossier_id"])
        if product is None:
            raise ValueError(f"checkpoint is missing dossier for {case_id}")
        au = product["au"]
        if au.get("product_id") != case.get("au_product_id"):
            raise ValueError(f"fixed AU product mismatch for {case_id}")
        rak_case = case.get("rakuten", {})
        axes = []
        for i, axis in enumerate(rak_case.get("axes", [])):
            span = axis.get("value_span")
            if not sources.verified(span, axis.get("value")):
                raise ValueError(f"unverified selected Rakuten axis span: {case_id}:R{i}")
            axes.append({
                "condition_id": f"R{i}",
                "axis": axis.get("axis_label", axis.get("axis_key", "")),
                "value": axis.get("value"),
                "choices": axis.get("family_values", []),
                "source": _provenance(span),
                "source_verified": True,
            })
        selected_attributes, rejected_attributes = [], []
        for attr in rak_case.get("variant_attributes", []):
            span = attr.get("value_span")
            record = {"axis": attr.get("title", ""), "value": attr.get("value"),
                      "unit": attr.get("unit"), "source": _provenance(span)}
            if sources.verified(span, attr.get("value")):
                selected_attributes.append({**record, "source_verified": True})
            else:
                rejected_attributes.append({**record, "reason": "missing_or_unverified_source_span"})

        au_rows = []
        seen_row_keys: set[str] = set()
        for row in au.get("rows", []):
            row_key = row.get("row_key")
            if not isinstance(row_key, str) or not row_key or row_key in seen_row_keys:
                raise ValueError(f"missing or duplicate AU row key in {case_id}")
            seen_row_keys.add(row_key)
            conditions = []
            for i, axis in enumerate(row.get("axes", [])):
                span = axis.get("value_span")
                if not sources.verified(span, axis.get("value")):
                    raise ValueError(f"unverified fixed AU axis span: {case_id}:{row_key}:{i}")
                conditions.append({
                    "condition_id": f"A:{row_key}:{i}",
                    "axis": axis.get("axis_name", ""),
                    "value": axis.get("value"),
                    "source": _provenance(span),
                    "source_verified": True,
                })
            au_rows.append({"row_key": row_key, "sku_id": row.get("sku_id"),
                            "row_index": row.get("row_index"),
                            "column_index": row.get("column_index"),
                            "conditions": conditions})
        if not axes or not au_rows:
            raise ValueError(f"empty SKU conditions for {case_id}")

        title_span = au.get("title_source")
        if title_span and not sources.verified(title_span, au.get("title")):
            raise ValueError(f"unverified fixed AU title span for {case_id}")
        rak_title_span = product.get("rakuten", {}).get("title_source")
        rak_title = product.get("rakuten", {}).get("title", "")
        if rak_title_span and not sources.verified(rak_title_span, rak_title):
            raise ValueError(f"unverified fixed Rakuten title span for {case_id}")

        au_sources = [{"source_id": "S0", "kind": "title", "text": au.get("title", ""),
                       "source": _provenance(title_span)}]
        au_sources.extend(
            {"source_id": f"S{i + 1}", "kind": "description", "text": desc.get("text", ""),
             "source": [_provenance(s) for s in desc.get("source_refs", [])]}
            for i, desc in enumerate(au.get("descriptions", [])))
        offset = len(au_sources)
        au_sources.extend(
            {"source_id": f"S{offset + i}", "kind": "purchase_option", "text": option.get("text", ""),
             "source": _provenance(option.get("source_ref"))}
            for i, option in enumerate(au.get("purchase_options", [])))

        result.append({
            "case_id": case_id,
            "dossier_id": case["dossier_id"],
            "cohort": case.get("cohort"),
            "au_product_id": au["product_id"],
            "rakuten_url": rak_case.get("url"),
            "rakuten_variant_id": rak_case.get("variant_id"),
            "rakuten_sku_key": rak_case.get("source_sku_key"),
            "rakuten_conditions": axes,
            "selected_attributes": selected_attributes,
            "selected_attributes_rejected": rejected_attributes,
            "rakuten_title": rak_title,
            "title_source": _provenance(rak_title_span),
            "au_rows": au_rows,
            "sources": au_sources,
        })
    hashes = {"zip_sha256": _digest(Path(archive.filename).read_bytes()),
              "cases_jsonl_sha256": _digest(cases_bytes),
              "products_jsonl_sha256": _digest(products_bytes)}
    return result, hashes


def load_cases(zip_path: Path, case_ids: list[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Load selected cases and hash-verified local evidence from the checkpoint."""
    with ZipFile(zip_path) as archive:
        return _make_cases(archive, case_ids)


def choose_holdout(zip_path: Path, development_ids: list[str], count: int = 12
                   ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Choose a deterministic non-family holdout, prioritizing product diversity.

    Cases sharing a development AU product or Rakuten URL are excluded. The
    first selection pass also requires a distinct Rakuten raw document; a
    second pass relaxes only that document constraint if necessary.
    """
    if count < 1:
        raise ValueError("count must be positive")
    with ZipFile(zip_path) as archive:
        cases = _jsonl(archive.read(INPUT_ROOT + "cases.jsonl"))
        products = _jsonl(archive.read(INPUT_ROOT + "products.jsonl"))
        product_by_dossier = {p["dossier_id"]: p for p in products}
        by_id = {c["case_id"]: c for c in cases}
        unknown = [case_id for case_id in development_ids if case_id not in by_id]
        if unknown:
            raise ValueError("unknown development case IDs: " + ", ".join(unknown))
        dev_au = {str(by_id[x]["au_product_id"]) for x in development_ids}
        dev_urls = {str(by_id[x]["rakuten"].get("url", "")) for x in development_ids}
        dev_docs = {axis["value_span"]["raw_file"] for x in development_ids
                    for axis in by_id[x]["rakuten"].get("axes", [])
                    if axis.get("value_span", {}).get("raw_file")}
        eligible = []
        for case in cases:
            rak = case.get("rakuten", {})
            product = product_by_dossier.get(case.get("dossier_id"), {})
            rows = product.get("au", {}).get("rows", [])
            url = str(rak.get("url", ""))
            sku = str(rak.get("source_sku_key", ""))
            if (case.get("cohort") not in {"legacy", "novel"} or
                    str(case.get("au_product_id")) in dev_au or url in dev_urls or
                    not url or not sku or not 2 <= len(rows) <= 15):
                continue
            span = next((x.get("value_span") for x in rak.get("axes", []) if x.get("value_span")), {})
            if span.get("raw_file") in dev_docs:
                continue
            eligible.append({"case": case, "url": url, "sku": sku,
                             "product_id": str(case["au_product_id"]),
                             "dossier_id": str(case["dossier_id"]),
                             "source_document": str(span.get("raw_file", ""))})
        eligible.sort(key=lambda x: hashlib.sha256(x["case"]["case_id"].encode("utf-8")).hexdigest())

        chosen: list[dict[str, Any]] = []
        used_urls, used_products, used_dossiers, used_skus, used_docs = set(), set(), set(), set(), set()
        for require_document_unique in (True, False):
            for item in eligible:
                if len(chosen) >= count:
                    break
                if (item["url"] in used_urls or item["product_id"] in used_products or
                        item["dossier_id"] in used_dossiers or item["sku"] in used_skus or
                        (require_document_unique and item["source_document"] in used_docs)):
                    continue
                chosen.append(item)
                used_urls.add(item["url"])
                used_products.add(item["product_id"])
                used_dossiers.add(item["dossier_id"])
                used_skus.add(item["sku"])
                used_docs.add(item["source_document"])
            if len(chosen) >= count:
                break
        selected_ids = [item["case"]["case_id"] for item in chosen]
        selected, hashes = _make_cases(archive, selected_ids)
    metadata = {
        "selection_rule": "cohort in {legacy,novel}; 2-15 AU rows; exclude development AU product IDs, Rakuten URLs and source documents; unique Rakuten URL, AU product, dossier, source_sku_key; SHA256(case_id) order; prioritize distinct Rakuten source document",
        "requested_count": count,
        "selected_count": len(selected),
        "shortfall": max(0, count - len(selected)),
        "development_case_ids": list(development_ids),
        "excluded_development_au_product_ids": sorted(dev_au),
        "excluded_development_rakuten_urls": sorted(dev_urls),
        "excluded_development_source_documents": sorted(dev_docs),
        "selected_case_ids": selected_ids,
        "selected_au_product_ids": [item["product_id"] for item in chosen],
        "selected_rakuten_urls": [item["url"] for item in chosen],
        "selected_source_documents": [item["source_document"] for item in chosen],
        "selected_source_sku_keys": [item["sku"] for item in chosen],
        "unique_au_products": len(used_products),
        "unique_rakuten_urls": len(used_urls),
        "unique_rakuten_source_documents": len(used_docs),
        "unique_source_sku_keys": len(used_skus),
        "input_hashes": hashes,
    }
    return selected, metadata
