#!/usr/bin/env python3
"""Diagnose frozen whole-value GLiNER choices; never adopt a SKU.

Machine annotations are read only after input, transport, output and length
bindings have been checked. Confidence is uncalibrated and no threshold is fit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

THRESHOLDS = (0.5, 0.7, 0.9)
PRIMARY_THRESHOLD = 0.9
MAX_TOKENS = 512
RESERVED = ("[P]", "[L]", "[C]", "[E]", "[R]", "[DESCRIPTION]",
            "[EXAMPLE]", "[OUTPUT]", "(", ")")
BINDING_KEYS = ("task_id", "case_id", "condition_id", "au_row_key", "axis_name",
                "option_values", "selected_value", "source_sku_key",
                "fixed_au_product_ref", "selected_au_row")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def safe_member(base: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe frozen member path")
    return base / relative


def escaped(value: str) -> str:
    for token in RESERVED:
        value = value.replace(token, token.translate(str.maketrans("[]()", "［］（）")))
    return value


def raw_binding(provenance: dict) -> dict:
    return {key: provenance[key] for key in BINDING_KEYS}


def model_premise(source: dict, text_source: str) -> str:
    return source["premise"] if text_source == "premise" else source["provenance"]["original_relation_premise"]


def verify_raw_bindings(sources: list[dict], requests: list[dict], scope: str,
                        text_source: str = "premise") -> None:
    """Rebuild the grouping and indexed schema independently of the runner."""
    groups, source_ids, task_bindings = {}, set(), {}
    for source in sources:
        if source["id"] in source_ids:
            raise ValueError("duplicate original request ID")
        source_ids.add(source["id"])
        p = source["provenance"]
        values = p["option_values"]
        if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                or any(not isinstance(value, str) or not value for value in values)):
            raise ValueError("invalid complete original alternatives")
        if not isinstance(p["axis_name"], str) or not p["axis_name"] or p["selected_value"] not in values:
            raise ValueError("invalid raw axis or selected whole value")
        if p["selected_au_row"]["row_key"] != p["au_row_key"]:
            raise ValueError("original fixed AU row mismatch")
        index = p["candidate_option_index"]
        if type(index) is not int or not 0 <= index < len(values) or p["candidate_option_value"] != values[index]:
            raise ValueError("original alternative index binding mismatch")
        binding = raw_binding(p)
        if p["task_id"] in task_bindings and binding != task_bindings[p["task_id"]]:
            raise ValueError("one task has different original row or alternatives")
        task_bindings[p["task_id"]] = binding
        if scope == "title" and (p.get("scope") != "title" or p["window"]["field_kind"] != "plain_title"):
            raise ValueError("non-title original entered title-only run")
        key = p["task_id"] if scope == "title" else p["source_request_id"]
        groups.setdefault(key, []).append(source)
    if len(groups) != len(requests):
        raise ValueError("source windows missing or duplicated after grouping")
    request_ids = set()
    for request in requests:
        if request["id"] in request_ids or request["id"] not in groups:
            raise ValueError("invalid grouped request ID")
        request_ids.add(request["id"])
        rows = groups[request["id"]]
        first = rows[0]
        p, values = first["provenance"], first["provenance"]["option_values"]
        if (len(rows) != len(values) or
                sorted(row["provenance"]["candidate_option_index"] for row in rows) != list(range(len(values)))):
            raise ValueError("one original window lacks a whole alternative")
        candidate_metadata = ("candidate_option_index", "candidate_option_value", "original_relation_request_id")
        comparable = {k: v for k, v in p.items() if k not in candidate_metadata}
        for row in rows:
            this = {k: v for k, v in row["provenance"].items()
                    if k not in candidate_metadata}
            if row["premise"] != first["premise"] or model_premise(row, text_source) != model_premise(first, text_source) or this != comparable:
                raise ValueError("alternative changes original window or premise")
        premise = model_premise(first, text_source)
        title, separator, _ = premise.partition("\n現在のAU選択行：\n")
        if not separator or not title.startswith("現在のAU商品名："):
            raise ValueError("original premise lacks AU title and selected row")
        row_text = "\n".join(axis["axis_name"] + "：" + axis["value"] for axis in p["selected_au_row"]["axes"])
        text = title + "\n現在のAU選択行：\n" + row_text
        quote = p["window"]["quote"]
        if scope == "title":
            if title.removeprefix("現在のAU商品名：") != quote or premise != text + "\n引用本文：" + quote:
                raise ValueError("title quote or row differs from original premise")
        else:
            if not premise.startswith(text + "\n引用本文：" + quote):
                raise ValueError("full premise differs from original row and quote")
            text = premise
        unknown, suffix = "不明", 0
        while unknown in values:
            suffix += 1
            unknown = f"不明（情報不足）#{suffix}"
        labels = [f"選択肢{index + 1}：{escaped(value)}" for index, value in enumerate(values)] + [escaped(unknown)]
        mapping = dict(zip(labels, [*values, None], strict=True))
        expected = {"text": text, "axis_name": p["axis_name"], "option_values": values,
                    "candidate_labels": [*values, unknown], "unknown_label": unknown,
                    "schema_task_name": escaped(p["axis_name"]), "schema_labels": labels,
                    "schema_label_to_original_value": mapping, "schema_unknown_label": labels[-1],
                    "source_request_ids": [row["id"] for row in rows], "source_premise": premise,
                    "provenance": p, "selected_value_metadata_only": p["selected_value"],
                    "scope_proven": False, "desired_value_injected_into_premise": False}
        if text_source == "original_relation_premise":
            if first["premise"] != quote:
                raise ValueError("quote-only source input differs from literal window")
            expected.update({"text_source": text_source, "source_input_premise": first["premise"],
                             "source_original_relation_request_ids": [row["provenance"]["original_relation_request_id"] for row in rows]})
        elif "text_source" in request:
            expected["text_source"] = text_source
            expected["source_input_premise"] = first["premise"]
            expected["source_original_relation_request_ids"] = [row["provenance"].get("original_relation_request_id") for row in rows]
        if any(request.get(key) != value for key, value in expected.items()):
            raise ValueError("grouped request differs from independently rebuilt whole raw schema")
        if request.get("task_id", p["task_id"]) != p["task_id"] or request.get("input_scope", scope) != scope:
            raise ValueError("grouped task or input scope differs")


def verify_probabilities(record: dict) -> None:
    if record["status"] != "ok":
        if record.get("chosen_value") is not None or record.get("confidence") is not None or record.get("probabilities") is not None:
            raise ValueError("non-inferred window contains a class result")
        return
    probabilities = record["schema_probabilities"]
    if set(probabilities) != set(record["schema_labels"]):
        raise ValueError("output omitted or added a transported whole alternative")
    if any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1
           for value in probabilities.values()) or not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-4):
        raise ValueError("invalid exclusive class probabilities")
    label, confidence = record["chosen_schema_label"], record["confidence"]
    if (label not in probabilities or not math.isfinite(confidence)
            or not math.isclose(confidence, probabilities[label], abs_tol=1e-5)
            or not math.isclose(confidence, max(probabilities.values()), abs_tol=1e-5)):
        raise ValueError("class winner and confidence disagree")
    value = record["schema_label_to_original_value"][label]
    expected_raw = {record["unknown_label"] if raw is None else raw: probabilities[schema]
                    for schema, raw in record["schema_label_to_original_value"].items()}
    if (record["chosen_value"] != value or record["chosen_label"] != (record["unknown_label"] if value is None else value)
            or record["probabilities"] != expected_raw):
        raise ValueError("transported winner differs from whole original value")


def load_verified(run_dir: Path) -> tuple[dict, list[dict], dict]:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    freeze = json.loads((run_dir / "freeze.json").read_text(encoding="utf-8"))
    required = {"source_requests.jsonl", "requests.jsonl", "freeze.json", "predictions.jsonl"}
    if set(summary["output_sha256"]) != required:
        raise ValueError("completed run does not hash every required member")
    for name, expected in summary["output_sha256"].items():
        if sha(safe_member(run_dir, name)) != expected:
            raise ValueError(f"completed output SHA mismatch: {name}")
    for name, expected in freeze["code_sha256"].items():
        if sha(safe_member(run_dir / "code", name)) != expected:
            raise ValueError(f"frozen code SHA mismatch: {name}")
    if not freeze["code_sha256"] or any(summary.get(key) != value for key, value in freeze.items()):
        raise ValueError("summary differs from frozen inference configuration")
    if (freeze.get("labels_read") is not False or freeze.get("production_eligible") is not False
            or freeze.get("scope_proven") is not False):
        raise ValueError("run lacks required non-production, label-free scope declaration")
    if freeze["source_input_sha256"] != sha(run_dir / "source_requests.jsonl") or freeze["requests_sha256"] != sha(run_dir / "requests.jsonl"):
        raise ValueError("frozen input SHA differs")
    scope = freeze.get("input_scope", "title")
    text_source = freeze.get("text_source", "premise")
    if scope not in ("title", "all_windows") or text_source not in ("premise", "original_relation_premise") or freeze["schema_transport"]["reserved_tokens"] != list(RESERVED):
        raise ValueError("unexpected input scope or schema transport")
    sources, requests, records = (read(run_dir / name) for name in
                                  ("source_requests.jsonl", "requests.jsonl", "predictions.jsonl"))
    if len(sources) != freeze["source_request_count"] or len(requests) != freeze["request_count"] or len(records) != len(requests):
        raise ValueError("incomplete run or source request counts")
    verify_raw_bindings(sources, requests, scope, text_source)
    if dict(Counter(record["status"] for record in records)) != summary["status_counts"]:
        raise ValueError("summary status counts differ from complete output")
    for request, record in zip(requests, records, strict=True):
        if any(record.get(key) != value for key, value in request.items()):
            raise ValueError("request/returned record binding mismatch")
        verify_probabilities(record)
    preflight = freeze["preflight"]
    if preflight["max_tokens"] != MAX_TOKENS or preflight["max_len"] is not None or not preflight["includes_schema"]:
        raise ValueError("run differs from whole-schema, untruncated input contract")
    counts = preflight.get("actual_encoder_input_token_counts")
    audit_sha = None
    length_provenance_verified = counts is not None
    if counts is None:
        audit_path = run_dir / "encoder-input-length-audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit_sha = sha(audit_path)
        if audit["requests_sha256"] != freeze["requests_sha256"] or audit["labels_read"] is not False or audit["additional_inference"] is not False:
            raise ValueError("post-run actual length audit does not bind frozen requests")
        dependencies = freeze["loader_dependency_sha256"]
        length_provenance_verified = all(key in audit for key in ("processor_sha256", "compiler_sha256"))
        if length_provenance_verified and audit["processor_sha256"] not in dependencies.values():
            raise ValueError("actual length audit uses a different frozen processor")
        if length_provenance_verified and audit["compiler_sha256"] not in dependencies.values():
            # Early runs pinned the processor and engine but not its compiler.
            # Check that adjacent installed compiler against the later audit;
            # this remains post-run provenance, not an original frozen pin.
            engine_paths = [Path(path) for path in dependencies if Path(path).name == "engine.py"]
            if len(engine_paths) != 1 or sha(engine_paths[0].with_name("compiler.py")) != audit["compiler_sha256"]:
                raise ValueError("actual length audit compiler bytes differ")
        counts = audit["actual_encoder_token_counts"]
        if audit["actual_encoder_max"] != max(counts) or audit["actual_over_512_count"] != sum(n > MAX_TOKENS for n in counts):
            raise ValueError("actual length audit summary differs from counts")
    if len(counts) != len(records) or any(type(n) is not int or n < 1 for n in counts):
        raise ValueError("actual encoder length count mismatch")
    bad_inferred = []
    for record, count in zip(records, counts, strict=True):
        if "actual_encoder_input_token_count" in record and record["actual_encoder_input_token_count"] != count:
            raise ValueError("record differs from actual frozen encoder input length")
        if record["status"] in {"input_too_long", "inference_too_long"} and count <= MAX_TOKENS:
            raise ValueError("in-range input falsely marked long")
        if record["status"] == "ok" and count > MAX_TOKENS:
            bad_inferred.append(record["id"])
        record["diagnostic_actual_encoder_input_token_count"] = count
        record["diagnostic_inference_eligible"] = length_provenance_verified and count <= MAX_TOKENS and record["status"] == "ok"
    receipt = {"raw_whole_value_and_fixed_row_binding_verified": True,
               "indexed_transport_mapping_verified": True, "outputs_and_frozen_code_sha_verified": True,
               "actual_encoder_input_lengths_verified": length_provenance_verified, "actual_encoder_max": max(counts),
               "actual_over_512_count": sum(n > MAX_TOKENS for n in counts),
               "overlong_inferred_window_ids": bad_inferred,
               "strict_512_run_eligible": length_provenance_verified and not bad_inferred,
               "legacy_actual_length_audit_missing_dependency_pins": not length_provenance_verified,
               "actual_length_audit_sha256": audit_sha, "source_input_sha256": freeze["source_input_sha256"],
               "request_count": len(requests), "input_scope": scope, "text_source": text_source,
               "verification_order": "all output, raw binding and actual length checks completed before annotation reads"}
    return summary, records, receipt


def aggregate(records: list[dict], threshold: float) -> list[dict]:
    groups = defaultdict(list)
    for record in records:
        p = record["provenance"]
        groups[(p["case_id"], p["au_row_key"], p["axis_name"], p["selected_value"])].append(record)
    result = []
    for key, rows in groups.items():
        winners = [row for row in rows if row.get("diagnostic_inference_eligible", False)
                   and row.get("chosen_value") is not None and row["confidence"] >= threshold]
        supported_values = {row["chosen_value"] for row in winners}
        support = supported_values == {key[3]}
        p = rows[0]["provenance"]
        result.append({"case_id": key[0], "au_row_key": key[1], "axis_name": key[2], "selected_value": key[3],
                       "task_id": p["task_id"], "condition_id": p["condition_id"],
                       "fixed_au_product_ref": p["fixed_au_product_ref"], "option_values": p["option_values"],
                       "relation": "support" if support else "unknown", "threshold": threshold,
                       "candidate_action": "candidate_support" if support else "drop",
                       "supported_whole_alternatives": [value for value in p["option_values"] if value in supported_values],
                       "selected_support_window_ids": [row["id"] for row in winners if row["chosen_value"] == key[3]],
                       "competing_support_window_ids": [row["id"] for row in winners if row["chosen_value"] != key[3]],
                       "window_count": len(rows), "eligible_window_count": sum(row["diagnostic_inference_eligible"] for row in rows),
                       "scope_proven": False, "whole_sku_adoption": "not_decided", "production_eligible": False})
    return result


def metrics(rows: list[dict], annotations: dict) -> dict:
    confusion, agreement, actual, predicted, correct = Counter(), 0, 0, 0, 0
    for row in rows:
        key = tuple(row[name] for name in ("case_id", "au_row_key", "axis_name", "selected_value"))
        if key not in annotations:
            raise ValueError("machine annotation missing exact whole condition")
        expected, observed = annotations[key]["relation"], row["relation"]
        confusion[f"{expected}->{observed}"] += 1
        agreement += expected == observed
        actual += expected == "support"
        predicted += observed == "support"
        correct += expected == observed == "support"
    return {"count": len(rows), "confusion": dict(confusion), "agreement": agreement,
            "actual_support": actual, "predicted_support": predicted, "true_support": correct,
            "false_support": predicted - correct, "support_precision": correct / predicted if predicted else None,
            "support_precision_defined": bool(predicted), "support_recall": correct / actual if actual else None}


def score(run_dir: Path, annotation_paths: list[Path], output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    summary, records, receipt = load_verified(run_dir)
    annotations = {}
    for path in annotation_paths:
        for annotation in read(path):
            key = tuple(annotation[name] for name in ("case_id", "au_row_key", "axis_name", "selected_value"))
            if key in annotations or annotation["relation"] not in {"support", "conflict", "unknown"}:
                raise ValueError("duplicate or invalid machine annotation")
            annotations[key] = annotation
    condition_keys = {tuple(row["provenance"][name] for name in ("case_id", "au_row_key", "axis_name", "selected_value")) for row in records}
    if condition_keys != set(annotations):
        raise ValueError("annotation condition set differs from complete probe input")
    diagnostics = {}
    for threshold in THRESHOLDS:
        rows = aggregate(records, threshold)
        diagnostics[str(threshold)] = {"metrics": metrics(rows, annotations), "conditions": rows}
    raw_rows = aggregate(records, 0.0)
    result = {"schema_version": "generic-gliner-whole-choice-diagnostics-v1",
              "run_summary_sha256": sha(run_dir / "summary.json"), "prediction_sha256": sha(run_dir / "predictions.jsonl"),
              "code_sha256": sha(Path(__file__)), "annotation_sha256": {str(path): sha(path) for path in annotation_paths},
              "verification": receipt, "primary_threshold": PRIMARY_THRESHOLD, "thresholds_for_diagnostics": list(THRESHOLDS),
              "thresholds_fitted_to_labels": False, "retrospective_diagnostic_grid": True,
              "gliner_operating_threshold_preregistered": False, "confidence_calibrated": False,
              "independent_final_test": False, "machine_labels_not_human_gold": True,
              "full_page_annotations_vs_available_evidence_only": True, "scope_proven": False,
              "whole_sku_accuracy": False, "production_eligible": False, "class_match_is_semantic_proof": False,
              "different_raw_winner_proves_selected_conflict": False,
              "diagnostics": diagnostics, "unthresholded_choice_diagnostic": {
                  "description": "Uncalibrated whole class winners, with competing alternatives abstained; no semantic proof",
                  "metrics": metrics(raw_rows, annotations), "conditions": raw_rows},
              "inference_seconds": summary["inference_seconds"]}
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--annotation-path", type=Path, action="append", required=True, dest="annotation_paths")
    parser.add_argument("--output", type=Path, required=True)
    result = score(**vars(parser.parse_args()))
    print(json.dumps({threshold: diagnostic["metrics"] for threshold, diagnostic in result["diagnostics"].items()}, ensure_ascii=False))
