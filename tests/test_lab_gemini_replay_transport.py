from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/sku_gemini_replay_transport.py"
SPEC = importlib.util.spec_from_file_location("sku_gemini_replay_transport", SCRIPT)
transport = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = transport
SPEC.loader.exec_module(transport)


SECRET = "fixture-only-token-DO-NOT-USE"


class FakeResponse:
    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


class FakeOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def model_response(text: str = '{"ok": true}', finish: str = "STOP") -> bytes:
    return json.dumps({
        "modelVersion": "fixture-model-version",
        "usageMetadata": {"promptTokenCount": 3},
        "candidates": [{
            "index": 0,
            "finishReason": finish,
            "content": {"parts": [{"text": text}]},
        }],
    }).encode()


class GeminiReplayTransportTests(unittest.TestCase):
    def assert_secret_absent(self, paths):
        for path in paths:
            if path.exists():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))

    def test_auth_check_redacts_credentials_and_limits_invalid_auth_details(self):
        tokeninfo = urllib.error.HTTPError(
            "https://oauth2.googleapis.com/tokeninfo?access_token=" + SECRET,
            400,
            "bad request",
            {},
            io.BytesIO(json.dumps({
                "error": "invalid_token",
                "error_description": "rejected " + SECRET,
            }).encode()),
        )
        project = urllib.error.HTTPError(
            "https://aiplatform.googleapis.com/v1/projects?token=" + SECRET,
            403,
            "forbidden",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "private detail " + SECRET,
                }
            }).encode()),
        )
        opener = FakeOpener([tokeninfo, project])

        result = transport.auth_check(SECRET, opener=opener)

        self.assertFalse(result["authenticated"])
        self.assertEqual(result["probes"][0]["error"], "invalid_token")
        self.assertEqual(result["probes"][1]["error_status"], "PERMISSION_DENIED")
        self.assertNotIn("url", json.dumps(result).lower())
        self.assertNotIn("error_description", json.dumps(result))
        self.assertEqual(len(opener.requests), 2)
        # The secret is used for the probe, but is never returned as an artifact.
        self.assertIn(SECRET, opener.requests[0][0].full_url)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_auth_check_success_does_not_return_auth_headers_or_probe_urls(self):
        opener = FakeOpener([FakeResponse(status=200), FakeResponse(status=200)])

        result = transport.auth_check(SECRET, opener=opener)

        self.assertTrue(result["authenticated"])
        self.assertTrue(all(p["authenticated"] for p in result["probes"]))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("Authorization", json.dumps(result))
        self.assertNotIn("url", json.dumps(result).lower())

    def test_provider_fields_keep_allowlisted_fields_and_drop_secrets_and_thoughts(self):
        output = transport.provider_fields({
            "modelVersion": "fixture-model-version",
            "usageMetadata": {"promptTokenCount": 3},
            "private": SECRET,
            "promptFeedback": {"blockReason": "private"},
            "error": {
                "status": "PERMISSION_DENIED",
                "code": 403,
                "message": "private " + SECRET,
                "details": [{"secret": SECRET}],
            },
            "candidates": [{
                "index": 0,
                "finishReason": "STOP",
                "safetyRatings": [{"category": "fixture"}],
                "private": SECRET,
                "content": {"parts": [
                    {"text": "visible"},
                    {"text": SECRET, "thought": True},
                ]},
            }],
        })

        serialized = json.dumps(output)
        self.assertIn("modelVersion", output)
        self.assertEqual(output["error_status"], "PERMISSION_DENIED")
        self.assertEqual(output["error_code"], 403)
        self.assertEqual(output["candidates"][0]["content"]["parts"], [{"text": "visible"}])
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn("message", serialized)
        self.assertNotIn("promptFeedback", serialized)

    def test_model_json_rejects_non_stop_finish_and_accepts_single_stop_json(self):
        non_stop = {"candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": {"parts": [{"text": '{"ok": true}'}]},
        }]}
        self.assertEqual(transport.model_json(non_stop), (None, "non_STOP_finish"))

        valid = {"candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": '{"ok": true}'}]},
        }]}
        self.assertEqual(transport.model_json(valid), ({"ok": True}, None))

    def test_call_once_persists_redacted_success_and_never_retries_completed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            answer = run / "answers/001.json"
            opener = FakeOpener([FakeResponse(model_response('{"text": "' + SECRET + '"}'))])

            result = transport.call_once(run, "matching", 1, {"fixture": True}, answer, SECRET, opener=opener)

            self.assertEqual(result["http_status"], 200)
            self.assertIsNone(result["parse_error"])
            self.assertTrue(answer.exists())
            attempt = run / "provider/matching/001.attempt.json"
            response = run / "provider/matching/001.response.json"
            receipt = run / "provider/matching/001.receipt.json"
            self.assert_secret_absent([attempt, response, receipt, answer])
            self.assertIn("[REDACTED]", answer.read_text())
            self.assertEqual(len(opener.requests), 1)
            self.assertEqual(opener.requests[0][0].get_header("Authorization"), "Bearer " + SECRET)

            with self.assertRaises(FileExistsError):
                transport.call_once(run, "matching", 1, {"fixture": True}, answer, SECRET, opener=opener)
            self.assertEqual(len(opener.requests), 1)
            self.assert_secret_absent(list((run / "provider/matching").glob("*.json")) + [answer])

    def test_verify_attempt_binds_body_provider_response_and_parsed_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            answer = run / "answers/001.json"
            body = {"fixture": True}
            transport.call_once(run, "matching", 1, body, answer, SECRET,
                                opener=FakeOpener([FakeResponse(model_response('{"ok": true}'))]))

            receipt = transport.verify_attempt(run, "matching", 1, body, answer)
            self.assertEqual(receipt["http_status"], 200)

            answer.write_text('{"ok": false}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "parsed answer missing or changed"):
                transport.verify_attempt(run, "matching", 1, body, answer)

    def test_verify_attempt_rejects_response_and_request_body_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            answer = run / "answers/001.json"
            body = {"fixture": True}
            transport.call_once(run, "matching", 1, body, answer, SECRET,
                                opener=FakeOpener([FakeResponse(model_response('{"ok": true}'))]))

            with self.assertRaisesRegex(ValueError, "attempt request body changed"):
                transport.verify_attempt(run, "matching", 1, {"fixture": False}, answer)

            response = run / "provider/matching/001.response.json"
            response.write_text(response.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provider response changed"):
                transport.verify_attempt(run, "matching", 1, body, answer)

    def test_call_once_refuses_resume_when_attempt_ledger_exists_without_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            attempt = run / "provider/signature/007.attempt.json"
            attempt.parent.mkdir(parents=True)
            attempt.write_text('{"stage":"signature","index":7}\n', encoding="utf-8")
            answer = run / "answers/007.json"
            opener = FakeOpener([FakeResponse(model_response())])

            with self.assertRaises(FileExistsError):
                transport.call_once(run, "signature", 7, {"fixture": True}, answer, SECRET, opener=opener)

            self.assertEqual(opener.requests, [])
            self.assertFalse(answer.exists())

    def test_call_once_suppresses_http_error_body_and_auth_url_secrets(self):
        error = urllib.error.HTTPError(
            "https://aiplatform.googleapis.com/v1/models?token=" + SECRET,
            403,
            "forbidden " + SECRET,
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "code": 403,
                    "status": "PERMISSION_DENIED",
                    "message": "server echoed " + SECRET,
                    "details": [{"private": SECRET}],
                }
            }).encode()),
        )
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            answer = run / "answers/009.json"
            opener = FakeOpener([error])

            result = transport.call_once(run, "matching", 9, {"fixture": True}, answer, SECRET, opener=opener)

            self.assertEqual(result["http_status"], 403)
            self.assertEqual(result["error_status"], "PERMISSION_DENIED")
            self.assertEqual(result["error_code"], 403)
            self.assertEqual(result["parse_error"], "transport_failure")
            self.assertFalse(answer.exists())
            self.assertEqual(len(opener.requests), 1)
            paths = list((run / "provider/matching").glob("*.json"))
            self.assert_secret_absent(paths)
            for path in paths:
                saved = path.read_text()
                self.assertNotIn("server echoed", saved)
                self.assertNotIn("details", saved)
                self.assertNotIn("token=", saved)

    def test_call_once_does_not_persist_exception_messages_with_secret_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            answer = run / "answers/010.json"
            opener = FakeOpener([urllib.error.URLError("failed URL https://example.invalid/" + SECRET)])

            result = transport.call_once(run, "matching", 10, {"fixture": True}, answer, SECRET, opener=opener)

            self.assertEqual(result["exception_type"], "URLError")
            self.assertEqual(result["parse_error"], "transport_failure")
            self.assertFalse(answer.exists())
            self.assert_secret_absent(list((run / "provider/matching").glob("*.json")))
            serialized = "".join(p.read_text() for p in (run / "provider/matching").glob("*.json"))
            self.assertNotIn("example.invalid", serialized)


if __name__ == "__main__":
    unittest.main()
