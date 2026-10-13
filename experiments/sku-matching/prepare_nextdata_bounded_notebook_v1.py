#!/usr/bin/env python3
"""Build one bounded notebook and its SAME-private-Dataset payload locally.

No network, authentication, weights, model execution, Kaggle submission or
monitoring. Reviewed source files, original inputs and prior first scores are
copied without alteration. Only the explicit public worker inventory is sent.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import zipfile

import nextdata_exact_cache_resume_v1 as R

DATASET_REF = "superbigzabuton/sku-nextdata-table-fp32-20261013-v1-inputs"
ARCHIVE_NAME = "sku-nextdata-exact-resume-payload.zip"
EXPORT_NAME = "RESUME_EXPORT_MANIFEST.json"
STATE_FILES = {"index.sqlite", "PREFLIGHT_RECEIPT.json", "pair-token-coverage.jsonl", "worker-plan.json",
               "nextdata_exact_cache_resume_v1.py", "kaggle_nextdata_exact_resume_v1.py"}
REVIEWED_CODE = {
    "experiments/sku-matching/kaggle_nextdata_exact_resume_v1.py": "a6e6ac7cd13c806c21a43715bbdf51f0c6d1846f602de6a8028a6df25144fbf3",
    "experiments/sku-matching/nextdata_exact_cache_resume_v1.py": "10485cfe97d7cda5cea585e431a253ad8c8f32a59268af2244975ecb08aca377",
    "experiments/sku-matching/prepare_nextdata_exact_resume_v1.py": "8bcdb6e62aacd84ee2e928da10db3fed6f7a42ab050ca079641a35db6653a33a",
    "tests/test_lab_sku_nextdata_exact_resume.py": "c2888a7c9bd1424909d8820d411e57978d9cd9c8cae441b6ca957662fed6f69a",
}
DB_COLUMNS = {
    "metadata": ("key", "value"),
    "requests": ("kind", "exact_key", "legacy_key", "text_sha", "locator", "ordinal"),
    "occurrences": ("kind", "pair_ref", "variant", "option_index", "chunk_ref", "exact_key", "ordinal"),
    "saved": ("kind", "pair_ref", "variant", "option_index", "chunk_ref", "exact_key", "score", "origin"),
    "cache": ("kind", "exact_key", "score", "origin"),
}
COMMON_SCORE_FIELDS = {"pair_ref", "variant", "option_index", "chunk_ref"}
SCORE_FIELDS = {
    "jev": {"state", "status", "logits", "probabilities", "token_counts", "cache_key", "error_type"},
    "nli": {"state", "probabilities", "predicted_class", "predicted_confidence",
            "token_count_untruncated", "cache_key", "error_type"},
}

BOOTSTRAP = r'''
import hashlib, json, pathlib, shutil, stat, subprocess, sys, tempfile, time, zipfile

CONFIG = json.loads(__CONFIG_REPR__)

def sha(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def safe_member(member):
    name = pathlib.PurePosixPath(member.filename)
    mode = (member.external_attr >> 16) & 0o170000
    if name.is_absolute() or ".." in name.parts or "\\" in member.filename or member.is_dir() or mode == stat.S_IFLNK:
        raise ValueError("unsafe archive member")

def verify_export(root):
    marker = root / "RESUME_EXPORT_MANIFEST.json"
    if sha(marker) != CONFIG["export_manifest_sha256"]:
        raise ValueError("mounted export manifest SHA differs")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    expected = {"dataset_ref": CONFIG["dataset_ref"], "dataset_version": CONFIG["dataset_version"],
                "kernel_ref": CONFIG["kernel_ref"], "kernel_version": CONFIG["kernel_version"],
                "shard_id": CONFIG["shard_id"], "max_seconds": 6000, "labels_included": False,
                "private_source_joins_included": False, "raw_pages_included": False,
                "credentials_included": False, "planning_preview": False}
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError("mounted export intent differs from frozen notebook")
    files = manifest["files"]
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p != marker}
    if actual != set(files):
        raise ValueError("mounted export file inventory differs")
    for name, pin in files.items():
        relative = pathlib.PurePosixPath(name)
        path = root / name
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or path.is_symlink() or not path.is_file():
            raise ValueError("ordinary contained export files required")
        if path.stat().st_size != pin["size_bytes"] or sha(path) != pin["sha256"]:
            raise ValueError("mounted file size/SHA differs: " + name)
    return manifest

def stage_input():
    target = pathlib.Path(tempfile.mkdtemp(prefix="sku-exact-resume-")) / "payload"
    input_root = pathlib.Path("/kaggle/input")
    markers = list(input_root.rglob("RESUME_EXPORT_MANIFEST.json"))
    archives = list(input_root.rglob("sku-nextdata-exact-resume-payload.zip"))
    if len(markers) == 1 and not archives:
        source = markers[0].parent
        manifest = verify_export(source)
        target.mkdir()
        for name in [*manifest["files"], "RESUME_EXPORT_MANIFEST.json"]:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
    elif len(archives) == 1 and not markers:
        if sha(archives[0]) != CONFIG["archive_sha256"] or archives[0].stat().st_size != CONFIG["archive_bytes"]:
            raise ValueError("mounted archive size/SHA differs")
        with zipfile.ZipFile(archives[0]) as archive:
            names = [member.filename for member in archive.infolist()]
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive members")
            for member in archive.infolist():
                safe_member(member)
            if archive.testzip() is not None:
                raise ValueError("mounted archive CRC failure")
            target.mkdir()
            archive.extractall(target)
    else:
        raise ValueError("ambiguous/missing source-only export mount; no alternate source selected")
    manifest = verify_export(target)
    return target, manifest

def preserve_output(output, returncode, started):
    paths = sorted(p for p in output.glob("*") if p.is_file()) if output.exists() else []
    allowed = {"EXECUTION_FREEZE.json", "new-first-scores.jsonl", "COMPLETION.json"}
    if any(p.name not in allowed or p.is_symlink() for p in paths):
        raise ValueError("unexpected numeric output file")
    archive_path = pathlib.Path("/kaggle/working/sku-exact-resume-results.zip")
    if paths:
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths:
                archive.write(path, path.name)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("numeric result CRC failure")
    receipt = {"schema_version": "sku-exact-resume-notebook-output-v1", "kernel_ref": CONFIG["kernel_ref"],
               "kernel_version": CONFIG["kernel_version"], "dataset_ref": CONFIG["dataset_ref"],
               "dataset_version": CONFIG["dataset_version"], "shard_id": CONFIG["shard_id"],
               "max_seconds": 6000, "returncode": returncode, "export_manifest_sha256": CONFIG["export_manifest_sha256"],
               "original_bundle_manifest_sha256": CONFIG["original_bundle_manifest_sha256"],
               "state_bundle_manifest_sha256": CONFIG["state_bundle_manifest_sha256"],
               "files": {p.name: {"size_bytes": p.stat().st_size, "sha256": sha(p)} for p in paths},
               "result_archive_sha256": sha(archive_path) if paths else None,
               "result_archive_bytes": archive_path.stat().st_size if paths else None,
               "notebook_elapsed_seconds": time.perf_counter() - started,
               "original_raw_numeric_files_retained": True, "production_adoption_allowed": False}
    pathlib.Path("/kaggle/working/sku-exact-resume-result-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt

def main():
    started = time.perf_counter()
    if CONFIG["planning_preview"]:
        raise RuntimeError("local planning preview cannot execute or repeat original v2 first scores")
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12+ required")
    print(json.dumps({"stage": "input-start", "kernel_ref": CONFIG["kernel_ref"], "kernel_version": CONFIG["kernel_version"],
                      "dataset_ref": CONFIG["dataset_ref"], "dataset_version": CONFIG["dataset_version"], "shard_id": CONFIG["shard_id"]}), flush=True)
    payload, manifest = stage_input()
    print(json.dumps({"stage": "input-files-verified", "file_count": len(manifest["files"]),
                      "export_manifest_sha256": CONFIG["export_manifest_sha256"]}), flush=True)
    print(json.dumps({"stage": "dependencies-start", "python": sys.version}), flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "transformers==5.17.0",
                    "tokenizers==0.23.3", "fugashi==1.5.2", "unidic-lite==1.0.8",
                    "huggingface-hub==1.33.0", "safetensors==0.8.0"], check=True)
    original, state = payload / "original", payload / "worker-state"
    sys.path[:0] = [str(state), str(original / "code")]
    import nextdata_exact_cache_resume_v1 as resume
    old_check = resume.GPU.verify_bundle(original)
    new_check = resume.GPU.verify_bundle(state)
    if (old_check["manifest_sha256"], new_check["manifest_sha256"]) != (
            CONFIG["original_bundle_manifest_sha256"], CONFIG["state_bundle_manifest_sha256"]):
        raise ValueError("original/state source pins differ")
    resume.validate_loaded_original_code()
    database = resume.connect(state / "index.sqlite")
    try:
        resume.verify_resume_ready(database)
    finally:
        database.close()
    print(json.dumps({"stage": "input-ready", "original_manifest_sha256": old_check["manifest_sha256"],
                      "state_manifest_sha256": new_check["manifest_sha256"]}), flush=True)
    output = pathlib.Path("/kaggle/working/sku-exact-resume-results")
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(state) + ":" + str(original / "code")
    command = [sys.executable, "-B", str(state / "kaggle_nextdata_exact_resume_v1.py"),
               "--original-bundle-dir", str(original), "--state-bundle-dir", str(state),
               "--output-dir", str(output), "--weights-dir", str(payload.parent / "models"),
               "--shard-id", str(CONFIG["shard_id"]), "--remote-ref", CONFIG["kernel_ref"],
               "--version", str(CONFIG["kernel_version"]), "--max-seconds", "6000"]
    returncode = None
    try:
        returncode = subprocess.run(command, env=env, check=False).returncode
    finally:
        receipt = preserve_output(output, returncode, started)
        print(json.dumps({"stage": "output-preserved", "returncode": returncode,
                          "file_count": len(receipt["files"]), "result_archive_sha256": receipt["result_archive_sha256"]}), flush=True)
    if returncode != 0:
        raise RuntimeError("bounded executor failed; preserved first-score files must be recovered without repair or semantic retry")
    print(json.dumps({"stage": "complete", "shard_id": CONFIG["shard_id"]}), flush=True)

main()
'''


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_reviewed_code():
    root = Path(__file__).resolve().parents[2]
    for name, expected in REVIEWED_CODE.items():
        path = root / name
        if path.is_symlink() or sha(path) != expected:
            raise ValueError("reviewed source must remain unchanged: " + name)


def safe_archive_member(member):
    name = Path(member.filename)
    mode = (member.external_attr >> 16) & 0o170000
    if name.is_absolute() or ".." in name.parts or "\\" in member.filename or member.is_dir() or mode == stat.S_IFLNK:
        raise ValueError("unsafe archive member")


def verify_export_archive(path, files):
    """Verify actual compressed member bytes, not just directory/CRC metadata."""
    with zipfile.ZipFile(path) as archive:
        names = [m.filename for m in archive.infolist()]
        if set(names) != set(files) or len(names) != len(files):
            raise ValueError("export archive file inventory differs")
        for member in archive.infolist():
            safe_archive_member(member)
            pin = files[member.filename]
            if member.file_size != pin["size_bytes"]:
                raise ValueError("export archive member size differs")
            digest = hashlib.sha256()
            with archive.open(member) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != pin["sha256"]:
                raise ValueError("export archive member SHA differs")
        if archive.testzip() is not None:
            raise ValueError("export archive CRC failure")


def audit_index_export(db, planning_preview=False):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != set(DB_COLUMNS) or db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
        raise ValueError("index contains a table/view/trigger outside public numeric worker schema")
    for table, names in DB_COLUMNS.items():
        columns = tuple(r[1] for r in db.execute(f"PRAGMA table_info({table})"))
        if columns != names:
            raise ValueError("index includes columns outside public numeric worker schema")
    if db.execute("PRAGMA freelist_count").fetchone()[0] != 0:
        raise ValueError("export index must have no abandoned database pages")
    R.verify_index_identity(db)
    if not planning_preview:
        R.verify_resume_ready(db)
    counts = {}
    for table in ("saved", "cache"):
        counts[table] = 0
        for row in db.execute(f"SELECT kind,score,origin FROM {table}"):
            kind, score, origin = row[0], json.loads(row[1]), json.loads(row[2])
            if kind not in R.KINDS or not SCORE_FIELDS[kind] <= set(score) or set(score) - SCORE_FIELDS[kind] - COMMON_SCORE_FIELDS:
                raise ValueError("numeric record has an unexpected/private field")
            if set(origin) != {"type", "remote_ref", "version", "file_sha256", "line_number"} or origin["type"] not in {"original_v2", "bounded_gpu_first_record"}:
                raise ValueError("numeric record origin has an unexpected/private field")
            if not re.fullmatch(r"superbigzabuton/[a-z0-9][a-z0-9-]+", origin["remote_ref"]) or type(origin["version"]) is not int or origin["version"] < 1:
                raise ValueError("numeric origin is not a pinned approved-owner version")
            if not re.fullmatch(r"[0-9a-f]{64}", origin["file_sha256"]) or type(origin["line_number"]) is not int or origin["line_number"] < 1:
                raise ValueError("numeric origin must identify immutable original raw bytes")
            counts[table] += 1
    return {"table_inventory": sorted(tables), "numeric_record_counts": counts, "private_fields_included": False}


def verify_state(state_dir, shard_id, planning_preview):
    checked = R.GPU.verify_bundle(state_dir)
    manifest = json.loads((state_dir / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
    if set(manifest["files"]) != STATE_FILES or manifest.get("private_source_joins_included") is not False:
        raise ValueError("only the exact public worker-state allowlist may be exported")
    for name in ("nextdata_exact_cache_resume_v1.py", "kaggle_nextdata_exact_resume_v1.py"):
        if sha(state_dir / name) != REVIEWED_CODE["experiments/sku-matching/" + name]:
            raise ValueError("worker-state code differs from reviewed source")
    plan = json.loads((state_dir / "worker-plan.json").read_text(encoding="utf-8"))
    if "local_product_receipts" in plan or plan.get("labels_read") is not False:
        raise ValueError("private source joins and grades must stay local")
    if not planning_preview and plan.get("numeric_continuation_allowed") is not True:
        raise ValueError("post-v2 imported worker-state required; planning-only state cannot be packaged for execution")
    if (plan.get("original_input_sha256"), plan.get("original_bundle_manifest_sha256"), plan.get("model_identities")) != (
            R.PINNED_INPUT_SHA, R.ORIGINAL_BUNDLE_SHA, {kind: R.model_identity(kind) for kind in R.KINDS}):
        raise ValueError("worker plan changes original model input or pins")
    if sha(state_dir / "index.sqlite") != plan["index_sha256"]:
        raise ValueError("worker index is not pinned by its original plan")
    if sha(state_dir / "pair-token-coverage.jsonl") != R.PINNED_COVERAGE_SHA or sha(state_dir / "PREFLIGHT_RECEIPT.json") != R.PINNED_PREFLIGHT_SHA:
        raise ValueError("original chunk ranges/preflight changed")
    shard = next((s for s in plan["shards"] if s["shard_id"] == shard_id), None)
    if shard is None:
        raise ValueError("one actual frozen shard must be selected")
    keys = [tuple(k) for k in shard["request_keys"]]
    if len(keys) != shard["unique_request_count"] or len(set(keys)) != len(keys) or not 0 < len(keys) <= min(20000, plan["max_unique_requests_per_shard"]):
        raise ValueError("shard duplicates requests or violates its frozen budget")
    db = R.connect(state_dir / "index.sqlite")
    try:
        audit = audit_index_export(db, planning_preview)
        for kind, key in keys:
            if kind not in R.KINDS or db.execute("SELECT 1 FROM requests WHERE kind=? AND exact_key=?", (kind, key)).fetchone() is None:
                raise ValueError("shard references an unknown request")
            if db.execute("SELECT 1 FROM cache WHERE kind=? AND exact_key=?", (kind, key)).fetchone():
                raise ValueError("shard already has a first record; replan instead of retry")
    finally:
        db.close()
    return checked, shard, audit


def render_notebook(config):
    source = BOOTSTRAP.replace("__CONFIG_REPR__", repr(R.canonical(config)))
    ast.parse(source)
    notebook = {"nbformat": 4, "nbformat_minor": 5,
                "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                             "language_info": {"name": "python"}},
                "cells": [{"id": "sku-exact-resume-bootstrap-v1", "cell_type": "code", "execution_count": None,
                           "metadata": {}, "outputs": [], "source": source.splitlines(keepends=True)}]}
    return notebook


def push_payload_bytes(notebook):
    source = json.loads(json.dumps(notebook))
    for cell in source["cells"]:
        cell["source"] = "".join(cell["source"])
        cell["outputs"] = []
    return len(json.dumps(source).encode("utf-8"))


def validate_intent(kernel_ref, kernel_version, dataset_version):
    if not re.fullmatch(r"superbigzabuton/[a-z0-9][a-z0-9-]+", kernel_ref):
        raise ValueError("fixed new Notebook ref must use the already approved owner")
    if type(kernel_version) is not int or kernel_version < 1 or kernel_ref == R.ORIGINAL_REF:
        raise ValueError("continuation requires a separate fixed Notebook ref; preserve original v1/v2")
    if type(dataset_version) is not int or dataset_version <= 1:
        raise ValueError("same private Dataset requires a fixed new version; do not replace v1")


def build(args):
    validate_intent(args.kernel_ref, args.kernel_version, args.dataset_version)
    verify_reviewed_code()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    original = R.GPU.verify_bundle(args.original_bundle_dir)
    if original["manifest_sha256"] != R.ORIGINAL_BUNDLE_SHA:
        raise ValueError("exact old unmodified approved original bundle required")
    state, shard, audit = verify_state(args.worker_state_dir, args.shard_id, args.planning_preview)
    args.output_dir.mkdir(parents=True)
    payload = args.output_dir / "payload"
    payload.mkdir()
    copied_inventory = {}
    for source_root, prefix, check in ((args.original_bundle_dir, "original", original), (args.worker_state_dir, "worker-state", state)):
        for name in [*check["files"], "BUNDLE_MANIFEST.json"]:
            source = source_root / name
            target = payload / prefix / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha(source) != sha(target):
                raise ValueError("source bytes changed while copying")
            copied_inventory[f"{prefix}/{name}"] = {"size_bytes": target.stat().st_size, "sha256": sha(target)}
    export = {"schema_version": "sku-exact-resume-private-export-v1", "dataset_ref": DATASET_REF,
              "dataset_version": args.dataset_version, "kernel_ref": args.kernel_ref, "kernel_version": args.kernel_version,
              "shard_id": args.shard_id, "max_seconds": 6000, "planning_preview": args.planning_preview,
              "labels_included": False, "private_source_joins_included": False, "raw_pages_included": False,
              "credentials_included": False, "model_weights_included": False,
              "original_bundle_manifest_sha256": original["manifest_sha256"],
              "state_bundle_manifest_sha256": state["manifest_sha256"], "original_input_sha256": R.PINNED_INPUT_SHA,
              "original_root_freeze_sha256": R.PINNED_ROOT_SHA, "original_preflight_coverage_sha256": R.PINNED_COVERAGE_SHA,
              "model_identities": {kind: R.model_identity(kind) for kind in R.KINDS},
              "shard_request_vector_sha256": R.value_sha(shard["request_keys"]), "unique_request_count": shard["unique_request_count"],
              "jev_sequence_count": shard["jev_sequence_count"], "nli_sequence_count": shard["nli_sequence_count"],
              "index_export_audit": audit, "files": copied_inventory,
              "approved_model_assets": "Original provenance and immutable HF revisions/file hashes reused by the unchanged approved CUDA loader; no new weights or model are exported."}
    R.write_json(payload / EXPORT_NAME, export)
    upload = args.output_dir / ("preview-dataset-do-not-upload" if args.planning_preview else "dataset-upload")
    upload.mkdir()
    archive_path = upload / ARCHIVE_NAME
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                archive.write(path, str(path.relative_to(payload)))
    archive_inventory = {**copied_inventory, EXPORT_NAME: {"size_bytes": (payload / EXPORT_NAME).stat().st_size,
                                                          "sha256": sha(payload / EXPORT_NAME)}}
    verify_export_archive(archive_path, archive_inventory)
    archive_sha = sha(archive_path)
    export_sha = sha(payload / EXPORT_NAME)
    config = {"dataset_ref": DATASET_REF, "dataset_version": args.dataset_version,
              "kernel_ref": args.kernel_ref, "kernel_version": args.kernel_version, "shard_id": args.shard_id,
              "planning_preview": args.planning_preview, "archive_sha256": archive_sha,
              "archive_bytes": archive_path.stat().st_size, "export_manifest_sha256": export_sha,
              "original_bundle_manifest_sha256": original["manifest_sha256"], "state_bundle_manifest_sha256": state["manifest_sha256"]}
    notebook_dir = args.output_dir / ("preview-notebook-do-not-submit" if args.planning_preview else "notebook-upload")
    notebook_dir.mkdir()
    notebook = render_notebook(config)
    notebook_path = notebook_dir / "nextdata_exact_resume.ipynb"
    R.write_json(notebook_path, notebook)
    payload_bytes = push_payload_bytes(notebook)
    if payload_bytes >= 1024 * 1024:
        raise ValueError("Notebook submission code payload must be less than 1MiB")
    if not args.planning_preview:
        slug = args.kernel_ref.split("/")[1]
        R.write_json(upload / "dataset-metadata.json", {"id": DATASET_REF, "title": DATASET_REF.split("/")[1],
                     "licenses": [{"name": "other"}]})
        R.write_json(notebook_dir / "kernel-metadata.json", {"id": args.kernel_ref, "title": slug,
                     "code_file": notebook_path.name, "language": "python", "kernel_type": "notebook",
                     "is_private": True, "enable_gpu": True, "enable_tpu": False, "enable_internet": True,
                     "dataset_sources": [DATASET_REF + "/" + str(args.dataset_version)],
                     "competition_sources": [], "kernel_sources": []})
        R.write_json(notebook_dir / "training-params.json", {"timeout_seconds": 7200})
    else:
        R.write_json(args.output_dir / "PREVIEW_BLOCKED.json", {"submit_allowed": False, "upload_allowed": False,
                     "reason": "original fixed-v2 terminal raw scores have not yet been imported; preview Notebook refuses before dependencies/model execution"})
    if R.GPU.verify_bundle(args.original_bundle_dir)["manifest_sha256"] != original["manifest_sha256"] or R.GPU.verify_bundle(args.worker_state_dir)["manifest_sha256"] != state["manifest_sha256"]:
        raise ValueError("source bundle changed while packaging")
    verify_reviewed_code()
    receipt = {"schema_version": "sku-exact-resume-local-notebook-package-v1", "submitted": False,
               "network_operations_performed": False, "model_inference_performed": False,
               "planning_preview": args.planning_preview, "ready_for_root_remote_review": not args.planning_preview,
               **config, "export_manifest_file": str(payload / EXPORT_NAME),
               "export_file_count": len(copied_inventory) + 1,
               "export_uncompressed_bytes": sum(e["size_bytes"] for e in copied_inventory.values()) + (payload / EXPORT_NAME).stat().st_size,
               "notebook_file_sha256": sha(notebook_path), "notebook_push_payload_bytes": payload_bytes,
               "notebook_source_signature": R.value_sha([["code", "".join(notebook["cells"][0]["source"])] ]),
               "builder_sha256": sha(Path(__file__)), "reviewed_source_sha256": REVIEWED_CODE,
               "expected_submit_ref_version": {"ref": args.kernel_ref, "version": args.kernel_version},
               "artifact_acceptance": ["Actual returned job ref/version and saved Notebook source must match these frozen values; no autosubmit repeat on mismatch.",
                    "Dataset new version is private/ready, mounted inventory and both source manifests match exactly.",
                    "EXECUTION_FREEZE attests real CUDA FP32/eager/batch16/model revisions/parameter and file counts.",
                    "Retrieve every flushed new-first-scores record; ROOT imports without overwriting original saved/cache records; only completed whole products are assembled."],
               "original_source_score_bytes_modified": False, "production_adoption_allowed": False}
    R.write_json(args.output_dir / "LOCAL_NOTEBOOK_PACKAGE_RECEIPT.json", receipt)
    print(R.canonical({"stage": "local-preview" if args.planning_preview else "local-package-ready-for-root-review",
                       "archive_bytes": receipt["archive_bytes"], "archive_sha256": archive_sha,
                       "export_file_count": receipt["export_file_count"], "notebook_push_payload_bytes": payload_bytes,
                       "remote_operations_performed": False}))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original-bundle-dir", "worker-state-dir", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--kernel-ref", required=True)
    parser.add_argument("--kernel-version", type=int, required=True)
    parser.add_argument("--dataset-version", type=int, required=True)
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--planning-preview", action="store_true", help="Local blocked preview only; no Kaggle metadata is emitted")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
