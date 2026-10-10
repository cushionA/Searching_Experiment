"""Assemble fixed-pair, label-free v9 SKU inputs from the three frozen bundles."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_LEGACY = ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v5/input/legacy"
DEFAULT_FAMILY = ROOT / ".lab-output/sku-claude-v8-followup-20261010-v1/claude-canonical-family/inputs"
DEFAULT_NOVEL = ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v5/input/novel"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-integrated-inputs-20261010-v1"
sys.path.insert(0, str(HERE))
from claude_v9_gate import sku_gate_atoms as atoms  # noqa: E402
from claude_v9_gate import sku_gate_sources as src  # noqa: E402
import build_luna_annotation_inputs as annotation  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(content)


def _raw_description_lines(store: src.RawStore, product: dict, side: str) -> list[dict]:
    """Recover each source-field line, retaining headings and mixed purchase/spec text."""
    if side == "au":
        rel, sha = product["au_product"]["raw_file"], product["au_product"]["sha256"]
        store.raw(rel, sha)
        raw = store.json(rel)
        info = raw.get("itemInfo", {})
        fields = [(f"$.itemInfo.{name}", info.get(name)) for name in
                  ("extraItemComment", "itemComment", "detailComment")]
        lines = []
        for path, value in fields:
            if not isinstance(value, str) or not value.strip():
                continue
            # Existing v9 scoped metadata carries useful classification; retain matching tags
            # where the literal line is already present, while restoring omitted short headings.
            prior = {(x["text"], x.get("source_field") or ((x.get("span") or {}).get("locator", {}).get("json_path"))):
                     x.get("scope_tag", "product_page") for x in product.get("au_description_lines", [])}
            visible_text, _ = annotation.html_text(value)
            for raw_line in visible_text.splitlines():
                text = raw_line.strip()
                if not text:
                    continue
                span = src.json_leaf_span(store, rel, path, text)
                default_scope = ("search_keywords" if path.endswith("itemComment") and not path.endswith("extraItemComment")
                                 else "detail_comment" if path.endswith("detailComment") else "product_page")
                scope = default_scope if default_scope in ("search_keywords", "detail_comment") else prior.get((text, path), default_scope)
                if span:
                    lines.append({"text": text, "span": span,
                                  "scope_tag": scope, "source_field": path,
                                  "citation_status": "verified_span"})
                else:
                    # A line with HTML markup may not be a standalone raw substring; keep it
                    # visible as literal source-field text without fabricating a span.
                    lines.append({"text": text, "span": None,
                                  "scope_tag": scope, "source_field": path,
                                  "citation_status": "unquoted_observation"})
        return lines
    rp = product["rakuten_product"]
    rel, encoding = rp["raw_file"], rp.get("encoding", "euc_jp")
    store.raw(rel, rp["sha256"])
    extracted = annotation.extract_rakuten_description(ROOT / rel, rp["sha256"])
    # Re-read the literal page description. This extractor keeps mixed specification and
    # purchase clauses intact; it is used only to recover source lines, never labels.
    lines = []
    old_scopes = {(x["text"], (x.get("span") or {}).get("locator", {}).get("path")):
                  x.get("scope_tag", "rakuten_item_desc") for x in product.get("rakuten_description_lines", [])}
    for raw_field in extracted["raw_fields"]:
        for text in raw_field["raw_text"].splitlines():
            text = text.strip()
            if not text:
                continue
            span = src.locate_lines_in_text(store, rel, encoding, [text], "item_desc")[0]
            lines.append({"text": text, "span": span,
                          "scope_tag": old_scopes.get((text, span.get("locator", {}).get("path") if span else None),
                                                       "rakuten_item_desc"),
                          "source_field": raw_field.get("source_field", "Rakuten item_desc"),
                          "citation_status": "verified_span" if span else "unquoted_observation"})
    return lines


def _rederive(product: dict) -> None:
    selectors = product["rakuten_product"].get("selector_families", [])
    vocab, families, tokens = [], [], set()
    # AU rows and Rakuten selector values are the complete fixed-pair vocabularies.
    colors = [a["value"] for row in product["au_rows"] for a in row["axes"]
              if any(w in a["axis_name"] for w in ("カラー", "色"))]
    colors += [v for fam in selectors if any(w in (fam.get("label") or fam["key"]) for w in ("カラー", "色"))
               for v in fam["values"]]
    vocab = atoms.color_base_vocab(colors)
    for fam in selectors:
        label = fam.get("label") or fam["key"]
        ft = sorted({a["value"] for v in fam["values"]
                     for a in atoms.atomize(v, label, vocab, tuple(fam["values"]))["atoms"]
                     if a["type"] == "variant"})
        tokens.update(ft)
        families.append({**fam, "variant_tokens": ft})
    product["color_vocab"] = sorted(vocab)
    product["variant_tokens"] = sorted(tokens)
    product["rakuten_product"]["selector_families"] = families


def prepare_bundle(legacy_dir: Path, family_dir: Path, novel_dir: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite output: {output}")
    sources = [("legacy", legacy_dir), ("family", family_dir), ("novel", novel_dir)]
    cases, products, source_hashes, input_audit = [], [], {}, {}
    for name, directory in sources:
        cpath, ppath = directory / "cases.jsonl", directory / "products.jsonl"
        source_hashes[str(cpath.relative_to(ROOT))] = digest(cpath)
        source_hashes[str(ppath.relative_to(ROOT))] = digest(ppath)
        product_map = {p["dossier_id"]: p for p in read_jsonl(ppath)}
        store = src.RawStore(ROOT)
        for product in product_map.values():
            _rederive(product)
            product["au_description_lines"] = _raw_description_lines(store, product, "au")
            product["rakuten_description_lines"] = _raw_description_lines(store, product, "rakuten")
            for field in ("au_product", "rakuten_product"):
                source_hashes[product[field]["raw_file"]] = store.sha(product[field]["raw_file"])
        rows = read_jsonl(cpath)
        input_audit[name] = {"case_count": len(rows), "case_ids": [case["case_id"] for case in rows],
                             "product_count": len(product_map),
                             "dossier_ids": list(product_map)}
        for case in rows:
            if case["dossier_id"] not in product_map:
                raise ValueError(f"Missing product dossier for {case['case_id']}")
        products.extend(product_map.values())
        cases.extend(rows)
    product_map = {p["dossier_id"]: p for p in products}
    if len(product_map) != len(products):
        raise ValueError("Duplicate dossier_id across input bundles")
    out = output / "inputs"
    product_text = "".join(json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(",", ":"))+"\n" for p in products)
    case_text = "".join(json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(",", ":"))+"\n" for c in cases)
    write_new(out / "products.jsonl", product_text)
    write_new(out / "cases.jsonl", case_text)
    store = src.RawStore(ROOT)
    # Verify every attached raw citation; unspanned HTML-derived display lines remain uncited.
    for p in products:
        for key in ("au_description_lines", "rakuten_description_lines"):
            for line in p[key]:
                if line.get("span") and not src.verify_span(store, line["span"]):
                    raise ValueError(f"Invalid source span in dossier {p['dossier_id']}")
    unspanned = {"au": 0, "rakuten": 0}
    verified = {"au": 0, "rakuten": 0}
    for p in products:
        for side, key in (("au", "au_description_lines"), ("rakuten", "rakuten_description_lines")):
            for line in p[key]:
                if line.get("span"):
                    verified[side] += 1
                else:
                    unspanned[side] += 1
    manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "schema_version": "sku-integrated-inputs-v1",
                "task_version": "sku-gate-task-v9", "synthetic": False, "labels_read": False,
                "source_input_sha256": source_hashes, "source_raw_sha256": store._sha,
                "case_count": len(cases), "product_count": len(products),
                "bundle_inputs": input_audit,
                "output_sha256": {"inputs/cases.jsonl": digest(out / "cases.jsonl"),
                                  "inputs/products.jsonl": digest(out / "products.jsonl")},
                "description_lines": {"verified_span_count": verified, "unquoted_observation_count": unspanned,
                                      "unquoted_mode": "observation_only; exporters must not use as quoted evidence"},
                "changed_context_fields": ["color_vocab", "variant_tokens", "rakuten_product.selector_families.variant_tokens",
                                           "au_description_lines", "rakuten_description_lines"],
                "fixed_pair_mapping": True, "sku_scope": "one Rakuten SKU record per case against complete fixed AU SKU array"}
    write_new(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_new(out / "sources-audit.json", json.dumps({"source_sha256": source_hashes,
               "bundle_inputs": input_audit, "description_lines": manifest["description_lines"],
               "verified_span_count": sum(1 for p in products for k in ("au_description_lines", "rakuten_description_lines")
                                           for line in p[k] if line.get("span") and src.verify_span(store, line["span"]))},
               ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-dir", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--family-dir", type=Path, default=DEFAULT_FAMILY)
    parser.add_argument("--novel-dir", type=Path, default=DEFAULT_NOVEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    prepare_bundle(args.legacy_dir, args.family_dir, args.novel_dir, args.output)


if __name__ == "__main__":
    main()
