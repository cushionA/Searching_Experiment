#!/usr/bin/env python3
"""Compare frozen CPU subset outputs with a full-runtime run, without labels.

Exact raw inputs and request bindings must agree before numeric differences
are computed. GPU/CPU logits need not be bit-identical. No inference, grading,
reference-label reads, or production adoption takes place in this utility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLD = ROOT / ".lab-output/sku-scoped-condition-jev-title-choice-described-20261011-v1"
OLD_NAMES = ("model-requests.jsonl", "classification-options.jsonl", "predictions.jsonl")
NEW_NAMES = ("model-requests.jsonl", "jev-classification-options.jsonl", "jev-predictions.jsonl")
MODEL_FIELDS = ("question", "state", "choices")
IDENTITY_FIELDS = ("model_id", "model_revision", "precision", "format_version")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def semantic_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def rows(path, id_field, selected=None):
    result, seen = {}, set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            identity = row.get(id_field)
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError(f"missing/duplicate {id_field}: {path.name}:{line_number}")
            seen.add(identity)
            if selected is None or identity in selected:
                result[identity] = row
    if selected is not None and set(result) != selected:
        raise ValueError("runtime output is missing old subset request IDs: " + path.name)
    return result


def read_run(directory, names):
    model = rows(directory / names[0], "id")
    scores = rows(directory / names[1], "id")
    predictions = rows(directory / names[2], "request_id")
    if not model or set(model) != set(scores) or set(model) != set(predictions):
        raise ValueError("model/score/prediction request ID sets differ: " + str(directory))
    return model, scores, predictions


def validate_common_inputs(old, new):
    """Fail before interpreting any numeric output if an input differs."""
    old_model, old_scores, old_predictions = old
    new_model, new_scores, new_predictions = new
    if not set(old_model) <= set(new_model):
        raise ValueError("new runtime is missing old subset request IDs")
    for identity, old_input in old_model.items():
        new_input = new_model[identity]
        for field in MODEL_FIELDS:
            if old_input.get(field) != new_input.get(field):
                raise ValueError(f"exact model {field} differs: {identity}")
            for label, score in (("old", old_scores[identity]), ("new", new_scores[identity])):
                if score.get(field) != old_input.get(field):
                    raise ValueError(f"{label} score record differs from exact model {field}: {identity}")
        for field in IDENTITY_FIELDS:
            if old_scores[identity].get(field) != new_scores[identity].get(field):
                raise ValueError(f"model identity {field} differs: {identity}")
        old_prediction, new_prediction = old_predictions[identity], new_predictions[identity]
        if old_prediction.get("source_request") != new_prediction.get("source_request"):
            raise ValueError("complete source_request differs: " + identity)
        for prediction in (old_prediction, new_prediction):
            source = prediction.get("source_request")
            if (not isinstance(source, dict) or source.get("request_id") != identity or
                    prediction.get("request_sha256") != source.get("request_sha256")):
                raise ValueError("prediction identity does not bind its source_request: " + identity)
        if old_prediction.get("state") not in {"support", "unknown"} or new_prediction.get("state") not in {"support", "unknown"}:
            raise ValueError("unsupported development prediction state: " + identity)


def numeric_differences(old_values, new_values, labels, field):
    if old_values is None or new_values is None:
        return {"available": False, "old_available": old_values is not None,
                "new_available": new_values is not None, "max_absolute_difference": None, "values": []}
    if (not isinstance(old_values, dict) or not isinstance(new_values, dict) or
            set(old_values) != set(labels) or set(new_values) != set(labels)):
        raise ValueError("numeric candidate labels differ from exact input choices: " + field)
    differences = []
    for label in labels:
        a, b = old_values[label], new_values[label]
        if (isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or
                not isinstance(b, (int, float)) or not math.isfinite(a) or not math.isfinite(b)):
            raise ValueError("candidate score is not finite numeric data: " + field)
        if field == "probabilities" and (not 0 <= a <= 1 or not 0 <= b <= 1):
            raise ValueError("candidate probability is outside [0, 1]")
        differences.append({"label": label, "old": a, "new": b,
                            "difference": b - a, "absolute_difference": abs(b - a)})
    return {"available": True, "max_absolute_difference": max(r["absolute_difference"] for r in differences),
            "values": differences}


def compare_rows(old, new):
    validate_common_inputs(old, new)
    old_model, old_scores, old_predictions = old
    _, new_scores, new_predictions = new
    comparisons = []
    for identity, model_input in old_model.items():
        before, after = old_scores[identity], new_scores[identity]
        labels = [choice["label"] for choice in model_input["choices"]]
        if not labels or len(labels) != len(set(labels)):
            raise ValueError("original candidate labels must be nonempty and unique")
        comparisons.append({
            "request_id": identity, "request_sha256": old_predictions[identity]["request_sha256"],
            "model_input_sha256": semantic_sha({field: model_input[field] for field in MODEL_FIELDS}),
            "source_request_sha256": semantic_sha(old_predictions[identity]["source_request"]),
            "old_state": old_predictions[identity]["state"], "new_state": new_predictions[identity]["state"],
            "state_changed": old_predictions[identity]["state"] != new_predictions[identity]["state"],
            "old_argmax": before.get("argmax_label"), "new_argmax": after.get("argmax_label"),
            "argmax_changed": before.get("argmax_label") != after.get("argmax_label"),
            "old_status": before.get("status"), "new_status": after.get("status"),
            "status_changed": before.get("status") != after.get("status"),
            "token_counts_identical": before.get("token_counts") == after.get("token_counts"),
            "logits": numeric_differences(before.get("logits"), after.get("logits"), labels, "logits"),
            "probabilities": numeric_differences(before.get("probabilities"), after.get("probabilities"), labels, "probabilities"),
        })
    return {
        "comparison_count": len(comparisons), "new_runtime_request_count": len(new_predictions),
        "exact_model_inputs_verified": True, "complete_source_requests_verified": True,
        "state_change_count": sum(r["state_changed"] for r in comparisons),
        "argmax_change_count": sum(r["argmax_changed"] for r in comparisons),
        "status_change_count": sum(r["status_changed"] for r in comparisons),
        "token_count_change_count": sum(not r["token_counts_identical"] for r in comparisons),
        "max_absolute_logit_difference": max((r["logits"]["max_absolute_difference"] for r in comparisons if r["logits"]["available"]), default=None),
        "max_absolute_probability_difference": max((r["probabilities"]["max_absolute_difference"] for r in comparisons if r["probabilities"]["available"]), default=None),
        "numeric_unavailable_request_count": sum(not r["logits"]["available"] or not r["probabilities"]["available"] for r in comparisons),
        "changed_request_ids": [r["request_id"] for r in comparisons if r["state_changed"] or r["argmax_changed"] or r["status_changed"]],
        "comparisons": comparisons,
    }


def optional_fusion(old_directory, new_directory):
    old_path = old_directory / "predictions.jsonl"
    new_path = new_directory / "fusion-predictions.jsonl"
    old = rows(old_path, "request_id")
    new = rows(new_path, "request_id", selected=set(old))
    comparisons = []
    for identity, before in old.items():
        after = new[identity]
        if before.get("source_request") != after.get("source_request"):
            raise ValueError("optional fusion complete source_request differs: " + identity)
        labels = before["source_request"]["option_values"]
        comparisons.append({"request_id": identity,
                            "old_state": before["state"], "new_state": after["state"],
                            "state_changed": before["state"] != after["state"],
                            "old_winning_value": before["winning_value"], "new_winning_value": after["winning_value"],
                            "winning_value_changed": before["winning_value"] != after["winning_value"],
                            "fused_scores": numeric_differences(before.get("fused_scores"), after.get("fused_scores"), labels, "fused_scores")})
    return {"performed": True, "comparison_count": len(comparisons),
            "state_change_count": sum(r["state_changed"] for r in comparisons),
            "winning_value_change_count": sum(r["winning_value_changed"] for r in comparisons),
            "comparisons": comparisons, "file_sha256": {str(old_path): sha(old_path), str(new_path): sha(new_path)}}


def run(run_dir, output, old_run_dir=DEFAULT_OLD, old_fusion_run_dir=None):
    if output.exists():
        raise FileExistsError(output)
    files = [old_run_dir / name for name in OLD_NAMES] + [run_dir / name for name in NEW_NAMES]
    frozen = {str(path): sha(path) for path in files}
    result = compare_rows(read_run(old_run_dir, OLD_NAMES), read_run(run_dir, NEW_NAMES))
    fusion = optional_fusion(old_fusion_run_dir, run_dir) if old_fusion_run_dir is not None else {"performed": False}
    for path in files:
        if sha(path) != frozen[str(path)]:
            raise ValueError("runtime file changed during comparison: " + str(path))
    report = {"schema_version": "full-condition-runtime-comparison-v1", "labels_read": False,
              "model_inference_run": False, "full_cohort_scoring_run": False, "independent_test": False,
              "production_adoption_allowed": False, "cpu_gpu_bit_identity_required": False,
              "comparison_scope": "previously used CPU development subset only; numeric differences do not measure SKU accuracy",
              "old_run_dir": str(old_run_dir), "new_run_dir": str(run_dir), "file_sha256": frozen,
              "code_sha256": sha(Path(__file__)), **result, "optional_fusion": fusion}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({key: report[key] for key in (
        "comparison_count", "state_change_count", "argmax_change_count",
        "max_absolute_logit_difference", "max_absolute_probability_difference")}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-run-dir", type=Path, default=DEFAULT_OLD)
    parser.add_argument("--old-fusion-run-dir", type=Path)
    run(**vars(parser.parse_args()))
