#!/usr/bin/env python3
"""Freeze a compact, deterministic replay of previously misclassified real SKUs.

Selection uses the earlier scored error list, not a new test set. Original
requests, options, rows and case plans are unchanged. Model text contains no
desired value singled out and no labels. HTML structure and source spans are
retained separately from literal citations into the existing visible document.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from build_scoped_html_blocks_v1 import extract_blocks

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAMES = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl")
FIELDS = ("itemComment", "extraItemComment", "extraItemCommentWithVideo")
MAX_PER_GROUP = 3


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, values):
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def choose_errors(diagnostics):
    errors = diagnostics["variants"]["fusion-predictions"]["errors"]
    groups = defaultdict(list)
    seen = set()
    for error in errors:
        if error["cell"] not in {"fp", "fn"} or error["case_id"] in seen:
            raise ValueError("prior diagnostic must contain unique false accepts/misses")
        seen.add(error["case_id"])
        groups[(error["au_product_id"], error["cell"])].append(error)
    selected = [e for key in sorted(groups) for e in sorted(groups[key], key=lambda e: e["case_id"])[:MAX_PER_GROUP]]
    return selected, {"group_fields": ["au_product_id", "cell"], "order": "case_id ascending",
                      "max_per_group": MAX_PER_GROUP, "population_error_count": len(errors),
                      "population_error_counts": dict(Counter(e["cell"] for e in errors)),
                      "selection_uses_prior_scored_errors": True, "new_model_results_used": False}


def context_snippets(block):
    context = block.get("context", {})
    table = context.get("table", {})
    return (context.get("headings", []) + table.get("captions", []) +
            table.get("preceding_header_rows", []) + table.get("inherited_rowspan_cells", []))


def render_passage(block):
    # Legacy blocks have no safe anchor-filtered view; never render them.
    if block.get("schema_version", "").startswith("scoped-html-blocks-v1") or "support_text" not in block:
        raise ValueError("block must be rebuilt with anchor-excluded support fields")
    # Labels below describe DOM structure; only anchor-excluded snippets are evidence.
    parts = ["説明フィールド：" + block["source_ref"]["locator"]["json_path"], "HTML構造：" + block["kind"]]
    for snippet in context_snippets(block):
        if "support_text" not in snippet:
            raise ValueError("context snippet must be rebuilt with anchor-excluded support fields")
        if snippet["support_text"]:
            parts.append("表・節の見出し：\n" + snippet["support_text"])
    parts.append("このブロックの本文：\n" + block["support_text"])
    return "\n".join(parts)


def literal_refs(block, document):
    """Bind every rendered visible piece to its original field's document span.

    A failed literal match excludes the whole block. It never creates a quote
    by normalizing the document or by locating the same word in another field.
    Raw HTML offsets remain the more specific location of each original node.
    """
    if "support_visible_parts" not in block:
        return [], ["legacy_block_requires_rebuild"]
    snippets = context_snippets(block)
    if any("support_visible_parts" not in s for s in snippets):
        return [], ["legacy_context_requires_rebuild"]
    parts = block["support_visible_parts"] + [p for s in snippets for p in s["support_visible_parts"]]
    if not parts:
        return [], ["no_support_text"]
    field = block["source_ref"]["locator"]["json_path"]
    raw_sha = block["source_ref"]["sha256"]
    doc_text = document["description"]["text"]
    refs, failures, seen = [], [], set()
    def same_field(ref):
        locator = ref.get("locator")
        path = locator.get("json_path") if isinstance(locator, dict) else locator
        return ref.get("sha256") == raw_sha and path == field
    for part in parts:
        candidates = []
        for segment in document["description"].get("segments", []):
            if not any(same_field(ref) for ref in segment.get("source_refs", [])):
                continue
            start = doc_text.find(part, segment["start"], segment["end"])
            if start >= 0:
                candidates.append(start)
        if not candidates:
            failures.append(part)
            continue
        start = min(candidates)
        identity = start, start + len(part), part
        if identity not in seen:
            seen.add(identity)
            refs.append({"document_id": document["document_id"], "document_sha256": document["document_sha256"],
                         "field": "description", "start": start, "end": start + len(part), "quote": part,
                         "source_html_block_id": block["block_id"]})
    return refs, failures


def raw_title_file(document):
    candidates = {(ref["raw_file"], ref["sha256"]) for ref in document["title"].get("source_refs", [])
                  if ref.get("raw_file", "").endswith(".json")}
    if len(candidates) != 1:
        raise ValueError("AU title must identify one immutable API item JSON")
    raw_file, expected = next(iter(candidates))
    path = ROOT / raw_file
    if sha(path) != expected:
        raise ValueError("AU original API file SHA differs: " + raw_file)
    return path, raw_file, expected


def prepare(full_prepared, novel_prepared, full_results, novel_results, diagnostics, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    from trial_scoped_html_evidence_v1 import query_for
    selected, policy = choose_errors(json.loads(diagnostics.read_text()))
    ids = {e["case_id"] for e in selected}
    combined = {name: [] for name in NAMES}
    source_shas = {str(diagnostics.resolve()): sha(diagnostics)}
    source_manifests = []
    for prep in (full_prepared, novel_prepared):
        manifest = json.loads((prep / "manifest.json").read_text())
        source_manifests.append(manifest)
        source_shas[str((prep / "manifest.json").resolve())] = sha(prep / "manifest.json")
        source = {}
        for name in NAMES:
            expected = manifest["output_sha256"][name]
            if sha(prep / name) != expected:
                raise ValueError("original preparation hash differs: " + name)
            source_shas[str((prep / name).resolve())] = expected
            source[name] = read(prep / name)
        tasks = [t for t in source["tasks.jsonl"] if t["case_id"] in ids]
        request_ids = {t["request_id"] for t in tasks}
        requests = [r for r in source["requests.jsonl"] if r["request_id"] in request_ids]
        document_ids = {r["target_document_id"] for r in requests}
        filtered = {"tasks.jsonl": tasks, "requests.jsonl": requests,
                    "documents.jsonl": [d for d in source["documents.jsonl"] if d["document_id"] in document_ids],
                    "case_plans.jsonl": [c for c in source["case_plans.jsonl"] if c["case_id"] in ids],
                    "row_plans.jsonl": [c for c in source["row_plans.jsonl"] if c["case_id"] in ids]}
        for name in NAMES:
            combined[name].extend(filtered[name])
    if {c["case_id"] for c in combined["case_plans.jsonl"]} != ids:
        raise ValueError("selected prior errors not fully present in original preparations")
    requests = {r["request_id"]: r for r in combined["requests.jsonl"]}
    documents = {d["document_id"]: d for d in combined["documents.jsonl"]}
    if len(requests) != len(combined["requests.jsonl"]) or len(documents) != len(combined["documents.jsonl"]):
        raise ValueError("cross-cohort request/document ID collision")
    baseline = {}
    for results in (full_results, novel_results):
        path = results / "fusion-predictions.jsonl"
        source_shas[str(path.resolve())] = sha(path)
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                prediction = json.loads(line)
                identity = prediction["request_id"]
                if identity in requests:
                    if identity in baseline or prediction["source_request"] != requests[identity]:
                        raise ValueError("original prediction binding differs or collides")
                    baseline[identity] = prediction
    if baseline.keys() != requests.keys():
        raise ValueError("not all selected requests have frozen baseline predictions")
    blocks, excluded, raw_files = [], [], {}
    for document in documents.values():
        if document["side"] != "au":
            continue
        path, raw_file, expected = raw_title_file(document)
        source_shas[str(path.resolve())] = expected
        raw_files[raw_file] = expected
        item = json.loads(path.read_bytes())["itemInfo"]
        seen_html = set()
        for field in FIELDS:
            html = item.get(field)
            if not isinstance(html, str) or not html or html in seen_html:
                continue
            seen_html.add(html)
            for block in extract_blocks(html, {"raw_file": raw_file, "sha256": expected,
                                              "json_path": "$.itemInfo." + field}, document["title"]["text"]):
                refs, failures = literal_refs(block, document)
                new = {**block, "target_document_id": document["document_id"],
                       "target_document_sha256": document["document_sha256"], "quote_refs": refs,
                       "model_passage": render_passage(block), "scope_status": "unproved"}
                if failures or not refs:
                    excluded.append({**new, "exclusion_reason": "not_all_visible_parts_bind_original_document", "unmatched_parts": failures})
                else:
                    blocks.append(new)
    blocks_by_doc = defaultdict(list)
    for block in blocks:
        blocks_by_doc[block["target_document_id"]].append(block)
    retrieval = []
    for request in requests.values():
        if request["direction"] != "rakuten_to_au":
            continue
        for block in blocks_by_doc[request["target_document_id"]]:
            identity = hashlib.sha256((request["request_id"] + block["block_id"]).encode()).hexdigest()
            retrieval.append({"id": "scoped-html-rank:" + identity, "request_id": request["request_id"],
                              "target_document_id": request["target_document_id"], "block_id": block["block_id"],
                              "query": query_for(request, documents[request["target_document_id"]]),
                              "passage": block["model_passage"], "quote_refs": block["quote_refs"]})
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {**combined, "baseline-predictions.jsonl": [baseline[k] for k in requests],
               "blocks.jsonl": blocks, "excluded-blocks.jsonl": excluded, "retrieval-requests.jsonl": retrieval}
    for name, values in outputs.items():
        write(output_dir / name, values)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in (Path(__file__).name, "build_scoped_html_blocks_v1.py"):
        shutil.copyfile(HERE / name, code_dir / name)
    manifest = {**deepcopy(source_manifests[0]), "schema_version": "scoped-html-error-preparation-v1",
                "labels_read": False, "labels_in_model_inputs": False, "errors_only": True,
                "development_only": True,
                "prior_machine_label_derived_selection": True, "independent_test": False,
                "synthetic_data": False, "production_adoption_allowed": False,
                "selection_policy": policy, "selected_error_cells": {e["case_id"]: e["cell"] for e in selected},
                "selected_error_counts": dict(Counter(e["cell"] for e in selected)),
                "case_count": len(ids), "request_count": len(requests), "document_count": len(documents),
                "task_count": len(combined["tasks.jsonl"]), "row_count": len(combined["row_plans.jsonl"]),
                "cohort_case_counts": dict(Counter(c["cohort"] for c in combined["case_plans.jsonl"])),
                "baseline_matched_count": sum(c["upstream_decision"]["decision"] == "matched" for c in combined["case_plans.jsonl"]),
                "block_count": len(blocks), "excluded_block_count": len(excluded), "retrieval_pair_count": len(retrieval),
                "block_counts_by_document": dict(Counter(b["target_document_id"] for b in blocks)),
                "forward_request_count": sum(r["direction"] == "rakuten_to_au" for r in requests.values()),
                "raw_files": raw_files, "source_input_sha256": source_shas,
                "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
                "output_sha256": {name: sha(output_dir / name) for name in outputs}}
    for path, expected in source_shas.items():
        if sha(path) != expected:
            raise ValueError("original input changed while preparing: " + path)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("full-prepared", "novel-prepared", "full-results", "novel-results", "diagnostics", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ("case_count", "selected_error_counts", "request_count", "forward_request_count", "block_count", "excluded_block_count", "retrieval_pair_count")}, ensure_ascii=False))
