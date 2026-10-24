import contextlib
import importlib.util
import json
import requests
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kaggle_ops_public_cancel", ROOT / ".agents/skills/kaggle-ops/scripts/kaggle_ops.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class PublicSubmissionPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "notebook"
        self.folder.mkdir()
        ops.save(self.folder / "kernel-metadata.json", {
            "id": "owner/public-smoke-test", "code_file": "run.py", "kernel_type": "script",
            "is_private": False, "enable_gpu": False, "enable_tpu": False, "enable_internet": False,
        })
        (self.folder / "run.py").write_text("print('smoke')", encoding="utf-8")
        self.output = self.root / "job"
        self.client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(
            ref="owner/public-smoke-test", version_number=1, error="")))

    def fake_view_session(self, body=None, *, page_status=200, post_status=200, xsrf="csrf-test-secret"):
        body = body or {
            "author": {"userName": "owner"}, "currentVersionNumber": 1,
            "kernel": {"id": 987, "slug": "public-smoke-test", "url": "/code/owner/public-smoke-test",
                       "author": {"userName": "owner"}},
            "kernelRun": {"id": 4567, "kernelId": 987, "kernelVersionNumber": 1},
        }
        class Cookies:
            def get(self, name):
                return {"XSRF-TOKEN": xsrf, "build-hash": "build-test"}.get(name)
        class Response:
            def __init__(self, status, url, json_body=None):
                self.status_code, self.url, self._json_body = status, url, json_body
            def json(self):
                return self._json_body
        class Session:
            instances = []
            def __init__(self):
                self.cookies = Cookies()
                self.headers = []
                self.closed = False
                Session.instances.append(self)
            def get(self, url, **kwargs):
                self.page_call = (url, kwargs)
                return Response(page_status, url)
            def post(self, url, **kwargs):
                self.post_call = (url, kwargs)
                return Response(post_status, url, body)
            def close(self):
                self.closed = True
        return Session

    def test_public_submission_requires_explicit_allow_public(self):
        with patch.object(ops, "latest_version", return_value=0):
            with self.assertRaisesRegex(ValueError, "--allow-public"):
                ops.submit(self.client, self.folder, self.output)
        self.client.kernels_push.assert_not_called()
        self.assertFalse((self.output / "job.json").exists())

    def test_public_submission_records_requested_privacy(self):
        with patch.object(ops, "latest_version", return_value=0):
            job = ops.submit(self.client, self.folder, self.output, allow_public=True)
        self.assertFalse(job["is_private"])
        self.assertEqual(ops.load(self.output / "job.json")["is_private"], False)
        self.client.kernels_push.assert_called_once()


class CancelTerminalAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "notebook"
        self.folder.mkdir()
        ops.save(self.folder / "kernel-metadata.json", {
            "id": "owner/public-smoke-test", "code_file": "run.py", "kernel_type": "script",
            "is_private": False, "enable_gpu": False, "enable_tpu": False, "enable_internet": False,
        })
        (self.folder / "run.py").write_text("print('smoke')", encoding="utf-8")
        self.output = self.root / "job"
        self.client = SimpleNamespace(kernels_push=Mock(return_value=SimpleNamespace(
            ref="owner/public-smoke-test", version_number=1, error="")))
        self.job_path = self.root / "job.json"
        self.ref, self.version, self.session_id = "owner/public-smoke-test", 1, 4567
        self.identity = {"ref": self.ref, "version": self.version, "kernel_session_id": self.session_id,
                         "kernel_id": 987, "source": "public_get_kernel_view_model"}
        self.accepted = {"outcome": "request_accepted", "ref": self.ref, "version": self.version,
                         "kernel_session_id": self.session_id, "reason": "stop synthetic smoke test"}

    def fake_view_session(self, body=None, *, page_status=200, post_status=200, xsrf="csrf-test-secret"):
        body = body or {
            "author": {"userName": "owner"}, "currentVersionNumber": 1,
            "kernel": {"id": 987, "slug": "public-smoke-test", "url": "/code/owner/public-smoke-test",
                       "author": {"userName": "owner"}},
            "kernelRun": {"id": 4567, "kernelId": 987, "kernelVersionNumber": 1},
        }
        class Cookies:
            def get(self, name):
                return {"XSRF-TOKEN": xsrf, "build-hash": "build-test"}.get(name)
        class Response:
            def __init__(self, status, url, json_body=None):
                self.status_code, self.url, self._json_body = status, url, json_body
            def json(self):
                return self._json_body
        class Session:
            instances = []
            def __init__(self):
                self.cookies = Cookies()
                self.headers = []
                self.closed = False
                Session.instances.append(self)
            def get(self, url, **kwargs):
                self.page_call = (url, kwargs)
                return Response(page_status, url)
            def post(self, url, **kwargs):
                self.post_call = (url, kwargs)
                return Response(post_status, url, body)
            def close(self):
                self.closed = True
        return Session

    def write_job(self):
        ops.save(self.job_path, {"ref": self.ref, "version": self.version, "state": "submitted",
                                 "is_private": False, "session_identity": dict(self.identity),
                                 "cancel_attempts": [dict(self.accepted)], "cancel_state": "request_accepted_pending"})

    def test_wait_persists_acknowledged_cancel_audit(self):
        self.write_job()
        with patch.object(ops, "status", return_value={"ref": self.ref, "version": self.version,
                                                        "status": "cancel_acknowledged", "failure_message": ""}), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            result = ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertEqual(result["cancel_final"]["cause"], "user_requested")
        self.assertEqual(saved["cancel_state"], "complete")
        self.assertEqual(saved["cancel_final"]["status"], "cancel_acknowledged")
        self.assertEqual(saved["cancel_final"]["cause"], "user_requested")

    def test_wait_records_accepted_cancel_that_lost_to_natural_completion(self):
        self.write_job()
        with patch.object(ops, "status", return_value={"ref": self.ref, "version": self.version,
                                                        "status": "complete", "failure_message": ""}), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            result = ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertEqual(result["cancel_final"]["cause"], "completed")
        self.assertEqual(saved["cancel_state"], "terminal_without_cancellation")
        self.assertEqual(saved["cancel_final"]["status"], "complete")

    def test_wait_records_timeout_cancel_ack_without_cancel_attempt(self):
        ops.save(self.job_path, {"ref": self.ref, "version": self.version, "state": "submitted",
                                 "is_private": False, "session_identity": dict(self.identity)})
        with patch.object(ops, "status", return_value={"ref": self.ref, "version": self.version,
                                                        "status": "cancel_acknowledged",
                                                        "failure_message": "Maximum allowed execution duration reached."}), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            result = ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertEqual(result["cancel_final"]["cause"], "timeout")
        self.assertEqual(saved["cancel_state"], "complete")
        self.assertEqual(saved["cancel_final"]["cause"], "timeout")

    def test_wait_records_unattributed_cancel_ack_without_cancel_attempt(self):
        ops.save(self.job_path, {"ref": self.ref, "version": self.version, "state": "submitted",
                                 "is_private": False, "session_identity": dict(self.identity)})
        with patch.object(ops, "status", return_value={"ref": self.ref, "version": self.version,
                                                        "status": "cancel_acknowledged", "failure_message": ""}), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            result = ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertEqual(result["cancel_final"]["cause"], "external_or_unknown")
        self.assertEqual(saved["cancel_final"]["cause"], "external_or_unknown")

    def test_wait_refuses_to_audit_terminal_status_onto_rebound_job(self):
        self.write_job()
        def status_and_rebind(*_args):
            changed = ops.load(self.job_path)
            changed.update(ref="owner/different-notebook", version=2)
            ops.save(self.job_path, changed)
            return {"ref": self.ref, "version": self.version,
                    "status": "cancel_acknowledged", "failure_message": ""}
        with patch.object(ops, "status", side_effect=status_and_rebind), \
             patch.object(ops, "log_slice", return_value={}), \
             self.assertRaisesRegex(ValueError, "saved job ref/version changed"):
            ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertNotIn("cancel_final", saved)
        self.assertEqual(saved["ref"], "owner/different-notebook")

    def test_wait_refuses_terminal_status_with_conflicting_session_id(self):
        self.write_job()
        with patch.object(ops, "status", return_value={"ref": self.ref, "version": self.version,
                                                        "kernel_session_id": 999, "status": "cancel_acknowledged",
                                                        "failure_message": ""}), \
             patch.object(ops, "log_slice", return_value={}), \
             self.assertRaisesRegex(ValueError, "session identity conflicts"):
            ops.wait(SimpleNamespace(), self.job_path, self.root, 10)
        saved = ops.load(self.job_path)
        self.assertNotIn("cancel_final", saved)
        self.assertEqual(saved["cancel_state"], "request_accepted_pending")

    def test_concurrent_wait_uses_cancel_request_persisted_after_wait_started(self):
        ops.save(self.job_path, {"ref": self.ref, "version": self.version, "state": "submitted",
                                 "is_private": False, "session_identity": dict(self.identity),
                                 "cancel_attempts": [], "cancel_state": "idle"})
        cancel_api_entered = threading.Event()
        release_cancel_api = threading.Event()
        wait_saw_terminal = threading.Event()
        wait_finished = threading.Event()
        errors = []
        calls = [0]

        def blocking_cancel(_request):
            cancel_api_entered.set()
            if not release_cancel_api.wait(5):
                raise TimeoutError("test did not release the cancel API stub")
            return SimpleNamespace(error_message="")

        def status_stub(_client, ref, version):
            calls[0] += 1
            if calls[0] == 1:
                return {"ref": ref, "version": version, "status": "running", "failure_message": ""}
            wait_saw_terminal.set()
            return {"ref": ref, "version": version, "status": "cancel_acknowledged", "failure_message": ""}

        api = SimpleNamespace(cancel_kernel_session=Mock(side_effect=blocking_cancel))
        client = SimpleNamespace(build_kaggle_client=lambda: contextlib.nullcontext(
            SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=api))))

        def run_cancel():
            try:
                ops.cancel(client, self.job_path, self.ref, self.version, "stop synthetic smoke test")
            except Exception as error:
                errors.append(error)

        def run_wait():
            try:
                ops.wait(client, self.job_path, self.root, 10)
            except Exception as error:
                errors.append(error)
            finally:
                wait_finished.set()

        with patch.object(ops, "resolve_session_identity", return_value=dict(self.identity)), \
             patch.object(ops, "status", side_effect=status_stub), \
             patch.object(ops, "log_slice", return_value={}), patch.object(ops, "outputs", return_value=[]):
            cancel_thread = threading.Thread(target=run_cancel)
            cancel_thread.start()
            self.assertTrue(cancel_api_entered.wait(3))
            wait_thread = threading.Thread(target=run_wait)
            wait_thread.start()
            self.assertTrue(wait_saw_terminal.wait(3))
            # Wait sees the terminal status while cancel still owns the job lock.
            self.assertFalse(wait_finished.wait(0.1))
            release_cancel_api.set()
            cancel_thread.join(5)
            wait_thread.join(5)

        self.assertFalse(cancel_thread.is_alive())
        self.assertFalse(wait_thread.is_alive())
        self.assertEqual(errors, [])
        saved = ops.load(self.job_path)
        self.assertEqual(saved["cancel_attempts"][-1]["outcome"], "request_accepted")
        self.assertEqual(saved["cancel_state"], "complete")
        self.assertEqual(saved["cancel_final"]["cause"], "user_requested")

    def test_public_flag_does_not_allow_tpu(self):
        metadata_path = self.folder / "kernel-metadata.json"
        metadata = ops.load(metadata_path)
        metadata["enable_tpu"] = True
        ops.save(metadata_path, metadata)
        with self.assertRaisesRegex(ValueError, "TPU must be disabled"):
            ops.submit(self.client, self.folder, self.output, allow_public=True)
        self.client.kernels_push.assert_not_called()

    def test_version_verification_uses_saved_privacy_and_keeps_old_job_default_private(self):
        remote = SimpleNamespace(metadata=SimpleNamespace(
            ref="owner/public-smoke-test", current_version_number=2, is_private=False,
            enable_gpu=False, enable_internet=False, enable_tpu=False),
            blob=SimpleNamespace(kernel_type="script", source="print('smoke')"))
        job = {"ref": "owner/public-smoke-test", "version": 1, "previous_version": 0,
               "source_signature": ops.source_signature("print('smoke')", "script"),
               "kernel_type": "script", "enable_gpu": False, "enable_internet": False,
               "is_private": False}
        with patch.object(ops, "get_kernel", return_value=remote):
            self.assertEqual(ops.verify_saved_version(SimpleNamespace(), job), 2)
            with self.assertRaisesRegex(ValueError, "privacy or GPU"):
                ops.verify_saved_version(SimpleNamespace(), {k: v for k, v in job.items() if k != "is_private"})

    def test_public_lookup_validates_and_returns_kernel_run_id_as_session_id(self):
        Session = self.fake_view_session()
        client = SimpleNamespace()
        remote = SimpleNamespace(metadata=SimpleNamespace(id=987))
        with patch.object(requests, "Session", Session), patch.object(ops, "get_kernel", return_value=remote):
            identity = ops.resolve_session_identity(client, "owner/public-smoke-test", 1, is_private=False)
        self.assertEqual(identity["kernel_session_id"], 4567)
        self.assertEqual(identity["kernel_id"], 987)
        self.assertEqual(identity["source"], "public_get_kernel_view_model")
        session = Session.instances[-1]
        endpoint, options = session.post_call
        self.assertEqual(endpoint, "https://www.kaggle.com/api/i/kernels.LegacyKernelsService/GetKernelViewModel")
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["json"], {"authorUserName": "owner", "kernelSlug": "public-smoke-test",
                                            "kernelVersionId": 0, "versionNumber": 1})
        self.assertEqual(options["headers"]["X-XSRF-TOKEN"], "csrf-test-secret")
        self.assertEqual(options["headers"]["X-Kaggle-Build-Version"], "build-test")
        self.assertNotIn("Authorization", options["headers"])
        self.assertTrue(session.closed)

    def test_public_lookup_rejects_kernel_id_that_differs_from_sdk_metadata(self):
        Session = self.fake_view_session()
        with patch.object(requests, "Session", Session), patch.object(
                ops, "get_kernel", return_value=SimpleNamespace(metadata=SimpleNamespace(id=988))):
            identity = ops.resolve_session_identity(SimpleNamespace(), "owner/public-smoke-test", 1, is_private=False)
        self.assertIsNone(identity["kernel_session_id"])
        self.assertIn("public lookup failed", identity["session_id_unavailable_reason"])

    def test_public_lookup_rejects_ref_version_and_kernel_id_mismatches(self):
        bodies = []
        wrong_owner = {"author": {"userName": "other"}, "currentVersionNumber": 1,
                       "kernel": {"id": 987, "slug": "public-smoke-test", "url": "/code/owner/public-smoke-test",
                                  "author": {"userName": "owner"}},
                       "kernelRun": {"id": 4567, "kernelId": 987, "kernelVersionNumber": 1}}
        wrong_slug = json.loads(json.dumps(wrong_owner))
        wrong_slug["author"]["userName"] = "owner"
        wrong_slug["kernel"]["slug"] = "other"
        wrong_version = json.loads(json.dumps(wrong_slug))
        wrong_version["kernel"]["slug"] = "public-smoke-test"
        wrong_version["currentVersionNumber"] = 2
        wrong_kernel_id = json.loads(json.dumps(wrong_version))
        wrong_kernel_id["currentVersionNumber"] = 1
        wrong_kernel_id["kernelRun"]["kernelId"] = 988
        missing_session_id = json.loads(json.dumps(wrong_kernel_id))
        missing_session_id["kernelRun"]["kernelId"] = 987
        missing_session_id["kernelRun"].pop("id")
        bodies.extend([wrong_owner, wrong_slug, wrong_version, wrong_kernel_id, missing_session_id])
        for body in bodies:
            Session = self.fake_view_session(body)
            with patch.object(requests, "Session", Session), patch.object(ops, "get_kernel", return_value=SimpleNamespace(metadata=SimpleNamespace(id=987))):
                identity = ops.resolve_session_identity(SimpleNamespace(), "owner/public-smoke-test", 1, is_private=False)
            self.assertIsNone(identity["kernel_session_id"])
            self.assertIn("public lookup failed", identity["session_id_unavailable_reason"])

    def test_public_lookup_http_or_csrf_failure_does_not_retain_secret_values(self):
        for Session in (self.fake_view_session(post_status=403), self.fake_view_session(xsrf="")):
            with patch.object(requests, "Session", Session):
                identity = ops.resolve_session_identity(SimpleNamespace(), "owner/public-smoke-test", 1, is_private=False)
            self.assertIsNone(identity["kernel_session_id"])
            self.assertNotIn("csrf-test-secret", identity["session_id_unavailable_reason"])

    def test_public_cancel_sends_only_verified_session_id(self):
        job = {"ref": "owner/public-smoke-test", "version": 1, "state": "submitted", "is_private": False}
        job_path = self.output / "cancel-job.json"
        self.output.mkdir(parents=True, exist_ok=True)
        ops.save(job_path, job)
        binding = {"ref": job["ref"], "version": 1, "kernel_session_id": 4567,
                   "kernel_id": 987, "source": "public_get_kernel_view_model"}
        api = SimpleNamespace(cancel_kernel_session=Mock(return_value=SimpleNamespace(error_message="")))
        with patch.object(ops, "resolve_session_identity", return_value=binding), \
             patch.object(ops, "status", return_value={"ref": job["ref"], "version": 1,
                                                        "status": "running", "failure_message": ""}):
            result = ops.cancel(SimpleNamespace(build_kaggle_client=lambda: contextlib.nullcontext(
                SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=api)))),
                job_path, job["ref"], 1, "stop synthetic public smoke test")
        self.assertEqual(result["request_outcome"], "request_accepted")
        request = api.cancel_kernel_session.call_args.args[0]
        self.assertEqual(request.kernel_session_id, 4567)

    def test_post_submit_lookup_failure_keeps_submission_accepted(self):
        def lookup(*args, **kwargs):
            self.assertEqual(ops.load(self.output / "job.json")["state"], "submitted")
            raise requests.Timeout()
        with patch.object(ops, "latest_version", return_value=0), patch.object(
                ops, "resolve_session_identity", side_effect=lookup):
            job = ops.submit(self.client, self.folder, self.output, allow_public=True)
        self.assertEqual(job["state"], "submitted")
        self.assertEqual(job["session_identity"]["kernel_session_id"], None)
        self.assertIn("Timeout", job["session_identity"]["session_id_unavailable_reason"])
        self.client.kernels_push.assert_called_once()


if __name__ == "__main__":
    unittest.main()
