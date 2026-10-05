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
        self.metadata = {"id": "owner/notebook", "code_file": "run.py", "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_tpu": False, "enable_internet": False}
        self.write_metadata()
        (self.folder / "run.py").write_text("print('done')", encoding="utf-8")
        ops.save(self.folder / "training-params.json", {"timeout_seconds": 600})
        self.output = self.root / "results"
        self.job = {"ref": "owner/notebook", "version": 7, "state": "submitted"}
        self.latest = patch.object(ops, "latest_version", return_value=0)
        self.latest.start()
        self.addCleanup(self.latest.stop)

    def write_metadata(self):
        ops.save(self.folder / "kernel-metadata.json", self.metadata)

    def client(self, api):
        return SimpleNamespace(build_kaggle_client=lambda: contextlib.nullcontext(SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=api))))

    def remote(self, version=7, source="print('done')", private=True):
        return SimpleNamespace(metadata=SimpleNamespace(current_version_number=version, is_private=private, enable_gpu=True, enable_internet=False),
                               blob=SimpleNamespace(kernel_type="script", source=source))

    def test_config_directory_is_writable_without_using_home_or_saving_credentials(self):
        directory = self.root / "config"
        with patch.dict(os.environ, {"KAGGLE_CONFIG_DIR": str(directory), "KAGGLE_API_TOKEN": "fake-secret"}):
            self.assertEqual(ops.configure(), str(directory))
        self.assertTrue(directory.is_dir())
        self.assertEqual(list(directory.iterdir()), [])
        with patch.dict(os.environ, {}, clear=True):
            configured = Path(ops.configure())
            self.assertEqual(configured, ROOT / ".deps/kaggle-config")
            self.assertEqual(os.environ["KAGGLE_CONFIG_DIR"], str(configured))

    def test_new_own_notebook_403_checks_all_listing_pages(self):
        self.latest.stop()
        response = requests.Response()
        response.status_code = 403
        client = SimpleNamespace(config_values={"username": "owner"}, kernels_list=Mock(side_effect=[
            [SimpleNamespace(ref="owner/other")], []]))
        with patch.object(ops, "get_kernel", side_effect=requests.HTTPError(response=response)):
            self.assertEqual(ops.latest_version(client, "owner/notebook"), 0)
        self.assertEqual([call.kwargs["page"] for call in client.kernels_list.call_args_list], [1, 2])

    def test_existing_or_other_owners_403_remains_a_permission_failure(self):
        self.latest.stop()
        response = requests.Response()
        response.status_code = 403
        client = SimpleNamespace(config_values={"username": "owner"}, kernels_list=Mock(return_value=[SimpleNamespace(ref="owner/notebook")]))
        with patch.object(ops, "get_kernel", side_effect=requests.HTTPError(response=response)):
            with self.assertRaises(requests.HTTPError):
                ops.latest_version(client, "owner/notebook")
            client.kernels_list.reset_mock()
            with self.assertRaises(requests.HTTPError):
                ops.latest_version(client, "another/notebook")
        client.kernels_list.assert_not_called()

    def test_missing_submission_version_recovers_matching_source_without_resubmission(self):
        client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="owner/notebook", version_number=0, error="")))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "get_kernel", return_value=self.remote()):
            result = ops.submit(client, self.folder, self.output)
        self.assertEqual(result["version"], 7)
        self.assertTrue(result["source_verified"])
        self.assertEqual(result["submission_response"]["version"], 0)
        client.kernels_push.assert_called_once()

    def test_error_response_with_saved_matching_source_is_reconciled_once(self):
        client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="owner/notebook", version_number=7, error="ambiguous response")))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "get_kernel", return_value=self.remote()):
            result = ops.submit(client, self.folder, self.output)
        self.assertEqual(result["state"], "submitted")
        self.assertTrue(result["source_verified"])
        client.kernels_push.assert_called_once()

    def test_unmatched_source_or_privacy_keeps_submission_unknown_and_never_retries(self):
        for remote in (self.remote(source="print('different')"), self.remote(private=False)):
            output = self.root / ("mismatch" if remote.metadata.is_private else "public")
            client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="owner/notebook", version_number=0, error="")))
            with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "get_kernel", return_value=remote):
                with self.assertRaises(ValueError):
                    ops.submit(client, self.folder, output)
                self.assertEqual(ops.load(output / "job.json")["state"], "submission_unknown")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    ops.submit(client, self.folder, output)
            client.kernels_push.assert_called_once()

    def test_notebook_source_signature_ignores_server_output_but_checks_executed_code(self):
        notebook = {"cells": [{"cell_type": "code", "source": ["print('done')\n"], "outputs": []}]}
        signature = ops.source_signature(json.dumps(notebook), "notebook")
        notebook["cells"][0].update(source="print('done')\n", outputs=[{"output_type": "stream", "text": "done"}])
        self.assertEqual(ops.source_signature(json.dumps(notebook), "notebook"), signature)
        notebook["cells"][0]["source"] = "print('different')\n"
        self.assertNotEqual(ops.source_signature(json.dumps(notebook), "notebook"), signature)

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
        client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="/code/owner/notebook", version_number=7, error="")))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}):
            result = ops.submit(client, self.folder, self.output)
        self.assertEqual(result["version"], 7)
        self.assertEqual(result["ref"], "owner/notebook")
        self.assertEqual(result["submission_response"]["ref"], "/code/owner/notebook")
        client.kernels_push.assert_called_once_with(str(self.folder), timeout="600")

    def test_unexpected_submission_ref_stays_unknown_and_never_resubmits(self):
        client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="/code/another/notebook", version_number=7, error="")))
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}):
            with self.assertRaisesRegex(ValueError, "unexpected ref"):
                ops.submit(client, self.folder, self.output)
            with self.assertRaisesRegex(ValueError, "already exists"):
                ops.submit(client, self.folder, self.output)
        self.assertEqual(ops.load(self.output / "job.json")["state"], "submission_unknown")
        client.kernels_push.assert_called_once()

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
        clock = [0]
        with patch.object(ops, "status", return_value={"status": "running"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops.time, "monotonic", side_effect=lambda: clock[0]), patch.object(ops.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = ops.wait(client, self.output / "job.json", self.output, 1)
        self.assertEqual(result["status"], "waiting_timeout")
        self.assertEqual(ops.load(self.output / "job.json"), self.job)
        client.kernels_push.assert_not_called()

    def test_monitor_events_keep_status_when_log_fails_and_timeout_is_local_elapsed(self):
        job = {**self.job, "source_signature": "fake-secret"}
        ops.save(self.output / "job.json", job)
        clock = [0]
        with patch.dict(os.environ, {"KAGGLE_API_TOKEN": "fake-secret"}), \
             patch.object(ops, "status", return_value={"status": "running"}), \
             patch.object(ops, "log_slice", side_effect=requests.Timeout("fake-secret")), \
             patch.object(ops.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(ops.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = ops.wait(Mock(), self.output / "job.json", self.output, 1)
        events = [json.loads(line) for line in (self.output / "monitor-events.jsonl").read_text().splitlines()]
        self.assertEqual(result["status"], "waiting_timeout")
        self.assertEqual(events[0]["event"], "wait_started")
        self.assertTrue(any(event["event"] == "status_observed" and event["status"] == "running" for event in events))
        self.assertTrue(any(event["event"] == "log_retrieval_error" and event["error_type"] == "Timeout" for event in events))
        self.assertEqual(events[-1]["event"], "wait_deadline")
        self.assertEqual(events[-1]["status"], "running")
        self.assertTrue(all(event["monitor_elapsed_seconds"] >= 0 for event in events))
        serialized = (self.output / "monitor-events.jsonl").read_text()
        self.assertNotIn("fake-secret", serialized)

    def test_monitor_events_append_on_same_version_rewait_and_preserve_return(self):
        ops.save(self.output / "job.json", self.job)
        with patch.object(ops, "status", return_value={"status": "complete"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            first = ops.wait(Mock(), self.output / "job.json", self.output, 1)
            first_count = len((self.output / "monitor-events.jsonl").read_text().splitlines())
            second = ops.wait(Mock(), self.output / "job.json", self.output, 1)
        events = [json.loads(line) for line in (self.output / "monitor-events.jsonl").read_text().splitlines()]
        self.assertEqual(first, second)
        self.assertTrue(first["ready_for_verification"])
        self.assertEqual(sum(event["event"] == "wait_started" for event in events), 2)
        self.assertGreater(len(events), first_count)
        self.assertEqual(events[-1]["event"], "verification_ready")

    def test_status_observation_failure_is_recorded_without_guessing_remote_state(self):
        ops.save(self.output / "job.json", self.job)
        failure = requests.Timeout("unavailable")
        with patch.object(ops, "status", side_effect=failure), self.assertRaises(requests.Timeout) as caught:
            ops.wait(Mock(), self.output / "job.json", self.output, 1)
        self.assertIs(caught.exception, failure)
        events = [json.loads(line) for line in (self.output / "monitor-events.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "status_retrieval_error")
        self.assertEqual(events[-1]["error_type"], "Timeout")
        self.assertNotIn("status", events[-1])
        self.assertEqual(ops.load(self.output / "job.json"), self.job)

    def test_monitor_history_write_failure_does_not_abort_wait(self):
        ops.save(self.output / "job.json", self.job)
        (self.output / "monitor-events.jsonl").mkdir()
        with patch.object(ops, "status", return_value={"status": "complete"}), patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            result = ops.wait(Mock(), self.output / "job.json", self.output, 1)
        self.assertTrue(result["ready_for_verification"])
        self.assertEqual(ops.load(self.output / "continuation.json")["next_phase"], "verification")

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
        job = dict(self.job, state="submission_unknown", previous_version=6, code_sha256="digest", kernel_type="script",
                   source_signature=ops.source_signature("print('done')", "script"), enable_gpu=True, enable_internet=False)
        ops.save(self.output / "job.json", job)
        with self.assertRaises(ValueError):
            ops.reconcile(Mock(), self.output / "job.json", 6)
        with patch.object(ops, "get_kernel", return_value=self.remote()), patch.object(ops, "status", return_value={"status": "running"}):
            result = ops.reconcile(Mock(), self.output / "job.json", 7)
        self.assertEqual(result["code_sha256"], "digest")
        self.assertEqual(result["state"], "submitted")

    def test_reconciliation_cannot_accept_a_different_or_old_notebook_version(self):
        job = dict(self.job, state="submission_unknown", previous_version=6, kernel_type="script",
                   source_signature=ops.source_signature("print('done')", "script"), enable_gpu=True, enable_internet=False)
        ops.save(self.output / "job.json", job)
        for remote in (self.remote(version=6), self.remote(source="print('different')")):
            with patch.object(ops, "get_kernel", return_value=remote), patch.object(ops, "status") as status:
                with self.assertRaises(ValueError):
                    ops.reconcile(Mock(), self.output / "job.json", 7)
            status.assert_not_called()
        self.assertEqual(ops.load(self.output / "job.json")["state"], "submission_unknown")


if __name__ == "__main__":
    unittest.main()
