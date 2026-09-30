import copy
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from jse.lab.__main__ import demo_config, export_checkpoint, fixture_answer
from jse.lab.agent import API_URL, answer_with_model
from jse.lab.engine import Engine, verify
from jse.lab.state import LabError, Store, encoded


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store.create(Path(self.temp.name) / "run", demo_config())
        self.engine = Engine(self.store)
        self.engine.advance()

    def request(self):
        return json.loads(self.store.artifact(self.store.state["active_request"]).read_text())

    def response(self, model="gpt-6-luna", status="completed", answer=None):
        return {"id": "resp_fixture", "model": model, "status": status,
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(answer or fixture_answer(self.request()), ensure_ascii=False)}]}]}

    def api(self, data):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = encoded(data)
        return opener

    def test_responses_uses_exact_model_schema_request_and_records_server_evidence(self):
        request = self.request()
        opener = self.api(self.response())
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-api-secret"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=opener):
            result = answer_with_model(self.engine, "responses")
        sent = opener.open.call_args.args[0]
        payload = json.loads(sent.data)
        self.assertEqual(sent.full_url, API_URL)
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertFalse(payload["store"])
        self.assertEqual(json.loads(payload["input"]), request)
        self.assertNotIn("tools", payload)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["max_output_tokens"], 4000)
        self.assertEqual(result["status"]["status"], "awaiting_human")
        self.assertTrue(result["model_call"]["model_runtime_verified"])
        self.assertEqual(result["model_call"]["model_returned"], "gpt-6-luna")
        self.assertTrue(verify(self.store)["ok"])
        self.assertTrue(verify(self.store)["model_runtime_verified"])
        self.assertNotIn("test-api-secret", (self.store.directory / "state.json").read_text())
        archive = Path(self.temp.name) / "checkpoint.zip"
        export_checkpoint(self.store, archive)
        with zipfile.ZipFile(archive) as zipped:
            self.assertIn("model-calls/001.json", zipped.namelist())

    def test_missing_key_and_wrong_requested_model_do_not_consume_call(self):
        with patch.dict("os.environ", {}, clear=True), patch("urllib.request.build_opener", side_effect=AssertionError("network")):
            for model in ("gpt-6-luna", "gpt-6-sol"):
                with self.assertRaises(LabError):
                    answer_with_model(self.engine, "responses", model=model)
        self.assertEqual(self.store.state.get("model_calls", []), [])
        self.assertFalse(self.store.state["agent_requests"][0]["answered"])

    def test_wrong_returned_model_or_incomplete_output_are_rejected(self):
        for model, status in (("gpt-6-sol", "completed"), ("gpt-6-luna", "incomplete")):
            with self.subTest(model=model, status=status):
                opener = self.api(self.response(model, status))
                with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=opener):
                    with self.assertRaises(LabError):
                        answer_with_model(self.engine, "responses", max_calls=3, retry_call=bool(self.store.state.get("model_calls")))
                self.assertFalse(self.store.state["agent_requests"][0]["answered"])
                self.assertFalse(self.store.state["model_calls"][-1]["model_runtime_verified"])
                self.assertTrue(verify(self.store)["ok"])

    def test_failed_call_is_reserved_and_never_retried_without_opt_in(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("proxy failed test-key")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(LabError):
                answer_with_model(self.engine, "responses")
            with self.assertRaises(LabError):
                answer_with_model(self.engine, "responses", max_calls=2)
            self.assertEqual(opener.open.call_count, 1)
            with self.assertRaises(LabError):
                answer_with_model(self.engine, "responses", max_calls=2, retry_call=True)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(len(self.store.state["model_calls"]), 2)
        self.assertNotIn("test-key", str(self.store.state["model_calls"]))
        self.assertTrue(verify(self.store)["ok"])

    def test_interrupted_model_call_survives_checkpoint_and_retry(self):
        opener = MagicMock()
        opener.open.side_effect = KeyboardInterrupt()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(KeyboardInterrupt):
                answer_with_model(self.engine, "responses")
        restored = Store(self.store.directory)
        self.assertEqual(restored.state["model_calls"][0]["status"], "interrupted")
        self.assertTrue(verify(restored)["ok"])

    def test_removed_cli_backend_does_not_consume_a_model_call(self):
        with self.assertRaises(LabError):
            answer_with_model(self.engine, "codex")
        self.assertEqual(self.store.state.get("model_calls", []), [])
        self.assertFalse(self.store.state["agent_requests"][0]["answered"])

    def test_model_metadata_tampering_is_detected(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=self.api(self.response())):
            answer_with_model(self.engine, "responses")
        call = self.store.state["model_calls"][0]
        self.store.artifact(call["path"]).write_text("{}")
        self.assertFalse(verify(self.store)["ok"])

    def test_invalid_model_answer_passes_through_existing_validation(self):
        opener = self.api(self.response(answer={"request_id": "wrong", "unexpected": True}))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("jse.lab.agent.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(LabError):
                answer_with_model(self.engine, "responses")
        self.assertEqual(self.store.state["agent_requests"][0]["invalid_answers"], 1)
        self.assertFalse(self.store.state["agent_requests"][0]["answered"])
        self.assertTrue(verify(self.store)["ok"])


if __name__ == "__main__":
    unittest.main()
