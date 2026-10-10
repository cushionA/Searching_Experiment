#!/usr/bin/env python3
"""Local llama-server client for the frozen, label-blind SKU mapping trial.

This candidate intentionally performs no downloads/builds and only connects to a
loopback server. The pinned Qwen runner is loaded from the frozen input bundle.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

PLAN_DEFAULT = Path(__file__).resolve().parents[2] / ".lab-output/sku-kaggle-gpu-20261010-v5/bonsai2-t4-test-plan.json"
FROZEN_INPUT_SHA256 = "0b5e5c9cbe41d56b36d5207e2f19ecaefe2f78fd149cc7e648ee1624a9408814"
FROZEN_MANIFEST_SHA256 = "494dc446c45673511309186272bba48caf3e92b8d141b98604df1b541d1d584c"
FROZEN_CONFIG_SHA256 = "b07bd702f4fa35ab9938f666fd050e7f9208abe057240a23c9910cde935f5e1a"
FROZEN_QWEN_RUNNER_SHA256 = "4bfca9164fd27ae00830b5d323f853403527ade4e446bebb8b761752b40184d3"
MODEL_REPO = "prism-ml/Ternary-Bonsai-2-27B-gguf"
MODEL_REVISION = "b072e1d3b35a0a630cece372c2127528e0994386"
MODEL_FILE = "Ternary-Bonsai-2-27B-PTQ1_0.gguf"
MODEL_SHA256 = "53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3"
LLAMA_REPO = "https://github.com/PrismML-Eng/llama.cpp.git"
LLAMA_BRANCH = "prism"
LLAMA_COMMIT = "6684606ac1bfcf8e31465369d9365df03b3af904"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_positive_finite(value: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            records.append(value)
    return records


def require_loopback_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("server URL must use loopback; this runner does not call remote services")
    return url.rstrip("/")


class ServerClient:
    """Minimal stdlib HTTP client for the pinned fork's documented server API."""

    def __init__(self, base_url: str, timeout_seconds: float = 120.0):
        self.base_url = require_loopback_url(base_url)
        self.timeout_seconds = require_positive_finite(timeout_seconds, "request timeout")
        # A dedicated opener keeps this local-only client out of urllib's
        # process-global proxy cache and leaves external request behavior intact.
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                timeout_seconds: float | None = None) -> tuple[int, bytes]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            timeout = self.timeout_seconds if timeout_seconds is None else require_positive_finite(timeout_seconds, "request timeout")
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def json_request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                     timeout_seconds: float | None = None) -> tuple[int, dict[str, Any], bytes]:
        status, raw = self.request(method, path, payload, timeout_seconds)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = {"_decode_error": True}
        return status, decoded if isinstance(decoded, dict) else {"_json_value": decoded}, raw


def validate_pin_plan(plan: dict[str, Any]) -> None:
    model = plan.get("model")
    runtime = plan.get("runtime")
    frozen = plan.get("inference_protocol", {}).get("frozen_inputs")
    if not isinstance(model, dict) or not isinstance(runtime, dict) or not isinstance(frozen, dict):
        raise ValueError("test plan is missing pinned model/runtime/input metadata")
    candidate = model.get("text_only_candidate")
    if (model.get("repo_id") != MODEL_REPO or model.get("revision") != MODEL_REVISION
            or not isinstance(candidate, dict) or candidate.get("filename") != MODEL_FILE
            or candidate.get("sha256") != MODEL_SHA256):
        raise ValueError("plan does not authorize the single pinned PTQ1_0 Bonsai candidate")
    if runtime.get("repo_url") != LLAMA_REPO or runtime.get("branch") != LLAMA_BRANCH or runtime.get("pinned_commit") != LLAMA_COMMIT:
        raise ValueError("plan does not match the pinned PrismML llama.cpp fork")
    prompt_contract = plan.get("inference_protocol", {}).get("prompt_and_validation")
    if (frozen.get("case_count") != 196 or frozen.get("inputs_sha256") != FROZEN_INPUT_SHA256
            or frozen.get("manifest_sha256") != FROZEN_MANIFEST_SHA256
            or not isinstance(prompt_contract, dict)
            or prompt_contract.get("reuse_existing_runner_sha256") != FROZEN_QWEN_RUNNER_SHA256):
        raise ValueError("plan does not identify the frozen 196-case Qwen prompt/input set")


def load_frozen_runner(path: Path):
    actual = sha256_file(path)
    if actual != FROZEN_QWEN_RUNNER_SHA256:
        raise ValueError(f"frozen prompt/validator runner hash mismatch: {actual}")
    spec = importlib.util.spec_from_file_location("frozen_qwen_sku_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load frozen Qwen runner module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("build_prompt", "candidate_pool", "validate_prediction"):
        if not callable(getattr(module, name, None)):
            raise ValueError(f"frozen runner is missing required function {name}")
    return module


def parse_proc_argv(raw: bytes) -> list[str]:
    """Decode argv without flattening JSON-valued arguments into shell text."""
    return [part.decode("utf-8", errors="strict") for part in raw.split(b"\0") if part]


def extract_proc_environment_value(raw: bytes, key: str) -> str | None:
    """Extract exactly one named variable; never materialize or log the full environment."""
    prefix = key.encode("ascii") + b"="
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix):].decode("utf-8", errors="strict")
    return None


def option_value(argv: list[str], *names: str) -> str | None:
    for index, arg in enumerate(argv):
        for name in names:
            if arg == name:
                return argv[index + 1] if index + 1 < len(argv) else None
            if arg.startswith(name + "="):
                return arg[len(name) + 1:]
    return None


def validate_server_argv(argv: list[str], expected_model_path: Path) -> dict[str, Any]:
    """Validate actual argv elements, including the JSON chat-template argument."""
    if not argv or Path(argv[0]).name not in {"llama-server", "llama-server.exe"}:
        raise ValueError("server PID argv is not llama-server")
    model_arg = option_value(argv, "-m", "--model")
    if model_arg is None or Path(model_arg).resolve() != expected_model_path.resolve():
        raise ValueError("live server argv does not use the pinned model path")
    if "--no-mmproj-auto" not in argv:
        raise ValueError("live server process must disable automatic vision projector loading")
    if option_value(argv, "-c", "--ctx-size") != "18432":
        raise ValueError("server context must be 18432 tokens to fit the 16000-token input plus output")
    if option_value(argv, "-np", "--parallel") != "1":
        raise ValueError("server must run one parallel slot for deterministic single-request inference")
    if option_value(argv, "-ngl", "--n-gpu-layers") != "99":
        raise ValueError("server process must request full GPU layer offload")
    if option_value(argv, "--split-mode") != "none" or option_value(argv, "--main-gpu") != "0":
        raise ValueError("server must keep the full model on one selected GPU (split-mode none, main-gpu 0)")
    kwargs_raw = option_value(argv, "--chat-template-kwargs")
    try:
        chat_kwargs = json.loads(kwargs_raw) if kwargs_raw is not None else None
    except json.JSONDecodeError as exc:
        raise ValueError("--chat-template-kwargs must be a valid JSON object") from exc
    if not isinstance(chat_kwargs, dict) or chat_kwargs.get("enable_thinking") is not False:
        raise ValueError("server must set enable_thinking=false in parsed chat-template kwargs")
    if option_value(argv, "--reasoning-budget") != "0" or option_value(argv, "--reasoning-format") != "none":
        raise ValueError("server process must explicitly disable reasoning output")
    return {"server_argv": argv, "chat_template_kwargs": chat_kwargs,
            "context_size": 18432, "parallel_slots": 1, "gpu_layers_requested": 99,
            "split_mode": "none", "main_gpu": 0, "thinking_enabled": False,
            "reasoning_budget": 0, "reasoning_format": "none"}


def read_proc_environment_value(pid: int, key: str) -> str | None:
    """Read one named environment value without exposing other process secrets."""
    env_path = Path("/proc") / str(pid) / "environ"
    return extract_proc_environment_value(env_path.read_bytes(), key)


def select_visible_gpu(inventory: dict[str, Any], visible_devices: str | None) -> dict[str, Any]:
    """Resolve CUDA_VISIBLE_DEVICES against host inventory and require one T4/L4."""
    devices = inventory.get("devices") if isinstance(inventory, dict) else None
    if not inventory.get("available") or not isinstance(devices, list) or not devices:
        raise ValueError("nvidia-smi host inventory is unavailable")
    if visible_devices is None:
        selected_tokens = [str(item.get("index")) for item in devices]
    elif not visible_devices.strip():
        selected_tokens = []
    else:
        selected_tokens = [part.strip() for part in visible_devices.split(",")]
    if len(selected_tokens) != 1:
        raise ValueError("server CUDA_VISIBLE_DEVICES must expose exactly one GPU")
    token = selected_tokens[0]
    selected = next((item for item in devices if token in {str(item.get("index")), str(item.get("uuid"))}), None)
    if selected is None:
        raise ValueError("server CUDA_VISIBLE_DEVICES does not map to an nvidia-smi device")
    if not re.search(r"Tesla T4|\bL4\b", str(selected.get("name", "")), re.I):
        raise ValueError("the selected GPU must be a Tesla T4 or L4")
    return selected


def preflight(input_dir: Path, plan_path: Path) -> dict[str, Any]:
    plan = read_json(plan_path)
    validate_pin_plan(plan)
    inputs_path = input_dir / "inputs.jsonl"
    manifest_path = input_dir / "manifest.json"
    config_path = input_dir / "config.json"
    runner_path = input_dir / "kaggle_gpu_sku_runner.py"
    for path in (inputs_path, manifest_path, config_path, runner_path):
        if not path.is_file():
            raise FileNotFoundError(f"required frozen input is missing: {path.name}")
    hashes = {"inputs": sha256_file(inputs_path), "manifest": sha256_file(manifest_path),
              "config": sha256_file(config_path), "qwen_runner": sha256_file(runner_path)}
    expected = {"inputs": FROZEN_INPUT_SHA256, "manifest": FROZEN_MANIFEST_SHA256,
                "config": FROZEN_CONFIG_SHA256, "qwen_runner": FROZEN_QWEN_RUNNER_SHA256}
    if hashes != expected:
        raise ValueError(f"frozen input bundle SHA-256 mismatch: observed={hashes}")
    manifest = read_json(manifest_path)
    config = read_json(config_path)
    if manifest.get("config_sha256") != hashes["config"]:
        raise ValueError("manifest config_sha256 does not match frozen config")
    if manifest.get("inputs_sha256") != hashes["inputs"] or manifest.get("input_count") != 196:
        raise ValueError("manifest does not match frozen 196 input records")
    models = config.get("models")
    if (not isinstance(models, list) or len(models) != 1 or models[0].get("name") != "Qwen/Qwen3.5-9B"
            or models[0].get("revision") != "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
            or models[0].get("load") != "nf4"):
        raise ValueError("frozen config differs from approved Qwen3.5-9B NF4 baseline config")
    cases = read_jsonl(inputs_path)
    ids = [case.get("case_id") for case in cases]
    if len(cases) != 196 or any(not isinstance(cid, str) or not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("frozen input must contain 196 unique nonempty case IDs")
    runner = load_frozen_runner(runner_path)
    for case in cases:
        runner.candidate_pool(case)
    return {"plan": plan, "config": config, "manifest": manifest, "cases": cases,
            "runner": runner, "hashes": hashes, "paths": {"input": inputs_path, "manifest": manifest_path,
            "config": config_path, "runner": runner_path}}


def validate_server_metadata(metadata: dict[str, Any], model_path: Path) -> dict[str, Any]:
    required = ("server_commit", "server_version", "server_pid", "model_sha256", "model_path", "startup_log",
                "server_binary_path", "server_binary_sha256")
    missing = [key for key in required if metadata.get(key) in (None, "")]
    if missing:
        raise ValueError(f"server metadata is incomplete: {missing}")
    if metadata["server_commit"] != LLAMA_COMMIT:
        raise ValueError("server metadata commit does not match pinned PrismML fork")
    if metadata["model_sha256"] != MODEL_SHA256 or Path(metadata["model_path"]).resolve() != model_path.resolve():
        raise ValueError("server model metadata does not match the pinned PTQ1_0 GGUF")
    if not model_path.is_file() or sha256_file(model_path) != MODEL_SHA256:
        raise ValueError("local GGUF is absent or its SHA-256 differs from the pinned model")
    binary_path = Path(str(metadata["server_binary_path"])).resolve()
    if not binary_path.is_file() or sha256_file(binary_path) != metadata["server_binary_sha256"]:
        raise ValueError("llama-server binary is missing or its recorded SHA-256 does not match")
    version_result = subprocess.run([str(binary_path), "--version"], capture_output=True, text=True, timeout=10)
    version_text = (version_result.stdout + version_result.stderr).strip()
    if version_result.returncode != 0 or LLAMA_COMMIT[:7] not in version_text:
        raise ValueError("running llama-server binary does not report the pinned fork commit")
    try:
        pid = int(metadata["server_pid"])
        proc_root = Path("/proc") / str(pid)
        proc_exe = (proc_root / "exe").resolve()
        proc_argv = parse_proc_argv((proc_root / "cmdline").read_bytes())
        cuda_visible_devices = read_proc_environment_value(pid, "CUDA_VISIBLE_DEVICES")
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not inspect the live llama-server process: {exc}") from exc
    if proc_exe != binary_path:
        raise ValueError("server PID is not running the verified llama-server binary")
    argv_facts = validate_server_argv(proc_argv, model_path)
    log_path = Path(str(metadata["startup_log"]))
    if not log_path.is_file():
        raise ValueError("server startup log is unavailable")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU", log_text, flags=re.IGNORECASE)
    if not matches or not any(int(loaded) > 0 and int(loaded) == int(total) for loaded, total in matches):
        raise ValueError("startup log does not prove complete GPU layer offload; CPU fallback is rejected")
    return {"server_commit": metadata["server_commit"], "server_version": version_text,
            "server_binary_sha256": metadata["server_binary_sha256"], "server_pid": metadata["server_pid"],
            "model_sha256": metadata["model_sha256"],
            "model_path": str(model_path.resolve()), "startup_log": str(log_path.resolve()),
            "cuda_visible_devices": cuda_visible_devices, "gpu_offload_log_matches": matches,
            "gpu_offload_log_lines": [line for line in log_text.splitlines()
                                      if re.search(r"offloaded\s+\d+\s*/\s*\d+\s+layers?\s+to\s+GPU", line, re.I)],
            **argv_facts,
            "startup_log_sha256": sha256_file(log_path)}


def nvidia_inventory() -> dict[str, Any]:
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,uuid", "--format=csv,noheader,nounits"],
                              check=True, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    devices = []
    for line in proc.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", 3)]
        if len(parts) == 4:
            devices.append({"index": parts[0], "name": parts[1], "memory_mib": parts[2], "uuid": parts[3]})
    return {"available": bool(devices), "devices": devices}


def check_server(client: ServerClient, timeout_seconds: float | None = None) -> dict[str, Any]:
    health_status, health, health_raw = client.json_request("GET", "/health", timeout_seconds=timeout_seconds)
    if health_status != 200 or health.get("status") != "ok":
        raise RuntimeError(f"llama-server is not ready: HTTP {health_status} {health_raw[:500]!r}")
    return {"health_http_status": health_status, "health_response": health}


def bounded_timeout(configured_timeout: float, deadline: float | None) -> float:
    configured_timeout = require_positive_finite(configured_timeout, "request timeout")
    if deadline is None:
        return configured_timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("overall runtime budget expired before server request")
    return min(configured_timeout, remaining)


def apply_template_and_count(client: ServerClient, messages: list[dict[str, str]],
                             timeout_seconds: float | None = None,
                             deadline: float | None = None) -> tuple[str, list[int], int, dict[str, Any]]:
    # These endpoint payloads follow the immutable pinned fork's server README.
    timeout = bounded_timeout(client.timeout_seconds if timeout_seconds is None else timeout_seconds, deadline)
    status, rendered, rendered_raw = client.json_request("POST", "/apply-template", {"messages": messages}, timeout)
    if status != 200 or not isinstance(rendered.get("prompt"), str):
        raise RuntimeError(f"/apply-template failed: HTTP {status} {rendered_raw[:500]!r}")
    prompt = rendered["prompt"]
    timeout = bounded_timeout(client.timeout_seconds if timeout_seconds is None else timeout_seconds, deadline)
    token_status, token_result, token_raw = client.json_request("POST", "/tokenize",
        {"content": prompt, "add_special": False, "parse_special": True, "with_pieces": False}, timeout)
    tokens = token_result.get("tokens")
    if token_status != 200 or not isinstance(tokens, list) or any(not isinstance(t, int) for t in tokens):
        raise RuntimeError(f"/tokenize returned invalid count: HTTP {token_status} {token_raw[:500]!r}")
    return prompt, tokens, len(tokens), {"apply_template": rendered, "tokenize": token_result}


def parse_response(raw_response: bytes, case: dict[str, Any], runner: Any) -> dict[str, Any]:
    try:
        response = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"raw_server_response": raw_response.decode("utf-8", errors="replace"),
                "raw_assistant_text": None, "parsed": None, "validation": {"valid": False,
                "error": f"invalid_server_json: {exc}"}}
    if not isinstance(response, dict):
        return {"raw_server_response": response, "raw_assistant_text": None, "parsed": None,
                "validation": {"valid": False, "error": "server_response_not_object"}}
    raw_text = response.get("content")
    if not isinstance(raw_text, str):
        return {"raw_server_response": response, "raw_assistant_text": None, "parsed": None,
                "validation": {"valid": False, "error": "missing_content_string"}}
    checked = runner.validate_prediction(raw_text, case)
    return {"raw_server_response": response, "raw_assistant_text": raw_text,
            "parsed": checked.get("prediction"), "validation": checked,
            "generation_stop_type": response.get("stop_type"),
            "generation_truncated": response.get("stop_type") == "limit",
            "output_token_count": len(response["tokens"]) if isinstance(response.get("tokens"), list) else None,
            "review_fallback": None if checked["valid"] else {"decision": "review", "au_row_key": None,
                "reason": f"Invalid model output retained for review: {checked.get('error')}", "evidence": []}}


def finalize(output_dir: Path, runtime: dict[str, Any], summary: dict[str, Any], run_manifest: dict[str, Any]) -> None:
    runtime_path = output_dir / "runtime.json"
    summary_path = output_dir / "summary.json"
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = {path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
             for path in sorted(output_dir.iterdir()) if path.is_file() and path.name != "run-manifest.json"}
    run_manifest["files"] = files
    (output_dir / "run-manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(input_dir: Path, output_dir: Path, server_url: str, plan_path: Path,
            metadata_path: Path, model_path: Path, overall_timeout: float | None = None,
            request_timeout: float = 120.0) -> int:
    request_timeout = require_positive_finite(request_timeout, "request timeout")
    if overall_timeout is not None:
        overall_timeout = require_positive_finite(overall_timeout, "overall timeout")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    wall_budget = 7200.0
    state: dict[str, Any] = {"started_at": utc_now(), "status": "preflight", "labels_used": False,
                             "model_repo": MODEL_REPO, "model_revision": MODEL_REVISION,
                             "model_file": MODEL_FILE, "model_sha256": MODEL_SHA256,
                             "server_commit_expected": LLAMA_COMMIT, "server_commit_actual": None,
                             "input_hashes": None, "completed": 0, "errors": [],
                             "smoke_test": {"status": "pending"}}
    per_case_path = output_dir / "predictions-bonsai2-ptq1-0.jsonl"
    cases: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    exit_code = 2
    try:
        bundle = preflight(input_dir, plan_path)
        cases = bundle["cases"]
        state["input_hashes"] = bundle["hashes"]
        config = bundle["config"]
        wall_budget = require_positive_finite(config.get("runtime_budget_seconds", 7200), "configured runtime budget")
        if overall_timeout is not None:
            wall_budget = min(wall_budget, overall_timeout)
        metadata = validate_server_metadata(read_json(metadata_path), model_path)
        state["server"] = metadata
        state["server_commit_actual"] = metadata["server_commit"]
        state["gpu_inventory"] = nvidia_inventory()
        selected_gpu = select_visible_gpu(state["gpu_inventory"], metadata["cuda_visible_devices"])
        state["selected_gpu"] = selected_gpu
        client = ServerClient(server_url, request_timeout)
        state["server_health"] = check_server(client, bounded_timeout(request_timeout, started + wall_budget))
        state["status"] = "running"
        max_input = int(config.get("max_input_tokens", 16000))
        max_new = int(config.get("max_new_tokens", 384))
        smoke_failed = False
        with per_case_path.open("w", encoding="utf-8") as output:
            for index, case in enumerate(cases):
                row: dict[str, Any] = {"case_id": case["case_id"], "dossier_id": case.get("dossier_id"),
                    "split": case.get("split"), "index": index, "model": MODEL_REPO,
                    "model_revision": MODEL_REVISION, "input_sha256": bundle["hashes"]["inputs"]}
                elapsed = time.monotonic() - started
                if index > 0 and smoke_failed:
                    row.update(status="skipped_smoke_failed", error="one-case generation smoke did not complete successfully")
                elif elapsed >= wall_budget:
                    row.update(status="skipped_runtime_budget", error=f"overall_budget_seconds={wall_budget}")
                else:
                    try:
                        prompt = bundle["runner"].build_prompt(case)
                        template_details: dict[str, Any] = {}
                        deadline = started + wall_budget
                        # Both preflight APIs are part of the pinned server README.
                        _rendered, token_ids, input_tokens, template_details = apply_template_and_count(
                            client, [{"role": "user", "content": prompt}], request_timeout, deadline)
                        row["input_tokens"] = input_tokens
                        if input_tokens > max_input:
                            row.update(status="invalid_input_over_limit", error=f"input_tokens={input_tokens} limit={max_input}")
                        elif time.monotonic() - started >= wall_budget:
                            row.update(status="skipped_runtime_budget", error=f"overall_budget_seconds={wall_budget}")
                        else:
                            remaining = bounded_timeout(request_timeout, deadline)
                            body = {"prompt": token_ids, "n_predict": max_new, "temperature": 0,
                                    "top_k": 1, "seed": 1, "stream": False, "cache_prompt": False,
                                    "return_tokens": True}
                            status, raw_response = client.request("POST", "/completion", body,
                                                                  remaining)
                            row["raw_server_http_status"] = status
                            row["template_and_tokenization"] = template_details
                            parsed = parse_response(raw_response, case, bundle["runner"])
                            row.update(parsed)
                            if status < 200 or status >= 300:
                                row.update(status="http_error", error=f"HTTP {status}")
                            elif not parsed["validation"]["valid"]:
                                row.update(status="invalid_output", error=parsed["validation"]["error"])
                            else:
                                row["status"] = "ok"
                            if index == 0:
                                assistant_text = parsed.get("raw_assistant_text")
                                smoke_ok = status == 200 and isinstance(assistant_text, str) and bool(assistant_text.strip())
                                state["smoke_test"] = {"status": "passed" if smoke_ok else "failed",
                                    "http_status": status, "generation_content_received": bool(assistant_text.strip()) if isinstance(assistant_text, str) else False,
                                    "case_id": row["case_id"]}
                                smoke_failed = not smoke_ok
                    except (TimeoutError, URLError, OSError) as exc:
                        row.update(status="request_error", error=f"{type(exc).__name__}: {exc}")
                    except Exception as exc:
                        row.update(status="error", error=f"{type(exc).__name__}: {exc}")
                if index == 0 and state["smoke_test"]["status"] == "pending":
                    state["smoke_test"] = {"status": "failed", "case_id": row["case_id"],
                        "reason": f"first case did not reach generation ({row['status']})"}
                    smoke_failed = True
                counts[row["status"]] = counts.get(row["status"], 0) + 1
                if row["status"] != "ok" and "review_fallback" not in row:
                    row["review_fallback"] = {"decision": "review", "au_row_key": None,
                        "reason": f"No validated model decision: {row.get('error', row['status'])}", "evidence": []}
                if row["status"] in {"error", "request_error", "http_error"}:
                    state["errors"].append({"case_id": row["case_id"], "status": row["status"], "error": row.get("error")})
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                state["completed"] += 1
                if (index + 1) % 16 == 0 or index + 1 == len(cases):
                    print(f"Bonsai2: case {index + 1}/{len(cases)}", flush=True)
        path_keys = {"inputs": "input", "manifest": "manifest", "config": "config", "qwen_runner": "runner"}
        final_hashes = {key: sha256_file(bundle["paths"][path_keys[key]]) for key in bundle["hashes"]}
        state["input_hashes_after"] = final_hashes
        state["input_unchanged"] = final_hashes == bundle["hashes"]
        state["counts"] = counts
        if smoke_failed:
            state["status"] = "smoke_failed"
        else:
            state["status"] = "complete" if state["completed"] == len(cases) and not counts.get("skipped_runtime_budget") else "partial"
        if not state["input_unchanged"]:
            state["status"] = "input_changed_during_run"
        exit_code = 0 if state["status"] == "complete" else 2
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["errors"].append({"stage": state.get("status"), "error": state["error"]})
    finally:
        if not per_case_path.exists():
            per_case_path.write_text("", encoding="utf-8")
        output_sha = sha256_file(per_case_path)
        state["finished_at"] = utc_now()
        state["elapsed_seconds"] = time.monotonic() - started
        state["output_file"] = per_case_path.name
        state["output_sha256"] = output_sha
        if not counts and per_case_path.stat().st_size:
            for line in per_case_path.read_text(encoding="utf-8").splitlines():
                try:
                    status = json.loads(line).get("status", "unknown")
                except Exception:
                    status = "corrupt_record"
                counts[status] = counts.get(status, 0) + 1
        summary = {"status": state["status"], "case_count": len(cases) or 196,
                   "completed": state.get("completed", 0), "counts": counts,
                   "labels_used": False, "model": {"repo_id": MODEL_REPO, "revision": MODEL_REVISION,
                   "file": MODEL_FILE, "sha256": MODEL_SHA256}, "errors": state.get("errors", [])}
        manifest = {"created_at": state["started_at"], "status": state["status"], "labels_used": False,
                    "frozen_input_hashes": state.get("input_hashes"), "case_count": len(cases) or 196,
                    "model": summary["model"], "server": state.get("server"), "model_results": state,
                    "files": {}}
        runtime = {"started_at": state["started_at"], "finished_at": state["finished_at"],
                   "elapsed_seconds": state["elapsed_seconds"], "wall_clock_budget_seconds": wall_budget,
                   "request_timeout_seconds": request_timeout, "status": state["status"],
                   "gpu_inventory": state.get("gpu_inventory"), "selected_gpu": state.get("selected_gpu"),
                   "server": state.get("server"),
                   "server_health": state.get("server_health"), "input_hashes": state.get("input_hashes"),
                   "input_unchanged": state.get("input_unchanged"), "labels_used": False}
        finalize(output_dir, runtime, summary, manifest)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--server-url", default="http://127.0.0.1:8080")
    parser.add_argument("--server-metadata", required=True, type=Path,
                        help="bootstrap-created metadata containing actual server commit, model hash, GPU, command and log")
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--plan", type=Path, default=PLAN_DEFAULT)
    parser.add_argument("--overall-timeout-seconds", type=float)
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args(argv)
    return execute(args.input_dir, args.output_dir, args.server_url, args.plan,
                   args.server_metadata, args.model_path, args.overall_timeout_seconds,
                   args.request_timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
