"""Build a compact, self-contained Kaggle GPU notebook for public image evaluation."""
from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/captcha-small-model"
SCRIPT_NAMES = (
    "benchmark_public_samples.py",
    "benchmark_embeddings.py",
    "benchmark_moe_vie.py",
    "download_model.py",
    "subprocess_runner.py",
)
MODEL_NAMES = ("TinyCLIP-ViT-40M-32-Text-19M", "MobileCLIP2-S0", "MobileCLIP2-S2", "MoE-ViE-B16")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cell(kind: str, source: str) -> dict:
    return {
        "id": hashlib.sha256((kind + source).encode()).hexdigest()[:12],
        "cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True),
        **({"execution_count": None, "outputs": []} if kind == "code" else {}),
    }


def safe_relative(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"Unsafe/empty image path: {raw!r}")
    p = PurePosixPath(raw)
    if p.is_absolute() or p.as_posix() != raw or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError(f"Image path must be normalized and relative: {raw!r}")
    return p.as_posix()


def image_references(manifest: dict) -> set[str]:
    refs: set[str] = set()
    for row in manifest.get("samples", []):
        refs.add(safe_relative(row["path"]))
    for case in manifest.get("cases", []):
        refs.update(safe_relative(p) for p in case["tile_paths"])
    if not refs:
        raise ValueError("Manifest has no referenced images")
    return refs


def validate_bundle(bundle: Path, manifest_bytes: bytes, refs: set[str]) -> str:
    if not bundle.is_file():
        raise FileNotFoundError(bundle)
    hasher = hashlib.sha256()
    with bundle.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    with zipfile.ZipFile(bundle) as archive:
        files: set[str] = set()
        for info in archive.infolist():
            name = info.filename
            if name.endswith("/"):
                name = name[:-1]
            if not name:
                continue
            safe_relative(name)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"ZIP symlink is forbidden: {info.filename}")
            if not info.is_dir():
                if name in files:
                    raise ValueError(f"Duplicate ZIP member path: {name}")
                files.add(name)
        if "manifest.json" not in files:
            raise ValueError("Bundle must contain root manifest.json")
        if archive.read("manifest.json") != manifest_bytes:
            raise ValueError("Bundle manifest.json differs byte-for-byte from --manifest")
        missing = refs - files
        if missing:
            raise ValueError(f"Bundle lacks referenced image members: {sorted(missing)[:5]}")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Exact manifest.json to include")
    parser.add_argument("--dataset-ref", required=True, help="Kaggle dataset owner/slug")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bundle", type=Path, help="Optional local evaluation_bundle.zip to prevalidate and pin")
    parser.add_argument("--models", default=",".join(MODEL_NAMES), help="Comma-separated subset for a bounded repair trial")
    args = parser.parse_args()
    selected_models = args.models.split(",")
    if not selected_models or len(set(selected_models)) != len(selected_models) or any(name not in MODEL_NAMES for name in selected_models):
        raise ValueError("--models must be a unique subset of supported models")
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output}")
    if "/" not in args.dataset_ref or any(not part for part in args.dataset_ref.split("/")):
        raise ValueError("--dataset-ref must be owner/slug")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError("Manifest root must be an object")
    refs = image_references(manifest)
    bundle_sha = validate_bundle(args.bundle, manifest_bytes, refs) if args.bundle else None

    payload = {
        "scripts": {name: (EXPERIMENT / name).read_text(encoding="utf-8") for name in SCRIPT_NAMES},
        "script_sha256": {name: sha256((EXPERIMENT / name).read_bytes()) for name in SCRIPT_NAMES},
        "manifest_sha256": sha256(manifest_bytes),
        "bundle_sha256": bundle_sha,
        "dataset_ref": args.dataset_ref,
    }
    payload_raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    packed = base64.b64encode(gzip.compress(payload_raw, compresslevel=9, mtime=0)).decode("ascii")
    if len(packed.encode("ascii")) > 1024 * 1024:
        raise ValueError(f"Compressed notebook payload exceeds 1 MiB: {len(packed)} bytes")

    intro = f"""# Public image-set GPU benchmark

Runs {', '.join(selected_models)} against the supplied public evaluation manifest. This measures performance on these supplied labels and board references; it does not establish general CAPTCHA-solving accuracy. The manifest bytes are pinned by SHA-256. Input images are read from either `evaluation_bundle.zip` or Kaggle's auto-extracted `evaluation_bundle/` folder, checked against the pinned manifest, and copied under `/tmp` only. Model caches and MoE source also live under `/tmp`; small logs, summaries, scripts, and result files are collected in `/kaggle/working`.

Dataset: `{args.dataset_ref}`

Manifest SHA-256: `{payload['manifest_sha256']}`

Source bundle SHA-256: `{bundle_sha or 'not locally pinned'}`. Kaggle may auto-extract the archive; then the manifest SHA and every image SHA are checked, while the original archive SHA is recorded but cannot be rechecked.

The notebook uses the Kaggle-provided PyTorch, CUDA, and Triton. It installs only pinned Python packages with `--no-deps`. Each model runs in its own subprocess with an explicit timeout and per-model log. Any failed or timed-out process remains visible in the final status and causes the notebook to fail after writing its summary.
"""
    install = """# Keep Kaggle's preinstalled torch, CUDA, and Triton unchanged.
%pip install --no-deps open_clip_torch==3.3.0 ftfy==6.3.1 einops==0.8.1 timm==1.0.30
"""
    setup = f'''import base64, gzip, hashlib, json, os, pathlib, platform, shutil, stat, subprocess, sys, time, zipfile

payload = json.loads(gzip.decompress(base64.b64decode({packed!r})))
work = pathlib.Path("/kaggle/working")
input_root = pathlib.Path("/tmp/public-eval-input")
source_root = pathlib.Path("/tmp/public-eval-scripts")
saved_scripts = work / "public-eval-scripts"
output_names = ["public-eval-summary.json", "environment.json", "public-eval-scripts"]
model_keys = ["tinyclip_vit_40m_32_text_19m", "mobileclip2_s0", "mobileclip2_s2", "moe_vie_b16"]
output_names += ["public-eval-tinyclip-download.log.txt", "public-eval-tinyclip-provenance.json"]
for key in model_keys:
    output_names += [f"public-eval-result-{{key}}", f"public-eval-{{key}}.log.txt"]
if not work.is_dir():
    raise FileNotFoundError("Expected /kaggle/working")
for name in output_names:
    if (work / name).exists():
        raise FileExistsError(f"Refusing to overwrite existing artifact: {{work / name}}")
for path in (input_root, source_root):
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing scratch path: {{path}}")
input_root.mkdir(parents=True)
source_root.mkdir(parents=True)
for name, content in payload["scripts"].items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != payload["script_sha256"][name]:
        raise RuntimeError(f"Embedded source SHA-256 mismatch: {{name}}")
    (source_root / name).write_bytes(raw)
saved_scripts.mkdir()
for name in payload["scripts"]:
    shutil.copyfile(source_root / name, saved_scripts / name)
manifest_path = input_root / "manifest.json"

mount = pathlib.Path("/kaggle/input")
if not mount.is_dir():
    raise FileNotFoundError("Kaggle dataset input mount is missing")
def safe_rel(raw):
    p = pathlib.PurePosixPath(raw)
    if not raw or "\\\\" in raw or p.is_absolute() or p.as_posix() != raw or any(x in ("", ".", "..") for x in p.parts):
        raise ValueError(f"Unsafe archive path: {{raw!r}}")
    return p.as_posix()

# Kaggle may preserve an archive or expand a .zip into evaluation_bundle/.
# Search only this dataset's known mount roots; .bin keeps ZIP bytes opaque.
owner, slug = payload["dataset_ref"].split("/", 1)
dataset_roots = [mount / slug, mount / "datasets" / owner / slug]
dataset_roots = [root for root in dataset_roots if root.is_dir()]
if not dataset_roots:
    raise FileNotFoundError(f"Dataset mount not found under expected roots: {{mount / slug}}, {{mount / 'datasets' / owner / slug}}")
print(json.dumps({{"input_discovery": "started", "dataset_roots": [str(p) for p in dataset_roots]}}), flush=True)
archive_candidates = sorted({{root / name for root in dataset_roots
                             for name in ("evaluation_bundle.bin", "evaluation_bundle.zip")
                             if (root / name).is_file()}})
if len(archive_candidates) > 1:
    raise RuntimeError(f"Expected at most one evaluation_bundle.bin/.zip, found {{len(archive_candidates)}}")
archive_path = archive_candidates[0] if len(archive_candidates) == 1 else None
actual_bundle_sha = None
input_mode = None
input_load_started = time.monotonic()
input_image_bytes_copied = 0
if archive_path is not None:
    print(json.dumps({{"input_archive_found": str(archive_path), "bytes": archive_path.stat().st_size}}), flush=True)
    bundle_hash = hashlib.sha256()
    hashed_bytes = 0
    next_hash_report = 64 * 1024 * 1024
    with archive_path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            bundle_hash.update(block)
            hashed_bytes += len(block)
            if hashed_bytes >= next_hash_report:
                print(json.dumps({{"input_hash_bytes": hashed_bytes, "elapsed_seconds": round(time.monotonic() - input_load_started, 1)}}), flush=True)
                next_hash_report += 64 * 1024 * 1024
    actual_bundle_sha = bundle_hash.hexdigest()
    if payload["bundle_sha256"] and actual_bundle_sha != payload["bundle_sha256"]:
        raise RuntimeError(f"Bundle SHA-256 mismatch: expected {{payload['bundle_sha256']}}, got {{actual_bundle_sha}}")
    print(json.dumps({{"input_hash_complete": True, "sha256": actual_bundle_sha}}), flush=True)
    input_mode = "opaque_zip_archive" if archive_path.suffix == ".bin" else "zip_archive"
    with zipfile.ZipFile(archive_path) as zf:
        names = set()
        for info in zf.infolist():
            name = info.filename[:-1] if info.filename.endswith("/") else info.filename
            if not name: continue
            safe_rel(name)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode): raise ValueError(f"ZIP symlink is forbidden: {{info.filename}}")
            if not info.is_dir():
                if name in names: raise ValueError(f"Duplicate ZIP member path: {{name}}")
                names.add(name)
        if "manifest.json" not in names: raise ValueError("ZIP missing root manifest.json")
        manifest_bytes = zf.read("manifest.json")
        actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_sha != payload["manifest_sha256"]:
            raise ValueError(f"Manifest SHA-256 mismatch: expected {{payload['manifest_sha256']}}, got {{actual_manifest_sha}}")
        manifest_path.write_bytes(manifest_bytes)
        manifest = json.loads(manifest_bytes)
        refs = set()
        for sample in manifest.get("samples", []): refs.add(sample["path"])
        for case in manifest.get("cases", []): refs.update(case["tile_paths"])
        if not refs: raise ValueError("Manifest contains no image references")
        refs = {{safe_rel(x) for x in refs}}
        missing = refs - names
        if missing: raise ValueError(f"ZIP missing referenced images: {{sorted(missing)[:5]}}")
        print(json.dumps({{"input_extraction": "started", "image_count": len(refs)}}), flush=True)
        for index, relative in enumerate(sorted(refs), 1):
            destination = input_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(relative) as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            input_image_bytes_copied += destination.stat().st_size
            if index % 1000 == 0 or index == len(refs):
                print(json.dumps({{"input_extracted_images": index, "image_count": len(refs),
                                  "image_bytes_copied": input_image_bytes_copied,
                                  "elapsed_seconds": round(time.monotonic() - input_load_started, 1)}}), flush=True)
else:
    extracted_candidates = sorted({{candidate for root in dataset_roots
                                   for candidate in (root / "evaluation_bundle" / "manifest.json",
                                       *root.glob("*/evaluation_bundle/manifest.json"))
                                   if candidate.is_file()}})
    print(json.dumps({{"input_extracted_manifest_candidates": len(extracted_candidates)}}), flush=True)
    matching = []
    for candidate in extracted_candidates:
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest == payload["manifest_sha256"]:
                matching.append(candidate)
        except OSError:
            continue
    if len(matching) != 1:
        raise FileNotFoundError(f"Expected one auto-extracted evaluation_bundle/manifest.json matching pinned SHA; found {{len(matching)}}")
    bundle_root = matching[0].parent
    if matching[0].is_symlink() or bundle_root.is_symlink(): raise ValueError("Symlinks in auto-extracted manifest path are forbidden")
    manifest_bytes = matching[0].read_bytes()
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path.write_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    refs = set()
    for sample in manifest.get("samples", []): refs.add(sample["path"])
    for case in manifest.get("cases", []): refs.update(case["tile_paths"])
    if not refs: raise ValueError("Manifest contains no image references")
    refs = {{safe_rel(x) for x in refs}}
    print(json.dumps({{"input_extraction": "started", "image_count": len(refs)}}), flush=True)
    for index, relative in enumerate(sorted(refs), 1):
        source = bundle_root / relative
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(bundle_root.resolve()):
            raise ValueError(f"Image path escapes extracted bundle: {{relative}}")
        cursor = bundle_root
        for part in pathlib.PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink(): raise ValueError(f"Symlink in extracted image path: {{relative}}")
        if not resolved.is_file(): raise FileNotFoundError(source)
        destination = input_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, destination)
        input_image_bytes_copied += destination.stat().st_size
        if index % 1000 == 0 or index == len(refs):
            print(json.dumps({{"input_extracted_images": index, "image_count": len(refs),
                              "image_bytes_copied": input_image_bytes_copied,
                              "elapsed_seconds": round(time.monotonic() - input_load_started, 1)}}), flush=True)
    input_mode = "kaggle_auto_extracted_zip"

# Capture runtime details before the isolated model processes start.
env = {{"python": sys.version, "platform": platform.platform(), "dataset_ref": payload["dataset_ref"],
       "input_mode": input_mode, "original_bundle_sha256": payload["bundle_sha256"], "mounted_bundle_sha256": actual_bundle_sha,
       "mounted_archive_path": str(archive_path) if archive_path else None,
       "manifest_sha256": payload["manifest_sha256"], "input_image_count": len(refs),
       "input_manifest_bytes": len(manifest_bytes), "input_image_bytes_copied": input_image_bytes_copied,
       "input_load_elapsed_seconds": round(time.monotonic() - input_load_started, 3)}}
try:
    import torch
    env["torch"] = torch.__version__; env["cuda_runtime"] = torch.version.cuda
    env["cuda_available"] = torch.cuda.is_available(); env["gpu_count"] = torch.cuda.device_count()
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        env.update({{"gpu_name": torch.cuda.get_device_name(0), "gpu_capability": list(torch.cuda.get_device_capability(0)),
                    "gpu_total_memory_mib": props.total_memory / 1048576}})
except Exception as e: env["torch_error"] = f"{{type(e).__name__}}: {{e}}"
try:
    import triton; env["triton"] = triton.__version__
except Exception as e: env["triton_error"] = f"{{type(e).__name__}}: {{e}}"
try:
    p = subprocess.run(["nvidia-smi", "--query-gpu=driver_version,name,memory.total,memory.used,memory.free", "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
    env["nvidia_smi"] = {{"returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}}
except Exception as e: env["nvidia_smi_error"] = f"{{type(e).__name__}}: {{e}}"
(work / "environment.json").write_text(json.dumps(env, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(env, indent=2))
'''
    run = f'''import json, pathlib, sys

work = pathlib.Path("/kaggle/working")
root = pathlib.Path("/tmp/public-eval-input")
scripts = pathlib.Path("/tmp/public-eval-scripts")
sys.path.insert(0, str(scripts))
from subprocess_runner import run_logged_process
manifest = root / "manifest.json"
model_dir = pathlib.Path("/tmp/public-eval-model-cache")
baseline_dir = pathlib.Path("/tmp/public-eval-tinyclip")
model_dir.mkdir(); baseline_dir.mkdir()
models = {selected_models!r}
summary = {{"status": "running", "models": {{}}, "input_manifest_sha256": {payload['manifest_sha256']!r}, "logs": {{}}}}
summary_path = work / "public-eval-summary.json"

def save_summary():
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")

def run_one(key, command, timeout):
    log = work / ("public-eval-" + key + ".log.txt")
    try:
        result = run_logged_process(command, log, timeout)
    except Exception as exc:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"{{type(exc).__name__}}: {{exc}}\\n")
        result = {{"returncode": None, "timed_out": False, "launch_error": f"{{type(exc).__name__}}: {{exc}}"}}
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:] if log.exists() else []
    result["log"] = str(log); result["log_tail"] = "\\n".join(tail)
    return result

# The existing downloader verifies pinned TinyCLIP config and weight hashes.
if "TinyCLIP-ViT-40M-32-Text-19M" in models:
    summary["tinyclip_download"] = run_one("tinyclip-download", [sys.executable, str(scripts / "download_model.py"),
        "--size", "medium", "--output", str(baseline_dir)], 180)
    summary["logs"]["tinyclip_download"] = summary["tinyclip_download"]["log"]
    if summary["tinyclip_download"].get("returncode") == 0:
        provenance = baseline_dir / "provenance.json"
        if provenance.is_file(): shutil.copyfile(provenance, work / "public-eval-tinyclip-provenance.json")
    save_summary()

for name in models:
    key = name.lower().replace("-", "_")
    out_dir = work / ("public-eval-result-" + key)
    cmd = [sys.executable, str(scripts / "benchmark_public_samples.py"), "--input", str(manifest),
           "--output-dir", str(out_dir), "--models", name, "--device", "cuda", "--precision", "fp16",
           "--memory-format", "contiguous", "--batch-size", "32", "--model-dir", str(model_dir),
           "--baseline-dir", str(baseline_dir)]
    timeout = 1800 if name == "MoE-ViE-B16" else 400
    if name == "MoE-ViE-B16":
        cmd += ["--source-dir", "/tmp/public-eval-moe-source"]
    if name == "TinyCLIP-ViT-40M-32-Text-19M" and summary["tinyclip_download"].get("returncode") != 0:
        status = {{"returncode": None, "timed_out": False, "skipped": True, "reason": "TinyCLIP asset download failed"}}
    else:
        status = run_one(key, cmd, timeout)
    summary["models"][name] = status
    summary["logs"][name] = status.get("log")
    save_summary()

summary["moe"] = summary["models"].get("MoE-ViE-B16", {{}})

failures = {{name: status for name, status in summary["models"].items()
             if status.get("returncode") != 0 or status.get("timed_out") or status.get("skipped")}}
if "tinyclip_download" in summary and summary["tinyclip_download"].get("returncode") != 0: failures["tinyclip_download"] = summary["tinyclip_download"]
summary["status"] = "error" if failures else "success"
summary["failures"] = failures
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
if failures: raise RuntimeError(f"One or more benchmark processes failed; see {{summary_path}}")
'''
    # run_one log path names already keyed by model; align that mapping with `key` above.
    # Benchmark artifact collection cell emits an index of compact outputs; image inputs remain in /tmp.
    collect = '''import json, pathlib
work = pathlib.Path("/kaggle/working")
summary = json.loads((work / "public-eval-summary.json").read_text(encoding="utf-8"))
collected = [str(p) for p in sorted(work.rglob("*")) if p.is_file()]
print(json.dumps({"status": summary["status"], "collected_files": collected,
                  "models": summary["models"]}, ensure_ascii=False, indent=2))
'''
    notebook = {
        "cells": [cell("markdown", intro), cell("code", install), cell("code", setup), cell("code", run), cell("code", collect)],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3.12"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    # Validate every Python cell before writing; strip the single IPython install magic.
    for item in notebook["cells"]:
        if item["cell_type"] == "code":
            source = "".join(item["source"])
            source = "\n".join(line for line in source.splitlines() if not line.startswith("%pip "))
            ast.parse(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    notebook_text = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    notebook_size = len(notebook_text.encode("utf-8"))
    if notebook_size >= 1024 * 1024:
        raise ValueError(f"Notebook exceeds Kaggle 1 MiB source limit: {notebook_size} bytes")
    args.output.write_text(notebook_text, encoding="utf-8")
    print(f"Wrote {args.output} ({args.output.stat().st_size:,} bytes; {len(refs)} manifest image references)")


if __name__ == "__main__":
    main()
