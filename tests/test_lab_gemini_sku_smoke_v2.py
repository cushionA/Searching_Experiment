from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/trial_gemini_sku_smoke_v2.py"
SPEC = importlib.util.spec_from_file_location("gemini_sku_smoke_v2", SCRIPT)
trial = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(trial)


def fixture_case() -> dict:
    return {
        "case_id": "fake-case", "cohort": "fixture",
        "rakuten_conditions": [
            {"condition_id": "R0", "axis": "色", "value": "赤", "choices": ["赤", "青"]},
        ],
        "au_rows": [
            {"row_key": "very-long-private-row-key-one", "conditions": [
                {"condition_id": "A:long-one:0", "axis": "色", "value": "赤", "source": {"raw_file": "fake"}},
            ]},
            {"row_key": "very-long-private-row-key-two", "conditions": [
                {"condition_id": "A:long-two:0", "axis": "色", "value": "青", "source": {"raw_file": "fake"}},
            ]},
        ],
        "sources": [{"source_id": "S0", "kind": "title", "text": "架空の商品説明", "source": {"raw_file": "fake"}}],
    }


def response(statuses=("support", "contradiction"), refs=(("A0",), ("A0",))) -> dict:
    return {"rows": [
        {"rakuten_checks": [{"status": statuses[i], "source_ids": list(refs[i])}],
         "au_checks": [{"status": statuses[i], "source_ids": ["A0"]}]}
        for i in range(2)
    ]}


class StructuredGeminiSmokeTests(unittest.TestCase):
    def test_request_uses_schema_and_compact_values_only_payload(self):
        case = fixture_case()
        body = trial.request_body(case)
        config = body["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["responseSchema"], trial.response_schema(case))
        prompt = body["contents"][0]["parts"][0]["text"]
        payload = json.loads(prompt.split("INPUT=", 1)[1])
        self.assertEqual(payload["rakuten_conditions"], [{"axis": "色", "value": "赤"}])
        self.assertEqual(payload["au_rows"], [[{"axis": "色", "value": "赤"}], [{"axis": "色", "value": "青"}]])
        self.assertEqual(payload["sources"], {"S0": {"kind": "title", "text": "架空の商品説明"}})
        self.assertNotIn("choices", payload["rakuten_conditions"][0])
        self.assertNotIn("condition_id", prompt)
        self.assertNotIn("row_key", prompt)
        self.assertNotIn("quote", prompt)
        self.assertNotIn("reason", prompt)
        row_schema = config["responseSchema"]["properties"]["rows"]["items"]
        self.assertEqual(row_schema["required"], ["rakuten_checks", "au_checks"])
        check_schema = row_schema["properties"]["rakuten_checks"]["items"]
        self.assertEqual(check_schema["properties"]["status"]["enum"], ["support", "contradiction", "unknown"])

    def test_local_A0_expands_to_each_rows_own_value(self):
        case = fixture_case()
        expanded = trial.expand_response(case, response())
        self.assertEqual(expanded["rows"][0]["rakuten_checks"][0]["evidence"][0]["quote"], "赤")
        self.assertEqual(expanded["rows"][1]["rakuten_checks"][0]["evidence"][0]["quote"], "青")
        self.assertEqual(expanded["rows"][1]["au_checks"][0]["evidence"][0]["source_id"], "A:long-two:0")

    def test_validator_fails_closed_for_missing_entries_and_bad_or_foreign_ids(self):
        case = fixture_case()
        broken = response()
        broken["rows"].pop()
        self.assertFalse(trial.validate(case, broken)[0])
        broken = response()
        broken["rows"][0]["rakuten_checks"].pop()
        self.assertFalse(trial.validate(case, broken)[0])
        for sid in ("A1", "A:long-two:0", "S99"):
            broken = response(refs=((sid,), ("A0",)))
            self.assertFalse(trial.validate(case, broken)[0], sid)
        broken = response()
        broken["rows"][0]["extra"] = "unexpected"
        self.assertFalse(trial.validate(case, broken)[0])

    def test_unknown_contradiction_and_multiple_positive_rows_do_not_adopt(self):
        case = fixture_case()
        self.assertEqual(trial.decide(case, response())["adopted_row_key"], "very-long-private-row-key-one")
        for statuses in (("unknown", "contradiction"), ("contradiction", "unknown"), ("support", "support")):
            result = trial.decide(case, response(statuses=statuses))
            self.assertEqual(result["decision"], "exclude", statuses)
            self.assertIsNone(result["adopted_row_key"])

    def test_assertion_without_evidence_is_rejected(self):
        case = fixture_case()
        broken = response()
        broken["rows"][0]["rakuten_checks"][0]["source_ids"] = []
        self.assertFalse(trial.validate(case, broken)[0])

    def test_main_keeps_raw_response_and_separates_parseerror_without_network(self):
        case = fixture_case()

        class FakeResponse:
            status_code = 200
            content = json.dumps({"modelVersion": "fake", "candidates": [{"finishReason": "STOP",
                "content": {"parts": [{"text": "not json"}]}}]}).encode()

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            def post(self, *args, **kwargs):
                return FakeResponse()

        requests_module = types.ModuleType("google.auth.transport.requests")
        requests_module.AuthorizedSession = FakeSession
        transport_module = types.ModuleType("google.auth.transport")
        transport_module.requests = requests_module
        auth_module = types.ModuleType("google.auth")
        auth_module.transport = transport_module
        google_module = types.ModuleType("google")
        google_module.auth = auth_module
        fake_cases = [case]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            argv = ["trial", "--backend", "vertex", "--output-dir", str(out)]
            stderr = io.StringIO()
            with mock.patch.dict(sys.modules, {"google": google_module, "google.auth": auth_module,
                    "google.auth.transport": transport_module, "google.auth.transport.requests": requests_module}), \
                 mock.patch.object(trial, "make_cases", return_value=(fake_cases, {})), \
                 mock.patch.object(trial, "vertex_credentials", return_value=(object(), "fake")), \
                 mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
                self.assertEqual(trial.main(), 2)
            raw = json.loads((out / "rawresponse-01.json").read_text())
            err = json.loads((out / "parseerror-01.json").read_text())
            self.assertEqual(raw["candidates"][0]["parts"][0]["text"], "not json")
            self.assertEqual(err["error_type"], "JSONDecodeError")
            self.assertTrue((out / "parseerror-01.json").exists())
            self.assertIn("JSONDecodeError", stderr.getvalue())

    def test_http_error_is_redacted_and_stops_remaining_cases(self):
        fake_token = "fake-token-for-redaction-test"
        fake_cases = []
        for i in range(6):
            case = fixture_case()
            case["case_id"] = f"fake-case-{i}"
            fake_cases.append(case)

        class FakeCredentials:
            def with_quota_project(self, project):
                return self

        class FakeResponse:
            status_code = 400
            content = json.dumps({"error": {"status": "INVALID_ARGUMENT",
                "message": f"diagnostic includes {fake_token}"}}).encode()

        class FakeSession:
            posts = []

            def __init__(self, *args, **kwargs):
                pass

            def post(self, *args, **kwargs):
                self.posts.append((args, kwargs))
                return FakeResponse()

        requests_module = types.ModuleType("google.auth.transport.requests")
        requests_module.AuthorizedSession = FakeSession
        transport_module = types.ModuleType("google.auth.transport")
        transport_module.requests = requests_module
        auth_module = types.ModuleType("google.auth")
        auth_module.transport = transport_module
        google_module = types.ModuleType("google")
        google_module.auth = auth_module
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            argv = ["trial", "--backend", "vertex", "--output-dir", str(out)]
            stderr = io.StringIO()
            with mock.patch.dict(sys.modules, {"google": google_module, "google.auth": auth_module,
                    "google.auth.transport": transport_module, "google.auth.transport.requests": requests_module}), \
                 mock.patch.dict("os.environ", {"GCP_ACCESS_TOKEN": fake_token}), \
                 mock.patch.object(trial, "make_cases", return_value=(fake_cases, {})), \
                 mock.patch.object(trial, "vertex_credentials", return_value=(FakeCredentials(), "oauth_access_token_secret_environment")), \
                 mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
                self.assertEqual(trial.main(), 2)
            self.assertEqual(len(FakeSession.posts), 1)
            self.assertTrue((out / "rawresponse-01.json").exists())
            self.assertFalse((out / "rawresponse-02.json").exists())
            saved = (out / "rawresponse-01.json").read_text()
            self.assertNotIn(fake_token, saved)
            self.assertIn("[REDACTED]", saved)
            self.assertIn("INVALID_ARGUMENT", saved)
            results = json.loads((out / "results.json").read_text())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["validation_reason"], "http_400_INVALID_ARGUMENT")
            self.assertNotIn(fake_token, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
