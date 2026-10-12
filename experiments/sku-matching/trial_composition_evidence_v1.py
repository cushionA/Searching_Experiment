#!/usr/bin/env python3
"""Compare title-free, source-bound SKU composition evidence on frozen dev inputs.

References are opened only after all declared pipelines' predictions and same
AU-row gates have been frozen. A literal quote does not prove applicability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time

import composition_evidence_v1 as CONTENT
import composition_source_blocks_v1 as SOURCES
import evaluate_mixed_gpu_sku_v1 as E
import evaluate_keyword_filtered_product_v1 as REFERENCES
import product_condition_sharing_v1 as SHARING
import trial_keyword_filtered_product_v1 as VERIFY
import trial_product_condition_models_v1 as PRODUCT

ROOT = PRODUCT.ROOT
PREP = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5"
CPU = ROOT / ".lab-output/sku-mixed-cpu-run-20261012-v1"
CURRENT = ROOT / ".lab-output/sku-raw-nli-unique-support-evaluation-20261012-v1"
CURRENT_PRED = ROOT / ".lab-output/sku-raw-nli-unique-support-20261012-v1/predictions.jsonl"
OLD_EVAL = ROOT / ".lab-output/sku-mixed-cpu-evaluation-20261012-v4"
ROUTES = ROOT / ".lab-output/sku-mixed-cpu-reference-routes-20261012-v2"
DEFAULT_OUT = ROOT / ".lab-output/sku-composition-evidence-20261013-v1"
POLICIES = ("replace-model-conditions", "fallback-all", "fallback-shared")
TOP_K, MIN_VOTES = 3, 2


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def literal_block_refs(block, document_id, documents):
    refs = block.get("quote_refs")
    request = {"target_document_id": document_id}
    if (not isinstance(refs, list) or not refs or any(ref.get("field") != "description"
            or not PRODUCT.V1.verified_ref(ref, request, documents) for ref in refs)):
        return None
    return deepcopy(refs)


def aggregate(units, records, blocks, documents):
    """Require two distinct ranked source blocks to select one whole raw option."""
    by_unit = defaultdict(list)
    for record in records:
        by_unit[record["group_id"]].append(record)
    results = {}
    for uid, unit in units.items():
        current = sorted(by_unit[uid], key=lambda r: r["block_rank"])
        if len({r["block_id"] for r in current}) != len(current) or len(current) > TOP_K:
            raise ValueError("duplicate or excessive source blocks")
        votes, winners = Counter(), {}
        for record in current:
            if record["group_sha256"] != unit["group_sha256"]:
                raise ValueError("classification unit binding differs")
            winner = PRODUCT.raw_winner(record, unit["option_values"])
            winners[record["block_id"]] = winner
            if winner is not None:
                votes[winner] += 1
        leaders = [v for v, count in votes.items() if count >= MIN_VOTES]
        winner = leaders[0] if len(leaders) == 1 else None
        used = [bid for bid, v in winners.items() if winner is not None and v == winner]
        refs = []
        for bid in used:
            current_refs = literal_block_refs(blocks[bid], unit["target_document_id"], documents)
            if current_refs is None:
                raise ValueError("scored evidence lost its exact document quote")
            refs.extend(current_refs)
        if winner is not None and unit["kind"] == "paired":
            text = unit["target_selected_axes_text"]
            if text:
                ref = {"document_id": unit["target_document_id"], "field": "selected_axes",
                       "start": 0, "end": len(text), "quote": text}
                request = unit["source_request"]
                if not E.GATE.verify_quote(ref, request, documents):
                    raise ValueError("selected row context quote differs")
                refs.append(ref)
        results[uid] = {"group_id": uid, "group_sha256": unit["group_sha256"],
                        "status": "ok" if winner is not None else "unknown",
                        "winning_value": winner, "quote_refs": PRODUCT.deduplicate_refs(refs),
                        "block_votes": dict(votes), "block_winners": winners,
                        "supporting_block_ids": used, "scope_status": "unproved",
                        "production_adoption_allowed": False, "variant": CONTENT.CONTENT_VERSION,
                        "reason": "composition_two_blocks_agree" if winner else "composition_no_unique_majority"}
    return results


def paired_prediction(request, baseline, result):
    support = result["winning_value"] == request["selected_value"] and bool(result["quote_refs"])
    return {**{key: deepcopy(baseline[key]) for key in
                   ("request_id", "request_sha256", "selected_value", "direction", "source_request", "source_task_bindings")},
            "state": "support" if support else "unknown",
            "winning_value": result["winning_value"],
            "reason": result["reason"] if support or result["winning_value"] is None else "composition_alternative_value_selected",
            "quote_refs": deepcopy(result["quote_refs"]) if support else [],
            "composition_result": deepcopy(result), "source_model_prediction": deepcopy(baseline),
            "scope_status": "unproved", "production_adoption_allowed": False,
            "variant": CONTENT.CONTENT_VERSION}


def policy_predictions(policy, baseline, derived, eligible, proof_ids):
    if policy not in POLICIES or set(baseline) != set(derived):
        raise ValueError("invalid frozen composition policy")
    result = deepcopy(baseline)
    for rid, value in derived.items():
        if rid in proof_ids:
            continue
        if policy == "replace-model-conditions":
            result[rid] = deepcopy(value)
        elif baseline[rid]["state"] != "support" and value["state"] == "support":
            if policy == "fallback-all" or rid in eligible:
                result[rid] = deepcopy(value)
    if any(result[rid] != baseline[rid] for rid in proof_ids):
        raise ValueError("raw typed proofs must survive unchanged")
    return result


def run(output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    started = time.perf_counter()
    source = VERIFY.verify_source(CPU / "product-model")
    manifest, requests, documents, tasks, cases, membership, hosts = E.load_mixed_prepared(PREP)
    cpu_summary, _ = E.verify_cpu_run(PREP, CPU, manifest, requests)
    _, _, eligible = E.check_prediction_coverage(PREP, CPU, requests, documents, tasks, cpu_summary)
    for name, expected in manifest["output_sha256"].items():
        if E.sha(PREP / name) != expected:
            raise ValueError("prepared source differs: " + name)
    before_summary = read_json(CURRENT / "summary.json")
    for name, expected in before_summary["output_sha256"].items():
        if E.sha(CURRENT / name) != expected:
            raise ValueError("current evaluation output differs: " + name)
    assembler = read_json(CURRENT_PRED.parent / "summary.json")
    if E.sha(CURRENT_PRED) != assembler["predictions_sha256"]:
        raise ValueError("current derived predictions differ")
    baseline = E.GATE.indexed(CURRENT_PRED, "request_id")
    E.GATE.validate_predictions(requests, documents, tasks, baseline, baseline, "baseline")
    bindings = {rid: p["source_task_bindings"] for rid, p in baseline.items()}
    proof_ids = {rid for rid, p in baseline.items() if p.get("verified_value_equivalence")}
    if len(eligible) != 80 or len(requests) != 134 or len(proof_ids) != 1:
        raise ValueError("frozen request/sharing/proof counts differ")
    identity = deepcopy(source["summary"]["model_identity"])
    identity.update(model_id="sku-composition-evidence-jev", revision=CONTENT.CONTENT_VERSION,
                    prompt_builder_sha256=E.sha(Path(CONTENT.__file__)),
                    variants=list(POLICIES), top_k=TOP_K, minimum_votes=MIN_VOTES)
    plan = SHARING.build_groups(requests, documents, bindings, model_identity=identity,
                               content_version=CONTENT.CONTENT_VERSION, invariance_contract=source["contract"])
    if len(plan["groups"]) != 29 or set(plan["request_to_group"]) != eligible:
        raise ValueError("shared scope differs")
    units = {g["group_id"]: {**deepcopy(g), "kind": "shared", "target_selected_axes_text": None}
             for g in plan["groups"]}
    for rid, request in requests.items():
        if rid in eligible or rid in proof_ids:
            continue
        units[rid] = {"group_id": rid, "group_sha256": request["request_sha256"], "kind": "paired",
                      "axis_name": request["axis_name"], "option_values": deepcopy(request["option_values"]),
                      "target_document_id": request["target_document_id"],
                      "target_selected_axes_text": request["target_selected_axes_text"], "source_request": deepcopy(request)}
    rakuten_blocks, rakuten_exclusions, raw_files = SOURCES.build_rakuten_blocks(PREP, list(documents.values()))
    if any(E.sha(Path(path)) != expected for path, expected in raw_files.items()):
        raise ValueError("Rakuten raw capture changed during extraction")
    blocks = deepcopy(source["blocks"])
    for block in rakuten_blocks:
        if block["block_id"] in blocks:
            raise ValueError("source block/document identity collision")
        blocks[block["block_id"]] = block
    retrieval, rejected = [], []
    for uid, unit in units.items():
        for bid, block in blocks.items():
            if block["target_document_id"] != unit["target_document_id"]:
                continue
            reason = CONTENT.exclusion_reason(block)
            refs = literal_block_refs(block, unit["target_document_id"], documents)
            if reason or refs is None:
                rejected.append({"unit_id": uid, "block_id": bid, "reason": reason or "invalid_literal_quote"})
                continue
            retrieval.append({"id": uid + ":retrieval:" + bid, "request_id": uid, "group_id": uid,
                              "group_sha256": unit["group_sha256"], "block_id": bid,
                              "target_document_id": unit["target_document_id"], "quote_refs": refs,
                              "query": CONTENT.query_for(unit["axis_name"], unit["option_values"], unit["target_selected_axes_text"]),
                              "passage": block["model_passage"]})
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, value in (("sharing-plan.json", plan), ("sharing-contract.json", source["contract"])):
        E.write_json(output_dir / name, value)
    for name, values in (("classification-units.jsonl", units.values()), ("retrieval-requests.jsonl", retrieval),
                         ("excluded-evidence.jsonl", rejected), ("baseline-predictions.jsonl", baseline.values()),
                         ("source-blocks.jsonl", blocks.values()), ("rakuten-block-exclusions.jsonl", rakuten_exclusions)):
        E.write_rows(output_dir / name, values)
    code_sources = {name: PRODUCT.HERE / ("manifests/reranker.json" if name == "reranker.json" else name)
                    for name in source["summary"]["code_sha256"]}
    for module in (E, REFERENCES, VERIFY, CONTENT, SOURCES):
        code_sources[Path(module.__file__).name] = Path(module.__file__)
    code_sources["build_scoped_html_blocks_v1.py"] = PRODUCT.HERE / "build_scoped_html_blocks_v1.py"
    code_sources["prepare_scoped_html_trial_v1.py"] = PRODUCT.HERE / "prepare_scoped_html_trial_v1.py"
    code_sources[Path(__file__).name] = Path(__file__)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name, path in code_sources.items():
        shutil.copyfile(path, code_dir / name)
    paths = [PREP / "manifest.json", *(PREP / n for n in E.PREP_FILES),
             *(CPU / "product-model" / n for n in VERIFY.SOURCE_ARTIFACTS),
             CPU / "summary.json", CPU / "cpu-source-freeze.json", CPU / "execution-freeze.json",
             CURRENT / "summary.json", CURRENT_PRED.parent / "summary.json", CURRENT_PRED,
             PREP / "raw-source-registry.jsonl", *(Path(p) for p in raw_files),
             *(CURRENT / name for name in before_summary["output_sha256"]),
             *code_sources.values(), *code_dir.iterdir(), *(p for p in output_dir.iterdir() if p.is_file())]
    snapshot = VERIFY.snapshot_paths({str(p): p for p in paths})
    E.write_json(output_dir / "execution-freeze.json", {
        "schema_version": "composition-evidence-execution-v1", "experiment_status": "exploratory_dev_informed",
        "labels_read": False, "independent_test": False, "production_adoption_allowed": False,
        "policies": list(POLICIES), "content_version": CONTENT.CONTENT_VERSION, "source_snapshot": snapshot,
        "model_identity": identity, "case_count": 83, "request_count": 134, "shared_request_count": 80,
        "shared_unit_count": 29, "paired_unit_count": 53, "unchanged_typed_proof_count": 1,
        "new_jev_inference": True, "new_reranker_inference": True, "new_nli_inference": False,
        "model_title_input": False, "shared_selected_axes_input": False,
        "wanted_value_separately_injected": False, "all_raw_options_preserved": True,
        "unknown_control_retained": True, "text_truncation": False, "top_k": TOP_K,
        "minimum_votes": MIN_VOTES, "threads": 4, "batch_size": 16, "device": "cpu", "precision": "fp32",
        "rakuten_raw_capture_sha256": raw_files,
        "evidence_policy": "exclude search-keyword blocks, linked blocks and every row of linked/unbound tables; preserve exact literal source spans; use source-bound remaining blocks",
        "paired_reverse_change_authorized": True, "raw_core_or_residual_auto_proof": False})
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    with PRODUCT.FULL.Progress(output_dir) as progress:
        progress.emit(stage="composition-reranker-load", unit_count=len(units), retrieval_count=len(retrieval))
        model = PRODUCT.V1.RERANKER.Model(ROOT / ".deps/sku-fixed-models/reranker", threads=4, verify_files=True)
        rankings, reranker_runtime = PRODUCT.V1.rank_retrieval(retrieval, model, output_dir, progress)
        del model
        choices = []
        for uid, unit in units.items():
            for index, bid in enumerate(rankings.get(uid, {}).get("ordered_block_ids", [])[:TOP_K]):
                choices.append(CONTENT.choice_request(uid + ":block:" + str(index), unit["axis_name"],
                    unit["option_values"], SHARING.UNKNOWN, blocks[bid], unit["target_selected_axes_text"],
                    {"group_id": uid, "group_sha256": unit["group_sha256"], "block_id": bid,
                     "block_rank": index + 1, "unknown_control_label": SHARING.UNKNOWN,
                     "all_original_option_values": deepcopy(unit["option_values"]), "unit_kind": unit["kind"]}))
        E.write_rows(output_dir / "model-requests.jsonl", choices)
        classified_snapshot = VERIFY.snapshot_paths({str(output_dir / "model-requests.jsonl"): output_dir / "model-requests.jsonl"})
        E.write_json(output_dir / "classification-input-freeze.json", {"labels_read": False, "input_snapshot": classified_snapshot,
                     "request_count": len(choices), "no_legacy_prompt_cache": True})
        records, jev_runtime, pins = PRODUCT.score_jev(choices, PRODUCT.JEV.run_choices,
            ROOT / ".deps/sku-fixed-models/jev", 4, 16, output_dir, progress)
        if not PRODUCT.JEV._same_pins(pins) or pins["device"] != "cpu" or pins["precision"] != "fp32":
            raise ValueError("JEV fixed model/runtime pins differ")
        VERIFY.verify_snapshot(snapshot, phase="after composition inference")
        VERIFY.verify_snapshot(classified_snapshot, phase="after composition inference")
        results = aggregate(units, records, blocks, documents)
        E.write_rows(output_dir / "unit-results.jsonl", results.values())
        shared_results = {g["group_id"]: results[g["group_id"]] for g in plan["groups"]}
        derived = {p["request_id"]: p for p in SHARING.fan_out(plan, shared_results, baseline)}
        E.SHARED.validate_shared_predictions(requests, documents, tasks, derived, baseline, eligible, "product-top3-jev")
        for uid, unit in units.items():
            if unit["kind"] == "paired":
                derived[uid] = paired_prediction(requests[uid], baseline[uid], results[uid])
        for policy in POLICIES:
            destination = output_dir / policy
            destination.mkdir()
            predicted = policy_predictions(policy, baseline, derived, eligible, proof_ids)
            E.GATE.validate_predictions(requests, documents, tasks, predicted, baseline, "baseline")
            if any(any(r["field"] != "description" for r in predicted[rid].get("quote_refs", []))
                   for rid in eligible if predicted[rid] != baseline[rid]):
                raise ValueError("new shared support has non-description citations")
            E.write_rows(destination / "predictions.jsonl", predicted.values())
            E.GATE.finish(PREP, destination / "predictions.jsonl", destination / "gates")
        progress.emit(stage="all-gates-complete", policy_count=len(POLICIES))
    frozen_outputs = VERIFY.snapshot_paths({str(p): p for p in output_dir.rglob("*") if p.is_file()})
    E.write_json(output_dir / "all-gates-frozen.json", {"labels_read": False, "all_gates_frozen": True,
                 "policies": list(POLICIES), "case_count": 83, "output_snapshot": frozen_outputs})
    _, _, _, references, _ = REFERENCES.verify_original_sources(PREP, OLD_EVAL, ROUTES)
    scored_ids = {r["case_id"] for r in E.eligibility_rows(membership, hosts)[0] if r["primary_score_eligible"]}
    scored = {k: v for k, v in references.items() if k in scored_ids and not v.get("reference_uncertain", False)}
    if len(scored) != 73:
        raise ValueError("scored fixed machine-reference count differs")
    old_gates = E.GATE.indexed(CURRENT / "gates/decisions.jsonl", "case_id")
    before = {r["case_id"]: r for r in E.metrics_for(old_gates, scored)["case_results"]}
    metrics = {}
    for policy in POLICIES:
        destination = output_dir / policy
        gates = E.GATE.indexed(destination / "gates/decisions.jsonl", "case_id")
        if set(gates) != set(cases):
            raise ValueError("all-case gate coverage differs")
        metrics[policy], details = {}, []
        for stratum in E.STRATA:
            subset = {k: v for k, v in scored.items() if membership[k]["membership_strata"] == [stratum]}
            value = E.metrics_for(gates, subset)
            metrics[policy][stratum] = {k: v for k, v in value.items() if k != "case_results"}
            details.extend({"stratum": stratum, **r} for r in value["case_results"])
        value = E.metrics_for(gates, scored)
        metrics[policy]["scored_primary_reference_union"] = {k: v for k, v in value.items() if k != "case_results"}
        changes = [{"case_id": r["case_id"], "before": before[r["case_id"]], "after": r}
                   for r in value["case_results"] if r != before[r["case_id"]]]
        E.write_rows(destination / "case-results.jsonl", details)
        E.write_rows(destination / "changes-vs-current.jsonl", changes)
    for current_snapshot in (snapshot, classified_snapshot, frozen_outputs):
        VERIFY.verify_snapshot(current_snapshot, phase="after composition scoring")
    VERIFY.verify_source(CPU / "product-model")
    summary = {"schema_version": "composition-evidence-result-v1", "experiment_status": "exploratory_dev_informed",
               "independent_test": False, "production_adoption_allowed": False, "scored_case_count": 73,
               "all_case_count": 83, "labels_read_after_all_gates_frozen": True,
               "input_and_code_sha_verified_after_scoring": True, "reference_kind": "existing machine labels",
               "model_identity": identity, "verified_jev_pin": pins, "jev_runtime": jev_runtime,
               "reranker_runtime": reranker_runtime, "total_seconds": time.perf_counter() - started,
               "exclusion_counts": dict(Counter(r["reason"] for r in rejected)),
               "classification_statuses": dict(Counter(r["status"] for r in records)),
               "unit_count": len(units), "classification_request_count": len(choices),
               "policies": metrics, "before_union": before_summary["metrics"]["scored_primary_reference_union"],
               "output_sha256": {str(p.relative_to(output_dir)): E.sha(p) for p in output_dir.rglob("*") if p.is_file()}}
    E.write_json(output_dir / "summary.json", summary)
    print(json.dumps({"policies": {k: v["scored_primary_reference_union"] for k, v in metrics.items()}}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    run(**vars(parser.parse_args()))
