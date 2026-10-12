#!/usr/bin/env python3
"""Build the upload-only private input bundle for fresh fixed JEV/NLI inference."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

import run_fresh_baseline_final_subset_v1 as RUNTIME
from trial_scoped_condition_gliner_v1 import sha, write_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CODE = (
    "run_fresh_baseline_final_subset_v1.py",
    "kaggle_full_condition_choices_v1.py",
    "trial_full_scoped_condition_choices_v1.py",
    "trial_generic_model_jev_v1.py",
    "trial_generic_model_nli_v1.py",
    "trial_cpu_requirement_relations_v1.py",
    "trial_scoped_condition_nli_choice_v1.py",
    "trial_scoped_condition_nli_v1.py",
    "trial_scoped_condition_gliner_v1.py",
    "trial_generic_gliner_choice_v1.py",
    "try_gliner.py",
)
INPUTS = RUNTIME.SOURCE_FILES
DATASET_ID = "superbigzabuton/sku-final-test-inputs-200-20261013-v1"
KERNEL_ID = "superbigzabuton/sku-final-test-baseline-20261013-v1"
TIMEOUT_SECONDS = 7200


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scan_secrets(payload: Path):
    patterns = [re.compile(rb"KAGGLE_API_TOKEN\s*[:=]"), re.compile(rb"KAGGLE_KEY\s*[:=]"),
                re.compile(rb"AKIA[0-9A-Z]{16}"), re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")]
    for path in payload.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith((".env", "kaggle.json")) or path.suffix.lower() in {".safetensors", ".bin", ".pt", ".pth"}:
            raise ValueError("forbidden credential/weight file in upload bundle: " + str(path.relative_to(payload)))
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in patterns):
            raise ValueError("secret pattern found in upload bundle: " + str(path.relative_to(payload)))


def _notebook(dataset_id: str, kernel_id: str) -> dict:
    source = f'''# Fresh fixed JEV/NLI inference on the frozen 200-case input subset.
import hashlib, importlib.metadata, json, os, shutil, subprocess, sys, time, zipfile
from pathlib import Path

DATASET_ID = {dataset_id!r}
WORK = Path("/kaggle/working")
TMP = Path("/tmp/final-test-baseline")
TMP.mkdir(parents=True, exist_ok=True)
os.environ["HF_HOME"] = str(TMP / "hf")
os.environ["HUGGINGFACE_HUB_CACHE"] = str(TMP / "hf" / "hub")
os.environ["TRANSFORMERS_CACHE"] = str(TMP / "hf" / "transformers")
os.environ["PIP_CACHE_DIR"] = str(TMP / "pip-cache")

def stage(name, action):
    started = time.perf_counter()
    print(json.dumps({{"event":"stage_begin", "stage":name, "utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}), flush=True)
    result = action()
    print(json.dumps({{"event":"stage_end", "stage":name, "seconds":time.perf_counter()-started}}), flush=True)
    return result

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""): h.update(part)
    return h.hexdigest()

def find_bundle():
    roots = list(Path("/kaggle/input").iterdir())
    manifests = [p for root in roots if root.is_dir() for p in root.rglob("BUNDLE_MANIFEST.json") if p.is_file()]
    if manifests:
        if len(manifests) != 1: raise RuntimeError("expected one mounted bundle manifest")
        return manifests[0].parent
    for root in roots:
        if not root.is_dir(): continue
        for archive in root.rglob("*.zip"):
            with zipfile.ZipFile(archive) as z:
                if "BUNDLE_MANIFEST.json" not in z.namelist(): continue
                dest = TMP / "bundle"
                dest.mkdir(exist_ok=True)
                for member in z.infolist():
                    relative = Path(member.filename)
                    mode = (member.external_attr >> 16) & 0o170000
                    if relative.is_absolute() or ".." in relative.parts or mode == 0o120000:
                        raise RuntimeError("unsafe path or link in bundle ZIP")
                    target = dest / relative
                    if not target.resolve().is_relative_to(dest.resolve()): raise RuntimeError("ZIP path escape")
                    if member.is_dir(): target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as src, target.open("wb") as out: shutil.copyfileobj(src, out)
                return dest
    raise FileNotFoundError("private source bundle missing")

bundle = stage("discover_and_verify_bundle", find_bundle)
meta = json.loads((bundle / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
if meta.get("labels_included") is not False or meta.get("weights_included") is not False:
    raise RuntimeError("bundle privacy/model-weight flags differ")
files = meta.get("files")
if not isinstance(files, dict) or not files: raise RuntimeError("bundle inventory missing")
for relative, entry in files.items():
    rel = Path(relative); path = bundle / rel
    if rel.is_absolute() or ".." in rel.parts or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle.resolve()):
        raise RuntimeError("unsafe or missing input member: " + relative)
    if path.stat().st_size != entry["size_bytes"] or sha(path) != entry["sha256"]:
        raise RuntimeError("input member SHA/size mismatch: " + relative)
actual = {{str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file() and p.name != "BUNDLE_MANIFEST.json"}}
if actual != set(files): raise RuntimeError("bundle has unlisted files")
input_dir, code_dir = bundle / "input", bundle / "code"

def install_runtime():
    import torch
    if not torch.cuda.is_available(): raise RuntimeError("real CUDA required; CPU fallback is disabled")
    identity = (torch.__version__, torch.version.cuda)
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "transformers==5.17.0", "tokenizers==0.23.3", "safetensors", "huggingface_hub", "fugashi", "unidic-lite"], check=True)
    import torch as after
    if (after.__version__, after.version.cuda) != identity or not after.cuda.is_available():
        raise RuntimeError("runtime dependency install changed CUDA torch")
    names = ("torch", "transformers", "tokenizers", "safetensors", "huggingface_hub", "fugashi", "unidic-lite")
    return {{"packages":{{n:importlib.metadata.version(n) for n in names}}, "torch":identity,
            "gpu":torch.cuda.get_device_name(torch.cuda.current_device())}}

runtime = stage("install_cuda_runtime", install_runtime)
sys.path.insert(0, str(code_dir))
import kaggle_full_condition_choices_v1 as gpu
import run_fresh_baseline_final_subset_v1 as run_fresh
from trial_generic_model_jev_v1 import JEV

weights = TMP / "pinned-models"
def acquire_and_verify():
    roots = gpu.acquire_weights(weights, bundle / "provenance" / "jev-provenance.json", download=True)
    return {{kind:str(path) for kind,path in roots.items()}}
model_dirs = stage("acquire_exact_revision_hash_pinned_models", acquire_and_verify)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
output = WORK / "final-test-baseline"
if output.exists(): raise FileExistsError(output)
result = stage("fresh_full_subset_jev_nli_inference", lambda: run_fresh.run(
    input_dir=input_dir, output_dir=output, jev_model_dir=Path(model_dirs["jev"]),
    nli_model_dir=Path(model_dirs["nli"]), device="cuda"))
expected_requests = meta["input_counts"]["request_count"]
if result.get("request_count") != expected_requests: raise RuntimeError("prediction request denominator differs")
inner = json.loads((output / "model-run" / "summary.json").read_text(encoding="utf-8"))
if inner.get("device") != "cuda" or inner.get("precision") != "fp32" or inner.get("execution_mode") != "verified-cuda-no-fallback":
    raise RuntimeError("actual model execution was not verified CUDA FP32")
required = ["runtime-source-freeze.json", "summary.json", "model-run/freeze.json", "model-run/summary.json",
            "model-run/progress.json", "model-run/jev-predictions.jsonl", "model-run/nli-predictions.jsonl",
            "model-run/fusion-predictions.jsonl", "model-run/predictions.jsonl",
            "model-run/jev-classification-options.jsonl", "model-run/nli-classification-options.jsonl",
            "model-run/jev-unique-scores.jsonl", "model-run/nli-unique-scores.jsonl"]
missing = [name for name in required if not (output / name).is_file()]
if missing: raise RuntimeError("required output artifacts missing: " + str(missing))
receipt = {{"schema_version":"fresh-fixed-jev-nli-final-artifact-v1", "request_count":expected_requests,
    "labels_read":False, "production_adoption_allowed":False, "independent_test":True,
    "model_run_device":"cuda", "precision":"fp32", "expected_output_artifacts":required,
    "model_run_summary_sha256":sha(output / "model-run" / "summary.json"),
    "runtime_source_freeze_sha256":sha(output / "runtime-source-freeze.json"),
    "verified_model_pins":{{"jev":inner["verified_jev_pin"], "nli":inner["verified_nli_pin"]}},
    "kaggle_runtime":runtime}}
(output / "artifact-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\\n", encoding="utf-8")
archive = WORK / "final-test-baseline-results.zip"
with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for path in sorted(output.rglob("*")):
        if path.is_file(): z.write(path, str(path.relative_to(output)))
print(json.dumps({{"event":"complete", "request_count":expected_requests, "artifact":str(archive),
    "artifact_sha256":sha(archive), "device":"cuda", "precision":"fp32",
    "prediction_states":result["baseline_prediction_states"]}}), flush=True)
'''
    return {
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                   "source": source.splitlines(keepends=True)}],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3.10.12"}},
        "nbformat": 4, "nbformat_minor": 5,
    }


def build(input_dir: Path, output_dir: Path):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    requests, documents, bindings, input_manifest, _ = RUNTIME.load_adapter_v2_inputs(input_dir)
    if len(requests) != 5512 or len(documents) != 212 or sum(map(len, bindings.values())) != 8614:
        raise ValueError("frozen 200-case subset counts differ")
    payload = output_dir / "payload"
    for directory in (payload / "input", payload / "code", payload / "provenance"):
        directory.mkdir(parents=True, exist_ok=True)
    for name in INPUTS:
        shutil.copyfile(input_dir / name, payload / "input" / name)
    for name in CODE:
        shutil.copyfile(HERE / name, payload / "code" / name)
    provenance = ROOT / ".deps/sku-fixed-models/jev/provenance.json"
    if not provenance.is_file():
        raise FileNotFoundError(provenance)
    shutil.copyfile(provenance, payload / "provenance/jev-provenance.json")
    _scan_secrets(payload)
    input_hashes = {name: sha(payload / "input" / name) for name in INPUTS}
    code_hashes = {name: sha(payload / "code" / name) for name in CODE}
    if input_hashes["manifest.json"] != sha(input_dir / "manifest.json"):
        raise ValueError("subset manifest changed during packaging")
    file_manifest = {str(path.relative_to(payload)): {"size_bytes": path.stat().st_size, "sha256": sha(path)}
                     for path in sorted(payload.rglob("*")) if path.is_file()}
    manifest = {"schema_version": "fresh-fixed-jev-nli-final-test-bundle-v1",
                "labels_included": False, "labels_read": False, "weights_included": False,
                "synthetic_data": False, "independent_test": True,
                "production_adoption_allowed": False,
                "input_counts": {"case_count": 200, "product_count": 106, "task_count": 8614,
                                 "request_count": len(requests), "document_count": len(documents),
                                 "provided_model_input_ids": len({r["model_input_id"] for r in requests})},
                "model_pins": {"jev_model_id": "argos1111/modernbert-ja-310m-jev",
                               "jev_revision": "07cda23579443e7a33c0f474114279fa032340d6",
                               "nli_model_id": "0x3/bert-base-japanese-v3_nli-jsnli-jnli-jsick",
                               "nli_revision": "21df45469d02809dc430e3ac5f5d6f67b63aabf3",
                               "download_policy": "exact pinned revision and per-file SHA256 verification; no remote code"},
                "source_subset_manifest_sha256": sha(input_dir / "manifest.json"),
                "source_subset_flags": {key: input_manifest.get(key) for key in
                                         ("labels_read", "model_inference_run", "independent_test")},
                "input_sha256": input_hashes, "code_sha256": code_hashes,
                "private_dataset": True, "files": file_manifest,
                "expected_artifact_schema": {"prediction_rows": len(requests),
                    "required": ["summary.json", "runtime-source-freeze.json", "model-run/freeze.json",
                        "model-run/summary.json", "model-run/progress.json", "model-run/jev-predictions.jsonl",
                        "model-run/nli-predictions.jsonl", "model-run/fusion-predictions.jsonl",
                        "model-run/predictions.jsonl", "model-run/jev-classification-options.jsonl",
                        "model-run/nli-classification-options.jsonl", "model-run/jev-unique-scores.jsonl",
                        "model-run/nli-unique-scores.jsonl"]}}
    write_json(payload / "BUNDLE_MANIFEST.json", manifest)

    upload = output_dir / "dataset-upload"
    upload.mkdir()
    archive = upload / "final-test-baseline-input.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                stream.write(path, str(path.relative_to(payload)))
    metadata = {"id": DATASET_ID, "title": DATASET_ID.rsplit("/", 1)[-1],
                "licenses": [{"name": "other"}],
                "description": "Private label-free 200-case baseline inputs and allowlisted source code."}
    write_json(upload / "dataset-metadata.json", metadata)

    notebook_dir = output_dir / "notebook-upload"
    notebook_dir.mkdir()
    notebook = _notebook(DATASET_ID, KERNEL_ID)
    notebook_path = notebook_dir / "final_test_baseline.ipynb"
    write_json(notebook_path, notebook)
    kernel_metadata = {"id": KERNEL_ID, "title": KERNEL_ID.rsplit("/", 1)[-1],
                       "code_file": notebook_path.name, "language": "python", "kernel_type": "notebook",
                       "is_private": True, "enable_gpu": True, "enable_tpu": False,
                       "enable_internet": True, "dataset_sources": [DATASET_ID],
                       "competition_sources": [], "kernel_sources": []}
    write_json(notebook_dir / "kernel-metadata.json", kernel_metadata)
    write_json(notebook_dir / "training-params.json", {"timeout_seconds": TIMEOUT_SECONDS})

    _scan_secrets(upload)
    _scan_secrets(notebook_dir)
    with zipfile.ZipFile(archive) as stream:
        if stream.testzip() is not None:
            raise ValueError("dataset archive failed integrity check")
        if set(stream.namelist()) != set(file_manifest) | {"BUNDLE_MANIFEST.json"}:
            raise ValueError("dataset archive member inventory differs")
    receipt = {"schema_version": "fresh-final-test-baseline-package-receipt-v1",
               "dataset_id": DATASET_ID, "kernel_id": KERNEL_ID, "dataset_version_reused": None,
               "old_model_dataset_reuse": {"ref": "superbigzabuton/sku-mixed83-fp32-20261012-v1",
                   "version": 1, "verified_weights_present": False,
                   "finding": "previous package inventory contains inputs/code only; prior kernel downloaded pinned models to /tmp"},
               "input_counts": manifest["input_counts"], "dataset_archive_sha256": sha(archive),
               "dataset_archive_bytes": archive.stat().st_size,
               "bundle_manifest_sha256": sha(payload / "BUNDLE_MANIFEST.json"),
               "notebook_code_payload_sha256": sha(notebook_path), "timeout_seconds": TIMEOUT_SECONDS,
               "labels_included": False, "weights_included": False, "secret_scan_passed": True,
               "upload_performed": False, "submission_performed": False,
               "artifact_contract": manifest["expected_artifact_schema"]}
    write_json(output_dir / "package-receipt.json", receipt)
    print(json.dumps({"dataset_bytes": archive.stat().st_size,
                      "bundle_manifest_sha256": receipt["bundle_manifest_sha256"],
                      "input_counts": manifest["input_counts"],
                      "labels_included": False, "weights_included": False}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
