"""Offline tests for the durable Kaggle to ChatGPT automation handoff."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / ".agents/skills/kaggle-ops/scripts/kaggle_monitor.py"
SPEC = importlib.util.spec_from_file_location("kaggle_monitor_under_test", SCRIPT)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class KaggleMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.job_path = self.root / "job.json"
        self.job = {"ref": "owner/notebook", "version": 7, "state": "submitted",
                    "session_identity": {"ref": "owner/notebook", "version": 7,
                                          "kernel_session_id": 123},
                    "cancel_attempts": [],
                    "notification": {"target": monitor.target("owner/repo")}}
        monitor.ops.save(self.job_path, self.job)
        self.output = self.root / "output"

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, current):
        with patch.object(monitor.ops, "status", return_value=current):
            return monitor.poll(object(), self.job_path, self.output)

    def current(self, status, **kwargs):
        return {"ref": "owner/notebook", "version": 7, "status": status,
                "failure_message": "", "kernel_session_id": 123, **kwargs}

    def test_terminal_complete_without_scores_is_recorded(self):
        result = self.poll(self.current("complete"))
        self.assertEqual(result["terminal_event"]["status"], "complete")
        self.assertEqual(result["terminal_event"]["verification"], "not_evaluated")
        self.assertNotIn("score", result["terminal_event"])
        self.assertEqual(result["notification"]["state"], "not_delivered")

    def test_error_and_cancel_reasons_are_recorded(self):
        result = self.poll(self.current("error", failure_message="kernel failed"))
        self.assertEqual(result["terminal_event"]["reason"], "error")
        cases = (("Exceeded the max allowed execution duration", [], "timeout"),
                 ("", [{"outcome": "request_accepted", "ref": "owner/notebook", "version": 7, "kernel_session_id": 123}], "user_requested"),
                 ("", [], "external_or_unknown"))
        for message, attempts, expected in cases:
            with self.subTest(expected=expected):
                self.job["cancel_attempts"] = attempts
                monitor.ops.save(self.job_path, self.job)
                self.output = self.root / ("reason-" + expected)
                result = self.poll(self.current("cancel_acknowledged", failure_message=message))
                self.assertEqual(result["terminal_event"]["reason"], expected)

    def test_cancel_requested_is_pending_and_not_a_terminal_event(self):
        result = self.poll(self.current("cancel_requested"))
        self.assertEqual(result["monitor_state"], "observed")
        self.assertNotIn("terminal_event", result)

    def test_repeated_terminal_polls_and_restart_preserve_one_event(self):
        first = self.poll(self.current("complete"))
        event = first["terminal_event"]
        second = self.poll(self.current("complete"))
        self.assertEqual(second["terminal_event"], event)
        saved = monitor.ops.load(self.output / monitor.event_id(self.job) / "observation.json")
        restarted = monitor.poll(object(), self.job_path, self.output)
        self.assertEqual(saved["terminal_event"], restarted["terminal_event"])
        self.assertEqual(restarted["poll_attempts"], 3)

    def test_source_failure_preserves_prior_event_and_does_not_guess(self):
        self.poll(self.current("complete"))
        with patch.object(monitor.ops, "status", side_effect=TimeoutError("offline")):
            result = monitor.poll(object(), self.job_path, self.output)
        self.assertEqual(result["monitor_state"], "retry_pending")
        self.assertEqual(result["terminal_event"]["status"], "complete")
        self.assertEqual(result["notification"]["state"], "not_delivered")
        with patch.object(monitor.ops, "status", return_value=self.current("complete")):
            recovered = monitor.poll(object(), self.job_path, self.output)
        self.assertEqual(recovered["monitor_state"], "observed")
        self.assertEqual(recovered["terminal_event"], result["terminal_event"])
        self.job["version"] = 8
        monitor.ops.save(self.job_path, self.job)
        new_event = monitor.poll(object(), self.job_path, self.output)
        self.assertNotIn("terminal_event", new_event)

    def test_ref_version_and_saved_session_mismatch_do_not_create_event(self):
        for current in (self.current("complete", ref="other/notebook"),
                        self.current("complete", version=8),
                        self.current("complete", kernel_session_id=None)):
            with self.subTest(current=current), patch.object(monitor.ops, "status", return_value=current):
                result = monitor.poll(object(), self.job_path, self.output)
                self.assertEqual(result["monitor_state"], "retry_pending")
                self.assertNotIn("terminal_event", result)

    def test_public_status_missing_session_id_is_enriched_by_exact_identity_lookup(self):
        self.job["is_private"] = False
        monitor.ops.save(self.job_path, self.job)
        current = self.current("complete", kernel_session_id=None)
        identity = {"ref": "owner/notebook", "version": 7, "kernel_session_id": 123,
                    "source": "public_get_kernel_view_model"}
        with patch.object(monitor.ops, "status", return_value=current), \
                patch.object(monitor.ops, "resolve_session_identity", return_value=identity) as resolve:
            result = monitor.poll(object(), self.job_path, self.output)
        resolve.assert_called_once_with(unittest.mock.ANY, "owner/notebook", 7, is_private=False)
        self.assertEqual(result["monitor_state"], "observed")
        self.assertEqual(result["observation"]["kernel_session_id"], 123)
        self.assertEqual(result["terminal_event"]["kernel_session_id"], 123)

    def test_public_identity_lookup_rejects_missing_or_mismatched_binding(self):
        self.job["is_private"] = False
        monitor.ops.save(self.job_path, self.job)
        bad_identities = (
            {"ref": "owner/notebook", "version": 7, "kernel_session_id": None},
            {"ref": "owner/notebook", "version": 7, "kernel_session_id": 999},
            {"ref": "other/notebook", "version": 7, "kernel_session_id": 123},
            {"ref": "owner/notebook", "version": 8, "kernel_session_id": 123},
        )
        for index, identity in enumerate(bad_identities):
            with self.subTest(identity=identity):
                self.output = self.root / f"bad-public-identity-{index}"
                with patch.object(monitor.ops, "status", return_value=self.current("complete", kernel_session_id=None)), \
                        patch.object(monitor.ops, "resolve_session_identity", return_value=identity):
                    result = monitor.poll(object(), self.job_path, self.output)
                self.assertEqual(result["monitor_state"], "retry_pending")
                self.assertNotIn("terminal_event", result)

    def test_matching_status_session_skips_public_identity_lookup(self):
        self.job["is_private"] = False
        monitor.ops.save(self.job_path, self.job)
        with patch.object(monitor.ops, "status", return_value=self.current("complete")), \
                patch.object(monitor.ops, "resolve_session_identity") as resolve:
            result = monitor.poll(object(), self.job_path, self.output)
        resolve.assert_not_called()
        self.assertEqual(result["monitor_state"], "observed")

    def test_register_saves_target_and_identity_without_starting_scheduler(self):
        self.job["is_private"] = False
        monitor.ops.save(self.job_path, self.job)
        result = monitor.register(self.job_path, self.root / "registry", monitor.target("owner/repo"))
        self.assertFalse(result["scheduler_active"])
        saved = monitor.ops.load(self.job_path)
        self.assertEqual(saved["notification"]["target"]["provider"], "chatgpt")
        self.assertEqual(saved["notification"]["event_id"], monitor.event_id(self.job))
        self.assertEqual(saved["session_identity"], self.job["session_identity"])
        self.assertFalse(monitor.ops.load(result["registry_file"])["is_private"])
        self.assertTrue(Path(result["registry_file"]).exists())

    def test_automation_plan_pins_job_hourly_and_requires_final_marker(self):
        plan = monitor.automation_plan(self.job)
        prompt = plan["prompt"]
        self.assertEqual(plan["timing_mode"], "condition_watch")
        self.assertIn("RRULE:FREQ=HOURLY", plan["schedule"])
        self.assertIn(self.job["ref"], prompt)
        self.assertIn("version 7", prompt)
        self.assertIn(monitor.event_id(self.job), prompt)
        self.assertIn("KAGGLE_EVENT:", prompt)
        self.assertIn("最終回答", prompt)
        self.assertIn("端末受信は未確認", prompt)

    def test_record_automation_accepts_only_exact_successful_plan(self):
        plan = monitor.automation_plan(self.job)
        valid = {"success": True, "automation": {"id": "auto-1", "is_enabled": True,
                 "prompt": plan["prompt"], "timing_mode": plan["timing_mode"]}}
        with self.assertRaises(ValueError):
            monitor.record_automation(self.job_path, {"success": False, "automation": valid["automation"]})
        wrong = json.loads(json.dumps(valid))
        wrong["automation"]["prompt"] += " changed"
        with self.assertRaises(ValueError):
            monitor.record_automation(self.job_path, wrong)
        result = monitor.record_automation(self.job_path, {"structuredContent": valid})
        self.assertEqual(result["automation_id"], "auto-1")
        self.assertEqual(result["delivery_status"], "unknown")
        self.assertNotEqual(result["state"], "delivered")

    def test_record_run_requires_matching_automation_run_and_complete_marker(self):
        plan = monitor.automation_plan(self.job)
        monitor.record_automation(self.job_path, {"success": True, "automation": {
            "id": "auto-1", "is_enabled": True, "prompt": plan["prompt"], "timing_mode": plan["timing_mode"]}})
        identifier = monitor.event_id(self.job)

        def response(**overrides):
            result = {"run_id": "run-1", "created_at": "2026-01-01T00:01:00Z",
                      "completed_at": "2026-01-01T00:02:00Z", "is_failure_message": False, "content_truncated": False,
                      "content_text": f"finished KAGGLE_EVENT:{identifier}"}
            result.update(overrides)
            return {"automations": [{"id": "auto-1", "run_details": {
                "last_run": {"id": "run-1", "started_at": "2026-01-01T00:00:00Z", "recorded_status": "completed"},
                "latest_result": result}}]}

        accepted = monitor.record_run(self.job_path, response())
        self.assertEqual(accepted["state"], "run_completed")
        self.assertEqual(accepted["delivery_status"], "unknown")
        self.assertNotEqual(accepted["state"], "delivered")
        rejected = (
            response(is_failure_message=True), response(content_truncated=True),
            response(content_text="KAGGLE_EVENT:other"), response(run_id="different"),
            {"automations": [{"id": "other", "run_details": {}}]},
            response(completed_at=None), response(content_text=f"truncated KAGGLE_EVENT:{identifier[:12]}"),
        )
        for index, payload in enumerate(rejected):
            with self.subTest(index=index):
                if payload.get("automations", [{}])[0].get("id") != "auto-1":
                    with self.assertRaises(ValueError):
                        monitor.record_run(self.job_path, payload)
                    continue
                result = monitor.record_run(self.job_path, payload)
                self.assertEqual(result["state"], "retry_pending")
                self.assertEqual(result["delivery_status"], "unknown")


    def test_secret_values_are_redacted_from_persisted_observation_and_errors(self):
        secret = "test-kaggle-secret-123"
        prior = os.environ.get("KAGGLE_API_TOKEN")
        os.environ["KAGGLE_API_TOKEN"] = secret
        try:
            result = self.poll(self.current("complete", failure_message=f"failed with {secret}"))
            serialized = (self.output / monitor.event_id(self.job) / "observation.json").read_text()
            self.assertNotIn(secret, serialized)
            self.assertIn("[REDACTED]", serialized)
            self.assertNotIn(secret, json.dumps(result))
        finally:
            if prior is None:
                os.environ.pop("KAGGLE_API_TOKEN", None)
            else:
                os.environ["KAGGLE_API_TOKEN"] = prior

    def test_register_preserves_existing_automation_binding_and_job_identity(self):
        self.job["notification"].update(automation_id="auto-bound", state="registered",
                                        delivery_status="unknown")
        original_identity = json.loads(json.dumps(self.job["session_identity"]))
        original_ref, original_version, original_state = self.job["ref"], self.job["version"], self.job["state"]
        monitor.ops.save(self.job_path, self.job)
        result = monitor.register(self.job_path, self.root / "registry", monitor.target("owner/repo"))
        saved = monitor.ops.load(self.job_path)
        self.assertFalse(result["scheduler_active"])
        self.assertEqual(saved["notification"]["automation_id"], "auto-bound")
        self.assertEqual(saved["notification"]["state"], "registered")
        self.assertEqual(saved["ref"], original_ref)
        self.assertEqual(saved["version"], original_version)
        self.assertEqual(saved["state"], original_state)
        self.assertEqual(saved["session_identity"], original_identity)

    def test_run_result_created_before_latest_run_is_stale(self):
        plan = monitor.automation_plan(self.job)
        monitor.record_automation(self.job_path, {"success": True, "automation": {
            "id": "auto-1", "is_enabled": True, "prompt": plan["prompt"], "timing_mode": plan["timing_mode"]}})
        identifier = monitor.event_id(self.job)
        payload = {"automations": [{"id": "auto-1", "run_details": {
            "last_run": {"id": "run-1", "started_at": "2026-02-01T00:00:00Z", "recorded_status": "completed"},
            "latest_result": {"run_id": "run-1", "created_at": "2026-01-31T23:59:00Z",
                "completed_at": "2026-02-01T00:01:00Z", "is_failure_message": False,
                "content_truncated": False, "content_text": f"KAGGLE_EVENT:{identifier}"}}}]}
        result = monitor.record_run(self.job_path, payload)
        self.assertEqual(result["state"], "retry_pending")
        self.assertEqual(result["delivery_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
