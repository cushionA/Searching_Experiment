#!/usr/bin/env python3
"""Audit frozen Luna SKU annotations against blinded, source-grounded cases.

This validates annotation coverage and provenance. It does not turn machine
annotations into human gold and does not score a matcher or model.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = {"matched", "unmatched", "review"}
EVIDENCE_SOURCES = {"au_title", "au_purchase_option", "au_row", "au_description",
                    "rakuten_title", "rakuten_axes", "rakuten_sku", "rakuten_description"}
SYNTHETIC_PARTS = {"sku-synthetic", "sku-fixed-product-20261010"}
COMMERCIAL_TERMS = re.compile(
    r"価格|金額|値段|送料|クーポン|在庫|残り|売切|売り切れ|欠品|購入可|購入不可|"
    r"availability|inventory|\bstock\b|\bprice\b|\b\d[\d,]*\s*円"
    , re.IGNORECASE)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            rows.append(row)
    return rows


def source_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def contains_synthetic_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if str(value.get("record_kind", "")).upper() == "SYNTHETIC":
            return True
        return any(contains_synthetic_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_synthetic_marker(v) for v in value)
    return False


def normalize_quote(value: Any) -> str:
    return " ".join(str(value or "").split())


def case_id_for(pair_id: str, sku_record_key: str) -> str:
    digest = hashlib.sha256(f"{pair_id}\0{sku_record_key}".encode("utf-8")).hexdigest()
    return "case-" + digest[:20]


def evidence_corpus(case: dict[str, Any], dossier: dict[str, Any]) -> dict[str, set[str]]:
    """Build allowed exact-quote corpora for each citation source."""
    au = dossier["au_product"]
    raku = dossier["rakuten_product"]
    out: dict[str, set[str]] = {key: set() for key in EVIDENCE_SOURCES}
    out["au_title"].add(str(au.get("title_raw", "")))
    out["rakuten_title"].add(str(raku.get("title_raw", "")))
    options = au.get("purchase_options_raw", {})
    for kind in ("free_options", "paid_options"):
        for option in options.get(kind, []) or []:
            if option.get("title"):
                out["au_purchase_option"].add(str(option["title"]))
            out["au_purchase_option"].update(str(x) for x in option.get("selection_titles", []) if x)
    for row in dossier.get("au_rows", []):
        pieces = []
        for axis in row.get("axes_raw", []):
            name, value = axis.get("axis_name_raw"), axis.get("value_raw")
            if name is not None:
                pieces.append(str(name))
            if value is not None:
                pieces.append(str(value))
            if name is not None and value is not None:
                pieces.append(f"{name}={value}")
        out["au_row"].update(x for x in pieces if x)
    for block in au.get("description", {}).get("blocks", []):
        if block.get("text"):
            out["au_description"].add(str(block["text"]))
    case_raku = case.get("rakuten", {})
    option_strings = []
    for option in case_raku.get("option_values", []):
        key, value = option.get("axis_key"), option.get("value")
        if key is not None:
            out["rakuten_axes"].add(str(key))
        if value is not None:
            out["rakuten_axes"].add(str(value))
            option_strings.append(f"{key}={value}" if key is not None else str(value))
            out["rakuten_sku"].add(str(value))
        if key is not None and value is not None:
            out["rakuten_sku"].add(f"{key}={value}")
    if option_strings:
        out["rakuten_sku"].add(" / ".join(option_strings))
    for axis in case_raku.get("axes_labels", []):
        for key in ("key", "label", "name"):
            if axis.get(key) is not None:
                out["rakuten_axes"].add(str(axis[key]))
    description = raku.get("description", {})
    excerpt = (description.get("individual_description_excerpt")
               or description.get("visible_page_text_excerpt", ""))
    if excerpt:
        out["rakuten_description"].add(str(excerpt))
        out["rakuten_description"].update(x for x in str(excerpt).splitlines() if x)
    return out


def au_row_evidence_texts(row: dict[str, Any]) -> set[str]:
    pieces: set[str] = set()
    for axis in row.get("axes_raw", []):
        name, value = axis.get("axis_name_raw"), axis.get("value_raw")
        if name is not None:
            pieces.add(str(name))
        if value is not None:
            pieces.add(str(value))
        if name is not None and value is not None:
            pieces.add(f"{name}={value}")
    return pieces


def allowed_source_refs(case: dict[str, Any], dossier: dict[str, Any]) -> dict[str, set[str]]:
    au_src = dossier["au_product"].get("source", {})
    au_desc = dossier["au_product"].get("description", {}).get("source", {})
    rk = case.get("rakuten", {}).get("source", {})
    rk_product = dossier.get("rakuten_product", {}).get("source", {})
    rk_desc = dossier.get("rakuten_product", {}).get("description", {}).get("source", {})
    refs = {key: set() for key in EVIDENCE_SOURCES}
    refs["au_title"].update({str(au_src.get("json_path", "")), "$.itemInfo.itemName", "$.itemInfo.itemTitle"})
    refs["au_purchase_option"].update({"products.jsonl:free_options,paid_options",
                                       "products.jsonl:free_options", "products.jsonl:paid_options"})
    refs["au_row"].update(str(row.get("row_key")) for row in dossier.get("au_rows", []))
    refs["au_description"].update(str(x) for x in au_desc.get("json_paths", []))
    refs["au_description"].update(str(row.get("source_field")) for row in dossier.get("au_product", {}).get("description", {}).get("blocks", []))
    refs["rakuten_title"].update({str(rk_product.get("json_path", "")), str(rk.get("json_path", ""))})
    refs["rakuten_axes"].update({str(rk.get("json_path", "")), str(rk.get("source_row_key", "")), str(rk.get("source_sku_key", ""))})
    refs["rakuten_sku"].update({str(rk.get("json_path", "")), str(rk.get("source_row_key", "")), str(rk.get("source_sku_key", ""))})
    refs["rakuten_description"].update({str(rk_desc.get("json_path", "")), str(rk_desc.get("page_url", ""))})
    return {k: {x for x in v if x} for k, v in refs.items()}


def case_option_texts(case: dict[str, Any], index: int) -> set[str]:
    options = case.get("rakuten", {}).get("option_values", [])
    if index < 0 or index >= len(options):
        return set()
    option = options[index]
    key, value = option.get("axis_key"), option.get("value")
    texts = {str(x) for x in (key, value) if x is not None}
    if key is not None and value is not None:
        texts.add(f"{key}={value}")
    return texts


def citation_source_binding(source: str, source_ref: str, case: dict[str, Any],
                            dossier: dict[str, Any], corpora: dict[str, set[str]]) -> set[str]:
    """Resolve a citation ref to the specific source excerpt/row it names."""
    dossier_id = str(dossier.get("dossier_id", ""))
    prefix = f"dossiers/{dossier_id}.json#"
    if source_ref.startswith(prefix):
        pointer = source_ref[len(prefix):]
        field_map = {
            "au_title": "$.au_product.title_raw",
            "au_purchase_option": ("$.au_product.purchase_options_raw.free_options",
                                   "$.au_product.purchase_options_raw.paid_options"),
            "au_description": "$.au_product.description.blocks",
            "rakuten_title": "$.rakuten_product.title_raw",
            "rakuten_description": "$.rakuten_product.description.individual_description_excerpt",
        }
        expected = field_map.get(source)
        if isinstance(expected, str) and pointer == expected:
            return corpora[source]
        if isinstance(expected, tuple) and pointer in expected:
            return corpora[source]
        return set()
    case_prefix = (f"{case.get('shard_id')}/cases.jsonl[case_id={case.get('case_id')}]."
                   "rakuten.option_values[")
    if source in {"rakuten_axes", "rakuten_sku"} and source_ref.startswith(case_prefix) and source_ref.endswith("]"):
        index_text = source_ref[len(case_prefix):-1]
        if index_text.isdigit():
            return case_option_texts(case, int(index_text))
    return corpora[source] if source_ref in allowed_source_refs(case, dossier).get(source, set()) else set()


def validate_label_records(cases: list[dict[str, Any]], dossiers: dict[str, dict[str, Any]],
                           labels: list[dict[str, Any]], group_manifest: dict[str, Any],
                           manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    case_by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_id = case.get("case_id")
        if not case_id or case_id in case_by_id:
            errors.append({"code": "duplicate_or_missing_case_id", "case_id": case_id})
        else:
            case_by_id[case_id] = case
    labels_by_id: dict[str, dict[str, Any]] = {}
    for label in labels:
        case_id = label.get("case_id")
        if not case_id or case_id in labels_by_id:
            errors.append({"code": "duplicate_or_missing_label_case_id", "case_id": case_id})
        else:
            labels_by_id[case_id] = label
    missing = sorted(set(case_by_id) - set(labels_by_id))
    extra = sorted(set(labels_by_id) - set(case_by_id))
    if missing:
        errors.append({"code": "label_coverage_missing", "count": len(missing), "examples": missing[:10]})
    if extra:
        errors.append({"code": "label_case_not_in_frozen_inputs", "count": len(extra), "examples": extra[:10]})

    decision_counts: Counter[str] = Counter()
    citation_count = 0
    commercial_mentions = []
    raw_sha_expected: dict[str, str] = {}
    url_splits: dict[str, set[str]] = defaultdict(set)
    group_splits: dict[str, set[str]] = defaultdict(set)
    group_ids: dict[str, str] = {}
    pair_splits: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        case_id = str(case.get("case_id"))
        dossier_id = str(case.get("dossier_id", ""))
        dossier = dossiers.get(dossier_id)
        if dossier is None:
            errors.append({"code": "missing_dossier", "case_id": case_id, "dossier_id": dossier_id})
            continue
        pair_id = str(dossier.get("pair_ref", ""))
        rk = case.get("rakuten", {}).get("source", {})
        expected_id = case_id_for(pair_id, str(rk.get("sku_record_key", "")))
        if case_id != expected_id:
            errors.append({"code": "case_identity_key_mismatch", "case_id": case_id, "expected": expected_id})
        if dossier.get("dossier_id") != dossier_id or dossier.get("group_id") != case.get("group_id"):
            errors.append({"code": "case_dossier_reference_mismatch", "case_id": case_id})
        pair_splits[pair_id].add(str(case.get("split")))
        group_splits[str(case.get("group_id"))].add(str(case.get("split")))
        group_ids[pair_id] = str(case.get("group_id"))
        url = str(rk.get("url", ""))
        url_splits[url].add(str(case.get("split")))
        rk_source = dossier.get("rakuten_product", {}).get("source", {})
        for field in ("url", "raw_file", "sha256"):
            case_value = rk.get(field)
            dossier_value = rk_source.get("url" if field == "url" else field)
            if case_value != dossier_value:
                errors.append({"code": "case_rakuten_source_mismatch", "case_id": case_id, "field": field,
                               "case_value": case_value, "dossier_value": dossier_value})
        raw_file, raw_sha = str(rk.get("raw_file", "")), str(rk.get("sha256", ""))
        if raw_file and raw_sha:
            raw_sha_expected[raw_file] = raw_sha
        if contains_synthetic_marker(case) or contains_synthetic_marker(dossier):
            errors.append({"code": "synthetic_record_marker", "case_id": case_id})
        if has_commercial_key(case) or has_commercial_key(dossier):
            errors.append({"code": "commercial_field_leaked_into_blinded_input", "case_id": case_id})
        label = labels_by_id.get(case_id)
        if label is None:
            continue
        if contains_synthetic_marker(label):
            errors.append({"code": "synthetic_label_marker", "case_id": case_id})
        if has_false_gold_claim(label):
            errors.append({"code": "label_claims_human_or_independent_gold", "case_id": case_id})
        decision = label.get("decision")
        decision_counts[str(decision)] += 1
        if not isinstance(decision, str) or decision not in DECISIONS:
            errors.append({"code": "invalid_decision", "case_id": case_id, "decision": decision})
        row_keys = label.get("matching_au_row_keys")
        if not isinstance(row_keys, list):
            errors.append({"code": "missing_matching_au_row_keys_list", "case_id": case_id})
            row_keys = []
        dossier_row_keys = {str(row.get("row_key")) for row in dossier.get("au_rows", [])}
        if decision == "matched":
            if not row_keys:
                errors.append({"code": "matched_without_au_row_key", "case_id": case_id})
            invalid_keys = sorted(set(map(str, row_keys)) - dossier_row_keys)
            if invalid_keys:
                errors.append({"code": "matched_row_key_outside_fixed_au_array", "case_id": case_id,
                               "row_keys": invalid_keys[:10]})
        elif row_keys:
            errors.append({"code": "nonmatched_has_au_row_keys", "case_id": case_id, "decision": decision})
        # Optional redundant identity fields are verified when supplied.
        identity = {"pair_id": pair_id, "dossier_id": dossier_id, "source_url": rk.get("url"),
                    "sku_record_key": rk.get("sku_record_key"), "source_row_index": rk.get("source_row_index"),
                    "raw_sha256": rk.get("sha256")}
        for field, expected in identity.items():
            if field in label and label[field] != expected:
                errors.append({"code": "label_source_identity_mismatch", "case_id": case_id,
                               "field": field, "expected": expected, "actual": label[field]})
        rationale = str(label.get("rationale", ""))
        if COMMERCIAL_TERMS.search(rationale):
            commercial_mentions.append({"case_id": case_id, "where": "rationale"})
        citations = label.get("evidence")
        if not isinstance(citations, list) or not citations:
            errors.append({"code": "missing_evidence_citations", "case_id": case_id})
            continue
        corpora = evidence_corpus(case, dossier)
        refs = allowed_source_refs(case, dossier)
        direct_row_citations: set[str] = set()
        for cite in citations:
            citation_count += 1
            if not isinstance(cite, dict):
                errors.append({"code": "invalid_evidence_citation", "case_id": case_id})
                continue
            source, source_ref, quote = cite.get("source"), str(cite.get("source_ref", "")), str(cite.get("quote", ""))
            if source not in EVIDENCE_SOURCES:
                errors.append({"code": "unallowed_evidence_source", "case_id": case_id, "source": source})
                continue
            eligible_texts = citation_source_binding(source, source_ref, case, dossier, corpora)
            if not eligible_texts:
                errors.append({"code": "evidence_source_ref_not_in_case_dossier", "case_id": case_id,
                               "source": source, "source_ref": source_ref})
            if source == "au_row":
                source_row = next((r for r in dossier.get("au_rows", [])
                                   if str(r.get("row_key")) == source_ref), None)
                eligible_texts = au_row_evidence_texts(source_row) if source_row else set()
                if source_row is not None:
                    direct_row_citations.add(source_ref)
            q = normalize_quote(quote)
            if not q or not any(q in normalize_quote(text) for text in eligible_texts):
                errors.append({"code": "evidence_quote_not_in_dossier_source", "case_id": case_id,
                               "source": source, "quote": quote[:160]})
        if decision == "matched":
            uncited = sorted(set(map(str, row_keys)) - direct_row_citations)
            if uncited:
                warnings.append({"code": "matched_row_key_without_direct_row_citation",
                                 "case_id": case_id, "row_keys": uncited[:10]})
    for group, splits in group_splits.items():
        if len(splits) > 1:
            errors.append({"code": "group_split_leakage", "group_id": group, "splits": sorted(splits)})
    for url, splits in url_splits.items():
        if url and len(splits) > 1:
            errors.append({"code": "rakuten_url_split_leakage", "url": url, "splits": sorted(splits)})
    for pair, splits in pair_splits.items():
        if len(splits) > 1:
            errors.append({"code": "pair_split_leakage", "pair_id": pair, "splits": sorted(splits)})
    policy_families = manifest.get("split_policy", {}).get("families_kept_together", [])
    pair_manage = {str(m.get("pair_id")): str(m.get("manage_number", "")).lower()
                   for entry in group_manifest.values() for m in entry.get("members", [])}
    for family in policy_families:
        family_splits = set()
        for pair, manage in pair_manage.items():
            if manage in {str(x).lower() for x in family}:
                family_splits.update(pair_splits.get(pair, set()))
        if len(family_splits) > 1:
            errors.append({"code": "known_family_split_leakage", "family": family, "splits": sorted(family_splits)})
    if commercial_mentions:
        warnings.append({"code": "commercial_language_in_label_basis", "count": len(commercial_mentions),
                         "examples": commercial_mentions[:10],
                         "interpretation": "Inspect these annotations; price/stock must not determine semantic identity."})

    # A valid input package is explicitly machine-annotation input, not synthetic data.
    if manifest.get("blindness", {}).get("synthetic_data_included") is not False:
        errors.append({"code": "input_manifest_does_not_attest_real_only"})
    if manifest.get("blindness", {}).get("matcher_and_model_fields_removed") is not True:
        errors.append({"code": "input_manifest_does_not_attest_blinding"})
    return {"errors": errors, "warnings": warnings,
            "case_count": len(cases), "label_count": len(labels),
            "decision_counts": dict(decision_counts), "missing_case_count": len(missing),
            "extra_label_count": len(extra), "citation_count": citation_count,
            "commercial_basis_mentions": len(commercial_mentions),
            "raw_sha_references": raw_sha_expected}


def has_commercial_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?:^|_)(?:price|stock|availability)(?:$|_)", str(key), re.I):
                return True
            if has_commercial_key(child):
                return True
    elif isinstance(value, list):
        return any(has_commercial_key(x) for x in value)
    return False


def has_false_gold_claim(value: Any) -> bool:
    """Reject annotation payloads that label themselves as human/external gold."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = str(key).casefold()
            if key_norm in {"human_gold", "independent_external_gold", "is_gold", "human_reviewed"} and child is True:
                return True
            if key_norm == "record_kind" and str(child).upper() in {"HUMAN_GOLD", "INDEPENDENT_GOLD", "GOLD_LABELS"}:
                return True
            if has_false_gold_claim(child):
                return True
    elif isinstance(value, list):
        return any(has_false_gold_claim(x) for x in value)
    return False


def audit(input_dir: Path, label_files: Iterable[Path], output_dir: Path,
          semantic_review_path: Path | None = None) -> dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    if any(part.lower() in SYNTHETIC_PARTS or part.lower().startswith("sku-synthetic")
           for part in input_dir.parts):
        raise ValueError("refusing synthetic annotation inputs")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite audit output: {output_dir}")
    manifest_path = input_dir / "manifest.json"
    manifest = read_json(manifest_path)
    cases = read_jsonl(input_dir / "cases.jsonl")
    dossier_index = read_json(input_dir / "dossier_index.json")
    group_manifest = read_json(input_dir / "group_manifest.json")
    dossiers: dict[str, dict[str, Any]] = {}
    for pair_id, relative in dossier_index.items():
        dossier = read_json(input_dir / relative)
        if dossier.get("pair_ref") != pair_id:
            raise ValueError(f"Dossier index pair mismatch: {pair_id}")
        dossiers[str(dossier.get("dossier_id"))] = dossier
    labels: list[dict[str, Any]] = []
    source_snapshot = manifest.get("source_inputs_pre_post_sha256", {})
    if source_snapshot.get("unchanged") is not True or source_snapshot.get("pre") != source_snapshot.get("post"):
        raise ValueError("Source table snapshot changed while annotation inputs were prepared")
    for source_file, expected_sha in source_snapshot.get("pre", {}).items():
        path = source_path(source_file)
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ValueError(f"Frozen source table hash mismatch: {source_file}")
    shard_case_ids = []
    shard_input_paths = []
    for shard, shard_manifest in manifest.get("shards", {}).items():
        shard_file = input_dir / shard / "cases.jsonl"
        shard_manifest_file = input_dir / shard / "manifest.json"
        if not shard_file.is_file() or sha256_file(shard_file) != shard_manifest.get("cases_sha256"):
            raise ValueError(f"Frozen case shard hash mismatch: {shard}")
        shard_rows = read_jsonl(shard_file)
        shard_cases = read_json(shard_manifest_file)
        if ([r.get("case_id") for r in shard_rows] != shard_cases.get("case_ids")
                or len(shard_rows) != shard_manifest.get("case_count")):
            raise ValueError(f"Case shard membership mismatch: {shard}")
        if shard_cases.get("cases_sha256") != shard_manifest.get("cases_sha256"):
            raise ValueError(f"Shard manifest disagrees with root manifest: {shard}")
        if any(r.get("shard_id") != shard for r in shard_rows):
            raise ValueError(f"Case has wrong shard assignment: {shard}")
        shard_case_ids.extend(str(r.get("case_id")) for r in shard_rows)
        shard_input_paths.extend([shard_file, shard_manifest_file])
    if sorted(shard_case_ids) != sorted(str(r.get("case_id")) for r in cases):
        raise ValueError("Input shard coverage does not match frozen cases.jsonl exactly")

    input_paths = [manifest_path, input_dir / "cases.jsonl", input_dir / "dossier_index.json",
                   input_dir / "group_manifest.json", input_dir / "eligibility.jsonl",
                   input_dir / "au_eligibility.jsonl", *sorted((input_dir / "dossiers").glob("*.json")),
                   *shard_input_paths]
    input_hashes = {str(p.relative_to(input_dir)): sha256_file(p) for p in input_paths}
    expected_output_hashes = manifest.get("output_sha256", {})
    for filename, expected_sha in expected_output_hashes.items():
        path = input_dir / filename
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ValueError(f"Frozen annotation input hash mismatch: {filename}")
    expected_dossier_hashes = manifest.get("dossier_sha256", {})
    for relative, expected_sha in expected_dossier_hashes.items():
        path = input_dir / relative
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ValueError(f"Frozen dossier hash mismatch: {relative}")
    label_hashes = {}
    for path in label_files:
        path = path.resolve()
        if any(part.lower() in SYNTHETIC_PARTS or part.lower().startswith("sku-synthetic") for part in path.parts):
            raise ValueError(f"refusing synthetic label path: {path}")
        label_hashes[str(path)] = sha256_file(path)
        labels.extend(read_jsonl(path))
    result = validate_label_records(cases, dossiers, labels, group_manifest, manifest)
    semantic_review: dict[str, Any] = {
        "status": "not_performed", "reviewer_kind": "none",
        "checked_case_count": 0, "checked_product_pair_count": 0,
        "checked_family_count": 0, "findings": [],
    }
    semantic_review_hash = None
    if semantic_review_path is not None:
        semantic_review_path = semantic_review_path.resolve()
        if any(part.lower() in SYNTHETIC_PARTS or part.lower().startswith("sku-synthetic")
               for part in semantic_review_path.parts):
            raise ValueError(f"refusing synthetic semantic review path: {semantic_review_path}")
        semantic_review = read_json(semantic_review_path)
        semantic_review_hash = sha256_file(semantic_review_path)
        pair_refs = {str(d.get("pair_ref")) for d in dossiers.values()}
        reviewed_pairs = set(map(str, semantic_review.get("reviewed_pair_refs", [])))
        if reviewed_pairs != pair_refs:
            result["errors"].append({
                "code": "semantic_review_pair_coverage_mismatch",
                "missing_count": len(pair_refs - reviewed_pairs),
                "extra_count": len(reviewed_pairs - pair_refs),
            })
        family_urls = {str(c.get("rakuten", {}).get("source", {}).get("url", ""))
                       for c in cases}
        reviewed_urls = set(map(str, semantic_review.get("reviewed_family_urls", [])))
        if reviewed_urls != family_urls:
            result["errors"].append({
                "code": "semantic_review_url_coverage_mismatch",
                "missing_count": len(family_urls - reviewed_urls),
                "extra_count": len(reviewed_urls - family_urls),
            })
        group_ids = {str(c.get("group_id")) for c in cases}
        reviewed_groups = set(map(str, semantic_review.get("reviewed_group_ids", [])))
        if reviewed_groups != group_ids:
            result["errors"].append({
                "code": "semantic_review_group_coverage_mismatch",
                "missing_count": len(group_ids - reviewed_groups),
                "extra_count": len(reviewed_groups - group_ids),
            })
        reviewed_case_ids = set(map(str, semantic_review.get("reviewed_case_ids", [])))
        case_ids = {str(c.get("case_id")) for c in cases}
        if not reviewed_case_ids <= case_ids:
            result["errors"].append({"code": "semantic_review_unknown_case_ids",
                                     "count": len(reviewed_case_ids - case_ids)})
        if semantic_review.get("status") != "completed" or not semantic_review.get("findings"):
            result["errors"].append({"code": "semantic_review_record_incomplete"})
        if semantic_review.get("human_gold") is True or semantic_review.get("human_reviewed") is True:
            result["errors"].append({"code": "semantic_review_false_human_claim"})
        semantic_review["checked_case_count"] = len(reviewed_case_ids)
        semantic_review["checked_product_pair_count"] = len(reviewed_pairs)
        semantic_review["checked_family_count"] = len(reviewed_groups)
        semantic_review["checked_rakuten_url_count"] = len(reviewed_urls)
    # Verify each case's exact Rakuten SKU row against its referenced raw page.
    from fetch_rakuten import parse_page
    parsed_pages: dict[tuple[str, str], list[dict[str, Any]]] = {}
    raw_case_errors = []
    for case in cases:
        rk = case.get("rakuten", {}).get("source", {})
        url, raw_file, raw_sha = str(rk.get("url", "")), str(rk.get("raw_file", "")), str(rk.get("sha256", ""))
        path = source_path(raw_file)
        key = (url, raw_sha)
        if key not in parsed_pages:
            try:
                raw = path.read_bytes()
                if sha256_bytes(raw) != raw_sha:
                    raise ValueError("raw SHA256 mismatch")
                page, sku_rows = parse_page({"url": url, "requested_url": url, "retrieved_at_utc": "",
                                             "status": 200, "raw_file": raw_file, "content_type": ""}, raw)
                if not page.get("structured_item_data"):
                    raise ValueError("raw page has no structured SKU data")
                parsed_pages[key] = sku_rows
            except (OSError, ValueError, TypeError) as exc:
                parsed_pages[key] = []
                raw_case_errors.append({"code": "raw_case_page_unreadable", "url": url,
                                        "raw_file": raw_file, "error": str(exc)})
        sku_rows = parsed_pages[key]
        idx = rk.get("source_row_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(sku_rows):
            raw_case_errors.append({"code": "raw_case_row_index_invalid", "case_id": case.get("case_id"),
                                    "source_row_index": idx, "raw_row_count": len(sku_rows)})
            continue
        row = sku_rows[idx]
        expected_record_key = hashlib.sha256(f"{url}\n{raw_sha}\n{idx}".encode("utf-8")).hexdigest()
        expected_row_key = f"{url}#sku-row-{idx}-{expected_record_key[:16]}"
        sid = row.get("variant_id") or row.get("merchant_defined_sku_id") or f"row-{idx}"
        expected_sku_key = f"{url}#{sid}:{idx}"
        checks = {"sku_record_key": (rk.get("sku_record_key"), expected_record_key),
                  "source_row_key": (rk.get("source_row_key"), expected_row_key),
                  "source_sku_key": (rk.get("source_sku_key"), expected_sku_key),
                  "option_values": (case.get("rakuten", {}).get("option_values"), row.get("option_values"))}
        for field, (actual, expected) in checks.items():
            if actual != expected:
                raw_case_errors.append({"code": "raw_case_source_identity_mismatch",
                                        "case_id": case.get("case_id"), "field": field,
                                        "actual": actual, "expected": expected})
    result["errors"].extend(raw_case_errors)
    result["raw_sku_source_rows_checked"] = sum(1 for c in cases if c.get("rakuten", {}).get("source", {}).get("url") and c.get("rakuten", {}).get("source", {}).get("sha256"))
    raw_integrity = {"checked": 0, "missing": [], "sha256_mismatch": []}
    frozen_raw = manifest.get("raw_source_pre_post_sha256", {})
    if frozen_raw.get("unchanged") is not True or frozen_raw.get("pre") != frozen_raw.get("post"):
        result["errors"].append({"code": "raw_source_manifest_not_immutable"})
    case_raw_refs = result.pop("raw_sha_references")
    frozen_pre = frozen_raw.get("pre") or case_raw_refs
    for raw_file, expected_sha in case_raw_refs.items():
        if frozen_pre.get(raw_file) != expected_sha:
            result["errors"].append({"code": "case_raw_reference_not_in_frozen_manifest",
                                     "raw_file": raw_file, "case_sha256": expected_sha,
                                     "manifest_sha256": frozen_pre.get(raw_file)})
    for raw_file, expected_sha in frozen_pre.items():
        path = source_path(raw_file)
        if not path.is_file():
            raw_integrity["missing"].append(raw_file)
        elif sha256_file(path) != expected_sha:
            raw_integrity["sha256_mismatch"].append(raw_file)
        else:
            raw_integrity["checked"] += 1
    if raw_integrity["missing"] or raw_integrity["sha256_mismatch"]:
        result["errors"].append({"code": "raw_source_integrity_failure", **raw_integrity})
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "record_kind": "LUNA_MACHINE_ANNOTATION_AUDIT",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "annotation_status": "machine_labeled_not_human_gold",
        "human_gold": False, "independent_external_gold": False,
        "human_reviewed": False,
        "independent_second_review_checked_counts": {
            "cases": semantic_review["checked_case_count"],
            "product_pairs": semantic_review["checked_product_pair_count"],
            "families": semantic_review["checked_family_count"],
        },
        "independent_second_review": semantic_review,
        "independent_second_review_sha256": semantic_review_hash,
        "annotation_operational_history": semantic_review.get("operational_history", []),
        "input_directory": str(input_dir.relative_to(ROOT) if input_dir.is_relative_to(ROOT) else input_dir),
        "input_manifest_sha256": sha256_file(manifest_path), "input_files_sha256": input_hashes,
        "label_files_sha256": label_hashes, "raw_integrity": raw_integrity,
        **result,
    }
    report_path = output_dir / "audit.json"
    with report_path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if report["errors"]:
        raise SystemExit(f"Luna annotation audit failed with {len(report['errors'])} errors; see {report_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--label-file", type=Path, action="append", required=True,
                        help="Repeat for all independent annotation shard files")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--semantic-review-json", type=Path,
                        help="Source-only independent second-review record covering all frozen pairs/families")
    args = parser.parse_args(argv)
    result = audit(args.input_dir, args.label_file, args.output_dir, args.semantic_review_json)
    print(json.dumps({k: result[k] for k in ("annotation_status", "case_count", "label_count",
                                            "decision_counts", "errors", "warnings", "raw_integrity")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
