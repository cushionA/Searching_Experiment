#!/usr/bin/env python3
"""Fresh frozen JEV/NLI baseline inference on a label-free final request subset.

This preserves the original full-condition title + exact target-row task text
and scoring policy. It only adapts the input binding schema emitted by adapter
v2; labels are neither opened nor scored here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time

import trial_full_scoped_condition_choices_v1 as FULL
from trial_scoped_condition_gliner_v1 import canonical, digest, read, sha

HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                "request-model-input-map.jsonl", "baseline-model-inputs.jsonl")


def _indexed(rows, key, name):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {key} in {name}")
    return result


def load_adapter_v2_inputs(input_dir):
    """Return the legacy task's in-memory contract after strict v2 validation."""
    input_dir = Path(input_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("labels_read") is not False or
            manifest.get("labels_in_model_inputs") not in (None, False)):
        raise ValueError("final baseline input must explicitly exclude labels")
    if manifest.get("model_inference_run") is not False:
        raise ValueError("input producer must not have run inference")
    names = ("requests.jsonl", "documents.jsonl", "tasks.jsonl")
    recorded_hashes = manifest.get("output_sha256", manifest.get("outputs_sha256", {}))
    for name in (*names, "request-model-input-map.jsonl", "baseline-model-inputs.jsonl"):
        expected = recorded_hashes.get(name)
        if not expected or sha(input_dir / name) != expected:
            raise ValueError("adapter input checksum differs: " + name)
    requests = read(input_dir / "requests.jsonl")
    documents = read(input_dir / "documents.jsonl")
    tasks = read(input_dir / "tasks.jsonl")
    model_map = read(input_dir / "request-model-input-map.jsonl")
    model_inputs = read(input_dir / "baseline-model-inputs.jsonl")
    lookup = _indexed(requests, "request_id", "requests")
    by_document = _indexed(documents, "document_id", "documents")
    if not requests or not tasks:
        raise ValueError("nonempty request and task subset required")
    map_by_request = _indexed(model_map, "request_id", "request-model-input-map")
    input_by_id = _indexed(model_inputs, "model_input_id", "baseline-model-inputs")
    if set(map_by_request) != set(lookup):
        raise ValueError("model-input map does not cover exactly the subset requests")
    if len(input_by_id) != len(model_inputs):
        raise ValueError("duplicate model-input key")
    manifest_counts = (("request_count", "unique_request_count", len(requests)),
                       ("document_count", "document_count", len(documents)),
                       ("task_count", "task_binding_count", len(tasks)))
    for field, alias, count in manifest_counts:
        declared = manifest.get(field, manifest.get(alias))
        if declared is not None and declared != count:
            raise ValueError("adapter manifest count differs: " + field)
    for document in documents:
        if digest({k: v for k, v in document.items() if k != "document_sha256"}) != document.get("document_sha256"):
            raise ValueError("target document semantic SHA differs")
        if not all(isinstance(document.get(field), dict) and isinstance(document[field].get("text"), str)
                   for field in ("title", "description")):
            raise ValueError("raw title and full description strings required")
    bindings, seen = {}, set()
    for task in tasks:
        request_id = task.get("request_id")
        if task.get("task_id") in seen or request_id not in lookup:
            raise ValueError("duplicate or unbound task")
        seen.add(task["task_id"])
        bindings.setdefault(request_id, []).append(task)
    if set(bindings) != set(lookup):
        raise ValueError("request without source task binding")
    # The adapter deliberately strips this binding-only value from model-facing
    # requests. Reattach it from the frozen source condition references after
    # requiring every row binding for a request to agree.
    for request in requests:
        bound_tasks = bindings[request["request_id"]]
        conditions = [task.get("raw_condition_ref") for task in bound_tasks]
        if any(not isinstance(c, dict) for c in conditions):
            raise ValueError("task lacks its compact source condition reference")
        values = {c.get("value") for c in conditions}
        axes = {c.get("axis_label", c.get("axis_name")) for c in conditions}
        if len(values) != 1 or None in values or axes != {request.get("axis_name")}:
            raise ValueError("source task conditions disagree with request axis/value")
        derived = next(iter(values))
        if "selected_value" in request and request["selected_value"] != derived:
            raise ValueError("supplied binding-only value differs from source task provenance")
        request["selected_value"] = derived
        if request.get("selected_value_is_binding_only") is not True:
            raise ValueError("adapter must mark expected value as binding-only")
    for request in requests:
        FULL.validate_request(request)
        if request.get("production_adoption_allowed") is not False:
            raise ValueError("request must remain experimental")
        document = by_document.get(request["target_document_id"])
        if document is None or document["document_sha256"] != request["target_document_sha256"]:
            raise ValueError("request target document binding differs")
        if not document["title"]["text"] and not request["target_selected_axes_text"]:
            raise ValueError("current title or selected row is required")
        if FULL.UNKNOWN in request["option_values"]:
            raise ValueError("unknown control label collides with a source option")
    for task in tasks:
        request_id = task["request_id"]
        request = lookup[request_id]
        mapped = map_by_request[request_id]
        input_id = request.get("model_input_id")
        if mapped.get("model_input_id") != input_id or input_id not in input_by_id:
            raise ValueError("request-to-frozen-model-input mapping differs")
        model_input = input_by_id[input_id]
        for field in ("direction", "axis_name", "option_values", "target_document_id",
                      "target_document_sha256", "target_selected_axes", "target_selected_axes_text"):
            if model_input.get(field) != request.get(field):
                raise ValueError("request differs from frozen baseline model input: " + field)
        model_fields = ("direction", "axis_name", "option_values", "target_document_id",
                        "target_document_sha256", "target_selected_axes", "target_selected_axes_text")
        semantic_sha = digest({field: model_input[field] for field in model_fields})
        if (model_input.get("selected_value_in_model_input") is not False or
                model_input.get("model_input_sha256") != semantic_sha or
                input_id != "condition-model-input:" + semantic_sha):
            raise ValueError("baseline model input leaks selected value or has an invalid key")
        if (task.get("direction") != request["direction"] or
                task.get("model_input_id") != request.get("model_input_id")):
            raise ValueError("task direction or frozen model-input binding differs")
        condition = task.get("raw_condition_ref")
        if (not isinstance(condition, dict) or condition.get("value") != request["selected_value"] or
                condition.get("axis_label", condition.get("axis_name")) != request["axis_name"]):
            raise ValueError("task condition differs from its whole original request")
        if task.get("production_adoption_allowed") is not False:
            raise ValueError("source task must remain experimental")
        if task.get("status") not in ("ready", "bound", "included", "not_evaluated", None):
            raise ValueError("task is not an included source binding")
    return requests, by_document, bindings, manifest, names


def freeze_inputs(input_dir, output_dir, *, device, jev_chunk_requests, nli_chunk_pairs,
                  jev_model_dir, nli_model_dir):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    hashes = {}
    for name in SOURCE_FILES:
        path = input_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[name] = sha(path)
    code_names = tuple(dict.fromkeys((*FULL.CODE_NAMES, "kaggle_full_condition_choices_v1.py", Path(__file__).name)))
    code_hashes = {name: sha(HERE / name) for name in code_names}
    freeze = {"schema_version": "fresh-fixed-jev-nli-final-subset-v1", "labels_read": False,
              "production_adoption_allowed": False, "independent_test": True,
              "task": "frozen title plus exact target selected axes; no description/body inference",
              "task_implementation": "full-scoped-condition-choices-v1",
              "input_sha256": hashes, "code_sha256": code_hashes,
              "model_dirs": {"jev": str(Path(jev_model_dir).resolve()),
                             "nli": str(Path(nli_model_dir).resolve())},
              "device": device, "precision": "fp32", "threads": 4, "batch_size": 16,
              "jev_chunk_requests": jev_chunk_requests, "nli_chunk_pairs": nli_chunk_pairs,
              "fresh_inference": True}
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen_inputs = output_dir / "source-inputs"
    frozen_inputs.mkdir()
    for name in SOURCE_FILES:
        shutil.copyfile(input_dir / name, frozen_inputs / name)
    with (output_dir / "runtime-source-freeze.json").open("x", encoding="utf-8") as stream:
        json.dump(freeze, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return freeze


def run(input_dir, output_dir, jev_model_dir, nli_model_dir, *, jev_chunk_requests=16,
        nli_chunk_pairs=256, device="cpu"):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    # Validate before creating the output freeze. FULL.run repeats model-contract
    # validation through this adapter loader and copies these exact source files.
    load_adapter_v2_inputs(input_dir)
    if device not in {"cpu", "cuda"}:
        raise ValueError("device must be cpu or cuda")
    freeze = freeze_inputs(input_dir, output_dir, device=device,
                           jev_chunk_requests=jev_chunk_requests, nli_chunk_pairs=nli_chunk_pairs,
                           jev_model_dir=jev_model_dir, nli_model_dir=nli_model_dir)
    FULL.load_inputs = load_adapter_v2_inputs
    FULL.CODE_NAMES = tuple(dict.fromkeys((*FULL.CODE_NAMES, "kaggle_full_condition_choices_v1.py", Path(__file__).name)))
    started = time.perf_counter()
    model_out = output_dir / "model-run"
    runners = {}
    gpu_runtime = None
    original_writer = FULL.write_json
    try:
        def accurate_writer(path, value):
            if Path(path).name in {"freeze.json", "summary.json"}:
                value["device"] = device
                value["precision"] = "fp32"
                value["execution_mode"] = ("verified-cuda-no-fallback" if device == "cuda" else
                                            "verified-cpu")
                if device == "cuda":
                    value["gpu_runtime"] = gpu_runtime
                    value["jev_pin"]["device"] = "cuda"
                    value["nli_pin"]["device"] = "cuda"
            return original_writer(path, value)
        FULL.write_json = accurate_writer
        if device == "cuda":
            import kaggle_full_condition_choices_v1 as GPU
            scorer = GPU.CudaScorer(Path(jev_model_dir), Path(nli_model_dir))
            gpu_runtime = scorer.runtime
            runners = {"jev_runner": scorer.run_choices, "nli_runner": scorer.run_pairs}
        elif device != "cpu":
            raise ValueError("device must be cpu or cuda")
        summary = FULL.run(input_dir, model_out, jev_model_dir=Path(jev_model_dir),
                           nli_model_dir=Path(nli_model_dir), threads=4, batch_size=16,
                           jev_chunk_requests=jev_chunk_requests, nli_chunk_pairs=nli_chunk_pairs, **runners)
    except BaseException as error:
        failure = {"schema_version": "fresh-fixed-jev-nli-failure-v1",
                   "error_type": type(error).__name__, "error": str(error),
                   "elapsed_seconds": time.perf_counter() - started,
                   "runtime_source_freeze_sha256": sha(output_dir / "runtime-source-freeze.json"),
                   "progress": (json.loads((model_out / "progress.json").read_text(encoding="utf-8"))
                                if (model_out / "progress.json").is_file() else None)}
        with (output_dir / "failure.json").open("x", encoding="utf-8") as stream:
            json.dump(failure, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        raise
    finally:
        FULL.write_json = original_writer
    for name, expected in freeze["input_sha256"].items():
        if sha(input_dir / name) != expected:
            raise ValueError("source input changed during inference: " + name)
    for name, expected in freeze["code_sha256"].items():
        if sha(HERE / name) != expected:
            raise ValueError("source code changed during inference: " + name)
    result = {"schema_version": "fresh-fixed-jev-nli-final-subset-v1",
              "labels_read": False, "independent_test": True,
              "production_adoption_allowed": False, "fresh_inference": True,
              "request_count": summary["request_count"], "model_run_dir": "model-run",
              "device": device, "precision": "fp32", "gpu_runtime": gpu_runtime,
              "total_seconds": time.perf_counter() - started,
              "jev_runtime": summary["jev_runtime"], "nli_runtime": summary["nli_runtime"],
              "nli_unique_pair_count": summary["unique_nli_pair_count"],
              "baseline_prediction_states": summary["fusion_prediction_states"],
              "verified_jev_pin": summary["verified_jev_pin"],
              "verified_nli_pin": summary["verified_nli_pin"],
              "runtime_source_freeze_sha256": sha(output_dir / "runtime-source-freeze.json"),
              "model_summary_sha256": sha(model_out / "summary.json"),
              "source_and_code_reverified_after_inference": True}
    with (output_dir / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jev-model-dir", type=Path, default=Path(".deps/sku-fixed-models/jev"))
    parser.add_argument("--nli-model-dir", type=Path, default=Path(".deps/sku-fixed-models/nli"))
    parser.add_argument("--jev-chunk-requests", type=int, default=16)
    parser.add_argument("--nli-chunk-pairs", type=int, default=256)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    result = run(**vars(parser.parse_args(argv)))
    print(canonical({k: result[k] for k in ("request_count", "total_seconds", "nli_unique_pair_count",
                                             "baseline_prediction_states")}), flush=True)


if __name__ == "__main__":
    main()
