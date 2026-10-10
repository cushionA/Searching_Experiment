#!/usr/bin/env python3
"""Generic noun-phrase rendering without condition-type dictionaries.

Use the whole raw axis and option as a compound noun, and render the fixed
AU title and selected row once. The original input and source bindings remain
available; this creates hypotheses, not synthetic SKU records.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(input_dir: Path, output_dir: Path):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text())
    expected = manifest["output_sha256"]["requests.jsonl"]
    if sha(input_dir / "requests.jsonl") != expected:
        raise ValueError("input SHA mismatch")
    requests = []
    for line in (input_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        p = row["provenance"]
        if p["window"]["field_kind"] != "plain_title":
            raise ValueError("this diagnostic requires title-source requests")
        title = p["window"]["quote"]
        axes = p["selected_au_row"]["axes"]
        # No candidate value or Rakuten-selected condition enters this premise.
        premise = f"この商品の商品名は「{title}」です。\n"
        premise += "\n".join(f"選択された{a['axis_name']}は「{a['value']}」です。" for a in axes)
        hypothesis = f"この商品は{p['axis_name']}{p['candidate_option_value']}の商品です。"
        requests.append({**deepcopy(row), "id": row["id"] + ":noun_phrase",
            "state": json.dumps({"前提": premise, "仮説": hypothesis}, ensure_ascii=False),
            "premise": premise, "hypothesis": hypothesis,
            "provenance": {**deepcopy(p), "hypothesis_style": "noun_phrase",
                "original_relation_request_id": row["id"], "single_title_occurrence": True,
                "original_relation_premise": row["premise"]}})
    if sha(input_dir / "requests.jsonl") != expected:
        raise ValueError("input changed while rendering")
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for row in requests:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    result = {"input_dir": str(input_dir.resolve()),
        "input_sha256": {n: sha(input_dir / n) for n in ("requests.jsonl", "manifest.json")},
        "code_sha256": sha(Path(__file__)), "request_count": len(requests),
        "output_sha256": {"requests.jsonl": sha(output_dir / "requests.jsonl")},
        "labels_read": False, "domain_rules": False, "scope": "title_and_row",
        "hypothesis_style": "noun_phrase", "production_eligible": False,
        "synthetic_sku_generation": False, "scope_proven": False}
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ("request_count", "output_sha256")}))
