#!/usr/bin/env python3
"""Run the frozen mixed SKU model replay on CPU after a CUDA submission failure.

The original requests, documents, tasks, blocks, retrieval inputs and sharing
contract are pinned. A fresh CPU baseline is generated for every request before
the product-condition variants run. This driver never reads labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
from types import FunctionType, SimpleNamespace

import trial_product_condition_models_v1 as PRODUCT
import trial_full_scoped_condition_choices_v1 as FULL
import trial_scoped_html_evidence_v1 as V1

BASELINE_FILES = {"fusion": "fusion-predictions.jsonl", "jev": "jev-predictions.jsonl",
                  "nli": "nli-predictions.jsonl"}
SOURCE_NAMES = ("manifest.json", *V1.SOURCE_NAMES)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path: Path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_rows(path: Path, values):
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def load_manifest(input_dir: Path, eligible_requests: Path):
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("labels_read") is not False or manifest.get("labels_in_model_inputs") is not False
            or manifest.get("independent_test") is not False or manifest.get("synthetic") is not False
            or manifest.get("development_only") is not True or manifest.get("production_adoption_allowed") is not False
            or manifest.get("errors_only") is not False):
        raise ValueError("CPU mixed replay requires the same label-free mixed development input")
    for name in V1.SOURCE_NAMES:
        expected = manifest.get("output_sha256", {}).get(name)
        if not expected or sha(input_dir / name) != expected:
            raise ValueError("received mixed input hash differs: " + name)
    contract = json.loads(Path(eligible_requests).read_text(encoding="utf-8"))
    PRODUCT.verify_contract_sources(contract, input_dir)
    return manifest, contract


def widened_loader():
    """Allow errors_only=false while retaining every other CPU loader check."""
    import inspect
    source = inspect.getsource(V1.load_inputs)
    old = 'manifest.get("errors_only") is not True'
    if source.count(old) != 1:
        raise RuntimeError("frozen product input guard changed; review CPU mixed adaptation")
    source = source.replace(old, 'manifest.get("errors_only") not in (True, False)', 1)
    namespace = dict(V1.load_inputs.__globals__)
    exec(compile(source, str(Path(__file__).resolve()), "exec"), namespace)
    return namespace["load_inputs"]


def run_with_loader(function, loader):
    """Call an unchanged runner with only its narrow input loader adapted."""
    v1 = SimpleNamespace(**{**V1.__dict__, "load_inputs": loader})
    clone = FunctionType(function.__code__, {**function.__globals__, "V1": v1},
                         function.__name__, function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = function.__kwdefaults__
    return clone


def assemble_refreshed_inputs(input_dir: Path, eligible_requests: Path, destination: Path,
                              predictions: list[dict], baseline_summary: dict,
                              baseline_variant="fusion"):
    """Bind fresh-baseline predictions to every original request and contract."""
    if baseline_variant not in BASELINE_FILES:
        raise ValueError("unsupported CPU baseline variant")
    input_dir, destination = Path(input_dir), Path(destination)
    received_manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    original = {r["request_id"]: r for r in rows(input_dir / "baseline-predictions.jsonl")}
    selected = {}
    for prediction in predictions:
        identity = prediction.get("request_id")
        if identity not in original or identity in selected:
            raise ValueError("CPU baseline has duplicate or out-of-scope request")
        if prediction.get("source_request") != original[identity].get("source_request"):
            raise ValueError("CPU fresh baseline lost its exact source request")
        bindings = original[identity].get("source_task_bindings")
        if not isinstance(bindings, list) or prediction.get("source_task_bindings") not in (None, bindings):
            raise ValueError("CPU baseline source-task bindings differ")
        selected[identity] = {**prediction, "source_task_bindings": bindings}
    if set(selected) != set(original) or len(selected) != baseline_summary.get("request_count"):
        raise ValueError("fresh CPU baseline does not cover every original request")
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    for name in V1.SOURCE_NAMES:
        if name != "baseline-predictions.jsonl":
            shutil.copyfile(input_dir / name, destination / name)
    write_rows(destination / "baseline-predictions.jsonl", selected.values())
    baseline_refresh = {"variant": baseline_variant, "device": "cpu", "precision": "fp32",
        "source_prepared_manifest_sha256": sha(input_dir / "manifest.json"),
        "received_baseline_sha256": sha(input_dir / "baseline-predictions.jsonl"),
        "baseline_model_summary_sha256": baseline_summary.get("summary_sha256"),
        "request_count": len(selected)}
    refreshed_manifest = {**received_manifest, "baseline_predictions_are_placeholder": False,
        "baseline_model_inference_required": False, "baseline_predictions_are_unevaluated": False,
        "baseline_must_be_regenerated_for_all_requests": False, "baseline_refresh": baseline_refresh}
    refreshed_manifest["output_sha256"] = {**received_manifest.get("output_sha256", {}),
        **{name: sha(destination / name) for name in V1.SOURCE_NAMES}}
    write_json(destination / "manifest.json", refreshed_manifest)
    contract = json.loads(Path(eligible_requests).read_text(encoding="utf-8"))
    contract["received_contract_sha256"] = sha(Path(eligible_requests))
    contract["baseline_refresh"] = baseline_refresh
    contract_names = set(contract["source_sha256"]["prepared"]) | {"baseline-predictions.jsonl"}
    contract["source_sha256"]["prepared"] = {name: sha(destination / name) for name in sorted(contract_names)}
    refreshed_contract = destination.parent / "eligible-requests.json"
    write_json(refreshed_contract, contract)
    return destination, refreshed_contract, baseline_refresh


def run(input_dir: Path, eligible_requests: Path, output_dir: Path,
        reranker_model_dir=V1.DEFAULT_RERANKER, jev_model_dir=PRODUCT.JEV.DEFAULT_MODEL,
        nli_model_dir=PRODUCT.NLI.DEFAULT_MODEL, *, baseline_variant="fusion",
        baseline_runner=None, product_runner=None, reranker_factory=None):
    input_dir, eligible_requests, output_dir = map(Path, (input_dir, eligible_requests, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if baseline_variant not in BASELINE_FILES:
        raise ValueError("unsupported CPU baseline variant")
    received_manifest, _ = load_manifest(input_dir, eligible_requests)
    loader = widened_loader()
    loader(input_dir)
    started = time.perf_counter()
    code_names = tuple(dict.fromkeys((Path(FULL.__file__).name, *PRODUCT.CODE_NAMES)))
    code_hashes = {name: sha(Path(FULL.__file__).with_name(name)) for name in code_names
                   if Path(FULL.__file__).with_name(name).is_file()}
    code_hashes["reranker.json"] = sha(V1.HERE / "manifests/reranker.json")
    driver_name = Path(__file__).name
    code_hashes[driver_name] = sha(Path(__file__).resolve())
    source_freeze = {"input_sha256": {name: sha(input_dir / name) for name in SOURCE_NAMES},
                     "eligible_contract_sha256": sha(eligible_requests), "code_sha256": code_hashes}
    output_dir.mkdir(parents=True)
    write_json(output_dir / "cpu-source-freeze.json", source_freeze)
    code_dir = output_dir / "code"
    (code_dir / "manifests").mkdir(parents=True)
    for name in code_hashes:
        source = ((V1.HERE / "manifests/reranker.json") if name == "reranker.json" else
                  Path(__file__).resolve() if name == driver_name else Path(FULL.__file__).with_name(name))
        target = code_dir / ("manifests/reranker.json" if name == "reranker.json" else name)
        shutil.copyfile(source, target)
    execution_freeze = {**source_freeze, "schema_version": "mixed-product-condition-cpu-freeze-v1",
        "refresh_baseline": True, "baseline_variant": baseline_variant,
        "stage_devices": {"reranker": "cpu", "jev": "cpu", "nli": "cpu"},
        "stage_precision": {"reranker": "int8", "jev": "fp32", "nli": "fp32"},
        "gpu_submitted": False}
    write_json(output_dir / "execution-freeze.json", execution_freeze)
    baseline_dir = output_dir / "baseline-refresh/baseline-model"
    baseline_dir.parent.mkdir(parents=True)
    cpu_full_run = run_with_loader(FULL.run, loader)
    baseline_summary = (baseline_runner or cpu_full_run)(input_dir, baseline_dir,
        jev_model_dir=jev_model_dir, nli_model_dir=nli_model_dir)
    predictions = list(rows(baseline_dir / BASELINE_FILES[baseline_variant]))
    baseline_summary["summary_sha256"] = sha(baseline_dir / "summary.json")
    refreshed, refreshed_contract, refresh_info = assemble_refreshed_inputs(
        input_dir, eligible_requests, output_dir / "baseline-refresh/prepared", predictions,
        baseline_summary, baseline_variant)
    cpu_product_run = run_with_loader(PRODUCT.run, loader)
    product_dir = output_dir / "product-model"
    product_summary = (product_runner or cpu_product_run)(refreshed, refreshed_contract, product_dir,
        reranker_model_dir=reranker_model_dir, jev_model_dir=jev_model_dir, nli_model_dir=nli_model_dir,
        reranker_factory=reranker_factory)
    for name in (*(variant + "-predictions.jsonl" for variant in PRODUCT.VARIANTS),
                 "product-groups.jsonl", "sharing-plan.json", "sharing-contract.json"):
        shutil.copyfile(product_dir / name, output_dir / name)
    for name, expected in source_freeze["input_sha256"].items():
        if sha(input_dir / name) != expected:
            raise ValueError("received CPU source changed during inference: " + name)
    if sha(eligible_requests) != source_freeze["eligible_contract_sha256"]:
        raise ValueError("received CPU sharing contract changed during inference")
    for name, expected in source_freeze["code_sha256"].items():
        path = ((V1.HERE / "manifests/reranker.json") if name == "reranker.json" else
                Path(__file__).resolve() if name == driver_name else Path(FULL.__file__).with_name(name))
        if not path.is_file() or sha(path) != expected:
            raise ValueError("CPU source code or reranker pin changed during inference: " + name)
    summary = {**product_summary, "schema_version": "product-condition-cpu-fallback-v1/device-only-fp32",
        "execution_mode": "explicit_cpu_fallback_after_gpu_submission_unknown",
        "gpu_used": False, "gpu_submitted": False,
        "stage_devices": {"reranker": "cpu", "jev": "cpu", "nli": "cpu"},
        "stage_precision": {"reranker": "int8", "jev": "fp32", "nli": "fp32"},
        "baseline_mode": "fresh-cpu-" + baseline_variant,
        "baseline_refresh_summary": refresh_info, "received_source_freeze": source_freeze,
        "received_source_and_code_verified_after_inference": True,
        "saved_prediction_fanout_and_literal_references_verified": True,
        "source_errors_only": received_manifest.get("errors_only"),
        "total_cpu_job_seconds": time.perf_counter() - started,
        "output_sha256": {str(path.relative_to(output_dir)): sha(path) for path in output_dir.rglob("*")
                          if path.is_file() and path != output_dir / "summary.json"}}
    write_json(output_dir / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--eligible-requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reranker-model-dir", type=Path, default=V1.DEFAULT_RERANKER)
    parser.add_argument("--jev-model-dir", type=Path, default=PRODUCT.JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=PRODUCT.NLI.DEFAULT_MODEL)
    parser.add_argument("--baseline-variant", choices=tuple(BASELINE_FILES), default="fusion")
    result = run(**vars(parser.parse_args(argv)))
    print(json.dumps({"request_count": result["request_count"], "baseline_mode": result["baseline_mode"],
                      "prediction_states": result["prediction_states"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
