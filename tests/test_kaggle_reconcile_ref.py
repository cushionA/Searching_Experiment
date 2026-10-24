import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kaggle_ops_reconcile_ref", ROOT / ".agents/skills/kaggle-ops/scripts/kaggle_ops.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


class KaggleReconcileRefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job_path = Path(self.temp.name) / "job.json"
        source = "print('smoke')"
        self.candidate_ref = "owner/title-derived-slug"
        self.job = {"ref": "owner/generated-slug", "state": "submission_unknown", "previous_version": 0,
                    "code_sha256": "digest", "kernel_type": "script", "source_signature": ops.source_signature(source, "script"),
                    "enable_gpu": False, "enable_internet": False,
                    "submission_response": {"ref": "/code/owner/title-derived-slug", "version": 1, "error": ""}}
        ops.save(self.job_path, self.job)
        self.remote = SimpleNamespace(
            metadata=SimpleNamespace(ref=self.candidate_ref, current_version_number=1, is_private=True,
                                     enable_gpu=False, enable_internet=False),
            blob=SimpleNamespace(kernel_type="script", source=source))
        self.client = SimpleNamespace(config_values={"username": "owner"})

    def reconcile(self, ref=None, version=1):
        return ops.reconcile(self.client, self.job_path, version, ref)

    def test_exact_saved_response_ref_and_version_reconcile_after_full_verification(self):
        status = {"ref": self.candidate_ref, "version": 1, "status": "running", "failure_message": "",
                  "kernel_session_id": None, "session_id_unavailable_reason": "not exposed"}
        with patch.object(ops, "get_kernel", return_value=self.remote) as get_kernel, \
             patch.object(ops, "status", return_value=status) as get_status:
            result = self.reconcile(self.candidate_ref)
        get_kernel.assert_called_once_with(self.client, self.candidate_ref)
        get_status.assert_called_once_with(self.client, self.candidate_ref, 1)
        self.assertEqual(result["state"], "submitted")
        self.assertEqual(result["ref"], self.candidate_ref)
        self.assertEqual(result["requested_ref"], "owner/generated-slug")
        self.assertEqual(result["reconciliation_ref_evidence"], {
            "requested_ref": "owner/generated-slug", "submission_response_ref": self.candidate_ref,
            "submission_response_version": 1, "verified_ref": self.candidate_ref, "verified_version": 1,
            "verified_source_signature": self.job["source_signature"]})

    def test_ref_must_match_saved_response_exactly(self):
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "exactly match"):
                self.reconcile("owner/unrelated")
        get_kernel.assert_not_called()
        self.assertEqual(ops.load(self.job_path), self.job)

    def test_response_version_must_match_requested_version(self):
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "saved submission response"):
                self.reconcile(self.candidate_ref, 2)
        get_kernel.assert_not_called()
        self.assertEqual(ops.load(self.job_path), self.job)

    def test_other_owner_and_authenticated_owner_mismatch_are_rejected(self):
        job = {**self.job, "submission_response": {"ref": "/code/other/title-derived-slug", "version": 1, "error": ""}}
        ops.save(self.job_path, job)
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "original notebook owner"):
                self.reconcile("other/title-derived-slug")
        get_kernel.assert_not_called()
        ops.save(self.job_path, self.job)
        self.client.config_values["username"] = "someone-else"
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "authenticated Kaggle owner"):
                self.reconcile(self.candidate_ref)
        get_kernel.assert_not_called()
        self.assertEqual(ops.load(self.job_path), self.job)

    def test_only_first_submission_can_change_ref(self):
        job = {**self.job, "previous_version": 1}
        ops.save(self.job_path, job)
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "previous_version 0"):
                self.reconcile(self.candidate_ref, 2)
        get_kernel.assert_not_called()
        self.assertEqual(ops.load(self.job_path), job)

    def test_remote_source_type_privacy_gpu_internet_and_ref_are_verified_before_write(self):
        bad_remotes = [
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "ref": self.candidate_ref}),
                             blob=SimpleNamespace(kernel_type="script", source="print('other')")),
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "is_private": False}), blob=self.remote.blob),
            SimpleNamespace(metadata=self.remote.metadata, blob=SimpleNamespace(kernel_type="notebook", source="print('smoke')")),
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "enable_gpu": True}), blob=self.remote.blob),
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "enable_internet": True}), blob=self.remote.blob),
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "ref": "owner/other"}), blob=self.remote.blob),
            SimpleNamespace(metadata=SimpleNamespace(**{**vars(self.remote.metadata), "current_version_number": 2}), blob=self.remote.blob),
        ]
        for remote in bad_remotes:
            with self.subTest(metadata=vars(remote.metadata)), patch.object(ops, "get_kernel", return_value=remote), \
                 patch.object(ops, "status") as get_status, self.assertRaises(ValueError):
                self.reconcile(self.candidate_ref)
            get_status.assert_not_called()
            self.assertEqual(ops.load(self.job_path), self.job)

    def test_status_mismatch_leaves_original_job_ref_and_state_untouched(self):
        status = {"ref": self.candidate_ref, "version": 2, "status": "running"}
        with patch.object(ops, "get_kernel", return_value=self.remote), patch.object(ops, "status", return_value=status):
            with self.assertRaisesRegex(ValueError, "status response"):
                self.reconcile(self.candidate_ref)
        self.assertEqual(ops.load(self.job_path), self.job)

    def test_error_response_and_arbitrary_or_nonpositive_version_are_rejected(self):
        job = {**self.job, "submission_response": {"ref": "/code/owner/title-derived-slug", "version": 1, "error": "push error"}}
        ops.save(self.job_path, job)
        with patch.object(ops, "get_kernel") as get_kernel:
            with self.assertRaisesRegex(ValueError, "contains an error"):
                self.reconcile(self.candidate_ref)
            with self.assertRaisesRegex(ValueError, "positive integer"):
                self.reconcile(self.candidate_ref, True)
            with self.assertRaisesRegex(ValueError, "positive integer"):
                self.reconcile(self.candidate_ref, 0)
        get_kernel.assert_not_called()

    def test_cli_does_not_allow_ref_with_not_accepted(self):
        class CapturedStdout(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        captured = CapturedStdout()
        argv = ["kaggle_ops.py", "reconcile", "--job", str(self.job_path), "--not-accepted", "--ref", "owner/title-derived-slug"]
        with patch.object(sys, "argv", argv), patch.object(sys, "stdout", captured), patch.object(sys, "stderr", captured), \
             patch.object(ops, "api", return_value=SimpleNamespace()), patch.object(ops, "mark_not_accepted") as mark:
            self.assertEqual(ops.main(), 2)
        mark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
