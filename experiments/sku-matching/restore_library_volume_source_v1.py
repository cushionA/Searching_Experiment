#!/usr/bin/env python3
"""Restore pinned label-blind TEST case/product inputs from source archives.

Cases are selected by the frozen group maps and copied byte-for-byte. Product
contexts are rebuilt through build_raw_pair_inputs.pseudo_dossier and the
existing sku_gates.build_product_context; no SKU rules are added here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import types
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".lab-output/sku-library-volume-20261015-v1"
V2_ZIP = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261011-sku-independent-real-eval-v2.zip"
V3_ZIP = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261012-sku-independent-real-eval-v3.zip"
ARRAY_ZIP = ROOT / "experiments/sku-matching/results/20261013-sku-claude-frozen-dev-alignment-inputs.zip"
V2_BASE = ".lab-output/sku-independent-real-20261011/eval-v2/"
V3_BASE = ".lab-output/sku-independent-real-20261011/eval-v3/"
ARRAY_PREFIX = "raw_sources/"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slash(path: str) -> str:
    return path.replace("\\", "/")


def rows(data: bytes):
    return [(payload, json.loads(payload)) for _, payload, _ in raw_rows(data)]


def raw_rows(data: bytes):
    for rawline in data.splitlines(keepends=True):
        payload = rawline.rstrip(b"\r\n")
        if payload.strip():
            yield rawline, payload, json.loads(payload)


def group_map(data: bytes):
    result = {}
    for _, record in rows(data):
        obj = record.get("group", record)
        dossier = str(obj.get("dossier_id", record.get("dossier_id", "")))
        if not dossier or dossier in result:
            raise ValueError("invalid or duplicate frozen dossier split entry")
        result[dossier] = obj
    return result


def archive_sha(path: Path) -> str:
    return sha(path.read_bytes())


def extract_member(z: zipfile.ZipFile, member: str, dest_root: Path, logical: str, expected: str | None = None):
    data = z.read(member)
    actual = sha(data)
    if expected and actual != expected:
        raise ValueError(f"source member SHA mismatch: {member}")
    dest = dest_root / logical
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        existing = dest.read_bytes()
        if sha(existing) != actual:
            raise ValueError(f"existing source capture SHA mismatch: {logical}")
        return actual, len(existing)
    with dest.open("xb") as f:
        f.write(data)
    return actual, len(data)


class _RawTitle(HTMLParser):
    """Retain literal title text from source markup for the existing span locator."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.in_title:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.in_title:
            self.parts.append(f"&#{name};")


def raw_title(raw: bytes, encoding: str) -> str:
    text = raw.decode(encoding, errors="replace")
    parser = _RawTitle()
    parser.feed(text)
    return "".join(parser.parts).strip()


def import_existing_builder():
    """Load the existing dossier/context helpers; stub only the absent CLI wrapper."""
    here = ROOT / "experiments/sku-matching"
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(ROOT))
    runner = types.ModuleType("run_sku_gates")
    runner.ROOT = ROOT
    sys.modules.setdefault("run_sku_gates", runner)
    import claude_v9_gate.sku_gate_sources as sources
    import claude_v9_gate.sku_gates as gates
    sys.modules.setdefault("sku_gate_sources", sources)
    sys.modules.setdefault("sku_gates", gates)
    import build_raw_pair_inputs as pair_builder
    import build_luna_annotation_inputs as annot
    return pair_builder, annot, sources, gates


def build_record(case: dict, pair: dict, title: str):
    selected = case["rakuten_selected"]
    au_ref = dict(pair["au_raw_item"])
    au_ref["file"] = slash(au_ref["file"])
    return {
        "provenance": {"au_raw_item": au_ref},
        "pair_status": case.get("pair_status", "source_rebuild"),
        "au_product_ref": {"item_id": str(case["au_product_id"])},
        "rakuten_sku": {
            "raw_file": slash(selected["raw_file"]), "sha256": selected["sha256"],
            "source_url": selected["url"], "source_sku_key": selected["source_sku_key"],
        },
        "raku_title_raw": title,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=OUT)
    parser.add_argument("--source-name", default="source-test", help="new source directory name; existing outputs are never overwritten")
    parser.add_argument("--source-captures", type=Path, help="reuse an existing targeted raw capture directory")
    parser.add_argument("--verify-dev", action="store_true", help="rebuild all contexts from frozen captures and compare DEV native fields")
    args = parser.parse_args()
    output_root = args.out_root.resolve()
    source_root = args.source_captures.resolve() if args.source_captures else output_root / "source-captures"
    source_test = output_root / args.source_name
    if args.verify_dev:
        if not source_root.is_dir():
            raise FileNotFoundError("verify-dev requires existing frozen source-captures")
    else:
        if source_test.exists():
            raise FileExistsError(f"refusing to overwrite {source_test}")
        if not source_root.exists():
            source_root.mkdir(parents=True)
        (source_test / "inputs").mkdir(parents=True)

    inventory_path = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    version_specs = {
        "v2": {"zip": V2_ZIP, "base": V2_BASE, "cases": "eval-v2/run-token/inputs/cases.jsonl",
               "groups": "eval-v2/groups-token/groups.jsonl", "pairs": "eval-v2/token-pairs-v2/token_pairs.jsonl",
               "arrays": "eval-v2/token-pairs-v2/au_product_sku_arrays.jsonl", "expected_cases": 2494,
               "expected_products": 176, "expected_test_cases": 1199, "expected_test_products": 85},
        "v3": {"zip": V3_ZIP, "base": V3_BASE, "cases": "eval-v3/run-token/inputs/cases.jsonl",
               "groups": "eval-v3/groups-token/groups.jsonl", "pairs": "eval-v3/token-pairs/token_pairs.jsonl",
               "arrays": "eval-v3/token-pairs/au_product_sku_arrays.jsonl", "expected_cases": 168,
               "expected_products": 37, "expected_test_cases": 93, "expected_test_products": 21},
    }
    raw_archives = {}
    version_data = {}
    with zipfile.ZipFile(ARRAY_ZIP) as az:
        for version, spec in version_specs.items():
            with zipfile.ZipFile(spec["zip"]) as z:
                member_prefix = ".lab-output/sku-independent-real-20261011/" + spec["cases"].split("/", 1)[0] + "/"
                cases_member = ".lab-output/sku-independent-real-20261011/" + spec["cases"]
                groups_member = ".lab-output/sku-independent-real-20261011/" + spec["groups"]
                pairs_member = ".lab-output/sku-independent-real-20261011/" + spec["pairs"]
                cases_bytes, groups_bytes, pairs_bytes = z.read(cases_member), z.read(groups_member), z.read(pairs_member)
                cases = list(raw_rows(cases_bytes))
                groups = group_map(groups_bytes)
                pairs = [obj for _, obj in rows(pairs_bytes)]
                pair_index = {str(x["pair_id"]): x for x in pairs}
                if len(pair_index) != len(pairs):
                    raise ValueError(f"duplicate source pair id in {version}")
                if len(cases) != spec["expected_cases"]:
                    raise ValueError(f"unexpected case population in {version}")
                selected, all_products = [], {}
                for line, payload, case in cases:
                    dossier_id = str(case["dossier_id"])
                    binding = groups.get(dossier_id)
                    if not binding or binding.get("excluded") or binding.get("split") not in {"dev", "test"}:
                        raise ValueError(f"case lacks frozen split: {version}/{dossier_id}")
                    pair_id = str(case["source_record"]["pair_id"])
                    if pair_id not in pair_index:
                        raise ValueError(f"case pair lacks source token-pair row: {pair_id}")
                    pair = pair_index[pair_id]
                    if str(case["au_product_id"]) != str(pair.get("au_product_id", case["au_product_id"])):
                        raise ValueError(f"case/source AU product mismatch: {case['case_id']}")
                    product_rec = all_products.setdefault(dossier_id, {"case": case, "pair": pair, "version": version})
                    if (str(product_rec["case"]["au_product_id"]) != str(case["au_product_id"])
                            or product_rec["case"]["source_record"]["pair_id"] != pair_id):
                        raise ValueError(f"inconsistent case family within dossier {dossier_id}")
                    if binding["split"] == "test":
                        selected.append((line, payload, case, version, groups[dossier_id]))
                if len(selected) != spec["expected_test_cases"] or len(all_products) != spec["expected_products"]:
                    raise ValueError(f"case/product counts differ from inventory expectations for {version}")

                # Read only the two option-array members from the pinned alignment archive.
                array_member = ARRAY_PREFIX + ".lab-output/sku-independent-real-20261011/" + spec["arrays"]
                array_bytes = az.read(array_member)
                arrays_rel = ".lab-output/sku-independent-real-20261011/" + spec["arrays"]
                array_sha, _ = extract_member(az, array_member, source_root, arrays_rel)
                array_rows = rows(array_bytes)
                arrays, array_index = {}, {}
                for i, (_, value) in enumerate(array_rows, start=1):
                    pid = str(value["au_product_id"])
                    arrays.setdefault(pid, value)
                    array_index.setdefault(pid, i)
                if len({str(x["case"]["au_product_id"]) for x in all_products.values()} - set(arrays)):
                    raise ValueError(f"native AU option arrays missing for {version}")

                # Extract only AU/Rakuten raw files referenced by these case/pair records.
                needed_raw = {}
                for record in all_products.values():
                    c, pair = record["case"], record["pair"]
                    au_ref = pair["au_raw_item"]
                    rak = c["rakuten_selected"]
                    for ref in (au_ref, {"file": rak["raw_file"], "sha256": rak["sha256"]}):
                        path, digest = slash(str(ref["file"])), str(ref["sha256"])
                        if path in needed_raw and needed_raw[path] != digest:
                            raise ValueError(f"conflicting raw SHA for {path}")
                        needed_raw[path] = digest
                with zipfile.ZipFile(spec["zip"]) as raw_z:
                    raw_names = set(raw_z.namelist())
                    for logical, digest in needed_raw.items():
                        if logical not in raw_names:
                            raise FileNotFoundError(f"source archive lacks referenced raw file: {logical}")
                        got, _ = extract_member(raw_z, logical, source_root, logical, digest)
                        raw_archives[logical] = got

                version_data[version] = {"cases_bytes": cases_bytes, "groups_bytes": groups_bytes,
                    "pairs_bytes": pairs_bytes, "case_rows": cases, "groups": groups,
                    "pair_index": pair_index, "all_products": all_products, "selected": selected,
                    "arrays": arrays, "array_index": array_index, "arrays_rel": arrays_rel,
                    "array_sha": array_sha, "raw_refs": needed_raw,
                    "archive_sha": archive_sha(spec["zip"]),
                    "member_sha": {"cases": sha(cases_bytes), "groups": sha(groups_bytes), "pairs": sha(pairs_bytes)}}

    # Load the existing adapter without its obsolete CLI wrapper module.
    pair_builder, annot, sources, gates = import_existing_builder()
    pair_builder.ROOT = source_root
    annot.ROOT = source_root
    store = sources.RawStore(source_root)

    contexts_by_version, selected_raw_lines, sidecar_rows = {}, {"v2": [], "v3": []}, []
    for version, data in version_data.items():
        contexts = {}
        for dossier_id, row in data["all_products"].items():
            case, pair = row["case"], row["pair"]
            rak_ref = case["rakuten_selected"]
            raw_file = slash(rak_ref["raw_file"])
            raw_page = store.raw(raw_file, rak_ref["sha256"])
            # Encoding is from the existing description extractor; title text is copied literally from HTML.
            desc = annot.extract_rakuten_description(source_root / raw_file, rak_ref["sha256"])
            title = raw_title(raw_page, desc["source"].get("encoding", "utf-8"))
            record = build_record(case, pair, title)
            dossier = pair_builder.pseudo_dossier(str(case["source_record"]["pair_id"]), record, data["arrays"])
            if dossier["dossier_id"] != dossier_id:
                raise ValueError(f"reconstructed dossier id differs from frozen input: {dossier_id}")
            context = gates.build_product_context(store, dossier, None, data["arrays_rel"], data["array_index"])
            contexts[dossier_id] = context
        contexts_by_version[version] = contexts
        for line, payload, case, src_version, binding in data["selected"]:
            selected_raw_lines[src_version].append(line)
            sidecar_rows.append({"case_id": case["case_id"], "source_version": src_version,
                "source_archive": str(version_specs[src_version]["zip"].relative_to(ROOT)),
                "source_case_member": ".lab-output/sku-independent-real-20261011/" + version_specs[src_version]["cases"],
                "source_case_member_sha256": data["member_sha"]["cases"],
                "source_case_line_sha256": sha(line), "source_line_number": next(i for i,(ln,_,_) in enumerate(data["case_rows"],1) if ln==line),
                "frozen_split": binding["split"], "dossier_id": case["dossier_id"],
                "au_product_id": str(case["au_product_id"]), "pair_id": case["source_record"]["pair_id"],
                "au_raw_sha256": data["all_products"][case["dossier_id"]]["pair"]["au_raw_item"]["sha256"],
                "rakuten_raw_sha256": case["rakuten_selected"]["sha256"]})

    # Full all-product hash check uses the original v2/v3 counts, without writing DEV products.
    products_hash_check = {}
    for version, contexts in contexts_by_version.items():
        expected = inventory["splits"][version]["input_members"]["products.jsonl"]["sha256"]
        serialized = "".join(gates.canonical_json(contexts[k]) + "\n" for k in sorted(contexts)).encode("utf-8")
        products_hash_check[version] = {"expected_sha256": expected, "reconstructed_sha256": sha(serialized),
                                        "exact_match": sha(serialized) == expected,
                                        "product_count": len(contexts)}
    if args.verify_dev:
        dev_path = ROOT / ".lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs/products.jsonl"
        dev_rows = [obj for _, obj in rows(dev_path.read_bytes())]
        dev_by_product = {str(x["au_product"]["product_id"]): x for x in dev_rows}
        rebuilt_by_product = {str(x["au_product"]["product_id"]): x for ctx in contexts_by_version.values() for x in ctx.values()}
        def native_projection(row):
            product = row.get("rakuten_product", {})
            return {
                "au_rows": [{"row_key": r.get("row_key"), "axes": [
                    {"axis_name": a.get("axis_name"), "value": a.get("value"), "value_span": a.get("value_span")}
                    for a in r.get("axes", [])]} for r in row.get("au_rows", [])],
                "rakuten_selector_families": [{k: fam.get(k) for k in ("key", "label", "values")}
                    for fam in product.get("selector_families", [])],
                "rakuten_selector_source_fields": [{k: fam.get(k) for k in ("variant_tokens", "source", "label_span", "value_spans", "source_fields") if k in fam}
                    for fam in product.get("selector_families", [])],
                "au_raw_sha256": row.get("au_product", {}).get("sha256"),
                "rakuten_raw_sha256": product.get("sha256"),
            }
        def normalize_paths(value):
            if isinstance(value, list):
                return [normalize_paths(v) for v in value]
            if isinstance(value, dict):
                return {k: (v.replace("\\", "/") if k in {"raw_file", "file"} and isinstance(v, str) else normalize_paths(v)) for k, v in value.items()}
            return value
        def differing_paths(left, right, prefix=""):
            if isinstance(left, dict) and isinstance(right, dict):
                out = []
                for key in sorted(set(left) | set(right)):
                    path = f"{prefix}.{key}" if prefix else key
                    if key not in left or key not in right:
                        out.append(path)
                    else:
                        out.extend(differing_paths(left[key], right[key], path))
                return out
            if isinstance(left, list) and isinstance(right, list):
                if len(left) != len(right):
                    return [prefix + ".length"]
                out = []
                for i, (lv, rv) in enumerate(zip(left, right)):
                    out.extend(differing_paths(lv, rv, f"{prefix}[{i}]"))
                return out
            return [] if left == right else [prefix]
        missing = sorted(set(dev_by_product) - set(rebuilt_by_product))
        comparisons = []
        for product_id in sorted(set(dev_by_product) & set(rebuilt_by_product)):
            old = native_projection(dev_by_product[product_id])
            new = native_projection(rebuilt_by_product[product_id])
            different = differing_paths(old, new)
            path_normalized_different = differing_paths(normalize_paths(old), normalize_paths(new))
            comparisons.append({"au_product_id": product_id,
                "native_fields_match": not different,
                "native_values_match_after_path_normalization": not path_normalized_different,
                "different_field_paths": different,
                "different_field_paths_after_path_normalization": path_normalized_different})
        example_id = "233546355" if "233546355" in dev_by_product and "233546355" in rebuilt_by_product else (comparisons[0]["au_product_id"] if comparisons else None)
        example = None
        if example_id:
            old_ex = native_projection(dev_by_product[example_id])
            new_ex = native_projection(rebuilt_by_product[example_id])
            example = {"au_product_id": example_id, "old": old_ex, "rebuilt": new_ex,
                "path_normalized_old": normalize_paths(old_ex), "path_normalized_rebuilt": normalize_paths(new_ex)}
        result = {
            "schema_version": "sku-library-source-native-equivalence-v1",
            "comparison": "DEV original sku-gate product contexts vs rebuilt contexts from source archive native captures",
            "dev_source_products_path": str(dev_path.relative_to(ROOT)),
            "dev_source_products_sha256": sha(dev_path.read_bytes()),
            "dev_product_count": len(dev_by_product),
            "rebuilt_product_count": len(rebuilt_by_product),
            "matched_product_id_count": len(comparisons), "missing_dev_product_ids": missing,
            "native_fields_compared": ["ordered au_rows.row_key", "ordered au_rows.axes.axis_name/value/value_span exact JSON",
                "ordered Rakuten selector_families key/label/values", "Rakuten variant_tokens/source/span fields separately", "AU/Rakuten raw SHA256"],
            "exact_equivalent_product_count": sum(x["native_fields_match"] for x in comparisons),
            "path_normalized_equivalent_product_count": sum(x["native_values_match_after_path_normalization"] for x in comparisons),
            "different_product_count": sum(not x["native_values_match_after_path_normalization"] for x in comparisons),
            "per_product": comparisons,
            "example_path_format_difference": example,
            "all_213_original_product_sha_check": products_hash_check,
            "labels_read": False, "predictions_read": False, "model_inference_run": False,
            "source_restore_script_sha256": sha(Path(__file__).read_bytes()),
        }
        out = source_test / "SOURCE_NATIVE_EQUIVALENCE.json"
        with out.open("x", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps({k: result[k] for k in ("dev_product_count", "matched_product_id_count",
            "exact_equivalent_product_count", "path_normalized_equivalent_product_count", "different_product_count", "missing_dev_product_ids")}, ensure_ascii=False, indent=2))
        return
    selected_contexts = {}
    for version, data in version_data.items():
        test_dossiers = {case["dossier_id"] for _, _, case, _, binding in data["selected"] if binding["split"] == "test"}
        selected_contexts.update({k: contexts_by_version[version][k] for k in test_dossiers})
    if len(selected_contexts) != 106:
        raise ValueError(f"expected 106 test products, got {len(selected_contexts)}")

    inputs = source_test / "inputs"
    for version in ("v2", "v3"):
        with (inputs / "cases.jsonl").open("ab") as f:
            for rawline in selected_raw_lines[version]:
                f.write(rawline)
    product_bytes = "".join(gates.canonical_json(selected_contexts[k]) + "\n" for k in sorted(selected_contexts)).encode("utf-8")
    with (inputs / "products.jsonl").open("xb") as f:
        f.write(product_bytes)
    with (source_test / "case-source-map.jsonl").open("xb") as f:
        for row in sidecar_rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")

    total_cases = sum(len(v["selected"]) for v in version_data.values())
    manifest = {
        "schema_version": "sku-library-volume-restored-source-v2", "split": "test",
        "case_count": total_cases, "product_count": len(selected_contexts),
        "rows_and_native_bindings": "preserved by original cases and rebuilt product-context au_rows",
        "labels_read": False, "predictions_read": False, "model_inference_run": False,
        "case_records_copied_byte_for_byte": True,
        "source_archives": {v: {"path": str(s["zip"].relative_to(ROOT)), "sha256": version_data[v]["archive_sha"],
            "case_member_sha256": version_data[v]["member_sha"]["cases"],
            "group_member_sha256": version_data[v]["member_sha"]["groups"],
            "token_pair_member_sha256": version_data[v]["member_sha"]["pairs"],
            "arrays_member_sha256": version_data[v]["array_sha"],
            "test_cases": s["expected_test_cases"], "test_products": s["expected_test_products"]}
            for v,s in version_specs.items()},
        "array_archive": {"path": str(ARRAY_ZIP.relative_to(ROOT)), "sha256": archive_sha(ARRAY_ZIP)},
        "product_context_rebuild": "existing pseudo_dossier + sku_gates.build_product_context",
        "full_original_product_file_hash_check": products_hash_check,
        "test_inputs_sha256": {"cases.jsonl": sha((inputs / "cases.jsonl").read_bytes()),
            "products.jsonl": sha(product_bytes)},
        "case_source_map_sha256": sha((source_test / "case-source-map.jsonl").read_bytes()),
        "restore_script_sha256": sha(Path(__file__).read_bytes()),
        "source_capture_file_count": len(raw_archives),
        "source_capture_raw_sha256": raw_archives,
        "arrays_logical_paths": {v: d["arrays_rel"] for v,d in version_data.items()},
        "known_limits": ["The original product-file SHA is compared only after rebuilding all source products; exact mismatch is reported, never silently represented as byte restoration."]
    }
    with (source_test / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"output": str(source_test.relative_to(ROOT)), "cases": total_cases,
        "products": len(selected_contexts), "raw_capture_files": len(raw_archives),
        "full_product_hash_check": products_hash_check,
        "cases_sha256": manifest["test_inputs_sha256"]["cases.jsonl"],
        "products_sha256": manifest["test_inputs_sha256"]["products.jsonl"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
