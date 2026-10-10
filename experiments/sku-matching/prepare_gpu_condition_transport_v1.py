"""Create a private, immutable Kaggle payload after condition source preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(prepared: Path, preflight: Path, output: Path, dataset_ref: str, kernel_ref: str) -> dict:
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((prepared / "manifest.json").read_text())
    check = json.loads(preflight.read_text())
    if check.get("status") != "pass" or check.get("labels_read") is not False:
        raise ValueError("Missing label-free preflight pass")
    packet_sha = sha(prepared / "model/packets.jsonl")
    if check["packet_sha256"] != packet_sha or manifest["model_packets_sha256"] != packet_sha:
        raise ValueError("Packet hashes differ")
    if manifest["host_fullpools_sha256"] != sha(prepared / "host/fullpools.jsonl"):
        raise ValueError("Host full pool changed")
    code = ROOT / "experiments/sku-matching"
    if check["runner_sha256"] != sha(code / "kaggle_gpu_condition_runner_v1.py"):
        raise ValueError("Runner changed after preflight")
    output.mkdir(parents=True)
    upload = output / "dataset-upload"
    notebook = output / "notebook-upload"
    upload.mkdir(); notebook.mkdir()
    for source, name in ((prepared / "model/packets.jsonl", "packets.jsonl"),
                         (prepared / "host/fullpools.jsonl", "host-fullpools.jsonl"),
                         (prepared / "manifest.json", "preparation-manifest.json"),
                         (preflight, "preflight.json"),
                         (code / "kaggle_gpu_condition_runner_v1.py", "kaggle_gpu_condition_runner_v1.py"),
                         (code / "kaggle_gpu_sku_runner_v3.py", "kaggle_gpu_sku_runner_v3.py")):
        shutil.copyfile(source, upload / name)
    config = {"model": {"name": "Qwen/Qwen3.5-4B", "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "load": "nf4"},
              "task": "single_atomic_condition_vs_scoped_evidence", "batch_size": 1,
              "response": "last_token_logits_over_0_1_2", "symbol_permutations": 3,
              "aggregate": "all_three_permutations_agree", "max_input_tokens": 2048,
              "prefill_chunk_size": 512, "runtime_budget_seconds": 3600,
              "labels_read": False, "diagnostic_only": True,
              "evaluator_sha256": sha(code / "evaluate_gpu_condition_tasks_v1.py")}
    write(upload / "config.json", config)
    payloads = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(upload.iterdir())}
    write(upload / "upload-manifest.json", {"schema": "gpu-condition-upload-v1", "labels_read": False,
          "input_count": manifest["packet_count"], "inference_count": manifest["packet_count"] * 3,
          "upload_payloads": payloads})
    write(upload / "dataset-metadata.json", {"id": dataset_ref, "title": dataset_ref.split("/")[1],
                                             "licenses": [{"name": "CC0-1.0"}]})
    source = '''import hashlib, json, os, pathlib, subprocess, sys, time, venv
run_started=time.monotonic()
os.environ["HF_HOME"]="/tmp/sku-condition-v1-huggingface-cache"
os.environ["TOKENIZERS_PARALLELISM"]="false"
os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
print("Qwen3.5 4B NF4 condition relation diagnostic",flush=True)
root=pathlib.Path("/kaggle/input")
hits=[p.parent for p in root.rglob("upload-manifest.json") if (p.parent/"packets.jsonl").is_file() and (p.parent/"kaggle_gpu_condition_runner_v1.py").is_file()]
if len(hits)!=1: raise RuntimeError("Ambiguous or missing condition dataset")
inp=hits[0]; upload=json.loads((inp/"upload-manifest.json").read_text())
for name,info in upload["upload_payloads"].items():
    data=(inp/name).read_bytes()
    if len(data)!=info["bytes"] or hashlib.sha256(data).hexdigest()!=info["sha256"]: raise RuntimeError("Payload hash mismatch: "+name)
print("Verified label-free packets",upload["input_count"],flush=True)
venv_root=pathlib.Path("/tmp/sku-condition-v1-python")
venv.EnvBuilder(with_pip=False,system_site_packages=False).create(venv_root)
py=str(venv_root/"bin/python")
subprocess.run([sys.executable,"-m","pip","--python",py,"install","--no-cache-dir","torch==2.11.0","torchvision==0.26.0","--index-url","https://download.pytorch.org/whl/cu126"],check=True,timeout=900)
subprocess.run([sys.executable,"-m","pip","--python",py,"install","--no-cache-dir","transformers==5.17.0","accelerate==1.15.0","bitsandbytes==0.50.2"],check=True,timeout=600)
out=pathlib.Path("/kaggle/working/sku-condition-v1-results")
remaining=max(1,min(3600,int(3600-(time.monotonic()-run_started))))
cmd=[py,str(inp/"kaggle_gpu_condition_runner_v1.py"),"--input",str(inp/"packets.jsonl"),"--output-dir",str(out),"--timeout",str(remaining)]
try: rc=subprocess.run(cmd,timeout=remaining).returncode
except subprocess.TimeoutExpired:
    (pathlib.Path("/kaggle/working")/"sku-condition-v1-timeout.json").write_text(json.dumps({"timeout_seconds":remaining})); raise
print("Runner exit",rc,flush=True)
if rc: raise RuntimeError("Runner returned nonzero; inspect saved summary")
print("Result files",[p.name for p in out.iterdir()],flush=True)
'''
    write(notebook / "trial.ipynb", {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
          "cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                     "source": source.splitlines(keepends=True)}]})
    write(notebook / "kernel-metadata.json", {"id": kernel_ref, "title": kernel_ref.split("/")[1],
          "code_file": "trial.ipynb", "language": "python", "kernel_type": "notebook", "is_private": True,
          "enable_gpu": True, "enable_tpu": False, "enable_internet": True,
          "dataset_sources": [dataset_ref], "competition_sources": [], "kernel_sources": []})
    write(notebook / "training-params.json", {"timeout_seconds": 3600})
    freeze = {"schema": "condition-transport-freeze-v1", "labels_read": False,
              "model": config["model"], "case_count": manifest["case_count"],
              "full_pool_row_references": manifest["au_row_count_total"],
              "packet_count": manifest["packet_count"], "inference_count": manifest["packet_count"] * 3,
              "dataset_ref": dataset_ref, "kernel_ref": kernel_ref, "payloads": payloads,
              "notebook_sha256": sha(notebook / "trial.ipynb"),
              "evaluator_sha256": config["evaluator_sha256"], "preflight_sha256": sha(preflight)}
    write(output / "transport-freeze.json", freeze)
    return freeze


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepared", type=Path, required=True)
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--dataset-ref", required=True)
    ap.add_argument("--kernel-ref", required=True)
    args = ap.parse_args()
    print(json.dumps(prepare(args.prepared, args.preflight, args.output, args.dataset_ref, args.kernel_ref), indent=2))
