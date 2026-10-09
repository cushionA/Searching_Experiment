"""Check durable request limits and preservation of captured bytes, offline."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "au_capture_budget_test", ROOT / "experiments/sku-matching/fetch_au.py")
fetch_au = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_au)
URL = "https://wowma.jp/api/item/704502086"


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json"}

    def geturl(self):
        return URL


class RecordedOpener:
    def __init__(self, client, responses):
        self.client, self.responses = client, iter(responses)
        self.calls = 0

    def open(self, request, timeout):
        state = json.loads(self.client.state_path.read_text())
        # The persistent charge must exist before the network operation begins.
        if len(state["attempts"]) != self.calls + 1:
            raise AssertionError("request was not charged before opening")
        self.calls += 1
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return Response(value)


class AuCaptureBudgetTests(unittest.TestCase):
    def test_records_bytes_and_serializable_metadata_before_enforcing_http_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            client = fetch_au.BudgetedClient(Path(temp), max_http=1)
            opener = RecordedOpener(client, [b'{"stock":null}'])
            client.opener = opener
            meta, body = client.get(URL, {}, "detail", "item", "704502086")
            self.assertEqual(body, b'{"stock":null}')
            self.assertEqual((client.out / meta["capture_file"]).read_bytes(), body)
            json.dumps(meta)  # A self-reference in attempt summaries is invalid.
            self.assertEqual(client.captured_bytes, len(body))
            with self.assertRaises(fetch_au.BudgetExceeded):
                client.get(URL, {}, "detail", "item", "704502086")
            self.assertEqual(opener.calls, 1)
            self.assertEqual(client.charged_http, 1)

    def test_failed_request_consumes_cap_and_retry_cannot_open_again(self):
        with tempfile.TemporaryDirectory() as temp:
            client = fetch_au.BudgetedClient(Path(temp), max_http=1)
            opener = RecordedOpener(client, [URLError("offline failure")])
            client.opener = opener
            with self.assertRaises(fetch_au.BudgetExceeded):
                client.get(URL, {}, "detail", "item", "704502086")
            self.assertEqual(opener.calls, 1)
            saved = json.loads(client.state_path.read_text())
            self.assertEqual(len(saved["attempts"]), 1)
            self.assertEqual(saved["attempts"][0]["result"]["outcome"], "network_error")

    def test_resume_retains_byte_and_request_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            client = fetch_au.BudgetedClient(directory, max_http=2, max_body_bytes=5)
            client.opener = RecordedOpener(client, [b"12345"])
            client.get(URL, {}, "detail", "item", "704502086")
            resumed = fetch_au.BudgetedClient(directory, max_http=2, max_body_bytes=5)
            opener = RecordedOpener(resumed, [])
            resumed.opener = opener
            with self.assertRaises(fetch_au.BudgetExceeded):
                resumed.get(URL, {}, "detail", "item", "704502086")
            self.assertEqual(opener.calls, 0)
            self.assertEqual(resumed.charged_http, 1)
            self.assertEqual(resumed.captured_bytes, 5)


if __name__ == "__main__":
    unittest.main()
