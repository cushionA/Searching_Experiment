from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "experiments/sku-matching/kaggle_bonsai_sku_runner.py"
SPEC = importlib.util.spec_from_file_location("bonsai_sku_runner_tested", RUNNER_PATH)
assert SPEC and SPEC.loader
bonsai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bonsai)

FROZEN_DIR = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload"
PLAN = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v5/bonsai2-t4-test-plan.json"


class MockServerHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []

    def log_message(self, *_args):
        pass

    def _send(self, status: int, body: dict | str):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.calls.append((self.path, {}))
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "unknown route"})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append((self.path, data))
        if self.path == "/apply-template":
            self._send(200, {"prompt": "rendered prompt with generation marker"})
        elif self.path == "/tokenize":
            self._send(200, {"tokens": [11, 22, 33]})
        elif self.path == "/completion":
            # Exercise raw-response retention and strict rejection without simulating a model judgment.
            self._send(200, {"content": "not JSON", "stop_type": "limit", "tokens": [91, 92]})
        else:
            self._send(404, {"error": "unknown route"})


class BonsaiRunnerTests(unittest.TestCase):
    def test_plan_and_frozen_bundle_pins(self):
        plan = bonsai.read_json(PLAN)
        bonsai.validate_pin_plan(plan)
        bundle = bonsai.preflight(FROZEN_DIR, PLAN)
        self.assertEqual(len(bundle["cases"]), 196)
        self.assertEqual(bundle["hashes"]["inputs"], bonsai.FROZEN_INPUT_SHA256)
        self.assertEqual(bundle["hashes"]["config"], bonsai.FROZEN_CONFIG_SHA256)
        self.assertEqual(bundle["hashes"]["qwen_runner"], bonsai.FROZEN_QWEN_RUNNER_SHA256)
        self.assertEqual(bundle["hashes"]["manifest"], bonsai.FROZEN_MANIFEST_SHA256)
        self.assertTrue(all(bundle["runner"].candidate_pool(case) for case in bundle["cases"]))

    def test_plan_rejects_unpinned_model_or_fork(self):
        plan = bonsai.read_json(PLAN)
        plan["model"]["text_only_candidate"]["filename"] = "other.gguf"
        with self.assertRaises(ValueError):
            bonsai.validate_pin_plan(plan)
        plan = bonsai.read_json(PLAN)
        plan["runtime"]["pinned_commit"] = "0" * 40
        with self.assertRaises(ValueError):
            bonsai.validate_pin_plan(plan)

    def test_preflight_rejects_modified_frozen_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "bundle"
            copied.mkdir()
            for name in ("inputs.jsonl", "manifest.json", "config.json", "kaggle_gpu_sku_runner.py"):
                shutil.copy2(FROZEN_DIR / name, copied / name)
            config_path = copied / "config.json"
            config_path.write_text(config_path.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "frozen input bundle SHA-256 mismatch"):
                bonsai.preflight(copied, PLAN)

    def test_server_metadata_rejects_nonpinned_commit_before_claiming_gpu(self):
        metadata = {"server_commit": "0" * 40, "server_version": "x", "server_pid": 1,
                    "model_sha256": "x", "model_path": "x", "startup_log": "x",
                    "server_binary_path": "x", "server_binary_sha256": "x", "gpu_names": [],
                    "thinking_enabled": False, "reasoning_budget": 0, "reasoning_format": "none"}
        with self.assertRaisesRegex(ValueError, "commit does not match"):
            bonsai.validate_server_metadata(metadata, Path("absent.gguf"))

    def test_actual_argv_parser_accepts_json_kwargs_as_one_quoted_argument(self):
        model_path = Path("/models/pinned.gguf")
        argv = ["/opt/bin/llama-server", "-m", str(model_path), "--no-mmproj-auto",
                "-ngl", "99", "-c", "18432", "-np", "1", "--split-mode", "none",
                "--main-gpu", "0", "--reasoning-budget", "0", "--reasoning-format", "none",
                "--chat-template-kwargs", '{"enable_thinking":false}']
        facts = bonsai.validate_server_argv(argv, model_path)
        self.assertFalse(facts["thinking_enabled"])
        self.assertEqual(facts["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(bonsai.parse_proc_argv(b"/x/llama-server\0--chat-template-kwargs\0{\"enable_thinking\":false}\0"),
                         ["/x/llama-server", "--chat-template-kwargs", '{"enable_thinking":false}'])
        argv[-1] = '{"enable_thinking":true}'
        with self.assertRaisesRegex(ValueError, "enable_thinking=false"):
            bonsai.validate_server_argv(argv, model_path)

    def test_only_cuda_visibility_value_is_extracted_from_proc_environment(self):
        environ = b"KAGGLE_API_TOKEN=do-not-disclose\0CUDA_VISIBLE_DEVICES=0\0PATH=/bin\0"
        self.assertEqual(bonsai.extract_proc_environment_value(environ, "CUDA_VISIBLE_DEVICES"), "0")
        self.assertIsNone(bonsai.extract_proc_environment_value(environ, "MISSING"))

    def test_single_visible_gpu_selection_allows_two_t4_host_but_rejects_two_visible(self):
        inventory = {"available": True, "devices": [
            {"index": "0", "name": "Tesla T4", "uuid": "GPU-a"},
            {"index": "1", "name": "Tesla T4", "uuid": "GPU-b"},
        ]}
        self.assertEqual(bonsai.select_visible_gpu(inventory, "0"), inventory["devices"][0])
        self.assertEqual(bonsai.select_visible_gpu(inventory, "GPU-b"), inventory["devices"][1])
        with self.assertRaisesRegex(ValueError, "exactly one GPU"):
            bonsai.select_visible_gpu(inventory, "0,1")
        with self.assertRaisesRegex(ValueError, "exactly one GPU"):
            bonsai.select_visible_gpu(inventory, None)
        with self.assertRaisesRegex(ValueError, "does not map"):
            bonsai.select_visible_gpu(inventory, "2")

    def test_request_deadlines_must_be_finite_and_positive(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bonsai.ServerClient("http://127.0.0.1:8080", value)

    def test_loopback_only_and_documented_template_tokenize_payloads(self):
        with self.assertRaises(ValueError):
            bonsai.ServerClient("https://example.com")
        global_opener = urllib.request._opener
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockServerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            MockServerHandler.calls.clear()
            client = bonsai.ServerClient(f"http://127.0.0.1:{server.server_port}")
            self.assertIs(urllib.request._opener, global_opener)
            health = bonsai.check_server(client)
            self.assertEqual(health["health_http_status"], 200)
            prompt, token_ids, count, _details = bonsai.apply_template_and_count(
                client, [{"role": "user", "content": "frozen prompt"}])
            self.assertEqual(prompt, "rendered prompt with generation marker")
            self.assertEqual(token_ids, [11, 22, 33])
            self.assertEqual(count, 3)
            self.assertEqual(MockServerHandler.calls[1][0], "/apply-template")
            self.assertEqual(MockServerHandler.calls[2][0], "/tokenize")
            self.assertEqual(MockServerHandler.calls[2][1]["add_special"], False)
            self.assertIs(urllib.request._opener, global_opener)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_strict_parser_uses_existing_validator_and_preserves_server_body(self):
        bundle = bonsai.preflight(FROZEN_DIR, PLAN)
        case = bundle["cases"][0]
        parsed = bonsai.parse_response(b'{"content":"not JSON","stop":true}', case, bundle["runner"])
        self.assertEqual(parsed["raw_server_response"]["content"], "not JSON")
        self.assertEqual(parsed["raw_assistant_text"], "not JSON")
        self.assertFalse(parsed["validation"]["valid"])
        self.assertTrue(parsed["validation"]["error"].startswith("invalid_json:"))
        malformed = bonsai.parse_response(b"{", case, bundle["runner"])
        self.assertIsNone(malformed["raw_assistant_text"])
        self.assertFalse(malformed["validation"]["valid"])

    def test_frozen_validator_requires_literal_quotes_from_both_sides(self):
        bundle = bonsai.preflight(FROZEN_DIR, PLAN)
        case = bundle["cases"][0]
        pool = bundle["runner"].candidate_pool(case)
        rk_quote = case["rakuten"]["title"]
        au_quote = case["au"]["title"]
        prediction = {"decision": "matched", "au_row_key": pool[0]["row_key"], "reason": "structural validator fixture",
                      "evidence": [{"side": "rakuten", "quote": rk_quote}, {"side": "au", "quote": au_quote}]}
        self.assertTrue(bundle["runner"].validate_prediction(json.dumps(prediction, ensure_ascii=False), case)["valid"])
        prediction["evidence"][1]["quote"] = "not a source quote"
        self.assertFalse(bundle["runner"].validate_prediction(json.dumps(prediction), case)["valid"])

    def test_execute_flushes_failures_and_writes_complete_manifest(self):
        bundle = bonsai.preflight(FROZEN_DIR, PLAN)
        # Restrict this client-contract test to one existing source-backed record; no scoring is performed.
        bundle["cases"] = bundle["cases"][:1]
        metadata = {"server_commit": bonsai.LLAMA_COMMIT, "server_version": "mock-pinned-build",
                    "server_pid": 123, "model_sha256": bonsai.MODEL_SHA256, "model_path": "/mock/model.gguf",
                    "startup_log": "/mock/server.log", "server_command": "llama-server --no-mmproj-auto --chat-template-kwargs '{\"enable_thinking\":false}'",
                    "cuda_visible_devices": "0", "thinking_enabled": False,
                    "reasoning_budget": 0, "reasoning_format": "none"}
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockServerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with self.subTest("manifest completion after strict parse error"), patch.object(bonsai, "preflight", return_value=bundle), \
                patch.object(bonsai, "validate_server_metadata", return_value=metadata), \
                patch.object(bonsai, "nvidia_inventory", return_value={"available": True, "devices": [
                    {"index": "0", "name": "Tesla T4", "uuid": "GPU-a"},
                    {"index": "1", "name": "Tesla T4", "uuid": "GPU-b"}]}), \
                patch.object(bonsai, "check_server", wraps=bonsai.check_server):
            # Patch only the external file hash check: no model file is downloaded or read.
            with patch.object(bonsai, "sha256_file", side_effect=lambda p: "0b5e5c9cbe41d56b36d5207e2f19ecaefe2f78fd149cc7e648ee1624a9408814" if Path(p).name == "inputs.jsonl" else hashlib.sha256(Path(p).read_bytes()).hexdigest()):
                # Use a temporary directory for all generated result artifacts.
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "result"
                    metadata_path = Path(tmp) / "metadata.json"
                    metadata_path.write_text(json.dumps(metadata))
                    code = bonsai.execute(FROZEN_DIR, out, f"http://127.0.0.1:{server.server_port}",
                                          PLAN, metadata_path, Path("unused.gguf"), overall_timeout=30)
                    self.assertEqual(code, 0)
                    rows = [json.loads(line) for line in (out / "predictions-bonsai2-ptq1-0.jsonl").read_text().splitlines()]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["status"], "invalid_output")
                    self.assertEqual(rows[0]["raw_assistant_text"], "not JSON")
                    self.assertEqual(rows[0]["generation_stop_type"], "limit")
                    self.assertTrue(rows[0]["generation_truncated"])
                    self.assertEqual(rows[0]["output_token_count"], 2)
                    self.assertEqual(rows[0]["review_fallback"]["decision"], "review")
                    manifest = json.loads((out / "run-manifest.json").read_text())
                    self.assertEqual(manifest["status"], "complete")
                    self.assertEqual(manifest["model_results"]["smoke_test"]["status"], "passed")
                    self.assertEqual(manifest["model_results"]["selected_gpu"]["uuid"], "GPU-a")
                    self.assertIn("predictions-bonsai2-ptq1-0.jsonl", manifest["files"])
                    self.assertIn("summary.json", manifest["files"])
                    self.assertIn("runtime.json", manifest["files"])
                    completion = next(body for path, body in MockServerHandler.calls if path == "/completion")
                    self.assertEqual(completion["prompt"], [11, 22, 33])
                    self.assertEqual(completion["temperature"], 0)
                    self.assertEqual(completion["top_k"], 1)
                    self.assertNotIn("json_schema", completion)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
