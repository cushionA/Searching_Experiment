#!/usr/bin/env python3
"""Compare a frozen CPU product-model run with the same-input CUDA replay.

This is an execution parity diagnostic only. It reads model artifacts and
predictions, never labels, and reports FP32 numerical deltas without requiring
bitwise equality.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import integrate_claude_description_v1 as GATE
import trial_product_condition_models_v1 as PRODUCT

RUNTIME_FIELDS = {"latency_seconds", "device", "precision", "pins", "cache_reused",
                  "runtime", "gpu_runtime", "gpu_stage_telemetry", "total_seconds",
                  "inference_seconds", "tokenization_seconds", "model_load_seconds",
                  "file_verification_seconds", "synchronized_inference_timing"}
SCORE_FILES = {
    "retrieval": "retrieval-unique-scores.jsonl",
    "jev": "jev-unique-scores.jsonl",
    "nli": "nli-unique-scores.jsonl",
}
PREDICTION_VARIANTS = PRODUCT.VARIANTS


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path: Path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def index_rows(path: Path, key: str):
    result = {}
    for row in read_rows(path):
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError(f"missing or duplicate {key} in {Path(path).name}")
        result[identity] = row
    return result


def locate(root: Path, name: str, *, required=True) -> Path | None:
    root = Path(root)
    candidates = [root / name, root / "product-model" / name, root / "parity44" / name,
                  root / "parity44" / "product-model" / name]
    for path in candidates:
        if path.is_file():
            return path
    if required:
        raise FileNotFoundError(f"{name} not found under {root}")
    return None


def compare_exact_files(cpu_dir: Path, gpu_dir: Path, prepared_dir: Path | None = None):
    """Require identical frozen model inputs and share contract before comparison."""
    cpu_manifest = read_json(locate(cpu_dir, "source-manifest.json"))
    gpu_manifest = read_json(locate(gpu_dir, "source-manifest.json"))
    cpu_contract = read_json(locate(cpu_dir, "sharing-contract.json"))
    gpu_contract = read_json(locate(gpu_dir, "sharing-contract.json"))
    if cpu_contract != gpu_contract:
        raise ValueError("CPU/GPU sharing contract drift")
    # Source output hashes cover prepared request/document/task/block/prediction
    # files. Paths and transfer metadata in source-manifest are not compared.
    cpu_outputs = cpu_manifest.get("output_sha256", {})
    gpu_outputs = gpu_manifest.get("output_sha256", {})
    required_names = {"requests.jsonl", "documents.jsonl", "tasks.jsonl", "blocks.jsonl",
                      "retrieval-requests.jsonl", "baseline-predictions.jsonl",
                      "case_plans.jsonl", "row_plans.jsonl"}
    if not required_names <= set(cpu_outputs) or not required_names <= set(gpu_outputs):
        raise ValueError("source manifests do not pin the complete parity input set")
    if any(cpu_outputs[name] != gpu_outputs[name] for name in required_names):
        raise ValueError("CPU/GPU source file SHA drift")
    cpu_freeze = read_json(locate(cpu_dir, "freeze.json"))
    gpu_freeze = read_json(locate(gpu_dir, "execution-freeze.json", required=False)
                           or locate(gpu_dir, "freeze.json"))
    cpu_inputs = cpu_freeze.get("input_sha256", {})
    gpu_inputs = gpu_freeze.get("input_sha256", {})
    # GPU execution freezes can include path-qualified source inputs; normalize
    # by basename while rejecting collisions or any changed digest.
    def by_basename(mapping):
        out = {}
        for name, digest in mapping.items():
            base = Path(name).name
            if base in out and out[base] != digest:
                raise ValueError("ambiguous source freeze basename: " + base)
            out[base] = digest
        return out
    cpu_inputs, gpu_inputs = by_basename(cpu_inputs), by_basename(gpu_inputs)
    expected = {"manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                "baseline-predictions.jsonl", "blocks.jsonl", "retrieval-requests.jsonl"}
    if not expected <= set(cpu_inputs) or not expected <= set(gpu_inputs):
        raise ValueError("execution freezes lack the exact parity source files")
    if any(cpu_inputs[name] != gpu_inputs[name] for name in expected):
        raise ValueError("CPU/GPU execution freeze input SHA drift")
    if prepared_dir is not None:
        for name in required_names:
            path = Path(prepared_dir) / name
            if not path.is_file() or sha(path) != cpu_outputs[name]:
                raise ValueError("selected prepared input differs from frozen CPU/GPU source: " + name)
        manifest_path = Path(prepared_dir) / "manifest.json"
        if not manifest_path.is_file() or sha(manifest_path) != cpu_inputs["manifest.json"]:
            raise ValueError("selected prepared manifest differs from frozen parity source")
    return {"sharing_contract_sha256": sha(locate(cpu_dir, "sharing-contract.json")),
            "source_output_sha256": {name: cpu_outputs[name] for name in sorted(required_names)},
            "frozen_input_sha256": {name: cpu_inputs[name] for name in sorted(expected)}}


def _model_pin(summary: dict, stage: str, cpu_records: dict | None = None):
    if stage == "reranker":
        identity = summary.get("model_identity", {}).get("reranker", {})
        return {"model_id": identity.get("repo"), "revision": identity.get("revision"),
                "file_sha256": {name: item.get("sha256") for name, item in identity.get("files", {}).items()},
                "device": "cpu", "precision": "int8"}
    cpu_identity = summary.get("model_identity", {}).get(stage, {})
    if cpu_records:
        exemplar = next(iter(cpu_records.values()))
        pin = exemplar.get("pins", {}) if stage == "jev" else {}
    else:
        pin = {}
    model_id = cpu_identity.get("model_id") or pin.get("model_id")
    revision = cpu_identity.get("revision") or pin.get("revision")
    files = (cpu_identity.get("file_sha256") or cpu_identity.get("files_sha256") or
             pin.get("file_sha256") or pin.get("files_sha256"))
    precision = cpu_identity.get("precision") or pin.get("precision")
    device = cpu_identity.get("device") or pin.get("device")
    return {"model_id": model_id, "revision": revision, "file_sha256": files,
            "device": device, "precision": precision,
            "context_template": cpu_identity.get("context_template", pin.get("context_template")),
            "candidate_template": cpu_identity.get("candidate_template", pin.get("candidate_template")),
            "empty_description_candidate_template": cpu_identity.get(
                "empty_description_candidate_template", pin.get("empty_description_candidate_template"))}


def _gpu_pin(summary: dict, stage: str):
    key = "verified_" + stage + "_pin"
    pin = summary.get(key, {})
    identity = summary.get("model_identity", {}).get(stage, {})
    files = pin.get("file_sha256") or pin.get("files_sha256")
    files = files or identity.get("file_sha256") or identity.get("files_sha256")
    if stage == "jev":
        files = files or pin.get("files")
        if isinstance(files, dict) and files and all(isinstance(v, dict) for v in files.values()):
            files = {name: item.get("sha256") for name, item in files.items()}
    return {"model_id": pin.get("model_id") or identity.get("model_id"),
            "revision": pin.get("revision") or identity.get("revision"),
            "file_sha256": files, "device": pin.get("device"), "precision": pin.get("precision"),
            "context_template": pin.get("context_template", identity.get("context_template")),
            "candidate_template": pin.get("candidate_template", identity.get("candidate_template")),
            "empty_description_candidate_template": pin.get("empty_description_candidate_template",
                                                                identity.get("empty_description_candidate_template"))}


def verify_pins(cpu_summary: dict, gpu_summary: dict, cpu_records: dict):
    pins = {}
    for stage in ("jev", "nli"):
        cpu = _model_pin(cpu_summary, stage, cpu_records if stage == "jev" else None)
        gpu = _gpu_pin(gpu_summary, stage)
        if not cpu["model_id"] or not cpu["revision"] or not cpu["file_sha256"]:
            raise ValueError(f"CPU {stage} model pin is incomplete")
        if (gpu["model_id"] != cpu["model_id"] or gpu["revision"] != cpu["revision"]
                or gpu["file_sha256"] != cpu["file_sha256"]):
            raise ValueError(f"CPU/GPU {stage} model revision/file pin drift")
        if cpu["device"] != "cpu" or cpu["precision"] != "fp32":
            raise ValueError(f"CPU {stage} scorer is not pinned CPU FP32")
        if gpu["device"] != "cuda" or gpu["precision"] != "fp32":
            raise ValueError(f"GPU {stage} scorer is not pinned CUDA FP32")
        for template in ("context_template", "candidate_template", "empty_description_candidate_template"):
            if cpu.get(template) is not None and gpu.get(template) is not None and cpu[template] != gpu[template]:
                raise ValueError(f"CPU/GPU {stage} {template} drift")
        pins[stage] = {"model_id": cpu["model_id"], "revision": cpu["revision"],
                       "file_sha256": cpu["file_sha256"], "cpu_device": cpu["device"],
                       "gpu_device": gpu["device"], "precision": "fp32"}
    cpu_reranker = cpu_summary.get("model_identity", {}).get("reranker", {})
    gpu_reranker = gpu_summary.get("model_identity", {}).get("reranker", {})
    def reranker_files(identity):
        return {name: item.get("sha256") for name, item in identity.get("files", {}).items()}
    if (not cpu_reranker or cpu_reranker.get("repo") != gpu_reranker.get("repo")
            or cpu_reranker.get("revision") != gpu_reranker.get("revision")
            or reranker_files(cpu_reranker) != reranker_files(gpu_reranker)):
        raise ValueError("CPU/GPU reranker model revision/file pin drift")
    if cpu_reranker.get("onnx_execution_provider") != "CPUExecutionProvider":
        raise ValueError("CPU reranker was not pinned to CPU int8 ONNX")
    pins["reranker"] = {"model_id": cpu_reranker.get("repo"), "revision": cpu_reranker.get("revision"),
                        "file_sha256": reranker_files(cpu_reranker), "device": "cpu", "precision": "int8"}
    return pins


def compare_policies(cpu: dict, gpu: dict):
    fields = ("top_k", "minimum_votes", "verification_policy", "input_length_policy",
              "unknown_description", "variants", "wanted_value_separately_injected")
    cpu_view = {k: cpu.get(k) for k in fields if k in cpu}
    gpu_view = {k: gpu.get(k) for k in fields if k in gpu}
    if cpu_view != gpu_view:
        raise ValueError("CPU/GPU top-k, decision, unknown, or input policy drift")
    # JEV prompt/candidate templates are part of the model pin, not just score metadata.
    return cpu_view


def numeric_delta(left, right):
    if not isinstance(left, (int, float)) or isinstance(left, bool) or not math.isfinite(float(left)):
        raise ValueError("score is not finite numeric data")
    if not isinstance(right, (int, float)) or isinstance(right, bool) or not math.isfinite(float(right)):
        raise ValueError("score is not finite numeric data")
    left, right = float(left), float(right)
    absolute = abs(left - right)
    relative = absolute / max(abs(left), abs(right), 1e-30)
    return absolute, relative


def compare_score_maps(cpu: dict, gpu: dict, kind: str, *, value_fields: tuple[str, ...],
                       argmax_field: str | None):
    if set(cpu) != set(gpu):
        raise ValueError(f"CPU/GPU {kind} cache-key coverage differs ({len(cpu)} vs {len(gpu)})")
    maxima = {field: {"max_abs": 0.0, "max_rel": 0.0, "compared_values": 0}
              for field in value_fields}
    argmax_flips = 0
    status_disagreements = 0
    details = []
    for key in sorted(cpu):
        left, right = cpu[key], gpu[key]
        if left.get("status") != right.get("status"):
            status_disagreements += 1
        entry = {"cache_key": key}
        for field in value_fields:
            a, b = left.get(field), right.get(field)
            if a is None or b is None:
                if a != b:
                    raise ValueError(f"CPU/GPU {kind} {field} availability differs for {key}")
                continue
            if not isinstance(a, dict) or not isinstance(b, dict) or set(a) != set(b):
                raise ValueError(f"CPU/GPU {kind} {field} class labels differ for {key}")
            abs_values, rel_values = [], []
            for label in a:
                absolute, relative = numeric_delta(a[label], b[label])
                abs_values.append(absolute)
                rel_values.append(relative)
            maxima[field]["max_abs"] = max([maxima[field]["max_abs"], *abs_values])
            maxima[field]["max_rel"] = max([maxima[field]["max_rel"], *rel_values])
            maxima[field]["compared_values"] += len(abs_values)
            entry[field + "_max_abs"] = max(abs_values, default=0.0)
        if argmax_field:
            cpu_arg, gpu_arg = left.get(argmax_field), right.get(argmax_field)
            if cpu_arg is None and isinstance(left.get("probabilities"), dict) and left["probabilities"]:
                cpu_arg = max(left["probabilities"], key=left["probabilities"].get)
            if gpu_arg is None and isinstance(right.get("probabilities"), dict) and right["probabilities"]:
                gpu_arg = max(right["probabilities"], key=right["probabilities"].get)
            flip = cpu_arg != gpu_arg
            argmax_flips += flip
            entry.update(cpu_argmax=cpu_arg, gpu_argmax=gpu_arg, argmax_flip=flip)
        if status_disagreements and left.get("status") != right.get("status"):
            entry["status_disagreement"] = True
        details.append(entry)
    return {"cache_key_count": len(cpu), "status_disagreement_count": status_disagreements,
            "argmax_flip_count": argmax_flips, "numerical": maxima, "records": details}


def compare_retrieval(cpu_dir: Path, gpu_dir: Path):
    cpu_scores = index_rows(locate(cpu_dir, SCORE_FILES["retrieval"]), "cache_key")
    gpu_scores = index_rows(locate(gpu_dir, SCORE_FILES["retrieval"]), "cache_key")
    score_comparison = compare_score_maps(cpu_scores, gpu_scores, "retrieval",
                                          value_fields=("raw_logit",), argmax_field=None)
    if score_comparison["status_disagreement_count"]:
        raise ValueError("CPU int8 reranker status differs for an identical input/cache key")
    if score_comparison["numerical"]["raw_logit"]["max_abs"] != 0:
        raise ValueError("CPU int8 reranker raw logits differ")
    cpu_ranks = index_rows(locate(cpu_dir, "retrieval-selections.jsonl"), "request_id")
    gpu_ranks = index_rows(locate(gpu_dir, "retrieval-selections.jsonl"), "request_id")
    if set(cpu_ranks) != set(gpu_ranks):
        raise ValueError("CPU/GPU retrieval ranking coverage differs")
    rank_diff = [key for key in sorted(cpu_ranks)
                 if cpu_ranks[key].get("ordered_block_ids") != gpu_ranks[key].get("ordered_block_ids")]
    if rank_diff:
        raise ValueError(f"CPU int8 reranker ranking order differs for {len(rank_diff)} requests")
    return {"ranking_count": len(cpu_ranks), "ranking_order_disagreement_count": 0,
            "raw_logit_exact_match": True, "score_comparison": {k: v for k, v in score_comparison.items() if k != "records"}}


def compare_semantic(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        keys = (set(left) | set(right)) - RUNTIME_FIELDS
        return {key: compare_semantic(left.get(key), right.get(key)) for key in sorted(keys)
                if left.get(key) != right.get(key)}
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {"length": [len(left), len(right)]}
        return {str(i): compare_semantic(a, b) for i, (a, b) in enumerate(zip(left, right)) if a != b}
    return {"cpu": left, "gpu": right}


def compare_predictions(cpu_dir: Path, gpu_dir: Path):
    result = {}
    diffs = []
    for variant in PREDICTION_VARIANTS:
        name = variant + "-predictions.jsonl"
        cpu = index_rows(locate(cpu_dir, name), "request_id")
        gpu = index_rows(locate(gpu_dir, name), "request_id")
        if set(cpu) != set(gpu):
            raise ValueError(f"CPU/GPU {variant} prediction coverage differs")
        count = 0
        for identity in sorted(cpu):
            diff = compare_semantic(cpu[identity], gpu[identity])
            if diff:
                count += 1
                diffs.append({"variant": variant, "request_id": identity, "semantic_diff": diff})
        result[variant] = {"request_count": len(cpu), "semantic_prediction_disagreement_count": count}
    return result, diffs


def compare_gates(prepared_dir: Path, cpu_dir: Path, gpu_dir: Path, output_dir: Path):
    output = {}
    details = []
    for variant in PREDICTION_VARIANTS:
        cpu_finish = output_dir / "cpu-gates" / variant
        gpu_finish = output_dir / "gpu-gates" / variant
        GATE.finish(prepared_dir, locate(cpu_dir, variant + "-predictions.jsonl"), cpu_finish)
        GATE.finish(prepared_dir, locate(gpu_dir, variant + "-predictions.jsonl"), gpu_finish)
        cpu_decisions = index_rows(cpu_finish / "decisions.jsonl", "case_id")
        gpu_decisions = index_rows(gpu_finish / "decisions.jsonl", "case_id")
        if set(cpu_decisions) != set(gpu_decisions):
            raise ValueError("CPU/GPU final gate case coverage differs: " + variant)
        changed_rows = gate_binary_differences(cpu_decisions, gpu_decisions)
        changed = len(changed_rows)
        details.extend({"variant": variant, **row} for row in changed_rows)
        output[variant] = {"case_count": len(cpu_decisions), "binary_disagreement_count": changed}
    return output, details


def gate_binary_differences(cpu_decisions: dict, gpu_decisions: dict):
    if set(cpu_decisions) != set(gpu_decisions):
        raise ValueError("CPU/GPU final gate case coverage differs")
    changed = []
    for case_id in sorted(cpu_decisions):
        a, b = cpu_decisions[case_id], gpu_decisions[case_id]
        if a.get("decision") not in {"accept", "drop"} or b.get("decision") not in {"accept", "drop"}:
            raise ValueError("unsupported final gate decision")
        if a["decision"] != b["decision"]:
            changed.append({"case_id": case_id, "cpu_decision": a["decision"],
                            "gpu_decision": b["decision"], "cpu_top_row_key": a.get("top_row_key"),
                            "gpu_top_row_key": b.get("top_row_key")})
    return changed


def runtime_table(cpu_summary: dict, gpu_summary: dict):
    cpu_rows = {"reranker": cpu_summary.get("reranker_runtime", {}),
                "jev": cpu_summary.get("jev_runtime", {}), "nli": cpu_summary.get("nli_runtime", {})}
    telemetry = gpu_summary.get("gpu_stage_telemetry", {})
    gpu_reranker = gpu_summary.get("reranker_runtime", {})
    gpu_rows = {"reranker": gpu_reranker, "jev": telemetry.get("jev", {}), "nli": telemetry.get("nli", {})}
    stages = {}
    for stage in ("reranker", "jev", "nli"):
        cpu, gpu = cpu_rows[stage], gpu_rows[stage]
        stages[stage] = {
            "cpu_inference_seconds": cpu.get("inference_seconds"),
            "gpu_inference_seconds": gpu.get("inference_seconds"),
            "cpu_tokenization_seconds": cpu.get("tokenization_seconds"),
            "gpu_tokenization_seconds": gpu.get("tokenization_seconds"),
            "cpu_file_verification_seconds": cpu.get("file_verification_seconds",
                                                       cpu.get("model_load_and_file_verification_seconds")),
            "gpu_file_verification_seconds": gpu.get("file_verification_seconds"),
            "cpu_model_load_seconds": cpu.get("model_load_seconds"),
            "gpu_model_load_seconds": gpu.get("model_load_seconds"),
        }
    cpu_inference = sum((row.get("inference_seconds") or 0) for row in cpu_rows.values())
    gpu_inference = sum((row.get("inference_seconds") or 0) for row in gpu_rows.values())
    return {"stages": stages, "inference_seconds_sum": {"cpu": cpu_inference, "gpu": gpu_inference},
            "cpu_total_seconds": cpu_summary.get("total_seconds"),
            "gpu_total_job_seconds": gpu_summary.get("total_gpu_job_seconds"),
            "interpretation": "Stage inference sums exclude model download/acquisition, job startup, file verification, model loading, tokenization, source verification, and output saving. CPU total and GPU job total have different boundaries and must not be read as an end-to-end speedup."}


def run(cpu_dir: Path, gpu_dir: Path, prepared_dir: Path, output_dir: Path):
    cpu_dir, gpu_dir, prepared_dir, output_dir = map(Path, (cpu_dir, gpu_dir, prepared_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    cpu_summary = read_json(locate(cpu_dir, "summary.json"))
    gpu_summary = read_json(locate(gpu_dir, "summary.json"))
    if cpu_summary.get("labels_read") is not False or gpu_summary.get("labels_read") is not False:
        raise ValueError("parity sources must be frozen label-free model runs")
    if cpu_summary.get("independent_test") is not False or gpu_summary.get("independent_test") is not False:
        raise ValueError("independent-test source is prohibited")
    source_verification = compare_exact_files(cpu_dir, gpu_dir, prepared_dir)
    cpu_jev = index_rows(locate(cpu_dir, SCORE_FILES["jev"]), "cache_key")
    gpu_jev = index_rows(locate(gpu_dir, SCORE_FILES["jev"]), "cache_key")
    cpu_nli = index_rows(locate(cpu_dir, SCORE_FILES["nli"]), "cache_key")
    gpu_nli = index_rows(locate(gpu_dir, SCORE_FILES["nli"]), "cache_key")
    pin_summary = verify_pins(cpu_summary, gpu_summary, cpu_jev)
    policy_summary = compare_policies(cpu_summary, gpu_summary)
    retrieval = compare_retrieval(cpu_dir, gpu_dir)
    jev = compare_score_maps(cpu_jev, gpu_jev, "jev", value_fields=("logits", "probabilities"),
                             argmax_field="argmax_label")
    nli_logits_present = all("logits" in row for row in cpu_nli.values()) and all("logits" in row for row in gpu_nli.values())
    nli_fields = ("probabilities", "logits") if nli_logits_present else ("probabilities",)
    nli = compare_score_maps(cpu_nli, gpu_nli, "nli", value_fields=nli_fields,
                             argmax_field="predicted_class")
    prediction_summary, prediction_diffs = compare_predictions(cpu_dir, gpu_dir)
    output_dir.mkdir(parents=True)
    gate_summary, gate_diffs = compare_gates(prepared_dir, cpu_dir, gpu_dir, output_dir)
    runtime = runtime_table(cpu_summary, gpu_summary)
    write_rows = lambda path, values: path.write_text("".join(json.dumps(v, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for v in values), encoding="utf-8")
    write_rows(output_dir / "prediction-semantic-diffs.jsonl", prediction_diffs)
    write_rows(output_dir / "final-gate-binary-diffs.jsonl", gate_diffs)
    score_diffs = []
    for stage, comparison in (("jev", jev), ("nli", nli)):
        for row in comparison["records"]:
            if row.get("argmax_flip") or any(row.get(field + "_max_abs", 0.0) > 0
                                               for field in comparison["numerical"]):
                score_diffs.append({"stage": stage, **row})
    write_rows(output_dir / "score-key-parity-diffs.jsonl", score_diffs)
    result = {"schema_version": "product-gpu-cpu-parity-v1", "labels_read": False,
        "independent_test": False, "production_adoption_allowed": False,
        "parity_claim": "same-input execution comparison; FP32 CUDA numerical values are measured, not required to be bitwise identical",
        "source_verification": source_verification, "model_pins": pin_summary,
        "policy": policy_summary, "retrieval": retrieval,
        "jev": {k: v for k, v in jev.items() if k != "records"},
        "nli": {k: v for k, v in nli.items() if k != "records"},
        "nli_raw_logits_available_in_both_runs": nli_logits_present,
        "nli_logits_note": ("raw logits compared" if nli_logits_present else
                            "raw logits absent from CPU source; probabilities compared without reconstructing logits"),
        "relative_difference_definition": "abs(cpu-gpu) / max(abs(cpu), abs(gpu), 1e-30)",
        "predictions": prediction_summary, "final_gate": gate_summary, "runtime": runtime,
        "gpu_telemetry": {stage: gpu_summary.get("gpu_stage_telemetry", {}).get(stage, {})
                          for stage in ("jev", "nli")},
        "cpu_total_seconds": cpu_summary.get("total_seconds"),
        "gpu_total_job_seconds": gpu_summary.get("total_gpu_job_seconds"),
        "output_sha256": {str(p.relative_to(output_dir)): sha(p) for p in output_dir.rglob("*")
                          if p.is_file() and p.name != "summary.json"}}
    (output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                                        allow_nan=False) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-dir", type=Path, required=True,
                        help="frozen CPU product-model run, such as models44/")
    parser.add_argument("--gpu-dir", type=Path, required=True,
                        help="CUDA run root, such as parity44/")
    parser.add_argument("--prepared-dir", type=Path, required=True,
                        help="same frozen label-free 44-case prepared input directory")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    result = run(**args)
    print(json.dumps({"jev_argmax_flips": result["jev"]["argmax_flip_count"],
                      "nli_argmax_flips": result["nli"]["argmax_flip_count"],
                      "final_gate": result["final_gate"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
