#!/usr/bin/env python3
"""Connect verified Claude handoff/recovery packages to full fixed-AU evidence.

No inference or adoption. Reverse obligations retain their original source package
and remain separate from the AU evidence tasks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = Path(__file__).with_name("prepare_generic_residual_evidence_v1.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_rows(directory: Path, reverse_name: str) -> tuple[list[dict], list[dict], dict]:
    manifest_path = directory / "manifest.json"
    manifest_digest = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest["output_sha256"]
    for name, digest in files.items():
        path = Path(name)
        if path.is_absolute() or len(path.parts) != 1 or path.name != name:
            raise ValueError("output manifest requires a simple local filename")
        if sha(directory / name) != digest:
            raise ValueError(f"producer output SHA differs: {directory / name}")
    if "cards.jsonl" not in files or reverse_name not in files:
        raise ValueError("producer manifest does not cover forward/reverse files")
    source = {"directory": str(directory.resolve()), "manifest_sha256": manifest_digest,
              "outputs_sha256": files, "code_sha256": manifest["code_sha256"]}
    groups = []
    for name, direction in (("cards.jsonl", "rakuten_to_au"), (reverse_name, "au_to_rakuten")):
        rows = []
        for line_number, line in enumerate((directory / name).read_text(encoding="utf-8").splitlines(), 1):
            card = json.loads(line)
            if card.get("direction") != direction:
                raise ValueError(f"wrong evidence direction: {directory / name}:{line_number}")
            card = copy.deepcopy(card)
            card["producer_package_ref"] = {**source, "file": name, "line": line_number}
            rows.append(card)
        groups.append(rows)
    if sha(manifest_path) != manifest_digest:
        raise ValueError("producer manifest changed during reading")
    return groups[0], groups[1], source


def validate_whole_conditions(forward: list[dict], reverse: list[dict], cases: list[dict], products: list[dict]):
    by_case = {case["case_id"]: case for case in cases}
    by_product = {product["dossier_id"]: product for product in products}
    for card in forward:
        case = by_case[card["case_id"]]
        candidates = [axis for axis in case["rakuten"]["axes"] if axis == card.get("raw_condition")]
        if len(candidates) != 1:
            raise ValueError("forward raw condition is not one complete source Rakuten axis")
        axis = candidates[0]
        expected = {"condition_id": f"claude-whole-axis:{axis['axis_index']}", "axis_name": axis["axis_label"],
                    "selected_value": axis["value"], "option_values": axis["family_values"],
                    "source_refs": [axis.get("axis_label_span"), axis["value_span"]]}
        if any(card.get(key) != value for key, value in expected.items()):
            raise ValueError("forward condition value/options/source differs from complete Rakuten axis")
    for card in reverse:
        case = by_case[card["case_id"]]
        product = by_product[case["dossier_id"]]
        rows = [row for row in product["au"]["rows"] if row["row_key"] == card["au_row_key"]]
        if (len(rows) != 1 or card.get("au_product_id") != case["au_product_id"]
                or card.get("source_sku_key") != case["rakuten"]["source_sku_key"]):
            raise ValueError("reverse condition fixed AU row or Rakuten SKU differs")
        axes = [axis for axis in rows[0]["axes"] if axis == card.get("raw_condition")]
        if len(axes) != 1:
            raise ValueError("reverse condition is not one complete source AU row axis")
        axis = axes[0]
        options = list(dict.fromkeys(a["value"] for row in product["au"]["rows"]
                                    for a in row["axes"] if a["axis_name"] == axis["axis_name"]))
        expected = {"axis_name": axis["axis_name"], "selected_value": axis["value"], "option_values": options,
                    "source_refs": [axis.get("axis_name_span"), axis["value_span"]]}
        if any(card.get(key) != value for key, value in expected.items()):
            raise ValueError("reverse condition value/options/source differs from complete AU axis")


def prepare(handoff_dir: Path, recovery_dir: Path, input_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    forward_a, reverse_a, source_a = package_rows(handoff_dir, "reverse_conditions.jsonl")
    forward_b, reverse_b, source_b = package_rows(recovery_dir, "reverse-conditions.jsonl")
    if {r["case_id"] for r in forward_a + reverse_a} & {r["case_id"] for r in forward_b + reverse_b}:
        raise ValueError("original handoff and omitted-case recovery must have disjoint cases")
    forward, reverse = forward_a + forward_b, reverse_a + reverse_b
    spec = importlib.util.spec_from_file_location("generic_residual_evidence", EVIDENCE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases, products = (module.read_jsonl(input_dir / name) for name in ("cases.jsonl", "products.jsonl"))
    module.validate(cases, products, forward)
    validate_whole_conditions(forward, reverse, cases, products)
    output_dir.mkdir(parents=True, exist_ok=False)
    module.write_jsonl(output_dir / "cards.jsonl", forward)
    module.write_jsonl(output_dir / "reverse_conditions.jsonl", reverse)
    evidence = module.prepare(input_dir, output_dir / "cards.jsonl", output_dir / "evidence")
    # Recheck every producer output after building the derived evidence.
    for source in (source_a, source_b):
        directory = Path(source["directory"])
        if sha(directory / "manifest.json") != source["manifest_sha256"]:
            raise ValueError("producer manifest changed during preparation")
        for name, digest in source["outputs_sha256"].items():
            if sha(directory / name) != digest:
                raise ValueError("producer output changed during preparation")
    result = {"schema_version": "claude-residual-full-au-evidence-v1",
              "producer_packages": [source_a, source_b], "card_count": len(forward),
              "case_count": len({r["case_id"] for r in forward}),
              "reverse_condition_count": len(reverse),
              "forward_counts_by_package": {"original_handoff": len(forward_a), "omitted_case_recovery": len(forward_b)},
              "forward_condition_groups": dict(Counter(r.get("producer_group", r.get("upstream_condition_status")) for r in forward)),
              "inference_run": False, "labels_read": False, "automatic_adoption_allowed": False,
              "reverse_checks_implemented": False, "unknown_final_policy": "drop",
              "upstream_alignment_is_independent_proof": False, "all_au_description_windows_retained": True,
              "code_sha256": sha(Path(__file__)), "evidence_code_sha256": sha(EVIDENCE_PATH),
              "evidence_manifest": evidence,
              "output_sha256": {name: sha(output_dir / name) for name in ("cards.jsonl", "reverse_conditions.jsonl", "evidence/manifest.json")}}
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", type=Path, required=True)
    parser.add_argument("--recovery-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    parser.add_argument("--output-dir", type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({key: result[key] for key in ("card_count", "case_count", "reverse_condition_count", "forward_counts_by_package")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
