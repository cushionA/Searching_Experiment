#!/usr/bin/env python3
"""Prepare a source-only paired-text Kaggle package; never submits it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import kaggle_nextdata_table_alloption_v1 as TASK

BOOTSTRAP = r'''
import hashlib, json, pathlib, shutil, subprocess, sys, tempfile, time, zipfile

MANIFEST_SHA = "__MANIFEST_SHA__"

def sha(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    started = time.perf_counter()
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12+ required")
    print(json.dumps({"stage": "dependencies-start", "python": sys.version}), flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "transformers==5.17.0",
                    "tokenizers==0.23.3", "fugashi==1.5.2", "unidic-lite==1.0.8",
                    "huggingface-hub==1.33.0", "safetensors==0.8.0"], check=True)
    directory = pathlib.Path(tempfile.mkdtemp(prefix="sku-nextdata-table-"))
    bundle = directory / "payload"
    print(json.dumps({"stage": "input-start"}), flush=True)
    manifests = list(pathlib.Path("/kaggle/input").rglob("BUNDLE_MANIFEST.json"))
    if len(manifests) == 1:
        if sha(manifests[0]) != MANIFEST_SHA:
            raise ValueError("mounted manifest SHA mismatch")
        inventory = json.loads(manifests[0].read_text())
        bundle.mkdir()
        for name in [*inventory["files"], "BUNDLE_MANIFEST.json"]:
            relative = pathlib.Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe input path")
            source = manifests[0].parent / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError("ordinary input file required")
            target = bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    else:
        archives = list(pathlib.Path("/kaggle/input").rglob("sku-nextdata-table-input.zip"))
        if len(archives) != 1:
            raise ValueError("exactly one source-only input archive required")
        with zipfile.ZipFile(archives[0]) as archive:
            for member in archive.infolist():
                path = pathlib.Path(member.filename)
                if path.is_absolute() or ".." in path.parts or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("unsafe archive member")
            if archive.testzip() is not None:
                raise ValueError("input CRC failure")
            archive.extractall(bundle)
    if sha(bundle / "BUNDLE_MANIFEST.json") != MANIFEST_SHA:
        raise ValueError("input manifest SHA mismatch")
    sys.path.insert(0, str(bundle / "code"))
    import kaggle_nextdata_table_alloption_v1 as task
    verified = task.GPU.verify_bundle(bundle)
    print(json.dumps({"stage": "input-ready", "file_count": verified["file_count"], "manifest_sha256": MANIFEST_SHA}), flush=True)
    output = pathlib.Path("/kaggle/working/sku-nextdata-table-results")
    subprocess.run([sys.executable, "-B", str(bundle / "code/kaggle_nextdata_table_alloption_v1.py"),
                    "--bundle-dir", str(bundle), "--input-file", str(bundle / "input/paired-public-inputs.jsonl"),
                    "--output-dir", str(output), "--weights-dir", str(directory / "models"),
                    "--jev-provenance", str(bundle / "provenance/jev-provenance.json")], check=True)
    receipt = json.loads((output / "COMPLETION.json").read_text())
    artifact = pathlib.Path("/kaggle/working/sku-nextdata-table-results.zip")
    with zipfile.ZipFile(artifact, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(output)))
    with zipfile.ZipFile(artifact) as archive:
        if archive.testzip() is not None:
            raise ValueError("result CRC failure")
    result = {"schema_version": "sku-nextdata-table-artifact-v1", "artifact_sha256": sha(artifact),
              "artifact_bytes": artifact.stat().st_size, "bundle_manifest_sha256": MANIFEST_SHA,
              "input_sha256": receipt["input_sha256"], "totals": receipt["totals"],
              "gpu_runtime": receipt["gpu_runtime"], "model_pins": receipt["model_pins"],
              "notebook_elapsed_seconds": time.perf_counter() - started,
              "notebook_elapsed_seconds_scope": "notebook start through dependency install, input verification, model download/loading, complete inference and result archive; excludes queue and upload",
              "production_adoption_allowed": False}
    pathlib.Path("/kaggle/working/sku-nextdata-table-result-receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    shutil.rmtree(output)
    print(json.dumps({"stage": "complete", "artifact_bytes": result["artifact_bytes"], "gpu": receipt["gpu_runtime"]["gpu_name"]}), flush=True)

main()
'''


def prepare(input_file, output_dir, owner, slug, provenance, root_freeze, sourceproof):
    if json.loads(root_freeze.read_text(encoding="utf-8")).get("root_verified") is not True:
        raise ValueError("ROOT independently verified population freeze required before packaging")
    rows = TASK.load_rows(input_file, root_freeze, sourceproof)
    output_dir.mkdir(parents=True, exist_ok=False)
    payload = output_dir / "payload"
    for name in ("input", "code", "provenance"):
        (payload / name).mkdir(parents=True)
    shutil.copyfile(input_file, payload / "input/paired-public-inputs.jsonl")
    names = ("kaggle_nextdata_table_alloption_v1.py", *TASK.GPU.CODE_NAMES)
    for name in dict.fromkeys(names):
        shutil.copyfile(Path(__file__).parent / name, payload / "code" / name)
    shutil.copyfile(provenance, payload / "provenance/jev-provenance.json")
    shutil.copyfile(root_freeze, payload / "provenance/root-population-freeze.json")
    shutil.copyfile(sourceproof, payload / "provenance/description-sourceproof.json")
    TASK.write_json(payload / "experiment-policy.json", TASK.POLICY)
    inventory = {str(p.relative_to(payload)): {"size_bytes": p.stat().st_size, "sha256": TASK.sha(p)}
                 for p in sorted(payload.rglob("*")) if p.is_file()}
    manifest = {"schema_version": "sku-nextdata-table-private-input-v1", "labels_included": False,
                "development_only": True, "independent_test": False, "production_adoption_allowed": False,
                "pair_count": len(rows), "paired_input_count": 2 * len(rows),
                "option_prediction_count": 2 * sum(len(row["option_values"]) for row in rows),
                "public_input_sha256": TASK.sha(input_file), "root_population_freeze_sha256": TASK.sha(root_freeze),
                "description_sourceproof_sha256": TASK.sha(sourceproof), "files": inventory}
    TASK.write_json(payload / "BUNDLE_MANIFEST.json", manifest)
    verified = TASK.GPU.verify_bundle(payload)
    upload = output_dir / "dataset-upload"
    upload.mkdir()
    archive_path = upload / "sku-nextdata-table-input.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(payload)))
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("private archive CRC failure")
    dataset_slug = slug + "-inputs"
    TASK.write_json(upload / "dataset-metadata.json", {"id": f"{owner}/{dataset_slug}", "title": dataset_slug,
                    "licenses": [{"name": "other"}]})
    notebook = output_dir / "notebook-upload"
    notebook.mkdir()
    source = BOOTSTRAP.replace("__MANIFEST_SHA__", verified["manifest_sha256"])
    cells = [{"id": "sku-nextdata-table-bootstrap-v1", "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
              "source": source.splitlines(keepends=True)}]
    notebook_metadata = {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                         "language_info": {"name": "python"}}
    TASK.write_json(notebook / "nextdata_table.ipynb", {"cells": cells, "metadata": notebook_metadata, "nbformat": 4, "nbformat_minor": 5})
    TASK.write_json(notebook / "kernel-metadata.json", {"id": f"{owner}/{slug}", "title": slug,
                    "code_file": "nextdata_table.ipynb", "language": "python", "kernel_type": "notebook",
                    "is_private": True, "enable_gpu": True, "enable_tpu": False, "enable_internet": True,
                    "dataset_sources": [f"{owner}/{dataset_slug}"], "competition_sources": [], "kernel_sources": []})
    TASK.write_json(notebook / "training-params.json", {"timeout_seconds": 7200})
    receipt = {"schema_version": "sku-nextdata-table-kaggle-local-package-v1", "submitted": False,
               "input_manifest_sha256": verified["manifest_sha256"], "archive_sha256": TASK.sha(archive_path),
               "archive_bytes": archive_path.stat().st_size, "dataset_ref": f"{owner}/{dataset_slug}",
               "kernel_ref": f"{owner}/{slug}", "notebook_sha256": TASK.sha(notebook / "nextdata_table.ipynb"),
               "pair_count": len(rows), "option_prediction_count": manifest["option_prediction_count"]}
    TASK.write_json(output_dir / "LOCAL_PACKAGE_RECEIPT.json", receipt)
    print(TASK.canonical(receipt))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--owner", default="superbigzabuton")
    parser.add_argument("--slug", default="sku-nextdata-table-fp32-20261013-v1")
    parser.add_argument("--jev-provenance", type=Path, required=True)
    parser.add_argument("--root-population-freeze", type=Path, required=True)
    parser.add_argument("--description-sourceproof", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.input_file, args.output_dir, args.owner, args.slug, args.jev_provenance,
            args.root_population_freeze, args.description_sourceproof)


if __name__ == "__main__":
    main()
