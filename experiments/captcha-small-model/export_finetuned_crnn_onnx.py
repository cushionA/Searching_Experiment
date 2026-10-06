#!/usr/bin/env python3
"""Export a verified validation-selected CRNN and audit ONNX/PyTorch parity.

Only the supplied frozen internal test fold is used for export verification.
Whole images, fixed vocabulary and greedy CTC are unchanged. No training,
quantization, downloaded Python execution or external ONNX tensor data is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import json
import time
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_exported_crnn_{name}", Path(__file__).with_name(name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selected = sibling("evaluate_finetuned_crnn_text")
runtime = sibling("recognize_finetuned_crnn_onnx")
crnn, splitting = selected.crnn, selected.splitting


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def group_score(group: list[dict[str, Any]]) -> dict[str, Any]:
        chars = sum(len(row["label"]) for row in group)
        correct = sum(row["exact"] for row in group)
        distance = sum(row["distance"] for row in group)
        return {"samples": len(group), "exact_count": correct, "exact_match": correct / len(group),
                "reference_characters": chars, "edit_distance": distance, "character_error_rate": distance / chars}
    return group_score(rows) | {"by_source": {source: group_score([row for row in rows if row["source"] == source])
                                              for source in sorted({row["source"] for row in rows})}}


def reference_rows(path: Path, expected_sha: str, samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected.verify_checkpoint_sha(path, expected_sha)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != {sample["id"] for sample in samples}:
        raise ValueError("reference predictions must cover exactly the frozen test fold")
    for sample in samples:
        row = by_id[sample["id"]]
        if any(row.get(key) != sample[key] for key in ("source", "sha256", "label")) or not isinstance(row.get("answer"), str):
            raise ValueError("reference prediction image identity mismatch")
    return by_id


def export(args: argparse.Namespace) -> dict[str, Any]:
    import numpy as np
    import onnx
    from safetensors import safe_open
    from PIL import Image
    from torchvision.transforms.functional import to_tensor

    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite export directory: {args.output}")
    if args.batch_size <= 0 or args.opset not in (17, 18):
        raise ValueError("batch size must be positive and opset 17 or 18")
    checkpoint, model_dir, manifest_path, split_path = (path.resolve() for path in
                                                      (args.checkpoint, args.model_dir, args.manifest, args.split))
    config_path = (args.train_config or checkpoint.parent / "config.json").resolve()
    checkpoint_sha = selected.verify_checkpoint_sha(checkpoint, args.checkpoint_sha256)
    files = crnn.verify_model_files(model_dir)
    manifest, samples = crnn.reference().load_and_check_manifest(manifest_path)
    manifest_sha, split_sha = crnn.sha256_file(manifest_path), crnn.sha256_file(split_path)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    partitions = splitting.validate_split(samples, split, manifest_sha)
    test_samples = partitions["test"]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    with safe_open(checkpoint, framework="pt", device="cpu") as handle:
        checkpoint_metadata = handle.metadata() or {}
    epoch = selected.validate_provenance(checkpoint_metadata, config, manifest_sha, split_sha, files, split)
    reference_path = args.reference_predictions.resolve()
    reference = reference_rows(reference_path, args.reference_predictions_sha256, test_samples)
    torch, device = selected.setup_runtime("cpu")
    model = selected.load_selected_model(torch, checkpoint, device)
    output = args.output.resolve()
    output.mkdir(parents=True)
    graph_path = output / "captcha-crnn-finetuned.onnx"
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    export_started = time.perf_counter()
    dummy = torch.zeros((1, 1, 40, 150), dtype=torch.float32)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        torch.onnx.export(model, (dummy,), str(graph_path), export_params=True,
                          input_names=["pixel_values"], output_names=["logits"],
                          dynamic_axes={"pixel_values": {0: "batch"}, "logits": {0: "batch"}},
                          opset_version=args.opset, do_constant_folding=True,
                          dynamo=False, external_data=False)
    export_warnings = [{"category": item.category.__name__, "message": str(item.message)} for item in captured]
    graph = onnx.load(str(graph_path), load_external_data=False)
    if any(tensor.data_location == onnx.TensorProto.EXTERNAL or tensor.external_data
           for tensor in graph.graph.initializer):
        raise ValueError("export must have inline weights without external tensor files")
    props = {
        "model_family": "CaptchaCRNN", "checkpoint_sha256": checkpoint_sha,
        "selected_epoch": str(epoch), "selection": "validation only",
        "training_config_sha256": crnn.sha256_file(config_path), "manifest_sha256": manifest_sha,
        "split_sha256": split_sha, "vocabulary": crnn.VOCABULARY, "blank_index": "0",
        "input_processor": "PIL-L-bicubic150x40-float32-div255",
        "input_signature": "float32[batch,1,40,150]", "output_signature": "float32[batch,37,63]",
        "decoder": "argmax; collapse adjacent equal tokens including blank transitions; then remove blank0",
        "quantization": "none", "external_data": "false", "limitation": splitting.LIMITATION,
        "exporter_sha256": crnn.sha256_file(Path(__file__)),
        "architecture_sha256": crnn.sha256_file(Path(__file__).with_name("evaluate_crnn_text.py")),
    }
    onnx.helper.set_model_props(graph, props)
    onnx.checker.check_model(graph, full_check=True)
    onnx.save_model(graph, str(graph_path), save_as_external_data=False)
    # Verify the saved bytes, including embedded metadata, independently again.
    onnx.checker.check_model(str(graph_path), full_check=True)
    graph_sha = crnn.sha256_file(graph_path)
    ocr = runtime.CRNNOnnxOCR(graph_path, graph_sha)
    processor = SimpleNamespace(torch=torch, to_tensor=to_tensor)
    rows, comparisons, batch_checks = [], [], []
    maximum_difference, difference_sum, elements = 0.0, 0.0, 0
    argmax_differences, preprocessing_differences = 0, 0
    # Legacy LSTM export warnings are tested rather than suppressed. The same
    # artifact must accept batch1, batch2 and the requested normal batch size.
    for count in sorted({1, 2, min(args.batch_size, len(test_samples))}):
        images = []
        for sample in test_samples[:count]:
            with Image.open(sample["path"]) as image:
                images.append(image.copy())
        array = np.stack([runtime.preprocess(image) for image in images])
        with torch.inference_mode():
            tensor = torch.stack([crnn.CaptchaCRNNOCR.preprocess(processor, image)[0] for image in images])
            torch_logits = model(tensor).numpy()
        ort_logits = ocr.infer_logits(images)
        same_answers = runtime.greedy_ctc_decode(ort_logits) == crnn.greedy_ctc_decode(torch, torch.from_numpy(torch_logits))
        batch_checks.append({"batch_size": count, "input_shape": list(array.shape), "output_shape": list(ort_logits.shape),
                             "preprocessing_bitwise_equal": bool(np.array_equal(array, tensor.numpy())),
                             "same_decoded_answers": same_answers,
                             "max_logit_abs_difference": float(np.max(np.abs(ort_logits - torch_logits)))})
    verification_started = time.perf_counter()
    with torch.inference_mode():
        for start in range(0, len(test_samples), args.batch_size):
            group, images = test_samples[start:start + args.batch_size], []
            for sample in group:
                with Image.open(sample["path"]) as image:
                    images.append(image.copy())
            tensor = torch.stack([crnn.CaptchaCRNNOCR.preprocess(processor, image)[0] for image in images])
            array = np.stack([runtime.preprocess(image) for image in images])
            preprocessing_differences += int(np.count_nonzero(array != tensor.numpy()))
            torch_logits = model(tensor).numpy()
            ort_logits = ocr.infer_logits(images)
            difference = np.abs(ort_logits - torch_logits)
            maximum_difference = max(maximum_difference, float(difference.max()))
            difference_sum += float(difference.sum(dtype=np.float64))
            elements += difference.size
            tokens_different = np.argmax(ort_logits, axis=-1) != np.argmax(torch_logits, axis=-1)
            argmax_differences += int(np.count_nonzero(tokens_different))
            torch_answers = crnn.greedy_ctc_decode(torch, torch.from_numpy(torch_logits))
            ort_answers = runtime.greedy_ctc_decode(ort_logits)
            for index, (sample, torch_answer, answer) in enumerate(zip(group, torch_answers, ort_answers, strict=True)):
                rows.append({key: sample[key] for key in ("id", "source", "sha256", "label")} |
                            {"answer": answer, "exact": answer == sample["label"],
                             "distance": crnn.reference().levenshtein(answer, sample["label"])})
                comparisons.append({"id": sample["id"], "torch_answer": torch_answer,
                                    "onnx_answer": answer, "saved_cpu_reference_answer": reference[sample["id"]]["answer"],
                                    "onnx_matches_torch": answer == torch_answer,
                                    "onnx_matches_saved_reference": answer == reference[sample["id"]]["answer"],
                                    "max_logit_abs_difference": float(difference[index].max()),
                                    "argmax_token_differences": int(np.count_nonzero(tokens_different[index]))})
    crnn.reference().atomic_jsonl(output / "onnx-test.jsonl", rows)
    crnn.reference().atomic_jsonl(output / "parity.jsonl", comparisons)
    mismatches = [row for row in comparisons if not row["onnx_matches_torch"] or not row["onnx_matches_saved_reference"]]
    good_batches = all(row["same_decoded_answers"] and row["preprocessing_bitwise_equal"] for row in batch_checks)
    passed = not mismatches and good_batches and preprocessing_differences == 0
    runtime_path = Path(__file__).with_name("recognize_finetuned_crnn_onnx.py")
    (output / runtime_path.name).write_bytes(runtime_path.read_bytes())
    (output / "requirements-runtime.txt").write_text("\n".join(f"{name}=={importlib.metadata.version(name)}" for name in
                                                              ("onnxruntime", "numpy", "Pillow")) + "\n", encoding="utf-8")
    initializer_digest = hashlib.sha256()
    for initializer in sorted(graph.graph.initializer, key=lambda tensor: tensor.name):
        initializer_digest.update(initializer.SerializeToString())
    result = {
        "schema_version": 1, "status": "PASS" if passed else "FAIL", "started_at_utc": started,
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checkpoint": {"path": str(checkpoint), "sha256": checkpoint_sha, "selected_epoch": epoch,
                       "unmodified_after_export": crnn.sha256_file(checkpoint) == checkpoint_sha},
        "graph": {"file": graph_path.name, "sha256": graph_sha, "bytes": graph_path.stat().st_size,
                  "opset": args.opset, "ir_version": graph.ir_version, "checker_full_check": "PASS",
                  "external_data": False, "initializer_count": len(graph.graph.initializer),
                  "initializer_protobuf_sha256": initializer_digest.hexdigest(), "metadata": props,
                  "input_signature": ["batch", 1, 40, 150], "output_signature": ["batch", 37, 63]},
        "exporter": {"torch_onnx_dynamo": False, "do_constant_folding": True,
                     "export_seconds": verification_started - export_started, "warnings": export_warnings},
        "scope": "Frozen internal test614 if supplied standard manifest; full manifest checked; no training images scored.",
        "full_manifest_samples": len(samples), "evaluated_samples": len(test_samples),
        "manifest_sha256": manifest_sha, "split_sha256": split_sha, "fold": "test",
        "training_config_sha256": crnn.sha256_file(config_path), "training_config": config,
        "saved_cpu_reference": {"path": str(reference_path), "sha256": args.reference_predictions_sha256},
        "metrics": score(rows), "parity": {"decoded_mismatches": len(mismatches), "mismatches": mismatches,
                                             "preprocessing_element_differences": preprocessing_differences,
                                             "argmax_token_differences": argmax_differences,
                                             "max_logit_abs_difference": maximum_difference,
                                             "mean_logit_abs_difference": difference_sum / elements,
                                             "logit_elements_compared": elements,
                                             "verification_seconds": time.perf_counter() - verification_started,
                                             "batch_size_checks": batch_checks},
        "onnx_predictions_sha256": crnn.sha256_file(output / "onnx-test.jsonl"),
        "runtime_versions": {name: importlib.metadata.version(name) for name in
                             ("torch", "torchvision", "onnx", "onnxruntime", "numpy", "Pillow", "safetensors")},
        "runtime_provider": ocr.session.get_providers(), "runtime_threads": {"intra": 2, "inter": 1},
        "limitation": splitting.LIMITATION,
        "code_sha256": {filename: crnn.sha256_file(Path(__file__).with_name(filename)) for filename in
                        ("export_finetuned_crnn_onnx.py", "recognize_finetuned_crnn_onnx.py",
                         "evaluate_finetuned_crnn_text.py", "evaluate_crnn_text.py", "evaluate_public_text.py", "split_crnn_text.py")},
    }
    crnn.reference().atomic_json(output / "verification.json", result)
    if not passed or not result["checkpoint"]["unmodified_after_export"]:
        raise RuntimeError(f"ONNX verification failed; inspect {output / 'verification.json'}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--train-config", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--reference-predictions", type=Path, required=True)
    parser.add_argument("--reference-predictions-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--opset", type=int, choices=(17, 18), default=17)
    args = parser.parse_args()
    result = export(args)
    print(json.dumps({"status": result["status"], "graph": result["graph"], "parity": result["parity"]}))


if __name__ == "__main__":
    main()
