import contextlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kaggle_ops", ROOT / ".agents/skills/kaggle-ops/scripts/kaggle_ops.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class KaggleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "notebook"
        self.folder.mkdir()
        self.metadata = {"id": "owner/notebook", "code_file": "run.py", "is_private": True, "enable_gpu": True, "enable_tpu": False}
        self.write_metadata()
        (self.folder / "run.py").write_text("print('done')", encoding="utf-8")
        ops.save(self.folder / "training-params.json", {"timeout_seconds": 600})
        self.output = self.root / "results"
        self.job = {"ref": "owner/notebook", "version": 7, "state": "submitted"}
        latest = patch.object(ops, "latest_version", return_value=0)
        latest.start()
        self.addCleanup(latest.stop)

    def write_metadata(self):
        ops.save(self.folder / "kernel-metadata.json", self.metadata)

    def client(self, api):
        return SimpleNamespace(build_kaggle_client=lambda: contextlib.nullcontext(SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=api))))

    def test_utf8_open_preserves_binary_and_positional_encoding(self):
        path = self.root / "text"
        with ops.utf8_open(path, "w") as handle:
            handle.write("日本語")
        with ops.utf8_open(path, "r", -1, "utf-8") as handle:
            self.assertEqual(handle.read(), "日本語")
        with ops.utf8_open(path, "rb") as handle:
            self.assertEqual(handle.read(), "日本語".encode())

    def test_submit_persists_unknown_before_network_and_never_retries(self):
        def push(*args, **kwargs):
            self.assertEqual(ops.load(self.output / "job.json")["state"], "submission_unknown")
            raise requests.Timeout()
        client = SimpleNamespace(kernels_push=Mock(side_effect=push))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}):
            with self.assertRaises(requests.Timeout):
                ops.submit(client, self.folder, self.output)
            with self.assertRaisesRegex(ValueError, "already exists"):
                ops.submit(client, self.folder, self.output)
        self.assertEqual(client.kernels_push.call_count, 1)

    def test_submit_pins_returned_version(self):
        client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="owner/notebook", version_number=7, error="")))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}):
            result = ops.submit(client, self.folder, self.output)
        self.assertEqual(result["version"], 7)
        client.kernels_push.assert_called_once_with(str(self.folder), timeout="600")

    def test_unknown_quota_and_unsafe_uploads_block_submission(self):
        client = Mock()
        with patch.object(ops, "quota", return_value={"gpu": None}):
            with self.assertRaisesRegex(ValueError, "unknown GPU quota"):
                ops.submit(client, self.folder, self.output)
        (self.folder / ".env.local").write_text("dummy", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "credentials"):
            ops.submit(client, self.folder, self.output)
        client.kernels_push.assert_not_called()

    def test_status_uses_pinned_version(self):
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusResponse
        response = ApiGetKernelSessionStatusResponse()
        api = SimpleNamespace(get_kernel_session_status=Mock(return_value=response))
        ops.status(self.client(api), self.job["ref"], 7)
        request = api.get_kernel_session_status.call_args.args[0]
        self.assertEqual(request.version_label, "v7")

    def test_paginated_outputs_remain_on_version_and_reject_traversal(self):
        calls = []
        def listing(request):
            calls.append((request.version_label, request.page_token))
            if len(calls) == 1:
                return SimpleNamespace(files=[], log="finished", next_page_token="page2")
            return SimpleNamespace(files=[SimpleNamespace(file_name="../escape", url="https://example.org/out")], log="", next_page_token="")
        with self.assertRaisesRegex(ValueError, "escapes"):
            ops.outputs(self.client(SimpleNamespace(list_kernel_session_output=listing)), self.job, self.output)
        self.assertEqual([c[0] for c in calls], ["v7", "v7"])
        self.assertEqual(calls[1][1], "page2")

    def test_outputs_hash_files_and_do_not_forward_credentials(self):
        response = SimpleNamespace(files=[SimpleNamespace(file_name="nested/result.txt", url="https://example.org/file")], log="done", next_page_token="")
        content = Mock()
        content.__enter__ = Mock(return_value=content)
        content.__exit__ = Mock(return_value=False)
        content.iter_content.return_value = [b"abc"]
        with patch("requests.get", return_value=content) as get:
            result = ops.outputs(self.client(SimpleNamespace(list_kernel_session_output=lambda r: response)), self.job, self.output)
        self.assertEqual(result[0]["bytes"], 3)
        self.assertEqual(result[0]["sha256"], ops.hashlib.sha256(b"abc").hexdigest())
        self.assertNotIn("headers", get.call_args.kwargs)

    def test_log_stream_pins_version_and_redacts_token(self):
        response = Mock(headers={"content-type": "text/event-stream"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"data: fake-secret\ndata: END_OF_LOG\n"]
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "fake-secret"}), patch("requests.get", return_value=response) as get:
            ops.log_slice(self.job, self.output)
        self.assertEqual(get.call_args.kwargs["params"], {"versionLabel": "v7"})
        self.assertNotIn("fake-secret", (self.output / "session.log").read_text())

    def test_complete_recovers_outputs_before_continuation(self):
        ops.save(self.output / "job.json", self.job)
        with patch.object(ops, "status", return_value={"status": "complete"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]) as outputs:
            result = ops.wait(Mock(), self.output / "job.json", self.output, 1)
        outputs.assert_called_once()
        self.assertTrue(result["ready_for_verification"])
        self.assertEqual(ops.load(self.output / "continuation.json")["next_phase"], "verification")

    def test_failure_and_timeout_never_resubmit(self):
        ops.save(self.output / "job.json", self.job)
        client = Mock()
        with patch.object(ops, "status", return_value={"status": "error"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]) as outputs:
            result = ops.wait(client, self.output / "job.json", self.output, 1)
        self.assertEqual(result["next_phase"], "failure_review")
        self.assertFalse(outputs.call_args.kwargs["download"])
        with patch.object(ops, "status", return_value={"status": "running"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops.time, "monotonic", side_effect=[0, 2, 2]):
            result = ops.wait(client, self.output / "job.json", self.output, 1)
        self.assertEqual(result["status"], "waiting_timeout")
        self.assertEqual(ops.load(self.output / "job.json"), self.job)
        client.kernels_push.assert_not_called()

    def test_read_retry_is_bounded_and_does_not_retry_auth(self):
        read = Mock(side_effect=requests.Timeout())
        with patch.object(ops.time, "sleep"), self.assertRaises(requests.Timeout):
            ops.read_retry(read)
        self.assertEqual(read.call_count, 3)
        response = requests.Response()
        response.status_code = 401
        read = Mock(side_effect=requests.HTTPError(response=response))
        with self.assertRaises(requests.HTTPError):
            ops.read_retry(read)
        self.assertEqual(read.call_count, 1)

    def test_cloud_rerun_submit_is_rejected(self):
        with patch.dict(os.environ, {"KAGGLE_JOB_ACTION": "submit", "GITHUB_RUN_ATTEMPT": "2"}), patch.object(ops, "submit") as submit:
            with self.assertRaisesRegex(ValueError, "do not rerun"):
                ops.cloud_run(Mock(), self.output)
        submit.assert_not_called()

    def test_active_remote_job_blocks_new_output_directory(self):
        client = Mock()
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "latest_version", return_value=6), patch.object(ops, "status", return_value={"status": "running"}):
            with self.assertRaisesRegex(ValueError, "active"):
                ops.submit(client, self.folder, self.output)
        client.kernels_push.assert_not_called()

    def test_embedded_credential_blocks_upload(self):
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "fake-secret"}):
            (self.folder / "run.py").write_text("token = 'fake-secret'", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "configured credential"):
                ops.clean_upload(self.folder)

    def test_reconciliation_requires_newer_version_and_preserves_identity(self):
        job = dict(self.job, state="submission_unknown", previous_version=6, code_sha256="digest")
        ops.save(self.output / "job.json", job)
        with self.assertRaises(ValueError):
            ops.reconcile(Mock(), self.output / "job.json", 6)
        with patch.object(ops, "status", return_value={"status": "running"}):
            result = ops.reconcile(Mock(), self.output / "job.json", 7)
        self.assertEqual(result["code_sha256"], "digest")
        self.assertEqual(result["state"], "submitted")


if __name__ == "__main__":
    unittest.main()
