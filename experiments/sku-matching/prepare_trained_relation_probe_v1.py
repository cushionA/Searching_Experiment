#!/usr/bin/env python3
"""Render every original alternative in the documented JEV JNLI format.

No semantic normalization, labels, domain branches, truncation or SKU adoption.
The input is frozen structured-choice requests; all source bindings are retained.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

QUESTION = "前提が正しいとき、仮説との論理的な関係を判定してください。前提から分からない情報を補わないでください。"
CHOICES = [
    {"label": "entailment", "description": "含意：前提から仮説が正しいと必ず言える"},
    {"label": "contradiction", "description": "矛盾：前提から仮説が誤りだと必ず言える"},
    {"label": "neutral", "description": "中立：前提だけでは仮説が正しいとも誤りとも判断できない"},
]
TRAINING_SOURCE = {
    "url": "https://raw.githubusercontent.com/Argos1111/jev_local/4047ac113ebc1446c5cfae06dd7d1de741d04f66/modernbert/data.py",
    "sha256": "01c6df21fb01d7dad9bd477baf2c86a64fd5e3e7d5d6d0fa91da4b7fc558eab8",
    "claim": "documented source format; not independently recovered model training bytes",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(input_dir: Path, output_dir: Path, scope="title", hypothesis_style="axis"):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if scope not in {"title", "all_windows"} or hypothesis_style not in {"axis", "caption"}:
        raise ValueError("unsupported generic task rendering")
    paths = [input_dir / name for name in ("requests.jsonl", "manifest.json")]
    hashes = {p.name: sha(p) for p in paths}
    manifest = json.loads(paths[1].read_text())
    if manifest["output_sha256"]["requests.jsonl"] != hashes["requests.jsonl"]:
        raise ValueError("source request SHA mismatch")
    rows, seen = [], set()
    for line in paths[0].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        source = json.loads(line)
        provenance = source["provenance"]
        if provenance["arm"] != "natural_structure":
            continue
        if scope == "title" and provenance["window"]["field_kind"] != "plain_title":
            continue
        options = provenance["option_values"]
        raw_choices = [c for c in source["choices"] if c["label"] != "unknown"]
        if [c["description"] for c in raw_choices] != options:
            raise ValueError("whole original alternatives or ordering changed")
        if provenance["selected_value"] not in options or provenance["scope_proven"]:
            raise ValueError("invalid raw selector or scope claim")
        for index, value in enumerate(options):
            axis = provenance["axis_name"]
            hypothesis = (f"この商品の「{axis}」は「{value}」です。" if hypothesis_style == "axis"
                          else f"この商品は「{axis}：{value}」の商品です。")
            identity = f"{source['id']}:jnli:{hypothesis_style}:option:{index}"
            if identity in seen:
                raise ValueError("duplicate relation request id")
            seen.add(identity)
            rows.append({"id": identity, "question": QUESTION,
                "state": json.dumps({"前提": source["state"], "仮説": hypothesis}, ensure_ascii=False),
                "choices": deepcopy(CHOICES), "premise": source["state"], "hypothesis": hypothesis,
                "provenance": {**deepcopy(provenance), "source_request_id": source["id"],
                    "candidate_option_index": index, "candidate_option_value": value,
                    "hypothesis_style": hypothesis_style, "scope": scope,
                    "desired_value_injected_into_premise": False}})
    if not rows:
        raise ValueError("empty relation requests")
    if any(sha(p) != hashes[p.name] for p in paths):
        raise RuntimeError("source changed during preparation")
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    result = {"schema_version": "trained-relation-probe-v1", "input_sha256": hashes,
        "input_dir": str(input_dir.resolve()), "code_sha256": sha(Path(__file__)),
        "output_sha256": {"requests.jsonl": sha(output_dir / "requests.jsonl")},
        "training_format_source": TRAINING_SOURCE, "scope": scope,
        "hypothesis_style": hypothesis_style, "request_count": len(rows),
        "task_count": len({r["provenance"]["task_id"] for r in rows}),
        "all_original_alternatives_retained": True, "labels_read": False,
        "production_eligible": False, "scope_proven": False, "text_truncation": False,
        "domain_rules": False, "synthetic_sku_generation": False,
        "inference_run": False, "source_complete_text_retained": True,
        "inference_reads_full_text": scope == "all_windows"}
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=("title", "all_windows"), default="title")
    parser.add_argument("--hypothesis-style", choices=("axis", "caption"), default="axis")
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ("request_count", "task_count", "output_sha256")}))
