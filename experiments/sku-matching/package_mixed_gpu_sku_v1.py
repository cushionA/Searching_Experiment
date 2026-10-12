#!/usr/bin/env python3
"""Build a private, source-only Dataset and a separate GPU notebook upload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import kaggle_product_condition_gpu_v1 as RUNNER
import trial_scoped_html_evidence_v1 as V1

HERE = Path(__file__).resolve().parent
SOURCE_NAMES = ("manifest.json", *V1.SOURCE_NAMES)
CODE_NAMES = RUNNER.CODE_NAMES
RERANKER_MANIFEST = "manifests/reranker.json"
VERSION = "mixed-gpu-sku-package-v1"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_sources(input_dir: Path, contract_path: Path) -> tuple[dict, dict]:
    manifest_path = input_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("synthetic") is True or manifest.get("labels_read") is not False or manifest.get("labels_in_model_inputs") is not False:
        raise ValueError("prepared source must be label-free and must not declare synthetic data")
    if manifest.get("independent_test") is not False:
        raise ValueError("prepared source must not be an independent test cohort")
    hashes = manifest.get("output_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("prepared manifest lacks output hashes")
    for name in SOURCE_NAMES[1:]:
        path = input_dir / name
        if not path.is_file() or sha(path) != hashes.get(name):
            raise ValueError("prepared source SHA256 mismatch: " + name)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("basis") != "user_asserted" or not isinstance(contract.get("eligible_forward_request_ids"), list):
        raise ValueError("sharing contract is missing its explicit eligibility set")
    if contract.get("sold_out_rows_excluded_upstream") is not False and manifest.get("schema_version") == "mixed-real-gpu-sku-inputs-v1":
        raise ValueError("mixed input contract must report captured sold-out rows as retained")
    if manifest.get("schema_version") == "mixed-real-gpu-sku-inputs-v1":
        if manifest.get("row_policy_audit") != contract.get("row_policy_audit"):
            raise ValueError("mixed eligibility contract row policy differs from prepared manifest")
    loaded = RUNNER.development_loader()(input_dir)
    request_rows = loaded[0]
    RUNNER.TASK.SHARING._validate_contract(contract, {row["request_id"]: row for row in request_rows})
    RUNNER.TASK.verify_contract_sources(contract, input_dir)
    return manifest, contract


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _notebook_source(dataset_id: str, include_parity44: bool) -> str:
    return f'''# Private mixed real SKU GPU replay. Dataset: {dataset_id}
import hashlib, importlib.metadata, json, os, shutil, subprocess, sys, time, zipfile
from pathlib import Path

DATASET_ID = {dataset_id!r}
WORK = Path("/kaggle/working")
TMP = Path("/tmp/mixed-gpu-sku")
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
    with open(path, "rb") as f:
        for part in iter(lambda:f.read(1024*1024), b""): h.update(part)
    return h.hexdigest()

def find_bundle():
    roots = list(Path("/kaggle/input").iterdir())
    manifests = [p for root in roots if root.is_dir() for p in root.rglob("BUNDLE_MANIFEST.json") if p.is_file()]
    if manifests:
        if len(manifests) != 1: raise RuntimeError("expected exactly one mounted bundle manifest")
        return manifests[0].parent
    archives = [p for root in roots for p in (root.rglob("*.zip") if root.is_dir() else [])]
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            if "BUNDLE_MANIFEST.json" in z.namelist():
                dest = TMP / "bundle"
                dest.mkdir(exist_ok=True)
                for member in z.infolist():
                    relative = Path(member.filename)
                    mode = (member.external_attr >> 16) & 0o170000
                    if relative.is_absolute() or ".." in relative.parts or mode == 0o120000:
                        raise RuntimeError("unsafe path or link in bundle ZIP")
                    target = dest / relative
                    if not target.resolve().is_relative_to(dest.resolve()):
                        raise RuntimeError("bundle ZIP member escapes extraction root")
                    if member.is_dir(): target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as source, target.open("wb") as output: shutil.copyfileobj(source, output)
                return dest
    raise FileNotFoundError("source bundle ZIP or extracted BUNDLE_MANIFEST.json missing")

bundle = stage("discover_and_verify_bundle", find_bundle)
code_dir = bundle / "code"
input_dir = bundle / "input"
contract_path = bundle / "eligible-requests.json"
metadata = json.loads((bundle / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
if metadata.get("labels_included") is not False or metadata.get("synthetic_data") is not False:
    raise RuntimeError("bundle privacy/data flags are invalid")
if not isinstance(metadata.get("files"), dict) or not metadata["files"]:
    raise RuntimeError("bundle inventory is missing")
for rel, entry in metadata["files"].items():
    path = bundle / rel
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle.resolve()) or path.stat().st_size != entry["size_bytes"] or sha(path) != entry["sha256"]:
        raise RuntimeError("bundle member size/SHA mismatch: " + rel)
actual_files = {{str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file() and p.name != "BUNDLE_MANIFEST.json"}}
if actual_files != set(metadata["files"]): raise RuntimeError("bundle inventory differs from mounted files")

# Keep Kaggle's CUDA-enabled torch build. Install only user-space runtime packages.
def install_runtime():
    import torch
    if not torch.cuda.is_available(): raise RuntimeError("Kaggle did not provide CUDA; CPU fallback is disabled")
    before = (torch.__version__, torch.version.cuda)
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "transformers==5.17.0", "tokenizers==0.23.3", "onnxruntime==1.23.2",
        "fugashi", "unidic-lite", "safetensors", "huggingface_hub"], check=True)
    import torch as torch_after
    if (torch_after.__version__, torch_after.version.cuda) != before or not torch_after.cuda.is_available():
        raise RuntimeError("runtime package installation changed the Kaggle CUDA torch build")
    names = ("torch", "transformers", "tokenizers", "onnxruntime", "fugashi", "unidic-lite", "safetensors", "huggingface_hub")
    return {{name: importlib.metadata.version(name) for name in names}}, before
packages, torch_identity = stage("install_pinned_runtime", install_runtime)
print(json.dumps({{"event":"runtime_versions", "packages":packages, "torch_identity":torch_identity}}), flush=True)

# Import only after package installation; the bundle has already passed its full file/SHA scan.
sys.path.insert(0, str(code_dir))
import kaggle_product_condition_gpu_v1 as runner
verified = runner.WEIGHTS.verify_bundle(bundle)

# /kaggle/working contains only the compact runner result tree. Model files/cache stay under /tmp.
weights_dir = TMP / "weights"
def acquire(): return runner.acquire_models(weights_dir)
roots = stage("download_and_verify_pinned_models", acquire)
scorer = runner.CudaScorer(roots["jev"], roots["nli"])
main_result_dir = WORK / "mixed83"
if main_result_dir.exists(): raise FileExistsError("refusing to overwrite mixed83 results")
def run_main():
    return runner.run(input_dir, contract_path, main_result_dir, bundle_dir=bundle,
        refresh=True, baseline_variant="fusion", scorer=scorer,
        reranker_model_dir=roots["reranker"], jev_model_dir=roots["jev"], nli_model_dir=roots["nli"])
summary = stage("mixed83_gpu_inference", run_main)
parity_summary = None
if {include_parity44!r}:
    parity_input = bundle / "parity44" / "input"
    parity_contract = bundle / "parity44" / "eligible-requests.json"
    parity_result_dir = WORK / "parity44"
    if parity_result_dir.exists(): raise FileExistsError("refusing to overwrite parity44 results")
    parity_scorer = runner.CudaScorer(roots["jev"], roots["nli"])
    def run_parity44():
        return runner.run(parity_input, parity_contract, parity_result_dir, bundle_dir=bundle,
            refresh=False, scorer=parity_scorer, reranker_model_dir=roots["reranker"],
            jev_model_dir=roots["jev"], nli_model_dir=roots["nli"])
    parity_summary = stage("parity44_gpu_inference", run_parity44)
print(json.dumps({{"event":"complete", "request_count":summary["request_count"],
    "prediction_states":summary["prediction_states"], "result_dir":str(main_result_dir),
    "mixed83_seconds":summary["total_gpu_job_seconds"],
    "parity44_seconds":parity_summary["total_gpu_job_seconds"] if parity_summary else None,
    "parity44_summary":str(WORK / "parity44") if parity_summary else None,
    "bundle_manifest_sha256":verified["manifest_sha256"], "packages":packages}}, ensure_ascii=False), flush=True)
'''


def package(input_dir: Path, contract_path: Path, output_dir: Path, *, dataset_id: str, notebook_id: str,
            parity_input_dir: Path | None = None, parity_contract_path: Path | None = None) -> dict:
    input_dir, contract_path, output_dir = Path(input_dir), Path(contract_path), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if "/" not in dataset_id or "/" not in notebook_id:
        raise ValueError("dataset-id and notebook-id must be Kaggle owner/slug refs")
    if dataset_id.split("/")[0] != notebook_id.split("/")[0]:
        raise ValueError("Dataset owner and notebook owner must match")
    source_manifest, contract = validate_sources(input_dir, contract_path)
    if (parity_input_dir is None) != (parity_contract_path is None):
        raise ValueError("parity input and contract must be supplied together")
    parity_manifest = None
    if parity_input_dir is not None:
        parity_manifest, _ = validate_sources(Path(parity_input_dir), Path(parity_contract_path))
    output_dir.mkdir(parents=True)
    payload = output_dir / "payload"
    (payload / "input").mkdir(parents=True)
    for name in SOURCE_NAMES:
        _copy_file(input_dir / name, payload / "input" / name)
    _copy_file(contract_path, payload / "eligible-requests.json")
    if parity_input_dir is not None:
        parity_dir = payload / "parity44" / "input"
        for name in SOURCE_NAMES:
            _copy_file(Path(parity_input_dir) / name, parity_dir / name)
        _copy_file(Path(parity_contract_path), payload / "parity44" / "eligible-requests.json")
    for name in CODE_NAMES:
        source = HERE / name
        if not source.is_file():
            raise FileNotFoundError("runner CODE_NAMES source missing: " + name)
        _copy_file(source, payload / "code" / name)
    _copy_file(HERE / RERANKER_MANIFEST, payload / "code" / RERANKER_MANIFEST)
    files = {str(path.relative_to(payload)): {"size_bytes": path.stat().st_size, "sha256": sha(path)}
             for path in sorted(payload.rglob("*")) if path.is_file()}
    bundle_manifest = {
        "schema_version": VERSION, "private_dataset": True, "labels_included": False,
        "labels_read": False, "synthetic_data": False, "independent_test": False,
        "development_only": True, "gpu": True, "tpu": False, "internet": True,
        "files": files,
        "source": {"prepared_schema_version": source_manifest["schema_version"],
                   "prepared_input_sha256": {name: sha(input_dir / name) for name in SOURCE_NAMES},
                   "eligible_contract_sha256": sha(contract_path),
                   "eligible_request_count": len(contract["eligible_forward_request_ids"]),
                   "parity44": ({"prepared_schema_version": parity_manifest["schema_version"],
                       "prepared_input_sha256": {name: sha(Path(parity_input_dir) / name) for name in SOURCE_NAMES},
                       "eligible_contract_sha256": sha(Path(parity_contract_path))} if parity_manifest else None)},
        "model_policy": "Use the existing pinned JEV/NLI FP32 CUDA and INT8 ONNX CPU model files; download to /tmp at runtime.",
    }
    write_json(payload / "BUNDLE_MANIFEST.json", bundle_manifest)
    dataset_upload = output_dir / "dataset-upload"
    dataset_upload.mkdir()
    archive = dataset_upload / "mixed-gpu-sku-input.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(payload).as_posix())
    write_json(dataset_upload / "dataset-metadata.json", {
        "id": dataset_id, "title": dataset_id.split("/")[-1], "licenses": [{"name": "other"}],
        "description": "Private, label-free source inputs and frozen inference code for a development GPU replay."})
    notebook_upload = output_dir / "notebook-upload"
    notebook_upload.mkdir()
    notebook_text = _notebook_source(dataset_id, parity_input_dir is not None)
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {}, "source": ["Private mixed real SKU development GPU replay. No labels or scoring code are mounted."]},
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
         "source": notebook_text.splitlines(keepends=True)}]}
    write_json(notebook_upload / "mixed_gpu_sku.ipynb", notebook)
    write_json(notebook_upload / "kernel-metadata.json", {
        "id": notebook_id, "title": notebook_id.split("/")[-1], "code_file": "mixed_gpu_sku.ipynb",
        "language": "python", "kernel_type": "notebook", "is_private": True,
        "enable_gpu": True, "enable_tpu": False, "enable_internet": True,
        "dataset_sources": [dataset_id], "competition_sources": [], "kernel_sources": []})
    write_json(notebook_upload / "training-params.json", {"timeout_seconds": 3600})
    receipt = {"schema_version": VERSION, "dataset_id": dataset_id, "notebook_id": notebook_id,
        "archive": str(archive.relative_to(output_dir)), "archive_sha256": sha(archive),
        "archive_bytes": archive.stat().st_size,
        "bundle_manifest_sha256": sha(payload / "BUNDLE_MANIFEST.json"),
        "file_count": len(files), "notebook_bytes": (notebook_upload / "mixed_gpu_sku.ipynb").stat().st_size,
        "labels_included": False, "synthetic_data": False, "private": True,
        "source_file_names": list(SOURCE_NAMES), "code_names": list(CODE_NAMES)}
    write_json(output_dir / "package-receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--eligible-requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--notebook-id", required=True)
    parser.add_argument("--parity-input-dir", type=Path)
    parser.add_argument("--parity-contract", type=Path)
    args = parser.parse_args(argv)
    result = package(args.input_dir, args.eligible_requests, args.output_dir,
                     dataset_id=args.dataset_id, notebook_id=args.notebook_id,
                     parity_input_dir=args.parity_input_dir, parity_contract_path=args.parity_contract)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
