import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jse.lab.__main__ import demo_config, fixture_answer
from jse.lab.engine import Engine, verify
from jse.lab.fetch import FixtureTransport
from jse.lab.state import LabError, Store


class Failure(FixtureTransport):
    def __init__(self, code="ProxyError"):
        self.code = code
    def get(self, *args):
        raise LabError(self.code)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"

    def finished_failure(self, code="ProxyError", config=None):
        store = Store.create(self.run, config or demo_config())
        engine = Engine(store, Failure(code))
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        engine.decide(store.state["gate"]["id"], "approve", "test permission")
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        return store, engine

    def request(self, store):
        return json.loads(store.artifact(store.state["active_request"]).read_text())

    def test_retry_preserves_every_attempt_http_byte_and_answer(self):
        store, engine = self.finished_failure()
        previous = copy.deepcopy(store.state)
        engine.retry_failed("同一run・残予算内で再試行を許可")
        for name, arm in store.state["arms"].items():
            self.assertEqual(arm["attempts"], previous["arms"][name]["attempts"])
            self.assertEqual(arm["http"], previous["arms"][name]["http"])
            self.assertEqual(arm["bytes_charged"], previous["arms"][name]["bytes_charged"])
            self.assertFalse(arm["done"])
            self.assertEqual(arm["frontier"][0]["url"], previous["arms"][name]["attempts"][-1]["url"])
        self.assertEqual(store.state["agent_requests"], previous["agent_requests"])
        self.assertEqual(store.state["config_hash"], previous["config_hash"])
        self.assertEqual(store.state["decisions"][-1]["decision"], "retry")
        restored = Store(self.run)
        restored_engine = Engine(restored, FixtureTransport())
        restored_engine.advance()
        while restored.state["status"] == "awaiting_agent":
            restored_engine.answer(fixture_answer(self.request(restored)))
            restored_engine.advance()
        self.assertEqual(restored.state["status"], "awaiting_human")
        self.assertTrue(verify(restored)["ok"])
        self.assertGreater(restored.state["arms"]["bfs"]["bytes_charged"], previous["arms"]["bfs"]["bytes_charged"])
        self.assertEqual(restored.state["arms"]["bfs"]["attempts"][0]["error"], "ProxyError")

    def test_timeout_is_retryable_but_http_denial_is_not(self):
        store, engine = self.finished_failure("TimeoutError")
        engine.retry_failed("test")
        self.assertEqual(store.state["status"], "ready")
        for invalid in ("http_403", "robots_denied", "outside_scope", "body_deadline"):
            with tempfile.TemporaryDirectory() as directory:
                original = self.run
                self.run = Path(directory) / "run"
                _, rejected = self.finished_failure(invalid)
                with self.assertRaises(LabError):
                    rejected.retry_failed("test")
                self.run = original

    def test_exhausted_bytes_or_model_budget_cannot_be_reset(self):
        config = demo_config()
        config["limits"].update(bytes_per_arm=20000, agent_requests=3)
        store, engine = self.finished_failure(config=config)
        before = copy.deepcopy(store.state)
        with self.assertRaises(LabError):
            engine.retry_failed("test")
        self.assertEqual(store.state, before)

    def test_gate_tampering_and_repeated_retry_are_rejected(self):
        store, engine = self.finished_failure()
        store.state["gate"]["content"]["review"]["findings"] = "changed"
        with self.assertRaises(LabError):
            engine.retry_failed("test")
        store.state["gate"]["content"]["review"]["findings"] = fixture_answer({"request_id": "unused", "kind": "review"})["findings"]
        engine.retry_failed("test")
        with self.assertRaises(LabError):
            engine.retry_failed("test")

    def test_diagnostic_uses_same_failed_robots_and_same_ledger(self):
        store, engine = self.finished_failure()
        before = copy.deepcopy(store.state["arms"]["bfs"])
        old_gate = store.state["gate"]["id"]
        with patch("jse.lab.fetch.LiveTransport", return_value=FixtureTransport()):
            result = engine.diagnose_proxy("bfs", "urllib")
        after = store.state["arms"]["bfs"]
        self.assertEqual(len(after["http"]), len(before["http"]) + 1)
        self.assertEqual(after["http"][-1]["kind"], "proxy_diagnostic")
        self.assertEqual(result["url"], before["http"][-1]["url"])
        self.assertEqual(result["status"], 200)
        self.assertFalse(result["origin_block_confirmed"])
        self.assertGreater(after["bytes_charged"], before["bytes_charged"])
        self.assertNotEqual(store.state["gate"]["id"], old_gate)
        self.assertTrue(verify(store)["ok"])


if __name__ == "__main__":
    unittest.main()
