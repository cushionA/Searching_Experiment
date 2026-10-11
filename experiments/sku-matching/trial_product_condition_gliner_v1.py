#!/usr/bin/env python3
"""Offline GLiNER comparator on the completed, frozen product-only blocks.

No retrieval, JEV inference, source row changes, labels, or threshold search.
All original options are classified before immutable group decisions fan out.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import shutil
import time

import trial_generic_gliner_choice_v1 as GL
import trial_product_condition_models_v1 as PRODUCT

FULL, SHARING = PRODUCT.FULL, PRODUCT.SHARING
HERE, ROOT = PRODUCT.HERE, PRODUCT.ROOT
read, sha, digest = PRODUCT.read, PRODUCT.sha, PRODUCT.digest
write_json, write_rows = PRODUCT.write_json, PRODUCT.write_rows
PARAMETER_COUNT, CONFIDENCE, TOP_K, MIN_VOTES = 287355159, .5, 3, 2
INSTRUCTION = ("商品名と引用された商品説明から、現在販売するこの商品の条件に当てはまる選択値を1つ選びます。"
               "別商品の購入案内や検索語一覧は現在の商品仕様を証明しません。"
               "記載が不足したり適用範囲が不明なら不明を選びます。")
VARIANTS = ("product-top1-gliner", "product-top3-gliner", "product-top3-jev-gliner-majority-agree",
            "product-top3-jev-gliner-block-agree")
SOURCE_NAMES = ("summary.json", "freeze.json", "sharing-plan.json", "sharing-contract.json", "product-groups.jsonl",
                "model-requests.jsonl", "jev-classification-options.jsonl", "baseline-predictions.jsonl",
                "source-documents.jsonl", "source-blocks.jsonl", "source-requests.jsonl", "source-tasks.jsonl",
                "retrieval-selections.jsonl", "retrieval-scores.jsonl")
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, *PRODUCT.CODE_NAMES)))


def load_source(source_run):
    summary = json.loads((source_run / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "product-condition-models-v1" or summary.get("labels_read") is not False
            or summary.get("selected_axes_model_input") is not False or summary.get("wanted_value_separately_injected") is not False
            or summary.get("input_and_code_sha256_verified_after_inference") is not True):
        raise ValueError("source must be the completed frozen product-only trial")
    for name in SOURCE_NAMES[1:]:
        if summary.get("output_sha256", {}).get(name) != sha(source_run / name):
            raise ValueError("completed product source SHA256 differs: " + name)
    for name, expected in summary["code_sha256"].items():
        path = HERE / "manifests/reranker.json" if name == "reranker.json" else HERE / name
        if sha(path) != expected:
            raise ValueError("frozen product implementation differs: " + name)
    if summary["model_identity"] != PRODUCT.model_identity():
        raise ValueError("source product model pins differ")
    plan = json.loads((source_run / "sharing-plan.json").read_text(encoding="utf-8"))
    if plan["plan_sha256"] != SHARING.digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("source sharing plan semantic SHA differs")
    groups = read(source_run / "product-groups.jsonl")
    if groups != plan["groups"]:
        raise ValueError("source product groups differ from immutable plan")
    documents = {d["document_id"]: d for d in read(source_run / "source-documents.jsonl")}
    blocks = {b["block_id"]: b for b in read(source_run / "source-blocks.jsonl")}
    rankings = {r["request_id"]: r for r in read(source_run / "retrieval-selections.jsonl")}
    expected = PRODUCT.classification_requests(groups, blocks, rankings)
    choices = read(source_run / "model-requests.jsonl")
    if choices != expected:
        raise ValueError("source model requests differ from product-only block renderer")
    jev = read(source_run / "jev-classification-options.jsonl")
    if len(jev) != len(choices) or [r["id"] for r in jev] != [r["id"] for r in choices]:
        raise ValueError("source JEV block coverage differs")
    for record, choice in zip(jev, choices, strict=True):
        if any(record.get(key) != value for key, value in choice.items()):
            raise ValueError("source JEV block inputs differ")
    baseline = {p["request_id"]: p for p in read(source_run / "baseline-predictions.jsonl")}
    if set(baseline) != set(plan["original_requests"]):
        raise ValueError("source baseline original coverage differs")
    return plan, choices, jev, documents, blocks, baseline, summary


def model_requests(choices, groups):
    by_group = {g["group_id"]: g for g in groups}
    requests = []
    for choice in choices:
        group = by_group[choice["group_id"]]
        values = deepcopy(group["option_values"])
        unknown = GL.unknown_label(values)
        labels, label_map = GL.schema_choices(values, unknown)
        requests.append({"id": choice["id"], "group_id": group["group_id"], "group_sha256": group["group_sha256"],
                         "source_group_id": choice.get("source_group_id", group["group_id"]),
                         "source_classification_id": choice.get("source_classification_id", choice["id"]),
                         "block_id": choice["block_id"], "block_rank": choice["block_rank"],
                         "text": choice["state"], "axis_name": group["axis_name"], "option_values": values,
                         "instruction": INSTRUCTION, "candidate_labels": [*values, unknown], "unknown_label": unknown,
                         "schema_task_name": GL.schema_text(group["axis_name"]), "schema_labels": labels,
                         "schema_label_to_original_value": label_map, "schema_unknown_label": labels[-1],
                         "scope_status": "unproved", "production_adoption_allowed": False})
    return requests


def rebuild_plan(source_plan, source_choices, source_jev, original_requests, pin):
    """New component/policy identity invalidates decision caches and group IDs."""
    identity = deepcopy(source_plan["model_identity"])
    identity.update(model_id="sku-product-global-jev-gliner-ensemble", revision="product-condition-gliner-v1",
                    gliner=pin, gliner_parameter_count=PARAMETER_COUNT, gliner_precision="fp32",
                    gliner_instruction=INSTRUCTION, gliner_confidence=CONFIDENCE,
                    variants=list(VARIANTS), aggregation="top1/top3/majority-agree/block-agree", minimum_votes=MIN_VOTES)
    new_plan = SHARING.build_groups(original_requests, source_plan["documents"], source_plan["bindings"],
                                    model_identity=identity, content_version="product-condition-gliner-v1/product-source-blocks",
                                    invariance_contract=source_plan["invariance_contract"])
    def signature(group):
        return digest([group["target_document_id"], group["target_document_sha256"], group["axis_name"], group["option_values"]])
    new_by_signature = {signature(group): group for group in new_plan["groups"]}
    mapping = {group["group_id"]: new_by_signature[signature(group)] for group in source_plan["groups"]}
    if len(mapping) != len(new_plan["groups"]):
        raise ValueError("new model identity changed the product grouping semantics")
    choices, jev = [], []
    for previous, target in ((source_choices, choices), (source_jev, jev)):
        for row in previous:
            group = mapping[row["group_id"]]
            target.append({**deepcopy(row), "source_group_id": row["group_id"],
                           "source_classification_id": row["id"], "group_id": group["group_id"],
                           "group_sha256": group["group_sha256"], "id": group["group_id"] + ":block:" + str(row["block_rank"] - 1)})
    return new_plan, choices, jev


def model_key(request):
    return digest([GL.MODEL_ID, GL.REVISION, "fp32", request["text"], request["schema_task_name"],
                   request["schema_labels"], request["instruction"]])


class Backend:
    """Pinned GLiNER ClassifyAPI, without truncation or model-specific SKU rules."""
    def __init__(self, model_dir, threads):
        import torch
        from gliner2 import AutoExtractor
        from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
        if torch.cuda.is_available():
            raise RuntimeError("CPU-only comparator refuses an available CUDA device")
        torch.set_num_threads(threads)
        started = time.perf_counter()
        self.model = AutoExtractor.from_pretrained(str(model_dir), local_files_only=True).to("cpu").eval()
        self.classifier = Classifier(self.model)
        self.Config, self.Schema = ClassificationConfig, ClassificationSchema
        if (sum(p.numel() for p in self.model.parameters()) != PARAMETER_COUNT
                or any(p.device.type != "cpu" or p.dtype != torch.float32 for p in self.model.parameters())):
            raise RuntimeError("pinned GLiNER must have 287355159 FP32 parameters on CPU")
        self.runtime = {"model_load_seconds": time.perf_counter() - started, "inference_seconds": 0.0,
                        "tokenization_seconds": 0.0, "parameter_count": PARAMETER_COUNT, "device": "cpu", "precision": "fp32"}
        self.dependencies = {inspect.getfile(c): sha(inspect.getfile(c)) for c in
                             (AutoExtractor, Classifier, ClassificationConfig, ClassificationSchema, type(self.model.processor))}

    def schema(self, request):
        return self.Schema().single(request["schema_task_name"], request["schema_labels"], instruction=request["instruction"])

    def token_lengths(self, requests):
        started = time.perf_counter()
        counts = GL.complete_input_token_counts(requests, [self.schema(r) for r in requests], self.classifier, self.model.processor)
        self.runtime["tokenization_seconds"] += time.perf_counter() - started
        return counts

    def classify(self, requests, batch_size):
        first = requests[0]
        if any((r["schema_task_name"], r["schema_labels"], r["instruction"]) !=
               (first["schema_task_name"], first["schema_labels"], first["instruction"]) for r in requests):
            raise ValueError("cannot batch different whole original schemas")
        config = self.Config(batch_size=batch_size, max_len=None, max_candidates_per_task=max(64, len(first["schema_labels"])))
        started = time.perf_counter()
        try:
            return self.classifier.batch_classify([r["text"] for r in requests], self.schema(first), config=config)
        finally:
            self.runtime["inference_seconds"] += time.perf_counter() - started


def classify_requests(requests, backend, output_dir, progress, batch_size):
    unique = FULL.unique_inputs(requests, model_key)
    cache, schemas = {}, defaultdict(list)
    for request in unique:
        schemas[digest([request["schema_task_name"], request["schema_labels"], request["instruction"]])].append(request)
    completed = 0
    with (output_dir / "gliner-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
        for schema_requests in schemas.values():
            for offset in range(0, len(schema_requests), batch_size):
                chunk = schema_requests[offset:offset + batch_size]
                records = [{**request, "model_input_sha256": model_key(request), "chosen_value": None,
                            "chosen_label": request["unknown_label"], "confidence": None, "probabilities": None} for request in chunk]
                try:
                    lengths = backend.token_lengths(chunk)
                    if len(lengths) != len(chunk) or any(type(n) is not int or n < 1 for n in lengths):
                        raise ValueError("invalid compiled schema input lengths")
                except Exception as error:
                    for record in records:
                        record.update(status="tokenization_error", actual_encoder_input_token_count=None,
                                      error_type=type(error).__name__, error=str(error))
                    accepted = []
                else:
                    accepted = []
                    for record, length in zip(records, lengths, strict=True):
                        record["actual_encoder_input_token_count"] = length
                        if length > GL.MAX_TOKENS:
                            record["status"] = "input_too_long"
                        else:
                            accepted.append(record)
                if accepted:
                    try:
                        results = backend.classify(accepted, batch_size)
                        if len(results) != len(accepted):
                            raise ValueError("GLiNER classification result count differs")
                        validated = [GL.validate_result(result[request["schema_task_name"]], request)
                                     for request, result in zip(accepted, results, strict=True)]
                    except Exception as error:
                        for record in accepted:
                            record.update(status="inference_error", error_type=type(error).__name__, error=str(error))
                    else:
                        for record, value in zip(accepted, validated, strict=True):
                            record.update(value, status="ok")
                for record in records:
                    cache[record["model_input_sha256"]] = record
                FULL._append(handle, records)
                completed += len(chunk)
                progress.emit(stage="product-gliner", completed_groups=completed, total_groups=len(unique))
    records = []
    fields = ("status", "chosen_value", "chosen_label", "chosen_schema_label", "confidence", "probabilities", "schema_probabilities",
              "actual_encoder_input_token_count", "error_type", "error")
    for request in requests:
        cached = cache[model_key(request)]
        records.append({**request, "model_input_sha256": model_key(request),
                        **{key: deepcopy(cached[key]) for key in fields if key in cached}})
    write_rows(output_dir / "gliner-classification-options.jsonl", records)
    return records, len(unique)


def winner(record, options):
    if record.get("status") != "ok" or record.get("chosen_value") not in options or record.get("confidence", 0) < CONFIDENCE:
        return None
    probabilities = record.get("probabilities")
    if not isinstance(probabilities, dict) or sum(p == max(probabilities.values()) for p in probabilities.values()) != 1:
        return None
    return record["chosen_value"]


def group_results(groups, gliner_records, jev_records, documents, blocks, variant):
    if variant not in VARIANTS:
        raise ValueError("unsupported frozen GLiNER variant")
    gliner_by_group, jev_by_block = defaultdict(list), {}
    for record in gliner_records:
        gliner_by_group[record["group_id"]].append(record)
    for record in jev_records:
        jev_by_block[(record["group_id"], record["block_id"])] = record
    outcomes = {}
    for group in groups:
        records = sorted(gliner_by_group[group["group_id"]], key=lambda r: r["block_rank"])[:TOP_K]
        gliner_winners = {r["block_id"]: winner(r, group["option_values"]) for r in records}
        jev_winners = {r["block_id"]: PRODUCT.raw_winner(jev_by_block[(group["group_id"], r["block_id"])], group["option_values"])
                       for r in records}
        votes = Counter(v for v in gliner_winners.values() if v is not None)
        jev_votes = Counter(v for v in jev_winners.values() if v is not None)
        selected, used, reason = None, [], "no_unique_gliner_majority"
        if variant == "product-top1-gliner":
            selected = gliner_winners.get(records[0]["block_id"]) if records and records[0]["block_rank"] == 1 else None
            used = [records[0]["block_id"]] if selected else []
            reason = "gliner_top1_unique_raw_value" if selected else "gliner_top1_unknown_tie_error_or_below_fixed_confidence"
        elif variant == "product-top3-jev-gliner-block-agree":
            joint = {bid: value for bid, value in gliner_winners.items() if value is not None and value == jev_winners[bid]}
            leaders = [value for value, count in Counter(joint.values()).items() if count >= MIN_VOTES]
            if len(leaders) == 1:
                selected = leaders[0]
                used = [bid for bid, value in joint.items() if value == selected]
                reason = "two_blocks_jev_gliner_agree_on_same_raw_value"
        else:
            leaders = [value for value, count in votes.items() if count >= MIN_VOTES]
            if len(leaders) == 1:
                selected = leaders[0]
                used = [bid for bid, value in gliner_winners.items() if value == selected]
                reason = "two_blocks_gliner_agree"
                if variant == "product-top3-jev-gliner-majority-agree":
                    if jev_votes[selected] < MIN_VOTES:
                        selected, used, reason = None, [], "jev_and_gliner_majorities_differ_or_unknown"
                    else:
                        used = list(dict.fromkeys([*used, *(bid for bid, value in jev_winners.items() if value == selected)]))
                        reason = "jev_and_gliner_majorities_agree_on_same_raw_value"
        refs = []
        for bid in used:
            current = PRODUCT.product_refs(group, documents, blocks[bid])
            if current is None:
                selected, refs, used, reason = None, [], [], "literal_product_description_reference_invalid"
                break
            refs.extend(current)
        outcomes[group["group_id"]] = {"group_id": group["group_id"], "group_sha256": group["group_sha256"],
            "status": "ok" if selected else "unknown", "winning_value": selected, "quote_refs": PRODUCT.deduplicate_refs(refs),
            "variant": variant, "reason": reason, "gliner_block_votes": dict(votes), "jev_block_votes": dict(jev_votes),
            "gliner_block_winners": gliner_winners, "jev_block_winners": jev_winners, "supporting_block_ids": used,
            "confidence": CONFIDENCE, "scope_status": "unproved", "production_adoption_allowed": False}
    return outcomes


def run(source_run, output_dir, model_dir=ROOT / ".deps/sku-gliner-model", threads=4, batch_size=8, *, backend_factory=None, pin_verifier=None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != 4 or batch_size != 8:
        raise ValueError("frozen CPU settings are four threads and batch size eight")
    started = time.perf_counter()
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    source_plan, source_choices, source_jev, documents, blocks, baseline, source_summary = load_source(source_run)
    pin = (pin_verifier or GL.verify_snapshot)(model_dir)
    plan, choices, jev = rebuild_plan(source_plan, source_choices, source_jev, read(source_run / "source-requests.jsonl"), pin)
    requests = model_requests(choices, plan["groups"])
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in SOURCE_NAMES:
        shutil.copyfile(source_run / name, output_dir / ("received-" + name))
    for name in ("sharing-contract.json", "baseline-predictions.jsonl"):
        shutil.copyfile(source_run / name, output_dir / name)
    write_json(output_dir / "sharing-plan.json", plan)
    write_rows(output_dir / "product-groups.jsonl", plan["groups"])
    write_rows(output_dir / "received-jev-remapped.jsonl", jev)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    reranker_manifest = HERE / "manifests/reranker.json"
    shutil.copyfile(reranker_manifest, code_dir / "reranker.json")
    write_rows(output_dir / "model-requests.jsonl", requests)
    freeze = {"schema_version": "product-condition-gliner-v1", "labels_read": False, "labels_in_model_inputs": False,
              "errors_only": True, "development_only": True, "independent_test": False, "synthetic_data": False,
              "scope_status": "unproved", "production_adoption_allowed": False, "variants": list(VARIANTS),
              "confidence": CONFIDENCE, "threshold_policy": "fixed .5; no search", "top_k": TOP_K, "minimum_votes": MIN_VOTES,
              "threads": threads, "batch_size": batch_size, "precision": "fp32", "device": "cpu", "gliner_pin": pin,
              "expected_parameter_count": PARAMETER_COUNT, "instruction": INSTRUCTION,
              "original_option_transport": "index-prefixed complete values; pinned delimiter escaping; original values retained",
              "model_context": "exact received product-only title plus original block passage",
              "selected_axes_model_input": False, "wanted_value_separately_injected": False,
              "new_jev_inference": False, "new_reranker_inference": False, "new_nli_inference": False,
              "input_length_policy": "actual compiled-schema encoder length <=512; max_len=None; long/error inputs cast no vote",
              "aggregation_policy": "top1; majority2; two model majorities agree; or same-block two-model agreement2",
              "source_run_sha256": {n: sha(source_run / n) for n in SOURCE_NAMES},
              "source_model_identity": source_summary["model_identity"], "invariance_contract": source_summary["invariance_contract"],
              "model_identity": plan["model_identity"], "new_sharing_plan_sha256": sha(output_dir / "sharing-plan.json"),
              "new_component_invalidates_product_group_decision_cache": True,
              "model_requests_sha256": sha(output_dir / "model-requests.jsonl"),
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()}, "request_count": len(plan["original_requests"]),
              "eligible_request_count": len(plan["request_to_group"]), "group_count": len(plan["groups"]), "block_count": len(requests)}
    write_json(output_dir / "freeze.json", freeze)
    with FULL.Progress(output_dir) as progress:
        progress.emit(stage="gliner-load")
        backend = (backend_factory or Backend)(model_dir, threads)
        write_json(output_dir / "model-ready.json", {"runtime": backend.runtime, "loader_dependency_sha256": backend.dependencies})
        records, unique_count = classify_requests(requests, backend, output_dir, progress, batch_size)
        states = {}
        for variant in VARIANTS:
            result = group_results(plan["groups"], records, jev, documents, blocks, variant)
            write_rows(output_dir / (variant + "-group-results.jsonl"), result.values())
            predictions = SHARING.fan_out(plan, result, baseline)
            write_rows(output_dir / (variant + "-predictions.jsonl"), predictions)
            states[variant] = dict(Counter(p["state"] for p in predictions))
        for name, expected in freeze["source_run_sha256"].items():
            if sha(source_run / name) != expected:
                raise ValueError("completed source changed during comparison: " + name)
        for name, expected in freeze["code_sha256"].items():
            path = reranker_manifest if name == "reranker.json" else HERE / name
            if sha(path) != expected:
                raise ValueError("source implementation changed during comparison: " + name)
        progress.emit(stage="complete", request_count=len(plan["original_requests"]))
    summary = {**freeze, "runtime": backend.runtime, "loader_dependency_sha256": backend.dependencies,
               "total_seconds": time.perf_counter() - started, "unique_model_input_count": unique_count,
               "model_statuses": dict(Counter(r["status"] for r in records)), "prediction_states": states,
               "input_and_code_sha256_verified_after_inference": True,
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".deps/sku-gliner-model")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    run(**vars(parser.parse_args()))
