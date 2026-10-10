#!/usr/bin/env python3
"""NLI on every literal source window, with generic whole-axis hypotheses.

This isolates sentence relation from title boilerplate and evidence ownership.
Ownership remains unproved: the output cannot adopt an AU SKU.
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
    requests, empty_quotes = [], []
    for line in (input_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        p = row["provenance"]
        quote = p["window"]["quote"]
        if not quote.strip():
            empty_quotes.append({**deepcopy(row), "not_inferred_reason": "literal_quote_contains_only_whitespace",
                                 "relation": "unknown", "scope_proven": False})
            continue
        hypothesis = f"{p['axis_name']}は{p['candidate_option_value']}です。"
        requests.append({**deepcopy(row), "id": row["id"] + ":quote_axis",
            "state": json.dumps({"前提": quote, "仮説": hypothesis}, ensure_ascii=False),
            "premise": quote, "hypothesis": hypothesis,
            "provenance": {**deepcopy(p), "hypothesis_style": "quote_axis",
                "original_relation_request_id": row["id"], "original_relation_premise": row["premise"],
                "relation_task_scope": "quoted_text_only_not_fixed_row_applicability",
                "scope_proven": False}})
    if sha(input_dir / "requests.jsonl") != expected:
        raise ValueError("input changed while rendering")
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for row in requests:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    with (output_dir / "empty-quote-requests.jsonl").open("x", encoding="utf-8") as stream:
        for row in empty_quotes:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    result = {"input_dir": str(input_dir.resolve()),
        "input_sha256": {n: sha(input_dir / n) for n in ("requests.jsonl", "manifest.json")},
        "code_sha256": sha(Path(__file__)), "request_count": len(requests),
        "output_sha256": {name: sha(output_dir / name) for name in ("requests.jsonl", "empty-quote-requests.jsonl")},
        "empty_literal_quote_request_count": len(empty_quotes),
        "empty_literal_quotes_retained_and_not_inferred": True,
        "labels_read": False, "domain_rules": False, "scope": "all_literal_windows",
        "hypothesis_style": "quote_axis", "production_eligible": False,
        "synthetic_sku_generation": False, "scope_proven": False,
        "evidence_ownership_removed_for_relation_diagnostic": True}
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ("request_count", "output_sha256")}))
