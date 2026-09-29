import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from jse.lab.__main__ import demo_config, export_checkpoint
from jse.lab.engine import verify
from jse.lab.fetch import Fetcher, FixtureTransport
from jse.lab.state import Store


class AdaptiveFixture(FixtureTransport):
    def __init__(self):
        self.calls = []

    def get(self, url, cap, timeout, user_agent):
        self.calls.append(url)
        path = urlsplit(url).path
        pages = {
            "/robots.txt": ("text/plain", "User-agent: *\nDisallow: /denied\n"),
            "/static": ("text/html", '<title>Static</title><p>固定本文</p><a href="/target">Target</a>'),
            "/dynamic": ("text/html", '<title>Dynamic</title><p>初期本文</p><script src="/append.js"></script>'),
            "/append.js": ("application/javascript", 'setTimeout(() => { const a=document.createElement("a");a.href="/target";a.textContent="追加リンク";document.body.appendChild(a); }, 100);'),
            "/outside": ("text/html", '<p>Scope</p><script src="https://outside.example/script.js"></script>'),
            "/uses-denied": ("text/html", '<p>Robots</p><script src="/denied.js"></script>'),
            "/post": ("text/html", '<p>POST</p><script>fetch("/write", {method:"POST",body:"x"})</script>'),
            "/empty": ("text/html", '<script>window.empty=true</script>'),
            "/huge-dom": ("text/html", '<p>Small</p><script>document.body.append("x".repeat(100001))</script>'),
            "/redirected-script": ("text/html", '<p>Redirect</p><script src="/redirect"></script>'),
            "/redirect": ("text/html", ""),
            "/redirect-outside": ("text/html", ""),
        }
        content_type, text = pages.get(path, ("text/html", "<p>target</p>"))
        body = text.encode()
        headers = {"content-type": content_type + "; charset=utf-8"}
        status = 200
        if path in ("/redirect", "/redirect-outside"):
            status = 302
            headers["location"] = "/dynamic" if path == "/redirect" else "https://outside.example/"
        return status, headers, body[:cap], len(body) >= cap


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = demo_config()
        config["transport"] = "adaptive"
        config["limits"]["requests_per_arm"] = 30
        self.store = Store.create(self.root / "run", config)
        self.transport = AdaptiveFixture()
        self.fetcher = Fetcher(self.store, self.transport)

    def page(self, path):
        self.fetcher.page("bfs", {"url": "https://lab.example" + path, "parent": None, "anchor": "test", "depth": 0})
        return self.store.state["arms"]["bfs"]

    def test_static_comparison_learning_survives_restart(self):
        arm = self.page("/static")
        self.assertEqual(arm["attempts"][-1]["status"], "ok", arm["attempts"])
        self.assertEqual(arm["adaptive_history"][-1]["rendering_type"], "static")
        self.assertEqual(len(arm["http"]), 3)
        self.store = Store(self.root / "run")
        self.fetcher = Fetcher(self.store, self.transport)
        arm = self.page("/static")
        self.assertEqual(arm["adaptive_decisions"][-1]["predicted"], "static")
        self.store = Store(self.root / "run")
        self.fetcher = Fetcher(self.store, self.transport)
        arm = self.page("/static")
        self.assertEqual(arm["pages"][-1]["source_kind"], "http")
        self.assertEqual(len(arm["http"]), 6)
        self.assertTrue(verify(self.store)["ok"], verify(self.store))

    def test_dynamic_links_dom_and_checkpoint(self):
        arm = self.page("/dynamic")
        self.assertEqual(arm["attempts"][-1]["status"], "ok", arm["attempts"])
        page = arm["pages"][0]
        self.assertIn("https://lab.example/target", [link["url"] for link in page["links"]])
        self.assertEqual(arm["adaptive_history"][-1]["rendering_type"], "client only")
        self.assertNotEqual(page["body_sha256"], page["dom_sha256"])
        self.assertEqual(len(arm["http"]), 4)
        self.assertTrue(verify(self.store)["ok"], verify(self.store))
        checkpoint = self.root / "run.zip"
        export_checkpoint(self.store, checkpoint)
        with zipfile.ZipFile(checkpoint) as archive:
            self.assertIn("blobs/" + page["dom_sha256"], archive.namelist())
            archive.extractall(self.root / "restored")
        self.assertTrue(verify(Store(self.root / "restored"))["ok"])
        self.store.artifact("blobs/" + page["dom_sha256"]).write_bytes(b"changed")
        self.assertFalse(verify(self.store)["ok"])

    def test_scope_robots_and_post_are_rejected_before_network(self):
        for path, expected in (("/outside", "outside_scope"), ("/uses-denied", "robots_denied"), ("/post", "adaptive_only_get"), ("/redirect-outside", "outside_scope")):
            with self.subTest(path=path):
                arm = self.page(path)
                self.assertEqual(arm["attempts"][-1]["status"], "failed")
                self.assertEqual(arm["attempts"][-1]["error"], expected)
        self.assertFalse(any("outside.example" in url or url.endswith("/write") or url.endswith("/denied.js") for url in self.transport.calls))

    def test_resource_budget_stops_before_unaccounted_request(self):
        self.store.state["config"]["limits"]["requests_per_arm"] = 2
        arm = self.page("/dynamic")
        self.assertEqual(len(arm["http"]), 2)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(arm["attempts"][-1]["error"], "request_budget")
        self.assertEqual(arm["bytes_charged"], sum(item["bytes_charged"] for item in arm["http"]))

    def test_browser_redirect_keeps_final_url_and_charges_every_hop(self):
        arm = self.page("/redirect")
        self.assertEqual(arm["attempts"][-1]["status"], "ok", arm["attempts"])
        self.assertEqual(arm["pages"][0]["url"], "https://lab.example/dynamic")
        self.assertEqual(len([r for r in arm["http"] if r["status"] == 302]), 2)
        self.assertTrue(verify(self.store)["ok"], verify(self.store))

    def test_empty_oversized_dom_and_resource_redirect_fail(self):
        for path, expected in (("/empty", "adaptive_empty_result"), ("/huge-dom", "dom_size_budget"), ("/redirected-script", "adaptive_resource_redirect_not_supported")):
            with self.subTest(path=path):
                arm = self.page(path)
                self.assertEqual(arm["attempts"][-1]["error"], expected)
                self.assertFalse(arm["pages"])

    def test_comparison_budget_failure_does_not_train_or_succeed(self):
        self.store.state["config"]["limits"]["requests_per_arm"] = 3
        arm = self.page("/dynamic")
        self.assertEqual(arm["attempts"][-1]["error"], "request_budget")
        self.assertEqual(len(self.transport.calls), 3)
        self.assertFalse(arm["adaptive_history"])
        self.assertFalse(arm["pages"])
        self.assertTrue(verify(self.store)["ok"], verify(self.store))

    def test_rendering_source_tampering_is_rejected(self):
        arm = self.page("/dynamic")
        arm["renderings"][0]["http_record_index"] = 0
        self.assertFalse(verify(self.store)["ok"])


if __name__ == "__main__":
    unittest.main()
