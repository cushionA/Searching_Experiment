import json
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jse.lab.__main__ import demo_config
from jse.lab.engine import verify
from jse.lab.fetch import Fetcher, LiveTransport
from jse.lab.impit_transport import ImpitTransport
from jse.lab.network import environment_proxy, sanitize_detail
from jse.lab.state import LabError, Store


class NetworkTests(unittest.TestCase):
    def test_no_proxy_is_applied_per_host_and_port(self):
        proxies = {"https": "http://proxy.example:8080", "no": ".example.org,aozora.gr.jp:443"}
        self.assertIsNone(environment_proxy("https://www.example.org/robots.txt", proxies))
        self.assertIsNone(environment_proxy("https://aozora.gr.jp:443/robots.txt", proxies))
        self.assertEqual(environment_proxy("https://example.org.evil.test/", proxies), proxies["https"])
        self.assertEqual(environment_proxy("https://other.test/", proxies), proxies["https"])

    def test_redacts_encoded_credentials_headers_keys_and_queries(self):
        proxy = "http://alice:p%40ssword@proxy.example:8080"
        message = f"CONNECT refused {proxy}; user=alice p@ssword; Proxy-Authorization: Basic dXNlcjpwYXNz; Bearer API value; https://example.org/?ticket=private"
        with patch.dict("os.environ", {"OPENAI_API_KEY": "API value"}):
            detail = sanitize_detail(message, (proxy,))
        for secret in ("alice", "p%40ssword", "p@ssword", "dXNlcjpwYXNz", "API value", "ticket=private"):
            self.assertNotIn(secret, detail)
        self.assertIn("CONNECT refused", detail)
        self.assertIn("proxy.example:8080", detail)
        self.assertLessEqual(len(sanitize_detail("x" * 5000)), 1500)

    def test_impit_failure_retains_safe_detail_and_reserved_budget(self):
        class HTTPError(Exception):
            pass
        class ProxyError(HTTPError):
            pass
        class Client:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            @contextmanager
            def stream(self, *args, **kwargs):
                raise ProxyError("CONNECT refused: http://alice:secret@proxy.example:8080")
                yield
        fake = SimpleNamespace(Client=Client, HTTPError=HTTPError, InvalidURL=HTTPError, StreamError=HTTPError)
        proxies = {"https": "http://alice:secret@proxy.example:8080"}
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"impit": fake}), patch("urllib.request.getproxies", return_value=proxies):
            store = Store.create(Path(directory) / "run", demo_config())
            fetcher = Fetcher(store, ImpitTransport())
            candidate = {"url": "https://lab.example/", "parent": None, "anchor": "seed", "depth": 0}
            fetcher.page("bfs", candidate)
            record = store.state["arms"]["bfs"]["http"][0]
            self.assertEqual(record["error"], "ProxyError")
            self.assertEqual(record["error_type"], "ProxyError")
            self.assertEqual(record["failure_stage_hint"], "proxy_connect")
            self.assertEqual(record["proxy_host"], "proxy.example")
            self.assertEqual(record["proxy_port"], 8080)
            self.assertEqual(record["network_route"], "environment_proxy")
            self.assertIn("CONNECT refused", record["error_detail"])
            self.assertEqual(record["bytes_charged"], 20000)
            serialized = (store.directory / "state.json").read_text()
            self.assertNotIn("alice", serialized)
            self.assertNotIn("secret", serialized)
            self.assertEqual(store.state["arms"]["bfs"]["attempts"][0]["error_detail"], record["error_detail"])
            self.assertTrue(verify(store)["ok"])
            self.assertEqual(verify(store)["arms"]["bfs"]["http_failures"][0]["error"], "ProxyError")

    def test_both_transports_report_no_proxy_route(self):
        with patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080", "no": "aozora.gr.jp"}):
            impit, live = ImpitTransport(), LiveTransport()
        self.assertEqual(impit.route_metadata("https://www.aozora.gr.jp/robots.txt")["network_route"], "direct_public_ip_tunnel")
        self.assertEqual(live.route_metadata("https://www.aozora.gr.jp/robots.txt")["network_route"], "direct_public_ip")
        self.assertNotIn("proxy_host", impit.route_metadata("https://www.aozora.gr.jp/robots.txt"))

    def test_urllib_proxy_error_is_distinct_from_http_status(self):
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080"}):
            store = Store.create(Path(directory) / "run", demo_config())
            transport = LiveTransport()
            with patch.object(transport.opener, "open", side_effect=urllib.error.URLError("Tunnel connection failed: 403 Forbidden")):
                with self.assertRaisesRegex(LabError, "^ProxyError$"):
                    Fetcher(store, transport).http("bfs", "https://lab.example/robots.txt", "robots")
            record = store.state["arms"]["bfs"]["http"][0]
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["error_type"], "URLError")
            self.assertIn("403 Forbidden", record["error_detail"])


if __name__ == "__main__":
    unittest.main()
