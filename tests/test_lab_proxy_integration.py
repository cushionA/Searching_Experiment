"""Real proxy routing against loopback fixtures only; never uses a public relay."""

import contextlib
import http.client
import json
import os
import select
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts.with_proxy import prepare


ROOT = Path(__file__).resolve().parents[1]
HOST = "proxy-check.invalid"
BODY = b"<title>local proxy fixture</title><p id='result'>proxied</p>"


@contextlib.contextmanager
def serving(handler, tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *_args):
        pass


def proxy_handler(origin_port, events, tls_port=None):
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            # Only this synthetic URL may reach the loopback origin.
            if self.path != f"http://{HOST}/fixture":
                self.send_error(403)
                return
            events.append(("GET", self.path))
            conn = http.client.HTTPConnection("127.0.0.1", origin_port, timeout=5)
            try:
                conn.request("GET", "/fixture")
                response = conn.getresponse()
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                conn.close()

        def do_CONNECT(self):
            port = {f"{HOST}:443": tls_port, f"{HOST}:80": origin_port}.get(self.path)
            if port is None:
                self.send_error(403)
                return
            events.append(("CONNECT", self.path))
            with socket.create_connection(("127.0.0.1", port), timeout=5) as upstream:
                self.send_response(200, "Connection established")
                self.end_headers()
                self.wfile.flush()
                self.connection.settimeout(5)
                while True:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 5)
                    if not ready:
                        break
                    for source in ready:
                        try:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)
                        except (ConnectionError, TimeoutError):
                            return

        def log_message(self, *_args):
            pass

    return Proxy


class ProxyIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "openssl is needed for the temporary fixture certificate")
    def test_https_fetch_uses_connect_with_certificate_verification(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, capture_output=True, timeout=15)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
            trusted = ssl.create_default_context(cafile=str(cert))
            self.assertTrue(trusted.check_hostname)
            self.assertEqual(trusted.verify_mode, ssl.CERT_REQUIRED)
            with serving(Origin, tls) as origin:
                with serving(proxy_handler(None, events, origin.server_port)) as proxy:
                    child = prepare(f"http://127.0.0.1:{proxy.server_port}", "cli", {}, root / "no-policy")["env"]
                    child["SSL_CERT_FILE"] = str(cert)
                    with patch.dict(os.environ, child, clear=True):
                        # Use the real lab fetch transport, including its normal ProxyHandler.
                        from jse.lab.fetch import LiveTransport
                        transport = LiveTransport()
                        status, headers, body, truncated = transport.get(
                            f"https://{HOST}/fixture", 4096, 5, "proxy-fixture")
                    self.assertEqual(status, 200)
                    self.assertEqual(body, BODY)
                    self.assertFalse(truncated)
            self.assertEqual(events, [("CONNECT", f"{HOST}:443")])

    @unittest.skipUnless(shutil.which("node") and shutil.which("chromium"), "Node/Chromium are optional")
    def test_real_browser_and_robots_client_use_local_forward_proxy(self):
        available = subprocess.run([
            "node", "--input-type=module", "-e",
            "import {require} from './experiments/bot-diagnostics/runtime.mjs'; require.resolve('playwright');",
        ], cwd=ROOT, capture_output=True, timeout=10)
        if available.returncode:
            self.skipTest("bot-diagnostics Playwright dependency is not installed")
        events = []
        script = """
import {openBrowser, httpClient} from './experiments/bot-diagnostics/runner.mjs';
const target = 'http://proxy-check.invalid/fixture';
const b = await openBrowser('playwright-baseline', {fixture:true});
let client;
try {
  await b.context.route('**/*', route => route.request().url() === target
    ? route.continue() : route.abort('blockedbyclient'));
  await b.page.goto(target, {waitUntil:'load', timeout:10000});
  const text = await b.page.locator('#result').innerText();
  client = await httpClient('playwright-baseline', 'proxy-fixture');
  const response = await client.get(target);
  const body = await new Response(response.body).text();
  console.log(JSON.stringify({text, status:response.status, robotsBody:body}));
} finally { await client?.close(); await b.close(); }
"""
        with tempfile.TemporaryDirectory() as directory, serving(Origin) as origin:
            with serving(proxy_handler(origin.server_port, events)) as proxy:
                env = dict(os.environ)
                env.update(BOT_DIAGNOSTICS_STATE=directory,
                           BOT_DIAGNOSTICS_CHROMIUM=shutil.which("chromium"),
                           NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
                child = prepare(f"http://127.0.0.1:{proxy.server_port}", "cli", env,
                                Path(directory) / "no-policy")["env"]
                result = subprocess.run(["node", "--input-type=module", "-e", script],
                                        cwd=ROOT, env=child, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads(result.stdout)
                self.assertEqual(observed["text"], "proxied")
                self.assertEqual(observed["status"], 200)
                self.assertEqual(observed["robotsBody"], BODY.decode())
        self.assertIn(("GET", f"http://{HOST}/fixture"), events)
        self.assertGreaterEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
