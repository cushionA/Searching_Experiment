#!/usr/bin/env python3
"""Share product-global asymmetric conditions, then ensemble source blocks.

Only requests named in the user's invariance contract are shared. Selected AU
row text and wanted values never enter the model context. Current-source quotes
and semantic applicability remain distinct; all results are development-only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time

import product_condition_sharing_v1 as SHARING
import trial_scoped_html_evidence_v1 as V1

FULL, JEV, NLI = V1.FULL, V1.JEV, V1.NLI
HERE, ROOT = V1.HERE, V1.ROOT
read, sha, digest = V1.read, V1.sha, V1.digest
write_json, write_rows = V1.write_json, V1.write_rows
TOP_K, MIN_VOTES = 3, 2
VARIANTS = ("product-top1-jev", "product-top3-jev", "product-top3-verified")
UNKNOWN_DESCRIPTION = "この商品の条件に該当する値は、商品名と引用された商品説明から特定できない。"
CONTENT_VERSION = "product-condition-models-v1/title+html-blocks-v1/top1+top3"
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, "product_condition_sharing_v1.py", *V1.CODE_NAMES)))


def verify_contract_sources(contract, prepared_dir):
    sources = contract.get("source_sha256", {}).get("prepared")
    required = {"manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl"}
    allowed = {"manifest.json", *V1.SOURCE_NAMES}
    if not isinstance(sources, dict) or not required <= set(sources) or not set(sources) <= allowed:
        raise ValueError("eligibility contract requires pinned prepared manifest/request/document/task sources")
    for name, expected in sources.items():
        if sha(prepared_dir / name) != expected:
            raise ValueError("eligibility contract prepared source SHA256 differs: " + name)


def model_identity():
    return {"model_id": "sku-product-global-jev-nli-reranker", "revision": CONTENT_VERSION,
            "jev": JEV._pins(), "nli": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
             "parameter_count": NLI.FP32_PARAMETER_COUNT, "files_sha256": NLI.NLI_FILE_SHA256},
            "reranker": json.loads((HERE / "manifests/reranker.json").read_text(encoding="utf-8")),
            "variants": list(VARIANTS), "top_k": TOP_K, "minimum_votes": MIN_VOTES,
            "verification": "same block JEV winner and raw NLI support unique argmax; at least two blocks"}


def query_for(group):
    return ("現在の商品名：" + group["title"] + "\n確認する軸：" + group["axis_name"]
            + "\n元の選択肢：\n" + "\n".join(group["option_values"]))


def state_for(group, block):
    return ("現在の商品名：" + group["title"]
            + "\nこのページの商品説明（表の文脈とリンク先を含む）：\n" + block["model_passage"])


def product_refs(group, documents, block):
    request = {"target_document_id": group["target_document_id"]}
    refs = block.get("quote_refs")
    if (not isinstance(refs, list) or not refs or any(not isinstance(ref, dict)
            or ref.get("field") != "description" or not V1.verified_ref(ref, request, documents) for ref in refs)):
        return None
    document = documents[group["target_document_id"]]
    title_refs = [FULL.quote_ref(document, "title", document["title"]["text"])] if document["title"]["text"] else []
    return [*title_refs, *deepcopy(refs)]


def retrieval_requests(groups, documents, blocks):
    rows, rejected = [], []
    for group in groups:
        for block in blocks.values():
            if block["target_document_id"] != group["target_document_id"]:
                continue
            refs = product_refs(group, documents, block)
            if refs is None:
                rejected.append({"group_id": group["group_id"], "block_id": block["block_id"],
                                 "reason": "literal_description_reference_invalid"})
                continue
            rows.append({"id": group["group_id"] + ":retrieval:" + block["block_id"],
                         "request_id": group["group_id"], "group_id": group["group_id"],
                         "group_sha256": group["group_sha256"], "block_id": block["block_id"],
                         "target_document_id": group["target_document_id"], "query": query_for(group),
                         "passage": block["model_passage"], "quote_refs": refs})
    return rows, rejected


def classification_requests(groups, blocks, rankings):
    choices = []
    for group in groups:
        model_request = group["model_request"]
        raw_values = [c["label"] for c in model_request["choices"]]
        if len(raw_values) != len(group["option_values"]) + 1 or raw_values[:-1] != group["option_values"]:
            raise ValueError("sharing model must preserve ordered whole options and one unknown")
        unknown = raw_values[-1]
        for index, bid in enumerate(rankings.get(group["group_id"], {}).get("ordered_block_ids", [])[:TOP_K]):
            candidates = deepcopy(model_request["choices"])
            candidates[-1]["description"] = UNKNOWN_DESCRIPTION
            choices.append({"id": group["group_id"] + ":block:" + str(index), "group_id": group["group_id"],
                            "group_sha256": group["group_sha256"], "block_id": bid, "block_rank": index + 1,
                            "question": model_request["question"], "state": state_for(group, blocks[bid]),
                            "choices": candidates, "unknown_control_label": unknown,
                            "all_original_option_values": deepcopy(group["option_values"])})
    return choices


def pairs_for(choices, groups_by_id, documents, blocks):
    pairs = []
    for choice in choices:
        group = groups_by_id[choice["group_id"]]
        refs = product_refs(group, documents, blocks[choice["block_id"]])
        if refs is None:
            raise ValueError("scored block lost its literal source reference")
        for index, value in enumerate(group["option_values"]):
            pairs.append({"id": choice["id"] + ":nli:" + str(index), "group_id": group["group_id"],
                          "group_sha256": group["group_sha256"], "block_id": choice["block_id"],
                          "block_rank": choice["block_rank"], "option_index": index, "option_value": value,
                          "premise": choice["state"], "hypothesis": V1.HYPOTHESIS_TEMPLATE.format(
                              axis_name=group["axis_name"], selected_value=value), "quote_refs": refs,
                          "scope_status": "unproved", "production_adoption_allowed": False})
    return pairs


def raw_winner(record, options):
    if record.get("status") != "ok":
        return None
    values = record["logits"].values()
    maximum = max(values)
    if sum(value == maximum for value in record["logits"].values()) != 1:
        return None
    winner = record["argmax_label"]
    return winner if winner in options else None


def deduplicate_refs(refs):
    unique = {}
    for ref in refs:
        unique.setdefault(digest(ref), ref)
    return list(unique.values())


def group_results(groups, jev_records, nli_records, documents, blocks, variant):
    if variant not in VARIANTS:
        raise ValueError("unknown frozen product variant")
    by_group = defaultdict(list)
    by_pair = {}
    for record in jev_records:
        by_group[record["group_id"]].append(record)
    for record in nli_records:
        key = (record["group_id"], record["block_id"], record["option_value"])
        if key in by_pair:
            raise ValueError("duplicate group/block/option NLI result")
        by_pair[key] = record
    output = {}
    for group in groups:
        records = sorted(by_group[group["group_id"]], key=lambda r: r["block_rank"])[:TOP_K]
        winners = {r["block_id"]: raw_winner(r, group["option_values"]) for r in records}
        votes = Counter(v for v in winners.values() if v is not None)
        winner, used_blocks, reason = None, [], "no_unique_majority"
        if variant == "product-top1-jev":
            winner = winners.get(records[0]["block_id"]) if records and records[0]["block_rank"] == 1 else None
            used_blocks = [records[0]["block_id"]] if winner else []
            reason = "top1_unique_raw_value" if winner else "top1_unknown_tie_or_error"
        else:
            leaders = [value for value, count in votes.items() if count >= MIN_VOTES]
            if len(leaders) == 1:
                winner = leaders[0]
                used_blocks = [bid for bid, value in winners.items() if value == winner]
                reason = "two_or_more_blocks_agree"
                if variant == "product-top3-verified":
                    used_blocks = [bid for bid in used_blocks if V1.unique_raw_support(by_pair.get((group["group_id"], bid, winner)))]
                    if len(used_blocks) < MIN_VOTES:
                        winner, used_blocks, reason = None, [], "fewer_than_two_joint_jev_nli_supports"
                    else:
                        reason = "two_or_more_same_block_jev_and_raw_nli_supports"
        refs = []
        for bid in used_blocks:
            current = product_refs(group, documents, blocks[bid])
            if current is None:
                winner, refs, used_blocks, reason = None, [], [], "literal_description_reference_invalid"
                break
            refs.extend(current)
        output[group["group_id"]] = {"group_id": group["group_id"], "group_sha256": group["group_sha256"],
                 "status": "ok" if winner else "unknown", "winning_value": winner,
                 "quote_refs": deduplicate_refs(refs), "reason": reason, "variant": variant,
                 "block_votes": dict(votes), "block_winners": winners, "supporting_block_ids": used_blocks,
                 "scope_status": "unproved", "production_adoption_allowed": False}
    return output


def score_jev(choices, call, model_dir, threads, batch_size, output_dir, progress):
    unique = FULL.unique_inputs(choices, lambda r: JEV.request_key(r["question"], r["state"], r["choices"]))
    cache, runtime, pins = {}, {}, None
    with (FULL.reuse_jev_loader() if call is JEV.run_choices else nullcontext()):
        with (output_dir / "jev-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique), V1.JEV_CHUNK_REQUESTS):
                chunk = unique[offset:offset + V1.JEV_CHUNK_REQUESTS]
                result = call(chunk, model_dir=model_dir, threads=threads, batch_size=batch_size)
                if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                    raise ValueError("JEV output identity differs")
                cache.update({r["cache_key"]: r for r in result["records"]})
                FULL._append(handle, result["records"])
                FULL._accumulate(runtime, result["runtime"])
                pins = result["pins"]
                progress.emit(stage="product-jev", completed_groups=min(offset + len(chunk), len(unique)), total_groups=len(unique))
        records = call(choices, model_dir=model_dir, threads=threads, batch_size=batch_size, cache=cache)["records"] if choices else []
    write_rows(output_dir / "jev-classification-options.jsonl", records)
    runtime.update(request_count=len(choices), unique_request_count=len(unique),
                   candidate_pair_count=sum(len(c["choices"]) for c in choices),
                   unique_candidate_pair_count=sum(len(c["choices"]) for c in unique))
    return records, runtime, pins


def run(prepared_dir, eligible_requests, output_dir, reranker_model_dir=V1.DEFAULT_RERANKER,
        jev_model_dir=JEV.DEFAULT_MODEL, nli_model_dir=NLI.DEFAULT_MODEL, threads=4, batch_size=16,
        *, reranker_factory=None, jev_runner=None, nli_runner=None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != 4 or batch_size != 16:
        raise ValueError("frozen CPU settings are four threads and batch size 16")
    started = time.perf_counter()
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    requests, documents, bindings, baseline, blocks, source_retrieval, manifest = V1.load_inputs(prepared_dir)
    contract = json.loads(eligible_requests.read_text(encoding="utf-8"))
    verify_contract_sources(contract, prepared_dir)
    identity = model_identity()
    original_bindings = {rid: p["source_task_bindings"] for rid, p in baseline.items()}
    plan = SHARING.build_groups(requests, documents, original_bindings, model_identity=identity,
                                content_version=CONTENT_VERSION, invariance_contract=contract)
    groups = plan["groups"]
    groups_by_id = {group["group_id"]: group for group in groups}
    retrieval, rejected = retrieval_requests(groups, documents, blocks)
    eligible_ids = set(plan["request_to_group"])
    old_choices = [c for c in FULL.choice_requests(requests, documents) if c["id"] in eligible_ids]
    old_unique = FULL.unique_inputs(old_choices, lambda r: JEV.request_key(r["question"], r["state"], r["choices"]))
    old_retrieval = [row for row in source_retrieval if row["request_id"] in eligible_ids]
    cost_baseline = {"eligible_row_request_count": len(eligible_ids),
                     "old_eligible_retrieval_pair_count": len(old_retrieval),
                     "old_eligible_unique_retrieval_pair_count": len(FULL.unique_inputs(old_retrieval, V1.retrieval_key)),
                     "old_eligible_unique_actual_row_jev_count": len(old_unique),
                     "old_eligible_unique_actual_row_jev_candidate_pairs": sum(len(c["choices"]) for c in old_unique),
                     "shared_product_group_count": len(groups), "shared_retrieval_pair_count": len(retrieval),
                     "shared_unique_retrieval_pair_count": len(FULL.unique_inputs(retrieval, V1.retrieval_key)),
                     "top1_jev_block_group_upper_bound": len(groups), "top3_jev_block_group_upper_bound": len(groups) * TOP_K,
                     "top3_nli_pair_upper_bound": sum(TOP_K * len(g["option_values"]) for g in groups)}
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", *V1.SOURCE_NAMES):
        shutil.copyfile(prepared_dir / name, output_dir / ("source-" + name))
    shutil.copyfile(eligible_requests, output_dir / "source-eligible-requests.json")
    shutil.copyfile(eligible_requests, output_dir / "sharing-contract.json")
    shutil.copyfile(prepared_dir / "baseline-predictions.jsonl", output_dir / "baseline-predictions.jsonl")
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    reranker_manifest = HERE / "manifests/reranker.json"
    shutil.copyfile(reranker_manifest, code_dir / "reranker.json")
    write_json(output_dir / "sharing-plan.json", plan)
    write_rows(output_dir / "product-groups.jsonl", groups)
    write_rows(output_dir / "retrieval-model-requests.jsonl", retrieval)
    write_rows(output_dir / "retrieval-rejected-blocks.jsonl", rejected)
    freeze = {"schema_version": "product-condition-models-v1", "labels_read": False, "labels_in_model_inputs": False,
              "errors_only": True, "development_only": True, "independent_test": False, "synthetic_data": False,
              "scope_status": "unproved", "production_adoption_allowed": False, "invariance_basis": "user_asserted",
              "request_sharing_does_not_prove_description_scope": True, "invariance_contract": contract,
              "eligible_request_count": len(eligible_ids), "passthrough_request_count": len(requests) - len(eligible_ids),
              "request_count": len(requests), "group_count": len(groups), "selected_axes_model_input": False,
              "wanted_value_separately_injected": False, "all_raw_original_options_preserved": True,
              "product_model_context": "current title plus original HTML block with context/links; no selected row",
              "variants": list(VARIANTS), "top_k": TOP_K, "minimum_votes": MIN_VOTES,
              "majority_policy": "at least two distinct blocks select the same whole raw option; unknown/error/tie cast no vote",
              "verification_policy": "at least two of those same JEV-winning blocks also have raw three-class NLI support unique argmax",
              "nli_policy": "all raw options on all selected top-three blocks; wanted value does not gate inference",
              "input_length_policy": "never truncate; reranker >512 skips block; JEV/NLI overlong/error unknown",
              "noneligible_requests": "exact baseline passthrough", "eligible_quote_fields": ["title", "description"],
              "threads": threads, "batch_size": batch_size, "device": "cpu", "jev_precision": "fp32", "nli_precision": "fp32",
              "reranker_precision": "int8", "content_version": CONTENT_VERSION, "model_identity": identity,
              "unknown_description": UNKNOWN_DESCRIPTION, "old_actual_row_cost_baseline": cost_baseline,
              "sharing_savings": plan["savings"], "sharing_plan_sha256": sha(output_dir / "sharing-plan.json"),
              "eligible_requests_sha256": sha(eligible_requests),
              "input_sha256": {name: sha(prepared_dir / name) for name in ("manifest.json", *V1.SOURCE_NAMES)},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()}}
    write_json(output_dir / "freeze.json", freeze)
    with FULL.Progress(output_dir) as progress:
        progress.emit(stage="product-reranker-load")
        load_started = time.perf_counter()
        model = (reranker_factory or V1.RERANKER.Model)(reranker_model_dir, threads=threads, verify_files=True)
        if model.metadata != identity["reranker"]:
            raise ValueError("verified reranker identity differs")
        load_seconds = time.perf_counter() - load_started
        rankings, reranker_runtime = V1.rank_retrieval(retrieval, model, output_dir, progress, batch_size)
        reranker_runtime["model_load_and_file_verification_seconds"] = load_seconds
        del model
        choices = classification_requests(groups, blocks, rankings)
        write_rows(output_dir / "model-requests.jsonl", choices)
        jev_records, jev_runtime, jev_pins = score_jev(choices, jev_runner or JEV.run_choices, jev_model_dir,
                                                     threads, batch_size, output_dir, progress)
        pairs = pairs_for(choices, groups_by_id, documents, blocks)
        write_rows(output_dir / "model-nli-pairs.jsonl", pairs)
        unique_pairs = FULL.unique_inputs(pairs, lambda p: NLI.pair_key(p["premise"], p["hypothesis"]))
        nli_call, nli_cache, nli_runtime, nli_pins = nli_runner or NLI.run_pairs, {}, {}, None
        with (FULL.reuse_nli_factories() if nli_runner is None and pairs else nullcontext()):
            with (output_dir / "nli-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
                for offset in range(0, len(unique_pairs), V1.NLI_CHUNK_PAIRS):
                    chunk = unique_pairs[offset:offset + V1.NLI_CHUNK_PAIRS]
                    result = FULL.score_nli_chunk(nli_call, chunk, nli_model_dir, threads, batch_size)
                    if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                        raise ValueError("NLI output identity differs")
                    FULL._append(handle, result["records"])
                    nli_cache.update({r["cache_key"]: r for r in result["records"]})
                    FULL._accumulate(nli_runtime, result["runtime"])
                    nli_pins = result["pins"]
                    progress.emit(stage="product-nli", completed_pairs=min(offset + len(chunk), len(unique_pairs)), total_pairs=len(unique_pairs))
            nli_records = nli_call(pairs, model_dir=nli_model_dir, threads=threads, batch_size=batch_size, cache=nli_cache)["records"] if pairs else []
        write_rows(output_dir / "nli-classification-options.jsonl", nli_records)
        prediction_states = {}
        for variant in VARIANTS:
            result = group_results(groups, jev_records, nli_records, documents, blocks, variant)
            write_rows(output_dir / (variant + "-group-results.jsonl"), result.values())
            predictions = SHARING.fan_out(plan, result, baseline)
            write_rows(output_dir / (variant + "-predictions.jsonl"), predictions)
            prediction_states[variant] = dict(Counter(p["state"] for p in predictions))
        for name, expected in freeze["input_sha256"].items():
            if sha(prepared_dir / name) != expected:
                raise ValueError("source input changed during inference: " + name)
        if sha(eligible_requests) != freeze["eligible_requests_sha256"]:
            raise ValueError("eligibility contract changed during inference")
        for name, expected in freeze["code_sha256"].items():
            path = reranker_manifest if name == "reranker.json" else HERE / name
            if sha(path) != expected:
                raise ValueError("source implementation changed during inference: " + name)
        progress.emit(stage="complete", request_count=len(requests))
    summary = {**freeze, "prediction_states": prediction_states, "total_seconds": time.perf_counter() - started,
               "reranker_runtime": reranker_runtime, "jev_runtime": jev_runtime, "nli_runtime": nli_runtime,
               "verified_jev_pin": jev_pins, "verified_nli_pin": nli_pins,
               "actual_top3_jev_block_group_count": len(choices), "actual_nli_pair_count": len(pairs),
               "actual_unique_nli_pair_count": len(unique_pairs), "input_and_code_sha256_verified_after_inference": True,
               "baseline_exact_file_copy": sha(output_dir / "baseline-predictions.jsonl") == sha(prepared_dir / "baseline-predictions.jsonl"),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--eligible-requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reranker-model-dir", type=Path, default=V1.DEFAULT_RERANKER)
    parser.add_argument("--jev-model-dir", type=Path, default=JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    run(**vars(parser.parse_args()))
