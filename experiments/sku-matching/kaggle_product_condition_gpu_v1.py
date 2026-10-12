#!/usr/bin/env python3
"""Retest the frozen product-condition composition with FP32 CUDA arithmetic.

JEV/NLI use the CPU runner's validation, exact input keys and result assembly.
Only their model arithmetic moves to CUDA; the pinned INT8 ONNX reranker stays
on CPU. No labels, new models, truncation, quantization or CPU fallback are used.
The optional fresh baseline is the existing title/selected-row JEV/NLI fusion.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import shutil
import time
from types import FunctionType, SimpleNamespace

import trial_product_condition_models_v1 as TASK
import kaggle_full_condition_choices_v1 as WEIGHTS

JEV, NLI, FULL, V1 = TASK.JEV, TASK.NLI, TASK.FULL, TASK.V1
HERE = Path(__file__).resolve().parent
VERSION = "product-condition-gpu-v1/device-only-fp32"
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, "kaggle_full_condition_choices_v1.py",
                                 "download_candidate.py", *TASK.CODE_NAMES)))
SOURCE_NAMES = ("manifest.json", *V1.SOURCE_NAMES)


def clone_function(function, **overrides):
    """Run unchanged bytecode with local dependency overrides, never patch modules."""
    clone = FunctionType(function.__code__, {**function.__globals__, **overrides},
                         function.__name__, function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = function.__kwdefaults__
    return clone


class CudaEnvironment:
    """Keep the caller's CUDA visibility while retaining the CPU offline policy."""
    def __setitem__(self, name, value):
        if name != "CUDA_VISIBLE_DEVICES":
            os.environ[name] = value

    def update(self, *args, **kwargs):
        for name, value in dict(*args, **kwargs).items():
            self[name] = value


CUDA_OS = SimpleNamespace(environ=CudaEnvironment())


def development_loader():
    """Widen only the old error-subset guard to honest mixed development inputs.

All source SHA, request/document/task, whole-option, baseline, retrieval and
quote checks remain the existing implementation. Independent-test inputs still
fail. A source edit makes this narrow, explicit adaptation fail closed.
"""
    source = inspect.getsource(V1.load_inputs)
    old = 'manifest.get("errors_only") is not True'
    if source.count(old) != 1:
        raise RuntimeError("the frozen input guard changed; review the GPU adaptation")
    source = source.replace(old, 'manifest.get("errors_only") not in (True, False)', 1)
    namespace = dict(V1.load_inputs.__globals__)
    exec(compile(source, str(HERE / Path(__file__).name), "exec"), namespace)
    return namespace["load_inputs"]


def cuda_jev_pins():
    return {**JEV._pins(), "device": "cuda"}


def same_cuda_jev_pins(pins):
    return isinstance(pins, dict) and all(pins.get(k) == v for k, v in cuda_jev_pins().items())


class CudaScorer(WEIGHTS.CudaScorer):
    """One verified model load per stage, synchronized timing, no CPU fallback."""
    def __init__(self, jev_model_dir, nli_model_dir):
        super().__init__(jev_model_dir, nli_model_dir)
        self.runtime["attention_implementation"] = {"jev": "eager", "nli": "model default, as CPU"}
        self.runtime["synchronized_inference_timing"] = True
        self.runtime["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
        self.stage = None
        self.on_ready = None
        self.telemetry = {kind: {"api_call_count": 0, "model_load_count": 0,
                                "forward_call_count": 0, "forwarded_pair_count": 0,
                                "inferred_unique_input_count": 0, "inference_seconds": 0.0,
                                "file_verification_seconds": 0.0, "model_load_seconds": 0.0,
                                "tokenization_seconds": 0.0} for kind in ("jev", "nli")}

    def memory(self):
        cuda = self.torch.cuda
        return {"allocated_bytes": cuda.memory_allocated(), "reserved_bytes": cuda.memory_reserved(),
                "peak_allocated_bytes": cuda.max_memory_allocated(),
                "peak_reserved_bytes": cuda.max_memory_reserved()}

    def load(self, kind):
        self.stage = kind
        if kind in self.loaded:
            tokenizer, model, pins = self.loaded[kind]
            runtime = {"file_verification_seconds": 0.0, "model_load_seconds": 0.0}
        else:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            module = JEV if kind == "jev" else NLI
            started = time.perf_counter()
            pins = JEV.verify_model_files(self.roots[kind]) if kind == "jev" else WEIGHTS.verify_nli_files(self.roots[kind])
            runtime = {"file_verification_seconds": time.perf_counter() - started}
            started = time.perf_counter()
            # JEV explicitly uses eager attention on CPU. NLI keeps the original
            # Transformers default rather than selecting a different kernel.
            options = {"local_files_only": True, "trust_remote_code": False,
                       "revision": module.MODEL_REVISION}
            tokenizer = AutoTokenizer.from_pretrained(str(self.roots[kind]), **options)
            model_options = {**options, "use_safetensors": True, "torch_dtype": self.torch.float32}
            if kind == "jev":
                model_options["attn_implementation"] = "eager"
            model = AutoModelForSequenceClassification.from_pretrained(
                str(self.roots[kind]), **model_options).to("cuda").eval()
            parameters = list(model.parameters())
            if model.config.num_labels != (1 if kind == "jev" else 3):
                raise RuntimeError("pinned model classification count differs: " + kind)
            if sum(p.numel() for p in parameters) != module.FP32_PARAMETER_COUNT:
                raise RuntimeError("pinned FP32 parameter count differs: " + kind)
            if not parameters or any(p.device.type != "cuda" or p.dtype != self.torch.float32 for p in parameters):
                raise RuntimeError("every model parameter must be FP32 on CUDA; no CPU fallback")
            self.torch.cuda.synchronize()
            runtime["model_load_seconds"] = time.perf_counter() - started
            pins = {**pins, "device": "cuda", "precision": "fp32",
                    "actual_parameter_devices": sorted({str(p.device) for p in parameters}),
                    "actual_parameter_dtypes": sorted({str(p.dtype) for p in parameters}),
                    "actual_attention_implementation": getattr(model.config, "_attn_implementation", None)}
            self.loaded[kind] = tokenizer, model, pins
            self.telemetry[kind]["model_load_count"] += 1
            for name in ("file_verification_seconds", "model_load_seconds"):
                self.telemetry[kind][name] += runtime[name]
        if self.on_ready is not None:
            self.on_ready(pins, {**runtime, "precision": "fp32", "device": "cuda"})
        return tokenizer, model, pins, runtime

    def logits(self, tokenizer, model, encodings, expected_columns):
        """JEV uses the original tokenizer.pad and transfers real tensors to CUDA."""
        features = tokenizer.pad(encodings, padding=True, return_tensors="pt")
        return self.forward(model, features, len(encodings), expected_columns)

    def forward(self, model, features, row_count, expected_columns):
        self.torch.cuda.synchronize()
        with self.torch.inference_mode(), self.torch.autocast("cuda", enabled=False):
            tensors = {name: value.to("cuda") for name, value in features.items()}
            if any(value.device.type != "cuda" for value in tensors.values()):
                raise RuntimeError("model batch tensors are not entirely on CUDA")
            self.telemetry[self.stage]["forward_call_count"] += 1
            self.telemetry[self.stage]["forwarded_pair_count"] += row_count
            output = model(**tensors).logits
            if output.device.type != "cuda" or output.dtype != self.torch.float32:
                raise RuntimeError("model logits must be FP32 on CUDA")
            rows = output.detach().cpu().to(self.torch.float64).tolist()
        self.torch.cuda.synchronize()
        if len(rows) != row_count or any(not isinstance(row, list) or len(row) != expected_columns
                                         for row in rows):
            raise ValueError("GPU output row/class count differs from the frozen model")
        # Use the CPU probability helpers to check finite values and class order.
        if expected_columns == 3:
            for row in rows:
                NLI.relation_probabilities(row)
        else:
            for row in rows:
                JEV._softmax(row)
        return rows

    def _infer_jev(self, requests, model_dir, threads, batch_size, on_ready):
        self.on_ready = on_ready
        try:
            records, pins, runtime = super().infer_jev(requests)
        finally:
            self.on_ready = None
        self._record_runtime("jev", runtime, len(requests))
        return [{"cache_key": r["cache_key"], **records[r["cache_key"]]} for r in requests], pins, runtime

    def _infer_nli(self, pairs, model_dir, threads, batch_size, on_ready):
        self.on_ready = on_ready
        try:
            tokenizer, model, pins, runtime = self.load("nli")
        finally:
            self.on_ready = None
        prepared, records, long_count, errors = [], {}, 0, 0
        runtime.update(inference_seconds=0.0, tokenization_seconds=0.0)
        # Preserve the CPU loader's two tokenizer calls and its chunk failure
        # behavior: tokenizer errors escape to FULL.score_nli_chunk as unknown.
        for pair in pairs:
            started = time.perf_counter()
            encoding = tokenizer(pair["premise"], pair["hypothesis"],
                                 truncation=False, add_special_tokens=True)
            count = len(encoding["input_ids"])
            runtime["tokenization_seconds"] += time.perf_counter() - started
            if NLI.exceeds_token_limit(count):
                long_count += 1
                records[pair["cache_key"]] = {"task_id": pair["cache_key"], "probabilities": None,
                    "token_count_untruncated": count, "latency_seconds": 0.0,
                    "note": f"input exceeds {NLI.MAX_TOKENS} tokens; no truncation; unknown"}
            else:
                prepared.append((pair, count))
        for offset in range(0, len(prepared), batch_size):
            batch = prepared[offset:offset + batch_size]
            started = time.perf_counter()
            features = tokenizer([p["premise"] for p, _ in batch], [p["hypothesis"] for p, _ in batch],
                                 padding=True, truncation=False, return_tensors="pt")
            runtime["tokenization_seconds"] += time.perf_counter() - started
            self.torch.cuda.synchronize()
            started = time.perf_counter()
            try:
                rows = self.forward(model, features, len(batch), 3)
                elapsed = time.perf_counter() - started
                for (pair, count), row in zip(batch, rows, strict=True):
                    probabilities, _, _ = NLI.relation_probabilities(row)
                    records[pair["cache_key"]] = {"task_id": pair["cache_key"], "probabilities": probabilities,
                        "token_count_untruncated": count, "latency_seconds": elapsed / len(batch), "note": None}
            except Exception as error:
                errors += len(batch)
                for pair, count in batch:
                    records[pair["cache_key"]] = {"task_id": pair["cache_key"], "probabilities": None,
                        "token_count_untruncated": count, "latency_seconds": 0.0,
                        "note": f"inference error: {type(error).__name__}: {error}", "error_type": type(error).__name__}
            runtime["inference_seconds"] += time.perf_counter() - started
        runtime.update(long_input_unknown_count=long_count, inference_error_count=errors, precision="fp32")
        self._record_runtime("nli", runtime, len(pairs))
        return [records[p["cache_key"]] for p in pairs], pins, runtime

    def _record_runtime(self, kind, runtime, count):
        self.telemetry[kind]["inferred_unique_input_count"] += count
        for name in ("inference_seconds", "tokenization_seconds"):
            self.telemetry[kind][name] += runtime.get(name, 0.0)
        self.telemetry[kind]["memory"] = self.memory()

    def _call(self, kind, function, inputs, model_dir, threads, batch_size, cache, on_ready):
        if (threads, batch_size) != (4, 16):
            raise ValueError("frozen GPU settings are four host threads and batch size 16")
        if Path(model_dir).resolve() != self.roots[kind].resolve():
            raise ValueError("GPU scorer model directory differs from its verified load")
        for previous in (cache or {}).values():
            if previous.get("device") != "cuda":
                raise ValueError("GPU caches must come from this CUDA inference, not CPU output")
        self.telemetry[kind]["api_call_count"] += 1
        result = function(inputs, model_dir=model_dir, threads=threads, batch_size=batch_size,
                          cache=cache, on_ready=on_ready)
        result["runtime"].update(device="cuda", precision="fp32")
        result["pins"]["device"] = "cuda"
        for record in result["records"]:
            record["device"] = "cuda"
        return result

    def run_choices(self, requests, model_dir=None, threads=4, batch_size=16, cache=None, on_ready=None):
        function = clone_function(JEV.run_choices, os=CUDA_OS, _infer=self._infer_jev,
                                  _pins=cuda_jev_pins, _same_pins=same_cuda_jev_pins)
        return self._call("jev", function, requests, model_dir or self.roots["jev"],
                          threads, batch_size, cache, on_ready)

    def run_pairs(self, pairs, model_dir=None, threads=4, batch_size=16, cache=None, on_ready=None):
        function = clone_function(NLI.run_pairs, os=CUDA_OS, _infer=self._infer_nli)
        ready = []

        def verified(pins, runtime):
            ready.append((pins, runtime))
            if on_ready is not None:
                on_ready(pins, runtime)

        try:
            return self._call("nli", function, pairs, model_dir or self.roots["nli"],
                              threads, batch_size, cache, verified)
        except Exception as error:
            if not ready:
                raise
            # FULL.score_nli_chunk's exact verified-chunk failure policy, with
            # explicit CUDA provenance so the final cache fan-out can reuse it.
            records = [{**p, "cache_key": NLI.pair_key(p["premise"], p["hypothesis"]),
                        "model_id": NLI.MODEL_ID, "model_revision": NLI.MODEL_REVISION,
                        "precision": "fp32", "device": "cuda", "probabilities": None,
                        "relation": "unknown", "token_count_untruncated": None,
                        "error_type": type(error).__name__,
                        "note": "verified model chunk failed: " + str(error)} for p in pairs]
            return {"records": records, "pins": ready[0][0], "runtime": {
                **ready[0][1], "device": "cuda", "inference_error_count": len(records)}}


def metadata_writer(scorer, *, errors_only, baseline_mode):
    def write(path, payload):
        if Path(path).name in {"freeze.json", "summary.json"}:
            # Retain the original CPU-semantic metadata for comparison. Its CPU
            # device tag describes the source implementation, not this execution.
            if Path(path).name == "freeze.json":
                TASK.write_json(Path(path).with_name("cpu-semantic-freeze.json"), payload)
            payload = {**payload, "schema_version": VERSION, "cpu_semantic_schema_version": payload["schema_version"],
                       "errors_only": errors_only, "source_errors_only": errors_only,
                       "device": "mixed-cpu-cuda", "gpu_used": True,
                       "stage_devices": {"reranker": "cpu", "jev": "cuda", "nli": "cuda"},
                       "stage_precision": {"reranker": "int8", "jev": "fp32", "nli": "fp32"},
                       "gpu_runtime": deepcopy(scorer.runtime), "baseline_mode": baseline_mode,
                       "model_choice_changed": False, "cpu_numeric_equality_assumed": False}
            if Path(path).name == "summary.json":
                payload["gpu_stage_telemetry"] = deepcopy(scorer.telemetry)
                for kind in ("jev", "nli"):
                    if kind + "_runtime" in payload:
                        payload[kind + "_runtime"] = {**payload[kind + "_runtime"], "device": "cuda", "precision": "fp32"}
                if "reranker_runtime" in payload:
                    payload["reranker_runtime"] = {**payload["reranker_runtime"], "device": "cpu", "precision": "int8",
                        "execution_provider": "CPUExecutionProvider"}
        TASK.write_json(path, payload)
    return write


def freeze_sources(input_dir, contract, destination):
    destination.mkdir(parents=True, exist_ok=False)
    code = destination / "code"
    code.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code / name)
    (code / "manifests").mkdir()
    shutil.copyfile(HERE / "manifests/reranker.json", code / "manifests/reranker.json")
    freeze = {"schema_version": VERSION, "labels_read": False, "labels_in_model_inputs": False,
              "input_sha256": {name: TASK.sha(input_dir / name) for name in SOURCE_NAMES},
              "eligible_requests_sha256": TASK.sha(contract),
              "code_sha256": {name: TASK.sha(code / name) for name in CODE_NAMES},
              "reranker_manifest_sha256": TASK.sha(code / "manifests/reranker.json")}
    TASK.write_json(destination / "gpu-source-freeze.json", freeze)
    return freeze


def verify_sources(input_dir, contract, freeze):
    for name, expected in freeze["input_sha256"].items():
        if TASK.sha(input_dir / name) != expected:
            raise ValueError("source input changed during GPU execution: " + name)
    if TASK.sha(contract) != freeze["eligible_requests_sha256"]:
        raise ValueError("eligibility contract changed during GPU execution")
    for name, expected in freeze["code_sha256"].items():
        if TASK.sha(HERE / name) != expected:
            raise ValueError("source code changed during GPU execution: " + name)
    if TASK.sha(HERE / "manifests/reranker.json") != freeze["reranker_manifest_sha256"]:
        raise ValueError("reranker manifest changed during GPU execution")


def verify_predictions(prepared_dir, product_dir):
    """Recompute fan-out from saved group results and check complete raw bindings."""
    requests, documents, _, baseline, _, _, _ = development_loader()(prepared_dir)
    plan = json.loads((product_dir / "sharing-plan.json").read_text(encoding="utf-8"))
    for variant in TASK.VARIANTS:
        rows = TASK.read(product_dir / (variant + "-predictions.jsonl"))
        groups = {r["group_id"]: r for r in TASK.read(product_dir / (variant + "-group-results.jsonl"))}
        expected = TASK.SHARING.fan_out(plan, groups, baseline)
        if rows != expected or [p["request_id"] for p in rows] != [r["request_id"] for r in requests]:
            raise ValueError("saved product predictions differ from frozen fan-out: " + variant)
        for request, prediction in zip(requests, rows, strict=True):
            original = baseline[request["request_id"]]
            if (prediction.get("source_request") != request
                    or prediction.get("source_task_bindings") != original["source_task_bindings"]):
                raise ValueError("prediction lost complete source binding")
            if (request["request_id"] in plan["request_to_group"] and prediction.get("state") == "support"
                    and (not prediction.get("quote_refs") or any(
                    not V1.verified_ref(ref, request, documents) for ref in prediction["quote_refs"]))):
                raise ValueError("support prediction lost its verified literal source reference")
    return True


def refresh_baseline(input_dir, eligible_requests, destination, scorer, *, baseline_variant="fusion"):
    """Create a traceable derived prep, retaining the received source unmodified."""
    destination.mkdir(parents=True, exist_ok=False)
    baseline_output = destination / "baseline-model"
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    run = clone_function(FULL.run, CODE_NAMES=CODE_NAMES,
        write_json=metadata_writer(scorer, errors_only=manifest["errors_only"], baseline_mode="fresh-gpu-baseline"))
    run(input_dir, baseline_output, jev_model_dir=scorer.roots["jev"], nli_model_dir=scorer.roots["nli"],
        jev_runner=scorer.run_choices, nli_runner=scorer.run_pairs)
    summary = json.loads((baseline_output / "summary.json").read_text(encoding="utf-8"))
    names = {"fusion": "fusion-predictions.jsonl", "jev": "jev-predictions.jsonl", "nli": "nli-predictions.jsonl"}
    selected = FULL.read(baseline_output / names[baseline_variant])
    originals = {p["request_id"]: p for p in FULL.read(input_dir / "baseline-predictions.jsonl")}
    for prediction in selected:
        original = originals[prediction["request_id"]]
        if prediction["source_request"] != original["source_request"]:
            raise ValueError("fresh baseline lost its complete source request")
        prediction["source_task_bindings"] = deepcopy(original["source_task_bindings"])
    prepared = destination / "prepared"
    prepared.mkdir()
    for name in V1.SOURCE_NAMES:
        if name != "baseline-predictions.jsonl":
            shutil.copyfile(input_dir / name, prepared / name)
    FULL.write_rows(prepared / "baseline-predictions.jsonl", selected)
    manifest = {**manifest, "baseline_predictions_are_placeholder": False, "baseline_model_inference_required": False,
                "baseline_predictions_are_unevaluated": False, "baseline_must_be_regenerated_for_all_requests": False,
                "baseline_refresh": {"variant": baseline_variant, "device": "cuda", "precision": "fp32",
                    "source_prepared_manifest_sha256": TASK.sha(input_dir / "manifest.json"),
                    "received_baseline_sha256": TASK.sha(input_dir / "baseline-predictions.jsonl"),
                    "baseline_model_summary_sha256": TASK.sha(baseline_output / "summary.json")}}
    manifest["output_sha256"] = {**manifest.get("output_sha256", {}),
        **{name: TASK.sha(prepared / name) for name in V1.SOURCE_NAMES}}
    TASK.write_json(prepared / "manifest.json", manifest)
    contract = json.loads(eligible_requests.read_text(encoding="utf-8"))
    contract["received_contract_sha256"] = TASK.sha(eligible_requests)
    contract["baseline_refresh"] = manifest["baseline_refresh"]
    contract["source_sha256"]["prepared"] = {name: TASK.sha(prepared / name)
        for name in contract["source_sha256"]["prepared"]}
    refreshed_contract = destination / "eligible-requests.json"
    TASK.write_json(refreshed_contract, contract)
    return prepared, refreshed_contract, summary


def run(input_dir, eligible_requests, output_dir, reranker_model_dir=V1.DEFAULT_RERANKER,
        jev_model_dir=JEV.DEFAULT_MODEL, nli_model_dir=NLI.DEFAULT_MODEL, *, refresh=False,
        baseline_variant="fusion", bundle_dir=None, scorer=None, reranker_factory=None):
    input_dir, eligible_requests, output_dir = Path(input_dir), Path(eligible_requests), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if baseline_variant not in {"fusion", "jev", "nli"}:
        raise ValueError("unsupported frozen baseline variant")
    started = time.perf_counter()
    bundle = WEIGHTS.verify_bundle(bundle_dir) if bundle_dir else None
    loader = development_loader()
    loader(input_dir)
    TASK.verify_contract_sources(json.loads(eligible_requests.read_text(encoding="utf-8")), input_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    placeholders = (manifest.get("baseline_predictions_are_placeholder") or manifest.get("baseline_model_inference_required")
                    or manifest.get("baseline_predictions_are_unevaluated")
                    or manifest.get("baseline_must_be_regenerated_for_all_requests"))
    if placeholders and not refresh:
        raise ValueError("received baseline is a placeholder; --refresh-baseline is required")
    scorer = scorer or CudaScorer(jev_model_dir, nli_model_dir)
    received_dir, received_contract = input_dir, eligible_requests
    freeze = freeze_sources(input_dir, eligible_requests, output_dir)
    TASK.write_json(output_dir / "execution-freeze.json", {**freeze, "gpu_runtime": scorer.runtime,
        "refresh_baseline": refresh, "baseline_variant": baseline_variant, "bundle": bundle,
        "stage_devices": {"reranker": "cpu", "jev": "cuda", "nli": "cuda"},
        "stage_precision": {"reranker": "int8", "jev": "fp32", "nli": "fp32"}})
    baseline_summary = None
    if refresh:
        input_dir, eligible_requests, baseline_summary = refresh_baseline(
            input_dir, eligible_requests, output_dir / "baseline-refresh", scorer, baseline_variant=baseline_variant)
    local_v1 = SimpleNamespace(**{**V1.__dict__, "load_inputs": loader})
    product = clone_function(TASK.run, os=CUDA_OS, V1=local_v1, CODE_NAMES=CODE_NAMES,
        write_json=metadata_writer(scorer, errors_only=manifest["errors_only"],
            baseline_mode="fresh-gpu-" + baseline_variant if refresh else "received-baseline-parity-replay"))
    product(input_dir, eligible_requests, output_dir / "product-model",
        reranker_model_dir=reranker_model_dir, jev_model_dir=scorer.roots["jev"], nli_model_dir=scorer.roots["nli"],
        reranker_factory=reranker_factory, jev_runner=scorer.run_choices, nli_runner=scorer.run_pairs)
    summary = json.loads((output_dir / "product-model/summary.json").read_text(encoding="utf-8"))
    verify_predictions(input_dir, output_dir / "product-model")
    verify_sources(received_dir, received_contract, freeze)
    if bundle_dir and WEIGHTS.verify_bundle(bundle_dir)["manifest_sha256"] != bundle["manifest_sha256"]:
        raise ValueError("received bundle changed during GPU execution")
    summary = {**summary, "total_gpu_job_seconds": time.perf_counter() - started,
        "received_source_freeze": freeze, "received_source_and_code_verified_after_inference": True,
        "saved_prediction_fanout_and_literal_references_verified": True,
        "baseline_refresh_summary": baseline_summary, "gpu_stage_telemetry": deepcopy(scorer.telemetry)}
    # Convenience copies remain byte-identical to the product runner's outputs.
    for name in [*(v + "-predictions.jsonl" for v in TASK.VARIANTS), "product-groups.jsonl",
                 "model-requests.jsonl", "model-nli-pairs.jsonl", "sharing-plan.json", "sharing-contract.json"]:
        shutil.copyfile(output_dir / "product-model" / name, output_dir / name)
    summary["output_sha256"] = {str(p.relative_to(output_dir)): TASK.sha(p)
        for p in output_dir.rglob("*") if p.is_file() and p != output_dir / "summary.json"}
    TASK.write_json(output_dir / "summary.json", summary)
    return summary


def acquire_models(weights_dir, jev_provenance=None):
    """Reuse the existing pinned HF download functions; never install runtimes."""
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    if jev_provenance is None:
        jev_provenance = weights_dir / "jev-provenance.json"
        provenance = {"schema_version": "generic_model_jev_provenance_v1", "model_id": JEV.MODEL_ID,
            "revision": JEV.MODEL_REVISION, "format_version": JEV.FORMAT_VERSION,
            "files": {name: {"sha256": expected,
                "source_url": f"https://huggingface.co/{JEV.MODEL_ID}/resolve/{JEV.MODEL_REVISION}/{name}"}
                for name, expected in JEV.JEV_FILE_SHA256.items()}}
        if jev_provenance.exists() and json.loads(jev_provenance.read_text(encoding="utf-8")) != provenance:
            raise RuntimeError("existing JEV acquisition provenance differs from pinned identity")
        if not jev_provenance.exists():
            TASK.write_json(jev_provenance, provenance)
    # The existing acquisition implementation explicitly revision-pins every
    # hf_hub_download and checks its weights/config/tokenizer SHA before copying.
    roots = WEIGHTS.acquire_weights(weights_dir, Path(jev_provenance), download=True)
    from download_candidate import download_candidate
    roots["reranker"] = download_candidate("reranker", weights_dir / "reranker")
    return roots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--eligible-requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--reranker-model-dir", type=Path, default=V1.DEFAULT_RERANKER)
    parser.add_argument("--jev-model-dir", type=Path, default=JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--weights-dir", type=Path, default=Path("/kaggle/working/product-condition-weights"))
    parser.add_argument("--jev-provenance", type=Path)
    parser.add_argument("--refresh-baseline", dest="refresh", action="store_true")
    parser.add_argument("--baseline-variant", choices=("fusion", "jev", "nli"), default="fusion")
    args = vars(parser.parse_args())
    download = args.pop("download_models")
    weights_dir, provenance = args.pop("weights_dir"), args.pop("jev_provenance")
    if download:
        roots = acquire_models(weights_dir, provenance)
        args.update({kind + "_model_dir": path for kind, path in roots.items()})
    summary = run(**args)
    print(FULL.canonical({"event": "complete", "request_count": summary["request_count"],
                          "prediction_states": summary["prediction_states"]}), flush=True)


if __name__ == "__main__":
    main()
