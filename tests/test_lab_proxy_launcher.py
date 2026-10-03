import contextlib
import io
import json
import os
import signal
import socket
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scripts.with_proxy import ProxyError, main, prepare, validate_proxy


class ProxyLauncherTests(unittest.TestCase):
    def test_cli_proxy_precedes_environment_and_sets_only_child_environment(self):
        original = {
            "HTTP_PROXY": "http://old.example:3128",
            "HTTPS_PROXY": "http://old.example:3128",
            "http_proxy": "http://old.example:3128",
            "https_proxy": "http://old.example:3128",
            "ALL_PROXY": "socks5://old.example:1080",
            "all_proxy": "socks5://old.example:1080",
            "NO_PROXY": "localhost",
            "no_proxy": "internal.example",
            "JSE_PROXY_URL": "http://env.example:8000",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, original, clear=True):
            before = dict(os.environ)
            result = prepare("http://cli.example:9000", "cli", dict(os.environ), Path(directory) / "missing")
            self.assertEqual(os.environ.copy(), before)
        child = result["env"]
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertEqual(child[key], "http://cli.example:9000")
        self.assertNotIn("ALL_PROXY", child)
        self.assertNotIn("all_proxy", child)
        self.assertEqual(child["NO_PROXY"], "localhost")
        self.assertEqual(child["no_proxy"], "internal.example")

    def test_environment_proxy_selection_and_check(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"JSE_PROXY_URL": "http://env.example:8123"}, clear=True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["--proxy", "http://cli.example:9123", "--check"], policy_path=Path(directory) / "missing")
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["source"], "cli")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["--check"], policy_path=Path(directory) / "missing")
            self.assertEqual(status, 0)
            data = json.loads(output.getvalue())
            self.assertEqual((data["host"], data["port"], data["source"]), ("env.example", 8123, "env"))

    def test_bad_proxy_urls_are_rejected_without_leaking_values(self):
        invalid = (
            "http://alice:top-secret@proxy.example:8080",
            "https://proxy.example:8080/path",
            "http://proxy.example:8080/?token=top-secret",
            "http://proxy.example:8080/#top-secret",
            "http://proxy.example:99999",
            "ftp://proxy.example:8080",
            "http://proxy.example:not-a-port",
            "http://proxy.example:8080\nsecret",
            " http://proxy.example:8080",
            "http://proxy.example:8080 ",
        )
        for value in invalid:
            with self.subTest(value=value):
                output = io.StringIO()
                with self.assertRaises(ProxyError) as error:
                    validate_proxy(value)
                self.assertEqual(str(error.exception), "invalid proxy URL")
                with contextlib.redirect_stderr(output):
                    status = main(["--proxy", value, "--check"])
                self.assertEqual(status, 2)
                self.assertNotIn("top-secret", output.getvalue())
                self.assertNotIn("alice", output.getvalue())

    def test_explicit_proxy_reaches_child_and_child_exit_code_is_returned(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            capture = Path(directory) / "child.json"
            code = (
                "import json,os,sys; "
                f"json.dump({{k:os.environ.get(k) for k in "
                "['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']}, "
                f"open({str(capture)!r}, 'w')); sys.exit(23)"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(
                    ["--proxy", "http://child.example:8100", "--", os.sys.executable, "-c", code],
                    policy_path=Path(directory) / "missing",
                )
            self.assertEqual(status, 23)
            child = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual([child[k] for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")], ["http://child.example:8100"] * 4)
            self.assertIsNone(child["ALL_PROXY"])
            self.assertIsNone(child["all_proxy"])
            self.assertEqual(os.environ, {})

    def test_managed_policy_blocks_changed_proxy_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "network-policy.json"
            policy.write_text('{"version":1}', encoding="utf-8")
            env = {key: "http://managed.example:8080" for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
            with patch.dict(os.environ, env, clear=True), patch("scripts.with_proxy.subprocess.Popen") as popen:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    status = main(["--proxy", "http://other.example:8080", "--", "would-run"], policy_path=policy)
                self.assertEqual(status, 2)
                popen.assert_not_called()
                self.assertIn("managed network policy", err.getvalue())
            with patch.dict(os.environ, env, clear=True):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    status = main(["--proxy", "http://other.example:8080", "--check"], policy_path=policy)
                self.assertEqual(status, 2)

    def test_managed_same_proxy_preserves_environment_and_policy_read_failure_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "network-policy.json"
            policy.write_text('{"version":1}', encoding="utf-8")
            env = {
                "HTTP_PROXY": "http://managed.example:8080",
                "HTTPS_PROXY": "http://managed.example:8080",
                "ALL_PROXY": "socks5://other.example:1080",
                "NO_PROXY": "localhost",
            }
            result = prepare("http://MANAGED.example:8080", "cli", env, policy)
            self.assertEqual(result["env"], env)
            for snapshot in ("{broken", "{}", "[]", "null", '{"version":2}', '{"version":true}'):
                policy.write_text(snapshot, encoding="utf-8")
                with self.subTest(snapshot=snapshot), self.assertRaises(ProxyError):
                    prepare("http://managed.example:8080", "cli", env, policy)

    @unittest.skipUnless(os.name == "posix", "POSIX signal exit status")
    def test_signal_exit_is_reported_to_the_calling_shell(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            status = main(["--", os.sys.executable, "-c",
                           "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"],
                          policy_path=Path(directory) / "missing")
        self.assertEqual(status, 128 + signal.SIGTERM)

    def test_inherited_proxy_is_preserved_and_check_needs_no_network(self):
        env = {
            "HTTP_PROXY": "http://inherited.example:8181",
            "HTTPS_PROXY": "http://inherited.example:8181",
            "ALL_PROXY": "socks5://fallback.example:1080",
            "NO_PROXY": "localhost",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = prepare(None, "inherited", env, Path(directory) / "missing")
            self.assertEqual(result["env"], env)
            with patch.dict(os.environ, env, clear=True), patch.object(socket, "create_connection", side_effect=AssertionError("network")):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = main(["--check"], policy_path=Path(directory) / "missing")
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["no_proxy"], {"NO_PROXY": "localhost"})

    def test_urllib_reaches_local_fake_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            requested_path = None

            def do_GET(self):
                type(self).requested_path = self.path
                body = b"local proxy fixture"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            proxy = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as directory:
                child_env = prepare(proxy, "cli", {}, Path(directory) / "missing")["env"]
                with patch.dict(os.environ, child_env, clear=True):
                    with urllib.request.urlopen("http://example.test/proxy-check", timeout=3) as response:
                        self.assertEqual(response.read(), b"local proxy fixture")
            self.assertEqual(Handler.requested_path, "http://example.test/proxy-check")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
