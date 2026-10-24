import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kaggle_ops_cancel", ROOT / ".agents/skills/kaggle-ops/scripts/kaggle_ops.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class KaggleCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job_path = Path(self.temp.name) / "job.json"
        ops.save(self.job_path, {"ref": "owner/notebook", "version": 7, "state": "submitted"})

    def client(self, api):
        return SimpleNamespace(build_kaggle_client=lambda: contextlib.nullcontext(
            SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=api))))

    def test_identity_lookup_is_explicitly_unavailable_for_pinned_sdk_schema(self):
        response = SimpleNamespace(status=SimpleNamespace(name="RUNNING"), failure_message="")
        api = SimpleNamespace(get_kernel_session_status=Mock(return_value=response))
        identity = ops.resolve_session_identity(self.client(api), "owner/notebook", 7)
        self.assertIsNone(identity["kernel_session_id"])
        self.assertIn("no session-id lookup", identity["session_id_unavailable_reason"])
        request = api.get_kernel_session_status.call_args.args[0]
        self.assertEqual((request.user_name, request.kernel_slug, request.version_label), ("owner", "notebook", "v7"))

    def test_missing_session_id_fails_closed_without_cancel_api_call(self):
        statuses = iter([{"status": "running", "failure_message": ""}])
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": None,
                                                                            "session_id_unavailable_reason": "not exposed"}), \
             patch.object(ops, "status", side_effect=lambda *a: next(statuses)):
            result = ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "stop requested by user")
        self.assertEqual(result["request_outcome"], "session_id_unavailable")
        api.cancel_kernel_session.assert_not_called()
        job = ops.load(self.job_path)
        self.assertEqual(job["cancel_attempts"][-1]["outcome"], "session_id_unavailable")

    def test_wrong_ref_or_version_is_rejected_before_status_or_cancel(self):
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with patch.object(ops, "status") as status:
            with self.assertRaisesRegex(ValueError, "does not match"):
                ops.cancel(self.client(api), self.job_path, "owner/notebook", 8, "reason")
        status.assert_not_called()
        api.cancel_kernel_session.assert_not_called()

    def test_invalid_positive_version_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            ops.cancel(self.client(SimpleNamespace()), self.job_path, "owner/notebook", True, "reason")

    def test_saved_non_null_session_binding_must_match_resolved_identity(self):
        job = ops.load(self.job_path)
        job["session_identity"] = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 123}
        ops.save(self.job_path, job)
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": 456}), \
             patch.object(ops, "status", return_value={"status": "running", "failure_message": ""}), \
             self.assertRaisesRegex(ValueError, "conflicts with the saved job binding"):
            ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        api.cancel_kernel_session.assert_not_called()

    def test_live_status_ref_version_or_session_mismatch_blocks_api_request(self):
        binding = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 456}
        api = SimpleNamespace(cancel_kernel_session=Mock())
        for observed in ({"ref": "other/notebook", "version": 7, "status": "running", "failure_message": ""},
                         {"ref": "owner/notebook", "version": 8, "status": "running", "failure_message": ""},
                         {"ref": "owner/notebook", "version": 7, "kernel_session_id": 789,
                          "status": "running", "failure_message": ""}):
            with self.subTest(observed=observed), \
                 patch.object(ops, "resolve_session_identity", return_value=binding), \
                 patch.object(ops, "status", return_value=observed), \
                 self.assertRaisesRegex(ValueError, "does not match|conflicts"):
                ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        api.cancel_kernel_session.assert_not_called()

    def test_lock_contention_prevents_second_cancel_attempt(self):
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with ops._cancel_job_lock(self.job_path):
            with self.assertRaisesRegex(ValueError, "job lock"):
                ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        api.cancel_kernel_session.assert_not_called()

    def test_terminal_job_does_not_send_cancel_and_timeout_cause_is_recorded(self):
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": None}), \
             patch.object(ops, "status", return_value={"status": "cancel_acknowledged", "failure_message": "Your notebook was stopped because it exceeded the max allowed execution duration."}):
            result = ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        self.assertEqual(result["cause"], "timeout")
        self.assertEqual(result["request_outcome"], "not_requested_terminal")
        api.cancel_kernel_session.assert_not_called()

    def test_cancel_requested_is_pending_and_does_not_repeat_request(self):
        job = ops.load(self.job_path)
        job["cancel_attempts"] = [{"outcome": "request_accepted"}]
        ops.save(self.job_path, job)
        api = SimpleNamespace(cancel_kernel_session=Mock())
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": 456}), \
             patch.object(ops, "status", return_value={"status": "cancel_requested", "failure_message": ""}):
            result = ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        self.assertEqual(result["request_outcome"], "not_requested_pending")
        api.cancel_kernel_session.assert_not_called()

    def test_supported_future_binding_records_acceptance_and_pending_final_state(self):
        api = SimpleNamespace(cancel_kernel_session=Mock(return_value=SimpleNamespace(error_message="")))
        statuses = iter([{"status": "running", "failure_message": ""},
                         {"status": "cancel_requested", "failure_message": ""}])
        client = self.client(api)
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": 456}), \
             patch.object(ops, "status", side_effect=lambda *a: next(statuses)):
            result = ops.cancel(client, self.job_path, "owner/notebook", 7, "stop requested by user")
        request = api.cancel_kernel_session.call_args.args[0]
        self.assertEqual(request.kernel_session_id, 456)
        self.assertEqual(result["request_outcome"], "request_accepted")
        self.assertEqual(result["status"], "cancel_requested")
        self.assertEqual(ops.load(self.job_path)["cancel_state"], "request_accepted_pending")

    def test_api_rejection_is_recorded_and_not_misreported_as_accepted(self):
        api = SimpleNamespace(cancel_kernel_session=Mock(return_value=SimpleNamespace(error_message="already stopped")))
        statuses = iter([{"status": "running", "failure_message": ""}])
        with patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                            "kernel_session_id": 456}), \
             patch.object(ops, "status", side_effect=lambda *a: next(statuses)):
            result = ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        self.assertEqual(result, {"request_outcome": "request_rejected", "error_message": "already stopped"})
        self.assertEqual(ops.load(self.job_path)["cancel_attempts"][-1]["outcome"], "request_rejected")

    def test_http_api_rejection_is_persisted_and_never_retried(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError(response=response)
        api = SimpleNamespace(cancel_kernel_session=Mock(side_effect=error))
        binding = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 456}
        with patch.object(ops, "resolve_session_identity", return_value=binding), \
             patch.object(ops, "status", return_value={"status": "running", "failure_message": ""}):
            with self.assertRaises(requests.HTTPError):
                ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        self.assertEqual(api.cancel_kernel_session.call_count, 1)
        attempt = ops.load(self.job_path)["cancel_attempts"][-1]
        self.assertEqual((attempt["outcome"], attempt["http_status"]), ("request_rejected", 403))

    def test_ambiguous_api_exception_is_persisted_and_never_retried(self):
        api = SimpleNamespace(cancel_kernel_session=Mock(side_effect=TimeoutError()))
        client = self.client(api)
        binding = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 456}
        with patch.object(ops, "resolve_session_identity", return_value=binding), \
             patch.object(ops, "status", return_value={"status": "running", "failure_message": ""}):
            with self.assertRaises(TimeoutError):
                ops.cancel(client, self.job_path, "owner/notebook", 7, "reason")
            with self.assertRaisesRegex(ValueError, "unresolved"):
                ops.cancel(client, self.job_path, "owner/notebook", 7, "reason")
        self.assertEqual(api.cancel_kernel_session.call_count, 1)
        self.assertEqual(ops.load(self.job_path)["cancel_attempts"][-1]["outcome"], "request_unknown")

    def test_post_request_status_failure_keeps_acceptance_and_observation_error(self):
        api = SimpleNamespace(cancel_kernel_session=Mock(return_value=SimpleNamespace(error_message="")))
        binding = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 456}
        with patch.object(ops, "resolve_session_identity", return_value=binding), \
             patch.object(ops, "status", side_effect=[{"status": "running", "failure_message": ""}, TimeoutError()]):
            with self.assertRaises(TimeoutError):
                ops.cancel(self.client(api), self.job_path, "owner/notebook", 7, "reason")
        job = ops.load(self.job_path)
        self.assertEqual(job["cancel_state"], "request_accepted_pending")
        self.assertEqual(job["cancel_attempts"][-1]["outcome"], "request_accepted")
        self.assertEqual(job["cancel_attempts"][-1]["status_observation"]["error_type"], "TimeoutError")

    def test_cancel_cli_returns_nonzero_when_session_id_is_unavailable(self):
        class CapturedStdout(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        args = ["kaggle_ops.py", "cancel", "--job", str(self.job_path), "--ref", "owner/notebook",
                "--version", "7", "--reason", "stop"]
        captured = CapturedStdout()
        with patch("sys.argv", args), patch("sys.stdout", captured), patch("sys.stderr", captured), \
             patch.object(ops, "api", return_value=self.client(SimpleNamespace(get_kernel_session_status=Mock(
                 return_value=SimpleNamespace(status=SimpleNamespace(name="RUNNING"), failure_message=""))))):
            self.assertEqual(ops.main(), 2)

    def test_cancel_cli_returns_nonzero_when_api_rejects_request(self):
        class CapturedStdout(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        args = ["kaggle_ops.py", "cancel", "--job", str(self.job_path), "--ref", "owner/notebook",
                "--version", "7", "--reason", "stop"]
        api = SimpleNamespace(cancel_kernel_session=Mock(return_value=SimpleNamespace(error_message="rejected")))
        captured = CapturedStdout()
        with patch("sys.argv", args), patch("sys.stdout", captured), patch("sys.stderr", captured), \
             patch.object(ops, "api", return_value=self.client(api)), \
             patch.object(ops, "resolve_session_identity", return_value={"ref": "owner/notebook", "version": 7,
                                                                           "kernel_session_id": 456}), \
             patch.object(ops, "status", return_value={"status": "running", "failure_message": ""}):
            self.assertEqual(ops.main(), 2)
        api.cancel_kernel_session.assert_called_once()

    def test_wait_keeps_cancel_requested_pending_then_observes_running_and_terminal(self):
        job = {"ref": "owner/notebook", "version": 7, "state": "submitted",
               "session_identity": {"ref": "owner/notebook", "version": 7, "kernel_session_id": 456},
               "cancel_attempts": [{"outcome": "request_accepted", "ref": "owner/notebook", "version": 7,
                                    "kernel_session_id": 456}]}
        ops.save(self.job_path, job)
        states = iter([{"status": "cancel_requested", "failure_message": ""},
                       {"status": "running", "failure_message": ""},
                       {"status": "cancel_acknowledged", "failure_message": ""}])
        clock = [0]
        with patch.object(ops, "status", side_effect=lambda *a: next(states)), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]) as outputs, \
             patch.object(ops.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(ops.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + 1)):
            result = ops.wait(self.client(SimpleNamespace()), self.job_path, self.job_path.parent, 10, interval=1)
        self.assertEqual(result["status"], "cancel_acknowledged")
        self.assertEqual(result["cancel_final"]["cause"], "user_requested")
        outputs.assert_called_once()

    def test_acknowledged_cancel_with_accepted_attempt_records_cause_and_caveat(self):
        attempt = {"outcome": "request_accepted", "ref": "owner/notebook", "version": 7, "kernel_session_id": 456}
        cause, evidence = ops._cancel_cause("cancel_acknowledged", "", [attempt], "owner/notebook", 7, 456)
        self.assertEqual(cause, "user_requested")
        self.assertIn("not cryptographic", evidence)
        legacy = {"outcome": "request_accepted"}
        cause, _ = ops._cancel_cause("cancel_acknowledged", "", [legacy], "owner/notebook", 7, 456)
        self.assertEqual(cause, "external_or_unknown")
        cause, _ = ops._cancel_cause("complete", "", [attempt], "owner/notebook", 7, 456)
        self.assertEqual(cause, "completed")
        cause, _ = ops._cancel_cause("error", "", [attempt], "owner/notebook", 7, 456)
        self.assertEqual(cause, "error")


class KaggleNotificationConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "notebook"
        self.folder.mkdir()
        (self.folder / "run.py").write_text("print('done')", encoding="utf-8")
        ops.save(self.folder / "kernel-metadata.json", {"id": "owner/notebook", "code_file": "run.py", "kernel_type": "script",
                                                         "is_private": True, "enable_gpu": True, "enable_tpu": False,
                                                         "enable_internet": False})
        ops.save(self.folder / "training-params.json", {"timeout_seconds": 600})
        self.output = self.root / "results"
        self.target = {"provider": "chatgpt", "repository": "owner/repo", "snapshot_ref": "kaggle-monitor-state",
                       "snapshot_path": "automation/kaggle-monitor.json"}

    def client(self):
        return SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(ref="owner/notebook", version_number=7, error="")))

    def test_default_notification_is_explicitly_unconfigured(self):
        client = self.client()
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "latest_version", return_value=0):
            job = ops.submit(client, self.folder, self.output)
        self.assertEqual(job["notification"], {"target": {"provider": "chatgpt"}, "state": "not_configured",
                                                "delivery_status": "unknown"})

    def test_explicit_notification_target_is_saved_without_claiming_registration(self):
        client = self.client()
        with patch.object(ops, "quota", return_value={"gpu": {"remaining_seconds": 1000}}), patch.object(ops, "latest_version", return_value=0):
            job = ops.submit(client, self.folder, self.output, self.target)
        self.assertEqual(job["notification"], {"target": self.target, "state": "target_configured",
                                                "delivery_status": "unknown"})

    def test_invalid_notification_target_fails_before_quota_or_push(self):
        client = self.client()
        invalid = {**self.target, "snapshot_path": "../outside.json"}
        with patch.object(ops, "quota") as quota, self.assertRaisesRegex(ValueError, "repository-relative"):
            ops.submit(client, self.folder, self.output, invalid)
        quota.assert_not_called()
        client.kernels_push.assert_not_called()
        self.assertFalse((self.output / "job.json").exists())

    def test_cli_invalid_notification_config_fails_before_authentication(self):
        class CapturedStdout(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        args = ["kaggle_ops.py", "submit", "--folder", str(self.folder), "--output", str(self.output),
                "--notification-config", '{"provider":"chatgpt","repository":"owner/repo","snapshot_ref":"main","snapshot_path":"../escape"}']
        captured = CapturedStdout()
        with patch("sys.argv", args), patch("sys.stdout", captured), patch("sys.stderr", captured), \
             patch.object(ops, "api") as api:
            self.assertEqual(ops.main(), 2)
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
