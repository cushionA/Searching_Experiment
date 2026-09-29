import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jse.lab.__main__ import demo_config, export_checkpoint, fixture_answer, grade, run_demo
from jse.lab.engine import Engine, verify
from jse.lab.fetch import Fetcher, FixtureTransport, LiveTransport, parse_page, public_addresses
from jse.lab.state import LabError, Store, canonical, encoded, locked


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"

    def create(self, config=None, transport=None):
        store = Store.create(self.run, config or demo_config())
        return store, Engine(store, transport)

    def request(self, store):
        return json.loads((store.directory / store.state["active_request"]).read_text(encoding="utf-8"))

    def approve(self, store, engine):
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        engine.decide(store.state["gate"]["id"], "approve", "test human decision")

    def test_demo_uses_full_flow_without_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")), patch("socket.getaddrinfo", side_effect=AssertionError("network")):
            result = run_demo(self.run)
        self.assertFalse(result["model_called"])
        self.assertEqual(result["status"]["status"], "complete")
        self.assertTrue(result["verification"]["ok"])
        self.assertEqual(result["verification"]["arms"]["luna"]["html_pages"], 4)
        self.assertIsNone(result["verification"]["arms"]["luna"]["coverage"])

    def test_human_gate_and_idempotent_run(self):
        store, engine = self.create()
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        for _ in range(3):
            engine.advance()
        self.assertEqual(store.state["status"], "awaiting_human")
        self.assertEqual(len(store.state["arms"]["bfs"]["http"]), 0)
        self.assertEqual(len(store.state["agent_requests"]), 1)
        with self.assertRaises(LabError):
            engine.decide("stale-id", "approve", "test")
        gate = store.state["gate"]["id"]
        engine.decide(gate, "approve", "test")
        with self.assertRaises(LabError):
            engine.decide(gate, "approve", "test")

    def test_configuration_and_gate_tampering_rejected(self):
        store, engine = self.create()
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        store.state["gate"]["content"]["proposal"]["method"] = "changed"
        with self.assertRaises(LabError):
            engine.decide(store.state["gate"]["id"], "approve", "test")
        store.state["config"]["limits"]["pages_per_arm"] += 1
        store.save()
        with self.assertRaises(LabError):
            Store(self.run)

    def test_reject_never_starts_crawl(self):
        store, engine = self.create()
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        engine.decide(store.state["gate"]["id"], "reject", "change scope")
        engine.advance()
        self.assertEqual(store.state["status"], "rejected")
        self.assertEqual(store.state["arms"]["bfs"]["http"], [])

    def test_cli_restart_keeps_checkpoint(self):
        config = self.root / "config.json"
        config.write_bytes(encoded(demo_config()))
        def call(*arguments):
            result = subprocess.run([sys.executable, "-B", "-m", "jse.lab", *arguments, "--run", str(self.run)], capture_output=True, encoding="utf-8", check=True)
            return json.loads(result.stdout)
        call("init", "--config", str(config))
        first = call("run")
        second = call("run")
        self.assertEqual(first, second)
        store = Store(self.run)
        answer = self.root / "answer.json"
        answer.write_bytes(encoded(fixture_answer(self.request(store))))
        waiting = call("answer", "--file", str(answer))
        call("decide", "--id", waiting["gate"]["id"], "--decision", "approve", "--note", "test")
        progressed = call("run")
        self.assertEqual(progressed["status"], "awaiting_agent")
        self.assertEqual(progressed["arms"]["bfs"]["pages"], 4)

    def test_unknown_urls_and_fabricated_quotes_are_rejected(self):
        store, engine = self.create()
        self.approve(store, engine)
        engine.advance()
        response = fixture_answer(self.request(store))
        response["next_urls"] = ["https://lab.example/invented"]
        with self.assertRaises(LabError):
            engine.answer(response)
        response = fixture_answer(self.request(store))
        response["claims"] = [{"url": "https://lab.example/", "label": "false", "quote": "観測していない文章"}]
        with self.assertRaises(LabError):
            engine.answer(response)
        engine.answer(fixture_answer(self.request(store)))
        self.assertEqual(store.state["claims"], [])

    def test_invalid_answer_limit_and_stale_response(self):
        store, engine = self.create()
        engine.advance()
        for _ in range(3):
            with self.assertRaises(LabError):
                engine.answer({"request_id": "not-current"})
        self.assertEqual(store.state["status"], "paused")
        engine.resume()
        self.assertEqual(len(store.state["agent_requests"]), 1)

    def test_failed_http_and_robots_consume_budget(self):
        class Fail(FixtureTransport):
            def get(self, url, cap, timeout, user_agent):
                return 503, {"content-type": "text/plain"}, b"unavailable", False
        config = demo_config()
        config["limits"]["requests_per_arm"] = 2
        store, engine = self.create(config, Fail())
        self.approve(store, engine)
        engine.advance()
        for arm in store.state["arms"].values():
            self.assertEqual(len(arm["attempts"]), 1)
            self.assertEqual(len(arm["http"]), 1)
            self.assertEqual(arm["attempts"][0]["error"], "robots_unavailable")
            self.assertGreater(arm["bytes_charged"], 0)
        self.assertTrue(verify(store)["ok"])

    def test_result_gate_can_retry_transient_seed_failure_without_resetting_budget(self):
        class Flaky(FixtureTransport):
            calls = 0

            def get(self, url, cap, timeout, user_agent):
                self.calls += 1
                if self.calls <= 2:
                    raise LabError("ProxyError")
                return super().get(url, cap, timeout, user_agent)

        store, engine = self.create(transport=Flaky())
        self.approve(store, engine)
        engine.advance()
        request = self.request(store)
        engine.answer(fixture_answer(request))
        self.assertEqual(store.state["gate"]["kind"], "result")
        spent = {name: len(arm["http"]) for name, arm in store.state["arms"].items()}
        engine.retry_failed("same-run retry authorized")
        self.assertEqual(store.state["phase"], "experiment")
        self.assertEqual(store.state["status"], "ready")
        self.assertTrue(all(not arm["done"] for arm in store.state["arms"].values()))
        engine.advance()
        self.assertTrue(all(len(arm["http"]) > spent[name] for name, arm in store.state["arms"].items()))
        self.assertEqual(store.state["decisions"][-1]["decision"], "retry")

    def test_robots_denial_does_not_fetch_page(self):
        store, _ = self.create()
        Fetcher(store).page("bfs", {"url": "https://lab.example/private", "parent": None, "anchor": "", "depth": 0})
        arm = store.state["arms"]["bfs"]
        self.assertEqual(len(arm["http"]), 1)
        self.assertEqual(arm["attempts"][0]["error"], "robots_denied")

    def test_redirect_cannot_escape_scope(self):
        class Redirect(FixtureTransport):
            def get(self, url, cap, timeout, user_agent):
                if url.endswith("robots.txt"):
                    return super().get(url, cap, timeout, user_agent)
                return 302, {"location": "https://outside.example/"}, b"", False
        store, _ = self.create()
        Fetcher(store, Redirect()).page("bfs", {"url": "https://lab.example/", "parent": None, "anchor": "", "depth": 0})
        arm = store.state["arms"]["bfs"]
        self.assertEqual(len(arm["http"]), 2)
        self.assertEqual(arm["attempts"][0]["error"], "redirect_outside_scope")

    def test_byte_cap_and_crash_reservation_survive_resume(self):
        class Crash(FixtureTransport):
            def get(self, url, cap, timeout, user_agent):
                raise KeyboardInterrupt()
        config = demo_config()
        config["limits"].update(bytes_per_arm=1024, bytes_per_response=1024)
        store, engine = self.create(config, Crash())
        self.approve(store, engine)
        with self.assertRaises(KeyboardInterrupt):
            engine.advance()
        restored = Store(self.run)
        engine = Engine(restored)
        engine.advance()
        self.assertEqual(restored.state["status"], "paused")
        self.assertEqual(restored.state["arms"]["bfs"]["bytes_charged"], 1024)
        engine.resume()
        engine.advance()
        self.assertEqual(restored.state["arms"]["bfs"]["stop_reason"], "bytes_per_arm")
        self.assertEqual(restored.state["arms"]["bfs"]["attempts"][0]["error"], "interrupted_unknown")

    def test_private_dns_and_unsafe_urls(self):
        for url in ("http://example.org/", "https://x:y@example.org/", "https://example.org:8080/", "https://example.org/\n", "https://example.org\\@other/"):
            with self.assertRaises(LabError):
                canonical(url)
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(LabError):
                public_addresses("example.org")
        with self.assertRaises(LabError):
            LiveTransport().get("https://169.254.169.254/", 512, 1, "DiscoveryLab/0.1")

    def test_agent_request_limit_reserves_final_review(self):
        config = demo_config()
        config["limits"].update(agent_requests=3, pages_per_arm=8)
        store, engine = self.create(config)
        self.approve(store, engine)
        engine.advance()
        engine.answer(fixture_answer(self.request(store)))
        engine.advance()
        self.assertEqual(self.request(store)["kind"], "review")
        self.assertEqual(len(store.state["agent_requests"]), 3)
        self.assertEqual(store.state["arms"]["luna"]["stop_reason"], "agent_request_budget_reserved_for_review")

    def test_tampered_blob_fails_verification(self):
        run_demo(self.run)
        store = Store(self.run)
        page = store.state["arms"]["luna"]["pages"][0]
        (self.run / "blobs" / page["text_sha256"]).write_text("tampered", encoding="utf-8")
        self.assertFalse(verify(store)["ok"])

    def test_gold_is_separate_and_checkpoint_replays(self):
        run_demo(self.run)
        store = Store(self.run)
        result = grade(store, {"target_urls": ["https://lab.example/people/a", "https://lab.example/people/b"]}, self.root / "grade.json")
        self.assertEqual(result["arms"]["luna"]["hits"], 2)
        self.assertEqual(result["arms"]["bfs"]["hits"], 0)
        archive = self.root / "checkpoint.zip"
        export_checkpoint(store, archive)
        restored = self.root / "restored"
        with zipfile.ZipFile(archive) as zipped:
            self.assertNotIn("grade.json", zipped.namelist())
            self.assertNotIn("run.lock", zipped.namelist())
            zipped.extractall(restored)
        self.assertTrue(verify(Store(restored))["ok"])

    def test_concurrent_writer_is_rejected(self):
        Store.create(self.run, demo_config())
        with locked(self.run):
            result = subprocess.run([sys.executable, "-B", "-m", "jse.lab", "status", "--run", str(self.run)], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertIn("別のプロセス", result.stderr)

    def test_export_rejects_artifact_path_escape(self):
        store, engine = self.create()
        engine.advance()
        store.state["agent_requests"][0]["path"] = "../private.json"
        outside = self.root / "private.json"
        outside.write_text('{"private": true}', encoding="utf-8")
        with self.assertRaises(LabError):
            export_checkpoint(store, self.root / "invalid.zip")
        self.assertFalse((self.root / "invalid.zip").exists())
        with self.assertRaises(LabError):
            store.artifact("blobs/../../private.json")

    def test_answer_hash_is_verified(self):
        run_demo(self.run)
        store = Store(self.run)
        path = store.artifact(store.state["agent_requests"][0]["answer_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["hypothesis"] = "changed"
        path.write_bytes(encoded(data))
        self.assertFalse(verify(store)["ok"])

    def test_request_cap_includes_robots(self):
        config = demo_config()
        config["limits"].update(pages_per_arm=8, requests_per_arm=2)
        store, engine = self.create(config)
        self.approve(store, engine)
        engine.advance()
        for arm in store.state["arms"].values():
            self.assertEqual(len(arm["pages"]), 1)
            self.assertEqual(len(arm["http"]), 2)
            self.assertEqual(arm["stop_reason"], "requests_per_arm")

    def test_shift_jis_meta_preserves_japanese(self):
        body = '<meta charset="shift_jis"><title>教員</title><p>青木 花子 教授</p>'.encode("shift_jis")
        title, text, _ = parse_page(body, {"content-type": "text/html"}, "https://lab.example/")
        self.assertEqual(title, "教員")
        self.assertIn("青木 花子", text)

    def test_body_deadline_uses_remaining_time(self):
        timeouts = []
        class Response:
            status = 200
            headers = {"content-type": "text/html"}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read1(self, cap):
                return b"x"
        transport = LiveTransport()
        with patch.object(transport.opener, "open", return_value=Response()), patch("jse.lab.fetch.time.monotonic", side_effect=[0, 0.5, 1.1]):
            with self.assertRaisesRegex(LabError, "body_deadline"):
                transport.get("https://example.org/", 512, 1, "DiscoveryLab/0.1")
        self.assertEqual(timeouts, [0.5])

    def test_http_body_complete_closes_file_pointer(self):
        class Response:
            status = 200
            headers = {"content-type": "text/html"}
            fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda _: None)))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read1(self, cap):
                self.fp = None
                return b"<title>done</title>"
        transport = LiveTransport()
        with patch.object(transport.opener, "open", return_value=Response()):
            status, _, body, truncated = transport.get("https://example.org/", 512, 1, "DiscoveryLab/0.1")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<title>done</title>")
        self.assertFalse(truncated)


if __name__ == "__main__":
    unittest.main()
