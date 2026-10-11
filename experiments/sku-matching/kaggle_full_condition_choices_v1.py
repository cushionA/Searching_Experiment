#!/usr/bin/env python3
"""CUDA FP32 execution of the frozen whole-option task; no labels or scoring.

Only model arithmetic moves to CUDA. Source binding, task text, literal quotes,
unknown control and equal-weight fusion use the existing CPU pure helpers.
The caller submits the private Kaggle job and scores the returned predictions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import shutil
import time

import trial_full_scoped_condition_choices_v1 as TASK

JEV, NLI = TASK.JEV, TASK.NLI
HERE = Path(__file__).resolve().parent
CODE_NAMES = (Path(__file__).name, *[n for n in TASK.CODE_NAMES if n != "try_gliner.py"])


def verify_bundle(bundle_dir):
    """Verify the exact private source-only inventory, including size and SHA."""
    bundle_dir = Path(bundle_dir).resolve()
    manifest_path = bundle_dir / "BUNDLE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("labels_included") is not False:
        raise ValueError("bundle must explicitly exclude labels")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("bundle requires a nonempty exact files inventory")
    for name, entry in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("unsafe bundle file path")
        path = bundle_dir / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle_dir):
            raise ValueError("bundle inventory requires ordinary contained files")
        if not isinstance(entry, dict) or path.stat().st_size != entry.get("size_bytes") or TASK.sha(path) != entry.get("sha256"):
            raise ValueError("bundle file size/SHA256 differs: " + name)
    actual = {str(p.relative_to(bundle_dir)) for p in bundle_dir.rglob("*")
              if p.is_file() and p != manifest_path and "__pycache__" not in p.parts}
    if actual != set(files):
        raise ValueError("bundle inventory differs from actual payload")
    return {"manifest_sha256": TASK.sha(manifest_path), "file_count": len(files),
            "labels_included": False, "files": files}


def verify_nli_files(model_dir):
    verified = {}
    for name, expected in NLI.NLI_FILE_SHA256.items():
        path = model_dir / name
        if not path.is_file() or TASK.sha(path) != expected:
            raise RuntimeError("pinned NLI file SHA256 differs: " + name)
        verified[name] = {"sha256": expected, "size_bytes": path.stat().st_size,
                          "source_url": f"https://huggingface.co/{NLI.MODEL_ID}/resolve/{NLI.MODEL_REVISION}/{name}"}
    card = "".join((model_dir / "README.md").read_text(encoding="utf-8").split())
    if '{0:"entailment",1:"neutral",2:"contradiction}' not in card:
        raise RuntimeError("NLI README label order differs from the frozen mapping")
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("id2label") != {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}:
        raise RuntimeError("NLI generic config label order differs")
    for name in ("added_tokens.json", "tokenizer.json", "tokenizer.model", "spiece.model", "vocab.json", "merges.txt"):
        if (model_dir / name).exists():
            raise RuntimeError("unpinned NLI tokenizer file exists: " + name)
    return {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
            "parameter_count": NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cuda",
            "files": verified, "file_sha256": dict(NLI.NLI_FILE_SHA256),
            "legacy_provenance_manifest_sha256": NLI._PINNED.NLI_PROVENANCE_MANIFEST_SHA256,
            "legacy_provenance_manifest_present": False,
            "label_order_provenance": "Pinned README: 0 entailment, 1 neutral, 2 contradiction",
            "config_id2label": config["id2label"]}


def acquire_weights(weights_dir, jev_provenance, *, download=True):
    """Acquire exactly the pinned files; installation is the notebook's job."""
    roots = {"jev": Path(weights_dir) / "jev", "nli": Path(weights_dir) / "nli"}
    for kind, module, filenames in (("jev", JEV, JEV.JEV_FILE_SHA256), ("nli", NLI, NLI.NLI_FILE_SHA256)):
        destination = roots[kind]
        destination.mkdir(parents=True, exist_ok=True)
        for name, expected in filenames.items():
            target = destination / name
            if target.exists() and TASK.sha(target) == expected:
                continue
            if not download:
                raise RuntimeError("pinned model file missing/mismatched with download disabled: " + str(target))
            from huggingface_hub import hf_hub_download
            acquired = Path(hf_hub_download(repo_id=module.MODEL_ID, revision=module.MODEL_REVISION,
                                           filename=name))
            if TASK.sha(acquired) != expected:
                raise RuntimeError("downloaded model SHA256 differs: " + name)
            shutil.copyfile(acquired, target)
        if kind == "jev":
            shutil.copyfile(jev_provenance, destination / "provenance.json")
            JEV.verify_model_files(destination)
        else:
            verify_nli_files(destination)
    return roots


class CudaScorer:
    """Verified local models with exact caches; no CPU fallback or autocast."""

    def __init__(self, jev_model_dir, nli_model_dir):
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("This driver requires a real CUDA GPU; no CPU fallback")
        torch.set_num_threads(4)
        torch.set_float32_matmul_precision("highest")
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.torch = torch
        self.roots = {"jev": Path(jev_model_dir), "nli": Path(nli_model_dir)}
        self.loaded = {}
        device = torch.cuda.current_device()
        properties = torch.cuda.get_device_properties(device)
        packages = {}
        for name in ("torch", "transformers", "tokenizers", "huggingface_hub", "safetensors", "fugashi", "unidic_lite"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
        self.runtime = {"device": "cuda", "device_index": device, "gpu_name": torch.cuda.get_device_name(device),
                        "gpu_total_memory_bytes": properties.total_memory, "gpu_compute_capability": list(torch.cuda.get_device_capability(device)),
                        "cuda_version": torch.version.cuda, "python": platform.python_version(), "packages": packages,
                        "precision": "fp32", "autocast": False, "attention_implementation": "eager",
                        "cuda_matmul_tf32": False, "cudnn_tf32": False, "float32_matmul_precision": "highest",
                        "batch_size": 16, "threads": 4, "cpu_numeric_equality_assumed": False}

    def load(self, kind):
        if kind in self.loaded:
            tokenizer, model, pins = self.loaded[kind]
            return tokenizer, model, pins, {"file_verification_seconds": 0.0, "model_load_seconds": 0.0}
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        module = JEV if kind == "jev" else NLI
        verified_at = time.perf_counter()
        pins = JEV.verify_model_files(self.roots[kind]) if kind == "jev" else verify_nli_files(self.roots[kind])
        pins = {**pins, "device": "cuda", "precision": "fp32"}
        verification_seconds = time.perf_counter() - verified_at
        started = time.perf_counter()
        options = {"local_files_only": True, "trust_remote_code": False, "revision": module.MODEL_REVISION}
        tokenizer = AutoTokenizer.from_pretrained(str(self.roots[kind]), **options)
        model = AutoModelForSequenceClassification.from_pretrained(
            str(self.roots[kind]), **options, use_safetensors=True, dtype=self.torch.float32,
            attn_implementation="eager").to("cuda").eval()
        parameters = list(model.parameters())
        if model.config.num_labels != (1 if kind == "jev" else 3) or sum(p.numel() for p in parameters) != module.FP32_PARAMETER_COUNT:
            raise RuntimeError("pinned GPU parameter/class count differs: " + kind)
        if any(p.device.type != "cuda" or p.dtype != self.torch.float32 for p in parameters):
            raise RuntimeError("every model parameter must be FP32 on CUDA")
        self.loaded[kind] = (tokenizer, model, pins)
        return tokenizer, model, pins, {"file_verification_seconds": verification_seconds,
                                      "model_load_seconds": time.perf_counter() - started}

    def logits(self, tokenizer, model, encodings, expected_columns):
        features = tokenizer.pad(encodings, padding=True, return_tensors="pt")
        with self.torch.inference_mode():
            logits = model(**{k: v.to("cuda") for k, v in features.items()}).logits.detach().cpu().tolist()
        if len(logits) != len(encodings) or any(not isinstance(row, list) or len(row) != expected_columns
                                              or any(not math.isfinite(float(v)) for v in row) for row in logits):
            raise ValueError("GPU batch output is not the expected finite logit matrix")
        return logits

    def infer_jev(self, requests):
        tokenizer, model, pins, runtime = self.load("jev")
        runtime.update(inference_seconds=0.0, tokenization_seconds=0.0)
        records, prepared, scores = {}, [], {}
        for request in requests:
            key, counts, encodings = request["cache_key"], {}, []
            started = time.perf_counter()
            try:
                context = JEV.render_context(request["question"], request["state"])
                for choice in request["choices"]:
                    encoding = tokenizer(context, JEV.render_candidate(choice), truncation=False,
                                         padding=False, add_special_tokens=True)
                    counts[choice["label"]] = len(encoding["input_ids"])
                    encodings.append(encoding)
                if any(count > JEV.MAX_TOKENS for count in counts.values()):
                    records[key] = {**JEV._empty_scores("input_too_long", "pair exceeds 512 tokens; no truncation"), "token_counts": counts}
                else:
                    scores[key] = {"values": [None] * len(encodings), "latency_seconds": 0.0,
                                   "token_counts": counts, "request": request}
                    prepared.extend((key, index, encoding) for index, encoding in enumerate(encodings))
            except Exception as error:
                records[key] = {**JEV._empty_scores("tokenization_error", str(error), type(error).__name__), "token_counts": counts or None}
            runtime["tokenization_seconds"] += time.perf_counter() - started
        for offset in range(0, len(prepared), 16):
            batch = [item for item in prepared[offset:offset + 16] if item[0] not in records]
            if not batch:
                continue
            started = time.perf_counter()
            try:
                rows = self.logits(tokenizer, model, [item[2] for item in batch], 1)
                elapsed = time.perf_counter() - started
                for (key, index, _), row in zip(batch, rows, strict=True):
                    scores[key]["values"][index] = float(row[0])
                    scores[key]["latency_seconds"] += elapsed / len(batch)
            except Exception as error:
                for key, _, _ in batch:
                    records[key] = {**JEV._empty_scores("inference_error", str(error), type(error).__name__), "token_counts": scores[key]["token_counts"]}
            runtime["inference_seconds"] += time.perf_counter() - started
        for key, score in scores.items():
            if key in records:
                continue
            values = score["values"]
            if any(v is None for v in values):
                records[key] = {**JEV._empty_scores("inference_error", "candidate scoring incomplete", "RuntimeError"), "token_counts": score["token_counts"]}
                continue
            probabilities = JEV._softmax(values)
            labels = [c["label"] for c in score["request"]["choices"]]
            winner = max(range(len(values)), key=lambda i: values[i])
            records[key] = {"status": "ok", "logits": dict(zip(labels, values, strict=True)),
                            "probabilities": dict(zip(labels, probabilities, strict=True)), "argmax_label": labels[winner],
                            "argmax_index": winner, "argmax_probability": probabilities[winner],
                            "token_counts": score["token_counts"], "latency_seconds": score["latency_seconds"],
                            "note": None, "error_type": None}
        return records, pins, runtime

    def run_choices(self, requests, *, cache=None, threads=4, batch_size=16, **_):
        if (threads, batch_size) != (4, 16):
            raise ValueError("frozen GPU settings are four host threads and batch size 16")
        started = time.perf_counter()
        unique, seen_ids = {}, set()
        for request in requests:
            JEV._validate_request(request)
            if request["id"] in seen_ids:
                raise ValueError("duplicate choice request ID")
            seen_ids.add(request["id"])
            key = JEV.request_key(request["question"], request["state"], request["choices"])
            unique.setdefault(key, {**request, "cache_key": key})
        available, pending = {}, []
        for key, request in unique.items():
            previous = (cache or {}).get(key)
            if previous is None:
                pending.append(request)
                continue
            if (previous.get("cache_key"), previous.get("model_id"), previous.get("model_revision"),
                    previous.get("precision"), previous.get("device"), previous.get("format_version")) != (
                    key, JEV.MODEL_ID, JEV.MODEL_REVISION, "fp32", "cuda", JEV.FORMAT_VERSION):
                raise ValueError("GPU JEV cache identity differs")
            if JEV.request_key(previous["question"], previous["state"], previous["choices"]) != key:
                raise ValueError("GPU JEV cache text differs")
            JEV._validate_scores(previous, request)
            available[key] = previous
        pins, runtime = {**JEV._pins(), "device": "cuda"}, {}
        if pending:
            inferred, pins, runtime = self.infer_jev(pending)
            available.update(inferred)
        records, seen_keys = [], set()
        for request in requests:
            key = JEV.request_key(request["question"], request["state"], request["choices"])
            score = available[key]
            JEV._validate_scores(score, request)
            records.append({**deepcopy(request), "request": deepcopy(request), "cache_key": key,
                            "model_id": JEV.MODEL_ID, "model_revision": JEV.MODEL_REVISION,
                            "precision": "fp32", "device": "cuda", "format_version": JEV.FORMAT_VERSION,
                            **{field: deepcopy(score.get(field)) for field in JEV._empty_scores("ok")},
                            "pins": deepcopy(pins), "cache_reused": key in seen_keys or key in (cache or {})})
            seen_keys.add(key)
        runtime.update(total_seconds=time.perf_counter() - started, device="cuda", precision="fp32",
                       request_count=len(requests), unique_request_count=len(unique), inferred_unique_request_count=len(pending),
                       cached_unique_request_count=len(unique) - len(pending),
                       input_too_long_count=sum(r["status"] == "input_too_long" for r in records),
                       error_count=sum(r["status"] != "ok" for r in records))
        return {"records": records, "pins": pins, "runtime": runtime}

    def infer_nli(self, pairs):
        tokenizer, model, pins, runtime = self.load("nli")
        runtime.update(inference_seconds=0.0, tokenization_seconds=0.0)
        records, prepared = {}, []
        for pair in pairs:
            key = pair["cache_key"]
            started = time.perf_counter()
            try:
                encoding = tokenizer(pair["premise"], pair["hypothesis"], truncation=False,
                                     padding=False, add_special_tokens=True)
                count = len(encoding["input_ids"])
                if count > NLI.MAX_TOKENS:
                    records[key] = {"probabilities": None, "token_count_untruncated": count,
                                    "latency_seconds": 0.0, "note": "pair exceeds 512 tokens; no truncation", "error_type": None}
                else:
                    prepared.append((key, encoding, count))
            except Exception as error:
                records[key] = {"probabilities": None, "token_count_untruncated": None, "latency_seconds": 0.0,
                                "note": str(error), "error_type": type(error).__name__}
            runtime["tokenization_seconds"] += time.perf_counter() - started
        for offset in range(0, len(prepared), 16):
            batch = prepared[offset:offset + 16]
            started = time.perf_counter()
            try:
                logits = self.logits(tokenizer, model, [item[1] for item in batch], 3)
                elapsed = time.perf_counter() - started
                for (key, _, count), row in zip(batch, logits, strict=True):
                    probabilities, _, _ = NLI.relation_probabilities(row)
                    records[key] = {"probabilities": probabilities, "token_count_untruncated": count,
                                    "latency_seconds": elapsed / len(batch), "note": None, "error_type": None}
            except Exception as error:
                for key, _, count in batch:
                    records[key] = {"probabilities": None, "token_count_untruncated": count, "latency_seconds": 0.0,
                                    "note": str(error), "error_type": type(error).__name__}
            runtime["inference_seconds"] += time.perf_counter() - started
        return records, pins, runtime

    def run_pairs(self, pairs, *, cache=None, threads=4, batch_size=16, **_):
        if (threads, batch_size) != (4, 16):
            raise ValueError("frozen GPU settings are four host threads and batch size 16")
        started = time.perf_counter()
        unique, seen_ids = {}, set()
        for pair in pairs:
            NLI._validate_pair(pair)
            if pair["id"] in seen_ids:
                raise ValueError("duplicate NLI pair ID")
            seen_ids.add(pair["id"])
            key = NLI.pair_key(pair["premise"], pair["hypothesis"])
            unique.setdefault(key, {**pair, "cache_key": key})
        available, pending = {}, []
        for key, pair in unique.items():
            previous = (cache or {}).get(key)
            if previous is None:
                pending.append(pair)
                continue
            if (previous.get("cache_key"), previous.get("model_id"), previous.get("model_revision"),
                    previous.get("precision"), previous.get("device")) != (key, NLI.MODEL_ID, NLI.MODEL_REVISION, "fp32", "cuda"):
                raise ValueError("GPU NLI cache identity differs")
            if NLI.pair_key(previous["premise"], previous["hypothesis"]) != key:
                raise ValueError("GPU NLI cache text differs")
            NLI._probabilities(previous)
            available[key] = previous
        pins = {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                "parameter_count": NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cuda",
                "file_sha256": NLI.NLI_FILE_SHA256}
        runtime = {}
        if pending:
            inferred, pins, runtime = self.infer_nli(pending)
            available.update(inferred)
        records, seen_keys = [], set()
        for pair in pairs:
            key = NLI.pair_key(pair["premise"], pair["hypothesis"])
            raw = available[key]
            probabilities = NLI._probabilities(raw)
            count = raw.get("token_count_untruncated")
            winner = max(probabilities, key=probabilities.get) if probabilities is not None and count is not None and count <= 512 else "unknown"
            confidence = probabilities[winner] if probabilities is not None and count is not None and count <= 512 else None
            records.append({**deepcopy(pair), "cache_key": key, "model_id": NLI.MODEL_ID, "model_revision": NLI.MODEL_REVISION,
                            "precision": "fp32", "device": "cuda", "probabilities": probabilities, "predicted_class": winner,
                            "predicted_confidence": confidence, "relation": winner if confidence is not None and confidence >= NLI.THRESHOLD else "unknown",
                            "confidence_threshold": NLI.THRESHOLD, "token_count_untruncated": count,
                            "latency_seconds": raw.get("latency_seconds", 0.0), "note": raw.get("note"),
                            "error_type": raw.get("error_type"), "cache_reused": key in seen_keys or key in (cache or {})})
            seen_keys.add(key)
        runtime.update(total_seconds=time.perf_counter() - started, device="cuda", precision="fp32", pair_count=len(pairs),
                       unique_pair_count=len(unique), inferred_unique_pair_count=len(pending), cached_unique_pair_count=len(unique) - len(pending),
                       long_input_unknown_count=sum((r["token_count_untruncated"] or 0) > 512 for r in records),
                       inference_error_count=sum(r["error_type"] is not None for r in records))
        return {"records": records, "pins": pins, "runtime": runtime}


def run(input_dir, output_dir, bundle_dir, scorer):
    """All requests, frozen text and full provenance; no reference data reads."""
    input_dir, output_dir, bundle_dir = Path(input_dir), Path(output_dir), Path(bundle_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not input_dir.resolve().is_relative_to(bundle_dir.resolve()):
        raise ValueError("input directory must be in the verified private bundle")
    bundle = verify_bundle(bundle_dir)
    requests, documents, bindings, manifest, names = TASK.load_inputs(input_dir)
    choices = TASK.choice_requests(requests, documents)
    unique_choices = TASK.unique_inputs(choices, lambda r: JEV.request_key(r["question"], r["state"], r["choices"]))
    pairs = TASK.pairs_for(requests, documents, bindings)
    output_dir.mkdir(parents=True, exist_ok=False)
    source_names = ("manifest.json", *names, *[n for n in ("row_plans.jsonl", "case_plans.jsonl") if (input_dir / n).exists()])
    for name in source_names:
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    TASK.write_rows(output_dir / "model-requests.jsonl", choices)
    TASK.write_rows(output_dir / "model-nli-pairs.jsonl", pairs)
    freeze = {"schema_version": "kaggle-full-condition-choices-v1", "labels_read": False,
              "errors_only": False, "development_only": True, "independent_test": False,
              "production_adoption_allowed": False, "scope_status": "unproved", "synthetic_data": False,
              "body_model_input": False, "full_description_preserved_in_source_documents": True,
              "ranking_loaded": False, "new_retrieval_run": False, "source_selected_value_in_question_or_state": False,
              "all_original_options_preserved": True, "source_input_errors_only": manifest.get("errors_only", False),
              "request_count": len(requests), "unique_jev_request_count": len(unique_choices),
              "candidate_pair_count": sum(len(c["choices"]) for c in choices),
              "unique_jev_candidate_pair_count": sum(len(c["choices"]) for c in unique_choices),
              "nli_pair_count_before_skipping": len(pairs), "unknown_control_label": TASK.UNKNOWN,
              "threads": 4, "batch_size": 16, "precision": "fp32", "device": "cuda", "gpu_runtime": scorer.runtime,
              "jev_chunk_requests": 16, "nli_chunk_pairs": 256,
              "question_template": TASK.QUESTION, "choice_description_template": TASK.DESCRIPTION,
              "unknown_description_template": TASK.UNKNOWN_DESCRIPTION, "hypothesis_template": TASK.HYPOTHESIS_TEMPLATE,
              "fusion_weights": {"nli_relative_entailment": .5, "jev_candidate_probability": .5},
              "fusion_policy": "JEV unknown/error/tie remains unknown; otherwise unique original-option winner by equal score mean supports only its exact source value",
              "single_option_policy": "preserve previous relative mass (1 if positive); fusion does not add an absolute-support check",
              "input_length_policy": "untruncated inputs; any missing, invalid, overlong or failed candidate yields unknown",
              "input_sha256": {n: TASK.sha(input_dir / n) for n in source_names},
              "code_sha256": {n: TASK.sha(HERE / n) for n in CODE_NAMES},
              "bundle_manifest_sha256": bundle["manifest_sha256"], "bundle_file_count": bundle["file_count"],
              "jev_pin": {**JEV._pins(), "device": "cuda"},
              "nli_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION, "device": "cuda", "precision": "fp32",
                          "parameter_count": NLI.FP32_PARAMETER_COUNT, "files_sha256": NLI.NLI_FILE_SHA256}}
    TASK.write_json(output_dir / "freeze.json", freeze)
    jev_cache, nli_cache, jev_runtime, nli_runtime = {}, {}, {}, {}
    jev_pins = nli_pins = None
    with TASK.Progress(output_dir, interval=30) as progress:
        progress.emit(stage="jev", completed_groups=0, total_groups=len(unique_choices))
        with (output_dir / "jev-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique_choices), 16):
                chunk = unique_choices[offset:offset + 16]
                result = scorer.run_choices(chunk)
                if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                    raise ValueError("GPU JEV output identity/order differs")
                TASK._append(handle, result["records"])
                jev_cache.update({r["cache_key"]: r for r in result["records"]})
                jev_pins = result["pins"]
                TASK._accumulate(jev_runtime, result["runtime"])
                progress.emit(completed_groups=min(offset + len(chunk), len(unique_choices)))
        jev_records = scorer.run_choices(choices, cache=jev_cache)["records"]
        TASK.write_rows(output_dir / "jev-classification-options.jsonl", jev_records)
        jev_predictions = [TASK.jev_prediction(r, rec, documents, bindings) for r, rec in zip(requests, jev_records, strict=True)]
        TASK.write_rows(output_dir / "jev-predictions.jsonl", jev_predictions)
        eligibility = {r["request_id"]: TASK.jev_eligibility(rec) for r, rec in zip(requests, jev_records, strict=True)}
        eligible_pairs = [p for p in pairs if eligibility[p["request_id"]][0]]
        unique_pairs = TASK.unique_inputs(eligible_pairs, lambda p: NLI.pair_key(p["premise"], p["hypothesis"]))
        TASK.write_rows(output_dir / "nli-skips.jsonl", [{"request_id": r["request_id"], "request_sha256": r["request_sha256"],
                        "reason": eligibility[r["request_id"]][1], "source_request": r,
                        "skipped_pair_count": len(r["option_values"])} for r in requests if not eligibility[r["request_id"]][0]])
        progress.emit(stage="nli", completed_pairs=0, total_pairs=len(unique_pairs))
        with (output_dir / "nli-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique_pairs), 256):
                chunk = unique_pairs[offset:offset + 256]
                result = scorer.run_pairs(chunk)
                if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                    raise ValueError("GPU NLI output identity/order differs")
                TASK._append(handle, result["records"])
                nli_cache.update({r["cache_key"]: r for r in result["records"]})
                nli_pins = result["pins"]
                TASK._accumulate(nli_runtime, result["runtime"])
                progress.emit(completed_pairs=min(offset + len(chunk), len(unique_pairs)))
        nli_records = scorer.run_pairs(eligible_pairs, cache=nli_cache)["records"] if eligible_pairs else []
        TASK.write_rows(output_dir / "nli-classification-options.jsonl", nli_records)
        grouped = defaultdict(list)
        for record in nli_records:
            grouped[record["request_id"]].append(record)
        nli_predictions = []
        for request in requests:
            records = grouped[request["request_id"]]
            decision = TASK.choose(request["option_values"], request["selected_value"], records)
            if not eligibility[request["request_id"]][0]:
                decision["reason"] = "skipped_" + eligibility[request["request_id"]][1]
            nli_predictions.append({**TASK.prediction_base(request, bindings), **decision, "candidate_records": records,
                                    "quote_refs": TASK.current_refs(request, documents) if decision["state"] == "support" else [],
                                    "variant": "nli-title-choice"})
        TASK.write_rows(output_dir / "nli-predictions.jsonl", nli_predictions)
        fusion = [TASK.fusion_prediction(r, j, n, documents, bindings)
                  for r, j, n in zip(requests, jev_predictions, nli_predictions, strict=True)]
        TASK.write_rows(output_dir / "fusion-predictions.jsonl", fusion)
        TASK.write_rows(output_dir / "predictions.jsonl", fusion)
        for name, expected in freeze["input_sha256"].items():
            if TASK.sha(input_dir / name) != expected:
                raise ValueError("input changed during GPU inference: " + name)
        for name, expected in freeze["code_sha256"].items():
            if TASK.sha(HERE / name) != expected:
                raise ValueError("code changed during GPU inference: " + name)
        if verify_bundle(bundle_dir)["manifest_sha256"] != bundle["manifest_sha256"]:
            raise ValueError("bundle manifest changed during GPU inference")
        progress.emit(stage="complete", request_count=len(fusion))
    summary = {**freeze, "jev_runtime": jev_runtime, "nli_runtime": nli_runtime,
               "verified_jev_pin": jev_pins, "verified_nli_pin": nli_pins,
               "nli_pair_count_after_skipping": len(eligible_pairs), "unique_nli_pair_count": len(unique_pairs),
               "nli_skipped_requests": dict(Counter(reason for valid, reason, _ in eligibility.values() if not valid)),
               "jev_prediction_states": dict(Counter(p["state"] for p in jev_predictions)),
               "fusion_prediction_states": dict(Counter(p["state"] for p in fusion)),
               "input_and_code_sha256_verified_after_inference": True,
               "output_sha256": {p.name: TASK.sha(p) for p in output_dir.iterdir() if p.is_file()}}
    TASK.write_json(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--weights-dir", type=Path, required=True)
    parser.add_argument("--jev-provenance", type=Path, required=True)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    verify_bundle(args.bundle_dir)
    if not args.jev_provenance.resolve().is_relative_to(args.bundle_dir.resolve()):
        raise ValueError("JEV provenance must be inside the verified bundle")
    weights = acquire_weights(args.weights_dir, args.jev_provenance, download=not args.no_download)
    scorer = CudaScorer(weights["jev"], weights["nli"])
    result = run(args.input_dir, args.output_dir, args.bundle_dir, scorer)
    print(TASK.canonical({"event": "complete", "request_count": result["request_count"],
                          "jev_prediction_states": result["jev_prediction_states"],
                          "fusion_prediction_states": result["fusion_prediction_states"]}), flush=True)


if __name__ == "__main__":
    main()
