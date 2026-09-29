import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import impit

from jse.lab.__main__ import demo_config
from jse.lab.fetch import Fetcher
from jse.lab.impit_transport import ImpitTransport, PinnedTunnel
from jse.lab.state import LabError, Store


class ImpitTests(unittest.TestCase):
    def setUp(self):
        self.response = SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, http_version="HTTP/2", iter_bytes=lambda: iter([b"hello", b" world"]))
        self.options = []
        self.calls = []
        owner = self

        class Client:
            def __init__(self, **options):
                owner.options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            @contextmanager
            def stream(self, method, url, **options):
                owner.calls.append((method, url, options))
                yield owner.response

        self.client_patch = patch("impit.Client", Client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def test_proxy_stream_is_capped_and_has_no_redirect_or_cookie_session(self):
        with patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080"}):
            transport = ImpitTransport()
        result = transport.get("https://example.org/", 7, 1, "DiscoveryLab/test")
        self.assertEqual(result[2:], (b"hello w", True))
        self.assertEqual(transport.last_http_version, "HTTP/2")
        options = self.options[0]
        self.assertEqual(options["proxy"], "http://proxy.example:8080")
        self.assertFalse(options["follow_redirects"])
        self.assertFalse(options["http3"])
        self.assertTrue(options["verify"])
        self.assertNotIn("cookie_jar", options)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]["headers"]["User-Agent"], "DiscoveryLab/test")

    def test_direct_request_uses_checked_address_and_original_hostname(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))
        with patch("urllib.request.getproxies", return_value={}), patch("jse.lab.impit_transport.public_addresses", return_value=[address]) as dns, patch("jse.lab.impit_transport.PinnedTunnel") as tunnel:
            tunnel.return_value.__enter__.return_value = "http://127.0.0.1:1234"
            result = ImpitTransport().get("https://example.org/", 100, 1, "DiscoveryLab/test")
        dns.assert_called_once_with("example.org")
        self.assertEqual(tunnel.call_args.args[:2], ("example.org", address))
        self.assertEqual(self.calls[0][1], "https://example.org/")
        self.assertEqual(result[2:], (b"hello world", False))

    def test_private_ip_and_private_dns_never_reach_impit(self):
        with patch("urllib.request.getproxies", return_value={}):
            transport = ImpitTransport()
        with self.assertRaisesRegex(LabError, "private_address"):
            transport.get("https://127.0.0.1/", 100, 1, "DiscoveryLab/test")
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]):
            with self.assertRaisesRegex(LabError, "private_address"):
                transport.get("https://example.org/", 100, 1, "DiscoveryLab/test")
        self.assertFalse(self.calls)

    def test_stream_failure_keeps_reserved_budget_and_hides_error_detail(self):
        def broken():
            yield b"partial"
            raise impit.ReadError("sensitive diagnostic")

        self.response.iter_bytes = broken
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080"}):
            config = demo_config()
            config["transport"] = "impit"
            store = Store.create(Path(directory) / "run", config)
            fetcher = Fetcher(store)
            with self.assertRaisesRegex(LabError, "^ReadError$"):
                fetcher.http("bfs", "https://lab.example/", "page")
            arm = store.state["arms"]["bfs"]
            self.assertEqual(arm["bytes_charged"], config["limits"]["bytes_per_response"])
            self.assertEqual(arm["http"][0]["http_client"], "impit")
            self.assertEqual(arm["http"][0]["network_route"], "environment_proxy")
            self.assertEqual(len(self.calls), 1)

    def test_redirect_is_returned_to_budgeted_fetcher(self):
        self.response.status_code = 302
        self.response.headers["location"] = "https://outside.example/"
        with patch("urllib.request.getproxies", return_value={"https": "http://proxy.example:8080"}):
            result = ImpitTransport().get("https://example.org/", 100, 1, "DiscoveryLab/test")
        self.assertEqual(result[0], 302)
        self.assertEqual(len(self.calls), 1)

    def test_tunnel_relays_only_to_pinned_socket(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            observed = []

            def echo():
                with server.accept()[0] as peer:
                    peer.settimeout(2)
                    observed.append(peer.recv(100))
                    peer.sendall(b"reply")

            worker = threading.Thread(target=echo, daemon=True)
            worker.start()
            address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", server.getsockname())
            tunnel = PinnedTunnel("example.org", address, time.monotonic() + 3)
            with tunnel as proxy:
                with socket.create_connection(("127.0.0.1", urlsplit(proxy).port), timeout=2) as peer:
                    peer.sendall(b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n\r\n")
                    self.assertIn(b"200", peer.recv(100))
                    peer.sendall(b"opaque TLS bytes")
                    self.assertEqual(peer.recv(100), b"reply")
            worker.join(2)
            self.assertEqual(observed, [b"opaque TLS bytes"])
            self.assertFalse(tunnel.thread.is_alive())

    def test_tunnel_rejects_other_authority_and_expires_partial_header(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1))
        tunnel = PinnedTunnel("example.org", address, time.monotonic() + 2)
        with tunnel as proxy:
            with socket.create_connection(("127.0.0.1", urlsplit(proxy).port), timeout=1) as peer:
                peer.sendall(b"CONNECT outside.example:443 HTTP/1.1\r\n\r\n")
                self.assertIn(b"403", peer.recv(100))
        self.assertEqual(tunnel.error, "impit_tunnel_target_mismatch")
        tunnel = PinnedTunnel("example.org", address, time.monotonic() + 0.2)
        with tunnel as proxy:
            with socket.create_connection(("127.0.0.1", urlsplit(proxy).port), timeout=1) as peer:
                peer.sendall(b"CONNECT")
                self.assertEqual(peer.recv(100), b"")
        self.assertEqual(tunnel.error, "TimeoutError")


if __name__ == "__main__":
    unittest.main()
