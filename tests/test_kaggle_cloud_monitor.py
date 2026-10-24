"""Offline tests for Cloud-native Kaggle monitor state recovery and receipts."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".agents/skills/kaggle-ops/scripts/kaggle_cloud_monitor.py"
SPEC = importlib.util.spec_from_file_location("kaggle_cloud_monitor_under_test", SCRIPT)
cloud_monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud_monitor)


class KaggleCloudMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = self.root / "output"
        self.ref = "owner/notebook"
        self.version = 7
        self.identifier = cloud_monitor.event_id(self.ref, self.version)
        self.target = cloud_monitor.monitor.target("owner/repo")

    def tearDown(self):
        self.tmp.cleanup()

    def observation(self, status="complete", **kwargs):
        event = {"event_id": self.identifier, "ref": self.ref, "version": self.version,
                 "status": status, "reason": status, "verification": "not_evaluated"}
        state = {"event_id": self.identifier, "ref": self.ref, "version": self.version,
                 "notification": {"target": self.target, "state": "not_delivered"},
                 "monitor_state": "observed", "poll_attempts": 1, "terminal_event": event}
        state.update(kwargs)
        return state

    def save_snapshot(self, jobs):
        path = self.root / "snapshot.json"
        cloud_monitor.ops.save(path, {"schema_version": 1, "jobs": jobs})
        return path

    def save_local(self, state):
        path = self.output / self.identifier / "observation.json"
        cloud_monitor.ops.save(path, state)
        return path

    def test_restore_validates_snapshot_binding_and_preserves_local_terminal_event(self):
        local = self.observation("error")
        local["terminal_event"]["reason"] = "prior confirmed error"
        self.save_local(local)
        incoming = self.observation("complete")
        incoming.pop("terminal_event")
        incoming["poll_attempts"] = 2
        result = cloud_monitor.restore(self.save_snapshot([incoming]), self.output)
        saved = cloud_monitor.ops.load(self.output / self.identifier / "observation.json")
        self.assertEqual(result["count"], 1)
        self.assertEqual(saved["terminal_event"]["reason"], "prior confirmed error")
        self.assertEqual(saved["poll_attempts"], 2)

    def test_restore_rejects_bad_identity_and_target(self):
        bad_states = []
        wrong_id = self.observation()
        wrong_id["event_id"] = "0" * 64
        bad_states.append(wrong_id)
        wrong_version = self.observation()
        wrong_version["terminal_event"]["version"] = 8
        bad_states.append(wrong_version)
        wrong_target = self.observation()
        wrong_target["notification"]["target"]["snapshot_path"] = "../escape"
        bad_states.append(wrong_target)
        for state in bad_states:
            with self.subTest(state=state), self.assertRaises(ValueError):
                cloud_monitor.restore(self.save_snapshot([state]), self.output)

    def test_restore_rejects_conflicting_local_target_binding(self):
        local = self.observation()
        local["notification"]["target"] = cloud_monitor.monitor.target("other/repo")
        self.save_local(local)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            cloud_monitor.restore(self.save_snapshot([self.observation()]), self.output)

    def test_pending_includes_complete_all_review_error_and_cancel(self):
        statuses = ("complete", "error", "cancel_acknowledged")
        for index, status in enumerate(statuses):
            identifier = cloud_monitor.event_id(f"owner/notebook-{index}", self.version)
            state = self.observation(status)
            state["ref"] = f"owner/notebook-{index}"
            state["event_id"] = identifier
            state["terminal_event"].update(event_id=identifier, ref=state["ref"])
            if index == 0:
                state["terminal_event"]["verification"] = "all_review"
            cloud_monitor.ops.save(self.output / identifier / "observation.json", state)
        result = cloud_monitor.pending(self.output)
        self.assertEqual({item["status"] for item in result}, set(statuses))
        all_review = next(item for item in result if item["status"] == "complete")
        self.assertEqual(all_review["ref"], "owner/notebook-0")

    def test_acknowledge_requires_actual_matching_marker_and_keeps_only_hash(self):
        self.save_local(self.observation("complete"))
        message = self.root / "message.txt"
        message.write_text(f"Completed {self.ref}, version 7. KAGGLE_EVENT:{self.identifier}", encoding="utf-8")
        receipt = cloud_monitor.acknowledge(self.output, self.identifier, message)
        self.assertEqual(receipt["state"], "conversation_published")
        self.assertEqual(receipt["provenance"], "prior_assistant_final")
        self.assertEqual(receipt["delivery_status"], "unknown")
        saved_text = (self.output / self.identifier / "observation.json").read_text(encoding="utf-8")
        self.assertNotIn("Completed ", saved_text)
        self.assertNotIn("message", receipt)
        self.assertEqual(cloud_monitor.pending(self.output), [])

    def test_failed_acknowledge_leaves_terminal_event_pending_and_duplicate_is_idempotent(self):
        self.save_local(self.observation("cancel_acknowledged"))
        message = self.root / "failed-message.txt"
        message.write_text(f"KAGGLE_EVENT:{self.identifier}", encoding="utf-8")
        with self.assertRaises(ValueError):
            cloud_monitor.acknowledge(self.output, self.identifier, message)
        self.assertEqual(len(cloud_monitor.pending(self.output)), 1)
        message.write_text(f"Cancelled {self.ref}, v7. KAGGLE_EVENT:{self.identifier}", encoding="utf-8")
        first = cloud_monitor.acknowledge(self.output, self.identifier, message)
        second = cloud_monitor.acknowledge(self.output, self.identifier, message)
        self.assertEqual(first, second)
        self.assertEqual(cloud_monitor.pending(self.output), [])

    def test_plan_targets_fixed_registry_snapshot_and_hourly_condition_watch(self):
        plan = cloud_monitor.plan("cushionA/Searching_Experiment", registry_ref="main")
        self.assertEqual(plan["timing_mode"], "condition_watch")
        self.assertIn("RRULE:FREQ=HOURLY", plan["schedule"])
        self.assertIn(".github/kaggle-jobs/", plan["prompt"])
        self.assertIn("KAGGLE_API_TOKEN", plan["prompt"])
        self.assertIn("same Codex conversation", plan["prompt"])
        self.assertIn("compare-and-swap", plan["prompt"])
        self.assertIn("Never submit or cancel", plan["prompt"])


if __name__ == "__main__":
    unittest.main()
