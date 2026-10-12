#!/usr/bin/env python3
"""Exploratory CPU rerun with only explicitly labeled keyword blocks removed.

The original mixed CPU product-model artifacts are treated as immutable input.
Their source files, code, rankings, score tables, and summary hashes are checked
before any derived predictions are written. Labels are never loaded.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import product_condition_sharing_v1 as SHARING
import trial_product_condition_models_v1 as PRODUCT
import trial_scoped_html_evidence_v1 as V1
import trial_generic_model_jev_v1 as JEV

DEFAULT_RUN = Path(".lab-output/sku-mixed-cpu-run-20261012-v1")
DEFAULT_OUTPUT = Path(".lab-output/sku-keyword-filter-cpu-20261012-v1")
DEFAULT_JEV_MODEL = Path(".deps/sku-fixed-models/jev")
THREADS = 4
BATCH_SIZE = 16
TOP_K = 3
VARIANT = "product-top3-jev"

# The first nonempty visible line must itself be an explicit keyword heading.
# Product-category words and item quantities elsewhere are intentionally inert.
KEYWORD_HEADING = re.compile(
    r"^\s*(?:▼\s*)?(?:(?:【\s*)(?:検索\s*(?:ワード|キーワード)|関連キーワード|キーワード)"
    r"(?:\s*】).*|(?:検索\s*(?:ワード|キーワード)|関連キーワード|キーワード)"
    r"(?:\s*[:：].*)?)\s*$"
)
SOURCE_ARTIFACTS = (
    "freeze.json", "summary.json", "source-manifest.json", "sharing-plan.json", "sharing-contract.json",
    "baseline-predictions.jsonl", "product-groups.jsonl", "source-documents.jsonl",
    "source-blocks.jsonl", "retrieval-scores.jsonl", "retrieval-selections.jsonl",
    "jev-unique-scores.jsonl",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path: Path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value):
    PRODUCT.write_json(Path(path), value)


def write_rows(path: Path, values):
    PRODUCT.write_rows(Path(path), values)


def snapshot_paths(paths):
    """Capture immutable file identities used by an execution phase."""
    snapshot = {}
    for name, path in paths.items():
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError("frozen execution input is missing: " + str(path))
        snapshot[name] = {"path": str(path), "sha256": sha(path)}
    return snapshot


def verify_snapshot(snapshot, *, phase):
    for name, item in snapshot.items():
        path = Path(item["path"])
        if not path.is_file() or sha(path) != item["sha256"]:
            raise ValueError("frozen execution input changed " + phase + ": " + name)


def is_explicit_keyword_block(block: dict) -> bool:
    """Match an explicitly labeled block heading, never content semantics."""
    text = block.get("text")
    if not isinstance(text, str):
        return False
    heading = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return bool(heading and KEYWORD_HEADING.fullmatch(heading))


def verify_source(product_dir: Path):
    """Verify the frozen CPU product run and return its indexed read-only data."""
    product_dir = Path(product_dir)
    summary = read_json(product_dir / "summary.json")
    freeze = read_json(product_dir / "freeze.json")
    if (summary.get("schema_version") != "product-condition-models-v1"
            or summary.get("labels_read") is not False
            or summary.get("labels_in_model_inputs") is not False
            or summary.get("development_only") is not True
            or summary.get("independent_test") is not False
            or summary.get("synthetic_data") is not False
            or summary.get("production_adoption_allowed") is not False
            or summary.get("device") != "cpu"
            or summary.get("jev_precision") != "fp32"
            or summary.get("reranker_precision") != "int8"
            or summary.get("top_k") != TOP_K
            or summary.get("sharing_savings", {}).get("shared_group_count") != 29):
        raise ValueError("source product-model summary is outside the expected frozen CPU development run")
    if (freeze.get("schema_version") != summary.get("schema_version")
            or freeze.get("input_sha256") != summary.get("input_sha256")
            or freeze.get("code_sha256") != summary.get("code_sha256")
            or freeze.get("sharing_plan_sha256") != summary.get("sharing_plan_sha256")
            or freeze.get("eligible_requests_sha256") != summary.get("eligible_requests_sha256")):
        raise ValueError("source freeze and summary provenance differ")

    output_hashes = summary.get("output_sha256")
    if not isinstance(output_hashes, dict) or not output_hashes:
        raise ValueError("source summary has no output SHA manifest")
    for name, expected in output_hashes.items():
        path = product_dir / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("source output SHA mismatch: " + name)

    for name, expected in summary["input_sha256"].items():
        path = product_dir / ("source-" + name)
        if not path.is_file() or sha(path) != expected:
            raise ValueError("copied source input SHA mismatch: " + name)
    if sha(product_dir / "sharing-plan.json") != summary["sharing_plan_sha256"]:
        raise ValueError("source sharing plan SHA differs from freeze")
    if sha(product_dir / "source-eligible-requests.json") != summary["eligible_requests_sha256"]:
        raise ValueError("source eligible-request contract SHA differs from freeze")
    for name, expected in summary.get("code_sha256", {}).items():
        copied_name = "reranker.json" if name == "reranker.json" else name
        if sha(product_dir / "code" / copied_name) != expected:
            raise ValueError("copied frozen source code SHA mismatch: " + name)
        current_path = (PRODUCT.HERE / "manifests/reranker.json" if name == "reranker.json"
                        else PRODUCT.HERE / name)
        if not current_path.is_file() or sha(current_path) != expected:
            raise ValueError("current product code differs from source freeze: " + name)
    if not JEV._same_pins(summary.get("verified_jev_pin")):
        raise ValueError("source summary JEV model pin differs from the frozen CPU FP32 model")
    reranker_manifest = read_json(product_dir / "code/reranker.json")
    if reranker_manifest != summary.get("model_identity", {}).get("reranker"):
        raise ValueError("source summary reranker identity differs from its copied manifest")

    source_manifest = read_json(product_dir / "source-manifest.json")
    if (summary["input_sha256"].get("manifest.json") != sha(product_dir / "source-manifest.json")
            or source_manifest.get("errors_only") is not False
            or source_manifest.get("labels_read") is not False
            or source_manifest.get("independent_test") is not False
            or source_manifest.get("development_only") is not True
            or source_manifest.get("synthetic") is not False):
        raise ValueError("copied source manifest does not prove the expected mixed development input")

    groups = list(read_rows(product_dir / "product-groups.jsonl"))
    blocks_list = list(read_rows(product_dir / "source-blocks.jsonl"))
    documents_list = list(read_rows(product_dir / "source-documents.jsonl"))
    scores = list(read_rows(product_dir / "retrieval-scores.jsonl"))
    selections = list(read_rows(product_dir / "retrieval-selections.jsonl"))
    plan = read_json(product_dir / "sharing-plan.json")
    contract = read_json(product_dir / "sharing-contract.json")
    baseline = list(read_rows(product_dir / "baseline-predictions.jsonl"))
    blocks = {row["block_id"]: row for row in blocks_list}
    documents = {row["document_id"]: row for row in documents_list}
    if (len(blocks) != len(blocks_list) or len(documents) != len(documents_list)
            or len(groups) != summary.get("group_count")
            or len(groups) != 29):
        raise ValueError("frozen source group/document/block coverage differs")
    expected_groups = {group["group_id"] for group in groups}
    score_by_group = {}
    for row in scores:
        if row.get("group_id") not in expected_groups:
            raise ValueError("retrieval score references an unknown group")
        score_by_group.setdefault(row["group_id"], {})[row["block_id"]] = row
    selection_by_group = {row["request_id"]: row for row in selections}
    if (len(selection_by_group) != len(selections)
            or not set(selection_by_group) <= expected_groups):
        raise ValueError("retrieval ranking coverage differs from product groups")
    for group_id, selection in selection_by_group.items():
        rows = score_by_group.get(group_id, {})
        ranked = sorted((row for row in rows.values() if row.get("status") == "ok"),
                        key=lambda row: (-row["raw_logit"], row["block_id"]))
        if selection.get("ordered_block_ids") != [row["block_id"] for row in ranked]:
            raise ValueError("stored retrieval order differs from original reranker scores")
        for row in rows.values():
            if row.get("block_id") not in blocks:
                raise ValueError("retrieval score references an unknown source block")
    # One frozen group has no valid retrieval request at all; carry it forward
    # with an empty ranking so group_results emits unknown and fan-out retains
    # the existing baseline semantics.
    for group_id in expected_groups - set(selection_by_group):
        if score_by_group.get(group_id):
            raise ValueError("group has retrieval scores but no frozen ranking: " + group_id)
        selection_by_group[group_id] = {"request_id": group_id,
                                        "ordered_block_ids": [],
                                        "reason": "no_usable_block",
                                        "skipped_block_ids": []}
    if not set(plan.get("request_to_group", {}).values()) == expected_groups:
        raise ValueError("sharing plan group coverage differs")
    if sha(product_dir / "sharing-contract.json") != summary["eligible_requests_sha256"]:
        raise ValueError("sharing contract SHA differs from the frozen eligible-request contract")
    baseline_ids = [row.get("request_id") for row in baseline]
    original_ids = list(plan.get("original_requests", {}))
    passthrough_ids = plan.get("passthrough_request_ids", [])
    if (len(baseline_ids) != len(set(baseline_ids)) or set(baseline_ids) != set(original_ids)
            or len(original_ids) != summary.get("request_count")
            or len(passthrough_ids) != len(set(passthrough_ids))
            or len(passthrough_ids) != summary.get("passthrough_request_count")
            or not set(passthrough_ids) <= set(original_ids)):
        raise ValueError("source request identity or passthrough coverage differs")
    return {
        "product_dir": product_dir, "summary": summary, "freeze": freeze,
        "groups": groups, "blocks": blocks, "documents": documents,
        "scores": score_by_group, "selections": selection_by_group,
        "plan": plan, "contract": contract, "baseline": baseline,
        "baseline_sha256": sha(product_dir / "baseline-predictions.jsonl"),
    }


def filtered_rankings(selections: dict, blocks: dict, scores: dict, *, top_k=TOP_K):
    """Drop only explicitly labeled keyword blocks; retain source score order."""
    rankings, exclusions = {}, []
    for group_id, selection in selections.items():
        kept, removed = [], []
        for old_rank, block_id in enumerate(selection["ordered_block_ids"], start=1):
            block = blocks[block_id]
            score = scores[group_id][block_id]
            if is_explicit_keyword_block(block):
                removed.append(block_id)
                exclusions.append({"group_id": group_id, "block_id": block_id,
                    "old_rank": old_rank, "raw_logit": score["raw_logit"],
                    "heading": next((line.strip() for line in block["text"].splitlines() if line.strip()), ""),
                    "reason": "explicit_keyword_heading"})
            else:
                kept.append({"block_id": block_id, "old_rank": old_rank,
                             "raw_logit": score["raw_logit"]})
        rankings[group_id] = {"request_id": group_id,
            "ordered_block_ids": [row["block_id"] for row in kept],
            "ordered_scored_blocks": kept,
            "selected_block_ids": [row["block_id"] for row in kept[:top_k]],
            "excluded_block_ids": removed,
            "source_order_sha256": PRODUCT.digest(selection["ordered_block_ids"])}
    return rankings, exclusions


def validated_jev_cache(records):
    """Index the old JEV cache only after exact input and pin validation."""
    cache = {}
    expected_pins = JEV._pins()
    for record in records:
        request = record.get("request")
        if not isinstance(request, dict):
            raise ValueError("old JEV cache record lacks its exact raw request")
        key = JEV.request_key(request.get("question"), request.get("state"), request.get("choices"))
        if (record.get("cache_key") != key
                or record.get("model_id") != JEV.MODEL_ID
                or record.get("model_revision") != JEV.MODEL_REVISION
                or record.get("precision") != "fp32"
                or record.get("format_version") != JEV.FORMAT_VERSION
                or not JEV._same_pins(record.get("pins"))):
            raise ValueError("old JEV cache identity differs from exact CPU FP32 pins")
        # The old JSONL writer sorts object keys, while the scorer validates
        # score-map iteration order against the raw choice order. Restore that
        # deterministic order in memory without changing any score values.
        record = dict(record)
        labels = [choice["label"] for choice in request["choices"]]
        for field in ("token_counts", "logits", "probabilities"):
            mapping = record.get(field)
            if isinstance(mapping, dict) and set(mapping) == set(labels):
                record[field] = {label: mapping[label] for label in labels}
        JEV._validate_scores(record, request)
        if key in cache:
            raise ValueError("duplicate exact request key in old JEV cache")
        cache[key] = record
    return cache


def validate_filtered_rankings(source, rankings):
    for group_id, ranking in rankings.items():
        old = source["selections"][group_id]
        old_scores = source["scores"].get(group_id, {})
        for row in ranking["ordered_scored_blocks"]:
            if row["raw_logit"] != old_scores[row["block_id"]]["raw_logit"]:
                raise ValueError("filtered ranking changed an old reranker score")
        if ranking["source_order_sha256"] != PRODUCT.digest(old["ordered_block_ids"]):
            raise ValueError("filtered ranking lost source order binding")


def score_filtered_choices(choices, cache, model_dir, runner=None):
    """Reuse only exact pinned keys and infer every uncached unique input."""
    unique_keys = {JEV.request_key(row["question"], row["state"], row["choices"])
                   for row in choices}
    reusable_keys = unique_keys & set(cache)
    pending_keys = unique_keys - set(cache)
    call = runner or JEV.run_choices
    result = call(choices, model_dir=Path(model_dir), threads=THREADS,
                  batch_size=BATCH_SIZE, cache=cache) if choices else {
                      "records": [], "pins": JEV._pins(),
                      "runtime": {"inferred_unique_request_count": 0,
                                  "cached_unique_request_count": 0,
                                  "unique_request_count": 0}}
    if result.get("runtime", {}).get("inferred_unique_request_count") != len(pending_keys):
        raise ValueError("actual JEV inference count differs from exact-key cache accounting")
    return result, reusable_keys, pending_keys


def run_keyword_filtered(source_run_dir: Path, output_dir: Path,
                         jev_model_dir: Path = DEFAULT_JEV_MODEL,
                         *, jev_runner=None):
    source_run_dir, output_dir, jev_model_dir = map(Path, (source_run_dir, output_dir, jev_model_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    product_dir = source_run_dir / "product-model"
    source = verify_source(product_dir)
    # Independently verify the exact local model acquisition before any scoring.
    local_pin = JEV.verify_model_files(jev_model_dir)
    if not JEV._same_pins(local_pin):
        raise ValueError("local JEV files do not match the frozen CPU FP32 model pin")
    for name, expected in local_pin["file_sha256"].items():
        if sha(jev_model_dir / name) != expected:
            raise ValueError("local JEV model file SHA mismatch: " + name)

    started = time.perf_counter()
    old_cache_path = product_dir / "jev-unique-scores.jsonl"
    old_cache_records = list(read_rows(old_cache_path))
    cache = validated_jev_cache(old_cache_records)
    rankings, exclusions = filtered_rankings(source["selections"], source["blocks"], source["scores"])
    choices = PRODUCT.classification_requests(source["groups"], source["blocks"], rankings)
    unique_keys = {JEV.request_key(row["question"], row["state"], row["choices"]) for row in choices}

    output_dir.mkdir(parents=True, exist_ok=False)
    source_snapshot = output_dir / "source"
    source_snapshot.mkdir()
    for name in SOURCE_ARTIFACTS:
        shutil.copyfile(product_dir / name, source_snapshot / name)
    shutil.copytree(product_dir / "code", source_snapshot / "code")
    cache_dir = output_dir / "cache"
    cache_dir.mkdir()
    shutil.copyfile(old_cache_path, cache_dir / "source-jev-unique-scores.jsonl")
    (output_dir / "models").mkdir()
    write_json(output_dir / "models/jev-pin.json", {
        "pin": local_pin, "model_dir": str(jev_model_dir.resolve()),
        "provenance_manifest_sha256": sha(jev_model_dir / "provenance.json"),
        "verified_file_sha256": {name: sha(jev_model_dir / name) for name in local_pin["file_sha256"]},
        "device": "cpu", "precision": "fp32"})

    validate_filtered_rankings(source, rankings)
    write_rows(output_dir / "filtered-retrieval-rankings.jsonl", rankings.values())
    write_rows(output_dir / "keyword-block-exclusions.jsonl", exclusions)
    write_rows(output_dir / "classification-requests.jsonl", choices)

    own_script = output_dir / "code" / Path(__file__).name
    own_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve(), own_script)
    frozen_paths = {
        **{"source_artifact:" + name: product_dir / name for name in SOURCE_ARTIFACTS},
        **{"source_copied_code:" + str(p.relative_to(product_dir / "code")):
           p for p in sorted((product_dir / "code").rglob("*")) if p.is_file()},
        **{"current_code:" + name: (PRODUCT.HERE / "manifests/reranker.json"
           if name == "reranker.json" else PRODUCT.HERE / name) for name in PRODUCT.CODE_NAMES},
        "experiment_script": Path(__file__).resolve(),
        "copied_experiment_script": own_script,
        "jev_model_provenance": jev_model_dir / "provenance.json",
        **{"jev_model:" + name: jev_model_dir / name for name in local_pin["file_sha256"]},
        "classification_requests": output_dir / "classification-requests.jsonl",
        "filtered_rankings": output_dir / "filtered-retrieval-rankings.jsonl",
        "keyword_exclusions": output_dir / "keyword-block-exclusions.jsonl",
        "source_jev_cache": old_cache_path,
        "copied_source_jev_cache": cache_dir / "source-jev-unique-scores.jsonl",
    }
    execution_snapshot = snapshot_paths(frozen_paths)
    execution_freeze = {
        "schema_version": "keyword-filtered-product-execution-freeze-v1",
        "labels_read": False, "labels_in_model_inputs": False,
        "experiment_status": "exploratory_dev_informed",
        "frozen_files": execution_snapshot,
        "source_output_sha256": source["summary"]["output_sha256"],
        "source_code_sha256": source["summary"]["code_sha256"],
        "source_artifact_sha256": {name: sha(product_dir / name) for name in SOURCE_ARTIFACTS},
        "classification_requests_sha256": sha(output_dir / "classification-requests.jsonl"),
        "filtered_rankings_sha256": sha(output_dir / "filtered-retrieval-rankings.jsonl"),
        "keyword_exclusions_sha256": sha(output_dir / "keyword-block-exclusions.jsonl"),
        "policy": {"regex": KEYWORD_HEADING.pattern,
                   "only_explicit_first_nonempty_keyword_heading": True,
                   "reranker_rerun": False, "top_k_after_filter": TOP_K},
    }
    write_json(output_dir / "execution-freeze.json", execution_freeze)

    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    result, reusable_keys, pending_keys = score_filtered_choices(
        choices, cache, jev_model_dir, runner=jev_runner)
    verify_snapshot(execution_snapshot, phase="after scoring")
    verify_source(product_dir)
    jev_records = result["records"]
    expected_ids = [choice["id"] for choice in choices]
    if [row.get("id") for row in jev_records] != expected_ids:
        raise ValueError("new JEV output request identities differ from filtered choices")
    if not JEV._same_pins(result.get("pins")):
        raise ValueError("new JEV result pin differs from verified fixed CPU model")
    write_rows(output_dir / "jev-classification-options.jsonl", jev_records)
    write_rows(output_dir / "new-jev-inference-scores.jsonl",
               [row for row in jev_records if row.get("cache_key") in pending_keys])

    group_results = PRODUCT.group_results(source["groups"], jev_records, [],
                                          source["documents"], source["blocks"], VARIANT)
    if set(group_results) != {group["group_id"] for group in source["groups"]}:
        raise ValueError("filtered product group results do not cover all shared groups")
    predictions = SHARING.fan_out(source["plan"], group_results, source["baseline"])
    prediction_ids = [row.get("request_id") for row in predictions]
    expected_original_ids = set(source["plan"]["original_requests"])
    if (len(predictions) != source["summary"].get("request_count")
            or len(prediction_ids) != len(set(prediction_ids))
            or set(prediction_ids) != expected_original_ids):
        raise ValueError("filtered fan-out does not cover every original request")
    if sha(product_dir / "baseline-predictions.jsonl") != source["baseline_sha256"]:
        raise ValueError("source fresh baseline changed during replay")
    passthrough = set(source["plan"].get("passthrough_request_ids", []))
    pred_by_id = {row["request_id"]: row for row in predictions}
    baseline_by_id = {row["request_id"]: row for row in source["baseline"]}
    if any(pred_by_id[identity] != baseline_by_id[identity] for identity in passthrough):
        raise ValueError("fresh nonshared baseline predictions changed during product fan-out")
    if len(passthrough) != 54:
        raise ValueError("expected exactly 54 nonshared passthrough requests")
    write_rows(output_dir / "product-top3-jev-group-results.jsonl", group_results.values())
    write_rows(output_dir / "product-top3-jev-predictions.jsonl", predictions)

    # This is a label-free prediction freeze. Parent evaluation consumes these
    # files only after validating the summary and all prediction hashes.
    verify_snapshot(execution_snapshot, phase="before final freeze")
    verify_source(product_dir)
    output_hashes = {str(path.relative_to(output_dir)): sha(path)
                     for path in sorted(output_dir.rglob("*")) if path.is_file()}
    source_hashes = {name: sha(product_dir / name) for name in SOURCE_ARTIFACTS}
    code_hashes = {str(path.relative_to(source_snapshot / "code")): sha(path)
                   for path in sorted((source_snapshot / "code").rglob("*")) if path.is_file()}
    output_hashes[str(own_script.relative_to(output_dir))] = sha(own_script)
    freeze = {
        "schema_version": "keyword-filtered-product-cpu-v1",
        "experiment_status": "exploratory_dev_informed",
        "labels_read": False, "labels_in_model_inputs": False,
        "independent_test": False, "production_adoption_allowed": False,
        "source_run_dir": str(source_run_dir.resolve()),
        "source_product_dir": str(product_dir.resolve()),
        "source_summary_sha256": sha(product_dir / "summary.json"),
        "source_summary_errors_only_metadata": source["summary"].get("errors_only"),
        "source_manifest_errors_only": False,
        "source_scope_binding": "copied source-manifest.json and source freeze input SHA",
        "source_freeze_sha256": sha(product_dir / "freeze.json"),
        "source_artifact_sha256": source_hashes,
        "source_output_sha256": source["summary"]["output_sha256"],
        "execution_freeze_sha256": sha(output_dir / "execution-freeze.json"),
        "source_code_sha256": source["summary"]["code_sha256"],
        "copied_source_code_sha256": code_hashes,
        "new_experiment_code_sha256": {Path(__file__).name: sha(own_script)},
        "source_sharing_plan_sha256": source["summary"]["sharing_plan_sha256"],
        "source_fresh_baseline_sha256": source["baseline_sha256"],
        "source_jev_cache_sha256": sha(old_cache_path),
        "model_pins": {"jev": local_pin,
                       "reranker": source["summary"].get("model_identity", {}).get("reranker")},
        "policy": {"filtered_heading_regex": KEYWORD_HEADING.pattern,
                   "filter_scope": "only first nonempty block text line matching explicit keyword heading",
                   "category_or_quantity_semantics_used": False,
                   "reranker_rerun": False,
                   "ranking_source": "verified original reranker scores; original order retained after exclusions",
                   "top_k_after_filter": TOP_K,
                   "jev_cache_key": "pinned JEV request_key(question,state,ordered choices)",
                   "baseline_refresh": False,
                   "nonshared_baseline_preserved": True},
        "group_count": len(group_results), "request_count": len(predictions),
        "excluded_group_block_pair_count": len(exclusions),
        "excluded_unique_keyword_block_count": len({row["block_id"] for row in exclusions}),
        "filtered_choice_count": len(choices),
        "unique_filtered_jev_key_count": len(unique_keys),
        "old_exact_jev_cache_hit_count": len(reusable_keys),
        "actual_new_jev_inference_count": len(pending_keys),
        "jev_runtime": result.get("runtime", {}),
        "prediction_sha256": sha(output_dir / "product-top3-jev-predictions.jsonl"),
        "group_result_sha256": sha(output_dir / "product-top3-jev-group-results.jsonl"),
        "output_sha256": output_hashes,
    }
    write_json(output_dir / "freeze.json", freeze)
    summary = {
        **{k: v for k, v in freeze.items() if k != "output_sha256"},
        "group_decision_states": dict(Counter(r["status"] for r in group_results.values())),
        "prediction_states": dict(Counter(r["state"] for r in predictions)),
        "filter_counts_by_heading": dict(Counter(x["heading"] for x in exclusions)),
        "runtime_seconds": time.perf_counter() - started,
        "freeze_sha256": sha(output_dir / "freeze.json"),
        "output_sha256": {str(path.relative_to(output_dir)): sha(path)
                          for path in sorted(output_dir.rglob("*"))
                          if path.is_file() and path.name != "summary.json"},
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--jev-model-dir", type=Path, default=DEFAULT_JEV_MODEL)
    args = parser.parse_args()
    summary = run_keyword_filtered(args.source_run_dir, args.output_dir, args.jev_model_dir)
    print(json.dumps({"output_dir": str(args.output_dir),
                      "group_count": summary["group_count"],
                      "request_count": summary["request_count"],
                      "actual_new_jev_inference_count": summary["actual_new_jev_inference_count"],
                      "prediction_sha256": summary["prediction_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
