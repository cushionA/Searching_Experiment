import argparse
import ctypes
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "experiments" / "structure-routing" / "minilm-ranking.py"
COMMON = ROOT / "lab-runs" / "structure-routing-all-candidates-20261115" / "common-inputs"
RUN = ROOT / "lab-runs" / "structure-routing-all-candidates-20261115" / "minilm-onnx"
MODEL = ROOT / ".deps" / "structure-routing-models" / "MiniLM-L6-dmr"
ONNX_MODEL = ROOT / ".deps" / "structure-routing-models" / "MiniLM-L6-dmr-onnx"
BATCH_SIZE = 32
THREADS = 4


def _source_module():
    spec = importlib.util.spec_from_file_location("minilm_ranking_source", BASELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_cases(source):
    manifest = json.loads((COMMON / "manifest.json").read_text(encoding="utf-8"))
    cases = []
    for item in manifest["cases"]:
        seed = item["seeds"][0]
        html_path = ROOT / seed["dom"]
        candidate_path = ROOT / seed["candidates"]
        html_text = html_path.read_text(encoding="utf-8")
        candidates, groups = source.extract_candidates(html_text)
        stored = json.loads(candidate_path.read_text(encoding="utf-8"))
        if candidates != stored["candidates"]:
            raise ValueError(f"Candidate schema differs from common input for case {item['id']}")
        if any(not candidate["bbox_missing"] for candidate in candidates):
            raise ValueError("Expected the shared missing-bbox constraint")
        cases.append({
            "id": item["id"],
            "task": item["task"],
            "url": seed["url"],
            "html_path": seed["dom"],
            "html_sha256": seed["dom_sha256"],
            "candidate_path": seed["candidates"],
            "candidate_sha256": source._sha256(candidate_path),
            "candidates": candidates,
            "groups": groups,
        })
    return cases


def _rank(candidates, scores):
    scored = [dict(candidate, score=float(score)) for candidate, score in zip(candidates, scores)]
    ranked = sorted(scored, key=lambda item: item["score"], reverse=True)
    group_scores = {}
    for candidate in scored:
        group_scores.setdefault(candidate["group_key"], []).append(candidate["score"])
    group_results = []
    for key, members in group_scores.items():
        candidate = next(row for row in scored if row["group_key"] == key)
        group_results.append({
            "group_key": key,
            "selector": candidate["group_selector"] + " a[href]",
            "candidate_count": len(members),
            "mean_score": math.fsum(members) / len(members),
            "max_score": max(members),
        })
    group_results.sort(key=lambda item: item["mean_score"], reverse=True)
    winner = next(item for item in group_results if item["group_key"] == ranked[0]["group_key"])
    return {
        "candidate_order": [item["xpath"] for item in ranked],
        "candidate_scores": [item["score"] for item in ranked],
        "group_ranking": group_results,
        "selected_group_key": winner["group_key"],
        "selected_selector": winner["selector"],
    }


def _token_hash(ids):
    return hashlib.sha256(",".join(str(value) for value in ids).encode("ascii")).hexdigest()


def _windows_peak_working_set():
    if os.name != "nt":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def export_and_reference():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(THREADS)
    source = _source_module()
    cases = _load_cases(source)
    manifest = source.model_manifest(MODEL)
    RUN.mkdir(parents=True, exist_ok=True)
    ONNX_MODEL.mkdir(parents=True, exist_ok=True)
    model_started = time.perf_counter()
    model = SentenceTransformer(
        str(MODEL), device="cpu", trust_remote_code=False, local_files_only=True,
    )
    model.eval()
    model_load_seconds = time.perf_counter() - model_started
    max_length = int(model.max_seq_length)
    tokenizer = model.tokenizer
    references = {}
    for case in cases:
        texts = [source.query_text(case["task"]), *(candidate["doc"] for candidate in case["candidates"])]
        raw_lengths = [len(tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]) for text in texts]
        actual_lengths = []
        token_hashes = []
        embeddings = []
        started = time.perf_counter()
        forward_calls = 0
        for offset in range(0, len(texts), BATCH_SIZE):
            features = model.tokenize(texts[offset:offset + BATCH_SIZE])
            actual_lengths.extend(int(value) for value in features["attention_mask"].sum(dim=1).tolist())
            token_hashes.extend(
                _token_hash(row[:int(mask.sum())].tolist())
                for row, mask in zip(features["input_ids"], features["attention_mask"])
            )
            with torch.inference_mode():
                encoded = model(features)["sentence_embedding"]
                encoded = torch.nn.functional.normalize(encoded.float(), p=2, dim=1)
            embeddings.append(encoded.cpu().numpy())
            forward_calls += 1
        inference_seconds = time.perf_counter() - started
        vectors = np.concatenate(embeddings, axis=0).astype(np.float32)
        scores = vectors[1:] @ vectors[0]
        reference = _rank(case["candidates"], scores.tolist())
        references[case["id"]] = {
            "case_id": case["id"],
            "task": case["task"],
            "url": case["url"],
            "html_sha256": case["html_sha256"],
            "candidate_sha256": case["candidate_sha256"],
            "candidate_count": len(case["candidates"]),
            "group_count": len(case["groups"]),
            "query_text": source.query_text(case["task"]),
            "max_sequence_length": max_length,
            "batch_size": BATCH_SIZE,
            "forward_calls": forward_calls,
            "tokenized_inputs": len(texts),
            "input_token_total": sum(actual_lengths),
            "input_token_max": max(actual_lengths, default=0),
            "truncation_count": sum(length > max_length for length in raw_lengths),
            "bbox_missing_candidates": sum(bool(candidate["bbox_missing"]) for candidate in case["candidates"]),
            "token_id_sha256": token_hashes,
            "cosine_by_candidate": [float(score) for score in scores],
            "ranking": reference,
            "inference_seconds": inference_seconds,
        }

    transformer = model[0].auto_model
    transformer.eval()

    class TokenEncoder(torch.nn.Module):
        def __init__(self, encoder):
            super().__init__()
            self.encoder = encoder

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.encoder(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                return_dict=False,
            )[0]

    export_features = model.tokenize([source.query_text(cases[0]["task"]), cases[0]["candidates"][0]["doc"]])
    sample = (
        export_features["input_ids"],
        export_features["attention_mask"],
        export_features["token_type_ids"],
    )
    float_path = ONNX_MODEL / "model-fp32.onnx"
    export_started = time.perf_counter()
    torch.onnx.export(
        TokenEncoder(transformer), sample, str(float_path),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["token_embeddings"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "sequence"},
            "attention_mask": {0: "batch", 1: "sequence"},
            "token_type_ids": {0: "batch", 1: "sequence"},
            "token_embeddings": {0: "batch", 1: "sequence"},
        },
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    export_seconds = time.perf_counter() - export_started
    result = {
        "model": manifest,
        "model_directory": str(MODEL.relative_to(ROOT)),
        "onnx_directory": str(ONNX_MODEL.relative_to(ROOT)),
        "fp32_onnx_path": str(float_path.relative_to(ROOT)),
        "fp32_onnx_bytes": float_path.stat().st_size,
        "max_sequence_length": max_length,
        "model_load_seconds": model_load_seconds,
        "export_seconds": export_seconds,
        "torch_threads": torch.get_num_threads(),
        "torch_version": torch.__version__,
        "cases": references,
    }
    destination = RUN / "torch-reference.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), "fp32_onnx": str(float_path),
                      "bytes": float_path.stat().st_size, "model_load_seconds": model_load_seconds,
                      "export_seconds": export_seconds,
                      "reference_cases": {key: value["ranking"]["selected_selector"] for key, value in references.items()}},
                     ensure_ascii=False))


def quantize():
    from onnxruntime.quantization import QuantType, quantize_dynamic

    source = ONNX_MODEL / "model-fp32.onnx"
    target = ONNX_MODEL / "model-int8.onnx"
    if not source.is_file():
        raise FileNotFoundError(source)
    started = time.perf_counter()
    quantize_dynamic(
        str(source), str(target),
        weight_type=QuantType.QInt8,
        op_types_to_quantize=["MatMul", "Gemm"],
    )
    elapsed = time.perf_counter() - started
    result = {
        "source": str(source.relative_to(ROOT)),
        "target": str(target.relative_to(ROOT)),
        "source_bytes": source.stat().st_size,
        "target_bytes": target.stat().st_size,
        "quantization": "ONNX Runtime dynamic QInt8 weights; MatMul/Gemm",
        "quantization_seconds": elapsed,
    }
    destination = RUN / "quantization.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(destination), **result}, ensure_ascii=False))


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def predict(html_path, task, model_file, tokenizer_file, output_path, command_started=None):
    source = _source_module()
    started = time.perf_counter()
    html_path = Path(html_path).resolve(strict=True)
    model_path = Path(model_file)
    if not model_path.is_absolute():
        model_path = ONNX_MODEL / model_path
    model_path = model_path.resolve(strict=True)
    tokenizer_path = Path(tokenizer_file)
    if not tokenizer_path.is_absolute():
        tokenizer_path = ONNX_MODEL / tokenizer_path
    tokenizer_path = tokenizer_path.resolve(strict=True)
    metadata_path = ONNX_MODEL / "model-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    tokenizer_sha = _file_sha256(tokenizer_path)
    if tokenizer_sha != metadata["tokenizer_sha256"]:
        raise ValueError("Tokenizer hash does not match ONNX artifact metadata")

    html_text = html_path.read_text(encoding="utf-8")
    html_sha = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
    candidates, groups = source.extract_candidates(html_text)
    query = source.query_text(task)
    texts = [query, *(candidate["doc"] for candidate in candidates)]
    onnx_sha = _file_sha256(model_path)
    if not candidates:
        result = {
            "status": "no_candidates",
            "kind": "selector",
            "selector": None,
            "task": task,
            "input": {"html_sha256": html_sha, "candidate_count": 0, "group_count": 0},
        }
    else:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = THREADS
        options.inter_op_num_threads = 1
        session_started = time.perf_counter()
        session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        model_load_seconds = time.perf_counter() - session_started
        tokenizer_started = time.perf_counter()
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer_load_seconds = time.perf_counter() - tokenizer_started
        raw_length_started = time.perf_counter()
        raw_lengths = [len(tokenizer.encode(text).ids) for text in texts]
        raw_length_scan_seconds = time.perf_counter() - raw_length_started
        max_length = int(metadata["max_sequence_length"])
        tokenizer.enable_truncation(max_length=max_length)
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        vectors = []
        token_total = 0
        max_input_tokens = 0
        session_calls = 0
        inference_started = time.perf_counter()
        for offset in range(0, len(texts), BATCH_SIZE):
            encodings = tokenizer.encode_batch(texts[offset:offset + BATCH_SIZE])
            ids = [encoding.ids for encoding in encodings]
            masks = [encoding.attention_mask for encoding in encodings]
            types = [encoding.type_ids for encoding in encodings]
            actual = [sum(mask) for mask in masks]
            token_total += sum(actual)
            max_input_tokens = max(max_input_tokens, max(actual, default=0))
            hidden = session.run(
                ["token_embeddings"],
                {
                    "input_ids": np.asarray(ids, dtype=np.int64),
                    "attention_mask": np.asarray(masks, dtype=np.int64),
                    "token_type_ids": np.asarray(types, dtype=np.int64),
                },
            )[0]
            mask = np.asarray(masks, dtype=np.float32)[..., None]
            pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
            normalized = pooled / np.maximum(np.linalg.norm(pooled, ord=2, axis=1, keepdims=True), 1e-12)
            vectors.append(normalized.astype(np.float32))
            session_calls += 1
        inference_seconds = time.perf_counter() - inference_started
        vectors = np.concatenate(vectors, axis=0)
        scores = vectors[1:] @ vectors[0]
        ranking = _rank(candidates, scores.tolist())
        ranked_candidates = sorted(
            (dict(candidate, score=float(score)) for candidate, score in zip(candidates, scores)),
            key=lambda item: item["score"], reverse=True,
        )
        result = {
            "status": "ok",
            "kind": "selector",
            "selector_type": "css",
            "attribute": "href",
            "selector": ranking["selected_selector"],
            "selected_group_key": ranking["selected_group_key"],
            "task": task,
            "query": query,
            "model": {
                "source_repo": metadata["source_model_repo"],
                "source_revision": metadata["source_model_revision"],
                "source_weight_sha256": metadata["source_weight_sha256"],
                "onnx_path": str(model_path),
                "onnx_bytes": model_path.stat().st_size,
                "onnx_sha256": onnx_sha,
                "tokenizer_path": str(tokenizer_path),
                "tokenizer_bytes": tokenizer_path.stat().st_size,
                "tokenizer_sha256": tokenizer_sha,
                "metadata_path": str(metadata_path),
                "metadata_bytes": metadata_path.stat().st_size,
                "pooling": metadata["pooling"],
                "max_sequence_length": max_length,
            },
            "input": {
                "html_path": str(html_path),
                "html_sha256": html_sha,
                "candidate_count": len(candidates),
                "group_count": len(groups),
                "bbox_missing_candidates": sum(bool(candidate["bbox_missing"]) for candidate in candidates),
                "tokenized_inputs": len(texts),
                "input_token_total": token_total,
                "input_token_max": max_input_tokens,
                "truncation_count": sum(length > max_length for length in raw_lengths),
            },
            "ranking": [
                {
                    "rank": index + 1,
                    "xpath": candidate["xpath"],
                    "group_key": candidate["group_key"],
                    "selector": candidate["group_selector"] + " a[href]",
                    "href": candidate["href"],
                    "score": candidate["score"],
                }
                for index, candidate in enumerate(ranked_candidates)
            ],
            "group_ranking": ranking["group_ranking"],
            "runtime": {
                "provider": session.get_providers()[0],
                "intra_op_threads": options.intra_op_num_threads,
                "inter_op_threads": options.inter_op_num_threads,
                "model_load_seconds": model_load_seconds,
                "tokenizer_load_seconds": tokenizer_load_seconds,
                "raw_length_scan_seconds": raw_length_scan_seconds,
                "inference_seconds": inference_seconds,
                "session_calls": session_calls,
                "batch_size": BATCH_SIZE,
                "peak_working_set_bytes": _windows_peak_working_set(),
                "torch_import_spec": None if importlib.util.find_spec("torch") is None else "unexpectedly-present",
                "sentence_transformers_import_spec": None if importlib.util.find_spec("sentence_transformers") is None else "unexpectedly-present",
                "onnxruntime_version": ort.__version__,
                "tokenizers_version": importlib.metadata.version("tokenizers"),
                "numpy_version": np.__version__,
                "lxml_version": importlib.metadata.version("lxml"),
            },
            "benchmark_reference_dependency": False,
            "original_weight_dependency": False,
            "candidate_manifest_dependency": False,
        }
    result["process_elapsed_seconds"] = time.perf_counter() - (command_started or started)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "status": result["status"], "selector": result.get("selector"),
                      "candidate_count": result.get("input", {}).get("candidate_count"),
                      "process_elapsed_seconds": result["process_elapsed_seconds"]}, ensure_ascii=False))


def _run_onnx(case, reference, mode, batch_size):
    import onnxruntime as ort
    from tokenizers import Tokenizer

    model_path = ONNX_MODEL / ("model-fp32.onnx" if mode == "fp32" else "model-int8.onnx")
    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    session_started = time.perf_counter()
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    session_load_seconds = time.perf_counter() - session_started
    tokenizer_started = time.perf_counter()
    tokenizer = Tokenizer.from_file(str(MODEL / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=reference["max_sequence_length"])
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    tokenizer_load_seconds = time.perf_counter() - tokenizer_started
    texts = [reference["query_text"], *(candidate["doc"] for candidate in case["candidates"])]
    input_ids = []
    attention = []
    token_types = []
    actual_lengths = []
    token_hashes = []
    for offset in range(0, len(texts), batch_size):
        encodings = tokenizer.encode_batch(texts[offset:offset + batch_size])
        ids = [encoding.ids for encoding in encodings]
        masks = [encoding.attention_mask for encoding in encodings]
        types = [encoding.type_ids for encoding in encodings]
        input_ids.append(np.asarray(ids, dtype=np.int64))
        attention.append(np.asarray(masks, dtype=np.int64))
        token_types.append(np.asarray(types, dtype=np.int64))
        actual_lengths.extend(int(sum(mask)) for mask in masks)
        token_hashes.extend(_token_hash(row[:sum(mask)]) for row, mask in zip(ids, masks))
    inference_started = time.perf_counter()
    vectors = []
    session_calls = 0
    for ids, masks, types in zip(input_ids, attention, token_types):
        hidden = session.run(
            ["token_embeddings"],
            {"input_ids": ids, "attention_mask": masks, "token_type_ids": types},
        )[0]
        mask = masks.astype(np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
        normalized = pooled / np.maximum(np.linalg.norm(pooled, ord=2, axis=1, keepdims=True), 1e-12)
        vectors.append(normalized.astype(np.float32))
        session_calls += 1
    inference_seconds = time.perf_counter() - inference_started
    vectors = np.concatenate(vectors, axis=0)
    scores = vectors[1:] @ vectors[0]
    ranking = _rank(case["candidates"], scores.tolist())
    ref_scores = np.asarray(reference["cosine_by_candidate"], dtype=np.float32)
    rank_exact = ranking["candidate_order"] == reference["ranking"]["candidate_order"]
    token_exact = token_hashes == reference["token_id_sha256"]
    return {
        "model_path": str(model_path.relative_to(ROOT)),
        "model_bytes": model_path.stat().st_size,
        "mode": mode,
        "provider": session.get_providers()[0],
        "intra_op_threads": options.intra_op_num_threads,
        "inter_op_threads": options.inter_op_num_threads,
        "session_load_seconds": session_load_seconds,
        "tokenizer_load_seconds": tokenizer_load_seconds,
        "inference_seconds": inference_seconds,
        "session_calls": session_calls,
        "tokenized_inputs": len(texts),
        "input_token_total": sum(actual_lengths),
        "input_token_max": max(actual_lengths, default=0),
        "truncation_count": sum(length > reference["max_sequence_length"] for length in actual_lengths),
        "bbox_missing_candidates": sum(bool(candidate["bbox_missing"]) for candidate in case["candidates"]),
        "token_id_hashes_exact": token_exact,
        "cosine_max_abs_delta_vs_pytorch": float(np.max(np.abs(scores - ref_scores))) if len(scores) else 0.0,
        "cosine_mean_abs_delta_vs_pytorch": float(np.mean(np.abs(scores - ref_scores))) if len(scores) else 0.0,
        "candidate_rank_exact_vs_pytorch": rank_exact,
        "selected_group_exact_vs_pytorch": ranking["selected_group_key"] == reference["ranking"]["selected_group_key"],
        "selected_selector": ranking["selected_selector"],
        "candidate_order": ranking["candidate_order"],
        "group_ranking": ranking["group_ranking"],
        "peak_working_set_bytes": _windows_peak_working_set(),
        "torch_import_spec": None if importlib.util.find_spec("torch") is None else "unexpectedly-present",
        "sentence_transformers_import_spec": None if importlib.util.find_spec("sentence_transformers") is None else "unexpectedly-present",
        "onnxruntime_version": ort.__version__,
        "tokenizers_version": importlib.metadata.version("tokenizers"),
        "numpy_version": np.__version__,
        "lxml_version": importlib.metadata.version("lxml"),
    }


def rank(mode, case_id=None):
    source = _source_module()
    cases = _load_cases(source)
    ref_path = RUN / "torch-reference.json"
    reference = json.loads(ref_path.read_text(encoding="utf-8"))
    chosen = [case for case in cases if case_id is None or case["id"] == case_id]
    results = []
    for case in chosen:
        case_reference = reference["cases"][case["id"]]
        result = _run_onnx(case, case_reference, mode, BATCH_SIZE)
        results.append({
            "case_id": case["id"],
            "task": case["task"],
            "url": case["url"],
            "html_sha256": case["html_sha256"],
            "candidate_sha256": case["candidate_sha256"],
            "candidate_count": len(case["candidates"]),
            "group_count": len(case["groups"]),
            "query_text": source.query_text(case["task"]),
            "input_schema": "query_text + candidates[*].doc from shared extract_candidates; mean token pooling with attention mask; L2 normalize; cosine; group mean ordering and top-candidate rule selection",
            "bbox_missing": all(bool(candidate["bbox_missing"]) for candidate in case["candidates"]),
            "runtime": result,
        })
    out = {
        "model_repo": "McGill-NLP/MiniLM-L6-dmr",
        "model_revision": source.MODEL_REVISION,
        "model_file_hashes": source.model_manifest(MODEL)["files"],
        "mode": mode,
        "batch_size": BATCH_SIZE,
        "results": results,
    }
    path = RUN / f"{mode}-results.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "results": [
        {"case_id": item["case_id"], "selector": item["runtime"]["selected_selector"],
         "session_load_seconds": item["runtime"]["session_load_seconds"],
         "inference_seconds": item["runtime"]["inference_seconds"],
         "cosine_max_abs_delta": item["runtime"]["cosine_max_abs_delta_vs_pytorch"],
         "rank_exact": item["runtime"]["candidate_rank_exact_vs_pytorch"],
         "token_exact": item["runtime"]["token_id_hashes_exact"]}
        for item in results]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("export", "quantize", "rank-fp32", "rank-int8", "predict"))
    parser.add_argument("--case", choices=("01", "08"))
    parser.add_argument("--html", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--model-file", type=Path, default=Path("model-fp32.onnx"))
    parser.add_argument("--tokenizer", type=Path, default=Path("tokenizer.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    if args.action == "export":
        export_and_reference()
    elif args.action == "quantize":
        quantize()
    elif args.action == "predict":
        if args.html is None or args.task is None or args.output is None:
            parser.error("predict requires --html, --task, and --output")
        predict(args.html, args.task, args.model_file, args.tokenizer, args.output, started)
    elif args.action == "rank-fp32":
        rank("fp32", args.case)
    else:
        rank("int8", args.case)
    print(json.dumps({"process_elapsed_seconds": time.perf_counter() - started}, ensure_ascii=False))


if __name__ == "__main__":
    main()
