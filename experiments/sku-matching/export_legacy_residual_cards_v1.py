#!/usr/bin/env python3
"""Export raw whole-axis residuals from frozen legacy unknown flags.

This is a development bridge, not model-based first-stage correspondence.
No labels are read and no requirement type selects the exported conditions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def export(input_dir: Path, predictions: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    cases_path = input_dir / "cases.jsonl"
    cases = {x["case_id"]: x for x in map(json.loads, cases_path.open(encoding="utf-8"))}
    cards = {}
    with predictions.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            prediction = json.loads(line)
            case = cases[prediction["case_id"]]
            requirements = {r["requirement_id"]: r for r in prediction["requirements"]}
            axes = {axis["axis_index"]: axis for axis in case["rakuten"]["axes"]}
            for row in prediction["rows"]:
                for result in row.get("atom_results", []):
                    if result["status"] != "unknown":
                        continue
                    requirement = requirements[result["requirement_id"]]
                    index = requirement["axis_index"]
                    axis = axes[index]
                    identity = (case["case_id"], row["row_key"], index)
                    card = cards.setdefault(identity, {
                        "case_id": case["case_id"], "au_row_key": row["row_key"],
                        "condition_id": f"whole-axis:{index}", "axis_name": axis["axis_label"],
                        "selected_value": axis["value"], "option_values": axis["family_values"],
                        "raw_condition": axis,
                        "source_refs": [axis.get("axis_label_span"), axis.get("value_span")],
                        "development_origin": "legacy_unknown_flags_not_model_correspondence",
                        "legacy_prediction_line": line_number, "legacy_unresolved_ids": []})
                    card["legacy_unresolved_ids"].append(result["requirement_id"])
    output_dir.mkdir(parents=True, exist_ok=False)
    path = output_dir / "cards.jsonl"
    with path.open("x", encoding="utf-8") as out:
        for identity in sorted(cards):
            out.write(json.dumps(cards[identity], ensure_ascii=False, separators=(",", ":")) + "\n")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema_version": "legacy_residual_cards_v1", "labels_read": False,
                "development_only": True, "first_stage_modelized": False,
                "no_type_filter": True, "whole_axis_values_preserved": True,
                "card_count": len(cards), "case_count": len({x[0] for x in cards}),
                "source_sha256": {str(cases_path): sha(cases_path), str(predictions): sha(predictions)},
                "code_sha256": sha(Path(__file__)), "output_sha256": sha(path)}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    parser.add_argument("--predictions", type=Path, default=ROOT / ".lab-output/sku-integrated-gate-20261010-v2/predictions.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".lab-output/sku-generic-residual-cards-20261010-v1")
    print(json.dumps(export(**vars(parser.parse_args()))))
