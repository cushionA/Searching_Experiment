#!/usr/bin/env python3
"""Reuse frozen top-ten retrieval, then model-select current-row evidence.

The same pinned JEV model selects a description and subsequently its value.
Source existence and model relevance do not prove applicability. This is a
label-free development diagnostic on the frozen real error subset.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import nullcontext
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import time

import trial_scoped_html_evidence_v1 as V1

FULL, JEV, NLI = V1.FULL, V1.JEV, V1.NLI
HERE, ROOT = V1.HERE, V1.ROOT
read, sha, digest = V1.read, V1.sha, V1.digest
write_json, write_rows = V1.write_json, V1.write_rows
TOP_K = 10
EVIDENCE_UNKNOWN = "対象商品の条件を確認できる記述がない"
EVIDENCE_QUESTION = ("現在販売するこの商品の、この選択行について「{axis_name}」の仕様を判断できる記述を選んでください。"
                     "検索語一覧や別商品の購入案内だけでは判断できません。判断できなければ「" + EVIDENCE_UNKNOWN + "」を選んでください。")
EVIDENCE_UNKNOWN_DESCRIPTION = "現在の商品と選択行について、この条件の仕様を確認できる記述が候補にない。"
PREVIOUS_NAMES = ("summary.json", "freeze.json", "retrieval-selections.jsonl", "retrieval-scores.jsonl",
                  "jev-classification-options.jsonl", "nli-classification-options.jsonl")
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, *V1.CODE_NAMES)))


def restore_choice_score_order(record):
    """Restore label order lost by canonical JSON; never repair keys or scores."""
    restored = deepcopy(record)
    labels = [choice["label"] for choice in restored["choices"]]
    for field in ("logits", "probabilities", "token_counts"):
        values = restored.get(field)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError("previous JEV score mapping must be an object: " + field)
        expected = labels
        if field == "token_counts" and restored.get("status") == "tokenization_error":
            expected = labels[:len(values)]
        if set(values) != set(expected):
            raise ValueError("previous JEV score keys differ from original choices: " + field)
        restored[field] = {label: values[label] for label in expected}
    return restored


def load_previous(previous_run, prepared_dir, requests, blocks, retrieval):
    summary = json.loads((previous_run / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "scoped-html-evidence-v1"
            or summary.get("labels_read") is not False or summary.get("labels_in_model_inputs") is not False
            or summary.get("input_and_code_sha256_verified_after_inference") is not True
            or summary.get("production_adoption_allowed") is not False):
        raise ValueError("previous run must be a completed frozen label-free v1 diagnostic")
    for name in PREVIOUS_NAMES[1:]:
        if summary.get("output_sha256", {}).get(name) != sha(previous_run / name):
            raise ValueError("previous output SHA256 differs: " + name)
    for name in ("manifest.json", *V1.SOURCE_NAMES):
        if summary.get("input_sha256", {}).get(name) != sha(prepared_dir / name):
            raise ValueError("previous prepared source SHA256 differs: " + name)
    expected_reranker = json.loads((HERE / "manifests/reranker.json").read_text(encoding="utf-8"))
    if summary.get("reranker_pin") != expected_reranker or summary.get("jev_pin") != JEV._pins():
        raise ValueError("previous frozen reranker/JEV pins differ")
    expected_nli = {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                    "parameter_count": NLI.FP32_PARAMETER_COUNT, "files_sha256": NLI.NLI_FILE_SHA256}
    if summary.get("nli_pin") != expected_nli:
        raise ValueError("previous frozen NLI pin differs")
    if summary.get("unique_jev_request_count", 0) and not JEV._same_pins(summary.get("verified_jev_pin")):
        raise ValueError("previous verified JEV pin differs")
    actual_nli = summary.get("verified_nli_pin")
    if summary.get("unique_nli_pair_count", 0) and (not isinstance(actual_nli, dict) or
            any(actual_nli.get(k) != expected_nli[k] for k in ("model_id", "revision", "parameter_count"))):
        raise ValueError("previous verified NLI pin differs")
    for name, expected in summary.get("code_sha256", {}).items():
        path = HERE / "manifests/reranker.json" if name == "reranker.json" else HERE / name
        if sha(path) != expected:
            raise ValueError("previous frozen implementation changed: " + name)
    source_by_id = {row["id"]: row for row in retrieval}
    scores = read(previous_run / "retrieval-scores.jsonl")
    if len(scores) != len(source_by_id) or {row["id"] for row in scores} != set(source_by_id):
        raise ValueError("previous retrieval pair coverage differs")
    by_request = defaultdict(list)
    seen = set()
    for score in scores:
        if score["id"] in seen or any(score.get(k) != v for k, v in source_by_id[score["id"]].items()):
            raise ValueError("previous retrieval source binding differs")
        seen.add(score["id"])
        if score.get("status") == "ok":
            count, value = score.get("token_count_untruncated"), score.get("raw_logit")
            if type(count) is not int or not 0 < count <= 512 or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("previous usable retrieval scores are invalid")
        by_request[score["request_id"]].append(score)
    selections = read(previous_run / "retrieval-selections.jsonl")
    selected = {row["request_id"]: row for row in selections}
    if len(selected) != len(selections) or set(selected) != set(by_request):
        raise ValueError("previous retrieval selection coverage differs")
    for rid, group in by_request.items():
        ordered = [row["block_id"] for row in sorted((s for s in group if s["status"] == "ok"),
                                                    key=lambda s: (-s["raw_logit"], s["block_id"]))]
        if selected[rid].get("ordered_block_ids") != ordered or any(bid not in blocks for bid in ordered):
            raise ValueError("previous raw-logit block ordering differs")
    cache = {}
    for stored_record in read(previous_run / "jev-classification-options.jsonl"):
        record = restore_choice_score_order(stored_record)
        key = JEV.request_key(record["question"], record["state"], record["choices"])
        if (record.get("cache_key") != key or not JEV._same_pins(record.get("pins"))
                or (record.get("model_id"), record.get("model_revision"), record.get("precision")) !=
                (JEV.MODEL_ID, JEV.MODEL_REVISION, "fp32")):
            raise ValueError("previous JEV cache identity differs")
        JEV._validate_scores(record, record)
        cache.setdefault(key, record)
    nli_cache = {}
    for record in read(previous_run / "nli-classification-options.jsonl"):
        key = NLI.pair_key(record["premise"], record["hypothesis"])
        if (record.get("cache_key") != key or
                (record.get("model_id"), record.get("model_revision"), record.get("precision")) !=
                (NLI.MODEL_ID, NLI.MODEL_REVISION, "fp32")):
            raise ValueError("previous NLI cache identity differs")
        NLI._probabilities(record)
        nli_cache.setdefault(key, record)
    return selected, cache, nli_cache, summary


def evidence_requests(requests, documents, blocks, rankings):
    rows, skips = [], {}
    for request in requests:
        if request["direction"] != V1.FORWARD:
            continue
        block_ids = rankings.get(request["request_id"], {}).get("ordered_block_ids", [])[:TOP_K]
        if not block_ids:
            skips[request["request_id"]] = "no_usable_retrieved_block"
            continue
        evidence_label_to_block_id = {"記述" + str(index + 1): bid for index, bid in enumerate(block_ids)}
        document = documents[request["target_document_id"]]
        state = ("現在のAU商品ID：" + str(document.get("product_id", document["document_id"]))
                 + "\n現在の商品名：" + document["title"]["text"]
                 + "\n現在の選択行：\n" + request["target_selected_axes_text"])
        rows.append({"id": request["request_id"], "request_sha256": request["request_sha256"],
                     "question": EVIDENCE_QUESTION.format(axis_name=request["axis_name"]), "state": state,
                     "choices": [{"label": label, "description": blocks[bid]["model_passage"]}
                                 for label, bid in evidence_label_to_block_id.items()]
                                + [{"label": EVIDENCE_UNKNOWN, "description": EVIDENCE_UNKNOWN_DESCRIPTION}],
                     "candidate_block_ids": block_ids, "evidence_label_to_block_id": evidence_label_to_block_id,
                     "all_original_option_values": deepcopy(request["option_values"]),
                     "direction": request["direction"], "target_document_id": request["target_document_id"]})
    return rows, skips


def choose_evidence(requests, records, rankings, documents, blocks, initial_skips):
    by_id = {r["id"]: r for r in records}
    selections, skips, decisions = {}, dict(initial_skips), []
    for request in requests:
        if request["direction"] != V1.FORWARD:
            continue
        record, winner, reason, tie = by_id.get(request["request_id"]), None, None, False
        candidate_ids = rankings.get(request["request_id"], {}).get("ordered_block_ids", [])[:TOP_K]
        label_map = {"記述" + str(index + 1): bid for index, bid in enumerate(candidate_ids)}
        if record is not None:
            expected_choices = [{"label": label, "description": blocks[bid]["model_passage"]}
                                for label, bid in label_map.items()]
            expected_choices.append({"label": EVIDENCE_UNKNOWN, "description": EVIDENCE_UNKNOWN_DESCRIPTION})
            if (record.get("candidate_block_ids") != candidate_ids
                    or record.get("evidence_label_to_block_id") != label_map
                    or record.get("choices") != expected_choices):
                raise ValueError("evidence ordinal labels differ from frozen candidate block ordering")
        if record is None:
            reason = skips.get(request["request_id"], "evidence_scores_missing")
        elif record.get("status") != "ok":
            reason = "evidence_" + record.get("status", "error")
        else:
            maximum = max(record["logits"].values())
            tie = sum(value == maximum for value in record["logits"].values()) != 1
            raw = record["argmax_label"]
            if tie:
                reason = "evidence_score_tie"
            elif raw == EVIDENCE_UNKNOWN:
                reason = "evidence_unknown_control"
            elif raw not in label_map:
                raise ValueError("evidence winner was not a frozen top-ten candidate")
            elif V1.block_refs(request, documents, blocks[label_map[raw]]) is None:
                reason = "literal_description_reference_invalid"
            else:
                winner, reason = label_map[raw], "unique_current_row_evidence_choice"
        decision = {"request_id": request["request_id"], "request_sha256": request["request_sha256"],
                    "block_id": winner, "reason": reason, "tie": tie, "candidate_record": record,
                    "scope_status": "unproved", "production_adoption_allowed": False}
        decisions.append(decision)
        if winner is not None:
            selections[request["request_id"]] = {"request_id": request["request_id"], "block_id": winner,
                                                 "reason": reason, "semantic_evidence_choice": True,
                                                 "original_retrieval": rankings[request["request_id"]]}
        else:
            skips[request["request_id"]] = reason
    return selections, skips, decisions


def score_jev(rows, call, cache, model_dir, threads, batch_size, output_dir, prefix, progress):
    unique = FULL.unique_inputs(rows, lambda r: JEV.request_key(r["question"], r["state"], r["choices"]))
    runtime, pins = {}, None
    original_keys = set(cache)
    with (output_dir / (prefix + "-unique-scores.jsonl")).open("x", encoding="utf-8") as handle:
        for offset in range(0, len(unique), V1.JEV_CHUNK_REQUESTS):
            chunk = unique[offset:offset + V1.JEV_CHUNK_REQUESTS]
            result = call(chunk, model_dir=model_dir, threads=threads, batch_size=batch_size, cache=cache)
            if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                raise ValueError("JEV output identity differs")
            cache.update({r["cache_key"]: r for r in result["records"]})
            FULL._append(handle, result["records"])
            FULL._accumulate(runtime, result["runtime"])
            pins = result["pins"]
            progress.emit(stage=prefix, completed_groups=min(offset + len(chunk), len(unique)), total_groups=len(unique))
    records = call(rows, model_dir=model_dir, threads=threads, batch_size=batch_size, cache=cache)["records"] if rows else []
    write_rows(output_dir / (prefix + "-classification-options.jsonl"), records)
    runtime.update(unique_request_count=len(unique), request_count=len(rows),
                   previously_cached_unique_count=sum(JEV.request_key(r["question"], r["state"], r["choices"]) in original_keys for r in unique))
    return records, runtime, pins


def run(prepared_dir, previous_run, output_dir, jev_model_dir=JEV.DEFAULT_MODEL,
        nli_model_dir=NLI.DEFAULT_MODEL, threads=4, batch_size=16, *, jev_runner=None, nli_runner=None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != 4 or batch_size != 16:
        raise ValueError("frozen CPU settings are four threads and batch size 16")
    started = time.perf_counter()
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    requests, documents, bindings, baseline, blocks, retrieval, manifest = V1.load_inputs(prepared_dir)
    rankings, cache, nli_cache, previous_summary = load_previous(previous_run, prepared_dir, requests, blocks, retrieval)
    initial_cache_count, initial_nli_cache_count = len(cache), len(nli_cache)
    evidence_inputs, initial_skips = evidence_requests(requests, documents, blocks, rankings)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", *V1.SOURCE_NAMES):
        shutil.copyfile(prepared_dir / name, output_dir / ("source-" + name))
    for name in PREVIOUS_NAMES:
        shutil.copyfile(previous_run / name, output_dir / ("previous-" + name))
    shutil.copyfile(prepared_dir / "baseline-predictions.jsonl", output_dir / "baseline-predictions.jsonl")
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    reranker_manifest = HERE / "manifests/reranker.json"
    shutil.copyfile(reranker_manifest, code_dir / "reranker.json")
    write_rows(output_dir / "evidence-choice-model-requests.jsonl", evidence_inputs)
    freeze = {"schema_version": "scoped-html-evidence-choice-v2", "labels_read": False, "labels_in_model_inputs": False,
              "errors_only": True, "development_only": True, "independent_test": False, "synthetic_data": False,
              "scope_status": "unproved", "production_adoption_allowed": False, "request_count": len(requests),
              "forward_request_count": sum(r["direction"] == V1.FORWARD for r in requests), "body_model_input": True,
              "selected_value_separately_injected": False, "all_raw_original_options_preserved": True,
              "top_k": TOP_K, "new_reranker_inference": False, "evidence_choice_model": "same pinned JEV315M reused",
              "retrieval_policy": "reuse frozen raw-logit ordering; semantic JEV selects one of the first ten complete blocks plus unknown",
              "verification_policy": "semantic evidence choice then JEV unique desired value; verified adds selected candidate raw NLI support unique argmax",
              "scope_is_not_proved_by_choice": True, "input_length_policy": "all inputs untruncated; any JEV candidate >512 yields unknown; NLI overlong/errors unknown",
              "threads": threads, "batch_size": batch_size, "device": "cpu", "jev_precision": "fp32", "nli_precision": "fp32",
              "reverse_requests": "exact baseline copy", "evidence_unknown_control_label": EVIDENCE_UNKNOWN,
              "evidence_label_policy": "short ordinal labels 記述1..10; opaque source block IDs only in metadata",
              "evidence_question_template": EVIDENCE_QUESTION, "evidence_unknown_description": EVIDENCE_UNKNOWN_DESCRIPTION,
              "value_question_template": V1.QUESTION, "value_choice_description_template": V1.DESCRIPTION,
              "value_unknown_description_template": V1.UNKNOWN_DESCRIPTION, "hypothesis_template": V1.HYPOTHESIS_TEMPLATE,
              "jev_chunk_requests": V1.JEV_CHUNK_REQUESTS, "nli_chunk_pairs": V1.NLI_CHUNK_PAIRS,
              "nli_policy": "only value-JEV desired winners; all their raw options are hypotheses; no relative normalization",
              "input_sha256": {n: sha(prepared_dir / n) for n in ("manifest.json", *V1.SOURCE_NAMES)},
              "previous_run_sha256": {n: sha(previous_run / n) for n in PREVIOUS_NAMES},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()}, "jev_pin": JEV._pins(),
              "nli_pin": previous_summary["nli_pin"], "reranker_pin": previous_summary["reranker_pin"],
              "initial_jev_cache_count": initial_cache_count, "initial_nli_cache_count": initial_nli_cache_count}
    write_json(output_dir / "freeze.json", freeze)
    jev_call, nli_call = jev_runner or JEV.run_choices, nli_runner or NLI.run_pairs
    with FULL.Progress(output_dir) as progress:
        # The shared context retains the one loaded JEV model across both phases.
        with (FULL.reuse_jev_loader() if jev_runner is None and evidence_inputs else nullcontext()):
            evidence_records, evidence_runtime, evidence_pins = score_jev(
                evidence_inputs, jev_call, cache, jev_model_dir, threads, batch_size, output_dir, "evidence-choice", progress)
            selections, skips, evidence_decisions = choose_evidence(requests, evidence_records, rankings, documents, blocks, initial_skips)
            write_rows(output_dir / "evidence-choice-predictions.jsonl", evidence_decisions)
            write_rows(output_dir / "evidence-choice-selections.jsonl", selections.values())
            choices, refs, exclusions = V1.model_requests(requests, documents, blocks, selections)
            exclusions.update(skips)
            write_rows(output_dir / "model-requests.jsonl", choices)
            write_rows(output_dir / "model-skips.jsonl", [{"request_id": rid, "reason": reason} for rid, reason in exclusions.items()])
            value_records, value_runtime, value_pins = score_jev(
                choices, jev_call, cache, jev_model_dir, threads, batch_size, output_dir, "value-choice", progress)
        write_rows(output_dir / "jev-classification-options.jsonl", value_records)
        by_jev = {r["id"]: r for r in value_records}
        eligible = {r["request_id"] for r in requests if r["request_id"] in by_jev
                    and FULL.jev_eligibility(by_jev[r["request_id"]])[0]
                    and by_jev[r["request_id"]]["argmax_label"] == r["selected_value"]}
        original_bindings = {rid: old["source_task_bindings"] for rid, old in baseline.items()}
        pairs = V1.nli_pairs_for(requests, choices, refs, original_bindings, eligible)
        write_rows(output_dir / "model-nli-pairs.jsonl", pairs)
        unique_pairs = FULL.unique_inputs(pairs, lambda p: NLI.pair_key(p["premise"], p["hypothesis"]))
        nli_runtime, nli_pins = {}, None
        previous_nli_keys = set(nli_cache)
        with (FULL.reuse_nli_factories() if nli_runner is None and pairs else nullcontext()):
            with (output_dir / "nli-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
                for offset in range(0, len(unique_pairs), V1.NLI_CHUNK_PAIRS):
                    chunk = unique_pairs[offset:offset + V1.NLI_CHUNK_PAIRS]
                    def cached_call(rows, **kwargs):
                        return nli_call(rows, cache=nli_cache, **kwargs)
                    result = FULL.score_nli_chunk(cached_call, chunk, nli_model_dir, threads, batch_size)
                    if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                        raise ValueError("NLI output identity differs")
                    FULL._append(handle, result["records"])
                    nli_cache.update({r["cache_key"]: r for r in result["records"]})
                    FULL._accumulate(nli_runtime, result["runtime"])
                    nli_pins = result["pins"]
                    progress.emit(stage="nli", completed_pairs=min(offset + len(chunk), len(unique_pairs)), total_pairs=len(unique_pairs))
            nli_records = nli_call(pairs, model_dir=nli_model_dir, threads=threads, batch_size=batch_size, cache=nli_cache)["records"] if pairs else []
        write_rows(output_dir / "nli-classification-options.jsonl", nli_records)
        grouped = defaultdict(list)
        for record in nli_records:
            grouped[record["request_id"]].append(record)
        evidence_by_id = {r["request_id"]: r for r in evidence_decisions}
        predictions = {}
        for verified in (False, True):
            name = "structured-verified" if verified else "structured-jev"
            rows = []
            for request in requests:
                rid = request["request_id"]
                row = V1.prediction(request, baseline[rid], by_jev.get(rid), refs.get(rid), exclusions.get(rid),
                                    selections.get(rid), grouped[rid], verified)
                if request["direction"] == V1.FORWARD:
                    row["evidence_choice_prediction"] = evidence_by_id[rid]
                    row["task_structure_version"] = "scoped-html-evidence-choice-v2"
                rows.append(row)
            write_rows(output_dir / (name + "-predictions.jsonl"), rows)
            predictions[name] = dict(Counter(row["state"] for row in rows))
        for name, expected in freeze["input_sha256"].items():
            if sha(prepared_dir / name) != expected:
                raise ValueError("source input changed during inference: " + name)
        for name, expected in freeze["previous_run_sha256"].items():
            if sha(previous_run / name) != expected:
                raise ValueError("previous run changed during inference: " + name)
        for name, expected in freeze["code_sha256"].items():
            path = reranker_manifest if name == "reranker.json" else HERE / name
            if sha(path) != expected:
                raise ValueError("source implementation changed during inference: " + name)
        progress.emit(stage="complete", request_count=len(requests))
    summary = {**freeze, "prediction_states": predictions, "total_seconds": time.perf_counter() - started,
               "evidence_choice_runtime": evidence_runtime, "value_choice_runtime": value_runtime, "nli_runtime": nli_runtime,
               "verified_evidence_choice_pin": evidence_pins, "verified_value_choice_pin": value_pins, "verified_nli_pin": nli_pins,
               "nli_pair_count": len(pairs), "unique_nli_pair_count": len(unique_pairs),
               "previously_cached_unique_nli_count": sum(NLI.pair_key(p["premise"], p["hypothesis"]) in previous_nli_keys for p in unique_pairs),
               "evidence_choice_reasons": dict(Counter(r["reason"] for r in evidence_decisions)),
               "model_skips": dict(Counter(exclusions.values())), "input_and_code_sha256_verified_after_inference": True,
               "previous_run_sha256_verified_after_inference": True,
               "baseline_exact_file_copy": sha(output_dir / "baseline-predictions.jsonl") == sha(prepared_dir / "baseline-predictions.jsonl"),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jev-model-dir", type=Path, default=JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    run(**vars(parser.parse_args()))
