#!/usr/bin/env python3
"""Package this source-backed diagnostic, keeping references apart from inference.

Weights, environments and credentials are excluded. The previous full trial is
an explicit dependency; this archive includes the current AU raw API files and
every input, frozen model score, gate and separate machine-label reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def package(prepared_dir, run_dir, evaluation_dir, report, audit_files, check_log, output, additional_dirs=()):
    if any(path.exists() for path in (output, output.with_suffix(".manifest.json"), Path(str(output) + ".sha256"))):
        raise FileExistsError(output)
    prep = json.loads((prepared_dir / "manifest.json").read_text())
    run = json.loads((run_dir / "summary.json").read_text())
    evaluation = json.loads((evaluation_dir / "summary.json").read_text())
    if not (prep["errors_only"] and run["errors_only"] and prep["synthetic_data"] is False
            and prep["independent_test"] is False and run["production_adoption_allowed"] is False):
        raise ValueError("scope declarations differ")
    files = {}

    def add(path, name=None):
        # Keep the repository spelling of shared artifact symlinks while
        # accepting the same relative CLI paths as the trial commands.
        path = Path(path).absolute()
        name = name or str(path.relative_to(ROOT))
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("unsafe archive path")
        if name in files and files[name] != path:
            raise ValueError("archive path collision")
        if not path.is_file() or any(part in {".deps", ".venv", "__pycache__"} for part in Path(name).parts):
            raise ValueError("unavailable or excluded payload path: " + name)
        if path.suffix in {".safetensors", ".onnx", ".pyc"} or path.name.lower() in {".env", "credentials.json", "kaggle.json"}:
            raise ValueError("weights/credentials/cache cannot enter archive")
        files[name] = path

    for directory in (prepared_dir, run_dir, evaluation_dir, *additional_dirs):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                add(path)
    for raw_name, expected in prep["raw_files"].items():
        path = ROOT / raw_name
        if sha(path) != expected:
            raise ValueError("raw AU API hash changed")
        add(path, raw_name)
    for directory, manifest in ((prepared_dir, prep), (run_dir, run)):
        for name, expected in manifest["output_sha256"].items():
            if sha(directory / name) != expected:
                raise ValueError("frozen output hash differs: " + name)
    for path in (report, *audit_files, check_log, Path(__file__)):
        add(path)
    for suffix in ("blocks", "preparation", "evidence", "evidence_choice", "trial_evaluation"):
        add(ROOT / "tests" / ("test_lab_sku_scoped_html_" + suffix + ".py"))
    for name in ("build_scoped_html_blocks_v1.py", "prepare_scoped_html_trial_v1.py",
                 "trial_scoped_html_evidence_v1.py", "trial_scoped_html_evidence_choice_v2.py",
                 "evaluate_scoped_html_trial_v1.py", "integrate_claude_description_v1.py"):
        add(HERE / name)
    labels = [(ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl", "reference-only/legacy-labels.jsonl"),
              (ROOT / ".lab-output/sku-novel-luna-labels-20261010-v1/labels.jsonl", "reference-only/novel-labels.jsonl")]
    for path, name in labels:
        add(path, name)
    previous = HERE / "results/20261011-sku-full-condition-choice-trial.zip"
    dependencies = [{"path": str(previous.relative_to(ROOT)), "size_bytes": previous.stat().st_size,
                     "sha256": sha(previous), "role": "previous full-cohort source/baseline and frozen code"}]
    manifest = {"schema_version": "scoped-html-error-trial-archive-v1", "case_count": prep["case_count"],
                "previous_error_counts": prep["selected_error_counts"], "selection_policy": prep["selection_policy"],
                "reference_kind": "machine_labels_not_human_gold", "independent_test": False,
                "production_adoption_allowed": False, "synthetic_sku_evaluation": False,
                "unit_test_fixtures_are_not_evaluation_samples": True,
                "dependencies": dependencies,
                "payload": {name: {"sha256": sha(path), "size_bytes": path.stat().st_size}
                            for name, path in sorted(files.items())}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("archive CRC failed")
        for name, expected in manifest["payload"].items():
            body = archive.read(name)
            if len(body) != expected["size_bytes"] or hashlib.sha256(body).hexdigest() != expected["sha256"]:
                raise ValueError("archive bytes differ: " + name)
    archive_sha = sha(output)
    sidecar = {**manifest, "archive_sha256": archive_sha, "archive_size_bytes": output.stat().st_size,
               "payload_file_count": len(files), "crc_and_all_payload_hashes_verified": True,
               "evaluation_summary_sha256": sha(evaluation_dir / "summary.json")}
    output.with_suffix(".manifest.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    Path(str(output) + ".sha256").write_text(archive_sha + "  " + output.name + "\n")
    return {key: sidecar[key] for key in ("archive_sha256", "archive_size_bytes", "payload_file_count")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared-dir", "run-dir", "evaluation-dir", "report", "check-log", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--audit-files", type=Path, nargs="+", required=True)
    parser.add_argument("--additional-dirs", type=Path, nargs="*", default=[])
    print(json.dumps(package(**vars(parser.parse_args())), ensure_ascii=False))
