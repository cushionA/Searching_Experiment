#!/usr/bin/env python3
"""Fresh frozen JEV/NLI baseline inference on a label-free DEV request subset.

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
import trial_generic_model_jev_v1 as JEV
import trial_generic_model_nli_v1 as NLI
from trial_scoped_condition_gliner_v1 import ROOT, canonical, digest, read, sha

HERE = Path(__file__).resolve().parent
SOURCE_FILES = ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                "request-model-input-map.jsonl", "baseline-model-inputs.jsonl",
                "case-plans.jsonl", "membership.jsonl", "selection-plan.json", "row-plans.jsonl")


def _indexed(rows, key, name):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {key} in {name}")
    return result


def prepare_dev_baseline_inputs(subset_dir, output_dir):
    """Bind the exact frozen subset to its already-pinned, label-free DEV model-input records."""
    subset_dir, output_dir = Path(subset_dir), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    raw_manifest = json.loads((subset_dir / "manifest.json").read_text(encoding="utf-8"))
    source_dir = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
    source_manifest_path = source_dir / "manifest.json"
    if sha(source_manifest_path) != raw_manifest.get("full_dev_source_manifest_sha256"):
        raise ValueError("full DEV source manifest pin differs")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (source_manifest.get("split") != "dev" or source_manifest.get("independent_test") is not False or
            source_manifest.get("labels_read") is not False or source_manifest.get("model_inference_run") is not False):
        raise ValueError("pinned full source must be label-free DEV inputs without inference")
    for name, expected in source_manifest.get("outputs_sha256", {}).items():
        if sha(source_dir / name) != expected:
            raise ValueError("pinned full DEV source file checksum differs: " + name)
    requests = read(subset_dir / "requests.jsonl")
    request_ids = {r["request_id"] for r in requests}
    if len(request_ids) != len(requests):
        raise ValueError("duplicate selected request IDs")
    source_requests = {r["request_id"]: r for r in read(source_dir / "requests.jsonl")}
    if not request_ids.issubset(source_requests):
        raise ValueError("subset request is absent from pinned DEV source requests")
    # Preserve the original choice-model input semantics, while adding no desired
    # selected value to those model inputs. Only filter source-pinned map rows.
    model_map = [r for r in read(source_dir / "request-model-input-map.jsonl") if r["request_id"] in request_ids]
    if {r["request_id"] for r in model_map} != request_ids:
        raise ValueError("pinned DEV request-model map does not exactly cover the subset")
    model_input_ids = {r["model_input_id"] for r in model_map}
    model_inputs = [r for r in read(source_dir / "baseline-model-inputs.jsonl")
                    if r["model_input_id"] in model_input_ids]
    if {r["model_input_id"] for r in model_inputs} != model_input_ids:
        raise ValueError("pinned DEV baseline inputs do not cover mapped model IDs")
    input_index = {r["model_input_id"]: r for r in model_inputs}
    map_index = {r["request_id"]: r for r in model_map}
    model_fields = ("direction", "axis_name", "option_values", "target_document_id",
                    "target_document_sha256", "target_selected_axes", "target_selected_axes_text")
    for r in requests:
        if r != source_requests[r["request_id"]]:
            raise ValueError("subset request differs from the original frozen DEV request")
        model_input_id = map_index[r["request_id"]]["model_input_id"]
        if model_input_id != r["model_input_id"]:
            raise ValueError("source DEV mapping differs from selected request")
        model_input = input_index[model_input_id]
        if any(model_input.get(f) != r.get(f) for f in model_fields):
            raise ValueError("filtered frozen model input differs from request: " + r["request_id"])
        if model_input.get("selected_value_in_model_input") is not False or "selected_value" in model_input:
            raise ValueError("desired selected value leaked into frozen model input")
    output_dir.mkdir(parents=True,exist_ok=False)
    names=("requests.jsonl","documents.jsonl","tasks.jsonl","case-plans.jsonl","membership.jsonl",
           "selection-plan.json","row-plans.jsonl")
    for name in names:
        shutil.copyfile(subset_dir/name,output_dir/name)
    for name, rows in (("request-model-input-map.jsonl",model_map),("baseline-model-inputs.jsonl",model_inputs)):
        output_dir.joinpath(name).write_text("".join(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n" for r in rows),encoding="utf-8")
    manifest=dict(raw_manifest)
    manifest.update({"schema_version":raw_manifest["schema_version"],
                     "baseline_input_adapter_schema":"fresh-dev-v7-whole-sku-gate-baseline-input-v1",
                     "phase":"source_inputs_bound_to_frozen_baseline_model_inputs",
                     "split":"dev","independent_test":False,"development_only":True,
                     "labels_read":False,"test_split_read":False,"model_inference_run":False,
                     "gate_evaluation_run":False,"scores_created":False,
                     "selected_request_count":len(requests),"selected_document_count":len(read(output_dir/"documents.jsonl")),
                     "filtered_model_input_count":len(model_inputs),"model_input_files_filtered_from_full_dev":True,
                     "source_adapter_manifest_sha256":sha(subset_dir/"manifest.json"),
                     "model_map_source_sha256":sha(source_dir/"request-model-input-map.jsonl"),
                     "baseline_model_inputs_source_sha256":sha(source_dir/"baseline-model-inputs.jsonl"),
                     "outputs_sha256":{name:sha(output_dir/name) for name in names},
                     "dev_baseline_outputs_sha256":{name:sha(output_dir/name) for name in ("request-model-input-map.jsonl","baseline-model-inputs.jsonl")}})
    manifest["outputs_sha256"].update(manifest["dev_baseline_outputs_sha256"])
    manifest["output_sha256"]=dict(manifest["outputs_sha256"])
    output_dir.joinpath("manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    result=validate_dev_subset(output_dir,manifest)
    return {"schema_version":"sku_dev_baseline_input_preparation_v1",**result,
            "source_subset_manifest_sha256":sha(subset_dir/"manifest.json"),
            "source_subset_output_sha256":{name:sha(subset_dir/name) for name in raw_manifest.get("outputs_sha256",{})},
            "prepared_manifest_sha256":sha(output_dir/"manifest.json"),
            "source_full_dev_manifest_sha256":sha(source_manifest_path),
            "source_full_dev_model_map_sha256":sha(source_dir/"request-model-input-map.jsonl"),
            "source_full_dev_model_inputs_sha256":sha(source_dir/"baseline-model-inputs.jsonl"),
            "prepared_output_sha256":{n:sha(output_dir/n) for n in ("requests.jsonl","documents.jsonl","tasks.jsonl","request-model-input-map.jsonl","baseline-model-inputs.jsonl","membership.jsonl","case-plans.jsonl","selection-plan.json","row-plans.jsonl","manifest.json")},
            "unique_request_count":len(requests),"unique_model_input_count":len(model_inputs),
            "selected_task_count":len(read(output_dir/"tasks.jsonl")),"target_selected_value_in_model_input":False}


def validate_dev_subset(input_dir, manifest=None):
    """Fail closed unless this exact request set is a subset of frozen DEV cases."""
    input_dir = Path(input_dir)
    manifest = manifest or json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("labels_read") is not False or manifest.get("test_split_read") is not False or
            manifest.get("model_inference_run") is not False or manifest.get("gate_evaluation_run") is not False):
        raise ValueError("DEV baseline requires explicit label/test-read/inference-free source metadata")
    if manifest.get("schema_version") != "fresh-dev-v7-whole-sku-gate-prepare-v1":
        raise ValueError("unexpected DEV adapter input schema")
    source_dir = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
    source_manifest_path = source_dir / "manifest.json"
    if sha(source_manifest_path) != manifest.get("full_dev_source_manifest_sha256"):
        raise ValueError("full DEV source manifest pin differs")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (source_manifest.get("split") != "dev" or source_manifest.get("independent_test") is not False or
            source_manifest.get("labels_read") is not False):
        raise ValueError("pinned full-population source is not explicitly label-free DEV")
    selection = json.loads((input_dir / "selection-plan.json").read_text(encoding="utf-8"))
    if (selection.get("source_split") != "dev" or selection.get("labels_read") is not False or
            selection.get("test_split_read") is not False or selection.get("predictions_read") is not False or
            selection.get("model_inference_run") is not False or selection.get("gate_output_created") is not False or
            selection.get("selection_uses_condition_text_or_availability") is not False or
            selection.get("selection_uses_v7_status_or_predictions") is not False):
        raise ValueError("selection plan is not a frozen label-blind DEV membership selection")
    if (selection.get("source_full_dev_manifest_sha256") != manifest.get("full_dev_source_manifest_sha256") or
            selection.get("source_full_dev_case_plans_sha256") != manifest.get("full_dev_source_file_sha256", {}).get("case-plans.jsonl") or
            selection.get("membership_sha256") != manifest.get("membership_sha256")):
        raise ValueError("selection plan source/membership pins differ")
    for name, expected in manifest.get("full_dev_source_file_sha256", {}).items():
        if sha(source_dir / name) != expected:
            raise ValueError("full DEV source file pin differs: " + name)
    for name, expected in manifest.get("outputs_sha256", {}).items():
        if name in SOURCE_FILES and sha(input_dir / name) != expected:
            raise ValueError("DEV subset output pin differs: " + name)
    membership = read(input_dir / "membership.jsonl")
    selected_plans = read(input_dir / "case-plans.jsonl")
    tasks = read(input_dir / "tasks.jsonl")
    requests = read(input_dir / "requests.jsonl")
    documents = read(input_dir / "documents.jsonl")
    full_cases = read(source_dir / "case-plans.jsonl")
    full_plan_by_id = {x["case_id"]:x for x in full_cases if x.get("split") == "dev"}
    full_dev_ids = set(full_plan_by_id)
    if len(full_dev_ids) != len(full_cases):
        raise ValueError("full source case plan contains non-DEV membership")
    source_case_sha = {}
    for version, path in (("v2",".lab-output/sku-independent-real-20261011/eval-v2/run-token/inputs/cases.jsonl"),
                          ("v3",".lab-output/sku-independent-real-20261011/eval-v3/run-token/inputs/cases.jsonl")):
        expected=selection.get("source_case_input_sha256_by_version",{}).get(version)
        actual=sha(ROOT/path)
        if not expected or actual != expected:
            raise ValueError("selected source case input pin differs: " + version)
        source_case_sha[version]=actual
    member_ids = {x.get("case_id") for x in membership}
    selected_ids = {x.get("case_id") for x in selected_plans}
    if (None in member_ids or len(member_ids) != len(membership) or member_ids != selected_ids or
            not member_ids or not member_ids.issubset(full_dev_ids)):
        raise ValueError("subset membership is not an exact subset of frozen DEV case IDs")
    for item in membership:
        if item.get("source_case_input_sha256") != source_case_sha.get(item.get("source_version")):
            raise ValueError("membership source case input pin differs")
        old, new = full_plan_by_id[item["case_id"]], next(p for p in selected_plans if p["case_id"] == item["case_id"])
        for field in ("dossier_id","au_product_id","source_sku_key","row_keys","total_au_rows","raw_selected_sku_source_sha256"):
            if old.get(field) != new.get(field):
                raise ValueError("selected case plan differs from frozen DEV source: " + field)
    task_ids = {x.get("case_id") for x in tasks}
    if not task_ids.issubset(member_ids) or task_ids != member_ids:
        raise ValueError("full-gate tasks do not bind exactly the selected DEV cases")
    counts = {"selected_case_count": len(member_ids), "selected_row_count": sum(len(x.get("row_keys", [])) for x in selected_plans),
              "selected_task_count": len(tasks), "selected_request_count": len(requests),
              "selected_document_count": len(documents)}
    for field, actual in counts.items():
        if manifest.get(field) != actual:
            raise ValueError("DEV subset manifest count differs: " + field)
    if counts["selected_case_count"] != 40 or counts["selected_row_count"] != 504:
        raise ValueError("unexpected frozen DEV selection size")
    return {"split": "dev", "independent_test": False, "labels_read": False,
            "membership_sha256": manifest.get("membership_sha256"), **counts}


def load_adapter_v2_inputs(input_dir):
    """Return the legacy task's in-memory contract after strict v2 validation."""
    input_dir = Path(input_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    validate_dev_subset(input_dir, manifest)
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
    freeze = {"schema_version": "fresh-fixed-jev-nli-dev-subset-v1", "labels_read": False,
              "production_adoption_allowed": False, "independent_test": False,
              "split": "dev", "development_only": True,
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
    result = {"schema_version": "fresh-fixed-jev-nli-dev-subset-v1",
              "labels_read": False, "independent_test": False, "split": "dev", "development_only": True,
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


def verify_local_models(jev_model_dir, nli_model_dir):
    """Verify the exact frozen FP32 JEV/NLI files before producing the run freeze."""
    jev_model_dir, nli_model_dir = Path(jev_model_dir).resolve(), Path(nli_model_dir).resolve()
    jev_pin = JEV.verify_model_files(jev_model_dir)
    nli_files = {}
    for name, expected in NLI.NLI_FILE_SHA256.items():
        path = nli_model_dir / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("pinned NLI file SHA differs: " + name)
        nli_files[name] = sha(path)
    card = "".join((nli_model_dir / "README.md").read_text(encoding="utf-8").split())
    if '{0:"entailment",1:"neutral",2:"contradiction}' not in card:
        raise ValueError("pinned NLI model card label order differs")
    config = json.loads((nli_model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("id2label") != {"0":"LABEL_0","1":"LABEL_1","2":"LABEL_2"}:
        raise ValueError("pinned NLI model config labels differ")
    return {"device":"cpu","precision":"fp32","threads":4,"batch_size":16,
            "jev":{"model_id":JEV.MODEL_ID,"revision":JEV.MODEL_REVISION,
                   "parameter_count":JEV.FP32_PARAMETER_COUNT,"file_sha256":jev_pin["file_sha256"],
                   "provenance_sha256":sha(jev_model_dir/"provenance.json"),
                   "model_dir":str(jev_model_dir)},
            "nli":{"model_id":NLI.MODEL_ID,"revision":NLI.MODEL_REVISION,
                   "parameter_count":NLI.FP32_PARAMETER_COUNT,"file_sha256":nli_files,
                   "provenance_manifest_sha256":NLI._PINNED.NLI_PROVENANCE_MANIFEST_SHA256,
                   "model_dir":str(nli_model_dir)}}


_RUN_BASELINE = run
_BASE_FREEZE_INPUTS = freeze_inputs

def run(input_dir, output_dir, jev_model_dir=Path(".deps/sku-fixed-models/jev"),
        nli_model_dir=Path(".deps/sku-fixed-models/nli"), *, jev_chunk_requests=16,
        nli_chunk_pairs=256, device="cpu"):
    output_dir=Path(output_dir)
    output_root=output_dir.parent
    prepared=output_root/"input-preparation"/"prepared-inputs"
    prep=prepare_dev_baseline_inputs(input_dir,prepared)
    prep_path=output_root/"input-preparation"/"preparation-receipt.json"
    prep_path.write_text(json.dumps(prep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if device != "cpu":
        raise ValueError("this DEV baseline wrapper permits CPU only")
    model_pins=verify_local_models(jev_model_dir,nli_model_dir)
    pins_path=output_root/"input-preparation"/"model-pins.json"
    pins_path.write_text(json.dumps(model_pins,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    def pinned_freeze(input_dir,output_dir,*,device,jev_chunk_requests,nli_chunk_pairs,jev_model_dir,nli_model_dir):
        freeze=_BASE_FREEZE_INPUTS(input_dir,output_dir,device=device,
                                   jev_chunk_requests=jev_chunk_requests,nli_chunk_pairs=nli_chunk_pairs,
                                   jev_model_dir=jev_model_dir,nli_model_dir=nli_model_dir)
        freeze.update({"split":"dev","development_only":True,"independent_test":False,
                       "model_pin_manifest_sha256":sha(pins_path),
                       "jev_file_sha256":model_pins["jev"]["file_sha256"],
                       "nli_file_sha256":model_pins["nli"]["file_sha256"]})
        path=Path(output_dir)/"runtime-source-freeze.json"
        path.write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return freeze
    globals()["freeze_inputs"]=pinned_freeze
    try:
        summary=_RUN_BASELINE(prepared,output_dir,jev_model_dir,nli_model_dir,
                              jev_chunk_requests=jev_chunk_requests,nli_chunk_pairs=nli_chunk_pairs,device=device)
        validate_dev_subset(prepared)
        if sha(Path(input_dir)/"manifest.json") != prep["source_subset_manifest_sha256"]:
            raise ValueError("original DEV subset manifest changed during inference")
        for name,expected in prep["source_subset_output_sha256"].items():
            if sha(Path(input_dir)/name) != expected:
                raise ValueError("original DEV subset source changed during inference: " + name)
        if verify_local_models(jev_model_dir,nli_model_dir) != model_pins:
            raise ValueError("pinned CPU model files changed during inference")
        return summary
    finally:
        globals()["freeze_inputs"]=_BASE_FREEZE_INPUTS


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
