"""Optional Camoufox uses the shared scenario and verifies TLS through a local proxy."""

import json
import os
import shutil
import signal
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_lab_proxy_integration import HOST, Origin, proxy_handler, serving


ROOT = Path(__file__).resolve().parents[1]
DEPS = Path(os.environ.get("BOT_DIAGNOSTICS_CAMOUFOX_DEPS", ROOT / ".deps/camoufox"))
INSTALLED = (DEPS / "browser/camoufox-bin").is_file() and (DEPS / "node_modules/camoufox-js").is_dir()
NATIVE_TESTS = os.environ.get("BOT_DIAGNOSTICS_CAMOUFOX_NATIVE_TESTS") == "1"


def run_browser_command(command, *, timeout, env=None):
    # A timed-out Node parent must not leave its Firefox/Chromium children running.
    with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise RuntimeError(f"Browser fixture timed out after {timeout}s: {stdout}\n{stderr}") from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class CamoufoxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is an optional diagnostics dependency")
    def test_offline_runtime_guards(self):
        result = subprocess.run([
            "node", "--test", "experiments/bot-diagnostics/camoufox-runtime.test.mjs",
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NATIVE_TESTS and INSTALLED and shutil.which("node"),
                         "Enable the separately installed browser checks with BOT_DIAGNOSTICS_CAMOUFOX_NATIVE_TESTS=1")
    def test_shared_browser_scenario_cookie_redirect_and_evidence(self):
        available = subprocess.run([
            "node", "--input-type=module", "-e",
            "import {require} from './experiments/bot-diagnostics/runtime.mjs'; require.resolve('playwright');",
        ], cwd=ROOT, capture_output=True, timeout=10)
        if available.returncode or not shutil.which("chromium"):
            self.skipTest("The optional Playwright baseline and Chromium are needed for comparison")
        with tempfile.TemporaryDirectory() as directory:
            result = run_browser_command([
                "node", "experiments/bot-diagnostics/camoufox-smoke.mjs", str(Path(directory) / "run"),
            ], timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    @unittest.skipUnless(NATIVE_TESTS and INSTALLED and all(shutil.which(name) for name in ("node", "openssl", "certutil")),
                         "Enable native checks; optional Camoufox, Node, OpenSSL and certutil are required")
    def test_proxy_connect_trusted_ca_and_untrusted_ca_rejection(self):
        script = """
import {openBrowser} from './experiments/bot-diagnostics/runner.mjs';
const target = 'https://proxy-check.invalid/fixture';
const browser = await openBrowser('camoufox', {fixture:true});
try {
  await browser.context.route('**/*', route => route.request().url() === target
    ? route.continue() : route.abort('blockedbyclient'));
  try {
    await browser.page.goto(target, {waitUntil:'load',timeout:10000});
    console.log(JSON.stringify({loaded:true, text:await browser.page.locator('#result').innerText(),
      engine:browser.runtime.engine}));
  } catch(error) { console.log(JSON.stringify({loaded:false,error:String(error.message)})); }
} finally { await browser.close(); }
"""
        events = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("trusted", "unrelated"):
                subprocess.run([
                    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}",
                    "-keyout", str(root / f"{name}.key"), "-out", str(root / f"{name}.pem"),
                ], check=True, capture_output=True, timeout=15)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(root / "trusted.pem", root / "trusted.key")
            with serving(Origin, tls) as origin, serving(proxy_handler(None, events, origin.server_port)) as proxy:
                for name, loaded in (("trusted", True), ("unrelated", False)):
                    env = {**os.environ, "BOT_DIAGNOSTICS_STATE": str(root / name),
                           "BOT_DIAGNOSTICS_CA": str(root / f"{name}.pem"),
                           "HTTPS_PROXY": f"http://127.0.0.1:{proxy.server_port}"}
                    result = run_browser_command(["node", "--input-type=module", "-e", script],
                                                 env=env, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    observed = json.loads(result.stdout)
                    self.assertEqual(observed["loaded"], loaded, observed)
                    if loaded:
                        self.assertEqual(observed["text"], "proxied")
                        self.assertEqual(observed["engine"], "firefox")
                    else:
                        self.assertRegex(observed["error"], "(?i)certificate|cert_|issuer")
            self.assertIn(("CONNECT", f"{HOST}:443"), events)


if __name__ == "__main__":
    unittest.main()
