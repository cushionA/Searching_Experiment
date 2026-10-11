#!/usr/bin/env python3
"""Freeze three error-subset gates before scoring separate machine references.

This is a capped development diagnostic selected from previous FP/FN cases.
It reports corrections and AU-row evidence separately, never full-data or
independent accuracy. It performs no inference, threshold search, or changes
to the original rules, predictions, references, or input layout.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from integrate_claude_description_v1 import finish, score, verify_quote

HERE = Path(__file__).resolve().parent
SOURCE_NAMES = ("requests.jsonl", "documents.jsonl", "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl")
VARIANTS = ("baseline", "structured-jev", "structured-verified")
PREPARED_SCHEMA = "scoped-html-error-preparation-v1"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def indexed(path, key):
    result = {}
    for row in rows(path):
        value = row.get(key)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError("missing or duplicate " + key + ": " + path.name)
        result[value] = row
    return result


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def write_rows(path, values):
    with path.open("x", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def load_prepared(prepared_dir):
    manifest = json.loads((prepared_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != PREPARED_SCHEMA or manifest.get("phase") != "prepare" or
            manifest.get("labels_read") is not False or manifest.get("errors_only") is not True or
            manifest.get("independent_test") is not False or manifest.get("synthetic_data") is not False or
            manifest.get("production_adoption_allowed") is not False):
        raise ValueError("unsupported real development error-subset preparation contract")
    for name in SOURCE_NAMES:
        if manifest.get("output_sha256", {}).get(name) != sha(prepared_dir / name):
            raise ValueError("prepared file SHA differs: " + name)
    requests = indexed(prepared_dir / "requests.jsonl", "request_id")
    documents = indexed(prepared_dir / "documents.jsonl", "document_id")
    tasks = indexed(prepared_dir / "tasks.jsonl", "task_id")
    cases = indexed(prepared_dir / "case_plans.jsonl", "case_id")
    row_plans = list(rows(prepared_dir / "row_plans.jsonl"))
    row_keys = {(row["case_id"], row["au_row_key"]) for row in row_plans}
    if len(row_keys) != len(row_plans) or any(row["case_id"] not in cases for row in row_plans):
        raise ValueError("duplicate or unselected prepared row identities")
    cells = manifest.get("selected_error_cells")
    counts = manifest.get("selected_error_counts")
    if (not cases or not isinstance(cells, dict) or set(cells) != set(cases) or
            any(v not in {"fp", "fn"} for v in cells.values()) or
            not isinstance(counts, dict) or set(counts) != {"fp", "fn"} or
            any(type(v) is not int or v < 0 for v in counts.values()) or
            counts != {key: Counter(cells.values())[key] for key in ("fp", "fn")}):
        raise ValueError("selected error identities/counts differ from the prepared cases")
    if not isinstance(manifest.get("selection_policy"), dict):
        raise ValueError("selection policy must be frozen by the subset producer")
    for field, actual in (("request_count", len(requests)), ("document_count", len(documents)),
                          ("task_count", len(tasks)), ("case_count", len(cases)), ("row_count", len(row_plans))):
        if manifest.get(field) != actual:
            raise ValueError("manifest count differs: " + field)
    bound = set()
    for task in tasks.values():
        if (task["request_id"] not in requests or task["case_id"] not in cases or
                (task["case_id"], task["au_row_key"]) not in row_keys):
            raise ValueError("subset task binds outside selected requests/cases")
        bound.add(task["request_id"])
    if set(requests) != bound:
        raise ValueError("prepared request has no selected source task binding")
    return manifest, requests, documents, tasks, cases


def validate_predictions(requests, documents, tasks, predicted, baseline, variant):
    if set(predicted) != set(requests):
        raise ValueError("prediction must cover exactly every original subset request: " + variant)
    literal_counts = Counter()
    for identity, request in requests.items():
        value, original = predicted[identity], baseline[identity]
        if (value.get("request_sha256") != request["request_sha256"] or
                value.get("selected_value") != request["selected_value"] or
                value.get("source_request") != request or
                value.get("source_task_bindings") != original.get("source_task_bindings") or
                value.get("scope_status") != "unproved" or value.get("production_adoption_allowed") is not False):
            raise ValueError("complete original prediction binding/scope differs: " + identity)
        if value.get("state") not in {"support", "conflict", "unknown"}:
            raise ValueError("unsupported automatic prediction state")
        if variant != "baseline" and request["direction"] == "au_to_rakuten" and value != original:
            raise ValueError("reverse prediction must remain the complete old baseline copy")
        if value["state"] == "support":
            refs = value.get("quote_refs", [])
            literal = isinstance(refs, list) and bool(refs) and all(verify_quote(ref, request, documents) for ref in refs)
            literal_counts["literal_verified_support" if literal else "support_without_verified_literal"] += 1
    # Shared original model requests can carry bindings outside this capped
    # subset. Preserve them rather than manufacturing narrowed bindings.
    original_bindings = {}
    for identity, value in baseline.items():
        group = value.get("source_task_bindings")
        if not isinstance(group, list):
            raise ValueError("baseline needs complete original task bindings")
        for binding in group:
            if binding["request_id"] != identity:
                raise ValueError("baseline source task binds another request")
            old = original_bindings.setdefault(binding["task_id"], binding)
            if old != binding:
                raise ValueError("original shared task bindings conflict")
    for identity, task in tasks.items():
        if original_bindings.get(identity) != task:
            raise ValueError("prepared task is absent/changed in original prediction bindings")
    return dict(literal_counts)


def route_references(case_ids, legacy_labels, novel_labels, output_dir):
    """Called only after all three predictions and gates have been frozen."""
    references, routes = {}, []
    for cohort, path in (("legacy-v3", legacy_labels), ("novel-v1", novel_labels)):
        checksum = sha(path)
        seen = set()
        for line, reference in enumerate(rows(path), 1):
            identity = reference["case_id"]
            if identity in seen:
                raise ValueError("duplicate reference case ID within source")
            seen.add(identity)
            if identity not in case_ids:
                continue
            if identity in references:
                raise ValueError("selected case is ambiguously present in two reference sources")
            route = {"case_id": identity, "reference_cohort": cohort,
                     "source_path": str(path), "source_sha256": checksum, "jsonl_row": line}
            references[identity] = {**reference, "scoring_reference_source": route}
            routes.append(route)
    if set(references) != case_ids:
        raise ValueError("selected errors are missing separate reference labels")
    write_rows(output_dir / "references-for-scoring.jsonl", references.values())
    write_rows(output_dir / "reference-routes.jsonl", routes)
    return references, dict(Counter(r["reference_cohort"] for r in routes))


def reference_label(reference):
    label = reference.get("label", reference.get("decision"))
    if label not in {"matched", "unmatched"}:
        raise ValueError("selected previous errors need resolved matched/unmatched machine references")
    return label


def cell(decision, label):
    accepted, matched = decision["decision"] == "accept", label == "matched"
    return "tp" if accepted and matched else "fp" if accepted else "fn" if matched else "tn"


def expected_rows(reference):
    keys = reference.get("matched_au_row_keys", reference.get("matching_au_row_keys"))
    if keys is None:
        return None
    if not isinstance(keys, list) or not keys or any(not isinstance(v, str) for v in keys):
        raise ValueError("matched row reference needs nonempty whole AU row keys")
    return keys


def compare_variant(baseline, current, references, selected_cells):
    if set(baseline) != set(current) or set(current) != set(selected_cells):
        raise ValueError("gated selected-case sets differ")
    counts, by_product, details = Counter(), defaultdict(Counter), []
    for identity, prediction in current.items():
        reference = references[identity]
        label = reference_label(reference)
        original = baseline[identity]
        previous_cell = cell(original, label)
        if previous_cell != selected_cells[identity] or previous_cell not in {"fp", "fn"}:
            raise ValueError("original capped baseline is not the producer-declared error")
        actual_cell = cell(prediction, label)
        corrected = (previous_cell == "fp" and actual_cell == "tn") or (previous_cell == "fn" and actual_cell == "tp")
        accepted_row_status = "not_accepted"
        if prediction["decision"] == "accept":
            if label == "unmatched":
                accepted_row_status = "false_positive_sku"
            else:
                keys = expected_rows(reference)
                accepted_row_status = ("accepted_matched_row_unscored" if keys is None else
                                       "correct_accepted_row" if prediction["top_row_key"] in keys else "wrong_accepted_row")
                counts[accepted_row_status] += 1
        product = prediction["au_product_id"]
        counts["binary_" + actual_cell] += 1
        by_product[product]["baseline_" + previous_cell] += 1
        by_product[product]["result_" + actual_cell] += 1
        if corrected:
            key = "fp_corrected" if previous_cell == "fp" else "fn_binary_recovered"
            counts[key] += 1
            by_product[product][key] += 1
        if accepted_row_status in {"correct_accepted_row", "wrong_accepted_row", "accepted_matched_row_unscored"}:
            by_product[product][accepted_row_status] += 1
        details.append({"case_id": identity, "au_product_id": product, "previous_error": previous_cell,
                        "baseline_decision": original["decision"], "new_decision": prediction["decision"],
                        "binary_cell": actual_cell, "binary_error_corrected": corrected,
                        "accepted_row_status": accepted_row_status, "top_row_key": prediction["top_row_key"],
                        "source_sku_key": prediction["source_sku_key"], "reason": prediction["reason"]})
    denominators = Counter(selected_cells.values())
    return {
        "selected_case_count": len(current), "previous_error_counts": {k: denominators[k] for k in ("fp", "fn")},
        "fp_corrected": {"count": counts["fp_corrected"], "denominator": denominators["fp"]},
        "fn_binary_recovered": {"count": counts["fn_binary_recovered"], "denominator": denominators["fn"]},
        "fn_correct_row_recovered": counts["correct_accepted_row"],
        "fn_wrong_row_accepted": counts["wrong_accepted_row"],
        "fn_accepted_row_unscored": counts["accepted_matched_row_unscored"],
        "remaining_fp": counts["binary_fp"], "remaining_fn_binary": counts["binary_fn"],
        "binary_confusion_on_selected_previous_errors": {k: counts["binary_" + k] for k in ("tp", "fp", "fn", "tn")},
        "per_product": {k: dict(v) for k, v in sorted(by_product.items())}, "case_results": details,
    }


def run(prepared_dir, run_dir, legacy_labels, novel_labels, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest, requests, documents, tasks, cases = load_prepared(prepared_dir)
    input_paths = [prepared_dir / n for n in ("manifest.json", *SOURCE_NAMES)]
    input_paths.extend(run_dir / (variant + "-predictions.jsonl") for variant in VARIANTS)
    input_paths.extend(p for p in (run_dir / "freeze.json", run_dir / "summary.json", run_dir / "manifest.json") if p.is_file())
    input_paths.extend((Path(__file__), HERE / "integrate_claude_description_v1.py"))
    frozen_sha = {str(path): sha(path) for path in input_paths}
    predictions = {variant: indexed(run_dir / (variant + "-predictions.jsonl"), "request_id") for variant in VARIANTS}
    baseline_predictions = predictions["baseline"]
    if set(baseline_predictions) != set(requests):
        raise ValueError("baseline prediction ID set differs from full original subset requests")
    literals = {variant: validate_predictions(requests, documents, tasks, values, baseline_predictions, variant)
                for variant, values in predictions.items()}
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "freeze.json", {
        "schema_version": "scoped-html-error-evaluation-v1", "labels_read": False,
        "model_inference_run": False, "threshold_search": False, "independent_test": False,
        "production_adoption_allowed": False, "full_dataset_accuracy_measured": False,
        "selection_policy": manifest["selection_policy"], "selected_error_cells": manifest["selected_error_cells"],
        "selected_error_counts": manifest["selected_error_counts"], "input_sha256": frozen_sha,
        "variants": list(VARIANTS), "literal_quote_checks": literals,
        "literal_existence_is_not_scope_proof": True})
    gates, gate_manifests = {}, {}
    for variant in VARIANTS:
        destination = output_dir / variant
        gate_manifests[variant] = finish(prepared_dir, run_dir / (variant + "-predictions.jsonl"), destination)
        gates[variant] = indexed(destination / "decisions.jsonl", "case_id")
        if set(gates[variant]) != set(cases):
            raise ValueError("finished gate case IDs differ from selected subset")
    write_json(output_dir / "all-gates-frozen.json", {"labels_read": False,
        "all_three_predictions_and_gates_frozen": True,
        "gate_manifest_sha256": {variant: sha(output_dir / variant / "manifest.json") for variant in VARIANTS}})
    # This is the first access to separate reference files, after every gate.
    label_sha = {str(path): sha(path) for path in (legacy_labels, novel_labels)}
    references, routed_counts = route_references(set(cases), legacy_labels, novel_labels, output_dir)
    results = {}
    for variant in VARIANTS:
        scoring = score(output_dir / variant, output_dir / "references-for-scoring.jsonl", output_dir / variant / "score-machine.json")
        comparison = compare_variant(gates["baseline"], gates[variant], references, manifest["selected_error_cells"])
        if comparison["binary_confusion_on_selected_previous_errors"] != {k: scoring["metrics"][k] for k in ("tp", "fp", "fn", "tn")}:
            raise ValueError("correction calculation differs from unchanged machine scorer")
        results[variant] = {k: v for k, v in comparison.items() if k not in {"per_product", "case_results"}}
        results[variant].update(gated_decision_counts=gate_manifests[variant]["decision_counts"],
                                reference_kind="machine_labels_not_human_gold")
        write_rows(output_dir / variant / "case-results.jsonl", comparison["case_results"])
        write_json(output_dir / variant / "per-product-errors.json", comparison["per_product"])
    if any(sha(Path(path)) != checksum for path, checksum in {**frozen_sha, **label_sha}.items()):
        raise ValueError("original input, prediction, code or reference changed during evaluation")
    summary = {"schema_version": "scoped-html-error-evaluation-result-v1",
               "evaluation_scope": "capped previously incorrect real cases; correction diagnostic, not full-dataset accuracy",
               "full_dataset_accuracy_measured": False, "independent_test": False,
               "production_adoption_allowed": False, "reference_kind": "machine_labels_not_human_gold",
               "labels_read_after_all_three_predictions_and_gates_frozen": True,
               "threshold_search": False, "model_inference_run": False,
               "selected_case_count": len(cases), "request_count": len(requests),
               "selection_policy": manifest["selection_policy"], "selected_error_counts": manifest["selected_error_counts"],
               "reference_route_counts": routed_counts, "variants": results,
               "input_sha256": frozen_sha, "labels_sha256": label_sha,
               "original_files_sha256_unchanged": True,
               "output_sha256": {str(p.relative_to(output_dir)): sha(p) for p in output_dir.rglob("*") if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    print(json.dumps({"selected_case_count": len(cases), "variants": results}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--legacy-labels", type=Path, required=True)
    parser.add_argument("--novel-labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(**vars(parser.parse_args()))
