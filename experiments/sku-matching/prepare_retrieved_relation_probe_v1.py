#!/usr/bin/env python3
"""Select frozen top-five literal windows without changing relation requests.

This validates the completed retrieval run against every source window and raw
alternative. Retrieval is relevance selection, never proof that a passage
applies to the fixed AU row. No annotations, inference, or SKU rules are read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path


TOP_KS = (1, 3, 5)
MAX_TOP_K = 5
SOURCE_NAMES = ("requests.jsonl", "empty-quote-requests.jsonl", "manifest.json")
VARIABLE_PROVENANCE = {"candidate_option_index", "candidate_option_value", "original_relation_request_id"}
REQUIRED_CODE = {"trial_generic_evidence_reranker_v1.py", "backend_reranker.py", "reranker.json"}


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha(path):
    return sha_bytes(path.read_bytes())


def _reject_constant(value):
    raise ValueError("nonfinite JSON value: " + value)


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def parse(value):
    return json.loads(value, parse_constant=_reject_constant, parse_float=_float)


def lines(value):
    return [(parse(line), line) for line in value.splitlines(keepends=True) if line.strip()]


def _relative(name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("unsafe artifact path")
    return name


def _read_verified(directory, checksums):
    result = {}
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError("artifact checksums are required")
    for name, expected in checksums.items():
        value = (directory / _relative(name)).read_bytes()
        if not isinstance(expected, str) or sha_bytes(value) != expected:
            raise ValueError("artifact SHA mismatch: " + name)
        result[name] = value
    return result


def _query(provenance, style):
    axes = provenance["selected_au_row"]["axes"]
    row_text = "\n".join(axis["axis_name"] + "：" + axis["value"] for axis in axes)
    axis_name = provenance["axis_name"]
    if style == "axis_options":
        return f'この商品の「{axis_name}」の値はどれですか？\n選択肢：' + " / ".join(provenance["option_values"]) + "\n選択中のAU行：\n" + row_text
    if style == "axis_only":
        return f'この商品の「{axis_name}」の仕様を説明した文章を探してください。\n選択中のAU行：\n' + row_text
    raise ValueError("unsupported frozen retrieval query style")


def _expected_windows(requests, empty_requests, query_style):
    groups, ids, task_bindings = {}, set(), {}
    for request, empty in [(r, False) for r in requests] + [(r, True) for r in empty_requests]:
        request_id = request["id"]
        if not isinstance(request_id, str) or not request_id or request_id in ids:
            raise ValueError("missing or duplicate original request ID")
        ids.add(request_id)
        p = request["provenance"]
        if not isinstance(p, dict):
            raise ValueError("request provenance must be an object")
        for name in ("task_id", "source_request_id", "axis_name", "au_row_key"):
            if not isinstance(p.get(name), str) or not p[name]:
                raise ValueError("missing whole request binding: " + name)
        options, index = p["option_values"], p["candidate_option_index"]
        if not isinstance(options, list) or not options or not all(isinstance(v, str) for v in options):
            raise ValueError("all whole raw alternatives must be strings")
        if type(index) is not int or not 0 <= index < len(options) or p["candidate_option_value"] != options[index]:
            raise ValueError("raw alternative/index binding mismatch")
        row = p["selected_au_row"]
        if not isinstance(row, dict) or row.get("row_key") != p["au_row_key"]:
            raise ValueError("fixed AU row binding mismatch")
        axes = row.get("axes")
        if not isinstance(axes, list) or not axes or any(not isinstance(a, dict) or not isinstance(a.get("axis_name"), str) or not isinstance(a.get("value"), str) for a in axes):
            raise ValueError("complete raw AU row axes are required")
        window = p["window"]
        quote = window["quote"]
        if not isinstance(quote, str) or empty != (not bool(quote.strip())):
            raise ValueError("literal quote/empty classification mismatch")
        if not isinstance(window.get("source_ref"), dict) or not window["source_ref"]:
            raise ValueError("original literal source reference is required")
        if not empty and request.get("premise") != quote:
            raise ValueError("relation premise differs from the full literal quote")
        binding = {name: deepcopy(p.get(name)) for name in ("axis_name", "option_values", "au_row_key", "selected_au_row", "fixed_au_product_ref", "case_id", "condition_id", "selected_value", "source_sku_key")}
        if p["task_id"] in task_bindings and task_bindings[p["task_id"]] != binding:
            raise ValueError("task identity changes its whole condition or fixed AU row")
        task_bindings[p["task_id"]] = binding
        key = (p["task_id"], p["source_request_id"])
        common = {name: deepcopy(value) for name, value in p.items() if name not in VARIABLE_PROVENANCE}
        if key not in groups:
            digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:20]
            groups[key] = {"id": "evidence-rank:" + digest, "task_id": key[0], "source_request_id": key[1],
                           "query": _query(p, query_style), "passage": quote, "provenance": common,
                           "source_relation_request_ids": [], "candidate_option_indices": [],
                           "excluded_from_inference": empty, "scope_proven": False}
        expected = groups[key]
        if expected["provenance"] != common or expected["passage"] != quote or expected["excluded_from_inference"] != empty:
            raise ValueError("same source window changes provenance or literal passage")
        if index in expected["candidate_option_indices"]:
            raise ValueError("duplicate raw alternative for a source window")
        expected["source_relation_request_ids"].append(request_id)
        expected["candidate_option_indices"].append(index)
    for record in groups.values():
        if sorted(record["candidate_option_indices"]) != list(range(len(record["provenance"]["option_values"]))):
            raise ValueError("source window does not preserve every original alternative")
        record["nli_source_ids"] = list(record["source_relation_request_ids"])
    return sorted(groups.values(), key=lambda record: (record["task_id"], record["source_request_id"]))


def validate(input_dir, ranking_dir):
    """Return validated byte snapshots and selections; never load labels/models."""
    source_manifest_bytes = (input_dir / "manifest.json").read_bytes()
    source_manifest = parse(source_manifest_bytes)
    hashes = source_manifest["output_sha256"]
    source = _read_verified(input_dir, {name: hashes[name] for name in SOURCE_NAMES[:2]})
    source["manifest.json"] = source_manifest_bytes
    requests, empty = (lines(source[name]) for name in SOURCE_NAMES[:2])
    if len(requests) != source_manifest["request_count"] or len(empty) != source_manifest["empty_literal_quote_request_count"]:
        raise ValueError("source manifest row count mismatch")
    summary_bytes = (ranking_dir / "summary.json").read_bytes()
    summary = parse(summary_bytes)
    ranking = _read_verified(ranking_dir, summary["output_sha256"])
    required = {"freeze.json", "ranking-requests.jsonl", "rankings.jsonl", "top-k-windows.jsonl"} | {"source-" + name for name in SOURCE_NAMES}
    if not required <= ranking.keys():
        raise ValueError("completed ranking is missing frozen artifacts")
    freeze = parse(ranking["freeze.json"])
    if any(summary.get(name) != value for name, value in freeze.items()):
        raise ValueError("completed summary differs from pre-inference freeze")
    if freeze.get("top_ks_for_diagnostics") != list(TOP_KS):
        raise ValueError("retrieval top-K must have been frozen as [1,3,5]")
    for name in ("labels_read", "domain_rules", "desired_value_injected_into_query", "scope_proven", "production_eligible", "synthetic_sku_generation", "truncation_used", "score_is_condition_confidence"):
        if freeze.get(name) is not False:
            raise ValueError("unexpected retrieval diagnostic policy: " + name)
    if freeze.get("score_kind") != "raw_logit" or freeze.get("passage_scope") != "exact_literal_window_quote" or freeze.get("complete_raw_au_row_in_query") is not True:
        raise ValueError("unexpected retrieval input/score policy")
    for name, value in source.items():
        if freeze["input_sha256"].get(name) != sha_bytes(value) or ranking["source-" + name] != value:
            raise ValueError("retrieval source is not the exact supplied input: " + name)
    if sha_bytes(ranking["ranking-requests.jsonl"]) != freeze["ranking_requests_sha256"]:
        raise ValueError("ranking requests differ from inference freeze")
    if not REQUIRED_CODE <= freeze["code_sha256"].keys():
        raise ValueError("ranking code freeze is incomplete")
    code = _read_verified(ranking_dir / "code", freeze["code_sha256"])
    if parse(code["reranker.json"]) != freeze["pins"]:
        raise ValueError("ranking model pins differ from frozen code")
    ranking["summary.json"] = summary_bytes
    style = freeze.get("query_style", "axis_options")
    expected = _expected_windows([r for r, _ in requests], [r for r, _ in empty], style)
    actual = [row for row, _ in lines(ranking["ranking-requests.jsonl"])]
    if actual != expected:
        raise ValueError("ranking requests do not match every original window/alternative binding")
    predictions = [row for row, _ in lines(ranking["rankings.jsonl"])]
    if len(predictions) != len(expected):
        raise ValueError("ranking output is missing original windows")
    eligible = defaultdict(list)
    for original, prediction in zip(expected, predictions, strict=True):
        if any(prediction.get(name) != value for name, value in original.items()):
            raise ValueError("ranking output changed a frozen source binding")
        status = prediction.get("status")
        if status == "ok":
            score, length = prediction.get("score"), prediction.get("pair_token_length")
            if original["excluded_from_inference"] or type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError("invalid successful retrieval score")
            if type(length) is not int or not 0 <= length <= freeze["pins"]["max_length"] or prediction.get("token_count_untruncated") != length:
                raise ValueError("successful retrieval did not preserve complete input length")
            if prediction.get("score_kind") != "raw_logit":
                raise ValueError("retrieval score kind differs from freeze")
            eligible[original["task_id"]].append(prediction)
        elif status == "empty_literal_quote":
            if not original["excluded_from_inference"] or any(prediction.get(name) is not None for name in ("score", "rank", "pair_token_length", "token_count_untruncated")):
                raise ValueError("empty quote retrieval status is inconsistent")
        elif status == "input_too_long":
            length = prediction.get("pair_token_length")
            if original["excluded_from_inference"] or type(length) is not int or length <= freeze["pins"]["max_length"] or prediction.get("token_count_untruncated") != length or prediction.get("score") is not None or prediction.get("rank") is not None:
                raise ValueError("overlimit retrieval status is inconsistent")
        else:
            raise ValueError("ranking is incomplete or has unknown output status")
    selections = []
    for task_id in sorted({row["task_id"] for row in expected}):
        ordered = sorted(eligible[task_id], key=lambda row: (-row["score"], row["source_request_id"], row["id"]))
        if any(type(row.get("rank")) is not int or row["rank"] != rank for rank, row in enumerate(ordered, 1)):
            raise ValueError("retrieval ranks disagree with frozen score ordering")
        selections.append({"task_id": task_id, "top_k_source_request_ids": {str(k): [row["source_request_id"] for row in ordered[:k]] for k in TOP_KS},
                           "eligible_window_count": len(ordered), "scope_proven": False, "production_eligible": False})
    if [row for row, _ in lines(ranking["top-k-windows.jsonl"])] != selections:
        raise ValueError("top-K window selection disagrees with completed ranking")
    for name, value in {"input_relation_request_count": len(requests), "empty_literal_quote_request_count": len(empty), "unique_original_window_count": len(expected), "task_count": len(selections), "status_counts": dict(Counter(row["status"] for row in predictions))}.items():
        if summary.get(name) != value:
            raise ValueError("ranking summary count mismatch: " + name)
    return source, ranking, code, requests, selections, freeze


def prepare(input_dir: Path, ranking_dir: Path, output_dir: Path):
    input_dir, ranking_dir, output_dir = map(Path, (input_dir, ranking_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    source, ranking, code, requests, selections, freeze = validate(input_dir, ranking_dir)
    selected_keys = {(row["task_id"], source_id) for row in selections for source_id in row["top_k_source_request_ids"][str(MAX_TOP_K)]}
    selected, unselected = [], []
    for request, line in requests:
        p = request["provenance"]
        (selected if (p["task_id"], p["source_request_id"]) in selected_keys else unselected).append(line)
    # Preserve exact original JSONL bytes and all request fields. Selection
    # metadata is separate, so backend inputs receive no new target hints.
    output_dir.mkdir(parents=True, exist_ok=False)
    artifacts = {"requests.jsonl": b"".join(selected), "unselected-requests.jsonl": b"".join(unselected),
                 "empty-quote-requests.jsonl": source["empty-quote-requests.jsonl"],
                 **{"source-" + name: value for name, value in source.items()},
                 **{"ranking-snapshot/" + name: value for name, value in ranking.items()},
                 **{"ranking-snapshot/code/" + name: value for name, value in code.items()},
                 "code/prepare_retrieved_relation_probe_v1.py": Path(__file__).read_bytes()}
    selection_metadata = [{**deepcopy(row), "maximum_top_k_selected": MAX_TOP_K,
                           "selected_source_request_ids": row["top_k_source_request_ids"][str(MAX_TOP_K)],
                           "selected_window_count": len(row["top_k_source_request_ids"][str(MAX_TOP_K)]),
                           "empty_selection": not row["top_k_source_request_ids"][str(MAX_TOP_K)]} for row in selections]
    artifacts["selections.jsonl"] = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in selection_metadata).encode()
    for name, value in artifacts.items():
        destination = output_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(value)
    manifest = {"schema_version": "retrieved_relation_probe_manifest_v1", "input_dir": str(input_dir.resolve()),
                "ranking_dir": str(ranking_dir.resolve()), "request_count": len(selected),
                "unselected_request_count": len(unselected), "source_request_count": len(requests),
                "empty_literal_quote_request_count": len(lines(source["empty-quote-requests.jsonl"])),
                "task_count": len(selections), "empty_selection_task_ids": [row["task_id"] for row in selection_metadata if row["empty_selection"]],
                "maximum_top_k_selected": MAX_TOP_K, "top_ks_frozen_before_ranking": list(TOP_KS),
                "ranking_query_style": freeze.get("query_style", "axis_options"), "labels_read": False,
                "scope_proven": False, "production_eligible": False, "domain_rules": False,
                "synthetic_sku_generation": False, "truncation_used": False,
                "selected_requests_modified": False, "all_original_alternatives_preserved": True,
                "unselected_and_empty_requests_retained": True, "ranking_score_is_condition_confidence": False,
                "input_sha256": {name: sha_bytes(value) for name, value in source.items()},
                "ranking_sha256": {name: sha_bytes(value) for name, value in ranking.items()},
                "ranking_code_sha256": {name: sha_bytes(value) for name, value in code.items()},
                "code_sha256": sha_bytes(artifacts["code/prepare_retrieved_relation_probe_v1.py"]),
                "output_sha256": {name: sha_bytes(value) for name, value in artifacts.items()}}
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--ranking-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({name: result[name] for name in ("request_count", "unselected_request_count", "task_count", "empty_selection_task_ids")}, ensure_ascii=False))
